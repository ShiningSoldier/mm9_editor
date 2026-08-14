"""Consolidated workspace for preparing a static prefab import."""

from __future__ import annotations

import os
import re
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from tkinter import filedialog, ttk
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from features.prefabs.inspector import PrefabInspection
from features.prefabs.graph import PrefabAnalysis, SupportState
from features.prefabs.resource_backed import ResourceBackedCandidate


_PREFAB_EXTENSIONS = {".ed", ".dat"}
_ANCHOR_LABELS = {
    "Bottom center (surface placement)": "bottom_center",
    "Original prefab origin": "original_origin",
    "Bounds center": "center",
    "Controller pivot": "controller_pivot",
}
_ANCHOR_LABEL_BY_VALUE = {value: label for label, value in _ANCHOR_LABELS.items()}


@dataclass(frozen=True)
class PrefabImportRequest:
    prefab_path: str
    new_name: str
    collision_mode: str
    collision_thickness: float
    collision_segment_length: float
    placement_anchor: str
    browser_root: str
    import_mode: str = "static"
    external_bindings: Dict[str, str] = field(default_factory=dict)
    resource_candidate_id: str = ""
    resource_class: str = ""
    resource_model: str = ""
    resource_skins: Tuple[str, ...] = ()


def discover_prefab_files(root: str, query: str = "") -> List[str]:
    """Return ED/DAT prefab files below *root*, sorted by relative path."""
    root = os.path.abspath(str(root or ""))
    if not os.path.isdir(root):
        return []
    needle = str(query or "").strip().lower()
    matches: List[str] = []
    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = sorted(
            name for name in dir_names
            if not name.startswith(".") and name.lower() != "__pycache__"
        )
        for file_name in sorted(file_names):
            if os.path.splitext(file_name)[1].lower() not in _PREFAB_EXTENSIONS:
                continue
            path = os.path.join(current_root, file_name)
            relative = os.path.relpath(path, root).replace("\\", "/")
            if needle and needle not in relative.lower():
                continue
            matches.append(os.path.abspath(path))
    return sorted(matches, key=lambda path: os.path.relpath(path, root).lower())


def available_placement_anchors(info: PrefabInspection) -> Tuple[str, ...]:
    """Return placement anchors that can be resolved for *info*."""
    anchors = ["bottom_center", "original_origin", "center"]
    model_names = {model.name.lower() for model in info.models if model.name}
    if info.source_format == "legacy_ed":
        has_controller_pivot = any(
            obj.position is not None for obj in info.behavior_objects
        )
    else:
        has_controller_pivot = any(
            obj.position is not None and obj.name and obj.name.lower() in model_names
            for obj in info.objects
        )
    if has_controller_pivot:
        anchors.append("controller_pivot")
    return tuple(anchors)


def format_workspace_summary(
    info: PrefabInspection,
    analysis: Optional[PrefabAnalysis] = None,
) -> str:
    """Build the compact inspection text shown beside the source browser."""
    lines = [
        f"Source: {os.path.basename(info.path)}",
        f"Format: {'DEdit ED source' if info.source_format == 'legacy_ed' else 'compiled DAT'} "
        f"(version {info.version})",
        f"Geometry: {info.model_count} part(s), {info.total_polygons} polygon(s)",
    ]
    if info.bounds_min is not None and info.bounds_max is not None:
        lines.append(
            "Bounds: "
            f"{_format_vec(info.bounds_min)} -> {_format_vec(info.bounds_max)}"
        )
    if info.model_roles:
        lines.append("Roles: " + _format_counts(info.model_roles))
    if info.object_classes:
        lines.append("Source objects: " + _format_counts(info.object_classes))
    if analysis is not None:
        ownership_counts: Dict[str, int] = {}
        for brush in analysis.graph.brushes:
            ownership_counts[brush.ownership] = ownership_counts.get(brush.ownership, 0) + 1
        local_links = [
            item for item in analysis.graph.references if item.target_kind == "local"
        ]
        external_links = [
            item for item in analysis.graph.references if item.target_kind == "external"
        ]
        dependency_counts: Dict[str, int] = {}
        for dependency in analysis.graph.dependencies:
            key = f"{dependency.resource_type}/{dependency.availability}"
            dependency_counts[key] = dependency_counts.get(key, 0) + 1
        lines.extend([
            "",
            f"Static import: {analysis.static_state.label}",
            f"Full behavior: {analysis.behavioral_state.label}",
        ])
        if ownership_counts:
            lines.append("Brush ownership: " + _format_counts(ownership_counts))
        if analysis.graph.references:
            lines.append(
                f"Links: internal={len(local_links)}, external={len(external_links)}"
            )
            for reference in analysis.graph.references[:5]:
                lines.append(
                    f"- {reference.property_name}: {reference.source_value} "
                    f"[{reference.target_kind}]"
                )
        if dependency_counts:
            lines.append("Resources: " + _format_counts(dependency_counts))
            for dependency in analysis.graph.dependencies[:5]:
                lines.append(
                    f"- {dependency.resource_type}: {dependency.path} "
                    f"[{dependency.availability}]"
                )
        important = [
            item for item in analysis.diagnostics_for("behavioral")
            if item.severity.value in {"blocking", "action_required"}
        ]
        if important:
            lines.append("Behavioral analysis:")
            lines.extend(f"- {item.message}" for item in important[:8])
    if info.behavior_object_classes:
        lines.extend([
            "",
            "Static-mode warning:",
            "Static geometry mode will not import these controller/resource objects: "
            + _format_counts(info.behavior_object_classes),
        ])
    if info.parse_warnings:
        lines.append("")
        lines.append("Source warnings:")
        lines.extend(f"- {warning}" for warning in info.parse_warnings[:8])
    if not info.models:
        lines.extend([
            "",
            "This prefab contains no static brush/BSP geometry. Resource-backed "
            "objects must be added with the object placement tools.",
        ])
    return "\n".join(lines)


