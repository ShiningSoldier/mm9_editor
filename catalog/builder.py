"""
catalog.py
==========

Build (and read) catalog.json -- an index of every WorldObject class and every
.abc model filename present in MM9's shipped levels.  The editor uses this to
populate the Add Object dialog and to provide canonical templates for new
placements.

Normal use
----------
The editor builds catalog.json automatically on first launch by scanning the
game's ``WORLDS.REZ`` archive.  You should never need to run this script
manually unless you want to force a rebuild.

    # Rebuild directly from an archive:
    python catalog.py build-from-rez path/to/WORLDS.REZ
    python catalog.py build-from-rez path/to/WORLDS.REZ --data-rez path/to/DATA.REZ

    # Inspect an existing catalog:
    python catalog.py info catalog/data/catalog.json

catalog.json shape
------------------
    {
      "classes": {
          "<ClassName>": {
              "instance_count": 87,
              "levels":         ["BOOTCAMP.DAT", "BATHHOUSE.DAT", ...],
              "category":       "npc_civilian",   # see CATEGORY_RULES below
              "property_names": ["Name","Pos",...],
              "filenames":      ["models\\\\peasantmale.abc", ...],
              "skins":          ["skins\\\\peasantm2a.dtx", ...],
              "template": {
                  "source_level":    "BOOTCAMP.DAT",
                  "source_instance": "CommonerHuman2MaleA0",
                  "default_pos":     [10144.0, 552.0, -2752.0]
              }
          },
          ...
      },
      "filenames": {
          "<lowercased path>": {
              "uses":    121,
              "classes": ["Prop","WorldObject","Barrel"],
              "levels":  ["ANSKRAMKEEP.DAT", ...]
          },
          ...
      },
      "summary": {
          "total_levels":           45,
          "total_classes":          282,
          "max_npc_nbr":            436,
          "free_npc_nbrs_above_max": [437, 438, ..., 456]
      }
    }
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import struct
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import _path_setup  # noqa: F401  (adds mm9_patcher/ to sys.path)
from .actor_visuals import (
    ActorVisual,
    load_actor_visuals_from_data_dir,
    load_actor_visuals_from_data_rez,
    resolve_actor_visual,
)
import mm9_patch as patcher


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CATALOG_PATH = os.path.join(HERE, "data", "catalog.json")
OBJECT_LTO_DUMP_SCHEMA = "mm9_editor.object_lto_dump.v1"
DEFAULT_OBJECT_LTO_DUMP_HELPER = os.path.join(
    os.path.dirname(HERE),
    "tools",
    "object_lto_dump",
    "bin",
    "object_lto_dump.exe",
)


class ObjectLtoDumpError(RuntimeError):
    """Raised when an object.lto dump cannot be loaded or generated."""


def _normalize_object_lto_dump(dump: Dict[str, Any],
                               source_path: Optional[str] = None) -> Dict[str, Any]:
    """Return the catalog-facing object.lto layer for a helper JSON dump."""
    if dump.get("schema") != OBJECT_LTO_DUMP_SCHEMA:
        raise ObjectLtoDumpError(
            f"unsupported object.lto dump schema: {dump.get('schema')!r}")

    classes: Dict[str, Any] = {}
    for item in dump.get("classes", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name:
            classes[name] = item

    return {
        "available": True,
        "schema": dump.get("schema"),
        "source_dump": os.path.abspath(source_path) if source_path else None,
        "object_lto_path": dump.get("object_lto_path"),
        "server_object_version": dump.get("server_object_version"),
        "class_count": int(dump.get("class_count") or len(classes)),
        "classes": classes,
    }


def load_object_lto_dump(path: str) -> Dict[str, Any]:
    """Load a JSON dump produced by ``tools/object_lto_dump``."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            dump = json.load(f)
    except Exception as exc:
        raise ObjectLtoDumpError(
            f"could not read object.lto dump {path!r}: {exc}") from exc
    return _normalize_object_lto_dump(dump, source_path=path)


