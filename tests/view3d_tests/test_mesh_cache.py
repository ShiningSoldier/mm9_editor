import os
import sys
import unittest
from unittest import mock


from tests._path import ROOT  # noqa: F401

from view3d.gl_mesh import MeshCache


class MeshCacheTests(unittest.TestCase):
    def test_activate_level_keeps_two_level_lru_for_warm_switches(self):
        level_a, level_b, level_c = object(), object(), object()
        model_a, model_b, model_c = object(), object(), object()
        gpu_a, gpu_b, gpu_c = object(), object(), object()
        cache = MeshCache()

        cache.activate_level(level_a, [model_a])
        cache._cache[(id(model_a), None, "normal")] = gpu_a
        cache.activate_level(level_b, [model_b])
        cache._cache[(id(model_b), None, "normal")] = gpu_b

        with mock.patch("view3d.gl_mesh.delete_mesh") as delete_mesh:
            cache.activate_level(level_a, [model_a])
            delete_mesh.assert_not_called()

            cache.activate_level(level_c, [model_c])
            cache._cache[(id(model_c), None, "normal")] = gpu_c

        self.assertIn((id(model_a), None, "normal"), cache._cache)
        self.assertNotIn((id(model_b), None, "normal"), cache._cache)
        self.assertIn((id(model_c), None, "normal"), cache._cache)
        delete_mesh.assert_called_once_with(gpu_b)

    def test_activate_level_prunes_removed_preview_models(self):
        level = object()
        keep, removed = object(), object()
        kept_gpu, removed_gpu = object(), object()
        cache = MeshCache()
        cache.activate_level(level, [keep, removed])
        cache._cache[(id(keep), None, "normal")] = kept_gpu
        cache._cache[(id(removed), None, "normal")] = removed_gpu

        with mock.patch("view3d.gl_mesh.delete_mesh") as delete_mesh:
            cache.activate_level(level, [keep])

        self.assertIn((id(keep), None, "normal"), cache._cache)
        self.assertNotIn((id(removed), None, "normal"), cache._cache)
        delete_mesh.assert_called_once_with(removed_gpu)

    def test_activate_level_drops_old_texture_archive_variants(self):
        level, model = object(), object()
        old_textures, new_textures = object(), object()
        old_gpu = object()
        cache = MeshCache()
        cache.activate_level(level, [model], tex_cache=old_textures)
        old_key = (id(model), id(old_textures), "normal")
        cache._cache[old_key] = old_gpu

        with mock.patch("view3d.gl_mesh.delete_mesh") as delete_mesh:
            cache.activate_level(level, [model], tex_cache=new_textures)

        self.assertNotIn(old_key, cache._cache)
        delete_mesh.assert_called_once_with(old_gpu)

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
