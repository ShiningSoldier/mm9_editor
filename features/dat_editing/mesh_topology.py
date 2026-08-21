"""Deterministic mesh-topology analysis for the glTF -> ED pipeline.

This Phase-3 module consumes the format-neutral :class:`GeometryScene` emitted
by the glTF reader.  It welds points in DEDit units, splits triangles into
edge-connected components, normalizes orientable winding, and classifies each
component without creating Brushes or writing files.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

import _path_setup  # noqa: F401
from features.dat_editing import geometry_scene


Vec2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]
Edge = Tuple[int, int]

DEFAULT_WELD_TOLERANCE = 0.01
DEFAULT_AREA_TOLERANCE = 1.0e-8
DEFAULT_VOLUME_TOLERANCE = 1.0e-8
DEFAULT_PLANE_TOLERANCE = 0.01

EXACT_CONVEX = "exact_convex"
SLAB_CANDIDATE = "slab_candidate"
# The later strict-policy layer maps a slab candidate to this policy-specific
# result instead of silently selecting triangle_slab.
BLOCKED_OPEN = "blocked_open"
BLOCKED_NON_MANIFOLD = "blocked_non_manifold"
BLOCKED_CONCAVE = "blocked_concave"
BLOCKED_INVALID = "blocked_invalid"


@dataclass(frozen=True)
class TopologyDiagnostic:
    """One stable, machine-readable topology diagnostic."""

    severity: str
    code: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class TopologyFace:
    """A valid, welded triangle with normalized component-local winding."""

    vertex_indices: Tuple[int, int, int]
    material_name: str
    uv_coords: Tuple[Optional[Vec2], Optional[Vec2], Optional[Vec2]]
    source_model_index: int
    source_face_index: int
    source_primitive_index: Optional[int]
    winding_flipped: bool
    extras: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return {
            "vertex_indices": list(self.vertex_indices),
            "material_name": self.material_name,
            "uv_coords": [list(value) if value is not None else None for value in self.uv_coords],
            "source_model_index": self.source_model_index,
            "source_face_index": self.source_face_index,
            "source_primitive_index": self.source_primitive_index,
            "winding_flipped": self.winding_flipped,
            "extras": dict(self.extras),
        }


@dataclass(frozen=True)
class TopologyComponent:
    """Topology and convexity result for one edge-connected component."""

    component_id: str
    classification: str
    model_index: int
    model_name: str
    scene_index: Optional[int]
    scene_node_index: Optional[int]
    mesh_index: Optional[int]
    primitive_indices: Tuple[int, ...]
    source_face_indices: Tuple[int, ...]
    source_point_count: int
    welded_point_count: int
    source_triangle_count: int
    points: Tuple[Vec3, ...]
    faces: Tuple[TopologyFace, ...]
    bounds_min: Optional[Vec3]
    bounds_max: Optional[Vec3]
    signed_volume: float
    absolute_volume: float
    boundary_edge_count: int
    nonmanifold_edge_count: int
    inconsistent_edge_count: int
    duplicate_face_count: int
    winding_flip_count: int
    global_winding_reversed: bool
    topology_status: str
    convexity_status: str
    convexity_violation_count: int
    max_convexity_violation: float
    diagnostics: Tuple[TopologyDiagnostic, ...]

    @property
    def strict_convex_ready(self) -> bool:
        return self.classification == EXACT_CONVEX

    @property
    def slab_candidate(self) -> bool:
        return self.classification == SLAB_CANDIDATE

    @property
    def blockers(self) -> Tuple[TopologyDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "blocker")

    @property
    def cautions(self) -> Tuple[TopologyDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "caution")

    @property
    def notes(self) -> Tuple[TopologyDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "note")

    def to_dict(self) -> Dict[str, object]:
        return {
            "component_id": self.component_id,
            "classification": self.classification,
            "strict_convex_ready": self.strict_convex_ready,
            "slab_candidate": self.slab_candidate,
            "source": {
                "model_index": self.model_index,
                "model_name": self.model_name,
                "scene_index": self.scene_index,
                "scene_node_index": self.scene_node_index,
                "mesh_index": self.mesh_index,
                "primitive_indices": list(self.primitive_indices),
                "face_indices": list(self.source_face_indices),
            },
            "counts": {
                "source_points": self.source_point_count,
                "welded_points": self.welded_point_count,
                "source_triangles": self.source_triangle_count,
                "valid_triangles": len(self.faces),
            },
            "bounds": {
                "min": list(self.bounds_min) if self.bounds_min is not None else None,
                "max": list(self.bounds_max) if self.bounds_max is not None else None,
            },
            "signed_volume": self.signed_volume,
            "absolute_volume": self.absolute_volume,
            "topology": {
                "status": self.topology_status,
                "boundary_edges": self.boundary_edge_count,
                "nonmanifold_edges": self.nonmanifold_edge_count,
                "inconsistent_edges": self.inconsistent_edge_count,
                "duplicate_faces": self.duplicate_face_count,
                "winding_flips": self.winding_flip_count,
                "global_winding_reversed": self.global_winding_reversed,
            },
            "convexity": {
                "status": self.convexity_status,
                "violation_count": self.convexity_violation_count,
                "max_violation": self.max_convexity_violation,
            },
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True)
class MeshTopologyReport:
    """Scene-level deterministic output of :func:`analyze_geometry_scene`."""

    status: str
    source_path: str
    weld_tolerance: float
    area_tolerance: float
    volume_tolerance: float
    plane_tolerance: float
    source_model_count: int
    analyzed_model_count: int
    source_triangle_count: int
    components: Tuple[TopologyComponent, ...]
    diagnostics: Tuple[TopologyDiagnostic, ...]

    @property
    def component_count(self) -> int:
        return len(self.components)

    @property
    def strict_convex_ready(self) -> bool:
        return bool(self.components) and all(item.strict_convex_ready for item in self.components)

    @property
    def blockers(self) -> Tuple[TopologyDiagnostic, ...]:
        result = [item for item in self.diagnostics if item.severity == "blocker"]
        for component in self.components:
            result.extend(component.blockers)
        return tuple(result)

    @property
    def cautions(self) -> Tuple[TopologyDiagnostic, ...]:
        result = [item for item in self.diagnostics if item.severity == "caution"]
        for component in self.components:
            result.extend(component.cautions)
        return tuple(result)

    @property
    def notes(self) -> Tuple[TopologyDiagnostic, ...]:
        result = [item for item in self.diagnostics if item.severity == "note"]
        for component in self.components:
            result.extend(component.notes)
        return tuple(result)

    @property
    def classification_counts(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for component in self.components:
            result[component.classification] = result.get(component.classification, 0) + 1
        return result

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "source_path": self.source_path,
            "options": {
                "weld_tolerance": self.weld_tolerance,
                "area_tolerance": self.area_tolerance,
                "volume_tolerance": self.volume_tolerance,
                "plane_tolerance": self.plane_tolerance,
            },
            "inventory": {
                "source_models": self.source_model_count,
                "analyzed_models": self.analyzed_model_count,
                "source_triangles": self.source_triangle_count,
                "components": self.component_count,
                "classification_counts": self.classification_counts,
            },
            "strict_convex_ready": self.strict_convex_ready,
            "components": [item.to_dict() for item in self.components],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "blockers": [item.to_dict() for item in self.blockers],
            "cautions": [item.to_dict() for item in self.cautions],
            "notes": [item.to_dict() for item in self.notes],
        }


@dataclass(frozen=True)
class _WorkingFace:
    source_face_index: int
    source_point_indices: Tuple[int, int, int]
    vertex_indices: Tuple[int, int, int]
    material_name: str
    uv_coords: Tuple[Optional[Vec2], Optional[Vec2], Optional[Vec2]]
    primitive_index: Optional[int]
    extras: Dict[str, object]
    diagnostics: Tuple[TopologyDiagnostic, ...]


@dataclass(frozen=True)
class _InvalidFace:
    source_face_index: int
    source_point_indices: Tuple[int, ...]
    welded_point_indices: Tuple[int, ...]
    primitive_index: Optional[int]
    diagnostic: TopologyDiagnostic


def analyze_geometry_scene(
    scene: geometry_scene.GeometryScene,
    *,
    weld_tolerance: float = DEFAULT_WELD_TOLERANCE,
    area_tolerance: float = DEFAULT_AREA_TOLERANCE,
    volume_tolerance: float = DEFAULT_VOLUME_TOLERANCE,
    plane_tolerance: float = DEFAULT_PLANE_TOLERANCE,
) -> MeshTopologyReport:
    """Analyze all mesh models without mutating ``scene`` or writing output.

    Open, orientable manifold triangle surfaces are reported as slab candidates,
    but are *not* strict-convex ready.  Selecting the ``triangle_slab`` policy
    remains a later, explicit conversion-service decision.
    """
    if not isinstance(scene, geometry_scene.GeometryScene):
        raise TypeError("scene must be a GeometryScene")
    weld_tolerance = _nonnegative_finite("weld_tolerance", weld_tolerance)
    area_tolerance = _nonnegative_finite("area_tolerance", area_tolerance)
    volume_tolerance = _nonnegative_finite("volume_tolerance", volume_tolerance)
    plane_tolerance = _nonnegative_finite("plane_tolerance", plane_tolerance)

    components: List[TopologyComponent] = []
    report_diagnostics: List[TopologyDiagnostic] = []
    analyzed_models = 0
    source_triangles = 0
    scene_index = _optional_int(scene.metadata.get("selected_scene_index"))
    for model_index, model in enumerate(scene.models):
        if not model.faces:
            continue
        analyzed_models += 1
        source_triangles += len(model.faces)
        components.extend(_analyze_model(
            model,
            model_index=model_index,
            scene_index=scene_index,
            weld_tolerance=weld_tolerance,
            area_tolerance=area_tolerance,
            volume_tolerance=volume_tolerance,
            plane_tolerance=plane_tolerance,
        ))

    if not components:
        report_diagnostics.append(_diagnostic(
            "blocker",
            "empty_geometry_scene",
            "the scene contains no mesh faces to analyze",
        ))

    has_blocked_component = any(item.classification.startswith("blocked_") for item in components)
    if report_diagnostics or has_blocked_component:
        status = "blocked"
    elif components and all(item.classification == EXACT_CONVEX for item in components):
        status = "ready_strict_convex"
    else:
        status = "analyzed_with_slab_candidates"

    return MeshTopologyReport(
        status=status,
        source_path=str(scene.source_path),
        weld_tolerance=weld_tolerance,
        area_tolerance=area_tolerance,
        volume_tolerance=volume_tolerance,
        plane_tolerance=plane_tolerance,
        source_model_count=len(scene.models),
        analyzed_model_count=analyzed_models,
        source_triangle_count=source_triangles,
        components=tuple(components),
        diagnostics=tuple(report_diagnostics),
    )


def _analyze_model(
    model: geometry_scene.GeometryModel,
    *,
    model_index: int,
    scene_index: Optional[int],
    weld_tolerance: float,
    area_tolerance: float,
    volume_tolerance: float,
    plane_tolerance: float,
) -> List[TopologyComponent]:
    welded_points, point_remap = _weld_finite_points(model.points, weld_tolerance)
    valid_faces: List[_WorkingFace] = []
    invalid_faces: List[_InvalidFace] = []

    for face_index, face in enumerate(model.faces):
        primitive_index = _optional_int(face.extras.get("primitive_index"))
        raw_indices = _face_indices(face.vertex_indices)
        if raw_indices is None:
            invalid_faces.append(_InvalidFace(
                source_face_index=face_index,
                source_point_indices=(),
                welded_point_indices=(),
                primitive_index=primitive_index,
                diagnostic=_diagnostic(
                    "blocker",
                    "non_triangle_face",
                    f"model {model_index} face {face_index} does not contain exactly three integer indices",
                ),
            ))
            continue
        if any(index < 0 or index >= len(model.points) for index in raw_indices):
            invalid_faces.append(_InvalidFace(
                source_face_index=face_index,
                source_point_indices=raw_indices,
                welded_point_indices=(),
                primitive_index=primitive_index,
                diagnostic=_diagnostic(
                    "blocker",
                    "point_index_out_of_range",
                    f"model {model_index} face {face_index} references a point outside the model",
                ),
            ))
            continue
        remapped = tuple(point_remap[index] for index in raw_indices)
        if any(index is None for index in remapped):
            invalid_faces.append(_InvalidFace(
                source_face_index=face_index,
                source_point_indices=raw_indices,
                welded_point_indices=tuple(index for index in remapped if index is not None),
                primitive_index=primitive_index,
                diagnostic=_diagnostic(
                    "blocker",
                    "non_finite_point",
                    f"model {model_index} face {face_index} references a non-finite 3D point",
                ),
            ))
            continue
        vertices = (int(remapped[0]), int(remapped[1]), int(remapped[2]))
        if len(set(vertices)) != 3:
            invalid_faces.append(_InvalidFace(
                source_face_index=face_index,
                source_point_indices=raw_indices,
                welded_point_indices=vertices,
                primitive_index=primitive_index,
                diagnostic=_diagnostic(
                    "blocker",
                    "face_collapsed_after_weld",
                    f"model {model_index} face {face_index} collapsed after {weld_tolerance:g}-unit welding",
                ),
            ))
            continue
        area = _triangle_area(*(welded_points[index] for index in vertices))
        if not math.isfinite(area) or area <= area_tolerance:
            invalid_faces.append(_InvalidFace(
                source_face_index=face_index,
                source_point_indices=raw_indices,
                welded_point_indices=vertices,
                primitive_index=primitive_index,
                diagnostic=_diagnostic(
                    "blocker",
                    "degenerate_face",
                    f"model {model_index} face {face_index} has area {area:g} after welding",
                ),
            ))
            continue
        uv_coords, uv_diagnostic = _normalized_uv_coords(face.uv_coords, model_index, face_index)
        diagnostics = (uv_diagnostic,) if uv_diagnostic is not None else ()
        valid_faces.append(_WorkingFace(
            source_face_index=face_index,
            source_point_indices=raw_indices,
            vertex_indices=vertices,
            material_name=str(face.material_name or "Default"),
            uv_coords=uv_coords,
            primitive_index=primitive_index,
            extras=dict(face.extras),
            diagnostics=diagnostics,
        ))

    seeds: List[Tuple[int, str, object]] = []
    for face_ids in _edge_connected_components(valid_faces):
        seeds.append((min(valid_faces[index].source_face_index for index in face_ids), "valid", face_ids))
    for invalid in invalid_faces:
        seeds.append((invalid.source_face_index, "invalid", invalid))
    seeds.sort(key=lambda item: (item[0], 0 if item[1] == "valid" else 1))

    result: List[TopologyComponent] = []
    for ordinal, (_, seed_kind, payload) in enumerate(seeds):
        component_id = f"model_{model_index:04d}_component_{ordinal:04d}"
        if seed_kind == "valid":
            result.append(_analyze_valid_component(
                model,
                model_index=model_index,
                scene_index=scene_index,
                component_id=component_id,
                welded_points=welded_points,
                working_faces=valid_faces,
                face_ids=payload,
                volume_tolerance=volume_tolerance,
                plane_tolerance=plane_tolerance,
            ))
        else:
            result.append(_invalid_face_component(
                model,
                model_index=model_index,
                scene_index=scene_index,
                component_id=component_id,
                welded_points=welded_points,
                invalid=payload,
            ))
    return result


def _analyze_valid_component(
    model: geometry_scene.GeometryModel,
    *,
    model_index: int,
    scene_index: Optional[int],
    component_id: str,
    welded_points: Sequence[Vec3],
    working_faces: Sequence[_WorkingFace],
    face_ids: Sequence[int],
    volume_tolerance: float,
    plane_tolerance: float,
) -> TopologyComponent:
    selected = [
        working_faces[index]
        for index in sorted(face_ids, key=lambda item: working_faces[item].source_face_index)
    ]
    used_global_points = sorted({index for face in selected for index in face.vertex_indices})
    global_to_local = {point_index: local_index for local_index, point_index in enumerate(used_global_points)}
    points = tuple(welded_points[index] for index in used_global_points)
    local_vertices = [tuple(global_to_local[index] for index in face.vertex_indices) for face in selected]

    edge_uses = _edge_uses(local_vertices)
    boundary_edges = sum(1 for uses in edge_uses.values() if len(uses) == 1)
    nonmanifold_edges = sum(1 for uses in edge_uses.values() if len(uses) > 2)
    inconsistent_edges = sum(
        1
        for uses in edge_uses.values()
        if len(uses) == 2 and _same_directed_edge(uses[0][1], uses[0][2], uses[1][1], uses[1][2])
    )
    duplicate_faces = len(local_vertices) - len({tuple(sorted(face)) for face in local_vertices})
    flip_assignments, orientable = _winding_assignments(local_vertices, edge_uses)

    is_closed_manifold = boundary_edges == 0 and nonmanifold_edges == 0
    global_reverse = False
    signed_volume = 0.0
    if orientable:
        oriented_vertices = [
            _flipped_triangle(vertices) if flip_assignments[index] else vertices
            for index, vertices in enumerate(local_vertices)
        ]
        signed_volume = _signed_volume(points, oriented_vertices)
        if is_closed_manifold and signed_volume < -volume_tolerance:
            global_reverse = True
            oriented_vertices = [_flipped_triangle(vertices) for vertices in oriented_vertices]
            signed_volume = -signed_volume
    else:
        oriented_vertices = list(local_vertices)

    diagnostics: List[TopologyDiagnostic] = []
    for face in selected:
        diagnostics.extend(face.diagnostics)
    if duplicate_faces:
        diagnostics.append(_diagnostic(
            "blocker",
            "duplicate_faces",
            f"{component_id} contains {duplicate_faces} duplicate triangle(s)",
        ))
    if nonmanifold_edges:
        diagnostics.append(_diagnostic(
            "blocker",
            "nonmanifold_edges",
            f"{component_id} has {nonmanifold_edges} edge(s) used by more than two triangles",
        ))
    if not orientable:
        diagnostics.append(_diagnostic(
            "blocker",
            "inconsistent_winding",
            f"{component_id} has contradictory winding constraints and is not orientable",
        ))
    elif inconsistent_edges:
        diagnostics.append(_diagnostic(
            "note",
            "winding_repaired",
            f"{component_id} repaired {inconsistent_edges} initially inconsistent shared edge(s)",
        ))

    convexity_status = "not_applicable"
    violation_count = 0
    max_violation = 0.0
    zero_volume = is_closed_manifold and abs(signed_volume) <= volume_tolerance
    if is_closed_manifold and orientable and not zero_volume and not duplicate_faces:
        violation_count, max_violation = _convexity_violations(points, oriented_vertices, plane_tolerance)
        convexity_status = "convex" if violation_count == 0 else "concave"

    if duplicate_faces or not orientable:
        classification = BLOCKED_INVALID
        topology_status = "invalid"
    elif nonmanifold_edges:
        classification = BLOCKED_NON_MANIFOLD
        topology_status = "non_manifold"
    elif boundary_edges:
        classification = SLAB_CANDIDATE
        topology_status = "open_manifold"
        convexity_status = "not_applicable_open"
        diagnostics.append(_diagnostic(
            "caution",
            "open_surface",
            f"{component_id} has {boundary_edges} boundary edge(s); strict_convex is blocked "
            "and triangle_slab requires explicit selection",
        ))
    elif zero_volume:
        classification = BLOCKED_INVALID
        topology_status = "closed_zero_volume"
        convexity_status = "not_applicable_zero_volume"
        diagnostics.append(_diagnostic(
            "blocker",
            "zero_volume",
            f"{component_id} is closed but its signed volume is within {volume_tolerance:g} of zero",
        ))
    elif violation_count:
        classification = BLOCKED_CONCAVE
        topology_status = "closed_manifold"
        diagnostics.append(_diagnostic(
            "blocker",
            "concave_component",
            f"{component_id} fails {violation_count} face half-space test(s); maximum violation is {max_violation:g}",
        ))
    else:
        classification = EXACT_CONVEX
        topology_status = "closed_manifold"

    final_faces: List[TopologyFace] = []
    winding_flip_count = 0
    for index, face in enumerate(selected):
        flip = bool(orientable and (flip_assignments[index] ^ global_reverse))
        vertices = oriented_vertices[index]
        uv_coords = _flipped_uvs(face.uv_coords) if flip else face.uv_coords
        if flip:
            winding_flip_count += 1
        final_faces.append(TopologyFace(
            vertex_indices=vertices,
            material_name=face.material_name,
            uv_coords=uv_coords,
            source_model_index=model_index,
            source_face_index=face.source_face_index,
            source_primitive_index=face.primitive_index,
            winding_flipped=flip,
            extras=dict(face.extras),
        ))

    source_point_indices = {index for face in selected for index in face.source_point_indices}
    primitive_indices = tuple(sorted({face.primitive_index for face in selected if face.primitive_index is not None}))
    bounds_min, bounds_max = _bounds(points)
    return TopologyComponent(
        component_id=component_id,
        classification=classification,
        model_index=model_index,
        model_name=str(model.name),
        scene_index=scene_index,
        scene_node_index=_optional_int(model.extras.get("scene_node_index")),
        mesh_index=_optional_int(model.extras.get("mesh_index")),
        primitive_indices=primitive_indices,
        source_face_indices=tuple(face.source_face_index for face in selected),
        source_point_count=len(source_point_indices),
        welded_point_count=len(points),
        source_triangle_count=len(selected),
        points=points,
        faces=tuple(final_faces),
        bounds_min=bounds_min,
        bounds_max=bounds_max,
        signed_volume=signed_volume,
        absolute_volume=abs(signed_volume),
        boundary_edge_count=boundary_edges,
        nonmanifold_edge_count=nonmanifold_edges,
        inconsistent_edge_count=inconsistent_edges,
        duplicate_face_count=duplicate_faces,
        winding_flip_count=winding_flip_count,
        global_winding_reversed=global_reverse,
        topology_status=topology_status,
        convexity_status=convexity_status,
        convexity_violation_count=violation_count,
        max_convexity_violation=max_violation,
        diagnostics=tuple(diagnostics),
    )


def _invalid_face_component(
    model: geometry_scene.GeometryModel,
    *,
    model_index: int,
    scene_index: Optional[int],
    component_id: str,
    welded_points: Sequence[Vec3],
    invalid: _InvalidFace,
) -> TopologyComponent:
    used_points = sorted(set(invalid.welded_point_indices))
    points = tuple(welded_points[index] for index in used_points)
    bounds_min, bounds_max = _bounds(points)
    primitive_indices = (invalid.primitive_index,) if invalid.primitive_index is not None else ()
    return TopologyComponent(
        component_id=component_id,
        classification=BLOCKED_INVALID,
        model_index=model_index,
        model_name=str(model.name),
        scene_index=scene_index,
        scene_node_index=_optional_int(model.extras.get("scene_node_index")),
        mesh_index=_optional_int(model.extras.get("mesh_index")),
        primitive_indices=primitive_indices,
        source_face_indices=(invalid.source_face_index,),
        source_point_count=len(set(invalid.source_point_indices)),
        welded_point_count=len(points),
        source_triangle_count=1,
        points=points,
        faces=(),
        bounds_min=bounds_min,
        bounds_max=bounds_max,
        signed_volume=0.0,
        absolute_volume=0.0,
        boundary_edge_count=0,
        nonmanifold_edge_count=0,
        inconsistent_edge_count=0,
        duplicate_face_count=0,
        winding_flip_count=0,
        global_winding_reversed=False,
        topology_status="invalid",
        convexity_status="not_applicable_invalid",
        convexity_violation_count=0,
        max_convexity_violation=0.0,
        diagnostics=(invalid.diagnostic,),
    )


def _weld_finite_points(
    points: Sequence[Vec3],
    tolerance: float,
) -> Tuple[Tuple[Vec3, ...], Tuple[Optional[int], ...]]:
    """Match the legacy ED writer's stable first-point weld behavior."""
    cells: Dict[Tuple[int, int, int], List[int]] = {}
    exact: Dict[Vec3, int] = {}
    result: List[Vec3] = []
    remap: List[Optional[int]] = []
    tolerance_sq = tolerance * tolerance
    for raw_point in points:
        point = _finite_vec3(raw_point)
        if point is None:
            remap.append(None)
            continue
        if tolerance <= 0.0:
            match = exact.get(point)
            if match is None:
                match = len(result)
                exact[point] = match
                result.append(point)
            remap.append(match)
            continue
        cell = tuple(int(math.floor(value / tolerance)) for value in point)
        matches: List[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    matches.extend(cells.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), ()))
        match = next((
            index
            for index in sorted(matches)
            if _distance_sq(point, result[index]) <= tolerance_sq
        ), None)
        if match is None:
            match = len(result)
            result.append(point)
            cells.setdefault(cell, []).append(match)
        remap.append(match)
    return tuple(result), tuple(remap)


