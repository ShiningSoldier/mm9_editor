#!/usr/bin/env python3
"""Build an installable DATA.REZ batch for ACTOR/MONSTERS row edits."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _path_setup  # noqa: F401
from core.rezmgr import RezReader, RezWriter, _restype_for_filename


DEFAULT_MM9_ROOT = r"C:\Program Files (x86)\GOG Galaxy\Games\Might and Magic 9"
ENTRY_ACTOR = "DATA/ACTOR"
ENTRY_MONSTERS = "DATA/MONSTERS"
STRATEGY_REPLACE_ROW = "replace-row"
STRATEGY_APPEND_ROW = "append-row"
STRATEGY_NEW_CLASS = "new-class"


@dataclass(frozen=True)
class RowPatch:
    table: str
    action: str
    source_row: str
    target_row: str
    original_row: Optional[Dict[str, str]]
    patched_row: Dict[str, str]
    selected_by_runtime_class: bool


def _data_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), "data")


def _data_rez(root: str) -> str:
    return os.path.join(_data_dir(root), "DATA.REZ")


def _line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _parse_set(values: Iterable[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise ValueError(f"--set expects Field=Value, got {raw!r}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--set has an empty field name: {raw!r}")
        out[key] = value
    return out


def _parse_table(text: str) -> Tuple[List[str], List[Tuple[str, List[str], str]]]:
    lines = text.splitlines(keepends=True)
    if not lines:
        raise ValueError("actor table is empty")
    header = lines[0].rstrip("\r\n").split("\t")
    rows: List[Tuple[str, List[str], str]] = []
    for line in lines[1:]:
        suffix = "\r\n" if line.endswith("\r\n") else (
            "\n" if line.endswith("\n") else ""
        )
        body = line[:-len(suffix)] if suffix else line
        if not body:
            rows.append(("", [], suffix))
            continue
        cells = body.split("\t")
        rows.append((body, cells, suffix))
    return header, rows


def _row_dict(header: List[str], cells: List[str]) -> Dict[str, str]:
    padded = list(cells)
    if len(padded) < len(header):
        padded.extend([""] * (len(header) - len(padded)))
    return {name: padded[index] for index, name in enumerate(header)}


def _cells_from_dict(header: List[str], row: Dict[str, str]) -> List[str]:
    return [str(row.get(name, "") or "") for name in header]


def _find_row_index(
    header: List[str],
    rows: List[Tuple[str, List[str], str]],
    row_number: str,
) -> Optional[int]:
    try:
        number_index = header.index("Number")
    except ValueError as exc:
        raise ValueError("actor table is missing Number column") from exc
    for index, (_body, cells, _suffix) in enumerate(rows):
        if cells and len(cells) > number_index and cells[number_index].strip() == str(row_number):
            return index
    return None


def _patch_table_text(
    text: str,
    *,
    table_name: str,
    strategy: str,
    source_row: str,
    target_row: str,
    field_overrides: Dict[str, str],
    target_class: str,
    runtime_row: Optional[str],
) -> Tuple[str, RowPatch]:
    header, rows = _parse_table(text)
    unknown_fields = sorted(key for key in field_overrides if key not in header)
    if unknown_fields:
        joined = ", ".join(unknown_fields)
        raise ValueError(f"{table_name} does not contain field(s): {joined}")

    source_index = _find_row_index(header, rows, source_row)
    if source_index is None:
        raise ValueError(f"{table_name} source row {source_row} was not found")

    original = _row_dict(header, rows[source_index][1])
    patched = dict(original)
    patched.update(field_overrides)
    patched["Number"] = str(target_row)

    newline = _line_ending(text)
    selected = runtime_row is not None and str(target_row) == str(runtime_row)

    if strategy == STRATEGY_REPLACE_ROW:
        if str(source_row) != str(target_row):
            raise ValueError("replace-row requires source row and target row to match")
        suffix = rows[source_index][2] or newline
        rows[source_index] = (
            "\t".join(_cells_from_dict(header, patched)),
            _cells_from_dict(header, patched),
            suffix,
        )
        action = "replaced"
        original_row = original
    else:
        target_index = _find_row_index(header, rows, target_row)
        target_cells = _cells_from_dict(header, patched)
        if target_index is None:
            rows.append(("\t".join(target_cells), target_cells, newline))
            action = "added"
            original_row = None
        else:
            suffix = rows[target_index][2] or newline
            original_row = _row_dict(header, rows[target_index][1])
            rows[target_index] = ("\t".join(target_cells), target_cells, suffix)
            action = "updated"

    rebuilt = [text.splitlines(keepends=True)[0]]
    for body, _cells, suffix in rows:
        rebuilt.append(body + suffix)
    return "".join(rebuilt), RowPatch(
        table=table_name,
        action=action,
        source_row=str(source_row),
        target_row=str(target_row),
        original_row=original_row,
        patched_row=patched,
        selected_by_runtime_class=selected,
    )


def patch_actor_table_text(
    text: str,
    *,
    table_name: str,
    strategy: str,
    source_row: str,
    target_row: str,
    field_overrides: Dict[str, str],
    target_class: str,
    runtime_row: Optional[str],
) -> Tuple[str, RowPatch]:
    """Patch one actor table while preserving the table's tabular text shape."""
    return _patch_table_text(
        text,
        table_name=table_name,
        strategy=strategy,
        source_row=source_row,
        target_row=target_row,
        field_overrides=field_overrides,
        target_class=target_class,
        runtime_row=runtime_row,
    )


