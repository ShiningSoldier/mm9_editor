import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from catalog import categorize
from view3d.gl_objects import _build_arrays, is_editor_helper_billboard


class FakeObject:
    def __init__(self, type_str, **props):
        self.type_str = type_str
        self.props = props

    def get(self, name, default=None):
        return self.props.get(name, default)


def obj(type_str, name=None):
    props = {"Pos": (1.0, 2.0, 3.0)}
    if name:
        props["Name"] = name
    return FakeObject(type_str, **props)


class HelperBillboardTests(unittest.TestCase):
    def test_helper_billboards_are_hidden_by_default(self):
        objects = [
            obj("BlueWater"),
            obj("ExitTrigger"),
            obj("AIRail", "AITrk0"),
            obj("AmbientSound"),
            obj("Prop"),
            obj("ColloidalWarrior"),
        ]

        _verts, indices = _build_arrays(
            objects,
            categorize,
            include_helpers=False,
            selected_index=-1,
        )

        self.assertEqual(indices, [4, 5])

    def test_selected_helper_billboard_stays_visible(self):
        objects = [
            obj("ExitTrigger"),
            obj("Prop"),
        ]

        _verts, indices = _build_arrays(
            objects,
            categorize,
            include_helpers=False,
            selected_index=0,
        )

        self.assertEqual(indices, [0, 1])

    def test_helper_toggle_can_include_everything(self):
        objects = [
            obj("BlueWater"),
            obj("ExitTrigger"),
            obj("AIRail", "AITrk0"),
            obj("AmbientSound"),
            obj("Prop"),
        ]

        _verts, indices = _build_arrays(
            objects,
            categorize,
            include_helpers=True,
            selected_index=-1,
        )

        self.assertEqual(indices, [0, 1, 2, 3, 4])

    def test_helper_predicate_matches_known_noisy_classes(self):
        self.assertTrue(is_editor_helper_billboard(obj("BlueWater"), categorize))
        self.assertTrue(is_editor_helper_billboard(obj("ExitTrigger"), categorize))
        self.assertTrue(is_editor_helper_billboard(obj("AIRail", "AITrk0"), categorize))
        self.assertTrue(is_editor_helper_billboard(obj("AmbientSound"), categorize))
        self.assertFalse(is_editor_helper_billboard(obj("Prop"), categorize))
        self.assertFalse(is_editor_helper_billboard(obj("ColloidalWarrior"), categorize))


if __name__ == "__main__":
    unittest.main()
