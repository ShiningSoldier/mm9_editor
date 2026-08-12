"""LoMM install-root catalog bootstrap helpers."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from .builder import (
    build_catalog_from_rez,
    load_catalog,
    save_catalog_atomic,
)


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOMM_CATALOG_PATH = os.path.join(HERE, "data", "catalog_lomm.json")


class LommCatalogError(RuntimeError):
    """Raised when a LoMM install cannot produce a valid catalog."""


def _find_child_dir(parent: str, name: str) -> Optional[str]:
    if not os.path.isdir(parent):
        return None
    wanted = name.casefold()
    for child in os.listdir(parent):
        path = os.path.join(parent, child)
        if child.casefold() == wanted and os.path.isdir(path):
            return path
    return None


def _find_child_file(parent: str, name: str) -> Optional[str]:
    if not os.path.isdir(parent):
        return None
    wanted = name.casefold()
    for child in os.listdir(parent):
        path = os.path.join(parent, child)
        if child.casefold() == wanted and os.path.isfile(path):
            return path
    return None


def lomm_catalog_sources(lomm_root: str) -> Dict[str, Optional[str]]:
    """Resolve catalog inputs from a Legends of Might and Magic install."""
    root = os.path.abspath(os.path.expanduser(str(lomm_root or "").strip()))
    if not os.path.isdir(root):
        raise LommCatalogError(f"LoMM install folder was not found: {root!r}")
    data_dir = _find_child_dir(root, "data")
    if not data_dir:
        raise LommCatalogError(f"LoMM install has no Data folder: {root!r}")
    worlds_rez = _find_child_file(data_dir, "worlds.rez")
    if not worlds_rez:
        raise LommCatalogError(
            f"LoMM Data folder has no WORLDS.REZ archive: {data_dir!r}"
        )
    return {
        "root": root,
        "data_dir": data_dir,
        "worlds_rez": worlds_rez,
        "object_lto": _find_child_file(data_dir, "object.lto"),
        "skins_rez": _find_child_file(data_dir, "skins.rez"),
        "skins_dir": _find_child_dir(data_dir, "skins"),
        "models_rez": _find_child_file(data_dir, "models.rez"),
        "models_dir": _find_child_dir(data_dir, "models"),
    }


def validate_lomm_catalog(catalog: object) -> Dict[str, Any]:
    """Validate and return a generated/loaded LoMM catalog document."""
    if not isinstance(catalog, dict):
        raise LommCatalogError("LoMM catalog root must be a JSON object")
    if not isinstance(catalog.get("classes"), dict):
        raise LommCatalogError("LoMM catalog has no classes map")
    if not isinstance(catalog.get("model_variants"), dict):
        raise LommCatalogError("LoMM catalog has no model_variants map")
    summary = catalog.get("summary")
    if not isinstance(summary, dict) or not isinstance(
        summary.get("total_levels"), int
    ):
        raise LommCatalogError("LoMM catalog has no valid build summary")
    if summary["total_levels"] < 1:
        raise LommCatalogError("LoMM catalog contains no levels")
    return catalog


def build_lomm_catalog_from_root(
    lomm_root: str,
    *,
    object_lto_helper_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a LoMM catalog from its install without MM9 actor-table logic."""
    sources = lomm_catalog_sources(lomm_root)
    catalog = build_catalog_from_rez(
        str(sources["worlds_rez"]),
        object_lto_path=sources["object_lto"],
        object_lto_helper_path=object_lto_helper_path,
        skins_rez_path=sources["skins_rez"],
        skins_dir=sources["skins_dir"],
        models_rez_path=sources["models_rez"],
        models_dir=sources["models_dir"],
    )
    if sources["object_lto"] and not isinstance(catalog.get("object_lto"), dict):
        raise LommCatalogError(
            "LoMM object.lto was found but its class metadata could not be loaded"
        )
    catalog["game"] = "lomm"
    catalog["catalog_sources"] = {
        key: value
        for key, value in sources.items()
        if key != "root" and value
    }
    return validate_lomm_catalog(catalog)


def ensure_lomm_catalog(
    lomm_root: str,
    catalog_path: str = DEFAULT_LOMM_CATALOG_PATH,
    *,
    object_lto_helper_path: Optional[str] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Load an existing LoMM catalog or atomically create it when missing.

    Returns ``(catalog, generated)``. Existing files are never overwritten.
    """
    path = os.path.abspath(os.path.expanduser(catalog_path))
    if os.path.isfile(path):
        return validate_lomm_catalog(load_catalog(path)), False

    catalog = build_lomm_catalog_from_root(
        lomm_root,
        object_lto_helper_path=object_lto_helper_path,
    )
    if os.path.exists(path):
        # Another process completed the same bootstrap while this one built.
        return validate_lomm_catalog(load_catalog(path)), False
    save_catalog_atomic(catalog, path, validator=validate_lomm_catalog)
    return catalog, True
