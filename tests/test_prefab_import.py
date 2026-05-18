import os
import sys
import struct
import tempfile
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import bsp
import mm9_rezmgr
import prefab_import
import project as P
import project_io
from mm9_patcher.mm9_patch import World
from tests.test_game_resources import write_minimal_rez


DATA_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mm9_data")


class PrefabImportTests(unittest.TestCase):
    def load_bootcamp_bytes(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            return f.read()

    def fence_prefab_path(self):
        path = os.path.join(DATA_ROOT, "PreFabs", "Fences&Gates", "OldWoodFence1.dat")
        if not os.path.exists(path):
            self.skipTest(f"missing converted prefab: {path}")
        return path

    def test_static_plan_imports_physics_model_by_default_for_system_only_prefab(self):
        bootcamp = self.load_bootcamp_bytes()
        target_bsp = bsp.parse(bootcamp)
        plan = prefab_import.build_static_import_plan(
            target_bsp,
            self.fence_prefab_path(),
            new_name="ImportedFence",
            target_pos=(1000.0, 50.0, -2000.0),
        )

        self.assertEqual(plan.source_model_names, ["PhysicsBSP"])
        self.assertEqual(plan.source_model_roles, ["physics"])
        self.assertEqual(plan.info_flags_overrides, [2])
        self.assertEqual(len(plan.submodels), 1)
        self.assertEqual(plan.submodels[0].source_name, "PhysicsBSP")
        self.assertEqual(plan.submodels[0].new_name, "ImportedFence")

        preview = prefab_import.build_preview_bsp(target_bsp, [plan])
        imported = preview.model_by_name("ImportedFence")
        self.assertIsNotNone(imported)
        self.assertAlmostEqual(imported.min_box[0], 1000.0, places=3)
        self.assertAlmostEqual(imported.max_box[0], 1382.0, places=3)
        self.assertAlmostEqual(imported.max_box[1], 50.0, places=3)

    def test_rez_save_appends_static_prefab_bsp_model(self):
        bootcamp = self.load_bootcamp_bytes()
        original_bsp = bsp.parse(bootcamp)
        original_world = self._world_from_bytes(bootcamp)

        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            work_dir = os.path.join(tmp, "output")
            write_minimal_rez(source_rez, {"WORLDS/BOOTCAMP": bootcamp})

            project = P.Project(work_dir=work_dir)
            level = project.add_level_from_rez(source_rez, "WORLDS/BOOTCAMP")
            level.append_op(P.ImportPrefabBspOp(
                prefab_path=self.fence_prefab_path(),
                new_name="ImportedFence",
                target_pos=(1000.0, 50.0, -2000.0),
            ))

            plan = project.save_plan()
            self.assertEqual(plan.dats[0].stats()["prefab_imports"], 1)
            self.assertEqual(plan.dats[0].stats()["prefab_bsp_models"], 1)
            self.assertTrue(any(
                "uses PhysicsBSP polygon data as a normal visible submodel" in warning
                for warning in plan.dats[0].validation_warnings
            ))
            self.assertTrue(any(
                "uses Default texture names" in warning
                for warning in plan.dats[0].validation_warnings
            ))
            project.execute(plan)

            output_rez = os.path.join(work_dir, plan.batch_id, "data", "WORLDS.REZ")
            with mm9_rezmgr.RezReader(output_rez) as reader:
                changed = reader.extract_to_bytes("WORLDS/BOOTCAMP")

        changed_world = self._world_from_bytes(changed)
        changed_bsp = bsp.parse(changed)
        imported = changed_bsp.model_by_name("ImportedFence")

        self.assertIsNotNone(imported)
        self.assertEqual(len(changed_world.objects), len(original_world.objects) + 1)
        imported_obj = changed_world.objects[-1]
        self.assertEqual(imported_obj.type_str, "WorldObject")
        self.assertEqual(imported_obj.get("Name"), "ImportedFence")
        self.assertEqual(imported_obj.get("Visible"), 1)
        self.assertEqual(imported_obj.get("Solid"), 1)
        self.assertEqual(imported_obj.get("RayHit"), 1)
        self.assertEqual(imported_obj.get("BoxPhysics"), 0)
        self.assertEqual(len(changed_bsp.world_models), len(original_bsp.world_models) + 1)
        self.assertAlmostEqual(imported.min_box[0], 1000.0, places=3)
        self.assertAlmostEqual(imported.max_box[1], 50.0, places=3)
        raw = changed_bsp.raw_model_bytes(changed, imported)
        rel = imported.world_bsp_start - imported.raw_start
        self.assertEqual(struct.unpack_from("<I", raw, rel)[0], 2)

    def test_project_io_roundtrips_prefab_import_op(self):
        op = P.ImportPrefabBspOp(
            prefab_path=self.fence_prefab_path(),
            new_name="ImportedFence",
            target_pos=(1.0, 2.0, 3.0),
            target_yaw=0.5,
            include_roles=("visibility",),
        )

        restored = project_io.dict_to_op(project_io.op_to_dict(op))

        self.assertIsInstance(restored, P.ImportPrefabBspOp)
        self.assertEqual(restored.prefab_path, op.prefab_path)
        self.assertEqual(restored.new_name, "ImportedFence")
        self.assertEqual(restored.target_pos, (1.0, 2.0, 3.0))
        self.assertEqual(restored.target_yaw, 0.5)
        self.assertEqual(restored.include_roles, ("visibility",))

    def test_prefab_import_creates_same_named_worldobject_controller(self):
        bootcamp = self.load_bootcamp_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            write_minimal_rez(source_rez, {"WORLDS/BOOTCAMP": bootcamp})

            project = P.Project()
            level = project.add_level_from_rez(source_rez, "WORLDS/BOOTCAMP")
            baseline_count = len(level.materialize().objects)
            level.append_op(P.ImportPrefabBspOp(
                prefab_path=self.fence_prefab_path(),
                new_name="ImportedFence",
                target_pos=(1.0, 2.0, 3.0),
            ))

            materialized = level.materialize()

            self.assertEqual(len(materialized.objects), baseline_count + 1)
            helper = materialized.objects[-1]
            self.assertEqual(helper.type_str, "WorldObject")
            self.assertEqual(helper.get("Name"), "ImportedFence")
            self.assertEqual(helper.get("Pos"), (1.0, 2.0, 3.0))
            self.assertEqual(helper.get("Rotation"), (0.0, 0.0, 0.0, 0.0))
            self.assertEqual(level.prefab_import_for_materialized(baseline_count).new_name, "ImportedFence")

    def test_prefab_import_plan_records_source_roles(self):
        bootcamp = self.load_bootcamp_bytes()
        plan = prefab_import.build_static_import_plan(
            bsp.parse(bootcamp),
            self.fence_prefab_path(),
            new_name="ImportedFence",
        )

        self.assertEqual(plan.source_model_roles, ["physics"])
        self.assertEqual(plan.info_flags_overrides, [2])

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
