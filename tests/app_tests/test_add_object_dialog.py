import unittest

from ui.add_object_dialog import (
    _SCOPE_ALL,
    _SCOPE_OBSERVED,
    _class_detail_text,
    _class_display_label,
    _class_matches_scope,
)


class AddObjectDialogSemanticsTests(unittest.TestCase):
    def test_unplaced_object_lto_class_label_is_explicit(self):
        entry = {
            "source": "object.lto",
            "instance_count": 0,
            "template": {"source_level": "object.lto"},
        }

        self.assertEqual(
            _class_display_label("LizardOrcMage", entry),
            "LizardOrcMage  (unplaced)",
        )

    def test_zero_count_non_object_lto_class_label_is_explicit(self):
        entry = {
            "source": "dat",
            "instance_count": 0,
            "template": {"source_level": "BOOTCAMP.DAT"},
        }

        self.assertEqual(
            _class_display_label("UnusedTemplate", entry),
            "UnusedTemplate  (0 instances)",
        )

    def test_scope_filter_can_hide_unobserved_classes(self):
        unplaced = {"source": "object.lto", "instance_count": 0}
        observed = {"source": "object.lto+dat", "instance_count": 3}

        self.assertTrue(_class_matches_scope(unplaced, _SCOPE_ALL))
        self.assertFalse(_class_matches_scope(unplaced, _SCOPE_OBSERVED))
        self.assertTrue(_class_matches_scope(observed, _SCOPE_OBSERVED))

    def test_detail_text_calls_out_object_lto_defaults(self):
        entry = {
            "category": "monster",
            "source": "object.lto",
            "instance_count": 0,
            "levels": [],
            "template": {"source_level": "object.lto"},
        }

        detail = _class_detail_text("LizardOrcMage", entry)

        self.assertIn("unplaced valid class", detail)
        self.assertIn("source: object.lto", detail)
        self.assertIn("template: object.lto defaults", detail)


if __name__ == "__main__":
    unittest.main()
