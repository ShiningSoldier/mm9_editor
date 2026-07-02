"""Legacy Terrain* BSP patch diagnostics and reference helpers."""

from __future__ import annotations

import copy
import math
import os
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core import bsp
from features.dat_editing import (
    bsp_record_inspector,
    output_validation,
    terrain_reconstruction,
    terrain_semantics,
)
from features.doors import bsp_writer


DEFAULT_TERRAIN_MODEL = terrain_semantics.DEFAULT_TERRAIN_MODEL
DEFAULT_MOVE_EPSILON = 0.01
DEFAULT_COLLISION_FLOOR_THRESHOLD = 16.0
DEFAULT_WALKABLE_VERTICAL_LIMIT = 64.0
DEFAULT_SYNCED_WALKABLE_VERTICAL_LIMIT = 128.0
DEFAULT_COLLISION_SYNC_RADIUS = 384.0
DEFAULT_COLLISION_SYNC_VERTICAL_BAND = 128.0
DEFAULT_COLLISION_LOWER_FALLBACK_SEARCH_DEPTH = 512.0
DEFAULT_COLLISION_LOWER_FALLBACK_MIN_GAP = 1.0
DEFAULT_COLLISION_REGENERATION_MAX_POLYGONS = 4
DEFAULT_COLLISION_REGENERATION_MAX_VERTICAL_DELTA = 128.0
DEFAULT_TERRAIN_PLANE_DISTANCE_LIMIT = 4.0
Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class VertexEditedModel:
    name: str
    source_model: bsp.WorldModelMesh
    edited_model: bsp.WorldModelMesh


@dataclass(frozen=True)
class VertexEditPlan:
    source_path: str
    metadata_path: str
    models: List[VertexEditedModel] = field(default_factory=list)


def patch_model_record(
    raw_record: bytes,
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
) -> bytes:
    """Patch topology-preserving BSP point/derived data into a raw model record."""
    validate_topology(source_model, edited_model)
    raw = bytearray(raw_record)
    (
        _name_length_pos,
        min_box_offset,
        max_box_offset,
        translation_offset,
        plane_offsets,
        _surface_offsets,
        polygon_offsets,
        point_offsets,
    ) = bsp_writer._world_bsp_patch_offsets(raw_record, source_model)

    struct.pack_into("<3f", raw, min_box_offset, *edited_model.min_box)
    struct.pack_into("<3f", raw, max_box_offset, *edited_model.max_box)
    struct.pack_into("<3f", raw, translation_offset, *edited_model.translation)

    planes = [plane_for_polygon(edited_model.points, polygon) for polygon in edited_model.polygons]
    point_normals = point_normals_for_polygons(len(edited_model.points), edited_model.polygons, planes)
    for plane_offset, (normal, distance) in zip(plane_offsets, planes):
        struct.pack_into("<3f", raw, plane_offset, *normal)
        struct.pack_into("<f", raw, plane_offset + 12, float(distance))

    for (center_offset, _surface_index_offset, _plane_index_offset), polygon in zip(
        polygon_offsets,
        edited_model.polygons,
    ):
        center = polygon_center(edited_model.points, polygon)
        struct.pack_into("<3f", raw, center_offset, *center)

    for (point_offset, normal_offset), point, normal in zip(
        point_offsets,
        edited_model.points,
        point_normals,
    ):
        struct.pack_into("<3f", raw, point_offset, *point)
        struct.pack_into("<3f", raw, normal_offset, *normal)
    return bytes(raw)


def validate_topology(source_model: bsp.WorldModelMesh, edited_model: bsp.WorldModelMesh) -> None:
    if len(source_model.points) != len(edited_model.points):
        raise ValueError(f"BSP model {source_model.name!r} point count changed")
    if len(source_model.polygons) != len(edited_model.polygons):
        raise ValueError(f"BSP model {source_model.name!r} polygon count changed")
    for index, (source_polygon, edited_polygon) in enumerate(zip(source_model.polygons, edited_model.polygons)):
        if list(source_polygon.vertex_indices) != list(edited_polygon.vertex_indices):
            raise ValueError(f"BSP model {source_model.name!r} polygon {index} vertex list changed")


def plane_for_polygon(points: Sequence[Vec3], polygon: bsp.Polygon) -> Tuple[Vec3, float]:
    verts = [points[index] for index in polygon.vertex_indices]
    normal = polygon_normal(verts)
    distance = vec3_dot(normal, verts[0])
    return normal, distance


def polygon_normal(vertices: Sequence[Vec3]) -> Vec3:
    nx = ny = nz = 0.0
    for i, current in enumerate(vertices):
        nxt = vertices[(i + 1) % len(vertices)]
        nx += (current[1] - nxt[1]) * (current[2] + nxt[2])
        ny += (current[2] - nxt[2]) * (current[0] + nxt[0])
        nz += (current[0] - nxt[0]) * (current[1] + nxt[1])
    return unit_vector((nx, ny, nz))


def point_normals_for_polygons(
    point_count: int,
    polygons: Sequence[bsp.Polygon],
    planes: Sequence[Tuple[Vec3, float]],
) -> List[Vec3]:
    accum = [[0.0, 0.0, 0.0] for _ in range(point_count)]
    for polygon, (normal, _distance) in zip(polygons, planes):
        for index in polygon.vertex_indices:
            accum[index][0] += normal[0]
            accum[index][1] += normal[1]
            accum[index][2] += normal[2]
    return [unit_vector((value[0], value[1], value[2])) for value in accum]


def polygon_center(points: Sequence[Vec3], polygon: bsp.Polygon) -> Vec3:
    verts = [points[index] for index in polygon.vertex_indices]
    count = float(len(verts))
    return (
        sum(point[0] for point in verts) / count,
        sum(point[1] for point in verts) / count,
        sum(point[2] for point in verts) / count,
    )


def vec3_dot(a: Vec3, b: Vec3) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def unit_vector(value: Vec3) -> Vec3:
    length = math.sqrt(vec3_dot(value, value))
    if length <= 1.0e-6:
        return (0.0, 1.0, 0.0)
    return (float(value[0]) / length, float(value[1]) / length, float(value[2]) / length)


@dataclass(frozen=True)
class TerrainFreshLoadResult:
    object_count: int
    model_names: List[str]
    validation_warnings: List[str]


@dataclass(frozen=True)
class TerrainVertexAudit:
    model_name: str
    moved_vertex_count: int
    total_vertex_count: int
    likely_walkable_moved_vertex_count: int
    max_delta: float
    max_horizontal_delta: float
    max_vertical_delta: float
    physics_bsp_present: bool = False
    collision_sample_count: int = 0
    collision_missing_sample_count: int = 0
    collision_max_floor_delta: float = 0.0
    collision_vertices_over_threshold: int = 0


@dataclass(frozen=True)
class TerrainCollisionSyncAudit:
    model_name: str
    moved_vertex_count: int
    total_vertex_count: int
    affected_polygon_count: int
    affected_floor_polygon_count: int
    max_delta: float
    max_horizontal_delta: float
    max_vertical_delta: float


@dataclass(frozen=True)
class TerrainCollisionCoverageAudit:
    model_name: str
    physics_bsp_present: bool
    moved_walkable_polygon_count: int
    matched_physics_polygon_count: int
    matched_physics_surface_count: int
    matched_physics_surface_indices: List[int]
    matched_physics_texture_names: List[str]
    matched_physics_surface_flags: List[int]
    matched_physics_texture_flags: List[int]
    lower_fallback_physics_polygon_count: int
    lower_fallback_physics_surface_count: int
    lower_fallback_physics_surface_indices: List[int]
    lower_fallback_physics_texture_names: List[str]
    lower_fallback_physics_surface_flags: List[int]
    lower_fallback_physics_texture_flags: List[int]
    max_lower_fallback_depth: float
    close_match_count: int
    distant_match_count: int
    unmatched_walkable_polygon_count: int
    affected_physics_node_count: int
    affected_physics_block_cell_count: int
    max_source_floor_delta: float
    max_edited_floor_delta: float
    examples: List[str]


@dataclass(frozen=True)
class TerrainCollisionHelperSemanticsAudit:
    model_name: str
    physics_bsp_present: bool
    matched_floor_polygon_count: int
    lower_support_floor_polygon_count: int
    shared_neighbor_polygon_count: int
    non_floor_helper_polygon_count: int
    shared_floor_polygon_count: int
    preserved_attached_helper_polygon_count: int
    blocking_external_helper_polygon_count: int
    role_counts: Dict[str, int]
    surface_indices: List[int]
    texture_names: List[str]
    surface_flags: List[int]
    surface_flag_names: List[str]
    texture_flags: List[int]
    examples: List[str]


@dataclass(frozen=True)
class TerrainPlanarityAudit:
    model_name: str
    affected_polygon_count: int
    polygons_over_limit: int
    max_plane_distance: float
    plane_distance_limit: float


@dataclass(frozen=True)
class TerrainRenderClassificationAudit:
    model_name: str
    checked_node_table_count: int
    checked_node_count: int
    checked_polygon_reference_count: int
    changed_center_reference_count: int
    changed_vertex_reference_count: int
    source_ambiguous_reference_count: int
    max_center_distance_delta: float
    max_vertex_distance_delta: float
    examples: List[str]


@dataclass(frozen=True)
class TerrainRenderSplitSpanAudit:
    model_name: str
    checked_node_table_count: int
    checked_node_count: int
    checked_polygon_reference_count: int
    spanning_reference_count: int
    spanning_polygon_count: int
    touching_reference_count: int
    touching_polygon_count: int
    repeated_node_polygon_count: int
    duplicate_node_reference_count: int
    repeated_spanning_polygon_count: int
    repeated_touching_polygon_count: int
    examples: List[str]


@dataclass(frozen=True)
class TerrainDerivedDataRiskAudit:
    model_name: str
    moved_vertex_count: int
    affected_polygon_count: int
    off_plane_polygon_count: int
    render_classification_available: bool
    render_classification_clean: bool
    render_topology_rebuild_required: bool
    render_topology_rebuild_supported: bool
    render_changed_center_reference_count: int
    render_changed_vertex_reference_count: int
    visibility_culling_risk: str
    lighting_risk: str
    world_tree_risk: str
    collision_risk: str
    untouched_systems: List[str]
    blockers: List[str]
    cautions: List[str]
    examples: List[str]


@dataclass(frozen=True)
class TerrainWorldTreeAudit:
    model_name: str
    world_tree_present: bool
    world_tree_valid_node_count: bool
    world_tree_declared_node_count: int
    world_tree_decoded_node_count: int
    world_tree_internal_node_count: int
    world_tree_leaf_node_count: int
    world_tree_max_depth: int
    world_tree_dummy_terrain_depth: int
    moved_vertex_count: int
    moved_vertex_outside_tree_count: int
    moved_vertex_outside_world_count: int
    source_bounds_outside_tree: bool
    edited_bounds_outside_tree: bool
    source_bounds_outside_world: bool
    edited_bounds_outside_world: bool
    max_tree_overshoot: float
    max_world_overshoot: float
    examples: List[str]


@dataclass(frozen=True)
class TerrainVisibilityLightAudit:
    model_name: str
    edit_class: str
    moved_vertex_count: int
    affected_polygon_count: int
    off_plane_polygon_count: int
    max_vertical_delta: float
    render_classification_available: bool
    render_classification_clean: bool
    render_topology_rebuild_required: bool
    terrain_leaf_count: int
    terrain_node_count: int
    terrain_user_portal_count: int
    terrain_total_vis: int
    terrain_leaf_portal_reference_count: int
    terrain_lightmapped_polygon_count: int
    terrain_lightmap_extra_data_polygon_count: int
    affected_lightmapped_polygon_count: int
    affected_lightmap_extra_data_polygon_count: int
    affected_lightmap_pixel_count: int
    max_affected_lightmap_width: int
    max_affected_lightmap_height: int
    vis_bsp_present: bool
    active_vis_bsp: bool
    vis_bsp_leaf_count: int
    vis_bsp_node_count: int
    vis_bsp_user_portal_count: int
    vis_bsp_total_vis: int
    vis_bsp_leaf_portal_reference_count: int
    active_portals: bool
    vis_bsp_spatial_audit_available: bool
    moved_vertex_vis_sample_count: int
    changed_vis_partition_vertex_count: int
    ambiguous_vis_partition_vertex_count: int
    max_vis_partition_distance_delta: float
    lightmap_grid_size: float
    render_data_size: int
    light_grid_preservation: str
    portal_preservation: str
    render_data_preservation: str
    visibility_culling_risk: str
    lighting_risk: str
    portal_risk: str
    required_unbuilt_systems: List[str]
    blockers: List[str]
    cautions: List[str]
    examples: List[str]


terrain_model_names = terrain_semantics.terrain_model_names


def validate_terrain_vertex_edit_plan(plan: VertexEditPlan) -> None:
    if not plan.models:
        raise ValueError("terrain vertex edit plan has no edited BSP models")
    non_terrain = [
        item.name
        for item in plan.models
        if not (_is_terrain_model(item.source_model) or _is_physics_bsp_model(item.source_model))
    ]
    if non_terrain:
        names = ", ".join(non_terrain)
        raise ValueError(
            "terrain vertex edit import only accepts Terrain* BSP models; "
            f"non-terrain model(s): {names}"
        )


def validate_terrain_vertex_edit_safety(
    plans: Sequence[VertexEditPlan],
    *,
    source_bsp: Optional[bsp.BspWorld] = None,
    source_dat: Optional[bytes] = None,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
    collision_floor_threshold: float = DEFAULT_COLLISION_FLOOR_THRESHOLD,
    walkable_vertical_limit: Optional[float] = None,
    allow_nonplanar_with_clean_render_classification: bool = False,
) -> None:
    errors = terrain_safety_errors(
        plans,
        source_bsp=source_bsp,
        source_dat=source_dat,
        move_epsilon=move_epsilon,
        collision_floor_threshold=collision_floor_threshold,
        walkable_vertical_limit=walkable_vertical_limit,
        allow_nonplanar_with_clean_render_classification=allow_nonplanar_with_clean_render_classification,
    )
    if errors:
        sync_enabled = _plan_has_physics_bsp_edit(plans)
        note = (
            "This workflow patches visible Terrain* and guarded existing PhysicsBSP floor polygons, "
            "but it still cannot split collision polygons, rebuild visibility, or rebuild world-tree partitioning."
            if sync_enabled
            else (
                "This workflow only patches the visible compiled Terrain* BSP record. "
                "Large walkable-height changes need a PhysicsBSP/visibility rebuild path, "
                "which is not implemented yet."
            )
        )
        raise ValueError(
            "Unsafe Terrain* vertex edit; import was blocked.\n\n"
            + "\n".join(f"- {error}" for error in errors)
            + "\n\n"
            + note
        )


def terrain_safety_errors(
    plans: Sequence[VertexEditPlan],
    *,
    source_bsp: Optional[bsp.BspWorld] = None,
    source_dat: Optional[bytes] = None,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
    collision_floor_threshold: float = DEFAULT_COLLISION_FLOOR_THRESHOLD,
    walkable_vertical_limit: Optional[float] = None,
    allow_nonplanar_with_clean_render_classification: bool = False,
) -> List[str]:
    errors: List[str] = []
    sync_enabled = _plan_has_physics_bsp_edit(plans)
    limit = (
        float(walkable_vertical_limit)
        if walkable_vertical_limit is not None
        else (
            DEFAULT_SYNCED_WALKABLE_VERTICAL_LIMIT
            if sync_enabled
            else DEFAULT_WALKABLE_VERTICAL_LIMIT
        )
    )
    for audit in audit_terrain_vertex_edits(
        plans,
        source_bsp=source_bsp,
        move_epsilon=move_epsilon,
        collision_threshold=collision_floor_threshold,
    ):
        if (
            audit.likely_walkable_moved_vertex_count > 0
            and audit.max_vertical_delta > limit
        ):
            errors.append(
                f"{audit.model_name}: moved likely-walkable terrain vertically by "
                f"{audit.max_vertical_delta:.2f} units "
                f"(limit {limit:.2f})"
            )
        if audit.collision_sample_count > 0 and audit.collision_vertices_over_threshold > 0:
            errors.append(
                f"{audit.model_name}: {audit.collision_vertices_over_threshold}/"
                f"{audit.collision_sample_count} sampled moved walkable vertices differ "
                f"from PhysicsBSP floor by more than {float(collision_floor_threshold):.2f} units"
            )
        if audit.collision_sample_count > 0 and audit.collision_missing_sample_count > 0 and audit.max_vertical_delta > limit:
            errors.append(
                f"{audit.model_name}: {audit.collision_missing_sample_count}/"
                f"{audit.collision_sample_count} sampled moved walkable vertices no longer "
                "raycast to a PhysicsBSP floor"
            )
    for audit in audit_terrain_world_tree_risks(
        source_bsp,
        plans,
        move_epsilon=move_epsilon,
    ):
        if not audit.world_tree_present:
            errors.append(
                f"{audit.model_name}: decoded world-tree layout is not available; "
                "compiled world-tree bounds cannot be verified"
            )
            continue
        if not audit.world_tree_valid_node_count:
            errors.append(
                f"{audit.model_name}: decoded world-tree layout is not clean "
                f"({audit.world_tree_decoded_node_count}/"
                f"{audit.world_tree_declared_node_count} node(s)); "
                "compiled world-tree bounds cannot be verified"
            )
            continue
        if audit.moved_vertex_outside_tree_count > 0:
            errors.append(
                f"{audit.model_name}: {audit.moved_vertex_outside_tree_count} moved "
                "terrain vertex/vertices are outside compiled world-tree bounds "
                f"(max overshoot {audit.max_tree_overshoot:.2f})"
            )
        if audit.moved_vertex_outside_world_count > 0:
            errors.append(
                f"{audit.model_name}: {audit.moved_vertex_outside_world_count} moved "
                "terrain vertex/vertices are outside compiled world extents "
                f"(max overshoot {audit.max_world_overshoot:.2f})"
            )
        if audit.edited_bounds_outside_tree and not audit.source_bounds_outside_tree:
            errors.append(
                f"{audit.model_name}: edited terrain bounds extend outside compiled "
                f"world-tree bounds (max overshoot {audit.max_tree_overshoot:.2f})"
            )
        if audit.edited_bounds_outside_world and not audit.source_bounds_outside_world:
            errors.append(
                f"{audit.model_name}: edited terrain bounds extend outside compiled "
                f"world extents (max overshoot {audit.max_world_overshoot:.2f})"
            )
    for audit in audit_terrain_visibility_light_risks(
        source_dat,
        source_bsp,
        plans,
        move_epsilon=move_epsilon,
    ):
        for blocker in audit.blockers:
            errors.append(f"{audit.model_name}: {blocker}")
    classification_by_name: Dict[str, TerrainRenderClassificationAudit] = {}
    if allow_nonplanar_with_clean_render_classification:
        classification_by_name = {
            audit.model_name: audit
            for audit in audit_terrain_render_classifications(source_dat, source_bsp, plans)
        }
    else:
        for audit in audit_terrain_derived_data_risks(source_dat, source_bsp, plans):
            if not audit.render_topology_rebuild_required:
                continue
            if audit.render_topology_rebuild_supported:
                continue
            detail = audit.blockers[0] if audit.blockers else "render topology rebuild is not supported"
            errors.append(
                f"{audit.model_name}: decoded render BSP classification changed "
                f"(centers={audit.render_changed_center_reference_count}, "
                f"vertices={audit.render_changed_vertex_reference_count}) and {detail}; "
                "make a smaller edit"
            )
    for audit in audit_terrain_planarity(
        plans,
        move_epsilon=move_epsilon,
        plane_distance_limit=DEFAULT_TERRAIN_PLANE_DISTANCE_LIMIT,
    ):
        if audit.polygons_over_limit > 0:
            classification = classification_by_name.get(audit.model_name)
            if classification is not None and _terrain_render_classification_is_clean(classification):
                continue
            if (
                allow_nonplanar_with_clean_render_classification
                and classification is not None
                and not _plan_item_render_topology_rebuild_errors(
                    source_dat,
                    source_bsp,
                    audit.model_name,
                    plans,
                )
            ):
                continue
            if allow_nonplanar_with_clean_render_classification and classification is not None:
                if classification.changed_vertex_reference_count > 0:
                    rebuild_errors = _plan_item_render_topology_rebuild_errors(
                        source_dat,
                        source_bsp,
                        audit.model_name,
                        plans,
                    )
                    detail = (
                        "; ".join(rebuild_errors[:2])
                        if rebuild_errors
                        else (
                            "polygon vertices crossed decoded render split planes "
                            f"({classification.changed_vertex_reference_count} vertex reference change(s))"
                        )
                    )
                    errors.append(
                        f"{audit.model_name}: experimental render topology rebuild is blocked because "
                        f"{detail}; the fixed-size repeated-polygon placement rebuild "
                        "cannot fit this edit"
                    )
                    continue
                errors.append(
                    f"{audit.model_name}: experimental render section bounds patch is blocked because "
                    f"the decoded render BSP classification changed "
                    f"(centers={classification.changed_center_reference_count}, "
                    f"vertices={classification.changed_vertex_reference_count}); "
                    "this edit needs a render node/list rebuild that could not be fitted "
                    "into the decoded Terrain0 chunk ranges"
                )
                continue
            errors.append(
                f"{audit.model_name}: {audit.polygons_over_limit}/"
                f"{audit.affected_polygon_count} affected terrain polygon(s) moved away "
                f"from their compiled plane by more than {audit.plane_distance_limit:.2f} units "
                f"(max {audit.max_plane_distance:.2f})"
            )
    return errors


def terrain_validation_warnings(
    plans: Sequence[VertexEditPlan],
    *,
    source_bsp: Optional[bsp.BspWorld] = None,
    source_dat: Optional[bytes] = None,
) -> List[str]:
    if not plans:
        return []
    names = sorted({item.name for plan in plans for item in plan.models})
    model_text = ", ".join(names) if names else "Terrain*"
    sync_enabled = _plan_has_physics_bsp_edit(plans)
    warnings = [
        (
            "Terrain vertex edits patch visible terrain BSP model(s) in place "
            f"({model_text}); source mesh faces are ignored and original DAT polygon "
            "lists/topology are preserved"
        ),
    ]
    if sync_enabled:
        warnings.append(
            "Terrain collision regeneration rewrites guarded existing PhysicsBSP "
            "floor polygons experimentally; VisBSP, render lighting, portals, "
            "and world-tree data are still not rebuilt"
        )
    else:
        warnings.append(
            "Terrain vertex edits do not rebuild PhysicsBSP, VisBSP, render "
            "lighting, or portals; keep edits small and validate from a fresh "
            "load of the patched WORLDS.REZ"
        )
    warnings.extend(terrain_audit_warnings(plans, source_bsp=source_bsp))
    warnings.extend(terrain_collision_coverage_warnings(source_dat, source_bsp, plans))
    warnings.extend(terrain_collision_helper_semantics_warnings(source_dat, source_bsp, plans))
    warnings.extend(terrain_collision_sync_warnings(plans))
    warnings.extend(terrain_planarity_warnings(plans))
    warnings.extend(terrain_render_classification_warnings(source_dat, source_bsp, plans))
    warnings.extend(terrain_derived_data_risk_warnings(source_dat, source_bsp, plans))
    warnings.extend(terrain_world_tree_warnings(source_bsp, plans))
    warnings.extend(terrain_visibility_light_warnings(source_dat, source_bsp, plans))
    return warnings


