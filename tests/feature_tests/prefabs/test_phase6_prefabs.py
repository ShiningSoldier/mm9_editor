import os
import tempfile
import unittest

from core import project as P
from core import project_io
from core import rezmgr
from features.prefabs.behavioral import (
    PHASE6_BEHAVIORAL_CLASSES,
    PHASE6_REVIEWED_SCRIPTS,
    analyze_prefab,
    build_behavioral_import_plan,
    build_script_import_assets,
    materialize_behavioral_plan,
)
from features.prefabs.graph import SupportState
from mm9_patcher import mm9_patch as patcher
from tests.core_tests.test_game_resources import write_minimal_rez
from tests.feature_tests.prefabs._fixtures import write_minimal_dat


def _prop(name, *, script_name="", script_params=""):
    return patcher.WorldObject("Prop", [
        patcher.Property("Name", 0, 0, name),
        patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
        patcher.Property("ScriptName", 0, 0, script_name),
        patcher.Property("ScriptParams", 0, 0, script_params),
    ])


def _prop_template():
    return _prop("")


class Phase6BehavioralPrefabTests(unittest.TestCase):
    def test_legacy_shooter_enum_two_retains_firebolt_runtime_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "Shooter.dat")
            write_minimal_dat(source, [], [patcher.WorldObject("Shooter", [
                patcher.Property("Name", 0, 0, "Shooter1"),
                patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
                patcher.Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
                patcher.Property("Type", 6, 0, 1),
                patcher.Property("ShootInterval", 3, 0, 2.0),
                patcher.Property("StartOn", 5, 0, 1),
                patcher.Property("ProjectileType", 6, 0, 2),
                patcher.Property("LavaSpeed", 3, 0, 200.0),
                patcher.Property("ScriptName", 0, 0, ""),
                patcher.Property("ScriptParams", 0, 0, ""),
            ])])
            template = patcher.WorldObject("Shooter", [
                patcher.Property("Name", 0, 0, ""),
                patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
                patcher.Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
                patcher.Property("Type", 6, 0, 1),
                patcher.Property("ShootInterval", 3, 0, 0.0),
                patcher.Property("StartOn", 5, 0, 0),
                patcher.Property("ProjectileName", 0, 0, "FireBolt"),
                patcher.Property("LavaSpeed", 3, 0, 200.0),
                patcher.Property("ScriptName", 0, 0, ""),
                patcher.Property("ScriptParams", 0, 0, ""),
            ])
            catalog = {"classes": {"Shooter": {"object_lto": {
                "template_properties": [
                    {"name": prop.name, "code": prop.code}
                    for prop in template.props
                ],
            }}}}
            analysis = analyze_prefab(
                source,
                catalog=catalog,
                supported_classes=PHASE6_BEHAVIORAL_CLASSES,
            )
            plan = build_behavioral_import_plan(analysis, root_name="ImportedShooter")
            created = materialize_behavioral_plan(
                analysis,
                plan,
                class_templates={"Shooter": template},
            )

        self.assertEqual(analysis.behavioral_state, SupportState.BEHAVIORAL_READY)
        self.assertEqual(created[0].get("ProjectileName"), "FireBolt")
        self.assertIn(
            "Shooter.ProjectileType",
            next(
                item.message for item in analysis.diagnostics
                if item.code == "behavioral_obsolete_source_properties"
            ),
        )

    def test_other_legacy_shooter_enum_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "Shooter.dat")
            write_minimal_dat(source, [], [patcher.WorldObject("Shooter", [
                patcher.Property("Name", 0, 0, "Shooter1"),
                patcher.Property("ProjectileType", 6, 0, 7),
            ])])
            catalog = {"classes": {"Shooter": {"object_lto": {
                "template_properties": [{"name": "Name", "code": 0}],
            }}}}
            analysis = analyze_prefab(
                source,
                catalog=catalog,
                supported_classes=PHASE6_BEHAVIORAL_CLASSES,
            )

        self.assertEqual(analysis.behavioral_state, SupportState.BLOCKED)
        self.assertIn(
            "cannot use runtime default",
            " ".join(item.message for item in analysis.diagnostics),
        )

    def test_reviewed_script_rewrites_local_and_external_object_targets(self):
        script = (
            "GetObjectHandle Note1, NoteHandle\r\n"
            "GetObjectHandle Bell1, BellHandle\r\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "Organ.dat")
            write_minimal_dat(source, [], [
                _prop("MusicSwitch", script_name="tocatta.scr"),
                _prop("Note1"),
            ])
            loader = lambda _path: script
            analysis = analyze_prefab(
                source,
                supported_classes=PHASE6_BEHAVIORAL_CLASSES,
                allow_scripts=True,
                allowed_script_names=PHASE6_REVIEWED_SCRIPTS,
                script_loader=loader,
            )
            plan = build_behavioral_import_plan(
                analysis,
                root_name="ImportedOrgan",
                external_bindings={"Bell1": "ExistingBell"},
            )
            overrides, assets = build_script_import_assets(
                analysis,
                plan,
                operation_id="abcdef",
                script_loader=loader,
            )
            created = materialize_behavioral_plan(
                analysis,
                plan,
                class_templates={"Prop": _prop_template()},
                object_overrides=overrides,
            )

        self.assertEqual(analysis.behavioral_state, SupportState.ACTION_REQUIRED)
        self.assertTrue(plan.ready)
        rewritten = next(iter(assets.values()))
        self.assertIn("GetObjectHandle ImportedOrgan_Note1,", rewritten)
        self.assertIn("GetObjectHandle ExistingBell,", rewritten)
        self.assertIn("SCRIPTS\\MM9EDITOR", created[0].get("ScriptName"))

    def test_unreviewed_script_remains_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "UnknownScript.dat")
            write_minimal_dat(source, [], [_prop("Thing", script_name="custom.scr")])
            analysis = analyze_prefab(
                source,
                supported_classes=PHASE6_BEHAVIORAL_CLASSES,
                allow_scripts=True,
                allowed_script_names=PHASE6_REVIEWED_SCRIPTS,
                script_loader=lambda _path: "",
            )

        self.assertEqual(analysis.behavioral_state, SupportState.BLOCKED)
        self.assertIn(
            "behavioral_script_not_reviewed",
            {item.code for item in analysis.diagnostics},
        )

    def test_propanim_is_passed_through_with_its_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "Shopkeeper.dat")
            write_minimal_dat(source, [], [
                _prop(
                    "Shopkeeper",
                    script_name="PropAnim.scr",
                    script_params="Innkeeper2",
                )
            ])
            analysis = analyze_prefab(
                source,
                supported_classes=PHASE6_BEHAVIORAL_CLASSES,
                allow_scripts=True,
                allowed_script_names=PHASE6_REVIEWED_SCRIPTS,
                script_loader=lambda _path: "GetParam 0 Params\r\nLoopAnim Params 0\r\n",
            )
            plan = build_behavioral_import_plan(analysis, root_name="ImportedShopkeeper")
            overrides, assets = build_script_import_assets(
                analysis,
                plan,
                operation_id="shop",
                script_loader=lambda _path: "GetParam 0 Params\r\nLoopAnim Params 0\r\n",
            )
            created = materialize_behavioral_plan(
                analysis,
                plan,
                class_templates={"Prop": _prop_template()},
                object_overrides=overrides,
            )

        self.assertTrue(plan.ready)
        self.assertEqual(created[0].get("ScriptName"), "PropAnim.scr")
        self.assertEqual(created[0].get("ScriptParams"), "Innkeeper2")
        self.assertEqual(assets, {})

    def test_script_sources_and_assets_round_trip_with_operation(self):
        op = P.ImportBehavioralPrefabOp(
            prefab_path="PipeOrgan.ed",
            root_name="ImportedOrgan",
            script_sources={"SCRIPTS\\TOCATTA.SCR": "source"},
            script_assets={"SCRIPTS\\MM9EDITOR\\TEST.SCR": "generated"},
        )
        restored = project_io.dict_to_op(project_io.op_to_dict(op))

        self.assertEqual(restored.script_sources, op.script_sources)
        self.assertEqual(restored.script_assets, op.script_assets)

    def test_generated_scripts_are_added_to_complete_staged_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "SCRIPTS.REZ")
            output = os.path.join(tmp, "out", "data", "SCRIPTS.REZ")
            write_minimal_rez(source, {"SCRIPTS/EXISTING.SCR": b"existing"})
            patch = P.ArchivePatch(
                source_archive=source,
                output_archive=output,
                entries=["SCRIPTS/MM9EDITOR/PREFAB_TEST.SCR"],
                kind="behavioral_scripts",
                additions={
                    "SCRIPTS/MM9EDITOR/PREFAB_TEST.SCR": b"GetObjectHandle Imported, H\r\n"
                },
            )
            P.Project(work_dir=os.path.join(tmp, "out")).execute_behavioral_scripts_rez(
                patch
            )
            with rezmgr.RezReader(output) as reader:
                existing = reader.extract_to_bytes("SCRIPTS/EXISTING.SCR")
                generated = reader.extract_to_bytes(
                    "SCRIPTS/MM9EDITOR/PREFAB_TEST.SCR"
                )

        self.assertEqual(existing, b"existing")
        self.assertIn(b"Imported", generated)

    def test_save_plan_collects_active_generated_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "SCRIPTS.REZ")
            write_minimal_rez(source, {"SCRIPTS/EXISTING.SCR": b"existing"})
            op = P.ImportBehavioralPrefabOp(
                prefab_path="PipeOrgan.ed",
                root_name="ImportedOrgan",
                script_assets={
                    "SCRIPTS\\MM9EDITOR\\PREFAB_TEST.SCR": "generated"
                },
            )
            world = patcher.World(
                patcher.Header(66, 0, 0, (0,) * 8),
                b"",
                [],
                b"",
            )
            level = P.LevelEdit(
                path="unused",
                source_kind="file",
                world=world,
                ops=[op],
            )
            dat_write = P.DatWrite(
                source_path="unused",
                output_path="unused",
                ops_summary=[],
                materialized=world,
                level_edit=level,
            )
            plan = P.SavePlan(batch_id="batch", dats=[dat_write])
            project = P.Project(
                scripts_rez_path=source,
                work_dir=os.path.join(tmp, "output"),
            )
            project._populate_archive_patches(plan)

        script_patch = plan.behavioral_scripts_archive_patch()
        self.assertIsNotNone(script_patch)
        self.assertEqual(script_patch.kind, "behavioral_scripts")
        self.assertIn(
            "SCRIPTS\\MM9EDITOR\\PREFAB_TEST.SCR",
            script_patch.additions,
        )


if __name__ == "__main__":
    unittest.main()
