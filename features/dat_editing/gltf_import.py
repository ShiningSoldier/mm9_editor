"""Fail-closed glTF 2.0 geometry reader for the planned glTF -> ED flow.

The reader stops at :class:`GeometryScene`.  It does not create DEDit brushes,
patch DAT records, infer a coordinate-system conversion, or restore any of the
retired editable mesh-sidecar operations.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import unquote, urlsplit

import _path_setup  # noqa: F401
from features.dat_editing import geometry_scene


Vec2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]
Mat4 = Tuple[Tuple[float, float, float, float], ...]

_GLB_MAGIC = 0x46546C67
_GLB_JSON_CHUNK = 0x4E4F534A
_GLB_BIN_CHUNK = 0x004E4942
_TRIANGLES_MODE = 4
_GEOMETRY_EXTENSIONS = {
    "EXT_meshopt_compression",
    "KHR_draco_mesh_compression",
    "KHR_mesh_quantization",
}
_COMPONENT_FORMATS: Dict[int, Tuple[str, int]] = {
    5121: ("B", 1),
    5123: ("H", 2),
    5125: ("I", 4),
    5126: ("f", 4),
}
_TYPE_COMPONENT_COUNTS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
}


class GltfImportError(ValueError):
    """A structured, user-reportable glTF contract failure."""

    def __init__(self, message: str, *, code: str = "invalid_gltf", location: str = "") -> None:
        self.code = str(code or "invalid_gltf")
        self.location = str(location or "")
        self.detail = str(message)
        prefix = f"[{self.code}]"
        if self.location:
            prefix += f" {self.location}:"
        super().__init__(f"{prefix} {self.detail}")


@dataclass(frozen=True)
class _BufferPayload:
    index: int
    data: bytes
    uri: str
    resolved_path: str
    declared_byte_length: int

    def report_dict(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "uri": self.uri,
            "resolved_path": self.resolved_path or None,
            "declared_byte_length": self.declared_byte_length,
            "byte_length": len(self.data),
            "sha256": hashlib.sha256(self.data).hexdigest(),
        }


@dataclass
class _ReadState:
    warnings: List[str]
    ignored_features: Set[str]
    selected_node_count: int = 0
    ignored_non_mesh_node_count: int = 0
    mesh_instance_count: int = 0
    primitive_count: int = 0
    triangle_count: int = 0


def load_gltf_geometry_scene(path: str) -> geometry_scene.GeometryScene:
    """Load the Phase-2 static glTF subset into a format-neutral scene.

    Coordinates are returned in baked glTF world space.  The later conversion
    service is responsible for applying the explicit glTF-world -> DEDit
    coordinate matrix and unit scale from ``docs/gltf_to_ed.md``.
    """
    absolute = os.path.abspath(os.fspath(path))
    if not os.path.isfile(absolute):
        raise GltfImportError(
            f"source file was not found: {absolute}",
            code="source_not_found",
            location="source",
        )

    extension = os.path.splitext(absolute)[1].lower()
    warnings: List[str] = []
    if extension == ".glb":
        gltf, glb_bin, source_bytes = _load_glb_document(absolute, warnings)
        source_format = "glb"
    elif extension == ".gltf":
        gltf, source_bytes = _load_json_document(absolute)
        glb_bin = None
        source_format = "gltf"
    else:
        raise GltfImportError(
            f"expected a .gltf or .glb file, got {extension or '<no extension>'}",
            code="unsupported_source_extension",
            location="source",
        )

    asset = _validate_asset(gltf)
    required_extensions = _string_array(gltf, "extensionsRequired")
    blocked_extensions = sorted(set(required_extensions) & _GEOMETRY_EXTENSIONS)
    if blocked_extensions:
        raise GltfImportError(
            "unsupported required geometry extension(s): " + ", ".join(blocked_extensions),
            code="unsupported_geometry_extension",
            location="extensionsRequired",
        )

    buffers = _load_buffers(gltf, absolute, glb_bin)
    materials, material_names = _load_materials(gltf)
    nodes = _object_array(gltf, "nodes")
    meshes = _object_array(gltf, "meshes")
    scenes = _object_array(gltf, "scenes")
    selected_scene_index = _selected_scene_index(gltf, scenes)
    animated_nodes = _animation_targets(gltf, len(nodes))
    state = _ReadState(warnings=warnings, ignored_features=set())

    models: List[geometry_scene.GeometryModel] = []
    used_model_names: Dict[str, int] = {}
    owner_by_node: Dict[int, str] = {}
    selected_scene = scenes[selected_scene_index]
    root_nodes = selected_scene.get("nodes", [])
    if not isinstance(root_nodes, list):
        raise GltfImportError(
            "scene nodes must be an array",
            code="invalid_scene_nodes",
            location=f"scenes[{selected_scene_index}].nodes",
        )

    for root_ordinal, raw_node_index in enumerate(root_nodes):
        node_index = _required_index(
            raw_node_index,
            len(nodes),
            location=f"scenes[{selected_scene_index}].nodes[{root_ordinal}]",
            code="invalid_node_reference",
        )
        _visit_node(
            gltf,
            buffers,
            nodes,
            meshes,
            material_names,
            node_index=node_index,
            parent_matrix=_identity_matrix(),
            parent_owner=f"scene:{selected_scene_index}",
            path=(),
            active_stack=(),
            animated_ancestor=None,
            animated_nodes=animated_nodes,
            owner_by_node=owner_by_node,
            used_model_names=used_model_names,
            models=models,
            state=state,
        )

    if any(face.material_name == "Default" for model in models for face in model.faces):
        if not any(material.name == "Default" for material in materials):
            materials.append(geometry_scene.GeometryMaterial(
                name="Default",
                texture_name="",
                extras={
                    "source_index": None,
                    "source_name": "",
                    "resolution_source": "unresolved_default",
                    "gltf_extras": {},
                },
            ))

    used_extensions = _string_array(gltf, "extensionsUsed")
    ignored_extensions = sorted(set(used_extensions) - _GEOMETRY_EXTENSIONS)
    for extension_name in ignored_extensions:
        state.ignored_features.add(f"extension:{extension_name}")
    if gltf.get("cameras"):
        state.ignored_features.add("cameras")
    if gltf.get("animations"):
        state.ignored_features.add("animations_outside_selected_meshes")
    if gltf.get("skins"):
        state.ignored_features.add("skins_outside_selected_meshes")

    metadata: Dict[str, object] = {
        "kind": "gltf_geometry_scene",
        "format": source_format,
        "source": {
            "path": absolute,
            "byte_length": len(source_bytes),
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "buffers": [buffer.report_dict() for buffer in buffers],
        },
        "asset": {
            "version": asset["version"],
            "min_version": asset.get("minVersion"),
            "generator": asset.get("generator"),
            "copyright": asset.get("copyright"),
        },
        "selected_scene_index": selected_scene_index,
        "selected_scene_name": str(selected_scene.get("name") or ""),
        "coordinate_system": {
            "space": "gltf_world",
            "node_transforms_baked": True,
            "dedit_coordinate_conversion_applied": False,
        },
        "inventory": {
            "scene_count": len(scenes),
            "node_count": len(nodes),
            "selected_node_count": state.selected_node_count,
            "ignored_non_mesh_node_count": state.ignored_non_mesh_node_count,
            "mesh_count": len(meshes),
            "mesh_instance_count": state.mesh_instance_count,
            "primitive_count": state.primitive_count,
            "triangle_count": state.triangle_count,
            "source_material_count": len(material_names),
            "material_count": len(materials),
            "model_count": len(models),
        },
        "extensions_used": used_extensions,
        "extensions_required": required_extensions,
        "ignored_features": sorted(state.ignored_features),
        "warnings": list(state.warnings),
        # Retain the historical metadata key for GeometryScene consumers that
        # already display import warnings.
        "import_warnings": list(state.warnings),
    }
    return geometry_scene.GeometryScene(
        source_path=absolute,
        models=models,
        materials=materials,
        metadata=metadata,
    )


def _load_json_document(path: str) -> Tuple[Dict[str, Any], bytes]:
    try:
        with open(path, "rb") as source:
            data = source.read()
    except OSError as exc:
        raise GltfImportError(
            str(exc), code="source_read_failed", location="source"
        ) from exc
    try:
        decoded = data.decode("utf-8-sig")
        document = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GltfImportError(
            f"invalid UTF-8 JSON: {exc}", code="invalid_json", location="source"
        ) from exc
    if not isinstance(document, dict):
        raise GltfImportError(
            "root must be a JSON object", code="invalid_root", location="source"
        )
    return document, data


def _load_glb_document(
    path: str,
    warnings: List[str],
) -> Tuple[Dict[str, Any], Optional[bytes], bytes]:
    try:
        with open(path, "rb") as source:
            data = source.read()
    except OSError as exc:
        raise GltfImportError(
            str(exc), code="source_read_failed", location="source"
        ) from exc
    if len(data) < 12:
        raise GltfImportError(
            "GLB header is shorter than 12 bytes", code="invalid_glb_header", location="source"
        )
    magic, version, declared_length = struct.unpack_from("<III", data, 0)
    if magic != _GLB_MAGIC:
        raise GltfImportError(
            "GLB magic is invalid", code="invalid_glb_magic", location="source"
        )
    if version != 2:
        raise GltfImportError(
            f"only GLB version 2 is supported, got {version}",
            code="unsupported_glb_version",
            location="source",
        )
    if declared_length != len(data):
        raise GltfImportError(
            f"declared GLB length is {declared_length}, actual length is {len(data)}",
            code="invalid_glb_length",
            location="source",
        )

    json_chunk: Optional[bytes] = None
    bin_chunk: Optional[bytes] = None
    offset = 12
    chunk_index = 0
    while offset < declared_length:
        if offset + 8 > declared_length:
            raise GltfImportError(
                "truncated GLB chunk header",
                code="invalid_glb_chunk",
                location=f"chunks[{chunk_index}]",
            )
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        if chunk_length % 4:
            raise GltfImportError(
                "GLB chunk length must be aligned to four bytes",
                code="invalid_glb_chunk",
                location=f"chunks[{chunk_index}]",
            )
        end = offset + chunk_length
        if end > declared_length:
            raise GltfImportError(
                "GLB chunk extends past the declared file length",
                code="invalid_glb_chunk",
                location=f"chunks[{chunk_index}]",
            )
        chunk = data[offset:end]
        offset = end
        if chunk_type == _GLB_JSON_CHUNK:
            if json_chunk is not None or chunk_index != 0:
                raise GltfImportError(
                    "the GLB JSON chunk must be first and unique",
                    code="invalid_glb_json_chunk",
                    location=f"chunks[{chunk_index}]",
                )
            json_chunk = chunk
        elif chunk_type == _GLB_BIN_CHUNK:
            if bin_chunk is not None:
                raise GltfImportError(
                    "multiple GLB BIN chunks are not supported",
                    code="invalid_glb_bin_chunk",
                    location=f"chunks[{chunk_index}]",
                )
            bin_chunk = chunk
        else:
            warnings.append(
                f"Ignored unknown GLB chunk {chunk_index} with type 0x{chunk_type:08X}."
            )
        chunk_index += 1

    if json_chunk is None:
        raise GltfImportError(
            "GLB JSON chunk is missing", code="missing_glb_json_chunk", location="source"
        )
    try:
        document = json.loads(json_chunk.decode("utf-8").rstrip(" \t\r\n\0"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GltfImportError(
            f"invalid GLB JSON chunk: {exc}",
            code="invalid_json",
            location="chunks[0]",
        ) from exc
    if not isinstance(document, dict):
        raise GltfImportError(
            "GLB JSON root must be an object", code="invalid_root", location="chunks[0]"
        )
    return document, bin_chunk, data


def _validate_asset(gltf: Dict[str, Any]) -> Dict[str, Any]:
    asset = gltf.get("asset")
    if not isinstance(asset, dict):
        raise GltfImportError(
            "asset must be an object", code="invalid_asset", location="asset"
        )
    version = asset.get("version")
    if not isinstance(version, str) or not version.strip():
        raise GltfImportError(
            "asset.version must be a string", code="invalid_asset_version", location="asset.version"
        )
    try:
        major = int(version.strip().split(".", 1)[0])
    except ValueError as exc:
        raise GltfImportError(
            f"invalid asset version {version!r}",
            code="invalid_asset_version",
            location="asset.version",
        ) from exc
    if major != 2:
        raise GltfImportError(
            f"only glTF 2.x is supported, got {version!r}",
            code="unsupported_asset_version",
            location="asset.version",
        )
    min_version = asset.get("minVersion")
    if min_version is not None:
        if not isinstance(min_version, str):
            raise GltfImportError(
                "asset.minVersion must be a string",
                code="invalid_asset_version",
                location="asset.minVersion",
            )
        try:
            min_major = int(min_version.strip().split(".", 1)[0])
        except ValueError as exc:
            raise GltfImportError(
                f"invalid minimum version {min_version!r}",
                code="invalid_asset_version",
                location="asset.minVersion",
            ) from exc
        if min_major > 2:
            raise GltfImportError(
                f"asset requires unsupported glTF version {min_version!r}",
                code="unsupported_asset_version",
                location="asset.minVersion",
            )
    return asset


def _load_buffers(
    gltf: Dict[str, Any],
    gltf_path: str,
    glb_bin: Optional[bytes],
) -> List[_BufferPayload]:
    definitions = _object_array(gltf, "buffers")
    base_dir = os.path.realpath(os.path.dirname(gltf_path))
    result: List[_BufferPayload] = []
    for index, definition in enumerate(definitions):
        location = f"buffers[{index}]"
        declared = _nonnegative_int(
            definition.get("byteLength"),
            code="invalid_buffer_length",
            location=f"{location}.byteLength",
        )
        raw_uri = definition.get("uri")
        uri = "" if raw_uri is None else raw_uri
        report_uri = ""
        resolved_path = ""
        if uri == "":
            if index != 0 or glb_bin is None:
                raise GltfImportError(
                    "a buffer without a URI requires the first GLB BIN chunk",
                    code="missing_buffer_uri",
                    location=f"{location}.uri",
                )
            data = glb_bin
        elif not isinstance(uri, str):
            raise GltfImportError(
                "buffer URI must be a string",
                code="invalid_buffer_uri",
                location=f"{location}.uri",
            )
        elif uri.lower().startswith("data:"):
            data = _decode_buffer_data_uri(uri, location=f"{location}.uri")
            report_uri = uri.partition(",")[0] + ",<payload omitted>"
        else:
            resolved_path = _resolve_external_buffer_path(
                base_dir,
                uri,
                location=f"{location}.uri",
            )
            try:
                with open(resolved_path, "rb") as source:
                    data = source.read()
            except OSError as exc:
                raise GltfImportError(
                    str(exc),
                    code="buffer_read_failed",
                    location=f"{location}.uri",
                ) from exc
            report_uri = uri
        if len(data) < declared:
            raise GltfImportError(
                f"declared {declared} bytes but only {len(data)} are available",
                code="truncated_buffer",
                location=location,
            )
        result.append(_BufferPayload(
            index=index,
            data=data,
            uri=report_uri,
            resolved_path=resolved_path,
            declared_byte_length=declared,
        ))
    return result


def _decode_buffer_data_uri(uri: str, *, location: str) -> bytes:
    header, separator, payload = uri.partition(",")
    if not separator or ";base64" not in header.lower():
        raise GltfImportError(
            "buffer data URI must use base64 encoding",
            code="unsupported_data_uri",
            location=location,
        )
    try:
        return base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise GltfImportError(
            "buffer data URI contains invalid base64",
            code="invalid_data_uri",
            location=location,
        ) from exc


def _resolve_external_buffer_path(base_dir: str, uri: str, *, location: str) -> str:
    parsed = urlsplit(uri)
    if parsed.scheme or parsed.netloc:
        raise GltfImportError(
            f"network or absolute URI is not supported: {uri!r}",
            code="unsupported_buffer_uri",
            location=location,
        )
    if parsed.query or parsed.fragment:
        raise GltfImportError(
            "buffer URI queries and fragments are not supported",
            code="unsupported_buffer_uri",
            location=location,
        )
    decoded_path = unquote(parsed.path).replace("/", os.sep)
    if not decoded_path or os.path.isabs(decoded_path):
        raise GltfImportError(
            f"invalid external buffer path: {uri!r}",
            code="unsafe_buffer_path",
            location=location,
        )
    candidate = os.path.realpath(os.path.join(base_dir, decoded_path))
    try:
        common = os.path.commonpath((base_dir, candidate))
    except ValueError as exc:
        raise GltfImportError(
            f"external buffer path escapes the glTF directory: {uri!r}",
            code="unsafe_buffer_path",
            location=location,
        ) from exc
    if os.path.normcase(common) != os.path.normcase(base_dir):
        raise GltfImportError(
            f"external buffer path escapes the glTF directory: {uri!r}",
            code="unsafe_buffer_path",
            location=location,
        )
    return candidate


def _load_materials(
    gltf: Dict[str, Any],
) -> Tuple[List[geometry_scene.GeometryMaterial], List[str]]:
    definitions = _object_array(gltf, "materials")
    result: List[geometry_scene.GeometryMaterial] = []
    names: List[str] = []
    used_names: Dict[str, int] = {}
    for index, definition in enumerate(definitions):
        raw_name = definition.get("name")
        source_name = raw_name.strip() if isinstance(raw_name, str) else ""
        base_name = source_name or f"Material_{index}"
        name = _unique_name(base_name, used_names)
        raw_extras = definition.get("extras")
        gltf_extras = dict(raw_extras) if isinstance(raw_extras, dict) else {}
        mm9_texture = gltf_extras.get("MM9_texture")
        if isinstance(mm9_texture, str) and mm9_texture.strip():
            texture_name = mm9_texture.strip()
            resolution_source = "extras"
        elif source_name.lower().endswith(".dtx"):
            texture_name = source_name
            resolution_source = "material_name"
        else:
            texture_name = ""
            resolution_source = "unresolved"
        result.append(geometry_scene.GeometryMaterial(
            name=name,
            texture_name=texture_name,
            extras={
                "source_index": index,
                "source_name": source_name,
                "resolution_source": resolution_source,
                "gltf_extras": gltf_extras,
                "ignored_pbr_fields": sorted(
                    key
                    for key in (
                        "pbrMetallicRoughness",
                        "normalTexture",
                        "occlusionTexture",
                        "emissiveTexture",
                        "emissiveFactor",
                        "alphaMode",
                        "alphaCutoff",
                        "doubleSided",
                    )
                    if key in definition
                ),
            },
        ))
        names.append(name)
    return result, names


def _selected_scene_index(gltf: Dict[str, Any], scenes: Sequence[Dict[str, Any]]) -> int:
    if "scene" in gltf:
        return _required_index(
            gltf.get("scene"),
            len(scenes),
            location="scene",
            code="invalid_scene_reference",
        )
    if len(scenes) == 1:
        return 0
    raise GltfImportError(
        f"root scene is absent and scene count is {len(scenes)}; selection is ambiguous",
        code="ambiguous_scene_selection",
        location="scene",
    )


def _animation_targets(gltf: Dict[str, Any], node_count: int) -> Dict[int, Tuple[str, ...]]:
    definitions = _object_array(gltf, "animations")
    targets: Dict[int, List[str]] = {}
    for animation_index, definition in enumerate(definitions):
        channels = definition.get("channels", [])
        if not isinstance(channels, list):
            raise GltfImportError(
                "animation channels must be an array",
                code="invalid_animation",
                location=f"animations[{animation_index}].channels",
            )
        for channel_index, channel in enumerate(channels):
            location = f"animations[{animation_index}].channels[{channel_index}]"
            if not isinstance(channel, dict) or not isinstance(channel.get("target"), dict):
                raise GltfImportError(
                    "animation channel target must be an object",
                    code="invalid_animation",
                    location=f"{location}.target",
                )
            target = channel["target"]
            node_index = _required_index(
                target.get("node"),
                node_count,
                location=f"{location}.target.node",
                code="invalid_node_reference",
            )
            target_path = target.get("path")
            if target_path not in {"translation", "rotation", "scale", "weights"}:
                raise GltfImportError(
                    f"unsupported animation target path {target_path!r}",
                    code="invalid_animation",
                    location=f"{location}.target.path",
                )
            targets.setdefault(node_index, []).append(str(target_path))
    return {index: tuple(paths) for index, paths in targets.items()}


def _visit_node(
    gltf: Dict[str, Any],
    buffers: Sequence[_BufferPayload],
    nodes: Sequence[Dict[str, Any]],
    meshes: Sequence[Dict[str, Any]],
    material_names: Sequence[str],
    *,
    node_index: int,
    parent_matrix: Mat4,
    parent_owner: str,
    path: Tuple[str, ...],
    active_stack: Tuple[int, ...],
    animated_ancestor: Optional[int],
    animated_nodes: Dict[int, Tuple[str, ...]],
    owner_by_node: Dict[int, str],
    used_model_names: Dict[str, int],
    models: List[geometry_scene.GeometryModel],
    state: _ReadState,
) -> None:
    if node_index in active_stack:
        raise GltfImportError(
            "node hierarchy contains a cycle",
            code="node_cycle",
            location=f"nodes[{node_index}]",
        )
    existing_owner = owner_by_node.get(node_index)
    if existing_owner is not None:
        raise GltfImportError(
            f"node already belongs to {existing_owner}; glTF scene nodes must form a tree",
            code="node_multiple_parents",
            location=f"nodes[{node_index}]",
        )
    owner_by_node[node_index] = parent_owner

    node = nodes[node_index]
    state.selected_node_count += 1
    node_label = _source_label(node.get("name"), f"Node_{node_index}")
    node_path = path + (f"{node_label}[{node_index}]",)
    local_matrix = _node_matrix(node, node_index=node_index, warnings=state.warnings)
    world_matrix = _matrix_multiply(parent_matrix, local_matrix)
    effective_animated_ancestor = (
        animated_ancestor
        if animated_ancestor is not None
        else (node_index if node_index in animated_nodes else None)
    )

    if "mesh" in node:
        mesh_index = _required_index(
            node.get("mesh"),
            len(meshes),
            location=f"nodes[{node_index}].mesh",
            code="invalid_mesh_reference",
        )
        if "skin" in node:
            raise GltfImportError(
                "skinned mesh nodes are outside the static input subset",
                code="unsupported_skinning",
                location=f"nodes[{node_index}].skin",
            )
        if node.get("weights") is not None:
            raise GltfImportError(
                "node morph weights are outside the static input subset",
                code="unsupported_morph_targets",
                location=f"nodes[{node_index}].weights",
            )
        if effective_animated_ancestor is not None:
            paths = ", ".join(animated_nodes[effective_animated_ancestor])
            raise GltfImportError(
                f"animation target node {effective_animated_ancestor} affects this mesh instance ({paths})",
                code="unsupported_animation",
                location=f"nodes[{node_index}]",
            )
        model = _node_mesh_model(
            gltf,
            buffers,
            node,
            meshes[mesh_index],
            material_names,
            node_index=node_index,
            mesh_index=mesh_index,
            node_path=node_path,
            world_matrix=world_matrix,
            used_model_names=used_model_names,
            state=state,
        )
        models.append(model)
        state.mesh_instance_count += 1
    else:
        state.ignored_non_mesh_node_count += 1

    children = node.get("children", [])
    if not isinstance(children, list):
        raise GltfImportError(
            "node children must be an array",
            code="invalid_node_children",
            location=f"nodes[{node_index}].children",
        )
    for child_ordinal, raw_child_index in enumerate(children):
        child_index = _required_index(
            raw_child_index,
            len(nodes),
            location=f"nodes[{node_index}].children[{child_ordinal}]",
            code="invalid_node_reference",
        )
        _visit_node(
            gltf,
            buffers,
            nodes,
            meshes,
            material_names,
            node_index=child_index,
            parent_matrix=world_matrix,
            parent_owner=f"node:{node_index}",
            path=node_path,
            active_stack=active_stack + (node_index,),
            animated_ancestor=effective_animated_ancestor,
            animated_nodes=animated_nodes,
            owner_by_node=owner_by_node,
            used_model_names=used_model_names,
            models=models,
            state=state,
        )


def _node_mesh_model(
    gltf: Dict[str, Any],
    buffers: Sequence[_BufferPayload],
    node: Dict[str, Any],
    mesh: Dict[str, Any],
    material_names: Sequence[str],
    *,
    node_index: int,
    mesh_index: int,
    node_path: Tuple[str, ...],
    world_matrix: Mat4,
    used_model_names: Dict[str, int],
    state: _ReadState,
) -> geometry_scene.GeometryModel:
    if mesh.get("weights") is not None:
        raise GltfImportError(
            "mesh morph weights are outside the static input subset",
            code="unsupported_morph_targets",
            location=f"meshes[{mesh_index}].weights",
        )
    determinant = _linear_determinant(world_matrix)
    if abs(determinant) <= 1.0e-12:
        raise GltfImportError(
            "baked mesh transform is singular",
            code="singular_mesh_transform",
            location=f"nodes[{node_index}]",
        )
    winding_reversed = determinant < 0.0
    node_name = _source_label(node.get("name"), "")
    mesh_name = _source_label(mesh.get("name"), "")
    source_name = node_name or mesh_name or f"GltfNode{node_index}"
    model_name = _unique_name(source_name, used_model_names)
    model = geometry_scene.GeometryModel(
        name=model_name,
        extras={
            "source_format": "gltf",
            "source_name": source_name,
            "scene_node_index": node_index,
            "node_name": node_name,
            "node_path": list(node_path),
            "mesh_index": mesh_index,
            "mesh_name": mesh_name,
            "world_matrix": [list(row) for row in world_matrix],
            "transform_determinant": determinant,
            "winding_reversed": winding_reversed,
            "node_extras": _extras_dict(node.get("extras")),
            "mesh_extras": _extras_dict(mesh.get("extras")),
        },
    )

    primitives = mesh.get("primitives")
    if not isinstance(primitives, list) or not primitives:
        raise GltfImportError(
            "mesh primitives must be a non-empty array",
            code="invalid_mesh_primitives",
            location=f"meshes[{mesh_index}].primitives",
        )
    for primitive_index, primitive in enumerate(primitives):
        if not isinstance(primitive, dict):
            raise GltfImportError(
                "mesh primitive must be an object",
                code="invalid_mesh_primitive",
                location=f"meshes[{mesh_index}].primitives[{primitive_index}]",
            )
        _append_primitive(
            gltf,
            buffers,
            model,
            primitive,
            material_names,
            mesh_index=mesh_index,
            primitive_index=primitive_index,
            world_matrix=world_matrix,
            winding_reversed=winding_reversed,
            state=state,
        )
    return model


def _append_primitive(
    gltf: Dict[str, Any],
    buffers: Sequence[_BufferPayload],
    model: geometry_scene.GeometryModel,
    primitive: Dict[str, Any],
    material_names: Sequence[str],
    *,
    mesh_index: int,
    primitive_index: int,
    world_matrix: Mat4,
    winding_reversed: bool,
    state: _ReadState,
) -> None:
    location = f"meshes[{mesh_index}].primitives[{primitive_index}]"
    extensions = primitive.get("extensions")
    if isinstance(extensions, dict) and "KHR_draco_mesh_compression" in extensions:
        raise GltfImportError(
            "Draco-compressed primitives are outside the initial input subset",
            code="unsupported_geometry_extension",
            location=f"{location}.extensions.KHR_draco_mesh_compression",
        )
    mode = primitive.get("mode", _TRIANGLES_MODE)
    if type(mode) is not int or mode != _TRIANGLES_MODE:
        raise GltfImportError(
            f"only TRIANGLES mode (4) is supported, got {mode!r}",
            code="unsupported_primitive_mode",
            location=f"{location}.mode",
        )
    attributes = primitive.get("attributes")
    if not isinstance(attributes, dict):
        raise GltfImportError(
            "primitive attributes must be an object",
            code="invalid_primitive_attributes",
            location=f"{location}.attributes",
        )
    if "POSITION" not in attributes:
        raise GltfImportError(
            "POSITION attribute is required",
            code="missing_position_attribute",
            location=f"{location}.attributes",
        )
    if primitive.get("targets"):
        raise GltfImportError(
            "morph targets are outside the static input subset",
            code="unsupported_morph_targets",
            location=f"{location}.targets",
        )
    weighted_attributes = sorted(
        key for key in attributes if key.startswith("JOINTS_") or key.startswith("WEIGHTS_")
    )
    if weighted_attributes:
        raise GltfImportError(
            "skinning attributes are outside the static input subset: "
            + ", ".join(weighted_attributes),
            code="unsupported_skinning",
            location=f"{location}.attributes",
        )
    for ignored in sorted(set(attributes) - {"POSITION", "TEXCOORD_0"}):
        state.ignored_features.add(f"attribute:{ignored}")

    positions = _read_accessor(
        gltf,
        buffers,
        attributes["POSITION"],
        expected_type="VEC3",
        allowed_component_types=(5126,),
        semantic="POSITION",
        location=f"{location}.attributes.POSITION",
    )
    transformed_positions = [
        _matrix_point(world_matrix, (float(value[0]), float(value[1]), float(value[2])))
        for value in positions
    ]

    texcoords: Optional[List[Any]] = None
    if "TEXCOORD_0" in attributes:
        texcoords = _read_accessor(
            gltf,
            buffers,
            attributes["TEXCOORD_0"],
            expected_type="VEC2",
            allowed_component_types=(5126,),
            semantic="TEXCOORD_0",
            location=f"{location}.attributes.TEXCOORD_0",
        )
        if len(texcoords) != len(positions):
            raise GltfImportError(
                f"TEXCOORD_0 count {len(texcoords)} does not match POSITION count {len(positions)}",
                code="attribute_count_mismatch",
                location=f"{location}.attributes.TEXCOORD_0",
            )

    if "indices" in primitive:
        indices = [
            int(value)
            for value in _read_accessor(
                gltf,
                buffers,
                primitive["indices"],
                expected_type="SCALAR",
                allowed_component_types=(5121, 5123, 5125),
                semantic="indices",
                location=f"{location}.indices",
            )
        ]
    else:
        indices = list(range(len(positions)))
    if len(indices) % 3:
        raise GltfImportError(
            f"triangle index count must be divisible by 3, got {len(indices)}",
            code="invalid_triangle_count",
            location=location,
        )
    for index_offset, vertex_index in enumerate(indices):
        if vertex_index < 0 or vertex_index >= len(positions):
            raise GltfImportError(
                f"index {vertex_index} is outside POSITION count {len(positions)}",
                code="index_out_of_range",
                location=f"{location}.indices[{index_offset}]",
            )

    raw_material_index = primitive.get("material")
    if raw_material_index is None:
        material_index: Optional[int] = None
        material_name = "Default"
    else:
        material_index = _required_index(
            raw_material_index,
            len(material_names),
            location=f"{location}.material",
            code="invalid_material_reference",
        )
        material_name = material_names[material_index]

    first_point = len(model.points)
    model.points.extend(transformed_positions)
    for triangle_index, offset in enumerate(range(0, len(indices), 3)):
        source_indices = tuple(indices[offset:offset + 3])
        emitted_indices = source_indices
        if winding_reversed:
            emitted_indices = (source_indices[0], source_indices[2], source_indices[1])
        face_uvs: List[Optional[Vec2]]
        if texcoords is None:
            face_uvs = [None, None, None]
        else:
            face_uvs = [
                (float(texcoords[index][0]), float(texcoords[index][1]))
                for index in emitted_indices
            ]
        model.faces.append(geometry_scene.GeometryFace(
            vertex_indices=[first_point + index for index in emitted_indices],
            material_name=material_name,
            uv_coords=face_uvs,
            extras={
                "source_format": "gltf",
                "mesh_index": mesh_index,
                "primitive_index": primitive_index,
                "triangle_index": triangle_index,
                "material_index": material_index,
                "source_vertex_indices": list(source_indices),
                "winding_reversed": winding_reversed,
            },
        ))
    state.primitive_count += 1
    state.triangle_count += len(indices) // 3


def _read_accessor(
    gltf: Dict[str, Any],
    buffers: Sequence[_BufferPayload],
    raw_accessor_index: object,
    *,
    expected_type: str,
    allowed_component_types: Sequence[int],
    semantic: str,
    location: str,
) -> List[Any]:
    accessors = _object_array(gltf, "accessors")
    views = _object_array(gltf, "bufferViews")
    accessor_index = _required_index(
        raw_accessor_index,
        len(accessors),
        location=location,
        code="invalid_accessor_reference",
    )
    accessor = accessors[accessor_index]
    accessor_location = f"accessors[{accessor_index}]"
    if accessor.get("sparse") is not None:
        raise GltfImportError(
            "sparse accessors are outside the initial input subset",
            code="unsupported_sparse_accessor",
            location=accessor_location,
        )
    if accessor.get("type") != expected_type:
        raise GltfImportError(
            f"{semantic} accessor must have type {expected_type}, got {accessor.get('type')!r}",
            code="unsupported_accessor_type",
            location=f"{accessor_location}.type",
        )
    component_type = accessor.get("componentType")
    if type(component_type) is not int or component_type not in allowed_component_types:
        raise GltfImportError(
            f"{semantic} accessor has unsupported componentType {component_type!r}",
            code="unsupported_component_type",
            location=f"{accessor_location}.componentType",
        )
    if accessor.get("normalized") not in (None, False):
        raise GltfImportError(
            f"normalized {semantic} accessors are outside the initial input subset",
            code="unsupported_normalized_accessor",
            location=f"{accessor_location}.normalized",
        )
    count = _positive_int(
        accessor.get("count"),
        code="invalid_accessor_count",
        location=f"{accessor_location}.count",
    )
    view_index = _required_index(
        accessor.get("bufferView"),
        len(views),
        location=f"{accessor_location}.bufferView",
        code="invalid_buffer_view_reference",
    )
    view = views[view_index]
    view_location = f"bufferViews[{view_index}]"
    view_extensions = view.get("extensions")
    if isinstance(view_extensions, dict) and "EXT_meshopt_compression" in view_extensions:
        raise GltfImportError(
            "meshopt-compressed buffer views are outside the initial input subset",
            code="unsupported_geometry_extension",
            location=f"{view_location}.extensions.EXT_meshopt_compression",
        )
    buffer_index = _required_index(
        view.get("buffer"),
        len(buffers),
        location=f"{view_location}.buffer",
        code="invalid_buffer_reference",
    )
    buffer = buffers[buffer_index]
    view_offset = _optional_nonnegative_int(
        view.get("byteOffset"),
        default=0,
        code="invalid_buffer_view_offset",
        location=f"{view_location}.byteOffset",
    )
    view_length = _nonnegative_int(
        view.get("byteLength"),
        code="invalid_buffer_view_length",
        location=f"{view_location}.byteLength",
    )
    view_end = view_offset + view_length
    if view_end > buffer.declared_byte_length:
        raise GltfImportError(
            f"buffer view ends at {view_end}, beyond declared buffer size "
            f"{buffer.declared_byte_length}",
            code="buffer_view_out_of_range",
            location=view_location,
        )

    component_format, component_size = _COMPONENT_FORMATS[component_type]
    component_count = _TYPE_COMPONENT_COUNTS[expected_type]
    element_size = component_size * component_count
    accessor_offset = _optional_nonnegative_int(
        accessor.get("byteOffset"),
        default=0,
        code="invalid_accessor_offset",
        location=f"{accessor_location}.byteOffset",
    )
    if accessor_offset % component_size:
        raise GltfImportError(
            "accessor byteOffset is not aligned to its component size",
            code="misaligned_accessor",
            location=f"{accessor_location}.byteOffset",
        )
    raw_stride = view.get("byteStride")
    if raw_stride is None:
        stride = element_size
    else:
        stride = _positive_int(
            raw_stride,
            code="invalid_buffer_view_stride",
            location=f"{view_location}.byteStride",
        )
        if semantic == "indices":
            raise GltfImportError(
                "index buffer views may not define byteStride",
                code="invalid_buffer_view_stride",
                location=f"{view_location}.byteStride",
            )
        if stride < element_size or stride > 252 or stride % component_size:
            raise GltfImportError(
                f"byteStride {stride} is incompatible with element size {element_size}",
                code="invalid_buffer_view_stride",
                location=f"{view_location}.byteStride",
            )
    start = view_offset + accessor_offset
    if start % component_size:
        raise GltfImportError(
            "accessor data start is not aligned to its component size",
            code="misaligned_accessor",
            location=accessor_location,
        )
    last_end = start + (count - 1) * stride + element_size
    if start < view_offset or last_end > view_end:
        raise GltfImportError(
            f"accessor byte range [{start}, {last_end}) exceeds buffer view [{view_offset}, {view_end})",
            code="accessor_out_of_range",
            location=accessor_location,
        )

    result: List[Any] = []
    unpack_format = "<" + component_format * component_count
    for item_index in range(count):
        item_offset = start + item_index * stride
        values = struct.unpack_from(unpack_format, buffer.data, item_offset)
        if component_type == 5126 and not all(math.isfinite(float(value)) for value in values):
            raise GltfImportError(
                f"{semantic} accessor contains a non-finite value",
                code="nonfinite_accessor_value",
                location=f"{accessor_location}[{item_index}]",
            )
        if expected_type == "SCALAR":
            result.append(values[0])
        else:
            result.append(tuple(float(value) for value in values))
    return result


def _node_matrix(node: Dict[str, Any], *, node_index: int, warnings: List[str]) -> Mat4:
    has_matrix = "matrix" in node
    has_trs = any(key in node for key in ("translation", "rotation", "scale"))
    if has_matrix and has_trs:
        raise GltfImportError(
            "node may not define both matrix and TRS transforms",
            code="ambiguous_node_transform",
            location=f"nodes[{node_index}]",
        )
    if has_matrix:
        raw = node.get("matrix")
        if not isinstance(raw, list) or len(raw) != 16:
            raise GltfImportError(
                "node matrix must contain 16 finite numbers",
                code="invalid_node_transform",
                location=f"nodes[{node_index}].matrix",
            )
        values = _finite_numbers(raw, location=f"nodes[{node_index}].matrix")
        matrix: Mat4 = (
            (values[0], values[4], values[8], values[12]),
            (values[1], values[5], values[9], values[13]),
            (values[2], values[6], values[10], values[14]),
            (values[3], values[7], values[11], values[15]),
        )
        if any(abs(matrix[3][axis]) > 1.0e-8 for axis in range(3)) or abs(matrix[3][3] - 1.0) > 1.0e-8:
            raise GltfImportError(
                "node matrix must be affine",
                code="invalid_node_transform",
                location=f"nodes[{node_index}].matrix",
            )
        return matrix

    translation = _finite_vector(
        node.get("translation"),
        count=3,
        default=(0.0, 0.0, 0.0),
        location=f"nodes[{node_index}].translation",
    )
    scale = _finite_vector(
        node.get("scale"),
        count=3,
        default=(1.0, 1.0, 1.0),
        location=f"nodes[{node_index}].scale",
    )
    rotation = _finite_vector(
        node.get("rotation"),
        count=4,
        default=(0.0, 0.0, 0.0, 1.0),
        location=f"nodes[{node_index}].rotation",
    )
    length = math.sqrt(sum(value * value for value in rotation))
    if length <= 1.0e-12:
        raise GltfImportError(
            "node rotation quaternion has zero length",
            code="invalid_node_transform",
            location=f"nodes[{node_index}].rotation",
        )
    if abs(length - 1.0) > 1.0e-5:
        warnings.append(f"Normalized non-unit quaternion on node {node_index} (length={length:g}).")
    x, y, z, w = (value / length for value in rotation)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    rotation_matrix = (
        (1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)),
        (2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)),
        (2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)),
    )
    return (
        (
            rotation_matrix[0][0] * scale[0],
            rotation_matrix[0][1] * scale[1],
            rotation_matrix[0][2] * scale[2],
            translation[0],
        ),
        (
            rotation_matrix[1][0] * scale[0],
            rotation_matrix[1][1] * scale[1],
            rotation_matrix[1][2] * scale[2],
            translation[1],
        ),
        (
            rotation_matrix[2][0] * scale[0],
            rotation_matrix[2][1] * scale[1],
            rotation_matrix[2][2] * scale[2],
            translation[2],
        ),
        (0.0, 0.0, 0.0, 1.0),
    )


def _identity_matrix() -> Mat4:
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _matrix_multiply(left: Mat4, right: Mat4) -> Mat4:
    return tuple(
        tuple(sum(left[row][k] * right[k][column] for k in range(4)) for column in range(4))
        for row in range(4)
    )


def _matrix_point(matrix: Mat4, point: Vec3) -> Vec3:
    x, y, z = point
    result = (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3],
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3],
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3],
    )
    if not all(math.isfinite(value) for value in result):
        raise GltfImportError(
            "baked node transform produced a non-finite position",
            code="nonfinite_transformed_position",
            location="node transform",
        )
    return result


def _linear_determinant(matrix: Mat4) -> float:
    a, b, c = matrix[0][:3]
    d, e, f = matrix[1][:3]
    g, h, i = matrix[2][:3]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def _object_array(document: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    value = document.get(key, [])
    if not isinstance(value, list):
        raise GltfImportError(
            f"{key} must be an array", code="invalid_array", location=key
        )
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise GltfImportError(
                f"{key} item must be an object",
                code="invalid_array_item",
                location=f"{key}[{index}]",
            )
    return value


def _string_array(document: Dict[str, Any], key: str) -> List[str]:
    value = document.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise GltfImportError(
            f"{key} must be an array of strings", code="invalid_array", location=key
        )
    return list(value)


def _required_index(value: object, count: int, *, location: str, code: str) -> int:
    if type(value) is not int or value < 0 or value >= count:
        raise GltfImportError(
            f"index {value!r} is outside array length {count}", code=code, location=location
        )
    return value


def _positive_int(value: object, *, code: str, location: str) -> int:
    if type(value) is not int or value <= 0:
        raise GltfImportError(
            f"expected a positive integer, got {value!r}", code=code, location=location
        )
    return value


def _nonnegative_int(value: object, *, code: str, location: str) -> int:
    if type(value) is not int or value < 0:
        raise GltfImportError(
            f"expected a non-negative integer, got {value!r}", code=code, location=location
        )
    return value


def _optional_nonnegative_int(
    value: object,
    *,
    default: int,
    code: str,
    location: str,
) -> int:
    if value is None:
        return int(default)
    return _nonnegative_int(value, code=code, location=location)


def _finite_numbers(values: Sequence[object], *, location: str) -> Tuple[float, ...]:
    result: List[float] = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GltfImportError(
                f"expected a finite number, got {value!r}",
                code="invalid_node_transform",
                location=f"{location}[{index}]",
            )
        number = float(value)
        if not math.isfinite(number):
            raise GltfImportError(
                f"expected a finite number, got {value!r}",
                code="invalid_node_transform",
                location=f"{location}[{index}]",
            )
        result.append(number)
    return tuple(result)


def _finite_vector(
    value: object,
    *,
    count: int,
    default: Sequence[float],
    location: str,
) -> Tuple[float, ...]:
    if value is None:
        return tuple(float(item) for item in default)
    if not isinstance(value, list) or len(value) != count:
        raise GltfImportError(
            f"expected an array of {count} finite numbers",
            code="invalid_node_transform",
            location=location,
        )
    return _finite_numbers(value, location=location)


def _source_label(value: object, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _unique_name(base: str, used: Dict[str, int]) -> str:
    count = used.get(base, 0) + 1
    used[base] = count
    return base if count == 1 else f"{base}_{count}"


def _extras_dict(value: object) -> Dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


__all__ = ["GltfImportError", "load_gltf_geometry_scene"]
