"""
gl_baked_props.py
=================

Opt-in baked static prop rendering for the editor viewport.

This module flattens currently supported ABC object previews into one OpenGL
mesh in display space. Selection, dragging, picking, and unsupported models
continue to use the existing per-object path in ``gl_object_models``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Set, Tuple

import numpy as np

from view3d.gl_mesh import GpuMesh, delete_mesh
from view3d.gl_object_models import (
    ObjectModelCache,
    ObjectModelRenderItem,
    _ALPHA_ORDER,
    _DEFAULT_COLOR,
    _alpha_mode_for_piece,
    _civilian_appearance_key,
    _object_matrix,
    _object_model_filename,
    _resolve_skin_for_piece,
    _texture_from_caches,
)


@dataclass
class BakedPropRange:
    """One drawable range inside a baked prop batch."""

    world_index: int
    alpha_mode: str
    byte_offset: int
    index_count: int
    texture_id: int


@dataclass
class BakedPropBatch:
    """Single-mesh static prop batch."""

    mesh: GpuMesh
    ranges: List[BakedPropRange] = field(default_factory=list)
    world_indices: Set[int] = field(default_factory=set)


def _transformed_normal(model: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    ma = model @ np.array([float(a[0]), float(a[1]), float(a[2]), 1.0], dtype=np.float32)
    mb = model @ np.array([float(b[0]), float(b[1]), float(b[2]), 1.0], dtype=np.float32)
    mc = model @ np.array([float(c[0]), float(c[1]), float(c[2]), 1.0], dtype=np.float32)
    n = np.cross(mb[:3] - ma[:3], mc[:3] - ma[:3])
    length = float(np.linalg.norm(n))
    if length <= 1.0e-8:
        return np.zeros(3, dtype=np.float32)
    return (n / length).astype(np.float32)


def _append_baked_piece(
    rows: List[List[float]],
    indices: List[int],
    abc,
    piece,
    model: np.ndarray,
) -> int:
    verts = getattr(piece, "vertices", []) or []
    tris = getattr(piece, "triangles", []) or []
    if not verts or not tris:
        return 0

    count = 0
    for tri in tris:
        refs = getattr(tri, "refs", ()) or ()
        if len(refs) != 3:
            continue
        if any(ref.vertex_index < 0 or ref.vertex_index >= len(verts) for ref in refs):
            continue

        src = [np.asarray(verts[ref.vertex_index].pos, dtype=np.float32) for ref in refs]
        normal = _transformed_normal(model, src[0], src[1], src[2])
        if not np.any(normal):
            continue

        for ref, point in zip(refs, src):
            p = model @ np.array([float(point[0]), float(point[1]), float(point[2]), 1.0], dtype=np.float32)
            rows.append([
                float(p[0]), float(p[1]), float(p[2]),
                float(normal[0]), float(normal[1]), float(normal[2]),
                float(ref.u), float(ref.v),
            ])
            indices.append(len(indices))
            count += 1
    return count


def build_baked_prop_arrays(
    items: Sequence[ObjectModelRenderItem],
    cache: ObjectModelCache,
    skin_cache=None,
    tex_cache=None,
    actor_visuals=None,
) -> Tuple[np.ndarray, np.ndarray, List[BakedPropRange], Set[int]]:
    """
    Build CPU vertex/index arrays for a baked prop batch.

    The output is display-space geometry with one draw range per object piece.
    Keeping object-local ranges lets the viewport suppress selected/dragged
    objects and draw those through the live per-object path instead.
    """
    rows: List[List[float]] = []
    indices: List[int] = []
    ranges: List[BakedPropRange] = []
    world_indices: Set[int] = set()

    order = {name: i for i, name in enumerate(_ALPHA_ORDER)}

    for item in items:
        filename = _object_model_filename(item.obj, actor_visuals=actor_visuals)
        abc = cache.get_or_load_abc(filename)
        if abc is None:
            continue
        model = _object_matrix(item.obj, y_override=item.y_override)
        if model is None:
            continue

        pieces = list(getattr(abc, "pieces", []) or [])
        if not pieces:
            continue

        object_type = str(getattr(item.obj, "type_str", "") or "")
        appearance_key = _civilian_appearance_key(object_type, str(item.obj.get("Name") or ""))

        item_range_start = len(ranges)
        for piece_index, piece in enumerate(pieces):
            piece_name = str(getattr(piece, "name", "") or getattr(piece, "texture_name", "") or item.mesh.model_name)
            skin = _resolve_skin_for_piece(
                piece_name,
                piece_index,
                len(pieces),
                item.skins,
                filename or item.mesh.model_name,
                object_type=object_type,
                appearance_key=appearance_key,
                skin_cache=skin_cache,
                tex_cache=tex_cache,
            )
            alpha_mode = _alpha_mode_for_piece(piece_name, skin, skin_cache, tex_cache)
            texture_id = int(_texture_from_caches(skin, skin_cache, tex_cache) or 0)
            start_index = len(indices)
            count = _append_baked_piece(rows, indices, abc, piece, model)
            if count:
                ranges.append(BakedPropRange(
                    world_index=int(item.world_index),
                    alpha_mode=alpha_mode,
                    byte_offset=start_index * 4,
                    index_count=count,
                    texture_id=texture_id,
                ))

        if len(ranges) > item_range_start:
            world_indices.add(int(item.world_index))

    if not rows:
        return (
            np.zeros((0, 8), dtype=np.float32),
            np.zeros(0, dtype=np.uint32),
            [],
            set(),
        )

    ranges.sort(key=lambda r: (order.get(r.alpha_mode, 0), r.texture_id, r.world_index, r.byte_offset))
    return (
        np.asarray(rows, dtype=np.float32),
        np.asarray(indices, dtype=np.uint32),
        ranges,
        world_indices,
    )


def upload_baked_prop_batch(
    items: Sequence[ObjectModelRenderItem],
    cache: ObjectModelCache,
    skin_cache=None,
    tex_cache=None,
    actor_visuals=None,
) -> Optional[BakedPropBatch]:
    """Bake supported object render items and upload them as one GL mesh."""
    from OpenGL import GL  # type: ignore

    verts, indices, ranges, world_indices = build_baked_prop_arrays(
        items,
        cache,
        skin_cache=skin_cache,
        tex_cache=tex_cache,
        actor_visuals=actor_visuals,
    )
    if not ranges or len(indices) == 0:
        return None

    vao = int(GL.glGenVertexArrays(1))
    vbo = int(GL.glGenBuffers(1))
    ibo = int(GL.glGenBuffers(1))

    GL.glBindVertexArray(vao)

    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
    GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts, GL.GL_STATIC_DRAW)

    stride = 8 * 4
    GL.glEnableVertexAttribArray(0)
    GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, stride, None)
    GL.glEnableVertexAttribArray(1)
    GL.glVertexAttribPointer(1, 3, GL.GL_FLOAT, GL.GL_FALSE, stride, GL.ctypes.c_void_p(12))
    GL.glEnableVertexAttribArray(2)
    GL.glVertexAttribPointer(2, 2, GL.GL_FLOAT, GL.GL_FALSE, stride, GL.ctypes.c_void_p(24))

    GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ibo)
    GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, indices.nbytes, indices, GL.GL_STATIC_DRAW)

    GL.glBindVertexArray(0)

    mesh = GpuMesh(
        vao=vao,
        vbo=vbo,
        ibo=ibo,
        index_count=int(len(indices)),
        vertex_count=int(len(verts)),
        triangle_count=int(len(indices) // 3),
        dropped_polys=0,
        category="object",
        model_name="baked_props",
        tex_ranges=[],
        tri_positions=None,
    )
    return BakedPropBatch(mesh=mesh, ranges=ranges, world_indices=world_indices)


def delete_baked_prop_batch(batch: Optional[BakedPropBatch]) -> None:
    if batch is None:
        return
    try:
        delete_mesh(batch.mesh)
    except Exception:
        pass


def draw_baked_prop_batch(
    batch: BakedPropBatch,
    solid_prog,
    light_dir: Tuple[float, float, float],
    fog_enabled: bool = False,
    fog_near: float = 500.0,
    fog_far: float = 3000.0,
    fog_color: Tuple[float, float, float] = (0.055, 0.063, 0.086),
    excluded_world_indices: Optional[Set[int]] = None,
) -> Tuple[int, int, Set[int]]:
    """Draw a baked prop batch, skipping any live-edited object indices."""
    import ctypes
    from OpenGL import GL  # type: ignore

    excluded = set(excluded_world_indices or set())
    drawn_world_indices: Set[int] = set()
    tris = 0

    with solid_prog as prog:
        prog.set_vec3("uLightDir", light_dir)
        prog.set_int("uFogEnabled", 1 if fog_enabled else 0)
        prog.set_float("uFogNear", fog_near)
        prog.set_float("uFogFar", fog_far)
        prog.set_vec3("uFogColor", fog_color)
        prog.set_vec3("uColor", _DEFAULT_COLOR)
        prog.set_float("uAlpha", 1.0)
        prog.set_int("uTex", 0)
        prog.set_int("uUseTexAlpha", 0)

        GL.glBindVertexArray(batch.mesh.vao)
        GL.glActiveTexture(GL.GL_TEXTURE0)

        active_mode = None
        depth_mask_disabled = False
        try:
            for alpha_mode in _ALPHA_ORDER:
                for item in batch.ranges:
                    if item.alpha_mode != alpha_mode or item.world_index in excluded:
                        continue
                    if alpha_mode != active_mode:
                        if depth_mask_disabled:
                            GL.glDepthMask(GL.GL_TRUE)
                            depth_mask_disabled = False
                        prog.set_int("uUseTexAlpha", 1 if alpha_mode != "opaque" else 0)
                        if alpha_mode == "blend":
                            GL.glDepthMask(GL.GL_FALSE)
                            depth_mask_disabled = True
                        active_mode = alpha_mode

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
                    drawn_world_indices.add(item.world_index)
                    tris += item.index_count // 3
        finally:
            if depth_mask_disabled:
                GL.glDepthMask(GL.GL_TRUE)
            prog.set_int("uUseTexAlpha", 0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            GL.glBindVertexArray(0)

    return len(drawn_world_indices), tris, drawn_world_indices
