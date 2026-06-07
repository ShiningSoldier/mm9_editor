"""
project.py
==========

In-memory representation of a multi-level MM9 mod project.

A Project owns a list of LevelEdits (one per loaded .DAT). Each LevelEdit
has the original World (read-only reference), a list of pending Operations
(add / move / delete), and a derived view that shows what the World would
look like with all pending ops applied.

This is what the GUI works with. Operations are reified so we can:
- Undo/redo
- Show an explicit diff before saving
- Stage RUDE registrations alongside DAT edits

Saving is explicit: project.save_plan() returns a SavePlan that the user
reviews (in the editor's diff dialog), and Project.execute(plan) writes
the actual files.
"""

from __future__ import annotations

import copy
import json
import os
import struct
import tempfile
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
import mm9_patch as patcher
from core import bsp
from features.doors import bsp_writer as door_bsp_writer
from features.doors import clone as door_clone
from features.doors import validation as door_clone_validation
from features.dat_editing import bsp_compile
from features.dat_editing import mesh_import
from features.dat_editing import output_validation
from features.dat_editing import replace_submodel
from features.dat_editing import vertex_edit
from features.prefabs import import_static as prefab_import
from features.prefabs import validation as prefab_import_validation


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# --------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------

@dataclass
class AddOp:
    template: patcher.WorldObject     # full deep-copied template
    overrides: Dict[str, Any] = field(default_factory=dict)
    # optional fresh-NPC registration to perform on save:
    rude: Optional[Dict[str, Any]] = None     # {npc_nbr, name, blurb, lines}

    def apply_to(self, world: patcher.World) -> patcher.WorldObject:
        new_obj = copy.deepcopy(self.template)
        for k, v in self.overrides.items():
            new_obj.set(k, v)
        world.objects.append(new_obj)
        return new_obj

    def summary(self) -> str:
        ts = self.template.type_str
        name = self.overrides.get("Name") or self.template.get("Name")
        pos = self.overrides.get("Pos") or self.template.get("Pos") or (0, 0, 0)
        rude = " + RUDE" if self.rude else ""
        return f"+ {ts:20s} {name}  at ({pos[0]:.0f}, {pos[1]:.0f}, {pos[2]:.0f}){rude}"


@dataclass
class MoveOp:
    target_index: int                  # index of the existing object in World.objects
    new_pos: Tuple[float, float, float]
    new_rot: Optional[Tuple[float, float, float, float]] = None

    def apply_to(self, world: patcher.World) -> None:
        obj = world.objects[self.target_index]
        obj.set("Pos", list(self.new_pos))
        if self.new_rot is not None:
            obj.set("Rotation", list(self.new_rot))

    def summary(self) -> str:
        return f"~ move object[{self.target_index}] to {self.new_pos}"


@dataclass
class DeleteOp:
    target_index: int

    def apply_to(self, world: patcher.World) -> None:
        # Mark for deletion; actual removal happens in materialize() in reverse order
        pass

    def summary(self) -> str:
        return f"- delete object[{self.target_index}]"


@dataclass
class EditOp:
    target_index: int
    overrides: Dict[str, Any]

    def apply_to(self, world: patcher.World) -> None:
        obj = world.objects[self.target_index]
        for k, v in self.overrides.items():
            obj.set(k, v)

    def summary(self) -> str:
        keys = ", ".join(self.overrides.keys())
        return f"~ edit object[{self.target_index}] ({keys})"


@dataclass
class CloneDoorOp:
    source_name: str
    new_name: str
    target_pos: Optional[Tuple[float, float, float]] = None
    target_yaw: float = 0.0
    include_pair: bool = True

    def build_plan(self, level: "LevelEdit", objects) -> door_clone.DoorClonePlan:
        if not getattr(level, "_raw_bytes", None):
            raise ValueError("door cloning requires the source DAT bytes")
        bsp_world = level.get_bsp()
        if bsp_world is None:
            raise ValueError("door cloning requires parsed BSP geometry")
        return door_clone.build_clone_plan(
            objects,
            bsp_world,
            level._raw_bytes,
            self.source_name,
            self.new_name,
            target_pos=self.target_pos,
            target_yaw=self.target_yaw,
            include_pair=self.include_pair,
        )

    def apply_to(self, level: "LevelEdit", world: patcher.World) -> List[patcher.WorldObject]:
        plan = self.build_plan(level, world.objects)
        world.objects.extend(copy.deepcopy(plan.objects))
        return plan.objects

    def pending_object_count(self, level: "LevelEdit", objects) -> int:
        return len(self.build_plan(level, objects).objects)

    def retarget_from_object(
        self,
        level: "LevelEdit",
        objects,
        object_offset: int,
        new_pos: Tuple[float, float, float],
    ) -> None:
        plan = self.build_plan(level, objects)
        if not (0 <= object_offset < len(plan.objects)):
            return
        old_pos = plan.objects[object_offset].get("Pos")
        primary_pos = plan.objects[0].get("Pos")
        if old_pos is None or primary_pos is None:
            return
        delta = (
            float(new_pos[0]) - float(old_pos[0]),
            float(new_pos[1]) - float(old_pos[1]),
            float(new_pos[2]) - float(old_pos[2]),
        )
        self.target_pos = (
            float(primary_pos[0]) + delta[0],
            float(primary_pos[1]) + delta[1],
            float(primary_pos[2]) + delta[2],
        )

    def rerotate_from_object(
        self,
        level: "LevelEdit",
        objects,
        object_offset: int,
        new_rot: Tuple[float, float, float, float],
    ) -> None:
        plan = self.build_plan(level, objects)
        if not (0 <= object_offset < len(plan.objects)):
            return
        old_rot = plan.objects[object_offset].get("Rotation")
        if old_rot is None:
            return
        self.target_yaw = float(self.target_yaw) + (float(new_rot[1]) - float(old_rot[1]))

    def summary(self) -> str:
        pos = self.target_pos
        target = "" if pos is None else f" at ({pos[0]:.0f}, {pos[1]:.0f}, {pos[2]:.0f})"
        pair = " + pair" if self.include_pair else ""
        yaw = "" if abs(float(self.target_yaw)) < 1.0e-6 else f" yaw {self.target_yaw:.2f}"
        return f"+ clone door {self.source_name} -> {self.new_name}{pair}{target}{yaw}"


