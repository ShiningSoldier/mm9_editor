"""
add_object_dialog.py
====================

Modal "Add Object" dialog — replaces the old catalog panel's class-picker
role.  Shows all WorldObject classes from the catalog plus any user-defined
presets.  Returns a ``(kind, value)`` tuple or ``None`` if cancelled.

Return values
-------------
``("class",  class_name)``   — place a built-in WorldObject class
``("preset", preset_name)``  — place a user preset

Usage::

    from add_object_dialog import AddObjectDialog
    from preset_manager import PresetStore

    result = AddObjectDialog.ask(parent, catalog, preset_store)
    if result:
        kind, value = result
        if kind == "class":
            editor._begin_place_class(value)
        elif kind == "preset":
            editor._begin_place_preset(value)
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional, Tuple

from catalog import CATEGORY_COLORS
from preset_manager import PresetStore


# Friendly display names for categories (same keys as CATEGORY_COLORS)
CATEGORY_LABELS: Dict[str, str] = {
    "spawn":        "Spawn points",
    "trigger":      "Triggers",
    "light":        "Lights",
    "sound":        "Sound",
    "door":         "Doors",
    "npc_civilian": "NPCs — civilian",
    "npc_named":    "NPCs — named",
    "monster":      "Monsters",
    "creature":     "Creatures",
    "prop":         "Props",
    "marker":       "Markers / AI",
    "world":        "World / Sky",
    "interactive":  "Interactive",
    "other":        "Other",
}

# Preferred display order for the category list
CATEGORY_ORDER = [
    "npc_civilian", "npc_named", "monster", "creature",
    "prop", "interactive", "door",
    "trigger", "light", "sound",
    "marker", "world", "spawn", "other",
]

# Internal sentinel key for the presets bucket
_PRESETS_CAT = "__presets__"


class AddObjectDialog(tk.Toplevel):
    """Modal class-picker / preset-picker dialog.

    Do not instantiate directly — use :py:meth:`ask`.
    """

    def __init__(self, parent: tk.Misc,
                 catalog: Dict[str, Any],
                 preset_store: Optional[PresetStore] = None) -> None:
        super().__init__(parent)
        self.title("Add Object")
        self.configure(bg="#1a1d22")
        self.resizable(True, True)
        self.result: Optional[Tuple[str, str]] = None

        self._catalog_classes = catalog.get("classes", {})
        self._preset_store    = preset_store
        self._build_category_index()
        self._build_ui()

        # Size and centre over parent
        self.update_idletasks()
        w, h = 700, 520
        px = parent.winfo_rootx() + parent.winfo_width()  // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2
        self.geometry(f"{w}x{h}+{px - w // 2}+{py - h // 2}")
        self.minsize(500, 360)

        # Modal
        self.transient(parent)
        self.grab_set()
        self.focus_force()

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _build_category_index(self) -> None:
        """Group catalog classes by category, preserving CATEGORY_ORDER."""
        self._by_cat: Dict[str, List[str]] = {}
        for cls, entry in self._catalog_classes.items():
            cat = entry.get("category", "other")
            self._by_cat.setdefault(cat, []).append(cls)
        for cat in self._by_cat:
            self._by_cat[cat].sort()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ── Top bar ──────────────────────────────────────────────────
        top = tk.Frame(self, bg="#1a1d22")
        top.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(top, text="Add Object", bg="#1a1d22", fg="#dddddd",
                 font=("Segoe UI", 11, "bold")).pack(side="left")

        # ── Body (category list | class list) ────────────────────────
        body = tk.Frame(self, bg="#1a1d22")
        body.pack(fill="both", expand=True, padx=10, pady=4)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # Left — category list
        cat_frame = tk.Frame(body, bg="#1a1d22")
        cat_frame.grid(row=0, column=0, sticky="ns", padx=(0, 6))
        tk.Label(cat_frame, text="Category", bg="#1a1d22", fg="#888",
                 font=("Segoe UI", 8)).pack(anchor="w")
        self.cat_listbox = tk.Listbox(
            cat_frame, bg="#0e1116", fg="#cccccc",
            selectbackground="#3a4660", relief="flat",
            highlightthickness=0, activestyle="none",
            exportselection=False,
            width=18, font=("Segoe UI", 9),
        )
        self.cat_listbox.pack(fill="both", expand=True)
        self.cat_listbox.bind("<<ListboxSelect>>", self._on_cat_select)

        self._cat_keys: List[str] = []   # parallel to cat_listbox rows

        # "My Presets" entry — only if there are any presets
        has_presets = (self._preset_store is not None
                       and len(self._preset_store) > 0)
        if has_presets:
            self.cat_listbox.insert(tk.END, "  ★ My Presets")
            self.cat_listbox.itemconfig(tk.END, fg="#f0c060")
            self._cat_keys.append(_PRESETS_CAT)

        # "All" entry
        self.cat_listbox.insert(tk.END, "  All")
        self._cat_keys.append("__all__")

        for cat in CATEGORY_ORDER:
            if cat not in self._by_cat:
                continue
            label = CATEGORY_LABELS.get(cat, cat)
            color = CATEGORY_COLORS.get(cat, "#808080")
            self.cat_listbox.insert(tk.END, f"  {label}")
            self.cat_listbox.itemconfig(tk.END, fg=color)
            self._cat_keys.append(cat)

        # Select "My Presets" by default if present, else "All"
        self.cat_listbox.selection_set(0)

        # Right — filter + class list
        right = tk.Frame(body, bg="#1a1d22")
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        filter_row = tk.Frame(right, bg="#1a1d22")
        filter_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        tk.Label(filter_row, text="Filter:", bg="#1a1d22", fg="#888",
                 font=("Segoe UI", 8)).pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._populate_classes())
        self.filter_entry = tk.Entry(
            filter_row, textvariable=self.filter_var,
            bg="#23272d", fg="#e6e6e6", insertbackground="#fff",
            relief="flat", font=("Segoe UI", 9),
        )
        self.filter_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
        # Sort toggle: A–Z vs most-common first (only meaningful for catalog classes)
        self._sort_by_count = tk.BooleanVar(value=False)
        self._sort_cb = tk.Checkbutton(
            filter_row, text="Most common first",
            variable=self._sort_by_count,
            command=self._populate_classes,
            bg="#1a1d22", fg="#888",
            selectcolor="#23272d",
            activebackground="#1a1d22",
            font=("Segoe UI", 8),
        )
        self._sort_cb.pack(side="left", padx=(8, 0))

        cls_frame = tk.Frame(right, bg="#1a1d22")
        cls_frame.grid(row=1, column=0, sticky="nsew")
        cls_frame.rowconfigure(0, weight=1)
        cls_frame.columnconfigure(0, weight=1)
        self.cls_listbox = tk.Listbox(
            cls_frame, bg="#0e1116", fg="#e6e6e6",
            selectbackground="#3a4660", relief="flat",
            highlightthickness=0, activestyle="none",
            exportselection=False,
            font=("Consolas", 9),
        )
        self.cls_listbox.grid(row=0, column=0, sticky="nsew")
        sb = tk.Scrollbar(cls_frame, command=self.cls_listbox.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.cls_listbox.config(yscrollcommand=sb.set)
        self.cls_listbox.bind("<<ListboxSelect>>", self._on_cls_select)
        self.cls_listbox.bind("<Double-Button-1>", lambda _e: self._do_place())

        # (kind, value) parallel to cls_listbox rows
        self._cls_items: List[Tuple[str, str]] = []

        # ── Detail bar ───────────────────────────────────────────────
        self.detail = tk.Label(
            self, text="", bg="#1a1d22", fg="#888",
            anchor="w", justify="left", wraplength=660,
            font=("Segoe UI", 8),
        )
        self.detail.pack(fill="x", padx=10, pady=(0, 4))

        # ── Buttons ──────────────────────────────────────────────────
        btns = tk.Frame(self, bg="#1a1d22")
        btns.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(btns, text="Cancel", bg="#23272d", fg="#cccccc",
                  activebackground="#33373d", relief="flat",
                  command=self.destroy).pack(side="right")
        self.place_btn = tk.Button(
            btns, text="Place on map →",
            bg="#2c5e8a", fg="white",
            activebackground="#3a78ad", relief="flat",
            state="disabled",
            command=self._do_place,
        )
        self.place_btn.pack(side="right", padx=(0, 8))

        # Focus filter entry for immediate typing
        self.filter_entry.focus_set()
        self._populate_classes()

    # ------------------------------------------------------------------
    # Populate / filter
    # ------------------------------------------------------------------

    def _active_category(self) -> str:
        sel = self.cat_listbox.curselection()
        if not sel:
            return "__all__"
        return self._cat_keys[sel[0]]

    def _populate_classes(self) -> None:
        flt    = self.filter_var.get().strip().lower()
        cat    = self._active_category()
        by_cnt = self._sort_by_count.get()

        self.cls_listbox.delete(0, tk.END)
        self._cls_items = []

        # ── User presets ─────────────────────────────────────────────
        if cat == _PRESETS_CAT:
            self._sort_cb.config(state="disabled")
            presets = (self._preset_store.presets
                       if self._preset_store else [])
            for p in presets:
                if flt and flt not in p.name.lower():
                    continue
                color = CATEGORY_COLORS.get(p.category, "#f0c060")
                label = f"★ {p.name}  [{p.base_class}]"
                self._cls_items.append(("preset", p.name))
                self.cls_listbox.insert(tk.END, label)
                self.cls_listbox.itemconfig(tk.END, fg=color)
            self.place_btn.config(state="disabled")
            self.detail.config(text="")
            return

        # ── Catalog classes ───────────────────────────────────────────
        self._sort_cb.config(state="normal")

        if cat == "__all__":
            candidates = list(self._catalog_classes.keys())
        else:
            candidates = list(self._by_cat.get(cat, []))

        # Also prepend matching presets when "All" is selected
        if cat == "__all__" and self._preset_store:
            for p in self._preset_store.presets:
                if flt and flt not in p.name.lower():
                    continue
                color = CATEGORY_COLORS.get(p.category, "#f0c060")
                label = f"★ {p.name}  [{p.base_class}]"
                self._cls_items.append(("preset", p.name))
                self.cls_listbox.insert(tk.END, label)
                self.cls_listbox.itemconfig(tk.END, fg=color)

        # Apply text filter to catalog classes
        if flt:
            candidates = [c for c in candidates if flt in c.lower()]

        # Sort: instance-count descending or alphabetical
        if by_cnt:
            candidates.sort(
                key=lambda c: -self._catalog_classes[c].get("instance_count", 0))
        else:
            candidates.sort()

        for cls in candidates:
            entry    = self._catalog_classes[cls]
            item_cat = entry.get("category", "other")
            color    = CATEGORY_COLORS.get(item_cat, "#808080")
            count    = entry.get("instance_count", 0)
            label    = f"{cls}  ({count}×)" if count else cls
            self._cls_items.append(("class", cls))
            self.cls_listbox.insert(tk.END, label)
            self.cls_listbox.itemconfig(tk.END, fg=color)

        self.place_btn.config(state="disabled")
        self.detail.config(text="")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_cat_select(self, _evt=None) -> None:
        self._populate_classes()

    def _on_cls_select(self, _evt=None) -> None:
        sel = self.cls_listbox.curselection()
        if not sel:
            self.place_btn.config(state="disabled")
            self.detail.config(text="")
            return

        kind, value = self._cls_items[sel[0]]
        self.place_btn.config(state="normal")

        if kind == "preset" and self._preset_store:
            p = self._preset_store.get(value)
            if p:
                ovr_summary = ", ".join(
                    f"{k}={v!r}" for k, v in list(p.overrides.items())[:4])
                if len(p.overrides) > 4:
                    ovr_summary += f"  (+{len(p.overrides) - 4} more)"
                self.detail.config(text=(
                    f"★ Preset: {p.name}  ·  base: {p.base_class}  ·  "
                    f"overrides: {ovr_summary or '(none)'}  "
                    f"{'·  ' + p.description if p.description else ''}"
                ))
                return

        # Catalog class detail
        entry    = self._catalog_classes.get(value, {})
        template = entry.get("template") or {}
        cat      = entry.get("category", "other")
        self.detail.config(text=(
            f"{value}  ·  category: {cat}  ·  "
            f"{entry.get('instance_count', 0)} instances across "
            f"{len(entry.get('levels', []))} levels  ·  "
            f"template: {template.get('source_instance', '?')} "
            f"from {template.get('source_level', '?')}"
        ))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _do_place(self) -> None:
        sel = self.cls_listbox.curselection()
        if not sel:
            return
        self.result = self._cls_items[sel[0]]
        self.destroy()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def ask(cls, parent: tk.Misc,
            catalog: Dict[str, Any],
            preset_store: Optional[PresetStore] = None,
            ) -> Optional[Tuple[str, str]]:
        """Show the dialog modally.

        Returns a ``(kind, value)`` tuple where *kind* is ``"class"`` or
        ``"preset"`` and *value* is the class name or preset name
        respectively.  Returns ``None`` if the user cancelled.
        """
        dlg = cls(parent, catalog, preset_store=preset_store)
        parent.wait_window(dlg)
        return dlg.result