def _edge_connected_components(faces: Sequence[_WorkingFace]) -> List[Tuple[int, ...]]:
    edge_faces: Dict[Edge, List[int]] = {}
    for face_id, face in enumerate(faces):
        for edge in _triangle_edges(face.vertex_indices):
            edge_faces.setdefault(_undirected_edge(*edge), []).append(face_id)
    adjacency: List[Set[int]] = [set() for _ in faces]
    for face_ids in edge_faces.values():
        if len(face_ids) < 2:
            continue
        anchor = face_ids[0]
        for other in face_ids[1:]:
            adjacency[anchor].add(other)
            adjacency[other].add(anchor)

    result: List[Tuple[int, ...]] = []
    unseen = set(range(len(faces)))
    while unseen:
        first = min(unseen, key=lambda index: faces[index].source_face_index)
        unseen.remove(first)
        pending = [first]
        component: List[int] = []
        while pending:
            face_id = pending.pop()
            component.append(face_id)
            neighbors = sorted(adjacency[face_id], reverse=True)
            for neighbor in neighbors:
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    pending.append(neighbor)
        result.append(tuple(sorted(component, key=lambda index: faces[index].source_face_index)))
    return result


def _edge_uses(vertices: Sequence[Tuple[int, int, int]]) -> Dict[Edge, List[Tuple[int, int, int]]]:
    result: Dict[Edge, List[Tuple[int, int, int]]] = {}
    for face_id, triangle in enumerate(vertices):
        for start, end in _triangle_edges(triangle):
            result.setdefault(_undirected_edge(start, end), []).append((face_id, start, end))
    return result


