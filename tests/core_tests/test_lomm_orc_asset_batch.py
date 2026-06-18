import json
import os
import tempfile
import unittest


from tests._path import ROOT  # noqa: F401

from core import rezmgr as mm9_rezmgr
from tests.core_tests.test_game_resources import write_minimal_rez
from tools import lomm_orc_asset_batch


TABLE_HEADER = (
    "Number\tMonster Name\tModelName\tSkinName\tSkinName2\tSkinName3\t"
    "Type/Picture\tScriptName\tFootSound\tFootRadius\tBaseName\tIsMonster\n"
)
ROW_191_MONSTERS = (
    "191\tLizard-Orc Mage\tlizardorc.abc\tLizardOrc.dtx\t"
    "LizOrcCutlass.dtx\t\tLizard-Orc C\tbaserange.scr\t"
    "LizardOrcstep\t350\tLizardOrc\t1\n"
)


class LoMMOrcAssetBatchTests(unittest.TestCase):
    def _write_mm9_data(self, root: str) -> None:
        data = os.path.join(root, "data")
        write_minimal_rez(os.path.join(data, "DATA.REZ"), {
            "DATA/ACTOR": (TABLE_HEADER + ROW_191_MONSTERS).encode("latin-1"),
            "DATA/MONSTERS": (TABLE_HEADER + ROW_191_MONSTERS).encode("latin-1"),
        })
        write_minimal_rez(os.path.join(data, "MODELS.REZ"), {
            "MODELS/LIZARDORC": b"MM9_LIZARD_ORC_MODEL",
        })
        write_minimal_rez(os.path.join(data, "SKINS.REZ"), {
            "SKINS/LIZARDORC": b"MM9_LIZARD_ORC_SKIN",
        })
        write_minimal_rez(os.path.join(data, "SCRIPTS.REZ"), {
            "SCRIPTS/BASERANGE": b"script",
        })
        write_minimal_rez(os.path.join(data, "SOUNDS.REZ"), {
            "SOUNDS/ANIMSOUNDS/FOOTSTEPS/LIZARDORCSTEP1": b"step1",
            "SOUNDS/ANIMSOUNDS/FOOTSTEPS/LIZARDORCSTEP2": b"step2",
            "SOUNDS/ANIMSOUNDS/LIZARDORC/DIE1": b"anim-die",
            "SOUNDS/DEATHSOUNDS/LIZARDORC/DIE1": b"death-die",
        })

    def _write_lomm_data(self, root: str) -> bytes:
        data = os.path.join(root, "data")
        models = os.path.join(data, "MODELS")
        skins = os.path.join(data, "SKINS")
        os.makedirs(models, exist_ok=True)
        os.makedirs(skins, exist_ok=True)
        model = b"Stand Walk Run WAttack1 WAttack2 WAttack3 Rangeattack"
        skin = b"LOMM_ORC_SKIN"
        with open(os.path.join(models, "ORC.ABC"), "wb") as fh:
            fh.write(model)
        with open(os.path.join(skins, "ORC.DTX"), "wb") as fh:
            fh.write(skin)
        write_minimal_rez(os.path.join(data, "SOUNDS.REZ"), {
            "SOUNDS/ANIMSOUNDS/ORCATTACK1": b"attack",
            "SOUNDS/DEATHSOUNDS/ORCDIE1": b"death",
        })
        return skin

    def test_builds_asset_batch_and_records_inherited_sounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = os.path.join(tmp, "mm9")
            lomm_root = os.path.join(tmp, "lomm")
            out = os.path.join(tmp, "out")
            self._write_mm9_data(mm9_root)
            skin = self._write_lomm_data(lomm_root)

            result = lomm_orc_asset_batch.build_lomm_orc_asset_batch(
                mm9_root=mm9_root,
                lomm_root=lomm_root,
                output_dir=out,
            )

            self.assertEqual(result["status"], "ready")
            with mm9_rezmgr.RezReader(os.path.join(out, "data", "MODELS.REZ")) as reader:
                model = reader.extract_to_bytes("MODELS/ORCMM9")
            with mm9_rezmgr.RezReader(os.path.join(out, "data", "SKINS.REZ")) as reader:
                copied_skin = reader.extract_to_bytes("SKINS/ORC")

            self.assertIn(b"Hattack1", model)
            self.assertIn(b"RangeAttack", model)
            self.assertEqual(copied_skin, skin)

            with open(result["manifest"], "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            policy = manifest["sound_and_script_policy"]
            self.assertTrue(policy["script_checks"]["SCRIPTS/BASERANGE"])
            self.assertTrue(
                policy["footstep_checks"]["SOUNDS/ANIMSOUNDS/FOOTSTEPS/LIZARDORCSTEP1"])
            self.assertTrue(policy["inherited_animation_sounds"])
            self.assertTrue(policy["inherited_death_sounds"])
            self.assertIn(
                "SOUNDS/ANIMSOUNDS/ORCATTACK1",
                policy["lomm_orc_sound_sources_available_but_not_copied"],
            )


if __name__ == "__main__":
    unittest.main()
