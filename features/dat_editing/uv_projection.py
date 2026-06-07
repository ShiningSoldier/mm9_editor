"""LithTech texture projection helpers used by DAT geometry import."""

from __future__ import annotations

import math
from typing import Optional, Sequence, Tuple


Vec3 = Tuple[float, float, float]
Vec2 = Tuple[float, float]


def dedit_uv_to_opq(
    positions: Sequence[Vec3],
    uvs: Sequence[Vec2],
    *,
    tex_width: float = 128.0,
    tex_height: float = 128.0,
) -> Optional[Tuple[Vec3, Vec3, Vec3]]:
    """Port DEdit's ``ConvertUVToOPQ`` routine for the first triangle.

    OBJ/glTF UVs are normalized in the editor import path.  DEdit's historical
    OBJ importer used the same normalized UV space and then scaled the OPQ
    vectors by the texture dimensions; keep 128x128 as the current fallback when
    real DTX dimensions are unavailable.
    """
    if len(positions) < 3 or len(uvs) < 3:
        return None
    width = float(tex_width) if tex_width and tex_width > 0.0 else 128.0
    height = float(tex_height) if tex_height and tex_height > 0.0 else 128.0
    if width <= 0.0 or height <= 0.0:
        return None

    tv0 = (float(uvs[0][0]), -float(uvs[0][1]), 0.0)
    tv1 = (float(uvs[1][0]), -float(uvs[1][1]), 0.0)
    tv2 = (float(uvs[2][0]), -float(uvs[2][1]), 0.0)

    bc_o = _bary_coords(tv0, tv1, tv2, (0.0, 0.0, 0.0))
    bc_p = _bary_coords(tv0, tv1, tv2, (1.0, 0.0, 0.0))
    bc_q = _bary_coords(tv0, tv1, tv2, (0.0, 1.0, 0.0))
    if bc_o is None or bc_p is None or bc_q is None:
        return None

    v0, v1, v2 = positions[:3]
    o = _weighted_sum(bc_o, v0, v1, v2)
    p = _sub(_weighted_sum(bc_p, v0, v1, v2), o)
    q = _sub(_weighted_sum(bc_q, v0, v1, v2), o)

    p_len = _length(p)
    q_len = _length(q)
    if p_len <= 1.0e-6 or q_len <= 1.0e-6:
        return None
    tp = 1.0 / (p_len / width)
    tq = 1.0 / (q_len / height)
    p = _scale(p, 1.0 / p_len)
    q = _scale(q, 1.0 / q_len)

    r = _cross(q, p)
    p_new = _cross(r, q)
    q_new = _cross(p, r)
    p_new_len = _length(p_new)
    q_new_len = _length(q_new)
    if p_new_len <= 1.0e-6 or q_new_len <= 1.0e-6:
        return None
    p_new = _scale(p_new, 1.0 / p_new_len)
    q_new = _scale(q_new, 1.0 / q_new_len)

    p_dot = _dot(p, p_new)
    q_dot = _dot(q, q_new)
    if abs(p_dot) <= 1.0e-6 or abs(q_dot) <= 1.0e-6:
        return None
    pscale = 1.0 / p_dot
    qscale = 1.0 / q_dot

    r = _cross(q_new, p_new)
    p_new = _scale(p_new, tp * pscale)
    q_new = _scale(q_new, tq * qscale)

    r_len = _length(r)
    if r_len <= 1.0e-6:
        return None
    r = _scale(r, 1.0 / r_len)
    p = _add(p_new, r)
    q = _sub(q_new, _scale(r, _dot(p_new, q_new)))
    return o, p, q


def _bary_coords(p0: Vec3, p1: Vec3, p2: Vec3, p: Vec3) -> Optional[Vec3]:
    n = _bary_area(p0, p1, p2)
    if abs(n) < 0.001:
        return None
    u = _bary_area(p1, p2, p) / n
    v = _bary_area(p2, p0, p) / n
    w = 1.0 - u - v
    return u, v, w


def _bary_area(p0: Vec3, p1: Vec3, p2: Vec3) -> float:
    e0 = _sub(p1, p0)
    e1 = _sub(p2, p0)
    return e0[0] * e1[1] - e1[0] * e0[1]


def _weighted_sum(weights: Vec3, v0: Vec3, v1: Vec3, v2: Vec3) -> Vec3:
    return (
        weights[0] * v0[0] + weights[1] * v1[0] + weights[2] * v2[0],
        weights[0] * v0[1] + weights[1] * v1[1] + weights[2] * v2[1],
        weights[0] * v0[2] + weights[1] * v1[2] + weights[2] * v2[2],
    )


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a: Vec3, factor: float) -> Vec3:
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(value: Vec3) -> float:
    return math.sqrt(_dot(value, value))
