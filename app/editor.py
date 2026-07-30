#!/usr/bin/env python3
"""
app/editor.py
=============

3-D placement editor for Might and Magic IX worlds.

Usage:
    python mm9_editor.py [--catalog catalog/data/catalog.json]

Layout:

    +---------------------------------------------------------------------+
    | File   View   Help                                                  |
    +---------------------------------------------------------------------+
    | [Open from WORLDS.REZ…] [Save…]  Levels: [BOOTCAMP.DAT ▼]          |
    +-----------------+-----------------------------+--------------------+
    |                 |                             |                    |
    |  Catalog        |        3-D View             |  Properties        |
    |                 |                             |                    |
    |  o Classes      |       (canvas)              |  Name:    [...]    |
    |  o Models       |                             |  Pos:     [...]    |
    |                 |                             |  Rotation:[...]    |
    |  [filter]       |                             |  ...               |
    |  [...list...]   |                             |                    |
    |  [Place →]      |                             |  [Delete]          |
    +-----------------+-----------------------------+--------------------+
    |  status bar                                                         |
    +---------------------------------------------------------------------+
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import struct
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence

EDITOR_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# tkinter is imported lazily inside main() so config validation can produce
# readable console errors even on machines where Tk isn't available yet.
tk = None  # type: ignore

import _path_setup  # noqa: F401
import mm9_patch as patcher
from core import autodetect
from core import project as P
from core import project_io
from features.doors import clone as door_clone
from features.doors import links as door_links
from features.dat_editing import compiler_strategy as dat_compiler_strategy
from features.dat_editing import gltf_export as dat_gltf_export
from features.dat_editing import legacy_ed as dat_legacy_ed
from features.dat_editing import terrain_reconstruction
from features.dat_editing import terrain_semantics
from features.prefabs import import_static as prefab_import
from features.prefabs import inspector as prefab_inspector
from catalog import build_catalog_from_rez, load_catalog, save_catalog
from features.presets.manager import PresetStore

# These imports pull in tkinter; they're deferred to _import_gui() below.
CatalogPanel = PropertiesPanel = SaveDialog = LommConversionDialog = None  # type: ignore
View3D = None          # type: ignore
OPENGL_AVAILABLE = False
_view3d_missing: list = []   # packages still needed; populated by _import_gui()


DAT_TO_ED_DEFAULT_TERRAIN_SUPPORT_RADIUS = 4096.0
DAT_TO_ED_PROCESSOR_BRUSH_BUDGET = 1500
DAT_TO_ED_PROCESSOR_POLYGON_BUDGET = 12000
DAT_TO_ED_ANSKRAMKEEP_BACK_START_POINT = (0.0, -104.0, 16.0)
DAT_TO_ED_TERRAIN_SUPPORT_SELECTION_MODE_BY_LEVEL = {
    "ISLEOFASHES": "multi_anchor_budget",
}


def _ask_prefab_collision_options(parent, title: str = "Prefab Collision") -> Optional[Dict[str, Any]]:
    """Return collision import options, or None if the user cancels."""
    win = tk.Toplevel(parent)
    win.title(title)
    win.configure(bg="#11151c")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()

    result: Dict[str, Any] = {}
    mode_var = tk.StringVar(value="box_approx")
    thickness_var = tk.StringVar(value="8")
    segment_var = tk.StringVar(value="512")

    outer = tk.Frame(win, bg="#11151c")
    outer.pack(fill="both", expand=True, padx=14, pady=12)

    tk.Label(
        outer,
        text="Collision helper",
        bg="#11151c",
        fg="#dde3ea",
        font=("Segoe UI", 10, "bold"),
    ).pack(anchor="w")

    options = []
    if "mesh" in str(title or "").lower():
        options.append(("Per-face InvisibleBrush slabs", "face_slabs"))
    options.extend([
        ("Thin InvisibleBrush box (recommended)", "box_approx"),
        ("No collision helper", "none"),
        ("Duplicate prefab geometry (diagnostic)", "invisible_bsp"),
    ])
    for label, value in options:
        tk.Radiobutton(
            outer,
            text=label,
            value=value,
            variable=mode_var,
            bg="#11151c",
            fg="#dde3ea",
            selectcolor="#23272d",
            activebackground="#11151c",
            activeforeground="#ffffff",
            anchor="w",
        ).pack(anchor="w", pady=(8 if value == "face_slabs" else 2, 0))

    row = tk.Frame(outer, bg="#11151c")
    row.pack(fill="x", pady=(12, 0))
    tk.Label(row, text="Box thickness", bg="#11151c", fg="#aeb7c2").pack(side="left")
    entry = tk.Entry(row, textvariable=thickness_var, width=8, bg="#1b2028", fg="#dde3ea", insertbackground="#dde3ea")
    entry.pack(side="left", padx=(10, 4))
    tk.Label(row, text="units", bg="#11151c", fg="#aeb7c2").pack(side="left")

    segment_row = tk.Frame(outer, bg="#11151c")
    segment_row.pack(fill="x", pady=(8, 0))
    tk.Label(segment_row, text="Max segment length", bg="#11151c", fg="#aeb7c2").pack(side="left")
    segment_entry = tk.Entry(segment_row, textvariable=segment_var, width=8, bg="#1b2028", fg="#dde3ea", insertbackground="#dde3ea")
    segment_entry.pack(side="left", padx=(10, 4))
    tk.Label(segment_row, text="units", bg="#11151c", fg="#aeb7c2").pack(side="left")

    def _sync_state(*_args) -> None:
        state = "normal" if mode_var.get() in {"box_approx", "face_slabs"} else "disabled"
        entry.configure(state=state)
        segment_entry.configure(state=state)

    mode_var.trace_add("write", _sync_state)
    _sync_state()

    buttons = tk.Frame(outer, bg="#11151c")
    buttons.pack(fill="x", pady=(14, 0))

    def _ok() -> None:
        try:
            thickness = float(thickness_var.get())
        except ValueError:
            messagebox.showerror("Prefab collision", "Box thickness must be a number.", parent=win)
            return
        try:
            segment_length = float(segment_var.get())
        except ValueError:
            messagebox.showerror("Prefab collision", "Max segment length must be a number.", parent=win)
            return
        if thickness < 1.0 or thickness > 512.0:
            messagebox.showerror("Prefab collision", "Box thickness must be between 1 and 512.", parent=win)
            return
        if segment_length < 64.0 or segment_length > 8192.0:
            messagebox.showerror("Prefab collision", "Max segment length must be between 64 and 8192.", parent=win)
            return
        result["collision_mode"] = mode_var.get()
        result["collision_thickness"] = thickness
        result["collision_segment_length"] = segment_length
        win.destroy()

    def _cancel() -> None:
        win.destroy()

    tk.Button(buttons, text="Cancel", command=_cancel).pack(side="right", padx=(8, 0))
    tk.Button(buttons, text="OK", command=_ok).pack(side="right")

    win.protocol("WM_DELETE_WINDOW", _cancel)
    parent.wait_window(win)
    return result or None


def _import_gui():
    """Import all the GUI-dependent modules. Called only after config
    validation passes, so console errors don't get masked by missing-Tk."""
    global tk, CatalogPanel, PropertiesPanel, SaveDialog, LommConversionDialog
    global filedialog, messagebox, simpledialog, ttk
    global EditPresetDialog, ManagePresetsDialog
    global View3D, OPENGL_AVAILABLE, _view3d_missing
    import tkinter as _tk
    from tkinter import filedialog as _fd, messagebox as _mb, simpledialog as _sd, ttk as _ttk
    from ui.catalog_panel import CatalogPanel as _CP
    from ui.properties_panel import PropertiesPanel as _PP
    from ui.diff_panel import SaveDialog as _SD
    from ui.preset_dialog import EditPresetDialog as _EPD, ManagePresetsDialog as _MPD
    from ui.lomm_conversion_dialog import LommConversionDialog as _LCD
    tk = _tk
    filedialog = _fd; messagebox = _mb; simpledialog = _sd; ttk = _ttk
    CatalogPanel = _CP; PropertiesPanel = _PP; SaveDialog = _SD
    LommConversionDialog = _LCD
    EditPresetDialog = _EPD; ManagePresetsDialog = _MPD
    try:
        from view3d import View3D as _V3D, OPENGL_AVAILABLE as _OGL
        View3D = _V3D
        OPENGL_AVAILABLE = _OGL
        if not OPENGL_AVAILABLE:
            try:
                from view3d.gl_view import _MISSING
                _view3d_missing = list(_MISSING)
            except Exception:
                _view3d_missing = ["GL context"]
    except Exception as _e:
        print(f"[3D view] import error: {_e}", file=sys.stderr)
        View3D = None
        OPENGL_AVAILABLE = False
        _view3d_missing = ["import failed — see console"]


