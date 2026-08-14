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

NODE_GROUP = 0
NODE_BRUSH = 1
NODE_OBJECT = 2

_PROP_TYPE_NAMES: Dict[int, str] = {
    0: "string",
    1: "vector",
    2: "color",
    3: "real",
    4: "flags",
    5: "bool",
    6: "longint",
    7: "rotation",
}
_OBJECT_ANCHOR_PROPERTIES = {"Name", "Pos", "Rotation"}
_MAX_OBJECT_PROPERTIES = 512
_MAX_LEGACY_STRING_LENGTH = 4096


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


@dataclass(frozen=True)
class LegacyEdObjectProperty:
    name: str
    type_code: int
    type_name: str
    flags: int
    byte_length: int
    value: Any


@dataclass(frozen=True)
class LegacyEdObjectRecord:
    class_name: str
    properties: Tuple[LegacyEdObjectProperty, ...]
    offset: int
    end: int
    byte_length: int

    def property_value(self, name: str, default: Any = None) -> Any:
        for prop in self.properties:
            if prop.name == name:
                return prop.value
        return default


@dataclass(frozen=True)
class LegacyEdObjectScanReport:
    source_path: str
    version: int
    wrapper: str
    byte_count: int
    scan_byte_count: int
    object_count: int
    property_count: int
    class_counts: Dict[str, int]
    records: Tuple[LegacyEdObjectRecord, ...]
    skipped_candidate_count: int
    skipped_ranges: List[Dict[str, int]]


@dataclass(frozen=True)
class LegacyEdNodeLayoutReport:
    source_path: str
    version: int
    wrapper: str
    byte_count: int
    scan_byte_count: int
    status: str
    header_byte_count: int = 0
    polyhedron_count: int = 0
    surface_count: int = 0
    surface_trailing_field_count: int = 0
    node_layout_kind: str = ""
    node_start: int = 0
    root_child_count: int = 0
    group_child_count: int = 0
    brush_object_count: int = 0
    object_property_count: int = 0
    brush_names: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()


@dataclass(frozen=True)
class LegacyEdNode:
    """One recursively decoded DEdit node container.

    ``brush_index`` is the authoritative link to the polyhedron stream.  A
    brush node's nearest object ancestor is therefore the authored BSP owner;
    flat object-record order and brush display names are not used as guesses.
    """

    node_type: int
    brush_index: Optional[int]
    class_name: str
    properties: Tuple[LegacyEdObjectProperty, ...]
    node_name: str
    children: Tuple["LegacyEdNode", ...]
    offset: int
    end: int

    def property_value(self, name: str, default: Any = None) -> Any:
        wanted = str(name).casefold()
        for prop in self.properties:
            if prop.name.casefold() == wanted:
                return prop.value
        return default


@dataclass(frozen=True)
class LegacyEdAnalysisBundle:
    geometry_scene: geometry_scene.GeometryScene
    object_scan: LegacyEdObjectScanReport
    node_layout: LegacyEdNodeLayoutReport
    node_tree: Optional[LegacyEdNode] = None


@dataclass(frozen=True)
class _ObjectScanResult:
    records: Tuple[LegacyEdObjectRecord, ...]
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


def load_legacy_ed_object_scan_report(path: str) -> LegacyEdObjectScanReport:
    if not os.path.exists(path):
        raise ValueError(f"legacy ED file was not found: {path}")
    with open(path, "rb") as f:
        data = f.read()
    return scan_legacy_ed_object_records(data, source_path=os.path.abspath(path))


def load_legacy_ed_node_layout_report(path: str) -> LegacyEdNodeLayoutReport:
    if not os.path.exists(path):
        raise ValueError(f"legacy ED file was not found: {path}")
    with open(path, "rb") as f:
        data = f.read()
    return scan_legacy_ed_node_layout(data, source_path=os.path.abspath(path))


