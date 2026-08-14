import os
import math
import struct
import tempfile
import unittest

from core import project as P
from core import bsp
from core import project_io
from features.prefabs.behavioral import (
    PHASE2_OBJECT_CLASSES,
    PHASE3_PASSIVE_CLASSES,
    PHASE4_SIMPLE_CLASSES,
    PHASE5_LINKED_CLASSES,
    OMIT_PORTAL_BINDING,
    analyze_prefab,
    build_behavioral_bsp_import_plan,
    build_behavioral_import_plan,
    materialize_behavioral_plan,
    materialize_object_only_plan,
    materialize_passive_plan,
    validate_plan_target_bindings,
    validate_door_import_parity,
    target_has_user_portal,
    spatial_semantics_for,
    transform_spatial_value,
)
from features.prefabs.graph import SpatialSemantics, SupportState
from mm9_patcher import mm9_patch as patcher
from tests.feature_tests.prefabs._fixtures import box_model, write_minimal_dat
from features.dat_editing import legacy_ed_writer


def _object(class_name, name, *properties):
    return patcher.WorldObject(class_name, [
        patcher.Property("Name", 0, 0, name),
        patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
        *properties,
    ])


def _write_passive_mixed_ed(path):
    brushes = [
        legacy_ed_writer.LegacyEdBrush(
            points=((0.0, 0.0, 0.0), (16.0, 0.0, 0.0), (0.0, 0.0, 16.0)),
            surfaces=(legacy_ed_writer.LegacyEdSurface(
                vertex_indices=(0, 1, 2),
                plane_normal=(0.0, 1.0, 0.0),
                plane_dist=0.0,
                texture_name=r"TEXTURES\World\Static.dtx",
            ),),
        ),
        legacy_ed_writer.LegacyEdBrush(
            points=((32.0, 0.0, 0.0), (48.0, 0.0, 0.0), (32.0, 0.0, 16.0)),
            surfaces=(legacy_ed_writer.LegacyEdSurface(
                vertex_indices=(0, 1, 2),
                plane_normal=(0.0, 1.0, 0.0),
                plane_dist=0.0,
                texture_name=r"TEXTURES\World\Owned.dtx",
            ),),
        ),
    ]
    props = (
        legacy_ed_writer.LegacyEdObjectProperty("Name", 0, 0, "OwnedPart"),
        legacy_ed_writer.LegacyEdObjectProperty("Pos", 1, 0, (32.0, 0.0, 0.0)),
        legacy_ed_writer.LegacyEdObjectProperty(
            "Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)
        ),
    )
    root = legacy_ed_writer.world_root_node([
        legacy_ed_writer.brush_node(0, "StaticBrush", node_id=2),
        legacy_ed_writer.object_node(
            "WorldObject",
            "OwnedPart",
            node_id=3,
            properties=props,
            children=(legacy_ed_writer.brush_node(1, "OwnedBrush", node_id=4),),
        ),
    ])
    data = bytearray(struct.pack("<I", 1249))
    data.extend(b"\x00" * 37)
    data.extend(struct.pack("<I", len(brushes)))
    for brush in brushes:
        data.extend(legacy_ed_writer.write_brush_record(brush))
    data.extend(legacy_ed_writer.build_node_hierarchy(root))
    data.extend(b"\x00" * 4)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _write_simple_moving_ed(
    path,
    *,
    class_name="RotatingDoor",
    stale_pivot=False,
    second_controller=False,
    portal=False,
    sound_pos=(0.0, 0.0, 0.0),
):
    def triangle(x):
        return legacy_ed_writer.LegacyEdBrush(
            points=((x, 0.0, 0.0), (x + 10.0, 0.0, 0.0), (x, 10.0, 0.0)),
            surfaces=(legacy_ed_writer.LegacyEdSurface(
                vertex_indices=(0, 1, 2),
                plane_normal=(0.0, 0.0, 1.0),
                plane_dist=0.0,
                texture_name=r"TEXTURES\World\Mover.dtx",
            ),),
        )

    brushes = [triangle(0.0)]

    def mover_props(name, x):
        result = [
            legacy_ed_writer.LegacyEdObjectProperty("Name", 0, 0, name),
            legacy_ed_writer.LegacyEdObjectProperty("Pos", 1, 0, (x + 5.0, 5.0, 0.0)),
            legacy_ed_writer.LegacyEdObjectProperty(
                "Rotation", 7, 0, (0.0, 0.25, 0.0, 0.0)
            ),
            legacy_ed_writer.LegacyEdObjectProperty("SoundPos", 1, 0, sound_pos),
        ]
        if class_name == "Door":
            result.extend([
                legacy_ed_writer.LegacyEdObjectProperty("MoveDir", 1, 1, (1.0, 0.0, 0.0)),
                legacy_ed_writer.LegacyEdObjectProperty("MoveDist", 3, 0, 32.0),
            ])
        else:
            result.extend([
                legacy_ed_writer.LegacyEdObjectProperty(
                    "RotationPoint",
                    1,
                    0,
                    (1000.0, 1000.0, 1000.0) if stale_pivot else (x, 5.0, 0.0),
                ),
                legacy_ed_writer.LegacyEdObjectProperty(
                    "RotationAngles", 1, 0, (0.0, 90.0, 0.0)
                ),
            ])
        if portal:
            result.append(legacy_ed_writer.LegacyEdObjectProperty(
                "PortalName", 0, 0, "DoorPortal"
            ))
        if second_controller:
            result.append(legacy_ed_writer.LegacyEdObjectProperty(
                "DoubleDoorName",
                0,
                0,
                "Mover2" if name == "Mover1" else "Mover1",
            ))
        return tuple(result)

    children = [legacy_ed_writer.object_node(
        class_name,
        "Mover1",
        node_id=3,
        properties=mover_props("Mover1", 0.0),
        children=(legacy_ed_writer.brush_node(0, "MoverBrush1", node_id=4),),
    )]
    if second_controller:
        brushes.append(triangle(20.0))
        children.append(legacy_ed_writer.object_node(
            class_name,
            "Mover2",
            node_id=5,
            properties=mover_props("Mover2", 20.0),
            children=(legacy_ed_writer.brush_node(1, "MoverBrush2", node_id=6),),
        ))
    if portal:
        portal_index = len(brushes)
        brushes.append(triangle(40.0))
        portal_props = []
        for prop in legacy_ed_writer.full_world_brush_node_properties("DoorPortal"):
            if prop.name == "Portal":
                prop = legacy_ed_writer.LegacyEdObjectProperty("Portal", 5, prop.flags, True)
            portal_props.append(prop)
        children.append(legacy_ed_writer.brush_node(
            portal_index,
            "DoorPortal",
            node_id=7,
            properties=tuple(portal_props),
        ))
    root = legacy_ed_writer.world_root_node(children)
    data = bytearray(struct.pack("<I", 1249))
    data.extend(b"\x00" * 37)
    data.extend(struct.pack("<I", len(brushes)))
    for brush in brushes:
        data.extend(legacy_ed_writer.write_brush_record(brush))
    data.extend(legacy_ed_writer.build_node_hierarchy(root))
    data.extend(b"\x00" * 4)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


class BehavioralPrefabAnalysisTests(unittest.TestCase):
    def _write_linked_door(self, root, external=False):
        path = os.path.join(root, "LinkedDoor.dat")
        target = "LevelTrigger" if external else "Trigger1"
        objects = [
            _object(
                "RotatingDoor",
                "Door1",
                patcher.Property("OpenTriggerTarget0", 0, 0, target),
                patcher.Property("OpenSound", 0, 0, r"Sounds\Doors\Open.wav"),
                patcher.Property("ScriptName", 0, 0, "DoorLogic.scr"),
            ),
            _object("Trigger", "Trigger1"),
        ]
        write_minimal_dat(
            path,
            [
                box_model("Door1", (-8.0, 0.0, -2.0), (8.0, 32.0, 2.0)),
                box_model("PhysicsBSP", (-8.0, 0.0, -2.0), (8.0, 32.0, 2.0)),
            ],
            objects,
        )
        return path

    def test_analysis_is_fail_closed_until_class_capabilities_are_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_linked_door(tmp)
            analysis = analyze_prefab(path)

        self.assertEqual(analysis.static_state, SupportState.STATIC_READY)
        self.assertEqual(analysis.behavioral_state, SupportState.BLOCKED)
        self.assertIn(
            "behavioral_class_policy_pending",
            {item.code for item in analysis.diagnostics},
        )

    def test_ready_plan_pairs_controller_bsp_and_rewrites_internal_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_linked_door(tmp)
            analysis = analyze_prefab(
                path,
                supported_classes={"RotatingDoor", "Trigger"},
                allow_scripts=True,
            )
            first = build_behavioral_import_plan(
                analysis,
                root_name="ImportedDoor",
                existing_names={"ImportedDoor_Door1"},
            )
            second = build_behavioral_import_plan(
                analysis,
                root_name="ImportedDoor",
                existing_names={"ImportedDoor_Door1"},
            )

        self.assertEqual(analysis.behavioral_state, SupportState.BEHAVIORAL_READY)
        self.assertEqual(first, second)
        self.assertEqual(first.support_state, SupportState.BEHAVIORAL_READY)
        self.assertEqual(first.objects[0].target_name, "ImportedDoor_Door1_2")
        owned = next(item for item in first.brushes if item.source_name == "Door1")
        self.assertEqual(owned.owner_target_name, "ImportedDoor_Door1_2")
        reference = next(
            item for item in first.references
            if item.property_name == "OpenTriggerTarget0"
        )
        self.assertEqual(reference.target_value, "ImportedDoor_Trigger1")

    def test_external_bindings_and_missing_resources_require_explicit_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_linked_door(tmp, external=True)
            analysis = analyze_prefab(
                path,
                supported_classes={"RotatingDoor", "Trigger"},
                resource_exists=lambda _kind, _path: False,
                allow_scripts=True,
            )
            unresolved = build_behavioral_import_plan(
                analysis,
                root_name="ImportedDoor",
            )
            resolved = build_behavioral_import_plan(
                analysis,
                root_name="ImportedDoor",
                external_bindings={"LevelTrigger": "ExistingTrigger5"},
                dependency_decisions={r"Sounds\Doors\Open.wav": "stage"},
            )

        self.assertEqual(analysis.behavioral_state, SupportState.ACTION_REQUIRED)
        self.assertEqual(unresolved.support_state, SupportState.ACTION_REQUIRED)
        self.assertEqual(resolved.support_state, SupportState.ACTION_REQUIRED)
        resolved = build_behavioral_import_plan(
            analysis,
            root_name="ImportedDoor",
            external_bindings={"LevelTrigger": "ExistingTrigger5"},
            dependency_decisions={
                r"Sounds\Doors\Open.wav": "stage",
                r"SCRIPTS\DoorLogic.scr": "provide",
            },
        )
        self.assertEqual(resolved.support_state, SupportState.BEHAVIORAL_READY)
        self.assertEqual(resolved.references[0].target_value, "ExistingTrigger5")
        self.assertIn(
            r"SCRIPTS\DoorLogic.scr",
            {item.path for item in resolved.dependencies},
        )

    def test_spatial_registry_distinguishes_points_directions_extents_and_behavior(self):
        self.assertEqual(
            spatial_semantics_for("Door", "RotationPoint"),
            SpatialSemantics.WORLD_POINT,
        )
        self.assertEqual(
            spatial_semantics_for("Door", "MoveDir"),
            SpatialSemantics.DIRECTION,
        )
        self.assertEqual(
            spatial_semantics_for("Trigger", "TriggerDims"),
            SpatialSemantics.EXTENT,
        )
        self.assertEqual(
            spatial_semantics_for("RotatingDoor", "RotationAngles"),
            SpatialSemantics.BEHAVIOR_LOCAL,
        )
        point = transform_spatial_value(
            SpatialSemantics.WORLD_POINT,
            (1.0, 2.0, 0.0),
            target_pos=(10.0, 20.0, 30.0),
            target_yaw=math.pi * 0.5,
        )
        self.assertAlmostEqual(point[0], 10.0, places=5)
        self.assertAlmostEqual(point[1], 22.0, places=5)
        self.assertAlmostEqual(point[2], 31.0, places=5)


class BehavioralPrefabProjectOperationTests(unittest.TestCase):
    def test_operation_round_trips_all_phase_one_decisions(self):
        op = P.ImportBehavioralPrefabOp(
            prefab_path=r"C:\PreFabs\Doors\A1_Door.ed",
            root_name="ImportedDoor",
            target_pos=(1.0, 2.0, 3.0),
            target_yaw=45.0,
            placement_anchor="controller_pivot",
            source_fingerprint="abc123",
            external_bindings={"Target1": "LevelTarget7"},
            dependency_decisions={r"Sounds\Door.wav": "stage"},
            enabled_capabilities=("RotatingDoor",),
        )

        restored = project_io.dict_to_op(project_io.op_to_dict(op))

        self.assertEqual(restored, op)
        self.assertIn("promoted class capabilities", restored.summary())

    def test_phase_seven_loads_operations_written_with_the_retired_experimental_flag(self):
        op = P.ImportBehavioralPrefabOp(
            prefab_path=r"C:\PreFabs\Doors\A1_Door.ed",
            root_name="ImportedDoor",
        )
        payload = project_io.op_to_dict(op)
        payload["experimental_enabled"] = False

        restored = project_io.dict_to_op(payload)

        self.assertEqual(restored, op)
        self.assertFalse(hasattr(restored, "experimental_enabled"))

    def test_behavioral_assembly_removal_round_trips_as_an_undoable_tombstone(self):
        op = P.ImportBehavioralPrefabOp(
            prefab_path=r"C:\PreFabs\Doors\A1_Door.ed",
            root_name="ImportedDoor",
        )
        removal = P.RemoveBehavioralPrefabOp(op.operation_id, op.root_name)
        level = P.LevelEdit(path="target", ops=[op, removal])

        self.assertEqual(level.effective_ops(), [])
        self.assertIs(level.undo_last_op(), removal)
        self.assertEqual(level.effective_ops(), [op])
        self.assertIs(level.redo_last_op(), removal)
        self.assertEqual(level.effective_ops(), [])
        self.assertEqual(
            project_io.dict_to_op(project_io.op_to_dict(removal)),
            removal,
        )


