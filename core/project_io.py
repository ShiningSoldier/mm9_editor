"""
project_io.py
=============

JSON serialisation / deserialisation for the in-memory Project model.

A .mm9mod file captures:
  - which REZ archive entries are open
  - the full pending op list for each level (including AddOp templates)
  - the project's NPCNbr counter and staging paths

On load the world data is re-fetched from the original source files (only the
ops are stored, not the raw level bytes).  This means the file is *not* fully
self-contained — the source REZ archives must still exist and be
accessible.

Format version history
----------------------
1  initial (this implementation)
2  REZ-backed workflow; backup_path is persisted when present
3  CloneDoorOp pending operations
4  ImportPrefabBspOp pending operations
5  Prefab import collision helper mode
6  Prefab collision helper thickness
7  Prefab collision helper max segment length
8  Blender OBJ mesh imports
9  Blender OBJ collision helper mode
10 BSP vertex edit operations
11 BSP submodel replacement operations
"""

from __future__ import annotations

import copy
import json
import os
import struct
from typing import Any, Dict, List, Optional, Tuple

import _path_setup  # noqa: F401
import mm9_patch as patcher
from core import project as P


# ─────────────────────────────────────────────────────────────────────────────
# Property / WorldObject serialisation
# ─────────────────────────────────────────────────────────────────────────────

def _value_to_json(code: int, value: Any) -> Any:
    """Convert a Property value to a JSON-safe representation."""
    if code in (1, 2, 7):          # vectors and quaternions are lists of floats
        return list(value)
    if code == 5:                  # bool stored as int in MM9
        return bool(value)
    return value                   # str, int, float — already JSON-safe


def _json_to_value(code: int, raw: Any) -> Any:
    """Restore a Property value from its JSON representation."""
    if code in (1, 2, 7):
        return [float(x) for x in raw]
    if code == 5:
        return bool(raw)
    if code in (4, 6):
        return int(raw)
    if code == 3:
        return float(raw)
    return raw                     # str (code 0) passes through unchanged


def prop_to_dict(p: patcher.Property) -> Dict[str, Any]:
    return {
        "name":     p.name,
        "code":     p.code,
        "flags":    p.flags,
        "value":    _value_to_json(p.code, p.value),
        "orig_dlen": p.orig_dlen,
    }


def dict_to_prop(d: Dict[str, Any]) -> patcher.Property:
    code = int(d["code"])
    return patcher.Property(
        name      = d["name"],
        code      = code,
        flags     = int(d.get("flags", 0)),
        value     = _json_to_value(code, d["value"]),
        orig_dlen = d.get("orig_dlen"),
    )


def worldobject_to_dict(obj: patcher.WorldObject) -> Dict[str, Any]:
    return {
        "type_str": obj.type_str,
        "props":    [prop_to_dict(p) for p in obj.props],
    }


def dict_to_worldobject(d: Dict[str, Any]) -> patcher.WorldObject:
    obj = patcher.WorldObject(type_str=d["type_str"])
    obj.props = [dict_to_prop(p) for p in d["props"]]
    return obj


# ─────────────────────────────────────────────────────────────────────────────
# Op serialisation
# ─────────────────────────────────────────────────────────────────────────────

