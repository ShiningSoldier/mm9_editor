import unittest
import tempfile
from unittest.mock import patch
import os
import json

from catalog import save_catalog, DEFAULT_CATALOG_PATH

class BuilderTest(unittest.TestCase):
    def setUp(self):
        self.sample_catalog = {
            "classes": {},
            "filenames": {},
            "summary": {
                "total_levels": 1,
                "total_classes": 0,
                "max_npc_nbr": 0,
                "free_npc_nbrs_above_max": [],
            },
        }

    def test_creates_output_file(self):
        """Written file contains valid JSON matching the input catalog."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, "catalog.json")
            save_catalog(self.sample_catalog, out)
            with open(out) as f:
                result = json.load(f)
            self.assertEqual(result, self.sample_catalog)

    def test_creates_missing_dir(self):
        """Deeply nested missing directories are all created (makedirs behaviour)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, "new_subdir", "catalog.json")
            self.assertFalse(os.path.exists(os.path.dirname(out)))
            save_catalog(self.sample_catalog, out)
            self.assertTrue(os.path.isfile(out))

    def test_existing_dir_is_fine(self):
        """No error is raised when the output directory already exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, "catalog.json")
            save_catalog(self.sample_catalog, out)   # first write
            save_catalog(self.sample_catalog, out)   # second write — should not raise
            
if __name__ == "__main__":
    unittest.main()