import os
import struct
import sys
import tempfile
import time
import unittest


from tests._path import ROOT  # noqa: F401

from core import game_resources
from core import rezmgr as mm9_rezmgr


def write_file(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def write_minimal_rez(path: str, entries):
    """Write a tiny REZ with entries like {"RUDE/NPC1": b"..."}."""
    now = int(time.time())
    payload_chunks = []
    entry_positions = {}
    cursor = mm9_rezmgr._HEADER_SIZE
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
            block += struct.pack("<I", mm9_rezmgr.ENTRY_TYPE_RESOURCE)
            block += struct.pack("<III", entry_positions[f"{root}/{name}"], len(data), now)
            block += struct.pack("<III", 0, 0, 0)
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
        root_block += struct.pack("<I", mm9_rezmgr.ENTRY_TYPE_DIRECTORY)
        root_block += struct.pack("<III", dir_positions[root], len(block), now)
        root_block += root.encode("latin-1") + b"\x00"
    root_pos = next_write + len(dir_payload)
    root_size = len(root_block)

    ft = b"RezMgr Version 1 Copyright (C) 1995 MONOLITH INC.".ljust(
        mm9_rezmgr.USER_TITLE_SIZE, b" ")
    ut = b"LithTech Resource File".ljust(mm9_rezmgr.USER_TITLE_SIZE, b" ")
    header = struct.pack(
        mm9_rezmgr._HEADER_FMT,
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


class GameResourcesTests(unittest.TestCase):
    def test_reads_rez_resources_with_game_virtual_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            rez_path = os.path.join(tmp, "game", "data", "RUDE.REZ")
            write_minimal_rez(rez_path, {"RUDE/NPC1": b"rez"})

            res = game_resources.GameResources(
                archives={"rude": rez_path},
            )

            self.assertEqual(res.read_bytes("RUDE/NPC1"), b"rez")
            self.assertEqual(res.read_text("RUDE/NPC1"), "rez")
            self.assertEqual(res.archive_for("RUDE/NPC1"), rez_path)
            self.assertEqual(res.locate("RUDE/NPC1").source, "rez")
            self.assertIn("RUDE/NPC1", res.list("RUDE/"))

    def test_missing_loose_files_are_not_used_as_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "legacy_extracted")
            write_file(os.path.join(data_dir, "RUDE", "RUDE", "NPC1.RUDE"), b"loose")

            res = game_resources.GameResources()

            self.assertIsNone(res.locate("RUDE/NPC1"))
            with self.assertRaises(FileNotFoundError):
                res.read_bytes("RUDE/NPC1")

    def test_rez_archive_wins_without_loose_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            rez_path = os.path.join(tmp, "game", "data", "RUDE.REZ")
            write_minimal_rez(rez_path, {"RUDE/NPC1": b"rez"})

            res = game_resources.GameResources(
                archives={"rude": rez_path},
            )

            self.assertEqual(res.read_bytes("RUDE/NPC1"), b"rez")
            self.assertEqual(res.archive_for("RUDE/NPC1"), rez_path)
            self.assertEqual(res.locate("RUDE/NPC1").source, "rez")

    def test_resolves_sound_dependencies_with_or_without_wav_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            rez_path = os.path.join(tmp, "game", "data", "SOUNDS.REZ")
            write_minimal_rez(rez_path, {"SOUNDS/DOORS/OPEN": b"wav"})
            res = game_resources.GameResources(archives={"sounds": rez_path})

            self.assertTrue(res.exists("SOUNDS/DOORS/OPEN"))
            self.assertTrue(res.exists("sounds\\doors\\open.wav"))
            self.assertEqual(res.archive_for("SOUNDS/DOORS/OPEN.WAV"), rez_path)

    def test_cache_archive_tree_materializes_root_relative_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            rez_path = os.path.join(tmp, "game", "data", "TEXTURES.REZ")
            cache_dir = os.path.join(tmp, "cache")
            write_minimal_rez(rez_path, {
                "TEXTURES/A/B/STONE.DTX": b"stone",
                "TEXTURES/CHROME": b"chrome",
            })
            res = game_resources.GameResources(
                archives={"textures": rez_path},
                cache_dir=cache_dir,
            )

            root = res.cache_archive_tree("textures", "TEXTURES", (".DTX",))

            self.assertIsNotNone(root)
            with open(os.path.join(root, "A", "B", "STONE.DTX"), "rb") as f:
                self.assertEqual(f.read(), b"stone")
            with open(os.path.join(root, "CHROME.DTX"), "rb") as f:
                self.assertEqual(f.read(), b"chrome")
            self.assertTrue(os.path.isfile(os.path.join(root, ".complete")))


if __name__ == "__main__":
    unittest.main()