def audit_terrain_planarity(
    plans: Sequence[VertexEditPlan],
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
    plane_distance_limit: float = DEFAULT_TERRAIN_PLANE_DISTANCE_LIMIT,
) -> List[TerrainPlanarityAudit]:
    audits: List[TerrainPlanarityAudit] = []
    for plan in plans or []:
        for item in plan.models:
            if not _is_terrain_model(item.source_model):
                continue
            affected = 0
            over_limit = 0
            max_distance = 0.0
            for polygon in item.source_model.polygons:
                if not any(
                    _distance(item.source_model.points[index], item.edited_model.points[index]) > float(move_epsilon)
                    for index in polygon.vertex_indices
                    if 0 <= index < len(item.source_model.points) and 0 <= index < len(item.edited_model.points)
                ):
                    continue
                affected += 1
                try:
                    source_vertices = [item.source_model.points[index] for index in polygon.vertex_indices]
                    edited_vertices = [item.edited_model.points[index] for index in polygon.vertex_indices]
                except IndexError:
                    continue
                if len(source_vertices) < 3:
                    continue
                normal = _polygon_normal(source_vertices)
                distance = _dot(normal, source_vertices[0])
                polygon_max = max(abs(_dot(normal, point) - distance) for point in edited_vertices)
                max_distance = max(max_distance, polygon_max)
                if polygon_max > float(plane_distance_limit):
                    over_limit += 1
            audits.append(TerrainPlanarityAudit(
                model_name=item.name,
                affected_polygon_count=affected,
                polygons_over_limit=over_limit,
                max_plane_distance=max_distance,
                plane_distance_limit=float(plane_distance_limit),
            ))
    return audits


def terrain_planarity_warnings(
    plans: Sequence[VertexEditPlan],
) -> List[str]:
    warnings: List[str] = []
    for audit in audit_terrain_planarity(plans):
        if audit.affected_polygon_count <= 0:
            continue
        warnings.append(
            "Terrain planarity audit: "
            f"{audit.model_name} affected {audit.affected_polygon_count} polygon(s); "
            f"over {audit.plane_distance_limit:.2f} units="
            f"{audit.polygons_over_limit}, max plane distance={audit.max_plane_distance:.2f}"
        )
    return warnings


def audit_terrain_render_classifications(
    source_dat: Optional[bytes],
    source_bsp: Optional[bsp.BspWorld],
    plans: Sequence[VertexEditPlan],
    *,
    classification_epsilon: float = 0.5,
) -> List[TerrainRenderClassificationAudit]:
    if source_dat is None or source_bsp is None:
        return []
    audits: List[TerrainRenderClassificationAudit] = []
    for plan in plans or []:
        for item in plan.models:
            if not _is_terrain_model(item.source_model):
                continue
            source_model = source_bsp.model_by_name(item.name) or item.source_model
            raw = source_bsp.raw_model_bytes(source_dat, source_model)
            if raw is None:
                continue
            edited_model = _snapped_edited_model(
                source_model,
                item.edited_model,
                move_epsilon=DEFAULT_MOVE_EPSILON,
            )
            audits.append(audit_terrain_render_classification(
                raw,
                source_model,
                edited_model,
                classification_epsilon=classification_epsilon,
            ))
    return audits


def terrain_render_classification_warnings(
    source_dat: Optional[bytes],
    source_bsp: Optional[bsp.BspWorld],
    plans: Sequence[VertexEditPlan],
) -> List[str]:
    warnings: List[str] = []
    for audit in audit_terrain_render_classifications(source_dat, source_bsp, plans):
        prefix = (
            "Terrain render classification warning:"
            if audit.changed_center_reference_count > 0 or audit.changed_vertex_reference_count > 0
            else "Terrain render classification audit:"
        )
        text = (
            f"{prefix} {audit.model_name} checked "
            f"{audit.checked_node_table_count} table(s), "
            f"{audit.checked_node_count} node(s), "
            f"{audit.checked_polygon_reference_count} polygon reference(s); "
            f"changed centers={audit.changed_center_reference_count}, "
            f"changed vertices={audit.changed_vertex_reference_count}, "
            f"ambiguous source refs={audit.source_ambiguous_reference_count}, "
            f"max center delta={audit.max_center_distance_delta:.2f}, "
            f"max vertex delta={audit.max_vertex_distance_delta:.2f}"
        )
        if audit.examples:
            text += f"; example: {audit.examples[0]}"
        warnings.append(text)
    return warnings


def audit_terrain_derived_data_risks(
    source_dat: Optional[bytes],
    source_bsp: Optional[bsp.BspWorld],
    plans: Sequence[VertexEditPlan],
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
) -> List[TerrainDerivedDataRiskAudit]:
    """Summarize untouched derived-data systems implicated by terrain edits."""
    audits: List[TerrainDerivedDataRiskAudit] = []
    if source_bsp is None:
        return audits
    for plan in plans or []:
        plan_has_physics_edit = any(
            _is_physics_bsp_model(item.source_model)
            for item in plan.models
        )
        for item in plan.models:
            if not _is_terrain_model(item.source_model):
                continue
            source_model = source_bsp.model_by_name(item.name) or item.source_model
            edited_model = _snapped_edited_model(
                source_model,
                item.edited_model,
                move_epsilon=float(move_epsilon),
            )
            single_plan = VertexEditPlan(
                source_path=plan.source_path,
                metadata_path=plan.metadata_path,
                models=[
                    VertexEditedModel(
                        name=item.name,
                        source_model=source_model,
                        edited_model=edited_model,
                    )
                ],
            )
            vertex_audit = next(
                iter(audit_terrain_vertex_edits(
                    [single_plan],
                    source_bsp=source_bsp,
                    move_epsilon=float(move_epsilon),
                )),
                None,
            )
            planarity = next(
                iter(audit_terrain_planarity(
                    [single_plan],
                    move_epsilon=float(move_epsilon),
                    plane_distance_limit=DEFAULT_TERRAIN_PLANE_DISTANCE_LIMIT,
                )),
                None,
            )
            coverage = next(
                iter(audit_terrain_collision_coverage(
                    source_dat,
                    source_bsp,
                    [single_plan],
                    move_epsilon=float(move_epsilon),
                )),
                None,
            )

            raw = source_bsp.raw_model_bytes(source_dat, source_model) if source_dat is not None else None
            render_audit: Optional[TerrainRenderClassificationAudit] = None
            topology_errors: List[str] = []
            if raw is not None:
                render_audit = audit_terrain_render_classification(
                    raw,
                    source_model,
                    edited_model,
                )
                if not _terrain_render_classification_is_clean(render_audit):
                    topology_errors = _terrain_render_topology_rebuild_errors(
                        raw,
                        source_model,
                        edited_model,
                    )

            moved_vertex_count = int(vertex_audit.moved_vertex_count) if vertex_audit else 0
            affected_polygon_count = int(planarity.affected_polygon_count) if planarity else 0
            off_plane_polygon_count = int(planarity.polygons_over_limit) if planarity else 0
            render_available = render_audit is not None
            render_clean = bool(render_audit is not None and _terrain_render_classification_is_clean(render_audit))
            render_required = bool(
                render_audit is not None
                and (
                    int(render_audit.changed_center_reference_count) > 0
                    or int(render_audit.changed_vertex_reference_count) > 0
                )
            )
            render_supported = bool(render_required and not topology_errors)

            blockers: List[str] = []
            cautions: List[str] = []
            examples: List[str] = []
            if render_audit is None:
                cautions.append("decoded Terrain0 render classification audit was not available")
            elif render_required:
                if render_supported:
                    cautions.append(
                        "decoded render classification changed; this edit needs the "
                        "experimental fixed-chunk render topology writer"
                    )
                else:
                    detail = topology_errors[0] if topology_errors else "render topology rebuild is not available"
                    blockers.append(f"render topology rebuild blocked: {detail}")
                examples.extend(render_audit.examples[:2])
            else:
                cautions.append("decoded render classification stayed clean")

            if off_plane_polygon_count > 0:
                cautions.append(
                    "edited terrain moved off original polygon planes; lightmaps, "
                    "light grid, VisBSP, portals, and world-tree data are not rebuilt"
                )
            elif moved_vertex_count > 0:
                cautions.append(
                    "terrain points changed in place; lightmaps, light grid, VisBSP, "
                    "portals, and world-tree data are not rebuilt"
                )

            collision_risk = "none"
            if coverage is None:
                collision_risk = "unknown"
            elif coverage.moved_walkable_polygon_count <= 0:
                collision_risk = "low"
            elif plan_has_physics_edit:
                collision_risk = "guarded_physicsbsp_regeneration"
                cautions.append(
                    "PhysicsBSP points were regenerated, but its node table and "
                    "physics block table are still preserved in place"
                )
            elif coverage.close_match_count == 0 and coverage.unmatched_walkable_polygon_count > 0:
                collision_risk = "native_terrain_or_unmatched"
                cautions.append(
                    "moved walkable Terrain* polygons have no close PhysicsBSP floor; "
                    "collision may be native Terrain/world-BSP or otherwise undecoded"
                )
                examples.extend(coverage.examples[:2])
            elif coverage.max_edited_floor_delta > DEFAULT_COLLISION_FLOOR_THRESHOLD:
                collision_risk = "visible_collision_diverged"
                cautions.append(
                    "edited visible terrain diverges from unchanged PhysicsBSP floor"
                )
                examples.extend(coverage.examples[:2])
            else:
                collision_risk = "low"

            max_horizontal = float(vertex_audit.max_horizontal_delta) if vertex_audit else 0.0
            max_vertical = float(vertex_audit.max_vertical_delta) if vertex_audit else 0.0
            visibility_risk = (
                "blocked"
                if blockers
                else (
                    "render_topology_rebuild_required"
                    if render_required
                    else ("unknown" if not render_available else "low")
                )
            )
            lighting_risk = "medium" if off_plane_polygon_count > 0 or max_vertical > 0.0 else "low"
            world_tree_risk = (
                "medium"
                if render_required or off_plane_polygon_count > 0 or max_horizontal > 0.0
                else "low"
            )
            untouched = [
                "VisBSP",
                "portals",
                "lightmaps",
                "light_grid",
                "world_tree",
            ]
            if plan_has_physics_edit:
                untouched.extend(["PhysicsBSP_nodes", "physics_block_table"])
            else:
                untouched.append("PhysicsBSP")

            audits.append(TerrainDerivedDataRiskAudit(
                model_name=item.name,
                moved_vertex_count=int(moved_vertex_count),
                affected_polygon_count=int(affected_polygon_count),
                off_plane_polygon_count=int(off_plane_polygon_count),
                render_classification_available=bool(render_available),
                render_classification_clean=bool(render_clean),
                render_topology_rebuild_required=bool(render_required),
                render_topology_rebuild_supported=bool(render_supported),
                render_changed_center_reference_count=(
                    int(render_audit.changed_center_reference_count) if render_audit else 0
                ),
                render_changed_vertex_reference_count=(
                    int(render_audit.changed_vertex_reference_count) if render_audit else 0
                ),
                visibility_culling_risk=visibility_risk,
                lighting_risk=lighting_risk,
                world_tree_risk=world_tree_risk,
                collision_risk=collision_risk,
                untouched_systems=untouched,
                blockers=blockers,
                cautions=cautions,
                examples=_unique_text(examples)[:4],
            ))
    return audits


def terrain_derived_data_risk_warnings(
    source_dat: Optional[bytes],
    source_bsp: Optional[bsp.BspWorld],
    plans: Sequence[VertexEditPlan],
) -> List[str]:
    warnings: List[str] = []
    for audit in audit_terrain_derived_data_risks(source_dat, source_bsp, plans):
        if audit.moved_vertex_count <= 0:
            continue
        prefix = (
            "Terrain derived-data risk warning:"
            if audit.blockers or audit.visibility_culling_risk != "low" or audit.collision_risk not in {"low", "none"}
            else "Terrain derived-data risk audit:"
        )
        text = (
            f"{prefix} {audit.model_name} moved {audit.moved_vertex_count} vertex/vertices, "
            f"affected polygons={audit.affected_polygon_count}, "
            f"off-plane polygons={audit.off_plane_polygon_count}; "
            f"render clean={audit.render_classification_clean}, "
            f"render rebuild required={audit.render_topology_rebuild_required}, "
            f"render rebuild supported={audit.render_topology_rebuild_supported}, "
            f"visibility/culling risk={audit.visibility_culling_risk}, "
            f"collision risk={audit.collision_risk}, "
            f"untouched={_short_list_text(audit.untouched_systems, limit=8)}"
        )
        if audit.blockers:
            text += f"; blocker: {audit.blockers[0]}"
        elif audit.cautions:
            text += f"; note: {audit.cautions[0]}"
        if audit.examples:
            text += f"; example: {audit.examples[0]}"
        warnings.append(text)
    return warnings


def audit_terrain_world_tree_risks(
    source_bsp: Optional[bsp.BspWorld],
    plans: Sequence[VertexEditPlan],
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
    bounds_epsilon: float = 0.5,
) -> List[TerrainWorldTreeAudit]:
    audits: List[TerrainWorldTreeAudit] = []
    if source_bsp is None:
        return audits
    tree = getattr(source_bsp, "world_tree", None)
    world_min = getattr(source_bsp, "world_extents_min", None)
    world_max = getattr(source_bsp, "world_extents_max", None)
    tree_min = getattr(tree, "min_box", None) if tree is not None else None
    tree_max = getattr(tree, "max_box", None) if tree is not None else None

    for plan in plans or []:
        for item in plan.models:
            if not _is_terrain_model(item.source_model):
                continue
            source_model = source_bsp.model_by_name(item.name) or item.source_model
            edited_model = _snapped_edited_model(
                source_model,
                item.edited_model,
                move_epsilon=float(move_epsilon),
            )
            moved_count = 0
            outside_tree = 0
            outside_world = 0
            max_tree_overshoot = 0.0
            max_world_overshoot = 0.0
            examples: List[str] = []
            for index, (source_point, edited_point) in enumerate(zip(source_model.points, edited_model.points)):
                if _distance(source_point, edited_point) <= float(move_epsilon):
                    continue
                moved_count += 1
                tree_overshoot = (
                    _point_box_overshoot(edited_point, tree_min, tree_max, epsilon=float(bounds_epsilon))
                    if tree_min is not None and tree_max is not None
                    else 0.0
                )
                world_overshoot = (
                    _point_box_overshoot(edited_point, world_min, world_max, epsilon=float(bounds_epsilon))
                    if world_min is not None and world_max is not None
                    else 0.0
                )
                if tree_overshoot > 0.0:
                    outside_tree += 1
                    max_tree_overshoot = max(max_tree_overshoot, tree_overshoot)
                    if len(examples) < 4:
                        examples.append(
                            f"vertex {index} edited outside world-tree bounds by {tree_overshoot:.2f}"
                        )
                if world_overshoot > 0.0:
                    outside_world += 1
                    max_world_overshoot = max(max_world_overshoot, world_overshoot)
                    if len(examples) < 4:
                        examples.append(
                            f"vertex {index} edited outside world extents by {world_overshoot:.2f}"
                        )

            source_bounds_outside_tree = (
                _bounds_box_overshoot(source_model.min_box, source_model.max_box, tree_min, tree_max, epsilon=float(bounds_epsilon)) > 0.0
                if tree_min is not None and tree_max is not None
                else False
            )
            edited_bounds_outside_tree = (
                _bounds_box_overshoot(edited_model.min_box, edited_model.max_box, tree_min, tree_max, epsilon=float(bounds_epsilon)) > 0.0
                if tree_min is not None and tree_max is not None
                else False
            )
            source_bounds_outside_world = (
                _bounds_box_overshoot(source_model.min_box, source_model.max_box, world_min, world_max, epsilon=float(bounds_epsilon)) > 0.0
                if world_min is not None and world_max is not None
                else False
            )
            edited_bounds_outside_world = (
                _bounds_box_overshoot(edited_model.min_box, edited_model.max_box, world_min, world_max, epsilon=float(bounds_epsilon)) > 0.0
                if world_min is not None and world_max is not None
                else False
            )
            max_tree_overshoot = max(
                max_tree_overshoot,
                (
                    _bounds_box_overshoot(edited_model.min_box, edited_model.max_box, tree_min, tree_max, epsilon=float(bounds_epsilon))
                    if tree_min is not None and tree_max is not None
                    else 0.0
                ),
            )
            max_world_overshoot = max(
                max_world_overshoot,
                (
                    _bounds_box_overshoot(edited_model.min_box, edited_model.max_box, world_min, world_max, epsilon=float(bounds_epsilon))
                    if world_min is not None and world_max is not None
                    else 0.0
                ),
            )
            audits.append(TerrainWorldTreeAudit(
                model_name=item.name,
                world_tree_present=tree is not None,
                world_tree_valid_node_count=bool(getattr(tree, "valid_node_count", False)) if tree is not None else False,
                world_tree_declared_node_count=int(getattr(tree, "declared_node_count", 0) or 0),
                world_tree_decoded_node_count=int(getattr(tree, "decoded_node_count", 0) or 0),
                world_tree_internal_node_count=int(getattr(tree, "internal_node_count", 0) or 0),
                world_tree_leaf_node_count=int(getattr(tree, "leaf_node_count", 0) or 0),
                world_tree_max_depth=int(getattr(tree, "max_depth", 0) or 0),
                world_tree_dummy_terrain_depth=int(getattr(tree, "dummy_terrain_depth", 0) or 0),
                moved_vertex_count=int(moved_count),
                moved_vertex_outside_tree_count=int(outside_tree),
                moved_vertex_outside_world_count=int(outside_world),
                source_bounds_outside_tree=bool(source_bounds_outside_tree),
                edited_bounds_outside_tree=bool(edited_bounds_outside_tree),
                source_bounds_outside_world=bool(source_bounds_outside_world),
                edited_bounds_outside_world=bool(edited_bounds_outside_world),
                max_tree_overshoot=float(max_tree_overshoot),
                max_world_overshoot=float(max_world_overshoot),
                examples=examples,
            ))
    return audits


def terrain_world_tree_warnings(
    source_bsp: Optional[bsp.BspWorld],
    plans: Sequence[VertexEditPlan],
) -> List[str]:
    warnings: List[str] = []
    for audit in audit_terrain_world_tree_risks(source_bsp, plans):
        if audit.moved_vertex_count <= 0:
            continue
        prefix = (
            "Terrain world-tree warning:"
            if (
                not audit.world_tree_present
                or not audit.world_tree_valid_node_count
                or audit.moved_vertex_outside_tree_count > 0
                or audit.moved_vertex_outside_world_count > 0
                or audit.edited_bounds_outside_tree
                or audit.edited_bounds_outside_world
            )
            else "Terrain world-tree audit:"
        )
        text = (
            f"{prefix} {audit.model_name} tree nodes="
            f"{audit.world_tree_decoded_node_count}/{audit.world_tree_declared_node_count}, "
            f"valid={audit.world_tree_valid_node_count}, "
            f"depth={audit.world_tree_max_depth}, "
            f"leaves={audit.world_tree_leaf_node_count}, "
            f"moved vertices outside tree={audit.moved_vertex_outside_tree_count}, "
            f"outside world={audit.moved_vertex_outside_world_count}, "
            f"edited bounds outside tree={audit.edited_bounds_outside_tree}, "
            f"outside world={audit.edited_bounds_outside_world}"
        )
        if audit.examples:
            text += f"; example: {audit.examples[0]}"
        warnings.append(text)
    return warnings


