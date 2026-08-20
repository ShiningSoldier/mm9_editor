"""Editor for project-owned RUDE OnRudeExit script integrations."""

from __future__ import annotations

import copy
import re
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from core import project as project_model
from core import rude_script


BG = "#0e1116"
PANEL = "#1a1d22"
FIELD = "#23272d"
TEXT = "#e6e6e6"
MUTED = "#8d98a5"
ACCENT = "#3a78ad"
GOOD = "#4f8c61"
WARN = "#c78b3b"


def parse_item_ids(text: str) -> Tuple[int, ...]:
    parts = [part for part in re.split(r"[\s,;]+", str(text).strip()) if part]
    try:
        values = tuple(int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("Reward item ids must be integers separated by commas") from exc
    if any(value <= 0 for value in values):
        raise ValueError("Reward item ids must be positive")
    return values


def format_item_ids(values: Iterable[int]) -> str:
    return ", ".join(str(int(value)) for value in values)


def parse_world_changes(text: str) -> List[rude_script.ScriptWorldChange]:
    changes = []
    for line_number, raw_line in enumerate(str(text).splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        if "," in line:
            object_name, message = line.split(",", 1)
        else:
            parts = line.split(None, 1)
            object_name = parts[0]
            message = parts[1] if len(parts) == 2 else "trigger"
        change = rude_script.ScriptWorldChange(
            object_name=object_name.strip(),
            message=message.strip(),
        )
        try:
            change.validate()
        except ValueError as exc:
            raise ValueError(f"World change line {line_number}: {exc}") from exc
        changes.append(change)
    return changes


def format_world_changes(values: Sequence[rude_script.ScriptWorldChange]) -> str:
    return "\n".join(f"{item.object_name}, {item.message}" for item in values)


class RudeScriptEditorWindow(tk.Toplevel):
    """Author rewards/sounds/world messages behind ordered completion keys."""

    def __init__(
        self,
        parent: tk.Misc,
        project: project_model.Project,
        npc_nbr: int,
        *,
        on_changed: Optional[Callable[[rude_script.DialogueScriptAssetEdit], None]] = None,
        on_attach: Optional[
            Callable[[rude_script.DialogueScriptAssetEdit], bool]
        ] = None,
    ):
        super().__init__(parent)
        self.project = project
        self.npc_nbr = int(npc_nbr)
        self.on_changed = on_changed
        self.on_attach = on_attach
        asset = project.dialogue_script_assets.get(self.npc_nbr)
        self.integration = copy.deepcopy(
            asset.integration if asset is not None
            else rude_script.DialogueScriptIntegration(npc_nbr=self.npc_nbr)
        )
        self._asset_existed = asset is not None
        self._loaded_hook_index: Optional[int] = None

        self.title(f"Dialogue Script Integration — NPC{self.npc_nbr}")
        self.configure(bg=BG)
        self.geometry("1180x830")
        self.minsize(980, 680)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._build()
        self._refresh_hooks(select_index=0)
        self._refresh_preview()
        self.transient(parent)
        self.focus_force()

    def _build(self) -> None:
        header = tk.LabelFrame(
            self, text="Independent script resource", bg=PANEL, fg=TEXT)
        header.pack(fill="x", padx=10, pady=(10, 5))
        header.grid_columnconfigure(1, weight=1)

        self.virtual_path_var = tk.StringVar(value=self.integration.virtual_path)
        self.base_path_var = tk.StringVar(value=self.integration.base_virtual_path)
        self.script_name_var = tk.StringVar(value=self.integration.script_name)
        self._form_entry(header, "Generated resource / ScriptName", self.virtual_path_var, 0)
        if self._asset_existed:
            for child in header.grid_slaves(row=0, column=1):
                if isinstance(child, tk.Entry):
                    child.configure(state="readonly")
        self._form_entry(header, "Existing base ScriptName (optional)", self.base_path_var, 1)
        base_buttons = tk.Frame(header, bg=PANEL)
        base_buttons.grid(row=2, column=1, sticky="w", padx=6, pady=(0, 5))
        tk.Button(
            base_buttons, text="Load Existing Script", command=self._load_base_script,
            bg=ACCENT, fg="white", relief="flat",
        ).pack(side="left", padx=(0, 5))
        tk.Button(
            base_buttons, text="Use Standalone Script", command=self._clear_base_script,
            bg=FIELD, fg=TEXT, relief="flat",
        ).pack(side="left")
        tk.Label(
            header,
            text=(
                "For an NPC that already has behavior, load its exact current ScriptName. "
                "The editor copies it and inserts one call into its single local OnRudeExit "
                "handler. Assign the generated ScriptName to the placed NPC when staging the DAT."
            ),
            bg=PANEL, fg=MUTED, justify="left", anchor="w", wraplength=1000,
        ).grid(row=3, column=0, columnspan=3, sticky="ew", padx=6, pady=(0, 6))

        pane = tk.PanedWindow(
            self, orient="horizontal", bg=BG, sashwidth=5, sashrelief="flat")
        pane.pack(fill="both", expand=True, padx=10, pady=5)
        left = tk.LabelFrame(pane, text="Ordered OnRudeExit hooks", bg=PANEL, fg=TEXT)
        right = tk.Frame(pane, bg=PANEL)
        pane.add(left, minsize=300)
        pane.add(right, minsize=580, stretch="always")

        self.hook_list = tk.Listbox(
            left, exportselection=False, bg=FIELD, fg=TEXT,
            selectbackground=ACCENT, relief="flat")
        self.hook_list.pack(fill="both", expand=True, padx=6, pady=6)
        self.hook_list.bind("<<ListboxSelect>>", self._on_hook_selected)
        buttons = tk.Frame(left, bg=PANEL)
        buttons.pack(fill="x", padx=6, pady=(0, 6))
        for label, command in (
            ("+ Hook", self._add_hook),
            ("Delete", self._delete_hook),
            ("↑", lambda: self._move_hook(-1)),
            ("↓", lambda: self._move_hook(1)),
        ):
            tk.Button(
                buttons, text=label, command=command,
                bg=FIELD, fg=TEXT, relief="flat",
            ).pack(side="left", padx=2)

        form = tk.LabelFrame(right, text="Selected hook", bg=PANEL, fg=TEXT)
        form.pack(fill="x", padx=5, pady=(0, 5))
        form.grid_columnconfigure(1, weight=1)
        self.key_var = tk.StringVar()
        self.label_var = tk.StringVar()
        self.consume_var = tk.BooleanVar(value=True)
        self.exp_var = tk.StringVar(value="0")
        self.gold_var = tk.StringVar(value="0")
        self.items_var = tk.StringVar()
        self.sound_enabled_var = tk.BooleanVar(value=True)
        self.sound_var = tk.StringVar(value=rude_script.DEFAULT_COMPLETION_SOUND)
        self._form_entry(form, "RUDE completion key", self.key_var, 0)
        self._form_entry(form, "Authoring label", self.label_var, 1)
        tk.Checkbutton(
            form, text="Consume completion key (one-shot)",
            variable=self.consume_var, bg=PANEL, fg=TEXT,
            selectcolor=FIELD, activebackground=PANEL, activeforeground=TEXT,
        ).grid(row=2, column=1, sticky="w", padx=6, pady=3)
        self._form_entry(form, "Experience reward", self.exp_var, 3)
        self._form_entry(form, "Gold reward", self.gold_var, 4)
        self._form_entry(form, "Item reward ids", self.items_var, 5)

        sound_row = tk.Frame(form, bg=PANEL)
        sound_row.grid(row=6, column=1, sticky="ew", padx=6, pady=3)
        sound_row.grid_columnconfigure(1, weight=1)
        tk.Checkbutton(
            sound_row, text="Play completion sound", variable=self.sound_enabled_var,
            bg=PANEL, fg=TEXT, selectcolor=FIELD,
            activebackground=PANEL, activeforeground=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=(0, 6))
        tk.Entry(
            sound_row, textvariable=self.sound_var, bg=FIELD, fg=TEXT,
            insertbackground="white", relief="flat",
        ).grid(row=0, column=1, sticky="ew")
        tk.Label(form, text="Completion sound", bg=PANEL, fg=MUTED).grid(
            row=6, column=0, sticky="w", padx=6, pady=3)

        tk.Label(
            form, text="World changes\nObjectName, message",
            bg=PANEL, fg=MUTED, justify="left",
        ).grid(row=7, column=0, sticky="nw", padx=6, pady=3)
        self.world_text = tk.Text(
            form, height=4, bg=FIELD, fg=TEXT,
            insertbackground="white", relief="flat")
        self.world_text.grid(row=7, column=1, sticky="ew", padx=6, pady=3)
        tk.Label(
            form,
            text=(
                "Verified world primitive: GetObjectHandle followed by Trigger. "
                "Use messages such as trigger, open, unlock, destroy, on, or off."
            ),
            bg=PANEL, fg=MUTED, justify="left", anchor="w", wraplength=650,
        ).grid(row=8, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 4))
        tk.Button(
            form, text="Apply Hook & Refresh Preview", command=self._apply_hook,
            bg=ACCENT, fg="white", relief="flat",
        ).grid(row=9, column=1, sticky="e", padx=6, pady=6)

        preview = tk.LabelFrame(right, text="Generated JSL preview", bg=PANEL, fg=TEXT)
        preview.pack(fill="both", expand=True, padx=5, pady=(5, 0))
        self.preview_text = tk.Text(
            preview, wrap="none", bg="#111821", fg=TEXT,
            insertbackground="white", relief="flat", state="disabled")
        yscroll = tk.Scrollbar(preview, command=self.preview_text.yview)
        xscroll = tk.Scrollbar(preview, orient="horizontal", command=self.preview_text.xview)
        self.preview_text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.preview_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=(6, 0))
        yscroll.grid(row=0, column=1, sticky="ns", pady=(6, 0))
        xscroll.grid(row=1, column=0, sticky="ew", padx=(6, 0), pady=(0, 6))
        preview.grid_rowconfigure(0, weight=1)
        preview.grid_columnconfigure(0, weight=1)

        footer = tk.Frame(self, bg=PANEL)
        footer.pack(fill="x", padx=10, pady=(5, 10))
        self.status_var = tk.StringVar(value="Working copy — not yet applied")
        tk.Label(
            footer, textvariable=self.status_var, bg=PANEL, fg=MUTED,
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=8, pady=6)
        tk.Button(
            footer, text="Apply Script to Project", command=self._apply_to_project,
            bg=GOOD, fg="white", relief="flat",
        ).pack(side="right", padx=4, pady=4)
        if self.on_attach is not None:
            tk.Button(
                footer, text="Apply & Attach to Selected NPC",
                command=self._apply_and_attach,
                bg=ACCENT, fg="white", relief="flat",
            ).pack(side="right", padx=4, pady=4)
        tk.Button(
            footer, text="Close", command=self.destroy,
            bg=FIELD, fg=TEXT, relief="flat",
        ).pack(side="right", padx=4, pady=4)

    def _form_entry(
        self, parent: tk.Misc, label: str, variable: tk.StringVar, row: int,
    ) -> tk.Entry:
        tk.Label(parent, text=label, bg=PANEL, fg=MUTED).grid(
            row=row, column=0, sticky="w", padx=6, pady=3)
        entry = tk.Entry(
            parent, textvariable=variable, bg=FIELD, fg=TEXT,
            insertbackground="white", relief="flat")
        entry.grid(row=row, column=1, sticky="ew", padx=6, pady=3)
        return entry

    def _candidate_key(self) -> int:
        used = {hook.completion_key for hook in self.integration.hooks}
        asset = self.project.rude_assets.get(self.npc_nbr)
        if asset is not None:
            for choice in asset.dialogue.choices_in_file_order:
                for key_id in choice.effects.granted:
                    if key_id > 0 and key_id not in used:
                        return key_id
        return max(used, default=0) + 1

    def _add_hook(self) -> None:
        if not self._apply_loaded_hook(show_error=True):
            return
        self.integration.hooks.append(rude_script.RudeExitHook(
            completion_key=self._candidate_key(),
            completion_sound=rude_script.DEFAULT_COMPLETION_SOUND,
        ))
        self._refresh_hooks(select_index=len(self.integration.hooks) - 1)
        self._refresh_preview()

    def _delete_hook(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        del self.integration.hooks[index]
        self._loaded_hook_index = None
        self._refresh_hooks(select_index=max(0, index - 1))
        self._refresh_preview()

    def _move_hook(self, delta: int) -> None:
        index = self._selected_index()
        if index is None or not self._apply_loaded_hook(show_error=True):
            return
        target = index + int(delta)
        if target < 0 or target >= len(self.integration.hooks):
            return
        hook = self.integration.hooks.pop(index)
        self.integration.hooks.insert(target, hook)
        self._loaded_hook_index = None
        self._refresh_hooks(select_index=target)
        self._refresh_preview()

    def _selected_index(self) -> Optional[int]:
        selection = self.hook_list.curselection()
        return int(selection[0]) if selection else None

    def _on_hook_selected(self, _event=None) -> None:
        index = self._selected_index()
        if index is None or index == self._loaded_hook_index:
            return
        if not self._apply_loaded_hook(show_error=True):
            self.hook_list.selection_clear(0, "end")
            if self._loaded_hook_index is not None:
                self.hook_list.selection_set(self._loaded_hook_index)
            return
        self._load_hook(index)

    def _load_hook(self, index: int) -> None:
        if index < 0 or index >= len(self.integration.hooks):
            self._loaded_hook_index = None
            return
        hook = self.integration.hooks[index]
        self._loaded_hook_index = index
        self.key_var.set(str(hook.completion_key))
        self.label_var.set(hook.label)
        self.consume_var.set(hook.consume_key)
        self.exp_var.set(str(hook.reward.experience))
        self.gold_var.set(str(hook.reward.gold))
        self.items_var.set(format_item_ids(hook.reward.item_ids))
        self.sound_enabled_var.set(bool(hook.completion_sound))
        self.sound_var.set(
            hook.completion_sound or rude_script.DEFAULT_COMPLETION_SOUND)
        self.world_text.delete("1.0", "end")
        self.world_text.insert("1.0", format_world_changes(hook.world_changes))

    def _hook_from_fields(self) -> rude_script.RudeExitHook:
        try:
            key_id = int(self.key_var.get().strip())
            experience = int(self.exp_var.get().strip() or "0")
            gold = int(self.gold_var.get().strip() or "0")
        except ValueError as exc:
            raise ValueError("Completion key, experience, and gold must be integers") from exc
        hook = rude_script.RudeExitHook(
            completion_key=key_id,
            label=self.label_var.get(),
            consume_key=self.consume_var.get(),
            reward=rude_script.ScriptReward(
                experience=experience,
                gold=gold,
                item_ids=parse_item_ids(self.items_var.get()),
            ),
            completion_sound=(
                self.sound_var.get().strip()
                if self.sound_enabled_var.get() else ""
            ),
            world_changes=parse_world_changes(
                self.world_text.get("1.0", "end-1c")),
        )
        hook.validate()
        return hook

    def _apply_loaded_hook(self, *, show_error: bool) -> bool:
        index = self._loaded_hook_index
        if index is None or index >= len(self.integration.hooks):
            return True
        try:
            self.integration.hooks[index] = self._hook_from_fields()
        except ValueError as exc:
            if show_error:
                messagebox.showerror("Invalid script hook", str(exc), parent=self)
            return False
        return True

    def _apply_hook(self) -> None:
        index = self._loaded_hook_index
        if index is None:
            messagebox.showinfo("No hook", "Add or select a hook first.", parent=self)
            return
        if not self._apply_loaded_hook(show_error=True):
            return
        self._refresh_hooks(select_index=index)
        self._refresh_preview()
        self.status_var.set("Hook applied to working script; review the generated JSL")

    def _refresh_hooks(self, *, select_index: int = 0) -> None:
        self.hook_list.delete(0, "end")
        for index, hook in enumerate(self.integration.hooks, 1):
            actions = []
            if hook.reward.has_actions:
                actions.append("reward")
            if hook.completion_sound:
                actions.append("sound")
            if hook.world_changes:
                actions.append(f"world×{len(hook.world_changes)}")
            label = f" — {hook.label}" if hook.label else ""
            self.hook_list.insert(
                "end",
                f"{index}. key {hook.completion_key}{label} [{', '.join(actions) or 'hook'}]",
            )
        self._loaded_hook_index = None
        if self.integration.hooks:
            select_index = max(0, min(select_index, len(self.integration.hooks) - 1))
            self.hook_list.selection_set(select_index)
            self.hook_list.see(select_index)
            self._load_hook(select_index)
        else:
            self.key_var.set("")
            self.label_var.set("")
            self.exp_var.set("0")
            self.gold_var.set("0")
            self.items_var.set("")
            self.world_text.delete("1.0", "end")

    def _load_base_script(self) -> None:
        requested = self.base_path_var.get().strip() or f"NPC{self.npc_nbr}.SCR"
        try:
            path, source = self.project.load_script_source(requested)
        except Exception as exc:
            messagebox.showerror("Cannot load base script", str(exc), parent=self)
            return
        self.integration.base_virtual_path = path
        self.integration.base_source_text = source
        self.base_path_var.set(path)
        self._refresh_preview()
        self.status_var.set(
            f"Loaded {path}; automatic integration requires one unambiguous callback")

    def _clear_base_script(self) -> None:
        self.integration.base_virtual_path = ""
        self.integration.base_source_text = ""
        self.base_path_var.set("")
        self._refresh_preview()
        self.status_var.set("Using a standalone script for an NPC with no other ScriptName")

    def _working_integration(self) -> rude_script.DialogueScriptIntegration:
        candidate = copy.deepcopy(self.integration)
        candidate.virtual_path = self.virtual_path_var.get().strip()
        candidate.base_virtual_path = self.base_path_var.get().strip()
        return candidate

    def _set_preview(self, value: str) -> None:
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", value)
        self.preview_text.configure(state="disabled")

    def _refresh_preview(self) -> None:
        if not self._apply_loaded_hook(show_error=False):
            self._set_preview("Fix the selected hook to generate a preview.")
            return
        try:
            candidate = self._working_integration()
            rendered = candidate.render()
            self.script_name_var.set(candidate.script_name)
        except Exception as exc:
            self._set_preview(f"Cannot generate script yet:\n\n{exc}")
            return
        self._set_preview(rendered)

    def _commit_to_project(
        self,
    ) -> Optional[rude_script.DialogueScriptAssetEdit]:
        if not self._apply_loaded_hook(show_error=True):
            return None
        try:
            candidate = self._working_integration()
            asset = self.project.upsert_dialogue_script_asset(candidate)
        except Exception as exc:
            messagebox.showerror("Cannot apply dialogue script", str(exc), parent=self)
            return None
        self.integration = copy.deepcopy(asset.integration)
        self.virtual_path_var.set(asset.integration.virtual_path)
        self.base_path_var.set(asset.integration.base_virtual_path)
        self.script_name_var.set(asset.integration.script_name)
        self._asset_existed = True
        self._refresh_hooks(select_index=self._selected_index() or 0)
        self._refresh_preview()
        if self.on_changed is not None:
            self.on_changed(asset)
        return asset

    def _apply_to_project(self) -> None:
        asset = self._commit_to_project()
        if asset is None:
            return
        self.status_var.set(
            f"Applied to project. Assign ScriptName={asset.integration.script_name} "
            "to the placed NPC, then use File → Save."
        )

    def _apply_and_attach(self) -> None:
        asset = self._commit_to_project()
        if asset is None or self.on_attach is None:
            return
        if self.on_attach(asset):
            self.status_var.set(
                f"Applied script and attached {asset.integration.script_name} "
                f"to the selected NPC{self.npc_nbr}; use File → Save."
            )