@dataclass
class ImportPrefabBspOp:
    prefab_path: str
    new_name: str
    target_pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    target_yaw: float = 0.0
    include_roles: Optional[Tuple[str, ...]] = None
    collision_mode: str = "none"
    collision_thickness: float = 8.0
    collision_segment_length: float = 512.0

    def build_plan(self, level: "LevelEdit") -> prefab_import.PrefabBspImportPlan:
        bsp_world = level.get_bsp()
        if bsp_world is None:
            raise ValueError("prefab BSP import requires parsed target BSP geometry")
        return prefab_import.build_static_import_plan(
            bsp_world,
            self.prefab_path,
            new_name=self.new_name,
            target_pos=self.target_pos,
            target_yaw=self.target_yaw,
            include_roles=self.include_roles,
            collision_mode=_normalized_prefab_collision_mode(self.collision_mode),
            collision_thickness=float(self.collision_thickness),
            collision_segment_length=float(self.collision_segment_length),
            target_dat_bytes=level.source_bytes(),
        )

    def object_names(self, level: Optional["LevelEdit"] = None) -> List[str]:
        names = [self.new_name]
        if _normalized_prefab_collision_mode(self.collision_mode) in {"invisible_bsp", "box_approx"}:
            collision_names = self._collision_submodel_names(level)
            names.extend(collision_names or [f"{self.new_name}_Collision"])
        return names

    def apply_to(self, world: patcher.World, level: Optional["LevelEdit"] = None) -> List[patcher.WorldObject]:
        wanted = {name.lower() for name in self.object_names(level)}
        for obj in world.objects:
            obj_name = (obj.get("Name") or "").lower()
            if obj_name in wanted:
                raise ValueError(f"object named {obj.get('Name')!r} already exists")
        template = _find_static_worldobject_template(world)
        new_obj = _make_prefab_worldobject(
            template,
            self.new_name,
            self.target_pos,
            self.target_yaw,
            visible=1,
            type_str="WorldObject",
        )
        created = [new_obj]
        world.objects.append(new_obj)
        if _normalized_prefab_collision_mode(self.collision_mode) in {"invisible_bsp", "box_approx"}:
            collision_template = _find_object_template(world, "InvisibleBrush") or template
            for collision_name, collision_pos in self._collision_object_specs(level):
                collision_obj = _make_prefab_worldobject(
                    collision_template,
                    collision_name,
                    collision_pos,
                    0.0,
                    visible=0,
                    type_str="InvisibleBrush",
                )
                created.append(collision_obj)
                world.objects.append(collision_obj)
        return created

    def summary(self) -> str:
        pos = self.target_pos
        roles = "" if not self.include_roles else f" roles={','.join(self.include_roles)}"
        yaw = "" if abs(float(self.target_yaw)) < 1.0e-6 else f" yaw {self.target_yaw:.2f}"
        collision_mode = _normalized_prefab_collision_mode(self.collision_mode)
        collision = "" if collision_mode == "none" else f" collision={collision_mode}"
        thickness = (
            "" if collision_mode == "none"
            else f" thickness={float(self.collision_thickness):.1f}"
        )
        segment = (
            "" if collision_mode != "box_approx"
            else f" segment={float(self.collision_segment_length):.0f}"
        )
        return (
            f"+ import prefab BSP {os.path.basename(self.prefab_path)} -> {self.new_name}"
            f" at ({pos[0]:.0f}, {pos[1]:.0f}, {pos[2]:.0f}){yaw}{roles}{collision}{thickness}{segment}"
            )

    def _collision_submodel_names(self, level: Optional["LevelEdit"]) -> List[str]:
        if level is None:
            return []
        try:
            return [
                submodel.new_name
                for submodel in self.build_plan(level).submodels
                if "_collision" in submodel.new_name.lower()
            ]
        except Exception:
            return []

    def _collision_object_specs(self, level: Optional["LevelEdit"]) -> List[Tuple[str, Tuple[float, float, float]]]:
        if level is None:
            return [(f"{self.new_name}_Collision", tuple(float(v) for v in self.target_pos))]
        try:
            plan = self.build_plan(level)
            specs = []
            for submodel in plan.submodels:
                if "_collision" in submodel.new_name.lower():
                    model = door_clone.translated_model_clone(submodel)
                    specs.append((submodel.new_name, _bounds_center(model.min_box, model.max_box)))
            if specs:
                return specs
        except Exception:
            return [(f"{self.new_name}_Collision", tuple(float(v) for v in self.target_pos))]
        return [(f"{self.new_name}_Collision", tuple(float(v) for v in self.target_pos))]

    def helper_object(self) -> patcher.WorldObject:
        return patcher.WorldObject(
            type_str="EditorPrefabBspImport",
            props=[
                patcher.Property("Name", 0, 0, self.new_name),
                patcher.Property("Pos", 1, 0, tuple(float(v) for v in self.target_pos)),
                patcher.Property("Rotation", 7, 0, (0.0, float(self.target_yaw), 0.0, 0.0)),
                patcher.Property("PrefabPath", 0, 0, self.prefab_path),
            ],
        )


@dataclass
class ImportMeshBspOp:
    obj_path: str
    meta_path: str
    new_name: str
    target_pos: Optional[Tuple[float, float, float]] = None
    target_yaw: float = 0.0
    collision_mode: str = "none"
    collision_thickness: float = 8.0
    collision_segment_length: float = 512.0

    def build_plan(self, level: "LevelEdit") -> mesh_import.MeshBspImportPlan:
        bsp_world = level.get_bsp()
        if bsp_world is None:
            raise ValueError("mesh BSP import requires parsed target BSP geometry")
        return mesh_import.build_mesh_import_plan(
            bsp_world,
            self.obj_path,
            meta_path=self.meta_path,
            new_name=self.new_name,
            target_pos=self.target_pos,
            target_yaw=self.target_yaw,
            collision_mode=self.collision_mode,
            collision_thickness=self.collision_thickness,
            collision_segment_length=self.collision_segment_length,
        )

    def apply_to(self, world: patcher.World, level: Optional["LevelEdit"] = None) -> List[patcher.WorldObject]:
        if level is None:
            raise ValueError("mesh BSP import requires the target level")
        plan = self.build_plan(level)
        wanted = {name.lower() for name, _pos in mesh_import.object_specs(plan)}
        for obj in world.objects:
            obj_name = (obj.get("Name") or "").lower()
            if obj_name in wanted:
                raise ValueError(f"object named {obj.get('Name')!r} already exists")
        template = _find_static_worldobject_template(world)
        collision_template = _find_object_template(world, "InvisibleBrush") or template
        created: List[patcher.WorldObject] = []
        for item in plan.models:
            name = item.name
            pos = _bounds_center(item.mesh.min_box, item.mesh.max_box)
            collision = mesh_import.is_collision_model(item)
            new_obj = _make_prefab_worldobject(
                collision_template if collision else template,
                name,
                pos,
                0.0 if collision else self.target_yaw,
                visible=0 if collision else 1,
                type_str="InvisibleBrush" if collision else "WorldObject",
            )
            created.append(new_obj)
            world.objects.append(new_obj)
        return created

    def summary(self) -> str:
        target = "" if self.target_pos is None else (
            f" at ({self.target_pos[0]:.0f}, {self.target_pos[1]:.0f}, {self.target_pos[2]:.0f})"
        )
        yaw = "" if abs(float(self.target_yaw)) < 1.0e-6 else f" yaw {self.target_yaw:.2f}"
        collision = "" if str(self.collision_mode or "none").lower() in {"none", "off", "false", "0"} else f" collision={self.collision_mode}"
        return f"+ import Blender geometry {os.path.basename(self.obj_path)} -> {self.new_name}{target}{yaw}{collision}"


@dataclass
class EditBspVerticesOp:
    obj_path: str
    meta_path: str

    def build_plan(self, level: "LevelEdit") -> vertex_edit.VertexEditPlan:
        bsp_world = level.get_bsp()
        if bsp_world is None:
            raise ValueError("BSP vertex edit requires parsed target BSP geometry")
        return vertex_edit.build_vertex_edit_plan(
            bsp_world,
            level.source_bytes(),
            self.obj_path,
            meta_path=self.meta_path,
        )

    def apply_to(self, world: patcher.World, level: Optional["LevelEdit"] = None) -> List[patcher.WorldObject]:
        return []

    def summary(self) -> str:
        return f"~ edit BSP vertices from {os.path.basename(self.obj_path)}"


@dataclass
class ReplaceBspSubmodelOp:
    obj_path: str
    meta_path: str

    def build_plan(self, level: "LevelEdit") -> replace_submodel.ReplaceSubmodelPlan:
        bsp_world = level.get_bsp()
        if bsp_world is None:
            raise ValueError("BSP submodel replacement requires parsed target BSP geometry")
        return replace_submodel.build_replace_submodel_plan(
            bsp_world,
            level.source_bytes(),
            self.obj_path,
            meta_path=self.meta_path,
        )

    def apply_to(self, world: patcher.World, level: Optional["LevelEdit"] = None) -> List[patcher.WorldObject]:
        return []

    def summary(self) -> str:
        return f"~ replace BSP submodel topology from {os.path.basename(self.obj_path)}"


def _find_static_worldobject_template(world: patcher.World) -> patcher.WorldObject:
    for obj in world.objects:
        if obj.type_str == "WorldObject":
            names = {p.name for p in obj.props}
            if {"Name", "Pos", "Rotation", "Visible", "Solid", "RayHit", "BoxPhysics"} <= names:
                return obj
    for obj in world.objects:
        if obj.type_str == "WorldObject":
            return obj
    raise ValueError("target level has no WorldObject template for static prefab import")


def _find_object_template(world: patcher.World, type_str: str) -> Optional[patcher.WorldObject]:
    for obj in world.objects:
        if obj.type_str == type_str:
            return obj
    return None


def _normalized_prefab_collision_mode(value: str) -> str:
    mode = str(value or "none").lower()
    if mode in {"none", "off", "false", "0"}:
        return "none"
    if mode in {"invisible_bsp", "collision_helper"}:
        return "invisible_bsp"
    if mode in {"box", "box_approx"}:
        return "box_approx"
    return mode