def audit_terrain_visibility_light_risks(
    source_dat: Optional[bytes],
    source_bsp: Optional[bsp.BspWorld],
    plans: Sequence[VertexEditPlan],
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
) -> List[TerrainVisibilityLightAudit]:
    """Report preserved visibility, portal, render-tail, and light-grid state."""
    audits: List[TerrainVisibilityLightAudit] = []
    if source_bsp is None:
        return audits

    terrain_names = sorted({
        str(item.name)
        for plan in plans or []
        for item in plan.models
        if _is_terrain_model(item.source_model)
    })
    inspections: Dict[str, bsp_record_inspector.BspRecordInspection] = {}
    if source_dat is not None and terrain_names:
        try:
            inspections = bsp_record_inspector.inspect_dat(
                source_dat,
                model_names=terrain_names + ["VisBSP"],
            )
        except Exception:
            inspections = {}
    vis_info = inspections.get("VisBSP")
    vis_model = source_bsp.model_by_name("VisBSP")
    vis_present = bool((vis_info.present if vis_info is not None else False) or vis_model is not None)
    active_vis = _visibility_inspection_active(vis_info)
    render_data_size = (
        max(0, len(source_dat) - int(getattr(source_bsp, "ren_pos", 0) or 0))
        if source_dat is not None
        else 0
    )
    lightmap_grid_size = float(getattr(source_bsp, "lightmap_grid_size", 0.0) or 0.0)

    for plan in plans or []:
        for item in plan.models:
            if not _is_terrain_model(item.source_model):
                continue
            source_model = source_bsp.model_by_name(item.name) or item.source_model
            edited_model = _snapped_edited_model(
                source_model,
                item.edited_model,
                move_epsilon=float(move_epsilon),
            )
            single_plan = VertexEditPlan(
                source_path=plan.source_path,
                metadata_path=plan.metadata_path,
                models=[
                    VertexEditedModel(
                        name=item.name,
                        source_model=source_model,
                        edited_model=edited_model,
                    )
                ],
            )
            vertex_audit = next(
                iter(audit_terrain_vertex_edits(
                    [single_plan],
                    source_bsp=source_bsp,
                    move_epsilon=float(move_epsilon),
                )),
                None,
            )
            planarity = next(
                iter(audit_terrain_planarity(
                    [single_plan],
                    move_epsilon=float(move_epsilon),
                    plane_distance_limit=DEFAULT_TERRAIN_PLANE_DISTANCE_LIMIT,
                )),
                None,
            )

            raw = source_bsp.raw_model_bytes(source_dat, source_model) if source_dat is not None else None
            render_audit: Optional[TerrainRenderClassificationAudit] = None
            topology_errors: List[str] = []
            if raw is not None:
                render_audit = audit_terrain_render_classification(
                    raw,
                    source_model,
                    edited_model,
                )
                if not _terrain_render_classification_is_clean(render_audit):
                    topology_errors = _terrain_render_topology_rebuild_errors(
                        raw,
                        source_model,
                        edited_model,
                    )

            terrain_info = inspections.get(str(item.name))
            terrain_visibility_active = _visibility_inspection_active(terrain_info)
            moved_vertex_count = int(vertex_audit.moved_vertex_count) if vertex_audit else 0
            affected_polygon_count = int(planarity.affected_polygon_count) if planarity else 0
            off_plane_polygon_count = int(planarity.polygons_over_limit) if planarity else 0
            max_vertical_delta = float(vertex_audit.max_vertical_delta) if vertex_audit else 0.0
            render_available = render_audit is not None
            render_clean = bool(render_audit is not None and _terrain_render_classification_is_clean(render_audit))
            render_required = bool(
                render_audit is not None
                and (
                    int(render_audit.changed_center_reference_count) > 0
                    or int(render_audit.changed_vertex_reference_count) > 0
                )
            )

            terrain_portals = int(getattr(terrain_info, "user_portal_count", 0) or 0)
            terrain_leaf_portals = int(getattr(terrain_info, "leaf_portal_reference_count", 0) or 0)
            vis_portals = int(getattr(vis_info, "user_portal_count", 0) or 0)
            vis_leaf_portals = int(getattr(vis_info, "leaf_portal_reference_count", 0) or 0)
            active_portals = bool(terrain_portals or terrain_leaf_portals or vis_portals or vis_leaf_portals)
            lightmap_edit = _audit_terrain_lightmapped_polygon_edits(
                source_dat,
                source_bsp,
                source_model,
                edited_model,
                move_epsilon=float(move_epsilon),
            )
            vis_partition = _audit_vis_bsp_vertex_partitions(
                source_dat,
                source_bsp,
                source_model,
                edited_model,
                move_epsilon=float(move_epsilon),
            )

            blockers: List[str] = []
            cautions: List[str] = []
            examples: List[str] = []
            edit_needs_visibility_rebuild = bool(render_required or off_plane_polygon_count > 0)
            lightmap_rebuild_required = bool(
                lightmap_edit["affected_lightmapped_polygon_count"] > 0
                and (
                    off_plane_polygon_count > 0
                    or max_vertical_delta > float(move_epsilon)
                    or render_required
                )
            )
            render_topology_rebuild_supported = bool(render_required and not topology_errors)
            edit_class = _terrain_derived_data_edit_class(
                moved_vertex_count=moved_vertex_count,
                off_plane_polygon_count=off_plane_polygon_count,
                render_required=render_required,
                affected_lightmapped_polygon_count=int(lightmap_edit["affected_lightmapped_polygon_count"]),
            )
            light_grid_preservation = _light_grid_preservation_policy(
                moved_vertex_count=moved_vertex_count,
                max_vertical_delta=max_vertical_delta,
                off_plane_polygon_count=off_plane_polygon_count,
                lightmap_rebuild_required=lightmap_rebuild_required,
                affected_lightmapped_polygon_count=int(lightmap_edit["affected_lightmapped_polygon_count"]),
                move_epsilon=float(move_epsilon),
            )
            portal_preservation = _portal_preservation_policy(
                moved_vertex_count=moved_vertex_count,
                active_portals=active_portals,
                edit_needs_visibility_rebuild=edit_needs_visibility_rebuild,
                vis_partition_available=bool(vis_partition["available"]),
                changed_vis_partition_count=int(vis_partition["changed_count"]),
            )
            render_data_preservation = _render_data_preservation_policy(
                render_available=render_available,
                render_required=render_required,
                render_topology_rebuild_supported=render_topology_rebuild_supported,
                render_data_size=render_data_size,
            )
            required_unbuilt_systems = _terrain_required_unbuilt_systems(
                moved_vertex_count=moved_vertex_count,
                active_vis=active_vis,
                active_portals=active_portals,
                terrain_visibility_active=terrain_visibility_active,
                edit_needs_visibility_rebuild=edit_needs_visibility_rebuild,
                vis_partition_available=bool(vis_partition["available"]),
                changed_vis_partition_count=int(vis_partition["changed_count"]),
                lightmap_rebuild_required=lightmap_rebuild_required,
                render_required=render_required,
                render_topology_rebuild_supported=render_topology_rebuild_supported,
            )
            if moved_vertex_count > 0 and edit_needs_visibility_rebuild and active_vis:
                blockers.append(
                    "active VisBSP visibility/culling data is present, but this "
                    "off-plane/render-topology terrain edit would preserve it unchanged"
                )
            if moved_vertex_count > 0 and edit_needs_visibility_rebuild and active_portals:
                blockers.append(
                    "active portal references are present, but this off-plane/render-topology "
                    "terrain edit would preserve portal visibility unchanged"
                )
            if moved_vertex_count > 0 and edit_needs_visibility_rebuild and terrain_visibility_active:
                blockers.append(
                    "Terrain* has ordinary BSP visibility/node data that is not rebuilt "
                    "for this off-plane/render-topology edit"
                )
            if vis_partition["changed_count"] > 0:
                blockers.append(
                    f"{vis_partition['changed_count']} moved terrain vertex/vertices cross "
                    "decoded VisBSP visibility partitions"
                )
            if moved_vertex_count > 0 and lightmap_rebuild_required:
                blockers.append(
                    f"{lightmap_edit['affected_lightmapped_polygon_count']} affected "
                    "Terrain* polygon lightmap(s) would be preserved stale; "
                    "lightmap payloads are not rebuilt"
                )
            if render_required and not render_topology_rebuild_supported:
                detail = topology_errors[0] if topology_errors else "render topology rebuild is not supported"
                blockers.append(
                    "decoded terrain render topology would need an unsupported rebuild: "
                    f"{detail}"
                )
            if moved_vertex_count > 0 and portal_preservation == "requires_portal_visibility_rebuild":
                if not any("portal" in blocker.lower() for blocker in blockers):
                    blockers.append(
                        "portal visibility data would need a rebuild that is not implemented"
                    )
            if required_unbuilt_systems:
                blockers.append(
                    "edit requires derived data the editor cannot rebuild: "
                    f"{_short_list_text(required_unbuilt_systems, limit=6)}"
                )

            if render_audit is None:
                cautions.append("decoded render classification was not available")
            elif render_required:
                cautions.append("decoded render classification changes")
                examples.extend(render_audit.examples[:2])
            else:
                cautions.append("decoded render classification stays clean")
            if active_vis:
                cautions.append(
                    "VisBSP is active and preserved unchanged "
                    f"(leaves={int(getattr(vis_info, 'leaf_count', 0) or 0)}, "
                    f"nodes={int(getattr(vis_info, 'node_count', 0) or 0)}, "
                    f"total_vis={int(getattr(vis_info, 'total_vis', 0) or 0)})"
                )
                if vis_partition["available"]:
                    cautions.append(
                        "moved vertices were classified against the decoded VisBSP tree "
                        f"({vis_partition['sample_count']} sample(s), "
                        f"{vis_partition['changed_count']} changed partition(s))"
                    )
            elif vis_present:
                cautions.append("VisBSP record is present but has no decoded active visibility data")
            else:
                cautions.append("no VisBSP record is present")
            if lightmap_grid_size > 0.0 and (off_plane_polygon_count > 0 or max_vertical_delta > 0.0):
                cautions.append(
                    "lightmap grid/header data is preserved unchanged while terrain height changed"
                )
            if lightmap_edit["terrain_lightmapped_polygon_count"] > 0:
                cautions.append(
                    f"Terrain* has {lightmap_edit['terrain_lightmapped_polygon_count']} "
                    "decoded baked polygon lightmap(s)"
                )
            else:
                cautions.append("Terrain* decoded polygon lightmap dimensions are all zero")
            if lightmap_edit["affected_lightmapped_polygon_count"] > 0:
                cautions.append(
                    f"moved terrain touches {lightmap_edit['affected_lightmapped_polygon_count']} "
                    "baked polygon lightmap(s)"
                )
            examples.extend(vis_partition["examples"])
            examples.extend(lightmap_edit["examples"])

            if blockers:
                visibility_risk = "blocked"
            elif moved_vertex_count > 0 and active_vis and vis_partition["available"] and vis_partition["changed_count"] == 0:
                visibility_risk = "low"
            elif moved_vertex_count > 0 and (active_vis or terrain_visibility_active):
                visibility_risk = "medium"
            elif moved_vertex_count > 0:
                visibility_risk = "low"
            else:
                visibility_risk = "none"
            if lightmap_rebuild_required:
                lighting_risk = "blocked"
            elif moved_vertex_count > 0 and lightmap_edit["affected_lightmapped_polygon_count"] > 0:
                lighting_risk = "medium"
            elif moved_vertex_count > 0 and (off_plane_polygon_count > 0 or max_vertical_delta > 0.0):
                lighting_risk = "light_grid_preserved"
            else:
                lighting_risk = "low" if moved_vertex_count > 0 else "none"
            portal_risk = (
                "blocked"
                if active_portals and blockers
                else ("medium" if active_portals and moved_vertex_count > 0 else "none")
            )

            audits.append(TerrainVisibilityLightAudit(
                model_name=str(item.name),
                edit_class=str(edit_class),
                moved_vertex_count=int(moved_vertex_count),
                affected_polygon_count=int(affected_polygon_count),
                off_plane_polygon_count=int(off_plane_polygon_count),
                max_vertical_delta=float(max_vertical_delta),
                render_classification_available=bool(render_available),
                render_classification_clean=bool(render_clean),
                render_topology_rebuild_required=bool(render_required),
                terrain_leaf_count=int(getattr(terrain_info, "leaf_count", 0) or 0),
                terrain_node_count=int(getattr(terrain_info, "node_count", 0) or 0),
                terrain_user_portal_count=terrain_portals,
                terrain_total_vis=int(getattr(terrain_info, "total_vis", 0) or 0),
                terrain_leaf_portal_reference_count=terrain_leaf_portals,
                terrain_lightmapped_polygon_count=int(lightmap_edit["terrain_lightmapped_polygon_count"]),
                terrain_lightmap_extra_data_polygon_count=int(lightmap_edit["terrain_lightmap_extra_data_polygon_count"]),
                affected_lightmapped_polygon_count=int(lightmap_edit["affected_lightmapped_polygon_count"]),
                affected_lightmap_extra_data_polygon_count=int(lightmap_edit["affected_lightmap_extra_data_polygon_count"]),
                affected_lightmap_pixel_count=int(lightmap_edit["affected_lightmap_pixel_count"]),
                max_affected_lightmap_width=int(lightmap_edit["max_affected_lightmap_width"]),
                max_affected_lightmap_height=int(lightmap_edit["max_affected_lightmap_height"]),
                vis_bsp_present=bool(vis_present),
                active_vis_bsp=bool(active_vis),
                vis_bsp_leaf_count=int(getattr(vis_info, "leaf_count", 0) or 0),
                vis_bsp_node_count=int(getattr(vis_info, "node_count", 0) or 0),
                vis_bsp_user_portal_count=vis_portals,
                vis_bsp_total_vis=int(getattr(vis_info, "total_vis", 0) or 0),
                vis_bsp_leaf_portal_reference_count=vis_leaf_portals,
                active_portals=bool(active_portals),
                vis_bsp_spatial_audit_available=bool(vis_partition["available"]),
                moved_vertex_vis_sample_count=int(vis_partition["sample_count"]),
                changed_vis_partition_vertex_count=int(vis_partition["changed_count"]),
                ambiguous_vis_partition_vertex_count=int(vis_partition["ambiguous_count"]),
                max_vis_partition_distance_delta=float(vis_partition["max_distance_delta"]),
                lightmap_grid_size=float(lightmap_grid_size),
                render_data_size=int(render_data_size),
                light_grid_preservation=str(light_grid_preservation),
                portal_preservation=str(portal_preservation),
                render_data_preservation=str(render_data_preservation),
                visibility_culling_risk=visibility_risk,
                lighting_risk=lighting_risk,
                portal_risk=portal_risk,
                required_unbuilt_systems=list(required_unbuilt_systems),
                blockers=_unique_text(blockers),
                cautions=_unique_text(cautions)[:5],
                examples=_unique_text(examples)[:4],
            ))
    return audits


def terrain_visibility_light_warnings(
    source_dat: Optional[bytes],
    source_bsp: Optional[bsp.BspWorld],
    plans: Sequence[VertexEditPlan],
) -> List[str]:
    warnings: List[str] = []
    for audit in audit_terrain_visibility_light_risks(source_dat, source_bsp, plans):
        if audit.moved_vertex_count <= 0:
            continue
        prefix = (
            "Terrain visibility/light warning:"
            if (
                audit.blockers
                or audit.visibility_culling_risk not in {"low", "none"}
                or audit.lighting_risk not in {"low", "none"}
                or audit.portal_risk not in {"none"}
            )
            else "Terrain visibility/light audit:"
        )
        text = (
            f"{prefix} {audit.model_name} moved {audit.moved_vertex_count} vertex/vertices, "
            f"edit class={audit.edit_class}, "
            f"off-plane polygons={audit.off_plane_polygon_count}, "
            f"render rebuild required={audit.render_topology_rebuild_required}, "
            f"VisBSP active={audit.active_vis_bsp} "
            f"(leaves={audit.vis_bsp_leaf_count}, nodes={audit.vis_bsp_node_count}, "
            f"total_vis={audit.vis_bsp_total_vis}), "
            f"VisBSP spatial audit={audit.vis_bsp_spatial_audit_available} "
            f"(samples={audit.moved_vertex_vis_sample_count}, "
            f"partition changes={audit.changed_vis_partition_vertex_count}, "
            f"ambiguous={audit.ambiguous_vis_partition_vertex_count}), "
            f"polygon lightmaps={audit.terrain_lightmapped_polygon_count}, "
            f"affected lightmaps={audit.affected_lightmapped_polygon_count}, "
            f"portals active={audit.active_portals}, "
            f"lightmap grid={audit.lightmap_grid_size:.2f}, "
            f"render data bytes={audit.render_data_size}, "
            f"light grid={audit.light_grid_preservation}, "
            f"portals={audit.portal_preservation}, "
            f"render data={audit.render_data_preservation}, "
            f"visibility risk={audit.visibility_culling_risk}, "
            f"lighting risk={audit.lighting_risk}, "
            f"portal risk={audit.portal_risk}"
        )
        if audit.blockers:
            text += f"; blocker: {audit.blockers[0]}"
        elif audit.cautions:
            text += f"; note: {audit.cautions[0]}"
        if audit.examples:
            text += f"; example: {audit.examples[0]}"
        warnings.append(text)
    return warnings


def _terrain_derived_data_edit_class(
    *,
    moved_vertex_count: int,
    off_plane_polygon_count: int,
    render_required: bool,
    affected_lightmapped_polygon_count: int,
) -> str:
    if int(moved_vertex_count) <= 0:
        return "no_change"
    if bool(render_required):
        return "render_topology_edit"
    if int(off_plane_polygon_count) > 0:
        return "off_plane_edit"
    if int(affected_lightmapped_polygon_count) > 0:
        return "same_plane_lightmapped_point_edit"
    return "same_plane_point_edit"


def _light_grid_preservation_policy(
    *,
    moved_vertex_count: int,
    max_vertical_delta: float,
    off_plane_polygon_count: int,
    lightmap_rebuild_required: bool,
    affected_lightmapped_polygon_count: int,
    move_epsilon: float,
) -> str:
    if int(moved_vertex_count) <= 0:
        return "unchanged_no_terrain_movement"
    if bool(lightmap_rebuild_required):
        return "requires_polygon_lightmap_rebuild"
    if int(affected_lightmapped_polygon_count) > 0:
        return "unchanged_same_plane_baked_lightmaps_warn"
    if int(off_plane_polygon_count) > 0 or float(max_vertical_delta) > float(move_epsilon):
        return "unchanged_height_change_warn"
    return "unchanged_same_plane"


def _portal_preservation_policy(
    *,
    moved_vertex_count: int,
    active_portals: bool,
    edit_needs_visibility_rebuild: bool,
    vis_partition_available: bool,
    changed_vis_partition_count: int,
) -> str:
    if int(moved_vertex_count) <= 0:
        return "unchanged_no_terrain_movement"
    if not bool(active_portals):
        return "unchanged_no_active_portals"
    if bool(edit_needs_visibility_rebuild) or int(changed_vis_partition_count) > 0:
        return "requires_portal_visibility_rebuild"
    if not bool(vis_partition_available):
        return "unchanged_active_portals_unverified"
    return "unchanged_same_vis_partition"


def _render_data_preservation_policy(
    *,
    render_available: bool,
    render_required: bool,
    render_topology_rebuild_supported: bool,
    render_data_size: int,
) -> str:
    if int(render_data_size) <= 0:
        return "none"
    if not bool(render_available):
        return "unchanged_unverified"
    if not bool(render_required):
        return "unchanged_clean_render_classification"
    if bool(render_topology_rebuild_supported):
        return "unchanged_top_level_render_tail_with_world_model_topology_rebuild"
    return "requires_unsupported_render_rebuild"


def _terrain_required_unbuilt_systems(
    *,
    moved_vertex_count: int,
    active_vis: bool,
    active_portals: bool,
    terrain_visibility_active: bool,
    edit_needs_visibility_rebuild: bool,
    vis_partition_available: bool,
    changed_vis_partition_count: int,
    lightmap_rebuild_required: bool,
    render_required: bool,
    render_topology_rebuild_supported: bool,
) -> List[str]:
    systems: List[str] = []
    if int(moved_vertex_count) <= 0:
        return systems
    if bool(active_vis) and (
        bool(edit_needs_visibility_rebuild)
        or int(changed_vis_partition_count) > 0
        or not bool(vis_partition_available)
    ):
        systems.append("VisBSP")
    if bool(active_portals) and (
        bool(edit_needs_visibility_rebuild)
        or int(changed_vis_partition_count) > 0
        or not bool(vis_partition_available)
    ):
        systems.append("portals")
    if bool(terrain_visibility_active) and bool(edit_needs_visibility_rebuild):
        systems.append("TerrainBSP_visibility_nodes")
    if bool(lightmap_rebuild_required):
        systems.append("polygon_lightmaps")
    if bool(render_required) and not bool(render_topology_rebuild_supported):
        systems.append("terrain_render_topology")
    return _unique_text(systems)


def _visibility_inspection_active(
    inspection: Optional[bsp_record_inspector.BspRecordInspection],
) -> bool:
    if inspection is None or not inspection.present:
        return False
    return any(
        int(getattr(inspection, name, 0) or 0) > 0
        for name in (
            "leaf_count",
            "node_count",
            "user_portal_count",
            "total_vis",
            "leaf_list_count",
            "leaf_portal_reference_count",
            "leaf_poly_reference_count",
            "leaf_list_reference_count",
        )
    )


def _terrain_render_classification_is_clean(audit: TerrainRenderClassificationAudit) -> bool:
    return (
        audit.checked_node_table_count > 0
        and audit.checked_polygon_reference_count > 0
        and audit.changed_center_reference_count == 0
        and audit.changed_vertex_reference_count == 0
    )


def validate_experimental_section_bounds_classification(
    raw_record: bytes,
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
) -> TerrainRenderClassificationAudit:
    """Require a clean decoded render-BSP audit before experimental writes."""
    audit = audit_terrain_render_classification(raw_record, source_model, edited_model)
    if _terrain_render_classification_is_clean(audit):
        return audit
    raise ValueError(_dirty_render_classification_message(audit))


def validate_experimental_section_bounds_plans(
    source_dat: bytes,
    source_bsp: bsp.BspWorld,
    plans: Sequence[VertexEditPlan],
) -> List[TerrainRenderClassificationAudit]:
    audits = audit_terrain_render_classifications(source_dat, source_bsp, plans)
    if not audits:
        raise ValueError(
            "Blocked experimental Terrain* render section bounds patch: no "
            "decoded render BSP classification audit was available. This edit "
            "needs a decoded Terrain0 render tail before it can be written."
        )
    for audit in audits:
        if not _terrain_render_classification_is_clean(audit):
            item = _terrain_plan_item_by_name(plans, audit.model_name)
            source_model = source_bsp.model_by_name(audit.model_name)
            raw = source_bsp.raw_model_bytes(source_dat, source_model) if source_model is not None else None
            if item is None or source_model is None or raw is None:
                raise ValueError(_dirty_render_classification_message(audit))
            edited_model = _snapped_edited_model(
                source_model,
                item.edited_model,
                move_epsilon=DEFAULT_MOVE_EPSILON,
            )
            try:
                validate_experimental_render_topology_rebuild(
                    raw,
                    source_model,
                    edited_model,
                )
            except ValueError as exc:
                raise ValueError(
                    _dirty_render_classification_message(audit, rebuild_error=str(exc))
                ) from exc
    return audits


def _dirty_render_classification_message(
    audit: TerrainRenderClassificationAudit,
    *,
    rebuild_error: str = "",
) -> str:
    if audit.changed_vertex_reference_count > 0:
        message = (
            "Blocked experimental Terrain* render topology rebuild: edited "
            "polygon vertices crossed decoded render split planes. "
            f"{audit.model_name} checked {audit.checked_node_table_count} table(s), "
            f"{audit.checked_node_count} node(s), "
            f"{audit.checked_polygon_reference_count} polygon reference(s); "
            f"changed centers={audit.changed_center_reference_count}, "
            f"changed vertices={audit.changed_vertex_reference_count}. "
            "The fixed-size repeated-polygon placement rebuild could not fit "
            "the edited split spans."
        )
        if rebuild_error:
            message += " Rebuild detail: " + rebuild_error
        if audit.examples:
            message += " Example: " + audit.examples[0]
        return message
    message = (
        "Blocked experimental Terrain* render section bounds patch: decoded "
        "render BSP classification audit is not clean. "
        f"{audit.model_name} checked {audit.checked_node_table_count} table(s), "
        f"{audit.checked_node_count} node(s), "
        f"{audit.checked_polygon_reference_count} polygon reference(s); "
        f"changed centers={audit.changed_center_reference_count}, "
        f"changed vertices={audit.changed_vertex_reference_count}. "
        "This edit needs a render node/list rebuild before it can be written."
    )
    if audit.examples:
        message += " Example: " + audit.examples[0]
    return message


def _terrain_plan_item_by_name(
    plans: Sequence[VertexEditPlan],
    model_name: str,
) -> Optional[VertexEditedModel]:
    key = str(model_name or "").lower()
    for plan in plans or []:
        for item in plan.models:
            if str(item.name or "").lower() == key and _is_terrain_model(item.source_model):
                return item
    return None


def _plan_item_supports_render_topology_rebuild(
    source_dat: Optional[bytes],
    source_bsp: Optional[bsp.BspWorld],
    model_name: str,
    plans: Sequence[VertexEditPlan],
) -> bool:
    return not _plan_item_render_topology_rebuild_errors(
        source_dat,
        source_bsp,
        model_name,
        plans,
    )


def _plan_item_render_topology_rebuild_errors(
    source_dat: Optional[bytes],
    source_bsp: Optional[bsp.BspWorld],
    model_name: str,
    plans: Sequence[VertexEditPlan],
) -> List[str]:
    if source_dat is None or source_bsp is None:
        return ["decoded Terrain0 render tail is not available"]
    source_model = source_bsp.model_by_name(model_name)
    item = _terrain_plan_item_by_name(plans, model_name)
    raw = source_bsp.raw_model_bytes(source_dat, source_model) if source_model is not None else None
    if item is None or source_model is None or raw is None:
        return ["decoded Terrain0 render tail is not available"]
    edited_model = _snapped_edited_model(
        source_model,
        item.edited_model,
        move_epsilon=DEFAULT_MOVE_EPSILON,
    )
    return _terrain_render_topology_rebuild_errors(raw, source_model, edited_model)


def validate_experimental_render_topology_rebuild(
    raw_record: bytes,
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
) -> None:
    errors = _terrain_render_topology_rebuild_errors(raw_record, source_model, edited_model)
    if errors:
        raise ValueError(
            "Blocked experimental Terrain* render topology rebuild: "
            + "; ".join(errors)
        )


