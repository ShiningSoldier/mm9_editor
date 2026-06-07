"""glTF export for MM9 DAT BSP geometry."""

from __future__ import annotations

import hashlib
import json
import os
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from core import bsp
from features.dat_editing import export_roundtrip


Vec3 = Tuple[float, float, float]
Vec2 = Tuple[float, float]
TextureSizeLookup = export_roundtrip.TextureSizeLookup


@dataclass(frozen=True)
class GltfExportResult:
    gltf_path: str
    bin_path: str
    meta_path: str
    model_count: int
    polygon_count: int
    vertex_count: int
    triangle_count: int


def export_geometry_scene_gltf(
    scene,
    output_dir: str,
    *,
    base_name: str = "",
) -> GltfExportResult:
    """Export a GeometryScene as inspection-only glTF with source metadata."""
    if scene is None:
        raise ValueError("GeometryScene is required")
    os.makedirs(output_dir, exist_ok=True)
    label = export_roundtrip._sanitize_material(base_name or os.path.splitext(os.path.basename(scene.source_path or "source_prefab"))[0])
    gltf_path = os.path.abspath(os.path.join(output_dir, f"{label}_source_geometry.gltf"))
    bin_path = os.path.abspath(os.path.join(output_dir, f"{label}_source_geometry.bin"))
    meta_path = os.path.abspath(os.path.join(output_dir, f"{label}_source_geometry.gltf.datmeta.json"))

    material_map = scene.material_texture_map()
    material_names = sorted(material_map.keys() or {"Default"})
    material_index = {name: index for index, name in enumerate(material_names)}
    gltf_materials = [
        {
            "name": name,
            "pbrMetallicRoughness": {
                "baseColorFactor": [*export_roundtrip._fallback_color(((index * 37) % 100) / 100.0), 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
            "extras": {
                "MM9_texture": material_map.get(name, name),
            },
        }
        for index, name in enumerate(material_names)
    ]
    binary = bytearray()
    buffer_views: List[Dict[str, Any]] = []
    accessors: List[Dict[str, Any]] = []
    meshes: List[Dict[str, Any]] = []
    nodes: List[Dict[str, Any]] = []
    meta_models: List[Dict[str, Any]] = []
    total_polygons = 0
    total_vertices = 0
    total_triangles = 0

    for model_index, model in enumerate(scene.mesh_models()):
        positions: List[Vec3] = []
        texcoords: List[Vec2] = []
        indices: List[int] = []
        polygon_indices: List[int] = []
        polygon_meta: List[Dict[str, Any]] = []
        for face_index, face in enumerate(model.faces):
            if len(face.vertex_indices) < 3:
                continue
            first = len(positions)
            face_positions = [model.points[index] for index in face.vertex_indices if 0 <= index < len(model.points)]
            if len(face_positions) != len(face.vertex_indices):
                continue
            positions.extend(face_positions)
            if len(face.uv_coords) == len(face.vertex_indices) and all(uv is not None for uv in face.uv_coords):
                texcoords.extend((float(uv[0]), float(uv[1])) for uv in face.uv_coords if uv is not None)
            else:
                texcoords.extend((0.0, 0.0) for _ in face.vertex_indices)
            for offset in range(1, len(face.vertex_indices) - 1):
                indices.extend([first, first + offset, first + offset + 1])
            polygon_indices.append(face_index)
            polygon_meta.append({
                "index": face_index,
                "vertex_indices": list(face.vertex_indices),
                "material_name": face.material_name,
                "texture_name": material_map.get(face.material_name, face.material_name),
                "source_extras": dict(face.extras or {}),
            })
        if not indices:
            continue
        position_accessor = _append_vec3_accessor(binary, buffer_views, accessors, positions, target=34962)
        uv_accessor = _append_vec2_accessor(binary, buffer_views, accessors, texcoords, target=34962)
        index_accessor = _append_u32_accessor(binary, buffer_views, accessors, indices, target=34963)
        material_name = model.faces[0].material_name if model.faces else "Default"
        mesh_index = len(meshes)
        meshes.append({
            "name": model.name,
            "primitives": [{
                "attributes": {"POSITION": position_accessor, "TEXCOORD_0": uv_accessor},
                "indices": index_accessor,
                "material": material_index.get(material_name, 0),
                "mode": 4,
                "extras": {"MM9_polygon_indices": polygon_indices},
            }],
            "extras": {
                "MM9_model_name": model.name,
                "MM9_model_index": model_index,
                "MM9_source_extras": dict(model.extras or {}),
            },
        })
        nodes.append({
            "name": model.name,
            "mesh": mesh_index,
            "extras": {
                "MM9_model_name": model.name,
                "MM9_role": str((model.extras or {}).get("role") or ""),
                "MM9_source_extras": dict(model.extras or {}),
            },
        })
        meta_models.append({
            "name": model.name,
            "index": model_index,
            "source_extras": dict(model.extras or {}),
            "polygons": polygon_meta,
        })
        total_polygons += len(polygon_meta)
        total_vertices += len(positions)
        total_triangles += len(indices) // 3

    meta = {
        "version": 1,
        "kind": "mm9_geometry_scene_inspection",
        "format": "gltf",
        "source": {
            "path": scene.source_path,
            "scene_metadata": dict(scene.metadata or {}),
        },
        "coordinate_system": {
            "export_space": "raw_source",
            "dat_to_export_matrix": export_roundtrip._identity_transform(),
            "export_to_dat_matrix": export_roundtrip._identity_transform(),
            "notes": "Inspection-only source-prefab glTF; not a full DAT rebuild target.",
        },
        "files": {
            "gltf": os.path.basename(gltf_path),
            "bin": os.path.basename(bin_path),
            "sidecar": os.path.basename(meta_path),
        },
        "materials": [
            {"material_name": name, "texture_name": material_map.get(name, name)}
            for name in material_names
        ],
        "models": meta_models,
    }
    gltf = {
        "asset": {"version": "2.0", "generator": "mm9_editor GeometryScene glTF inspection exporter"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": gltf_materials,
        "buffers": [{"uri": os.path.basename(bin_path), "byteLength": len(binary)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "extras": {"MM9_datmeta": meta},
    }
    with open(bin_path, "wb") as f:
        f.write(binary)
    _write_json(gltf_path, gltf)
    _write_json(meta_path, meta)
    return GltfExportResult(
        gltf_path=gltf_path,
        bin_path=bin_path,
        meta_path=meta_path,
        model_count=len(meta_models),
        polygon_count=total_polygons,
        vertex_count=total_vertices,
        triangle_count=total_triangles,
    )


def export_gltf_roundtrip(
    bsp_world: bsp.BspWorld,
    source_dat: bytes,
    output_dir: str,
    *,
    source_path: str = "",
    base_name: str = "",
    objects: Optional[Sequence[Any]] = None,
    selected_model_names: Optional[Sequence[str]] = None,
    raw_coordinates: bool = False,
    include_helper_geometry: bool = False,
    texture_size_lookup: Optional[TextureSizeLookup] = None,
) -> GltfExportResult:
    if bsp_world is None:
        raise ValueError("BSP world is required")
    os.makedirs(output_dir, exist_ok=True)

    label = export_roundtrip._export_base_name(base_name, source_path, bsp_world)
    gltf_path = os.path.abspath(os.path.join(output_dir, f"{label}_geometry.gltf"))
    bin_path = os.path.abspath(os.path.join(output_dir, f"{label}_geometry.bin"))
    meta_path = os.path.abspath(os.path.join(output_dir, f"{label}_geometry.gltf.datmeta.json"))

    effective_include_helpers = bool(
        include_helper_geometry or raw_coordinates or selected_model_names
    )
    models = export_roundtrip._selected_models(
        bsp_world,
        selected_model_names,
        include_helper_geometry=effective_include_helpers,
    )
    material_names = export_roundtrip._material_names(models)
    material_by_texture = {texture: material for texture, material in material_names}
    gltf_materials = [
        {
            "name": material,
            "pbrMetallicRoughness": {
                "baseColorFactor": [*export_roundtrip._fallback_color(((index * 37) % 100) / 100.0), 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
            "extras": {
                "MM9_texture": texture,
            },
        }
        for index, (texture, material) in enumerate(material_names)
    ]
    material_index_by_name = {
        material: index for index, (_texture, material) in enumerate(material_names)
    }

    binary = bytearray()
    buffer_views: List[Dict[str, Any]] = []
    accessors: List[Dict[str, Any]] = []
    meshes: List[Dict[str, Any]] = []
    nodes: List[Dict[str, Any]] = []
    meta_models: List[Dict[str, Any]] = []
    total_polygons = 0
    total_vertices = 0
    total_triangles = 0

    for model_index, model in enumerate(models):
        object_name = export_roundtrip._obj_name(model.name, model_index)
        primitive_groups = _primitive_groups_for_model(
            model,
            material_by_texture,
            material_index_by_name,
            raw_coordinates=raw_coordinates,
            effective_include_helpers=effective_include_helpers,
            texture_size_lookup=texture_size_lookup,
        )
        if not primitive_groups:
            continue
        primitives: List[Dict[str, Any]] = []
        poly_meta: List[Dict[str, Any]] = []
        model_vertex_count = 0
        model_triangle_count = 0
        for group in primitive_groups:
            position_accessor = _append_vec3_accessor(
                binary,
                buffer_views,
                accessors,
                group["positions"],
                target=34962,
            )
            uv_accessor = _append_vec2_accessor(
                binary,
                buffer_views,
                accessors,
                group["texcoords"],
                target=34962,
            )
            index_accessor = _append_u32_accessor(
                binary,
                buffer_views,
                accessors,
                group["indices"],
                target=34963,
            )
            primitives.append({
                "attributes": {
                    "POSITION": position_accessor,
                    "TEXCOORD_0": uv_accessor,
                },
                "indices": index_accessor,
                "material": group["material_index"],
                "mode": 4,
                "extras": {
                    "MM9_polygon_indices": group["polygon_indices"],
                },
            })
            model_vertex_count += len(group["positions"])
            model_triangle_count += len(group["indices"]) // 3
            poly_meta.extend(group["polygon_meta"])

        mesh_index = len(meshes)
        meshes.append({
            "name": object_name,
            "primitives": primitives,
            "extras": {
                "MM9_model_name": model.name,
                "MM9_model_index": model_index,
            },
        })
        nodes.append({
            "name": object_name,
            "mesh": mesh_index,
            "extras": {
                "MM9_model_name": model.name,
                "MM9_role": export_roundtrip._model_role(model, objects or []),
            },
        })
        meta_models.append(export_roundtrip._model_metadata(model_index, model, objects or [], poly_meta))
        total_polygons += len(poly_meta)
        total_vertices += model_vertex_count
        total_triangles += model_triangle_count

    meta = _metadata(
        bsp_world,
        source_dat,
        source_path,
        gltf_path,
        bin_path,
        meta_path,
        material_names,
        meta_models,
        raw_coordinates=raw_coordinates,
        include_helper_geometry=effective_include_helpers,
    )
    gltf = {
        "asset": {
            "version": "2.0",
            "generator": "mm9_editor glTF geometry exporter",
        },
        "scene": 0,
        "scenes": [
            {"nodes": list(range(len(nodes)))}
        ],
        "nodes": nodes,
        "meshes": meshes,
        "materials": gltf_materials,
        "buffers": [
            {
                "uri": os.path.basename(bin_path),
                "byteLength": len(binary),
            }
        ],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "extras": {
            "MM9_datmeta": meta,
        },
    }

    with open(bin_path, "wb") as f:
        f.write(binary)
    _write_json(gltf_path, gltf)
    _write_json(meta_path, meta)
    return GltfExportResult(
        gltf_path=gltf_path,
        bin_path=bin_path,
        meta_path=meta_path,
        model_count=len(meta_models),
        polygon_count=total_polygons,
        vertex_count=total_vertices,
        triangle_count=total_triangles,
    )


def _primitive_groups_for_model(
    model: bsp.WorldModelMesh,
    material_by_texture: Dict[str, str],
    material_index_by_name: Dict[str, int],
    *,
    raw_coordinates: bool,
    effective_include_helpers: bool,
    texture_size_lookup: Optional[TextureSizeLookup],
) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for poly_index, polygon in enumerate(model.polygons):
        if not effective_include_helpers and export_roundtrip._is_non_render_polygon(model, polygon):
            continue
        texture_name = model.texture_name_for(polygon) or "Default"
        material_name = material_by_texture.get(texture_name, export_roundtrip._sanitize_material(texture_name))
        group = groups.setdefault(material_name, {
            "positions": [],
            "texcoords": [],
            "indices": [],
            "polygon_indices": [],
            "polygon_meta": [],
            "material_index": material_index_by_name[material_name],
        })
        poly_vertices = list(polygon.vertex_indices)
        if not raw_coordinates:
            poly_vertices.reverse()
        corner_indices: List[int] = []
        for source_vertex_index in poly_vertices:
            if source_vertex_index < 0 or source_vertex_index >= len(model.points):
                continue
            point = model.points[source_vertex_index]
            position = export_roundtrip._transform_point(point, raw_coordinates)
            uv = export_roundtrip._uv_for_vertex(model, polygon, point, texture_name, texture_size_lookup)
            corner_indices.append(len(group["positions"]))
            group["positions"].append(position)
            group["texcoords"].append(uv)
        if len(corner_indices) < 3:
            continue
        for offset in range(1, len(corner_indices) - 1):
            group["indices"].extend([
                corner_indices[0],
                corner_indices[offset],
                corner_indices[offset + 1],
            ])
        group["polygon_indices"].append(poly_index)
        group["polygon_meta"].append({
            "index": poly_index,
            "surface_index": polygon.surface_index,
            "plane_index": polygon.plane_index,
            "vertex_indices": list(polygon.vertex_indices),
            "texture_name": texture_name,
            "material_name": material_name,
        })
    return [group for group in groups.values() if group["indices"]]


def _metadata(
    bsp_world: bsp.BspWorld,
    source_dat: bytes,
    source_path: str,
    gltf_path: str,
    bin_path: str,
    meta_path: str,
    material_names: Sequence[Tuple[str, str]],
    meta_models: Sequence[Dict[str, Any]],
    *,
    raw_coordinates: bool,
    include_helper_geometry: bool,
) -> Dict[str, Any]:
    transform = export_roundtrip._identity_transform() if raw_coordinates else export_roundtrip._display_transform()
    return {
        "version": 1,
        "kind": "mm9_dat_geometry_roundtrip",
        "format": "gltf",
        "source": {
            "path": source_path,
            "sha256": hashlib.sha256(source_dat).hexdigest(),
            "size": len(source_dat),
            "dat_version": bsp_world.version,
            "object_data_pos": bsp_world.obj_pos,
            "render_data_pos": bsp_world.ren_pos,
            "world_model_table_start": bsp_world.world_model_table_start,
        },
        "coordinate_system": {
            "export_space": "raw_dat" if raw_coordinates else "blender_display",
            "dat_to_export_matrix": transform,
            "export_to_dat_matrix": transform,
            "notes": (
                "glTF export reflects X by default to match the editor viewport. "
                "MM9 metadata is embedded in glTF extras and duplicated in the sidecar."
            ),
        },
        "export_options": {
            "include_helper_geometry": bool(include_helper_geometry),
            "raw_coordinates": bool(raw_coordinates),
        },
        "files": {
            "gltf": os.path.basename(gltf_path),
            "bin": os.path.basename(bin_path),
            "sidecar": os.path.basename(meta_path),
        },
        "materials": [
            {"material_name": material, "texture_name": texture}
            for texture, material in material_names
        ],
        "models": list(meta_models),
        "parse_warnings": list(getattr(bsp_world, "parse_warnings", []) or []),
    }


def _append_vec3_accessor(
    binary: bytearray,
    buffer_views: List[Dict[str, Any]],
    accessors: List[Dict[str, Any]],
    values: Sequence[Vec3],
    *,
    target: int,
) -> int:
    raw = b"".join(struct.pack("<3f", *value) for value in values)
    view = _append_buffer_view(binary, buffer_views, raw, target=target)
    mins = [min(float(value[i]) for value in values) for i in range(3)]
    maxs = [max(float(value[i]) for value in values) for i in range(3)]
    return _append_accessor(accessors, view, 5126, len(values), "VEC3", mins, maxs)


def _append_vec2_accessor(
    binary: bytearray,
    buffer_views: List[Dict[str, Any]],
    accessors: List[Dict[str, Any]],
    values: Sequence[Vec2],
    *,
    target: int,
) -> int:
    raw = b"".join(struct.pack("<2f", *value) for value in values)
    view = _append_buffer_view(binary, buffer_views, raw, target=target)
    return _append_accessor(accessors, view, 5126, len(values), "VEC2")


def _append_u32_accessor(
    binary: bytearray,
    buffer_views: List[Dict[str, Any]],
    accessors: List[Dict[str, Any]],
    values: Sequence[int],
    *,
    target: int,
) -> int:
    raw = b"".join(struct.pack("<I", int(value)) for value in values)
    view = _append_buffer_view(binary, buffer_views, raw, target=target)
    return _append_accessor(accessors, view, 5125, len(values), "SCALAR")


def _append_buffer_view(
    binary: bytearray,
    buffer_views: List[Dict[str, Any]],
    raw: bytes,
    *,
    target: int,
) -> int:
    while len(binary) % 4:
        binary.append(0)
    offset = len(binary)
    binary.extend(raw)
    view = {
        "buffer": 0,
        "byteOffset": offset,
        "byteLength": len(raw),
        "target": target,
    }
    buffer_views.append(view)
    return len(buffer_views) - 1


def _append_accessor(
    accessors: List[Dict[str, Any]],
    buffer_view: int,
    component_type: int,
    count: int,
    type_name: str,
    mins: Optional[Sequence[float]] = None,
    maxs: Optional[Sequence[float]] = None,
) -> int:
    accessor: Dict[str, Any] = {
        "bufferView": buffer_view,
        "componentType": component_type,
        "count": count,
        "type": type_name,
    }
    if mins is not None:
        accessor["min"] = [float(value) for value in mins]
    if maxs is not None:
        accessor["max"] = [float(value) for value in maxs]
    accessors.append(accessor)
    return len(accessors) - 1


def _write_json(path: str, doc: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
