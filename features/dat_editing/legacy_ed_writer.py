"""Structured writer for MM9-compatible legacy DEdit ED v1249 data.

The writer covers the validated raw-brush, prefab, recursive node hierarchy,
and zlib-blocked full-world layouts.  Feature converters build on these
primitives instead of hand-assembling byte strings in each import path.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from features.dat_editing import legacy_ed


Vec3 = Tuple[float, float, float]
Quat = Tuple[float, float, float, float]

DEFAULT_BRUSH_POINT_WELD_TOLERANCE = 0.01
DEFAULT_FULL_LEVEL_ZLIB_BLOCK_SIZE = 50000
DEFAULT_FULL_LEVEL_INFOSTRING = (
    "AmbientLight 80 80 80 ; PBlockSize 2048 ; LMGridSize 64; MaxLMSize 32"
)

NODE_NODE = 0
NODE_BRUSH = 1
NODE_OBJECT = 2

MM9_BRUSH_OBJECT_PROPERTIES: Tuple[Tuple[str, int, int, object], ...] = (
    ("Name", 0, 0, ""),
    ("Pos", 1, 0, (0.0, 0.0, 0.0)),
    ("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
    ("Solid", 5, 0, True),
    ("Nonexistant", 5, 0, False),
    ("Invisible", 5, 0, False),
    ("Translucent", 5, 0, False),
    ("SkyPortal", 5, 0, False),
    ("FullyBright", 5, 0, False),
    ("FlatShade", 5, 0, False),
    ("GouraudShade", 5, 0, True),
    ("LightMap", 5, 0, True),
    ("Subdivide", 5, 0, True),
    ("HullMaker", 5, 0, False),
    ("AlwaysLightMap", 5, 0, False),
    ("DirectionalLight", 5, 0, False),
    ("Portal", 5, 0, False),
    ("NoSnap", 5, 0, False),
    ("SkyPan", 5, 0, False),
    ("Additive", 5, 0, False),
    ("Terrain", 5, 0, False),
    ("TimeOfDay", 5, 0, False),
    ("DetailLevel", 6, 0, 1.0),
    ("Effect", 0, 0, ""),
    ("EffectParam", 0, 0, ""),
    ("FrictionCoefficient", 3, 0, 1.0),
)

MM9_FULL_WORLD_BRUSH_NODE_PROPERTIES: Tuple[Tuple[str, int, int, object], ...] = (
    ("Name", 0, 0, ""),
    ("Pos", 1, 0, (0.0, 0.0, 0.0)),
    ("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
    ("Solid", 5, 0, True),
    ("Nonexistant", 5, 0, False),
    ("Invisible", 5, 0, False),
    ("Translucent", 5, 0, False),
    ("SkyPortal", 5, 0, False),
    ("FullyBright", 5, 0, False),
    ("FlatShade", 5, 0, False),
    ("GouraudShade", 5, 0, False),
    ("LightMap", 5, 0, False),
    ("Subdivide", 5, 0, False),
    ("HullMaker", 5, 0, False),
    ("AlwaysLightMap", 5, 0, False),
    ("DirectionalLight", 5, 0, False),
    ("Portal", 5, 0, False),
    ("NoSnap", 5, 0, False),
    ("SkyPan", 5, 0, False),
    ("Additive", 5, 0, False),
    ("TerrainOccluder", 5, 0, False),
    ("TimeOfDay", 5, 0, False),
    ("VisBlocker", 5, 0, False),
    ("NotAStep", 5, 0, False),
    ("DetailLevel", 6, 0, 0.0),
    ("Effect", 0, 0, ""),
    ("EffectParam", 0, 0, ""),
    ("FrictionCoefficient", 3, 0, 1.0),
)

MM9_WORLD_PROPERTIES_OBJECT_PROPERTIES: Tuple[Tuple[str, int, int, object], ...] = (
    ("Name", 0, 0, "WorldProperties0"),
    ("Pos", 1, 0, (0.0, 0.0, 0.0)),
    ("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
    ("MoveToFloor", 5, 0, False),
    ("ScriptName", 0, 16384, ""),
    ("ScriptParams", 0, 0, ""),
    ("NeedsTick", 5, 0, False),
    ("TouchNotify", 5, 0, False),
    ("Wind", 1, 0, (0.0, 0.0, 0.0)),
    ("AllSkyPortals", 5, 0, True),
    ("EnvironmentMap", 0, 16384, "TEXTURES\\ENVIRONMENTMAPS\\OutdoorTest.dtx"),
    ("SkyPanning", 6, 96, 0.0),
    ("LightScale", 2, 0, (255.0, 255.0, 255.0)),
    ("SoftSky", 0, 16384, "textures\\environmentmaps\\clouds\\clouds.dtx"),
    ("PlayerLightOn", 5, 0, True),
    ("EnableFog", 5, 0, False),
    ("FogColor", 2, 0, (127.0, 127.0, 127.0)),
    ("FogNearZ", 3, 0, 1.0),
    ("FogFarZ", 3, 0, 5000.0),
    ("FarZ", 3, 0, 8000.0),
    ("SkyFog", 5, 0, False),
    ("SkyFogNearZ", 3, 0, 100.0),
    ("SkyFogFarZ", 3, 0, 1000.0),
    ("CanSaveGame", 5, 0, True),
    ("CanMiniSaveGame", 5, 0, True),
    ("VFogInfo", 6, 288, 0.0),
    ("NightLightScale", 2, 0, (255.0, 255.0, 255.0)),
    ("Optimizations", 6, 544, 0.0),
    ("PanSkyTexture", 0, 16448, "Textures\\SkyPan.dtx"),
    ("PanSky", 5, 64, False),
    ("PanSkyOffsetX", 3, 64, 10.0),
    ("PanSkyOffsetZ", 3, 64, 10.0),
    ("PanSkyScaleX", 3, 64, 10.0),
    ("PanSkyScaleZ", 3, 64, 10.0),
    ("VFog", 5, 256, False),
    ("VFogMinY", 3, 256, 0.0),
    ("VFogMaxY", 3, 256, 1300.0),
    ("VFogDensity", 3, 256, 1800.0),
    ("VFogMax", 3, 256, 120.0),
    ("VFogMinYVal", 3, 256, 0.5),
    ("VFogMaxYVal", 3, 256, 1.0),
    ("DrawSpriteDist", 3, 512, 1500.0),
    ("DrawParticlesDist", 3, 512, 1500.0),
    ("DynamicLightDist", 3, 512, 1500.0),
    ("LockPVSRefresh", 6, 512, 100.0),
)

MM9_START_POINT_OBJECT_PROPERTIES: Tuple[Tuple[str, int, int, object], ...] = (
    ("Name", 0, 0, "StartPoint0"),
    ("Pos", 1, 0, (0.0, 0.0, 0.0)),
    ("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
    ("TeamNbr", 6, 0, 0.0),
    ("PlayerNbr", 6, 0, 0.0),
    ("MovePlayerToFloor", 5, 0, True),
)

MM9_LIGHT_OBJECT_PROPERTIES: Tuple[Tuple[str, int, int, object], ...] = (
    ("Name", 0, 0, "Light0"),
    ("Pos", 1, 0, (0.0, 0.0, 0.0)),
    ("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
    ("ClipLight", 5, 0, True),
    ("LightObjects", 5, 0, True),
    ("FastLightObjects", 5, 0, False),
    ("LightRadius", 3, 2, 800.0),
    ("LightColor", 2, 0, (255.0, 255.0, 255.0)),
    ("OuterColor", 2, 0, (0.0, 0.0, 0.0)),
    ("BrightScale", 3, 0, 1.0),
    ("Time", 3, 0, 0.0),
)

MM9_AIRAIL_OBJECT_PROPERTIES: Tuple[Tuple[str, int, int, object], ...] = (
    ("Name", 0, 0, "AIRail0"),
    ("Pos", 1, 0, (0.0, 0.0, 0.0)),
    ("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
    ("ScriptName", 0, 16384, ""),
    ("ScriptParams", 0, 0, ""),
    ("NeedsTick", 5, 0, False),
    ("Visible", 5, 0, False),
    ("OneWayOnly", 5, 0, False),
    ("IsLadder", 5, 0, False),
    ("UseRotation", 5, 0, False),
    ("NoRunZone", 5, 0, False),
    ("BoxPhysics", 5, 0, True),
    ("StartOn", 5, 0, True),
    ("RailLink0", 0, 0, ""),
    ("RailLink1", 0, 0, ""),
    ("RailLink2", 0, 0, ""),
    ("RailLink3", 0, 0, ""),
    ("ShowSurface", 5, 1, False),
    ("SpriteSurfaceName", 0, 1, ""),
    ("SurfaceColor1", 2, 1, (0.0, 0.0, 0.0)),
    ("SurfaceColor2", 2, 1, (0.0, 0.0, 0.0)),
    ("XScaleMin", 3, 1, 15.0),
    ("XScaleMax", 3, 1, 25.0),
    ("YScaleMin", 3, 1, 15.0),
    ("YScaleMax", 3, 1, 25.0),
    ("XScaleDuration", 3, 1, 10.0),
    ("YScaleDuration", 3, 1, 10.0),
    ("SurfaceHeight", 3, 1, 5.0),
    ("SurfaceAlpha", 3, 1, 0.7),
    ("NumSurfacePolies", 6, 1, 160.0),
    ("Viscosity", 3, 1, 0.0),
    ("Damage", 3, 1, 0.0),
    ("DamageType", 6, 1, 0.0),
    ("MoveToFloor", 5, 1, False),
    ("TouchNotify", 5, 0, False),
    ("UserData", 6, 0, 0.0),
)


@dataclass(frozen=True)
class LegacyEdSurface:
    vertex_indices: Tuple[int, ...]
    plane_normal: Vec3
    plane_dist: float
    texture_name: str = "Default"
    uv_o: Vec3 = (0.0, 0.0, 0.0)
    uv_p: Vec3 = (1.0, 0.0, 0.0)
    uv_q: Vec3 = (0.0, 0.0, 1.0)
    texture_flags: int = 0
    surface_flags: int = 0
    shade_rgb: Tuple[int, int, int] = (0, 0, 0)


@dataclass(frozen=True)
class LegacyEdBrush:
    points: Tuple[Vec3, ...]
    surfaces: Tuple[LegacyEdSurface, ...]
    color_rgb: Tuple[int, int, int] = (128, 128, 128)
    name: str = ""


@dataclass(frozen=True)
class LegacyEdObjectProperty:
    name: str
    type_code: int
    flags: int
    value: object


@dataclass(frozen=True)
class LegacyEdNodeItem:
    class_name: str = ""
    properties: Tuple[LegacyEdObjectProperty, ...] = ()
    node_id: int = 0
    unknown2: int = 0
    display_name: str = ""


@dataclass(frozen=True)
class LegacyEdNode:
    node_type: int = NODE_NODE
    item: LegacyEdNodeItem = field(default_factory=LegacyEdNodeItem)
    children: Tuple["LegacyEdNode", ...] = ()
    brush_index: int = 0


def build_raw_brush_stream(brushes: Sequence[LegacyEdBrush]) -> bytes:
    """Return ED v1249 plus raw brush records, matching the historical scanner."""
    out = bytearray()
    out.extend(struct.pack("<I", legacy_ed.LEGACY_ED_VERSION))
    for brush in brushes:
        out.extend(write_brush_record(brush))
    return bytes(out)


def build_direct_root_prefab(
    brushes: Sequence[LegacyEdBrush],
    *,
    brush_names: Sequence[str] = (),
) -> bytes:
    """Return the direct-root Brush-object prefab layout validated in old DEDit."""
    count = len(brushes)
    names = _brush_names(count, brush_names)
    out = bytearray()
    out.extend(struct.pack("<I", legacy_ed.LEGACY_ED_VERSION))
    out.extend(b"\x00" * 37)
    out.extend(struct.pack("<I", count))
    for brush in brushes:
        out.extend(write_brush_record(brush))
    out.extend(direct_root_node_intro(count))
    for index, name in enumerate(names):
        if index:
            out.extend(direct_root_between_brush_objects(index))
        out.extend(write_brush_object_record(name))
    out.extend(direct_root_tail(count))
    return bytes(out)


def build_node_hierarchy(root: LegacyEdNode) -> bytes:
    """Return a full-world EDUnpacker-style root node container.

    This writes only the node hierarchy stream.  A full `.ED` file still needs
    the v1249 header/wrapper and brush polyhedron stream before this hierarchy.
    """
    return write_node_container(root, include_entry=False)


def wrap_zlib_blocked_full_level(
    raw_ed_bytes: bytes,
    *,
    brush_count: int,
    infostring: Optional[str] = None,
    block_size: int = DEFAULT_FULL_LEVEL_ZLIB_BLOCK_SIZE,
    inner_suffix: bytes = b"",
) -> Tuple[bytes, Dict[str, int]]:
    """Wrap an ED v1249 raw brush stream in the observed full-level shell.

    ``raw_ed_bytes`` starts with the four-byte ED version and then contains
    brush records.  The decompressed full-level payload replaces that version
    with the declared brush count and may append a node hierarchy.  Metadata is
    returned so callers can report the exact wrapper they assembled.
    """
    if len(raw_ed_bytes) < 4:
        raise ValueError("raw legacy ED stream is too short to wrap")
    version = struct.unpack_from("<I", raw_ed_bytes, 0)[0]
    if version != legacy_ed.LEGACY_ED_VERSION:
        raise ValueError(
            f"raw legacy ED version {version} does not match {legacy_ed.LEGACY_ED_VERSION}"
        )
    if brush_count <= 0:
        raise ValueError("full-level ED wrapper requires at least one brush")
    if block_size <= 0:
        raise ValueError("full-level ED wrapper block size must be positive")

    inner_payload = struct.pack("<I", int(brush_count)) + raw_ed_bytes[4:] + bytes(inner_suffix)
    chunks = [
        inner_payload[offset:offset + block_size]
        for offset in range(0, len(inner_payload), block_size)
    ]
    compressed_chunks = [zlib.compress(chunk) for chunk in chunks]
    uncompressed_sizes = [len(chunk) for chunk in chunks]
    compressed_sizes = [len(chunk) for chunk in compressed_chunks]
    encoded_info = (
        DEFAULT_FULL_LEVEL_INFOSTRING if infostring is None else str(infostring)
    ).encode("latin1", errors="replace")
    if len(encoded_info) > 4096:
        raise ValueError("full-level ED infostring is too long")

    out = bytearray()
    out.extend(struct.pack("<I", legacy_ed.LEGACY_ED_VERSION))
    out.append(1)
    out.extend(struct.pack("<I", len(encoded_info)))
    out.extend(encoded_info)
    out.extend(b"\x00" * 32)
    out.extend(struct.pack("<I", len(chunks)))
    out.extend(struct.pack("<I", int(block_size)))
    for value in compressed_sizes:
        out.extend(struct.pack("<I", value))
    for value in uncompressed_sizes:
        out.extend(struct.pack("<I", value))
    for chunk in compressed_chunks:
        out.extend(chunk)

    return bytes(out), {
        "block_count": len(chunks),
        "block_size": int(block_size),
        "decompressed_byte_count": len(inner_payload),
        "compressed_byte_count": sum(compressed_sizes),
    }


def build_zlib_blocked_full_world(
    brushes: Sequence[LegacyEdBrush],
    root: LegacyEdNode,
    *,
    infostring: Optional[str] = None,
    block_size: int = DEFAULT_FULL_LEVEL_ZLIB_BLOCK_SIZE,
) -> Tuple[bytes, Dict[str, int]]:
    """Build a complete zlib-blocked ED v1249 world in memory."""
    if not brushes:
        raise ValueError("full-world ED requires at least one brush")
    raw = build_raw_brush_stream(brushes)
    hierarchy = build_node_hierarchy(root) + b"\x00" * 4
    return wrap_zlib_blocked_full_level(
        raw,
        brush_count=len(brushes),
        infostring=infostring,
        block_size=block_size,
        inner_suffix=hierarchy,
    )


def world_root_node(
    children: Sequence[LegacyEdNode],
    *,
    node_id: int = 1,
    display_name: str = "WorldRoot",
    unknown2: int = 0,
) -> LegacyEdNode:
    return LegacyEdNode(
        node_type=NODE_NODE,
        children=tuple(children),
        item=empty_node_item(node_id=node_id, display_name=display_name, unknown2=unknown2),
    )


def group_node(
    display_name: str,
    children: Sequence[LegacyEdNode],
    *,
    node_id: int,
    unknown2: int = 0,
) -> LegacyEdNode:
    return LegacyEdNode(
        node_type=NODE_NODE,
        children=tuple(children),
        item=empty_node_item(node_id=node_id, display_name=display_name, unknown2=unknown2),
    )


def brush_node(
    brush_index: int,
    brush_name: str,
    *,
    node_id: int,
    properties: Sequence[LegacyEdObjectProperty] = (),
    display_name: str = "",
) -> LegacyEdNode:
    props = tuple(properties) if properties else full_world_brush_node_properties(brush_name)
    return LegacyEdNode(
        node_type=NODE_BRUSH,
        brush_index=int(brush_index),
        children=(),
        item=LegacyEdNodeItem(
            class_name="Brush",
            properties=props,
            node_id=int(node_id),
            unknown2=0,
            display_name=str(display_name),
        ),
    )


def object_node(
    class_name: str,
    display_name: str,
    *,
    node_id: int,
    properties: Sequence[LegacyEdObjectProperty] = (),
    children: Sequence[LegacyEdNode] = (),
) -> LegacyEdNode:
    return LegacyEdNode(
        node_type=NODE_OBJECT,
        children=tuple(children),
        item=LegacyEdNodeItem(
            class_name=str(class_name),
            properties=tuple(properties),
            node_id=int(node_id),
            unknown2=0,
            display_name=str(display_name),
        ),
    )


def empty_node_item(*, node_id: int, display_name: str, unknown2: int = 0) -> LegacyEdNodeItem:
    return LegacyEdNodeItem(
        class_name="",
        properties=(),
        node_id=int(node_id),
        unknown2=int(unknown2),
        display_name=str(display_name),
    )


def write_node_container(node: LegacyEdNode, *, include_entry: bool = True) -> bytes:
    if len(node.children) > 0xFFFF:
        raise ValueError("legacy ED node container has too many children")
    out = bytearray()
    if include_entry:
        if node.node_type not in {NODE_NODE, NODE_BRUSH, NODE_OBJECT}:
            raise ValueError(f"unsupported legacy ED node type {node.node_type}")
        out.extend(struct.pack("<I", int(node.node_type)))
        if node.node_type == NODE_BRUSH:
            out.extend(struct.pack("<I", int(node.brush_index) & 0xFFFFFFFF))
    out.extend(struct.pack("<H", len(node.children)))
    for child in node.children:
        out.extend(write_node_container(child, include_entry=True))
    out.extend(write_node_item(node.item))
    return bytes(out)


def write_node_item(item: LegacyEdNodeItem) -> bytes:
    payload = bytearray()
    payload.extend(prefixed_string(item.class_name))
    payload.extend(struct.pack("<I", len(item.properties)))
    for prop in item.properties:
        payload.extend(write_object_property(prop))
    if len(payload) > 0xFFFF:
        raise ValueError("legacy ED node item payload is too large")
    out = bytearray()
    out.extend(struct.pack("<H", len(payload)))
    out.extend(payload)
    out.extend(struct.pack("<I", int(item.node_id) & 0xFFFFFFFF))
    out.extend(struct.pack("<I", int(item.unknown2) & 0xFFFFFFFF))
    out.extend(prefixed_string(item.display_name))
    return bytes(out)


def build_named_group_prefab(
    brushes: Sequence[LegacyEdBrush],
    *,
    group_name: str = "Group",
    brush_names: Sequence[str] = (),
) -> bytes:
    """Return the named null/group Brush-object prefab layout validated in DEDit."""
    count = len(brushes)
    names = _brush_names(count, brush_names)
    out = bytearray()
    out.extend(struct.pack("<I", legacy_ed.LEGACY_ED_VERSION))
    out.extend(b"\x00" * 37)
    out.extend(struct.pack("<I", count))
    for brush in brushes:
        out.extend(write_brush_record(brush))
    out.extend(named_group_node_intro(count))
    for index, name in enumerate(names):
        if index:
            out.extend(named_group_between_brush_objects(index, count))
        out.extend(write_brush_object_record(name))
    out.extend(named_group_tail(count, group_name or "Group"))
    return bytes(out)


def write_brush_record(brush: LegacyEdBrush) -> bytes:
    brush = normalize_brush_points(brush)
    points = tuple(_finite_vec3(point) for point in brush.points)
    if len(points) > 65535:
        raise ValueError("legacy ED brush point count exceeds uint16 index range")
    out = bytearray()
    out.extend(_rgb_bytes(brush.color_rgb))
    out.extend(struct.pack("<I", len(points)))
    for point in points:
        out.extend(struct.pack("<3f", *point))
    out.extend(struct.pack("<I", len(brush.surfaces)))
    for surface in brush.surfaces:
        out.extend(write_surface_record(surface, point_count=len(points)))
    return bytes(out)


def normalize_brush_points(
    brush: LegacyEdBrush,
    *,
    tolerance: float = DEFAULT_BRUSH_POINT_WELD_TOLERANCE,
) -> LegacyEdBrush:
    """Return a DEDit-like brush with coincident points welded and planes rebuilt.

    DEDit rewrites dirty worlds by welding per-brush points within roughly
    0.01 world units. Processor does not perform that normalization when it is
    handed a newly generated ED directly, and duplicate coordinates then cause
    large numbers of ``Unable to generate a plane`` warnings. Keep the first
    point in source order, remap every face, and recompute its plane from the
    welded geometry.
    """
    source_points = tuple(_finite_vec3(point) for point in brush.points)
    safe_tolerance = max(0.0, float(tolerance))
    welded_points, point_remap = _weld_points(source_points, safe_tolerance)

    normalized_surfaces: List[LegacyEdSurface] = []
    for surface_index, surface in enumerate(brush.surfaces):
        source_indices = tuple(int(index) for index in surface.vertex_indices)
        if any(index < 0 or index >= len(source_points) for index in source_indices):
            raise ValueError(
                f"legacy ED brush surface {surface_index} references a point outside the brush"
            )
        remapped = _collapse_polygon_index_loop(tuple(point_remap[index] for index in source_indices))
        remapped = _prefer_repeated_polygon_boundary(remapped)
        if len(remapped) < 3 or len(set(remapped)) < 3:
            raise ValueError(
                f"legacy ED brush surface {surface_index} became degenerate after point welding"
            )
        normal, distance = _polygon_plane(welded_points, remapped)
        normalized_surfaces.append(
            replace(
                surface,
                vertex_indices=remapped,
                plane_normal=normal,
                plane_dist=distance,
            )
        )

    used_indices = {
        index
        for surface in normalized_surfaces
        for index in surface.vertex_indices
    }
    if len(used_indices) != len(welded_points):
        compact_remap: Dict[int, int] = {}
        compact_points: List[Vec3] = []
        for old_index, point in enumerate(welded_points):
            if old_index not in used_indices:
                continue
            compact_remap[old_index] = len(compact_points)
            compact_points.append(point)
        welded_points = tuple(compact_points)
        normalized_surfaces = [
            replace(
                surface,
                vertex_indices=tuple(compact_remap[index] for index in surface.vertex_indices),
            )
            for surface in normalized_surfaces
        ]

    return replace(
        brush,
        points=tuple(welded_points),
        surfaces=tuple(normalized_surfaces),
    )


def write_surface_record(surface: LegacyEdSurface, *, point_count: int) -> bytes:
    indices = tuple(int(index) for index in surface.vertex_indices)
    if not (3 <= len(indices) <= 64):
        raise ValueError("legacy ED surface vertex count must be 3..64")
    if any(index < 0 or index >= int(point_count) for index in indices):
        raise ValueError("legacy ED surface references a point outside the brush")
    encoded_texture = str(surface.texture_name or "Default").encode("latin1", errors="replace")[:512]
    out = bytearray()
    out.extend(struct.pack("<I", len(indices)))
    out.extend(struct.pack("<" + "H" * len(indices), *indices))
    normal = _finite_vec3(surface.plane_normal)
    dist = _finite_float(surface.plane_dist)
    out.extend(struct.pack("<3ff", normal[0], normal[1], normal[2], dist))
    out.extend(struct.pack("<3f", *_finite_vec3(surface.uv_o)))
    out.extend(struct.pack("<3f", *_finite_vec3(surface.uv_p)))
    out.extend(struct.pack("<3f", *_finite_vec3(surface.uv_q)))
    out.extend(struct.pack("<I", int(surface.texture_flags) & 0xFFFFFFFF))
    out.extend(struct.pack("<H", len(encoded_texture)))
    out.extend(encoded_texture)
    out.extend(struct.pack("<I", int(surface.surface_flags) & 0xFFFFFFFF))
    out.extend(_rgb_bytes(surface.shade_rgb))
    return bytes(out)


def write_brush_object_record(brush_name: str) -> bytes:
    return write_object_record("Brush", brush_object_properties(brush_name))


def brush_object_properties(brush_name: str) -> Tuple[LegacyEdObjectProperty, ...]:
    return _properties_from_template(MM9_BRUSH_OBJECT_PROPERTIES, brush_name)


def full_world_brush_node_properties(brush_name: str) -> Tuple[LegacyEdObjectProperty, ...]:
    return _properties_from_template(MM9_FULL_WORLD_BRUSH_NODE_PROPERTIES, brush_name)


def world_properties_object_properties(
    *,
    name: str = "WorldProperties0",
    pos: Vec3 = (0.0, 0.0, 0.0),
) -> Tuple[LegacyEdObjectProperty, ...]:
    return _properties_from_template_values(
        MM9_WORLD_PROPERTIES_OBJECT_PROPERTIES,
        {"Name": str(name or "WorldProperties0"), "Pos": _finite_vec3(pos)},
    )


def start_point_object_properties(
    *,
    name: str = "StartPoint0",
    pos: Vec3 = (0.0, 0.0, 0.0),
    rotation: Quat = (0.0, 0.0, 0.0, 0.0),
) -> Tuple[LegacyEdObjectProperty, ...]:
    return _properties_from_template_values(
        MM9_START_POINT_OBJECT_PROPERTIES,
        {
            "Name": str(name or "StartPoint0"),
            "Pos": _finite_vec3(pos),
            "Rotation": _finite_quat(rotation),
        },
    )


def light_object_properties(
    *,
    name: str = "Light0",
    pos: Vec3 = (0.0, 0.0, 0.0),
    radius: float = 800.0,
    color_rgb: Vec3 = (255.0, 255.0, 255.0),
) -> Tuple[LegacyEdObjectProperty, ...]:
    return _properties_from_template_values(
        MM9_LIGHT_OBJECT_PROPERTIES,
        {
            "Name": str(name or "Light0"),
            "Pos": _finite_vec3(pos),
            "LightRadius": _finite_float(radius),
            "LightColor": _finite_vec3(color_rgb),
        },
    )


def airail_object_properties(
    *,
    name: str = "AIRail0",
    pos: Vec3 = (0.0, 0.0, 0.0),
    rotation: Quat = (0.0, 0.0, 0.0, 0.0),
    rail_links: Sequence[str] = (),
) -> Tuple[LegacyEdObjectProperty, ...]:
    links = [str(item or "") for item in tuple(rail_links)[:4]]
    while len(links) < 4:
        links.append("")
    return _properties_from_template_values(
        MM9_AIRAIL_OBJECT_PROPERTIES,
        {
            "Name": str(name or "AIRail0"),
            "Pos": _finite_vec3(pos),
            "Rotation": _finite_quat(rotation),
            "RailLink0": links[0],
            "RailLink1": links[1],
            "RailLink2": links[2],
            "RailLink3": links[3],
        },
    )


def _properties_from_template(
    template: Sequence[Tuple[str, int, int, object]],
    brush_name: str,
) -> Tuple[LegacyEdObjectProperty, ...]:
    return _properties_from_template_values(template, {"Name": brush_name})


def _properties_from_template_values(
    template: Sequence[Tuple[str, int, int, object]],
    overrides: dict,
) -> Tuple[LegacyEdObjectProperty, ...]:
    properties: List[LegacyEdObjectProperty] = []
    for name, type_code, flags, value in template:
        if name in overrides:
            value = overrides[name]
        properties.append(LegacyEdObjectProperty(name, type_code, flags, value))
    return tuple(properties)


def write_object_record(class_name: str, properties: Sequence[LegacyEdObjectProperty]) -> bytes:
    payload = bytearray()
    payload.extend(prefixed_string(class_name))
    payload.extend(struct.pack("<I", len(properties)))
    for prop in properties:
        payload.extend(write_object_property(prop))
    if len(payload) > 0xFFFF:
        raise ValueError(f"legacy {class_name!r} object property record is too large")
    return struct.pack("<H", len(payload)) + bytes(payload)


def write_object_property(prop: LegacyEdObjectProperty) -> bytes:
    encoded_value = property_value_bytes(prop.type_code, prop.value)
    if len(encoded_value) > 0xFFFF:
        raise ValueError(f"legacy property {prop.name!r} is too large")
    out = bytearray()
    out.extend(prefixed_string(prop.name))
    out.append(int(prop.type_code) & 0xFF)
    out.extend(struct.pack("<I", int(prop.flags) & 0xFFFFFFFF))
    out.extend(struct.pack("<H", len(encoded_value)))
    out.extend(encoded_value)
    return bytes(out)


def property_value_bytes(type_code: int, value: object) -> bytes:
    if type_code == 0:
        return prefixed_string(str(value))
    if type_code in (1, 2):
        return struct.pack("<3f", *_finite_vec3(value))
    if type_code in (3, 4, 6):
        return struct.pack("<f", _finite_float(value))
    if type_code == 5:
        return bytes([1 if bool(value) else 0])
    if type_code == 7:
        return struct.pack("<4f", *_finite_quat(value))
    raise ValueError(f"unsupported legacy property type code {type_code}")


def direct_root_node_intro(brush_count: int) -> bytes:
    count_byte = max(0, min(255, int(brush_count)))
    return bytes([count_byte]) + b"\x00\x01" + b"\x00" * 9


def direct_root_between_brush_objects(index: int) -> bytes:
    marker = 0x1994 + max(0, int(index))
    return struct.pack("<IIIII", marker, 0, 0x00010000, int(index) << 16, 0)


def direct_root_tail(brush_count: int) -> bytes:
    count = max(0, int(brush_count))
    if count == 1:
        return (
            struct.pack("<II", 0x1BAB, 0)
            + prefixed_string("Brush")
            + struct.pack("<IIII", 6, 0, 0x1B6A, 8)
            + b"\x00" * 6
        )
    marker = 0x1994 + count
    return (
        struct.pack("<IIII", marker, 0, 0x00060000, 0)
        + struct.pack("<HHHHII", 0, 0x1994, 0, 8, 0, 0)
    )


def named_group_node_intro(brush_count: int) -> bytes:
    count_byte = max(0, min(255, int(brush_count)))
    return b"\x01" + b"\x00" * 5 + bytes([count_byte]) + b"\x00\x01" + b"\x00" * 9


def named_group_between_brush_objects(index: int, brush_count: int) -> bytes:
    count = max(0, int(brush_count))
    child_index = max(0, int(index))
    marker_base = 0x1FE0
    if child_index == 1:
        marker = marker_base + count
    else:
        marker = marker_base + max(1, count - child_index)
    return struct.pack("<IIIII", marker, 0, 0x00010000, child_index << 16, 0)


def named_group_tail(brush_count: int, group_name: str) -> bytes:
    count = max(0, int(brush_count))
    marker_base = 0x1FE0
    brush_tail_marker = marker_base
    group_marker = marker_base + max(0, count - 1)
    root_marker = marker_base + count + 1
    return (
        struct.pack("<IIII", brush_tail_marker, 0, 0x00060000, 0)
        + b"\x00\x00"
        + struct.pack("<I", group_marker)
        + struct.pack("<I", 16)
        + prefixed_string(group_name)
        + struct.pack("<II", 6, 0)
        + struct.pack("<I", root_marker)
        + struct.pack("<I", 8)
        + b"\x00" * 6
    )


def prefixed_string(value: str, *, max_bytes: int = 4096) -> bytes:
    encoded = str(value).encode("latin1", errors="replace")[:max_bytes]
    return struct.pack("<H", len(encoded)) + encoded


def _brush_names(count: int, names: Sequence[str]) -> Tuple[str, ...]:
    result = [str(names[index]) if index < len(names) and names[index] else f"Brush{index}" for index in range(count)]
    return tuple(result)


def _weld_points(
    points: Sequence[Vec3],
    tolerance: float,
) -> Tuple[Tuple[Vec3, ...], Tuple[int, ...]]:
    if tolerance <= 0.0:
        point_to_index: Dict[Vec3, int] = {}
        result: List[Vec3] = []
        remap: List[int] = []
        for point in points:
            index = point_to_index.get(point)
            if index is None:
                index = len(result)
                point_to_index[point] = index
                result.append(point)
            remap.append(index)
        return tuple(result), tuple(remap)

    cells: Dict[Tuple[int, int, int], List[int]] = {}
    result: List[Vec3] = []
    remap: List[int] = []
    tolerance_sq = tolerance * tolerance
    for point in points:
        cell = tuple(int(math.floor(value / tolerance)) for value in point)
        matches: List[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    matches.extend(cells.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), ()))
        match = next(
            (
                index
                for index in sorted(matches)
                if sum((point[axis] - result[index][axis]) ** 2 for axis in range(3))
                <= tolerance_sq
            ),
            None,
        )
        if match is None:
            match = len(result)
            result.append(point)
            cells.setdefault(cell, []).append(match)
        remap.append(match)
    return tuple(result), tuple(remap)


def _collapse_polygon_index_loop(indices: Sequence[int]) -> Tuple[int, ...]:
    result: List[int] = []
    for index in indices:
        if not result or result[-1] != int(index):
            result.append(int(index))
    if len(result) > 1 and result[0] == result[-1]:
        result.pop()
    return tuple(result)


def _prefer_repeated_polygon_boundary(indices: Sequence[int]) -> Tuple[int, ...]:
    """Match DEDit's cleanup of concatenated compiled-BSP boundary paths.

    Some MM9 DAT faces encode a coarse boundary and a refined boundary in one
    vertex list. After point welding, the true corner indices occur in both
    paths while refinement-only residue occurs once. DEDit removes that residue
    when a dirty ED is saved; retaining it makes Processor reject the brush
    plane on the first run.
    """
    counts: Dict[int, int] = {}
    for index in indices:
        counts[int(index)] = counts.get(int(index), 0) + 1
    repeated = tuple(int(index) for index in indices if counts[int(index)] > 1)
    if len(repeated) >= 3 and len(set(repeated)) >= 3:
        return repeated
    return tuple(int(index) for index in indices)


def _polygon_plane(points: Sequence[Vec3], indices: Sequence[int]) -> Tuple[Vec3, float]:
    first = points[int(indices[0])]
    for offset in range(1, len(indices) - 1):
        second = points[int(indices[offset])]
        third = points[int(indices[offset + 1])]
        ux, uy, uz = (
            second[0] - first[0],
            second[1] - first[1],
            second[2] - first[2],
        )
        vx, vy, vz = (
            third[0] - first[0],
            third[1] - first[1],
            third[2] - first[2],
        )
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length <= 1.0e-7:
            continue
        normal = (nx / length, ny / length, nz / length)
        return normal, (
            normal[0] * first[0]
            + normal[1] * first[1]
            + normal[2] * first[2]
        )
    raise ValueError("legacy ED surface has no stable plane after point welding")


def _finite_vec3(value: object) -> Vec3:
    try:
        x, y, z = value  # type: ignore[misc]
        result = (float(x), float(y), float(z))
    except Exception:
        return (0.0, 0.0, 0.0)
    if not all(math.isfinite(item) for item in result):
        return (0.0, 0.0, 0.0)
    return result


def _finite_quat(value: object) -> Quat:
    try:
        x, y, z, w = value  # type: ignore[misc]
        result = (float(x), float(y), float(z), float(w))
    except Exception:
        return (0.0, 0.0, 0.0, 0.0)
    if not all(math.isfinite(item) for item in result):
        return (0.0, 0.0, 0.0, 0.0)
    return result


def _finite_float(value: object) -> float:
    try:
        result = float(value)
    except Exception:
        return 0.0
    return result if math.isfinite(result) else 0.0


def _rgb_bytes(value: Iterable[int]) -> bytes:
    items = list(value)
    padded = (items + [0, 0, 0])[:3]
    return bytes(max(0, min(255, int(item))) for item in padded)
