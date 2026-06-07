"""Minimal glTF 2.0 reader for MM9 DAT geometry round trips."""

from __future__ import annotations

import json
import math
import os
import base64
import struct
from typing import Any, Dict, List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from features.dat_editing import geometry_scene
from features.dat_editing import obj_workflow


Vec2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]
Mat4 = List[List[float]]


class GltfImportError(ValueError):
    pass


def load_gltf_geometry_scene(path: str, meta_path: Optional[str] = None) -> geometry_scene.GeometryScene:
    if not os.path.exists(path):
        raise ValueError(f"glTF file was not found: {path}")
    glb_bin: Optional[bytes] = None
    if os.path.splitext(path)[1].lower() == ".glb":
        gltf, glb_bin = _load_glb(path)
    else:
        with open(path, "r", encoding="utf-8") as f:
            gltf = json.load(f)
    if not isinstance(gltf, dict):
        raise GltfImportError("glTF root must be a JSON object")
    if str((gltf.get("asset") or {}).get("version") or "")[:1] != "2":
        raise GltfImportError("only glTF 2.0 files are supported")

    meta = _roundtrip_meta(gltf, path, meta_path)
    buffers = _load_buffers(gltf, path, glb_bin)
    materials = _materials(gltf, meta)
    material_texture_map = {
        material.name: material.texture_name or material.name or "Default"
        for material in materials
    }

    models: List[geometry_scene.GeometryModel] = []
    nodes = gltf.get("nodes") or []
    meshes = gltf.get("meshes") or []
    if not isinstance(nodes, list) or not isinstance(meshes, list):
        raise GltfImportError("glTF nodes and meshes must be arrays")
    for node_index, node in enumerate(nodes):
        if not isinstance(node, dict) or "mesh" not in node:
            continue
        mesh_index = int(node["mesh"])
        if mesh_index < 0 or mesh_index >= len(meshes) or not isinstance(meshes[mesh_index], dict):
            raise GltfImportError(f"node {node_index} references missing mesh {mesh_index}")
        model = _node_mesh_to_model(
            gltf,
            buffers,
            node,
            meshes[mesh_index],
            node_index,
            material_texture_map,
            _node_matrix(node),
            meta,
        )
        if model.faces:
            models.append(model)

    return geometry_scene.GeometryScene(
        source_path=os.path.abspath(path),
        models=models,
        materials=materials,
        metadata=meta,
    )


def _roundtrip_meta(gltf: Dict[str, Any], gltf_path: str, meta_path: Optional[str]) -> Dict[str, Any]:
    embedded = (gltf.get("extras") or {}).get("MM9_datmeta")
    if isinstance(embedded, dict):
        meta = dict(embedded)
        meta.setdefault("import_metadata_source", "embedded_gltf_extras")
        meta.setdefault("import_warnings", [])
        return meta
    if meta_path:
        meta = obj_workflow.load_roundtrip_meta(meta_path)
        meta.setdefault("import_metadata_source", "selected_sidecar")
        meta.setdefault("import_warnings", [])
        return meta
    default_meta = f"{gltf_path}.datmeta.json"
    if os.path.exists(default_meta):
        meta = obj_workflow.load_roundtrip_meta(default_meta)
        meta.setdefault("import_metadata_source", "default_sidecar")
        meta.setdefault("import_warnings", [
            "glTF extras did not contain MM9 DAT metadata; using the .datmeta.json sidecar."
        ])
        return meta
    return {
        "kind": obj_workflow.ROUNDTRIP_KIND,
        "format": "gltf",
        "coordinate_system": {},
        "materials": [],
        "models": [],
        "import_metadata_source": "missing",
        "import_warnings": [
            "glTF DAT metadata is missing. Import will treat this as generic triangle geometry; "
            "original BSP polygon identity, DAT texture paths, and source checksum validation are unavailable."
        ],
    }


