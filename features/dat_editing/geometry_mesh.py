"""GeometryScene to BSP mesh helpers for retained DAT -> ED tooling."""

from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from core import bsp
from features.dat_editing import geometry_scene
from features.dat_editing import uv_projection


Vec2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]


def material_texture_map_from_meta(meta: Dict[str, object]) -> Dict[str, str]:
    return {
        str(item.get("material_name") or ""): str(item.get("texture_name") or "Default")
        for item in meta.get("materials", []) or []
        if isinstance(item, dict)
    }


def load_obj_geometry_scene(
    path: str,
    meta: Optional[Dict[str, object]] = None,
) -> geometry_scene.GeometryScene:
    if not os.path.exists(path):
        raise ValueError(f"OBJ file was not found: {path}")
    objects: List[geometry_scene.GeometryModel] = []
    current = geometry_scene.GeometryModel(name=os.path.splitext(os.path.basename(path))[0] or "Mesh")
    material = "Default"
    global_points: List[Vec3] = []
    global_uvs: List[Vec2] = []
    local_map: Dict[int, int] = {}
    seen_materials: set[str] = set()

    def finish_current() -> None:
        nonlocal current, local_map
        if current.faces:
            objects.append(current)
        current = geometry_scene.GeometryModel(name=current.name)
        local_map = {}

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_number, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            head = parts[0]
            if head == "v" and len(parts) >= 4:
                try:
                    point = (float(parts[1]), float(parts[2]), float(parts[3]))
                except ValueError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid OBJ vertex coordinates") from exc
                if not all(math.isfinite(item) for item in point):
                    raise ValueError(f"{path}:{line_number}: OBJ vertex coordinates must be finite")
                global_points.append(point)
                continue
            if head == "vt" and len(parts) >= 3:
                try:
                    uv = (float(parts[1]), float(parts[2]))
                except ValueError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid OBJ texture coordinates") from exc
                if not all(math.isfinite(item) for item in uv):
                    raise ValueError(f"{path}:{line_number}: OBJ texture coordinates must be finite")
                global_uvs.append(uv)
                continue
            if head in {"o", "g"} and len(parts) >= 2:
                if current.faces:
                    finish_current()
                current.name = " ".join(parts[1:])
                continue
            if head == "usemtl" and len(parts) >= 2:
                material = " ".join(parts[1:])
                seen_materials.add(material)
                continue
            if head == "f" and len(parts) >= 4:
                face_indices: List[int] = []
                face_uvs: List[Optional[Vec2]] = []
                for token in parts[1:]:
                    token_parts = token.split("/")
                    vertex_text = token_parts[0]
                    if not vertex_text:
                        continue
                    try:
                        obj_index = int(vertex_text)
                    except ValueError as exc:
                        raise ValueError(f"{path}:{line_number}: invalid OBJ face vertex {token!r}") from exc
                    global_index = obj_index - 1 if obj_index > 0 else len(global_points) + obj_index
                    if global_index < 0 or global_index >= len(global_points):
                        raise ValueError(
                            f"{path}:{line_number}: OBJ face references missing vertex index {obj_index}"
                        )
                    if global_index not in local_map:
                        local_map[global_index] = len(current.points)
                        current.points.append(global_points[global_index])
                    face_indices.append(local_map[global_index])
                    uv_coord: Optional[Vec2] = None
                    if len(token_parts) >= 2 and token_parts[1]:
                        try:
                            uv_obj_index = int(token_parts[1])
                        except ValueError as exc:
                            raise ValueError(f"{path}:{line_number}: invalid OBJ face texture vertex {token!r}") from exc
                        uv_global_index = uv_obj_index - 1 if uv_obj_index > 0 else len(global_uvs) + uv_obj_index
                        if not (0 <= uv_global_index < len(global_uvs)):
                            raise ValueError(
                                f"{path}:{line_number}: OBJ face references missing texture vertex index {uv_obj_index}"
                            )
                        uv_coord = global_uvs[uv_global_index]
                    face_uvs.append(uv_coord)
                if len(face_indices) >= 3:
                    current.faces.append(geometry_scene.GeometryFace(face_indices, material, face_uvs))
    if current.faces:
        objects.append(current)
    material_to_texture = material_texture_map_from_meta(meta or {})
    materials = [
        geometry_scene.GeometryMaterial(
            name=name,
            texture_name=material_to_texture.get(name, name or "Default"),
        )
        for name in sorted(seen_materials or {"Default"})
    ]
    return geometry_scene.GeometryScene(
        source_path=os.path.abspath(path),
        models=objects,
        materials=materials,
        metadata=dict(meta or {}),
    )


