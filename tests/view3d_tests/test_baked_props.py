import unittest
from types import SimpleNamespace

import numpy as np

from tests._path import ROOT  # noqa: F401

from view3d.gl_baked_props import build_baked_prop_arrays
from view3d.gl_mesh import GpuMesh
from view3d.gl_object_models import ObjectModelRenderItem


class _FakeCache:
    def __init__(self, abc):
        self.abc = abc
        self.requests = []

    def get_or_load_abc(self, filename):
        self.requests.append(filename)
        return self.abc


def _fake_mesh(name="models\\props\\box.abc"):
    return GpuMesh(
        vao=0,
        vbo=0,
        ibo=0,
        index_count=3,
        vertex_count=3,
        triangle_count=1,
        dropped_polys=0,
        category="object",
        model_name=name,
        tex_ranges=[],
        tri_positions=None,
    )


def _fake_abc(piece_name="box"):
    verts = [
        SimpleNamespace(pos=(0.0, 0.0, 0.0)),
        SimpleNamespace(pos=(1.0, 0.0, 0.0)),
        SimpleNamespace(pos=(0.0, 1.0, 0.0)),
    ]
    refs = [
        SimpleNamespace(vertex_index=0, u=0.0, v=0.0),
        SimpleNamespace(vertex_index=1, u=1.0, v=0.0),
        SimpleNamespace(vertex_index=2, u=0.0, v=1.0),
    ]
    piece = SimpleNamespace(
        name=piece_name,
        texture_name=piece_name,
        vertices=verts,
        triangles=[SimpleNamespace(refs=refs)],
    )
    return SimpleNamespace(pieces=[piece])


class BakedPropTests(unittest.TestCase):
    def test_bakes_object_vertices_in_display_space(self):
        obj = {
            "Filename": "models\\props\\box.abc",
            "Pos": [10.0, 20.0, 30.0],
            "Rotation": [0.0, 0.0, 0.0, 0.0],
            "Scale": 1.0,
        }
        item = ObjectModelRenderItem(
            world_index=7,
            obj=obj,
            mesh=_fake_mesh(),
            skins=[],
            material_ranges=[],
        )

        verts, indices, ranges, world_indices = build_baked_prop_arrays(
            [item],
            _FakeCache(_fake_abc()),
        )

        self.assertEqual(indices.tolist(), [0, 1, 2])
        self.assertEqual(world_indices, {7})
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0].world_index, 7)
        self.assertEqual(ranges[0].index_count, 3)
        self.assertEqual(ranges[0].byte_offset, 0)
        np.testing.assert_allclose(verts[0, :3], [-10.0, 20.0, 30.0])
        np.testing.assert_allclose(verts[1, :3], [-11.0, 20.0, 30.0])
        np.testing.assert_allclose(verts[2, :3], [-10.0, 21.0, 30.0])

    def test_skips_degenerate_triangles(self):
        abc = _fake_abc()
        abc.pieces[0].vertices[2].pos = (2.0, 0.0, 0.0)
        obj = {
            "Filename": "models\\props\\box.abc",
            "Pos": [0.0, 0.0, 0.0],
            "Rotation": [0.0, 0.0, 0.0, 0.0],
        }
        item = ObjectModelRenderItem(
            world_index=1,
            obj=obj,
            mesh=_fake_mesh(),
            skins=[],
            material_ranges=[],
        )

        verts, indices, ranges, world_indices = build_baked_prop_arrays(
            [item],
            _FakeCache(abc),
        )

        self.assertEqual(verts.shape, (0, 8))
        self.assertEqual(indices.size, 0)
        self.assertEqual(ranges, [])
        self.assertEqual(world_indices, set())


if __name__ == "__main__":
    unittest.main()
