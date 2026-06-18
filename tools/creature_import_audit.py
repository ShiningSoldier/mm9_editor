#!/usr/bin/env python3
"""Audit a proposed LoMM creature import into MM9.

This command does not modify archives. It reports which assets must be copied,
which actor/monster rows would be touched, and whether the selected row strategy
is expected to affect the game runtime or only the editor preview.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _path_setup  # noqa: F401
from core.rezmgr import RezReader


STRATEGY_REPLACE_ROW = "replace-row"
STRATEGY_APPEND_ROW = "append-row"
STRATEGY_NEW_CLASS = "new-class"

ASSET_CONFIG = {
    "models": {
        "subdir": "MODELS",
        "archive": "MODELS.REZ",
        "default_ext": ".ABC",
    },
    "skins": {
        "subdir": "SKINS",
        "archive": "SKINS.REZ",
        "default_ext": ".DTX",
    },
    "sounds": {
        "subdir": "SOUNDS",
        "archive": "SOUNDS.REZ",
        "default_ext": ".WAV",
    },
}


@dataclass(frozen=True)
class AssetRequest:
    kind: str
    source_ref: str
    target_ref: str


def _data_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), "data")


def _token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _object_name_prefix(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", str(value or ""))


def _archive_path(root: str, archive_name: str) -> str:
    return os.path.join(_data_dir(root), archive_name)


def _read_rez_text(rez_path: str, vpath: str) -> str:
    with RezReader(rez_path) as reader:
        return reader.extract_to_bytes(vpath).decode("latin-1")


def _split_ref(ref: str, default_ext: str) -> Tuple[str, str]:
    normalized = ref.replace("\\", "/").strip()
    root, ext = os.path.splitext(normalized)
    if not ext:
        ext = default_ext
    return root.strip("/"), ext.upper()


def _stem_key(ref: str, default_ext: str = "") -> str:
    root, _ext = _split_ref(ref, default_ext)
    parts = [part for part in root.replace("\\", "/").split("/") if part]
    return "/".join(parts).lower()


def _target_vpath(kind: str, target_ref: str) -> str:
    cfg = ASSET_CONFIG[kind]
    stem = _stem_key(target_ref, cfg["default_ext"])
    if kind == "sounds":
        return f"{cfg['subdir']}/{stem}".upper()
    return f"{cfg['subdir']}/{stem}".upper()


def _find_loose_asset(root: str, kind: str, ref: str) -> Optional[str]:
    cfg = ASSET_CONFIG[kind]
    data_dir = _data_dir(root)
    subdir = os.path.join(data_dir, cfg["subdir"])
    if not os.path.isdir(subdir):
        return None
    wanted = _stem_key(ref, cfg["default_ext"])
    for current, _dirs, files in os.walk(subdir):
        rel_dir = os.path.relpath(current, subdir)
        rel_dir = "" if rel_dir == "." else rel_dir.replace("\\", "/")
        for filename in files:
            rel = f"{rel_dir}/{filename}" if rel_dir else filename
            if _stem_key(rel, cfg["default_ext"]) == wanted:
                return os.path.abspath(os.path.join(current, filename))
    return None


def _find_rez_asset(root: str, kind: str, ref: str) -> Optional[Dict[str, Any]]:
    cfg = ASSET_CONFIG[kind]
    archive = _archive_path(root, cfg["archive"])
    if not os.path.isfile(archive):
        return None
    wanted = _stem_key(ref, cfg["default_ext"])
    with RezReader(archive) as reader:
        for path in reader.list_paths():
            if _stem_key(path, cfg["default_ext"]) == wanted:
                entry = reader.find(path)
                if entry is None:
                    continue
                return {
                    "archive": archive,
                    "virtual_path": entry.virtual_path(),
                    "typed_virtual_path": entry.typed_virtual_path(),
                    "size": entry.size,
                    "resource_type": entry.type_str,
                }
    return None


def _audit_asset(mm9_root: str, lomm_root: str, request: AssetRequest) -> Dict[str, Any]:
    target_vpath = _target_vpath(request.kind, request.target_ref)
    in_mm9_target = _find_rez_asset(mm9_root, request.kind, request.target_ref)
    source_loose = _find_loose_asset(lomm_root, request.kind, request.source_ref)
    source_rez = None if source_loose else _find_rez_asset(lomm_root, request.kind, request.source_ref)
    source_found = source_loose is not None or source_rez is not None

    source: Dict[str, Any]
    if source_loose:
        source = {"kind": "loose", "path": source_loose}
    elif source_rez:
        source = {"kind": "rez", **source_rez}
    else:
        source = {"kind": "missing"}

    return {
        "kind": request.kind,
        "source_ref": request.source_ref,
        "target_ref": request.target_ref,
        "target_archive": ASSET_CONFIG[request.kind]["archive"],
        "target_virtual_path": target_vpath,
        "source_found": source_found,
        "source": source,
        "already_in_mm9": in_mm9_target is not None,
        "mm9_existing": in_mm9_target,
        "action": (
            "reuse-existing-mm9-asset"
            if in_mm9_target is not None
            else ("copy-from-lomm" if source_found else "missing")
        ),
    }


def _table_rows(text: str) -> Tuple[List[str], List[Dict[str, str]]]:
    reader = csv.DictReader(StringIO(text), delimiter="\t")
    rows = [dict(row) for row in reader]
    return list(reader.fieldnames or []), rows


def _row_by_number(rows: Iterable[Dict[str, str]], row_number: str) -> Optional[Dict[str, str]]:
    for row in rows:
        if str(row.get("Number", "")).strip() == str(row_number):
            return row
    return None


def _infer_row_for_class(rows: Iterable[Dict[str, str]], target_class: str) -> Optional[Dict[str, str]]:
    target = _token(target_class)
    for row in rows:
        if _token(row.get("Monster Name", "")) == target:
            return row
    for row in rows:
        if _token(row.get("Type/Picture", "")) == target:
            return row
    return None


def _next_free_row(actor_rows: List[Dict[str, str]], monster_rows: List[Dict[str, str]]) -> int:
    used = set()
    for row in actor_rows + monster_rows:
        value = str(row.get("Number", "")).strip()
        if value.isdigit():
            used.add(int(value))
    candidate = max(used or {0}) + 1
    while candidate in used:
        candidate += 1
    return candidate


def _row_summary(row: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    if row is None:
        return None
    keys = (
        "Number",
        "Monster Name",
        "ModelName",
        "SkinName",
        "SkinName2",
        "SkinName3",
        "Type/Picture",
        "ScriptName",
        "FootSound",
        "FootRadius",
        "BaseName",
        "IsMonster",
    )
    return {key: str(row.get(key, "") or "") for key in keys if key in row}


def _strategy_report(strategy: str) -> Dict[str, str]:
    if strategy == STRATEGY_REPLACE_ROW:
        return {
            "runtime_visibility": "true-runtime-replacement",
            "summary": (
                "Replaces the row selected by an existing MM9 runtime class. "
                "This affects the game, but all uses of that class/row change."
            ),
        }
    if strategy == STRATEGY_APPEND_ROW:
        return {
            "runtime_visibility": "editor-preview-only-unless-class-selects-row",
            "summary": (
                "Adds a new table row. Existing MM9 classes are not expected "
                "to select it at runtime without a new class/row binding."
            ),
        }
    return {
        "runtime_visibility": "experimental-new-class-mapping",
        "summary": (
            "Requires an object.lto/runtime class that selects the new row. "
            "Not proven by actor-table edits alone."
        ),
    }


def _suggested_row_changes(
    row: Optional[Dict[str, str]],
    target_row: int,
    creature_name: str,
    model_target: Optional[str],
    skin_target: Optional[str],
    target_class: str,
    strategy: str,
) -> Dict[str, Any]:
    fields: Dict[str, str] = {}
    if strategy != STRATEGY_REPLACE_ROW:
        fields["Number"] = str(target_row)
        fields["Monster Name"] = creature_name
        fields["Type/Picture"] = creature_name
    if model_target:
        fields["ModelName"] = os.path.basename(model_target)
    if skin_target:
        fields["SkinName"] = os.path.basename(skin_target)
    if strategy in (STRATEGY_APPEND_ROW, STRATEGY_NEW_CLASS):
        fields["BaseName"] = target_class
    return {
        "source_row": _row_summary(row),
        "target_row": str(target_row),
        "field_overrides": fields,
    }


def _validation_checklist(strategy: str) -> List[str]:
    checklist = [
        "Install patched archives through the editor install flow.",
        "Keep the live game DATA folder free of loose extracted resource folders that shadow REZ archives.",
        "Regenerate/open the catalog after DATA/MODELS/SKINS changes.",
        "Place the target class in a throwaway test level.",
        "Verify editor preview model, primary skin, and accessory skins.",
        "Verify in-game spawn, idle, movement, attack, damage, death, and sounds.",
    ]
    if strategy == STRATEGY_REPLACE_ROW:
        checklist.append("Confirm every use of the replaced host class is allowed to change.")
    elif strategy == STRATEGY_APPEND_ROW:
        checklist.append("Do not expect game-visible changes until a runtime class selects the appended row.")
    else:
        checklist.append("Validate the new object.lto class appears in DEdit/editor and selects the intended row in game.")
    return checklist


def audit_creature_import(
    *,
    mm9_root: str,
    lomm_root: str,
    creature_name: str,
    target_class: str,
    row_strategy: str,
    model: Optional[str] = None,
    target_model: Optional[str] = None,
    skin: Optional[str] = None,
    target_skin: Optional[str] = None,
    sounds: Optional[List[str]] = None,
    target_row: Optional[int] = None,
) -> Dict[str, Any]:
    mm9_data = _data_dir(mm9_root)
    lomm_data = _data_dir(lomm_root)
    data_rez = _archive_path(mm9_root, "DATA.REZ")
    if not os.path.isfile(data_rez):
        raise FileNotFoundError(data_rez)

    actor_text = _read_rez_text(data_rez, "DATA/ACTOR")
    monsters_text = _read_rez_text(data_rez, "DATA/MONSTERS")
    _actor_header, actor_rows = _table_rows(actor_text)
    _monster_header, monster_rows = _table_rows(monsters_text)

    inferred_actor_row = _infer_row_for_class(actor_rows, target_class)
    inferred_monster_row = _infer_row_for_class(monster_rows, target_class)
    inferred_row_number = (
        str((inferred_monster_row or inferred_actor_row or {}).get("Number", "")).strip()
    )

    if target_row is None:
        if row_strategy == STRATEGY_REPLACE_ROW:
            if not inferred_row_number:
                raise ValueError(f"Could not infer selected row for class {target_class!r}; pass --target-row")
            target_row = int(inferred_row_number)
        else:
            target_row = _next_free_row(actor_rows, monster_rows)

    actor_target_row = _row_by_number(actor_rows, str(target_row))
    monster_target_row = _row_by_number(monster_rows, str(target_row))

    asset_requests: List[AssetRequest] = []
    if model:
        asset_requests.append(AssetRequest("models", model, target_model or model))
    if skin:
        asset_requests.append(AssetRequest("skins", skin, target_skin or skin))
    for sound in sounds or []:
        asset_requests.append(AssetRequest("sounds", sound, sound))

    assets = [_audit_asset(mm9_root, lomm_root, request) for request in asset_requests]
    missing_assets = [asset for asset in assets if asset["action"] == "missing"]

    strategy_info = _strategy_report(row_strategy)
    model_target = target_model or model
    skin_target = target_skin or skin
    editor_preview_only = row_strategy != STRATEGY_REPLACE_ROW
    suggested_visual_rule = {
        "type_str": target_class,
        "object_name_prefix": _object_name_prefix(creature_name),
        "script_name": "",
        "source_file": "MONSTERS.TXT",
        "source_row": str(target_row),
        "comment": (
            f"{creature_name} import maps {target_class} placements to "
            f"MONSTERS.TXT row {target_row} for editor preview."
        ),
        "editor_preview_only": editor_preview_only,
    }
    report = {
        "version": 1,
        "created_at": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "inputs": {
            "mm9_root": os.path.abspath(mm9_root),
            "lomm_root": os.path.abspath(lomm_root),
            "mm9_data_dir": mm9_data,
            "lomm_data_dir": lomm_data,
            "creature_name": creature_name,
            "target_class": target_class,
            "row_strategy": row_strategy,
        },
        "strategy": {
            "kind": row_strategy,
            **strategy_info,
        },
        "assets": {
            "copy_plan": assets,
            "missing": missing_assets,
        },
        "actor_rows": {
            "inferred_runtime_row": inferred_row_number or None,
            "target_row": str(target_row),
            "actor_target_row": _row_summary(actor_target_row),
            "monster_target_row": _row_summary(monster_target_row),
            "suggested_actor_row": _suggested_row_changes(
                actor_target_row or inferred_actor_row,
                target_row,
                creature_name,
                model_target,
                skin_target,
                target_class,
                row_strategy,
            ),
            "suggested_monster_row": _suggested_row_changes(
                monster_target_row or inferred_monster_row,
                target_row,
                creature_name,
                model_target,
                skin_target,
                target_class,
                row_strategy,
            ),
        },
        "visual_mapping": {
            "editor_mapping_key": _token(creature_name),
            "target_class": target_class,
            "requires_name_or_class_quirk": editor_preview_only,
            "suggested_rule": suggested_visual_rule,
            "runtime_note": strategy_info["summary"],
        },
        "warnings": [],
        "validation_checklist": _validation_checklist(row_strategy),
    }

    if row_strategy == STRATEGY_REPLACE_ROW and (actor_target_row is None or monster_target_row is None):
        report["warnings"].append(
            f"Row {target_row} was not found in both ACTOR and MONSTERS tables."
        )
    if missing_assets:
        report["warnings"].append("One or more requested LoMM assets were not found.")
    if row_strategy != STRATEGY_REPLACE_ROW:
        report["warnings"].append(
            "This strategy is not expected to change existing-class game behavior by itself."
        )

    return report


def _print_summary(report: Dict[str, Any]) -> None:
    print(f"creature: {report['inputs']['creature_name']}")
    print(f"target class: {report['inputs']['target_class']}")
    print(f"strategy: {report['strategy']['kind']} ({report['strategy']['runtime_visibility']})")
    print(f"target row: {report['actor_rows']['target_row']}")
    print("assets:")
    for asset in report["assets"]["copy_plan"]:
        print(
            f"  {asset['kind']}: {asset['source_ref']} -> "
            f"{asset['target_archive']}::{asset['target_virtual_path']} "
            f"[{asset['action']}]"
        )
    if report["warnings"]:
        print("warnings:")
        for warning in report["warnings"]:
            print(f"  - {warning}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit a proposed LoMM creature import into MM9."
    )
    parser.add_argument("--mm9-root", required=True, help="Path to the MM9 install root.")
    parser.add_argument("--lomm-root", required=True, help="Path to the LoMM install root.")
    parser.add_argument("--creature-name", required=True)
    parser.add_argument("--target-class", required=True)
    parser.add_argument(
        "--row-strategy",
        choices=(STRATEGY_REPLACE_ROW, STRATEGY_APPEND_ROW, STRATEGY_NEW_CLASS),
        required=True,
    )
    parser.add_argument("--target-row", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--target-model", default=None)
    parser.add_argument("--skin", default=None)
    parser.add_argument("--target-skin", default=None)
    parser.add_argument("--sound", action="append", default=[])
    parser.add_argument("--out", default=None, help="optional JSON report path")
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args(argv)

    report = audit_creature_import(
        mm9_root=args.mm9_root,
        lomm_root=args.lomm_root,
        creature_name=args.creature_name,
        target_class=args.target_class,
        row_strategy=args.row_strategy,
        model=args.model,
        target_model=args.target_model,
        skin=args.skin,
        target_skin=args.target_skin,
        sounds=args.sound,
        target_row=args.target_row,
    )

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        print(f"wrote audit report: {args.out}")

    if args.print_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_summary(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
