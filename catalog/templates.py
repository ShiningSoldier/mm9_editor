"""Construct canonical editable objects from catalog class metadata."""

from __future__ import annotations

from typing import Any, Mapping, Optional

import _path_setup  # noqa: F401
import mm9_patch as patcher


def class_template_from_catalog(
    catalog: Mapping[str, Any],
    class_name: str,
) -> Optional[patcher.WorldObject]:
    """Return an object.lto-derived template for *class_name*, when available.

    Observed DAT templates are intentionally not used here.  Static prefab
    controllers must be constructible even when the target level has no
    instance of the required class, and object.lto is the canonical source for
    the class' complete property schema and defaults.
    """
    classes = catalog.get("classes")
    if not isinstance(classes, Mapping):
        return None
    entry = classes.get(class_name)
    if not isinstance(entry, Mapping):
        return None
    object_lto = entry.get("object_lto")
    if not isinstance(object_lto, Mapping):
        return None
    items = object_lto.get("template_properties")
    if not isinstance(items, list):
        return None

    props = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        try:
            props.append(patcher.Property(
                str(item["name"]),
                int(item["code"]),
                int(item.get("flags") or 0),
                item.get("value"),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    if not props:
        return None
    return patcher.WorldObject(str(class_name), props)
