import json
import os
import tempfile
import types
import unittest


from tests._path import ROOT  # noqa: F401

import _path_setup  # noqa: F401
import mm9_patch as patcher
from core import install_manager
from core import project as P
from core import rezmgr
from core import rude
from core import run_world
from tests.core_tests.test_game_resources import write_minimal_rez
from tests.core_tests.test_project_rez_output import (
    load_world_from_bytes,
    make_world_bytes,
)


class _FakeProject:
    def __init__(self, dat_bytes=b"preview dat", overlay_entries=None, issues=None):
        self.dat_bytes = dat_bytes
        self.overlay_entries = dict(overlay_entries or {})
        self.issues = list(issues or [])

    def build_runtime_dat(self, level):
        return self.dat_bytes, types.SimpleNamespace(
            blocking_issues=self.issues,
            validation_warnings=["preview warning"],
        )

    def build_runtime_overlay_entries(self, level):
        return dict(self.overlay_entries)


def _make_game_root(root):
    game_root = os.path.join(root, "Might and Magic 9")
    data_dir = os.path.join(game_root, "data")
    os.makedirs(data_dir)
    for name in ("worlds.rez", "data.rez"):
        with open(os.path.join(data_dir, name), "wb") as handle:
            handle.write(b"rez")
    with open(os.path.join(game_root, "lithtech.exe"), "wb") as handle:
        handle.write(b"exe")
    with open(os.path.join(game_root, "autoexec.cfg"), "w", encoding="ascii") as handle:
        handle.write('"Renderer" "display"\n')
    fonts = os.path.join(game_root, "Fonts")
    os.makedirs(fonts)
    with open(os.path.join(fonts, "test.fnt"), "wb") as handle:
        handle.write(b"font")
    os.makedirs(os.path.join(game_root, "DATA"), exist_ok=True)
    with open(os.path.join(game_root, "rez.txt"), "w", encoding="ascii") as handle:
        handle.write("data\\worlds.rez\n")
        handle.write("data\\data.rez\n")
        handle.write("DATA\n")
    return game_root


