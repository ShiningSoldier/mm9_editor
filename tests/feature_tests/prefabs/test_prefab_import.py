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
from mm9_patcher.mm9_patch import Header, Property, World, WorldObject
from tests.core_tests.test_game_resources import write_minimal_rez
from tests.feature_tests.prefabs._fixtures import (
    box_model,
    write_legacy_ed_prefab,
    write_minimal_dat,
    write_prefab_fixtures,
)


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class PrefabImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fence_path, cls.door_path = write_prefab_fixtures(cls._tmp.name)
        cls.ed_path = write_legacy_ed_prefab(cls._tmp.name)
        cls.multi_path = os.path.join(cls._tmp.name, "MultiStatic.dat")
        write_minimal_dat(cls.multi_path, [
            box_model("PartA", (-10.0, 0.0, -10.0), (0.0, 20.0, 10.0)),
            box_model("PartB", (0.0, 0.0, -10.0), (10.0, 30.0, 10.0)),
        ], [])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def load_bootcamp_bytes(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            return f.read()

    def fence_prefab_path(self):
        return self.fence_path

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
        self.assertAlmostEqual(imported.min_box[0], 809.0, places=3)
        self.assertAlmostEqual(imported.max_box[0], 1191.0, places=3)
        self.assertAlmostEqual(imported.min_box[1], 50.0, places=3)

    def test_static_plan_compiles_legacy_ed_brushes_into_one_model(self):
        bootcamp = self.load_bootcamp_bytes()
        target_bsp = bsp.parse(bootcamp)

        plan = prefab_import.build_static_import_plan(
            target_bsp,
            self.ed_path,
            new_name="ImportedSourceChair",
            target_pos=(1000.0, 50.0, -2000.0),
        )
        imported = prefab_import.build_preview_bsp(target_bsp, [plan]).model_by_name(
            "ImportedSourceChair"
        )

        self.assertEqual(plan.visible_model_names, ["ImportedSourceChair"])
        self.assertEqual(plan.source_model_names, ["legacy_ed_visible_brushes"])
        self.assertEqual(plan.source_model_roles, ["geometry"])
        self.assertIsNotNone(imported)
        self.assertEqual(len(imported.polygons), 2)
        self.assertEqual(set(imported.texture_names), {
            r"TEXTURES\World\SourceFloor.dtx",
            r"TEXTURES\World\SourceBack.dtx",
        })
        self.assertAlmostEqual(imported.min_box[1], 50.0, places=3)
        self.assertTrue(all(surface.flags & bsp.SURF_SOLID for surface in imported.surfaces))

    def test_legacy_ed_exact_collision_uses_a_compiled_hidden_copy(self):
        bootcamp = self.load_bootcamp_bytes()
        target_bsp = bsp.parse(bootcamp)

        plan = prefab_import.build_static_import_plan(
            target_bsp,
            self.ed_path,
            new_name="ImportedSourceChair",
            collision_mode="invisible_bsp",
        )
        preview = prefab_import.build_preview_bsp(target_bsp, [plan])

        self.assertEqual(plan.collision_model_names, ["ImportedSourceChair_Collision"])
        self.assertEqual(plan.source_model_roles, ["geometry", "collision_helper"])
        self.assertEqual(len(preview.model_by_name("ImportedSourceChair").polygons), 2)
        self.assertEqual(len(preview.model_by_name("ImportedSourceChair_Collision").polygons), 2)

    def test_static_plan_prefers_controller_geometry_and_authored_physics_collision(self):
        bootcamp = self.load_bootcamp_bytes()
        plan = prefab_import.build_static_import_plan(
            bsp.parse(bootcamp),
            self.door_path,
            new_name="ImportedDoor",
            collision_mode="invisible_bsp",
        )

        self.assertEqual(plan.visible_model_names, ["ImportedDoor"])
        self.assertEqual(plan.collision_model_names, ["ImportedDoor_Collision"])
        self.assertEqual(plan.source_model_names, ["Door1", "PhysicsBSP"])
        self.assertEqual(plan.source_model_roles, ["controller_geometry", "collision_helper"])
        self.assertNotIn("VisBSP", plan.source_model_names)

    def test_visibility_role_requires_explicit_diagnostic_override(self):
        bootcamp = self.load_bootcamp_bytes()
        target = bsp.parse(bootcamp)

        with self.assertRaisesRegex(ValueError, "VisBSP import is unsafe"):
            prefab_import.build_static_import_plan(
                target,
                self.fence_prefab_path(),
                include_roles=("visibility",),
            )

        diagnostic = prefab_import.build_static_import_plan(
            target,
            self.fence_prefab_path(),
            include_roles=("visibility",),
            allow_unsafe_visibility=True,
        )
        self.assertEqual(diagnostic.source_model_roles, ["visibility"])

    def test_original_origin_anchor_remains_available(self):
        bootcamp = self.load_bootcamp_bytes()
        plan = prefab_import.build_static_import_plan(
            bsp.parse(bootcamp),
            self.fence_prefab_path(),
            new_name="ImportedFence",
            target_pos=(1000.0, 50.0, -2000.0),
            placement_anchor="original_origin",
        )
        imported = prefab_import.build_preview_bsp(bsp.parse(bootcamp), [plan]).model_by_name("ImportedFence")

        self.assertEqual(plan.source_pivot, (0.0, 0.0, 0.0))
        self.assertAlmostEqual(imported.min_box[0], 1000.0, places=3)
        self.assertAlmostEqual(imported.max_box[1], 50.0, places=3)

    def test_controller_anchor_uses_same_named_controller_position(self):
        bootcamp = self.load_bootcamp_bytes()
        plan = prefab_import.build_static_import_plan(
            bsp.parse(bootcamp),
            self.door_path,
            new_name="ImportedDoor",
            placement_anchor="controller_pivot",
        )

        self.assertEqual(plan.source_pivot, (2.0, 3.0, 4.0))

    def test_target_worldobject_name_collision_is_rejected_before_placement(self):
        bootcamp = self.load_bootcamp_bytes()
        with self.assertRaisesRegex(ValueError, "WorldObject named 'ImportedFence'"):
            prefab_import.build_static_import_plan(
                bsp.parse(bootcamp),
                self.fence_prefab_path(),
                new_name="ImportedFence",
                target_object_names=["ImportedFence"],
            )

    def test_multi_model_import_creates_one_matching_controller_per_model(self):
        bootcamp = self.load_bootcamp_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            write_minimal_rez(source_rez, {"WORLDS/BOOTCAMP": bootcamp})
            project = P.Project()
            level = project.add_level_from_rez(source_rez, "WORLDS/BOOTCAMP")
            baseline = len(level.materialize().objects)
            level.append_op(P.ImportPrefabBspOp(
                prefab_path=self.multi_path,
                new_name="ImportedSet",
            ))

            added = level.materialize().objects[baseline:]

        self.assertEqual(
            [obj.get("Name") for obj in added],
            ["ImportedSet_PartA", "ImportedSet_PartB"],
        )

    def test_stored_canonical_template_does_not_require_target_instance(self):
        template = WorldObject("WorldObject", [
            Property("Name", 0, 0, "noname"),
            Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
            Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
            Property("Visible", 5, 0, 1),
            Property("Solid", 5, 0, 1),
            Property("RayHit", 5, 0, 1),
            Property("BoxPhysics", 5, 0, 0),
        ])
        empty_world = World(Header(66, 0, 0, (0,) * 8), b"", [], b"")
        op = P.ImportPrefabBspOp(
            prefab_path=self.fence_prefab_path(),
            new_name="ImportedFence",
            target_pos=(1.0, 2.0, 3.0),
            worldobject_template=template,
        )

        created = op.apply_to(empty_world)

        self.assertEqual(created[0].get("Name"), "ImportedFence")
        self.assertEqual(created[0].get("Pos"), (1.0, 2.0, 3.0))

    def test_box_import_materializes_in_level_without_target_templates(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "GREATGATE.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as handle:
            greatgate = handle.read()
        base_template = WorldObject("WorldObject", [
            Property("Name", 0, 0, "noname"),
            Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
            Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
            Property("Visible", 5, 0, 1),
            Property("Solid", 5, 0, 1),
            Property("RayHit", 5, 0, 1),
            Property("BoxPhysics", 5, 0, 0),
        ])
        collision_template = WorldObject(
            "InvisibleBrush",
            [Property(prop.name, prop.code, prop.flags, prop.value) for prop in base_template.props],
        )
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            write_minimal_rez(source_rez, {"WORLDS/GREATGATE": greatgate})
            project = P.Project()
            level = project.add_level_from_rez(source_rez, "WORLDS/GREATGATE")
            self.assertFalse(any(obj.type_str == "WorldObject" for obj in level.world.objects))
            baseline_count = len(level.world.objects)
            level.append_op(P.ImportPrefabBspOp(
                prefab_path=self.fence_prefab_path(),
                new_name="ImportedFence",
                collision_mode="box_approx",
                worldobject_template=base_template,
                invisiblebrush_template=collision_template,
            ))

            added = level.materialize().objects[baseline_count:]
            preview = level.preview_bsp()

        self.assertEqual(
            [obj.get("Name") for obj in added],
            ["ImportedFence", "ImportedFence_Collision"],
        )
        self.assertIsNotNone(preview.model_by_name("ImportedFence_Collision"))

    def test_rez_save_rejects_minimal_compiled_prefab_bsp_model(self):
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

            with self.assertRaisesRegex(ValueError, "not a complete MM9 runtime BSP"):
                project.save_plan()

    def test_rez_save_rejects_preview_compiled_legacy_ed_geometry(self):
        bootcamp = self.load_bootcamp_bytes()
        original_bsp = bsp.parse(bootcamp)

        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            work_dir = os.path.join(tmp, "output")
            write_minimal_rez(source_rez, {"WORLDS/BOOTCAMP": bootcamp})

            project = P.Project(work_dir=work_dir)
            level = project.add_level_from_rez(source_rez, "WORLDS/BOOTCAMP")
            level.append_op(P.ImportPrefabBspOp(
                prefab_path=self.ed_path,
                new_name="ImportedSourceChair",
                target_pos=(1000.0, 50.0, -2000.0),
            ))
            with self.assertRaisesRegex(ValueError, "editor-preview ED BSP"):
                project.save_plan()

    def test_rez_save_resource_backed_prop_does_not_append_bsp(self):
        bootcamp = self.load_bootcamp_bytes()
        original_bsp = bsp.parse(bootcamp)
        original_world = self._world_from_bytes(bootcamp)
        template = next(
            obj for obj in original_world.objects
            if obj.type_str == "Prop"
            and (obj.get("Filename") or "").lower()
            == r"models\props\bookcase02ew.abc"
        )

        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            work_dir = os.path.join(tmp, "output")
            write_minimal_rez(source_rez, {"WORLDS/BOOTCAMP": bootcamp})
            project = P.Project(work_dir=work_dir)
            level = project.add_level_from_rez(source_rez, "WORLDS/BOOTCAMP")
            level.append_op(P.ImportResourcePrefabOp(
                template=template,
                overrides={
                    "Name": "ImportedBookcase",
                    "Pos": [1000.0, 50.0, -2000.0],
                    "Filename": r"models\props\bookcase02ew.abc",
                    "Skin": r"skins\props\bookcase02.dtx",
                },
                prefab_path=self.ed_path,
                candidate_id="Prop|bookcase02ew",
                model_path=r"models\props\bookcase02ew.abc",
                skin_paths=(r"skins\props\bookcase02.dtx",),
            ))

            plan = project.save_plan()
            self.assertEqual(plan.dats[0].stats()["resource_prefab_imports"], 1)
            project.execute(plan)
            output_rez = os.path.join(work_dir, plan.batch_id, "data", "WORLDS.REZ")
            with mm9_rezmgr.RezReader(output_rez) as reader:
                changed = reader.extract_to_bytes("WORLDS/BOOTCAMP")

        changed_bsp = bsp.parse(changed)
        changed_world = self._world_from_bytes(changed)
        imported = next(
            obj for obj in changed_world.objects
            if obj.get("Name") == "ImportedBookcase"
        )
        self.assertEqual(imported.type_str, "Prop")
        self.assertEqual(imported.get("Filename"), r"models\props\bookcase02ew.abc")
        self.assertEqual(
            len(changed_bsp.world_models),
            len(original_bsp.world_models),
        )

    def test_project_io_roundtrips_prefab_import_op(self):
        op = P.ImportPrefabBspOp(
            prefab_path=self.fence_prefab_path(),
            new_name="ImportedFence",
            target_pos=(1.0, 2.0, 3.0),
            target_yaw=0.5,
            include_roles=("visibility",),
            placement_anchor="center",
            allow_unsafe_visibility=True,
            preview_only=True,
            worldobject_template=WorldObject("WorldObject", [
                Property("Name", 0, 0, "noname"),
            ]),
        )

        restored = project_io.dict_to_op(project_io.op_to_dict(op))

        self.assertIsInstance(restored, P.ImportPrefabBspOp)
        self.assertEqual(restored.prefab_path, op.prefab_path)
        self.assertEqual(restored.new_name, "ImportedFence")
        self.assertEqual(restored.target_pos, (1.0, 2.0, 3.0))
        self.assertEqual(restored.target_yaw, 0.5)
        self.assertEqual(restored.include_roles, ("visibility",))
        self.assertEqual(restored.placement_anchor, "center")
        self.assertTrue(restored.allow_unsafe_visibility)
        self.assertTrue(restored.preview_only)
        self.assertEqual(restored.worldobject_template.type_str, "WorldObject")
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

            with self.assertRaisesRegex(ValueError, "not a complete MM9 runtime BSP"):
                project.save_plan()

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
        self.assertEqual(plan.submodels[1].source_name, "generated_geometry")
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

    def test_rez_save_rejects_scaled_box_collision_helper(self):
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

            with self.assertRaisesRegex(ValueError, "generated collision box"):
                project.save_plan()

    def test_box_collision_helper_recovers_missing_level_raw_bytes(self):
        bootcamp = self.load_bootcamp_bytes()
        plan = prefab_import.build_static_import_plan(
            bsp.parse(bootcamp),
            self.fence_prefab_path(),
            new_name="ImportedFence",
            collision_mode="box_approx",
            target_dat_bytes=None,
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
