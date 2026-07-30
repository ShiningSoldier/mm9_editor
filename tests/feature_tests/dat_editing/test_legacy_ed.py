import os
import struct
import unittest
from unittest import mock

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
    def test_combined_analysis_shares_wrapper_and_object_scan(self):
        data = _sample_ed_bytes()
        original_decompress = legacy_ed._try_decompress_full_level_wrapper
        original_object_scan = legacy_ed._scan_object_records
        with mock.patch.object(
            legacy_ed,
            "_try_decompress_full_level_wrapper",
            wraps=original_decompress,
        ) as decompress_mock, mock.patch.object(
            legacy_ed,
            "_scan_object_records",
            wraps=original_object_scan,
        ) as object_scan_mock:
            analysis = legacy_ed.analyze_legacy_ed_bytes(data, source_path="fixture.ed")

        self.assertEqual(decompress_mock.call_count, 1)
        self.assertEqual(object_scan_mock.call_count, 1)
        self.assertEqual(analysis.geometry_scene.metadata["recovered_brush_count"], 1)
        self.assertEqual(analysis.object_scan.object_count, 0)
        self.assertEqual(analysis.node_layout.version, legacy_ed.LEGACY_ED_VERSION)

    def test_legacy_ed_bytes_to_geometry_scene_recovers_brush_record(self):
        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(_sample_ed_bytes(), source_path="fixture.ed")

        self.assertEqual(scene.metadata["kind"], "lithtech_legacy_ed_source_world")
        self.assertEqual(scene.metadata["version"], legacy_ed.LEGACY_ED_VERSION)
        self.assertEqual(scene.metadata["recovered_brush_count"], 1)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 1)
        self.assertEqual(scene.metadata["recovered_object_count"], 0)
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
        self.assertEqual(face.extras["surface_flags"], 0)
        self.assertEqual(face.extras["shade_rgb"], [0, 0, 0])

    def test_legacy_ed_rejects_wrong_version(self):
        with self.assertRaisesRegex(legacy_ed.LegacyEdParseError, "unsupported legacy ED version"):
            legacy_ed.legacy_ed_bytes_to_geometry_scene(struct.pack("<I", 1))

    def test_real_barrel_prefab_recovers_object_properties_when_available(self):
        path = r"C:\lithtech\PreFabs\Props\Barrel.ed"
        if not os.path.exists(path):
            self.skipTest(f"missing legacy ED prefab: {path}")

        scene = legacy_ed.load_legacy_ed_geometry_scene(path)
        report = legacy_ed.load_legacy_ed_object_scan_report(path)

        self.assertEqual(scene.metadata["version"], legacy_ed.LEGACY_ED_VERSION)
        self.assertEqual(scene.metadata["recovered_brush_count"], 0)
        self.assertEqual(scene.metadata["recovered_object_count"], 1)
        self.assertEqual(scene.metadata["object_class_counts"], {"DestructableProp": 1})
        self.assertEqual(report.object_count, 1)
        self.assertEqual(report.property_count, 68)
        self.assertEqual(report.class_counts, {"DestructableProp": 1})

        record = report.records[0]
        self.assertEqual(record.class_name, "DestructableProp")
        self.assertEqual(record.property_value("Name"), "Barrel1")
        self.assertEqual(record.property_value("Filename"), "MODELS\\Props\\Barrel.ABC")
        self.assertEqual(record.property_value("Skin"), "Skins\\Props\\Barrel.dtx")
        self.assertEqual(record.property_value("Pos"), (0.0, 0.0, 0.0))

        formatted = legacy_ed.format_legacy_ed_object_scan_report(report)
        self.assertIn("DestructableProp=1", formatted)
        self.assertIn("Filename=MODELS\\Props\\Barrel.ABC", formatted)

    def test_real_chair_prefab_recovers_expected_brushes_when_available(self):
        path = r"C:\lithtech\PreFabs\Furniture\Chair.ed"
        if not os.path.exists(path):
            self.skipTest(f"missing legacy ED prefab: {path}")

        scene = legacy_ed.load_legacy_ed_geometry_scene(path)

        self.assertEqual(scene.metadata["version"], legacy_ed.LEGACY_ED_VERSION)
        self.assertEqual(scene.metadata["recovered_brush_count"], 6)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 38)
        self.assertEqual(scene.metadata["recovered_object_count"], 6)
        self.assertEqual(scene.metadata["object_class_counts"], {"Brush": 6})
        self.assertIn("TEXTURES\\LevelTextures\\21_DeathMatch\\ClankersFloor.dtx", scene.material_texture_map())

        report = legacy_ed.load_legacy_ed_object_scan_report(path)
        self.assertEqual(report.object_count, 6)
        self.assertEqual(report.property_count, 156)
        self.assertEqual(report.class_counts, {"Brush": 6})
        self.assertEqual(report.records[0].property_value("Name"), "Brush46")

        layout = legacy_ed.load_legacy_ed_node_layout_report(path)
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.polyhedron_count, 6)
        self.assertEqual(layout.surface_count, 38)
        self.assertEqual(layout.surface_trailing_field_count, 38)
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes")
        self.assertEqual(layout.root_child_count, 1)
        self.assertEqual(layout.group_child_count, 6)
        self.assertEqual(layout.brush_object_count, 6)
        formatted = legacy_ed.format_legacy_ed_node_layout_report(layout)
        self.assertIn("named_group_brush_nodes", formatted)

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
        self.assertEqual(scene.metadata["recovered_brush_count"], 3774)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 22191)
        self.assertEqual(scene.metadata["recovered_object_count"], 4961)
        self.assertEqual(scene.metadata["object_class_counts"]["Brush"], 3774)
        self.assertEqual(scene.metadata["object_class_counts"]["WorldProperties"], 1)
        self.assertIn("TEXTURES\\A3Sturmgaard\\floors\\keepstonefloor128a.dtx", scene.material_texture_map())


if __name__ == "__main__":
    unittest.main()
