import os
import struct
import unittest

from tests._path import ROOT  # noqa: F401

from features.dat_editing import output_validation
from mm9_patcher.mm9_patch import World


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class OutputValidationTests(unittest.TestCase):
    def load_bootcamp(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            return path, f.read()

    def test_validates_real_dat_structure(self):
        path, data = self.load_bootcamp()
        world = World.load(path)

        result = output_validation.validate_geometry_dat(
            data,
            expected_object_count=len(world.objects),
        )

        self.assertFalse(result.errors)
        self.assertIsNotNone(result.parsed_bsp)
        self.assertEqual(result.object_count, len(world.objects))

    def test_reports_bad_header_offsets(self):
        _path, data = self.load_bootcamp()
        corrupted = bytearray(data)
        struct.pack_into("<I", corrupted, 4, 12)

        result = output_validation.validate_geometry_dat(bytes(corrupted))

        self.assertTrue(any("header offsets are inconsistent" in error
                            for error in result.errors))

    def test_reports_missing_required_bsp_model(self):
        _path, data = self.load_bootcamp()

        result = output_validation.validate_geometry_dat(
            data,
            required_bsp_names=["DefinitelyMissingBspModel"],
        )

        self.assertTrue(any("required BSP model" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