def _winding_assignments(
    vertices: Sequence[Tuple[int, int, int]],
    edge_uses: Dict[Edge, List[Tuple[int, int, int]]],
) -> Tuple[Tuple[bool, ...], bool]:
    adjacency: List[List[Tuple[int, bool]]] = [[] for _ in vertices]
    for uses in edge_uses.values():
        if len(uses) != 2:
            continue
        left, right = uses
        require_different_flip = _same_directed_edge(left[1], left[2], right[1], right[2])
        adjacency[left[0]].append((right[0], require_different_flip))
        adjacency[right[0]].append((left[0], require_different_flip))

    assignments: List[Optional[bool]] = [None] * len(vertices)
    orientable = True
    for first in range(len(vertices)):
        if assignments[first] is not None:
            continue
        assignments[first] = False
        pending = [first]
        while pending:
            face_id = pending.pop()
            for neighbor, different in sorted(adjacency[face_id], reverse=True):
                expected = bool(assignments[face_id]) ^ different
                if assignments[neighbor] is None:
                    assignments[neighbor] = expected
                    pending.append(neighbor)
                elif assignments[neighbor] != expected:
                    orientable = False
    return tuple(bool(value) for value in assignments), orientable


def _convexity_violations(
    points: Sequence[Vec3],
    vertices: Sequence[Tuple[int, int, int]],
    tolerance: float,
) -> Tuple[int, float]:
    violation_count = 0
    max_violation = 0.0
    for triangle in vertices:
        a, b, c = (points[index] for index in triangle)
        normal = _cross(_subtract(b, a), _subtract(c, a))
        length = math.sqrt(_dot(normal, normal))
        if length <= 0.0:
            continue
        unit = tuple(value / length for value in normal)
        distance = _dot(unit, a)
        face_violation = max((_dot(unit, point) - distance for point in points), default=0.0)
        if face_violation > tolerance:
            violation_count += 1
            max_violation = max(max_violation, face_violation)
    return violation_count, max_violation


