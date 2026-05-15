import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from view3d.dtx import TextureAlphaInfo


class DtxAlphaTests(unittest.TestCase):
    def test_low_range_mostly_transparent_actor_alpha_is_ignored(self):
        info = TextureAlphaInfo(
            pixel_format=3,
            width=256,
            height=256,
            min_alpha=0,
            max_alpha=25,
            transparent_fraction=0.9999,
            mid_fraction=0.0001,
            nonopaque_fraction=1.0,
        )

        self.assertFalse(info.has_useful_alpha)

    def test_binary_cutout_alpha_remains_useful(self):
        info = TextureAlphaInfo(
            pixel_format=6,
            width=128,
            height=128,
            min_alpha=0,
            max_alpha=255,
            transparent_fraction=0.4,
            mid_fraction=0.0,
            nonopaque_fraction=0.4,
        )

        self.assertTrue(info.has_useful_alpha)


if __name__ == "__main__":
    unittest.main()