def load_legacy_ed_analysis_bundle(path: str) -> LegacyEdAnalysisBundle:
    """Read and analyze one legacy ED while sharing decompression and scans."""
    if not os.path.exists(path):
        raise ValueError(f"legacy ED file was not found: {path}")
    absolute = os.path.abspath(path)
    with open(path, "rb") as f:
        data = f.read()
    return analyze_legacy_ed_bytes(data, source_path=absolute)


def analyze_legacy_ed_bytes(data: bytes, *, source_path: str = "") -> LegacyEdAnalysisBundle:
    cache: Dict[str, Any] = {}
    scene = legacy_ed_bytes_to_geometry_scene(
        data,
        source_path=source_path,
        _analysis_cache=cache,
    )
    object_scan = scan_legacy_ed_object_records(
        data,
        source_path=source_path,
        _analysis_cache=cache,
    )
    node_layout = scan_legacy_ed_node_layout(
        data,
        source_path=source_path,
        _analysis_cache=cache,
    )
    try:
        node_tree = parse_legacy_ed_node_tree(
            data,
            source_path=source_path,
            _analysis_cache=cache,
        )
    except LegacyEdParseError:
        # Generated diagnostics and damaged sources still retain the existing
        # geometry/object scan report. Behavioral import will fail closed when
        # the authoritative hierarchy is unavailable.
        node_tree = None
    return LegacyEdAnalysisBundle(
        geometry_scene=scene,
        object_scan=object_scan,
        node_layout=node_layout,
        node_tree=node_tree,
    )


def parse_legacy_ed_node_tree(
    data: bytes,
    *,
    source_path: str = "",
    _analysis_cache: Optional[Dict[str, Any]] = None,
) -> LegacyEdNode:
    """Decode the complete recursive node/container hierarchy of an ED v1249.

    The layout follows DEdit's ``TEDNodeContainer`` structure: child headers
    and child containers precede the current node item.  The four trailing
    file bytes are outside the root container and are deliberately ignored.
    """
    del source_path  # Reserved for richer parse diagnostics.
    if len(data) < 4 or _u32(data, 0) != LEGACY_ED_VERSION:
        version = _u32(data, 0) if len(data) >= 4 else -1
        raise LegacyEdParseError(
            f"unsupported legacy ED version {version}; expected {LEGACY_ED_VERSION}"
        )
    _wrapper, scan_data = _legacy_ed_analysis_scan_data(data, _analysis_cache)
    header_end = _uncompressed_ed_header_end(scan_data)
    payload_start = header_end if header_end is not None else 4
    layout = _parse_polyhedron_layout(scan_data, payload_start)
    if layout is None:
        raise LegacyEdParseError("polyhedron stream did not match ED v1249 layout")
    root, _end = _parse_node_container(
        scan_data,
        int(layout["end"]),
        node_type=NODE_GROUP,
        brush_index=None,
        depth=0,
    )
    return root


def _legacy_ed_analysis_scan_data(
    data: bytes,
    analysis_cache: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], bytes]:
    if analysis_cache is None:
        wrapper = _try_decompress_full_level_wrapper(data)
        return wrapper, wrapper["decompressed"] if wrapper is not None else data
    if "wrapper_ready" not in analysis_cache:
        wrapper = _try_decompress_full_level_wrapper(data)
        analysis_cache["wrapper"] = wrapper
        analysis_cache["scan_data"] = wrapper["decompressed"] if wrapper is not None else data
        analysis_cache["wrapper_ready"] = True
    return analysis_cache.get("wrapper"), analysis_cache["scan_data"]


