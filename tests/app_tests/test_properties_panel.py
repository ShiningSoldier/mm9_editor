import unittest
import types
from unittest.mock import MagicMock
import mm9_patch as patcher
from core import project as P
from tests._path import ROOT
import os
import importlib.util

_EDITOR_PATH = os.path.join(ROOT, "mm9_editor.py")
_SPEC = importlib.util.spec_from_file_location("mm9_editor_app", _EDITOR_PATH)
mm9_editor_app = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(mm9_editor_app)

class PropertiesPanelTests(unittest.TestCase):
    def test_on_property_edited_single_value(self):
        app = object.__new__(mm9_editor_app.EditorApp)
        app._flush_view_transforms = MagicMock()
        app._refresh_after_edit = MagicMock()
        
        # Setup LevelEdit active
        level = object.__new__(P.LevelEdit)
        level.ops = []
        level.redo_ops = []
        level.clear_redo = MagicMock()
        level.prefab_import_for_materialized = MagicMock(return_value=None)
        level.existing_index_for_materialized = MagicMock(return_value=42)
        level.append_op = MagicMock()
        app.active = level

        # Setup properties panel and selected object
        obj = patcher.WorldObject("Actor", [patcher.Property("Name", 0, 0, "Bob")])
        props_panel = MagicMock()
        props_panel.current_obj = obj
        app.props_panel = props_panel
        app._selected_world_index = 0

        # Call method with a single value update
        app._on_property_edited("WanderON", 1)

        # Verify EditOp was appended with WanderON override
        level.append_op.assert_called_once()
        op = level.append_op.call_args[0][0]
        self.assertIsInstance(op, P.EditOp)
        self.assertEqual(op.target_index, 42)
        self.assertEqual(op.overrides, {"WanderON": 1})
        app._refresh_after_edit.assert_called_with(0)

    def test_on_property_edited_multiple_values(self):
        app = object.__new__(mm9_editor_app.EditorApp)
        app._flush_view_transforms = MagicMock()
        app._refresh_after_edit = MagicMock()
        
        # Setup LevelEdit active
        level = object.__new__(P.LevelEdit)
        level.ops = []
        level.redo_ops = []
        level.clear_redo = MagicMock()
        level.prefab_import_for_materialized = MagicMock(return_value=None)
        level.existing_index_for_materialized = MagicMock(return_value=42)
        level.append_op = MagicMock()
        app.active = level

        # Setup properties panel and selected object
        obj = patcher.WorldObject("Actor", [patcher.Property("Name", 0, 0, "Bob")])
        props_panel = MagicMock()
        props_panel.current_obj = obj
        app.props_panel = props_panel
        app._selected_world_index = 0

        # Call method with a dictionary of updates
        updates = {"WanderON": 1, "Solid": 0}
        app._on_property_edited(updates)

        # Verify EditOp was appended with both overrides in a single operation
        level.append_op.assert_called_once()
        op = level.append_op.call_args[0][0]
        self.assertIsInstance(op, P.EditOp)
        self.assertEqual(op.target_index, 42)
        self.assertEqual(op.overrides, {"WanderON": 1, "Solid": 0})
        app._refresh_after_edit.assert_called_with(0)


if __name__ == "__main__":
    unittest.main()
