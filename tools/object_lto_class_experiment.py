#!/usr/bin/env python3
"""Prepare and validate an experimental object.lto class placement."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _path_setup  # noqa: F401
import mm9_patch as patcher
from catalog.builder import (
    DEFAULT_OBJECT_LTO_DUMP_HELPER,
    _object_lto_default_value,
    generate_object_lto_dump,
    load_object_lto_dump,
)
from core.rezmgr import RezReader, RezWriter, _restype_for_filename


ENTRY_ACTOR = "DATA/ACTOR"
ENTRY_MONSTERS = "DATA/MONSTERS"


def _data_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), "data")


def _archive(root: str, name: str) -> str:
    return os.path.join(_data_dir(root), name)


def _read_rez_text(rez_path: str, vpath: str) -> str:
    with RezReader(rez_path) as reader:
        return reader.extract_to_bytes(vpath).decode("latin-1")


def _table_rows(text: str) -> List[Dict[str, str]]:
    import csv
    from io import StringIO

    return [dict(row) for row in csv.DictReader(StringIO(text), delimiter="\t")]


def _token(value: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _infer_row_for_class(rows: Iterable[Dict[str, str]], class_name: str) -> Optional[Dict[str, str]]:
    target = _token(class_name)
    for row in rows:
        if _token(row.get("Monster Name", "")) == target:
            return row
    for row in rows:
        if _token(row.get("Type/Picture", "")) == target:
            return row
    return None


def _row_by_number(rows: Iterable[Dict[str, str]], row_number: str) -> Optional[Dict[str, str]]:
    for row in rows:
        if str(row.get("Number", "")).strip() == str(row_number):
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
        "BaseName",
        "IsMonster",
    )
    return {key: str(row.get(key, "") or "") for key in keys if key in row}


def _load_dump(
    *,
    object_lto_dump: Optional[str],
    object_lto: Optional[str],
    helper_path: Optional[str],
) -> Dict[str, Any]:
    if object_lto_dump:
        return load_object_lto_dump(object_lto_dump)
    if object_lto:
        return generate_object_lto_dump(object_lto, helper_path=helper_path)
    raise ValueError("pass --object-lto-dump or --object-lto")


def _class_definition_proposal(
    class_name: str,
    parent_class: str,
    parent_info: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "name": class_name,
        "parent": parent_class,
        "declared_properties": [],
        "inherits_property_count": len((parent_info or {}).get("properties") or []),
        "notes": [
            "Minimal proposal: inherit all properties and behavior from parent.",
            "This is not a binary object.lto patch; it is the class shape to validate after object.lto editing exists.",
        ],
    }


def _template_properties(class_info: Dict[str, Any]) -> List[patcher.Property]:
    props: List[patcher.Property] = []
    for prop in class_info.get("properties", []) or []:
        name = prop.get("name")
        type_id = prop.get("type_id")
        if not isinstance(name, str) or not isinstance(type_id, int):
            continue
        if not (0 <= type_id <= 7):
            continue
        props.append(patcher.Property(
            name,
            type_id,
            int(prop.get("flags") or 0),
            _object_lto_default_value(prop),
        ))
    return props


def _set_or_add(obj: patcher.WorldObject, name: str, code: int, value: Any, flags: int = 0) -> None:
    for prop in obj.props:
        if prop.name == name:
            prop.value = value
            prop.dirty = True
            return
    obj.props.append(patcher.Property(name, code, flags, value))


def _placement_object(
    class_info: Dict[str, Any],
    *,
    class_name: str,
    object_name: str,
    pos: Tuple[float, float, float],
    yaw: float,
    filename: str,
    script_name: str,
) -> patcher.WorldObject:
    obj = patcher.WorldObject(class_name, _template_properties(class_info))
    _set_or_add(obj, "Name", 0, object_name)
    _set_or_add(obj, "Pos", 1, tuple(float(v) for v in pos))
    _set_or_add(obj, "Rotation", 7, (0.0, float(yaw), 0.0, 0.0))
    if filename:
        _set_or_add(obj, "Filename", 0, filename)
    if script_name:
        _set_or_add(obj, "ScriptName", 0, script_name)
    return obj


def _world_from_bytes(data: bytes) -> patcher.World:
    fd, path = tempfile.mkstemp(prefix="object_lto_experiment_", suffix=".DAT")
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
    fd, path = tempfile.mkstemp(prefix="object_lto_experiment_out_", suffix=".DAT")
    os.close(fd)
    try:
        world.save(path)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _find_level_entry(reader: RezReader, level_name: str) -> Optional[str]:
    wanted = os.path.splitext(os.path.basename(level_name))[0].upper()
    for vpath in reader.list_paths():
        if not vpath.upper().startswith("WORLDS/"):
            continue
        base = os.path.splitext(os.path.basename(vpath))[0].upper()
        if base == wanted:
            return vpath
    return None


def _write_world_patch(
    *,
    mm9_root: str,
    output_dir: str,
    level_name: str,
    obj: patcher.WorldObject,
) -> Dict[str, Any]:
    worlds_rez = _archive(mm9_root, "WORLDS.REZ")
    output_dir = os.path.abspath(output_dir)
    output_data = os.path.join(output_dir, "data")
    os.makedirs(output_data, exist_ok=True)
    output_rez = os.path.join(output_data, "WORLDS.REZ")

    with RezReader(worlds_rez) as reader:
        vpath = _find_level_entry(reader, level_name)
        if not vpath:
            raise ValueError(f"level {level_name!r} not found in {worlds_rez}")
        world = _world_from_bytes(reader.extract_to_bytes(vpath))

    world.objects.append(obj)
    payload = _world_to_bytes(world)

    with RezWriter(worlds_rez, output_rez) as writer:
        writer.replace(vpath, payload, restype=_restype_for_filename("x.DAT"))
        result = writer.commit()

    changed_name = os.path.basename(vpath)
    if not changed_name.upper().endswith(".DAT"):
        changed_name += ".DAT"
    changed = os.path.join(
        output_dir,
        "changed_entries",
        "WORLDS",
        changed_name,
    )
    os.makedirs(os.path.dirname(changed), exist_ok=True)
    with open(changed, "wb") as fh:
        fh.write(payload)

    return {
        "source_archive": worlds_rez,
        "output_archive": output_rez,
        "entries": [vpath],
        "changed_entries": [changed],
        "log": result.get("log", []),
    }


def build_object_lto_class_experiment(
    *,
    mm9_root: str,
    output_dir: str,
    class_name: str,
    parent_class: str,
    target_row: str,
    level_name: str,
    object_name: str,
    pos: Tuple[float, float, float],
    yaw: float,
    filename: str,
    script_name: str,
    object_lto_dump: Optional[str] = None,
    object_lto: Optional[str] = None,
    helper_path: Optional[str] = None,
) -> Dict[str, Any]:
    dump = _load_dump(
        object_lto_dump=object_lto_dump,
        object_lto=object_lto,
        helper_path=helper_path,
    )
    classes = dump.get("classes") or {}
    class_info = classes.get(class_name)
    parent_info = classes.get(parent_class)

    data_rez = _archive(mm9_root, "DATA.REZ")
    actor_rows = _table_rows(_read_rez_text(data_rez, ENTRY_ACTOR))
    monster_rows = _table_rows(_read_rez_text(data_rez, ENTRY_MONSTERS))
    inferred_actor = _infer_row_for_class(actor_rows, class_name)
    inferred_monster = _infer_row_for_class(monster_rows, class_name)
    parent_actor = _infer_row_for_class(actor_rows, parent_class)
    parent_monster = _infer_row_for_class(monster_rows, parent_class)
    target_actor = _row_by_number(actor_rows, target_row)
    target_monster = _row_by_number(monster_rows, target_row)

    validation_errors: List[str] = []
    if parent_info is None:
        validation_errors.append(f"parent class {parent_class!r} is not present in object.lto dump")
    if target_actor is None:
        validation_errors.append(f"target row {target_row!r} is not present in ACTOR.TXT")
    if target_monster is None:
        validation_errors.append(f"target row {target_row!r} is not present in MONSTERS.TXT")
    if class_info is None:
        validation_errors.append(f"candidate class {class_name!r} is not present in object.lto dump")
    else:
        if class_info.get("hidden_in_dedit"):
            validation_errors.append(f"candidate class {class_name!r} is hidden in DEdit")
        if class_info.get("runtime_loadable") is False:
            validation_errors.append(f"candidate class {class_name!r} is not runtime-loadable")
        hierarchy = class_info.get("hierarchy") or []
        if parent_class not in hierarchy and class_info.get("parent") != parent_class:
            validation_errors.append(
                f"candidate class {class_name!r} does not inherit from {parent_class!r}"
            )

    inferred_row = str((inferred_monster or inferred_actor or {}).get("Number", "")).strip()
    target_matches_table_heuristic = bool(inferred_row and inferred_row == str(target_row))
    candidate_constructor_differs = _constructors_differ(class_info, parent_info)
    if class_info is not None and not inferred_row:
        validation_errors.append(
            f"no ACTOR.TXT/MONSTERS.TXT row appears to select class {class_name!r}"
        )

    status = "ready-to-place" if not validation_errors else "blocked"
    placement: Dict[str, Any] = {
        "status": "not-written",
        "reason": "; ".join(validation_errors) if validation_errors else "",
    }
    archives: List[Dict[str, Any]] = []
    log: List[str] = []

    if status == "ready-to-place" and class_info is not None:
        obj = _placement_object(
            class_info,
            class_name=class_name,
            object_name=object_name,
            pos=pos,
            yaw=yaw,
            filename=filename,
            script_name=script_name,
        )
        world_patch = _write_world_patch(
            mm9_root=mm9_root,
            output_dir=output_dir,
            level_name=level_name,
            obj=obj,
        )
        archives.append({
            "source_archive": world_patch["source_archive"],
            "output_archive": world_patch["output_archive"],
            "entries": world_patch["entries"],
            "kind": "object_lto_class_experiment",
        })
        log.extend(world_patch["log"])
        placement = {
            "status": "written",
            "level": level_name,
            "object_type": class_name,
            "object_name": object_name,
            "pos": list(pos),
            "yaw": yaw,
            "changed_entries": world_patch["changed_entries"],
        }

    report = {
        "version": 1,
        "created_at": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "kind": "object_lto_class_experiment",
        "status": status,
        "mm9_root": os.path.abspath(mm9_root),
        "object_lto": {
            "source_dump": dump.get("source_dump"),
            "object_lto_path": dump.get("object_lto_path"),
            "server_object_version": dump.get("server_object_version"),
            "class_count": dump.get("class_count"),
        },
        "candidate": {
            "class_name": class_name,
            "parent_class": parent_class,
            "class_exists": class_info is not None,
            "parent_exists": parent_info is not None,
            "class_info": _class_summary(class_info),
            "parent_info": _class_summary(parent_info),
            "minimal_class_definition": _class_definition_proposal(
                class_name,
                parent_class,
                parent_info,
            ),
        },
        "actor_row_selection": {
            "target_row": str(target_row),
            "inferred_candidate_actor_row": _row_summary(inferred_actor),
            "inferred_candidate_monster_row": _row_summary(inferred_monster),
            "parent_actor_row": _row_summary(parent_actor),
            "parent_monster_row": _row_summary(parent_monster),
            "target_actor_row": _row_summary(target_actor),
            "target_monster_row": _row_summary(target_monster),
            "target_row_matches_table_name_heuristic": target_matches_table_heuristic,
            "target_row_known_selected_by_candidate_class": False,
            "candidate_constructor_differs_from_parent": candidate_constructor_differs,
            "runtime_note": _runtime_note(
                class_info,
                parent_info,
                inferred_row,
                target_row,
            ),
        },
        "placement": placement,
        "archives": archives,
        "validation_errors": validation_errors,
        "smoke_test_checklist": [
            "Install the output batch into a temporary/restorable MM9 install.",
            "Confirm the class is visible in an object.lto dump from the patched install.",
            "Open the throwaway level in the editor and verify preview model/skins.",
            "Launch the game and load the test level.",
            "Confirm the actor appears, idles, moves, attacks, takes damage, dies, and survives save/load.",
        ],
    }

    os.makedirs(output_dir, exist_ok=True)
    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    log_path = os.path.join(output_dir, "object_lto_class_experiment_log.txt")
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + ("\n" if log else ""))

    report["manifest"] = manifest_path
    report["log"] = log_path
    return report


def _class_summary(class_info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not class_info:
        return None
    return {
        "name": class_info.get("name"),
        "parent": class_info.get("parent"),
        "hierarchy": class_info.get("hierarchy") or [],
        "flags": int(class_info.get("flags") or 0),
        "flag_names": class_info.get("flag_names") or [],
        "hidden_in_dedit": bool(class_info.get("hidden_in_dedit")),
        "runtime_loadable": bool(class_info.get("runtime_loadable", True)),
        "class_object_size": class_info.get("class_object_size"),
        "declared_properties": [
            {
                "name": prop.get("name"),
                "type": prop.get("type"),
                "default_value": prop.get("default_value"),
            }
            for prop in (class_info.get("declared_properties") or [])
        ],
        "property_count": len(class_info.get("properties") or []),
    }


def _runtime_note(
    class_info: Optional[Dict[str, Any]],
    parent_info: Optional[Dict[str, Any]],
    inferred_row: str,
    target_row: str,
) -> str:
    if class_info is None:
        return "Cannot verify runtime row selection because the class is absent from object.lto."
    if not inferred_row:
        return (
            "Class exists, but no actor-table row matches its class/name tokens; "
            "runtime row selection must be verified in game."
        )
    if str(inferred_row) == str(target_row):
        if _constructors_differ(class_info, parent_info):
            return (
                f"Candidate class matches target row {target_row} by table-name heuristic "
                "and has a constructor pointer that differs from the parent. This is a "
                "runtime row-binding candidate, but in-game validation is still required."
            )
        return (
            f"Candidate class matches target row {target_row} by table-name heuristic. "
            "This is not proof of game runtime selection; wrapper classes that inherit "
            "a parent constructor may still use the parent class row."
        )
    return (
        f"Candidate class appears to match row {inferred_row}, not requested row {target_row}; "
        "game runtime behavior must be treated as unknown."
    )


def _constructors_differ(
    class_info: Optional[Dict[str, Any]],
    parent_info: Optional[Dict[str, Any]],
) -> bool:
    if not class_info or not parent_info:
        return False
    class_abi = class_info.get("abi") or {}
    parent_abi = parent_info.get("abi") or {}
    class_key = (
        class_abi.get("construct_fn_module"),
        class_abi.get("construct_fn_rva"),
    )
    parent_key = (
        parent_abi.get("construct_fn_module"),
        parent_abi.get("construct_fn_rva"),
    )
    return bool(class_key[0] or class_key[1]) and class_key != parent_key


def _parse_vec3(value: str) -> Tuple[float, float, float]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected X,Y,Z")
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected numeric X,Y,Z") from exc


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and optionally place an experimental object.lto class."
    )
    parser.add_argument("--mm9-root", required=True, help="Path to the MM9 install root.")
    parser.add_argument("--object-lto-dump")
    parser.add_argument(
        "--object-lto",
        help="Path to object.lto. Defaults to <mm9-root>\\data\\object.lto.",
    )
    parser.add_argument("--object-lto-helper", default=DEFAULT_OBJECT_LTO_DUMP_HELPER)
    parser.add_argument("--class-name", default="LoMMOrcMage")
    parser.add_argument("--parent-class", default="LizardOrcMage")
    parser.add_argument("--target-row", default="121")
    parser.add_argument("--level", default="BOOTCAMP")
    parser.add_argument("--object-name", default="Stage5LoMMOrc1")
    parser.add_argument("--pos", type=_parse_vec3, default=(0.0, 0.0, 0.0))
    parser.add_argument("--yaw", type=float, default=0.0)
    parser.add_argument("--filename", default=r"models\OrcMM9.abc")
    parser.add_argument("--script-name", default="")
    parser.add_argument(
        "--out",
        default=os.path.join(ROOT, "output", "object_lto_class_experiment"),
    )
    args = parser.parse_args(argv)
    object_lto = args.object_lto or os.path.join(
        os.path.abspath(args.mm9_root), "data", "object.lto"
    )

    result = build_object_lto_class_experiment(
        mm9_root=args.mm9_root,
        output_dir=args.out,
        class_name=args.class_name,
        parent_class=args.parent_class,
        target_row=str(args.target_row),
        level_name=args.level,
        object_name=args.object_name,
        pos=args.pos,
        yaw=args.yaw,
        filename=args.filename,
        script_name=args.script_name,
        object_lto_dump=args.object_lto_dump,
        object_lto=object_lto if not args.object_lto_dump else None,
        helper_path=args.object_lto_helper,
    )
    print(f"status: {result['status']}")
    print(f"manifest: {result['manifest']}")
    if result["validation_errors"]:
        print("validation errors:")
        for item in result["validation_errors"]:
            print(f"  - {item}")
    if result["archives"]:
        print("archives:")
        for item in result["archives"]:
            print(f"  - {item['output_archive']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
