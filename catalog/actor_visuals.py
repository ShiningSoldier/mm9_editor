"""
actor_visuals.py
================

Lookup table for actor/monster model and skin names stored in DATA.REZ.
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, replace
from io import StringIO
from typing import Any, Dict, Iterable, List, Optional, Tuple


_TABLE_FILENAMES = ("ACTOR.TXT", "MONSTERS.TXT")
_GENERIC_TYPE_PREFIXES = {"peasant"}
@dataclass(frozen=True)
class ActorVisualRule:
    source_file: str
    source_row: str
    comment: str
    lookup_key: str = ""
    type_str: str = ""
    object_name: str = ""
    object_name_prefix: str = ""
    script_name: str = ""
    editor_preview_only: bool = False
    fallback_model: str = ""
    fallback_skins: Tuple[str, ...] = ()
    fallback_accessory_skins: Tuple[str, ...] = ()
    fallback_monster_name: str = ""
    fallback_type_picture: str = ""

    @property
    def visual_key(self) -> str:
        return self.lookup_key or _row_lookup_key(self.source_file, self.source_row)

    def matches(self, type_str: str, object_name: str, script_name: str = "") -> bool:
        if self.type_str and self.type_str != type_str:
            return False
        if self.object_name and self.object_name != object_name:
            return False
        if self.object_name_prefix and not object_name.startswith(
            self.object_name_prefix
        ):
            return False
        if self.script_name and _script_key(self.script_name) != _script_key(
            script_name
        ):
            return False
        return True


ActorVisualQuirk = ActorVisualRule


_ACTOR_VISUAL_RULES: Tuple[ActorVisualRule, ...] = (
    ActorVisualRule(
        type_str="Honk",
        object_name="Accountant",
        source_file="MONSTERS.TXT",
        source_row="217",
        comment=(
            "TEMPLEOFHONK.DAT stores Accountant as class Honk, but in-game "
            "appearance matches ElderHonkFemale / Honk Worshipper2 B."
        ),
    ),
    ActorVisualRule(
        type_str="Honk",
        source_file="MONSTERS.TXT",
        source_row="186",
        comment="Base Honk class maps to Honk Worshipper A.",
    ),
    ActorVisualRule(
        type_str="Honk2",
        source_file="MONSTERS.TXT",
        source_row="216",
        comment="Honk2 class maps to Honk Worshipper2 A.",
    ),
    ActorVisualRule(
        type_str="ElderHonk",
        source_file="MONSTERS.TXT",
        source_row="187",
        comment="ElderHonk class maps to Elder Honk / Honk Worshipper B.",
    ),
    ActorVisualRule(
        type_str="ElderHonkFemale",
        source_file="MONSTERS.TXT",
        source_row="217",
        comment="ElderHonkFemale class maps to Elder Honk / Honk Worshipper2 B.",
    ),
    ActorVisualRule(
        type_str="HonkSeer",
        source_file="MONSTERS.TXT",
        source_row="188",
        comment="HonkSeer class maps to Honk Seer / Honk Worshipper C.",
    ),
    ActorVisualRule(
        type_str="TheGreatHonk",
        source_file="MONSTERS.TXT",
        source_row="262",
        comment="TheGreatHonk class maps to The Great Honk / God's Pet.",
    ),
    ActorVisualRule(
        type_str="SuccEbora",
        script_name=r"scripts\eborabath.scr",
        source_file="MONSTERS.TXT",
        source_row="301",
        comment=(
            "Ebora bath script uses the Ebora actor-table row even when the "
            "DAT class/name carries a placeholder appearance."
        ),
    ),
    ActorVisualRule(
        type_str="Concubine",
        script_name=r"scripts\eboraconcubine.scr",
        source_file="MONSTERS.TXT",
        source_row="302",
        comment=(
            "Ebora concubine script uses the Siren-skinned Ebora row for a "
            "deterministic editor preview."
        ),
    ),
    ActorVisualRule(
        type_str="LizardOrc",
        object_name_prefix="LoMMOrc",
        source_file="MONSTERS.TXT",
        source_row="304",
        editor_preview_only=True,
        comment=(
            "LoMM Orc appended-row variant is an editor preview mapping only; "
            "stock MM9 LizardOrc runtime does not select row 304."
        ),
        fallback_model="models\\OrcMM9.abc",
        fallback_skins=("skins\\Orc.dtx",),
        fallback_monster_name="LoMM Orc",
        fallback_type_picture="LoMM Orc",
    ),
    ActorVisualRule(
        type_str="LizardOrcMage",
        object_name_prefix="LoMMOrc",
        source_file="MONSTERS.TXT",
        source_row="304",
        editor_preview_only=True,
        comment=(
            "LoMM Orc mage-class appended-row experiment is editor-preview-only; "
            "the stock LizardOrcMage runtime selects row 191."
        ),
        fallback_model="models\\OrcMM9.abc",
        fallback_skins=("skins\\Orc.dtx",),
        fallback_monster_name="LoMM Orc",
        fallback_type_picture="LoMM Orc",
    ),
)

_ACTOR_VISUAL_QUIRKS = _ACTOR_VISUAL_RULES


@dataclass(frozen=True)
class ActorVisual:
    key: str
    model: str
    skins: Tuple[str, ...]
    accessory_skins: Tuple[str, ...]
    source_file: str
    number: str
    monster_name: str
    type_picture: str
    quirk: str = ""
    editor_preview_only: bool = False

    def to_json(self) -> Dict[str, object]:
        return {
            "model": self.model,
            "skins": list(self.skins),
            "accessory_skins": list(self.accessory_skins),
            "source_file": self.source_file,
            "number": self.number,
            "monster_name": self.monster_name,
            "type_picture": self.type_picture,
            "quirk": self.quirk,
            "editor_preview_only": self.editor_preview_only,
        }

    @property
    def all_skins(self) -> Tuple[str, ...]:
        return self.skins + tuple(
            skin for skin in self.accessory_skins if skin not in self.skins
        )

    @classmethod
    def from_json(cls, key: str, data: Dict[str, object]) -> "ActorVisual":
        accessory = data.get("accessory_skins")
        if accessory is None:
            accessory = data.get("secondary_skins", [])
        skins = data.get("skins") or []
        accessory = accessory or []
        return cls(
            key=key,
            model=str(data.get("model", "") or ""),
            skins=tuple(str(s) for s in skins if s),
            accessory_skins=tuple(str(s) for s in accessory if s),
            source_file=str(data.get("source_file", "") or ""),
            number=str(data.get("number", "") or ""),
            monster_name=str(data.get("monster_name", "") or ""),
            type_picture=str(data.get("type_picture", "") or ""),
            quirk=str(data.get("quirk", "") or ""),
            editor_preview_only=bool(data.get("editor_preview_only", False)),
        )


def _token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _row_lookup_key(source_file: str, source_row: str) -> str:
    return f"row{_token(source_file)}{_token(source_row)}"


def _script_key(script_name: str) -> str:
    value = str(script_name or "").replace("/", "\\").strip().strip('"').lower()
    if value.startswith("scripts\\"):
        value = value[len("scripts\\"):]
    if value.endswith(".scr"):
        value = value[:-4]
    return value


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


def object_actor_keys(
    type_str: str,
    object_name: str = "",
    script_name: str = "",
    visual_rules: Optional[Iterable[object]] = None,
) -> List[str]:
    out: List[str] = []
    seen = set()

    def add(key: str) -> None:
        key = _token(key)
        if key and key not in seen:
            out.append(key)
            seen.add(key)

    for key in _quirk_visual_keys(type_str, object_name, script_name, visual_rules):
        add(key)

    for raw in (type_str, _strip_instance_suffix(object_name)):
        add(raw)

    return out


def resolve_actor_visual(
    visual_index: Optional[Dict[str, object]],
    type_str: str,
    object_name: str = "",
    script_name: str = "",
    visual_rules: Optional[Iterable[object]] = None,
) -> Optional[ActorVisual]:
    visual_index = visual_index or {}

    for quirk in _matching_quirks(type_str, object_name, script_name, visual_rules):
        visual = _visual_from_index(visual_index, quirk.visual_key)
        if visual is not None:
            return _apply_visual_rule(visual, quirk)
        if quirk.fallback_model:
            return ActorVisual(
                key=quirk.visual_key,
                model=quirk.fallback_model,
                skins=quirk.fallback_skins,
                accessory_skins=quirk.fallback_accessory_skins,
                source_file=quirk.source_file,
                number=quirk.source_row,
                monster_name=(
                    quirk.fallback_monster_name or quirk.lookup_key
                ),
                type_picture=(
                    quirk.fallback_type_picture or quirk.lookup_key
                ),
                quirk=(
                    _visual_rule_note(quirk)
                ),
                editor_preview_only=quirk.editor_preview_only,
            )

    for key in object_actor_keys(type_str, object_name, script_name, visual_rules):
        visual = _visual_from_index(visual_index, key)
        if visual is not None:
            return visual

    return None


def _apply_visual_rule(visual: ActorVisual, rule: ActorVisualRule) -> ActorVisual:
    return replace(
        visual,
        key=_token(rule.visual_key),
        quirk=_visual_rule_note(rule),
        editor_preview_only=rule.editor_preview_only,
    )


def _visual_rule_note(rule: ActorVisualRule) -> str:
    note = f"{rule.source_file}:{rule.source_row}: {rule.comment}"
    if rule.script_name:
        note = f"{note} ScriptName={rule.script_name}"
    if rule.editor_preview_only:
        note = f"{note} [editor-preview-only]"
    return note


def _visual_from_index(
    visual_index: Dict[str, object],
    key: str,
) -> Optional[ActorVisual]:
    visual = visual_index.get(_token(key))

    if isinstance(visual, ActorVisual):
        return visual

    if isinstance(visual, dict):
        return ActorVisual.from_json(_token(key), visual)

    return None


def _matching_quirks(
    type_str: str,
    object_name: str,
    script_name: str = "",
    visual_rules: Optional[Iterable[object]] = None,
) -> List[ActorVisualQuirk]:
    return [
        quirk
        for quirk in _all_visual_rules(visual_rules)
        if quirk.matches(type_str, object_name, script_name)
    ]

def _quirk_visual_keys(
    type_str: str,
    object_name: str,
    script_name: str = "",
    visual_rules: Optional[Iterable[object]] = None,
) -> List[str]:
    return [
        quirk.visual_key
        for quirk in _matching_quirks(
            type_str, object_name, script_name, visual_rules)
    ]


def _all_visual_rules(
    extra_rules: Optional[Iterable[object]] = None,
) -> Tuple[ActorVisualRule, ...]:
    if not extra_rules:
        return _ACTOR_VISUAL_RULES
    return _ACTOR_VISUAL_RULES + tuple(
        _coerce_visual_rule(rule) for rule in extra_rules
    )


def _coerce_visual_rule(rule: object) -> ActorVisualRule:
    if isinstance(rule, ActorVisualRule):
        return rule
    if not isinstance(rule, dict):
        raise TypeError(f"unsupported actor visual rule: {type(rule).__name__}")
    allowed = {
        "source_file",
        "source_row",
        "comment",
        "lookup_key",
        "type_str",
        "object_name",
        "object_name_prefix",
        "script_name",
        "editor_preview_only",
        "fallback_model",
        "fallback_skins",
        "fallback_accessory_skins",
        "fallback_monster_name",
        "fallback_type_picture",
    }
    data: Dict[str, Any] = {
        key: value for key, value in rule.items() if key in allowed
    }
    for tuple_key in ("fallback_skins", "fallback_accessory_skins"):
        if tuple_key in data:
            data[tuple_key] = tuple(str(item) for item in (data[tuple_key] or ()))
    return ActorVisualRule(**data)


def parse_actor_visual_tables(
    tables: Iterable[Tuple[str, str]],
    visual_rules: Optional[Iterable[object]] = None,
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
            primary_skin = _skin_path(row.get("SkinName", ""))
            skins = (primary_skin,) if primary_skin else ()
            accessory_skins = tuple(
                skin for skin in (
                    _skin_path(row.get("SkinName2", "")),
                    _skin_path(row.get("SkinName3", "")),
                )
                if skin
            )
            monster_name = str(row.get("Monster Name", "") or "").strip()
            type_picture = str(row.get("Type/Picture", "") or "").strip()
            row_number = str(row.get("Number", "") or "").strip()
            keys = [_row_lookup_key(source_file, row_number)]
            keys.extend(_row_keys(monster_name, type_picture))

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
                    accessory_skins=accessory_skins,
                    source_file=source_file,
                    number=row_number,
                    monster_name=monster_name,
                    type_picture=type_picture,
                    quirk=_quirk_for_key_from_rules(key, visual_rules),
                )

        merged.update(local)

    return merged


def _quirk_for_key_from_rules(
    key: str,
    visual_rules: Optional[Iterable[object]] = None,
) -> str:
    token_key = _token(key)
    for quirk in _all_visual_rules(visual_rules):
        if _token(quirk.visual_key) == token_key:
            return _visual_rule_note(quirk)
    return ""


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
    from core.rezmgr import RezReader

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
