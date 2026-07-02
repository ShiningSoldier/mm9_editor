"""Shared Terrain*/PhysicsBSP identity helpers for DAT reconstruction."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from core import bsp


DEFAULT_TERRAIN_MODEL = "Terrain0"
PHYSICS_BSP_MODEL = "PhysicsBSP"
VIS_BSP_MODEL = "VisBSP"

HELPER_TEXTURE_ROLES: Dict[str, str] = {
    "/LEVELTEXTURES/MISC/RAIL.DTX": "aiRail",
    "/LEVELTEXTURES/MISC/FIRETHROUGH.DTX": "collision",
    "/LEVELTEXTURES/MISC/INVISIBLE.DTX": "collision",
    "/LEVELTEXTURES/INVISIBLE.DTX": "collision",
    "/LEVELTEXTURES/TERRAIN/WATERMARKER.DTX": "water",
    "/LEVELTEXTURES/MISC/GREENSCREEN.DTX": "trigger",
    "/LEVELTEXTURES/MISC/SOUNDONLY.DTX": "sound",
    "/SKYBOX/SKYMARKER.DTX": "skyVisibility",
}


def is_terrain_name(name: object) -> bool:
    """Return true for compiled DAT world-model names in the Terrain* family."""
    return str(name or "").lower().startswith("terrain")


def is_terrain_model(model: object) -> bool:
    """Return true when *model* is a compiled Terrain* world model."""
    return is_terrain_name(getattr(model, "name", ""))


def is_physics_bsp_name(name: object) -> bool:
    return str(name or "").lower() == PHYSICS_BSP_MODEL.lower()


def is_physics_bsp_model(model: object) -> bool:
    return is_physics_bsp_name(getattr(model, "name", ""))


def is_vis_bsp_name(name: object) -> bool:
    return str(name or "").lower() == VIS_BSP_MODEL.lower()


def is_vis_bsp_model(model: object) -> bool:
    return is_vis_bsp_name(getattr(model, "name", ""))


def terrain_model_names(
    bsp_world: bsp.BspWorld,
    *,
    preferred_name: str = DEFAULT_TERRAIN_MODEL,
) -> List[str]:
    """Return Terrain* world-model names, preferring Terrain0 when present."""
    models = list(getattr(bsp_world, "world_models", []) or [])
    preferred_key = str(preferred_name or "").lower()
    preferred = [
        str(model.name)
        for model in models
        if str(model.name or "").lower() == preferred_key
    ]
    terrain = [
        str(model.name)
        for model in models
        if is_terrain_model(model) and str(model.name or "").lower() != preferred_key
    ]
    return preferred + terrain


def default_dat_to_ed_model_names(
    bsp_world: bsp.BspWorld,
    *,
    include_skyboxes: bool = False,
) -> Tuple[str, ...]:
    """Return DAT world models eligible for default direct Brush reconstruction."""
    names: List[str] = []
    for model in getattr(bsp_world, "world_models", ()) or ():
        name = str(getattr(model, "name", "") or "")
        if not name:
            continue
        if is_terrain_model(model):
            continue
        if is_physics_bsp_model(model):
            continue
        if is_vis_bsp_model(model):
            continue
        try:
            is_skybox = bool(getattr(model, "is_skybox", lambda: False)())
        except Exception:
            is_skybox = True
        if is_skybox and not include_skyboxes:
            continue
        if not getattr(model, "points", None) or not getattr(model, "polygons", None):
            continue
        if model_has_only_helper_textures(model):
            continue
        names.append(name)
    return tuple(names)


def model_by_name(models: Sequence[object], name: str) -> Optional[object]:
    """Return the first model with a case-insensitive DAT world-model name match."""
    key = str(name or "").lower()
    for model in models:
        if str(getattr(model, "name", "") or "").lower() == key:
            return model
    return None


def helper_texture_role(texture_name: object) -> Optional[str]:
    """Return a semantic helper role for known non-render source textures."""
    norm = "/" + str(texture_name or "").replace("\\", "/").upper().lstrip("/")
    if norm.endswith(".SPR"):
        return "sprite"
    for marker, role in HELPER_TEXTURE_ROLES.items():
        if marker in norm:
            return role
    if "/SPRITES/WATER/" in norm or "/SPRITETEXTURES/WATER/" in norm:
        return "water"
    return None


def helper_texture_roles_for_model(model: object) -> Dict[str, int]:
    """Count known helper texture roles used by a DAT world model."""
    roles: Dict[str, int] = {}
    for polygon in getattr(model, "polygons", ()) or ():
        try:
            texture = model.texture_name_for(polygon)
        except Exception:
            texture = ""
        role = helper_texture_role(texture)
        if role:
            roles[role] = roles.get(role, 0) + 1
    return roles


def model_has_only_helper_textures(model: object) -> bool:
    """Return true when every textured polygon is a known helper texture."""
    polygons = tuple(getattr(model, "polygons", ()) or ())
    if not polygons:
        return False
    textured_count = 0
    helper_count = 0
    for polygon in polygons:
        try:
            texture = model.texture_name_for(polygon)
        except Exception:
            texture = ""
        if not texture:
            continue
        textured_count += 1
        if helper_texture_role(texture):
            helper_count += 1
    return textured_count > 0 and helper_count == textured_count