def _make_prefab_worldobject(
    template: patcher.WorldObject,
    name: str,
    pos: Tuple[float, float, float],
    yaw: float,
    visible: int,
    type_str: str,
) -> patcher.WorldObject:
    obj = copy.deepcopy(template)
    obj.type_str = type_str
    _set_prop_if_present(obj, "Name", name)
    _set_prop_if_present(obj, "Pos", tuple(float(v) for v in pos))
    _set_prop_if_present(obj, "Rotation", (0.0, float(yaw), 0.0, 0.0))
    _set_prop_if_present(obj, "MoveToFloor", 0)
    _set_prop_if_present(obj, "ScriptName", "")
    _set_prop_if_present(obj, "ScriptParams", "")
    _set_prop_if_present(obj, "Visible", int(visible))
    _set_prop_if_present(obj, "Solid", 1)
    _set_prop_if_present(obj, "RayHit", 1)
    # Shipped InvisibleBrushes use the BSP brush itself for blocking and keep
    # BoxPhysics disabled.  The visible WorldObject follows the same model.
    _set_prop_if_present(obj, "BoxPhysics", 0)
    _set_prop_if_present(obj, "Alpha", 1.0)
    _set_prop_if_present(obj, "NeedsTick", 0)
    _set_prop_if_present(obj, "TouchNotify", 0)
    return obj


