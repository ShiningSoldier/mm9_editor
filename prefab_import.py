"""
prefab_import.py
================

Planning helpers for importing static BSP geometry from converted DEdit prefab
DAT files.

Stage 2 is intentionally narrow: it imports renamed BSP world-model records and
does not import controller WorldObjects, scripts, or special door/elevator
logic.  The writer path reuses the same raw-record transform machinery as
physical door cloning.
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
import bsp
import door_clone
import prefab_inspector


Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class PrefabBspImportPlan:
    source_path: str
    new_name: str
    target_pos: Vec3
    target_yaw: float
    submodels: List[door_clone.DoorSubmodelClone]
    source_model_names: List[str]
    source_model_roles: List[str]
    info_flags_overrides: List[Optional[int]]


def suggest_import_name(existing_bsp: bsp.BspWorld, prefab_path: str) -> str:
    """Return a collision-free BSP model name prefix for *prefab_path*."""
    stem = os.path.splitext(os.path.basename(prefab_path))[0] or "Prefab"
    stem = _sanitize_model_name(stem)
    existing = {
        str(getattr(model, "name", "") or "").lower()
        for model in getattr(existing_bsp, "world_models", []) or []
    }
    if stem.lower() not in existing:
        return stem
    index = 1
    while f"{stem}{index}".lower() in existing:
        index += 1
    return f"{stem}{index}"


def build_static_import_plan(
    target_bsp: bsp.BspWorld,
    prefab_path: str,
    new_name: Optional[str] = None,
    target_pos: Sequence[float] = (0.0, 0.0, 0.0),
    target_yaw: float = 0.0,
    include_roles: Optional[Sequence[str]] = None,
) -> PrefabBspImportPlan:
    """
    Build a static BSP import plan from a converted prefab DAT.

    By default this imports visual geometry only: `visibility` models are used
    when present, otherwise normal `geometry`/`controller_geometry` models are
    used.  `physics` records are excluded by default to avoid inserting two
    identical visible copies for many DEdit prefabs.
    """
    info = prefab_inspector.inspect_prefab(prefab_path)
    with open(prefab_path, "rb") as f:
        prefab_dat = f.read()
    prefab_bsp = bsp.parse(prefab_dat)

    source_models = _select_static_models(prefab_bsp, info, include_roles)
    if not source_models:
        raise ValueError(f"{prefab_path!r} has no importable static BSP models")

    target = _as_vec3(target_pos, "target_pos")
    prefix = _sanitize_model_name(new_name or suggest_import_name(target_bsp, prefab_path))
    new_names = _new_model_names(prefix, source_models)
    _validate_model_names(target_bsp, new_names)

    submodels: List[door_clone.DoorSubmodelClone] = []
    for source_model, model_name in zip(source_models, new_names):
        raw = prefab_bsp.raw_model_bytes(prefab_dat, source_model)
        if raw is None:
            raise ValueError(f"prefab BSP model {source_model.name!r} has no recoverable byte range")
        submodels.append(door_clone.DoorSubmodelClone(
            source_name=source_model.name,
            new_name=model_name,
            source_model=source_model,
            raw_bytes=bytes(raw),
            source_pivot=(0.0, 0.0, 0.0),
            target_pivot=target,
            yaw_radians=float(target_yaw),
            info_flags_override=_info_flags_override_for_role(
                next((m.role for m in info.models if m.name.lower() == source_model.name.lower()), "geometry")
            ),
        ))

    roles = [
        next((m.role for m in info.models if m.name.lower() == model.name.lower()), "geometry")
        for model in source_models
    ]
    return PrefabBspImportPlan(
        source_path=os.path.abspath(prefab_path),
        new_name=prefix,
        target_pos=target,
        target_yaw=float(target_yaw),
        submodels=submodels,
        source_model_names=[model.name for model in source_models],
        source_model_roles=roles,
        info_flags_overrides=[sub.info_flags_override for sub in submodels],
    )


def build_preview_bsp(
    target_bsp: bsp.BspWorld,
    import_plans: Sequence[PrefabBspImportPlan],
) -> bsp.BspWorld:
    preview = copy.deepcopy(target_bsp)
    for plan in import_plans or []:
        for submodel in plan.submodels:
            preview.world_models.append(door_clone.translated_model_clone(submodel))
    return preview


def _select_static_models(
    prefab_bsp: bsp.BspWorld,
    info: prefab_inspector.PrefabInspection,
    include_roles: Optional[Sequence[str]],
) -> List[bsp.WorldModelMesh]:
    role_by_name = {model.name.lower(): model.role for model in info.models}
    requested = {str(role).lower() for role in include_roles or []}
    if requested:
        roles = requested
    else:
        available = set(role_by_name.values())
        if available and available <= {"physics", "visibility"} and "physics" in available:
            roles = {"physics"}
        elif "visibility" in available:
            roles = {"visibility"}
        else:
            roles = {"geometry", "controller_geometry"}

    return [
        model
        for model in prefab_bsp.world_models
        if role_by_name.get(model.name.lower(), "geometry") in roles
    ]


def _new_model_names(prefix: str, models: Sequence[bsp.WorldModelMesh]) -> List[str]:
    if len(models) == 1:
        return [prefix]
    names: List[str] = []
    seen: set[str] = set()
    for index, model in enumerate(models, start=1):
        suffix = _sanitize_model_name(str(model.name or f"Model{index}"))
        name = f"{prefix}_{suffix}"
        if name.lower() in seen:
            name = f"{name}{index}"
        seen.add(name.lower())
        names.append(name)
    return names


def _validate_model_names(target_bsp: bsp.BspWorld, new_names: Sequence[str]) -> None:
    existing = {
        str(getattr(model, "name", "") or "").lower()
        for model in getattr(target_bsp, "world_models", []) or []
    }
    lowered = [name.lower() for name in new_names]
    if len(set(lowered)) != len(lowered):
        raise ValueError(f"imported BSP model names must be unique: {', '.join(new_names)}")
    for name in new_names:
        if not str(name or "").strip():
            raise ValueError("imported BSP model names must be non-empty")
        if name.lower() in existing:
            raise ValueError(f"BSP model named {name!r} already exists in the target level")


def _sanitize_model_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "", str(value or ""))
    return cleaned or "Prefab"


def _info_flags_override_for_role(role: str) -> Optional[int]:
    # Converted prefabs often contain only PhysicsBSP/VisBSP.  VisBSP records
    # carry leaf/visibility payloads that are not safe to splice into another
    # level's model list.  PhysicsBSP has the plain polygon data we need; patch
    # it to the ordinary submodel flag used by normal static world models.
    return 2 if role == "physics" else None


def _as_vec3(value: object, prop_name: str) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{prop_name} must be a 3-vector, got {value!r}")
    return (float(value[0]), float(value[1]), float(value[2]))
