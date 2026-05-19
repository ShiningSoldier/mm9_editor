"""
prefab_import_validation.py
===========================

Save-plan validation for static converted-prefab BSP imports.

These checks are intentionally warnings, not hard failures.  Placement already
rejects structurally impossible imports; this module calls out cases that are
likely to surprise the user or the game loader.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import List, Sequence

import _path_setup  # noqa: F401
from core import bsp
from features.doors import clone as door_clone
from . import import_static as prefab_import
from . import inspector as prefab_inspector


def validate_import_plans(
    target_bsp: bsp.BspWorld,
    import_plans: Sequence[prefab_import.PrefabBspImportPlan],
) -> List[str]:
    warnings: List[str] = []
    if not import_plans:
        return warnings

    new_names = [
        submodel.new_name
        for plan in import_plans
        for submodel in plan.submodels
    ]
    for name, count in sorted(Counter(name.lower() for name in new_names).items()):
        if count > 1:
            original = next(n for n in new_names if n.lower() == name)
            warnings.append(f"prefab import creates duplicate BSP model name {original!r}")

    existing_names = {
        str(getattr(model, "name", "") or "").lower()
        for model in getattr(target_bsp, "world_models", []) or []
    }
    for name in new_names:
        if name.lower() in existing_names:
            warnings.append(f"prefab import BSP name {name!r} collides with the target level")

    for plan in import_plans:
        warnings.extend(_validate_one_plan(plan))

    return warnings


def _validate_one_plan(plan: prefab_import.PrefabBspImportPlan) -> List[str]:
    warnings: List[str] = []
    label = f"prefab {os.path.basename(plan.source_path)} -> {plan.new_name}"

    if not os.path.exists(plan.source_path):
        return [f"{label}: source file is missing"]

    try:
        info = prefab_inspector.inspect_prefab(plan.source_path)
    except Exception as exc:
        return [f"{label}: cannot re-read source prefab ({exc})"]

    if info.parse_warnings:
        for warning in info.parse_warnings[:5]:
            warnings.append(f"{label}: source parse warning: {warning}")

    if info.object_count:
        classes = ", ".join(f"{name}={count}" for name, count in sorted(info.object_classes.items()))
        warnings.append(
            f"{label}: source prefab contains WorldObjects ({classes}); static BSP import ignores them"
        )

    if not plan.submodels:
        warnings.append(f"{label}: no BSP submodels selected")
        return warnings

    selected_roles = set(plan.source_model_roles)
    has_collision_helper = "collision_helper" in selected_roles
    has_collision_box = "collision_box" in selected_roles
    if "visibility" in selected_roles:
        warnings.append(
            f"{label}: imports VisBSP; visibility records can contain leaf/PVS data and may not load safely"
        )
    if "physics" in selected_roles and any(v == 2 for v in plan.info_flags_overrides):
        warnings.append(
            f"{label}: uses PhysicsBSP polygon data as a normal visible submodel; "
            "collision probably requires merging into the level PhysicsBSP"
        )
    elif "physics" in selected_roles:
        warnings.append(f"{label}: imports PhysicsBSP without converting its model flags")
    if selected_roles == {"visibility"} and "physics" in info.model_roles:
        warnings.append(
            f"{label}: imports VisBSP only; collision behavior in-game is not confirmed yet"
        )
    if has_collision_helper:
        warnings.append(
            f"{label}: adds an experimental InvisibleBrush collision helper; "
            "this matches shipped blocker/controller patterns but needs in-game confirmation"
        )
    if has_collision_box:
        warnings.append(
            f"{label}: adds an experimental scaled InvisibleBrush box collision helper; "
            "this matches shipped blocker/controller patterns but needs in-game confirmation"
        )
    if not (has_collision_helper or has_collision_box):
        warnings.append(f"{label}: imports visible geometry without a collision helper")

    for submodel in plan.submodels:
        model = submodel.source_model
        role = _role_for_submodel(plan, submodel)
        if not submodel.raw_bytes:
            warnings.append(f"{label}: source model {submodel.source_name!r} has empty raw bytes")
        if not model.polygons:
            warnings.append(f"{label}: source model {submodel.source_name!r} has no polygons")
        if any(str(tex or "").lower() == "default" for tex in model.texture_names):
            warnings.append(
                f"{label}: source model {submodel.source_name!r} uses Default texture names"
            )
        if len(model.polygons) > 5000:
            warnings.append(
                f"{label}: source model {submodel.source_name!r} is large ({len(model.polygons)} polygons)"
            )
        if role == "collision_box":
            warnings.extend(_collision_shape_warnings(label, submodel))

    return warnings


def _role_for_submodel(
    plan: prefab_import.PrefabBspImportPlan,
    submodel,
) -> str:
    for candidate, role in zip(plan.submodels, plan.source_model_roles):
        if candidate is submodel:
            return role
    return ""


def _collision_shape_warnings(label: str, submodel) -> List[str]:
    model = submodel.source_model
    min_box, max_box = door_clone.transform_bounds(
        model.min_box,
        model.max_box,
        submodel.source_pivot,
        submodel.target_pivot,
        submodel.yaw_radians,
        scale=submodel.scale,
    )
    sizes = [
        abs(float(max_box[0]) - float(min_box[0])),
        abs(float(max_box[1]) - float(min_box[1])),
        abs(float(max_box[2]) - float(min_box[2])),
    ]
    nonzero = [max(size, 1.0e-6) for size in sizes]
    shortest = min(nonzero)
    tallest = sizes[1]
    longest = max(nonzero)
    warnings: List[str] = []
    if shortest < 2.0:
        warnings.append(f"{label}: collision helper {submodel.new_name!r} is extremely thin ({shortest:.1f} units)")
    if tallest > 512.0:
        warnings.append(f"{label}: collision helper {submodel.new_name!r} is very tall ({tallest:.1f} units)")
    if longest / shortest > 512.0:
        warnings.append(
            f"{label}: collision helper {submodel.new_name!r} has an extreme aspect ratio "
            f"({longest / shortest:.1f}:1)"
        )
    return warnings
