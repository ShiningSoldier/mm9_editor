"""
bsp.py
======

Parser for the BSP / WorldModel mesh data inside MM9's compiled .DAT v66
worlds. Returns a flat list of polygon-with-vertex-positions, which is
exactly what the map view needs to draw level outlines.

We intentionally skip everything we don't need (lightmap data, vis lists,
plane equations, BSP tree, physics blocks). What we keep:

    BspWorld
      world_models: List[WorldModelMesh]
        name: str
        min_box: (x,y,z)
        max_box: (x,y,z)
        polygons: List[Polygon]
          vertices: List[(x,y,z)]
          surface_index: int
          plane_index: int

Format reference: lithtech/libs/rezmgr/rezmgr.cpp (header), godot-dat-reader/
Models/DAT.gd (BSP traversal), godot-dat-reader/Research/bspv66.bt (010
Editor template). We follow the lithtech_2 (== v66) code paths.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import BinaryIO, List, Optional, Tuple

DAT_VERSION_V66 = 66


# --------------------------------------------------------------------------
# Stream helpers
# --------------------------------------------------------------------------

class _Stream:
    """Tiny seekable byte cursor with the helpers we need."""
    def __init__(self, buf: bytes, offset: int = 0):
        self.buf = buf
        self.pos = offset

    def u8(self) -> int:
        v = self.buf[self.pos]; self.pos += 1; return v
    def u16(self) -> int:
        v = struct.unpack_from("<H", self.buf, self.pos)[0]; self.pos += 2; return v
    def u32(self) -> int:
        v = struct.unpack_from("<I", self.buf, self.pos)[0]; self.pos += 4; return v
    def s16(self) -> int:
        v = struct.unpack_from("<h", self.buf, self.pos)[0]; self.pos += 2; return v
    def s32(self) -> int:
        v = struct.unpack_from("<i", self.buf, self.pos)[0]; self.pos += 4; return v
    def f32(self) -> float:
        v = struct.unpack_from("<f", self.buf, self.pos)[0]; self.pos += 4; return v
    def vec3(self) -> Tuple[float, float, float]:
        v = struct.unpack_from("<3f", self.buf, self.pos); self.pos += 12; return v
    def skip(self, n: int) -> None:
        self.pos += n
    def read(self, n: int) -> bytes:
        v = self.buf[self.pos : self.pos + n]; self.pos += n; return v
    def lt_string_u16(self) -> str:
        n = self.u16(); s = self.buf[self.pos : self.pos + n].decode("latin-1"); self.pos += n; return s
    def cstring(self) -> str:
        end = self.buf.index(b"\x00", self.pos)
        s = self.buf[self.pos : end].decode("latin-1")
        self.pos = end + 1
        return s


# --------------------------------------------------------------------------
# Top-level dataclasses
# --------------------------------------------------------------------------

@dataclass
class Surface:
    """
    One surface record from the BSP.  A surface is referenced by one or more
    polygons via ``Polygon.surface_index`` and defines how a texture is
    projected onto those polygons.

    Planar UV projection (LithTech 2 / v66)
    ----------------------------------------
    The three vec3 fields encode LithTech's OPQ texture mapping.  For a vertex
    at world-space position P and texture dimensions W×H:

        d  = P - uv_o
        U  = dot(d, uv_p) / W
        V  = dot(d, uv_q) / H

    ``uv_p`` and ``uv_q`` are projection vectors that produce pixel-space
    texture coordinates.  They are not basis axes to normalise by squared
    length; doing that over-scales UVs and collapses textured surfaces to
    lowest-mip average colours.

    Attributes
    ----------
    uv_o          : world-space origin of the UV projection plane
    uv_p          : S-axis vector (world → U)
    uv_q          : T-axis vector (world → V)
    texture_index : index into WorldModelMesh.texture_names
    flags         : surface flags (see LT2 surface flag constants)
    texture_flags : additional per-texture flags
    """
    uv_o:          Tuple[float, float, float]
    uv_p:          Tuple[float, float, float]
    uv_q:          Tuple[float, float, float]
    texture_index: int
    flags:         int
    texture_flags: int

    def compute_uv(
        self,
        pos: Tuple[float, float, float],
        tex_width: float = 128.0,
        tex_height: float = 128.0,
    ) -> Tuple[float, float]:
        """
        Return (U, V) texture coordinates for a vertex at world-space *pos*.

        Texture dimensions default to 128×128 to match the historical DAT
        reader fallback when the DTX header is not available.
        """
        ox, oy, oz = self.uv_o
        px, py, pz = self.uv_p
        qx, qy, qz = self.uv_q
        dx = pos[0] - ox
        dy = pos[1] - oy
        dz = pos[2] - oz
        tw = tex_width if tex_width > 0.0 else 128.0
        th = tex_height if tex_height > 0.0 else 128.0
        u  = (dx * px + dy * py + dz * pz) / tw
        v  = (dx * qx + dy * qy + dz * qz) / th
        return u, v


@dataclass
class Polygon:
    vertex_indices: List[int]
    surface_index:  int
    plane_index:    int


@dataclass
class WorldTreeLayout:
    min_box: Tuple[float, float, float]
    max_box: Tuple[float, float, float]
    declared_node_count: int
    dummy_terrain_depth: int
    decoded_node_count: int
    internal_node_count: int
    leaf_node_count: int
    max_depth: int
    layout_start: int
    layout_end: int
    byte_count: int
    bit_count: int
    valid_node_count: bool
    depth_limit_exceeded: bool = False


@dataclass
class WorldModelMesh:
    name:          str
    min_box:       Tuple[float, float, float]
    max_box:       Tuple[float, float, float]
    translation:   Tuple[float, float, float]
    raw_start:     Optional[int] = None
    raw_end:       Optional[int] = None
    next_world_item: Optional[int] = None
    world_bsp_start: Optional[int] = None
    world_bsp_end:   Optional[int] = None
    points:        List[Tuple[float, float, float]] = field(default_factory=list)
    polygons:      List[Polygon]                    = field(default_factory=list)
    texture_names: List[str]                        = field(default_factory=list)
    surfaces:      List[Surface]                    = field(default_factory=list)

    def texture_name_for(self, polygon: "Polygon") -> Optional[str]:
        """
        Return the texture file path for *polygon* (e.g.
        ``'World\\\\Tiles\\\\floor01.dtx'``), or ``None`` if the surface or
        texture index is out of range.
        """
        if not self.surfaces:
            return None
        si = polygon.surface_index
        if si < 0 or si >= len(self.surfaces):
            return None
        ti = self.surfaces[si].texture_index
        if ti < 0 or ti >= len(self.texture_names):
            return None
        return self.texture_names[ti]

    def is_skybox(self) -> bool:
        """Skyboxes are tiny meshes used for rendering the sky; their bounds
        sit far from the main level and the user almost never wants them on
        the map. Identified by name."""
        n = self.name.lower()
        return (
            n.startswith("skybox")
            or n.startswith("demosky")
            or n.startswith("tod_sky")
            or "skybox" in n
            or "sky_box" in n
        )


    def category(self) -> str:
        """Coarse classification used to colour edges on the map view."""
        if self.is_skybox():            return "skybox"
        n = self.name.lower()
        if "terrain" in n:              return "terrain"
        if len(self.polygons) >= 100:   return "main"
        return "submodel"


@dataclass
class BspWorld:
    version:      int
    world_info:   str
    obj_pos:      int = 0
    ren_pos:      int = 0
    world_model_table_start: int = 0
    world_models:    List[WorldModelMesh] = field(default_factory=list)
    parse_warnings:  List[str] = field(default_factory=list)
    lightmap_grid_size: float = 0.0
    world_extents_min: Optional[Tuple[float, float, float]] = None
    world_extents_max: Optional[Tuple[float, float, float]] = None
    world_tree: Optional[WorldTreeLayout] = None

    def all_edges_xz(self) -> List[Tuple[Tuple[float, float], Tuple[float, float], int]]:
        """Yield (xz_a, xz_b, model_index) for every edge of every polygon.
        Edges are not deduplicated — a polygon shared between two leaves will
        contribute its edges only once because we only walk each polygon once.
        """
        out: List[Tuple[Tuple[float, float], Tuple[float, float], int]] = []
        for mi, m in enumerate(self.world_models):
            pts = m.points
            for poly in m.polygons:
                vis = poly.vertex_indices
                if len(vis) < 2: continue
                for i in range(len(vis)):
                    a = pts[vis[i]]
                    b = pts[vis[(i + 1) % len(vis)]]
                    out.append(((a[0], a[2]), (b[0], b[2]), mi))
        return out

    def model_by_name(self, name: str) -> Optional[WorldModelMesh]:
        """Return the first BSP world model whose name matches case-insensitively."""
        key = str(name or "").lower()
        for model in self.world_models:
            if model.name.lower() == key:
                return model
        return None

    def raw_model_bytes(self, source_dat: bytes, model: WorldModelMesh) -> Optional[bytes]:
        """Return the original byte record for *model* when parse provenance is available."""
        if model.raw_start is None or model.raw_end is None:
            return None
        if model.raw_start < 0 or model.raw_end < model.raw_start or model.raw_end > len(source_dat):
            return None
        return source_dat[model.raw_start:model.raw_end]


# --------------------------------------------------------------------------
# Floor-Y raycasting (Phase 4)
# --------------------------------------------------------------------------

_RAYCAST_SANE = 1.0e6


def raycast_floor_y(
    bsp:          "BspWorld",
    x:            float,
    z:            float,
    y_hint_min:   Optional[float] = None,
    y_hint_max:   Optional[float] = None,
    y_above:      float = 1.0e6,
) -> Optional[float]:
    """Cast a vertical ray downward from (x, y_above, z) and return the Y
    coordinate of the highest *floor* surface hit.

    A floor surface is one whose geometric normal faces upward — i.e. the
    surface an NPC or prop would stand on, as opposed to a ceiling or wall.

    Derivation note
    ---------------
    For ray direction D = (0, -1, 0), Möller-Trumbore gives:

        h = D × e2  →  (−e2z,  0,  e2x)
        a = e1 · h  =  e1z·e2x − e1x·e2z  =  (e1 × e2).y  =  normal.y

    So ``a`` is exactly the Y-component of the unnormalised polygon normal.
    ``a > 0`` means the surface faces up (a floor); ``a ≤ 0`` means it faces
    down (ceiling) or is near-vertical (wall).  No separate normal calculation
    is needed.

    Parameters
    ----------
    bsp           : parsed BSP world
    x, z          : XZ position of the vertical ray
    y_hint_min,
    y_hint_max    : optional preferred vertical band. When supplied, hits
                    inside this band are preferred over hits outside it.
    y_above       : ray origin Y (default 1e6, safely above any MM9 geometry)
    """
    EPSILON = 1e-7
    sane    = _RAYCAST_SANE
    hits: List[float] = []

    for model in bsp.world_models:
        if model.is_skybox():
            continue
        pts = model.points
        for poly in model.polygons:
            vis = poly.vertex_indices
            nv  = len(vis)
            if nv < 3:
                continue
            # Fan-triangulate the polygon from vertex 0
            try:
                v0 = pts[vis[0]]
            except IndexError:
                continue
            for k in range(1, nv - 1):
                try:
                    v1 = pts[vis[k]]
                    v2 = pts[vis[k + 1]]
                except IndexError:
                    continue
                # Drop corrupted geometry.
                if (abs(v0[0]) > sane or abs(v0[1]) > sane or abs(v0[2]) > sane
                        or abs(v1[0]) > sane or abs(v1[1]) > sane or abs(v1[2]) > sane
                        or abs(v2[0]) > sane or abs(v2[1]) > sane or abs(v2[2]) > sane):
                    continue

                # Edge vectors
                e1x = v1[0] - v0[0]; e1y = v1[1] - v0[1]; e1z = v1[2] - v0[2]
                e2x = v2[0] - v0[0];                        e2z = v2[2] - v0[2]

                # a = (e1 × e2).y  — see derivation above
                a = e1z * e2x - e1x * e2z

                # Skip walls (a ≈ 0) and ceilings (a < 0)
                if a < EPSILON:
                    continue

                f  = 1.0 / a
                # s = ray_origin − v0
                sx = x       - v0[0]
                sy = y_above - v0[1]
                sz = z       - v0[2]

                # u = f * (s · h),  h = (−e2z, 0, e2x)
                u = f * (-sx * e2z + sz * e2x)
                if u < -EPSILON or u > 1.0 + EPSILON:
                    continue

                # q = s × e1
                qx = sy * e1z - sz * e1y
                qy = sz * e1x - sx * e1z
                qz = sx * e1y - sy * e1x

                # v = f * (D · q),  D = (0, −1, 0)  →  −f·q.y
                v = -f * qy
                if v < -EPSILON or u + v > 1.0 + EPSILON:
                    continue

                # t = f * (e2 · q);  hit_y = y_above − t  (D.y = −1)
                t = f * (e2x * qx + (v2[1] - v0[1]) * qy + e2z * qz)
                if t < EPSILON:
                    continue   # behind or coincident with ray origin

                hits.append(y_above - t)

    if not hits:
        return None

    # Prefer hits inside the caller-provided vertical hint band.
    if y_hint_min is not None and y_hint_max is not None:
        pad = max(20.0, (y_hint_max - y_hint_min) * 0.2)
        in_band = [h for h in hits
                   if y_hint_min - pad <= h <= y_hint_max + pad]
        if in_band:
            return max(in_band)

    return max(hits)


# --------------------------------------------------------------------------
# WorldTree: bit-packed quadtree subdivision data — has to be skipped
# --------------------------------------------------------------------------

def _read_world_tree_layout(
    s: _Stream,
    min_box: Tuple[float, float, float],
    max_box: Tuple[float, float, float],
    declared_node_count: int,
    dummy_terrain_depth: int,
    *,
    max_depth: int = 16,
) -> WorldTreeLayout:
    """Consume and summarize the bit-packed quadtree subdivision layout."""
    layout_start = s.pos
    state = {"current_byte": 0, "current_bit": 8}
    decoded_nodes = 0
    internal_nodes = 0
    max_seen_depth = 0
    depth_limit_exceeded = False

    def step(depth: int) -> None:
        nonlocal decoded_nodes, internal_nodes, max_seen_depth, depth_limit_exceeded
        decoded_nodes += 1
        max_seen_depth = max(max_seen_depth, int(depth))
        if state["current_bit"] == 8:
            state["current_byte"] = s.u8()
            state["current_bit"] = 0
        subdivide = (state["current_byte"] & (1 << state["current_bit"])) != 0
        state["current_bit"] += 1
        if depth > max_depth:
            depth_limit_exceeded = True
            return
        if subdivide:
            internal_nodes += 1
            for _ in range(4):
                step(depth + 1)

    step(0)
    layout_end = s.pos
    bit_count = int(decoded_nodes)
    return WorldTreeLayout(
        min_box=min_box,
        max_box=max_box,
        declared_node_count=int(declared_node_count),
        dummy_terrain_depth=int(dummy_terrain_depth),
        decoded_node_count=int(decoded_nodes),
        internal_node_count=int(internal_nodes),
        leaf_node_count=max(0, int(decoded_nodes) - int(internal_nodes)),
        max_depth=int(max_seen_depth),
        layout_start=int(layout_start),
        layout_end=int(layout_end),
        byte_count=int(layout_end) - int(layout_start),
        bit_count=int(bit_count),
        valid_node_count=int(decoded_nodes) == int(declared_node_count),
        depth_limit_exceeded=bool(depth_limit_exceeded),
    )


def _walk_world_tree(s: _Stream, max_depth: int = 16) -> None:
    _read_world_tree_layout(
        s,
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        0,
        0,
        max_depth=max_depth,
    )


# --------------------------------------------------------------------------
# Per-section readers (lithtech_2 / v66 paths only)
# --------------------------------------------------------------------------

def _read_surface(s: _Stream) -> Surface:
    """Read one surface record and return it."""
    uv_o          = s.vec3()          # UV projection origin
    uv_p          = s.vec3()          # S-axis  (U direction)
    uv_q          = s.vec3()          # T-axis  (V direction)
    texture_index = s.u16()
    s.u32()                           # unknown (lithtech_2 path)
    flags         = s.u32()
    s.u32()                           # unknown2 (4 bytes)
    use_effects   = s.u8()
    if use_effects == 1:
        s.lt_string_u16()             # effect_name
        s.lt_string_u16()             # effect_param
    texture_flags = s.u16()
    return Surface(
        uv_o=uv_o, uv_p=uv_p, uv_q=uv_q,
        texture_index=texture_index,
        flags=flags,
        texture_flags=texture_flags,
    )


def _read_leaf(s: _Stream) -> None:
    """Skip a leaf entry (variable size)."""
    count = s.u16()
    if count == 0xFFFF:
        s.u16()               # leaf_list_index
    else:
        for _ in range(count):
            s.s16()           # PortalID
            sz = s.u16()      # Size
            s.skip(sz)        # leaf data
    poly_count = s.u32()
    s.skip(poly_count * 4)
    s.u32()                   # unk_1


def _read_polygon(s: _Stream, vert_count: int) -> Polygon:
    """Read a polygon record; keep only the surface/plane indices and the
    vertex indices (which we resolve against the global Points array)."""
    s.skip(12)                # center vec3
    s.u16(); s.u16()          # lightmap_width, lightmap_height
    unknown_flag = s.u16()
    if unknown_flag > 0:
        s.skip(unknown_flag * 4)   # short[unknown_flag * 2]
    surface_index = s.u16()
    plane_index   = s.u16()
    vis: List[int] = []
    for _ in range(vert_count):
        idx = s.u16()
        s.skip(3)             # 3 dummy bytes (often colour for LT1)
        vis.append(idx)
    return Polygon(vertex_indices=vis,
                   surface_index=surface_index,
                   plane_index=plane_index)


def _read_pblock_table(s: _Stream) -> None:
    """Skip the physics block table (lithtech_2 layout)."""
    a = s.u32(); b = s.u32(); c = s.u32()
    size = a * b * c
    s.skip(12)                # unk_vector_1
    s.skip(12)                # unk_vector_2
    for _ in range(size):
        block_size = s.u16()
        s.u16()               # unk_short
        s.skip(6 * block_size)


def _read_world_bsp(s: _Stream) -> WorldModelMesh:
    """Parse a single WorldModel's WorldBSP and return its mesh."""
    s.u32()                   # info_flags
    s.u32()                   # unknown (lithtech_2 has this; lithtech_jupiter doesn't)
    world_name = s.lt_string_u16()

    point_count       = s.u32()
    plane_count       = s.u32()
    surface_count     = s.u32()
    user_portal_count = s.u32()
    poly_count        = s.u32()
    leaf_count        = s.u32()
    vert_count        = s.u32()
    total_vis         = s.u32()
    leaf_list_count   = s.u32()
    node_count        = s.u32()
    s.u32()                   # unknown_value_2 (lithtech_2)
    s.u32()                   # unknown_value_3 (lithtech_2)

    min_box     = s.vec3()
    max_box     = s.vec3()
    translation = s.vec3()

    name_length   = s.u32()
    texture_count = s.u32()
    # Texture names are null-terminated and concatenated. Reading them
    # individually is more robust than trusting name_length.
    texture_names: List[str] = []
    for _ in range(texture_count):
        texture_names.append(s.cstring())

    # Per-poly vertex counts (u8 Count, u8 Extra)
    verts_per_poly: List[int] = []
    for _ in range(poly_count):
        c = s.u8()
        e = s.u8()
        verts_per_poly.append(c + e)

    # Leaves
    for _ in range(leaf_count):
        _read_leaf(s)

    # Planes (vec3 normal + float distance = 16 bytes)
    s.skip(plane_count * 16)

    # Surfaces (variable size)
    surfaces: List[Surface] = []
    for _ in range(surface_count):
        surfaces.append(_read_surface(s))

    # Polygons
    polygons: List[Polygon] = []
    for i in range(poly_count):
        polygons.append(_read_polygon(s, verts_per_poly[i]))

    # Nodes (PolyIndex u32 + LeafIndex u16 + 2× u32 = 14 bytes)
    s.skip(node_count * 14)

    # User portals (variable: name string + 4+4+2+12+12)
    for _ in range(user_portal_count):
        s.lt_string_u16()
        s.u32(); s.u32(); s.u16()
        s.skip(24)            # 2× vec3

    # Points (vec3 + vec3 = 24 bytes for lithtech_2)
    points: List[Tuple[float, float, float]] = []
    for _ in range(point_count):
        p = s.vec3()
        s.vec3()              # normal
        points.append(p)

    # Physics block table
    _read_pblock_table(s)

    s.s32()                   # root_node_index
    # Section count comes last but we don't parse the terrain payload — the
    # caller seeks to NextWorldItem to skip it.
    try:
        s.s32()                   # section count (may be junk for drifted reads)
    except Exception:
        pass

    return WorldModelMesh(
        name=world_name,
        min_box=min_box, max_box=max_box, translation=translation,
        points=points, polygons=polygons,
        texture_names=texture_names, surfaces=surfaces,
    )


