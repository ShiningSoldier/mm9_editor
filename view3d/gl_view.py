"""
gl_view.py
==========

View3D — a Tkinter widget that renders and edits the active MM9 level in
3-D using PyOpenGL.

If PyOpenGL or pyopengltk is not installed the widget degrades gracefully
to a placeholder label that shows the install command.

Public API
----------
  set_active_level(level_edit)
  refresh()
  flush_pending_transforms()
  select_by_index(world_index)
  set_place_mode(on)           — enable/disable 3-D place mode

Additional 3-D controls
-----------------------
  set_camera_mode(mode)        — "orbit" | "fly"

Mouse / keyboard in orbit mode
  Left-drag    — orbit around target; if pointer is over a sprite,
                 initiates a 3-D drag-to-move instead
  Alt+drag     — pan
  Middle-drag  — pan
  Scroll       — zoom
  Arrow keys   — nudge selected object on X/Z plane
  PgUp/PgDn    — nudge selected object vertically
  Q / E        — nudge selected object down / up
  F            — fit camera to level bounds
  Right-click  — cancel place mode

Mouse / keyboard in place mode  (set_place_mode(True))
  Left-click   — cast ray into BSP; fire on_place(wx, wz) at hit point
  Right-click  — cancel place mode

Mouse / keyboard in fly mode
  Left-drag    — look (yaw/pitch)
  W/A/S/D      — move forward/left/back/right
  Q / E        — move down / up
  Scroll       — dolly forward / back along the viewing direction
  Shift        — 5× speed
  F            — reset to fit-to-bounds position

Camera-mode toolbar
  [Orbit] [Fly]  toggle buttons in the top-left of the widget
  Status bar     at the bottom: mode | eye XYZ | object count
"""

from __future__ import annotations

import math
import os
import sys
import time
from typing import Any, Callable, Optional

try:
    import tkinter as tk
    from tkinter import ttk
    _HAS_TK = True
except ImportError:
    tk = None        # type: ignore[assignment]
    ttk = None       # type: ignore[assignment]
    _HAS_TK = False

# ---------------------------------------------------------------------------
# Dependency detection
# ---------------------------------------------------------------------------

_MISSING: list = []

try:
    import OpenGL          # type: ignore   # noqa: F401
    from OpenGL import GL  # type: ignore
except ImportError:
    _MISSING.append("PyOpenGL")

try:
    import pyopengltk      # type: ignore   # noqa: F401
except ImportError:
    _MISSING.append("pyopengltk")

try:
    import numpy as np
except ImportError:
    _MISSING.append("numpy")

OPENGL_AVAILABLE: bool = len(_MISSING) == 0

_INSTALL_HINT = (
    "3-D view requires additional packages.\n\n"
    "Install with:\n"
    "    pip install PyOpenGL PyOpenGL_accelerate pyopengltk numpy\n\n"
    "Then restart the editor."
)

# ---------------------------------------------------------------------------
# Lazy imports — only resolved when GL is available
# ---------------------------------------------------------------------------

if OPENGL_AVAILABLE:
    import numpy as np
    from pyopengltk import OpenGLFrame          # type: ignore

    # pyopengltk's Windows pixel descriptor defaults to zero stencil bits.
    # Sky portals use stencil masking, so request it before tkMap creates the
    # native GL context. The queried GL_STENCIL_BITS below remains authoritative
    # in case a driver cannot provide the requested format.
    if sys.platform.startswith("win32"):
        try:
            import pyopengltk.win32 as _pyopengltk_win32  # type: ignore
            _pyopengltk_win32.pfd.cStencilBits = 8
        except Exception:
            pass

    from view3d.camera    import Camera
    from view3d.coords    import (display_to_game_point, game_to_display_bounds,
                                  game_to_display_point)
    from view3d.gl_shader import ShaderProgram
    from view3d.gl_shader import (SOLID_VERT, SOLID_FRAG,
                                  SKY_PORTAL_VERT, SKY_PORTAL_FRAG)
    from view3d.gl_shader import BILLBOARD_VERT, BILLBOARD_GEOM, BILLBOARD_FRAG
    from view3d.gl_mesh   import (MeshCache, draw_mesh, raycast_mesh_array,
                                  build_bsp_draw_batch, draw_bsp_batch,
                                  DEFAULT_HELPER_ROLE_GROUPS,
                                  normal_render_world_bounds,
                                  build_sky_draw_batch, draw_sky_batch)
    from view3d.gl_objects import (upload_objects, draw_sprites,
                                   decode_pick_color, delete_sprites,
                                   ObjectSprites,
                                   hidden_world_helper_model_names,
                                   should_draw_billboard_for_modeled_object)
    from view3d.gl_object_models import (ObjectModelCache, build_render_items,
                                         draw_object_model_items)
    from view3d.sky import (build_soft_sky_model, resolve_sky_scene,
                            resolve_soft_sky_texture)
    import _path_setup     # type: ignore  # noqa: F401
    from catalog import categorize

# Light direction (world space): above and slightly to the side
_LIGHT_DIR = (0.4, 0.9, 0.3)

# Fog colour — must match the GL clear colour set in initgl()
_FOG_COLOR = (0.055, 0.063, 0.086)

# Diameter of each object sprite in world units.
# ~40 units is clearly legible at normal viewing distances in MM9 levels
# without dominating the geometry.
_SPRITE_WORLD_SIZE = 40.0

# Screen-space radius (pixels) used for the fast CPU sprite hit test.
# Larger than the visual radius so the user doesn't have to pixel-hunt.
_SPRITE_HIT_RADIUS_PX = 20

# Keyboard object nudges in world units. Hold Shift for a larger step.
_NUDGE_XZ_STEP = 25.0
_NUDGE_Y_STEP = 25.0


def _new_load_profile(level):
    if os.environ.get("MM9_EDITOR_PROFILE_LOAD") != "1":
        return None
    label = str(
        getattr(level, "display_name", "")
        or getattr(level, "rez_vpath", "")
        or getattr(level, "path", "")
        or "(no level)"
    )
    return {
        "label": label,
        "started": time.perf_counter(),
        "stages": [],
    }


def _mark_load_stage(profile, name: str, started: float) -> None:
    if profile is not None:
        profile["stages"].append((name, time.perf_counter() - started))


def _emit_load_profile(profile) -> None:
    if profile is None:
        return
    total = time.perf_counter() - float(profile["started"])
    parts = [f"total={total:.3f}s"]
    parts.extend(
        f"{name}={duration:.3f}s"
        for name, duration in profile["stages"]
    )
    print(
        f"[view3d load] {profile['label']}  " + "  ".join(parts),
        file=sys.stderr,
    )


_NUDGE_FAST_MULT = 5.0
_ROTATE_YAW_STEP_DEG = 15.0
_ROTATE_FAST_MULT = 3.0

# Fly movement uses real timer deltas, capped so a stalled event loop cannot
# teleport the camera on its next callback. One wheel notch dollies by this
# fraction of a second at the configured fly speed.
_MAX_FLY_TICK_SECONDS = 0.1
_FLY_WHEEL_SECONDS = 0.25
_VIEWPORT_KEYSYMS = frozenset({
    "w", "a", "s", "d", "q", "e", "f",
    "shift_l", "shift_r",
    "up", "down", "left", "right",
    "prior", "next", "page_up", "page_down",
    "bracketleft", "bracketright", "braceleft", "braceright",
})


def _fly_elapsed_seconds(previous: Optional[float], current: float) -> float:
    """Return a non-negative, stall-safe fly-camera timer delta."""
    if previous is None:
        return 0.0
    return min(max(float(current) - float(previous), 0.0), _MAX_FLY_TICK_SECONDS)


def _should_accept_viewport_key(
    keysym: str,
    *,
    direct_to_canvas: bool,
    focus_known: bool,
    focus_in_viewport: bool,
    pointer_over_canvas: bool,
) -> bool:
    """Pure policy for the guarded application-wide keyboard fallback."""
    if str(keysym or "").lower() not in _VIEWPORT_KEYSYMS:
        return False
    if direct_to_canvas:
        return True
    if focus_known:
        return bool(focus_in_viewport)
    return bool(pointer_over_canvas)

# ---------------------------------------------------------------------------
# Placeholder widget (GL not available)
# ---------------------------------------------------------------------------

class _PlaceholderView(tk.Frame if _HAS_TK else object):
    """Shown when PyOpenGL / pyopengltk are not installed."""

    def __init__(self, parent: "tk.Misc", missing: list) -> None:
        if _HAS_TK:
            super().__init__(parent, bg="#0e1116")
        msg = _INSTALL_HINT if missing else "OpenGL initialisation failed."
        if _HAS_TK:
            tk.Label(
                self,
                text=msg,
                bg="#0e1116", fg="#888888",
                font=("Consolas", 10),
                justify="left",
                wraplength=420,
                padx=32, pady=32,
            ).pack(expand=True)

    # Stub public API so callers never need to branch on OPENGL_AVAILABLE
    def set_active_level(self, level): pass
    def refresh(self):                 pass
    def select_by_index(self, idx):    pass
    def set_place_mode(self, on):      pass
    def set_camera_mode(self, mode):   pass
    def set_show_helper_billboards(self, enabled): pass
    def set_show_object_helper_billboards(self, enabled): pass
    def set_show_world_helper_billboards(self, enabled): pass
    def set_helper_bsp_mode(self, mode): pass
    def set_helper_role_groups(self, groups): pass


# ---------------------------------------------------------------------------
# GL canvas (only defined when dependencies are present)
# ---------------------------------------------------------------------------

