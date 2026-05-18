"""
door_bsp_writer.py
==================

Minimal DAT serializer support for appending copied BSP submodels.  It handles
the raw-record shape produced by physical-door clones and static prefab imports:
copy an existing WorldModel record, rename it, transform its bounds, point
positions, normals, and surface projection vectors, and insert it immediately
before the WorldObject section or the terminal BSP tail.
"""

from __future__ import annotations

import struct
from typing import List, Sequence, Tuple

import _path_setup  # noqa: F401
import bsp
import door_clone
import mm9_patch as patcher


HEADER_SIZE = struct.calcsize("<11I")
Vec3 = Tuple[float, float, float]


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
        self.pos += count

    def lt_string_u16(self) -> Tuple[int, int, str]:
        length_pos = self.pos
        length = self.u16()
        value_pos = self.pos
        value = self.data[value_pos:value_pos + length].decode("latin-1")
        self.pos += length
        return length_pos, length, value

    def cstring(self) -> str:
        end = self.data.index(b"\x00", self.pos)
        value = self.data[self.pos:end].decode("latin-1")
        self.pos = end + 1
        return value


def _pack_lt_string(value: str) -> bytes:
    raw = str(value or "").encode("latin-1")
    if len(raw) > 0xFFFF:
        raise ValueError(f"world model name is too long: {value!r}")
    return struct.pack("<H", len(raw)) + raw


def _patch_vec3(buf: bytearray, offset: int, delta: Vec3) -> None:
    x, y, z = struct.unpack_from("<3f", buf, offset)
    dx, dy, dz = delta
    struct.pack_into("<3f", buf, offset, x + dx, y + dy, z + dz)


def _skip_leaf(cursor: _Cursor) -> None:
    count = cursor.u16()
    if count == 0xFFFF:
        cursor.u16()
    else:
        for _ in range(count):
            cursor.skip(2)
            size = cursor.u16()
            cursor.skip(size)
    poly_count = cursor.u32()
    cursor.skip(poly_count * 4)
    cursor.u32()


def _read_surface_offsets(cursor: _Cursor) -> Tuple[int, int, int]:
    uv_o_offset = cursor.pos
    cursor.skip(12)
    uv_p_offset = cursor.pos
    cursor.skip(12)
    uv_q_offset = cursor.pos
    cursor.skip(12)
    cursor.skip(2)
    cursor.skip(4)
    cursor.skip(4)
    cursor.skip(4)
    use_effects = cursor.u8()
    if use_effects == 1:
        cursor.lt_string_u16()
        cursor.lt_string_u16()
    cursor.skip(2)
    return uv_o_offset, uv_p_offset, uv_q_offset