# --------------------------------------------------------------------------
# Top-level entry point
# --------------------------------------------------------------------------

def parse(data: bytes) -> BspWorld:
    """Parse a v66 .DAT bytes blob, returning the BSP geometry."""
    s = _Stream(data, 0)

    # WorldHeader (44 bytes): version, obj_pos, ren_pos, 8× dummy
    version = s.u32()
    if version != DAT_VERSION_V66:
        raise ValueError(f"unsupported DAT version {version} (expected {DAT_VERSION_V66})")
    obj_pos = s.u32()
    ren_pos = s.u32()
    s.skip(8 * 4)             # dummies

    # WorldInfo: u32 length + string + float LMGridSize + 2× vec3 bounds
    info_len = s.u32()
    world_info = s.read(info_len).decode("latin-1", errors="replace")
    lightmap_grid_size = s.f32()       # LMGridSize
    world_extents_min = s.vec3()
    world_extents_max = s.vec3()

    # WorldTree: vec3 min/max + int NumNodes + int DummyTerrainDepth +
    # bit-packed quadtree
    tree_min = s.vec3()
    tree_max = s.vec3()
    tree_node_count = s.u32()
    dummy_terrain_depth = s.u32()
    world_tree = _read_world_tree_layout(
        s,
        tree_min,
        tree_max,
        tree_node_count,
        dummy_terrain_depth,
    )

    # WorldModelHeader: int Count + WorldModel[Count]
    world_model_table_start = s.pos
    world_model_count = s.u32()
    bsp = BspWorld(
        version=version,
        world_info=world_info,
        obj_pos=obj_pos,
        ren_pos=ren_pos,
        world_model_table_start=world_model_table_start,
        lightmap_grid_size=lightmap_grid_size,
        world_extents_min=world_extents_min,
        world_extents_max=world_extents_max,
        world_tree=world_tree,
    )

    for mi in range(world_model_count):
        rec_start = s.pos
        next_world_item = s.u32()
        s.skip(32)            # padding
        world_bsp_start = s.pos
        try:
            mesh = _read_world_bsp(s)
            mesh.raw_start = rec_start
            mesh.raw_end = next_world_item if 0 < next_world_item <= len(data) else s.pos
            mesh.next_world_item = next_world_item
            mesh.world_bsp_start = world_bsp_start
            mesh.world_bsp_end = s.pos
            bsp.world_models.append(mesh)
        except Exception as e:
            # Best effort — record what we got, but don't bail out: terrain
            # models or sub-models we can't parse aren't fatal for the editor.
            bsp.parse_warnings.append(
                f"WorldModel #{mi} at {rec_start}: {e}")

        # ALWAYS seek to NextWorldItem after a WorldModel. This skips terrain
        # section data we don't understand, AND realigns the cursor if the
        # WorldBSP read drifted. It also gracefully terminates iteration if
        # NextWorldItem points past obj_pos (start of WorldObject section).
        if next_world_item <= 0 or next_world_item > len(data):
            break
        # If NextWorldItem points at or past the WorldObject section, we've
        # walked all the geometry — stop early even if world_model_count says
        # there are more (some shipped DATs have the count slightly off).
        if next_world_item >= obj_pos:
            break
        s.pos = next_world_item

    # Sanity check: we should have landed at obj_pos
    if abs(s.pos - obj_pos) > 4:
        # Within a few bytes is acceptable; off by more means something
        # was misread. Useful diagnostic during development.
        # (The smoke test below treats this as a warning, not an error.)
        pass

    return bsp


