import os
import struct
import tempfile
import unittest

import numpy as np

from tests._path import ROOT  # noqa: F401

from view3d import dtx


def _bgra_dtx(width=2, height=1, pixels=None):
    if pixels is None:
        pixels = bytes([
            10, 20, 30, 255,
            40, 50, 60, 128,
        ])
    header = bytearray(164)
    struct.pack_into("<i", header, 4, -5)
    struct.pack_into("<H", header, 8, width)
    struct.pack_into("<H", header, 10, height)
    struct.pack_into("<H", header, 12, 1)
    struct.pack_into("<H", header, 26, 3)
    return bytes(header) + pixels


class DtxDecodeCacheTests(unittest.TestCase):
    def test_bgra_decode_returns_rgba_pixels(self):
        decoded = dtx.decode_dtx_rgba(_bgra_dtx())

        self.assertIsNotNone(decoded)
        width, height, rgba = decoded
        self.assertEqual((width, height), (2, 1))
        self.assertEqual(rgba.dtype, np.uint8)
        np.testing.assert_array_equal(
            rgba.reshape(-1, 4),
            np.asarray([
                [30, 20, 10, 255],
                [60, 50, 40, 128],
            ], dtype=np.uint8),
        )

    def test_decode_cache_key_changes_with_pixels(self):
        key_a = dtx.decoded_texture_cache_key(_bgra_dtx(pixels=bytes([1, 2, 3, 4] * 2)))
        key_b = dtx.decoded_texture_cache_key(_bgra_dtx(pixels=bytes([1, 2, 3, 5] * 2)))

        self.assertIsNotNone(key_a)
        self.assertIsNotNone(key_b)
        self.assertNotEqual(key_a, key_b)

    def test_decode_cache_roundtrip_writes_npz(self):
        blob = _bgra_dtx()
        with tempfile.TemporaryDirectory() as temp_dir:
            first = dtx.decode_dtx_rgba_cached(blob, cache_dir=temp_dir)
            second = dtx.decode_dtx_rgba_cached(blob, cache_dir=temp_dir)

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertTrue(any(name.endswith(".npz") for _root, _dirs, files in os.walk(temp_dir) for name in files))
            self.assertEqual(first[0:2], second[0:2])
            np.testing.assert_array_equal(first[2], second[2])

    def test_default_cache_dir_lives_under_project_cache(self):
        cache_dir = dtx._default_decode_cache_dir()
        self.assertTrue(cache_dir.endswith(os.path.join("cache", "view3d_textures")))
        self.assertIn("mm9_editor", cache_dir.replace("\\", "/"))


if __name__ == "__main__":
    unittest.main()
