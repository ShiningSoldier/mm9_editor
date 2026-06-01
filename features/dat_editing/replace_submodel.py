"""Arbitrary topology replacement for existing additive BSP submodels."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
import mm9_patch as patcher
from core import bsp
from features.dat_editing import bsp_compile, mesh_import


HEADER_SIZE = struct.calcsize("<11I")
Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class ReplacedBspModel:
    name: str
    source_model: bsp.WorldModelMesh
    replacement_model: bsp.WorldModelMesh
    record: bsp_compile.CompiledWorldModelRecord


@dataclass(frozen=True)
class ReplaceSubmodelPlan:
    obj_path: str
    meta_path: str
    models: List[ReplacedBspModel] = field(default_factory=list)


def build_replace_submodel_plan(
    target_bsp: bsp.BspWorld,
    source_dat: bytes,
    obj_path: str,
    meta_path: Optional[str] = None,
    model_names: Optional[Sequence[str]] = None,
) -> ReplaceSubmodelPlan:
    meta_path = meta_path or mesh_import._default_meta_path(obj_path)
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    _validate_source_identity(source_dat, meta)

    material_to_texture = {
        str(item.get("material_name") or ""): str(item.get("texture_name") or "Default")
        for item in meta.get("materials", []) or []
    }
    export_to_dat = meta.get("coordinate_system", {}).get("export_to_dat_matrix")
    if not export_to_dat:
        export_to_dat = _identity_matrix()
    parsed_by_name = {
        _object_key(obj.name): obj
        for obj in mesh_import._parse_obj(obj_path)
    }
    wanted = {str(name or "").lower() for name in model_names or []}
    replacements: List[ReplacedBspModel] = []

    for index, model_meta in enumerate(meta.get("models", []) or []):
        model_name = str(model_meta.get("name") or "")
        if wanted and model_name.lower() not in wanted:
            continue
        source_model = target_bsp.model_by_name(model_name)
        if source_model is None:
            raise ValueError(f"source BSP model {model_name!r} is not present in the target level")
        _validate_replace_target(source_model)
        object_name = _obj_name(model_name, index)
        parsed = parsed_by_name.get(_object_key(object_name))
        if parsed is None:
            raise ValueError(f"OBJ object {object_name!r} for BSP model {model_name!r} was not found")
        replacement_model = mesh_import._parsed_obj_to_mesh(
            parsed,
            model_name,
            material_to_texture,
            export_to_dat,
        )
        replacement_model.raw_start = source_model.raw_start
        replacement_model.raw_end = None
        replacement_model.next_world_item = source_model.next_world_item
        replacement_model.world_bsp_start = None
        replacement_model.world_bsp_end = None
        record = bsp_compile.compile_world_model_record(replacement_model)
        replacements.append(ReplacedBspModel(
            name=model_name,
            source_model=source_model,
            replacement_model=replacement_model,
            record=record,
        ))

    if not replacements:
        raise ValueError("no replaceable BSP submodels were found in the OBJ/metadata pair")
    return ReplaceSubmodelPlan(
        obj_path=os.path.abspath(obj_path),
        meta_path=os.path.abspath(meta_path),
        models=replacements,
    )


def build_preview_bsp(target_bsp: bsp.BspWorld, plans: Sequence[ReplaceSubmodelPlan]) -> bsp.BspWorld:
    replacement_by_name: Dict[str, bsp.WorldModelMesh] = {}
    for plan in plans or []:
        for item in plan.models:
            replacement_by_name[item.name.lower()] = item.replacement_model
    return bsp.BspWorld(
        version=target_bsp.version,
        world_info=target_bsp.world_info,
        obj_pos=target_bsp.obj_pos,
        ren_pos=target_bsp.ren_pos,
        world_model_table_start=target_bsp.world_model_table_start,
        world_models=[
            copy.deepcopy(replacement_by_name.get(model.name.lower(), model))
            for model in target_bsp.world_models
        ],
        parse_warnings=list(getattr(target_bsp, "parse_warnings", []) or []),
    )


def apply_replace_submodel_plans(
    source_dat: bytes,
    bsp_world: bsp.BspWorld,
    plans: Sequence[ReplaceSubmodelPlan],
) -> bytes:
    replacements = [item for plan in plans or [] for item in plan.models]
    if not replacements:
        return source_dat
    by_start = {
        int(item.source_model.raw_start): item
        for item in replacements
        if item.source_model.raw_start is not None and item.source_model.raw_end is not None
    }
    if len(by_start) != len(replacements):
        raise ValueError("replacement BSP models require raw source byte ranges")

    header = patcher.Header.parse(source_dat)
    pre_objects_old = source_dat[HEADER_SIZE:header.obj_pos]
    object_section = source_dat[header.obj_pos:header.ren_pos]
    render_data = source_dat[header.ren_pos:]
    ranges = [
        (
            int(item.source_model.raw_start),
            int(item.source_model.raw_end),
            len(item.record.raw_bytes),
        )
        for item in replacements
    ]
    ranges.sort()

    def transform_pos(old_pos: int) -> int:
        delta = 0
        for start, end, new_len in ranges:
            if old_pos >= end:
                delta += new_len - (end - start)
        return old_pos + delta

    pre_objects_new = bytearray()
    cursor = HEADER_SIZE
    for start, end, _new_len in ranges:
        if start < cursor:
            raise ValueError("overlapping BSP replacement ranges")
        pre_objects_new += source_dat[cursor:start]
        item = by_start[start]
        next_item = transform_pos(int(item.source_model.next_world_item or end))
        pre_objects_new += bsp_compile.patch_next_world_item(item.record, next_item)
        cursor = end
    pre_objects_new += source_dat[cursor:header.obj_pos]

    new_obj_pos = HEADER_SIZE + len(pre_objects_new)
    new_ren_pos = new_obj_pos + len(object_section)
    _patch_shifted_next_pointers(pre_objects_new, bsp_world, replacements, transform_pos)
    _patch_terminal_tail(pre_objects_new, bsp_world, header.obj_pos, new_obj_pos, transform_pos)

    new_header = patcher.Header(header.version, new_obj_pos, new_ren_pos, header.dummy)
    return new_header.pack() + bytes(pre_objects_new) + object_section + render_data


def _patch_shifted_next_pointers(
    pre_objects: bytearray,
    bsp_world: bsp.BspWorld,
    replacements: Sequence[ReplacedBspModel],
    transform_pos,
) -> None:
    replaced_starts = {int(item.source_model.raw_start) for item in replacements}
    for model in getattr(bsp_world, "world_models", []) or []:
        if model.raw_start is None or model.next_world_item is None:
            continue
        if int(model.raw_start) in replaced_starts:
            continue
        new_start = transform_pos(int(model.raw_start))
        rel = new_start - HEADER_SIZE
        if 0 <= rel <= len(pre_objects) - 4:
            struct.pack_into("<I", pre_objects, rel, transform_pos(int(model.next_world_item)))


def _patch_terminal_tail(
    pre_objects: bytearray,
    bsp_world: bsp.BspWorld,
    old_obj_pos: int,
    new_obj_pos: int,
    transform_pos,
) -> None:
    parsed_models = [
        model for model in getattr(bsp_world, "world_models", []) or []
        if model.raw_start is not None and model.next_world_item is not None
    ]
    if not parsed_models:
        return
    last_model = max(parsed_models, key=lambda model: int(model.raw_start))
    tail_old = int(last_model.next_world_item)
    if not (int(last_model.raw_start) < tail_old < old_obj_pos):
        return
    tail_new = transform_pos(tail_old)
    rel = tail_new - HEADER_SIZE
    if 0 <= rel <= len(pre_objects) - 4 and struct.unpack_from("<I", pre_objects, rel)[0] == old_obj_pos:
        struct.pack_into("<I", pre_objects, rel, new_obj_pos)


def _validate_replace_target(model: bsp.WorldModelMesh) -> None:
    name = str(model.name or "").lower()
    if name in {"physicsbsp", "visbsp"}:
        raise ValueError(f"{model.name!r} cannot be replaced by the submodel replacement path")
    if model.is_skybox():
        raise ValueError(f"skybox model {model.name!r} cannot be replaced by this path")
    if model.raw_start is None or model.raw_end is None:
        raise ValueError(f"BSP model {model.name!r} has no raw byte range")


def _validate_source_identity(source_dat: bytes, meta: Dict[str, object]) -> None:
    source = meta.get("source", {}) or {}
    expected = str(source.get("sha256") or "")
    if expected and hashlib.sha256(source_dat).hexdigest().lower() != expected.lower():
        raise ValueError("OBJ metadata source checksum does not match the currently loaded DAT")


def _obj_name(name: str, index: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", str(name or "")).strip("_")
    return cleaned or f"WorldModel_{index}"


def _object_key(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(name or "").lower()).strip("_")


def _identity_matrix() -> List[List[float]]:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