def scan_legacy_ed_object_records(
    data: bytes,
    *,
    source_path: str = "",
    _analysis_cache: Optional[Dict[str, Any]] = None,
) -> LegacyEdObjectScanReport:
    if len(data) < 4:
        raise LegacyEdParseError("legacy ED file is too short")
    version = _u32(data, 0)
    if version != LEGACY_ED_VERSION:
        raise LegacyEdParseError(f"unsupported legacy ED version {version}; expected {LEGACY_ED_VERSION}")

    wrapper, scan_data = _legacy_ed_analysis_scan_data(data, _analysis_cache)
    scan = _analysis_cache.get("object_scan_result") if _analysis_cache is not None else None
    if scan is None:
        scan = _scan_object_records(scan_data)
        if _analysis_cache is not None:
            _analysis_cache["object_scan_result"] = scan
    records = scan.records
    return LegacyEdObjectScanReport(
        source_path=os.path.abspath(source_path) if source_path else "",
        version=version,
        wrapper="zlib_blocked_full_level" if wrapper is not None else "",
        byte_count=len(data),
        scan_byte_count=len(scan_data),
        object_count=len(records),
        property_count=sum(len(record.properties) for record in records),
        class_counts=_object_class_counts(records),
        records=records,
        skipped_candidate_count=scan.skipped_candidate_count,
        skipped_ranges=scan.skipped_ranges,
    )


def scan_legacy_ed_node_layout(
    data: bytes,
    *,
    source_path: str = "",
    _analysis_cache: Optional[Dict[str, Any]] = None,
) -> LegacyEdNodeLayoutReport:
    """Return an EDUnpacker-style layout audit for legacy ED v1249 data.

    The Pascal EDUnpacker project confirms the structured order used by old ED
    files: header, optional zlib block table, polyhedron/brush records, then
    node containers whose object items use the same property encoding decoded by
    ``scan_legacy_ed_object_records``.  This audit intentionally stays local and
    does not shell out to the external Pascal tool.
    """
    if len(data) < 4:
        raise LegacyEdParseError("legacy ED file is too short")
    version = _u32(data, 0)
    if version != LEGACY_ED_VERSION:
        raise LegacyEdParseError(f"unsupported legacy ED version {version}; expected {LEGACY_ED_VERSION}")

    wrapper, scan_data = _legacy_ed_analysis_scan_data(data, _analysis_cache)
    wrapper_name = "zlib_blocked_full_level" if wrapper is not None else ""
    header_end = 0 if wrapper is not None else _uncompressed_ed_header_end(scan_data)
    payload_start = header_end if header_end is not None else 4
    notes: List[str] = []
    blockers: List[str] = []
    if wrapper is not None:
        notes.append("full-level wrapper decompressed to the inner polyhedron stream")
    elif header_end is None:
        notes.append("no EDUnpacker-style uncompressed header was found; treating bytes as raw brush stream")

    layout = _parse_polyhedron_layout(scan_data, payload_start)
    if layout is None:
        blockers.append("polyhedron stream did not match EDUnpacker ED v1249 layout")
        status = "layout_parse_failed"
        return LegacyEdNodeLayoutReport(
            source_path=os.path.abspath(source_path) if source_path else "",
            version=version,
            wrapper=wrapper_name,
            byte_count=len(data),
            scan_byte_count=len(scan_data),
            status=status,
            header_byte_count=payload_start,
            notes=tuple(notes),
            blockers=tuple(blockers),
        )

    object_scan = _analysis_cache.get("object_scan_result") if _analysis_cache is not None else None
    if object_scan is None:
        object_scan = _scan_object_records(scan_data)
        if _analysis_cache is not None:
            _analysis_cache["object_scan_result"] = object_scan
    brush_records = [
        record for record in object_scan.records
        if record.class_name == "Brush"
    ]
    node_kind, root_children, group_children = _classify_prefab_node_layout(
        scan_data,
        layout["end"],
        len(brush_records),
    )
    if not node_kind and layout["end"] < len(scan_data):
        notes.append("node/container tail is present but does not match known direct-root or named-group prefab layouts")
    elif not node_kind:
        notes.append("no node/container tail follows the polyhedron stream")

    status = "layout_parsed" if not blockers else "layout_parse_failed"
    return LegacyEdNodeLayoutReport(
        source_path=os.path.abspath(source_path) if source_path else "",
        version=version,
        wrapper=wrapper_name,
        byte_count=len(data),
        scan_byte_count=len(scan_data),
        status=status,
        header_byte_count=payload_start,
        polyhedron_count=int(layout["polyhedron_count"]),
        surface_count=int(layout["surface_count"]),
        surface_trailing_field_count=int(layout["surface_trailing_field_count"]),
        node_layout_kind=node_kind,
        node_start=int(layout["end"]),
        root_child_count=root_children,
        group_child_count=group_children,
        brush_object_count=len(brush_records),
        object_property_count=sum(len(record.properties) for record in brush_records),
        brush_names=tuple(
            str(record.property_value("Name", ""))
            for record in brush_records
        ),
        notes=tuple(notes),
        blockers=tuple(blockers),
    )


