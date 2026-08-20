"""Interactive graph/state editor and mock-key simulator for RUDE assets."""

from __future__ import annotations

import copy
import re
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from core import project as project_model
from core import rude
from core import rude_quest


BG = "#0e1116"
PANEL = "#1a1d22"
FIELD = "#23272d"
TEXT = "#e6e6e6"
MUTED = "#8d98a5"
ACCENT = "#3a78ad"
GOOD = "#4f8c61"
WARN = "#c78b3b"
BAD = "#b75555"


def parse_slot_values(text: str, size: int, label: str) -> Tuple[int, ...]:
    """Parse a comma/space separated fixed-size RUDE slot field."""
    parts = [part for part in re.split(r"[\s,;]+", str(text).strip()) if part]
    if len(parts) > size:
        raise ValueError(f"{label} accepts at most {size} values")
    try:
        values = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"{label} must contain integers separated by commas") from exc
    values.extend([0] * (size - len(values)))
    return tuple(values)


def parse_key_set(text: str) -> Set[int]:
    parts = [part for part in re.split(r"[\s,;]+", str(text).strip()) if part]
    try:
        values = {int(part) for part in parts}
    except ValueError as exc:
        raise ValueError("Mock party keys must be integers separated by commas") from exc
    return {value for value in values if value != 0}


def format_slot_values(values: Iterable[int]) -> str:
    return ", ".join(str(int(value)) for value in values)


def format_key_set(values: Iterable[int]) -> str:
    return ", ".join(str(value) for value in sorted(set(values)))


def action_description(action: rude.RudeAction) -> str:
    if action.kind is rude.RudeActionKind.STATE:
        return f"state {action.target_state}"
    if action.kind is rude.RudeActionKind.CLOSE:
        return "close dialogue"
    native = action.native_action
    if native is None:
        return f"unknown native action {action.value}"
    return native.name.replace("_", " ").title()


def format_validation_issue(issue: rude_quest.QuestValidationIssue) -> str:
    location = ""
    if issue.state_id is not None:
        location = f" state {issue.state_id}"
    if issue.branch_id is not None:
        location += f" branch {issue.branch_id}"
    return f"{issue.severity.value.upper():7} {issue.code}{location}: {issue.message}"


def format_key_usage(usage: rude_quest.QuestKeyUsage) -> str:
    certainty = "" if usage.certain else " [possible/parameter]"
    return (
        f"{usage.role.value}: {usage.location}{certainty}\n"
        f"    {usage.detail}"
    )


