"""Runtime class registry loading for LoMM/MM9 conversion.

The active ``object.lto`` is preferred because it describes the classes the
runtime can actually construct.  Generated catalog JSON is used as a fallback
for installs whose LTO cannot be inspected on the current machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class RuntimeClass:
    name: str
    hierarchy: Tuple[str, ...] = ()
    properties: Tuple[str, ...] = ()
    runtime_loadable: bool = True
    hidden_in_editor: bool = False

    @property
    def is_actor(self) -> bool:
        hierarchy = {part.casefold() for part in self.hierarchy}
        return "actor" in hierarchy or "aibase" in hierarchy


@dataclass
class RuntimeClassRegistry:
    classes: Dict[str, RuntimeClass] = field(default_factory=dict)
    source: str = "observed world data"
    warnings: Tuple[str, ...] = ()

    def get(self, class_name: str) -> Optional[RuntimeClass]:
        wanted = str(class_name or "").casefold()
        for name, info in self.classes.items():
            if name.casefold() == wanted:
                return info
        return None

    def contains(self, class_name: str) -> bool:
        info = self.get(class_name)
        return bool(info and info.runtime_loadable)

    def is_actor(self, class_name: str) -> bool:
        info = self.get(class_name)
        return bool(info and info.is_actor)

    @property
    def class_names(self) -> set[str]:
        return {info.name for info in self.classes.values() if info.runtime_loadable}


def _property_names(raw: object) -> Tuple[str, ...]:
    if isinstance(raw, Mapping):
        return tuple(str(name) for name in raw)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    names = []
    for item in raw:
        if isinstance(item, Mapping):
            name = item.get("name") or item.get("Name")
            if name:
                names.append(str(name))
        elif item:
            names.append(str(item))
    return tuple(names)


def _class_from_raw(name: str, raw: Mapping[str, Any]) -> RuntimeClass:
    hierarchy_raw = raw.get("hierarchy", raw.get("class_hierarchy", ()))
    if not isinstance(hierarchy_raw, Sequence) or isinstance(hierarchy_raw, (str, bytes, bytearray)):
        hierarchy_raw = ()
    flags = raw.get("flags") if isinstance(raw.get("flags"), Mapping) else {}
    hidden = bool(
        raw.get("hidden_in_dedit", raw.get("hidden_in_editor", raw.get("hidden", False)))
        or flags.get("hidden_in_editor", flags.get("hidden", False))
    )
    loadable = bool(raw.get("runtime_loadable", True)) and not bool(
        raw.get("abstract", False) or flags.get("abstract", False)
    )
    return RuntimeClass(
        name=str(name),
        hierarchy=tuple(str(part) for part in hierarchy_raw),
        properties=_property_names(raw.get("properties", raw.get("props", ()))),
        runtime_loadable=loadable,
        hidden_in_editor=hidden,
    )


def _classes_from_payload(payload: Mapping[str, Any]) -> Dict[str, RuntimeClass]:
    embedded = payload.get("object_lto")
    if isinstance(embedded, Mapping):
        payload = embedded
    raw_classes = payload.get("classes")
    classes: Dict[str, RuntimeClass] = {}
    if isinstance(raw_classes, Mapping):
        for name, raw in raw_classes.items():
            if isinstance(raw, Mapping):
                classes[str(name)] = _class_from_raw(str(name), raw)
    elif isinstance(raw_classes, Sequence) and not isinstance(raw_classes, (str, bytes, bytearray)):
        for raw in raw_classes:
            if not isinstance(raw, Mapping):
                continue
            name = raw.get("name") or raw.get("class_name") or raw.get("class")
            if name:
                classes[str(name)] = _class_from_raw(str(name), raw)
    return classes


def _load_json(path: Path) -> Dict[str, RuntimeClass]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        return {}
    return _classes_from_payload(payload)


def _dump_object_lto(path: Path) -> Dict[str, RuntimeClass]:
    # Import lazily: conversion can still work from generated catalog JSON when
    # the platform-specific LTO helper is unavailable.
    from catalog.builder import generate_object_lto_dump

    payload = generate_object_lto_dump(str(path))
    return _classes_from_payload(payload)


def load_runtime_registry(
    *,
    object_lto: Optional[Path] = None,
    catalog_json: Optional[Path] = None,
    observed_classes: Iterable[str] = (),
) -> RuntimeClassRegistry:
    """Load a runtime registry, preferring the active install's object.lto."""

    warnings = []
    classes: Dict[str, RuntimeClass] = {}
    source = "observed world data"
    lto_path = Path(object_lto) if object_lto else None
    if lto_path and lto_path.is_file():
        try:
            classes = _dump_object_lto(lto_path)
            if classes:
                source = str(lto_path)
        except Exception as exc:  # pragma: no cover - depends on local helper/runtime
            warnings.append(f"Could not inspect {lto_path}: {exc}")

    catalog_path = Path(catalog_json) if catalog_json else None
    if not classes and catalog_path and catalog_path.is_file():
        try:
            classes = _load_json(catalog_path)
            if classes:
                source = str(catalog_path)
        except Exception as exc:
            warnings.append(f"Could not read {catalog_path}: {exc}")

    # World-observed classes are a last resort only. A DAT containing a class
    # does not prove that the target runtime registers it; treating observed
    # names as authoritative would recreate the compatibility bug this module
    # is intended to prevent.
    if not classes:
        for name in observed_classes:
            text = str(name or "").strip()
            if text and not any(existing.casefold() == text.casefold() for existing in classes):
                classes[text] = RuntimeClass(name=text)

    return RuntimeClassRegistry(classes=classes, source=source, warnings=tuple(warnings))


__all__ = [
    "RuntimeClass",
    "RuntimeClassRegistry",
    "load_runtime_registry",
]
