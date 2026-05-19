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
from core import bsp
from features.doors import clone as door_clone
from . import inspector as prefab_inspector


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
    collision_mode: str = "none",
    collision_thickness: float = 8.0,
    collision_segment_length: float = 512.0,
    target_dat_bytes: Optional[bytes] = None,
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
    collision_mode = _normalize_collision_mode(collision_mode)
    collision_names = _collision_model_names(new_names) if collision_mode == "invisible_bsp" else []
    _validate_model_names(target_bsp, [*new_names, *collision_names])

    submodels: List[door_clone.DoorSubmodelClone] = []
    source_names: List[str] = []
    roles: List[str] = []
    info_flags_overrides: List[Optional[int]] = []
    for source_model, model_name in zip(source_models, new_names):
        raw = prefab_bsp.raw_model_bytes(prefab_dat, source_model)
        if raw is None:
            raise ValueError(f"prefab BSP model {source_model.name!r} has no recoverable byte range")
        role = next((m.role for m in info.models if m.name.lower() == source_model.name.lower()), "geometry")
        override = _info_flags_override_for_role(role)
        submodels.append(door_clone.DoorSubmodelClone(
            source_name=source_model.name,
            new_name=model_name,
            source_model=source_model,
            raw_bytes=bytes(raw),
            source_pivot=(0.0, 0.0, 0.0),
            target_pivot=target,
            yaw_radians=float(target_yaw),
            info_flags_override=override,
        ))
        source_names.append(source_model.name)
        roles.append(role)
        info_flags_overrides.append(override)

    if collision_mode == "invisible_bsp":
        for source_model, model_name in zip(source_models, collision_names):
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
                info_flags_override=2,
            ))
            source_names.append(source_model.name)
            roles.append("collision_helper")
            info_flags_overrides.append(2)
    elif collision_mode == "box_approx":
        first_min, first_max = door_clone.transform_bounds(
            source_models[0].min_box,
            source_models[0].max_box,
            (0.0, 0.0, 0.0),
            target,
            float(target_yaw),
        )
        box_template = _select_collision_box_template(
            target_bsp,
            target_dat_bytes,
            desired_size=_collision_box_size(first_min, first_max, collision_thickness),
        )
        box_collision_names: List[str] = []
        for source_model, model_name in zip(source_models, new_names):
            target_min, target_max = door_clone.transform_bounds(
                source_model.min_box,
                source_model.max_box,
                (0.0, 0.0, 0.0),
                target,
                float(target_yaw),
            )
            target_min, target_max = _thin_collision_bounds(target_min, target_max, collision_thickness)
            segments = _segment_collision_bounds(target_min, target_max, collision_segment_length)
            segment_names = _collision_segment_names(model_name, len(segments))
            box_collision_names.extend(segment_names)
            for segment_name, (segment_min, segment_max) in zip(segment_names, segments):
                submodels.append(door_clone.DoorSubmodelClone(
                    source_name=box_template.name,
                    new_name=segment_name,
                    source_model=box_template,
                    raw_bytes=bytes(target_bsp.raw_model_bytes(target_dat_bytes, box_template)),
                    source_pivot=box_template.min_box,
                    target_pivot=segment_min,
                    yaw_radians=0.0,
                    scale=_bounds_scale(box_template.min_box, box_template.max_box, segment_min, segment_max),
                    info_flags_override=2,
                ))
                source_names.append(box_template.name)
                roles.append("collision_box")
                info_flags_overrides.append(2)
        _validate_model_names(target_bsp, [*new_names, *box_collision_names])

    return PrefabBspImportPlan(
        source_path=os.path.abspath(prefab_path),
        new_name=prefix,
        target_pos=target,
        target_yaw=float(target_yaw),
        submodels=submodels,
        source_model_names=source_names,
        source_model_roles=roles,
        info_flags_overrides=info_flags_overrides,
    )


def build_preview_bsp(
    target_bsp: bsp.BspWorld,
    import_plans: Sequence[PrefabBspImportPlan],
) -> bsp.BspWorld:
    preview = _shallow_bsp_with_original_models(target_bsp)
    for plan in import_plans or []:
        for submodel in plan.submodels:
            preview.world_models.append(door_clone.translated_model_clone(submodel))
    return preview


def _shallow_bsp_with_original_models(target_bsp: bsp.BspWorld) -> bsp.BspWorld:
    return bsp.BspWorld(
        version=target_bsp.version,
        world_info=target_bsp.world_info,
        obj_pos=target_bsp.obj_pos,
        ren_pos=target_bsp.ren_pos,
        world_model_table_start=target_bsp.world_model_table_start,
        world_models=list(target_bsp.world_models),
        parse_warnings=list(getattr(target_bsp, "parse_warnings", []) or []),
    )


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


def _collision_model_names(model_names: Sequence[str]) -> List[str]:
    return [f"{name}_Collision" for name in model_names]


def _collision_segment_names(model_name: str, segment_count: int) -> List[str]:
    if segment_count <= 1:
        return [f"{model_name}_Collision"]
    return [f"{model_name}_Collision{index}" for index in range(1, segment_count + 1)]


