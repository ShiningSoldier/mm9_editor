"""Terrain polygon cleanup helpers for DAT -> ED reconstruction."""

from __future__ import annotations

import math
import heapq
from collections import defaultdict, deque
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from features.dat_editing import terrain_semantics


Vec3 = Tuple[float, float, float]
XZPoint = Tuple[float, float]


class TerrainSupportItem(NamedTuple):
    polygon_index: int
    polygon: object
    indices: Tuple[int, ...]
    points: Tuple[Vec3, ...]
    center: Vec3
    bounds: Tuple[float, float, float, float]


class TerrainSupportPlacement(NamedTuple):
    center: Vec3
    top_y: float


class TerrainCoverageItem(NamedTuple):
    polygon_index: int
    bounds_min: Vec3
    bounds_max: Vec3
    texture_name: str
    xz_points: Tuple[XZPoint, ...]


class GeneratedTerrainCoverageItem(NamedTuple):
    min_x: float
    max_x: float
    min_z: float
    max_z: float
    texture_name: str
    xz_points: Tuple[XZPoint, ...]


PHYSICS_SHELL_COVERAGE_ROLES: Tuple[str, ...] = (
    "floor",
    "ceiling",
    "side_wall",
    "helper/special",
    "degenerate",
)


class PhysicsShellCandidate(NamedTuple):
    polygon_index: int
    polygon: object
    indices: Tuple[int, ...]
    points: Tuple[Vec3, ...]
    area: float
    role: str
    generated_face_count: int


_PHYSICS_SHELL_MIN_POLYGON_AREA = 0.25
_PHYSICS_SHELL_MIN_EDGE_LENGTH = 0.05
_PHYSICS_SHELL_MAX_PLANE_DEVIATION = 0.01
_PHYSICS_SHELL_MIN_EXTRUSION_THICKNESS = 1.0


class TerrainCutoutModelInfo(NamedTuple):
    model_index: int
    name: str
    bounds_min: Vec3
    bounds_max: Vec3
    footprint_area: float


def canonical_terrain_polygon_indices(indices: Sequence[int]) -> Tuple[int, ...]:
    """Return one boundary loop from a DAT Terrain* polygon index list.

    Some MM9 Terrain0 polygons carry repeated coarse/refined boundary vertices.
    Prefer a unique suffix when the DAT list ends with a complete boundary loop;
    otherwise keep the last occurrence of each index in source order.
    """
    raw = tuple(int(index) for index in indices)
    if len(set(raw)) == len(raw):
        return raw
    unique = set(raw)
    for start in range(len(raw)):
        suffix = raw[start:]
        if len(suffix) == len(unique) and len(set(suffix)) == len(suffix) and set(suffix) == unique:
            return tuple(suffix)

    seen = set()
    reversed_result: List[int] = []
    for index in reversed(raw):
        if index not in seen:
            seen.add(index)
            reversed_result.append(index)
    return tuple(reversed(reversed_result))


def simplify_collinear_terrain_polygon_indices(
    indices: Sequence[int],
    points: Sequence[object],
) -> Tuple[int, ...]:
    """Remove duplicate and collinear boundary vertices from a Terrain* polygon."""
    result = [int(index) for index in indices]
    changed = True
    while changed and len(result) > 3:
        changed = False
        for offset, index in enumerate(tuple(result)):
            prev_point = _finite_vec3(points[result[(offset - 1) % len(result)]])
            current_point = _finite_vec3(points[index])
            next_point = _finite_vec3(points[result[(offset + 1) % len(result)]])
            if _point_distance_sq(prev_point, current_point) <= 1.0e-8:
                del result[offset]
                changed = True
                break
            if _point_distance_sq(current_point, next_point) <= 1.0e-8:
                del result[offset]
                changed = True
                break
            if _cross_length(prev_point, current_point, next_point) <= 1.0e-3:
                del result[offset]
                changed = True
                break
    return tuple(result)


def terrain_support_items(terrain: object) -> Tuple[TerrainSupportItem, ...]:
    """Build Terrain* support polygon items from a parsed DAT world model."""
    terrain_points = getattr(terrain, "points", ()) or ()
    result: List[TerrainSupportItem] = []
    for polygon_index, polygon in enumerate(getattr(terrain, "polygons", ()) or ()):
        raw_indices = tuple(int(index) for index in getattr(polygon, "vertex_indices", ()) or ())
        indices = canonical_terrain_polygon_indices(raw_indices)
        indices = simplify_collinear_terrain_polygon_indices(indices, terrain_points)
        if not (3 <= len(indices) <= 64):
            continue
        points = tuple(_finite_vec3(terrain_points[index]) for index in indices)
        if not points:
            continue
        center = (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
            sum(point[2] for point in points) / len(points),
        )
        poly_min_x = min(point[0] for point in points)
        poly_max_x = max(point[0] for point in points)
        poly_min_z = min(point[2] for point in points)
        poly_max_z = max(point[2] for point in points)
        result.append(TerrainSupportItem(
            int(polygon_index),
            polygon,
            tuple(indices),
            points,
            center,
            (poly_min_x, poly_max_x, poly_min_z, poly_max_z),
        ))
    return tuple(result)


def terrain_coverage_items(
    terrain: object,
    *,
    ignored_textures: Sequence[str] = (),
    require_texture: bool = True,
) -> Tuple[TerrainCoverageItem, ...]:
    """Build cleaned Terrain* polygon footprints for source/cutout coverage reports."""
    ignored = {str(item).lower() for item in ignored_textures}
    points = list(getattr(terrain, "points", ()) or ())
    result: List[TerrainCoverageItem] = []

    for polygon_index, polygon in enumerate(getattr(terrain, "polygons", ()) or ()):
        texture_name = dat_polygon_texture_name(terrain, polygon)
        texture_key = texture_name.lower()
        if texture_key in ignored or (require_texture and not texture_name):
            continue
        raw_indices = tuple(int(index) for index in getattr(polygon, "vertex_indices", ()) or ())
        if len(raw_indices) < 3:
            continue
        try:
            indices = canonical_terrain_polygon_indices(raw_indices)
            indices = simplify_collinear_terrain_polygon_indices(indices, points)
        except Exception:
            indices = unique_polygon_indices(raw_indices)
        if len(indices) < 3:
            continue
        try:
            poly_points = tuple(_finite_vec3(points[int(index)]) for index in indices)
        except Exception:
            continue
        if len(poly_points) < 3:
            continue
        bounds_min = tuple(min(point[index] for point in poly_points) for index in range(3))
        bounds_max = tuple(max(point[index] for point in poly_points) for index in range(3))
        if bounds_max[0] - bounds_min[0] <= 1.0e-6 or bounds_max[2] - bounds_min[2] <= 1.0e-6:
            continue
        xz_points = tuple((float(point[0]), float(point[2])) for point in poly_points)
        result.append(TerrainCoverageItem(
            polygon_index=int(polygon_index),
            bounds_min=bounds_min,  # type: ignore[arg-type]
            bounds_max=bounds_max,  # type: ignore[arg-type]
            texture_name=texture_name,
            xz_points=xz_points,
        ))
    return tuple(result)