def _bounds_center(
    min_box: Tuple[float, float, float],
    max_box: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    return (
        (float(min_box[0]) + float(max_box[0])) * 0.5,
        (float(min_box[1]) + float(max_box[1])) * 0.5,
        (float(min_box[2]) + float(max_box[2])) * 0.5,
    )


def _set_prop_if_present(obj: patcher.WorldObject, name: str, value: Any) -> None:
    if any(p.name == name for p in obj.props):
        obj.set(name, value)


# --------------------------------------------------------------------------
# Per-level edit set
# --------------------------------------------------------------------------

# Where the level's bytes come from.
SOURCE_REZ  = "rez"    # path is "<rez_path>::<virtual_path>", e.g.
                       # "C:/.../data/WORLDS.REZ::WORLDS/BOOTCAMP"


@dataclass
class LevelEdit:
    path:        str                    # see source_kind for interpretation
    source_kind: str = SOURCE_REZ
    rez_path:    Optional[str] = None   # filled when source_kind == SOURCE_REZ
    rez_vpath:   Optional[str] = None   # filled when source_kind == SOURCE_REZ
    output:      Optional[str] = None   # explicit write target if any
    backup_path: Optional[str] = None   # copy made when the source REZ opened
    world:       Optional[patcher.World] = None
    ops:         List[Any] = field(default_factory=list)
    redo_ops:    List[Any] = field(default_factory=list)
    display_name: str = ""              # short label for tabs / dropdowns
    _next_id:    int = 0
    # Cached BSP geometry (lazily parsed on first map-view refresh)
    bsp:         Optional[Any] = None

    def load(self) -> None:
        if self.world is not None and getattr(self, "_raw_bytes", None):
            return
        if self.source_kind == SOURCE_REZ:
            assert self.rez_path and self.rez_vpath
            data = self.source_bytes()
            # Detect format: only DAT (version 66) is editable here.
            if len(data) < 4 or struct.unpack_from("<I", data, 0)[0] != 66:
                raise ValueError(
                    f"{self.rez_vpath} is not a v66 .DAT — the editor can only "
                    f"open compiled level files. (For .ED files use DEdit.)")
            if self.world is not None:
                if not self.display_name:
                    self.display_name = self.rez_vpath
                return
            # Use a tempfile so mm9_patch.World.load (which only takes a path) works.
            fd, tmp = tempfile.mkstemp(prefix="mm9_rez_", suffix=".DAT")
            os.close(fd)
            with open(tmp, "wb") as f: f.write(data)
            try:
                self.world = patcher.World.load(tmp)
            finally:
                try: os.remove(tmp)
                except OSError: pass
            self._raw_bytes = data
            if not self.display_name:
                self.display_name = self.rez_vpath
        else:
            raise ValueError(f"unknown source_kind {self.source_kind!r}")

    def source_bytes(self) -> bytes:
        """Return the original level DAT bytes, reloading them if needed."""
        data = getattr(self, "_raw_bytes", None)
        if data:
            return data
        if self.source_kind == SOURCE_REZ:
            assert self.rez_path and self.rez_vpath
            import sys
            here = os.path.dirname(os.path.abspath(__file__))
            if here not in sys.path: sys.path.insert(0, here)
            from core import rezmgr
            with rezmgr.RezReader(self.rez_path) as r:
                data = r.extract_to_bytes(self.rez_vpath)
            self._raw_bytes = data
            return data
        raise ValueError(f"unknown source_kind {self.source_kind!r}")

    def get_bsp(self):
        """Lazily parse the level's BSP geometry; cached after the first call."""
        if self.bsp is not None:
            return self.bsp
        try:
            data = self.source_bytes()
        except Exception:
            return None
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path: sys.path.insert(0, here)
        from core import bsp as bsp_mod
        try:
            self.bsp = bsp_mod.parse(data)
        except Exception:
            self.bsp = None
        return self.bsp

    def materialize(self) -> patcher.World:
        """Return a fresh World with all pending ops applied."""
        assert self.world is not None
        w = copy.deepcopy(self.world)
        deletes = []
        for op in self.ops:
            if isinstance(op, DeleteOp):
                deletes.append(op.target_index)
            elif isinstance(op, CloneDoorOp):
                op.apply_to(self, w)
            elif isinstance(op, ImportPrefabBspOp):
                op.apply_to(w, self)
            elif isinstance(op, ImportMeshBspOp):
                op.apply_to(w, self)
            elif isinstance(op, EditBspVerticesOp):
                op.apply_to(w, self)
            elif isinstance(op, ReplaceBspSubmodelOp):
                op.apply_to(w, self)
            else:
                op.apply_to(w)
        for idx in sorted(deletes, reverse=True):
            del w.objects[idx]
        return w

    def editor_materialize(self) -> patcher.World:
        """Return materialized WorldObjects plus editor-only prefab import handles."""
        return self.materialize()

    def materialized_object_count(self) -> int:
        return len(self.materialize().objects)

    def door_clone_plans(self) -> List[door_clone.DoorClonePlan]:
        """Return BSP/controller clone plans for pending CloneDoorOps."""
        assert self.world is not None
        w = copy.deepcopy(self.world)
        deletes = []
        plans: List[door_clone.DoorClonePlan] = []
        for op in self.ops:
            if isinstance(op, DeleteOp):
                deletes.append(op.target_index)
                continue
            if isinstance(op, CloneDoorOp):
                plan = op.build_plan(self, w.objects)
                plans.append(plan)
                w.objects.extend(copy.deepcopy(plan.objects))
                continue
            if isinstance(op, ImportPrefabBspOp):
                op.apply_to(w, self)
                continue
            if isinstance(op, ImportMeshBspOp):
                op.apply_to(w, self)
                continue
            if isinstance(op, EditBspVerticesOp):
                op.apply_to(w, self)
                continue
            if isinstance(op, ReplaceBspSubmodelOp):
                op.apply_to(w, self)
                continue
            op.apply_to(w)
        for idx in sorted(deletes, reverse=True):
            del w.objects[idx]
        return plans

    def prefab_import_plans(self) -> List[prefab_import.PrefabBspImportPlan]:
        """Return BSP import plans for pending static prefab imports."""
        base = self.get_bsp()
        if base is None:
            return []
        working_bsp = base
        plans: List[prefab_import.PrefabBspImportPlan] = []
        for op in self.ops:
            if not isinstance(op, ImportPrefabBspOp):
                continue
            plan = prefab_import.build_static_import_plan(
                working_bsp,
                op.prefab_path,
                new_name=op.new_name,
                target_pos=op.target_pos,
                target_yaw=op.target_yaw,
                include_roles=op.include_roles,
                collision_mode=_normalized_prefab_collision_mode(op.collision_mode),
                collision_thickness=float(op.collision_thickness),
                collision_segment_length=float(op.collision_segment_length),
                target_dat_bytes=self.source_bytes(),
            )
            plans.append(plan)
            working_bsp = prefab_import.build_preview_bsp(working_bsp, [plan])
        return plans

    def mesh_import_plans(self) -> List[mesh_import.MeshBspImportPlan]:
        """Return preview plans for pending Blender OBJ mesh imports."""
        base = self.get_bsp()
        if base is None:
            return []
        working_bsp = base
        plans: List[mesh_import.MeshBspImportPlan] = []
        for op in self.ops:
            if not isinstance(op, ImportMeshBspOp):
                continue
            plan = mesh_import.build_mesh_import_plan(
                working_bsp,
                op.obj_path,
                meta_path=op.meta_path,
                new_name=op.new_name,
                target_pos=op.target_pos,
                target_yaw=op.target_yaw,
                collision_mode=op.collision_mode,
                collision_thickness=op.collision_thickness,
                collision_segment_length=op.collision_segment_length,
            )
            plans.append(plan)
            working_bsp = mesh_import.build_preview_bsp(working_bsp, [plan])
        return plans

    def vertex_edit_plans(self) -> List[vertex_edit.VertexEditPlan]:
        base = self.get_bsp()
        if base is None:
            return []
        working_bsp = base
        plans: List[vertex_edit.VertexEditPlan] = []
        for op in self.ops:
            if not isinstance(op, EditBspVerticesOp):
                continue
            plan = vertex_edit.build_vertex_edit_plan(
                working_bsp,
                self.source_bytes(),
                op.obj_path,
                meta_path=op.meta_path,
            )
            plans.append(plan)
            working_bsp = vertex_edit.build_preview_bsp(working_bsp, [plan])
        return plans

    def replace_submodel_plans(self) -> List[replace_submodel.ReplaceSubmodelPlan]:
        base = self.get_bsp()
        if base is None:
            return []
        working_bsp = base
        plans: List[replace_submodel.ReplaceSubmodelPlan] = []
        for op in self.ops:
            if not isinstance(op, ReplaceBspSubmodelOp):
                continue
            plan = replace_submodel.build_replace_submodel_plan(
                working_bsp,
                self.source_bytes(),
                op.obj_path,
                meta_path=op.meta_path,
            )
            plans.append(plan)
            working_bsp = replace_submodel.build_preview_bsp(working_bsp, [plan])
        return plans

    def preview_bsp(self):
        """Return BSP geometry plus pending physical door clone previews."""
        base = self.get_bsp()
        if base is None:
            return None
        plans = self.door_clone_plans()
        prefab_plans = self.prefab_import_plans()
        mesh_plans = self.mesh_import_plans()
        vertex_plans = self.vertex_edit_plans()
        replace_plans = self.replace_submodel_plans()
        preview = base
        if vertex_plans:
            preview = vertex_edit.build_preview_bsp(preview, vertex_plans)
        if replace_plans:
            preview = replace_submodel.build_preview_bsp(preview, replace_plans)
        if plans:
            preview = door_clone.build_preview_bsp(preview, plans)
        if prefab_plans:
            preview = prefab_import.build_preview_bsp(preview, prefab_plans)
        if mesh_plans:
            preview = mesh_import.build_preview_bsp(preview, mesh_plans)
        return preview

    def materialized_existing_indices(self) -> List[int]:
        """Map materialized existing-object rows back to baseline indices."""
        assert self.world is not None
        deleted = {
            op.target_index
            for op in self.ops
            if isinstance(op, DeleteOp)
        }
        return [
            idx for idx in range(len(self.world.objects))
            if idx not in deleted
        ]

    def existing_index_for_materialized(
        self,
        world_index: int,
    ) -> Optional[int]:
        indices = self.materialized_existing_indices()
        if 0 <= world_index < len(indices):
            return indices[world_index]
        return None

    def _pending_add_counts(self) -> List[int]:
        assert self.world is not None
        counts: List[int] = []
        w = copy.deepcopy(self.world)
        deletes = []
        for op in self.ops:
            if isinstance(op, DeleteOp):
                deletes.append(op.target_index)
                continue
            if isinstance(op, CloneDoorOp):
                plan = op.build_plan(self, w.objects)
                w.objects.extend(copy.deepcopy(plan.objects))
                counts.append(len(plan.objects))
                continue
            if isinstance(op, ImportPrefabBspOp):
                created = op.apply_to(w, self)
                counts.append(len(created))
                continue
            if isinstance(op, ImportMeshBspOp):
                created = op.apply_to(w, self)
                counts.append(len(created))
                continue
            if isinstance(op, AddOp):
                op.apply_to(w)
                counts.append(1)
                continue
            op.apply_to(w)
        for idx in sorted(deletes, reverse=True):
            del w.objects[idx]
        return counts

    def objects_before_op(self, target_op: Any) -> List[patcher.WorldObject]:
        """Return materialized object state immediately before *target_op*."""
        assert self.world is not None
        w = copy.deepcopy(self.world)
        for op in self.ops:
            if op is target_op:
                return w.objects
            if isinstance(op, DeleteOp):
                continue
            if isinstance(op, CloneDoorOp):
                op.apply_to(self, w)
                continue
            if isinstance(op, ImportPrefabBspOp):
                op.apply_to(w, self)
                continue
            if isinstance(op, ImportMeshBspOp):
                op.apply_to(w, self)
                continue
            op.apply_to(w)
        return w.objects

    def pending_add_offset_for_materialized(self, world_index: int) -> Optional[Tuple[Any, int]]:
        offset = world_index - len(self.materialized_existing_indices())
        if offset < 0:
            return None
        add_ops = [op for op in self.ops if isinstance(op, (AddOp, CloneDoorOp, ImportPrefabBspOp, ImportMeshBspOp))]
        cursor = 0
        for op, count in zip(add_ops, self._pending_add_counts()):
            if cursor <= offset < cursor + count:
                return op, offset - cursor
            cursor += count
        return None

    def prefab_import_offset_for_materialized(self, world_index: int) -> Optional[int]:
        offset = world_index - len(self.materialized_existing_indices())
        if offset < 0:
            return None
        import_index = 0
        cursor = 0
        add_ops = [op for op in self.ops if isinstance(op, (AddOp, CloneDoorOp, ImportPrefabBspOp, ImportMeshBspOp))]
        for op, count in zip(add_ops, self._pending_add_counts()):
            if cursor <= offset < cursor + count:
                return import_index if isinstance(op, ImportPrefabBspOp) else None
            if isinstance(op, ImportPrefabBspOp):
                import_index += 1
            cursor += count
        return None

    def prefab_import_for_materialized(self, world_index: int) -> Optional[ImportPrefabBspOp]:
        offset = self.prefab_import_offset_for_materialized(world_index)
        if offset is None:
            return None
        imports = [op for op in self.ops if isinstance(op, ImportPrefabBspOp)]
        return imports[offset] if offset < len(imports) else None

    def mesh_import_offset_for_materialized(self, world_index: int) -> Optional[int]:
        offset = world_index - len(self.materialized_existing_indices())
        if offset < 0:
            return None
        import_index = 0
        cursor = 0
        add_ops = [op for op in self.ops if isinstance(op, (AddOp, CloneDoorOp, ImportPrefabBspOp, ImportMeshBspOp))]
        for op, count in zip(add_ops, self._pending_add_counts()):
            if cursor <= offset < cursor + count:
                return import_index if isinstance(op, ImportMeshBspOp) else None
            if isinstance(op, ImportMeshBspOp):
                import_index += 1
            cursor += count
        return None

    def mesh_import_for_materialized(self, world_index: int) -> Optional[ImportMeshBspOp]:
        offset = self.mesh_import_offset_for_materialized(world_index)
        if offset is None:
            return None
        imports = [op for op in self.ops if isinstance(op, ImportMeshBspOp)]
        return imports[offset] if offset < len(imports) else None

    def add_offset_for_materialized(self, world_index: int) -> Optional[int]:
        offset = world_index - len(self.materialized_existing_indices())
        if offset < 0:
            return None
        add_index = 0
        cursor = 0
        for op, count in zip(
            [op for op in self.ops if isinstance(op, (AddOp, CloneDoorOp, ImportPrefabBspOp, ImportMeshBspOp))],
            self._pending_add_counts(),
        ):
            if cursor <= offset < cursor + count:
                return add_index if isinstance(op, AddOp) else None
            if isinstance(op, AddOp):
                add_index += 1
            cursor += count
        return None

    def append_op(self, op: Any) -> None:
        """Append a new user operation and clear redo history."""
        self.ops.append(op)
        self.redo_ops.clear()

    def clear_redo(self) -> None:
        self.redo_ops.clear()

    def undo_last_op(self) -> Optional[Any]:
        if not self.ops:
            return None
        op = self.ops.pop()
        self.redo_ops.append(op)
        return op

    def redo_last_op(self) -> Optional[Any]:
        if not self.redo_ops:
            return None
        op = self.redo_ops.pop()
        self.ops.append(op)
        return op

    def coalesce_move_op(
        self,
        target_index: int,
        new_pos: Optional[Tuple[float, float, float]] = None,
        new_rot: Optional[Tuple[float, float, float, float]] = None,
    ) -> MoveOp:
        """
        Update the most recent MoveOp for this object, or append a new one.

        Drag, height, and yaw gestures emit repeated callbacks; treating one
        gesture as one operation keeps undo useful instead of replaying every
        frame-sized preview step.
        """
        if (self.ops
                and isinstance(self.ops[-1], MoveOp)
                and self.ops[-1].target_index == target_index):
            op = self.ops[-1]
            if new_pos is not None:
                op.new_pos = new_pos
            if new_rot is not None:
                op.new_rot = new_rot
            self.redo_ops.clear()
            return op

        if new_pos is None:
            assert self.world is not None
            old_pos = self.world.objects[target_index].get("Pos")
            if old_pos is None:
                new_pos = (0.0, 0.0, 0.0)
            else:
                new_pos = (float(old_pos[0]), float(old_pos[1]), float(old_pos[2]))
        op = MoveOp(target_index=target_index, new_pos=new_pos, new_rot=new_rot)
        self.append_op(op)
        return op

    def output_path(self, work_dir: Optional[str] = None,
                    batch_id: Optional[str] = None) -> str:
        """Where save() will write. Output is a copy of the source archive in
        work_dir with the modified entry, preserving the game-facing filename."""
        if self.output:
            return self.output
        if self.source_kind == SOURCE_REZ:
            assert self.rez_path
            archive_name = os.path.basename(self.rez_path)
            if work_dir:
                batch = batch_id or _timestamp()
                return os.path.join(work_dir, batch, "data", archive_name)
            base, ext = os.path.splitext(archive_name)
            return os.path.join(
                os.path.dirname(self.rez_path) or ".",
                f"{base}_modded{ext}",
            )
        raise ValueError(f"unsupported source_kind {self.source_kind!r}")

    def write(self, output: str) -> None:
        """Materialize and serialize to a patched REZ archive."""
        materialized = self.materialize()
        if self.source_kind != SOURCE_REZ:
            raise ValueError(f"unsupported source_kind {self.source_kind!r}")
        # Serialize world to bytes, then patch the source REZ into output.
        import sys, io
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path: sys.path.insert(0, here)
        from core import rezmgr
        # mm9_patch only writes by path, so use a temp file
        tmp = os.path.join(os.path.dirname(os.path.abspath(output)) or ".",
                           f".tmp_save_{_timestamp()}.dat")
        os.makedirs(os.path.dirname(os.path.abspath(tmp)) or ".", exist_ok=True)
        materialized.save(tmp)
        try:
            with open(tmp, "rb") as f:
                data = f.read()
            with rezmgr.RezWriter(self.rez_path, output) as w:
                w.replace(self.rez_vpath, data)
                w.commit()
        finally:
            try: os.remove(tmp)
            except OSError: pass


# --------------------------------------------------------------------------
# Project
# --------------------------------------------------------------------------

@dataclass
class Project:
    levels: List[LevelEdit] = field(default_factory=list)
    rude_rez_path: Optional[str] = None
    next_npc_nbr: int = 437
    work_dir:    Optional[str] = None      # where save() writes by default
    backup_root: Optional[str] = None      # where backups live

    # Track sources we've already backed up this session, so we don't do it twice.
    backed_up_archives: List[str] = field(default_factory=list)

    def add_level_from_rez(self, rez_path: str, virtual_path: str) -> LevelEdit:
        """Open a level that lives inside a .REZ archive."""
        for L in self.levels:
            if (L.source_kind == SOURCE_REZ
                    and os.path.abspath(L.rez_path or "") == os.path.abspath(rez_path)
                    and L.rez_vpath == virtual_path):
                return L
        # Auto-backup the archive on first open this session
        backup_path = self._maybe_backup_archive(rez_path)
        path = f"{rez_path}::{virtual_path}"
        L = LevelEdit(
            path=path,
            source_kind=SOURCE_REZ,
            rez_path=rez_path,
            rez_vpath=virtual_path,
        )
        L.load()
        L.backup_path = backup_path
        self.levels.append(L)
        return L

    def find_level(self, path: str) -> Optional[LevelEdit]:
        for L in self.levels:
            if os.path.abspath(L.path) == os.path.abspath(path):
                return L
        return None

    def _maybe_backup_archive(self, rez_path: str) -> Optional[str]:
        """Copy the source REZ to <backup_root>/<archive>.REZ.bak the first
        time we see it this session. Returns the backup path (or None if no
        backup_root is configured)."""
        if not self.backup_root:
            return None
        ap = os.path.abspath(rez_path)
        target = os.path.join(self.backup_root,
                              os.path.basename(rez_path) + ".bak")
        if ap in self.backed_up_archives:
            return target if os.path.exists(target) else None
        try:
            os.makedirs(self.backup_root, exist_ok=True)
            import shutil
            if not os.path.exists(target):
                shutil.copy2(rez_path, target)
            self.backed_up_archives.append(ap)
            return target
        except Exception:
            return None

    def has_pending(self) -> bool:
        return any(L.ops for L in self.levels)

    # ---------- save planning (explicit; user reviews before commit) ----------

    def save_plan(self) -> "SavePlan":
        batch_id = _timestamp()
        plan = SavePlan(batch_id=batch_id)
        for L in self.levels:
            if not L.ops:
                continue
            materialized = L.materialize()
            door_clones = L.door_clone_plans()
            prefab_imports = L.prefab_import_plans()
            mesh_imports = L.mesh_import_plans()
            vertex_edits = L.vertex_edit_plans()
            replaced_submodels = L.replace_submodel_plans()
            validation_warnings: List[str] = []
            if door_clones and getattr(L, "_raw_bytes", None):
                bsp_world = L.get_bsp()
                if bsp_world is not None:
                    validation_warnings = door_clone_validation.validate_clone_plans(
                        L._raw_bytes,
                        materialized,
                        bsp_world,
                        door_clones,
                    )
            if prefab_imports:
                bsp_world = L.get_bsp()
                if bsp_world is not None:
                    validation_warnings.extend(
                        prefab_import_validation.validate_import_plans(
                            bsp_world,
                            prefab_imports,
                        )
                    )
            if mesh_imports:
                validation_warnings.append(
                    "Blender OBJ/glTF mesh imports use an experimental minimal BSP compiler; "
                    "reopen the output DAT and validate in-game before relying on it"
                )
                validation_warnings.extend(
                    mesh_import.validate_collision_controllers(
                        mesh_imports,
                        materialized.objects,
                    )
                )
            if vertex_edits:
                validation_warnings.append(
                    "BSP vertex edits patch existing world-model records in place; "
                    "topology changes are blocked, but in-game validation is still required"
                )
            if replaced_submodels:
                validation_warnings.append(
                    "BSP submodel replacements rebuild existing world-model records with an "
                    "experimental compiler; PhysicsBSP/VisBSP/skyboxes are blocked, but "
                    "in-game validation is required"
                )
            plan.dats.append(DatWrite(
                source_path=L.path,
                output_path=L.output_path(self.work_dir, batch_id),
                ops_summary=[op.summary() for op in L.ops],
                materialized=materialized,
                level_edit=L,
                backup_path=L.backup_path,
                door_clones=door_clones,
                prefab_imports=prefab_imports,
                mesh_imports=mesh_imports,
                vertex_edits=vertex_edits,
                replaced_submodels=replaced_submodels,
                validation_warnings=validation_warnings,
            ))
            for op in L.ops:
                if isinstance(op, AddOp) and op.rude:
                    plan.rude_entries.append(RudeRegistration(**op.rude))
        self._populate_archive_patches(plan)
        return plan

    def _populate_archive_patches(self, plan: "SavePlan") -> None:
        archive_entries: Dict[str, Dict[str, Any]] = {}
        for d in plan.dats:
            L = d.level_edit
            if not L or L.source_kind != SOURCE_REZ or not L.rez_path:
                continue
            key = os.path.abspath(L.rez_path)
            patch = archive_entries.setdefault(key, {
                "source_archive": L.rez_path,
                "output_archive": d.output_path,
                "entries": [],
                "kind": "level",
            })
            if L.rez_vpath:
                patch["entries"].append(L.rez_vpath)

        for patch in archive_entries.values():
            plan.archive_patches.append(ArchivePatch(**patch))

        if plan.rude_entries and self.rude_rez_path and self.work_dir and plan.batch_id:
            output = os.path.join(
                self.work_dir, plan.batch_id, "data",
                os.path.basename(self.rude_rez_path),
            )
            entries = ["RUDE/NPCNAME", "RUDE/TOPBLURB"]
            entries.extend(f"RUDE/NPC{r.npc_nbr}" for r in plan.rude_entries)
            plan.archive_patches.append(ArchivePatch(
                source_archive=self.rude_rez_path,
                output_archive=output,
                entries=entries,
                kind="rude",
            ))

    def execute(self, plan: "SavePlan") -> List[str]:
        """Write the output files in the plan. Edited levels are grouped by
        source archive and written through RezWriter.
        RUDE writes are separate (execute_rude)."""
        log: List[str] = []
        rez_groups: Dict[str, List[DatWrite]] = {}
        for d in plan.dats:
            if d.level_edit and d.level_edit.source_kind == SOURCE_REZ:
                assert d.level_edit.rez_path
                rez_groups.setdefault(os.path.abspath(d.level_edit.rez_path), []).append(d)
            else:
                raise ValueError("loose DAT sources are no longer supported")

        for _archive_path, writes in rez_groups.items():
            log.extend(self._execute_rez_writes(writes))

        if plan.rude_entries and plan.rude_archive_patch() is not None:
            log.extend(self.execute_rude_rez(plan))

        manifest = self._write_manifest(plan)
        if manifest:
            log.append(f"wrote {manifest}")
        return log

    def _execute_rez_writes(self, writes: List["DatWrite"]) -> List[str]:
        """Write one patched REZ per source archive.

        Multiple edited levels may live in the same archive.  They must be
        replaced in a single RezWriter pass so one edited level does not
        overwrite another by starting again from the original source archive.
        """
        if not writes:
            return []
        first = writes[0].level_edit
        assert first and first.rez_path
        source_rez = first.rez_path
        output = writes[0].output_path
        os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)

        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from core import rezmgr

        log: List[str] = []
        with rezmgr.RezWriter(source_rez, output) as writer:
            for d in writes:
                L = d.level_edit
                assert L and L.rez_path and L.rez_vpath
                if os.path.abspath(L.rez_path) != os.path.abspath(source_rez):
                    raise ValueError("mixed source archives in one REZ write group")
                data = self._dat_write_to_bytes(d)
                writer.replace(L.rez_vpath, data)
                self._write_changed_entry_copy(output, L.rez_vpath, data)
                log.append(
                    f"  patched {L.rez_vpath} ({len(d.materialized.objects)} objects)")
            writer.commit()
        return [f"wrote {output}"] + log

    def _world_to_bytes(self, world: patcher.World) -> bytes:
        """Serialize a World through mm9_patch's path-based writer."""
        fd, tmp = tempfile.mkstemp(prefix="mm9_world_", suffix=".DAT")
        os.close(fd)
        try:
            world.save(tmp)
            with open(tmp, "rb") as f:
                return f.read()
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _dat_write_to_bytes(self, d: "DatWrite") -> bytes:
        L = d.level_edit
        source_dat = L._raw_bytes if L is not None and getattr(L, "_raw_bytes", None) else None
        bsp_world = L.get_bsp() if L is not None else None
        if d.vertex_edits:
            if source_dat is None or bsp_world is None:
                raise ValueError("BSP vertex edit save requires source DAT bytes and parsed BSP geometry")
            source_dat = vertex_edit.apply_vertex_edit_plans(source_dat, bsp_world, d.vertex_edits)
            bsp_world = bsp.parse(source_dat)
        if d.replaced_submodels:
            if source_dat is None or bsp_world is None:
                raise ValueError("BSP submodel replacement save requires source DAT bytes and parsed BSP geometry")
            source_dat = replace_submodel.apply_replace_submodel_plans(
                source_dat,
                bsp_world,
                d.replaced_submodels,
            )
            bsp_world = bsp.parse(source_dat)
        bsp_clones = [sub for plan in d.door_clones for sub in plan.submodels]
        bsp_clones.extend(sub for plan in d.prefab_imports for sub in plan.submodels)
        bsp_clones.extend(
            bsp_compile.compile_world_model_record(item.mesh)
            for plan in d.mesh_imports
            for item in plan.models
        )
        if not bsp_clones:
            if source_dat is not None and (d.vertex_edits or d.replaced_submodels):
                data = door_bsp_writer.serialize_world_with_bsp_clones(
                    source_dat,
                    d.materialized,
                    bsp_world,
                    [],
                )
                return self._validate_geometry_dat_bytes(d, data)
            return self._world_to_bytes(d.materialized)
        if source_dat is None:
            raise ValueError("BSP clone save requires source DAT bytes")
        if bsp_world is None:
            raise ValueError("BSP clone save requires parsed BSP geometry")
        data = door_bsp_writer.serialize_world_with_bsp_clones(
            source_dat,
            d.materialized,
            bsp_world,
            bsp_clones,
        )
        return self._validate_geometry_dat_bytes(d, data)

    def _validate_geometry_dat_bytes(self, d: "DatWrite", data: bytes) -> bytes:
        required_names: List[str] = []
        required_names.extend(sub.new_name for plan in d.door_clones for sub in plan.submodels)
        required_names.extend(sub.new_name for plan in d.prefab_imports for sub in plan.submodels)
        required_names.extend(item.name for plan in d.mesh_imports for item in plan.models)
        required_names.extend(item.name for plan in d.replaced_submodels for item in plan.models)
        validation = output_validation.validate_geometry_dat(
            data,
            expected_object_count=len(d.materialized.objects),
            required_bsp_names=required_names,
        )
        validation.raise_for_errors()
        for warning in validation.warnings:
            if warning not in d.validation_warnings:
                d.validation_warnings.append(warning)
        return data

    def _write_changed_entry_copy(self, archive_output: str,
                                  virtual_path: str,
                                  data: bytes) -> Optional[str]:
        """Write a loose copy of a patched archive entry for review/debugging."""
        if not self.work_dir:
            return None
        batch_dir = os.path.dirname(os.path.dirname(os.path.abspath(archive_output)))
        if os.path.basename(os.path.dirname(os.path.abspath(archive_output))).lower() != "data":
            return None
        rel = str(virtual_path or "").replace("\\", "/").strip("/")
        if not rel:
            return None
        if not os.path.splitext(rel)[1]:
            rel += self._default_entry_extension(rel)
        out_path = os.path.abspath(os.path.join(batch_dir, "changed_entries", *rel.split("/")))
        root = os.path.abspath(os.path.join(batch_dir, "changed_entries"))
        try:
            if os.path.commonpath([out_path, root]) != root:
                return None
        except ValueError:
            return None
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(data)
        return out_path

    def _default_entry_extension(self, virtual_path: str) -> str:
        root = str(virtual_path or "").replace("\\", "/").split("/", 1)[0].upper()
        return {
            "WORLDS": ".DAT",
            "RUDE": ".RUDE",
            "SCRIPTS": ".SCR",
            "TEXTURES": ".DTX",
            "SKINS": ".DTX",
            "MODELS": ".ABC",
        }.get(root, "")

    def _write_manifest(self, plan: "SavePlan") -> Optional[str]:
        if not plan.dats or not self.work_dir or not plan.batch_id:
            return None
        manifest_path = os.path.join(self.work_dir, plan.batch_id, "manifest.json")
        try:
            os.makedirs(os.path.dirname(os.path.abspath(manifest_path)) or ".",
                        exist_ok=True)
            doc = {
                "version": 1,
                "saved_at": plan.batch_id,
                "archives": self._manifest_archives(plan),
                "dats": [
                    {
                        "source_path": d.source_path,
                        "backup_path": d.backup_path,
                        "output_path": d.output_path,
                        "objects_after": d.stats()["objects_after"],
                        "door_clones": d.stats()["door_clones"],
                        "prefab_imports": d.stats()["prefab_imports"],
                        "mesh_imports": d.stats()["mesh_imports"],
                        "vertex_edits": d.stats()["vertex_edits"],
                        "submodel_replacements": d.stats()["submodel_replacements"],
                        "geometry_edits": d.geometry_manifest_details(),
                        "ops_summary": d.ops_summary,
                        "validation_warnings": d.validation_warnings,
                    }
                    for d in plan.dats
                ],
                "rude_entries": [
                    {
                        "npc_nbr": r.npc_nbr,
                        "name": r.name,
                        "blurb": r.blurb,
                        "lines": r.lines,
                        "force": r.force,
                    }
                    for r in plan.rude_entries
                ],
            }
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2, ensure_ascii=False)
            return manifest_path
        except Exception:
            return None

    def _manifest_archives(self, plan: "SavePlan") -> List[Dict[str, Any]]:
        return [
            {
                "source_archive": p.source_archive,
                "output_archive": p.output_archive,
                "entries": p.entries,
                "kind": p.kind,
            }
            for p in plan.archive_patches
        ]

    def execute_rude_rez(self, plan: "SavePlan") -> List[str]:
        """Write fresh-NPC RUDE registrations into output/<batch>/data/RUDE.REZ."""
        patch = plan.rude_archive_patch()
        if patch is None or not plan.rude_entries:
            return []
        source_rez = patch.source_archive
        output_rez = patch.output_archive
        if not source_rez or not os.path.isfile(source_rez):
            raise FileNotFoundError("RUDE.REZ source archive was not found")

        self._maybe_backup_archive(source_rez)
        os.makedirs(os.path.dirname(os.path.abspath(output_rez)) or ".", exist_ok=True)

        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from core import rezmgr

        with rezmgr.RezReader(source_rez) as reader:
            npcname_vpath = self._find_rez_entry(reader, ("RUDE/NPCNAME", "RUDE/NPCNAME.RUDE"))
            topblurb_vpath = self._find_rez_entry(reader, ("RUDE/TOPBLURB", "RUDE/TOPBLURB.RUDE"))
            if npcname_vpath is None or topblurb_vpath is None:
                raise FileNotFoundError("RUDE.REZ is missing NPCNAME or TOPBLURB")
            npcname = reader.extract_to_bytes(npcname_vpath).decode("latin-1")
            topblurb = reader.extract_to_bytes(topblurb_vpath).decode("latin-1")
            existing_paths = {
                reader.find(path).virtual_path()
                for path in reader.list_paths()
                if reader.find(path) is not None
            }

        name_ids = self._csv_first_col_ints(npcname)
        blurb_ids = self._csv_first_col_ints(topblurb)
        npcname_out = npcname
        topblurb_out = topblurb
        npc_files: Dict[str, bytes] = {}
        log: List[str] = []

        for entry in plan.rude_entries:
            n = entry.npc_nbr
            npc_vpath = f"RUDE/NPC{n}"
            npc_existing = self._find_existing_vpath(existing_paths, npc_vpath)
            conflicts = []
            if npc_existing is not None:
                conflicts.append(f"NPC{n} already exists")
            if n in name_ids:
                conflicts.append(f"NPCNAME already has {n}")
            if n in blurb_ids:
                conflicts.append(f"TOPBLURB already has {n}")
            if conflicts and not entry.force:
                raise ValueError(
                    f"RUDE registration conflict for NPC{n}: "
                    + "; ".join(conflicts))

            name_line = f'{n},"{self._rude_escape(entry.name)}"'
            blurb_line = f'{n},{n},"{self._rude_escape(entry.blurb)}"'
            npcname_out = self._replace_or_append_first_col(npcname_out, n, name_line)
            topblurb_out = self._replace_or_append_first_col(topblurb_out, n, blurb_line)
            npc_files[npc_existing or npc_vpath] = self._build_npc_rude(entry).encode("latin-1")
            log.append(f"  patched RUDE/NPC{n} ({len(entry.lines) + 1} option(s))")

        npcname_bytes = npcname_out.encode("latin-1")
        topblurb_bytes = topblurb_out.encode("latin-1")

        with rezmgr.RezWriter(source_rez, output_rez) as writer:
            writer.replace(npcname_vpath, npcname_bytes)
            writer.replace(topblurb_vpath, topblurb_bytes)
            self._write_changed_entry_copy(output_rez, npcname_vpath, npcname_bytes)
            self._write_changed_entry_copy(output_rez, topblurb_vpath, topblurb_bytes)
            for vpath, data in npc_files.items():
                if self._find_existing_vpath(existing_paths, vpath) is not None:
                    writer.replace(vpath, data)
                else:
                    writer.add(vpath, data)
                self._write_changed_entry_copy(output_rez, vpath, data)
            writer.commit()

        return [f"wrote {output_rez}"] + log

    def _find_rez_entry(self, reader: Any, candidates: Tuple[str, ...]) -> Optional[str]:
        for candidate in candidates:
            ent = reader.find(candidate)
            if ent is not None:
                return ent.virtual_path()
        return None

    def _find_existing_vpath(self, existing_paths: set, virtual_path: str) -> Optional[str]:
        key = self._strip_rude_ext(str(virtual_path or "").replace("\\", "/").lower())
        for path in existing_paths:
            norm = str(path).replace("\\", "/")
            norm_key = self._strip_rude_ext(norm.lower())
            if norm.lower() == key or norm_key == key:
                return path
        return None

    def _strip_rude_ext(self, path: str) -> str:
        return path[:-5] if path.lower().endswith(".rude") else path

    def _csv_first_col_ints(self, text: str) -> set:
        ids = set()
        for line in str(text or "").splitlines():
            head = line.split(",", 1)[0].strip()
            try:
                ids.add(int(head))
            except ValueError:
                pass
        return ids

    def _replace_or_append_first_col(self, text: str, key: int, new_line: str) -> str:
        lines = str(text or "").splitlines()
        replaced = False
        out = []
        for line in lines:
            head = line.split(",", 1)[0].strip()
            try:
                if int(head) == key and not replaced:
                    out.append(new_line)
                    replaced = True
                    continue
            except ValueError:
                pass
            out.append(line)
        if not replaced:
            out.append(new_line)
        return "\n".join(out) + "\n"

    def _build_npc_rude(self, entry: "RudeRegistration") -> str:
        effect_columns = ",".join(["0"] * 24)
        lines: List[str] = []
        branch = 1
        for player_text, npc_response in entry.lines:
            lines.append(
                f'{entry.npc_nbr},{entry.npc_nbr},{branch},'
                f'"{self._rude_escape(player_text)}",'
                f'"{self._rude_escape(npc_response)}",'
                f'{entry.npc_nbr},{effect_columns}'
            )
            branch += 1
        lines.append(
            f'{entry.npc_nbr},{entry.npc_nbr},{branch},'
            f'"Goodbye.","Farewell.",-1,{effect_columns}'
        )
        return "\n".join(lines) + "\n"

    def _rude_escape(self, s: str) -> str:
        return str(s or "").replace('"', "'")


