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
import bsp
import prefab_import
import prefab_inspector


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

    for submodel in plan.submodels:
        model = submodel.source_model
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

    return warnings
