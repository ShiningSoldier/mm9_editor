import os
import struct
import sys
import tempfile
import unittest


from tests._path import ROOT  # noqa: F401

from features.prefabs import inspector as prefab_inspector
from tests.feature_tests.prefabs._fixtures import write_legacy_ed_prefab, write_prefab_fixtures


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class PrefabInspectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fence_path, cls.door_path = write_prefab_fixtures(cls._tmp.name)
        cls.ed_path = write_legacy_ed_prefab(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_inspects_converted_fence_prefab(self):
        path = self.fence_path

        info = prefab_inspector.inspect_prefab(path)

        self.assertEqual(info.version, 66)
        self.assertEqual(info.object_count, 0)
        self.assertEqual(info.model_count, 2)
        self.assertEqual(info.model_roles, {"physics": 1, "visibility": 1})
        self.assertTrue(info.has_only_system_geometry)
        self.assertEqual(info.total_polygons, 12)
        self.assertFalse(info.parse_warnings)

        names = [model.name for model in info.models]
        self.assertEqual(names, ["PhysicsBSP", "VisBSP"])

    def test_inspects_converted_door_prefab_objects_and_controller_geometry(self):
        path = self.door_path

        info = prefab_inspector.inspect_prefab(path)

        self.assertEqual(info.object_classes, {"RotatingDoor": 1})
        self.assertEqual(info.model_roles, {
            "controller_geometry": 1,
            "physics": 1,
            "visibility": 1,
        })
        self.assertEqual(info.objects[0].name, "Door1")
        self.assertEqual(info.models[0].name, "Door1")

    def test_report_includes_roles_and_bounds(self):
        path = self.fence_path

        report = prefab_inspector.format_report(prefab_inspector.inspect_prefab(path))

        self.assertIn("Model roles: physics=1, visibility=1", report)
        self.assertIn("Note: this prefab contains only system-named BSP models.", report)
        self.assertIn("Bounds:", report)

    def test_inspects_legacy_dedit_source_prefab(self):
        info = prefab_inspector.inspect_prefab(self.ed_path)

        self.assertEqual(info.source_format, "legacy_ed")
        self.assertEqual(info.version, 1249)
        self.assertEqual(info.model_count, 2)
        self.assertEqual(info.model_roles, {"geometry": 2})
        self.assertEqual(info.total_polygons, 2)
        self.assertEqual(info.bounds_min, (0.0, 0.0, 0.0))
        self.assertEqual(info.bounds_max, (128.0, 0.0, 384.0))

    def test_unknown_prefab_version_reports_both_supported_formats(self):
        path = os.path.join(self._tmp.name, "Unknown.ed")
        with open(path, "wb") as handle:
            handle.write(struct.pack("<I", 1248))

        with self.assertRaisesRegex(ValueError, "compiled DAT.*DEdit source prefab"):
            prefab_inspector.inspect_prefab(path)


if __name__ == "__main__":
    unittest.main()
