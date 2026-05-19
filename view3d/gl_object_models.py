"""
gl_object_models.py
===================

Best-effort ABC mesh rendering for placed WorldObjects.

This is intentionally conservative: models that the current ABC loader cannot
parse are skipped and remain visible through the existing billboard renderer.
Selection, dragging, and picking still use billboards; these meshes are only a
visual overlay for supported props.
"""

from __future__ import annotations

import math
import os
import sys
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

from catalog.actor_visuals import resolve_actor_visual
from view3d.abc_loader import load_abc, upload_abc_model
from view3d.gl_mesh import GpuMesh, delete_mesh, draw_mesh


_DEFAULT_COLOR: Tuple[float, float, float] = (0.55, 0.62, 0.58)
_logged_skin_misses: set = set()

_ALPHA_CUTOUT_TOKENS = (
    "branch", "branches", "leaf", "leaves", "twig", "foliage", "fern",
    "grass", "plant", "plants", "tree", "bush", "flower", "herb", "vine",
    "frond", "needle", "thorn", "web", "grate", "fence", "chain", "rail",
)
_ALPHA_BLEND_TOKENS = (
    "flame", "fire", "glow", "halo", "light", "lamp", "lantern", "candle",
    "torch", "smoke", "steam", "mist", "glass", "window",
)
_ALPHA_ORDER = ("opaque", "cutout", "blend")
_DEFAULT_SKIN_SUFFIXES = ("", "1", "2", "3", "A", "B", "C", "D", "1A", "1B", "1C", "1D")
_PEASANT_VARIANT_SUFFIXES = ("A", "B", "C", "D")

_MODEL_ACCESSORY_STEMS = {
    "clansoldier": {
        "shield": ("ClanShield1", "ClanShieldGuard"),
        "sword": ("BeldSword",),
    },
    "lizardorc": {
        "cutlass": ("LizOrcCutlass",),
    },
    "krohn": {
        "shield": ("KrohnShield", "KrohnShieldSculpt"),
        "spear": ("KrohnSpear", "KrohnSpearSculpt"),
        "sword": ("KrohnSword", "KrohnSwordSculpt"),
    },
    "giant": {
        "mallet": ("GiantMallet",),
    },
    "greenman": {
        "staff": ("GreenManStaff",),
    },
    "highwayman": {
        "dagger": ("HighwaymanDagger",),
        "sword": ("HighwaymanSS",),
    },
    "imp": {
        "trident": ("ImpTrident",),
    },
    "lich": {
        "staff": ("LichStaff",),
    },
    "skeleton": {
        "dagger": ("SkeletonDagger",),
    },
    "skeletonwarrior": {
        "scimitar": ("SkeletonScimitar",),
    },
    "yanmir": {
        "staff": ("YanmirMallet",),
        "mallet": ("YanmirMallet",),
    },
}

_GENERIC_ACCESSORY_STEMS = {
    "cutlass": ("Cutlass",),
    "dagger": ("Dagger",),
    "mallet": ("Mallet",),
    "scimitar": ("Scimitar",),
    "shield": ("Shield",),
    "spear": ("Spear",),
    "staff": ("Staff",),
    "sword": ("Sword",),
    "trident": ("Trident",),
}

_CIVILIAN_CLASS_PREFIXES = (
    "Commoner", "Town", "Shopkeeper", "ShopKeeper", "Wealthy", "Poor", "Prisoner",
)

_MONSTER_DEFAULT_SKIN_STEMS = {
    # These variants share ColloidalWarrior.abc, but the actual game skin is
    # assigned by ACTOR.TXT / MONSTERS.TXT rather than by the DAT object.
    "colloidalsoldier": ("Colloidal1",),
    "colloidalwarrior": ("Colloidal2",),
    "colloidalguardian": ("Colloidal3",),
    # Beholder variants share EvileyeTerror.abc.  BOOTCAMP's
    # OrbusCACHEONLY/Oculus object has no Skin property; ACTOR/MONSTERS maps
    # the class to these Orbus skins.
    "eye": ("Orbus1",),
    "orbus": ("Orbus2",),
    "oculus": ("Orbus3",),
    # Skeleton variants also rely on ACTOR/MONSTERS for body/weapon skins.
    "skeleton": ("Skeleton2",),
    "skeletonwarrior": ("SkeletonWar2",),
    "skeletonmaster": ("SkeletonWar1",),
}


@dataclass
class ObjectModelInstance:
    """One drawable object-model instance in world space."""

    world_index: int
    mesh: GpuMesh
    mvp: np.ndarray
    skins: List[str]
    selected: bool = False


@dataclass
class ObjectModelRenderItem:
    """Cached per-object ABC render data for a loaded level."""

    world_index: int
    obj: object
    mesh: GpuMesh
    skins: List[str]
    material_ranges: List[Tuple[str, int, int, int]]
    y_override: Optional[float] = None


def _normalise_model_name(filename: str) -> str:
    norm = filename.replace("/", "\\").strip().strip('"')
    parts = [p for p in norm.split("\\") if p]
    if parts and parts[0].lower() == "models":
        parts = parts[1:]
    return "/".join(parts).upper()