def format_legacy_ed_node_layout_report(report: LegacyEdNodeLayoutReport) -> str:
    wrapper = f", wrapper={report.wrapper}" if report.wrapper else ""
    lines = [
        (
            f"Legacy ED node layout: status={report.status}, version={report.version}{wrapper}, "
            f"polyhedrons={report.polyhedron_count}, surfaces={report.surface_count}, "
            f"surface_trailing_fields={report.surface_trailing_field_count}"
        )
    ]
    if report.node_layout_kind:
        lines.append(
            "Node layout: "
            f"{report.node_layout_kind}, root_children={report.root_child_count}, "
            f"group_children={report.group_child_count}, brush_objects={report.brush_object_count}"
        )
    if report.brush_names:
        lines.append("Brush names: " + ", ".join(report.brush_names[:16]))
    for note in report.notes:
        lines.append(f"note: {note}")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    return "\n".join(lines)


def format_legacy_ed_object_scan_report(report: LegacyEdObjectScanReport, *, max_records: int = 16) -> str:
    wrapper = f", wrapper={report.wrapper}" if report.wrapper else ""
    lines = [
        (
            f"Legacy ED object scan: objects={report.object_count}, "
            f"properties={report.property_count}, version={report.version}{wrapper}"
        )
    ]
    if report.class_counts:
        class_text = ", ".join(
            f"{name}={count}" for name, count in sorted(report.class_counts.items())
        )
        lines.append(f"Classes: {class_text}")
    for record in report.records[:max_records]:
        name = record.property_value("Name", "")
        filename = record.property_value("Filename", "")
        suffix = ""
        if name:
            suffix += f", Name={name}"
        if filename:
            suffix += f", Filename={filename}"
        lines.append(
            f"- @{record.offset}: {record.class_name} "
            f"props={len(record.properties)} bytes={record.byte_length}{suffix}"
        )
    if len(report.records) > max_records:
        lines.append(f"- ... {len(report.records) - max_records} more object record(s)")
    if report.skipped_candidate_count:
        lines.append(f"Rejected object-like candidates: {report.skipped_candidate_count}")
    return "\n".join(lines)