def _load_glb(path: str) -> Tuple[Dict[str, Any], Optional[bytes]]:
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 12:
        raise GltfImportError("GLB file is too short")
    magic, version, declared_length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise GltfImportError("GLB header magic is invalid")
    if version != 2:
        raise GltfImportError("only GLB version 2 is supported")
    if declared_length > len(data):
        raise GltfImportError("GLB file is shorter than its declared length")
    json_chunk: Optional[bytes] = None
    bin_chunk: Optional[bytes] = None
    offset = 12
    while offset + 8 <= declared_length:
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        end = offset + chunk_length
        if end > declared_length:
            raise GltfImportError("GLB chunk reads past declared length")
        chunk = data[offset:end]
        offset = end
        if chunk_type == 0x4E4F534A:
            json_chunk = chunk
        elif chunk_type == 0x004E4942 and bin_chunk is None:
            bin_chunk = chunk
    if json_chunk is None:
        raise GltfImportError("GLB JSON chunk is missing")
    try:
        gltf = json.loads(json_chunk.decode("utf-8").rstrip(" \t\r\n\0"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GltfImportError(f"GLB JSON chunk is invalid: {exc}") from exc
    if not isinstance(gltf, dict):
        raise GltfImportError("GLB JSON chunk must contain a glTF object")
    return gltf, bin_chunk


def _load_buffers(gltf: Dict[str, Any], gltf_path: str, glb_bin: Optional[bytes] = None) -> List[bytes]:
    result: List[bytes] = []
    base_dir = os.path.dirname(os.path.abspath(gltf_path))
    buffers = gltf.get("buffers") or []
    if not isinstance(buffers, list):
        raise GltfImportError("glTF buffers must be an array")
    for index, buffer in enumerate(buffers):
        if not isinstance(buffer, dict):
            raise GltfImportError(f"buffer {index} must be an object")
        uri = str(buffer.get("uri") or "")
        if not uri:
            if glb_bin is None or index != 0:
                raise GltfImportError("buffer without uri is only supported for the first GLB binary chunk")
            data = glb_bin
            declared = buffer.get("byteLength")
            if declared is not None and int(declared) > len(data):
                raise GltfImportError("GLB binary chunk is shorter than declared byteLength")
            result.append(data)
            continue
        if uri.startswith("data:"):
            result.append(_decode_data_uri(uri, index))
            continue
        path = os.path.abspath(os.path.join(base_dir, uri.replace("/", os.sep)))
        if os.path.commonpath([base_dir, path]) != base_dir:
            raise GltfImportError(f"buffer uri escapes the glTF directory: {uri!r}")
        with open(path, "rb") as f:
            data = f.read()
        declared = buffer.get("byteLength")
        if declared is not None and int(declared) > len(data):
            raise GltfImportError(f"buffer {uri!r} is shorter than declared byteLength")
        result.append(data)
    return result


def _decode_data_uri(uri: str, index: int) -> bytes:
    header, sep, payload = uri.partition(",")
    if not sep or ";base64" not in header.lower():
        raise GltfImportError(f"buffer {index} data URI must be base64 encoded")
    try:
        return base64.b64decode(payload, validate=True)
    except ValueError as exc:
        raise GltfImportError(f"buffer {index} data URI is not valid base64") from exc


def _materials(gltf: Dict[str, Any], meta: Dict[str, Any]) -> List[geometry_scene.GeometryMaterial]:
    result: List[geometry_scene.GeometryMaterial] = []
    seen: set[str] = set()
    for item in meta.get("materials", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("material_name") or "")
        texture = str(item.get("texture_name") or name or "Default")
        if name and name not in seen:
            result.append(geometry_scene.GeometryMaterial(name=name, texture_name=texture, extras=dict(item)))
            seen.add(name)
    for index, material in enumerate(gltf.get("materials") or []):
        if not isinstance(material, dict):
            continue
        name = str(material.get("name") or f"Material_{index}")
        texture = str((material.get("extras") or {}).get("MM9_texture") or name or "Default")
        if name not in seen and not (material.get("extras") or {}).get("MM9_texture"):
            _add_import_warning(
                meta,
                f"glTF material {name!r} has no MM9_texture metadata; using the material name as the DAT texture path."
            )
        if name not in seen:
            result.append(geometry_scene.GeometryMaterial(name=name, texture_name=texture, extras=dict(material.get("extras") or {})))
            seen.add(name)
    if not result:
        result.append(geometry_scene.GeometryMaterial(name="Default", texture_name="Default"))
    return result


def _add_import_warning(meta: Dict[str, Any], message: str) -> None:
    warnings = meta.setdefault("import_warnings", [])
    if isinstance(warnings, list) and message not in warnings:
        warnings.append(message)


def _node_mesh_to_model(
    gltf: Dict[str, Any],
    buffers: Sequence[bytes],
    node: Dict[str, Any],
    mesh: Dict[str, Any],
    node_index: int,
    material_texture_map: Dict[str, str],
    node_matrix: Mat4,
    meta: Dict[str, Any],
) -> geometry_scene.GeometryModel:
    name = str(
        (node.get("extras") or {}).get("MM9_model_name")
        or (mesh.get("extras") or {}).get("MM9_model_name")
        or node.get("name")
        or mesh.get("name")
        or f"GltfNode{node_index}"
    )
    model = geometry_scene.GeometryModel(
        name=name,
        extras={
            "source_format": "gltf",
            "node_name": str(node.get("name") or ""),
            "mesh_name": str(mesh.get("name") or ""),
            "role": str((node.get("extras") or {}).get("MM9_role") or ""),
            "node_extras": dict(node.get("extras") or {}),
            "mesh_extras": dict(mesh.get("extras") or {}),
            "datmeta": meta,
        },
    )
    primitive_list = mesh.get("primitives") or []
    if not isinstance(primitive_list, list):
        raise GltfImportError(f"mesh {name!r} primitives must be an array")
    for primitive_index, primitive in enumerate(primitive_list):
        if not isinstance(primitive, dict):
            continue
        _append_primitive_faces(
            gltf,
            buffers,
            model,
            primitive,
            primitive_index,
            material_texture_map,
            node_matrix,
        )
    return model


def _append_primitive_faces(
    gltf: Dict[str, Any],
    buffers: Sequence[bytes],
    model: geometry_scene.GeometryModel,
    primitive: Dict[str, Any],
    primitive_index: int,
    material_texture_map: Dict[str, str],
    node_matrix: Mat4,
) -> None:
    if int(primitive.get("mode", 4)) != 4:
        raise GltfImportError("only TRIANGLES glTF primitives are supported")
    attrs = primitive.get("attributes") or {}
    if "POSITION" not in attrs:
        return
    positions = [
        _matrix_point(node_matrix, value)
        for value in _read_accessor(gltf, buffers, int(attrs["POSITION"]), expected_type="VEC3")
    ]
    texcoords: List[Optional[Vec2]] = []
    if "TEXCOORD_0" in attrs:
        texcoords = [
            (float(value[0]), float(value[1]))
            for value in _read_accessor(gltf, buffers, int(attrs["TEXCOORD_0"]), expected_type="VEC2")
        ]
    indices = _primitive_indices(gltf, buffers, primitive, len(positions))
    material_name = _material_name(gltf, primitive, material_texture_map)
    polygon_indices = [
        int(value)
        for value in ((primitive.get("extras") or {}).get("MM9_polygon_indices") or [])
        if isinstance(value, (int, float))
    ]
    polygon_meta = _model_polygon_meta(model)

    if polygon_indices and polygon_meta:
        if _append_metadata_faces(model, positions, texcoords, material_name, polygon_indices, polygon_meta, primitive_index):
            return
        _append_triangle_faces(
            model,
            positions,
            texcoords,
            indices,
            material_name,
            primitive_index,
            warning_reason="metadata polygon reconstruction was invalid; imported actual triangles instead",
        )
        return
    _append_triangle_faces(model, positions, texcoords, indices, material_name, primitive_index)


def _append_metadata_faces(
    model: geometry_scene.GeometryModel,
    positions: Sequence[Vec3],
    texcoords: Sequence[Optional[Vec2]],
    material_name: str,
    polygon_indices: Sequence[int],
    polygon_meta: Dict[int, Dict[str, Any]],
    primitive_index: int,
) -> bool:
    cursor = 0
    pending_points: List[Vec3] = []
    pending_faces: List[geometry_scene.GeometryFace] = []
    for polygon_index in polygon_indices:
        meta = polygon_meta.get(int(polygon_index)) or {}
        vertex_count = len(meta.get("vertex_indices") or [])
        if vertex_count < 3 or cursor + vertex_count > len(positions):
            return False
        local_positions = list(positions[cursor:cursor + vertex_count])
        if _positions_are_degenerate(local_positions):
            return False
        first_index = len(model.points) + len(pending_points)
        pending_points.extend(local_positions)
        uvs = _slice_uvs(texcoords, cursor, vertex_count)
        face = geometry_scene.GeometryFace(
            vertex_indices=list(range(first_index, first_index + vertex_count)),
            material_name=str(meta.get("material_name") or material_name),
            uv_coords=uvs,
            extras={
                "source_format": "gltf",
                "polygon_index": int(polygon_index),
                "primitive_index": primitive_index,
                "original_surface_index": meta.get("surface_index"),
                "original_plane_index": meta.get("plane_index"),
                "original_vertex_indices": list(meta.get("vertex_indices") or []),
                "texture_name": str(meta.get("texture_name") or ""),
            },
        )
        pending_faces.append(face)
        cursor += vertex_count
    model.points.extend(pending_points)
    model.faces.extend(pending_faces)
    return True


def _append_triangle_faces(
    model: geometry_scene.GeometryModel,
    positions: Sequence[Vec3],
    texcoords: Sequence[Optional[Vec2]],
    indices: Sequence[int],
    material_name: str,
    primitive_index: int,
    *,
    warning_reason: str = "",
) -> None:
    skipped = 0
    for offset in range(0, len(indices) - 2, 3):
        tri_indices = indices[offset:offset + 3]
        if any(index < 0 or index >= len(positions) for index in tri_indices):
            skipped += 1
            continue
        tri_positions = [positions[index] for index in tri_indices]
        if _positions_are_degenerate(tri_positions):
            skipped += 1
            continue
        first_index = len(model.points)
        model.points.extend(tri_positions)
        uvs = [
            texcoords[index] if 0 <= index < len(texcoords) else None
            for index in tri_indices
        ]
        model.faces.append(geometry_scene.GeometryFace(
            vertex_indices=[first_index, first_index + 1, first_index + 2],
            material_name=material_name,
            uv_coords=uvs,
            extras={
                "source_format": "gltf",
                "primitive_index": primitive_index,
                "triangle_index": offset // 3,
            },
        ))
    if skipped:
        reason = f" ({warning_reason})" if warning_reason else ""
        _record_import_warning(
            model,
            f"glTF import skipped {skipped} degenerate or invalid triangle(s) "
            f"in model {model.name!r} primitive {primitive_index}{reason}."
        )


def _positions_are_degenerate(vertices: Sequence[Vec3]) -> bool:
    if len({(float(x), float(y), float(z)) for x, y, z in vertices}) < 3:
        return True
    nx = ny = nz = 0.0
    for index, current in enumerate(vertices):
        nxt = vertices[(index + 1) % len(vertices)]
        nx += (current[1] - nxt[1]) * (current[2] + nxt[2])
        ny += (current[2] - nxt[2]) * (current[0] + nxt[0])
        nz += (current[0] - nxt[0]) * (current[1] + nxt[1])
    return math.sqrt(nx * nx + ny * ny + nz * nz) <= 1.0e-8


def _record_import_warning(model: geometry_scene.GeometryModel, message: str) -> None:
    datmeta = model.extras.get("datmeta") if isinstance(model.extras, dict) else None
    if not isinstance(datmeta, dict):
        return
    warnings = datmeta.setdefault("import_warnings", [])
    if isinstance(warnings, list) and message not in warnings:
        warnings.append(message)


def _slice_uvs(texcoords: Sequence[Optional[Vec2]], start: int, count: int) -> List[Optional[Vec2]]:
    if len(texcoords) < start + count:
        return [None for _ in range(count)]
    return [texcoords[start + index] for index in range(count)]


def _model_polygon_meta(model: geometry_scene.GeometryModel) -> Dict[int, Dict[str, Any]]:
    mesh_extras = model.extras.get("mesh_extras") if isinstance(model.extras, dict) else {}
    model_name = str((mesh_extras or {}).get("MM9_model_name") or model.name)
    datmeta = model.extras.get("datmeta") if isinstance(model.extras, dict) else {}
    if isinstance(datmeta, dict):
        models = datmeta.get("models") or []
    else:
        models = []
    for item in models:
        if isinstance(item, dict) and str(item.get("name") or "") == model_name:
            return {
                int(poly.get("index")): poly
                for poly in item.get("polygons", []) or []
                if isinstance(poly, dict) and isinstance(poly.get("index"), int)
            }
    return {}


def _material_name(
    gltf: Dict[str, Any],
    primitive: Dict[str, Any],
    material_texture_map: Dict[str, str],
) -> str:
    material_index = primitive.get("material")
    materials = gltf.get("materials") or []
    if isinstance(material_index, int) and 0 <= material_index < len(materials):
        material = materials[material_index]
        if isinstance(material, dict):
            name = str(material.get("name") or f"Material_{material_index}")
            if name in material_texture_map:
                return name
            texture = str((material.get("extras") or {}).get("MM9_texture") or "")
            if texture:
                return name
            return name
    return "Default"


def _primitive_indices(
    gltf: Dict[str, Any],
    buffers: Sequence[bytes],
    primitive: Dict[str, Any],
    vertex_count: int,
) -> List[int]:
    if "indices" not in primitive:
        return list(range(vertex_count))
    return [int(value) for value in _read_accessor(gltf, buffers, int(primitive["indices"]), expected_type="SCALAR")]


def _read_accessor(
    gltf: Dict[str, Any],
    buffers: Sequence[bytes],
    accessor_index: int,
    *,
    expected_type: str,
) -> List[Any]:
    accessors = gltf.get("accessors") or []
    buffer_views = gltf.get("bufferViews") or []
    if accessor_index < 0 or accessor_index >= len(accessors):
        raise GltfImportError(f"missing accessor {accessor_index}")
    accessor = accessors[accessor_index]
    if not isinstance(accessor, dict):
        raise GltfImportError(f"accessor {accessor_index} must be an object")
    if accessor.get("sparse"):
        raise GltfImportError("sparse glTF accessors are not supported yet")
    if str(accessor.get("type") or "") != expected_type:
        raise GltfImportError(f"accessor {accessor_index} must have type {expected_type}")
    view_index = int(accessor.get("bufferView", -1))
    if view_index < 0 or view_index >= len(buffer_views) or not isinstance(buffer_views[view_index], dict):
        raise GltfImportError(f"accessor {accessor_index} references missing bufferView")
    view = buffer_views[view_index]
    buffer_index = int(view.get("buffer", 0))
    if buffer_index < 0 or buffer_index >= len(buffers):
        raise GltfImportError(f"bufferView {view_index} references missing buffer")
    component_type = int(accessor.get("componentType", 0))
    count = int(accessor.get("count", 0))
    components = _component_count(expected_type)
    component_format, component_size = _component_format(component_type)
    element_size = component_size * components
    stride = int(view.get("byteStride") or element_size)
    if stride < element_size:
        raise GltfImportError("glTF byteStride is smaller than accessor element size")
    start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    data = buffers[buffer_index]
    result: List[Any] = []
    for index in range(count):
        offset = start + index * stride
        end = offset + element_size
        if end > len(data):
            raise GltfImportError(f"accessor {accessor_index} reads past buffer end")
        values = struct.unpack_from("<" + component_format * components, data, offset)
        if expected_type == "SCALAR":
            result.append(values[0])
        else:
            result.append(tuple(float(value) for value in values))
    return result


def _component_count(type_name: str) -> int:
    if type_name == "SCALAR":
        return 1
    if type_name == "VEC2":
        return 2
    if type_name == "VEC3":
        return 3
    raise GltfImportError(f"unsupported accessor type {type_name!r}")


def _component_format(component_type: int) -> Tuple[str, int]:
    if component_type == 5126:
        return "f", 4
    if component_type == 5125:
        return "I", 4
    if component_type == 5123:
        return "H", 2
    if component_type == 5121:
        return "B", 1
    raise GltfImportError(f"unsupported glTF componentType {component_type}")


def _node_matrix(node: Dict[str, Any]) -> Mat4:
    if isinstance(node.get("matrix"), list) and len(node["matrix"]) == 16:
        values = [float(value) for value in node["matrix"]]
        return [
            [values[0], values[4], values[8], values[12]],
            [values[1], values[5], values[9], values[13]],
            [values[2], values[6], values[10], values[14]],
            [values[3], values[7], values[11], values[15]],
        ]
    translation = _vec(node.get("translation"), 3, [0.0, 0.0, 0.0])
    scale = _vec(node.get("scale"), 3, [1.0, 1.0, 1.0])
    rotation = _vec(node.get("rotation"), 4, [0.0, 0.0, 0.0, 1.0])
    rot = _quat_matrix(rotation)
    for row in range(3):
        for col in range(3):
            rot[row][col] *= scale[col]
        rot[row][3] = translation[row]
    return rot


def _quat_matrix(q: Sequence[float]) -> Mat4:
    x, y, z, w = [float(value) for value in q]
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length <= 1.0e-8:
        x = y = z = 0.0
        w = 1.0
    else:
        x, y, z, w = x / length, y / length, z / length, w / length
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return [
        [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy), 0.0],
        [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx), 0.0],
        [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy), 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _vec(value: Any, count: int, default: Sequence[float]) -> List[float]:
    if not isinstance(value, list) or len(value) != count:
        return [float(item) for item in default]
    return [float(item) for item in value]


def _matrix_point(matrix: Mat4, point: Vec3) -> Vec3:
    x, y, z = point
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3],
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3],
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3],
    )
