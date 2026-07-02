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
            self.assertEqual(opened["initial_lomm_root"], os.path.join(tmp, "LoMM"))
            self.assertTrue(callable(opened["on_success"]))

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

    def test_dat_to_ed_command_stages_active_dat_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            staged_calls = {}
            infos = []

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

    def test_dat_to_ed_command_enables_physics_shell_patch_for_non_terrain_levels(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = os.path.join(tmp, "out")
            staged_calls = {}
            infos = []

            class FakeModel:
                def __init__(self, name, polygon_count=1):
                    self.name = name
                    self.points = [(0.0, 0.0, 0.0)]
                    self.polygons = [object()] * polygon_count

                def is_skybox(self):
                    return False

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
                        object_count=4,
                        polygon_count=12,
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

                mm9_editor_app.EditorApp.cmd_generate_dedit_ed_from_dat(app)
            finally:
                method_globals["filedialog"] = old_filedialog
                method_globals["messagebox"] = old_messagebox
                method_globals["dat_compiler_strategy"] = old_compiler

            self.assertEqual(staged_calls["model_names"], ("WorldObject1",))
            self.assertFalse(staged_calls["include_terrain_support_patch"])
            self.assertFalse(staged_calls["include_terrain_support_source_coverage"])
            self.assertTrue(staged_calls["include_physics_shell_patch"])
            self.assertEqual(staged_calls["physics_shell_max_polygons"], 1499)
            self.assertEqual(staged_calls["physics_shell_thickness"], 16.0)
            self.assertTrue(staged_calls["selection_kwargs"]["include_physics_shell_patch"])
            self.assertFalse(staged_calls["selection_kwargs"]["include_terrain_support_patch"])
            self.assertEqual(infos[0][0], "DAT to ED generation complete")


if __name__ == "__main__":
    unittest.main()
