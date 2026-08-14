import unittest

from tests._path import ROOT  # noqa: F401

from catalog.templates import class_template_from_catalog


class CatalogTemplateTests(unittest.TestCase):
    def test_builds_object_from_object_lto_schema_even_when_dat_template_is_elsewhere(self):
        catalog = {"classes": {"WorldObject": {
            "template": {"source_level": "BOOTCAMP.DAT"},
            "object_lto": {"template_properties": [
                {"name": "Name", "code": 0, "flags": 0, "value": "noname"},
                {"name": "Pos", "code": 1, "flags": 0, "value": [0, 0, 0]},
                {"name": "Visible", "code": 5, "flags": 0, "value": True},
            ]},
        }}}

        template = class_template_from_catalog(catalog, "WorldObject")

        self.assertEqual(template.type_str, "WorldObject")
        self.assertEqual(template.get("Name"), "noname")
        self.assertEqual(template.get("Pos"), [0, 0, 0])
        self.assertTrue(template.get("Visible"))

    def test_missing_object_lto_schema_does_not_clone_observed_dat_data(self):
        catalog = {"classes": {"WorldObject": {
            "template": {"source_level": "BOOTCAMP.DAT", "properties": []},
        }}}

        self.assertIsNone(class_template_from_catalog(catalog, "WorldObject"))


if __name__ == "__main__":
    unittest.main()