def _world_bsp_patch_offsets(raw: bytes, source_model: bsp.WorldModelMesh) -> Tuple[
    int,
    int,
    int,
    int,
    List[int],
    List[Tuple[int, int, int]],
    List[Tuple[int, int, int]],
    List[Tuple[int, int]],
]:
    if source_model.raw_start is None or source_model.world_bsp_start is None:
        raise ValueError(f"BSP model {source_model.name!r} has no raw provenance")
    start = source_model.world_bsp_start - source_model.raw_start
    if start < 0 or start >= len(raw):
        raise ValueError(f"BSP model {source_model.name!r} has invalid raw provenance")

    cursor = _Cursor(raw, start)
    cursor.skip(4)  # info_flags
    cursor.skip(4)  # unknown
    name_length_pos, _name_length, _name = cursor.lt_string_u16()

    point_count = cursor.u32()
    plane_count = cursor.u32()
    surface_count = cursor.u32()
    user_portal_count = cursor.u32()
    poly_count = cursor.u32()
    leaf_count = cursor.u32()
    cursor.u32()  # vert_count
    cursor.u32()  # total_vis
    cursor.u32()  # leaf_list_count
    node_count = cursor.u32()
    cursor.u32()  # unknown_value_2
    cursor.u32()  # unknown_value_3

    min_box_offset = cursor.pos
    cursor.skip(36)  # min_box, max_box, translation

    cursor.u32()  # name_length
    texture_count = cursor.u32()
    for _ in range(texture_count):
        cursor.cstring()

    verts_per_poly: List[int] = []
    for _ in range(poly_count):
        verts_per_poly.append(cursor.u8() + cursor.u8())

    for _ in range(leaf_count):
        _skip_leaf(cursor)

    plane_offsets: List[int] = []
    for _ in range(plane_count):
        plane_offsets.append(cursor.pos)
        cursor.skip(16)

    surface_offsets: List[Tuple[int, int, int]] = []
    for _ in range(surface_count):
        surface_offsets.append(_read_surface_offsets(cursor))

    polygon_offsets: List[Tuple[int, int, int]] = []
    for vert_count in verts_per_poly:
        center_offset = cursor.pos
        cursor.skip(12)
        cursor.skip(2)
        cursor.skip(2)
        unknown_flag = cursor.u16()
        if unknown_flag > 0:
            cursor.skip(unknown_flag * 4)
        surface_index_offset = cursor.pos
        cursor.skip(2)
        plane_index_offset = cursor.pos
        cursor.skip(2)
        polygon_offsets.append((center_offset, surface_index_offset, plane_index_offset))
        cursor.skip(vert_count * 5)

    cursor.skip(node_count * 14)

    for _ in range(user_portal_count):
        cursor.lt_string_u16()
        cursor.skip(4)
        cursor.skip(4)
        cursor.skip(2)
        cursor.skip(24)

    point_offsets: List[Tuple[int, int]] = []
    for _ in range(point_count):
        point_offsets.append((cursor.pos, cursor.pos + 12))
        cursor.skip(24)  # point vec3 + normal vec3

    return (
        name_length_pos,
        min_box_offset,
        min_box_offset + 12,
        min_box_offset + 24,
        plane_offsets,
        surface_offsets,
        polygon_offsets,
        point_offsets,
    )


