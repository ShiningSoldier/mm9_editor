import os
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from core import bsp
from core import project as P
from core import project_io
from core import rezmgr as mm9_rezmgr
from features.dat_editing import export_roundtrip, replace_submodel
from mm9_patcher.mm9_patch import World
from tests.core_tests.test_game_resources import write_minimal_rez


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class ReplaceSubmodelTests(unittest.TestCase):
    def load_bootcamp(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            data = f.read()
        return path, data, bsp.parse(data), World.load(path)

    def export_replaceable_model(self, tmp):
        path, data, bsp_world, world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models
                      if m.polygons
                      and len(m.points) >= 3
                      and not m.is_skybox()
                      and str(m.name or "").lower() not in {"physicsbsp", "visbsp"}), None)
        if model is None:
            self.skipTest("BOOTCAMP has no replaceable BSP model")
        result = export_roundtrip.export_roundtrip(
            bsp_world,
            data,
            tmp,
            source_path=path,
            base_name="ReplaceSubmodelSource",
            selected_model_names=[model.name],
        )
        return path, data, bsp_world, world, model, result

    def remove_first_face(self, obj_path):
        with open(obj_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        removed = False
        edited = []
        for line in lines:
            if not removed and line.startswith("f "):
                removed = True
                continue
            edited.append(line)
        if not removed:
            self.skipTest("exported OBJ has no face to remove")
        with open(obj_path, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(edited)

    def test_replace_plan_accepts_topology_change_and_updates_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            _path, data, bsp_world, _world, source_model, exported = self.export_replaceable_model(tmp)
            self.remove_first_face(exported.obj_path)

            plan = replace_submodel.build_replace_submodel_plan(
                bsp_world,
                data,
                exported.obj_path,
                exported.meta_path,
            )

            self.assertEqual(len(plan.models), 1)
            replaced = plan.models[0].replacement_model
            self.assertEqual(replaced.name, source_model.name)
            self.assertEqual(len(replaced.polygons), len(source_model.polygons) - 1)
            preview = replace_submodel.build_preview_bsp(bsp_world, [plan])
            self.assertEqual(
                len(preview.model_by_name(source_model.name).polygons),
                len(source_model.polygons) - 1,
            )

    def test_project_save_replaces_existing_bsp_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            _path, data, original_bsp, original_world, source_model, exported = self.export_replaceable_model(tmp)
            self.remove_first_face(exported.obj_path)
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
            level.append_op(P.ReplaceBspSubmodelOp(
                obj_path=exported.obj_path,
                meta_path=exported.meta_path,
            ))

            plan = project.save_plan()
            self.assertEqual(plan.dats[0].stats()["submodel_replacements"], 1)
            self.assertEqual(plan.dats[0].stats()["replaced_bsp_models"], 1)
            self.assertTrue(any("submodel replacements rebuild" in warning
                                for warning in plan.dats[0].validation_warnings))
            project.execute(plan)

            output_rez = os.path.join(tmp, "out", plan.batch_id, "data", "WORLDS.REZ")
            with mm9_rezmgr.RezReader(output_rez) as reader:
                changed = reader.extract_to_bytes("WORLDS/BOOTCAMP")

        changed_bsp = bsp.parse(changed)
        changed_model = changed_bsp.model_by_name(source_model.name)
        self.assertEqual(len(changed_bsp.world_models), len(original_bsp.world_models))
        self.assertEqual(len(changed_model.polygons), len(source_model.polygons) - 1)
        self.assertEqual(len(self._world_from_bytes(changed).objects), len(original_world.objects))

    def test_system_models_are_rejected(self):
        path, data, bsp_world, _world = self.load_bootcamp()
        system_model = next((m for m in bsp_world.world_models
                             if str(m.name or "").lower() in {"physicsbsp", "visbsp"}), None)
        if system_model is None:
            self.skipTest("BOOTCAMP has no system BSP model")
        with tempfile.TemporaryDirectory() as tmp:
            exported = export_roundtrip.export_roundtrip(
                bsp_world,
                data,
                tmp,
                source_path=path,
                base_name="BlockedSubmodel",
                selected_model_names=[system_model.name],
            )
            with self.assertRaisesRegex(ValueError, "cannot be replaced"):
                replace_submodel.build_replace_submodel_plan(
                    bsp_world,
                    data,
                    exported.obj_path,
                    exported.meta_path,
                )

    def test_project_io_roundtrips_replace_submodel_op(self):
        op = P.ReplaceBspSubmodelOp(
            obj_path="C:/tmp/level_geometry.obj",
            meta_path="C:/tmp/level_geometry.datmeta.json",
        )

        restored = project_io.dict_to_op(project_io.op_to_dict(op))

        self.assertIsInstance(restored, P.ReplaceBspSubmodelOp)
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
