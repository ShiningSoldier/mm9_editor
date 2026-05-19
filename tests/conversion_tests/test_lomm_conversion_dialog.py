import os
import unittest

from tests._path import ROOT  # noqa: F401

from ui import lomm_conversion_dialog as dialog


class FakeConversion:
    source_virtual_path = "WORLDS/SOURCE"


class FakeInsertResult:
    added_virtual_path = "WORLDS/NEWLEVEL"
    worlds_rez = os.path.join("C:", "MM9", "data", "WORLDS.REZ")
    backup_path = os.path.join("C:", "MM9", "mm9_editor", "backups", "WORLDS.REZ")
    conversion = FakeConversion()


class LommConversionDialogHelperTests(unittest.TestCase):
    def test_default_converted_name_normalizes_world_entry(self):
        self.assertEqual(
            dialog.default_converted_name("WORLDS/CHATEAUESCAPE.DAT"),
            "CHATEAUESCAPE_MM9",
        )

    def test_success_message_mentions_archive_and_backup(self):
        msg = dialog.format_success_message(FakeInsertResult())

        self.assertIn("WORLDS/NEWLEVEL", msg)
        self.assertIn("WORLDS.REZ", msg)
        self.assertIn("Backup", msg)


if __name__ == "__main__":
    unittest.main()
