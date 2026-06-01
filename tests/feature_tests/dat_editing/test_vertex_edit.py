import os
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from core import bsp
from core import project as P
from core import project_io
from core import rezmgr as mm9_rezmgr
from features.dat_editing import export_roundtrip, vertex_edit
from mm9_patcher.mm9_patch import World
from tests.core_tests.test_game_resources import write_minimal_rez


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class VertexEditTests(unittest.TestCase):
    def load_bootcamp(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            data = f.read()
        return path, data, bsp.parse(data), World.load(path)

    def export_model(self, tmp):
        path, data, bsp_world, world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models
                      if m.polygons and len(m.points) >= 3 and not m.is_skybox()), None)
        if model is None:
            self.skipTest("BOOTCAMP has no editable BSP model")
        result = export_roundtrip.export_roundtrip(
            bsp_world,
            data,
            tmp,
            source_path=path,
            base_name="VertexEditSource",
            selected_model_names=[model.name],
        )
        return path, data, bsp_world, world, model, result

    def nudge_first_vertex(self, obj_path, delta_x=10.0):
        with open(obj_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for index, line in enumerate(lines):
            if line.startswith("v "):
                parts = line.split()
                parts[1] = f"{float(parts[1]) + delta_x:.6f}"
                lines[index] = " ".join(parts) + "\n"
                break
        with open(obj_path, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(lines)

    def test_vertex_edit_plan_patches_preview_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            _path, data, bsp_world, _world, source_model, exported = self.export_model(tmp)
            self.nudge_first_vertex(exported.obj_path, delta_x=10.0)

            plan = vertex_edit.build_vertex_edit_plan(
                bsp_world,
                data,
                exported.obj_path,
                exported.meta_path,
            )

            self.assertEqual(len(plan.models), 1)
            edited = plan.models[0].edited_model
            self.assertEqual(edited.name, source_model.name)
            self.assertAlmostEqual(edited.points[0][0], source_model.points[0][0] - 10.0, places=3)
            preview = vertex_edit.build_preview_bsp(bsp_world, [plan])
            self.assertAlmostEqual(
                preview.model_by_name(source_model.name).points[0][0],
                source_model.points[0][0] - 10.0,
                places=3,
            )

    def test_vertex_edit_save_patches_existing_bsp_record(self):
        _path, data, original_bsp, original_world = self.load_bootcamp()
        source_model = next((m for m in original_bsp.world_models
                             if m.polygons and len(m.points) >= 3 and not m.is_skybox()), None)
        if source_model is None:
            self.skipTest("BOOTCAMP has no editable BSP model")

        with tempfile.TemporaryDirectory() as tmp:
            exported = export_roundtrip.export_roundtrip(
                original_bsp,
                data,
                tmp,
                base_name="VertexEditSource",
                selected_model_names=[source_model.name],
            )
            self.nudge_first_vertex(exported.obj_path, delta_x=10.0)
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            write_minimal_rez(source_rez, {"WORLDS/BOOTCAMP": data})

            project = P.Project(work_dir=os.path.join(tmp, "out"))
            level = P.LevelEdit(
                path=f"{source_rez}::WORLDS/BOOTCAMP",
                source_kind=P.SOURCE_REZ,
                rez_path=source_rez,
                rez_vpath="WORLDS/BOOTCAMP",
                world=original_world,
            )
            level._raw_bytes = data
            project.levels.append(level)
            level.append_op(P.EditBspVerticesOp(
                obj_path=exported.obj_path,
                meta_path=exported.meta_path,
            ))

            plan = project.save_plan()
            self.assertEqual(plan.dats[0].stats()["vertex_edits"], 1)
            self.assertEqual(plan.dats[0].stats()["vertex_edit_models"], 1)
            self.assertTrue(any("patch existing world-model records" in warning
                                for warning in plan.dats[0].validation_warnings))
            project.execute(plan)

            output_rez = os.path.join(tmp, "out", plan.batch_id, "data", "WORLDS.REZ")
            with mm9_rezmgr.RezReader(output_rez) as reader:
                changed = reader.extract_to_bytes("WORLDS/BOOTCAMP")

        changed_bsp = bsp.parse(changed)
        changed_model = changed_bsp.model_by_name(source_model.name)
        self.assertEqual(len(changed_bsp.world_models), len(original_bsp.world_models))
        self.assertAlmostEqual(changed_model.points[0][0], source_model.points[0][0] - 10.0, places=3)
        self.assertEqual(len(changed_model.polygons), len(source_model.polygons))
        self.assertEqual(len(self._world_from_bytes(changed).objects), len(original_world.objects))

    def test_topology_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _path, data, bsp_world, _world, _source_model, exported = self.export_model(tmp)
            with open(exported.obj_path, "r", encoding="utf-8") as f:
                lines = [line for line in f.readlines() if not line.startswith("f ")]
            with open(exported.obj_path, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(lines)

            with self.assertRaisesRegex(ValueError, "not found|polygon count changed"):
                vertex_edit.build_vertex_edit_plan(
                    bsp_world,
                    data,
                    exported.obj_path,
                    exported.meta_path,
                )

    def test_project_io_roundtrips_vertex_edit_op(self):
        op = P.EditBspVerticesOp(
            obj_path="C:/tmp/level_geometry.obj",
            meta_path="C:/tmp/level_geometry.datmeta.json",
        )

        restored = project_io.dict_to_op(project_io.op_to_dict(op))

        self.assertIsInstance(restored, P.EditBspVerticesOp)
        self.assertEqual(restored.obj_path, op.obj_path)
        self.assertEqual(restored.meta_path, op.meta_path)

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
