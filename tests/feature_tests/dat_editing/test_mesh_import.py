import os
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from core import bsp
from core import rezmgr as mm9_rezmgr
from core import project as P
from core import project_io
from features.dat_editing import bsp_compile
from features.dat_editing import export_roundtrip, mesh_import
from mm9_patcher.mm9_patch import World
from tests.core_tests.test_game_resources import write_minimal_rez


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class MeshImportTests(unittest.TestCase):
    def load_bootcamp(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            data = f.read()
        return path, data, bsp.parse(data), World.load(path)

    def exported_one_model(self, tmp):
        path, data, bsp_world, _world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models if m.polygons and m.points), None)
        if model is None:
            self.skipTest("BOOTCAMP has no importable BSP model")
        result = export_roundtrip.export_roundtrip(
            bsp_world,
            data,
            tmp,
            source_path=path,
            base_name="MeshImportSource",
            selected_model_names=[model.name],
        )
        return path, data, bsp_world, model, result

    def test_builds_preview_mesh_from_exported_obj_and_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            _path, _data, target_bsp, source_model, exported = self.exported_one_model(tmp)
            plan = mesh_import.build_mesh_import_plan(
                target_bsp,
                exported.obj_path,
                exported.meta_path,
                new_name="ImportedMeshPreview",
            )

            self.assertEqual(plan.new_name, "ImportedMeshPreview")
            self.assertEqual(len(plan.models), 1)
            imported = plan.models[0].mesh
            self.assertEqual(imported.name, "ImportedMeshPreview")
            self.assertEqual(len(imported.polygons), len(source_model.polygons))
            self.assertEqual(imported.texture_names, source_model.texture_names)
            self.assertIsNone(imported.raw_start)
            self.assertEqual(plan.collision_mode, "none")

            preview = mesh_import.build_preview_bsp(target_bsp, [plan])
            self.assertIsNotNone(preview.model_by_name("ImportedMeshPreview"))

    def test_mesh_import_can_generate_box_collision_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            _path, _data, target_bsp, _source_model, exported = self.exported_one_model(tmp)
            plan = mesh_import.build_mesh_import_plan(
                target_bsp,
                exported.obj_path,
                exported.meta_path,
                new_name="ImportedMeshPreview",
                collision_mode="box_approx",
                collision_thickness=16.0,
            )

            self.assertEqual(plan.collision_mode, "box_approx")
            self.assertEqual(len(plan.models), 2)
            visible, collision = plan.models
            self.assertEqual(visible.role, "visible")
            self.assertEqual(collision.role, "collision_box")
            self.assertEqual(collision.name, "ImportedMeshPreview_Collision")
            self.assertEqual(len(collision.mesh.polygons), 6)
            horizontal_sizes = [
                collision.mesh.max_box[0] - collision.mesh.min_box[0],
                collision.mesh.max_box[2] - collision.mesh.min_box[2],
            ]
            self.assertAlmostEqual(min(horizontal_sizes), 16.0, places=3)

    def test_compiled_mesh_record_roundtrips_through_bsp_parser(self):
        model = bsp.WorldModelMesh(
            name="CompiledQuad",
            min_box=(0.0, 0.0, 0.0),
            max_box=(10.0, 0.0, 10.0),
            translation=(0.0, 0.0, 0.0),
            points=[(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 0.0, 10.0), (0.0, 0.0, 10.0)],
            polygons=[bsp.Polygon([0, 1, 2, 3], 0, 0)],
            texture_names=["TEXTURES\\World\\Floor.dtx"],
            surfaces=[bsp.Surface((0, 0, 0), (1, 0, 0), (0, 0, 1), 0, 0, 0)],
        )

        record = bsp_compile.compile_world_model_record(model)
        patched = bsp_compile.patch_next_world_item(record, 1234)

        self.assertEqual(int.from_bytes(patched[:4], "little"), 1234)
        self.assertGreater(len(patched), 36)

    def test_project_op_materializes_controller_and_preview_bsp(self):
        _path, data, original_bsp, _world = self.load_bootcamp()
        original_model = next((m for m in original_bsp.world_models if m.polygons and m.points), None)
        if original_model is None:
            self.skipTest("BOOTCAMP has no importable BSP model")
        with tempfile.TemporaryDirectory() as tmp:
            exported = export_roundtrip.export_roundtrip(
                original_bsp,
                data,
                tmp,
                base_name="MeshImportSource",
                selected_model_names=[original_model.name],
            )
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            write_minimal_rez(source_rez, {"WORLDS/BOOTCAMP": data})

            project = P.Project(work_dir=os.path.join(tmp, "out"))
            level = P.LevelEdit(
                path=f"{source_rez}::WORLDS/BOOTCAMP",
                source_kind=P.SOURCE_REZ,
                rez_path=source_rez,
                rez_vpath="WORLDS/BOOTCAMP",
                world=World.load(os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")),
            )
            level._raw_bytes = data
            project.levels.append(level)
            baseline_count = len(level.materialize().objects)
            level.append_op(P.ImportMeshBspOp(
                obj_path=exported.obj_path,
                meta_path=exported.meta_path,
                new_name="ImportedMeshPreview",
                collision_mode="box_approx",
            ))

            materialized = level.materialize()
            self.assertEqual(len(materialized.objects), baseline_count + 2)
            self.assertEqual(materialized.objects[-2].get("Name"), "ImportedMeshPreview")
            self.assertEqual(materialized.objects[-2].type_str, "WorldObject")
            self.assertEqual(materialized.objects[-1].get("Name"), "ImportedMeshPreview_Collision")
            self.assertEqual(materialized.objects[-1].type_str, "InvisibleBrush")
            self.assertEqual(materialized.objects[-1].get("Visible"), 0)

            preview = level.preview_bsp()
            self.assertIsNotNone(preview.model_by_name("ImportedMeshPreview"))
            self.assertIsNotNone(preview.model_by_name("ImportedMeshPreview_Collision"))
            self.assertEqual(len(preview.world_models), len(original_bsp.world_models) + 2)

            plan = project.save_plan()
            self.assertEqual(plan.dats[0].stats()["mesh_imports"], 1)
            self.assertEqual(plan.dats[0].stats()["mesh_bsp_models"], 2)
            self.assertTrue(any("experimental minimal BSP compiler" in warning for warning in plan.dats[0].validation_warnings))
            project.execute(plan)

            output_rez = os.path.join(tmp, "out", plan.batch_id, "data", "WORLDS.REZ")
            with mm9_rezmgr.RezReader(output_rez) as reader:
                changed = reader.extract_to_bytes("WORLDS/BOOTCAMP")

        changed_world = self._world_from_bytes(changed)
        changed_bsp = bsp.parse(changed)
        imported = changed_bsp.model_by_name("ImportedMeshPreview")
        collision = changed_bsp.model_by_name("ImportedMeshPreview_Collision")
        self.assertIsNotNone(imported)
        self.assertIsNotNone(collision)
        self.assertTrue(any(obj.get("Name") == "ImportedMeshPreview" for obj in changed_world.objects))
        self.assertTrue(any(obj.get("Name") == "ImportedMeshPreview_Collision"
                            and obj.type_str == "InvisibleBrush"
                            for obj in changed_world.objects))
        self.assertEqual(len(changed_bsp.world_models), len(original_bsp.world_models) + 2)
        self.assertEqual(len(imported.polygons), len(original_model.polygons))

    def test_project_io_roundtrips_mesh_import_op(self):
        op = P.ImportMeshBspOp(
            obj_path="C:/tmp/level_geometry.obj",
            meta_path="C:/tmp/level_geometry.datmeta.json",
            new_name="ImportedMeshPreview",
            target_pos=(1.0, 2.0, 3.0),
            target_yaw=0.25,
            collision_mode="box_approx",
            collision_thickness=24.0,
            collision_segment_length=128.0,
        )

        restored = project_io.dict_to_op(project_io.op_to_dict(op))

        self.assertIsInstance(restored, P.ImportMeshBspOp)
        self.assertEqual(restored.obj_path, op.obj_path)
        self.assertEqual(restored.meta_path, op.meta_path)
        self.assertEqual(restored.new_name, op.new_name)
        self.assertEqual(restored.target_pos, (1.0, 2.0, 3.0))
        self.assertEqual(restored.target_yaw, 0.25)
        self.assertEqual(restored.collision_mode, "box_approx")
        self.assertEqual(restored.collision_thickness, 24.0)
        self.assertEqual(restored.collision_segment_length, 128.0)

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
