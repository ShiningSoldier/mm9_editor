import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import bsp
from view3d import gl_mesh


class GlMeshHelperTextureTests(unittest.TestCase):
    def test_firethrough_helper_texture_draws_as_solid_geometry(self):
        mesh = bsp.WorldModelMesh(
            name="InvisibleBrushTest",
            min_box=(0.0, 0.0, 0.0),
            max_box=(8.0, 8.0, 8.0),
            translation=(0.0, 0.0, 0.0),
            points=[
                (0.0, 0.0, 0.0),
                (8.0, 0.0, 0.0),
                (8.0, 8.0, 0.0),
                (0.0, 8.0, 0.0),
            ],
            polygons=[bsp.Polygon([0, 1, 2, 3], surface_index=0, plane_index=0)],
            texture_names=["TEXTURES\\LevelTextures\\Misc\\Firethrough.dtx"],
            surfaces=[
                bsp.Surface(
                    uv_o=(0.0, 0.0, 0.0),
                    uv_p=(1.0, 0.0, 0.0),
                    uv_q=(0.0, 1.0, 0.0),
                    texture_index=0,
                    flags=0,
                    texture_flags=0,
                )
            ],
        )

        verts, indices, ranges = gl_mesh._triangulate_model(mesh)

        self.assertEqual(len(indices), 6)
        self.assertEqual(verts.shape[0], 6)
        self.assertEqual(ranges, [])


if __name__ == "__main__":
    unittest.main()
