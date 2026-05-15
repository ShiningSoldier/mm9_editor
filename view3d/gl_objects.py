"""
gl_objects.py
=============

Billboard point-sprite rendering for WorldObjects (NPCs, props, lights, …).

Each WorldObject with a Pos property is rendered as a circular point sprite
coloured by its catalog category.  The selected object gets a white ring.

Two passes are supported:
  - Normal render  (uPickMode = 0): coloured sprites, white selection ring
  - Picking pass   (uPickMode = 1): each sprite's colour encodes its
    world_index, allowing CPU-side decode of which object is under the cursor.

Vertex layout (interleaved, 24 bytes/vertex):
    offset  0 : vec3  position  (x, y, z)
    offset 12 : vec3  colour    (r, g, b)  normalised 0..1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

# Colour table (RGB 0..1) — mirrors CATEGORY_COLORS from catalog.py but as
# float triples so we don't need to parse hex strings at upload time.
_CAT_COLORS: Dict[str, Tuple[float, float, float]] = {
    "spawn":       (0.40, 1.00, 0.40),   # bright green
    "npc_civilian":(1.00, 0.50, 0.10),   # orange-red
    "npc_named":   (1.00, 0.15, 0.15),   # bright red
    "monster":     (0.55, 0.05, 0.05),   # dark red
    "creature":    (0.75, 0.70, 0.45),   # sandy
    "prop":        (0.30, 0.70, 0.30),   # green
    "light":       (1.00, 1.00, 0.30),   # yellow
    "sound":       (0.55, 0.20, 0.80),   # purple
    "door":        (0.55, 0.35, 0.15),   # brown
    "trigger":     (0.25, 0.55, 1.00),   # blue
    "marker":      (0.55, 0.55, 0.55),   # grey
    "world":       (0.40, 0.50, 0.55),   # slate
    "interactive": (0.80, 1.00, 0.25),   # yellow-green
    "other":       (0.50, 0.50, 0.50),   # neutral grey
}
_DEFAULT_COLOR = (0.50, 0.50, 0.50)
_EDITOR_HELPER_CATEGORIES = {"trigger", "sound", "marker", "world", "light", "door"}
_EDITOR_HELPER_CLASSES = {
    "BlueWater", "StartPoint", "ScriptObject", "EffectsMgr", "WorldObject",
    "EarthQuake", "Ladder", "Switch", "Fire", "Camera", "DestructableBrush",
    "WeatherMan", "Fog", "Spawner", "Button", "Shooter"
}
_EDITOR_HELPER_NAME_PREFIXES = ("AITrk",)


def _cat_color(cat: str) -> Tuple[float, float, float]:
    return _CAT_COLORS.get(cat, _DEFAULT_COLOR)


def is_editor_helper_billboard(obj, categorize) -> bool:
    """Return True for editor/control objects that are noisy as billboards."""
    if getattr(obj, "type_str", "") in _EDITOR_HELPER_CLASSES:
        return True
    name = ""
    try:
        name = str(obj.get("Name") or "")
    except Exception:
        pass
    if any(name.startswith(prefix) for prefix in _EDITOR_HELPER_NAME_PREFIXES):
        return True
    return categorize(obj.type_str) in _EDITOR_HELPER_CATEGORIES


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ObjectSprites:
    """
    GPU-side point-sprite batch for all WorldObjects in one level.

    world_indices[i]  — the world_index of the i-th point in the VBO,
                        used to decode picking results.
    positions         — (N, 3) float32 ndarray of world-space XYZ for each
                        sprite, mirroring the VBO data on the CPU.  Used by
                        gl_view._render() to sort sprites back-to-front each
                        frame without downloading data from the GPU.
    """
    vao:          int
    vbo:          int
    count:        int
    world_indices: List[int]       = field(default_factory=list)
    positions:     "np.ndarray"    = field(
        default_factory=lambda: np.zeros((0, 3), dtype=np.float32)
    )


# ---------------------------------------------------------------------------
# Build CPU arrays (no GL dependency)
# ---------------------------------------------------------------------------

def _build_arrays(
    objects,              # List[WorldObject]  (patcher.WorldObject)
    categorize,           # Callable[[str], str]
    include_helpers: bool = True,
    selected_index: int = -1,
) -> Tuple[np.ndarray, List[int]]:
    """
    Build the interleaved vertex array and parallel world_indices list.

    Returns (verts: float32 (N, 6), world_indices: List[int]).
    Objects without a Pos property are skipped.
    """
    rows:    List[np.ndarray] = []
    w_idxs:  List[int]        = []

    for world_idx, obj in enumerate(objects):
        if (not include_helpers
                and world_idx != selected_index
                and is_editor_helper_billboard(obj, categorize)):
            continue

        pos = obj.get("Pos")
        if pos is None:
            continue
        try:
            x, y, z = float(pos[0]), float(pos[1]), float(pos[2])
        except (TypeError, IndexError, ValueError):
            continue

        cat = categorize(obj.type_str)
        r, g, b = _cat_color(cat)
        rows.append(np.array([x, y, z, r, g, b], dtype=np.float32))
        w_idxs.append(world_idx)

    if not rows:
        return np.zeros((0, 6), dtype=np.float32), []

    return np.vstack(rows).astype(np.float32), w_idxs


# ---------------------------------------------------------------------------
# GPU upload / draw / delete
# ---------------------------------------------------------------------------

def upload_objects(
    objects,        # List[WorldObject]
    categorize,     # Callable[[str], str]  — from catalog.categorize
    include_helpers: bool = True,
    selected_index: int = -1,
) -> Optional[ObjectSprites]:
    """
    Build and upload the point-sprite VBO for all WorldObjects in *objects*.
    Returns None if there are no objects with a Pos property.
    Requires a live GL context.
    """
    from OpenGL import GL  # type: ignore

    verts, w_idxs = _build_arrays(
        objects,
        categorize,
        include_helpers=include_helpers,
        selected_index=selected_index,
    )
    if verts.shape[0] == 0:
        return None

    vao = GL.glGenVertexArrays(1)
    vbo = GL.glGenBuffers(1)

    GL.glBindVertexArray(vao)
    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
    GL.glBufferData(GL.GL_ARRAY_BUFFER,
                    verts.nbytes, verts, GL.GL_DYNAMIC_DRAW)

    stride = 6 * 4  # 6 floats × 4 bytes
    GL.glEnableVertexAttribArray(0)
    GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, stride, None)
    GL.glEnableVertexAttribArray(1)
    GL.glVertexAttribPointer(1, 3, GL.GL_FLOAT, GL.GL_FALSE, stride,
                             GL.ctypes.c_void_p(12))
    GL.glBindVertexArray(0)

    return ObjectSprites(
        vao=int(vao), vbo=int(vbo),
        count=verts.shape[0],
        world_indices=w_idxs,
        positions=verts[:, :3].copy(),   # CPU-side XYZ for back-to-front sort
    )


def draw_sprites(
    sprites:          ObjectSprites,
    selected_index:   int = -1,
    point_size:       float = 14.0,
    pick_mode:        bool = False,
) -> None:
    """
    Draw all sprites.  The caller must have bound the BILLBOARD shader and
    set uMVP before calling.

    In pick_mode the shader encodes each sprite's world_index as RGB —
    the caller reads back the pixel to determine which object was clicked.
    """
    from OpenGL import GL  # type: ignore
    from view3d.gl_shader import ShaderProgram  # for uniform names (not used directly here)

    # The shader uniforms (uPickMode, uSelected, uPointSize, uObjectIndex)
    # are set via the ShaderProgram wrapper by the caller in gl_view.py.
    # Here we just issue the draw call.
    GL.glBindVertexArray(sprites.vao)
    GL.glDrawArrays(GL.GL_POINTS, 0, sprites.count)
    GL.glBindVertexArray(0)


def decode_pick_color(r: int, g: int, b: int) -> int:
    """
    Decode the RGB triplet written by the picking pass back to a world_index.
    Returns -1 if r==g==b==255 (background / no object).
    """
    if r == 255 and g == 255 and b == 255:
        return -1
    return r | (g << 8) | (b << 16)


def delete_sprites(sprites: ObjectSprites) -> None:
    """Free GPU resources."""
    from OpenGL import GL  # type: ignore
    GL.glDeleteVertexArrays(1, [sprites.vao])
    GL.glDeleteBuffers(1, [sprites.vbo])
