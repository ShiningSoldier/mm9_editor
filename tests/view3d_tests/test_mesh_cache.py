import os
import sys
import unittest
from unittest import mock


from tests._path import ROOT  # noqa: F401

from view3d.gl_mesh import MeshCache


class MeshCacheTests(unittest.TestCase):
    def test_discard_model_prunes_all_upload_variants(self):
        target = object()
        other = object()
        cache = MeshCache()
        target_gpu_a = object()
        target_gpu_b = object()
        other_gpu = object()
        cache._cache[(id(target), None, "normal")] = target_gpu_a
        cache._cache[(id(target), 7, "helpers")] = target_gpu_b
        cache._cache[(id(other), None, "normal")] = other_gpu

        with mock.patch("view3d.gl_mesh.delete_mesh") as delete_mesh:
            cache.discard_model(target)

        self.assertEqual(list(cache._cache.values()), [other_gpu])
        self.assertEqual(
            {call.args[0] for call in delete_mesh.call_args_list},
            {target_gpu_a, target_gpu_b},
        )

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
