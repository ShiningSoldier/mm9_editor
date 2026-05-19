import os
import sys
import unittest


from tests._path import ROOT  # noqa: F401

from features.prefabs import inspector as prefab_inspector


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class PrefabInspectorTests(unittest.TestCase):
    def test_inspects_converted_fence_prefab(self):
        path = os.path.join(DATA_ROOT, "PreFabs", "Fences&Gates", "OldWoodFence1.dat")
        if not os.path.exists(path):
            self.skipTest(f"missing converted prefab: {path}")

        info = prefab_inspector.inspect_prefab(path)

        self.assertEqual(info.version, 66)
        self.assertEqual(info.object_count, 0)
        self.assertEqual(info.model_count, 2)
        self.assertEqual(info.model_roles, {"physics": 1, "visibility": 1})
        self.assertTrue(info.has_only_system_geometry)
        self.assertEqual(info.total_polygons, 690)
        self.assertFalse(info.parse_warnings)

        names = [model.name for model in info.models]
        self.assertEqual(names, ["PhysicsBSP", "VisBSP"])

    def test_inspects_converted_door_prefab_objects_and_controller_geometry(self):
        path = os.path.join(DATA_ROOT, "PreFabs", "Doors", "A1_Door.dat")
        if not os.path.exists(path):
            self.skipTest(f"missing converted prefab: {path}")

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
        path = os.path.join(DATA_ROOT, "PreFabs", "Fences&Gates", "OldWoodFence1.dat")
        if not os.path.exists(path):
            self.skipTest(f"missing converted prefab: {path}")

        report = prefab_inspector.format_report(prefab_inspector.inspect_prefab(path))

        self.assertIn("Model roles: physics=1, visibility=1", report)
        self.assertIn("Note: this prefab contains only system-named BSP models.", report)
        self.assertIn("Bounds:", report)


if __name__ == "__main__":
    unittest.main()
