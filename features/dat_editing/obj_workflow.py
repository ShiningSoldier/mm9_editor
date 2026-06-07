"""Shared helpers for OBJ + DAT sidecar geometry round trips."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional, Sequence


ROUNDTRIP_KIND = "mm9_dat_geometry_roundtrip"


def load_roundtrip_meta(meta_path: str, source_dat: Optional[bytes] = None) -> Dict[str, object]:
    if not meta_path:
        raise ValueError("DAT sidecar metadata path is empty; select the .datmeta.json file exported with the OBJ")
    if not os.path.exists(meta_path):
        raise ValueError(f"DAT sidecar metadata was not found: {meta_path}")
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"DAT sidecar metadata is not valid JSON: {meta_path} ({exc})") from exc
    if not isinstance(meta, dict):
        raise ValueError(f"DAT sidecar metadata must be a JSON object: {meta_path}")
    kind = str(meta.get("kind") or "")
    if kind != ROUNDTRIP_KIND:
        raise ValueError(
            f"DAT sidecar metadata has unsupported kind {kind!r}; expected {ROUNDTRIP_KIND!r}"
        )
    models = meta.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("DAT sidecar metadata has no exported BSP models")
    _validate_export_to_dat_matrix(meta)
    if source_dat is not None:
        validate_source_identity(source_dat, meta)
    return meta


def validate_source_identity(source_dat: bytes, meta: Dict[str, object]) -> None:
    source = meta.get("source", {}) or {}
    if not isinstance(source, dict):
        raise ValueError("DAT sidecar metadata has invalid source information")
    expected = str(source.get("sha256") or "")
    if not expected:
        return
    actual = hashlib.sha256(source_dat).hexdigest()
    if actual.lower() != expected.lower():
        path = str(source.get("path") or "<unknown source>")
        raise ValueError(
            "OBJ metadata source checksum does not match the currently loaded DAT. "
            f"Metadata was exported from {path!r} with sha256 {expected[:12]}..., "
            f"but the loaded level is {actual[:12]}.... Re-export this level or open the matching source DAT."
        )


def expected_obj_names(meta: Dict[str, object]) -> List[str]:
    result: List[str] = []
    for index, model_meta in enumerate(meta.get("models", []) or []):
        if not isinstance(model_meta, dict):
            continue
        result.append(obj_name(str(model_meta.get("name") or ""), index))
    return result


def missing_obj_message(expected_name: str, model_name: str, available_names: Sequence[str]) -> str:
    available = ", ".join(str(name) for name in available_names[:12])
    if len(available_names) > 12:
        available += f", ... (+{len(available_names) - 12} more)"
    suffix = f" Available OBJ objects: {available}" if available else " The OBJ contains no mesh objects with faces."
    return (
        f"OBJ object {expected_name!r} for BSP model {model_name!r} was not found. "
        "Use the OBJ exported with the selected .datmeta.json sidecar, or keep exported object names intact."
        + suffix
    )


def obj_name(name: str, index: int) -> str:
    import re

    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", str(name or "")).strip("_")
    return cleaned or f"WorldModel_{index}"


def object_key(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9_]+", "_", str(name or "").lower()).strip("_")


def _validate_export_to_dat_matrix(meta: Dict[str, object]) -> None:
    coord = meta.get("coordinate_system", {}) or {}
    if not isinstance(coord, dict):
        raise ValueError("DAT sidecar metadata has invalid coordinate_system information")
    matrix = coord.get("export_to_dat_matrix")
    if matrix is None:
        return
    if not isinstance(matrix, list) or len(matrix) != 4:
        raise ValueError("DAT sidecar export_to_dat_matrix must be a 4x4 matrix")
    for row in matrix:
        if not isinstance(row, list) or len(row) != 4:
            raise ValueError("DAT sidecar export_to_dat_matrix must be a 4x4 matrix")
        for value in row:
            float(value)
