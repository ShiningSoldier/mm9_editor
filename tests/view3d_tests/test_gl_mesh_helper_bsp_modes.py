import os
import sys
import types
import unittest
from unittest import mock


from tests._path import ROOT  # noqa: F401

from core import bsp
from view3d.dtx import TextureAlphaInfo
from view3d import gl_mesh


class _FakeCache:
    def __init__(self):
        self.uploaded = []

    def get_or_upload(
        self,
        model,
        tex_cache=None,
        helper_mode="normal",
        helper_roles=None,
        model_helper_role=None,
    ):
        self.uploaded.append((model.name, helper_mode, tuple(sorted(helper_roles or ()))))
        verts, indices, ranges = gl_mesh._triangulate_model(
            model,
            tex_cache=tex_cache,
            helper_mode=helper_mode,
            helper_roles=set(helper_roles or ()),
            model_helper_role=model_helper_role,
        )
        if len(indices) == 0:
            return None
        return gl_mesh.GpuMesh(
            vao=0,
            vbo=0,
            ibo=0,
            index_count=len(indices),
            vertex_count=verts.shape[0],
            triangle_count=len(indices) // 3,
            dropped_polys=0,
            category=model.category(),
            model_name=model.name,
            tex_ranges=ranges,
            helper_role=next(iter(helper_roles), None) if helper_mode == "helpers" and helper_roles else None,
            tri_positions=None,
        )


class _FakeTexCache:
    def __init__(self, alpha_info=None):
        self._alpha_info = alpha_info

    def get(self, _name):
        return 7

    def alpha_info(self, _name):
        return self._alpha_info


class _FakeProgram:
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def set_mat4(self, *_args):
        pass

    def set_vec3(self, *_args):
        pass

    def set_float(self, *_args):
        pass

    def set_int(self, *_args):
        pass


class _FakeGL:
    GL_FALSE = 0
    GL_TRUE = 1
    GL_TEXTURE0 = 0
    GL_TEXTURE_2D = 0
    GL_TRIANGLES = 0
    GL_UNSIGNED_INT = 0
    draw_calls = 0

    @classmethod
    def glBindVertexArray(cls, *_args):
        pass

    @classmethod
    def glActiveTexture(cls, *_args):
        pass

    @classmethod
    def glBindTexture(cls, *_args):
        pass

    @classmethod
    def glDepthMask(cls, *_args):
        pass

    @classmethod
    def glDrawElements(cls, *_args):
        cls.draw_calls += 1


def _mesh(name: str, texture: str = "") -> bsp.WorldModelMesh:
    surfaces = []
    if texture:
        surfaces = [
            bsp.Surface(
                uv_o=(0.0, 0.0, 0.0),
                uv_p=(1.0, 0.0, 0.0),
                uv_q=(0.0, 1.0, 0.0),
                texture_index=0,
                flags=0,
                texture_flags=0,
            )
        ]
    return bsp.WorldModelMesh(
        name=name,
        min_box=(0.0, 0.0, 0.0),
        max_box=(8.0, 8.0, 8.0),
        translation=(0.0, 0.0, 0.0),
        points=[(0.0, 0.0, 0.0), (8.0, 0.0, 0.0), (0.0, 8.0, 0.0)],
        polygons=[bsp.Polygon([0, 1, 2], 0, 0)],
        texture_names=[texture] if texture else [],
        surfaces=surfaces,
    )