def generated_terrain_coverage_items(
    scene: object,
    *,
    source_texture_names: Sequence[str],
    ignored_textures: Sequence[str] = (),
) -> Tuple[GeneratedTerrainCoverageItem, ...]:
    """Build generated ED terrain-top coverage footprints filtered by source textures."""
    source_textures = {str(item).lower() for item in source_texture_names}
    ignored = {str(item).lower() for item in ignored_textures}
    result: List[GeneratedTerrainCoverageItem] = []
    for model in getattr(scene, "models", ()) or ():
        points = list(getattr(model, "points", ()) or ())
        if not points:
            continue
        for face in getattr(model, "faces", ()) or ():
            texture_name = str(getattr(face, "material_name", "") or "")
            texture_key = texture_name.lower()
            if not texture_name or texture_key in ignored or texture_key not in source_textures:
                continue
            indices = tuple(int(index) for index in getattr(face, "vertex_indices", ()) or ())
            if len(indices) < 3:
                continue
            try:
                poly_points = tuple(_finite_vec3(points[int(index)]) for index in indices)
            except Exception:
                continue
            if len(poly_points) < 3:
                continue
            xz_points = tuple((float(point[0]), float(point[2])) for point in poly_points)
            min_x, max_x, min_z, max_z = xz_polygon_bounds(xz_points)
            if max_x - min_x <= 1.0e-6 or max_z - min_z <= 1.0e-6:
                continue
            result.append(GeneratedTerrainCoverageItem(
                min_x=float(min_x),
                max_x=float(max_x),
                min_z=float(min_z),
                max_z=float(max_z),
                texture_name=texture_name,
                xz_points=xz_points,
            ))
    return tuple(result)


def generated_coverage_point_hit(
    x: float,
    z: float,
    generated_items: Sequence[GeneratedTerrainCoverageItem],
) -> bool:
    """Return whether a sampled X/Z point lands on generated ED terrain coverage."""
    for item in generated_items:
        if (
            float(x) < item.min_x - 1.0e-5
            or float(x) > item.max_x + 1.0e-5
            or float(z) < item.min_z - 1.0e-5
            or float(z) > item.max_z + 1.0e-5
        ):
            continue
        if point_in_xz_polygon(float(x), float(z), item.xz_points):
            return True
    return False


def terrain_coverage_point_texture_hit(
    x: float,
    z: float,
    terrain_items: Sequence[TerrainCoverageItem],
) -> Optional[str]:
    """Return the texture under a sampled X/Z point, if covered by Terrain*."""
    for item in terrain_items:
        if (
            float(x) < item.bounds_min[0] - 1.0e-5
            or float(x) > item.bounds_max[0] + 1.0e-5
            or float(z) < item.bounds_min[2] - 1.0e-5
            or float(z) > item.bounds_max[2] + 1.0e-5
        ):
            continue
        if point_in_xz_polygon(float(x), float(z), item.xz_points):
            return item.texture_name
    return None


def dat_polygon_texture_name(model: object, polygon: object) -> str:
    surfaces = getattr(model, "surfaces", ()) or ()
    textures = getattr(model, "texture_names", ()) or ()
    try:
        surface = surfaces[int(getattr(polygon, "surface_index"))]
        texture_index = int(getattr(surface, "texture_index"))
        return str(textures[texture_index])
    except Exception:
        return ""


def physics_shell_source_polygon_role(
    model: object,
    polygon: object,
    model_points: Sequence[object],
) -> str:
    """Classify a PhysicsBSP polygon by reconstruction value."""
    indices = _quality_checked_physics_shell_polygon_indices(
        tuple(int(index) for index in (getattr(polygon, "vertex_indices", ()) or ())),
        model_points,
    )
    if not indices:
        return "degenerate"
    polygon_points = tuple(_finite_vec3(model_points[index]) for index in indices)
    return _physics_shell_role_for_valid_polygon(model, polygon, polygon_points)


def physics_shell_source_polygon_roles(model: object) -> Dict[int, str]:
    """Return role labels for every source PhysicsBSP polygon."""
    points = tuple(_finite_vec3(point) for point in (getattr(model, "points", ()) or ()))
    return {
        int(polygon_index): physics_shell_source_polygon_role(model, polygon, points)
        for polygon_index, polygon in enumerate(getattr(model, "polygons", ()) or ())
    }


def physics_shell_candidates(model: object) -> Tuple[PhysicsShellCandidate, ...]:
    """Build valid PhysicsBSP source polygons that can be slabbed into ED brushes."""
    points = tuple(_finite_vec3(point) for point in (getattr(model, "points", ()) or ()))
    result: List[PhysicsShellCandidate] = []
    for polygon_index, polygon in enumerate(getattr(model, "polygons", ()) or ()):
        indices = _quality_checked_physics_shell_polygon_indices(
            tuple(int(index) for index in (getattr(polygon, "vertex_indices", ()) or ())),
            points,
        )
        if not indices:
            continue
        polygon_points = tuple(points[index] for index in indices)
        area = polygon_area(polygon_points)
        role = _physics_shell_role_for_valid_polygon(model, polygon, polygon_points)
        if role == "degenerate":
            continue
        result.append(
            PhysicsShellCandidate(
                polygon_index=int(polygon_index),
                polygon=polygon,
                indices=indices,
                points=polygon_points,
                area=float(area),
                role=role,
                generated_face_count=len(indices) + 2,
            )
        )
    return tuple(result)


def physics_shell_slab_quality_ok(
    points: Sequence[object],
    *,
    thickness: float = 4.0,
) -> bool:
    """Return whether a polygon can form stable front/back slab planes."""
    polygon_points = tuple(_finite_vec3(point) for point in points)
    return _physics_shell_polygon_quality_ok(polygon_points, thickness=thickness)


def _quality_checked_physics_shell_polygon_indices(
    indices: Sequence[int],
    model_points: Sequence[object],
) -> Optional[Tuple[int, ...]]:
    if not (3 <= len(indices) <= 64):
        return None
    if any(index < 0 or index >= len(model_points) for index in indices):
        return None

    simplified = _simplify_physics_shell_quality_indices(indices, model_points)
    if not (3 <= len(simplified) <= 64):
        return None
    polygon_points = tuple(_finite_vec3(model_points[index]) for index in simplified)
    if not _physics_shell_polygon_quality_ok(polygon_points, thickness=4.0):
        return None
    return tuple(simplified)