def required_binding_targets(
    analysis: Optional[PrefabAnalysis],
) -> Tuple[Tuple[str, str], ...]:
    """Return unique explicit target-level bindings for workspace rendering."""
    if analysis is None:
        return ()
    required: Dict[str, Tuple[str, str]] = {}
    for ref in analysis.graph.references:
        is_portal = ref.property_name.casefold() == "portalname"
        if ref.target_kind != "external" and not is_portal:
            continue
        key = ref.source_value.casefold()
        kind = "portal" if is_portal else "object"
        previous = required.get(key)
        if previous is None or kind == "portal":
            required[key] = (ref.source_value, kind)
    return tuple(required[key] for key in sorted(required))


def build_import_request(
    *,
    prefab_path: str,
    new_name: str,
    collision_mode: str,
    collision_thickness: str,
    collision_segment_length: str,
    placement_anchor: str,
    browser_root: str,
    import_mode: str = "static",
    external_bindings: Optional[Mapping[str, str]] = None,
    resource_candidate_id: str = "",
    resource_class: str = "",
    resource_model: str = "",
    resource_skins: Sequence[str] = (),
) -> PrefabImportRequest:
    """Validate workspace fields and return their typed request."""
    path = os.path.abspath(str(prefab_path or "").strip())
    if not path or not os.path.isfile(path):
        raise ValueError("Choose an existing prefab ED or DAT file.")
    if os.path.splitext(path)[1].lower() not in _PREFAB_EXTENSIONS:
        raise ValueError("Prefab source must be a .ed or .dat file.")

    name = str(new_name or "").strip()
    if not name:
        raise ValueError("Enter an imported prefab root name.")
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError("Prefab root name may contain only letters, digits, and underscores.")

    mode = str(collision_mode or "").strip().lower()
    if mode not in {"invisible_bsp", "box_approx", "none"}:
        raise ValueError("Choose a collision strategy.")
    try:
        thickness = float(collision_thickness)
    except (TypeError, ValueError) as exc:
        raise ValueError("Box thickness must be a number.") from exc
    try:
        segment_length = float(collision_segment_length)
    except (TypeError, ValueError) as exc:
        raise ValueError("Maximum collision segment length must be a number.") from exc
    if not 1.0 <= thickness <= 512.0:
        raise ValueError("Box thickness must be between 1 and 512 units.")
    if not 64.0 <= segment_length <= 8192.0:
        raise ValueError("Maximum collision segment length must be between 64 and 8192 units.")

    anchor = str(placement_anchor or "").strip().lower()
    if anchor not in set(_ANCHOR_LABELS.values()):
        raise ValueError("Choose a placement anchor.")
    root = os.path.abspath(str(browser_root or os.path.dirname(path)))
    requested_mode = str(import_mode or "static").strip().lower()
    if requested_mode not in {"static", "behavioral", "resource", "preview"}:
        raise ValueError("Choose a supported prefab representation.")
    if requested_mode == "resource":
        if not str(resource_candidate_id or "").strip():
            raise ValueError("Choose a catalog game-model candidate.")
        if not str(resource_class or "").strip() or not str(resource_model or "").strip():
            raise ValueError("The selected game-model candidate is incomplete.")
    return PrefabImportRequest(
        prefab_path=path,
        new_name=name,
        collision_mode=mode,
        collision_thickness=thickness,
        collision_segment_length=segment_length,
        placement_anchor=anchor,
        browser_root=root,
        import_mode=requested_mode,
        external_bindings={
            str(key): str(value).strip()
            for key, value in (external_bindings or {}).items()
            if str(key).strip() and str(value).strip()
        },
        resource_candidate_id=str(resource_candidate_id or ""),
        resource_class=str(resource_class or ""),
        resource_model=str(resource_model or ""),
        resource_skins=tuple(str(value) for value in resource_skins),
    )


