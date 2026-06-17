#!/usr/bin/env python3
"""Create a reversible MM9 output batch for testing the LoMM Orc.

The patch keeps stock MM9 object classes intact.  It ports the LoMM Orc as a
`LizardOrc` placement variant by adding the LoMM model/skin assets and
appending matching actor-table rows.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import tempfile
from datetime import datetime
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _path_setup  # noqa: F401
import mm9_patch as patcher
from core.rezmgr import RezReader, RezWriter, _restype_for_filename


DEFAULT_MM9_ROOT = r"C:\Program Files (x86)\GOG Galaxy\Games\Might and Magic 9"
DEFAULT_LOMM_ROOT = r"C:\games\Legends of Might and Magic"
ORC_ROW_NAME = "LoMM Orc"
ORC_ROW_NUMBER = 304
ORC_MAGE_SLOT_ROW_NUMBER = 191
ORC_MODEL_NAME = "OrcMM9.abc"
ORC_MODEL_VPATH = "MODELS/ORCMM9"
ORC_SKIN_NAME = "Orc.dtx"
ORC_SKIN_VPATH = "SKINS/ORC"
TABLE_MODE_APPEND = "append"
TABLE_MODE_APPEND_MAGE = "append-mage"
TABLE_MODE_MAGE_SLOT = "mage-slot"

_ABC_ANIMATION_REPLACEMENTS: Tuple[Tuple[bytes, bytes], ...] = (
    (b"Stand", b"stand"),
    (b"Walk", b"walk"),
    (b"Run", b"run"),
    (b"WAttack1", b"Hattack1"),
    (b"WAttack2", b"Hattack2"),
    (b"WAttack3", b"Hattack3"),
    (b"Rangeattack", b"RangeAttack"),
)


def _data_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), "data")


def _read_rez_text(rez_path: str, vpath: str) -> str:
    with RezReader(rez_path) as reader:
        return reader.extract_to_bytes(vpath).decode("latin-1")


def _line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _header_and_source_cells(
    text: str,
    source_row_number: int,
) -> Tuple[List[str], List[str]]:
    lines = text.splitlines()
    if not lines:
        raise ValueError("empty actor table")
    header = lines[0].split("\t")
    source_prefix = f"{source_row_number}\t"
    for line in lines[1:]:
        if line.startswith(source_prefix):
            cells = line.split("\t")
            if len(cells) < len(header):
                cells.extend([""] * (len(header) - len(cells)))
            if len(cells) > len(header):
                cells = cells[:len(header)]
            return header, cells
    raise ValueError(f"could not find source actor-table row {source_row_number}")


def _make_lomm_orc_cells(
    header: List[str],
    source_cells: List[str],
    table_mode: str,
) -> List[str]:
    cells = list(source_cells)
    if len(cells) < len(header):
        cells.extend([""] * (len(header) - len(cells)))
    if table_mode == TABLE_MODE_MAGE_SLOT:
        updates = {
            "ModelName": ORC_MODEL_NAME,
            "SkinName": ORC_SKIN_NAME,
            "SkinName2": "",
            "SkinName3": "",
            "ScriptName": "baserange.scr",
            "FootSound": "orcstep",
            "FootRadius": "500",
            "IsMonster": "1",
        }
    else:
        base_name = (
            "LizardOrcMage"
            if table_mode == TABLE_MODE_APPEND_MAGE
            else "LizardOrc"
        )
        updates = {
            "Number": str(ORC_ROW_NUMBER),
            "Monster Name": ORC_ROW_NAME,
            "ModelName": ORC_MODEL_NAME,
            "SkinName": ORC_SKIN_NAME,
            "SkinName2": "",
            "SkinName3": "",
            "Type/Picture": ORC_ROW_NAME,
            "LVL": "4",
            "HP": "80",
            "EXP": "64",
            "WalkVelocity": "125",
            "RunVelocity": "265",
            "LungeVelocity": "215",
            "AlertRadius": "1200",
            "Accuracy": "2",
            "ScriptName": "baserange.scr",
            "FootSound": "orcstep",
            "FootRadius": "500",
            "BaseName": base_name,
            "IsMonster": "1",
        }
    header_index = {key: index for index, key in enumerate(header)}
    for key, value in updates.items():
        index = header_index.get(key)
        if index is not None:
            cells[index] = value
    return cells[:len(header)]


def _line_is_lomm_orc_row(line: str) -> bool:
    stripped = line.rstrip("\r\n")
    if not stripped.strip():
        return False
    cells = stripped.split("\t")
    if cells and cells[0].strip() == str(ORC_ROW_NUMBER):
        return True
    return len(cells) > 1 and cells[1].strip().lower() == ORC_ROW_NAME.lower()


def _upsert_orc_row(
    text: str,
    source_row_number: int = 189,
    table_mode: str = TABLE_MODE_APPEND,
) -> Tuple[str, str]:
    header, source_cells = _header_and_source_cells(text, source_row_number)
    cells = _make_lomm_orc_cells(header, source_cells, table_mode)
    newline = _line_ending(text)
    row_text = "\t".join(cells)

    if table_mode == TABLE_MODE_MAGE_SLOT:
        lines = text.splitlines(keepends=True)
        source_prefix = f"{source_row_number}\t"
        for index, line in enumerate(lines):
            if line.startswith(source_prefix):
                suffix = "\r\n" if line.endswith("\r\n") else (
                    "\n" if line.endswith("\n") else newline
                )
                lines[index] = row_text + suffix
                return "".join(lines), "replaced"
        raise ValueError(f"could not replace actor-table row {source_row_number}")

    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if _line_is_lomm_orc_row(line):
            suffix = "\r\n" if line.endswith("\r\n") else (
                "\n" if line.endswith("\n") else newline
            )
            lines[index] = row_text + suffix
            return "".join(lines), "updated"

    if text.endswith(newline):
        return text + row_text + newline, "added"
    if text.endswith("\n"):
        return text + row_text + "\n", "added"
    return text + newline + row_text + newline, "added"


def _patched_orc_model(data: bytes) -> Tuple[bytes, Dict[str, int]]:
    patched = data
    counts: Dict[str, int] = {}
    for old, new in _ABC_ANIMATION_REPLACEMENTS:
        if len(old) != len(new):
            raise ValueError(f"ABC patch replacement changes length: {old!r}")
        count = patched.count(old)
        if count:
            patched = patched.replace(old, new)
        counts[old.decode("ascii")] = count
    return patched, counts


def _replace_or_add(
    writer: RezWriter,
    reader: RezReader,
    virtual_path: str,
    payload: bytes,
    restype: int,
) -> str:
    if reader.find(virtual_path) is None:
        writer.add(virtual_path, payload, restype=restype)
        return "added"
    writer.replace(virtual_path, payload, restype=restype)
    return "replaced"


def _patch_rez(
    source_rez: str,
    output_rez: str,
    updates: List[Tuple[str, bytes, int]],
) -> List[str]:
    log: List[str] = []
    with RezReader(source_rez) as reader:
        with RezWriter(source_rez, output_rez) as writer:
            for vpath, payload, restype in updates:
                action = _replace_or_add(writer, reader, vpath, payload, restype)
                log.append(f"{action} {vpath}")
            result = writer.commit()
    log.extend(result.get("log", []))
    return log


def _read_file(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _world_from_bytes(data: bytes) -> patcher.World:
    fd, path = tempfile.mkstemp(prefix="lomm_orc_world_", suffix=".DAT")
    os.close(fd)
    try:
        with open(path, "wb") as fh:
            fh.write(data)
        return patcher.World.load(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _world_to_bytes(world: patcher.World) -> bytes:
    fd, path = tempfile.mkstemp(prefix="lomm_orc_world_out_", suffix=".DAT")
    os.close(fd)
    try:
        world.save(path)
        return _read_file(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _runtime_class_for_table_mode(table_mode: str) -> str:
    return (
        "LizardOrcMage"
        if table_mode in (TABLE_MODE_APPEND_MAGE, TABLE_MODE_MAGE_SLOT)
        else "LizardOrc"
    )


def _patch_lomm_orc_world_objects(
    worlds_rez: str,
    table_mode: str,
) -> List[Tuple[str, bytes, int]]:
    updates: List[Tuple[str, bytes, int]] = []
    runtime_class = _runtime_class_for_table_mode(table_mode)
    with RezReader(worlds_rez) as reader:
        for vpath in reader.list_paths():
            if not vpath.upper().startswith("WORLDS/"):
                continue
            ent = reader.find(vpath)
            if ent is None or ent.size < 4:
                continue
            data = reader.extract_to_bytes(vpath)
            if struct.unpack_from("<I", data, 0)[0] != patcher.DAT_VERSION:
                continue
            try:
                world = _world_from_bytes(data)
            except Exception:
                continue
            changed = False
            for obj in world.objects:
                if not str(obj.get("Name") or "").startswith("LoMMOrc"):
                    continue
                if obj.type_str != runtime_class:
                    obj.type_str = runtime_class
                    changed = True
                if str(obj.get("Filename") or "") != "models\\OrcMM9.abc":
                    obj.set("Filename", "models\\OrcMM9.abc")
                    changed = True
            if changed:
                updates.append((
                    vpath,
                    _world_to_bytes(world),
                    _restype_for_filename("x.DAT"),
                ))
    return updates


def build_lomm_orc_patch(
    mm9_root: str,
    lomm_root: str,
    output_dir: str,
    table_mode: str = TABLE_MODE_APPEND,
) -> Dict[str, object]:
    mm9_data = _data_dir(mm9_root)
    lomm_data = _data_dir(lomm_root)
    data_rez = os.path.join(mm9_data, "DATA.REZ")
    worlds_rez = os.path.join(mm9_data, "WORLDS.REZ")
    models_rez = os.path.join(mm9_data, "MODELS.REZ")
    skins_rez = os.path.join(mm9_data, "SKINS.REZ")
    for path in (data_rez, models_rez, skins_rez):
        if not os.path.isfile(path):
            raise FileNotFoundError(path)

    orc_model = os.path.join(lomm_data, "MODELS", "ORC.ABC")
    orc_skin = os.path.join(lomm_data, "SKINS", "ORC.DTX")
    for path in (orc_model, orc_skin):
        if not os.path.isfile(path):
            raise FileNotFoundError(path)

    output_dir = os.path.abspath(output_dir)
    output_data = os.path.join(output_dir, "data")
    os.makedirs(output_data, exist_ok=True)

    source_row_number = (
        ORC_MAGE_SLOT_ROW_NUMBER
        if table_mode in (TABLE_MODE_APPEND_MAGE, TABLE_MODE_MAGE_SLOT)
        else 189
    )
    actor_text, actor_row_action = _upsert_orc_row(
        _read_rez_text(data_rez, "DATA/ACTOR"),
        source_row_number=source_row_number,
        table_mode=table_mode,
    )
    monsters_text, monsters_row_action = _upsert_orc_row(
        _read_rez_text(data_rez, "DATA/MONSTERS"),
        source_row_number=source_row_number,
        table_mode=table_mode,
    )
    patched_model, animation_replacements = _patched_orc_model(_read_file(orc_model))

    archives = []
    log: List[str] = []

    world_updates = (
        _patch_lomm_orc_world_objects(worlds_rez, table_mode)
        if os.path.isfile(worlds_rez)
        else []
    )
    if world_updates:
        worlds_output = os.path.join(output_data, "WORLDS.REZ")
        log.extend(_patch_rez(worlds_rez, worlds_output, world_updates))
        archives.append({
            "source_archive": worlds_rez,
            "output_archive": worlds_output,
            "entries": [item[0] for item in world_updates],
        })

    data_output = os.path.join(output_data, "DATA.REZ")
    log.extend(_patch_rez(data_rez, data_output, [
        ("DATA/ACTOR", actor_text.encode("latin-1"), _restype_for_filename("ACTOR.TXT")),
        (
            "DATA/MONSTERS",
            monsters_text.encode("latin-1"),
            _restype_for_filename("MONSTERS.TXT"),
        ),
    ]))
    archives.append({
        "source_archive": data_rez,
        "output_archive": data_output,
        "entries": ["DATA/ACTOR", "DATA/MONSTERS"],
    })

    models_output = os.path.join(output_data, "MODELS.REZ")
    log.extend(_patch_rez(models_rez, models_output, [
        (ORC_MODEL_VPATH, patched_model, _restype_for_filename(orc_model)),
    ]))
    archives.append({
        "source_archive": models_rez,
        "output_archive": models_output,
        "entries": [ORC_MODEL_VPATH],
    })

    skins_output = os.path.join(output_data, "SKINS.REZ")
    log.extend(_patch_rez(skins_rez, skins_output, [
        (ORC_SKIN_VPATH, _read_file(orc_skin), _restype_for_filename(orc_skin)),
    ]))
    archives.append({
        "source_archive": skins_rez,
        "output_archive": skins_output,
        "entries": [ORC_SKIN_VPATH],
    })

    manifest = {
        "version": 1,
        "created_at": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "kind": "lomm_orc_patch",
        "table_mode": table_mode,
        "game_data_dir": mm9_data,
        "mm9_root": os.path.abspath(mm9_root),
        "lomm_root": os.path.abspath(lomm_root),
        "archives": archives,
        "lomm_orc": {
            "row_number": (
                ORC_MAGE_SLOT_ROW_NUMBER
                if table_mode == TABLE_MODE_MAGE_SLOT
                else ORC_ROW_NUMBER
            ),
            "actor_row_action": actor_row_action,
            "monsters_row_action": monsters_row_action,
            "runtime_class": _runtime_class_for_table_mode(table_mode),
            "preset_name": "LoMM Orc",
            "model": ORC_MODEL_VPATH,
            "source_model": "MODELS/ORC",
            "skin": ORC_SKIN_VPATH,
            "animation_replacements": animation_replacements,
            "world_entries_updated": [item[0] for item in world_updates],
        },
    }
    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    log_path = os.path.join(output_dir, "lomm_orc_patch_log.txt")
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")

    return {
        "output_dir": output_dir,
        "manifest": manifest_path,
        "log": log_path,
        "archives": archives,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a reversible MM9 output batch for the LoMM Orc."
    )
    parser.add_argument("--mm9-root", default=DEFAULT_MM9_ROOT)
    parser.add_argument("--lomm-root", default=DEFAULT_LOMM_ROOT)
    parser.add_argument(
        "--out",
        default=os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "output",
            "lomm_orc_patch_v2",
        ),
    )
    parser.add_argument(
        "--table-mode",
        choices=(TABLE_MODE_APPEND, TABLE_MODE_APPEND_MAGE, TABLE_MODE_MAGE_SLOT),
        default=TABLE_MODE_APPEND,
        help=(
            "append creates row 304 from Lizard-Orc; append-mage creates row "
            "304 from Lizard-Orc Mage and places objects as LizardOrcMage; "
            "mage-slot overwrites unused Lizard-Orc Mage row 191 for runtime "
            "row-selection testing"
        ),
    )
    args = parser.parse_args(argv)

    result = build_lomm_orc_patch(
        args.mm9_root,
        args.lomm_root,
        args.out,
        table_mode=args.table_mode,
    )
    print(f"wrote LoMM Orc patch batch: {result['output_dir']}")
    print(f"manifest: {result['manifest']}")
    print(f"log: {result['log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