def _simplify_physics_shell_quality_indices(
    indices: Sequence[int],
    model_points: Sequence[object],
) -> Tuple[int, ...]:
    result = [int(index) for index in indices]
    min_edge_sq = _PHYSICS_SHELL_MIN_EDGE_LENGTH * _PHYSICS_SHELL_MIN_EDGE_LENGTH
    changed = True
    while changed and len(result) > 3:
        changed = False
        for offset, index in enumerate(tuple(result)):
            next_offset = (offset + 1) % len(result)
            current_point = _finite_vec3(model_points[index])
            next_point = _finite_vec3(model_points[result[next_offset]])
            if vec3_distance_sq(current_point, next_point) <= min_edge_sq:
                del result[next_offset]
                changed = True
                break
    return tuple(result)


def _physics_shell_polygon_quality_ok(
    points: Sequence[Vec3],
    *,
    thickness: float,
) -> bool:
    polygon_points = tuple(_finite_vec3(point) for point in points)
    if len(polygon_points) < 3:
        return False

    area = polygon_area(polygon_points)
    if not math.isfinite(area) or area < _PHYSICS_SHELL_MIN_POLYGON_AREA:
        return False

    min_edge_sq = _PHYSICS_SHELL_MIN_EDGE_LENGTH * _PHYSICS_SHELL_MIN_EDGE_LENGTH
    for point, next_point in zip(polygon_points, polygon_points[1:] + polygon_points[:1]):
        if vec3_distance_sq(point, next_point) < min_edge_sq:
            return False

    normal, distance = polygon_plane(polygon_points, tuple(range(len(polygon_points))))
    if not all(math.isfinite(value) for value in normal) or not math.isfinite(distance):
        return False
    max_plane_delta = max(
        abs(vec3_dot(normal, point) - distance)
        for point in polygon_points
    )
    if max_plane_delta > _PHYSICS_SHELL_MAX_PLANE_DEVIATION:
        return False

    safe_thickness = max(0.0, float(thickness))
    if safe_thickness < _PHYSICS_SHELL_MIN_EXTRUSION_THICKNESS:
        return False
    for point in polygon_points:
        back_point = (
            point[0] - normal[0] * safe_thickness,
            point[1] - normal[1] * safe_thickness,
            point[2] - normal[2] * safe_thickness,
        )
        if not all(math.isfinite(value) for value in back_point):
            return False
        separation = abs(vec3_dot(normal, point) - vec3_dot(normal, back_point))
        if separation < _PHYSICS_SHELL_MIN_EXTRUSION_THICKNESS:
            return False
    return True


def balanced_physics_shell_candidates(
    candidates: Sequence[PhysicsShellCandidate],
    limit: int,
) -> Tuple[PhysicsShellCandidate, ...]:
    """Select PhysicsBSP shell polygons with side-wall coverage before helper fill."""
    by_role, structural_candidates, helper_candidates = _physics_shell_candidate_sort_groups(candidates)
    return _balanced_physics_shell_candidates_from_sorted(
        by_role,
        structural_candidates,
        helper_candidates,
        limit,
    )


def budgeted_balanced_physics_shell_source_polygon_count(
    candidates: Sequence[PhysicsShellCandidate],
    *,
    requested_source_polygon_count: int,
    generated_polygon_budget: int,
) -> int:
    """Return the largest balanced source count that fits a generated face budget."""
    requested = max(0, int(requested_source_polygon_count))
    budget = max(0, int(generated_polygon_budget))
    if requested <= 0 or budget <= 0:
        return 0
    by_role, structural_candidates, helper_candidates = _physics_shell_candidate_sort_groups(candidates)
    candidate_limit = min(
        requested,
        sum(len(role_candidates) for role_candidates in by_role.values()),
    )

    fitted_count = 0
    for candidate_count in range(1, candidate_limit + 1):
        selected = _balanced_physics_shell_candidates_from_sorted(
            by_role,
            structural_candidates,
            helper_candidates,
            candidate_count,
        )
        generated_count = sum(candidate.generated_face_count for candidate in selected)
        if generated_count > budget:
            break
        fitted_count = candidate_count
    return fitted_count


def _physics_shell_candidate_sort_groups(
    candidates: Sequence[PhysicsShellCandidate],
) -> Tuple[
    Dict[str, Tuple[PhysicsShellCandidate, ...]],
    Tuple[PhysicsShellCandidate, ...],
    Tuple[PhysicsShellCandidate, ...],
]:
    ranked_candidates = _connected_spatial_physics_shell_candidate_order(candidates)
    by_role_lists: Dict[str, List[PhysicsShellCandidate]] = defaultdict(list)
    for candidate in ranked_candidates:
        if candidate.role == "degenerate":
            continue
        by_role_lists[str(candidate.role)].append(candidate)
    by_role: Dict[str, Tuple[PhysicsShellCandidate, ...]] = {}
    for role, role_candidates in by_role_lists.items():
        by_role[role] = tuple(role_candidates)

    structural_roles = ("side_wall", "floor", "ceiling")
    structural_candidates = tuple(
        (
            candidate
            for candidate in ranked_candidates
            if candidate.role in structural_roles
        ),
    )
    helper_candidates = by_role.get("helper/special", ())
    return by_role, structural_candidates, helper_candidates


def _connected_spatial_physics_shell_candidate_order(
    candidates: Sequence[PhysicsShellCandidate],
) -> Tuple[PhysicsShellCandidate, ...]:
    valid_candidates = tuple(candidate for candidate in candidates if candidate.role != "degenerate")
    if not valid_candidates:
        return ()

    components = _physics_shell_candidate_components(valid_candidates)
    ordered_components = sorted(
        components,
        key=lambda component: (
            not any(candidate.role == "side_wall" for candidate in component),
            -_physics_shell_component_score(component),
            min(candidate.polygon_index for candidate in component),
        ),
    )
    result: List[PhysicsShellCandidate] = []
    for component in ordered_components:
        result.extend(_spatial_physics_shell_component_order(component))
    return tuple(result)


def _physics_shell_candidate_components(
    candidates: Sequence[PhysicsShellCandidate],
) -> Tuple[Tuple[PhysicsShellCandidate, ...], ...]:
    vertex_to_offsets: Dict[int, List[int]] = defaultdict(list)
    for offset, candidate in enumerate(candidates):
        for index in set(candidate.indices):
            vertex_to_offsets[int(index)].append(offset)

    visited = [False] * len(candidates)
    components: List[Tuple[PhysicsShellCandidate, ...]] = []
    for start_offset, start_candidate in enumerate(candidates):
        if visited[start_offset]:
            continue
        stack = [start_offset]
        visited[start_offset] = True
        component: List[PhysicsShellCandidate] = []
        while stack:
            offset = stack.pop()
            candidate = candidates[offset]
            component.append(candidate)
            for index in set(candidate.indices):
                for neighbor_offset in vertex_to_offsets.get(int(index), ()):
                    if visited[neighbor_offset]:
                        continue
                    visited[neighbor_offset] = True
                    stack.append(neighbor_offset)
        components.append(tuple(component))
    return tuple(components)


