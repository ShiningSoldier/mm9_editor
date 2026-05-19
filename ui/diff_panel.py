"""
diff_panel.py
=============

Modal "Save preview" dialog — the explicit RUDE-workflow stop. Shows what
each .DAT write will do (per-level op summary) and what the staged RUDE
folder would look like, then lets the user commit (or cancel).
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, List, Optional

from core import project as P


class SaveDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, project: P.Project, plan: P.SavePlan,
                 on_committed: Callable[[List[str]], None],
                 cfg: Optional[Any] = None):
        super().__init__(parent)
        self.title("Save preview")
        self.configure(bg="#1a1d22")
        self.geometry("780x560")
        self.project = project
        self.plan    = plan
        self.on_committed = on_committed
        self.cfg     = cfg   # optional config object; provides archive_path()

        # Header
        tk.Label(self, text="About to write the following files:",
                 bg="#1a1d22", fg="#dddddd",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 4))

        # Body — split into DAT side and RUDE side
        body = tk.Frame(self, bg="#1a1d22")
        body.pack(fill="both", expand=True, padx=12, pady=4)

        # Level/archive panel
        dat_frame = tk.LabelFrame(body, text="Level/archive writes", bg="#1a1d22",
                                  fg="#cccccc", font=("Segoe UI", 9, "bold"))
        dat_frame.pack(fill="both", expand=True, side="top", pady=(0, 8))
        dat_text = tk.Text(dat_frame, bg="#0e1116", fg="#e6e6e6",
                           font=("Consolas", 9), relief="flat",
                           height=12, wrap="none")
        dat_text.pack(fill="both", expand=True, padx=4, pady=4)
        if not plan.dats:
            dat_text.insert("end", "(no DAT changes)\n")
        for d in plan.dats:
            dat_text.insert("end",
                f"=== {self._source_label(d)}  →  {d.output_path} ===\n")
            dat_text.insert("end",
                f"  objects after save: {d.stats()['objects_after']}\n")
            if d.stats().get("door_clones", 0):
                dat_text.insert("end",
                    f"  physical door clones: {d.stats()['door_clones']}\n")
            if d.stats().get("prefab_imports", 0):
                dat_text.insert("end",
                    f"  prefab BSP imports: {d.stats()['prefab_imports']} "
                    f"({d.stats()['prefab_bsp_models']} BSP model(s))\n")
            for line in d.ops_summary:
                dat_text.insert("end", f"  {line}\n")
            for warning in d.validation_warnings:
                dat_text.insert("end", f"  [warn] {warning}\n")
            dat_text.insert("end", "\n")
        archive_only = [
            p for p in plan.archive_patches
            if p.kind != "level"
        ]
        for p in archive_only:
            dat_text.insert("end",
                f"=== {os.path.basename(p.source_archive)}  →  {p.output_archive} ===\n")
            for entry in p.entries:
                dat_text.insert("end", f"  patch {entry}\n")
            dat_text.insert("end", "\n")
        dat_text.config(state="disabled")

        # RUDE panel — explicit workflow: must opt in, choose target dir
        rude_frame = tk.LabelFrame(body, text="RUDE registrations", bg="#1a1d22",
                                   fg="#cccccc", font=("Segoe UI", 9, "bold"))
        rude_frame.pack(fill="x", side="top", pady=(0, 8))
        rude_text = tk.Text(rude_frame, bg="#0e1116", fg="#e6e6e6",
                            font=("Consolas", 9), relief="flat",
                            height=6, wrap="none")
        rude_text.pack(fill="x", padx=4, pady=4)
        if not plan.rude_entries:
            rude_text.insert("end", "(no fresh NPCs to register)\n")
        for r in plan.rude_entries:
            rude_text.insert("end",
                f"  NPCNbr {r.npc_nbr:>4}  '{r.name}'   blurb: {r.blurb!r}\n")
            for p, resp in r.lines:
                rude_text.insert("end", f"      {p!r} → {resp!r}\n")
        rude_text.config(state="disabled")

        # RUDE controls. Fresh-NPC dialogue is written into the patched
        # output data/RUDE.REZ archive when present.
        ctl = tk.Frame(rude_frame, bg="#1a1d22")
        ctl.pack(fill="x", padx=4, pady=(0, 4))
        self.rude_archive_patch = plan.rude_archive_patch()
        archive_mode = self.rude_archive_patch is not None
        self.write_rude = tk.IntVar(value=1 if archive_mode else 0)
        cb_text = "Write RUDE entries to output data/RUDE.REZ"
        cb = tk.Checkbutton(ctl, text=cb_text,
                            variable=self.write_rude, bg="#1a1d22", fg="#ccc",
                            selectcolor="#23272d",
                            activebackground="#1a1d22")
        cb.pack(side="left")

        self.rude_dir_var = tk.StringVar(
            value=self.rude_archive_patch.output_archive if archive_mode else "")
        self.rude_dir_entry = tk.Entry(ctl, textvariable=self.rude_dir_var,
                                       bg="#23272d", fg="#e6e6e6",
                                       insertbackground="#fff", relief="flat",
                                       state="disabled")
        self.rude_dir_entry.pack(side="left", fill="x", expand=True, padx=(8, 4))
        self.browse_btn = tk.Button(ctl, text="Browse…",
                                    bg="#23272d", fg="#cccccc", relief="flat",
                                    state="disabled")
        self.browse_btn.pack(side="left")

        # Bottom buttons
        bottom = tk.Frame(self, bg="#1a1d22")
        bottom.pack(fill="x", padx=12, pady=12)
        tk.Button(bottom, text="Cancel", bg="#23272d", fg="white",
                  relief="flat", command=self.destroy).pack(side="right")
        tk.Button(bottom, text="Commit", bg="#2c5e8a", fg="white",
                  activebackground="#3a78ad", relief="flat",
                  command=self._commit).pack(side="right", padx=(0, 8))

        cb.config(state="disabled")

    # ---------- handlers ----------

    def _source_label(self, d: P.DatWrite) -> str:
        L = d.level_edit
        if L and L.source_kind == P.SOURCE_REZ and L.rez_path and L.rez_vpath:
            return f"{os.path.basename(L.rez_path)}::{L.rez_vpath}"
        return os.path.basename(d.source_path)

    def _commit(self) -> None:
        log: List[str] = []
        try:
            log.extend(self.project.execute(self.plan))
        except Exception as e:
            log.append(f"[error] {e}")
            messagebox.showerror("Save failed", str(e))
        self.on_committed(log)
        self.destroy()
