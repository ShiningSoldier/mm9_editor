"""
catalog_panel.py
================

Left-panel widget that lists every object currently in the active level.
Replaces the old global catalog browser.

Public API
----------
- LevelPanel(parent, catalog, on_place_class, on_select_object)
- lp.set_active_level(level_edit)   — called when the active level changes
- lp.refresh()                      — rebuild list from current level state
- lp.highlight_index(world_index)   — sync selection when the map is clicked
"""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, Dict, List, Optional, Tuple

import _path_setup  # noqa: F401
import mm9_patch as patcher
import project as P
from catalog import CATEGORY_COLORS, categorize

# Category display order in the list (objects are sorted by this, then by name)
_CAT_ORDER = [
    "spawn", "npc_civilian", "npc_named", "monster", "creature",
    "prop", "interactive", "door",
    "trigger", "light", "sound",
    "marker", "world", "other",
]
_CAT_RANK = {c: i for i, c in enumerate(_CAT_ORDER)}


def _cat_rank(cat: str) -> int:
    return _CAT_RANK.get(cat, len(_CAT_ORDER))


class LevelPanel(tk.Frame):
    """Lists objects in the active level; wires selection to map + properties."""

    def __init__(
        self,
        parent: tk.Misc,
        catalog: Dict[str, Any],
        on_place_class: Callable[[str], None],
        on_select_object: Callable[[int, patcher.WorldObject], None],
        on_place_preset: Optional[Callable[[str], None]] = None,
        preset_store: Optional[Any] = None,
    ) -> None:
        super().__init__(parent, bg="#1a1d22")
        self.catalog          = catalog
        self.on_place_class   = on_place_class
        self.on_select_object = on_select_object
        self.on_place_preset  = on_place_preset
        self._preset_store    = preset_store

        self._level: Optional[P.LevelEdit] = None
        # Parallel list to listbox rows: (world_index, WorldObject)
        self._items: List[Tuple[int, patcher.WorldObject]] = []

        self._build_ui()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Header
        self.header = tk.Label(
            self, text="Objects", bg="#1a1d22", fg="#dddddd",
            font=("Segoe UI", 10, "bold"), anchor="w",
        )
        self.header.pack(fill="x", padx=8, pady=(8, 0))

        self.subheader = tk.Label(
            self, text="(no level loaded)", bg="#1a1d22", fg="#666",
            font=("Segoe UI", 8), anchor="w",
        )
        self.subheader.pack(fill="x", padx=8, pady=(0, 4))

        # Filter
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._populate())
        filter_row = tk.Frame(self, bg="#1a1d22")
        filter_row.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(filter_row, text="Filter:", bg="#1a1d22", fg="#888",
                 font=("Segoe UI", 8)).pack(side="left")
        tk.Entry(filter_row, textvariable=self.filter_var,
                 bg="#23272d", fg="#e6e6e6", insertbackground="#fff",
                 relief="flat", font=("Segoe UI", 9),
                 ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        # Listbox
        lf = tk.Frame(self, bg="#1a1d22")
        lf.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.listbox = tk.Listbox(
            lf, bg="#0e1116", fg="#e6e6e6",
            selectbackground="#3a4660",
            font=("Consolas", 9), relief="flat",
            highlightthickness=0, activestyle="none",
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(lf, command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_listbox_select)

        # "Add Object" button
        self.add_btn = tk.Button(
            self, text="+ Add Object",
            bg="#2c5e8a", fg="white",
            activebackground="#3a78ad",
            relief="flat", state="disabled",
            command=self._do_add_object,
        )
        self.add_btn.pack(fill="x", padx=8, pady=(4, 8))

    def _category_for_obj(self, obj: patcher.WorldObject) -> str:
        """Return the catalog category for an object, with live-data fallback."""
        entry = self.catalog.get("classes", {}).get(obj.type_str, {})
        cat = entry.get("category")
        if isinstance(cat, str) and cat:
            return cat

        fname = obj.get("Filename")
        filenames = [fname] if isinstance(fname, str) else None
        return categorize(obj.type_str, (p.name for p in obj.props), filenames)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_active_level(self, level: Optional[P.LevelEdit]) -> None:
        self._level = level
        self.add_btn.config(state="normal" if level else "disabled")
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the list from the current level's materialized world."""
        # Remember which world_index was selected so we can restore it
        sel = self.listbox.curselection()
        prev_idx = self._items[sel[0]][0] if sel else None

        self.listbox.delete(0, tk.END)
        self._items = []

        if not self._level or not self._level.world:
            self.subheader.config(text="(no level loaded)")
            return

        world = self._level.materialize()
        n_existing = len(self._level.world.objects)
        deletes = {op.target_index for op in self._level.ops
                   if isinstance(op, P.DeleteOp)}

        # Build (world_index, obj, is_pending) triples
        triples: List[Tuple[int, patcher.WorldObject, bool]] = []

        # Existing objects
        keep_idx = [i for i in range(n_existing) if i not in deletes]
        for j, orig_idx in enumerate(keep_idx):
            triples.append((orig_idx, world.objects[j], False))

        # Pending additions
        added_count = sum(1 for op in self._level.ops if isinstance(op, P.AddOp))
        for k in range(added_count):
            mat_idx = len(keep_idx) + k
            world_idx = n_existing + k
            triples.append((world_idx, world.objects[mat_idx], True))

        # Sort: category rank first, then type name, then object name
        flt = self.filter_var.get().strip().lower()
        triples.sort(key=lambda t: (
            t[2],                          # pending last
            _cat_rank(self._category_for_obj(t[1])),
            t[1].type_str,
            (t[1].get("Name") or ""),
        ))

        # Populate listbox
        restore_row: Optional[int] = None
        for row, (world_idx, obj, pending) in enumerate(triples):
            name    = obj.get("Name") or "(unnamed)"
            type_s  = obj.type_str
            cat     = self._category_for_obj(obj)
            color   = CATEGORY_COLORS.get(cat, CATEGORY_COLORS["other"])
            prefix  = "+ " if pending else "  "

            if flt and flt not in name.lower() and flt not in type_s.lower():
                continue

            self._items.append((world_idx, obj))
            display = f"{prefix}{name}  ({type_s})"
            self.listbox.insert(tk.END, display)
            self.listbox.itemconfig(tk.END, fg=color)

            if world_idx == prev_idx:
                restore_row = len(self._items) - 1

        n = len(self._items)
        total = len(triples)
        level_name = self._level.display_name or "level"
        self.subheader.config(
            text=f"{level_name}  ·  {total} object{'s' if total != 1 else ''}"
                 + (f"  ({n} shown)" if n != total else "")
        )

        # Restore selection
        if restore_row is not None:
            self.listbox.selection_set(restore_row)
            self.listbox.see(restore_row)

    def highlight_index(self, world_index: int) -> None:
        """Sync list selection when the user clicks a dot on the map."""
        for row, (wi, _obj) in enumerate(self._items):
            if wi == world_index:
                self.listbox.selection_clear(0, tk.END)
                self.listbox.selection_set(row)
                self.listbox.see(row)
                return

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_listbox_select(self, _evt=None) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        world_idx, obj = self._items[sel[0]]
        self.on_select_object(world_idx, obj)

    def _do_add_object(self) -> None:
        if not self._level:
            return
        from add_object_dialog import AddObjectDialog
        result = AddObjectDialog.ask(
            self.winfo_toplevel(), self.catalog,
            preset_store=self._preset_store)
        if result is None:
            return
        kind, value = result
        if kind == "preset" and self.on_place_preset:
            self.on_place_preset(value)
        else:
            self.on_place_class(value)


# Keep the old name as an alias so any external code that imports
# CatalogPanel continues to work during the transition.
CatalogPanel = LevelPanel