def _spatial_physics_shell_component_order(
    component: Sequence[PhysicsShellCandidate],
) -> Tuple[PhysicsShellCandidate, ...]:
    vertex_to_candidates: Dict[int, List[PhysicsShellCandidate]] = defaultdict(list)
    remaining: Dict[int, PhysicsShellCandidate] = {}
    for candidate in component:
        remaining[int(candidate.polygon_index)] = candidate
        for index in set(candidate.indices):
            vertex_to_candidates[int(index)].append(candidate)

    result: List[PhysicsShellCandidate] = []
    frontier: Dict[int, PhysicsShellCandidate] = {}

    while remaining:
        if frontier:
            candidate = min(frontier.values(), key=_physics_shell_candidate_spatial_key)
        else:
            candidate = min(remaining.values(), key=_physics_shell_candidate_spatial_key)

        remaining.pop(candidate.polygon_index, None)
        frontier.pop(candidate.polygon_index, None)
        result.append(candidate)

        for index in set(candidate.indices):
            for neighbor in vertex_to_candidates.get(int(index), ()):
                if neighbor.polygon_index in remaining:
                    frontier[int(neighbor.polygon_index)] = neighbor

    return tuple(result)


def _physics_shell_component_score(component: Sequence[PhysicsShellCandidate]) -> float:
    side_wall_area = 0.0
    support_area = 0.0
    helper_area = 0.0
    for candidate in component:
        if candidate.role == "side_wall":
            side_wall_area += float(candidate.area)
        elif candidate.role in {"floor", "ceiling"}:
            support_area += float(candidate.area)
        elif candidate.role == "helper/special":
            helper_area += float(candidate.area)
    return side_wall_area * 8.0 + support_area * 0.25 + helper_area * 0.01


def _physics_shell_candidate_spatial_key(candidate: PhysicsShellCandidate) -> Tuple[int, float, int]:
    role_priority = {
        "side_wall": 0,
        "floor": 1,
        "ceiling": 1,
        "helper/special": 3,
    }.get(str(candidate.role), 2)
    return role_priority, -float(candidate.area), int(candidate.polygon_index)