# --------------------------------------------------------------------------
# Save plan (the data structure the editor's diff dialog visualizes)
# --------------------------------------------------------------------------

@dataclass
class DatWrite:
    source_path: str
    output_path: str
    ops_summary: List[str]
    materialized: patcher.World
    level_edit: Optional["LevelEdit"] = None
    backup_path: Optional[str] = None
    door_clones: List[door_clone.DoorClonePlan] = field(default_factory=list)
    prefab_imports: List[prefab_import.PrefabBspImportPlan] = field(default_factory=list)
    mesh_imports: List[mesh_import.MeshBspImportPlan] = field(default_factory=list)
    vertex_edits: List[vertex_edit.VertexEditPlan] = field(default_factory=list)
    replaced_submodels: List[replace_submodel.ReplaceSubmodelPlan] = field(default_factory=list)
    validation_warnings: List[str] = field(default_factory=list)

    def stats(self) -> Dict[str, int]:
        return {
            "objects_after": len(self.materialized.objects),
            "door_clones": len(self.door_clones),
            "prefab_imports": len(self.prefab_imports),
            "prefab_bsp_models": sum(len(plan.submodels) for plan in self.prefab_imports),
            "mesh_imports": len(self.mesh_imports),
            "mesh_bsp_models": sum(len(plan.models) for plan in self.mesh_imports),
            "vertex_edits": len(self.vertex_edits),
            "vertex_edit_models": sum(len(plan.models) for plan in self.vertex_edits),
            "submodel_replacements": len(self.replaced_submodels),
            "replaced_bsp_models": sum(len(plan.models) for plan in self.replaced_submodels),
        }

    def geometry_manifest_details(self) -> Dict[str, Any]:
        return {
            "mesh_imports": [
                {
                    "obj_path": plan.obj_path,
                    "meta_path": plan.meta_path,
                    "new_name": plan.new_name,
                    "collision_mode": plan.collision_mode,
                    "visible_model_count": sum(1 for item in plan.models if not mesh_import.is_collision_model(item)),
                    "collision_model_count": sum(1 for item in plan.models if mesh_import.is_collision_model(item)),
                    "role_counts": mesh_import.role_counts(plan.models),
                    "models": [
                        _manifest_model_summary(item.name, item.mesh, role=item.role)
                        for item in plan.models
                    ],
                }
                for plan in self.mesh_imports
            ],
            "vertex_edits": [
                {
                    "obj_path": plan.obj_path,
                    "meta_path": plan.meta_path,
                    "models": [
                        _manifest_model_summary(item.name, item.edited_model)
                        for item in plan.models
                    ],
                }
                for plan in self.vertex_edits
            ],
            "submodel_replacements": [
                {
                    "obj_path": plan.obj_path,
                    "meta_path": plan.meta_path,
                    "models": [
                        {
                            **_manifest_model_summary(item.name, item.replacement_model),
                            "source_polygon_count": len(item.source_model.polygons),
                            "source_point_count": len(item.source_model.points),
                            "replacement_record_bytes": len(item.record.raw_bytes),
                        }
                        for item in plan.models
                    ],
                }
                for plan in self.replaced_submodels
            ],
        }

    def geometry_risk_report(self) -> List[str]:
        """Human-readable geometry details for the pre-save review dialog."""
        lines: List[str] = []
        if self.mesh_imports:
            mesh_model_count = sum(len(plan.models) for plan in self.mesh_imports)
            visible_model_count = sum(
                1
                for plan in self.mesh_imports
                for item in plan.models
                if not mesh_import.is_collision_model(item)
            )
            collision_model_count = mesh_model_count - visible_model_count
            lines.append(
                "geometry risk: mesh import uses generated standalone BSP "
                "records; this does not rebuild PhysicsBSP, VisBSP, portals, "
                "or light data"
            )
            lines.append(
                "mesh geometry: "
                f"{visible_model_count} visible model(s), "
                f"{collision_model_count} collision/helper model(s)"
            )
            role_counts = _combine_counts(
                mesh_import.role_counts(plan.models)
                for plan in self.mesh_imports
            )
            if role_counts:
                lines.append(f"mesh roles: {_format_counts(role_counts)}")
            uv_counts = _combine_counts(
                _uv_method_counts(item.mesh.surfaces)
                for plan in self.mesh_imports
                for item in plan.models
            )
            if uv_counts:
                lines.append(f"mesh UV methods: {_format_counts(uv_counts)}")
            source_counts = _combine_counts(
                _source_format_counts(item.mesh.polygons)
                for plan in self.mesh_imports
                for item in plan.models
            )
            if source_counts:
                lines.append(f"mesh source formats: {_format_counts(source_counts)}")
            elif any(str(plan.source_format or "").lower() in {"gltf", "glb"} for plan in self.mesh_imports):
                lines.append("mesh source formats: glTF geometry without original DAT face metadata")
            for plan in self.mesh_imports:
                for warning in _mesh_import_risk_warnings(plan):
                    lines.append(f"mesh warning: {warning}")

        if self.vertex_edits:
            model_count = sum(len(plan.models) for plan in self.vertex_edits)
            lines.append(
                "geometry risk: vertex edits patch existing BSP records in place; "
                f"{model_count} model(s) will keep their original topology"
            )

        if self.replaced_submodels:
            model_count = sum(len(plan.models) for plan in self.replaced_submodels)
            uv_counts = _combine_counts(
                _uv_method_counts(item.replacement_model.surfaces)
                for plan in self.replaced_submodels
                for item in plan.models
            )
            lines.append(
                "geometry risk: submodel replacement rebuilds standalone BSP "
                f"record(s) for {model_count} model(s); PhysicsBSP, VisBSP, "
                "and skybox replacement remain blocked"
            )
            if uv_counts:
                lines.append(f"replacement UV methods: {_format_counts(uv_counts)}")

        if self.prefab_imports:
            submodel_count = sum(len(plan.submodels) for plan in self.prefab_imports)
            lines.append(
                "geometry risk: prefab import splices source BSP submodel "
                f"record(s); {submodel_count} BSP model(s) will be added"
            )

        return lines


