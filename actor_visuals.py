"""
actor_visuals.py
================

Lookup table for actor/monster model and skin names stored in DATA.REZ.
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from io import StringIO
from typing import Dict, Iterable, List, Optional, Tuple


_TABLE_FILENAMES = ("ACTOR.TXT", "MONSTERS.TXT")
_GENERIC_TYPE_PREFIXES = {"peasant"}


@dataclass(frozen=True)
class ActorVisual:
    key: str
    model: str
    skins: Tuple[str, ...]
    source_file: str
    number: str
    monster_name: str
    type_picture: str

    def to_json(self) -> Dict[str, object]:
        return {
            "model": self.model,
            "skins": list(self.skins),
            "source_file": self.source_file,
            "number": self.number,
            "monster_name": self.monster_name,
            "type_picture": self.type_picture,
        }


def _token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _strip_instance_suffix(value: str) -> str:
    return re.sub(r"\d+$", "", str(value or ""))


def _model_path(model_name: str) -> str:
    value = str(model_name or "").replace("/", "\\").strip().strip('"')
    if not value or value == "0":
        return ""
    if not value.lower().startswith("models\\"):
        value = "models\\" + value
    return value


def _skin_path(skin_name: str) -> str:
    value = str(skin_name or "").replace("/", "\\").strip().strip('"')
    if not value or value == "0":
        return ""
    if not value.lower().startswith("skins\\"):
        value = "skins\\" + value
    return value


def _type_picture_variants(type_picture: str) -> List[str]:
    words = [w for w in re.split(r"\s+", str(type_picture or "").strip()) if w]
    variants: List[List[str]] = []

    def add(candidate: List[str]) -> None:
        if candidate and candidate not in variants:
            variants.append(candidate)

    add(words)
    if words and words[0].lower() in _GENERIC_TYPE_PREFIXES:
        add(words[1:])
    if len(words) > 1 and re.fullmatch(r"[A-Za-z]", words[-1]):
        add(words[:-1])
        if words[0].lower() in _GENERIC_TYPE_PREFIXES:
            add(words[1:-1])

    return [" ".join(v) for v in variants]


def _row_keys(monster_name: str, type_picture: str) -> List[str]:
    monster_key = _token(monster_name)
    if not monster_key:
        return []

    out: List[str] = []
    seen = set()
    for variant in _type_picture_variants(type_picture):
        key = monster_key + _token(variant)
        if key and key not in seen:
            out.append(key)
            seen.add(key)
    return out


def object_actor_keys(type_str: str, object_name: str = "") -> List[str]:
    """Return actor-table lookup keys for a DAT world object."""
    out: List[str] = []
    seen = set()
    for raw in (_strip_instance_suffix(object_name), type_str):
        key = _token(raw)
        if key and key not in seen:
            out.append(key)
            seen.add(key)
    return out


def resolve_actor_visual(
    visual_index: Optional[Dict[str, object]],
    type_str: str,
    object_name: str = "",
) -> Optional[ActorVisual]:
    if not visual_index:
        return None
    for key in object_actor_keys(type_str, object_name):
        visual = visual_index.get(key)
        if isinstance(visual, ActorVisual):
            return visual
        if isinstance(visual, dict):
            return ActorVisual(
                key=key,
                model=str(visual.get("model", "") or ""),
                skins=tuple(str(s) for s in visual.get("skins", []) if s),
                source_file=str(visual.get("source_file", "") or ""),
                number=str(visual.get("number", "") or ""),
                monster_name=str(visual.get("monster_name", "") or ""),
                type_picture=str(visual.get("type_picture", "") or ""),
            )
    return None


def parse_actor_visual_tables(
    tables: Iterable[Tuple[str, str]],
) -> Dict[str, ActorVisual]:
    """Parse ACTOR.TXT/MONSTERS.TXT contents into runtime model lookups.

    If the same lookup key appears in both files, later tables win.  Callers
    should pass ACTOR.TXT first and MONSTERS.TXT second, because MONSTERS.TXT
    has several corrected model/skin values where ACTOR.TXT stores placeholders.
    Within one file, the first row for a key is kept so A/B cosmetic variants
    resolve predictably to the first game-table entry.
    """
    merged: Dict[str, ActorVisual] = {}

    for source_file, text in tables:
        rows = list(csv.DictReader(StringIO(text), delimiter="\t"))
        name_counts: Dict[str, int] = {}
        for row in rows:
            monster_key = _token(row.get("Monster Name", ""))
            name_counts[monster_key] = name_counts.get(monster_key, 0) + 1

        local: Dict[str, ActorVisual] = {}
        for row in rows:
            model = _model_path(row.get("ModelName", ""))
            if not model:
                continue
            # SkinName2/SkinName3 are usually weapon/accessory skins, so keep
            # them out of the body preview for now.
            primary_skin = _skin_path(row.get("SkinName", ""))
            skins = (primary_skin,) if primary_skin else ()
            monster_name = str(row.get("Monster Name", "") or "").strip()
            type_picture = str(row.get("Type/Picture", "") or "").strip()
            keys = _row_keys(monster_name, type_picture)

            monster_key = _token(monster_name)
            if monster_key and name_counts.get(monster_key, 0) == 1:
                keys.append(monster_key)

            for key in keys:
                if not key or key in local:
                    continue
                local[key] = ActorVisual(
                    key=key,
                    model=model,
                    skins=skins,
                    source_file=source_file,
                    number=str(row.get("Number", "") or "").strip(),
                    monster_name=monster_name,
                    type_picture=type_picture,
                )

        merged.update(local)

    return merged


def load_actor_visuals_from_data_dir(data_dir: str) -> Dict[str, ActorVisual]:
    tables: List[Tuple[str, str]] = []
    for filename in _TABLE_FILENAMES:
        path = os.path.join(data_dir, filename)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="latin-1", newline="") as fh:
            tables.append((filename, fh.read()))
    return parse_actor_visual_tables(tables)


def load_actor_visuals_from_data_rez(data_rez_path: str) -> Dict[str, ActorVisual]:
    from mm9_rezmgr import RezReader

    reader = RezReader(data_rez_path).open()
    try:
        path_by_name = {
            os.path.basename(vpath).upper(): vpath
            for vpath in reader.list_paths()
        }
        tables: List[Tuple[str, str]] = []
        for filename in _TABLE_FILENAMES:
            vpath = path_by_name.get(filename) or path_by_name.get(
                os.path.splitext(filename)[0])
            if not vpath:
                continue
            text = reader.extract_to_bytes(vpath).decode("latin-1")
            tables.append((filename, text))
        return parse_actor_visual_tables(tables)
    finally:
        reader.close()