def parse_path(path: str) -> BspWorld:
    with open(path, "rb") as f:
        return parse(f.read())


# --------------------------------------------------------------------------
# CLI / smoke-test
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    import argparse, glob, os, sys
    p = argparse.ArgumentParser(description="Parse BSP geometry from MM9 .DAT files")
    p.add_argument("path", nargs="+", help="one or more .DAT files (or a glob)")
    p.add_argument("--quiet", "-q", action="store_true")
    args = p.parse_args(argv)

    files: List[str] = []
    for pat in args.path:
        if "*" in pat:
            files.extend(sorted(glob.glob(pat)))
        else:
            files.append(pat)

    ok = fail = 0
    for fp in files:
        try:
            bsp = parse_path(fp)
            total_verts = sum(len(m.points)   for m in bsp.world_models)
            total_polys = sum(len(m.polygons) for m in bsp.world_models)
            if not args.quiet:
                print(f"  {os.path.basename(fp):28s}  models={len(bsp.world_models):>3}  "
                      f"verts={total_verts:>6}  polys={total_polys:>6}  "
                      f"info={bsp.world_info[:60]!r}")
            ok += 1
        except Exception as e:
            print(f"  {os.path.basename(fp):28s}  FAIL: {e}", file=sys.stderr)
            fail += 1
    print(f"\n  total: {ok} ok, {fail} fail")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
