#!/usr/bin/env python3
"""
lomm_to_mm9.py
==============

Convert a Legends of Might and Magic .DAT world file into a Might and
Magic IX-compatible .DAT.

Both games run on the same LithTech engine family and share the v66
DAT container format, but they register different sets of WorldObject
classes and the shared classes have slightly different property sets.
This script reads a YAML config (default: ``conversion/lomm_to_mm9.yaml``)
describing the conversion rules and applies them in order:

    1.  Classify actors from the LoMM and MM9 object.lto registries and apply
        the selected actor policy. Unsupported actors are preserved by
        default; historical nearest-MM9 substitutions are opt-in.
    2.  ``convert_class`` rules clone templates for non-actor conversions such
        as ``TreasureChest`` and ``Brazier``/``Fire``.
    3.  Drop unsupported non-actor WorldObjects while retaining MM9 world
        helper classes and preserved actors.
    4.  ``patch_class`` rules add missing properties to shared classes
        (e.g. ``StartPoint.MovePlayerToFloor = 1``,
        ``WorldProperties.CanSaveGame = 1``).
    5.  Audit model, skin, and sound references for staged asset copying.

See ``conversion/lomm_to_mm9.yaml`` for the default rules and inline schema docs.

Usage
-----

    python lomm_to_mm9.py --mm9_root "C:\\Games\\Might and Magic IX" \\
        --lomm_root "C:\\Games\\Legends of Might and Magic" \\
        --level_to_convert CHATEAUESCAPE \\
        --converted_level_name CHATEAUESCAPE_MM9
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# Local imports (implementation lives under conversion/; project root contains
# mm9_rezmgr.py, mm9_patcher/, and default data folders).
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from mm9_patcher.mm9_patch import (  # type: ignore  # noqa: E402
    Header,
    HEADER_SIZE,
    Property,
    World,
    WorldObject,
    serialize_objects,
)
from core.rezmgr import RezReader  # type: ignore  # noqa: E402
from conversion.runtime_registry import (  # type: ignore  # noqa: E402
    RuntimeClassRegistry,
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_CATALOG = os.path.join(PROJECT_ROOT, "catalog", "data", "catalog.json")
DEFAULT_LOMM_CATALOG = os.path.join(
    PROJECT_ROOT, "catalog", "data", "catalog_lomm.json",
)
DEFAULT_CONFIG = os.path.join(HERE, "lomm_to_mm9.yaml")

ACTOR_POLICY_PRESERVE = "preserve"
ACTOR_POLICY_LEGACY = "legacy"
ACTOR_POLICY_REMOVE = "remove"
ACTOR_POLICIES = (
    ACTOR_POLICY_PRESERVE,
    ACTOR_POLICY_LEGACY,
    ACTOR_POLICY_REMOVE,
)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config(path: str) -> Dict[str, Any]:
    """Load YAML if PyYAML is available, otherwise fall back to JSON."""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    except ImportError:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "PyYAML is not installed and the config file is not valid "
                f"JSON either: {exc}"
            ) from exc


@dataclass
class _PropSpec:
    """Property to add by an add_props rule."""
    code: int
    value: Any
    flags: int = 0


@dataclass
class _PatchRule:
    add_props: Dict[str, _PropSpec] = field(default_factory=dict)


@dataclass
class _ConvertRule:
    template: str                                   # "WORLDS/X.DAT::ObjectName"
    preserve: Tuple[str, ...] = ()
    new_type: Optional[str] = None
    overrides: Dict[str, Any] = field(default_factory=dict)
    add_props: Dict[str, _PropSpec] = field(default_factory=dict)


@dataclass
class _Config:
    remove_unknown_classes: bool
    extra_remove_classes: Set[str]
    keep_classes: Set[str]
    patch_class: Dict[str, _PatchRule]
    convert_class: Dict[str, _ConvertRule]
    actor_policy: str = ACTOR_POLICY_PRESERVE


def _parse_propspec_dict(
    raw: Dict[str, Any],
    where: str,
) -> Dict[str, _PropSpec]:
    out: Dict[str, _PropSpec] = {}
    for pname, spec in (raw or {}).items():
        if not isinstance(spec, dict):
            raise ValueError(
                f"{where}.{pname} must be a mapping with 'code' and "
                f"'value' (got {spec!r})"
            )
        if "code" not in spec or "value" not in spec:
            raise ValueError(
                f"{where}.{pname} requires 'code' and 'value' "
                f"(got {spec!r})"
            )
        out[pname] = _PropSpec(
            code=int(spec["code"]),
            value=spec["value"],
            flags=int(spec.get("flags", 0)),
        )
    return out


def _parse_convert_rules(
    raw: Dict[str, Any],
    where: str,
) -> Dict[str, _ConvertRule]:
    rules: Dict[str, _ConvertRule] = {}
    for cls, rule_raw in (raw or {}).items():
        rule_raw = rule_raw or {}
        if "template" not in rule_raw:
            raise ValueError(
                f"{where}.{cls} requires 'template' "
                f"(WORLDS/X.DAT::ObjectName)"
            )
        rules[cls] = _ConvertRule(
            template=str(rule_raw["template"]),
            preserve=tuple(rule_raw.get("preserve") or ()),
            new_type=rule_raw.get("new_type"),
            overrides=dict(rule_raw.get("overrides") or {}),
            add_props=_parse_propspec_dict(
                rule_raw.get("add_props") or {},
                where=f"{where}.{cls}.add_props",
            ),
        )
    return rules


def _parse_config(raw: Dict[str, Any]) -> _Config:
    remove_unknown = bool(raw.get("remove_unknown_classes", True))
    extra_remove = set(raw.get("extra_remove_classes") or [])
    keep_classes = set(raw.get("keep_classes") or [])

    patch_class: Dict[str, _PatchRule] = {}
    for cls, rule_raw in (raw.get("patch_class") or {}).items():
        rule_raw = rule_raw or {}
        patch_class[cls] = _PatchRule(
            add_props=_parse_propspec_dict(
                rule_raw.get("add_props") or {},
                where=f"patch_class.{cls}.add_props",
            ),
        )

    convert_class = _parse_convert_rules(
        raw.get("convert_class") or {}, where="convert_class",
    )

    actor_policy = str(
        raw.get("actor_policy", ACTOR_POLICY_PRESERVE)
    ).strip().lower()
    if actor_policy not in ACTOR_POLICIES:
        raise ValueError(
            f"actor_policy must be one of {', '.join(ACTOR_POLICIES)} "
            f"(got {actor_policy!r})"
        )

    return _Config(
        remove_unknown_classes=remove_unknown,
        extra_remove_classes=extra_remove,
        keep_classes=keep_classes,
        patch_class=patch_class,
        convert_class=convert_class,
        actor_policy=actor_policy,
    )


# ---------------------------------------------------------------------------
# REZ helpers
# ---------------------------------------------------------------------------

class _Mm9Catalog:
    """Class registry + lazy MM9 WORLDS.REZ template loader."""

    def __init__(
        self,
        rez_path: str,
        catalog_json: Optional[str] = None,
    ) -> None:
        if not os.path.isfile(rez_path):
            raise FileNotFoundError(
                f"MM9 WORLDS.REZ not found: {rez_path!r}"
            )
        self.rez_path = rez_path
        self._reader = RezReader(rez_path).open()
        self._level_cache: Dict[str, World] = {}
        self._classes: Optional[Set[str]] = None
        self._catalog_json = (
            catalog_json if catalog_json and os.path.isfile(catalog_json) else None
        )

    def catalog_source(self) -> str:
        if self._catalog_json:
            return f"catalog.json ({self._catalog_json})"
        return f"WORLDS.REZ scan ({self.rez_path})"

    def list_levels(self) -> List[str]:
        return sorted(
            p for p in self._reader.list_paths()
            if not p.upper().endswith(".ED")
        )

    def load_level(self, virtual_path: str) -> World:
        if virtual_path in self._level_cache:
            return self._level_cache[virtual_path]
        # MM9 WORLDS.REZ entries are inconsistent: some carry a ".DAT"
        # extension (used for duplicate-name disambiguation) and some
        # do not.  Accept either form transparently.
        try:
            data = self._reader.extract_to_bytes(virtual_path)
        except Exception:
            alt = (
                virtual_path[:-4]
                if virtual_path.upper().endswith(".DAT")
                else virtual_path + ".DAT"
            )
            data = self._reader.extract_to_bytes(alt)
        fd, tmp = tempfile.mkstemp(suffix=".DAT", prefix="mm9_tpl_")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            world = World.load(tmp)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        self._level_cache[virtual_path] = world
        return world

    def class_names(self, exclude_basename: Optional[str] = None) -> Set[str]:
        if self._classes is not None:
            return self._classes
        if self._catalog_json:
            with open(self._catalog_json, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            classes_raw = data.get("classes") or {}
            if not classes_raw:
                raise RuntimeError(
                    f"{self._catalog_json} has no 'classes' section"
                )
            self._classes = set(classes_raw.keys())
            return self._classes

        names: Set[str] = set()
        skip_upper = exclude_basename.upper() if exclude_basename else None
        for path in self.list_levels():
            entry_basename = path.split("/")[-1].upper()
            entry_stem = entry_basename
            if entry_stem.endswith(".DAT"):
                entry_stem = entry_stem[:-4]
            if skip_upper and (
                entry_basename == skip_upper or entry_stem == skip_upper
            ):
                continue
            try:
                world = self.load_level(path)
            except Exception:
                continue
            for obj in world.objects:
                names.add(obj.type_str)
        self._classes = names
        return names


def _parse_template_spec(spec: str) -> Tuple[str, str]:
    if "::" not in spec:
        raise ValueError(
            f"template spec must look like 'WORLDS/X.DAT::ObjectName' "
            f"(got {spec!r})"
        )
    level, name = spec.split("::", 1)
    return level, name


def _find_named(world: World, class_name: str, name: str) -> WorldObject:
    for obj in world.objects:
        if obj.type_str == class_name and obj.get("Name") == name:
            return obj
    raise LookupError(
        f"No {class_name} named {name!r} in this template level"
    )


# ---------------------------------------------------------------------------
# Property helpers
# ---------------------------------------------------------------------------

def _has_prop(obj: WorldObject, name: str) -> bool:
    return any(p.name == name for p in obj.props)


def _get_prop(obj: WorldObject, name: str) -> Optional[Property]:
    for p in obj.props:
        if p.name == name:
            return p
    return None


def _ensure_prop(
    obj: WorldObject,
    name: str,
    code: int,
    flags: int,
    value: Any,
) -> bool:
    if _has_prop(obj, name):
        return False
    obj.props.append(Property(
        name=name,
        code=code,
        flags=flags,
        value=value,
        orig_dlen=None,
        dirty=True,
    ))
    return True


def _clone_prop(src: Property) -> Property:
    return Property(
        name=src.name,
        code=src.code,
        flags=src.flags,
        value=copy.deepcopy(src.value),
        orig_dlen=None,
        dirty=True,
    )


# ---------------------------------------------------------------------------
# Conversion stages
# ---------------------------------------------------------------------------

@dataclass
class AssetAudit:
    in_mm9: List[str] = field(default_factory=list)
    in_lomm_only: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)


@dataclass
class ConversionStats:
    removed_by_class: Dict[str, int]
    patched_by_class: Dict[str, int]
    converted_by_class: Dict[str, Tuple[str, int]]
    audit_models: AssetAudit
    audit_skins: AssetAudit
    audit_sounds: AssetAudit
    compatibility: "CompatibilityReport"
    implicit_skin_refs: List["ImplicitSkinReference"] = field(default_factory=list)
    preview_actor_visuals: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class ImplicitSkinReference:
    model: str
    skin: str
    object_class: str
    object_name: str
    variant_name: str
    source_keys: Tuple[str, ...]
    catalog_source: str
    dat_model: str = ""
    preview_model_override: bool = False


@dataclass(frozen=True)
class CompatibilityRecord:
    source_index: int
    object_name: str
    source_class: str
    source_position: Tuple[float, ...]
    output_index: int
    status: str
    action: str
    target_class: str = ""
    reason: str = ""


@dataclass
class CompatibilityReport:
    actor_policy: str
    source_registry: str
    target_registry: str
    records: List[CompatibilityRecord] = field(default_factory=list)
    registry_warnings: List[str] = field(default_factory=list)

    @property
    def status_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for record in self.records:
            counts[record.status] = counts.get(record.status, 0) + 1
        return counts

    @property
    def unresolved_actor_count(self) -> int:
        return sum(
            1 for record in self.records
            if record.status == "unsupported_actor_preserved"
        )

    @property
    def unresolved_actor_classes(self) -> List[str]:
        return sorted({
            record.source_class for record in self.records
            if record.status == "unsupported_actor_preserved"
        })


_ACTOR_SIGNATURE_PROPS = frozenset({
    "SightDistance",
    "HearingDistance",
    "TeamNbr",
    "CanReachAll",
    "CheckForAIBarrier",
})


def _looks_like_actor(obj: WorldObject, registry: RuntimeClassRegistry) -> bool:
    if registry.is_actor(obj.type_str):
        return True
    prop_names = {prop.name for prop in obj.props}
    return len(prop_names & _ACTOR_SIGNATURE_PROPS) >= 2


def _position_tuple(obj: WorldObject) -> Tuple[float, ...]:
    value = obj.get("Pos")
    if not isinstance(value, (tuple, list)):
        return ()
    try:
        return tuple(float(part) for part in value)
    except (TypeError, ValueError):
        return ()


def _actor_compatibility(
    world: World,
    config: _Config,
    actor_policy: str,
    source_registry: RuntimeClassRegistry,
    target_registry: RuntimeClassRegistry,
) -> CompatibilityReport:
    report = CompatibilityReport(
        actor_policy=actor_policy,
        source_registry=source_registry.source,
        target_registry=target_registry.source,
        registry_warnings=list(source_registry.warnings + target_registry.warnings),
    )
    for index, obj in enumerate(world.objects):
        if not _looks_like_actor(obj, source_registry):
            continue
        cls = obj.type_str
        target_available = target_registry.contains(cls) or cls in config.keep_classes
        legacy_rule = config.convert_class.get(cls)
        if actor_policy == ACTOR_POLICY_LEGACY and legacy_rule is not None:
            target = legacy_rule.new_type or cls
            status = "legacy_actor_substitution"
            action = "replace"
            reason = "Legacy compatibility rule selected explicitly."
        elif target_available:
            target = cls
            status = "same_name_mm9_implementation"
            action = "preserve"
            reason = (
                "MM9 registers this class name; its MM9 implementation may "
                "differ from LoMM."
            )
        elif actor_policy == ACTOR_POLICY_REMOVE:
            target = ""
            status = "unsupported_actor_removed"
            action = "remove"
            reason = "The class is not registered by MM9 and removal was selected."
        else:
            target = ""
            status = "unsupported_actor_preserved"
            action = "preserve"
            reason = (
                "The class is not registered by MM9; it remains editable but "
                "will not appear in the MM9 runtime until removed or replaced."
            )
        report.records.append(CompatibilityRecord(
            source_index=index,
            object_name=str(obj.get("Name") or ""),
            source_class=cls,
            source_position=_position_tuple(obj),
            output_index=-1,
            status=status,
            action=action,
            target_class=target,
            reason=reason,
        ))
    return report


def _convert_with_template(
    src: WorldObject,
    template: WorldObject,
    preserve_fields: Iterable[str],
    new_type: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
    add_props: Optional[Dict[str, _PropSpec]] = None,
) -> WorldObject:
    """Clone `template`, replay `preserve_fields` from `src`, then apply
    `overrides` (auto-detect code from existing template prop) and
    `add_props` (with explicit code).  Result type is `new_type`
    (defaults to `template.type_str`)."""
    out = WorldObject(
        type_str=new_type or template.type_str,
        props=[_clone_prop(p) for p in template.props],
    )
    by_name = {p.name: p for p in out.props}

    # 1) Preserve from source
    for fld in preserve_fields:
        sp = _get_prop(src, fld)
        if sp is None:
            continue
        if fld in by_name:
            tp = by_name[fld]
            tp.value = copy.deepcopy(sp.value)
            tp.dirty = True
        else:
            new_p = _clone_prop(sp)
            out.props.append(new_p)
            by_name[fld] = new_p

    # 2) Apply overrides (auto-detect code from template)
    for fld, value in (overrides or {}).items():
        if fld in by_name:
            tp = by_name[fld]
            tp.value = copy.deepcopy(value)
            tp.dirty = True
        else:
            # Override field doesn't exist on template; assume string
            # (the common case for Filename/Skin) and add it.
            new_p = Property(
                name=fld, code=0, flags=0,
                value=copy.deepcopy(value),
                orig_dlen=None, dirty=True,
            )
            out.props.append(new_p)
            by_name[fld] = new_p

    # 3) Add new props with explicit codes
    for fld, spec in (add_props or {}).items():
        if fld in by_name:
            # Already present (possibly from the template clone or an
            # earlier override).  Update the value to keep behaviour
            # predictable.
            tp = by_name[fld]
            tp.value = copy.deepcopy(spec.value)
            tp.code = spec.code
            tp.flags = spec.flags
            tp.dirty = True
        else:
            new_p = Property(
                name=fld, code=spec.code, flags=spec.flags,
                value=copy.deepcopy(spec.value),
                orig_dlen=None, dirty=True,
            )
            out.props.append(new_p)
            by_name[fld] = new_p

    return out


def _apply_convert_rules(
    world: World,
    rules: Dict[str, _ConvertRule],
    catalog: _Mm9Catalog,
) -> Dict[str, Tuple[str, int]]:
    """Replace every world object whose class is a key in `rules`
    with a clone of the rule's template.  Returns
    {src_class: (new_class, count)}."""
    if not rules:
        return {}

    resolved: Dict[str, Tuple[_ConvertRule, WorldObject]] = {}
    for cls, rule in rules.items():
        level, name = _parse_template_spec(rule.template)
        target_class = rule.new_type or cls
        try:
            template = _find_named(
                catalog.load_level(level), target_class, name,
            )
        except (LookupError, KeyError) as exc:
            raise LookupError(
                f"convert rule for {cls!r}: {exc}"
            ) from exc
        resolved[cls] = (rule, template)

    counts: Dict[str, Tuple[str, int]] = {}
    for i, obj in enumerate(world.objects):
        if obj.type_str not in resolved:
            continue
        rule, template = resolved[obj.type_str]
        new_type = rule.new_type
        world.objects[i] = _convert_with_template(
            src=obj,
            template=template,
            preserve_fields=rule.preserve,
            new_type=new_type,
            overrides=rule.overrides,
            add_props=rule.add_props,
        )
        out_cls = new_type or obj.type_str
        prev = counts.get(obj.type_str, (out_cls, 0))
        counts[obj.type_str] = (out_cls, prev[1] + 1)
    return counts


def _stage_drop_classes(
    world: World,
    mm9_classes: Set[str],
    config: _Config,
    protected_actor_classes: Set[str],
) -> Dict[str, int]:
    keep = config.keep_classes
    extra = config.extra_remove_classes
    drop_unknown = config.remove_unknown_classes

    kept: List[WorldObject] = []
    removed: Dict[str, int] = {}
    for obj in world.objects:
        cls = obj.type_str
        if cls in keep or cls in protected_actor_classes:
            kept.append(obj)
            continue
        is_unknown = cls not in mm9_classes
        if cls in extra or (drop_unknown and is_unknown):
            removed[cls] = removed.get(cls, 0) + 1
        else:
            kept.append(obj)
    world.objects = kept
    return removed


def _stage_patch_classes(
    world: World,
    rules: Dict[str, _PatchRule],
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for obj in world.objects:
        rule = rules.get(obj.type_str)
        if rule is None:
            continue
        added = 0
        for pname, spec in rule.add_props.items():
            if _ensure_prop(obj, pname, spec.code, spec.flags, spec.value):
                added += 1
        if added > 0:
            counts[obj.type_str] = counts.get(obj.type_str, 0) + added
    return counts


# ---------------------------------------------------------------------------
# Asset audit
# ---------------------------------------------------------------------------

def _normalize_asset_path(path: str) -> Tuple[str, str]:
    """Return (stem_lower, ext_lower) for asset path comparison.
    Strips the ``models/``, ``skins/``, or ``sounds/`` prefix and the file extension."""
    p = (path or "").replace("\\", "/").lower().lstrip("/")
    parts = p.split("/")
    if parts and parts[0] in ("models", "skins", "sounds"):
        parts = parts[1:]
    rejoined = "/".join(parts)
    stem, dot, ext = rejoined.rpartition(".")
    if not dot:
        return rejoined, ""
    return stem, ext


def _build_rez_asset_index(rez_path: Optional[str]) -> Set[str]:
    """Return a set of normalized stems present in the given REZ
    archive (case- and extension-insensitive lookup)."""
    if not rez_path or not os.path.isfile(rez_path):
        return set()
    out: Set[str] = set()
    try:
        with RezReader(rez_path) as reader:
            for p in reader.list_paths():
                stem, _ = _normalize_asset_path(p)
                out.add(stem)
    except Exception:
        pass
    return out


def _build_loose_asset_index(folder: Optional[str], subdir: str) -> Set[str]:
    """Return a set of normalized stems for loose files inside
    `folder/<subdir case-insensitive>`."""
    if not folder or not os.path.isdir(folder):
        return set()
    target_root = None
    for entry in os.listdir(folder):
        if entry.lower() == subdir.lower():
            target_root = os.path.join(folder, entry)
            break
    if target_root is None or not os.path.isdir(target_root):
        return set()
    out: Set[str] = set()
    for root, _dirs, files in os.walk(target_root):
        rel_root = os.path.relpath(root, target_root).replace("\\", "/")
        if rel_root == ".":
            rel_root = ""
        for fn in files:
            stem, _, _ = fn.lower().rpartition(".")
            if not stem:
                continue
            if rel_root:
                full_stem = f"{rel_root.lower()}/{stem}"
            else:
                full_stem = stem
            out.add(full_stem)
    return out


def _classify_asset_refs(
    refs: Set[str],
    mm9_index: Set[str],
    lomm_index: Set[str],
 ) -> AssetAudit:
    audit = AssetAudit()
    for ref in sorted(refs):
        stem, _ = _normalize_asset_path(ref)
        if not stem:
            continue
        if stem in mm9_index:
            audit.in_mm9.append(ref)
        elif stem in lomm_index:
            audit.in_lomm_only.append(ref)
        else:
            audit.missing.append(ref)
    return audit


def _catalog_asset_path(value: object, root: str, extensions: Tuple[str, ...]) -> str:
    path = str(value or "").strip().strip('"').replace("/", "\\").lstrip("\\")
    if not path.casefold().endswith(tuple(ext.casefold() for ext in extensions)):
        return ""
    if not path.casefold().startswith(root.casefold() + "\\"):
        path = root + "\\" + path
    return path.casefold()


def _variant_match_token(value: object) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _catalog_variant_score(row: Dict[str, Any], wanted: Set[str]) -> int:
    tokens = {_variant_match_token(row.get("name"))}
    for source_key in row.get("source_keys") or ():
        source = str(source_key)
        tokens.add(_variant_match_token(source.split(":")[-1]))
        if source.casefold().startswith("asset-name:"):
            tokens.add(_variant_match_token(os.path.splitext(os.path.basename(source))[0]))
    tokens.discard("")
    if tokens & wanted:
        return 30
    if any(token.startswith(target) for token in tokens for target in wanted):
        return 20
    return 0


def _catalog_class_entry(catalog: Dict[str, Any], class_name: str) -> Dict[str, Any]:
    for name, entry in (catalog.get("classes") or {}).items():
        if str(name).casefold() == str(class_name).casefold() and isinstance(entry, dict):
            return entry
    return {}


def _implicit_catalog_skin_refs(
    world: World,
    lomm_catalog_json: Optional[str],
) -> Tuple[List[ImplicitSkinReference], Dict[str, Dict[str, Any]]]:
    """Resolve catalog skins and editor-only class model fallbacks."""
    if not lomm_catalog_json or not os.path.isfile(lomm_catalog_json):
        return [], {}
    try:
        with open(lomm_catalog_json, "r", encoding="utf-8") as fh:
            catalog = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid LoMM catalog {lomm_catalog_json!r}: {exc}") from exc
    raw_variants = catalog.get("model_variants")
    if not isinstance(raw_variants, dict):
        raise ValueError(
            f"invalid LoMM catalog {lomm_catalog_json!r}: model_variants is missing"
        )
    variants = {
        key: rows
        for raw_key, rows in raw_variants.items()
        if (
            key := _catalog_asset_path(raw_key, "models", (".abc", ".lta", ".ltb"))
        )
        and isinstance(rows, list)
    }
    model_resources = {
        path
        for raw_path in (catalog.get("model_resources") or ())
        if (path := _catalog_asset_path(
            raw_path, "models", (".abc", ".lta", ".ltb")
        ))
    }
    model_resources.update(variants)

    resolved: List[ImplicitSkinReference] = []
    preview_actor_visuals: Dict[str, Dict[str, Any]] = {}
    seen = set()
    for obj in world.objects:
        explicit_skins: List[str] = []
        for prop_name in ("Skin", "SkinName", "Skin2", "Skin3"):
            value = obj.get(prop_name)
            for piece in str(value or "").split(";"):
                skin = _catalog_asset_path(piece, "skins", (".dtx",))
                if skin and skin not in explicit_skins:
                    explicit_skins.append(skin)

        dat_model = _catalog_asset_path(
            obj.get("Filename"), "models", (".abc", ".lta", ".ltb")
        )
        if not dat_model:
            continue

        wanted = {
            token
            for token in (
                _variant_match_token(getattr(obj, "type_str", "")),
                _variant_match_token(obj.get("Name")),
            )
            if token
        }
        model = dat_model
        rows = [row for row in variants.get(model, ()) if isinstance(row, dict)]
        scored = [(_catalog_variant_score(row, wanted), row) for row in rows]

        class_name = str(getattr(obj, "type_str", "") or "")
        class_entry = _catalog_class_entry(catalog, class_name)
        hierarchy = {
            str(value).casefold()
            for value in ((class_entry.get("object_lto") or {}).get("hierarchy") or ())
        }
        preview_model_override = False
        if "actor" in hierarchy:
            class_model = _catalog_asset_path(
                f"models\\{class_name}.abc", "models", (".abc",)
            )
            class_rows = [
                row for row in variants.get(class_model, ()) if isinstance(row, dict)
            ]
            class_scored = [
                (_catalog_variant_score(row, wanted), row) for row in class_rows
            ]
            current_score = max((score for score, _row in scored), default=0)
            class_score = max((score for score, _row in class_scored), default=0)
            if (
                class_model
                and class_model != dat_model
                and class_model in model_resources
                and class_score > current_score
            ):
                model = class_model
                scored = class_scored
                preview_model_override = True

        if not scored:
            selected_rows: List[Dict[str, Any]] = []
        else:
            best_score = max(score for score, _row in scored)
            if best_score:
                selected_rows = [row for score, row in scored if score == best_score]
            elif len(scored) == 1:
                selected_rows = [scored[0][1]]
            else:
                # Multiple appearance variants with no class/name match are
                # ambiguous; explicit DAT skin data is required in that case.
                selected_rows = []

        if preview_model_override and len(selected_rows) > 1:
            # LoMM selects among cosmetic alternatives at runtime. The editor
            # uses one stable appearance instead of treating alternatives as
            # accessory textures for separate ABC pieces.
            selected_rows = [sorted(
                selected_rows,
                key=lambda row: (
                    str(row.get("name") or "").casefold(),
                    tuple(str(value).casefold() for value in (row.get("skins") or ())),
                ),
            )[0]]

        selected_skins = list(explicit_skins)
        if not selected_skins:
            for row in selected_rows:
                for skin_value in row.get("skins") or ():
                    skin = _catalog_asset_path(skin_value, "skins", (".dtx",))
                    if skin and skin not in selected_skins:
                        selected_skins.append(skin)

        if preview_model_override:
            preview_key = _variant_match_token(class_name)
            preview_actor_visuals[preview_key] = {
                "model": model,
                "skins": selected_skins[:1],
                "accessory_skins": selected_skins[1:],
                "source_file": os.path.abspath(lomm_catalog_json),
                "number": "",
                "monster_name": class_name,
                "type_picture": class_name,
                "quirk": (
                    f"LoMM class resource overrides DAT fallback {dat_model} "
                    "for editor preview"
                ),
                "editor_preview_only": True,
            }

        if explicit_skins:
            continue

        for row in selected_rows:
            for skin_value in row.get("skins") or ():
                skin = _catalog_asset_path(skin_value, "skins", (".dtx",))
                key = (model, skin, str(getattr(obj, "type_str", "")))
                if not skin or key in seen:
                    continue
                seen.add(key)
                resolved.append(ImplicitSkinReference(
                    model=model,
                    skin=skin,
                    object_class=str(getattr(obj, "type_str", "") or ""),
                    object_name=str(obj.get("Name") or ""),
                    variant_name=str(row.get("name") or ""),
                    source_keys=tuple(str(value) for value in (row.get("source_keys") or ())),
                    catalog_source=os.path.abspath(lomm_catalog_json),
                    dat_model=dat_model,
                    preview_model_override=preview_model_override,
                ))
    return resolved, preview_actor_visuals


def _stage_audit_assets(
    world: World,
    mm9_models_rez: Optional[str],
    mm9_skins_rez: Optional[str],
    mm9_sounds_rez: Optional[str],
    lomm_data_dir: Optional[str],
    lomm_models_rez: Optional[str] = None,
    lomm_skins_rez: Optional[str] = None,
    lomm_sounds_rez: Optional[str] = None,
    lomm_catalog_json: Optional[str] = None,
) -> Tuple[
    AssetAudit,
    AssetAudit,
    AssetAudit,
    List[ImplicitSkinReference],
    Dict[str, Dict[str, Any]],
]:
    """Walk `world.objects`, gather all Filename, Skin, and Sound references,
    and return (model_audit, skin_audit, sound_audit)."""
    model_refs: Set[str] = set()
    skin_refs: Set[str] = set()
    sound_refs: Set[str] = set()
    # Filename is overloaded across classes (AmbientSound stores a .wav
    # path in it, for example).  Restrict each audit bucket to its own
    # known extensions so the punch list stays meaningful.
    MODEL_EXTS = (".abc", ".lta", ".ltb")
    SKIN_EXTS = (".dtx",)
    for obj in world.objects:
        fn = obj.get("Filename")
        sk = obj.get("Skin")
        if isinstance(fn, str) and fn.strip():
            if fn.lower().endswith(MODEL_EXTS):
                model_refs.add(fn)
        if isinstance(sk, str) and sk.strip():
            # Skin can be a semicolon-separated list of dtx files.
            for piece in sk.split(";"):
                piece = piece.strip()
                if piece and piece.lower().endswith(SKIN_EXTS):
                    skin_refs.add(piece)
        for prop in obj.props:
            val = prop.value
            if isinstance(val, str) and val.strip():
                for piece in val.split(";"):
                    piece = piece.strip()
                    if piece.lower().endswith(".wav"):
                        sound_refs.add(piece)

    implicit_skin_refs, preview_actor_visuals = _implicit_catalog_skin_refs(
        world, lomm_catalog_json
    )
    model_refs.update(
        visual["model"] for visual in preview_actor_visuals.values()
        if visual.get("model")
    )
    skin_refs.update(record.skin for record in implicit_skin_refs)

    mm9_models = _build_rez_asset_index(mm9_models_rez)
    mm9_skins = _build_rez_asset_index(mm9_skins_rez)
    mm9_sounds = _build_rez_asset_index(mm9_sounds_rez)
    lomm_models = (
        _build_loose_asset_index(lomm_data_dir, "MODELS")
        | _build_rez_asset_index(lomm_models_rez)
    )
    lomm_skins = (
        _build_loose_asset_index(lomm_data_dir, "SKINS")
        | _build_rez_asset_index(lomm_skins_rez)
    )
    lomm_sounds = (
        _build_loose_asset_index(lomm_data_dir, "SOUNDS")
        | _build_rez_asset_index(lomm_sounds_rez)
    )

    model_audit = _classify_asset_refs(model_refs, mm9_models, lomm_models)
    skin_audit = _classify_asset_refs(skin_refs, mm9_skins, lomm_skins)
    sound_audit = _classify_asset_refs(sound_refs, mm9_sounds, lomm_sounds)
    return (
        model_audit,
        skin_audit,
        sound_audit,
        implicit_skin_refs,
        preview_actor_visuals,
    )


def convert(
    src_world: World,
    catalog: _Mm9Catalog,
    config: _Config,
    input_basename: Optional[str] = None,
    mm9_models_rez: Optional[str] = None,
    mm9_skins_rez: Optional[str] = None,
    mm9_sounds_rez: Optional[str] = None,
    lomm_data_dir: Optional[str] = None,
    lomm_models_rez: Optional[str] = None,
    lomm_skins_rez: Optional[str] = None,
    lomm_sounds_rez: Optional[str] = None,
    source_registry: Optional[RuntimeClassRegistry] = None,
    target_registry: Optional[RuntimeClassRegistry] = None,
    actor_policy: Optional[str] = None,
    lomm_catalog_json: Optional[str] = None,
) -> ConversionStats:
    selected_policy = str(actor_policy or config.actor_policy).strip().lower()
    if selected_policy not in ACTOR_POLICIES:
        raise ValueError(
            f"actor_policy must be one of {', '.join(ACTOR_POLICIES)} "
            f"(got {selected_policy!r})"
        )

    catalog_classes = catalog.class_names(exclude_basename=input_basename)
    if target_registry is None:
        target_registry = RuntimeClassRegistry(
            classes={}, source=catalog.catalog_source(),
        )
        from conversion.runtime_registry import RuntimeClass
        target_registry.classes = {
            name: RuntimeClass(name=name) for name in catalog_classes
        }
    if source_registry is None:
        source_registry = RuntimeClassRegistry(source="source DAT property signatures")

    compatibility = _actor_compatibility(
        src_world,
        config,
        selected_policy,
        source_registry,
        target_registry,
    )
    actor_classes = {record.source_class for record in compatibility.records}
    protected_actor_classes = {
        record.source_class for record in compatibility.records
        if record.action == "preserve"
    }
    remove_actor_indices = {
        record.source_index for record in compatibility.records
        if record.action == "remove"
    }
    if remove_actor_indices:
        src_world.objects = [
            obj for index, obj in enumerate(src_world.objects)
            if index not in remove_actor_indices
        ]

    # 1. Apply non-actor convert rules. Actor substitutions are opt-in through
    #    legacy mode; the default preserves LoMM actors for explicit editing.
    selected_rules = {
        cls: rule for cls, rule in config.convert_class.items()
        if cls not in actor_classes or selected_policy == ACTOR_POLICY_LEGACY
    }
    converted = _apply_convert_rules(
        src_world, selected_rules, catalog,
    )

    # 2-3. Drop and patch.
    # Non-actor editor/world-helper classes may intentionally be marked as not
    # runtime-loadable in object.lto while still being valid DAT content, so
    # retain the visible/scanned catalog layer as well as loadable LTO classes.
    # Actor compatibility above continues to require runtime_loadable=True.
    mm9_classes = catalog_classes | target_registry.class_names
    removed = _stage_drop_classes(
        src_world, mm9_classes, config, protected_actor_classes,
    )
    for record in compatibility.records:
        if record.status == "unsupported_actor_removed":
            removed[record.source_class] = removed.get(record.source_class, 0) + 1
    patched = _stage_patch_classes(src_world, config.patch_class)

    # Resolve preserved records to their converted-DAT baseline indices. The
    # editor uses these stable indices so deleting an incompatible actor clears
    # its install blocker even if the actor's properties are edited later.
    claimed: Set[int] = set()
    resolved_records: List[CompatibilityRecord] = []
    for record in compatibility.records:
        output_index = -1
        if record.action == "preserve":
            for index, obj in enumerate(src_world.objects):
                if index in claimed or obj.type_str != record.source_class:
                    continue
                if str(obj.get("Name") or "") != record.object_name:
                    continue
                if _position_tuple(obj) != record.source_position:
                    continue
                output_index = index
                claimed.add(index)
                break
        resolved_records.append(replace(record, output_index=output_index))
    compatibility.records = resolved_records

    # 4. Asset audit (informational).
    (
        model_audit,
        skin_audit,
        sound_audit,
        implicit_skin_refs,
        preview_actor_visuals,
    ) = _stage_audit_assets(
        src_world,
        mm9_models_rez,
        mm9_skins_rez,
        mm9_sounds_rez,
        lomm_data_dir,
        lomm_models_rez=lomm_models_rez,
        lomm_skins_rez=lomm_skins_rez,
        lomm_sounds_rez=lomm_sounds_rez,
        lomm_catalog_json=lomm_catalog_json,
    )

    return ConversionStats(
        removed_by_class=removed,
        patched_by_class=patched,
        converted_by_class=converted,
        audit_models=model_audit,
        audit_skins=skin_audit,
        audit_sounds=sound_audit,
        compatibility=compatibility,
        implicit_skin_refs=implicit_skin_refs,
        preview_actor_visuals=preview_actor_visuals,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _default_output(input_path: str) -> str:
    p = Path(input_path)
    return str(p.with_name(p.stem + "_mm9" + p.suffix))


def _verify_round_trip(out_path: str) -> bool:
    with open(out_path, "rb") as fh:
        original = fh.read()
    world = World.load(out_path)
    obj_section = serialize_objects(world.objects)
    new_obj_pos = HEADER_SIZE + len(world.pre_objects)
    new_ren_pos = new_obj_pos + len(obj_section)
    hdr = Header(
        world.header.version, new_obj_pos, new_ren_pos, world.header.dummy,
    ).pack()
    rebuilt = hdr + world.pre_objects + obj_section + world.render_data
    return rebuilt == original


def _world_from_bytes(data: bytes) -> World:
    fd, tmp = tempfile.mkstemp(suffix=".DAT", prefix="lomm_cli_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return World.load(tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _print_audit(label: str, audit: AssetAudit) -> None:
    total = len(audit.in_mm9) + len(audit.in_lomm_only) + len(audit.missing)
    print(
        f"  {label:8s} : {len(audit.in_mm9):>3} in MM9, "
        f"{len(audit.in_lomm_only):>3} in LoMM only, "
        f"{len(audit.missing):>3} missing  (total {total})"
    )
    if audit.in_lomm_only:
        print(f"      -- need to be added to MM9 {label}.REZ from LoMM assets:")
        for p in audit.in_lomm_only:
            print(f"         {p}")
    if audit.missing:
        print(f"      -- not found in MM9 or LoMM (manual fix needed):")
        for p in audit.missing:
            print(f"         {p}")


def _print_summary(stats: ConversionStats, world: World) -> None:
    print("== conversion summary ==")
    compat = stats.compatibility
    print(f"  actor policy                  : {compat.actor_policy}")
    print(
        f"  unsupported actors preserved  : "
        f"{compat.unresolved_actor_count}"
    )
    for cls in compat.unresolved_actor_classes:
        count = sum(
            1 for record in compat.records
            if record.status == "unsupported_actor_preserved"
            and record.source_class == cls
        )
        print(f"      {count:>4}  {cls}")
    if stats.removed_by_class:
        total = sum(stats.removed_by_class.values())
        print(f"  unknown-class objects removed : {total}")
        for cls, n in sorted(
            stats.removed_by_class.items(), key=lambda kv: -kv[1],
        ):
            print(f"      {n:>4}  {cls}")
    else:
        print("  unknown-class objects removed : 0 (file already clean)")

    if stats.patched_by_class:
        print("  classes patched               :")
        for cls, n in sorted(
            stats.patched_by_class.items(), key=lambda kv: -kv[1],
        ):
            print(f"      +{n} props on {cls}")
    else:
        print("  classes patched               : (none)")

    if stats.converted_by_class:
        print("  classes converted             :")
        for src_cls, (out_cls, n) in sorted(
            stats.converted_by_class.items(), key=lambda kv: -kv[1][1],
        ):
            arrow = (
                f"{src_cls} -> {out_cls}"
                if src_cls != out_cls else src_cls
            )
            print(f"      {n:>4}  {arrow}")
    else:
        print("  classes converted             : (none)")

    print(
        f"  remaining objects             : {len(world.objects)} "
        f"({len(set(o.type_str for o in world.objects))} classes)"
    )

    print()
    print("== asset audit ==")
    _print_audit("models", stats.audit_models)
    _print_audit("skins ", stats.audit_skins)
    _print_audit("sounds", stats.audit_sounds)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a LoMM level from LoMM WORLDS.REZ and add it to "
            "MM9 WORLDS.REZ."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mm9_root", required=True,
        help="Path to the Might and Magic IX install folder.",
    )
    parser.add_argument(
        "--lomm_root", required=True,
        help="Path to the Legends of Might and Magic install folder.",
    )
    parser.add_argument(
        "--level_to_convert", required=True,
        help="Level name from LoMM WORLDS.REZ, e.g. CHATEAUESCAPE.",
    )
    parser.add_argument(
        "--converted_level_name", required=True,
        help="New level name to add to MM9 WORLDS.REZ.",
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG,
        help=(
            "Path to the YAML (or JSON) conversion config "
            "(default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--catalog", default=DEFAULT_CATALOG,
        help=(
            "Path to a pre-built MM9 catalog.json with the canonical class "
            "registry (default: %(default)s).  Pass an empty string to "
            "force a fresh scan of WORLDS.REZ."
        ),
    )
    parser.add_argument(
        "--lomm-catalog",
        "--lomm_catalog",
        dest="lomm_catalog",
        default=DEFAULT_LOMM_CATALOG,
        help=(
            "Path to the LoMM catalog used for runtime classes and implicit "
            "model skins. It is built from --lomm_root when missing."
        ),
    )
    parser.add_argument(
        "--backup_root",
        help=(
            "Folder for automatic WORLDS.REZ backups. Default: "
            "<mm9_root>/mm9_editor/backups"
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate and convert, but do not modify MM9 WORLDS.REZ.",
    )
    parser.add_argument(
        "--actor-policy", choices=ACTOR_POLICIES,
        help=(
            "How LoMM actors are handled. Defaults to actor_policy in the "
            "conversion config (preserve in the bundled config)."
        ),
    )
    parser.add_argument(
        "--allow-incompatible-actors", action="store_true",
        help=(
            "Advanced override for live insertion when preserved actors are "
            "not registered by MM9. Has no effect on --dry-run."
        ),
    )
    args = parser.parse_args(argv)

    try:
        from conversion import lomm_to_mm9_service as service

        request = service.ConvertLevelRequest(
            mm9_root=args.mm9_root,
            lomm_root=args.lomm_root,
            level_to_convert=args.level_to_convert,
            converted_level_name=args.converted_level_name,
            config_path=args.config,
            catalog_json=args.catalog or None,
            lomm_catalog_json=args.lomm_catalog or None,
            actor_policy=args.actor_policy,
        )

        print(f"MM9 root     : {args.mm9_root}")
        print(f"LoMM root    : {args.lomm_root}")
        print(f"source level : {args.level_to_convert}")
        print(f"output level : {args.converted_level_name}")
        print(f"config       : {args.config}")
        print()

        if args.dry_run:
            result = service.convert_level_to_bytes(request)
            print(f"source entry : {result.source_virtual_path}")
            print(f"output entry : {result.output_virtual_path}")
            print(f"DAT bytes    : {len(result.dat_bytes)}")
            print()
            _print_summary(result.stats, _world_from_bytes(result.dat_bytes))
            print()
            print("dry-run: MM9 WORLDS.REZ was not modified")
            return 0

        result = service.convert_and_insert_level(
            request,
            backup_root=args.backup_root,
            allow_incompatible_actors=args.allow_incompatible_actors,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"source entry : {result.conversion.source_virtual_path}")
    print(f"added entry  : {result.added_virtual_path}")
    print(f"MM9 WORLDS   : {result.worlds_rez}")
    print(f"backup       : {result.backup_path}")
    print()
    _print_summary(
        result.conversion.stats,
        _world_from_bytes(result.conversion.dat_bytes),
    )
    print()
    for line in result.log:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