def op_to_dict(op: Any) -> Dict[str, Any]:
    if isinstance(op, P.AddOp):
        return {
            "op":        "add",
            "template":  worldobject_to_dict(op.template),
            "overrides": _overrides_to_json(op.overrides),
            "rude":      op.rude,
        }
    if isinstance(op, P.MoveOp):
        return {
            "op":           "move",
            "target_index": op.target_index,
            "new_pos":      list(op.new_pos),
            "new_rot":      list(op.new_rot) if op.new_rot is not None else None,
        }
    if isinstance(op, P.DeleteOp):
        return {
            "op":           "delete",
            "target_index": op.target_index,
        }
    if isinstance(op, P.EditOp):
        return {
            "op":           "edit",
            "target_index": op.target_index,
            "overrides":    _overrides_to_json(op.overrides),
        }
    if isinstance(op, P.CloneDoorOp):
        return {
            "op":           "clone_door",
            "source_name":  op.source_name,
            "new_name":     op.new_name,
            "target_pos":   list(op.target_pos) if op.target_pos is not None else None,
            "target_yaw":   float(op.target_yaw),
            "include_pair": bool(op.include_pair),
        }
    if isinstance(op, P.ImportPrefabBspOp):
        return {
            "op":            "import_prefab_bsp",
            "prefab_path":   op.prefab_path,
            "new_name":      op.new_name,
            "target_pos":    list(op.target_pos),
            "target_yaw":    float(op.target_yaw),
            "include_roles": list(op.include_roles) if op.include_roles is not None else None,
            "collision_mode": op.collision_mode,
            "collision_thickness": float(op.collision_thickness),
            "collision_segment_length": float(op.collision_segment_length),
        }
    if isinstance(op, P.ImportMeshBspOp):
        return {
            "op":          "import_mesh_bsp",
            "obj_path":    op.obj_path,
            "meta_path":   op.meta_path,
            "new_name":    op.new_name,
            "target_pos":  list(op.target_pos) if op.target_pos is not None else None,
            "target_yaw":  float(op.target_yaw),
            "collision_mode": op.collision_mode,
            "collision_thickness": float(op.collision_thickness),
            "collision_segment_length": float(op.collision_segment_length),
        }
    if isinstance(op, P.EditBspVerticesOp):
        return {
            "op":        "edit_bsp_vertices",
            "obj_path":  op.obj_path,
            "meta_path": op.meta_path,
        }
    if isinstance(op, P.ReplaceBspSubmodelOp):
        return {
            "op":        "replace_bsp_submodel",
            "obj_path":  op.obj_path,
            "meta_path": op.meta_path,
        }
    raise TypeError(f"Unknown op type: {type(op)}")