if OPENGL_AVAILABLE:

    class _GLCanvas(OpenGLFrame):
        """
        Raw OpenGL canvas.  All rendering logic lives here; View3D wraps it
        with a toolbar and status bar.

        Callbacks set by View3D
        -----------------------
          _on_select_cb(world_index)   — object clicked in 3-D view
          _on_status_cb(text)          — update status bar text after render
        """

        def __init__(self, parent, **kwargs):
            super().__init__(parent, **kwargs)
            self._ready        = False
            self._camera       = Camera()
            self._mesh_cache   = MeshCache()
            self._bsp_draw_batch = None
            self._sky_scene = None
            self._sky_draw_batch = None
            self._soft_sky_model = None
            self._sprites: Optional[ObjectSprites] = None
            self._solid_prog:     Optional[ShaderProgram] = None
            self._sky_portal_prog: Optional[ShaderProgram] = None
            self._billboard_prog: Optional[ShaderProgram] = None
            self._stencil_bits: int = 0

            # TextureCache — created in initgl() once GL is live.
            # Set _textures_dir/_models_dir before initgl fires (View3D does this).
            self._textures_dir: Optional[str] = None
            self._tex_cache    = None   # dtx.TextureCache or None
            self._skins_dir: Optional[str] = None
            self._skin_cache    = None   # dtx.TextureCache or None
            self._models_dir: Optional[str] = None
            self._obj_model_cache = None   # gl_object_models.ObjectModelCache or None
            self._actor_visuals: dict = {}
            self._world_helper_metadata: dict = {}

            self._level        = None   # LevelEdit or None
            self._bsp_world    = None   # bsp.BspWorld or None
            self._objects:     list = []
            self._obj_count    = 0
            self._object_model_items: list = []

            self._selected_index = -1

            # Fog shader support remains in the renderer, but the editor keeps
            # it disabled: the old toggle was barely visible and reduced
            # placement clarity more than it helped.
            self._fog_enabled: bool = False
            self._show_object_helper_billboards: bool = False
            self._show_world_helper_billboards: bool = False
            self._helper_bsp_mode: str = "normal"
            self._helper_role_groups = set(DEFAULT_HELPER_ROLE_GROUPS)

            # Deferred level load: when set_active_level() is called before
            # initgl() has fired (widget not yet shown), we queue the args
            # here.  initgl() drains this once GL is live.
            self._pending_level_args: Optional[tuple] = None

            # Mouse drag state
            self._last_mx   = 0
            self._last_my   = 0
            self._drag_mode: Optional[str] = None  # "orbit"|"pan"|"fly_look"
            self._drag_start_mx = 0
            self._drag_start_my = 0

            # Redraws can arrive faster than the GL/Tk pipeline can consume
            # them while dragging.  Coalesce requests to one scheduled frame.
            self._render_after: Optional[str] = None
            self._last_render_time: float = 0.0
            self._render_min_interval_ms: int = 16

            # Lightweight developer profiler. It is intentionally available
            # only through the debug environment setting, not a user shortcut.
            self._profile_enabled: bool = os.environ.get("MM9_EDITOR_PROFILE") == "1"
            self._profile_accum: dict = {}
            self._profile_frames: int = 0

            # Fly-mode key state
            self._fly_keys:  set = set()
            self._fly_after: Optional[str] = None
            self._fly_last_tick: Optional[float] = None

            # Callbacks wired by View3D
            self._on_select_cb: Optional[Callable[[int], None]] = None
            self._on_status_cb: Optional[Callable[[str], None]] = None
            # (wx: float, wz: float) — fallback place callback.
            self._on_place_cb:  Optional[Callable[[float, float], None]] = None
            # (wx: float, wy: float, wz: float) — exact 3-D BSP hit callback.
            self._on_place_xyz_cb: Optional[Callable[[float, float, float], None]] = None
            # (world_index: int, wx: float, wz: float) — fallback move callback
            self._on_move_cb:   Optional[Callable[[int, float, float], None]] = None
            # (world_index: int, wx: float, wy: float, wz: float) — exact 3-D move
            self._on_move_xyz_cb: Optional[
                Callable[[int, float, float, float], None]
            ] = None
            # (world_index: int, rotation_tuple) — fired by yaw rotation keys.
            self._on_rotate_cb: Optional[Callable[[int, tuple], None]] = None
            # Debounced transform commits.  The view previews transforms
            # immediately, then commits the latest value once input settles.
            self._transform_commit_after: Optional[str] = None
            self._pending_move_xyz: Optional[tuple[int, float, float, float]] = None
            self._pending_move_xz: Optional[tuple[int, float, float]] = None
            self._pending_elevation: Optional[tuple[int, float]] = None
            self._pending_rotation: Optional[tuple[int, tuple]] = None

            # Place mode
            self._place_mode: bool = False

            # 3-D sprite drag state (Stage 2)
            self._3d_drag_index:   int   = -1    # world_index being dragged; -1 = none
            self._3d_drag_plane_y: float = 0.0   # Y of the XZ drag plane
            self._3d_drag_wx:      float = 0.0   # current X while dragging
            self._3d_drag_wz:      float = 0.0   # current Z while dragging
            self._3d_drag_orig_xyz: Optional[np.ndarray] = None
            self._3d_drag_moved:   bool  = False

            # Y-elevation fallback for callers without exact XYZ movement.
            self._on_elevate_cb: Optional[Callable[[int, float], None]] = None
            # Pending VBO patches: vbo_index -> new (x, y, z) float32 array.
            # Applied in _render() via glBufferSubData before drawing sprites.
            self._sprite_position_pending: dict = {}   # Dict[int, np.ndarray]

            self._bind_events()

        # ------------------------------------------------------------------
        # Event binding
        # ------------------------------------------------------------------

        def _bind_events(self) -> None:
            try:
                self.configure(takefocus=1)
            except tk.TclError:
                pass
            self.bind("<Configure>",       self._on_resize)
            self.bind("<Enter>",           self._on_enter)
            self.bind("<ButtonPress-1>",   self._on_lmb_down)
            self.bind("<ButtonRelease-1>", self._on_lmb_up)
            self.bind("<B1-Motion>",       self._on_mouse_drag)
            self.bind("<ButtonPress-2>",   self._on_mmb_down)
            self.bind("<B2-Motion>",       self._on_mouse_drag)
            self.bind("<ButtonRelease-2>", self._on_mmb_up)
            self.bind("<ButtonPress-3>",   self._on_rmb)
            self.bind("<MouseWheel>",      self._on_wheel)
            self.bind("<Button-4>",        self._on_wheel)   # Linux scroll up
            self.bind("<Button-5>",        self._on_wheel)   # Linux scroll down
            self.bind("<KeyPress>",        self._on_key_down)
            self.bind("<KeyRelease>",      self._on_key_up)
            self.bind("<FocusOut>",        self._on_focus_out)

            # Some pyopengltk/OpenGLFrame builds on Windows do not reliably
            # receive KeyPress even after focus_set(). Guarded bind_all keeps
            # controls working, while _accept_key_event requires the canvas to
            # own focus (or the pointer when Tk reports no focus at all).
            self.bind_all("<KeyPress>",   self._on_key_down, add="+")
            self.bind_all("<KeyRelease>", self._on_key_up,   add="+")
            self.bind_all("<MouseWheel>", self._on_global_wheel, add="+")
            self.bind_all("<Button-4>",   self._on_global_wheel, add="+")
            self.bind_all("<Button-5>",   self._on_global_wheel, add="+")

        # ------------------------------------------------------------------
        # pyopengltk lifecycle
        # ------------------------------------------------------------------

        def initgl(self) -> None:
            """Called once by pyopengltk after the GL context is created."""
            try:
                GL.glEnable(GL.GL_DEPTH_TEST)
                GL.glEnable(GL.GL_PROGRAM_POINT_SIZE)
                GL.glEnable(GL.GL_BLEND)
                GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
                GL.glClearColor(0.055, 0.063, 0.086, 1.0)  # editor dark bg

                self._solid_prog     = ShaderProgram.build(SOLID_VERT, SOLID_FRAG)
                self._sky_portal_prog = ShaderProgram.build(
                    SKY_PORTAL_VERT, SKY_PORTAL_FRAG,
                )
                self._billboard_prog = ShaderProgram.build3(
                    BILLBOARD_VERT, BILLBOARD_GEOM, BILLBOARD_FRAG
                )
                try:
                    self._stencil_bits = int(GL.glGetIntegerv(GL.GL_STENCIL_BITS))
                except Exception:
                    self._stencil_bits = 0
                GL.glClearStencil(0)
                if self._stencil_bits <= 0:
                    print(
                        "[view3d] sky portals disabled: framebuffer has no stencil buffer",
                        file=sys.stderr,
                    )

                # Build texture cache if the textures directory is available
                if self._textures_dir:
                    try:
                        from view3d.dtx import TextureCache
                        self._tex_cache = TextureCache(self._textures_dir)
                        print(
                            f"[view3d] texture cache ready — "
                            f"{self._tex_cache.index_size} DTX files indexed",
                            file=sys.stderr,
                        )
                    except Exception as _exc:
                        print(f"[view3d] texture cache init failed: {_exc}",
                              file=sys.stderr)

                # Build skin texture cache for object WorldObject.Skin paths.
                if self._skins_dir:
                    try:
                        from view3d.dtx import TextureCache
                        self._skin_cache = TextureCache(self._skins_dir)
                        print(
                            f"[view3d] skin cache ready — "
                            f"{self._skin_cache.index_size} DTX files indexed",
                            file=sys.stderr,
                        )
                    except Exception as _exc:
                        print(f"[view3d] skin cache init failed: {_exc}",
                              file=sys.stderr)

                # Build ABC model index if the extracted MODELS folder exists.
                if self._models_dir:
                    try:
                        self._obj_model_cache = ObjectModelCache(self._models_dir)
                        print(
                            f"[view3d] ABC model cache ready — "
                            f"{self._obj_model_cache.index_size} ABC files indexed",
                            file=sys.stderr,
                        )
                    except Exception as _exc:
                        print(f"[view3d] ABC model cache init failed: {_exc}",
                              file=sys.stderr)

                self._ready = True

                # Drain any level load that was queued before GL was ready
                if self._pending_level_args is not None:
                    args, self._pending_level_args = self._pending_level_args, None
                    self.load_level(*args)

            except Exception as exc:
                print(f"[view3d] GL init error: {exc}", file=sys.stderr)
                self._ready = False

        def redraw(self) -> None:
            """Called by pyopengltk each frame."""
            if not self._ready:
                return
            try:
                self._render_after = None
                self._render()
                self._last_render_time = time.monotonic()
                self._post_status()
            except Exception as exc:
                print(f"[view3d] render error: {exc}", file=sys.stderr)

        def _request_render(self, immediate: bool = False) -> None:
            """Schedule one GL redraw, coalescing bursts from drag/key input."""
            if not self._ready:
                return
            if immediate:
                if self._render_after is not None:
                    try:
                        self.after_cancel(self._render_after)
                    except Exception:
                        pass
                    self._render_after = None
                self.tkExpose(None)
                return
            if self._render_after is not None:
                return
            elapsed_ms = (time.monotonic() - self._last_render_time) * 1000.0
            delay_ms = max(0, self._render_min_interval_ms - int(elapsed_ms))
            self._render_after = self.after(delay_ms, lambda: self.tkExpose(None))

        def _profile_record(self, timings: dict) -> None:
            if not self._profile_enabled:
                return
            for key, value in timings.items():
                self._profile_accum[key] = self._profile_accum.get(key, 0.0) + value
            self._profile_frames += 1
            if self._profile_frames < 120:
                return
            frames = max(1, self._profile_frames)
            parts = [
                f"{key}={self._profile_accum.get(key, 0.0) / frames:.2f}ms"
                for key in ("frame", "bsp", "sky", "abc", "sprites")
            ]
            print("[view3d profile] " + "  ".join(parts), file=sys.stderr)
            self._profile_accum.clear()
            self._profile_frames = 0

        # ------------------------------------------------------------------
        # Rendering
        # ------------------------------------------------------------------

        def _render(self) -> None:
            frame_t0 = time.perf_counter()
            timings = {"bsp": 0.0, "sky": 0.0, "abc": 0.0, "sprites": 0.0}
            w = self.winfo_width()
            h = self.winfo_height()
            if w < 1 or h < 1:
                return

            GL.glViewport(0, 0, w, h)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)

            aspect = w / h
            mvp    = self._camera.mvp(aspect)

            # --- BSP geometry ---
            self._last_models_drawn = 0
            self._last_tris_drawn   = 0
            t0 = time.perf_counter()
            if self._solid_prog and self._bsp_draw_batch:
                # Derive fog distances from camera far plane so fog scales
                # naturally with the level regardless of its physical size.
                cam_far   = self._camera.far
                fog_near  = cam_far * 0.20   # fog starts at 20 % of far clip
                fog_far   = cam_far * 0.80   # fully opaque at 80 % of far clip
                self._last_models_drawn, self._last_tris_drawn = draw_bsp_batch(
                    self._bsp_draw_batch,
                    self._solid_prog,
                    mvp,
                    light_dir=_LIGHT_DIR,
                    fog_enabled=self._fog_enabled,
                    fog_near=fog_near,
                    fog_far=fog_far,
                    fog_color=_FOG_COLOR,
                    render_pass="opaque",
                )
            timings["bsp"] = (time.perf_counter() - t0) * 1000.0

            # --- Camera-relative sky, clipped by visible SURF_SKY portals ---
            self._last_sky_layers_drawn = 0
            self._last_sky_tris_drawn = 0
            t0 = time.perf_counter()
            if (
                self._stencil_bits > 0
                and self._sky_scene is not None
                and self._sky_draw_batch is not None
                and self._sky_portal_prog is not None
                and self._solid_prog is not None
                and self._bsp_world is not None
            ):
                world_min = getattr(self._bsp_world, "world_extents_min", None)
                world_max = getattr(self._bsp_world, "world_extents_max", None)
                if world_min is None or world_max is None:
                    bounds = normal_render_world_bounds(
                        self._bsp_world,
                        hidden_helper_model_names=self._hidden_helper_model_names(),
                    )
                    if bounds is not None:
                        world_min, world_max = bounds
                if world_min is not None and world_max is not None:
                    camera_game = display_to_game_point(self._camera.eye)
                    sky_eye_game = self._sky_scene.view_position(
                        camera_game,
                        world_min,
                        world_max,
                    )
                    sky_eye = game_to_display_point(sky_eye_game)
                    sky_mvp = self._camera.mvp_from_eye(
                        tuple(float(v) for v in sky_eye),
                        aspect,
                        near=0.01,
                        far=self._sky_scene.far_distance,
                    )
                    (
                        self._last_sky_layers_drawn,
                        self._last_sky_tris_drawn,
                    ) = draw_sky_batch(
                        self._sky_draw_batch,
                        self._sky_portal_prog,
                        self._solid_prog,
                        mvp,
                        sky_mvp,
                    )
            timings["sky"] = (time.perf_counter() - t0) * 1000.0

            # --- Supported WorldObject ABC meshes ---
            self._last_obj_models_drawn = 0
            self._last_obj_tris_drawn   = 0
            self._modeled_world_indices = set()
            t0 = time.perf_counter()
            drag_world_index = (
                self._3d_drag_index
                if self._drag_mode == "3d_drag" and self._3d_drag_index >= 0
                else None
            )
            if self._solid_prog and self._object_model_items:
                cam_far   = self._camera.far
                fog_near  = cam_far * 0.20
                fog_far   = cam_far * 0.80
                (
                    self._last_obj_models_drawn,
                    self._last_obj_tris_drawn,
                    self._modeled_world_indices,
                ) = draw_object_model_items(
                        self._object_model_items,
                        self._solid_prog,
                        mvp,
                        light_dir=_LIGHT_DIR,
                        selected_index=self._selected_index,
                        tex_cache=self._tex_cache,
                        skin_cache=self._skin_cache,
                        fog_enabled=self._fog_enabled,
                        fog_near=fog_near,
                        fog_far=fog_far,
                        fog_color=_FOG_COLOR,
                        only_world_index=drag_world_index,
                    )
            timings["abc"] = (time.perf_counter() - t0) * 1000.0

            # Translucent BSP belongs after the sky and opaque object models.
            # BOOTCAMP's round church windows deliberately layer stained glass
            # over SURF_SKY portals; drawing this pass earlier lets sky erase it.
            t0 = time.perf_counter()
            if self._solid_prog and self._bsp_draw_batch:
                cam_far = self._camera.far
                draw_bsp_batch(
                    self._bsp_draw_batch,
                    self._solid_prog,
                    mvp,
                    light_dir=_LIGHT_DIR,
                    fog_enabled=self._fog_enabled,
                    fog_near=cam_far * 0.20,
                    fog_far=cam_far * 0.80,
                    fog_color=_FOG_COLOR,
                    render_pass="translucent",
                )
            timings["bsp"] += (time.perf_counter() - t0) * 1000.0

            # Flush any pending sprite-position VBO patches.
            if self._sprite_position_pending and self._sprites:
                self._flush_sprite_positions()

            # --- WorldObject sprites ---
            t0 = time.perf_counter()
            if self._billboard_prog and self._sprites:
                sprites = self._sprites
                eye     = self._camera.eye

                # Sort back-to-front (painter's algorithm) so nearer sprites
                # paint over farther ones when quads overlap.
                if self._drag_mode == "3d_drag":
                    order = np.arange(sprites.count, dtype=np.int64)
                elif sprites.count > 0:
                    eye_np = np.array(eye, dtype=np.float32)
                    diffs  = sprites.positions - eye_np          # (N, 3)
                    sq_dist = np.einsum("ij,ij->i", diffs, diffs)  # (N,)
                    order   = np.argsort(-sq_dist)               # back→front
                else:
                    order = np.empty(0, dtype=np.int64)

                # Disable depth writes/test for sprites so they always render
                # on top of BSP geometry as editor handles.
                GL.glDepthFunc(GL.GL_ALWAYS)
                GL.glDepthMask(GL.GL_FALSE)

                with self._billboard_prog as prog:
                    prog.set_mat4("uMVP",        mvp)
                    prog.set_vec3("uCamPos",     tuple(float(v) for v in eye))
                    prog.set_float("uWorldSize", _SPRITE_WORLD_SIZE)
                    prog.set_int("uPickMode",    0)
                    prog.set_int("uSelected",    self._selected_index)
                    GL.glBindVertexArray(sprites.vao)
                    for i in order:
                        world_idx = sprites.world_indices[int(i)]
                        is_marker = (
                            world_idx == self._selected_index
                            or world_idx == self._3d_drag_index
                        )
                        if not should_draw_billboard_for_modeled_object(
                            world_idx,
                            self._modeled_world_indices,
                            selected_index=self._selected_index,
                            drag_index=self._3d_drag_index,
                            show_object_helpers=self._show_object_helper_billboards,
                        ):
                            continue
                        prog.set_float(
                            "uWorldSize",
                            _SPRITE_WORLD_SIZE * (1.35 if is_marker else 1.0),
                        )
                        prog.set_int("uObjectIndex", world_idx)
                        GL.glDrawArrays(GL.GL_POINTS, int(i), 1)
                    GL.glBindVertexArray(0)

                # Restore normal depth state for any subsequent passes
                GL.glDepthMask(GL.GL_TRUE)
                GL.glDepthFunc(GL.GL_LESS)
            timings["sprites"] = (time.perf_counter() - t0) * 1000.0
            timings["frame"] = (time.perf_counter() - frame_t0) * 1000.0
            self._profile_record(timings)

        # ------------------------------------------------------------------
        # Status bar update (called after each frame)
        # ------------------------------------------------------------------

        def _post_status(self) -> None:
            if self._on_status_cb is None:
                return
            eye  = display_to_game_point(self._camera.eye)
            mode = self._camera.mode.capitalize()
            n    = self._obj_count
            md   = getattr(self, "_last_models_drawn", 0)
            tri  = getattr(self, "_last_tris_drawn",   0)
            omd  = getattr(self, "_last_obj_models_drawn", 0)
            otri = getattr(self, "_last_obj_tris_drawn",   0)
            sky  = getattr(self, "_last_sky_layers_drawn", 0)
            sky_text = f"  ·  {sky} sky" if self._sky_scene is not None else ""
            text = (f"{mode}  ·  "
                    f"eye ({eye[0]:.0f}, {eye[1]:.0f}, {eye[2]:.0f})  ·  "
                    f"{n} obj  ·  {md} BSP  {tri:,} tris  ·  "
                    f"{omd} ABC  {otri:,} tris{sky_text}")
            self._on_status_cb(text)

        # ------------------------------------------------------------------
        # Sprite-position VBO flush
        # ------------------------------------------------------------------

        def _flush_sprite_positions(self) -> None:
            """
            Write any pending position changes to the sprite VBO.

            Called at the start of _render() so the GPU always sees the most
            recent object marker positions before the draw call.  Requires a
            live GL context.
            """
            sprites = self._sprites
            if sprites is None or not self._sprite_position_pending:
                self._sprite_position_pending.clear()
                return
            stride = 6 * 4   # 6 floats × 4 bytes per sprite vertex
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, sprites.vbo)
            for vbo_idx, xyz in self._sprite_position_pending.items():
                if 0 <= vbo_idx < sprites.count:
                    data = np.asarray(xyz, dtype=np.float32)
                    GL.glBufferSubData(
                        GL.GL_ARRAY_BUFFER,
                        int(vbo_idx) * stride,   # byte offset to this vertex
                        12,                       # 3 floats × 4 bytes
                        data,
                    )
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
            self._sprite_position_pending.clear()

        # ------------------------------------------------------------------
        # Colour-buffer picking
        # ------------------------------------------------------------------

        def _pick(self, sx: int, sy: int) -> int:
            """Return the world_index of the sprite under (sx, sy), or -1."""
            if not self._ready or not self._billboard_prog or not self._sprites:
                return -1
            w = self.winfo_width()
            h = self.winfo_height()
            if w < 1 or h < 1:
                return -1

            aspect = w / h
            mvp    = self._camera.mvp(aspect)

            # Render sprites only with pick encoding into the back buffer
            old_clear = (0.055, 0.063, 0.086, 1.0)
            GL.glClearColor(1.0, 1.0, 1.0, 1.0)   # white = background
            GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)

            GL.glDepthFunc(GL.GL_ALWAYS)
            GL.glDepthMask(GL.GL_FALSE)

            with self._billboard_prog as prog:
                prog.set_mat4("uMVP",        mvp)
                prog.set_vec3("uCamPos",     tuple(float(v) for v in self._camera.eye))
                prog.set_float("uWorldSize", _SPRITE_WORLD_SIZE)
                prog.set_int("uPickMode",    1)
                prog.set_int("uSelected",   -1)
                GL.glBindVertexArray(self._sprites.vao)
                for i, world_idx in enumerate(self._sprites.world_indices):
                    prog.set_int("uObjectIndex", world_idx)
                    GL.glDrawArrays(GL.GL_POINTS, i, 1)
                GL.glBindVertexArray(0)

            GL.glDepthMask(GL.GL_TRUE)
            GL.glDepthFunc(GL.GL_LESS)

            GL.glFlush()
            gl_y  = h - sy - 1
            pixel = GL.glReadPixels(sx, gl_y, 1, 1,
                                    GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
            # glReadPixels return type varies across PyOpenGL versions and
            # platforms.  On Windows it often returns a flat bytes/1-D buffer
            # (pixel[0] = R byte) rather than the (1,1,3) shaped array that
            # older code assumes.  Flatten to a 3-element uint8 array before
            # indexing so both shapes work.
            rgb = np.frombuffer(bytes(bytearray(pixel)), dtype=np.uint8).flatten()
            r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])

            # Restore clear colour and schedule a clean redraw
            GL.glClearColor(*old_clear)
            self._request_render()

            return decode_pick_color(r, g, b)

        # ------------------------------------------------------------------
        # Level data management
        # ------------------------------------------------------------------

        def _delete_sprites(self) -> None:
            if self._sprites:
                try:
                    delete_sprites(self._sprites)
                except Exception:
                    pass
                self._sprites = None

        def _rebuild_sprites(self) -> None:
            self._delete_sprites()
            self._sprite_position_pending.clear()
            if self._objects:
                self._sprites = upload_objects(
                    self._objects,
                    categorize,
                    include_world_helpers=self._show_world_helper_billboards,
                    object_helper_indices=[
                        item.world_index for item in self._object_model_items
                    ],
                    selected_index=self._selected_index,
                    world_helper_metadata=self._world_helper_metadata,
                )

        def _hidden_helper_model_names(self) -> set[str]:
            """Return catalog-derived BSP helpers hidden by normal rendering."""
            return hidden_world_helper_model_names(
                self._objects,
                categorize,
                self._world_helper_metadata,
            )

        def _fit_camera_to_level(self) -> bool:
            """Fit to ordinary rendered BSP geometry in viewport coordinates."""
            if self._bsp_world is None:
                return False
            bounds = normal_render_world_bounds(
                self._bsp_world,
                hidden_helper_model_names=self._hidden_helper_model_names(),
            )
            if bounds is None:
                return False
            display_lo, display_hi = game_to_display_bounds(*bounds)
            self._camera.fit_to_bounds(display_lo, display_hi)
            return True

        def _rebuild_sky(self, force: bool = False) -> None:
            """Resolve DAT sky objects and build the portal/layer draw batch."""
            scene = resolve_sky_scene(self._objects)
            if not force and scene == self._sky_scene and self._sky_draw_batch is not None:
                return
            if self._soft_sky_model is not None:
                self._mesh_cache.discard_model(self._soft_sky_model)
            self._sky_scene = scene
            self._sky_draw_batch = None
            self._soft_sky_model = None
            if self._sky_scene is None or self._bsp_world is None:
                return
            soft_texture = resolve_soft_sky_texture(
                self._sky_scene,
                self._tex_cache,
            )
            self._soft_sky_model = build_soft_sky_model(
                self._sky_scene,
                soft_texture,
            )
            self._sky_draw_batch = build_sky_draw_batch(
                self._bsp_world,
                self._sky_scene,
                self._mesh_cache,
                tex_cache=self._tex_cache,
                soft_sky_model=self._soft_sky_model,
            )

        def load_level(self, level, bsp_world, objects, load_profile=None) -> None:
            """
            Upload geometry and sprites for a new level.
            Called by View3D.set_active_level().
            bsp_world may be None if BSP parsing failed.

            If GL has not been initialized yet (widget not yet shown) the call
            is queued in _pending_level_args and replayed inside initgl().
            """
            if load_profile is None:
                load_profile = _new_load_profile(level)
            if not self._ready:
                self._pending_level_args = (
                    level,
                    bsp_world,
                    objects,
                    load_profile,
                )
                return
            stage_started = time.perf_counter()
            self._discard_pending_transform_commit()
            self._bsp_draw_batch = None
            self._sky_draw_batch = None
            self._delete_sprites()

            self._level      = level
            self._bsp_world  = bsp_world
            self._objects    = objects
            self._obj_count  = len(objects)
            self._object_model_items = []
            self._selected_index = -1
            self._3d_drag_index = -1
            self._3d_drag_orig_xyz = None
            self._3d_drag_moved = False
            self._sprite_position_pending.clear()   # stale patches must not apply to new VBO

            if bsp_world is not None:
                self._mesh_cache.activate_level(
                    level if level is not None else bsp_world,
                    getattr(bsp_world, "world_models", []) or [],
                    tex_cache=self._tex_cache,
                )
            _mark_load_stage(load_profile, "canvas_reset", stage_started)

            stage_started = time.perf_counter()
            if objects:
                if self._obj_model_cache is not None:
                    self._object_model_items = build_render_items(
                        objects,
                        self._obj_model_cache,
                        skin_cache=self._skin_cache,
                        tex_cache=self._tex_cache,
                        bsp_world=bsp_world,
                        actor_visuals=self._actor_visuals,
                    )
            _mark_load_stage(load_profile, "object_models", stage_started)

            stage_started = time.perf_counter()
            if objects:
                self._rebuild_sprites()
            _mark_load_stage(load_profile, "sprites", stage_started)

            stage_started = time.perf_counter()
            if bsp_world is not None:
                self._bsp_draw_batch = build_bsp_draw_batch(
                    bsp_world,
                    self._mesh_cache,
                    tex_cache=self._tex_cache,
                    helper_bsp_mode=self._helper_bsp_mode,
                    helper_role_groups=self._helper_role_groups,
                    hidden_helper_model_names=self._hidden_helper_model_names(),
                )
            _mark_load_stage(load_profile, "bsp_meshes", stage_started)

            stage_started = time.perf_counter()
            self._rebuild_sky()
            _mark_load_stage(load_profile, "sky", stage_started)

            stage_started = time.perf_counter()
            self._fit_camera_to_level()
            _mark_load_stage(load_profile, "camera_fit", stage_started)

            self._request_render()
            _emit_load_profile(load_profile)

        def reload_sprites(self, objects) -> None:
            """Re-upload object sprites after ops change the level state."""
            self._discard_pending_transform_commit()
            self._delete_sprites()
            self._sprite_position_pending.clear()   # new VBO; old patches are invalid
            self._objects   = objects
            self._obj_count = len(objects)
            self._object_model_items = []
            if objects:
                if self._obj_model_cache is not None:
                    self._object_model_items = build_render_items(
                        objects,
                        self._obj_model_cache,
                        skin_cache=self._skin_cache,
                        tex_cache=self._tex_cache,
                        bsp_world=self._bsp_world,
                        actor_visuals=self._actor_visuals,
                    )
                self._rebuild_sprites()
            self._rebuild_sky()
            self._request_render()

        def reload_level_state(self, bsp_world, objects) -> None:
            """Reload sprites and the BSP draw batch without refitting camera."""
            self._discard_pending_transform_commit()
            self._delete_sprites()
            self._sprite_position_pending.clear()
            self._objects = objects
            self._obj_count = len(objects)
            self._object_model_items = []
            self._bsp_world = bsp_world

            if bsp_world is not None:
                self._mesh_cache.activate_level(
                    self._level if self._level is not None else bsp_world,
                    getattr(bsp_world, "world_models", []) or [],
                    tex_cache=self._tex_cache,
                )
            self._bsp_draw_batch = None
            if bsp_world is not None:
                self._bsp_draw_batch = build_bsp_draw_batch(
                    bsp_world,
                    self._mesh_cache,
                    tex_cache=self._tex_cache,
                    helper_bsp_mode=self._helper_bsp_mode,
                    helper_role_groups=self._helper_role_groups,
                    hidden_helper_model_names=self._hidden_helper_model_names(),
                )

            if objects:
                if self._obj_model_cache is not None:
                    self._object_model_items = build_render_items(
                        objects,
                        self._obj_model_cache,
                        skin_cache=self._skin_cache,
                        tex_cache=self._tex_cache,
                        bsp_world=bsp_world,
                        actor_visuals=self._actor_visuals,
                    )
                self._rebuild_sprites()
            self._rebuild_sky(force=True)
            self._request_render()

        def set_show_object_helper_billboards(self, enabled: bool) -> None:
            self._show_object_helper_billboards = bool(enabled)
            if not self._ready:
                return
            self._request_render()

        def set_show_world_helper_billboards(self, enabled: bool) -> None:
            self._show_world_helper_billboards = bool(enabled)
            if not self._ready:
                return
            self._rebuild_sprites()
            self._request_render()

        def set_show_helper_billboards(self, enabled: bool) -> None:
            self.set_show_object_helper_billboards(enabled)

        def set_helper_bsp_mode(self, mode: str) -> None:
            self._helper_bsp_mode = str(mode or "normal").lower()
            if not self._ready:
                return
            if self._bsp_world is not None:
                self._bsp_draw_batch = build_bsp_draw_batch(
                    self._bsp_world,
                    self._mesh_cache,
                    tex_cache=self._tex_cache,
                    helper_bsp_mode=self._helper_bsp_mode,
                    helper_role_groups=self._helper_role_groups,
                    hidden_helper_model_names=self._hidden_helper_model_names(),
                )
            self._request_render()

        def set_helper_role_groups(self, groups) -> None:
            self._helper_role_groups = set(groups or ())
            if not self._ready:
                return
            if self._bsp_world is not None:
                self._bsp_draw_batch = build_bsp_draw_batch(
                    self._bsp_world,
                    self._mesh_cache,
                    tex_cache=self._tex_cache,
                    helper_bsp_mode=self._helper_bsp_mode,
                    helper_role_groups=self._helper_role_groups,
                    hidden_helper_model_names=self._hidden_helper_model_names(),
                )
            self._request_render()

        def set_selected_index(self, world_index: int) -> None:
            self._selected_index = world_index
            if not self._show_world_helper_billboards and self._objects:
                self._rebuild_sprites()
            self._request_render()

        # ------------------------------------------------------------------
        # Input — orbit / pan / fly
        # ------------------------------------------------------------------

        def _on_resize(self, _e) -> None:
            self._request_render()

        # ------------------------------------------------------------------
        # Sprite helpers (Stage 1 + 2)
        # ------------------------------------------------------------------

        def _sprite_vbo_index(self, world_index: int) -> int:
            """Return the VBO row for world_index, or -1 if it has no sprite."""
            if self._sprites is None:
                return -1
            try:
                return self._sprites.world_indices.index(world_index)
            except ValueError:
                return -1

        def _get_sprite_xyz_for_index(self, world_index: int) -> Optional[np.ndarray]:
            """Return a copy of the marker position for world_index, if present."""
            if self._sprites is None:
                return None
            vbo_idx = self._sprite_vbo_index(world_index)
            if vbo_idx < 0:
                return None
            return self._sprites.positions[vbo_idx].copy()

        def _get_world_y_for_index(self, world_index: int) -> float:
            """Return the Y position of the sprite with the given world_index."""
            xyz = self._get_sprite_xyz_for_index(world_index)
            return float(xyz[1]) if xyz is not None else 0.0

        def _set_local_object_pos(self, world_index: int, xyz: np.ndarray) -> None:
            """
            Update this view's materialized object copy so ABC meshes follow
            the marker during a drag.  The project model is only changed on
            mouse-up through _on_move_cb.
            """
            if 0 <= world_index < len(self._objects):
                obj = self._objects[world_index]
                try:
                    game_xyz = display_to_game_point(xyz)
                    obj.set("Pos", [
                        float(game_xyz[0]),
                        float(game_xyz[1]),
                        float(game_xyz[2]),
                    ])
                except Exception:
                    pass

        @staticmethod
        def _safe_rotation(value: Any) -> list:
            try:
                vals = list(value)
            except Exception:
                vals = []
            vals = (vals + [0.0, 0.0, 0.0, 0.0])[:4]
            return [float(v) for v in vals]

        def _get_rotation_for_index(self, world_index: int) -> list:
            if 0 <= world_index < len(self._objects):
                try:
                    return self._safe_rotation(
                        self._objects[world_index].get("Rotation"))
                except Exception:
                    pass
            return [0.0, 0.0, 0.0, 0.0]

        def _set_local_object_rotation(self, world_index: int,
                                       rotation: list) -> None:
            if 0 <= world_index < len(self._objects):
                try:
                    self._objects[world_index].set("Rotation", list(rotation))
                except Exception:
                    pass

        def _set_sprite_xyz_for_index(
            self, world_index: int, xyz: np.ndarray, update_object: bool = False
        ) -> None:
            """Patch the CPU marker array and queue the matching VBO update."""
            if self._sprites is None:
                return
            vbo_idx = self._sprite_vbo_index(world_index)
            if vbo_idx < 0:
                return
            xyz = np.asarray(xyz, dtype=np.float32)
            self._sprites.positions[vbo_idx] = xyz
            self._sprite_position_pending[vbo_idx] = xyz
            if update_object:
                self._set_local_object_pos(world_index, xyz)

        def _schedule_move_commit_xyz(
            self, world_index: int, xyz: np.ndarray, delay_ms: int = 120
        ) -> None:
            """Commit the latest exact position after rapid input settles."""
            game_xyz = display_to_game_point(xyz)
            self._pending_move_xyz = (
                world_index,
                float(game_xyz[0]),
                float(game_xyz[1]),
                float(game_xyz[2]),
            )
            self._pending_move_xz = None
            self._pending_elevation = None
            self._schedule_transform_commit(delay_ms)

        def _schedule_move_commit_xz(
            self, world_index: int, wx: float, wz: float, delay_ms: int = 120
        ) -> None:
            """Fallback commit for callers that only accept X/Z movement."""
            game_xyz = display_to_game_point((float(wx), 0.0, float(wz)))
            self._pending_move_xz = (
                world_index,
                float(game_xyz[0]),
                float(game_xyz[2]),
            )
            self._pending_move_xyz = None
            self._pending_elevation = None
            self._schedule_transform_commit(delay_ms)

        def _schedule_rotation_commit(
            self, world_index: int, rotation: tuple, delay_ms: int = 120
        ) -> None:
            """Commit the latest rotation after rapid key-repeat settles."""
            self._pending_rotation = (
                world_index,
                tuple(float(v) for v in rotation),
            )
            self._schedule_transform_commit(delay_ms)

        def _schedule_elevation_commit(
            self, world_index: int, new_y: float, delay_ms: int = 120
        ) -> None:
            """Fallback vertical commit when exact XYZ movement is unavailable."""
            self._pending_elevation = (world_index, float(new_y))
            self._pending_move_xyz = None
            self._schedule_transform_commit(delay_ms)

        def _schedule_transform_commit(self, delay_ms: int = 120) -> None:
            if self._transform_commit_after is not None:
                try:
                    self.after_cancel(self._transform_commit_after)
                except Exception:
                    pass
            self._transform_commit_after = self.after(
                delay_ms, self._flush_transform_commit)

        def _flush_transform_commit(self) -> None:
            """Commit any pending preview transform to the editor model."""
            if self._transform_commit_after is not None:
                try:
                    self.after_cancel(self._transform_commit_after)
                except Exception:
                    pass
                self._transform_commit_after = None

            move_xyz, self._pending_move_xyz = self._pending_move_xyz, None
            move_xz, self._pending_move_xz = self._pending_move_xz, None
            elevation, self._pending_elevation = self._pending_elevation, None
            rotation, self._pending_rotation = self._pending_rotation, None

            if move_xyz is not None and self._on_move_xyz_cb is not None:
                idx, wx, wy, wz = move_xyz
                self._on_move_xyz_cb(idx, wx, wy, wz)
            elif move_xz is not None and self._on_move_cb is not None:
                idx, wx, wz = move_xz
                self._on_move_cb(idx, wx, wz)
            elif elevation is not None and self._on_elevate_cb is not None:
                idx, new_y = elevation
                self._on_elevate_cb(idx, new_y)

            if rotation is not None and self._on_rotate_cb is not None:
                idx, rot = rotation
                self._on_rotate_cb(idx, rot)

        def _discard_pending_transform_commit(self) -> None:
            """Drop pending preview commits when the loaded object set changes."""
            if self._transform_commit_after is not None:
                try:
                    self.after_cancel(self._transform_commit_after)
                except Exception:
                    pass
                self._transform_commit_after = None
            self._pending_move_xyz = None
            self._pending_move_xz = None
            self._pending_elevation = None
            self._pending_rotation = None

        def _restore_3d_drag_position(self) -> None:
            """Restore local marker/model state when a drag is cancelled."""
            if self._3d_drag_index >= 0 and self._3d_drag_orig_xyz is not None:
                self._set_sprite_xyz_for_index(
                    self._3d_drag_index,
                    self._3d_drag_orig_xyz,
                    update_object=True,
                )

        def _nudge_step(self, e: tk.Event, vertical: bool = False) -> float:
            step = _NUDGE_Y_STEP if vertical else _NUDGE_XZ_STEP
            if bool(getattr(e, "state", 0) & 0x0001):
                step *= _NUDGE_FAST_MULT
            return step

        def _rotate_step_degrees(self, e: tk.Event) -> float:
            step = _ROTATE_YAW_STEP_DEG
            if bool(getattr(e, "state", 0) & 0x0001):
                step *= _ROTATE_FAST_MULT
            return step

        def _camera_xz_axes(self) -> "tuple[np.ndarray, np.ndarray]":
            """Return camera-relative forward/right vectors flattened to XZ."""
            forward = (self._camera.target - self._camera.eye).astype(np.float32)
            forward[1] = 0.0
            flen = float(np.linalg.norm(forward))
            if flen < 1e-6:
                forward = np.array([0.0, 0.0, -1.0], dtype=np.float32)
            else:
                forward /= flen

            world_up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
            right = np.cross(forward, world_up).astype(np.float32)
            rlen = float(np.linalg.norm(right))
            if rlen < 1e-6:
                right = np.array([1.0, 0.0, 0.0], dtype=np.float32)
            else:
                right /= rlen
            return forward, right

        def _nudge_selected_xz(self, direction: np.ndarray, step: float) -> bool:
            """Move selected object on X/Z, preserving its current Y in 3-D."""
            if (self._selected_index < 0
                    or self._sprites is None):
                return False
            if self._on_move_xyz_cb is None and self._on_move_cb is None:
                return False
            xyz = self._get_sprite_xyz_for_index(self._selected_index)
            if xyz is None:
                return False

            xyz[0] += float(direction[0]) * step
            xyz[2] += float(direction[2]) * step
            self._set_sprite_xyz_for_index(
                self._selected_index, xyz, update_object=True)
            if self._on_move_xyz_cb is not None:
                self._schedule_move_commit_xyz(self._selected_index, xyz)
            elif self._on_move_cb is not None:
                self._schedule_move_commit_xz(
                    self._selected_index, float(xyz[0]), float(xyz[2]))
            self._request_render()
            return True

        def _nudge_selected_y(self, delta_y: float) -> bool:
            """Move selected object vertically, using the elevation callback."""
            if (self._selected_index < 0
                    or self._sprites is None):
                return False
            if self._on_move_xyz_cb is None and self._on_elevate_cb is None:
                return False
            xyz = self._get_sprite_xyz_for_index(self._selected_index)
            if xyz is None:
                return False

            xyz[1] += float(delta_y)
            self._set_sprite_xyz_for_index(
                self._selected_index, xyz, update_object=True)
            if self._on_move_xyz_cb is not None:
                self._schedule_move_commit_xyz(self._selected_index, xyz)
            else:
                self._schedule_elevation_commit(
                    self._selected_index, float(xyz[1]))
            self._request_render()
            return True

        def _rotate_selected_yaw(self, delta_degrees: float) -> bool:
            """Rotate selected object around world Y."""
            if self._selected_index < 0 or self._on_rotate_cb is None:
                return False
            if (self._selected_index >= len(self._objects)
                    or self._objects[self._selected_index].get("Rotation") is None):
                return False
            rot = self._get_rotation_for_index(self._selected_index)
            rot[1] += math.radians(float(delta_degrees))
            self._set_local_object_rotation(self._selected_index, rot)
            self._schedule_rotation_commit(self._selected_index, tuple(rot))
            self._request_render()
            return True

        def _handle_orbit_nudge_key(self, e: tk.Event) -> bool:
            """Handle selected-object keyboard nudges in orbit mode."""
            if self._camera.mode != "orbit" or self._place_mode:
                return False

            k = e.keysym.lower()
            forward, right = self._camera_xz_axes()
            xz_step = self._nudge_step(e, vertical=False)
            y_step = self._nudge_step(e, vertical=True)
            rot_step = self._rotate_step_degrees(e)

            if k == "up":
                return self._nudge_selected_xz(forward, xz_step)
            if k == "down":
                return self._nudge_selected_xz(-forward, xz_step)
            if k == "right":
                return self._nudge_selected_xz(right, xz_step)
            if k == "left":
                return self._nudge_selected_xz(-right, xz_step)
            if k in {"prior", "page_up", "e"}:
                return self._nudge_selected_y(y_step)
            if k in {"next", "page_down", "q"}:
                return self._nudge_selected_y(-y_step)
            if k in {"bracketleft", "braceleft"}:
                return self._rotate_selected_yaw(-rot_step)
            if k in {"bracketright", "braceright"}:
                return self._rotate_selected_yaw(rot_step)
            return False

        def _screen_hit_sprite(self, sx: int, sy: int) -> int:
            """
            Fast CPU-side screen-space sprite hit test.

            Projects all sprite world positions through the current MVP and
            returns the world_index of the nearest sprite within
            *_SPRITE_HIT_RADIUS_PX*, or -1 if none.
            No GL round-trip; safe to call in _on_lmb_down.
            """
            if self._sprites is None or self._sprites.count == 0:
                return -1
            w = self.winfo_width()
            h = self.winfo_height()
            if w < 1 or h < 1:
                return -1

            aspect  = w / h
            mvp     = self._camera.mvp(aspect)          # (4, 4) float32

            pos  = self._sprites.positions              # (N, 3) float32
            N    = pos.shape[0]
            ones = np.ones((N, 1), dtype=np.float32)
            pos_h = np.hstack([pos, ones])              # (N, 4)

            # clip[i] = mvp @ pos_h[i]  (matches GLSL clip = uMVP * aPos)
            clips  = (mvp @ pos_h.T).T                  # (N, 4)
            wc     = clips[:, 3]                        # clip-space w

            in_front = wc > 0.0
            safe_wc  = np.where(in_front, wc, 1.0)
            ndc_x    = clips[:, 0] / safe_wc
            ndc_y    = clips[:, 1] / safe_wc

            scr_x = (ndc_x + 1.0) * 0.5 * w
            scr_y = (1.0 - ndc_y) * 0.5 * h           # NDC Y is flipped vs screen

            d2    = (scr_x - sx) ** 2 + (scr_y - sy) ** 2
            valid = in_front & (d2 < _SPRITE_HIT_RADIUS_PX ** 2)
            if not np.any(valid):
                return -1

            best = int(np.argmin(np.where(valid, d2, np.inf)))
            return int(self._sprites.world_indices[best])

        def _xz_from_ray(
            self, sx: int, sy: int, plane_y: float
        ) -> "Optional[Tuple[float, float]]":
            """
            Intersect the camera ray through pixel (sx, sy) with the
            horizontal plane Y = plane_y.  Returns (wx, wz) or None if
            the ray is nearly parallel to the plane or hits from behind.
            """
            w = self.winfo_width()
            h = self.winfo_height()
            if w < 1 or h < 1:
                return None

            aspect      = w / h
            ray_o, ray_d = self._camera.unproject(sx, sy, w, h, aspect)

            dy = float(ray_d[1])
            if abs(dy) < 1e-6:
                return None    # Ray nearly horizontal
            t = (plane_y - float(ray_o[1])) / dy
            if t < 0.0:
                return None    # Plane is behind the camera
            wx = float(ray_o[0]) + t * float(ray_d[0])
            wz = float(ray_o[2]) + t * float(ray_d[2])
            return wx, wz

        def _ray_place(self, sx: int, sy: int) -> None:
            """
            Stage 1 — click-to-place.

            Casts a ray through pixel (sx, sy) against all non-skybox BSP
            meshes.  On the closest hit, fires _on_place_xyz_cb(wx, wy, wz)
            when available so the editor can preserve the exact clicked
            height.  Falls back to _on_place_cb(wx, wz) for old callers.
            Does nothing if no surface is hit.
            """
            if (not self._ready
                    or (not self._on_place_cb and not self._on_place_xyz_cb)):
                return
            w = self.winfo_width()
            h = self.winfo_height()
            if w < 1 or h < 1:
                return

            aspect      = w / h
            ray_o, ray_d = self._camera.unproject(sx, sy, w, h, aspect)

            best_t:   float = float("inf")
            best_hit: Optional[np.ndarray] = None

            for gm in self._mesh_cache._cache.values():
                if (gm is None
                        or gm.is_empty()
                        or gm.tri_positions is None
                        or gm.helper_role is not None
                        or gm.category == "skybox"):
                    continue
                result = raycast_mesh_array(gm.tri_positions, ray_o, ray_d)
                if result is not None:
                    t, hit = result
                    if t < best_t:
                        best_t   = t
                        best_hit = hit

            if best_hit is not None:
                game_hit = display_to_game_point(best_hit)
                wx = float(game_hit[0])
                wy = float(game_hit[1])
                wz = float(game_hit[2])
                if self._on_place_xyz_cb:
                    self._on_place_xyz_cb(wx, wy, wz)
                elif self._on_place_cb:
                    self._on_place_cb(wx, wz)

        # ------------------------------------------------------------------
        # Input — orbit / pan / fly / place / drag
        # ------------------------------------------------------------------

        def focus_for_input(self) -> None:
            """
            Give the GL canvas keyboard focus.

            Tk sends KeyPress events only to the focused widget, and on
            Windows mouse-wheel events also commonly follow focus rather than
            the pointer.  The 3-D view is meant to behave like a viewport, so
            entering or showing it should make it the input target.
            """
            try:
                self.focus_set()
            except tk.TclError:
                pass

        def _on_enter(self, _e: tk.Event) -> None:
            self.focus_for_input()

        def _on_rmb(self, _e: tk.Event) -> None:
            """Right-click: cancel place mode."""
            self.focus_for_input()
            if self._place_mode:
                self._place_mode = False
                try:
                    self.config(cursor="")
                except Exception:
                    pass

        def _on_lmb_down(self, e: tk.Event) -> None:
            self.focus_for_input()
            self._flush_transform_commit()
            self._3d_drag_index = -1   # clear any stale drag state
            self._last_mx = self._drag_start_mx = e.x
            self._last_my = self._drag_start_my = e.y
            alt = bool(e.state & 0x20000)

            if self._camera.mode == "fly":
                self._drag_mode = "fly_look"
                return

            if self._place_mode:
                self._drag_mode = None   # No camera movement while placing
                return

            # Check for sprite drag initiation (fast CPU test — no GL round-trip)
            if (self._on_move_cb or self._on_move_xyz_cb) and self._sprites:
                hit_idx = self._screen_hit_sprite(e.x, e.y)
                if hit_idx >= 0:
                    orig = self._get_sprite_xyz_for_index(hit_idx)
                    if orig is None:
                        self._drag_mode = "pan" if alt else "orbit"
                        return
                    self.set_selected_index(hit_idx)
                    self._3d_drag_orig_xyz = orig
                    self._3d_drag_moved = False
                    self._3d_drag_index   = hit_idx
                    self._3d_drag_plane_y = float(orig[1])
                    self._3d_drag_wx = float(orig[0])
                    self._3d_drag_wz = float(orig[2])
                    self._drag_mode = "3d_drag"
                    if self._on_select_cb:
                        self._on_select_cb(hit_idx)
                    return

            self._drag_mode = "pan" if alt else "orbit"

        def _on_lmb_up(self, e: tk.Event) -> None:
            dx         = abs(e.x - self._drag_start_mx)
            dy         = abs(e.y - self._drag_start_my)
            small_move = dx < 5 and dy < 5

            # Place mode: left-click → ray cast → fire on_place callback
            if self._place_mode and small_move:
                self._ray_place(e.x, e.y)
                self._drag_mode = None
                return

            # 3-D drag: commit on big move; treat small move as selection click
            if self._3d_drag_index >= 0:
                if not small_move and (self._on_move_cb or self._on_move_xyz_cb):
                    if not self._3d_drag_moved:
                        result = self._xz_from_ray(
                            e.x, e.y, self._3d_drag_plane_y)
                        if result is not None:
                            self._3d_drag_wx, self._3d_drag_wz = result
                            self._3d_drag_moved = True
                    if self._3d_drag_moved:
                        game_xyz = display_to_game_point((
                            self._3d_drag_wx,
                            self._3d_drag_plane_y,
                            self._3d_drag_wz,
                        ))
                        if self._on_move_xyz_cb is not None:
                            self._on_move_xyz_cb(
                                self._3d_drag_index,
                                float(game_xyz[0]),
                                float(game_xyz[1]),
                                float(game_xyz[2]),
                            )
                        elif self._on_move_cb is not None:
                            self._on_move_cb(
                                self._3d_drag_index,
                                float(game_xyz[0]),
                                float(game_xyz[2]),
                            )
                    else:
                        self._restore_3d_drag_position()
                else:
                    self._restore_3d_drag_position()
                    self._request_render()
                self._3d_drag_orig_xyz = None
                self._3d_drag_index = -1
                self._drag_mode     = None
                self._3d_drag_moved = False
                return

            # Default: small move outside place/drag → colour-buffer sprite pick
            if small_move and self._on_select_cb and self._camera.mode != "fly":
                idx = self._pick(e.x, e.y)
                if idx >= 0:
                    self.set_selected_index(idx)
                    self._on_select_cb(idx)
            self._drag_mode = None

        def _on_mmb_down(self, e: tk.Event) -> None:
            self.focus_for_input()
            self._last_mx = e.x
            self._last_my = e.y
            self._drag_mode = "pan"

        def _on_mmb_up(self, _e) -> None:
            self._drag_mode = None

        def _on_mouse_drag(self, e: tk.Event) -> None:
            dx = e.x - self._last_mx
            dy = e.y - self._last_my
            self._last_mx = e.x
            self._last_my = e.y
            if not dx and not dy:
                return
            if self._drag_mode == "orbit":
                self._camera.orbit(dx, dy)
                self._request_render()
            elif self._drag_mode == "pan":
                self._camera.pan(dx, dy)
                self._request_render()
            elif self._drag_mode == "fly_look":
                self._camera.fly_rotate(dx, dy)
                self._request_render()
            elif self._drag_mode == "3d_drag":
                result = self._xz_from_ray(
                    e.x, e.y, self._3d_drag_plane_y)
                if result is not None:
                    self._3d_drag_wx, self._3d_drag_wz = result
                    xyz = np.array(
                        [self._3d_drag_wx, self._3d_drag_plane_y, self._3d_drag_wz],
                        dtype=np.float32,
                    )
                    self._set_sprite_xyz_for_index(
                        self._3d_drag_index, xyz, update_object=True)
                    self._3d_drag_moved = True
                    self._request_render()

        def _on_wheel(self, e: tk.Event) -> None:
            self.focus_for_input()
            num = getattr(e, "num", None)
            delta = getattr(e, "delta", 0)
            scroll_up = (num == 4 or delta > 0)
            shift = bool(getattr(e, "state", 0) & 0x0001)

            # Shift+scroll — elevate the selected object along world Y.
            # Each notch moves 25 world units (≈ half a standing NPC height).
            if (self._camera.mode == "orbit"
                    and shift
                    and self._selected_index >= 0
                    and self._sprites is not None
                    and (self._on_move_xyz_cb is not None
                         or self._on_elevate_cb is not None)):
                step = 25.0
                sprites = self._sprites
                try:
                    vbo_idx = sprites.world_indices.index(self._selected_index)
                except ValueError:
                    pass   # sprite not in VBO — fall through to normal zoom
                else:
                    xyz = sprites.positions[vbo_idx].copy()   # (3,) float32
                    xyz[1] += step if scroll_up else -step
                    self._set_sprite_xyz_for_index(
                        self._selected_index, xyz, update_object=True)
                    if self._on_move_xyz_cb is not None:
                        self._schedule_move_commit_xyz(self._selected_index, xyz)
                    else:
                        self._schedule_elevation_commit(
                            self._selected_index, float(xyz[1]))
                    self._request_render()
                    return   # consumed — don't zoom camera

            if self._camera.mode == "fly":
                speed_multiplier = 5.0 if shift else 1.0
                distance = self._camera.fly_speed * _FLY_WHEEL_SECONDS
                self._camera.fly_dolly(
                    distance * speed_multiplier * (1.0 if scroll_up else -1.0)
                )
            elif scroll_up:
                self._camera.zoom(1.15)
            else:
                self._camera.zoom(1.0 / 1.15)
            self._request_render()

        def _on_global_wheel(self, e: tk.Event) -> None:
            """
            Route wheel events to the viewport when Tk sends them elsewhere.

            Windows commonly dispatches wheel events to the focused widget,
            not necessarily the widget under the pointer.  Skip events already
            delivered to this canvas so the zoom step is not applied twice.
            """
            widget = getattr(e, "widget", None)
            if widget is self:
                return
            if widget is None or not hasattr(widget, "winfo_toplevel"):
                return
            if widget.winfo_toplevel() != self.winfo_toplevel():
                return
            if not self._event_is_over_canvas():
                return
            self._on_wheel(e)

        def _event_is_over_canvas(self) -> bool:
            if not self.winfo_viewable():
                return False
            try:
                px, py = self.winfo_pointerxy()
                x0 = self.winfo_rootx()
                y0 = self.winfo_rooty()
                return (x0 <= px < x0 + self.winfo_width()
                        and y0 <= py < y0 + self.winfo_height())
            except tk.TclError:
                return False

        def _on_key_down(self, e: tk.Event) -> None:
            if not self._accept_key_event(e):
                return
            k = e.keysym.lower()
            if k == "f":
                self._refit()
                return "break"
            if self._handle_orbit_nudge_key(e):
                return "break"
            fly_key = k in {
                "w", "a", "s", "d", "q", "e", "shift_l", "shift_r",
            }
            if self._camera.mode == "fly" and fly_key:
                self._fly_keys.add(k)
                if self._fly_after is None and self._fly_keys:
                    self._fly_last_tick = None
                    self._fly_tick()
                return "break"
            return None

        def _on_key_up(self, e: tk.Event) -> None:
            if not self.winfo_viewable():
                return
            key = e.keysym.lower()
            was_active = key in self._fly_keys
            self._fly_keys.discard(key)
            if not self._fly_keys:
                self._cancel_fly_tick()
            return "break" if was_active else None

        def _on_focus_out(self, _e: tk.Event) -> None:
            self._flush_transform_commit()
            self._fly_keys.clear()
            self._cancel_fly_tick()

        def _cancel_fly_tick(self) -> None:
            if self._fly_after is not None:
                try:
                    self.after_cancel(self._fly_after)
                except tk.TclError:
                    pass
                self._fly_after = None
            self._fly_last_tick = None

        def _focus_is_in_viewport(self, widget) -> bool:
            """Return whether *widget* is this canvas or one of its children."""
            current = widget
            while current is not None:
                if current is self:
                    return True
                current = getattr(current, "master", None)
            return False

        def _accept_key_event(self, e: tk.Event) -> bool:
            """Return True when a key event should drive the 3-D viewport."""
            if not self.winfo_viewable():
                return False

            # Direct canvas bindings are authoritative even on pyopengltk
            # builds whose focus reporting is unreliable.
            try:
                focus = self.focus_get()
            except tk.TclError:
                focus = None
            return _should_accept_viewport_key(
                e.keysym,
                direct_to_canvas=getattr(e, "widget", None) is self,
                focus_known=focus is not None,
                focus_in_viewport=(
                    self._focus_is_in_viewport(focus)
                    if focus is not None
                    else False
                ),
                # Tk can transiently report no focused widget. In that narrow
                # case, the global fallback is accepted only under the pointer.
                pointer_over_canvas=(
                    self._event_is_over_canvas()
                    if focus is None
                    else False
                ),
            )

        def _fly_tick(self) -> None:
            """Advance fly-camera movement using actual elapsed timer time."""
            if self._camera.mode != "fly" or not self._fly_keys:
                self._fly_after = None
                self._fly_last_tick = None
                return
            now = time.perf_counter()
            dt = _fly_elapsed_seconds(self._fly_last_tick, now)
            self._fly_last_tick = now
            fwd   = (1 if "w" in self._fly_keys else 0) - (1 if "s" in self._fly_keys else 0)
            right = (1 if "d" in self._fly_keys else 0) - (1 if "a" in self._fly_keys else 0)
            up    = (1 if "e" in self._fly_keys else 0) - (1 if "q" in self._fly_keys else 0)
            fast  = "shift_l" in self._fly_keys or "shift_r" in self._fly_keys
            if fwd or right or up:
                orig = self._camera.fly_speed
                self._camera.fly_speed = orig * (5.0 if fast else 1.0)
                try:
                    self._camera.fly_move(float(fwd), float(right), float(up), dt)
                finally:
                    self._camera.fly_speed = orig
                self._request_render()
            if self._fly_keys:
                self._fly_after = self.after(16, self._fly_tick)
            else:
                self._fly_after = None

        def _refit(self) -> None:
            if self._fit_camera_to_level():
                self._request_render()

        def set_camera_mode(self, mode: str) -> None:
            """Change camera mode and stop movement that no longer applies."""
            if mode != "fly":
                self._fly_keys.clear()
                self._cancel_fly_tick()
            self._camera.set_mode(mode)
            self._request_render()


