"""
properties_panel.py
===================

Inspector for the currently selected WorldObject.
"""

from __future__ import annotations

import math
import struct
import tkinter as tk
from typing import Any, Callable, Dict, List, Optional, Tuple

import _path_setup  # noqa: F401
import mm9_patch as patcher


PRIMARY_FIELDS = [
    "Name", "Pos", "Rotation", "ScriptName", "ScriptParams",
    "NPCNbr", "Filename", "Skin",
    "MoveToFloor", "Solid", "Gravity", "Visible", "Shadow",
    "WanderON", "ShouldRepopulate",
]


def _value_to_str(code: int, value: Any) -> str:
    if code == 0:    return "" if value is None else str(value)
    if code in (1,2):
        return ", ".join(f"{x:.4g}" for x in value)
    if code == 3:    return f"{float(value):.6g}"
    if code == 5:    return "1" if value else "0"
    if code in (4,6):
        try:
            f = struct.unpack("<f", struct.pack("<I", int(value)))[0]
            if 0.0 < abs(f) < 1e7 and f == int(f):
                return f"{int(value)}    (= float {f:.0f})"
            elif abs(f) < 1e7:
                return f"{int(value)}    (= float {f:g})"
        except Exception:
            pass
        return str(int(value))
    if code == 7:
        return ", ".join(f"{x:.4g}" for x in value)
    return repr(value)


def _str_to_value(code: int, text: str) -> Any:
    text = text.strip()
    if code == 0: return text
    if code in (1, 2):
        parts = [p for p in text.replace(",", " ").split() if p]
        if len(parts) != 3: raise ValueError("Vector expects 3 numbers")
        return [float(p) for p in parts]
    if code == 3: return float(text)
    if code == 5:
        return 0 if text.lower() in ("0", "false", "no", "off", "") else 1
    if code in (4, 6):
        if text.startswith("!float_bits"):
            f = float(text.split(None, 1)[1])
            return struct.unpack("<I", struct.pack("<f", f))[0]
        if "(=" in text or "(= " in text:
            text = text.split("(")[0].strip()
        return int(text)
    if code == 7:
        parts = [p for p in text.replace(",", " ").split() if p]
        if len(parts) != 4: raise ValueError("Rotation expects 4 numbers")
        return [float(p) for p in parts]
    raise ValueError(f"unknown code {code}")


