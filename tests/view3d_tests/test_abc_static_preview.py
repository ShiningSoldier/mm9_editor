import os
import sys
import unittest


from tests._path import ROOT  # noqa: F401

from view3d.abc_loader import (
    AbcPiece,
    AbcVertex,
    _bake_with_node_matrices,
    _bounds_for_pieces,
    _is_top_level_model_path,
    load_abc,
)


DATA_ROOT = os.environ.get("MM9_MODELS_DIR", "")


def _prop_model_path(*parts):
    if DATA_ROOT:
        return os.path.join(DATA_ROOT, "PROPS", *parts)
    return os.path.join(ROOT, "mm9_data", "MODELS", "PROPS", *parts)


class AbcStaticPreviewTests(unittest.TestCase):
    def test_static_pose_bake_preserves_authored_vertex_normals(self):
        saved_normals = (
            (0.0, 0.6, 0.8),
            (0.8, 0.6, 0.0),
        )
        piece = AbcPiece(
            name="model",
            vertices=[
                AbcVertex(
                    bone_index=0,
                    pos=(0.0, 0.0, 0.0),
                    weights=((0, (0.0, 0.0, 0.0), 1.0),),
                    saved_pos=(4.0, 5.0, 6.0),
                    saved_normal=saved_normals[0],
                ),
                AbcVertex(
                    bone_index=0,
                    pos=(2.0, 0.0, 0.0),
                    saved_pos=(6.0, 5.0, 6.0),
                    saved_normal=saved_normals[1],
                ),
            ],
            triangles=[],
        )
        matrix = (
            (1.0, 0.0, 0.0, 10.0),
            (0.0, 1.0, 0.0, 20.0),
            (0.0, 0.0, 1.0, 30.0),
            (0.0, 0.0, 0.0, 1.0),
        )

        baked = _bake_with_node_matrices([piece], {0: matrix})

        self.assertIsNotNone(baked)
        self.assertEqual([vertex.pos for vertex in baked[0].vertices], [
            (10.0, 20.0, 30.0),
            (12.0, 20.0, 30.0),
        ])
        self.assertEqual(
            [vertex.normal for vertex in baked[0].vertices],
            list(saved_normals),
        )
        self.assertEqual(
            [vertex.saved_normal for vertex in baked[0].vertices],
            list(saved_normals),
        )

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
        normals = [vertex.normal for piece in model.pieces for vertex in piece.vertices]
        self.assertTrue(normals)
        self.assertTrue(any(normal is not None for normal in normals))
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

    def test_rigid_prop_uses_direct_bone_index_and_saved_model_space(self):
        path = _prop_model_path("TABLE_BENCH.ABC")
        if not os.path.exists(path):
            self.skipTest(f"missing test model: {path}")

        model = load_abc(path, bake_static_bind_pose=True)

        self.assertIsNotNone(model)
        self.assertTrue(model.baked_bind_pose)
        self.assertEqual(model.default_user_dims(), (49.0, 11.0, 49.0))
        self.assertEqual(model.bottom_pivot_offset_y, 0.0)
        vertices = [vertex for piece in model.pieces for vertex in piece.vertices]
        self.assertTrue(vertices)
        self.assertEqual({vertex.bone_index for vertex in vertices}, {1})
        self.assertTrue(all(vertex.pos == vertex.saved_pos for vertex in vertices))
        bounds = _bounds_for_pieces(model.pieces)
        self.assertAlmostEqual(bounds[2], -17.724, places=3)
        self.assertAlmostEqual(bounds[3], 17.713, places=3)

    def test_tree_detects_ground_oriented_source_pivot(self):
        path = _prop_model_path("PLANTSANDTREES", "TREE02.ABC")
        if not os.path.exists(path):
            self.skipTest(f"missing test model: {path}")

        model = load_abc(path, bake_static_bind_pose=True)

        self.assertIsNotNone(model)
        self.assertTrue(model.baked_bind_pose)
        self.assertEqual(model.default_user_dims(), (35.0, 278.0, 35.0))
        self.assertAlmostEqual(model.bottom_pivot_offset_y, 279.630981, places=3)


if __name__ == "__main__":
    unittest.main()