def _signed_volume(points: Sequence[Vec3], vertices: Sequence[Tuple[int, int, int]]) -> float:
    if not points:
        return 0.0
    reference = tuple(sum(point[axis] for point in points) / len(points) for axis in range(3))
    volume = 0.0
    for triangle in vertices:
        a, b, c = (_subtract(points[index], reference) for index in triangle)
        volume += _dot(a, _cross(b, c)) / 6.0
    return volume


def _bounds(points: Sequence[Vec3]) -> Tuple[Optional[Vec3], Optional[Vec3]]:
    if not points:
        return None, None
    return (
        tuple(min(point[axis] for point in points) for axis in range(3)),
        tuple(max(point[axis] for point in points) for axis in range(3)),
    )


def _normalized_uv_coords(
    raw_uvs: Sequence[Optional[Vec2]],
    model_index: int,
    face_index: int,
) -> Tuple[
    Tuple[Optional[Vec2], Optional[Vec2], Optional[Vec2]],
    Optional[TopologyDiagnostic],
]:
    if not isinstance(raw_uvs, (list, tuple)) or len(raw_uvs) != 3:
        return (None, None, None), _diagnostic(
            "caution",
            "invalid_uv_triplet",
            f"model {model_index} face {face_index} does not have three usable UV slots",
        )
    result: List[Optional[Vec2]] = []
    invalid = False
    for raw_uv in raw_uvs:
        if raw_uv is None:
            result.append(None)
            continue
        if not isinstance(raw_uv, (list, tuple)) or len(raw_uv) != 2:
            result.append(None)
            invalid = True
            continue
        try:
            uv = (float(raw_uv[0]), float(raw_uv[1]))
        except (TypeError, ValueError, OverflowError):
            result.append(None)
            invalid = True
            continue
        if not all(math.isfinite(value) for value in uv):
            result.append(None)
            invalid = True
            continue
        result.append(uv)
    diagnostic = None
    if invalid:
        diagnostic = _diagnostic(
            "caution",
            "invalid_uv_value",
            f"model {model_index} face {face_index} contains an unusable UV value",
        )
    return (result[0], result[1], result[2]), diagnostic