def audit_terrain_collision_syncs(
    plans: Sequence[VertexEditPlan],
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
) -> List[TerrainCollisionSyncAudit]:
    audits: List[TerrainCollisionSyncAudit] = []
    for plan in plans or []:
        for item in plan.models:
            if not _is_physics_bsp_model(item.source_model):
                continue
            moved_indices: set[int] = set()
            max_delta = 0.0
            max_horizontal = 0.0
            max_vertical = 0.0
            for index, (old, new) in enumerate(zip(item.source_model.points, item.edited_model.points)):
                dx = float(new[0]) - float(old[0])
                dy = float(new[1]) - float(old[1])
                dz = float(new[2]) - float(old[2])
                delta = math.sqrt(dx * dx + dy * dy + dz * dz)
                if delta <= float(move_epsilon):
                    continue
                moved_indices.add(index)
                max_delta = max(max_delta, delta)
                max_horizontal = max(max_horizontal, math.sqrt(dx * dx + dz * dz))
                max_vertical = max(max_vertical, abs(dy))
            affected_polygons = 0
            affected_floor_polygons = 0
            points = list(item.source_model.points)
            for polygon in item.source_model.polygons:
                if not any(index in moved_indices for index in polygon.vertex_indices):
                    continue
                affected_polygons += 1
                try:
                    vertices = [points[index] for index in polygon.vertex_indices]
                except IndexError:
                    continue
                if len(vertices) >= 3 and _polygon_normal(vertices)[1] > 0.35:
                    affected_floor_polygons += 1
            audits.append(TerrainCollisionSyncAudit(
                model_name=item.name,
                moved_vertex_count=len(moved_indices),
                total_vertex_count=len(item.source_model.points),
                affected_polygon_count=affected_polygons,
                affected_floor_polygon_count=affected_floor_polygons,
                max_delta=max_delta,
                max_horizontal_delta=max_horizontal,
                max_vertical_delta=max_vertical,
            ))
    return audits


def terrain_collision_sync_warnings(
    plans: Sequence[VertexEditPlan],
) -> List[str]:
    warnings: List[str] = []
    for audit in audit_terrain_collision_syncs(plans):
        if audit.moved_vertex_count <= 0:
            warnings.append(f"Terrain collision regeneration: {audit.model_name} has no moved vertices")
            continue
        warnings.append(
            "Terrain collision regeneration: "
            f"{audit.model_name} moved {audit.moved_vertex_count}/{audit.total_vertex_count} vertices "
            f"across {audit.affected_floor_polygon_count} floor polygon(s) "
            f"({audit.affected_polygon_count} polygon(s) total); "
            f"max delta={audit.max_delta:.2f}, "
            f"horizontal={audit.max_horizontal_delta:.2f}, "
            f"vertical={audit.max_vertical_delta:.2f}"
        )
    return warnings


def build_terrain_collision_regeneration_edit(
    source_bsp: bsp.BspWorld,
    source_dat: Optional[bytes],
    terrain_plan: VertexEditPlan,
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
    close_floor_delta: float = 4.0,
    vertical_band: float = DEFAULT_COLLISION_SYNC_VERTICAL_BAND,
    max_physics_polygons: int = DEFAULT_COLLISION_REGENERATION_MAX_POLYGONS,
    max_vertical_delta: float = DEFAULT_COLLISION_REGENERATION_MAX_VERTICAL_DELTA,
) -> Optional[VertexEditedModel]:
    """Build a guarded in-place PhysicsBSP edit from moved Terrain* floors.

    This first regeneration path only rewrites existing isolated PhysicsBSP
    floor polygons by projecting their current X/Z vertices onto the edited
    terrain.  It does not split collision polygons or rebuild the block table,
    so it blocks shared moving vertices and dirty BSP node classifications.
    """
    physics_model = source_bsp.model_by_name("PhysicsBSP")
    if physics_model is None:
        raise ValueError("terrain collision regeneration requires a PhysicsBSP model")

    terrain_items = [
        item
        for item in terrain_plan.models
        if _is_terrain_model(item.source_model)
    ]
    if not terrain_items:
        return None

    source_terrain_models: List[bsp.WorldModelMesh] = []
    edited_terrain_by_name: Dict[str, bsp.WorldModelMesh] = {}
    for model in getattr(source_bsp, "world_models", []) or []:
        if not _is_terrain_model(model):
            continue
        source_terrain_models.append(model)
    for item in terrain_items:
        source_model = source_bsp.model_by_name(item.name) or item.source_model
        edited_terrain_by_name[str(item.name).lower()] = _snapped_edited_model(
            source_model,
            item.edited_model,
            move_epsilon=float(move_epsilon),
        )

    edited_terrain_models = [
        edited_terrain_by_name.get(str(model.name).lower(), model)
        for model in source_terrain_models
    ]
    source_terrain_world = _bsp_world_for_models(source_bsp, source_terrain_models)
    edited_terrain_world = _bsp_world_for_models(source_bsp, edited_terrain_models)

    matched_physics_polygons: set[int] = set()
    errors: List[str] = []
    for item in terrain_items:
        source_model = source_bsp.model_by_name(item.name) or item.source_model
        edited_model = edited_terrain_by_name[str(item.name).lower()]
        for terrain_polygon_index in _moved_walkable_polygon_indices(
            source_model,
            edited_model,
            move_epsilon=float(move_epsilon),
        ):
            sample_pairs = _terrain_polygon_source_edited_sample_pairs(
                source_model,
                edited_model,
                terrain_polygon_index,
            )
            match = _best_physics_floor_polygon_match(
                physics_model,
                sample_pairs,
                vertical_band=float(vertical_band),
            )
            if match is None:
                continue
            physics_polygon_index, source_delta, _edited_delta, _source_point, _edited_point, _floor_y = match
            if float(source_delta) > float(close_floor_delta):
                continue
            matched_physics_polygons.add(int(physics_polygon_index))

    if errors:
        raise ValueError("Blocked Terrain collision regeneration: " + "; ".join(errors[:6]))
    if not matched_physics_polygons:
        return None
    if len(matched_physics_polygons) > int(max_physics_polygons):
        raise ValueError(
            "Blocked Terrain collision regeneration: "
            f"{len(matched_physics_polygons)} PhysicsBSP polygons would need regeneration "
            f"(limit {int(max_physics_polygons)})"
        )

    point_refs = _polygon_references_by_point(physics_model)
    point_targets: Dict[int, Vec3] = {}
    for physics_polygon_index in sorted(matched_physics_polygons):
        if not (0 <= int(physics_polygon_index) < len(physics_model.polygons)):
            errors.append(f"PhysicsBSP polygon {physics_polygon_index} is out of range")
            continue
        polygon = physics_model.polygons[int(physics_polygon_index)]
        vertices = [
            physics_model.points[int(index)]
            for index in polygon.vertex_indices
            if 0 <= int(index) < len(physics_model.points)
        ]
        if len(vertices) < 3 or _polygon_normal(vertices)[1] <= 0.35:
            errors.append(f"PhysicsBSP polygon {physics_polygon_index} is not a walkable floor")
            continue
        for point_index in sorted(set(int(index) for index in polygon.vertex_indices)):
            if not (0 <= int(point_index) < len(physics_model.points)):
                errors.append(
                    f"PhysicsBSP polygon {physics_polygon_index} references invalid point {point_index}"
                )
                continue
            point = physics_model.points[int(point_index)]
            source_y = _raycast_floor_y_in_band(
                source_terrain_world,
                float(point[0]),
                float(point[2]),
                y_hint_min=float(point[1]) - float(vertical_band),
                y_hint_max=float(point[1]) + float(vertical_band),
            )
            if source_y is None or abs(float(source_y) - float(point[1])) > float(close_floor_delta):
                errors.append(
                    f"PhysicsBSP polygon {physics_polygon_index} point {point_index} "
                    "does not project back to the source terrain floor"
                )
                continue
            edited_y = _raycast_floor_y_in_band(
                edited_terrain_world,
                float(point[0]),
                float(point[2]),
                y_hint_min=float(source_y) - float(vertical_band),
                y_hint_max=float(source_y) + float(vertical_band),
            )
            if edited_y is None:
                errors.append(
                    f"PhysicsBSP polygon {physics_polygon_index} point {point_index} "
                    "does not project to the edited terrain floor"
                )
                continue
            delta_y = float(edited_y) - float(point[1])
            if abs(delta_y) <= float(move_epsilon):
                continue
            if abs(delta_y) > float(max_vertical_delta):
                errors.append(
                    f"PhysicsBSP polygon {physics_polygon_index} point {point_index} "
                    f"would move vertically by {abs(delta_y):.2f} units "
                    f"(limit {float(max_vertical_delta):.2f})"
                )
                continue
            unsafe_refs = sorted(
                ref
                for ref in point_refs.get(int(point_index), set())
                if int(ref) not in matched_physics_polygons
                and not _physics_shared_helper_is_preservable(
                    physics_model,
                    int(physics_polygon_index),
                    int(ref),
                )
            )
            if unsafe_refs:
                unsafe_summary = _physics_collision_role_summary(physics_model, unsafe_refs)
                errors.append(
                    f"PhysicsBSP polygon {physics_polygon_index} point {point_index} is shared by "
                    f"non-regenerated polygon(s): {_short_list_text(unsafe_refs, limit=4)}"
                    f" ({unsafe_summary})"
                )
                continue
            target = (float(point[0]), float(edited_y), float(point[2]))
            previous = point_targets.get(int(point_index))
            if previous is not None and _distance(previous, target) > float(move_epsilon):
                errors.append(
                    f"PhysicsBSP point {point_index} has conflicting regenerated targets"
                )
                continue
            point_targets[int(point_index)] = target

    if errors:
        raise ValueError("Blocked Terrain collision regeneration: " + "; ".join(errors[:6]))
    if not point_targets:
        return None

    edited_physics = copy.deepcopy(physics_model)
    edited_points = list(physics_model.points)
    for point_index, target in point_targets.items():
        edited_points[int(point_index)] = target
    edited_physics.points = edited_points
    edited_physics.min_box, edited_physics.max_box = _bounds(edited_points)
    edited_physics.raw_start = physics_model.raw_start
    edited_physics.raw_end = physics_model.raw_end
    edited_physics.next_world_item = physics_model.next_world_item
    edited_physics.world_bsp_start = physics_model.world_bsp_start
    edited_physics.world_bsp_end = physics_model.world_bsp_end

    physics_raw = source_bsp.raw_model_bytes(source_dat, physics_model) if source_dat is not None else None
    if physics_raw is not None:
        classification_errors = _physics_bsp_node_classification_errors(
            physics_raw,
            physics_model,
            edited_physics,
        )
        if classification_errors:
            raise ValueError(
                "Blocked Terrain collision regeneration: "
                + "; ".join(classification_errors[:6])
            )

    return VertexEditedModel(
        name=str(physics_model.name),
        source_model=physics_model,
        edited_model=edited_physics,
    )


def audit_terrain_collision_helper_semantics(
    source_dat: Optional[bytes],
    source_bsp: Optional[bsp.BspWorld],
    plans: Sequence[VertexEditPlan],
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
    close_floor_delta: float = 4.0,
    vertical_band: float = DEFAULT_COLLISION_SYNC_VERTICAL_BAND,
    lower_fallback_search_depth: float = DEFAULT_COLLISION_LOWER_FALLBACK_SEARCH_DEPTH,
    lower_fallback_min_gap: float = DEFAULT_COLLISION_LOWER_FALLBACK_MIN_GAP,
    max_examples: int = 8,
) -> List[TerrainCollisionHelperSemanticsAudit]:
    if source_dat is None or source_bsp is None:
        return []
    physics_model = source_bsp.model_by_name("PhysicsBSP")
    if physics_model is None:
        return [
            TerrainCollisionHelperSemanticsAudit(
                model_name=item.name,
                physics_bsp_present=False,
                matched_floor_polygon_count=0,
                lower_support_floor_polygon_count=0,
                shared_neighbor_polygon_count=0,
                non_floor_helper_polygon_count=0,
                shared_floor_polygon_count=0,
                preserved_attached_helper_polygon_count=0,
                blocking_external_helper_polygon_count=0,
                role_counts={},
                surface_indices=[],
                texture_names=[],
                surface_flags=[],
                surface_flag_names=[],
                texture_flags=[],
                examples=[],
            )
            for plan in plans or []
            for item in plan.models
            if _is_terrain_model(item.source_model)
        ]

    point_refs = _polygon_references_by_point(physics_model)
    audits: List[TerrainCollisionHelperSemanticsAudit] = []
    for plan in plans or []:
        for item in plan.models:
            if not _is_terrain_model(item.source_model):
                continue
            source_model = source_bsp.model_by_name(item.name) or item.source_model
            edited_model = _snapped_edited_model(
                source_model,
                item.edited_model,
                move_epsilon=float(move_epsilon),
            )
            matched_floor_polygons: set[int] = set()
            lower_support_polygons: set[int] = set()
            examples: List[str] = []
            for terrain_polygon_index in _moved_walkable_polygon_indices(
                source_model,
                edited_model,
                move_epsilon=float(move_epsilon),
            ):
                sample_pairs = _terrain_polygon_source_edited_sample_pairs(
                    source_model,
                    edited_model,
                    terrain_polygon_index,
                )
                match = _best_physics_floor_polygon_match(
                    physics_model,
                    sample_pairs,
                    vertical_band=float(vertical_band),
                )
                if match is None:
                    continue
                physics_polygon_index, source_delta, _edited_delta, _source_point, _edited_point, _floor_y = match
                if float(source_delta) > float(close_floor_delta):
                    continue
                matched_floor_polygons.add(int(physics_polygon_index))
                lower_support_polygons.update(
                    int(lower_polygon_index)
                    for lower_polygon_index, _lower_y, _depth in _physics_lower_fallback_floor_hits_for_match(
                        physics_model,
                        sample_pairs,
                        matched_polygon_index=int(physics_polygon_index),
                        vertical_band=float(vertical_band),
                        search_depth=float(lower_fallback_search_depth),
                        min_gap=float(lower_fallback_min_gap),
                    )
                )

            shared_neighbors: set[int] = set()
            for physics_polygon_index in matched_floor_polygons:
                if not (0 <= int(physics_polygon_index) < len(physics_model.polygons)):
                    continue
                for point_index in set(int(index) for index in physics_model.polygons[int(physics_polygon_index)].vertex_indices):
                    shared_neighbors.update(point_refs.get(int(point_index), set()))
            shared_neighbors.difference_update(matched_floor_polygons)

            role_counts: Dict[str, int] = {}
            semantic_polygons: set[int] = set(matched_floor_polygons)
            semantic_polygons.update(lower_support_polygons)
            semantic_polygons.update(shared_neighbors)
            non_floor_helper_polygons: set[int] = set()
            shared_floor_polygons: set[int] = set()
            preserved_attached_helpers: set[int] = set()
            blocking_external_helpers: set[int] = set()
            for polygon_index in sorted(semantic_polygons):
                role = _physics_collision_semantic_role(
                    physics_model,
                    int(polygon_index),
                    matched_floor_polygons=matched_floor_polygons,
                    lower_support_polygons=lower_support_polygons,
                )
                role_counts[role] = role_counts.get(role, 0) + 1
                if int(polygon_index) in shared_neighbors:
                    if role in {"matched_floor", "lower_support_floor", "floor"}:
                        shared_floor_polygons.add(int(polygon_index))
                    else:
                        non_floor_helper_polygons.add(int(polygon_index))
                        if any(
                            _physics_shared_helper_is_preservable(
                                physics_model,
                                int(matched_polygon_index),
                                int(polygon_index),
                            )
                            for matched_polygon_index in matched_floor_polygons
                        ):
                            preserved_attached_helpers.add(int(polygon_index))
                        else:
                            blocking_external_helpers.add(int(polygon_index))
                if len(examples) < int(max_examples):
                    texture_name = _physics_polygon_texture_name(physics_model, int(polygon_index))
                    flags = _physics_polygon_surface_flags(physics_model, int(polygon_index))
                    examples.append(
                        f"PhysicsBSP polygon {polygon_index}: role={role}, "
                        f"texture={texture_name or 'none'}, flags={flags}"
                    )

            material_summary = _physics_collision_material_summary(
                physics_model,
                semantic_polygons,
            )
            flag_names = sorted({
                name
                for flags in material_summary["surface_flags"]
                for name in _surface_flag_names(int(flags))
            })
            audits.append(TerrainCollisionHelperSemanticsAudit(
                model_name=item.name,
                physics_bsp_present=True,
                matched_floor_polygon_count=len(matched_floor_polygons),
                lower_support_floor_polygon_count=len(lower_support_polygons),
                shared_neighbor_polygon_count=len(shared_neighbors),
                non_floor_helper_polygon_count=len(non_floor_helper_polygons),
                shared_floor_polygon_count=len(shared_floor_polygons),
                preserved_attached_helper_polygon_count=len(preserved_attached_helpers),
                blocking_external_helper_polygon_count=len(blocking_external_helpers),
                role_counts=dict(sorted(role_counts.items())),
                surface_indices=list(material_summary["surface_indices"]),
                texture_names=list(material_summary["texture_names"]),
                surface_flags=list(material_summary["surface_flags"]),
                surface_flag_names=flag_names,
                texture_flags=list(material_summary["texture_flags"]),
                examples=examples,
            ))
    return audits


def terrain_collision_helper_semantics_warnings(
    source_dat: Optional[bytes],
    source_bsp: Optional[bsp.BspWorld],
    plans: Sequence[VertexEditPlan],
) -> List[str]:
    warnings: List[str] = []
    for audit in audit_terrain_collision_helper_semantics(source_dat, source_bsp, plans):
        if audit.matched_floor_polygon_count <= 0 and audit.shared_neighbor_polygon_count <= 0:
            continue
        if not audit.physics_bsp_present:
            warnings.append(
                f"Terrain collision helper semantics warning: {audit.model_name} could not inspect PhysicsBSP"
            )
            continue
        text = (
            f"Terrain collision helper semantics audit: {audit.model_name} "
            f"matched floors={audit.matched_floor_polygon_count}, "
            f"lower supports={audit.lower_support_floor_polygon_count}, "
            f"shared neighbors={audit.shared_neighbor_polygon_count}, "
            f"non-floor helpers={audit.non_floor_helper_polygon_count}, "
            f"shared floors={audit.shared_floor_polygon_count}, "
            f"preserved attached helpers={audit.preserved_attached_helper_polygon_count}, "
            f"external helper blockers={audit.blocking_external_helper_polygon_count}, "
            f"roles={_format_role_counts(audit.role_counts)}, "
            f"textures={_short_list_text(audit.texture_names)}, "
            f"surface flags={_short_list_text(audit.surface_flag_names)}"
        )
        if audit.examples:
            text += f"; example: {audit.examples[0]}"
        warnings.append(text)
    return warnings


def audit_terrain_collision_coverage(
    source_dat: Optional[bytes],
    source_bsp: Optional[bsp.BspWorld],
    plans: Sequence[VertexEditPlan],
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
    close_floor_delta: float = 4.0,
    vertical_band: float = DEFAULT_COLLISION_SYNC_VERTICAL_BAND,
    lower_fallback_search_depth: float = DEFAULT_COLLISION_LOWER_FALLBACK_SEARCH_DEPTH,
    lower_fallback_min_gap: float = DEFAULT_COLLISION_LOWER_FALLBACK_MIN_GAP,
    max_examples: int = 8,
) -> List[TerrainCollisionCoverageAudit]:
    if source_dat is None or source_bsp is None:
        return []

    physics_model = source_bsp.model_by_name("PhysicsBSP")
    physics_raw = source_bsp.raw_model_bytes(source_dat, physics_model) if physics_model is not None else None
    physics_polygon_nodes: Dict[int, set[int]] = {}
    physics_node_cells: Dict[int, set[int]] = {}
    if physics_model is not None and physics_raw is not None:
        try:
            physics_polygon_nodes = _physics_bsp_polygon_node_map(physics_raw, physics_model)
            physics_node_cells = _physics_bsp_node_block_cell_map(physics_raw, physics_model)
        except Exception:
            physics_polygon_nodes = {}
            physics_node_cells = {}

    audits: List[TerrainCollisionCoverageAudit] = []
    for plan in plans or []:
        for item in plan.models:
            if not _is_terrain_model(item.source_model):
                continue
            source_model = source_bsp.model_by_name(item.name) or item.source_model
            edited_model = _snapped_edited_model(
                source_model,
                item.edited_model,
                move_epsilon=float(move_epsilon),
            )
            moved_polygons = _moved_walkable_polygon_indices(
                source_model,
                edited_model,
                move_epsilon=float(move_epsilon),
            )
            if physics_model is None:
                audits.append(TerrainCollisionCoverageAudit(
                    model_name=item.name,
                    physics_bsp_present=False,
                    moved_walkable_polygon_count=len(moved_polygons),
                    matched_physics_polygon_count=0,
                    matched_physics_surface_count=0,
                    matched_physics_surface_indices=[],
                    matched_physics_texture_names=[],
                    matched_physics_surface_flags=[],
                    matched_physics_texture_flags=[],
                    lower_fallback_physics_polygon_count=0,
                    lower_fallback_physics_surface_count=0,
                    lower_fallback_physics_surface_indices=[],
                    lower_fallback_physics_texture_names=[],
                    lower_fallback_physics_surface_flags=[],
                    lower_fallback_physics_texture_flags=[],
                    max_lower_fallback_depth=0.0,
                    close_match_count=0,
                    distant_match_count=0,
                    unmatched_walkable_polygon_count=len(moved_polygons),
                    affected_physics_node_count=0,
                    affected_physics_block_cell_count=0,
                    max_source_floor_delta=0.0,
                    max_edited_floor_delta=0.0,
                    examples=[],
                ))
                continue

            matched_physics_polygons: set[int] = set()
            lower_fallback_physics_polygons: set[int] = set()
            affected_nodes: set[int] = set()
            affected_cells: set[int] = set()
            close_matches = 0
            distant_matches = 0
            unmatched = 0
            max_source_delta = 0.0
            max_edited_delta = 0.0
            max_lower_fallback_depth = 0.0
            examples: List[str] = []
            for terrain_polygon_index in moved_polygons:
                sample_pairs = _terrain_polygon_source_edited_sample_pairs(
                    source_model,
                    edited_model,
                    terrain_polygon_index,
                )
                match = _best_physics_floor_polygon_match(
                    physics_model,
                    sample_pairs,
                    vertical_band=float(vertical_band),
                )
                if match is None:
                    unmatched += 1
                    if len(examples) < int(max_examples):
                        examples.append(
                            f"{item.name} polygon {terrain_polygon_index}: no PhysicsBSP floor match"
                    )
                    continue
                physics_polygon_index, source_delta, edited_delta, _source_point, _edited_point, _floor_y = match
                matched_physics_polygons.add(int(physics_polygon_index))
                max_source_delta = max(max_source_delta, float(source_delta))
                max_edited_delta = max(max_edited_delta, float(edited_delta))
                if float(source_delta) <= float(close_floor_delta):
                    close_matches += 1
                else:
                    distant_matches += 1
                polygon_nodes = set(physics_polygon_nodes.get(int(physics_polygon_index), set()))
                affected_nodes.update(polygon_nodes)
                for node_index in polygon_nodes:
                    affected_cells.update(physics_node_cells.get(int(node_index), set()))
                lower_fallback_hits = _physics_lower_fallback_floor_hits_for_match(
                    physics_model,
                    sample_pairs,
                    matched_polygon_index=int(physics_polygon_index),
                    vertical_band=float(vertical_band),
                    search_depth=float(lower_fallback_search_depth),
                    min_gap=float(lower_fallback_min_gap),
                )
                for lower_polygon_index, _lower_floor_y, lower_depth in lower_fallback_hits:
                    lower_fallback_physics_polygons.add(int(lower_polygon_index))
                    max_lower_fallback_depth = max(
                        float(max_lower_fallback_depth),
                        float(lower_depth),
                    )
                if len(examples) < int(max_examples):
                    lower_text = ""
                    if lower_fallback_hits:
                        lower_polygon_index, _lower_floor_y, lower_depth = lower_fallback_hits[0]
                        lower_text = (
                            f", lower_fallback={int(lower_polygon_index)} "
                            f"depth={float(lower_depth):.2f}"
                        )
                    examples.append(
                        f"{item.name} polygon {terrain_polygon_index} -> "
                        f"PhysicsBSP polygon {physics_polygon_index}; "
                        f"source_delta={float(source_delta):.2f}, "
                        f"edited_delta={float(edited_delta):.2f}, "
                        f"nodes={len(polygon_nodes)}, "
                        f"cells={len({cell for node in polygon_nodes for cell in physics_node_cells.get(int(node), set())})}"
                        f"{lower_text}"
                    )

            material_summary = _physics_collision_material_summary(
                physics_model,
                matched_physics_polygons,
            )
            lower_material_summary = _physics_collision_material_summary(
                physics_model,
                lower_fallback_physics_polygons,
            )
            audits.append(TerrainCollisionCoverageAudit(
                model_name=item.name,
                physics_bsp_present=True,
                moved_walkable_polygon_count=len(moved_polygons),
                matched_physics_polygon_count=len(matched_physics_polygons),
                matched_physics_surface_count=int(material_summary["surface_count"]),
                matched_physics_surface_indices=list(material_summary["surface_indices"]),
                matched_physics_texture_names=list(material_summary["texture_names"]),
                matched_physics_surface_flags=list(material_summary["surface_flags"]),
                matched_physics_texture_flags=list(material_summary["texture_flags"]),
                lower_fallback_physics_polygon_count=len(lower_fallback_physics_polygons),
                lower_fallback_physics_surface_count=int(lower_material_summary["surface_count"]),
                lower_fallback_physics_surface_indices=list(lower_material_summary["surface_indices"]),
                lower_fallback_physics_texture_names=list(lower_material_summary["texture_names"]),
                lower_fallback_physics_surface_flags=list(lower_material_summary["surface_flags"]),
                lower_fallback_physics_texture_flags=list(lower_material_summary["texture_flags"]),
                max_lower_fallback_depth=float(max_lower_fallback_depth),
                close_match_count=int(close_matches),
                distant_match_count=int(distant_matches),
                unmatched_walkable_polygon_count=int(unmatched),
                affected_physics_node_count=len(affected_nodes),
                affected_physics_block_cell_count=len(affected_cells),
                max_source_floor_delta=float(max_source_delta),
                max_edited_floor_delta=float(max_edited_delta),
                examples=examples,
            ))
    return audits


