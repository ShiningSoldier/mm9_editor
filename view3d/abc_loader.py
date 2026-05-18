"""
abc_loader.py
=============

Binary loader for LithTech ABC model files (MM9 era, PC format).

ABC format — confirmed reverse-engineering findings
----------------------------------------------------
The file is a sequence of *named blocks*, each written by save_StartSection():

    uint16  name_len
    char[]  name  (name_len bytes, ASCII)
    uint32  next_sibling_offset  (file offset of next block; 0xFFFFFFFF = none)
    [data...]

Top-level block names: Header, Pieces, Nodes, ChildModels, Animation,
Sockets, AnimBindings.

Header block layout
~~~~~~~~~~~~~~~~~~~
    uint32  version  (= 13)
    uint32 × 14  ModelAllocations fields:
        nKeyFrames, nParentAnims, nNodes, nPieces, nChildModels, nTris,
        nVerts, nVertWeights, nLODs, nSockets, nWeightSets, nStrings,
        StringLengths, VertAnimDataSize
    uint16+chars  CommandString  (WriteString format)
    float32  GlobalRadius  (= VisRadius, e.g. 96.0)
    uint32  n_obbs  (usually 0)
    [per-OBB: float[3] pos + float[3] size + float[3][3] basis + uint32 node_idx + float radius]
    [60 zero bytes  — nNodes × 30-byte placeholder structures]

Pieces block layout  (this module only)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    uint32  nVerts_total
    uint32  nPieces
    uint32  0
    12 bytes  (format constant, meaning TBD)
    [for each piece:]
        uint16 + chars  piece_name    (WriteString)
        [for each LOD:]
            uint32          nTris
            nTris × 30 bytes  triangle data:
                [for each of 3 vertex-refs per triangle:]
                    float32  U  (texture coord)
                    float32  V  (texture coord)
                    uint16   vertex_index
            uint32          nVerts
            nVerts_lod × variable-length vertex data:
                uint16  n_weights
                uint16  weight_index / flags
                n_weights ×:
                    uint32  bone_index
                    float32 x, y, z    (position for this weighted copy)
                    float32 weight
                float32 x, y, z    (saved model-space vertex position)
                float32 nx, ny, nz  (saved model-space normal)
        [0-47 trailing bytes before next piece in some files]

IMPORTANT LIMITATIONS
~~~~~~~~~~~~~~~~~~~~~
This loader handles rigid/static single-piece and multi-piece models with one
or more LODs.  For multi-LOD rigid props it validates the full LOD chain but
returns only LOD0 (the highest-detail mesh) for rendering.  For top-level
animated character/creature previews it can fall back to a relaxed LOD0-only
parse when later LOD totals do not match the rigid prop layout.

It also parses the model node tree and the old MM9 uncompressed animation
node layout used by many NPCs.  A conservative, opt-in static-pose bake
helper exists for character previews.  The normal prop path leaves vertices
unbaked; the viewer enables the bake for top-level animated character/NPC
models so their bone-local vertices are shown in a usable static pose.

Ambiguous piece boundaries and unsupported animation/skinning variants are
intentionally skipped (load_abc returns None).  They remain visible through the
editor's billboard fallback.

Usage
-----
    from view3d.abc_loader import load_abc, AbcModel, AbcPiece
    model = load_abc("path/to/model.abc")
    if model:
        for piece in model.pieces:
            print(piece.name, len(piece.vertices), len(piece.triangles))

See upload_abc_model() in gl_mesh.py (or call it directly here) to upload
to the GPU.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AbcVertex:
    """
    Single vertex from an ABC piece.
    ``bone_index`` is the 0-based node index.  Known rigid prop files store
    this as 1-based; top-level animated character files store it as 0-based.
    ``pos`` is the currently selected draw position. For ordinary props this is
    the first bone-local weight position; for static character previews it can
    be replaced by ``saved_pos``. ``weights`` preserve the bone-local source
    records for future animation/skinning work.
    """
    bone_index: int
    pos:        Tuple[float, float, float]   # (x, y, z)
    weights:    Tuple[Tuple[int, Tuple[float, float, float], float], ...] = ()
    normal:     Optional[Tuple[float, float, float]] = None
    saved_pos:  Optional[Tuple[float, float, float]] = None
    saved_normal: Optional[Tuple[float, float, float]] = None


@dataclass
class AbcNode:
    """One entry in the ABC Nodes block."""

    name:         str
    index:        int
    flags:        int
    parent_index: int
    matrix:       Tuple[Tuple[float, float, float, float], ...]


@dataclass
class AbcTriRef:
    """
    One vertex-reference within a triangle: index + texture UV.
    UV coords may exceed [0, 1] (texture wrapping).
    """
    vertex_index: int
    u: float
    v: float


@dataclass
class AbcTriangle:
    """Triangle = three AbcTriRef entries."""
    refs: Tuple[AbcTriRef, AbcTriRef, AbcTriRef]


@dataclass
class AbcPiece:
    """
    One named piece (sub-mesh) from an ABC file.
    A model typically has 1–3 pieces; characters may have more.
    """
    name:      str
    vertices:  List[AbcVertex]
    triangles: List[AbcTriangle]
    # Texture name is not stored per-piece in the ABC format we've parsed;
    # it must be resolved externally via the object's skin/material data.
    texture_name: str = ""


@dataclass
class AbcModel:
    """Loaded ABC model: header metadata + list of pieces."""
    name:           str
    version:        int
    n_verts_total:  int
    n_tris_total:   int
    n_pieces:       int
    n_nodes:        int
    global_radius:  float
    command_string: str
    pieces:         List[AbcPiece] = field(default_factory=list)
    nodes:          List[AbcNode]  = field(default_factory=list)
    baked_bind_pose: bool = False

    def is_empty(self) -> bool:
        return not any(p.triangles for p in self.pieces)


@dataclass
class _ParsedPiece:
    piece: AbcPiece
    lod_count: int
    tri_total: int
    vert_total: int
    trailing_bytes: int


# ---------------------------------------------------------------------------
# Internal parsing helpers
# ---------------------------------------------------------------------------

def _parse_blocks(data: bytes) -> List[Tuple[str, int, int, int]]:
    """
    Walk the top-level named-block chain.

    Returns list of (block_name, hdr_offset, data_start, data_end).
    """
    blocks: List[Tuple[str, int, int, int]] = []
    pos = 0
    n = len(data)
    while pos < n - 6:
        if pos + 2 > n:
            break
        nlen = struct.unpack_from('<H', data, pos)[0]
        if nlen == 0 or nlen > 128:
            break
        if pos + 2 + nlen + 4 > n:
            break
        try:
            bname = data[pos + 2 : pos + 2 + nlen].decode('ascii')
        except UnicodeDecodeError:
            break
        if not all(32 <= ord(c) < 127 for c in bname):
            break
        next_sib = struct.unpack_from('<I', data, pos + 2 + nlen)[0]
        data_start = pos + 2 + nlen + 4
        if 0 < next_sib <= n:
            data_end = next_sib
        else:
            data_end = n   # last block extends to EOF
        blocks.append((bname, pos, data_start, data_end))
        if next_sib == 0 or next_sib >= n:
            break
        pos = next_sib
    return blocks


def _read_string(data: bytes, pos: int) -> Tuple[str, int]:
    """
    Read a WriteString value: uint16 length followed by length bytes.
    Returns (string, new_pos).
    """
    if pos + 2 > len(data):
        return '', pos
    slen = struct.unpack_from('<H', data, pos)[0]
    if pos + 2 + slen > len(data):
        return '', pos
    s = data[pos + 2 : pos + 2 + slen].decode('latin-1', errors='replace')
    return s, pos + 2 + slen


def _parse_allocs(hdata: bytes) -> Optional[dict]:
    """
    Parse the 14-field ModelAllocations from the Header block data.
    Returns a dict or None on error.
    """
    if len(hdata) < 4 + 14 * 4:
        return None
    version = struct.unpack_from('<I', hdata, 0)[0]
    raw = struct.unpack_from('<14I', hdata, 4)
    keys = (
        'nKeyFrames', 'nParentAnims', 'nNodes', 'nPieces', 'nChildModels',
        'nTris', 'nVerts', 'nVertWeights', 'nLODs', 'nSockets',
        'nWeightSets', 'nStrings', 'StringLengths', 'VertAnimDataSize',
    )
    d = dict(zip(keys, raw))
    d['version'] = version
    return d


def _parse_nodes(ndata: bytes, n_nodes_expected: int) -> Optional[List[AbcNode]]:
    """
    Parse the recursive Nodes block tree.

    On disk, ModelNode::Save writes:
        WriteString name
        uint16 node_index
        uint8  flags
        float32[16] global_transform
        uint32 child_count
        children...

    Some ABC files append WeightSet data after the node tree inside the same
    top-level Nodes block, so successful parsing only requires that the node
    tree consumes a valid prefix and returns the expected node count.
    """
    if n_nodes_expected <= 0 or not ndata:
        return []

    nodes: List[AbcNode] = []

    def parse_one(pos: int, parent_index: int, depth: int) -> Optional[int]:
        if depth > max(256, n_nodes_expected + 4):
            return None

        name, pos2 = _read_string(ndata, pos)
        if not name or pos2 == pos:
            return None
        pos = pos2

        if pos + 2 + 1 + 16 * 4 + 4 > len(ndata):
            return None

        node_index = struct.unpack_from('<H', ndata, pos)[0]
        pos += 2
        flags = ndata[pos]
        pos += 1

        raw_mat = struct.unpack_from('<16f', ndata, pos)
        pos += 16 * 4
        matrix = tuple(
            tuple(raw_mat[i * 4:(i + 1) * 4]) for i in range(4)
        )

        child_count = struct.unpack_from('<I', ndata, pos)[0]
        pos += 4
        if child_count > max(256, n_nodes_expected):
            return None

        nodes.append(AbcNode(
            name=name,
            index=int(node_index),
            flags=int(flags),
            parent_index=int(parent_index),
            matrix=matrix,
        ))

        for _ in range(child_count):
            pos = parse_one(pos, int(node_index), depth + 1)  # type: ignore[assignment]
            if pos is None:
                return None
        return pos

    end = parse_one(0, -1, 0)
    if end is None:
        return None
    if len(nodes) != n_nodes_expected:
        return None

    seen = set()
    for node in nodes:
        if node.index in seen or node.index < 0 or node.index >= max(n_nodes_expected, 1):
            return None
        seen.add(node.index)
        for row in node.matrix:
            for value in row:
                if abs(value) > 1.0e6:
                    return None

    return nodes


def _mat_transform_point_colvec(
    matrix: Tuple[Tuple[float, float, float, float], ...],
    pos: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    """
    Transform a point by the column-vector convention used by ABC node data.

    ``ModelNode::Save`` writes LTMatrix values directly.  For these files the
    node/global matrices and raw ``NodeKeyFrame`` transforms store translation
    in column 3 (`m[0][3]`, `m[1][3]`, `m[2][3]`).  Applying row-vector math here
    rotates vertices around the origin and collapses NPCs into shard clusters.
    """
    x, y, z = pos
    return (
        x * matrix[0][0] + y * matrix[0][1] + z * matrix[0][2] + matrix[0][3],
        x * matrix[1][0] + y * matrix[1][1] + z * matrix[1][2] + matrix[1][3],
        x * matrix[2][0] + y * matrix[2][1] + z * matrix[2][2] + matrix[2][3],
    )


def _mat_identity() -> Tuple[Tuple[float, float, float, float], ...]:
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _mat_mul(
    a: Tuple[Tuple[float, float, float, float], ...],
    b: Tuple[Tuple[float, float, float, float], ...],
) -> Tuple[Tuple[float, float, float, float], ...]:
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4))
        for i in range(4)
    )


def _mat_inverse_rigid(
    matrix: Tuple[Tuple[float, float, float, float], ...],
) -> Tuple[Tuple[float, float, float, float], ...]:
    """Inverse of a rigid transform with orthonormal rotation and column translation."""
    r_t = (
        (matrix[0][0], matrix[1][0], matrix[2][0]),
        (matrix[0][1], matrix[1][1], matrix[2][1]),
        (matrix[0][2], matrix[1][2], matrix[2][2]),
    )
    tx, ty, tz = matrix[0][3], matrix[1][3], matrix[2][3]
    inv_t = (
        -(r_t[0][0] * tx + r_t[0][1] * ty + r_t[0][2] * tz),
        -(r_t[1][0] * tx + r_t[1][1] * ty + r_t[1][2] * tz),
        -(r_t[2][0] * tx + r_t[2][1] * ty + r_t[2][2] * tz),
    )
    return (
        (r_t[0][0], r_t[0][1], r_t[0][2], inv_t[0]),
        (r_t[1][0], r_t[1][1], r_t[1][2], inv_t[1]),
        (r_t[2][0], r_t[2][1], r_t[2][2], inv_t[2]),
        (0.0, 0.0, 0.0, 1.0),
    )


def _quat_to_matrix(
    quat: Tuple[float, float, float, float],
    trans: Tuple[float, float, float],
) -> Tuple[Tuple[float, float, float, float], ...]:
    """Convert LithTech ``LTRotation`` (x, y, z, w) plus translation to a matrix."""
    x, y, z, w = quat
    norm = x * x + y * y + z * z + w * w
    if norm > 0.0:
        s = 2.0 / norm
    else:
        s = 0.0

    xx = x * x * s
    yy = y * y * s
    zz = z * z * s
    xy = x * y * s
    xz = x * z * s
    yz = y * z * s
    wx = w * x * s
    wy = w * y * s
    wz = w * z * s
    tx, ty, tz = trans

    return (
        (1.0 - (yy + zz), xy - wz, xz + wy, tx),
        (xy + wz, 1.0 - (xx + zz), yz - wx, ty),
        (xz - wy, yz + wx, 1.0 - (xx + yy), tz),
        (0.0, 0.0, 0.0, 1.0),
    )


def _node_offsets_from_parent(
    nodes: List[AbcNode],
) -> dict:
    """Return bind-pose offsets used for MNODE_ROTATIONONLY nodes."""
    by_index = {node.index: node for node in nodes}
    offsets = {}
    for node in nodes:
        parent = by_index.get(node.parent_index)
        if parent is None:
            offsets[node.index] = (0.0, 0.0, 0.0)
            continue
        local = _mat_mul(_mat_inverse_rigid(parent.matrix), node.matrix)
        offsets[node.index] = (local[0][3], local[1][3], local[2][3])
    return offsets


def _bounds_for_pieces(pieces: List[AbcPiece]) -> Optional[Tuple[float, float, float, float, float, float]]:
    pts = [v.pos for p in pieces for v in p.vertices]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    zs = [p[2] for p in pts]
    return min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)


def _bounds_are_sane(bounds: Optional[Tuple[float, float, float, float, float, float]]) -> bool:
    if bounds is None:
        return False
    vals = list(bounds)
    if any(abs(v) > 5000.0 for v in vals):
        return False
    extent = max(bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4])
    return 0.001 <= extent <= 2000.0


def _bake_with_node_matrices(
    pieces: List[AbcPiece],
    matrices_by_node: dict,
) -> Optional[List[AbcPiece]]:
    """Return pieces whose bone-local vertices are transformed by node matrices."""
    if not pieces or not matrices_by_node:
        return None

    out: List[AbcPiece] = []
    for piece in pieces:
        verts: List[AbcVertex] = []
        for vert in piece.vertices:
            if vert.weights:
                total = 0.0
                acc = [0.0, 0.0, 0.0]
                for bone_index, pos, weight in vert.weights:
                    matrix = matrices_by_node.get(bone_index)
                    if matrix is None:
                        return None
                    x, y, z = _mat_transform_point_colvec(matrix, pos)
                    acc[0] += x * weight
                    acc[1] += y * weight
                    acc[2] += z * weight
                    total += weight
                if abs(total) <= 1e-6:
                    return None
                baked_pos = (acc[0] / total, acc[1] / total, acc[2] / total)
                verts.append(AbcVertex(
                    bone_index=vert.bone_index,
                    pos=baked_pos,
                ))
            else:
                matrix = matrices_by_node.get(vert.bone_index)
                if matrix is None:
                    return None
                verts.append(AbcVertex(
                    bone_index=vert.bone_index,
                    pos=_mat_transform_point_colvec(matrix, vert.pos),
                ))
        out.append(AbcPiece(
            name=piece.name,
            vertices=verts,
            triangles=piece.triangles,
            texture_name=piece.texture_name,
        ))

    return out if _bounds_are_sane(_bounds_for_pieces(out)) else None


def _bake_bind_pose(pieces: List[AbcPiece], nodes: List[AbcNode]) -> Optional[List[AbcPiece]]:
    """Return pieces whose single-bone vertices are transformed to bind pose."""
    if not pieces or not nodes:
        return None
    return _bake_with_node_matrices(pieces, {node.index: node.matrix for node in nodes})


def _pieces_have_saved_model_positions(pieces: List[AbcPiece]) -> bool:
    return bool(pieces) and all(
        vert.saved_pos is not None
        for piece in pieces
        for vert in piece.vertices
    )


def _pieces_have_multi_weight_vertices(pieces: List[AbcPiece]) -> bool:
    return any(
        len(vert.weights) > 1
        for piece in pieces
        for vert in piece.vertices
    )


def _use_saved_model_positions(pieces: List[AbcPiece]) -> List[AbcPiece]:
    out: List[AbcPiece] = []
    for piece in pieces:
        verts = []
        for vert in piece.vertices:
            verts.append(AbcVertex(
                bone_index=vert.bone_index,
                pos=vert.saved_pos if vert.saved_pos is not None else vert.pos,
                weights=vert.weights,
                normal=vert.saved_normal,
                saved_pos=vert.saved_pos,
                saved_normal=vert.saved_normal,
            ))
        out.append(AbcPiece(
            name=piece.name,
            vertices=verts,
            triangles=piece.triangles,
            texture_name=piece.texture_name,
        ))
    return out


def _parse_old_static_animation_pose(
    adata: bytes,
    nodes: List[AbcNode],
) -> Optional[dict]:
    """
    Parse the old MM9 ABC raw-``NodeKeyFrame`` animation layout.

    MM9 character ABCs observed in the corpus use ``compression_type`` of
    ``0xFFFFFFFF``.  Unlike the newer LTB ``AnimNode`` channel layout, each node
    record is a 4-byte sentinel followed by raw 36-byte ``NodeKeyFrame`` structs:
    translation (3 floats), quaternion (4 floats), time (uint32), and a def-vert
    pointer/placeholder (uint32).  The first ``stand`` animation frame gives a
    useful frozen character preview.
    """
    if not adata or not nodes or len(adata) < 4:
        return None

    try:
        n_anims = struct.unpack_from('<I', adata, 0)[0]
    except struct.error:
        return None
    if n_anims <= 0 or n_anims > 512:
        return None

    pos = 4
    best_pose = None
    best_name = ""
    node_count = len(nodes)
    offsets = _node_offsets_from_parent(nodes)

    for _ in range(n_anims):
        if pos + 12 > len(adata):
            break
        pos += 12  # animation user dims
        anim_name, pos2 = _read_string(adata, pos)
        if pos2 == pos or pos2 + 12 > len(adata):
            break
        pos = pos2

        comp, _interp_ms, n_keyframes = struct.unpack_from('<3I', adata, pos)
        pos += 12
        if n_keyframes <= 0 or n_keyframes > 10000:
            break

        for _key in range(n_keyframes):
            if pos + 4 > len(adata):
                return best_pose
            _time_ms = struct.unpack_from('<I', adata, pos)[0]
            _tag, pos2 = _read_string(adata, pos + 4)
            if pos2 == pos + 4:
                return best_pose
            pos = pos2

        if comp != 0xFFFFFFFF:
            # Newer channel-compressed animation blocks need a different parser;
            # stop cleanly and keep any earlier old-style candidate.
            break

        node_stride = 4 + n_keyframes * 36
        if pos + node_count * node_stride > len(adata):
            break

        local_by_node = {}
        for node in nodes:
            _sentinel = struct.unpack_from('<I', adata, pos)[0]
            pos += 4

            tx, ty, tz, qx, qy, qz, qw, _frame_time, _def_vert = struct.unpack_from(
                '<7fII',
                adata,
                pos,
            )
            pos += n_keyframes * 36

            trans = offsets.get(node.index, (0.0, 0.0, 0.0)) if (node.flags & 2) else (tx, ty, tz)
            local_by_node[node.index] = _quat_to_matrix((qx, qy, qz, qw), trans)

        global_by_node = {}
        for node in nodes:
            local = local_by_node.get(node.index)
            if local is None:
                return best_pose
            parent_matrix = global_by_node.get(node.parent_index)
            global_by_node[node.index] = (
                _mat_mul(parent_matrix, local)
                if parent_matrix is not None
                else local
            )

        lname = anim_name.lower()
        if best_pose is None or lname.startswith('stand') or lname.startswith('idle'):
            best_pose = global_by_node
            best_name = lname
            if best_name.startswith('stand') or best_name.startswith('idle'):
                break

    return best_pose


def _should_bake_static_bind_pose(
    allocs: dict,
    nodes: List[AbcNode],
    source_path: str = "",
) -> bool:
    """
    True for character/creature-style ABCs, false for ordinary props.

    Props commonly have a tiny node tree.  NPCs and creatures have many nodes;
    some static civilian variants only advertise one parent animation but still
    store their vertices in bone-local coordinates.  Baking those vertices gives
    a useful frozen preview without entering the larger animation/skinning
    problem.
    """
    return (
        _is_top_level_model_path(source_path)
        and allocs.get('nNodes', 0) > 4
        and allocs.get('nPieces', 0) > 0
        and len(nodes) == allocs.get('nNodes', 0)
    )


def _is_top_level_model_path(source_path: str = "") -> bool:
    norm_path = source_path.replace("\\", "/")
    upper_path = norm_path.upper()
    if "/MODELS/" not in upper_path:
        return True
    rel_model_path = upper_path.rsplit("/MODELS/", 1)[1]
    parts = [part for part in rel_model_path.split("/") if part]
    # REZ-backed model caches are laid out as cache/models/<fingerprint>/... .
    # The fingerprint is not part of the game's virtual model path.
    if len(parts) >= 2 and _looks_like_cache_fingerprint(parts[0]):
        parts = parts[1:]
    return len(parts) == 1


def _looks_like_cache_fingerprint(value: str) -> bool:
    return len(value) == 16 and all(ch in "0123456789ABCDEF" for ch in value.upper())


def _allow_relaxed_static_preview(allocs: dict, source_path: str = "") -> bool:
    return (
        _is_top_level_model_path(source_path)
        and allocs.get('nNodes', 0) > 4
        and allocs.get('nPieces', 0) > 0
    )


def _parse_pieces(
    pdata: bytes,
    n_pieces_expected: int,
    n_verts_total: int,
    n_vert_weights_total: int,
    n_tris_total: int,
    n_lods: int,
    n_nodes: int,
    bone_index_base: int,
    allow_relaxed_lod0: bool = False,
) -> Optional[List[AbcPiece]]:
    """
    Parse the Pieces block binary data.

    Returns a list of AbcPiece or None on fatal parse error.
    For multi-piece models this function infers each piece's vertex count
    from the remaining block budget.
    """
    n = len(pdata)
    if n < 24:
        return None

    # Preamble: nVerts(4) + nPieces(4) + 0(4) + 12-byte constant = 24 bytes
    n_verts_preamble = struct.unpack_from('<I', pdata, 0)[0]
    n_pieces_preamble = struct.unpack_from('<I', pdata, 4)[0]
    if n_pieces_preamble != n_pieces_expected:
        return None

    valid_vert_counts = {0, n_verts_total}
    if n_vert_weights_total:
        valid_vert_counts.add(n_vert_weights_total)
    if n_verts_preamble not in valid_vert_counts:
        # In the observed corpus this marks animated/skinned variants whose
        # piece payload is not the rigid static layout parsed here.
        return None

    max_verts = max(n_verts_total, n_vert_weights_total, n_verts_preamble, 1)
    starts = _find_piece_starts(
        pdata,
        n_pieces_expected=n_pieces_expected,
        max_verts=max_verts,
        max_tris=max(n_tris_total, 1),
    )
    if starts is None:
        return None

    parsed: List[_ParsedPiece] = []
    bounds = starts + [n]
    for i in range(n_pieces_expected):
        piece = _parse_piece_lods(
            pdata,
            piece_start=bounds[i],
            piece_end=bounds[i + 1],
            max_verts=max_verts,
            max_tris=max(n_tris_total, 1),
            n_nodes=n_nodes,
            bone_index_base=bone_index_base,
        )
        if piece is None:
            return None
        parsed.append(piece)

    strict_valid = (
        all(p.lod_count == n_lods for p in parsed)
        and sum(p.tri_total for p in parsed) == n_tris_total
        and sum(p.vert_total for p in parsed) in valid_vert_counts
    )
    if not strict_valid and not allow_relaxed_lod0:
        return None

    return [p.piece for p in parsed]


def _looks_like_piece_start(
    pdata: bytes,
    off: int,
    max_verts: int,
    max_tris: int,
) -> bool:
    """Return True if *off* looks like ``WriteString(name) + LOD header``."""
    n = len(pdata)
    if off + 10 > n:
        return False
    slen = struct.unpack_from('<H', pdata, off)[0]
    if slen <= 0 or slen > 64:
        return False
    if off + 2 + slen + 8 > n:
        return False
    raw_name = pdata[off + 2 : off + 2 + slen]
    if not all(32 <= b < 127 for b in raw_name):
        return False
    lod_off = off + 2 + slen
    return _lod_header_score(pdata, lod_off, max_verts, max_tris) is not None


def _find_piece_starts(
    pdata: bytes,
    n_pieces_expected: int,
    max_verts: int,
    max_tris: int,
) -> Optional[List[int]]:
    """
    Locate piece records inside a Pieces block.

    Multi-piece ABCs store each piece as ``name, LODs, vertices`` followed by
    the next piece's name.  The piece name plus first LOD header is a strong
    signature in the shipped corpus: it matches the declared piece count for
    nearly every rigid static model and avoids guessing vertex splits.
    """
    if n_pieces_expected <= 0:
        return None
    if not _looks_like_piece_start(pdata, 24, max_verts, max_tris):
        return None

    starts = [24]
    for off in range(25, len(pdata) - 10):
        if _looks_like_piece_start(pdata, off, max_verts, max_tris):
            starts.append(off)

    if len(starts) != n_pieces_expected:
        return None
    return starts


def _find_next_lod_header_limited(
    pdata: bytes,
    search_start: int,
    search_stop: int,
    max_verts: int,
    remaining_tris: int,
) -> Optional[int]:
    """Find the next LOD header before *search_stop*.

    The original implementation only checked 48-byte-aligned offsets from
    *search_start*, assuming that any inter-LOD trailer would be an exact
    multiple of the 48-byte vertex record size.  That assumption holds for most
    shipped props, but at least one file (Training_StrawDummy2_C.abc) stores a
    40-byte trailer between LOD 1 and LOD 2 of its first piece, causing the
    aligned scan to skip LOD 2 entirely and fail strict validation.

    Scanning every byte is safe because ``_lod_header_score`` already validates
    triangle count, vertex count, UV plausibility, and vertex-index ranges — a
    strong enough signature to avoid false positives in practice.  The search
    window is bounded by *search_stop* (the end of the current piece), so the
    worst-case scan is the size of one piece, which is acceptable at load time.
    """
    stop = min(search_stop, len(pdata))
    for off in range(search_start, max(search_start, stop - 8)):
        if _lod_header_score(pdata, off, max_verts, remaining_tris) is not None:
            return off
    return None


def _triangle_refs_in_range(piece: AbcPiece) -> bool:
    n_verts = len(piece.vertices)
    if n_verts <= 0:
        return False
    for tri in piece.triangles:
        for ref in tri.refs:
            if ref.vertex_index < 0 or ref.vertex_index >= n_verts:
                return False
    return True


def _vertex_weight_count(pdata: bytes, off: int) -> Optional[int]:
    if off + 4 > len(pdata):
        return None
    count = struct.unpack_from('<H', pdata, off)[0]
    # The upper 16 bits are not part of the count.  In weighted character
    # meshes they often carry a per-vertex weight-set index.
    if count <= 0 or count > 64:
        return None
    return count


def _vertex_record_size(pdata: bytes, off: int) -> Optional[int]:
    n_weights = _vertex_weight_count(pdata, off)
    if n_weights is None:
        return None
    # Single-weight records are 48 bytes, matching the older rigid parser:
    # 4-byte header + one 20-byte weighted copy + 24 bytes of normal/tangent
    # data.  LoMM's Goblin.abc uses 2-4 weighted copies on many vertices.
    return 28 + 20 * n_weights


def _vertex_records_end(
    pdata: bytes,
    vert_start: int,
    vert_count: int,
    limit: Optional[int] = None,
) -> Optional[int]:
    end_limit = len(pdata) if limit is None else min(limit, len(pdata))
    off = vert_start
    for _ in range(vert_count):
        rec_size = _vertex_record_size(pdata, off)
        if rec_size is None:
            return None
        off += rec_size
        if off > end_limit:
            return None
    return off


def _normalise_bone_index(bone_raw: int, bone_index_base: int, n_nodes: int) -> int:
    if 0 <= bone_raw - bone_index_base < max(n_nodes, 1):
        return bone_raw - bone_index_base
    return max(0, bone_raw - 1)


def _parse_piece_lods(
    pdata: bytes,
    piece_start: int,
    piece_end: int,
    max_verts: int,
    max_tris: int,
    n_nodes: int,
    bone_index_base: int,
) -> Optional[_ParsedPiece]:
    """
    Parse one piece and validate all of its LOD chunks.

    Only LOD0 geometry is returned for drawing, but tri/vertex totals across
    every LOD are retained so the caller can validate against Header totals.
    Some ABCs carry a few trailing bytes after a piece's last vertex array;
    those are preserved only as validation slack, not interpreted yet.
    """
    n = len(pdata)
    if piece_start < 24 or piece_end > n or piece_start >= piece_end:
        return None

    piece_name, off = _read_string(pdata, piece_start)
    if not piece_name:
        return None

    lod_count = 0
    tri_total = 0
    vert_total = 0
    trailing_total = 0
    lod0_piece: Optional[AbcPiece] = None

    while off + 8 <= piece_end:
        header = _lod_header_score(
            pdata,
            off,
            max_verts=max(max_verts, 1),
            max_tris=max(max_tris - tri_total, 1),
        )
        if header is None:
            return None

        tri_count = header
        tri_start = off + 4
        vert_count_pos = tri_start + tri_count * 30
        if vert_count_pos + 4 > piece_end:
            return None
        vert_count = struct.unpack_from('<I', pdata, vert_count_pos)[0]
        vert_start = vert_count_pos + 4
        if vert_start > piece_end:
            return None
        if vert_count <= 0 or vert_count > max_verts:
            return None

        lod_data_end = _vertex_records_end(
            pdata,
            vert_start=vert_start,
            vert_count=vert_count,
            limit=piece_end,
        )
        if lod_data_end is None:
            return None

        trailing = 0
        next_lod = _find_next_lod_header_limited(
            pdata,
            search_start=lod_data_end,
            search_stop=piece_end,
            max_verts=max(max_verts, 1),
            remaining_tris=max(max_tris - tri_total - tri_count, 1),
        )
        if next_lod is None:
            trailing = piece_end - lod_data_end
        else:
            trailing = next_lod - lod_data_end
            if trailing < 0:
                return None

        lod_piece = _parse_lod_geometry(
            pdata,
            piece_name=piece_name,
            tri_count=tri_count,
            tri_start=tri_start,
            vert_start=vert_start,
            vert_count=vert_count,
            n_nodes=n_nodes,
            bone_index_base=bone_index_base,
        )
        if len(lod_piece.triangles) != tri_count:
            return None
        if len(lod_piece.vertices) != vert_count:
            return None
        if not _triangle_refs_in_range(lod_piece):
            return None

        if lod0_piece is None:
            lod0_piece = lod_piece

        lod_count += 1
        tri_total += tri_count
        vert_total += vert_count
        trailing_total += trailing

        if next_lod is None:
            break
        off = next_lod

    if lod0_piece is None:
        return None

    return _ParsedPiece(
        piece=lod0_piece,
        lod_count=lod_count,
        tri_total=tri_total,
        vert_total=vert_total,
        trailing_bytes=trailing_total,
    )


def _lod_header_score(
    pdata: bytes,
    off: int,
    max_verts: int,
    max_tris: int,
) -> Optional[int]:
    """Return tri_count if *off* looks like an LOD header."""
    n = len(pdata)
    if off + 4 > n:
        return None
    tri_count = struct.unpack_from('<I', pdata, off)[0]
    if tri_count <= 0 or tri_count > max_tris:
        return None

    tri_start = off + 4
    tri_end = tri_start + tri_count * 30
    vert_count_pos = tri_end
    if vert_count_pos + 4 > n:
        return None
    vert_count = struct.unpack_from('<I', pdata, vert_count_pos)[0]
    if vert_count <= 0 or vert_count > max_verts:
        return None
    if _vertex_records_end(pdata, vert_count_pos + 4, vert_count) is None:
        return None

    ok = 0
    bad = 0
    sampled_refs = min(tri_count, 8) * 3
    for t in range(min(tri_count, 8)):
        for r in range(3):
            rbase = tri_start + t * 30 + r * 10
            u = struct.unpack_from('<f', pdata, rbase)[0]
            v = struct.unpack_from('<f', pdata, rbase + 4)[0]
            vi = struct.unpack_from('<H', pdata, rbase + 8)[0]
            if vi < max_verts and abs(u) < 1000.0 and abs(v) < 1000.0:
                ok += 1
            else:
                bad += 1

    if ok < sampled_refs or bad:
        return None
    return tri_count


def _parse_lod_geometry(
    pdata: bytes,
    piece_name: str,
    tri_count: int,
    tri_start: int,
    vert_start: int,
    vert_count: int,
    n_nodes: int,
    bone_index_base: int,
) -> AbcPiece:
    """Parse one LOD's triangle refs and vertices as a single AbcPiece."""
    n = len(pdata)

    triangles: List[AbcTriangle] = []
    for t in range(tri_count):
        tbase = tri_start + t * 30
        if tbase + 30 > n:
            break
        refs = []
        for r in range(3):
            rbase = tbase + r * 10
            u = struct.unpack_from('<f', pdata, rbase)[0]
            v = struct.unpack_from('<f', pdata, rbase + 4)[0]
            vi = struct.unpack_from('<H', pdata, rbase + 8)[0]
            refs.append(AbcTriRef(vertex_index=vi, u=u, v=v))
        triangles.append(AbcTriangle(refs=tuple(refs)))  # type: ignore[arg-type]

    vertices: List[AbcVertex] = []
    vbase = vert_start
    for _v_idx in range(vert_count):
        n_weights = _vertex_weight_count(pdata, vbase)
        rec_size = _vertex_record_size(pdata, vbase)
        if n_weights is None or rec_size is None or vbase + rec_size > n:
            break
        weights = []
        for w_idx in range(n_weights):
            wbase = vbase + 4 + w_idx * 20
            bone_raw = struct.unpack_from('<I', pdata, wbase)[0]
            bone_idx = _normalise_bone_index(bone_raw, bone_index_base, n_nodes)
            wx, wy, wz, weight = struct.unpack_from('<ffff', pdata, wbase + 4)
            weights.append((bone_idx, (wx, wy, wz), weight))
        bone_idx = weights[0][0]
        x, y, z = struct.unpack_from('<fff', pdata, vbase + 8)
        tail_start = vbase + 4 + n_weights * 20
        sx, sy, sz = struct.unpack_from('<fff', pdata, tail_start)
        nx, ny, nz = struct.unpack_from('<fff', pdata, tail_start + 12)
        normal_len_sq = nx * nx + ny * ny + nz * nz
        saved_normal = (nx, ny, nz) if normal_len_sq > 1.0e-12 else None
        vertices.append(AbcVertex(
            bone_index=bone_idx,
            pos=(x, y, z),
            weights=tuple(weights),
            saved_pos=(sx, sy, sz),
            saved_normal=saved_normal,
        ))
        vbase += rec_size

    return AbcPiece(name=piece_name, vertices=vertices, triangles=triangles)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_abc(path: str, bake_static_bind_pose: bool = False) -> Optional[AbcModel]:
    """
    Load an ABC model file.

    Returns an AbcModel on success, or None if the file is unreadable,
    uses an unsupported format variant, or has no valid geometry.

    Parameters
    ----------
    path : str
        Absolute or relative filesystem path to the .ABC file.
    bake_static_bind_pose : bool
        Static-preview option for top-level animated character/NPC models.
        Ordinary prop models are left unbaked even when this is True.
    """
    try:
        with open(path, 'rb') as fh:
            raw = fh.read()
    except OSError:
        return None

    blocks = _parse_blocks(raw)
    block_map = {name: (ds, de) for name, _, ds, de in blocks}

    # ── Header ────────────────────────────────────────────────────────────
    if 'Header' not in block_map:
        return None
    hds, hde = block_map['Header']
    hdata = raw[hds:hde]
    allocs = _parse_allocs(hdata)
    if allocs is None:
        return None

    # This stage supports rigid/static piece payloads. Animated/skinned variants
    # intentionally fall back to billboards when validation below fails.
    if allocs['nPieces'] <= 0:
        return None

    # Read CommandString + GlobalRadius from header continuation.
    cmd_pos = 4 + 14 * 4   # after version + alloc fields
    command_string, cmd_pos = _read_string(hdata, cmd_pos)
    global_radius = 0.0
    if cmd_pos + 4 <= len(hdata):
        global_radius = struct.unpack_from('<f', hdata, cmd_pos)[0]

    # ── Pieces ────────────────────────────────────────────────────────────
    if 'Pieces' not in block_map:
        return None
    pds, pde = block_map['Pieces']
    pdata = raw[pds:pde]

    # Rigid prop payloads observed so far store bone indices as 1-based values.
    # Top-level character/NPC meshes store the node index directly, including
    # several one-animation civilian variants; subtracting one assigns every
    # vertex to the previous bone and collapses the static preview into shards.
    bone_index_base = 0 if (
        _is_top_level_model_path(path) and allocs.get('nNodes', 0) > 4
    ) else 1

    pieces = _parse_pieces(
        pdata,
        n_pieces_expected=allocs['nPieces'],
        n_verts_total=allocs['nVerts'],
        n_vert_weights_total=allocs.get('nVertWeights', 0),
        n_tris_total=allocs['nTris'],
        n_lods=allocs['nLODs'],
        n_nodes=allocs['nNodes'],
        bone_index_base=bone_index_base,
        allow_relaxed_lod0=(
            bake_static_bind_pose
            and _allow_relaxed_static_preview(allocs, path)
        ),
    )
    if pieces is None:
        return None

    nodes: List[AbcNode] = []
    if 'Nodes' in block_map:
        nds, nde = block_map['Nodes']
        parsed_nodes = _parse_nodes(raw[nds:nde], allocs['nNodes'])
        if parsed_nodes is not None:
            nodes = parsed_nodes

    baked_bind_pose = False
    if bake_static_bind_pose and _should_bake_static_bind_pose(allocs, nodes, path):
        if (
            _pieces_have_multi_weight_vertices(pieces)
            and _pieces_have_saved_model_positions(pieces)
        ):
            pieces = _use_saved_model_positions(pieces)
            baked_bind_pose = True
        else:
            baked = None
            if 'Animation' in block_map:
                ads, ade = block_map['Animation']
                pose = _parse_old_static_animation_pose(raw[ads:ade], nodes)
                if pose is not None:
                    baked = _bake_with_node_matrices(pieces, pose)
            if baked is None:
                baked = _bake_bind_pose(pieces, nodes)
            if baked is not None:
                pieces = baked
                baked_bind_pose = True

    import os
    model_name = os.path.splitext(os.path.basename(path))[0]

    return AbcModel(
        name=model_name,
        version=allocs['version'],
        n_verts_total=allocs['nVerts'],
        n_tris_total=allocs['nTris'],
        n_pieces=allocs['nPieces'],
        n_nodes=allocs['nNodes'],
        global_radius=global_radius,
        command_string=command_string,
        pieces=pieces,
        nodes=nodes,
        baked_bind_pose=baked_bind_pose,
    )


