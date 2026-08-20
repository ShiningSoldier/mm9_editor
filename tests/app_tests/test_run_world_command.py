import os
import tempfile
import types
import unittest


from tests._path import ROOT  # noqa: F401

import _path_setup  # noqa: F401
from app import editor


class _Messagebox:
    calls = []

    @classmethod
    def reset(cls):
        cls.calls = []

    @classmethod
    def showwarning(cls, title, body):
        cls.calls.append(("warning", title, body))

    @classmethod
    def showerror(cls, title, body):
        cls.calls.append(("error", title, body))


class RunWorldCommandTests(unittest.TestCase):
    def setUp(self):
        _Messagebox.reset()
        self.messagebox_was_defined = hasattr(editor, "messagebox")
        self.old_messagebox = getattr(editor, "messagebox", None)
        self.old_run_world = editor.run_world
        editor.messagebox = _Messagebox

    def tearDown(self):
        if self.messagebox_was_defined:
            editor.messagebox = self.old_messagebox
        else:
            del editor.messagebox
        editor.run_world = self.old_run_world

    def _app(self, game_root):
        app = object.__new__(editor.EditorApp)
        app.view3d = None
        app.active = types.SimpleNamespace(rez_vpath="WORLDS/LEVEL1")
        app.project = types.SimpleNamespace(work_dir=os.path.join(game_root, "output"))
        app.cfg = types.SimpleNamespace(
            game_root=game_root,
            work_dir=os.path.join(game_root, "fallback-output"),
        )
        app._run_world_session = None
        return app

    def test_command_launches_active_level_and_remembers_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            session = types.SimpleNamespace(
                process=types.SimpleNamespace(poll=lambda: None),
                world_name=r"worlds\LEVEL1",
                session_dir=os.path.join(tmp, "preview"),
            )

            def launch(project, level, **kwargs):
                calls.append((project, level, kwargs))
                return session

            editor.run_world = types.SimpleNamespace(launch_current_level=launch)
            app = self._app(tmp)

            app.cmd_run_current_level()

            self.assertIs(app._run_world_session, session)
            self.assertEqual(calls[0][0], app.project)
            self.assertEqual(calls[0][1], app.active)
            self.assertEqual(calls[0][2]["game_root"], tmp)
            self.assertEqual(calls[0][2]["staging_root"], app.project.work_dir)
            self.assertEqual(_Messagebox.calls, [])

    def test_command_requires_an_open_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(tmp)
            del app.active

            app.cmd_run_current_level()

            self.assertEqual(_Messagebox.calls[0][0:2], ("warning", "No level"))

    def test_command_does_not_launch_a_second_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(tmp)
            app._run_world_session = types.SimpleNamespace(
                process=types.SimpleNamespace(poll=lambda: None),
            )
            editor.run_world = types.SimpleNamespace(
                launch_current_level=lambda *args, **kwargs: self.fail(
                    "second preview was launched"
                )
            )

            app.cmd_run_current_level()

            self.assertEqual(
                _Messagebox.calls[0][0:2],
                ("warning", "MM9 preview already running"),
            )


if __name__ == "__main__":
    unittest.main()
