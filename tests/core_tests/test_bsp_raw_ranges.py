import os
import sys
import struct
import unittest


from tests._path import ROOT  # noqa: F401

from core import bsp


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class BspRawRangeTests(unittest.TestCase):
    def load_sturmford(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "STURMFORDCITY.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            data = f.read()
        return data, bsp.parse(data)

    def test_door_submodels_keep_source_byte_ranges(self):
        data, world = self.load_sturmford()
        self.assertGreater(world.obj_pos, 0)
        self.assertGreater(world.world_model_table_start, 44)

        for name in ("Door32", "ChurchdoorR", "ChurchdoorL"):
            with self.subTest(name=name):
                model = world.model_by_name(name)
                self.assertIsNotNone(model)
                self.assertIsNotNone(model.raw_start)
                self.assertIsNotNone(model.raw_end)
                self.assertIsNotNone(model.world_bsp_start)
                self.assertIsNotNone(model.world_bsp_end)

                raw = world.raw_model_bytes(data, model)
                self.assertIsNotNone(raw)
                self.assertGreater(len(raw), 36)
                self.assertEqual(
                    struct.unpack_from("<I", raw, 0)[0],
                    model.next_world_item,
                )
                self.assertGreater(model.world_bsp_end, model.world_bsp_start)


if __name__ == "__main__":
    unittest.main()