def graph_layout(
    state_ids: Sequence[int],
    *,
    columns: int = 3,
    x_spacing: int = 170,
    y_spacing: int = 120,
    margin_x: int = 90,
    margin_y: int = 70,
) -> Dict[int, Tuple[int, int]]:
    columns = max(1, int(columns))
    return {
        int(state_id): (
            margin_x + (index % columns) * x_spacing,
            margin_y + (index // columns) * y_spacing,
        )
        for index, state_id in enumerate(state_ids)
    }


class RudeEditorWindow(tk.Toplevel):
    """Edit one independently opened :class:`RudeAssetEdit`."""

    def __init__(
        self,
        parent: tk.Misc,
        project: project_model.Project,
        asset: project_model.RudeAssetEdit,
        *,
        on_changed: Optional[Callable[[project_model.RudeAssetEdit], None]] = None,
        on_open_related: Optional[Callable[[int], Optional["RudeEditorWindow"]]] = None,
        dialogue_overrides_provider: Optional[
            Callable[[], Mapping[int, rude.RudeDialogue]]
        ] = None,
    ):
        super().__init__(parent)
        self.project = project
        self.asset = asset
        self.dialogue = copy.deepcopy(asset.dialogue)
        self.on_changed = on_changed
        self.on_open_related = on_open_related
        self.dialogue_overrides_provider = dialogue_overrides_provider
        self.simulator: Optional[rude.RudeSimulator] = None
        self.quest_key_index: Optional[rude_quest.QuestKeyIndex] = None
        self.validation_issues: List[rude_quest.QuestValidationIssue] = []
        self._state_ids: List[int] = []
        self._graph_state_items: Dict[int, int] = {}
        self._loaded_choice: Optional[rude.RudeChoice] = None

        self.title(f"RUDE Dialogue & Quest Editor — NPC{asset.npc_nbr}")
        self.configure(bg=BG)
        self.geometry("1320x860")
        self.minsize(1050, 700)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._build()
        self._refresh_all(select_state=self.dialogue.metadata.initial_state)
        self.transient(parent)
        self.focus_force()

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        header = tk.Frame(self, bg=PANEL)
        header.pack(fill="x", padx=10, pady=(10, 5))

        tk.Label(
            header,
            text=f"NPC{self.asset.npc_nbr}",
            bg=PANEL,
            fg="#ffffff",
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, rowspan=2, sticky="nw", padx=(8, 16), pady=8)

        self.name_var = tk.StringVar(value=self.dialogue.metadata.name)
        self.initial_state_var = tk.StringVar(
            value=str(self.dialogue.metadata.initial_state))
        self.blurb_var = tk.StringVar(value=self.dialogue.metadata.opening_blurb)

        self._labeled_entry(header, "Display name", self.name_var, 0, 1, width=34)
        self._labeled_entry(
            header, "Initial state", self.initial_state_var, 0, 3, width=10)
        self._labeled_entry(header, "Opening blurb", self.blurb_var, 1, 1, width=72)
        header.grid_columnconfigure(2, weight=1)

        tk.Button(
            header,
            text="Apply to Project",
            command=self._apply_to_project,
            bg=ACCENT,
            fg="white",
            activebackground="#4b8ac1",
            relief="flat",
        ).grid(row=0, column=5, rowspan=2, padx=8, pady=8, sticky="ns")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=5)
        self.graph_tab = tk.Frame(self.notebook, bg=BG)
        self.sim_tab = tk.Frame(self.notebook, bg=BG)
        self.quest_tab = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.graph_tab, text="State Graph & Choices")
        self.notebook.add(self.sim_tab, text="Simulator")
        self.notebook.add(self.quest_tab, text="Quest Tools")
        self._build_graph_tab(self.graph_tab)
        self._build_simulator_tab(self.sim_tab)
        self._build_quest_tab(self.quest_tab)

        footer = tk.Frame(self, bg=PANEL)
        footer.pack(fill="x", padx=10, pady=(5, 10))
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(
            footer,
            textvariable=self.status_var,
            bg=PANEL,
            fg=MUTED,
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=8, pady=6)
        tk.Button(
            footer,
            text="Close",
            command=self._close,
            bg=FIELD,
            fg=TEXT,
            relief="flat",
        ).pack(side="right", padx=6, pady=4)

    def _labeled_entry(
        self,
        parent: tk.Misc,
        label: str,
        variable: tk.StringVar,
        row: int,
        column: int,
        *,
        width: int,
    ) -> tk.Entry:
        tk.Label(parent, text=label, bg=PANEL, fg=MUTED).grid(
            row=row, column=column, sticky="w", padx=(0, 6), pady=4)
        entry = tk.Entry(
            parent,
            textvariable=variable,
            width=width,
            bg=FIELD,
            fg=TEXT,
            insertbackground="white",
            relief="flat",
        )
        entry.grid(row=row, column=column + 1, sticky="ew", padx=(0, 14), pady=4)
        return entry

    def _build_graph_tab(self, parent: tk.Frame) -> None:
        pane = tk.PanedWindow(
            parent, orient="horizontal", bg=BG, sashwidth=5, sashrelief="flat")
        pane.pack(fill="both", expand=True)

        graph_frame = tk.LabelFrame(
            pane, text="Dialogue graph", bg=PANEL, fg=TEXT)
        editor_frame = tk.Frame(pane, bg=PANEL)
        pane.add(graph_frame, minsize=420, stretch="always")
        pane.add(editor_frame, minsize=550, stretch="always")

        self.graph_canvas = tk.Canvas(
            graph_frame,
            bg="#111821",
            highlightthickness=0,
            scrollregion=(0, 0, 1000, 1000),
        )
        gx = tk.Scrollbar(graph_frame, orient="horizontal", command=self.graph_canvas.xview)
        gy = tk.Scrollbar(graph_frame, orient="vertical", command=self.graph_canvas.yview)
        self.graph_canvas.configure(xscrollcommand=gx.set, yscrollcommand=gy.set)
        self.graph_canvas.grid(row=0, column=0, sticky="nsew")
        gy.grid(row=0, column=1, sticky="ns")
        gx.grid(row=1, column=0, sticky="ew")
        graph_frame.grid_rowconfigure(0, weight=1)
        graph_frame.grid_columnconfigure(0, weight=1)
        tk.Label(
            graph_frame,
            text=(
                "Green: main table state   Blue: selected; table column 5 is not graphed"
                if self.asset.npc_nbr in {
                    rude_quest.QUEST_NOTES_NPC_NBR,
                    rude_quest.AUTO_NOTES_NPC_NBR,
                    rude_quest.AWARDS_NPC_NBR,
                }
                else "Green: initial state   Blue: selected   Red edge: missing target"
            ),
            bg=PANEL,
            fg=MUTED,
            anchor="w",
        ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=4)

        lists = tk.Frame(editor_frame, bg=PANEL)
        lists.pack(fill="x", padx=8, pady=8)

        state_box = tk.LabelFrame(lists, text="States", bg=PANEL, fg=TEXT)
        state_box.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.state_list = tk.Listbox(
            state_box, height=8, exportselection=False,
            bg=FIELD, fg=TEXT, selectbackground=ACCENT, relief="flat")
        self.state_list.pack(fill="both", expand=True, padx=4, pady=4)
        self.state_list.bind("<<ListboxSelect>>", self._on_state_selected)
        state_buttons = tk.Frame(state_box, bg=PANEL)
        state_buttons.pack(fill="x", padx=4, pady=(0, 4))
        self._small_button(state_buttons, "+ State", self._add_state).pack(side="left")
        self._small_button(state_buttons, "Rename", self._rename_state).pack(
            side="left", padx=3)
        self._small_button(state_buttons, "Delete", self._delete_state).pack(side="left")

        choice_box = tk.LabelFrame(lists, text="Ordered choices", bg=PANEL, fg=TEXT)
        choice_box.pack(side="left", fill="both", expand=True, padx=(4, 0))
        self.choice_list = tk.Listbox(
            choice_box, height=8, exportselection=False,
            bg=FIELD, fg=TEXT, selectbackground=ACCENT, relief="flat")
        self.choice_list.pack(fill="both", expand=True, padx=4, pady=4)
        self.choice_list.bind("<<ListboxSelect>>", self._on_choice_selected)
        choice_buttons = tk.Frame(choice_box, bg=PANEL)
        choice_buttons.pack(fill="x", padx=4, pady=(0, 4))
        for label, command in (
            ("+ Choice", self._add_choice),
            ("Delete", self._delete_choice),
            ("↑", lambda: self._move_choice(-1)),
            ("↓", lambda: self._move_choice(1)),
        ):
            self._small_button(choice_buttons, label, command).pack(
                side="left", padx=(0, 3))

        form = tk.LabelFrame(
            editor_frame, text="Selected choice (all RUDE columns)", bg=PANEL, fg=TEXT)
        form.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        form.grid_columnconfigure(1, weight=1)

        self.branch_var = tk.StringVar()
        self.action_var = tk.StringVar()
        self.action_help_var = tk.StringVar()
        self.required_var = tk.StringVar()
        self.reserved_var = tk.StringVar()
        self.granted_var = tk.StringVar()
        self.forbidden_var = tk.StringVar()
        self.removed_var = tk.StringVar()

        self._form_entry(form, "Branch id", self.branch_var, 0, width=12)
        action_entry = self._form_entry(form, "Next/action", self.action_var, 1, width=12)
        action_entry.bind("<KeyRelease>", lambda _event: self._update_action_help())
        tk.Label(
            form, textvariable=self.action_help_var, bg=PANEL, fg=WARN, anchor="w") \
            .grid(row=1, column=2, sticky="w", padx=6)

        tk.Label(form, text="Player text", bg=PANEL, fg=MUTED).grid(
            row=2, column=0, sticky="nw", padx=6, pady=4)
        self.player_text = tk.Text(
            form, height=2, bg=FIELD, fg=TEXT, insertbackground="white", relief="flat")
        self.player_text.grid(row=2, column=1, columnspan=2, sticky="ew", padx=6, pady=4)

        tk.Label(form, text="NPC response", bg=PANEL, fg=MUTED).grid(
            row=3, column=0, sticky="nw", padx=6, pady=4)
        self.response_text = tk.Text(
            form, height=3, bg=FIELD, fg=TEXT, insertbackground="white", relief="flat")
        self.response_text.grid(row=3, column=1, columnspan=2, sticky="ew", padx=6, pady=4)

        slot_rows = (
            ("Required keys (5)", self.required_var),
            ("Reserved slots (4)", self.reserved_var),
            ("Granted keys/params (5)", self.granted_var),
            ("Forbidden keys (5)", self.forbidden_var),
            ("Removed keys/params (5)", self.removed_var),
        )
        for offset, (label, variable) in enumerate(slot_rows, 4):
            self._form_entry(form, label, variable, offset)

        tk.Label(
            form,
            text=(
                "Action: positive/0 = state, -1 = close, -2/-3/-4/-5/-6/-7/-8/"
                "-10/-11/-14/-15/-16 = known native actions"
            ),
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            wraplength=680,
        ).grid(row=9, column=0, columnspan=3, sticky="ew", padx=6, pady=(6, 2))
        tk.Button(
            form,
            text="Apply Choice",
            command=self._apply_choice_fields,
            bg=ACCENT,
            fg="white",
            relief="flat",
        ).grid(row=10, column=2, sticky="e", padx=6, pady=6)

    def _form_entry(
        self,
        parent: tk.Misc,
        label: str,
        variable: tk.StringVar,
        row: int,
        *,
        width: int = 45,
    ) -> tk.Entry:
        tk.Label(parent, text=label, bg=PANEL, fg=MUTED).grid(
            row=row, column=0, sticky="w", padx=6, pady=3)
        entry = tk.Entry(
            parent, textvariable=variable, width=width,
            bg=FIELD, fg=TEXT, insertbackground="white", relief="flat")
        entry.grid(row=row, column=1, sticky="ew", padx=6, pady=3)
        return entry

    def _small_button(
        self, parent: tk.Misc, label: str, command: Callable[[], None]
    ) -> tk.Button:
        return tk.Button(
            parent, text=label, command=command,
            bg=FIELD, fg=TEXT, activebackground="#343b45", relief="flat")

    def _build_simulator_tab(self, parent: tk.Frame) -> None:
        controls = tk.Frame(parent, bg=PANEL)
        controls.pack(fill="x", padx=8, pady=8)
        tk.Label(controls, text="Mock party keys", bg=PANEL, fg=MUTED).pack(
            side="left", padx=(8, 6), pady=8)
        self.mock_keys_var = tk.StringVar()
        tk.Entry(
            controls, textvariable=self.mock_keys_var, width=55,
            bg=FIELD, fg=TEXT, insertbackground="white", relief="flat") \
            .pack(side="left", fill="x", expand=True, padx=4, pady=8)
        tk.Button(
            controls, text="Set Keys & Reset", command=self._reset_simulator,
            bg=ACCENT, fg="white", relief="flat") \
            .pack(side="left", padx=8, pady=6)

        summary = tk.Frame(parent, bg=PANEL)
        summary.pack(fill="x", padx=8, pady=(0, 8))
        self.sim_state_var = tk.StringVar(value="Not started")
        self.sim_keys_display_var = tk.StringVar(value="Active keys: (none)")
        self.sim_blurb_var = tk.StringVar(value="")
        tk.Label(
            summary, textvariable=self.sim_state_var, bg=PANEL, fg="#ffffff",
            font=("Segoe UI", 11, "bold"), anchor="w") \
            .pack(fill="x", padx=8, pady=(8, 2))
        tk.Label(
            summary, textvariable=self.sim_keys_display_var,
            bg=PANEL, fg=GOOD, anchor="w") \
            .pack(fill="x", padx=8, pady=2)
        tk.Label(
            summary, textvariable=self.sim_blurb_var,
            bg=PANEL, fg=TEXT, anchor="w", justify="left", wraplength=1150) \
            .pack(fill="x", padx=8, pady=(2, 8))

        body = tk.PanedWindow(
            parent, orient="horizontal", bg=BG, sashwidth=5, sashrelief="flat")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        choices_frame = tk.LabelFrame(
            body, text="Visible choices", bg=PANEL, fg=TEXT)
        result_frame = tk.LabelFrame(
            body, text="NPC response / result", bg=PANEL, fg=TEXT)
        body.add(choices_frame, minsize=420, stretch="always")
        body.add(result_frame, minsize=420, stretch="always")

        self.sim_choice_list = tk.Listbox(
            choices_frame, exportselection=False,
            bg=FIELD, fg=TEXT, selectbackground=ACCENT, relief="flat")
        self.sim_choice_list.pack(fill="both", expand=True, padx=6, pady=6)
        self.sim_choice_list.bind("<Double-Button-1>", lambda _event: self._simulate_choice())
        tk.Button(
            choices_frame, text="Choose", command=self._simulate_choice,
            bg=ACCENT, fg="white", relief="flat") \
            .pack(anchor="e", padx=6, pady=(0, 6))

        self.sim_response = tk.Text(
            result_frame, height=10, wrap="word", state="disabled",
            bg=FIELD, fg=TEXT, relief="flat")
        self.sim_response.pack(fill="both", expand=True, padx=6, pady=6)
        self.sim_event_var = tk.StringVar(value="Reset to begin.")
        tk.Label(
            result_frame, textvariable=self.sim_event_var,
            bg=PANEL, fg=WARN, anchor="w", justify="left", wraplength=520) \
            .pack(fill="x", padx=6, pady=(0, 6))

    def _build_quest_tab(self, parent: tk.Frame) -> None:
        pane = tk.PanedWindow(
            parent, orient="vertical", bg=BG, sashwidth=5, sashrelief="flat")
        pane.pack(fill="both", expand=True, padx=8, pady=8)

        validation = tk.LabelFrame(
            pane, text="Dialogue validation", bg=PANEL, fg=TEXT)
        key_index = tk.LabelFrame(
            pane, text="Key usage across RUDE and scripts", bg=PANEL, fg=TEXT)
        authoring = tk.LabelFrame(
            pane, text="Journal and award entry", bg=PANEL, fg=TEXT)
        pane.add(validation, minsize=150, stretch="always")
        pane.add(key_index, minsize=190, stretch="always")
        pane.add(authoring, minsize=190, stretch="always")

        validation_controls = tk.Frame(validation, bg=PANEL)
        validation_controls.pack(fill="x", padx=6, pady=4)
        tk.Button(
            validation_controls,
            text="Run Validation",
            command=self._run_validation,
            bg=ACCENT,
            fg="white",
            relief="flat",
        ).pack(side="left", padx=(0, 8))
        self.validation_summary_var = tk.StringVar(value="Not run")
        tk.Label(
            validation_controls,
            textvariable=self.validation_summary_var,
            bg=PANEL,
            fg=MUTED,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self.validation_list = tk.Listbox(
            validation,
            height=5,
            exportselection=False,
            bg=FIELD,
            fg=TEXT,
            selectbackground=ACCENT,
            relief="flat",
        )
        self.validation_list.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.validation_list.bind(
            "<Double-Button-1>", self._open_validation_issue)

        key_controls = tk.Frame(key_index, bg=PANEL)
        key_controls.pack(fill="x", padx=6, pady=4)
        tk.Label(key_controls, text="Key id", bg=PANEL, fg=MUTED).pack(
            side="left", padx=(0, 5))
        self.key_query_var = tk.StringVar()
        tk.Entry(
            key_controls,
            textvariable=self.key_query_var,
            width=12,
            bg=FIELD,
            fg=TEXT,
            insertbackground="white",
            relief="flat",
        ).pack(side="left", padx=(0, 5))
        tk.Button(
            key_controls,
            text="Build / Refresh Index",
            command=self._build_quest_index,
            bg=ACCENT,
            fg="white",
            relief="flat",
        ).pack(side="left", padx=3)
        self._small_button(
            key_controls, "Find Key", self._show_key_usage).pack(side="left", padx=3)
        self._small_button(
            key_controls, "Suggest Unused", self._suggest_unused_key).pack(
                side="left", padx=3)
        self.key_index_summary_var = tk.StringVar(value="Index not built")
        tk.Label(
            key_index,
            textvariable=self.key_index_summary_var,
            bg=PANEL,
            fg=GOOD,
            anchor="w",
        ).pack(fill="x", padx=6, pady=(0, 3))
        self.key_usage_text = tk.Text(
            key_index,
            height=6,
            wrap="word",
            state="disabled",
            bg=FIELD,
            fg=TEXT,
            relief="flat",
        )
        self.key_usage_text.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        authoring.grid_columnconfigure(1, weight=1)
        self.entry_title_var = tk.StringVar()
        self.entry_required_var = tk.StringVar()
        self.entry_forbidden_var = tk.StringVar()
        self._form_entry(authoring, "Title", self.entry_title_var, 0)
        self._form_entry(
            authoring, "Required keys (up to 5)", self.entry_required_var, 1)
        self._form_entry(
            authoring, "Forbidden keys (up to 5)", self.entry_forbidden_var, 2)
        tk.Label(authoring, text="Quest-note detail", bg=PANEL, fg=MUTED).grid(
            row=3, column=0, sticky="nw", padx=6, pady=3)
        self.entry_body_text = tk.Text(
            authoring,
            height=3,
            bg=FIELD,
            fg=TEXT,
            insertbackground="white",
            relief="flat",
        )
        self.entry_body_text.grid(
            row=3, column=1, sticky="ew", padx=6, pady=3)
        entry_buttons = tk.Frame(authoring, bg=PANEL)
        entry_buttons.grid(row=4, column=1, sticky="e", padx=6, pady=5)
        tk.Button(
            entry_buttons,
            text="Add Quest Note to NPC997",
            command=lambda: self._add_authored_entry(
                rude_quest.QUEST_NOTES_NPC_NBR),
            bg=ACCENT,
            fg="white",
            relief="flat",
        ).pack(side="left", padx=3)
        tk.Button(
            entry_buttons,
            text="Add Award to NPC999",
            command=lambda: self._add_authored_entry(rude_quest.AWARDS_NPC_NBR),
            bg="#6f5a32",
            fg="white",
            relief="flat",
        ).pack(side="left", padx=3)
        tk.Label(
            authoring,
            text=(
                "Entries are added to the related asset's working editor. "
                "Apply that asset to the project before File → Save."
            ),
            bg=PANEL,
            fg=MUTED,
            anchor="w",
        ).grid(row=5, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 5))

    # -------------------------------------------------------------- selections

    def _selected_state_id(self) -> Optional[int]:
        selection = self.state_list.curselection()
        if not selection:
            return None
        index = int(selection[0])
        return self._state_ids[index] if index < len(self._state_ids) else None

    def _selected_choice_index(self) -> Optional[int]:
        selection = self.choice_list.curselection()
        if not selection:
            return None
        return int(selection[0])

    def _select_state_id(self, state_id: int, choice_index: int = 0) -> None:
        if state_id not in self._state_ids:
            return
        if (
            self._loaded_choice is not None
            and self._loaded_choice.state_id != state_id
            and not self._apply_choice_fields(refresh=False)
        ):
            self._restore_loaded_selection()
            return
        index = self._state_ids.index(state_id)
        self.state_list.selection_clear(0, "end")
        self.state_list.selection_set(index)
        self.state_list.see(index)
        self._refresh_choices(state_id, select_index=choice_index)
        self._draw_graph(selected_state=state_id)

    def _on_state_selected(self, _event=None) -> None:
        state_id = self._selected_state_id()
        if state_id is None:
            return
        if (
            self._loaded_choice is not None
            and self._loaded_choice.state_id != state_id
            and not self._apply_choice_fields(refresh=False)
        ):
            self._restore_loaded_selection()
            return
        self._refresh_choices(state_id, select_index=0)
        self._draw_graph(selected_state=state_id)

    def _on_choice_selected(self, _event=None) -> None:
        state_id = self._selected_state_id()
        choice_index = self._selected_choice_index()
        if state_id is None or choice_index is None:
            return
        state = self.dialogue.state(state_id)
        if state is None or choice_index >= len(state.choices):
            return
        target = state.choices[choice_index]
        if target is self._loaded_choice:
            return
        if not self._apply_choice_fields(refresh=False):
            self._restore_loaded_selection()
            return
        self._refresh_choices(state_id, select_index=choice_index)
        self._draw_graph(selected_state=state_id)

    def _restore_loaded_selection(self) -> None:
        choice = self._loaded_choice
        if choice is None or choice.state_id not in self._state_ids:
            return
        state = self.dialogue.state(choice.state_id)
        if state is None:
            return
        try:
            choice_index = next(
                index for index, candidate in enumerate(state.choices)
                if candidate is choice
            )
        except StopIteration:
            return
        state_index = self._state_ids.index(choice.state_id)
        self.state_list.selection_clear(0, "end")
        self.state_list.selection_set(state_index)
        self.choice_list.selection_clear(0, "end")
        self.choice_list.selection_set(choice_index)

    def _refresh_all(
        self,
        *,
        select_state: Optional[int] = None,
        select_choice: int = 0,
    ) -> None:
        self._state_ids = [state.state_id for state in self.dialogue.states]
        self.state_list.delete(0, "end")
        for state in self.dialogue.states:
            marker = " ★" if state.state_id == self.dialogue.metadata.initial_state else ""
            self.state_list.insert(
                "end", f"State {state.state_id}{marker}  ({len(state.choices)} choices)")
        if not self._state_ids:
            self.choice_list.delete(0, "end")
            self._clear_choice_form()
            self._draw_graph()
            return
        if select_state not in self._state_ids:
            select_state = self._state_ids[0]
        self._select_state_id(int(select_state), select_choice)

    def _refresh_choices(self, state_id: int, *, select_index: int = 0) -> None:
        self.choice_list.delete(0, "end")
        state = self.dialogue.state(state_id)
        if state is None:
            self._clear_choice_form()
            return
        for index, choice in enumerate(state.choices, 1):
            prompt = choice.player_text.replace("\r", " ").replace("\n", " ")
            if len(prompt) > 46:
                prompt = prompt[:43] + "..."
            self.choice_list.insert(
                "end",
                f"{index}. [{choice.branch_id}] {prompt}  →  "
                f"{action_description(choice.action)}",
            )
        if state.choices:
            select_index = max(0, min(select_index, len(state.choices) - 1))
            self.choice_list.selection_set(select_index)
            self.choice_list.see(select_index)
            self._load_choice(state.choices[select_index])
        else:
            self._clear_choice_form()

    def _load_choice(self, choice: rude.RudeChoice) -> None:
        self._loaded_choice = choice
        self.branch_var.set(str(choice.branch_id))
        self.action_var.set(str(choice.action.value))
        self.player_text.delete("1.0", "end")
        self.player_text.insert("1.0", choice.player_text)
        self.response_text.delete("1.0", "end")
        self.response_text.insert("1.0", choice.npc_response)
        self.required_var.set(format_slot_values(choice.conditions.required))
        self.reserved_var.set(format_slot_values(choice.conditions.reserved))
        self.granted_var.set(format_slot_values(choice.effects.granted))
        self.forbidden_var.set(format_slot_values(choice.conditions.forbidden))
        self.removed_var.set(format_slot_values(choice.effects.removed))
        self._update_action_help()

    def _clear_choice_form(self) -> None:
        self._loaded_choice = None
        for variable in (
            self.branch_var,
            self.action_var,
            self.required_var,
            self.reserved_var,
            self.granted_var,
            self.forbidden_var,
            self.removed_var,
        ):
            variable.set("")
        self.player_text.delete("1.0", "end")
        self.response_text.delete("1.0", "end")
        self.action_help_var.set("")

    # --------------------------------------------------------------- mutations

    def _apply_choice_fields(self, *, refresh: bool = True) -> bool:
        choice = self._loaded_choice
        if choice is None:
            return True
        state_id = choice.state_id
        state = self.dialogue.state(state_id)
        if state is None:
            return True
        try:
            choice_index = next(
                index for index, candidate in enumerate(state.choices)
                if candidate is choice
            )
        except StopIteration:
            return True
        try:
            branch_id = int(self.branch_var.get().strip())
            action = rude.RudeAction(int(self.action_var.get().strip()))
            player_text = self.player_text.get("1.0", "end-1c")
            npc_response = self.response_text.get("1.0", "end-1c")
            if len(player_text.encode("latin-1")) > 127:
                raise ValueError("Player text exceeds the runtime limit of 127 Latin-1 bytes")
            if len(npc_response.encode("latin-1")) > 255:
                raise ValueError("NPC response exceeds the runtime limit of 255 Latin-1 bytes")
            conditions = rude.RudeKeyConditions(
                required=parse_slot_values(self.required_var.get(), 5, "Required keys"),
                reserved=parse_slot_values(self.reserved_var.get(), 4, "Reserved slots"),
                forbidden=parse_slot_values(self.forbidden_var.get(), 5, "Forbidden keys"),
            )
            effects = rude.RudeKeyEffects(
                granted=parse_slot_values(self.granted_var.get(), 5, "Granted keys"),
                removed=parse_slot_values(self.removed_var.get(), 5, "Removed keys"),
            )
        except (ValueError, UnicodeEncodeError) as exc:
            messagebox.showerror("Invalid RUDE choice", str(exc), parent=self)
            return False

        changed = (
            choice.branch_id != branch_id
            or choice.player_text != player_text
            or choice.npc_response != npc_response
            or choice.action != action
            or choice.conditions != conditions
            or choice.effects != effects
        )
        choice.branch_id = branch_id
        choice.player_text = player_text
        choice.npc_response = npc_response
        choice.action = action
        choice.conditions = conditions
        choice.effects = effects
        if changed:
            self._invalidate_quest_analysis()
        if refresh:
            self._refresh_choices(state_id, select_index=choice_index)
            self._draw_graph(selected_state=state_id)
        self.status_var.set(f"Applied branch {branch_id} to working dialogue")
        return True

    def _apply_metadata_fields(self) -> bool:
        try:
            name = self.name_var.get().strip()
            if not name:
                raise ValueError("Display name is required")
            name.encode("latin-1")
            opening_blurb = self.blurb_var.get()
            opening_blurb.encode("latin-1")
            initial_state = int(self.initial_state_var.get().strip())
            if self.dialogue.states and self.dialogue.state(initial_state) is None:
                raise ValueError(f"Initial state {initial_state} does not exist")
        except (ValueError, UnicodeEncodeError) as exc:
            messagebox.showerror("Invalid RUDE metadata", str(exc), parent=self)
            return False
        changed = (
            self.dialogue.metadata.name != name
            or self.dialogue.metadata.initial_state != initial_state
            or self.dialogue.metadata.opening_blurb != opening_blurb
        )
        self.dialogue.metadata.name = name
        self.dialogue.metadata.initial_state = initial_state
        self.dialogue.metadata.opening_blurb = opening_blurb
        if changed:
            self._invalidate_quest_analysis()
        return True

    def _apply_to_project(self) -> bool:
        if not self._apply_choice_fields() or not self._apply_metadata_fields():
            return False
        try:
            self.dialogue.to_bytes()
            self.asset.dialogue = copy.deepcopy(self.dialogue)
            self.asset.validate_identity()
        except Exception as exc:
            messagebox.showerror("RUDE asset failed", str(exc), parent=self)
            return False
        if self.on_changed is not None:
            self.on_changed(self.asset)
        self._refresh_all(select_state=self.dialogue.metadata.initial_state)
        self.status_var.set(
            f"Applied NPC{self.asset.npc_nbr} to project; use File → Save to write RUDE.REZ")
        return True

    def _add_state(self) -> None:
        if not self._apply_choice_fields(refresh=False):
            return
        value = simpledialog.askinteger(
            "Add state", "New state id:", parent=self, minvalue=0)
        if value is None:
            return
        if self.dialogue.state(value) is not None:
            messagebox.showerror("State exists", f"State {value} already exists", parent=self)
            return
        self.dialogue.append_choice(rude.RudeChoice(
            npc_nbr=self.asset.npc_nbr,
            state_id=value,
            branch_id=1,
            player_text="Goodbye.",
            npc_response="Farewell.",
            action=rude.RudeAction.close(),
        ))
        self._invalidate_quest_analysis()
        self._refresh_all(select_state=value)
        self.status_var.set(f"Added state {value} with a closing choice")

    def _rename_state(self) -> None:
        old_state = self._selected_state_id()
        if old_state is None:
            return
        if not self._apply_choice_fields(refresh=False):
            return
        value = simpledialog.askinteger(
            "Rename state", "New state id:", initialvalue=old_state,
            parent=self, minvalue=0)
        if value is None:
            return
        try:
            self.dialogue.rename_state(old_state, value, update_inbound_actions=True)
        except (KeyError, ValueError) as exc:
            messagebox.showerror("Rename failed", str(exc), parent=self)
            return
        self._invalidate_quest_analysis()
        self.initial_state_var.set(str(self.dialogue.metadata.initial_state))
        self._refresh_all(select_state=value)
        self.status_var.set(f"Renamed state {old_state} to {value}; inbound edges updated")

    def _delete_state(self) -> None:
        state_id = self._selected_state_id()
        if state_id is None:
            return
        if not messagebox.askyesno(
            "Delete state",
            f"Delete state {state_id} and all of its choices?\n\n"
            "Inbound transitions will remain as visible missing-target edges.",
            parent=self,
        ):
            return
        self.dialogue.remove_state(state_id)
        self._invalidate_quest_analysis()
        states = self.dialogue.states
        if states and self.dialogue.metadata.initial_state == state_id:
            self.dialogue.metadata.initial_state = states[0].state_id
            self.initial_state_var.set(str(states[0].state_id))
        self._refresh_all()
        self.status_var.set(f"Deleted state {state_id}")

    def _add_choice(self) -> None:
        state_id = self._selected_state_id()
        if state_id is None:
            messagebox.showinfo("No state", "Add a state first.", parent=self)
            return
        if not self._apply_choice_fields(refresh=False):
            return
        branch_id = self.dialogue.next_branch_id(state_id)
        self.dialogue.append_choice(rude.RudeChoice(
            npc_nbr=self.asset.npc_nbr,
            state_id=state_id,
            branch_id=branch_id,
            player_text="New choice",
            npc_response="New response",
            action=rude.RudeAction.state(state_id),
        ))
        self._invalidate_quest_analysis()
        state = self.dialogue.state(state_id)
        self._refresh_all(select_state=state_id, select_choice=len(state.choices) - 1)
        self.status_var.set(f"Added branch {branch_id} to state {state_id}")

    def _delete_choice(self) -> None:
        state_id = self._selected_state_id()
        choice_index = self._selected_choice_index()
        if state_id is None or choice_index is None:
            return
        state = self.dialogue.state(state_id)
        if state is None:
            return
        branch = state.choices[choice_index].branch_id
        if not messagebox.askyesno(
                "Delete choice", f"Delete branch {branch}?", parent=self):
            return
        self.dialogue.remove_choice(state_id, choice_index)
        self._invalidate_quest_analysis()
        self._refresh_all(select_state=state_id, select_choice=max(0, choice_index - 1))
        self.status_var.set(f"Deleted branch {branch} from state {state_id}")

    def _move_choice(self, delta: int) -> None:
        state_id = self._selected_state_id()
        choice_index = self._selected_choice_index()
        if state_id is None or choice_index is None:
            return
        if not self._apply_choice_fields(refresh=False):
            return
        state = self.dialogue.state(state_id)
        if state is None:
            return
        target = choice_index + int(delta)
        if target < 0 or target >= len(state.choices):
            return
        self.dialogue.reorder_choice(state_id, choice_index, target)
        self._invalidate_quest_analysis()
        self._refresh_all(select_state=state_id, select_choice=target)
        self.status_var.set(f"Reordered choices in state {state_id}")

    # ------------------------------------------------------------------ graph

    def _draw_graph(self, selected_state: Optional[int] = None) -> None:
        canvas = self.graph_canvas
        canvas.delete("all")
        states = self.dialogue.states
        state_ids = [state.state_id for state in states]
        positions = graph_layout(state_ids)
        node_w, node_h = 126, 58
        is_special_table = self.asset.npc_nbr in {
            rude_quest.QUEST_NOTES_NPC_NBR,
            rude_quest.AUTO_NOTES_NPC_NBR,
            rude_quest.AWARDS_NPC_NBR,
        }

        for edge in (() if is_special_table else self.dialogue.graph_edges):
            if edge.action.kind is not rude.RudeActionKind.STATE:
                continue
            source = positions.get(edge.source_state)
            target = positions.get(edge.target_state)
            if source is None:
                continue
            if target is None:
                canvas.create_line(
                    source[0], source[1], source[0] + 70, source[1] + 45,
                    fill=BAD, width=2, arrow="last")
                canvas.create_text(
                    source[0] + 84, source[1] + 50,
                    text=f"missing {edge.target_state}", fill=BAD, anchor="w")
            elif edge.source_state == edge.target_state:
                x, y = source
                canvas.create_line(
                    x + node_w // 2, y,
                    x + node_w // 2 + 35, y - 38,
                    x, y - node_h // 2 - 24,
                    x - node_w // 2, y,
                    smooth=True, fill="#7992ad", arrow="last")
            else:
                canvas.create_line(
                    source[0], source[1], target[0], target[1],
                    fill="#62768d", width=2, arrow="last")

        self._graph_state_items.clear()
        for state in states:
            x, y = positions[state.state_id]
            fill = FIELD
            outline = "#607080"
            if state.state_id == self.dialogue.metadata.initial_state:
                fill, outline = "#294a34", GOOD
            if state.state_id == selected_state:
                fill, outline = "#244d70", "#62a7dd"
            rect = canvas.create_rectangle(
                x - node_w // 2,
                y - node_h // 2,
                x + node_w // 2,
                y + node_h // 2,
                fill=fill,
                outline=outline,
                width=2,
            )
            if is_special_table:
                label = f"Table state {state.state_id}\n{len(state.choices)} row(s)"
            else:
                terminal_count = sum(
                    choice.action.kind is not rude.RudeActionKind.STATE
                    for choice in state.choices
                )
                label = f"State {state.state_id}\n{len(state.choices)} choice(s)"
                if terminal_count:
                    label += f" · {terminal_count} terminal"
            text_item = canvas.create_text(
                x, y, text=label, fill=TEXT, justify="center")
            for item in (rect, text_item):
                canvas.tag_bind(
                    item,
                    "<Button-1>",
                    lambda _event, sid=state.state_id: self._select_state_id(sid),
                )
            self._graph_state_items[state.state_id] = rect

        if positions:
            max_x = max(x for x, _y in positions.values()) + 120
            max_y = max(y for _x, y in positions.values()) + 100
            canvas.configure(scrollregion=(0, 0, max(520, max_x), max(420, max_y)))
        else:
            canvas.create_text(
                220, 120,
                text="No states. Use + State to create the first node.",
                fill=MUTED,
            )

    def _update_action_help(self) -> None:
        try:
            action = rude.RudeAction(int(self.action_var.get().strip()))
            self.action_help_var.set(action_description(action))
        except ValueError:
            self.action_help_var.set("invalid action")

    # ------------------------------------------------------------- quest tools

    def _invalidate_quest_analysis(self) -> None:
        self.quest_key_index = None
        self.validation_issues = []
        if hasattr(self, "validation_summary_var"):
            self.validation_summary_var.set("Out of date — run validation")
        if hasattr(self, "validation_list"):
            self.validation_list.delete(0, "end")
            self.validation_list.insert("end", "Dialogue changed; run validation again.")
        if hasattr(self, "key_index_summary_var"):
            self.key_index_summary_var.set("Out of date — rebuild index")
        if hasattr(self, "key_usage_text"):
            self._set_key_usage_text("Dialogue changed; rebuild the key index.")

    def _run_validation(self) -> None:
        if not self._apply_choice_fields() or not self._apply_metadata_fields():
            return
        report = rude_quest.validate_dialogue(self.dialogue)
        self.validation_issues = list(report.issues)
        self.validation_list.delete(0, "end")
        for issue in self.validation_issues:
            self.validation_list.insert("end", format_validation_issue(issue))
        if not self.validation_issues:
            self.validation_list.insert("end", "OK — no validation issues")
        info_count = sum(
            issue.severity is rude_quest.QuestIssueSeverity.INFO
            for issue in report.issues
        )
        self.validation_summary_var.set(
            f"{len(report.errors)} error(s), {len(report.warnings)} warning(s), "
            f"{info_count} info; {len(report.reachable_states)} reachable, "
            f"{len(report.unreachable_states)} unreachable state(s)"
        )
        self.status_var.set(
            f"Validated NPC{self.asset.npc_nbr}: "
            f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)")

    def _open_validation_issue(self, _event=None) -> None:
        selection = self.validation_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        if index >= len(self.validation_issues):
            return
        issue = self.validation_issues[index]
        if issue.state_id is None or self.dialogue.state(issue.state_id) is None:
            return
        state = self.dialogue.state(issue.state_id)
        choice_index = 0
        if issue.branch_id is not None:
            choice_index = next(
                (
                    position
                    for position, choice in enumerate(state.choices)
                    if choice.branch_id == issue.branch_id
                ),
                0,
            )
        self.notebook.select(self.graph_tab)
        self._refresh_all(
            select_state=issue.state_id,
            select_choice=choice_index,
        )

    def _dialogue_overrides(self) -> Dict[int, rude.RudeDialogue]:
        overrides = {
            int(npc_nbr): asset.dialogue
            for npc_nbr, asset in self.project.rude_assets.items()
        }
        if self.dialogue_overrides_provider is not None:
            try:
                overrides.update({
                    int(npc_nbr): dialogue
                    for npc_nbr, dialogue
                    in self.dialogue_overrides_provider().items()
                })
            except Exception:
                pass
        overrides[self.asset.npc_nbr] = self.dialogue
        return overrides

    def _script_overrides(self) -> Dict[str, str]:
        scripts: Dict[str, str] = {}
        for asset in self.project.dialogue_script_assets.values():
            try:
                scripts[asset.integration.virtual_path] = asset.integration.render()
            except Exception:
                # Invalid working script assets cannot be applied to the project,
                # but older project files should not make the quest index unusable.
                continue
        for level in self.project.levels:
            try:
                operations = level.effective_ops()
            except Exception:
                operations = ()
            for operation in operations:
                for path, text in dict(
                        getattr(operation, "script_assets", {}) or {}).items():
                    scripts[str(path)] = str(text)
        return scripts

    def _build_quest_index(self) -> None:
        if not self._apply_choice_fields() or not self._apply_metadata_fields():
            return
        self.key_index_summary_var.set("Scanning RUDE.REZ and SCRIPTS.REZ...")
        self.update_idletasks()
        try:
            self.quest_key_index = rude_quest.build_quest_key_index(
                self.project.rude_rez_path,
                self.project.scripts_rez_path,
                dialogue_overrides=self._dialogue_overrides(),
                script_overrides=self._script_overrides(),
            )
        except Exception as exc:
            messagebox.showerror("Key index failed", str(exc), parent=self)
            self.key_index_summary_var.set("Index failed")
            return
        index = self.quest_key_index
        self.key_index_summary_var.set(
            f"{len(index.used_keys)} key value(s), {index.usage_count} usage(s); "
            f"{index.rude_resource_count} RUDE and "
            f"{index.script_resource_count} script resource(s); "
            f"{len(index.unresolved_script_usages)} dynamic script operand(s)"
        )
        if self.key_query_var.get().strip():
            self._show_key_usage()
        else:
            lines = [
                "Enter a key id and choose Find Key.",
                "With an empty key field, Suggest Unused starts immediately "
                "above the largest resolved value.",
            ]
            if index.scan_warnings:
                lines.append("Scan warnings:")
                lines.extend(f"  {warning}" for warning in index.scan_warnings[:10])
            if index.unresolved_script_usages:
                lines.append(
                    "Dynamic script operands are listed separately because their "
                    "runtime key cannot be proven statically."
                )
                lines.extend(
                    f"  {item.source}:{item.line_number} "
                    f"{item.role.value} {item.operand}"
                    for item in index.unresolved_script_usages[:10]
                )
            self._set_key_usage_text("\n".join(lines))

    def _set_key_usage_text(self, value: str) -> None:
        self.key_usage_text.configure(state="normal")
        self.key_usage_text.delete("1.0", "end")
        self.key_usage_text.insert("1.0", value)
        self.key_usage_text.configure(state="disabled")

    def _show_key_usage(self) -> None:
        if self.quest_key_index is None:
            self._build_quest_index()
            return
        try:
            key_id = int(self.key_query_var.get().strip())
        except ValueError:
            messagebox.showerror(
                "Invalid key", "Enter one integer key id.", parent=self)
            return
        usages = self.quest_key_index.usage_for(key_id)
        if not usages:
            self._set_key_usage_text(
                f"Key {key_id} has no resolved RUDE or script usage in this index.\n"
                "Dynamic script operands may still resolve to this value at runtime."
            )
            return
        self._set_key_usage_text(
            f"Key {key_id}: {len(usages)} indexed usage(s)\n\n"
            + "\n".join(format_key_usage(usage) for usage in usages)
        )

    def _suggest_unused_key(self) -> None:
        if self.quest_key_index is None:
            self._build_quest_index()
            if self.quest_key_index is None:
                return
        query = self.key_query_var.get().strip()
        if query:
            try:
                start = int(query)
            except ValueError:
                messagebox.showerror(
                    "Invalid key", "Enter one integer starting key.", parent=self)
                return
        else:
            start = max(self.quest_key_index.used_keys, default=0) + 1
        key_id = self.quest_key_index.next_unused(start)
        self.key_query_var.set(str(key_id))
        self.entry_required_var.set(str(key_id))
        self._show_key_usage()

    def _add_authored_entry(self, target_npc_nbr: int) -> None:
        if not self._apply_choice_fields() or not self._apply_metadata_fields():
            return
        try:
            title = self.entry_title_var.get().strip()
            body = self.entry_body_text.get("1.0", "end-1c")
            required = parse_slot_values(
                self.entry_required_var.get(), 5, "Required keys")
            forbidden = parse_slot_values(
                self.entry_forbidden_var.get(), 5, "Forbidden keys")
            if not title:
                raise ValueError("Entry title is required")
            if target_npc_nbr == rude_quest.AWARDS_NPC_NBR and not any(required):
                raise ValueError("An award entry requires at least one visibility key")
            if not any(required) and not any(forbidden):
                raise ValueError(
                    "A quest note requires a required or forbidden visibility key")
            overlap = sorted((set(required) & set(forbidden)) - {0})
            if overlap:
                raise ValueError(
                    "Entry keys cannot be both required and forbidden: "
                    + ", ".join(str(value) for value in overlap))
            if len(title.encode("latin-1")) > 127:
                raise ValueError(
                    "Entry title exceeds the runtime limit of 127 Latin-1 bytes")
            if (
                target_npc_nbr == rude_quest.QUEST_NOTES_NPC_NBR
                and len(body.encode("latin-1")) > 255
            ):
                raise ValueError(
                    "Entry body exceeds the runtime limit of 255 Latin-1 bytes")
        except (ValueError, UnicodeEncodeError) as exc:
            messagebox.showerror("Invalid special entry", str(exc), parent=self)
            return

        target_window: Optional[RudeEditorWindow] = None
        if target_npc_nbr == self.asset.npc_nbr:
            target_window = self
        elif self.on_open_related is not None:
            target_window = self.on_open_related(target_npc_nbr)

        try:
            if target_window is not None:
                target_window.append_special_entry_from_tool(
                    title=title,
                    body=body,
                    required_keys=required,
                    forbidden_keys=forbidden,
                )
            else:
                asset = self.project.open_rude_asset(target_npc_nbr)
                if target_npc_nbr == rude_quest.QUEST_NOTES_NPC_NBR:
                    rude_quest.append_quest_note(
                        asset.dialogue, title, body, required, forbidden)
                else:
                    rude_quest.append_award(
                        asset.dialogue, title, required, forbidden)
                asset.validate_identity()
                if self.on_changed is not None:
                    self.on_changed(asset)
        except Exception as exc:
            messagebox.showerror("Cannot add special entry", str(exc), parent=self)
            return

        self.entry_title_var.set("")
        self.entry_body_text.delete("1.0", "end")
        self.quest_key_index = None
        self.key_index_summary_var.set("Out of date — journal/award asset changed")
        self._set_key_usage_text("A related RUDE asset changed; rebuild the key index.")
        label = "quest note" if target_npc_nbr == 997 else "award"
        self.status_var.set(f"Added {label} to NPC{target_npc_nbr} working dialogue")

    def append_special_entry_from_tool(
        self,
        *,
        title: str,
        body: str,
        required_keys: Iterable[int],
        forbidden_keys: Iterable[int],
    ) -> rude.RudeChoice:
        if not self._apply_choice_fields() or not self._apply_metadata_fields():
            raise ValueError("the selected special-table row contains invalid edits")
        if self.asset.npc_nbr == rude_quest.QUEST_NOTES_NPC_NBR:
            choice = rude_quest.append_quest_note(
                self.dialogue,
                title,
                body,
                required_keys,
                forbidden_keys,
            )
        elif self.asset.npc_nbr == rude_quest.AWARDS_NPC_NBR:
            choice = rude_quest.append_award(
                self.dialogue,
                title,
                required_keys,
                forbidden_keys,
            )
        else:
            raise ValueError("related editor is not NPC997 or NPC999")
        state = self.dialogue.state(choice.state_id)
        choice_index = next(
            index for index, candidate in enumerate(state.choices)
            if candidate is choice
        )
        self._invalidate_quest_analysis()
        self._refresh_all(
            select_state=choice.state_id,
            select_choice=choice_index,
        )
        self.notebook.select(self.graph_tab)
        self.status_var.set(
            f"Added branch {choice.branch_id} to NPC{self.asset.npc_nbr} working dialogue")
        self.lift()
        self.focus_force()
        return choice

    # --------------------------------------------------------------- simulator

    def _reset_simulator(self) -> None:
        if not self._apply_choice_fields() or not self._apply_metadata_fields():
            return
        try:
            keys = parse_key_set(self.mock_keys_var.get())
            self.simulator = rude.RudeSimulator(self.dialogue, keys)
        except ValueError as exc:
            messagebox.showerror("Invalid mock keys", str(exc), parent=self)
            return
        self.sim_response.config(state="normal")
        self.sim_response.delete("1.0", "end")
        self.sim_response.config(state="disabled")
        self.sim_event_var.set("Simulation reset.")
        self._render_simulator(show_blurb=True)

    def _render_simulator(self, *, show_blurb: bool = False) -> None:
        simulator = self.simulator
        self.sim_choice_list.delete(0, "end")
        if simulator is None:
            return
        self.mock_keys_var.set(format_key_set(simulator.active_keys))
        keys = format_key_set(simulator.active_keys) or "(none)"
        self.sim_keys_display_var.set(f"Active keys: {keys}")
        if simulator.terminal:
            self.sim_state_var.set("Terminal action")
            return
        state_id = simulator.current_state
        state = self.dialogue.state(state_id) if state_id is not None else None
        if state is None:
            self.sim_state_var.set(f"Missing state {state_id}")
            self.sim_event_var.set(
                f"The dialogue targets state {state_id}, but that state has no rows.")
            return
        self.sim_state_var.set(f"State {state_id}")
        self.sim_blurb_var.set(
            self.dialogue.metadata.opening_blurb if show_blurb else "")
        choices = simulator.available_choices
        for index, choice in enumerate(choices, 1):
            self.sim_choice_list.insert(
                "end", f"{index}. [{choice.branch_id}] {choice.player_text}")
        if choices:
            self.sim_choice_list.selection_set(0)
        else:
            self.sim_event_var.set(
                "No choices are visible for the current mock party key set.")

    def _simulate_choice(self) -> None:
        if self.simulator is None:
            self._reset_simulator()
            return
        selection = self.sim_choice_list.curselection()
        if not selection:
            return
        try:
            result = self.simulator.choose(int(selection[0]))
        except rude.RudeSimulationError as exc:
            messagebox.showerror("Simulation failed", str(exc), parent=self)
            return
        self.sim_response.config(state="normal")
        self.sim_response.delete("1.0", "end")
        self.sim_response.insert("1.0", result.response)
        self.sim_response.config(state="disabled")
        effects = []
        if result.granted_keys:
            effects.append("+keys " + format_key_set(result.granted_keys))
        if result.removed_keys:
            effects.append("-keys " + format_key_set(result.removed_keys))
        effect_text = ("; " + ", ".join(effects)) if effects else ""
        self.sim_event_var.set(
            f"Branch {result.choice.branch_id}: "
            f"{action_description(result.action)}{effect_text}")
        self._render_simulator()

    # ----------------------------------------------------------------- closing

    def _working_signature(self, dialogue: rude.RudeDialogue) -> Tuple[object, ...]:
        metadata = dialogue.metadata
        return (
            metadata.npc_nbr,
            metadata.name,
            metadata.initial_state,
            metadata.opening_blurb,
            dialogue.to_text(),
        )

    def _close(self) -> None:
        if not self._apply_choice_fields(refresh=False):
            return
        if self._working_signature(self.dialogue) != self._working_signature(
                self.asset.dialogue):
            answer = messagebox.askyesnocancel(
                "Unapplied dialogue changes",
                "Apply the working dialogue to the project before closing?",
                parent=self,
            )
            if answer is None:
                return
            if answer and not self._apply_to_project():
                return
        self.destroy()


__all__ = [
    "RudeEditorWindow",
    "action_description",
    "format_key_usage",
    "format_key_set",
    "format_slot_values",
    "format_validation_issue",
    "graph_layout",
    "parse_key_set",
    "parse_slot_values",
]
