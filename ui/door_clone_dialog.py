"""
door_clone_dialog.py
====================

Small Tk dialog for choosing an existing physical door to clone.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk, messagebox
from typing import Callable, Iterable, Optional


@dataclass(frozen=True)
class DoorCloneDialogResult:
    source_name: str
    new_name: str
    include_pair: bool = True


class DoorCloneDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        door_names: Iterable[str],
        default_source: str = "",
        default_new_name: str = "",
        default_include_pair: bool = True,
        suggest_name: Optional[Callable[[str], str]] = None,
        describe_source: Optional[Callable[[str], str]] = None,
    ) -> None:
        super().__init__(parent)
        self.title("Clone Physical Door")
        self.configure(bg="#1a1d22")
        self.resizable(False, False)
        self.result: Optional[DoorCloneDialogResult] = None
        self._door_names = sorted(str(name) for name in door_names if str(name))
        self._suggest_name = suggest_name
        self._describe_source = describe_source
        self._last_suggested_name = default_new_name

        self.source_var = tk.StringVar(value=default_source or (self._door_names[0] if self._door_names else ""))
        self.name_var = tk.StringVar(value=default_new_name)
        self.include_pair_var = tk.BooleanVar(value=bool(default_include_pair))

        self._build_ui()
        self._update_source_details()
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Return>", lambda _evt: self._ok())
        self.bind("<Escape>", lambda _evt: self._cancel())
        self.name_entry.focus_set()

    def _build_ui(self) -> None:
        outer = tk.Frame(self, bg="#1a1d22", padx=14, pady=12)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer, text="Clone Physical Door",
            bg="#1a1d22", fg="#e6e6e6",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        tk.Label(outer, text="Source:", bg="#1a1d22", fg="#aaaaaa").grid(
            row=1, column=0, sticky="w", pady=4)
        self.source_combo = ttk.Combobox(
            outer,
            textvariable=self.source_var,
            values=self._door_names,
            state="readonly",
            width=34,
        )
        self.source_combo.grid(row=1, column=1, sticky="ew", pady=4)
        self.source_combo.bind("<<ComboboxSelected>>", self._on_source_changed)

        tk.Label(outer, text="New name:", bg="#1a1d22", fg="#aaaaaa").grid(
            row=3, column=0, sticky="w", pady=4)
        self.name_entry = tk.Entry(
            outer,
            textvariable=self.name_var,
            bg="#23272d", fg="#e6e6e6",
            insertbackground="#ffffff",
            relief="flat",
            width=36,
        )
        self.name_entry.grid(row=3, column=1, sticky="ew", pady=4)

        self.details_label = tk.Label(
            outer,
            text="",
            bg="#1a1d22",
            fg="#888888",
            font=("Segoe UI", 8),
            anchor="w",
            justify="left",
            wraplength=360,
        )
        self.details_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        self.pair_check = tk.Checkbutton(
            outer,
            text="Clone paired door leaf when this source has one",
            variable=self.include_pair_var,
            bg="#1a1d22", fg="#d0d0d0",
            selectcolor="#23272d",
            activebackground="#1a1d22",
            activeforeground="#ffffff",
        )
        self.pair_check.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 4))

        buttons = tk.Frame(outer, bg="#1a1d22")
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        tk.Button(
            buttons, text="Cancel",
            bg="#30343b", fg="#e6e6e6",
            relief="flat",
            command=self._cancel,
        ).pack(side="right", padx=(6, 0))
        tk.Button(
            buttons, text="Place Clone",
            bg="#2c5e8a", fg="white",
            activebackground="#3a78ad",
            relief="flat",
            command=self._ok,
        ).pack(side="right")

    def _on_source_changed(self, _evt=None) -> None:
        if self._suggest_name is None:
            return
        current_name = self.name_var.get().strip()
        if current_name and current_name != self._last_suggested_name:
            return
        suggested = self._suggest_name(self.source_var.get().strip())
        self._last_suggested_name = suggested
        self.name_var.set(suggested)
        self._update_source_details()

    def _update_source_details(self) -> None:
        if self._describe_source is None:
            self.details_label.config(text="")
            return
        self.details_label.config(text=self._describe_source(self.source_var.get().strip()))

    def _ok(self) -> None:
        source = self.source_var.get().strip()
        new_name = self.name_var.get().strip()
        if not source:
            messagebox.showerror("Missing source", "Choose a door to clone.", parent=self)
            return
        if not new_name:
            messagebox.showerror("Missing name", "Enter a new door name.", parent=self)
            return
        self.result = DoorCloneDialogResult(
            source_name=source,
            new_name=new_name,
            include_pair=bool(self.include_pair_var.get()),
        )
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    @classmethod
    def ask(
        cls,
        parent: tk.Misc,
        door_names: Iterable[str],
        default_source: str = "",
        default_new_name: str = "",
        default_include_pair: bool = True,
        suggest_name: Optional[Callable[[str], str]] = None,
        describe_source: Optional[Callable[[str], str]] = None,
    ) -> Optional[DoorCloneDialogResult]:
        dlg = cls(
            parent,
            door_names,
            default_source=default_source,
            default_new_name=default_new_name,
            default_include_pair=default_include_pair,
            suggest_name=suggest_name,
            describe_source=describe_source,
        )
        parent.wait_window(dlg)
        return dlg.result
