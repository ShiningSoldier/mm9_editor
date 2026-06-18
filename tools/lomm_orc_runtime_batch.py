#!/usr/bin/env python3
"""Build the combined experimental LoMMOrcMage runtime output batch."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import _path_setup  # noqa: F401
from core.rezmgr import RezReader, RezWriter, _restype_for_filename
from tools import actor_table_patch, object_lto_class_experiment


DEFAULT_OBJECT_LTO_BATCH = os.path.join(ROOT, "output", "lomm_orc_object_lto_candidate")
DEFAULT_ASSET_BATCH = os.path.join(ROOT, "output", "lomm_orc_stage2_assets")
DEFAULT_OUT_DIR = os.path.join(ROOT, "output", "lomm_orc_runtime_stage3")

CLASS_NAME = "LoMMOrcMage"
PARENT_CLASS = "LizardOrcMage"
SOURCE_ROW = "191"
TARGET_ROW = "121"
STALE_EXPERIMENTAL_ROWS = ("301", "303", "304", "306")
MODEL_NAME = "OrcMM9.abc"
SKIN_NAME = "Orc.dtx"

ROW_191_ACTOR_DEFAULTS = {
    "Monster Name": "Lizard-Orc Mage",
    "ModelName": "lizardorc.abc",
    "SkinName": "LizardOrc.dtx",
    "SkinName2": "LizOrcCutlass.dtx",
    "SkinName3": "",
    "Type/Picture": "Lizard-Orc B",
    "ScriptName": "basemelee.scr",
    "FootSound": "LizardOrcstep",
    "FootRadius": "100",
    "BaseName": "LizardOrc",
    "IsMonster": "1",
}

ROW_191_MONSTER_DEFAULTS = {
    "Monster Name": "Lizard-Orc Mage",
    "ModelName": "lizardorc.abc",
    "SkinName": "LizardOrc.dtx",
    "SkinName2": "LizOrcCutlass.dtx",
    "SkinName3": "",
    "Type/Picture": "Lizard-Orc C",
    "ScriptName": "baserange.scr",
    "FootSound": "LizardOrcstep",
    "FootRadius": "350",
    "BaseName": "LizardOrc",
    "IsMonster": "1",
}


def _data_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), "data")


def _copy_file(src: str, dst: str) -> Dict[str, Any]:
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return {
        "source_file": os.path.abspath(src),
        "output_file": os.path.abspath(dst),
        "size": os.path.getsize(dst),
    }


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def _read_rez_text(rez_path: str, vpath: str) -> str:
    with RezReader(rez_path) as reader:
        return reader.extract_to_bytes(vpath).decode("latin-1")


def _target_row_exists(data_rez: str) -> Dict[str, bool]:
    import csv
    from io import StringIO

    result: Dict[str, bool] = {}
    for table, vpath in (("ACTOR.TXT", "DATA/ACTOR"), ("MONSTERS.TXT", "DATA/MONSTERS")):
        rows = list(csv.DictReader(StringIO(_read_rez_text(data_rez, vpath)), delimiter="\t"))
        result[table] = any(row.get("Number") == TARGET_ROW for row in rows)
    return result


def _patch_data_rez_rows(source_rez: str, output_rez: str) -> Dict[str, Any]:
    with RezReader(source_rez) as reader:
        actor_text = reader.extract_to_bytes("DATA/ACTOR").decode("latin-1")
        monsters_text = reader.extract_to_bytes("DATA/MONSTERS").decode("latin-1")

    patched_actor, actor_patch = actor_table_patch.patch_actor_table_text(
        actor_text,
        table_name="ACTOR.TXT",
        strategy=actor_table_patch.STRATEGY_REPLACE_ROW,
        source_row=SOURCE_ROW,
        target_row=SOURCE_ROW,
        field_overrides=ROW_191_ACTOR_DEFAULTS,
        target_class=PARENT_CLASS,
        runtime_row=SOURCE_ROW,
    )
    patched_monsters, monsters_patch = actor_table_patch.patch_actor_table_text(
        monsters_text,
        table_name="MONSTERS.TXT",
        strategy=actor_table_patch.STRATEGY_REPLACE_ROW,
        source_row=SOURCE_ROW,
        target_row=SOURCE_ROW,
        field_overrides=ROW_191_MONSTER_DEFAULTS,
        target_class=PARENT_CLASS,
        runtime_row=SOURCE_ROW,
    )
    patched_actor, actor_stale_rows = _strip_stale_experimental_rows(patched_actor)
    patched_monsters, monsters_stale_rows = _strip_stale_experimental_rows(patched_monsters)

    os.makedirs(os.path.dirname(output_rez), exist_ok=True)
    with RezWriter(source_rez, output_rez) as writer:
        writer.replace(
            "DATA/ACTOR",
            patched_actor.encode("latin-1"),
            restype=_restype_for_filename("ACTOR.TXT"),
        )
        writer.replace(
            "DATA/MONSTERS",
            patched_monsters.encode("latin-1"),
            restype=_restype_for_filename("MONSTERS.TXT"),
        )
        writer.commit()
    return {
        "actor_row_191": actor_patch.__dict__,
        "monster_row_191": monsters_patch.__dict__,
        "stale_experimental_rows_removed": {
            "ACTOR.TXT": actor_stale_rows,
            "MONSTERS.TXT": monsters_stale_rows,
        },
    }


def _strip_stale_experimental_rows(text: str) -> tuple[str, List[str]]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return text, []
    header = lines[0].rstrip("\r\n").split("\t")
    try:
        number_index = header.index("Number")
        name_index = header.index("Monster Name")
        model_index = header.index("ModelName")
        base_index = header.index("BaseName")
    except ValueError:
        return text, []

    kept = [lines[0]]
    removed: List[str] = []
    for line in lines[1:]:
        suffix = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
        body = line[:-len(suffix)] if suffix else line
        cells = body.split("\t") if body else []
        is_stale_lomm_row = (
            len(cells) > max(number_index, name_index, model_index, base_index)
            and cells[number_index].strip() in STALE_EXPERIMENTAL_ROWS
            and (
                cells[name_index].strip() == "LoMM Orc Mage"
                or cells[model_index].strip().lower() == MODEL_NAME.lower()
                or cells[base_index].strip() == CLASS_NAME
            )
        )
        if is_stale_lomm_row:
            removed.append(cells[number_index].strip())
            continue
        kept.append(line)
    return "".join(kept), removed


def _field_overrides() -> Dict[str, str]:
    return {
        "Monster Name": "LoMM Orc Mage",
        "ModelName": MODEL_NAME,
        "SkinName": SKIN_NAME,
        "SkinName2": "",
        "SkinName3": "",
        "Type/Picture": CLASS_NAME,
        "ScriptName": "baserange.scr",
        "FootSound": "orcstep",
        "FootRadius": "500",
        "BaseName": CLASS_NAME,
        "IsMonster": "1",
    }


def _prepare_temp_root(mm9_root: str, patched_data_rez: str) -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory(prefix="lomm_orc_runtime_root_")
    data_dir = os.path.join(tmp.name, "data")
    os.makedirs(data_dir, exist_ok=True)
    shutil.copy2(patched_data_rez, os.path.join(data_dir, "DATA.REZ"))
    shutil.copy2(os.path.join(_data_dir(mm9_root), "WORLDS.REZ"), os.path.join(data_dir, "WORLDS.REZ"))
    return tmp


def build_lomm_orc_runtime_batch(
    *,
    mm9_root: str,
    output_dir: str,
    object_lto_batch: str,
    asset_batch: str,
    level_name: str = "BOOTCAMP",
    object_name: str = "Stage5LoMMOrc1",
    script_name: str = "",
) -> Dict[str, Any]:
    output_dir = os.path.abspath(output_dir)
    output_data = os.path.join(output_dir, "data")
    os.makedirs(output_data, exist_ok=True)

    object_lto_data = os.path.join(os.path.abspath(object_lto_batch), "data")
    object_lto_dump = os.path.join(os.path.abspath(object_lto_batch), "object_lto_dump.json")
    object_lto_manifest = os.path.join(os.path.abspath(object_lto_batch), "manifest.json")
    asset_data = os.path.join(os.path.abspath(asset_batch), "data")
    asset_manifest = os.path.join(os.path.abspath(asset_batch), "manifest.json")
    for path in (
        os.path.join(object_lto_data, "object.lto"),
        os.path.join(object_lto_data, "object_lto_base.lto"),
        object_lto_dump,
        os.path.join(asset_data, "MODELS.REZ"),
        os.path.join(asset_data, "SKINS.REZ"),
    ):
        if not os.path.isfile(path):
            raise FileNotFoundError(path)

    row_patch_dir = os.path.join(output_dir, f"row{TARGET_ROW}_data_patch")
    row_patch = actor_table_patch.build_actor_table_patch(
        mm9_root=mm9_root,
        output_dir=row_patch_dir,
        strategy=actor_table_patch.STRATEGY_NEW_CLASS,
        target_class=CLASS_NAME,
        source_row=SOURCE_ROW,
        target_row=TARGET_ROW,
        runtime_row=TARGET_ROW,
        field_overrides=_field_overrides(),
    )
    patched_data_rez = row_patch["output_archive"]
    stock_data_rez = os.path.join(output_dir, "row191_stock_restore", "data", "DATA.REZ")
    row191_restore = _patch_data_rez_rows(patched_data_rez, stock_data_rez)

    with _prepare_temp_root(mm9_root, stock_data_rez) as temp_root:
        placement_dir = os.path.join(output_dir, "placement_stage5")
        placement = object_lto_class_experiment.build_object_lto_class_experiment(
            mm9_root=temp_root,
            output_dir=placement_dir,
            class_name=CLASS_NAME,
            parent_class=PARENT_CLASS,
            target_row=TARGET_ROW,
            level_name=level_name,
            object_name=object_name,
            pos=(0.0, 0.0, 0.0),
            yaw=0.0,
            filename=rf"models\{MODEL_NAME}",
            script_name=script_name,
            object_lto_dump=object_lto_dump,
        )

    if placement["status"] != "ready-to-place":
        status = "blocked"
    else:
        status = "ready"

    copied_files = [
        _copy_file(stock_data_rez, os.path.join(output_data, "DATA.REZ")),
        _copy_file(os.path.join(asset_data, "MODELS.REZ"), os.path.join(output_data, "MODELS.REZ")),
        _copy_file(os.path.join(asset_data, "SKINS.REZ"), os.path.join(output_data, "SKINS.REZ")),
        _copy_file(os.path.join(object_lto_data, "object.lto"), os.path.join(output_data, "object.lto")),
        _copy_file(
            os.path.join(object_lto_data, "object_lto_base.lto"),
            os.path.join(output_data, "object_lto_base.lto"),
        ),
    ]
    archives: List[Dict[str, Any]] = [
        {
            "source_archive": os.path.join(_data_dir(mm9_root), "DATA.REZ"),
            "output_archive": os.path.join(output_data, "DATA.REZ"),
            "entries": ["DATA/ACTOR", "DATA/MONSTERS"],
            "kind": "actor_tables",
        },
        {
            "source_archive": os.path.join(_data_dir(mm9_root), "MODELS.REZ"),
            "output_archive": os.path.join(output_data, "MODELS.REZ"),
            "entries": ["MODELS/ORCMM9"],
            "kind": "lomm_orc_model",
        },
        {
            "source_archive": os.path.join(_data_dir(mm9_root), "SKINS.REZ"),
            "output_archive": os.path.join(output_data, "SKINS.REZ"),
            "entries": ["SKINS/ORC"],
            "kind": "lomm_orc_skin",
        },
    ]
    placement_world = os.path.join(output_dir, "placement_stage5", "data", "WORLDS.REZ")
    if os.path.isfile(placement_world):
        copied_files.append(_copy_file(placement_world, os.path.join(output_data, "WORLDS.REZ")))
        archives.append({
            "source_archive": os.path.join(_data_dir(mm9_root), "WORLDS.REZ"),
            "output_archive": os.path.join(output_data, "WORLDS.REZ"),
            "entries": ["WORLDS/BOOTCAMP"],
            "kind": "throwaway_lomm_orc_placement",
        })

    validation_errors = list(placement.get("validation_errors") or [])
    row_checks = _target_row_exists(os.path.join(output_data, "DATA.REZ"))
    for table, exists in row_checks.items():
        if not exists:
            validation_errors.append(f"row {TARGET_ROW} missing from {table} in final DATA.REZ")
    if validation_errors:
        status = "blocked"

    loose_files = [
        {
            **copied_files[3],
            "target_relative": "data\\object.lto",
            "kind": "object_lto_wrapper",
            "install_note": "Loose file installed and restored through the manifest-aware install manager.",
        },
        {
            **copied_files[4],
            "target_relative": "data\\object_lto_base.lto",
            "kind": "object_lto_original_base",
            "install_note": "Required by the wrapper object.lto; installed and restored as a loose file.",
        },
    ]

    manifest = {
        "version": 1,
        "kind": "lomm_orc_runtime_stage3_batch",
        "created_at": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "status": status,
        "mm9_root": os.path.abspath(mm9_root),
        "output_dir": output_dir,
        "class_name": CLASS_NAME,
        "parent_class": PARENT_CLASS,
        "target_row": TARGET_ROW,
        "archives": archives,
        "loose_files": loose_files,
        "target_row_data_patch": {
            "manifest": row_patch["manifest"],
            "runtime_visibility": _load_json(row_patch["manifest"])["actor_table_patch"]["runtime_visibility"],
            "field_overrides": _field_overrides(),
            "row_checks": row_checks,
        },
        "row191_stock_restore": {
            "source_archive": patched_data_rez,
            "output_archive": stock_data_rez,
            "field_overrides": {
                "ACTOR.TXT": ROW_191_ACTOR_DEFAULTS,
                "MONSTERS.TXT": ROW_191_MONSTER_DEFAULTS,
            },
            "row_patches": row191_restore,
        },
        "object_lto": {
            "batch": os.path.abspath(object_lto_batch),
            "manifest": object_lto_manifest if os.path.isfile(object_lto_manifest) else None,
            "dump": object_lto_dump,
        },
        "assets": {
            "batch": os.path.abspath(asset_batch),
            "manifest": asset_manifest if os.path.isfile(asset_manifest) else None,
            "model": "MODELS/ORCMM9",
            "skin": "SKINS/ORC",
        },
        "placement": {
            "manifest": placement.get("manifest"),
            "status": placement.get("status"),
            "placement": placement.get("placement"),
            "actor_row_selection": placement.get("actor_row_selection"),
        },
        "sound_and_script_policy": (
            _load_json(asset_manifest).get("sound_and_script_policy")
            if os.path.isfile(asset_manifest)
            else None
        ),
        "validation_errors": validation_errors,
        "install_notes": [
            "Install/copy this batch only into a temporary or restorable MM9 install.",
            "The install manager installs both the REZ archives and the manifest-declared loose object.lto files.",
            "Keep extracted DATA subfolders such as DATA\\WORLDS, DATA\\MODELS, and DATA\\SKINS out of the live install while testing.",
        ],
    }

    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    log_path = os.path.join(output_dir, "runtime_batch_log.txt")
    with open(log_path, "w", encoding="utf-8") as fh:
        for item in copied_files:
            fh.write(f"copied {item['output_file']} {item['size']} bytes\n")
        for error in validation_errors:
            fh.write(f"ERROR {error}\n")

    return {
        "output_dir": output_dir,
        "manifest": manifest_path,
        "log": log_path,
        "status": status,
        "validation_errors": validation_errors,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the combined LoMMOrcMage object.lto/DATA/assets/world test batch."
    )
    parser.add_argument("--mm9-root", required=True, help="Path to the MM9 install root.")
    parser.add_argument("--object-lto-batch", default=DEFAULT_OBJECT_LTO_BATCH)
    parser.add_argument("--asset-batch", default=DEFAULT_ASSET_BATCH)
    parser.add_argument("--out", default=DEFAULT_OUT_DIR)
    parser.add_argument("--level", default="BOOTCAMP")
    parser.add_argument("--object-name", default="Stage5LoMMOrc1")
    parser.add_argument("--script-name", default="")
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args(argv)

    result = build_lomm_orc_runtime_batch(
        mm9_root=args.mm9_root,
        output_dir=args.out,
        object_lto_batch=args.object_lto_batch,
        asset_batch=args.asset_batch,
        level_name=args.level,
        object_name=args.object_name,
        script_name=args.script_name,
    )
    if args.print_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"wrote LoMM Orc runtime batch: {result['output_dir']}")
        print(f"status: {result['status']}")
        print(f"manifest: {result['manifest']}")
        print(f"log: {result['log']}")
        for error in result["validation_errors"]:
            print(f"validation error: {error}")
    return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
