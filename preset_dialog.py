"""
preset_dialog.py
================

Modal dialogs for creating and editing user presets.

``EditPresetDialog`` — create a new preset or edit an existing one.
``ManagePresetsDialog`` — list, reorder, edit and delete all presets.

Both are used from the main editor and from the Add-Object dialog.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional

from catalog import CATEGORY_COLORS
from preset_manager import PresetStore, UserPreset


# Categories available in the dropdown (same palette as catalog)
_CATEGORY_OPTS = [
    ("prop",         "Prop"),
    ("npc_civilian", "NPC — civilian"),
    ("npc_named",    "NPC — named"),
    ("monster",      "Monster"),
    ("creature",     "Creature"),
    ("interactive",  "Interactive"),
    ("door",         "Door"),
    ("trigger",      "Trigger"),
    ("light",        "Light"),
    ("sound",        "Sound"),
    ("marker",       "Marker / AI"),
    ("world",        "World / Sky"),
    ("spawn",        "Spawn"),
    ("other",        "Other"),
]
_CAT_KEYS   = [k for k, _ in _CATEGORY_OPTS]
_CAT_LABELS = [v for _, v in _CATEGORY_OPTS]


# ──────────────────────────────────────────────────────────────────────
# EditPresetDialog
# ──────────────────────────────────────────────────────────────────────

class EditPresetDialog(tk.Toplevel):
    """Modal dialog for creating / editing a single :class:`UserPreset`.

    Parameters
    ----------
    parent:
        Owner window.
    catalog_classes:
        ``catalog["classes"]`` dict — used to populate the base-class picker.
    preset:
        If given, the dialog is pre-filled for editing.  If ``None``, it is
        opened in "create" mode.
    initial_overrides:
        Optional ``{prop: value}`` dict — pre-filled as the override table.
        Handy when the user clicks "Save as Preset…" from the Properties panel.
    initial_base_class:
        Pre-select this class in the base-class combo.

    Do not instantiate directly — use :py:meth:`ask`.
    """

    def __init__(
        self,
        parent: tk.Misc,
        catalog_classes: Dict[str, Any],
        preset: Optional[UserPreset] = None,
        initial_overrides: Optional[Dict[str, Any]] = None,
        initial_base_class: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.title("Edit Preset" if preset else "New Preset")
        self.configure(bg="#1a1d22")
        self.resizable(True, True)
        self.result: Optional[UserPreset] = None

        self._catalog_classes = catalog_classes
        self._editing = preset

        # ── state variables ──────────────────────────────────────────
        init_base = (preset.base_class if preset else
                     initial_base_class or "Prop")
        init_cat  = (preset.category   if preset else "other")
        init_ovr  = (dict(preset.overrides) if preset
                     else dict(initial_overrides or {}))
        init_name = preset.name        if preset else ""
        init_desc = preset.description if preset else ""

        self._name_var  = tk.StringVar(value=init_name)
        self._base_var  = tk.StringVar(value=init_base)
        self._desc_var  = tk.StringVar(value=init_desc)
        self._cat_var   = tk.StringVar(value=init_cat)
        # Overrides table: list of [prop_name, value_str] rows
        self._ovr_rows: List[List[tk.StringVar]] = []

        self._build_ui(init_ovr)

        # Size and centre
        self.update_idletasks()
        w, h = 620, 520
        px = parent.winfo_rootx() + parent.winfo_width()  // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2
        self.geometry(f"{w}x{h}+{px - w // 2}+{py - h // 2}")
        self.minsize(480, 380)

        self.transient(parent)
        self.grab_set()
        self.focus_force()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self, init_overrides: Dict[str, Any]) -> None:
        pad = {"padx": 10, "pady": 4}

        # ── Name ─────────────────────────────────────────────────────
        row = tk.Frame(self, bg="#1a1d22"); row.pack(fill="x", **pad)
        tk.Label(row, text="Name:", bg="#1a1d22", fg="#9bb",
                 width=14, anchor="w",
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Entry(row, textvariable=self._name_var,
                 bg="#23272d", fg="#e6e6e6", insertbackground="#fff",
                 relief="flat", font=("Segoe UI", 9)
                 ).pack(side="left", fill="x", expand=True)

        # ── Base class ───────────────────────────────────────────────
        row = tk.Frame(self, bg="#1a1d22"); row.pack(fill="x", **pad)
        tk.Label(row, text="Base class:", bg="#1a1d22", fg="#9bb",
                 width=14, anchor="w",
                 font=("Segoe UI", 9)).pack(side="left")
        all_classes = sorted(self._catalog_classes.keys())
        self._base_combo = ttk.Combobox(
            row, textvariable=self._base_var,
            values=all_classes, state="normal", width=30,
            font=("Consolas", 9),
        )
        self._base_combo.pack(side="left")

        # ── Category ─────────────────────────────────────────────────
        row = tk.Frame(self, bg="#1a1d22"); row.pack(fill="x", **pad)
        tk.Label(row, text="Category:", bg="#1a1d22", fg="#9bb",
                 width=14, anchor="w",
                 font=("Segoe UI", 9)).pack(side="left")
        self._cat_combo = ttk.Combobox(
            row, textvariable=self._cat_var,
            values=_CAT_LABELS, state="readonly", width=22,
            font=("Segoe UI", 9),
        )
        # Map stored key → display label for initial display
        try:
            idx = _CAT_KEYS.index(self._cat_var.get())
            self._cat_combo.current(idx)
        except ValueError:
            self._cat_combo.current(len(_CAT_KEYS) - 1)
        self._cat_combo.pack(side="left")
        tk.Label(row, text="(used for colour in the Add Object dialog)",
                 bg="#1a1d22", fg="#555",
                 font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))

        # ── Description ──────────────────────────────────────────────
        row = tk.Frame(self, bg="#1a1d22"); row.pack(fill="x", **pad)
        tk.Label(row, text="Description:", bg="#1a1d22", fg="#9bb",
                 width=14, anchor="w",
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Entry(row, textvariable=self._desc_var,
                 bg="#23272d", fg="#e6e6e6", insertbackground="#fff",
                 relief="flat", font=("Segoe UI", 9)
                 ).pack(side="left", fill="x", expand=True)

        # ── Overrides table ──────────────────────────────────────────
        tk.Label(self, text="Property overrides", bg="#1a1d22", fg="#aaa",
                 anchor="w", font=("Segoe UI", 9, "bold")
                 ).pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(self, text="These values are applied on top of the cloned template when the preset is placed.",
                 bg="#1a1d22", fg="#555",
                 anchor="w", font=("Segoe UI", 8)
                 ).pack(fill="x", padx=10)

        # Scrollable frame for override rows
        ovr_outer = tk.Frame(self, bg="#1a1d22")
        ovr_outer.pack(fill="both", expand=True, padx=10, pady=4)
        ovr_outer.rowconfigure(0, weight=1)
        ovr_outer.columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(ovr_outer, bg="#0e1116",
                                 highlightthickness=0)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        vsb = tk.Scrollbar(ovr_outer, orient="vertical",
                           command=self._canvas.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self._canvas.configure(yscrollcommand=vsb.set)

        self._ovr_frame = tk.Frame(self._canvas, bg="#0e1116")
        self._canvas_win = self._canvas.create_window(
            (0, 0), window=self._ovr_frame, anchor="nw")
        self._ovr_frame.bind(
            "<Configure>",
            lambda _e: self._canvas.configure(
                scrollregion=self._canvas.bbox("all")))
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfig(
                self._canvas_win, width=e.width))

        # Column headers
        hdr = tk.Frame(self._ovr_frame, bg="#0e1116")
        hdr.pack(fill="x", pady=(2, 0))
        tk.Label(hdr, text="Property", bg="#0e1116", fg="#555",
                 width=20, anchor="w", font=("Segoe UI", 8)).pack(side="left")
        tk.Label(hdr, text="Value", bg="#0e1116", fg="#555",
                 anchor="w", font=("Segoe UI", 8)).pack(side="left", padx=(4, 0))

        # Populate with initial overrides
        for prop, val in init_overrides.items():
            self._add_ovr_row(prop, str(val))

        # Add-row button
        add_btn_row = tk.Frame(self, bg="#1a1d22")
        add_btn_row.pack(fill="x", padx=10, pady=(0, 4))
        tk.Button(add_btn_row, text="+ Add property",
                  bg="#23272d", fg="#7cba7c",
                  activebackground="#33373d", relief="flat",
                  font=("Segoe UI", 8),
                  command=lambda: self._add_ovr_row("", "")
                  ).pack(side="left")

        # ── Buttons ──────────────────────────────────────────────────
        btns = tk.Frame(self, bg="#1a1d22")
        btns.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(btns, text="Cancel", bg="#23272d", fg="#cccccc",
                  activebackground="#33373d", relief="flat",
                  command=self.destroy).pack(side="right")
        tk.Button(btns, text="Save preset",
                  bg="#2c5e8a", fg="white",
                  activebackground="#3a78ad", relief="flat",
                  command=self._do_save,
                  ).pack(side="right", padx=(0, 8))

    def _add_ovr_row(self, prop: str = "", val: str = "") -> None:
        row = tk.Frame(self._ovr_frame, bg="#0e1116")
        row.pack(fill="x", pady=1)

        p_var = tk.StringVar(value=prop)
        v_var = tk.StringVar(value=val)
        self._ovr_rows.append([p_var, v_var])

        tk.Entry(row, textvariable=p_var,
                 bg="#1a1d22", fg="#9bb",
                 insertbackground="#fff", relief="flat",
                 width=20, font=("Consolas", 9)).pack(side="left")
        tk.Entry(row, textvariable=v_var,
                 bg="#1a1d22", fg="#e6e6e6",
                 insertbackground="#fff", relief="flat",
                 font=("Consolas", 9)).pack(side="left", fill="x",
                                            expand=True, padx=(4, 4))

        def _del(r=row, pair=[p_var, v_var]):
            r.destroy()
            if pair in self._ovr_rows:
                self._ovr_rows.remove(pair)

        tk.Button(row, text="✕", bg="#0e1116", fg="#883232",
                  activebackground="#1a1d22", relief="flat",
                  font=("Segoe UI", 8),
                  command=_del).pack(side="right")

    # ------------------------------------------------------------------
    # Validation / save
    # ------------------------------------------------------------------

    def _do_save(self) -> None:
        name = self._name_var.get().strip()
        if not name:
            messagebox.showerror("Missing name",
                                 "Please enter a name for this preset.",
                                 parent=self)
            return

        base_class = self._base_var.get().strip()
        if not base_class:
            messagebox.showerror("Missing base class",
                                 "Please choose a base class.",
                                 parent=self)
            return

        # Resolve category label → key
        cat_label = self._cat_var.get()
        try:
            cat_key = _CAT_KEYS[_CAT_LABELS.index(cat_label)]
        except (ValueError, IndexError):
            cat_key = self._cat_var.get()   # stored as key already
            if cat_key not in _CAT_KEYS:
                cat_key = "other"

        # Build overrides dict — skip blank rows, try JSON-parse values
        overrides: Dict[str, Any] = {}
        for p_var, v_var in self._ovr_rows:
            prop = p_var.get().strip()
            val  = v_var.get().strip()
            if not prop:
                continue
            # Try to convert numeric and boolean-ish strings
            overrides[prop] = _coerce_value(val)

        self.result = UserPreset(
            name=name,
            base_class=base_class,
            overrides=overrides,
            category=cat_key,
            description=self._desc_var.get().strip(),
        )
        self.destroy()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def ask(
        cls,
        parent: tk.Misc,
        catalog_classes: Dict[str, Any],
        preset: Optional[UserPreset] = None,
        initial_overrides: Optional[Dict[str, Any]] = None,
        initial_base_class: Optional[str] = None,
    ) -> Optional[UserPreset]:
        """Show the dialog and return the configured :class:`UserPreset`, or
        ``None`` if the user cancelled."""
        dlg = cls(parent, catalog_classes, preset=preset,
                  initial_overrides=initial_overrides,
                  initial_base_class=initial_base_class)
        parent.wait_window(dlg)
        return dlg.result


# ──────────────────────────────────────────────────────────────────────
# ManagePresetsDialog
# ──────────────────────────────────────────────────────────────────────

class ManagePresetsDialog(tk.Toplevel):
    """Dialog that lets the user view, reorder, edit and delete all presets.

    Changes are committed to *store* only when the user clicks **Save & close**.
    """

    def __init__(self, parent: tk.Misc,
                 store: PresetStore,
                 catalog_classes: Dict[str, Any]) -> None:
        super().__init__(parent)
        self.title("Manage Presets")
        self.configure(bg="#1a1d22")
        self.resizable(True, True)

        self._store   = store
        self._cat_cls = catalog_classes
        # Work on a mutable copy so we can cancel without side effects
        self._working: List[UserPreset] = list(store.presets)

        self._build_ui()

        self.update_idletasks()
        w, h = 560, 420
        px = parent.winfo_rootx() + parent.winfo_width()  // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2
        self.geometry(f"{w}x{h}+{px - w // 2}+{py - h // 2}")
        self.minsize(400, 300)

        self.transient(parent)
        self.grab_set()
        self.focus_force()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Header
        tk.Label(self, text="User Presets", bg="#1a1d22", fg="#dddddd",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=(10, 4))

        # List + side buttons
        body = tk.Frame(self, bg="#1a1d22")
        body.pack(fill="both", expand=True, padx=10, pady=4)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self.lb = tk.Listbox(
            body, bg="#0e1116", fg="#e6e6e6",
            selectbackground="#3a4660",
            relief="flat", highlightthickness=0,
            activestyle="none", exportselection=False,
            font=("Segoe UI", 9),
        )
        self.lb.grid(row=0, column=0, sticky="nsew")
        sb = tk.Scrollbar(body, command=self.lb.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.lb.config(yscrollcommand=sb.set)
        self.lb.bind("<Double-Button-1>", lambda _: self._do_edit())

        btns = tk.Frame(body, bg="#1a1d22")
        btns.grid(row=0, column=2, sticky="ns", padx=(6, 0))
        for txt, cmd in [
            ("Edit",    self._do_edit),
            ("Delete",  self._do_delete),
            ("▲ Up",    self._do_up),
            ("▼ Down",  self._do_down),
        ]:
            tk.Button(btns, text=txt, bg="#23272d", fg="#cccccc",
                      activebackground="#33373d", relief="flat",
                      font=("Segoe UI", 9),
                      command=cmd).pack(fill="x", pady=2)

        # Bottom buttons
        foot = tk.Frame(self, bg="#1a1d22")
        foot.pack(fill="x", padx=10, pady=(4, 10))
        tk.Button(foot, text="Cancel", bg="#23272d", fg="#cccccc",
                  activebackground="#33373d", relief="flat",
                  command=self.destroy).pack(side="right")
        tk.Button(foot, text="Save & close",
                  bg="#2c5e8a", fg="white",
                  activebackground="#3a78ad", relief="flat",
                  command=self._do_save).pack(side="right", padx=(0, 8))

        self._refresh_list()

    def _refresh_list(self) -> None:
        sel = self.lb.curselection()
        self.lb.delete(0, tk.END)
        for p in self._working:
            color = CATEGORY_COLORS.get(p.category, "#808080")
            self.lb.insert(tk.END, f"  {p.name}  [{p.base_class}]")
            self.lb.itemconfig(tk.END, fg=color)
        if sel:
            idx = min(sel[0], len(self._working) - 1)
            self.lb.selection_set(idx)

    def _selected_index(self) -> Optional[int]:
        sel = self.lb.curselection()
        return sel[0] if sel else None

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _do_edit(self) -> None:
        i = self._selected_index()
        if i is None:
            return
        result = EditPresetDialog.ask(
            self, self._cat_cls, preset=self._working[i])
        if result:
            self._working[i] = result
            self._refresh_list()
            self.lb.selection_set(i)

    def _do_delete(self) -> None:
        i = self._selected_index()
        if i is None:
            return
        name = self._working[i].name
        if not messagebox.askyesno("Delete preset",
                                   f"Delete preset {name!r}?",
                                   parent=self):
            return
        del self._working[i]
        self._refresh_list()

    def _do_up(self) -> None:
        i = self._selected_index()
        if i is None or i == 0:
            return
        self._working[i - 1], self._working[i] = (
            self._working[i], self._working[i - 1])
        self._refresh_list()
        self.lb.selection_set(i - 1)

    def _do_down(self) -> None:
        i = self._selected_index()
        if i is None or i >= len(self._working) - 1:
            return
        self._working[i], self._working[i + 1] = (
            self._working[i + 1], self._working[i])
        self._refresh_list()
        self.lb.selection_set(i + 1)

    def _do_save(self) -> None:
        # Rebuild the store from the working list
        # Clear existing, then re-add in order
        for name in list(self._store.names()):
            try:
                self._store.remove(name)
            except KeyError:
                pass
        for p in self._working:
            self._store.add(p)
        try:
            self._store.save()
        except Exception as e:
            messagebox.showerror("Save failed", str(e), parent=self)
            return
        self.destroy()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def open(cls, parent: tk.Misc,
             store: PresetStore,
             catalog_classes: Dict[str, Any]) -> None:
        """Open the dialog.  Changes are committed directly to *store* on
        "Save & close"."""
        dlg = cls(parent, store, catalog_classes)
        parent.wait_window(dlg)


# ──────────────────────────────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────────────────────────────

def _coerce_value(s: str) -> Any:
    """Try to parse *s* as int, float, or bool; fall back to str."""
    import json as _json
    stripped = s.strip()
    # Boolean keywords
    if stripped.lower() in ("true",  "1"): return 1
    if stripped.lower() in ("false", "0"): return 0
    # Try int
    try:
        return int(stripped)
    except ValueError:
        pass
    # Try float
    try:
        return float(stripped)
    except ValueError:
        pass
    # Try JSON (e.g. lists like "[1.0, 2.0, 3.0]")
    if stripped.startswith(("[", "{")):
        try:
            return _json.loads(stripped)
        except Exception:
            pass
    return s
