import os
import tempfile
import unittest

from features.prefabs.corpus import audit_prefab_corpus, discover_corpus_files
from mm9_patcher import mm9_patch as patcher
from tests.feature_tests.prefabs._fixtures import box_model, write_minimal_dat


def _catalog():
    return {"classes": {"Prop": {"object_lto": {"template_properties": [
        {"name": "Name", "code": 0, "flags": 0, "value": ""},
        {"name": "Pos", "code": 1, "flags": 0, "value": [0.0, 0.0, 0.0]},
        {"name": "Rotation", "code": 7, "flags": 0, "value": [0.0, 0.0, 0.0, 0.0]},
    ]}}}}


class PrefabCorpusAuditTests(unittest.TestCase):
    def test_good_static_and_behavioral_sources_close_the_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "Nested"))
            write_minimal_dat(
                os.path.join(tmp, "Static.dat"),
                [box_model("PhysicsBSP", (0.0, 0.0, 0.0), (8.0, 8.0, 8.0))],
                [],
            )
            write_minimal_dat(
                os.path.join(tmp, "Nested", "Prop.dat"),
                [],
                [patcher.WorldObject("Prop", [
                    patcher.Property("Name", 0, 0, "Prop1"),
                    patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
                    patcher.Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
                ])],
            )
            report = audit_prefab_corpus(tmp, catalog=_catalog())

        self.assertTrue(report.passed)
        self.assertEqual(report.total_files, 2)
        self.assertEqual(
            report.state_counts,
            {"behavioral_ready": 1, "static_ready": 1},
        )
        self.assertTrue(all(record.deterministic for record in report.records))
        self.assertEqual(
            [record.relative_path for record in report.records],
            ["Nested/Prop.dat", "Static.dat"],
        )

    def test_unknown_runtime_class_is_a_corpus_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_minimal_dat(
                os.path.join(tmp, "Unknown.dat"),
                [],
                [patcher.WorldObject("UnknownBehavior", [
                    patcher.Property("Name", 0, 0, "Unknown1"),
                ])],
            )
            report = audit_prefab_corpus(tmp, catalog=_catalog())

        self.assertFalse(report.passed)
        self.assertEqual(len(report.failures), 1)
        self.assertIn("behavioral import blocked", report.failures[0].failure)

    def test_discovery_rejects_a_missing_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing")
            with self.assertRaises(FileNotFoundError):
                discover_corpus_files(missing)


if __name__ == "__main__":
    unittest.main()
