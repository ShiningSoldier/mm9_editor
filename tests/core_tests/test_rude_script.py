import json
import os
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from core import project as P
from core import project_io
from core import rezmgr
from core import rude_script
from mm9_patcher import mm9_patch as patcher
from tests.core_tests.test_game_resources import write_minimal_rez


def _integration(*, npc_nbr=437, base_path="", base_source=""):
    return rude_script.DialogueScriptIntegration(
        npc_nbr=npc_nbr,
        base_virtual_path=base_path,
        base_source_text=base_source,
        hooks=[
            rude_script.RudeExitHook(
                completion_key=6001,
                label="Rescue complete",
                consume_key=True,
                reward=rude_script.ScriptReward(
                    experience=1200,
                    gold=350,
                    item_ids=(42, 43),
                ),
                completion_sound=rude_script.DEFAULT_COMPLETION_SOUND,
                world_changes=[
                    rude_script.ScriptWorldChange("QuestDoor", "unlock"),
                    rude_script.ScriptWorldChange("TownExit", "trigger"),
                ],
            )
        ],
    )


class RudeScriptGenerationTests(unittest.TestCase):
    def test_standalone_script_uses_verified_commands_in_action_order(self):
        source = _integration().render()

        self.assertIn(":Main\r\n\tOnRudeExit MM9EditorRudeExit", source)
        commands = [
            "HasKey 6001, MM9EditorHasKey",
            "TakeKey 6001",
            "GiveExp 1200",
            "GiveGold 350",
            "GiveItem 42",
            "GiveItem 43",
            'PlaySound "sounds\\events\\quest.wav", DoNothing, 100, 240, FALSE, 100',
            "GetObjectHandle QuestDoor, MM9EditorTarget",
            "Trigger MM9EditorTarget, unlock",
            "GetObjectHandle TownExit, MM9EditorTarget",
            "Trigger MM9EditorTarget, trigger",
        ]
        positions = [source.index(command) for command in commands]
        self.assertEqual(positions, sorted(positions))

    def test_existing_script_is_copied_and_calls_generated_hook_from_callback(self):
        base = (
            "; existing comments remain byte-for-byte around insertions\r\n"
            "#include globals.inc\r\n"
            "\r\n"
            ":OnRude\r\n"
            "\tGiveKey 77\r\n"
            "\tExit\r\n"
            "\r\n"
            ":Main\r\n"
            "\tOnRudeExit OnRude\r\n"
            "\tExit\r\n"
        )
        source = _integration(
            base_path="NPC1.SCR",
            base_source=base,
        ).render()

        self.assertIn(
            ":OnRude\r\n\tGosub MM9EditorRudeExit\r\n\tGiveKey 77",
            source,
        )
        self.assertEqual(source.count("OnRudeExit OnRude"), 1)
        self.assertIn("; existing comments remain byte-for-byte", source)
        self.assertIn(":MM9EditorRudeExit", source)

    def test_ambiguous_dynamic_onrudeexit_script_is_rejected(self):
        base = (
            ":First\nExit\n:Second\nExit\n:Main\n"
            "OnRudeExit First\nOnRudeExit Second\nExit\n"
        )
        integration = _integration(base_path="NPC2.SCR", base_source=base)
        with self.assertRaisesRegex(ValueError, "more than once"):
            integration.render()

    def test_unsafe_or_duplicate_hooks_are_rejected(self):
        integration = _integration()
        integration.hooks.append(rude_script.RudeExitHook(
            completion_key=6001,
            completion_sound=rude_script.DEFAULT_COMPLETION_SOUND,
        ))
        with self.assertRaisesRegex(ValueError, "more than one hook"):
            integration.render()

        integration = _integration()
        integration.hooks[0].world_changes[0].object_name = "Door; GiveGold 999"
        with self.assertRaisesRegex(ValueError, "safe JSL token"):
            integration.render()


