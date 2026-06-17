#!/usr/bin/env python3
"""Create a reversible MM9 output batch for testing the LoMM Dwarf.

This intentionally replaces the stock MM9 Dwarf actor asset names in output
archives instead of adding a new actor row.  It is a compatibility probe: the
game already knows how to spawn DwarvenGuard/DwarvenSoldier/DwarvenCommander,
so replacing MODELS/DWARF and SKINS/DWARF avoids the actor-row selection issue
that made the LoMM Orc test ambiguous.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _path_setup  # noqa: F401
from core.rezmgr import RezReader, RezWriter, _restype_for_filename


DEFAULT_MM9_ROOT = r"C:\Program Files (x86)\GOG Galaxy\Games\Might and Magic 9"
DEFAULT_LOMM_ROOT = r"C:\games\Legends of Might and Magic"
DWARF_MODEL_VPATH = "MODELS/DWARF"
DWARF_SKIN_VPATH = "SKINS/DWARF"


def _data_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), "data")


def _read_file(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


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


def build_lomm_dwarf_patch(
    mm9_root: str,
    lomm_root: str,
    output_dir: str,
) -> Dict[str, Any]:
    mm9_data = _data_dir(mm9_root)
    lomm_data = _data_dir(lomm_root)
    models_rez = os.path.join(mm9_data, "MODELS.REZ")
    skins_rez = os.path.join(mm9_data, "SKINS.REZ")
    lomm_model = os.path.join(lomm_data, "MODELS", "DWARF.ABC")
    lomm_skin = os.path.join(lomm_data, "SKINS", "DWARF.DTX")
    for path in (models_rez, skins_rez, lomm_model, lomm_skin):
        if not os.path.isfile(path):
            raise FileNotFoundError(path)

    output_dir = os.path.abspath(output_dir)
    output_data = os.path.join(output_dir, "data")
    os.makedirs(output_data, exist_ok=True)

    archives: List[Dict[str, Any]] = []
    log: List[str] = []

    models_output = os.path.join(output_data, "MODELS.REZ")
    log.extend(_patch_rez(models_rez, models_output, [
        (DWARF_MODEL_VPATH, _read_file(lomm_model), _restype_for_filename(lomm_model)),
    ]))
    archives.append({
        "source_archive": models_rez,
        "output_archive": models_output,
        "entries": [DWARF_MODEL_VPATH],
    })

    skins_output = os.path.join(output_data, "SKINS.REZ")
    log.extend(_patch_rez(skins_rez, skins_output, [
        (DWARF_SKIN_VPATH, _read_file(lomm_skin), _restype_for_filename(lomm_skin)),
    ]))
    archives.append({
        "source_archive": skins_rez,
        "output_archive": skins_output,
        "entries": [DWARF_SKIN_VPATH],
    })

    manifest = {
        "version": 1,
        "created_at": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "kind": "lomm_dwarf_replace_stock_patch",
        "game_data_dir": mm9_data,
        "mm9_root": os.path.abspath(mm9_root),
        "lomm_root": os.path.abspath(lomm_root),
        "archives": archives,
        "lomm_dwarf": {
            "mode": "replace-stock-dwarf-assets",
            "runtime_classes": [
                "DwarvenGuard",
                "DwarvenSoldier",
                "DwarvenCommander",
            ],
            "model": DWARF_MODEL_VPATH,
            "skin": DWARF_SKIN_VPATH,
            "source_model": lomm_model,
            "source_skin": lomm_skin,
            "actor_rows": [120, 121, 122],
        },
    }

    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    log_path = os.path.join(output_dir, "lomm_dwarf_patch_log.txt")
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
        description="Build a reversible MM9 output batch for the LoMM Dwarf."
    )
    parser.add_argument("--mm9-root", default=DEFAULT_MM9_ROOT)
    parser.add_argument("--lomm-root", default=DEFAULT_LOMM_ROOT)
    parser.add_argument(
        "--out",
        default=os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "output",
            "lomm_dwarf_replace_stock",
        ),
    )
    args = parser.parse_args(argv)

    result = build_lomm_dwarf_patch(args.mm9_root, args.lomm_root, args.out)
    print(f"wrote LoMM Dwarf patch batch: {result['output_dir']}")
    print(f"manifest: {result['manifest']}")
    print(f"log: {result['log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