def _manifest_model_summary(
    name: str,
    model: bsp.WorldModelMesh,
    *,
    role: str = "",
) -> Dict[str, Any]:
    details: Dict[str, Any] = {
        "name": name,
        "point_count": len(model.points),
        "polygon_count": len(model.polygons),
        "texture_count": len(model.texture_names),
        "surface_count": len(model.surfaces),
        "default_uv_surface_count": sum(1 for surface in model.surfaces if _is_default_uv_surface(surface)),
        "uv_method_counts": _uv_method_counts(model.surfaces),
        "source_face_count": _source_face_count(model.polygons),
        "source_format_counts": _source_format_counts(model.polygons),
        "source_physics_material_counts": _source_metadata_counts(model.polygons, "physics_material"),
        "source_surface_key_counts": _source_metadata_counts(model.polygons, "surface_key"),
        "source_surface_flag_counts": _source_surface_flag_counts(model.polygons),
        "textures": list(model.texture_names[:16]),
    }
    if role:
        details["role"] = role
    return details


def _combine_counts(counts_list: Iterable[Dict[str, int]]) -> Dict[str, int]:
    combined: Dict[str, int] = {}
    for counts in counts_list:
        for key, count in (counts or {}).items():
            text = str(key or "")
            if not text:
                continue
            combined[text] = combined.get(text, 0) + int(count)
    return combined


