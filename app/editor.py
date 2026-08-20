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
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
import time
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
from core import rude_script
from core import run_world
from core.game_resources import GameResources
from features.dat_editing import compiler_strategy as dat_compiler_strategy
from features.dat_editing import gltf_export as dat_gltf_export
from features.dat_editing import legacy_ed as dat_legacy_ed
from features.dat_editing import terrain_reconstruction
from features.dat_editing import terrain_semantics
from features.prefabs import import_static as prefab_import
from features.prefabs import behavioral as prefab_behavioral
from features.prefabs import inspector as prefab_inspector
from features.prefabs import resource_backed as prefab_resources
from catalog import (
    DEFAULT_LOMM_CATALOG_PATH,
    build_catalog_from_rez,
    class_template_from_catalog,
    ensure_lomm_catalog,
    load_catalog,
    save_catalog,
)
from features.presets.manager import PresetStore

# These imports pull in tkinter; they're deferred to _import_gui() below.
CatalogPanel = PropertiesPanel = SaveDialog = LommConversionDialog = None  # type: ignore
PrefabImportWorkspace = None  # type: ignore
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


def _maximize_window(root) -> None:
    """Start with a maximized normal window, with portable fallbacks."""
    try:
        root.state("zoomed")
        return
    except Exception:
        pass
    try:
        root.attributes("-zoomed", True)
        return
    except Exception:
        pass
    try:
        root.geometry(
            f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0"
        )
    except Exception:
        pass


class _LoadingOverlay:
    """Single reusable modal loading indicator over the editor window."""

    _FRAME_MS = 80

    def __init__(self, parent) -> None:
        self.parent = parent
        self._after_id = None
        self._angle = 0
        self.frame = tk.Frame(parent, bg="#0e1116", cursor="watch")
        panel = tk.Frame(
            self.frame,
            bg="#181d24",
            highlightbackground="#354252",
            highlightthickness=1,
            padx=34,
            pady=24,
        )
        panel.place(relx=0.5, rely=0.5, anchor="center")
        self.canvas = tk.Canvas(
            panel,
            width=38,
            height=38,
            bg="#181d24",
            highlightthickness=0,
        )
        self.canvas.pack(side="left", padx=(0, 14))
        self.arc = self.canvas.create_arc(
            5,
            5,
            33,
            33,
            start=0,
            extent=255,
            style="arc",
            outline="#55a7e5",
            width=4,
        )
        tk.Label(
            panel,
            text="Loading...",
            bg="#181d24",
            fg="#f1f4f8",
            font=("Segoe UI", 13),
        ).pack(side="left")

        # Consume input while a level is changing underneath the overlay.
        for sequence in ("<Button>", "<ButtonRelease>", "<Motion>", "<Key>"):
            self.frame.bind(sequence, lambda _event: "break")

    def show(self) -> None:
        self.frame.place(x=0, y=0, relwidth=1, relheight=1)
        self.frame.lift()
        try:
            self.frame.focus_set()
        except Exception:
            pass
        self._animate()
        # Paint before synchronous GL/model work starts on Tk's UI thread.
        try:
            self.parent.update_idletasks()
        except Exception:
            pass

    def _animate(self) -> None:
        self.pulse()
        try:
            self._after_id = self.parent.after(self._FRAME_MS, self._animate)
        except Exception:
            self._after_id = None

    def pulse(self) -> None:
        """Advance and paint once between synchronous loading stages."""
        self._angle = (self._angle - 30) % 360
        try:
            self.canvas.itemconfigure(self.arc, start=self._angle)
            self.parent.update_idletasks()
        except Exception:
            pass

    def hide(self) -> None:
        if self._after_id is not None:
            try:
                self.parent.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        try:
            self.frame.place_forget()
        except Exception:
            pass


