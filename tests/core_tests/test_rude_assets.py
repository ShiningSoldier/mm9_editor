import json
import os
import copy
import shutil
import tempfile
import unittest


from tests._path import ROOT  # noqa: F401

from core import project as P
from core import project_io
from core import rezmgr
from core import rude
from tests.core_tests.test_game_resources import write_minimal_rez


def row(npc_nbr, state_id=None, branch_id=1, action=-1, response="Response"):
    state_id = npc_nbr if state_id is None else state_id
    return (
        f'{npc_nbr},{state_id},{branch_id},"Choice","{response}",{action},'
        + ",".join(["0"] * 24)
        + "\r\n"
    ).encode("latin-1")


def write_rude_archive(path):
    write_minimal_rez(path, {
        "RUDE/NPCNAME": (
            b'1,"Normal NPC"\r\n'
            b'997,"Quest Notes"\r\n'
            b'998,"Auto Notes"\r\n'
            b'999,"Awards"\r\n'
        ),
        "RUDE/TOPBLURB": (
            b'1,1,"Hello"\r\n'
            b'997,997,"Quest Notes"\r\n'
            b'998,998,"Auto Notes"\r\n'
            b'999,999,"Awards"\r\n'
        ),
        "RUDE/NPC1": row(1),
        "RUDE/NPC997": row(997, response="Original quest note"),
        "RUDE/NPC998": row(998, response="Original auto note"),
        "RUDE/NPC999": row(999, response="Original award"),
    }, resource_type=rezmgr._restype_for_filename("NPC.RUDE"))


