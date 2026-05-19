"""
lomm_conversion_dialog.py
=========================

Modal UI for adding a converted Legends of Might and Magic level to MM9.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

import _path_setup  # noqa: F401
from conversion import lomm_to_mm9_service as service


def default_converted_name(level_name: str) -> str:
    value = str(level_name or "").replace("\\", "/").strip().strip("/")
    if value.upper().startswith("WORLDS/"):
        value = value.split("/", 1)[1]
    if value.upper().endswith(".DAT"):
        value = value[:-4]
    return f"{value}_MM9" if value else ""


def format_success_message(result: service.InsertConvertedLevelResult) -> str:
    return (
        f"Added {result.added_virtual_path} to:\n"
        f"{result.worlds_rez}\n\n"
        f"Backup:\n{result.backup_path}"
    )


class LommConversionDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        mm9_root: str,
        backup_root: Optional[str] = None,
        catalog_json: Optional[str] = None,
        initial_lomm_root: str = "",
        on_success: Optional[Callable[[service.InsertConvertedLevelResult, str], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._parent_window = parent.winfo_toplevel()
        self.mm9_root = mm9_root
        self.backup_root = backup_root
        self.catalog_json = catalog_json
        self.on_success = on_success
        self._levels: list[service.LommLevelEntry] = []
        self.result: Optional[service.InsertConvertedLevelResult] = None
        self._last_suggested_name = ""

        self.title("LoMM to MM9 conversion")
        self.configure(bg="#1a1d22")
        self.geometry("720x360")
        self.minsize(640, 320)
        self.transient(self._parent_window)

        self.lomm_root_var = tk.StringVar(value=initial_lomm_root)
        self.level_var = tk.StringVar()
        self.converted_name_var = tk.StringVar()
        self.status_var = tk.StringVar(value="")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _evt: self._close())
        self._activate_modal()
        if initial_lomm_root:
            self.after_idle(self._load_levels)

    def _build_ui(self) -> None:
        outer = tk.Frame(self, bg="#1a1d22", padx=14, pady=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)

        tk.Label(
            outer,
            text="LoMM to MM9 conversion",
            bg="#1a1d22",
            fg="#e6e6e6",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))

        tk.Label(outer, text="LoMM install:", bg="#1a1d22", fg="#aaaaaa").grid(
            row=1, column=0, sticky="w", pady=5)
        self.root_entry = tk.Entry(
            outer,
            textvariable=self.lomm_root_var,
            bg="#23272d",
            fg="#e6e6e6",
            insertbackground="#ffffff",
            relief="flat",
        )
        self.root_entry.grid(row=1, column=1, sticky="ew", pady=5, padx=(8, 6))
        self.browse_btn = tk.Button(
            outer,
            text="Browse...",
            bg="#30343b",
            fg="#e6e6e6",
            relief="flat",
            command=self._browse_lomm_root,
        )
        self.browse_btn.grid(row=1, column=2, sticky="ew", pady=5)

        self.load_btn = tk.Button(
            outer,
            text="Load levels",
            bg="#30343b",
            fg="#e6e6e6",
            relief="flat",
            command=self._load_levels,
        )
        self.load_btn.grid(row=2, column=1, sticky="w", pady=(0, 10), padx=(8, 0))

        tk.Label(outer, text="LoMM level:", bg="#1a1d22", fg="#aaaaaa").grid(
            row=3, column=0, sticky="w", pady=5)
        self.level_combo = ttk.Combobox(
            outer,
            textvariable=self.level_var,
            state="readonly",
            values=[],
        )
        self.level_combo.grid(row=3, column=1, columnspan=2, sticky="ew", pady=5, padx=(8, 0))
        self.level_combo.bind("<<ComboboxSelected>>", self._on_level_selected)

        tk.Label(outer, text="New MM9 level:", bg="#1a1d22", fg="#aaaaaa").grid(
            row=4, column=0, sticky="w", pady=5)
        self.name_entry = tk.Entry(
            outer,
            textvariable=self.converted_name_var,
            bg="#23272d",
            fg="#e6e6e6",
            insertbackground="#ffffff",
            relief="flat",
        )
        self.name_entry.grid(row=4, column=1, columnspan=2, sticky="ew", pady=5, padx=(8, 0))

        self.status_label = tk.Label(
            outer,
            textvariable=self.status_var,
            bg="#1a1d22",
            fg="#9aa5b1",
            anchor="w",
            justify="left",
            wraplength=650,
        )
        self.status_label.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 0))

        buttons = tk.Frame(outer, bg="#1a1d22")
        buttons.grid(row=6, column=0, columnspan=3, sticky="e", pady=(18, 0))
        tk.Button(
            buttons,
            text="Cancel",
            bg="#30343b",
            fg="#e6e6e6",
            relief="flat",
            command=self._close,
        ).pack(side="right", padx=(8, 0))
        self.convert_btn = tk.Button(
            buttons,
            text="Convert",
            bg="#2c5e8a",
            fg="white",
            activebackground="#3a78ad",
            relief="flat",
            command=self._convert,
        )
        self.convert_btn.pack(side="right")

    def _activate_modal(self) -> None:
        self.lift(self._parent_window)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.root_entry.focus_set()

    def _close(self) -> None:
        try:
            if self.grab_current() is self:
                self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _browse_lomm_root(self) -> None:
        initial = self.lomm_root_var.get().strip()
        chosen = filedialog.askdirectory(
            title="Choose Legends of Might and Magic install",
            initialdir=initial if initial and os.path.isdir(initial) else None,
            parent=self,
        )
        if chosen:
            self.lomm_root_var.set(chosen)
            self._load_levels()

    def _load_levels(self) -> None:
        lomm_root = self.lomm_root_var.get().strip()
        if not lomm_root:
            messagebox.showerror(
                "LoMM install required",
                "Choose the Legends of Might and Magic install folder.",
                parent=self,
            )
            return
        try:
            levels = service.list_lomm_levels(lomm_root)
        except Exception as exc:
            messagebox.showerror("Cannot load LoMM levels", str(exc), parent=self)
            return
        if not levels:
            messagebox.showerror(
                "No LoMM levels",
                "No v66 DAT levels were found in LoMM WORLDS.REZ.",
                parent=self,
            )
            return
        self._levels = levels
        names = [level.display_name for level in levels]
        self.level_combo["values"] = names
        self.level_var.set(names[0])
        self._suggest_converted_name(names[0])
        self.status_var.set(f"Loaded {len(levels)} level(s) from LoMM WORLDS.REZ.")

    def _on_level_selected(self, _evt=None) -> None:
        self._suggest_converted_name(self.level_var.get())

    def _suggest_converted_name(self, level_name: str) -> None:
        current = self.converted_name_var.get().strip()
        if current and current != self._last_suggested_name:
            return
        suggested = default_converted_name(level_name)
        self._last_suggested_name = suggested
        self.converted_name_var.set(suggested)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        combo_state = "disabled" if busy else "readonly"
        for widget in (
            self.root_entry,
            self.browse_btn,
            self.load_btn,
            self.name_entry,
            self.convert_btn,
        ):
            widget.configure(state=state)
        self.level_combo.configure(state=combo_state)
        self.configure(cursor="watch" if busy else "")
        self.update_idletasks()

    def _convert(self) -> None:
        lomm_root = self.lomm_root_var.get().strip()
        level_name = self.level_var.get().strip()
        converted_name = self.converted_name_var.get().strip()
        if not lomm_root:
            messagebox.showerror("LoMM install required", "Choose a LoMM install folder.", parent=self)
            return
        if not level_name:
            messagebox.showerror("LoMM level required", "Choose a LoMM level.", parent=self)
            return
        if not converted_name:
            messagebox.showerror("New level required", "Enter a new MM9 level name.", parent=self)
            return
        if not messagebox.askyesno(
            "Convert LoMM level?",
            "This will back up and replace MM9 data/WORLDS.REZ.\n\n"
            f"LoMM level: {level_name}\n"
            f"New MM9 level: {converted_name}",
            parent=self,
        ):
            return

        request = service.ConvertLevelRequest(
            mm9_root=self.mm9_root,
            lomm_root=lomm_root,
            level_to_convert=level_name,
            converted_level_name=converted_name,
            catalog_json=self.catalog_json,
        )
        self.status_var.set("Converting level...")
        self._set_busy(True)
        try:
            result = service.convert_and_insert_level(
                request,
                backup_root=self.backup_root,
            )
        except Exception as exc:
            self.status_var.set("")
            messagebox.showerror("Conversion failed", str(exc), parent=self)
            return
        finally:
            self._set_busy(False)

        self.result = result
        self.status_var.set(f"Added {result.added_virtual_path}.")
        messagebox.showinfo(
            "Conversion complete",
            format_success_message(result),
            parent=self,
        )
        self._close()
        if self.on_success is not None:
            self.on_success(result, lomm_root)

    @classmethod
    def open(
        cls,
        parent: tk.Misc,
        mm9_root: str,
        backup_root: Optional[str] = None,
        catalog_json: Optional[str] = None,
        initial_lomm_root: str = "",
        on_success: Optional[Callable[[service.InsertConvertedLevelResult, str], None]] = None,
    ) -> "LommConversionDialog":
        return cls(
            parent,
            mm9_root=mm9_root,
            backup_root=backup_root,
            catalog_json=catalog_json,
            initial_lomm_root=initial_lomm_root,
            on_success=on_success,
        )