def terrain_collision_coverage_warnings(
    source_dat: Optional[bytes],
    source_bsp: Optional[bsp.BspWorld],
    plans: Sequence[VertexEditPlan],
) -> List[str]:
    warnings: List[str] = []
    for audit in audit_terrain_collision_coverage(source_dat, source_bsp, plans):
        if audit.moved_walkable_polygon_count <= 0:
            continue
        if not audit.physics_bsp_present:
            warnings.append(
                f"Terrain collision coverage warning: {audit.model_name} moved "
                f"{audit.moved_walkable_polygon_count} walkable polygon(s), but PhysicsBSP is missing"
            )
            continue
        prefix = (
            "Terrain collision coverage warning:"
            if audit.unmatched_walkable_polygon_count > 0 or audit.distant_match_count > 0
            else "Terrain collision coverage audit:"
        )
        text = (
            f"{prefix} {audit.model_name} moved "
            f"{audit.moved_walkable_polygon_count} walkable polygon(s); "
            f"matched PhysicsBSP polygons={audit.matched_physics_polygon_count}, "
            f"close={audit.close_match_count}, distant={audit.distant_match_count}, "
            f"unmatched={audit.unmatched_walkable_polygon_count}, "
            f"affected PhysicsBSP nodes={audit.affected_physics_node_count}, "
            f"block cells={audit.affected_physics_block_cell_count}, "
            f"collision surfaces={audit.matched_physics_surface_count}, "
            f"textures={_short_list_text(audit.matched_physics_texture_names)}, "
            f"lower fallback polygons={audit.lower_fallback_physics_polygon_count}, "
            f"lower fallback surfaces={audit.lower_fallback_physics_surface_count}, "
            f"lower fallback textures={_short_list_text(audit.lower_fallback_physics_texture_names)}, "
            f"max lower fallback depth={audit.max_lower_fallback_depth:.2f}, "
            f"max source floor delta={audit.max_source_floor_delta:.2f}, "
            f"max edited floor delta={audit.max_edited_floor_delta:.2f}"
        )
        if audit.examples:
            text += f"; example: {audit.examples[0]}"
        warnings.append(text)
    return warnings


def audit_terrain_vertex_edits(
    plans: Sequence[VertexEditPlan],
    *,
    source_bsp: Optional[bsp.BspWorld] = None,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
    collision_threshold: float = 16.0,
    collision_sample_limit: int = 512,
) -> List[TerrainVertexAudit]:
    audits: List[TerrainVertexAudit] = []
    physics_world = _physics_bsp_world_for_audit(source_bsp, plans)
    for plan in plans or []:
        for item in plan.models:
            if not _is_terrain_model(item.source_model):
                continue
            walkable_vertices = _likely_walkable_vertex_indices(item.source_model)
            moved = 0
            moved_walkable = 0
            max_delta = 0.0
            max_horizontal = 0.0
            max_vertical = 0.0
            collision_candidates: List[Tuple[Vec3, Vec3]] = []
            for index, (old, new) in enumerate(zip(item.source_model.points, item.edited_model.points)):
                dx = float(new[0]) - float(old[0])
                dy = float(new[1]) - float(old[1])
                dz = float(new[2]) - float(old[2])
                delta = math.sqrt(dx * dx + dy * dy + dz * dz)
                if delta <= float(move_epsilon):
                    continue
                moved += 1
                if index in walkable_vertices:
                    moved_walkable += 1
                    collision_candidates.append((old, new))
                max_delta = max(max_delta, delta)
                max_horizontal = max(max_horizontal, math.sqrt(dx * dx + dz * dz))
                max_vertical = max(max_vertical, abs(dy))
            collision_sample_count = 0
            collision_missing_sample_count = 0
            collision_max_floor_delta = 0.0
            collision_vertices_over_threshold = 0
            if physics_world is not None and collision_candidates:
                for old, new in _limited_samples(collision_candidates, collision_sample_limit):
                    collision_sample_count += 1
                    hint_min = min(float(old[1]), float(new[1])) - 128.0
                    hint_max = max(float(old[1]), float(new[1])) + 128.0
                    floor_y = _raycast_floor_y_in_band(
                        physics_world,
                        float(new[0]),
                        float(new[2]),
                        y_hint_min=hint_min,
                        y_hint_max=hint_max,
                    )
                    if floor_y is None:
                        collision_missing_sample_count += 1
                        continue
                    floor_delta = abs(float(new[1]) - float(floor_y))
                    collision_max_floor_delta = max(collision_max_floor_delta, floor_delta)
                    if floor_delta > float(collision_threshold):
                        collision_vertices_over_threshold += 1
            audits.append(TerrainVertexAudit(
                model_name=item.name,
                moved_vertex_count=moved,
                total_vertex_count=len(item.source_model.points),
                likely_walkable_moved_vertex_count=moved_walkable,
                max_delta=max_delta,
                max_horizontal_delta=max_horizontal,
                max_vertical_delta=max_vertical,
                physics_bsp_present=physics_world is not None,
                collision_sample_count=collision_sample_count,
                collision_missing_sample_count=collision_missing_sample_count,
                collision_max_floor_delta=collision_max_floor_delta,
                collision_vertices_over_threshold=collision_vertices_over_threshold,
            ))
    return audits


def terrain_audit_warnings(
    plans: Sequence[VertexEditPlan],
    *,
    vertical_collision_threshold: float = 16.0,
    horizontal_collision_threshold: float = 64.0,
    source_bsp: Optional[bsp.BspWorld] = None,
) -> List[str]:
    warnings: List[str] = []
    for audit in audit_terrain_vertex_edits(
        plans,
        source_bsp=source_bsp,
        collision_threshold=vertical_collision_threshold,
    ):
        if audit.moved_vertex_count <= 0:
            warnings.append(f"Terrain vertex audit: {audit.model_name} has no moved vertices")
            continue
        warnings.append(
            "Terrain vertex audit: "
            f"{audit.model_name} moved {audit.moved_vertex_count}/{audit.total_vertex_count} vertices "
            f"(likely walkable: {audit.likely_walkable_moved_vertex_count}); "
            f"max delta={audit.max_delta:.2f}, "
            f"horizontal={audit.max_horizontal_delta:.2f}, "
            f"vertical={audit.max_vertical_delta:.2f}"
        )
        if audit.likely_walkable_moved_vertex_count > 0 and not audit.physics_bsp_present:
            warnings.append(
                "Terrain collision audit: PhysicsBSP was not available, so edited terrain "
                "could not be compared with collision floor data"
            )
        if audit.collision_sample_count > 0:
            warnings.append(
                "Terrain collision audit: "
                f"sampled {audit.collision_sample_count} moved walkable vertices against PhysicsBSP; "
                f"missing={audit.collision_missing_sample_count}, "
                f"max floor delta={audit.collision_max_floor_delta:.2f}, "
                f"over {float(vertical_collision_threshold):.2f}={audit.collision_vertices_over_threshold}"
            )
            if audit.collision_missing_sample_count > 0:
                warnings.append(
                    "Terrain collision caution: some edited walkable terrain vertices no longer "
                    "raycast to a PhysicsBSP floor at their edited X/Z position"
                )
            if audit.collision_vertices_over_threshold > 0:
                warnings.append(
                    "Terrain collision caution: edited visible terrain diverges from unchanged "
                    f"PhysicsBSP floor by more than {float(vertical_collision_threshold):.2f} units"
                )
        if (
            audit.likely_walkable_moved_vertex_count > 0
            and audit.max_vertical_delta > float(vertical_collision_threshold)
        ):
            warnings.append(
                "Terrain collision caution: edited walkable-looking terrain moved vertically by "
                f"{audit.max_vertical_delta:.2f} units; PhysicsBSP remains unchanged"
            )
        if audit.max_horizontal_delta > float(horizontal_collision_threshold):
            warnings.append(
                "Terrain collision caution: terrain vertices moved horizontally by "
                f"{audit.max_horizontal_delta:.2f} units; collision and visibility remain unchanged"
            )
    return warnings


def fresh_load_validate_dat(
    data: bytes,
    *,
    expected_object_count: Optional[int] = None,
    required_model_names: Optional[Sequence[str]] = None,
) -> TerrainFreshLoadResult:
    """Parse final DAT bytes as a fresh-load smoke test for terrain edits."""
    validation = output_validation.validate_geometry_dat(
        data,
        expected_object_count=expected_object_count,
        required_bsp_names=required_model_names or [],
    )
    validation.raise_for_errors()
    parsed = validation.parsed_bsp
    names = [model.name for model in (parsed.world_models if parsed else [])]
    return TerrainFreshLoadResult(
        object_count=int(validation.object_count or 0),
        model_names=names,
        validation_warnings=list(validation.warnings),
    )


def apply_terrain_aware_vertex_edit_plans(
    source_dat: bytes,
    bsp_world: bsp.BspWorld,
    plans: Sequence[VertexEditPlan],
    *,
    experimental_section_bounds: bool = False,
    experimental_section_bounds_plan_keys: Optional[set[Tuple[str, str]]] = None,
) -> bytes:
    data = bytearray(source_dat)
    for plan in plans or []:
        current_bsp = bsp.parse(bytes(data))
        plan_key = (
            os.path.abspath(plan.source_path or ""),
            os.path.abspath(plan.metadata_path or ""),
        )
        plan_uses_section_bounds = bool(experimental_section_bounds)
        if experimental_section_bounds_plan_keys is not None:
            plan_uses_section_bounds = plan_key in experimental_section_bounds_plan_keys
        for item in plan.models:
            source_model = current_bsp.model_by_name(item.name)
            if source_model is None:
                raise ValueError(f"source BSP model {item.name!r} is not present")
            raw = current_bsp.raw_model_bytes(bytes(data), source_model)
            if raw is None or source_model.raw_start is None or source_model.raw_end is None:
                raise ValueError(f"BSP model {item.name!r} has no recoverable raw byte range")
            if _is_terrain_model(source_model):
                edited_model = _snapped_edited_model(
                    source_model,
                    item.edited_model,
                    move_epsilon=DEFAULT_MOVE_EPSILON,
                )
                if plan_uses_section_bounds:
                    classification = audit_terrain_render_classification(
                        raw,
                        source_model,
                        edited_model,
                    )
                    if _terrain_render_classification_is_clean(classification):
                        patched = patch_terrain_model_points_and_section_bounds(raw, source_model, edited_model)
                    else:
                        validate_experimental_render_topology_rebuild(
                            raw,
                            source_model,
                            edited_model,
                        )
                        patched = patch_terrain_model_points_section_bounds_and_render_topology(
                            raw,
                            source_model,
                            edited_model,
                        )
                else:
                    patched = patch_terrain_model_points_only(raw, source_model, edited_model)
            else:
                patched = patch_model_record(raw, source_model, item.edited_model)
            if len(patched) != len(raw):
                raise ValueError(f"patched BSP model {item.name!r} changed record size")
            data[source_model.raw_start:source_model.raw_end] = patched
    return bytes(data)


def patch_terrain_model_points_only(
    raw_record: bytes,
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
) -> bytes:
    if len(source_model.points) != len(edited_model.points):
        raise ValueError(f"terrain BSP model {source_model.name!r} point count changed")
    if len(source_model.polygons) != len(edited_model.polygons):
        raise ValueError(f"terrain BSP model {source_model.name!r} polygon count changed")
    edited_model = _snapped_edited_model(
        source_model,
        edited_model,
        move_epsilon=move_epsilon,
    )
    raw = bytearray(raw_record)
    (
        _name_length_pos,
        min_box_offset,
        max_box_offset,
        _translation_offset,
        _plane_offsets,
        _surface_offsets,
        _polygon_offsets,
        point_offsets,
    ) = bsp_writer._world_bsp_patch_offsets(raw_record, source_model)
    for (point_offset, _normal_offset), old, new in zip(point_offsets, source_model.points, edited_model.points):
        if _distance(old, new) <= float(move_epsilon):
            continue
        struct.pack_into("<3f", raw, point_offset, *new)

    min_box, max_box = _expanded_bounds_if_needed(source_model, edited_model)
    if min_box != tuple(source_model.min_box):
        struct.pack_into("<3f", raw, min_box_offset, *min_box)
    if max_box != tuple(source_model.max_box):
        struct.pack_into("<3f", raw, max_box_offset, *max_box)
    return bytes(raw)


def patch_terrain_model_points_and_section_bounds(
    raw_record: bytes,
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
) -> bytes:
    """Experimental Terrain* patch for off-plane edits.

    This updates vertex positions, derived polygon centers/point normals, and
    the decoded terrain render-section bound headers.  It deliberately leaves
    the plane table, render node topology, and polygon lists untouched.
    """
    validate_topology(source_model, edited_model)
    edited_model = _snapped_edited_model(
        source_model,
        edited_model,
        move_epsilon=move_epsilon,
    )
    raw = bytearray(raw_record)
    (
        _name_length_pos,
        min_box_offset,
        max_box_offset,
        translation_offset,
        _plane_offsets,
        _surface_offsets,
        polygon_offsets,
        point_offsets,
    ) = bsp_writer._world_bsp_patch_offsets(raw_record, source_model)

    min_box, max_box = _expanded_bounds_if_needed(source_model, edited_model)
    struct.pack_into("<3f", raw, min_box_offset, *min_box)
    struct.pack_into("<3f", raw, max_box_offset, *max_box)
    struct.pack_into("<3f", raw, translation_offset, *edited_model.translation)

    planes = [plane_for_polygon(edited_model.points, polygon) for polygon in edited_model.polygons]
    point_normals = point_normals_for_polygons(
        len(edited_model.points),
        edited_model.polygons,
        planes,
    )

    for (center_offset, _surface_index_offset, _plane_index_offset), polygon in zip(
        polygon_offsets,
        edited_model.polygons,
    ):
        center = polygon_center(edited_model.points, polygon)
        struct.pack_into("<3f", raw, center_offset, *center)

    for (point_offset, normal_offset), old, point, normal in zip(
        point_offsets,
        source_model.points,
        edited_model.points,
        point_normals,
    ):
        if _distance(old, point) > float(move_epsilon):
            struct.pack_into("<3f", raw, point_offset, *point)
        struct.pack_into("<3f", raw, normal_offset, *normal)

    _patch_terrain_render_section_bounds(raw, raw_record, source_model, edited_model)
    return bytes(raw)


def patch_terrain_model_points_section_bounds_and_render_topology(
    raw_record: bytes,
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
) -> bytes:
    """Experimental Terrain* patch that rebuilds fixed-size render node lists.

    This keeps the shipped render chunk chain and each chunk's polygon set in
    place, but rewrites decoded BSP node child references, compact node copies,
    and sorted polygon lists so moved polygon centers are classified against the
    edited splitter polygon planes.
    """
    validate_experimental_render_topology_rebuild(raw_record, source_model, edited_model)
    edited_model = _snapped_edited_model(
        source_model,
        edited_model,
        move_epsilon=move_epsilon,
    )
    raw = bytearray(patch_terrain_model_points_and_section_bounds(
        raw_record,
        source_model,
        edited_model,
        move_epsilon=move_epsilon,
    ))
    _rebuild_terrain_render_topology(raw, raw_record, source_model, edited_model)
    return bytes(raw)


def audit_terrain_render_classification(
    raw_record: bytes,
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
    *,
    classification_epsilon: float = 0.5,
    max_examples: int = 8,
) -> TerrainRenderClassificationAudit:
    """Audit whether a Terrain* edit preserves decoded render BSP side tests.

    The decoded Terrain0 tail stores BSP-style node tables whose splitter is a
    terrain polygon index.  This audit keeps those compiled splitter planes
    fixed and checks whether edited child-subtree polygon centers or vertices
    moved to a different side of any splitter plane.
    """
    validate_topology(source_model, edited_model)
    layout = bsp_record_inspector._decode_world_bsp_layout(raw_record, source_model)
    node_tables = _terrain_render_bsp_node_table_ranges(layout)
    changed_centers = 0
    changed_vertices = 0
    ambiguous = 0
    checked_refs = 0
    checked_nodes = 0
    max_center_delta = 0.0
    max_vertex_delta = 0.0
    examples: List[str] = []
    epsilon = float(classification_epsilon)
    source_centers = [
        polygon_center(source_model.points, polygon)
        for polygon in source_model.polygons
    ]
    edited_centers = [
        polygon_center(edited_model.points, polygon)
        for polygon in edited_model.polygons
    ]
    source_polygon_points = [
        [
            source_model.points[int(vertex_index)]
            for vertex_index in polygon.vertex_indices
            if 0 <= int(vertex_index) < len(source_model.points)
        ]
        for polygon in source_model.polygons
    ]
    edited_polygon_points = [
        [
            edited_model.points[int(vertex_index)]
            for vertex_index in polygon.vertex_indices
            if 0 <= int(vertex_index) < len(edited_model.points)
        ]
        for polygon in edited_model.polygons
    ]

    for table_name, node_range, includes_count in node_tables:
        nodes = _terrain_section_nodes(raw_record, node_range, includes_count=includes_count)
        if not nodes:
            continue
        descendants: Dict[int, set[int]] = {}
        for node_index, (splitter_polygon_index, side0, side1) in enumerate(nodes):
            if not (0 <= splitter_polygon_index < len(source_model.polygons)):
                continue
            checked_nodes += 1
            normal, distance = plane_for_polygon(
                source_model.points,
                source_model.polygons[int(splitter_polygon_index)],
            )
            for side_index, child_index in enumerate((side0, side1)):
                if child_index < 0 or child_index >= len(nodes):
                    continue
                for polygon_index in _terrain_node_descendant_polygons(nodes, int(child_index), descendants):
                    if not (0 <= polygon_index < len(source_model.polygons)):
                        continue
                    checked_refs += 1
                    source_center = source_centers[int(polygon_index)]
                    edited_center = edited_centers[int(polygon_index)]
                    source_center_distance = _dot(normal, source_center) - float(distance)
                    edited_center_distance = _dot(normal, edited_center) - float(distance)
                    center_delta = abs(edited_center_distance - source_center_distance)
                    max_center_delta = max(max_center_delta, center_delta)
                    source_center_sign = _classification_sign(source_center_distance, epsilon)
                    edited_center_sign = _classification_sign(edited_center_distance, epsilon)
                    if source_center_sign == 0:
                        ambiguous += 1
                    if source_center_sign != edited_center_sign:
                        changed_centers += 1
                        if len(examples) < int(max_examples):
                            examples.append(
                                f"{table_name}[{node_index}] side {side_index} polygon {polygon_index}: "
                                f"center {source_center_distance:.2f}->{edited_center_distance:.2f}"
                            )

                    source_vertex_signs = _points_classification_signs(
                        source_polygon_points[int(polygon_index)],
                        normal,
                        float(distance),
                        epsilon,
                    )
                    edited_vertex_signs, vertex_delta = _edited_points_classification_signs(
                        source_polygon_points[int(polygon_index)],
                        edited_polygon_points[int(polygon_index)],
                        normal,
                        float(distance),
                        epsilon,
                    )
                    max_vertex_delta = max(max_vertex_delta, vertex_delta)
                    if any(sign == 0 for sign in source_vertex_signs):
                        ambiguous += 1
                    if source_vertex_signs != edited_vertex_signs:
                        changed_vertices += 1
                        if len(examples) < int(max_examples):
                            examples.append(
                                f"{table_name}[{node_index}] side {side_index} polygon {polygon_index}: "
                                f"vertices {source_vertex_signs}->{edited_vertex_signs}"
                            )

    return TerrainRenderClassificationAudit(
        model_name=str(source_model.name),
        checked_node_table_count=len(node_tables),
        checked_node_count=int(checked_nodes),
        checked_polygon_reference_count=int(checked_refs),
        changed_center_reference_count=int(changed_centers),
        changed_vertex_reference_count=int(changed_vertices),
        source_ambiguous_reference_count=int(ambiguous),
        max_center_distance_delta=float(max_center_delta),
        max_vertex_distance_delta=float(max_vertex_delta),
        examples=examples,
    )


