"""Preview-only OBJ + DAT sidecar import for additive geometry."""

from __future__ import annotations

import copy
import json
import math
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from core import bsp


Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class ImportedMeshModel:
    name: str
    mesh: bsp.WorldModelMesh
    source_object_name: str = ""
    role: str = "visible"


@dataclass(frozen=True)
class MeshBspImportPlan:
    obj_path: str
    meta_path: str
    new_name: str
    target_pos: Vec3
    target_yaw: float
    original_center: Vec3
    collision_mode: str = "none"
    models: List[ImportedMeshModel] = field(default_factory=list)


def suggest_import_name(target_bsp: bsp.BspWorld, obj_path: str) -> str:
    stem = os.path.splitext(os.path.basename(obj_path))[0] or "MeshImport"
    stem = re.sub(r"_geometry$", "", stem, flags=re.IGNORECASE)
    stem = _sanitize_model_name(stem)
    existing = {
        str(getattr(model, "name", "") or "").lower()
        for model in getattr(target_bsp, "world_models", []) or []
    }
    if stem.lower() not in existing:
        return stem
    index = 1
    while f"{stem}{index}".lower() in existing:
        index += 1
    return f"{stem}{index}"


def build_mesh_import_plan(
    target_bsp: bsp.BspWorld,
    obj_path: str,
    meta_path: Optional[str] = None,
    new_name: Optional[str] = None,
    target_pos: Optional[Sequence[float]] = None,
    target_yaw: float = 0.0,
    collision_mode: str = "none",
    collision_thickness: float = 8.0,
    collision_segment_length: float = 512.0,
) -> MeshBspImportPlan:
    meta_path = meta_path or _default_meta_path(obj_path)
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    material_to_texture = {
        str(item.get("material_name") or ""): str(item.get("texture_name") or "Default")
        for item in meta.get("materials", []) or []
    }
    export_to_dat = meta.get("coordinate_system", {}).get("export_to_dat_matrix")
    if not export_to_dat:
        export_to_dat = _identity_matrix()

    parsed = _parse_obj(obj_path)
    if not parsed:
        raise ValueError(f"{obj_path!r} has no mesh objects with faces")

    prefix = _sanitize_model_name(new_name or suggest_import_name(target_bsp, obj_path))
    names = _new_model_names(prefix, [obj.name for obj in parsed])
    collision_mode = _normalize_collision_mode(collision_mode)

    imported: List[ImportedMeshModel] = []
    for parsed_obj, model_name in zip(parsed, names):
        mesh = _parsed_obj_to_mesh(parsed_obj, model_name, material_to_texture, export_to_dat)
        imported.append(ImportedMeshModel(
            name=model_name,
            mesh=mesh,
            source_object_name=parsed_obj.name,
            role="visible",
        ))

    original_center = _combined_center([item.mesh for item in imported])
    target = _as_vec3(target_pos, "target_pos") if target_pos is not None else original_center
    transformed = [
        ImportedMeshModel(
            name=item.name,
            mesh=_transform_mesh(item.mesh, original_center, target, float(target_yaw)),
            source_object_name=item.source_object_name,
            role=item.role,
        )
        for item in imported
    ]
    collision_models = _build_collision_models(
        transformed,
        collision_mode=collision_mode,
        collision_thickness=float(collision_thickness),
        collision_segment_length=float(collision_segment_length),
    )
    all_models = [*transformed, *collision_models]
    _validate_model_names(target_bsp, [item.name for item in all_models])
    return MeshBspImportPlan(
        obj_path=os.path.abspath(obj_path),
        meta_path=os.path.abspath(meta_path),
        new_name=prefix,
        target_pos=target,
        target_yaw=float(target_yaw),
        original_center=original_center,
        collision_mode=collision_mode,
        models=all_models,
    )


def build_preview_bsp(
    target_bsp: bsp.BspWorld,
    import_plans: Sequence[MeshBspImportPlan],
) -> bsp.BspWorld:
    preview = bsp.BspWorld(
        version=target_bsp.version,
        world_info=target_bsp.world_info,
        obj_pos=target_bsp.obj_pos,
        ren_pos=target_bsp.ren_pos,
        world_model_table_start=target_bsp.world_model_table_start,
        world_models=list(target_bsp.world_models),
        parse_warnings=list(getattr(target_bsp, "parse_warnings", []) or []),
    )
    for plan in import_plans or []:
        for item in plan.models:
            preview.world_models.append(copy.deepcopy(item.mesh))
    return preview


def object_specs(plan: MeshBspImportPlan) -> List[Tuple[str, Vec3]]:
    return [
        (item.name, _bounds_center(item.mesh.min_box, item.mesh.max_box))
        for item in plan.models
    ]


