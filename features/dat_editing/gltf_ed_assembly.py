"""Assemble Phase 4/5 Brush plans into validated ED v1249 documents.

Phase 6 is deliberately in-memory.  It owns deterministic ED names, the two
contracted document layouts, minimal full-world scaffolding, and an immediate
reader round-trip.  Choosing output paths and committing files belongs to a
later phase.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from features.dat_editing import gltf_brushes
from features.dat_editing import legacy_ed
from features.dat_editing import legacy_ed_writer


Vec3 = Tuple[float, float, float]

PREFAB = "prefab"
FULL_WORLD = "full_world"
OUTPUT_MODES = (PREFAB, FULL_WORLD)

UNCOMPRESSED_NAMED_GROUP = "uncompressed_named_group_prefab"
ZLIB_BLOCKED_FULL_LEVEL = "zlib_blocked_full_level"


@dataclass(frozen=True)
class EdAssemblyDiagnostic:
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
class EdNameMapping:
    kind: str
    source_id: str
    source_name: str
    output_name: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "kind": self.kind,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "output_name": self.output_name,
        }


@dataclass(frozen=True)
class EdNodeAssignment:
    role: str
    node_id: int
    name: str
    brush_index: Optional[int] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "role": self.role,
            "node_id": self.node_id,
            "name": self.name,
            "brush_index": self.brush_index,
        }


@dataclass(frozen=True)
class FullWorldScaffold:
    world_properties_position: Vec3
    start_point_position: Vec3
    light_position: Vec3
    infostring: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "world_properties": {
                "name": "WorldProperties0",
                "position": list(self.world_properties_position),
            },
            "start_point": {
                "name": "StartPoint0",
                "position": list(self.start_point_position),
            },
            "light": {
                "name": "Light0",
                "position": list(self.light_position),
            },
            "infostring": self.infostring,
        }


@dataclass(frozen=True)
class EdRoundTripValidation:
    writer: str
    reader_roundtrip: str
    expected_brush_count: int = 0
    recovered_brush_count: int = 0
    expected_surface_count: int = 0
    recovered_surface_count: int = 0
    expected_point_count: int = 0
    recovered_point_count: int = 0
    expected_node_count: int = 0
    recovered_node_count: int = 0
    expected_object_count: int = 0
    recovered_object_count: int = 0
    node_layout_kind: str = ""
    mismatches: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "writer": self.writer,
            "reader_roundtrip": self.reader_roundtrip,
            "expected": {
                "brushes": self.expected_brush_count,
                "surfaces": self.expected_surface_count,
                "points": self.expected_point_count,
                "nodes": self.expected_node_count,
                "objects": self.expected_object_count,
            },
            "recovered": {
                "brushes": self.recovered_brush_count,
                "surfaces": self.recovered_surface_count,
                "points": self.recovered_point_count,
                "nodes": self.recovered_node_count,
                "objects": self.recovered_object_count,
            },
            "node_layout_kind": self.node_layout_kind or None,
            "mismatches": list(self.mismatches),
        }


@dataclass(frozen=True)
class GltfEdAssembly:
    status: str
    output_mode: str
    ed_bytes: bytes
    wrapper_kind: str
    group_name: str
    brush_names: Tuple[str, ...]
    brush_count: int
    surface_count: int
    point_count: int
    node_count: int
    object_count: int
    name_map: Tuple[EdNameMapping, ...]
    node_assignments: Tuple[EdNodeAssignment, ...]
    scaffold: Optional[FullWorldScaffold]
    wrapper_block_count: int
    wrapper_block_size: int
    validation: EdRoundTripValidation
    diagnostics: Tuple[EdAssemblyDiagnostic, ...]

    @property
    def data(self) -> bytes:
        """Alias used by callers that treat the result as an in-memory artifact."""
        return self.ed_bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.ed_bytes).hexdigest() if self.ed_bytes else ""

    @property
    def blockers(self) -> Tuple[EdAssemblyDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "blocker")

    @property
    def cautions(self) -> Tuple[EdAssemblyDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "caution")

    @property
    def notes(self) -> Tuple[EdAssemblyDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "note")

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": 1,
            "kind": "mm9_gltf_to_ed_assembly",
            "status": self.status,
            "output_mode": self.output_mode,
            "output": {
                "ed_version": legacy_ed.LEGACY_ED_VERSION,
                "wrapper_kind": self.wrapper_kind or None,
                "byte_size": len(self.ed_bytes),
                "sha256": self.sha256 or None,
                "brush_count": self.brush_count,
                "surface_count": self.surface_count,
                "point_count": self.point_count,
                "node_count": self.node_count,
                "object_count": self.object_count,
                "group_name": self.group_name or None,
                "brush_names": list(self.brush_names),
                "name_map": [item.to_dict() for item in self.name_map],
                "node_assignments": [item.to_dict() for item in self.node_assignments],
                "wrapper_block_count": self.wrapper_block_count,
                "wrapper_block_size": self.wrapper_block_size,
                "full_world_scaffold": self.scaffold.to_dict() if self.scaffold else None,
            },
            "validation": self.validation.to_dict(),
            "blockers": [item.to_dict() for item in self.blockers],
            "cautions": [item.to_dict() for item in self.cautions],
            "notes": [item.to_dict() for item in self.notes],
        }


def assemble_gltf_ed(
    brush_plan: gltf_brushes.GltfBrushPlan,
    *,
    output_mode: str = PREFAB,
    group_name: str = "ImportedGLTF",
    infostring: Optional[str] = None,
    block_size: int = legacy_ed_writer.DEFAULT_FULL_LEVEL_ZLIB_BLOCK_SIZE,
    world_properties_position: Optional[Sequence[float]] = None,
    start_point_position: Optional[Sequence[float]] = None,
    light_position: Optional[Sequence[float]] = None,
) -> GltfEdAssembly:
    """Assemble a write-ready Phase 4/5 plan, or preserve its blocked state."""
    mode = _output_mode(output_mode)
    if brush_plan.status != "ready":
        message = (
            f"Brush plan status is {brush_plan.status!r}; ED assembly requires a fully ready plan"
        )
        return _empty_assembly(
            status="blocked",
            output_mode=mode,
            diagnostic=EdAssemblyDiagnostic("blocker", "brush_plan_blocked", message),
        )
    return assemble_ed_document(
        brush_plan.write_ready_brushes,
        output_mode=mode,
        group_name=group_name,
        brush_names=brush_plan.brush_names,
        brush_ids=tuple(item.brush_id for item in brush_plan.planned_brushes),
        infostring=infostring,
        block_size=block_size,
        world_properties_position=world_properties_position,
        start_point_position=start_point_position,
        light_position=light_position,
    )


def assemble_ed_document(
    brushes: Sequence[legacy_ed_writer.LegacyEdBrush],
    *,
    output_mode: str = PREFAB,
    group_name: str = "ImportedGLTF",
    brush_names: Sequence[str] = (),
    brush_ids: Sequence[str] = (),
    infostring: Optional[str] = None,
    block_size: int = legacy_ed_writer.DEFAULT_FULL_LEVEL_ZLIB_BLOCK_SIZE,
    world_properties_position: Optional[Sequence[float]] = None,
    start_point_position: Optional[Sequence[float]] = None,
    light_position: Optional[Sequence[float]] = None,
) -> GltfEdAssembly:
    """Assemble generic writer-ready Brushes without touching the filesystem."""
    mode = _output_mode(output_mode)
    source_brushes = tuple(brushes)
    if not source_brushes:
        return _empty_assembly(
            status="blocked",
            output_mode=mode,
            diagnostic=EdAssemblyDiagnostic(
                "blocker", "no_brushes", "ED assembly requires at least one Brush"
            ),
        )
    if brush_names and len(brush_names) != len(source_brushes):
        raise ValueError("brush_names must be empty or contain one name per Brush")
    if brush_ids and len(brush_ids) != len(source_brushes):
        raise ValueError("brush_ids must be empty or contain one ID per Brush")

    diagnostics: List[EdAssemblyDiagnostic] = []
    source_names = tuple(
        str(brush_names[index])
        if brush_names
        else str(brush.name or f"Brush_{index:04d}")
        for index, brush in enumerate(source_brushes)
    )
    output_names = _unique_legacy_names(source_names, fallback="Brush")
    safe_group_name = _safe_legacy_name(group_name, fallback="ImportedGLTF")
    name_map = [
        EdNameMapping("group", "group", str(group_name), safe_group_name),
    ]
    name_map.extend(
        EdNameMapping(
            "brush",
            str(brush_ids[index]) if brush_ids else f"brush_{index:04d}",
            source_names[index],
            output_names[index],
        )
        for index in range(len(source_brushes))
    )
    for item in name_map:
        if item.source_name != item.output_name:
            diagnostics.append(EdAssemblyDiagnostic(
                "note",
                "name_sanitized",
                f"{item.kind} name {item.source_name!r} became {item.output_name!r}",
            ))

    try:
        normalized_brushes = tuple(
            legacy_ed_writer.normalize_brush_points(brush)
            for brush in source_brushes
        )
    except (ValueError, OverflowError) as exc:
        return _failed_assembly(
            status="write_failed",
            output_mode=mode,
            group_name=safe_group_name,
            brush_names=output_names,
            name_map=tuple(name_map),
            diagnostic=EdAssemblyDiagnostic("blocker", "ed_writer_failed", str(exc)),
        )

    scaffold: Optional[FullWorldScaffold] = None
    node_assignments: Tuple[EdNodeAssignment, ...] = ()
    wrapper_kind = UNCOMPRESSED_NAMED_GROUP
    wrapper_metadata: Dict[str, int] = {}
    try:
        if mode == PREFAB:
            generated = legacy_ed_writer.build_named_group_prefab(
                normalized_brushes,
                group_name=safe_group_name,
                brush_names=output_names,
            )
            diagnostics.append(EdAssemblyDiagnostic(
                "note",
                "prefab_scope",
                "Named-group prefab is intended for insertion into a DEDit world",
            ))
        else:
            default_positions = _default_full_world_positions(normalized_brushes)
            encoded_infostring = _latin1_text(
                legacy_ed_writer.DEFAULT_FULL_LEVEL_INFOSTRING
                if infostring is None
                else infostring
            )
            scaffold = FullWorldScaffold(
                world_properties_position=(
                    default_positions[0]
                    if world_properties_position is None
                    else _finite_vec3("world_properties_position", world_properties_position)
                ),
                start_point_position=(
                    default_positions[1]
                    if start_point_position is None
                    else _finite_vec3("start_point_position", start_point_position)
                ),
                light_position=(
                    default_positions[2]
                    if light_position is None
                    else _finite_vec3("light_position", light_position)
                ),
                infostring=encoded_infostring,
            )
            root, node_assignments = _full_world_root(
                output_names,
                safe_group_name,
                scaffold,
            )
            generated, wrapper_metadata = legacy_ed_writer.build_zlib_blocked_full_world(
                normalized_brushes,
                root,
                infostring=encoded_infostring,
                block_size=block_size,
            )
            wrapper_kind = ZLIB_BLOCKED_FULL_LEVEL
            diagnostics.append(EdAssemblyDiagnostic(
                "caution",
                "minimal_full_world_scaffold",
                "Full-world output contains only imported Brushes and minimal load scaffolding; it is not game-ready",
            ))
    except (ValueError, OverflowError) as exc:
        return _failed_assembly(
            status="write_failed",
            output_mode=mode,
            group_name=safe_group_name,
            brush_names=output_names,
            name_map=tuple(name_map),
            diagnostic=EdAssemblyDiagnostic("blocker", "ed_writer_failed", str(exc)),
            scaffold=scaffold,
            node_assignments=node_assignments,
        )

    validation = _validate_roundtrip(
        generated,
        normalized_brushes,
        output_mode=mode,
        group_name=safe_group_name,
        brush_names=output_names,
        scaffold=scaffold,
    )
    status = "ready_prefab" if mode == PREFAB else "ready_full_world"
    if validation.reader_roundtrip != "pass":
        status = "validation_failed"
        diagnostics.append(EdAssemblyDiagnostic(
            "blocker",
            "ed_roundtrip_failed",
            "; ".join(validation.mismatches) or "generated ED did not pass reader round-trip",
        ))

    return GltfEdAssembly(
        status=status,
        output_mode=mode,
        ed_bytes=generated if status.startswith("ready_") else b"",
        wrapper_kind=wrapper_kind,
        group_name=safe_group_name,
        brush_names=output_names,
        brush_count=len(normalized_brushes),
        surface_count=sum(len(brush.surfaces) for brush in normalized_brushes),
        point_count=sum(len(brush.points) for brush in normalized_brushes),
        node_count=validation.recovered_node_count,
        object_count=validation.recovered_object_count,
        name_map=tuple(name_map),
        node_assignments=node_assignments,
        scaffold=scaffold,
        wrapper_block_count=int(wrapper_metadata.get("block_count", 0)),
        wrapper_block_size=int(wrapper_metadata.get("block_size", 0)),
        validation=validation,
        diagnostics=tuple(diagnostics),
    )


def _full_world_root(
    brush_names: Sequence[str],
    group_name: str,
    scaffold: FullWorldScaffold,
) -> Tuple[legacy_ed_writer.LegacyEdNode, Tuple[EdNodeAssignment, ...]]:
    assignments: List[EdNodeAssignment] = [
        EdNodeAssignment("root", 1, "Container"),
        EdNodeAssignment("brush_group", 2, group_name),
    ]
    brush_nodes = []
    for brush_index, name in enumerate(brush_names):
        node_id = brush_index + 3
        brush_nodes.append(legacy_ed_writer.brush_node(
            brush_index,
            name,
            node_id=node_id,
        ))
        assignments.append(EdNodeAssignment("brush", node_id, name, brush_index))
    group = legacy_ed_writer.group_node(
        group_name,
        tuple(brush_nodes),
        node_id=2,
        unknown2=16,
    )
    next_id = len(brush_names) + 3
    world_properties = legacy_ed_writer.object_node(
        "WorldProperties",
        "",
        node_id=next_id,
        properties=legacy_ed_writer.world_properties_object_properties(
            pos=scaffold.world_properties_position,
        ),
    )
    assignments.append(EdNodeAssignment("object", next_id, "WorldProperties0"))
    start_point = legacy_ed_writer.object_node(
        "StartPoint",
        "",
        node_id=next_id + 1,
        properties=legacy_ed_writer.start_point_object_properties(
            pos=scaffold.start_point_position,
        ),
    )
    assignments.append(EdNodeAssignment("object", next_id + 1, "StartPoint0"))
    light = legacy_ed_writer.object_node(
        "Light",
        "",
        node_id=next_id + 2,
        properties=legacy_ed_writer.light_object_properties(
            pos=scaffold.light_position,
        ),
    )
    assignments.append(EdNodeAssignment("object", next_id + 2, "Light0"))
    root = legacy_ed_writer.world_root_node(
        (group, world_properties, start_point, light),
        node_id=1,
        display_name="Container",
        unknown2=24,
    )
    return root, tuple(assignments)


def _validate_roundtrip(
    data: bytes,
    brushes: Sequence[legacy_ed_writer.LegacyEdBrush],
    *,
    output_mode: str,
    group_name: str,
    brush_names: Sequence[str],
    scaffold: Optional[FullWorldScaffold],
) -> EdRoundTripValidation:
    expected_brushes = len(brushes)
    expected_surfaces = sum(len(brush.surfaces) for brush in brushes)
    expected_points = sum(len(brush.points) for brush in brushes)
    expected_nodes = expected_brushes + (2 if output_mode == PREFAB else 5)
    expected_objects = expected_brushes + (0 if output_mode == PREFAB else 3)
    try:
        analysis = legacy_ed.analyze_legacy_ed_bytes(data, source_path="gltf_phase6.ed")
    except Exception as exc:
        return EdRoundTripValidation(
            writer="pass",
            reader_roundtrip="failed",
            expected_brush_count=expected_brushes,
            expected_surface_count=expected_surfaces,
            expected_point_count=expected_points,
            expected_node_count=expected_nodes,
            expected_object_count=expected_objects,
            mismatches=(f"reader rejected generated ED: {exc}",),
        )

    scene = analysis.geometry_scene
    models = scene.mesh_models()
    recovered_brushes = int(scene.metadata.get("recovered_brush_count", 0) or 0)
    recovered_surfaces = int(scene.metadata.get("recovered_polygon_count", 0) or 0)
    recovered_points = sum(len(model.points) for model in models)
    recovered_nodes = _node_count(analysis.node_tree)
    recovered_objects = analysis.object_scan.object_count
    mismatches: List[str] = []

    _expect_equal(mismatches, "Brush count", recovered_brushes, expected_brushes)
    _expect_equal(mismatches, "surface count", recovered_surfaces, expected_surfaces)
    _expect_equal(mismatches, "point count", recovered_points, expected_points)
    _expect_equal(mismatches, "node count", recovered_nodes, expected_nodes)
    _expect_equal(mismatches, "object count", recovered_objects, expected_objects)
    expected_layout = (
        "named_group_brush_nodes"
        if output_mode == PREFAB
        else "named_group_brush_nodes_with_root_objects"
    )
    _expect_equal(
        mismatches,
        "node layout",
        analysis.node_layout.node_layout_kind,
        expected_layout,
    )
    _expect_equal(
        mismatches,
        "Brush node names",
        analysis.node_layout.brush_names,
        tuple(brush_names),
    )
    _validate_brush_payloads(mismatches, brushes, models)

    class_counts = analysis.object_scan.class_counts
    _expect_equal(mismatches, "Brush object count", class_counts.get("Brush", 0), expected_brushes)
    if output_mode == PREFAB:
        _expect_equal(mismatches, "reader wrapper", analysis.node_layout.wrapper, "")
    else:
        _expect_equal(
            mismatches,
            "reader wrapper",
            analysis.node_layout.wrapper,
            ZLIB_BLOCKED_FULL_LEVEL,
        )
        _expect_equal(
            mismatches,
            "declared Brush count",
            scene.metadata.get("declared_brush_count"),
            expected_brushes,
        )
        for class_name in ("WorldProperties", "StartPoint", "Light"):
            _expect_equal(
                mismatches,
                f"{class_name} object count",
                class_counts.get(class_name, 0),
                1,
            )
        _validate_full_world_tree(
            mismatches,
            analysis.node_tree,
            group_name=group_name,
            brush_names=brush_names,
            scaffold=scaffold,
            recovered_infostring=str(scene.metadata.get("infostring", "")),
        )

    return EdRoundTripValidation(
        writer="pass",
        reader_roundtrip="pass" if not mismatches else "failed",
        expected_brush_count=expected_brushes,
        recovered_brush_count=recovered_brushes,
        expected_surface_count=expected_surfaces,
        recovered_surface_count=recovered_surfaces,
        expected_point_count=expected_points,
        recovered_point_count=recovered_points,
        expected_node_count=expected_nodes,
        recovered_node_count=recovered_nodes,
        expected_object_count=expected_objects,
        recovered_object_count=recovered_objects,
        node_layout_kind=analysis.node_layout.node_layout_kind,
        mismatches=tuple(mismatches),
    )


def _validate_brush_payloads(
    mismatches: List[str],
    expected: Sequence[legacy_ed_writer.LegacyEdBrush],
    recovered: Sequence[object],
) -> None:
    if len(expected) != len(recovered):
        return
    for brush_index, (brush, model) in enumerate(zip(expected, recovered)):
        points = tuple(getattr(model, "points", ()) or ())
        faces = tuple(getattr(model, "faces", ()) or ())
        if len(points) != len(brush.points) or len(faces) != len(brush.surfaces):
            continue
        for point_index, (wanted, actual) in enumerate(zip(brush.points, points)):
            if not _vec3_close(wanted, actual):
                mismatches.append(
                    f"Brush {brush_index} point {point_index} changed during round-trip"
                )
                break
        for surface_index, (wanted, actual) in enumerate(zip(brush.surfaces, faces)):
            if tuple(getattr(actual, "vertex_indices", ()) or ()) != wanted.vertex_indices:
                mismatches.append(
                    f"Brush {brush_index} surface {surface_index} vertex indices changed"
                )
            if str(getattr(actual, "material_name", "")) != wanted.texture_name:
                mismatches.append(
                    f"Brush {brush_index} surface {surface_index} texture changed"
                )
            extras = getattr(actual, "extras", {}) or {}
            for field_name, wanted_value in (
                ("uv_o", wanted.uv_o),
                ("uv_p", wanted.uv_p),
                ("uv_q", wanted.uv_q),
            ):
                actual_value = extras.get(field_name)
                if not _vec3_close(wanted_value, actual_value):
                    mismatches.append(
                        f"Brush {brush_index} surface {surface_index} {field_name} changed"
                    )


def _validate_full_world_tree(
    mismatches: List[str],
    root: Optional[legacy_ed.LegacyEdNode],
    *,
    group_name: str,
    brush_names: Sequence[str],
    scaffold: Optional[FullWorldScaffold],
    recovered_infostring: str,
) -> None:
    if root is None:
        mismatches.append("full-world root hierarchy was not decoded")
        return
    _expect_equal(mismatches, "root name", root.node_name, "Container")
    if len(root.children) != 4:
        return
    group = root.children[0]
    _expect_equal(mismatches, "group name", group.node_name, group_name)
    _expect_equal(mismatches, "group Brush count", len(group.children), len(brush_names))
    for brush_index, (node, name) in enumerate(zip(group.children, brush_names)):
        _expect_equal(mismatches, f"Brush node {brush_index} type", node.node_type, legacy_ed.NODE_BRUSH)
        _expect_equal(mismatches, f"Brush node {brush_index} index", node.brush_index, brush_index)
        _expect_equal(mismatches, f"Brush node {brush_index} name", node.property_value("Name"), name)
    for node, class_name in zip(root.children[1:], ("WorldProperties", "StartPoint", "Light")):
        _expect_equal(mismatches, f"{class_name} node class", node.class_name, class_name)
    if scaffold is None:
        mismatches.append("full-world scaffold metadata is missing")
        return
    for node, wanted in (
        (root.children[1], scaffold.world_properties_position),
        (root.children[2], scaffold.start_point_position),
        (root.children[3], scaffold.light_position),
    ):
        if not _vec3_close(wanted, node.property_value("Pos")):
            mismatches.append(f"{node.class_name} position changed during round-trip")
    _expect_equal(mismatches, "world infostring", recovered_infostring, scaffold.infostring)


def _default_full_world_positions(
    brushes: Sequence[legacy_ed_writer.LegacyEdBrush],
) -> Tuple[Vec3, Vec3, Vec3]:
    points = tuple(point for brush in brushes for point in brush.points)
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    min_z = min(point[2] for point in points)
    max_z = max(point[2] for point in points)
    center_x = (min_x + max_x) * 0.5
    center_z = (min_z + max_z) * 0.5
    y_span = max_y - min_y
    return (
        (center_x, max_y + 512.0, center_z),
        (center_x, max_y + max(64.0, y_span * 0.25), center_z),
        (center_x, max_y + max(256.0, y_span), center_z),
    )


def _empty_assembly(
    *,
    status: str,
    output_mode: str,
    diagnostic: EdAssemblyDiagnostic,
) -> GltfEdAssembly:
    return GltfEdAssembly(
        status=status,
        output_mode=output_mode,
        ed_bytes=b"",
        wrapper_kind="",
        group_name="",
        brush_names=(),
        brush_count=0,
        surface_count=0,
        point_count=0,
        node_count=0,
        object_count=0,
        name_map=(),
        node_assignments=(),
        scaffold=None,
        wrapper_block_count=0,
        wrapper_block_size=0,
        validation=EdRoundTripValidation("not_run", "not_run"),
        diagnostics=(diagnostic,),
    )


def _failed_assembly(
    *,
    status: str,
    output_mode: str,
    group_name: str,
    brush_names: Tuple[str, ...],
    name_map: Tuple[EdNameMapping, ...],
    diagnostic: EdAssemblyDiagnostic,
    scaffold: Optional[FullWorldScaffold] = None,
    node_assignments: Tuple[EdNodeAssignment, ...] = (),
) -> GltfEdAssembly:
    return GltfEdAssembly(
        status=status,
        output_mode=output_mode,
        ed_bytes=b"",
        wrapper_kind=(
            UNCOMPRESSED_NAMED_GROUP if output_mode == PREFAB else ZLIB_BLOCKED_FULL_LEVEL
        ),
        group_name=group_name,
        brush_names=brush_names,
        brush_count=0,
        surface_count=0,
        point_count=0,
        node_count=0,
        object_count=0,
        name_map=name_map,
        node_assignments=node_assignments,
        scaffold=scaffold,
        wrapper_block_count=0,
        wrapper_block_size=0,
        validation=EdRoundTripValidation("failed", "not_run"),
        diagnostics=(diagnostic,),
    )


def _output_mode(value: object) -> str:
    mode = str(value)
    if mode not in OUTPUT_MODES:
        raise ValueError(f"output_mode must be one of {', '.join(OUTPUT_MODES)}")
    return mode


def _unique_legacy_names(values: Sequence[str], *, fallback: str) -> Tuple[str, ...]:
    result: List[str] = []
    used = set()
    for value in values:
        base = _safe_legacy_name(value, fallback=fallback)
        candidate = base
        suffix_number = 2
        while candidate.casefold() in used:
            suffix = f"_{suffix_number}"
            candidate = base[:max(1, 96 - len(suffix))] + suffix
            suffix_number += 1
        used.add(candidate.casefold())
        result.append(candidate)
    return tuple(result)


def _safe_legacy_name(value: object, *, fallback: str, limit: int = 96) -> str:
    result: List[str] = []
    previous_underscore = False
    for char in str(value):
        safe = char if ord(char) <= 255 and (char.isalnum() or char in "-_.") else "_"
        if safe == "_" and previous_underscore:
            continue
        result.append(safe)
        previous_underscore = safe == "_"
    return ("".join(result).strip("_.") or fallback)[:limit]


def _latin1_text(value: object) -> str:
    return str(value).encode("latin1", errors="replace").decode("latin1")


def _finite_vec3(name: str, value: Sequence[float]) -> Vec3:
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain three finite numbers") from exc
    if len(result) != 3 or not all(math.isfinite(item) for item in result):
        raise ValueError(f"{name} must contain three finite numbers")
    return result[0], result[1], result[2]


def _vec3_close(expected: object, actual: object, tolerance: float = 1.0e-5) -> bool:
    try:
        wanted = tuple(float(item) for item in expected)  # type: ignore[union-attr]
        found = tuple(float(item) for item in actual)  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return False
    return (
        len(wanted) == 3
        and len(found) == 3
        and all(
            math.isclose(left, right, rel_tol=1.0e-6, abs_tol=tolerance)
            for left, right in zip(wanted, found)
        )
    )


def _node_count(root: Optional[legacy_ed.LegacyEdNode]) -> int:
    if root is None:
        return 0
    return 1 + sum(_node_count(child) for child in root.children)


def _expect_equal(
    mismatches: List[str],
    label: str,
    actual: object,
    expected: object,
) -> None:
    if actual != expected:
        mismatches.append(f"{label} is {actual!r}; expected {expected!r}")
