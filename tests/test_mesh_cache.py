import os
import sys
import unittest
from unittest import mock


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from view3d.gl_mesh import MeshCache


class MeshCacheTests(unittest.TestCase):
    def test_retain_models_prunes_only_removed_mesh_ids(self):
        keep = object()
        drop = object()
        cache = MeshCache()
        kept_gpu = object()
        dropped_gpu = object()
        cache._cache[(id(keep), None)] = kept_gpu
        cache._cache[(id(drop), None)] = dropped_gpu

        with mock.patch("view3d.gl_mesh.delete_mesh") as delete_mesh:
            cache.retain_models([keep])

        self.assertIn((id(keep), None), cache._cache)
        self.assertNotIn((id(drop), None), cache._cache)
        delete_mesh.assert_called_once_with(dropped_gpu)


if __name__ == "__main__":
    unittest.main()