# ---------------------------------------------------------------------------
# GPU upload helper (imported here to keep it close to the loader)
# ---------------------------------------------------------------------------

def upload_abc_model(
    abc:      AbcModel,
    category: str = "main",
) -> "Optional[Any]":   # returns Optional[GpuMesh]
    """
    Triangulate *abc* and upload it to the GPU.

    This function mirrors ``gl_mesh.upload_model`` but consumes AbcModel
    instead of WorldModelMesh.  It builds a flat (non-indexed) VBO where
    each triangle occupies 3 consecutive rows:

        [x, y, z, nx, ny, nz, u, v]   float32 × 8  = 32 bytes/vertex

    Normals are computed per-triangle by cross-product (flat shading).
    Returns a GpuMesh or None if the model is empty.

    Requires a live GL context.
    """
    try:
        from OpenGL import GL  # type: ignore
        import numpy as np
        from view3d.gl_mesh import GpuMesh
    except ImportError:
        return None

    _COORD_SANITY = 1.0e5
    _AREA_EPS     = 1.0e-8

    vert_rows  = []
    index_list = []
    tri_pos_rows = []  # for CPU raycast
    tex_ranges = []
    base = 0

    for piece in abc.pieces:
        verts = piece.vertices
        if not verts or not piece.triangles:
            continue

        positions = np.array([v.pos for v in verts], dtype=np.float64)
        piece_index_start = len(index_list)

        for tri in piece.triangles:
            refs = tri.refs
            # Gather the three positions.
            idxs = [r.vertex_index for r in refs]
            if any(i < 0 or i >= len(verts) for i in idxs):
                continue
            pts = positions[idxs]   # (3, 3)

            # Sanity-check coordinates.
            if np.any(np.abs(pts) > _COORD_SANITY):
                continue

            e1 = pts[1] - pts[0]
            e2 = pts[2] - pts[0]
            face_normal = np.cross(e1, e2)
            face_normal_len = float(np.linalg.norm(face_normal))
            if face_normal_len < _AREA_EPS:
                continue  # degenerate triangle
            face_normal_unit = (face_normal / face_normal_len).astype(np.float32)

            for k, ref in enumerate(refs):
                p = pts[k].astype(np.float32)
                v_normal = verts[idxs[k]].normal
                if v_normal is not None:
                    n_arr = np.array(v_normal, dtype=np.float32)
                    n_len = float(np.linalg.norm(n_arr))
                    n_unit = n_arr / n_len if n_len >= _AREA_EPS else face_normal_unit
                else:
                    n_unit = face_normal_unit
                vert_rows.append([
                    p[0], p[1], p[2],
                    n_unit[0], n_unit[1], n_unit[2],
                    float(ref.u), float(ref.v),
                ])

            tri_pos_rows.append(pts.astype(np.float32))   # (3, 3)
            index_list.extend([base, base + 1, base + 2])
            base += 3

        piece_index_count = len(index_list) - piece_index_start
        if piece_index_count > 0:
            tex_ranges.append((
                piece.name,
                piece_index_start * 4,  # uint32 index byte offset
                piece_index_count,
            ))

    if not vert_rows:
        return None

    verts_np   = np.array(vert_rows,    dtype=np.float32)
    indices_np = np.array(index_list,   dtype=np.uint32)
    tri_pos_np = np.stack(tri_pos_rows, axis=0)   # (T, 3, 3)
    n_tris     = len(tri_pos_rows)

    vao = int(GL.glGenVertexArrays(1))
    vbo = int(GL.glGenBuffers(1))
    ibo = int(GL.glGenBuffers(1))

    GL.glBindVertexArray(vao)

    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
    GL.glBufferData(GL.GL_ARRAY_BUFFER,
                    verts_np.nbytes, verts_np, GL.GL_STATIC_DRAW)

    stride = 8 * 4  # 32 bytes
    GL.glEnableVertexAttribArray(0)
    GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, stride, None)
    GL.glEnableVertexAttribArray(1)
    GL.glVertexAttribPointer(1, 3, GL.GL_FLOAT, GL.GL_FALSE, stride,
                             GL.ctypes.c_void_p(12))
    GL.glEnableVertexAttribArray(2)
    GL.glVertexAttribPointer(2, 2, GL.GL_FLOAT, GL.GL_FALSE, stride,
                             GL.ctypes.c_void_p(24))

    GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ibo)
    GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER,
                    indices_np.nbytes, indices_np, GL.GL_STATIC_DRAW)

    GL.glBindVertexArray(0)

    return GpuMesh(
        vao=vao, vbo=vbo, ibo=ibo,
        index_count=len(indices_np),
        vertex_count=verts_np.shape[0],
        triangle_count=n_tris,
        dropped_polys=0,
        category=category,
        model_name=abc.name,
        # For ABC object meshes, tex_ranges names are piece names rather than
        # concrete DTX paths. draw_object_models maps them through WorldObject
        # Skin entries at instance draw time.
        tex_ranges=tex_ranges,
        tri_positions=tri_pos_np,
    )