def legacy_ed_bytes_to_geometry_scene(
    data: bytes,
    *,
    source_path: str = "",
    _analysis_cache: Optional[Dict[str, Any]] = None,
) -> geometry_scene.GeometryScene:
    if len(data) < 4:
        raise LegacyEdParseError("legacy ED file is too short")
    version = _u32(data, 0)
    if version != LEGACY_ED_VERSION:
        raise LegacyEdParseError(f"unsupported legacy ED version {version}; expected {LEGACY_ED_VERSION}")

    wrapper, scan_data = _legacy_ed_analysis_scan_data(data, _analysis_cache)
    scan = _analysis_cache.get("brush_scan_result") if _analysis_cache is not None else None
    if scan is None:
        scan = _scan_brush_records(scan_data)
        if _analysis_cache is not None:
            _analysis_cache["brush_scan_result"] = scan
    object_scan = _analysis_cache.get("object_scan_result") if _analysis_cache is not None else None
    if object_scan is None:
        object_scan = _scan_object_records(scan_data)
        if _analysis_cache is not None:
            _analysis_cache["object_scan_result"] = object_scan
    records = scan.records
    material_names: Dict[str, str] = {}
    for record in records:
        for face in record.model.faces:
            if face.material_name:
                material_names[face.material_name] = face.material_name
    decoded_ranges = (
        [(record.start, record.end) for record in records]
        + [(record.offset, record.end) for record in object_scan.records]
    )
    unknown_ranges = _unknown_ranges(len(scan_data), decoded_ranges)
    metadata: Dict[str, Any] = {
        "kind": "lithtech_legacy_ed_source_world",
        "format": "ed",
        "version": version,
        "recovered_brush_count": len(records),
        "recovered_polygon_count": sum(len(record.model.faces) for record in records),
        "recovered_object_count": len(object_scan.records),
        "recovered_object_property_count": sum(len(record.properties) for record in object_scan.records),
        "object_class_counts": _object_class_counts(object_scan.records),
        "object_records": [_object_record_summary(record) for record in object_scan.records[:64]],
        "object_skipped_candidate_count": object_scan.skipped_candidate_count,
        "object_skipped_range_count": len(object_scan.skipped_ranges),
        "object_skipped_ranges": object_scan.skipped_ranges[:64],
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
    table_offset = info_end + 32
    if table_offset + 8 > len(data):
        return None
    block_count = _u32(data, table_offset)
    if block_count <= 0 or block_count > 100000:
        return None
    max_decomp_block_size = _u32(data, table_offset + 4)
    comp_sizes_offset = table_offset + 8
    decomp_sizes_offset = comp_sizes_offset + 4 * block_count
    payload_offset = decomp_sizes_offset + 4 * block_count
    if payload_offset + 6 > len(data):
        return None

    comp_sizes = [_u32(data, comp_sizes_offset + 4 * index) for index in range(block_count)]
    decomp_sizes = [_u32(data, decomp_sizes_offset + 4 * index) for index in range(block_count)]
    if any(size <= 0 for size in comp_sizes + decomp_sizes):
        return None
    if max_decomp_block_size < max(decomp_sizes):
        return None
    if payload_offset + sum(comp_sizes) != len(data):
        legacy = _try_decompress_legacy_rotated_wrapper(
            data,
            infostring=infostring,
            block_count=block_count,
            table_offset=table_offset,
        )
        if legacy is not None:
            return legacy
        return None

    pos = payload_offset
    chunks: List[bytes] = []
    for comp_size, decomp_size in zip(comp_sizes, decomp_sizes):
        chunk_data = data[pos:pos + comp_size]
        try:
            chunk = zlib.decompress(chunk_data)
        except zlib.error:
            return None
        if len(chunk) != decomp_size:
            return None
        chunks.append(chunk)
        pos += comp_size
    if pos != len(data):
        return None
    return {
        "infostring": infostring,
        "block_count": block_count,
        "max_decomp_block_size": max_decomp_block_size,
        "payload_offset": payload_offset,
        "compressed_payload_size": len(data) - payload_offset,
        "decompressed": b"".join(chunks),
    }


def _try_decompress_legacy_rotated_wrapper(
    data: bytes,
    *,
    infostring: str,
    block_count: int,
    table_offset: int,
) -> Optional[Dict[str, Any]]:
    """Read early generated wrappers that used a rotated two-table layout."""
    table1_offset = table_offset + 4
    table2_offset = table1_offset + 4 * block_count
    payload_offset = table2_offset + 4 * block_count
    if payload_offset + 6 > len(data):
        return None
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


def _scan_object_records(data: bytes) -> _ObjectScanResult:
    records: List[LegacyEdObjectRecord] = []
    skipped_offsets: List[int] = []
    pos = 4
    while pos < len(data) - 12:
        if not _looks_like_object_record_start(data, pos):
            pos += 1
            continue
        parsed = _parse_object_record_at(data, pos)
        if parsed is None:
            skipped_offsets.append(pos)
            pos += 1
            continue
        records.append(parsed)
        pos = max(parsed.end, pos + 1)
    return _ObjectScanResult(
        records=tuple(records),
        skipped_ranges=_offsets_to_ranges(skipped_offsets),
        skipped_candidate_count=len(skipped_offsets),
    )


def _looks_like_object_record_start(data: bytes, start: int) -> bool:
    if start + 10 > len(data):
        return False
    record_len = _u16(data, start)
    end = start + 2 + record_len
    if record_len < 12 or end > len(data):
        return False
    string_len = _u16(data, start + 2)
    string_start = start + 4
    string_end = string_start + string_len
    if string_len <= 0 or string_len > 255 or string_end + 4 > end:
        return False
    try:
        class_name = data[string_start:string_end].decode("latin1")
    except UnicodeDecodeError:
        return False
    return _reasonable_identifier_string(class_name)


def _parse_object_record_at(data: bytes, start: int) -> Optional[LegacyEdObjectRecord]:
    try:
        record_len = _u16(data, start)
        end = start + 2 + record_len
        if record_len < 12 or end > len(data):
            return None
        pos = start + 2
        class_name, pos = _read_prefixed_string(data, pos, max_length=255)
        if not _reasonable_identifier_string(class_name):
            return None
        if pos + 4 > end:
            return None
        property_count = _u32(data, pos)
        pos += 4
        if not (1 <= property_count <= _MAX_OBJECT_PROPERTIES):
            return None

        properties: List[LegacyEdObjectProperty] = []
        for _ in range(property_count):
            parsed = _parse_object_property(data, pos, end)
            if parsed is None:
                return None
            prop, pos = parsed
            properties.append(prop)
        if pos != end:
            return None
        if not _OBJECT_ANCHOR_PROPERTIES.intersection(prop.name for prop in properties):
            return None
        return LegacyEdObjectRecord(
            class_name=class_name,
            properties=tuple(properties),
            offset=start,
            end=end,
            byte_length=end - start,
        )
    except (struct.error, UnicodeDecodeError, ValueError):
        return None


def _parse_object_property(
    data: bytes,
    start: int,
    record_end: int,
) -> Optional[Tuple[LegacyEdObjectProperty, int]]:
    pos = start
    prop_name, pos = _read_prefixed_string(data, pos, max_length=255)
    if not _reasonable_identifier_string(prop_name):
        return None
    if pos + 7 > record_end:
        return None
    type_code = data[pos]
    pos += 1
    if type_code not in _PROP_TYPE_NAMES:
        return None
    flags = _u32(data, pos)
    pos += 4
    prop_len = _u16(data, pos)
    pos += 2
    value_start = pos
    value_end = value_start + prop_len
    if value_end > record_end:
        return None
    value = _decode_object_property_value(data, value_start, prop_len, type_code)
    if value is None:
        return None
    prop = LegacyEdObjectProperty(
        name=prop_name,
        type_code=type_code,
        type_name=_PROP_TYPE_NAMES[type_code],
        flags=flags,
        byte_length=prop_len,
        value=value,
    )
    return prop, value_end


def _parse_node_container(
    data: bytes,
    start: int,
    *,
    node_type: int,
    brush_index: Optional[int],
    depth: int,
) -> Tuple[LegacyEdNode, int]:
    if depth > 256:
        raise LegacyEdParseError("legacy ED node hierarchy exceeds 256 levels")
    if start + 2 > len(data):
        raise LegacyEdParseError("legacy ED node child count is outside the buffer")
    pos = start
    child_count = _u16(data, pos)
    pos += 2
    if child_count > 6553:
        raise LegacyEdParseError(f"unreasonable legacy ED child count {child_count}")

    children: List[LegacyEdNode] = []
    for _ in range(child_count):
        if pos + 4 > len(data):
            raise LegacyEdParseError("legacy ED child type is outside the buffer")
        child_type = _u32(data, pos)
        pos += 4
        if child_type not in {NODE_GROUP, NODE_BRUSH, NODE_OBJECT}:
            raise LegacyEdParseError(f"unknown legacy ED node type {child_type}")
        child_brush_index: Optional[int] = None
        if child_type == NODE_BRUSH:
            if pos + 4 > len(data):
                raise LegacyEdParseError("legacy ED brush index is outside the buffer")
            child_brush_index = _u32(data, pos)
            pos += 4
        child, pos = _parse_node_container(
            data,
            pos,
            node_type=child_type,
            brush_index=child_brush_index,
            depth=depth + 1,
        )
        children.append(child)

    item_start = pos
    if pos + 2 > len(data):
        raise LegacyEdParseError("legacy ED node item is outside the buffer")
    item_size = _u16(data, pos)
    item_end = pos + 2 + item_size
    pos += 2
    if item_end > len(data):
        raise LegacyEdParseError("legacy ED node item payload is outside the buffer")
    try:
        class_name, pos = _read_prefixed_string(data, pos, max_length=255)
        if pos + 4 > item_end:
            raise LegacyEdParseError("legacy ED node property count is outside the item")
        property_count = _u32(data, pos)
        pos += 4
        if property_count > _MAX_OBJECT_PROPERTIES:
            raise LegacyEdParseError(
                f"unreasonable legacy ED node property count {property_count}"
            )
        properties: List[LegacyEdObjectProperty] = []
        for _ in range(property_count):
            parsed = _parse_object_property(data, pos, item_end)
            if parsed is None:
                raise LegacyEdParseError("invalid legacy ED node property")
            prop, pos = parsed
            properties.append(prop)
        if pos != item_end:
            raise LegacyEdParseError(
                f"legacy ED node item size mismatch ({pos - item_start} != {item_size + 2})"
            )
        if pos + 8 > len(data):
            raise LegacyEdParseError("legacy ED node metadata is outside the buffer")
        pos += 8  # two preserved-but-uninterpreted DEdit metadata fields
        node_name, pos = _read_prefixed_string(data, pos, max_length=4096)
    except (ValueError, struct.error) as exc:
        if isinstance(exc, LegacyEdParseError):
            raise
        raise LegacyEdParseError(f"invalid legacy ED node item: {exc}") from exc

    return LegacyEdNode(
        node_type=int(node_type),
        brush_index=brush_index,
        class_name=class_name,
        properties=tuple(properties),
        node_name=node_name,
        children=tuple(children),
        offset=start,
        end=pos,
    ), pos


def _decode_object_property_value(data: bytes, start: int, byte_length: int, type_code: int) -> Any:
    if type_code == 0:
        value, end = _read_prefixed_string(data, start, max_length=_MAX_LEGACY_STRING_LENGTH)
        if end != start + byte_length or not _reasonable_payload_string(value):
            return None
        return value
    if type_code in (1, 2):
        if byte_length != 12:
            return None
        value = _vec3(data, start)
        if not _reasonable_vec3(value):
            return None
        return value
    if type_code in (3, 4, 6):
        if byte_length != 4:
            return None
        value = _float(data, start)
        return value if math.isfinite(value) else None
    if type_code == 5:
        if byte_length != 1:
            return None
        return bool(data[start])
    if type_code == 7:
        if byte_length != 16:
            return None
        value = (
            _float(data, start),
            _float(data, start + 4),
            _float(data, start + 8),
            _float(data, start + 12),
        )
        return value if all(math.isfinite(item) for item in value) else None
    return None


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
    surface_flags: Optional[int] = None
    shade_rgb: Optional[List[int]] = None
    if pos + 7 <= len(data):
        surface_flags = _u32(data, pos)
        shade_rgb = [data[pos + 4], data[pos + 5], data[pos + 6]]
        pos += 7
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
            "surface_flags": surface_flags,
            "shade_rgb": shade_rgb,
        },
    )
    return face, pos


