#!/usr/bin/env python3
"""Build and verify the LoMM Orc asset-only output batch for MM9."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime
from io import StringIO
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import _path_setup  # noqa: F401
from core.rezmgr import RezReader, RezWriter, _restype_for_filename
from tools import port_lomm_orc


DEFAULT_OUT_DIR = os.path.join(ROOT, "output", "lomm_orc_stage2_assets")

TARGET_MODEL_VPATH = port_lomm_orc.ORC_MODEL_VPATH
TARGET_SKIN_VPATH = port_lomm_orc.ORC_SKIN_VPATH
TARGET_MODEL_NAME = port_lomm_orc.ORC_MODEL_NAME
TARGET_SKIN_NAME = port_lomm_orc.ORC_SKIN_NAME
SOURCE_MODEL_REL = os.path.join("MODELS", "ORC.ABC")
SOURCE_SKIN_REL = os.path.join("SKINS", "ORC.DTX")
HOST_ROW = "191"
HOST_CLASS = "LizardOrcMage"
HOST_SOUND_TOKEN = "LIZARDORC"


def _data_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), "data")


def _read_file(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    with RezReader(source_rez) as reader:
        with RezWriter(source_rez, output_rez) as writer:
            for vpath, payload, restype in updates:
                action = _replace_or_add(writer, reader, vpath, payload, restype)
                actions.append({
                    "action": action,
                    "virtual_path": vpath,
                    "size": len(payload),
                    "sha256": _sha256(payload),
                })
            writer.commit()
    return actions


def _read_rez_text(rez_path: str, vpath: str) -> str:
    with RezReader(rez_path) as reader:
        return reader.extract_to_bytes(vpath).decode("latin-1")


def _table_rows(text: str) -> List[Dict[str, str]]:
    return list(csv.DictReader(StringIO(text), delimiter="\t"))


def _row_by_number(rows: Iterable[Dict[str, str]], number: str) -> Optional[Dict[str, str]]:
    for row in rows:
        if str(row.get("Number", "")).strip() == str(number):
            return row
    return None


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


def _script_vpath(script_name: str) -> str:
    stem = os.path.splitext(str(script_name or "").replace("\\", "/"))[0]
    return f"SCRIPTS/{stem}".upper()


def _footstep_vpaths(foot_sound: str) -> List[str]:
    stem = str(foot_sound or "").strip()
    if not stem:
        return []
    return [
        f"SOUNDS/ANIMSOUNDS/FOOTSTEPS/{stem}1".upper(),
        f"SOUNDS/ANIMSOUNDS/FOOTSTEPS/{stem}2".upper(),
    ]


def _existing_paths(rez_path: str, paths: Iterable[str]) -> Dict[str, bool]:
    with RezReader(rez_path) as reader:
        return {path: reader.find(path) is not None for path in paths}


def _paths_with_prefix(rez_path: str, prefix: str) -> List[str]:
    normalized = prefix.replace("\\", "/").upper().rstrip("/") + "/"
    with RezReader(rez_path) as reader:
        return sorted(path for path in reader.list_paths() if path.upper().startswith(normalized))


def _paths_with_stem_prefix(rez_path: str, prefix: str) -> List[str]:
    normalized = prefix.replace("\\", "/").upper().rstrip("/")
    with RezReader(rez_path) as reader:
        return sorted(path for path in reader.list_paths() if path.upper().startswith(normalized))


def _verify_output_entry(rez_path: str, virtual_path: str, expected: bytes) -> Dict[str, Any]:
    with RezReader(rez_path) as reader:
        entry = reader.find(virtual_path)
        data = reader.extract_to_bytes(virtual_path) if entry is not None else b""
    return {
        "archive": rez_path,
        "virtual_path": virtual_path,
        "exists": entry is not None,
        "size": len(data),
        "sha256": _sha256(data) if entry is not None else None,
        "matches_expected_payload": entry is not None and data == expected,
    }


def build_lomm_orc_asset_batch(
    mm9_root: str,
    lomm_root: str,
    output_dir: str,
) -> Dict[str, Any]:
    mm9_data = _data_dir(mm9_root)
    lomm_data = _data_dir(lomm_root)
    data_rez = os.path.join(mm9_data, "DATA.REZ")
    models_rez = os.path.join(mm9_data, "MODELS.REZ")
    skins_rez = os.path.join(mm9_data, "SKINS.REZ")
    scripts_rez = os.path.join(mm9_data, "SCRIPTS.REZ")
    sounds_rez = os.path.join(mm9_data, "SOUNDS.REZ")
    source_model = os.path.join(lomm_data, SOURCE_MODEL_REL)
    source_skin = os.path.join(lomm_data, SOURCE_SKIN_REL)
    for path in (data_rez, models_rez, skins_rez, scripts_rez, sounds_rez, source_model, source_skin):
        if not os.path.isfile(path):
            raise FileNotFoundError(path)

    output_dir = os.path.abspath(output_dir)
    output_data = os.path.join(output_dir, "data")
    os.makedirs(output_data, exist_ok=True)

    source_model_bytes = _read_file(source_model)
    patched_model, animation_replacements = port_lomm_orc._patched_orc_model(
        source_model_bytes)
    skin_bytes = _read_file(source_skin)

    models_output = os.path.join(output_data, "MODELS.REZ")
    skins_output = os.path.join(output_data, "SKINS.REZ")
    model_actions = _patch_rez(models_rez, models_output, [
        (TARGET_MODEL_VPATH, patched_model, _restype_for_filename(source_model)),
    ])
    skin_actions = _patch_rez(skins_rez, skins_output, [
        (TARGET_SKIN_VPATH, skin_bytes, _restype_for_filename(source_skin)),
    ])

    actor_rows = _table_rows(_read_rez_text(data_rez, "DATA/ACTOR"))
    monster_rows = _table_rows(_read_rez_text(data_rez, "DATA/MONSTERS"))
    actor_row = _row_by_number(actor_rows, HOST_ROW)
    monster_row = _row_by_number(monster_rows, HOST_ROW)
    sound_source_row = monster_row or actor_row
    script_name = str((sound_source_row or {}).get("ScriptName", "") or "baserange.scr")
    foot_sound = str((sound_source_row or {}).get("FootSound", "") or "LizardOrcstep")
    script_checks = _existing_paths(scripts_rez, [_script_vpath(script_name)])
    footstep_checks = _existing_paths(sounds_rez, _footstep_vpaths(foot_sound))
    inherited_anim_sounds = _paths_with_prefix(
        sounds_rez,
        f"SOUNDS/ANIMSOUNDS/{HOST_SOUND_TOKEN}",
    )
    inherited_death_sounds = _paths_with_prefix(
        sounds_rez,
        f"SOUNDS/DEATHSOUNDS/{HOST_SOUND_TOKEN}",
    )
    lomm_sounds_rez = os.path.join(lomm_data, "SOUNDS.REZ")
    lomm_orc_sound_sources = (
        _paths_with_stem_prefix(lomm_sounds_rez, "SOUNDS/ANIMSOUNDS/ORC")
        + _paths_with_stem_prefix(lomm_sounds_rez, "SOUNDS/DEATHSOUNDS/ORC")
    )

    model_verification = _verify_output_entry(models_output, TARGET_MODEL_VPATH, patched_model)
    skin_verification = _verify_output_entry(skins_output, TARGET_SKIN_VPATH, skin_bytes)
    validation_errors: List[str] = []
    if not model_verification["matches_expected_payload"]:
        validation_errors.append(f"{TARGET_MODEL_VPATH} did not verify in MODELS.REZ")
    if not skin_verification["matches_expected_payload"]:
        validation_errors.append(f"{TARGET_SKIN_VPATH} did not verify in SKINS.REZ")
    if not all(script_checks.values()):
        validation_errors.append(f"script reference {script_name!r} was not found in SCRIPTS.REZ")
    if not all(footstep_checks.values()):
        validation_errors.append(f"footstep sound group {foot_sound!r} was not complete in SOUNDS.REZ")
    if not inherited_anim_sounds:
        validation_errors.append("inherited LizardOrc animation sound set was not found")
    if not inherited_death_sounds:
        validation_errors.append("inherited LizardOrc death sound set was not found")

    manifest = {
        "version": 1,
        "kind": "lomm_orc_stage2_asset_batch",
        "created_at": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "status": "ready" if not validation_errors else "blocked",
        "mm9_root": os.path.abspath(mm9_root),
        "lomm_root": os.path.abspath(lomm_root),
        "output_dir": output_dir,
        "archives": [
            {
                "source_archive": models_rez,
                "output_archive": models_output,
                "entries": [TARGET_MODEL_VPATH],
            },
            {
                "source_archive": skins_rez,
                "output_archive": skins_output,
                "entries": [TARGET_SKIN_VPATH],
            },
        ],
        "assets": {
            "model": {
                "source_file": source_model,
                "target_archive": "MODELS.REZ",
                "target_virtual_path": TARGET_MODEL_VPATH,
                "target_row_name": TARGET_MODEL_NAME,
                "source_size": len(source_model_bytes),
                "patched_size": len(patched_model),
                "source_sha256": _sha256(source_model_bytes),
                "patched_sha256": _sha256(patched_model),
                "animation_replacements": animation_replacements,
                "archive_action": model_actions[0],
                "verification": model_verification,
            },
            "skin": {
                "source_file": source_skin,
                "target_archive": "SKINS.REZ",
                "target_virtual_path": TARGET_SKIN_VPATH,
                "target_row_name": TARGET_SKIN_NAME,
                "source_size": len(skin_bytes),
                "source_sha256": _sha256(skin_bytes),
                "archive_action": skin_actions[0],
                "verification": skin_verification,
            },
        },
        "sound_and_script_policy": {
            "strategy": "inherit-lizardorc-mage-row-script-and-sounds",
            "host_class": HOST_CLASS,
            "host_row": HOST_ROW,
            "actor_host_row": _row_summary(actor_row),
            "monster_host_row": _row_summary(monster_row),
            "script_name": script_name,
            "script_checks": script_checks,
            "foot_sound": foot_sound,
            "footstep_checks": footstep_checks,
            "inherited_animation_sounds": inherited_anim_sounds,
            "inherited_death_sounds": inherited_death_sounds,
            "lomm_orc_sound_sources_available_but_not_copied": sorted(set(lomm_orc_sound_sources)),
            "note": (
                "Stage 2 copies only model/skin assets. The first LoMMOrcMage "
                "runtime test should inherit LizardOrcMage script/combat/death "
                "sound behavior unless the selected runtime row deliberately opts into LoMM Orc sounds."
            ),
        },
        "validation_errors": validation_errors,
    }

    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    log_path = os.path.join(output_dir, "asset_batch_log.txt")
    with open(log_path, "w", encoding="utf-8") as fh:
        for action in model_actions + skin_actions:
            fh.write(f"{action['action']} {action['virtual_path']} {action['size']} bytes\n")
        for error in validation_errors:
            fh.write(f"ERROR {error}\n")

    return {
        "output_dir": output_dir,
        "manifest": manifest_path,
        "log": log_path,
        "status": manifest["status"],
        "validation_errors": validation_errors,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the Stage 2 LoMM Orc model/skin asset output batch."
    )
    parser.add_argument("--mm9-root", required=True, help="Path to the MM9 install root.")
    parser.add_argument("--lomm-root", required=True, help="Path to the LoMM install root.")
    parser.add_argument("--out", default=DEFAULT_OUT_DIR)
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args(argv)

    result = build_lomm_orc_asset_batch(args.mm9_root, args.lomm_root, args.out)
    if args.print_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"wrote LoMM Orc Stage 2 asset batch: {result['output_dir']}")
        print(f"status: {result['status']}")
        print(f"manifest: {result['manifest']}")
        print(f"log: {result['log']}")
        for error in result["validation_errors"]:
            print(f"validation error: {error}")
    return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