def _format_counts(counts: Dict[str, int]) -> str:
    return ", ".join(
        f"{key}={counts[key]}"
        for key in sorted(counts)
    )


def _mesh_import_risk_warnings(plan: mesh_import.MeshBspImportPlan) -> List[str]:
    warnings: List[str] = []
    source_format = str(plan.source_format or "").lower()
    metadata_source = str(plan.metadata_source or "").lower()
    if source_format in {"gltf", "glb"} and metadata_source == "missing":
        warnings.append(
            "generic glTF lacks MM9 DAT metadata; import is additive triangle "
            "geometry only and cannot be treated as a full-level DAT round trip"
        )
    elif source_format in {"gltf", "glb"}:
        warnings.append(
            "glTF import creates additive/replacement BSP records only; full-level "
            "DAT semantics such as PhysicsBSP, VisBSP, portals, and lighting are "
            "not reconstructed"
        )
    for warning in plan.import_warnings:
        text = str(warning or "").strip()
        if text and text not in warnings:
            warnings.append(text)
    return warnings


def _is_default_uv_surface(surface: bsp.Surface) -> bool:
    return (
        tuple(round(float(v), 6) for v in surface.uv_o) == (0.0, 0.0, 0.0)
        and tuple(round(float(v), 6) for v in surface.uv_p) == (1.0, 0.0, 0.0)
        and tuple(round(float(v), 6) for v in surface.uv_q) == (0.0, 0.0, 1.0)
    )


