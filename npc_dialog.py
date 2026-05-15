"""
npc_dialog.py
=============

Modal dialog shown whenever the user places an NPC class (any WorldObject
whose template has an NPCNbr property).  Asks whether to keep the cloned
NPC's existing dialogue identity or register it as a genuinely fresh NPC
with a new NPCNbr and RUDE entries.

Usage (from mm9_editor.py)::

    from npc_dialog import FreshNpcDialog

    result = FreshNpcDialog.ask(self.root, self.project.next_npc_nbr,
                                default_name="Commoner")

Return value
------------
``None``
    The user cancelled (no placement should happen).

``{"mode": "inherit"}``
    Keep the cloned object's existing NPCNbr and ScriptName unchanged.
    No RUDE registration is needed.

``{"mode": "fresh", "npc_nbr": 440, "name": "Tom the Peasant",
    "blurb": "Hail!", "lines": [("Hello.", "Well met!"), ...],
    "force": False}``
    Assign a new NPCNbr.  The caller is responsible for:
      * encoding npc_nbr as float-bits in the NPCNbr property override
      * attaching this dict (minus "mode") as AddOp.rude
      * advancing project.next_npc_nbr
    The "lines" list is (player_prompt, npc_response) tuples.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any, Dict, List, Optional, Tuple


class FreshNpcDialog(tk.Toplevel):
    """Modal "configure NPC" dialog.

    Do not instantiate directly — use the :py:meth:`ask` class method which
    blocks until the dialog is closed and returns the result dict.
    """

    def __init__(self, parent: tk.Misc, suggested_nbr: int,
                 default_name: str = "") -> None:
        super().__init__(parent)
        self.title("Configure NPC placement")
        self.configure(bg="#1a1d22")
        self.resizable(True, False)
        self.result: Optional[Dict[str, Any]] = None

        self._build(suggested_nbr, default_name)

        # Centre over parent
        self.update_idletasks()
        px = parent.winfo_rootx() + parent.winfo_width()  // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2
        self.geometry(f"+{px - self.winfo_width() // 2}+{py - self.winfo_height() // 2}")

        # Make modal
        self.transient(parent)
        self.grab_set()
        self.focus_force()

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build(self, suggested_nbr: int, default_name: str) -> None:
        # ---- Mode selection ----
        mode_outer = tk.Frame(self, bg="#1a1d22")
        mode_outer.pack(fill="x", padx=12, pady=(12, 4))

        tk.Label(mode_outer, text="Dialogue mode",
                 bg="#1a1d22", fg="#dddddd",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")

        self.mode_var = tk.StringVar(value="inherit")

        tk.Radiobutton(
            mode_outer,
            text="Inherit existing dialogue  (keep cloned NPCNbr unchanged)",
            variable=self.mode_var, value="inherit",
            bg="#1a1d22", fg="#cccccc",
            selectcolor="#23272d", activebackground="#1a1d22",
            command=self._on_mode_change,
        ).pack(anchor="w", pady=(6, 2))

        tk.Radiobutton(
            mode_outer,
            text="Register as fresh NPC  (assign new NPCNbr + write RUDE entries on save)",
            variable=self.mode_var, value="fresh",
            bg="#1a1d22", fg="#cccccc",
            selectcolor="#23272d", activebackground="#1a1d22",
            command=self._on_mode_change,
        ).pack(anchor="w", pady=(2, 4))

        # ---- Fresh-NPC detail form ----
        self.fresh_frame = tk.LabelFrame(
            self, text="Fresh NPC details",
            bg="#1a1d22", fg="#444444",
            font=("Segoe UI", 9),
        )
        self.fresh_frame.pack(fill="x", padx=12, pady=(0, 8), ipadx=4, ipady=4)

        # NPCNbr
        row0 = tk.Frame(self.fresh_frame, bg="#1a1d22")
        row0.pack(fill="x", padx=8, pady=(8, 4))
        tk.Label(row0, text="Assign NPCNbr:", bg="#1a1d22", fg="#9bb",
                 width=16, anchor="w", font=("Consolas", 9)).pack(side="left")
        self.nbr_var = tk.StringVar(value=str(suggested_nbr))
        tk.Entry(row0, textvariable=self.nbr_var,
                 bg="#23272d", fg="#e6e6e6", insertbackground="#fff",
                 relief="flat", width=10, font=("Consolas", 9),
                 state="disabled").pack(side="left")
        tk.Label(row0, text="  (edit if already taken)",
                 bg="#1a1d22", fg="#555", font=("Segoe UI", 8)).pack(side="left")

        # Display name
        row1 = tk.Frame(self.fresh_frame, bg="#1a1d22")
        row1.pack(fill="x", padx=8, pady=3)
        tk.Label(row1, text="Name (RUDE):", bg="#1a1d22", fg="#9bb",
                 width=16, anchor="w", font=("Consolas", 9)).pack(side="left")
        self.name_var = tk.StringVar(value=default_name)
        tk.Entry(row1, textvariable=self.name_var,
                 bg="#23272d", fg="#e6e6e6", insertbackground="#fff",
                 relief="flat", font=("Consolas", 9),
                 state="disabled").pack(side="left", fill="x", expand=True)

        # Greeting blurb
        row2 = tk.Frame(self.fresh_frame, bg="#1a1d22")
        row2.pack(fill="x", padx=8, pady=3)
        tk.Label(row2, text="Greeting:", bg="#1a1d22", fg="#9bb",
                 width=16, anchor="w", font=("Consolas", 9)).pack(side="left")
        self.blurb_var = tk.StringVar(value="Hail, traveler!")
        tk.Entry(row2, textvariable=self.blurb_var,
                 bg="#23272d", fg="#e6e6e6", insertbackground="#fff",
                 relief="flat", font=("Consolas", 9),
                 state="disabled").pack(side="left", fill="x", expand=True)

        # Dialogue lines
        tk.Label(
            self.fresh_frame,
            text="Dialogue lines  (one per row,  format:  player text :: npc response)",
            bg="#1a1d22", fg="#555", font=("Segoe UI", 8),
        ).pack(anchor="w", padx=8, pady=(8, 2))

        self.lines_text = tk.Text(
            self.fresh_frame, height=5,
            bg="#23272d", fg="#e6e6e6", insertbackground="#fff",
            relief="flat", font=("Consolas", 9), wrap="none",
            state="disabled",
        )
        self.lines_text.pack(fill="x", padx=8, pady=(0, 8))

        # Pre-populate with sample lines (inserted while temporarily enabled)
        self.lines_text.config(state="normal")
        self.lines_text.insert("end", "Hello.::Well met!\nGoodbye.::Safe travels.")
        self.lines_text.config(state="disabled")

        # ---- Buttons ----
        btns = tk.Frame(self, bg="#1a1d22")
        btns.pack(fill="x", padx=12, pady=(4, 12))
        tk.Button(btns, text="Cancel", bg="#23272d", fg="#cccccc",
                  activebackground="#33373d", relief="flat",
                  command=self._cancel).pack(side="right")
        tk.Button(btns, text="OK", bg="#2c5e8a", fg="white",
                  activebackground="#3a78ad", relief="flat",
                  command=self._ok).pack(side="right", padx=(0, 8))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _widgets_in(self, frame: tk.Widget):
        """Yield every descendant widget of *frame*."""
        for child in frame.winfo_children():
            yield child
            yield from self._widgets_in(child)

    def _set_fresh_enabled(self, enabled: bool) -> None:
        """Enable or disable every interactive widget inside fresh_frame."""
        state = "normal" if enabled else "disabled"
        self.fresh_frame.config(fg="#cccccc" if enabled else "#444444")
        for w in self._widgets_in(self.fresh_frame):
            try:
                w.config(state=state)
            except tk.TclError:
                pass  # Label, Frame, etc. don't have state

    def _on_mode_change(self) -> None:
        self._set_fresh_enabled(self.mode_var.get() == "fresh")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _ok(self) -> None:
        if self.mode_var.get() == "inherit":
            self.result = {"mode": "inherit"}
            self.destroy()
            return

        # --- Validate fresh form ---
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("Missing field",
                                 "NPC display name is required.", parent=self)
            return
        try:
            nbr = int(self.nbr_var.get().strip())
            if nbr <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid NPCNbr",
                                 "NPCNbr must be a positive integer.", parent=self)
            return

        blurb = self.blurb_var.get().strip() or "Hail, traveler!"

        lines: List[Tuple[str, str]] = []
        raw_text = self.lines_text.get("1.0", "end")
        for raw_line in raw_text.splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            if "::" in raw_line:
                prompt, response = raw_line.split("::", 1)
                prompt   = prompt.strip()
                response = response.strip()
                if prompt and response:
                    lines.append((prompt, response))
            # Lines without "::" are silently skipped (e.g. the blank sample row)

        self.result = {
            "mode":    "fresh",
            "npc_nbr": nbr,
            "name":    name,
            "blurb":   blurb,
            "lines":   lines,
            "force":   False,
        }
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def ask(cls, parent: tk.Misc, suggested_nbr: int,
            default_name: str = "") -> Optional[Dict[str, Any]]:
        """Show the dialog modally and return the user's choice.

        Parameters
        ----------
        parent:
            The Tk window to make this dialog transient to.
        suggested_nbr:
            The next available NPCNbr (from ``project.next_npc_nbr``).
        default_name:
            Pre-filled display name — typically derived from the class name.

        Returns
        -------
        ``None``
            User pressed Cancel.
        ``{"mode": "inherit"}``
            Keep the cloned object's NPCNbr.
        ``{"mode": "fresh", "npc_nbr": int, "name": str, "blurb": str,
           "lines": List[Tuple[str, str]], "force": bool}``
            Register a fresh NPC.  Caller should strip "mode" before
            storing in ``AddOp.rude``.
        """
        dlg = cls(parent, suggested_nbr, default_name)
        parent.wait_window(dlg)
        return dlg.result
