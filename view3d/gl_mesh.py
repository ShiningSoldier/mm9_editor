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
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np


# Vertices beyond this value are from corrupted BSP sections and must be
# dropped before they overflow GL buffer ranges.
_COORD_SANITY: float = 1.0e6

# Minimum cross-product magnitude to accept a triangle as non-degenerate.
# Triangles below this threshold are co-linear / zero-area and should be
# silently dropped (their normals would be garbage).
_AREA_EPSILON: float = 1.0e-8

# LithTech surface flags from runtime/world/src/de_world.h.  Invisible surfaces
# remain useful for collision/visibility, while sky surfaces are portals into
# the engine-managed sky scene.  Neither is ordinary textured world geometry.
_SURF_INVISIBLE = 1 << 2
_SURF_SKY = 1 << 4

_NON_RENDER_TEXTURE_TOKENS = (
    "/LEVELTEXTURES/MISC/RAIL.DTX",
    "/LEVELTEXTURES/MISC/SOUNDONLY.DTX",
    "/LEVELTEXTURES/MISC/INVISIBLE.DTX",
    "/LEVELTEXTURES/MISC/GREENSCREEN.DTX",
    "/LEVELTEXTURES/INVISIBLE.DTX",
    "/SKYBOX/SKYMARKER.DTX",
)

_HELPER_SOLID_TEXTURE_TOKENS = (
    "/LEVELTEXTURES/MISC/FIRETHROUGH.DTX",
)

_WATER_PLACEHOLDER_TEXTURE = "TEXTURES\\LevelTextures\\Terrain\\Ocean.dtx"

HELPER_ROLE_GROUPS = (
    "aiRail",
    "collision",
    "water",
    "trigger",
    "sound",
    "skyVisibility",
)
DEFAULT_HELPER_ROLE_GROUPS = frozenset(HELPER_ROLE_GROUPS)

_HELPER_ROLE_COLORS: Dict[str, Tuple[float, float, float]] = {
    "aiRail": (1.0, 0.82, 0.13),
    "collision": (1.0, 0.20, 0.65),
    "water": (0.0, 0.78, 1.0),
    "trigger": (0.0, 1.0, 0.30),
    "sound": (0.20, 0.50, 1.0),
    "skyVisibility": (0.35, 0.45, 1.0),
}


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


def _helper_role_group_for_texture(tex_name: str) -> Optional[str]:
    """Return the editor helper overlay group for *tex_name*, if any."""
    norm = _normalise_texture_name(tex_name)
    if "/LEVELTEXTURES/MISC/RAIL.DTX" in norm:
        return "aiRail"
    if ("/LEVELTEXTURES/MISC/FIRETHROUGH.DTX" in norm
            or "/LEVELTEXTURES/MISC/INVISIBLE.DTX" in norm
            or "/LEVELTEXTURES/INVISIBLE.DTX" in norm):
        return "collision"
    if ("/LEVELTEXTURES/TERRAIN/WATERMARKER.DTX" in norm
            or "/SPRITES/WATER/" in norm
            or "/SPRITETEXTURES/WATER/" in norm):
        return "water"
    if "/LEVELTEXTURES/MISC/GREENSCREEN.DTX" in norm:
        return "trigger"
    if "/LEVELTEXTURES/MISC/SOUNDONLY.DTX" in norm:
        return "sound"
    if "/SKYBOX/SKYMARKER.DTX" in norm or norm.endswith(".SPR"):
        return "skyVisibility"
    return None


def _is_non_render_texture(tex_name: str) -> bool:
    """True for LithTech editor/helper BSP materials the game does not draw."""
    return _helper_role_group_for_texture(tex_name) is not None


def _is_helper_solid_texture(tex_name: str) -> bool:
    """True for helper materials that should draw as plain editor geometry."""
    return _helper_role_group_for_texture(tex_name) == "collision"


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


def _model_helper_role_group(model) -> Optional[str]:  # type: ignore[type-arg]
    name = str(getattr(model, "name", "") or "").lower()
    if "_collision" in name:
        return "collision"
    return None