def _uv_method_counts(surfaces: Sequence[bsp.Surface]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for surface in surfaces:
        method = str(getattr(surface, "mm9_uv_method", "") or "")
        if not method:
            method = "default" if _is_default_uv_surface(surface) else "unknown"
        counts[method] = counts.get(method, 0) + 1
    return counts


def _source_face_count(polygons: Sequence[bsp.Polygon]) -> int:
    return sum(1 for polygon in polygons if getattr(polygon, "mm9_source_face", None))


def _source_format_counts(polygons: Sequence[bsp.Polygon]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for polygon in polygons:
        source_face = getattr(polygon, "mm9_source_face", None)
        if not isinstance(source_face, dict):
            continue
        source_format = str(source_face.get("source_format") or "")
        if not source_format:
            continue
        counts[source_format] = counts.get(source_format, 0) + 1
    return counts


def _source_metadata_counts(polygons: Sequence[bsp.Polygon], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for polygon in polygons:
        source_face = getattr(polygon, "mm9_source_face", None)
        if not isinstance(source_face, dict):
            continue
        value = str(source_face.get(key) or "")
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def _source_surface_flag_counts(polygons: Sequence[bsp.Polygon]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for polygon in polygons:
        source_face = getattr(polygon, "mm9_source_face", None)
        if not isinstance(source_face, dict):
            continue
        flags = source_face.get("surface_flags") or []
        if isinstance(flags, str):
            flags = [flags]
        if not isinstance(flags, (list, tuple)):
            continue
        for flag in flags:
            text = str(flag or "")
            if text:
                counts[text] = counts.get(text, 0) + 1
    return counts


@dataclass
class RudeRegistration:
    npc_nbr: int
    name: str
    blurb: str = "Hail, traveler!"
    lines: List[Tuple[str, str]] = field(default_factory=list)
    force: bool = False


@dataclass
class ArchivePatch:
    source_archive: str
    output_archive: str
    entries: List[str] = field(default_factory=list)
    kind: str = "archive"


@dataclass
class SavePlan:
    batch_id: Optional[str] = None
    dats: List[DatWrite] = field(default_factory=list)
    rude_entries: List[RudeRegistration] = field(default_factory=list)
    archive_patches: List[ArchivePatch] = field(default_factory=list)

    def rude_archive_patch(self) -> Optional[ArchivePatch]:
        for patch in self.archive_patches:
            if patch.kind == "rude":
                return patch
        return None
