import json
import os
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from core import bsp
from features.dat_editing import export_roundtrip


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class RoundTripExportTests(unittest.TestCase):
    def load_bootcamp(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            data = f.read()
        return path, data, bsp.parse(data)

    def test_exports_obj_mtl_and_sidecar_for_selected_model(self):
        path, data, bsp_world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models if m.polygons and m.texture_names), None)
        if model is None:
            self.skipTest("BOOTCAMP has no textured BSP model")

        with tempfile.TemporaryDirectory() as tmp:
            result = export_roundtrip.export_roundtrip(
                bsp_world,
                data,
                tmp,
                source_path=path,
                base_name="BootcampTest",
                selected_model_names=[model.name],
            )

            self.assertTrue(os.path.isfile(result.obj_path))
            self.assertTrue(os.path.isfile(result.mtl_path))
            self.assertTrue(os.path.isfile(result.meta_path))
            self.assertEqual(result.model_count, 1)

            with open(result.meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            self.assertEqual(meta["version"], 1)
            self.assertEqual(meta["kind"], "mm9_dat_geometry_roundtrip")
            self.assertEqual(meta["source"]["dat_version"], 66)
            self.assertEqual(meta["source"]["size"], len(data))
            self.assertEqual(len(meta["source"]["sha256"]), 64)
            self.assertEqual(meta["coordinate_system"]["export_space"], "blender_display")
            self.assertEqual(meta["coordinate_system"]["dat_to_export_matrix"][0][0], -1.0)
            self.assertEqual(meta["models"][0]["name"], model.name)
            self.assertEqual(meta["models"][0]["raw_start"], model.raw_start)
            self.assertEqual(meta["models"][0]["polygon_count"], len(model.polygons))
            self.assertEqual(meta["models"][0]["texture_names"], model.texture_names)
            self.assertTrue(meta["models"][0]["polygons"])
            self.assertTrue(meta["materials"])

            with open(result.obj_path, "r", encoding="utf-8") as f:
                obj_text = f.read()
            self.assertIn("mtllib BootcampTest_geometry.mtl", obj_text)
            self.assertIn(f"o {export_roundtrip._obj_name(model.name, 0)}", obj_text)
            self.assertIn("usemtl ", obj_text)
            self.assertIn("\nf ", obj_text)
            self.assertIn("\nvt ", obj_text)

            first_point = model.points[0]
            self.assertIn(f"v {-first_point[0]:.6f} {first_point[1]:.6f} {first_point[2]:.6f}", obj_text)

            with open(result.mtl_path, "r", encoding="utf-8") as f:
                mtl_text = f.read()
            self.assertIn("# mm9_texture ", mtl_text)

    def test_raw_coordinate_export_uses_identity_transform(self):
        path, data, bsp_world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models if m.points), None)
        if model is None:
            self.skipTest("BOOTCAMP has no BSP points")

        with tempfile.TemporaryDirectory() as tmp:
            result = export_roundtrip.export_roundtrip(
                bsp_world,
                data,
                tmp,
                source_path=path,
                base_name="RawTest",
                selected_model_names=[model.name],
                raw_coordinates=True,
            )

            with open(result.meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            self.assertEqual(meta["coordinate_system"]["export_space"], "raw_dat")
            self.assertEqual(meta["coordinate_system"]["dat_to_export_matrix"][0][0], 1.0)

            with open(result.obj_path, "r", encoding="utf-8") as f:
                obj_text = f.read()
            first_point = model.points[0]
            self.assertIn(f"v {first_point[0]:.6f} {first_point[1]:.6f} {first_point[2]:.6f}", obj_text)

    def test_default_export_omits_sky_visibility_and_physics_ceiling_cap(self):
        visible = bsp.WorldModelMesh(
            name="VisibleRoom",
            min_box=(0.0, 0.0, 0.0),
            max_box=(10.0, 0.0, 10.0),
            translation=(0.0, 0.0, 0.0),
            points=[(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 0.0, 10.0), (0.0, 0.0, 10.0)],
            polygons=[bsp.Polygon([0, 1, 2, 3], 0, 0)],
            texture_names=["TEXTURES\\World\\Floor.dtx"],
            surfaces=[bsp.Surface((0, 0, 0), (1, 0, 0), (0, 0, 1), 0, 0, 0)],
        )
        sky = bsp.WorldModelMesh(
            name="Skybox_Test",
            min_box=(0.0, 0.0, 0.0),
            max_box=(1.0, 1.0, 1.0),
            translation=(0.0, 0.0, 0.0),
            points=visible.points,
            polygons=visible.polygons,
            texture_names=visible.texture_names,
            surfaces=visible.surfaces,
        )
        vis = bsp.WorldModelMesh(
            name="VisBSP",
            min_box=(0.0, 0.0, 0.0),
            max_box=(1.0, 1.0, 1.0),
            translation=(0.0, 0.0, 0.0),
            points=visible.points,
            polygons=visible.polygons,
            texture_names=["TEXTURES\\Skybox\\SkyMarker.dtx"],
            surfaces=visible.surfaces,
        )
        physics = bsp.WorldModelMesh(
            name="PhysicsBSP",
            min_box=(-100.0, -50.0, -100.0),
            max_box=(100.0, 100.0, 100.0),
            translation=(0.0, 0.0, 0.0),
            points=[
                (-100.0, 100.0, -100.0),
                (100.0, 100.0, -100.0),
                (100.0, 100.0, 100.0),
                (-100.0, 100.0, 100.0),
            ],
            polygons=[bsp.Polygon([0, 1, 2, 3], 0, 0)],
            texture_names=["TEXTURES\\World\\CeilingCap.dtx"],
            surfaces=[bsp.Surface((0, 0, 0), (1, 0, 0), (0, 0, 1), 0, 0, 0)],
        )
        world = bsp.BspWorld(
            version=66,
            world_info="test",
            world_models=[visible, sky, vis, physics],
        )

        with tempfile.TemporaryDirectory() as tmp:
            result = export_roundtrip.export_roundtrip(world, b"dat", tmp, base_name="Filtered")
            with open(result.obj_path, "r", encoding="utf-8") as f:
                obj_text = f.read()
            with open(result.meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

        self.assertIn("o VisibleRoom", obj_text)
        self.assertNotIn("Skybox_Test", obj_text)
        self.assertNotIn("VisBSP", obj_text)
        self.assertIn("o PhysicsBSP", obj_text)
        self.assertEqual(obj_text.count("\nf "), 1)
        self.assertFalse(meta["export_options"]["include_helper_geometry"])


if __name__ == "__main__":
    unittest.main()
