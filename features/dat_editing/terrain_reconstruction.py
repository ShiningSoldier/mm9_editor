"""Terrain polygon cleanup helpers for DAT -> ED reconstruction."""

from __future__ import annotations

import math
import heapq
from collections import defaultdict, deque
from typing import Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

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


class TerrainPlayableAreaAllocation(NamedTuple):
    """One connected terrain neighborhood associated with gameplay anchors."""

    area_index: int
    seed_polygon_index: int
    anchor_count: int
    center: Vec3
    bounds: Tuple[float, float, float, float]
    candidate_polygon_count: int
    walkable_polygon_count: int
    walkable_xz_area: float
    allocation_weight: float
    allocated_polygon_budget: int


class _TerrainPlayableAreaContext(NamedTuple):
    allocation: TerrainPlayableAreaAllocation
    candidate_items: Tuple[TerrainSupportItem, ...]


class TerrainCollisionOracleTriangle(NamedTuple):
    bounds: Tuple[float, float, float, float]
    points: Tuple[Vec3, Vec3, Vec3]


class TerrainCollisionOracle(NamedTuple):
    cell_size: float
    cells: Mapping[
        Tuple[int, int],
        Tuple[TerrainCollisionOracleTriangle, ...],
    ]
    triangle_count: int


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


class PhysicsShellCandidateGroup(NamedTuple):
    """One closed slab boundary representing one or more source polygons."""

    candidates: Tuple[PhysicsShellCandidate, ...]
    points: Tuple[Vec3, ...]
    generated_face_count: int


class PhysicsShellConsolidationIndex(NamedTuple):
    """Reusable coplanar adjacency evidence for one candidate collection."""

    candidate_indices: Tuple[int, ...]
    compatible_neighbor_indices: Tuple[Tuple[int, Tuple[int, ...]], ...] = ()


class PhysicsShellPackingPlan(NamedTuple):
    """A deterministic cost-aware selection of consolidated shell regions."""

    groups: Tuple[PhysicsShellCandidateGroup, ...] = ()
    source_polygon_count: int = 0
    generated_brush_count: int = 0
    generated_face_count: int = 0
    recovered_source_area: float = 0.0
    weighted_value: float = 0.0
    role_counts: Tuple[Tuple[str, int], ...] = ()
    protected_polygon_indices: Tuple[int, ...] = ()


class PhysicsShellPackingComparison(NamedTuple):
    """Controlled comparison of balanced and cost-aware shell plans."""

    balanced: PhysicsShellPackingPlan = PhysicsShellPackingPlan()
    cost_aware: PhysicsShellPackingPlan = PhysicsShellPackingPlan()
    candidate_count: int = 0
    source_polygon_limit: int = 0
    generated_face_budget: int = 0
    preferred_validation_mode: str = "equivalent"
    weighted_value_delta: float = 0.0
    recovered_source_area_delta: float = 0.0
    generated_brush_delta: int = 0
    generated_face_delta: int = 0
    protected_sets_match: bool = True
    notes: Tuple[str, ...] = ()


class PhysicsShellStairAssembly(NamedTuple):
    """A conservative connected tread/riser candidate from PhysicsBSP."""

    assembly_index: int
    source_polygon_indices: Tuple[int, ...] = ()
    tread_polygon_indices: Tuple[int, ...] = ()
    riser_polygon_indices: Tuple[int, ...] = ()
    support_wall_polygon_indices: Tuple[int, ...] = ()
    elevation_levels: Tuple[float, ...] = ()
    bounds_min: Vec3 = (0.0, 0.0, 0.0)
    bounds_max: Vec3 = (0.0, 0.0, 0.0)
    step_count: int = 0
    min_step_height: float = 0.0
    max_step_height: float = 0.0
    generated_face_count: int = 0
    confidence: str = "candidate"
    notes: Tuple[str, ...] = ()


class PhysicsShellFocusedSelection(NamedTuple):
    selected: Tuple[PhysicsShellCandidate, ...]
    anchor_candidate_count: int = 0
    focus_candidate_count: int = 0
    focus_component_count: int = 0
    focus_selected_count: int = 0


class PhysicsShellRoleIndexBatch(NamedTuple):
    """A deterministic source-polygon subset for controlled Processor runs."""

    role: str
    batch_index: int
    polygon_indices: Tuple[int, ...]
    generated_face_count: int = 0


_PHYSICS_SHELL_MIN_POLYGON_AREA = 0.25
_PHYSICS_SHELL_MIN_EDGE_LENGTH = 0.05
_PHYSICS_SHELL_MAX_PLANE_DEVIATION = 0.01
_PHYSICS_SHELL_MIN_EXTRUSION_THICKNESS = 1.0
_PHYSICS_SHELL_MAX_CONSOLIDATED_GROUP_SIZE = 4
_PHYSICS_SHELL_COST_AWARE_MAX_CONSOLIDATED_GROUP_SIZE = 8
_PHYSICS_SHELL_CONSOLIDATION_SEARCH_NEIGHBORS = 24
_DEFAULT_PHYSICS_SHELL_ROLE_WEIGHTS: Mapping[str, float] = {
    "side_wall": 8.0,
    "floor": 1.0,
    "ceiling": 1.0,
    "helper/special": 0.05,
}


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


def build_terrain_collision_oracle(
    physics_model: Optional[object],
    *,
    cell_size: float = 512.0,
) -> TerrainCollisionOracle:
    """Index PhysicsBSP floor-like triangles for read-only terrain checks."""
    safe_cell_size = max(64.0, float(cell_size))
    if physics_model is None:
        return TerrainCollisionOracle(safe_cell_size, {}, 0)
    model_points = tuple(getattr(physics_model, "points", ()) or ())
    cells: Dict[
        Tuple[int, int],
        List[TerrainCollisionOracleTriangle],
    ] = defaultdict(list)
    triangle_count = 0
    for polygon in tuple(getattr(physics_model, "polygons", ()) or ()):
        raw_indices = tuple(
            int(index)
            for index in tuple(
                getattr(polygon, "vertex_indices", ()) or ()
            )
        )
        if len(raw_indices) < 3:
            continue
        try:
            indices = canonical_terrain_polygon_indices(raw_indices)
            indices = simplify_collinear_terrain_polygon_indices(
                indices,
                model_points,
            )
            points = tuple(_finite_vec3(model_points[index]) for index in indices)
        except (IndexError, TypeError, ValueError):
            continue
        if len(points) < 3:
            continue
        normal, _dist = polygon_plane(
            points,
            tuple(range(len(points))),
        )
        if abs(float(normal[1])) < 0.35:
            continue
        for offset in range(1, len(points) - 1):
            triangle_points = (
                points[0],
                points[offset],
                points[offset + 1],
            )
            bounds = (
                min(point[0] for point in triangle_points),
                max(point[0] for point in triangle_points),
                min(point[2] for point in triangle_points),
                max(point[2] for point in triangle_points),
            )
            triangle = TerrainCollisionOracleTriangle(
                bounds,
                triangle_points,
            )
            min_cell_x = math.floor(bounds[0] / safe_cell_size)
            max_cell_x = math.floor(bounds[1] / safe_cell_size)
            min_cell_z = math.floor(bounds[2] / safe_cell_size)
            max_cell_z = math.floor(bounds[3] / safe_cell_size)
            for cell_x in range(min_cell_x, max_cell_x + 1):
                for cell_z in range(min_cell_z, max_cell_z + 1):
                    cells[(cell_x, cell_z)].append(triangle)
            triangle_count += 1
    return TerrainCollisionOracle(
        safe_cell_size,
        {
            key: tuple(value)
            for key, value in cells.items()
        },
        triangle_count,
    )


def terrain_collision_oracle_floor_y(
    oracle: TerrainCollisionOracle,
    x: float,
    z: float,
    *,
    source_y: float,
    max_vertical_distance: float = 128.0,
) -> Optional[float]:
    """Return the PhysicsBSP floor nearest a source terrain height."""
    if oracle.triangle_count <= 0:
        return None
    cell = (
        math.floor(float(x) / oracle.cell_size),
        math.floor(float(z) / oracle.cell_size),
    )
    hits: List[float] = []
    for triangle in oracle.cells.get(cell, ()):
        if not _xz_bounds_overlap(
            triangle.bounds,
            float(x),
            float(x),
            float(z),
            float(z),
        ):
            continue
        y = _point_in_triangle_xz_y(
            float(x),
            float(z),
            triangle.points[0],
            triangle.points[1],
            triangle.points[2],
        )
        if y is None:
            continue
        if abs(float(y) - float(source_y)) <= max(
            0.0,
            float(max_vertical_distance),
        ):
            hits.append(float(y))
    if not hits:
        return None
    return min(
        hits,
        key=lambda value: (
            abs(value - float(source_y)),
            -value,
        ),
    )


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


