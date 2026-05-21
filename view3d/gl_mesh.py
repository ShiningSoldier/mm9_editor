"""
gl_mesh.py
==========

GPU buffer management for BSP geometry.

Each WorldModelMesh from bsp.py is uploaded once as a VAO + VBO + IBO
and cached by its Python object id.  A call to draw_mesh() issues a
single glDrawElements for the whole model.

Vertex layout (interleaved, 32 bytes/vertex):
    offset  0 : vec3  position  (x, y, z)   float32 × 3
    offset 12 : vec3  normal    (nx, ny, nz) float32 × 3
    offset 24 : vec2  texcoord  (u, v)       float32 × 2

Normals are computed per-triangle at upload time (flat shading) using the
cross-product of the first two edges.  Degenerate triangles (zero area,
index out of range, or garbage coordinates) are silently dropped so that
corrupted sub-models in shipped levels do not crash the viewer.

UV coordinates are computed from the surface's planar projection stored in
bsp.Surface.  If a mesh has no surface data (e.g. partially parsed models),
all UVs default to (0, 0) and the fragment shader falls back to the solid
category-colour tint.

Coordinate system
-----------------
MM9 stores all BSP points in world space already; WorldModelMesh.translation
is metadata about the sub-model's home position but the vertex coordinates
are not relative to it.  We therefore upload the raw points as-is, matching
the coordinate space used by the rest of the editor.

This module requires a live GL context.  Import GpuMesh freely; only call
upload_model / draw_mesh / delete_mesh / draw_bsp after GL is initialised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


# Vertices beyond this value are from corrupted BSP sections and must be
# dropped before they overflow GL buffer ranges.
_COORD_SANITY: float = 1.0e6

# Minimum cross-product magnitude to accept a triangle as non-degenerate.
# Triangles below this threshold are co-linear / zero-area and should be
# silently dropped (their normals would be garbage).
_AREA_EPSILON: float = 1.0e-8

_NON_RENDER_TEXTURE_TOKENS = (
    "/LEVELTEXTURES/MISC/RAIL.DTX",
    "/LEVELTEXTURES/MISC/SOUNDONLY.DTX",
    "/LEVELTEXTURES/MISC/INVISIBLE.DTX",
    "/LEVELTEXTURES/INVISIBLE.DTX",
    "/SKYBOX/SKYMARKER.DTX",
)

_HELPER_SOLID_TEXTURE_TOKENS = (
    "/LEVELTEXTURES/MISC/FIRETHROUGH.DTX",
)

_WATER_PLACEHOLDER_TEXTURE = "TEXTURES\\LevelTextures\\Terrain\\Ocean.dtx"


def _normalise_texture_name(tex_name: str) -> str:
    norm = (tex_name or "").replace("\\", "/").upper()
    if norm.startswith("TEXTURES/"):
        norm = norm[len("TEXTURES"):]
    elif not norm.startswith("/"):
        norm = "/" + norm
    return norm


def _render_texture_name(tex_name: str) -> str:
    """
    Return the texture to use for a BSP surface in the editor view.

    Some water in MM9 is represented by marker materials or sprite/effect
    references in the BSP.  The game replaces those through a special water
    path; the editor substitutes a static ocean DTX so water volumes remain
    visible without showing the white "WATER" marker texture.
    """
    norm = _normalise_texture_name(tex_name)
    if ("/LEVELTEXTURES/TERRAIN/WATERMARKER.DTX" in norm
            or "/SPRITES/WATER/" in norm
            or "/SPRITETEXTURES/WATER/" in norm):
        return _WATER_PLACEHOLDER_TEXTURE
    return tex_name


def _is_non_render_texture(tex_name: str) -> bool:
    """True for LithTech editor/helper BSP materials the game does not draw."""
    norm = _normalise_texture_name(tex_name)
    if norm.endswith(".SPR"):
        return True
    return any(token in norm for token in _NON_RENDER_TEXTURE_TOKENS)


def _is_helper_solid_texture(tex_name: str) -> bool:
    """True for helper materials that should draw as plain editor geometry."""
    norm = _normalise_texture_name(tex_name)
    return any(token in norm for token in _HELPER_SOLID_TEXTURE_TOKENS)


def _is_helper_bsp_model(model) -> bool:  # type: ignore[type-arg]
    name = str(getattr(model, "name", "") or "").lower()
    if name in {"physicsbsp", "visbsp"}:
        return False
    if "_collision" in name:
        return True
    for tex_name in getattr(model, "texture_names", []) or []:
        if _is_helper_solid_texture(str(tex_name or "")):
            return True
    return False



# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GpuMesh:
    """
    OpenGL object IDs for one uploaded WorldModelMesh.
    Created by upload_model(); freed by delete_mesh().
    """
    vao:            int           # vertex array object
    vbo:            int           # interleaved position + normal buffer
    ibo:            int           # uint32 triangle index buffer
    index_count:    int           # 3 × number of triangles
    vertex_count:   int           # total vertices in the VBO
    triangle_count: int           # triangles actually uploaded
    dropped_polys:  int           # polygons skipped (degenerate / corrupt)
    category:       str           # "main" | "submodel" | "terrain" | "skybox"
    model_name:     str
    # Per-texture sub-range draw list: (texture_name, ibo_byte_offset, index_count).
    # Triangles in the IBO are sorted by texture so each range is a contiguous
    # block.  Empty list means no surface data was available (fall back to solid
    # colour tint).
    tex_ranges: List[Tuple[str, int, int]] = field(default_factory=list)

    # CPU-side triangle positions: shape (T, 3, 3) float32 where axis 1 indexes
    # vertex 0/1/2 and axis 2 is XYZ.  Retained after GPU upload so ray-triangle
    # intersection can be done without a GPU readback.  None when mesh is empty.
    tri_positions: Optional[np.ndarray] = None

    def is_empty(self) -> bool:
        return self.index_count == 0

    def is_visible(
        self,
        show_submodels: bool = True,
        show_skybox:    bool = False,
        show_terrain:   bool = True,
    ) -> bool:
        if self.category == "skybox":   return show_skybox
        if self.category == "submodel": return show_submodels
        if self.category == "terrain":  return show_terrain
        return True  # "main"


@dataclass
class BspDrawRange:
    """One pre-resolved texture range within a BSP mesh."""

    texture_id: int
    byte_offset: int
    index_count: int


@dataclass
class BspDrawItem:
    """Precomputed draw data for one static BSP world model."""

    mesh: GpuMesh
    color: Tuple[float, float, float]
    ranges: List[BspDrawRange] = field(default_factory=list)
    alpha: float = 1.0
    wireframe: bool = False


@dataclass
class BspDrawBatch:
    """Static BSP draw plan reused until the level or texture cache changes."""

    items: List[BspDrawItem]
    models_drawn: int
    triangles_drawn: int


# ---------------------------------------------------------------------------
# Triangulation  (pure numpy, no GL dependency)
# ---------------------------------------------------------------------------

def _triangulate_model(
    mesh,
    tex_cache=None,
) -> Tuple[np.ndarray, np.ndarray, List[Tuple[str, int, int]]]:  # type: ignore[type-arg]
    """
    Convert a bsp.WorldModelMesh into flat numpy arrays ready for GPU upload.

    Robustness rules:
      - Skip polygons whose vertex indices are out of range.
      - Skip polygons containing a vertex whose coordinate exceeds
        _COORD_SANITY (corrupted BSP sections in some shipped levels).
      - Skip individual triangles whose cross-product magnitude is below
        _AREA_EPSILON (degenerate / zero-area).

    Returns
    -------
    verts      : float32 ndarray, shape (N, 8) — [x, y, z, nx, ny, nz, u, v]
    indices    : uint32 ndarray, shape (M,)    — flat triangle list (M = 3 × tris)
                 Triangles are sorted by texture name for contiguous draw ranges.
    tex_ranges : list of (texture_name, ibo_byte_offset, index_count) — one entry
                 per unique texture encountered.  Empty when no surface data is
                 present.
    """
    _empty = (
        np.zeros((0, 8), dtype=np.float32),
        np.zeros(0, dtype=np.uint32),
        [],
    )

    pts   = mesh.points
    n_pts = len(pts)

    if n_pts < 3 or not mesh.polygons:
        return _empty

    pts_np        = np.asarray(pts, dtype=np.float32)   # (P, 3)
    surfaces      = getattr(mesh, "surfaces",      [])   # bsp.Surface list
    texture_names = getattr(mesh, "texture_names", [])   # List[str]
    n_surfaces    = len(surfaces)
    n_textures    = len(texture_names)
    texture_sizes: Dict[str, Tuple[int, int]] = {}

    def _texture_size(tex_name: str) -> Tuple[int, int]:
        if not tex_name:
            return (128, 128)
        if tex_name not in texture_sizes:
            size = None
            if tex_cache is not None and hasattr(tex_cache, "image_size"):
                size = tex_cache.image_size(tex_name)
            texture_sizes[tex_name] = size if size is not None else (128, 128)
        return texture_sizes[tex_name]

    vert_rows:     List[np.ndarray] = []
    index_list:    List[int]        = []
    tri_tex_names: List[str]        = []   # one entry per accepted triangle
    base = 0

    for poly in mesh.polygons:
        vis   = poly.vertex_indices
        n_vis = len(vis)
        if n_vis < 3:
            continue

        # ── index bounds ──────────────────────────────────────────────
        if any(v < 0 or v >= n_pts for v in vis):
            continue

        # ── coordinate sanity ─────────────────────────────────────────
        pv = pts_np[vis]   # (V, 3)
        if np.any(np.abs(pv) > _COORD_SANITY):
            continue

        # ── resolve surface + texture for this polygon ─────────────────
        si   = poly.surface_index
        surf = surfaces[si] if (n_surfaces > 0 and 0 <= si < n_surfaces) else None

        tex_name = ""
        if (surf is not None
                and n_textures > 0
                and 0 <= surf.texture_index < n_textures):
            tex_name = texture_names[surf.texture_index]

        tex_name = _render_texture_name(tex_name)
        if _is_non_render_texture(tex_name):
            continue
        if _is_helper_solid_texture(tex_name):
            tex_name = ""

        # ── per-vertex UV from LithTech OPQ surface projection ─────────
        if surf is not None:
            tex_w, tex_h = _texture_size(tex_name)
            uvs = [surf.compute_uv((float(pv[i, 0]),
                                    float(pv[i, 1]),
                                    float(pv[i, 2])),
                                   float(tex_w),
                                   float(tex_h)) for i in range(n_vis)]
        else:
            uvs = [(0.0, 0.0)] * n_vis   # fallback: no surface data

        # Fan-triangulate from vertex 0; check each triangle individually
        poly_verts:   List[np.ndarray] = []
        poly_indices: List[int]        = []
        tri_base = base

        for k in range(1, n_vis - 1):
            v0, v1, v2 = pv[0], pv[k], pv[k + 1]
            uv0, uv1, uv2 = uvs[0], uvs[k], uvs[k + 1]

            # ── degenerate triangle check ──────────────────────────────
            e1    = v1 - v0
            e2    = v2 - v0
            n_vec = np.cross(e1, e2)
            n_len = float(np.linalg.norm(n_vec))
            if n_len < _AREA_EPSILON:
                continue   # zero-area triangle — drop silently

            n_unit = (n_vec / n_len).astype(np.float32)

            # Emit three vertices: position + normal + UV
            for v, uv in zip((v0, v1, v2), (uv0, uv1, uv2)):
                poly_verts.append(np.array(
                    [v[0], v[1], v[2],
                     n_unit[0], n_unit[1], n_unit[2],
                     float(uv[0]), float(uv[1])],
                    dtype=np.float32,
                ))

            poly_indices.extend([
                tri_base + len(poly_verts) - 3,
                tri_base + len(poly_verts) - 2,
                tri_base + len(poly_verts) - 1,
            ])
            tri_tex_names.append(tex_name)   # one per accepted triangle

        if poly_verts:
            vdata = np.array(poly_verts, dtype=np.float32).reshape(-1, 8)
            vert_rows.append(vdata)
            index_list.extend(poly_indices)
            base += len(poly_verts)

    if not vert_rows:
        return _empty

    verts   = np.vstack(vert_rows).astype(np.float32)
    indices = np.array(index_list, dtype=np.uint32)
    n_tris  = len(tri_tex_names)

    # ── Sort triangles by texture name for contiguous per-texture draw calls ──
    # Each triangle owns exactly 3 consecutive VBO entries (flat-shaded, no
    # vertex sharing), so reordering the IBO triples is safe without touching
    # the VBO.
    tex_ranges: List[Tuple[str, int, int]] = []
    if n_tris > 0 and any(tri_tex_names):
        tex_arr = np.array(tri_tex_names, dtype=object)
        order   = np.argsort(tex_arr, kind="stable")
        indices = indices.reshape(-1, 3)[order].ravel()
        tex_arr = tex_arr[order]

        i = 0
        while i < n_tris:
            name = str(tex_arr[i])
            j = i + 1
            while j < n_tris and tex_arr[j] == name:
                j += 1
            byte_off = i * 3 * 4    # i tris × 3 indices × 4 bytes/uint32
            count    = (j - i) * 3
            tex_ranges.append((name, byte_off, count))
            i = j

    return verts, indices, tex_ranges


def triangulation_stats(mesh) -> dict:  # type: ignore[type-arg]
    """
    Return a dict with geometry statistics for *mesh* without uploading to GL.
    Useful for diagnostics and the status bar.

    Keys: total_polys, accepted_tris, dropped_polys, vertex_count
    """
    pts = mesh.points
    n_pts = len(pts)
    total_polys = len(mesh.polygons)
    accepted_tris = 0
    dropped_polys = 0

    if n_pts < 3:
        return dict(total_polys=total_polys, accepted_tris=0,
                    dropped_polys=total_polys, vertex_count=0)

    pts_np = np.asarray(pts, dtype=np.float32)

    for poly in mesh.polygons:
        vis = poly.vertex_indices
        n_vis = len(vis)
        if n_vis < 3:
            dropped_polys += 1
            continue
        if any(v < 0 or v >= n_pts for v in vis):
            dropped_polys += 1
            continue
        pv = pts_np[vis]
        if np.any(np.abs(pv) > _COORD_SANITY):
            dropped_polys += 1
            continue

        poly_ok = False
        for k in range(1, n_vis - 1):
            e1 = pv[k]   - pv[0]
            e2 = pv[k+1] - pv[0]
            if float(np.linalg.norm(np.cross(e1, e2))) >= _AREA_EPSILON:
                accepted_tris += 1
                poly_ok = True
        if not poly_ok:
            dropped_polys += 1

    return dict(
        total_polys=total_polys,
        accepted_tris=accepted_tris,
        dropped_polys=dropped_polys,
        vertex_count=accepted_tris * 3,
    )


# ---------------------------------------------------------------------------
# GPU upload / draw / delete
# ---------------------------------------------------------------------------

def upload_model(mesh, tex_cache=None) -> Optional[GpuMesh]:  # type: ignore[type-arg]
    """
    Triangulate *mesh* and upload it to the GPU.
    Returns None if the model has no valid geometry after filtering.
    Requires a live GL context.
    """
    from OpenGL import GL  # type: ignore

    verts, indices, tex_ranges = _triangulate_model(mesh, tex_cache=tex_cache)
    n_verts = verts.shape[0]
    n_tris  = len(indices) // 3

    # Count dropped polygons for the stats field
    total_polys = len(mesh.polygons)
    # Rough estimate: accepted fan triangles come from accepted polygons;
    # exact count would require a second pass — use total minus accepted
    dropped = total_polys - n_tris  # lower bound; fine for diagnostics

    if n_tris == 0:
        return None

    # Extract CPU-side triangle positions for ray-triangle intersection.
    # Each triangle owns 3 consecutive, non-shared VBO entries (flat normals),
    # so verts[idx] gives the correct positions without any vertex aliasing.
    tri_idx  = indices.reshape(-1, 3)                   # (T, 3)
    tri_pos  = np.stack([                               # (T, 3, 3)
        verts[tri_idx[:, 0], :3],
        verts[tri_idx[:, 1], :3],
        verts[tri_idx[:, 2], :3],
    ], axis=1).astype(np.float32)

    vao = int(GL.glGenVertexArrays(1))
    vbo = int(GL.glGenBuffers(1))
    ibo = int(GL.glGenBuffers(1))

    GL.glBindVertexArray(vao)

    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
    GL.glBufferData(GL.GL_ARRAY_BUFFER,
                    verts.nbytes, verts, GL.GL_STATIC_DRAW)

    stride = 8 * 4   # 8 floats × 4 bytes = 32 bytes
    # location 0: position  (offset  0, vec3)
    GL.glEnableVertexAttribArray(0)
    GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, stride, None)
    # location 1: normal    (offset 12, vec3)
    GL.glEnableVertexAttribArray(1)
    GL.glVertexAttribPointer(1, 3, GL.GL_FLOAT, GL.GL_FALSE, stride,
                             GL.ctypes.c_void_p(12))
    # location 2: texcoord  (offset 24, vec2)
    GL.glEnableVertexAttribArray(2)
    GL.glVertexAttribPointer(2, 2, GL.GL_FLOAT, GL.GL_FALSE, stride,
                             GL.ctypes.c_void_p(24))

    GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ibo)
    GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER,
                    indices.nbytes, indices, GL.GL_STATIC_DRAW)

    GL.glBindVertexArray(0)

    return GpuMesh(
        vao=vao, vbo=vbo, ibo=ibo,
        index_count=len(indices),
        vertex_count=n_verts,
        triangle_count=n_tris,
        dropped_polys=max(0, dropped),
        category=mesh.category(),
        model_name=mesh.name,
        tex_ranges=tex_ranges,
        tri_positions=tri_pos,
    )


def draw_mesh(gpu_mesh: GpuMesh) -> None:
    """
    Issue a glDrawElements call for *gpu_mesh*.
    The caller must have already bound the shader program and set uniforms.
    """
    from OpenGL import GL  # type: ignore
    GL.glBindVertexArray(gpu_mesh.vao)
    GL.glDrawElements(GL.GL_TRIANGLES, gpu_mesh.index_count,
                      GL.GL_UNSIGNED_INT, None)
    GL.glBindVertexArray(0)


def delete_mesh(gpu_mesh: GpuMesh) -> None:
    """Free GPU resources. Call when the level is closed or reloaded."""
    from OpenGL import GL  # type: ignore
    GL.glDeleteVertexArrays(1, [gpu_mesh.vao])
    GL.glDeleteBuffers(1, [gpu_mesh.vbo])
    GL.glDeleteBuffers(1, [gpu_mesh.ibo])


# ---------------------------------------------------------------------------
# Ray-triangle intersection  (CPU, no GL dependency)
# ---------------------------------------------------------------------------

def raycast_mesh_array(
    tri_positions: np.ndarray,
    ray_o:         np.ndarray,
    ray_d:         np.ndarray,
) -> "Optional[Tuple[float, np.ndarray]]":
    """
    Vectorised Möller-Trumbore ray-triangle intersection.

    Tests all triangles in *tri_positions* simultaneously using numpy
    broadcasting and returns the closest hit.

    Parameters
    ----------
    tri_positions : float32 ndarray, shape (T, 3, 3)
        CPU-side triangle data.  Axis 1 indexes vertex 0/1/2; axis 2 is XYZ.
        Typically ``GpuMesh.tri_positions``.
    ray_o : (3,) array — ray origin in world space
    ray_d : (3,) array — normalised ray direction in world space

    Returns
    -------
    (t, hit_xyz)  where *t* is the ray parameter and *hit_xyz* is a (3,)
    float32 world-space point, or ``None`` if no triangle is intersected.
    """
    EPSILON = 1e-7

    v0 = tri_positions[:, 0, :].astype(np.float64)    # (T, 3)
    v1 = tri_positions[:, 1, :].astype(np.float64)
    v2 = tri_positions[:, 2, :].astype(np.float64)
    ro = np.asarray(ray_o, dtype=np.float64)           # (3,)
    rd = np.asarray(ray_d, dtype=np.float64)           # (3,)

    e1 = v1 - v0                                       # (T, 3)
    e2 = v2 - v0

    h  = np.cross(rd, e2)                              # (T, 3)  h = D × e2
    a  = np.einsum("ij,ij->i", e1, h)                 # (T,)    a = e1 · h

    valid = np.abs(a) > EPSILON
    if not np.any(valid):
        return None

    # Avoid division by zero for degenerate triangles
    safe_a = np.where(valid, a, 1.0)
    f      = np.where(valid, 1.0 / safe_a, 0.0)       # (T,)

    s = ro - v0                                        # (T, 3)
    u = f * np.einsum("ij,ij->i", s, h)               # (T,)
    valid &= (u >= -EPSILON) & (u <= 1.0 + EPSILON)

    q  = np.cross(s, e1)                               # (T, 3)
    v  = f * np.einsum("ij,j->i", q, rd)              # (T,)
    valid &= (v >= -EPSILON) & ((u + v) <= 1.0 + EPSILON)

    t = f * np.einsum("ij,ij->i", e2, q)              # (T,)
    valid &= t > EPSILON

    if not np.any(valid):
        return None

    t_masked = np.where(valid, t, np.inf)
    best     = int(np.argmin(t_masked))
    best_t   = float(t_masked[best])
    if np.isinf(best_t):
        return None

    hit = (ro + best_t * rd).astype(np.float32)
    return best_t, hit


# ---------------------------------------------------------------------------
# High-level draw helper
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Textured draw helper
# ---------------------------------------------------------------------------

def _draw_mesh_textured(gpu_mesh: GpuMesh, prog, tex_cache) -> None:
    """
    Draw *gpu_mesh* with per-range texture binding.

    For each entry in gpu_mesh.tex_ranges the texture is looked up in
    *tex_cache*; if found, uHasTex=1 and the texture is bound to unit 0;
    otherwise uHasTex=0 (shader falls back to the solid category tint).
    The VAO is bound/unbound here; the caller must NOT call glBindVertexArray.
    """
    import ctypes
    from OpenGL import GL  # type: ignore

    GL.glBindVertexArray(gpu_mesh.vao)
    GL.glActiveTexture(GL.GL_TEXTURE0)

    for tex_name, byte_off, count in gpu_mesh.tex_ranges:
        tex_id = tex_cache.get(tex_name) if tex_name else 0
        if tex_id:
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
            prog.set_int("uHasTex", 1)
        else:
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            prog.set_int("uHasTex", 0)
        GL.glDrawElements(
            GL.GL_TRIANGLES, count,
            GL.GL_UNSIGNED_INT, ctypes.c_void_p(byte_off),
        )

    GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
    GL.glBindVertexArray(0)


def _draw_mesh_resolved_ranges(gpu_mesh: GpuMesh, ranges: List[BspDrawRange], prog) -> None:
    """
    Draw a textured BSP mesh using texture IDs resolved during cache build.

    This avoids path lookup and lazy texture-cache probing on every frame.
    """
    import ctypes
    from OpenGL import GL  # type: ignore

    GL.glBindVertexArray(gpu_mesh.vao)
    GL.glActiveTexture(GL.GL_TEXTURE0)

    for item in ranges:
        if item.texture_id:
            GL.glBindTexture(GL.GL_TEXTURE_2D, item.texture_id)
            prog.set_int("uHasTex", 1)
        else:
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            prog.set_int("uHasTex", 0)
        GL.glDrawElements(
            GL.GL_TRIANGLES,
            item.index_count,
            GL.GL_UNSIGNED_INT,
            ctypes.c_void_p(item.byte_offset),
        )

    GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
    GL.glBindVertexArray(0)


# Category colour table (linear RGB 0-1) used by draw_bsp
_MODEL_COLORS: Dict[str, Tuple[float, float, float]] = {
    "main":     (0.22, 0.38, 0.52),
    "submodel": (0.28, 0.28, 0.42),
    "terrain":  (0.28, 0.38, 0.24),
    "skybox":   (0.15, 0.20, 0.35),
}
_MODEL_COLOR_DEFAULT: Tuple[float, float, float] = (0.25, 0.30, 0.35)


def build_bsp_draw_batch(
    bsp_world,
    cache: "MeshCache",
    tex_cache=None,
    show_submodels: bool = True,
    show_skybox: bool = False,
    show_terrain: bool = True,
    helper_bsp_mode: str = "solid",
) -> BspDrawBatch:
    """Upload visible static BSP meshes and precompute their draw ranges."""
    if bsp_world is None:
        return BspDrawBatch([], 0, 0)

    items: List[BspDrawItem] = []
    tris_drawn = 0

    helper_mode = str(helper_bsp_mode or "solid").lower()
    for model in bsp_world.world_models:
        is_helper = _is_helper_bsp_model(model)
        if is_helper and helper_mode == "hidden":
            continue
        cat = model.category()
        if cat == "skybox" and not show_skybox:
            continue
        if cat == "submodel" and not show_submodels:
            continue
        if cat == "terrain" and not show_terrain:
            continue

        gm = cache.get_or_upload(model, tex_cache=tex_cache)
        if gm is None or gm.is_empty():
            continue

        color = _MODEL_COLORS.get(gm.category, _MODEL_COLOR_DEFAULT)
        alpha = 1.0
        wireframe = False
        ranges: List[BspDrawRange] = []
        if is_helper and helper_mode in {"solid", "wireframe"}:
            color = (0.95, 0.18, 0.62)
            alpha = 0.35 if helper_mode == "solid" else 0.85
            wireframe = helper_mode == "wireframe"
        elif tex_cache is not None and gm.tex_ranges:
            for tex_name, byte_off, count in gm.tex_ranges:
                tex_id = tex_cache.get(tex_name) if tex_name else 0
                ranges.append(BspDrawRange(
                    texture_id=int(tex_id or 0),
                    byte_offset=int(byte_off),
                    index_count=int(count),
                ))

        items.append(BspDrawItem(
            mesh=gm,
            color=color,
            ranges=ranges,
            alpha=alpha,
            wireframe=wireframe,
        ))
        tris_drawn += gm.triangle_count

    return BspDrawBatch(
        items=items,
        models_drawn=len(items),
        triangles_drawn=tris_drawn,
    )


def draw_bsp_batch(
    batch: BspDrawBatch,
    solid_prog,
    mvp: np.ndarray,
    light_dir: Tuple[float, float, float] = (0.4, 0.9, 0.3),
    fog_enabled: bool = False,
    fog_near: float = 500.0,
    fog_far: float = 3000.0,
    fog_color: Tuple[float, float, float] = (0.055, 0.063, 0.086),
) -> Tuple[int, int]:
    """Draw a precomputed static BSP batch."""
    with solid_prog as prog:
        prog.set_mat4("uMVP", mvp)
        prog.set_vec3("uLightDir", light_dir)
        prog.set_float("uAlpha", 1.0)

        prog.set_int("uFogEnabled", 1 if fog_enabled else 0)
        prog.set_float("uFogNear", fog_near)
        prog.set_float("uFogFar", fog_far)
        prog.set_vec3("uFogColor", fog_color)

        prog.set_int("uTex", 0)
        prog.set_int("uUseTexAlpha", 1)

        for item in batch.items:
            prog.set_vec3("uColor", item.color)
            prog.set_float("uAlpha", float(item.alpha))
            if item.wireframe:
                from OpenGL import GL  # type: ignore
                GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE)
                try:
                    prog.set_int("uHasTex", 0)
                    draw_mesh(item.mesh)
                finally:
                    GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_FILL)
            elif item.ranges:
                _draw_mesh_resolved_ranges(item.mesh, item.ranges, prog)
            else:
                prog.set_int("uHasTex", 0)
                draw_mesh(item.mesh)
            prog.set_float("uAlpha", 1.0)

    return batch.models_drawn, batch.triangles_drawn


def draw_bsp(
    bsp_world,
    cache: "MeshCache",
    solid_prog,                          # ShaderProgram
    mvp: np.ndarray,
    light_dir:      Tuple[float, float, float] = (0.4, 0.9, 0.3),
    show_submodels: bool  = True,
    show_skybox:    bool  = False,
    show_terrain:   bool  = True,
    fog_enabled:    bool  = False,
    fog_near:       float = 500.0,
    fog_far:        float = 3000.0,
    fog_color:      Tuple[float, float, float] = (0.055, 0.063, 0.086),
    tex_cache       = None,              # Optional[TextureCache] from dtx.py
) -> Tuple[int, int]:
    """
    Draw all visible BSP models in *bsp_world* using *solid_prog*.

    The shader must already be compiled; this function calls use() internally.

    fog_enabled / fog_near / fog_far / fog_color are forwarded to the
    SOLID shader as uFogEnabled / uFogNear / uFogFar / uFogColor.
    fog_color should match the GL clear colour so fog fades to background.

    tex_cache (optional): a dtx.TextureCache instance.  When provided, each
    model is drawn in per-texture sub-ranges (uHasTex=1 for found textures,
    uHasTex=0 fallback for missing ones).  When None, all geometry is drawn
    with the solid category-colour tint (uHasTex=0).

    Returns
    -------
    (models_drawn, triangles_drawn)  — useful for the status bar.
    """
    models_drawn = 0
    tris_drawn   = 0

    with solid_prog as prog:
        prog.set_mat4("uMVP",        mvp)
        prog.set_vec3("uLightDir",   light_dir)
        prog.set_float("uAlpha",     1.0)

        # Fog uniforms — always set so the shader has valid values even when off
        prog.set_int("uFogEnabled",  1 if fog_enabled else 0)
        prog.set_float("uFogNear",   fog_near)
        prog.set_float("uFogFar",    fog_far)
        prog.set_vec3("uFogColor",   fog_color)

        # uTex always points at texture unit 0
        prog.set_int("uTex", 0)
        # BSP textures use clip-alpha for foliage, fences, and grates.
        prog.set_int("uUseTexAlpha", 1)

        for model in bsp_world.world_models:
            # Visibility category filter
            cat = model.category()
            if cat == "skybox"   and not show_skybox:    continue
            if cat == "submodel" and not show_submodels: continue
            if cat == "terrain"  and not show_terrain:   continue

            gm = cache.get_or_upload(model, tex_cache=tex_cache)
            if gm is None or gm.is_empty():
                continue

            color = _MODEL_COLORS.get(gm.category, _MODEL_COLOR_DEFAULT)
            prog.set_vec3("uColor", color)

            if tex_cache is not None and gm.tex_ranges:
                # Textured path: iterate per-texture sub-ranges
                _draw_mesh_textured(gm, prog, tex_cache)
            else:
                # Untextured fallback: solid category-colour tint
                prog.set_int("uHasTex", 0)
                draw_mesh(gm)

            models_drawn += 1
            tris_drawn   += gm.triangle_count

    return models_drawn, tris_drawn


# ---------------------------------------------------------------------------
# Mesh cache — keyed by id(WorldModelMesh)
# ---------------------------------------------------------------------------

class MeshCache:
    """
    Caches GpuMesh objects by Python id() of the source WorldModelMesh.
    Call invalidate() when a new level is loaded to free stale GPU memory.
    """

    def __init__(self) -> None:
        self._cache: Dict[Tuple[int, Optional[int]], Optional[GpuMesh]] = {}

    def get_or_upload(self, mesh, tex_cache=None) -> Optional[GpuMesh]:  # type: ignore[type-arg]
        key = (id(mesh), id(tex_cache) if tex_cache is not None else None)
        if key not in self._cache:
            self._cache[key] = upload_model(mesh, tex_cache=tex_cache)
        return self._cache[key]

    def invalidate(self) -> None:
        """Delete all cached GPU meshes and clear the cache."""
        for gm in self._cache.values():
            if gm is not None:
                try:
                    delete_mesh(gm)
                except Exception:
                    pass
        self._cache.clear()

    def retain_models(self, meshes, tex_cache=None) -> None:
        """Drop cached meshes that are not part of the current BSP preview."""
        tex_id = id(tex_cache) if tex_cache is not None else None
        keep_model_ids = {id(mesh) for mesh in meshes or []}
        for key, gm in list(self._cache.items()):
            model_id, cached_tex_id = key
            if model_id in keep_model_ids and cached_tex_id == tex_id:
                continue
            if gm is not None:
                try:
                    delete_mesh(gm)
                except Exception:
                    pass
            del self._cache[key]

    @property
    def stats(self) -> dict:
        """Aggregate geometry stats across all cached meshes."""
        total_tris = sum(
            gm.triangle_count for gm in self._cache.values() if gm is not None)
        total_verts = sum(
            gm.vertex_count for gm in self._cache.values() if gm is not None)
        n_models = sum(1 for gm in self._cache.values() if gm is not None)
        return dict(models=n_models, triangles=total_tris, vertices=total_verts)

    def __len__(self) -> int:
        return len(self._cache)