def _balanced_physics_shell_candidates_from_sorted(
    by_role: Dict[str, Tuple[PhysicsShellCandidate, ...]],
    structural_candidates: Sequence[PhysicsShellCandidate],
    helper_candidates: Sequence[PhysicsShellCandidate],
    limit: int,
) -> Tuple[PhysicsShellCandidate, ...]:
    safe_limit = max(0, int(limit))
    if safe_limit <= 0:
        return ()

    selected: List[PhysicsShellCandidate] = []
    selected_indices = set()

    def add_from_role(role: str, count: int) -> None:
        remaining = max(0, int(count))
        if remaining <= 0:
            return
        for candidate in by_role.get(role, ()):
            if len(selected) >= safe_limit or remaining <= 0:
                return
            if candidate.polygon_index in selected_indices:
                continue
            selected_indices.add(candidate.polygon_index)
            selected.append(candidate)
            remaining -= 1

    side_wall_quota = max(1, (safe_limit + 1) // 2)
    floor_quota = max(1 if safe_limit >= 2 else 0, safe_limit // 5)
    ceiling_quota = max(1 if safe_limit >= 4 else 0, safe_limit // 10)

    add_from_role("side_wall", side_wall_quota)
    add_from_role("floor", floor_quota)
    add_from_role("ceiling", ceiling_quota)

    for candidate in tuple(structural_candidates) + tuple(helper_candidates):
        if len(selected) >= safe_limit:
            break
        if candidate.polygon_index in selected_indices:
            continue
        selected_indices.add(candidate.polygon_index)
        selected.append(candidate)

    return tuple(selected)


def _physics_shell_role_for_valid_polygon(
    model: object,
    polygon: object,
    polygon_points: Sequence[Vec3],
) -> str:
    texture_name = dat_polygon_texture_name(model, polygon)
    if terrain_semantics.helper_texture_role(texture_name):
        return "helper/special"

    normal, _dist = polygon_plane(polygon_points, tuple(range(len(polygon_points))))
    if not all(math.isfinite(value) for value in normal):
        return "degenerate"
    if normal[1] > 0.45:
        return "floor"
    if normal[1] < -0.45:
        return "ceiling"
    return "side_wall"


def unique_polygon_indices(indices: Sequence[int]) -> Tuple[int, ...]:
    seen = set()
    result: List[int] = []
    for index in indices:
        safe_index = int(index)
        if safe_index in seen:
            continue
        seen.add(safe_index)
        result.append(safe_index)
    return tuple(result)


def terrain_cutout_model_infos(
    models: Sequence[object],
    *,
    include_skyboxes: bool,
    min_model_footprint_area: float,
) -> Tuple[TerrainCutoutModelInfo, ...]:
    """Return non-terrain world-model footprints that can explain Terrain0 cutouts."""
    result: List[TerrainCutoutModelInfo] = []
    for model_index, model in enumerate(models):
        name = str(getattr(model, "name", "") or f"WorldModel{model_index}")
        if terrain_cutout_blocked_model_name(name, include_skyboxes=include_skyboxes):
            continue
        try:
            is_skybox = bool(getattr(model, "is_skybox", lambda: False)())
        except Exception:
            is_skybox = False
        if is_skybox and not include_skyboxes:
            continue
        points = [_finite_vec3(point) for point in getattr(model, "points", ()) or ()]
        if not points or not (getattr(model, "polygons", ()) or ()):
            continue
        bounds_min = tuple(min(point[index] for point in points) for index in range(3))
        bounds_max = tuple(max(point[index] for point in points) for index in range(3))
        width = bounds_max[0] - bounds_min[0]
        depth = bounds_max[2] - bounds_min[2]
        area = max(0.0, width * depth)
        if area < float(min_model_footprint_area):
            continue
        result.append(TerrainCutoutModelInfo(
            model_index=int(model_index),
            name=name,
            bounds_min=bounds_min,  # type: ignore[arg-type]
            bounds_max=bounds_max,  # type: ignore[arg-type]
            footprint_area=float(area),
        ))
    return tuple(result)


def terrain_cutout_blocked_model_name(name: str, *, include_skyboxes: bool) -> bool:
    lowered = str(name or "").strip().lower()
    if not lowered:
        return True
    if (
        terrain_semantics.is_terrain_name(name)
        or terrain_semantics.is_physics_bsp_name(name)
        or terrain_semantics.is_vis_bsp_name(name)
    ):
        return True
    blocked_prefixes = (
        "perceptionbrush",
        "aitrk",
        "ocean",
        "bluewater",
        "water",
    )
    if any(lowered.startswith(prefix) for prefix in blocked_prefixes):
        return True
    if not include_skyboxes and (
        lowered.startswith("skybox")
        or lowered.startswith("tod_sky")
        or lowered.startswith("tod sky")
    ):
        return True
    return False


def terrain_cutout_model_clusters(
    model_infos: Sequence[TerrainCutoutModelInfo],
    *,
    cluster_gap: float,
    min_cluster_footprint_area: float,
) -> Tuple[Tuple[TerrainCutoutModelInfo, ...], ...]:
    unused = set(range(len(model_infos)))
    clusters: List[Tuple[TerrainCutoutModelInfo, ...]] = []
    while unused:
        seed = min(unused)
        unused.remove(seed)
        cluster_indices = {seed}
        queue = [seed]
        while queue:
            current = queue.pop(0)
            current_bounds = (model_infos[current].bounds_min, model_infos[current].bounds_max)
            for other in tuple(unused):
                other_bounds = (model_infos[other].bounds_min, model_infos[other].bounds_max)
                if _expanded_xz_bounds_overlap(current_bounds, other_bounds, cluster_gap):
                    unused.remove(other)
                    cluster_indices.add(other)
                    queue.append(other)
        cluster = tuple(model_infos[index] for index in sorted(cluster_indices))
        min_x, max_x, min_z, max_z = terrain_cutout_cluster_xz_bounds(cluster)
        area = max(0.0, (max_x - min_x) * (max_z - min_z))
        if area >= float(min_cluster_footprint_area):
            clusters.append(cluster)
    clusters.sort(
        key=lambda cluster: (
            terrain_cutout_cluster_xz_bounds(cluster)[0],
            terrain_cutout_cluster_xz_bounds(cluster)[2],
        )
    )
    return tuple(clusters)


def terrain_cutout_cluster_xz_bounds(
    cluster: Sequence[TerrainCutoutModelInfo],
) -> Tuple[float, float, float, float]:
    min_x = min(item.bounds_min[0] for item in cluster)
    max_x = max(item.bounds_max[0] for item in cluster)
    min_z = min(item.bounds_min[2] for item in cluster)
    max_z = max(item.bounds_max[2] for item in cluster)
    return (min_x, max_x, min_z, max_z)


def select_terrain_support_items(
    items: Sequence[TerrainSupportItem],
    anchor_points: Sequence[Vec3],
    *,
    margin: float,
    selection_mode: str = "bounds",
    radius: float = 0.0,
    max_items: int = 0,
) -> Tuple[TerrainSupportItem, ...]:
    if not anchor_points:
        return ()
    anchor_min_x = min(point[0] for point in anchor_points)
    anchor_max_x = max(point[0] for point in anchor_points)
    anchor_min_z = min(point[2] for point in anchor_points)
    anchor_max_z = max(point[2] for point in anchor_points)
    safe_margin = max(0.0, float(margin))
    min_x = anchor_min_x - safe_margin
    max_x = anchor_max_x + safe_margin
    min_z = anchor_min_z - safe_margin
    max_z = anchor_max_z + safe_margin

    mode = normalize_terrain_support_selection_mode(selection_mode)
    if mode == "bounds":
        return tuple(
            item for item in items
            if _xz_bounds_overlap(item.bounds, min_x, max_x, min_z, max_z)
        )
    if mode not in {"connected_radius", "connected_budget"}:
        raise ValueError(f"unsupported terrain support selection mode: {selection_mode}")

    bounds_candidates = tuple(
        item for item in items
        if _xz_bounds_overlap(item.bounds, min_x, max_x, min_z, max_z)
    )
    start_candidates = tuple(
        item for item in items
        if anchor_min_x <= item.center[0] <= anchor_max_x
        and anchor_min_z <= item.center[2] <= anchor_max_z
    ) or bounds_candidates or tuple(items)
    if not start_candidates:
        return ()
    safe_radius = float(radius)
    if safe_radius <= 0.0:
        raise ValueError("connected terrain support patch requires a positive radius")
    start_item = max(start_candidates, key=terrain_support_start_score)
    return tuple(
        _connected_terrain_support_items_within_radius(
            tuple(items),
            seed_polygon_index=start_item.polygon_index,
            center_x=float(start_item.center[0]),
            center_z=float(start_item.center[2]),
            radius=safe_radius,
            max_items=max(0, int(max_items)) if mode == "connected_budget" else 0,
        )
    )


def normalize_terrain_support_selection_mode(value: object) -> str:
    mode = str(value or "bounds").strip().lower().replace("-", "_")
    if mode in {"", "bounds", "bound", "rect", "rectangle", "overlap", "footprint"}:
        return "bounds"
    if mode in {"connected", "connected_radius", "component", "component_radius"}:
        return "connected_radius"
    if mode in {
        "connected_budget",
        "budgeted_connected",
        "connected_radius_budget",
        "budgeted_connected_radius",
        "component_budget",
        "budgeted_component",
        "component_radius_budget",
        "budgeted_component_radius",
    }:
        return "connected_budget"
    return mode


def normalize_terrain_support_brush_mode(value: object) -> str:
    mode = str(value or "single_polygon").strip().lower().replace("-", "_")
    if mode in {"", "single", "single_polygon", "polygon", "polygon_prism", "polygon_prisms"}:
        return "single_polygon"
    if mode in {"paired", "paired_triangles", "triangle_pairs", "cell", "cell_prisms", "terrain_cells"}:
        return "paired_triangles"
    if mode in {
        "triangulated",
        "triangulated_ngons",
        "triangulated_n_gons",
        "split_ngons",
        "split_n_gons",
        "triangle_ngons",
        "triangle_n_gons",
    }:
        return "triangulated_ngons"
    return mode


def polygon_normal(vertices: Sequence[object]) -> Vec3:
    """Return a normalized polygon normal using the same winding as DAT polygons."""
    finite = tuple(_finite_vec3(vertex) for vertex in vertices)
    if len(finite) < 3:
        return (0.0, 0.0, 0.0)
    nx = ny = nz = 0.0
    for index, current in enumerate(finite):
        next_vertex = finite[(index + 1) % len(finite)]
        nx += (current[1] - next_vertex[1]) * (current[2] + next_vertex[2])
        ny += (current[2] - next_vertex[2]) * (current[0] + next_vertex[0])
        nz += (current[0] - next_vertex[0]) * (current[1] + next_vertex[1])
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length <= 1.0e-8:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def walkable_vertex_indices(
    model: object,
    *,
    normal_y_threshold: float = 0.35,
) -> set[int]:
    """Return vertex indices used by upward-facing polygons in a DAT world model."""
    result: set[int] = set()
    points = list(getattr(model, "points", ()) or ())
    for polygon in getattr(model, "polygons", ()) or ():
        raw_indices = tuple(int(index) for index in getattr(polygon, "vertex_indices", ()) or ())
        if len(raw_indices) < 3:
            continue
        polygon_vertices: List[Vec3] = []
        valid_indices: List[int] = []
        for index in raw_indices:
            if not (0 <= int(index) < len(points)):
                continue
            polygon_vertices.append(_finite_vec3(points[int(index)]))
            valid_indices.append(int(index))
        if len(polygon_vertices) < 3:
            continue
        if polygon_normal(polygon_vertices)[1] > float(normal_y_threshold):
            result.update(valid_indices)
    return result


def point_in_xz_polygon(
    x: float,
    z: float,
    polygon: Sequence[XZPoint],
    *,
    epsilon: float = 1.0e-4,
) -> bool:
    """Return whether an X/Z point is inside or on the boundary of a polygon."""
    if len(polygon) < 3:
        return False
    eps = float(epsilon)
    for offset, point in enumerate(polygon):
        next_point = polygon[(offset + 1) % len(polygon)]
        if _point_on_xz_segment(float(x), float(z), point, next_point, eps):
            return True

    inside = False
    prev_x, prev_z = polygon[-1]
    for cur_x, cur_z in polygon:
        intersects = (cur_z > z) != (prev_z > z)
        if intersects:
            crossing_x = (prev_x - cur_x) * (float(z) - cur_z) / (prev_z - cur_z) + cur_x
            if float(x) <= crossing_x + eps:
                inside = not inside
        prev_x, prev_z = cur_x, cur_z
    return inside


def xz_polygon_interior_sample_points(
    polygon: Sequence[XZPoint],
    sample_grid: int,
    *,
    max_grid_side: int = 16,
) -> Tuple[XZPoint, ...]:
    """Sample points inside an X/Z polygon for coverage diagnostics."""
    if len(polygon) < 3:
        return ()
    min_x, max_x, min_z, max_z = xz_polygon_bounds(polygon)
    if max_x - min_x <= 1.0e-6 or max_z - min_z <= 1.0e-6:
        return ()
    side = _clamp_int(int(sample_grid), 1, int(max_grid_side))
    samples: List[XZPoint] = []
    for z_index in range(side):
        z = min_z + (float(z_index) + 0.5) * (max_z - min_z) / float(side)
        for x_index in range(side):
            x = min_x + (float(x_index) + 0.5) * (max_x - min_x) / float(side)
            if point_in_xz_polygon(x, z, polygon):
                samples.append((x, z))
    if samples:
        return tuple(samples)
    center = (
        sum(point[0] for point in polygon) / float(len(polygon)),
        sum(point[1] for point in polygon) / float(len(polygon)),
    )
    return (center,) if point_in_xz_polygon(center[0], center[1], polygon) else ()


def xz_rect_sample_points(
    min_x: float,
    max_x: float,
    min_z: float,
    max_z: float,
    sample_grid: int,
    *,
    max_grid_side: int = 32,
) -> Tuple[XZPoint, ...]:
    """Sample a regular X/Z footprint rectangle for cutout diagnostics."""
    width = float(max_x) - float(min_x)
    depth = float(max_z) - float(min_z)
    if width <= 1.0e-6 or depth <= 1.0e-6:
        return ()
    side = _clamp_int(int(sample_grid), 1, int(max_grid_side))
    samples: List[XZPoint] = []
    for z_index in range(side):
        z = float(min_z) + (float(z_index) + 0.5) * depth / float(side)
        for x_index in range(side):
            x = float(min_x) + (float(x_index) + 0.5) * width / float(side)
            samples.append((x, z))
    return tuple(samples)


def xz_polygon_bounds(polygon: Sequence[XZPoint]) -> Tuple[float, float, float, float]:
    if not polygon:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        min(point[0] for point in polygon),
        max(point[0] for point in polygon),
        min(point[1] for point in polygon),
        max(point[1] for point in polygon),
    )


def vec3_dot(a: Vec3, b: Vec3) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def vec3_distance_sq(a: Vec3, b: Vec3) -> float:
    dx = float(a[0]) - float(b[0])
    dy = float(a[1]) - float(b[1])
    dz = float(a[2]) - float(b[2])
    return dx * dx + dy * dy + dz * dz


def vec3_distance(a: Vec3, b: Vec3) -> float:
    return math.sqrt(vec3_distance_sq(a, b))


def vec3_bounds(points: Sequence[object]) -> Tuple[Vec3, Vec3]:
    finite = tuple(_finite_vec3(point) for point in points)
    if not finite:
        raise ValueError("cannot compute bounds for an empty point sequence")
    return (
        (
            min(point[0] for point in finite),
            min(point[1] for point in finite),
            min(point[2] for point in finite),
        ),
        (
            max(point[0] for point in finite),
            max(point[1] for point in finite),
            max(point[2] for point in finite),
        ),
    )


def expanded_vec3_bounds(
    source_min: Vec3,
    source_max: Vec3,
    points: Sequence[object],
) -> Tuple[Vec3, Vec3]:
    point_min, point_max = vec3_bounds(points)
    return (
        (
            min(float(source_min[0]), point_min[0]),
            min(float(source_min[1]), point_min[1]),
            min(float(source_min[2]), point_min[2]),
        ),
        (
            max(float(source_max[0]), point_max[0]),
            max(float(source_max[1]), point_max[1]),
            max(float(source_max[2]), point_max[2]),
        ),
    )


def point_box_overshoot(
    point: Vec3,
    min_box: Optional[Vec3],
    max_box: Optional[Vec3],
    *,
    epsilon: float = 0.0,
) -> float:
    if min_box is None or max_box is None:
        return 0.0
    overshoot = 0.0
    for value, min_value, max_value in zip(point, min_box, max_box):
        if float(value) < float(min_value) - float(epsilon):
            overshoot = max(overshoot, float(min_value) - float(value))
        if float(value) > float(max_value) + float(epsilon):
            overshoot = max(overshoot, float(value) - float(max_value))
    return float(overshoot)


def bounds_box_overshoot(
    bounds_min: Vec3,
    bounds_max: Vec3,
    limit_min: Optional[Vec3],
    limit_max: Optional[Vec3],
    *,
    epsilon: float = 0.0,
) -> float:
    return max(
        point_box_overshoot(bounds_min, limit_min, limit_max, epsilon=float(epsilon)),
        point_box_overshoot(bounds_max, limit_min, limit_max, epsilon=float(epsilon)),
    )


def classification_sign(value: float, epsilon: float) -> int:
    if float(value) > float(epsilon):
        return 1
    if float(value) < -float(epsilon):
        return -1
    return 0


def points_classification_signs(
    points: Sequence[Vec3],
    normal: Vec3,
    distance: float,
    epsilon: float,
) -> Tuple[int, ...]:
    return tuple(
        classification_sign(vec3_dot(normal, point) - float(distance), epsilon)
        for point in points
    )


def edited_points_classification_signs(
    source_points: Sequence[Vec3],
    edited_points: Sequence[Vec3],
    normal: Vec3,
    distance: float,
    epsilon: float,
) -> Tuple[Tuple[int, ...], float]:
    signs: List[int] = []
    max_delta = 0.0
    for source_point, edited_point in zip(source_points, edited_points):
        source_distance = vec3_dot(normal, source_point) - float(distance)
        edited_distance = vec3_dot(normal, edited_point) - float(distance)
        max_delta = max(max_delta, abs(edited_distance - source_distance))
        signs.append(classification_sign(edited_distance, epsilon))
    return tuple(signs), max_delta


def polygon_area(points: Sequence[object]) -> float:
    finite = tuple(_finite_vec3(point) for point in points)
    if len(finite) < 3:
        return 0.0
    p0 = finite[0]
    area = 0.0
    for offset in range(1, len(finite) - 1):
        p1 = finite[offset]
        p2 = finite[offset + 1]
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        area += math.sqrt(nx * nx + ny * ny + nz * nz) * 0.5
    return area


def polygon_plane(points: Sequence[object], indices: Sequence[int]) -> Tuple[Vec3, float]:
    p0 = _finite_vec3(points[indices[0]])
    for offset in range(1, len(indices) - 1):
        p1 = _finite_vec3(points[indices[offset]])
        p2 = _finite_vec3(points[indices[offset + 1]])
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length > 1.0e-7:
            normal = (nx / length, ny / length, nz / length)
            return normal, vec3_dot(normal, p0)
    return (0.0, 1.0, 0.0), p0[1]


def terrain_support_start_score(item: TerrainSupportItem) -> float:
    normal, _dist = polygon_plane(item.points, tuple(range(len(item.points))))
    area = polygon_area(item.points)
    upward = max(0.0, float(normal[1]))
    return area * (upward ** 4)


def terrain_support_start_placement(
    items: Sequence[TerrainSupportItem],
    anchor_points: Sequence[Vec3],
    *,
    margin: float,
) -> TerrainSupportPlacement:
    if not items:
        raise ValueError("terrain support placement requires selected item(s)")
    if not anchor_points:
        raise ValueError("terrain support placement requires anchor point(s)")

    anchor_min_x = min(point[0] for point in anchor_points)
    anchor_max_x = max(point[0] for point in anchor_points)
    anchor_min_z = min(point[2] for point in anchor_points)
    anchor_max_z = max(point[2] for point in anchor_points)
    safe_margin = max(0.0, float(margin))
    min_x = anchor_min_x - safe_margin
    max_x = anchor_max_x + safe_margin
    min_z = anchor_min_z - safe_margin
    max_z = anchor_max_z + safe_margin
    anchor_center = ((min_x + max_x) * 0.5, 0.0, (min_z + max_z) * 0.5)

    start_center = items[0].center
    start_top_y = items[0].center[1]
    start_score = -1.0
    fallback_center = items[0].center
    closest_distance = float("inf")
    start_candidates = tuple(
        item for item in items
        if anchor_min_x <= item.center[0] <= anchor_max_x
        and anchor_min_z <= item.center[2] <= anchor_max_z
    ) or tuple(items)
    start_candidate_polygon_indices = {item.polygon_index for item in start_candidates}

    for item in items:
        distance = (item.center[0] - anchor_center[0]) ** 2 + (item.center[2] - anchor_center[2]) ** 2
        if distance < closest_distance:
            closest_distance = distance
            fallback_center = item.center
        score = terrain_support_start_score(item)
        if item.polygon_index in start_candidate_polygon_indices and score > start_score:
            start_score = score
            start_center = item.center
            start_top_y = max(point[1] for point in item.points)

    if start_score <= 0.0:
        start_center = fallback_center
        start_top_y = fallback_center[1]
    return TerrainSupportPlacement(
        center=(start_center[0], start_top_y, start_center[2]),
        top_y=start_top_y,
    )


def triangulate_polygon_vertex_offsets(points: Sequence[Vec3]) -> Tuple[Tuple[int, int, int], ...]:
    """Triangulate a Terrain* polygon boundary as offsets into *points*."""
    finite = tuple(_finite_vec3(point) for point in points)
    if len(finite) < 3:
        return ()
    if len(finite) == 3:
        return ((0, 1, 2),)

    projected = tuple((point[0], point[2]) for point in finite)
    area = _signed_area_2d(projected)
    if abs(area) < 1.0e-5:
        return tuple((0, offset, offset + 1) for offset in range(1, len(finite) - 1))
    orientation = 1.0 if area > 0.0 else -1.0
    remaining = list(range(len(finite)))
    triangles: List[Tuple[int, int, int]] = []
    guard = 0
    while len(remaining) > 3 and guard < len(finite) * len(finite):
        guard += 1
        ear_index: Optional[int] = None
        for offset, current in enumerate(remaining):
            prev_index = remaining[(offset - 1) % len(remaining)]
            next_index = remaining[(offset + 1) % len(remaining)]
            if not _is_ear_2d(projected, prev_index, current, next_index, remaining, orientation):
                continue
            ear_index = offset
            triangles.append((prev_index, current, next_index))
            break
        if ear_index is None:
            return tuple(
                (remaining[0], remaining[offset], remaining[offset + 1])
                for offset in range(1, len(remaining) - 1)
            )
        del remaining[ear_index]

    if len(remaining) == 3:
        triangles.append((remaining[0], remaining[1], remaining[2]))
    return tuple(triangles)


def triangulated_terrain_support_items(
    item: TerrainSupportItem,
) -> Tuple[TerrainSupportItem, ...]:
    """Split one TerrainSupportItem into triangle TerrainSupportItem records."""
    triangles = triangulate_polygon_vertex_offsets(item.points)
    if not triangles:
        triangles = tuple((0, offset, offset + 1) for offset in range(1, len(item.points) - 1))

    result: List[TerrainSupportItem] = []
    for tri in triangles:
        tri_indices = tuple(int(item.indices[offset]) for offset in tri)
        tri_points = tuple(_finite_vec3(item.points[offset]) for offset in tri)
        center = (
            sum(point[0] for point in tri_points) / 3.0,
            sum(point[1] for point in tri_points) / 3.0,
            sum(point[2] for point in tri_points) / 3.0,
        )
        bounds = (
            min(point[0] for point in tri_points),
            max(point[0] for point in tri_points),
            min(point[2] for point in tri_points),
            max(point[2] for point in tri_points),
        )
        result.append(TerrainSupportItem(
            item.polygon_index,
            item.polygon,
            tri_indices,
            tri_points,
            center,
            bounds,
        ))
    return tuple(result)


def _xz_bounds_overlap(
    bounds: Tuple[float, float, float, float],
    min_x: float,
    max_x: float,
    min_z: float,
    max_z: float,
) -> bool:
    poly_min_x, poly_max_x, poly_min_z, poly_max_z = bounds
    return poly_max_x >= min_x and poly_min_x <= max_x and poly_max_z >= min_z and poly_min_z <= max_z


def _xz_bounds_distance_sq(bounds: Tuple[float, float, float, float], x: float, z: float) -> float:
    min_x, max_x, min_z, max_z = bounds
    dx = 0.0
    if x < min_x:
        dx = min_x - x
    elif x > max_x:
        dx = x - max_x
    dz = 0.0
    if z < min_z:
        dz = min_z - z
    elif z > max_z:
        dz = z - max_z
    return dx * dx + dz * dz


def _point_on_xz_segment(
    x: float,
    z: float,
    first: XZPoint,
    second: XZPoint,
    eps: float,
) -> bool:
    ax, az = first
    bx, bz = second
    min_x = min(ax, bx) - eps
    max_x = max(ax, bx) + eps
    min_z = min(az, bz) - eps
    max_z = max(az, bz) + eps
    if x < min_x or x > max_x or z < min_z or z > max_z:
        return False
    dx = bx - ax
    dz = bz - az
    length_sq = dx * dx + dz * dz
    if length_sq <= eps * eps:
        return (x - ax) * (x - ax) + (z - az) * (z - az) <= eps * eps
    cross = (x - ax) * dz - (z - az) * dx
    return abs(cross) <= eps * math.sqrt(length_sq)


def _expanded_xz_bounds_overlap(
    first: Tuple[Vec3, Vec3],
    second: Tuple[Vec3, Vec3],
    gap: float,
) -> bool:
    first_min, first_max = first
    second_min, second_max = second
    safe_gap = max(0.0, float(gap))
    return (
        first_max[0] + safe_gap >= second_min[0]
        and first_min[0] - safe_gap <= second_max[0]
        and first_max[2] + safe_gap >= second_min[2]
        and first_min[2] - safe_gap <= second_max[2]
    )


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(int(low), min(int(high), int(value)))


def _connected_terrain_support_items_within_radius(
    items: Sequence[TerrainSupportItem],
    *,
    seed_polygon_index: int,
    center_x: float,
    center_z: float,
    radius: float,
    max_items: int = 0,
) -> List[TerrainSupportItem]:
    local_by_polygon = {item.polygon_index: index for index, item in enumerate(items)}
    seed = local_by_polygon.get(int(seed_polygon_index))
    if seed is None:
        return []
    by_vertex: Dict[int, List[int]] = defaultdict(list)
    for local_index, item in enumerate(items):
        for vertex_index in item.indices:
            by_vertex[int(vertex_index)].append(local_index)

    radius_sq = max(0.0, float(radius)) ** 2
    limit = max(0, int(max_items))
    if limit:
        def item_priority(local_index: int) -> Tuple[float, float, int]:
            distance = _xz_bounds_distance_sq(items[local_index].bounds, center_x, center_z)
            return (distance, -terrain_support_start_score(items[local_index]), local_index)

        selected: List[TerrainSupportItem] = []
        queued = {seed}
        queue_heap: List[Tuple[float, float, int]] = [item_priority(seed)]
        while queue_heap and len(selected) < limit:
            distance, _score, current = heapq.heappop(queue_heap)
            if distance > radius_sq:
                continue
            selected.append(items[current])
            for vertex_index in items[current].indices:
                for neighbor in by_vertex.get(int(vertex_index), ()):
                    if neighbor in queued:
                        continue
                    queued.add(neighbor)
                    heapq.heappush(queue_heap, item_priority(neighbor))
        return selected

    seen = {seed}
    queue = deque([seed])
    while queue:
        current = queue.popleft()
        for vertex_index in items[current].indices:
            for neighbor in by_vertex.get(int(vertex_index), ()):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)

    return [
        item for local_index, item in enumerate(items)
        if local_index in seen and _xz_bounds_distance_sq(item.bounds, center_x, center_z) <= radius_sq
    ]


def _polygon_area(points: Sequence[Vec3]) -> float:
    return polygon_area(points)


def _polygon_plane(points: Sequence[Vec3], indices: Sequence[int]) -> Tuple[Vec3, float]:
    return polygon_plane(points, indices)


def _signed_area_2d(points: Sequence[Tuple[float, float]]) -> float:
    return sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))
    ) * 0.5