def physics_shell_role_index_batches(
    model: object,
    *,
    max_indices_per_batch: int = 128,
    max_generated_faces_per_batch: int = 0,
    roles: Sequence[str] = PHYSICS_SHELL_COVERAGE_ROLES,
) -> Tuple[PhysicsShellRoleIndexBatch, ...]:
    """Return deterministic role/index batches for Processor warning bisection.

    LithTech 2.1 Processor logs identify problem brushes only by count.  These
    batches let callers compile small, provenance-preserving subsets whose
    source polygon indices and shell roles are known before each run.  Invalid
    or degenerate source polygons are omitted because they cannot produce a
    stable shell brush.
    """
    batch_size = int(max_indices_per_batch)
    if batch_size <= 0:
        raise ValueError("PhysicsBSP subset batch size must be positive")
    face_budget = int(max_generated_faces_per_batch)
    if face_budget < 0:
        raise ValueError("PhysicsBSP subset generated-face budget cannot be negative")

    requested_roles: List[str] = []
    seen_roles = set()
    for raw_role in roles:
        role = str(raw_role or "").strip()
        if not role or role in seen_roles:
            continue
        seen_roles.add(role)
        requested_roles.append(role)

    candidates = physics_shell_candidates(model)
    by_role: Dict[str, List[PhysicsShellCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_role[str(candidate.role)].append(candidate)

    batches: List[PhysicsShellRoleIndexBatch] = []
    for role in requested_roles:
        role_candidates = sorted(
            by_role.get(role, ()),
            key=lambda candidate: int(candidate.polygon_index),
        )
        role_batch_index = 0
        group: List[PhysicsShellCandidate] = []
        group_face_count = 0
        for candidate in role_candidates:
            candidate_faces = int(candidate.generated_face_count)
            if group and (
                len(group) >= batch_size
                or (face_budget > 0 and group_face_count + candidate_faces > face_budget)
            ):
                batches.append(
                    PhysicsShellRoleIndexBatch(
                        role=role,
                        batch_index=role_batch_index,
                        polygon_indices=tuple(int(item.polygon_index) for item in group),
                        generated_face_count=group_face_count,
                    )
                )
                role_batch_index += 1
                group = []
                group_face_count = 0
            group.append(candidate)
            group_face_count += candidate_faces
        if group:
            batches.append(
                PhysicsShellRoleIndexBatch(
                    role=role,
                    batch_index=role_batch_index,
                    polygon_indices=tuple(int(item.polygon_index) for item in group),
                    generated_face_count=group_face_count,
                )
            )
    return tuple(batches)


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


def focused_balanced_physics_shell_candidates(
    candidates: Sequence[PhysicsShellCandidate],
    limit: int,
    *,
    focus_points: Sequence[object] = (),
    focus_radius: float = 0.0,
    focus_budget: int = 0,
    focus_seed_radius: float = 0.0,
) -> PhysicsShellFocusedSelection:
    """Prioritize a connected local shell neighborhood before global fill.

    The focused set starts from candidate centers near one of ``focus_points``.
    It only retains nearby faces from components containing those seeds, which
    avoids spending a stairwell reservation on disconnected nearby residue.
    By default the seed radius is one quarter of the outer focus radius.
    When the local set fits the reservation it is retained in full so floors,
    risers, and ceilings are not lost to the global side-wall quota.
    """
    safe_limit = max(0, int(limit))
    default_selected = balanced_physics_shell_candidates(candidates, safe_limit)
    safe_radius = max(0.0, float(focus_radius))
    safe_seed_radius = min(
        safe_radius,
        max(0.0, float(focus_seed_radius)) if float(focus_seed_radius) > 0.0 else safe_radius * 0.25,
    )
    anchors = tuple(_finite_vec3(point) for point in focus_points)
    if safe_limit <= 0 or safe_radius <= 0.0 or safe_seed_radius <= 0.0 or not anchors:
        return PhysicsShellFocusedSelection(selected=default_selected)

    valid_candidates = tuple(
        candidate for candidate in candidates if candidate.role != "degenerate"
    )
    radius_sq = safe_radius * safe_radius
    seed_radius_sq = safe_seed_radius * safe_seed_radius
    anchor_indices = {
        candidate.polygon_index
        for candidate in valid_candidates
        if _physics_shell_candidate_near_any_focus(candidate, anchors, seed_radius_sq)
    }
    if not anchor_indices:
        return PhysicsShellFocusedSelection(selected=default_selected)

    components = _physics_shell_candidate_components(valid_candidates)
    focus_components = tuple(
        component for component in components
        if any(candidate.polygon_index in anchor_indices for candidate in component)
    )
    focus_component_indices = {
        candidate.polygon_index
        for component in focus_components
        for candidate in component
    }
    focused_candidates = tuple(
        candidate for candidate in valid_candidates
        if candidate.polygon_index in focus_component_indices
        and _physics_shell_candidate_near_any_focus(candidate, anchors, radius_sq)
    )
    if not focused_candidates:
        return PhysicsShellFocusedSelection(
            selected=default_selected,
            anchor_candidate_count=len(anchor_indices),
            focus_component_count=len(focus_components),
        )

    reservation = min(
        safe_limit,
        max(0, int(focus_budget)) if int(focus_budget) > 0 else safe_limit,
    )
    if len(focused_candidates) <= reservation:
        focused_selected = _connected_spatial_physics_shell_candidate_order(focused_candidates)
    else:
        focused_selected = balanced_physics_shell_candidates(focused_candidates, reservation)
    focused_selected_indices = {item.polygon_index for item in focused_selected}
    remaining_candidates = tuple(
        candidate for candidate in valid_candidates
        if candidate.polygon_index not in focused_selected_indices
    )
    remaining_selected = balanced_physics_shell_candidates(
        remaining_candidates,
        safe_limit - len(focused_selected),
    )
    return PhysicsShellFocusedSelection(
        selected=tuple(focused_selected) + tuple(remaining_selected),
        anchor_candidate_count=len(anchor_indices),
        focus_candidate_count=len(focused_candidates),
        focus_component_count=len(focus_components),
        focus_selected_count=len(focused_selected),
    )


def build_physics_shell_consolidation_index(
    model: Optional[object],
    candidates: Sequence[PhysicsShellCandidate],
) -> PhysicsShellConsolidationIndex:
    """Precompute safe coplanar adjacency for repeated packing estimates.

    Budget preflight may evaluate the same candidate set many times while
    searching for the largest source subset.  The old path rebuilt the
    vertex-to-candidate graph and compatibility checks on every estimate.
    This index stores only provenance-stable polygon-index neighbors, so a
    later subset can reuse the expensive adjacency work without sharing any
    mutable selection state.
    """
    ordered = tuple(candidates)
    neighbors: Dict[int, set[int]] = {
        int(candidate.polygon_index): set()
        for candidate in ordered
    }
    by_vertex: Dict[int, List[int]] = defaultdict(list)
    for offset, candidate in enumerate(ordered):
        for index in set(candidate.indices):
            by_vertex[int(index)].append(offset)
    candidate_pairs = set()
    for offsets in by_vertex.values():
        for left_offset, left in enumerate(offsets):
            for right in offsets[left_offset + 1:]:
                candidate_pairs.add((left, right))
    for left_offset, right_offset in sorted(candidate_pairs):
        candidate = ordered[left_offset]
        other = ordered[right_offset]
        if len(set(candidate.indices) & set(other.indices)) < 2:
            continue
        if not _physics_shell_group_candidate_compatible(
            model,
            (candidate,),
            other,
        ):
            continue
        first_index = int(candidate.polygon_index)
        other_index = int(other.polygon_index)
        neighbors[first_index].add(other_index)
        neighbors[other_index].add(first_index)
    return PhysicsShellConsolidationIndex(
        candidate_indices=tuple(int(candidate.polygon_index) for candidate in ordered),
        compatible_neighbor_indices=tuple(
            (
                polygon_index,
                tuple(sorted(neighbors.get(polygon_index, set()))),
            )
            for polygon_index in sorted(neighbors)
        ),
    )


def physics_shell_group_intersects_bounds(
    group: PhysicsShellCandidateGroup,
    bounds: Sequence[Tuple[Vec3, Vec3]],
    *,
    roles: Sequence[str] = ("side_wall",),
) -> bool:
    """Return whether a group's role and bounds intersect a protected void."""
    if not bounds:
        return False
    protected_roles = {str(role) for role in roles}
    if protected_roles and not any(
        str(candidate.role) in protected_roles
        for candidate in group.candidates
    ):
        return False
    if not group.points:
        return False
    group_min = tuple(
        min(float(point[axis]) for point in group.points)
        for axis in range(3)
    )
    group_max = tuple(
        max(float(point[axis]) for point in group.points)
        for axis in range(3)
    )
    return any(
        all(
            group_max[axis] >= bounds_min[axis] - 1.0e-5
            and group_min[axis] <= bounds_max[axis] + 1.0e-5
            for axis in range(3)
        )
        for bounds_min, bounds_max in bounds
    )


def normalized_physics_shell_role_weights(
    overrides: Optional[Mapping[str, float]] = None,
) -> Tuple[Tuple[str, float], ...]:
    """Return deterministic non-negative role weights for shell scoring."""
    weights = dict(_DEFAULT_PHYSICS_SHELL_ROLE_WEIGHTS)
    for raw_role, raw_weight in (overrides or {}).items():
        role = str(raw_role or "").strip()
        if not role:
            continue
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(weight) or weight < 0.0:
            continue
        weights[role] = weight
    return tuple(sorted((role, float(weight)) for role, weight in weights.items()))


def build_physics_shell_packing_plan(
    model: Optional[object],
    candidates: Sequence[PhysicsShellCandidate],
    *,
    source_polygon_limit: int = 0,
    generated_face_budget: int = 0,
    consolidation_index: Optional[PhysicsShellConsolidationIndex] = None,
    protected_bounds: Sequence[Tuple[Vec3, Vec3]] = (),
    protected_roles: Sequence[str] = ("side_wall",),
    role_weights: Optional[Mapping[str, float]] = None,
    playable_importance_points: Sequence[Vec3] = (),
    playable_importance_radius: float = 0.0,
    playable_importance_weight: float = 0.0,
) -> PhysicsShellPackingPlan:
    """Select consolidated regions by recovered value per generated face.

    ``source_polygon_limit`` and ``generated_face_budget`` are independent
    ceilings.  Regions are scored by role-weighted source area per normalized
    slab face, then by source area and polygon index for deterministic ties.
    Optional role-weight overrides and playable-importance points apply a
    deterministic multiplicative bias without changing either hard ceiling.
    A region is accepted only when both ceilings remain satisfied.  This
    planner is intentionally side-effect free so callers can compare it with
    the legacy role-balanced selector before enabling it for a world.
    """
    ordered = tuple(candidates)
    if not ordered:
        return PhysicsShellPackingPlan()
    source_limit = (
        len(ordered)
        if int(source_polygon_limit) <= 0
        else max(0, int(source_polygon_limit))
    )
    face_budget = max(0, int(generated_face_budget))
    index = consolidation_index or build_physics_shell_consolidation_index(model, ordered)
    groups = consolidated_physics_shell_candidate_groups(
        model,
        ordered,
        consolidation_index=index,
        max_group_size=_PHYSICS_SHELL_COST_AWARE_MAX_CONSOLIDATED_GROUP_SIZE,
        require_exact_large_union=True,
    )
    normalized_weights = dict(normalized_physics_shell_role_weights(role_weights))
    importance_points_list: List[Vec3] = []
    for raw_point in playable_importance_points:
        try:
            point = _finite_vec3(raw_point)
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(float(value)) for value in point):
            importance_points_list.append(point)
    importance_points = tuple(importance_points_list)
    importance_radius = max(0.0, float(playable_importance_radius))
    importance_weight = max(0.0, float(playable_importance_weight))

    def playable_factor(group: PhysicsShellCandidateGroup) -> float:
        if not importance_points or importance_radius <= 0.0 or importance_weight <= 0.0:
            return 1.0
        if not group.points:
            return 1.0
        center = tuple(
            sum(float(point[axis]) for point in group.points) / len(group.points)
            for axis in range(3)
        )
        nearest_distance = min(
            math.sqrt(sum((center[axis] - point[axis]) ** 2 for axis in range(3)))
            for point in importance_points
        )
        influence = max(0.0, 1.0 - nearest_distance / importance_radius)
        return 1.0 + importance_weight * influence

    def group_metrics(group: PhysicsShellCandidateGroup) -> Tuple[float, float, int]:
        source_area = sum(float(candidate.area) for candidate in group.candidates)
        role_weight = max(
            (normalized_weights.get(str(candidate.role), 0.25) for candidate in group.candidates),
            default=0.25,
        )
        weighted_value = source_area * role_weight * playable_factor(group)
        face_count = max(1, int(group.generated_face_count))
        return weighted_value / float(face_count), source_area, min(
            int(candidate.polygon_index) for candidate in group.candidates
        )

    ranked_groups = sorted(
        groups,
        key=lambda group: (
            -group_metrics(group)[0],
            -group_metrics(group)[1],
            group_metrics(group)[2],
        ),
    )
    selected: List[PhysicsShellCandidateGroup] = []
    source_count = 0
    face_count = 0
    recovered_area = 0.0
    weighted_value = 0.0
    role_counts: Dict[str, int] = defaultdict(int)
    protected_indices = set()
    for group in ranked_groups:
        group_source_count = len(group.candidates)
        group_face_count = max(0, int(group.generated_face_count))
        if physics_shell_group_intersects_bounds(
            group,
            protected_bounds,
            roles=protected_roles,
        ):
            protected_indices.update(
                int(candidate.polygon_index)
                for candidate in group.candidates
            )
            continue
        if source_count + group_source_count > source_limit:
            continue
        if face_budget and face_count + group_face_count > face_budget:
            continue
        selected.append(group)
        source_count += group_source_count
        face_count += group_face_count
        group_value, group_area, _first_index = group_metrics(group)
        recovered_area += group_area
        weighted_value += group_value * float(group_face_count)
        for candidate in group.candidates:
            role_counts[str(candidate.role)] += 1
    return PhysicsShellPackingPlan(
        groups=tuple(selected),
        source_polygon_count=source_count,
        generated_brush_count=len(selected),
        generated_face_count=face_count,
        recovered_source_area=recovered_area,
        weighted_value=weighted_value,
        role_counts=tuple(sorted(role_counts.items())),
        protected_polygon_indices=tuple(sorted(protected_indices)),
    )


def build_balanced_physics_shell_packing_plan(
    model: Optional[object],
    candidates: Sequence[PhysicsShellCandidate],
    *,
    source_polygon_limit: int = 0,
    generated_face_budget: int = 0,
    consolidation_index: Optional[PhysicsShellConsolidationIndex] = None,
    protected_bounds: Sequence[Tuple[Vec3, Vec3]] = (),
    protected_roles: Sequence[str] = ("side_wall",),
    role_weights: Optional[Mapping[str, float]] = None,
    playable_importance_points: Sequence[Vec3] = (),
    playable_importance_radius: float = 0.0,
    playable_importance_weight: float = 0.0,
) -> PhysicsShellPackingPlan:
    """Measure the legacy balanced policy with cost-aware plan semantics.

    This preserves balanced ordering while applying the same source, face, and
    protected-region ceilings used by the cost-aware planner.  It is intended
    as a comparison baseline, not a replacement for focused shell emission.
    """
    ordered = tuple(candidates)
    if not ordered:
        return PhysicsShellPackingPlan()
    source_limit = (
        len(ordered)
        if int(source_polygon_limit) <= 0
        else max(0, int(source_polygon_limit))
    )
    face_budget = max(0, int(generated_face_budget))
    index = consolidation_index or build_physics_shell_consolidation_index(model, ordered)
    ranked = balanced_physics_shell_candidates(ordered, len(ordered))
    groups = consolidated_physics_shell_candidate_groups(
        model,
        ranked,
        consolidation_index=index,
    )
    normalized_weights = dict(normalized_physics_shell_role_weights(role_weights))
    importance_points = tuple(_finite_vec3(point) for point in playable_importance_points)
    importance_radius = max(0.0, float(playable_importance_radius))
    importance_weight = max(0.0, float(playable_importance_weight))

    selected: List[PhysicsShellCandidateGroup] = []
    source_count = 0
    face_count = 0
    recovered_area = 0.0
    weighted_value = 0.0
    role_counts: Dict[str, int] = defaultdict(int)
    protected_indices = set()
    for group in groups:
        if physics_shell_group_intersects_bounds(
            group,
            protected_bounds,
            roles=protected_roles,
        ):
            protected_indices.update(
                int(candidate.polygon_index) for candidate in group.candidates
            )
            continue
        group_source_count = len(group.candidates)
        group_face_count = max(0, int(group.generated_face_count))
        if source_count + group_source_count > source_limit:
            continue
        if face_budget and face_count + group_face_count > face_budget:
            continue
        group_area = sum(float(candidate.area) for candidate in group.candidates)
        role_weight = max(
            (normalized_weights.get(str(candidate.role), 0.25) for candidate in group.candidates),
            default=0.25,
        )
        playable_factor = 1.0
        if importance_points and importance_radius > 0.0 and importance_weight > 0.0:
            center = tuple(
                sum(float(point[axis]) for point in group.points) / len(group.points)
                for axis in range(3)
            )
            nearest_distance = min(
                math.sqrt(sum((center[axis] - point[axis]) ** 2 for axis in range(3)))
                for point in importance_points
            )
            playable_factor += importance_weight * max(
                0.0,
                1.0 - nearest_distance / importance_radius,
            )
        selected.append(group)
        source_count += group_source_count
        face_count += group_face_count
        recovered_area += group_area
        weighted_value += group_area * role_weight * playable_factor
        for candidate in group.candidates:
            role_counts[str(candidate.role)] += 1
    return PhysicsShellPackingPlan(
        groups=tuple(selected),
        source_polygon_count=source_count,
        generated_brush_count=len(selected),
        generated_face_count=face_count,
        recovered_source_area=recovered_area,
        weighted_value=weighted_value,
        role_counts=tuple(sorted(role_counts.items())),
        protected_polygon_indices=tuple(sorted(protected_indices)),
    )


def compare_physics_shell_packing_plans(
    model: Optional[object],
    candidates: Sequence[PhysicsShellCandidate],
    *,
    source_polygon_limit: int = 0,
    generated_face_budget: int = 0,
    consolidation_index: Optional[PhysicsShellConsolidationIndex] = None,
    protected_bounds: Sequence[Tuple[Vec3, Vec3]] = (),
    protected_roles: Sequence[str] = ("side_wall",),
    role_weights: Optional[Mapping[str, float]] = None,
    playable_importance_points: Sequence[Vec3] = (),
    playable_importance_radius: float = 0.0,
    playable_importance_weight: float = 0.0,
) -> PhysicsShellPackingComparison:
    """Compare both policies without changing the generator's default mode."""
    ordered = tuple(candidates)
    index = consolidation_index or build_physics_shell_consolidation_index(model, ordered)
    common = dict(
        source_polygon_limit=source_polygon_limit,
        generated_face_budget=generated_face_budget,
        consolidation_index=index,
        protected_bounds=protected_bounds,
        protected_roles=protected_roles,
        role_weights=role_weights,
        playable_importance_points=playable_importance_points,
        playable_importance_radius=playable_importance_radius,
        playable_importance_weight=playable_importance_weight,
    )
    balanced = build_balanced_physics_shell_packing_plan(model, ordered, **common)
    cost_aware = build_physics_shell_packing_plan(model, ordered, **common)
    value_delta = cost_aware.weighted_value - balanced.weighted_value
    area_delta = cost_aware.recovered_source_area - balanced.recovered_source_area
    protected_sets_match = (
        cost_aware.protected_polygon_indices == balanced.protected_polygon_indices
    )
    meaningful_gain = value_delta > max(1.0e-6, balanced.weighted_value * 0.01)
    if meaningful_gain and protected_sets_match:
        preferred = "cost_aware"
    elif value_delta < -max(1.0e-6, balanced.weighted_value * 0.01):
        preferred = "balanced"
    else:
        preferred = "equivalent"
    notes = [
        "Comparison is advisory; balanced remains the generation default.",
    ]
    if not protected_sets_match:
        notes.append(
            "Protected polygon sets differ between policies; manual opening review is required."
        )
    return PhysicsShellPackingComparison(
        balanced=balanced,
        cost_aware=cost_aware,
        candidate_count=len(ordered),
        source_polygon_limit=(
            len(ordered) if int(source_polygon_limit) <= 0 else max(0, int(source_polygon_limit))
        ),
        generated_face_budget=max(0, int(generated_face_budget)),
        preferred_validation_mode=preferred,
        weighted_value_delta=value_delta,
        recovered_source_area_delta=area_delta,
        generated_brush_delta=(
            cost_aware.generated_brush_count - balanced.generated_brush_count
        ),
        generated_face_delta=(
            cost_aware.generated_face_count - balanced.generated_face_count
        ),
        protected_sets_match=protected_sets_match,
        notes=tuple(notes),
    )


def detect_physics_shell_stair_assemblies(
    model: Optional[object],
    candidates: Sequence[PhysicsShellCandidate],
    *,
    consolidation_index: Optional[PhysicsShellConsolidationIndex] = None,
    min_step_height: float = 3.0,
    max_step_height: float = 32.0,
    max_horizontal_gap: float = 4.0,
    elevation_tolerance: float = 1.0,
    min_elevation_levels: int = 3,
) -> Tuple[PhysicsShellStairAssembly, ...]:
    """Detect conservative connected tread/riser stair candidates.

    Floor fragments are first consolidated into coplanar tread regions.  Tread
    regions connect only when their vertical separation is a plausible step
    rise and their X/Z bounds touch or nearly touch.  Requiring at least three
    distinct elevations avoids classifying a floor plus curb as a staircase.
    """
    ordered = tuple(candidate for candidate in candidates if candidate.role != "degenerate")
    floors = tuple(candidate for candidate in ordered if candidate.role == "floor")
    if not floors:
        return ()
    safe_min_rise = max(0.1, float(min_step_height))
    safe_max_rise = max(safe_min_rise, float(max_step_height))
    safe_gap = max(0.0, float(max_horizontal_gap))
    safe_elevation_tolerance = max(1.0e-4, float(elevation_tolerance))
    safe_min_levels = max(3, int(min_elevation_levels))
    index = consolidation_index or build_physics_shell_consolidation_index(
        model,
        ordered,
    )
    tread_groups = consolidated_physics_shell_candidate_groups(
        model,
        floors,
        consolidation_index=index,
        max_group_size=_PHYSICS_SHELL_COST_AWARE_MAX_CONSOLIDATED_GROUP_SIZE,
        require_exact_large_union=True,
    )

    def group_geometry(
        group: PhysicsShellCandidateGroup,
    ) -> Tuple[float, Vec3, Vec3]:
        points = tuple(_finite_vec3(point) for point in group.points)
        elevation = sum(point[1] for point in points) / len(points)
        bounds_min = tuple(min(point[axis] for point in points) for axis in range(3))
        bounds_max = tuple(max(point[axis] for point in points) for axis in range(3))
        return elevation, bounds_min, bounds_max  # type: ignore[return-value]

    tread_geometry = tuple(group_geometry(group) for group in tread_groups)

    def horizontal_gap(left: int, right: int) -> float:
        _left_y, left_min, left_max = tread_geometry[left]
        _right_y, right_min, right_max = tread_geometry[right]
        dx = max(0.0, left_min[0] - right_max[0], right_min[0] - left_max[0])
        dz = max(0.0, left_min[2] - right_max[2], right_min[2] - left_max[2])
        return math.sqrt(dx * dx + dz * dz)

    neighbors: Dict[int, set[int]] = {index: set() for index in range(len(tread_groups))}
    for left in range(len(tread_groups)):
        left_y = tread_geometry[left][0]
        for right in range(left + 1, len(tread_groups)):
            rise = abs(left_y - tread_geometry[right][0])
            if rise < safe_min_rise or rise > safe_max_rise:
                continue
            if horizontal_gap(left, right) > safe_gap:
                continue
            neighbors[left].add(right)
            neighbors[right].add(left)

    components: List[Tuple[int, ...]] = []
    remaining = set(neighbors)
    while remaining:
        seed = min(remaining)
        stack = [seed]
        component = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(neighbors[current] - component)
        remaining.difference_update(component)
        if len(component) >= safe_min_levels:
            components.append(tuple(sorted(component)))

    side_walls = tuple(candidate for candidate in ordered if candidate.role == "side_wall")
    detected: List[PhysicsShellStairAssembly] = []
    for component in components:
        raw_elevations = sorted(tread_geometry[index][0] for index in component)
        elevations: List[float] = []
        for elevation in raw_elevations:
            if not elevations or abs(elevation - elevations[-1]) > safe_elevation_tolerance:
                elevations.append(elevation)
        if len(elevations) < safe_min_levels:
            continue
        rises = tuple(
            upper - lower for lower, upper in zip(elevations, elevations[1:])
        )
        if any(rise < safe_min_rise or rise > safe_max_rise for rise in rises):
            continue
        tread_points = tuple(
            point
            for index in component
            for point in tread_groups[index].points
        )
        tread_min = tuple(min(point[axis] for point in tread_points) for axis in range(3))
        tread_max = tuple(max(point[axis] for point in tread_points) for axis in range(3))
        risers: List[PhysicsShellCandidate] = []
        supports: List[PhysicsShellCandidate] = []
        for wall in side_walls:
            wall_min = tuple(min(point[axis] for point in wall.points) for axis in range(3))
            wall_max = tuple(max(point[axis] for point in wall.points) for axis in range(3))
            near_xz = (
                wall_max[0] >= tread_min[0] - safe_gap
                and wall_min[0] <= tread_max[0] + safe_gap
                and wall_max[2] >= tread_min[2] - safe_gap
                and wall_min[2] <= tread_max[2] + safe_gap
            )
            overlaps_y = (
                wall_max[1] >= elevations[0] - safe_elevation_tolerance
                and wall_min[1] <= elevations[-1] + safe_elevation_tolerance
            )
            if not near_xz or not overlaps_y:
                continue
            height = wall_max[1] - wall_min[1]
            if height <= safe_max_rise + safe_elevation_tolerance:
                risers.append(wall)
            else:
                supports.append(wall)
        tread_indices = tuple(sorted({
            int(candidate.polygon_index)
            for index in component
            for candidate in tread_groups[index].candidates
        }))
        riser_indices = tuple(sorted(int(item.polygon_index) for item in risers))
        support_indices = tuple(sorted(int(item.polygon_index) for item in supports))
        all_candidates = tuple(
            candidate
            for index in component
            for candidate in tread_groups[index].candidates
        ) + tuple(risers) + tuple(supports)
        all_points = tuple(point for candidate in all_candidates for point in candidate.points)
        bounds_min = tuple(min(point[axis] for point in all_points) for axis in range(3))
        bounds_max = tuple(max(point[axis] for point in all_points) for axis in range(3))
        step_count = len(elevations) - 1
        confidence = (
            "high"
            if len(elevations) >= 4 and len(risers) >= step_count
            else "medium"
            if risers
            else "candidate"
        )
        detected.append(PhysicsShellStairAssembly(
            assembly_index=0,
            source_polygon_indices=tuple(sorted(set(tread_indices + riser_indices + support_indices))),
            tread_polygon_indices=tread_indices,
            riser_polygon_indices=riser_indices,
            support_wall_polygon_indices=support_indices,
            elevation_levels=tuple(elevations),
            bounds_min=bounds_min,  # type: ignore[arg-type]
            bounds_max=bounds_max,  # type: ignore[arg-type]
            step_count=step_count,
            min_step_height=min(rises),
            max_step_height=max(rises),
            generated_face_count=sum(int(item.generated_face_count) for item in all_candidates),
            confidence=confidence,
            notes=(
                "Candidate requires route/Processor validation before atomic reservation is enabled.",
            ),
        ))
    detected.sort(key=lambda item: (item.bounds_min, item.tread_polygon_indices))
    return tuple(
        item._replace(assembly_index=index)
        for index, item in enumerate(detected)
    )


def budgeted_balanced_physics_shell_source_polygon_count(
    candidates: Sequence[PhysicsShellCandidate],
    *,
    requested_source_polygon_count: int,
    generated_polygon_budget: int,
    model: Optional[object] = None,
    consolidation_index: Optional[PhysicsShellConsolidationIndex] = None,
    analysis_cache: Optional[Dict[str, object]] = None,
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
    index = consolidation_index or build_physics_shell_consolidation_index(model, candidates)

    # Adding source polygons to the deterministic balanced ordering cannot
    # normally reduce the generated-face requirement: a new polygon either
    # joins an existing group without changing its hull or adds faces.  Use a
    # binary search here; the old linear probe became prohibitively expensive
    # once multi-polygon consolidation began exploring local combinations.
    fitted_count = 0
    fitted_groups: Tuple[PhysicsShellCandidateGroup, ...] = ()
    low = 0
    high = candidate_limit
    while low <= high:
        candidate_count = (low + high) // 2
        if candidate_count <= 0:
            low = 1
            continue
        selected = _balanced_physics_shell_candidates_from_sorted(
            by_role,
            structural_candidates,
            helper_candidates,
            candidate_count,
        )
        groups = consolidated_physics_shell_candidate_groups(
            model,
            selected,
            consolidation_index=index,
        )
        generated_count = sum(group.generated_face_count for group in groups)
        if generated_count <= budget:
            fitted_count = candidate_count
            fitted_groups = groups
            low = candidate_count + 1
        else:
            high = candidate_count - 1
    if analysis_cache is not None:
        analysis_cache["balanced_group_plan_source_polygon_count"] = fitted_count
        analysis_cache["balanced_group_plan"] = fitted_groups
    return fitted_count


def consolidated_physics_shell_candidate_groups(
    model: Optional[object],
    candidates: Sequence[PhysicsShellCandidate],
    *,
    consolidation_index: Optional[PhysicsShellConsolidationIndex] = None,
    max_group_size: int = _PHYSICS_SHELL_MAX_CONSOLIDATED_GROUP_SIZE,
    require_exact_large_union: bool = False,
) -> Tuple[PhysicsShellCandidateGroup, ...]:
    """Greedily merge safe adjacent coplanar polygons into convex boundaries.

    The default remains conservative for the balanced selector.  Cost-aware
    packing can request larger groups, but groups larger than four polygons
    must have an exact convex union: their hull area must match the sum of
    source areas, so concave regions, holes, and gaps are rejected instead of
    being filled by a synthetic slab.  Smaller groups may still represent safe
    overlapping BSP fragments where the hull does not extend beyond their
    total area.
    """
    ordered = tuple(candidates)
    safe_max_group_size = max(2, int(max_group_size))
    candidate_offsets = {
        int(candidate.polygon_index): offset
        for offset, candidate in enumerate(ordered)
    }
    candidate_polygon_indices = set(candidate_offsets)
    indexed_candidate_indices = (
        set(consolidation_index.candidate_indices)
        if consolidation_index is not None
        else set()
    )
    indexed_neighbors = (
        dict(consolidation_index.compatible_neighbor_indices)
        if consolidation_index is not None
        and candidate_polygon_indices.issubset(indexed_candidate_indices)
        else {}
    )
    by_vertex: Dict[int, List[int]] = defaultdict(list)
    if not indexed_neighbors:
        for offset, candidate in enumerate(ordered):
            for index in set(candidate.indices):
                by_vertex[int(index)].append(offset)

    used = set()
    result: List[PhysicsShellCandidateGroup] = []
    for offset, candidate in enumerate(ordered):
        if offset in used:
            continue
        best_group: Optional[Tuple[Tuple[int, ...], Tuple[Vec3, ...]]] = None

        def search(group_offsets: Tuple[int, ...]) -> None:
            nonlocal best_group
            group_candidates = tuple(ordered[item] for item in group_offsets)
            if len(group_candidates) >= 2:
                merged_points = _merged_physics_shell_group_points(
                    model,
                    group_candidates[:-1],
                    group_candidates[-1],
                    require_exact_union=(
                        require_exact_large_union and len(group_candidates) > 4
                    ),
                )
                if merged_points:
                    score = (
                        len(group_offsets),
                        -len(merged_points),
                        tuple(ordered[item].polygon_index for item in group_offsets),
                    )
                    if best_group is None:
                        best_group = (group_offsets, merged_points)
                    else:
                        best_score = (
                            len(best_group[0]),
                            -len(best_group[1]),
                            tuple(ordered[item].polygon_index for item in best_group[0]),
                        )
                        if score > best_score:
                            best_group = (group_offsets, merged_points)
            if len(group_offsets) >= safe_max_group_size:
                return
            group_index_set = set(group_offsets)
            neighbor_offsets = set()
            for group_candidate in group_candidates:
                if indexed_neighbors:
                    neighbor_offsets.update(
                        candidate_offsets[neighbor_index]
                        for neighbor_index in indexed_neighbors.get(
                            int(group_candidate.polygon_index),
                            (),
                        )
                        if neighbor_index in candidate_offsets
                    )
                else:
                    for index in set(group_candidate.indices):
                        neighbor_offsets.update(by_vertex.get(int(index), ()))
            compatible_neighbors: List[int] = []
            for neighbor_offset in sorted(neighbor_offsets):
                if neighbor_offset <= group_offsets[-1] or neighbor_offset in used or neighbor_offset in group_index_set:
                    continue
                if not _physics_shell_group_candidate_compatible(
                    model,
                    group_candidates,
                    ordered[neighbor_offset],
                ):
                    continue
                if not any(
                    len(set(item.indices) & set(ordered[neighbor_offset].indices)) >= 2
                    for item in group_candidates
                ):
                    continue
                compatible_neighbors.append(neighbor_offset)
            # Once a candidate group has four members, exact-union expansion
            # follows one deterministic frontier.  Exploring every remaining
            # combination is exponential on dense BSP triangulations and can
            # make preflight slower than Processor itself; any rejected
            # extension still leaves the already-safe four-polygon group.
            branch_limit = (
                1
                if require_exact_large_union and len(group_offsets) >= 4
                else _PHYSICS_SHELL_CONSOLIDATION_SEARCH_NEIGHBORS
            )
            for neighbor_offset in compatible_neighbors[:branch_limit]:
                search(group_offsets + (neighbor_offset,))

        search((offset,))
        if best_group is None:
            group_offsets = (offset,)
            group_points = candidate.points
        else:
            group_offsets, group_points = best_group
        group_candidates = tuple(ordered[item] for item in group_offsets)

        result.append(PhysicsShellCandidateGroup(
            candidates=tuple(group_candidates),
            points=group_points,
            generated_face_count=len(group_points) + 2,
        ))
        used.update(group_offsets)
    return tuple(result)


def _merged_physics_shell_pair_points(
    model: Optional[object],
    first: PhysicsShellCandidate,
    second: PhysicsShellCandidate,
) -> Tuple[Vec3, ...]:
    if len(set(first.indices) & set(second.indices)) < 2:
        return ()
    return _merged_physics_shell_group_points(model, (first,), second)


def _merged_physics_shell_group_points(
    model: Optional[object],
    group: Sequence[PhysicsShellCandidate],
    additional: PhysicsShellCandidate,
    *,
    require_exact_union: bool = False,
) -> Tuple[Vec3, ...]:
    if not _physics_shell_group_candidate_compatible(model, group, additional):
        return ()
    if not any(len(set(item.indices) & set(additional.indices)) >= 2 for item in group):
        return ()
    if model is not None:
        texture_names = {
            dat_polygon_texture_name(model, item.polygon).casefold()
            for item in tuple(group) + (additional,)
        }
        if len(texture_names) != 1:
            return ()

    first = group[0]
    first_normal, first_distance = polygon_plane(first.points, tuple(range(len(first.points))))
    for item in tuple(group[1:]) + (additional,):
        item_normal, item_distance = polygon_plane(item.points, tuple(range(len(item.points))))
        alignment = vec3_dot(first_normal, item_normal)
        plane_sign = 1.0 if alignment >= 0.0 else -1.0
        if abs(alignment) < 0.9999 or abs(first_distance - plane_sign * item_distance) > 0.1:
            return ()

    unique_points: List[Vec3] = []
    seen_indices = set()
    for candidate in tuple(group) + (additional,):
        for index, point in zip(candidate.indices, candidate.points):
            if index in seen_indices:
                continue
            seen_indices.add(index)
            unique_points.append(point)
    if len(unique_points) < 3:
        return ()

    drop_axis = max(range(3), key=lambda axis: abs(first_normal[axis]))
    axes = tuple(axis for axis in range(3) if axis != drop_axis)
    projected = [(point[axes[0]], point[axes[1]], offset) for offset, point in enumerate(unique_points)]
    hull_offsets = _convex_hull_point_offsets(projected)
    if len(hull_offsets) < 3:
        return ()
    hull = tuple(unique_points[offset] for offset in hull_offsets)
    merged_area = polygon_area(hull)
    source_area = sum(float(candidate.area) for candidate in tuple(group) + (additional,))
    area_tolerance = max(0.1, source_area * 1.0e-4)
    if require_exact_union and abs(merged_area - source_area) > area_tolerance:
        return ()
    if merged_area > source_area + area_tolerance:
        return ()
    if merged_area < max(float(candidate.area) for candidate in tuple(group) + (additional,)) - area_tolerance:
        return ()
    hull_normal, _distance = polygon_plane(hull, tuple(range(len(hull))))
    if vec3_dot(hull_normal, first_normal) < 0.0:
        hull = tuple(reversed(hull))
    return hull


def _physics_shell_group_candidate_compatible(
    model: Optional[object],
    group: Sequence[PhysicsShellCandidate],
    additional: PhysicsShellCandidate,
) -> bool:
    if not group or any(item.role != additional.role for item in group):
        return False
    if model is not None:
        texture_names = {
            dat_polygon_texture_name(model, item.polygon).casefold()
            for item in tuple(group) + (additional,)
        }
        if len(texture_names) != 1:
            return False
    first = group[0]
    first_normal, first_distance = polygon_plane(first.points, tuple(range(len(first.points))))
    item_normal, item_distance = polygon_plane(additional.points, tuple(range(len(additional.points))))
    alignment = vec3_dot(first_normal, item_normal)
    plane_sign = 1.0 if alignment >= 0.0 else -1.0
    return abs(alignment) >= 0.9999 and abs(first_distance - plane_sign * item_distance) <= 0.1


def _convex_hull_point_offsets(points: Sequence[Tuple[float, float, int]]) -> Tuple[int, ...]:
    ordered = sorted(points, key=lambda item: (item[0], item[1], item[2]))
    if len(ordered) <= 1:
        return tuple(item[2] for item in ordered)

    def cross(origin: Tuple[float, float, int], a: Tuple[float, float, int], b: Tuple[float, float, int]) -> float:
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])

    lower: List[Tuple[float, float, int]] = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 1.0e-8:
            lower.pop()
        lower.append(point)
    upper: List[Tuple[float, float, int]] = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 1.0e-8:
            upper.pop()
        upper.append(point)
    return tuple(item[2] for item in lower[:-1] + upper[:-1])


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


