"""Small generated v66 DAT prefabs used by the active prefab test suite."""

from __future__ import annotations

import os
import struct
from typing import Iterable, Sequence

from core import bsp
from features.dat_editing import bsp_compile
from features.dat_editing import legacy_ed
from mm9_patcher import mm9_patch as patcher


def write_prefab_fixtures(root: str) -> tuple[str, str]:
    fence = os.path.join(root, "OldWoodFence1.dat")
    door = os.path.join(root, "A1_Door.dat")
    write_minimal_dat(
        fence,
        [
            box_model("PhysicsBSP", (0.0, -48.0, -32.0), (382.0, 0.0, 0.0), "Default"),
            box_model("VisBSP", (0.0, -48.0, -32.0), (382.0, 0.0, 0.0), "Default"),
        ],
        [],
    )
    write_minimal_dat(
        door,
        [
            box_model("Door1", (-24.0, 0.0, -4.0), (24.0, 96.0, 4.0)),
            box_model("PhysicsBSP", (-24.0, 0.0, -4.0), (24.0, 96.0, 4.0)),
            box_model("VisBSP", (-24.0, 0.0, -4.0), (24.0, 96.0, 4.0)),
        ],
        [patcher.WorldObject("RotatingDoor", [
            patcher.Property("Name", 0, 0, "Door1"),
            patcher.Property("Pos", 1, 0, (2.0, 3.0, 4.0)),
        ])],
    )
    return fence, door


def write_legacy_ed_prefab(root: str) -> str:
    """Write a small two-brush DEdit v1249 source prefab."""
    path = os.path.join(root, "SourceChair.ed")
    data = bytearray(struct.pack("<I", legacy_ed.LEGACY_ED_VERSION))
    data.extend(b"\x00" * 16)
    _append_legacy_ed_brush(
        data,
        texture=b"TEXTURES\\World\\SourceFloor.dtx",
        points=[
            (0.0, 0.0, 0.0),
            (128.0, 0.0, 0.0),
            (128.0, 0.0, 128.0),
            (0.0, 0.0, 128.0),
        ],
    )
    data.extend(b"\x00" * 7)
    _append_legacy_ed_brush(
        data,
        texture=b"TEXTURES\\World\\SourceBack.dtx",
        points=[
            (0.0, 0.0, 256.0),
            (128.0, 0.0, 256.0),
            (128.0, 0.0, 384.0),
            (0.0, 0.0, 384.0),
        ],
    )
    data.extend(b"\x00" * 12)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _append_legacy_ed_brush(
    data: bytearray,
    *,
    texture: bytes,
    points: Sequence[tuple[float, float, float]],
) -> None:
    data.extend(bytes([255, 128, 64]))
    data.extend(struct.pack("<I", len(points)))
    for point in points:
        data.extend(struct.pack("<3f", *point))
    data.extend(struct.pack("<I", 1))
    data.extend(struct.pack("<I", len(points)))
    data.extend(struct.pack(f"<{len(points)}H", *range(len(points))))
    data.extend(struct.pack("<3ff", 0.0, 1.0, 0.0, 0.0))
    data.extend(struct.pack("<3f", 0.0, 0.0, 0.0))
    data.extend(struct.pack("<3f", 1.0, 0.0, 0.0))
    data.extend(struct.pack("<3f", 0.0, 0.0, 1.0))
    data.extend(struct.pack("<I", 1))
    data.extend(struct.pack("<H", len(texture)))
    data.extend(texture)


def write_minimal_dat(
    path: str,
    models: Sequence[bsp.WorldModelMesh],
    objects: Iterable[patcher.WorldObject],
) -> None:
    records = [bsp_compile.compile_world_model_record(model) for model in models]
    info = struct.pack("<I", 0)
    info += struct.pack("<f", 16.0)
    info += struct.pack("<6f", -1024.0, -1024.0, -1024.0, 1024.0, 1024.0, 1024.0)
    tree = struct.pack(
        "<6fII",
        -1024.0, -1024.0, -1024.0,
        1024.0, 1024.0, 1024.0,
        1,
        0,
    ) + b"\x00"
    table_prefix = info + tree + struct.pack("<I", len(records))
    header_size = struct.calcsize("<11I")
    object_section = patcher.serialize_objects(list(objects))
    obj_pos = header_size + len(table_prefix) + sum(len(record.raw_bytes) for record in records)

    offset = header_size + len(table_prefix)
    raw_records = []
    for index, record in enumerate(records):
        next_item = offset + len(record.raw_bytes) if index + 1 < len(records) else obj_pos
        raw = bsp_compile.patch_next_world_item(record, next_item)
        raw_records.append(raw)
        offset += len(raw)
    ren_pos = obj_pos + len(object_section)
    header = patcher.Header(66, obj_pos, ren_pos, (0,) * 8)
    with open(path, "wb") as handle:
        handle.write(header.pack() + table_prefix + b"".join(raw_records) + object_section)


def box_model(
    name: str,
    min_box: tuple[float, float, float],
    max_box: tuple[float, float, float],
    texture: str = r"TEXTURES\LevelTextures\Misc\Firethrough.dtx",
) -> bsp.WorldModelMesh:
    x0, y0, z0 = min_box
    x1, y1, z1 = max_box
    points = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    return bsp.WorldModelMesh(
        name=name,
        min_box=min_box,
        max_box=max_box,
        translation=(0.0, 0.0, 0.0),
        points=points,
        polygons=[
            bsp.Polygon([0, 3, 2, 1], 0, 0),
            bsp.Polygon([4, 5, 6, 7], 0, 1),
            bsp.Polygon([0, 4, 7, 3], 0, 2),
            bsp.Polygon([1, 2, 6, 5], 0, 3),
            bsp.Polygon([0, 1, 5, 4], 0, 4),
            bsp.Polygon([3, 7, 6, 2], 0, 5),
        ],
        texture_names=[texture],
        surfaces=[bsp.Surface(
            uv_o=min_box,
            uv_p=(1.0, 0.0, 0.0),
            uv_q=(0.0, 1.0, 0.0),
            texture_index=0,
            flags=bsp.SURF_SOLID,
            texture_flags=0,
        )],
    )