def _is_ear_2d(
    points: Sequence[Tuple[float, float]],
    prev_index: int,
    current_index: int,
    next_index: int,
    remaining: Sequence[int],
    orientation: float,
) -> bool:
    a = points[prev_index]
    b = points[current_index]
    c = points[next_index]
    cross = _cross_2d(a, b, c)
    if cross * orientation <= 1.0e-5:
        return False
    for candidate in remaining:
        if candidate in {prev_index, current_index, next_index}:
            continue
        if _point_in_triangle_2d(points[candidate], a, b, c, orientation):
            return False
    return True


def _cross_2d(a: Tuple[float, float], b: Tuple[float, float], c: Tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])


def _point_in_triangle_2d(
    point: Tuple[float, float],
    a: Tuple[float, float],
    b: Tuple[float, float],
    c: Tuple[float, float],
    orientation: float,
) -> bool:
    eps = 1.0e-5
    return (
        _cross_2d(a, b, point) * orientation >= -eps
        and _cross_2d(b, c, point) * orientation >= -eps
        and _cross_2d(c, a, point) * orientation >= -eps
    )


def _finite_vec3(value: object) -> Vec3:
    try:
        x, y, z = value  # type: ignore[misc]
        result = (float(x), float(y), float(z))
    except Exception:
        return (0.0, 0.0, 0.0)
    if not all(math.isfinite(component) for component in result):
        return (0.0, 0.0, 0.0)
    return result


def _point_distance_sq(a: Vec3, b: Vec3) -> float:
    return vec3_distance_sq(a, b)


def _cross_length(a: Vec3, b: Vec3, c: Vec3) -> float:
    ux = float(b[0]) - float(a[0])
    uy = float(b[1]) - float(a[1])
    uz = float(b[2]) - float(a[2])
    vx = float(c[0]) - float(b[0])
    vy = float(c[1]) - float(b[1])
    vz = float(c[2]) - float(b[2])
    cx = uy * vz - uz * vy
    cy = uz * vx - ux * vz
    cz = ux * vy - uy * vx
    return math.sqrt(cx * cx + cy * cy + cz * cz)
