import os
import sys
import unittest


from tests._path import ROOT  # noqa: F401

from core import bsp
from view3d import gl_mesh


class _FakeCache:
    def __init__(self):
        self.uploaded = []

    def get_or_upload(self, model, tex_cache=None):
        self.uploaded.append(model.name)
        return gl_mesh.GpuMesh(
            vao=0,
            vbo=0,
            ibo=0,
            index_count=6,
            vertex_count=6,
            triangle_count=2,
            dropped_polys=0,
            category=model.category(),
            model_name=model.name,
            tex_ranges=[],
            tri_positions=None,
        )


def _mesh(name: str, texture: str = "") -> bsp.WorldModelMesh:
    return bsp.WorldModelMesh(
        name=name,
        min_box=(0.0, 0.0, 0.0),
        max_box=(8.0, 8.0, 8.0),
        translation=(0.0, 0.0, 0.0),
        points=[(0.0, 0.0, 0.0), (8.0, 0.0, 0.0), (0.0, 8.0, 0.0)],
        polygons=[bsp.Polygon([0, 1, 2], 0, 0)],
        texture_names=[texture] if texture else [],
    )


class GlMeshHelperBspModeTests(unittest.TestCase):
    def test_hidden_mode_skips_collision_helpers(self):
        world = bsp.BspWorld(
            version=66,
            world_info="",
            world_models=[
                _mesh("TerrainBig"),
                _mesh("Fence_Collision", "TEXTURES\\LevelTextures\\Misc\\Firethrough.dtx"),
            ],
        )
        cache = _FakeCache()

        batch = gl_mesh.build_bsp_draw_batch(world, cache, helper_bsp_mode="hidden")

        self.assertEqual([item.mesh.model_name for item in batch.items], ["TerrainBig"])
        self.assertEqual(cache.uploaded, ["TerrainBig"])

    def test_wireframe_mode_marks_helper_items(self):
        world = bsp.BspWorld(
            version=66,
            world_info="",
            world_models=[_mesh("Fence_Collision", "TEXTURES\\LevelTextures\\Misc\\Firethrough.dtx")],
        )

        batch = gl_mesh.build_bsp_draw_batch(world, _FakeCache(), helper_bsp_mode="wireframe")

        self.assertEqual(len(batch.items), 1)
        self.assertTrue(batch.items[0].wireframe)
        self.assertLess(batch.items[0].alpha, 1.0)

    def test_physics_and_vis_bsp_are_not_helpers(self):
        # Even if they use helper textures like Firethrough.dtx, PhysicsBSP and VisBSP
        # are system meshes and must not be treated as helpers.
        world = bsp.BspWorld(
            version=66,
            world_info="",
            world_models=[
                _mesh("PhysicsBSP", "TEXTURES\\LevelTextures\\Misc\\Firethrough.dtx"),
                _mesh("VisBSP", "TEXTURES\\LevelTextures\\Misc\\Firethrough.dtx"),
            ],
        )

        batch = gl_mesh.build_bsp_draw_batch(world, _FakeCache(), helper_bsp_mode="solid")

        self.assertEqual(len(batch.items), 2)
        for item in batch.items:
            self.assertEqual(item.alpha, 1.0)
            self.assertNotEqual(item.color, (0.95, 0.18, 0.62))  # helper tint


if __name__ == "__main__":
    unittest.main()