def _uncompressed_ed_header_end(data: bytes) -> Optional[int]:
    if len(data) < 41 or data[4] != 0:
        return None
    info_len = _u32(data, 5)
    header_end = 9 + info_len + 32
    if info_len > _MAX_LEGACY_STRING_LENGTH or header_end > len(data):
        return None
    return header_end


def _parse_polyhedron_layout(data: bytes, start: int) -> Optional[Dict[str, int]]:
    try:
        if start + 4 > len(data):
            return None
        pos = start
        polyhedron_count = _u32(data, pos)
        pos += 4
        if not (0 <= polyhedron_count <= 100000):
            return None
        surface_count = 0
        surface_trailing_field_count = 0
        for _ in range(polyhedron_count):
            if pos + 7 > len(data):
                return None
            pos += 3
            point_count = _u32(data, pos)
            pos += 4
            if not (0 <= point_count <= 50000):
                return None
            point_bytes = point_count * 12
            if pos + point_bytes + 4 > len(data):
                return None
            pos += point_bytes
            surfaces = _u32(data, pos)
            pos += 4
            if not (0 <= surfaces <= 10000):
                return None
            surface_count += surfaces
            for _surface_index in range(surfaces):
                if pos + 4 > len(data):
                    return None
                poly_count = _u32(data, pos)
                pos += 4
                if not (0 <= poly_count <= 64):
                    return None
                poly_bytes = poly_count * 2
                if pos + poly_bytes + 16 + 36 + 4 + 2 > len(data):
                    return None
                pos += poly_bytes
                pos += 16
                pos += 36
                pos += 4
                texture_len = _u16(data, pos)
                pos += 2
                if texture_len > 512 or pos + texture_len > len(data):
                    return None
                pos += texture_len
                if pos + 7 <= len(data):
                    pos += 7
                    surface_trailing_field_count += 1
        return {
            "end": pos,
            "polyhedron_count": polyhedron_count,
            "surface_count": surface_count,
            "surface_trailing_field_count": surface_trailing_field_count,
        }
    except (struct.error, ValueError):
        return None


