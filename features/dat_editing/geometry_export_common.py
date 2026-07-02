"""Shared read-only geometry export helpers for MM9 DAT inspection."""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from core import bsp


Vec3 = Tuple[float, float, float]
TextureSizeLookup = Callable[[str], Optional[Tuple[int, int]]]


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


def _fallback_color(hue: float) -> Tuple[float, float, float]:
    # Tiny HSV-ish fallback so external viewers distinguish material slots even
    # when DTX files are unavailable.
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
