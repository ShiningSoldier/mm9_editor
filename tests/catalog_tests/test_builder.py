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
    load_catalog,
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

    def test_world_helpers_are_derived_from_object_lto_hierarchy_and_resources(self):
        actor = _object_lto_class("LoMMActor", parent="AIBase")
        actor["hierarchy"] = ["BaseClass", "ModelObject", "Actor", "AIBase", "LoMMActor"]
        object_lto_dump = _normalized_object_lto_dump({
            "LoMMActor": actor,
            "ServiceNode": _object_lto_class("ServiceNode"),
            "ModeledEffect": _object_lto_class(
                "ModeledEffect",
                properties=[
                    _object_lto_prop("Filename", 0, r"models\effects\visible.abc"),
                ],
            ),
            "AmbientAudio": _object_lto_class(
                "AmbientAudio",
                properties=[
                    _object_lto_prop("Filename", 0, r"sounds\ambient\wind.wav"),
                ],
            ),
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            catalog = build_catalog(tmpdir, object_lto_dump=object_lto_dump)

        helpers = {
            name: entry["world_helper"]
            for name, entry in catalog["classes"].items()
        }
        self.assertFalse(helpers["LoMMActor"]["is_helper"])
        self.assertEqual(helpers["LoMMActor"]["reason"], "actor_hierarchy")
        self.assertTrue(helpers["ServiceNode"]["is_helper"])
        self.assertFalse(helpers["ModeledEffect"]["is_helper"])
        self.assertTrue(helpers["AmbientAudio"]["is_helper"])

    def test_dat_model_resource_prevents_helper_classification(self):
        obj = patcher.WorldObject("CustomVisibleObject", [
            patcher.Property("Name", 0, 0, "Visible0"),
            patcher.Property("Pos", 1, 0, (1.0, 2.0, 3.0)),
            patcher.Property("Filename", 0, 0, r"models\custom\visible.abc"),
        ])

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "CUSTOM.DAT")
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
            catalog = build_catalog(tmpdir)

        entry = catalog["classes"]["CustomVisibleObject"]
        self.assertEqual(
            entry["dat_model_filenames"],
            [r"models\custom\visible.abc"],
        )
        self.assertFalse(entry["world_helper"]["is_helper"])
        self.assertEqual(entry["world_helper"]["reason"], "dat_model_resource")

    def test_loading_an_old_catalog_adds_world_helper_metadata_in_memory(self):
        old_catalog = {
            "classes": {
                "Timer": {
                    "property_names": ["Name", "Pos"],
                    "filenames": [],
                    "object_lto": {
                        "parent": "ObjectBase",
                        "hierarchy": ["BaseClass", "ObjectBase", "Timer"],
                        "template_properties": [],
                    },
                }
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "old_catalog.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(old_catalog, f)
            loaded = load_catalog(path)

        self.assertTrue(loaded["classes"]["Timer"]["world_helper"]["is_helper"])

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

    def test_object_lto_models_and_skins_are_separated_into_variants(self):
        object_lto_dump = _normalized_object_lto_dump({
            "Torch": _object_lto_class(
                "Torch",
                properties=[
                    _object_lto_prop("Filename", 0, r"models\props\torch.abc"),
                    _object_lto_prop("Skin", 0, r"skins\props\torch.dtx"),
                    _object_lto_prop("Skin2", 0, r"skins\props\flame.dtx"),
                ],
            ),
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            catalog = build_catalog(tmpdir, object_lto_dump=object_lto_dump)

        entry = catalog["classes"]["Torch"]
        self.assertEqual(entry["filenames"], [r"models\props\torch.abc"])
        self.assertEqual(
            entry["skins"],
            [r"skins\props\flame.dtx", r"skins\props\torch.dtx"],
        )
        self.assertEqual(entry["accessory_skins"], [r"skins\props\flame.dtx"])
        variant = catalog["model_variants"][r"models\props\torch.abc"][0]
        self.assertEqual(variant["name"], "Torch")
        self.assertEqual(
            variant["skins"],
            [r"skins\props\torch.dtx", r"skins\props\flame.dtx"],
        )
        self.assertEqual(variant["source_keys"], ["object.lto:Torch"])

    def test_skin_inventory_infers_conservative_lomm_actor_variants(self):
        object_lto_dump = _normalized_object_lto_dump({
            "Goblin": _object_lto_class(
                "Goblin",
                properties=[
                    _object_lto_prop("Filename", 0, r"models\Goblin.abc"),
                ],
            ),
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            catalog = build_catalog(
                tmpdir,
                object_lto_dump=object_lto_dump,
                skin_paths=[
                    r"SKINS\GOBLIN.DTX",
                    r"SKINS\GOBLINCHIEF.DTX",
                    r"SKINS\GOBLINPOLE.DTX",
                ],
            )

        variants = catalog["model_variants"][r"models\goblin.abc"]
        self.assertEqual([row["name"] for row in variants], ["Goblin", "Goblin Chief"])
        self.assertEqual(
            [row["skins"] for row in variants],
            [[r"skins\goblin.dtx"], [r"skins\goblinchief.dtx"]],
        )

    def test_skin_inventory_accepts_named_variants_without_plain_base_skin(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            catalog = build_catalog(
                tmpdir,
                model_paths=[r"MODELS\PRINCESS.ABC"],
                skin_paths=[
                    r"SKINS\PRINCESSBLUE.DTX",
                    r"SKINS\PRINCESSGOLD.DTX",
                    r"SKINS\PRINCESSPINK.DTX",
                    r"SKINS\PRINCESSPOLE.DTX",
                ],
            )

        self.assertEqual(
            catalog["model_resources"],
            [r"models\princess.abc"],
        )
        variants = catalog["model_variants"][r"models\princess.abc"]
        self.assertEqual(
            [row["name"] for row in variants],
            ["Princess Blue", "Princess Gold", "Princess Pink"],
        )

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
