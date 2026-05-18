import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import bsp
import prefab_import
import prefab_import_validation


DATA_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mm9_data")


class PrefabImportValidationTests(unittest.TestCase):
    def load_bootcamp_bsp(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        return bsp.parse_path(path)

    def fence_prefab_path(self):
        path = os.path.join(DATA_ROOT, "PreFabs", "Fences&Gates", "OldWoodFence1.dat")
        if not os.path.exists(path):
            self.skipTest(f"missing converted prefab: {path}")
        return path

    def door_prefab_path(self):
        path = os.path.join(DATA_ROOT, "PreFabs", "Doors", "A1_Door.dat")
        if not os.path.exists(path):
            self.skipTest(f"missing converted prefab: {path}")
        return path

    def test_warns_for_system_only_fence_default_texture(self):
        target_bsp = self.load_bootcamp_bsp()
        plan = prefab_import.build_static_import_plan(
            target_bsp,
            self.fence_prefab_path(),
            new_name="ImportedFence",
        )

        warnings = prefab_import_validation.validate_import_plans(target_bsp, [plan])

        self.assertTrue(any("normal visible submodel" in warning for warning in warnings))
        self.assertTrue(any("Default texture" in warning for warning in warnings))

    def test_warns_when_static_import_ignores_prefab_objects(self):
        target_bsp = self.load_bootcamp_bsp()
        plan = prefab_import.build_static_import_plan(
            target_bsp,
            self.door_prefab_path(),
            new_name="ImportedDoorGeometry",
        )

        warnings = prefab_import_validation.validate_import_plans(target_bsp, [plan])

        self.assertTrue(any("static BSP import ignores them" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
