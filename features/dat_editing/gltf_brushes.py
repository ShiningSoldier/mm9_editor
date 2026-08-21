"""Build writer-ready legacy ED Brushes from analyzed glTF mesh components.

Phase 4 remains a pure planning layer: it creates ``LegacyEdBrush`` values and
structured provenance, but never writes an ED file.  The plan exposes brushes
for writing only when every component, material, projection, and budget check
passes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from features.dat_editing import geometry_scene
from features.dat_editing import gltf_materials
from features.dat_editing import legacy_ed_writer
from features.dat_editing import mesh_topology


Vec3 = Tuple[float, float, float]

STRICT_CONVEX = "strict_convex"
TRIANGLE_SLAB = "triangle_slab"
WORLD_ALIGNED_PROJECTION = gltf_materials.WORLD_ALIGNED_PROJECTION

DEFAULT_MAX_BRUSHES = 1500
DEFAULT_MAX_SURFACES = 12000
MAX_POINTS_PER_BRUSH = 65535
MAX_VERTICES_PER_SURFACE = 64


@dataclass(frozen=True)
class BrushDiagnostic:
    severity: str
    code: str
    message: str
    component_id: str = ""
    source_face_index: Optional[int] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "component_id": self.component_id or None,
            "source_face_index": self.source_face_index,
        }


@dataclass(frozen=True)
class BrushSurfaceProvenance:
    surface_index: int
    role: str
    source_face_index: Optional[int]
    source_material_name: str
    texture_name: str
    texture_resolution_source: str
    texture_dimension_source: str
    uv_method: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "surface_index": self.surface_index,
            "role": self.role,
            "source_face_index": self.source_face_index,
            "source_material_name": self.source_material_name,
            "texture_name": self.texture_name,
            "texture_resolution_source": self.texture_resolution_source,
            "texture_dimension_source": self.texture_dimension_source,
            "uv_method": self.uv_method,
        }


@dataclass(frozen=True)
class PlannedEdBrush:
    brush_id: str
    component_id: str
    name: str
    output_classification: str
    source_face_indices: Tuple[int, ...]
    nominal_volume: float
    brush: legacy_ed_writer.LegacyEdBrush
    surfaces: Tuple[BrushSurfaceProvenance, ...]

    def to_dict(self) -> Dict[str, object]:
        bounds_min, bounds_max = _bounds(self.brush.points)
        return {
            "brush_id": self.brush_id,
            "component_id": self.component_id,
            "name": self.name,
            "output_classification": self.output_classification,
            "source_face_indices": list(self.source_face_indices),
            "nominal_volume": self.nominal_volume,
            "point_count": len(self.brush.points),
            "surface_count": len(self.brush.surfaces),
            "bounds": {
                "min": list(bounds_min) if bounds_min is not None else None,
                "max": list(bounds_max) if bounds_max is not None else None,
            },
            "surfaces": [item.to_dict() for item in self.surfaces],
        }


@dataclass(frozen=True)
class ComponentBrushPlan:
    component_id: str
    geometry_policy: str
    status: str
    output_classification: str
    source_triangle_count: int
    source_volume: float
    generated_brush_ids: Tuple[str, ...]
    generated_brush_count: int
    generated_surface_count: int
    generated_point_count: int
    nominal_generated_volume: float
    nominal_added_volume: float
    diagnostics: Tuple[BrushDiagnostic, ...]

    @property
    def blockers(self) -> Tuple[BrushDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "blocker")

    @property
    def cautions(self) -> Tuple[BrushDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "caution")

    @property
    def notes(self) -> Tuple[BrushDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "note")

    def to_dict(self) -> Dict[str, object]:
        return {
            "component_id": self.component_id,
            "geometry_policy": self.geometry_policy,
            "status": self.status,
            "output_classification": self.output_classification,
            "source_triangle_count": self.source_triangle_count,
            "source_volume": self.source_volume,
            "generated_brush_ids": list(self.generated_brush_ids),
            "generated_brush_count": self.generated_brush_count,
            "generated_surface_count": self.generated_surface_count,
            "generated_point_count": self.generated_point_count,
            "nominal_generated_volume": self.nominal_generated_volume,
            "nominal_added_volume": self.nominal_added_volume,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True)
class GltfBrushPlan:
    status: str
    geometry_policy: str
    planned_brushes: Tuple[PlannedEdBrush, ...]
    components: Tuple[ComponentBrushPlan, ...]
    material_uv_report: gltf_materials.MaterialUvReport
    estimated_brush_count: int
    estimated_surface_count: int
    max_brushes: int
    max_surfaces: int
    diagnostics: Tuple[BrushDiagnostic, ...]

    @property
    def blockers(self) -> Tuple[BrushDiagnostic, ...]:
        result = [item for item in self.diagnostics if item.severity == "blocker"]
        for component in self.components:
            result.extend(component.blockers)
        result.extend(_brush_diagnostic(item) for item in self.material_uv_report.blockers)
        return _unique_diagnostics(result)

    @property
    def cautions(self) -> Tuple[BrushDiagnostic, ...]:
        result = [item for item in self.diagnostics if item.severity == "caution"]
        for component in self.components:
            result.extend(component.cautions)
        result.extend(_brush_diagnostic(item) for item in self.material_uv_report.cautions)
        return _unique_diagnostics(result)

    @property
    def notes(self) -> Tuple[BrushDiagnostic, ...]:
        result = [item for item in self.diagnostics if item.severity == "note"]
        for component in self.components:
            result.extend(component.notes)
        result.extend(_brush_diagnostic(item) for item in self.material_uv_report.notes)
        return _unique_diagnostics(result)

    @property
    def write_ready_brushes(self) -> Tuple[legacy_ed_writer.LegacyEdBrush, ...]:
        if self.status != "ready":
            return ()
        return tuple(item.brush for item in self.planned_brushes)

    @property
    def brush_names(self) -> Tuple[str, ...]:
        return tuple(item.name for item in self.planned_brushes)

    def to_dict(self) -> Dict[str, object]:
        generated_surface_count = sum(len(item.brush.surfaces) for item in self.planned_brushes)
        generated_point_count = sum(len(item.brush.points) for item in self.planned_brushes)
        return {
            "status": self.status,
            "geometry_policy": self.geometry_policy,
            "budgets": {
                "estimated_brushes": self.estimated_brush_count,
                "estimated_surfaces": self.estimated_surface_count,
                "max_brushes": self.max_brushes,
                "max_surfaces": self.max_surfaces,
                "brushes_pass": self.estimated_brush_count <= self.max_brushes,
                "surfaces_pass": self.estimated_surface_count <= self.max_surfaces,
            },
            "generated": {
                "brushes": len(self.planned_brushes),
                "surfaces": generated_surface_count,
                "points": generated_point_count,
            },
            "material_uv": self.material_uv_report.to_dict(),
            "materials": [item.to_dict() for item in self.material_uv_report.materials],
            "components": [item.to_dict() for item in self.components],
            "brushes": [item.to_dict() for item in self.planned_brushes],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "blockers": [item.to_dict() for item in self.blockers],
            "cautions": [item.to_dict() for item in self.cautions],
            "notes": [item.to_dict() for item in self.notes],
        }


@dataclass(frozen=True)
class _SurfaceBuild:
    role: str
    source_face_index: Optional[int]
    source_material_name: str
    texture_name: str
    texture_resolution_source: str
    texture_dimension_source: str
    uv_method: str


@dataclass(frozen=True)
class _BuiltBrush:
    brush: legacy_ed_writer.LegacyEdBrush
    source_face_indices: Tuple[int, ...]
    nominal_volume: float
    output_classification: str
    surfaces: Tuple[_SurfaceBuild, ...]


def build_gltf_brush_plan(
    scene: geometry_scene.GeometryScene,
    topology: mesh_topology.MeshTopologyReport,
    *,
    geometry_policy: str = STRICT_CONVEX,
    material_map: Optional[Mapping[str, str]] = None,
    fallback_texture: Optional[str] = None,
    texture_dimensions: Optional[Mapping[str, Sequence[float]]] = None,
    texture_size_lookup: Optional[gltf_materials.TextureSizeLookup] = None,
    texture_bytes_lookup: Optional[gltf_materials.TextureBytesLookup] = None,
    fallback_texture_size: Optional[Sequence[float]] = None,
    default_uv_projection: Optional[str] = None,
    slab_thickness: Optional[float] = None,
    slab_back_texture: Optional[str] = None,
    slab_side_texture: Optional[str] = None,
    max_brushes: int = DEFAULT_MAX_BRUSHES,
    max_surfaces: int = DEFAULT_MAX_SURFACES,
) -> GltfBrushPlan:
    """Convert analyzed components into a fail-closed legacy ED Brush plan."""
    if not isinstance(scene, geometry_scene.GeometryScene):
        raise TypeError("scene must be a GeometryScene")
    if not isinstance(topology, mesh_topology.MeshTopologyReport):
        raise TypeError("topology must be a MeshTopologyReport")
    policy = str(geometry_policy or "")
    if policy not in {STRICT_CONVEX, TRIANGLE_SLAB}:
        raise ValueError("geometry_policy must be 'strict_convex' or 'triangle_slab'")
    max_brushes = _budget("max_brushes", max_brushes, DEFAULT_MAX_BRUSHES)
    max_surfaces = _budget("max_surfaces", max_surfaces, DEFAULT_MAX_SURFACES)
    material_converter = gltf_materials.MaterialUvConverter(
        scene,
        material_map=material_map,
        fallback_texture=fallback_texture,
        texture_dimensions=texture_dimensions,
        texture_size_lookup=texture_size_lookup,
        texture_bytes_lookup=texture_bytes_lookup,
        fallback_texture_size=fallback_texture_size,
        default_uv_projection=default_uv_projection,
    )

    thickness: Optional[float] = None
    global_diagnostics: List[BrushDiagnostic] = []
    if str(scene.source_path) != topology.source_path:
        global_diagnostics.append(_diagnostic(
            "blocker",
            "topology_source_mismatch",
            "the GeometryScene source path does not match the topology report source path",
        ))
    if len(scene.models) != topology.source_model_count:
        global_diagnostics.append(_diagnostic(
            "blocker",
            "topology_model_count_mismatch",
            f"the GeometryScene has {len(scene.models)} models but the topology report records "
            f"{topology.source_model_count}",
        ))
    if policy == TRIANGLE_SLAB:
        thickness = _positive_finite("slab_thickness", slab_thickness)
        if thickness <= topology.weld_tolerance:
            global_diagnostics.append(_diagnostic(
                "blocker",
                "slab_thickness_within_weld_tolerance",
                f"slab thickness {thickness:g} must exceed the {topology.weld_tolerance:g}-unit weld tolerance",
            ))
        for option_name, texture_name in (
            ("slab_back_texture", slab_back_texture),
            ("slab_side_texture", slab_side_texture),
        ):
            try:
                gltf_materials.validate_dtx_texture_path(texture_name)
            except ValueError as exc:
                raise ValueError(f"{option_name} {exc}") from exc
    elif slab_thickness is not None:
        _positive_finite("slab_thickness", slab_thickness)

    estimated_brushes, estimated_surfaces = _estimated_counts(topology.components, policy)
    if estimated_brushes > max_brushes:
        global_diagnostics.append(_diagnostic(
            "blocker",
            "brush_budget_exceeded",
            f"policy {policy} estimates {estimated_brushes} Brushes, exceeding the configured limit {max_brushes}",
        ))
    if estimated_surfaces > max_surfaces:
        global_diagnostics.append(_diagnostic(
            "blocker",
            "surface_budget_exceeded",
            f"policy {policy} estimates {estimated_surfaces} surfaces, exceeding the configured limit {max_surfaces}",
        ))
    budget_blocked = any(
        item.code in {"brush_budget_exceeded", "surface_budget_exceeded"}
        for item in global_diagnostics
    )

    planned_brushes: List[PlannedEdBrush] = []
    component_plans: List[ComponentBrushPlan] = []
    used_names: Dict[str, int] = {}

    for component in topology.components:
        diagnostics: List[BrushDiagnostic] = []
        compatible = _policy_compatible(component, policy, diagnostics)
        local_built: Tuple[_BuiltBrush, ...] = ()
        if compatible and budget_blocked:
            diagnostics.append(_diagnostic(
                "blocker",
                "global_budget_preflight_failed",
                f"{component.component_id} was not built because the global Brush budget preflight failed",
                component_id=component.component_id,
            ))
        elif compatible and global_diagnostics:
            diagnostics.append(_diagnostic(
                "blocker",
                "global_preflight_failed",
                f"{component.component_id} was not built because the global Brush preflight failed",
                component_id=component.component_id,
            ))
        elif compatible and not global_diagnostics:
            if policy == STRICT_CONVEX:
                local_built, build_diagnostics = _build_exact_component(
                    component,
                    material_converter=material_converter,
                    enclosure_tolerance=max(1.0e-5, topology.plane_tolerance),
                )
            else:
                local_built, build_diagnostics = _build_slab_component(
                    component,
                    thickness=float(thickness),
                    back_texture=str(slab_back_texture),
                    side_texture=str(slab_side_texture),
                    material_converter=material_converter,
                    enclosure_tolerance=max(1.0e-5, topology.plane_tolerance),
                )
            diagnostics.extend(build_diagnostics)

        diagnostics = list(_unique_diagnostics(diagnostics))
        if any(item.severity == "blocker" for item in diagnostics):
            local_built = ()

        generated: List[PlannedEdBrush] = []
        for item in local_built:
            brush_id = f"brush_{len(planned_brushes):06d}"
            base_name = _brush_base_name(component, item.source_face_indices, policy)
            name = _unique_brush_name(base_name, used_names)
            brush = replace(item.brush, name=name)
            surfaces = tuple(
                BrushSurfaceProvenance(
                    surface_index=index,
                    role=surface.role,
                    source_face_index=surface.source_face_index,
                    source_material_name=surface.source_material_name,
                    texture_name=surface.texture_name,
                    texture_resolution_source=surface.texture_resolution_source,
                    texture_dimension_source=surface.texture_dimension_source,
                    uv_method=surface.uv_method,
                )
                for index, surface in enumerate(item.surfaces)
            )
            planned = PlannedEdBrush(
                brush_id=brush_id,
                component_id=component.component_id,
                name=name,
                output_classification=item.output_classification,
                source_face_indices=item.source_face_indices,
                nominal_volume=item.nominal_volume,
                brush=brush,
                surfaces=surfaces,
            )
            planned_brushes.append(planned)
            generated.append(planned)

        output_classification = "not_generated"
        if generated:
            output_classification = generated[0].output_classification
        generated_volume = sum(item.nominal_volume for item in generated)
        component_plans.append(ComponentBrushPlan(
            component_id=component.component_id,
            geometry_policy=policy,
            status="planned" if generated else "blocked",
            output_classification=output_classification,
            source_triangle_count=component.source_triangle_count,
            source_volume=component.absolute_volume,
            generated_brush_ids=tuple(item.brush_id for item in generated),
            generated_brush_count=len(generated),
            generated_surface_count=sum(len(item.brush.surfaces) for item in generated),
            generated_point_count=sum(len(item.brush.points) for item in generated),
            nominal_generated_volume=generated_volume,
            nominal_added_volume=generated_volume if policy == TRIANGLE_SLAB else 0.0,
            diagnostics=tuple(diagnostics),
        ))

    if not topology.components:
        global_diagnostics.append(_diagnostic(
            "blocker",
            "empty_topology_report",
            "the topology report contains no components",
        ))
    material_uv_report = material_converter.report()
    all_blockers = [item for item in global_diagnostics if item.severity == "blocker"]
    for component in component_plans:
        all_blockers.extend(component.blockers)
    all_blockers.extend(_brush_diagnostic(item) for item in material_uv_report.blockers)
    status = "ready" if planned_brushes and not all_blockers else "blocked"

    return GltfBrushPlan(
        status=status,
        geometry_policy=policy,
        planned_brushes=tuple(planned_brushes),
        components=tuple(component_plans),
        material_uv_report=material_uv_report,
        estimated_brush_count=estimated_brushes,
        estimated_surface_count=estimated_surfaces,
        max_brushes=max_brushes,
        max_surfaces=max_surfaces,
        diagnostics=tuple(_unique_diagnostics(global_diagnostics)),
    )


def _build_exact_component(
    component: mesh_topology.TopologyComponent,
    *,
    material_converter: gltf_materials.MaterialUvConverter,
    enclosure_tolerance: float,
) -> Tuple[Tuple[_BuiltBrush, ...], Tuple[BrushDiagnostic, ...]]:
    diagnostics: List[BrushDiagnostic] = []
    if len(component.points) > MAX_POINTS_PER_BRUSH:
        diagnostics.append(_component_diagnostic(
            component,
            "blocker",
            "brush_point_limit_exceeded",
            f"component has {len(component.points)} points; the ED writer limit is {MAX_POINTS_PER_BRUSH}",
        ))
        return (), tuple(diagnostics)

    surfaces: List[legacy_ed_writer.LegacyEdSurface] = []
    provenance: List[_SurfaceBuild] = []
    for face in component.faces:
        resolved, material_diagnostics = material_converter.resolve_face(component, face)
        diagnostics.extend(_brush_diagnostic(item) for item in material_diagnostics)
        if resolved is None:
            continue
        surface = _resolved_surface(component.points, face.vertex_indices, resolved)
        surfaces.append(surface)
        provenance.append(_surface_build_from_resolution(
            "source",
            face.source_face_index,
            face.material_name,
            resolved,
        ))

    if any(item.severity == "blocker" for item in diagnostics):
        return (), tuple(_unique_diagnostics(diagnostics))
    brush = legacy_ed_writer.LegacyEdBrush(
        points=component.points,
        surfaces=tuple(surfaces),
        color_rgb=(128, 128, 128),
    )
    normalized, validation_diagnostic = _validated_brush(
        brush,
        component_id=component.component_id,
        enclosure_tolerance=enclosure_tolerance,
    )
    if validation_diagnostic is not None:
        diagnostics.append(validation_diagnostic)
        return (), tuple(_unique_diagnostics(diagnostics))
    return (_BuiltBrush(
        brush=normalized,
        source_face_indices=component.source_face_indices,
        nominal_volume=component.absolute_volume,
        output_classification="exact",
        surfaces=tuple(provenance),
    ),), tuple(_unique_diagnostics(diagnostics))


def _build_slab_component(
    component: mesh_topology.TopologyComponent,
    *,
    thickness: float,
    back_texture: str,
    side_texture: str,
    material_converter: gltf_materials.MaterialUvConverter,
    enclosure_tolerance: float,
) -> Tuple[Tuple[_BuiltBrush, ...], Tuple[BrushDiagnostic, ...]]:
    diagnostics: List[BrushDiagnostic] = []
    result: List[_BuiltBrush] = []
    for face in component.faces:
        front_resolution, front_diagnostics = material_converter.resolve_face(component, face)
        diagnostics.extend(_brush_diagnostic(item) for item in front_diagnostics)
        if front_resolution is None:
            continue

        front_points = tuple(component.points[index] for index in face.vertex_indices)
        normal, _distance = _polygon_plane(front_points, (0, 1, 2))
        back_points = tuple(_subtract(point, _scale(normal, thickness)) for point in front_points)
        points = front_points + back_points
        front_indices = (0, 1, 2)
        back_indices = (5, 4, 3)
        front_surface = _resolved_surface(points, front_indices, front_resolution)

        generated_surfaces: List[legacy_ed_writer.LegacyEdSurface] = [front_surface]
        generated_provenance: List[_SurfaceBuild] = [_surface_build_from_resolution(
            "front",
            face.source_face_index,
            face.material_name,
            front_resolution,
        )]
        back_points_for_surface = tuple(points[index] for index in back_indices)
        back_resolution, back_diagnostics = material_converter.resolve_generated_surface(
            back_points_for_surface,
            back_texture,
            resolution_source="slab_back_option",
            component_id=component.component_id,
            source_face_index=face.source_face_index,
        )
        diagnostics.extend(_brush_diagnostic(item) for item in back_diagnostics)
        if back_resolution is None:
            continue
        back_surface = _resolved_surface(points, back_indices, back_resolution)
        generated_surfaces.append(back_surface)
        generated_provenance.append(_surface_build_from_resolution(
            "generated_back",
            face.source_face_index,
            "",
            back_resolution,
        ))
        generated_failed = False
        for start, end in ((0, 1), (1, 2), (2, 0)):
            side_indices = (start, start + 3, end + 3, end)
            side_points = tuple(points[index] for index in side_indices)
            side_resolution, side_diagnostics = material_converter.resolve_generated_surface(
                side_points,
                side_texture,
                resolution_source="slab_side_option",
                component_id=component.component_id,
                source_face_index=face.source_face_index,
            )
            diagnostics.extend(_brush_diagnostic(item) for item in side_diagnostics)
            if side_resolution is None:
                generated_failed = True
                break
            generated_surfaces.append(_resolved_surface(points, side_indices, side_resolution))
            generated_provenance.append(_surface_build_from_resolution(
                "generated_side",
                face.source_face_index,
                "",
                side_resolution,
            ))
        if generated_failed:
            continue

        brush = legacy_ed_writer.LegacyEdBrush(
            points=points,
            surfaces=tuple(generated_surfaces),
            color_rgb=(160, 112, 96),
        )
        normalized, validation_diagnostic = _validated_brush(
            brush,
            component_id=component.component_id,
            source_face_index=face.source_face_index,
            enclosure_tolerance=enclosure_tolerance,
        )
        if validation_diagnostic is not None:
            diagnostics.append(validation_diagnostic)
            continue
        result.append(_BuiltBrush(
            brush=normalized,
            source_face_indices=(face.source_face_index,),
            nominal_volume=_triangle_area(*front_points) * thickness,
            output_classification="approximated",
            surfaces=tuple(generated_provenance),
        ))

    if any(item.severity == "blocker" for item in diagnostics) or len(result) != len(component.faces):
        return (), tuple(_unique_diagnostics(diagnostics))
    return tuple(result), tuple(_unique_diagnostics(diagnostics))


def _resolved_surface(
    points: Sequence[Vec3],
    indices: Sequence[int],
    resolved: gltf_materials.ResolvedSurfaceProjection,
) -> legacy_ed_writer.LegacyEdSurface:
    normal, distance = _polygon_plane(points, indices)
    return legacy_ed_writer.LegacyEdSurface(
        vertex_indices=tuple(int(index) for index in indices),
        plane_normal=normal,
        plane_dist=distance,
        texture_name=resolved.texture.texture_name,
        uv_o=resolved.uv_o,
        uv_p=resolved.uv_p,
        uv_q=resolved.uv_q,
        texture_flags=resolved.texture_flags,
        surface_flags=resolved.surface_flags,
        shade_rgb=(0, 0, 0),
    )


def _policy_compatible(
    component: mesh_topology.TopologyComponent,
    policy: str,
    diagnostics: List[BrushDiagnostic],
) -> bool:
    if policy == STRICT_CONVEX:
        if component.classification == mesh_topology.EXACT_CONVEX:
            return True
        code = (
            "blocked_open"
            if component.classification == mesh_topology.SLAB_CANDIDATE
            else "topology_not_strict_convex"
        )
        diagnostics.append(_component_diagnostic(
            component,
            "blocker",
            code,
            f"component classification {component.classification!r} is not accepted by strict_convex",
        ))
        return False
    if component.classification in {mesh_topology.EXACT_CONVEX, mesh_topology.SLAB_CANDIDATE}:
        return True
    diagnostics.append(_component_diagnostic(
        component,
        "blocker",
        "topology_not_slab_safe",
        f"component classification {component.classification!r} is not accepted by triangle_slab",
    ))
    return False


def _estimated_counts(
    components: Sequence[mesh_topology.TopologyComponent],
    policy: str,
) -> Tuple[int, int]:
    brushes = 0
    surfaces = 0
    for component in components:
        if policy == STRICT_CONVEX and component.classification == mesh_topology.EXACT_CONVEX:
            brushes += 1
            surfaces += len(component.faces)
        elif policy == TRIANGLE_SLAB and component.classification in {
            mesh_topology.EXACT_CONVEX,
            mesh_topology.SLAB_CANDIDATE,
        }:
            brushes += len(component.faces)
            surfaces += len(component.faces) * 5
    return brushes, surfaces


def _validated_brush(
    brush: legacy_ed_writer.LegacyEdBrush,
    *,
    component_id: str,
    enclosure_tolerance: float,
    source_face_index: Optional[int] = None,
) -> Tuple[legacy_ed_writer.LegacyEdBrush, Optional[BrushDiagnostic]]:
    try:
        normalized = legacy_ed_writer.normalize_brush_points(brush)
        if len(normalized.points) > MAX_POINTS_PER_BRUSH:
            raise ValueError(f"point count exceeds {MAX_POINTS_PER_BRUSH}")
        if any(
            len(surface.vertex_indices) > MAX_VERTICES_PER_SURFACE
            for surface in normalized.surfaces
        ):
            raise ValueError(f"surface vertex count exceeds {MAX_VERTICES_PER_SURFACE}")
        if not _brush_encloses_points(normalized, enclosure_tolerance):
            raise ValueError("surface planes do not enclose all Brush points")
        legacy_ed_writer.write_brush_record(normalized)
    except (TypeError, ValueError, OverflowError) as exc:
        return brush, _diagnostic(
            "blocker",
            "writer_rejected_brush",
            f"legacy ED writer validation failed: {exc}",
            component_id=component_id,
            source_face_index=source_face_index,
        )
    return normalized, None


def _brush_encloses_points(brush: legacy_ed_writer.LegacyEdBrush, tolerance: float) -> bool:
    for surface in brush.surfaces:
        for point in brush.points:
            if _dot(surface.plane_normal, point) - surface.plane_dist > tolerance:
                return False
    return True


def _surface_build_from_resolution(
    role: str,
    source_face_index: Optional[int],
    source_material_name: str,
    resolved: gltf_materials.ResolvedSurfaceProjection,
) -> _SurfaceBuild:
    return _SurfaceBuild(
        role=role,
        source_face_index=source_face_index,
        source_material_name=source_material_name,
        texture_name=resolved.texture.texture_name,
        texture_resolution_source=resolved.texture.resolution_source,
        texture_dimension_source=resolved.texture.dimension_source,
        uv_method=resolved.uv_method,
    )


def _brush_diagnostic(diagnostic: gltf_materials.MaterialDiagnostic) -> BrushDiagnostic:
    return BrushDiagnostic(
        severity=diagnostic.severity,
        code=diagnostic.code,
        message=diagnostic.message,
        component_id=diagnostic.component_id,
        source_face_index=diagnostic.source_face_index,
    )


def _positive_finite(name: str, value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite positive number") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be a finite positive number")
    return result


def _budget(name: str, value: object, hard_limit: int) -> int:
    if type(value) is not int or value <= 0 or value > hard_limit:
        raise ValueError(f"{name} must be an integer in 1..{hard_limit}")
    return int(value)


def _brush_base_name(
    component: mesh_topology.TopologyComponent,
    source_face_indices: Tuple[int, ...],
    policy: str,
) -> str:
    base = f"{component.model_name}_{component.component_id}"
    if policy == TRIANGLE_SLAB and source_face_indices:
        base += f"_face_{source_face_indices[0]:06d}"
    return _safe_name(base)


def _safe_name(value: str, limit: int = 96) -> str:
    result: List[str] = []
    previous_underscore = False
    for char in str(value):
        safe = char if ord(char) <= 255 and (char.isalnum() or char in "-_.") else "_"
        if safe == "_" and previous_underscore:
            continue
        result.append(safe)
        previous_underscore = safe == "_"
    text = "".join(result).strip("_.") or "Brush"
    return text[:limit]


def _unique_brush_name(base_name: str, used: Dict[str, int]) -> str:
    candidate = base_name
    suffix_number = 2
    while candidate.casefold() in used:
        suffix = f"_{suffix_number}"
        candidate = base_name[:max(1, 96 - len(suffix))] + suffix
        suffix_number += 1
    used[candidate.casefold()] = 1
    return candidate


def _component_diagnostic(
    component: mesh_topology.TopologyComponent,
    severity: str,
    code: str,
    message: str,
) -> BrushDiagnostic:
    return _diagnostic(
        severity,
        code,
        f"{component.component_id}: {message}",
        component_id=component.component_id,
    )


def _diagnostic(
    severity: str,
    code: str,
    message: str,
    *,
    component_id: str = "",
    source_face_index: Optional[int] = None,
) -> BrushDiagnostic:
    return BrushDiagnostic(
        severity=severity,
        code=code,
        message=message,
        component_id=component_id,
        source_face_index=source_face_index,
    )


def _unique_diagnostics(values: Sequence[BrushDiagnostic]) -> Tuple[BrushDiagnostic, ...]:
    result: List[BrushDiagnostic] = []
    seen = set()
    for item in values:
        key = (item.severity, item.code, item.message, item.component_id, item.source_face_index)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


def _polygon_plane(points: Sequence[Vec3], indices: Sequence[int]) -> Tuple[Vec3, float]:
    first = points[int(indices[0])]
    for offset in range(1, len(indices) - 1):
        second = points[int(indices[offset])]
        third = points[int(indices[offset + 1])]
        normal = _cross(_subtract(second, first), _subtract(third, first))
        length = math.sqrt(_dot(normal, normal))
        if length <= 1.0e-8:
            continue
        normal = _scale(normal, 1.0 / length)
        return normal, _dot(normal, first)
    raise ValueError("surface has no stable plane")


def _triangle_area(a: Vec3, b: Vec3, c: Vec3) -> float:
    normal = _cross(_subtract(b, a), _subtract(c, a))
    return 0.5 * math.sqrt(_dot(normal, normal))


def _bounds(points: Sequence[Vec3]) -> Tuple[Optional[Vec3], Optional[Vec3]]:
    if not points:
        return None, None
    return (
        tuple(min(point[axis] for point in points) for axis in range(3)),
        tuple(max(point[axis] for point in points) for axis in range(3)),
    )


def _subtract(a: Vec3, b: Vec3) -> Vec3:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _scale(value: Vec3, factor: float) -> Vec3:
    return value[0] * factor, value[1] * factor, value[2] * factor


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )
