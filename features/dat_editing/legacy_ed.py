"""Read-only legacy DEdit `.ed` brush scanner.

This covers the older binary brush records found in MM9 prefab `.ed` assets and
the accidentally shipped full-level `.ED` files that wrap the same brush stream
in block tables plus contiguous zlib chunks.  The parser remains
diagnostic-first and reports how much geometry it could recover.
"""

from __future__ import annotations

import math
import os
import struct
import zlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from features.dat_editing import geometry_scene


Vec3 = Tuple[float, float, float]


LEGACY_ED_VERSION = 1249


@dataclass(frozen=True)
class _BrushRecord:
    model: geometry_scene.GeometryModel
    start: int
    end: int


@dataclass(frozen=True)
class _ScanResult:
    records: List[_BrushRecord]
    skipped_ranges: List[Dict[str, int]]
    skipped_candidate_count: int


class LegacyEdParseError(ValueError):
    pass


def load_legacy_ed_geometry_scene(path: str) -> geometry_scene.GeometryScene:
    if not os.path.exists(path):
        raise ValueError(f"legacy ED file was not found: {path}")
    with open(path, "rb") as f:
        data = f.read()
    return legacy_ed_bytes_to_geometry_scene(data, source_path=os.path.abspath(path))


def legacy_ed_bytes_to_geometry_scene(data: bytes, *, source_path: str = "") -> geometry_scene.GeometryScene:
    if len(data) < 4:
        raise LegacyEdParseError("legacy ED file is too short")
    version = _u32(data, 0)
    if version != LEGACY_ED_VERSION:
        raise LegacyEdParseError(f"unsupported legacy ED version {version}; expected {LEGACY_ED_VERSION}")

    wrapper = _try_decompress_full_level_wrapper(data)
    scan_data = wrapper["decompressed"] if wrapper is not None else data
    scan = _scan_brush_records(scan_data)
    records = scan.records
    material_names: Dict[str, str] = {}
    for record in records:
        for face in record.model.faces:
            if face.material_name:
                material_names[face.material_name] = face.material_name
    unknown_ranges = _unknown_ranges(len(scan_data), [(record.start, record.end) for record in records])
    metadata: Dict[str, Any] = {
        "kind": "lithtech_legacy_ed_source_world",
        "format": "ed",
        "version": version,
        "recovered_brush_count": len(records),
        "recovered_polygon_count": sum(len(record.model.faces) for record in records),
        "unknown_ranges": unknown_ranges,
        "skipped_candidate_count": scan.skipped_candidate_count,
        "skipped_range_count": len(scan.skipped_ranges),
        "skipped_ranges": scan.skipped_ranges[:64],
    }
    if wrapper is not None:
        metadata.update({
            "wrapper": "zlib_blocked_full_level",
            "infostring": wrapper["infostring"],
            "block_count": wrapper["block_count"],
            "compressed_payload_offset": wrapper["payload_offset"],
            "compressed_payload_size": wrapper["compressed_payload_size"],
            "decompressed_size": len(scan_data),
            "declared_brush_count": _u32(scan_data, 0) if len(scan_data) >= 4 else None,
        })
    return geometry_scene.GeometryScene(
        source_path=os.path.abspath(source_path) if source_path else "",
        models=[record.model for record in records],
        materials=[
            geometry_scene.GeometryMaterial(name=name, texture_name=name)
            for name in sorted(material_names)
        ],
        metadata=metadata,
    )


def _try_decompress_full_level_wrapper(data: bytes) -> Optional[Dict[str, Any]]:
    if len(data) < 128 or data[4] != 1:
        return None
    info_len = _u32(data, 5)
    info_start = 9
    info_end = info_start + info_len
    if info_len > 4096 or info_end > len(data):
        return None
    infostring = data[info_start:info_end].decode("latin1", errors="replace")
    table_offset = info_end
    while table_offset < len(data) and data[table_offset] == 0:
        table_offset += 1
    if table_offset + 4 > len(data):
        return None
    block_count = _u32(data, table_offset)
    if block_count <= 0 or block_count > 100000:
        return None
    table1_offset = table_offset + 4
    table2_offset = table1_offset + 4 * block_count
    payload_offset = table2_offset + 4 * block_count
    if payload_offset + 6 > len(data):
        return None

    # Older full-level ED files have two block-size tables, then an extra
    # 32-bit field before the first zlib stream.  Subsequent streams are packed
    # contiguously; zlib's eof marker is authoritative for each block length.
    pos = payload_offset + 4
    chunks: List[bytes] = []
    for _ in range(block_count):
        if pos >= len(data):
            return None
        decoder = zlib.decompressobj()
        try:
            chunk = decoder.decompress(data[pos:])
        except zlib.error:
            return None
        if not decoder.eof:
            return None
        used = len(data[pos:]) - len(decoder.unused_data)
        if used <= 0:
            return None
        chunks.append(chunk)
        pos += used
    if pos != len(data):
        return None
    return {
        "infostring": infostring,
        "block_count": block_count,
        "payload_offset": payload_offset,
        "compressed_payload_size": len(data) - payload_offset,
        "decompressed": b"".join(chunks),
    }


def _scan_brush_records(data: bytes) -> _ScanResult:
    records: List[_BrushRecord] = []
    skipped_offsets: List[int] = []
    pos = 4
    while pos < len(data) - 32:
        parsed = _parse_brush_at(data, pos, len(records))
        if parsed is None:
            skipped_offsets.append(pos)
            pos += 1
            continue
        records.append(parsed)
        pos = max(parsed.end, pos + 1)
    return _ScanResult(
        records=records,
        skipped_ranges=_offsets_to_ranges(skipped_offsets),
        skipped_candidate_count=len(skipped_offsets),
    )


def _parse_brush_at(data: bytes, start: int, brush_index: int) -> Optional[_BrushRecord]:
    try:
        if start + 7 > len(data):
            return None
        color = [data[start], data[start + 1], data[start + 2]]
        point_count = _u32(data, start + 3)
        if not (3 <= point_count <= 50000):
            return None
        pos = start + 7
        points: List[Vec3] = []
        for _ in range(point_count):
            if pos + 12 > len(data):
                return None
            point = _vec3(data, pos)
            if not _reasonable_vec3(point):
                return None
            points.append(point)
            pos += 12

        if pos + 4 > len(data):
            return None
        polygon_count = _u32(data, pos)
        pos += 4
        if not (1 <= polygon_count <= 10000):
            return None

        model = geometry_scene.GeometryModel(
            name=f"LegacyBrush{brush_index}",
            points=points,
            extras={
                "source_format": "legacy_ed",
                "brush_index": brush_index,
                "record_start": start,
                "color": color,
            },
        )
        for polygon_index in range(polygon_count):
            face, pos = _parse_polygon(data, pos, polygon_index, point_count)
            if face is None:
                return None
            model.faces.append(face)
            if polygon_index != polygon_count - 1:
                pos = _skip_zero_padding_before_polygon(data, pos)
        if not model.faces:
            return None
        model.extras["record_end"] = pos
        return _BrushRecord(model=model, start=start, end=pos)
    except (struct.error, UnicodeDecodeError, ValueError):
        return None


def _parse_polygon(
    data: bytes,
    start: int,
    polygon_index: int,
    point_count: int,
) -> Tuple[Optional[geometry_scene.GeometryFace], int]:
    pos = start
    if pos + 4 > len(data):
        return None, pos
    vertex_count = _u32(data, pos)
    pos += 4
    if not (3 <= vertex_count <= 64):
        return None, pos
    indices: List[int] = []
    for _ in range(vertex_count):
        if pos + 2 > len(data):
            return None, pos
        index = _u16(data, pos)
        pos += 2
        if index >= point_count:
            return None, pos
        indices.append(index)

    if pos + 16 + 36 + 4 + 2 > len(data):
        return None, pos
    normal = _vec3(data, pos)
    dist = _float(data, pos + 12)
    pos += 16
    uv_o = _vec3(data, pos)
    uv_p = _vec3(data, pos + 12)
    uv_q = _vec3(data, pos + 24)
    pos += 36
    texture_flags = _u32(data, pos)
    pos += 4
    texture_len = _u16(data, pos)
    pos += 2
    if texture_len > 512 or pos + texture_len > len(data):
        return None, pos
    texture_name = data[pos:pos + texture_len].decode("latin1")
    pos += texture_len
    if not texture_name:
        texture_name = "Default"
    face = geometry_scene.GeometryFace(
        vertex_indices=indices,
        material_name=texture_name,
        uv_coords=[None for _ in indices],
        extras={
            "source_format": "legacy_ed",
            "polygon_index": polygon_index,
            "normal": list(normal),
            "dist": dist,
            "uv_o": list(uv_o),
            "uv_p": list(uv_p),
            "uv_q": list(uv_q),
            "texture_flags": texture_flags,
        },
    )
    return face, pos


def _skip_zero_padding_before_polygon(data: bytes, pos: int) -> int:
    skipped = 0
    while skipped < 8 and pos + 4 <= len(data):
        candidate = _u32(data, pos)
        if 3 <= candidate <= 64:
            break
        if data[pos] != 0:
            break
        pos += 1
        skipped += 1
    return pos


def _unknown_ranges(length: int, ranges: Sequence[Tuple[int, int]]) -> List[Dict[str, int]]:
    result: List[Dict[str, int]] = []
    cursor = 4
    for start, end in sorted(ranges):
        if start > cursor:
            result.append({"start": cursor, "end": start, "byte_length": start - cursor})
        cursor = max(cursor, end)
    if cursor < length:
        result.append({"start": cursor, "end": length, "byte_length": length - cursor})
    return result


def _offsets_to_ranges(offsets: Sequence[int]) -> List[Dict[str, int]]:
    if not offsets:
        return []
    ranges: List[Dict[str, int]] = []
    start = prev = int(offsets[0])
    for raw in offsets[1:]:
        value = int(raw)
        if value == prev + 1:
            prev = value
            continue
        ranges.append({"start": start, "end": prev + 1, "byte_length": prev + 1 - start})
        start = prev = value
    ranges.append({"start": start, "end": prev + 1, "byte_length": prev + 1 - start})
    return ranges


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _float(data: bytes, offset: int) -> float:
    return struct.unpack_from("<f", data, offset)[0]


def _vec3(data: bytes, offset: int) -> Vec3:
    return (_float(data, offset), _float(data, offset + 4), _float(data, offset + 8))


def _reasonable_vec3(value: Vec3) -> bool:
    return all(math.isfinite(item) and abs(item) <= 10000000.0 for item in value)