def is_collision_model(item: ImportedMeshModel) -> bool:
    return item.role in {"collision_helper", "collision_box"}


@dataclass
class _ParsedFace:
    vertex_indices: List[int]
    material_name: str


@dataclass
class _ParsedObject:
    name: str
    points: List[Vec3] = field(default_factory=list)
    faces: List[_ParsedFace] = field(default_factory=list)


def _parse_obj(path: str) -> List[_ParsedObject]:
    objects: List[_ParsedObject] = []
    current = _ParsedObject(name=os.path.splitext(os.path.basename(path))[0] or "Mesh")
    material = "Default"
    global_points: List[Vec3] = []
    local_map: Dict[int, int] = {}

    def finish_current() -> None:
        nonlocal current, local_map
        if current.faces:
            objects.append(current)
        current = _ParsedObject(name=current.name)
        local_map = {}

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            head = parts[0]
            if head == "v" and len(parts) >= 4:
                global_points.append((float(parts[1]), float(parts[2]), float(parts[3])))
                continue
            if head in {"o", "g"} and len(parts) >= 2:
                if current.faces:
                    finish_current()
                current.name = " ".join(parts[1:])
                continue
            if head == "usemtl" and len(parts) >= 2:
                material = " ".join(parts[1:])
                continue
            if head == "f" and len(parts) >= 4:
                face_indices: List[int] = []
                for token in parts[1:]:
                    vertex_text = token.split("/", 1)[0]
                    if not vertex_text:
                        continue
                    obj_index = int(vertex_text)
                    global_index = obj_index - 1 if obj_index > 0 else len(global_points) + obj_index
                    if global_index < 0 or global_index >= len(global_points):
                        continue
                    if global_index not in local_map:
                        local_map[global_index] = len(current.points)
                        current.points.append(global_points[global_index])
                    face_indices.append(local_map[global_index])
                if len(face_indices) >= 3:
                    current.faces.append(_ParsedFace(face_indices, material))
    if current.faces:
        objects.append(current)
    return objects


def _parsed_obj_to_mesh(
    parsed: _ParsedObject,
    model_name: str,
    material_to_texture: Dict[str, str],
    export_to_dat: Sequence[Sequence[float]],
) -> bsp.WorldModelMesh:
    points = [_matrix_point(export_to_dat, point) for point in parsed.points]
    texture_names: List[str] = []
    surface_by_texture: Dict[str, int] = {}
    surfaces: List[bsp.Surface] = []
    polygons: List[bsp.Polygon] = []

    for face in parsed.faces:
        texture = material_to_texture.get(face.material_name, face.material_name or "Default")
        if texture not in surface_by_texture:
            surface_by_texture[texture] = len(surfaces)
            texture_names.append(texture)
            surfaces.append(bsp.Surface(
                uv_o=(0.0, 0.0, 0.0),
                uv_p=(1.0, 0.0, 0.0),
                uv_q=(0.0, 0.0, 1.0),
                texture_index=len(texture_names) - 1,
                flags=0,
                texture_flags=0,
            ))
        # Imported from the default exporter, OBJ faces were reversed for the
        # Blender-space reflection; after applying the inverse reflection, turn
        # them back into DAT winding.
        indices = list(reversed(face.vertex_indices))
        polygons.append(bsp.Polygon(
            vertex_indices=indices,
            surface_index=surface_by_texture[texture],
            plane_index=0,
        ))

    min_box, max_box = _bounds(points)
    return bsp.WorldModelMesh(
        name=model_name,
        min_box=min_box,
        max_box=max_box,
        translation=(0.0, 0.0, 0.0),
        points=points,
        polygons=polygons,
        texture_names=texture_names or ["Default"],
        surfaces=surfaces,
    )


def _transform_mesh(
    mesh: bsp.WorldModelMesh,
    source_center: Vec3,
    target_center: Vec3,
    yaw_radians: float,
) -> bsp.WorldModelMesh:
    moved = copy.deepcopy(mesh)
    moved.points = [
        _transform_point(point, source_center, target_center, yaw_radians)
        for point in moved.points
    ]
    moved.min_box, moved.max_box = _bounds(moved.points)
    moved.translation = _transform_point(mesh.translation, source_center, target_center, yaw_radians)
    for surface in moved.surfaces:
        surface.uv_o = _transform_point(surface.uv_o, source_center, target_center, yaw_radians)
        surface.uv_p = _rotate_y(surface.uv_p, yaw_radians)
        surface.uv_q = _rotate_y(surface.uv_q, yaw_radians)
    return moved


