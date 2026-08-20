import os
import importlib.util
import json
import sys
import tempfile
import types
import unittest


from tests._path import ROOT  # noqa: F401

import _path_setup  # noqa: F401
from core import game_resources
from core import rezmgr
import mm9_patch as patcher
from core import project as P
from tests.core_tests.test_game_resources import write_minimal_rez


_EDITOR_PATH = os.path.join(ROOT, "mm9_editor.py")
_SPEC = importlib.util.spec_from_file_location("mm9_editor_app", _EDITOR_PATH)
mm9_editor_app = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(mm9_editor_app)


def make_world_bytes(object_type: str = "TemplateClass",
                     filename: str = "models/example.abc") -> bytes:
    header = patcher.Header(66, 0, 0, (0,) * 8)
    obj = patcher.WorldObject(object_type, [
        patcher.Property("Name", 0, 0, "Template"),
        patcher.Property("Pos", 1, 0, (1.0, 2.0, 3.0)),
        patcher.Property("Filename", 0, 0, filename),
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


class EditorResourceWorkflowTests(unittest.TestCase):
    def test_dat_to_ed_reserved_stair_command_detects_and_forwards_high_confidence_ids(self):
        calls = {}
        prompts = []
        physics_model = types.SimpleNamespace(name="PhysicsBSP")
        bsp_world = types.SimpleNamespace(world_models=[physics_model])
        assemblies = (
            types.SimpleNamespace(
                assembly_index=1,
                confidence="candidate",
                step_count=2,
                source_polygon_indices=(10, 11),
                generated_face_count=12,
                bounds_min=(0.0, 0.0, 0.0),
                bounds_max=(16.0, 8.0, 16.0),
            ),
            types.SimpleNamespace(
                assembly_index=3,
                confidence="high",
                step_count=7,
                source_polygon_indices=(30, 31, 32),
                generated_face_count=24,
                bounds_min=(32.0, 0.0, 64.0),
                bounds_max=(96.0, 56.0, 128.0),
            ),
        )

        class FakeSimpleDialog:
            @staticmethod
            def askstring(title, body, **_kwargs):
                prompts.append((title, body))
                return "3"

        class FakeMessagebox:
            @staticmethod
            def showinfo(title, body):
                raise AssertionError(f"unexpected info: {title}: {body}")

            @staticmethod
            def showerror(title, body):
                raise AssertionError(f"unexpected error: {title}: {body}")

            @staticmethod
            def showwarning(title, body):
                raise AssertionError(f"unexpected warning: {title}: {body}")

        fake_reconstruction = types.SimpleNamespace(
            physics_shell_candidates=lambda model: (
                calls.setdefault("candidate_model", model),
            ),
            detect_physics_shell_stair_assemblies=lambda model, candidates: (
                calls.setdefault("detector_input", (model, candidates)),
                assemblies,
            )[1],
        )
        method = mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat_with_stair_assemblies
        method_globals = method.__globals__
        old_simpledialog = method_globals.get("simpledialog")
        old_messagebox = method_globals.get("messagebox")
        old_reconstruction = method_globals.get("terrain_reconstruction")
        try:
            method_globals["simpledialog"] = FakeSimpleDialog
            method_globals["messagebox"] = FakeMessagebox
            method_globals["terrain_reconstruction"] = fake_reconstruction
            app = object.__new__(mm9_editor_app.EditorApp)
            app.active = types.SimpleNamespace(get_bsp=lambda: bsp_world)
            app.root = None
            app.cmd_generate_dedit_ed_from_dat = lambda **kwargs: calls.setdefault(
                "generation_kwargs", kwargs
            )
            method(app)
        finally:
            method_globals["simpledialog"] = old_simpledialog
            method_globals["messagebox"] = old_messagebox
            method_globals["terrain_reconstruction"] = old_reconstruction

        self.assertIs(calls["candidate_model"], physics_model)
        self.assertEqual(
            calls["generation_kwargs"]["physics_shell_stair_assembly_indices"],
            (3,),
        )
        self.assertIn("1: candidate", prompts[0][1])
        self.assertIn("[inspection only]", prompts[0][1])
        self.assertIn("3: high", prompts[0][1])
        self.assertIn("Eligible IDs: 3", prompts[0][1])

    def test_source_ed_object_class_probe_distinguishes_anskramkeep_and_bathhouse(self):
        anskramkeep = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.ED")
        bathhouse = os.path.join(ROOT, "mm9_data", "WORLDS", "BATHHOUSE.ED")
        if not os.path.exists(anskramkeep) or not os.path.exists(bathhouse):
            self.skipTest("missing source ED oracle fixture")

        self.assertFalse(
            mm9_editor_app.EditorApp._source_ed_has_object_class(
                anskramkeep,
                "DestructableProp",
            )
        )
        self.assertTrue(
            mm9_editor_app.EditorApp._source_ed_has_object_class(
                bathhouse,
                "DestructableProp",
            )
        )

    def _dummy_app(self, archives, catalog):
        app = object.__new__(mm9_editor_app.EditorApp)
        app.resources = game_resources.GameResources(archives=archives)
        app.project = P.Project()
        app.catalog = catalog
        app.cfg = types.SimpleNamespace()
        app.view3d = None
        return app

    def test_template_lookup_loads_source_level_through_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            worlds_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            write_minimal_rez(worlds_rez, {
                "WORLDS/SOURCE": make_world_bytes(),
            })
            catalog = {
                "classes": {
                    "TemplateClass": {
                        "template": {"source_level": "SOURCE.DAT"},
                        "levels": ["SOURCE.DAT"],
                    },
                },
                "filenames": {},
            }
            app = self._dummy_app({"worlds": worlds_rez}, catalog)

            obj = mm9_editor_app.EditorApp._find_template_for_class(
                app, "TemplateClass")

            self.assertIsNotNone(obj)
            self.assertEqual(obj.type_str, "TemplateClass")

    def test_filename_lookup_loads_source_level_through_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            worlds_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            write_minimal_rez(worlds_rez, {
                "WORLDS/SOURCE": make_world_bytes(
                    object_type="Prop",
                    filename="models/example.abc"),
            })
            catalog = {
                "classes": {},
                "filenames": {
                    "models/example.abc": {
                        "levels": ["SOURCE.DAT"],
                        "classes": ["Prop"],
                    },
                },
            }
            app = self._dummy_app({"worlds": worlds_rez}, catalog)

            obj = mm9_editor_app.EditorApp._find_template_for_filename(
                app, "models/example.abc")

            self.assertIsNotNone(obj)
            self.assertEqual(obj.type_str, "Prop")

    def test_next_npc_number_is_read_from_rude_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            rude_rez = os.path.join(tmp, "game", "data", "RUDE.REZ")
            write_minimal_rez(rude_rez, {
                f"RUDE/NPC{n}": b""
                for n in (1, 2, 4, 997, 998, 999)
            })
            app = self._dummy_app({"rude": rude_rez}, {"classes": {}, "filenames": {}})

            n = mm9_editor_app.EditorApp._suggest_next_npc_nbr(app)

            self.assertEqual(n, 3)

    def test_fresh_npc_placement_creates_separate_rude_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            rude_rez = os.path.join(tmp, "game", "data", "RUDE.REZ")
            write_minimal_rez(rude_rez, {
                "RUDE/NPCNAME": b'1,"Existing"\r\n',
                "RUDE/TOPBLURB": b'1,1,"Hello"\r\n',
                "RUDE/NPC1": (
                    b'1,1,1,"Bye","Bye",-1,'
                    + b','.join([b"0"] * 24)
                    + b'\r\n'
                ),
            }, resource_type=rezmgr._restype_for_filename("NPC.RUDE"))
            header = patcher.Header(66, 0, 0, (0,) * 8)
            level = P.LevelEdit(
                path="dummy.dat",
                world=patcher.World(
                    header=header,
                    pre_objects=b"",
                    objects=[],
                    render_data=b"",
                ),
            )
            template = patcher.WorldObject("Civilian", [
                patcher.Property("Name", 0, 0, "Template"),
                patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
                patcher.Property("NPCNbr", 6, 0, 0),
            ])
            app = object.__new__(mm9_editor_app.EditorApp)
            app.active = level
            app.project = P.Project(rude_rez_path=rude_rez)
            app.view3d = None
            app._pending_template = template
            app._pending_kind = "class"
            app._pending_rude_config = {
                "mode": "fresh",
                "npc_nbr": 437,
                "name": "Independent NPC",
                "blurb": "Hello independently",
                "lines": [("Question", "Answer")],
                "force": False,
            }
            app._refresh_after_edit = lambda _index: None

            mm9_editor_app.EditorApp._place_pending_at_pos(
                app, [1.0, 2.0, 3.0])

            self.assertEqual(len(level.ops), 1)
            self.assertIsNone(level.ops[0].rude)
            self.assertIn(437, app.project.rude_assets)
            self.assertTrue(app.project.rude_assets[437].is_new)
            self.assertEqual(app.project.next_npc_nbr, 438)

    def test_lomm_conversion_command_opens_dialog_with_detected_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            opened = {}

            class FakeDialog:
                @staticmethod
                def open(parent, **kwargs):
                    opened["parent"] = parent
                    opened.update(kwargs)

            method_globals = mm9_editor_app.EditorApp.cmd_lomm_to_mm9_conversion.__globals__
            old_dialog = method_globals.get("LommConversionDialog")
            try:
                method_globals["LommConversionDialog"] = FakeDialog
                app = self._dummy_app({}, {"classes": {}, "filenames": {}})
                app.root = object()
                app.editor_settings = {
                    "last_lomm_root": os.path.join(tmp, "LoMM"),
                }
                os.makedirs(app.editor_settings["last_lomm_root"])
                app.cfg = types.SimpleNamespace(
                    game_root=tmp,
                    backup_root=os.path.join(tmp, "backups"),
                )
                app.catalog_path = os.path.join(tmp, "catalog.json")

                mm9_editor_app.EditorApp.cmd_lomm_to_mm9_conversion(app)
            finally:
                method_globals["LommConversionDialog"] = old_dialog

            self.assertEqual(opened["parent"], app.root)
            self.assertEqual(opened["mm9_root"], tmp)
            self.assertEqual(opened["backup_root"], os.path.join(tmp, "backups"))
            self.assertEqual(opened["catalog_json"], os.path.join(tmp, "catalog.json"))
            self.assertTrue(opened["lomm_catalog_json"].endswith("catalog_lomm.json"))
            self.assertEqual(opened["initial_lomm_root"], os.path.join(tmp, "LoMM"))
            self.assertTrue(callable(opened["on_success"]))

    def test_converted_level_prefers_staged_models_and_independently_falls_back_skins(self):
        with tempfile.TemporaryDirectory() as tmp:
            live_data = os.path.join(tmp, "live", "data")
            stage_dir = os.path.join(tmp, "stage")
            stage_data = os.path.join(stage_dir, "data")
            live_models = os.path.join(live_data, "MODELS.REZ")
            live_skins = os.path.join(live_data, "SKINS.REZ")
            staged_models = os.path.join(stage_data, "MODELS.REZ")
            write_minimal_rez(live_models, {"MODELS/LIVE.ABC": b"live-model"})
            write_minimal_rez(live_skins, {"SKINS/LIVE.DTX": b"live-skin"})
            write_minimal_rez(staged_models, {"MODELS/STAGED.ABC": b"staged-model"})

            updates = []
            visual_updates = []

            class FakeView:
                def update_asset_directories(self, **kwargs):
                    updates.append(kwargs)

                def update_actor_visuals(self, actor_visuals):
                    visual_updates.append(actor_visuals)

            app = object.__new__(mm9_editor_app.EditorApp)
            app.resources = game_resources.GameResources(
                archives={"models": live_models, "skins": live_skins},
                cache_dir=os.path.join(tmp, "cache"),
            )
            app.catalog = {
                "actor_visuals": {
                    "mm9actor": {"model": r"models\live.abc"},
                },
            }
            app.view3d = FakeView()
            converted = types.SimpleNamespace(
                conversion_stage_dir=stage_dir,
                preview_actor_visuals={
                    "princess": {
                        "model": r"models\princess.abc",
                        "skins": [r"skins\princessblue.dtx"],
                    },
                },
            )

            mm9_editor_app.EditorApp._update_view_assets_for_level(app, converted)
            converted_update = updates[-1]
            self.assertTrue(os.path.isfile(os.path.join(
                converted_update["models_dir"], "STAGED.ABC"
            )))
            self.assertTrue(os.path.isfile(os.path.join(
                converted_update["skins_dir"], "LIVE.DTX"
            )))
            self.assertIn("mm9actor", visual_updates[-1])
            self.assertEqual(
                visual_updates[-1]["princess"]["model"],
                r"models\princess.abc",
            )

            ordinary = types.SimpleNamespace(
                conversion_stage_dir="",
                preview_actor_visuals={},
            )
            mm9_editor_app.EditorApp._update_view_assets_for_level(app, ordinary)
            ordinary_update = updates[-1]
            self.assertTrue(os.path.isfile(os.path.join(
                ordinary_update["models_dir"], "LIVE.ABC"
            )))
            self.assertNotEqual(
                converted_update["models_dir"], ordinary_update["models_dir"]
            )
            self.assertNotIn("princess", visual_updates[-1])

    def test_lomm_conversion_success_opens_new_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            worlds_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            write_minimal_rez(worlds_rez, {
                "WORLDS/NEWLEVEL": make_world_bytes("Converted"),
            })
            app = self._dummy_app({"worlds": worlds_rez}, {"classes": {}, "filenames": {}})

            class FakeCombo(dict):
                def __setitem__(self, key, value):
                    super().__setitem__(key, value)

            class FakeVar:
                def __init__(self):
                    self.value = None

                def set(self, value):
                    self.value = value

            selected = {}
            app.level_combo = FakeCombo()
            app.level_var = FakeVar()
            app._set_active = lambda level: selected.setdefault("level", level)

            result = types.SimpleNamespace(
                worlds_rez=worlds_rez,
                added_virtual_path="WORLDS/NEWLEVEL",
                stage_dir=os.path.join(tmp, "stage"),
                conversion=types.SimpleNamespace(
                    stats=types.SimpleNamespace(
                        compatibility=types.SimpleNamespace(
                            actor_policy="preserve",
                            source_registry="LoMM",
                            target_registry="MM9",
                            registry_warnings=[],
                            status_counts={},
                            unresolved_actor_count=1,
                            unresolved_actor_classes=["Princess"],
                            records=[],
                        ),
                        preview_actor_visuals={
                            "princess": {
                                "model": r"models\princess.abc",
                                "skins": [r"skins\princessblue.dtx"],
                            },
                        },
                    ),
                ),
            )
            app._remember_lomm_root = lambda value: selected.setdefault("lomm_root", value)

            mm9_editor_app.EditorApp._on_lomm_conversion_success(
                app,
                result,
                r"C:\games\Legends of Might and Magic",
            )

            self.assertEqual(app.level_combo["values"], ["WORLDS/NEWLEVEL"])
            self.assertEqual(app.level_var.value, "WORLDS/NEWLEVEL")
            self.assertEqual(selected["level"].rez_vpath, "WORLDS/NEWLEVEL")
            self.assertEqual(selected["lomm_root"], r"C:\games\Legends of Might and Magic")
            self.assertEqual(
                selected["level"].preview_actor_visuals["princess"]["model"],
                r"models\princess.abc",
            )
            self.assertEqual(
                selected["level"].conversion_stage_dir,
                os.path.join(tmp, "stage"),
            )

    def test_lomm_root_setting_round_trips_through_editor_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            lomm_root = os.path.join(tmp, "LoMM")
            os.makedirs(lomm_root)
            app = object.__new__(mm9_editor_app.EditorApp)
            app.settings_path = os.path.join(tmp, "editor_settings.json")
            app.editor_settings = {}

            mm9_editor_app.EditorApp._remember_lomm_root(app, lomm_root)

            with open(app.settings_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["settings"]["last_lomm_root"], os.path.abspath(lomm_root))

            app2 = object.__new__(mm9_editor_app.EditorApp)
            app2.settings_path = app.settings_path
            loaded = mm9_editor_app.EditorApp._load_editor_settings(app2)
            app2.editor_settings = loaded

            self.assertEqual(
                mm9_editor_app.EditorApp._last_lomm_root(app2),
                os.path.abspath(lomm_root),
            )

    def test_lomm_conversion_command_reports_missing_game_root(self):
        errors = []

        class FakeMessagebox:
            @staticmethod
            def showerror(title, body):
                errors.append((title, body))

        method_globals = mm9_editor_app.EditorApp.cmd_lomm_to_mm9_conversion.__globals__
        old_messagebox = method_globals.get("messagebox")
        try:
            method_globals["messagebox"] = FakeMessagebox
            app = self._dummy_app({}, {"classes": {}, "filenames": {}})
            app.cfg = types.SimpleNamespace(game_root=None, backup_root=None)

            mm9_editor_app.EditorApp.cmd_lomm_to_mm9_conversion(app)
        finally:
            method_globals["messagebox"] = old_messagebox

        self.assertEqual(errors[0][0], "MM9 game folder not detected")

    def test_dat_to_ed_default_model_selection_skips_system_models(self):
        class FakeModel:
            def __init__(self, name, *, skybox=False, points=True, polygons=True, texture=""):
                self.name = name
                self.points = [(0.0, 0.0, 0.0)] if points else []
                self.polygons = [object()] if polygons else []
                self._skybox = skybox
                self._texture = texture

            def is_skybox(self):
                return self._skybox

            def texture_name_for(self, _polygon):
                return self._texture

        world = types.SimpleNamespace(world_models=[
            FakeModel("Terrain0"),
            FakeModel("PhysicsBSP"),
            FakeModel("VisBSP"),
            FakeModel("SkyBox0", skybox=True),
            FakeModel("EmptyThing", points=False),
            FakeModel("AITrk0", texture="TEXTURES\\LevelTextures\\Misc\\rail.dtx"),
            FakeModel("WorldObject1"),
            FakeModel("MonsterDoor1"),
        ])

        names = mm9_editor_app.EditorApp._default_dat_to_ed_model_names(world)

        self.assertEqual(names, ("WorldObject1", "MonsterDoor1"))

    def test_isleofashes_uses_multi_anchor_terrain_support_policy(self):
        self.assertEqual(
            mm9_editor_app.DAT_TO_ED_TERRAIN_SUPPORT_SELECTION_MODE_BY_LEVEL["ISLEOFASHES"],
            "multi_anchor_budget",
        )

    def test_dat_to_ed_command_stages_active_dat_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            staged_calls = {}
            infos = []
            source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.ED")

            class FakeModel:
                def __init__(self, name):
                    self.name = name
                    self.points = [(0.0, 0.0, 0.0)]
                    self.polygons = [object()]

                def is_skybox(self):
                    return False

            class FakeLevel:
                display_name = "BOOTCAMP.DAT"
                rez_vpath = "WORLDS/BOOTCAMP.DAT"
                path = ""

                def get_bsp(self):
                    return types.SimpleNamespace(world_models=[
                        FakeModel("Terrain0"),
                        FakeModel("WorldObject1"),
                    ])

                def source_bytes(self):
                    return b"fake dat bytes"

            class FakeFileDialog:
                @staticmethod
                def askdirectory(**_kwargs):
                    return output_dir

            class FakeMessagebox:
                @staticmethod
                def showinfo(title, body):
                    infos.append((title, body))

                @staticmethod
                def showerror(title, body):
                    raise AssertionError(f"unexpected error: {title}: {body}")

                @staticmethod
                def showwarning(title, body):
                    raise AssertionError(f"unexpected warning: {title}: {body}")

            class FakeCompilerStrategy:
                @staticmethod
                def build_full_world_skeleton_acceptance_report(**kwargs):
                    staged_calls.update(kwargs)
                    with open(kwargs["source_dat_path"], "rb") as f:
                        self.assertEqual(f.read(), b"fake dat bytes")
                    generated_ed = os.path.join(
                        kwargs["work_dir"],
                        "full_world_skeleton_source",
                        kwargs["output_filename"],
                    )
                    os.makedirs(os.path.dirname(generated_ed), exist_ok=True)
                    with open(generated_ed, "wb") as f:
                        f.write(b"ed")
                    return types.SimpleNamespace(
                        blockers=(),
                        generated_ed_path=generated_ed,
                        selected_model_names=("WorldObject1",),
                        object_count=4,
                        polygon_count=12,
                        include_low_risk_behavior_prop_objects=bool(
                            kwargs.get("include_low_risk_behavior_prop_objects")
                        ),
                        include_wall_torch_objects=bool(kwargs.get("include_wall_torch_objects")),
                        include_fire_objects=bool(kwargs.get("include_fire_objects")),
                    )

                @staticmethod
                def format_full_world_skeleton_acceptance_report(report):
                    return f"formatted report for {report.generated_ed_path}"

                @staticmethod
                def build_dat_to_ed_selection_report(**kwargs):
                    staged_calls["selection_kwargs"] = kwargs
                    return types.SimpleNamespace(
                        status="selection_report_built",
                        selected_model_count=len(kwargs["selected_model_names"]),
                    )

                @staticmethod
                def write_dat_to_ed_selection_report(report, output_path, **kwargs):
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump({
                            "kind": "mm9_dat_to_ed_selection_report",
                            "status": report.status,
                            "selected_model_count": report.selected_model_count,
                            "diagnostics_linked": bool(kwargs.get("acceptance_report")),
                        }, f)
                    return output_path

                @staticmethod
                def write_full_world_skeleton_acceptance_manifest(report, manifest_path, **kwargs):
                    with open(manifest_path, "w", encoding="utf-8") as f:
                        json.dump({
                            "kind": "mm9_dat_to_ed_acceptance",
                            "source": {
                                "original_source": kwargs["original_source"],
                                "staged_source_dat_path": kwargs["staged_source_dat_path"],
                            },
                            "artifacts": {
                                "generated_ed_path": report.generated_ed_path,
                                "text_report_path": kwargs["text_report_path"],
                                "selection_report_path": kwargs["selection_report_path"],
                            },
                        }, f)
                    return manifest_path

            method_globals = mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat.__globals__
            old_filedialog = method_globals.get("filedialog")
            old_messagebox = method_globals.get("messagebox")
            old_compiler = method_globals.get("dat_compiler_strategy")
            try:
                method_globals["filedialog"] = FakeFileDialog
                method_globals["messagebox"] = FakeMessagebox
                method_globals["dat_compiler_strategy"] = FakeCompilerStrategy
                app = object.__new__(mm9_editor_app.EditorApp)
                app.active = FakeLevel()
                app.cfg = types.SimpleNamespace(
                    work_dir=tmp,
                    editor_dir=tmp,
                    game_data_dir=os.path.join(tmp, "game", "data"),
                )

                mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat(app)
            finally:
                method_globals["filedialog"] = old_filedialog
                method_globals["messagebox"] = old_messagebox
                method_globals["dat_compiler_strategy"] = old_compiler

            self.assertEqual(staged_calls["model_names"], ("WorldObject1",))
            self.assertTrue(staged_calls["include_terrain_support_patch"])
            self.assertTrue(staged_calls["include_terrain_support_source_coverage"])
            self.assertFalse(staged_calls["include_physics_shell_patch"])
            self.assertFalse(staged_calls["include_physics_shell_source_coverage"])
            self.assertFalse(staged_calls["include_door_objects"])
            self.assertEqual(staged_calls["door_source_ed_path"], "")
            self.assertFalse(staged_calls["include_airail_objects"])
            self.assertEqual(staged_calls["airail_source_ed_path"], "")
            self.assertFalse(staged_calls["include_sky_objects"])
            self.assertEqual(staged_calls["sky_source_ed_path"], "")
            self.assertFalse(staged_calls["include_sky_marker_brushes"])
            self.assertFalse(staged_calls["include_sound_objects"])
            self.assertEqual(staged_calls["sound_source_ed_path"], "")
            self.assertFalse(staged_calls["include_collision_helper_objects"])
            self.assertFalse(staged_calls["include_collision_helper_brushes"])
            self.assertEqual(staged_calls["collision_helper_source_ed_path"], "")
            self.assertFalse(staged_calls["include_trigger_helper_objects"])
            self.assertFalse(staged_calls["include_trigger_helper_brushes"])
            self.assertEqual(staged_calls["trigger_helper_source_ed_path"], "")
            self.assertTrue(staged_calls["include_low_risk_behavior_prop_objects"])
            self.assertEqual(staged_calls["low_risk_behavior_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_wall_torch_objects"])
            self.assertEqual(staged_calls["wall_torch_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_fire_objects"])
            self.assertEqual(staged_calls["fire_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_candle_prop_objects"])
            self.assertEqual(staged_calls["candle_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_brazier_objects"])
            self.assertEqual(staged_calls["brazier_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_treasure_chest_objects"])
            self.assertEqual(staged_calls["treasure_chest_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_prop_damager_objects"])
            self.assertEqual(staged_calls["prop_damager_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_destructable_prop_objects"])
            self.assertEqual(staged_calls["destructable_prop_source_ed_path"], source_ed)
            self.assertEqual(staged_calls["terrain_support_selection_mode"], "connected_budget")
            self.assertGreater(staged_calls["terrain_support_radius"], 0.0)
            self.assertEqual(staged_calls["terrain_support_max_polygons"], 1499)
            self.assertEqual(staged_calls["max_processor_brushes"], 1500)
            self.assertEqual(staged_calls["max_processor_polygons"], 12000)
            self.assertTrue(staged_calls["block_unreconstructed_physics_shell"])
            self.assertEqual(
                staged_calls["worlds_install_dir"],
                os.path.join(tmp, "game", "data", "WORLDS"),
            )
            self.assertTrue(os.path.exists(os.path.join(output_dir, "source_dat", "BOOTCAMP.DAT")))
            report_path = os.path.join(output_dir, "BOOTCAMP_dat_to_ed_report.txt")
            with open(report_path, "r", encoding="utf-8") as f:
                self.assertIn("formatted report", f.read())
            selection_path = os.path.join(output_dir, "BOOTCAMP_dat_to_ed_selection_report.json")
            with open(selection_path, "r", encoding="utf-8") as f:
                selection = json.load(f)
            self.assertEqual(selection["kind"], "mm9_dat_to_ed_selection_report")
            self.assertTrue(selection["diagnostics_linked"])
            self.assertEqual(staged_calls["selection_kwargs"]["selected_model_names"], ("WorldObject1",))
            self.assertTrue(staged_calls["selection_kwargs"]["include_terrain_support_patch"])
            self.assertFalse(staged_calls["selection_kwargs"]["include_physics_shell_patch"])
            self.assertTrue(staged_calls["selection_kwargs"]["include_airail_semantics"])
            self.assertTrue(staged_calls["selection_kwargs"]["include_sky_semantics"])
            self.assertTrue(staged_calls["selection_kwargs"]["include_sound_semantics"])
            self.assertTrue(staged_calls["selection_kwargs"]["include_trigger_semantics"])
            manifest_path = os.path.join(output_dir, "BOOTCAMP_dat_to_ed_acceptance_manifest.json")
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["kind"], "mm9_dat_to_ed_acceptance")
            self.assertEqual(manifest["source"]["original_source"], "BOOTCAMP.DAT")
            self.assertEqual(
                manifest["source"]["staged_source_dat_path"],
                os.path.join(output_dir, "source_dat", "BOOTCAMP.DAT"),
            )
            self.assertEqual(manifest["artifacts"]["text_report_path"], report_path)
            self.assertEqual(manifest["artifacts"]["selection_report_path"], selection_path)
            self.assertEqual(infos[0][0], "DAT to ED generation complete")
            self.assertIn("Low-risk behavior prop objects: included", infos[0][1])
            self.assertIn("Validated light/fire behavior prop objects: included", infos[0][1])
            self.assertIn("Behavior prop validation profile: not included", infos[0][1])

    def test_dat_to_ed_command_enables_physics_shell_patch_for_non_terrain_levels(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            staged_calls = {}
            infos = []

            source_ed = os.path.join(tmp, "mm9_data", "WORLDS", "ANSKRAMKEEP.ED")
            os.makedirs(os.path.dirname(source_ed), exist_ok=True)
            with open(source_ed, "wb") as f:
                f.write(b"source ed oracle")

            class FakeModel:
                def __init__(self, name, polygon_count=1, texture="TEXTURES\\LevelTextures\\Stone.dtx"):
                    self.name = name
                    self.points = [(0.0, 0.0, 0.0)]
                    self.polygons = [object()] * polygon_count
                    self._texture = texture

                def is_skybox(self):
                    return False

                def texture_name_for(self, _polygon):
                    return self._texture

            class FakeLevel:
                display_name = "ANSKRAMKEEP.DAT"
                rez_vpath = "WORLDS/ANSKRAMKEEP.DAT"
                path = ""
                world = types.SimpleNamespace(objects=[
                    patcher.WorldObject("DestructableBrush", [
                        patcher.Property("Name", 0, 0, "Floordoor1"),
                    ]),
                ])

                def get_bsp(self):
                    return types.SimpleNamespace(world_models=[
                        FakeModel("PhysicsBSP", polygon_count=6450),
                        FakeModel("AITrk2", polygon_count=6, texture="TEXTURES\\LevelTextures\\Misc\\rail.dtx"),
                        FakeModel("SkyMarkerProbe", polygon_count=6, texture="TEXTURES\\SkyBox\\SkyMarker.dtx"),
                        FakeModel("SoundOnlyProbe", polygon_count=6, texture="TEXTURES\\LevelTextures\\Misc\\SoundOnly.dtx"),
                        FakeModel("InvisibleBrush7", polygon_count=6, texture="TEXTURES\\LevelTextures\\Misc\\Invisible.dtx"),
                        FakeModel("Tavernzone", polygon_count=6, texture="TEXTURES\\LevelTextures\\Misc\\greenscreen.dtx"),
                        FakeModel("WorldObject1", polygon_count=12),
                        FakeModel("InnerDoor", polygon_count=6),
                        FakeModel("Floordoor1", polygon_count=6),
                    ])

                def source_bytes(self):
                    return b"fake dat bytes"

            class FakeFileDialog:
                @staticmethod
                def askdirectory(**_kwargs):
                    return output_dir

            class FakeMessagebox:
                @staticmethod
                def showinfo(title, body):
                    infos.append((title, body))

                @staticmethod
                def showerror(title, body):
                    raise AssertionError(f"unexpected error: {title}: {body}")

                @staticmethod
                def showwarning(title, body):
                    raise AssertionError(f"unexpected warning: {title}: {body}")

            class FakeCompilerStrategy:
                @staticmethod
                def build_full_world_skeleton_acceptance_report(**kwargs):
                    staged_calls.update(kwargs)
                    generated_ed = os.path.join(
                        kwargs["work_dir"],
                        "full_world_skeleton_source",
                        kwargs["output_filename"],
                    )
                    os.makedirs(os.path.dirname(generated_ed), exist_ok=True)
                    with open(generated_ed, "wb") as f:
                        f.write(b"ed")
                    return types.SimpleNamespace(
                        blockers=(),
                        generated_ed_path=generated_ed,
                        selected_model_names=tuple(kwargs["model_names"]),
                        object_count=4,
                        polygon_count=12,
                        include_low_risk_behavior_prop_objects=bool(
                            kwargs.get("include_low_risk_behavior_prop_objects")
                        ),
                        include_door_objects=bool(kwargs.get("include_door_objects")),
                        include_airail_objects=bool(kwargs.get("include_airail_objects")),
                        include_sky_objects=bool(kwargs.get("include_sky_objects")),
                        include_sound_objects=bool(kwargs.get("include_sound_objects")),
                        include_collision_helper_objects=bool(kwargs.get("include_collision_helper_objects")),
                        include_collision_helper_brushes=bool(kwargs.get("include_collision_helper_brushes")),
                        include_trigger_helper_objects=bool(kwargs.get("include_trigger_helper_objects")),
                        include_trigger_helper_brushes=bool(kwargs.get("include_trigger_helper_brushes")),
                    )

                @staticmethod
                def format_full_world_skeleton_acceptance_report(report):
                    return f"formatted report for {report.generated_ed_path}"

                @staticmethod
                def build_dat_to_ed_selection_report(**kwargs):
                    staged_calls["selection_kwargs"] = kwargs
                    return types.SimpleNamespace(
                        status="selection_report_built",
                        selected_model_count=len(kwargs["selected_model_names"]),
                    )

                @staticmethod
                def write_dat_to_ed_selection_report(report, output_path, **kwargs):
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump({"kind": "mm9_dat_to_ed_selection_report"}, f)
                    return output_path

                @staticmethod
                def write_full_world_skeleton_acceptance_manifest(report, manifest_path, **kwargs):
                    with open(manifest_path, "w", encoding="utf-8") as f:
                        json.dump({"kind": "mm9_dat_to_ed_acceptance"}, f)
                    return manifest_path

            method_globals = mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat.__globals__
            old_filedialog = method_globals.get("filedialog")
            old_messagebox = method_globals.get("messagebox")
            old_compiler = method_globals.get("dat_compiler_strategy")
            try:
                method_globals["filedialog"] = FakeFileDialog
                method_globals["messagebox"] = FakeMessagebox
                method_globals["dat_compiler_strategy"] = FakeCompilerStrategy
                app = object.__new__(mm9_editor_app.EditorApp)
                app.active = FakeLevel()
                app.cfg = types.SimpleNamespace(
                    work_dir=tmp,
                    editor_dir=tmp,
                    game_data_dir=os.path.join(tmp, "game", "data"),
                )

                mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat(
                    app,
                    physics_shell_stair_assembly_indices=(3,),
                )
            finally:
                method_globals["filedialog"] = old_filedialog
                method_globals["messagebox"] = old_messagebox
                method_globals["dat_compiler_strategy"] = old_compiler

            self.assertEqual(
                staged_calls["model_names"],
                ("WorldObject1", "InnerDoor", "Floordoor1"),
            )
            self.assertFalse(staged_calls["include_terrain_support_patch"])
            self.assertFalse(staged_calls["include_terrain_support_source_coverage"])
            self.assertFalse(staged_calls["include_validation_floor"])
            self.assertTrue(staged_calls["include_physics_shell_patch"])
            self.assertTrue(staged_calls["include_physics_shell_source_coverage"])
            self.assertTrue(staged_calls["include_door_objects"])
            self.assertEqual(staged_calls["door_source_ed_path"], source_ed)
            self.assertFalse(staged_calls["include_destructable_brush_objects"])
            self.assertTrue(staged_calls["include_airail_objects"])
            self.assertEqual(staged_calls["airail_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_sky_objects"])
            self.assertEqual(staged_calls["sky_source_ed_path"], source_ed)
            self.assertFalse(staged_calls["include_sky_marker_brushes"])
            self.assertTrue(staged_calls["include_sound_objects"])
            self.assertEqual(staged_calls["sound_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_collision_helper_objects"])
            self.assertFalse(staged_calls["include_collision_helper_brushes"])
            self.assertEqual(staged_calls["collision_helper_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_trigger_helper_objects"])
            self.assertFalse(staged_calls["include_trigger_helper_brushes"])
            self.assertEqual(staged_calls["trigger_helper_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_low_risk_behavior_prop_objects"])
            self.assertEqual(staged_calls["low_risk_behavior_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_wall_torch_objects"])
            self.assertEqual(staged_calls["wall_torch_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_fire_objects"])
            self.assertEqual(staged_calls["fire_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_candle_prop_objects"])
            self.assertEqual(staged_calls["candle_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_brazier_objects"])
            self.assertEqual(staged_calls["brazier_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_treasure_chest_objects"])
            self.assertEqual(staged_calls["treasure_chest_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_prop_damager_objects"])
            self.assertEqual(staged_calls["prop_damager_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_destructable_prop_objects"])
            self.assertEqual(staged_calls["destructable_prop_source_ed_path"], source_ed)
            self.assertEqual(staged_calls["physics_shell_thickness"], 16.0)
            self.assertEqual(
                staged_calls["physics_shell_focus_points"],
                (mm9_editor_app.DAT_TO_ED_ANSKRAMKEEP_BACK_START_POINT,),
            )
            self.assertEqual(staged_calls["physics_shell_focus_radius"], 512.0)
            self.assertEqual(staged_calls["physics_shell_focus_budget"], 512)
            self.assertEqual(staged_calls["physics_shell_focus_seed_radius"], 128.0)
            self.assertEqual(staged_calls["physics_shell_stair_assembly_indices"], (3,))
            self.assertEqual(
                staged_calls["output_filename"],
                "ANSKRAMKEEP_reconstructed_stairs_3.ed",
            )
            self.assertTrue(staged_calls["block_unreconstructed_physics_shell"])
            self.assertTrue(staged_calls["selection_kwargs"]["include_physics_shell_patch"])
            self.assertEqual(
                staged_calls["selection_kwargs"]["selected_model_names"],
                ("WorldObject1", "InnerDoor", "Floordoor1"),
            )
            self.assertFalse(staged_calls["selection_kwargs"]["include_terrain_support_patch"])
            self.assertTrue(staged_calls["selection_kwargs"]["include_airail_semantics"])
            self.assertTrue(staged_calls["selection_kwargs"]["include_sky_semantics"])
            self.assertTrue(staged_calls["selection_kwargs"]["include_sound_semantics"])
            self.assertTrue(staged_calls["selection_kwargs"]["include_collision_semantics"])
            self.assertTrue(staged_calls["selection_kwargs"]["include_trigger_semantics"])
            self.assertEqual(infos[0][0], "DAT to ED generation complete")

    def test_dat_to_ed_behavior_prop_validation_command_enables_explicit_prop_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            staged_calls = {}
            infos = []

            source_ed = os.path.join(tmp, "game", "data", "WORLDS", "BATHHOUSE.ED")
            os.makedirs(os.path.dirname(source_ed), exist_ok=True)
            with open(source_ed, "wb") as f:
                f.write(b"source ed oracle")

            class FakeModel:
                def __init__(self, name, polygon_count=1, texture="TEXTURES\\LevelTextures\\Stone.dtx"):
                    self.name = name
                    self.points = [(0.0, 0.0, 0.0)]
                    self.polygons = [object()] * polygon_count
                    self._texture = texture

                def is_skybox(self):
                    return False

                def texture_name_for(self, _polygon):
                    return self._texture

            class FakeLevel:
                display_name = "BATHHOUSE.DAT"
                rez_vpath = "WORLDS/BATHHOUSE.DAT"
                path = ""

                def get_bsp(self):
                    return types.SimpleNamespace(world_models=[
                        FakeModel("Terrain0", polygon_count=20),
                        FakeModel("WorldObject1", polygon_count=12),
                    ])

                def source_bytes(self):
                    return b"fake dat bytes"

            class FakeFileDialog:
                @staticmethod
                def askdirectory(**_kwargs):
                    return output_dir

            class FakeMessagebox:
                @staticmethod
                def showinfo(title, body):
                    infos.append((title, body))

                @staticmethod
                def showerror(title, body):
                    raise AssertionError(f"unexpected error: {title}: {body}")

                @staticmethod
                def showwarning(title, body):
                    raise AssertionError(f"unexpected warning: {title}: {body}")

            class FakeCompilerStrategy:
                @staticmethod
                def build_full_world_skeleton_acceptance_report(**kwargs):
                    staged_calls.update(kwargs)
                    generated_ed = os.path.join(
                        kwargs["work_dir"],
                        "full_world_skeleton_source",
                        kwargs["output_filename"],
                    )
                    os.makedirs(os.path.dirname(generated_ed), exist_ok=True)
                    with open(generated_ed, "wb") as f:
                        f.write(b"ed")
                    return types.SimpleNamespace(
                        blockers=(),
                        generated_ed_path=generated_ed,
                        selected_model_names=("Terrain0", "WorldObject1"),
                        object_count=42,
                        polygon_count=32,
                        include_low_risk_behavior_prop_objects=bool(
                            kwargs.get("include_low_risk_behavior_prop_objects")
                        ),
                        include_wall_torch_objects=bool(
                            kwargs.get("include_wall_torch_objects")
                        ),
                        include_fire_objects=bool(kwargs.get("include_fire_objects")),
                        include_candle_prop_objects=bool(
                            kwargs.get("include_candle_prop_objects")
                        ),
                        include_brazier_objects=bool(
                            kwargs.get("include_brazier_objects")
                        ),
                        include_treasure_chest_objects=bool(
                            kwargs.get("include_treasure_chest_objects")
                        ),
                        include_prop_damager_objects=bool(
                            kwargs.get("include_prop_damager_objects")
                        ),
                        include_destructable_prop_objects=bool(
                            kwargs.get("include_destructable_prop_objects")
                        ),
                    )

                @staticmethod
                def format_full_world_skeleton_acceptance_report(report):
                    return f"formatted report for {report.generated_ed_path}"

                @staticmethod
                def build_behavior_prop_reconstruction_report(**kwargs):
                    staged_calls["behavior_prop_report_kwargs"] = kwargs
                    return types.SimpleNamespace(status="behavior_prop_reconstruction_report_built")

                @staticmethod
                def format_behavior_prop_reconstruction_report(report):
                    return f"behavior prop report: {report.status}"

                @staticmethod
                def build_dat_to_ed_selection_report(**kwargs):
                    staged_calls["selection_kwargs"] = kwargs
                    return types.SimpleNamespace(
                        status="selection_report_built",
                        selected_model_count=len(kwargs["selected_model_names"]),
                    )

                @staticmethod
                def write_dat_to_ed_selection_report(report, output_path, **kwargs):
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump({"kind": "mm9_dat_to_ed_selection_report"}, f)
                    return output_path

                @staticmethod
                def write_full_world_skeleton_acceptance_manifest(report, manifest_path, **kwargs):
                    staged_calls["manifest_kwargs"] = kwargs
                    with open(manifest_path, "w", encoding="utf-8") as f:
                        json.dump({
                            "kind": "mm9_dat_to_ed_acceptance",
                            "behavior_prop_report_path": kwargs.get("behavior_prop_report_path", ""),
                        }, f)
                    return manifest_path

            method_globals = mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat.__globals__
            old_filedialog = method_globals.get("filedialog")
            old_messagebox = method_globals.get("messagebox")
            old_compiler = method_globals.get("dat_compiler_strategy")
            try:
                method_globals["filedialog"] = FakeFileDialog
                method_globals["messagebox"] = FakeMessagebox
                method_globals["dat_compiler_strategy"] = FakeCompilerStrategy
                app = object.__new__(mm9_editor_app.EditorApp)
                app.active = FakeLevel()
                app.cfg = types.SimpleNamespace(
                    work_dir=tmp,
                    editor_dir=tmp,
                    game_data_dir=os.path.join(tmp, "game", "data"),
                )

                mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat(
                    app,
                    behavior_prop_validation_profile="all",
                )
            finally:
                method_globals["filedialog"] = old_filedialog
                method_globals["messagebox"] = old_messagebox
                method_globals["dat_compiler_strategy"] = old_compiler

            self.assertEqual(
                staged_calls["output_filename"],
                "BATHHOUSE_reconstructed_behavior_prop_validation.ed",
            )
            self.assertTrue(staged_calls["include_low_risk_behavior_prop_objects"])
            self.assertEqual(staged_calls["low_risk_behavior_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_wall_torch_objects"])
            self.assertEqual(staged_calls["wall_torch_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_fire_objects"])
            self.assertEqual(staged_calls["fire_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_candle_prop_objects"])
            self.assertEqual(staged_calls["candle_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_brazier_objects"])
            self.assertEqual(staged_calls["brazier_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_treasure_chest_objects"])
            self.assertEqual(staged_calls["treasure_chest_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_prop_damager_objects"])
            self.assertEqual(staged_calls["prop_damager_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_destructable_prop_objects"])
            self.assertEqual(staged_calls["destructable_prop_source_ed_path"], source_ed)
            self.assertEqual(staged_calls["behavior_prop_report_kwargs"]["source_ed_path"], source_ed)
            behavior_report_path = os.path.join(
                output_dir,
                "BATHHOUSE_dat_to_ed_behavior_prop_validation_report.txt",
            )
            self.assertEqual(
                staged_calls["manifest_kwargs"]["behavior_prop_report_path"],
                behavior_report_path,
            )
            with open(behavior_report_path, "r", encoding="utf-8") as f:
                self.assertIn("behavior prop report", f.read())
            self.assertIn("Behavior prop validation profile: all", infos[0][1])
            self.assertIn(behavior_report_path, infos[0][1])

    def test_dat_to_ed_destructable_brush_validation_uses_dat_objects_without_source_ed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            staged_calls = {}
            infos = []

            class FakeModel:
                def __init__(self, name, polygon_count=1, texture="TEXTURES\\LevelTextures\\Stone.dtx"):
                    self.name = name
                    self.points = [(0.0, 0.0, 0.0)]
                    self.polygons = [object()] * polygon_count
                    self._texture = texture

                def is_skybox(self):
                    return False

                def texture_name_for(self, _polygon):
                    return self._texture

            class FakeLevel:
                display_name = "DRAGONSTADIUM.DAT"
                rez_vpath = "WORLDS/DRAGONSTADIUM.DAT"
                path = ""
                world = types.SimpleNamespace(objects=[
                    patcher.WorldObject("DestructableBrush", [
                        patcher.Property("Name", 0, 0, "p1 level 1"),
                    ]),
                    patcher.WorldObject("DestructableBrush", [
                        patcher.Property("Name", 0, 0, "p1 level 2"),
                    ]),
                ])

                def get_bsp(self):
                    return types.SimpleNamespace(world_models=[
                        FakeModel("PhysicsBSP", polygon_count=120),
                        FakeModel("p1 level 1", polygon_count=28),
                        FakeModel("p1 level 2", polygon_count=32),
                    ])

                def source_bytes(self):
                    return b"fake dat bytes"

            class FakeFileDialog:
                @staticmethod
                def askdirectory(**_kwargs):
                    return output_dir

            class FakeMessagebox:
                @staticmethod
                def showinfo(title, body):
                    infos.append((title, body))

                @staticmethod
                def showerror(title, body):
                    raise AssertionError(f"unexpected error: {title}: {body}")

                @staticmethod
                def showwarning(title, body):
                    raise AssertionError(f"unexpected warning: {title}: {body}")

            class FakeCompilerStrategy:
                @staticmethod
                def build_full_world_skeleton_acceptance_report(**kwargs):
                    staged_calls.update(kwargs)
                    generated_ed = os.path.join(
                        kwargs["work_dir"],
                        "full_world_skeleton_source",
                        kwargs["output_filename"],
                    )
                    os.makedirs(os.path.dirname(generated_ed), exist_ok=True)
                    with open(generated_ed, "wb") as f:
                        f.write(b"ed")
                    return types.SimpleNamespace(
                        blockers=(),
                        generated_ed_path=generated_ed,
                        selected_model_names=tuple(kwargs["model_names"]),
                        object_count=7,
                        polygon_count=60,
                        include_destructable_brush_objects=bool(
                            kwargs.get("include_destructable_brush_objects")
                        ),
                    )

                @staticmethod
                def format_full_world_skeleton_acceptance_report(report):
                    return f"formatted report for {report.generated_ed_path}"

                @staticmethod
                def build_behavior_prop_reconstruction_report(**_kwargs):
                    raise AssertionError("source-ED behavior prop report should not be generated")

                @staticmethod
                def build_dat_to_ed_selection_report(**kwargs):
                    staged_calls["selection_kwargs"] = kwargs
                    return types.SimpleNamespace(
                        status="selection_report_built",
                        selected_model_count=len(kwargs["selected_model_names"]),
                    )

                @staticmethod
                def write_dat_to_ed_selection_report(report, output_path, **kwargs):
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump({"kind": "mm9_dat_to_ed_selection_report"}, f)
                    return output_path

                @staticmethod
                def write_full_world_skeleton_acceptance_manifest(report, manifest_path, **kwargs):
                    staged_calls["manifest_kwargs"] = kwargs
                    with open(manifest_path, "w", encoding="utf-8") as f:
                        json.dump({
                            "kind": "mm9_dat_to_ed_acceptance",
                            "behavior_prop_report_path": kwargs.get("behavior_prop_report_path", ""),
                        }, f)
                    return manifest_path

            method_globals = mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat.__globals__
            old_filedialog = method_globals.get("filedialog")
            old_messagebox = method_globals.get("messagebox")
            old_compiler = method_globals.get("dat_compiler_strategy")
            try:
                method_globals["filedialog"] = FakeFileDialog
                method_globals["messagebox"] = FakeMessagebox
                method_globals["dat_compiler_strategy"] = FakeCompilerStrategy
                app = object.__new__(mm9_editor_app.EditorApp)
                app.active = FakeLevel()
                app.cfg = types.SimpleNamespace(
                    work_dir=tmp,
                    editor_dir=tmp,
                    game_data_dir=os.path.join(tmp, "game", "data"),
                )

                mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat(
                    app,
                    behavior_prop_validation_profile="destructable_brush",
                )
            finally:
                method_globals["filedialog"] = old_filedialog
                method_globals["messagebox"] = old_messagebox
                method_globals["dat_compiler_strategy"] = old_compiler

            self.assertEqual(
                staged_calls["output_filename"],
                "DRAGONSTADIUM_reconstructed_destructable_brush_validation.ed",
            )
            self.assertEqual(staged_calls["model_names"], ("p1 level 1", "p1 level 2"))
            self.assertTrue(staged_calls["include_destructable_brush_objects"])
            self.assertTrue(staged_calls["include_validation_floor"])
            self.assertFalse(staged_calls["include_terrain_support_patch"])
            self.assertFalse(staged_calls["include_physics_shell_patch"])
            self.assertFalse(staged_calls["block_unreconstructed_physics_shell"])
            self.assertFalse(staged_calls["include_low_risk_behavior_prop_objects"])
            self.assertFalse(staged_calls["include_destructable_prop_objects"])
            self.assertEqual(staged_calls["destructable_prop_source_ed_path"], "")
            self.assertEqual(staged_calls["manifest_kwargs"]["behavior_prop_report_path"], "")
            self.assertIn("Behavior prop validation report:\nnot generated", infos[0][1])
            self.assertIn("DestructableBrush objects: included", infos[0][1])
            self.assertIn("Behavior prop validation profile: destructable_brush", infos[0][1])

    def test_dat_to_ed_normal_command_auto_uses_dat_native_destructable_brushes(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            staged_calls = {}
            infos = []

            class FakeModel:
                def __init__(self, name, polygon_count=1, texture="TEXTURES\\LevelTextures\\Stone.dtx"):
                    self.name = name
                    self.points = [(0.0, 0.0, 0.0)]
                    self.polygons = [object()] * polygon_count
                    self._texture = texture

                def is_skybox(self):
                    return False

                def texture_name_for(self, _polygon):
                    return self._texture

            class FakeLevel:
                display_name = "DRAGONSTADIUM.DAT"
                rez_vpath = "WORLDS/DRAGONSTADIUM.DAT"
                path = ""
                world = types.SimpleNamespace(objects=[
                    patcher.WorldObject("DestructableBrush", [
                        patcher.Property("Name", 0, 0, "p1 level 1"),
                    ]),
                    patcher.WorldObject("DestructableBrush", [
                        patcher.Property("Name", 0, 0, "p1 level 2"),
                    ]),
                ])

                def get_bsp(self):
                    return types.SimpleNamespace(world_models=[
                        FakeModel("PhysicsBSP", polygon_count=120),
                        FakeModel("p1 level 1", polygon_count=28),
                        FakeModel("p1 level 2", polygon_count=32),
                    ])

                def source_bytes(self):
                    return b"fake dat bytes"

            class FakeFileDialog:
                @staticmethod
                def askdirectory(**_kwargs):
                    return output_dir

            class FakeMessagebox:
                @staticmethod
                def showinfo(title, body):
                    infos.append((title, body))

                @staticmethod
                def showerror(title, body):
                    raise AssertionError(f"unexpected error: {title}: {body}")

                @staticmethod
                def showwarning(title, body):
                    raise AssertionError(f"unexpected warning: {title}: {body}")

            class FakeCompilerStrategy:
                @staticmethod
                def build_full_world_skeleton_acceptance_report(**kwargs):
                    staged_calls.update(kwargs)
                    generated_ed = os.path.join(
                        kwargs["work_dir"],
                        "full_world_skeleton_source",
                        kwargs["output_filename"],
                    )
                    os.makedirs(os.path.dirname(generated_ed), exist_ok=True)
                    with open(generated_ed, "wb") as f:
                        f.write(b"ed")
                    return types.SimpleNamespace(
                        blockers=(),
                        generated_ed_path=generated_ed,
                        selected_model_names=tuple(kwargs["model_names"]),
                        object_count=7,
                        polygon_count=60,
                        include_destructable_brush_objects=bool(
                            kwargs.get("include_destructable_brush_objects")
                        ),
                    )

                @staticmethod
                def format_full_world_skeleton_acceptance_report(report):
                    return f"formatted report for {report.generated_ed_path}"

                @staticmethod
                def build_behavior_prop_reconstruction_report(**_kwargs):
                    raise AssertionError("normal DAT-native generation should not build a source-ED behavior report")

                @staticmethod
                def build_dat_to_ed_selection_report(**kwargs):
                    staged_calls["selection_kwargs"] = kwargs
                    return types.SimpleNamespace(
                        status="selection_report_built",
                        selected_model_count=len(kwargs["selected_model_names"]),
                    )

                @staticmethod
                def write_dat_to_ed_selection_report(report, output_path, **kwargs):
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump({"kind": "mm9_dat_to_ed_selection_report"}, f)
                    return output_path

                @staticmethod
                def write_full_world_skeleton_acceptance_manifest(report, manifest_path, **kwargs):
                    staged_calls["manifest_kwargs"] = kwargs
                    with open(manifest_path, "w", encoding="utf-8") as f:
                        json.dump({
                            "kind": "mm9_dat_to_ed_acceptance",
                            "behavior_prop_report_path": kwargs.get("behavior_prop_report_path", ""),
                        }, f)
                    return manifest_path

            method_globals = mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat.__globals__
            old_filedialog = method_globals.get("filedialog")
            old_messagebox = method_globals.get("messagebox")
            old_compiler = method_globals.get("dat_compiler_strategy")
            try:
                method_globals["filedialog"] = FakeFileDialog
                method_globals["messagebox"] = FakeMessagebox
                method_globals["dat_compiler_strategy"] = FakeCompilerStrategy
                app = object.__new__(mm9_editor_app.EditorApp)
                app.active = FakeLevel()
                app.cfg = types.SimpleNamespace(
                    work_dir=tmp,
                    editor_dir=tmp,
                    game_data_dir=os.path.join(tmp, "game", "data"),
                )

                mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat(app)
            finally:
                method_globals["filedialog"] = old_filedialog
                method_globals["messagebox"] = old_messagebox
                method_globals["dat_compiler_strategy"] = old_compiler

            self.assertEqual(
                staged_calls["output_filename"],
                "DRAGONSTADIUM_reconstructed.ed",
            )
            self.assertEqual(staged_calls["model_names"], ("p1 level 1", "p1 level 2"))
            self.assertTrue(staged_calls["include_destructable_brush_objects"])
            self.assertTrue(staged_calls["include_validation_floor"])
            self.assertFalse(staged_calls["include_terrain_support_patch"])
            self.assertFalse(staged_calls["include_physics_shell_patch"])
            self.assertFalse(staged_calls["block_unreconstructed_physics_shell"])
            self.assertEqual(staged_calls["manifest_kwargs"]["behavior_prop_report_path"], "")
            self.assertIn("Behavior prop validation report:\nnot generated", infos[0][1])
            self.assertIn("DestructableBrush objects: included", infos[0][1])
            self.assertIn("Behavior prop validation profile: not included", infos[0][1])

    def test_dat_to_ed_medium_light_prop_validation_command_excludes_high_risk_props(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            staged_calls = {}
            infos = []

            source_ed = os.path.join(tmp, "game", "data", "WORLDS", "ANSKRAMKEEP.ED")
            os.makedirs(os.path.dirname(source_ed), exist_ok=True)
            with open(source_ed, "wb") as f:
                f.write(b"source ed oracle")

            class FakeModel:
                def __init__(self, name, polygon_count=1, texture="TEXTURES\\LevelTextures\\Stone.dtx"):
                    self.name = name
                    self.points = [(0.0, 0.0, 0.0)]
                    self.polygons = [object()] * polygon_count
                    self._texture = texture

                def is_skybox(self):
                    return False

                def texture_name_for(self, _polygon):
                    return self._texture

            class FakeLevel:
                display_name = "ANSKRAMKEEP.DAT"
                rez_vpath = "WORLDS/ANSKRAMKEEP.DAT"
                path = ""

                def get_bsp(self):
                    return types.SimpleNamespace(world_models=[
                        FakeModel("PhysicsBSP", polygon_count=6450),
                        FakeModel("WorldObject1", polygon_count=12),
                    ])

                def source_bytes(self):
                    return b"fake dat bytes"

            class FakeFileDialog:
                @staticmethod
                def askdirectory(**_kwargs):
                    return output_dir

            class FakeMessagebox:
                @staticmethod
                def showinfo(title, body):
                    infos.append((title, body))

                @staticmethod
                def showerror(title, body):
                    raise AssertionError(f"unexpected error: {title}: {body}")

                @staticmethod
                def showwarning(title, body):
                    raise AssertionError(f"unexpected warning: {title}: {body}")

            class FakeCompilerStrategy:
                @staticmethod
                def build_full_world_skeleton_acceptance_report(**kwargs):
                    staged_calls.update(kwargs)
                    generated_ed = os.path.join(
                        kwargs["work_dir"],
                        "full_world_skeleton_source",
                        kwargs["output_filename"],
                    )
                    os.makedirs(os.path.dirname(generated_ed), exist_ok=True)
                    with open(generated_ed, "wb") as f:
                        f.write(b"ed")
                    return types.SimpleNamespace(
                        blockers=(),
                        generated_ed_path=generated_ed,
                        selected_model_names=("WorldObject1",),
                        object_count=42,
                        polygon_count=32,
                        include_low_risk_behavior_prop_objects=bool(
                            kwargs.get("include_low_risk_behavior_prop_objects")
                        ),
                        include_wall_torch_objects=bool(
                            kwargs.get("include_wall_torch_objects")
                        ),
                        include_fire_objects=bool(kwargs.get("include_fire_objects")),
                        include_candle_prop_objects=bool(
                            kwargs.get("include_candle_prop_objects")
                        ),
                        include_brazier_objects=bool(
                            kwargs.get("include_brazier_objects")
                        ),
                        include_treasure_chest_objects=bool(
                            kwargs.get("include_treasure_chest_objects")
                        ),
                        include_prop_damager_objects=bool(
                            kwargs.get("include_prop_damager_objects")
                        ),
                        include_destructable_prop_objects=bool(
                            kwargs.get("include_destructable_prop_objects")
                        ),
                    )

                @staticmethod
                def format_full_world_skeleton_acceptance_report(report):
                    return f"formatted report for {report.generated_ed_path}"

                @staticmethod
                def build_behavior_prop_reconstruction_report(**kwargs):
                    staged_calls["behavior_prop_report_kwargs"] = kwargs
                    return types.SimpleNamespace(status="behavior_prop_reconstruction_report_built")

                @staticmethod
                def format_behavior_prop_reconstruction_report(report):
                    return f"behavior prop report: {report.status}"

                @staticmethod
                def build_dat_to_ed_selection_report(**kwargs):
                    staged_calls["selection_kwargs"] = kwargs
                    return types.SimpleNamespace(
                        status="selection_report_built",
                        selected_model_count=len(kwargs["selected_model_names"]),
                    )

                @staticmethod
                def write_dat_to_ed_selection_report(report, output_path, **kwargs):
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump({"kind": "mm9_dat_to_ed_selection_report"}, f)
                    return output_path

                @staticmethod
                def write_full_world_skeleton_acceptance_manifest(report, manifest_path, **kwargs):
                    staged_calls["manifest_kwargs"] = kwargs
                    with open(manifest_path, "w", encoding="utf-8") as f:
                        json.dump({
                            "kind": "mm9_dat_to_ed_acceptance",
                            "behavior_prop_report_path": kwargs.get("behavior_prop_report_path", ""),
                        }, f)
                    return manifest_path

            method_globals = mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat.__globals__
            old_filedialog = method_globals.get("filedialog")
            old_messagebox = method_globals.get("messagebox")
            old_compiler = method_globals.get("dat_compiler_strategy")
            try:
                method_globals["filedialog"] = FakeFileDialog
                method_globals["messagebox"] = FakeMessagebox
                method_globals["dat_compiler_strategy"] = FakeCompilerStrategy
                app = object.__new__(mm9_editor_app.EditorApp)
                app.active = FakeLevel()
                app.cfg = types.SimpleNamespace(
                    work_dir=tmp,
                    editor_dir=tmp,
                    game_data_dir=os.path.join(tmp, "game", "data"),
                )

                mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat(
                    app,
                    behavior_prop_validation_profile="medium_light",
                )
            finally:
                method_globals["filedialog"] = old_filedialog
                method_globals["messagebox"] = old_messagebox
                method_globals["dat_compiler_strategy"] = old_compiler

            self.assertEqual(
                staged_calls["output_filename"],
                "ANSKRAMKEEP_reconstructed_medium_light_prop_validation.ed",
            )
            self.assertTrue(staged_calls["include_low_risk_behavior_prop_objects"])
            self.assertEqual(staged_calls["low_risk_behavior_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_wall_torch_objects"])
            self.assertEqual(staged_calls["wall_torch_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_fire_objects"])
            self.assertEqual(staged_calls["fire_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_candle_prop_objects"])
            self.assertEqual(staged_calls["candle_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_brazier_objects"])
            self.assertEqual(staged_calls["brazier_source_ed_path"], source_ed)
            self.assertFalse(staged_calls["include_treasure_chest_objects"])
            self.assertEqual(staged_calls["treasure_chest_source_ed_path"], "")
            self.assertFalse(staged_calls["include_prop_damager_objects"])
            self.assertEqual(staged_calls["prop_damager_source_ed_path"], "")
            self.assertFalse(staged_calls["include_destructable_prop_objects"])
            self.assertEqual(staged_calls["destructable_prop_source_ed_path"], "")
            self.assertEqual(staged_calls["behavior_prop_report_kwargs"]["source_ed_path"], source_ed)
            self.assertIn("Behavior prop validation profile: medium_light", infos[0][1])

            staged_calls.clear()
            infos.clear()
            try:
                method_globals["filedialog"] = FakeFileDialog
                method_globals["messagebox"] = FakeMessagebox
                method_globals["dat_compiler_strategy"] = FakeCompilerStrategy
                app = object.__new__(mm9_editor_app.EditorApp)
                app.active = FakeLevel()
                app.cfg = types.SimpleNamespace(
                    work_dir=tmp,
                    editor_dir=tmp,
                    game_data_dir=os.path.join(tmp, "game", "data"),
                )

                mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat(
                    app,
                    behavior_prop_validation_profile="candle_prop",
                )
            finally:
                method_globals["filedialog"] = old_filedialog
                method_globals["messagebox"] = old_messagebox
                method_globals["dat_compiler_strategy"] = old_compiler

            self.assertEqual(
                staged_calls["output_filename"],
                "ANSKRAMKEEP_reconstructed_candle_prop_validation.ed",
            )
            self.assertTrue(staged_calls["include_low_risk_behavior_prop_objects"])
            self.assertEqual(staged_calls["low_risk_behavior_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_wall_torch_objects"])
            self.assertEqual(staged_calls["wall_torch_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_fire_objects"])
            self.assertEqual(staged_calls["fire_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_candle_prop_objects"])
            self.assertEqual(staged_calls["candle_prop_source_ed_path"], source_ed)
            self.assertFalse(staged_calls["include_brazier_objects"])
            self.assertEqual(staged_calls["brazier_source_ed_path"], "")
            self.assertFalse(staged_calls["include_treasure_chest_objects"])
            self.assertEqual(staged_calls["treasure_chest_source_ed_path"], "")
            self.assertFalse(staged_calls["include_prop_damager_objects"])
            self.assertEqual(staged_calls["prop_damager_source_ed_path"], "")
            self.assertFalse(staged_calls["include_destructable_prop_objects"])
            self.assertEqual(staged_calls["destructable_prop_source_ed_path"], "")
            self.assertEqual(staged_calls["behavior_prop_report_kwargs"]["source_ed_path"], source_ed)
            self.assertIn("Behavior prop validation profile: candle_prop", infos[0][1])

            staged_calls.clear()
            infos.clear()
            try:
                method_globals["filedialog"] = FakeFileDialog
                method_globals["messagebox"] = FakeMessagebox
                method_globals["dat_compiler_strategy"] = FakeCompilerStrategy
                app = object.__new__(mm9_editor_app.EditorApp)
                app.active = FakeLevel()
                app.cfg = types.SimpleNamespace(
                    work_dir=tmp,
                    editor_dir=tmp,
                    game_data_dir=os.path.join(tmp, "game", "data"),
                )

                mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat(
                    app,
                    behavior_prop_validation_profile="brazier",
                )
            finally:
                method_globals["filedialog"] = old_filedialog
                method_globals["messagebox"] = old_messagebox
                method_globals["dat_compiler_strategy"] = old_compiler

            self.assertEqual(
                staged_calls["output_filename"],
                "ANSKRAMKEEP_reconstructed_brazier_validation.ed",
            )
            self.assertTrue(staged_calls["include_low_risk_behavior_prop_objects"])
            self.assertEqual(staged_calls["low_risk_behavior_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_wall_torch_objects"])
            self.assertEqual(staged_calls["wall_torch_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_fire_objects"])
            self.assertEqual(staged_calls["fire_source_ed_path"], source_ed)
            self.assertFalse(staged_calls["include_candle_prop_objects"])
            self.assertEqual(staged_calls["candle_prop_source_ed_path"], "")
            self.assertTrue(staged_calls["include_brazier_objects"])
            self.assertEqual(staged_calls["brazier_source_ed_path"], source_ed)
            self.assertFalse(staged_calls["include_treasure_chest_objects"])
            self.assertEqual(staged_calls["treasure_chest_source_ed_path"], "")
            self.assertFalse(staged_calls["include_prop_damager_objects"])
            self.assertEqual(staged_calls["prop_damager_source_ed_path"], "")
            self.assertFalse(staged_calls["include_destructable_prop_objects"])
            self.assertEqual(staged_calls["destructable_prop_source_ed_path"], "")
            self.assertEqual(staged_calls["behavior_prop_report_kwargs"]["source_ed_path"], source_ed)
            self.assertIn("Behavior prop validation profile: brazier", infos[0][1])

            staged_calls.clear()
            infos.clear()
            try:
                method_globals["filedialog"] = FakeFileDialog
                method_globals["messagebox"] = FakeMessagebox
                method_globals["dat_compiler_strategy"] = FakeCompilerStrategy
                app = object.__new__(mm9_editor_app.EditorApp)
                app.active = FakeLevel()
                app.cfg = types.SimpleNamespace(
                    work_dir=tmp,
                    editor_dir=tmp,
                    game_data_dir=os.path.join(tmp, "game", "data"),
                )

                mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat(
                    app,
                    behavior_prop_validation_profile="treasure_chest",
                )
            finally:
                method_globals["filedialog"] = old_filedialog
                method_globals["messagebox"] = old_messagebox
                method_globals["dat_compiler_strategy"] = old_compiler

            self.assertEqual(
                staged_calls["output_filename"],
                "ANSKRAMKEEP_reconstructed_treasure_chest_validation.ed",
            )
            self.assertTrue(staged_calls["include_low_risk_behavior_prop_objects"])
            self.assertEqual(staged_calls["low_risk_behavior_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_wall_torch_objects"])
            self.assertEqual(staged_calls["wall_torch_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_fire_objects"])
            self.assertEqual(staged_calls["fire_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_candle_prop_objects"])
            self.assertEqual(staged_calls["candle_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_brazier_objects"])
            self.assertEqual(staged_calls["brazier_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_treasure_chest_objects"])
            self.assertEqual(staged_calls["treasure_chest_source_ed_path"], source_ed)
            self.assertFalse(staged_calls["include_prop_damager_objects"])
            self.assertEqual(staged_calls["prop_damager_source_ed_path"], "")
            self.assertFalse(staged_calls["include_destructable_prop_objects"])
            self.assertEqual(staged_calls["destructable_prop_source_ed_path"], "")
            self.assertEqual(staged_calls["behavior_prop_report_kwargs"]["source_ed_path"], source_ed)
            self.assertIn("Behavior prop validation profile: treasure_chest", infos[0][1])

            staged_calls.clear()
            infos.clear()
            try:
                method_globals["filedialog"] = FakeFileDialog
                method_globals["messagebox"] = FakeMessagebox
                method_globals["dat_compiler_strategy"] = FakeCompilerStrategy
                app = object.__new__(mm9_editor_app.EditorApp)
                app.active = FakeLevel()
                app.cfg = types.SimpleNamespace(
                    work_dir=tmp,
                    editor_dir=tmp,
                    game_data_dir=os.path.join(tmp, "game", "data"),
                )

                mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat(
                    app,
                    behavior_prop_validation_profile="prop_damager",
                )
            finally:
                method_globals["filedialog"] = old_filedialog
                method_globals["messagebox"] = old_messagebox
                method_globals["dat_compiler_strategy"] = old_compiler

            self.assertEqual(
                staged_calls["output_filename"],
                "ANSKRAMKEEP_reconstructed_prop_damager_validation.ed",
            )
            self.assertTrue(staged_calls["include_low_risk_behavior_prop_objects"])
            self.assertEqual(staged_calls["low_risk_behavior_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_wall_torch_objects"])
            self.assertEqual(staged_calls["wall_torch_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_fire_objects"])
            self.assertEqual(staged_calls["fire_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_candle_prop_objects"])
            self.assertEqual(staged_calls["candle_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_brazier_objects"])
            self.assertEqual(staged_calls["brazier_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_treasure_chest_objects"])
            self.assertEqual(staged_calls["treasure_chest_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_prop_damager_objects"])
            self.assertEqual(staged_calls["prop_damager_source_ed_path"], source_ed)
            self.assertFalse(staged_calls["include_destructable_prop_objects"])
            self.assertEqual(staged_calls["destructable_prop_source_ed_path"], "")
            self.assertEqual(staged_calls["behavior_prop_report_kwargs"]["source_ed_path"], source_ed)
            self.assertIn("Behavior prop validation profile: prop_damager", infos[0][1])

            staged_calls.clear()
            infos.clear()
            try:
                method_globals["filedialog"] = FakeFileDialog
                method_globals["messagebox"] = FakeMessagebox
                method_globals["dat_compiler_strategy"] = FakeCompilerStrategy
                app = object.__new__(mm9_editor_app.EditorApp)
                app.active = FakeLevel()
                app.cfg = types.SimpleNamespace(
                    work_dir=tmp,
                    editor_dir=tmp,
                    game_data_dir=os.path.join(tmp, "game", "data"),
                )

                mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat(
                    app,
                    behavior_prop_validation_profile="destructable_prop",
                )
            finally:
                method_globals["filedialog"] = old_filedialog
                method_globals["messagebox"] = old_messagebox
                method_globals["dat_compiler_strategy"] = old_compiler

            self.assertEqual(
                staged_calls["output_filename"],
                "ANSKRAMKEEP_reconstructed_destructable_prop_validation.ed",
            )
            self.assertTrue(staged_calls["include_validation_floor"])
            self.assertFalse(staged_calls["include_physics_shell_patch"])
            self.assertTrue(staged_calls["include_low_risk_behavior_prop_objects"])
            self.assertEqual(staged_calls["low_risk_behavior_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_wall_torch_objects"])
            self.assertEqual(staged_calls["wall_torch_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_fire_objects"])
            self.assertEqual(staged_calls["fire_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_candle_prop_objects"])
            self.assertEqual(staged_calls["candle_prop_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_brazier_objects"])
            self.assertEqual(staged_calls["brazier_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_treasure_chest_objects"])
            self.assertEqual(staged_calls["treasure_chest_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_prop_damager_objects"])
            self.assertEqual(staged_calls["prop_damager_source_ed_path"], source_ed)
            self.assertTrue(staged_calls["include_destructable_prop_objects"])
            self.assertEqual(staged_calls["destructable_prop_source_ed_path"], source_ed)
            self.assertEqual(staged_calls["behavior_prop_report_kwargs"]["source_ed_path"], source_ed)
            self.assertIn("DestructableProp objects: included", infos[0][1])
            self.assertIn("Behavior prop validation profile: destructable_prop", infos[0][1])


if __name__ == "__main__":
    unittest.main()
