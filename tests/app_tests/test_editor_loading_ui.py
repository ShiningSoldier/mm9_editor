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


class EditorWindowTests(unittest.TestCase):
    def test_maximize_uses_native_zoomed_state(self):
        root = _FakeRoot()

        editor._maximize_window(root)

        self.assertEqual(root.calls, [("state", "zoomed")])

    def test_maximize_falls_back_to_screen_geometry(self):
        root = _FakeRoot(state_error=True, attributes_error=True)

        editor._maximize_window(root)

        self.assertEqual(root.calls[-1], ("geometry", "1920x1080+0+0"))


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
