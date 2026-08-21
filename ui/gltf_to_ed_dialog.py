"""Desktop workflow for the maintained glTF/GLB -> DEDit ED converter."""

from __future__ import annotations

import math
import os
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional, Sequence, Tuple

from features.dat_editing import gltf_brushes
from features.dat_editing import gltf_ed_assembly
from features.dat_editing import gltf_to_ed_service
from features.dat_editing import gltf_to_ed_validation


OUTPUT_MODE_LABELS = {
    "Prefab (insert into an existing DEDit world)": gltf_ed_assembly.PREFAB,
    "Full world (minimal standalone scaffold)": gltf_ed_assembly.FULL_WORLD,
}
GEOMETRY_POLICY_LABELS = {
    "Strict convex solids (recommended)": gltf_brushes.STRICT_CONVEX,
    "Triangle slabs (explicit approximation)": gltf_brushes.TRIANGLE_SLAB,
}
COORDINATE_LABELS = {
    "Editor display coordinates (recommended)": gltf_to_ed_service.EDITOR_DISPLAY,
    "Raw DEDit coordinates (identity)": gltf_to_ed_service.RAW_DEDIT,
}
UV_POLICY_LABELS = {
    "Reject missing/degenerate UVs (recommended)": None,
    "Use world-aligned fallback projection": "world_aligned",
}


@dataclass(frozen=True)
class GltfToEdUiRequest:
    source_path: str
    output_path: str
    options: gltf_to_ed_service.GltfToEdConversionOptions


@dataclass(frozen=True)
class GltfToEdUiResult:
    conversion: gltf_to_ed_service.GltfToEdConversionReport
    validation: Optional[gltf_to_ed_validation.GltfToEdValidationReport] = None


def suggest_output_path(source_path: str, output_mode: str) -> str:
    source = os.path.abspath(os.fspath(source_path)) if source_path else ""
    if not source:
        return ""
    stem = os.path.splitext(source)[0]
    suffix = "_world.ed" if output_mode == gltf_ed_assembly.FULL_WORLD else ".ed"
    return stem + suffix


def build_conversion_request(
    *,
    source_path: str,
    output_path: str,
    output_mode: str,
    geometry_policy: str,
    coordinate_preset: str,
    unit_scale: str,
    weld_tolerance: str,
    material_map_path: str = "",
    texture_dimensions_path: str = "",
    fallback_texture: str = "",
    fallback_texture_width: str = "128",
    fallback_texture_height: str = "128",
    default_uv_projection: Optional[str] = None,
    slab_thickness: str = "",
    slab_back_texture: str = "",
    slab_side_texture: str = "",
    overwrite: bool = False,
) -> GltfToEdUiRequest:
    """Validate dialog strings and build the typed Phase 7 request."""
    source = os.path.abspath(str(source_path or "").strip())
    output = os.path.abspath(str(output_path or "").strip())
    if not os.path.isfile(source):
        raise ValueError("Choose an existing .gltf or .glb source file.")
    if os.path.splitext(source)[1].lower() not in {".gltf", ".glb"}:
        raise ValueError("Source must use the .gltf or .glb extension.")
    if os.path.splitext(output)[1].lower() != ".ed":
        raise ValueError("Output must use the .ed extension.")
    if os.path.normcase(os.path.realpath(source)) == os.path.normcase(os.path.realpath(output)):
        raise ValueError("Source and output paths must be different.")
    if output_mode not in gltf_ed_assembly.OUTPUT_MODES:
        raise ValueError("Choose prefab or full-world output.")
    if geometry_policy not in {gltf_brushes.STRICT_CONVEX, gltf_brushes.TRIANGLE_SLAB}:
        raise ValueError("Choose a supported geometry policy.")
    if coordinate_preset not in {
        gltf_to_ed_service.EDITOR_DISPLAY,
        gltf_to_ed_service.RAW_DEDIT,
    }:
        raise ValueError("Choose a supported coordinate preset.")

    scale = _finite_float("Unit scale", unit_scale, minimum=0.0, inclusive=False)
    tolerance = _finite_float("Weld tolerance", weld_tolerance, minimum=0.0)
    fallback_size = _optional_dimensions(
        fallback_texture_width,
        fallback_texture_height,
    )
    material_map = _optional_json_path("Material map", material_map_path)
    dimensions_map = _optional_json_path(
        "Texture dimensions map", texture_dimensions_path
    )
    if default_uv_projection not in {None, "world_aligned"}:
        raise ValueError("Choose a supported missing-UV policy.")

    thickness: Optional[float] = None
    back_texture = str(slab_back_texture or "").strip() or None
    side_texture = str(slab_side_texture or "").strip() or None
    if geometry_policy == gltf_brushes.TRIANGLE_SLAB:
        thickness = _finite_float(
            "Slab thickness", slab_thickness, minimum=0.0, inclusive=False
        )
        if thickness <= tolerance:
            raise ValueError("Slab thickness must be greater than weld tolerance.")
        if not back_texture or not side_texture:
            raise ValueError("Triangle slabs require back and side DTX texture paths.")

    options = gltf_to_ed_service.GltfToEdConversionOptions(
        output_mode=output_mode,
        geometry_policy=geometry_policy,
        coordinate_preset=coordinate_preset,
        unit_scale=scale,
        weld_tolerance=tolerance,
        material_map_path=material_map,
        texture_dimensions_path=dimensions_map,
        fallback_texture=str(fallback_texture or "").strip() or None,
        fallback_texture_size=fallback_size,
        default_uv_projection=default_uv_projection,
        slab_thickness=thickness,
        slab_back_texture=back_texture,
        slab_side_texture=side_texture,
        overwrite=bool(overwrite),
    )
    return GltfToEdUiRequest(source, output, options)


