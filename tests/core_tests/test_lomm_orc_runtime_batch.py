import json
import os
import tempfile
import unittest


from tests._path import ROOT  # noqa: F401
import _path_setup  # noqa: F401

import mm9_patch as patcher
from core.rezmgr import RezReader
from tests.core_tests.test_game_resources import write_minimal_rez
from tools import lomm_orc_runtime_batch


TABLE_HEADER = (
    "Number\tMonster Name\tModelName\tSkinName\tSkinName2\tSkinName3\t"
    "Type/Picture\tScriptName\tFootSound\tFootRadius\tBaseName\tIsMonster\n"
)
ROW_191 = (
    "191\tLizard-Orc Mage\tlizardorc.abc\tLizardOrc.dtx\t"
    "LizOrcCutlass.dtx\t\tLizard-Orc C\tbaserange.scr\t"
    "LizardOrcstep\t350\tLizardOrc\t1\n"
)
ROW_304_STALE = (
    "304\tLoMM Orc Mage\tOrcMM9.abc\tOrc.dtx\t\t\t"
    "LoMMOrcMage\tbaserange.scr\torcstep\t500\tLoMMOrcMage\t1\n"
)
ROW_303_STALE = (
    "303\tLoMM Orc Mage\tOrcMM9.abc\tOrc.dtx\t\t\t"
    "LoMMOrcMage\tbaserange.scr\torcstep\t500\tLoMMOrcMage\t1\n"
)
ROW_306_STALE = (
    "306\tLoMM Orc Mage\tOrcMM9.abc\tOrc.dtx\t\t\t"
    "LoMMOrcMage\tbaserange.scr\torcstep\t500\tLoMMOrcMage\t1\n"
)


def _prop(name, type_id, value, source_class="LizardOrc"):
    return {
        "name": name,
        "source_class": source_class,
        "type_id": type_id,
        "type": "string" if type_id == 0 else ("vector" if type_id == 1 else "rotation"),
        "flags": 0,
        "flag_names": [],
        "group": 0,
        "hidden_in_dedit": False,
        "default_value": value,
        "default_raw": {
            "vector": [0, 0, 0],
            "float": 0,
            "string": value if type_id == 0 else None,
        },
    }


def _class(name, parent, hierarchy, properties):
    return {
        "name": name,
        "parent": parent,
        "hierarchy": hierarchy,
        "flags": 0,
        "flag_names": [],
        "hidden_in_dedit": False,
        "runtime_loadable": True,
        "class_object_size": 2988,
        "declared_properties": [],
        "properties": properties,
    }


def _world_bytes():
    world = patcher.World(
        patcher.Header(patcher.DAT_VERSION, patcher.HEADER_SIZE, patcher.HEADER_SIZE, (0,) * 8),
        b"",
        [patcher.WorldObject("StartPoint", [
            patcher.Property("Name", 0, 0, "StartPoint0"),
            patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
        ])],
        b"",
    )
    fd, path = tempfile.mkstemp(suffix=".DAT")
    os.close(fd)
    try:
        world.save(path)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.remove(path)


