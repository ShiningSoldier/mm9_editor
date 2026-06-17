import json
import os
import tempfile
import unittest


from tests._path import ROOT  # noqa: F401

from core import install_manager
from core import rezmgr as mm9_rezmgr
from tests.core_tests.test_game_resources import write_minimal_rez
from tools import actor_table_patch


HEADER = (
    "Number\tMonster Name\tModelName\tSkinName\tSkinName2\tSkinName3\t"
    "Type/Picture\tScriptName\tFootSound\tFootRadius\tBaseName\tIsMonster\r\n"
)
ROW_191_ACTOR = (
    "191\tLizard-Orc Mage\tlizardorc.abc\tLizardOrc.dtx\t"
    "LizOrcCutlass.dtx\t\tLizard-Orc B\tbasemelee.scr\t"
    "LizardOrcstep\t100\tLizardOrc\t1\r\n"
)
ROW_191_MONSTERS = (
    "191\tLizard-Orc Mage\tlizardorc.abc\tLizardOrc.dtx\t"
    "LizOrcCutlass.dtx\t\tLizard-Orc C\tbaserange.scr\t"
    "LizardOrcstep\t350\tLizardOrc\t1\r\n"
)


class ActorTablePatchTests(unittest.TestCase):
    def _write_data_rez(self, root: str) -> str:
        data = os.path.join(root, "data")
        data_rez = os.path.join(data, "DATA.REZ")
        write_minimal_rez(data_rez, {
            "DATA/ACTOR": (HEADER + ROW_191_ACTOR).encode("latin-1"),
            "DATA/MONSTERS": (HEADER + ROW_191_MONSTERS).encode("latin-1"),
        })
        return data_rez

    def test_replace_row_writes_installable_data_rez_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = os.path.join(tmp, "game")
            output_dir = os.path.join(tmp, "out", "actor_patch")
            self._write_data_rez(mm9_root)

            result = actor_table_patch.build_actor_table_patch(
                mm9_root=mm9_root,
                output_dir=output_dir,
                strategy=actor_table_patch.STRATEGY_REPLACE_ROW,
                target_class="LizardOrcMage",
                source_row="191",
                target_row="191",
                runtime_row="191",
                field_overrides={
                    "ModelName": "OrcMM9.abc",
                    "SkinName": "Orc.dtx",
                    "SkinName2": "",
                    "ScriptName": "baserange.scr",
                    "FootSound": "orcstep",
                    "FootRadius": "500",
                },
            )

            output_rez = os.path.join(output_dir, "data", "DATA.REZ")
            self.assertEqual(result["output_archive"], output_rez)
            self.assertIn(output_rez, install_manager.archives_to_install(output_dir))

            with mm9_rezmgr.RezReader(output_rez) as reader:
                actor = reader.extract_to_bytes("DATA/ACTOR").decode("latin-1")
                monsters = reader.extract_to_bytes("DATA/MONSTERS").decode("latin-1")

            self.assertIn("191\tLizard-Orc Mage\tOrcMM9.abc\tOrc.dtx\t\t", actor)
            self.assertIn("191\tLizard-Orc Mage\tOrcMM9.abc\tOrc.dtx\t\t", monsters)
            self.assertNotIn("304\t", actor)
            self.assertTrue(os.path.isfile(os.path.join(
                output_dir, "changed_entries", "DATA", "ACTOR.TXT")))

            with open(result["manifest"], "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            patch = manifest["actor_table_patch"]
            self.assertEqual(patch["runtime_visibility"], "true-runtime-replacement")
            self.assertEqual(patch["runtime_row"], "191")
            self.assertEqual(patch["row_patches"][0]["action"], "replaced")
            self.assertTrue(patch["row_patches"][0]["selected_by_runtime_class"])

    def test_append_row_records_preview_only_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = os.path.join(tmp, "game")
            output_dir = os.path.join(tmp, "out", "append_patch")
            self._write_data_rez(mm9_root)

            result = actor_table_patch.build_actor_table_patch(
                mm9_root=mm9_root,
                output_dir=output_dir,
                strategy=actor_table_patch.STRATEGY_APPEND_ROW,
                target_class="LizardOrcMage",
                source_row="191",
                target_row="304",
                runtime_row="191",
                field_overrides={
                    "Monster Name": "LoMM Orc",
                    "Type/Picture": "LoMM Orc",
                    "ModelName": "OrcMM9.abc",
                    "SkinName": "Orc.dtx",
                },
            )

            with mm9_rezmgr.RezReader(result["output_archive"]) as reader:
                actor = reader.extract_to_bytes("DATA/ACTOR").decode("latin-1")

            self.assertIn("191\tLizard-Orc Mage\tlizardorc.abc", actor)
            self.assertIn("304\tLoMM Orc\tOrcMM9.abc\tOrc.dtx", actor)

            with open(result["manifest"], "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            patch = manifest["actor_table_patch"]
            self.assertEqual(
                patch["runtime_visibility"],
                "editor-preview-only-unless-class-selects-row",
            )
            self.assertFalse(patch["row_patches"][0]["selected_by_runtime_class"])

    def test_unknown_field_override_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = os.path.join(tmp, "game")
            self._write_data_rez(mm9_root)

            with self.assertRaisesRegex(ValueError, "NoSuchField"):
                actor_table_patch.build_actor_table_patch(
                    mm9_root=mm9_root,
                    output_dir=os.path.join(tmp, "out", "bad_patch"),
                    strategy=actor_table_patch.STRATEGY_REPLACE_ROW,
                    target_class="LizardOrcMage",
                    source_row="191",
                    target_row="191",
                    runtime_row="191",
                    field_overrides={"NoSuchField": "oops"},
                )


if __name__ == "__main__":
    unittest.main()