# ---------------------------------------------------------------------------
# Public widget
# ---------------------------------------------------------------------------

class View3D(tk.Frame if _HAS_TK else object):
    """
    3-D level viewer and object placement surface.

    Parameters
    ----------
    parent    : tk parent widget
    on_select : Callable[[int], None]
        Fired with world_index when the user clicks an object sprite.
    on_place  : Callable[[float, float], None]
        Fired with (wx, wz) when the user left-clicks the BSP in place mode.
        Legacy fallback when on_place_xyz is not supplied.
    on_place_xyz : Callable[[float, float, float], None]
        Fired with the exact BSP hit point (wx, wy, wz) when supplied.
        Prefer this for 3-D placement so clicked height is preserved.
    on_move   : Callable[[int, float, float], None]
        Fired with (world_index, wx, wz) when the user drag-commits a sprite
        to a new XZ position. Legacy fallback when on_move_xyz is not supplied.
    on_move_xyz : Callable[[int, float, float, float], None]
        Fired with (world_index, wx, wy, wz) for exact 3-D movement when
        supplied.  This preserves object height during 3-D drags and nudges.
    on_rotate : Callable[[int, tuple], None]
        Fired with (world_index, rotation_tuple) when the user rotates the
        selected object around world Y in the 3-D viewport.
    on_elevate : Callable[[int, float], None]
        Fallback fired with (world_index, new_y) when a vertical preview
        transform is committed and no exact XYZ move callback is supplied.
    """

    def __init__(
        self,
        parent:       "tk.Misc",
        on_select:    Optional[Callable[[int], None]] = None,
        on_place:     Optional[Callable[[float, float], None]] = None,
        on_place_xyz: Optional[Callable[[float, float, float], None]] = None,
        on_move:      Optional[Callable[[int, float, float], None]] = None,
        on_move_xyz:  Optional[Callable[[int, float, float, float], None]] = None,
        on_rotate:    Optional[Callable[[int, tuple], None]] = None,
        on_elevate:   Optional[Callable[[int, float], None]] = None,
        textures_dir: Optional[str] = None,
        skins_dir:    Optional[str] = None,
        models_dir:   Optional[str] = None,
        actor_visuals: Optional[dict] = None,
        world_helper_metadata: Optional[dict] = None,
    ) -> None:
        if _HAS_TK:
            super().__init__(parent, bg="#0e1116")
        self.on_select     = on_select
        self.on_place      = on_place
        self.on_place_xyz  = on_place_xyz
        self.on_move       = on_move
        self.on_move_xyz   = on_move_xyz
        self.on_rotate     = on_rotate
        self.on_elevate    = on_elevate
        self._level        = None
        self._textures_dir = textures_dir
        self._skins_dir    = skins_dir
        self._models_dir   = models_dir
        self._actor_visuals = actor_visuals or {}
        self._world_helper_metadata = world_helper_metadata or {}

        if not OPENGL_AVAILABLE:
            self._inner = _PlaceholderView(self, _MISSING)
            self._inner.pack(fill="both", expand=True)
            return

        self._build_ui()

    def _build_ui(self) -> None:
        """Build the toolbar, GL canvas, and status bar."""
        # ── Toolbar ──────────────────────────────────────────────────────
        bar = tk.Frame(self, bg="#15171b", height=28)
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)

        tk.Label(bar, text="Camera:", bg="#15171b", fg="#aaaaaa",
                 font=("Segoe UI", 8)).pack(side="left", padx=(8, 4))

        self._mode_var = tk.StringVar(value="orbit")

        def _set_orbit():
            self._mode_var.set("orbit")
            self._canvas.set_camera_mode("orbit")
            self._update_mode_buttons()
            self._canvas.focus_for_input()

        def _set_fly():
            self._mode_var.set("fly")
            self._canvas.set_camera_mode("fly")
            self._update_mode_buttons()
            self._canvas.focus_for_input()

        self._btn_orbit = tk.Button(
            bar, text="Orbit",
            bg="#2c5e8a", fg="white",
            activebackground="#3a78ad",
            relief="flat", font=("Segoe UI", 8),
            command=_set_orbit,
        )
        self._btn_orbit.pack(side="left", padx=(0, 2), pady=3)

        self._btn_fly = tk.Button(
            bar, text="Fly",
            bg="#23272d", fg="#aaaaaa",
            activebackground="#2c5e8a",
            relief="flat", font=("Segoe UI", 8),
            command=_set_fly,
        )
        self._btn_fly.pack(side="left", padx=(0, 12), pady=3)

        # ── GL canvas ─────────────────────────────────────────────────────
        self._canvas: _GLCanvas = _GLCanvas(self, width=800, height=600)
        self._canvas._on_select_cb   = self._on_select
        self._canvas._on_status_cb   = self._on_status_update
        self._canvas._on_place_cb    = self._on_place_fired
        self._canvas._on_place_xyz_cb = self._on_place_xyz_fired
        self._canvas._on_move_cb     = self._on_move_fired
        self._canvas._on_move_xyz_cb = self._on_move_xyz_fired
        self._canvas._on_rotate_cb   = self._on_rotate_fired
        self._canvas._on_elevate_cb  = self._on_elevate_fired
        self._canvas._textures_dir  = self._textures_dir   # passed to initgl
        self._canvas._skins_dir     = self._skins_dir      # passed to initgl
        self._canvas._models_dir    = self._models_dir     # passed to initgl
        self._canvas._actor_visuals = self._actor_visuals
        self._canvas._world_helper_metadata = self._world_helper_metadata
        self._canvas.pack(fill="both", expand=True)

        # ── Status bar ────────────────────────────────────────────────────
        self._status = tk.Label(
            self, anchor="w",
            bg="#1a1d22", fg="#888888",
            font=("Consolas", 8),
            padx=8,
        )
        self._status.pack(side="bottom", fill="x")
        self._status.config(text="No level loaded")

    def _update_mode_buttons(self) -> None:
        """Highlight the active camera-mode button."""
        if self._mode_var.get() == "orbit":
            self._btn_orbit.config(bg="#2c5e8a", fg="white")
            self._btn_fly.config(bg="#23272d",   fg="#aaaaaa")
        else:
            self._btn_orbit.config(bg="#23272d",  fg="#aaaaaa")
            self._btn_fly.config(bg="#2c5e8a",    fg="white")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_active_level(self, level_edit) -> None:
        """
        Load a LevelEdit into the 3-D view.

        BSP geometry is obtained from level_edit.get_bsp() which works for
        both loose .DAT files and REZ-sourced levels (no re-extraction needed).
        """
        if not OPENGL_AVAILABLE:
            return
        load_profile = _new_load_profile(level_edit)
        self._level = level_edit
        if level_edit is None:
            self._canvas.load_level(None, None, [], load_profile)
            self._status.config(text="No level loaded")
            return

        # preview_bsp() adds pending cloned physical doors to the cached BSP.
        bsp_world = None
        stage_started = time.perf_counter()
        try:
            bsp_world = level_edit.preview_bsp()
        except Exception as exc:
            print(f"[view3d] BSP parse failed for {level_edit.display_name!r}: "
                  f"{exc}", file=sys.stderr)
        _mark_load_stage(load_profile, "preview_bsp", stage_started)

        stage_started = time.perf_counter()
        mat = (
            level_edit.editor_materialize()
            if hasattr(level_edit, "editor_materialize")
            else level_edit.materialize()
        )
        _mark_load_stage(load_profile, "materialize", stage_started)
        self._canvas.load_level(
            level_edit,
            bsp_world,
            mat.objects,
            load_profile,
        )
        self._status.config(
            text=f"{level_edit.display_name}  ·  loading 3-D view…")

    def refresh(self) -> None:
        """Reload object sprites after ops have changed the level state."""
        if not OPENGL_AVAILABLE or self._level is None:
            return
        self._canvas._flush_transform_commit()
        mat = (
            self._level.editor_materialize()
            if hasattr(self._level, "editor_materialize")
            else self._level.materialize()
        )
        try:
            bsp_world = self._level.preview_bsp()
        except Exception as exc:
            print(f"[view3d] BSP preview failed for {self._level.display_name!r}: "
                  f"{exc}", file=sys.stderr)
            bsp_world = self._level.get_bsp()
        self._canvas.reload_level_state(bsp_world, mat.objects)

    def flush_pending_transforms(self) -> None:
        """Commit any debounced preview transform immediately."""
        if not OPENGL_AVAILABLE:
            return
        self._canvas._flush_transform_commit()

    def select_by_index(self, world_index: int) -> None:
        """Highlight a WorldObject (white ring)."""
        if not OPENGL_AVAILABLE:
            return
        self._canvas.set_selected_index(world_index)

    def set_place_mode(self, on: bool) -> None:
        """
        Enable or disable 3-D place mode.

        While active, a left-click on the BSP geometry casts a ray, finds
        the closest surface hit, and fires ``on_place(wx, wz)``.  The cursor
        changes to a crosshair to signal the mode.  Right-click or another
        call with ``on=False`` cancels.
        """
        if not OPENGL_AVAILABLE:
            return
        self._canvas._place_mode = on
        try:
            self._canvas.config(cursor="crosshair" if on else "")
        except Exception:
            pass

    def set_show_object_helper_billboards(self, enabled: bool) -> None:
        """Toggle billboards for objects that already render as 3-D models."""
        if not OPENGL_AVAILABLE:
            return
        self._canvas.set_show_object_helper_billboards(enabled)

    def set_show_world_helper_billboards(self, enabled: bool) -> None:
        """Toggle editor service/world helper billboards."""
        if not OPENGL_AVAILABLE:
            return
        self._canvas.set_show_world_helper_billboards(enabled)

    def set_show_helper_billboards(self, enabled: bool) -> None:
        """Backward-compatible alias for object helper billboards."""
        self.set_show_object_helper_billboards(enabled)

    def set_helper_bsp_mode(self, mode: str) -> None:
        """Set collision/helper BSP preview mode."""
        if not OPENGL_AVAILABLE:
            return
        self._canvas.set_helper_bsp_mode(mode)

    def set_helper_role_groups(self, groups) -> None:
        """Set visible helper BSP role groups."""
        if not OPENGL_AVAILABLE:
            return
        self._canvas.set_helper_role_groups(groups)

    def set_camera_mode(self, mode: str) -> None:
        """Switch between 'orbit' and 'fly' programmatically."""
        if not OPENGL_AVAILABLE:
            return
        self._mode_var.set(mode)
        self._canvas.set_camera_mode(mode)
        self._update_mode_buttons()
        self._canvas.focus_for_input()

    def update_asset_directories(self, textures_dir: Optional[str] = None,
                                 skins_dir: Optional[str] = None,
                                 models_dir: Optional[str] = None) -> None:
        """Update cache directories and rebuild texture/model caches if they changed."""
        if not OPENGL_AVAILABLE:
            return
        if textures_dir is not None and textures_dir != self._textures_dir:
            self._textures_dir = textures_dir
            self._canvas._textures_dir = textures_dir
            if self._canvas._ready:
                try:
                    from view3d.dtx import TextureCache
                    self._canvas._tex_cache = TextureCache(textures_dir)
                except Exception as exc:
                    print(f"[view3d] textures cache update failed: {exc}", file=sys.stderr)
        if skins_dir is not None and skins_dir != self._skins_dir:
            self._skins_dir = skins_dir
            self._canvas._skins_dir = skins_dir
            if self._canvas._ready:
                try:
                    from view3d.dtx import TextureCache
                    self._canvas._skin_cache = TextureCache(skins_dir)
                except Exception as exc:
                    print(f"[view3d] skins cache update failed: {exc}", file=sys.stderr)
        if models_dir is not None and models_dir != self._models_dir:
            self._models_dir = models_dir
            self._canvas._models_dir = models_dir
            if self._canvas._ready:
                try:
                    from view3d.gl_object_models import ObjectModelCache
                    self._canvas._obj_model_cache = ObjectModelCache(models_dir)
                except Exception as exc:
                    print(f"[view3d] models cache update failed: {exc}", file=sys.stderr)

    def update_actor_visuals(self, actor_visuals: Optional[dict]) -> None:
        """Replace per-object actor visuals before loading the active level."""
        self._actor_visuals = actor_visuals or {}
        if OPENGL_AVAILABLE:
            self._canvas._actor_visuals = self._actor_visuals

    def focus_for_input(self) -> None:
        """Focus the GL canvas so keyboard and wheel controls reach it."""
        if not OPENGL_AVAILABLE:
            return
        self._canvas.focus_for_input()

    # ------------------------------------------------------------------
    # Internal callbacks
    # ------------------------------------------------------------------

    def _on_select(self, world_index: int) -> None:
        if self.on_select:
            self.on_select(world_index)

    def _on_place_fired(self, wx: float, wz: float) -> None:
        """Relay the on_place callback fired by _GLCanvas._ray_place()."""
        if self.on_place:
            self.on_place(wx, wz)

    def _on_place_xyz_fired(self, wx: float, wy: float, wz: float) -> None:
        """Relay the exact 3-D hit callback fired by _GLCanvas._ray_place()."""
        if self.on_place_xyz:
            self.on_place_xyz(wx, wy, wz)
        elif self.on_place:
            self.on_place(wx, wz)

    def _on_move_fired(self, world_index: int, wx: float, wz: float) -> None:
        """Relay the on_move callback fired by _GLCanvas on 3-D drag commit."""
        if self.on_move:
            self.on_move(world_index, wx, wz)

    def _on_move_xyz_fired(
        self, world_index: int, wx: float, wy: float, wz: float
    ) -> None:
        """Relay the exact 3-D move callback fired by _GLCanvas."""
        if self.on_move_xyz:
            self.on_move_xyz(world_index, wx, wy, wz)
        elif self.on_move:
            self.on_move(world_index, wx, wz)

    def _on_rotate_fired(self, world_index: int, rotation: tuple) -> None:
        """Relay the 3-D yaw rotation callback fired by _GLCanvas."""
        if self.on_rotate:
            self.on_rotate(world_index, rotation)

    def _on_elevate_fired(self, world_index: int, new_y: float) -> None:
        """Relay fallback vertical commits fired by _GLCanvas."""
        if self.on_elevate:
            self.on_elevate(world_index, new_y)

    def _on_status_update(self, text: str) -> None:
        """Receive status text from _GLCanvas after each render."""
        try:
            self._status.config(text=text)
        except tk.TclError:
            pass  # widget may have been destroyed
