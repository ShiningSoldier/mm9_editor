import os
import sys
import math
import struct
import tempfile
import unittest


from tests._path import ROOT  # noqa: F401
from tests._investigation import investigation_test

from core import bsp
from features.doors import clone as door_clone
from core import rezmgr as mm9_rezmgr
from core import project as P
from mm9_patcher.mm9_patch import World
from tests.core_tests.test_game_resources import write_minimal_rez


DATA_ROOT = os.path.join(ROOT, "mm9_data")


@investigation_test
class DoorBspWriterTests(unittest.TestCase):
    def load_sturmford_bytes(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "STURMFORDCITY.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            return f.read()

    def load_bootcamp_bytes(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            return f.read()

    def test_rez_save_appends_cloned_door_bsp_submodel(self):
        sturmford = self.load_sturmford_bytes()
        original_bsp = bsp.parse(sturmford)
        original_world = World.load(os.path.join(DATA_ROOT, "WORLDS", "STURMFORDCITY.DAT"))
        source_model = original_bsp.model_by_name("Door32")
        self.assertIsNotNone(source_model)

        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            work_dir = os.path.join(tmp, "output")
            write_minimal_rez(source_rez, {"WORLDS/STURMFORDCITY": sturmford})

            project = P.Project(work_dir=work_dir)
            level = project.add_level_from_rez(source_rez, "WORLDS/STURMFORDCITY")
            level.append_op(P.CloneDoorOp(
                source_name="Door32",
                new_name="Door32Clone",
                target_pos=(4100.0, 7790.0, 7350.0),
            ))

            plan = project.save_plan()
            project.execute(plan)

            output_rez = os.path.join(work_dir, plan.batch_id, "data", "WORLDS.REZ")
            with mm9_rezmgr.RezReader(output_rez) as reader:
                changed = reader.extract_to_bytes("WORLDS/STURMFORDCITY")

        changed_world = self._world_from_bytes(changed)
        changed_bsp = bsp.parse(changed)
        cloned_model = changed_bsp.model_by_name("Door32Clone")

        self.assertIsNotNone(cloned_model)
        self.assertEqual(len(changed_bsp.world_models), len(original_bsp.world_models) + 1)
        self.assertEqual(len(changed_world.objects), len(original_world.objects) + 1)
        self.assertTrue(any(obj.get("Name") == "Door32Clone" for obj in changed_world.objects))

        delta = (68.0, 6.0, 8.0)
        self.assertAlmostEqual(cloned_model.min_box[0], source_model.min_box[0] + delta[0], places=3)
        self.assertAlmostEqual(cloned_model.min_box[1], source_model.min_box[1] + delta[1], places=3)
        self.assertAlmostEqual(cloned_model.min_box[2], source_model.min_box[2] + delta[2], places=3)
        self.assertAlmostEqual(cloned_model.points[0][0], source_model.points[0][0] + delta[0], places=3)
        self.assertAlmostEqual(cloned_model.points[0][1], source_model.points[0][1] + delta[1], places=3)
        self.assertAlmostEqual(cloned_model.points[0][2], source_model.points[0][2] + delta[2], places=3)

    def test_rez_save_appends_paired_rotating_door_bsp_submodels(self):
        sturmford = self.load_sturmford_bytes()
        original_bsp = bsp.parse(sturmford)

        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            work_dir = os.path.join(tmp, "output")
            write_minimal_rez(source_rez, {"WORLDS/STURMFORDCITY": sturmford})

            project = P.Project(work_dir=work_dir)
            level = project.add_level_from_rez(source_rez, "WORLDS/STURMFORDCITY")
            level.append_op(P.CloneDoorOp(
                source_name="ChurchdoorR",
                new_name="ChurchdoorCloneR",
                target_pos=(-500.0, 7800.0, 5300.0),
            ))

            plan = project.save_plan()
            project.execute(plan)

            output_rez = os.path.join(work_dir, plan.batch_id, "data", "WORLDS.REZ")
            with mm9_rezmgr.RezReader(output_rez) as reader:
                changed = reader.extract_to_bytes("WORLDS/STURMFORDCITY")

        changed_world = self._world_from_bytes(changed)
        changed_bsp = bsp.parse(changed)
        object_names = {obj.get("Name") for obj in changed_world.objects}

        self.assertIn("ChurchdoorCloneR", object_names)
        self.assertIn("ChurchdoorCloneL", object_names)
        self.assertIsNotNone(changed_bsp.model_by_name("ChurchdoorCloneR"))
        self.assertIsNotNone(changed_bsp.model_by_name("ChurchdoorCloneL"))
        self.assertEqual(len(changed_bsp.world_models), len(original_bsp.world_models) + 2)

    def test_saved_rotated_clone_preserves_surface_uv_projection(self):
        sturmford = self.load_sturmford_bytes()
        original_bsp = bsp.parse(sturmford)
        source_model = original_bsp.model_by_name("Door32")
        if not source_model.surfaces:
            self.skipTest("Door32 has no parsed surface UVs")

        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            work_dir = os.path.join(tmp, "output")
            write_minimal_rez(source_rez, {"WORLDS/STURMFORDCITY": sturmford})

            project = P.Project(work_dir=work_dir)
            level = project.add_level_from_rez(source_rez, "WORLDS/STURMFORDCITY")
            level.append_op(P.CloneDoorOp(
                source_name="Door32",
                new_name="Door32RotatedUV",
                target_pos=(4100.0, 7790.0, 7350.0),
                target_yaw=math.pi / 2,
            ))

            plan = project.save_plan()
            project.execute(plan)

            output_rez = os.path.join(work_dir, plan.batch_id, "data", "WORLDS.REZ")
            with mm9_rezmgr.RezReader(output_rez) as reader:
                changed = reader.extract_to_bytes("WORLDS/STURMFORDCITY")

        changed_bsp = bsp.parse(changed)
        cloned_model = changed_bsp.model_by_name("Door32RotatedUV")
        submodel = plan.dats[0].door_clones[0].submodels[0]
        transformed_point = door_clone.transform_point(
            source_model.points[0],
            submodel.source_pivot,
            submodel.target_pivot,
            submodel.yaw_radians,
        )

        src_uv = source_model.surfaces[0].compute_uv(source_model.points[0])
        cloned_uv = cloned_model.surfaces[0].compute_uv(transformed_point)
        self.assertAlmostEqual(cloned_uv[0], src_uv[0], places=4)
        self.assertAlmostEqual(cloned_uv[1], src_uv[1], places=4)

    def test_bootcamp_clone_preserves_terminal_world_model_tail(self):
        bootcamp = self.load_bootcamp_bytes()
        original_header_obj = struct.unpack_from("<I", bootcamp, 4)[0]
        original_bsp = bsp.parse(bootcamp)
        physics = original_bsp.model_by_name("PhysicsBSP")
        self.assertIsNotNone(physics)
        self.assertLess(physics.next_world_item, original_header_obj)
        self.assertEqual(
            struct.unpack_from("<I", bootcamp, physics.next_world_item)[0],
            original_header_obj,
        )

        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            work_dir = os.path.join(tmp, "output")
            write_minimal_rez(source_rez, {"WORLDS/BOOTCAMP": bootcamp})

            project = P.Project(work_dir=work_dir)
            level = project.add_level_from_rez(source_rez, "WORLDS/BOOTCAMP")
            level.append_op(P.CloneDoorOp(
                source_name="NewHouseDoor0",
                new_name="NewHouseDoorClone0",
                target_pos=(9200.0, 552.0, -1620.0),
            ))
            plan = project.save_plan()
            project.execute(plan)

            output_rez = os.path.join(work_dir, plan.batch_id, "data", "WORLDS.REZ")
            with mm9_rezmgr.RezReader(output_rez) as reader:
                changed = reader.extract_to_bytes("WORLDS/BOOTCAMP")

        changed_header_obj = struct.unpack_from("<I", changed, 4)[0]
        changed_bsp = bsp.parse(changed)
        changed_physics = changed_bsp.model_by_name("PhysicsBSP")
        self.assertEqual(changed_physics.next_world_item, physics.next_world_item)
        self.assertIsNotNone(changed_bsp.model_by_name("NewHouseDoorClone0"))
        self.assertIsNotNone(changed_bsp.model_by_name("NewHouseDoorClone1"))

        inserted_len = changed_header_obj - original_header_obj
        shifted_tail = physics.next_world_item + inserted_len
        self.assertEqual(
            struct.unpack_from("<I", changed, shifted_tail)[0],
            changed_header_obj,
        )

    def _world_from_bytes(self, data: bytes) -> World:
        fd, path = tempfile.mkstemp(suffix=".DAT")
        os.close(fd)
        try:
            with open(path, "wb") as f:
                f.write(data)
            return World.load(path)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