class RudeScriptProjectTests(unittest.TestCase):
    def test_asset_round_trips_in_project_format_22(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "quest.mm9mod")
            project = P.Project()
            project.upsert_dialogue_script_asset(_integration())
            project_io.project_to_json(project, path)

            with open(path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
            restored = P.Project()
            log = project_io.project_from_json(path, restored)

        self.assertEqual(document["version"], 22)
        self.assertIn(437, restored.dialogue_script_assets)
        asset = restored.dialogue_script_assets[437]
        self.assertEqual(asset.integration.hooks[0].reward.item_ids, (42, 43))
        self.assertIn("dialogue script asset", " ".join(log))

    def test_script_only_save_stages_runtime_typed_scr_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts_rez = os.path.join(tmp, "game", "data", "SCRIPTS.REZ")
            work_dir = os.path.join(tmp, "output")
            write_minimal_rez(
                scripts_rez,
                {"SCRIPTS/EXISTING.SCR": b"existing"},
            )
            project = P.Project(
                scripts_rez_path=scripts_rez,
                work_dir=work_dir,
            )
            project.upsert_dialogue_script_asset(_integration())

            self.assertTrue(project.has_pending())
            plan = project.save_plan()
            patch = plan.scripts_archive_patch()
            self.assertIsNotNone(patch)
            self.assertEqual(patch.kind, "dialogue_scripts")
            self.assertEqual(plan.dats, [])
            project.execute(plan)

            output_rez = os.path.join(
                work_dir, plan.batch_id, "data", "SCRIPTS.REZ")
            manifest_path = os.path.join(work_dir, plan.batch_id, "manifest.json")
            with rezmgr.RezReader(output_rez) as reader:
                generated = reader.find("SCRIPTS/MM9EDITOR/NPC437_RUDE.SCR")
                self.assertIsNotNone(generated)
                self.assertEqual(generated.type_str.upper(), "SCR")
                source = reader.extract_to_bytes(
                    "SCRIPTS/MM9EDITOR/NPC437_RUDE.SCR")
                self.assertIn(b"OnRudeExit MM9EditorRudeExit", source)
                self.assertEqual(
                    reader.extract_to_bytes("SCRIPTS/EXISTING.SCR"), b"existing")
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)

        self.assertEqual(manifest["dialogue_script_assets"][0]["npc_nbr"], 437)
        self.assertIn(
            "SCRIPTS\\MM9EDITOR\\NPC437_RUDE.SCR",
            manifest["dialogue_script_assets"][0]["script_name"],
        )

    def test_untracked_existing_generated_path_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts_rez = os.path.join(tmp, "SCRIPTS.REZ")
            write_minimal_rez(
                scripts_rez,
                {"SCRIPTS/MM9EDITOR/NPC437_RUDE.SCR": b"user script"},
                resource_type=rezmgr._restype_for_filename("NPC.SCR"),
            )
            project = P.Project(
                scripts_rez_path=scripts_rez,
                work_dir=os.path.join(tmp, "output"),
            )
            project.upsert_dialogue_script_asset(_integration())
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                project.save_plan()

    def test_runtime_overlay_includes_applied_dialogue_script_without_dat_edits(self):
        world = patcher.World(
            patcher.Header(66, 0, 0, (0,) * 8),
            b"",
            [],
            b"",
        )
        level = P.LevelEdit(path="preview", source_kind="file", world=world)
        project = P.Project()
        project.upsert_dialogue_script_asset(_integration())

        entries = project.build_runtime_overlay_entries(level)

        self.assertIn(r"SCRIPTS\MM9EDITOR\NPC437_RUDE.SCR", entries)
        self.assertIn(
            b"GetObjectHandle TownExit, MM9EditorTarget",
            entries[r"SCRIPTS\MM9EDITOR\NPC437_RUDE.SCR"],
        )


if __name__ == "__main__":
    unittest.main()
