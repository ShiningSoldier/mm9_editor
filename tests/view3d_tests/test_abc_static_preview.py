import os
import sys
import unittest


from tests._path import ROOT  # noqa: F401

from view3d.abc_loader import (
    _bounds_for_pieces,
    _is_top_level_model_path,
    load_abc,
)


DATA_ROOT = os.environ.get("MM9_MODELS_DIR", "")


class AbcStaticPreviewTests(unittest.TestCase):
    def load_model(self, name):
        path = os.path.join(DATA_ROOT, name)
        if not os.path.exists(path):
            self.skipTest(f"missing test model: {path}")
        model = load_abc(path, bake_static_bind_pose=True)
        self.assertIsNotNone(model)
        return model

    def assert_sane_preview(self, name):
        model = self.load_model(name)
        self.assertTrue(model.baked_bind_pose)
        self.assertGreater(sum(len(piece.triangles) for piece in model.pieces), 0)
        bounds = _bounds_for_pieces(model.pieces)
        self.assertIsNotNone(bounds)
        extent = max(bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4])
        self.assertGreater(extent, 0.001)
        self.assertLess(extent, 2000.0)

    def test_weighted_creature_previews_load(self):
        for name in ("BANSHEE.ABC", "BIGFOOT.ABC", "COW.ABC", "DRAGON.ABC", "GOBLIN.ABC"):
            with self.subTest(name=name):
                self.assert_sane_preview(name)

    def test_one_animation_civilian_previews_are_baked(self):
        for name in ("PEASANTF1.ABC", "PEASANTM1D.ABC", "PEASANTMS2.ABC"):
            with self.subTest(name=name):
                self.assert_sane_preview(name)

    def test_rez_cache_paths_keep_top_level_model_semantics(self):
        self.assertTrue(_is_top_level_model_path(
            r"C:\Users\shini\AppData\Local\MM9Editor\cache\models\0123456789abcdef\COW.ABC"))
        self.assertFalse(_is_top_level_model_path(
            r"C:\Users\shini\AppData\Local\MM9Editor\cache\models\0123456789abcdef\PROPS\BOX02.ABC"))
        self.assertTrue(_is_top_level_model_path(
            r"C:\Games\Might and Magic 9\cache\models\0123456789abcdef\COW.ABC"))
        self.assertFalse(_is_top_level_model_path(
            r"C:\Games\Might and Magic 9\cache\models\0123456789abcdef\PROPS\BOX02.ABC"))


if __name__ == "__main__":
    unittest.main()
