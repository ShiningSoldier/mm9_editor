import json
import os
import tempfile
import unittest


from tests._path import ROOT  # noqa: F401

import _path_setup  # noqa: F401
from core import install_manager
from core import rezmgr as mm9_rezmgr
from tests.core_tests.test_game_resources import write_minimal_rez
from tools import build_debug_script_patch


class DebugScriptPatchTests(unittest.TestCase):
    def test_builds_installable_scripts_rez_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = os.path.join(tmp, "game")
            data_dir = os.path.join(mm9_root, "data")
            scripts_rez = os.path.join(data_dir, "SCRIPTS.REZ")
            output_dir = os.path.join(tmp, "output", "debug_scripts")
            source_script = os.path.join(
                ROOT, "tools", "debug_scripts", "MM9ED_DEBUG_ACTOR.SCR")
            write_minimal_rez(scripts_rez, {
                "SCRIPTS/EXISTING.SCR": b"DebugOut existing\n",
                "SCRIPTS/MMIXSCRIPTTEXT": b"0,Hello\r\n",
            })

            result = build_debug_script_patch.build_debug_script_patch(
                mm9_root,
                output_dir,
                source_script,
            )

            output_rez = os.path.join(output_dir, "data", "SCRIPTS.REZ")
            self.assertEqual(result["archives"][0]["output_archive"], output_rez)
            self.assertIn(output_rez, install_manager.archives_to_install(output_dir))

            with mm9_rezmgr.RezReader(output_rez) as reader:
                payload = reader.extract_to_bytes("SCRIPTS/MM9ED_DEBUG_ACTOR.SCR")
                entry = reader.find("SCRIPTS/MM9ED_DEBUG_ACTOR.SCR")
                existing = reader.extract_to_bytes("SCRIPTS/EXISTING.SCR")
                script_text = reader.extract_to_bytes("SCRIPTS/MMIXSCRIPTTEXT.CSV")

            with open(source_script, "rb") as fh:
                self.assertEqual(payload, fh.read())
            self.assertEqual(entry.virtual_path(), "SCRIPTS/MM9ED_DEBUG_ACTOR")
            self.assertEqual(entry.typed_virtual_path(), "SCRIPTS/MM9ED_DEBUG_ACTOR.SCR")
            self.assertEqual(existing, b"DebugOut existing\n")
            self.assertIn(
                b"300,MM9ED_DEBUG_ACTOR script is running.",
                script_text,
            )

            with open(result["manifest"], "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            self.assertEqual(manifest["kind"], "debug_script_patch")
            self.assertEqual(
                manifest["debug_scripts"][0]["script_name_property"],
                r"scripts\MM9ED_DEBUG_ACTOR.scr",
            )
            self.assertEqual(
                manifest["debug_scripts"][0]["typed_virtual_path"],
                "SCRIPTS/MM9ED_DEBUG_ACTOR.SCR",
            )
            self.assertEqual(manifest["script_text"]["debug_row"], 300)


if __name__ == "__main__":
    unittest.main()
