"""
camera.py
=========

Orbit and fly camera for the 3-D viewer.

Coordinate system: Y-up, right-handed — matches both MM9 world space and
the OpenGL default.  All matrices are 4×4 numpy float32 arrays in
row-major order.  Pass them to glUniformMatrix4fv with transpose=GL_TRUE,
or transpose with .T first.
"""

from __future__ import annotations

import math
from typing import Tuple

import numpy as np

# Fit close enough for editing rather than fully zoomed out for screenshots.
_FIT_DISTANCE_SCALE = 0.42
_FIT_HEIGHT_SCALE = 0.14


# ---------------------------------------------------------------------------
# Internal matrix helpers
# ---------------------------------------------------------------------------

def _look_at(
    eye:    np.ndarray,
    target: np.ndarray,
    up:     np.ndarray,
) -> np.ndarray:
    """Standard lookAt view matrix (row-major)."""
    f = target - eye
    fn = np.linalg.norm(f)
    if fn < 1e-10:
        f = np.array([0.0, 0.0, -1.0], dtype=np.float32)
    else:
        f = (f / fn).astype(np.float32)

    r = np.cross(f, up).astype(np.float32)
    rn = np.linalg.norm(r)
    if rn < 1e-10:
        r = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    else:
        r /= rn

    u = np.cross(r, f).astype(np.float32)

    m = np.eye(4, dtype=np.float32)
    m[0, :3] = r
    m[1, :3] = u
    m[2, :3] = -f
    m[0, 3]  = -float(np.dot(r, eye))
    m[1, 3]  = -float(np.dot(u, eye))
    m[2, 3]  =  float(np.dot(f, eye))
    return m


def _perspective(
    fov_deg: float,
    aspect:  float,
    near:    float,
    far:     float,
) -> np.ndarray:
    """Standard perspective projection matrix (row-major, OpenGL clip space)."""
    f = 1.0 / math.tan(math.radians(fov_deg) * 0.5)
    n, fa = near, far
    m = np.zeros((4, 4), dtype=np.float32)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (fa + n) / (n - fa)
    m[2, 3] = (2.0 * fa * n) / (n - fa)
    m[3, 2] = -1.0
    return m


def _rotate_around(
    v:         np.ndarray,
    axis:      np.ndarray,
    angle_rad: float,
) -> np.ndarray:
    """Rodrigues' rotation: rotate *v* around *axis* by *angle_rad*."""
    c    = math.cos(angle_rad)
    s    = math.sin(angle_rad)
    axis = axis / (np.linalg.norm(axis) + 1e-15)
    return (v * c
            + np.cross(axis, v) * s
            + axis * float(np.dot(axis, v)) * (1.0 - c)).astype(np.float32)


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

