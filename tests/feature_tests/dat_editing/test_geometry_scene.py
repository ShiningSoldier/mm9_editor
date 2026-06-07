import os
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from features.dat_editing import mesh_import


class GeometrySceneTests(unittest.TestCase):
    def test_obj_loader_produces_format_neutral_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj_path = os.path.join(tmp, "scene.obj")
            with open(obj_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(
                    "o FirstModel\n"
                    "v 0 0 0\n"
                    "v 1 0 0\n"
                    "v 1 0 1\n"
                    "vt 0 0\n"
                    "vt 1 0\n"
                    "vt 1 1\n"
                    "usemtl Floor\n"
                    "f 1/1 2/2 3/3\n"
                    "o SecondModel\n"
                    "v 0 1 0\n"
                    "v 1 1 0\n"
                    "v 1 1 1\n"
                    "usemtl Wall\n"
                    "f 4 5 6\n"
                )

            scene = mesh_import.load_obj_geometry_scene(
                obj_path,
                {
                    "materials": [
                        {"material_name": "Floor", "texture_name": "TEXTURES\\World\\Floor.dtx"},
                        {"material_name": "Wall", "texture_name": "TEXTURES\\World\\Wall.dtx"},
                    ],
                },
            )

            self.assertEqual(scene.source_path, os.path.abspath(obj_path))
            self.assertEqual([model.name for model in scene.mesh_models()], ["FirstModel", "SecondModel"])
            self.assertEqual(scene.material_texture_map()["Floor"], "TEXTURES\\World\\Floor.dtx")
            self.assertEqual(scene.models[0].faces[0].material_name, "Floor")
            self.assertEqual(scene.models[0].faces[0].uv_coords[1], (1.0, 0.0))
            self.assertEqual(scene.models[1].faces[0].uv_coords, [None, None, None])


if __name__ == "__main__":
    unittest.main()