class EditorApp:
    def __init__(self, root: tk.Tk, catalog: Dict[str, Any],
                 paths: Any, catalog_path: Optional[str] = None):
        self.root = root
        self.catalog = catalog
        self.catalog_path = catalog_path
        self.cfg = paths          # GamePaths — kept as self.cfg for compat
        self.resources = paths.resources()

        self.project = P.Project(
            rude_rez_path = paths.archive_path("rude") if paths.has_archive("rude") else None,
            work_dir    = getattr(paths, "work_dir",    None),
            backup_root = getattr(paths, "backup_root", None),
        )
        suggested_npc = self._suggest_next_npc_nbr()
        if suggested_npc is not None:
            self.project.next_npc_nbr = suggested_npc

        # User preset store — loads (or creates) user_presets.json in the
        # editor directory so presets survive across sessions.
        _editor_dir = getattr(paths, "editor_dir", None) or EDITOR_ROOT
        self.editor_dir = _editor_dir
        self.settings_path = os.path.join(_editor_dir, "editor_settings.json")
        self.editor_settings = self._load_editor_settings()
        self.preset_store = PresetStore(
            os.path.join(_editor_dir, "user_presets.json"))
        self.preset_store.load()

        root.title("MM9 Mod Editor")
        root.configure(bg="#0e1116")
        root.geometry("1500x900")
        self._selected_world_index: Optional[int] = None

        self._build_menu()
        self._build_layout()
        self._build_bindings()

    # ---------- layout ----------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        m_file = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=m_file)
        m_file.add_command(label="Open Level from WORLDS.REZ…", accelerator="Ctrl+O",
                           command=self.cmd_open_worlds_rez)
        m_file.add_separator()
        m_file.add_command(label="Save…",                 accelerator="Ctrl+S",
                           command=self.cmd_save)
        m_file.add_command(label="Install Output to Game…",
                           command=self.cmd_install_output)
        m_file.add_command(label="Restore Installed Backup…",
                           command=self.cmd_restore_backup)
        m_file.add_separator()
        m_file.add_command(label="Save Project…",        accelerator="Ctrl+Shift+S",
                           command=self.cmd_save_project)
        m_file.add_command(label="Open Project…",        accelerator="Ctrl+Shift+O",
                           command=self.cmd_open_project)
        m_file.add_separator()
        m_file.add_command(label="Quit", command=self.root.destroy)

        self._edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=self._edit_menu)
        self._edit_menu.add_command(label="Undo", accelerator="Ctrl+Z",
                                    command=self.cmd_undo, state="disabled")
        self._edit_menu.add_command(label="Redo", accelerator="Ctrl+Y",
                                    command=self.cmd_redo, state="disabled")

        self._view_object_helpers_var = tk.BooleanVar(value=False)
        self._view_world_helpers_var = tk.BooleanVar(value=False)
        self._view_helper_bsp_mode_var = tk.StringVar(value="normal")
        self._view_helper_role_vars = {
            "aiRail": tk.BooleanVar(value=True),
            "collision": tk.BooleanVar(value=True),
            "water": tk.BooleanVar(value=True),
            "trigger": tk.BooleanVar(value=True),
            "sound": tk.BooleanVar(value=True),
            "skyVisibility": tk.BooleanVar(value=True),
        }

        m_view = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=m_view)
        m_view.add_checkbutton(
            label="Toggle object helpers",
            variable=self._view_object_helpers_var,
            command=self.cmd_toggle_object_helpers,
        )
        m_view.add_checkbutton(
            label="Toggle world helpers",
            variable=self._view_world_helpers_var,
            command=self.cmd_toggle_world_helpers,
        )
        m_helpers = tk.Menu(m_view, tearoff=0)
        m_view.add_cascade(label="Helper BSP", menu=m_helpers)
        m_helpers.add_radiobutton(
            label="Normal",
            value="normal",
            variable=self._view_helper_bsp_mode_var,
            command=self.cmd_set_helper_bsp_mode,
        )
        m_helpers.add_radiobutton(
            label="Helpers translucent",
            value="helpers",
            variable=self._view_helper_bsp_mode_var,
            command=self.cmd_set_helper_bsp_mode,
        )
        m_helpers.add_separator()
        for role, label in (
            ("aiRail", "AI rails"),
            ("collision", "Collision / Firethrough"),
            ("water", "Water volumes"),
            ("trigger", "Triggers"),
            ("sound", "Sound"),
            ("skyVisibility", "Sky / Visibility"),
        ):
            m_helpers.add_checkbutton(
                label=label,
                variable=self._view_helper_role_vars[role],
                command=self.cmd_set_helper_role_groups,
            )

        m_conversion = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Conversion", menu=m_conversion)
        m_conversion.add_command(label="LoMM to MM9",
                                 command=self.cmd_lomm_to_mm9_conversion)

        m_tools = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=m_tools)
        m_tools.add_command(label="Clone Physical Door...",
                            command=self.cmd_clone_physical_door)
        m_tools.add_command(label="Import Static Prefab BSP...",
                            command=self.cmd_import_static_prefab_bsp)
        m_tools.add_separator()
        m_tools.add_command(label="Generate DEDit ED from DAT...",
                            command=self.cmd_generate_dedit_ed_from_dat)
        m_tools.add_command(label="Generate DEDit ED with Reserved Stairs...",
                            command=self.cmd_generate_dedit_ed_from_dat_with_stair_assemblies)
        m_tools.add_command(label="Export DAT Geometry as glTF for Inspection...",
                            command=self.cmd_export_dat_geometry_gltf)

        m_presets = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Presets", menu=m_presets)
        m_presets.add_command(label="New Preset…",
                              command=self.cmd_new_preset)
        m_presets.add_command(label="Manage Presets…",
                              command=self.cmd_manage_presets)

        m_help = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=m_help)
        m_help.add_command(label="About…", command=self._about)

    def _build_layout(self) -> None:
        # Toolbar
        bar = tk.Frame(self.root, bg="#1a1d22", height=36)
        bar.pack(side="top", fill="x")
        tk.Button(bar, text="Open from WORLDS.REZ…", bg="#23272d", fg="white",
                  relief="flat", command=self.cmd_open_worlds_rez).pack(side="left", padx=4, pady=4)
        tk.Button(bar, text="Save…", bg="#2c5e8a", fg="white",
                  activebackground="#3a78ad",
                  relief="flat", command=self.cmd_save).pack(side="left", padx=4, pady=4)

        tk.Label(bar, text="Level:", bg="#1a1d22", fg="#cccccc",
                 font=("Segoe UI", 9)).pack(side="left", padx=(16, 4))

        self.level_var = tk.StringVar()
        self.level_combo = ttk.Combobox(bar, textvariable=self.level_var,
                                        state="readonly", width=40)
        self.level_combo.pack(side="left", padx=4, pady=4)
        self.level_combo.bind("<<ComboboxSelected>>", self._on_level_change)

        tk.Label(bar, text="(3D: F fit · arrows nudge · PgUp/PgDn height · [/] rotate)",
                 bg="#1a1d22", fg="#888", font=("Segoe UI", 9)
                 ).pack(side="right", padx=8)

        # Three-column body
        body = tk.PanedWindow(self.root, orient="horizontal",
                              bg="#0e1116", sashrelief="flat", sashwidth=4)
        body.pack(fill="both", expand=True)

        # Left: level object list
        self.level_panel = CatalogPanel(
            body, self.catalog,
            on_place_class   = self._begin_place_class,
            on_select_object = self._on_panel_object_selected,
            on_place_preset  = self._begin_place_preset,
            preset_store     = self.preset_store,
        )
        self.level_panel.config(width=260)
        body.add(self.level_panel, minsize=200, stretch="never")

        # Center: 3-D viewport
        self._view_frame = tk.Frame(body, bg="#0e1116")
        body.add(self._view_frame, minsize=400, stretch="always")

        if View3D is not None:
            textures_dir = self._asset_dir(
                archive_key="textures",
                virtual_root="TEXTURES",
                extensions=(".DTX",),
            )
            skins_dir = self._asset_dir(
                archive_key="skins",
                virtual_root="SKINS",
                extensions=(".DTX",),
            )
            models_dir = self._asset_dir(
                archive_key="models",
                virtual_root="MODELS",
                extensions=(".ABC",),
            )
            self.view3d = View3D(
                self._view_frame,
                on_select    = self._on_3d_object_selected,
                on_place_xyz = self._on_3d_clicked_for_place,
                on_move_xyz  = self._on_object_positioned,
                on_rotate    = self._on_object_rotated,
                on_elevate   = self._on_object_elevated,
                textures_dir = textures_dir,
                skins_dir    = skins_dir,
                models_dir   = models_dir,
                actor_visuals = self.catalog.get("actor_visuals", {}),
                on_object_helpers_changed = self._on_view3d_object_helpers_changed,
            )
            self.view3d.pack(fill="both", expand=True)
        else:
            self.view3d = None

        # Right: properties — fixed initial width; user can drag the sash wider
        self.props_panel = PropertiesPanel(
            body,
            on_edit        = self._on_property_edited,
            on_delete      = self._on_object_deleted,
            on_save_preset = self._on_save_as_preset,
        )
        self.props_panel.config(width=260)
        body.add(self.props_panel, minsize=200, stretch="never")

    def _build_bindings(self) -> None:
        self.root.bind_all("<Control-o>",       lambda e: self.cmd_open_worlds_rez())
        self.root.bind_all("<Control-s>",       lambda e: self.cmd_save())
        self.root.bind_all("<Control-S>",       lambda e: self.cmd_save_project())
        self.root.bind_all("<Control-O>",       lambda e: self.cmd_open_project())
        self.root.bind_all("<Control-z>",       lambda e: self.cmd_undo())
        self.root.bind_all("<Control-y>",       lambda e: self.cmd_redo())
        self.root.bind_all("<Control-Z>",       lambda e: self.cmd_redo())

    # ---------- editor settings ----------

    def _load_editor_settings(self) -> Dict[str, Any]:
        path = getattr(self, "settings_path", "")
        if not path or not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            return dict(raw.get("settings", raw))
        except Exception:
            return {}

    def _save_editor_settings(self) -> None:
        path = getattr(self, "settings_path", "")
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "version": 1,
                        "settings": getattr(self, "editor_settings", {}),
                    },
                    fh,
                    indent=2,
                    ensure_ascii=False,
                )
        except Exception:
            pass

    def _last_lomm_root(self) -> str:
        settings = getattr(self, "editor_settings", {}) or {}
        value = str(settings.get("last_lomm_root") or "")
        return value if value and os.path.isdir(value) else ""

    def _remember_lomm_root(self, lomm_root: str) -> None:
        value = str(lomm_root or "").strip()
        if not value:
            return
        settings = getattr(self, "editor_settings", None)
        if not isinstance(settings, dict):
            settings = {}
            self.editor_settings = settings
        settings["last_lomm_root"] = os.path.abspath(value)
        self._save_editor_settings()

    def _on_3d_object_selected(self, world_index: int) -> None:
        """Selection callback from the 3-D view (one-arg; adapts to two-arg)."""
        L = getattr(self, "active", None)
        if not L:
            return
        mat = L.editor_materialize() if hasattr(L, "editor_materialize") else L.materialize()
        obj = mat.objects[world_index] if 0 <= world_index < len(mat.objects) else None
        self._on_object_selected(world_index, obj)

    # ---------- view-delegation helpers ----------

    def _refresh_all_views(self) -> None:
        """Refresh the 3-D viewer."""
        if self.view3d:
            self.view3d.refresh()

    def _select_all_views(self, world_index: int) -> None:
        """Highlight an object in the 3-D viewer."""
        if self.view3d:
            self.view3d.select_by_index(world_index)

    def _update_history_menu(self) -> None:
        menu = getattr(self, "_edit_menu", None)
        if menu is None:
            return
        L = getattr(self, "active", None)
        can_undo = bool(L and L.ops)
        can_redo = bool(L and getattr(L, "redo_ops", None))
        menu.entryconfig(0, state="normal" if can_undo else "disabled")
        menu.entryconfig(1, state="normal" if can_redo else "disabled")

    def _show_selected_materialized(self, world_index: Optional[int] = None) -> None:
        L = getattr(self, "active", None)
        if not L:
            self._selected_world_index = None
            self.props_panel.show(None)
            return
        if world_index is None:
            world_index = self._selected_world_index
        if world_index is None:
            self.props_panel.show(None)
            return
        mat = L.editor_materialize() if hasattr(L, "editor_materialize") else L.materialize()
        if 0 <= world_index < len(mat.objects):
            self._selected_world_index = world_index
            self.props_panel.show(mat.objects[world_index])
            self._select_all_views(world_index)
            if self.level_panel:
                self.level_panel.highlight_index(world_index)
        else:
            self._selected_world_index = None
            self.props_panel.show(None)

    def _refresh_after_edit(self, selected_index: Optional[int] = None) -> None:
        self._refresh_all_views()
        self.level_panel.refresh()
        self._show_selected_materialized(selected_index)
        self._update_history_menu()

    # ---------- commands ----------

    def _flush_view_transforms(self) -> None:
        """Commit any debounced 3-D preview transform before model reads."""
        if self.view3d and hasattr(self.view3d, "flush_pending_transforms"):
            self.view3d.flush_pending_transforms()

    def cmd_open_worlds_rez(self) -> None:
        self._flush_view_transforms()
        rez_path = self.resources.archive_for("WORLDS/")
        if not rez_path:
            messagebox.showwarning(
                "WORLDS.REZ not found",
                "No game data/WORLDS.REZ archive was detected.")
            return
        from ui.rez_picker import RezPicker
        RezPicker(self.root, rez_path, self._open_rez_level)

    def _open_rez_level(self, rez_path: str, virtual_path: str) -> None:
        try:
            L = self.project.add_level_from_rez(rez_path, virtual_path)
        except Exception as e:
            messagebox.showerror("Open failed", str(e))
            return

        if self.view3d is not None:
            textures_dir = self._asset_dir(
                archive_key="textures",
                virtual_root="TEXTURES",
                extensions=(".DTX",),
            )
            skins_dir = self._asset_dir(
                archive_key="skins",
                virtual_root="SKINS",
                extensions=(".DTX",),
            )
            models_dir = self._asset_dir(
                archive_key="models",
                virtual_root="MODELS",
                extensions=(".ABC",),
            )
            self.view3d.update_asset_directories(
                textures_dir=textures_dir,
                skins_dir=skins_dir,
                models_dir=models_dir,
            )

        names = [L.display_name for L in self.project.levels]
        self.level_combo["values"] = names
        self.level_var.set(L.display_name)
        self._set_active(L)

    def cmd_save(self) -> None:
        self._flush_view_transforms()
        if not self.project.has_pending():
            messagebox.showinfo("Nothing to save",
                                "There are no pending edits in any loaded level.")
            return
        plan = self.project.save_plan()
        SaveDialog(self.root, self.project, plan,
                   on_committed=self._on_save_committed,
                   cfg=self.cfg)

    def cmd_install_output(self) -> None:
        """Install a saved output batch into the detected game data folder."""
        game_data_dir = getattr(self.cfg, "game_data_dir", None)
        if not game_data_dir or not os.path.isdir(game_data_dir):
            messagebox.showerror(
                "Game folder not detected",
                "No game data folder was detected. Launch from the game folder "
                "or use --game-root.")
            return

        initial = getattr(self.project, "work_dir", None) or getattr(self.cfg, "work_dir", None)
        batch_dir = filedialog.askdirectory(
            title="Pick an output batch folder to install",
            initialdir=initial if initial and os.path.isdir(initial) else None,
        )
        if not batch_dir:
            return

        try:
            from core import install_manager
            archives = install_manager.archives_to_install(batch_dir)
        except Exception as e:
            messagebox.showerror("Cannot inspect output batch", str(e))
            return
        if not archives:
            messagebox.showerror(
                "No archives found",
                f"No patched .REZ files were found under:\n{os.path.join(batch_dir, 'data')}")
            return
        archive_names = [os.path.basename(path) for path in archives]

        if not messagebox.askyesno(
            "Install output to game?",
            "This will back up and replace the following game archives:\n\n"
            + "\n".join(f"  {name}" for name in archive_names)
            + f"\n\nGame data folder:\n{game_data_dir}",
        ):
            return

        try:
            result = install_manager.install_batch(
                batch_dir=batch_dir,
                game_data_dir=game_data_dir,
                backup_root=getattr(self.cfg, "backup_root", None),
            )
        except Exception as e:
            messagebox.showerror("Install failed", str(e))
            return

        messagebox.showinfo("Install complete", "\n".join(result.log_lines()))

    def cmd_restore_backup(self) -> None:
        """Restore a backup previously created by Install Output to Game."""
        initial = getattr(self.cfg, "backup_root", None)
        backup_path = filedialog.askdirectory(
            title="Pick an install backup folder to restore",
            initialdir=initial if initial and os.path.isdir(initial) else None,
        )
        if not backup_path:
            return

        try:
            from core import install_manager
            archives = install_manager.backups_to_restore(backup_path)
        except Exception as e:
            messagebox.showerror("Cannot inspect backup", str(e))
            return
        if not archives:
            messagebox.showerror(
                "No archives found",
                f"No backed-up .REZ files were found under:\n{backup_path}")
            return

        archive_names = [os.path.basename(path) for path in archives]
        game_data_dir = getattr(self.cfg, "game_data_dir", None)
        if not messagebox.askyesno(
            "Restore backup to game?",
            "This will back up the current live archives and restore:\n\n"
            + "\n".join(f"  {name}" for name in archive_names)
            + (
                f"\n\nGame data folder:\n{game_data_dir}"
                if game_data_dir else
                "\n\nThe backup manifest will be used to find the game data folder."
            ),
        ):
            return

        try:
            result = install_manager.restore_backup(
                backup_path=backup_path,
                game_data_dir=game_data_dir,
                safety_backup_root=getattr(self.cfg, "backup_root", None),
            )
        except Exception as e:
            messagebox.showerror("Restore failed", str(e))
            return

        messagebox.showinfo("Restore complete", "\n".join(result.log_lines()))

    def cmd_undo(self) -> None:
        self._flush_view_transforms()
        L = getattr(self, "active", None)
        if not L or not L.undo_last_op():
            self._update_history_menu()
            return
        self._refresh_after_edit(self._selected_world_index)

    def cmd_redo(self) -> None:
        self._flush_view_transforms()
        L = getattr(self, "active", None)
        if not L or not L.redo_last_op():
            self._update_history_menu()
            return
        self._refresh_after_edit(self._selected_world_index)

    def cmd_toggle_object_helpers(self) -> None:
        """Mirror the 3-D toolbar Helpers toggle from the View menu."""
        if self.view3d is None:
            return
        self.view3d.set_show_object_helper_billboards(
            bool(self._view_object_helpers_var.get())
        )

    def cmd_toggle_world_helpers(self) -> None:
        """Show or hide service/world helper object billboards."""
        if self.view3d is None:
            return
        self.view3d.set_show_world_helper_billboards(
            bool(self._view_world_helpers_var.get())
        )

    def cmd_set_helper_bsp_mode(self) -> None:
        """Apply the selected helper BSP preview mode."""
        if self.view3d is None:
            return
        self.view3d.set_helper_bsp_mode(self._view_helper_bsp_mode_var.get())
        self.cmd_set_helper_role_groups()

    def cmd_set_helper_role_groups(self) -> None:
        """Apply selected helper BSP role groups."""
        if self.view3d is None:
            return
        groups = {
            role
            for role, var in self._view_helper_role_vars.items()
            if bool(var.get())
        }
        self.view3d.set_helper_role_groups(groups)

    def cmd_lomm_to_mm9_conversion(self) -> None:
        """Open the LoMM-to-MM9 conversion workflow."""
        self._flush_view_transforms()
        mm9_root = getattr(self.cfg, "game_root", None)
        if not mm9_root or not os.path.isdir(mm9_root):
            messagebox.showerror(
                "MM9 game folder not detected",
                "No MM9 game folder was detected. Launch from the game folder "
                "or use --game-root.",
            )
            return
        LommConversionDialog.open(
            self.root,
            mm9_root=mm9_root,
            backup_root=getattr(self.cfg, "backup_root", None),
            catalog_json=self.catalog_path,
            initial_lomm_root=self._last_lomm_root(),
            on_success=self._on_lomm_conversion_success,
        )

    def _on_lomm_conversion_success(self, result: Any, lomm_root: str = "") -> None:
        """Open the newly inserted MM9 level after a LoMM conversion."""
        self._remember_lomm_root(lomm_root)
        try:
            self._open_rez_level(result.worlds_rez, result.added_virtual_path)
        except Exception as exc:
            messagebox.showerror(
                "Open converted level failed",
                str(exc),
            )

    def _on_view3d_object_helpers_changed(self, enabled: bool) -> None:
        """Keep View menu state synced when the toolbar button is clicked."""
        self._view_object_helpers_var.set(bool(enabled))

    def cmd_save_project(self) -> None:
        """Save the current project (levels + ops) to a .mm9mod JSON file."""
        self._flush_view_transforms()
        if not self.project.levels:
            messagebox.showinfo("Nothing to save", "No levels are open.")
            return
        path = filedialog.asksaveasfilename(
            title="Save project",
            defaultextension=".mm9mod",
            filetypes=[("MM9 mod project", "*.mm9mod"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            project_io.project_to_json(self.project, path)
            messagebox.showinfo("Project saved",
                                f"Saved {len(self.project.levels)} level(s) to:\n{path}")
        except Exception as e:
            messagebox.showerror("Save project failed", str(e))

    def cmd_open_project(self) -> None:
        """Load a .mm9mod project file, re-opening its levels from source files."""
        self._flush_view_transforms()
        path = filedialog.askopenfilename(
            title="Open project",
            filetypes=[("MM9 mod project", "*.mm9mod"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            log = project_io.project_from_json(path, self.project)
        except Exception as e:
            messagebox.showerror("Open project failed", str(e))
            return

        # Refresh level combo and activate first loaded level
        names = [L.display_name for L in self.project.levels]
        self.level_combo["values"] = names
        if names:
            self.level_var.set(names[0])
            self._set_active(self.project.levels[0])

        # Show load log
        if log:
            messagebox.showinfo("Project loaded", "\n".join(log))

    # ---------- selection / catalog / placement ----------

    def _on_level_change(self, _evt) -> None:
        self._flush_view_transforms()
        name = self.level_var.get()
        for L in self.project.levels:
            if L.display_name == name:
                self._set_active(L)
                return

    def _set_active(self, L: P.LevelEdit) -> None:
        self.active = L
        self._selected_world_index = None
        if self.view3d:
            self.view3d.set_active_level(L)
        self.level_panel.set_active_level(L)
        self.props_panel.show(None)
        self._update_history_menu()
        if self.view3d:
            self.root.after_idle(self.view3d.focus_for_input)

    def _on_object_selected(self, world_index: int,
                            obj: Optional[patcher.WorldObject]) -> None:
        """Called when the user selects an object in the 3-D view."""
        self._selected_world_index = world_index
        self.props_panel.show(obj)
        self.level_panel.highlight_index(world_index)

    def _on_panel_object_selected(self, world_index: int,
                                  obj: patcher.WorldObject) -> None:
        """Called when the user clicks a row in the level panel."""
        self._selected_world_index = world_index
        self.props_panel.show(obj)
        self._select_all_views(world_index)

    def _begin_place_class(self, class_name: str) -> None:
        if not getattr(self, "active", None):
            messagebox.showwarning("No level", "Open a level from WORLDS.REZ first.")
            return
        # Find a template instance in any level we have loaded — failing that, in
        # the catalog's recorded source level (load it ad-hoc).
        template = self._find_template_for_class(class_name)
        if template is None:
            e = self.catalog["classes"].get(class_name)
            if not e:
                detail = "class is not in the catalog (try rebuilding catalog.json)"
            else:
                src = e["template"]["source_level"]
                detail = (
                    f"searched loaded levels and configured game resources; "
                    f"{class_name!r} was not found in its "
                    f"source level ({src!r}).\n\n"
                    f"Make sure WORLDS.REZ is detected or rebuild catalog.json "
                    f"if the source data changed."
                )
            messagebox.showerror("No template",
                                 f"Couldn't find a usable instance of {class_name!r}.\n\n"
                                 f"{detail}")
            return

        # Phase 6: if this is an NPC class (has NPCNbr), ask about dialogue mode
        # before entering place-mode so the user configures RUDE up-front.
        self._pending_rude_config = None
        if any(p.name == "NPCNbr" for p in template.props):
            from ui.npc_dialog import FreshNpcDialog
            result = FreshNpcDialog.ask(
                self.root,
                suggested_nbr=self.project.next_npc_nbr,
                default_name=class_name,
            )
            if result is None:
                return  # user cancelled — abort placement entirely
            self._pending_rude_config = result

        self._pending_template = template
        self._pending_kind = "class"
        if self.view3d is not None:
            self.view3d.set_place_mode(True)

    def _begin_place_filename(self, filename: str) -> None:
        if not getattr(self, "active", None):
            messagebox.showwarning("No level", "Open a .DAT first.")
            return
        # Pick a Prop-class instance using this filename as the template.
        template = self._find_template_for_filename(filename)
        if template is None:
            messagebox.showerror("No template",
                                 f"Couldn't find a Prop using {filename!r}.")
            return
        self._pending_template = template
        self._pending_kind = "filename"
        self._pending_filename = filename
        if self.view3d is not None:
            self.view3d.set_place_mode(True)

    def _begin_place_preset(self, preset_name: str) -> None:
        """Enter place-mode for a user preset."""
        if not getattr(self, "active", None):
            messagebox.showwarning("No level", "Open a .DAT first.")
            return
        preset = self.preset_store.get(preset_name)
        if preset is None:
            messagebox.showerror("Unknown preset",
                                 f"Preset {preset_name!r} was not found in the store.")
            return
        # Find a template object for the preset's base class
        template = self._find_template_for_class(preset.base_class)
        if template is None:
            messagebox.showerror("No template",
                                 f"Couldn't find a template for base class "
                                 f"{preset.base_class!r}.\n\n"
                                 f"Open at least one level that contains this "
                                 f"class, or rebuild catalog.json.")
            return
        self._pending_template = template
        self._pending_kind = "preset"
        self._pending_preset = preset
        self._pending_rude_config = None
        if self.view3d is not None:
            self.view3d.set_place_mode(True)

    def cmd_clone_physical_door(self) -> None:
        if not getattr(self, "active", None):
            messagebox.showwarning("No level", "Open a level from WORLDS.REZ first.")
            return
        L = self.active
        bsp_world = L.get_bsp()
        if bsp_world is None:
            messagebox.showerror("No BSP", "This level's BSP geometry could not be parsed.")
            return
        links = door_links.build_physical_door_links(L.world.objects, bsp_world)
        if not links:
            messagebox.showinfo(
                "No physical doors",
                "No Door or RotatingDoor objects with matching BSP submodels were found in this level.",
            )
            return

        selected = ""
        selected_idx = getattr(self, "_selected_world_index", None)
        if selected_idx is not None:
            mat = L.materialize()
            if 0 <= selected_idx < len(mat.objects):
                obj = mat.objects[selected_idx]
                if door_links.find_physical_door_link(L.world.objects, bsp_world, obj.get("Name") or ""):
                    selected = obj.get("Name") or ""
        if not selected:
            selected = links[0].name

        links_by_name = {link.name.lower(): link for link in links}
        def _suggest_door_name(name: str) -> str:
            link = links_by_name.get(str(name or "").lower())
            return door_clone.suggest_clone_name(
                L.materialize().objects,
                bsp_world,
                name,
                pair_name=link.pair_name if link else "",
            )

        def _describe_door_source(name: str) -> str:
            link = links_by_name.get(str(name or "").lower())
            if link is None:
                return ""
            parts = [link.class_name]
            if link.pair_name:
                if link.is_paired:
                    parts.append(f"paired with {link.pair_name}")
                else:
                    parts.append(f"references missing pair {link.pair_name}")
            portal = ""
            try:
                portal = str(link.obj.get("PortalName") or "")
            except Exception:
                portal = ""
            if portal:
                parts.append(f"PortalName={portal}")
            parts.append(f"{len(link.model.polygons)} polys")
            return " · ".join(parts)

        default_name = _suggest_door_name(selected)
        from ui.door_clone_dialog import DoorCloneDialog
        result = DoorCloneDialog.ask(
            self.root,
            [link.name for link in links],
            default_source=selected,
            default_new_name=default_name,
            default_include_pair=True,
            suggest_name=_suggest_door_name,
            describe_source=_describe_door_source,
        )
        if result is None:
            return

        self._pending_template = None
        self._pending_kind = "clone_door"
        self._pending_door_source = result.source_name
        self._pending_door_name = result.new_name
        self._pending_door_include_pair = result.include_pair
        if self.view3d is not None:
            self.view3d.set_place_mode(True)

    def cmd_export_dat_geometry_gltf(self) -> None:
        if not getattr(self, "active", None):
            messagebox.showwarning("No level", "Open a level from WORLDS.REZ first.")
            return
        L = self.active
        bsp_world = L.get_bsp()
        if bsp_world is None:
            messagebox.showerror("No BSP", "This level's BSP geometry could not be parsed.")
            return

        editor_dir = getattr(self.cfg, "editor_dir", None) or EDITOR_ROOT
        output_dir = filedialog.askdirectory(
            title="Export DAT geometry as inspection glTF",
            initialdir=editor_dir,
        )
        if not output_dir:
            return

        try:
            source_name = L.display_name or L.rez_vpath or L.path or "level"
            base_name = os.path.splitext(os.path.basename(source_name))[0] or "level"
            result = dat_gltf_export.export_gltf_inspection(
                bsp_world,
                L.source_bytes(),
                output_dir,
                source_path=source_name,
                base_name=base_name,
                objects=L.materialize().objects if L.world is not None else None,
            )
        except Exception as e:
            messagebox.showerror("glTF export failed", str(e))
            return

        messagebox.showinfo(
            "glTF export complete",
            "Wrote inspection glTF files:\n\n"
            f"{result.gltf_path}\n"
            f"{result.bin_path}\n"
            f"{result.meta_path}\n\n"
            f"Models: {result.model_count}; polygons: {result.polygon_count}; "
            f"triangles: {result.triangle_count}",
        )

    def cmd_generate_dedit_ed_from_dat_with_stair_assemblies(self) -> None:
        """Select high-confidence PhysicsBSP stairs and run normal generation."""
        if not getattr(self, "active", None):
            messagebox.showwarning("No level", "Open a level from WORLDS.REZ first.")
            return
        bsp_world = self.active.get_bsp()
        if bsp_world is None:
            messagebox.showerror("No BSP", "This level's BSP geometry could not be parsed.")
            return
        physics_model = terrain_semantics.model_by_name(
            getattr(bsp_world, "world_models", ()) or (),
            terrain_semantics.PHYSICS_BSP_MODEL,
        )
        if physics_model is None:
            messagebox.showinfo(
                "No stair assemblies",
                "The active DAT has no PhysicsBSP model to inspect.",
            )
            return
        try:
            candidates = terrain_reconstruction.physics_shell_candidates(physics_model)
            assemblies = terrain_reconstruction.detect_physics_shell_stair_assemblies(
                physics_model,
                candidates,
            )
        except Exception as e:
            messagebox.showerror("Stair detection failed", str(e))
            return
        if not assemblies:
            messagebox.showinfo(
                "No stair assemblies",
                "No conservative PhysicsBSP stair assemblies were detected.",
            )
            return

        eligible_indices = {
            int(assembly.assembly_index)
            for assembly in assemblies
            if str(assembly.confidence).lower() == "high"
        }
        details = []
        for assembly in assemblies:
            eligible = int(assembly.assembly_index) in eligible_indices
            bounds_min = tuple(round(float(value), 1) for value in assembly.bounds_min)
            bounds_max = tuple(round(float(value), 1) for value in assembly.bounds_max)
            details.append(
                f"{assembly.assembly_index}: {assembly.confidence}; "
                f"steps={assembly.step_count}; "
                f"source polygons={len(assembly.source_polygon_indices)}; "
                f"estimated faces={assembly.generated_face_count}; "
                f"bounds={bounds_min}..{bounds_max}"
                + ("" if eligible else " [inspection only]")
            )
        if not eligible_indices:
            messagebox.showinfo(
                "No reservable stair assemblies",
                "Stair candidates were detected, but none have high confidence.\n\n"
                + "\n".join(details),
            )
            return

        answer = simpledialog.askstring(
            "Reserve PhysicsBSP stair assemblies",
            "Detected stair assemblies:\n\n"
            + "\n".join(details)
            + "\n\nOnly high-confidence IDs are reservable. "
              "Enter one or more eligible IDs separated by commas. "
              "Every requested assembly will be emitted completely or rejected completely.\n"
            + "Eligible IDs: "
            + ", ".join(str(index) for index in sorted(eligible_indices)),
            initialvalue="",
            parent=getattr(self, "root", None),
        )
        if answer is None or not str(answer).strip():
            return
        try:
            parts = tuple(
                part for part in re.split(r"[,;\s]+", str(answer).strip()) if part
            )
            selected_indices = tuple(sorted({int(part) for part in parts}))
        except ValueError:
            messagebox.showerror(
                "Invalid stair assembly selection",
                "Enter only integer assembly IDs separated by commas.",
            )
            return
        invalid_indices = tuple(
            index for index in selected_indices if index not in eligible_indices
        )
        if not selected_indices or invalid_indices:
            messagebox.showerror(
                "Invalid stair assembly selection",
                "These IDs are not eligible high-confidence assemblies: "
                + ", ".join(str(index) for index in invalid_indices),
            )
            return
        self.cmd_generate_dedit_ed_from_dat(
            physics_shell_stair_assembly_indices=selected_indices,
        )

    def cmd_generate_dedit_ed_from_dat(
        self,
        *,
        behavior_prop_validation_profile: str = "none",
        physics_shell_stair_assembly_indices: Sequence[int] = (),
    ) -> None:
        if not getattr(self, "active", None):
            messagebox.showwarning("No level", "Open a level from WORLDS.REZ first.")
            return
        L = self.active
        bsp_world = L.get_bsp()
        if bsp_world is None:
            messagebox.showerror("No BSP", "This level's BSP geometry could not be parsed.")
            return
        source_name = L.display_name or L.rez_vpath or L.path or "level"
        stem = self._dat_to_ed_output_stem(source_name)
        level_policy_key = stem.upper()
        physics_shell_stair_assembly_indices = tuple(sorted({
            int(index) for index in physics_shell_stair_assembly_indices
        }))

        behavior_prop_validation_profile_key = str(
            behavior_prop_validation_profile or "none"
        ).strip().lower().replace("-", "_").replace(" ", "_")
        behavior_prop_validation_enabled = behavior_prop_validation_profile_key not in {
            "",
            "none",
            "off",
            "false",
            "0",
        }
        destructable_brush_behavior_prop_validation_enabled = behavior_prop_validation_profile_key in {
            "destructable_brush",
            "destructablebrush",
            "destructible_brush",
            "destructiblebrush",
            "destructible",
            "dragonstadium_destructable_brush",
            "dragonstadium_destructible_brush",
        }
        destructable_prop_behavior_prop_validation_enabled = behavior_prop_validation_profile_key in {
            "destructable_prop",
            "destructableprop",
            "destructible_prop",
            "destructibleprop",
        }
        default_model_names = self._default_dat_to_ed_model_names(bsp_world)
        destructable_brush_model_names = self._dat_object_model_names_for_class(
            L,
            bsp_world,
            "DestructableBrush",
        )
        has_terrain0 = any(
            str(name or "").lower() == terrain_semantics.DEFAULT_TERRAIN_MODEL.lower()
            for name in terrain_semantics.terrain_model_names(bsp_world)
        )
        has_physics_bsp = any(
            terrain_semantics.is_physics_bsp_model(model)
            for model in getattr(bsp_world, "world_models", []) or []
        )
        has_airail_helpers = any(
            self._dat_model_is_pure_airail_helper(model)
            for model in getattr(bsp_world, "world_models", []) or []
        )
        has_sky_helpers = any(
            self._dat_model_has_sky_visibility_helper(model)
            for model in getattr(bsp_world, "world_models", []) or []
        )
        has_sound_helpers = any(
            self._dat_model_has_sound_helper(model)
            for model in getattr(bsp_world, "world_models", []) or []
        )
        has_collision_helpers = any(
            self._dat_model_is_pure_collision_helper(model)
            for model in getattr(bsp_world, "world_models", []) or []
        )
        has_trigger_helpers = any(
            self._dat_model_is_pure_trigger_helper(model)
            for model in getattr(bsp_world, "world_models", []) or []
        )
        dat_native_destructable_brush_enabled = bool(
            destructable_brush_model_names
        ) and (
            destructable_brush_behavior_prop_validation_enabled
            or (
                not behavior_prop_validation_enabled
                and not has_terrain0
                and level_policy_key == "DRAGONSTADIUM"
            )
        )
        model_names = (
            destructable_brush_model_names
            if dat_native_destructable_brush_enabled
            else default_model_names
        )
        has_door_models = any(
            "door" in str(name or "").lower()
            for name in model_names or ()
        )
        if destructable_brush_behavior_prop_validation_enabled and not destructable_brush_model_names:
            messagebox.showerror(
                "DAT to ED generation failed",
                "No same-name DestructableBrush DAT object/BSP model pairs were found.",
            )
            return
        if not model_names and not has_terrain0:
            messagebox.showerror(
                "DAT to ED generation failed",
                "No eligible DAT world models were found for ED generation.",
            )
            return
        include_terrain_support_patch = bool(has_terrain0 and model_names)
        include_physics_shell_patch = bool((not has_terrain0) and model_names and has_physics_bsp)
        include_validation_floor = False
        if dat_native_destructable_brush_enabled:
            include_terrain_support_patch = False
            include_physics_shell_patch = False
            include_validation_floor = True
        elif destructable_prop_behavior_prop_validation_enabled and not has_terrain0:
            include_physics_shell_patch = False
            include_validation_floor = True
        terrain_support_brush_budget = max(
            1,
            DAT_TO_ED_PROCESSOR_BRUSH_BUDGET - len(model_names or ()),
        )
        selected_model_name_set = {str(name).lower() for name in model_names or ()}
        selected_polygon_count = sum(
            len(getattr(model, "polygons", []) or [])
            for model in getattr(bsp_world, "world_models", []) or []
            if str(getattr(model, "name", "") or "").lower() in selected_model_name_set
        )
        remaining_polygon_budget = max(1, DAT_TO_ED_PROCESSOR_POLYGON_BUDGET - selected_polygon_count)
        physics_shell_polygon_budget = min(
            terrain_support_brush_budget,
            max(1, remaining_polygon_budget // 6),
        )

        editor_dir = getattr(self.cfg, "editor_dir", None) or EDITOR_ROOT
        initial_dir = getattr(self.cfg, "work_dir", None) or editor_dir
        output_dir = filedialog.askdirectory(
            title="Generate DEDit ED from DAT",
            initialdir=initial_dir,
        )
        if not output_dir:
            return

        try:
            source_ed_oracle_path = self._source_ed_oracle_path(
                source_name,
                level_path=getattr(L, "path", "") or "",
            )
            if (
                behavior_prop_validation_enabled
                and not source_ed_oracle_path
                and not destructable_brush_behavior_prop_validation_enabled
            ):
                messagebox.showerror(
                    "DAT to ED generation failed",
                    "Behavior prop validation requires a same-stem source ED oracle.",
                )
                return
            medium_risk_behavior_prop_validation_enabled = behavior_prop_validation_profile_key in {
                "all",
                "on",
                "true",
                "yes",
                "1",
                "included",
                "medium",
                "medium_risk",
                "medium_light",
                "medium_risk_light",
                "light",
                "lights",
                "light_fire",
                "light_fire_sound",
                "light_fire_sound_model",
            }
            candle_prop_behavior_prop_validation_enabled = behavior_prop_validation_profile_key in {
                "all",
                "on",
                "true",
                "yes",
                "1",
                "included",
                "medium",
                "medium_risk",
                "medium_light",
                "medium_risk_light",
                "light",
                "lights",
                "light_fire",
                "light_fire_sound",
                "light_fire_sound_model",
                "candle",
                "candle_prop",
                "candleprop",
            }
            brazier_behavior_prop_validation_enabled = behavior_prop_validation_profile_key in {
                "all",
                "on",
                "true",
                "yes",
                "1",
                "included",
                "medium",
                "medium_risk",
                "medium_light",
                "medium_risk_light",
                "light",
                "lights",
                "light_fire",
                "light_fire_sound",
                "light_fire_sound_model",
                "brazier",
            }
            treasure_chest_behavior_prop_validation_enabled = behavior_prop_validation_profile_key in {
                "all",
                "on",
                "true",
                "yes",
                "1",
                "included",
                "high",
                "high_risk",
                "treasure",
                "treasure_chest",
                "treasurechest",
                "chest",
            }
            prop_damager_behavior_prop_validation_enabled = behavior_prop_validation_profile_key in {
                "all",
                "on",
                "true",
                "yes",
                "1",
                "included",
                "high",
                "high_risk",
                "prop_damager",
                "propdamager",
                "damager",
                "damage",
            }
            high_risk_behavior_prop_validation_enabled = behavior_prop_validation_profile_key in {
                "all",
                "on",
                "true",
                "yes",
                "1",
                "included",
                "high",
                "high_risk",
            }
            door_source_ed_path = source_ed_oracle_path
            include_door_objects = bool(has_door_models and door_source_ed_path)
            if not include_door_objects:
                door_source_ed_path = ""
            airail_source_ed_path = source_ed_oracle_path
            include_airail_objects = bool(has_airail_helpers)
            if not include_airail_objects:
                airail_source_ed_path = ""
            sky_source_ed_path = source_ed_oracle_path
            include_sky_objects = bool(has_sky_helpers)
            if not include_sky_objects:
                sky_source_ed_path = ""
            sound_source_ed_path = source_ed_oracle_path
            include_sound_objects = bool(has_sound_helpers)
            if not include_sound_objects:
                sound_source_ed_path = ""
            # Helper Brush emissions remain diagnostic-only until compiled-DAT
            # leakage reports show they do not enter visible/visibility BSP.
            collision_helper_source_ed_path = source_ed_oracle_path
            include_collision_helper_objects = bool(has_collision_helpers)
            include_collision_helper_brushes = False
            if not include_collision_helper_objects:
                collision_helper_source_ed_path = ""
            trigger_helper_source_ed_path = source_ed_oracle_path
            include_trigger_helper_objects = bool(has_trigger_helpers)
            include_trigger_helper_brushes = False
            if not include_trigger_helper_objects:
                trigger_helper_source_ed_path = ""
            low_risk_behavior_prop_source_ed_path = source_ed_oracle_path
            include_low_risk_behavior_prop_objects = bool(low_risk_behavior_prop_source_ed_path)
            validated_light_fire_behavior_prop_source_ed_path = source_ed_oracle_path
            candle_prop_behavior_prop_source_ed_path = (
                ""
                if brazier_behavior_prop_validation_enabled and not candle_prop_behavior_prop_validation_enabled
                else source_ed_oracle_path
            )
            brazier_behavior_prop_source_ed_path = (
                ""
                if candle_prop_behavior_prop_validation_enabled and not brazier_behavior_prop_validation_enabled
                else source_ed_oracle_path
            )
            treasure_chest_behavior_prop_source_ed_path = (
                source_ed_oracle_path
                if (
                    not behavior_prop_validation_enabled
                    or treasure_chest_behavior_prop_validation_enabled
                    or prop_damager_behavior_prop_validation_enabled
                    or destructable_prop_behavior_prop_validation_enabled
                    or high_risk_behavior_prop_validation_enabled
                )
                else ""
            )
            prop_damager_behavior_prop_source_ed_path = (
                source_ed_oracle_path
                if (
                    not behavior_prop_validation_enabled
                    or prop_damager_behavior_prop_validation_enabled
                    or destructable_prop_behavior_prop_validation_enabled
                    or high_risk_behavior_prop_validation_enabled
                )
                else ""
            )
            destructable_prop_behavior_prop_source_ed_path = (
                source_ed_oracle_path
                if (
                    not behavior_prop_validation_enabled
                    or destructable_prop_behavior_prop_validation_enabled
                    or high_risk_behavior_prop_validation_enabled
                )
                else ""
            )
            include_wall_torch_objects = bool(validated_light_fire_behavior_prop_source_ed_path)
            include_fire_objects = bool(validated_light_fire_behavior_prop_source_ed_path)
            include_candle_prop_objects = bool(candle_prop_behavior_prop_source_ed_path)
            include_brazier_objects = bool(brazier_behavior_prop_source_ed_path)
            include_treasure_chest_objects = bool(treasure_chest_behavior_prop_source_ed_path)
            include_prop_damager_objects = bool(prop_damager_behavior_prop_source_ed_path)
            include_destructable_prop_objects = bool(destructable_prop_behavior_prop_source_ed_path)
            include_destructable_brush_objects = bool(dat_native_destructable_brush_enabled)
            allow_unreconstructed_physics_shell = bool(dat_native_destructable_brush_enabled)
            source_has_destructable_props = self._source_ed_has_object_class(
                destructable_prop_behavior_prop_source_ed_path,
                "DestructableProp",
            )
            if include_destructable_prop_objects and source_has_destructable_props and not has_terrain0:
                include_physics_shell_patch = False
                include_validation_floor = True
                allow_unreconstructed_physics_shell = True
            physics_shell_focus_points = ()
            physics_shell_focus_radius = 0.0
            physics_shell_focus_budget = 0
            physics_shell_focus_seed_radius = 0.0
            if include_physics_shell_patch and level_policy_key == "ANSKRAMKEEP":
                physics_shell_focus_points = (DAT_TO_ED_ANSKRAMKEEP_BACK_START_POINT,)
                physics_shell_focus_radius = 512.0
                physics_shell_focus_budget = 512
                physics_shell_focus_seed_radius = 128.0
            os.makedirs(output_dir, exist_ok=True)
            staged_dir = os.path.join(output_dir, "source_dat")
            os.makedirs(staged_dir, exist_ok=True)
            staged_dat = os.path.join(staged_dir, f"{stem}.DAT")
            with open(staged_dat, "wb") as f:
                f.write(L.source_bytes())

            worlds_install_dir = ""
            game_data_dir = getattr(self.cfg, "game_data_dir", None)
            if game_data_dir:
                worlds_install_dir = os.path.join(game_data_dir, "WORLDS")
            if not behavior_prop_validation_enabled:
                output_suffix = "reconstructed"
            elif destructable_brush_behavior_prop_validation_enabled:
                output_suffix = "reconstructed_destructable_brush_validation"
            elif (
                candle_prop_behavior_prop_validation_enabled
                and not brazier_behavior_prop_validation_enabled
                and not high_risk_behavior_prop_validation_enabled
            ):
                output_suffix = "reconstructed_candle_prop_validation"
            elif (
                brazier_behavior_prop_validation_enabled
                and not candle_prop_behavior_prop_validation_enabled
                and not high_risk_behavior_prop_validation_enabled
            ):
                output_suffix = "reconstructed_brazier_validation"
            elif (
                treasure_chest_behavior_prop_validation_enabled
                and not high_risk_behavior_prop_validation_enabled
            ):
                output_suffix = "reconstructed_treasure_chest_validation"
            elif (
                prop_damager_behavior_prop_validation_enabled
                and not high_risk_behavior_prop_validation_enabled
            ):
                output_suffix = "reconstructed_prop_damager_validation"
            elif (
                destructable_prop_behavior_prop_validation_enabled
                and not high_risk_behavior_prop_validation_enabled
            ):
                output_suffix = "reconstructed_destructable_prop_validation"
            elif medium_risk_behavior_prop_validation_enabled and not high_risk_behavior_prop_validation_enabled:
                output_suffix = "reconstructed_medium_light_prop_validation"
            elif high_risk_behavior_prop_validation_enabled and not medium_risk_behavior_prop_validation_enabled:
                output_suffix = "reconstructed_high_risk_prop_validation"
            else:
                output_suffix = "reconstructed_behavior_prop_validation"
            if physics_shell_stair_assembly_indices:
                output_suffix += "_stairs_" + "_".join(
                    str(index) for index in physics_shell_stair_assembly_indices
                )

            report = dat_compiler_strategy.build_full_world_skeleton_acceptance_report(
                source_dat_path=staged_dat,
                model_names=model_names or (terrain_semantics.DEFAULT_TERRAIN_MODEL,),
                group_name=f"{stem}_ReconstructedDAT",
                work_dir=output_dir,
                worlds_install_dir=worlds_install_dir,
                output_filename=f"{stem}_{output_suffix}.ed",
                output_prefix=stem,
                include_validation_floor=include_validation_floor,
                validation_floor_name=f"{stem}_DestructableBrushValidationFloor",
                include_terrain_support_patch=include_terrain_support_patch,
                terrain_support_name_prefix=f"{stem}_TerrainSupport",
                terrain_support_margin=0.0,
                terrain_support_selection_mode=DAT_TO_ED_TERRAIN_SUPPORT_SELECTION_MODE_BY_LEVEL.get(
                    level_policy_key,
                    "connected_budget",
                ),
                terrain_support_radius=DAT_TO_ED_DEFAULT_TERRAIN_SUPPORT_RADIUS,
                terrain_support_brush_mode="single_polygon",
                terrain_support_thickness=128.0,
                terrain_support_max_polygons=terrain_support_brush_budget,
                include_physics_shell_patch=include_physics_shell_patch,
                physics_shell_name_prefix=f"{stem}_PhysicsShell",
                physics_shell_max_polygons=physics_shell_polygon_budget,
                physics_shell_thickness=16.0,
                physics_shell_focus_points=physics_shell_focus_points,
                physics_shell_focus_radius=physics_shell_focus_radius,
                physics_shell_focus_budget=physics_shell_focus_budget,
                physics_shell_focus_seed_radius=physics_shell_focus_seed_radius,
                physics_shell_stair_assembly_indices=physics_shell_stair_assembly_indices,
                include_door_objects=include_door_objects,
                door_source_ed_path=door_source_ed_path,
                include_airail_objects=include_airail_objects,
                airail_source_ed_path=airail_source_ed_path,
                include_sky_objects=include_sky_objects,
                sky_source_ed_path=sky_source_ed_path,
                include_sky_marker_brushes=False,
                include_sound_objects=include_sound_objects,
                sound_source_ed_path=sound_source_ed_path,
                include_collision_helper_objects=include_collision_helper_objects,
                include_collision_helper_brushes=include_collision_helper_brushes,
                collision_helper_source_ed_path=collision_helper_source_ed_path,
                include_trigger_helper_objects=include_trigger_helper_objects,
                include_trigger_helper_brushes=include_trigger_helper_brushes,
                trigger_helper_source_ed_path=trigger_helper_source_ed_path,
                include_low_risk_behavior_prop_objects=include_low_risk_behavior_prop_objects,
                low_risk_behavior_prop_source_ed_path=low_risk_behavior_prop_source_ed_path,
                include_wall_torch_objects=include_wall_torch_objects,
                wall_torch_source_ed_path=validated_light_fire_behavior_prop_source_ed_path,
                include_fire_objects=include_fire_objects,
                fire_source_ed_path=validated_light_fire_behavior_prop_source_ed_path,
                include_candle_prop_objects=include_candle_prop_objects,
                candle_prop_source_ed_path=candle_prop_behavior_prop_source_ed_path,
                include_brazier_objects=include_brazier_objects,
                brazier_source_ed_path=brazier_behavior_prop_source_ed_path,
                include_treasure_chest_objects=include_treasure_chest_objects,
                treasure_chest_source_ed_path=treasure_chest_behavior_prop_source_ed_path,
                include_prop_damager_objects=include_prop_damager_objects,
                prop_damager_source_ed_path=prop_damager_behavior_prop_source_ed_path,
                include_destructable_prop_objects=include_destructable_prop_objects,
                destructable_prop_source_ed_path=destructable_prop_behavior_prop_source_ed_path,
                include_destructable_brush_objects=include_destructable_brush_objects,
                include_terrain_support_source_coverage=include_terrain_support_patch,
                terrain_support_source_coverage_sample_grid=3,
                terrain_support_source_coverage_max_gaps=128,
                include_physics_shell_source_coverage=include_physics_shell_patch,
                max_processor_brushes=DAT_TO_ED_PROCESSOR_BRUSH_BUDGET,
                max_processor_polygons=DAT_TO_ED_PROCESSOR_POLYGON_BUDGET,
                block_unreconstructed_physics_shell=not allow_unreconstructed_physics_shell,
                max_models=512,
                max_model_points=16384,
                max_model_polygons=16384,
                max_total_points=65536,
                max_total_polygons=65536,
            )
            report_text = dat_compiler_strategy.format_full_world_skeleton_acceptance_report(report)
            report_path = os.path.join(output_dir, f"{stem}_dat_to_ed_report.txt")
            self._write_text_file(report_path, report_text)
            behavior_prop_report_path = ""
            if behavior_prop_validation_enabled and source_ed_oracle_path:
                behavior_prop_report = dat_compiler_strategy.build_behavior_prop_reconstruction_report(
                    source_dat_path=staged_dat,
                    source_ed_path=source_ed_oracle_path,
                )
                behavior_prop_report_text = dat_compiler_strategy.format_behavior_prop_reconstruction_report(
                    behavior_prop_report
                )
                behavior_prop_report_path = os.path.join(
                    output_dir,
                    f"{stem}_dat_to_ed_behavior_prop_validation_report.txt",
                )
                self._write_text_file(behavior_prop_report_path, behavior_prop_report_text)
            selection_report_path = os.path.join(output_dir, f"{stem}_dat_to_ed_selection_report.json")
            selection_report = dat_compiler_strategy.build_dat_to_ed_selection_report(
                source_dat_path=staged_dat,
                requested_model_names=model_names or (terrain_semantics.DEFAULT_TERRAIN_MODEL,),
                selected_model_names=report.selected_model_names,
                terrain_support_model_name=terrain_semantics.DEFAULT_TERRAIN_MODEL,
                include_terrain_support_patch=include_terrain_support_patch,
                physics_shell_model_name=terrain_semantics.PHYSICS_BSP_MODEL,
                include_physics_shell_patch=include_physics_shell_patch,
                include_airail_semantics=True,
                include_sky_semantics=True,
                include_sound_semantics=True,
                include_collision_semantics=True,
                include_trigger_semantics=True,
                include_skyboxes=False,
                max_models=512,
                max_model_points=16384,
                max_model_polygons=16384,
                max_total_points=65536,
                max_total_polygons=65536,
            )
            dat_compiler_strategy.write_dat_to_ed_selection_report(
                selection_report,
                selection_report_path,
                acceptance_report=report,
            )
            manifest_path = os.path.join(output_dir, f"{stem}_dat_to_ed_acceptance_manifest.json")
            dat_compiler_strategy.write_full_world_skeleton_acceptance_manifest(
                report,
                manifest_path,
                original_source=source_name,
                staged_source_dat_path=staged_dat,
                text_report_path=report_path,
                selection_report_path=selection_report_path,
                behavior_prop_report_path=behavior_prop_report_path,
            )
        except Exception as e:
            messagebox.showerror("DAT to ED generation failed", str(e))
            return

        if report.blockers:
            messagebox.showerror(
                "DAT to ED generation blocked",
                "The DAT to ED report contains blockers.\n\n"
                f"Report:\n{report_path}\n\n"
                f"Selection report:\n{selection_report_path}\n\n"
                f"Manifest:\n{manifest_path}\n\n"
                + "\n".join(f"- {item}" for item in report.blockers[:8]),
            )
            return

        messagebox.showinfo(
            "DAT to ED generation complete",
            "Generated a DEDit ED candidate from the active DAT.\n\n"
            f"ED:\n{report.generated_ed_path}\n\n"
            f"Report:\n{report_path}\n\n"
            f"Behavior prop validation report:\n{behavior_prop_report_path or 'not generated'}\n\n"
            f"Selection report:\n{selection_report_path}\n\n"
            f"Manifest:\n{manifest_path}\n\n"
            f"Selected models: {len(report.selected_model_names)}; "
            f"generated brushes/objects: {report.object_count}; "
            f"polygons: {report.polygon_count}\n"
            f"Door/RotatingDoor objects: {'included' if getattr(report, 'include_door_objects', False) else 'not included'}\n\n"
            f"AIRail objects: {'included' if getattr(report, 'include_airail_objects', False) else 'not included'}\n\n"
            f"Sky objects: {'included' if getattr(report, 'include_sky_objects', False) else 'not included'}\n\n"
            f"SkyMarker Brushes: {'included' if getattr(report, 'include_sky_marker_brushes', False) else 'not included'}\n\n"
            f"AmbientSound objects: {'included' if getattr(report, 'include_sound_objects', False) else 'not included'}\n\n"
            f"Collision helper objects: {'included' if getattr(report, 'include_collision_helper_objects', False) else 'not included'}; "
            f"Brushes: {'included' if getattr(report, 'include_collision_helper_brushes', False) else 'not included'}\n\n"
            f"Trigger helper objects: {'included' if getattr(report, 'include_trigger_helper_objects', False) else 'not included'}; "
            f"Brushes: {'included' if getattr(report, 'include_trigger_helper_brushes', False) else 'not included'}\n\n"
            f"Low-risk behavior prop objects: {'included' if getattr(report, 'include_low_risk_behavior_prop_objects', False) else 'not included'}\n\n"
            f"Validated light/fire behavior prop objects: {'included' if getattr(report, 'include_wall_torch_objects', False) or getattr(report, 'include_fire_objects', False) else 'not included'}\n\n"
            f"TreasureChest objects: {'included' if getattr(report, 'include_treasure_chest_objects', False) else 'not included'}\n\n"
            f"PropDamager objects: {'included' if getattr(report, 'include_prop_damager_objects', False) else 'not included'}\n\n"
            f"DestructableProp objects: {'included' if getattr(report, 'include_destructable_prop_objects', False) else 'not included'}\n\n"
            f"DestructableBrush objects: {'included' if getattr(report, 'include_destructable_brush_objects', False) else 'not included'}\n\n"
            f"Behavior prop validation profile: {behavior_prop_validation_profile_key if behavior_prop_validation_enabled else 'not included'}\n\n"
            f"Stair assemblies requested: {', '.join(str(index) for index in physics_shell_stair_assembly_indices) or 'none'}; "
            f"selected: {', '.join(str(index) for index in getattr(report, 'physics_shell_selected_stair_assembly_indices', ())) or 'none'}; "
            f"rejected: {', '.join(str(index) for index in getattr(report, 'physics_shell_rejected_stair_assembly_indices', ())) or 'none'}\n\n"
            "Next: open the ED in old DEDit, save it, process it with LithTech 2.1 "
            "Processor.exe, then fresh-load the DAT in game.",
        )

    @staticmethod
    def _dat_to_ed_output_stem(source_name: str) -> str:
        base = os.path.splitext(os.path.basename(str(source_name or "level")))[0] or "level"
        safe = re.sub(r"[^A-Za-z0-9_]+", "_", base).strip("_")
        return safe or "level"

    def _airail_source_ed_oracle_path(self, source_name: str, *, level_path: str = "") -> str:
        return self._source_ed_oracle_path(source_name, level_path=level_path)

    def _source_ed_oracle_path(self, source_name: str, *, level_path: str = "") -> str:
        stem = self._dat_to_ed_output_stem(source_name)
        candidates = []
        if level_path:
            candidates.append(os.path.join(os.path.dirname(os.path.abspath(level_path)), f"{stem}.ED"))
        game_data_dir = getattr(self.cfg, "game_data_dir", None)
        if game_data_dir:
            candidates.append(os.path.join(game_data_dir, "WORLDS", f"{stem}.ED"))
        editor_dir = getattr(self.cfg, "editor_dir", None) or EDITOR_ROOT
        candidates.append(os.path.join(editor_dir, "mm9_data", "WORLDS", f"{stem}.ED"))
        candidates.append(os.path.join(EDITOR_ROOT, "mm9_data", "WORLDS", f"{stem}.ED"))
        seen = set()
        for candidate in candidates:
            path = os.path.abspath(candidate)
            key = path.lower()
            if key in seen:
                continue
            seen.add(key)
            if os.path.exists(path):
                return path
        return ""

    @staticmethod
    def _source_ed_has_object_class(source_ed_path: str, class_name: str) -> bool:
        path = os.path.abspath(source_ed_path) if source_ed_path else ""
        if not path or not os.path.exists(path):
            return False
        try:
            report = dat_legacy_ed.load_legacy_ed_object_scan_report(path)
        except Exception:
            return False
        target = str(class_name or "").strip().lower()
        return any(
            str(name or "").strip().lower() == target and int(count) > 0
            for name, count in (getattr(report, "class_counts", {}) or {}).items()
        )

    @staticmethod
    def _dat_model_is_pure_airail_helper(model: Any) -> bool:
        helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
        return (
            int(helper_roles.get("aiRail", 0)) > 0
            and set(helper_roles.keys()) == {"aiRail"}
            and terrain_semantics.model_has_only_helper_textures(model)
        )

    @staticmethod
    def _dat_model_has_sky_visibility_helper(model: Any) -> bool:
        helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
        return int(helper_roles.get("skyVisibility", 0)) > 0

    @staticmethod
    def _dat_model_has_sound_helper(model: Any) -> bool:
        helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
        return int(helper_roles.get("sound", 0)) > 0

    @staticmethod
    def _dat_model_is_pure_collision_helper(model: Any) -> bool:
        helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
        return (
            int(helper_roles.get("collision", 0)) > 0
            and set(helper_roles.keys()).issubset({"collision", "sprite"})
            and terrain_semantics.model_has_only_helper_textures(model)
        )

    @staticmethod
    def _dat_model_is_pure_trigger_helper(model: Any) -> bool:
        helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
        return (
            int(helper_roles.get("trigger", 0)) > 0
            and set(helper_roles.keys()) == {"trigger"}
            and terrain_semantics.model_has_only_helper_textures(model)
        )

    @staticmethod
    def _write_text_file(path: str, text: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")

    @staticmethod
    def _default_dat_to_ed_model_names(bsp_world: Any) -> tuple:
        return terrain_semantics.default_dat_to_ed_model_names(bsp_world)

    @staticmethod
    def _dat_object_model_names_for_class(level: Any, bsp_world: Any, class_name: str) -> tuple:
        world_models = getattr(bsp_world, "world_models", ()) or ()
        model_names_by_key = {
            str(getattr(model, "name", "") or "").lower(): str(getattr(model, "name", "") or "")
            for model in world_models
            if str(getattr(model, "name", "") or "")
        }
        objects = tuple(getattr(getattr(level, "world", None), "objects", ()) or ())
        if not objects:
            try:
                data = level.source_bytes()
                header = patcher.Header.parse(data)
                objects, _object_end = patcher.parse_objects(data, header.obj_pos)
            except Exception:
                objects = ()

        selected: List[str] = []
        seen = set()
        for obj in objects:
            if str(getattr(obj, "type_str", "") or "") != str(class_name or ""):
                continue
            name = str(obj.get("Name", "") or "")
            key = name.lower()
            if not key or key in seen or key not in model_names_by_key:
                continue
            selected.append(model_names_by_key[key])
            seen.add(key)
        return tuple(selected)


    def cmd_import_static_prefab_bsp(self) -> None:
        if not getattr(self, "active", None):
            messagebox.showwarning("No level", "Open a level from WORLDS.REZ first.")
            return
        L = self.active
        bsp_world = L.get_bsp()
        if bsp_world is None:
            messagebox.showerror("No BSP", "This level's BSP geometry could not be parsed.")
            return

        editor_dir = getattr(self.cfg, "editor_dir", None) or EDITOR_ROOT
        start_dir = os.path.join(editor_dir, "mm9_data", "PreFabs")
        path = filedialog.askopenfilename(
            title="Import static prefab BSP",
            initialdir=start_dir if os.path.isdir(start_dir) else editor_dir,
            filetypes=[("DAT files", "*.dat"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            info = prefab_inspector.inspect_prefab(path)
            default_name = prefab_import.suggest_import_name(L.preview_bsp() or bsp_world, path)
        except Exception as e:
            messagebox.showerror("Prefab import failed", str(e))
            return

        name = simpledialog.askstring(
            "Import Static Prefab BSP",
            "New BSP model name:",
            initialvalue=default_name,
            parent=self.root,
        )
        if name is None:
            return
        name = str(name).strip()
        if not name:
            messagebox.showerror("Prefab import failed", "The BSP model name cannot be empty.")
            return

        try:
            # Validate before entering click placement so unsupported prefabs
            # fail at the dialog, not after the user picks a surface.
            prefab_import.build_static_import_plan(
                L.preview_bsp() or bsp_world,
                path,
                new_name=name,
                target_pos=(0.0, 0.0, 0.0),
            )
        except Exception as e:
            messagebox.showerror("Prefab import failed", str(e))
            return

        collision_options = _ask_prefab_collision_options(self.root)
        if collision_options is None:
            return
        collision_mode = str(collision_options.get("collision_mode", "none"))
        collision_thickness = float(collision_options.get("collision_thickness", 8.0))
        collision_segment_length = float(collision_options.get("collision_segment_length", 512.0))
        if collision_mode in {"box_approx", "invisible_bsp"}:
            try:
                prefab_import.build_static_import_plan(
                    L.preview_bsp() or bsp_world,
                    path,
                    new_name=name,
                    target_pos=(0.0, 0.0, 0.0),
                    collision_mode=collision_mode,
                    collision_thickness=collision_thickness,
                    collision_segment_length=collision_segment_length,
                    target_dat_bytes=L.source_bytes(),
                )
            except Exception as e:
                messagebox.showerror("Prefab collision failed", str(e))
                return

        self._pending_template = None
        self._pending_kind = "import_prefab_bsp"
        self._pending_prefab_path = path
        self._pending_prefab_name = name
        self._pending_prefab_roles = None
        self._pending_prefab_collision_mode = collision_mode
        self._pending_prefab_collision_thickness = collision_thickness
        self._pending_prefab_collision_segment_length = collision_segment_length
        if self.view3d is not None:
            self.view3d.set_place_mode(True)
        model_roles = ", ".join(f"{k}={v}" for k, v in sorted(info.model_roles.items()))
        collision_text = (
            (
                f"Hidden box collision helper will also be imported "
                f"(thickness {collision_thickness:g}, max segment {collision_segment_length:g})."
            )
            if collision_mode == "box_approx"
            else (
                "Diagnostic duplicate-geometry collision helper will also be imported."
                if collision_mode == "invisible_bsp"
                else "No collision helper will be imported."
            )
        )
        messagebox.showinfo(
            "Place prefab",
            f"Click a surface in the 3-D view to place {name!r}.\n\n"
            f"Prefab models: {info.model_count}; roles: {model_roles or 'unknown'}.\n"
            f"{collision_text}",
        )

    def _show_text_dialog(self, title: str, text: str) -> None:
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg="#0e1116")
        win.geometry("820x560")

        frame = tk.Frame(win, bg="#0e1116")
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        yscroll = tk.Scrollbar(frame, orient="vertical")
        yscroll.pack(side="right", fill="y")
        widget = tk.Text(
            frame,
            bg="#11151c",
            fg="#dde3ea",
            insertbackground="#dde3ea",
            relief="flat",
            wrap="none",
            yscrollcommand=yscroll.set,
        )
        widget.pack(side="left", fill="both", expand=True)
        yscroll.config(command=widget.yview)
        widget.insert("1.0", text)
        widget.configure(state="disabled")

        buttons = tk.Frame(win, bg="#0e1116")
        buttons.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(buttons, text="Close", command=win.destroy).pack(side="right")

    def _on_save_as_preset(self) -> None:
        """Open the New Preset dialog pre-filled from the selected object."""
        obj = self.props_panel.current_obj
        if obj is None:
            return
        overrides = self.props_panel.current_overrides_snapshot()
        result = EditPresetDialog.ask(
            self.root,
            catalog_classes    = self.catalog.get("classes", {}),
            initial_base_class = obj.type_str,
            initial_overrides  = overrides,
        )
        if result is None:
            return
        try:
            self.preset_store.add_or_update(result)
            self.preset_store.save()
            messagebox.showinfo(
                "Preset saved",
                f"Preset {result.name!r} saved.\n\n"
                f"It will appear under '★ My Presets' in the Add Object dialog.")
            # Refresh the level panel so the new preset is available immediately
            self.level_panel._preset_store = self.preset_store
        except Exception as e:
            messagebox.showerror("Save preset failed", str(e))

    def cmd_new_preset(self) -> None:
        """Open the New Preset dialog from the Presets menu."""
        obj = self.props_panel.current_obj
        overrides = self.props_panel.current_overrides_snapshot() if obj else {}
        base_class = obj.type_str if obj else "Prop"
        result = EditPresetDialog.ask(
            self.root,
            catalog_classes    = self.catalog.get("classes", {}),
            initial_base_class = base_class,
            initial_overrides  = overrides,
        )
        if result is None:
            return
        try:
            self.preset_store.add_or_update(result)
            self.preset_store.save()
            messagebox.showinfo("Preset saved",
                                f"Preset {result.name!r} created.")
            self.level_panel._preset_store = self.preset_store
        except Exception as e:
            messagebox.showerror("Save preset failed", str(e))

    def cmd_manage_presets(self) -> None:
        """Open the Manage Presets dialog."""
        ManagePresetsDialog.open(
            self.root, self.preset_store,
            self.catalog.get("classes", {}))
        # After the dialog closes the store is up-to-date; refresh the panel
        self.level_panel._preset_store = self.preset_store

    def _on_3d_clicked_for_place(self, wx: float, wy: float, wz: float) -> None:
        """Place the pending object at the exact BSP hit point from the 3-D view.

        This preserves the clicked Y coordinate, which is important when
        placing objects on tables, platforms, balconies,
        or any other geometry where vertical precision matters.
        """
        if getattr(self, "_pending_kind", None) == "clone_door":
            self._place_pending_door_clone_at_pos((float(wx), float(wy), float(wz)))
            return
        if getattr(self, "_pending_kind", None) == "import_prefab_bsp":
            self._place_pending_prefab_bsp_at_pos((float(wx), float(wy), float(wz)))
            return
        if not getattr(self, "_pending_template", None):
            return
        self._place_pending_at_pos([float(wx), float(wy), float(wz)])

    def _place_pending_door_clone_at_pos(self, new_pos: tuple) -> None:
        L = self.active
        source_name = getattr(self, "_pending_door_source", "")
        new_name = getattr(self, "_pending_door_name", "")
        include_pair = bool(getattr(self, "_pending_door_include_pair", True))
        op = P.CloneDoorOp(
            source_name=source_name,
            new_name=new_name,
            target_pos=tuple(float(v) for v in new_pos),
            include_pair=include_pair,
        )
        try:
            # Validate now so placement errors stay near the click that caused them.
            op.build_plan(L, L.materialize().objects)
        except Exception as e:
            messagebox.showerror("Clone door failed", str(e))
            return
        L.append_op(op)
        mat = L.materialize()
        selected_index = next(
            (i for i, obj in enumerate(mat.objects) if (obj.get("Name") or "") == new_name),
            len(mat.objects) - 1,
        )
        self._pending_kind = None
        self._pending_door_source = ""
        self._pending_door_name = ""
        self._pending_door_include_pair = True
        if self.view3d is not None:
            self.view3d.set_place_mode(False)
        self._refresh_after_edit(selected_index)

    def _place_pending_prefab_bsp_at_pos(self, new_pos: tuple) -> None:
        L = self.active
        prefab_path = getattr(self, "_pending_prefab_path", "")
        new_name = getattr(self, "_pending_prefab_name", "")
        include_roles = getattr(self, "_pending_prefab_roles", None)
        collision_mode = getattr(self, "_pending_prefab_collision_mode", "none")
        collision_thickness = float(getattr(self, "_pending_prefab_collision_thickness", 8.0))
        collision_segment_length = float(getattr(self, "_pending_prefab_collision_segment_length", 512.0))
        op = P.ImportPrefabBspOp(
            prefab_path=prefab_path,
            new_name=new_name,
            target_pos=tuple(float(v) for v in new_pos),
            include_roles=include_roles,
            collision_mode=collision_mode,
            collision_thickness=collision_thickness,
            collision_segment_length=collision_segment_length,
        )
        try:
            # Validate against a preview that includes existing pending prefab
            # imports, so same-name imports are caught before save preview.
            target_bsp = L.preview_bsp() or L.get_bsp()
            prefab_import.build_static_import_plan(
                target_bsp,
                prefab_path,
                new_name=new_name,
                target_pos=new_pos,
                include_roles=include_roles,
                collision_mode=collision_mode,
                collision_thickness=collision_thickness,
                collision_segment_length=collision_segment_length,
                target_dat_bytes=L.source_bytes(),
            )
        except Exception as e:
            messagebox.showerror("Prefab import failed", str(e))
            return

        L.append_op(op)
        mat = L.editor_materialize() if hasattr(L, "editor_materialize") else L.materialize()
        helper_index = next(
            (i for i, obj in enumerate(mat.objects) if (obj.get("Name") or "") == new_name),
            len(mat.objects) - 1,
        )
        self._pending_kind = None
        self._pending_prefab_path = ""
        self._pending_prefab_name = ""
        self._pending_prefab_roles = None
        self._pending_prefab_collision_mode = "none"
        self._pending_prefab_collision_thickness = 8.0
        self._pending_prefab_collision_segment_length = 512.0
        self._selected_world_index = helper_index
        if self.view3d is not None:
            self.view3d.set_place_mode(False)
        self._refresh_after_edit(helper_index)

    def _place_pending_at_pos(self, new_pos: List[float]) -> None:
        """Create the pending AddOp at an already-resolved XYZ position."""
        if not getattr(self, "_pending_template", None):
            return
        L = self.active
        # Build overrides
        overrides = {"Pos": new_pos}
        # Auto-name to avoid collisions — scan the materialized world so
        # that pending (not-yet-committed) additions are included in the check.
        base = self._pending_template.type_str
        mat = L.materialize()
        existing = {(o.get("Name") or "") for o in mat.objects}
        i = 1
        while f"{base}_new{i}" in existing:
            i += 1
        overrides["Name"] = f"{base}_new{i}"
        # If we placed by filename, swap the Filename field
        if self._pending_kind == "filename":
            overrides["Filename"] = self._pending_filename
        # If we placed from a user preset, apply its overrides on top
        if self._pending_kind == "preset":
            preset = getattr(self, "_pending_preset", None)
            if preset:
                preset_overrides = dict(preset.overrides)
                name_prefix = str(preset_overrides.pop("__name_prefix", "") or "")
                overrides.update(preset_overrides)
                if name_prefix and "Name" not in preset_overrides:
                    j = 1
                    while f"{name_prefix}{j}" in existing:
                        j += 1
                    overrides["Name"] = f"{name_prefix}{j}"

        # Phase 6: encode NPCNbr override and attach rude registration dict
        rude_data = None
        rc = getattr(self, "_pending_rude_config", None)
        if rc and rc.get("mode") == "fresh":
            nbr = rc["npc_nbr"]
            # NPCNbr is stored as IEEE-754 float bits packed into a LongInt slot
            overrides["NPCNbr"] = struct.unpack("<I", struct.pack("<f", float(nbr)))[0]
            # Strip "mode" key — AddOp.rude must match RudeRegistration fields
            rude_data = {k: v for k, v in rc.items() if k != "mode"}
            # Advance the project's counter when the suggested number was used
            if nbr >= self.project.next_npc_nbr:
                self.project.next_npc_nbr = nbr + 1

        op = P.AddOp(template=copy.deepcopy(self._pending_template),
                     overrides=overrides,
                     rude=rude_data)
        L.append_op(op)
        mat = L.materialize()
        new_name = overrides["Name"]
        new_index = next(
            (i for i, obj in enumerate(mat.objects) if (obj.get("Name") or "") == new_name),
            len(mat.objects) - 1,
        )
        self._refresh_after_edit(new_index)

    def _on_property_edited(self, name_or_dict: str | Dict[str, Any], new_value: Any = None) -> None:
        self._flush_view_transforms()
        L = self.active
        obj = self.props_panel.current_obj
        if obj is None: return
        selected_idx = getattr(self, "_selected_world_index", None)

        if isinstance(name_or_dict, dict):
            overrides = name_or_dict
        else:
            overrides = {name_or_dict: new_value}

        if selected_idx is not None:
            prefab_op = L.prefab_import_for_materialized(selected_idx)
            if prefab_op is not None:
                for name, val in overrides.items():
                    if name == "Pos":
                        prefab_op.target_pos = tuple(float(v) for v in val)
                    elif name == "Rotation":
                        vals = list(val) if isinstance(val, (list, tuple)) else []
                        vals = (vals + [0.0, 0.0, 0.0, 0.0])[:4]
                        prefab_op.target_yaw = float(vals[1])
                L.clear_redo()
                self._refresh_after_edit(selected_idx)
                return

            baseline_idx = L.existing_index_for_materialized(selected_idx)
            if baseline_idx is not None:
                L.append_op(P.EditOp(target_index=baseline_idx,
                                     overrides=overrides))
                self._refresh_after_edit(selected_idx)
                return

            add_offset = L.add_offset_for_materialized(selected_idx)
            if add_offset is not None:
                adds = [op for op in L.ops if isinstance(op, P.AddOp)]
                if add_offset < len(adds):
                    for name, val in overrides.items():
                        adds[add_offset].overrides[name] = val
                    L.clear_redo()
                    self._refresh_after_edit(selected_idx)
                    return

            pending = L.pending_add_offset_for_materialized(selected_idx)
            if pending is not None:
                pending_op, object_offset = pending
                if isinstance(pending_op, P.CloneDoorOp):
                    for name, val in overrides.items():
                        if name == "Pos":
                            pending_op.retarget_from_object(
                                L,
                                L.objects_before_op(pending_op),
                                object_offset,
                                tuple(float(v) for v in val),
                            )
                        elif name == "Rotation":
                            pending_op.rerotate_from_object(
                                L,
                                L.objects_before_op(pending_op),
                                object_offset,
                                tuple(float(v) for v in val),
                            )
                    L.clear_redo()
                    self._refresh_after_edit(selected_idx)
                    return

        # Legacy fallback for callers that provide an object without the
        # selected materialized index.
        mat = L.editor_materialize() if hasattr(L, "editor_materialize") else L.materialize()
        for mat_idx, candidate in enumerate(mat.objects):
            if candidate != obj:
                continue
            baseline_idx = L.existing_index_for_materialized(mat_idx)
            if baseline_idx is not None:
                L.append_op(P.EditOp(target_index=baseline_idx,
                                     overrides=overrides))
                selected_idx = mat_idx
                break
            add_offset = L.add_offset_for_materialized(mat_idx)
            adds = [op for op in L.ops if isinstance(op, P.AddOp)]
            if add_offset is not None and add_offset < len(adds):
                for name, val in overrides.items():
                    adds[add_offset].overrides[name] = val
                L.clear_redo()
                selected_idx = mat_idx
                break
        self._refresh_after_edit(selected_idx)

    def _on_object_deleted(self) -> None:
        L = self.active
        obj = self.props_panel.current_obj
        if obj is None: return
        selected_idx = getattr(self, "_selected_world_index", None)
        if selected_idx is not None:
            prefab_op = L.prefab_import_for_materialized(selected_idx)
            if prefab_op is not None:
                try:
                    L.ops.remove(prefab_op)
                except ValueError:
                    pass
                L.clear_redo()
                self.props_panel.show(None)
                self._selected_world_index = None
                self._refresh_after_edit(None)
                return

            baseline_idx = L.existing_index_for_materialized(selected_idx)
            if baseline_idx is not None:
                L.append_op(P.DeleteOp(target_index=baseline_idx))
            else:
                pending = L.pending_add_offset_for_materialized(selected_idx)
                if pending is not None:
                    pending_op, _object_offset = pending
                    if isinstance(pending_op, P.CloneDoorOp):
                        try:
                            L.ops.remove(pending_op)
                        except ValueError:
                            pass
                        L.clear_redo()
                    else:
                        add_offset = L.add_offset_for_materialized(selected_idx)
                        if add_offset is None:
                            add_offset = -1
                        add_seen = 0
                        for i, op in enumerate(list(L.ops)):
                            if not isinstance(op, P.AddOp):
                                continue
                            if add_seen == add_offset:
                                del L.ops[i]
                                L.clear_redo()
                                break
                            add_seen += 1
        else:
            # Legacy fallback for callers that provide an object without
            # preserving the selected materialized index.
            for i, op in enumerate(list(L.ops)):
                if isinstance(op, P.AddOp):
                    if op.overrides.get("Name") == obj.get("Name"):
                        del L.ops[i]
                        L.clear_redo()
                        break
        self.props_panel.show(None)
        self._selected_world_index = None
        self._refresh_after_edit(None)

    def _on_object_positioned(self, world_index: int,
                              new_wx: float, new_wy: float,
                              new_wz: float) -> None:
        """Called by the 3-D view for exact object movement.

        The supplied Y coordinate is preserved so keyboard nudges and 3-D
        drags keep deliberate height adjustments intact.
        """
        L = getattr(self, "active", None)
        if not L or not L.world:
            return
        new_pos = (float(new_wx), float(new_wy), float(new_wz))

        baseline_idx = L.existing_index_for_materialized(world_index)
        refresh_clone_preview = False
        prefab_op = L.prefab_import_for_materialized(world_index)
        if prefab_op is not None:
            prefab_op.target_pos = new_pos
            L.clear_redo()
            refresh_clone_preview = True
        else:
            if baseline_idx is not None:
                L.coalesce_move_op(baseline_idx, new_pos=new_pos)
            else:
                pending = L.pending_add_offset_for_materialized(world_index)
                if pending is not None:
                    pending_op, object_offset = pending
                    if isinstance(pending_op, P.CloneDoorOp):
                        pending_op.retarget_from_object(
                            L,
                            L.objects_before_op(pending_op),
                            object_offset,
                            new_pos,
                        )
                        L.clear_redo()
                        refresh_clone_preview = True
                    else:
                        add_offset = L.add_offset_for_materialized(world_index)
                        adds = [op for op in L.ops if isinstance(op, P.AddOp)]
                        if add_offset is not None and add_offset < len(adds):
                            adds[add_offset].overrides["Pos"] = list(new_pos)
                            L.clear_redo()
        if refresh_clone_preview:
            self._refresh_after_edit(world_index)
            return
        if getattr(self, "_selected_world_index", None) == world_index:
            self._show_selected_materialized(world_index)
        self._update_history_menu()

    def _on_object_rotated(self, world_index: int, new_rot: tuple) -> None:
        """Called by the 3-D view when the selected object's yaw changes."""
        L = getattr(self, "active", None)
        if not L or not L.world:
            return
        rot = tuple(float(v) for v in new_rot)

        baseline_idx = L.existing_index_for_materialized(world_index)
        refresh_clone_preview = False
        prefab_op = L.prefab_import_for_materialized(world_index)
        if prefab_op is not None:
            prefab_op.target_yaw = float(rot[1])
            L.clear_redo()
            refresh_clone_preview = True
        else:
            if baseline_idx is not None:
                mat = L.materialize()
                if not (0 <= world_index < len(mat.objects)):
                    return
                old_pos = mat.objects[world_index].get("Pos")
                if old_pos is None:
                    return
                pos = (float(old_pos[0]), float(old_pos[1]), float(old_pos[2]))
                L.coalesce_move_op(baseline_idx, new_pos=pos, new_rot=rot)
            else:
                pending = L.pending_add_offset_for_materialized(world_index)
                if pending is not None:
                    pending_op, object_offset = pending
                    if isinstance(pending_op, P.CloneDoorOp):
                        pending_op.rerotate_from_object(
                            L,
                            L.objects_before_op(pending_op),
                            object_offset,
                            rot,
                        )
                        L.clear_redo()
                        refresh_clone_preview = True
                    else:
                        add_offset = L.add_offset_for_materialized(world_index)
                        adds = [op for op in L.ops if isinstance(op, P.AddOp)]
                        if add_offset is not None and add_offset < len(adds):
                            adds[add_offset].overrides["Rotation"] = list(rot)
                            L.clear_redo()
        if refresh_clone_preview:
            self._refresh_after_edit(world_index)
            return
        if getattr(self, "_selected_world_index", None) == world_index:
            self._show_selected_materialized(world_index)
        self._update_history_menu()

    def _on_object_elevated(self, world_index: int, new_y: float) -> None:
        """Fallback for vertical commits from the 3-D view.

        Exact XYZ movement normally uses _on_object_positioned().  This path
        exists for callers that only report a new Y value.
        """
        L = getattr(self, "active", None)
        if not L or not L.world:
            return

        baseline_idx = L.existing_index_for_materialized(world_index)
        refresh_clone_preview = False
        prefab_op = L.prefab_import_for_materialized(world_index)
        if prefab_op is not None:
            old = prefab_op.target_pos
            prefab_op.target_pos = (float(old[0]), float(new_y), float(old[2]))
            L.clear_redo()
            refresh_clone_preview = True
        else:
            if baseline_idx is not None:
                mat = L.materialize()
                if not (0 <= world_index < len(mat.objects)):
                    return
                old_pos = mat.objects[world_index].get("Pos")
                if old_pos is None:
                    return
                new_pos = (float(old_pos[0]), float(new_y), float(old_pos[2]))
                L.coalesce_move_op(baseline_idx, new_pos=new_pos)
            else:
                # Pending added object: update the AddOp/CloneDoorOp target position.
                pending = L.pending_add_offset_for_materialized(world_index)
                if pending is not None:
                    pending_op, object_offset = pending
                    if isinstance(pending_op, P.CloneDoorOp):
                        mat = L.materialize()
                        old = mat.objects[world_index].get("Pos")
                        if old is not None:
                            pending_op.retarget_from_object(
                                L,
                                L.objects_before_op(pending_op),
                                object_offset,
                                (float(old[0]), float(new_y), float(old[2])),
                            )
                            L.clear_redo()
                            refresh_clone_preview = True
                    else:
                        add_offset = L.add_offset_for_materialized(world_index)
                        adds = [op for op in L.ops if isinstance(op, P.AddOp)]
                        if add_offset is not None and add_offset < len(adds):
                            ov  = adds[add_offset].overrides
                            old = ov.get("Pos", [0.0, 0.0, 0.0])
                            ov["Pos"] = [float(old[0]), float(new_y), float(old[2])]
                            L.clear_redo()

        if refresh_clone_preview:
            self._refresh_after_edit(world_index)
            return
        if getattr(self, "_selected_world_index", None) == world_index:
            self._show_selected_materialized(world_index)
        self._update_history_menu()

    def _on_save_committed(self, log_lines: List[str]) -> None:
        # Promote saved materialized worlds to the new in-memory baseline, then
        # clear pending ops so saved additions stay visible in the session.
        for L in self.project.levels:
            if L.ops:
                L.world = L.materialize()
            L.ops.clear()
            L.redo_ops.clear()
        self._refresh_all_views()
        self.level_panel.set_active_level(self.active)
        self._update_history_menu()
        messagebox.showinfo("Saved", "\n".join(log_lines))

    # ---------- helpers ----------

    def _asset_dir(self, archive_key: str,
                   virtual_root: str, extensions) -> Optional[str]:
        """Return a cached REZ-backed asset directory."""
        try:
            return self.resources.cache_archive_tree(
                archive_key=archive_key,
                virtual_root=virtual_root,
                extensions=extensions,
            )
        except Exception as exc:
            print(
                f"[resources] cache extraction failed for {archive_key}: {exc}",
                file=sys.stderr,
            )
            return None

    def _load_world_from_resource_level(
        self,
        level_name: str,
    ) -> Optional[patcher.World]:
        """Load a catalog source level via GameResources.

        Catalogs built from REZ still record source levels as names like
        ``BOOTCAMP.DAT``.  The game archive stores the same level as a virtual
        resource such as ``WORLDS/BOOTCAMP``.  GameResources accepts either
        form, and this helper keeps the temporary-file bridge in one place
        until mm9_patch can parse from bytes directly.
        """
        if not level_name:
            return None
        normalized = str(level_name).replace("\\", "/").strip().strip("/")
        if not normalized.upper().startswith("WORLDS/"):
            normalized = f"WORLDS/{normalized}"
        try:
            data = self.resources.read_bytes(normalized)
        except Exception:
            return None
        if len(data) < 4 or struct.unpack_from("<I", data, 0)[0] != 66:
            return None

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(prefix="mm9_res_world_", suffix=".DAT")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            return patcher.World.load(tmp_path)
        except Exception:
            return None
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _suggest_next_npc_nbr(self) -> Optional[int]:
        """Return the first unused normal NPC<N> dialogue number.

        Works through GameResources so it can inspect RUDE.REZ.  The special
        journal/note/award files
        NPC997-999 are intentionally outside the normal allocation range.
        """
        try:
            rude_paths = self.resources.list("RUDE/")
        except Exception:
            return None
        used = set()
        rx = re.compile(r"^RUDE/NPC(\d+)(?:\.RUDE)?$", re.IGNORECASE)
        for path in rude_paths:
            m = rx.match(path)
            if not m:
                continue
            n = int(m.group(1))
            if 1 <= n <= 996:
                used.add(n)
        for n in range(1, 997):
            if n not in used:
                return n
        return None

    def _find_template_for_class(self, class_name: str) -> Optional[patcher.WorldObject]:
        # Pass 1: any already-loaded level (instant — no I/O)
        for L in self.project.levels:
            if L.world is None:
                continue
            for o in L.world.objects:
                if o.type_str == class_name:
                    return o

        e = self.catalog["classes"].get(class_name)
        if not e:
            return None
        object_lto_template = self._template_from_object_lto_catalog(class_name, e)
        if object_lto_template is not None:
            return object_lto_template

        preferred = e["template"]["source_level"]
        all_levels = [preferred] + [lvl for lvl in e.get("levels", [])
                                    if lvl != preferred]

        # Pass 2: game resource provider (REZ-backed)
        for lvl_name in all_levels:
            tmp_world = self._load_world_from_resource_level(lvl_name)
            if tmp_world is None:
                continue
            for o in tmp_world.objects:
                if o.type_str == class_name:
                    return o

        return None

    def _template_from_object_lto_catalog(
        self,
        class_name: str,
        entry: Dict[str, Any],
    ) -> Optional[patcher.WorldObject]:
        template = entry.get("template") or {}
        if template.get("source_level") != "object.lto":
            return None
        props = []
        for item in template.get("properties") or []:
            try:
                props.append(patcher.Property(
                    str(item["name"]),
                    int(item["code"]),
                    int(item.get("flags") or 0),
                    item.get("value"),
                ))
            except Exception:
                continue
        if not props:
            return None
        return patcher.WorldObject(class_name, props)

    def _find_template_for_filename(self, filename: str) -> Optional[patcher.WorldObject]:
        target = filename.lower()
        _prop_types = ("Prop", "WorldObject", "DestructableProp")

        # Pass 1: already-loaded levels
        for L in self.project.levels:
            for o in L.world.objects:
                if o.type_str in _prop_types:
                    if (o.get("Filename") or "").lower() == target:
                        return o

        e = self.catalog["filenames"].get(target)
        if not e:
            # No catalog entry — fall through to generic Prop below
            pass
        else:
            lvl_list = e["levels"]

            # Pass 2: game resource provider (REZ-backed)
            for lvl in lvl_list:
                w = self._load_world_from_resource_level(lvl)
                if w is None:
                    continue
                for o in w.objects:
                    if o.type_str in _prop_types:
                        if (o.get("Filename") or "").lower() == target:
                            return o

        # As a last resort: any Prop, with the filename overridden later
        for L in self.project.levels:
            for o in L.world.objects:
                if o.type_str == "Prop":
                    return o
        return None

    # ---------- about ----------

    def _about(self) -> None:
        messagebox.showinfo(
            "About",
            "MM9 Mod Editor\n\n"
            "3-D placement editor for Might and Magic IX.\n"
            "Catalog-driven, multi-level, explicit RUDE workflow.\n"
            "User presets for quick re-placement of custom objects.\n\n"
            "Built atop mm9_patch.py and rude_add_npc.py.")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main(argv=None):
    here = EDITOR_ROOT
    p = argparse.ArgumentParser(
        description="MM9 Mod Editor -- edit MM9 levels directly from a "
                    "game data/*.REZ install.")
    p.add_argument("--catalog", default=os.path.join(here, "catalog", "data", "catalog.json"),
                   help="Path to catalog.json (built automatically on first run).")
    p.add_argument("--game-root", default=None,
                   help="Path to the Might and Magic IX install folder. "
                        "If omitted, the editor checks its own folder and parent.")
    args = p.parse_args(argv)

    # ---------- Resolve editor-local paths ----------
    try:
        paths = autodetect.detect(here, game_root=args.game_root)
    except autodetect.GameNotFoundError as e:
        try:
            import tkinter as _tk
            import tkinter.messagebox as _mb
            _r = _tk.Tk(); _r.withdraw()
            _mb.showerror("MM9 Mod Editor -- setup problem", str(e))
            _r.destroy()
        except Exception:
            print(f"\nERROR: {e}\n", file=sys.stderr)
        return 2

    for note in paths.notes:
        print(f"  {note}")
    print()
    for warn in paths.warnings:
        print(f"WARNING: {warn}", file=sys.stderr)
    if paths.warnings:
        print(file=sys.stderr)

    # ---------- Load / build catalog ----------
    import json
    if not os.path.exists(args.catalog):
        if paths.has_archive("worlds"):
            print("catalog.json not found -- building from game data/WORLDS.REZ ...")
            cat_dict = build_catalog_from_rez(
                paths.archive_path("worlds"),
                data_rez_path=(
                    paths.archive_path("data") if paths.has_archive("data") else None
                ),
                object_lto_path=(
                    os.path.join(paths.game_data_dir, "object.lto")
                    if paths.game_data_dir else None
                ),
            )
        else:
            print(
                "ERROR: could not build catalog: game data/WORLDS.REZ was not found.",
                file=sys.stderr,
            )
            return 2
        save_catalog(cat_dict)
        print(f"  catalog saved to {args.catalog}")
    catalog = load_catalog(args.catalog)

    # ---------- Launch GUI ----------
    _import_gui()
    root = tk.Tk()

    if paths.warnings:
        def _show_warnings():
            messagebox.showwarning(
                "Setup warning",
                "\n\n".join(paths.warnings)
                + "\n\nThis is just a heads-up - the editor will work normally.")
        root.after(200, _show_warnings)

    app = EditorApp(root, catalog, paths, catalog_path=args.catalog)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