def _classify_prefab_node_layout(
    data: bytes,
    node_start: int,
    brush_object_count: int,
) -> Tuple[str, int, int]:
    if node_start + 2 > len(data):
        return "", 0, 0
    root_children = _u16(data, node_start)
    if root_children <= 0:
        return "", root_children, 0
    if (
        brush_object_count > 0
        and root_children == brush_object_count
        and node_start + 6 <= len(data)
        and _u32(data, node_start + 2) == 1
    ):
        return "direct_root_brush_nodes", root_children, 0
    if (
        brush_object_count > 0
        and root_children >= 1
        and node_start + 8 <= len(data)
        and _u32(data, node_start + 2) == 0
    ):
        group_children = _u16(data, node_start + 6)
        if group_children == brush_object_count:
            if root_children > 1:
                return "named_group_brush_nodes_with_root_objects", root_children, group_children
            return "named_group_brush_nodes", root_children, group_children
        return "named_group_candidate", root_children, group_children
    return "", root_children, 0


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


def _object_class_counts(records: Sequence[LegacyEdObjectRecord]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        counts[record.class_name] = counts.get(record.class_name, 0) + 1
    return counts


def _object_record_summary(record: LegacyEdObjectRecord) -> Dict[str, Any]:
    summary_props: Dict[str, Any] = {}
    for name in ("Name", "Pos", "Rotation", "Filename", "Skin"):
        value = record.property_value(name)
        if value is not None:
            summary_props[name] = list(value) if isinstance(value, tuple) else value
    return {
        "offset": record.offset,
        "end": record.end,
        "byte_length": record.byte_length,
        "class_name": record.class_name,
        "property_count": len(record.properties),
        "properties": summary_props,
    }


def _read_prefixed_string(data: bytes, offset: int, *, max_length: int) -> Tuple[str, int]:
    if offset + 2 > len(data):
        raise ValueError("string length is outside the buffer")
    length = _u16(data, offset)
    start = offset + 2
    end = start + length
    if length > max_length or end > len(data):
        raise ValueError("string payload is outside the buffer")
    return data[start:end].decode("latin1"), end


def _reasonable_identifier_string(value: str) -> bool:
    if not value or len(value) > 255 or "\x00" in value:
        return False
    if not (value[0].isalpha() or value[0] == "_"):
        return False
    return all(ch.isalnum() or ch in "_:-." for ch in value)


def _reasonable_payload_string(value: str) -> bool:
    if "\x00" in value:
        return False
    return all(ch in "\r\n\t" or 32 <= ord(ch) <= 126 for ch in value)


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