class GlMeshHelperBspModeTests(unittest.TestCase):
    def test_normal_mode_skips_collision_helpers(self):
        world = bsp.BspWorld(
            version=66,
            world_info="",
            world_models=[
                _mesh("TerrainBig"),
                _mesh("Fence_Collision", "TEXTURES\\LevelTextures\\Misc\\Firethrough.dtx"),
            ],
        )
        cache = _FakeCache()

        batch = gl_mesh.build_bsp_draw_batch(world, cache, helper_bsp_mode="normal")

        self.assertEqual([item.mesh.model_name for item in batch.items], ["TerrainBig"])
        self.assertEqual([name for name, _mode, _roles in cache.uploaded], ["TerrainBig", "Fence_Collision"])

    def test_helpers_mode_marks_helper_items_by_role(self):
        world = bsp.BspWorld(
            version=66,
            world_info="",
            world_models=[_mesh("Fence_Collision", "TEXTURES\\LevelTextures\\Misc\\Firethrough.dtx")],
        )

        batch = gl_mesh.build_bsp_draw_batch(
            world,
            _FakeCache(),
            helper_bsp_mode="helpers",
            helper_role_groups={"collision"},
        )

        self.assertEqual(len(batch.items), 1)
        self.assertEqual(batch.items[0].mesh.helper_role, "collision")
        self.assertFalse(batch.items[0].wireframe)
        self.assertLess(batch.items[0].alpha, 1.0)

    def test_named_hidden_helper_model_uses_existing_collision_group(self):
        world = bsp.BspWorld(
            version=66,
            world_info="",
            world_models=[_mesh("AIBarrier51", "Default")],
        )

        normal = gl_mesh.build_bsp_draw_batch(
            world,
            _FakeCache(),
            helper_bsp_mode="normal",
            hidden_helper_model_names={"aibarrier51"},
        )
        helpers = gl_mesh.build_bsp_draw_batch(
            world,
            _FakeCache(),
            helper_bsp_mode="helpers",
            helper_role_groups={"collision"},
            hidden_helper_model_names={"aibarrier51"},
        )

        self.assertEqual(normal.items, [])
        self.assertEqual(len(helpers.items), 1)
        self.assertEqual(helpers.items[0].mesh.helper_role, "collision")

    def test_vis_bsp_is_hidden_outside_raw_mode(self):
        # VisBSP is visibility/PVS data, while PhysicsBSP contains some visible
        # architecture in shipped MM9 worlds.
        world = bsp.BspWorld(
            version=66,
            world_info="",
            world_models=[
                _mesh("Terrain0"),
                _mesh("PhysicsBSP", "TEXTURES\\LevelTextures\\Misc\\Firethrough.dtx"),
                _mesh("VisBSP", "TEXTURES\\LevelTextures\\Misc\\Firethrough.dtx"),
            ],
        )

        batch = gl_mesh.build_bsp_draw_batch(world, _FakeCache(), helper_bsp_mode="normal")

        self.assertEqual(
            [item.mesh.model_name for item in batch.items],
            ["Terrain0"],
        )

    def test_helpers_mode_can_show_vis_bsp_for_debugging(self):
        world = bsp.BspWorld(
            version=66,
            world_info="",
            world_models=[
                _mesh("VisBSP", "TEXTURES\\Skybox\\SkyMarker.dtx"),
            ],
        )

        batch = gl_mesh.build_bsp_draw_batch(
            world,
            _FakeCache(),
            helper_bsp_mode="helpers",
            helper_role_groups={"skyVisibility"},
        )

        self.assertEqual(
            [item.mesh.model_name for item in batch.items],
            ["VisBSP"],
        )
        for item in batch.items:
            self.assertEqual(item.mesh.helper_role, "skyVisibility")
            self.assertLess(item.alpha, 1.0)

    def test_bsp_window_alpha_range_is_marked_blended(self):
        world = bsp.BspWorld(
            version=66,
            world_info="",
            world_models=[
                _mesh("PhysicsBSP", "TEXTURES\\A4Drangheim\\misc\\drangheimcityglass.dtx"),
            ],
        )
        alpha = TextureAlphaInfo(
            pixel_format=6,
            width=128,
            height=128,
            min_alpha=0,
            max_alpha=255,
            transparent_fraction=0.19,
            mid_fraction=0.43,
            nonopaque_fraction=0.62,
        )

        batch = gl_mesh.build_bsp_draw_batch(
            world,
            _FakeCache(),
            tex_cache=_FakeTexCache(alpha),
            helper_bsp_mode="normal",
        )

        self.assertEqual(len(batch.items), 1)
        self.assertEqual([r.alpha_mode for r in batch.items[0].ranges], ["blend"])

    def test_blended_glass_draws_only_in_translucent_bsp_pass(self):
        alpha = TextureAlphaInfo(
            pixel_format=6,
            width=128,
            height=128,
            min_alpha=35,
            max_alpha=255,
            transparent_fraction=0.0,
            mid_fraction=0.53,
            nonopaque_fraction=0.53,
        )
        batch = gl_mesh.build_bsp_draw_batch(
            bsp.BspWorld(
                version=66,
                world_info="",
                world_models=[
                    _mesh(
                        "WorldObject0",
                        r"TEXTURES\LevelTextures\Windows\StainedGlass2.dtx",
                    ),
                ],
            ),
            _FakeCache(),
            tex_cache=_FakeTexCache(alpha),
        )
        fake_module = types.SimpleNamespace(GL=_FakeGL)
        _FakeGL.draw_calls = 0

        with mock.patch.dict(sys.modules, {"OpenGL": fake_module}):
            gl_mesh.draw_bsp_batch(
                batch,
                _FakeProgram(),
                None,
                render_pass="opaque",
            )
            self.assertEqual(_FakeGL.draw_calls, 0)

            gl_mesh.draw_bsp_batch(
                batch,
                _FakeProgram(),
                None,
                render_pass="translucent",
            )
            self.assertEqual(_FakeGL.draw_calls, 1)


if __name__ == "__main__":
    unittest.main()
