import os
import struct
import sys
import tempfile
import time
import unittest


from tests._path import ROOT  # noqa: F401

from core import rezmgr as mm9_rezmgr
from core import game_resources


def write_duplicate_worlds_rez(path: str, dat_payload: bytes, ed_payload: bytes) -> None:
    now = int(time.time())
    entries = [
        ("BOOTCAMP", mm9_rezmgr._restype_for_filename("x.DAT"), dat_payload),
        ("BOOTCAMP", mm9_rezmgr._restype_for_filename("x.ED"), ed_payload),
    ]

    cursor = mm9_rezmgr._HEADER_SIZE
    payload_positions = []
    for _name, _restype, data in entries:
        payload_positions.append(cursor)
        cursor += len(data)

    worlds_block = bytearray()
    for (name, restype, data), pos in zip(entries, payload_positions):
        worlds_block += struct.pack("<I", mm9_rezmgr.ENTRY_TYPE_RESOURCE)
        worlds_block += struct.pack("<III", pos, len(data), now)
        worlds_block += struct.pack("<III", 0, restype, 0)
        worlds_block += name.encode("latin-1") + b"\x00"
        worlds_block += b"\x00"

    next_write = cursor
    worlds_pos = next_write
    root_pos = worlds_pos + len(worlds_block)
    root_block = bytearray()
    root_block += struct.pack("<I", mm9_rezmgr.ENTRY_TYPE_DIRECTORY)
    root_block += struct.pack("<III", worlds_pos, len(worlds_block), now)
    root_block += b"WORLDS\x00"

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
        root_pos, len(root_block), now,
        next_write,
        now,
        0,
        len("WORLDS") + 1,
        len("BOOTCAMP") + 1,
        1,
        1,
    )

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(header)
        for _name, _restype, data in entries:
            f.write(data)
        f.write(worlds_block)
        f.write(root_block)


class RezDuplicateResourceTests(unittest.TestCase):
    def test_reader_exposes_same_name_different_type_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            rez_path = os.path.join(tmp, "WORLDS.REZ")
            dat = struct.pack("<I", 66) + b"dat"
            ed = struct.pack("<I", 1249) + b"ed"
            write_duplicate_worlds_rez(rez_path, dat, ed)

            with mm9_rezmgr.RezReader(rez_path) as reader:
                paths = reader.list_paths()
                self.assertIn("WORLDS/BOOTCAMP.DAT", paths)
                self.assertIn("WORLDS/BOOTCAMP.ED", paths)
                self.assertEqual(reader.find("WORLDS/BOOTCAMP.DAT").type_str, "DAT")
                self.assertEqual(reader.find("WORLDS/BOOTCAMP.ED").type_str, "ED")
                self.assertEqual(reader.find("WORLDS/BOOTCAMP").type_str, "DAT")
                self.assertEqual(reader.extract_to_bytes("WORLDS/BOOTCAMP"), dat)

    def test_writer_replaces_only_selected_typed_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "WORLDS.REZ")
            output_rez = os.path.join(tmp, "out", "WORLDS.REZ")
            dat = struct.pack("<I", 66) + b"dat"
            dat2 = struct.pack("<I", 66) + b"changed"
            ed = struct.pack("<I", 1249) + b"ed"
            write_duplicate_worlds_rez(source_rez, dat, ed)

            with mm9_rezmgr.RezWriter(source_rez, output_rez) as writer:
                writer.replace("WORLDS/BOOTCAMP.DAT", dat2)
                writer.commit()

            with mm9_rezmgr.RezReader(output_rez) as reader:
                self.assertIn("WORLDS/BOOTCAMP.DAT", reader.list_paths())
                self.assertIn("WORLDS/BOOTCAMP.ED", reader.list_paths())
                self.assertEqual(reader.extract_to_bytes("WORLDS/BOOTCAMP.DAT"), dat2)
                self.assertEqual(reader.extract_to_bytes("WORLDS/BOOTCAMP.ED"), ed)
                self.assertEqual(reader.extract_to_bytes("WORLDS/BOOTCAMP"), dat2)

    def test_game_resources_preserves_typed_duplicate_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            dat = struct.pack("<I", 66) + b"dat"
            ed = struct.pack("<I", 1249) + b"ed"
            write_duplicate_worlds_rez(source_rez, dat, ed)

            resources = game_resources.GameResources(
                archives={"worlds": source_rez},
            )

            self.assertEqual(resources.read_bytes("WORLDS/BOOTCAMP"), dat)
            self.assertEqual(resources.read_bytes("WORLDS/BOOTCAMP.DAT"), dat)
            self.assertEqual(resources.read_bytes("WORLDS/BOOTCAMP.ED"), ed)


if __name__ == "__main__":
    unittest.main()
