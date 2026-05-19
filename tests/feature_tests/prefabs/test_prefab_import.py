import os
import sys
import struct
import tempfile
import unittest


from tests._path import ROOT  # noqa: F401

from core import bsp
from core import rezmgr as mm9_rezmgr
from features.prefabs import import_static as prefab_import
from core import project as P
from core import project_io
from mm9_patcher.mm9_patch import World
from tests.core_tests.test_game_resources import write_minimal_rez


DATA_ROOT = os.path.join(ROOT, "mm9_data")


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
        self.assertEqual(restored.collision_mode, "none")

    def test_project_io_roundtrips_prefab_collision_mode(self):
        op = P.ImportPrefabBspOp(
            prefab_path=self.fence_prefab_path(),
            new_name="ImportedFence",
            collision_mode="invisible_bsp",
            collision_thickness=24.0,
            collision_segment_length=256.0,
        )

        restored = project_io.dict_to_op(project_io.op_to_dict(op))

        self.assertEqual(restored.collision_mode, "invisible_bsp")
        self.assertEqual(restored.collision_thickness, 24.0)
        self.assertEqual(restored.collision_segment_length, 256.0)

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

    def test_prefab_import_collision_helper_creates_hidden_invisible_brush(self):
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
                collision_mode="invisible_bsp",
            ))

            materialized = level.materialize()
            self.assertEqual(len(materialized.objects), len(original_world.objects) + 2)
            visible = materialized.objects[-2]
            collision = materialized.objects[-1]
            self.assertEqual(visible.type_str, "WorldObject")
            self.assertEqual(visible.get("Name"), "ImportedFence")
            self.assertEqual(collision.type_str, "InvisibleBrush")
            self.assertEqual(collision.get("Name"), "ImportedFence_Collision")
            self.assertEqual(collision.get("Visible"), 0)
            self.assertEqual(collision.get("Solid"), 1)
            self.assertEqual(collision.get("RayHit"), 1)
            self.assertEqual(collision.get("BoxPhysics"), 0)
            self.assertEqual(level.prefab_import_for_materialized(len(original_world.objects) + 1).new_name, "ImportedFence")

            plan = project.save_plan()
            self.assertEqual(plan.dats[0].stats()["prefab_bsp_models"], 2)
            self.assertTrue(any("InvisibleBrush collision helper" in warning
                                for warning in plan.dats[0].validation_warnings))
            project.execute(plan)

            output_rez = os.path.join(work_dir, plan.batch_id, "data", "WORLDS.REZ")
            with mm9_rezmgr.RezReader(output_rez) as reader:
                changed = reader.extract_to_bytes("WORLDS/BOOTCAMP")

        changed_bsp = bsp.parse(changed)
        self.assertEqual(len(changed_bsp.world_models), len(original_bsp.world_models) + 2)
        self.assertIsNotNone(changed_bsp.model_by_name("ImportedFence"))
        self.assertIsNotNone(changed_bsp.model_by_name("ImportedFence_Collision"))

    def test_prefab_import_plan_records_source_roles(self):
        bootcamp = self.load_bootcamp_bytes()
        plan = prefab_import.build_static_import_plan(
            bsp.parse(bootcamp),
            self.fence_prefab_path(),
            new_name="ImportedFence",
        )

        self.assertEqual(plan.source_model_roles, ["physics"])
        self.assertEqual(plan.info_flags_overrides, [2])

    def test_static_plan_can_add_collision_helper_model(self):
        bootcamp = self.load_bootcamp_bytes()
        plan = prefab_import.build_static_import_plan(
            bsp.parse(bootcamp),
            self.fence_prefab_path(),
            new_name="ImportedFence",
            collision_mode="invisible_bsp",
        )

        self.assertEqual([sub.new_name for sub in plan.submodels],
                         ["ImportedFence", "ImportedFence_Collision"])
        self.assertEqual(plan.source_model_roles, ["physics", "collision_helper"])
        self.assertEqual(plan.info_flags_overrides, [2, 2])

    def test_static_plan_can_add_scaled_box_collision_helper(self):
        bootcamp = self.load_bootcamp_bytes()
        target_bsp = bsp.parse(bootcamp)
        plan = prefab_import.build_static_import_plan(
            target_bsp,
            self.fence_prefab_path(),
            new_name="ImportedFence",
            target_pos=(1000.0, 50.0, -2000.0),
            collision_mode="box_approx",
            target_dat_bytes=bootcamp,
        )

        self.assertEqual([sub.new_name for sub in plan.submodels],
                         ["ImportedFence", "ImportedFence_Collision"])
        self.assertEqual(plan.source_model_roles, ["physics", "collision_box"])
        self.assertTrue(plan.submodels[1].source_name.startswith("InvisibleBrush"))
        preview = prefab_import.build_preview_bsp(target_bsp, [plan])
        visible = preview.model_by_name("ImportedFence")
        collision = preview.model_by_name("ImportedFence_Collision")
        self.assertIsNotNone(visible)
        self.assertIsNotNone(collision)
        self.assertIs(preview.world_models[0], target_bsp.world_models[0])
        self.assertEqual(len(collision.polygons), 6)
        self.assertAlmostEqual(collision.min_box[0], visible.min_box[0], places=3)
        self.assertAlmostEqual(collision.min_box[1], visible.min_box[1], places=3)
        self.assertAlmostEqual(collision.max_box[0], visible.max_box[0], places=3)
        self.assertAlmostEqual(collision.max_box[1], visible.max_box[1], places=3)
        self.assertAlmostEqual(collision.max_box[2] - collision.min_box[2], 8.0, places=3)
        self.assertAlmostEqual(
            (collision.min_box[2] + collision.max_box[2]) * 0.5,
            (visible.min_box[2] + visible.max_box[2]) * 0.5,
            places=3,
        )

    def test_static_plan_honors_collision_thickness(self):
        bootcamp = self.load_bootcamp_bytes()
        target_bsp = bsp.parse(bootcamp)
        plan = prefab_import.build_static_import_plan(
            target_bsp,
            self.fence_prefab_path(),
            new_name="ImportedFence",
            target_pos=(1000.0, 50.0, -2000.0),
            collision_mode="box_approx",
            collision_thickness=16.0,
            target_dat_bytes=bootcamp,
        )

        preview = prefab_import.build_preview_bsp(target_bsp, [plan])
        collision = preview.model_by_name("ImportedFence_Collision")

        self.assertIsNotNone(collision)
        self.assertAlmostEqual(collision.max_box[2] - collision.min_box[2], 16.0, places=3)

    def test_static_plan_splits_long_collision_box_into_segments(self):
        bootcamp = self.load_bootcamp_bytes()
        target_bsp = bsp.parse(bootcamp)
        plan = prefab_import.build_static_import_plan(
            target_bsp,
            self.fence_prefab_path(),
            new_name="ImportedFence",
            target_pos=(1000.0, 50.0, -2000.0),
            collision_mode="box_approx",
            collision_segment_length=128.0,
            target_dat_bytes=bootcamp,
        )

        collision_models = [sub for sub in plan.submodels if "_Collision" in sub.new_name]
        preview = prefab_import.build_preview_bsp(target_bsp, [plan])
        collision_names = [sub.new_name for sub in collision_models]
        collision_previews = [preview.model_by_name(name) for name in collision_names]

        self.assertEqual(collision_names, [
            "ImportedFence_Collision1",
            "ImportedFence_Collision2",
            "ImportedFence_Collision3",
        ])
        self.assertTrue(all(model is not None for model in collision_previews))
        self.assertTrue(all((model.max_box[0] - model.min_box[0]) <= 128.0 for model in collision_previews))

    def test_prefab_import_materializes_one_invisible_brush_per_collision_segment(self):
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
                target_pos=(1000.0, 50.0, -2000.0),
                collision_mode="box_approx",
                collision_segment_length=128.0,
            ))

            materialized = level.materialize()

        added = materialized.objects[baseline_count:]
        self.assertEqual([obj.get("Name") for obj in added], [
            "ImportedFence",
            "ImportedFence_Collision1",
            "ImportedFence_Collision2",
            "ImportedFence_Collision3",
        ])
        self.assertTrue(all(obj.type_str == "InvisibleBrush" for obj in added[1:]))

    def test_rez_save_appends_scaled_box_collision_helper(self):
        bootcamp = self.load_bootcamp_bytes()
        original_bsp = bsp.parse(bootcamp)

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
                collision_mode="box_approx",
            ))

            plan = project.save_plan()
            self.assertTrue(any("box collision helper" in warning
                                for warning in plan.dats[0].validation_warnings))
            project.execute(plan)

            output_rez = os.path.join(work_dir, plan.batch_id, "data", "WORLDS.REZ")
            with mm9_rezmgr.RezReader(output_rez) as reader:
                changed = reader.extract_to_bytes("WORLDS/BOOTCAMP")

        changed_bsp = bsp.parse(changed)
        self.assertEqual(len(changed_bsp.world_models), len(original_bsp.world_models) + 2)
        visible = changed_bsp.model_by_name("ImportedFence")
        collision = changed_bsp.model_by_name("ImportedFence_Collision")
        self.assertIsNotNone(visible)
        self.assertIsNotNone(collision)
        self.assertEqual(len(collision.polygons), 6)
        self.assertAlmostEqual(collision.min_box[0], visible.min_box[0], places=3)
        self.assertAlmostEqual(collision.max_box[2] - collision.min_box[2], 8.0, places=3)
        changed_world = self._world_from_bytes(changed)
        collision_obj = next(obj for obj in changed_world.objects if obj.get("Name") == "ImportedFence_Collision")
        self.assertEqual(collision_obj.type_str, "InvisibleBrush")
        self.assertIsNotNone(collision_obj.get("DamagerStuff"))
        self.assertAlmostEqual(collision_obj.get("Pos")[0], (collision.min_box[0] + collision.max_box[0]) * 0.5, places=3)
        self.assertAlmostEqual(collision_obj.get("Pos")[1], (collision.min_box[1] + collision.max_box[1]) * 0.5, places=3)
        self.assertAlmostEqual(collision_obj.get("Pos")[2], (collision.min_box[2] + collision.max_box[2]) * 0.5, places=3)

    def test_box_collision_helper_recovers_missing_level_raw_bytes(self):
        bootcamp = self.load_bootcamp_bytes()

        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            write_minimal_rez(source_rez, {"WORLDS/BOOTCAMP": bootcamp})

            project = P.Project()
            level = project.add_level_from_rez(source_rez, "WORLDS/BOOTCAMP")
            delattr(level, "_raw_bytes")
            plan = prefab_import.build_static_import_plan(
                level.preview_bsp(),
                self.fence_prefab_path(),
                new_name="ImportedFence",
                collision_mode="box_approx",
                target_dat_bytes=level.source_bytes(),
            )

        self.assertEqual(plan.source_model_roles, ["physics", "collision_box"])
        self.assertEqual([sub.new_name for sub in plan.submodels],
                         ["ImportedFence", "ImportedFence_Collision"])

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