class RunWorldTests(unittest.TestCase):
    def test_normalize_world_path(self):
        self.assertEqual(
            run_world.normalize_world_path(r"WORLDS\DUNGEONS\KEEP.DAT"),
            (r"WORLDS\DUNGEONS\KEEP.DAT", r"worlds\DUNGEONS\KEEP"),
        )
        self.assertEqual(
            run_world.normalize_world_path("worlds/BOOTCAMP"),
            (r"worlds\BOOTCAMP.DAT", r"worlds\BOOTCAMP"),
        )

    def test_normalize_world_path_rejects_unsafe_or_non_world_paths(self):
        for value in (
            r"WORLDS\..\SaveGames\slot",
            r"C:\WORLDS\BOOTCAMP",
            r"RUDE\NPC1",
            r"WORLDS\BOOTCAMP.ED",
            r"WORLDS\\BOOTCAMP",
        ):
            with self.subTest(value=value):
                with self.assertRaises(run_world.RunWorldError):
                    run_world.normalize_world_path(value)

    def test_runtime_loose_resource_path_preserves_rude_type(self):
        self.assertEqual(
            run_world.runtime_loose_resource_path(r"RUDE\NPC438"),
            r"RUDE\NPC438.RUDE",
        )
        self.assertEqual(
            run_world.runtime_loose_resource_path(r"RUDE\NPCNAME.rude"),
            r"RUDE\NPCNAME.RUDE",
        )
        self.assertEqual(
            run_world.runtime_loose_resource_path(
                r"SCRIPTS\MM9EDITOR\NPC438_RUDE.SCR"
            ),
            r"SCRIPTS\MM9EDITOR\NPC438_RUDE.SCR",
        )

    def test_stage_current_level_builds_isolated_overlay_and_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            game_root = _make_game_root(tmp)
            conversion_data = os.path.join(tmp, "conversion", "data")
            os.makedirs(conversion_data)
            conversion_models = os.path.join(conversion_data, "MODELS.REZ")
            with open(conversion_models, "wb") as handle:
                handle.write(b"converted models")
            level = types.SimpleNamespace(
                rez_vpath=r"WORLDS\BOOTCAMP",
                conversion_stage_dir=os.path.dirname(conversion_data),
            )
            project = _FakeProject(
                dat_bytes=b"current in-memory dat",
                overlay_entries={
                    r"SCRIPTS\MM9EDITOR\TEST.SCR": b"script text",
                    r"RUDE\NPC438": b"dialogue",
                    r"RUDE\NPCNAME": b"names",
                    r"RUDE\TOPBLURB": b"blurbs",
                },
            )

            session = run_world.stage_current_level(
                project,
                level,
                game_root=game_root,
                staging_root=os.path.join(tmp, "output"),
            )

            with open(session.staged_dat, "rb") as handle:
                self.assertEqual(handle.read(), b"current in-memory dat")
            with open(
                os.path.join(session.overlay_dir, "SCRIPTS", "MM9EDITOR", "TEST.SCR"),
                "rb",
            ) as handle:
                self.assertEqual(handle.read(), b"script text")
            with open(
                os.path.join(session.overlay_dir, "RUDE", "NPC438.RUDE"),
                "rb",
            ) as handle:
                self.assertEqual(handle.read(), b"dialogue")
            self.assertFalse(os.path.exists(
                os.path.join(session.overlay_dir, "RUDE", "NPC438")
            ))
            with open(
                os.path.join(session.session_dir, "preview.json"),
                "r",
                encoding="utf-8",
            ) as handle:
                preview = json.load(handle)
            self.assertIn(
                {
                    "logical_path": r"RUDE\NPC438",
                    "loose_path": r"RUDE\NPC438.RUDE",
                },
                preview["overlay_resources"],
            )
            for name in ("NPCNAME.RUDE", "TOPBLURB.RUDE"):
                self.assertTrue(os.path.isfile(
                    os.path.join(session.overlay_dir, "RUDE", name)
                ))
            self.assertTrue(os.path.isfile(os.path.join(session.session_dir, "autoexec.cfg")))
            self.assertTrue(os.path.isfile(os.path.join(session.session_dir, "Fonts", "test.fnt")))
            for name in ("Minisaves", "SaveGames", "Saves"):
                self.assertTrue(os.path.isdir(os.path.join(session.session_dir, name)))
            self.assertFalse(os.path.exists(os.path.join(session.session_dir, "rez.txt")))
            self.assertEqual(session.resource_paths[-2], os.path.abspath(conversion_models))
            self.assertEqual(session.resource_paths[-1], session.overlay_dir)
            self.assertEqual(session.command[-2:], ("+runworld", r"worlds\BOOTCAMP"))
            self.assertEqual(session.command.count("-rez"), len(session.resource_paths))

    def test_stage_blocks_known_runtime_compatibility_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            game_root = _make_game_root(tmp)
            project = _FakeProject(issues=[{
                "code": "unsupported_actor",
                "message": "Unsupported actor remains",
            }])
            level = types.SimpleNamespace(
                rez_vpath="WORLDS/LEVEL1",
                conversion_stage_dir="",
            )
            with self.assertRaisesRegex(
                run_world.RunWorldError,
                "Unsupported actor remains",
            ):
                run_world.stage_current_level(
                    project,
                    level,
                    game_root=game_root,
                    staging_root=os.path.join(tmp, "output"),
                )

    def test_stage_rejects_logical_resources_with_one_typed_loose_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            game_root = _make_game_root(tmp)
            project = _FakeProject(overlay_entries={
                r"RUDE\NPC438": b"first",
                r"RUDE\NPC438.RUDE": b"second",
            })
            level = types.SimpleNamespace(
                rez_vpath="WORLDS/LEVEL1",
                conversion_stage_dir="",
            )

            with self.assertRaisesRegex(
                run_world.RunWorldError,
                "map to the same loose file",
            ):
                run_world.stage_current_level(
                    project,
                    level,
                    game_root=game_root,
                    staging_root=os.path.join(tmp, "output"),
                )

    def test_launch_uses_argument_list_and_isolated_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            game_root = _make_game_root(tmp)
            calls = []
            fake_process = types.SimpleNamespace(poll=lambda: None)

            def fake_popen(args, **kwargs):
                calls.append((args, kwargs))
                return fake_process

            session = run_world.launch_current_level(
                _FakeProject(),
                types.SimpleNamespace(
                    rez_vpath="WORLDS/LEVEL1",
                    conversion_stage_dir="",
                ),
                game_root=game_root,
                staging_root=os.path.join(tmp, "output"),
                popen_factory=fake_popen,
            )

            self.assertIs(session.process, fake_process)
            self.assertEqual(calls[0][0], list(session.command))
            self.assertEqual(calls[0][1], {"cwd": session.session_dir})
            self.assertNotEqual(
                os.path.normcase(session.session_dir),
                os.path.normcase(game_root),
            )

    def test_project_runtime_dat_uses_current_unsaved_level_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "data", "WORLDS.REZ")
            write_minimal_rez(source_rez, {
                "WORLDS/LEVEL1": make_world_bytes("Before"),
            })
            project = P.Project()
            level = project.add_level_from_rez(source_rez, "WORLDS/LEVEL1")
            level.append_op(P.EditOp(
                target_index=0,
                overrides={"Name": "Unsaved preview"},
            ))

            data, write = project.build_runtime_dat(level)
            world = load_world_from_bytes(data)

            self.assertEqual(world.objects[0].get("Name"), "Unsaved preview")
            self.assertEqual(write.ops_summary, ["~ edit object[0] (Name)"])

    def test_saved_and_installed_dat_remains_the_runtime_preview_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            game_data = os.path.join(tmp, "game", "data")
            source_rez = os.path.join(game_data, "WORLDS.REZ")
            work_dir = os.path.join(tmp, "output")
            write_minimal_rez(source_rez, {
                "WORLDS/LEVEL1": make_world_bytes("Before"),
            })
            project = P.Project(work_dir=work_dir)
            level = project.add_level_from_rez(source_rez, "WORLDS/LEVEL1")
            template = patcher.WorldObject("TestObject", [
                patcher.Property("Name", 0, 0, "Added NPC"),
                patcher.Property("Pos", 1, 0, (1.0, 2.0, 3.0)),
            ])
            level.append_op(P.AddOp(template=template))

            unsaved_bytes, _write = project.build_runtime_dat(level)
            self.assertEqual(len(load_world_from_bytes(unsaved_bytes).objects), 2)

            plan = project.save_plan()
            project.execute(plan)
            committed = plan.dats[0].committed_dat_bytes
            self.assertIsNotNone(committed)
            level.bsp = object()
            level._editor_materialized_cache = ((0, "stale"), level.world)
            project.accept_save_plan(plan)

            self.assertEqual(level.ops, [])
            self.assertEqual(level.source_bytes(), committed)
            self.assertIsNotNone(level.bsp)
            self.assertIsNone(level._editor_materialized_cache)
            saved_preview, _write = project.build_runtime_dat(level)
            self.assertEqual(saved_preview, committed)
            self.assertEqual(
                load_world_from_bytes(saved_preview).objects[-1].get("Name"),
                "Added NPC",
            )

            install_manager.install_batch(
                os.path.join(work_dir, plan.batch_id),
                game_data,
                os.path.join(tmp, "install-backups"),
            )
            installed_preview, _write = project.build_runtime_dat(level)
            self.assertEqual(installed_preview, committed)

    def test_unaccepted_or_failed_save_plan_keeps_pending_level_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "data", "WORLDS.REZ")
            write_minimal_rez(source_rez, {
                "WORLDS/LEVEL1": make_world_bytes("Before"),
            })
            project = P.Project(work_dir=os.path.join(tmp, "output"))
            level = project.add_level_from_rez(source_rez, "WORLDS/LEVEL1")
            level.append_op(P.EditOp(
                target_index=0,
                overrides={"Name": "Pending"},
            ))
            plan = project.save_plan()

            with self.assertRaisesRegex(ValueError, "did not complete successfully"):
                project.accept_save_plan(plan)

            self.assertEqual(len(level.ops), 1)
            self.assertEqual(level.world.objects[0].get("Name"), "Before")

    def test_accept_save_plan_does_not_clear_an_unwritten_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "data", "WORLDS.REZ")
            write_minimal_rez(source_rez, {
                "WORLDS/LEVEL1": make_world_bytes("One"),
                "WORLDS/LEVEL2": make_world_bytes("Two"),
            })
            project = P.Project(work_dir=os.path.join(tmp, "output"))
            written = project.add_level_from_rez(source_rez, "WORLDS/LEVEL1")
            untouched = project.add_level_from_rez(source_rez, "WORLDS/LEVEL2")
            written.append_op(P.EditOp(
                target_index=0,
                overrides={"Name": "One saved"},
            ))
            redo = P.EditOp(
                target_index=0,
                overrides={"Name": "Two redo"},
            )
            untouched.redo_ops.append(redo)

            plan = project.save_plan()
            project.execute(plan)
            project.accept_save_plan(plan)

            self.assertEqual(written.ops, [])
            self.assertEqual(
                written.world.objects[0].get("Name"),
                "One saved",
            )
            self.assertEqual(untouched.redo_ops, [redo])
            self.assertEqual(untouched.world.objects[0].get("Name"), "Two")

    def test_project_runtime_overlay_contains_new_npc_rude_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            worlds_rez = os.path.join(tmp, "data", "WORLDS.REZ")
            rude_rez = os.path.join(tmp, "data", "RUDE.REZ")
            write_minimal_rez(worlds_rez, {
                "WORLDS/LEVEL1": make_world_bytes("Before"),
            })
            write_minimal_rez(rude_rez, {
                "RUDE/NPCNAME": b'1,"Yrsa"\n',
                "RUDE/TOPBLURB": b'1,1,"Hello"\n',
                "RUDE/NPC1": b"existing\n",
            })
            project = P.Project(rude_rez_path=rude_rez)
            level = project.add_level_from_rez(worlds_rez, "WORLDS/LEVEL1")
            level.append_op(P.AddOp(
                template=patcher.WorldObject("TestObject", []),
                rude={
                    "npc_nbr": 438,
                    "name": "Preview NPC",
                    "blurb": "Preview blurb",
                    "lines": [("Question", "Answer")],
                },
            ))

            entries = project.build_runtime_overlay_entries(level)

            self.assertIn(b'438,"Preview NPC"', entries["RUDE/NPCNAME"])
            self.assertIn(b'438,438,"Preview blurb"', entries["RUDE/TOPBLURB"])
            self.assertIn(b'"Question","Answer"', entries["RUDE/NPC438"])

    def test_fresh_npc_preview_survives_unsaved_save_and_install_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            game_root = _make_game_root(tmp)
            game_data = os.path.join(game_root, "data")
            worlds_rez = os.path.join(game_data, "worlds.rez")
            rude_rez = os.path.join(game_data, "rude.rez")
            write_minimal_rez(worlds_rez, {
                "WORLDS/LEVEL1": make_world_bytes("Before"),
            })
            write_minimal_rez(rude_rez, {
                "RUDE/NPCNAME": b'1,"Existing"\r\n',
                "RUDE/TOPBLURB": b'1,1,"Hello"\r\n',
                "RUDE/NPC1": b'existing\r\n',
            }, resource_type=rezmgr._restype_for_filename("NPC.RUDE"))
            with open(
                os.path.join(game_root, "rez.txt"),
                "a",
                encoding="ascii",
            ) as handle:
                handle.write("data\\rude.rez\n")

            work_dir = os.path.join(tmp, "output")
            project = P.Project(work_dir=work_dir, rude_rez_path=rude_rez)
            level = project.add_level_from_rez(worlds_rez, "WORLDS/LEVEL1")
            template = patcher.WorldObject("TestObject", [
                patcher.Property("Name", 0, 0, "Fresh NPC"),
                patcher.Property("Pos", 1, 0, (1.0, 2.0, 3.0)),
                patcher.Property("NPCNbr", 3, 0, 438),
            ])
            level.append_op(P.AddOp(template=template))
            metadata = rude.RudeDialogueMetadata(
                438, "Fresh NPC", 438, "Hail, traveler!",
            )
            project.create_rude_asset(rude.make_simple_dialogue(metadata, [
                ("Hello.", "Well met!"),
                ("Goodbye.", "Safe travels."),
            ]))

            unsaved = run_world.stage_current_level(
                project,
                level,
                game_root=game_root,
                staging_root=work_dir,
            )
            self.assertTrue(os.path.isfile(os.path.join(
                unsaved.overlay_dir, "RUDE", "NPC438.RUDE",
            )))
            self.assertEqual(
                patcher.World.load(unsaved.staged_dat).objects[-1].get("Name"),
                "Fresh NPC",
            )

            plan = project.save_plan()
            project.execute(plan)
            project.accept_save_plan(plan)
            saved = run_world.stage_current_level(
                project,
                level,
                game_root=game_root,
                staging_root=work_dir,
            )
            self.assertTrue(os.path.isfile(os.path.join(
                saved.overlay_dir, "RUDE", "NPC438.RUDE",
            )))
            self.assertEqual(
                patcher.World.load(saved.staged_dat).objects[-1].get("Name"),
                "Fresh NPC",
            )

            install_manager.install_batch(
                os.path.join(work_dir, plan.batch_id),
                game_data,
                os.path.join(tmp, "install-backups"),
            )
            installed = run_world.stage_current_level(
                project,
                level,
                game_root=game_root,
                staging_root=work_dir,
            )
            self.assertFalse(os.path.exists(os.path.join(
                installed.overlay_dir, "RUDE", "NPC438.RUDE",
            )))
            self.assertEqual(
                patcher.World.load(installed.staged_dat).objects[-1].get("Name"),
                "Fresh NPC",
            )
            self.assertFalse(project.rude_assets[438].is_dirty)

    def test_runtime_preview_accepts_the_projects_exact_installed_fresh_npc(self):
        with tempfile.TemporaryDirectory() as tmp:
            worlds_rez = os.path.join(tmp, "data", "WORLDS.REZ")
            rude_rez = os.path.join(tmp, "data", "RUDE.REZ")
            write_minimal_rez(worlds_rez, {
                "WORLDS/LEVEL1": make_world_bytes("FreshNpcObject"),
            })
            metadata = rude.RudeDialogueMetadata(
                437, "Fresh NPC", 437, "Hail, traveler!",
            )
            dialogue = rude.make_simple_dialogue(metadata, [
                ("Hello.", "Well met!"),
                ("Goodbye.", "Safe travels."),
            ])
            write_minimal_rez(rude_rez, {
                "RUDE/NPCNAME": b'437,"Fresh NPC"\r\n',
                "RUDE/TOPBLURB": b'437,437,"Hail, traveler!"\r\n',
                "RUDE/NPC437": dialogue.to_bytes(),
            })
            project = P.Project(rude_rez_path=rude_rez)
            project.rude_assets[437] = P.RudeAssetEdit(
                npc_nbr=437,
                dialogue=dialogue,
                source_virtual_path="RUDE/NPC437",
            )
            level = project.add_level_from_rez(worlds_rez, "WORLDS/LEVEL1")

            entries = project.build_runtime_overlay_entries(level)

            self.assertNotIn("RUDE/NPC437", entries)
            self.assertFalse(project.rude_assets[437].is_dirty)


if __name__ == "__main__":
    unittest.main()
