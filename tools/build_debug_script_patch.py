#!/usr/bin/env python3
"""Build an installable MM9 output batch containing editor debug scripts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _path_setup  # noqa: F401
from core.rezmgr import RezReader, RezWriter, _restype_for_filename


DEFAULT_MM9_ROOT = r"C:\Program Files (x86)\GOG Galaxy\Games\Might and Magic 9"
DEBUG_SCRIPT_NAME = "MM9ED_DEBUG_ACTOR.SCR"
DEBUG_SCRIPT_ENTRY_NAME = "MM9ED_DEBUG_ACTOR"
DEBUG_SCRIPT_VPATH = f"SCRIPTS/{DEBUG_SCRIPT_ENTRY_NAME}"
DEBUG_SCRIPT_TYPED_VPATH = f"{DEBUG_SCRIPT_VPATH}.SCR"
SCRIPT_TEXT_VPATH = "SCRIPTS/MMIXSCRIPTTEXT"
SCRIPT_TEXT_TYPED_VPATH = f"{SCRIPT_TEXT_VPATH}.CSV"
DEBUG_TEXT_ROW = 300
DEBUG_TEXT = "MM9ED_DEBUG_ACTOR script is running."


def _data_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), "data")


def _read_file(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _patched_script_text(payload: bytes) -> bytes:
    text = payload.decode("latin-1")
    line = f"{DEBUG_TEXT_ROW},{DEBUG_TEXT}"
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for idx, existing in enumerate(lines):
        if existing.startswith(f"{DEBUG_TEXT_ROW},"):
            lines[idx] = line
            break
    else:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(line)
    return ("\r\n".join(lines) + "\r\n").encode("latin-1")


def _patch_scripts_rez(
    source_rez: str,
    output_rez: str,
    source_script: str,
    script_vpath: str,
) -> List[str]:
    payload = _read_file(source_script)
    restype = _restype_for_filename(source_script)
    log: List[str] = []
    with RezReader(source_rez) as reader:
        script_text = _patched_script_text(reader.extract_to_bytes(SCRIPT_TEXT_VPATH))
        with RezWriter(source_rez, output_rez) as writer:
            if reader.find(script_vpath) is None:
                writer.add(script_vpath, payload, restype=restype)
                log.append(f"added {script_vpath}")
            else:
                writer.replace(script_vpath, payload, restype=restype)
                log.append(f"replaced {script_vpath}")
            writer.replace(
                SCRIPT_TEXT_VPATH,
                script_text,
                restype=_restype_for_filename("MMIXScriptText.csv"),
            )
            log.append(f"replaced {SCRIPT_TEXT_TYPED_VPATH} row {DEBUG_TEXT_ROW}")
            result = writer.commit()
    log.extend(result.get("log", []))
    return log


def build_debug_script_patch(
    mm9_root: str,
    output_dir: str,
    source_script: str | None = None,
) -> Dict[str, Any]:
    mm9_data = _data_dir(mm9_root)
    scripts_rez = os.path.join(mm9_data, "SCRIPTS.REZ")
    source_script = source_script or os.path.join(
        ROOT, "tools", "debug_scripts", DEBUG_SCRIPT_NAME)

    for path in (scripts_rez, source_script):
        if not os.path.isfile(path):
            raise FileNotFoundError(path)

    output_dir = os.path.abspath(output_dir)
    output_data = os.path.join(output_dir, "data")
    changed_dir = os.path.join(output_dir, "changed_entries", "SCRIPTS")
    os.makedirs(output_data, exist_ok=True)
    os.makedirs(changed_dir, exist_ok=True)

    scripts_output = os.path.join(output_data, "SCRIPTS.REZ")
    log = _patch_scripts_rez(
        scripts_rez,
        scripts_output,
        source_script,
        DEBUG_SCRIPT_VPATH,
    )

    changed_copy = os.path.join(changed_dir, DEBUG_SCRIPT_NAME)
    with open(changed_copy, "wb") as fh:
        fh.write(_read_file(source_script))
    text_changed_copy = os.path.join(changed_dir, "MMIXSCRIPTTEXT.CSV")
    with RezReader(scripts_output) as reader:
        with open(text_changed_copy, "wb") as fh:
            fh.write(reader.extract_to_bytes(SCRIPT_TEXT_VPATH))

    archives = [{
        "source_archive": scripts_rez,
        "output_archive": scripts_output,
        "entries": [DEBUG_SCRIPT_VPATH, SCRIPT_TEXT_VPATH],
        "kind": "scripts",
    }]
    manifest = {
        "version": 1,
        "created_at": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "kind": "debug_script_patch",
        "game_data_dir": mm9_data,
        "mm9_root": os.path.abspath(mm9_root),
        "archives": archives,
        "debug_scripts": [{
            "name": DEBUG_SCRIPT_NAME,
            "source_script": os.path.abspath(source_script),
            "virtual_path": DEBUG_SCRIPT_VPATH,
            "typed_virtual_path": DEBUG_SCRIPT_TYPED_VPATH,
            "script_name_property": r"scripts\MM9ED_DEBUG_ACTOR.scr",
            "rollover_text_row": DEBUG_TEXT_ROW,
            "rollover_text": DEBUG_TEXT,
        }],
        "script_text": {
            "virtual_path": SCRIPT_TEXT_VPATH,
            "typed_virtual_path": SCRIPT_TEXT_TYPED_VPATH,
            "debug_row": DEBUG_TEXT_ROW,
            "debug_text": DEBUG_TEXT,
        },
    }

    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    log_path = os.path.join(output_dir, "debug_script_patch_log.txt")
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")

    return {
        "output_dir": output_dir,
        "manifest": manifest_path,
        "log": log_path,
        "archives": archives,
        "changed_copy": changed_copy,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an installable SCRIPTS.REZ patch for MM9 debug scripts."
    )
    parser.add_argument("--mm9-root", default=DEFAULT_MM9_ROOT)
    parser.add_argument("--script", default=None)
    parser.add_argument(
        "--out",
        default=os.path.join(ROOT, "output", "debug_scripts"),
    )
    args = parser.parse_args(argv)

    result = build_debug_script_patch(args.mm9_root, args.out, args.script)
    print(f"wrote debug script patch batch: {result['output_dir']}")
    print(f"manifest: {result['manifest']}")
    print(f"log: {result['log']}")
    print(f"script entry: {DEBUG_SCRIPT_TYPED_VPATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
