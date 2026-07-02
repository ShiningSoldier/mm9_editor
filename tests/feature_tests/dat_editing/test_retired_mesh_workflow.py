import importlib.util
import unittest

from tests._path import ROOT  # noqa: F401

from core import project as P
from core import project_io


class RetiredMeshWorkflowTests(unittest.TestCase):
    def test_project_io_rejects_retired_operation_kinds(self):
        for kind in (
            "import_mesh_bsp",
            "edit_bsp_vertices",
            "edit_terrain_vertices",
            "replace_bsp_submodel",
        ):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(ValueError, "retired editable mesh-sidecar"):
                    project_io.dict_to_op({"op": kind})

    def test_retired_operation_classes_are_removed_from_project_model(self):
        for name in (
            "ImportMeshBspOp",
            "EditBspVerticesOp",
            "EditTerrainVerticesOp",
            "ReplaceBspSubmodelOp",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(P, name))

    def test_retired_sidecar_modules_are_removed(self):
        for name in (
            "features.dat_editing.bsp_edit_plan",
            "features.dat_editing.bsp_record_patch",
            "features.dat_editing.export_roundtrip",
            "features.dat_editing.gltf_import",
            "features.dat_editing.mesh_import",
            "features.dat_editing.obj_workflow",
            "features.dat_editing.replace_submodel",
            "features.dat_editing.terrain_vertex",
            "features.dat_editing.vertex_edit",
        ):
            with self.subTest(name=name):
                self.assertIsNone(importlib.util.find_spec(name))


if __name__ == "__main__":
    unittest.main()
