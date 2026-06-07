import unittest

from tests._path import ROOT  # noqa: F401

from features.dat_editing import uv_projection


class UvProjectionTests(unittest.TestCase):
    def test_dedit_uv_to_opq_matches_axis_aligned_triangle(self):
        result = uv_projection.dedit_uv_to_opq(
            [(0.0, 0.0, 0.0), (128.0, 0.0, 0.0), (128.0, 0.0, 128.0)],
            [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)],
        )

        self.assertIsNotNone(result)
        uv_o, uv_p, uv_q = result
        self.assertEqual(uv_o, (0.0, 0.0, 0.0))
        self.assertAlmostEqual(uv_p[0], 1.0)
        self.assertAlmostEqual(uv_p[1], -1.0)
        self.assertAlmostEqual(uv_p[2], 0.0)
        self.assertAlmostEqual(uv_q[0], 0.0)
        self.assertAlmostEqual(uv_q[1], 0.0)
        self.assertAlmostEqual(uv_q[2], -1.0)

    def test_dedit_uv_to_opq_rejects_degenerate_uv_triangle(self):
        result = uv_projection.dedit_uv_to_opq(
            [(0.0, 0.0, 0.0), (128.0, 0.0, 0.0), (128.0, 0.0, 128.0)],
            [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)],
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
