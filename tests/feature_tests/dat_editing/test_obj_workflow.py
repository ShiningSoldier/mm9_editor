import json
import os
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from features.dat_editing import obj_workflow


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class ObjWorkflowTests(unittest.TestCase):
    def load_bootcamp_bytes(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            return f.read()

    def write_meta(self, tmp, meta):
        path = os.path.join(tmp, "test_geometry.datmeta.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        return path

    def minimal_meta(self):
        return {
            "kind": "mm9_dat_geometry_roundtrip",
            "source": {},
            "coordinate_system": {
                "export_to_dat_matrix": [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
            },
            "models": [{"name": "VisibleRoom"}],
        }

    def test_rejects_wrong_sidecar_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = self.minimal_meta()
            meta["kind"] = "something_else"
            path = self.write_meta(tmp, meta)

            with self.assertRaisesRegex(ValueError, "unsupported kind"):
                obj_workflow.load_roundtrip_meta(path)

    def test_checksum_mismatch_error_points_to_reexport(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = self.minimal_meta()
            meta["source"] = {
                "path": "WORLDS/OTHERLEVEL",
                "sha256": "0" * 64,
            }
            path = self.write_meta(tmp, meta)

            with self.assertRaisesRegex(ValueError, "Re-export this level"):
                obj_workflow.load_roundtrip_meta(path, self.load_bootcamp_bytes())

    def test_missing_obj_message_lists_available_objects(self):
        message = obj_workflow.missing_obj_message(
            "ExpectedModel",
            "SourceModel",
            ["OtherModel", "AnotherModel"],
        )

        self.assertIn("ExpectedModel", message)
        self.assertIn("SourceModel", message)
        self.assertIn("OtherModel", message)


if __name__ == "__main__":
    unittest.main()