def audit_terrain_render_split_spans(
    raw_record: bytes,
    source_model: bsp.WorldModelMesh,
    *,
    classification_epsilon: float = 0.5,
    max_examples: int = 8,
) -> TerrainRenderSplitSpanAudit:
    """Summarize how shipped render nodes represent split-spanning polygons."""
    layout = bsp_record_inspector._decode_world_bsp_layout(raw_record, source_model)
    node_tables = _terrain_render_bsp_node_table_ranges(layout)
    epsilon = float(classification_epsilon)
    polygon_points = [
        [
            source_model.points[int(vertex_index)]
            for vertex_index in polygon.vertex_indices
            if 0 <= int(vertex_index) < len(source_model.points)
        ]
        for polygon in source_model.polygons
    ]
    checked_nodes = 0
    checked_refs = 0
    spanning_refs = 0
    touching_refs = 0
    duplicate_node_refs = 0
    spanning_polygons: set[int] = set()
    touching_polygons: set[int] = set()
    repeated_polygons: set[int] = set()
    examples: List[str] = []

    for table_name, node_range, includes_count in node_tables:
        nodes = _terrain_section_nodes(raw_record, node_range, includes_count=includes_count)
        if not nodes:
            continue
        polygon_counts: Dict[int, int] = {}
        for polygon_index, _side0, _side1 in nodes:
            polygon_counts[int(polygon_index)] = polygon_counts.get(int(polygon_index), 0) + 1
        repeated_polygons.update(
            polygon_index
            for polygon_index, count in polygon_counts.items()
            if count > 1
        )
        duplicate_node_refs += sum(count - 1 for count in polygon_counts.values() if count > 1)

        descendants: Dict[int, set[int]] = {}
        for node_index, (splitter_polygon_index, side0, side1) in enumerate(nodes):
            if not (0 <= splitter_polygon_index < len(source_model.polygons)):
                continue
            checked_nodes += 1
            normal, distance = plane_for_polygon(
                source_model.points,
                source_model.polygons[int(splitter_polygon_index)],
            )
            for side_index, child_index in enumerate((side0, side1)):
                if child_index < 0 or child_index >= len(nodes):
                    continue
                for polygon_index in _terrain_node_descendant_polygons(nodes, int(child_index), descendants):
                    if not (0 <= polygon_index < len(source_model.polygons)):
                        continue
                    checked_refs += 1
                    signs = _points_classification_signs(
                        polygon_points[int(polygon_index)],
                        normal,
                        float(distance),
                        epsilon,
                    )
                    nonzero_signs = {sign for sign in signs if sign != 0}
                    if len(nonzero_signs) > 1:
                        spanning_refs += 1
                        spanning_polygons.add(int(polygon_index))
                        if len(examples) < int(max_examples):
                            examples.append(
                                f"{table_name}[{node_index}] side {side_index} polygon "
                                f"{polygon_index}: vertices span {signs}"
                            )
                    if any(sign == 0 for sign in signs):
                        touching_refs += 1
                        touching_polygons.add(int(polygon_index))

    return TerrainRenderSplitSpanAudit(
        model_name=str(source_model.name),
        checked_node_table_count=len(node_tables),
        checked_node_count=int(checked_nodes),
        checked_polygon_reference_count=int(checked_refs),
        spanning_reference_count=int(spanning_refs),
        spanning_polygon_count=len(spanning_polygons),
        touching_reference_count=int(touching_refs),
        touching_polygon_count=len(touching_polygons),
        repeated_node_polygon_count=len(repeated_polygons),
        duplicate_node_reference_count=int(duplicate_node_refs),
        repeated_spanning_polygon_count=len(spanning_polygons & repeated_polygons),
        repeated_touching_polygon_count=len(touching_polygons & repeated_polygons),
        examples=examples,
    )


def _terrain_render_topology_rebuild_errors(
    raw_record: bytes,
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
) -> List[str]:
    errors: List[str] = []
    try:
        validate_topology(source_model, edited_model)
        layout = bsp_record_inspector._decode_world_bsp_layout(raw_record, source_model)
    except Exception as exc:
        return [f"Terrain0 render tail could not be decoded ({exc})"]

    if not bool(getattr(layout, "terrain_tail_render_fully_decoded", False)):
        errors.append("render chunk chain is not fully decoded")
    if int(getattr(layout, "terrain_tail_render_unknown_payload_size", 0) or 0) != 0:
        errors.append("render chunk chain has unknown tail bytes")
    if not bool(getattr(layout, "terrain_tail_render_chunk_chain_valid", False)):
        errors.append("render chunk chain is not valid")
    if int(getattr(layout, "terrain_tail_render_terminal_chunk_count", 0) or 0) != 1:
        errors.append("render chunk chain does not have exactly one terminal chunk")

    ranges = getattr(layout, "section_ranges", {}) or {}
    node_tables = _terrain_render_bsp_node_table_ranges(layout)
    if not node_tables:
        errors.append("no decoded Terrain0 render BSP node tables were found")
    table_names = {name for name, _range, _includes_count in node_tables}
    for header_name, node_name in _terrain_render_header_node_ranges(layout):
        if node_name not in table_names:
            errors.append(f"{header_name} source node table {node_name} is not decoded")
            continue
        compact_name = _terrain_compact_nodes_name_for_header(header_name)
        if compact_name not in ranges:
            errors.append(f"{header_name} compact node table is not decoded")

    for table_name, node_range, includes_count in node_tables:
        nodes = _terrain_section_nodes(raw_record, node_range, includes_count=includes_count)
        if not nodes:
            errors.append(f"{table_name} has no nodes")
            continue
        if len(nodes) >= 0xFFFE:
            errors.append(f"{table_name} has too many nodes for compact u16 copies")
        root_count = _terrain_node_root_count(nodes)
        if root_count != 1 or _terrain_node_root_index(nodes) != 0:
            errors.append(f"{table_name} root is not the expected node 0")
        if any(
            not (0 <= int(poly_index) < len(source_model.polygons))
            for poly_index, _side0, _side1 in nodes
        ):
            errors.append(f"{table_name} contains an out-of-range polygon index")
        expected_node_bytes = (4 if includes_count else 0) + len(nodes) * 12
        if int(node_range[1]) - int(node_range[0]) != expected_node_bytes:
            errors.append(f"{table_name} node range has unexpected padding")
        list_name = _terrain_polygon_list_name_for_node_table(table_name)
        list_range = ranges.get(list_name)
        if list_range is None:
            errors.append(f"{table_name} sorted polygon list is not decoded")
        else:
            unique_polygon_count = len({int(poly_index) for poly_index, _side0, _side1 in nodes})
            if int(list_range[1]) - int(list_range[0]) != 8 + unique_polygon_count * 4:
                errors.append(f"{table_name} sorted polygon list size would need to change")
        errors.extend(_terrain_node_table_rebuild_errors(table_name, nodes, source_model, edited_model))

    return errors


def _rebuild_terrain_render_topology(
    raw: bytearray,
    raw_record: bytes,
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
) -> None:
    layout = bsp_record_inspector._decode_world_bsp_layout(raw_record, source_model)
    ranges = layout.section_ranges
    rebuilt_tables: Dict[str, List[Tuple[int, int, int]]] = {}
    for table_name, node_range, includes_count in _terrain_render_bsp_node_table_ranges(layout):
        source_nodes = _terrain_section_nodes(raw_record, node_range, includes_count=includes_count)
        rebuilt_nodes = _rebuild_terrain_node_table(source_nodes, source_model, edited_model)
        _write_terrain_node_table(
            raw,
            node_range,
            rebuilt_nodes,
            includes_count=includes_count,
        )
        _write_terrain_sorted_polygon_list(
            raw,
            ranges[_terrain_polygon_list_name_for_node_table(table_name)],
            rebuilt_nodes,
        )
        rebuilt_tables[table_name] = rebuilt_nodes

    for header_name, node_name in _terrain_render_header_node_ranges(layout):
        compact_name = _terrain_compact_nodes_name_for_header(header_name)
        nodes = rebuilt_tables.get(node_name)
        compact_range = ranges.get(compact_name)
        if nodes is None or compact_range is None:
            continue
        _write_terrain_compact_node_table(raw, compact_range, nodes)


def _rebuild_terrain_node_table(
    source_nodes: Sequence[Tuple[int, int, int]],
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
    *,
    classification_epsilon: float = 0.5,
) -> List[Tuple[int, int, int]]:
    result: List[Optional[Tuple[int, int, int]]] = [None] * len(source_nodes)
    source_centers = [
        polygon_center(source_model.points, polygon)
        for polygon in source_model.polygons
    ]
    edited_centers = [
        polygon_center(edited_model.points, polygon)
        for polygon in edited_model.polygons
    ]
    source_polygon_points = [
        [
            source_model.points[int(vertex_index)]
            for vertex_index in polygon.vertex_indices
            if 0 <= int(vertex_index) < len(source_model.points)
        ]
        for polygon in source_model.polygons
    ]
    edited_polygon_points = [
        [
            edited_model.points[int(vertex_index)]
            for vertex_index in polygon.vertex_indices
            if 0 <= int(vertex_index) < len(edited_model.points)
        ]
        for polygon in edited_model.polygons
    ]
    descendant_memo: Dict[int, set[int]] = {}
    errors: List[str] = []

    def build(
        indices: Sequence[int],
        preferred_root_index: Optional[int] = None,
        *,
        source_cell: Sequence[Tuple[Vec3, float, int]] = (),
        edited_cell: Sequence[Tuple[Vec3, float, int]] = (),
    ) -> int:
        root_index = _choose_terrain_subtree_root(
            source_nodes,
            indices,
            preferred_root_index=preferred_root_index,
        )
        if root_index < 0:
            raise ValueError("cannot rebuild empty terrain render subtree")
        splitter_polygon_index = int(source_nodes[root_index][0])
        source_normal, source_distance = plane_for_polygon(
            source_model.points,
            source_model.polygons[splitter_polygon_index],
        )
        normal, distance = plane_for_polygon(
            edited_model.points,
            edited_model.polygons[splitter_polygon_index],
        )
        side0_indices: List[int] = []
        side1_indices: List[int] = []
        occurrences_by_polygon: Dict[int, List[int]] = {}
        for candidate_index in indices:
            candidate_index = int(candidate_index)
            if candidate_index == root_index:
                continue
            polygon_index = int(source_nodes[candidate_index][0])
            occurrences_by_polygon.setdefault(polygon_index, []).append(candidate_index)

        for polygon_index, occurrences in occurrences_by_polygon.items():
            source_split_sides = _terrain_polygon_desired_split_sides_in_cell(
                source_polygon_points[int(polygon_index)],
                source_centers[int(polygon_index)],
                source_cell,
                source_normal,
                float(source_distance),
                classification_epsilon,
            )
            desired_sides = _terrain_polygon_desired_split_sides_in_cell(
                edited_polygon_points[int(polygon_index)],
                edited_centers[int(polygon_index)],
                edited_cell,
                normal,
                float(distance),
                classification_epsilon,
            )
            if desired_sides == {-1, 1}:
                source_occurrence_sides = _terrain_occurrence_source_sides(
                    source_nodes,
                    root_index,
                    occurrences,
                    descendant_memo,
                )
                if source_split_sides != {-1, 1} and source_occurrence_sides != {-1, 1}:
                    errors.append(
                        f"polygon {polygon_index} newly spans a render split but "
                        "the fixed source occurrence placement does not cover both child sides"
                    )
                    side0_indices.extend(
                        int(occurrence)
                        for occurrence in occurrences
                        if _source_descendant_side(
                            source_nodes,
                            root_index,
                            int(occurrence),
                            descendant_memo,
                        ) <= 0
                    )
                    side1_indices.extend(
                        int(occurrence)
                        for occurrence in occurrences
                        if _source_descendant_side(
                            source_nodes,
                            root_index,
                            int(occurrence),
                            descendant_memo,
                        ) > 0
                    )
                    continue
                assigned = _assign_spanning_occurrences_to_sides(
                    source_nodes,
                    root_index,
                    occurrences,
                    descendant_memo,
                )
                side0_indices.extend(assigned[-1])
                side1_indices.extend(assigned[1])
                continue
            desired_side = next(iter(desired_sides)) if desired_sides else 0
            if desired_side == 0:
                for candidate_index in occurrences:
                    source_side = _source_descendant_side(
                        source_nodes,
                        root_index,
                        int(candidate_index),
                        descendant_memo,
                    )
                    if source_side <= 0:
                        side0_indices.append(int(candidate_index))
                    else:
                        side1_indices.append(int(candidate_index))
            elif desired_side < 0:
                side0_indices.extend(occurrences)
            else:
                side1_indices.extend(occurrences)

        side0 = (
            build(
                side0_indices,
                preferred_root_index=int(source_nodes[root_index][1]),
                source_cell=tuple(source_cell) + ((source_normal, float(source_distance), -1),),
                edited_cell=tuple(edited_cell) + ((normal, float(distance), -1),),
            )
            if side0_indices
            else -2
        )
        side1 = (
            build(
                side1_indices,
                preferred_root_index=int(source_nodes[root_index][2]),
                source_cell=tuple(source_cell) + ((source_normal, float(source_distance), 1),),
                edited_cell=tuple(edited_cell) + ((normal, float(distance), 1),),
            )
            if side1_indices
            else -1
        )
        result[root_index] = (splitter_polygon_index, int(side0), int(side1))
        return root_index

    if source_nodes:
        build(list(range(len(source_nodes))), preferred_root_index=0)
    if errors:
        raise ValueError("; ".join(errors[:8]))
    return [
        item if item is not None else (int(source_nodes[index][0]), -2, -1)
        for index, item in enumerate(result)
    ]


def _source_descendant_side(
    nodes: Sequence[Tuple[int, int, int]],
    root_index: int,
    candidate_index: int,
    descendant_memo: Dict[int, set[int]],
) -> int:
    _poly_index, side0, side1 = nodes[int(root_index)]
    if int(side0) >= 0 and int(candidate_index) in _terrain_node_descendant_indices(nodes, int(side0), descendant_memo):
        return -1
    if int(side1) >= 0 and int(candidate_index) in _terrain_node_descendant_indices(nodes, int(side1), descendant_memo):
        return 1
    return -1


def _terrain_node_table_rebuild_errors(
    table_name: str,
    source_nodes: Sequence[Tuple[int, int, int]],
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
) -> List[str]:
    try:
        _rebuild_terrain_node_table(source_nodes, source_model, edited_model)
    except ValueError as exc:
        return [f"{table_name}: {exc}"]
    return []


def _terrain_occurrence_source_sides(
    source_nodes: Sequence[Tuple[int, int, int]],
    root_index: int,
    occurrences: Sequence[int],
    descendant_memo: Dict[int, set[int]],
) -> set[int]:
    return {
        _source_descendant_side(
            source_nodes,
            int(root_index),
            int(occurrence),
            descendant_memo,
        )
        for occurrence in occurrences
    }


def _choose_terrain_subtree_root(
    source_nodes: Sequence[Tuple[int, int, int]],
    indices: Sequence[int],
    *,
    preferred_root_index: Optional[int],
) -> int:
    choices = {int(index) for index in indices}
    if not choices:
        return -1
    preferred = int(preferred_root_index) if preferred_root_index is not None else -1
    if preferred in choices:
        return preferred
    parents: Dict[int, int] = {}
    for index in choices:
        _polygon_index, side0, side1 = source_nodes[int(index)]
        for side in (int(side0), int(side1)):
            if side in choices:
                parents[side] = parents.get(side, 0) + 1
    roots = [index for index in sorted(choices) if parents.get(index, 0) == 0]
    if roots:
        return roots[0]
    return min(choices)


def _terrain_polygon_desired_split_sides(
    points: Sequence[Vec3],
    center: Vec3,
    normal: Vec3,
    distance: float,
    epsilon: float,
) -> set[int]:
    signs = _points_classification_signs(points, normal, float(distance), float(epsilon))
    nonzero_signs = {sign for sign in signs if sign != 0}
    if len(nonzero_signs) > 1:
        return {-1, 1}
    if len(nonzero_signs) == 1:
        return nonzero_signs
    center_sign = _classification_sign(_dot(normal, center) - float(distance), float(epsilon))
    if center_sign < 0:
        return {-1}
    if center_sign > 0:
        return {1}
    return {0}


def _terrain_polygon_desired_split_sides_in_cell(
    points: Sequence[Vec3],
    center: Vec3,
    cell_planes: Sequence[Tuple[Vec3, float, int]],
    normal: Vec3,
    distance: float,
    epsilon: float,
) -> set[int]:
    whole_polygon_sides = _terrain_polygon_desired_split_sides(
        points,
        center,
        normal,
        distance,
        epsilon,
    )
    if whole_polygon_sides != {-1, 1}:
        return whole_polygon_sides
    clipped = _clip_polygon_to_terrain_cell(points, cell_planes, float(epsilon))
    if clipped:
        return _terrain_polygon_desired_split_sides(
            clipped,
            _point_average(clipped),
            normal,
            distance,
            epsilon,
        )
    return _terrain_polygon_desired_split_sides(
        points,
        center,
        normal,
        distance,
        epsilon,
    )


def _clip_polygon_to_terrain_cell(
    points: Sequence[Vec3],
    cell_planes: Sequence[Tuple[Vec3, float, int]],
    epsilon: float,
) -> List[Vec3]:
    clipped = list(points)
    for normal, distance, side in cell_planes:
        clipped = _clip_polygon_to_terrain_halfspace(
            clipped,
            normal,
            float(distance),
            int(side),
            float(epsilon),
        )
        if len(clipped) < 3:
            return []
    return clipped


def _clip_polygon_to_terrain_halfspace(
    points: Sequence[Vec3],
    normal: Vec3,
    distance: float,
    side: int,
    epsilon: float,
) -> List[Vec3]:
    if not points:
        return []

    def inside(value: float) -> bool:
        if int(side) < 0:
            return value <= float(epsilon)
        return value >= -float(epsilon)

    result: List[Vec3] = []
    previous = points[-1]
    previous_value = _dot(normal, previous) - float(distance)
    previous_inside = inside(previous_value)
    for current in points:
        current_value = _dot(normal, current) - float(distance)
        current_inside = inside(current_value)
        if current_inside != previous_inside:
            denominator = previous_value - current_value
            if abs(denominator) > 1e-6:
                t = previous_value / denominator
                result.append((
                    float(previous[0]) + (float(current[0]) - float(previous[0])) * t,
                    float(previous[1]) + (float(current[1]) - float(previous[1])) * t,
                    float(previous[2]) + (float(current[2]) - float(previous[2])) * t,
                ))
        if current_inside:
            result.append(current)
        previous = current
        previous_value = current_value
        previous_inside = current_inside
    return _dedupe_adjacent_points(result)


def _dedupe_adjacent_points(points: Sequence[Vec3]) -> List[Vec3]:
    result: List[Vec3] = []
    for point in points:
        if not result or _distance(result[-1], point) > 1e-4:
            result.append(point)
    if len(result) > 1 and _distance(result[0], result[-1]) <= 1e-4:
        result.pop()
    return result


def _point_average(points: Sequence[Vec3]) -> Vec3:
    if not points:
        return (0.0, 0.0, 0.0)
    count = float(len(points))
    return (
        sum(float(point[0]) for point in points) / count,
        sum(float(point[1]) for point in points) / count,
        sum(float(point[2]) for point in points) / count,
    )


def _assign_spanning_occurrences_to_sides(
    source_nodes: Sequence[Tuple[int, int, int]],
    root_index: int,
    occurrences: Sequence[int],
    descendant_memo: Dict[int, set[int]],
) -> Dict[int, List[int]]:
    assigned: Dict[int, List[int]] = {-1: [], 1: []}
    undecided: List[int] = []
    for occurrence in occurrences:
        source_side = _source_descendant_side(
            source_nodes,
            int(root_index),
            int(occurrence),
            descendant_memo,
        )
        if source_side < 0:
            assigned[-1].append(int(occurrence))
        elif source_side > 0:
            assigned[1].append(int(occurrence))
        else:
            undecided.append(int(occurrence))
    for occurrence in undecided:
        target_side = -1 if len(assigned[-1]) <= len(assigned[1]) else 1
        assigned[target_side].append(int(occurrence))
    return assigned


def _terrain_node_descendant_indices(
    nodes: Sequence[Tuple[int, int, int]],
    node_index: int,
    memo: Dict[int, set[int]],
) -> set[int]:
    if node_index in memo:
        return memo[node_index]
    if not (0 <= int(node_index) < len(nodes)):
        return set()
    _polygon_index, side0, side1 = nodes[int(node_index)]
    result = {int(node_index)}
    for child_index in (int(side0), int(side1)):
        if child_index >= 0:
            result.update(_terrain_node_descendant_indices(nodes, child_index, memo))
    memo[int(node_index)] = result
    return result


def _write_terrain_node_table(
    raw: bytearray,
    node_range: Tuple[int, int],
    nodes: Sequence[Tuple[int, int, int]],
    *,
    includes_count: bool,
) -> None:
    start, end = int(node_range[0]), int(node_range[1])
    cursor = start
    if includes_count:
        struct.pack_into("<I", raw, cursor, len(nodes))
        cursor += 4
    expected_end = cursor + len(nodes) * 12
    if expected_end != end:
        raise ValueError("rebuilt terrain node table would change byte size")
    for index, (polygon_index, side0, side1) in enumerate(nodes):
        struct.pack_into("<Iii", raw, cursor + index * 12, int(polygon_index), int(side0), int(side1))


def _write_terrain_sorted_polygon_list(
    raw: bytearray,
    list_range: Tuple[int, int],
    nodes: Sequence[Tuple[int, int, int]],
) -> None:
    start, end = int(list_range[0]), int(list_range[1])
    values = sorted({int(polygon_index) for polygon_index, _side0, _side1 in nodes})
    expected_end = start + 8 + len(values) * 4
    if expected_end != end:
        raise ValueError("rebuilt terrain polygon list would change byte size")
    struct.pack_into("<II", raw, start, 0, len(values))
    for index, polygon_index in enumerate(values):
        struct.pack_into("<I", raw, start + 8 + index * 4, int(polygon_index))


def _write_terrain_compact_node_table(
    raw: bytearray,
    compact_range: Tuple[int, int],
    nodes: Sequence[Tuple[int, int, int]],
) -> None:
    start, end = int(compact_range[0]), int(compact_range[1])
    if len(nodes) >= 0xFFFE:
        raise ValueError("rebuilt compact terrain node table exceeds u16 limits")
    entries: List[Tuple[int, int, int]] = []
    node_to_compact: Dict[int, int] = {}

    def emit(node_index: int) -> int:
        if int(node_index) in node_to_compact:
            return node_to_compact[int(node_index)]
        _polygon_index, side0, side1 = nodes[int(node_index)]
        compact_side0 = _compact_child_ref(side0, emit)
        compact_side1 = _compact_child_ref(side1, emit)
        compact_index = len(entries)
        node_to_compact[int(node_index)] = compact_index
        entries.append((int(node_index), int(compact_side0), int(compact_side1)))
        return compact_index

    root = emit(0)
    if start + 4 + len(entries) * 6 != end:
        raise ValueError("rebuilt compact terrain node table would change byte size")
    struct.pack_into("<HH", raw, start, len(entries), int(root))
    for index, (node_index, side0, side1) in enumerate(entries):
        struct.pack_into("<HHH", raw, start + 4 + index * 6, int(node_index), int(side0), int(side1))


def _compact_child_ref(
    child_index: int,
    emit: Any,
) -> int:
    if int(child_index) == -2:
        return 0xFFFF
    if int(child_index) == -1:
        return 0xFFFE
    if int(child_index) < 0:
        raise ValueError(f"unsupported terrain node leaf marker {child_index}")
    return int(emit(int(child_index)))


def _terrain_node_root_count(nodes: Sequence[Tuple[int, int, int]]) -> int:
    parents: Dict[int, int] = {}
    for _polygon_index, side0, side1 in nodes:
        for side in (int(side0), int(side1)):
            if side >= 0:
                parents[side] = parents.get(side, 0) + 1
    return sum(1 for index in range(len(nodes)) if parents.get(index, 0) == 0)


def _terrain_node_root_index(nodes: Sequence[Tuple[int, int, int]]) -> int:
    parents: Dict[int, int] = {}
    for _polygon_index, side0, side1 in nodes:
        for side in (int(side0), int(side1)):
            if side >= 0:
                parents[side] = parents.get(side, 0) + 1
    roots = [index for index in range(len(nodes)) if parents.get(index, 0) == 0]
    return roots[0] if roots else -1