def _build_collision_models(
    visible_models: Sequence[ImportedMeshModel],
    *,
    collision_mode: str,
    collision_thickness: float,
    collision_segment_length: float,
) -> List[ImportedMeshModel]:
    if collision_mode == "none":
        return []
    if collision_mode == "invisible_bsp":
        return [
            ImportedMeshModel(
                name=f"{item.name}_Collision",
                mesh=_rename_mesh(item.mesh, f"{item.name}_Collision"),
                source_object_name=item.source_object_name,
                role="collision_helper",
            )
            for item in visible_models
        ]
    if collision_mode != "box_approx":
        raise ValueError(f"unsupported mesh collision mode: {collision_mode!r}")

    result: List[ImportedMeshModel] = []
    for item in visible_models:
        thin_min, thin_max = _thin_collision_bounds(item.mesh.min_box, item.mesh.max_box, collision_thickness)
        segments = _segment_collision_bounds(thin_min, thin_max, collision_segment_length)
        names = _collision_segment_names(item.name, len(segments))
        for name, (min_box, max_box) in zip(names, segments):
            result.append(ImportedMeshModel(
                name=name,
                mesh=_make_box_mesh(name, min_box, max_box),
                source_object_name=item.source_object_name,
                role="collision_box",
            ))
    return result


def _rename_mesh(mesh: bsp.WorldModelMesh, name: str) -> bsp.WorldModelMesh:
    renamed = copy.deepcopy(mesh)
    renamed.name = name
    return renamed


def _make_box_mesh(name: str, min_box: Vec3, max_box: Vec3) -> bsp.WorldModelMesh:
    x0, y0, z0 = min_box
    x1, y1, z1 = max_box
    points = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    polygons = [
        bsp.Polygon([0, 1, 2, 3], 0, 0),
        bsp.Polygon([5, 4, 7, 6], 0, 0),
        bsp.Polygon([4, 0, 3, 7], 0, 0),
        bsp.Polygon([1, 5, 6, 2], 0, 0),
        bsp.Polygon([3, 2, 6, 7], 0, 0),
        bsp.Polygon([4, 5, 1, 0], 0, 0),
    ]
    texture = "TEXTURES\\LevelTextures\\Misc\\Firethrough.dtx"
    return bsp.WorldModelMesh(
        name=name,
        min_box=min_box,
        max_box=max_box,
        translation=(0.0, 0.0, 0.0),
        points=points,
        polygons=polygons,
        texture_names=[texture],
        surfaces=[
            bsp.Surface(
                uv_o=min_box,
                uv_p=(1.0, 0.0, 0.0),
                uv_q=(0.0, 0.0, 1.0),
                texture_index=0,
                flags=0,
                texture_flags=0,
            )
        ],
    )


def _collision_segment_names(model_name: str, segment_count: int) -> List[str]:
    if segment_count <= 1:
        return [f"{model_name}_Collision"]
    return [f"{model_name}_Collision{index}" for index in range(1, segment_count + 1)]


def _normalize_collision_mode(value: str) -> str:
    mode = str(value or "none").lower()
    if mode in {"none", "off", "false", "0"}:
        return "none"
    if mode in {"invisible_bsp", "collision_helper"}:
        return "invisible_bsp"
    if mode in {"box", "box_approx"}:
        return "box_approx"
    raise ValueError(f"unsupported mesh collision mode: {value!r}")


def _thin_collision_bounds(min_box: Vec3, max_box: Vec3, thickness: float = 8.0) -> Tuple[Vec3, Vec3]:
    mins = [float(v) for v in min_box]
    maxs = [float(v) for v in max_box]
    x_size = maxs[0] - mins[0]
    z_size = maxs[2] - mins[2]
    if x_size <= 0.0 or z_size <= 0.0:
        return (tuple(mins), tuple(maxs))  # type: ignore[return-value]
    thin_axis = 0 if x_size <= z_size else 2
    target_thickness = max(1.0, float(thickness))
    thickness = min(maxs[thin_axis] - mins[thin_axis], target_thickness)
    center = (mins[thin_axis] + maxs[thin_axis]) * 0.5
    mins[thin_axis] = center - thickness * 0.5
    maxs[thin_axis] = center + thickness * 0.5
    return (tuple(mins), tuple(maxs))  # type: ignore[return-value]