def build_cloned_world_model_record(
    submodel: door_clone.DoorSubmodelClone,
    raw_start: int,
    next_world_item: int,
) -> bytes:
    """Return a renamed/translated clone record with patched NextWorldItem."""
    raw = bytearray(submodel.raw_bytes)
    world_bsp_rel = submodel.source_model.world_bsp_start - submodel.source_model.raw_start
    if submodel.info_flags_override is not None:
        struct.pack_into("<I", raw, world_bsp_rel, int(submodel.info_flags_override) & 0xFFFFFFFF)
    (
        name_length_pos,
        min_box_offset,
        max_box_offset,
        translation_offset,
        plane_offsets,
        surface_offsets,
        polygon_offsets,
        point_offsets,
    ) = _world_bsp_patch_offsets(raw, submodel.source_model)

    old_name_len = struct.unpack_from("<H", raw, name_length_pos)[0]
    old_name_total = 2 + old_name_len
    raw[name_length_pos:name_length_pos + old_name_total] = _pack_lt_string(submodel.new_name)

    name_size_delta = len(_pack_lt_string(submodel.new_name)) - old_name_total

    def adj(offset: int) -> int:
        return offset + name_size_delta if offset > name_length_pos else offset

    new_min, new_max = door_clone.transform_bounds(
        submodel.source_model.min_box,
        submodel.source_model.max_box,
        submodel.source_pivot,
        submodel.target_pivot,
        submodel.yaw_radians,
        scale=submodel.scale,
    )
    struct.pack_into("<3f", raw, adj(min_box_offset), *new_min)
    struct.pack_into("<3f", raw, adj(max_box_offset), *new_max)
    struct.pack_into("<3f", raw, adj(translation_offset), *door_clone.transform_point(
        submodel.source_model.translation,
        submodel.source_pivot,
        submodel.target_pivot,
        submodel.yaw_radians,
        scale=submodel.scale,
    ))
    for (uv_o_offset, uv_p_offset, uv_q_offset), surface in zip(surface_offsets, submodel.source_model.surfaces):
        struct.pack_into("<3f", raw, adj(uv_o_offset), *door_clone.transform_point(
            surface.uv_o,
            submodel.source_pivot,
            submodel.target_pivot,
            submodel.yaw_radians,
            scale=submodel.scale,
        ))
        struct.pack_into("<3f", raw, adj(uv_p_offset), *door_clone.transform_projection_vector(
            surface.uv_p,
            submodel.yaw_radians,
            submodel.scale,
        ))
        struct.pack_into("<3f", raw, adj(uv_q_offset), *door_clone.transform_projection_vector(
            surface.uv_q,
            submodel.yaw_radians,
            submodel.scale,
        ))

    transformed_points = [
        door_clone.transform_point(
            point,
            submodel.source_pivot,
            submodel.target_pivot,
            submodel.yaw_radians,
            scale=submodel.scale,
        )
        for point in submodel.source_model.points
    ]

    for plane_offset in plane_offsets:
        normal = struct.unpack_from("<3f", raw, adj(plane_offset))
        distance = struct.unpack_from("<f", raw, adj(plane_offset + 12))[0]
        new_normal = door_clone.transform_normal_vector(normal, submodel.yaw_radians, submodel.scale)
        # Source planes use dot(normal, point) == distance.
        source_point = (
            float(normal[0]) * float(distance),
            float(normal[1]) * float(distance),
            float(normal[2]) * float(distance),
        )
        new_point = door_clone.transform_point(
            source_point,
            submodel.source_pivot,
            submodel.target_pivot,
            submodel.yaw_radians,
            scale=submodel.scale,
        )
        new_distance = (
            new_normal[0] * new_point[0]
            + new_normal[1] * new_point[1]
            + new_normal[2] * new_point[2]
        )
        struct.pack_into("<3f", raw, adj(plane_offset), *new_normal)
        struct.pack_into("<f", raw, adj(plane_offset + 12), float(new_distance))

    for center_offset, _surface_index_offset, _plane_index_offset in polygon_offsets:
        center = struct.unpack_from("<3f", raw, adj(center_offset))
        struct.pack_into("<3f", raw, adj(center_offset), *door_clone.transform_point(
            center,
            submodel.source_pivot,
            submodel.target_pivot,
            submodel.yaw_radians,
            scale=submodel.scale,
        ))

    for (point_offset, normal_offset), point in zip(point_offsets, submodel.source_model.points):
        struct.pack_into("<3f", raw, adj(point_offset), *transformed_points.pop(0))
        normal = struct.unpack_from("<3f", raw, adj(normal_offset))
        struct.pack_into("<3f", raw, adj(normal_offset), *door_clone.transform_normal_vector(
            normal,
            submodel.yaw_radians,
            submodel.scale,
        ))

    struct.pack_into("<I", raw, 0, next_world_item)
    return bytes(raw)


def serialize_world_with_door_clones(
    source_dat: bytes,
    materialized: patcher.World,
    bsp_world: bsp.BspWorld,
    clone_plans: Sequence[door_clone.DoorClonePlan],
) -> bytes:
    """
    Serialize *materialized* while appending cloned door BSP submodels.

    The original render data and all non-cloned BSP bytes are preserved.  The
    world-model count is incremented, and the header's object/render offsets are
    recomputed around the inserted records and materialized object section.
    """
    clones = [sub for plan in clone_plans for sub in plan.submodels]
    return serialize_world_with_bsp_clones(source_dat, materialized, bsp_world, clones)


