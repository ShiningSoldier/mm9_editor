import inspect
import json
import os
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from core import bsp
from features.dat_editing import gltf_export


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class GltfExportTests(unittest.TestCase):
    def test_gltf_export_uses_neutral_dat_inspection_helpers(self):
        source = inspect.getsource(gltf_export)

        self.assertIn("geometry_export_common", source)
        self.assertNotIn("import export_roundtrip", source)

    def load_bootcamp(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            data = f.read()
        return path, data, bsp.parse(data)

    def test_exports_gltf_bin_and_embedded_dat_metadata(self):
        path, data, bsp_world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models if m.polygons and m.texture_names), None)
        if model is None:
            self.skipTest("BOOTCAMP has no textured BSP model")

        with tempfile.TemporaryDirectory() as tmp:
            result = gltf_export.export_gltf_inspection(
                bsp_world,
                data,
                tmp,
                source_path=path,
                base_name="BootcampGltfTest",
                selected_model_names=[model.name],
            )

            self.assertTrue(os.path.isfile(result.gltf_path))
            self.assertTrue(os.path.isfile(result.bin_path))
            self.assertTrue(os.path.isfile(result.meta_path))
            self.assertEqual(result.model_count, 1)
            self.assertGreater(result.triangle_count, 0)

            with open(result.gltf_path, "r", encoding="utf-8") as f:
                gltf = json.load(f)
            self.assertEqual(gltf["asset"]["version"], "2.0")
            self.assertEqual(gltf["buffers"][0]["uri"], "BootcampGltfTest_geometry.bin")
            self.assertEqual(gltf["buffers"][0]["byteLength"], os.path.getsize(result.bin_path))
            self.assertEqual(len(gltf["nodes"]), 1)
            self.assertEqual(len(gltf["meshes"]), 1)
            self.assertTrue(gltf["meshes"][0]["primitives"])
            primitive = gltf["meshes"][0]["primitives"][0]
            self.assertIn("POSITION", primitive["attributes"])
            self.assertIn("TEXCOORD_0", primitive["attributes"])
            self.assertIn("MM9_polygon_indices", primitive["extras"])

            meta = gltf["extras"]["MM9_datmeta"]
            self.assertEqual(meta["kind"], "mm9_dat_geometry_inspection")
            self.assertEqual(meta["format"], "gltf")
            self.assertEqual(meta["source"]["size"], len(data))
            self.assertEqual(meta["models"][0]["name"], model.name)
            self.assertEqual(meta["coordinate_system"]["export_space"], "editor_display")

            with open(result.meta_path, "r", encoding="utf-8") as f:
                sidecar = json.load(f)
            self.assertEqual(sidecar["files"]["gltf"], os.path.basename(result.gltf_path))
            self.assertEqual(sidecar["models"][0]["name"], model.name)

    def test_raw_coordinate_gltf_export_uses_identity_transform(self):
        path, data, bsp_world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models if m.points), None)
        if model is None:
            self.skipTest("BOOTCAMP has no BSP model")

        with tempfile.TemporaryDirectory() as tmp:
            result = gltf_export.export_gltf_inspection(
                bsp_world,
                data,
                tmp,
                source_path=path,
                base_name="RawGltf",
                selected_model_names=[model.name],
                raw_coordinates=True,
            )

            with open(result.gltf_path, "r", encoding="utf-8") as f:
                gltf = json.load(f)
            meta = gltf["extras"]["MM9_datmeta"]
            self.assertEqual(meta["coordinate_system"]["export_space"], "raw_dat")
            self.assertEqual(meta["coordinate_system"]["dat_to_export_matrix"][0][0], 1.0)

    def test_full_level_export_omits_empty_mesh_primitives(self):
        path, data, bsp_world = self.load_bootcamp()

        with tempfile.TemporaryDirectory() as tmp:
            result = gltf_export.export_gltf_inspection(
                bsp_world,
                data,
                tmp,
                source_path=path,
                base_name="FullLevelGltf",
            )

            with open(result.gltf_path, "r", encoding="utf-8") as f:
                gltf = json.load(f)
            self.assertEqual(result.model_count, len(gltf["meshes"]))
            self.assertEqual(len(gltf["nodes"]), len(gltf["meshes"]))
            self.assertGreater(len(gltf["meshes"]), 0)
            for mesh in gltf["meshes"]:
                self.assertTrue(mesh["primitives"])


if __name__ == "__main__":
    unittest.main()