def geometry_model_to_bsp_mesh(
    model: geometry_scene.GeometryModel,
    model_name: str,
    material_to_texture: Dict[str, str],
    export_to_dat: Optional[Sequence[Sequence[float]]] = None,
) -> bsp.WorldModelMesh:
    matrix = export_to_dat if export_to_dat is not None else identity_matrix()
    raw_points = [_matrix_point(matrix, point) for point in model.points]
    points: List[Vec3] = []
    point_index_by_position: Dict[Tuple[float, float, float], int] = {}
    texture_names: List[str] = []
    texture_index_by_name: Dict[str, int] = {}
    surfaces: List[bsp.Surface] = []
    polygons: List[bsp.Polygon] = []

    for face in model.faces:
        remapped_indices: List[int] = []
        for vertex_index in face.vertex_indices:
            if vertex_index < 0 or vertex_index >= len(raw_points):
                continue
            point = raw_points[vertex_index]
            key = _point_dedupe_key(point)
            if key not in point_index_by_position:
                point_index_by_position[key] = len(points)
                points.append(point)
            remapped_indices.append(point_index_by_position[key])
        if len(set(remapped_indices)) < 3:
            continue
        remapped_face = geometry_scene.GeometryFace(
            vertex_indices=remapped_indices,
            material_name=face.material_name,
            uv_coords=list(face.uv_coords or []),
            extras=dict(face.extras or {}),
        )
        texture = material_to_texture.get(face.material_name, face.material_name or "Default")
        if texture not in texture_index_by_name:
            texture_index_by_name[texture] = len(texture_names)
            texture_names.append(texture)
        surface_index = len(surfaces)
        surfaces.append(_surface_from_geometry_face(
            points,
            remapped_face,
            texture_index_by_name[texture],
        ))
        indices = list(reversed(remapped_indices))
        polygons.append(bsp.Polygon(
            vertex_indices=indices,
            surface_index=surface_index,
            plane_index=0,
        ))
        source_face = _source_face_metadata(model, remapped_face)
        if source_face:
            setattr(polygons[-1], "mm9_source_face", source_face)

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


def identity_matrix() -> List[List[float]]:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _point_dedupe_key(point: Vec3) -> Tuple[float, float, float]:
    return (round(float(point[0]), 6), round(float(point[1]), 6), round(float(point[2]), 6))


def _surface_from_geometry_face(
    points: Sequence[Vec3],
    face: geometry_scene.GeometryFace,
    texture_index: int,
) -> bsp.Surface:
    fallback = bsp.Surface(
        uv_o=(0.0, 0.0, 0.0),
        uv_p=(1.0, 0.0, 0.0),
        uv_q=(0.0, 0.0, 1.0),
        texture_index=texture_index,
        flags=0,
        texture_flags=0,
    )
    source_surface = _surface_from_source_face_extras(face, texture_index)
    if source_surface is not None:
        return source_surface
    if len(face.uv_coords) != len(face.vertex_indices) or any(uv is None for uv in face.uv_coords):
        setattr(fallback, "mm9_uv_method", "default")
        return fallback
    if len(face.vertex_indices) < 3:
        setattr(fallback, "mm9_uv_method", "default")
        return fallback
    dedit_surface = _dedit_surface_from_obj_face(points, face, texture_index)
    if dedit_surface is not None:
        return dedit_surface
    fitted_surface = _least_squares_surface_from_obj_face(points, face, texture_index)
    if fitted_surface is not None:
        return fitted_surface
    setattr(fallback, "mm9_uv_method", "default")
    return fallback


