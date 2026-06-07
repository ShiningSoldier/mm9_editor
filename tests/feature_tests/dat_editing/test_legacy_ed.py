import os
import struct
import unittest

from tests._path import ROOT  # noqa: F401

from features.dat_editing import legacy_ed


def _sample_ed_bytes() -> bytes:
    data = bytearray()
    data.extend(struct.pack("<I", legacy_ed.LEGACY_ED_VERSION))
    data.extend(b"\x00" * 16)
    data.extend(bytes([255, 128, 64]))
    data.extend(struct.pack("<I", 4))
    for point in [
        (0.0, 0.0, 0.0),
        (128.0, 0.0, 0.0),
        (128.0, 0.0, 128.0),
        (0.0, 0.0, 128.0),
    ]:
        data.extend(struct.pack("<3f", *point))
    data.extend(struct.pack("<I", 1))
    data.extend(struct.pack("<I", 4))
    data.extend(struct.pack("<4H", 0, 1, 2, 3))
    data.extend(struct.pack("<3ff", 0.0, 1.0, 0.0, 0.0))
    data.extend(struct.pack("<3f", 0.0, 0.0, 0.0))
    data.extend(struct.pack("<3f", 1.0, 0.0, 0.0))
    data.extend(struct.pack("<3f", 0.0, 0.0, 1.0))
    data.extend(struct.pack("<I", 1))
    texture = b"TEXTURES\\World\\Floor.dtx"
    data.extend(struct.pack("<H", len(texture)))
    data.extend(texture)
    data.extend(b"\x00" * 12)
    return bytes(data)


class LegacyEdTests(unittest.TestCase):
    def test_legacy_ed_bytes_to_geometry_scene_recovers_brush_record(self):
        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(_sample_ed_bytes(), source_path="fixture.ed")

        self.assertEqual(scene.metadata["kind"], "lithtech_legacy_ed_source_world")
        self.assertEqual(scene.metadata["version"], legacy_ed.LEGACY_ED_VERSION)
        self.assertEqual(scene.metadata["recovered_brush_count"], 1)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 1)
        self.assertEqual(scene.material_texture_map()["TEXTURES\\World\\Floor.dtx"], "TEXTURES\\World\\Floor.dtx")

        model = scene.mesh_models()[0]
        self.assertEqual(model.name, "LegacyBrush0")
        self.assertEqual(model.extras["color"], [255, 128, 64])
        self.assertEqual(model.points[2], (128.0, 0.0, 128.0))
        face = model.faces[0]
        self.assertEqual(face.vertex_indices, [0, 1, 2, 3])
        self.assertEqual(face.material_name, "TEXTURES\\World\\Floor.dtx")
        self.assertEqual(face.extras["normal"], [0.0, 1.0, 0.0])
        self.assertEqual(face.extras["uv_o"], [0.0, 0.0, 0.0])
        self.assertEqual(face.extras["uv_p"], [1.0, 0.0, 0.0])
        self.assertEqual(face.extras["uv_q"], [0.0, 0.0, 1.0])
        self.assertEqual(face.extras["texture_flags"], 1)

    def test_legacy_ed_rejects_wrong_version(self):
        with self.assertRaisesRegex(legacy_ed.LegacyEdParseError, "unsupported legacy ED version"):
            legacy_ed.legacy_ed_bytes_to_geometry_scene(struct.pack("<I", 1))

    def test_real_chair_prefab_recovers_expected_brushes_when_available(self):
        path = r"C:\lithtech\PreFabs\Furniture\Chair.ed"
        if not os.path.exists(path):
            self.skipTest(f"missing legacy ED prefab: {path}")

        scene = legacy_ed.load_legacy_ed_geometry_scene(path)

        self.assertEqual(scene.metadata["version"], legacy_ed.LEGACY_ED_VERSION)
        self.assertEqual(scene.metadata["recovered_brush_count"], 6)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 38)
        self.assertIn("TEXTURES\\LevelTextures\\21_DeathMatch\\ClankersFloor.dtx", scene.material_texture_map())

    def test_shipped_full_level_ed_decompresses_and_recovers_brush_records(self):
        path = os.path.join(ROOT, "mm9_data", "WORLDS", "BEETHOVEN.ED")
        if not os.path.exists(path):
            self.skipTest(f"missing shipped ED level: {path}")

        scene = legacy_ed.load_legacy_ed_geometry_scene(path)

        self.assertEqual(scene.metadata["version"], legacy_ed.LEGACY_ED_VERSION)
        self.assertEqual(scene.metadata["wrapper"], "zlib_blocked_full_level")
        self.assertEqual(scene.metadata["block_count"], 129)
        self.assertIn("PBlockSize 2048", scene.metadata["infostring"])
        self.assertEqual(scene.metadata["declared_brush_count"], 3774)
        self.assertEqual(scene.metadata["recovered_brush_count"], 3748)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 22041)
        self.assertIn("TEXTURES\\A3Sturmgaard\\floors\\keepstonefloor128a.dtx", scene.material_texture_map())


if __name__ == "__main__":
    unittest.main()
