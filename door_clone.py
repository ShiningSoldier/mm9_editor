"""
door_clone.py
=============

In-memory planning helpers for cloning MM9 physical doors.

A physical door is a pair of records:

    - a WorldObject controller (`Door` or `RotatingDoor`)
    - a same-named BSP submodel containing visible/colliding geometry

This module deliberately stops before DAT write-back.  It produces cloned
controller objects and copied source BSP records with enough metadata for a
later serializer phase to insert/rename the BSP submodels safely.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import bsp
import door_links


Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class DoorSubmodelClone:
    """Copied BSP source record for one cloned physical door submodel."""

    source_name: str
    new_name: str
    source_model: bsp.WorldModelMesh
    raw_bytes: bytes
    delta: Vec3 = (0.0, 0.0, 0.0)
    source_pivot: Vec3 = (0.0, 0.0, 0.0)
    target_pivot: Vec3 = (0.0, 0.0, 0.0)
    yaw_radians: float = 0.0
    scale: Vec3 = (1.0, 1.0, 1.0)
    info_flags_override: Optional[int] = None


@dataclass(frozen=True)
class DoorClonePlan:
    """In-memory result of cloning a physical door controller/submodel set."""

    source_name: str
    new_name: str
    objects: List[object]
    submodels: List[DoorSubmodelClone]

    @property
    def paired(self) -> bool:
        return len(self.objects) == 2 and len(self.submodels) == 2

    @property
    def primary_object(self) -> object:
        return self.objects[0]

    @property
    def pair_object(self) -> Optional[object]:
        return self.objects[1] if self.paired else None


def _as_vec3(value: object, prop_name: str) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{prop_name} must be a 3-vector, got {value!r}")
    return (float(value[0]), float(value[1]), float(value[2]))


def _add_vec3(value: object, delta: Vec3, prop_name: str) -> Vec3:
    x, y, z = _as_vec3(value, prop_name)
    dx, dy, dz = delta
    return (x + dx, y + dy, z + dz)


def transform_point(
    point: object,
    source_pivot: Vec3,
    target_pivot: Vec3,
    yaw_radians: float,
    scale: Vec3 = (1.0, 1.0, 1.0),
) -> Vec3:
    x, y, z = _as_vec3(point, "point")
    sx, sy, sz = source_pivot
    tx, ty, tz = target_pivot
    scx, scy, scz = _as_vec3(scale, "scale")
    dx = (x - sx) * scx
    dy = (y - sy) * scy
    dz = (z - sz) * scz
    c = math.cos(float(yaw_radians))
    s = math.sin(float(yaw_radians))
    return (tx + dx * c + dz * s, ty + dy, tz - dx * s + dz * c)


def rotate_vector_y(vector: object, yaw_radians: float) -> Vec3:
    x, y, z = _as_vec3(vector, "vector")
    c = math.cos(float(yaw_radians))
    s = math.sin(float(yaw_radians))
    return (x * c + z * s, y, -x * s + z * c)


def transform_projection_vector(vector: object, yaw_radians: float, scale: Vec3 = (1.0, 1.0, 1.0)) -> Vec3:
    x, y, z = _as_vec3(vector, "vector")
    sx, sy, sz = _as_vec3(scale, "scale")
    sx = sx if abs(sx) > 1.0e-6 else 1.0
    sy = sy if abs(sy) > 1.0e-6 else 1.0
    sz = sz if abs(sz) > 1.0e-6 else 1.0
    return rotate_vector_y((x / sx, y / sy, z / sz), yaw_radians)


def transform_normal_vector(vector: object, yaw_radians: float, scale: Vec3 = (1.0, 1.0, 1.0)) -> Vec3:
    x, y, z = transform_projection_vector(vector, yaw_radians, scale)
    length = math.sqrt(x * x + y * y + z * z)
    if length <= 1.0e-6:
        return (x, y, z)
    return (x / length, y / length, z / length)


def transform_bounds(
    min_box: Vec3,
    max_box: Vec3,
    source_pivot: Vec3,
    target_pivot: Vec3,
    yaw_radians: float,
    scale: Vec3 = (1.0, 1.0, 1.0),
) -> Tuple[Vec3, Vec3]:
    corners = [
        transform_point((x, y, z), source_pivot, target_pivot, yaw_radians, scale=scale)
        for x in (float(min_box[0]), float(max_box[0]))
        for y in (float(min_box[1]), float(max_box[1]))
        for z in (float(min_box[2]), float(max_box[2]))
    ]
    return (
        (min(p[0] for p in corners), min(p[1] for p in corners), min(p[2] for p in corners)),
        (max(p[0] for p in corners), max(p[1] for p in corners), max(p[2] for p in corners)),
    )


def _has_property(obj: object, name: str) -> bool:
    return any(getattr(prop, "name", None) == name for prop in getattr(obj, "props", []) or [])


def _set_if_present(obj: object, name: str, value: object) -> None:
    if _has_property(obj, name):
        obj.set(name, value)


def _nonzero_vec3(value: object) -> bool:
    try:
        return any(abs(v) > 1.0e-6 for v in _as_vec3(value, "vector"))
    except ValueError:
        return False


def _rotate_yaw_property(value: object, yaw_delta: float) -> Tuple[float, float, float, float]:
    try:
        vals = list(value)
    except Exception:
        vals = []
    vals = (vals + [0.0, 0.0, 0.0, 0.0])[:4]
    vals[1] = float(vals[1]) + float(yaw_delta)
    return tuple(float(v) for v in vals)


def _transform_controller(obj: object, source_pivot: Vec3, target_pivot: Vec3, yaw_radians: float) -> None:
    for prop_name in ("Pos", "RotationPoint"):
        if _has_property(obj, prop_name):
            obj.set(prop_name, transform_point(obj.get(prop_name), source_pivot, target_pivot, yaw_radians))

    if _has_property(obj, "Rotation"):
        obj.set("Rotation", _rotate_yaw_property(obj.get("Rotation"), yaw_radians))

    # In shipped MM9 doors, SoundPos is often (0, 0, 0), which appears to mean
    # "use the controller position".  Preserve that sentinel instead of moving
    # it into world space.
    if _has_property(obj, "SoundPos") and _nonzero_vec3(obj.get("SoundPos")):
        obj.set("SoundPos", transform_point(obj.get("SoundPos"), source_pivot, target_pivot, yaw_radians))


def derive_pair_clone_name(source_name: str, pair_name: str, new_name: str) -> str:
    """
    Derive a cloned mate name from common MM9 double-door naming patterns.

    Examples:
      ChurchdoorR -> ChurchdoorL, new ChurchdoorCloneR => ChurchdoorCloneL
      Door1       -> Door2,       new TestDoor1        => TestDoor2
    """
    source = str(source_name or "")
    pair = str(pair_name or "")
    new = str(new_name or "")

    if source and pair and new:
        for source_suffix, pair_suffix in (("Left", "Right"), ("Right", "Left")):
            if source.lower().endswith(source_suffix.lower()) and pair.lower().endswith(pair_suffix.lower()):
                if new.lower().endswith(source_suffix.lower()):
                    return new[: -len(source_suffix)] + pair[-len(pair_suffix):]
                if new.lower().endswith(f"{source_suffix}clone".lower()):
                    return new[: -len(f"{source_suffix}Clone")] + f"Clone{pair[-len(pair_suffix):]}"

        source_tail = source[-1]
        pair_tail = pair[-1]
        if source_tail.lower() in {"l", "r"} and pair_tail.lower() in {"l", "r"}:
            if source_tail.lower() != pair_tail.lower() and new[-1:].lower() == source_tail.lower():
                return new[:-1] + pair_tail

        source_match = re.search(r"(\d+)$", source)
        pair_match = re.search(r"(\d+)$", pair)
        if source_match and pair_match and new.endswith(source_match.group(1)):
            return new[: -len(source_match.group(1))] + pair_match.group(1)

    return f"{new}_pair"


def _casefold_names(names: Sequence[str]) -> set[str]:
    return {str(name or "").lower() for name in names if str(name or "")}


def _validate_new_names(objects, bsp_world: bsp.BspWorld, new_names: Sequence[str]) -> None:
    if any(not str(name or "").strip() for name in new_names):
        raise ValueError("new door names must be non-empty")

    lowered = [name.lower() for name in new_names]
    if len(set(lowered)) != len(lowered):
        raise ValueError(f"new door names must be unique: {', '.join(new_names)}")

    existing_objects = _casefold_names(
        obj.get("Name")
        for obj in (objects or [])
        if hasattr(obj, "get")
    )
    existing_models = _casefold_names(
        getattr(model, "name", "")
        for model in (getattr(bsp_world, "world_models", []) or [])
    )
    for name in new_names:
        key = name.lower()
        if key in existing_objects:
            raise ValueError(f"object named {name!r} already exists")
        if key in existing_models:
            raise ValueError(f"BSP model named {name!r} already exists")


def existing_physical_names(objects, bsp_world: bsp.BspWorld) -> set[str]:
    """Return case-insensitive object/BSP names that would collide with a clone."""
    return _casefold_names(
        obj.get("Name")
        for obj in (objects or [])
        if hasattr(obj, "get")
    ) | _casefold_names(
        getattr(model, "name", "")
        for model in (getattr(bsp_world, "world_models", []) or [])
    )


def suggest_clone_name(objects, bsp_world: bsp.BspWorld, source_name: str, pair_name: str = "") -> str:
    """Return a non-colliding default clone name for *source_name*."""
    existing = existing_physical_names(objects, bsp_world)
    source = str(source_name or "")
    pair = str(pair_name or "")
    source_match = re.search(r"(\d+)$", source)
    pair_match = re.search(r"(\d+)$", pair)
    if (source_match and pair_match
            and source[:source_match.start(1)].lower() == pair[:pair_match.start(1)].lower()):
        base = f"{source[:source_match.start(1)]}Clone{source_match.group(1)}"
    else:
        base = ""
    for suffix in ("Left", "Right"):
        if base:
            break
        if source.lower().endswith(suffix.lower()):
            base = f"{source[:-len(suffix)]}Clone{source[-len(suffix):]}"
            break
    if base:
        pass
    elif len(source) > 1 and source[-1].lower() in {"l", "r"}:
        base = f"{source[:-1]}Clone{source[-1]}"
    else:
        base = f"{source}Clone"
    if base.lower() not in existing:
        return base
    index = 2
    while f"{base}{index}".lower() in existing:
        index += 1
    return f"{base}{index}"


def translated_model_clone(submodel: DoorSubmodelClone) -> bsp.WorldModelMesh:
    """Return a display/save-preview copy of a cloned BSP submodel."""
    model = copy.deepcopy(submodel.source_model)
    model.name = submodel.new_name
    model.min_box, model.max_box = transform_bounds(
        model.min_box,
        model.max_box,
        submodel.source_pivot,
        submodel.target_pivot,
        submodel.yaw_radians,
        scale=submodel.scale,
    )
    model.translation = transform_point(
        model.translation,
        submodel.source_pivot,
        submodel.target_pivot,
        submodel.yaw_radians,
        scale=submodel.scale,
    )
    model.points = [
        transform_point(
            point,
            submodel.source_pivot,
            submodel.target_pivot,
            submodel.yaw_radians,
            scale=submodel.scale,
        )
        for point in model.points
    ]
    for surface in model.surfaces:
        surface.uv_o = transform_point(
            surface.uv_o,
            submodel.source_pivot,
            submodel.target_pivot,
            submodel.yaw_radians,
            scale=submodel.scale,
        )
        surface.uv_p = transform_projection_vector(surface.uv_p, submodel.yaw_radians, submodel.scale)
        surface.uv_q = transform_projection_vector(surface.uv_q, submodel.yaw_radians, submodel.scale)
    model.raw_start = None
    model.raw_end = None
    model.next_world_item = None
    model.world_bsp_start = None
    model.world_bsp_end = None
    return model


def build_preview_bsp(
    bsp_world: bsp.BspWorld,
    clone_plans: Sequence[DoorClonePlan],
) -> bsp.BspWorld:
    """Return a BSP world with pending cloned door submodels appended."""
    preview = _shallow_bsp_with_original_models(bsp_world)
    for plan in clone_plans or []:
        for submodel in plan.submodels:
            preview.world_models.append(translated_model_clone(submodel))
    return preview


def _shallow_bsp_with_original_models(bsp_world: bsp.BspWorld) -> bsp.BspWorld:
    return bsp.BspWorld(
        version=bsp_world.version,
        world_info=bsp_world.world_info,
        obj_pos=bsp_world.obj_pos,
        ren_pos=bsp_world.ren_pos,
        world_model_table_start=bsp_world.world_model_table_start,
        world_models=list(bsp_world.world_models),
        parse_warnings=list(getattr(bsp_world, "parse_warnings", []) or []),
    )


def _clone_controller(source_obj: object, new_name: str, source_pivot: Vec3, target_pivot: Vec3, yaw_radians: float) -> object:
    cloned = copy.deepcopy(source_obj)
    cloned.set("Name", new_name)
    _transform_controller(cloned, source_pivot, target_pivot, yaw_radians)
    return cloned


def _clone_submodel(
    source_dat: bytes,
    bsp_world: bsp.BspWorld,
    model: bsp.WorldModelMesh,
    new_name: str,
    delta: Vec3,
    source_pivot: Vec3,
    target_pivot: Vec3,
    yaw_radians: float,
) -> DoorSubmodelClone:
    raw = bsp_world.raw_model_bytes(source_dat, model)
    if raw is None:
        raise ValueError(f"BSP model {model.name!r} has no recoverable source byte range")
    return DoorSubmodelClone(
        source_name=model.name,
        new_name=new_name,
        source_model=model,
        raw_bytes=bytes(raw),
        delta=delta,
        source_pivot=source_pivot,
        target_pivot=target_pivot,
        yaw_radians=float(yaw_radians),
    )


def build_clone_plan(
    objects,
    bsp_world: bsp.BspWorld,
    source_dat: bytes,
    source_name: str,
    new_name: str,
    target_pos: Optional[Sequence[float]] = None,
    target_yaw: float = 0.0,
    include_pair: bool = True,
) -> DoorClonePlan:
    """
    Build an in-memory clone plan for a physical door.

    `target_pos`, when supplied, becomes the cloned primary controller's `Pos`;
    paired doors keep their original offset from the primary door.  This mirrors
    how double doors are authored in STURMFORDCITY: each leaf has its own
    controller position and rotation point.
    """
    link = door_links.find_physical_door_link(objects, bsp_world, source_name)
    if link is None:
        raise ValueError(f"{source_name!r} is not a physical door with a BSP submodel")

    pair_new_name: Optional[str] = None
    if include_pair and link.is_paired:
        pair_new_name = derive_pair_clone_name(link.name, link.pair_name, new_name)

    planned_names = [new_name] + ([pair_new_name] if pair_new_name else [])
    _validate_new_names(objects, bsp_world, planned_names)

    source_pos = _as_vec3(link.obj.get("Pos"), "Pos")
    if target_pos is None:
        target_pivot = source_pos
    else:
        target_pivot = _as_vec3(target_pos, "target_pos")
    delta = (target_pivot[0] - source_pos[0], target_pivot[1] - source_pos[1], target_pivot[2] - source_pos[2])

    cloned_objects = [_clone_controller(link.obj, new_name, source_pos, target_pivot, target_yaw)]
    cloned_submodels = [_clone_submodel(
        source_dat, bsp_world, link.model, new_name, delta,
        source_pos, target_pivot, target_yaw,
    )]

    if pair_new_name:
        pair_obj = objects[link.pair_object_index]
        pair_model = link.pair_model
        cloned_pair = _clone_controller(pair_obj, pair_new_name, source_pos, target_pivot, target_yaw)
        _set_if_present(cloned_objects[0], "DoubleDoorName", pair_new_name)
        _set_if_present(cloned_pair, "DoubleDoorName", new_name)
        cloned_objects.append(cloned_pair)
        cloned_submodels.append(_clone_submodel(
            source_dat, bsp_world, pair_model, pair_new_name, delta,
            source_pos, target_pivot, target_yaw,
        ))

    return DoorClonePlan(
        source_name=link.name,
        new_name=new_name,
        objects=cloned_objects,
        submodels=cloned_submodels,
    )
