"""
door_clone_validation.py
========================

Lightweight checks for pending physical-door clone save plans.
"""

from __future__ import annotations

import struct
from typing import List, Sequence

import _path_setup  # noqa: F401
import bsp
import door_clone
import mm9_patch as patcher


def validate_clone_plans(
    source_dat: bytes,
    materialized: patcher.World,
    bsp_world: bsp.BspWorld,
    clone_plans: Sequence[door_clone.DoorClonePlan],
) -> List[str]:
    warnings: List[str] = []
    if not clone_plans:
        return warnings

    object_names = {
        str(obj.get("Name") or "").lower()
        for obj in materialized.objects
        if hasattr(obj, "get")
    }
    source_model_names = {
        str(model.name or "").lower()
        for model in getattr(bsp_world, "world_models", []) or []
    }
    clone_names: List[str] = []

    for plan in clone_plans:
        if not plan.objects or not plan.submodels:
            warnings.append(f"door clone {plan.new_name!r} has incomplete controller/BSP data")
            continue
        if len(plan.objects) != len(plan.submodels):
            warnings.append(
                f"door clone {plan.new_name!r} has {len(plan.objects)} controller(s) "
                f"but {len(plan.submodels)} BSP submodel(s)"
            )
        for obj in plan.objects:
            name = str(obj.get("Name") or "")
            clone_names.append(name.lower())
            if name.lower() not in object_names:
                warnings.append(f"door clone controller {name!r} is missing from materialized objects")
            portal = str(obj.get("PortalName") or "")
            if portal:
                warnings.append(
                    f"door clone {name!r} keeps source PortalName={portal!r}; "
                    "verify it should use the same visibility portal"
                )
        for submodel in plan.submodels:
            if not submodel.raw_bytes:
                warnings.append(f"door clone BSP {submodel.new_name!r} has no source bytes")
            if submodel.new_name.lower() in source_model_names:
                warnings.append(f"door clone BSP name {submodel.new_name!r} collides with source BSP")
            if submodel.new_name.lower() not in object_names:
                warnings.append(f"door clone BSP {submodel.new_name!r} has no matching controller object")

    for name in sorted({name for name in clone_names if clone_names.count(name) > 1}):
        warnings.append(f"duplicate cloned door controller name {name!r}")

    _validate_terminal_tail(source_dat, bsp_world, clone_plans, warnings)
    return warnings


def _validate_terminal_tail(
    source_dat: bytes,
    bsp_world: bsp.BspWorld,
    clone_plans: Sequence[door_clone.DoorClonePlan],
    warnings: List[str],
) -> None:
    try:
        header = patcher.Header.parse(source_dat)
    except Exception:
        return
    parsed_models = [m for m in getattr(bsp_world, "world_models", []) if m.raw_start is not None]
    if not parsed_models:
        return
    last_model = max(parsed_models, key=lambda m: m.raw_start)
    next_item = int(last_model.next_world_item or 0)
    if not (last_model.raw_start < next_item < header.obj_pos):
        return
    if next_item + 4 > len(source_dat):
        warnings.append("source DAT has a terminal BSP tail outside the file bounds")
        return
    tail_next = struct.unpack_from("<I", source_dat, next_item)[0]
    if tail_next != header.obj_pos:
        warnings.append(
            "source DAT has a terminal BSP tail whose first pointer does not "
            "match ObjectDataPos; cloned doors may need manual in-game testing"
        )
    else:
        count = sum(len(plan.submodels) for plan in clone_plans)
        warnings.append(
            f"source DAT has a terminal BSP tail after {last_model.name}; "
            f"{count} cloned door BSP record(s) will be inserted before it"
        )