def _patch_terrain_render_section_bounds(
    raw: bytearray,
    raw_record: bytes,
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
) -> None:
    layout = bsp_record_inspector._decode_world_bsp_layout(raw_record, source_model)
    ranges = layout.section_ranges
    for header_name, node_name in _terrain_render_header_node_ranges(layout):
        header_range = ranges.get(header_name)
        node_range = ranges.get(node_name)
        if header_range is None or node_range is None:
            continue
        if header_range[1] - header_range[0] < 36:
            continue
        polygon_indices = _terrain_section_node_polygon_indices(
            raw_record,
            node_range,
            includes_count=(node_name == "terrain_tail_nodes"),
        )
        if not polygon_indices:
            continue
        min_box, max_box = _bounds_for_polygons(edited_model, polygon_indices)
        size = (
            float(max_box[0]) - float(min_box[0]),
            float(max_box[1]) - float(min_box[1]),
            float(max_box[2]) - float(min_box[2]),
        )
        struct.pack_into("<3f", raw, header_range[0] + 12, *size)
        struct.pack_into("<3f", raw, header_range[0] + 24, *min_box)


def _terrain_render_header_node_ranges(layout: object) -> List[Tuple[str, str]]:
    ranges = getattr(layout, "section_ranges", {}) or {}
    chunk_count = int(getattr(layout, "terrain_tail_render_chunk_count", 0) or 0)
    pairs: List[Tuple[str, str]] = []
    for chunk_index in range(chunk_count):
        if chunk_index == 0:
            header_name = "terrain_tail_render_header"
            node_name = "terrain_tail_nodes"
        else:
            header_name = f"terrain_tail_render_chunk_{chunk_index:03d}_header"
            if chunk_index == 1:
                node_name = "terrain_tail_render_bsp_nodes"
            else:
                node_name = f"terrain_tail_render_chunk_{chunk_index - 1:03d}_bsp_nodes"
        if header_name in ranges and node_name in ranges:
            pairs.append((header_name, node_name))
    return pairs


def _terrain_compact_nodes_name_for_header(header_name: str) -> str:
    if header_name == "terrain_tail_render_header":
        return "terrain_tail_render_compact_nodes"
    if header_name.endswith("_header"):
        return header_name[:-len("_header")] + "_compact_nodes"
    return header_name + "_compact_nodes"


def _terrain_polygon_list_name_for_node_table(node_name: str) -> str:
    if node_name == "terrain_tail_nodes":
        return "terrain_tail_polygon_list"
    if node_name.endswith("_nodes"):
        return node_name[:-len("_nodes")] + "_polygon_list"
    return node_name + "_polygon_list"


def _terrain_render_bsp_node_table_ranges(layout: object) -> List[Tuple[str, Tuple[int, int], bool]]:
    ranges = getattr(layout, "section_ranges", {}) or {}
    names: List[str] = []
    if "terrain_tail_nodes" in ranges:
        names.append("terrain_tail_nodes")
    if "terrain_tail_render_bsp_nodes" in ranges:
        names.append("terrain_tail_render_bsp_nodes")
    chunk_count = int(getattr(layout, "terrain_tail_render_chunk_count", 0) or 0)
    for chunk_index in range(1, chunk_count):
        name = f"terrain_tail_render_chunk_{chunk_index:03d}_bsp_nodes"
        if name in ranges:
            names.append(name)
    return [
        (name, ranges[name], name == "terrain_tail_nodes")
        for name in names
    ]


