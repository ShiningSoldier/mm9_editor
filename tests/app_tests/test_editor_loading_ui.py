import os
import types
import unittest
from unittest import mock


from tests._path import ROOT  # noqa: F401

from app import editor


class _FakeRoot:
    def __init__(self, *, state_error=False, attributes_error=False):
        self.state_error = state_error
        self.attributes_error = attributes_error
        self.calls = []

    def state(self, value):
        self.calls.append(("state", value))
        if self.state_error:
            raise RuntimeError("unsupported")

    def attributes(self, name, value):
        self.calls.append(("attributes", name, value))
        if self.attributes_error:
            raise RuntimeError("unsupported")

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080

    def geometry(self, value):
        self.calls.append(("geometry", value))


class _FakeVariable:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _FakeMenu:
    def __init__(self, _parent, tearoff=None):
        self.tearoff = tearoff
        self.entries = []

    def _add(self, kind, **options):
        self.entries.append({"kind": kind, **options})

    def add_command(self, **options):
        self._add("command", **options)

    def add_separator(self):
        self._add("separator")

    def add_cascade(self, **options):
        self._add("cascade", **options)

    def add_checkbutton(self, **options):
        self._add("checkbutton", **options)

    def add_radiobutton(self, **options):
        self._add("radiobutton", **options)

    def index(self, index):
        if index != "end":
            raise AssertionError(f"unsupported menu index: {index}")
        return len(self.entries) - 1

    def entryconfig(self, index, **options):
        self.entries[index].update(options)


class _FakeMenuRoot:
    def __init__(self):
        self.menu = None

    def config(self, *, menu):
        self.menu = menu

    def destroy(self):
        pass


class _FakeStateWidget:
    def __init__(self):
        self.state = None

    def configure(self, *, state):
        self.state = state


class EditorWindowTests(unittest.TestCase):
    def test_maximize_uses_native_zoomed_state(self):
        root = _FakeRoot()

        editor._maximize_window(root)

        self.assertEqual(root.calls, [("state", "zoomed")])

    def test_maximize_falls_back_to_screen_geometry(self):
        root = _FakeRoot(state_error=True, attributes_error=True)

        editor._maximize_window(root)

        self.assertEqual(root.calls[-1], ("geometry", "1920x1080+0+0"))


class EditorMenuTests(unittest.TestCase):
    def _build_menu(self):
        app = object.__new__(editor.EditorApp)
        app.root = _FakeMenuRoot()
        fake_tk = types.SimpleNamespace(
            Menu=_FakeMenu,
            BooleanVar=_FakeVariable,
            StringVar=_FakeVariable,
        )

        with mock.patch.object(editor, "tk", fake_tk):
            app._build_menu()

        cascades = {
            entry["label"]: entry["menu"]
            for entry in app.root.menu.entries
            if entry["kind"] == "cascade"
        }
        return app, cascades

    @staticmethod
    def _command_labels(menu):
        return [
            entry["label"]
            for entry in menu.entries
            if entry["kind"] == "command"
        ]

    def test_commands_are_grouped_by_workflow(self):
        _app, cascades = self._build_menu()

        self.assertEqual(
            list(cascades),
            ["File", "Edit", "View", "Conversion", "Tools", "Dialogues", "Help"],
        )
        self.assertEqual(
            self._command_labels(cascades["Conversion"]),
            [
                "LoMM to MM9",
                "glTF/GLB to DEDit ED...",
                "DAT to ED (Experimental)...",
                "DAT to glTF...",
            ],
        )
        self.assertEqual(
            self._command_labels(cascades["Tools"]),
            ["Import Prefab...", "New Preset...", "Manage Presets..."],
        )
        self.assertEqual(
            self._command_labels(cascades["Dialogues"]),
            ["Dialogue and Quest Editor...", "Dialogue Script Integration..."],
        )
        self.assertNotIn("Presets", cascades)
        all_commands = [
            label
            for menu in cascades.values()
            for label in self._command_labels(menu)
        ]
        self.assertNotIn("Generate DEDit ED with Reserved Stairs...", all_commands)

    def test_run_current_level_menu_item_starts_disabled(self):
        app, cascades = self._build_menu()

        entry = cascades["File"].entries[app._run_current_level_menu_index]
        self.assertEqual(entry["label"], "Run Current Level")
        self.assertEqual(entry["state"], "disabled")

    def test_level_command_state_tracks_active_level(self):
        app = object.__new__(editor.EditorApp)
        app.run_current_level_button = _FakeStateWidget()
        app._run_current_level_menu = _FakeMenu(None)
        app._run_current_level_menu.add_command(label="Run Current Level")
        app._run_current_level_menu_index = 0

        app._update_level_command_states()

        self.assertEqual(app.run_current_level_button.state, "disabled")
        self.assertEqual(app._run_current_level_menu.entries[0]["state"], "disabled")

        app.active = object()
        app._update_level_command_states()

        self.assertEqual(app.run_current_level_button.state, "normal")
        self.assertEqual(app._run_current_level_menu.entries[0]["state"], "normal")


class LevelLoadingOverlayTests(unittest.TestCase):
    def _app(self):
        calls = []
        app = object.__new__(editor.EditorApp)
        app._loading_overlay = types.SimpleNamespace(
            show=lambda: calls.append("show"),
            pulse=lambda: calls.append("pulse"),
            hide=lambda: calls.append("hide"),
        )
        app.view3d = None
        app.level_panel = types.SimpleNamespace(
            set_active_level=lambda _level: calls.append("panel"),
        )
        app.props_panel = types.SimpleNamespace(
            show=lambda _object: calls.append("properties"),
        )
        app._update_history_menu = lambda: calls.append("history")
        app.root = types.SimpleNamespace(after_idle=lambda _callback: None)
        return app, calls

    def test_level_activation_wraps_all_work_in_one_overlay(self):
        app, calls = self._app()
        level = types.SimpleNamespace(
            display_name="BOOTCAMP",
            rez_vpath="",
            path="BOOTCAMP.DAT",
        )

        with mock.patch.dict(os.environ, {}, clear=True):
            app._set_active(level)

        self.assertEqual(
            calls,
            ["show", "panel", "pulse", "properties", "history", "hide"],
        )

    def test_overlay_is_hidden_when_viewport_loading_raises(self):
        app, calls = self._app()
        app.view3d = types.SimpleNamespace(
            set_active_level=lambda _level: (_ for _ in ()).throw(
                RuntimeError("viewport failed")
            ),
        )
        app._update_view_assets_for_level = lambda _level: None
        level = types.SimpleNamespace(
            display_name="BOOTCAMP",
            rez_vpath="",
            path="BOOTCAMP.DAT",
        )

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "viewport failed"):
                app._set_active(level)

        self.assertEqual(calls, ["show", "pulse", "hide"])


if __name__ == "__main__":
    unittest.main()