class IndependentRudeAssetTests(unittest.TestCase):
    def test_asset_identity_cannot_be_changed_through_metadata(self):
        metadata = rude.RudeDialogueMetadata(
            npc_nbr=437,
            name="Identity",
            initial_state=437,
            opening_blurb="Hello",
        )
        asset = P.RudeAssetEdit(
            npc_nbr=437,
            dialogue=rude.make_simple_dialogue(metadata, []),
            source_virtual_path="RUDE/NPC437",
        )
        asset.metadata.npc_nbr = 438

        with self.assertRaisesRegex(ValueError, "cannot change identity"):
            _dirty = asset.is_dirty

    def test_special_dialogue_can_be_edited_and_saved_without_a_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "RUDE.REZ")
            work_dir = os.path.join(tmp, "output")
            write_rude_archive(source_rez)
            project = P.Project(rude_rez_path=source_rez, work_dir=work_dir)

            asset = project.open_rude_asset(997)
            self.assertFalse(asset.is_dirty)
            self.assertFalse(project.has_pending())
            asset.dialogue.choices_in_file_order[0].npc_response = "Edited quest note"

            self.assertTrue(project.has_pending())
            plan = project.save_plan()
            self.assertEqual(plan.dats, [])
            self.assertEqual([item.npc_nbr for item in plan.rude_assets], [997])
            self.assertEqual(plan.rude_archive_patch().entries, ["RUDE/NPC997"])

            project.execute(plan)

            output_rez = os.path.join(work_dir, plan.batch_id, "data", "RUDE.REZ")
            with rezmgr.RezReader(output_rez) as reader:
                output_bytes = reader.extract_to_bytes("RUDE/NPC997.RUDE")
                self.assertEqual(reader.find("RUDE/NPC997.RUDE").type_str, "RUDE")
            with rezmgr.RezReader(source_rez) as reader:
                source_bytes = reader.extract_to_bytes("RUDE/NPC997")

            self.assertIn(b"Edited quest note", output_bytes)
            self.assertIn(b"Original quest note", source_bytes)
            manifest_path = os.path.join(work_dir, plan.batch_id, "manifest.json")
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            self.assertEqual(manifest["dats"], [])
            self.assertEqual(manifest["rude_assets"][0]["npc_nbr"], 997)

    def test_metadata_only_edit_patches_only_its_catalog_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "RUDE.REZ")
            work_dir = os.path.join(tmp, "output")
            write_rude_archive(source_rez)
            project = P.Project(rude_rez_path=source_rez, work_dir=work_dir)

            asset = project.open_rude_asset(999)
            asset.metadata.name = "Custom Awards"
            plan = project.save_plan()

            self.assertTrue(asset.name_changed)
            self.assertFalse(asset.blurb_changed)
            self.assertFalse(asset.dialogue_changed)
            self.assertEqual(plan.rude_archive_patch().entries, ["RUDE/NPCNAME"])

            project.execute(plan)
            output_rez = os.path.join(work_dir, plan.batch_id, "data", "RUDE.REZ")
            with rezmgr.RezReader(source_rez) as source_reader:
                original_blurb = source_reader.extract_to_bytes("RUDE/TOPBLURB")
                original_dialogue = source_reader.extract_to_bytes("RUDE/NPC999")
            with rezmgr.RezReader(output_rez) as output_reader:
                names = output_reader.extract_to_bytes("RUDE/NPCNAME")
                self.assertEqual(
                    output_reader.extract_to_bytes("RUDE/TOPBLURB"), original_blurb)
                self.assertEqual(
                    output_reader.extract_to_bytes("RUDE/NPC999"), original_dialogue)
            self.assertIn(b'999,"Custom Awards"', names)

    def test_new_dialogue_can_be_created_without_an_npc_placement(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "RUDE.REZ")
            work_dir = os.path.join(tmp, "output")
            write_rude_archive(source_rez)
            project = P.Project(rude_rez_path=source_rez, work_dir=work_dir)
            metadata = rude.RudeDialogueMetadata(
                npc_nbr=437,
                name="Independent NPC",
                initial_state=50,
                opening_blurb="Independent greeting",
            )

            asset = project.create_rude_asset(rude.make_simple_dialogue(
                metadata,
                [("Question", "Answer")],
            ))
            plan = project.save_plan()

            self.assertTrue(asset.is_new)
            self.assertEqual(project.levels, [])
            self.assertEqual(plan.dats, [])
            self.assertEqual(plan.rude_entries, [])
            self.assertEqual(
                plan.rude_archive_patch().entries,
                ["RUDE/NPCNAME", "RUDE/TOPBLURB", "RUDE/NPC437"],
            )

            project.execute(plan)
            output_rez = os.path.join(work_dir, plan.batch_id, "data", "RUDE.REZ")
            with rezmgr.RezReader(output_rez) as reader:
                self.assertIsNotNone(reader.find("RUDE/NPC437.RUDE"))
                catalog = rude.RudeMetadataCatalog.from_bytes(
                    reader.extract_to_bytes("RUDE/NPCNAME"),
                    reader.extract_to_bytes("RUDE/TOPBLURB"),
                )
                dialogue = rude.RudeDialogue.from_bytes(
                    catalog.metadata_for(437),
                    reader.extract_to_bytes("RUDE/NPC437"),
                )
            self.assertEqual(dialogue.metadata.initial_state, 50)
            self.assertEqual(dialogue.state(50).choices[0].npc_response, "Answer")

    def test_exact_installed_new_asset_is_reconciled_instead_of_conflicting(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "RUDE.REZ")
            work_dir = os.path.join(tmp, "output")
            write_rude_archive(source_rez)
            project = P.Project(rude_rez_path=source_rez, work_dir=work_dir)
            metadata = rude.RudeDialogueMetadata(
                npc_nbr=437,
                name="Installed NPC",
                initial_state=437,
                opening_blurb="Hello",
            )
            asset = project.create_rude_asset(rude.make_simple_dialogue(
                metadata,
                [("Hello.", "Well met!"), ("Goodbye.", "Safe travels.")],
            ))
            first_plan = project.save_plan()
            project.execute(first_plan)
            output_rez = first_plan.rude_archive_patch().output_archive
            shutil.copy2(output_rez, source_rez)

            # Even a detached copy of the old "new" asset sees its exact
            # installed output as idempotent, not as an ID collision.
            overlay = project.build_rude_overlay_entries(
                asset_edits=[copy.deepcopy(asset)],
            )
            self.assertEqual(overlay, {})

            self.assertIs(project.open_rude_asset(437), asset)
            self.assertFalse(asset.is_new)
            self.assertFalse(asset.is_dirty)

            # Subsequent edits now use the installed resource as their normal
            # optimistic-concurrency baseline.
            asset.dialogue.choices_in_file_order[0].npc_response = "Changed"
            second_plan = project.save_plan()
            self.assertEqual(
                second_plan.rude_archive_patch().entries,
                ["RUDE/NPC437"],
            )

    def test_independent_asset_round_trips_through_project_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "RUDE.REZ")
            project_path = os.path.join(tmp, "quest.mm9mod")
            write_rude_archive(source_rez)
            project = P.Project(rude_rez_path=source_rez)
            asset = project.open_rude_asset(998)
            choice = asset.dialogue.choices_in_file_order[0]
            choice.conditions = rude.RudeKeyConditions(
                required=(10, 20, 0, 0, 0),
                forbidden=(30, 0, 0, 0, 0),
            )
            choice.effects = rude.RudeKeyEffects(
                granted=(40, 0, 0, 0, 0),
                removed=(50, 0, 0, 0, 0),
            )
            choice.action = rude.RudeAction.native(rude.RudeNativeAction.TRAVEL)

            project_io.project_to_json(project, project_path)
            loaded = P.Project(rude_rez_path=source_rez)
            log = project_io.project_from_json(project_path, loaded)
            restored = loaded.rude_assets[998]
            restored_choice = restored.dialogue.choices_in_file_order[0]

            self.assertIn("loaded 1 independent RUDE asset(s)", log)
            self.assertTrue(restored.is_dirty)
            self.assertEqual(restored_choice.conditions.required_keys, (10, 20))
            self.assertEqual(restored_choice.conditions.forbidden_keys, (30,))
            self.assertEqual(restored_choice.effects.granted_keys, (40,))
            self.assertEqual(restored_choice.effects.removed_keys, (50,))
            self.assertEqual(
                restored_choice.action.native_action,
                rude.RudeNativeAction.TRAVEL,
            )


if __name__ == "__main__":
    unittest.main()
