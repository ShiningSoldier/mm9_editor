import os
import tempfile
import unittest


from tests._path import ROOT  # noqa: F401

from tests.core_tests.test_game_resources import write_minimal_rez
from tools import creature_import_audit


TABLE_HEADER = (
    "Number\tMonster Name\tModelName\tSkinName\tSkinName2\tSkinName3\t"
    "Type/Picture\tScriptName\tFootSound\tFootRadius\tBaseName\tIsMonster\n"
)


class CreatureImportAuditTests(unittest.TestCase):
    def _write_mm9_data(self, root: str) -> None:
        data = os.path.join(root, "data")
        os.makedirs(data, exist_ok=True)
        actor = (
            TABLE_HEADER
            + "191\tLizard-Orc Mage\tlizardorc.abc\tLizardOrc.dtx\t"
            + "LizOrcCutlass.dtx\t\tLizard-Orc B\tbasemelee.scr\t"
            + "LizardOrcstep\t100\tLizardOrc\t1\n"
        )
        monsters = (
            TABLE_HEADER
            + "191\tLizard-Orc Mage\tlizardorc.abc\tLizardOrc.dtx\t"
            + "LizOrcCutlass.dtx\t\tLizard-Orc C\tbaserange.scr\t"
            + "LizardOrcstep\t350\tLizardOrc\t1\n"
        )
        write_minimal_rez(os.path.join(data, "DATA.REZ"), {
            "DATA/ACTOR": actor.encode("latin-1"),
            "DATA/MONSTERS": monsters.encode("latin-1"),
        })
        write_minimal_rez(os.path.join(data, "MODELS.REZ"), {
            "MODELS/LIZARDORC": b"MM9_MODEL",
        })
        write_minimal_rez(os.path.join(data, "SKINS.REZ"), {
            "SKINS/LIZARDORC": b"MM9_SKIN",
        })
        write_minimal_rez(os.path.join(data, "SOUNDS.REZ"), {
            "SOUNDS/LIZARDORCSTEP": b"MM9_SOUND",
        })

    def _write_lomm_data(self, root: str) -> None:
        data = os.path.join(root, "data")
        os.makedirs(os.path.join(data, "MODELS"), exist_ok=True)
        os.makedirs(os.path.join(data, "SKINS"), exist_ok=True)
        with open(os.path.join(data, "MODELS", "ORC.ABC"), "wb") as fh:
            fh.write(b"LOMM_ORC_MODEL")
        with open(os.path.join(data, "SKINS", "ORC.DTX"), "wb") as fh:
            fh.write(b"LOMM_ORC_SKIN")

    def test_replace_row_reports_true_runtime_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9 = os.path.join(tmp, "mm9")
            lomm = os.path.join(tmp, "lomm")
            self._write_mm9_data(mm9)
            self._write_lomm_data(lomm)

            report = creature_import_audit.audit_creature_import(
                mm9_root=mm9,
                lomm_root=lomm,
                creature_name="LoMM Orc",
                target_class="LizardOrcMage",
                row_strategy=creature_import_audit.STRATEGY_REPLACE_ROW,
                model="ORC.ABC",
                target_model="OrcMM9.abc",
                skin="ORC.DTX",
                target_skin="Orc.dtx",
            )

            self.assertEqual(report["actor_rows"]["target_row"], "191")
            self.assertEqual(
                report["strategy"]["runtime_visibility"],
                "true-runtime-replacement",
            )
            self.assertEqual(report["assets"]["copy_plan"][0]["action"], "copy-from-lomm")
            self.assertEqual(
                report["assets"]["copy_plan"][0]["target_virtual_path"],
                "MODELS/ORCMM9",
            )
            self.assertEqual(
                report["actor_rows"]["suggested_monster_row"]["field_overrides"]["ModelName"],
                "OrcMM9.abc",
            )
            self.assertFalse(report["visual_mapping"]["requires_name_or_class_quirk"])
            self.assertFalse(
                report["visual_mapping"]["suggested_rule"]["editor_preview_only"])
            self.assertEqual(
                report["visual_mapping"]["suggested_rule"]["source_row"],
                "191",
            )

    def test_append_row_reports_editor_preview_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9 = os.path.join(tmp, "mm9")
            lomm = os.path.join(tmp, "lomm")
            self._write_mm9_data(mm9)
            self._write_lomm_data(lomm)

            report = creature_import_audit.audit_creature_import(
                mm9_root=mm9,
                lomm_root=lomm,
                creature_name="LoMM Orc",
                target_class="LizardOrcMage",
                row_strategy=creature_import_audit.STRATEGY_APPEND_ROW,
                model="ORC.ABC",
                skin="ORC.DTX",
            )

            self.assertEqual(
                report["strategy"]["runtime_visibility"],
                "editor-preview-only-unless-class-selects-row",
            )
            self.assertTrue(report["visual_mapping"]["requires_name_or_class_quirk"])
            self.assertTrue(
                report["visual_mapping"]["suggested_rule"]["editor_preview_only"])
            self.assertEqual(
                report["visual_mapping"]["suggested_rule"]["object_name_prefix"],
                "LoMMOrc",
            )
            self.assertIn("not expected", report["warnings"][0])


if __name__ == "__main__":
    unittest.main()
