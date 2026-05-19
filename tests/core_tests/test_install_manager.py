import json
import os
import sys
import tempfile
import unittest


from tests._path import ROOT  # noqa: F401

from core import install_manager


def write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


class InstallManagerTests(unittest.TestCase):
    def test_install_batch_backs_up_and_replaces_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = os.path.join(tmp, "output", "20260513_120000")
            game_data = os.path.join(tmp, "game", "data")
            backups = os.path.join(tmp, "backups")
            write(os.path.join(batch, "data", "WORLDS.REZ"), b"patched worlds")
            write(os.path.join(batch, "data", "RUDE.REZ"), b"patched rude")
            write(os.path.join(game_data, "WORLDS.REZ"), b"original worlds")
            write(os.path.join(game_data, "RUDE.REZ"), b"original rude")

            result = install_manager.install_batch(batch, game_data, backups)

            self.assertEqual(read(os.path.join(game_data, "WORLDS.REZ")),
                             b"patched worlds")
            self.assertEqual(read(os.path.join(game_data, "RUDE.REZ")),
                             b"patched rude")
            backup_worlds = os.path.join(result.backup_dir, "WORLDS.REZ")
            backup_rude = os.path.join(result.backup_dir, "RUDE.REZ")
            self.assertEqual(read(backup_worlds), b"original worlds")
            self.assertEqual(read(backup_rude), b"original rude")
            self.assertTrue(os.path.isfile(result.manifest_path))

    def test_manifest_limits_archives_to_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = os.path.join(tmp, "output", "20260513_120000")
            game_data = os.path.join(tmp, "game", "data")
            backups = os.path.join(tmp, "backups")
            write(os.path.join(batch, "data", "WORLDS.REZ"), b"patched worlds")
            write(os.path.join(batch, "data", "RUDE.REZ"), b"patched rude")
            write(os.path.join(game_data, "WORLDS.REZ"), b"original worlds")
            write(os.path.join(game_data, "RUDE.REZ"), b"original rude")
            manifest = {
                "archives": [
                    {"output_archive": os.path.join(batch, "data", "RUDE.REZ")},
                ],
            }
            with open(os.path.join(batch, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f)

            result = install_manager.install_batch(batch, game_data, backups)

            self.assertEqual([item.name for item in result.archives], ["RUDE.REZ"])
            self.assertEqual(read(os.path.join(game_data, "WORLDS.REZ")),
                             b"original worlds")
            self.assertEqual(read(os.path.join(game_data, "RUDE.REZ")),
                             b"patched rude")

    def test_missing_target_archive_fails_before_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = os.path.join(tmp, "output", "20260513_120000")
            game_data = os.path.join(tmp, "game", "data")
            backups = os.path.join(tmp, "backups")
            write(os.path.join(batch, "data", "WORLDS.REZ"), b"patched")
            os.makedirs(game_data)

            with self.assertRaises(install_manager.InstallError):
                install_manager.install_batch(batch, game_data, backups)

    def test_restore_backup_restores_originals_and_saves_current_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = os.path.join(tmp, "output", "20260513_120000")
            game_data = os.path.join(tmp, "game", "data")
            backups = os.path.join(tmp, "backups")
            write(os.path.join(batch, "data", "WORLDS.REZ"), b"patched worlds")
            write(os.path.join(batch, "data", "RUDE.REZ"), b"patched rude")
            write(os.path.join(game_data, "WORLDS.REZ"), b"original worlds")
            write(os.path.join(game_data, "RUDE.REZ"), b"original rude")

            install_result = install_manager.install_batch(batch, game_data, backups)
            restore_result = install_manager.restore_backup(
                os.path.dirname(install_result.backup_dir),
                safety_backup_root=backups,
            )

            self.assertEqual(read(os.path.join(game_data, "WORLDS.REZ")),
                             b"original worlds")
            self.assertEqual(read(os.path.join(game_data, "RUDE.REZ")),
                             b"original rude")
            self.assertEqual(read(os.path.join(restore_result.safety_backup_dir, "WORLDS.REZ")),
                             b"patched worlds")
            self.assertEqual(read(os.path.join(restore_result.safety_backup_dir, "RUDE.REZ")),
                             b"patched rude")
            self.assertTrue(os.path.isfile(restore_result.manifest_path))

    def test_backups_to_restore_accepts_data_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup_data = os.path.join(tmp, "backups", "install_20260513", "data")
            write(os.path.join(backup_data, "WORLDS.REZ"), b"original")

            archives = install_manager.backups_to_restore(backup_data)

            self.assertEqual(archives, [os.path.join(backup_data, "WORLDS.REZ")])


if __name__ == "__main__":
    unittest.main()
