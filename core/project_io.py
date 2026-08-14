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
8  retired mesh-sidecar imports
9  retired mesh-sidecar collision helper mode
10 retired direct BSP vertex edit operations
11 retired direct BSP submodel replacement operations
12 rejects retired editable mesh-sidecar operations on load/save
13 conversion preview metadata
14 hardened static-prefab anchors and canonical controller templates
15 experimental behavioral-prefab planning operations
16 object-only behavioral prefab materialization and catalog templates
17 linked behavioral graphs and atomic assembly deletion
18 Phase-6 reviewed script sources and generated SCRIPTS.REZ assets
19 behavioral prefab import promoted to supported; experimental flag retired
20 hybrid prefab representations and preview-only ED BSP safety metadata
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


RETIRED_OP_KINDS = {
    "import_mesh_bsp",
    "edit_bsp_vertices",
    "edit_terrain_vertices",
    "replace_bsp_submodel",
}


def _retired_op_error(kind: str) -> ValueError:
    return ValueError(
        f"Project operation {kind!r} belongs to the retired editable mesh-sidecar "
        "workflow. Recreate the work through DAT -> ED reconstruction instead."
    )


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
    if isinstance(op, P.ImportResourcePrefabOp):
        return {
            "op": "import_resource_prefab",
            "template": worldobject_to_dict(op.template),
            "overrides": _overrides_to_json(op.overrides),
            "rude": op.rude,
            "prefab_path": op.prefab_path,
            "candidate_id": op.candidate_id,
            "model_path": op.model_path,
            "skin_paths": list(op.skin_paths),
            "source_fingerprint": op.source_fingerprint,
        }
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
            "placement_anchor": op.placement_anchor,
            "allow_unsafe_visibility": bool(op.allow_unsafe_visibility),
            "worldobject_template": (
                worldobject_to_dict(op.worldobject_template)
                if op.worldobject_template is not None else None
            ),
            "invisiblebrush_template": (
                worldobject_to_dict(op.invisiblebrush_template)
                if op.invisiblebrush_template is not None else None
            ),
            "preview_only": bool(op.preview_only),
        }
    if isinstance(op, P.ImportBehavioralPrefabOp):
        return {
            "op": "import_behavioral_prefab",
            "prefab_path": op.prefab_path,
            "root_name": op.root_name,
            "target_pos": list(op.target_pos),
            "target_yaw": float(op.target_yaw),
            "placement_anchor": op.placement_anchor,
            "source_fingerprint": op.source_fingerprint,
            "external_bindings": dict(op.external_bindings),
            "dependency_decisions": dict(op.dependency_decisions),
            "enabled_capabilities": list(op.enabled_capabilities),
            "class_templates": {
                name: worldobject_to_dict(template)
                for name, template in op.class_templates.items()
            },
            "object_overrides": {
                str(index): _overrides_to_json(values)
                for index, values in op.object_overrides.items()
            },
            "planned_object_names": dict(op.planned_object_names),
            "script_sources": dict(op.script_sources),
            "script_assets": dict(op.script_assets),
            "planner_version": int(op.planner_version),
            "operation_id": op.operation_id,
        }
    if isinstance(op, P.RemoveBehavioralPrefabOp):
        return {
            "op": "remove_behavioral_prefab",
            "operation_id": op.operation_id,
            "root_name": op.root_name,
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
    if kind == "import_resource_prefab":
        return P.ImportResourcePrefabOp(
            template=dict_to_worldobject(d["template"]),
            overrides=d.get("overrides", {}),
            rude=d.get("rude"),
            prefab_path=str(d.get("prefab_path", "")),
            candidate_id=str(d.get("candidate_id", "")),
            model_path=str(d.get("model_path", "")),
            skin_paths=tuple(str(value) for value in d.get("skin_paths", ())),
            source_fingerprint=str(d.get("source_fingerprint", "")),
        )
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
            placement_anchor = str(d.get("placement_anchor", "original_origin")),
            allow_unsafe_visibility = bool(d.get("allow_unsafe_visibility", False)),
            worldobject_template = (
                dict_to_worldobject(d["worldobject_template"])
                if d.get("worldobject_template") is not None else None
            ),
            invisiblebrush_template = (
                dict_to_worldobject(d["invisiblebrush_template"])
                if d.get("invisiblebrush_template") is not None else None
            ),
            preview_only = bool(d.get("preview_only", False)),
        )
    if kind == "import_behavioral_prefab":
        return P.ImportBehavioralPrefabOp(
            prefab_path=str(d["prefab_path"]),
            root_name=str(d["root_name"]),
            target_pos=tuple(float(x) for x in d.get("target_pos", (0.0, 0.0, 0.0))),
            target_yaw=float(d.get("target_yaw", 0.0)),
            placement_anchor=str(d.get("placement_anchor", "bottom_center")),
            source_fingerprint=str(d.get("source_fingerprint", "")),
            external_bindings={
                str(key): str(value)
                for key, value in dict(d.get("external_bindings") or {}).items()
            },
            dependency_decisions={
                str(key): str(value)
                for key, value in dict(d.get("dependency_decisions") or {}).items()
            },
            enabled_capabilities=tuple(
                str(value) for value in d.get("enabled_capabilities", ())
            ),
            class_templates={
                str(name): dict_to_worldobject(value)
                for name, value in dict(d.get("class_templates") or {}).items()
            },
            object_overrides={
                str(index): dict(values)
                for index, values in dict(d.get("object_overrides") or {}).items()
            },
            planned_object_names={
                str(key): str(value)
                for key, value in dict(d.get("planned_object_names") or {}).items()
            },
            script_sources={
                str(key): str(value)
                for key, value in dict(d.get("script_sources") or {}).items()
            },
            script_assets={
                str(key): str(value)
                for key, value in dict(d.get("script_assets") or {}).items()
            },
            planner_version=int(d.get("planner_version", 1)),
            operation_id=str(d.get("operation_id") or ""),
        )
    if kind == "remove_behavioral_prefab":
        return P.RemoveBehavioralPrefabOp(
            operation_id=str(d["operation_id"]),
            root_name=str(d.get("root_name", "")),
        )
    if kind in RETIRED_OP_KINDS:
        raise _retired_op_error(kind)
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
        "conversion_report": copy.deepcopy(L.conversion_report),
        "conversion_stage_dir": L.conversion_stage_dir,
        "preview_actor_visuals": copy.deepcopy(L.preview_actor_visuals),
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
        conversion_report = copy.deepcopy(d.get("conversion_report")),
        conversion_stage_dir = str(d.get("conversion_stage_dir", "") or ""),
        preview_actor_visuals = copy.deepcopy(d.get("preview_actor_visuals") or {}),
    )
    L.ops = [dict_to_op(op) for op in d.get("ops", [])]
    return L


# ─────────────────────────────────────────────────────────────────────────────
# Project save / load
# ─────────────────────────────────────────────────────────────────────────────

FORMAT_VERSION = 20
SUPPORTED_FORMAT_VERSIONS = {
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
}


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
        loaded.conversion_report = copy.deepcopy(L.conversion_report)
        loaded.conversion_stage_dir = L.conversion_stage_dir
        loaded.preview_actor_visuals = copy.deepcopy(L.preview_actor_visuals)

        # Replay ops — deserialised copies replace whatever was on the level
        loaded.ops = L.ops
        loaded.redo_ops.clear()
        log.append(
            f"loaded {loaded.display_name}  ({len(loaded.ops)} pending ops)")

    return log
