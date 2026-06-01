"""Structural validation for DAT geometry writer output."""

from __future__ import annotations

import math
import struct
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

import _path_setup  # noqa: F401
import mm9_patch as patcher
from core import bsp


HEADER_SIZE = struct.calcsize("<11I")


@dataclass
class DatGeometryValidationResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    parsed_bsp: Optional[bsp.BspWorld] = None
    object_count: Optional[int] = None

    def raise_for_errors(self) -> None:
        if self.errors:
            raise ValueError("DAT geometry validation failed: " + "; ".join(self.errors))


def validate_geometry_dat(
    data: bytes,
    *,
    expected_object_count: Optional[int] = None,
    required_bsp_names: Optional[Sequence[str]] = None,
    allow_duplicate_model_names: Optional[Iterable[str]] = None,
) -> DatGeometryValidationResult:
    result = DatGeometryValidationResult()
    if len(data) < HEADER_SIZE:
        result.errors.append(f"DAT is too short for a header ({len(data)} bytes)")
        return result

    try:
        header = patcher.Header.parse(data)
    except Exception as exc:
        result.errors.append(str(exc))
        return result

    if not (HEADER_SIZE <= header.obj_pos <= header.ren_pos <= len(data)):
        result.errors.append(
            "header offsets are inconsistent "
            f"(ObjectDataPos={header.obj_pos}, RenderDataPos={header.ren_pos}, size={len(data)})"
        )
        return result

    try:
        objects, obj_end = patcher.parse_objects(data, header.obj_pos)
        result.object_count = len(objects)
        if obj_end != header.ren_pos:
            result.errors.append(
                f"WorldObject section ended at {obj_end}, expected RenderDataPos {header.ren_pos}"
            )
        if expected_object_count is not None and len(objects) != int(expected_object_count):
            result.errors.append(
                f"WorldObject count changed unexpectedly ({len(objects)} != {expected_object_count})"
            )
    except Exception as exc:
        result.errors.append(f"WorldObject section does not parse: {exc}")

    try:
        parsed = bsp.parse(data)
        result.parsed_bsp = parsed
    except Exception as exc:
        result.errors.append(f"BSP section does not parse: {exc}")
        return result

    if parsed.parse_warnings:
        result.warnings.extend(f"BSP parse warning: {warning}" for warning in parsed.parse_warnings[:8])

    _validate_world_model_table(data, header, parsed, result)
    _validate_model_names(parsed, required_bsp_names or [], allow_duplicate_model_names or [], result)
    _validate_model_records(header, parsed, result)
    return result


def _validate_world_model_table(
    data: bytes,
    header: patcher.Header,
    parsed: bsp.BspWorld,
    result: DatGeometryValidationResult,
) -> None:
    table_start = int(parsed.world_model_table_start or 0)
    if not (HEADER_SIZE <= table_start <= header.obj_pos - 4):
        result.errors.append(f"world-model table offset {table_start} is outside the pre-object section")
        return
    declared_count = struct.unpack_from("<I", data, table_start)[0]
    if declared_count < len(parsed.world_models):
        result.errors.append(
            f"world-model table count {declared_count} is smaller than parsed model count {len(parsed.world_models)}"
        )
    elif declared_count > len(parsed.world_models):
        result.warnings.append(
            f"world-model table declares {declared_count} model(s), parser reached {len(parsed.world_models)}"
        )


def _validate_model_names(
    parsed: bsp.BspWorld,
    required_bsp_names: Sequence[str],
    allow_duplicate_model_names: Iterable[str],
    result: DatGeometryValidationResult,
) -> None:
    names = [str(model.name or "") for model in parsed.world_models]
    lower_counts = Counter(name.lower() for name in names)
    allowed = {str(name or "").lower() for name in allow_duplicate_model_names}
    for name, count in sorted(lower_counts.items()):
        if count > 1 and name not in allowed:
            result.errors.append(f"duplicate BSP model name {name!r}")
    existing = {name.lower() for name in names}
    for required in required_bsp_names:
        if str(required or "").lower() not in existing:
            result.errors.append(f"required BSP model {required!r} is missing from output")


def _validate_model_records(
    header: patcher.Header,
    parsed: bsp.BspWorld,
    result: DatGeometryValidationResult,
) -> None:
    starts = []
    for model in parsed.world_models:
        if model.raw_start is None or model.raw_end is None:
            result.errors.append(f"BSP model {model.name!r} has no raw byte range")
            continue
        raw_start = int(model.raw_start)
        raw_end = int(model.raw_end)
        next_item = int(model.next_world_item or 0)
        starts.append(raw_start)
        if not (HEADER_SIZE <= raw_start < raw_end <= header.obj_pos):
            result.errors.append(
                f"BSP model {model.name!r} range {raw_start}..{raw_end} is outside pre-object data"
            )
        if next_item <= raw_start or next_item > header.obj_pos:
            result.errors.append(
                f"BSP model {model.name!r} has invalid NextWorldItem {next_item}"
            )
        if not model.points or not model.polygons:
            result.warnings.append(f"BSP model {model.name!r} has no parsed points or polygons")
        _validate_bounds(model, result)
        _validate_polygon_indices(model, result)

    if starts != sorted(starts):
        result.errors.append("parsed BSP model records are not ordered by file offset")


def _validate_bounds(model: bsp.WorldModelMesh, result: DatGeometryValidationResult) -> None:
    values = [*model.min_box, *model.max_box, *model.translation]
    if any(not math.isfinite(float(value)) for value in values):
        result.errors.append(f"BSP model {model.name!r} has non-finite bounds/translation")
        return
    for axis, (lo, hi) in enumerate(zip(model.min_box, model.max_box)):
        if float(lo) > float(hi):
            result.errors.append(f"BSP model {model.name!r} has inverted bounds on axis {axis}")


def _validate_polygon_indices(model: bsp.WorldModelMesh, result: DatGeometryValidationResult) -> None:
    point_count = len(model.points)
    surface_count = len(model.surfaces)
    for index, polygon in enumerate(model.polygons):
        if len(polygon.vertex_indices) < 3:
            result.errors.append(f"BSP model {model.name!r} polygon {index} has fewer than 3 vertices")
        for vertex_index in polygon.vertex_indices:
            if vertex_index < 0 or vertex_index >= point_count:
                result.errors.append(
                    f"BSP model {model.name!r} polygon {index} references point {vertex_index}"
                )
                return
        if polygon.surface_index < 0 or polygon.surface_index >= surface_count:
            result.errors.append(
                f"BSP model {model.name!r} polygon {index} references surface {polygon.surface_index}"
            )
            return
