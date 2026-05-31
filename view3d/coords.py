"""
Viewport-only coordinate conversion helpers.

MM9 DAT coordinates are kept untouched for editing and saving.  The OpenGL
preview uses a reflected display space so the level layout matches the game
instead of appearing as a left/right mirror.
"""

from __future__ import annotations

from typing import Iterable, Tuple

import numpy as np


def game_to_display_point(xyz: Iterable[float]) -> np.ndarray:
    """Convert one MM9 game-space point to viewport display space."""
    x, y, z = xyz
    return np.array([-float(x), float(y), float(z)], dtype=np.float32)


def display_to_game_point(xyz: Iterable[float]) -> np.ndarray:
    """Convert one viewport display-space point back to MM9 game space."""
    x, y, z = xyz
    return np.array([-float(x), float(y), float(z)], dtype=np.float32)


def game_to_display_bounds(
    min_xyz: Tuple[float, float, float],
    max_xyz: Tuple[float, float, float],
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """Convert an axis-aligned game-space bounds pair to display space."""
    lo = game_to_display_point(min_xyz)
    hi = game_to_display_point(max_xyz)
    out_min = np.minimum(lo, hi)
    out_max = np.maximum(lo, hi)
    return (
        (float(out_min[0]), float(out_min[1]), float(out_min[2])),
        (float(out_max[0]), float(out_max[1]), float(out_max[2])),
    )


def game_to_display_matrix() -> np.ndarray:
    """Return a 4x4 matrix that reflects game space into display space."""
    m = np.eye(4, dtype=np.float32)
    m[0, 0] = -1.0
    return m