def execute_conversion_request(
    request: GltfToEdUiRequest,
    *,
    converter: Callable[..., gltf_to_ed_service.GltfToEdConversionReport] = (
        gltf_to_ed_service.convert_gltf_to_ed
    ),
    validator: Callable[..., gltf_to_ed_validation.GltfToEdValidationReport] = (
        gltf_to_ed_validation.validate_gltf_to_ed
    ),
) -> GltfToEdUiResult:
    """Convert and run only the lightweight automatic Phase 8 stages."""
    conversion = converter(
        request.source_path,
        request.output_path,
        options=request.options,
    )
    validation = None
    if conversion.status in {"ready_prefab", "ready_full_world"}:
        validation = validator(conversion.json_report_path)
    return GltfToEdUiResult(conversion, validation)


def format_ui_result(result: GltfToEdUiResult) -> str:
    conversion = result.conversion
    lines = [
        f"Conversion: {conversion.status}",
        f"ED: {conversion.output_path or '<not written>'}",
        f"Brushes: {conversion.output.get('brush_count', 0)}",
        f"Surfaces: {conversion.output.get('surface_count', 0)}",
        f"JSON report: {conversion.json_report_path or '<not written>'}",
    ]
    if result.validation is not None:
        validation = result.validation
        lines.extend([
            "",
            f"Validation: {validation.status}",
            f"ED integrity: {validation.stages['ed_integrity'].state}",
            f"ED reader round-trip: {validation.stages['ed_roundtrip'].state}",
            f"DEdit: {validation.stages['dedit'].state}",
            f"Validation manifest: {validation.json_manifest_path}",
        ])
    if conversion.blockers:
        lines.extend(["", "Blockers:"])
        lines.extend(f"- {item.message}" for item in conversion.blockers[:12])
    if conversion.cautions:
        lines.extend(["", "Cautions:"])
        lines.extend(f"- {item.message}" for item in conversion.cautions[:12])
    return "\n".join(lines)


def validate_existing_report(
    report_path: str,
    *,
    record_dedit_pass: bool = False,
    validator: Callable[..., gltf_to_ed_validation.GltfToEdValidationReport] = (
        gltf_to_ed_validation.validate_gltf_to_ed
    ),
) -> gltf_to_ed_validation.GltfToEdValidationReport:
    absolute = os.path.abspath(str(report_path or "").strip())
    if not os.path.isfile(absolute):
        raise ValueError("Choose an existing Phase 7 JSON report.")
    options = gltf_to_ed_validation.GltfToEdValidationOptions(
        dedit_opened=True if record_dedit_pass else None,
        dedit_saved=True if record_dedit_pass else None,
    )
    return validator(absolute, options=options)