def generate_object_lto_dump(
    object_lto_path: str,
    helper_path: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Run the native helper and return a normalized object.lto dump layer."""
    helper = helper_path or DEFAULT_OBJECT_LTO_DUMP_HELPER
    if not os.path.isfile(helper):
        raise ObjectLtoDumpError(f"object.lto dump helper not found: {helper}")
    if not os.path.isfile(object_lto_path):
        raise ObjectLtoDumpError(f"object.lto not found: {object_lto_path}")

    try:
        proc = subprocess.run(
            [helper, object_lto_path],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        raise ObjectLtoDumpError(
            f"failed to run object.lto dump helper {helper!r}: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if detail:
            raise ObjectLtoDumpError(
                f"object.lto dump helper failed with exit code "
                f"{proc.returncode}: {detail}")
        raise ObjectLtoDumpError(
            f"object.lto dump helper failed with exit code {proc.returncode}")

    try:
        dump = json.loads(proc.stdout)
    except Exception as exc:
        raise ObjectLtoDumpError(
            f"object.lto dump helper did not emit valid JSON: {exc}") from exc
    return _normalize_object_lto_dump(dump)


def resolve_object_lto_dump(
    object_lto_dump_path: Optional[str] = None,
    object_lto_path: Optional[str] = None,
    helper_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Load an existing dump if present, otherwise try to generate one.

    The caller may catch :class:`ObjectLtoDumpError` to fall back to the current
    DAT-only catalog build.
    """
    dump_error: Optional[ObjectLtoDumpError] = None
    if object_lto_dump_path:
        if os.path.isfile(object_lto_dump_path):
            try:
                return load_object_lto_dump(object_lto_dump_path)
            except ObjectLtoDumpError as exc:
                dump_error = exc
        else:
            dump_error = ObjectLtoDumpError(
                f"object.lto dump file not found: {object_lto_dump_path}")

    if object_lto_path:
        try:
            return generate_object_lto_dump(
                object_lto_path,
                helper_path=helper_path,
            )
        except ObjectLtoDumpError as exc:
            if dump_error is not None:
                raise ObjectLtoDumpError(
                    f"{dump_error}; also failed to generate a fresh dump: {exc}"
                ) from exc
            raise

    if dump_error is not None:
        raise dump_error

    return None


def _object_lto_default_value(prop: Dict[str, Any]) -> Any:
    """Return a DAT-serializer-friendly value for a dumped PropDef."""
    type_id = prop.get("type_id")
    value = prop.get("default_value")

    if type_id == 0:
        return "" if value is None else str(value)
    if type_id in (1, 2):
        parts = value if isinstance(value, (list, tuple)) else []
        return [float(parts[i]) if i < len(parts) else 0.0 for i in range(3)]
    if type_id == 3:
        return float(value or 0.0)
    if type_id == 5:
        return bool(value)
    if type_id in (4, 6):
        return int(value or 0)
    if type_id == 7:
        parts = value if isinstance(value, (list, tuple)) else []
        return [float(parts[i]) if i < len(parts) else 0.0 for i in range(3)] + [0.0]
    return value


def _object_lto_template_properties(class_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    props: List[Dict[str, Any]] = []
    for prop in class_info.get("properties", []) or []:
        if not isinstance(prop, dict):
            continue
        name = prop.get("name")
        type_id = prop.get("type_id")
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(type_id, int) or not (0 <= type_id <= 7):
            continue
        props.append({
            "name": name,
            "code": type_id,
            "flags": int(prop.get("flags") or 0),
            "value": _object_lto_default_value(prop),
        })
    return props


def _normalise_resource_path(value: object, root: str, extensions: Sequence[str]) -> str:
    text = str(value or "").strip().strip('"').replace("/", "\\").lstrip("\\")
    if not text or not text.lower().endswith(tuple(ext.lower() for ext in extensions)):
        return ""
    if not text.lower().startswith(root.lower() + "\\"):
        text = root + "\\" + text
    return text.lower()


def _split_skin_values(value: object) -> List[str]:
    return [
        path
        for piece in str(value or "").split(";")
        if (path := _normalise_resource_path(piece, "skins", (".dtx",)))
    ]


def _object_lto_resources(
    template_props: Iterable[Dict[str, Any]],
) -> Tuple[Set[str], Set[str], Set[str]]:
    filenames: Set[str] = set()
    skins: Set[str] = set()
    accessory_skins: Set[str] = set()
    for prop in template_props:
        name = str(prop.get("name") or "").lower()
        value = prop.get("value")
        if name == "filename":
            path = _normalise_resource_path(value, "models", (".abc", ".lta", ".ltb"))
            if path:
                filenames.add(path)
        elif name in {"skin", "skinname", "skin2", "skin3"}:
            paths = _split_skin_values(value)
            skins.update(paths)
            if name in {"skin2", "skin3"}:
                accessory_skins.update(paths)
    return filenames, skins, accessory_skins


def _object_lto_variant_skins(template_props: Iterable[Dict[str, Any]]) -> List[str]:
    by_name: Dict[str, List[str]] = {}
    for prop in template_props:
        name = str(prop.get("name") or "").lower()
        if name in {"skin", "skinname", "skin2", "skin3"}:
            by_name[name] = _split_skin_values(prop.get("value"))
    ordered: List[str] = []
    for name in ("skin", "skinname", "skin2", "skin3"):
        for path in by_name.get(name, ()):
            if path not in ordered:
                ordered.append(path)
    return ordered


def _merge_object_lto_classes(classes: Dict[str, Dict[str, Any]],
                              object_lto_dump: Optional[Dict[str, Any]]) -> None:
    """Merge visible, runtime object.lto classes into the catalog class map."""
    if not object_lto_dump:
        return

    for class_name, class_info in sorted(
        (object_lto_dump.get("classes") or {}).items()
    ):
        if not isinstance(class_info, dict):
            continue

        template_props = _object_lto_template_properties(class_info)
        property_names = {str(p["name"]) for p in template_props}
        filenames, skins, accessory_skins = _object_lto_resources(template_props)
        existing = classes.get(class_name)
        if existing is not None:
            existing["source"] = "object.lto+dat"
            existing["property_names"] = sorted(
                set(existing.get("property_names") or ()) | property_names
            )
            existing["filenames"] = sorted(
                set(existing.get("filenames") or ()) | filenames
            )
            if skins:
                existing["skins"] = sorted(
                    set(existing.get("skins") or ()) | skins
                )
            if accessory_skins:
                existing["accessory_skins"] = sorted(
                    set(existing.get("accessory_skins") or ()) | accessory_skins
                )
            existing["object_lto"] = {
                "parent": class_info.get("parent"),
                "hierarchy": class_info.get("hierarchy") or [],
                "flags": int(class_info.get("flags") or 0),
                "flag_names": class_info.get("flag_names") or [],
                "hidden_in_dedit": bool(class_info.get("hidden_in_dedit")),
                "runtime_loadable": bool(class_info.get("runtime_loadable", True)),
                "template_properties": template_props,
            }
            continue

        if class_info.get("hidden_in_dedit"):
            continue
        if class_info.get("runtime_loadable") is False:
            continue

        classes[class_name] = {
            "instance_count": 0,
            "levels": [],
            "category": categorize(class_name, property_names, filenames),
            "property_names": sorted(property_names),
            "filenames": sorted(filenames),
            **({"skins": sorted(skins)} if skins else {}),
            **({"accessory_skins": sorted(accessory_skins)} if accessory_skins else {}),
            "source": "object.lto",
            "object_lto": {
                "parent": class_info.get("parent"),
                "hierarchy": class_info.get("hierarchy") or [],
                "flags": int(class_info.get("flags") or 0),
                "flag_names": class_info.get("flag_names") or [],
                "hidden_in_dedit": False,
                "runtime_loadable": True,
            },
            "template": {
                "source_level": "object.lto",
                "source_instance": class_name,
                "default_pos": [0.0, 0.0, 0.0],
                "properties": template_props,
            },
        }


def _add_model_variant(
    variants: Dict[str, Dict[Tuple[str, ...], Dict[str, List[str]]]],
    model: object,
    skins: Iterable[object],
    *,
    name: object = "",
    source_key: object = "",
) -> None:
    model_path = _normalise_resource_path(model, "models", (".abc", ".lta", ".ltb"))
    skin_paths: List[str] = []
    for value in skins:
        for path in _split_skin_values(value):
            if path not in skin_paths:
                skin_paths.append(path)
    if not model_path or not skin_paths:
        return
    entry = variants.setdefault(model_path, {}).setdefault(
        tuple(skin_paths), {"names": [], "source_keys": []}
    )
    label = str(name or "").strip()
    source = str(source_key or "").strip()
    if label and label not in entry["names"]:
        entry["names"].append(label)
    if source and source not in entry["source_keys"]:
        entry["source_keys"].append(source)


_INFERRED_VARIANT_SUFFIXES = (
    "chief", "elite", "warrior", "red", "blue", "green", "black", "white",
    "gold", "silver", "dark", "light",
)


def _inferred_variant_suffix(model_stem: str, skin_stem: str) -> Optional[str]:
    if skin_stem == model_stem:
        return ""
    if not skin_stem.startswith(model_stem):
        return None
    suffix = skin_stem[len(model_stem):]
    if suffix in _INFERRED_VARIANT_SUFFIXES:
        return suffix
    if re.fullmatch(r"(?:\d+[a-z]?|[abc])", suffix):
        return suffix
    return None


def _add_inferred_model_variants(
    variants: Dict[str, Dict[Tuple[str, ...], Dict[str, List[str]]]],
    model_paths: Iterable[str],
    skin_paths: Iterable[str],
) -> None:
    skins_by_stem: Dict[str, List[str]] = {}
    for value in skin_paths:
        path = _normalise_resource_path(value, "skins", (".dtx",))
        if path:
            skins_by_stem.setdefault(os.path.splitext(os.path.basename(path))[0].casefold(), []).append(path)
    for model_path in model_paths:
        stem = os.path.splitext(os.path.basename(model_path))[0].casefold()
        if stem not in skins_by_stem:
            continue
        candidates: List[Tuple[str, str]] = []
        for skin_stem, paths in skins_by_stem.items():
            suffix = _inferred_variant_suffix(stem, skin_stem)
            if suffix is None:
                continue
            candidates.extend((suffix, path) for path in paths)
        for suffix, path in sorted(candidates):
            raw_label = os.path.splitext(os.path.basename(model_path))[0]
            label = raw_label[:1].upper() + raw_label[1:]
            if suffix:
                label += " " + (suffix.title() if not suffix.isdigit() else suffix)
            _add_model_variant(
                variants,
                model_path,
                [path],
                name=label,
                source_key=f"asset-name:{path}",
            )


def _json_model_variants(
    variants: Dict[str, Dict[Tuple[str, ...], Dict[str, List[str]]]],
) -> Dict[str, List[Dict[str, Any]]]:
    result: Dict[str, List[Dict[str, Any]]] = {}
    for model_path, skin_sets in sorted(variants.items()):
        rows: List[Dict[str, Any]] = []
        used_names: Dict[str, int] = {}
        for skins, details in sorted(skin_sets.items()):
            base_name = details["names"][0] if details["names"] else os.path.splitext(os.path.basename(model_path))[0]
            count = used_names.get(base_name.casefold(), 0) + 1
            used_names[base_name.casefold()] = count
            rows.append({
                "name": base_name if count == 1 else f"{base_name} {count}",
                "skins": list(skins),
                "source_keys": details["source_keys"],
            })
        result[model_path] = rows
    return result

# --------------------------------------------------------------------------
# Categorisation
# --------------------------------------------------------------------------

# Map class names to broad categories.  Drives the colour palette in the editor.
#
# The explicit name rules are kept for classes that are unambiguous from their
# type name alone.  During catalog builds, categorize() also receives the
# observed property/model sets for each class; those data-derived checks catch
# MM9's many AI actor subclasses whose names do not contain words like
# "Warrior" or "Sorcerer" (Apparition, Annelid, Basilisk, Cat, etc.).

_AI_ACTOR_PROPERTIES = {
    "CanBeResurrected",
    "CanDamage",
    "GiveTreasure",
    "RangeAttackType",
    "SightDistance",
    "HearingDistance",
    "WanderON",
    "WanderOptions",
    "RunawayChance",
    "BuryOnDeath",
    "FadeOnDeath",
}

_ENVIRONMENT_PROPERTIES = {
    "FXStuff",
    "FogEnable",
    "FxName",
    "RainProperties",
    "SnowProperties",
    "SurfaceHeight",
    "Viscosity",
}

_INTERACTIVE_PROPERTIES = {
    "CloseTrigger0",
    "DefaultSpawn",
    "Destructable",
    "MoveDir",
    "MoveDist",
    "OpenTrigger0",
    "ProjectileName",
    "RotatingStuff",
    "TeleportDestination",
}

_MARKER_PROPERTIES = {
    "FOV",
    "HideRadius",
    "UseMarkerRotation",
}

_PROP_MODEL_PROPERTIES = {
    "AICanReachPlayer",
    "CanAttackThru",
    "Filename",
    "HidePieces",
    "NonSolidUse",
    "ObjectMass",
    "Rotates",
    "Skin",
}

_AMBIENT_CREATURE_CLASSES = {
    "BrownCow",
    "Cat",
    "Cow",
    "Dog",
    "Ewe",
    "Fish",
    "Goat",
    "Goose",
    "Pig",
    "Rooster",
}

_AMBIENT_CREATURE_MODELS = {
    "cat",
    "cow",
    "dog",
    "ewe",
    "fish",
    "goat",
    "hen",
    "pig",
    "rooster",
}

_NPC_CLASS_PREFIXES = (
    "Commoner",
    "Honk",
    "ElderHonk",
    "Town",
    "Shopkeeper",
    "ShopKeeper",
    "Wealthy",
    "Poor",
    "Prisoner",
)

_NPC_NAMED_CLASSES = {
    "BjarniThorvaldssen",
    "Baron",
    "ClanSoldier",
    "Concubine",
    "Count",
    "DragonKing",
    "DwarvenGuard",
    "ForadDarre",
    "ForadDarreNPC",
    "Fre",
    "Guard",
    "GuardCaptain",
    "GuardSegeant",
    "HalfOrcCaptain",
    "HalfOrcSoldier",
    "Hanndl",
    "KiratheCold",
    "Krohn",
    "LichKing",
    "MarkeltheGreat",
    "Monk",
    "NjamtheMeddler",
    "OldHag",
    "ReverendMonk",
    "SigmundtheStressed",
    "Skraelos",
    "SvenSvenssen",
    "TryggvaRavenlocks",
}


def _filename_stems(filenames: Optional[Iterable[str]]) -> Set[str]:
    stems: Set[str] = set()
    for fname in filenames or ():
        base = fname.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if "." in base:
            base = base.rsplit(".", 1)[0]
        if base:
            stems.add(base)
    return stems


def _has_ai_actor_signature(property_names: Optional[Iterable[str]]) -> bool:
    props = set(property_names or ())
    return len(_AI_ACTOR_PROPERTIES & props) >= 8


def _has_any_property(
    property_names: Optional[Iterable[str]],
    choices: Set[str],
) -> bool:
    return bool(set(property_names or ()) & choices)


def _has_prop_model_signature(
    property_names: Optional[Iterable[str]],
    filenames: Optional[Iterable[str]] = None,
) -> bool:
    props = set(property_names or ())
    if "Filename" not in props:
        return False
    if len(props & _PROP_MODEL_PROPERTIES) >= 4:
        return True
    model_dirs = (f.replace("\\", "/").lower() for f in filenames or ())
    return any("/props/" in f or "/pickupitems/" in f for f in model_dirs)


def _has_partial_actor_signature(property_names: Optional[Iterable[str]]) -> bool:
    props = set(property_names or ())
    return {"SightDistance", "HearingDistance", "Weapon"}.issubset(props)


def _is_ambient_creature(
    class_name: str,
    filenames: Optional[Iterable[str]] = None,
) -> bool:
    if class_name in _AMBIENT_CREATURE_CLASSES:
        return True
    return bool(_filename_stems(filenames) & _AMBIENT_CREATURE_MODELS)


def _is_civilian_npc(class_name: str) -> bool:
    return class_name.startswith(_NPC_CLASS_PREFIXES)


CATEGORY_RULES = [
    # (predicate, category)
    (lambda c: c == "StartPoint",                                     "spawn"),
    (lambda c: c.endswith("Trigger") or c == "Trigger",               "trigger"),
    (lambda c: "Light" in c,                                          "light"),
    (lambda c: "Sound" in c or c == "AmbientSound",                   "sound"),
    (lambda c: "Door" in c,                                           "door"),
    (lambda c: _is_civilian_npc(c),                                    "npc_civilian"),
    (lambda c: c in _NPC_NAMED_CLASSES,                                "npc_named"),
    (lambda c: "Soldier" in c or "Warrior" in c or "Sorcerer" in c
            or c in ("ColloidalSoldier","ColloidalGuardian",
                     "ColloidalWarrior","SkeletonWarrior",
                     "JellySpore","BasketBalg","KingBasilisk",
                     "EvilGrandSorcerer","EvilSorcerer",
                     "GreenMan","LobberPod","LobberPodB","Oculus"),   "monster"),
    (lambda c: c in _AMBIENT_CREATURE_CLASSES,                         "creature"),
    (lambda c: c in ("Prop","DestructableProp","CandleProp",
                     "WallTorch","Torch","Barrel","Cookpot",
                     "BonePile","Cauldron","TreasureChest"),          "prop"),
    (lambda c: c in ("Marker","AIRail","AIBarrier",
                     "InvisibleBrush","DamageBrush",
                     "PerceptionBrush","PortalZone",
                     "VolumeBrush","HidingPlace"),                 "marker"),
    (lambda c: c == "WorldProperties" or c == "OutsideDef"
            or c == "TOD_Sky" or c == "SkyPointer"
            or c == "DemoSkyWorldModel" or c == "Terrain",            "world"),
    (lambda c: c in ("Switch","Button","Ladder","ScriptObject",
                     "BlueWater","Fire","EarthQuake"),                "interactive"),
]

CATEGORY_COLORS = {
    "spawn":         "#00d050",
    "trigger":       "#5080ff",
    "light":         "#ffd040",
    "sound":         "#b070ff",
    "door":          "#a0703a",
    "npc_civilian":  "#ff7050",
    "npc_named":     "#ff3030",
    "monster":       "#a02020",
    "creature":      "#d09040",
    "prop":          "#40c060",
    "marker":        "#909090",
    "world":         "#3070b0",
    "interactive":   "#e0e040",
    "other":         "#808080",
}


def categorize(
    class_name: str,
    property_names: Optional[Iterable[str]] = None,
    filenames: Optional[Iterable[str]] = None,
) -> str:
    for predicate, cat in CATEGORY_RULES:
        if predicate(class_name):
            return cat

    if _has_any_property(property_names, _ENVIRONMENT_PROPERTIES):
        return "world"
    if _has_any_property(property_names, _INTERACTIVE_PROPERTIES):
        return "interactive"
    if _has_any_property(property_names, _MARKER_PROPERTIES):
        return "marker"
    if _has_prop_model_signature(property_names, filenames):
        return "prop"

    if _has_ai_actor_signature(property_names) or _has_partial_actor_signature(property_names):
        if _is_ambient_creature(class_name, filenames):
            return "creature"
        return "monster"

    return "other"


# --------------------------------------------------------------------------
# Build from a temporary WORLDS/ folder
# --------------------------------------------------------------------------

def _json_actor_visuals(actor_visuals: Optional[Dict[str, ActorVisual]]) -> Dict[str, Any]:
    if not actor_visuals:
        return {}
    return {
        key: visual.to_json()
        for key, visual in sorted(actor_visuals.items())
    }


def build_catalog(
    worlds_dir: str,
    actor_visuals: Optional[Dict[str, ActorVisual]] = None,
    actor_visual_rules: Optional[Iterable[object]] = None,
    object_lto_dump: Optional[Dict[str, Any]] = None,
    skin_paths: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Scan all *.DAT files in *worlds_dir* and return a catalog dict.

    This is the low-level builder used by :func:`build_catalog_from_rez`, which
    extracts DATs to a temp folder first.
    """
    classes: Dict[str, Dict[str, Any]] = {}
    filenames: Dict[str, Dict[str, Any]] = {}
    model_variants: Dict[str, Dict[Tuple[str, ...], Dict[str, List[str]]]] = {}
    max_npc_nbr = 0

    for key, visual in (actor_visuals or {}).items():
        _add_model_variant(
            model_variants,
            visual.model,
            visual.all_skins,
            name=visual.type_picture or visual.monster_name or key,
            source_key=key,
        )

    dat_paths = sorted(glob.glob(os.path.join(worlds_dir, "*.DAT")))
    for path in dat_paths:
        lvl = os.path.basename(path)
        try:
            world = patcher.World.load(path)
        except Exception as e:
            print(f"  [skip] {lvl}: {e}", file=sys.stderr)
            continue

        for obj in world.objects:
            cls = obj.type_str
            entry = classes.setdefault(cls, {
                "instance_count": 0,
                "levels":         set(),
                "category":       "other",
                "property_names": set(),
                "filenames":      set(),
                "template":       None,
            })
            entry["instance_count"] += 1
            entry["levels"].add(lvl)
            for p in obj.props:
                entry["property_names"].add(p.name)

            actor_visual = resolve_actor_visual(
                actor_visuals,
                cls,
                str(obj.get("Name") or ""),
                str(obj.get("ScriptName") or ""),
                actor_visual_rules,
            )
            fname = actor_visual.model if actor_visual else obj.get("Filename")
            if isinstance(fname, str) and fname.endswith((".abc", ".ABC", ".lta", ".ltb")):
                fname_key = fname.lower()
                entry["filenames"].add(fname_key)
                if actor_visual is not None:
                    skins = entry.setdefault("skins", set())
                    skins.update(s.lower() for s in actor_visual.skins)
                    accessory_skins = entry.setdefault("accessory_skins", set())
                    accessory_skins.update(
                        s.lower() for s in actor_visual.accessory_skins
                    )
                    sources = entry.setdefault("actor_visual_sources", set())
                    sources.add(
                        f"{actor_visual.source_file}:{actor_visual.number}"
                        + (
                            ":editor-preview-only"
                            if actor_visual.editor_preview_only
                            else ""
                        )
                    )
                    variant_skins: Iterable[object] = actor_visual.all_skins
                    variant_name = actor_visual.type_picture or actor_visual.monster_name or cls
                    variant_source = f"{actor_visual.source_file}:{actor_visual.number}"
                else:
                    variant_skins = [
                        obj.get("Skin"), obj.get("Skin2"), obj.get("Skin3")
                    ]
                    observed_skins = {
                        skin
                        for value in variant_skins
                        for skin in _split_skin_values(value)
                    }
                    if observed_skins:
                        entry.setdefault("skins", set()).update(observed_skins)
                    observed_accessories = {
                        skin
                        for value in (obj.get("Skin2"), obj.get("Skin3"))
                        for skin in _split_skin_values(value)
                    }
                    if observed_accessories:
                        entry.setdefault("accessory_skins", set()).update(
                            observed_accessories
                        )
                    variant_name = cls
                    variant_source = f"{lvl}:{obj.get('Name') or cls}"
                _add_model_variant(
                    model_variants,
                    fname,
                    variant_skins,
                    name=variant_name,
                    source_key=variant_source,
                )
                fnentry = filenames.setdefault(fname_key, {
                    "uses": 0, "classes": set(), "levels": set()
                })
                fnentry["uses"] += 1
                fnentry["classes"].add(cls)
                fnentry["levels"].add(lvl)

            # First instance becomes the canonical template for this class
            if entry["template"] is None:
                pos = obj.get("Pos") or (0, 0, 0)
                entry["template"] = {
                    "source_level":    lvl,
                    "source_instance": obj.get("Name") or "?",
                    "default_pos":     [float(pos[0]), float(pos[1]), float(pos[2])],
                }

            n = obj.get("NPCNbr")
            if isinstance(n, int):
                f = struct.unpack("<f", struct.pack("<I", n))[0]
                if 0 < f < 1e7:
                    max_npc_nbr = max(max_npc_nbr, int(f))

    # Convert sets to sorted lists for JSON serialisation
    for cls, entry in classes.items():
        entry["category"]       = categorize(
            cls, entry["property_names"], entry["filenames"])
        entry["levels"]         = sorted(entry["levels"])
        entry["property_names"] = sorted(entry["property_names"])
        entry["filenames"]      = sorted(entry["filenames"])
        entry.setdefault("source", "dat")
        if "skins" in entry:
            entry["skins"] = sorted(entry["skins"])
        if "accessory_skins" in entry:
            entry["accessory_skins"] = sorted(entry["accessory_skins"])
        if "actor_visual_sources" in entry:
            entry["actor_visual_sources"] = sorted(entry["actor_visual_sources"])
    for fn, entry in filenames.items():
        entry["levels"]  = sorted(entry["levels"])
        entry["classes"] = sorted(entry["classes"])

    _merge_object_lto_classes(classes, object_lto_dump)

    for class_name, class_info in ((object_lto_dump or {}).get("classes") or {}).items():
        if class_name not in classes or not isinstance(class_info, dict):
            continue
        template_props = _object_lto_template_properties(class_info)
        lto_models, lto_skins, _accessory_skins = _object_lto_resources(template_props)
        ordered_lto_skins = _object_lto_variant_skins(template_props)
        for model_path in lto_models:
            _add_model_variant(
                model_variants,
                model_path,
                ordered_lto_skins,
                name=class_name,
                source_key=f"object.lto:{class_name}",
            )

    all_model_paths = set(filenames)
    for entry in classes.values():
        all_model_paths.update(entry.get("filenames") or ())
    _add_inferred_model_variants(model_variants, all_model_paths, skin_paths or ())

    catalog = {
        "classes":   classes,
        "filenames": filenames,
        "actor_visuals": _json_actor_visuals(actor_visuals),
        "model_variants": _json_model_variants(model_variants),
        "summary": {
            "total_levels":            len(dat_paths),
            "total_classes":           len(classes),
            "max_npc_nbr":             max_npc_nbr,
            "free_npc_nbrs_above_max": list(range(max_npc_nbr + 1, max_npc_nbr + 21)),
        },
    }
    if object_lto_dump is not None:
        catalog["object_lto"] = object_lto_dump
    return catalog


def load_catalog(path: str) -> Dict[str, Any]:
    """Load and return a previously built catalog.json."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
    
def save_catalog(catalog: Dict[str, Any], path: str = DEFAULT_CATALOG_PATH) -> None:
    """Write *catalog* to *path*, creating parent directories if needed."""
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)


# --------------------------------------------------------------------------
# Build directly from WORLDS.REZ (standard install path)
# --------------------------------------------------------------------------

def build_catalog_from_rez(
    worlds_rez_path: str,
    data_rez_path: Optional[str] = None,
    data_dir: Optional[str] = None,
    object_lto_dump_path: Optional[str] = None,
    object_lto_path: Optional[str] = None,
    object_lto_helper_path: Optional[str] = None,
    actor_visual_rules: Optional[Iterable[object]] = None,
    skins_rez_path: Optional[str] = None,
    skins_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the catalog by extracting .DAT levels from *worlds_rez_path*.

    This is the standard first-run path used by the editor when no
    ``catalog.json`` exists.  It:

    1. Extracts every ``WORLDS/<name>`` entry whose first byte is ``0x42``
       (v66 DAT magic) into a temporary directory, adding a ``.DAT`` suffix.
    2. Optionally reads ACTOR.TXT/MONSTERS.TXT from DATA.REZ or an extracted
       DATA folder so NPC/monster models use the same runtime table as the game.
    3. Calls :func:`build_catalog` on that directory.
    4. Deletes the temp directory unconditionally.

    A standard MM9 install has no loose WORLDS/ folder -- all levels live
    inside WORLDS.REZ -- so this function is the only way to build the
    catalog without manually extracting the archive first.
    """
    import shutil
    import tempfile

    from core.rezmgr import RezReader

    print(f"Building catalog from {os.path.basename(worlds_rez_path)} ...")
    actor_visuals: Dict[str, ActorVisual] = {}
    if data_dir:
        actor_visuals = load_actor_visuals_from_data_dir(data_dir)
    elif data_rez_path:
        actor_visuals = load_actor_visuals_from_data_rez(data_rez_path)
    if actor_visuals:
        print(f"  {len(actor_visuals)} actor/monster visual keys loaded")

    if not skins_rez_path and not skins_dir:
        sibling_dir = os.path.dirname(os.path.abspath(worlds_rez_path))
        candidate_dir = os.path.join(sibling_dir, "SKINS")
        candidate_rez = os.path.join(sibling_dir, "SKINS.REZ")
        if os.path.isdir(candidate_dir):
            skins_dir = candidate_dir
        elif os.path.isfile(candidate_rez):
            skins_rez_path = candidate_rez

    skin_paths: List[str] = []
    if skins_dir and os.path.isdir(skins_dir):
        for current, _dirs, names in os.walk(skins_dir):
            for name in names:
                if name.lower().endswith(".dtx"):
                    relative = os.path.relpath(os.path.join(current, name), skins_dir)
                    skin_paths.append("skins\\" + relative.replace("/", "\\"))
    elif skins_rez_path and os.path.isfile(skins_rez_path):
        skin_reader = RezReader(skins_rez_path).open()
        try:
            skin_paths = [
                path for path in skin_reader.list_paths()
                if path.lower().endswith(".dtx")
            ]
        finally:
            skin_reader.close()
    if skin_paths:
        print(f"  {len(skin_paths)} skin resources indexed")

    object_lto_dump: Optional[Dict[str, Any]] = None
    try:
        object_lto_dump = resolve_object_lto_dump(
            object_lto_dump_path=object_lto_dump_path,
            object_lto_path=object_lto_path,
            helper_path=object_lto_helper_path,
        )
        if object_lto_dump:
            print(
                "  object.lto classes loaded: "
                f"{object_lto_dump.get('class_count', '?')}"
            )
    except ObjectLtoDumpError as exc:
        print(
            f"  [warn] object.lto dump unavailable; using DAT scan only: {exc}",
            file=sys.stderr,
        )

    tmpdir = tempfile.mkdtemp(prefix="mm9cat_")
    reader = None
    try:
        reader = RezReader(worlds_rez_path).open()
        extracted = 0
        for vpath in reader.list_paths():
            if not vpath.upper().startswith("WORLDS/"):
                continue
            data = reader.extract_to_bytes(vpath)
            # Must be a v66 DAT (first byte 0x42 = format magic)
            if len(data) < 4 or data[0] != 0x42:
                continue
            # Most REZ entries carry no extension, but duplicate same-name
            # resources are exposed as typed paths such as BOOTCAMP.DAT.
            basename = os.path.basename(vpath).upper()
            if not basename.endswith(".DAT"):
                basename += ".DAT"
            with open(os.path.join(tmpdir, basename), "wb") as f:
                f.write(data)
            extracted += 1
        print(f"  {extracted} levels extracted")
        return build_catalog(
            tmpdir,
            actor_visuals=actor_visuals,
            actor_visual_rules=actor_visual_rules,
            object_lto_dump=object_lto_dump,
            skin_paths=skin_paths,
        )
    finally:
        if reader is not None:
            reader.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Build or inspect the MM9 mod catalog (catalog.json).",
        epilog=(
            "The editor builds catalog.json automatically on first launch. "
            "Run this script manually only to force a rebuild or to inspect "
            "an existing catalog."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # build-from-rez — standard path, mirrors what the editor does on first run
    pbr = sub.add_parser(
        "build-from-rez",
        help="build catalog.json directly from WORLDS.REZ (standard install)",
    )
    pbr.add_argument(
        "worlds_rez",
        metavar="WORLDS.REZ",
        help="path to WORLDS.REZ inside the MM9 data/ folder",
    )
    pbr.add_argument(
        "--out",
        default=DEFAULT_CATALOG_PATH,
        help="output path for catalog.json (default: catalog/data/catalog.json)",
    )
    pbr.add_argument(
        "--data-rez",
        default=None,
        help="optional path to DATA.REZ; ACTOR.TXT/MONSTERS.TXT override NPC models",
    )
    pbr.add_argument(
        "--data-dir",
        default=None,
        help="optional extracted DATA folder containing ACTOR.TXT/MONSTERS.TXT",
    )
    pbr.add_argument(
        "--skins-rez",
        default=None,
        help=(
            "optional path to SKINS.REZ for model-variant inference; when "
            "omitted, a sibling SKINS directory or SKINS.REZ is detected"
        ),
    )
    pbr.add_argument(
        "--skins-dir",
        default=None,
        help="optional extracted SKINS directory for model-variant inference",
    )
    pbr.add_argument(
        "--object-lto-dump",
        default=None,
        help=(
            "optional JSON dump produced by tools/object_lto_dump; when "
            "present, this is preferred over running the native helper"
        ),
    )
    pbr.add_argument(
        "--object-lto",
        default=None,
        help=(
            "optional path to object.lto; the native helper is run to generate "
            "the class dump, and failures fall back to the DAT scan"
        ),
    )
    pbr.add_argument(
        "--object-lto-helper",
        default=None,
        help=(
            "optional path to object_lto_dump.exe "
            f"(default: {DEFAULT_OBJECT_LTO_DUMP_HELPER})"
        ),
    )

    # info -- summarise an existing catalog
    pi = sub.add_parser("info", help="print a summary of an existing catalog.json")
    pi.add_argument("path", metavar="catalog.json")

    args = p.parse_args(argv)

    if args.cmd == "build-from-rez":
        if not os.path.isfile(args.worlds_rez):
            print(f"ERROR: {args.worlds_rez!r} not found", file=sys.stderr)
            return 1
        cat = build_catalog_from_rez(
            args.worlds_rez,
            data_rez_path=args.data_rez,
            data_dir=args.data_dir,
            object_lto_dump_path=args.object_lto_dump,
            object_lto_path=args.object_lto,
            object_lto_helper_path=args.object_lto_helper,
            skins_rez_path=args.skins_rez,
            skins_dir=args.skins_dir,
        )
        save_catalog(cat, args.out)
        s = cat["summary"]
        print(f"Wrote {args.out}")
        print(f"  levels:          {s['total_levels']}")
        print(f"  classes:         {s['total_classes']}")
        print(
            "  model variants:  "
            f"{sum(len(rows) for rows in cat.get('model_variants', {}).values())}"
        )
        if cat.get("object_lto", {}).get("available"):
            print(
                "  object.lto:      "
                f"{cat['object_lto'].get('class_count', '?')} classes"
            )
        print(f"  max NPCNbr seen: {s['max_npc_nbr']}")
        print(f"  next free NPCs:  {s['free_npc_nbrs_above_max'][:5]} ...")
        return 0

    if args.cmd == "info":
        cat = load_catalog(args.path)
        s = cat.get("summary", {})
        print(f"Catalog: {args.path}")
        print(f"  levels:         {s.get('total_levels', '?')}")
        print(f"  classes:        {s.get('total_classes', '?')}")
        print(
            "  model variants: "
            f"{sum(len(rows) for rows in cat.get('model_variants', {}).values())}"
        )
        object_lto = cat.get("object_lto") or {}
        if object_lto.get("available"):
            print(f"  object.lto:     {object_lto.get('class_count', '?')} classes")
        print(f"  max NPCNbr:     {s.get('max_npc_nbr', '?')}")
        print(f"  next free NPCs: {s.get('free_npc_nbrs_above_max', [])[:5]} ...")
        return 0

    return 2

if __name__ == "__main__":
    sys.exit(main())