class PropertiesPanel(tk.Frame):
    def __init__(self, parent: tk.Misc,
                 on_edit:         Callable[[str, Any], None],
                 on_delete:       Callable[[], None],
                 on_save_preset:  Optional[Callable[[], None]] = None):
        super().__init__(parent, bg="#1a1d22")
        self.on_edit         = on_edit
        self.on_delete       = on_delete
        self.on_save_preset  = on_save_preset
        self.current_obj: Optional[patcher.WorldObject] = None
        self.entries: List[Tuple[str, int, tk.StringVar]] = []
        self.transform_vars: Dict[str, tk.Variable] = {}

        tk.Label(self, text="Properties", bg="#1a1d22", fg="#dddddd",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=8, pady=(8, 2))

        self.header = tk.Label(self, text="(nothing selected)", bg="#1a1d22",
                               fg="#aaa", anchor="w", justify="left",
                               font=("Segoe UI", 9))
        self.header.pack(fill="x", padx=8)

        self.body = tk.Frame(self, bg="#1a1d22")
        self.body.pack(fill="both", expand=True, padx=8, pady=4)

        btns = tk.Frame(self, bg="#1a1d22")
        btns.pack(fill="x", padx=8, pady=8)

        self.del_btn = tk.Button(btns, text="Delete",
                                 bg="#883232", fg="white",
                                 activebackground="#aa4040",
                                 relief="flat", state="disabled",
                                 command=self._do_delete)
        self.del_btn.pack(side="right")

        self.preset_btn = tk.Button(btns, text="Save as Preset...",
                                    bg="#23272d", fg="#cccccc",
                                    activebackground="#33373d",
                                    relief="flat", state="disabled",
                                    command=self._do_save_preset)
        self.preset_btn.pack(side="right", padx=(0, 6))

    def show(self, obj: Optional[patcher.WorldObject]) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        self.entries.clear()
        self.transform_vars.clear()
        self.current_obj = obj

        if obj is None:
            self.header.config(text="(nothing selected)")
            self.del_btn.config(state="disabled")
            self.preset_btn.config(state="disabled")
            return

        display_name = str(obj.get("Name") or "").strip()
        if display_name and display_name != obj.type_str:
            header = f"{display_name}\n{obj.type_str}\n({len(obj.props)} fields)"
        else:
            header = f"{obj.type_str}\n({len(obj.props)} fields)"
        self.header.config(text=header)
        self.del_btn.config(state="normal")
        self.preset_btn.config(
            state="normal" if self.on_save_preset else "disabled")

        present = {p.name: p for p in obj.props}
        transformed_fields = self._draw_transform_controls(present)
        ordered = [n for n in PRIMARY_FIELDS if n in present] + \
                  [n for n in present if n not in PRIMARY_FIELDS]

        for name in ordered:
            if name in transformed_fields:
                continue
            p = present[name]
            row = tk.Frame(self.body, bg="#1a1d22")
            row.pack(fill="x", pady=1)

            tk.Label(row, text=name, bg="#1a1d22", fg="#9bb",
                     width=16, anchor="w",
                     font=("Consolas", 9)).pack(side="left")

            sv = tk.StringVar(value=_value_to_str(p.code, p.value))
            ent = tk.Entry(row, textvariable=sv, bg="#23272d", fg="#e6e6e6",
                           insertbackground="#fff", relief="flat",
                           font=("Consolas", 9))
            ent.pack(side="left", fill="x", expand=True)
            ent.bind("<Return>", lambda e, n=name: self._commit(n))
            ent.bind("<FocusOut>", lambda e, n=name: self._commit(n))
            self.entries.append((name, p.code, sv))

    def _commit(self, name: str) -> None:
        if self.current_obj is None:
            return
        for n, code, sv in self.entries:
            if n != name:
                continue
            try:
                new_val = _str_to_value(code, sv.get())
            except Exception:
                p = next((p for p in self.current_obj.props if p.name == name), None)
                if p is not None:
                    sv.set(_value_to_str(p.code, p.value))
                return
            self.on_edit(name, new_val)
            p = next((p for p in self.current_obj.props if p.name == name), None)
            if p is not None:
                sv.set(_value_to_str(p.code, p.value))
            self._sync_transform_vars()
            break

    def _draw_transform_controls(self, present: Dict[str, patcher.Property]) -> set:
        handled = set()
        if not any(name in present for name in ("Pos", "Rotation", "MoveToFloor")):
            return handled

        section = tk.Frame(self.body, bg="#15171b", bd=1, relief="solid")
        section.pack(fill="x", pady=(0, 8))

        tk.Label(section, text="Transform", bg="#15171b", fg="#dddddd",
                 anchor="w", font=("Segoe UI", 9, "bold")).pack(
                     fill="x", padx=6, pady=(5, 3))

        if "Pos" in present:
            handled.add("Pos")
            pos = self._safe_vec3(present["Pos"].value)
            row = tk.Frame(section, bg="#15171b")
            row.pack(fill="x", padx=6, pady=2)
            for axis, value in zip(("X", "Y", "Z"), pos):
                tk.Label(row, text=axis, bg="#15171b", fg="#9bb",
                         font=("Consolas", 9)).pack(side="left")
                sv = tk.StringVar(value=self._format_number(value))
                self.transform_vars[f"pos_{axis.lower()}"] = sv
                ent = tk.Entry(row, textvariable=sv, bg="#23272d", fg="#e6e6e6",
                               insertbackground="#fff", relief="flat", width=9,
                               font=("Consolas", 9))
                ent.pack(side="left", padx=(2, 6), fill="x", expand=True)
                ent.bind("<Return>", lambda e: self._commit_pos())
                ent.bind("<FocusOut>", lambda e: self._commit_pos())

        if "Rotation" in present:
            handled.add("Rotation")
            row = tk.Frame(section, bg="#15171b")
            row.pack(fill="x", padx=6, pady=2)
            tk.Label(row, text="Yaw deg", bg="#15171b", fg="#9bb",
                     width=8, anchor="w",
                     font=("Consolas", 9)).pack(side="left")
            sv = tk.StringVar(value=self._format_number(
                self._yaw_degrees(present["Rotation"].value)))
            self.transform_vars["yaw"] = sv
            ent = tk.Entry(row, textvariable=sv, bg="#23272d", fg="#e6e6e6",
                           insertbackground="#fff", relief="flat", width=10,
                           font=("Consolas", 9))
            ent.pack(side="left", fill="x", expand=True, padx=(0, 6))
            ent.bind("<Return>", lambda e: self._commit_yaw())
            ent.bind("<FocusOut>", lambda e: self._commit_yaw())
            for label, delta in (("-15", -15.0), ("+15", 15.0)):
                tk.Button(row, text=label, bg="#23272d", fg="#dddddd",
                          activebackground="#33373d", relief="flat", width=4,
                          command=lambda d=delta: self._nudge_yaw(d)).pack(
                              side="left", padx=(0, 3))

        if "MoveToFloor" in present:
            handled.add("MoveToFloor")
            row = tk.Frame(section, bg="#15171b")
            row.pack(fill="x", padx=6, pady=(2, 6))
            iv = tk.IntVar(value=1 if present["MoveToFloor"].value else 0)
            self.transform_vars["move_to_floor"] = iv
            tk.Checkbutton(row, text="Move to floor", variable=iv,
                           bg="#15171b", fg="#dddddd", selectcolor="#23272d",
                           activebackground="#15171b", activeforeground="#ffffff",
                           command=self._commit_move_to_floor).pack(anchor="w")

        return handled

    def _prop(self, name: str) -> Optional[patcher.Property]:
        if self.current_obj is None:
            return None
        return next((p for p in self.current_obj.props if p.name == name), None)

    @staticmethod
    def _safe_vec3(value: Any) -> List[float]:
        try:
            vals = list(value)
        except TypeError:
            vals = []
        vals = (vals + [0.0, 0.0, 0.0])[:3]
        return [float(v) for v in vals]

    @staticmethod
    def _safe_rotation(value: Any) -> List[float]:
        try:
            vals = list(value)
        except TypeError:
            vals = []
        vals = (vals + [0.0, 0.0, 0.0, 1.0])[:4]
        return [float(v) for v in vals]

    @staticmethod
    def _format_number(value: float) -> str:
        return f"{float(value):.6g}"

    def _yaw_degrees(self, rotation: Any) -> float:
        rot = self._safe_rotation(rotation)
        return math.degrees(rot[1])

    def _commit_pos(self) -> None:
        if self.current_obj is None:
            return
        try:
            pos = [
                float(self.transform_vars["pos_x"].get()),
                float(self.transform_vars["pos_y"].get()),
                float(self.transform_vars["pos_z"].get()),
            ]
        except Exception:
            self._sync_transform_vars()
            return
        self.on_edit("Pos", pos)
        self._sync_transform_vars()

    def _commit_yaw(self) -> None:
        if self.current_obj is None:
            return
        rot_prop = self._prop("Rotation")
        if rot_prop is None:
            return
        try:
            yaw = float(self.transform_vars["yaw"].get())
        except Exception:
            self._sync_transform_vars()
            return
        rot = self._safe_rotation(rot_prop.value)
        rot[1] = math.radians(yaw)
        self.on_edit("Rotation", rot)
        self._sync_transform_vars()

    def _nudge_yaw(self, delta_degrees: float) -> None:
        if self.current_obj is None:
            return
        yaw_var = self.transform_vars.get("yaw")
        if yaw_var is None:
            return
        try:
            yaw = float(yaw_var.get())
        except Exception:
            rot_prop = self._prop("Rotation")
            yaw = self._yaw_degrees(rot_prop.value) if rot_prop else 0.0
        yaw_var.set(self._format_number(yaw + delta_degrees))
        self._commit_yaw()

    def _commit_move_to_floor(self) -> None:
        if self.current_obj is None:
            return
        move_var = self.transform_vars.get("move_to_floor")
        if move_var is None:
            return
        self.on_edit("MoveToFloor", 1 if int(move_var.get()) else 0)
        self._sync_transform_vars()

    def _sync_transform_vars(self) -> None:
        if self.current_obj is None:
            return
        pos_prop = self._prop("Pos")
        if pos_prop is not None and "pos_x" in self.transform_vars:
            x, y, z = self._safe_vec3(pos_prop.value)
            self.transform_vars["pos_x"].set(self._format_number(x))
            self.transform_vars["pos_y"].set(self._format_number(y))
            self.transform_vars["pos_z"].set(self._format_number(z))

        rot_prop = self._prop("Rotation")
        if rot_prop is not None and "yaw" in self.transform_vars:
            self.transform_vars["yaw"].set(self._format_number(
                self._yaw_degrees(rot_prop.value)))

        move_prop = self._prop("MoveToFloor")
        if move_prop is not None and "move_to_floor" in self.transform_vars:
            self.transform_vars["move_to_floor"].set(
                1 if move_prop.value else 0)

    def _do_delete(self) -> None:
        if self.current_obj is not None:
            self.on_delete()

    def _do_save_preset(self) -> None:
        if self.current_obj is None or self.on_save_preset is None:
            return
        self.on_save_preset()

    def current_overrides_snapshot(self) -> dict:
        """Return {name: raw_value} for the current object, minus placement fields."""
        if self.current_obj is None:
            return {}
        skip = {"Name", "Pos", "Rotation"}
        result = {}
        for p in self.current_obj.props:
            if p.name not in skip:
                result[p.name] = p.value
        return result
