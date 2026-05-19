import os
import importlib.util
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


if __name__ == "__main__":
    unittest.main()
