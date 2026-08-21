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

from catalog.world_helpers import (
    helper_value,
    object_has_actor_signature,
    object_model_resource,
)
from view3d.coords import game_to_display_point

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
_MODELED_CATEGORIES = {"npc_civilian", "npc_named", "monster", "creature", "prop"}

def _cat_color(cat: str) -> Tuple[float, float, float]:
    return _CAT_COLORS.get(cat, _DEFAULT_COLOR)


def _object_is_visible(obj) -> bool:
    """Mirror the model renderer's DAT ``Visible`` interpretation."""
    visible = obj.get("Visible")
    if visible is None:
        return True
    try:
        return bool(int(visible))
    except Exception:
        return bool(visible)


def _helper_metadata_for_type(metadata, type_name: str):
    if not metadata:
        return None
    entry = metadata.get(type_name)
    if entry is not None:
        return entry
    folded = type_name.casefold()
    for name, candidate in metadata.items():
        if str(name).casefold() == folded:
            return candidate
    return None


def is_world_helper_billboard(obj, categorize, world_helper_metadata=None) -> bool:
    """Return whether *obj* is a non-actor object without a model resource."""
    # Per-instance DAT evidence wins over class metadata.  This matters for
    # generic/special classes whose individual instances supply a model.
    if object_model_resource(obj) or object_has_actor_signature(obj):
        return False

    type_name = str(getattr(obj, "type_str", "") or "")
    decision = helper_value(
        _helper_metadata_for_type(world_helper_metadata, type_name)
    )
    if decision is not None:
        return decision

    # Catalog-free fallback for older/custom levels.  Categories only identify
    # broad visible-object families; no individual MM9 or LoMM classes live here.
    category = categorize(type_name) if callable(categorize) else "other"
    return category not in _MODELED_CATEGORIES


def is_editor_helper_billboard(obj, categorize, world_helper_metadata=None) -> bool:
    """Backward-compatible alias for world/service helper billboards."""
    return is_world_helper_billboard(obj, categorize, world_helper_metadata)


def hidden_world_helper_model_names(
    objects,
    categorize,
    world_helper_metadata=None,
) -> set[str]:
    """Return BSP model names owned by invisible world-helper objects.

    LithTech pairs some service objects with a same-named BSP submodel.  The
    submodel does not necessarily use a recognizable helper texture (MM9's
    ``AIBarrier`` models use the material name ``Default``), so the object is
    the authoritative visibility/classification record.
    """
    names: set[str] = set()
    normally_rendered_controller_classes = {
        "bluewater",
        "clearwater",
        "dirtywater",
    }
    for obj in objects or ():
        if _object_is_visible(obj):
            continue
        if str(getattr(obj, "type_str", "") or "").casefold() in normally_rendered_controller_classes:
            # Water controller objects are authored invisible, but their
            # same-named BSP is the visible water surface. Marker/material
            # classification controls its normal/helper rendering.
            continue
        if not is_world_helper_billboard(
            obj,
            categorize,
            world_helper_metadata,
        ):
            continue
        name = str(obj.get("Name") or "").strip()
        if name:
            names.add(name.casefold())
    return names


def should_draw_billboard_for_modeled_object(
    world_index: int,
    modeled_world_indices,
    selected_index: int = -1,
    drag_index: int = -1,
    show_object_helpers: bool = False,
) -> bool:
    """Return whether a billboard should be drawn for an object sprite."""
    if world_index not in modeled_world_indices:
        return True
    if world_index == selected_index or world_index == drag_index:
        return True
    return bool(show_object_helpers)


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
    include_world_helpers: bool = True,
    include_helpers: Optional[bool] = None,
    object_helper_indices=None,
    selected_index: int = -1,
    world_helper_metadata=None,
) -> Tuple[np.ndarray, List[int]]:
    """
    Build the interleaved vertex array and parallel world_indices list.

    Returns (verts: float32 (N, 6), world_indices: List[int]).
    Objects without a Pos property are skipped.
    """
    rows:    List[np.ndarray] = []
    w_idxs:  List[int]        = []

    if include_helpers is not None:
        include_world_helpers = bool(include_helpers)
    object_helper_indices = set(object_helper_indices or ())

    for world_idx, obj in enumerate(objects):
        # Visible=0 still suppresses the object's rendered model, but the
        # world-helper toggle exposes its editor gizmo.  Keep a selected
        # invisible object addressable even while helpers are hidden.
        if (world_idx != selected_index
                and not include_world_helpers
                and not _object_is_visible(obj)):
            continue
        if (not include_world_helpers
                and world_idx != selected_index
                and world_idx not in object_helper_indices
                and is_world_helper_billboard(
                    obj, categorize, world_helper_metadata
                )):
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
        dx, dy, dz = game_to_display_point((x, y, z))
        rows.append(np.array([dx, dy, dz, r, g, b], dtype=np.float32))
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
    include_world_helpers: bool = True,
    include_helpers: Optional[bool] = None,
    object_helper_indices=None,
    selected_index: int = -1,
    world_helper_metadata=None,
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
        include_world_helpers=include_world_helpers,
        include_helpers=include_helpers,
        object_helper_indices=object_helper_indices,
        selected_index=selected_index,
        world_helper_metadata=world_helper_metadata,
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