class PrefabImportWorkspace(tk.Toplevel):
    """Single modal surface for browsing, inspecting, and configuring imports."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        initial_dir: str,
        inspect_prefab: Callable[[str], PrefabInspection],
        analyze_prefab: Optional[Callable[[str], PrefabAnalysis]],
        suggest_name: Callable[[str], str],
        validate_request: Callable[[PrefabImportRequest], None],
        find_resource_candidates: Optional[
            Callable[[str, PrefabInspection], Sequence[ResourceBackedCandidate]]
        ] = None,
    ) -> None:
        super().__init__(parent)
        self._parent_window = parent.winfo_toplevel()
        self._inspect_prefab = inspect_prefab
        self._analyze_prefab = analyze_prefab
        self._find_resource_candidates = find_resource_candidates
        self._suggest_name = suggest_name
        self._validate_request = validate_request
        self.result: Optional[PrefabImportRequest] = None
        self._paths: List[str] = []
        self._selected_path = ""
        self._selected_info: Optional[PrefabInspection] = None
        self._selected_analysis: Optional[PrefabAnalysis] = None
        self._selected_resource_candidates: Tuple[ResourceBackedCandidate, ...] = ()
        self._resource_candidate_by_label: Dict[str, ResourceBackedCandidate] = {}
        self._last_suggested_name = ""
        self._inspection_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="prefab-inspection",
        )
        self._inspection_generation = 0
        self._inspection_future: Optional[Future] = None
        self._inspection_cache: Dict[
            Tuple[str, Optional[int], Optional[int]],
            Tuple[
                PrefabInspection,
                Optional[PrefabAnalysis],
                Tuple[ResourceBackedCandidate, ...],
            ],
        ] = {}

        initial = os.path.abspath(initial_dir) if initial_dir else os.getcwd()
        if not os.path.isdir(initial):
            initial = os.getcwd()
        self.root_var = tk.StringVar(value=initial)
        self.filter_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.anchor_label_var = tk.StringVar(value=_ANCHOR_LABEL_BY_VALUE["bottom_center"])
        self.collision_var = tk.StringVar(value="none")
        self.thickness_var = tk.StringVar(value="8")
        self.segment_var = tk.StringVar(value="512")
        self.import_mode_var = tk.StringVar(value="static")
        self.resource_candidate_var = tk.StringVar()
        self.behavior_ack_var = tk.BooleanVar(value=False)
        self._binding_vars: Dict[str, Tuple[str, tk.StringVar]] = {}
        self.status_var = tk.StringVar(value="Choose a prefab to begin.")

        self.title("Import Prefab")
        self.configure(bg="#15191f")
        self.geometry("1040x720")
        self.minsize(860, 600)
        self.transient(self._parent_window)

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _event: self._cancel())
        self.filter_var.trace_add("write", lambda *_args: self._refresh_file_list())
        self.collision_var.trace_add("write", self._sync_collision_fields)
        self.import_mode_var.trace_add("write", self._sync_import_mode)
        self.resource_candidate_var.trace_add("write", self._sync_import_mode)
        self._refresh_file_list(select_first=True)
        self._activate_modal()

    def _build_ui(self) -> None:
        outer = tk.Frame(self, bg="#15191f", padx=14, pady=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=2, uniform="columns")
        outer.columnconfigure(1, weight=3, uniform="columns")
        outer.rowconfigure(1, weight=1)

        tk.Label(
            outer,
            text="Prefab import workspace",
            bg="#15191f",
            fg="#eef2f6",
            font=("Segoe UI", 13, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        source_panel = self._panel(outer, "Source prefab")
        source_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 7))
        source_panel.columnconfigure(0, weight=1)
        source_panel.rowconfigure(4, weight=1)

        root_row = tk.Frame(source_panel, bg="#1b2027")
        root_row.grid(row=1, column=0, sticky="ew", padx=10, pady=(8, 5))
        root_row.columnconfigure(0, weight=1)
        self.root_entry = tk.Entry(
            root_row,
            textvariable=self.root_var,
            bg="#252b34",
            fg="#dce3ea",
            insertbackground="#ffffff",
            relief="flat",
        )
        self.root_entry.grid(row=0, column=0, sticky="ew")
        self.root_entry.bind(
            "<Return>",
            lambda _event: self._refresh_file_list(select_first=True),
        )
        tk.Button(
            root_row,
            text="Folder...",
            command=self._browse_root,
            bg="#303740",
            fg="#e5e9ee",
            relief="flat",
        ).grid(row=0, column=1, padx=(6, 0))

        filter_row = tk.Frame(source_panel, bg="#1b2027")
        filter_row.grid(row=2, column=0, sticky="ew", padx=10, pady=5)
        filter_row.columnconfigure(1, weight=1)
        tk.Label(filter_row, text="Filter", bg="#1b2027", fg="#aeb7c2").grid(
            row=0, column=0, sticky="w", padx=(0, 7))
        self.filter_entry = tk.Entry(
            filter_row,
            textvariable=self.filter_var,
            bg="#252b34",
            fg="#dce3ea",
            insertbackground="#ffffff",
            relief="flat",
        )
        self.filter_entry.grid(row=0, column=1, sticky="ew")

        tk.Label(
            source_panel,
            text="Original ED sources and converted DAT prefabs",
            bg="#1b2027",
            fg="#7f8b98",
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", padx=10, pady=(3, 4))

        list_frame = tk.Frame(source_panel, bg="#1b2027")
        list_frame.grid(row=4, column=0, sticky="nsew", padx=10)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        scroll = tk.Scrollbar(list_frame, orient="vertical")
        scroll.grid(row=0, column=1, sticky="ns")
        self.file_list = tk.Listbox(
            list_frame,
            bg="#11151b",
            fg="#d8dee6",
            selectbackground="#315f86",
            selectforeground="#ffffff",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#2a313a",
            activestyle="none",
            yscrollcommand=scroll.set,
        )
        self.file_list.grid(row=0, column=0, sticky="nsew")
        scroll.configure(command=self.file_list.yview)
        self.file_list.bind("<<ListboxSelect>>", self._on_file_selected)

        source_buttons = tk.Frame(source_panel, bg="#1b2027")
        source_buttons.grid(row=5, column=0, sticky="ew", padx=10, pady=10)
        self.file_count_label = tk.Label(
            source_buttons,
            text="",
            bg="#1b2027",
            fg="#7f8b98",
        )
        self.file_count_label.pack(side="left")
        tk.Button(
            source_buttons,
            text="Open file...",
            command=self._open_file,
            bg="#303740",
            fg="#e5e9ee",
            relief="flat",
        ).pack(side="right")

        right = tk.Frame(outer, bg="#15191f")
        right.grid(row=1, column=1, sticky="nsew", padx=(7, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=2)
        right.rowconfigure(1, weight=3)

        inspection_panel = self._panel(right, "Inspection")
        inspection_panel.grid(row=0, column=0, sticky="nsew", pady=(0, 7))
        inspection_panel.columnconfigure(0, weight=1)
        inspection_panel.rowconfigure(1, weight=1)
        self.summary = tk.Text(
            inspection_panel,
            bg="#11151b",
            fg="#cfd7df",
            relief="flat",
            wrap="word",
            height=10,
            padx=9,
            pady=7,
            state="disabled",
        )
        self.summary.grid(row=1, column=0, sticky="nsew", padx=10, pady=(8, 10))

        settings_panel = self._panel(right, "Import settings")
        settings_panel.grid(row=1, column=0, sticky="nsew", pady=(7, 0))
        settings_panel.columnconfigure(1, weight=1)

        self._field_label(settings_panel, "Imported root name", 1)
        self.name_entry = self._entry(settings_panel, self.name_var)
        self.name_entry.grid(row=1, column=1, sticky="ew", padx=(8, 10), pady=(8, 4))

        self._field_label(settings_panel, "Placement anchor", 2)
        self.anchor_combo = ttk.Combobox(
            settings_panel,
            textvariable=self.anchor_label_var,
            state="readonly",
        )
        self.anchor_combo.grid(row=2, column=1, sticky="ew", padx=(8, 10), pady=4)

        self._field_label(settings_panel, "Import type", 3, sticky="nw")
        mode_frame = tk.Frame(settings_panel, bg="#1b2027")
        mode_frame.grid(row=3, column=1, sticky="ew", padx=(8, 10), pady=4)
        self.resource_mode_radio = tk.Radiobutton(
            mode_frame,
            text="Use catalog game model (recommended)",
            value="resource",
            variable=self.import_mode_var,
            **self._radio_style(),
        )
        self.resource_mode_radio.pack(anchor="w")
        self.static_mode_radio = tk.Radiobutton(
            mode_frame,
            text="Import DEdit-compiled BSP",
            value="static",
            variable=self.import_mode_var,
            **self._radio_style(),
        )
        self.static_mode_radio.pack(anchor="w")
        self.behavioral_mode_radio = tk.Radiobutton(
            mode_frame,
            text="Full behavior (supported capabilities)",
            value="behavioral",
            variable=self.import_mode_var,
            **self._radio_style(),
        )
        self.behavioral_mode_radio.pack(anchor="w")
        self.preview_mode_radio = tk.Radiobutton(
            mode_frame,
            text="ED brush preview only (cannot save/install)",
            value="preview",
            variable=self.import_mode_var,
            **self._radio_style(),
        )
        self.preview_mode_radio.pack(anchor="w")

        self._field_label(settings_panel, "Game model", 4)
        self.resource_candidate_combo = ttk.Combobox(
            settings_panel,
            textvariable=self.resource_candidate_var,
            state="disabled",
        )
        self.resource_candidate_combo.grid(
            row=4, column=1, sticky="ew", padx=(8, 10), pady=4
        )

        self._field_label(settings_panel, "Collision", 5, sticky="nw")
        collision_frame = tk.Frame(settings_panel, bg="#1b2027")
        collision_frame.grid(row=5, column=1, sticky="ew", padx=(8, 10), pady=4)
        self.collision_frame = collision_frame
        self.authored_collision_radio = tk.Radiobutton(
            collision_frame,
            text="Use authored solid geometry",
            value="invisible_bsp",
            variable=self.collision_var,
            **self._radio_style(),
        )
        self.authored_collision_radio.pack(anchor="w")
        self.box_collision_radio = tk.Radiobutton(
            collision_frame,
            text="Thin box approximation (unavailable: preview BSP)",
            value="box_approx",
            variable=self.collision_var,
            **self._radio_style(),
        )
        self.box_collision_radio.pack(anchor="w")
        self.box_collision_radio.configure(state="disabled")
        tk.Radiobutton(
            collision_frame,
            text="No collision helper",
            value="none",
            variable=self.collision_var,
            **self._radio_style(),
        ).pack(anchor="w")

        numeric_frame = tk.Frame(settings_panel, bg="#1b2027")
        numeric_frame.grid(row=6, column=1, sticky="w", padx=(8, 10), pady=(2, 5))
        tk.Label(numeric_frame, text="Thickness", bg="#1b2027", fg="#8f9ba8").grid(
            row=0, column=0, sticky="w")
        self.thickness_entry = self._entry(numeric_frame, self.thickness_var, width=7)
        self.thickness_entry.grid(row=0, column=1, padx=(6, 14))
        tk.Label(numeric_frame, text="Max segment", bg="#1b2027", fg="#8f9ba8").grid(
            row=0, column=2, sticky="w")
        self.segment_entry = self._entry(numeric_frame, self.segment_var, width=7)
        self.segment_entry.grid(row=0, column=3, padx=(6, 0))

        self.behavior_frame = tk.Frame(
            settings_panel,
            bg="#302719",
            highlightbackground="#755b2b",
            highlightthickness=1,
        )
        self.binding_frame = tk.Frame(
            settings_panel,
            bg="#202936",
            highlightbackground="#3c5874",
            highlightthickness=1,
        )
        self.binding_frame.grid(
            row=7, column=0, columnspan=2, sticky="ew", padx=10, pady=(5, 4)
        )
        self.binding_frame.grid_remove()

        self.behavior_frame.grid(row=8, column=0, columnspan=2, sticky="ew", padx=10, pady=(5, 4))
        self.behavior_label = tk.Label(
            self.behavior_frame,
            text="",
            bg="#302719",
            fg="#e6c27a",
            justify="left",
            anchor="w",
            wraplength=520,
        )
        self.behavior_label.pack(fill="x", padx=8, pady=(7, 2))
        self.behavior_check = tk.Checkbutton(
            self.behavior_frame,
            text="Omit these source objects and their behavior",
            variable=self.behavior_ack_var,
            bg="#302719",
            fg="#f0d69a",
            selectcolor="#1b2027",
            activebackground="#302719",
            activeforeground="#ffffff",
            anchor="w",
        )
        self.behavior_check.pack(fill="x", padx=5, pady=(0, 6))
        self.behavior_frame.grid_remove()

        footer = tk.Frame(outer, bg="#15191f")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(11, 0))
        footer.columnconfigure(0, weight=1)
        self.status_label = tk.Label(
            footer,
            textvariable=self.status_var,
            bg="#15191f",
            fg="#9aa6b2",
            justify="left",
            anchor="w",
            wraplength=720,
        )
        self.status_label.grid(row=0, column=0, sticky="ew")
        tk.Button(
            footer,
            text="Cancel",
            command=self._cancel,
            bg="#303740",
            fg="#e5e9ee",
            relief="flat",
        ).grid(row=0, column=1, padx=(10, 7))
        self.place_button = tk.Button(
            footer,
            text="Start placement",
            command=self._submit,
            bg="#2c6b9b",
            fg="#ffffff",
            activebackground="#3b82b8",
            relief="flat",
            state="disabled",
        )
        self.place_button.grid(row=0, column=2)

    def _panel(self, parent: tk.Misc, title: str) -> tk.Frame:
        frame = tk.Frame(
            parent,
            bg="#1b2027",
            highlightbackground="#2a313a",
            highlightthickness=1,
        )
        tk.Label(
            frame,
            text=title,
            bg="#1b2027",
            fg="#e0e6ec",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 0))
        return frame

    @staticmethod
    def _field_label(parent: tk.Misc, text: str, row: int, sticky: str = "w") -> None:
        tk.Label(parent, text=text, bg="#1b2027", fg="#aeb7c2").grid(
            row=row, column=0, sticky=sticky, padx=(10, 0), pady=4)

    @staticmethod
    def _entry(parent: tk.Misc, variable: tk.StringVar, width: Optional[int] = None) -> tk.Entry:
        kwargs = {"width": width} if width is not None else {}
        return tk.Entry(
            parent,
            textvariable=variable,
            bg="#252b34",
            fg="#e2e7ec",
            insertbackground="#ffffff",
            relief="flat",
            **kwargs,
        )

    @staticmethod
    def _radio_style() -> dict:
        return {
            "bg": "#1b2027",
            "fg": "#d5dce4",
            "selectcolor": "#252b34",
            "activebackground": "#1b2027",
            "activeforeground": "#ffffff",
            "anchor": "w",
        }

    def _activate_modal(self) -> None:
        self.lift(self._parent_window)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.filter_entry.focus_set()

    def _browse_root(self) -> None:
        chosen = filedialog.askdirectory(
            title="Choose prefab folder",
            initialdir=self.root_var.get() if os.path.isdir(self.root_var.get()) else None,
            parent=self,
        )
        if chosen:
            self.root_var.set(os.path.abspath(chosen))
            self.filter_var.set("")
            self._refresh_file_list(select_first=True)

    def _open_file(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Choose DEdit or compiled prefab",
            initialdir=self.root_var.get() if os.path.isdir(self.root_var.get()) else None,
            filetypes=[
                ("Prefab files", ("*.ed", "*.dat")),
                ("DEdit source prefabs", "*.ed"),
                ("Compiled DAT prefabs", "*.dat"),
                ("All files", "*.*"),
            ],
            parent=self,
        )
        if chosen:
            self._load_path(chosen)

    def _refresh_file_list(self, *_args, select_first: bool = False) -> None:
        root = self.root_var.get().strip()
        previous = self._selected_path
        self._paths = discover_prefab_files(root, self.filter_var.get())
        self.file_list.delete(0, "end")
        for path in self._paths:
            self.file_list.insert("end", os.path.relpath(path, root).replace("\\", "/"))
        self.file_count_label.configure(text=f"{len(self._paths)} prefab(s)")
        wanted = previous if previous in self._paths else (self._paths[0] if self._paths else "")
        if wanted:
            index = self._paths.index(wanted)
            self.file_list.selection_set(index)
            self.file_list.see(index)
            self._load_path(wanted)
        elif not self._paths:
            self._clear_selection("No ED or DAT prefab files match this folder/filter.")

    def _on_file_selected(self, _event=None) -> None:
        selection = self.file_list.curselection()
        if selection:
            self._load_path(self._paths[int(selection[0])])

    def _load_path(self, path: str) -> None:
        absolute = os.path.abspath(path)
        cache_key = self._inspection_cache_key(absolute)
        cached = self._inspection_cache.get(cache_key)
        if cached is not None:
            self._inspection_generation += 1
            if self._inspection_future is not None:
                self._inspection_future.cancel()
            self.configure(cursor="")
            self._apply_inspection(absolute, *cached)
            return
        self.configure(cursor="watch")
        self.status_var.set("Inspecting prefab...")
        self.place_button.configure(state="disabled")
        self._inspection_generation += 1
        generation = self._inspection_generation
        if self._inspection_future is not None:
            self._inspection_future.cancel()
        self._inspection_future = self._inspection_executor.submit(
            self._inspect_and_analyze,
            absolute,
        )
        self.after(25, lambda: self._poll_inspection(generation, absolute, cache_key))

    @staticmethod
    def _inspection_cache_key(path: str) -> Tuple[str, Optional[int], Optional[int]]:
        try:
            stat = os.stat(path)
            return os.path.abspath(path), int(stat.st_size), int(stat.st_mtime_ns)
        except OSError:
            return os.path.abspath(path), None, None

    def _inspect_and_analyze(
        self,
        path: str,
    ) -> Tuple[
        PrefabInspection,
        Optional[PrefabAnalysis],
        Tuple[ResourceBackedCandidate, ...],
    ]:
        info = self._inspect_prefab(path)
        analysis = self._analyze_prefab(path) if self._analyze_prefab is not None else None
        candidates = tuple(
            self._find_resource_candidates(path, info)
            if self._find_resource_candidates is not None else ()
        )
        return info, analysis, candidates

    def _poll_inspection(
        self,
        generation: int,
        path: str,
        cache_key: Tuple[str, Optional[int], Optional[int]],
    ) -> None:
        try:
            exists = bool(self.winfo_exists())
        except tk.TclError:
            return
        if generation != self._inspection_generation or not exists:
            return
        future = self._inspection_future
        if future is None or not future.done():
            self.after(25, lambda: self._poll_inspection(generation, path, cache_key))
            return
        try:
            info, analysis, candidates = future.result()
        except Exception as exc:
            self._clear_selection(f"Cannot inspect prefab: {exc}", error=True)
            return
        finally:
            self.configure(cursor="")

        self._inspection_cache[cache_key] = (info, analysis, candidates)
        self._apply_inspection(path, info, analysis, candidates)

    def _apply_inspection(
        self,
        path: str,
        info: PrefabInspection,
        analysis: Optional[PrefabAnalysis],
        resource_candidates: Sequence[ResourceBackedCandidate] = (),
    ) -> None:
        self._selected_path = os.path.abspath(path)
        self._selected_info = info
        self._selected_analysis = analysis
        self._selected_resource_candidates = tuple(resource_candidates)
        self._resource_candidate_by_label = {
            candidate.display_name: candidate
            for candidate in self._selected_resource_candidates
        }
        labels = list(self._resource_candidate_by_label)
        self.resource_candidate_combo.configure(values=labels)
        self.resource_candidate_var.set(labels[0] if labels else "")
        self._set_summary(format_workspace_summary(info, analysis))
        self._rebuild_binding_fields(analysis)
        self.behavior_ack_var.set(False)
        if info.behavior_object_classes:
            classes = _format_counts(info.behavior_object_classes)
            self.behavior_label.configure(
                text=f"The selected non-behavioral representation cannot retain these source objects: {classes}."
            )
            self.behavior_frame.grid()
        else:
            self.behavior_frame.grid_remove()

        anchors = available_placement_anchors(info)
        labels = [_ANCHOR_LABEL_BY_VALUE[value] for value in anchors]
        self.anchor_combo.configure(values=labels)
        if self.anchor_label_var.get() not in labels:
            self.anchor_label_var.set(labels[0])

        collision_text = "Use authored PhysicsBSP (recommended)"
        self.authored_collision_radio.configure(text=collision_text)
        self.collision_var.set(
            "invisible_bsp"
            if info.source_format == "compiled_dat" and info.has_authored_collision
            else "none"
        )

        current_name = self.name_var.get().strip()
        if not current_name or current_name == self._last_suggested_name:
            suggested = self._suggest_name(self._selected_path)
            self._last_suggested_name = suggested
            self.name_var.set(suggested)
        if not info.models and info.behavior_objects:
            original_label = _ANCHOR_LABEL_BY_VALUE["original_origin"]
            if original_label in labels:
                self.anchor_label_var.set(original_label)
            self.import_mode_var.set("behavioral")
        elif self._selected_resource_candidates:
            self.import_mode_var.set("resource")
        elif info.source_format == "compiled_dat":
            self.import_mode_var.set("static")
        elif info.models:
            self.import_mode_var.set("preview")
        else:
            self.import_mode_var.set("behavioral")
        self._sync_import_mode()

    def _clear_selection(self, message: str, error: bool = False) -> None:
        self._inspection_generation += 1
        if self._inspection_future is not None:
            self._inspection_future.cancel()
        self.configure(cursor="")
        self._selected_path = ""
        self._selected_info = None
        self._selected_analysis = None
        self._selected_resource_candidates = ()
        self._resource_candidate_by_label.clear()
        self.resource_candidate_var.set("")
        self.resource_candidate_combo.configure(values=(), state="disabled")
        self._rebuild_binding_fields(None)
        self._set_summary(message)
        self.behavior_frame.grid_remove()
        self.place_button.configure(state="disabled")
        self.status_var.set(message)
        self._set_status_error(error)

    def _set_summary(self, text: str) -> None:
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", text)
        self.summary.configure(state="disabled")

    def _rebuild_binding_fields(self, analysis: Optional[PrefabAnalysis]) -> None:
        for child in self.binding_frame.winfo_children():
            child.destroy()
        self._binding_vars.clear()
        requirements = required_binding_targets(analysis)
        if not requirements:
            self.binding_frame.grid_remove()
            return
        tk.Label(
            self.binding_frame,
            text="Target-level bindings",
            bg="#202936",
            fg="#d8e7f5",
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(6, 2))
        self.binding_frame.columnconfigure(0, weight=1)
        list_frame = tk.Frame(self.binding_frame, bg="#202936")
        list_frame.grid(row=1, column=0, sticky="ew", padx=8)
        list_frame.columnconfigure(0, weight=1)
        canvas = tk.Canvas(
            list_frame,
            bg="#202936",
            highlightthickness=0,
            height=min(136, max(30, len(requirements) * 30)),
        )
        canvas.grid(row=0, column=0, sticky="ew")
        entries = tk.Frame(canvas, bg="#202936")
        entries.columnconfigure(1, weight=1)
        window_id = canvas.create_window((0, 0), window=entries, anchor="nw")
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        if len(requirements) > 4:
            scrollbar.grid(row=0, column=1, sticky="ns")
            canvas.configure(yscrollcommand=scrollbar.set)

        def _resize_binding_list(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(window_id, width=canvas.winfo_width())

        entries.bind("<Configure>", _resize_binding_list)
        canvas.bind("<Configure>", _resize_binding_list)
        for row, (source_name, kind) in enumerate(requirements):
            suffix = "portal" if kind == "portal" else "object"
            tk.Label(
                entries,
                text=f"{source_name} ({suffix})",
                bg="#202936",
                fg="#aebdcb",
                anchor="w",
            ).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=2)
            variable = tk.StringVar()
            variable.trace_add("write", self._sync_import_mode)
            entry = self._entry(entries, variable)
            entry.grid(row=row, column=1, sticky="ew", pady=2)
            self._binding_vars[source_name.casefold()] = (source_name, variable)
        tk.Label(
            self.binding_frame,
            text="Portal bindings must name an existing VisBSP portal; use <omit> to clear PortalName.",
            bg="#202936",
            fg="#8296a8",
            anchor="w",
            justify="left",
        ).grid(
            row=2,
            column=0,
            sticky="ew",
            padx=8,
            pady=(2, 6),
        )
        self.binding_frame.grid()

    def _binding_values(self) -> Dict[str, str]:
        return {
            source_name: variable.get().strip()
            for source_name, variable in self._binding_vars.values()
            if variable.get().strip()
        }

    def _bindings_complete(self) -> bool:
        return bool(self._binding_vars) and all(
            variable.get().strip()
            for _source_name, variable in self._binding_vars.values()
        )

    def _behavioral_submission_ready(self) -> bool:
        analysis = self._selected_analysis
        if analysis is None:
            return False
        if analysis.behavioral_state == SupportState.BEHAVIORAL_READY:
            return True
        if analysis.behavioral_state != SupportState.ACTION_REQUIRED:
            return False
        action_codes = {
            item.code
            for item in analysis.diagnostics_for("behavioral")
            if item.severity.value == "action_required"
        }
        return (
            action_codes <= {"behavioral_external_bindings_required"}
            and self._bindings_complete()
        )

    def _sync_collision_fields(self, *_args) -> None:
        static_mode = self.import_mode_var.get() == "static"
        # Generated box BSP is intentionally unavailable until a runtime
        # compiler exists.  Keep the old fields disabled for project/UI
        # compatibility rather than silently producing preview records.
        self.thickness_entry.configure(state="disabled")
        self.segment_entry.configure(state="disabled")
        self.box_collision_radio.configure(state="disabled")

    def _sync_import_mode(self, *_args) -> None:
        info = self._selected_info
        analysis = self._selected_analysis
        mode = self.import_mode_var.get()
        static_mode = mode == "static"
        behavioral_mode = mode == "behavioral"
        resource_mode = mode == "resource"
        preview_mode = mode == "preview"
        if self._binding_vars and behavioral_mode:
            self.binding_frame.grid()
        else:
            self.binding_frame.grid_remove()
        collision_state = "normal" if static_mode else "disabled"
        for widget in (
            self.authored_collision_radio,
            *self.collision_frame.winfo_children()[1:],
        ):
            try:
                widget.configure(state=collision_state)
            except tk.TclError:
                pass
        if info is not None and (static_mode or preview_mode or resource_mode) and info.behavior_object_classes:
            self.behavior_frame.grid()
        else:
            self.behavior_frame.grid_remove()
        self._sync_collision_fields()
        self.resource_candidate_combo.configure(
            state="readonly" if resource_mode and self._selected_resource_candidates else "disabled"
        )
        if info is not None:
            self.resource_mode_radio.configure(
                state="normal" if self._selected_resource_candidates else "disabled"
            )
            self.static_mode_radio.configure(
                state="normal" if info.source_format == "compiled_dat" and bool(info.models) else "disabled"
            )
            self.preview_mode_radio.configure(
                state="normal" if info.source_format == "legacy_ed" and bool(info.models) else "disabled"
            )

        ready = False
        message = "Choose a prefab to begin."
        error = False
        if info is not None and resource_mode:
            ready = self.resource_candidate_var.get() in self._resource_candidate_by_label
            if ready:
                candidate = self._resource_candidate_by_label[self.resource_candidate_var.get()]
                message = (
                    f"Runtime-safe {candidate.target_class} import is ready using "
                    f"{candidate.model_path}."
                )
            else:
                message = "This prefab has no confident stock MM9 Prop model match."
                error = True
        elif info is not None and static_mode:
            ready = bool(info.models) and (
                analysis is None or analysis.static_state == SupportState.STATIC_READY
            ) and info.source_format == "compiled_dat"
            if ready:
                message = "Compiled runtime BSP import is ready. Review settings, then start placement."
            else:
                message = "Static BSP import requires a DEdit-compiled v66 DAT."
                error = True
        elif info is not None and preview_mode:
            ready = info.source_format == "legacy_ed" and bool(info.models)
            message = (
                "Editor preview only. This geometry can be placed and inspected, "
                "but DAT save/install is blocked until it is replaced."
            )
            error = False
        elif info is not None:
            state = analysis.behavioral_state if analysis is not None else SupportState.BLOCKED
            ready = self._behavioral_submission_ready()
            if ready:
                message = (
                    "Target bindings are filled. They will be validated against this level."
                    if state == SupportState.ACTION_REQUIRED
                    else "Full behavioral plan is ready. Review it, then start placement."
                )
            else:
                details = [] if analysis is None else [
                    item.message for item in analysis.diagnostics_for("behavioral")
                    if item.severity.value in {"blocking", "action_required"}
                ]
                message = details[0] if details else "Full behavioral import is not supported yet."
                error = True
        self.place_button.configure(state="normal" if ready else "disabled")
        self.status_var.set(message)
        self._set_status_error(error)

    def _submit(self) -> None:
        info = self._selected_info
        mode = self.import_mode_var.get()
        if info is None:
            self.status_var.set("Choose a prefab.")
            self._set_status_error(True)
            return
        if mode in {"static", "preview"} and not info.models:
            self.status_var.set("Choose a prefab with static geometry.")
            self._set_status_error(True)
            return
        if mode == "behavioral":
            if not self._behavioral_submission_ready():
                self.status_var.set("Resolve the behavioral analysis blockers before placement.")
                self._set_status_error(True)
                return
        if mode in {"static", "preview", "resource"} and info.behavior_objects and not self.behavior_ack_var.get():
            self.status_var.set(
                "Confirm that controller/resource objects and their behavior will be omitted."
            )
            self._set_status_error(True)
            return
        anchor_label = self.anchor_label_var.get()
        anchor = _ANCHOR_LABELS.get(anchor_label, "")
        try:
            request = build_import_request(
                prefab_path=self._selected_path,
                new_name=self.name_var.get(),
                collision_mode=(
                    self.collision_var.get() if mode == "static" else "none"
                ),
                collision_thickness=self.thickness_var.get(),
                collision_segment_length=self.segment_var.get(),
                placement_anchor=anchor,
                browser_root=self.root_var.get(),
                import_mode=mode,
                external_bindings=self._binding_values(),
                resource_candidate_id=(
                    self._resource_candidate_by_label[self.resource_candidate_var.get()].candidate_id
                    if self.resource_candidate_var.get() in self._resource_candidate_by_label else ""
                ),
                resource_class=(
                    self._resource_candidate_by_label[self.resource_candidate_var.get()].target_class
                    if self.resource_candidate_var.get() in self._resource_candidate_by_label else ""
                ),
                resource_model=(
                    self._resource_candidate_by_label[self.resource_candidate_var.get()].model_path
                    if self.resource_candidate_var.get() in self._resource_candidate_by_label else ""
                ),
                resource_skins=(
                    self._resource_candidate_by_label[self.resource_candidate_var.get()].skin_paths
                    if self.resource_candidate_var.get() in self._resource_candidate_by_label else ()
                ),
            )
        except ValueError as exc:
            self.status_var.set(str(exc))
            self._set_status_error(True)
            return

        self.status_var.set("Validating import against the current level...")
        self._set_status_error(False)
        self.configure(cursor="watch")
        self.place_button.configure(state="disabled")
        self.update_idletasks()
        try:
            self._validate_request(request)
        except Exception as exc:
            self.status_var.set(str(exc))
            self._set_status_error(True)
            self._sync_import_mode()
            return
        finally:
            self.configure(cursor="")
        self.result = request
        self._close()

    def _set_status_error(self, error: bool) -> None:
        self.status_label.configure(fg="#e58b8b" if error else "#9aa6b2")

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        self._inspection_generation += 1
        self._inspection_executor.shutdown(wait=False, cancel_futures=True)
        try:
            if self.grab_current() is self:
                self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    @classmethod
    def ask(
        cls,
        parent: tk.Misc,
        *,
        initial_dir: str,
        inspect_prefab: Callable[[str], PrefabInspection],
        suggest_name: Callable[[str], str],
        validate_request: Callable[[PrefabImportRequest], None],
        analyze_prefab: Optional[Callable[[str], PrefabAnalysis]] = None,
        find_resource_candidates: Optional[
            Callable[[str, PrefabInspection], Sequence[ResourceBackedCandidate]]
        ] = None,
    ) -> Optional[PrefabImportRequest]:
        workspace = cls(
            parent,
            initial_dir=initial_dir,
            inspect_prefab=inspect_prefab,
            analyze_prefab=analyze_prefab,
            find_resource_candidates=find_resource_candidates,
            suggest_name=suggest_name,
            validate_request=validate_request,
        )
        parent.wait_window(workspace)
        return workspace.result


def _format_vec(value: Sequence[float]) -> str:
    return f"({float(value[0]):.1f}, {float(value[1]):.1f}, {float(value[2]):.1f})"


def _format_counts(counts: Iterable[Tuple[str, int]] | dict) -> str:
    items = counts.items() if isinstance(counts, dict) else counts
    return ", ".join(f"{name}={count}" for name, count in sorted(items))