class GltfToEdDialog(tk.Toplevel):
    """Modal conversion workspace with safe automatic validation actions."""

    def __init__(self, parent: tk.Misc, *, initial_dir: str = "") -> None:
        super().__init__(parent)
        self._parent_window = parent.winfo_toplevel()
        initial = os.path.abspath(initial_dir) if initial_dir else os.getcwd()
        if not os.path.isdir(initial):
            initial = os.getcwd()
        self._initial_dir = initial
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gltf-to-ed-ui")
        self._future: Optional[Future] = None
        self._future_callback: Optional[Callable[[object], None]] = None
        self._action_widgets: list[tk.Widget] = []
        self.result: Optional[GltfToEdUiResult] = None

        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.report_var = tk.StringVar()
        self.output_mode_label_var = tk.StringVar(value=next(iter(OUTPUT_MODE_LABELS)))
        self.geometry_policy_label_var = tk.StringVar(value=next(iter(GEOMETRY_POLICY_LABELS)))
        self.coordinate_label_var = tk.StringVar(value=next(iter(COORDINATE_LABELS)))
        self.uv_policy_label_var = tk.StringVar(value=next(iter(UV_POLICY_LABELS)))
        self.unit_scale_var = tk.StringVar(value="1")
        self.weld_tolerance_var = tk.StringVar(value="0.01")
        self.material_map_var = tk.StringVar()
        self.texture_dimensions_var = tk.StringVar()
        self.fallback_texture_var = tk.StringVar()
        self.fallback_width_var = tk.StringVar(value="128")
        self.fallback_height_var = tk.StringVar(value="128")
        self.slab_thickness_var = tk.StringVar(value="1")
        self.slab_back_texture_var = tk.StringVar()
        self.slab_side_texture_var = tk.StringVar()
        self.overwrite_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Choose a glTF/GLB source and output ED path.")

        self.title("glTF/GLB to DEDit ED")
        self.configure(bg="#15191f")
        self.geometry("1120x740")
        self.minsize(920, 650)
        self.transient(self._parent_window)
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())
        self.geometry_policy_label_var.trace_add("write", self._sync_slab_fields)
        self.output_mode_label_var.trace_add("write", self._sync_output_suggestion)
        self._sync_slab_fields()
        self._activate_modal()

    def _build_ui(self) -> None:
        outer = tk.Frame(self, bg="#15191f", padx=14, pady=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=3)
        outer.columnconfigure(1, weight=2)
        outer.rowconfigure(1, weight=1)

        tk.Label(
            outer,
            text="glTF/GLB to DEDit ED",
            bg="#15191f",
            fg="#eef2f6",
            font=("Segoe UI", 13, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        form = self._panel(outer, "Conversion")
        form.grid(row=1, column=0, sticky="nsew", padx=(0, 7))
        form.columnconfigure(1, weight=1)
        row = 1
        row = self._path_row(form, row, "Source glTF/GLB", self.source_var, self._browse_source)
        row = self._path_row(form, row, "Output ED", self.output_var, self._browse_output)
        row = self._combo_row(form, row, "Output mode", self.output_mode_label_var, tuple(OUTPUT_MODE_LABELS))
        row = self._combo_row(form, row, "Geometry", self.geometry_policy_label_var, tuple(GEOMETRY_POLICY_LABELS))
        row = self._combo_row(form, row, "Coordinates", self.coordinate_label_var, tuple(COORDINATE_LABELS))
        row = self._combo_row(form, row, "Missing UVs", self.uv_policy_label_var, tuple(UV_POLICY_LABELS))
        row = self._two_value_row(
            form, row, "Scale / weld", self.unit_scale_var, self.weld_tolerance_var,
            "unit scale", "weld tolerance",
        )
        row = self._path_row(form, row, "Material map JSON", self.material_map_var, self._browse_material_map)
        row = self._path_row(
            form, row, "Texture sizes JSON", self.texture_dimensions_var,
            self._browse_texture_dimensions,
        )
        row = self._entry_row(
            form, row, "Fallback DTX", self.fallback_texture_var,
            "Optional DTX path, e.g. TEXTURES\\WORLD\\Stone.dtx",
        )
        row = self._two_value_row(
            form, row, "Fallback size", self.fallback_width_var, self.fallback_height_var,
            "width", "height",
        )
        row = self._entry_row(form, row, "Slab thickness", self.slab_thickness_var)
        self._slab_thickness_entry = self._last_entry
        row = self._entry_row(form, row, "Slab back DTX", self.slab_back_texture_var)
        self._slab_back_entry = self._last_entry
        row = self._entry_row(form, row, "Slab side DTX", self.slab_side_texture_var)
        self._slab_side_entry = self._last_entry
        tk.Checkbutton(
            form,
            text="Replace an existing ED and its reports transactionally",
            variable=self.overwrite_var,
            bg="#1b2027",
            fg="#dce3ea",
            activebackground="#1b2027",
            activeforeground="#ffffff",
            selectcolor="#252b34",
        ).grid(row=row, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 5))

        right = tk.Frame(outer, bg="#15191f")
        right.grid(row=1, column=1, sticky="nsew", padx=(7, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        summary_panel = self._panel(right, "Result and validation")
        summary_panel.grid(row=0, column=0, sticky="nsew")
        summary_panel.columnconfigure(0, weight=1)
        summary_panel.rowconfigure(1, weight=1)
        self.summary = tk.Text(
            summary_panel,
            bg="#11151b",
            fg="#cfd7df",
            relief="flat",
            wrap="word",
            padx=9,
            pady=8,
            state="disabled",
        )
        self.summary.grid(row=1, column=0, sticky="nsew", padx=10, pady=(8, 6))
        report_row = tk.Frame(summary_panel, bg="#1b2027")
        report_row.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
        report_row.columnconfigure(0, weight=1)
        tk.Entry(
            report_row,
            textvariable=self.report_var,
            bg="#252b34",
            fg="#dce3ea",
            insertbackground="#ffffff",
            relief="flat",
        ).grid(row=0, column=0, sticky="ew")
        report_button = tk.Button(
            report_row, text="Report...", command=self._browse_report,
            bg="#303740", fg="#e5e9ee", relief="flat",
        )
        report_button.grid(row=0, column=1, padx=(6, 0))
        self._action_widgets.append(report_button)

        validation_buttons = tk.Frame(summary_panel, bg="#1b2027")
        validation_buttons.grid(row=3, column=0, sticky="ew", padx=10, pady=(5, 10))
        validate_button = tk.Button(
            validation_buttons,
            text="Validate existing report",
            command=self._validate_report,
            bg="#303740", fg="#e5e9ee", relief="flat",
        )
        validate_button.pack(side="left")
        dedit_button = tk.Button(
            validation_buttons,
            text="Record DEDit open/save pass",
            command=self._record_dedit_pass,
            bg="#5b426f", fg="#ffffff", relief="flat",
        )
        dedit_button.pack(side="left", padx=(7, 0))
        self._action_widgets.extend((validate_button, dedit_button))
        tk.Label(
            summary_panel,
            text=(
                "This dialog never launches DEDit, Processor, or the game. "
                "The DEDit button records an explicit manual attestation."
            ),
            bg="#1b2027", fg="#8f9aa6", justify="left", wraplength=390,
        ).grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 10))

        footer = tk.Frame(outer, bg="#15191f")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        tk.Label(
            footer, textvariable=self.status_var, bg="#15191f", fg="#9aa5b1",
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        tk.Button(
            footer, text="Close", command=self._close,
            bg="#30343b", fg="#e6e6e6", relief="flat",
        ).grid(row=0, column=1, padx=(8, 0))
        convert_button = tk.Button(
            footer,
            text="Convert and validate ED",
            command=self._convert,
            bg="#2c5e8a", fg="white", activebackground="#3a78ad", relief="flat",
        )
        convert_button.grid(row=0, column=2, padx=(8, 0))
        self._action_widgets.append(convert_button)

    def _panel(self, parent: tk.Misc, title: str) -> tk.Frame:
        panel = tk.Frame(parent, bg="#1b2027", highlightbackground="#2d3540", highlightthickness=1)
        tk.Label(
            panel, text=title, bg="#1b2027", fg="#e6ebf0",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(9, 2))
        return panel

    def _label(self, parent: tk.Misc, row: int, text: str) -> None:
        tk.Label(parent, text=text, bg="#1b2027", fg="#aeb7c2").grid(
            row=row, column=0, sticky="w", padx=(10, 8), pady=4,
        )

    def _entry(self, parent: tk.Misc, row: int, variable: tk.StringVar) -> tk.Entry:
        entry = tk.Entry(
            parent, textvariable=variable, bg="#252b34", fg="#dce3ea",
            insertbackground="#ffffff", relief="flat",
        )
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        self._last_entry = entry
        return entry

    def _path_row(self, parent, row, label, variable, command) -> int:
        self._label(parent, row, label)
        self._entry(parent, row, variable)
        button = tk.Button(
            parent, text="Browse...", command=command,
            bg="#303740", fg="#e5e9ee", relief="flat",
        )
        button.grid(row=row, column=2, padx=(6, 10), pady=4)
        self._action_widgets.append(button)
        return row + 1

    def _entry_row(self, parent, row, label, variable, hint="") -> int:
        self._label(parent, row, label)
        self._entry(parent, row, variable)
        if hint:
            tk.Label(parent, text=hint, bg="#1b2027", fg="#6f7c89").grid(
                row=row, column=2, sticky="w", padx=(6, 10), pady=4,
            )
        return row + 1

    def _combo_row(self, parent, row, label, variable, values) -> int:
        self._label(parent, row, label)
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        combo.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(0, 10), pady=4)
        return row + 1

    def _two_value_row(self, parent, row, label, first, second, first_hint, second_hint) -> int:
        self._label(parent, row, label)
        holder = tk.Frame(parent, bg="#1b2027")
        holder.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(0, 10), pady=4)
        holder.columnconfigure(0, weight=1)
        holder.columnconfigure(2, weight=1)
        tk.Entry(
            holder, textvariable=first, bg="#252b34", fg="#dce3ea",
            insertbackground="#ffffff", relief="flat",
        ).grid(row=0, column=0, sticky="ew")
        tk.Label(holder, text=first_hint, bg="#1b2027", fg="#73808d").grid(row=0, column=1, padx=5)
        tk.Entry(
            holder, textvariable=second, bg="#252b34", fg="#dce3ea",
            insertbackground="#ffffff", relief="flat",
        ).grid(row=0, column=2, sticky="ew")
        tk.Label(holder, text=second_hint, bg="#1b2027", fg="#73808d").grid(row=0, column=3, padx=(5, 0))
        return row + 1

    def _browse_source(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Choose glTF/GLB source",
            initialdir=self._path_initial_dir(self.source_var.get()),
            filetypes=(("glTF 2.0", "*.gltf *.glb"), ("All files", "*.*")),
            parent=self,
        )
        if chosen:
            self.source_var.set(chosen)
            self.output_var.set(suggest_output_path(chosen, self._output_mode()))

    def _browse_output(self) -> None:
        chosen = filedialog.asksaveasfilename(
            title="Write DEDit ED",
            initialdir=self._path_initial_dir(self.output_var.get() or self.source_var.get()),
            initialfile=os.path.basename(self.output_var.get()) or None,
            defaultextension=".ed",
            filetypes=(("DEDit ED", "*.ed"),),
            parent=self,
        )
        if chosen:
            self.output_var.set(chosen)

    def _browse_material_map(self) -> None:
        self._browse_json(self.material_map_var, "Choose material map JSON")

    def _browse_texture_dimensions(self) -> None:
        self._browse_json(self.texture_dimensions_var, "Choose texture dimensions JSON")

    def _browse_json(self, variable: tk.StringVar, title: str) -> None:
        chosen = filedialog.askopenfilename(
            title=title,
            initialdir=self._path_initial_dir(variable.get() or self.source_var.get()),
            filetypes=(("JSON", "*.json"), ("All files", "*.*")),
            parent=self,
        )
        if chosen:
            variable.set(chosen)

    def _browse_report(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Choose Phase 7 conversion report",
            initialdir=self._path_initial_dir(self.report_var.get() or self.output_var.get()),
            filetypes=(("glTF-to-ED report", "*.gltf_to_ed_report.json"), ("JSON", "*.json")),
            parent=self,
        )
        if chosen:
            self.report_var.set(chosen)

    def _path_initial_dir(self, value: str) -> str:
        path = os.path.abspath(str(value or "")) if value else ""
        if os.path.isdir(path):
            return path
        if path and os.path.isdir(os.path.dirname(path)):
            return os.path.dirname(path)
        return self._initial_dir

    def _output_mode(self) -> str:
        return OUTPUT_MODE_LABELS[self.output_mode_label_var.get()]

    def _sync_output_suggestion(self, *_args) -> None:
        source = self.source_var.get().strip()
        output = self.output_var.get().strip()
        if not source or not output:
            return
        prefab_path = suggest_output_path(source, gltf_ed_assembly.PREFAB)
        full_path = suggest_output_path(source, gltf_ed_assembly.FULL_WORLD)
        if os.path.normcase(output) in {os.path.normcase(prefab_path), os.path.normcase(full_path)}:
            self.output_var.set(suggest_output_path(source, self._output_mode()))

    def _sync_slab_fields(self, *_args) -> None:
        policy = GEOMETRY_POLICY_LABELS[self.geometry_policy_label_var.get()]
        state = "normal" if policy == gltf_brushes.TRIANGLE_SLAB else "disabled"
        for entry in (
            self._slab_thickness_entry, self._slab_back_entry, self._slab_side_entry,
        ):
            entry.configure(state=state)

    def _request(self) -> GltfToEdUiRequest:
        return build_conversion_request(
            source_path=self.source_var.get(),
            output_path=self.output_var.get(),
            output_mode=self._output_mode(),
            geometry_policy=GEOMETRY_POLICY_LABELS[self.geometry_policy_label_var.get()],
            coordinate_preset=COORDINATE_LABELS[self.coordinate_label_var.get()],
            unit_scale=self.unit_scale_var.get(),
            weld_tolerance=self.weld_tolerance_var.get(),
            material_map_path=self.material_map_var.get(),
            texture_dimensions_path=self.texture_dimensions_var.get(),
            fallback_texture=self.fallback_texture_var.get(),
            fallback_texture_width=self.fallback_width_var.get(),
            fallback_texture_height=self.fallback_height_var.get(),
            default_uv_projection=UV_POLICY_LABELS[self.uv_policy_label_var.get()],
            slab_thickness=self.slab_thickness_var.get(),
            slab_back_texture=self.slab_back_texture_var.get(),
            slab_side_texture=self.slab_side_texture_var.get(),
            overwrite=self.overwrite_var.get(),
        )

    def _convert(self) -> None:
        try:
            request = self._request()
        except ValueError as exc:
            messagebox.showerror("Conversion options", str(exc), parent=self)
            return
        self.status_var.set("Converting and validating ED...")
        self._start_task(lambda: execute_conversion_request(request), self._conversion_finished)

    def _conversion_finished(self, value: object) -> None:
        result = value
        if not isinstance(result, GltfToEdUiResult):
            raise TypeError("conversion worker returned an unexpected result")
        self.result = result
        self.report_var.set(result.conversion.json_report_path)
        self._set_summary(format_ui_result(result))
        self.status_var.set(f"Conversion finished: {result.conversion.status}")
        automatic_validation_passed = bool(
            result.validation is not None
            and result.validation.stages["ed_integrity"].state == gltf_to_ed_validation.PASS
            and result.validation.stages["ed_roundtrip"].state == gltf_to_ed_validation.PASS
            and result.validation.status not in {"blocked", "validation_failed", "write_failed"}
        )
        if (
            result.conversion.status in {"ready_prefab", "ready_full_world"}
            and automatic_validation_passed
        ):
            messagebox.showinfo(
                "ED conversion complete",
                "The ED and reports were written. Automatic disk validation passed; "
                "external DEDit/Processor/game checks remain separate.",
                parent=self,
            )
        else:
            messagebox.showerror(
                "ED conversion did not complete",
                "Conversion or automatic disk validation did not pass. Review "
                "the blockers in the result panel and JSON report.",
                parent=self,
            )

    def _validate_report(self) -> None:
        path = self.report_var.get().strip()
        if not path:
            messagebox.showerror("Conversion report", "Choose a Phase 7 JSON report.", parent=self)
            return
        self.status_var.set("Validating ED artifact...")
        self._start_task(
            lambda: validate_existing_report(path),
            self._validation_finished,
        )

    def _record_dedit_pass(self) -> None:
        path = self.report_var.get().strip()
        if not path:
            messagebox.showerror("Conversion report", "Choose a Phase 7 JSON report.", parent=self)
            return
        if not messagebox.askyesno(
            "Record DEDit validation?",
            "Confirm that you opened this exact ED in LithTech 2.1 DEDit and "
            "successfully saved it. This records manual evidence; it does not "
            "launch or inspect DEDit.",
            parent=self,
        ):
            return
        self.status_var.set("Recording DEDit open/save evidence...")
        self._start_task(
            lambda: validate_existing_report(path, record_dedit_pass=True),
            self._validation_finished,
        )

    def _validation_finished(self, value: object) -> None:
        report = value
        if not isinstance(report, gltf_to_ed_validation.GltfToEdValidationReport):
            raise TypeError("validation worker returned an unexpected result")
        self._set_summary(gltf_to_ed_validation.format_gltf_to_ed_validation_report(report))
        self.status_var.set(f"Validation finished: {report.status}")
        if report.status in {"blocked", "validation_failed", "write_failed"}:
            messagebox.showerror("ED validation failed", "Review the validation result panel.", parent=self)
        else:
            messagebox.showinfo("ED validation updated", f"Status: {report.status}", parent=self)

    def _start_task(self, task: Callable[[], object], callback: Callable[[object], None]) -> None:
        if self._future is not None:
            return
        self._set_busy(True)
        self._future_callback = callback
        self._future = self._executor.submit(task)
        self.after(60, self._poll_task)

    def _poll_task(self) -> None:
        future = self._future
        if future is None:
            return
        if not future.done():
            self.after(60, self._poll_task)
            return
        callback = self._future_callback
        self._future = None
        self._future_callback = None
        self._set_busy(False)
        try:
            value = future.result()
            if callback is not None:
                callback(value)
        except Exception as exc:
            self.status_var.set("Operation failed.")
            messagebox.showerror("glTF to ED failed", str(exc), parent=self)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for widget in self._action_widgets:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        self.configure(cursor="watch" if busy else "")
        self.update_idletasks()

    def _set_summary(self, text: str) -> None:
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", text)
        self.summary.configure(state="disabled")

    def _activate_modal(self) -> None:
        self.lift(self._parent_window)
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _close(self) -> None:
        if self._future is not None:
            messagebox.showinfo(
                "Operation in progress",
                "Wait for the current conversion or validation operation to finish.",
                parent=self,
            )
            return
        try:
            if self.grab_current() is self:
                self.grab_release()
        except tk.TclError:
            pass
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()

    @classmethod
    def open(cls, parent: tk.Misc, *, initial_dir: str = "") -> "GltfToEdDialog":
        return cls(parent, initial_dir=initial_dir)


