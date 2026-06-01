"""
OBJ + sidecar export for MM9 DAT BSP geometry round trips.

Stage 1 is intentionally read-only: it writes inspection/editing artifacts for
Blender, but it does not create pending ops or write patched DAT bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from core import bsp


Vec3 = Tuple[float, float, float]
TextureSizeLookup = Callable[[str], Optional[Tuple[int, int]]]


@dataclass(frozen=True)
class RoundTripExportResult:
    obj_path: str
    mtl_path: str
    meta_path: str
    model_count: int
    polygon_count: int
    vertex_count: int


def export_roundtrip(
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
) -> RoundTripExportResult:
    """Export *bsp_world* to OBJ/MTL plus ``.datmeta.json`` sidecar.

    By default coordinates are reflected from DAT game-space into the editor's
    display/Blender space (X becomes -X).  Use ``raw_coordinates=True`` for a
    debugging export that leaves DAT coordinates untouched.
    """
    if bsp_world is None:
        raise ValueError("BSP world is required")
    os.makedirs(output_dir, exist_ok=True)

    label = _export_base_name(base_name, source_path, bsp_world)
    obj_path = os.path.abspath(os.path.join(output_dir, f"{label}_geometry.obj"))
    mtl_path = os.path.abspath(os.path.join(output_dir, f"{label}_geometry.mtl"))
    meta_path = os.path.abspath(os.path.join(output_dir, f"{label}_geometry.datmeta.json"))

    effective_include_helpers = bool(
        include_helper_geometry or raw_coordinates or selected_model_names
    )
    models = _selected_models(
        bsp_world,
        selected_model_names,
        include_helper_geometry=effective_include_helpers,
    )
    material_names = _material_names(models)
    material_by_texture = {texture: name for texture, name in material_names}

    transform = _identity_transform() if raw_coordinates else _display_transform()
    reverse_winding = not raw_coordinates

    obj_lines: List[str] = [
        "# MM9 DAT geometry export",
        "# Edit with the accompanying .datmeta.json sidecar; material names are not authoritative.",
        f"mtllib {os.path.basename(mtl_path)}",
        "",
    ]
    meta_models: List[Dict[str, Any]] = []
    vertex_index = 1
    vt_index = 1
    total_polygons = 0
    total_vertices = 0

    for model_index, model in enumerate(models):
        object_name = _obj_name(model.name, model_index)
        obj_lines.append(f"o {object_name}")
        obj_lines.append(f"g {object_name}")

        point_indices: List[int] = []
        for point in model.points:
            x, y, z = _transform_point(point, raw_coordinates)
            obj_lines.append(f"v {_fmt_float(x)} {_fmt_float(y)} {_fmt_float(z)}")
            point_indices.append(vertex_index)
            vertex_index += 1
            total_vertices += 1

        last_material = None
        poly_meta: List[Dict[str, Any]] = []
        for poly_index, polygon in enumerate(model.polygons):
            if not effective_include_helpers and _is_non_render_polygon(model, polygon):
                continue
            texture_name = model.texture_name_for(polygon) or "Default"
            material_name = material_by_texture.get(texture_name, _sanitize_material(texture_name))
            if material_name != last_material:
                obj_lines.append(f"usemtl {material_name}")
                last_material = material_name

            face_parts: List[str] = []
            poly_vertices = list(polygon.vertex_indices)
            if reverse_winding:
                poly_vertices.reverse()
            for source_vertex_index in poly_vertices:
                if source_vertex_index < 0 or source_vertex_index >= len(model.points):
                    continue
                pos = model.points[source_vertex_index]
                u, v = _uv_for_vertex(model, polygon, pos, texture_name, texture_size_lookup)
                obj_lines.append(f"vt {_fmt_float(u)} {_fmt_float(v)}")
                face_parts.append(f"{point_indices[source_vertex_index]}/{vt_index}")
                vt_index += 1
            if len(face_parts) >= 3:
                obj_lines.append("f " + " ".join(face_parts))
                total_polygons += 1

            poly_meta.append({
                "index": poly_index,
                "surface_index": polygon.surface_index,
                "plane_index": polygon.plane_index,
                "vertex_indices": list(polygon.vertex_indices),
                "texture_name": texture_name,
                "material_name": material_name,
            })

        obj_lines.append("")
        meta_models.append(_model_metadata(model_index, model, objects or [], poly_meta))

    mtl_lines = _build_mtl_lines(material_names)
    meta = {
        "version": 1,
        "kind": "mm9_dat_geometry_roundtrip",
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
                "Default export reflects X to match the editor viewport. "
                "Skyboxes, VisBSP, helper materials, and PhysicsBSP top caps "
                "are omitted unless include_helper_geometry is enabled."
            ),
        },
        "export_options": {
            "include_helper_geometry": bool(effective_include_helpers),
            "raw_coordinates": bool(raw_coordinates),
        },
        "files": {
            "obj": os.path.basename(obj_path),
            "mtl": os.path.basename(mtl_path),
        },
        "materials": [
            {"material_name": material, "texture_name": texture}
            for texture, material in material_names
        ],
        "models": meta_models,
        "parse_warnings": list(getattr(bsp_world, "parse_warnings", []) or []),
    }

    _write_text(obj_path, "\n".join(obj_lines).rstrip() + "\n")
    _write_text(mtl_path, "\n".join(mtl_lines).rstrip() + "\n")
    _write_text(meta_path, json.dumps(meta, indent=2, ensure_ascii=False) + "\n")

    return RoundTripExportResult(
        obj_path=obj_path,
        mtl_path=mtl_path,
        meta_path=meta_path,
        model_count=len(models),
        polygon_count=total_polygons,
        vertex_count=total_vertices,
    )


def _selected_models(
    bsp_world: bsp.BspWorld,
    selected_model_names: Optional[Sequence[str]],
    *,
    include_helper_geometry: bool,
) -> List[bsp.WorldModelMesh]:
    models = list(getattr(bsp_world, "world_models", []) or [])
    if not selected_model_names:
        selected = models
    else:
        wanted = {str(name or "").lower() for name in selected_model_names}
        selected = [model for model in models if str(model.name or "").lower() in wanted]
    if include_helper_geometry:
        return selected
    return [model for model in selected if not _is_non_render_model(model)]


def _export_base_name(base_name: str, source_path: str, bsp_world: bsp.BspWorld) -> str:
    if base_name:
        stem = base_name
    elif source_path:
        stem = os.path.splitext(os.path.basename(source_path))[0]
    else:
        stem = str(getattr(bsp_world, "world_info", "") or "level").strip()[:48]
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_")
    return cleaned or "level"


def _obj_name(name: str, index: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", str(name or "")).strip("_")
    return cleaned or f"WorldModel_{index}"


def _material_names(models: Iterable[bsp.WorldModelMesh]) -> List[Tuple[str, str]]:
    result: List[Tuple[str, str]] = []
    used: set[str] = set()
    for model in models:
        for texture in getattr(model, "texture_names", []) or ["Default"]:
            texture = str(texture or "Default")
            if any(existing == texture for existing, _mat in result):
                continue
            material = _sanitize_material(texture)
            base = material
            suffix = 2
            while material.lower() in used:
                material = f"{base}_{suffix}"
                suffix += 1
            used.add(material.lower())
            result.append((texture, material))
    if not result:
        result.append(("Default", "Default"))
    return result


def _sanitize_material(texture_name: str) -> str:
    stem = str(texture_name or "Default").replace("\\", "/").rsplit("/", 1)[-1]
    stem = os.path.splitext(stem)[0]
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_")
    return cleaned or "Default"


def _build_mtl_lines(material_names: Sequence[Tuple[str, str]]) -> List[str]:
    lines = [
        "# MM9 DAT material names. Exact texture paths are stored in the .datmeta.json sidecar.",
        "",
    ]
    for index, (texture, material) in enumerate(material_names):
        hue = ((index * 37) % 100) / 100.0
        r, g, b = _fallback_color(hue)
        lines.extend([
            f"newmtl {material}",
            f"# mm9_texture {texture}",
            f"Kd {_fmt_float(r)} {_fmt_float(g)} {_fmt_float(b)}",
            "Ka 0.000000 0.000000 0.000000",
            "Ks 0.000000 0.000000 0.000000",
            "",
        ])
    return lines


def _fallback_color(hue: float) -> Tuple[float, float, float]:
    # Tiny HSV-ish fallback so Blender distinguishes material slots even when
    # DTX files are unavailable.
    h = (hue % 1.0) * 6.0
    c = 0.65
    x = c * (1.0 - abs((h % 2.0) - 1.0))
    if h < 1.0:
        rgb = (c, x, 0.25)
    elif h < 2.0:
        rgb = (x, c, 0.25)
    elif h < 3.0:
        rgb = (0.25, c, x)
    elif h < 4.0:
        rgb = (0.25, x, c)
    elif h < 5.0:
        rgb = (x, 0.25, c)
    else:
        rgb = (c, 0.25, x)
    return rgb


def _uv_for_vertex(
    model: bsp.WorldModelMesh,
    polygon: bsp.Polygon,
    pos: Vec3,
    texture_name: str,
    texture_size_lookup: Optional[TextureSizeLookup],
) -> Tuple[float, float]:
    if polygon.surface_index < 0 or polygon.surface_index >= len(model.surfaces):
        return (0.0, 0.0)
    tex_size = None
    if texture_size_lookup is not None:
        try:
            tex_size = texture_size_lookup(texture_name)
        except Exception:
            tex_size = None
    width, height = tex_size if tex_size else (128, 128)
    return model.surfaces[polygon.surface_index].compute_uv(pos, width, height)


def _model_metadata(
    index: int,
    model: bsp.WorldModelMesh,
    objects: Sequence[Any],
    polygons: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "index": index,
        "name": model.name,
        "role": _model_role(model, objects),
        "raw_start": model.raw_start,
        "raw_end": model.raw_end,
        "world_bsp_start": model.world_bsp_start,
        "world_bsp_end": model.world_bsp_end,
        "next_world_item": model.next_world_item,
        "min_box": list(model.min_box),
        "max_box": list(model.max_box),
        "translation": list(model.translation),
        "point_count": len(model.points),
        "polygon_count": len(model.polygons),
        "surface_count": len(model.surfaces),
        "texture_names": list(model.texture_names),
        "related_objects": _related_objects(model.name, objects),
        "polygons": list(polygons),
    }


def _model_role(model: bsp.WorldModelMesh, objects: Sequence[Any]) -> str:
    key = str(model.name or "").lower()
    if key == "physicsbsp":
        return "physics"
    if key == "visbsp":
        return "visibility"
    if model.is_skybox():
        return "skybox"
    object_names = {
        str(obj.get("Name") or "").lower()
        for obj in objects
        if hasattr(obj, "get")
    }
    if key in object_names:
        return "controller_geometry"
    return model.category()


def _is_non_render_model(model: bsp.WorldModelMesh) -> bool:
    name = str(getattr(model, "name", "") or "").lower()
    return model.is_skybox() or name == "visbsp"


def _is_non_render_polygon(model: bsp.WorldModelMesh, polygon: bsp.Polygon) -> bool:
    texture_name = model.texture_name_for(polygon) or ""
    if _helper_role_group_for_texture(texture_name) is not None:
        return True
    return _is_physics_world_ceiling_cap(model, polygon)


def _normalise_texture_name(tex_name: str) -> str:
    return "/" + str(tex_name or "").replace("\\", "/").upper().lstrip("/")


def _helper_role_group_for_texture(tex_name: str) -> Optional[str]:
    norm = _normalise_texture_name(tex_name)
    if "/LEVELTEXTURES/MISC/RAIL.DTX" in norm:
        return "aiRail"
    if ("/LEVELTEXTURES/MISC/FIRETHROUGH.DTX" in norm
            or "/LEVELTEXTURES/MISC/INVISIBLE.DTX" in norm
            or "/LEVELTEXTURES/INVISIBLE.DTX" in norm):
        return "collision"
    if ("/LEVELTEXTURES/TERRAIN/WATERMARKER.DTX" in norm
            or "/SPRITES/WATER/" in norm
            or "/SPRITETEXTURES/WATER/" in norm):
        return "water"
    if "/LEVELTEXTURES/MISC/GREENSCREEN.DTX" in norm:
        return "trigger"
    if "/LEVELTEXTURES/MISC/SOUNDONLY.DTX" in norm:
        return "sound"
    if "/SKYBOX/SKYMARKER.DTX" in norm or norm.endswith(".SPR"):
        return "skyVisibility"
    return None


def _is_physics_world_ceiling_cap(model: bsp.WorldModelMesh, polygon: bsp.Polygon) -> bool:
    name = str(getattr(model, "name", "") or "").lower()
    if name != "physicsbsp" or len(polygon.vertex_indices) < 3:
        return False
    if not model.min_box or not model.max_box:
        return False
    try:
        points = [model.points[i] for i in polygon.vertex_indices]
    except IndexError:
        return False
    max_y = float(model.max_box[1])
    if max(abs(float(point[1]) - max_y) for point in points) > 2.0:
        return False
    bounds_x = abs(float(model.max_box[0]) - float(model.min_box[0]))
    bounds_z = abs(float(model.max_box[2]) - float(model.min_box[2]))
    bounds_area = bounds_x * bounds_z
    if bounds_area <= 1.0:
        return False
    span_x = max(float(point[0]) for point in points) - min(float(point[0]) for point in points)
    span_z = max(float(point[2]) for point in points) - min(float(point[2]) for point in points)
    return (span_x * span_z) >= bounds_area * 0.5


def _related_objects(model_name: str, objects: Sequence[Any]) -> List[Dict[str, Any]]:
    key = str(model_name or "").lower()
    result: List[Dict[str, Any]] = []
    for index, obj in enumerate(objects):
        if not hasattr(obj, "get") or str(obj.get("Name") or "").lower() != key:
            continue
        result.append({
            "index": index,
            "type_str": getattr(obj, "type_str", ""),
            "name": obj.get("Name"),
            "properties": {
                prop_name: _jsonable(obj.get(prop_name))
                for prop_name in _interesting_props(obj)
            },
        })
    return result


def _interesting_props(obj: Any) -> List[str]:
    keep = {
        "Name", "Pos", "Rotation", "MoveDir", "MoveDist", "RotationPoint",
        "RotationAngles", "DoubleDoorName", "Locked", "StartOpen",
        "Visible", "Solid", "RayHit", "BoxPhysics", "PortalName",
        "OpenSound", "CloseSound", "LockedSound", "MoveSound",
    }
    return [
        prop.name
        for prop in getattr(obj, "props", []) or []
        if getattr(prop, "name", None) in keep
    ]


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _transform_point(point: Vec3, raw_coordinates: bool) -> Vec3:
    x, y, z = point
    if raw_coordinates:
        return (float(x), float(y), float(z))
    return (-float(x), float(y), float(z))


def _display_transform() -> List[List[float]]:
    return [
        [-1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _identity_transform() -> List[List[float]]:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _fmt_float(value: float) -> str:
    return f"{float(value):.6f}"


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
