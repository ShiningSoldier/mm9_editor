import os
import sys
import tempfile
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import autodetect


class AutodetectTests(unittest.TestCase):
    def _touch(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"")

    def test_detects_game_data_in_parent_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            game_root = os.path.join(tmp, "Might and Magic 9")
            editor_dir = os.path.join(game_root, "mm9_editor")
            data_dir = os.path.join(game_root, "data")
            os.makedirs(editor_dir)
            for name in ("WORLDS.REZ", "RUDE.REZ", "SCRIPTS.REZ"):
                self._touch(os.path.join(data_dir, name))

            paths = autodetect.detect(editor_dir)

            self.assertEqual(os.path.abspath(paths.game_root), os.path.abspath(game_root))
            self.assertEqual(os.path.abspath(paths.game_data_dir), os.path.abspath(data_dir))
            self.assertTrue(paths.has_archive("worlds"))
            self.assertEqual(paths.archive_path("rude"), os.path.join(data_dir, "RUDE.REZ"))

    def test_explicit_game_root_must_have_required_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            editor_dir = os.path.join(tmp, "editor")
            game_root = os.path.join(tmp, "game")
            os.makedirs(os.path.join(game_root, "data"))
            os.makedirs(editor_dir)

            with self.assertRaises(autodetect.GameNotFoundError):
                autodetect.detect(editor_dir, game_root=game_root)

    def test_extracted_data_without_game_archives_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            editor_dir = os.path.join(tmp, "mm9_editor")
            worlds_dir = os.path.join(editor_dir, "legacy_extracted", "WORLDS")
            os.makedirs(worlds_dir)

            with self.assertRaises(autodetect.GameNotFoundError):
                autodetect.detect(editor_dir)


if __name__ == "__main__":
    unittest.main()
