"""DAT world-model BSP record inspector and diff helpers.

This module intentionally stays close to the compiled DAT bytes.  The normal
``core.bsp`` parser keeps renderable mesh data and skips several derived BSP
sections; for Terrain*/PhysicsBSP research we also need to know whether those
opaque sections changed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from core import bsp
from features.doors import bsp_writer


DEFAULT_MODEL_NAMES = ("Terrain0", "PhysicsBSP")
Vec3 = Tuple[float, float, float]
ByteRange = Tuple[int, int]


@dataclass(frozen=True)
class TerrainPlaneRelationshipInspection:
    polygon_count: int = 0
    plane_count: int = 0
    reference_mode: str = "empty"
    polygon_records_use_plane_table: bool = False
    distinct_polygon_plane_index_count: int = 0
    zero_plane_index_count: int = 0
    out_of_range_polygon_count: int = 0
    referenced_plane_count: int = 0
    unused_plane_count: int = 0
    direct_plane_match_count: int = 0
    reversed_plane_match_count: int = 0
    plane_mismatch_count: int = 0
    max_normal_delta: float = 0.0
    max_distance_delta: float = 0.0


@dataclass(frozen=True)
class TerrainRenderChunkInspection:
    index: int
    source_node_table_name: str
    terminal: bool
    header_range: ByteRange
    compact_node_range: ByteRange
    bsp_header_range: ByteRange
    bsp_node_range: ByteRange
    bsp_polygon_list_range: ByteRange
    header_flags: Tuple[int, int, int]
    section_size: Vec3
    section_min: Vec3
    compact_node_count: int
    compact_root_index: int
    compact_in_leaf_count: int
    compact_out_leaf_count: int
    compact_valid_tree: bool
    bsp_marker: int
    bsp_depth: int
    bsp_center: Optional[Vec3]
    bsp_node_count: int
    bsp_in_leaf_count: int
    bsp_out_leaf_count: int
    bsp_polygon_list_count: int
    bsp_valid_tree: bool


@dataclass(frozen=True)
class BspRecordInspection:
    name: str
    present: bool
    raw_start: Optional[int] = None
    raw_end: Optional[int] = None
    raw_size: int = 0
    world_bsp_start: Optional[int] = None
    world_bsp_end: Optional[int] = None
    next_world_item: Optional[int] = None
    point_count: int = 0
    polygon_count: int = 0
    surface_count: int = 0
    texture_count: int = 0
    plane_count: int = 0
    lightmapped_polygon_count: int = 0
    lightmap_extra_data_polygon_count: int = 0
    lightmap_extra_data_value_count: int = 0
    lightmap_pixel_count: int = 0
    max_lightmap_width: int = 0
    max_lightmap_height: int = 0
    leaf_count: int = 0
    node_count: int = 0
    user_portal_count: int = 0
    vert_count: int = 0
    total_vis: int = 0
    leaf_list_count: int = 0
    leaf_portal_reference_count: int = 0
    leaf_poly_reference_count: int = 0
    leaf_list_reference_count: int = 0
    bsp_node_root_count: int = 0
    bsp_node_referenced_polygon_count: int = 0
    bsp_node_in_leaf_count: int = 0
    bsp_node_out_leaf_count: int = 0
    bsp_node_valid_tree: bool = False
    physics_block_cell_count: int = 0
    physics_block_record_count: int = 0
    physics_block_dimensions: Tuple[int, int, int] = (0, 0, 0)
    physics_block_cell_size: Optional[Vec3] = None
    physics_block_origin: Optional[Vec3] = None
    physics_block_nonempty_cell_count: int = 0
    physics_block_empty_cell_count: int = 0
    physics_block_max_cell_node_count: int = 0
    physics_block_valid_cell_tree_count: int = 0
    physics_block_invalid_cell_tree_count: int = 0
    physics_block_referenced_node_count: int = 0
    physics_block_duplicate_node_reference_count: int = 0
    physics_block_in_leaf_reference_count: int = 0
    physics_block_out_leaf_reference_count: int = 0
    world_bsp_known_size: int = 0
    trailing_payload_size: int = 0
    terrain_tail_node_count: int = 0
    terrain_tail_root_count: int = 0
    terrain_tail_in_leaf_count: int = 0
    terrain_tail_out_leaf_count: int = 0
    terrain_tail_referenced_polygon_count: int = 0
    terrain_tail_polygon_list_count: int = 0
    terrain_tail_render_payload_size: int = 0
    terrain_tail_valid_tree: bool = False
    terrain_tail_render_compact_node_count: int = 0
    terrain_tail_render_compact_root_index: int = 0
    terrain_tail_render_compact_in_leaf_count: int = 0
    terrain_tail_render_compact_out_leaf_count: int = 0
    terrain_tail_render_compact_valid_tree: bool = False
    terrain_tail_render_bsp_marker: int = 0
    terrain_tail_render_bsp_depth: int = 0
    terrain_tail_render_bsp_center: Optional[Vec3] = None
    terrain_tail_render_bsp_node_count: int = 0
    terrain_tail_render_bsp_in_leaf_count: int = 0
    terrain_tail_render_bsp_out_leaf_count: int = 0
    terrain_tail_render_bsp_polygon_list_count: int = 0
    terrain_tail_render_bsp_valid_tree: bool = False
    terrain_tail_render_chunk_count: int = 0
    terrain_tail_render_terminal_chunk_count: int = 0
    terrain_tail_render_chunk_compact_node_total: int = 0
    terrain_tail_render_chunk_bsp_node_total: int = 0
    terrain_tail_render_chunk_polygon_list_total: int = 0
    terrain_tail_render_chunk_chain_valid: bool = False
    terrain_tail_render_unknown_payload_size: int = 0
    terrain_tail_render_fully_decoded: bool = False
    terrain_tail_render_chunks: List[TerrainRenderChunkInspection] = field(default_factory=list)
    plane_relationship: TerrainPlaneRelationshipInspection = field(
        default_factory=TerrainPlaneRelationshipInspection,
    )
    section_ranges: Dict[str, ByteRange] = field(default_factory=dict)
    min_box: Optional[Vec3] = None
    max_box: Optional[Vec3] = None
    translation: Optional[Vec3] = None
    raw_error: str = ""


@dataclass(frozen=True)
class BspRecordFieldDiff:
    changed_count: int = 0
    total_count: int = 0
    max_delta: float = 0.0
    changed_indices: List[int] = field(default_factory=list)


@dataclass(frozen=True)
class BspRecordDiff:
    name: str
    source: BspRecordInspection
    changed: BspRecordInspection
    comparable: bool
    byte_diff_count: int = 0
    byte_diff_ranges: List[ByteRange] = field(default_factory=list)
    known_field_changed_bytes: int = 0
    unknown_structural_changed_bytes: int = 0
    moved_points: BspRecordFieldDiff = field(default_factory=BspRecordFieldDiff)
    changed_planes: BspRecordFieldDiff = field(default_factory=BspRecordFieldDiff)
    changed_polygon_centers: BspRecordFieldDiff = field(default_factory=BspRecordFieldDiff)
    changed_point_normals: BspRecordFieldDiff = field(default_factory=BspRecordFieldDiff)
    changed_bounds: BspRecordFieldDiff = field(default_factory=BspRecordFieldDiff)
    section_changed_bytes: Dict[str, int] = field(default_factory=dict)
    terrain_render_header_changed_bytes: int = 0
    terrain_render_header_changed_sections: Dict[str, int] = field(default_factory=dict)
    terrain_render_topology_changed_bytes: int = 0
    terrain_render_topology_changed_sections: Dict[str, int] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class BspRecordDiffReport:
    source_model_count: int
    changed_model_count: int
    model_diffs: List[BspRecordDiff]


@dataclass(frozen=True)
class _BspRecordLayout:
    plane_offsets: List[int]
    polygon_offsets: List[Tuple[int, int]]
    polygon_lightmap_sizes: List[Tuple[int, int, int]]
    point_offsets: List[Tuple[int, int]]
    section_ranges: Dict[str, ByteRange]
    leaf_count: int = 0
    node_count: int = 0
    user_portal_count: int = 0
    vert_count: int = 0
    total_vis: int = 0
    leaf_list_count: int = 0
    leaf_portal_reference_count: int = 0
    leaf_poly_reference_count: int = 0
    leaf_list_reference_count: int = 0
    bsp_node_root_count: int = 0
    bsp_node_referenced_polygon_count: int = 0
    bsp_node_in_leaf_count: int = 0
    bsp_node_out_leaf_count: int = 0
    bsp_node_valid_tree: bool = False
    physics_block_cell_count: int = 0
    physics_block_record_count: int = 0
    physics_block_dimensions: Tuple[int, int, int] = (0, 0, 0)
    physics_block_cell_size: Optional[Vec3] = None
    physics_block_origin: Optional[Vec3] = None
    physics_block_nonempty_cell_count: int = 0
    physics_block_empty_cell_count: int = 0
    physics_block_max_cell_node_count: int = 0
    physics_block_valid_cell_tree_count: int = 0
    physics_block_invalid_cell_tree_count: int = 0
    physics_block_referenced_node_count: int = 0
    physics_block_duplicate_node_reference_count: int = 0
    physics_block_in_leaf_reference_count: int = 0
    physics_block_out_leaf_reference_count: int = 0
    world_bsp_known_size: int = 0
    trailing_payload_size: int = 0
    terrain_tail_node_count: int = 0
    terrain_tail_root_count: int = 0
    terrain_tail_in_leaf_count: int = 0
    terrain_tail_out_leaf_count: int = 0
    terrain_tail_referenced_polygon_count: int = 0
    terrain_tail_polygon_list_count: int = 0
    terrain_tail_render_payload_size: int = 0
    terrain_tail_valid_tree: bool = False
    terrain_tail_render_compact_node_count: int = 0
    terrain_tail_render_compact_root_index: int = 0
    terrain_tail_render_compact_in_leaf_count: int = 0
    terrain_tail_render_compact_out_leaf_count: int = 0
    terrain_tail_render_compact_valid_tree: bool = False
    terrain_tail_render_bsp_marker: int = 0
    terrain_tail_render_bsp_depth: int = 0
    terrain_tail_render_bsp_center: Optional[Vec3] = None
    terrain_tail_render_bsp_node_count: int = 0
    terrain_tail_render_bsp_in_leaf_count: int = 0
    terrain_tail_render_bsp_out_leaf_count: int = 0
    terrain_tail_render_bsp_polygon_list_count: int = 0
    terrain_tail_render_bsp_valid_tree: bool = False
    terrain_tail_render_chunk_count: int = 0
    terrain_tail_render_terminal_chunk_count: int = 0
    terrain_tail_render_chunk_compact_node_total: int = 0
    terrain_tail_render_chunk_bsp_node_total: int = 0
    terrain_tail_render_chunk_polygon_list_total: int = 0
    terrain_tail_render_chunk_chain_valid: bool = False
    terrain_tail_render_unknown_payload_size: int = 0
    terrain_tail_render_fully_decoded: bool = False
    terrain_tail_render_chunks: List[TerrainRenderChunkInspection] = field(default_factory=list)
    plane_relationship: TerrainPlaneRelationshipInspection = field(
        default_factory=TerrainPlaneRelationshipInspection,
    )


class _Cursor:
    def __init__(self, data: bytes, pos: int):
        self.data = data
        self.pos = pos

    def u8(self) -> int:
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u16(self) -> int:
        value = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return value

    def u32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def skip(self, count: int) -> None:
        self.pos += int(count)

    def lt_string_u16(self) -> str:
        length = self.u16()
        value = self.data[self.pos:self.pos + length].decode("latin-1", errors="replace")
        self.pos += length
        return value

    def cstring(self) -> str:
        end = self.data.index(b"\x00", self.pos)
        value = self.data[self.pos:end].decode("latin-1", errors="replace")
        self.pos = end + 1
        return value


def inspect_dat(
    dat_bytes: bytes,
    model_names: Sequence[str] = DEFAULT_MODEL_NAMES,
    *,
    parsed_world: Optional[object] = None,
) -> Dict[str, BspRecordInspection]:
    """Return compiled BSP record summaries for selected world-model names."""
    world = parsed_world if parsed_world is not None else bsp.parse(dat_bytes)
    return {
        str(name): _inspect_model(dat_bytes, world, str(name))
        for name in model_names
    }


def diff_dat_records(
    source_dat: bytes,
    changed_dat: bytes,
    model_names: Sequence[str] = DEFAULT_MODEL_NAMES,
    *,
    max_ranges: int = 24,
    max_indices: int = 32,
    epsilon: float = 1.0e-4,
) -> BspRecordDiffReport:
    """Compare selected compiled BSP world-model records in two DAT blobs."""
    source_world = bsp.parse(source_dat)
    changed_world = bsp.parse(changed_dat)
    model_diffs = [
        _diff_model(
            name=str(name),
            source_dat=source_dat,
            changed_dat=changed_dat,
            source_world=source_world,
            changed_world=changed_world,
            max_ranges=max_ranges,
            max_indices=max_indices,
            epsilon=epsilon,
        )
        for name in model_names
    ]
    return BspRecordDiffReport(
        source_model_count=len(source_world.world_models),
        changed_model_count=len(changed_world.world_models),
        model_diffs=model_diffs,
    )


def report_to_dict(report: BspRecordDiffReport) -> Dict[str, object]:
    return asdict(report)


def format_inspection_report(inspections: Dict[str, BspRecordInspection]) -> str:
    lines = ["DAT BSP record inspection"]
    for name, item in inspections.items():
        if not item.present:
            lines.append(f"- {name}: missing")
            continue
        lines.append(
            f"- {name}: raw={_range_text(item.raw_start, item.raw_end)} "
            f"size={item.raw_size} points={item.point_count} "
            f"polygons={item.polygon_count} planes={item.plane_count} "
            f"surfaces={item.surface_count} textures={item.texture_count} "
            f"leaves={item.leaf_count} nodes={item.node_count}"
        )
        if item.lightmapped_polygon_count or item.lightmap_extra_data_polygon_count:
            lines.append(
                "  polygon lightmaps: "
                f"polygons={item.lightmapped_polygon_count}, "
                f"extra_payload_polygons={item.lightmap_extra_data_polygon_count}, "
                f"extra_values={item.lightmap_extra_data_value_count}, "
                f"pixels={item.lightmap_pixel_count}, "
                f"max={item.max_lightmap_width}x{item.max_lightmap_height}"
            )
        if item.section_ranges:
            section_text = ", ".join(
                f"{key}={end - start}"
                for key, (start, end) in item.section_ranges.items()
                if end > start and key in {
                    "leaves",
                    "nodes",
                    "physics_block_table",
                    "trailing_payload",
                    "terrain_tail_nodes",
                    "terrain_tail_polygon_list",
                    "terrain_tail_render_payload",
                    "terrain_tail_render_header",
                    "terrain_tail_render_compact_nodes",
                    "terrain_tail_render_bsp_header",
                    "terrain_tail_render_bsp_nodes",
                    "terrain_tail_render_bsp_polygon_list",
                    "terrain_tail_render_chunks",
                    "terrain_tail_render_unknown_payload",
                }
            )
            if section_text:
                lines.append(f"  decoded sections: {section_text}")
        if item.terrain_tail_node_count:
            lines.append(
                "  terrain tail: "
                f"nodes={item.terrain_tail_node_count}, "
                f"roots={item.terrain_tail_root_count}, "
                f"NODE_IN={item.terrain_tail_in_leaf_count}, "
                f"NODE_OUT={item.terrain_tail_out_leaf_count}, "
                f"node_polygons={item.terrain_tail_referenced_polygon_count}, "
                f"polygon_list={item.terrain_tail_polygon_list_count}, "
                f"render_payload={item.terrain_tail_render_payload_size}, "
                f"valid_tree={item.terrain_tail_valid_tree}"
            )
            if item.terrain_tail_render_compact_node_count:
                lines.append(
                    "  terrain render tail: "
                    f"compact_nodes={item.terrain_tail_render_compact_node_count}, "
                    f"root={item.terrain_tail_render_compact_root_index}, "
                    f"IN={item.terrain_tail_render_compact_in_leaf_count}, "
                    f"OUT={item.terrain_tail_render_compact_out_leaf_count}, "
                    f"valid_tree={item.terrain_tail_render_compact_valid_tree}"
                )
            if item.terrain_tail_render_bsp_node_count:
                center = item.terrain_tail_render_bsp_center or (0.0, 0.0, 0.0)
                lines.append(
                    "  terrain render BSP: "
                    f"marker={item.terrain_tail_render_bsp_marker}, "
                    f"depth={item.terrain_tail_render_bsp_depth}, "
                    f"center=({center[0]:.2f}, {center[1]:.2f}, {center[2]:.2f}), "
                    f"nodes={item.terrain_tail_render_bsp_node_count}, "
                    f"IN={item.terrain_tail_render_bsp_in_leaf_count}, "
                    f"OUT={item.terrain_tail_render_bsp_out_leaf_count}, "
                    f"polygon_list={item.terrain_tail_render_bsp_polygon_list_count}, "
                    f"valid_tree={item.terrain_tail_render_bsp_valid_tree}"
                )
            if item.terrain_tail_render_chunk_count:
                lines.append(
                    "  terrain render chunks: "
                    f"chunks={item.terrain_tail_render_chunk_count}, "
                    f"terminal={item.terrain_tail_render_terminal_chunk_count}, "
                    f"compact_nodes={item.terrain_tail_render_chunk_compact_node_total}, "
                    f"bsp_nodes={item.terrain_tail_render_chunk_bsp_node_total}, "
                    f"polygon_lists={item.terrain_tail_render_chunk_polygon_list_total}, "
                    f"chain_valid={item.terrain_tail_render_chunk_chain_valid}, "
                    f"fully_decoded={item.terrain_tail_render_fully_decoded}, "
                    f"unknown_tail_bytes={item.terrain_tail_render_unknown_payload_size}"
                )
        if item.plane_relationship.polygon_count:
            plane_info = item.plane_relationship
            lines.append(
                "  plane relationship: "
                f"mode={plane_info.reference_mode}, "
                f"polygon_records_use_plane_table={plane_info.polygon_records_use_plane_table}, "
                f"distinct_indices={plane_info.distinct_polygon_plane_index_count}, "
                f"zero_indices={plane_info.zero_plane_index_count}, "
                f"out_of_range={plane_info.out_of_range_polygon_count}"
            )
        if item.node_count:
            lines.append(
                "  BSP node table: "
                f"roots={item.bsp_node_root_count}, "
                f"NODE_IN={item.bsp_node_in_leaf_count}, "
                f"NODE_OUT={item.bsp_node_out_leaf_count}, "
                f"node_polygons={item.bsp_node_referenced_polygon_count}, "
                f"valid_tree={item.bsp_node_valid_tree}"
            )
        if item.physics_block_cell_count:
            cell_size = item.physics_block_cell_size or (0.0, 0.0, 0.0)
            origin = item.physics_block_origin or (0.0, 0.0, 0.0)
            lines.append(
                "  physics block table: "
                f"dims={item.physics_block_dimensions}, "
                f"cell_size=({cell_size[0]:.2f}, {cell_size[1]:.2f}, {cell_size[2]:.2f}), "
                f"origin=({origin[0]:.2f}, {origin[1]:.2f}, {origin[2]:.2f}), "
                f"cells={item.physics_block_cell_count}, "
                f"nonempty={item.physics_block_nonempty_cell_count}, "
                f"compact_nodes={item.physics_block_record_count}, "
                f"max_cell_nodes={item.physics_block_max_cell_node_count}, "
                f"valid_cell_trees={item.physics_block_valid_cell_tree_count}, "
                f"invalid_cell_trees={item.physics_block_invalid_cell_tree_count}, "
                f"referenced_bsp_nodes={item.physics_block_referenced_node_count}, "
                f"duplicate_refs={item.physics_block_duplicate_node_reference_count}"
            )
        if item.raw_error:
            lines.append(f"  raw parse note: {item.raw_error}")
    return "\n".join(lines)


def format_diff_report(report: BspRecordDiffReport) -> str:
    lines = [
        "DAT BSP record diff",
        f"source models={report.source_model_count}, changed models={report.changed_model_count}",
    ]
    for item in report.model_diffs:
        lines.append(f"\n{item.name}:")
        if not item.source.present:
            lines.append("  source: missing")
        if not item.changed.present:
            lines.append("  changed: missing")
        if not item.comparable:
            lines.append("  comparable: no")
            for note in item.notes:
                lines.append(f"  note: {note}")
            continue
        lines.append(
            f"  raw: {_range_text(item.source.raw_start, item.source.raw_end)} "
            f"-> {_range_text(item.changed.raw_start, item.changed.raw_end)}"
        )
        lines.append(
            f"  bytes changed: {item.byte_diff_count} "
            f"(known fields={item.known_field_changed_bytes}, "
            f"unknown/structural={item.unknown_structural_changed_bytes})"
        )
        if item.byte_diff_ranges:
            ranges = ", ".join(f"{start}-{end}" for start, end in item.byte_diff_ranges)
            lines.append(f"  changed raw ranges: {ranges}")
        lines.append(f"  moved points: {_field_text(item.moved_points)}")
        lines.append(f"  changed planes: {_field_text(item.changed_planes)}")
        lines.append(f"  changed polygon centers: {_field_text(item.changed_polygon_centers)}")
        lines.append(f"  changed point normals: {_field_text(item.changed_point_normals)}")
        lines.append(f"  changed bounds/translation: {_field_text(item.changed_bounds)}")
        if item.terrain_render_header_changed_bytes > 0:
            parts = [
                f"{name}={count}"
                for name, count in sorted(item.terrain_render_header_changed_sections.items())
                if count > 0
            ]
            detail = f" ({', '.join(parts)})" if parts else ""
            lines.append(
                "  changed render header bytes: "
                f"{item.terrain_render_header_changed_bytes}{detail}"
            )
        if item.terrain_render_topology_changed_bytes > 0:
            parts = [
                f"{name}={count}"
                for name, count in sorted(item.terrain_render_topology_changed_sections.items())
                if count > 0
            ]
            detail = f" ({', '.join(parts)})" if parts else ""
            lines.append(
                "  changed render topology bytes: "
                f"{item.terrain_render_topology_changed_bytes}{detail}"
            )
        if item.section_changed_bytes:
            interesting = [
                "leaves",
                "nodes",
                "physics_block_table",
                "trailing_payload",
                "terrain_tail_nodes",
                "terrain_tail_polygon_list",
                "terrain_tail_render_payload",
                "terrain_tail_render_header",
                "terrain_tail_render_chunks",
                "terrain_tail_render_unknown_payload",
                "planes",
                "polygons",
                "points",
            ]
            changed_sections = [
                f"{name}={item.section_changed_bytes[name]}"
                for name in interesting
                if item.section_changed_bytes.get(name, 0) > 0
            ]
            if changed_sections:
                lines.append(f"  changed section bytes: {', '.join(changed_sections)}")
        for note in item.notes:
            lines.append(f"  note: {note}")
    return "\n".join(lines)


def _inspect_model(dat_bytes: bytes, world: bsp.BspWorld, name: str) -> BspRecordInspection:
    model = world.model_by_name(name)
    if model is None:
        return BspRecordInspection(name=name, present=False)
    raw = world.raw_model_bytes(dat_bytes, model)
    layout: Optional[_BspRecordLayout] = None
    raw_error = ""
    if raw is not None:
        try:
            layout = _decode_world_bsp_layout(raw, model)
        except Exception as exc:
            raw_error = str(exc)
    else:
        raw_error = "raw bytes unavailable"
    lightmap_sizes = list(layout.polygon_lightmap_sizes) if layout is not None else []
    lightmapped_sizes = [
        (int(width), int(height), int(extra_count))
        for width, height, extra_count in lightmap_sizes
        if int(width) > 0 or int(height) > 0
    ]
    lightmap_extra_sizes = [
        int(extra_count)
        for _width, _height, extra_count in lightmap_sizes
        if int(extra_count) > 0
    ]
    return BspRecordInspection(
        name=model.name,
        present=True,
        raw_start=model.raw_start,
        raw_end=model.raw_end,
        raw_size=len(raw or b""),
        world_bsp_start=model.world_bsp_start,
        world_bsp_end=model.world_bsp_end,
        next_world_item=model.next_world_item,
        point_count=len(model.points),
        polygon_count=len(model.polygons),
        surface_count=len(model.surfaces),
        texture_count=len(model.texture_names),
        plane_count=len(layout.plane_offsets) if layout is not None else 0,
        lightmapped_polygon_count=len(lightmapped_sizes),
        lightmap_extra_data_polygon_count=len(lightmap_extra_sizes),
        lightmap_extra_data_value_count=sum(lightmap_extra_sizes),
        lightmap_pixel_count=sum(width * height for width, height, _extra_count in lightmapped_sizes),
        max_lightmap_width=max((width for width, _height, _extra_count in lightmapped_sizes), default=0),
        max_lightmap_height=max((height for _width, height, _extra_count in lightmapped_sizes), default=0),
        leaf_count=layout.leaf_count if layout is not None else 0,
        node_count=layout.node_count if layout is not None else 0,
        user_portal_count=layout.user_portal_count if layout is not None else 0,
        vert_count=layout.vert_count if layout is not None else 0,
        total_vis=layout.total_vis if layout is not None else 0,
        leaf_list_count=layout.leaf_list_count if layout is not None else 0,
        leaf_portal_reference_count=layout.leaf_portal_reference_count if layout is not None else 0,
        leaf_poly_reference_count=layout.leaf_poly_reference_count if layout is not None else 0,
        leaf_list_reference_count=layout.leaf_list_reference_count if layout is not None else 0,
        bsp_node_root_count=layout.bsp_node_root_count if layout is not None else 0,
        bsp_node_referenced_polygon_count=layout.bsp_node_referenced_polygon_count if layout is not None else 0,
        bsp_node_in_leaf_count=layout.bsp_node_in_leaf_count if layout is not None else 0,
        bsp_node_out_leaf_count=layout.bsp_node_out_leaf_count if layout is not None else 0,
        bsp_node_valid_tree=layout.bsp_node_valid_tree if layout is not None else False,
        physics_block_cell_count=layout.physics_block_cell_count if layout is not None else 0,
        physics_block_record_count=layout.physics_block_record_count if layout is not None else 0,
        physics_block_dimensions=layout.physics_block_dimensions if layout is not None else (0, 0, 0),
        physics_block_cell_size=layout.physics_block_cell_size if layout is not None else None,
        physics_block_origin=layout.physics_block_origin if layout is not None else None,
        physics_block_nonempty_cell_count=layout.physics_block_nonempty_cell_count if layout is not None else 0,
        physics_block_empty_cell_count=layout.physics_block_empty_cell_count if layout is not None else 0,
        physics_block_max_cell_node_count=layout.physics_block_max_cell_node_count if layout is not None else 0,
        physics_block_valid_cell_tree_count=layout.physics_block_valid_cell_tree_count if layout is not None else 0,
        physics_block_invalid_cell_tree_count=layout.physics_block_invalid_cell_tree_count if layout is not None else 0,
        physics_block_referenced_node_count=layout.physics_block_referenced_node_count if layout is not None else 0,
        physics_block_duplicate_node_reference_count=layout.physics_block_duplicate_node_reference_count if layout is not None else 0,
        physics_block_in_leaf_reference_count=layout.physics_block_in_leaf_reference_count if layout is not None else 0,
        physics_block_out_leaf_reference_count=layout.physics_block_out_leaf_reference_count if layout is not None else 0,
        world_bsp_known_size=layout.world_bsp_known_size if layout is not None else 0,
        trailing_payload_size=layout.trailing_payload_size if layout is not None else 0,
        terrain_tail_node_count=layout.terrain_tail_node_count if layout is not None else 0,
        terrain_tail_root_count=layout.terrain_tail_root_count if layout is not None else 0,
        terrain_tail_in_leaf_count=layout.terrain_tail_in_leaf_count if layout is not None else 0,
        terrain_tail_out_leaf_count=layout.terrain_tail_out_leaf_count if layout is not None else 0,
        terrain_tail_referenced_polygon_count=layout.terrain_tail_referenced_polygon_count if layout is not None else 0,
        terrain_tail_polygon_list_count=layout.terrain_tail_polygon_list_count if layout is not None else 0,
        terrain_tail_render_payload_size=layout.terrain_tail_render_payload_size if layout is not None else 0,
        terrain_tail_valid_tree=layout.terrain_tail_valid_tree if layout is not None else False,
        terrain_tail_render_compact_node_count=layout.terrain_tail_render_compact_node_count if layout is not None else 0,
        terrain_tail_render_compact_root_index=layout.terrain_tail_render_compact_root_index if layout is not None else 0,
        terrain_tail_render_compact_in_leaf_count=layout.terrain_tail_render_compact_in_leaf_count if layout is not None else 0,
        terrain_tail_render_compact_out_leaf_count=layout.terrain_tail_render_compact_out_leaf_count if layout is not None else 0,
        terrain_tail_render_compact_valid_tree=layout.terrain_tail_render_compact_valid_tree if layout is not None else False,
        terrain_tail_render_bsp_marker=layout.terrain_tail_render_bsp_marker if layout is not None else 0,
        terrain_tail_render_bsp_depth=layout.terrain_tail_render_bsp_depth if layout is not None else 0,
        terrain_tail_render_bsp_center=layout.terrain_tail_render_bsp_center if layout is not None else None,
        terrain_tail_render_bsp_node_count=layout.terrain_tail_render_bsp_node_count if layout is not None else 0,
        terrain_tail_render_bsp_in_leaf_count=layout.terrain_tail_render_bsp_in_leaf_count if layout is not None else 0,
        terrain_tail_render_bsp_out_leaf_count=layout.terrain_tail_render_bsp_out_leaf_count if layout is not None else 0,
        terrain_tail_render_bsp_polygon_list_count=layout.terrain_tail_render_bsp_polygon_list_count if layout is not None else 0,
        terrain_tail_render_bsp_valid_tree=layout.terrain_tail_render_bsp_valid_tree if layout is not None else False,
        terrain_tail_render_chunk_count=layout.terrain_tail_render_chunk_count if layout is not None else 0,
        terrain_tail_render_terminal_chunk_count=layout.terrain_tail_render_terminal_chunk_count if layout is not None else 0,
        terrain_tail_render_chunk_compact_node_total=layout.terrain_tail_render_chunk_compact_node_total if layout is not None else 0,
        terrain_tail_render_chunk_bsp_node_total=layout.terrain_tail_render_chunk_bsp_node_total if layout is not None else 0,
        terrain_tail_render_chunk_polygon_list_total=layout.terrain_tail_render_chunk_polygon_list_total if layout is not None else 0,
        terrain_tail_render_chunk_chain_valid=layout.terrain_tail_render_chunk_chain_valid if layout is not None else False,
        terrain_tail_render_unknown_payload_size=layout.terrain_tail_render_unknown_payload_size if layout is not None else 0,
        terrain_tail_render_fully_decoded=layout.terrain_tail_render_fully_decoded if layout is not None else False,
        terrain_tail_render_chunks=list(layout.terrain_tail_render_chunks) if layout is not None else [],
        plane_relationship=layout.plane_relationship if layout is not None else TerrainPlaneRelationshipInspection(),
        section_ranges=dict(layout.section_ranges) if layout is not None else {},
        min_box=tuple(model.min_box),
        max_box=tuple(model.max_box),
        translation=tuple(model.translation),
        raw_error=raw_error,
    )


def _diff_model(
    *,
    name: str,
    source_dat: bytes,
    changed_dat: bytes,
    source_world: bsp.BspWorld,
    changed_world: bsp.BspWorld,
    max_ranges: int,
    max_indices: int,
    epsilon: float,
) -> BspRecordDiff:
    source_info = _inspect_model(source_dat, source_world, name)
    changed_info = _inspect_model(changed_dat, changed_world, name)
    source_model = source_world.model_by_name(name)
    changed_model = changed_world.model_by_name(name)
    notes: List[str] = []
    if source_model is None or changed_model is None:
        notes.append("model missing in one side")
        return BspRecordDiff(name=name, source=source_info, changed=changed_info, comparable=False, notes=notes)

    source_raw = source_world.raw_model_bytes(source_dat, source_model)
    changed_raw = changed_world.raw_model_bytes(changed_dat, changed_model)
    if source_raw is None or changed_raw is None:
        notes.append("raw model bytes unavailable")
        return BspRecordDiff(name=name, source=source_info, changed=changed_info, comparable=False, notes=notes)
    if len(source_raw) != len(changed_raw):
        notes.append(f"raw record size changed {len(source_raw)} -> {len(changed_raw)}")
        return BspRecordDiff(name=name, source=source_info, changed=changed_info, comparable=False, notes=notes)

    byte_diff_mask = [left != right for left, right in zip(source_raw, changed_raw)]
    byte_diff_count = sum(1 for changed in byte_diff_mask if changed)
    byte_diff_ranges = _changed_ranges(byte_diff_mask, max_ranges=max_ranges)

    try:
        source_layout = _decode_world_bsp_layout(source_raw, source_model)
        changed_layout = _decode_world_bsp_layout(changed_raw, changed_model)
    except Exception as exc:
        notes.append(f"could not parse raw field offsets: {exc}")
        return BspRecordDiff(
            name=name,
            source=source_info,
            changed=changed_info,
            comparable=True,
            byte_diff_count=byte_diff_count,
            byte_diff_ranges=byte_diff_ranges,
            unknown_structural_changed_bytes=byte_diff_count,
            notes=notes,
        )

    source_offsets = bsp_writer._world_bsp_patch_offsets(source_raw, source_model)
    changed_offsets = bsp_writer._world_bsp_patch_offsets(changed_raw, changed_model)
    known_spans = _known_field_spans(source_offsets)
    known_spans.extend(_terrain_render_header_bound_spans(source_layout, changed_layout))
    known_spans.extend(_terrain_render_topology_spans(source_layout, changed_layout))
    known_mask = _range_mask(len(source_raw), known_spans)
    known_changed = sum(1 for index, changed in enumerate(byte_diff_mask) if changed and known_mask[index])
    unknown_changed = byte_diff_count - known_changed
    section_changed = _section_changed_bytes(
        byte_diff_mask,
        source_layout.section_ranges,
        changed_layout.section_ranges,
    )
    terrain_render_header_changed = _terrain_render_header_changed_sections(section_changed)
    terrain_render_topology_changed = _terrain_render_topology_changed_sections(section_changed)

    point_total = min(len(source_model.points), len(changed_model.points))
    if len(source_model.points) != len(changed_model.points):
        notes.append(f"point count changed {len(source_model.points)} -> {len(changed_model.points)}")
    polygon_total = min(len(source_model.polygons), len(changed_model.polygons))
    if len(source_model.polygons) != len(changed_model.polygons):
        notes.append(f"polygon count changed {len(source_model.polygons)} -> {len(changed_model.polygons)}")

    return BspRecordDiff(
        name=name,
        source=source_info,
        changed=changed_info,
        comparable=True,
        byte_diff_count=byte_diff_count,
        byte_diff_ranges=byte_diff_ranges,
        known_field_changed_bytes=known_changed,
        unknown_structural_changed_bytes=unknown_changed,
        moved_points=_diff_vectors(
            source_model.points[:point_total],
            changed_model.points[:point_total],
            total=len(source_model.points),
            epsilon=epsilon,
            max_indices=max_indices,
        ),
        changed_planes=_diff_planes(
            source_raw,
            changed_raw,
            source_offsets[4],
            changed_offsets[4],
            epsilon=epsilon,
            max_indices=max_indices,
        ),
        changed_polygon_centers=_diff_vectors_at_offsets(
            source_raw,
            changed_raw,
            [entry[0] for entry in source_offsets[6]],
            [entry[0] for entry in changed_offsets[6]],
            total=len(source_model.polygons),
            epsilon=epsilon,
            max_indices=max_indices,
        ),
        changed_point_normals=_diff_vectors_at_offsets(
            source_raw,
            changed_raw,
            [entry[1] for entry in source_layout.point_offsets],
            [entry[1] for entry in changed_layout.point_offsets],
            total=len(source_model.points),
            epsilon=epsilon,
            max_indices=max_indices,
        ),
        changed_bounds=_diff_vectors_at_offsets(
            source_raw,
            changed_raw,
            [source_offsets[1], source_offsets[2], source_offsets[3]],
            [changed_offsets[1], changed_offsets[2], changed_offsets[3]],
            total=3,
            epsilon=epsilon,
            max_indices=max_indices,
        ),
        section_changed_bytes=section_changed,
        terrain_render_header_changed_bytes=sum(terrain_render_header_changed.values()),
        terrain_render_header_changed_sections=terrain_render_header_changed,
        terrain_render_topology_changed_bytes=sum(terrain_render_topology_changed.values()),
        terrain_render_topology_changed_sections=terrain_render_topology_changed,
        notes=notes,
    )


def _known_field_spans(offsets: Tuple[object, ...]) -> List[ByteRange]:
    _name_length_pos, min_box_offset, max_box_offset, translation_offset, plane_offsets, surface_offsets, polygon_offsets, point_offsets = offsets
    spans: List[ByteRange] = [
        (int(min_box_offset), int(min_box_offset) + 12),
        (int(max_box_offset), int(max_box_offset) + 12),
        (int(translation_offset), int(translation_offset) + 12),
    ]
    spans.extend((int(offset), int(offset) + 16) for offset in plane_offsets)
    for _uv_o_offset, _uv_p_offset, _uv_q_offset, plane_index_offset in surface_offsets:
        spans.append((int(plane_index_offset), int(plane_index_offset) + 4))
    for center_offset, surface_index_offset in polygon_offsets:
        spans.append((int(center_offset), int(center_offset) + 12))
        spans.append((int(surface_index_offset), int(surface_index_offset) + 4))
    for point_offset, normal_offset in point_offsets:
        spans.append((int(point_offset), int(point_offset) + 12))
        spans.append((int(normal_offset), int(normal_offset) + 12))
    return spans


def _terrain_render_header_bound_spans(
    source_layout: _BspRecordLayout,
    changed_layout: _BspRecordLayout,
) -> List[ByteRange]:
    spans: List[ByteRange] = []
    names = sorted(
        name
        for name in source_layout.section_ranges
        if name == "terrain_tail_render_header"
        or (name.startswith("terrain_tail_render_chunk_") and name.endswith("_header"))
    )
    for name in names:
        source_range = source_layout.section_ranges.get(name)
        changed_range = changed_layout.section_ranges.get(name)
        if source_range != changed_range or source_range is None:
            continue
        start, end = source_range
        if int(end) - int(start) < 36:
            continue
        spans.append((int(start) + 12, int(start) + 36))
    return spans


def _terrain_render_topology_spans(
    source_layout: _BspRecordLayout,
    changed_layout: _BspRecordLayout,
) -> List[ByteRange]:
    spans: List[ByteRange] = []
    for name in sorted(source_layout.section_ranges):
        if not _is_terrain_render_topology_section(name):
            continue
        source_range = source_layout.section_ranges.get(name)
        changed_range = changed_layout.section_ranges.get(name)
        if source_range != changed_range or source_range is None:
            continue
        spans.append(source_range)
    return spans


def _terrain_render_header_changed_sections(
    section_changed: Dict[str, int],
) -> Dict[str, int]:
    return {
        name: int(count)
        for name, count in section_changed.items()
        if int(count) > 0
        and (
            name == "terrain_tail_render_header"
            or (name.startswith("terrain_tail_render_chunk_") and name.endswith("_header"))
        )
    }


def _terrain_render_topology_changed_sections(
    section_changed: Dict[str, int],
) -> Dict[str, int]:
    return {
        name: int(count)
        for name, count in section_changed.items()
        if int(count) > 0 and _is_terrain_render_topology_section(name)
    }


def _is_terrain_render_topology_section(name: str) -> bool:
    if name in {
        "terrain_tail_nodes",
        "terrain_tail_polygon_list",
        "terrain_tail_render_compact_nodes",
        "terrain_tail_render_bsp_nodes",
        "terrain_tail_render_bsp_polygon_list",
    }:
        return True
    if not name.startswith("terrain_tail_render_chunk_"):
        return False
    return (
        name.endswith("_compact_nodes")
        or name.endswith("_bsp_nodes")
        or name.endswith("_bsp_polygon_list")
    )


def _decode_bsp_node_table(
    raw: bytes,
    start: int,
    node_count: int,
    polygon_count: int,
) -> Dict[str, object]:
    info: Dict[str, object] = {
        "root_count": 0,
        "referenced_polygon_count": 0,
        "in_leaf_count": 0,
        "out_leaf_count": 0,
        "valid_tree": False,
    }
    if node_count <= 0:
        return info
    end = int(start) + int(node_count) * 14
    if int(start) < 0 or end > len(raw):
        return info

    parents: Dict[int, int] = {}
    referenced_polygons: set[int] = set()
    in_leaf_count = 0
    out_leaf_count = 0
    valid_refs = True
    children_by_node: Dict[int, List[int]] = {}
    for index in range(int(node_count)):
        poly_index, _leaf_index, side0_raw, side1_raw = struct.unpack_from("<I H I I", raw, int(start) + index * 14)
        if int(poly_index) < int(polygon_count):
            referenced_polygons.add(int(poly_index))
        else:
            valid_refs = False
        children: List[int] = []
        for raw_side in (side0_raw, side1_raw):
            side = int(raw_side) if int(raw_side) < 0x80000000 else int(raw_side) - 0x100000000
            if side == -1:
                in_leaf_count += 1
            elif side == -2:
                out_leaf_count += 1
            elif 0 <= side < int(node_count):
                parents[int(side)] = parents.get(int(side), 0) + 1
                children.append(int(side))
            else:
                valid_refs = False
        children_by_node[index] = children

    roots = [index for index in range(int(node_count)) if parents.get(index, 0) == 0]
    valid_tree = (
        valid_refs
        and len(roots) == 1
        and all(count == 1 for count in parents.values())
        and len(_reachable_indices(children_by_node, roots[0])) == int(node_count)
    )
    info.update({
        "root_count": len(roots),
        "referenced_polygon_count": len(referenced_polygons),
        "in_leaf_count": int(in_leaf_count),
        "out_leaf_count": int(out_leaf_count),
        "valid_tree": bool(valid_tree),
    })
    return info


def _decode_physics_block_table(
    cursor: _Cursor,
    raw_len: int,
    source_node_count: int,
) -> Dict[str, object]:
    info: Dict[str, object] = {
        "dimensions": (0, 0, 0),
        "cell_size": None,
        "origin": None,
        "cell_count": 0,
        "nonempty_cell_count": 0,
        "empty_cell_count": 0,
        "compact_node_count": 0,
        "max_cell_node_count": 0,
        "valid_cell_tree_count": 0,
        "invalid_cell_tree_count": 0,
        "referenced_node_count": 0,
        "duplicate_node_reference_count": 0,
        "in_leaf_reference_count": 0,
        "out_leaf_reference_count": 0,
    }
    if cursor.pos + 36 > int(raw_len):
        return info

    start = cursor.pos
    dim_x = cursor.u32()
    dim_y = cursor.u32()
    dim_z = cursor.u32()
    dimensions = (int(dim_x), int(dim_y), int(dim_z))
    cell_size = _read_vec3(cursor.data, cursor.pos)
    cursor.skip(12)
    origin = _read_vec3(cursor.data, cursor.pos)
    cursor.skip(12)
    cell_count = int(dim_x) * int(dim_y) * int(dim_z)
    info.update({
        "dimensions": dimensions,
        "cell_size": cell_size,
        "origin": origin,
        "cell_count": int(cell_count),
    })
    if cell_count <= 0:
        return info
    if cell_count > 1_000_000:
        return info

    nonempty = 0
    empty = 0
    compact_total = 0
    max_cell_nodes = 0
    valid_cells = 0
    invalid_cells = 0
    referenced_nodes: List[int] = []
    in_leaf_refs = 0
    out_leaf_refs = 0

    for _cell_index in range(cell_count):
        if cursor.pos + 4 > int(raw_len):
            invalid_cells += max(0, cell_count - _cell_index)
            cursor.pos = int(raw_len)
            break
        cell_node_count = cursor.u16()
        root_index = cursor.u16()
        entries_start = cursor.pos
        entries_end = entries_start + int(cell_node_count) * 6
        if entries_end > int(raw_len):
            invalid_cells += 1
            cursor.pos = int(raw_len)
            break
        entries: List[Tuple[int, int, int]] = []
        for entry_index in range(int(cell_node_count)):
            source_index, side0, side1 = struct.unpack_from("<HHH", cursor.data, entries_start + entry_index * 6)
            entries.append((int(source_index), int(side0), int(side1)))
        cursor.pos = entries_end

        compact_total += int(cell_node_count)
        max_cell_nodes = max(max_cell_nodes, int(cell_node_count))
        if cell_node_count <= 0:
            empty += 1
            if int(root_index) != 0xFFFF:
                invalid_cells += 1
            continue
        nonempty += 1
        referenced_nodes.extend(entry[0] for entry in entries)
        valid, leaf_counts = _validate_physics_block_compact_tree(
            entries,
            int(root_index),
            int(source_node_count),
        )
        in_leaf_refs += int(leaf_counts[0])
        out_leaf_refs += int(leaf_counts[1])
        if valid:
            valid_cells += 1
        else:
            invalid_cells += 1

    unique_refs = len(set(referenced_nodes))
    info.update({
        "nonempty_cell_count": int(nonempty),
        "empty_cell_count": int(empty),
        "compact_node_count": int(compact_total),
        "max_cell_node_count": int(max_cell_nodes),
        "valid_cell_tree_count": int(valid_cells),
        "invalid_cell_tree_count": int(invalid_cells),
        "referenced_node_count": int(unique_refs),
        "duplicate_node_reference_count": int(compact_total) - int(unique_refs),
        "in_leaf_reference_count": int(in_leaf_refs),
        "out_leaf_reference_count": int(out_leaf_refs),
    })
    return info


def _validate_physics_block_compact_tree(
    entries: Sequence[Tuple[int, int, int]],
    root_index: int,
    source_node_count: int,
) -> Tuple[bool, Tuple[int, int]]:
    if not entries:
        return (root_index == 0xFFFF, (0, 0))
    if not (0 <= int(root_index) < len(entries)):
        return (False, (0, 0))

    parents: Dict[int, int] = {}
    children_by_node: Dict[int, List[int]] = {}
    in_leaf_count = 0
    out_leaf_count = 0
    valid_refs = True
    for index, (source_node_index, side0, side1) in enumerate(entries):
        if not (0 <= int(source_node_index) < int(source_node_count)):
            valid_refs = False
        children: List[int] = []
        for side in (int(side0), int(side1)):
            if side == 0xFFFE:
                in_leaf_count += 1
            elif side == 0xFFFF:
                out_leaf_count += 1
            elif 0 <= side < len(entries):
                parents[side] = parents.get(side, 0) + 1
                children.append(side)
            else:
                valid_refs = False
        children_by_node[index] = children

    roots = [index for index in range(len(entries)) if parents.get(index, 0) == 0]
    valid_tree = (
        valid_refs
        and roots == [int(root_index)]
        and all(count == 1 for count in parents.values())
        and len(_reachable_indices(children_by_node, int(root_index))) == len(entries)
    )
    return (bool(valid_tree), (int(in_leaf_count), int(out_leaf_count)))


def _reachable_indices(
    children_by_node: Dict[int, Sequence[int]],
    root_index: int,
) -> set[int]:
    seen: set[int] = set()
    stack = [int(root_index)]
    while stack:
        index = stack.pop()
        if index in seen:
            continue
        seen.add(index)
        stack.extend(int(child) for child in children_by_node.get(index, ()))
    return seen


def _decode_world_bsp_layout(raw: bytes, source_model: bsp.WorldModelMesh) -> _BspRecordLayout:
    if source_model.raw_start is None or source_model.world_bsp_start is None:
        raise ValueError(f"BSP model {source_model.name!r} has no raw provenance")
    start = source_model.world_bsp_start - source_model.raw_start
    if start < 0 or start >= len(raw):
        raise ValueError(f"BSP model {source_model.name!r} has invalid raw provenance")

    cursor = _Cursor(raw, start)
    sections: Dict[str, ByteRange] = {
        "record_header": (0, start),
    }

    header_start = cursor.pos
    cursor.skip(4)  # info_flags
    cursor.skip(4)  # unknown
    cursor.lt_string_u16()
    point_count = cursor.u32()
    plane_count = cursor.u32()
    surface_count = cursor.u32()
    user_portal_count = cursor.u32()
    poly_count = cursor.u32()
    leaf_count = cursor.u32()
    vert_count = cursor.u32()
    total_vis = cursor.u32()
    leaf_list_count = cursor.u32()
    node_count = cursor.u32()
    cursor.u32()  # unknown_value_2
    cursor.u32()  # unknown_value_3
    sections["header"] = (header_start, cursor.pos)

    bounds_start = cursor.pos
    cursor.skip(36)  # min_box, max_box, translation
    sections["bounds"] = (bounds_start, cursor.pos)

    textures_start = cursor.pos
    cursor.u32()  # name_length
    texture_count = cursor.u32()
    for _ in range(texture_count):
        cursor.cstring()
    sections["textures"] = (textures_start, cursor.pos)

    verts_start = cursor.pos
    verts_per_poly: List[int] = []
    for _ in range(poly_count):
        verts_per_poly.append(cursor.u8() + cursor.u8())
    sections["poly_vertex_counts"] = (verts_start, cursor.pos)

    leaves_start = cursor.pos
    leaf_portal_refs = 0
    leaf_poly_refs = 0
    leaf_list_refs = 0
    for _ in range(leaf_count):
        count = cursor.u16()
        if count == 0xFFFF:
            cursor.u16()
            leaf_list_refs += 1
        else:
            leaf_portal_refs += count
            for _portal_index in range(count):
                cursor.skip(2)
                size = cursor.u16()
                cursor.skip(size)
        leaf_poly_count = cursor.u32()
        leaf_poly_refs += leaf_poly_count
        cursor.skip(leaf_poly_count * 4)
        cursor.u32()
    sections["leaves"] = (leaves_start, cursor.pos)

    planes_start = cursor.pos
    plane_offsets: List[int] = []
    for _ in range(plane_count):
        plane_offsets.append(cursor.pos)
        cursor.skip(16)
    sections["planes"] = (planes_start, cursor.pos)

    surfaces_start = cursor.pos
    surface_offsets: List[Tuple[int, int, int, int]] = []
    for _ in range(surface_count):
        surface_offsets.append(_read_surface_offsets(cursor))
    sections["surfaces"] = (surfaces_start, cursor.pos)

    polygons_start = cursor.pos
    polygon_offsets: List[Tuple[int, int]] = []
    polygon_lightmap_sizes: List[Tuple[int, int, int]] = []
    for vert_count in verts_per_poly:
        center_offset = cursor.pos
        cursor.skip(12)
        lightmap_width = cursor.u16()
        lightmap_height = cursor.u16()
        unknown_flag = cursor.u16()
        polygon_lightmap_sizes.append((
            int(lightmap_width),
            int(lightmap_height),
            int(unknown_flag),
        ))
        if unknown_flag > 0:
            cursor.skip(unknown_flag * 4)
        surface_index_offset = cursor.pos
        cursor.skip(4)  # uint32 surface index; plane reference is stored by the surface.
        polygon_offsets.append((center_offset, surface_index_offset))
        cursor.skip(vert_count * 5)
    sections["polygons"] = (polygons_start, cursor.pos)

    nodes_start = cursor.pos
    bsp_node_info = _decode_bsp_node_table(
        raw,
        nodes_start,
        int(node_count),
        int(poly_count),
    )
    cursor.skip(node_count * 14)
    sections["nodes"] = (nodes_start, cursor.pos)

    portals_start = cursor.pos
    for _ in range(user_portal_count):
        cursor.lt_string_u16()
        cursor.skip(4)
        cursor.skip(4)
        cursor.skip(2)
        cursor.skip(24)
    sections["user_portals"] = (portals_start, cursor.pos)

    points_start = cursor.pos
    point_offsets: List[Tuple[int, int]] = []
    for _ in range(point_count):
        point_offsets.append((cursor.pos, cursor.pos + 12))
        cursor.skip(24)
    sections["points"] = (points_start, cursor.pos)

    pblock_start = cursor.pos
    physics_block_info = _decode_physics_block_table(
        cursor,
        len(raw),
        int(node_count),
    )
    sections["physics_block_table"] = (pblock_start, cursor.pos)

    root_start = cursor.pos
    if cursor.pos + 4 <= len(raw):
        cursor.skip(4)
    sections["root_node_index"] = (root_start, cursor.pos)

    section_count_start = cursor.pos
    if cursor.pos + 4 <= len(raw):
        cursor.skip(4)
    sections["section_count"] = (section_count_start, cursor.pos)

    known_end = cursor.pos
    if source_model.world_bsp_end is not None and source_model.raw_start is not None:
        parsed_end = source_model.world_bsp_end - source_model.raw_start
        if parsed_end > known_end and parsed_end <= len(raw):
            sections["world_bsp_unparsed_tail"] = (known_end, parsed_end)
            known_end = parsed_end
    tail_info = _decode_terrain_tail(raw, known_end, len(raw), int(poly_count))
    if known_end < len(raw):
        sections["trailing_payload"] = (known_end, len(raw))
        sections.update(tail_info["section_ranges"])
    plane_relationship = _inspect_plane_relationship(raw, source_model, plane_offsets)

    return _BspRecordLayout(
        plane_offsets=plane_offsets,
        polygon_offsets=polygon_offsets,
        polygon_lightmap_sizes=polygon_lightmap_sizes,
        point_offsets=point_offsets,
        section_ranges=sections,
        leaf_count=int(leaf_count),
        node_count=int(node_count),
        user_portal_count=int(user_portal_count),
        vert_count=int(vert_count),
        total_vis=int(total_vis),
        leaf_list_count=int(leaf_list_count),
        leaf_portal_reference_count=leaf_portal_refs,
        leaf_poly_reference_count=leaf_poly_refs,
        leaf_list_reference_count=leaf_list_refs,
        bsp_node_root_count=int(bsp_node_info["root_count"]),
        bsp_node_referenced_polygon_count=int(bsp_node_info["referenced_polygon_count"]),
        bsp_node_in_leaf_count=int(bsp_node_info["in_leaf_count"]),
        bsp_node_out_leaf_count=int(bsp_node_info["out_leaf_count"]),
        bsp_node_valid_tree=bool(bsp_node_info["valid_tree"]),
        physics_block_cell_count=int(physics_block_info["cell_count"]),
        physics_block_record_count=int(physics_block_info["compact_node_count"]),
        physics_block_dimensions=physics_block_info["dimensions"],
        physics_block_cell_size=physics_block_info["cell_size"],
        physics_block_origin=physics_block_info["origin"],
        physics_block_nonempty_cell_count=int(physics_block_info["nonempty_cell_count"]),
        physics_block_empty_cell_count=int(physics_block_info["empty_cell_count"]),
        physics_block_max_cell_node_count=int(physics_block_info["max_cell_node_count"]),
        physics_block_valid_cell_tree_count=int(physics_block_info["valid_cell_tree_count"]),
        physics_block_invalid_cell_tree_count=int(physics_block_info["invalid_cell_tree_count"]),
        physics_block_referenced_node_count=int(physics_block_info["referenced_node_count"]),
        physics_block_duplicate_node_reference_count=int(physics_block_info["duplicate_node_reference_count"]),
        physics_block_in_leaf_reference_count=int(physics_block_info["in_leaf_reference_count"]),
        physics_block_out_leaf_reference_count=int(physics_block_info["out_leaf_reference_count"]),
        world_bsp_known_size=known_end - start,
        trailing_payload_size=max(0, len(raw) - known_end),
        terrain_tail_node_count=int(tail_info["node_count"]),
        terrain_tail_root_count=int(tail_info["root_count"]),
        terrain_tail_in_leaf_count=int(tail_info["in_leaf_count"]),
        terrain_tail_out_leaf_count=int(tail_info["out_leaf_count"]),
        terrain_tail_referenced_polygon_count=int(tail_info["referenced_polygon_count"]),
        terrain_tail_polygon_list_count=int(tail_info["polygon_list_count"]),
        terrain_tail_render_payload_size=int(tail_info["render_payload_size"]),
        terrain_tail_valid_tree=bool(tail_info["valid_tree"]),
        terrain_tail_render_compact_node_count=int(tail_info["render_compact_node_count"]),
        terrain_tail_render_compact_root_index=int(tail_info["render_compact_root_index"]),
        terrain_tail_render_compact_in_leaf_count=int(tail_info["render_compact_in_leaf_count"]),
        terrain_tail_render_compact_out_leaf_count=int(tail_info["render_compact_out_leaf_count"]),
        terrain_tail_render_compact_valid_tree=bool(tail_info["render_compact_valid_tree"]),
        terrain_tail_render_bsp_marker=int(tail_info["render_bsp_marker"]),
        terrain_tail_render_bsp_depth=int(tail_info["render_bsp_depth"]),
        terrain_tail_render_bsp_center=tail_info["render_bsp_center"],
        terrain_tail_render_bsp_node_count=int(tail_info["render_bsp_node_count"]),
        terrain_tail_render_bsp_in_leaf_count=int(tail_info["render_bsp_in_leaf_count"]),
        terrain_tail_render_bsp_out_leaf_count=int(tail_info["render_bsp_out_leaf_count"]),
        terrain_tail_render_bsp_polygon_list_count=int(tail_info["render_bsp_polygon_list_count"]),
        terrain_tail_render_bsp_valid_tree=bool(tail_info["render_bsp_valid_tree"]),
        terrain_tail_render_chunk_count=int(tail_info["render_chunk_count"]),
        terrain_tail_render_terminal_chunk_count=int(tail_info["render_terminal_chunk_count"]),
        terrain_tail_render_chunk_compact_node_total=int(tail_info["render_chunk_compact_node_total"]),
        terrain_tail_render_chunk_bsp_node_total=int(tail_info["render_chunk_bsp_node_total"]),
        terrain_tail_render_chunk_polygon_list_total=int(tail_info["render_chunk_polygon_list_total"]),
        terrain_tail_render_chunk_chain_valid=bool(tail_info["render_chunk_chain_valid"]),
        terrain_tail_render_unknown_payload_size=int(tail_info["render_unknown_payload_size"]),
        terrain_tail_render_fully_decoded=bool(tail_info["render_fully_decoded"]),
        terrain_tail_render_chunks=list(tail_info["render_chunks"]),
        plane_relationship=plane_relationship,
    )


def _read_surface_offsets(cursor: _Cursor) -> Tuple[int, int, int, int]:
    uv_o_offset = cursor.pos
    cursor.skip(12)
    uv_p_offset = cursor.pos
    cursor.skip(12)
    uv_q_offset = cursor.pos
    cursor.skip(12)
    cursor.skip(2)
    plane_index_offset = cursor.pos
    cursor.skip(4)
    cursor.skip(4)
    cursor.skip(4)
    use_effects = cursor.u8()
    if use_effects == 1:
        cursor.lt_string_u16()
        cursor.lt_string_u16()
    cursor.skip(2)
    return uv_o_offset, uv_p_offset, uv_q_offset, plane_index_offset


def _decode_terrain_tail(
    raw: bytes,
    start: int,
    end: int,
    polygon_count: int,
) -> Dict[str, object]:
    info: Dict[str, object] = {
        "section_ranges": {},
        "node_count": 0,
        "root_count": 0,
        "in_leaf_count": 0,
        "out_leaf_count": 0,
        "referenced_polygon_count": 0,
        "polygon_list_count": 0,
        "render_payload_size": max(0, int(end) - int(start)),
        "valid_tree": False,
        "render_compact_node_count": 0,
        "render_compact_root_index": 0,
        "render_compact_in_leaf_count": 0,
        "render_compact_out_leaf_count": 0,
        "render_compact_valid_tree": False,
        "render_bsp_marker": 0,
        "render_bsp_depth": 0,
        "render_bsp_center": None,
        "render_bsp_node_count": 0,
        "render_bsp_in_leaf_count": 0,
        "render_bsp_out_leaf_count": 0,
        "render_bsp_polygon_list_count": 0,
        "render_bsp_valid_tree": False,
        "render_chunk_count": 0,
        "render_terminal_chunk_count": 0,
        "render_chunk_compact_node_total": 0,
        "render_chunk_bsp_node_total": 0,
        "render_chunk_polygon_list_total": 0,
        "render_chunk_chain_valid": False,
        "render_unknown_payload_size": 0,
        "render_fully_decoded": False,
        "render_chunks": [],
    }
    if end <= start or start + 4 > end:
        return info

    node_count = struct.unpack_from("<I", raw, start)[0]
    nodes_start = start
    nodes_end = start + 4 + int(node_count) * 12
    if node_count <= 0 or node_count > 1_000_000 or nodes_end > end:
        return info

    nodes: List[Tuple[int, int, int]] = []
    for index in range(int(node_count)):
        poly_index, side0, side1 = struct.unpack_from("<Iii", raw, start + 4 + index * 12)
        if poly_index >= polygon_count:
            return info
        for side in (side0, side1):
            if side not in (-1, -2) and not (0 <= side < int(node_count)):
                return info
        nodes.append((poly_index, side0, side1))

    parents: Dict[int, int] = {}
    in_leaf_count = 0
    out_leaf_count = 0
    for _poly_index, side0, side1 in nodes:
        for side in (side0, side1):
            if side == -1:
                in_leaf_count += 1
            elif side == -2:
                out_leaf_count += 1
            else:
                parents[side] = parents.get(side, 0) + 1
    root_count = sum(1 for index in range(int(node_count)) if parents.get(index, 0) == 0)
    valid_tree = root_count == 1 and all(count == 1 for count in parents.values())

    ranges: Dict[str, ByteRange] = {
        "terrain_tail_nodes": (nodes_start, nodes_end),
    }
    cursor = nodes_end
    polygon_list_count = 0
    if cursor + 8 <= end:
        # MM9 Terrain0 records observed so far carry a zero marker, then a
        # sorted list of polygon indices that are referenced by the tail.
        marker = struct.unpack_from("<I", raw, cursor)[0]
        candidate_count = struct.unpack_from("<I", raw, cursor + 4)[0]
        candidate_end = cursor + 8 + int(candidate_count) * 4
        if marker == 0 and 0 <= candidate_count <= polygon_count and candidate_end <= end:
            values = [
                struct.unpack_from("<I", raw, cursor + 8 + index * 4)[0]
                for index in range(int(candidate_count))
            ]
            if all(value < polygon_count for value in values) and all(
                values[index] <= values[index + 1]
                for index in range(len(values) - 1)
            ):
                polygon_list_count = int(candidate_count)
                ranges["terrain_tail_polygon_list"] = (cursor, candidate_end)
                cursor = candidate_end

    if cursor < end:
        ranges["terrain_tail_render_payload"] = (cursor, end)
        render_info = _decode_terrain_tail_render_payload(
            raw,
            cursor,
            end,
            int(node_count),
        )
        ranges.update(render_info["section_ranges"])
        info.update({
            "render_compact_node_count": int(render_info["compact_node_count"]),
            "render_compact_root_index": int(render_info["compact_root_index"]),
            "render_compact_in_leaf_count": int(render_info["compact_in_leaf_count"]),
            "render_compact_out_leaf_count": int(render_info["compact_out_leaf_count"]),
            "render_compact_valid_tree": bool(render_info["compact_valid_tree"]),
            "render_bsp_marker": int(render_info["bsp_marker"]),
            "render_bsp_depth": int(render_info["bsp_depth"]),
            "render_bsp_center": render_info["bsp_center"],
            "render_bsp_node_count": int(render_info["bsp_node_count"]),
            "render_bsp_in_leaf_count": int(render_info["bsp_in_leaf_count"]),
            "render_bsp_out_leaf_count": int(render_info["bsp_out_leaf_count"]),
            "render_bsp_polygon_list_count": int(render_info["bsp_polygon_list_count"]),
            "render_bsp_valid_tree": bool(render_info["bsp_valid_tree"]),
            "render_chunk_count": int(render_info["chunk_count"]),
            "render_terminal_chunk_count": int(render_info["terminal_chunk_count"]),
            "render_chunk_compact_node_total": int(render_info["chunk_compact_node_total"]),
            "render_chunk_bsp_node_total": int(render_info["chunk_bsp_node_total"]),
            "render_chunk_polygon_list_total": int(render_info["chunk_polygon_list_total"]),
            "render_chunk_chain_valid": bool(render_info["chunk_chain_valid"]),
            "render_unknown_payload_size": int(render_info["unknown_payload_size"]),
            "render_fully_decoded": bool(render_info["fully_decoded"]),
            "render_chunks": list(render_info["chunks"]),
        })

    info.update({
        "section_ranges": ranges,
        "node_count": int(node_count),
        "root_count": int(root_count),
        "in_leaf_count": int(in_leaf_count),
        "out_leaf_count": int(out_leaf_count),
        "referenced_polygon_count": len({poly_index for poly_index, _side0, _side1 in nodes}),
        "polygon_list_count": int(polygon_list_count),
        "render_payload_size": max(0, end - cursor),
        "valid_tree": bool(valid_tree),
    })
    return info


def _decode_terrain_tail_render_payload(
    raw: bytes,
    start: int,
    end: int,
    terrain_tail_node_count: int,
) -> Dict[str, object]:
    info: Dict[str, object] = {
        "section_ranges": {},
        "compact_node_count": 0,
        "compact_root_index": 0,
        "compact_in_leaf_count": 0,
        "compact_out_leaf_count": 0,
        "compact_valid_tree": False,
        "bsp_marker": 0,
        "bsp_depth": 0,
        "bsp_center": None,
        "bsp_node_count": 0,
        "bsp_in_leaf_count": 0,
        "bsp_out_leaf_count": 0,
        "bsp_polygon_list_count": 0,
        "bsp_valid_tree": False,
        "chunk_count": 0,
        "terminal_chunk_count": 0,
        "chunk_compact_node_total": 0,
        "chunk_bsp_node_total": 0,
        "chunk_polygon_list_total": 0,
        "chunk_chain_valid": False,
        "unknown_payload_size": max(0, int(end) - int(start)),
        "fully_decoded": False,
        "chunks": [],
    }
    if terrain_tail_node_count <= 0 or start + 40 > end:
        return info

    first_chunk = _decode_terrain_tail_render_chunk(
        raw,
        start,
        end,
        terrain_tail_node_count,
        chunk_index=0,
        first_chunk=True,
        source_node_table_name="terrain_tail_nodes",
    )
    if not first_chunk["valid"]:
        return info

    ranges: Dict[str, ByteRange] = dict(first_chunk["section_ranges"])  # type: ignore[arg-type]
    chunks = [first_chunk]
    cursor = int(first_chunk["end"])
    expected_compact_count = int(first_chunk["bsp_node_count"])
    chain_valid = bool(first_chunk["chain_valid"])
    while cursor < end and expected_compact_count > 0:
        chunk = _decode_terrain_tail_render_chunk(
            raw,
            cursor,
            end,
            expected_compact_count,
            chunk_index=len(chunks),
            first_chunk=False,
            source_node_table_name=(
                "terrain_tail_render_bsp_nodes"
                if len(chunks) == 1
                else f"terrain_tail_render_chunk_{len(chunks) - 1:03d}_bsp_nodes"
            ),
        )
        if not chunk["valid"]:
            break
        ranges.update(chunk["section_ranges"])  # type: ignore[arg-type]
        chunks.append(chunk)
        cursor = int(chunk["end"])
        chain_valid = chain_valid and bool(chunk["chain_valid"])
        if bool(chunk["terminal"]):
            break
        expected_compact_count = int(chunk["bsp_node_count"])

    ranges["terrain_tail_render_chunks"] = (start, cursor)
    ranges["terrain_tail_render_unknown_payload"] = (cursor, end)

    compact_total = sum(int(chunk["compact_node_count"]) for chunk in chunks)
    bsp_node_total = sum(int(chunk["bsp_node_count"]) for chunk in chunks)
    polygon_list_total = sum(int(chunk["bsp_polygon_list_count"]) for chunk in chunks)
    terminal_count = sum(1 for chunk in chunks if bool(chunk["terminal"]))
    unknown_payload_size = max(0, int(end) - int(cursor))
    fully_decoded = bool(chain_valid and cursor == end)
    info.update({
        "section_ranges": ranges,
        "compact_node_count": int(first_chunk["compact_node_count"]),
        "compact_root_index": int(first_chunk["compact_root_index"]),
        "compact_in_leaf_count": int(first_chunk["compact_in_leaf_count"]),
        "compact_out_leaf_count": int(first_chunk["compact_out_leaf_count"]),
        "compact_valid_tree": bool(first_chunk["compact_valid_tree"]),
        "bsp_marker": int(first_chunk["bsp_marker"]),
        "bsp_depth": int(first_chunk["bsp_depth"]),
        "bsp_center": first_chunk["bsp_center"],
        "bsp_node_count": int(first_chunk["bsp_node_count"]),
        "bsp_in_leaf_count": int(first_chunk["bsp_in_leaf_count"]),
        "bsp_out_leaf_count": int(first_chunk["bsp_out_leaf_count"]),
        "bsp_polygon_list_count": int(first_chunk["bsp_polygon_list_count"]),
        "bsp_valid_tree": bool(first_chunk["bsp_valid_tree"]),
        "chunk_count": len(chunks),
        "terminal_chunk_count": int(terminal_count),
        "chunk_compact_node_total": int(compact_total),
        "chunk_bsp_node_total": int(bsp_node_total),
        "chunk_polygon_list_total": int(polygon_list_total),
        "chunk_chain_valid": fully_decoded,
        "unknown_payload_size": int(unknown_payload_size),
        "fully_decoded": fully_decoded,
        "chunks": [
            chunk["inspection"]
            for chunk in chunks
            if chunk.get("inspection") is not None
        ],
    })
    return info


def _decode_terrain_tail_render_chunk(
    raw: bytes,
    start: int,
    end: int,
    expected_compact_count: int,
    *,
    chunk_index: int,
    first_chunk: bool,
    source_node_table_name: str,
) -> Dict[str, object]:
    info: Dict[str, object] = {
        "valid": False,
        "terminal": False,
        "end": start,
        "section_ranges": {},
        "inspection": None,
        "chain_valid": False,
        "compact_node_count": 0,
        "compact_root_index": 0,
        "compact_in_leaf_count": 0,
        "compact_out_leaf_count": 0,
        "compact_valid_tree": False,
        "bsp_marker": 0,
        "bsp_depth": 0,
        "bsp_center": None,
        "bsp_node_count": 0,
        "bsp_in_leaf_count": 0,
        "bsp_out_leaf_count": 0,
        "bsp_polygon_list_count": 0,
        "bsp_valid_tree": False,
    }
    if expected_compact_count <= 0 or start + 40 > end:
        return info

    # Observed outdoor MM9 Terrain0 tails use three 1 dwords, two vec3-ish
    # records, then a compact u16 node table.  Keep this strictly validated.
    header_flags = struct.unpack_from("<3I", raw, start)
    section_size = _read_vec3(raw, start + 12)
    section_min = _read_vec3(raw, start + 24)
    if header_flags != (1, 1, 1):
        return info
    if not all(math.isfinite(value) and value >= 0.0 for value in section_size):
        return info
    if not all(math.isfinite(value) and -100000.0 <= value <= 100000.0 for value in section_min):
        return info
    header_start = start
    compact_start = start + 36
    compact_count = struct.unpack_from("<H", raw, compact_start)[0]
    compact_root = struct.unpack_from("<H", raw, compact_start + 2)[0]
    compact_end = compact_start + 4 + int(compact_count) * 6
    if compact_count != expected_compact_count or compact_end > end:
        return info

    compact_info = _decode_compact_render_node_table(raw, compact_start, compact_count)
    if not compact_info["valid"]:
        return info

    prefix = "terrain_tail_render" if first_chunk else f"terrain_tail_render_chunk_{chunk_index:03d}"
    ranges: Dict[str, ByteRange] = {
        f"{prefix}_header": (header_start, compact_start),
        f"{prefix}_compact_nodes": (compact_start, compact_end),
    }

    bsp_info = _decode_terrain_tail_render_bsp_block(
        raw,
        compact_end,
        end,
        prefix=f"{prefix}_bsp",
    )
    terminal = False
    if not bsp_info["valid_tree"]:
        bsp_info = _decode_terrain_tail_render_terminal_bsp_header(
            raw,
            compact_end,
            end,
            prefix=f"{prefix}_bsp",
        )
        terminal = bool(bsp_info["valid_tree"])
    if not bsp_info["valid_tree"]:
        return info

    ranges.update(bsp_info["section_ranges"])  # type: ignore[arg-type]
    bsp_header_range = _first_range_with_suffix(ranges, "_bsp_header")
    if bsp_header_range == (0, 0):
        bsp_header_range = _first_range_with_suffix(ranges, "_bsp_terminal_header")
    bsp_node_range = _first_range_with_suffix(ranges, "_bsp_nodes")
    bsp_polygon_list_range = _first_range_with_suffix(ranges, "_bsp_polygon_list")
    inspection = TerrainRenderChunkInspection(
        index=int(chunk_index),
        source_node_table_name=str(source_node_table_name),
        terminal=bool(terminal),
        header_range=ranges[f"{prefix}_header"],
        compact_node_range=ranges[f"{prefix}_compact_nodes"],
        bsp_header_range=bsp_header_range,
        bsp_node_range=bsp_node_range,
        bsp_polygon_list_range=bsp_polygon_list_range,
        header_flags=tuple(int(value) for value in header_flags),
        section_size=tuple(float(value) for value in section_size),
        section_min=tuple(float(value) for value in section_min),
        compact_node_count=int(compact_count),
        compact_root_index=int(compact_root),
        compact_in_leaf_count=int(compact_info["in_leaf_count"]),
        compact_out_leaf_count=int(compact_info["out_leaf_count"]),
        compact_valid_tree=bool(compact_info["valid_tree"]),
        bsp_marker=int(bsp_info["marker"]),
        bsp_depth=int(bsp_info["depth"]),
        bsp_center=bsp_info["center"],
        bsp_node_count=int(bsp_info["node_count"]),
        bsp_in_leaf_count=int(bsp_info["in_leaf_count"]),
        bsp_out_leaf_count=int(bsp_info["out_leaf_count"]),
        bsp_polygon_list_count=int(bsp_info["polygon_list_count"]),
        bsp_valid_tree=bool(bsp_info["valid_tree"]),
    )
    info.update({
        "valid": True,
        "terminal": bool(terminal),
        "end": int(bsp_info["end"]),
        "section_ranges": ranges,
        "inspection": inspection,
        "chain_valid": True,
        "compact_node_count": int(compact_count),
        "compact_root_index": int(compact_root),
        "compact_in_leaf_count": int(compact_info["in_leaf_count"]),
        "compact_out_leaf_count": int(compact_info["out_leaf_count"]),
        "compact_valid_tree": bool(compact_info["valid_tree"]),
        "bsp_marker": int(bsp_info["marker"]),
        "bsp_depth": int(bsp_info["depth"]),
        "bsp_center": bsp_info["center"],
        "bsp_node_count": int(bsp_info["node_count"]),
        "bsp_in_leaf_count": int(bsp_info["in_leaf_count"]),
        "bsp_out_leaf_count": int(bsp_info["out_leaf_count"]),
        "bsp_polygon_list_count": int(bsp_info["polygon_list_count"]),
        "bsp_valid_tree": bool(bsp_info["valid_tree"]),
    })
    return info


def _decode_compact_render_node_table(
    raw: bytes,
    start: int,
    compact_count: int,
) -> Dict[str, object]:
    info: Dict[str, object] = {
        "valid": False,
        "valid_tree": False,
        "in_leaf_count": 0,
        "out_leaf_count": 0,
    }
    compact_root = struct.unpack_from("<H", raw, start + 2)[0]
    order: List[int] = []
    child_refs: List[int] = []
    in_leaf_count = 0
    out_leaf_count = 0
    for index in range(int(compact_count)):
        node_index, side0, side1 = struct.unpack_from("<HHH", raw, start + 4 + index * 6)
        order.append(int(node_index))
        for side in (int(side0), int(side1)):
            if side == 0xFFFF:
                out_leaf_count += 1
            elif side == 0xFFFE:
                in_leaf_count += 1
            elif 0 <= side < int(compact_count):
                child_refs.append(side)
            else:
                return info
    if sorted(order) != list(range(int(compact_count))):
        return info
    parents: Dict[int, int] = {}
    for child in child_refs:
        parents[child] = parents.get(child, 0) + 1
    valid_tree = (
        compact_root < compact_count
        and sum(1 for index in range(int(compact_count)) if parents.get(index, 0) == 0) == 1
        and all(count == 1 for count in parents.values())
    )
    info.update({
        "valid": True,
        "valid_tree": bool(valid_tree),
        "in_leaf_count": int(in_leaf_count),
        "out_leaf_count": int(out_leaf_count),
    })
    return info


def _first_range_with_suffix(
    ranges: Dict[str, ByteRange],
    suffix: str,
) -> ByteRange:
    for name, value in ranges.items():
        if name.endswith(suffix):
            return value
    return (0, 0)


def _decode_terrain_tail_render_bsp_block(
    raw: bytes,
    start: int,
    end: int,
    *,
    prefix: str = "terrain_tail_render_bsp",
) -> Dict[str, object]:
    info: Dict[str, object] = {
        "section_ranges": {},
        "end": start,
        "marker": 0,
        "depth": 0,
        "center": None,
        "node_count": 0,
        "in_leaf_count": 0,
        "out_leaf_count": 0,
        "polygon_list_count": 0,
        "valid_tree": False,
    }
    if start + 24 > end:
        return info
    marker, depth = struct.unpack_from("<2I", raw, start)
    center = _read_vec3(raw, start + 8)
    if not all(math.isfinite(value) and -100000.0 <= value <= 100000.0 for value in center):
        return info
    node_count_offset = start + 20
    node_count = struct.unpack_from("<I", raw, node_count_offset)[0]
    nodes_start = node_count_offset + 4
    nodes_end = nodes_start + int(node_count) * 12
    if node_count <= 0 or node_count > 1_000_000 or nodes_end > end:
        return info

    children: List[int] = []
    parents: Dict[int, int] = {}
    in_leaf_count = 0
    out_leaf_count = 0
    polygons: List[int] = []
    for index in range(int(node_count)):
        poly_index, side0, side1 = struct.unpack_from("<Iii", raw, nodes_start + index * 12)
        polygons.append(int(poly_index))
        for side in (int(side0), int(side1)):
            if side == -1:
                in_leaf_count += 1
            elif side == -2:
                out_leaf_count += 1
            elif 0 <= side < int(node_count):
                children.append(side)
            else:
                return info
    for child in children:
        parents[child] = parents.get(child, 0) + 1
    valid_tree = (
        sum(1 for index in range(int(node_count)) if parents.get(index, 0) == 0) == 1
        and all(count == 1 for count in parents.values())
    )

    list_start = nodes_end
    if list_start + 8 > end:
        return info
    list_marker, list_count = struct.unpack_from("<2I", raw, list_start)
    list_end = list_start + 8 + int(list_count) * 4
    if list_marker != 0 or list_count > 1_000_000 or list_end > end:
        return info
    values = [
        struct.unpack_from("<I", raw, list_start + 8 + index * 4)[0]
        for index in range(int(list_count))
    ]
    if any(values[index] > values[index + 1] for index in range(len(values) - 1)):
        return info
    if set(values) != set(polygons):
        return info

    info.update({
        "section_ranges": {
            f"{prefix}_header": (start, nodes_start),
            f"{prefix}_nodes": (nodes_start, nodes_end),
            f"{prefix}_polygon_list": (list_start, list_end),
        },
        "end": list_end,
        "marker": int(marker),
        "depth": int(depth),
        "center": tuple(center),
        "node_count": int(node_count),
        "in_leaf_count": int(in_leaf_count),
        "out_leaf_count": int(out_leaf_count),
        "polygon_list_count": int(list_count),
        "valid_tree": bool(valid_tree),
    })
    return info


def _decode_terrain_tail_render_terminal_bsp_header(
    raw: bytes,
    start: int,
    end: int,
    *,
    prefix: str,
) -> Dict[str, object]:
    info: Dict[str, object] = {
        "section_ranges": {},
        "end": start,
        "marker": 0,
        "depth": 0,
        "center": None,
        "node_count": 0,
        "in_leaf_count": 0,
        "out_leaf_count": 0,
        "polygon_list_count": 0,
        "valid_tree": False,
    }
    if start + 20 > end:
        return info
    marker, depth = struct.unpack_from("<2I", raw, start)
    center = _read_vec3(raw, start + 8)
    if not all(math.isfinite(value) and -100000.0 <= value <= 100000.0 for value in center):
        return info
    info.update({
        "section_ranges": {
            f"{prefix}_terminal_header": (start, start + 20),
        },
        "end": start + 20,
        "marker": int(marker),
        "depth": int(depth),
        "center": tuple(center),
        "valid_tree": True,
    })
    return info


def _section_changed_bytes(
    changed_mask: Sequence[bool],
    source_sections: Dict[str, ByteRange],
    changed_sections: Dict[str, ByteRange],
) -> Dict[str, int]:
    out: Dict[str, int] = {}
    names = sorted(set(source_sections) | set(changed_sections))
    for name in names:
        source_range = source_sections.get(name)
        changed_range = changed_sections.get(name)
        if source_range != changed_range or source_range is None:
            out[name] = -1
            continue
        start, end = source_range
        start = max(0, int(start))
        end = min(len(changed_mask), int(end))
        out[name] = sum(1 for index in range(start, end) if changed_mask[index])
    return out


def _range_mask(size: int, ranges: Iterable[ByteRange]) -> List[bool]:
    mask = [False] * size
    for start, end in ranges:
        start = max(0, int(start))
        end = min(size, int(end))
        for index in range(start, end):
            mask[index] = True
    return mask


def _inspect_plane_relationship(
    raw: bytes,
    model: bsp.WorldModelMesh,
    plane_offsets: Sequence[int],
) -> TerrainPlaneRelationshipInspection:
    polygon_count = len(model.polygons)
    plane_count = len(plane_offsets)
    if polygon_count <= 0 or plane_count <= 0:
        return TerrainPlaneRelationshipInspection(
            polygon_count=polygon_count,
            plane_count=plane_count,
        )

    plane_indices = [int(polygon.plane_index) for polygon in model.polygons]
    distinct_indices = set(plane_indices)
    zero_count = sum(1 for index in plane_indices if index == 0)
    out_of_range = sum(1 for index in plane_indices if index < 0 or index >= plane_count)
    referenced = {index for index in distinct_indices if 0 <= index < plane_count}
    reference_mode = "indexed"
    uses_plane_table = out_of_range == 0
    if zero_count == polygon_count and plane_count > 1:
        reference_mode = "placeholder_zero"
        uses_plane_table = False
    elif out_of_range > 0:
        reference_mode = "invalid"
        uses_plane_table = False
    elif len(referenced) <= 1 and polygon_count > 1:
        reference_mode = "single_plane_index"

    direct = 0
    reversed_count = 0
    mismatches = 0
    max_normal_delta = 0.0
    max_distance_delta = 0.0
    if uses_plane_table:
        for polygon in model.polygons:
            plane_index = int(polygon.plane_index)
            if not (0 <= plane_index < plane_count):
                continue
            stored_normal = _read_vec3(raw, plane_offsets[plane_index])
            stored_distance = struct.unpack_from("<f", raw, plane_offsets[plane_index] + 12)[0]
            computed_normal, computed_distance = _polygon_plane(model.points, polygon)
            normal_delta = _distance(stored_normal, computed_normal)
            distance_delta = abs(float(stored_distance) - float(computed_distance))
            reversed_normal_delta = _distance(stored_normal, _negated_vec3(computed_normal))
            reversed_distance_delta = abs(float(stored_distance) + float(computed_distance))
            if reversed_normal_delta + reversed_distance_delta < normal_delta + distance_delta:
                reversed_count += 1
                normal_delta = reversed_normal_delta
                distance_delta = reversed_distance_delta
            else:
                direct += 1
            max_normal_delta = max(max_normal_delta, normal_delta)
            max_distance_delta = max(max_distance_delta, distance_delta)
            if normal_delta > 1.0e-3 or distance_delta > 1.0e-2:
                mismatches += 1

    return TerrainPlaneRelationshipInspection(
        polygon_count=polygon_count,
        plane_count=plane_count,
        reference_mode=reference_mode,
        polygon_records_use_plane_table=bool(uses_plane_table),
        distinct_polygon_plane_index_count=len(distinct_indices),
        zero_plane_index_count=zero_count,
        out_of_range_polygon_count=out_of_range,
        referenced_plane_count=len(referenced),
        unused_plane_count=max(0, plane_count - len(referenced)),
        direct_plane_match_count=direct,
        reversed_plane_match_count=reversed_count,
        plane_mismatch_count=mismatches,
        max_normal_delta=float(max_normal_delta),
        max_distance_delta=float(max_distance_delta),
    )


def _changed_ranges(mask: Sequence[bool], *, max_ranges: int) -> List[ByteRange]:
    ranges: List[ByteRange] = []
    start: Optional[int] = None
    for index, changed in enumerate(mask):
        if changed and start is None:
            start = index
        elif not changed and start is not None:
            ranges.append((start, index))
            start = None
            if len(ranges) >= max_ranges:
                return ranges
    if start is not None and len(ranges) < max_ranges:
        ranges.append((start, len(mask)))
    return ranges


def _diff_vectors(
    source: Sequence[Vec3],
    changed: Sequence[Vec3],
    *,
    total: int,
    epsilon: float,
    max_indices: int,
) -> BspRecordFieldDiff:
    changed_indices: List[int] = []
    changed_count = 0
    max_delta = 0.0
    for index, (left, right) in enumerate(zip(source, changed)):
        delta = _distance(left, right)
        if delta <= epsilon:
            continue
        changed_count += 1
        max_delta = max(max_delta, delta)
        if len(changed_indices) < max_indices:
            changed_indices.append(index)
    return BspRecordFieldDiff(
        changed_count=changed_count,
        total_count=total,
        max_delta=max_delta,
        changed_indices=changed_indices,
    )


def _diff_vectors_at_offsets(
    source_raw: bytes,
    changed_raw: bytes,
    source_offsets: Sequence[int],
    changed_offsets: Sequence[int],
    *,
    total: int,
    epsilon: float,
    max_indices: int,
) -> BspRecordFieldDiff:
    count = min(len(source_offsets), len(changed_offsets))
    return _diff_vectors(
        [_read_vec3(source_raw, source_offsets[index]) for index in range(count)],
        [_read_vec3(changed_raw, changed_offsets[index]) for index in range(count)],
        total=total,
        epsilon=epsilon,
        max_indices=max_indices,
    )


def _diff_planes(
    source_raw: bytes,
    changed_raw: bytes,
    source_offsets: Sequence[int],
    changed_offsets: Sequence[int],
    *,
    epsilon: float,
    max_indices: int,
) -> BspRecordFieldDiff:
    changed_indices: List[int] = []
    changed_count = 0
    max_delta = 0.0
    for index in range(min(len(source_offsets), len(changed_offsets))):
        source_normal = _read_vec3(source_raw, source_offsets[index])
        changed_normal = _read_vec3(changed_raw, changed_offsets[index])
        source_distance = struct.unpack_from("<f", source_raw, source_offsets[index] + 12)[0]
        changed_distance = struct.unpack_from("<f", changed_raw, changed_offsets[index] + 12)[0]
        delta = max(
            _distance(source_normal, changed_normal),
            abs(float(changed_distance) - float(source_distance)),
        )
        if delta <= epsilon:
            continue
        changed_count += 1
        max_delta = max(max_delta, delta)
        if len(changed_indices) < max_indices:
            changed_indices.append(index)
    return BspRecordFieldDiff(
        changed_count=changed_count,
        total_count=len(source_offsets),
        max_delta=max_delta,
        changed_indices=changed_indices,
    )


def _polygon_plane(
    points: Sequence[Vec3],
    polygon: bsp.Polygon,
) -> Tuple[Vec3, float]:
    vertices = [
        points[int(index)]
        for index in polygon.vertex_indices
        if 0 <= int(index) < len(points)
    ]
    if len(vertices) < 3:
        return (0.0, 0.0, 0.0), 0.0
    normal = _polygon_normal(vertices)
    return normal, _dot(normal, vertices[0])


def _polygon_normal(vertices: Sequence[Vec3]) -> Vec3:
    nx = ny = nz = 0.0
    for index, current in enumerate(vertices):
        nxt = vertices[(index + 1) % len(vertices)]
        nx += (float(current[1]) - float(nxt[1])) * (float(current[2]) + float(nxt[2]))
        ny += (float(current[2]) - float(nxt[2])) * (float(current[0]) + float(nxt[0]))
        nz += (float(current[0]) - float(nxt[0])) * (float(current[1]) + float(nxt[1]))
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length <= 1.0e-8:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def _negated_vec3(value: Vec3) -> Vec3:
    return (-float(value[0]), -float(value[1]), -float(value[2]))


def _dot(a: Vec3, b: Vec3) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _read_vec3(raw: bytes, offset: int) -> Vec3:
    return struct.unpack_from("<3f", raw, int(offset))


def _distance(a: Vec3, b: Vec3) -> float:
    dx = float(b[0]) - float(a[0])
    dy = float(b[1]) - float(a[1])
    dz = float(b[2]) - float(a[2])
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _field_text(diff: BspRecordFieldDiff) -> str:
    text = f"{diff.changed_count}/{diff.total_count}, max_delta={diff.max_delta:.6g}"
    if diff.changed_indices:
        text += f", indices={diff.changed_indices}"
    return text


def _range_text(start: Optional[int], end: Optional[int]) -> str:
    if start is None or end is None:
        return "unknown"
    return f"{start}-{end}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or diff MM9 DAT BSP world-model records")
    parser.add_argument("source", help="source DAT path")
    parser.add_argument("changed", nargs="?", help="changed DAT path; omit for inspection only")
    parser.add_argument("--model", action="append", dest="models", help="model name to inspect/diff; repeatable")
    parser.add_argument("--json", action="store_true", help="write machine-readable JSON")
    args = parser.parse_args(argv)

    models = tuple(args.models or DEFAULT_MODEL_NAMES)
    with open(args.source, "rb") as f:
        source = f.read()
    if args.changed:
        with open(args.changed, "rb") as f:
            changed = f.read()
        report = diff_dat_records(source, changed, models)
        if args.json:
            print(json.dumps(report_to_dict(report), indent=2, sort_keys=True))
        else:
            print(format_diff_report(report))
    else:
        report = inspect_dat(source, models)
        if args.json:
            print(json.dumps({name: asdict(item) for name, item in report.items()}, indent=2, sort_keys=True))
        else:
            print(format_inspection_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