def _overrides_to_json(overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Overrides values are raw Python (list, int, float, str) — already JSON
    safe, but lists of floats need to be preserved as lists not tuples."""
    result = {}
    for k, v in overrides.items():
        if isinstance(v, (list, tuple)):
            result[k] = list(v)
        else:
            result[k] = v
    return result


def dict_to_op(d: Dict[str, Any]) -> Any:
    kind = d["op"]
    if kind == "add":
        return P.AddOp(
            template  = dict_to_worldobject(d["template"]),
            overrides = d.get("overrides", {}),
            rude      = d.get("rude"),
        )
    if kind == "move":
        new_rot = d.get("new_rot")
        return P.MoveOp(
            target_index = int(d["target_index"]),
            new_pos      = tuple(float(x) for x in d["new_pos"]),
            new_rot      = tuple(float(x) for x in new_rot) if new_rot else None,
        )
    if kind == "delete":
        return P.DeleteOp(target_index=int(d["target_index"]))
    if kind == "edit":
        return P.EditOp(
            target_index = int(d["target_index"]),
            overrides    = d.get("overrides", {}),
        )
    if kind == "clone_door":
        target_pos = d.get("target_pos")
        return P.CloneDoorOp(
            source_name  = str(d["source_name"]),
            new_name     = str(d["new_name"]),
            target_pos   = tuple(float(x) for x in target_pos) if target_pos is not None else None,
            target_yaw   = float(d.get("target_yaw", 0.0)),
            include_pair = bool(d.get("include_pair", True)),
        )
    if kind == "import_prefab_bsp":
        roles = d.get("include_roles")
        return P.ImportPrefabBspOp(
            prefab_path   = str(d["prefab_path"]),
            new_name      = str(d["new_name"]),
            target_pos    = tuple(float(x) for x in d.get("target_pos", (0.0, 0.0, 0.0))),
            target_yaw    = float(d.get("target_yaw", 0.0)),
            include_roles = tuple(str(role) for role in roles) if roles is not None else None,
            collision_mode = str(d.get("collision_mode", "none")),
            collision_thickness = float(d.get("collision_thickness", 8.0)),
            collision_segment_length = float(d.get("collision_segment_length", 512.0)),
        )
    if kind == "import_mesh_bsp":
        target_pos = d.get("target_pos")
        return P.ImportMeshBspOp(
            obj_path   = str(d["obj_path"]),
            meta_path  = str(d.get("meta_path") or ""),
            new_name   = str(d["new_name"]),
            target_pos = tuple(float(x) for x in target_pos) if target_pos is not None else None,
            target_yaw = float(d.get("target_yaw", 0.0)),
            collision_mode = str(d.get("collision_mode", "none")),
            collision_thickness = float(d.get("collision_thickness", 8.0)),
            collision_segment_length = float(d.get("collision_segment_length", 512.0)),
        )
    if kind == "edit_bsp_vertices":
        return P.EditBspVerticesOp(
            obj_path=str(d["obj_path"]),
            meta_path=str(d.get("meta_path") or ""),
        )
    if kind == "replace_bsp_submodel":
        return P.ReplaceBspSubmodelOp(
            obj_path=str(d["obj_path"]),
            meta_path=str(d.get("meta_path") or ""),
        )
    raise ValueError(f"Unknown op kind: {kind!r}")


# ─────────────────────────────────────────────────────────────────────────────
# LevelEdit serialisation  (paths only — world bytes are re-loaded on open)
# ─────────────────────────────────────────────────────────────────────────────

def leveledit_to_dict(L: P.LevelEdit) -> Dict[str, Any]:
    return {
        "path":         L.path,
        "source_kind":  L.source_kind,
        "rez_path":     L.rez_path,
        "rez_vpath":    L.rez_vpath,
        "output":       L.output,
        "backup_path":  L.backup_path,
        "display_name": L.display_name,
        "ops":          [op_to_dict(op) for op in L.ops],
    }


def dict_to_leveledit(d: Dict[str, Any]) -> P.LevelEdit:
    L = P.LevelEdit(
        path         = d["path"],
        source_kind  = d.get("source_kind", "file"),
        rez_path     = d.get("rez_path"),
        rez_vpath    = d.get("rez_vpath"),
        output       = d.get("output"),
        backup_path  = d.get("backup_path"),
        display_name = d.get("display_name", ""),
    )
    L.ops = [dict_to_op(op) for op in d.get("ops", [])]
    return L


# ─────────────────────────────────────────────────────────────────────────────
# Project save / load
# ─────────────────────────────────────────────────────────────────────────────

FORMAT_VERSION = 11
SUPPORTED_FORMAT_VERSIONS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}


def project_to_json(project: P.Project, path: str) -> None:
    """Serialise *project* to a .mm9mod JSON file at *path*."""
    doc = {
        "version":          FORMAT_VERSION,
        "next_npc_nbr":     project.next_npc_nbr,
        "levels": [leveledit_to_dict(L) for L in project.levels],
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)


def project_from_json(path: str, existing_project: P.Project) -> List[str]:
    """Load a .mm9mod file and replay its levels and ops into *existing_project*.

    Returns a list of human-readable log lines (warnings / errors).
    The caller should call ``_set_active(levels[0])`` or similar after this.

    Raises ``ValueError`` on unrecognised format version.
    """
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    version = doc.get("version", 0)
    if version not in SUPPORTED_FORMAT_VERSIONS:
        raise ValueError(
            f"Unsupported .mm9mod version {version} "
            f"(this editor understands version {FORMAT_VERSION})")

    log: List[str] = []

    existing_project.next_npc_nbr    = doc.get("next_npc_nbr", 437)
    for level_dict in doc.get("levels", []):
        L = dict_to_leveledit(level_dict)
        source_kind = L.source_kind

        # Re-load the world from the original source
        try:
            if source_kind == P.SOURCE_REZ:
                loaded = existing_project.add_level_from_rez(
                    L.rez_path, L.rez_vpath)
            else:
                raise ValueError(
                    "loose DAT project sources are no longer supported")
        except Exception as e:
            log.append(
                f"[warn] could not open {L.display_name or L.path}: {e}  "
                f"— ops for this level are skipped")
            continue

        # Restore display name (may differ from the archive entry name)
        if L.display_name:
            loaded.display_name = L.display_name
        if L.output:
            loaded.output = L.output
        if L.backup_path:
            loaded.backup_path = L.backup_path

        # Replay ops — deserialised copies replace whatever was on the level
        loaded.ops = L.ops
        loaded.redo_ops.clear()
        log.append(
            f"loaded {loaded.display_name}  ({len(loaded.ops)} pending ops)")

    return log