def _surface_helper_role_group(surface) -> Optional[str]:  # type: ignore[type-arg]
    """Classify helper geometry encoded by LithTech surface flags."""
    flags = int(getattr(surface, "flags", 0)) if surface is not None else 0
    if flags & _SURF_SKY:
        return "skyVisibility"
    if flags & _SURF_INVISIBLE:
        return "collision"
    return None


def _is_visibility_bsp_model(model) -> bool:  # type: ignore[type-arg]
    """True for BSP records used by the engine as visibility/PVS data."""
    name = str(getattr(model, "name", "") or "").lower()
    return name == "visbsp"


def _normal_render_model_points(
    model,
    model_helper_role: Optional[str] = None,
) -> Optional[np.ndarray]:  # type: ignore[type-arg]
    """Return game-space points from polygons drawn in normal mode."""
    if model.is_skybox() or _is_visibility_bsp_model(model):
        return None
    if model_helper_role is not None or _model_helper_role_group(model) is not None:
        return None

    points = np.asarray(getattr(model, "points", []) or [], dtype=np.float64)
    if points.ndim != 2 or points.shape[1:] != (3,):
        return None
    surfaces = getattr(model, "surfaces", []) or []
    textures = getattr(model, "texture_names", []) or []
    visible_points = []
    for polygon in getattr(model, "polygons", []) or []:
        surface_index = int(getattr(polygon, "surface_index", -1))
        surface = (
            surfaces[surface_index]
            if 0 <= surface_index < len(surfaces)
            else None
        )
        texture_name = ""
        texture_index = int(getattr(surface, "texture_index", -1)) if surface else -1
        if 0 <= texture_index < len(textures):
            texture_name = str(textures[texture_index] or "")
        helper_role = (
            _surface_helper_role_group(surface)
            or _helper_role_group_for_texture(texture_name)
        )
        if helper_role is not None and helper_role != "water":
            continue

        indices = list(getattr(polygon, "vertex_indices", []) or [])
        if len(indices) < 3 or any(index < 0 or index >= len(points) for index in indices):
            continue
        polygon_points = points[indices]
        if (not np.all(np.isfinite(polygon_points))
                or np.any(np.abs(polygon_points) > _COORD_SANITY)
                or _is_physics_world_ceiling_cap(model, polygon_points)):
            continue
        visible_points.append(polygon_points)

    if not visible_points:
        return None
    return np.concatenate(visible_points, axis=0)


def normal_render_world_bounds(
    bsp_world,
    hidden_helper_model_names: Optional[Set[str]] = None,
) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
    """Bounds of BSP models that contribute ordinary viewport geometry."""
    hidden_helpers = {
        str(name).casefold() for name in (hidden_helper_model_names or ())
    }
    visible_points = []
    for model in getattr(bsp_world, "world_models", []) or []:
        is_hidden_helper = (
            str(getattr(model, "name", "") or "").casefold() in hidden_helpers
        )
        model_points = _normal_render_model_points(
            model,
            model_helper_role="collision" if is_hidden_helper else None,
        )
        if model_points is not None:
            visible_points.append(model_points)

    if not visible_points:
        return None

    points = np.concatenate(visible_points, axis=0)
    lo = np.min(points, axis=0)
    hi = np.max(points, axis=0)
    return (
        (float(lo[0]), float(lo[1]), float(lo[2])),
        (float(hi[0]), float(hi[1]), float(hi[2])),
    )