def _physics_shell_candidate_near_any_focus(
    candidate: PhysicsShellCandidate,
    focus_points: Sequence[Vec3],
    radius_sq: float,
) -> bool:
    if not candidate.points:
        return False
    point_count = float(len(candidate.points))
    center = (
        sum(point[0] for point in candidate.points) / point_count,
        sum(point[1] for point in candidate.points) / point_count,
        sum(point[2] for point in candidate.points) / point_count,
    )
    return any(vec3_distance_sq(center, focus) <= radius_sq for focus in focus_points)


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
    if mode == "multi_anchor_budget":
        return _multi_anchor_terrain_support_items(
            items,
            anchor_points,
            margin=safe_margin,
            radius=radius,
            max_items=max_items,
        )
    if mode == "playable_anchor_budget":
        return _playable_anchor_terrain_support_items(
            items,
            anchor_points,
            margin=safe_margin,
            radius=radius,
            max_items=max_items,
        )
    if mode == "playable_area_budget":
        return _playable_area_terrain_support_items(
            items,
            anchor_points,
            margin=safe_margin,
            radius=radius,
            max_items=max_items,
        )
    if mode == "adaptive_playable_area_budget":
        return _playable_area_terrain_support_items(
            items,
            anchor_points,
            margin=safe_margin,
            radius=radius,
            max_items=max_items,
            include_remote=True,
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
    if mode in {
        "multi_anchor",
        "multi_anchor_budget",
        "multi_seed",
        "multi_seed_budget",
        "spread_budget",
        "spread_anchor_budget",
        "multi_component_budget",
    }:
        return "multi_anchor_budget"
    if mode in {
        "playable_anchor",
        "playable_anchor_budget",
        "anchor_connected",
        "anchor_connected_budget",
        "anchor_neighborhood",
        "anchor_neighborhood_budget",
        "contiguous_anchor_budget",
    }:
        return "playable_anchor_budget"
    if mode in {
        "playable_area",
        "playable_area_budget",
        "gameplay_area",
        "gameplay_area_budget",
        "weighted_playable_area",
        "weighted_playable_area_budget",
        "region_allocation",
        "playable_region_allocation",
    }:
        return "playable_area_budget"
    if mode in {
        "adaptive_playable_area",
        "adaptive_playable_area_budget",
        "structural_playable_area",
        "structural_playable_area_budget",
        "playable_area_remote_fill",
        "adaptive_region_allocation",
    }:
        return "adaptive_playable_area_budget"
    return mode


def playable_terrain_area_allocations(
    items: Sequence[TerrainSupportItem],
    anchor_points: Sequence[Vec3],
    *,
    margin: float,
    radius: float,
    total_polygon_budget: int,
) -> Tuple[TerrainPlayableAreaAllocation, ...]:
    """Measure and budget connected, anchor-associated playable terrain areas."""
    return tuple(
        context.allocation
        for context in _playable_terrain_area_contexts(
            items,
            anchor_points,
            margin=margin,
            radius=radius,
            total_polygon_budget=total_polygon_budget,
        )
    )


def _playable_area_terrain_support_items(
    items: Sequence[TerrainSupportItem],
    anchor_points: Sequence[Vec3],
    *,
    margin: float,
    radius: float,
    max_items: int,
    include_remote: bool = False,
) -> Tuple[TerrainSupportItem, ...]:
    """Spend a source-polygon budget in weighted playable-area neighborhoods."""
    limit = max(0, int(max_items))
    if limit <= 0:
        return ()
    contexts = _playable_terrain_area_contexts(
        items,
        anchor_points,
        margin=margin,
        radius=radius,
        total_polygon_budget=limit,
    )
    if not contexts:
        return ()

    ordered_by_area: List[Tuple[TerrainSupportItem, ...]] = []
    for context in contexts:
        allocation = context.allocation
        ordered = tuple(_connected_terrain_support_items_within_radius(
            items,
            seed_polygon_index=allocation.seed_polygon_index,
            center_x=float(allocation.center[0]),
            center_z=float(allocation.center[2]),
            radius=radius,
            max_items=max(1, allocation.candidate_polygon_count),
        ))
        ordered_by_area.append(ordered)

    selected: List[TerrainSupportItem] = []
    selected_indices: set[int] = set()
    cursors = [0 for _context in contexts]
    for area_index, context in enumerate(contexts):
        quota = int(context.allocation.allocated_polygon_budget)
        ordered = ordered_by_area[area_index]
        added = 0
        while cursors[area_index] < len(ordered) and added < quota:
            item = ordered[cursors[area_index]]
            cursors[area_index] += 1
            if item.polygon_index in selected_indices:
                continue
            selected_indices.add(item.polygon_index)
            selected.append(item)
            added += 1
            if len(selected) >= limit:
                return tuple(selected)

    # Overlapping neighborhoods can leave part of the shared budget unused.
    # Continue each area's connected order round-robin instead of jumping to
    # unrelated remote polygons.
    made_progress = True
    while len(selected) < limit and made_progress:
        made_progress = False
        for area_index, ordered in enumerate(ordered_by_area):
            while cursors[area_index] < len(ordered):
                item = ordered[cursors[area_index]]
                cursors[area_index] += 1
                if item.polygon_index in selected_indices:
                    continue
                selected_indices.add(item.polygon_index)
                selected.append(item)
                made_progress = True
                break
            if len(selected) >= limit:
                break
    if include_remote and len(selected) < limit:
        remote_ordered_by_area = tuple(
            tuple(_connected_terrain_support_items_within_radius(
                items,
                seed_polygon_index=context.allocation.seed_polygon_index,
                center_x=float(context.allocation.center[0]),
                center_z=float(context.allocation.center[2]),
                radius=max(float(radius), 1.0) * 8.0,
                max_items=min(len(items), limit),
            ))
            for context in contexts
        )
        remote_cursors = [0 for _context in contexts]
        made_progress = True
        while len(selected) < limit and made_progress:
            made_progress = False
            for area_index, ordered in enumerate(remote_ordered_by_area):
                while remote_cursors[area_index] < len(ordered):
                    item = ordered[remote_cursors[area_index]]
                    remote_cursors[area_index] += 1
                    if item.polygon_index in selected_indices:
                        continue
                    selected_indices.add(item.polygon_index)
                    selected.append(item)
                    made_progress = True
                    break
                if len(selected) >= limit:
                    break
    return tuple(selected)


def _playable_terrain_area_contexts(
    items: Sequence[TerrainSupportItem],
    anchor_points: Sequence[Vec3],
    *,
    margin: float,
    radius: float,
    total_polygon_budget: int,
) -> Tuple[_TerrainPlayableAreaContext, ...]:
    safe_radius = max(0.0, float(radius))
    if safe_radius <= 0.0:
        raise ValueError("playable-area terrain support requires a positive radius")
    if not items or not anchor_points:
        return ()

    finite_anchors = tuple(_finite_vec3(point) for point in anchor_points)
    radius_sq = safe_radius * safe_radius
    safe_margin = max(0.0, float(margin))
    component_ids = _terrain_support_component_ids(items)
    anchor_seeds: List[Tuple[int, Vec3, int, float]] = []
    for anchor_order, anchor in enumerate(finite_anchors):
        x, z = float(anchor[0]), float(anchor[2])
        candidates = tuple(
            item
            for item in items
            if (
                _xz_bounds_overlap(
                    item.bounds,
                    x - safe_margin,
                    x + safe_margin,
                    z - safe_margin,
                    z + safe_margin,
                )
                if safe_margin > 0.0
                else _xz_bounds_distance_sq(item.bounds, x, z) <= radius_sq
            )
        )
        if not candidates:
            candidates = tuple(items)
        seed = min(
            candidates,
            key=lambda item: (
                _xz_bounds_distance_sq(item.bounds, x, z),
                -terrain_support_start_score(item),
                item.polygon_index,
            ),
        )
        distance_sq = _xz_bounds_distance_sq(seed.bounds, x, z)
        anchor_seeds.append((
            anchor_order,
            anchor,
            int(seed.polygon_index),
            float(distance_sq),
        ))

    relevant = [
        record for record in anchor_seeds if record[3] <= radius_sq
    ]
    if not relevant:
        relevant = [min(
            anchor_seeds,
            key=lambda record: (
                record[3],
                record[0],
                record[2],
            ),
        )]

    cluster_distance = max(256.0, min(2048.0, safe_radius * 0.5))
    cluster_distance_sq = cluster_distance * cluster_distance
    unused = set(range(len(relevant)))
    clusters: List[Tuple[Tuple[int, Vec3, int, float], ...]] = []
    while unused:
        start = min(unused)
        unused.remove(start)
        cluster_indices = {start}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            current_record = relevant[current]
            current_component = component_ids.get(current_record[2], -1)
            for other in tuple(sorted(unused)):
                other_record = relevant[other]
                if component_ids.get(other_record[2], -2) != current_component:
                    continue
                dx = current_record[1][0] - other_record[1][0]
                dz = current_record[1][2] - other_record[1][2]
                if dx * dx + dz * dz > cluster_distance_sq:
                    continue
                unused.remove(other)
                cluster_indices.add(other)
                queue.append(other)
        clusters.append(tuple(
            relevant[index] for index in sorted(cluster_indices)
        ))

    contexts: List[_TerrainPlayableAreaContext] = []
    for area_index, cluster in enumerate(clusters):
        center = (
            sum(record[1][0] for record in cluster) / len(cluster),
            sum(record[1][1] for record in cluster) / len(cluster),
            sum(record[1][2] for record in cluster) / len(cluster),
        )
        seed_record = min(
            cluster,
            key=lambda record: (
                (record[1][0] - center[0]) ** 2
                + (record[1][2] - center[2]) ** 2,
                record[0],
                record[2],
            ),
        )
        seed_item = next(
            item
            for item in items
            if int(item.polygon_index) == int(seed_record[2])
        )
        growth_center = (
            center
            if _xz_bounds_distance_sq(
                seed_item.bounds,
                float(center[0]),
                float(center[2]),
            )
            <= radius_sq
            else seed_item.center
        )
        candidates = tuple(_connected_terrain_support_items_within_radius(
            items,
            seed_polygon_index=seed_record[2],
            center_x=float(growth_center[0]),
            center_z=float(growth_center[2]),
            radius=safe_radius,
            max_items=0,
        ))
        if not candidates:
            continue
        min_x = min(item.bounds[0] for item in candidates)
        max_x = max(item.bounds[1] for item in candidates)
        min_z = min(item.bounds[2] for item in candidates)
        max_z = max(item.bounds[3] for item in candidates)
        walkable_items = tuple(
            item for item in candidates if terrain_support_item_is_walkable(item)
        )
        walkable_xz_area = sum(
            terrain_support_item_walkable_xz_area(item)
            for item in walkable_items
        )
        anchor_multiplier = 1.0 + 0.15 * float(len(cluster) - 1)
        weight = max(
            1.0,
            walkable_xz_area
            if walkable_xz_area > 0.0
            else float(len(candidates)),
        ) * anchor_multiplier
        contexts.append(_TerrainPlayableAreaContext(
            TerrainPlayableAreaAllocation(
                area_index=len(contexts),
                seed_polygon_index=int(seed_record[2]),
                anchor_count=len(cluster),
                center=growth_center,
                bounds=(min_x, max_x, min_z, max_z),
                candidate_polygon_count=len(candidates),
                walkable_polygon_count=len(walkable_items),
                walkable_xz_area=float(walkable_xz_area),
                allocation_weight=float(weight),
                allocated_polygon_budget=0,
            ),
            candidates,
        ))

    if not contexts:
        return ()
    allocations = _bounded_playable_area_allocations(
        tuple(
            context.allocation.candidate_polygon_count
            for context in contexts
        ),
        tuple(context.allocation.allocation_weight for context in contexts),
        total_budget=max(0, int(total_polygon_budget)),
    )
    return tuple(
        _TerrainPlayableAreaContext(
            context.allocation._replace(
                allocated_polygon_budget=int(allocations[index]),
            ),
            context.candidate_items,
        )
        for index, context in enumerate(contexts)
    )


def _bounded_playable_area_allocations(
    capacities: Sequence[int],
    weights: Sequence[float],
    *,
    total_budget: int,
) -> Tuple[int, ...]:
    budget = min(
        max(0, int(total_budget)),
        sum(max(0, int(capacity)) for capacity in capacities),
    )
    allocations = [0 for _capacity in capacities]
    active = [
        index for index, capacity in enumerate(capacities)
        if int(capacity) > 0
    ]
    if not active or budget <= 0:
        return tuple(allocations)
    if budget < len(active):
        ranked = sorted(
            active,
            key=lambda index: (
                -max(1.0, float(weights[index])),
                index,
            ),
        )
        for index in ranked[:budget]:
            allocations[index] = 1
        return tuple(allocations)

    reserve = min(
        16,
        max(1, budget // max(1, len(active) * 8)),
    )
    for index in active:
        grant = min(int(capacities[index]), reserve, budget)
        allocations[index] += grant
        budget -= grant
        if budget <= 0:
            return tuple(allocations)

    while budget > 0:
        available = [
            index for index in active
            if allocations[index] < int(capacities[index])
        ]
        if not available:
            break
        weight_total = sum(max(1.0, float(weights[index])) for index in available)
        quotas = {
            index: budget * max(1.0, float(weights[index])) / weight_total
            for index in available
        }
        granted = 0
        for index in available:
            room = int(capacities[index]) - allocations[index]
            grant = min(room, int(math.floor(quotas[index])))
            allocations[index] += grant
            granted += grant
        budget -= granted
        if budget <= 0:
            break
        ranked = sorted(
            (
                index for index in available
                if allocations[index] < int(capacities[index])
            ),
            key=lambda index: (
                -(quotas[index] - math.floor(quotas[index])),
                index,
            ),
        )
        if not ranked:
            break
        for index in ranked:
            if budget <= 0:
                break
            allocations[index] += 1
            budget -= 1
    return tuple(allocations)


def _terrain_support_component_ids(
    items: Sequence[TerrainSupportItem],
) -> Dict[int, int]:
    by_vertex: Dict[int, List[int]] = defaultdict(list)
    for local_index, item in enumerate(items):
        for vertex_index in item.indices:
            by_vertex[int(vertex_index)].append(local_index)
    component_ids: Dict[int, int] = {}
    remaining = set(range(len(items)))
    component_index = 0
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        queue = deque([start])
        while queue:
            current = queue.popleft()
            item = items[current]
            component_ids[int(item.polygon_index)] = component_index
            for vertex_index in item.indices:
                for neighbor in by_vertex.get(int(vertex_index), ()):
                    if neighbor not in remaining:
                        continue
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        component_index += 1
    return component_ids


def terrain_support_item_walkable_xz_area(item: TerrainSupportItem) -> float:
    """Return upward-projected XZ surface area for one support polygon."""
    normal, _dist = polygon_plane(
        item.points,
        tuple(range(len(item.points))),
    )
    return polygon_area(item.points) * max(0.0, float(normal[1]))


def terrain_support_item_is_walkable(item: TerrainSupportItem) -> bool:
    """Return whether a support polygon is a plausible traversable surface."""
    normal, _dist = polygon_plane(
        item.points,
        tuple(range(len(item.points))),
    )
    return (
        float(normal[1]) >= 0.45
        and terrain_support_item_walkable_xz_area(item) > 0.25
    )


def _playable_anchor_terrain_support_items(
    items: Sequence[TerrainSupportItem],
    anchor_points: Sequence[Vec3],
    *,
    margin: float,
    radius: float,
    max_items: int,
) -> Tuple[TerrainSupportItem, ...]:
    """Grow contiguous neighborhoods from DAT gameplay anchors only."""
    safe_radius = max(0.0, float(radius))
    limit = max(0, int(max_items))
    if safe_radius <= 0.0:
        raise ValueError("playable-anchor terrain support requires a positive radius")
    if limit <= 0 or not items or not anchor_points:
        return ()

    safe_margin = max(0.0, float(margin))
    radius_sq = safe_radius * safe_radius
    seeds: List[Tuple[int, Vec3]] = []
    seen_seed_polygons: set[int] = set()
    item_by_polygon = {
        int(item.polygon_index): item for item in items
    }
    for raw_anchor in anchor_points:
        anchor = _finite_vec3(raw_anchor)
        x, z = anchor[0], anchor[2]
        local_candidates = [
            item
            for item in items
            if _xz_bounds_overlap(
                item.bounds,
                x - safe_margin,
                x + safe_margin,
                z - safe_margin,
                z + safe_margin,
            )
        ]
        if not local_candidates:
            local_candidates = [
                item
                for item in items
                if _xz_bounds_distance_sq(item.bounds, x, z) <= radius_sq
            ]
        if not local_candidates:
            local_candidates = list(items)
        seed_item = min(
            local_candidates,
            key=lambda item: (
                _xz_bounds_distance_sq(item.bounds, x, z),
                -terrain_support_start_score(item),
                item.polygon_index,
            ),
        )
        polygon_index = int(seed_item.polygon_index)
        if polygon_index in seen_seed_polygons:
            continue
        seen_seed_polygons.add(polygon_index)
        distance_sq = _xz_bounds_distance_sq(seed_item.bounds, x, z)
        growth_center = (
            anchor
            if distance_sq <= radius_sq
            else seed_item.center
        )
        seeds.append((polygon_index, growth_center))

    if not seeds:
        return ()
    if len(seeds) > limit:
        seeds = seeds[:limit]

    base_quota, remainder = divmod(limit, len(seeds))
    selected: List[TerrainSupportItem] = []
    selected_polygon_indices: set[int] = set()
    for seed_order, (seed_index, center) in enumerate(seeds):
        quota = base_quota + (1 if seed_order < remainder else 0)
        neighborhood = _connected_terrain_support_items_within_radius(
            items,
            seed_polygon_index=seed_index,
            center_x=float(center[0]),
            center_z=float(center[2]),
            radius=safe_radius,
            max_items=quota,
        )
        for item in neighborhood:
            if item.polygon_index in selected_polygon_indices:
                continue
            selected_polygon_indices.add(item.polygon_index)
            selected.append(item)
            if len(selected) >= limit:
                return tuple(selected)

    if len(selected) < limit:
        seed_centers = tuple(
            item_by_polygon[index].center for index, _center in seeds
        )
        remaining = [
            item
            for item in items
            if item.polygon_index not in selected_polygon_indices
        ]
        remaining.sort(
            key=lambda item: (
                min(
                    (item.center[0] - center[0]) ** 2
                    + (item.center[2] - center[2]) ** 2
                    for center in seed_centers
                ),
                -terrain_support_start_score(item),
                item.polygon_index,
            )
        )
        for item in remaining:
            selected.append(item)
            if len(selected) >= limit:
                break
    return tuple(selected)


def _multi_anchor_terrain_support_items(
    items: Sequence[TerrainSupportItem],
    anchor_points: Sequence[Vec3],
    *,
    margin: float,
    radius: float,
    max_items: int,
) -> Tuple[TerrainSupportItem, ...]:
    """Select connected terrain neighborhoods around multiple world anchors.

    Outdoor worlds often contain several separated playable areas. A single
    connected-budget seed can spend the entire budget in the first component;
    this selector reserves a deterministic share for each distinct anchor
    neighborhood, then grows each neighborhood through shared terrain vertices.
    """
    safe_radius = max(0.0, float(radius))
    limit = max(0, int(max_items))
    if safe_radius <= 0.0:
        raise ValueError("multi-anchor terrain support requires a positive radius")
    if limit <= 0 or not items or not anchor_points:
        return ()

    safe_margin = max(0.0, float(margin))
    radius_sq = safe_radius * safe_radius
    seeds: List[Tuple[int, Vec3]] = []
    seen_seed_polygons = set()
    for raw_anchor in anchor_points:
        anchor = _finite_vec3(raw_anchor)
        x, z = anchor[0], anchor[2]
        local_candidates = [
            (index, item)
            for index, item in enumerate(items)
            if _xz_bounds_overlap(
                item.bounds,
                x - safe_margin,
                x + safe_margin,
                z - safe_margin,
                z + safe_margin,
            )
        ]
        if not local_candidates:
            local_candidates = [
                (index, item)
                for index, item in enumerate(items)
                if _xz_bounds_distance_sq(item.bounds, x, z) <= radius_sq
            ]
        if not local_candidates:
            continue
        seed_index, seed_item = max(
            local_candidates,
            key=lambda pair: (
                terrain_support_start_score(pair[1]),
                -_xz_bounds_distance_sq(pair[1].bounds, x, z),
                -pair[1].polygon_index,
            ),
        )
        polygon_index = int(seed_item.polygon_index)
        if polygon_index in seen_seed_polygons:
            continue
        seen_seed_polygons.add(polygon_index)
        seeds.append((polygon_index, anchor))

    if not seeds:
        return ()
    target_seed_count = min(limit, max(len(seeds), 128))
    if len(seeds) < target_seed_count:
        seed_centers = [
            next(item.center for item in items if item.polygon_index == polygon_index)
            for polygon_index, _anchor in seeds
        ]
        available = [
            item for item in items
            if item.polygon_index not in seen_seed_polygons
        ]
        while available and len(seeds) < target_seed_count:
            candidate = max(
                available,
                key=lambda item: (
                    min(
                        (item.center[0] - center[0]) ** 2
                        + (item.center[2] - center[2]) ** 2
                        for center in seed_centers
                    ),
                    terrain_support_start_score(item),
                    -item.polygon_index,
                ),
            )
            available.remove(candidate)
            seen_seed_polygons.add(int(candidate.polygon_index))
            seeds.append((int(candidate.polygon_index), candidate.center))
            seed_centers.append(candidate.center)
    if len(seeds) > limit:
        # Keep spatially distributed anchors instead of letting early model
        # order consume every available seed slot.
        stride = float(len(seeds)) / float(limit)
        seeds = [seeds[min(len(seeds) - 1, int(index * stride))] for index in range(limit)]

    base_quota, remainder = divmod(limit, len(seeds))
    selected: List[TerrainSupportItem] = []
    selected_polygon_indices = set()
    for seed_order, (seed_index, anchor) in enumerate(seeds):
        quota = base_quota + (1 if seed_order < remainder else 0)
        if quota <= 0:
            continue
        neighborhood = _connected_terrain_support_items_within_radius(
            items,
            seed_polygon_index=seed_index,
            center_x=float(anchor[0]),
            center_z=float(anchor[2]),
            radius=safe_radius,
            max_items=quota,
        )
        for item in neighborhood:
            if item.polygon_index in selected_polygon_indices:
                continue
            selected_polygon_indices.add(item.polygon_index)
            selected.append(item)
            if len(selected) >= limit:
                return tuple(selected)
    if len(selected) < limit:
        # Some compiled terrain tiles do not share vertex indices across local
        # seams. Fill any unused budget by nearest unselected tiles so sparse
        # outdoor islands still receive support instead of under-filling the
        # Processor allowance.
        anchors = tuple(_finite_vec3(point) for point in anchor_points)
        remaining = [
            item for item in items
            if item.polygon_index not in selected_polygon_indices
        ]
        remaining.sort(
            key=lambda item: (
                min(_xz_bounds_distance_sq(item.bounds, point[0], point[2]) for point in anchors),
                -terrain_support_start_score(item),
                item.polygon_index,
            )
        )
        for item in remaining:
            selected_polygon_indices.add(item.polygon_index)
            selected.append(item)
            if len(selected) >= limit:
                break
    return tuple(selected)


def normalize_terrain_support_brush_mode(value: object) -> str:
    mode = str(value or "single_polygon").strip().lower().replace("-", "_")
    if mode in {"", "single", "single_polygon", "polygon", "polygon_prism", "polygon_prisms"}:
        return "single_polygon"
    if mode in {"paired", "paired_triangles", "triangle_pairs", "cell", "cell_prisms", "terrain_cells"}:
        return "paired_triangles"
    if mode in {
        "adjacent",
        "adjacent_convex",
        "adjacent_regions",
        "convex_regions",
        "connected_regions",
        "continuous_regions",
    }:
        return "adjacent_convex"
    if mode in {
        "adaptive",
        "adaptive_structural",
        "adaptive_patches",
        "structural_patches",
        "compressed_structural",
        "terrain_compression",
    }:
        return "adaptive_structural"
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


def _point_in_triangle_xz_y(
    x: float,
    z: float,
    first: Vec3,
    second: Vec3,
    third: Vec3,
) -> Optional[float]:
    ax, az = float(first[0]), float(first[2])
    bx, bz = float(second[0]), float(second[2])
    cx, cz = float(third[0]), float(third[2])
    v0 = (cx - ax, cz - az)
    v1 = (bx - ax, bz - az)
    v2 = (float(x) - ax, float(z) - az)
    denominator = v0[0] * v1[1] - v1[0] * v0[1]
    if abs(denominator) <= 1.0e-7:
        return None
    u = (v2[0] * v1[1] - v1[0] * v2[1]) / denominator
    v = (v0[0] * v2[1] - v2[0] * v0[1]) / denominator
    if u < -1.0e-5 or v < -1.0e-5 or u + v > 1.00001:
        return None
    return (
        float(first[1])
        + u * (float(third[1]) - float(first[1]))
        + v * (float(second[1]) - float(first[1]))
    )


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
