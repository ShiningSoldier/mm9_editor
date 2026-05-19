"""
rez_picker.py
=============

Modal that lists levels inside a .rez archive and lets the user pick one to
open in the editor. This is the editor's normal level-open workflow.
"""

from __future__ import annotations

import os
import tkinter as tk
from typing import Callable, List

import _path_setup  # noqa: F401
from core import rezmgr


class RezPicker(tk.Toplevel):
    def __init__(self, parent: tk.Misc, rez_path: str,
                 on_pick: Callable[[str, str], None]):
        super().__init__(parent)
        self._parent_window = parent.winfo_toplevel()
        self.title(f"Open level from {os.path.basename(rez_path)}")
        self.configure(bg="#1a1d22")
        self.geometry("680x520")
        self.rez_path = rez_path
        self.on_pick  = on_pick
        self.transient(self._parent_window)
        self.protocol("WM_DELETE_WINDOW", self._close)

        tk.Label(self, text=f"Pick a level inside {os.path.basename(rez_path)}:",
                 bg="#1a1d22", fg="#dddddd",
                 font=("Segoe UI", 10, "bold")
                 ).pack(anchor="w", padx=10, pady=(10, 4))

        # Filter
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._populate())
        ent = tk.Entry(self, textvariable=self.filter_var, bg="#23272d",
                       fg="#e6e6e6", insertbackground="#fff", relief="flat")
        ent.pack(fill="x", padx=10, pady=2)
        ent.focus_set()

        # Listbox
        listframe = tk.Frame(self, bg="#1a1d22")
        listframe.pack(fill="both", expand=True, padx=10, pady=4)
        self.listbox = tk.Listbox(listframe, bg="#0e1116", fg="#e6e6e6",
                                  selectbackground="#3a4660",
                                  font=("Consolas", 9), relief="flat",
                                  highlightthickness=0, activestyle="none")
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(listframe, command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.bind("<Double-Button-1>", lambda e: self._pick())
        self.listbox.bind("<Return>",          lambda e: self._pick())
        self.listbox.bind("<MouseWheel>",      self._on_mousewheel)
        self.listbox.bind("<Button-4>",        self._on_mousewheel)
        self.listbox.bind("<Button-5>",        self._on_mousewheel)
        self.listbox.bind("<Enter>",           lambda _e: self.listbox.focus_set())

        # Detail label
        self.detail = tk.Label(self, text="", bg="#1a1d22", fg="#aaa",
                               anchor="w", justify="left",
                               font=("Segoe UI", 8))
        self.detail.pack(fill="x", padx=10, pady=(0, 4))
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        # Buttons
        bottom = tk.Frame(self, bg="#1a1d22")
        bottom.pack(fill="x", padx=10, pady=10)
        tk.Button(bottom, text="Cancel", bg="#23272d", fg="white",
                  relief="flat", command=self._close
                  ).pack(side="right")
        tk.Button(bottom, text="Open", bg="#2c5e8a", fg="white",
                  activebackground="#3a78ad", relief="flat",
                  command=self._pick
                  ).pack(side="right", padx=(0, 8))

        self._items: List[tuple] = []  # (vpath, type_tag, size, is_editable)
        self._load_entries()
        self._populate()
        self._activate_modal()

    # ---------- internals ----------

    def _load_entries(self) -> None:
        with rezmgr.RezReader(self.rez_path) as r:
            for vp in r.list_paths():
                ent = r.find(vp)
                if ent is None or ent.size == 0:
                    continue
                # The type-tag in MM9's REZ files is unreliable (different
                # build/patch versions of the game ship the same .DAT under
                # different type-tag conventions). Always peek the actual
                # payload magic to decide if a file is a v66 .DAT.
                magic = r.peek_bytes(vp, 4)
                editable = rezmgr.is_v66_dat_magic(magic)
                self._items.append((vp, ent.type_str, ent.size, editable))

    def _activate_modal(self) -> None:
        """Keep the picker above the editor while it is open.

        On Windows a non-transient Toplevel can slip behind the main Tk window
        when mouse-wheel focus changes.  A transient grab makes this behave
        like the modal picker it visually is.
        """
        self.lift(self._parent_window)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_force()
        self.bind("<FocusOut>", lambda _e: self.after(10, self._lift_if_open))

    def _lift_if_open(self) -> None:
        if self.winfo_exists():
            self.lift(self._parent_window)

    def _on_mousewheel(self, event) -> str:
        if getattr(event, "num", None) == 4:
            steps = -3
        elif getattr(event, "num", None) == 5:
            steps = 3
        else:
            steps = -int(event.delta / 120) if event.delta else 0
        if steps:
            self.listbox.yview_scroll(steps, "units")
        self._lift_if_open()
        return "break"

    def _close(self) -> None:
        try:
            if self.grab_current() is self:
                self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _populate(self) -> None:
        flt = self.filter_var.get().strip().lower()
        self.listbox.delete(0, tk.END)
        self._visible = []
        for item in self._items:
            vp, tt, sz, ed = item
            if flt and flt not in vp.lower():
                continue
            tag_label = "DAT" if ed else (tt or "?")
            label = f"[{tag_label:<3}]  {sz:>10,}  {vp}"
            self.listbox.insert(tk.END, label)
            self._visible.append(item)
            if not ed:
                self.listbox.itemconfig(tk.END, fg="#808080")

    def _on_select(self, _evt) -> None:
        sel = self.listbox.curselection()
        if not sel: return
        vp, tt, sz, ed = self._visible[sel[0]]
        if ed:
            self.detail.config(
                text=f"{vp}  ({sz:,} bytes, type-tag={tt!r}) — v66 .DAT, editable")
        else:
            self.detail.config(
                text=f"{vp}  ({sz:,} bytes, type-tag={tt!r})\n"
                     f"Not a v66 .DAT (magic doesn\'t match). Probably an .ED "
                     f"editor source — those aren\'t loaded by the game and "
                     f"can be ignored.")

    def _pick(self) -> None:
        sel = self.listbox.curselection()
        if not sel: return
        vp, tt, sz, ed = self._visible[sel[0]]
        if not ed:
            return  # silently ignore
        self.on_pick(self.rez_path, vp)
        self._close()
