import os
import json
import tempfile
import unittest
from unittest.mock import patch

import mm9_patch as patcher

from catalog import (
    OBJECT_LTO_DUMP_SCHEMA,
    ObjectLtoDumpError,
    build_catalog,
    generate_object_lto_dump,
    load_object_lto_dump,
    resolve_object_lto_dump,
    save_catalog,
)


def _object_lto_class(
    name,
    parent="BaseClass",
    hidden=False,
    runtime=True,
    properties=None,
):
    return {
        "name": name,
        "parent": parent,
        "hierarchy": ["BaseClass", name] if parent != "BaseClass" else ["BaseClass"],
        "flags": 0,
        "flag_names": [],
        "hidden_in_dedit": hidden,
        "runtime_loadable": runtime,
        "properties": properties or [],
    }


def _object_lto_prop(name, type_id, value, flags=0):
    return {
        "name": name,
        "type_id": type_id,
        "type": "test",
        "flags": flags,
        "flag_names": [],
        "default_value": value,
    }


def _normalized_object_lto_dump(classes):
    return {
        "available": True,
        "schema": OBJECT_LTO_DUMP_SCHEMA,
        "source_dump": None,
        "object_lto_path": "object.lto",
        "server_object_version": 1,
        "class_count": len(classes),
        "classes": classes,
    }


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

    def test_load_object_lto_dump_normalizes_classes_by_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "object_lto.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "schema": OBJECT_LTO_DUMP_SCHEMA,
                    "object_lto_path": r"C:\MM9\data\object.lto",
                    "server_object_version": 1,
                    "class_count": 2,
                    "classes": [
                        {"name": "Honk", "parent": "AIBase", "properties": []},
                        {"name": "LizardOrcMage", "parent": "LizardOrc", "properties": []},
                    ],
                }, f)

            dump = load_object_lto_dump(path)

        self.assertTrue(dump["available"])
        self.assertEqual(dump["class_count"], 2)
        self.assertIn("Honk", dump["classes"])
        self.assertEqual(dump["classes"]["LizardOrcMage"]["parent"], "LizardOrc")

    def test_unobserved_object_lto_class_enters_catalog_with_default_template(self):
        object_lto_dump = {
            "available": True,
            "schema": OBJECT_LTO_DUMP_SCHEMA,
            "source_dump": None,
            "object_lto_path": "object.lto",
            "server_object_version": 1,
            "class_count": 1,
            "classes": {
                "LizardOrcMage": _object_lto_class(
                    "LizardOrcMage",
                    parent="LizardOrc",
                    properties=[
                        _object_lto_prop("Name", 0, "noname"),
                        _object_lto_prop("Pos", 1, [0.0, 0.0, 0.0]),
                        _object_lto_prop("Rotation", 7, [0.0, 0.0, 0.0]),
                        _object_lto_prop("Filename", 0, r"models\lizardorc.abc"),
                    ],
                ),
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            catalog = build_catalog(tmpdir, object_lto_dump=object_lto_dump)

        self.assertEqual(catalog["summary"]["total_levels"], 0)
        entry = catalog["classes"]["LizardOrcMage"]
        self.assertEqual(entry["instance_count"], 0)
        self.assertEqual(entry["levels"], [])
        self.assertEqual(entry["source"], "object.lto")
        self.assertEqual(entry["template"]["source_level"], "object.lto")
        self.assertIn("Filename", entry["property_names"])
        self.assertIn(r"models\lizardorc.abc", entry["filenames"])
        props = {p["name"]: p for p in entry["template"]["properties"]}
        self.assertEqual(props["Rotation"]["code"], 7)
        self.assertEqual(props["Rotation"]["value"], [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(catalog["summary"]["total_classes"], 1)

    def test_existing_dat_class_keeps_observed_metadata_when_object_lto_merges(self):
        object_lto_dump = _normalized_object_lto_dump({
            "RedWolf": _object_lto_class(
                "RedWolf",
                properties=[
                    _object_lto_prop("Name", 0, "noname"),
                    _object_lto_prop("Filename", 0, r"models\wolf_from_lto.abc"),
                ],
            ),
        })
        obj = patcher.WorldObject("RedWolf", [
            patcher.Property("Name", 0, 0, "RedWolf6"),
            patcher.Property("Pos", 1, 0, (1.0, 2.0, 3.0)),
            patcher.Property("Filename", 0, 0, r"models\wolf_from_dat.abc"),
        ])

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "MOUNTAINPASS.DAT")
            patcher.World(
                patcher.Header(
                    patcher.DAT_VERSION,
                    patcher.HEADER_SIZE,
                    patcher.HEADER_SIZE,
                    (0,) * 8,
                ),
                b"",
                [obj],
                b"",
            ).save(path)
            catalog = build_catalog(tmpdir, object_lto_dump=object_lto_dump)

        entry = catalog["classes"]["RedWolf"]
        self.assertEqual(entry["instance_count"], 1)
        self.assertEqual(entry["levels"], ["MOUNTAINPASS.DAT"])
        self.assertEqual(entry["source"], "object.lto+dat")
        self.assertEqual(entry["template"]["source_level"], "MOUNTAINPASS.DAT")
        self.assertIn(r"models\wolf_from_dat.abc", entry["filenames"])
        self.assertEqual(entry["object_lto"]["parent"], "BaseClass")

    def test_hidden_or_no_runtime_object_lto_classes_are_not_added_when_unobserved(self):
        object_lto_dump = _normalized_object_lto_dump({
            "HiddenOnly": _object_lto_class("HiddenOnly", hidden=True),
            "NoRuntimeOnly": _object_lto_class("NoRuntimeOnly", runtime=False),
            "VisibleRuntime": _object_lto_class("VisibleRuntime"),
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            catalog = build_catalog(tmpdir, object_lto_dump=object_lto_dump)

        self.assertNotIn("HiddenOnly", catalog["classes"])
        self.assertNotIn("NoRuntimeOnly", catalog["classes"])
        self.assertIn("VisibleRuntime", catalog["classes"])

    def test_generate_object_lto_dump_reports_missing_helper(self):
        with self.assertRaises(ObjectLtoDumpError):
            generate_object_lto_dump(
                r"C:\missing\object.lto",
                helper_path=r"C:\missing\object_lto_dump.exe",
            )

    def test_resolve_object_lto_dump_can_regenerate_when_dump_missing(self):
        fresh_dump = {
            "available": True,
            "schema": OBJECT_LTO_DUMP_SCHEMA,
            "classes": {},
            "class_count": 0,
        }
        with patch(
            "catalog.builder.generate_object_lto_dump",
            return_value=fresh_dump,
        ) as generate:
            result = resolve_object_lto_dump(
                object_lto_dump_path=r"C:\missing\object_lto.json",
                object_lto_path=r"C:\MM9\data\object.lto",
            )

        self.assertIs(result, fresh_dump)
        generate.assert_called_once()
            
if __name__ == "__main__":
    unittest.main()