def _normalize_collision_mode(value: str) -> str:
    mode = str(value or "none").lower()
    if mode in {"none", "off", "false", "0"}:
        return "none"
    if mode in {"invisible_bsp", "collision_helper", "box", "box_approx"}:
        return "box_approx" if mode in {"box", "box_approx"} else "invisible_bsp"
    raise ValueError(f"unsupported prefab collision mode: {value!r}")


def _select_collision_box_template(
    target_bsp: bsp.BspWorld,
    target_dat_bytes: Optional[bytes],
    desired_size: Optional[Vec3] = None,
) -> bsp.WorldModelMesh:
    if not target_dat_bytes:
        raise ValueError("box collision helper requires target DAT bytes")
    candidates = [
        model
        for model in getattr(target_bsp, "world_models", []) or []
        if str(getattr(model, "name", "") or "").lower().startswith("invisiblebrush")
        and model.raw_start is not None
        and model.world_bsp_start is not None
        and len(getattr(model, "polygons", []) or []) >= 6
        and target_bsp.raw_model_bytes(target_dat_bytes, model) is not None
    ]
    if not candidates:
        raise ValueError("target level has no cloneable InvisibleBrush BSP model for box collision")
    return min(candidates, key=lambda model: _collision_template_score(model, desired_size))


def _collision_template_score(model: bsp.WorldModelMesh, desired_size: Optional[Vec3]) -> Tuple[float, float, int]:
    textures = " ".join(str(tex or "").lower() for tex in model.texture_names)
    material_penalty = 0.0 if "firethrough" in textures else 10.0
    poly_penalty = abs(len(model.polygons) - 6)
    if desired_size is None:
        shape_penalty = 0.0
    else:
        shape_penalty = _shape_score(_bounds_size(model.min_box, model.max_box), desired_size)
    return (material_penalty + shape_penalty, float(poly_penalty), len(model.polygons))


def _shape_score(source_size: Vec3, desired_size: Vec3) -> float:
    score = 0.0
    source_sorted = sorted((max(abs(v), 1.0) for v in source_size))
    desired_sorted = sorted((max(abs(v), 1.0) for v in desired_size))
    for source, desired in zip(source_sorted, desired_sorted):
        score += abs((source / desired) if source > desired else (desired / source))
    return score


def _bounds_scale(
    source_min: Vec3,
    source_max: Vec3,
    target_min: Vec3,
    target_max: Vec3,
) -> Vec3:
    result = []
    for axis in range(3):
        source_size = float(source_max[axis]) - float(source_min[axis])
        target_size = float(target_max[axis]) - float(target_min[axis])
        if abs(source_size) < 1.0e-6:
            result.append(1.0)
        else:
            result.append(target_size / source_size)
    return (float(result[0]), float(result[1]), float(result[2]))


def _bounds_size(min_box: Vec3, max_box: Vec3) -> Vec3:
    return (
        float(max_box[0]) - float(min_box[0]),
        float(max_box[1]) - float(min_box[1]),
        float(max_box[2]) - float(min_box[2]),
    )


def _collision_box_size(min_box: Vec3, max_box: Vec3, thickness: float = 8.0) -> Vec3:
    return _bounds_size(*_thin_collision_bounds(min_box, max_box, thickness))


def _thin_collision_bounds(min_box: Vec3, max_box: Vec3, thickness: float = 8.0) -> Tuple[Vec3, Vec3]:
    mins = [float(v) for v in min_box]
    maxs = [float(v) for v in max_box]
    x_size = maxs[0] - mins[0]
    z_size = maxs[2] - mins[2]
    if x_size <= 0.0 or z_size <= 0.0:
        return (tuple(mins), tuple(maxs))  # type: ignore[return-value]
    thin_axis = 0 if x_size <= z_size else 2
    target_thickness = max(1.0, float(thickness))
    thickness = min(maxs[thin_axis] - mins[thin_axis], target_thickness)
    center = (mins[thin_axis] + maxs[thin_axis]) * 0.5
    mins[thin_axis] = center - thickness * 0.5
    maxs[thin_axis] = center + thickness * 0.5
    return (tuple(mins), tuple(maxs))  # type: ignore[return-value]


def _segment_collision_bounds(
    min_box: Vec3,
    max_box: Vec3,
    segment_length: float = 512.0,
) -> List[Tuple[Vec3, Vec3]]:
    mins = [float(v) for v in min_box]
    maxs = [float(v) for v in max_box]
    max_segment = max(64.0, float(segment_length))
    x_size = maxs[0] - mins[0]
    z_size = maxs[2] - mins[2]
    axis = 0 if x_size >= z_size else 2
    length = maxs[axis] - mins[axis]
    if length <= max_segment:
        return [(tuple(mins), tuple(maxs))]  # type: ignore[list-item]
    count = max(1, int((length + max_segment - 1.0) // max_segment))
    step = length / count
    segments: List[Tuple[Vec3, Vec3]] = []
    for index in range(count):
        seg_min = list(mins)
        seg_max = list(maxs)
        seg_min[axis] = mins[axis] + step * index
        seg_max[axis] = maxs[axis] if index == count - 1 else mins[axis] + step * (index + 1)
        segments.append((tuple(seg_min), tuple(seg_max)))  # type: ignore[arg-type]
    return segments


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
