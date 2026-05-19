import json
import os
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from conversion import lomm_to_mm9_service as service
from core import install_manager
from core import rezmgr
from tests.core_tests.test_game_resources import write_minimal_rez
from tests.core_tests.test_project_rez_output import make_world_bytes, load_world_from_bytes


class LommToMm9ServiceTests(unittest.TestCase):
    def _write_config(self, folder: str) -> str:
        path = os.path.join(folder, "rules.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "remove_unknown_classes": True,
                "extra_remove_classes": [],
                "keep_classes": [],
                "patch_class": {},
                "convert_class": {},
            }, f)
        return path

    def _make_mm9_root(self, tmp: str, worlds_entries=None) -> str:
        root = os.path.join(tmp, "Might and Magic IX")
        data = os.path.join(root, "data")
        write_minimal_rez(
            os.path.join(data, "WORLDS.REZ"),
            worlds_entries or {"WORLDS/MM9LEVEL": make_world_bytes("MM9")},
        )
        write_minimal_rez(os.path.join(data, "RUDE.REZ"), {"RUDE/NPCNAME": b""})
        write_minimal_rez(os.path.join(data, "SCRIPTS.REZ"), {"SCRIPTS/EMPTY": b""})
        return root

    def _make_lomm_root(self, tmp: str, worlds_entries=None) -> str:
        root = os.path.join(tmp, "Legends of Might and Magic")
        data = os.path.join(root, "Data")
        write_minimal_rez(
            os.path.join(data, "worlds.rez"),
            worlds_entries or {"WORLDS/CHATEAUESCAPE": make_world_bytes("LoMM")},
        )
        write_minimal_rez(os.path.join(data, "RUDE.REZ"), {"RUDE/NPCNAME": b""})
        write_minimal_rez(os.path.join(data, "SCRIPTS.REZ"), {"SCRIPTS/EMPTY": b""})
        return root

    def test_validates_mm9_and_lomm_roots_case_insensitively(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = self._make_mm9_root(tmp)
            lomm_root = self._make_lomm_root(tmp)

            mm9 = service.validate_mm9_root(mm9_root)
            lomm = service.validate_lomm_root(lomm_root)

            self.assertTrue(mm9.worlds_rez.endswith("WORLDS.REZ"))
            self.assertTrue(lomm.worlds_rez.lower().endswith("worlds.rez"))
            self.assertTrue(lomm.rude_rez.endswith("RUDE.REZ"))
            self.assertTrue(lomm.scripts_rez.endswith("SCRIPTS.REZ"))

    def test_rejects_mm9_root_without_required_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "bad")
            os.makedirs(os.path.join(root, "data"))

            with self.assertRaisesRegex(service.ConversionServiceError, "missing required"):
                service.validate_mm9_root(root)

    def test_rejects_lomm_root_without_required_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "bad_lomm")
            data = os.path.join(root, "Data")
            write_minimal_rez(
                os.path.join(data, "worlds.rez"),
                {"WORLDS/CHATEAUESCAPE": make_world_bytes("LoMM")},
            )

            with self.assertRaisesRegex(service.ConversionServiceError, "missing required"):
                service.validate_lomm_root(root)

    def test_lists_only_v66_lomm_levels(self):
        with tempfile.TemporaryDirectory() as tmp:
            lomm_root = self._make_lomm_root(tmp, {
                "WORLDS/CHATEAUESCAPE": make_world_bytes("LoMM"),
                "WORLDS/EDITORFILE": (1249).to_bytes(4, "little") + b"ed",
            })

            levels = service.list_lomm_levels(lomm_root)

            self.assertEqual([level.display_name for level in levels], ["CHATEAUESCAPE"])

    def test_rejects_duplicate_output_level_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = self._make_mm9_root(tmp, {
                "WORLDS/EXISTS": make_world_bytes("MM9"),
            })

            with self.assertRaisesRegex(service.ConversionServiceError, "already contains"):
                service.ensure_output_level_available(mm9_root, "EXISTS")

    def test_converts_lomm_level_to_mm9_dat_bytes_without_writing_rez(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = self._make_mm9_root(tmp, {
                "WORLDS/MM9LEVEL": make_world_bytes("MM9"),
            })
            lomm_root = self._make_lomm_root(tmp, {
                "WORLDS/CHATEAUESCAPE": make_world_bytes("LoMM"),
            })
            config_path = self._write_config(tmp)
            worlds_rez = service.validate_mm9_root(mm9_root).worlds_rez
            before_size = os.path.getsize(worlds_rez)

            result = service.convert_level_to_bytes(service.ConvertLevelRequest(
                mm9_root=mm9_root,
                lomm_root=lomm_root,
                level_to_convert="CHATEAUESCAPE",
                converted_level_name="NEWLEVEL",
                config_path=config_path,
            ))

            self.assertEqual(result.source_virtual_path, "WORLDS/CHATEAUESCAPE")
            self.assertEqual(result.output_virtual_path, "WORLDS/NEWLEVEL")
            self.assertTrue(rezmgr.is_v66_dat_magic(result.dat_bytes[:4]))
            self.assertEqual(os.path.getsize(worlds_rez), before_size)
            world = load_world_from_bytes(result.dat_bytes)
            self.assertEqual(world.objects[0].get("Name"), "LoMM")

    def test_convert_and_insert_adds_level_and_backs_up_original_worlds_rez(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = self._make_mm9_root(tmp, {
                "WORLDS/MM9LEVEL": make_world_bytes("MM9"),
            })
            lomm_root = self._make_lomm_root(tmp, {
                "WORLDS/CHATEAUESCAPE": make_world_bytes("LoMM"),
            })
            config_path = self._write_config(tmp)
            backup_root = os.path.join(tmp, "backups")
            worlds_rez = service.validate_mm9_root(mm9_root).worlds_rez
            with open(worlds_rez, "rb") as f:
                original_worlds = f.read()

            result = service.convert_and_insert_level(
                service.ConvertLevelRequest(
                    mm9_root=mm9_root,
                    lomm_root=lomm_root,
                    level_to_convert="CHATEAUESCAPE",
                    converted_level_name="NEWLEVEL",
                    config_path=config_path,
                ),
                backup_root=backup_root,
            )

            self.assertTrue(os.path.isfile(result.backup_path))
            self.assertTrue(os.path.isfile(result.manifest_path))
            with open(result.backup_path, "rb") as f:
                self.assertEqual(f.read(), original_worlds)
            with open(result.manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["game_data_dir"], os.path.dirname(worlds_rez))
            self.assertEqual(manifest["backup_dir"], result.backup_dir)
            self.assertEqual(manifest["conversion"]["kind"], "lomm_to_mm9")
            self.assertEqual(manifest["conversion"]["source_virtual_path"], "WORLDS/CHATEAUESCAPE")
            self.assertEqual(manifest["conversion"]["added_virtual_path"], "WORLDS/NEWLEVEL")
            self.assertEqual(
                install_manager.backups_to_restore(os.path.dirname(result.backup_dir)),
                [result.backup_path],
            )
            self.assertFalse(os.path.exists(result.temp_output_path))
            with rezmgr.RezReader(worlds_rez) as reader:
                self.assertIn("WORLDS/NEWLEVEL", reader.list_paths())
                self.assertIn("WORLDS/MM9LEVEL", reader.list_paths())
                world = load_world_from_bytes(reader.extract_to_bytes("WORLDS/NEWLEVEL"))
            self.assertEqual(world.objects[0].get("Name"), "LoMM")


if __name__ == "__main__":
    unittest.main()
