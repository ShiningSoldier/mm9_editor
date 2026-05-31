import os
import sys
import unittest


from tests._path import ROOT  # noqa: F401

from core import bsp
from view3d import gl_mesh


class GlMeshHelperTextureTests(unittest.TestCase):
    def test_firethrough_helper_texture_is_hidden_in_normal_mode(self):
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

        self.assertEqual(len(indices), 0)
        self.assertEqual(verts.shape[0], 0)
        self.assertEqual(ranges, [])

    def test_firethrough_helper_texture_draws_in_helper_mode(self):
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

        verts, indices, ranges = gl_mesh._triangulate_model(
            mesh,
            helper_mode="helpers",
            helper_roles={"collision"},
        )

        self.assertEqual(len(indices), 6)
        self.assertEqual(verts.shape[0], 6)
        self.assertEqual(ranges, [])

    def test_physics_world_ceiling_cap_is_not_rendered(self):
        mesh = bsp.WorldModelMesh(
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
            polygons=[bsp.Polygon([0, 1, 2, 3], surface_index=0, plane_index=0)],
            texture_names=["TEXTURES\\A3Sturmgaard\\terrain\\sturmfordgrass.dtx"],
            surfaces=[
                bsp.Surface(
                    uv_o=(0.0, 0.0, 0.0),
                    uv_p=(1.0, 0.0, 0.0),
                    uv_q=(0.0, 0.0, 1.0),
                    texture_index=0,
                    flags=0,
                    texture_flags=0,
                )
            ],
        )

        verts, indices, ranges = gl_mesh._triangulate_model(mesh)

        self.assertEqual(len(indices), 0)
        self.assertEqual(verts.shape[0], 0)
        self.assertEqual(ranges, [])

    def test_physics_visible_geometry_below_world_top_still_renders(self):
        mesh = bsp.WorldModelMesh(
            name="PhysicsBSP",
            min_box=(-100.0, -50.0, -100.0),
            max_box=(100.0, 100.0, 100.0),
            translation=(0.0, 0.0, 0.0),
            points=[
                (-40.0, 20.0, -40.0),
                (40.0, 20.0, -40.0),
                (40.0, 20.0, 40.0),
                (-40.0, 20.0, 40.0),
            ],
            polygons=[bsp.Polygon([0, 1, 2, 3], surface_index=0, plane_index=0)],
            texture_names=["TEXTURES\\A3Sturmgaard\\terrain\\sturmfordgrass.dtx"],
            surfaces=[
                bsp.Surface(
                    uv_o=(0.0, 0.0, 0.0),
                    uv_p=(1.0, 0.0, 0.0),
                    uv_q=(0.0, 0.0, 1.0),
                    texture_index=0,
                    flags=0,
                    texture_flags=0,
                )
            ],
        )

        verts, indices, ranges = gl_mesh._triangulate_model(mesh)

        self.assertEqual(len(indices), 6)
        self.assertEqual(verts.shape[0], 6)


if __name__ == "__main__":
    unittest.main()
