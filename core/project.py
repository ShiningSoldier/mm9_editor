"""
project.py
==========

In-memory representation of a multi-level MM9 mod project.

A Project owns a list of LevelEdits (one per loaded .DAT). Each LevelEdit has
a current source-or-committed baseline, a list of pending Operations (add /
move / delete), and a derived view that shows what the World would look like
with all pending ops applied.

This is what the GUI works with. Operations are reified so we can:
- Undo/redo
- Show an explicit diff before saving
- Track and stage RUDE assets independently from DAT object placement

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
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import _path_setup  # noqa: F401
import mm9_patch as patcher
from core import bsp
from core import rude as rude_model
from core import rude_script
from features.doors import bsp_writer as door_bsp_writer
from features.doors import clone as door_clone
from features.doors import validation as door_clone_validation
from features.dat_editing import bsp_record_inspector
from features.dat_editing import output_validation
from features.prefabs import import_static as prefab_import
from features.prefabs import behavioral as prefab_behavioral
from features.prefabs import validation as prefab_import_validation


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _script_rez_key(path: str) -> str:
    normalized = str(path or "").replace("/", "\\").strip("\\").casefold()
    return normalized[:-4] if normalized.endswith(".scr") else normalized


def _prefab_file_version(path: str) -> int:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(4)
    except OSError:
        return -1
    return struct.unpack("<I", raw)[0] if len(raw) == 4 else -1


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
class ImportResourcePrefabOp(AddOp):
    """A prefab represented by a stock runtime model-backed object.

    This deliberately subclasses :class:`AddOp`: after the representation has
    been chosen, editing/moving/deleting it is exactly the same operation as a
    normal object placement.  The extra fields retain the import provenance so
    the choice can be audited and round-tripped through ``.mm9mod`` projects.
    """

    prefab_path: str = ""
    candidate_id: str = ""
    model_path: str = ""
    skin_paths: Tuple[str, ...] = ()
    source_fingerprint: str = ""

    def summary(self) -> str:
        name = self.overrides.get("Name") or self.template.get("Name") or ""
        pos = self.overrides.get("Pos") or self.template.get("Pos") or (0, 0, 0)
        model = self.model_path or self.overrides.get("Filename") or ""
        return (
            f"+ resource prefab {os.path.basename(self.prefab_path)} -> {name} "
            f"as {self.template.type_str} ({model}) at "
            f"({float(pos[0]):.0f}, {float(pos[1]):.0f}, {float(pos[2]):.0f})"
        )


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
    placement_anchor: str = "bottom_center"
    allow_unsafe_visibility: bool = False
    worldobject_template: Optional[patcher.WorldObject] = None
    invisiblebrush_template: Optional[patcher.WorldObject] = None
    # Legacy ED geometry can still be useful in the viewport while designing,
    # but it must never enter a game DAT.  The save planner enforces that hard
    # boundary independently of UI validation.
    preview_only: bool = False

    def build_plan(
        self,
        level: "LevelEdit",
        target_object_names: Optional[Sequence[str]] = None,
    ) -> prefab_import.PrefabBspImportPlan:
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
            placement_anchor=self.placement_anchor,
            allow_unsafe_visibility=bool(self.allow_unsafe_visibility),
            target_object_names=target_object_names,
            # Operations are materialized for editor preview here. Runtime
            # save planning performs a second, strict validation pass.
            allow_generated_bsp=True,
        )

    def object_names(self, level: Optional["LevelEdit"] = None) -> List[str]:
        if level is not None:
            try:
                plan = self.build_plan(level)
                return [*plan.visible_model_names, *plan.collision_model_names]
            except Exception:
                pass
        names = [self.new_name]
        if _normalized_prefab_collision_mode(self.collision_mode) in {"invisible_bsp", "box_approx"}:
            names.append(f"{self.new_name}_Collision")
        return names

    def apply_to(self, world: patcher.World, level: Optional["LevelEdit"] = None) -> List[patcher.WorldObject]:
        wanted = {name.lower() for name in self.object_names(level)}
        for obj in world.objects:
            obj_name = (obj.get("Name") or "").lower()
            if obj_name in wanted:
                raise ValueError(f"object named {obj.get('Name')!r} already exists")
        plan = self.build_plan(level) if level is not None else None
        visible_names = plan.visible_model_names if plan is not None else [self.new_name]
        template = copy.deepcopy(self.worldobject_template) if self.worldobject_template else None
        if template is None:
            template = _find_static_worldobject_template(world)
        created = []
        for visible_name in visible_names:
            new_obj = _make_prefab_worldobject(
                template,
                visible_name,
                self.target_pos,
                self.target_yaw,
                visible=1,
                type_str="WorldObject",
            )
            created.append(new_obj)
            world.objects.append(new_obj)
        if _normalized_prefab_collision_mode(self.collision_mode) in {"invisible_bsp", "box_approx"}:
            collision_template = (
                copy.deepcopy(self.invisiblebrush_template)
                if self.invisiblebrush_template
                else (_find_object_template(world, "InvisibleBrush") or template)
            )
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
        anchor = f" anchor={self.placement_anchor}"
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
            f" at ({pos[0]:.0f}, {pos[1]:.0f}, {pos[2]:.0f}){yaw}{anchor}{roles}{collision}{thickness}{segment}"
            + (" [EDITOR PREVIEW ONLY]" if self.preview_only else "")
        )

    def _collision_submodel_names(self, level: Optional["LevelEdit"]) -> List[str]:
        if level is None:
            return []
        try:
            return [
                str(name)
                for name in self.build_plan(level).collision_model_names
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
                name = getattr(submodel, "new_name", getattr(submodel, "name", ""))
                if name in plan.collision_model_names:
                    model = prefab_import.preview_submodel(submodel)
                    specs.append((name, _bounds_center(model.min_box, model.max_box)))
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
class ImportBehavioralPrefabOp:
    """Serializable atomic operation for promoted behavioral capabilities."""

    prefab_path: str
    root_name: str
    target_pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    target_yaw: float = 0.0
    placement_anchor: str = "bottom_center"
    source_fingerprint: str = ""
    external_bindings: Dict[str, str] = field(default_factory=dict)
    dependency_decisions: Dict[str, str] = field(default_factory=dict)
    enabled_capabilities: Tuple[str, ...] = ()
    class_templates: Dict[str, patcher.WorldObject] = field(default_factory=dict)
    object_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    planned_object_names: Dict[str, str] = field(default_factory=dict)
    script_sources: Dict[str, str] = field(default_factory=dict)
    script_assets: Dict[str, str] = field(default_factory=dict)
    planner_version: int = prefab_behavioral.PLANNER_VERSION
    operation_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if not str(self.operation_id).strip():
            self.operation_id = uuid.uuid4().hex

    def build_plan(
        self,
        *,
        existing_names: Sequence[str] = (),
        catalog: Optional[Dict[str, Any]] = None,
    ) -> prefab_behavioral.BehavioralPrefabImportPlan:
        analysis = self._analyze(catalog=catalog)
        actual_fingerprint = analysis.graph.source_fingerprint
        if self.source_fingerprint and self.source_fingerprint != actual_fingerprint:
            raise ValueError(
                "behavioral prefab source changed since this operation was created"
            )
        if int(self.planner_version) != prefab_behavioral.PLANNER_VERSION:
            raise ValueError(
                f"unsupported behavioral prefab planner version {self.planner_version}; "
                f"expected {prefab_behavioral.PLANNER_VERSION}"
            )
        return prefab_behavioral.build_behavioral_import_plan(
            analysis,
            root_name=self.root_name,
            target_pos=self.target_pos,
            target_yaw=self.target_yaw,
            existing_names=existing_names,
            external_bindings=self.external_bindings,
            dependency_decisions=self.dependency_decisions,
            fixed_object_names=self.planned_object_names,
        )

    def apply_to(self, world: patcher.World) -> List[patcher.WorldObject]:
        analysis, plan = self._analysis_and_plan(world.objects)
        plan.require_ready()
        created = prefab_behavioral.materialize_behavioral_plan(
            analysis,
            plan,
            class_templates=self.class_templates,
            placement_anchor=self.placement_anchor,
            object_overrides=self.object_overrides,
        )
        used_names = {
            str(obj.get("Name") or "").casefold()
            for obj in world.objects
            if str(obj.get("Name") or "")
        }
        for obj in created:
            name = str(obj.get("Name") or "").strip()
            if not name:
                raise ValueError(
                    f"behavioral prefab created an unnamed {obj.type_str} object"
                )
            if name.casefold() in used_names:
                raise ValueError(f"object named {name!r} already exists")
            used_names.add(name.casefold())
        world.objects.extend(copy.deepcopy(created))
        return list(created)

    def pending_object_count(self, objects: Sequence[patcher.WorldObject]) -> int:
        _analysis, plan = self._analysis_and_plan(objects)
        return len(plan.objects)

    def build_bsp_plan(
        self,
        target_bsp: bsp.BspWorld,
        *,
        existing_names: Sequence[str] = (),
        require_runtime_bsp: bool = False,
    ) -> Optional[prefab_import.PrefabBspImportPlan]:
        analysis = self._analyze()
        if self.source_fingerprint and self.source_fingerprint != analysis.graph.source_fingerprint:
            raise ValueError(
                "behavioral prefab source changed since this operation was created"
            )
        plan = prefab_behavioral.build_behavioral_import_plan(
            analysis,
            root_name=self.root_name,
            target_pos=self.target_pos,
            target_yaw=self.target_yaw,
            existing_names=existing_names,
            external_bindings=self.external_bindings,
            dependency_decisions=self.dependency_decisions,
            fixed_object_names=self.planned_object_names,
        )
        return prefab_behavioral.build_behavioral_bsp_import_plan(
            target_bsp,
            analysis,
            plan,
            placement_anchor=self.placement_anchor,
            allow_generated_bsp=not require_runtime_bsp,
            validate_runtime_bsp=require_runtime_bsp,
        )

    def retarget_from_object(
        self,
        objects: Sequence[patcher.WorldObject],
        object_offset: int,
        new_pos: Tuple[float, float, float],
    ) -> None:
        created = self._materialized_objects(objects)
        if not (0 <= object_offset < len(created)):
            return
        old_pos = created[object_offset].get("Pos")
        if not isinstance(old_pos, (tuple, list)) or len(old_pos) != 3:
            return
        delta = tuple(float(new_pos[axis]) - float(old_pos[axis]) for axis in range(3))
        self.target_pos = tuple(
            float(self.target_pos[axis]) + delta[axis] for axis in range(3)
        )

    def rerotate_from_object(
        self,
        objects: Sequence[patcher.WorldObject],
        object_offset: int,
        new_rot: Tuple[float, float, float, float],
    ) -> None:
        created = self._materialized_objects(objects)
        if not (0 <= object_offset < len(created)):
            return
        old_rot = created[object_offset].get("Rotation")
        if not isinstance(old_rot, (tuple, list)) or len(old_rot) < 2:
            return
        self.target_yaw = (
            float(self.target_yaw) + float(new_rot[1]) - float(old_rot[1])
        )

    def set_object_overrides(
        self,
        object_offset: int,
        overrides: Mapping[str, Any],
    ) -> None:
        analysis = self._analyze()
        runtime_objects = analysis.graph.runtime_objects
        if not (0 <= object_offset < len(runtime_objects)):
            return
        key = str(runtime_objects[object_offset].index)
        values = self.object_overrides.setdefault(key, {})
        values.update(copy.deepcopy(dict(overrides)))

    def _analysis_and_plan(
        self,
        objects: Sequence[patcher.WorldObject],
    ) -> Tuple[prefab_behavioral.PrefabAnalysis, prefab_behavioral.BehavioralPrefabImportPlan]:
        if int(self.planner_version) != prefab_behavioral.PLANNER_VERSION:
            raise ValueError(
                f"unsupported behavioral prefab planner version {self.planner_version}; "
                f"expected {prefab_behavioral.PLANNER_VERSION}"
            )
        analysis = self._analyze()
        if self.source_fingerprint and self.source_fingerprint != analysis.graph.source_fingerprint:
            raise ValueError(
                "behavioral prefab source changed since this operation was created"
            )
        plan = prefab_behavioral.build_behavioral_import_plan(
            analysis,
            root_name=self.root_name,
            target_pos=self.target_pos,
            target_yaw=self.target_yaw,
            existing_names=[str(obj.get("Name") or "") for obj in objects],
            external_bindings=self.external_bindings,
            dependency_decisions=self.dependency_decisions,
            fixed_object_names=self.planned_object_names,
        )
        self._validate_script_assets(analysis, plan)
        return analysis, plan

    def _analyze(
        self,
        *,
        catalog: Optional[Dict[str, Any]] = None,
    ) -> prefab_behavioral.PrefabAnalysis:
        loader = prefab_behavioral.script_loader_from_sources(self.script_sources)
        return prefab_behavioral.analyze_prefab(
            self.prefab_path,
            catalog=catalog,
            supported_classes=self.enabled_capabilities,
            allow_scripts=True,
            allowed_script_names=prefab_behavioral.PHASE6_REVIEWED_SCRIPTS,
            script_loader=loader,
        )

    def _validate_script_assets(
        self,
        analysis: prefab_behavioral.PrefabAnalysis,
        plan: prefab_behavioral.BehavioralPrefabImportPlan,
    ) -> None:
        expected_overrides, expected_assets = (
            prefab_behavioral.build_script_import_assets(
                analysis,
                plan,
                operation_id=self.operation_id,
                script_loader=prefab_behavioral.script_loader_from_sources(
                    self.script_sources
                ),
            )
        )
        if self.script_assets != expected_assets:
            raise ValueError(
                "behavioral prefab generated scripts no longer match its reviewed "
                "source, namespace, and bindings"
            )
        for source_index, expected in expected_overrides.items():
            actual = self.object_overrides.get(source_index, {})
            for name, value in expected.items():
                if actual.get(name) != value:
                    raise ValueError(
                        f"behavioral prefab script override {source_index}.{name} "
                        "does not match its generated archive asset"
                    )

    def _materialized_objects(
        self,
        objects: Sequence[patcher.WorldObject],
    ) -> Tuple[patcher.WorldObject, ...]:
        analysis, plan = self._analysis_and_plan(objects)
        return prefab_behavioral.materialize_behavioral_plan(
            analysis,
            plan,
            class_templates=self.class_templates,
            placement_anchor=self.placement_anchor,
            object_overrides=self.object_overrides,
        )

    def summary(self) -> str:
        pos = self.target_pos
        yaw = "" if abs(float(self.target_yaw)) < 1.0e-6 else f" yaw {self.target_yaw:.2f}"
        return (
            f"+ behavioral prefab {os.path.basename(self.prefab_path)} -> {self.root_name} "
            f"at ({pos[0]:.0f}, {pos[1]:.0f}, {pos[2]:.0f}){yaw} "
            f"[{len(self.enabled_capabilities)} promoted class capabilities]"
        )


@dataclass
class RemoveBehavioralPrefabOp:
    """Undoable tombstone for one atomic behavioral prefab import."""

    operation_id: str
    root_name: str = ""

    def summary(self) -> str:
        return f"- behavioral prefab assembly {self.root_name or self.operation_id}"


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
    conversion_report: Optional[Dict[str, Any]] = None
    conversion_stage_dir: str = ""
    preview_actor_visuals: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _next_id:    int = 0
    # Cached BSP geometry (lazily parsed on first map-view refresh)
    bsp:         Optional[Any] = None
    # Editor consumers only read materialized snapshots.  Cache those snapshots
    # and BSP preview plans by the baseline World identity plus a repr-based
    # operation fingerprint.  The fingerprint also notices in-place edits to
    # pending operations made by viewport drags and property controls.
    _editor_materialized_cache: Optional[Tuple[Tuple[int, str], patcher.World]] = field(
        default=None, init=False, repr=False, compare=False,
    )
    _door_plan_cache: Optional[
        Tuple[Tuple[int, str], List[door_clone.DoorClonePlan]]
    ] = field(default=None, init=False, repr=False, compare=False)
    _prefab_plan_cache: Optional[
        Tuple[Tuple[int, str], List[prefab_import.PrefabBspImportPlan]]
    ] = field(default=None, init=False, repr=False, compare=False)
    _preview_bsp_cache: Optional[Tuple[Tuple[int, str], Any]] = field(
        default=None, init=False, repr=False, compare=False,
    )

    def _editor_state_key(self) -> Tuple[int, str]:
        """Return a cheap cache key that includes in-place operation edits."""
        prefab_files = []
        for op in self.ops:
            if not isinstance(op, (ImportPrefabBspOp, ImportBehavioralPrefabOp)):
                continue
            path = os.path.abspath(op.prefab_path)
            try:
                stat = os.stat(path)
                prefab_files.append((path, stat.st_mtime_ns, stat.st_size))
            except OSError:
                prefab_files.append((path, None, None))
        return id(self.world), f"{self.ops!r}|prefabs={prefab_files!r}"

    def effective_ops(self) -> List[Any]:
        """Return operations after applying behavioral-assembly tombstones."""
        removed = {
            op.operation_id
            for op in self.ops
            if isinstance(op, RemoveBehavioralPrefabOp)
        }
        return [
            op for op in self.ops
            if not isinstance(op, RemoveBehavioralPrefabOp)
            and not (
                isinstance(op, ImportBehavioralPrefabOp)
                and op.operation_id in removed
            )
        ]

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
        """Return the current level baseline DAT bytes, reloading if needed."""
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

    def accept_saved_baseline(
        self,
        materialized: patcher.World,
        dat_bytes: bytes,
        *,
        bsp_changed: bool = False,
    ) -> None:
        """Promote one successfully written DAT to the in-memory baseline."""
        if not dat_bytes:
            raise ValueError("A saved level baseline cannot be empty")
        self.world = copy.deepcopy(materialized)
        self._raw_bytes = bytes(dat_bytes)
        self.ops.clear()
        self.redo_ops.clear()
        # Every derived plan/view must now be rebuilt from the committed bytes
        # and world.  The parsed BSP itself remains valid for object-only
        # edits; discard it only when the committed operation changed BSP.
        if bsp_changed:
            self.bsp = None
        self._editor_materialized_cache = None
        self._door_plan_cache = None
        self._prefab_plan_cache = None
        self._preview_bsp_cache = None

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
        for op in self.effective_ops():
            if isinstance(op, DeleteOp):
                deletes.append(op.target_index)
            elif isinstance(op, CloneDoorOp):
                op.apply_to(self, w)
            elif isinstance(op, ImportPrefabBspOp):
                op.apply_to(w, self)
            elif isinstance(op, ImportBehavioralPrefabOp):
                op.apply_to(w)
            else:
                op.apply_to(w)
        for idx in sorted(deletes, reverse=True):
            del w.objects[idx]
        return w

    def editor_materialize(self) -> patcher.World:
        """Return a shared, read-only materialized snapshot for editor views."""
        key = self._editor_state_key()
        cached = self._editor_materialized_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        world = self.materialize()
        self._editor_materialized_cache = (key, world)
        return world

    def materialized_object_count(self) -> int:
        return len(self.editor_materialize().objects)

    def door_clone_plans(self) -> List[door_clone.DoorClonePlan]:
        """Return BSP/controller clone plans for pending CloneDoorOps."""
        assert self.world is not None
        effective_ops = self.effective_ops()
        if not any(isinstance(op, CloneDoorOp) for op in effective_ops):
            return []
        key = self._editor_state_key()
        cached = self._door_plan_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        w = copy.deepcopy(self.world)
        deletes = []
        plans: List[door_clone.DoorClonePlan] = []
        for op in effective_ops:
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
            if isinstance(op, ImportBehavioralPrefabOp):
                op.apply_to(w)
                continue
            op.apply_to(w)
        for idx in sorted(deletes, reverse=True):
            del w.objects[idx]
        self._door_plan_cache = (key, plans)
        return plans

    def prefab_import_plans(
        self,
        *,
        require_runtime_bsp: bool = False,
    ) -> List[prefab_import.PrefabBspImportPlan]:
        """Return BSP plans for static and passive behavioral imports."""
        if not any(
            isinstance(op, (ImportPrefabBspOp, ImportBehavioralPrefabOp))
            for op in self.effective_ops()
        ):
            return []
        key = self._editor_state_key()
        cached = self._prefab_plan_cache
        if not require_runtime_bsp and cached is not None and cached[0] == key:
            return cached[1]
        base = self.get_bsp()
        if base is None:
            return []
        working_bsp = base
        working_object_names = [
            str(obj.get("Name") or "")
            for obj in (self.world.objects if self.world is not None else [])
        ]
        plans: List[prefab_import.PrefabBspImportPlan] = []
        for op in self.effective_ops():
            if isinstance(op, ImportBehavioralPrefabOp):
                before = self.objects_before_op(op)
                existing_names = [
                    str(obj.get("Name") or "") for obj in before
                ]
                plan = op.build_bsp_plan(
                    working_bsp,
                    existing_names=existing_names,
                    require_runtime_bsp=require_runtime_bsp,
                )
                if plan is None:
                    continue
                plans.append(plan)
                working_object_names.extend(plan.visible_model_names)
                working_bsp = prefab_import.build_preview_bsp(working_bsp, [plan])
                continue
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
                placement_anchor=op.placement_anchor,
                allow_unsafe_visibility=bool(op.allow_unsafe_visibility),
                target_object_names=working_object_names,
                allow_generated_bsp=not require_runtime_bsp,
                validate_runtime_bsp=require_runtime_bsp,
            )
            plans.append(plan)
            working_object_names.extend(plan.visible_model_names)
            working_object_names.extend(plan.collision_model_names)
            working_bsp = prefab_import.build_preview_bsp(working_bsp, [plan])
        if not require_runtime_bsp:
            self._prefab_plan_cache = (key, plans)
        return plans

    def behavioral_prefab_import_plans(
        self,
    ) -> List[prefab_behavioral.BehavioralPrefabImportPlan]:
        """Return deterministic plans for promoted behavioral prefab ops."""
        plans = []
        for op in self.effective_ops():
            if not isinstance(op, ImportBehavioralPrefabOp):
                continue
            before = self.objects_before_op(op)
            plan = op.build_plan(
                existing_names=[str(obj.get("Name") or "") for obj in before]
            )
            plan.require_ready()
            plans.append(plan)
        return plans

    def behavioral_prefab_blocking_issues(
        self,
        plans: Sequence[prefab_behavioral.BehavioralPrefabImportPlan],
        materialized: patcher.World,
    ) -> List[Dict[str, Any]]:
        """Reject links that became dangling after placement or later edits."""
        if not plans:
            return []
        target_bsp = self.get_bsp()
        target_bytes = self.source_bytes()
        target_names = [str(obj.get("Name") or "") for obj in materialized.objects]
        issues: List[Dict[str, Any]] = []
        for plan in plans:
            for message in prefab_behavioral.validate_plan_target_bindings(
                plan,
                target_object_names=target_names,
                target_bsp=target_bsp,
                target_dat_bytes=target_bytes,
            ):
                full_message = (
                    f"Behavioral prefab {os.path.basename(plan.source_path)} -> "
                    f"{plan.root_name}: {message}. Update the binding or remove the import."
                )
                issues.append({
                    "code": "behavioral_prefab_dangling_binding",
                    "message": full_message,
                    "level": self.display_name or self.rez_vpath or self.path,
                    "prefab": plan.source_path,
                    "root_name": plan.root_name,
                })
        return issues

    def runtime_unsafe_prefab_reasons(self) -> List[str]:
        """Return non-overridable reasons this edit cannot produce a game DAT."""
        reasons: List[str] = []
        label = self.display_name or self.rez_vpath or self.path
        for op in self.effective_ops():
            if isinstance(op, ImportPrefabBspOp):
                version = _prefab_file_version(op.prefab_path)
                if op.preview_only or version == 1249:
                    reasons.append(
                        f"{label}: {os.path.basename(op.prefab_path)} -> {op.new_name} "
                        "uses editor-preview ED BSP. Replace it with a catalog game "
                        "model or a DEdit-compiled v66 DAT import."
                    )
                if _normalized_prefab_collision_mode(op.collision_mode) == "box_approx":
                    reasons.append(
                        f"{label}: {op.new_name} uses a generated collision box. "
                        "Choose authored PhysicsBSP collision or no helper."
                    )
            elif isinstance(op, ImportBehavioralPrefabOp):
                if _prefab_file_version(op.prefab_path) != 1249:
                    continue
                try:
                    if op._analyze().graph.brushes:
                        reasons.append(
                            f"{label}: behavioral prefab "
                            f"{os.path.basename(op.prefab_path)} contains ED brushes. "
                            "Compile it to a v66 DAT with DEdit or import only its "
                            "runtime objects."
                        )
                except Exception as exc:
                    reasons.append(
                        f"{label}: cannot validate behavioral prefab "
                        f"{os.path.basename(op.prefab_path)}: {exc}"
                    )
        return reasons

    def preview_bsp(self):
        """Return BSP geometry plus pending physical door clone previews."""
        base = self.get_bsp()
        if base is None:
            return None
        if not any(
            isinstance(op, (CloneDoorOp, ImportPrefabBspOp, ImportBehavioralPrefabOp))
            for op in self.effective_ops()
        ):
            return base
        key = self._editor_state_key()
        cached = self._preview_bsp_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        plans = self.door_clone_plans()
        prefab_plans = self.prefab_import_plans()
        preview = base
        if plans:
            preview = door_clone.build_preview_bsp(preview, plans)
        if prefab_plans:
            preview = prefab_import.build_preview_bsp(preview, prefab_plans)
        self._preview_bsp_cache = (key, preview)
        return preview

    def materialized_existing_indices(self) -> List[int]:
        """Map materialized existing-object rows back to baseline indices."""
        assert self.world is not None
        deleted = {
            op.target_index
            for op in self.effective_ops()
            if isinstance(op, DeleteOp)
        }
        return [
            idx for idx in range(len(self.world.objects))
            if idx not in deleted
        ]

    def unresolved_conversion_indices(self) -> Set[int]:
        report = self.conversion_report or {}
        records = report.get("records") if isinstance(report, dict) else None
        if not isinstance(records, list):
            return set()
        out: Set[int] = set()
        for record in records:
            if not isinstance(record, dict):
                continue
            if record.get("status") != "unsupported_actor_preserved":
                continue
            try:
                index = int(record.get("output_index", -1))
            except (TypeError, ValueError):
                continue
            if index >= 0:
                out.add(index)
        return out

    def unresolved_conversion_count(self) -> int:
        deleted = {
            op.target_index for op in self.effective_ops() if isinstance(op, DeleteOp)
        }
        return len(self.unresolved_conversion_indices() - deleted)

    def is_unresolved_conversion_object(self, world_index: int) -> bool:
        baseline_index = self.existing_index_for_materialized(world_index)
        return (
            baseline_index is not None
            and baseline_index in self.unresolved_conversion_indices()
        )

    def conversion_blocking_issues(self) -> List[Dict[str, Any]]:
        count = self.unresolved_conversion_count()
        if not count:
            return []
        report = self.conversion_report or {}
        classes = report.get("unresolved_actor_classes", [])
        classes = [str(value) for value in classes] if isinstance(classes, list) else []
        suffix = f": {', '.join(classes)}" if classes else ""
        return [{
            "code": "unsupported_lomm_actors",
            "message": (
                f"{count} LoMM actor(s) in {self.display_name or self.rez_vpath} "
                f"are not registered by MM9{suffix}. Remove them before install."
            ),
            "count": count,
            "classes": classes,
            "level": self.display_name or self.rez_vpath or self.path,
        }]

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
        for op in self.effective_ops():
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
            if isinstance(op, ImportBehavioralPrefabOp):
                created = op.apply_to(w)
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
        for op in self.effective_ops():
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
            if isinstance(op, ImportBehavioralPrefabOp):
                op.apply_to(w)
                continue
            op.apply_to(w)
        return w.objects

    def pending_add_offset_for_materialized(self, world_index: int) -> Optional[Tuple[Any, int]]:
        offset = world_index - len(self.materialized_existing_indices())
        if offset < 0:
            return None
        add_ops = [
            op for op in self.effective_ops()
            if isinstance(op, (AddOp, CloneDoorOp, ImportPrefabBspOp, ImportBehavioralPrefabOp))
        ]
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
        add_ops = [
            op for op in self.effective_ops()
            if isinstance(op, (AddOp, CloneDoorOp, ImportPrefabBspOp, ImportBehavioralPrefabOp))
        ]
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
        imports = [
            op for op in self.effective_ops() if isinstance(op, ImportPrefabBspOp)
        ]
        return imports[offset] if offset < len(imports) else None

    def add_offset_for_materialized(self, world_index: int) -> Optional[int]:
        offset = world_index - len(self.materialized_existing_indices())
        if offset < 0:
            return None
        add_index = 0
        cursor = 0
        for op, count in zip(
            [
                op for op in self.effective_ops()
                if isinstance(op, (AddOp, CloneDoorOp, ImportPrefabBspOp, ImportBehavioralPrefabOp))
            ],
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

def _rude_metadata_signature(
    metadata: Optional[rude_model.RudeDialogueMetadata],
) -> Optional[Tuple[int, str, int, str]]:
    if metadata is None:
        return None
    return (
        int(metadata.npc_nbr),
        str(metadata.name),
        int(metadata.initial_state),
        str(metadata.opening_blurb),
    )


@dataclass
class RudeAssetEdit:
    """A project-level RUDE asset, independent of any placed world object."""

    npc_nbr: int
    dialogue: rude_model.RudeDialogue
    source_virtual_path: str
    original_metadata: Optional[rude_model.RudeDialogueMetadata] = None
    original_dialogue_bytes: Optional[bytes] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.npc_nbr = int(self.npc_nbr)
        self.source_virtual_path = (
            str(self.source_virtual_path or f"RUDE/NPC{self.npc_nbr}")
            .replace("\\", "/")
        )
        self.validate_identity()

    @property
    def metadata(self) -> rude_model.RudeDialogueMetadata:
        return self.dialogue.metadata

    @property
    def is_new(self) -> bool:
        return self.original_dialogue_bytes is None

    @property
    def dialogue_changed(self) -> bool:
        return self.is_new or self.dialogue.to_bytes() != self.original_dialogue_bytes

    @property
    def name_changed(self) -> bool:
        if self.original_metadata is None:
            return True
        return self.metadata.name != self.original_metadata.name

    @property
    def blurb_changed(self) -> bool:
        if self.original_metadata is None:
            return True
        return (
            self.metadata.initial_state != self.original_metadata.initial_state
            or self.metadata.opening_blurb != self.original_metadata.opening_blurb
        )

    @property
    def metadata_changed(self) -> bool:
        return self.name_changed or self.blurb_changed

    @property
    def is_dirty(self) -> bool:
        self.validate_identity()
        return self.dialogue_changed or self.metadata_changed

    def validate_identity(self) -> None:
        if self.metadata.npc_nbr != self.npc_nbr:
            raise ValueError(
                f"RUDE asset NPC{self.npc_nbr} cannot change identity to "
                f"NPC{self.metadata.npc_nbr}"
            )
        expected = f"RUDE/NPC{self.npc_nbr}".casefold()
        actual = self.source_virtual_path
        if actual.lower().endswith(".rude"):
            actual = actual[:-5]
        if actual.casefold() != expected:
            raise ValueError(
                f"RUDE asset NPC{self.npc_nbr} has unexpected resource path "
                f"{self.source_virtual_path!r}"
            )
        for choice in self.dialogue.choices_in_file_order:
            if choice.npc_nbr != self.npc_nbr:
                raise ValueError(
                    f"RUDE asset NPC{self.npc_nbr} contains a row for "
                    f"NPC{choice.npc_nbr}"
                )

    def summary(self) -> str:
        state_count = len(self.dialogue.states)
        choice_count = len(self.dialogue.choices_in_file_order)
        kind = "new" if self.is_new else "edit"
        return (
            f"{kind} RUDE/NPC{self.npc_nbr}: {state_count} state(s), "
            f"{choice_count} choice(s)"
        )


@dataclass
class Project:
    levels: List[LevelEdit] = field(default_factory=list)
    rude_rez_path: Optional[str] = None
    scripts_rez_path: Optional[str] = None
    next_npc_nbr: int = 437
    work_dir:    Optional[str] = None      # where save() writes by default
    backup_root: Optional[str] = None      # where backups live

    # Track sources we've already backed up this session, so we don't do it twice.
    backed_up_archives: List[str] = field(default_factory=list)
    rude_assets: Dict[int, RudeAssetEdit] = field(default_factory=dict)
    dialogue_script_assets: Dict[int, rude_script.DialogueScriptAssetEdit] = field(
        default_factory=dict)

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

    def open_rude_asset(self, npc_nbr: int) -> RudeAssetEdit:
        """Load an existing normal or special NPC resource for independent editing."""
        npc_nbr = int(npc_nbr)
        existing = self.rude_assets.get(npc_nbr)
        if existing is not None:
            # A project reopened after Install Output can still serialize this
            # asset as "new" even though its exact bytes now live in RUDE.REZ.
            # Establish that baseline before the editor lets the user change
            # it, so the next save is a normal edit rather than a collision.
            if existing.is_new:
                self.reconcile_external_asset_baselines()
            return existing
        if not self.rude_rez_path or not os.path.isfile(self.rude_rez_path):
            raise FileNotFoundError("RUDE.REZ source archive was not found")

        from core import rezmgr

        with rezmgr.RezReader(self.rude_rez_path) as reader:
            npcname_vpath = self._find_rez_entry(
                reader, ("RUDE/NPCNAME", "RUDE/NPCNAME.RUDE"))
            topblurb_vpath = self._find_rez_entry(
                reader, ("RUDE/TOPBLURB", "RUDE/TOPBLURB.RUDE"))
            dialogue_vpath = self._find_rez_entry(reader, (
                f"RUDE/NPC{npc_nbr}",
                f"RUDE/NPC{npc_nbr}.RUDE",
            ))
            if npcname_vpath is None or topblurb_vpath is None:
                raise FileNotFoundError("RUDE.REZ is missing NPCNAME or TOPBLURB")
            if dialogue_vpath is None:
                raise FileNotFoundError(
                    f"RUDE.REZ does not contain RUDE/NPC{npc_nbr}")
            catalog = rude_model.RudeMetadataCatalog.from_bytes(
                reader.extract_to_bytes(npcname_vpath),
                reader.extract_to_bytes(topblurb_vpath),
            )
            metadata = catalog.metadata_for(npc_nbr)
            dialogue_bytes = reader.extract_to_bytes(dialogue_vpath)

        dialogue = rude_model.RudeDialogue.from_bytes(
            metadata,
            dialogue_bytes,
            resource=dialogue_vpath,
        )
        asset = RudeAssetEdit(
            npc_nbr=npc_nbr,
            dialogue=dialogue,
            source_virtual_path=dialogue_vpath,
            original_metadata=copy.deepcopy(metadata),
            original_dialogue_bytes=dialogue_bytes,
        )
        self.rude_assets[npc_nbr] = asset
        return asset

    def create_rude_asset(
        self,
        dialogue: rude_model.RudeDialogue,
    ) -> RudeAssetEdit:
        """Stage a new RUDE dialogue without placing an NPC world object."""
        npc_nbr = int(dialogue.metadata.npc_nbr)
        if npc_nbr in self.rude_assets:
            raise ValueError(f"RUDE asset NPC{npc_nbr} is already open")
        if not self.rude_rez_path or not os.path.isfile(self.rude_rez_path):
            raise FileNotFoundError("RUDE.REZ source archive was not found")

        from core import rezmgr

        with rezmgr.RezReader(self.rude_rez_path) as reader:
            npcname_vpath = self._find_rez_entry(
                reader, ("RUDE/NPCNAME", "RUDE/NPCNAME.RUDE"))
            topblurb_vpath = self._find_rez_entry(
                reader, ("RUDE/TOPBLURB", "RUDE/TOPBLURB.RUDE"))
            if npcname_vpath is None or topblurb_vpath is None:
                raise FileNotFoundError("RUDE.REZ is missing NPCNAME or TOPBLURB")
            catalog = rude_model.RudeMetadataCatalog.from_bytes(
                reader.extract_to_bytes(npcname_vpath),
                reader.extract_to_bytes(topblurb_vpath),
            )
            dialogue_entry = reader.find(f"RUDE/NPC{npc_nbr}")
            conflicts = []
            if dialogue_entry is not None:
                conflicts.append(f"NPC{npc_nbr} already exists")
            if catalog.has_name(npc_nbr):
                conflicts.append(f"NPCNAME already has {npc_nbr}")
            if catalog.has_blurb(npc_nbr):
                conflicts.append(f"TOPBLURB already has {npc_nbr}")
            if conflicts:
                raise ValueError(
                    f"RUDE asset conflict for NPC{npc_nbr}: " + "; ".join(conflicts))

        asset = RudeAssetEdit(
            npc_nbr=npc_nbr,
            dialogue=dialogue,
            source_virtual_path=f"RUDE/NPC{npc_nbr}",
        )
        self.rude_assets[npc_nbr] = asset
        return asset

    def create_simple_rude_asset(
        self,
        registration: "RudeRegistration",
    ) -> RudeAssetEdit:
        metadata = rude_model.RudeDialogueMetadata(
            npc_nbr=registration.npc_nbr,
            name=registration.name,
            initial_state=registration.npc_nbr,
            opening_blurb=registration.blurb,
        )
        return self.create_rude_asset(
            rude_model.make_simple_dialogue(metadata, registration.lines))

    def close_rude_asset(self, npc_nbr: int, *, discard: bool = False) -> None:
        asset = self.rude_assets.get(int(npc_nbr))
        if asset is None:
            return
        if asset.is_dirty and not discard:
            raise ValueError(
                f"RUDE asset NPC{npc_nbr} has pending edits; pass discard=True to close it")
        del self.rude_assets[int(npc_nbr)]

    def load_script_source(self, virtual_path: str) -> Tuple[str, str]:
        """Load one shipped script by ScriptName/archive path without editing it."""
        if not self.scripts_rez_path or not os.path.isfile(self.scripts_rez_path):
            raise FileNotFoundError("SCRIPTS.REZ source archive was not found")
        requested = rude_script.canonical_script_path(virtual_path)
        from core import rezmgr

        with rezmgr.RezReader(self.scripts_rez_path) as reader:
            by_key = {
                _script_rez_key(path): path
                for path in reader.list_paths()
            }
            actual = by_key.get(_script_rez_key(requested))
            if actual is None:
                raise FileNotFoundError(
                    f"SCRIPTS.REZ does not contain {requested}"
                )
            data = reader.extract_to_bytes(actual)
        return requested, data.decode("latin-1")

    def upsert_dialogue_script_asset(
        self,
        integration: rude_script.DialogueScriptIntegration,
    ) -> rude_script.DialogueScriptAssetEdit:
        """Stage a reviewed generated script independently from world placement."""
        candidate = copy.deepcopy(integration)
        candidate.validate()
        candidate.to_bytes()
        npc_nbr = candidate.npc_nbr
        target_key = _script_rez_key(candidate.virtual_path)
        for other_npc, other in self.dialogue_script_assets.items():
            if other_npc != npc_nbr and _script_rez_key(
                    other.integration.virtual_path) == target_key:
                raise ValueError(
                    f"NPC{other_npc} already stages {candidate.virtual_path}"
                )
        existing = self.dialogue_script_assets.get(npc_nbr)
        if existing is not None:
            if _script_rez_key(existing.integration.virtual_path) != target_key:
                raise ValueError(
                    "A staged dialogue script's resource path cannot be renamed; "
                    "remove it and create a new asset instead"
                )
            existing.integration = candidate
            return existing
        asset = rude_script.DialogueScriptAssetEdit(candidate)
        self.dialogue_script_assets[npc_nbr] = asset
        return asset

    def close_dialogue_script_asset(
        self,
        npc_nbr: int,
        *,
        discard: bool = False,
    ) -> None:
        asset = self.dialogue_script_assets.get(int(npc_nbr))
        if asset is None:
            return
        if asset.is_dirty and not discard:
            raise ValueError(
                f"NPC{npc_nbr} dialogue script has pending edits; "
                "pass discard=True to remove it"
            )
        del self.dialogue_script_assets[int(npc_nbr)]

    def reconcile_external_asset_baselines(
        self,
        *,
        rude_rez_path: Optional[str] = None,
        scripts_rez_path: Optional[str] = None,
    ) -> List[str]:
        """Promote exact externally-installed assets to clean baselines.

        Save writes to a reviewed output directory, while Install Output later
        replaces the live archives.  A project asset created before that
        install still has no original baseline, so without reconciliation it
        mistakes its own installed resource for an unrelated ID collision.
        Only byte-for-byte dialogue/script matches with identical RUDE metadata
        are accepted; any real collision or external edit remains an error.
        """
        reconciled: List[str] = []
        rude_source = rude_rez_path or self.rude_rez_path
        if (
            self.rude_assets
            and rude_source
            and os.path.isfile(rude_source)
        ):
            from core import rezmgr
            with rezmgr.RezReader(rude_source) as reader:
                npcname_vpath = self._find_rez_entry(
                    reader, ("RUDE/NPCNAME", "RUDE/NPCNAME.RUDE"))
                topblurb_vpath = self._find_rez_entry(
                    reader, ("RUDE/TOPBLURB", "RUDE/TOPBLURB.RUDE"))
                if npcname_vpath is not None and topblurb_vpath is not None:
                    catalog = rude_model.RudeMetadataCatalog.from_bytes(
                        reader.extract_to_bytes(npcname_vpath),
                        reader.extract_to_bytes(topblurb_vpath),
                    )
                    existing_paths = set(reader.list_paths())
                    for npc_nbr, asset in self.rude_assets.items():
                        actual = self._find_existing_vpath(
                            existing_paths, f"RUDE/NPC{npc_nbr}")
                        if actual is None:
                            continue
                        try:
                            source_metadata = catalog.metadata_for(npc_nbr)
                        except KeyError:
                            continue
                        source_bytes = reader.extract_to_bytes(actual)
                        if (
                            source_bytes == asset.dialogue.to_bytes()
                            and _rude_metadata_signature(source_metadata)
                            == _rude_metadata_signature(asset.metadata)
                        ):
                            was_dirty = asset.is_dirty
                            asset.source_virtual_path = actual
                            asset.original_dialogue_bytes = source_bytes
                            asset.original_metadata = copy.deepcopy(source_metadata)
                            if was_dirty:
                                reconciled.append(f"RUDE/NPC{npc_nbr}")

        scripts_source = scripts_rez_path or self.scripts_rez_path
        if (
            self.dialogue_script_assets
            and scripts_source
            and os.path.isfile(scripts_source)
        ):
            from core import rezmgr
            with rezmgr.RezReader(scripts_source) as reader:
                by_key = {
                    _script_rez_key(path): path for path in reader.list_paths()
                }
                for asset in self.dialogue_script_assets.values():
                    actual = by_key.get(_script_rez_key(
                        asset.integration.virtual_path))
                    if actual is None:
                        continue
                    source_bytes = reader.extract_to_bytes(actual)
                    desired_bytes = asset.integration.to_bytes()
                    if source_bytes == desired_bytes:
                        was_dirty = asset.is_dirty
                        asset.original_script_bytes = source_bytes
                        if was_dirty:
                            reconciled.append(asset.integration.virtual_path)
        return reconciled

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
        return (
            any(L.effective_ops() for L in self.levels)
            or any(asset.is_dirty for asset in self.rude_assets.values())
            or any(
                asset.is_dirty for asset in self.dialogue_script_assets.values())
        )

    # ---------- save planning (explicit; user reviews before commit) ----------

    def _build_dat_write(self, L: LevelEdit, output_path: str) -> "DatWrite":
        """Build and validate the runtime write description for one level."""
        effective_ops = L.effective_ops()
        unsafe_prefabs = L.runtime_unsafe_prefab_reasons()
        if unsafe_prefabs:
            raise ValueError(
                "Cannot build an MM9 DAT from preview-grade prefab geometry:\n\n"
                + "\n".join(f"- {message}" for message in unsafe_prefabs)
            )
        materialized = L.materialize()
        door_clones = L.door_clone_plans()
        prefab_imports = L.prefab_import_plans(require_runtime_bsp=True)
        behavioral_prefab_imports = L.behavioral_prefab_import_plans()
        resource_prefab_imports = [
            op for op in effective_ops
            if isinstance(op, ImportResourcePrefabOp)
        ]
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
        blocking_issues = L.conversion_blocking_issues()
        blocking_issues.extend(
            L.behavioral_prefab_blocking_issues(
                behavioral_prefab_imports,
                materialized,
            )
        )
        validation_warnings.extend(
            issue["message"] for issue in blocking_issues
        )
        return DatWrite(
            source_path=L.path,
            output_path=output_path,
            ops_summary=[op.summary() for op in effective_ops],
            materialized=materialized,
            level_edit=L,
            backup_path=L.backup_path,
            door_clones=door_clones,
            prefab_imports=prefab_imports,
            behavioral_prefab_imports=behavioral_prefab_imports,
            resource_prefab_imports=resource_prefab_imports,
            validation_warnings=validation_warnings,
            blocking_issues=blocking_issues,
        )

    def build_runtime_dat(self, L: LevelEdit) -> Tuple[bytes, "DatWrite"]:
        """Serialize one level exactly as the save pipeline would.

        Clean levels reuse their original DAT bytes. Edited levels go through
        the same BSP writers and runtime validation guards used by Save.
        """
        d = self._build_dat_write(L, output_path="")
        if not L.effective_ops():
            return L.source_bytes(), d
        return self._dat_write_to_bytes(d), d

    def build_runtime_overlay_entries(self, L: LevelEdit) -> Dict[str, bytes]:
        """Build non-DAT loose resources required by one level preview."""
        self.reconcile_external_asset_baselines()
        entries = self.build_behavioral_script_overlay_entries([L])
        dialogue_scripts = self.build_dialogue_script_overlay_entries()
        folded_scripts = {
            _script_rez_key(path): path
            for path in entries
        }
        for path, data in dialogue_scripts.items():
            previous = folded_scripts.get(_script_rez_key(path))
            if previous is not None and entries[previous] != data:
                raise ValueError(f"conflicting runtime overlay resource {path}")
            entries[path] = data
            folded_scripts[_script_rez_key(path)] = path
        registrations = [
            RudeRegistration(**op.rude)
            for op in L.effective_ops()
            if isinstance(op, AddOp) and op.rude
        ]
        asset_edits = [
            copy.deepcopy(asset)
            for _npc_nbr, asset in sorted(self.rude_assets.items())
            if asset.is_dirty
        ]
        rude_entries = self.build_rude_overlay_entries(
            registrations,
            asset_edits=asset_edits,
        )
        folded = {
            str(path).replace("/", "\\").casefold(): path
            for path in entries
        }
        for path, data in rude_entries.items():
            key = str(path).replace("/", "\\").casefold()
            previous = folded.get(key)
            if previous is not None and entries[previous] != data:
                raise ValueError(f"conflicting runtime overlay resource {path}")
            entries[path] = data
            folded[key] = path
        return entries

    def save_plan(self) -> "SavePlan":
        self.reconcile_external_asset_baselines()
        batch_id = _timestamp()
        plan = SavePlan(batch_id=batch_id)
        for L in self.levels:
            effective_ops = L.effective_ops()
            if not effective_ops:
                continue
            plan.dats.append(self._build_dat_write(
                L,
                output_path=L.output_path(self.work_dir, batch_id),
            ))
            for op in effective_ops:
                if isinstance(op, AddOp) and op.rude:
                    plan.rude_entries.append(RudeRegistration(**op.rude))
        plan.rude_assets = [
            copy.deepcopy(asset)
            for _npc_nbr, asset in sorted(self.rude_assets.items())
            if asset.is_dirty
        ]
        plan.dialogue_script_assets = [
            copy.deepcopy(asset)
            for _npc_nbr, asset in sorted(self.dialogue_script_assets.items())
            if asset.is_dirty
        ]
        legacy_ids = {entry.npc_nbr for entry in plan.rude_entries}
        asset_ids = {asset.npc_nbr for asset in plan.rude_assets}
        overlap = sorted(legacy_ids & asset_ids)
        if overlap:
            joined = ", ".join(f"NPC{npc_nbr}" for npc_nbr in overlap)
            raise ValueError(
                f"RUDE assets are staged both independently and through legacy "
                f"NPC placement registrations: {joined}")
        self._populate_archive_patches(plan)
        self._populate_bsp_record_diff_reports(plan)
        return plan

    def _populate_bsp_record_diff_reports(self, plan: "SavePlan") -> None:
        for d in plan.dats:
            if not d.has_geometry_bsp_write():
                continue
            self._dat_write_to_bytes(d)

    def build_behavioral_script_overlay_entries(
        self,
        levels: Sequence[LevelEdit],
    ) -> Dict[str, bytes]:
        """Return validated loose script resources generated by *levels*."""
        script_assets: Dict[str, Tuple[str, bytes]] = {}
        for L in levels:
            for op in L.effective_ops():
                if not isinstance(op, ImportBehavioralPrefabOp):
                    continue
                for raw_path, raw_text in op.script_assets.items():
                    virtual_path = str(raw_path or "").replace("/", "\\").strip("\\")
                    folded = _script_rez_key(virtual_path)
                    if (
                        not folded.startswith("scripts\\mm9editor\\")
                        or os.path.splitext(virtual_path)[1].casefold() != ".scr"
                    ):
                        raise ValueError(
                            f"behavioral prefab generated an unsafe script path {raw_path!r}"
                        )
                    try:
                        data = str(raw_text).encode("latin-1")
                    except UnicodeEncodeError as exc:
                        raise ValueError(
                            f"generated script {virtual_path} is not Latin-1 encodable"
                        ) from exc
                    previous = script_assets.get(folded)
                    if previous is not None and previous[1] != data:
                        raise ValueError(
                            f"conflicting generated behavioral scripts target {virtual_path}"
                        )
                    script_assets[folded] = (virtual_path, data)
        return {
            path: data
            for _key, (path, data) in sorted(script_assets.items())
        }

    def build_dialogue_script_overlay_entries(
        self,
        assets: Optional[Sequence[rude_script.DialogueScriptAssetEdit]] = None,
    ) -> Dict[str, bytes]:
        """Return reviewed project-owned RUDE exit scripts for staging/preview."""
        selected = (
            list(assets)
            if assets is not None
            else list(self.dialogue_script_assets.values())
        )
        archived_paths: Dict[str, str] = {}
        archived_bytes: Dict[str, bytes] = {}
        if self.scripts_rez_path and os.path.isfile(self.scripts_rez_path):
            from core import rezmgr
            with rezmgr.RezReader(self.scripts_rez_path) as reader:
                archived_paths = {
                    _script_rez_key(path): path for path in reader.list_paths()
                }
                for asset in selected:
                    key = _script_rez_key(asset.integration.virtual_path)
                    actual = archived_paths.get(key)
                    if actual is not None:
                        archived_bytes[key] = reader.extract_to_bytes(actual)
        generated: Dict[str, Tuple[str, bytes]] = {}
        for asset in selected:
            if not asset.is_dirty:
                continue
            path = rude_script.canonical_script_path(
                asset.integration.virtual_path,
                require_editor_root=True,
            )
            data = asset.integration.to_bytes()
            key = _script_rez_key(path)
            archived = archived_bytes.get(key)
            if archived is not None:
                if archived == data:
                    # The exact generated asset was installed after this
                    # project created it.  Nothing needs to be overlaid.
                    continue
                if asset.original_script_bytes is None:
                    raise ValueError(
                        f"Generated dialogue script {path} already exists in "
                        "SCRIPTS.REZ; refusing to overwrite an untracked script"
                    )
                if archived != asset.original_script_bytes:
                    raise ValueError(
                        f"Generated dialogue script {path} changed in SCRIPTS.REZ "
                        "after the project opened it"
                    )
            previous = generated.get(key)
            if previous is not None and previous[1] != data:
                raise ValueError(f"conflicting dialogue scripts target {path}")
            generated[key] = (path, data)
        return {
            path: data
            for _key, (path, data) in sorted(generated.items())
        }

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

        script_levels = [
            d.level_edit for d in plan.dats if d.level_edit is not None
        ]
        behavioral_additions = self.build_behavioral_script_overlay_entries(
            script_levels)
        dialogue_additions = self.build_dialogue_script_overlay_entries(
            plan.dialogue_script_assets)
        additions = dict(behavioral_additions)
        addition_keys = {_script_rez_key(path): path for path in additions}
        for path, data in dialogue_additions.items():
            previous_path = addition_keys.get(_script_rez_key(path))
            if previous_path is not None and additions[previous_path] != data:
                raise ValueError(f"conflicting generated scripts target {path}")
            additions[path] = data
            addition_keys[_script_rez_key(path)] = path
        if additions:
            if not self.scripts_rez_path or not os.path.isfile(self.scripts_rez_path):
                raise FileNotFoundError(
                    "SCRIPTS.REZ is required to save generated script assets"
                )
            if not self.work_dir or not plan.batch_id:
                raise ValueError(
                    "an output work directory is required to stage generated scripts"
                )
            output = os.path.join(
                self.work_dir,
                plan.batch_id,
                "data",
                os.path.basename(self.scripts_rez_path),
            )
            plan.archive_patches.append(ArchivePatch(
                source_archive=self.scripts_rez_path,
                output_archive=output,
                entries=list(additions),
                kind=(
                    "scripts"
                    if behavioral_additions and dialogue_additions
                    else "dialogue_scripts"
                    if dialogue_additions
                    else "behavioral_scripts"
                ),
                additions=additions,
            ))

        # A staged conversion can also contain LoMM model/skin/sound assets.
        # Carry those already-patched archives into subsequent editor batches.
        carried = set()
        for d in plan.dats:
            L = d.level_edit
            if not L or not L.conversion_stage_dir or not self.work_dir:
                continue
            stage_data = os.path.join(L.conversion_stage_dir, "data")
            if not os.path.isdir(stage_data):
                continue
            for name in ("MODELS.REZ", "SKINS.REZ", "SOUNDS.REZ"):
                source = os.path.join(stage_data, name)
                key = os.path.abspath(source).casefold()
                if key in carried or not os.path.isfile(source):
                    continue
                carried.add(key)
                plan.archive_patches.append(ArchivePatch(
                    source_archive=source,
                    output_archive=os.path.join(
                        self.work_dir, plan.batch_id, "data", name,
                    ),
                    entries=[],
                    kind="conversion_asset",
                ))

        if plan.has_rude_changes():
            if not self.rude_rez_path or not os.path.isfile(self.rude_rez_path):
                raise FileNotFoundError("RUDE.REZ source archive was not found")
            if not self.work_dir or not plan.batch_id:
                raise ValueError(
                    "an output work directory is required to stage RUDE assets")
            output = os.path.join(
                self.work_dir, plan.batch_id, "data",
                os.path.basename(self.rude_rez_path),
            )
            entries: List[str] = []
            if plan.rude_entries:
                entries.extend(("RUDE/NPCNAME", "RUDE/TOPBLURB"))
                entries.extend(f"RUDE/NPC{r.npc_nbr}" for r in plan.rude_entries)
            for asset in plan.rude_assets:
                if asset.name_changed:
                    entries.append("RUDE/NPCNAME")
                if asset.blurb_changed:
                    entries.append("RUDE/TOPBLURB")
                if asset.dialogue_changed:
                    entries.append(f"RUDE/NPC{asset.npc_nbr}")
            entries = list(dict.fromkeys(entries))
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
        plan.executed_successfully = False
        for write in plan.dats:
            write.committed_dat_bytes = None
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

        for patch in plan.archive_patches:
            if patch.kind != "conversion_asset":
                continue
            import shutil
            os.makedirs(os.path.dirname(patch.output_archive), exist_ok=True)
            shutil.copy2(patch.source_archive, patch.output_archive)
            log.append(f"carried staged conversion asset archive {patch.output_archive}")

        script_patch = plan.scripts_archive_patch()
        if script_patch is not None:
            log.extend(self.execute_scripts_rez(script_patch))

        if plan.has_rude_changes() and plan.rude_archive_patch() is not None:
            log.extend(self.execute_rude_rez(plan))

        manifest = self._write_manifest(plan)
        if manifest:
            log.append(f"wrote {manifest}")
        plan.executed_successfully = True
        return log

    def accept_save_plan(self, plan: "SavePlan") -> None:
        """Promote every successfully written DAT in *plan* atomically.

        Archive execution and in-memory promotion are deliberately separate:
        a failed write must leave pending editor operations intact.  Validate
        the complete plan before mutating any open level for the same reason.
        """
        if not plan.executed_successfully:
            raise ValueError("Save plan did not complete successfully")
        committed = []
        for write in plan.dats:
            level = write.level_edit
            if level is None or not any(level is item for item in self.levels):
                raise ValueError("Save plan contains a level that is not open")
            if write.committed_dat_bytes is None:
                raise ValueError(
                    f"Save plan has no committed DAT bytes for {write.source_path}"
                )
            committed.append((
                level,
                write.materialized,
                write.committed_dat_bytes,
                write.has_geometry_bsp_write(),
            ))
        for level, materialized, dat_bytes, bsp_changed in committed:
            level.accept_saved_baseline(
                materialized,
                dat_bytes,
                bsp_changed=bsp_changed,
            )

    def execute_scripts_rez(self, patch: "ArchivePatch") -> List[str]:
        """Write reviewed generated script assets into a complete SCRIPTS.REZ."""
        source_rez = patch.source_archive
        output_rez = patch.output_archive
        if not source_rez or not os.path.isfile(source_rez):
            raise FileNotFoundError("SCRIPTS.REZ source archive was not found")
        self._maybe_backup_archive(source_rez)
        os.makedirs(os.path.dirname(os.path.abspath(output_rez)) or ".", exist_ok=True)

        from core import rezmgr

        with rezmgr.RezReader(source_rez) as reader:
            existing_paths = {
                _script_rez_key(path): path
                for path in reader.list_paths()
            }
        log = []
        with rezmgr.RezWriter(source_rez, output_rez) as writer:
            for virtual_path, data in sorted(patch.additions.items()):
                existing = existing_paths.get(_script_rez_key(virtual_path))
                if existing is None:
                    writer.add(virtual_path, data)
                else:
                    writer.replace(existing, data)
                self._write_changed_entry_copy(output_rez, virtual_path, data)
                log.append(f"  staged {virtual_path}")
            writer.commit()
        return [f"wrote {output_rez}"] + log

    def execute_behavioral_scripts_rez(self, patch: "ArchivePatch") -> List[str]:
        """Compatibility alias for the original Phase-6 script staging API."""
        return self.execute_scripts_rez(patch)

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
        committed: List[Tuple["DatWrite", bytes]] = []
        with rezmgr.RezWriter(source_rez, output) as writer:
            for d in writes:
                L = d.level_edit
                assert L and L.rez_path and L.rez_vpath
                if os.path.abspath(L.rez_path) != os.path.abspath(source_rez):
                    raise ValueError("mixed source archives in one REZ write group")
                data = self._dat_write_to_bytes(d)
                writer.replace(L.rez_vpath, data)
                self._write_changed_entry_copy(output, L.rez_vpath, data)
                committed.append((d, data))
                log.append(
                    f"  patched {L.rez_vpath} ({len(d.materialized.objects)} objects)")
            writer.commit()
        for write, data in committed:
            write.committed_dat_bytes = data
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
        bsp_clones = [sub for plan in d.door_clones for sub in plan.submodels]
        bsp_clones.extend(sub for plan in d.prefab_imports for sub in plan.submodels)
        if not bsp_clones:
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
        data = self._validate_geometry_dat_bytes(d, data)
        return self._validate_bsp_record_diff_guard(d, source_dat, data)

    def _validate_bsp_record_diff_guard(
        self,
        d: "DatWrite",
        source_dat: Optional[bytes],
        output_dat: bytes,
    ) -> bytes:
        if source_dat is None or not d.has_geometry_bsp_write():
            return output_dat
        d.dat_section_diff_report = _dat_section_diff_report(source_dat, output_dat)
        report = bsp_record_inspector.diff_dat_records(source_dat, output_dat)
        d.bsp_record_diff_report = bsp_record_inspector.report_to_dict(report)
        for diff in report.model_diffs:
            if diff.name.lower() != "physicsbsp":
                continue
            if not _physics_bsp_has_blocked_record_changes(diff):
                continue
            raise ValueError(
                "Blocked DAT save: PhysicsBSP compiled record changed. "
                "The current DAT writer must not rewrite PhysicsBSP "
                "points, planes, polygon centers, point normals, leaves, nodes, "
                "or other derived record data. "
                f"PhysicsBSP changed bytes={diff.byte_diff_count}, "
                f"points={diff.moved_points.changed_count}, "
                f"planes={diff.changed_planes.changed_count}, "
                f"polygon_centers={diff.changed_polygon_centers.changed_count}, "
                f"point_normals={diff.changed_point_normals.changed_count}, "
                f"unknown_structural={diff.unknown_structural_changed_bytes}."
            )
        return output_dat

    def _validate_geometry_dat_bytes(self, d: "DatWrite", data: bytes) -> bytes:
        required_names: List[str] = []
        required_names.extend(sub.new_name for plan in d.door_clones for sub in plan.submodels)
        required_names.extend(sub.new_name for plan in d.prefab_imports for sub in plan.submodels)
        validation = output_validation.validate_geometry_dat(
            data,
            expected_object_count=len(d.materialized.objects),
            required_bsp_names=required_names,
        )
        validation.raise_for_errors()
        prefab_names = [
            sub.new_name for plan in d.prefab_imports for sub in plan.submodels
        ]
        if prefab_names and validation.parsed_bsp is not None:
            imported_models = [
                validation.parsed_bsp.model_by_name(name)
                for name in prefab_names
            ]
            prefab_import.validate_compiled_runtime_models(
                data,
                validation.parsed_bsp,
                [model for model in imported_models if model is not None],
            )
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
        if (
            not (plan.dats or plan.archive_patches)
            or not self.work_dir
            or not plan.batch_id
        ):
            return None
        manifest_path = os.path.join(self.work_dir, plan.batch_id, "manifest.json")
        try:
            os.makedirs(os.path.dirname(os.path.abspath(manifest_path)) or ".",
                        exist_ok=True)
            doc = {
                "version": 1,
                "saved_at": plan.batch_id,
                "archives": self._manifest_archives(plan),
                "blocking_issues": [
                    issue for d in plan.dats for issue in d.blocking_issues
                ],
                "dats": [
                    {
                        "source_path": d.source_path,
                        "backup_path": d.backup_path,
                        "output_path": d.output_path,
                        "objects_after": d.stats()["objects_after"],
                        "door_clones": d.stats()["door_clones"],
                        "prefab_imports": d.stats()["prefab_imports"],
                        "resource_prefab_imports": d.stats()["resource_prefab_imports"],
                        "behavioral_prefab_imports": d.stats()["behavioral_prefab_imports"],
                        "geometry_edits": d.geometry_manifest_details(),
                        "ops_summary": d.ops_summary,
                        "validation_warnings": d.validation_warnings,
                        "blocking_issues": d.blocking_issues,
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
                "rude_assets": [
                    {
                        "npc_nbr": asset.npc_nbr,
                        "name": asset.metadata.name,
                        "source_virtual_path": asset.source_virtual_path,
                        "new": asset.is_new,
                        "metadata_changed": asset.metadata_changed,
                        "dialogue_changed": asset.dialogue_changed,
                        "states": len(asset.dialogue.states),
                        "choices": len(asset.dialogue.choices_in_file_order),
                    }
                    for asset in plan.rude_assets
                ],
                "dialogue_script_assets": [
                    {
                        "npc_nbr": asset.npc_nbr,
                        "source_virtual_path": asset.integration.virtual_path,
                        "script_name": asset.integration.script_name,
                        "base_virtual_path": asset.integration.base_virtual_path,
                        "hooks": len(asset.integration.hooks),
                        "new": asset.is_new,
                    }
                    for asset in plan.dialogue_script_assets
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

    def build_rude_overlay_entries(
        self,
        registrations: Sequence["RudeRegistration"] = (),
        asset_edits: Sequence["RudeAssetEdit"] = (),
        source_rez: Optional[str] = None,
    ) -> Dict[str, bytes]:
        """Build changed RUDE resources for legacy registrations and assets."""
        dirty_assets = [asset for asset in asset_edits if asset.is_dirty]
        if not registrations and not dirty_assets:
            return {}
        source_rez = source_rez or self.rude_rez_path
        if not source_rez or not os.path.isfile(source_rez):
            raise FileNotFoundError("RUDE.REZ source archive was not found")

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
            npcname_bytes = reader.extract_to_bytes(npcname_vpath)
            topblurb_bytes = reader.extract_to_bytes(topblurb_vpath)
            existing_paths = {
                reader.find(path).virtual_path()
                for path in reader.list_paths()
                if reader.find(path) is not None
            }
            source_dialogues: Dict[int, Tuple[str, bytes]] = {}
            for asset in dirty_assets:
                existing = self._find_existing_vpath(
                    existing_paths, f"RUDE/NPC{asset.npc_nbr}")
                if existing is not None:
                    source_dialogues[asset.npc_nbr] = (
                        existing,
                        reader.extract_to_bytes(existing),
                    )

        catalog = rude_model.RudeMetadataCatalog.from_bytes(
            npcname_bytes, topblurb_bytes)
        npc_files: Dict[str, bytes] = {}
        for entry in registrations:
            n = entry.npc_nbr
            npc_vpath = f"RUDE/NPC{n}"
            npc_existing = self._find_existing_vpath(existing_paths, npc_vpath)
            conflicts = []
            if npc_existing is not None:
                conflicts.append(f"NPC{n} already exists")
            if catalog.has_name(n):
                conflicts.append(f"NPCNAME already has {n}")
            if catalog.has_blurb(n):
                conflicts.append(f"TOPBLURB already has {n}")
            if conflicts and not entry.force:
                raise ValueError(
                    f"RUDE registration conflict for NPC{n}: "
                    + "; ".join(conflicts))

            metadata = rude_model.RudeDialogueMetadata(
                npc_nbr=n,
                name=entry.name,
                initial_state=n,
                opening_blurb=entry.blurb,
            )
            catalog.upsert(metadata)
            npc_files[npc_existing or npc_vpath] = rude_model.make_simple_dialogue(
                metadata,
                entry.lines,
            ).to_bytes()

        registration_ids = {entry.npc_nbr for entry in registrations}
        for asset in dirty_assets:
            asset.validate_identity()
            n = asset.npc_nbr
            if n in registration_ids:
                raise ValueError(
                    f"RUDE/NPC{n} is staged both as an asset and a placement registration")
            source_dialogue = source_dialogues.get(n)
            if asset.is_new:
                already_installed = False
                if source_dialogue is not None:
                    try:
                        installed_metadata = catalog.metadata_for(n)
                    except KeyError:
                        installed_metadata = None
                    already_installed = (
                        source_dialogue[1] == asset.dialogue.to_bytes()
                        and _rude_metadata_signature(installed_metadata)
                        == _rude_metadata_signature(asset.metadata)
                    )
                if already_installed:
                    # Install Output may have copied this exact new asset into
                    # the live RUDE.REZ while the project remained open.  It is
                    # an idempotent match, not an NPC-number collision.
                    continue
                conflicts = []
                if source_dialogue is not None:
                    conflicts.append(f"NPC{n} already exists")
                if catalog.has_name(n):
                    conflicts.append(f"NPCNAME already has {n}")
                if catalog.has_blurb(n):
                    conflicts.append(f"TOPBLURB already has {n}")
                if conflicts:
                    raise ValueError(
                        f"RUDE asset conflict for NPC{n}: " + "; ".join(conflicts))
            else:
                if source_dialogue is None:
                    raise FileNotFoundError(
                        f"RUDE.REZ no longer contains RUDE/NPC{n}")
                if (
                    asset.original_dialogue_bytes is not None
                    and source_dialogue[1] != asset.original_dialogue_bytes
                ):
                    raise ValueError(
                        f"RUDE/NPC{n} changed in the source archive after it was opened")
                try:
                    source_metadata = catalog.metadata_for(n)
                except KeyError as exc:
                    raise ValueError(
                        f"RUDE metadata for NPC{n} changed after it was opened") from exc
                if (
                    _rude_metadata_signature(source_metadata)
                    != _rude_metadata_signature(asset.original_metadata)
                ):
                    raise ValueError(
                        f"RUDE metadata for NPC{n} changed in the source archive "
                        f"after it was opened")

            if asset.metadata_changed:
                catalog.upsert(asset.metadata)
            if asset.dialogue_changed:
                npc_files[
                    source_dialogue[0] if source_dialogue is not None
                    else asset.source_virtual_path
                ] = asset.dialogue.to_bytes()

        npcname_out, topblurb_out = catalog.to_bytes()
        overlay: Dict[str, bytes] = dict(npc_files)
        if npcname_out != npcname_bytes:
            overlay[npcname_vpath] = npcname_out
        if topblurb_out != topblurb_bytes:
            overlay[topblurb_vpath] = topblurb_out
        return overlay

    def execute_rude_rez(self, plan: "SavePlan") -> List[str]:
        """Write independent RUDE assets and legacy registrations to RUDE.REZ."""
        patch = plan.rude_archive_patch()
        if patch is None or not plan.has_rude_changes():
            return []
        source_rez = patch.source_archive
        output_rez = patch.output_archive
        overlay_entries = self.build_rude_overlay_entries(
            plan.rude_entries,
            asset_edits=plan.rude_assets,
            source_rez=source_rez,
        )

        self._maybe_backup_archive(source_rez)
        os.makedirs(os.path.dirname(os.path.abspath(output_rez)) or ".", exist_ok=True)

        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from core import rezmgr

        with rezmgr.RezReader(source_rez) as reader:
            existing_paths = set(reader.list_paths())

        with rezmgr.RezWriter(source_rez, output_rez) as writer:
            for vpath, data in overlay_entries.items():
                if self._find_existing_vpath(existing_paths, vpath) is not None:
                    writer.replace(vpath, data)
                else:
                    # MM9 asks RezMgr for e.g. ``RUDE\\NPC437.rude``.  The
                    # archive stores the extensionless name separately from
                    # its four-character resource type, so type 0 makes a new
                    # entry invisible to the runtime even though RezReader can
                    # still list it by virtual path.
                    writer.add(
                        vpath,
                        data,
                        restype=rezmgr._restype_for_filename("NPC.RUDE"),
                    )
                self._write_changed_entry_copy(output_rez, vpath, data)
            writer.commit()

        registration_log = [
            f"  patched RUDE/NPC{entry.npc_nbr} ({len(entry.lines) + 1} option(s))"
            for entry in plan.rude_entries
        ]
        asset_log = [f"  {asset.summary()}" for asset in plan.rude_assets]
        return [f"wrote {output_rez}"] + registration_log + asset_log

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

    def _build_npc_rude(self, entry: "RudeRegistration") -> str:
        metadata = rude_model.RudeDialogueMetadata(
            npc_nbr=entry.npc_nbr,
            name=entry.name,
            initial_state=entry.npc_nbr,
            opening_blurb=entry.blurb,
        )
        return rude_model.make_simple_dialogue(metadata, entry.lines).to_text()


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
    behavioral_prefab_imports: List[
        prefab_behavioral.BehavioralPrefabImportPlan
    ] = field(default_factory=list)
    resource_prefab_imports: List[ImportResourcePrefabOp] = field(default_factory=list)
    validation_warnings: List[str] = field(default_factory=list)
    blocking_issues: List[Dict[str, Any]] = field(default_factory=list)
    bsp_record_diff_report: Optional[Dict[str, Any]] = None
    dat_section_diff_report: Optional[Dict[str, Any]] = None
    committed_dat_bytes: Optional[bytes] = field(
        default=None,
        repr=False,
        compare=False,
    )

    def stats(self) -> Dict[str, int]:
        return {
            "objects_after": len(self.materialized.objects),
            "door_clones": len(self.door_clones),
            "door_bsp_models": sum(len(plan.submodels) for plan in self.door_clones),
            "prefab_imports": len(self.prefab_imports),
            "prefab_bsp_models": sum(len(plan.submodels) for plan in self.prefab_imports),
            "resource_prefab_imports": len(self.resource_prefab_imports),
            "behavioral_prefab_imports": len(self.behavioral_prefab_imports),
            "behavioral_prefab_objects": sum(
                len(plan.objects) for plan in self.behavioral_prefab_imports
            ),
        }

    def has_geometry_bsp_write(self) -> bool:
        return bool(
            self.door_clones
            or self.prefab_imports
        )

    def geometry_manifest_details(self) -> Dict[str, Any]:
        return {
            "door_clones": [
                {
                    "source_name": plan.source_name,
                    "new_name": plan.new_name,
                    "paired": plan.paired,
                    "object_count": len(plan.objects),
                    "models": [
                        {
                            **_manifest_model_summary(sub.new_name, prefab_import.preview_submodel(sub)),
                            "source_name": sub.source_name,
                            "raw_record_bytes": len(sub.raw_bytes),
                            "info_flags_override": sub.info_flags_override,
                        }
                        for sub in plan.submodels
                    ],
                }
                for plan in self.door_clones
            ],
            "prefab_imports": [
                {
                    "source_path": plan.source_path,
                    "new_name": plan.new_name,
                    "target_pos": list(plan.target_pos),
                    "target_yaw": plan.target_yaw,
                    "placement_anchor": plan.placement_anchor,
                    "source_pivot": list(plan.source_pivot),
                    "visible_model_names": list(plan.visible_model_names),
                    "collision_model_names": list(plan.collision_model_names),
                    "source_model_names": list(plan.source_model_names),
                    "source_model_roles": list(plan.source_model_roles),
                    "info_flags_overrides": list(plan.info_flags_overrides),
                    "import_mode": plan.import_mode,
                    "models": [
                        {
                            **_manifest_model_summary(sub.new_name, prefab_import.preview_submodel(sub)),
                            "source_name": sub.source_name,
                            "raw_record_bytes": len(sub.raw_bytes),
                            "info_flags_override": sub.info_flags_override,
                        }
                        for sub in plan.submodels
                    ],
                }
                for plan in self.prefab_imports
            ],
            "resource_prefab_imports": [
                {
                    "source_path": op.prefab_path,
                    "candidate_id": op.candidate_id,
                    "target_class": op.template.type_str,
                    "name": op.overrides.get("Name") or op.template.get("Name"),
                    "model": op.model_path,
                    "skins": list(op.skin_paths),
                    "source_fingerprint": op.source_fingerprint,
                }
                for op in self.resource_prefab_imports
            ],
            "behavioral_prefab_imports": [
                plan.manifest_dict() for plan in self.behavioral_prefab_imports
            ],
            "dat_section_diff_report": self.dat_section_diff_report or {},
            "bsp_record_diff_report": self.bsp_record_diff_report or {},
        }

    def geometry_risk_report(self) -> List[str]:
        """Human-readable geometry details for the pre-save review dialog."""
        lines: List[str] = []
        if self.door_clones:
            submodel_count = sum(len(plan.submodels) for plan in self.door_clones)
            lines.append(
                "geometry risk: door clone splices copied BSP submodel "
                f"record(s); {submodel_count} BSP model(s) will be added"
            )

        if self.prefab_imports:
            submodel_count = sum(len(plan.submodels) for plan in self.prefab_imports)
            lines.append(
                "geometry risk: prefab import splices source BSP submodel "
                f"record(s); {submodel_count} BSP model(s) will be added"
            )

        if self.dat_section_diff_report:
            lines.extend(_dat_section_diff_risk_lines(self.dat_section_diff_report))
        if self.bsp_record_diff_report:
            lines.extend(_bsp_record_diff_risk_lines(self.bsp_record_diff_report))

        return lines


def _bsp_record_diff_risk_lines(report: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for item in report.get("model_diffs", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        lines.append(
            "BSP record diff: "
            f"{name} bytes={int(item.get('byte_diff_count') or 0)}, "
            f"points={_changed_count(item.get('moved_points'))}, "
            f"planes={_changed_count(item.get('changed_planes'))}, "
            f"polygon_centers={_changed_count(item.get('changed_polygon_centers'))}, "
            f"point_normals={_changed_count(item.get('changed_point_normals'))}, "
            f"render_header_bytes={int(item.get('terrain_render_header_changed_bytes') or 0)}, "
            f"render_topology_bytes={int(item.get('terrain_render_topology_changed_bytes') or 0)}, "
            f"unknown_structural={int(item.get('unknown_structural_changed_bytes') or 0)}"
        )
        render_header_sections = item.get("terrain_render_header_changed_sections") or {}
        if isinstance(render_header_sections, dict):
            changed_headers = [
                f"{section}={count}"
                for section, count in sorted(render_header_sections.items())
                if int(count or 0) > 0
            ]
            if changed_headers:
                lines.append(
                    "BSP record render header diff: "
                    + name
                    + " "
                    + ", ".join(changed_headers)
                )
        render_topology_sections = item.get("terrain_render_topology_changed_sections") or {}
        if isinstance(render_topology_sections, dict):
            changed_topology = [
                f"{section}={count}"
                for section, count in sorted(render_topology_sections.items())
                if int(count or 0) > 0
            ]
            if changed_topology:
                lines.append(
                    "BSP record render topology diff: "
                    + name
                    + " "
                    + ", ".join(changed_topology)
                )
        section_changed = item.get("section_changed_bytes") or {}
        if isinstance(section_changed, dict):
            interesting = [
                section
                for section in (
                    "leaves",
                    "nodes",
                    "physics_block_table",
                    "trailing_payload",
                    "terrain_tail_nodes",
                    "terrain_tail_polygon_list",
                    "terrain_tail_render_payload",
                    "terrain_tail_render_header",
                    "terrain_tail_render_chunks",
                    "terrain_tail_render_compact_nodes",
                    "terrain_tail_render_bsp_header",
                    "terrain_tail_render_bsp_nodes",
                    "terrain_tail_render_bsp_polygon_list",
                    "terrain_tail_render_unknown_payload",
                )
                if int(section_changed.get(section) or 0) > 0
            ]
            interesting.extend(
                section
                for section in sorted(section_changed)
                if (
                    section.startswith("terrain_tail_render_chunk_")
                    and section.endswith("_header")
                    and int(section_changed.get(section) or 0) > 0
                )
            )
            if interesting:
                lines.append(
                    "BSP record section diff: "
                    + name
                    + " "
                    + ", ".join(f"{section}={section_changed[section]}" for section in interesting)
                )
    return lines


def _dat_section_diff_report(source_dat: bytes, output_dat: bytes) -> Dict[str, Any]:
    source_sections = _dat_top_level_sections(source_dat)
    output_sections = _dat_top_level_sections(output_dat)
    source_ranges = {name: [start, end] for name, start, end in source_sections}
    output_ranges = {name: [start, end] for name, start, end in output_sections}
    source_by_name = {name: (start, end) for name, start, end in source_sections}
    changed: Dict[str, int] = {}
    for name, start, end in source_sections:
        changed[name] = _byte_diff_count(source_dat, output_dat, start, end)
    source_offsets = _dat_header_offsets(source_dat)
    output_offsets = _dat_header_offsets(output_dat)
    changed_sections = [
        name
        for name, count in changed.items()
        if int(count) > 0
    ]
    if len(source_dat) != len(output_dat):
        changed_sections.append("dat_size")
    for key in ("object_data_pos", "render_data_pos", "world_model_table_start"):
        if source_offsets.get(key) != output_offsets.get(key):
            changed_sections.append(key)
    changed_sections = sorted(set(changed_sections))
    return {
        "source_size": len(source_dat),
        "output_size": len(output_dat),
        "source_offsets": source_offsets,
        "output_offsets": output_offsets,
        "source_ranges": source_ranges,
        "output_ranges": output_ranges,
        "section_changed_bytes": changed,
        "changed_sections": changed_sections,
        "unchanged_sections": [
            name
            for name in source_by_name
            if int(changed.get(name) or 0) == 0
        ],
    }


def _dat_top_level_sections(data: bytes) -> List[Tuple[str, int, int]]:
    offsets = _dat_header_offsets(data)
    size = len(data)
    header_end = min(size, 44)
    world_model_table_start = int(offsets.get("world_model_table_start") or header_end)
    object_data_pos = int(offsets.get("object_data_pos") or world_model_table_start)
    render_data_pos = int(offsets.get("render_data_pos") or size)
    world_model_table_start = _clamp_int(world_model_table_start, header_end, size)
    object_data_pos = _clamp_int(object_data_pos, world_model_table_start, size)
    render_data_pos = _clamp_int(render_data_pos, object_data_pos, size)
    return [
        ("header", 0, header_end),
        ("world_setup_tree", header_end, world_model_table_start),
        ("world_model_records", world_model_table_start, object_data_pos),
        ("object_data", object_data_pos, render_data_pos),
        ("render_data", render_data_pos, size),
    ]


def _dat_header_offsets(data: bytes) -> Dict[str, int]:
    result = {
        "version": 0,
        "object_data_pos": 0,
        "render_data_pos": 0,
        "world_model_table_start": 0,
    }
    if len(data) >= 12:
        version, object_data_pos, render_data_pos = struct.unpack_from("<III", data, 0)
        result.update({
            "version": int(version),
            "object_data_pos": int(object_data_pos),
            "render_data_pos": int(render_data_pos),
        })
    try:
        parsed = bsp.parse(data)
        result["world_model_table_start"] = int(parsed.world_model_table_start)
    except Exception:
        result["world_model_table_start"] = 0
    return result


def _byte_diff_count(source: bytes, changed: bytes, start: int, end: int) -> int:
    start = max(0, int(start))
    end = max(start, int(end))
    count = 0
    overlap_end = min(end, len(source), len(changed))
    for offset in range(start, overlap_end):
        if source[offset] != changed[offset]:
            count += 1
    if end > overlap_end:
        count += end - overlap_end
    return count


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(int(minimum), min(int(maximum), int(value)))


def _dat_section_diff_risk_lines(report: Dict[str, Any]) -> List[str]:
    changed = report.get("section_changed_bytes") or {}
    if not isinstance(changed, dict):
        return []
    ordered = [
        "header",
        "world_setup_tree",
        "world_model_records",
        "object_data",
        "render_data",
    ]
    parts = [
        f"{name}={int(changed.get(name) or 0)}"
        for name in ordered
        if name in changed
    ]
    source_size = int(report.get("source_size") or 0)
    output_size = int(report.get("output_size") or 0)
    if source_size != output_size:
        parts.append(f"size {source_size}->{output_size}")
    return ["DAT section diff: " + ", ".join(parts)] if parts else []


def _physics_bsp_has_blocked_record_changes(diff: Any) -> bool:
    if getattr(diff, "byte_diff_count", 0) <= 0:
        return False
    if getattr(getattr(diff, "moved_points", None), "changed_count", 0) > 0:
        return True
    if getattr(getattr(diff, "changed_planes", None), "changed_count", 0) > 0:
        return True
    if getattr(getattr(diff, "changed_polygon_centers", None), "changed_count", 0) > 0:
        return True
    if getattr(getattr(diff, "changed_point_normals", None), "changed_count", 0) > 0:
        return True
    if getattr(getattr(diff, "changed_bounds", None), "changed_count", 0) > 0:
        return True
    allowed_header_only = {"record_header"}
    section_changes = getattr(diff, "section_changed_bytes", {}) or {}
    for section, count in section_changes.items():
        value = int(count or 0)
        if value == 0:
            continue
        if section not in allowed_header_only:
            return True
    return False


def _changed_count(value: Any) -> int:
    if isinstance(value, dict):
        return int(value.get("changed_count") or 0)
    return 0


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
    additions: Dict[str, bytes] = field(default_factory=dict, repr=False)


@dataclass
class SavePlan:
    batch_id: Optional[str] = None
    dats: List[DatWrite] = field(default_factory=list)
    rude_entries: List[RudeRegistration] = field(default_factory=list)
    archive_patches: List[ArchivePatch] = field(default_factory=list)
    rude_assets: List[RudeAssetEdit] = field(default_factory=list)
    dialogue_script_assets: List[rude_script.DialogueScriptAssetEdit] = field(
        default_factory=list)
    executed_successfully: bool = field(default=False, repr=False, compare=False)

    def has_rude_changes(self) -> bool:
        return bool(self.rude_entries or self.rude_assets)

    def rude_archive_patch(self) -> Optional[ArchivePatch]:
        for patch in self.archive_patches:
            if patch.kind == "rude":
                return patch
        return None

    def behavioral_scripts_archive_patch(self) -> Optional[ArchivePatch]:
        """Compatibility alias retained for Phase-6 prefab callers."""
        return self.scripts_archive_patch()

    def scripts_archive_patch(self) -> Optional[ArchivePatch]:
        for patch in self.archive_patches:
            if patch.kind in {
                "behavioral_scripts", "dialogue_scripts", "scripts",
            }:
                return patch
        return None