def _face_indices(raw_indices: object) -> Optional[Tuple[int, int, int]]:
    if not isinstance(raw_indices, (list, tuple)) or len(raw_indices) != 3:
        return None
    if any(type(index) is not int for index in raw_indices):
        return None
    return int(raw_indices[0]), int(raw_indices[1]), int(raw_indices[2])


def _finite_vec3(raw_point: object) -> Optional[Vec3]:
    if not isinstance(raw_point, (list, tuple)) or len(raw_point) != 3:
        return None
    try:
        point = (float(raw_point[0]), float(raw_point[1]), float(raw_point[2]))
    except (TypeError, ValueError, OverflowError):
        return None
    return point if all(math.isfinite(value) for value in point) else None


def _optional_int(value: object) -> Optional[int]:
    return int(value) if type(value) is int else None


def _nonnegative_finite(name: str, value: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite non-negative number") from exc
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return result


def _diagnostic(severity: str, code: str, message: str) -> TopologyDiagnostic:
    return TopologyDiagnostic(severity=severity, code=code, message=message)


def _triangle_edges(vertices: Tuple[int, int, int]) -> Tuple[Edge, Edge, Edge]:
    return (vertices[0], vertices[1]), (vertices[1], vertices[2]), (vertices[2], vertices[0])


def _undirected_edge(start: int, end: int) -> Edge:
    return (start, end) if start < end else (end, start)


def _same_directed_edge(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start == b_start and a_end == b_end


def _flipped_triangle(vertices: Tuple[int, int, int]) -> Tuple[int, int, int]:
    return vertices[0], vertices[2], vertices[1]


def _flipped_uvs(
    values: Tuple[Optional[Vec2], Optional[Vec2], Optional[Vec2]],
) -> Tuple[Optional[Vec2], Optional[Vec2], Optional[Vec2]]:
    return values[0], values[2], values[1]


def _triangle_area(a: Vec3, b: Vec3, c: Vec3) -> float:
    cross = _cross(_subtract(b, a), _subtract(c, a))
    return 0.5 * math.sqrt(_dot(cross, cross))


def _distance_sq(a: Vec3, b: Vec3) -> float:
    return sum((a[axis] - b[axis]) ** 2 for axis in range(3))


def _subtract(a: Vec3, b: Vec3) -> Vec3:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )
