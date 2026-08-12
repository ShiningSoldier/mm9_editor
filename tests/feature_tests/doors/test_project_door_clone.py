import os
import sys
import math
import unittest


from tests._path import ROOT  # noqa: F401
from tests._investigation import investigation_test

from core import bsp
from core import project as P
from core import project_io
from mm9_patcher.mm9_patch import World


DATA_ROOT = os.path.join(ROOT, "mm9_data")


@investigation_test
class ProjectDoorCloneTests(unittest.TestCase):
    def load_sturmford_level(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "STURMFORDCITY.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            data = f.read()
        level = P.LevelEdit(path=path, source_kind=P.SOURCE_REZ, world=World.load(path))
        level._raw_bytes = data
        level.bsp = bsp.parse(data)
        return level

    def load_bootcamp_level(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            data = f.read()
        level = P.LevelEdit(
            path=path,
            source_kind=P.SOURCE_REZ,
            world=World.load(path),
            rez_path=os.path.join(DATA_ROOT, "WORLDS.REZ"),
            rez_vpath="WORLDS/BOOTCAMP",
        )
        level._raw_bytes = data
        level.bsp = bsp.parse(data)
        return level

    def test_clone_door_op_materializes_controller_objects(self):
        level = self.load_sturmford_level()
        level.append_op(P.CloneDoorOp(
            source_name="ChurchdoorR",
            new_name="ProjectChurchdoorR",
            target_pos=(-500.0, 7800.0, 5300.0),
        ))

        materialized = level.materialize()
        names = [obj.get("Name") for obj in materialized.objects[-2:]]

        self.assertEqual(names, ["ProjectChurchdoorR", "ProjectChurchdoorL"])
        self.assertEqual(materialized.objects[-2].get("DoubleDoorName"), "ProjectChurchdoorL")
        self.assertEqual(materialized.objects[-1].get("DoubleDoorName"), "ProjectChurchdoorR")
        self.assertEqual(materialized.objects[-2].get("Pos"), (-500.0, 7800.0, 5300.0))
        self.assertEqual(materialized.objects[-1].get("Pos"), (-500.0, 7800.0, 5204.0))

    def test_pending_clone_indices_are_visible_as_pending_additions(self):
        level = self.load_sturmford_level()
        existing_count = len(level.world.objects)
        op = P.CloneDoorOp(source_name="ChurchdoorR", new_name="IndexChurchdoorR")
        level.append_op(op)

        self.assertEqual(level.pending_add_offset_for_materialized(existing_count), (op, 0))
        self.assertEqual(level.pending_add_offset_for_materialized(existing_count + 1), (op, 1))
        self.assertIsNone(level.add_offset_for_materialized(existing_count))

    def test_clone_door_op_project_io_roundtrip(self):
        op = P.CloneDoorOp(
            source_name="Door32",
            new_name="Door32Clone",
            target_pos=(1.0, 2.0, 3.0),
            target_yaw=0.25,
            include_pair=False,
        )

        restored = project_io.dict_to_op(project_io.op_to_dict(op))

        self.assertIsInstance(restored, P.CloneDoorOp)
        self.assertEqual(restored.source_name, "Door32")
        self.assertEqual(restored.new_name, "Door32Clone")
        self.assertEqual(restored.target_pos, (1.0, 2.0, 3.0))
        self.assertEqual(restored.target_yaw, 0.25)
        self.assertFalse(restored.include_pair)

    def test_save_plan_carries_door_clone_bsp_metadata(self):
        level = self.load_sturmford_level()
        level.rez_path = os.path.join(DATA_ROOT, "WORLDS.REZ")
        level.rez_vpath = "WORLDS/STURMFORDCITY.DAT"
        level.append_op(P.CloneDoorOp(source_name="Door32", new_name="PlanDoor32"))
        project = P.Project(levels=[level])

        plan = project.save_plan()

        self.assertEqual(len(plan.dats), 1)
        self.assertEqual(len(plan.dats[0].door_clones), 1)
        self.assertEqual(plan.dats[0].door_clones[0].submodels[0].source_name, "Door32")
        self.assertEqual(plan.dats[0].door_clones[0].submodels[0].new_name, "PlanDoor32")
        self.assertEqual(plan.dats[0].stats()["door_clones"], 1)

    def test_retarget_pending_clone_from_either_leaf(self):
        level = self.load_sturmford_level()
        op = P.CloneDoorOp(
            source_name="ChurchdoorR",
            new_name="RetargetChurchdoorR",
            target_pos=(-500.0, 7800.0, 5300.0),
        )
        level.append_op(op)
        existing_count = len(level.world.objects)

        pending_op, object_offset = level.pending_add_offset_for_materialized(existing_count + 1)
        self.assertIs(pending_op, op)
        objects_before = level.objects_before_op(op)
        op.retarget_from_object(
            level,
            objects_before,
            object_offset,
            (-450.0, 7800.0, 5204.0),
        )

        materialized = level.materialize()
        self.assertEqual(materialized.objects[existing_count].get("Pos"), (-450.0, 7800.0, 5300.0))
        self.assertEqual(materialized.objects[existing_count + 1].get("Pos"), (-450.0, 7800.0, 5204.0))

    def test_rerotate_pending_clone_changes_controller_and_preview_bsp(self):
        level = self.load_sturmford_level()
        op = P.CloneDoorOp(
            source_name="Door32",
            new_name="RotatedDoor32",
            target_pos=(4100.0, 7790.0, 7350.0),
        )
        level.append_op(op)
        existing_count = len(level.world.objects)

        pending_op, object_offset = level.pending_add_offset_for_materialized(existing_count)
        self.assertIs(pending_op, op)
        op.rerotate_from_object(
            level,
            level.objects_before_op(op),
            object_offset,
            (0.0, math.pi / 2, 0.0, 0.0),
        )

        materialized = level.materialize()
        self.assertAlmostEqual(materialized.objects[existing_count].get("Rotation")[1], math.pi / 2)
        preview = level.preview_bsp()
        self.assertIsNotNone(preview.model_by_name("RotatedDoor32"))

    def test_save_plan_warns_for_bootcamp_terminal_tail_and_portal_name(self):
        level = self.load_bootcamp_level()
        level.append_op(P.CloneDoorOp(
            source_name="NewHouseDoor0",
            new_name="NewHouseDoorClone0",
            target_pos=(9200.0, 552.0, -1620.0),
        ))
        project = P.Project(levels=[level])

        plan = project.save_plan()
        warnings = "\n".join(plan.dats[0].validation_warnings)

        self.assertIn("terminal BSP tail", warnings)
        self.assertIn("PortalName", warnings)


if __name__ == "__main__":
    unittest.main()
