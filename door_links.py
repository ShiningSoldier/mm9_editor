"""
door_links.py
=============

Read-only helpers for matching MM9 door controller objects to physical BSP
submodels.  Many doors are represented twice in a DAT:

    - a WorldObject (`Door` or `RotatingDoor`) that stores logic/properties
    - a same-named BSP world model that stores visible/colliding geometry

These helpers are deliberately small and conservative; mutation/cloning lives
in later phases once BSP serialization is safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import bsp


DOOR_CLASSES = {"Door", "RotatingDoor"}


@dataclass(frozen=True)
class PhysicalDoorLink:
    """A door controller object linked to its same-named BSP submodel."""

    name: str
    object_index: int
    obj: object
    model: bsp.WorldModelMesh
    pair_name: str = ""
    pair_object_index: Optional[int] = None
    pair_model: Optional[bsp.WorldModelMesh] = None

    @property
    def class_name(self) -> str:
        return str(getattr(self.obj, "type_str", "") or "")

    @property
    def is_rotating(self) -> bool:
        return self.class_name == "RotatingDoor"

    @property
    def is_paired(self) -> bool:
        return bool(self.pair_name and self.pair_object_index is not None and self.pair_model is not None)


def _object_name(obj: object) -> str:
    try:
        return str(obj.get("Name") or "")
    except Exception:
        return ""


def _object_type(obj: object) -> str:
    return str(getattr(obj, "type_str", "") or "")


def _object_string(obj: object, prop_name: str) -> str:
    try:
        return str(obj.get(prop_name) or "")
    except Exception:
        return ""


def index_objects_by_name(objects) -> Dict[str, List[int]]:
    """Return case-insensitive object-name -> world object indices."""
    out: Dict[str, List[int]] = {}
    for index, obj in enumerate(objects or []):
        name = _object_name(obj)
        if not name:
            continue
        out.setdefault(name.lower(), []).append(index)
    return out


def index_bsp_models_by_name(bsp_world: bsp.BspWorld) -> Dict[str, List[bsp.WorldModelMesh]]:
    """Return case-insensitive BSP model-name -> model list."""
    out: Dict[str, List[bsp.WorldModelMesh]] = {}
    for model in getattr(bsp_world, "world_models", []) or []:
        name = str(getattr(model, "name", "") or "")
        if not name:
            continue
        out.setdefault(name.lower(), []).append(model)
    return out


def is_door_controller(obj: object) -> bool:
    return _object_type(obj) in DOOR_CLASSES


def build_physical_door_links(objects, bsp_world: bsp.BspWorld) -> List[PhysicalDoorLink]:
    """
    Match `Door`/`RotatingDoor` objects to same-named BSP submodels.

    Duplicate names are handled conservatively by taking the first object/model
    for pair lookup.  Shipped MM9 physical door names observed so far are unique.
    """
    object_index = index_objects_by_name(objects)
    model_index = index_bsp_models_by_name(bsp_world)

    links: List[PhysicalDoorLink] = []
    for index, obj in enumerate(objects or []):
        if not is_door_controller(obj):
            continue
        name = _object_name(obj)
        if not name:
            continue
        model = next(iter(model_index.get(name.lower(), [])), None)
        if model is None:
            continue

        pair_name = _object_string(obj, "DoubleDoorName")
        pair_obj_index: Optional[int] = None
        pair_model: Optional[bsp.WorldModelMesh] = None
        if pair_name:
            pair_obj_index = next(iter(object_index.get(pair_name.lower(), [])), None)
            pair_model = next(iter(model_index.get(pair_name.lower(), [])), None)

        links.append(PhysicalDoorLink(
            name=name,
            object_index=index,
            obj=obj,
            model=model,
            pair_name=pair_name,
            pair_object_index=pair_obj_index,
            pair_model=pair_model,
        ))
    return links


def find_physical_door_link(objects, bsp_world: bsp.BspWorld, name: str) -> Optional[PhysicalDoorLink]:
    """Return a physical door link by controller/model name."""
    key = str(name or "").lower()
    for link in build_physical_door_links(objects, bsp_world):
        if link.name.lower() == key:
            return link
    return None