class ObjectOnlyPrefabMaterializationTests(unittest.TestCase):
    @staticmethod
    def _template(class_name, *, filename="", skin=""):
        props = [
            patcher.Property("Name", 0, 0, ""),
            patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
            patcher.Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
            patcher.Property("Filename", 0, 0, filename),
            patcher.Property("Skin", 0, 0, skin),
            patcher.Property("Scale", 3, 0, 1.0),
            patcher.Property("NeedsTick", 5, 0, True),
        ]
        return patcher.WorldObject(class_name, props)

    def _write_prop_prefab(self, root):
        path = os.path.join(root, "HangingSkeleton.dat")
        write_minimal_dat(path, [], [
            _object(
                "Prop",
                "Skeleton1",
                patcher.Property("Rotation", 7, 0, (0.0, 0.25, 0.0, 0.0)),
                patcher.Property("Filename", 0, 0, r"models\props\SkeletonCage.abc"),
                patcher.Property("Skin", 0, 0, r"skins\props\SkeletonCage.dtx"),
                patcher.Property("Scale", 3, 0, 1.5),
            ),
        ])
        return path

    def test_catalog_template_overlay_retains_defaults_and_transforms_pose(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_prop_prefab(tmp)
            analysis = analyze_prefab(path, supported_classes=PHASE2_OBJECT_CLASSES)
            plan = build_behavioral_import_plan(
                analysis,
                root_name="ImportedSkeleton",
                target_pos=(10.0, 20.0, 30.0),
                target_yaw=0.5,
            )
            created = materialize_object_only_plan(
                analysis,
                plan,
                class_templates={"Prop": self._template("Prop")},
                placement_anchor="original_origin",
            )

        self.assertEqual(len(created), 1)
        obj = created[0]
        self.assertEqual(obj.get("Name"), "ImportedSkeleton")
        self.assertEqual(tuple(obj.get("Pos")), (10.0, 20.0, 30.0))
        self.assertAlmostEqual(obj.get("Rotation")[1], 0.75)
        self.assertEqual(obj.get("Filename"), r"models\props\SkeletonCage.abc")
        self.assertEqual(obj.get("Skin"), r"skins\props\SkeletonCage.dtx")
        self.assertEqual(obj.get("Scale"), 1.5)
        self.assertTrue(obj.get("NeedsTick"))

    def test_multi_object_assembly_preserves_relative_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Torch.dat")
            write_minimal_dat(path, [], [
                _object("Light", "Light1"),
                patcher.WorldObject("Light", [
                    patcher.Property("Name", 0, 0, "Light2"),
                    patcher.Property("Pos", 1, 0, (10.0, 0.0, 0.0)),
                    patcher.Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
                ]),
            ])
            template = patcher.WorldObject("Light", [
                patcher.Property("Name", 0, 0, ""),
                patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
                patcher.Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
                patcher.Property("LightRadius", 3, 0, 100.0),
            ])
            analysis = analyze_prefab(path, supported_classes=PHASE2_OBJECT_CLASSES)
            plan = build_behavioral_import_plan(
                analysis,
                root_name="ImportedTorch",
                target_pos=(100.0, 5.0, 200.0),
                target_yaw=math.pi * 0.5,
            )
            created = materialize_object_only_plan(
                analysis,
                plan,
                class_templates={"Light": template},
                placement_anchor="original_origin",
            )

        self.assertEqual(len(created), 2)
        self.assertEqual(tuple(round(v, 5) for v in created[0].get("Pos")), (100.0, 5.0, 200.0))
        self.assertEqual(tuple(round(v, 5) for v in created[1].get("Pos")), (100.0, 5.0, 210.0))
        self.assertEqual(created[0].get("LightRadius"), 100.0)

    def test_scripted_object_only_prefab_stays_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Shopkeeper.dat")
            write_minimal_dat(path, [], [
                _object(
                    "Prop",
                    "Shopkeeper1",
                    patcher.Property("ScriptName", 0, 0, "PropAnim.scr"),
                    patcher.Property("ScriptParams", 0, 0, "Innkeeper2"),
                ),
            ])
            analysis = analyze_prefab(path, supported_classes=PHASE2_OBJECT_CLASSES)

        self.assertEqual(analysis.behavioral_state, SupportState.BLOCKED)
        self.assertIn(
            "behavioral_script_policy_pending",
            {item.code for item in analysis.diagnostics},
        )

    def test_atomic_operation_moves_rotates_overrides_serializes_and_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = self._write_prop_prefab(tmp)
            target_path = os.path.join(tmp, "Target.dat")
            write_minimal_dat(target_path, [], [
                _object("WorldObject", "Existing"),
            ])
            target_world = patcher.World.load(target_path)
            analysis = analyze_prefab(
                source_path,
                supported_classes=PHASE2_OBJECT_CLASSES,
            )
            op = P.ImportBehavioralPrefabOp(
                prefab_path=source_path,
                root_name="ImportedSkeleton",
                target_pos=(10.0, 20.0, 30.0),
                source_fingerprint=analysis.graph.source_fingerprint,
                enabled_capabilities=tuple(sorted(PHASE2_OBJECT_CLASSES)),
                class_templates={"Prop": self._template("Prop")},
            )
            level = P.LevelEdit(path="target", world=target_world, ops=[op])

            first = level.materialize()
            self.assertEqual(len(first.objects), 2)
            self.assertEqual(level.pending_add_offset_for_materialized(1), (op, 0))
            op.retarget_from_object(level.objects_before_op(op), 0, (40.0, 50.0, 60.0))
            op.rerotate_from_object(
                level.objects_before_op(op),
                0,
                (0.0, 1.25, 0.0, 0.0),
            )
            op.set_object_overrides(0, {"Scale": 2.0})
            moved = level.materialize()
            imported = moved.objects[-1]
            self.assertEqual(tuple(imported.get("Pos")), (40.0, 50.0, 60.0))
            self.assertAlmostEqual(imported.get("Rotation")[1], 1.25)
            self.assertEqual(imported.get("Scale"), 2.0)
            plans = level.behavioral_prefab_import_plans()
            write = P.DatWrite(
                source_path="target",
                output_path="output",
                ops_summary=[op.summary()],
                materialized=moved,
                behavioral_prefab_imports=plans,
            )
            self.assertEqual(write.stats()["behavioral_prefab_imports"], 1)
            self.assertEqual(write.stats()["behavioral_prefab_objects"], 1)
            self.assertEqual(
                write.geometry_manifest_details()["behavioral_prefab_imports"][0]["root_name"],
                "ImportedSkeleton",
            )
            self.assertIs(level.undo_last_op(), op)
            self.assertEqual(len(level.materialize().objects), 1)
            self.assertIs(level.redo_last_op(), op)
            self.assertEqual(len(level.materialize().objects), 2)

            restored = project_io.dict_to_op(project_io.op_to_dict(op))
            self.assertEqual(project_io.op_to_dict(restored), project_io.op_to_dict(op))
            reopened_level = P.LevelEdit(path="target", world=target_world, ops=[restored])
            reopened = reopened_level.materialize()
            self.assertEqual(reopened.objects[-1].get("Filename"), imported.get("Filename"))
            output = os.path.join(tmp, "Saved.dat")
            reopened.save(output)
            saved = patcher.World.load(output)
            self.assertEqual(saved.objects[-1].get("Name"), "ImportedSkeleton")
            self.assertEqual(saved.objects[-1].get("Scale"), 2.0)


class SimpleOwnedMovingPrefabTests(unittest.TestCase):
    @staticmethod
    def _worldobject_template():
        return patcher.WorldObject("WorldObject", [
            patcher.Property("Name", 0, 0, ""),
            patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
            patcher.Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
            patcher.Property("MoveToFloor", 5, 0, False),
        ])

    @staticmethod
    def _mover_template(class_name):
        props = [
            patcher.Property("Name", 0, 0, ""),
            patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
            patcher.Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
            patcher.Property("SoundPos", 1, 0, (0.0, 0.0, 0.0)),
            patcher.Property("DoubleDoorName", 0, 0, ""),
            patcher.Property("PortalName", 0, 0, ""),
        ]
        if class_name == "Door":
            props.extend([
                patcher.Property("MoveDir", 1, 1, (0.0, 0.0, 0.0)),
                patcher.Property("MoveDist", 3, 0, 0.0),
            ])
        else:
            props.extend([
                patcher.Property("RotationPoint", 1, 0, (0.0, 0.0, 0.0)),
                patcher.Property("RotationAngles", 1, 0, (0.0, 0.0, 0.0)),
            ])
        return patcher.WorldObject(class_name, props)

    def test_rotating_controller_owned_ed_bsp_previews_but_runtime_save_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_simple_moving_ed(
                os.path.join(tmp, "RotatingPanel.ed"),
                stale_pivot=True,
            )
            target = os.path.join(tmp, "Target.dat")
            write_minimal_dat(
                target,
                [box_model("PhysicsBSP", (-8.0, -8.0, -8.0), (8.0, 8.0, 8.0))],
                [_object("WorldObject", "Existing")],
            )
            with open(target, "rb") as handle:
                target_data = handle.read()
            analysis = analyze_prefab(source, supported_classes=PHASE4_SIMPLE_CLASSES)
            self.assertEqual(analysis.behavioral_state, SupportState.BEHAVIORAL_READY)
            self.assertIn(
                "behavioral_legacy_rotation_point_rebased",
                {item.code for item in analysis.diagnostics},
            )
            op = P.ImportBehavioralPrefabOp(
                prefab_path=source,
                root_name="ImportedPanel",
                target_pos=(100.0, 20.0, 300.0),
                target_yaw=math.pi * 0.5,
                placement_anchor="original_origin",
                source_fingerprint=analysis.graph.source_fingerprint,
                enabled_capabilities=tuple(sorted(PHASE4_SIMPLE_CLASSES)),
                class_templates={
                    "RotatingDoor": self._mover_template("RotatingDoor"),
                    "WorldObject": self._worldobject_template(),
                },
            )
            level = P.LevelEdit(
                path=target,
                world=patcher.World.load(target),
                bsp=bsp.parse(target_data),
                ops=[op],
            )
            level._raw_bytes = target_data
            materialized = level.materialize()
            imported = materialized.objects[-1]
            self.assertEqual(tuple(round(v, 5) for v in imported.get("Pos")), (100.0, 25.0, 305.0))
            self.assertEqual(
                tuple(round(v, 5) for v in imported.get("RotationPoint")),
                (100.0, 25.0, 305.0),
            )
            self.assertEqual(imported.get("SoundPos"), (0.0, 0.0, 0.0))
            self.assertEqual(imported.get("RotationAngles"), (0.0, 90.0, 0.0))
            self.assertAlmostEqual(imported.get("Rotation")[1], 0.25 + math.pi * 0.5)
            preview = level.preview_bsp()
            self.assertIsNotNone(preview.model_by_name("ImportedPanel"))

            write = P.DatWrite(
                source_path=target,
                output_path=os.path.join(tmp, "output.dat"),
                ops_summary=[op.summary()],
                materialized=materialized,
                level_edit=level,
                prefab_imports=level.prefab_import_plans(),
                behavioral_prefab_imports=level.behavioral_prefab_import_plans(),
            )
            with self.assertRaisesRegex(ValueError, "not a complete MM9 runtime BSP"):
                P.Project()._dat_write_to_bytes(write)

    def test_linear_move_direction_rotates_with_placement_yaw(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_simple_moving_ed(
                os.path.join(tmp, "SlidingPanel.ed"),
                class_name="Door",
            )
            target = os.path.join(tmp, "Target.dat")
            write_minimal_dat(
                target,
                [box_model("PhysicsBSP", (-8.0, -8.0, -8.0), (8.0, 8.0, 8.0))],
                [_object("WorldObject", "Existing")],
            )
            analysis = analyze_prefab(source, supported_classes=PHASE4_SIMPLE_CLASSES)
            plan = build_behavioral_import_plan(
                analysis,
                root_name="ImportedSlider",
                target_yaw=math.pi * 0.5,
            )
            created = materialize_behavioral_plan(
                analysis,
                plan,
                class_templates={"Door": self._mover_template("Door")},
                placement_anchor="original_origin",
            )
            with open(target, "rb") as handle:
                target_bsp = bsp.parse(handle.read())
            bsp_plan = build_behavioral_bsp_import_plan(
                target_bsp,
                analysis,
                plan,
                placement_anchor="original_origin",
            )

        direction = created[0].get("MoveDir")
        self.assertAlmostEqual(direction[0], 0.0, places=5)
        self.assertAlmostEqual(direction[1], 0.0, places=5)
        self.assertAlmostEqual(direction[2], 1.0, places=5)
        self.assertEqual(created[0].get("SoundPos"), (0.0, 0.0, 0.0))
        self.assertEqual(len(bsp_plan.submodels), 1)

    def test_plausible_pivot_and_explicit_sound_position_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_simple_moving_ed(
                os.path.join(tmp, "HingedPanel.ed"),
                sound_pos=(2.0, 5.0, 0.0),
            )
            analysis = analyze_prefab(source, supported_classes=PHASE4_SIMPLE_CLASSES)
            plan = build_behavioral_import_plan(
                analysis,
                root_name="ImportedHinge",
                target_pos=(100.0, 20.0, 300.0),
                target_yaw=math.pi * 0.5,
            )
            created = materialize_behavioral_plan(
                analysis,
                plan,
                class_templates={
                    "RotatingDoor": self._mover_template("RotatingDoor")
                },
                placement_anchor="original_origin",
            )

        self.assertNotIn(
            "behavioral_legacy_rotation_point_rebased",
            {item.code for item in analysis.diagnostics},
        )
        self.assertEqual(
            tuple(round(v, 5) for v in created[0].get("RotationPoint")),
            (100.0, 25.0, 300.0),
        )
        self.assertEqual(
            tuple(round(v, 5) for v in created[0].get("SoundPos")),
            (100.0, 25.0, 302.0),
        )

    def test_compound_and_portal_movers_remain_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            compound = _write_simple_moving_ed(
                os.path.join(tmp, "DoubleDoor.ed"),
                second_controller=True,
            )
            portal = _write_simple_moving_ed(
                os.path.join(tmp, "PortalDoor.ed"),
                portal=True,
            )
            compound_analysis = analyze_prefab(
                compound,
                supported_classes=PHASE4_SIMPLE_CLASSES,
            )
            portal_analysis = analyze_prefab(
                portal,
                supported_classes=PHASE4_SIMPLE_CLASSES,
            )

        self.assertEqual(compound_analysis.behavioral_state, SupportState.BLOCKED)
        self.assertIn(
            "behavioral_compound_moving_graph_pending",
            {item.code for item in compound_analysis.diagnostics},
        )
        self.assertEqual(portal_analysis.behavioral_state, SupportState.BLOCKED)
        self.assertIn("portal", {brush.role for brush in portal_analysis.graph.brushes})
        self.assertIn(
            "behavioral_moving_portal_pending",
            {item.code for item in portal_analysis.diagnostics},
        )


class LinkedBehavioralGraphTests(unittest.TestCase):
    @staticmethod
    def _target(tmp):
        path = os.path.join(tmp, "Target.dat")
        write_minimal_dat(
            path,
            [box_model("PhysicsBSP", (-8.0, -8.0, -8.0), (8.0, 8.0, 8.0))],
            [_object("WorldObject", "Existing")],
        )
        with open(path, "rb") as handle:
            data = handle.read()
        return path, data, bsp.parse(data)

    def test_double_door_links_are_namespaced_and_duplicate_imports_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_simple_moving_ed(
                os.path.join(tmp, "DoubleDoor.ed"),
                second_controller=True,
            )
            target, target_data, target_bsp = self._target(tmp)
            analysis = analyze_prefab(
                source,
                supported_classes=PHASE5_LINKED_CLASSES,
            )
            first = build_behavioral_import_plan(
                analysis,
                root_name="ImportedDoubleDoor",
                existing_names={"Existing"},
            )
            second = build_behavioral_import_plan(
                analysis,
                root_name="ImportedDoubleDoor",
                existing_names={
                    "Existing",
                    *(item.target_name for item in first.objects),
                },
            )
            template = SimpleOwnedMovingPrefabTests._mover_template("RotatingDoor")
            created = materialize_behavioral_plan(
                analysis,
                first,
                class_templates={"RotatingDoor": template},
            )
            first_bsp = build_behavioral_bsp_import_plan(
                target_bsp,
                analysis,
                first,
                placement_anchor="original_origin",
            )
            first_op = P.ImportBehavioralPrefabOp(
                prefab_path=source,
                root_name="ImportedDoubleDoor",
                source_fingerprint=analysis.graph.source_fingerprint,
                enabled_capabilities=tuple(sorted(PHASE5_LINKED_CLASSES)),
                class_templates={"RotatingDoor": template},
                planned_object_names={
                    str(item.source_index): item.target_name for item in first.objects
                },
            )
            second_op = P.ImportBehavioralPrefabOp(
                prefab_path=source,
                root_name="ImportedDoubleDoor",
                source_fingerprint=analysis.graph.source_fingerprint,
                enabled_capabilities=tuple(sorted(PHASE5_LINKED_CLASSES)),
                class_templates={"RotatingDoor": template},
                planned_object_names={
                    str(item.source_index): item.target_name for item in second.objects
                },
            )
            level = P.LevelEdit(
                path=target,
                world=patcher.World.load(target),
                bsp=target_bsp,
                ops=[first_op, second_op],
            )
            level._raw_bytes = target_data
            twice = level.materialize()
            level.undo_last_op()
            once = level.materialize()
            level.redo_last_op()
            restored = level.materialize()
            removal = P.RemoveBehavioralPrefabOp(
                first_op.operation_id,
                first_op.root_name,
            )
            level.append_op(removal)
            after_delete = level.materialize()
            after_delete_bsp = level.preview_bsp()
            level.undo_last_op()
            after_undo_delete = level.materialize()
            level.redo_last_op()
            after_redo_delete = level.materialize()

        self.assertEqual(analysis.behavioral_state, SupportState.BEHAVIORAL_READY)
        self.assertEqual(len(created), 2)
        self.assertEqual(len(first_bsp.submodels), 2)
        self.assertEqual(
            validate_door_import_parity(analysis, first, created, first_bsp),
            (),
        )
        first_names = {item.target_name for item in first.objects}
        second_names = {item.target_name for item in second.objects}
        self.assertTrue(first_names.isdisjoint(second_names))
        self.assertEqual(
            {item.target_value for item in first.references},
            first_names,
        )
        self.assertEqual(
            {item.target_value for item in second.references},
            second_names,
        )
        self.assertEqual(len(twice.objects), 5)
        self.assertEqual(len(once.objects), 3)
        self.assertEqual(len(restored.objects), 5)
        self.assertEqual(
            {obj.get("Name") for obj in after_delete.objects},
            {"Existing", *second_names},
        )
        self.assertTrue(all(after_delete_bsp.model_by_name(name) is None for name in first_names))
        self.assertTrue(all(after_delete_bsp.model_by_name(name) is not None for name in second_names))
        self.assertEqual(len(after_undo_delete.objects), 5)
        self.assertEqual(len(after_redo_delete.objects), 3)

    def test_portal_helper_requires_explicit_binding_and_is_not_compiled_as_bsp(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_simple_moving_ed(
                os.path.join(tmp, "PortalDoor.ed"),
                portal=True,
            )
            _target_path, _target_data, target_bsp = self._target(tmp)
            analysis = analyze_prefab(
                source,
                supported_classes=PHASE5_LINKED_CLASSES,
            )
            unresolved = build_behavioral_import_plan(
                analysis,
                root_name="ImportedPortalDoor",
            )
            plan = build_behavioral_import_plan(
                analysis,
                root_name="ImportedPortalDoor",
                external_bindings={"DoorPortal": OMIT_PORTAL_BINDING},
            )
            created = materialize_behavioral_plan(
                analysis,
                plan,
                class_templates={
                    "RotatingDoor": SimpleOwnedMovingPrefabTests._mover_template(
                        "RotatingDoor"
                    ),
                },
            )
            bsp_plan = build_behavioral_bsp_import_plan(
                target_bsp,
                analysis,
                plan,
                placement_anchor="original_origin",
            )

        self.assertEqual(analysis.behavioral_state, SupportState.ACTION_REQUIRED)
        self.assertEqual(unresolved.support_state, SupportState.ACTION_REQUIRED)
        self.assertEqual(plan.support_state, SupportState.BEHAVIORAL_READY)
        self.assertEqual(plan.references[0].binding_kind, "omitted_portal")
        self.assertEqual(created[0].get("PortalName"), "")
        self.assertIn("portal", {item.role for item in plan.brushes})
        self.assertNotIn("portal", set(bsp_plan.source_model_roles))
        self.assertEqual(len(bsp_plan.submodels), 1)

    def test_external_object_bindings_must_exist_in_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = BehavioralPrefabAnalysisTests()._write_linked_door(tmp, external=True)
            analysis = analyze_prefab(
                source,
                supported_classes={"RotatingDoor", "Trigger"},
                allow_scripts=True,
            )
            plan = build_behavioral_import_plan(
                analysis,
                root_name="ImportedDoor",
                external_bindings={"LevelTrigger": "MissingTarget"},
                dependency_decisions={
                    r"Sounds\Doors\Open.wav": "stage",
                    r"SCRIPTS\DoorLogic.scr": "provide",
                },
            )

        issues = validate_plan_target_bindings(
            plan,
            target_object_names={"ExistingTarget"},
        )
        self.assertEqual(len(issues), 1)
        self.assertIn("does not name an object", issues[0])
        self.assertFalse(validate_plan_target_bindings(
            plan,
            target_object_names={"MissingTarget"},
        ))

        materialized_world = patcher.World(
            header=patcher.Header(66, 0, 0, (0,) * 8),
            pre_objects=b"",
            objects=[_object("WorldObject", "ExistingTarget")],
            render_data=b"",
        )
        level = P.LevelEdit(
            path="target",
            world=materialized_world,
            bsp=bsp.BspWorld(66, ""),
        )
        level._raw_bytes = b"target"
        blocking = level.behavioral_prefab_blocking_issues(
            [plan],
            materialized_world,
        )
        self.assertEqual(len(blocking), 1)
        self.assertEqual(blocking[0]["code"], "behavioral_prefab_dangling_binding")

    def test_target_portal_probe_requires_a_positive_count_and_exact_name(self):
        world_name = b"VisBSP"
        portal_name = b"DoorPortal"
        raw = bytearray(b"\0" * 8)
        raw.extend(struct.pack("<H", len(world_name)))
        raw.extend(world_name)
        raw.extend(struct.pack("<IIII", 0, 0, 0, 1))
        raw.extend(b"\0" * 32)
        raw.extend(struct.pack("<H", len(portal_name)))
        raw.extend(portal_name)
        model = bsp.WorldModelMesh(
            "VisBSP",
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            raw_start=0,
            raw_end=len(raw),
            world_bsp_start=0,
        )
        target = bsp.BspWorld(66, "", world_models=[model])

        self.assertTrue(target_has_user_portal(target, bytes(raw), "doorportal"))
        self.assertFalse(target_has_user_portal(target, bytes(raw), "OtherPortal"))
        struct.pack_into("<I", raw, 8 + 2 + len(world_name) + 12, 0)
        self.assertFalse(target_has_user_portal(target, bytes(raw), "DoorPortal"))


class PassiveMixedPrefabTests(unittest.TestCase):
    @staticmethod
    def _worldobject_template():
        return patcher.WorldObject("WorldObject", [
            patcher.Property("Name", 0, 0, ""),
            patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
            patcher.Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
            patcher.Property("MoveToFloor", 5, 0, False),
            patcher.Property("Visible", 5, 0, True),
        ])

    def test_hierarchy_assigns_owned_and_unowned_brushes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_passive_mixed_ed(os.path.join(tmp, "Mixed.ed"))
            analysis = analyze_prefab(path, supported_classes=PHASE3_PASSIVE_CLASSES)
            plan = build_behavioral_import_plan(analysis, root_name="ImportedMixed")

        self.assertEqual(analysis.behavioral_state, SupportState.BEHAVIORAL_READY)
        self.assertEqual(
            [(item.ownership, item.owner_object_index) for item in analysis.graph.brushes],
            [("unowned", None), ("owned", 0)],
        )
        self.assertEqual(plan.brushes[1].owner_target_name, "ImportedMixed")
        synthetic = [item for item in plan.objects if item.synthetic]
        self.assertEqual(len(synthetic), 1)
        self.assertEqual(plan.brushes[0].target_name, synthetic[0].target_name)

    def test_compiled_sky_model_uses_exact_name_controller_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Sky.dat")
            write_minimal_dat(path, [
                box_model("Skybox", (-16.0, -16.0, -16.0), (16.0, 16.0, 16.0)),
            ], [
                _object(
                    "DemoSkyWorldModel",
                    "Skybox",
                    patcher.Property("SkyDims", 1, 0, (16.0, 16.0, 16.0)),
                ),
            ])
            analysis = analyze_prefab(path, supported_classes=PHASE3_PASSIVE_CLASSES)
            plan = build_behavioral_import_plan(analysis, root_name="ImportedDesert")

        self.assertEqual(analysis.behavioral_state, SupportState.BEHAVIORAL_READY)
        self.assertEqual(analysis.graph.brushes[0].ownership, "owned")
        self.assertEqual(analysis.graph.brushes[0].role, "skybox")
        self.assertEqual(plan.objects[0].target_name, "ImportedDesert_Skybox")
        self.assertEqual(plan.brushes[0].target_name, plan.objects[0].target_name)

    def test_atomic_passive_ed_operation_previews_but_runtime_save_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_passive_mixed_ed(os.path.join(tmp, "Mixed.ed"))
            target = os.path.join(tmp, "Target.dat")
            write_minimal_dat(
                target,
                [box_model("PhysicsBSP", (-8.0, -8.0, -8.0), (8.0, 8.0, 8.0))],
                [_object("WorldObject", "Existing")],
            )
            with open(target, "rb") as handle:
                target_data = handle.read()
            target_world = patcher.World.load(target)
            analysis = analyze_prefab(source, supported_classes=PHASE3_PASSIVE_CLASSES)
            op = P.ImportBehavioralPrefabOp(
                prefab_path=source,
                root_name="ImportedMixed",
                target_pos=(100.0, 20.0, 300.0),
                source_fingerprint=analysis.graph.source_fingerprint,
                enabled_capabilities=tuple(sorted(PHASE3_PASSIVE_CLASSES)),
                class_templates={"WorldObject": self._worldobject_template()},
            )
            level = P.LevelEdit(
                path=target,
                world=target_world,
                bsp=bsp.parse(target_data),
                ops=[op],
            )
            level._raw_bytes = target_data

            materialized = level.materialize()
            preview = level.preview_bsp()
            bsp_plans = level.prefab_import_plans()
            behavior_plans = level.behavioral_prefab_import_plans()
            write = P.DatWrite(
                source_path=target,
                output_path=os.path.join(tmp, "output.dat"),
                ops_summary=[op.summary()],
                materialized=materialized,
                level_edit=level,
                prefab_imports=bsp_plans,
                behavioral_prefab_imports=behavior_plans,
            )
            with self.assertRaisesRegex(ValueError, "not a complete MM9 runtime BSP"):
                P.Project()._dat_write_to_bytes(write)

        names = [obj.get("Name") for obj in materialized.objects]
        self.assertIn("ImportedMixed", names)
        synthetic_name = next(item.target_name for item in behavior_plans[0].objects if item.synthetic)
        self.assertIn(synthetic_name, names)
        self.assertIsNotNone(preview.model_by_name("ImportedMixed"))
        self.assertIsNotNone(preview.model_by_name(synthetic_name))


if __name__ == "__main__":
    unittest.main()