def _surface_from_source_face_extras(
    face: geometry_scene.GeometryFace,
    texture_index: int,
) -> Optional[bsp.Surface]:
    extras = face.extras or {}
    uv_o = _vec3_extra(extras, "uv_o")
    uv_p = _vec3_extra(extras, "uv_p")
    uv_q = _vec3_extra(extras, "uv_q")
    if uv_o is None or uv_p is None or uv_q is None:
        return None
    surface = bsp.Surface(
        uv_o=uv_o,
        uv_p=uv_p,
        uv_q=uv_q,
        texture_index=texture_index,
        flags=0,
        texture_flags=int(extras.get("texture_flags") or 0) & 0xFFFF,
    )
    setattr(surface, "mm9_uv_method", "source_opq")
    return surface


def _source_face_metadata(
    model: geometry_scene.GeometryModel,
    face: geometry_scene.GeometryFace,
) -> Dict[str, object]:
    metadata: Dict[str, object] = {}
    model_extras = model.extras or {}
    face_extras = face.extras or {}
    for key in (
        "source_format",
        "brush_index",
        "polygon_index",
        "record_start",
        "record_end",
        "physics_material",
        "surface_key",
        "surface_flags",
        "texture_flags",
        "normal",
        "dist",
    ):
        if key in face_extras:
            metadata[key] = face_extras[key]
        elif key in model_extras:
            metadata[key] = model_extras[key]
    source_format = metadata.get("source_format")
    if source_format:
        metadata["model_name"] = model.name
        metadata["material_name"] = face.material_name
    return metadata


def _vec3_extra(extras: Dict[str, object], key: str) -> Optional[Vec3]:
    value = extras.get(key)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        result = (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in result):
        return None
    return result


def _dedit_surface_from_obj_face(
    points: Sequence[Vec3],
    face: geometry_scene.GeometryFace,
    texture_index: int,
) -> Optional[bsp.Surface]:
    first_positions: List[Vec3] = []
    first_uvs: List[Vec2] = []
    for vertex_index, uv in zip(face.vertex_indices, face.uv_coords):
        if uv is None or vertex_index < 0 or vertex_index >= len(points):
            return None
        first_positions.append(points[vertex_index])
        first_uvs.append((float(uv[0]), -float(uv[1])))
        if len(first_positions) == 3:
            break
    opq = uv_projection.dedit_uv_to_opq(first_positions, first_uvs)
    if opq is None:
        return None
    uv_o, uv_p, uv_q = opq
    surface = bsp.Surface(
        uv_o=uv_o,
        uv_p=uv_p,
        uv_q=uv_q,
        texture_index=texture_index,
        flags=0,
        texture_flags=0,
    )
    setattr(surface, "mm9_uv_method", "dedit_opq")
    return surface


def _least_squares_surface_from_obj_face(
    points: Sequence[Vec3],
    face: geometry_scene.GeometryFace,
    texture_index: int,
) -> Optional[bsp.Surface]:
    origin = points[face.vertex_indices[0]]
    uv0 = face.uv_coords[0]
    if uv0 is None:
        return None
    offsets: List[Vec3] = []
    u_values: List[float] = []
    v_values: List[float] = []
    for vertex_index, uv in zip(face.vertex_indices[1:], face.uv_coords[1:]):
        if uv is None:
            return None
        point = points[vertex_index]
        offsets.append((
            float(point[0]) - float(origin[0]),
            float(point[1]) - float(origin[1]),
            float(point[2]) - float(origin[2]),
        ))
        u_values.append((float(uv[0]) - float(uv0[0])) * 128.0)
        v_values.append((float(uv[1]) - float(uv0[1])) * 128.0)
    uv_p = _least_squares_vector(offsets, u_values)
    uv_q = _least_squares_vector(offsets, v_values)
    if uv_p is None or uv_q is None:
        return None
    uv_o = _surface_origin_for_uv(origin, uv_p, uv_q, uv0)
    if uv_o is None:
        return None
    surface = bsp.Surface(
        uv_o=uv_o,
        uv_p=uv_p,
        uv_q=uv_q,
        texture_index=texture_index,
        flags=0,
        texture_flags=0,
    )
    setattr(surface, "mm9_uv_method", "least_squares")
    return surface