def _write_changed_entry(batch_dir: str, vpath: str, payload: bytes) -> str:
    rel = vpath.replace("\\", "/")
    ext = ".TXT"
    out_path = os.path.join(batch_dir, "changed_entries", *rel.split("/")) + ext
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(payload)
    return out_path


def _runtime_visibility(strategy: str, selected_by_runtime_class: bool) -> str:
    if strategy == STRATEGY_REPLACE_ROW and selected_by_runtime_class:
        return "true-runtime-replacement"
    if strategy == STRATEGY_APPEND_ROW:
        return "editor-preview-only-unless-class-selects-row"
    if strategy == STRATEGY_NEW_CLASS:
        return "experimental-new-class-mapping"
    return "runtime-selection-unknown"


def classify_runtime_visibility(strategy: str, selected_by_runtime_class: bool) -> str:
    return _runtime_visibility(strategy, selected_by_runtime_class)


def build_actor_table_patch(
    *,
    mm9_root: str,
    output_dir: str,
    strategy: str,
    target_class: str,
    source_row: str,
    target_row: str,
    runtime_row: Optional[str],
    field_overrides: Dict[str, str],
) -> Dict[str, Any]:
    source_rez = _data_rez(mm9_root)
    if not os.path.isfile(source_rez):
        raise FileNotFoundError(source_rez)

    output_dir = os.path.abspath(output_dir)
    output_data = os.path.join(output_dir, "data")
    os.makedirs(output_data, exist_ok=True)
    output_rez = os.path.join(output_data, "DATA.REZ")

    with RezReader(source_rez) as reader:
        actor_text = reader.extract_to_bytes(ENTRY_ACTOR).decode("latin-1")
        monsters_text = reader.extract_to_bytes(ENTRY_MONSTERS).decode("latin-1")

    patched_actor, actor_patch = _patch_table_text(
        actor_text,
        table_name="ACTOR.TXT",
        strategy=strategy,
        source_row=source_row,
        target_row=target_row,
        field_overrides=field_overrides,
        target_class=target_class,
        runtime_row=runtime_row,
    )
    patched_monsters, monsters_patch = _patch_table_text(
        monsters_text,
        table_name="MONSTERS.TXT",
        strategy=strategy,
        source_row=source_row,
        target_row=target_row,
        field_overrides=field_overrides,
        target_class=target_class,
        runtime_row=runtime_row,
    )

    actor_bytes = patched_actor.encode("latin-1")
    monsters_bytes = patched_monsters.encode("latin-1")
    log: List[str] = []
    with RezWriter(source_rez, output_rez) as writer:
        writer.replace(ENTRY_ACTOR, actor_bytes, restype=_restype_for_filename("ACTOR.TXT"))
        writer.replace(ENTRY_MONSTERS, monsters_bytes, restype=_restype_for_filename("MONSTERS.TXT"))
        result = writer.commit()
    log.extend(result.get("log", []))

    changed_actor = _write_changed_entry(output_dir, ENTRY_ACTOR, actor_bytes)
    changed_monsters = _write_changed_entry(output_dir, ENTRY_MONSTERS, monsters_bytes)

    selected = actor_patch.selected_by_runtime_class or monsters_patch.selected_by_runtime_class
    manifest = {
        "version": 1,
        "created_at": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "kind": "actor_table_patch",
        "game_data_dir": _data_dir(mm9_root),
        "mm9_root": os.path.abspath(mm9_root),
        "archives": [{
            "source_archive": source_rez,
            "output_archive": output_rez,
            "entries": [ENTRY_ACTOR, ENTRY_MONSTERS],
            "kind": "actor_tables",
        }],
        "actor_table_patch": {
            "strategy": strategy,
            "runtime_visibility": _runtime_visibility(strategy, selected),
            "target_class": target_class,
            "runtime_row": str(runtime_row) if runtime_row is not None else None,
            "source_row": str(source_row),
            "target_row": str(target_row),
            "field_overrides": dict(field_overrides),
            "row_patches": [
                actor_patch.__dict__,
                monsters_patch.__dict__,
            ],
            "changed_entries": [changed_actor, changed_monsters],
        },
    }

    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    log_path = os.path.join(output_dir, "actor_table_patch_log.txt")
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")

    return {
        "output_dir": output_dir,
        "manifest": manifest_path,
        "log": log_path,
        "archives": manifest["archives"],
        "output_archive": output_rez,
        "row_patches": manifest["actor_table_patch"]["row_patches"],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an installable DATA.REZ patch for ACTOR/MONSTERS rows."
    )
    parser.add_argument("--mm9-root", default=DEFAULT_MM9_ROOT)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--strategy",
        choices=(STRATEGY_REPLACE_ROW, STRATEGY_APPEND_ROW, STRATEGY_NEW_CLASS),
        required=True,
    )
    parser.add_argument("--target-class", required=True)
    parser.add_argument("--source-row", required=True)
    parser.add_argument("--target-row", required=True)
    parser.add_argument("--runtime-row", default=None)
    parser.add_argument("--set", action="append", default=[], dest="sets")
    args = parser.parse_args(argv)

    result = build_actor_table_patch(
        mm9_root=args.mm9_root,
        output_dir=args.out,
        strategy=args.strategy,
        target_class=args.target_class,
        source_row=str(args.source_row),
        target_row=str(args.target_row),
        runtime_row=str(args.runtime_row) if args.runtime_row is not None else None,
        field_overrides=_parse_set(args.sets),
    )
    print(f"wrote actor table patch batch: {result['output_dir']}")
    print(f"manifest: {result['manifest']}")
    print(f"log: {result['log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
