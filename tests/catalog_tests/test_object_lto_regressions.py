import os
import tempfile
import unittest
from unittest.mock import patch

from tests._path import ROOT  # noqa: F401
import _path_setup  # noqa: F401
import mm9_patch as patcher

import catalog
from catalog import (
    OBJECT_LTO_DUMP_SCHEMA,
    ObjectLtoDumpError,
    build_catalog_from_rez,
)
from catalog.actor_visuals import parse_actor_visual_tables, resolve_actor_visual
from tests.core_tests.test_game_resources import write_minimal_rez


TABLE_HEADER = (
    "Number\tMonster Name\tModelName\tSkinName\tSkinName2\tSkinName3\t"
    "Type/Picture\n"
)


def _object_lto_class(name, parent="BaseClass", properties=None):
    return {
        "name": name,
        "parent": parent,
        "hierarchy": ["BaseClass", name],
        "flags": 0,
        "flag_names": [],
        "hidden_in_dedit": False,
        "runtime_loadable": True,
        "properties": properties or [],
    }


def _object_lto_prop(name, type_id, value):
    return {
        "name": name,
        "type_id": type_id,
        "type": "test",
        "flags": 0,
        "flag_names": [],
        "default_value": value,
    }


def _object_lto_dump(classes):
    return {
        "available": True,
        "schema": OBJECT_LTO_DUMP_SCHEMA,
        "source_dump": None,
        "object_lto_path": "object.lto",
        "server_object_version": 1,
        "class_count": len(classes),
        "classes": classes,
    }


def _world_bytes(*objects):
    world = patcher.World(
        patcher.Header(
            patcher.DAT_VERSION,
            patcher.HEADER_SIZE,
            patcher.HEADER_SIZE,
            (0,) * 8,
        ),
        b"",
        list(objects),
        b"",
    )
    with tempfile.NamedTemporaryFile(suffix=".DAT", delete=False) as tmp:
        path = tmp.name
    try:
        world.save(path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


class ObjectLtoCatalogRegressionTests(unittest.TestCase):
    def test_lizard_orc_mage_appears_with_zero_dat_instances(self):
        object_lto_dump = _object_lto_dump({
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
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            cat = catalog.build_catalog(tmpdir, object_lto_dump=object_lto_dump)

        entry = cat["classes"]["LizardOrcMage"]
        self.assertEqual(entry["instance_count"], 0)
        self.assertEqual(entry["levels"], [])
        self.assertEqual(entry["source"], "object.lto")
        self.assertEqual(entry["template"]["source_level"], "object.lto")
        self.assertIn(r"models\lizardorc.abc", entry["filenames"])

    def test_accountant_still_resolves_to_monsters_row_217(self):
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "186\tHonk\thonkfemale.abc\thonkf1.dtx\thonkhat.dtx\t\tHonk Worshipper A\n"
                + "217\tElder Honk\thonkfemale.abc\thonkf3.dtx\thonkhat.dtx\t\tHonk Worshipper2 B\n",
            ),
        ])

        visual = resolve_actor_visual(visuals, "Honk", "Accountant")

        self.assertIsNotNone(visual)
        self.assertEqual(visual.source_file, "MONSTERS.TXT")
        self.assertEqual(visual.number, "217")
        self.assertEqual(visual.model, r"models\honkfemale.abc")
        self.assertEqual(visual.skins, (r"skins\honkf3.dtx",))
        self.assertEqual(visual.accessory_skins, (r"skins\honkhat.dtx",))

    def test_skinname2_is_preserved_for_honks_and_lizard_orcs(self):
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "186\tHonk\thonkfemale.abc\thonkf1.dtx\thonkhat.dtx\t\tHonk Worshipper A\n"
                + "191\tLizard-Orc Mage\tlizardorc.abc\tLizardOrc.dtx\tLizOrcCutlass.dtx\t\tLizard-Orc C\n",
            ),
        ])

        honk = resolve_actor_visual(visuals, "Honk", "Honk0")
        lizard_orc = resolve_actor_visual(visuals, "LizardOrcMage", "LizardOrcMage0")

        self.assertIsNotNone(honk)
        self.assertEqual(honk.accessory_skins, (r"skins\honkhat.dtx",))
        self.assertIn(r"skins\honkhat.dtx", honk.all_skins)

        self.assertIsNotNone(lizard_orc)
        self.assertEqual(lizard_orc.accessory_skins, (r"skins\LizOrcCutlass.dtx",))
        self.assertIn(r"skins\LizOrcCutlass.dtx", lizard_orc.all_skins)

    def test_dat_only_fallback_keeps_catalog_shape_when_object_lto_unavailable(self):
        obj = patcher.WorldObject("RedWolf", [
            patcher.Property("Name", 0, 0, "RedWolf6"),
            patcher.Property("Pos", 1, 0, (1.0, 2.0, 3.0)),
            patcher.Property("Filename", 0, 0, r"models\wolf.abc"),
        ])

        with tempfile.TemporaryDirectory() as tmpdir:
            worlds_rez = os.path.join(tmpdir, "WORLDS.REZ")
            write_minimal_rez(worlds_rez, {
                "WORLDS/FALLBACK": _world_bytes(obj),
            })

            with patch(
                "catalog.builder.resolve_object_lto_dump",
                side_effect=ObjectLtoDumpError("helper unavailable"),
            ):
                cat = build_catalog_from_rez(
                    worlds_rez,
                    object_lto_path=r"C:\MM9\data\object.lto",
                )

        self.assertNotIn("object_lto", cat)
        self.assertEqual(cat["summary"]["total_levels"], 1)
        self.assertEqual(cat["summary"]["total_classes"], 1)
        self.assertEqual(set(cat["classes"]), {"RedWolf"})

        entry = cat["classes"]["RedWolf"]
        self.assertEqual(entry["source"], "dat")
        self.assertEqual(entry["instance_count"], 1)
        self.assertEqual(entry["levels"], ["FALLBACK.DAT"])
        self.assertEqual(entry["template"]["source_level"], "FALLBACK.DAT")
        self.assertEqual(entry["template"]["source_instance"], "RedWolf6")
        self.assertIn(r"models\wolf.abc", entry["filenames"])
        self.assertIn("Name", entry["property_names"])
        self.assertIn("Filename", entry["property_names"])

        filename_entry = cat["filenames"][r"models\wolf.abc"]
        self.assertEqual(filename_entry["classes"], ["RedWolf"])
        self.assertEqual(filename_entry["levels"], ["FALLBACK.DAT"])
        self.assertEqual(filename_entry["uses"], 1)


if __name__ == "__main__":
    unittest.main()