def _is_physics_world_ceiling_cap(model, pv: np.ndarray) -> bool:  # type: ignore[type-arg]
    """
    True for huge PhysicsBSP top-boundary caps that close the collision world.

    STURMFORD ships one of these with a normal terrain texture instead of a
    helper material, so texture-based filtering cannot identify it.
    """
    name = str(getattr(model, "name", "") or "").lower()
    if name != "physicsbsp" or pv.shape[0] < 3:
        return False

    min_box = getattr(model, "min_box", None)
    max_box = getattr(model, "max_box", None)
    if not min_box or not max_box:
        return False

    max_y = float(max_box[1])
    if float(np.max(np.abs(pv[:, 1] - max_y))) > 2.0:
        return False

    bounds_x = abs(float(max_box[0]) - float(min_box[0]))
    bounds_z = abs(float(max_box[2]) - float(min_box[2]))
    bounds_area = bounds_x * bounds_z
    if bounds_area <= 1.0:
        return False

    span_x = float(np.max(pv[:, 0]) - np.min(pv[:, 0]))
    span_z = float(np.max(pv[:, 2]) - np.min(pv[:, 2]))
    return (span_x * span_z) >= bounds_area * 0.5



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
    helper_role: Optional[str] = None

    # CPU-side triangle positions: shape (T, 3, 3) float32 where axis 1 indexes
    # vertex 0/1/2 and axis 2 is XYZ.  Retained after GPU upload so ray-triangle
    # intersection can be done without a GPU readback.  None when mesh is empty.
    tri_positions: Optional[np.ndarray] = None

    # Half-extents attached to the ABC model's initial animation. LithTech's
    # Prop runtime uses these dimensions when resolving MoveToFloor.
    model_user_dims: Optional[Tuple[float, float, float]] = None
    model_no_animation: bool = False
    model_bottom_pivot_offset_y: float = 0.0

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
    alpha_mode: str = "opaque"  # "opaque", "cutout", or "blend"


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


@dataclass
class SkyDrawLayer:
    """One camera-relative sky world model and its authored draw index."""

    name: str
    index: float
    mesh: GpuMesh
    ranges: List[BspDrawRange] = field(default_factory=list)
    alpha: float = 1.0


@dataclass
class SkyDrawBatch:
    """Portal mask plus ordered sky-world-model drawing data."""

    portal_meshes: List[GpuMesh]
    layers: List[SkyDrawLayer]
    all_sky_portals: bool = False


_BSP_ALPHA_BLEND_TOKENS = (
    "glass",
    "window",
    "mirror",
    "cloud",
    "fog",
    "smoke",
    "steam",
)


def _bsp_alpha_mode_for_texture(tex_name: str, tex_cache=None) -> str:
    """Classify one BSP texture for depth/blend ordering."""
    if not tex_name or tex_cache is None or not hasattr(tex_cache, "alpha_info"):
        return "opaque"

    info = tex_cache.alpha_info(tex_name)
    if info is None or not getattr(info, "has_useful_alpha", False):
        return "opaque"

    key = _normalise_texture_name(tex_name).lower()
    mid = float(getattr(info, "mid_fraction", 0.0))
    transparent = float(getattr(info, "transparent_fraction", 0.0))

    if any(token in key for token in _BSP_ALPHA_BLEND_TOKENS):
        return "blend"
    if mid > 0.05 and mid >= transparent:
        return "blend"
    if transparent > 0.02:
        return "cutout"
    return "opaque"


def _resolved_bsp_ranges(gm: GpuMesh, tex_cache=None) -> List[BspDrawRange]:
    """Resolve texture IDs and alpha modes for a BSP mesh once per batch."""
    ranges: List[BspDrawRange] = []
    if tex_cache is None or not gm.tex_ranges:
        return ranges

    for tex_name, byte_off, count in gm.tex_ranges:
        tex_id = tex_cache.get(tex_name) if tex_name else 0
        ranges.append(BspDrawRange(
            texture_id=int(tex_id or 0),
            byte_offset=int(byte_off),
            index_count=int(count),
            alpha_mode=_bsp_alpha_mode_for_texture(tex_name, tex_cache),
        ))
    return ranges


# ---------------------------------------------------------------------------
# Triangulation  (pure numpy, no GL dependency)
# ---------------------------------------------------------------------------

