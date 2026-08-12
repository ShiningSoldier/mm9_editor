import json
import os
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from conversion import lomm_to_mm9 as converter
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

    def _make_actor_world(self, class_name: str, name: str = "Actor1") -> bytes:
        from mm9_patcher import mm9_patch as patcher
        world = patcher.World(
            header=patcher.Header(66, 0, 0, (0,) * 8),
            pre_objects=b"",
            objects=[patcher.WorldObject(class_name, [
                patcher.Property("Name", 0, 0, name),
                patcher.Property("Pos", 1, 0, (1.0, 2.0, 3.0)),
                patcher.Property("SightDistance", 3, 0, 100.0),
                patcher.Property("HearingDistance", 3, 0, 50.0),
            ])],
            render_data=b"",
        )
        fd, path = tempfile.mkstemp(suffix=".DAT")
        os.close(fd)
        try:
            world.save(path)
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.remove(path)

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

    def test_conversion_builds_explicit_missing_lomm_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = self._make_mm9_root(tmp)
            lomm_root = self._make_lomm_root(tmp)
            lomm_catalog = os.path.join(tmp, "catalogs", "catalog_lomm.json")

            service.convert_level_to_bytes(service.ConvertLevelRequest(
                mm9_root=mm9_root,
                lomm_root=lomm_root,
                level_to_convert="CHATEAUESCAPE",
                converted_level_name="NEWLEVEL",
                config_path=self._write_config(tmp),
                lomm_catalog_json=lomm_catalog,
            ))

            self.assertTrue(os.path.isfile(lomm_catalog))
            with open(lomm_catalog, "r", encoding="utf-8") as f:
                built = json.load(f)
            self.assertEqual(built["game"], "lomm")
            self.assertEqual(built["summary"]["total_levels"], 1)

    def test_explicit_dat_skin_prevents_catalog_skin_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path = os.path.join(tmp, "catalog_lomm.json")
            with open(catalog_path, "w", encoding="utf-8") as f:
                json.dump({
                    "model_variants": {
                        r"models\dragonred.abc": [{
                            "name": "DragonRed",
                            "skins": [r"skins\dragonred.dtx"],
                            "source_keys": [],
                        }],
                    },
                }, f)
            world = load_world_from_bytes(self._make_world_with_assets(
                name="DragonRed0",
                filename=r"models\DragonRed.abc",
                skin=r"skins\explicit.dtx",
            ))

            refs, preview_visuals = converter._implicit_catalog_skin_refs(
                world, catalog_path
            )

            self.assertEqual(refs, [])
            self.assertEqual(preview_visuals, {})

    def test_preserves_unsupported_lomm_actor_and_reports_it_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = self._make_mm9_root(tmp)
            lomm_root = self._make_lomm_root(tmp, {
                "WORLDS/HIDEOUT": self._make_actor_world("Orc"),
            })
            result = service.convert_level_to_bytes(service.ConvertLevelRequest(
                mm9_root=mm9_root,
                lomm_root=lomm_root,
                level_to_convert="HIDEOUT",
                converted_level_name="HIDEOUT_MM9",
                config_path=self._write_config(tmp),
            ))

            world = load_world_from_bytes(result.dat_bytes)
            self.assertEqual([obj.type_str for obj in world.objects], ["Orc"])
            self.assertEqual(result.stats.compatibility.actor_policy, "preserve")
            self.assertEqual(result.stats.compatibility.unresolved_actor_count, 1)
            record = result.stats.compatibility.records[0]
            self.assertEqual(record.status, "unsupported_actor_preserved")
            self.assertEqual(record.output_index, 0)

    def test_explicit_remove_policy_deletes_only_unsupported_actor(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = self._make_mm9_root(tmp)
            lomm_root = self._make_lomm_root(tmp, {
                "WORLDS/HIDEOUT": self._make_actor_world("Orc"),
            })
            result = service.convert_level_to_bytes(service.ConvertLevelRequest(
                mm9_root=mm9_root,
                lomm_root=lomm_root,
                level_to_convert="HIDEOUT",
                converted_level_name="HIDEOUT_MM9",
                config_path=self._write_config(tmp),
                actor_policy="remove",
            ))

            self.assertEqual(load_world_from_bytes(result.dat_bytes).objects, [])
            self.assertEqual(result.stats.removed_by_class, {"Orc": 1})
            self.assertEqual(
                result.stats.compatibility.records[0].status,
                "unsupported_actor_removed",
            )

    def test_hidden_but_runtime_loadable_mm9_actor_is_not_marked_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = self._make_mm9_root(tmp)
            lomm_root = self._make_lomm_root(tmp, {
                "WORLDS/HIDEOUT": self._make_actor_world("Dwarf"),
            })
            result = service.convert_level_to_bytes(service.ConvertLevelRequest(
                mm9_root=mm9_root,
                lomm_root=lomm_root,
                level_to_convert="HIDEOUT",
                converted_level_name="HIDEOUT_MM9",
                config_path=self._write_config(tmp),
            ))

            self.assertEqual(
                [obj.type_str for obj in load_world_from_bytes(result.dat_bytes).objects],
                ["Dwarf"],
            )
            self.assertEqual(result.stats.compatibility.unresolved_actor_count, 0)
            self.assertEqual(
                result.stats.compatibility.records[0].status,
                "same_name_mm9_implementation",
            )

    def test_legacy_actor_substitution_is_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = self._make_mm9_root(tmp)
            lomm_root = self._make_lomm_root(tmp, {
                "WORLDS/HIDEOUT": self._make_actor_world("Orc", "LoMMOrc"),
            })
            config_path = os.path.join(tmp, "legacy_rules.json")
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({
                    "remove_unknown_classes": True,
                    "patch_class": {},
                    "convert_class": {
                        "Orc": {
                            "template": "WORLDS/MM9LEVEL::MM9",
                            "new_type": "TestObject",
                            "preserve": ["Name", "Pos"],
                        },
                    },
                }, f)

            preserved = service.convert_level_to_bytes(service.ConvertLevelRequest(
                mm9_root=mm9_root,
                lomm_root=lomm_root,
                level_to_convert="HIDEOUT",
                converted_level_name="PRESERVED",
                config_path=config_path,
            ))
            legacy = service.convert_level_to_bytes(service.ConvertLevelRequest(
                mm9_root=mm9_root,
                lomm_root=lomm_root,
                level_to_convert="HIDEOUT",
                converted_level_name="LEGACY",
                config_path=config_path,
                actor_policy="legacy",
            ))

            self.assertEqual(load_world_from_bytes(preserved.dat_bytes).objects[0].type_str, "Orc")
            legacy_obj = load_world_from_bytes(legacy.dat_bytes).objects[0]
            self.assertEqual(legacy_obj.type_str, "TestObject")
            self.assertEqual(legacy_obj.get("Name"), "LoMMOrc")
            self.assertEqual(
                legacy.stats.compatibility.records[0].status,
                "legacy_actor_substitution",
            )

    def test_staging_does_not_modify_live_archive_and_blocks_unsupported_actor(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = self._make_mm9_root(tmp)
            lomm_root = self._make_lomm_root(tmp, {
                "WORLDS/HIDEOUT": self._make_actor_world("Orc"),
            })
            live_worlds = service.validate_mm9_root(mm9_root).worlds_rez
            with open(live_worlds, "rb") as f:
                before = f.read()

            result = service.convert_and_stage_level(
                service.ConvertLevelRequest(
                    mm9_root=mm9_root,
                    lomm_root=lomm_root,
                    level_to_convert="HIDEOUT",
                    converted_level_name="HIDEOUT_MM9",
                    config_path=self._write_config(tmp),
                ),
                staging_root=os.path.join(tmp, "output"),
            )

            with open(live_worlds, "rb") as f:
                self.assertEqual(f.read(), before)
            self.assertTrue(result.staged)
            self.assertTrue(os.path.isfile(result.worlds_rez))
            with open(result.manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(
                manifest["blocking_issues"][0]["code"],
                "unsupported_lomm_actors",
            )
            with self.assertRaisesRegex(install_manager.InstallError, "marked unsafe"):
                install_manager.install_batch(
                    result.stage_dir,
                    service.validate_mm9_root(mm9_root).data_dir,
                    os.path.join(tmp, "backups"),
                )

    def test_live_insertion_refuses_unresolved_actor_without_advanced_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = self._make_mm9_root(tmp)
            lomm_root = self._make_lomm_root(tmp, {
                "WORLDS/HIDEOUT": self._make_actor_world("Orc"),
            })
            request = service.ConvertLevelRequest(
                mm9_root=mm9_root,
                lomm_root=lomm_root,
                level_to_convert="HIDEOUT",
                converted_level_name="HIDEOUT_MM9",
                config_path=self._write_config(tmp),
            )

            with self.assertRaisesRegex(
                service.ConversionServiceError,
                "Refusing to modify the live game",
            ):
                service.convert_and_insert_level(request)

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

    def _make_world_with_assets(
        self,
        name: str,
        filename: str = "",
        skin: str = "",
        sound: str = "",
        class_name: str = "TestObject",
    ) -> bytes:
        from mm9_patcher import mm9_patch as patcher
        header = patcher.Header(66, 0, 0, (0,) * 8)
        props = [
            patcher.Property("Name", 0, 0, name),
            patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
        ]
        if filename:
            props.append(patcher.Property("Filename", 0, 0, filename))
        if skin:
            props.append(patcher.Property("Skin", 0, 0, skin))
        if sound:
            props.append(patcher.Property("AmbientSound", 0, 0, sound))
        obj = patcher.WorldObject(class_name, props)
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

    def test_copies_missing_assets_and_creates_manifest_and_log(self):
        def write_custom_rez(path: str, entries: dict, res_types: dict = None):
            if res_types is None:
                res_types = {}
            import time
            import struct
            now = int(time.time())
            payload_chunks = []
            entry_positions = {}
            cursor = rezmgr._HEADER_SIZE
            for vpath, data in entries.items():
                entry_positions[vpath] = cursor
                payload_chunks.append(data)
                cursor += len(data)
            tree = {}
            for vpath, data in entries.items():
                root, name = vpath.split("/", 1)
                tree.setdefault(root, []).append((name, data))
            dir_blocks = {}
            for root, files in tree.items():
                block = bytearray()
                for name, data in files:
                    full_path = f"{root}/{name}"
                    rt = res_types.get(full_path, 0)
                    block += struct.pack("<I", rezmgr.ENTRY_TYPE_RESOURCE)
                    block += struct.pack("<III", entry_positions[full_path], len(data), now)
                    block += struct.pack("<III", 0, rt, 0)
                    block += name.encode("latin-1") + b"\x00"
                    block += b"\x00"
                dir_blocks[root] = bytes(block)
            next_write = cursor
            dir_positions = {}
            dir_payload = bytearray()
            for root, block in dir_blocks.items():
                dir_positions[root] = next_write + len(dir_payload)
                dir_payload += block
            root_block = bytearray()
            for root, block in dir_blocks.items():
                root_block += struct.pack("<I", rezmgr.ENTRY_TYPE_DIRECTORY)
                root_block += struct.pack("<III", dir_positions[root], len(block), now)
                root_block += root.encode("latin-1") + b"\x00"
            root_pos = next_write + len(dir_payload)
            root_size = len(root_block)
            ft = b"RezMgr Version 1 Copyright (C) 1995 MONOLITH INC.".ljust(rezmgr.USER_TITLE_SIZE, b" ")
            ut = b"LithTech Resource File".ljust(rezmgr.USER_TITLE_SIZE, b" ")
            header = struct.pack(
                rezmgr._HEADER_FMT,
                0x0D, 0x0A,
                ft,
                0x0D, 0x0A,
                ut,
                0x0D, 0x0A, 0x1A,
                1,
                root_pos, root_size, now,
                next_write,
                now,
                0,
                max([len(k) + 1 for k in tree] + [1]),
                max([len(vpath.rsplit("/", 1)[-1]) + 1 for vpath in entries] + [1]),
                1,
                1,
            )
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(header)
                for chunk in payload_chunks:
                    f.write(chunk)
                f.write(dir_payload)
                f.write(root_block)

        with tempfile.TemporaryDirectory() as tmp:
            # 1. Setup MM9 with empty models, skins, and sounds rez files
            mm9_root = os.path.join(tmp, "mm9")
            mm9_data = os.path.join(mm9_root, "data")
            write_minimal_rez(os.path.join(mm9_data, "WORLDS.REZ"), {"WORLDS/MM9LEVEL": make_world_bytes("MM9")})
            write_minimal_rez(os.path.join(mm9_data, "RUDE.REZ"), {"RUDE/NPCNAME": b""})
            write_minimal_rez(os.path.join(mm9_data, "SCRIPTS.REZ"), {"SCRIPTS/EMPTY": b""})
            write_minimal_rez(os.path.join(mm9_data, "MODELS.REZ"), {"MODELS/EXISTING.ABC": b"exist_model"})
            write_minimal_rez(os.path.join(mm9_data, "SKINS.REZ"), {"SKINS/EXISTING.DTX": b"exist_skin"})
            write_minimal_rez(os.path.join(mm9_data, "SOUNDS.REZ"), {"SOUNDS/EXISTING": b"exist_sound"})
            
            # 2. Setup LoMM with level referencing missing assets, and the assets present
            lomm_root = os.path.join(tmp, "lomm")
            lomm_data = os.path.join(lomm_root, "Data")
            lomm_world_bytes = self._make_world_with_assets(
                name="LoMM",
                filename="models/weapons/sword.abc",
                skin="skins/weapons/sword.dtx",
                sound="sounds/ambient/creek.wav"
            )
            write_minimal_rez(os.path.join(lomm_data, "worlds.rez"), {"WORLDS/CHATEAUESCAPE": lomm_world_bytes})
            write_minimal_rez(os.path.join(lomm_data, "RUDE.REZ"), {"RUDE/NPCNAME": b""})
            write_minimal_rez(os.path.join(lomm_data, "SCRIPTS.REZ"), {"SCRIPTS/EMPTY": b""})
            
            # Add missing model in lomm models.rez
            write_custom_rez(
                os.path.join(lomm_data, "MODELS.REZ"),
                {"MODELS/WEAPONS/SWORD.ABC": b"LOMM_SWORD_MODEL"},
                {"MODELS/WEAPONS/SWORD.ABC": 12345}
            )
            # Add missing skin in lomm skins.rez
            write_custom_rez(
                os.path.join(lomm_data, "SKINS.REZ"),
                {"SKINS/WEAPONS/SWORD.DTX": b"LOMM_SWORD_SKIN"},
                {"SKINS/WEAPONS/SWORD.DTX": 67890}
            )
            # Add missing sound in loose directory (to test loose files search)
            sound_loose_path = os.path.join(lomm_data, "SOUNDS", "ambient", "creek.wav")
            os.makedirs(os.path.dirname(sound_loose_path), exist_ok=True)
            with open(sound_loose_path, "wb") as f:
                f.write(b"LOMM_CREEK_SOUND")

            config_path = self._write_config(tmp)
            backup_root = os.path.join(tmp, "backups")

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

            # Check that the files were copied and integrated successfully
            # Verify MODELS.REZ
            with rezmgr.RezReader(os.path.join(mm9_data, "MODELS.REZ")) as r:
                self.assertIn("MODELS/WEAPONS/SWORD.ABC", r.list_paths())
                self.assertEqual(r.extract_to_bytes("MODELS/WEAPONS/SWORD.ABC"), b"LOMM_SWORD_MODEL")
                self.assertEqual(r.find("MODELS/WEAPONS/SWORD.ABC").res_type, 12345)

            # Verify SKINS.REZ
            with rezmgr.RezReader(os.path.join(mm9_data, "SKINS.REZ")) as r:
                self.assertIn("SKINS/WEAPONS/SWORD.DTX", r.list_paths())
                self.assertEqual(r.extract_to_bytes("SKINS/WEAPONS/SWORD.DTX"), b"LOMM_SWORD_SKIN")
                self.assertEqual(r.find("SKINS/WEAPONS/SWORD.DTX").res_type, 67890)

            # Verify SOUNDS.REZ (should be upper-cased without extension)
            with rezmgr.RezReader(os.path.join(mm9_data, "SOUNDS.REZ")) as r:
                self.assertIn("SOUNDS/AMBIENT/CREEK", r.list_paths())
                self.assertEqual(r.extract_to_bytes("SOUNDS/AMBIENT/CREEK"), b"LOMM_CREEK_SOUND")
                # restype for wav should be mapped by filename
                from core.rezmgr import _restype_for_filename
                expected_wav_type = _restype_for_filename("creek.wav")
                self.assertEqual(r.find("SOUNDS/AMBIENT/CREEK").res_type, expected_wav_type)

            # Verify manifest
            self.assertTrue(os.path.isfile(result.manifest_path))
            with open(result.manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            
            archives_in_manifest = [a["name"] for a in manifest["archives"]]
            self.assertIn("WORLDS.REZ", archives_in_manifest)
            self.assertIn("MODELS.REZ", archives_in_manifest)
            self.assertIn("SKINS.REZ", archives_in_manifest)
            self.assertIn("SOUNDS.REZ", archives_in_manifest)

            # Verify conversion log
            log_path = os.path.join(os.path.dirname(result.backup_dir), "conversion_log.txt")
            self.assertTrue(os.path.isfile(log_path))
            with open(log_path, "r", encoding="utf-8") as f:
                log_content = f.read()
            self.assertIn("Copied MODELS (1)", log_content)
            self.assertIn("MODELS/WEAPONS/SWORD.ABC", log_content)
            self.assertIn("Copied SKINS (1)", log_content)
            self.assertIn("SKINS/WEAPONS/SWORD.DTX", log_content)
            self.assertIn("Copied SOUNDS (1)", log_content)
            self.assertIn("SOUNDS/AMBIENT/CREEK", log_content)

            # Verify restore list includes all backups
            restores = install_manager.backups_to_restore(os.path.dirname(result.backup_dir))
            restore_names = [os.path.basename(r) for r in restores]
            self.assertIn("WORLDS.REZ", restore_names)
            self.assertIn("MODELS.REZ", restore_names)
            self.assertIn("SKINS.REZ", restore_names)
            self.assertIn("SOUNDS.REZ", restore_names)

    def test_stages_implicit_skin_from_lomm_catalog_model_variant(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = self._make_mm9_root(tmp)
            mm9_data = os.path.join(mm9_root, "data")
            write_minimal_rez(
                os.path.join(mm9_data, "MODELS.REZ"),
                {"MODELS/EXISTING.ABC": b"mm9-model"},
            )
            write_minimal_rez(
                os.path.join(mm9_data, "SKINS.REZ"),
                {"SKINS/EXISTING.DTX": b"mm9-skin"},
            )

            lomm_world = self._make_world_with_assets(
                name="DragonRed0",
                filename=r"models\DragonRed.abc",
            )
            lomm_root = self._make_lomm_root(
                tmp,
                {"WORLDS/ISLEOFFIRE": lomm_world},
            )
            lomm_data = os.path.join(lomm_root, "Data")
            write_minimal_rez(
                os.path.join(lomm_data, "MODELS.REZ"),
                {"MODELS/DRAGONRED.ABC": b"dragon-model"},
            )
            write_minimal_rez(
                os.path.join(lomm_data, "SKINS.REZ"),
                {"SKINS/DRAGONRED.DTX": b"dragon-skin"},
            )
            lomm_catalog = os.path.join(tmp, "catalog_lomm.json")
            with open(lomm_catalog, "w", encoding="utf-8") as f:
                json.dump({
                    "game": "lomm",
                    "classes": {},
                    "summary": {"total_levels": 1},
                    "model_variants": {
                        r"models\dragonred.abc": [{
                            "name": "DragonRed",
                            "skins": [r"skins\dragonred.dtx"],
                            "source_keys": [r"asset-name:skins\dragonred.dtx"],
                        }],
                    },
                }, f)

            result = service.convert_and_stage_level(
                service.ConvertLevelRequest(
                    mm9_root=mm9_root,
                    lomm_root=lomm_root,
                    level_to_convert="ISLEOFFIRE",
                    converted_level_name="ISLEOFFIRE_MM9",
                    config_path=self._write_config(tmp),
                    lomm_catalog_json=lomm_catalog,
                ),
                staging_root=os.path.join(tmp, "staging"),
            )

            self.assertEqual(
                result.conversion.stats.audit_skins.in_lomm_only,
                [r"skins\dragonred.dtx"],
            )
            implicit = result.conversion.stats.implicit_skin_refs
            self.assertEqual(len(implicit), 1)
            self.assertEqual(implicit[0].model, r"models\dragonred.abc")
            self.assertEqual(implicit[0].skin, r"skins\dragonred.dtx")
            staged_skins = os.path.join(result.stage_dir, "data", "SKINS.REZ")
            with rezmgr.RezReader(staged_skins) as reader:
                self.assertEqual(
                    reader.extract_to_bytes("SKINS/DRAGONRED.DTX"),
                    b"dragon-skin",
                )
            with open(result.manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest_implicit = manifest["conversion"]["stats"]["implicit_skin_refs"]
            self.assertEqual(manifest_implicit[0]["variant_name"], "DragonRed")
            with open(
                os.path.join(result.stage_dir, "conversion_log.txt"),
                "r",
                encoding="utf-8",
            ) as f:
                log_content = f.read()
            self.assertIn("Implicit catalog skin resolutions", log_content)
            self.assertIn(r"models\dragonred.abc -> skins\dragonred.dtx", log_content)

    def test_stages_princess_class_visual_without_changing_dat_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9_root = self._make_mm9_root(tmp)
            mm9_data = os.path.join(mm9_root, "data")
            write_minimal_rez(
                os.path.join(mm9_data, "MODELS.REZ"),
                {"MODELS/PLAYER/KING.ABC": b"mm9-king"},
            )
            write_minimal_rez(
                os.path.join(mm9_data, "SKINS.REZ"),
                {"SKINS/EXISTING.DTX": b"mm9-skin"},
            )

            lomm_world = self._make_world_with_assets(
                name="Princess0",
                class_name="Princess",
                filename=r"models\player\king.abc",
            )
            lomm_root = self._make_lomm_root(
                tmp,
                {"WORLDS/_RESCUEATTHERUINS": lomm_world},
            )
            lomm_data = os.path.join(lomm_root, "Data")
            write_minimal_rez(
                os.path.join(lomm_data, "MODELS.REZ"),
                {"MODELS/PRINCESS.ABC": b"princess-model"},
            )
            write_minimal_rez(
                os.path.join(lomm_data, "SKINS.REZ"),
                {
                    "SKINS/PRINCESSBLUE.DTX": b"princess-blue",
                    "SKINS/PRINCESSGOLD.DTX": b"princess-gold",
                },
            )
            lomm_catalog = os.path.join(tmp, "catalog_lomm.json")
            with open(lomm_catalog, "w", encoding="utf-8") as f:
                json.dump({
                    "game": "lomm",
                    "classes": {
                        "Princess": {
                            "object_lto": {
                                "hierarchy": ["BaseClass", "Actor", "Princess"],
                            },
                        },
                    },
                    "object_lto": {
                        "classes": {
                            "Princess": {
                                "hierarchy": ["BaseClass", "Actor", "Princess"],
                                "runtime_loadable": True,
                            },
                        },
                    },
                    "summary": {"total_levels": 1},
                    "model_resources": [r"models\princess.abc"],
                    "model_variants": {
                        r"models\princess.abc": [
                            {
                                "name": "Princess Blue",
                                "skins": [r"skins\princessblue.dtx"],
                                "source_keys": [
                                    r"asset-name:skins\princessblue.dtx"
                                ],
                            },
                            {
                                "name": "Princess Gold",
                                "skins": [r"skins\princessgold.dtx"],
                                "source_keys": [
                                    r"asset-name:skins\princessgold.dtx"
                                ],
                            },
                        ],
                    },
                }, f)

            result = service.convert_and_stage_level(
                service.ConvertLevelRequest(
                    mm9_root=mm9_root,
                    lomm_root=lomm_root,
                    level_to_convert="_RESCUEATTHERUINS",
                    converted_level_name="_RESCUEATTHERUINS_MM9",
                    config_path=self._write_config(tmp),
                    lomm_catalog_json=lomm_catalog,
                ),
                staging_root=os.path.join(tmp, "staging"),
            )

            stats = result.conversion.stats
            self.assertEqual(
                stats.audit_models.in_lomm_only,
                [r"models\princess.abc"],
            )
            self.assertEqual(
                stats.audit_skins.in_lomm_only,
                [r"skins\princessblue.dtx"],
            )
            self.assertEqual(
                stats.preview_actor_visuals["princess"]["model"],
                r"models\princess.abc",
            )
            self.assertEqual(
                stats.preview_actor_visuals["princess"]["skins"],
                [r"skins\princessblue.dtx"],
            )
            self.assertEqual(stats.compatibility.unresolved_actor_count, 1)

            converted_world = load_world_from_bytes(result.conversion.dat_bytes)
            self.assertEqual(
                converted_world.objects[0].get("Filename"),
                r"models\player\king.abc",
            )
            with rezmgr.RezReader(
                os.path.join(result.stage_dir, "data", "MODELS.REZ")
            ) as reader:
                self.assertEqual(
                    reader.extract_to_bytes("MODELS/PRINCESS.ABC"),
                    b"princess-model",
                )
            with rezmgr.RezReader(
                os.path.join(result.stage_dir, "data", "SKINS.REZ")
            ) as reader:
                self.assertEqual(
                    reader.extract_to_bytes("SKINS/PRINCESSBLUE.DTX"),
                    b"princess-blue",
                )
            with open(result.manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertTrue(
                manifest["conversion"]["stats"]["implicit_skin_refs"][0]
                ["preview_model_override"]
            )
            self.assertEqual(
                manifest["conversion"]["stats"]["preview_actor_visuals"]
                ["princess"]["model"],
                r"models\princess.abc",
            )


if __name__ == "__main__":
    unittest.main()