def serialize_world_with_bsp_clones(
    source_dat: bytes,
    materialized: patcher.World,
    bsp_world: bsp.BspWorld,
    clones: Sequence[door_clone.DoorSubmodelClone],
) -> bytes:
    """
    Serialize *materialized* while inserting copied/renamed BSP world models.

    The inserted records may come from the same level (door clones) or from a
    converted prefab DAT.  Only the target level's object section and BSP model
    table offsets are rewritten.
    """
    clones = list(clones or [])
    if not clones:
        return _serialize_world(materialized)

    header = patcher.Header.parse(source_dat)
    pre_objects = bytearray(source_dat[HEADER_SIZE:header.obj_pos])
    object_section = patcher.serialize_objects(materialized.objects)
    render_data = source_dat[header.ren_pos:]

    count_offset = bsp_world.world_model_table_start - HEADER_SIZE
    if count_offset < 0 or count_offset + 4 > len(pre_objects):
        raise ValueError("BSP world-model count offset is outside the pre-object section")
    old_count = struct.unpack_from("<I", pre_objects, count_offset)[0]
    struct.pack_into("<I", pre_objects, count_offset, old_count + len(clones))

    insert_at = _clone_insert_offset(header, bsp_world)
    insert_rel = insert_at - HEADER_SIZE
    if insert_rel < 0 or insert_rel > len(pre_objects):
        raise ValueError("BSP clone insertion point is outside the pre-object section")

    cloned_records: List[bytes] = []
    for index, submodel in enumerate(clones):
        raw_start = insert_at + sum(len(record) for record in cloned_records)
        # Temporarily use zero; final next pointer depends on this record's
        # post-rename length, so patch after building once.
        record = build_cloned_world_model_record(submodel, raw_start, 0)
        if index < len(clones) - 1:
            next_item = raw_start + len(record)
        else:
            # If the shipped DAT has a dummy/terminator record after the last
            # parsed model, clones must point to that shifted record rather
            # than straight to ObjectDataPos.  Bootcamp depends on this tail.
            next_item = insert_at + sum(len(r) for r in cloned_records) + len(record)
        record = build_cloned_world_model_record(submodel, raw_start, next_item)
        cloned_records.append(record)

    if cloned_records:
        last_model = max(
            (m for m in bsp_world.world_models if m.raw_start is not None),
            key=lambda m: m.raw_start,
        )
        last_next_offset = last_model.raw_start - HEADER_SIZE
        if 0 <= last_next_offset <= len(pre_objects) - 4:
            struct.pack_into("<I", pre_objects, last_next_offset, insert_at)

    inserted = b"".join(cloned_records)
    pre_objects[insert_rel:insert_rel] = inserted

    new_obj_pos = HEADER_SIZE + len(pre_objects)
    new_ren_pos = new_obj_pos + len(object_section)

    # A few MM9 DATs have a terminal world-model-like record whose first field
    # points at the object section.  If we inserted before it, shift that
    # pointer to the new object position so the game does not jump into the
    # middle of the moved WorldObject section.
    shifted_tail_rel = insert_rel + len(inserted)
    if shifted_tail_rel + 4 <= len(pre_objects):
        tail_next = struct.unpack_from("<I", pre_objects, shifted_tail_rel)[0]
        if tail_next == header.obj_pos:
            struct.pack_into("<I", pre_objects, shifted_tail_rel, new_obj_pos)

    new_header = patcher.Header(
        header.version,
        new_obj_pos,
        new_ren_pos,
        header.dummy,
    )
    return new_header.pack() + bytes(pre_objects) + object_section + render_data


def _clone_insert_offset(header: patcher.Header, bsp_world: bsp.BspWorld) -> int:
    last_model = max(
        (m for m in bsp_world.world_models if m.raw_start is not None),
        key=lambda m: m.raw_start,
    )
    next_item = int(last_model.next_world_item or 0)
    if last_model.raw_start is not None and last_model.raw_start < next_item < header.obj_pos:
        return next_item
    return header.obj_pos


def _serialize_world(world: patcher.World) -> bytes:
    object_section = patcher.serialize_objects(world.objects)
    new_obj_pos = HEADER_SIZE + len(world.pre_objects)
    new_ren_pos = new_obj_pos + len(object_section)
    header = patcher.Header(world.header.version, new_obj_pos, new_ren_pos, world.header.dummy)
    return header.pack() + world.pre_objects + object_section + world.render_data