def _triangulate_model(
    mesh,
    tex_cache=None,
    helper_mode: str = "normal",
    helper_roles: Optional[Set[str]] = None,
    model_helper_role: Optional[str] = None,
    required_surface_flags: int = 0,
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

    helper_mode = str(helper_mode or "normal").lower()
    helper_roles = set(helper_roles or DEFAULT_HELPER_ROLE_GROUPS)

    pts_np        = np.asarray(pts, dtype=np.float32)   # (P, 3)
    surfaces      = getattr(mesh, "surfaces",      [])   # bsp.Surface list
    texture_names = getattr(mesh, "texture_names", [])   # List[str]
    n_surfaces    = len(surfaces)
    n_textures    = len(texture_names)
    model_helper_role = model_helper_role or _model_helper_role_group(mesh)
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
        pv_game = pts_np[vis]   # (V, 3)
        if np.any(np.abs(pv_game) > _COORD_SANITY):
            continue
        if _is_physics_world_ceiling_cap(mesh, pv_game):
            continue
        pv = pv_game.copy()
        pv[:, 0] *= -1.0

        # ── resolve surface + texture for this polygon ─────────────────
        si   = poly.surface_index
        surf = surfaces[si] if (n_surfaces > 0 and 0 <= si < n_surfaces) else None
        if required_surface_flags:
            flags = int(getattr(surf, "flags", 0)) if surf is not None else 0
            if flags & int(required_surface_flags) != int(required_surface_flags):
                continue

        tex_name = ""
        if (surf is not None
                and n_textures > 0
                and 0 <= surf.texture_index < n_textures):
            tex_name = texture_names[surf.texture_index]

        helper_role = (
            _surface_helper_role_group(surf)
            or _helper_role_group_for_texture(tex_name)
            or model_helper_role
        )
        if helper_mode == "helpers":
            if helper_role is None or helper_role not in helper_roles:
                continue
            tex_name = ""
        elif helper_mode == "raw":
            tex_name = _render_texture_name(tex_name)
        else:
            if helper_role is not None:
                if helper_role == "water":
                    tex_name = _render_texture_name(tex_name)
                else:
                    continue
            else:
                tex_name = _render_texture_name(tex_name)

        # ── per-vertex UV from LithTech OPQ surface projection ─────────
        if surf is not None:
            tex_w, tex_h = _texture_size(tex_name)
            uvs = [surf.compute_uv((float(pv_game[i, 0]),
                                    float(pv_game[i, 1]),
                                    float(pv_game[i, 2])),
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
        if _is_physics_world_ceiling_cap(mesh, pv):
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

def upload_model(
    mesh,
    tex_cache=None,
    helper_mode: str = "normal",
    helper_roles: Optional[Set[str]] = None,
    model_helper_role: Optional[str] = None,
    required_surface_flags: int = 0,
) -> Optional[GpuMesh]:  # type: ignore[type-arg]
    """
    Triangulate *mesh* and upload it to the GPU.
    Returns None if the model has no valid geometry after filtering.
    Requires a live GL context.
    """
    from OpenGL import GL  # type: ignore

    roles = set(helper_roles or [])
    verts, indices, tex_ranges = _triangulate_model(
        mesh,
        tex_cache=tex_cache,
        helper_mode=helper_mode,
        helper_roles=roles or None,
        model_helper_role=model_helper_role,
        required_surface_flags=required_surface_flags,
    )
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
        helper_role=next(iter(roles), None) if helper_mode == "helpers" and len(roles) == 1 else None,
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


def _draw_mesh_resolved_ranges(
    gpu_mesh: GpuMesh,
    ranges: List[BspDrawRange],
    prog,
    alpha_modes: Optional[Set[str]] = None,
) -> None:
    """
    Draw a textured BSP mesh using texture IDs resolved during cache build.

    This avoids path lookup and lazy texture-cache probing on every frame.
    """
    import ctypes
    from OpenGL import GL  # type: ignore

    GL.glBindVertexArray(gpu_mesh.vao)
    GL.glActiveTexture(GL.GL_TEXTURE0)

    for item in ranges:
        if alpha_modes is not None and item.alpha_mode not in alpha_modes:
            continue
        if item.texture_id:
            GL.glBindTexture(GL.GL_TEXTURE_2D, item.texture_id)
            prog.set_int("uHasTex", 1)
            prog.set_int("uUseTexAlpha", 1 if item.alpha_mode != "opaque" else 0)
        else:
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            prog.set_int("uHasTex", 0)
            prog.set_int("uUseTexAlpha", 0)
        GL.glDrawElements(
            GL.GL_TRIANGLES,
            item.index_count,
            GL.GL_UNSIGNED_INT,
            ctypes.c_void_p(item.byte_offset),
        )

    GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
    GL.glBindVertexArray(0)
    prog.set_int("uUseTexAlpha", 0)


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
    helper_bsp_mode: str = "normal",
    helper_role_groups: Optional[Set[str]] = None,
    hidden_helper_model_names: Optional[Set[str]] = None,
) -> BspDrawBatch:
    """Upload visible static BSP meshes and precompute their draw ranges."""
    if bsp_world is None:
        return BspDrawBatch([], 0, 0)

    items: List[BspDrawItem] = []
    tris_drawn = 0

    helper_mode = str(helper_bsp_mode or "normal").lower()
    if helper_mode in {"hidden"}:
        helper_mode = "normal"
    elif helper_mode in {"solid", "wireframe", "helpers", "translucent"}:
        helper_mode = "helpers"
    elif helper_mode != "raw":
        helper_mode = "normal"
    helper_roles = set(helper_role_groups or DEFAULT_HELPER_ROLE_GROUPS)
    hidden_helper_models = {
        str(name).casefold() for name in (hidden_helper_model_names or ())
    }

    for model in bsp_world.world_models:
        model_helper_role = (
            "collision"
            if str(getattr(model, "name", "") or "").casefold()
            in hidden_helper_models
            else None
        )
        cat = model.category()
        if cat == "skybox" and not show_skybox and helper_mode != "helpers":
            continue
        if cat == "submodel" and not show_submodels:
            continue
        if cat == "terrain" and not show_terrain:
            continue

        if helper_mode == "raw":
            gm = cache.get_or_upload(
                model,
                tex_cache=tex_cache,
                helper_mode="raw",
                model_helper_role=model_helper_role,
            )
            if gm is None or gm.is_empty():
                continue
            items.append(BspDrawItem(
                mesh=gm,
                color=_MODEL_COLORS.get(gm.category, _MODEL_COLOR_DEFAULT),
                ranges=_resolved_bsp_ranges(gm, tex_cache),
            ))
            tris_drawn += gm.triangle_count
            continue

        if not _is_visibility_bsp_model(model):
            gm = cache.get_or_upload(
                model,
                tex_cache=tex_cache,
                helper_mode="normal",
                model_helper_role=model_helper_role,
            )
            if gm is not None and not gm.is_empty():
                items.append(BspDrawItem(
                    mesh=gm,
                    color=_MODEL_COLORS.get(gm.category, _MODEL_COLOR_DEFAULT),
                    ranges=_resolved_bsp_ranges(gm, tex_cache),
                ))
                tris_drawn += gm.triangle_count

        if helper_mode == "helpers":
            for role in HELPER_ROLE_GROUPS:
                if role not in helper_roles:
                    continue
                gm = cache.get_or_upload(
                    model,
                    tex_cache=tex_cache,
                    helper_mode="helpers",
                    helper_roles={role},
                    model_helper_role=model_helper_role,
                )
                if gm is None or gm.is_empty():
                    continue
                items.append(BspDrawItem(
                    mesh=gm,
                    color=_HELPER_ROLE_COLORS.get(role, _MODEL_COLOR_DEFAULT),
                    alpha=0.38,
                    wireframe=False,
                ))
                tris_drawn += gm.triangle_count

    return BspDrawBatch(
        items=items,
        models_drawn=len(items),
        triangles_drawn=tris_drawn,
    )


def build_sky_draw_batch(
    bsp_world,
    sky_scene,
    cache: "MeshCache",
    tex_cache=None,
    soft_sky_model=None,
) -> SkyDrawBatch:
    """Upload SURF_SKY portals and the object-resolved sky layers."""
    if bsp_world is None or sky_scene is None:
        return SkyDrawBatch([], [], False)

    portal_meshes = []
    for model in getattr(bsp_world, "world_models", []) or []:
        gm = cache.get_or_upload(
            model,
            tex_cache=tex_cache,
            helper_mode="helpers",
            helper_roles={"skyVisibility"},
            required_surface_flags=_SURF_SKY,
        )
        if gm is not None and not gm.is_empty():
            portal_meshes.append(gm)

    layers = []
    for layer in getattr(sky_scene, "layers", ()):
        model = bsp_world.model_by_name(layer.model_name)
        if model is None:
            continue
        gm = cache.get_or_upload(model, tex_cache=tex_cache, helper_mode="normal")
        if gm is None or gm.is_empty():
            continue
        layers.append(SkyDrawLayer(
            name=layer.model_name,
            index=float(layer.index),
            mesh=gm,
            ranges=_resolved_bsp_ranges(gm, tex_cache),
        ))

    if soft_sky_model is not None:
        gm = cache.get_or_upload(
            soft_sky_model,
            tex_cache=tex_cache,
            helper_mode="normal",
        )
        if gm is not None and not gm.is_empty():
            layers.append(SkyDrawLayer(
                name=soft_sky_model.name,
                index=(max((layer.index for layer in layers), default=0.0) + 1.0),
                mesh=gm,
                ranges=_resolved_bsp_ranges(gm, tex_cache),
                alpha=0.45,
            ))

    layers.sort(key=lambda layer: layer.index)
    return SkyDrawBatch(
        portal_meshes=portal_meshes,
        layers=layers,
        all_sky_portals=bool(getattr(sky_scene, "all_sky_portals", False)),
    )


def draw_sky_batch(
    batch: SkyDrawBatch,
    portal_prog,
    solid_prog,
    world_mvp: np.ndarray,
    sky_mvp: np.ndarray,
) -> Tuple[int, int]:
    """Render ordered sky layers only through visible SURF_SKY portals."""
    if not batch.portal_meshes or not batch.layers:
        return 0, 0

    from OpenGL import GL  # type: ignore

    # Portal fragments visible from the main camera write stencil=1. Depth
    # testing remains enabled, so portals hidden behind walls do not leak sky.
    GL.glEnable(GL.GL_STENCIL_TEST)
    GL.glStencilMask(0xFF)
    GL.glClear(GL.GL_STENCIL_BUFFER_BIT)
    GL.glColorMask(GL.GL_FALSE, GL.GL_FALSE, GL.GL_FALSE, GL.GL_FALSE)
    GL.glDepthMask(GL.GL_FALSE)
    GL.glStencilFunc(GL.GL_ALWAYS, 1, 0xFF)
    GL.glStencilOp(GL.GL_KEEP, GL.GL_KEEP, GL.GL_REPLACE)
    try:
        with portal_prog as prog:
            prog.set_mat4("uMVP", world_mvp)
            for mesh in batch.portal_meshes:
                draw_mesh(mesh)

        GL.glColorMask(GL.GL_TRUE, GL.GL_TRUE, GL.GL_TRUE, GL.GL_TRUE)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glStencilMask(0x00)
        GL.glStencilFunc(GL.GL_EQUAL, 1, 0xFF)
        GL.glStencilOp(GL.GL_KEEP, GL.GL_KEEP, GL.GL_KEEP)

        with solid_prog as prog:
            prog.set_mat4("uMVP", sky_mvp)
            prog.set_vec3("uLightDir", (0.0, 1.0, 0.0))
            prog.set_float("uAlpha", 1.0)
            prog.set_int("uUnlit", 1)
            prog.set_int("uFogEnabled", 0)
            prog.set_float("uFogNear", 0.0)
            prog.set_float("uFogFar", 1.0)
            prog.set_vec3("uFogColor", (0.0, 0.0, 0.0))
            prog.set_int("uTex", 0)
            prog.set_int("uUseTexAlpha", 0)

            for layer in batch.layers:
                prog.set_vec3("uColor", _MODEL_COLORS["skybox"])
                prog.set_float("uAlpha", float(layer.alpha))
                if layer.ranges:
                    _draw_mesh_resolved_ranges(layer.mesh, layer.ranges, prog)
                else:
                    prog.set_int("uHasTex", 0)
                    draw_mesh(layer.mesh)
            prog.set_float("uAlpha", 1.0)
            prog.set_int("uUnlit", 0)
    finally:
        GL.glColorMask(GL.GL_TRUE, GL.GL_TRUE, GL.GL_TRUE, GL.GL_TRUE)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glStencilMask(0xFF)
        GL.glDisable(GL.GL_STENCIL_TEST)

    return len(batch.layers), sum(layer.mesh.triangle_count for layer in batch.layers)


def draw_bsp_batch(
    batch: BspDrawBatch,
    solid_prog,
    mvp: np.ndarray,
    light_dir: Tuple[float, float, float] = (0.4, 0.9, 0.3),
    fog_enabled: bool = False,
    fog_near: float = 500.0,
    fog_far: float = 3000.0,
    fog_color: Tuple[float, float, float] = (0.055, 0.063, 0.086),
    render_pass: str = "all",
) -> Tuple[int, int]:
    """Draw all, opaque/cutout, or translucent parts of a BSP batch."""
    render_pass = str(render_pass or "all").casefold()
    if render_pass not in {"all", "opaque", "translucent"}:
        raise ValueError(f"unknown BSP render pass: {render_pass!r}")

    def _draw_item(item: BspDrawItem, prog, alpha_modes: Optional[Set[str]] = None) -> None:
        prog.set_vec3("uColor", item.color)
        prog.set_float("uAlpha", float(item.alpha))
        if item.wireframe:
            from OpenGL import GL  # type: ignore
            GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE)
            try:
                prog.set_int("uHasTex", 0)
                prog.set_int("uUseTexAlpha", 0)
                draw_mesh(item.mesh)
            finally:
                GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_FILL)
        elif item.ranges:
            _draw_mesh_resolved_ranges(item.mesh, item.ranges, prog, alpha_modes=alpha_modes)
        else:
            prog.set_int("uHasTex", 0)
            prog.set_int("uUseTexAlpha", 0)
            draw_mesh(item.mesh)
        prog.set_float("uAlpha", 1.0)

    with solid_prog as prog:
        prog.set_mat4("uMVP", mvp)
        prog.set_vec3("uLightDir", light_dir)
        prog.set_float("uAlpha", 1.0)
        prog.set_int("uUnlit", 0)

        prog.set_int("uFogEnabled", 1 if fog_enabled else 0)
        prog.set_float("uFogNear", fog_near)
        prog.set_float("uFogFar", fog_far)
        prog.set_vec3("uFogColor", fog_color)

        prog.set_int("uTex", 0)
        prog.set_int("uUseTexAlpha", 0)

        if render_pass in {"all", "opaque"}:
            for item in batch.items:
                if item.alpha < 1.0:
                    continue
                _draw_item(item, prog, alpha_modes={"opaque", "cutout"})

        if render_pass in {"all", "translucent"}:
            from OpenGL import GL  # type: ignore
            GL.glDepthMask(GL.GL_FALSE)
            try:
                for item in batch.items:
                    if item.alpha < 1.0:
                        _draw_item(item, prog)
                    elif item.ranges and any(r.alpha_mode == "blend" for r in item.ranges):
                        _draw_item(item, prog, alpha_modes={"blend"})
            finally:
                GL.glDepthMask(GL.GL_TRUE)
                prog.set_int("uUseTexAlpha", 0)

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
        prog.set_int("uUnlit",       0)

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
        self._cache: Dict[tuple, Optional[GpuMesh]] = {}

    def get_or_upload(
        self,
        mesh,
        tex_cache=None,
        helper_mode: str = "normal",
        helper_roles: Optional[Set[str]] = None,
        model_helper_role: Optional[str] = None,
        required_surface_flags: int = 0,
    ) -> Optional[GpuMesh]:  # type: ignore[type-arg]
        roles_key = tuple(sorted(helper_roles or ()))
        key = (
            id(mesh),
            id(tex_cache) if tex_cache is not None else None,
            str(helper_mode or "normal").lower(),
            roles_key,
            str(model_helper_role or "").casefold(),
            int(required_surface_flags),
        )
        if key not in self._cache:
            self._cache[key] = upload_model(
                mesh,
                tex_cache=tex_cache,
                helper_mode=helper_mode,
                helper_roles=set(helper_roles or ()),
                model_helper_role=model_helper_role,
                required_surface_flags=required_surface_flags,
            )
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

    def discard_model(self, mesh) -> None:
        """Delete every cached upload belonging to one source mesh."""
        model_id = id(mesh)
        for key, gm in list(self._cache.items()):
            if key[0] != model_id:
                continue
            if gm is not None:
                try:
                    delete_mesh(gm)
                except Exception:
                    pass
            del self._cache[key]

    def retain_models(self, meshes, tex_cache=None) -> None:
        """Drop cached meshes that are not part of the current BSP preview."""
        keep_model_ids = {id(mesh) for mesh in meshes or []}
        for key, gm in list(self._cache.items()):
            model_id = key[0]
            cached_tex_id = key[1]
            if model_id in keep_model_ids and cached_tex_id == (id(tex_cache) if tex_cache is not None else None):
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