class LoMMOrcRuntimeBatchTests(unittest.TestCase):
    def _write_mm9_root(self, root: str) -> None:
        data = os.path.join(root, "data")
        os.makedirs(data, exist_ok=True)
        write_minimal_rez(os.path.join(data, "DATA.REZ"), {
            "DATA/ACTOR": (TABLE_HEADER + ROW_191 + ROW_303_STALE + ROW_304_STALE + ROW_306_STALE).encode("latin-1"),
            "DATA/MONSTERS": (TABLE_HEADER + ROW_191 + ROW_303_STALE + ROW_304_STALE + ROW_306_STALE).encode("latin-1"),
        })
        write_minimal_rez(os.path.join(data, "MODELS.REZ"), {
            "MODELS/LIZARDORC": b"model",
        })
        write_minimal_rez(os.path.join(data, "SKINS.REZ"), {
            "SKINS/LIZARDORC": b"skin",
        })
        write_minimal_rez(os.path.join(data, "WORLDS.REZ"), {
            "WORLDS/BOOTCAMP": _world_bytes(),
        })

    def _write_object_lto_batch(self, batch: str) -> None:
        data = os.path.join(batch, "data")
        os.makedirs(data, exist_ok=True)
        with open(os.path.join(data, "object.lto"), "wb") as fh:
            fh.write(b"wrapper")
        with open(os.path.join(data, "object_lto_base.lto"), "wb") as fh:
            fh.write(b"base")
        props = [
            _prop("Name", 0, "noname", "BaseClass"),
            _prop("Pos", 1, [0, 0, 0], "BaseClass"),
            _prop("Rotation", 7, [0, 0, 0, 0], "BaseClass"),
            _prop("Filename", 0, r"models\lizardorc.abc"),
            _prop("ScriptName", 0, "", "ObjectBase"),
        ]
        parent_hierarchy = [
            "BaseClass",
            "ObjectBase",
            "ModelObject",
            "Actor",
            "AIBase",
            "LizardOrc",
            "LizardOrcMage",
        ]
        classes = [
            _class("LizardOrcMage", "LizardOrc", parent_hierarchy, props),
            _class("LoMMOrcMage", "LizardOrcMage", parent_hierarchy + ["LoMMOrcMage"], props),
        ]
        with open(os.path.join(batch, "object_lto_dump.json"), "w", encoding="utf-8") as fh:
            json.dump({
                "schema": "mm9_editor.object_lto_dump.v1",
                "object_lto_path": "object.lto",
                "server_object_version": 1,
                "class_count": len(classes),
                "classes": classes,
            }, fh)
        with open(os.path.join(batch, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"kind": "object_lto_candidate"}, fh)

    def _write_asset_batch(self, batch: str) -> None:
        data = os.path.join(batch, "data")
        os.makedirs(data, exist_ok=True)
        write_minimal_rez(os.path.join(data, "MODELS.REZ"), {
            "MODELS/ORCMM9": b"orc-model",
        })
        write_minimal_rez(os.path.join(data, "SKINS.REZ"), {
            "SKINS/ORC": b"orc-skin",
        })
        with open(os.path.join(batch, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({
                "kind": "lomm_orc_stage2_asset_batch",
                "sound_and_script_policy": {"strategy": "inherit"},
            }, fh)

    def test_builds_combined_runtime_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9 = os.path.join(tmp, "mm9")
            object_lto = os.path.join(tmp, "object_lto")
            assets = os.path.join(tmp, "assets")
            out = os.path.join(tmp, "out")
            self._write_mm9_root(mm9)
            self._write_object_lto_batch(object_lto)
            self._write_asset_batch(assets)

            result = lomm_orc_runtime_batch.build_lomm_orc_runtime_batch(
                mm9_root=mm9,
                output_dir=out,
                object_lto_batch=object_lto,
                asset_batch=assets,
            )

            self.assertEqual(result["status"], "ready")
            with RezReader(os.path.join(out, "data", "DATA.REZ")) as reader:
                actor = reader.extract_to_bytes("DATA/ACTOR").decode("latin-1")
                monsters = reader.extract_to_bytes("DATA/MONSTERS").decode("latin-1")
            self.assertIn("121\tLoMM Orc Mage\tOrcMM9.abc\tOrc.dtx", actor)
            self.assertNotIn("303\tLoMM Orc Mage\tOrcMM9.abc\tOrc.dtx", actor)
            self.assertNotIn("303\tLoMM Orc Mage\tOrcMM9.abc\tOrc.dtx", monsters)
            self.assertNotIn("304\tLoMM Orc Mage\tOrcMM9.abc\tOrc.dtx", actor)
            self.assertNotIn("304\tLoMM Orc Mage\tOrcMM9.abc\tOrc.dtx", monsters)
            self.assertNotIn("306\tLoMM Orc Mage\tOrcMM9.abc\tOrc.dtx", actor)
            self.assertNotIn("306\tLoMM Orc Mage\tOrcMM9.abc\tOrc.dtx", monsters)
            self.assertIn(
                "191\tLizard-Orc Mage\tlizardorc.abc\tLizardOrc.dtx\tLizOrcCutlass.dtx",
                actor,
            )
            self.assertIn(
                "191\tLizard-Orc Mage\tlizardorc.abc\tLizardOrc.dtx\tLizOrcCutlass.dtx",
                monsters,
            )
            self.assertTrue(os.path.isfile(os.path.join(out, "data", "WORLDS.REZ")))
            self.assertTrue(os.path.isfile(os.path.join(out, "data", "object.lto")))
            self.assertTrue(os.path.isfile(os.path.join(out, "data", "object_lto_base.lto")))

            with open(result["manifest"], "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            self.assertEqual(manifest["target_row_data_patch"]["runtime_visibility"], "experimental-new-class-mapping")
            self.assertEqual(
                manifest["row191_stock_restore"]["row_patches"]["stale_experimental_rows_removed"]["ACTOR.TXT"],
                ["303", "304", "306"],
            )
            self.assertTrue(
                manifest["placement"]["actor_row_selection"]["target_row_matches_table_name_heuristic"])
            self.assertFalse(
                manifest["placement"]["actor_row_selection"]["target_row_known_selected_by_candidate_class"])


if __name__ == "__main__":
    unittest.main()
