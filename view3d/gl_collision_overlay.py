"""
gl_collision_overlay.py
=======================

OpenGL overlay rendering for MM9 collision sidecars.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from view3d.collision_sidecar import CollisionSidecar
from view3d.gl_mesh import GpuMesh, delete_mesh


ROLE_COLORS: Dict[str, Tuple[float, float, float]] = {
    "static": (0.70, 0.70, 0.78),
    "floor": (0.10, 0.95, 0.35),
    "wall": (1.00, 0.28, 0.25),
    "blockingHelper": (1.00, 0.20, 0.85),
    "dynamicDoor": (1.00, 0.68, 0.12),
    "triggerOnly": (0.15, 0.55, 1.00),
    "water": (0.00, 0.80, 1.00),
    "unknown": (0.85, 0.85, 0.85),
}

DEFAULT_COLLISION_ROLES: Set[str] = {
    "floor",
    "wall",
    "blockingHelper",
    "dynamicDoor",
    "water",
}


@dataclass
class CollisionOverlayRange:
    role: str
    byte_offset: int
    index_count: int


@dataclass
class CollisionOverlayBatch:
    mesh: GpuMesh
    ranges: List[CollisionOverlayRange] = field(default_factory=list)


def build_collision_overlay_arrays(
    sidecar: CollisionSidecar,
    roles: Optional[Set[str]] = None,
) -> Tuple[np.ndarray, np.ndarray, List[CollisionOverlayRange]]:
    """Build display-space flat-shaded overlay arrays from a collision sidecar."""
    allowed = set(roles or DEFAULT_COLLISION_ROLES)
    rows: List[List[float]] = []
    indices: List[int] = []
    tri_roles: List[str] = []

    source_vertices = np.asarray(sidecar.vertices, dtype=np.float32)
    source_indices = np.asarray(sidecar.indices, dtype=np.uint32).reshape((-1, 3))

    for tri_index, meta in enumerate(sidecar.triangles):
        role = meta.role
        if role not in allowed:
            continue
        idx = source_indices[tri_index]
        pts = source_vertices[idx].astype(np.float32).copy()
        pts[:, 0] *= -1.0
        normal = np.cross(pts[1] - pts[0], pts[2] - pts[0])
        length = float(np.linalg.norm(normal))
        if length <= 1.0e-8:
            continue
        normal = (normal / length).astype(np.float32)
        for point in pts:
            rows.append([
                float(point[0]), float(point[1]), float(point[2]),
                float(normal[0]), float(normal[1]), float(normal[2]),
                0.0, 0.0,
            ])
            indices.append(len(indices))
        tri_roles.append(role)

    if not rows:
        return np.zeros((0, 8), dtype=np.float32), np.zeros(0, dtype=np.uint32), []

    order = np.argsort(np.asarray(tri_roles, dtype=object), kind="stable")
    indices_array = np.asarray(indices, dtype=np.uint32).reshape((-1, 3))[order].ravel()
    roles_array = np.asarray(tri_roles, dtype=object)[order]

    ranges: List[CollisionOverlayRange] = []
    i = 0
    while i < len(roles_array):
        role = str(roles_array[i])
        j = i + 1
        while j < len(roles_array) and roles_array[j] == role:
            j += 1
        ranges.append(CollisionOverlayRange(
            role=role,
            byte_offset=i * 3 * 4,
            index_count=(j - i) * 3,
        ))
        i = j

    return np.asarray(rows, dtype=np.float32), indices_array, ranges


def upload_collision_overlay(
    sidecar: CollisionSidecar,
    roles: Optional[Set[str]] = None,
) -> Optional[CollisionOverlayBatch]:
    """Upload a sidecar collision overlay to the current GL context."""
    from OpenGL import GL  # type: ignore

    verts, indices, ranges = build_collision_overlay_arrays(sidecar, roles=roles)
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
        category="collision",
        model_name="collision_sidecar",
        tex_ranges=[],
        tri_positions=None,
    )
    return CollisionOverlayBatch(mesh=mesh, ranges=ranges)


def delete_collision_overlay(batch: Optional[CollisionOverlayBatch]) -> None:
    if batch is None:
        return
    try:
        delete_mesh(batch.mesh)
    except Exception:
        pass


def draw_collision_overlay(
    batch: CollisionOverlayBatch,
    solid_prog,
    mvp: np.ndarray,
    alpha: float = 0.34,
    roles: Optional[Set[str]] = None,
) -> Tuple[int, int]:
    """Draw a translucent collision overlay."""
    import ctypes
    from OpenGL import GL  # type: ignore

    allowed = set(roles or DEFAULT_COLLISION_ROLES)
    ranges_drawn = 0
    tris_drawn = 0

    with solid_prog as prog:
        prog.set_mat4("uMVP", mvp)
        prog.set_vec3("uLightDir", (0.4, 0.9, 0.3))
        prog.set_int("uFogEnabled", 0)
        prog.set_int("uTex", 0)
        prog.set_int("uHasTex", 0)
        prog.set_int("uUseTexAlpha", 0)
        prog.set_float("uAlpha", float(alpha))

        GL.glDepthMask(GL.GL_FALSE)
        GL.glBindVertexArray(batch.mesh.vao)
        try:
            for item in batch.ranges:
                if item.role not in allowed:
                    continue
                prog.set_vec3("uColor", ROLE_COLORS.get(item.role, ROLE_COLORS["unknown"]))
                GL.glDrawElements(
                    GL.GL_TRIANGLES,
                    item.index_count,
                    GL.GL_UNSIGNED_INT,
                    ctypes.c_void_p(item.byte_offset),
                )
                ranges_drawn += 1
                tris_drawn += item.index_count // 3
        finally:
            GL.glBindVertexArray(0)
            GL.glDepthMask(GL.GL_TRUE)
            prog.set_float("uAlpha", 1.0)

    return ranges_drawn, tris_drawn