def _normalise_skin_name(skin: str) -> str:
    first = (skin or "").split(";", 1)[0].strip()
    return first.replace("/", "\\").strip().strip('"')


def _split_skin_names(skin: str) -> List[str]:
    out: List[str] = []
    seen = set()
    for part in str(skin or "").split(";"):
        norm = part.replace("/", "\\").strip().strip('"')
        if not norm:
            continue
        key = norm.upper()
        if key not in seen:
            out.append(norm)
            seen.add(key)
    return out


def _skin_base_token(skin: str) -> str:
    name = _normalise_skin_name(skin).replace("\\", "/").rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _piece_token(piece_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", piece_name.lower())


def _skin_lookup_names(skin: str) -> List[str]:
    """
    Return likely cache lookup names for an object's Skin property.

    TextureCache roots point at cached SKINS/TEXTURES trees materialized from
    REZ archives. Object properties usually include a leading ``skins/``
    prefix, which should be stripped when the cache root is already SKINS.
    Basename lookup is retained as a final broad fallback.
    """
    norm = _normalise_skin_name(skin)
    if not norm:
        return []
    slash = norm.replace("\\", "/").lstrip("/")
    names = [slash]
    upper = slash.upper()
    if upper.startswith("SKINS/"):
        names.append(slash[6:])
    names.append(slash.rsplit("/", 1)[-1])

    out: List[str] = []
    seen = set()
    for name in names:
        key = name.upper()
        if name and key not in seen:
            out.append(name)
            seen.add(key)
    return out


def _append_skin_candidate(out: List[str], stem: str) -> None:
    stem = str(stem or "").replace("/", "\\").strip().strip('"')
    if not stem:
        return
    if stem.upper().endswith(".DTX"):
        path = stem
    else:
        path = stem + ".dtx"
    candidates = [path]
    if not path.upper().startswith("SKINS\\"):
        candidates.append("Skins\\" + path)
    seen = {s.upper() for s in out}
    for candidate in candidates:
        key = candidate.upper()
        if key not in seen:
            out.append(candidate)
            seen.add(key)


def _model_stem(filename_or_model: str) -> str:
    norm = str(filename_or_model or "").replace("\\", "/").strip().strip('"')
    base = norm.rsplit("/", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base


def _looks_like_civilian_class(text: str) -> bool:
    value = str(text or "")
    if not value.startswith(_CIVILIAN_CLASS_PREFIXES):
        return False
    lowered = value.lower()
    return any(word in lowered for word in (
        "human", "elf", "dwarf", "halforc", "child", "male", "female",
    ))


def _civilian_appearance_key(object_type: str = "", object_name: str = "") -> str:
    name = str(object_name or "")
    typ = str(object_type or "")
    if "child" in typ.lower() and _looks_like_civilian_class(typ):
        return f"{typ} {name}" if _looks_like_civilian_class(name) else typ
    if _looks_like_civilian_class(name):
        return name
    return typ if _looks_like_civilian_class(typ) else ""


def _civilian_variant_number(appearance_key: str) -> str:
    match = re.search(r"(\d+)$", str(appearance_key or ""))
    if not match:
        return "1"
    value = max(1, min(8, int(match.group(1))))
    return str(value)


def _civilian_preview_model(appearance_key: str) -> str:
    key = str(appearance_key or "").lower()
    if not key:
        return ""
    is_female = "female" in key or "girl" in key
    is_child = "child" in key or "boy" in key or "girl" in key
    if is_child:
        return "models\\PeasantChildGirl.abc" if is_female else "models\\PeasantChildBoy.abc"
    if "dwarf" in key:
        return "models\\PeasantFemaleDwarf.abc" if is_female else "models\\PeasantMaleDwarf.abc"
    if "halforc" in key:
        return "models\\PeasantHOFemale.abc" if is_female else "models\\PeasantHOMale.abc"
    return "models\\PeasantFemale.abc" if is_female else "models\\PeasantMale.abc"


def _is_civilian_placeholder_model(filename: str) -> bool:
    return _skin_base_token(filename) == "peasantmale"


def _object_model_filename(obj, actor_visuals: Optional[Dict[str, object]] = None) -> str:
    actor_visual = resolve_actor_visual(
        actor_visuals,
        str(getattr(obj, "type_str", "") or ""),
        str(obj.get("Name") or ""),
    )
    if actor_visual and actor_visual.model:
        return actor_visual.model

    filename = str(obj.get("Filename") or "")
    if not filename:
        return ""
    appearance_key = _civilian_appearance_key(
        str(getattr(obj, "type_str", "") or ""),
        str(obj.get("Name") or ""),
    )
    filename_key = _skin_base_token(filename)
    if (
        appearance_key
        and (
            filename_key.startswith("commoner")
            or filename_key.startswith("town")
            or filename_key.startswith("shopkeeper")
            or filename_key.startswith("wealthy")
            or filename_key.startswith("poor")
            or filename_key.startswith("prisoner")
        )
    ):
        return _civilian_preview_model(appearance_key) or filename
    if not _is_civilian_placeholder_model(filename):
        return filename
    return _civilian_preview_model(appearance_key) or filename


def _object_skin_names(obj, actor_visuals: Optional[Dict[str, object]] = None) -> List[str]:
    actor_visual = resolve_actor_visual(
        actor_visuals,
        str(getattr(obj, "type_str", "") or ""),
        str(obj.get("Name") or ""),
    )
    if actor_visual and actor_visual.skins:
        return list(actor_visual.skins)

    return _split_skin_names(str(obj.get("Skin") or ""))


def _object_is_visible(obj) -> bool:
    visible = obj.get("Visible")
    if visible is None:
        return True
    try:
        return bool(int(visible))
    except Exception:
        return bool(visible)


def _truthy_object_flag(obj, name: str) -> bool:
    value = obj.get(name)
    if value is None:
        return False
    try:
        return bool(int(value))
    except Exception:
        return bool(value)


def _skin_candidate_exists(skin: str, skin_cache=None, tex_cache=None) -> bool:
    for name in _skin_lookup_names(skin):
        if skin_cache is not None and hasattr(skin_cache, "has") and skin_cache.has(name):
            return True
        if tex_cache is not None and hasattr(tex_cache, "has") and tex_cache.has(name):
            return True
    return False


def _first_existing_skin(candidates: Iterable[str], skin_cache=None, tex_cache=None) -> str:
    first = ""
    seen = set()
    for skin in candidates:
        key = str(skin or "").upper()
        if not key or key in seen:
            continue
        seen.add(key)
        if not first:
            first = str(skin)
        if _skin_candidate_exists(str(skin), skin_cache=skin_cache, tex_cache=tex_cache):
            return str(skin)
    return first


def _accessory_skin_candidates(model_stem: str, piece_name: str) -> List[str]:
    out: List[str] = []
    model_key = _skin_base_token(model_stem)
    piece_key = _piece_token(piece_name)
    for accessory_word, generic_stems in _GENERIC_ACCESSORY_STEMS.items():
        if accessory_word not in piece_key:
            continue
        for source_key in (model_key,):
            for stem in _MODEL_ACCESSORY_STEMS.get(source_key, {}).get(accessory_word, ()):
                _append_skin_candidate(out, stem)
        for stem in (
            model_stem + accessory_word,
            model_key + accessory_word,
            *generic_stems,
        ):
            _append_skin_candidate(out, stem)
    return out


def _peasant_skin_candidates(model_stem: str, piece_name: str) -> List[str]:
    out: List[str] = []
    model_key = _skin_base_token(model_stem)
    piece_key = _piece_token(piece_name)
    if "peasant" not in model_key and not piece_key.startswith("mp"):
        return out

    match = re.search(r"mp(\d+)", piece_key)
    if match:
        for suffix in _PEASANT_VARIANT_SUFFIXES:
            _append_skin_candidate(out, f"PeasantM{match.group(1)}{suffix}")

    match = re.search(r"peasantm(\d+)", model_key)
    if match:
        for suffix in _PEASANT_VARIANT_SUFFIXES:
            _append_skin_candidate(out, f"PeasantM{match.group(1)}{suffix}")

    match = re.search(r"peasantms(\d+)", model_key)
    if match:
        for suffix in _PEASANT_VARIANT_SUFFIXES:
            _append_skin_candidate(out, f"PeasantM{match.group(1)}{suffix}")

    if model_key == "peasantmale":
        for suffix in _PEASANT_VARIANT_SUFFIXES:
            _append_skin_candidate(out, f"PeasantM7{suffix}")

    if model_key in ("peasantmale", "peasantms"):
        for number in ("1", "2", "3", "4", "5", "6", "7", "8"):
            for suffix in ("A", "B"):
                _append_skin_candidate(out, f"PeasantM{number}{suffix}")
    return out


def _civilian_skin_candidates(appearance_key: str) -> List[str]:
    out: List[str] = []
    if not _looks_like_civilian_class(appearance_key):
        return out
    key = str(appearance_key or "").lower()

    number = _civilian_variant_number(appearance_key)
    is_female = "female" in key or "girl" in key
    is_child = "child" in key or "boy" in key or "girl" in key

    if is_child:
        prefix = "PeasantGirl" if is_female else "PeasantBoy"
        for stem in (prefix + number, prefix + "1", prefix + "2", prefix + "3"):
            _append_skin_candidate(out, stem)
        return out

    if "dwarf" in key:
        prefix = "PeasantDF" if is_female else "PeasantDM"
        for suffix in ("A", "B", "C", "D", ""):
            _append_skin_candidate(out, prefix + number + suffix)
        for fallback in ("1", "2", "3", "4"):
            for suffix in ("A", "B", ""):
                _append_skin_candidate(out, prefix + fallback + suffix)
        return out

    if "halforc" in key:
        prefix = "PeasantHOF" if is_female else "PeasantHOM"
        for fallback in (number, "1", "2"):
            for suffix in ("A", "B", "C", ""):
                _append_skin_candidate(out, prefix + fallback + suffix)
        return out

    prefix = "PeasantF" if is_female else "PeasantM"
    for suffix in ("A", "B", "C", "D", ""):
        _append_skin_candidate(out, prefix + number + suffix)
    for fallback in ("1", "2", "3", "4", "5", "6", "7", "8"):
        for suffix in ("A", "B", "C", "D", ""):
            _append_skin_candidate(out, prefix + fallback + suffix)
    return out


def _monster_default_skin_candidates(model_stem: str, object_type: str) -> List[str]:
    out: List[str] = []
    seen = set()
    for key_source in (object_type, model_stem):
        key = _skin_base_token(key_source)
        if not key or key in seen:
            continue
        seen.add(key)
        for stem in _MONSTER_DEFAULT_SKIN_STEMS.get(key, ()):
            _append_skin_candidate(out, stem)
    return out


def _default_skin_candidates(
    model_name: str,
    piece_name: str,
    object_type: str = "",
    appearance_key: str = "",
) -> List[str]:
    """
    Infer plausible NPC/creature skins when a WorldObject omits Skin.

    Several shipped NPC and creature placements rely on engine defaults rather
    than an explicit object Skin property.  The viewer does not have that model
    database, so it tries conservative filename, class-name, piece-name, and
    known accessory conventions against the extracted SKINS/TEXTURES caches.
    """
    out: List[str] = []
    stem = _model_stem(model_name)
    object_stem = _model_stem(object_type)
    piece_stem = piece_name.strip()
    civilian_key = appearance_key or object_type

    for candidate in _accessory_skin_candidates(stem, piece_name):
        _append_skin_candidate(out, candidate)

    for candidate in _monster_default_skin_candidates(stem, object_type):
        _append_skin_candidate(out, candidate)

    if not (_skin_base_token(stem) == "peasantmale" and _looks_like_civilian_class(civilian_key)):
        for candidate in _peasant_skin_candidates(stem, piece_name):
            _append_skin_candidate(out, candidate)

    for candidate in _civilian_skin_candidates(civilian_key):
        _append_skin_candidate(out, candidate)

    for base in (stem, object_stem, piece_stem):
        if not base:
            continue
        for suffix in _DEFAULT_SKIN_SUFFIXES:
            _append_skin_candidate(out, base + suffix)

    return out


def _texture_from_caches(skin: str, skin_cache=None, tex_cache=None) -> int:
    for name in _skin_lookup_names(skin):
        if skin_cache is not None:
            tex_id = skin_cache.get(name)
            if tex_id:
                return tex_id
        if tex_cache is not None:
            tex_id = tex_cache.get(name)
            if tex_id:
                return tex_id

    norm = _normalise_skin_name(skin)
    if norm and norm.upper() not in _logged_skin_misses and len(_logged_skin_misses) < 20:
        _logged_skin_misses.add(norm.upper())
        print(f"[view3d] object skin not found: {norm}", file=sys.stderr)
    return 0


def _alpha_info_from_caches(skin: str, skin_cache=None, tex_cache=None):
    """Resolve DTX alpha metadata through the same names used for rendering."""
    for name in _skin_lookup_names(skin):
        if skin_cache is not None and hasattr(skin_cache, "alpha_info"):
            info = skin_cache.alpha_info(name)
            if info is not None:
                return info
        if tex_cache is not None and hasattr(tex_cache, "alpha_info"):
            info = tex_cache.alpha_info(name)
            if info is not None:
                return info
    return None


def _alpha_mode_for_piece(
    piece_name: str,
    skin: str,
    skin_cache=None,
    tex_cache=None,
) -> str:
    """
    Pick an alpha mode for one ABC sub-mesh.

    MM9's prop skins often contain undefined alpha channels, including several
    all-zero files.  Useful texture alpha is always required before enabling
    alpha in the shader; names decide blend-vs-cutout when they are known.
    """
    info = _alpha_info_from_caches(skin, skin_cache, tex_cache)
    if info is None or not getattr(info, "has_useful_alpha", False):
        return "opaque"

    piece_key = _piece_token(piece_name)
    skin_key = _skin_base_token(skin)
    skin_path_key = re.sub(r"[^a-z0-9]+", "", _normalise_skin_name(skin).lower())
    material_key = piece_key + " " + skin_key + " " + skin_path_key

    if any(word in material_key for word in _ALPHA_BLEND_TOKENS):
        return "blend"
    if any(word in material_key for word in _ALPHA_CUTOUT_TOKENS):
        return "cutout"

    # Last-resort classification for alpha textures without obvious names.
    # Binary-looking alpha is usually cutout; broad mid-alpha coverage is more
    # likely glass/glow-style blending.
    mid = float(getattr(info, "mid_fraction", 0.0))
    transparent = float(getattr(info, "transparent_fraction", 0.0))
    if mid > 0.05 and mid >= transparent:
        return "blend"
    if transparent > 0.02:
        return "cutout"
    return "opaque"


def _skin_for_piece(piece_name: str, piece_index: int, piece_count: int, skins: List[str]) -> str:
    if not skins:
        return ""
    if len(skins) == 1:
        return skins[0]

    piece_key = _piece_token(piece_name)
    skin_keys = [_skin_base_token(s) for s in skins]

    # Common MM9 prop conventions: large tree meshes use bark for trunks and
    # branch/leaf textures for every limb/top/twig piece.
    if any(word in piece_key for word in ("trunk", "bark", "cylinder", "treemesh")):
        for skin, skin_key in zip(skins, skin_keys):
            if "bark" in skin_key or "trunk" in skin_key:
                return skin
    if any(word in piece_key for word in ("branch", "top", "twig", "leaf", "leaves", "box")):
        for skin, skin_key in zip(skins, skin_keys):
            if any(word in skin_key for word in ("branch", "leaf", "leaves", "tree")) and "bark" not in skin_key:
                return skin

    # Weapons and held props often have fewer skins than pieces, with the
    # accessory skin named directly after the prop piece.
    accessory_words = (
        "base", "sword", "shield", "spear", "halberd", "cutlass", "thjorad",
        "herb", "crown", "statue", "bag", "suit", "armor", "armored",
        "amored", "scimitar",
    )
    for word in accessory_words:
        if word in piece_key:
            for skin, skin_key in zip(skins, skin_keys):
                if word in skin_key:
                    return skin

    for skin, skin_key in zip(skins, skin_keys):
        if not skin_key or not piece_key:
            continue
        if skin_key in piece_key:
            return skin
        if piece_key in skin_key and ("base" in piece_key or "base" not in skin_key):
            return skin

    if len(skins) == piece_count and piece_index < len(skins):
        return skins[piece_index]
    return skins[0]


def _resolve_skin_for_piece(
    piece_name: str,
    piece_index: int,
    piece_count: int,
    skins: List[str],
    model_name: str,
    object_type: str = "",
    appearance_key: str = "",
    skin_cache=None,
    tex_cache=None,
) -> str:
    if skins:
        chosen = _skin_for_piece(piece_name, piece_index, piece_count, skins)
        if _skin_candidate_exists(chosen, skin_cache=skin_cache, tex_cache=tex_cache):
            return chosen
        fallback = _first_existing_skin(skins, skin_cache=skin_cache, tex_cache=tex_cache)
        return fallback or chosen

    candidates = _default_skin_candidates(
        model_name=model_name,
        piece_name=piece_name,
        object_type=object_type,
        appearance_key=appearance_key,
    )
    inferred = _first_existing_skin(candidates, skin_cache=skin_cache, tex_cache=tex_cache)
    if inferred and _skin_candidate_exists(inferred, skin_cache=skin_cache, tex_cache=tex_cache):
        return inferred
    return ""


def _draw_mesh_by_piece(
    gpu_mesh: GpuMesh,
    prog,
    skins: List[str],
    skin_cache=None,
    tex_cache=None,
) -> None:
    import ctypes
    from OpenGL import GL  # type: ignore

    if not gpu_mesh.tex_ranges:
        skin = skins[0] if skins else ""
        tex_id = _texture_from_caches(skin, skin_cache, tex_cache)
        alpha_mode = _alpha_mode_for_piece(gpu_mesh.model_name, skin, skin_cache, tex_cache)
        prog.set_int("uUseTexAlpha", 1 if alpha_mode != "opaque" else 0)
        if tex_id:
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
            prog.set_int("uHasTex", 1)
        else:
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            prog.set_int("uHasTex", 0)
        draw_mesh(gpu_mesh)
        prog.set_int("uUseTexAlpha", 0)
        return

    piece_count = len(gpu_mesh.tex_ranges)
    draw_items = []
    for piece_index, (piece_name, byte_off, count) in enumerate(gpu_mesh.tex_ranges):
        skin = _skin_for_piece(piece_name, piece_index, piece_count, skins)
        tex_id = _texture_from_caches(skin, skin_cache, tex_cache)
        alpha_mode = _alpha_mode_for_piece(piece_name, skin, skin_cache, tex_cache)
        draw_items.append((alpha_mode, piece_name, byte_off, count, tex_id))

    GL.glBindVertexArray(gpu_mesh.vao)
    GL.glActiveTexture(GL.GL_TEXTURE0)

    for alpha_mode in _ALPHA_ORDER:
        pass_items = [item for item in draw_items if item[0] == alpha_mode]
        if not pass_items:
            continue

        prog.set_int("uUseTexAlpha", 1 if alpha_mode != "opaque" else 0)
        if alpha_mode == "blend":
            GL.glDepthMask(GL.GL_FALSE)
        try:
            for _mode, _piece_name, byte_off, count, tex_id in pass_items:
                if tex_id:
                    GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
                    prog.set_int("uHasTex", 1)
                else:
                    GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
                    prog.set_int("uHasTex", 0)
                GL.glDrawElements(
                    GL.GL_TRIANGLES, count,
                    GL.GL_UNSIGNED_INT, ctypes.c_void_p(byte_off),
                )
        finally:
            if alpha_mode == "blend":
                GL.glDepthMask(GL.GL_TRUE)

    prog.set_int("uUseTexAlpha", 0)
    GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
    GL.glBindVertexArray(0)


def _resolve_material_ranges(
    gpu_mesh: GpuMesh,
    skins: List[str],
    model_name: str = "",
    object_type: str = "",
    appearance_key: str = "",
    skin_cache=None,
    tex_cache=None,
) -> List[Tuple[str, int, int, int]]:
    """Resolve per-piece alpha mode and texture id once for an object."""
    if not gpu_mesh.tex_ranges:
        if skins:
            skin = skins[0]
        else:
            skin = _resolve_skin_for_piece(
                gpu_mesh.model_name,
                0,
                1,
                skins,
                model_name or gpu_mesh.model_name,
                object_type=object_type,
                appearance_key=appearance_key,
                skin_cache=skin_cache,
                tex_cache=tex_cache,
            )
        return [(
            _alpha_mode_for_piece(gpu_mesh.model_name, skin, skin_cache, tex_cache),
            -1,
            int(gpu_mesh.index_count),
            int(_texture_from_caches(skin, skin_cache, tex_cache) or 0),
        )]

    piece_count = len(gpu_mesh.tex_ranges)
    out: List[Tuple[str, int, int, int]] = []
    for piece_index, (piece_name, byte_off, count) in enumerate(gpu_mesh.tex_ranges):
        skin = _resolve_skin_for_piece(
            piece_name,
            piece_index,
            piece_count,
            skins,
            model_name or gpu_mesh.model_name,
            object_type=object_type,
            appearance_key=appearance_key,
            skin_cache=skin_cache,
            tex_cache=tex_cache,
        )
        out.append((
            _alpha_mode_for_piece(piece_name, skin, skin_cache, tex_cache),
            int(byte_off),
            int(count),
            int(_texture_from_caches(skin, skin_cache, tex_cache) or 0),
        ))
    order = {name: i for i, name in enumerate(_ALPHA_ORDER)}
    out.sort(key=lambda item: order.get(item[0], 0))
    return out


def _draw_mesh_material_ranges(
    gpu_mesh: GpuMesh,
    material_ranges: List[Tuple[str, int, int, int]],
    prog,
) -> None:
    """Draw an ABC mesh using pre-resolved material ranges."""
    import ctypes
    from OpenGL import GL  # type: ignore

    GL.glBindVertexArray(gpu_mesh.vao)
    GL.glActiveTexture(GL.GL_TEXTURE0)

    active_mode = None
    depth_mask_disabled = False
    try:
        for alpha_mode, byte_off, count, tex_id in material_ranges:
            if alpha_mode != active_mode:
                if depth_mask_disabled:
                    GL.glDepthMask(GL.GL_TRUE)
                    depth_mask_disabled = False
                prog.set_int("uUseTexAlpha", 1 if alpha_mode != "opaque" else 0)
                if alpha_mode == "blend":
                    GL.glDepthMask(GL.GL_FALSE)
                    depth_mask_disabled = True
                active_mode = alpha_mode

            if tex_id:
                GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
                prog.set_int("uHasTex", 1)
            else:
                GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
                prog.set_int("uHasTex", 0)

            if byte_off < 0:
                GL.glDrawElements(
                    GL.GL_TRIANGLES,
                    gpu_mesh.index_count,
                    GL.GL_UNSIGNED_INT,
                    None,
                )
            else:
                GL.glDrawElements(
                    GL.GL_TRIANGLES,
                    count,
                    GL.GL_UNSIGNED_INT,
                    ctypes.c_void_p(byte_off),
                )
    finally:
        if depth_mask_disabled:
            GL.glDepthMask(GL.GL_TRUE)

    prog.set_int("uUseTexAlpha", 0)
    GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
    GL.glBindVertexArray(0)


def _rotation_y(rot) -> float:
    try:
        # MM9 object rotations encountered so far are stored as
        # (pitch, yaw, roll, extra); placed props use index 1 for yaw.
        return float(rot[1])
    except Exception:
        return 0.0


def _model_yaw_offset(filename: str) -> float:
    """
    Return a preview-only yaw offset for model families whose authored ABC
    basis differs from the game object's yaw convention.

    Most props, including BOOTCAMP treasure chests, match the DAT yaw directly.
    A small set of furniture models observed in STURMFORDCITY are authored
    ninety degrees off; keep that correction local to those families instead
    of changing every object mesh.
    """
    stem = _skin_base_token(_model_stem(filename))
    if stem.startswith("cabinet") or stem.startswith("pew"):
        return -(math.pi * 0.5)
    return 0.0


def _object_yaw(obj) -> float:
    return _rotation_y(obj.get("Rotation") or (0.0, 0.0, 0.0, 0.0)) + _model_yaw_offset(
        str(obj.get("Filename") or "")
    )


def _scale_value(scale) -> float:
    try:
        return float(scale)
    except Exception:
        return 1.0


def _mesh_min_y(mesh: GpuMesh) -> Optional[float]:
    try:
        tri_positions = getattr(mesh, "tri_positions", None)
        if tri_positions is None or tri_positions.size == 0:
            return None
        return float(np.min(tri_positions[:, :, 1]))
    except Exception:
        return None


def _floor_y_override(obj, mesh: GpuMesh, bsp_world=None) -> Optional[float]:
    if bsp_world is None or not _truthy_object_flag(obj, "MoveToFloor"):
        return None
    pos = obj.get("Pos")
    if pos is None:
        return None
    try:
        x, y, z = float(pos[0]), float(pos[1]), float(pos[2])
    except Exception:
        return None
    local_min_y = _mesh_min_y(mesh)
    if local_min_y is None:
        return None
    try:
        from core import bsp as bsp_mod  # type: ignore
        floor_y = bsp_mod.raycast_floor_y(
            bsp_world,
            x,
            z,
            y_hint_min=y - 512.0,
            y_hint_max=y + 16.0,
            y_above=y + 16.0,
        )
    except Exception:
        return None
    if floor_y is None:
        return None
    return float(floor_y) - local_min_y * _scale_value(obj.get("Scale") or 1.0)


def _object_matrix(obj, y_override: Optional[float] = None) -> Optional[np.ndarray]:
    pos = obj.get("Pos")
    if pos is None:
        return None
    try:
        x, y, z = float(pos[0]), float(pos[1]), float(pos[2])
    except Exception:
        return None

    yaw = _object_yaw(obj)
    s = _scale_value(obj.get("Scale") or 1.0)
    c = math.cos(yaw)
    sn = math.sin(yaw)

    scale = np.eye(4, dtype=np.float32)
    scale[0, 0] = s
    scale[1, 1] = s
    scale[2, 2] = s

    rot = np.eye(4, dtype=np.float32)
    rot[0, 0] = c
    rot[0, 2] = sn
    rot[2, 0] = -sn
    rot[2, 2] = c

    trans = np.eye(4, dtype=np.float32)
    trans[0, 3] = x
    trans[1, 3] = float(y_override) if y_override is not None else y
    trans[2, 3] = z

    return (trans @ rot @ scale).astype(np.float32)


class ObjectModelCache:
    """
    Resolve, load, and upload ABC meshes for WorldObjects.

    Cache entries are keyed by normalised model filename. A value of None means
    the file is missing or unsupported, so failed models are not retried every
    frame.
    """

    def __init__(self, models_root: Optional[str]) -> None:
        self._root = models_root
        self._index: Dict[str, str] = {}
        self._cache: Dict[str, Optional[GpuMesh]] = {}
        self._build_index()

    def _build_index(self) -> None:
        if not self._root or not os.path.isdir(self._root):
            return
        for dirpath, _dirs, files in os.walk(self._root):
            for name in files:
                if not name.upper().endswith(".ABC"):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, self._root)
                key = rel.replace(os.sep, "/").upper()
                self._index[key] = full

    @property
    def index_size(self) -> int:
        return len(self._index)

    def invalidate(self) -> None:
        for mesh in self._cache.values():
            if mesh is not None:
                try:
                    delete_mesh(mesh)
                except Exception:
                    pass
        self._cache.clear()

    def _path_for(self, filename: str) -> Optional[str]:
        key = _normalise_model_name(filename)
        if key in self._index:
            return self._index[key]
        if not self._root:
            return None
        direct = os.path.join(self._root, *key.split("/"))
        return direct if os.path.isfile(direct) else None

    def get_or_upload(self, filename: str) -> Optional[GpuMesh]:
        key = _normalise_model_name(filename)
        if not key:
            return None
        if key in self._cache:
            return self._cache[key]

        path = self._path_for(filename)
        if path is None:
            self._cache[key] = None
            return None

        abc = load_abc(path, bake_static_bind_pose=True)
        if abc is None or abc.is_empty():
            self._cache[key] = None
            return None

        mesh = upload_abc_model(abc, category="object")
        self._cache[key] = mesh
        return mesh


def build_instances(
    objects,
    cache: ObjectModelCache,
    view_proj: np.ndarray,
    selected_index: int = -1,
    actor_visuals: Optional[Dict[str, object]] = None,
) -> List[ObjectModelInstance]:
    """Build drawable mesh instances for all currently supported objects."""
    instances: List[ObjectModelInstance] = []
    for world_index, obj in enumerate(objects):
        if not _object_is_visible(obj):
            continue
        filename = obj.get("Filename")
        has_actor_visual = resolve_actor_visual(
            actor_visuals,
            str(getattr(obj, "type_str", "") or ""),
            str(obj.get("Name") or ""),
        ) is not None
        if not filename and not has_actor_visual:
            continue
        filename_str = _object_model_filename(obj, actor_visuals=actor_visuals)
        mesh = cache.get_or_upload(filename_str)
        if mesh is None or mesh.is_empty():
            continue
        model = _object_matrix(obj)
        if model is None:
            continue
        skins = _object_skin_names(obj, actor_visuals=actor_visuals)
        instances.append(ObjectModelInstance(
            world_index=world_index,
            mesh=mesh,
            mvp=(view_proj @ model).astype(np.float32),
            skins=skins,
            selected=(world_index == selected_index),
        ))
    return instances


def build_render_items(
    objects,
    cache: ObjectModelCache,
    skin_cache=None,
    tex_cache=None,
    bsp_world=None,
    actor_visuals: Optional[Dict[str, object]] = None,
) -> List[ObjectModelRenderItem]:
    """
    Build the stable object-to-ABC-mesh mapping for a materialized level.

    Object transforms can still change between frames; the item keeps the
    object reference so drawing recomputes only the lightweight model matrix.
    """
    items: List[ObjectModelRenderItem] = []
    for world_index, obj in enumerate(objects):
        if not _object_is_visible(obj):
            continue
        filename = obj.get("Filename")
        has_actor_visual = resolve_actor_visual(
            actor_visuals,
            str(getattr(obj, "type_str", "") or ""),
            str(obj.get("Name") or ""),
        ) is not None
        if not filename and not has_actor_visual:
            continue
        filename_str = _object_model_filename(obj, actor_visuals=actor_visuals)
        mesh = cache.get_or_upload(filename_str)
        if mesh is None or mesh.is_empty():
            continue
        skins = _object_skin_names(obj, actor_visuals=actor_visuals)
        object_type = str(getattr(obj, "type_str", "") or "")
        appearance_key = _civilian_appearance_key(object_type, str(obj.get("Name") or ""))
        items.append(ObjectModelRenderItem(
            world_index=world_index,
            obj=obj,
            mesh=mesh,
            skins=skins,
            material_ranges=_resolve_material_ranges(
                mesh,
                skins,
                model_name=filename_str,
                object_type=object_type,
                appearance_key=appearance_key,
                skin_cache=skin_cache,
                tex_cache=tex_cache,
            ),
            y_override=_floor_y_override(obj, mesh, bsp_world=bsp_world),
        ))
    return items


def draw_object_model_items(
    items: List[ObjectModelRenderItem],
    solid_prog,
    view_proj: np.ndarray,
    light_dir: Tuple[float, float, float],
    selected_index: int = -1,
    tex_cache=None,
    skin_cache=None,
    fog_enabled: bool = False,
    fog_near: float = 500.0,
    fog_far: float = 3000.0,
    fog_color: Tuple[float, float, float] = (0.055, 0.063, 0.086),
    only_world_index: Optional[int] = None,
) -> Tuple[int, int, set]:
    """Draw a cached object-model render item list."""
    from OpenGL import GL  # type: ignore

    if not items:
        return 0, 0, set()

    drawn = 0
    tris = 0
    world_indices = set()

    with solid_prog as prog:
        prog.set_vec3("uLightDir", light_dir)
        prog.set_int("uFogEnabled", 1 if fog_enabled else 0)
        prog.set_float("uFogNear", fog_near)
        prog.set_float("uFogFar", fog_far)
        prog.set_vec3("uFogColor", fog_color)
        prog.set_int("uTex", 0)
        prog.set_int("uUseTexAlpha", 0)

        for item in items:
            if only_world_index is not None and item.world_index != only_world_index:
                continue
            model = _object_matrix(item.obj, y_override=item.y_override)
            if model is None:
                continue
            prog.set_mat4("uMVP", (view_proj @ model).astype(np.float32))
            selected = item.world_index == selected_index
            prog.set_vec3("uColor", (0.95, 0.95, 0.95) if selected else _DEFAULT_COLOR)
            prog.set_float("uAlpha", 1.0)

            _draw_mesh_material_ranges(item.mesh, item.material_ranges, prog)
            drawn += 1
            tris += item.mesh.triangle_count
            world_indices.add(item.world_index)

        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

    return drawn, tris, world_indices


def draw_object_models(
    objects,
    cache: ObjectModelCache,
    solid_prog,
    view_proj: np.ndarray,
    light_dir: Tuple[float, float, float],
    selected_index: int = -1,
    tex_cache=None,
    skin_cache=None,
    fog_enabled: bool = False,
    fog_near: float = 500.0,
    fog_far: float = 3000.0,
    fog_color: Tuple[float, float, float] = (0.055, 0.063, 0.086),
    actor_visuals: Optional[Dict[str, object]] = None,
) -> Tuple[int, int, set]:
    """
    Draw supported ABC models.

    Returns
    -------
    (instances_drawn, triangles_drawn, world_indices_drawn)
        The index set lets the caller suppress visible billboards for objects
        that already have a real mesh, while keeping billboard picking intact.
    """
    items = build_render_items(
        objects,
        cache,
        skin_cache=skin_cache,
        tex_cache=tex_cache,
        actor_visuals=actor_visuals,
    )
    return draw_object_model_items(
        items,
        solid_prog,
        view_proj,
        light_dir,
        selected_index=selected_index,
        tex_cache=tex_cache,
        skin_cache=skin_cache,
        fog_enabled=fog_enabled,
        fog_near=fog_near,
        fog_far=fog_far,
        fog_color=fog_color,
    )