def _surface_origin_for_uv(origin: Vec3, uv_p: Vec3, uv_q: Vec3, uv0: Vec2) -> Optional[Vec3]:
    pp = _dot(uv_p, uv_p)
    pq = _dot(uv_p, uv_q)
    qq = _dot(uv_q, uv_q)
    target_u = float(uv0[0]) * 128.0
    target_v = float(uv0[1]) * 128.0
    det = pp * qq - pq * pq
    if abs(det) <= 1.0e-8:
        return None
    a = (target_u * qq - target_v * pq) / det
    b = (target_v * pp - target_u * pq) / det
    offset = (
        a * float(uv_p[0]) + b * float(uv_q[0]),
        a * float(uv_p[1]) + b * float(uv_q[1]),
        a * float(uv_p[2]) + b * float(uv_q[2]),
    )
    return (
        float(origin[0]) - offset[0],
        float(origin[1]) - offset[1],
        float(origin[2]) - offset[2],
    )


def _least_squares_vector(offsets: Sequence[Vec3], values: Sequence[float]) -> Optional[Vec3]:
    basis = _face_basis(offsets)
    if basis is None:
        return None
    axis_a, axis_b = basis
    ata = [[0.0, 0.0], [0.0, 0.0]]
    atb = [0.0, 0.0]
    for offset, value in zip(offsets, values):
        row = [_dot(offset, axis_a), _dot(offset, axis_b)]
        for i in range(2):
            atb[i] += row[i] * float(value)
            for j in range(2):
                ata[i][j] += row[i] * row[j]
    det = ata[0][0] * ata[1][1] - ata[0][1] * ata[1][0]
    if abs(det) <= 1.0e-8:
        return None
    coeff_a = (atb[0] * ata[1][1] - atb[1] * ata[0][1]) / det
    coeff_b = (ata[0][0] * atb[1] - ata[1][0] * atb[0]) / det
    return (
        coeff_a * axis_a[0] + coeff_b * axis_b[0],
        coeff_a * axis_a[1] + coeff_b * axis_b[1],
        coeff_a * axis_a[2] + coeff_b * axis_b[2],
    )


def _face_basis(offsets: Sequence[Vec3]) -> Optional[Tuple[Vec3, Vec3]]:
    axis_a = next((_unit(offset) for offset in offsets if _length(offset) > 1.0e-6), None)
    if axis_a is None:
        return None
    normal = None
    for offset in offsets:
        candidate = _cross(axis_a, offset)
        if _length(candidate) > 1.0e-6:
            normal = _unit(candidate)
            break
    if normal is None:
        return None
    axis_b = _unit(_cross(normal, axis_a))
    return axis_a, axis_b


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


def _dot(a: Vec3, b: Vec3) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        float(a[1]) * float(b[2]) - float(a[2]) * float(b[1]),
        float(a[2]) * float(b[0]) - float(a[0]) * float(b[2]),
        float(a[0]) * float(b[1]) - float(a[1]) * float(b[0]),
    )


def _length(value: Vec3) -> float:
    return math.sqrt(_dot(value, value))


def _unit(value: Vec3) -> Vec3:
    length = _length(value)
    if length <= 1.0e-6:
        return (0.0, 1.0, 0.0)
    return (
        float(value[0]) / length,
        float(value[1]) / length,
        float(value[2]) / length,
    )
