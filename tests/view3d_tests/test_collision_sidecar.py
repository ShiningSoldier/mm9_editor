import json
import os
import struct
import tempfile
import unittest

import numpy as np

from tests._path import ROOT  # noqa: F401

from view3d.collision_sidecar import read_collision_sidecar
from view3d.gl_collision_overlay import build_collision_overlay_arrays


def _write_sidecar(path):
    vertices = [
        (1.0, 2.0, 3.0),
        (4.0, 2.0, 3.0),
        (1.0, 5.0, 3.0),
        (10.0, 0.0, 0.0),
        (10.0, 2.0, 0.0),
        (10.0, 0.0, 2.0),
    ]
    indices = [0, 1, 2, 3, 4, 5]
    triangles = [
        (2, 7, 0),  # floor
        (5, 9, 0),  # dynamicDoor
    ]
    with open(path, "wb") as f:
        f.write(b"MM9COLL\0")
        f.write(struct.pack("<IIII", 1, len(vertices), len(indices), len(triangles)))
        for vertex in vertices:
            f.write(struct.pack("<3f", *vertex))
        for index in indices:
            f.write(struct.pack("<I", index))
        for tri in triangles:
            f.write(struct.pack("<III", *tri))


class CollisionSidecarTests(unittest.TestCase):
    def test_reads_sidecar_and_manifest_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sidecar_path = os.path.join(temp_dir, "level.collisionmeshbin")
            manifest_path = os.path.join(temp_dir, "level.json")
            _write_sidecar(sidecar_path)
            with open(manifest_path, "w", encoding="latin-1") as f:
                json.dump({
                    "collisionMesh": {
                        "includesRenderFloors": True,
                        "renderFloorTriangles": 12,
                        "sourceModels": [
                            {"id": 7, "name": "FloorSource", "class": "PhysicsBSP", "baseRole": "physics"},
                            {"id": 9, "name": "Door01", "class": "Door", "baseRole": "dynamicDoor"},
                        ],
                    }
                }, f)

            sidecar = read_collision_sidecar(sidecar_path, manifest_path=manifest_path)

            self.assertEqual(sidecar.vertices.shape, (6, 3))
            self.assertEqual(sidecar.indices.tolist(), [0, 1, 2, 3, 4, 5])
            self.assertEqual([tri.role for tri in sidecar.triangles], ["floor", "dynamicDoor"])
            self.assertEqual(sidecar.role_counts(), {"floor": 1, "dynamicDoor": 1})
            self.assertTrue(sidecar.includes_render_floors)
            self.assertEqual(sidecar.render_floor_triangles, 12)
            self.assertEqual(sidecar.source_label(9), "Door01 (Door)")

    def test_overlay_arrays_filter_roles_and_reflect_x(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sidecar_path = os.path.join(temp_dir, "level.collisionmeshbin")
            _write_sidecar(sidecar_path)
            sidecar = read_collision_sidecar(sidecar_path)

            verts, indices, ranges = build_collision_overlay_arrays(sidecar, roles={"dynamicDoor"})

            self.assertEqual(indices.tolist(), [0, 1, 2])
            self.assertEqual(len(ranges), 1)
            self.assertEqual(ranges[0].role, "dynamicDoor")
            self.assertEqual(ranges[0].index_count, 3)
            np.testing.assert_allclose(verts[:, 0], [-10.0, -10.0, -10.0])

    def test_rejects_bad_magic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "bad.collisionmeshbin")
            with open(path, "wb") as f:
                f.write(b"not a sidecar")
            with self.assertRaises(ValueError):
                read_collision_sidecar(path)


if __name__ == "__main__":
    unittest.main()