def _terrain_section_nodes(
    raw_record: bytes,
    node_range: Tuple[int, int],
    *,
    includes_count: bool,
) -> List[Tuple[int, int, int]]:
    start, end = node_range
    start = int(start)
    end = int(end)
    if includes_count:
        if start + 4 > end:
            return []
        count = struct.unpack_from("<I", raw_record, start)[0]
        start += 4
        end = min(end, start + int(count) * 12)
    count = max(0, (end - start) // 12)
    return [
        tuple(int(value) for value in struct.unpack_from("<Iii", raw_record, start + index * 12))
        for index in range(count)
    ]


def _terrain_section_node_polygon_indices(
    raw_record: bytes,
    node_range: Tuple[int, int],
    *,
    includes_count: bool,
) -> List[int]:
    return [
        int(polygon_index)
        for polygon_index, _side0, _side1 in _terrain_section_nodes(
            raw_record,
            node_range,
            includes_count=includes_count,
        )
    ]


def _terrain_node_descendant_polygons(
    nodes: Sequence[Tuple[int, int, int]],
    node_index: int,
    memo: Dict[int, set[int]],
) -> set[int]:
    if node_index in memo:
        return memo[node_index]
    if not (0 <= int(node_index) < len(nodes)):
        return set()
    polygon_index, side0, side1 = nodes[int(node_index)]
    result = {int(polygon_index)}
    for child_index in (int(side0), int(side1)):
        if child_index >= 0:
            result.update(_terrain_node_descendant_polygons(nodes, child_index, memo))
    memo[int(node_index)] = result
    return result


def _points_classification_signs(
    points: Sequence[Vec3],
    normal: Vec3,
    distance: float,
    epsilon: float,
) -> Tuple[int, ...]:
    return terrain_reconstruction.points_classification_signs(points, normal, distance, epsilon)


def _edited_points_classification_signs(
    source_points: Sequence[Vec3],
    edited_points: Sequence[Vec3],
    normal: Vec3,
    distance: float,
    epsilon: float,
) -> Tuple[Tuple[int, ...], float]:
    return terrain_reconstruction.edited_points_classification_signs(
        source_points,
        edited_points,
        normal,
        distance,
        epsilon,
    )


def _classification_sign(value: float, epsilon: float) -> int:
    return terrain_reconstruction.classification_sign(value, epsilon)


def _bounds_for_polygons(
    model: bsp.WorldModelMesh,
    polygon_indices: Sequence[int],
) -> Tuple[Vec3, Vec3]:
    vertices: List[Vec3] = []
    for polygon_index in polygon_indices:
        if not (0 <= int(polygon_index) < len(model.polygons)):
            continue
        polygon = model.polygons[int(polygon_index)]
        for vertex_index in polygon.vertex_indices:
            if 0 <= int(vertex_index) < len(model.points):
                vertices.append(model.points[int(vertex_index)])
    if not vertices:
        return _bounds(model.points)
    return terrain_reconstruction.vec3_bounds(vertices)


def _is_terrain_model(model: bsp.WorldModelMesh) -> bool:
    return terrain_semantics.is_terrain_model(model)


def _is_physics_bsp_model(model: bsp.WorldModelMesh) -> bool:
    return terrain_semantics.is_physics_bsp_model(model)


_SURFACE_FLAG_NAMES: Tuple[Tuple[int, str], ...] = (
    (1 << 0, "SOLID"),
    (1 << 1, "NONEXISTENT"),
    (1 << 2, "INVISIBLE"),
    (1 << 4, "SKY"),
    (1 << 6, "FLATSHADE"),
    (1 << 7, "LIGHTMAP"),
    (1 << 8, "NOSUBDIV"),
    (1 << 10, "PARTICLEBLOCKER"),
    (1 << 12, "GOURAUDSHADE"),
    (1 << 17, "PHYSICSBLOCKER"),
    (1 << 19, "RBSPLITTER"),
    (1 << 21, "VISBLOCKER"),
    (1 << 22, "NOTASTEP"),
    (1 << 24, "RECEIVELIGHT"),
    (1 << 25, "RECEIVESHADOWS"),
    (1 << 26, "RECEIVESUNLIGHT"),
    (1 << 28, "SHADOWMESH"),
    (1 << 29, "CASTSHADOWMESH"),
    (1 << 30, "CLIPLIGHT"),
)


_SPECIAL_COLLISION_SURFACE_FLAGS = {
    "NONEXISTENT",
    "INVISIBLE",
    "SKY",
    "PARTICLEBLOCKER",
    "PHYSICSBLOCKER",
    "VISBLOCKER",
    "NOTASTEP",
}


def _physics_collision_material_summary(
    physics_model: bsp.WorldModelMesh,
    polygon_indices: Sequence[int],
) -> Dict[str, object]:
    surface_indices: set[int] = set()
    texture_names: set[str] = set()
    surface_flags: set[int] = set()
    texture_flags: set[int] = set()
    for polygon_index in polygon_indices:
        if not (0 <= int(polygon_index) < len(physics_model.polygons)):
            continue
        polygon = physics_model.polygons[int(polygon_index)]
        surface_index = int(polygon.surface_index)
        if 0 <= surface_index < len(physics_model.surfaces):
            surface_indices.add(surface_index)
            surface = physics_model.surfaces[surface_index]
            surface_flags.add(int(surface.flags))
            texture_flags.add(int(surface.texture_flags))
            texture_name = physics_model.texture_name_for(polygon)
            if texture_name:
                texture_names.add(str(texture_name))
        else:
            texture_names.add("<missing-surface>")
    return {
        "surface_count": len(surface_indices),
        "surface_indices": sorted(surface_indices),
        "texture_names": sorted(texture_names),
        "surface_flags": sorted(surface_flags),
        "texture_flags": sorted(texture_flags),
    }


def _physics_polygon_texture_name(
    physics_model: bsp.WorldModelMesh,
    polygon_index: int,
) -> str:
    if not (0 <= int(polygon_index) < len(physics_model.polygons)):
        return ""
    return str(physics_model.texture_name_for(physics_model.polygons[int(polygon_index)]) or "")


def _physics_polygon_surface_flags(
    physics_model: bsp.WorldModelMesh,
    polygon_index: int,
) -> int:
    if not (0 <= int(polygon_index) < len(physics_model.polygons)):
        return 0
    surface_index = int(physics_model.polygons[int(polygon_index)].surface_index)
    if not (0 <= surface_index < len(physics_model.surfaces)):
        return 0
    return int(physics_model.surfaces[surface_index].flags)


def _surface_flag_names(flags: int) -> List[str]:
    names = [name for bit, name in _SURFACE_FLAG_NAMES if int(flags) & int(bit)]
    known_mask = 0
    for bit, _name in _SURFACE_FLAG_NAMES:
        known_mask |= int(bit)
    unknown = int(flags) & ~known_mask
    if unknown:
        names.append(f"UNKNOWN_0x{unknown:X}")
    return names


def _physics_collision_semantic_role(
    physics_model: bsp.WorldModelMesh,
    polygon_index: int,
    *,
    matched_floor_polygons: Optional[set[int]] = None,
    lower_support_polygons: Optional[set[int]] = None,
) -> str:
    if matched_floor_polygons is not None and int(polygon_index) in matched_floor_polygons:
        return "matched_floor"
    if lower_support_polygons is not None and int(polygon_index) in lower_support_polygons:
        return "lower_support_floor"
    if not (0 <= int(polygon_index) < len(physics_model.polygons)):
        return "invalid"
    polygon = physics_model.polygons[int(polygon_index)]
    vertices = [
        physics_model.points[int(index)]
        for index in polygon.vertex_indices
        if 0 <= int(index) < len(physics_model.points)
    ]
    normal = _polygon_normal(vertices) if len(vertices) >= 3 else (0.0, 0.0, 0.0)
    flag_names = set(_surface_flag_names(_physics_polygon_surface_flags(physics_model, int(polygon_index))))
    if flag_names & _SPECIAL_COLLISION_SURFACE_FLAGS:
        if normal[1] > 0.35:
            return "special_floor_helper"
        if normal[1] < -0.35:
            return "special_ceiling_helper"
        return "special_non_floor_helper"
    if normal[1] > 0.35:
        return "floor"
    if normal[1] < -0.35:
        return "ceiling"
    return "side_wall"


def _physics_shared_helper_is_preservable(
    physics_model: bsp.WorldModelMesh,
    matched_floor_polygon_index: int,
    shared_polygon_index: int,
) -> bool:
    if int(shared_polygon_index) == int(matched_floor_polygon_index):
        return True
    role = _physics_collision_semantic_role(physics_model, int(shared_polygon_index))
    if role != "side_wall":
        return False
    return (
        _physics_polygon_surface_signature(physics_model, int(shared_polygon_index))
        == _physics_polygon_surface_signature(physics_model, int(matched_floor_polygon_index))
    )


def _physics_polygon_surface_signature(
    physics_model: bsp.WorldModelMesh,
    polygon_index: int,
) -> Tuple[str, int, int]:
    if not (0 <= int(polygon_index) < len(physics_model.polygons)):
        return ("", 0, 0)
    polygon = physics_model.polygons[int(polygon_index)]
    texture_name = str(physics_model.texture_name_for(polygon) or "")
    surface_index = int(polygon.surface_index)
    if not (0 <= surface_index < len(physics_model.surfaces)):
        return (texture_name, 0, 0)
    surface = physics_model.surfaces[surface_index]
    return (texture_name, int(surface.flags), int(surface.texture_flags))


def _physics_collision_role_summary(
    physics_model: bsp.WorldModelMesh,
    polygon_indices: Sequence[int],
) -> str:
    role_counts: Dict[str, int] = {}
    textures: set[str] = set()
    flag_names: set[str] = set()
    for polygon_index in polygon_indices:
        role = _physics_collision_semantic_role(physics_model, int(polygon_index))
        role_counts[role] = role_counts.get(role, 0) + 1
        texture = _physics_polygon_texture_name(physics_model, int(polygon_index))
        if texture:
            textures.add(texture)
        flag_names.update(_surface_flag_names(_physics_polygon_surface_flags(physics_model, int(polygon_index))))
    parts = []
    if role_counts:
        parts.append(f"roles={_format_role_counts(role_counts)}")
    if textures:
        parts.append(f"textures={_short_list_text(sorted(textures), limit=2)}")
    if flag_names:
        parts.append(f"flags={_short_list_text(sorted(flag_names), limit=4)}")
    return "; ".join(parts) if parts else "roles=none"


def _format_role_counts(role_counts: Dict[str, int]) -> str:
    if not role_counts:
        return "none"
    return ", ".join(
        f"{role}={int(count)}"
        for role, count in sorted(role_counts.items())
    )


def _unique_text(values: Sequence[str]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "")
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _point_box_overshoot(
    point: Vec3,
    min_box: Optional[Vec3],
    max_box: Optional[Vec3],
    *,
    epsilon: float = 0.0,
) -> float:
    return terrain_reconstruction.point_box_overshoot(point, min_box, max_box, epsilon=epsilon)


def _bounds_box_overshoot(
    bounds_min: Vec3,
    bounds_max: Vec3,
    limit_min: Optional[Vec3],
    limit_max: Optional[Vec3],
    *,
    epsilon: float = 0.0,
) -> float:
    return terrain_reconstruction.bounds_box_overshoot(
        bounds_min,
        bounds_max,
        limit_min,
        limit_max,
        epsilon=epsilon,
    )


def _short_list_text(values: Sequence[object], *, limit: int = 3) -> str:
    items = [str(value) for value in values]
    if not items:
        return "none"
    shown = items[:int(limit)]
    suffix = "" if len(items) <= int(limit) else f" (+{len(items) - int(limit)} more)"
    return ", ".join(shown) + suffix


def _moved_walkable_polygon_indices(
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
    *,
    move_epsilon: float,
) -> List[int]:
    result: List[int] = []
    for polygon_index, polygon in enumerate(source_model.polygons):
        try:
            vertices = [source_model.points[int(index)] for index in polygon.vertex_indices]
        except IndexError:
            continue
        if len(vertices) < 3 or _polygon_normal(vertices)[1] <= 0.35:
            continue
        moved = False
        for vertex_index in polygon.vertex_indices:
            if not (0 <= int(vertex_index) < len(source_model.points) and 0 <= int(vertex_index) < len(edited_model.points)):
                continue
            if _distance(source_model.points[int(vertex_index)], edited_model.points[int(vertex_index)]) > float(move_epsilon):
                moved = True
                break
        if moved:
            result.append(int(polygon_index))
    return result


def _terrain_polygon_source_edited_sample_pairs(
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
    polygon_index: int,
) -> List[Tuple[Vec3, Vec3]]:
    if not (0 <= int(polygon_index) < len(source_model.polygons)):
        return []
    source_polygon = source_model.polygons[int(polygon_index)]
    if not (0 <= int(polygon_index) < len(edited_model.polygons)):
        return []
    source_points: List[Vec3] = []
    edited_points: List[Vec3] = []
    for vertex_index in source_polygon.vertex_indices:
        if not (0 <= int(vertex_index) < len(source_model.points) and 0 <= int(vertex_index) < len(edited_model.points)):
            continue
        source_points.append(source_model.points[int(vertex_index)])
        edited_points.append(edited_model.points[int(vertex_index)])
    if not source_points:
        return []
    pairs = [(_point_average(source_points), _point_average(edited_points))]
    pairs.extend(zip(source_points, edited_points))
    return pairs


def _best_physics_floor_polygon_match(
    physics_model: bsp.WorldModelMesh,
    sample_pairs: Sequence[Tuple[Vec3, Vec3]],
    *,
    vertical_band: float,
) -> Optional[Tuple[int, float, float, Vec3, Vec3, float]]:
    best: Optional[Tuple[int, float, float, Vec3, Vec3, float]] = None
    for source_point, edited_point in sample_pairs:
        match = _physics_floor_polygon_at(
            physics_model,
            float(source_point[0]),
            float(source_point[2]),
            y_hint_min=float(source_point[1]) - float(vertical_band),
            y_hint_max=float(source_point[1]) + float(vertical_band),
        )
        if match is None:
            continue
        physics_polygon_index, floor_y = match
        source_delta = abs(float(source_point[1]) - float(floor_y))
        edited_delta = abs(float(edited_point[1]) - float(floor_y))
        if best is None or source_delta < best[1]:
            best = (
                int(physics_polygon_index),
                float(source_delta),
                float(edited_delta),
                source_point,
                edited_point,
                float(floor_y),
            )
    return best


def _physics_lower_fallback_floor_hits_for_match(
    physics_model: bsp.WorldModelMesh,
    sample_pairs: Sequence[Tuple[Vec3, Vec3]],
    *,
    matched_polygon_index: int,
    vertical_band: float,
    search_depth: float,
    min_gap: float,
) -> List[Tuple[int, float, float]]:
    if float(search_depth) <= 0.0:
        return []
    min_gap = max(0.0, float(min_gap))
    fallback_by_polygon: Dict[int, Tuple[int, float, float]] = {}
    for source_point, _edited_point in sample_pairs:
        top = _physics_floor_polygon_at(
            physics_model,
            float(source_point[0]),
            float(source_point[2]),
            y_hint_min=float(source_point[1]) - float(vertical_band),
            y_hint_max=float(source_point[1]) + float(vertical_band),
        )
        if top is None:
            continue
        top_polygon_index, top_floor_y = top
        if int(top_polygon_index) != int(matched_polygon_index):
            continue
        lower_hits = _physics_floor_polygon_hits_at(
            physics_model,
            float(source_point[0]),
            float(source_point[2]),
            y_hint_min=float(top_floor_y) - float(search_depth),
            y_hint_max=float(top_floor_y) - min_gap,
        )
        for lower_polygon_index, lower_floor_y in lower_hits:
            if int(lower_polygon_index) == int(matched_polygon_index):
                continue
            depth = float(top_floor_y) - float(lower_floor_y)
            if depth < min_gap or depth > float(search_depth):
                continue
            current = fallback_by_polygon.get(int(lower_polygon_index))
            if current is None or depth > current[2]:
                fallback_by_polygon[int(lower_polygon_index)] = (
                    int(lower_polygon_index),
                    float(lower_floor_y),
                    float(depth),
                )
    return sorted(
        fallback_by_polygon.values(),
        key=lambda item: (-float(item[2]), int(item[0])),
    )


def _physics_bsp_polygon_node_map(
    raw_record: bytes,
    physics_model: bsp.WorldModelMesh,
) -> Dict[int, set[int]]:
    layout = bsp_record_inspector._decode_world_bsp_layout(raw_record, physics_model)
    node_range = layout.section_ranges.get("nodes")
    if node_range is None:
        return {}
    start, end = int(node_range[0]), int(node_range[1])
    result: Dict[int, set[int]] = {}
    node_count = min(int(layout.node_count), max(0, (end - start) // 14))
    for node_index in range(node_count):
        polygon_index = struct.unpack_from("<I", raw_record, start + node_index * 14)[0]
        if 0 <= int(polygon_index) < len(physics_model.polygons):
            result.setdefault(int(polygon_index), set()).add(int(node_index))
    return result


def _bsp_world_for_models(
    source_bsp: bsp.BspWorld,
    models: Sequence[bsp.WorldModelMesh],
) -> bsp.BspWorld:
    return bsp.BspWorld(
        version=source_bsp.version,
        world_info=source_bsp.world_info,
        obj_pos=source_bsp.obj_pos,
        ren_pos=source_bsp.ren_pos,
        world_model_table_start=source_bsp.world_model_table_start,
        world_models=list(models),
        parse_warnings=list(getattr(source_bsp, "parse_warnings", []) or []),
    )


def _polygon_references_by_point(
    model: bsp.WorldModelMesh,
) -> Dict[int, set[int]]:
    result: Dict[int, set[int]] = {}
    for polygon_index, polygon in enumerate(model.polygons):
        for point_index in set(int(index) for index in polygon.vertex_indices):
            if 0 <= int(point_index) < len(model.points):
                result.setdefault(int(point_index), set()).add(int(polygon_index))
    return result


def _physics_bsp_node_classification_errors(
    raw_record: bytes,
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
    *,
    classification_epsilon: float = 0.5,
    max_examples: int = 8,
) -> List[str]:
    try:
        nodes = _ordinary_bsp_nodes(raw_record, source_model)
    except Exception as exc:
        return [f"PhysicsBSP node table could not be decoded ({exc})"]
    if not nodes:
        return ["PhysicsBSP node table is empty or undecoded"]
    errors: List[str] = []
    descendants: Dict[int, set[int]] = {}
    epsilon = float(classification_epsilon)
    source_centers = [
        polygon_center(source_model.points, polygon)
        for polygon in source_model.polygons
    ]
    edited_centers = [
        polygon_center(edited_model.points, polygon)
        for polygon in edited_model.polygons
    ]
    for node_index, (splitter_polygon_index, side0, side1) in enumerate(nodes):
        if not (0 <= int(splitter_polygon_index) < len(source_model.polygons)):
            continue
        source_normal, source_distance = plane_for_polygon(
            source_model.points,
            source_model.polygons[int(splitter_polygon_index)],
        )
        edited_normal, edited_distance = plane_for_polygon(
            edited_model.points,
            edited_model.polygons[int(splitter_polygon_index)],
        )
        for side_index, child_index in enumerate((side0, side1)):
            if child_index < 0 or child_index >= len(nodes):
                continue
            for polygon_index in _terrain_node_descendant_polygons(nodes, int(child_index), descendants):
                if not (0 <= int(polygon_index) < len(source_model.polygons)):
                    continue
                source_sign = _classification_sign(
                    _dot(source_normal, source_centers[int(polygon_index)]) - float(source_distance),
                    epsilon,
                )
                edited_sign = _classification_sign(
                    _dot(edited_normal, edited_centers[int(polygon_index)]) - float(edited_distance),
                    epsilon,
                )
                if source_sign != edited_sign:
                    errors.append(
                        f"PhysicsBSP node {node_index} side {side_index} polygon "
                        f"{polygon_index} classification changed {source_sign}->{edited_sign}"
                    )
                    if len(errors) >= int(max_examples):
                        return errors
    return errors


def _ordinary_bsp_nodes(
    raw_record: bytes,
    model: bsp.WorldModelMesh,
) -> List[Tuple[int, int, int]]:
    layout = bsp_record_inspector._decode_world_bsp_layout(raw_record, model)
    node_range = layout.section_ranges.get("nodes")
    if node_range is None:
        return []
    start, end = int(node_range[0]), int(node_range[1])
    node_count = min(int(layout.node_count), max(0, (end - start) // 14))
    nodes: List[Tuple[int, int, int]] = []
    for node_index in range(node_count):
        polygon_index, _leaf_index, side0_raw, side1_raw = struct.unpack_from(
            "<I H I I",
            raw_record,
            start + int(node_index) * 14,
        )
        nodes.append((
            int(polygon_index),
            _signed_u32(side0_raw),
            _signed_u32(side1_raw),
        ))
    return nodes


def _signed_u32(value: int) -> int:
    value = int(value)
    return value if value < 0x80000000 else value - 0x100000000


def _audit_terrain_lightmapped_polygon_edits(
    source_dat: Optional[bytes],
    source_bsp: Optional[bsp.BspWorld],
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
    max_examples: int = 4,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "terrain_lightmapped_polygon_count": 0,
        "terrain_lightmap_extra_data_polygon_count": 0,
        "affected_lightmapped_polygon_count": 0,
        "affected_lightmap_extra_data_polygon_count": 0,
        "affected_lightmap_pixel_count": 0,
        "max_affected_lightmap_width": 0,
        "max_affected_lightmap_height": 0,
        "examples": [],
    }
    if source_dat is None or source_bsp is None:
        return result
    raw = source_bsp.raw_model_bytes(source_dat, source_model)
    if raw is None:
        return result
    try:
        layout = bsp_record_inspector._decode_world_bsp_layout(raw, source_model)
    except Exception as exc:
        result["examples"] = [f"Terrain* polygon lightmap metadata could not be decoded ({exc})"]
        return result

    lightmap_sizes = list(getattr(layout, "polygon_lightmap_sizes", []) or [])
    terrain_lightmapped = 0
    terrain_extra = 0
    affected_lightmapped = 0
    affected_extra = 0
    affected_pixels = 0
    max_width = 0
    max_height = 0
    examples: List[str] = []
    for polygon_index, size in enumerate(lightmap_sizes):
        width, height, extra_count = (int(size[0]), int(size[1]), int(size[2]))
        has_lightmap = width > 0 or height > 0
        has_extra = extra_count > 0
        if has_lightmap:
            terrain_lightmapped += 1
        if has_extra:
            terrain_extra += 1
        if polygon_index >= len(source_model.polygons):
            continue
        polygon = source_model.polygons[int(polygon_index)]
        affected = any(
            0 <= int(vertex_index) < len(source_model.points)
            and 0 <= int(vertex_index) < len(edited_model.points)
            and _distance(
                source_model.points[int(vertex_index)],
                edited_model.points[int(vertex_index)],
            ) > float(move_epsilon)
            for vertex_index in polygon.vertex_indices
        )
        if not affected:
            continue
        if has_lightmap:
            affected_lightmapped += 1
            affected_pixels += max(0, width) * max(0, height)
            max_width = max(max_width, width)
            max_height = max(max_height, height)
            if len(examples) < int(max_examples):
                examples.append(
                    f"polygon {polygon_index} lightmap={width}x{height}, extra={extra_count}"
                )
        if has_extra:
            affected_extra += 1

    result.update({
        "terrain_lightmapped_polygon_count": int(terrain_lightmapped),
        "terrain_lightmap_extra_data_polygon_count": int(terrain_extra),
        "affected_lightmapped_polygon_count": int(affected_lightmapped),
        "affected_lightmap_extra_data_polygon_count": int(affected_extra),
        "affected_lightmap_pixel_count": int(affected_pixels),
        "max_affected_lightmap_width": int(max_width),
        "max_affected_lightmap_height": int(max_height),
        "examples": examples,
    })
    return result


def _audit_vis_bsp_vertex_partitions(
    source_dat: Optional[bytes],
    source_bsp: Optional[bsp.BspWorld],
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
    classification_epsilon: float = 0.5,
    max_examples: int = 4,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "available": False,
        "sample_count": 0,
        "changed_count": 0,
        "ambiguous_count": 0,
        "max_distance_delta": 0.0,
        "examples": [],
    }
    if source_dat is None or source_bsp is None:
        return result
    vis_model = source_bsp.model_by_name("VisBSP")
    if vis_model is None:
        return result
    raw = source_bsp.raw_model_bytes(source_dat, vis_model)
    if raw is None:
        return result
    try:
        nodes = _ordinary_bsp_nodes(raw, vis_model)
    except Exception as exc:
        result["examples"] = [f"VisBSP node table could not be decoded ({exc})"]
        return result
    root_index = _ordinary_bsp_root_index(nodes)
    if root_index is None:
        result["examples"] = ["VisBSP node tree root could not be identified"]
        return result

    result["available"] = True
    examples: List[str] = []
    changed_count = 0
    ambiguous_count = 0
    max_distance_delta = 0.0
    sample_count = 0
    for index, (source_point, edited_point) in enumerate(zip(source_model.points, edited_model.points)):
        if _distance(source_point, edited_point) <= float(move_epsilon):
            continue
        sample_count += 1
        source_path = _ordinary_bsp_point_partition_signature(
            nodes,
            vis_model,
            source_point,
            int(root_index),
            classification_epsilon=float(classification_epsilon),
        )
        edited_path = _ordinary_bsp_point_partition_signature(
            nodes,
            vis_model,
            edited_point,
            int(root_index),
            classification_epsilon=float(classification_epsilon),
        )
        max_distance_delta = max(
            max_distance_delta,
            _partition_path_distance_delta(source_path, edited_path),
        )
        if source_path["ambiguous"] or edited_path["ambiguous"]:
            ambiguous_count += 1
        if source_path["signature"] != edited_path["signature"]:
            changed_count += 1
            if len(examples) < int(max_examples):
                examples.append(
                    f"vertex {index} VisBSP partition changed "
                    f"{source_path['leaf']}->{edited_path['leaf']}"
                )
    result.update({
        "sample_count": int(sample_count),
        "changed_count": int(changed_count),
        "ambiguous_count": int(ambiguous_count),
        "max_distance_delta": float(max_distance_delta),
        "examples": examples,
    })
    return result


def _ordinary_bsp_root_index(nodes: Sequence[Tuple[int, int, int]]) -> Optional[int]:
    if not nodes:
        return None
    parents: set[int] = set()
    for _polygon_index, side0, side1 in nodes:
        for child in (int(side0), int(side1)):
            if 0 <= child < len(nodes):
                parents.add(int(child))
    roots = [index for index in range(len(nodes)) if index not in parents]
    if len(roots) == 1:
        return int(roots[0])
    return 0 if nodes else None


def _ordinary_bsp_point_partition_signature(
    nodes: Sequence[Tuple[int, int, int]],
    model: bsp.WorldModelMesh,
    point: Vec3,
    root_index: int,
    *,
    classification_epsilon: float = 0.5,
    max_depth: int = 4096,
) -> Dict[str, Any]:
    node_index = int(root_index)
    signature: List[Tuple[int, int]] = []
    distances: Dict[int, float] = {}
    seen: set[int] = set()
    ambiguous = False
    leaf = 0
    for _depth in range(int(max_depth)):
        if node_index < 0:
            leaf = int(node_index)
            break
        if node_index in seen or not (0 <= node_index < len(nodes)):
            ambiguous = True
            leaf = int(node_index)
            break
        seen.add(node_index)
        polygon_index, side0, side1 = nodes[node_index]
        if not (0 <= int(polygon_index) < len(model.polygons)):
            ambiguous = True
            leaf = int(node_index)
            break
        normal, distance = plane_for_polygon(
            model.points,
            model.polygons[int(polygon_index)],
        )
        signed_distance = _dot(normal, point) - float(distance)
        sign = _classification_sign(signed_distance, float(classification_epsilon))
        distances[int(node_index)] = float(signed_distance)
        signature.append((int(node_index), int(sign)))
        if sign == 0:
            ambiguous = True
            leaf = int(node_index)
            break
        node_index = int(side0) if sign < 0 else int(side1)
    else:
        ambiguous = True
        leaf = int(node_index)
    return {
        "signature": tuple(signature) + (("leaf", int(leaf)),),
        "leaf": int(leaf),
        "distances": distances,
        "ambiguous": bool(ambiguous),
    }


def _partition_path_distance_delta(
    source_path: Dict[str, Any],
    edited_path: Dict[str, Any],
) -> float:
    source_distances = source_path.get("distances", {}) or {}
    edited_distances = edited_path.get("distances", {}) or {}
    common = set(source_distances).intersection(set(edited_distances))
    if not common:
        return 0.0
    return max(
        abs(float(source_distances[index]) - float(edited_distances[index]))
        for index in common
    )


def _physics_bsp_node_block_cell_map(
    raw_record: bytes,
    physics_model: bsp.WorldModelMesh,
) -> Dict[int, set[int]]:
    layout = bsp_record_inspector._decode_world_bsp_layout(raw_record, physics_model)
    block_range = layout.section_ranges.get("physics_block_table")
    if block_range is None:
        return {}
    start, end = int(block_range[0]), int(block_range[1])
    if start + 36 > end:
        return {}
    dim_x, dim_y, dim_z = struct.unpack_from("<III", raw_record, start)
    cell_count = int(dim_x) * int(dim_y) * int(dim_z)
    if cell_count <= 0 or cell_count > 1_000_000:
        return {}
    cursor = start + 36
    result: Dict[int, set[int]] = {}
    for cell_index in range(cell_count):
        if cursor + 4 > end:
            break
        compact_count = struct.unpack_from("<H", raw_record, cursor)[0]
        cursor += 4
        entries_end = cursor + int(compact_count) * 6
        if entries_end > end:
            break
        for entry_index in range(int(compact_count)):
            source_node_index = struct.unpack_from("<H", raw_record, cursor + entry_index * 6)[0]
            if 0 <= int(source_node_index) < int(layout.node_count):
                result.setdefault(int(source_node_index), set()).add(int(cell_index))
        cursor = entries_end
    return result


def _build_collision_sync_edit(
    target_bsp: bsp.BspWorld,
    terrain_edits: Sequence[VertexEditedModel],
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
    influence_radius: float = DEFAULT_COLLISION_SYNC_RADIUS,
    vertical_band: float = DEFAULT_COLLISION_SYNC_VERTICAL_BAND,
) -> Optional[VertexEditedModel]:
    physics_model = target_bsp.model_by_name("PhysicsBSP")
    if physics_model is None:
        raise ValueError("terrain collision sync requires a PhysicsBSP model in the target level")

    samples: List[Tuple[Vec3, Vec3]] = []
    terrain_models: List[bsp.WorldModelMesh] = []
    for item in terrain_edits:
        if not _is_terrain_model(item.source_model):
            continue
        terrain_models.append(item.source_model)
        walkable_vertices = _likely_walkable_vertex_indices(item.source_model)
        for index, (old, new) in enumerate(zip(item.source_model.points, item.edited_model.points)):
            if index not in walkable_vertices:
                continue
            delta = (
                float(new[0]) - float(old[0]),
                float(new[1]) - float(old[1]),
                float(new[2]) - float(old[2]),
            )
            if math.sqrt(delta[0] * delta[0] + delta[1] * delta[1] + delta[2] * delta[2]) <= float(move_epsilon):
                continue
            samples.append((old, delta))

    if not samples:
        raise ValueError("terrain collision sync has no moved walkable terrain vertices to project")

    source_terrain_world = bsp.BspWorld(
        version=target_bsp.version,
        world_info=target_bsp.world_info,
        obj_pos=target_bsp.obj_pos,
        ren_pos=target_bsp.ren_pos,
        world_model_table_start=target_bsp.world_model_table_start,
        world_models=list(terrain_models),
        parse_warnings=list(target_bsp.parse_warnings),
    )
    floor_vertices = _likely_walkable_vertex_indices(physics_model)
    radius = float(influence_radius)
    radius_sq = radius * radius
    direct_vertex_deltas: Dict[int, List[Vec3]] = {}
    for sample_point, delta in samples:
        direct_indices = _physics_floor_polygon_vertices_at(
            physics_model,
            float(sample_point[0]),
            float(sample_point[2]),
            y_hint_min=float(sample_point[1]) - float(vertical_band),
            y_hint_max=float(sample_point[1]) + float(vertical_band),
        )
        for direct_index in direct_indices:
            direct_vertex_deltas.setdefault(int(direct_index), []).append(delta)

    edited = copy.deepcopy(physics_model)
    new_points = list(physics_model.points)
    moved_physics_vertices = 0

    candidate_vertices = sorted(floor_vertices | set(direct_vertex_deltas))
    for point_index in candidate_vertices:
        point = physics_model.points[point_index]
        direct_deltas = direct_vertex_deltas.get(point_index)
        if direct_deltas:
            count = float(len(direct_deltas))
            delta = (
                sum(value[0] for value in direct_deltas) / count,
                sum(value[1] for value in direct_deltas) / count,
                sum(value[2] for value in direct_deltas) / count,
            )
        else:
            terrain_y = _raycast_floor_y_in_band(
                source_terrain_world,
                float(point[0]),
                float(point[2]),
                y_hint_min=float(point[1]) - float(vertical_band),
                y_hint_max=float(point[1]) + float(vertical_band),
            )
            if terrain_y is None or abs(float(terrain_y) - float(point[1])) > float(vertical_band):
                continue
            weighted = [0.0, 0.0, 0.0]
            total_weight = 0.0
            for sample_point, sample_delta in samples:
                dx = float(point[0]) - float(sample_point[0])
                dz = float(point[2]) - float(sample_point[2])
                dist_sq = dx * dx + dz * dz
                if dist_sq > radius_sq:
                    continue
                weight = 1.0 / max(1.0, dist_sq)
                weighted[0] += float(sample_delta[0]) * weight
                weighted[1] += float(sample_delta[1]) * weight
                weighted[2] += float(sample_delta[2]) * weight
                total_weight += weight
            if total_weight <= 0.0:
                continue
            delta = (
                weighted[0] / total_weight,
                weighted[1] / total_weight,
                weighted[2] / total_weight,
            )
        if math.sqrt(delta[0] * delta[0] + delta[1] * delta[1] + delta[2] * delta[2]) <= float(move_epsilon):
            continue
        new_points[point_index] = (
            float(point[0]) + delta[0],
            float(point[1]) + delta[1],
            float(point[2]) + delta[2],
        )
        moved_physics_vertices += 1

    if moved_physics_vertices <= 0:
        return None

    edited.points = new_points
    edited.min_box, edited.max_box = _bounds(new_points)
    edited.raw_start = physics_model.raw_start
    edited.raw_end = physics_model.raw_end
    edited.next_world_item = physics_model.next_world_item
    edited.world_bsp_start = physics_model.world_bsp_start
    edited.world_bsp_end = physics_model.world_bsp_end
    return VertexEditedModel(
        name=physics_model.name,
        source_model=physics_model,
        edited_model=edited,
    )


def _likely_walkable_vertex_indices(model: bsp.WorldModelMesh) -> set[int]:
    return terrain_reconstruction.walkable_vertex_indices(model)


def _polygon_normal(vertices: Sequence[Vec3]) -> Vec3:
    return terrain_reconstruction.polygon_normal(vertices)


def _physics_floor_polygon_vertices_at(
    model: bsp.WorldModelMesh,
    x: float,
    z: float,
    *,
    y_hint_min: float,
    y_hint_max: float,
) -> set[int]:
    hits: List[Tuple[float, List[int]]] = []
    points = list(model.points)
    for polygon in model.polygons:
        indices = list(polygon.vertex_indices)
        if len(indices) < 3:
            continue
        polygon_vertices = [
            points[int(index)]
            for index in indices
            if 0 <= int(index) < len(points)
        ]
        if len(polygon_vertices) < 3 or _polygon_normal(polygon_vertices)[1] <= 0.35:
            continue
        for k in range(1, len(indices) - 1):
            tri_indices = [indices[0], indices[k], indices[k + 1]]
            try:
                tri = [points[index] for index in tri_indices]
            except IndexError:
                continue
            e1x = float(tri[1][0]) - float(tri[0][0])
            e1z = float(tri[1][2]) - float(tri[0][2])
            e2x = float(tri[2][0]) - float(tri[0][0])
            e2z = float(tri[2][2]) - float(tri[0][2])
            if e1z * e2x - e1x * e2z <= 1.0e-7:
                continue
            bary = _barycentric_xz((float(x), float(z)), tri)
            if bary is None:
                continue
            y = bary[0] * float(tri[0][1]) + bary[1] * float(tri[1][1]) + bary[2] * float(tri[2][1])
            if float(y_hint_min) <= y <= float(y_hint_max):
                hits.append((y, indices))
                break
    if not hits:
        return set()
    _hit_y, polygon_indices = max(hits, key=lambda item: item[0])
    return set(int(index) for index in polygon_indices)


def _physics_floor_polygon_at(
    model: bsp.WorldModelMesh,
    x: float,
    z: float,
    *,
    y_hint_min: float,
    y_hint_max: float,
) -> Optional[Tuple[int, float]]:
    hits = _physics_floor_polygon_hits_at(
        model,
        float(x),
        float(z),
        y_hint_min=float(y_hint_min),
        y_hint_max=float(y_hint_max),
    )
    if not hits:
        return None
    polygon_index, hit_y = max(hits, key=lambda item: item[1])
    return (int(polygon_index), float(hit_y))


def _physics_floor_polygon_hits_at(
    model: bsp.WorldModelMesh,
    x: float,
    z: float,
    *,
    y_hint_min: float,
    y_hint_max: float,
) -> List[Tuple[int, float]]:
    hits: List[Tuple[int, float]] = []
    points = list(model.points)
    for polygon_index, polygon in enumerate(model.polygons):
        indices = list(polygon.vertex_indices)
        if len(indices) < 3:
            continue
        polygon_vertices = [
            points[int(index)]
            for index in indices
            if 0 <= int(index) < len(points)
        ]
        if len(polygon_vertices) < 3 or _polygon_normal(polygon_vertices)[1] <= 0.35:
            continue
        for k in range(1, len(indices) - 1):
            tri_indices = [indices[0], indices[k], indices[k + 1]]
            try:
                tri = [points[index] for index in tri_indices]
            except IndexError:
                continue
            e1x = float(tri[1][0]) - float(tri[0][0])
            e1z = float(tri[1][2]) - float(tri[0][2])
            e2x = float(tri[2][0]) - float(tri[0][0])
            e2z = float(tri[2][2]) - float(tri[0][2])
            if e1z * e2x - e1x * e2z <= 1.0e-7:
                continue
            bary = _barycentric_xz((float(x), float(z)), tri)
            if bary is None:
                continue
            y = bary[0] * float(tri[0][1]) + bary[1] * float(tri[1][1]) + bary[2] * float(tri[2][1])
            if float(y_hint_min) <= y <= float(y_hint_max):
                hits.append((int(polygon_index), float(y)))
                break
    return sorted(hits, key=lambda item: (-float(item[1]), int(item[0])))


def _raycast_floor_y_in_band(
    world: bsp.BspWorld,
    x: float,
    z: float,
    *,
    y_hint_min: float,
    y_hint_max: float,
) -> Optional[float]:
    floor_y = bsp.raycast_floor_y(
        world,
        float(x),
        float(z),
        y_hint_min=float(y_hint_min),
        y_hint_max=float(y_hint_max),
    )
    if floor_y is None:
        return None
    pad = max(20.0, (float(y_hint_max) - float(y_hint_min)) * 0.2)
    if float(y_hint_min) - pad <= float(floor_y) <= float(y_hint_max) + pad:
        return float(floor_y)
    return None


def _barycentric_xz(point: Tuple[float, float], triangle: Sequence[Vec3]) -> Optional[Tuple[float, float, float]]:
    px, pz = point
    ax, az = float(triangle[0][0]), float(triangle[0][2])
    bx, bz = float(triangle[1][0]), float(triangle[1][2])
    cx, cz = float(triangle[2][0]), float(triangle[2][2])
    v0x = bx - ax
    v0z = bz - az
    v1x = cx - ax
    v1z = cz - az
    v2x = px - ax
    v2z = pz - az
    den = v0x * v1z - v1x * v0z
    if abs(den) <= 1.0e-7:
        return None
    u = (v2x * v1z - v1x * v2z) / den
    v = (v0x * v2z - v2x * v0z) / den
    w = 1.0 - u - v
    eps = 1.0e-6
    if u < -eps or v < -eps or w < -eps:
        return None
    return (w, u, v)


def _physics_bsp_world_for_audit(
    source_bsp: Optional[bsp.BspWorld],
    plans: Sequence[VertexEditPlan],
) -> Optional[bsp.BspWorld]:
    physics_model: Optional[bsp.WorldModelMesh] = None
    for plan in plans or []:
        for item in plan.models:
            if _is_physics_bsp_model(item.edited_model):
                physics_model = item.edited_model
                break
        if physics_model is not None:
            break
    if physics_model is None and source_bsp is not None:
        physics_model = source_bsp.model_by_name("PhysicsBSP")
    if physics_model is None:
        return None
    if source_bsp is not None:
        version = source_bsp.version
        world_info = source_bsp.world_info
        obj_pos = source_bsp.obj_pos
        ren_pos = source_bsp.ren_pos
        table_start = source_bsp.world_model_table_start
        parse_warnings = list(source_bsp.parse_warnings)
    else:
        version = bsp.DAT_VERSION_V66
        world_info = ""
        obj_pos = ren_pos = table_start = 0
        parse_warnings = []
    return bsp.BspWorld(
        version=version,
        world_info=world_info,
        obj_pos=obj_pos,
        ren_pos=ren_pos,
        world_model_table_start=table_start,
        world_models=[physics_model],
        parse_warnings=parse_warnings,
    )


def _plan_has_physics_bsp_edit(plans: Sequence[VertexEditPlan]) -> bool:
    return any(
        _is_physics_bsp_model(item.edited_model)
        for plan in plans or []
        for item in plan.models
    )


def _limited_samples(items: Sequence[Tuple[Vec3, Vec3]], limit: int) -> List[Tuple[Vec3, Vec3]]:
    if limit <= 0 or len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[0]]
    step = (len(items) - 1) / float(limit - 1)
    result: List[Tuple[Vec3, Vec3]] = []
    seen = set()
    for sample_index in range(limit):
        index = int(round(sample_index * step))
        if index in seen:
            continue
        seen.add(index)
        result.append(items[index])
    return result


def _snapped_edited_model(
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
) -> bsp.WorldModelMesh:
    if len(source_model.points) != len(edited_model.points):
        return edited_model
    snapped = copy.deepcopy(edited_model)
    snapped.points = _snap_points_to_source_epsilon(
        source_model.points,
        edited_model.points,
        move_epsilon=move_epsilon,
    )
    if snapped.points:
        snapped.min_box, snapped.max_box = _bounds(snapped.points)
    return snapped


def _snap_points_to_source_epsilon(
    source_points: Sequence[Vec3],
    edited_points: Sequence[Vec3],
    *,
    move_epsilon: float = DEFAULT_MOVE_EPSILON,
) -> List[Vec3]:
    snapped: List[Vec3] = []
    for old, new in zip(source_points, edited_points):
        snapped.append(
            tuple(float(value) for value in old)
            if _distance(old, new) <= float(move_epsilon)
            else tuple(float(value) for value in new)
        )
    return snapped


def _bounds(points: Sequence[Vec3]) -> Tuple[Vec3, Vec3]:
    return terrain_reconstruction.vec3_bounds(points)


def _expanded_bounds_if_needed(
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
) -> Tuple[Vec3, Vec3]:
    return terrain_reconstruction.expanded_vec3_bounds(
        source_model.min_box,
        source_model.max_box,
        edited_model.points,
    )


def _distance(a: Vec3, b: Vec3) -> float:
    return terrain_reconstruction.vec3_distance(a, b)


def _dot(a: Vec3, b: Vec3) -> float:
    return terrain_reconstruction.vec3_dot(a, b)
