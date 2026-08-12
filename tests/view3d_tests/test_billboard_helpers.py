import os
import sys
import unittest


from tests._path import ROOT  # noqa: F401

from catalog import categorize
from view3d.gl_objects import (
    _build_arrays,
    hidden_world_helper_model_names,
    is_editor_helper_billboard,
    is_world_helper_billboard,
    should_draw_billboard_for_modeled_object,
)


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
    def test_hidden_world_helper_names_drive_matching_bsp_classification(self):
        barrier = obj("AIBarrier", "AIBarrier51")
        barrier.props["Visible"] = 0
        visible_helper = obj("AIBarrier", "AIBarrier52")
        visible_helper.props["Visible"] = 1
        metadata = {"AIBarrier": {"world_helper": {"is_helper": True}}}

        self.assertEqual(
            hidden_world_helper_model_names(
                [barrier, visible_helper],
                categorize,
                metadata,
            ),
            {"aibarrier51"},
        )

    def test_world_helper_billboards_are_hidden_by_default(self):
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
            include_world_helpers=False,
            selected_index=-1,
        )

        self.assertEqual(indices, [4, 5])

    def test_selected_world_helper_billboard_stays_visible(self):
        objects = [
            obj("ExitTrigger"),
            obj("Prop"),
        ]

        _verts, indices = _build_arrays(
            objects,
            categorize,
            include_world_helpers=False,
            selected_index=0,
        )

        self.assertEqual(indices, [0, 1])

    def test_world_helper_toggle_can_include_everything(self):
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
            include_world_helpers=True,
            selected_index=-1,
        )

        self.assertEqual(indices, [0, 1, 2, 3, 4])

    def test_invisible_modeled_object_does_not_fall_back_to_billboard(self):
        candle = obj("CandleProp")
        candle.props.update({
            "Filename": r"models\Props\Candle.ABC",
            "Visible": 0,
        })

        _verts, indices = _build_arrays(
            [candle],
            categorize,
            include_world_helpers=True,
            selected_index=-1,
        )

        self.assertEqual(indices, [])

    def test_selected_invisible_object_keeps_selection_billboard(self):
        candle = obj("CandleProp")
        candle.props.update({
            "Filename": r"models\Props\Candle.ABC",
            "Visible": "0",
        })

        _verts, indices = _build_arrays(
            [candle],
            categorize,
            include_world_helpers=False,
            selected_index=0,
        )

        self.assertEqual(indices, [0])

    def test_modeled_world_category_objects_survive_world_helper_filter(self):
        objects = [
            obj("Door"),
            obj("AmbientSound"),
            obj("Prop"),
        ]

        _verts, indices = _build_arrays(
            objects,
            categorize,
            include_world_helpers=False,
            object_helper_indices={0},
            selected_index=-1,
        )

        self.assertEqual(indices, [0, 2])

    def test_world_helper_predicate_matches_known_noisy_classes(self):
        self.assertTrue(is_world_helper_billboard(obj("BlueWater"), categorize))
        self.assertTrue(is_world_helper_billboard(obj("ExitTrigger"), categorize))
        self.assertTrue(is_world_helper_billboard(obj("AIRail", "AITrk0"), categorize))
        self.assertTrue(is_world_helper_billboard(obj("AmbientSound"), categorize))
        self.assertFalse(is_world_helper_billboard(obj("Prop"), categorize))
        self.assertFalse(is_world_helper_billboard(obj("ColloidalWarrior"), categorize))

    def test_catalog_metadata_classifies_classes_without_name_rules(self):
        metadata = {
            "LoMMServiceNode": {
                "world_helper": {
                    "is_helper": True,
                    "reason": "non_actor_without_model_resource",
                    "source": "object.lto",
                }
            },
            "LoMMVisibleNode": {
                "world_helper": {
                    "is_helper": False,
                    "reason": "model_hierarchy",
                    "source": "object.lto",
                }
            },
        }

        self.assertTrue(is_world_helper_billboard(
            obj("LoMMServiceNode"), categorize, metadata
        ))
        self.assertFalse(is_world_helper_billboard(
            obj("LoMMVisibleNode"), categorize, metadata
        ))

    def test_explicit_instance_model_overrides_helper_class_metadata(self):
        metadata = {
            "SpecialNode": {"world_helper": {"is_helper": True}},
        }
        modeled = obj("SpecialNode")
        modeled.props["Filename"] = r"models\special.abc"

        self.assertFalse(is_world_helper_billboard(
            modeled, categorize, metadata
        ))

    def test_actor_properties_preserve_converted_lomm_actor_without_metadata(self):
        actor = obj("LoMMOnlyMonster")
        actor.props["SightDistance"] = 1000.0

        self.assertFalse(is_world_helper_billboard(actor, categorize))

    def test_editor_helper_alias_still_points_to_world_helpers(self):
        self.assertTrue(is_editor_helper_billboard(obj("AmbientSound"), categorize))
        self.assertFalse(is_editor_helper_billboard(obj("Prop"), categorize))

    def test_modeled_object_billboards_are_hidden_unless_enabled_or_marked(self):
        modeled = {2}

        self.assertFalse(should_draw_billboard_for_modeled_object(
            2, modeled, selected_index=-1, drag_index=-1,
            show_object_helpers=False,
        ))
        self.assertTrue(should_draw_billboard_for_modeled_object(
            2, modeled, selected_index=-1, drag_index=-1,
            show_object_helpers=True,
        ))
        self.assertTrue(should_draw_billboard_for_modeled_object(
            2, modeled, selected_index=2, drag_index=-1,
            show_object_helpers=False,
        ))
        self.assertTrue(should_draw_billboard_for_modeled_object(
            2, modeled, selected_index=-1, drag_index=2,
            show_object_helpers=False,
        ))
        self.assertTrue(should_draw_billboard_for_modeled_object(
            3, modeled, selected_index=-1, drag_index=-1,
            show_object_helpers=False,
        ))


if __name__ == "__main__":
    unittest.main()
