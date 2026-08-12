import os
import json
import sys
import tempfile
import unittest


from tests._path import ROOT  # noqa: F401

import _path_setup  # noqa: F401
import mm9_patch as patcher
from core import rezmgr as mm9_rezmgr
from core import project as P
from core import project_io
from tests.core_tests.test_game_resources import write_minimal_rez


def make_world_bytes(name: str) -> bytes:
    header = patcher.Header(66, 0, 0, (0,) * 8)
    obj = patcher.WorldObject("TestObject", [
        patcher.Property("Name", 0, 0, name),
        patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
    ])
    world = patcher.World(
        header=header,
        pre_objects=b"",
        objects=[obj],
        render_data=b"",
    )
    fd, path = tempfile.mkstemp(suffix=".DAT")
    os.close(fd)
    try:
        world.save(path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def load_world_from_bytes(data: bytes) -> patcher.World:
    fd, path = tempfile.mkstemp(suffix=".DAT")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(data)
        return patcher.World.load(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


class ProjectRezOutputTests(unittest.TestCase):
    def test_conversion_report_round_trips_with_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            write_minimal_rez(source_rez, {
                "WORLDS/LEVEL1": make_world_bytes("Orc1"),
            })
            project = P.Project(work_dir=os.path.join(tmp, "output"))
            level = project.add_level_from_rez(source_rez, "WORLDS/LEVEL1")
            level.conversion_report = {
                "unresolved_actor_classes": ["Orc"],
                "records": [{
                    "status": "unsupported_actor_preserved",
                    "output_index": 0,
                }],
            }
            level.conversion_stage_dir = os.path.join(tmp, "stage")
            level.preview_actor_visuals = {
                "princess": {
                    "model": r"models\princess.abc",
                    "skins": [r"skins\princessblue.dtx"],
                    "editor_preview_only": True,
                },
            }
            project_path = os.path.join(tmp, "conversion.mm9mod")
            project_io.project_to_json(project, project_path)

            loaded_project = P.Project(work_dir=os.path.join(tmp, "output2"))
            project_io.project_from_json(project_path, loaded_project)

            self.assertEqual(
                loaded_project.levels[0].conversion_report,
                level.conversion_report,
            )
            self.assertEqual(
                loaded_project.levels[0].conversion_stage_dir,
                level.conversion_stage_dir,
            )
            self.assertEqual(
                loaded_project.levels[0].preview_actor_visuals,
                level.preview_actor_visuals,
            )

    def test_conversion_blocker_clears_when_incompatible_actor_is_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            write_minimal_rez(source_rez, {
                "WORLDS/LEVEL1": make_world_bytes("Orc1"),
            })
            project = P.Project(work_dir=os.path.join(tmp, "output"))
            level = project.add_level_from_rez(source_rez, "WORLDS/LEVEL1")
            level.conversion_report = {
                "unresolved_actor_classes": ["Orc"],
                "records": [{
                    "status": "unsupported_actor_preserved",
                    "output_index": 0,
                }],
            }
            level.append_op(P.EditOp(target_index=0, overrides={"Name": "Still here"}))

            blocked_plan = project.save_plan()
            self.assertEqual(
                blocked_plan.dats[0].blocking_issues[0]["code"],
                "unsupported_lomm_actors",
            )

            level.append_op(P.DeleteOp(target_index=0))
            clean_plan = project.save_plan()
            self.assertEqual(clean_plan.dats[0].blocking_issues, [])

    def test_rez_save_writes_game_shaped_output_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            work_dir = os.path.join(tmp, "output")
            write_minimal_rez(source_rez, {
                "WORLDS/LEVEL1": make_world_bytes("Before"),
            })
            project = P.Project(work_dir=work_dir)
            level = project.add_level_from_rez(source_rez, "WORLDS/LEVEL1")
            level.append_op(P.EditOp(target_index=0, overrides={"Name": "After"}))

            plan = project.save_plan()
            log = project.execute(plan)

            expected_rez = os.path.join(work_dir, plan.batch_id, "data", "WORLDS.REZ")
            expected_entry = os.path.join(
                work_dir, plan.batch_id, "changed_entries", "WORLDS", "LEVEL1.DAT")
            self.assertEqual(plan.dats[0].output_path, expected_rez)
            self.assertTrue(os.path.isfile(expected_rez))
            self.assertTrue(os.path.isfile(expected_entry))
            self.assertTrue(any(line.startswith("wrote ") for line in log))

            with mm9_rezmgr.RezReader(expected_rez) as reader:
                changed = load_world_from_bytes(reader.extract_to_bytes("WORLDS/LEVEL1"))
            with mm9_rezmgr.RezReader(source_rez) as reader:
                original = load_world_from_bytes(reader.extract_to_bytes("WORLDS/LEVEL1"))

            self.assertEqual(changed.objects[0].get("Name"), "After")
            self.assertEqual(original.objects[0].get("Name"), "Before")

    def test_multiple_levels_in_same_rez_are_written_once_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            work_dir = os.path.join(tmp, "output")
            write_minimal_rez(source_rez, {
                "WORLDS/LEVEL1": make_world_bytes("One"),
                "WORLDS/LEVEL2": make_world_bytes("Two"),
            })
            project = P.Project(work_dir=work_dir)
            level1 = project.add_level_from_rez(source_rez, "WORLDS/LEVEL1")
            level2 = project.add_level_from_rez(source_rez, "WORLDS/LEVEL2")
            level1.append_op(P.EditOp(target_index=0, overrides={"Name": "OneChanged"}))
            level2.append_op(P.EditOp(target_index=0, overrides={"Name": "TwoChanged"}))

            plan = project.save_plan()
            project.execute(plan)

            expected_rez = os.path.join(work_dir, plan.batch_id, "data", "WORLDS.REZ")
            with mm9_rezmgr.RezReader(expected_rez) as reader:
                changed1 = load_world_from_bytes(reader.extract_to_bytes("WORLDS/LEVEL1"))
                changed2 = load_world_from_bytes(reader.extract_to_bytes("WORLDS/LEVEL2"))

            self.assertEqual(changed1.objects[0].get("Name"), "OneChanged")
            self.assertEqual(changed2.objects[0].get("Name"), "TwoChanged")

    def test_fresh_npc_registration_writes_rude_rez_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            worlds_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            rude_rez = os.path.join(tmp, "game", "data", "RUDE.REZ")
            work_dir = os.path.join(tmp, "output")
            write_minimal_rez(worlds_rez, {
                "WORLDS/LEVEL1": make_world_bytes("Before"),
            })
            write_minimal_rez(rude_rez, {
                "RUDE/NPCNAME": b'1,"Yrsa"\n',
                "RUDE/TOPBLURB": b'1,1,"Hello"\n',
                "RUDE/NPC1": b'1,1,1,"Goodbye.","Farewell.",-1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n',
            })

            project = P.Project(work_dir=work_dir, rude_rez_path=rude_rez)
            level = project.add_level_from_rez(worlds_rez, "WORLDS/LEVEL1")
            template = patcher.WorldObject("TestObject", [
                patcher.Property("Name", 0, 0, "New NPC"),
                patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
            ])
            level.append_op(P.AddOp(
                template=template,
                rude={
                    "npc_nbr": 438,
                    "name": "Test Peasant",
                    "blurb": "Hello! I'm an NPC.",
                    "lines": [("Are you heroes?", "Good!")],
                    "force": False,
                },
            ))

            plan = project.save_plan()
            self.assertIsNotNone(plan.rude_archive_patch())
            project.execute(plan)

            output_rude = os.path.join(work_dir, plan.batch_id, "data", "RUDE.REZ")
            self.assertTrue(os.path.isfile(output_rude))
            with mm9_rezmgr.RezReader(output_rude) as reader:
                npcname = reader.extract_to_bytes("RUDE/NPCNAME").decode("latin-1")
                topblurb = reader.extract_to_bytes("RUDE/TOPBLURB").decode("latin-1")
                npc438 = reader.extract_to_bytes("RUDE/NPC438").decode("latin-1")

            self.assertIn('438,"Test Peasant"', npcname)
            self.assertIn('438,438,"Hello! I\'m an NPC."', topblurb)
            self.assertIn('"Are you heroes?","Good!"', npc438)

            changed_copy = os.path.join(
                work_dir, plan.batch_id, "changed_entries", "RUDE", "NPC438.RUDE")
            self.assertTrue(os.path.isfile(changed_copy))

            manifest_path = os.path.join(work_dir, plan.batch_id, "manifest.json")
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            rude_archives = [a for a in manifest["archives"] if a["kind"] == "rude"]
            self.assertEqual(len(rude_archives), 1)
            self.assertIn("RUDE/NPC438", rude_archives[0]["entries"])

    def test_manifest_records_validation_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            work_dir = os.path.join(tmp, "output")
            write_minimal_rez(source_rez, {
                "WORLDS/LEVEL1": make_world_bytes("Before"),
            })
            project = P.Project(work_dir=work_dir)
            level = project.add_level_from_rez(source_rez, "WORLDS/LEVEL1")
            level.append_op(P.EditOp(target_index=0, overrides={"Name": "After"}))

            plan = project.save_plan()
            plan.dats[0].validation_warnings.append("test warning")
            project.execute(plan)

            manifest_path = os.path.join(work_dir, plan.batch_id, "manifest.json")
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["dats"][0]["validation_warnings"], ["test warning"])


if __name__ == "__main__":
    unittest.main()
