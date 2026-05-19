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


if __name__ == "__main__":
    unittest.main()
