"""Restricted topology-preserving BSP vertex edit import."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from core import bsp
from features.dat_editing import mesh_import
from features.doors import bsp_writer


Vec3 = Tuple[float, float, float]


@dataclass
class _ObjFace:
    vertex_indices: List[int]


@dataclass
class _ObjObject:
    name: str
    points: List[Vec3] = field(default_factory=list)
    faces: List[_ObjFace] = field(default_factory=list)


@dataclass(frozen=True)
class VertexEditedModel:
    name: str
    source_model: bsp.WorldModelMesh
    edited_model: bsp.WorldModelMesh


@dataclass(frozen=True)
class VertexEditPlan:
    obj_path: str
    meta_path: str
    models: List[VertexEditedModel] = field(default_factory=list)


def build_vertex_edit_plan(
    target_bsp: bsp.BspWorld,
    source_dat: bytes,
    obj_path: str,
    meta_path: Optional[str] = None,
    model_names: Optional[Sequence[str]] = None,
) -> VertexEditPlan:
    """Build an in-place vertex-edit plan from an OBJ exported by Stage 1.

    Only vertex movement is accepted. Point counts, polygon counts, and every
    polygon's point-index list must match the source metadata after converting
    the OBJ coordinates back into DAT space.
    """
    meta_path = meta_path or mesh_import._default_meta_path(obj_path)
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    _validate_source_identity(source_dat, meta)

    export_to_dat = meta.get("coordinate_system", {}).get("export_to_dat_matrix")
    if not export_to_dat:
        export_to_dat = _identity_matrix()
    parsed_by_name = {
        _object_key(obj.name): obj
        for obj in _parse_obj_preserve_order(obj_path)
    }
    wanted = {str(name or "").lower() for name in model_names or []}
    edited: List[VertexEditedModel] = []

    for index, model_meta in enumerate(meta.get("models", []) or []):
        model_name = str(model_meta.get("name") or "")
        if wanted and model_name.lower() not in wanted:
            continue
        source_model = target_bsp.model_by_name(model_name)
        if source_model is None:
            raise ValueError(f"source BSP model {model_name!r} is not present in the target level")
        object_name = _obj_name(model_name, index)
        parsed = parsed_by_name.get(_object_key(object_name))
        if parsed is None:
            raise ValueError(f"OBJ object {object_name!r} for BSP model {model_name!r} was not found")
        edited_model = _edited_model_from_obj(source_model, parsed, model_meta, export_to_dat)
        edited.append(VertexEditedModel(
            name=model_name,
            source_model=source_model,
            edited_model=edited_model,
        ))

    if not edited:
        raise ValueError("no editable BSP models were found in the OBJ/metadata pair")
    return VertexEditPlan(
        obj_path=os.path.abspath(obj_path),
        meta_path=os.path.abspath(meta_path),
        models=edited,
    )


def build_preview_bsp(target_bsp: bsp.BspWorld, plans: Sequence[VertexEditPlan]) -> bsp.BspWorld:
    replacement_by_name: Dict[str, bsp.WorldModelMesh] = {}
    for plan in plans or []:
        for item in plan.models:
            replacement_by_name[item.name.lower()] = item.edited_model
    return bsp.BspWorld(
        version=target_bsp.version,
        world_info=target_bsp.world_info,
        obj_pos=target_bsp.obj_pos,
        ren_pos=target_bsp.ren_pos,
        world_model_table_start=target_bsp.world_model_table_start,
        world_models=[
            copy.deepcopy(replacement_by_name.get(model.name.lower(), model))
            for model in target_bsp.world_models
        ],
        parse_warnings=list(getattr(target_bsp, "parse_warnings", []) or []),
    )


def apply_vertex_edit_plans(
    source_dat: bytes,
    bsp_world: bsp.BspWorld,
    plans: Sequence[VertexEditPlan],
) -> bytes:
    data = bytearray(source_dat)
    for plan in plans or []:
        for item in plan.models:
            source_model = bsp_world.model_by_name(item.name)
            if source_model is None:
                raise ValueError(f"source BSP model {item.name!r} is not present")
            raw = bsp_world.raw_model_bytes(source_dat, source_model)
            if raw is None or source_model.raw_start is None or source_model.raw_end is None:
                raise ValueError(f"BSP model {item.name!r} has no recoverable raw byte range")
            patched = patch_model_record(raw, source_model, item.edited_model)
            if len(patched) != len(raw):
                raise ValueError(f"patched BSP model {item.name!r} changed record size")
            data[source_model.raw_start:source_model.raw_end] = patched
    return bytes(data)


def patch_model_record(
    raw_record: bytes,
    source_model: bsp.WorldModelMesh,
    edited_model: bsp.WorldModelMesh,
) -> bytes:
    _validate_topology(source_model, edited_model)
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

    planes = [_plane_for_polygon(edited_model.points, polygon) for polygon in edited_model.polygons]
    point_normals = _point_normals(len(edited_model.points), edited_model.polygons, planes)
    for plane_offset, (normal, distance) in zip(plane_offsets, planes):
        struct.pack_into("<3f", raw, plane_offset, *normal)
        struct.pack_into("<f", raw, plane_offset + 12, float(distance))

    for (center_offset, _surface_index_offset, _plane_index_offset), polygon in zip(polygon_offsets, edited_model.polygons):
        center = _polygon_center(edited_model.points, polygon)
        struct.pack_into("<3f", raw, center_offset, *center)

    for (point_offset, normal_offset), point, normal in zip(point_offsets, edited_model.points, point_normals):
        struct.pack_into("<3f", raw, point_offset, *point)
        struct.pack_into("<3f", raw, normal_offset, *normal)
    return bytes(raw)


def _parse_obj_preserve_order(path: str) -> List[_ObjObject]:
    objects: List[_ObjObject] = []
    current: Optional[_ObjObject] = None
    global_points: List[Vec3] = []
    object_for_global: Dict[int, _ObjObject] = {}
    local_index_for_global: Dict[int, int] = {}

    def ensure_current(name: str = "") -> _ObjObject:
        nonlocal current
        if current is None:
            current = _ObjObject(name=name or os.path.splitext(os.path.basename(path))[0] or "Mesh")
            objects.append(current)
        elif name:
            if current.points or current.faces:
                current = _ObjObject(name=name)
                objects.append(current)
            else:
                current.name = name
        return current

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            head = parts[0]
            if head in {"o", "g"} and len(parts) >= 2:
                ensure_current(" ".join(parts[1:]))
                continue
            if head == "v" and len(parts) >= 4:
                obj = ensure_current()
                global_index = len(global_points)
                global_points.append((float(parts[1]), float(parts[2]), float(parts[3])))
                local_index_for_global[global_index] = len(obj.points)
                object_for_global[global_index] = obj
                obj.points.append(global_points[-1])
                continue
            if head == "f" and len(parts) >= 4:
                face_global: List[int] = []
                for token in parts[1:]:
                    vertex_text = token.split("/", 1)[0]
                    if not vertex_text:
                        continue
                    obj_index = int(vertex_text)
                    global_index = obj_index - 1 if obj_index > 0 else len(global_points) + obj_index
                    if 0 <= global_index < len(global_points):
                        face_global.append(global_index)
                if len(face_global) < 3:
                    continue
                obj = object_for_global.get(face_global[0], ensure_current())
                if any(object_for_global.get(index) is not obj for index in face_global):
                    raise ValueError("OBJ face spans multiple exported objects; topology edit import cannot map it")
                obj.faces.append(_ObjFace([local_index_for_global[index] for index in face_global]))
    return [obj for obj in objects if obj.faces]


def _edited_model_from_obj(
    source_model: bsp.WorldModelMesh,
    parsed: object,
    model_meta: Dict[str, object],
    export_to_dat: Sequence[Sequence[float]],
) -> bsp.WorldModelMesh:
    parsed_points = [_matrix_point(export_to_dat, point) for point in parsed.points]
    meta_polygons = list(model_meta.get("polygons", []) or [])
    if len(parsed_points) != len(source_model.points):
        raise ValueError(
            f"BSP model {source_model.name!r} point count changed "
            f"({len(source_model.points)} -> {len(parsed_points)})"
        )
    if len(parsed.faces) != len(source_model.polygons):
        raise ValueError(
            f"BSP model {source_model.name!r} polygon count changed "
            f"({len(source_model.polygons)} -> {len(parsed.faces)})"
        )
    if len(meta_polygons) != len(source_model.polygons):
        raise ValueError(f"metadata for BSP model {source_model.name!r} does not match source polygon count")

    for index, (face, source_polygon, polygon_meta) in enumerate(zip(parsed.faces, source_model.polygons, meta_polygons)):
        obj_indices_in_dat_winding = list(reversed(face.vertex_indices))
        meta_indices = [int(v) for v in polygon_meta.get("vertex_indices", [])]
        if meta_indices != list(source_polygon.vertex_indices):
            raise ValueError(f"metadata polygon {index} for {source_model.name!r} no longer matches source BSP")
        if obj_indices_in_dat_winding != list(source_polygon.vertex_indices):
            raise ValueError(
                f"BSP model {source_model.name!r} polygon {index} topology changed; "
                "only moving existing vertices is supported"
            )

    edited = copy.deepcopy(source_model)
    edited.points = parsed_points
    edited.min_box, edited.max_box = _bounds(parsed_points)
    edited.raw_start = source_model.raw_start
    edited.raw_end = source_model.raw_end
    edited.next_world_item = source_model.next_world_item
    edited.world_bsp_start = source_model.world_bsp_start
    edited.world_bsp_end = source_model.world_bsp_end
    return edited


def _validate_source_identity(source_dat: bytes, meta: Dict[str, object]) -> None:
    source = meta.get("source", {}) or {}
    expected = str(source.get("sha256") or "")
    if expected and hashlib.sha256(source_dat).hexdigest().lower() != expected.lower():
        raise ValueError("OBJ metadata source checksum does not match the currently loaded DAT")


def _validate_topology(source_model: bsp.WorldModelMesh, edited_model: bsp.WorldModelMesh) -> None:
    if len(source_model.points) != len(edited_model.points):
        raise ValueError(f"BSP model {source_model.name!r} point count changed")
    if len(source_model.polygons) != len(edited_model.polygons):
        raise ValueError(f"BSP model {source_model.name!r} polygon count changed")
    for index, (source_polygon, edited_polygon) in enumerate(zip(source_model.polygons, edited_model.polygons)):
        if list(source_polygon.vertex_indices) != list(edited_polygon.vertex_indices):
            raise ValueError(f"BSP model {source_model.name!r} polygon {index} vertex list changed")


def _obj_name(name: str, index: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", str(name or "")).strip("_")
    return cleaned or f"WorldModel_{index}"


def _object_key(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(name or "").lower()).strip("_")


def _matrix_point(matrix: Sequence[Sequence[float]], point: Vec3) -> Vec3:
    x, y, z = point
    return (
        float(matrix[0][0]) * x + float(matrix[0][1]) * y + float(matrix[0][2]) * z + float(matrix[0][3]),
        float(matrix[1][0]) * x + float(matrix[1][1]) * y + float(matrix[1][2]) * z + float(matrix[1][3]),
        float(matrix[2][0]) * x + float(matrix[2][1]) * y + float(matrix[2][2]) * z + float(matrix[2][3]),
    )


def _plane_for_polygon(points: Sequence[Vec3], polygon: bsp.Polygon) -> Tuple[Vec3, float]:
    verts = [points[index] for index in polygon.vertex_indices]
    normal = _polygon_normal(verts)
    distance = _dot(normal, verts[0])
    return normal, distance


def _polygon_normal(vertices: Sequence[Vec3]) -> Vec3:
    nx = ny = nz = 0.0
    for i, current in enumerate(vertices):
        nxt = vertices[(i + 1) % len(vertices)]
        nx += (current[1] - nxt[1]) * (current[2] + nxt[2])
        ny += (current[2] - nxt[2]) * (current[0] + nxt[0])
        nz += (current[0] - nxt[0]) * (current[1] + nxt[1])
    return _unit((nx, ny, nz))


def _point_normals(
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
    return [_unit((value[0], value[1], value[2])) for value in accum]


def _polygon_center(points: Sequence[Vec3], polygon: bsp.Polygon) -> Vec3:
    verts = [points[index] for index in polygon.vertex_indices]
    count = float(len(verts))
    return (
        sum(point[0] for point in verts) / count,
        sum(point[1] for point in verts) / count,
        sum(point[2] for point in verts) / count,
    )


def _bounds(points: Sequence[Vec3]) -> Tuple[Vec3, Vec3]:
    return (
        (min(p[0] for p in points), min(p[1] for p in points), min(p[2] for p in points)),
        (max(p[0] for p in points), max(p[1] for p in points), max(p[2] for p in points)),
    )


def _dot(a: Vec3, b: Vec3) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _unit(value: Vec3) -> Vec3:
    length = math.sqrt(_dot(value, value))
    if length <= 1.0e-6:
        return (0.0, 1.0, 0.0)
    return (float(value[0]) / length, float(value[1]) / length, float(value[2]) / length)


def _identity_matrix() -> List[List[float]]:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
