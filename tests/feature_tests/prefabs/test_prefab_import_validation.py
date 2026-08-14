import os
import sys
import unittest


from tests._path import ROOT  # noqa: F401

from core import bsp
from features.prefabs import import_static as prefab_import
from features.prefabs import validation as prefab_import_validation
from tests.feature_tests.prefabs._fixtures import write_prefab_fixtures


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class PrefabImportValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fence_path, cls.door_path = write_prefab_fixtures(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def load_bootcamp_bsp(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        return bsp.parse_path(path)

    def fence_prefab_path(self):
        return self.fence_path

    def door_prefab_path(self):
        return self.door_path

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
        self.assertTrue(any("without a collision helper" in warning for warning in warnings))

    def test_warns_when_static_import_ignores_prefab_objects(self):
        target_bsp = self.load_bootcamp_bsp()
        plan = prefab_import.build_static_import_plan(
            target_bsp,
            self.door_prefab_path(),
            new_name="ImportedDoorGeometry",
        )

        warnings = prefab_import_validation.validate_import_plans(target_bsp, [plan])

        self.assertTrue(any("static BSP import ignores them" in warning for warning in warnings))

    def test_warns_for_extreme_collision_helper_shape(self):
        target_bsp = self.load_bootcamp_bsp()
        path = self.fence_prefab_path()
        with open(os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT"), "rb") as f:
            bootcamp = f.read()
        plan = prefab_import.build_static_import_plan(
            target_bsp,
            path,
            new_name="ImportedFence",
            collision_mode="box_approx",
            collision_thickness=1.0,
            collision_segment_length=8192.0,
            target_dat_bytes=bootcamp,
        )

        warnings = prefab_import_validation.validate_import_plans(target_bsp, [plan])

        self.assertTrue(any("extremely thin" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