def _segment_collision_bounds(
    min_box: Vec3,
    max_box: Vec3,
    segment_length: float = 512.0,
) -> List[Tuple[Vec3, Vec3]]:
    mins = [float(v) for v in min_box]
    maxs = [float(v) for v in max_box]
    max_segment = max(64.0, float(segment_length))
    x_size = maxs[0] - mins[0]
    z_size = maxs[2] - mins[2]
    axis = 0 if x_size >= z_size else 2
    length = maxs[axis] - mins[axis]
    if length <= max_segment:
        return [(tuple(mins), tuple(maxs))]  # type: ignore[list-item]
    count = max(1, int((length + max_segment - 1.0) // max_segment))
    step = length / count
    segments: List[Tuple[Vec3, Vec3]] = []
    for index in range(count):
        seg_min = list(mins)
        seg_max = list(maxs)
        seg_min[axis] = mins[axis] + step * index
        seg_max[axis] = maxs[axis] if index == count - 1 else mins[axis] + step * (index + 1)
        segments.append((tuple(seg_min), tuple(seg_max)))  # type: ignore[arg-type]
    return segments


def _transform_point(point: Vec3, source_center: Vec3, target_center: Vec3, yaw_radians: float) -> Vec3:
    dx = float(point[0]) - float(source_center[0])
    dy = float(point[1]) - float(source_center[1])
    dz = float(point[2]) - float(source_center[2])
    c = math.cos(yaw_radians)
    s = math.sin(yaw_radians)
    return (
        float(target_center[0]) + dx * c + dz * s,
        float(target_center[1]) + dy,
        float(target_center[2]) - dx * s + dz * c,
    )


def _rotate_y(vector: Vec3, yaw_radians: float) -> Vec3:
    x, y, z = vector
    c = math.cos(yaw_radians)
    s = math.sin(yaw_radians)
    return (x * c + z * s, y, -x * s + z * c)


def _matrix_point(matrix: Sequence[Sequence[float]], point: Vec3) -> Vec3:
    x, y, z = point
    return (
        float(matrix[0][0]) * x + float(matrix[0][1]) * y + float(matrix[0][2]) * z + float(matrix[0][3]),
        float(matrix[1][0]) * x + float(matrix[1][1]) * y + float(matrix[1][2]) * z + float(matrix[1][3]),
        float(matrix[2][0]) * x + float(matrix[2][1]) * y + float(matrix[2][2]) * z + float(matrix[2][3]),
    )


def _bounds(points: Sequence[Vec3]) -> Tuple[Vec3, Vec3]:
    if not points:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    return (
        (min(p[0] for p in points), min(p[1] for p in points), min(p[2] for p in points)),
        (max(p[0] for p in points), max(p[1] for p in points), max(p[2] for p in points)),
    )


def _combined_center(meshes: Sequence[bsp.WorldModelMesh]) -> Vec3:
    mins, maxs = _bounds([
        point
        for mesh in meshes
        for point in (mesh.min_box, mesh.max_box)
    ])
    return _bounds_center(mins, maxs)


def _bounds_center(min_box: Vec3, max_box: Vec3) -> Vec3:
    return (
        (float(min_box[0]) + float(max_box[0])) * 0.5,
        (float(min_box[1]) + float(max_box[1])) * 0.5,
        (float(min_box[2]) + float(max_box[2])) * 0.5,
    )


def _new_model_names(prefix: str, source_names: Sequence[str]) -> List[str]:
    if len(source_names) == 1:
        return [prefix]
    names: List[str] = []
    seen: set[str] = set()
    for index, source_name in enumerate(source_names, start=1):
        suffix = _sanitize_model_name(source_name or f"Mesh{index}")
        name = f"{prefix}_{suffix}"
        if name.lower() in seen:
            name = f"{name}{index}"
        seen.add(name.lower())
        names.append(name)
    return names


def _validate_model_names(target_bsp: bsp.BspWorld, names: Sequence[str]) -> None:
    existing = {
        str(getattr(model, "name", "") or "").lower()
        for model in getattr(target_bsp, "world_models", []) or []
    }
    lowered = [name.lower() for name in names]
    if len(set(lowered)) != len(lowered):
        raise ValueError(f"imported mesh model names must be unique: {', '.join(names)}")
    for name in names:
        if not name.strip():
            raise ValueError("imported mesh model names must be non-empty")
        if name.lower() in existing:
            raise ValueError(f"BSP model named {name!r} already exists in the target level")


def _sanitize_model_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "", str(value or ""))
    return cleaned or "MeshImport"


def _default_meta_path(obj_path: str) -> str:
    base, ext = os.path.splitext(obj_path)
    if base.lower().endswith("_geometry"):
        return f"{base}.datmeta.json"
    return f"{base}.datmeta.json" if ext else f"{obj_path}.datmeta.json"


def _identity_matrix() -> List[List[float]]:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _as_vec3(value: object, prop_name: str) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{prop_name} must be a 3-vector, got {value!r}")
    return (float(value[0]), float(value[1]), float(value[2]))
