import os
import tempfile
import unittest

import numpy as np

from tests._path import ROOT  # noqa: F401

from core import bsp
from view3d import gl_mesh


class _TextureSizeCache:
    def __init__(self, size):
        self.size = size

    def image_size(self, _name):
        return self.size


def _mesh(texture="TEXTURES\\Example\\stone.dtx"):
    return bsp.WorldModelMesh(
        name="MainWorld",
        min_box=(0.0, 0.0, 0.0),
        max_box=(8.0, 8.0, 0.0),
        translation=(0.0, 0.0, 0.0),
        points=[
            (0.0, 0.0, 0.0),
            (8.0, 0.0, 0.0),
            (8.0, 8.0, 0.0),
            (0.0, 8.0, 0.0),
        ],
        polygons=[bsp.Polygon([0, 1, 2, 3], surface_index=0, plane_index=0)],
        texture_names=[texture],
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


class BspTriangulationCacheTests(unittest.TestCase):
    def test_cache_key_includes_texture_size(self):
        mesh = _mesh()
        key_128 = gl_mesh.bsp_triangulation_cache_key(
            mesh,
            tex_cache=_TextureSizeCache((128, 128)),
        )
        key_256 = gl_mesh.bsp_triangulation_cache_key(
            mesh,
            tex_cache=_TextureSizeCache((256, 256)),
        )

        self.assertNotEqual(key_128, key_256)

    def test_cached_triangulation_roundtrip(self):
        mesh = _mesh()
        with tempfile.TemporaryDirectory() as temp_dir:
            verts1, indices1, ranges1 = gl_mesh.triangulate_model_cached(
                mesh,
                tex_cache=_TextureSizeCache((128, 128)),
                cache_dir=temp_dir,
            )
            verts2, indices2, ranges2 = gl_mesh.triangulate_model_cached(
                mesh,
                tex_cache=_TextureSizeCache((128, 128)),
                cache_dir=temp_dir,
            )

            self.assertEqual(len(indices1), 6)
            self.assertTrue(any(name.endswith(".npz") for _root, _dirs, files in os.walk(temp_dir) for name in files))
            np.testing.assert_array_equal(verts1, verts2)
            np.testing.assert_array_equal(indices1, indices2)
            self.assertEqual(ranges1, ranges2)

    def test_helper_mode_has_distinct_cache_key(self):
        mesh = _mesh("TEXTURES\\LevelTextures\\Misc\\Firethrough.dtx")
        normal_key = gl_mesh.bsp_triangulation_cache_key(mesh, helper_mode="normal")
        helper_key = gl_mesh.bsp_triangulation_cache_key(
            mesh,
            helper_mode="helpers",
            helper_roles={"collision"},
        )

        self.assertNotEqual(normal_key, helper_key)

    def test_default_cache_dir_lives_under_project_cache(self):
        cache_dir = gl_mesh._default_bsp_cache_dir()
        self.assertTrue(cache_dir.endswith(os.path.join("cache", "view3d_bsp")))
        self.assertIn("mm9_editor", cache_dir.replace("\\", "/"))


if __name__ == "__main__":
    unittest.main()