def _import_gui():
    """Import all the GUI-dependent modules. Called only after config
    validation passes, so console errors don't get masked by missing-Tk."""
    global tk, CatalogPanel, PropertiesPanel, SaveDialog, LommConversionDialog
    global filedialog, messagebox, simpledialog, ttk
    global EditPresetDialog, ManagePresetsDialog, PrefabImportWorkspace
    global View3D, OPENGL_AVAILABLE, _view3d_missing
    import tkinter as _tk
    from tkinter import filedialog as _fd, messagebox as _mb, simpledialog as _sd, ttk as _ttk
    from ui.catalog_panel import CatalogPanel as _CP
    from ui.properties_panel import PropertiesPanel as _PP
    from ui.diff_panel import SaveDialog as _SD
    from ui.preset_dialog import EditPresetDialog as _EPD, ManagePresetsDialog as _MPD
    from ui.lomm_conversion_dialog import LommConversionDialog as _LCD
    from ui.prefab_import_workspace import PrefabImportWorkspace as _PIW
    tk = _tk
    filedialog = _fd; messagebox = _mb; simpledialog = _sd; ttk = _ttk
    CatalogPanel = _CP; PropertiesPanel = _PP; SaveDialog = _SD
    LommConversionDialog = _LCD
    PrefabImportWorkspace = _PIW
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
                 paths: Any, catalog_path: Optional[str] = None,
                 lomm_catalog_path: Optional[str] = None,
                 initial_lomm_root: str = ""):
        self.root = root
        self.catalog = catalog
        self.catalog_path = catalog_path
        self.lomm_catalog_path = (
            lomm_catalog_path or DEFAULT_LOMM_CATALOG_PATH
        )
        self.initial_lomm_root = str(initial_lomm_root or "")
        self.cfg = paths          # GamePaths — kept as self.cfg for compat
        self.resources = paths.resources()

        self.project = P.Project(
            rude_rez_path = paths.archive_path("rude") if paths.has_archive("rude") else None,
            scripts_rez_path = (
                paths.archive_path("scripts") if paths.has_archive("scripts") else None
            ),
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
        _maximize_window(root)
        self._selected_world_index: Optional[int] = None
        self._run_world_session = None
        self._rude_editor_windows: Dict[int, Any] = {}
        self._rude_script_editor_windows: Dict[int, Any] = {}

        self._build_menu()
        self._build_layout()
        self._loading_overlay = _LoadingOverlay(root)
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
        m_file.add_command(label="Run Current Level",     accelerator="Ctrl+Alt+R",
                           command=self.cmd_run_current_level)
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
        m_tools.add_command(label="Dialogue & Quest Editor...",
                            command=self.cmd_rude_dialogue_editor)
        m_tools.add_command(label="Dialogue Script Integration...",
                            command=self.cmd_rude_script_editor)
        m_tools.add_separator()
        m_tools.add_command(label="Import Prefab...",
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
        tk.Button(bar, text="Run Current Level", bg="#356a45", fg="white",
                  activebackground="#47895b",
                  relief="flat", command=self.cmd_run_current_level).pack(
                      side="left", padx=4, pady=4)
        tk.Button(bar, text="Dialogues & Quests...", bg="#5b426f", fg="white",
                  activebackground="#75568d",
                  relief="flat", command=self.cmd_rude_dialogue_editor).pack(
                      side="left", padx=4, pady=4)

        tk.Label(bar, text="Level:", bg="#1a1d22", fg="#cccccc",
                 font=("Segoe UI", 9)).pack(side="left", padx=(16, 4))

        self.level_var = tk.StringVar()
        self.level_combo = ttk.Combobox(bar, textvariable=self.level_var,
                                        state="readonly", width=40)
        self.level_combo.pack(side="left", padx=4, pady=4)
        self.level_combo.bind("<<ComboboxSelected>>", self._on_level_change)

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
            on_delete_incompatible = self._delete_all_incompatible_actors,
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
                world_helper_metadata = self.catalog.get("classes", {}),
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
        self.root.bind_all("<Control-Alt-r>",   lambda e: self.cmd_run_current_level())
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
        initial = str(getattr(self, "initial_lomm_root", "") or "")
        if initial and os.path.isdir(initial):
            return initial
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

    def _prefab_browser_root(self) -> str:
        settings = getattr(self, "editor_settings", {}) or {}
        remembered = str(settings.get("last_prefab_root") or "")
        if remembered and os.path.isdir(remembered):
            return remembered
        editor_dir = getattr(self.cfg, "editor_dir", None) or EDITOR_ROOT
        candidates = (
            os.path.abspath(os.path.join(editor_dir, os.pardir, "PreFabs")),
            os.path.join(editor_dir, "mm9_data", "PreFabs"),
            editor_dir,
        )
        return next((path for path in candidates if os.path.isdir(path)), editor_dir)

    def _remember_prefab_root(self, prefab_root: str) -> None:
        value = str(prefab_root or "").strip()
        if not value or not os.path.isdir(value):
            return
        settings = getattr(self, "editor_settings", None)
        if not isinstance(settings, dict):
            settings = {}
            self.editor_settings = settings
        settings["last_prefab_root"] = os.path.abspath(value)
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

    def _working_rude_dialogues(self) -> Dict[int, Any]:
        dialogues = {
            int(npc_nbr): asset.dialogue
            for npc_nbr, asset in self.project.rude_assets.items()
        }
        for npc_nbr, window in getattr(self, "_rude_editor_windows", {}).items():
            try:
                if window.winfo_exists():
                    dialogues[int(npc_nbr)] = window.dialogue
            except Exception:
                continue
        return dialogues

    def cmd_rude_dialogue_editor(
        self,
        npc_nbr: Optional[int] = None,
    ) -> Optional[Any]:
        """Open or create an independently editable RUDE dialogue asset."""
        rude_rez_path = getattr(self.project, "rude_rez_path", None)
        if not rude_rez_path or not os.path.isfile(rude_rez_path):
            messagebox.showerror(
                "RUDE.REZ not found",
                "No game data/RUDE.REZ archive was detected.",
            )
            return None

        if npc_nbr is None:
            npc_nbr = simpledialog.askinteger(
                "Dialogue & Quest Editor",
                "NPC dialogue number (existing resources include NPC997–999):",
                initialvalue=int(getattr(self.project, "next_npc_nbr", 437)),
                minvalue=1,
                parent=getattr(self, "root", None),
            )
        if npc_nbr is None:
            return None
        npc_nbr = int(npc_nbr)

        windows = getattr(self, "_rude_editor_windows", None)
        if not isinstance(windows, dict):
            windows = {}
            self._rude_editor_windows = windows
        existing_window = windows.get(npc_nbr)
        if existing_window is not None:
            try:
                if existing_window.winfo_exists():
                    existing_window.lift()
                    existing_window.focus_force()
                    return existing_window
            except Exception:
                pass
            windows.pop(npc_nbr, None)

        try:
            asset = self.project.open_rude_asset(npc_nbr)
        except FileNotFoundError as exc:
            if "does not contain" not in str(exc):
                messagebox.showerror("Cannot open RUDE asset", str(exc))
                return None
            if not messagebox.askyesno(
                "Create dialogue asset?",
                f"RUDE.REZ has no NPC{npc_nbr} dialogue. Create it as an "
                "independent project asset?",
                parent=getattr(self, "root", None),
            ):
                return None
            name = simpledialog.askstring(
                "New dialogue asset",
                "NPC display name:",
                initialvalue=f"NPC {npc_nbr}",
                parent=getattr(self, "root", None),
            )
            if name is None:
                return None
            name = name.strip()
            if not name:
                messagebox.showerror(
                    "Invalid display name", "The NPC display name cannot be empty.")
                return None
            try:
                from core import rude as rude_model

                metadata = rude_model.RudeDialogueMetadata(
                    npc_nbr=npc_nbr,
                    name=name,
                    initial_state=npc_nbr,
                    opening_blurb="Hello.",
                )
                asset = self.project.create_rude_asset(
                    rude_model.make_simple_dialogue(metadata, ()))
            except Exception as create_exc:
                messagebox.showerror("Cannot create RUDE asset", str(create_exc))
                return None
            if npc_nbr >= self.project.next_npc_nbr:
                self.project.next_npc_nbr = npc_nbr + 1
        except Exception as exc:
            messagebox.showerror("Cannot open RUDE asset", str(exc))
            return None

        from ui.rude_editor import RudeEditorWindow

        window = RudeEditorWindow(
            self.root,
            self.project,
            asset,
            on_changed=lambda _asset: self._update_history_menu(),
            on_open_related=self.cmd_rude_dialogue_editor,
            dialogue_overrides_provider=self._working_rude_dialogues,
        )
        windows[npc_nbr] = window

        def forget_window(event, *, expected=window, key=npc_nbr) -> None:
            if getattr(event, "widget", None) is expected:
                windows.pop(key, None)

        window.bind("<Destroy>", forget_window, add="+")
        return window

    def cmd_rude_script_editor(
        self,
        npc_nbr: Optional[int] = None,
    ) -> Optional[Any]:
        """Open independent rewards/sound/world-change script authoring."""
        scripts_rez_path = getattr(self.project, "scripts_rez_path", None)
        if not scripts_rez_path or not os.path.isfile(scripts_rez_path):
            messagebox.showerror(
                "SCRIPTS.REZ not found",
                "No game data/SCRIPTS.REZ archive was detected.",
            )
            return None
        if npc_nbr is None:
            npc_nbr = simpledialog.askinteger(
                "Dialogue Script Integration",
                "NPC dialogue number whose exit effects you want to author:",
                initialvalue=int(getattr(self.project, "next_npc_nbr", 437)),
                minvalue=1,
                parent=getattr(self, "root", None),
            )
        if npc_nbr is None:
            return None
        npc_nbr = int(npc_nbr)

        windows = getattr(self, "_rude_script_editor_windows", None)
        if not isinstance(windows, dict):
            windows = {}
            self._rude_script_editor_windows = windows
        existing_window = windows.get(npc_nbr)
        if existing_window is not None:
            try:
                if existing_window.winfo_exists():
                    existing_window.lift()
                    existing_window.focus_force()
                    return existing_window
            except Exception:
                pass
            windows.pop(npc_nbr, None)

        try:
            from ui.rude_script_editor import RudeScriptEditorWindow
            window = RudeScriptEditorWindow(
                self.root,
                self.project,
                npc_nbr,
                on_changed=lambda _asset: self._update_history_menu(),
                on_attach=self._attach_dialogue_script_to_selected,
            )
        except Exception as exc:
            messagebox.showerror("Cannot open script integration", str(exc))
            return None
        windows[npc_nbr] = window

        def forget_window(event, *, expected=window, key=npc_nbr) -> None:
            if getattr(event, "widget", None) is expected:
                windows.pop(key, None)

        window.bind("<Destroy>", forget_window, add="+")
        return window

    def _attach_dialogue_script_to_selected(
        self,
        asset: rude_script.DialogueScriptAssetEdit,
    ) -> bool:
        """Attach a reviewed generated ScriptName to the selected matching NPC."""
        level = getattr(self, "active", None)
        selected_index = getattr(self, "_selected_world_index", None)
        if level is None or selected_index is None:
            messagebox.showerror(
                "Select the NPC",
                "Open a level and select the placed NPC before attaching the script.",
            )
            return False
        materialized = (
            level.editor_materialize()
            if hasattr(level, "editor_materialize") else level.materialize()
        )
        if selected_index < 0 or selected_index >= len(materialized.objects):
            messagebox.showerror("Select the NPC", "The selected object is no longer present.")
            return False
        obj = materialized.objects[selected_index]
        try:
            selected_npc_nbr = int(obj.get("NPCNbr") or 0)
        except (TypeError, ValueError):
            selected_npc_nbr = 0
        if selected_npc_nbr != asset.npc_nbr:
            messagebox.showerror(
                "NPC number mismatch",
                f"The selected object has NPCNbr={selected_npc_nbr}; this script "
                f"belongs to NPC{asset.npc_nbr}.",
            )
            return False

        current_name = str(obj.get("ScriptName") or "").strip()
        target_name = asset.integration.script_name
        if current_name:
            try:
                current_path = rude_script.canonical_script_path(current_name)
                current_key = current_path.casefold()
                target_key = target_name.casefold()
                base_key = (
                    rude_script.canonical_script_path(
                        asset.integration.base_virtual_path).casefold()
                    if asset.integration.base_virtual_path else ""
                )
            except ValueError as exc:
                messagebox.showerror("Unsafe current ScriptName", str(exc))
                return False
            if current_key == target_key:
                return True
            if not base_key or current_key != base_key:
                messagebox.showerror(
                    "Existing behavior is not integrated",
                    f"The selected NPC currently uses {current_name}. Load that exact "
                    "base ScriptName in the script editor before replacing it, so its "
                    "existing behavior is preserved.",
                )
                return False

        self._on_property_edited("ScriptName", target_name)
        return True

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

    def _open_rez_level(self, rez_path: str, virtual_path: str) -> Optional[Any]:
        try:
            L = self.project.add_level_from_rez(rez_path, virtual_path)
        except Exception as e:
            messagebox.showerror("Open failed", str(e))
            return None

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
        return L

    def cmd_save(self) -> None:
        self._flush_view_transforms()
        if not self.project.has_pending():
            messagebox.showinfo("Nothing to save",
                                "There are no pending level or RUDE asset edits.")
            return
        plan = self.project.save_plan()
        SaveDialog(self.root, self.project, plan,
                   on_committed=self._on_save_committed,
                   cfg=self.cfg)

    def cmd_run_current_level(self) -> None:
        """Launch the active in-memory level in an isolated MM9 session."""
        self._flush_view_transforms()
        level = getattr(self, "active", None)
        if level is None:
            messagebox.showwarning(
                "No level",
                "Open a level from WORLDS.REZ first.",
            )
            return

        previous = getattr(self, "_run_world_session", None)
        process = getattr(previous, "process", None)
        if process is not None:
            try:
                running = process.poll() is None
            except Exception:
                running = False
            if running:
                messagebox.showwarning(
                    "MM9 preview already running",
                    "Close the current Might and Magic IX preview before "
                    "starting another one.",
                )
                return

        game_root = str(getattr(self.cfg, "game_root", "") or "")
        if not game_root or not os.path.isdir(game_root):
            messagebox.showerror(
                "MM9 game folder not detected",
                "No MM9 game folder was detected. Launch from the game folder "
                "or use --game-root.",
            )
            return
        staging_root = (
            getattr(self.project, "work_dir", None)
            or getattr(self.cfg, "work_dir", None)
        )
        try:
            session = run_world.launch_current_level(
                self.project,
                level,
                game_root=game_root,
                staging_root=staging_root,
            )
        except Exception as exc:
            messagebox.showerror("Run Current Level failed", str(exc))
            return
        self._run_world_session = session
        print(
            f"[run world] {session.world_name} staged at {session.session_dir}",
            file=sys.stderr,
        )

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
            blocking_issues = install_manager.batch_blocking_issues(batch_dir)
        except Exception as e:
            messagebox.showerror("Cannot inspect output batch", str(e))
            return
        if not archives:
            messagebox.showerror(
                "No archives found",
                f"No patched .REZ files were found under:\n{os.path.join(batch_dir, 'data')}")
            return
        archive_names = [os.path.basename(path) for path in archives]
        allow_blocking_issues = False
        if blocking_issues:
            details = "\n".join(
                f"  - {issue.get('message') or issue.get('code')}"
                for issue in blocking_issues
            )
            allow_blocking_issues = messagebox.askyesno(
                "Advanced override: incompatible LoMM actors",
                "This batch contains conversion issues that MM9 cannot display:\n\n"
                f"{details}\n\n"
                "Installing anyway is an advanced override. Continue?",
                icon="warning",
            )
            if not allow_blocking_issues:
                return

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
                allow_blocking_issues=allow_blocking_issues,
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
        """Show or hide billboards for objects with visible 3-D models."""
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
            staging_root=getattr(self.cfg, "work_dir", None),
            catalog_json=self.catalog_path,
            lomm_catalog_json=getattr(
                self, "lomm_catalog_path", DEFAULT_LOMM_CATALOG_PATH,
            ),
            initial_lomm_root=self._last_lomm_root(),
            on_success=self._on_lomm_conversion_success,
        )

    def _on_lomm_conversion_success(self, result: Any, lomm_root: str = "") -> None:
        """Open the staged MM9 level after a LoMM conversion."""
        self._remember_lomm_root(lomm_root)
        try:
            level = self._open_rez_level(result.worlds_rez, result.added_virtual_path)
            stats = getattr(getattr(result, "conversion", None), "stats", None)
            compatibility = getattr(stats, "compatibility", None)
            if level is not None and compatibility is not None:
                from conversion.lomm_to_mm9_service import _compatibility_report_dict
                level.conversion_report = _compatibility_report_dict(compatibility)
                level.conversion_stage_dir = str(getattr(result, "stage_dir", "") or "")
                level.preview_actor_visuals = copy.deepcopy(
                    getattr(stats, "preview_actor_visuals", {}) or {}
                )
                self._set_active(level)
        except Exception as exc:
            messagebox.showerror(
                "Open converted level failed",
                str(exc),
            )

    def cmd_save_project(self) -> None:
        """Save the current project (levels + ops) to a .mm9mod JSON file."""
        self._flush_view_transforms()
        if not self.project.levels and not self.project.rude_assets:
            messagebox.showinfo(
                "Nothing to save", "No levels or RUDE assets are open.")
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
            messagebox.showinfo(
                "Project saved",
                f"Saved {len(self.project.levels)} level(s) and "
                f"{len(self.project.rude_assets)} RUDE asset(s) to:\n{path}",
            )
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
        profile_load = os.environ.get("MM9_EDITOR_PROFILE_LOAD") == "1"
        load_started = time.perf_counter()
        load_stages = []

        def _mark(name: str, started: float) -> None:
            if profile_load:
                load_stages.append((name, time.perf_counter() - started))

        loading = getattr(self, "_loading_overlay", None)
        if loading is not None:
            loading.show()
        try:
            self.active = L
            self._selected_world_index = None
            if self.view3d:
                stage_started = time.perf_counter()
                self._update_view_assets_for_level(L)
                _mark("assets", stage_started)
                if loading is not None:
                    loading.pulse()
                stage_started = time.perf_counter()
                self.view3d.set_active_level(L)
                _mark("view3d", stage_started)
                if loading is not None:
                    loading.pulse()
            stage_started = time.perf_counter()
            self.level_panel.set_active_level(L)
            _mark("object_panel", stage_started)
            if loading is not None:
                loading.pulse()
            self.props_panel.show(None)
            self._update_history_menu()
            if self.view3d:
                self.root.after_idle(self.view3d.focus_for_input)
        finally:
            if loading is not None:
                loading.hide()
            if profile_load:
                total = time.perf_counter() - load_started
                parts = [f"total={total:.3f}s"]
                parts.extend(
                    f"{name}={duration:.3f}s"
                    for name, duration in load_stages
                )
                label = L.display_name or L.rez_vpath or L.path
                print(
                    f"[editor load] {label}  " + "  ".join(parts),
                    file=sys.stderr,
                )

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
        current_objects = L.editor_materialize().objects
        current_object_names = [obj.get("Name") or "" for obj in current_objects]
        worldobject_template = class_template_from_catalog(self.catalog, "WorldObject")
        invisiblebrush_template = class_template_from_catalog(self.catalog, "InvisibleBrush")

        def _resource_exists(kind: str, resource_path: str):
            return None if kind == "sprite" else self.resources.exists(resource_path)

        def _read_script(resource_path: str) -> str:
            reader = getattr(self.resources, "read_text", None)
            if reader is None:
                raise FileNotFoundError(resource_path)
            return reader(resource_path)

        def _analyze_behavioral(path: str):
            return prefab_behavioral.analyze_prefab(
                path,
                catalog=self.catalog,
                supported_classes=prefab_behavioral.PHASE6_BEHAVIORAL_CLASSES,
                resource_exists=_resource_exists,
                allow_scripts=True,
                allowed_script_names=prefab_behavioral.PHASE6_REVIEWED_SCRIPTS,
                script_loader=_read_script,
                allow_generated_bsp=False,
            )

        def _resource_candidates(path: str, _info):
            def _exists(resource_path: str) -> bool:
                try:
                    return bool(self.resources.exists(resource_path))
                except Exception:
                    return False

            return prefab_resources.find_resource_backed_candidates(
                path,
                self.catalog,
                resource_exists=_exists,
            )

        def _resource_template(request) -> patcher.WorldObject:
            if request.resource_class != "Prop":
                raise ValueError(
                    f"Resource-backed prefab class {request.resource_class!r} is not supported."
                )
            if not self.resources.exists(request.resource_model):
                raise ValueError(
                    f"Game model {request.resource_model!r} is not available in the active resources."
                )
            missing_skins = [
                path for path in request.resource_skins
                if not self.resources.exists(path)
            ]
            if missing_skins:
                raise ValueError(
                    "Game-model candidate has missing skin resource(s): "
                    + ", ".join(missing_skins)
                )
            template = self._find_template_for_filename(
                request.resource_model,
                class_name=request.resource_class,
            )
            if template is None:
                template = class_template_from_catalog(
                    self.catalog,
                    request.resource_class,
                )
            if template is None:
                raise ValueError(
                    f"The catalog has no complete {request.resource_class} template. "
                    "Rebuild the MM9 catalog."
                )
            return copy.deepcopy(template)

        def _behavioral_script_sources(analysis) -> Dict[str, str]:
            return prefab_behavioral.collect_reviewed_script_sources(
                analysis,
                _read_script,
            )

        def _behavioral_templates(analysis) -> Dict[str, patcher.WorldObject]:
            templates: Dict[str, patcher.WorldObject] = {}
            for source in analysis.graph.runtime_objects:
                if source.class_name in templates:
                    continue
                template = class_template_from_catalog(self.catalog, source.class_name)
                if template is None:
                    raise ValueError(
                        f"The catalog has no complete {source.class_name} template. "
                        "Rebuild the MM9 catalog."
                    )
                templates[source.class_name] = template
            if analysis.graph.brushes and "WorldObject" not in templates:
                template = class_template_from_catalog(self.catalog, "WorldObject")
                if template is None:
                    raise ValueError(
                        "The catalog has no complete WorldObject template. Rebuild the MM9 catalog."
                    )
                templates["WorldObject"] = template
            return templates

        def _suggest_name(path: str) -> str:
            return prefab_import.suggest_import_name(
                L.preview_bsp() or bsp_world,
                path,
                current_object_names,
            )

        def _validate_request(request) -> None:
            if request.import_mode == "resource":
                template = _resource_template(request)
                probe = P.ImportResourcePrefabOp(
                    template=template,
                    overrides={
                        "Name": request.new_name,
                        "Pos": [0.0, 0.0, 0.0],
                        "Rotation": [0.0, 0.0, 0.0, 0.0],
                        "Filename": request.resource_model,
                        **(
                            {"Skin": ";".join(request.resource_skins)}
                            if request.resource_skins else {}
                        ),
                    },
                    prefab_path=request.prefab_path,
                    candidate_id=request.resource_candidate_id,
                    model_path=request.resource_model,
                    skin_paths=tuple(request.resource_skins),
                )
                test_world = copy.deepcopy(L.editor_materialize())
                if any(
                    str(obj.get("Name") or "").casefold() == request.new_name.casefold()
                    for obj in test_world.objects
                ):
                    raise ValueError(f"Object named {request.new_name!r} already exists.")
                probe.apply_to(test_world)
                return
            if request.import_mode == "behavioral":
                analysis = _analyze_behavioral(request.prefab_path)
                plan = prefab_behavioral.build_behavioral_import_plan(
                    analysis,
                    root_name=request.new_name,
                    target_pos=(0.0, 0.0, 0.0),
                    existing_names=current_object_names,
                    external_bindings=request.external_bindings,
                )
                plan.require_ready()
                binding_issues = prefab_behavioral.validate_plan_target_bindings(
                    plan,
                    target_object_names=current_object_names,
                    target_bsp=bsp_world,
                    target_dat_bytes=(
                        L.source_bytes() if hasattr(L, "source_bytes") else b""
                    ),
                )
                if binding_issues:
                    raise ValueError("; ".join(binding_issues))
                prefab_behavioral.materialize_behavioral_plan(
                    analysis,
                    plan,
                    class_templates=_behavioral_templates(analysis),
                    placement_anchor=request.placement_anchor,
                    object_overrides=prefab_behavioral.build_script_import_assets(
                        analysis,
                        plan,
                        operation_id="validation",
                        script_loader=prefab_behavioral.script_loader_from_sources(
                            _behavioral_script_sources(analysis)
                        ),
                    )[0],
                )
                prefab_behavioral.build_behavioral_bsp_import_plan(
                    L.preview_bsp() or bsp_world,
                    analysis,
                    plan,
                    placement_anchor=request.placement_anchor,
                    allow_generated_bsp=False,
                    validate_runtime_bsp=True,
                )
                return
            if worldobject_template is None:
                raise ValueError(
                    "The catalog has no complete WorldObject template. Rebuild the MM9 catalog."
                )
            if (
                request.collision_mode in {"invisible_bsp", "box_approx"}
                and invisiblebrush_template is None
            ):
                raise ValueError(
                    "The catalog has no complete InvisibleBrush template. Rebuild the MM9 catalog "
                    "or choose 'No collision helper'."
                )
            prefab_import.build_static_import_plan(
                L.preview_bsp() or bsp_world,
                request.prefab_path,
                new_name=request.new_name,
                target_pos=(0.0, 0.0, 0.0),
                collision_mode=request.collision_mode,
                collision_thickness=request.collision_thickness,
                collision_segment_length=request.collision_segment_length,
                target_dat_bytes=L.source_bytes(),
                placement_anchor=request.placement_anchor,
                target_object_names=current_object_names,
                allow_generated_bsp=request.import_mode == "preview",
                validate_runtime_bsp=request.import_mode == "static",
            )

        request = PrefabImportWorkspace.ask(
            self.root,
            initial_dir=self._prefab_browser_root(),
            inspect_prefab=prefab_inspector.inspect_prefab,
            analyze_prefab=_analyze_behavioral,
            find_resource_candidates=_resource_candidates,
            suggest_name=_suggest_name,
            validate_request=_validate_request,
        )
        if request is None:
            return

        self._remember_prefab_root(request.browser_root)

        self._pending_template = None
        if request.import_mode == "resource":
            self._pending_kind = "import_resource_prefab"
            self._pending_prefab_path = request.prefab_path
            self._pending_prefab_name = request.new_name
            self._pending_resource_candidate_id = request.resource_candidate_id
            self._pending_resource_model = request.resource_model
            self._pending_resource_skins = tuple(request.resource_skins)
            self._pending_resource_template = _resource_template(request)
            try:
                with open(request.prefab_path, "rb") as handle:
                    self._pending_resource_fingerprint = hashlib.sha256(handle.read()).hexdigest()
            except OSError:
                self._pending_resource_fingerprint = ""
            if self.view3d is not None:
                self.view3d.set_place_mode(True)
            return
        if request.import_mode == "behavioral":
            analysis = _analyze_behavioral(request.prefab_path)
            self._pending_kind = "import_behavioral_prefab"
            self._pending_prefab_path = request.prefab_path
            self._pending_prefab_name = request.new_name
            self._pending_prefab_placement_anchor = request.placement_anchor
            self._pending_behavioral_fingerprint = analysis.graph.source_fingerprint
            self._pending_behavioral_templates = _behavioral_templates(analysis)
            self._pending_behavioral_bindings = dict(request.external_bindings)
            self._pending_behavioral_script_sources = _behavioral_script_sources(analysis)
            if self.view3d is not None:
                self.view3d.set_place_mode(True)
            return

        self._pending_kind = "import_prefab_bsp"
        self._pending_prefab_path = request.prefab_path
        self._pending_prefab_name = request.new_name
        self._pending_prefab_roles = None
        self._pending_prefab_collision_mode = request.collision_mode
        self._pending_prefab_collision_thickness = request.collision_thickness
        self._pending_prefab_collision_segment_length = request.collision_segment_length
        self._pending_prefab_placement_anchor = request.placement_anchor
        self._pending_prefab_worldobject_template = worldobject_template
        self._pending_prefab_invisiblebrush_template = invisiblebrush_template
        self._pending_prefab_preview_only = request.import_mode == "preview"
        if self.view3d is not None:
            self.view3d.set_place_mode(True)

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
        if getattr(self, "_pending_kind", None) == "import_prefab_bsp":
            self._place_pending_prefab_bsp_at_pos((float(wx), float(wy), float(wz)))
            return
        if getattr(self, "_pending_kind", None) == "import_resource_prefab":
            self._place_pending_resource_prefab_at_pos((float(wx), float(wy), float(wz)))
            return
        if getattr(self, "_pending_kind", None) == "import_behavioral_prefab":
            self._place_pending_behavioral_prefab_at_pos((float(wx), float(wy), float(wz)))
            return
        if not getattr(self, "_pending_template", None):
            return
        self._place_pending_at_pos([float(wx), float(wy), float(wz)])

    def _place_pending_prefab_bsp_at_pos(self, new_pos: tuple) -> None:
        L = self.active
        prefab_path = getattr(self, "_pending_prefab_path", "")
        new_name = getattr(self, "_pending_prefab_name", "")
        include_roles = getattr(self, "_pending_prefab_roles", None)
        collision_mode = getattr(self, "_pending_prefab_collision_mode", "none")
        collision_thickness = float(getattr(self, "_pending_prefab_collision_thickness", 8.0))
        collision_segment_length = float(getattr(self, "_pending_prefab_collision_segment_length", 512.0))
        placement_anchor = getattr(self, "_pending_prefab_placement_anchor", "bottom_center")
        worldobject_template = getattr(self, "_pending_prefab_worldobject_template", None)
        invisiblebrush_template = getattr(self, "_pending_prefab_invisiblebrush_template", None)
        preview_only = bool(getattr(self, "_pending_prefab_preview_only", False))
        op = P.ImportPrefabBspOp(
            prefab_path=prefab_path,
            new_name=new_name,
            target_pos=tuple(float(v) for v in new_pos),
            include_roles=include_roles,
            collision_mode=collision_mode,
            collision_thickness=collision_thickness,
            collision_segment_length=collision_segment_length,
            placement_anchor=placement_anchor,
            worldobject_template=copy.deepcopy(worldobject_template),
            invisiblebrush_template=copy.deepcopy(invisiblebrush_template),
            preview_only=preview_only,
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
                placement_anchor=placement_anchor,
                target_object_names=[
                    obj.get("Name") or "" for obj in L.editor_materialize().objects
                ],
                allow_generated_bsp=preview_only,
                validate_runtime_bsp=not preview_only,
            )
        except Exception as e:
            messagebox.showerror("Prefab import failed", str(e))
            return

        L.append_op(op)
        try:
            mat = L.editor_materialize() if hasattr(L, "editor_materialize") else L.materialize()
        except Exception as e:
            L.undo_last_op()
            messagebox.showerror("Prefab import failed", str(e))
            return
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
        self._pending_prefab_placement_anchor = "bottom_center"
        self._pending_prefab_worldobject_template = None
        self._pending_prefab_invisiblebrush_template = None
        self._pending_prefab_preview_only = False
        self._selected_world_index = helper_index
        if self.view3d is not None:
            self.view3d.set_place_mode(False)
        self._refresh_after_edit(helper_index)

    def _place_pending_resource_prefab_at_pos(self, new_pos: tuple) -> None:
        L = self.active
        template = getattr(self, "_pending_resource_template", None)
        name = str(getattr(self, "_pending_prefab_name", "") or "")
        model = str(getattr(self, "_pending_resource_model", "") or "")
        skins = tuple(getattr(self, "_pending_resource_skins", ()) or ())
        if template is None or not name or not model:
            messagebox.showerror(
                "Prefab import failed",
                "The selected catalog game-model candidate is incomplete.",
            )
            return
        overrides: Dict[str, Any] = {
            "Name": name,
            "Pos": [float(value) for value in new_pos],
            "Rotation": [0.0, 0.0, 0.0, 0.0],
            "Filename": model,
        }
        if skins:
            overrides["Skin"] = ";".join(str(value) for value in skins)
        op = P.ImportResourcePrefabOp(
            template=copy.deepcopy(template),
            overrides=overrides,
            prefab_path=str(getattr(self, "_pending_prefab_path", "") or ""),
            candidate_id=str(
                getattr(self, "_pending_resource_candidate_id", "") or ""
            ),
            model_path=model,
            skin_paths=skins,
            source_fingerprint=str(
                getattr(self, "_pending_resource_fingerprint", "") or ""
            ),
        )
        try:
            materialized = L.editor_materialize()
            if any(
                str(obj.get("Name") or "").casefold() == name.casefold()
                for obj in materialized.objects
            ):
                raise ValueError(f"Object named {name!r} already exists.")
            L.append_op(op)
            materialized = L.editor_materialize()
        except Exception as exc:
            if L.ops and L.ops[-1] is op:
                L.undo_last_op()
            messagebox.showerror("Prefab import failed", str(exc))
            return
        selected_index = next(
            (
                index for index, obj in enumerate(materialized.objects)
                if str(obj.get("Name") or "").casefold() == name.casefold()
            ),
            len(materialized.objects) - 1,
        )
        self._pending_kind = None
        self._pending_prefab_path = ""
        self._pending_prefab_name = ""
        self._pending_resource_candidate_id = ""
        self._pending_resource_model = ""
        self._pending_resource_skins = ()
        self._pending_resource_template = None
        self._pending_resource_fingerprint = ""
        self._selected_world_index = selected_index
        if self.view3d is not None:
            self.view3d.set_place_mode(False)
        self._refresh_after_edit(selected_index)

    def _place_pending_behavioral_prefab_at_pos(self, new_pos: tuple) -> None:
        L = self.active
        op = P.ImportBehavioralPrefabOp(
            prefab_path=getattr(self, "_pending_prefab_path", ""),
            root_name=getattr(self, "_pending_prefab_name", ""),
            target_pos=tuple(float(value) for value in new_pos),
            placement_anchor=getattr(
                self,
                "_pending_prefab_placement_anchor",
                "original_origin",
            ),
            source_fingerprint=getattr(self, "_pending_behavioral_fingerprint", ""),
            enabled_capabilities=tuple(sorted(prefab_behavioral.PHASE6_BEHAVIORAL_CLASSES)),
            class_templates=copy.deepcopy(
                getattr(self, "_pending_behavioral_templates", {})
            ),
            external_bindings=dict(
                getattr(self, "_pending_behavioral_bindings", {})
            ),
            script_sources=dict(
                getattr(self, "_pending_behavioral_script_sources", {})
            ),
            planner_version=prefab_behavioral.PLANNER_VERSION,
        )
        try:
            before = L.editor_materialize().objects
            analysis = op._analyze()
            plan = prefab_behavioral.build_behavioral_import_plan(
                analysis,
                root_name=op.root_name,
                target_pos=op.target_pos,
                target_yaw=op.target_yaw,
                existing_names=[str(obj.get("Name") or "") for obj in before],
                external_bindings=op.external_bindings,
            )
            op.planned_object_names = {
                str(item.source_index): item.target_name
                for item in plan.objects
            }
            script_overrides, script_assets = (
                prefab_behavioral.build_script_import_assets(
                    analysis,
                    plan,
                    operation_id=op.operation_id,
                    script_loader=prefab_behavioral.script_loader_from_sources(
                        op.script_sources
                    ),
                )
            )
            for source_index, values in script_overrides.items():
                op.object_overrides.setdefault(source_index, {}).update(values)
            op.script_assets = script_assets
            binding_issues = prefab_behavioral.validate_plan_target_bindings(
                plan,
                target_object_names=[str(obj.get("Name") or "") for obj in before],
                target_bsp=L.get_bsp(),
                target_dat_bytes=L.source_bytes(),
            )
            if binding_issues:
                raise ValueError("; ".join(binding_issues))
            prefab_behavioral.materialize_behavioral_plan(
                analysis,
                plan,
                class_templates=op.class_templates,
                placement_anchor=op.placement_anchor,
            )
            prefab_behavioral.build_behavioral_bsp_import_plan(
                L.preview_bsp() or L.get_bsp(),
                analysis,
                plan,
                placement_anchor=op.placement_anchor,
                allow_generated_bsp=False,
                validate_runtime_bsp=True,
            )
        except Exception as exc:
            messagebox.showerror("Prefab import failed", str(exc))
            return

        L.append_op(op)
        try:
            materialized = L.editor_materialize()
        except Exception as exc:
            L.undo_last_op()
            messagebox.showerror("Prefab import failed", str(exc))
            return
        selected_index = len(materialized.objects) - len(plan.objects)
        self._pending_kind = None
        self._pending_prefab_path = ""
        self._pending_prefab_name = ""
        self._pending_prefab_placement_anchor = "bottom_center"
        self._pending_behavioral_fingerprint = ""
        self._pending_behavioral_templates = {}
        self._pending_behavioral_bindings = {}
        self._pending_behavioral_script_sources = {}
        self._selected_world_index = selected_index
        if self.view3d is not None:
            self.view3d.set_place_mode(False)
        self._refresh_after_edit(selected_index)

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

        # Encode the NPCNbr override and stage dialogue as a project-level RUDE
        # asset.  The AddOp deliberately carries no dialogue payload: the
        # resource can now outlive, move independently of, or be edited without
        # this particular placed world object.
        rc = getattr(self, "_pending_rude_config", None)
        if rc and rc.get("mode") == "fresh":
            nbr = rc["npc_nbr"]
            # NPCNbr is stored as IEEE-754 float bits packed into a LongInt slot
            overrides["NPCNbr"] = struct.unpack("<I", struct.pack("<f", float(nbr)))[0]
            registration = P.RudeRegistration(**{
                k: v for k, v in rc.items() if k != "mode"
            })
            try:
                self.project.create_simple_rude_asset(registration)
            except Exception as exc:
                messagebox.showerror("RUDE asset failed", str(exc))
                return
            # Advance the project's counter when the suggested number was used
            if nbr >= self.project.next_npc_nbr:
                self.project.next_npc_nbr = nbr + 1

        op = P.AddOp(template=copy.deepcopy(self._pending_template),
                     overrides=overrides,
                     rude=None)
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
                if isinstance(pending_op, P.ImportBehavioralPrefabOp):
                    positional = False
                    property_overrides: Dict[str, Any] = {}
                    for name, val in overrides.items():
                        if name == "Pos":
                            pending_op.retarget_from_object(
                                L.objects_before_op(pending_op),
                                object_offset,
                                tuple(float(v) for v in val),
                            )
                            positional = True
                        elif name == "Rotation":
                            vals = list(val) if isinstance(val, (list, tuple)) else []
                            vals = (vals + [0.0, 0.0, 0.0, 0.0])[:4]
                            pending_op.rerotate_from_object(
                                L.objects_before_op(pending_op),
                                object_offset,
                                tuple(float(v) for v in vals),
                            )
                            positional = True
                        else:
                            property_overrides[name] = val
                    if property_overrides:
                        pending_op.set_object_overrides(object_offset, property_overrides)
                    if positional or property_overrides:
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
                    if isinstance(pending_op, P.ImportBehavioralPrefabOp):
                        L.append_op(P.RemoveBehavioralPrefabOp(
                            operation_id=pending_op.operation_id,
                            root_name=pending_op.root_name,
                        ))
                    elif isinstance(pending_op, P.CloneDoorOp):
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

    def _delete_all_incompatible_actors(self) -> None:
        L = self.active
        if not L:
            return
        unresolved = L.unresolved_conversion_indices()
        already_deleted = {
            op.target_index for op in L.ops if isinstance(op, P.DeleteOp)
        }
        targets = sorted(unresolved - already_deleted)
        if not targets:
            return
        if not messagebox.askyesno(
            "Delete incompatible LoMM actors?",
            f"Delete {len(targets)} actor(s) that MM9 cannot construct?\n\n"
            "This is an editor operation and can be undone before saving.",
        ):
            return
        for index in targets:
            L.append_op(P.DeleteOp(target_index=index))
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
                    elif isinstance(pending_op, P.ImportBehavioralPrefabOp):
                        pending_op.retarget_from_object(
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
                    elif isinstance(pending_op, P.ImportBehavioralPrefabOp):
                        pending_op.rerotate_from_object(
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
                    elif isinstance(pending_op, P.ImportBehavioralPrefabOp):
                        mat = L.materialize()
                        old = mat.objects[world_index].get("Pos")
                        if old is not None:
                            pending_op.retarget_from_object(
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
                   virtual_root: str, extensions,
                   archive_path: Optional[str] = None) -> Optional[str]:
        """Return a cached REZ-backed asset directory."""
        try:
            resources = self.resources
            if archive_path:
                resources = GameResources(
                    archives={archive_key: archive_path},
                    cache_dir=getattr(self.resources, "cache_dir", None),
                )
            return resources.cache_archive_tree(
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

    def _level_asset_archive(
        self,
        level: Optional[P.LevelEdit],
        archive_key: str,
    ) -> Optional[str]:
        """Prefer a converted level's complete staged asset archive."""
        archive_name = {
            "models": "MODELS.REZ",
            "skins": "SKINS.REZ",
        }.get(archive_key)
        stage_dir = str(
            getattr(level, "conversion_stage_dir", "") or ""
        )
        if archive_name and stage_dir:
            staged = os.path.join(stage_dir, "data", archive_name)
            if os.path.isfile(staged):
                return staged
        return self.resources.archives.get(archive_key)

    def _update_view_assets_for_level(
        self,
        level: Optional[P.LevelEdit],
    ) -> None:
        """Switch model/skin caches between live and staged complete archives."""
        if self.view3d is None:
            return
        models_rez = self._level_asset_archive(level, "models")
        skins_rez = self._level_asset_archive(level, "skins")
        models_dir = self._asset_dir(
            "models", "MODELS", (".ABC",), archive_path=models_rez,
        )
        skins_dir = self._asset_dir(
            "skins", "SKINS", (".DTX",), archive_path=skins_rez,
        )
        self.view3d.update_asset_directories(
            models_dir=models_dir,
            skins_dir=skins_dir,
        )
        if hasattr(self.view3d, "update_actor_visuals"):
            actor_visuals = dict(self.catalog.get("actor_visuals") or {})
            actor_visuals.update(
                getattr(level, "preview_actor_visuals", {}) or {}
            )
            self.view3d.update_actor_visuals(actor_visuals)

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

    def _find_template_for_filename(
        self,
        filename: str,
        class_name: Optional[str] = None,
    ) -> Optional[patcher.WorldObject]:
        target = filename.lower()
        _prop_types = (
            (class_name,)
            if class_name
            else ("Prop", "WorldObject", "DestructableProp")
        )

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
    p.add_argument(
        "--lomm-root",
        default=None,
        help=(
            "Path to the Legends of Might and Magic install folder. When the "
            "LoMM catalog is missing, it is built automatically from this install."
        ),
    )
    p.add_argument(
        "--lomm-catalog",
        default=DEFAULT_LOMM_CATALOG_PATH,
        help=(
            "Path to catalog_lomm.json (built atomically when missing and a "
            "valid --lomm-root is provided)."
        ),
    )
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
        save_catalog(cat_dict, args.catalog)
        print(f"  catalog saved to {args.catalog}")
    catalog = load_catalog(args.catalog)

    # ---------- Validate LoMM root / build its optional catalog ----------
    lomm_root = ""
    if args.lomm_root:
        try:
            from conversion.lomm_to_mm9_service import validate_lomm_root

            lomm_install = validate_lomm_root(args.lomm_root)
            lomm_root = lomm_install.root
            _lomm_catalog, generated = ensure_lomm_catalog(
                lomm_root,
                args.lomm_catalog,
            )
            if generated:
                print(f"  LoMM catalog saved to {args.lomm_catalog}")
        except Exception as exc:
            print(f"ERROR: could not prepare LoMM catalog: {exc}", file=sys.stderr)
            return 2

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

    app = EditorApp(
        root,
        catalog,
        paths,
        catalog_path=args.catalog,
        lomm_catalog_path=args.lomm_catalog,
        initial_lomm_root=lomm_root,
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
