import os
import sys
import math
import unittest


from tests._path import ROOT  # noqa: F401

from core import bsp
from features.doors import clone as door_clone
from features.doors import links as door_links
from mm9_patcher.mm9_patch import World


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class DoorCloneTests(unittest.TestCase):
    def load_sturmford(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "STURMFORDCITY.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            data = f.read()
        return data, World.load(path), bsp.parse(data)

    def test_clone_single_physical_door_controller_and_bsp_record(self):
        data, world, bsp_world = self.load_sturmford()

        plan = door_clone.build_clone_plan(
            world.objects,
            bsp_world,
            data,
            "Door32",
            "Door32Clone",
            target_pos=(4100.0, 7790.0, 7350.0),
        )

        self.assertFalse(plan.paired)
        self.assertEqual(len(plan.objects), 1)
        self.assertEqual(len(plan.submodels), 1)

        cloned = plan.primary_object
        self.assertEqual(cloned.type_str, "Door")
        self.assertEqual(cloned.get("Name"), "Door32Clone")
        self.assertEqual(cloned.get("Pos"), (4100.0, 7790.0, 7350.0))
        self.assertEqual(cloned.get("Locked"), 1)
        self.assertEqual(cloned.get("JiggleSound"), r"Sounds\Door\knock.wav")

        original = door_links.find_physical_door_link(world.objects, bsp_world, "Door32")
        self.assertIsNotNone(original)
        submodel = plan.submodels[0]
        self.assertEqual(submodel.source_name, "Door32")
        self.assertEqual(submodel.new_name, "Door32Clone")
        self.assertEqual(submodel.raw_bytes, bsp_world.raw_model_bytes(data, original.model))

    def test_clone_paired_rotating_door_updates_mate_links_and_offsets(self):
        data, world, bsp_world = self.load_sturmford()

        plan = door_clone.build_clone_plan(
            world.objects,
            bsp_world,
            data,
            "ChurchdoorR",
            "TestChurchdoorR",
            target_pos=(-500.0, 7800.0, 5300.0),
        )

        self.assertTrue(plan.paired)
        self.assertEqual([obj.get("Name") for obj in plan.objects], ["TestChurchdoorR", "TestChurchdoorL"])
        self.assertEqual([m.new_name for m in plan.submodels], ["TestChurchdoorR", "TestChurchdoorL"])

        right, left = plan.objects
        self.assertEqual(right.type_str, "RotatingDoor")
        self.assertEqual(left.type_str, "RotatingDoor")
        self.assertEqual(right.get("DoubleDoorName"), "TestChurchdoorL")
        self.assertEqual(left.get("DoubleDoorName"), "TestChurchdoorR")
        self.assertEqual(right.get("Pos"), (-500.0, 7800.0, 5300.0))
        self.assertEqual(left.get("Pos"), (-500.0, 7800.0, 5204.0))
        self.assertEqual(right.get("RotationPoint"), (-500.0, 7800.0, 5300.0))
        self.assertEqual(left.get("RotationPoint"), (-500.0, 7800.0, 5204.0))
        self.assertEqual(right.get("RotationAngles"), (0.0, -90.0, 0.0))
        self.assertEqual(left.get("RotationAngles"), (0.0, 90.0, 0.0))

        original_right = door_links.find_physical_door_link(world.objects, bsp_world, "ChurchdoorR")
        original_left = door_links.find_physical_door_link(world.objects, bsp_world, "ChurchdoorL")
        self.assertEqual(plan.submodels[0].raw_bytes, bsp_world.raw_model_bytes(data, original_right.model))
        self.assertEqual(plan.submodels[1].raw_bytes, bsp_world.raw_model_bytes(data, original_left.model))

    def test_rejects_clone_name_collisions(self):
        data, world, bsp_world = self.load_sturmford()

        with self.assertRaisesRegex(ValueError, "already exists"):
            door_clone.build_clone_plan(
                world.objects,
                bsp_world,
                data,
                "Door32",
                "ChurchdoorR",
            )

    def test_suggest_clone_name_avoids_object_and_bsp_collisions(self):
        _data, world, bsp_world = self.load_sturmford()

        self.assertEqual(
            door_clone.suggest_clone_name(world.objects, bsp_world, "Door32"),
            "Door32Clone",
        )
        world.objects[0].set("Name", "Door32Clone")
        self.assertEqual(
            door_clone.suggest_clone_name(world.objects, bsp_world, "Door32"),
            "Door32Clone2",
        )
        self.assertEqual(
            door_clone.suggest_clone_name(world.objects, bsp_world, "ChurchdoorR"),
            "ChurchdoorCloneR",
        )
        self.assertEqual(
            door_clone.suggest_clone_name(world.objects, bsp_world, "MonsterDoor1", pair_name="MonsterDoor2"),
            "MonsterDoorClone1",
        )

    def test_left_right_pair_names_use_clone_before_side_suffix(self):
        _data, world, bsp_world = self.load_sturmford()

        self.assertEqual(
            door_clone.derive_pair_clone_name("StoreDoorLeft", "StoreDoorRight", "StoreDoorCloneLeft"),
            "StoreDoorCloneRight",
        )

    def test_preview_bsp_includes_translated_and_rotated_clone_model(self):
        data, world, bsp_world = self.load_sturmford()
        source = bsp_world.model_by_name("Door32")

        plan = door_clone.build_clone_plan(
            world.objects,
            bsp_world,
            data,
            "Door32",
            "Door32Preview",
            target_pos=(4100.0, 7790.0, 7350.0),
            target_yaw=math.pi / 2,
        )
        preview = door_clone.build_preview_bsp(bsp_world, [plan])
        cloned = preview.model_by_name("Door32Preview")

        self.assertIsNotNone(cloned)
        self.assertEqual(len(preview.world_models), len(bsp_world.world_models) + 1)
        self.assertIs(preview.world_models[0], bsp_world.world_models[0])
        self.assertNotEqual(cloned.points[0], source.points[0])
        self.assertEqual(cloned.name, "Door32Preview")

    def test_preview_bsp_transforms_uv_projection_with_geometry(self):
        data, world, bsp_world = self.load_sturmford()
        source = bsp_world.model_by_name("Door32")
        if not source.surfaces:
            self.skipTest("Door32 has no parsed surface UVs")

        plan = door_clone.build_clone_plan(
            world.objects,
            bsp_world,
            data,
            "Door32",
            "Door32UVPreview",
            target_pos=(4100.0, 7790.0, 7350.0),
            target_yaw=math.pi / 2,
        )
        preview = door_clone.build_preview_bsp(bsp_world, [plan])
        cloned = preview.model_by_name("Door32UVPreview")
        sub = plan.submodels[0]

        src_uv = source.surfaces[0].compute_uv(source.points[0])
        cloned_point = door_clone.transform_point(
            source.points[0],
            sub.source_pivot,
            sub.target_pivot,
            sub.yaw_radians,
        )
        cloned_uv = cloned.surfaces[0].compute_uv(cloned_point)

        self.assertAlmostEqual(cloned_uv[0], src_uv[0], places=4)
        self.assertAlmostEqual(cloned_uv[1], src_uv[1], places=4)


if __name__ == "__main__":
    unittest.main()