class Camera:
    """
    Dual-mode camera: 'orbit' revolves around a fixed target point (good
    for inspecting a room), 'fly' moves freely with WASD + mouse-look
    (good for navigating multi-floor levels).

    Usage
    -----
        cam = Camera()
        cam.fit_to_bounds(bsp_min, bsp_max)

        # each frame:
        mvp = cam.proj_matrix(aspect) @ cam.view_matrix()
        # pass mvp (4×4 float32) to the shader
    """

    def __init__(self) -> None:
        self.eye    = np.array([0.0, 500.0, 2000.0], dtype=np.float32)
        self.target = np.array([0.0,   0.0,    0.0], dtype=np.float32)
        self._world_up = np.array([0.0, 1.0, 0.0], dtype=np.float32)

        self.fov  = 60.0        # degrees
        self.near = 10.0
        self.far  = 200_000.0

        self.mode = "orbit"     # "orbit" | "fly"

        # Fly-mode state — stored as yaw/pitch to avoid gimbal drift
        self._yaw        = 0.0   # radians, rotation around world Y
        self._pitch      = 0.0   # radians, above/below horizon  (±π/2)
        self.fly_speed   = 500.0 # world units per second

    # ------------------------------------------------------------------
    # Matrices
    # ------------------------------------------------------------------

    def view_matrix(self) -> np.ndarray:
        """Return the 4×4 view matrix (row-major float32)."""
        return _look_at(self.eye, self.target, self._world_up)

    def proj_matrix(self, aspect: float) -> np.ndarray:
        """Return the 4×4 perspective projection matrix (row-major float32)."""
        return _perspective(self.fov, max(aspect, 0.01), self.near, self.far)

    def mvp(self, aspect: float) -> np.ndarray:
        """Projection × View (use as MVP when Model == identity)."""
        return self.proj_matrix(aspect) @ self.view_matrix()

    # ------------------------------------------------------------------
    # Orbit controls  (active in 'orbit' mode)
    # ------------------------------------------------------------------

    def orbit(self, dx: float, dy: float) -> None:
        """Rotate eye around target. dx/dy are screen-pixel deltas."""
        sensitivity = 0.005  # radians per pixel
        arm = self.eye - self.target

        # Yaw around world Y
        arm = _rotate_around(arm, self._world_up, -dx * sensitivity)

        # Pitch around camera right axis; clamp so we never flip over the pole
        right = np.cross(arm / (np.linalg.norm(arm) + 1e-15), self._world_up)
        rn = np.linalg.norm(right)
        if rn > 1e-10:
            new_arm = _rotate_around(arm, right / rn, -dy * sensitivity)
            # Accept pitch only if we stay north-up
            test_up = np.cross(right / rn,
                               -new_arm / (np.linalg.norm(new_arm) + 1e-15))
            if test_up[1] > 0.05:
                arm = new_arm

        self.eye = (self.target + arm).astype(np.float32)

    def pan(self, dx: float, dy: float) -> None:
        """Translate both eye and target in the screen-space XY plane."""
        arm  = self.eye - self.target
        dist = float(np.linalg.norm(arm))
        sensitivity = dist * 0.0012

        fwd   = arm / (dist + 1e-15)
        right = np.cross(-fwd, self._world_up)
        right /= np.linalg.norm(right) + 1e-15
        up    = np.cross(right, -fwd)

        delta = (right * (-dx) + up * dy).astype(np.float32) * sensitivity
        self.eye    += delta
        self.target += delta

    def zoom(self, factor: float) -> None:
        """Move eye along the view axis. factor > 1 zooms in."""
        arm  = self.eye - self.target
        dist = float(np.linalg.norm(arm))
        new_dist = max(dist / max(factor, 1e-6), self.near * 4.0)
        self.eye = (self.target + arm / (dist + 1e-15) * new_dist).astype(np.float32)

    # ------------------------------------------------------------------
    # Fly controls  (active in 'fly' mode)
    # ------------------------------------------------------------------

    def fly_move(self, fwd: float, right: float, up: float, dt: float) -> None:
        """
        Move the camera. fwd/right/up are axis values in [-1, 1].
        dt is elapsed seconds since the last call.
        """
        cy, sy = math.cos(self._yaw),   math.sin(self._yaw)
        cp, sp = math.cos(self._pitch), math.sin(self._pitch)
        forward_dir = np.array([ sy * cp,  sp, -cy * cp], dtype=np.float32)
        right_dir   = np.array([      cy, 0.0,       sy], dtype=np.float32)

        step  = self.fly_speed * dt
        delta = (forward_dir * fwd
                 + right_dir * right
                 + self._world_up * up).astype(np.float32) * step
        self.eye    = (self.eye + delta).astype(np.float32)
        self.target = (self.eye + forward_dir).astype(np.float32)

    def fly_rotate(self, dx: float, dy: float) -> None:
        """Adjust yaw and pitch from mouse-look pixel deltas."""
        sensitivity = 0.003
        self._yaw   -= dx * sensitivity
        self._pitch  = float(np.clip(
            self._pitch - dy * sensitivity,
            -math.pi * 0.45,
             math.pi * 0.45,
        ))
        cy, sy = math.cos(self._yaw),   math.sin(self._yaw)
        cp, sp = math.cos(self._pitch), math.sin(self._pitch)
        forward = np.array([sy * cp, sp, -cy * cp], dtype=np.float32)
        self.target = (self.eye + forward).astype(np.float32)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def fit_to_bounds(
        self,
        min_xyz: Tuple[float, float, float],
        max_xyz: Tuple[float, float, float],
    ) -> None:
        """
        Position and orient the camera so the bounding box (min_xyz, max_xyz)
        is fully visible with a small margin.  Works in both modes.
        """
        lo     = np.array(min_xyz, dtype=np.float32)
        hi     = np.array(max_xyz, dtype=np.float32)
        centre = (lo + hi) * 0.5
        size   = float(np.linalg.norm(hi - lo))

        dist = (_FIT_DISTANCE_SCALE * size) / math.tan(math.radians(self.fov * 0.5))
        dist = max(dist, self.near * 10.0)

        self.target = centre.astype(np.float32)
        self.eye    = (centre + np.array(
            [0.0, size * _FIT_HEIGHT_SCALE, dist],
            dtype=np.float32)).astype(np.float32)

        # Keep near/far sensible for this level's scale
        self.near = max(dist * 0.001, 1.0)
        self.far  = max(dist * 10.0, size * 4.0)

        if self.mode == "fly":
            self._yaw   = 0.0
            self._pitch = math.atan2(-size * 0.2, dist) * 0.5
            cy, sy = math.cos(self._yaw),   math.sin(self._yaw)
            cp, sp = math.cos(self._pitch), math.sin(self._pitch)
            forward = np.array([sy * cp, sp, -cy * cp], dtype=np.float32)
            self.target = (self.eye + forward).astype(np.float32)

    def unproject(
        self,
        sx:         float,
        sy:         float,
        viewport_w: float,
        viewport_h: float,
        aspect:     float,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Unproject a screen pixel (sx, sy) into a world-space ray.

        The GLSL vertex shader applies  ``clip = mvp @ world``  (we upload
        with GL_TRUE so the numpy row-major matrix acts as a standard
        left-multiply).  Inverting that gives
        ``world = inv(mvp) @ clip_homogeneous``, then perspective-divide.

        Parameters
        ----------
        sx, sy      : pixel coordinates (origin top-left, Y increases downward)
        viewport_w,
        viewport_h  : canvas size in pixels
        aspect      : viewport_w / viewport_h  (same value passed to mvp())

        Returns
        -------
        ray_origin : (3,) float32 — world-space ray origin (≈ camera eye)
        ray_dir    : (3,) float32 — normalised world-space ray direction
        """
        mvp     = self.mvp(aspect)
        inv_mvp = np.linalg.inv(mvp.astype(np.float64))

        # Screen → NDC.  Y is flipped: screen top → NDC +1.
        ndc_x = 2.0 * sx / viewport_w - 1.0
        ndc_y = 1.0 - 2.0 * sy / viewport_h

        # Unproject near plane (ndc_z = −1) and far plane (ndc_z = +1).
        # Using homogeneous w=1; perspective-divide after multiplication
        # cancels out any actual clip-space w, so the choice of w here
        # does not affect the final world position.
        near_h = inv_mvp @ np.array([ndc_x, ndc_y, -1.0, 1.0])
        far_h  = inv_mvp @ np.array([ndc_x, ndc_y,  1.0, 1.0])

        near_w = (near_h[:3] / near_h[3]).astype(np.float32)
        far_w  = (far_h[:3]  / far_h[3]).astype(np.float32)

        direction = far_w - near_w
        length    = float(np.linalg.norm(direction))
        if length < 1e-15:
            direction = np.array([0.0, 0.0, -1.0], dtype=np.float32)
        else:
            direction = (direction / length).astype(np.float32)

        return near_w, direction

    def set_mode(self, mode: str) -> None:
        """
        Switch between 'orbit' and 'fly'.  When switching to fly, the
        yaw/pitch are derived from the current eye→target direction so
        the view does not jump.
        """
        if mode not in ("orbit", "fly") or mode == self.mode:
            return
        self.mode = mode
        if mode == "fly":
            d = self.target - self.eye
            d = d / (np.linalg.norm(d) + 1e-15)
            self._pitch = float(math.asin(float(np.clip(d[1], -1.0, 1.0))))
            self._yaw   = float(math.atan2(float(d[0]), -float(d[2])))