def _finite_float(
    label: str,
    value: str,
    *,
    minimum: Optional[float] = None,
    inclusive: bool = True,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite.")
    if minimum is not None:
        invalid = number < minimum if inclusive else number <= minimum
        if invalid:
            relation = "at least" if inclusive else "greater than"
            raise ValueError(f"{label} must be {relation} {minimum:g}.")
    return number


def _optional_dimensions(width: str, height: str) -> Optional[Tuple[float, float]]:
    left = str(width or "").strip()
    right = str(height or "").strip()
    if not left and not right:
        return None
    if not left or not right:
        raise ValueError("Fallback texture width and height must be supplied together.")
    return (
        _finite_float("Fallback texture width", left, minimum=0.0, inclusive=False),
        _finite_float("Fallback texture height", right, minimum=0.0, inclusive=False),
    )


def _optional_json_path(label: str, value: str) -> str:
    path = str(value or "").strip()
    if not path:
        return ""
    absolute = os.path.abspath(path)
    if not os.path.isfile(absolute):
        raise ValueError(f"{label} file was not found.")
    if os.path.splitext(absolute)[1].lower() != ".json":
        raise ValueError(f"{label} must use the .json extension.")
    return absolute


__all__ = [
    "COORDINATE_LABELS",
    "GEOMETRY_POLICY_LABELS",
    "GltfToEdDialog",
    "GltfToEdUiRequest",
    "GltfToEdUiResult",
    "OUTPUT_MODE_LABELS",
    "UV_POLICY_LABELS",
    "build_conversion_request",
    "execute_conversion_request",
    "format_ui_result",
    "suggest_output_path",
    "validate_existing_report",
]
