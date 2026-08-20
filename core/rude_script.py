"""Reviewed script integration for RUDE dialogue exit effects.

RUDE rows can grant keys, but rewards and world-object changes are performed
by the NPC's JSL script after the dialogue closes.  This module deliberately
models only commands verified in the shipped MM9 ``SCRIPTS.REZ`` corpus.  It
generates a standalone script for a new NPC, or makes a lossless copy of an
existing script and inserts one call into its existing ``OnRudeExit`` handler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_COMPLETION_SOUND = r"sounds\events\quest.wav"
SCRIPT_ROOT = r"SCRIPTS\MM9EDITOR"
GENERATED_HANDLER = "MM9EditorRudeExit"
_HAS_KEY_VAR = "MM9EditorHasKey"
_TARGET_VAR = "MM9EditorTarget"
_MARKER = "; <MM9EDITOR RUDE SCRIPT INTEGRATION>"
_END_MARKER = "; </MM9EDITOR RUDE SCRIPT INTEGRATION>"

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_SAFE_PATH_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_LABEL_RE = re.compile(r"^\s*:([A-Za-z_][A-Za-z0-9_]*)\s*(?:;.*)?$", re.I)
_ON_RUDE_EXIT_RE = re.compile(
    r"^\s*OnRudeExit(?:\s*,\s*|\s+)([A-Za-z_][A-Za-z0-9_]*)\b", re.I
)


def canonical_script_path(value: str, *, require_editor_root: bool = False) -> str:
    """Return a safe archive path using the spelling expected by ScriptName."""
    path = str(value or "").replace("/", "\\").strip().strip("\\")
    if not path:
        raise ValueError("Script resource path is required")
    if not path.casefold().startswith("scripts\\"):
        path = "SCRIPTS\\" + path
    parts = path.split("\\")
    if (
        any(part in {"", ".", ".."} for part in parts)
        or any(not _SAFE_PATH_PART_RE.fullmatch(part) for part in parts)
    ):
        raise ValueError(f"Unsafe script resource path {value!r}")
    if any(ch in path for ch in ('\r', '\n', '"', ';', ',')):
        raise ValueError(f"Unsafe script resource path {value!r}")
    if not path.casefold().endswith(".scr"):
        path += ".SCR"
    if require_editor_root and not path.casefold().startswith(
            (SCRIPT_ROOT + "\\").casefold()):
        raise ValueError(
            f"Generated dialogue scripts must be under {SCRIPT_ROOT}"
        )
    try:
        path.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ValueError("Script resource paths must be Latin-1 encodable") from exc
    return path


def default_script_path(npc_nbr: int) -> str:
    return rf"{SCRIPT_ROOT}\NPC{int(npc_nbr)}_RUDE.SCR"


@dataclass
class ScriptReward:
    experience: int = 0
    gold: int = 0
    item_ids: Tuple[int, ...] = ()

    def validate(self) -> None:
        self.experience = int(self.experience)
        self.gold = int(self.gold)
        self.item_ids = tuple(int(value) for value in self.item_ids)
        if self.experience < 0:
            raise ValueError("Experience reward cannot be negative")
        if self.gold < 0:
            raise ValueError("Gold reward cannot be negative")
        if any(value <= 0 for value in self.item_ids):
            raise ValueError("Reward item ids must be positive integers")

    @property
    def has_actions(self) -> bool:
        return bool(self.experience or self.gold or self.item_ids)


@dataclass
class ScriptWorldChange:
    """A verified named-object message: GetObjectHandle then Trigger."""

    object_name: str
    message: str = "trigger"

    def validate(self) -> None:
        self.object_name = str(self.object_name or "").strip()
        self.message = str(self.message or "").strip()
        if not _SAFE_TOKEN_RE.fullmatch(self.object_name):
            raise ValueError(
                f"World object name {self.object_name!r} is not a safe JSL token"
            )
        if not _SAFE_TOKEN_RE.fullmatch(self.message):
            raise ValueError(
                f"World trigger message {self.message!r} is not a safe JSL token"
            )


@dataclass
class RudeExitHook:
    """Actions run when RUDE has granted *completion_key* and then exits."""

    completion_key: int
    label: str = ""
    consume_key: bool = True
    reward: ScriptReward = field(default_factory=ScriptReward)
    completion_sound: str = ""
    world_changes: List[ScriptWorldChange] = field(default_factory=list)

    def validate(self) -> None:
        self.completion_key = int(self.completion_key)
        if self.completion_key <= 0:
            raise ValueError("OnRudeExit completion keys must be positive integers")
        self.label = str(self.label or "").strip()
        if self.label:
            try:
                self.label.encode("latin-1")
            except UnicodeEncodeError as exc:
                raise ValueError("Hook labels must be Latin-1 encodable") from exc
            if any(ch in self.label for ch in ("\r", "\n", ";")):
                raise ValueError("Hook labels cannot contain newlines or semicolons")
        self.consume_key = bool(self.consume_key)
        self.reward.validate()
        self.completion_sound = str(self.completion_sound or "").strip().replace(
            "/", "\\")
        if self.completion_sound:
            folded = self.completion_sound.casefold().lstrip("\\")
            if not folded.startswith("sounds\\") or not folded.endswith(".wav"):
                raise ValueError(
                    "Completion sound must be a WAV resource under sounds\\"
                )
            if any(ch in self.completion_sound for ch in ('\r', '\n', '"', ';', ',')):
                raise ValueError("Completion sound contains unsafe JSL characters")
            try:
                self.completion_sound.encode("latin-1")
            except UnicodeEncodeError as exc:
                raise ValueError("Completion sound paths must be Latin-1 encodable") from exc
        self.world_changes = list(self.world_changes)
        for change in self.world_changes:
            change.validate()
        if not (
            self.consume_key
            or self.reward.has_actions
            or self.completion_sound
            or self.world_changes
        ):
            raise ValueError(
                f"OnRudeExit hook for key {self.completion_key} has no actions"
            )


@dataclass
class DialogueScriptIntegration:
    """One generated ScriptName resource and its ordered RUDE-exit hooks."""

    npc_nbr: int
    virtual_path: str = ""
    hooks: List[RudeExitHook] = field(default_factory=list)
    base_virtual_path: str = ""
    base_source_text: str = ""

    def __post_init__(self) -> None:
        self.npc_nbr = int(self.npc_nbr)
        if not self.virtual_path:
            self.virtual_path = default_script_path(self.npc_nbr)

    @property
    def script_name(self) -> str:
        """Value to assign to the world object's ``ScriptName`` property."""
        return canonical_script_path(self.virtual_path, require_editor_root=True)

    def validate(self) -> None:
        self.npc_nbr = int(self.npc_nbr)
        if self.npc_nbr <= 0:
            raise ValueError("Dialogue script NPC number must be positive")
        self.virtual_path = canonical_script_path(
            self.virtual_path, require_editor_root=True)
        self.base_virtual_path = str(self.base_virtual_path or "").strip()
        self.base_source_text = str(self.base_source_text or "")
        if self.base_virtual_path:
            self.base_virtual_path = canonical_script_path(self.base_virtual_path)
            if not self.base_source_text:
                raise ValueError("A base script resource was selected but has no source")
        elif self.base_source_text:
            raise ValueError("Base script source requires a base resource path")
        try:
            self.base_source_text.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise ValueError("Base script source must be Latin-1 encodable") from exc
        self.hooks = list(self.hooks)
        if not self.hooks:
            raise ValueError("Add at least one OnRudeExit hook")
        seen = set()
        for hook in self.hooks:
            hook.validate()
            if hook.completion_key in seen:
                raise ValueError(
                    f"Completion key {hook.completion_key} is used by more than one hook"
                )
            seen.add(hook.completion_key)

    def render(self) -> str:
        self.validate()
        handler = _render_handler(self.hooks, newline=_source_newline(self.base_source_text))
        if self.base_source_text:
            return _integrate_base_source(self.base_source_text, handler)
        return _render_standalone(handler)

    def to_bytes(self) -> bytes:
        try:
            return self.render().encode("latin-1")
        except UnicodeEncodeError as exc:
            raise ValueError("Generated dialogue script is not Latin-1 encodable") from exc


@dataclass
class DialogueScriptAssetEdit:
    """Project-level generated script asset, independent of NPC placement."""

    integration: DialogueScriptIntegration
    original_script_bytes: Optional[bytes] = field(default=None, repr=False)

    @property
    def npc_nbr(self) -> int:
        return self.integration.npc_nbr

    @property
    def source_virtual_path(self) -> str:
        return self.integration.virtual_path

    @property
    def is_new(self) -> bool:
        return self.original_script_bytes is None

    @property
    def is_dirty(self) -> bool:
        return self.is_new or self.integration.to_bytes() != self.original_script_bytes

    def summary(self) -> str:
        return (
            f"{'new' if self.is_new else 'edit'} {self.integration.script_name}: "
            f"{len(self.integration.hooks)} OnRudeExit hook(s)"
        )


def _source_newline(source: str) -> str:
    if "\r\n" in source:
        return "\r\n"
    if "\r" in source and "\n" not in source:
        return "\r"
    return "\n"


def _active_line(line: str) -> bool:
    return not line.lstrip().startswith((";", "//"))


def _render_handler(hooks: Sequence[RudeExitHook], *, newline: str) -> str:
    lines = [_MARKER, f":{GENERATED_HANDLER}"]
    for hook in hooks:
        title = f"key {hook.completion_key}"
        if hook.label:
            title += f" - {hook.label}"
        lines.extend(("", f"; {title}", f"HasKey {hook.completion_key}, {_HAS_KEY_VAR}",
                      f"if ({_HAS_KEY_VAR}==TRUE)"))
        if hook.consume_key:
            lines.append(f"\tTakeKey {hook.completion_key}")
        if hook.reward.experience:
            lines.append(f"\tGiveExp {hook.reward.experience}")
        if hook.reward.gold:
            lines.append(f"\tGiveGold {hook.reward.gold}")
        for item_id in hook.reward.item_ids:
            lines.append(f"\tGiveItem {item_id}")
        if hook.completion_sound:
            lines.append(
                f'\tPlaySound "{hook.completion_sound}", DoNothing, '
                "100, 240, FALSE, 100"
            )
        for change in hook.world_changes:
            lines.extend((
                f"\tGetObjectHandle {change.object_name}, {_TARGET_VAR}",
                f"\tif ({_TARGET_VAR}!=0)",
                f"\t\tTrigger {_TARGET_VAR}, {change.message}",
                "\tendif",
            ))
        lines.append("endif")
    lines.extend(("", "Exit TRUE", _END_MARKER))
    return newline.join(lines) + newline


def _render_standalone(handler: str) -> str:
    nl = "\r\n"
    prefix = nl.join((
        "; Generated by MM9 Editor for a RUDE dialogue ScriptName.",
        "#include globals.inc",
        "",
        f"#number {_HAS_KEY_VAR} = 0",
        f"#hobject {_TARGET_VAR} = 0",
        "",
        ":Main",
        f"\tOnRudeExit {GENERATED_HANDLER}",
        "\tExit TRUE",
        "",
    ))
    if _source_newline(handler) != nl:
        handler = handler.replace("\n", nl)
    return prefix + handler


def _integrate_base_source(source: str, handler: str) -> str:
    """Insert a reviewed hook call while otherwise preserving *source*."""
    folded = source.casefold()
    for reserved in (_MARKER, GENERATED_HANDLER, _HAS_KEY_VAR, _TARGET_VAR):
        if reserved.casefold() in folded:
            raise ValueError(
                f"Base script already contains reserved MM9 Editor token {reserved!r}"
            )

    nl = _source_newline(source)
    lines = source.splitlines(keepends=True)
    if not lines:
        raise ValueError("Base script source is empty")

    labels: Dict[str, List[int]] = {}
    registrations: List[Tuple[int, str]] = []
    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        if not _active_line(line):
            continue
        match = _LABEL_RE.match(line)
        if match:
            labels.setdefault(match.group(1).casefold(), []).append(index)
        match = _ON_RUDE_EXIT_RE.match(line)
        if match:
            registrations.append((index, match.group(1)))

    if len(registrations) > 1:
        raise ValueError(
            "Base script changes OnRudeExit more than once; automatic integration "
            "would be ambiguous"
        )

    insertions: List[Tuple[int, List[str]]] = []
    declaration_at = min((items[0] for items in labels.values()), default=0)
    insertions.append((declaration_at, [
        f"#number {_HAS_KEY_VAR} = 0{nl}",
        f"#hobject {_TARGET_VAR} = 0{nl}",
        nl,
    ]))

    if registrations:
        callback = registrations[0][1]
        callback_labels = labels.get(callback.casefold(), [])
        if len(callback_labels) != 1:
            raise ValueError(
                f"Base OnRudeExit callback {callback!r} does not have exactly one "
                "local label"
            )
        insertions.append((callback_labels[0] + 1, [
            f"\tGosub {GENERATED_HANDLER}{nl}",
        ]))
    else:
        main_labels = labels.get("main", [])
        if len(main_labels) != 1:
            raise ValueError(
                "Base script has no OnRudeExit registration and does not have "
                "exactly one :Main label"
            )
        insertions.append((main_labels[0] + 1, [
            f"\tOnRudeExit {GENERATED_HANDLER}{nl}",
        ]))

    for index, new_lines in sorted(insertions, key=lambda item: item[0], reverse=True):
        lines[index:index] = new_lines
    merged = "".join(lines)
    if merged and not merged.endswith(("\r", "\n")):
        merged += nl
    if handler and _source_newline(handler) != nl:
        handler = handler.replace(_source_newline(handler), nl)
    return merged + nl + handler


def reward_to_dict(value: ScriptReward) -> Dict[str, Any]:
    return {
        "experience": value.experience,
        "gold": value.gold,
        "item_ids": list(value.item_ids),
    }


def reward_from_dict(value: Optional[Dict[str, Any]]) -> ScriptReward:
    value = dict(value or {})
    return ScriptReward(
        experience=int(value.get("experience", 0)),
        gold=int(value.get("gold", 0)),
        item_ids=tuple(int(item) for item in value.get("item_ids", [])),
    )


def hook_to_dict(value: RudeExitHook) -> Dict[str, Any]:
    return {
        "completion_key": value.completion_key,
        "label": value.label,
        "consume_key": value.consume_key,
        "reward": reward_to_dict(value.reward),
        "completion_sound": value.completion_sound,
        "world_changes": [
            {"object_name": item.object_name, "message": item.message}
            for item in value.world_changes
        ],
    }


def hook_from_dict(value: Dict[str, Any]) -> RudeExitHook:
    return RudeExitHook(
        completion_key=int(value["completion_key"]),
        label=str(value.get("label", "")),
        consume_key=bool(value.get("consume_key", True)),
        reward=reward_from_dict(value.get("reward")),
        completion_sound=str(value.get("completion_sound", "")),
        world_changes=[
            ScriptWorldChange(
                object_name=str(item.get("object_name", "")),
                message=str(item.get("message", "trigger")),
            )
            for item in value.get("world_changes", [])
        ],
    )


def integration_to_dict(value: DialogueScriptIntegration) -> Dict[str, Any]:
    return {
        "npc_nbr": value.npc_nbr,
        "virtual_path": value.virtual_path,
        "base_virtual_path": value.base_virtual_path,
        "base_source_text": value.base_source_text,
        "hooks": [hook_to_dict(hook) for hook in value.hooks],
    }


def integration_from_dict(value: Dict[str, Any]) -> DialogueScriptIntegration:
    return DialogueScriptIntegration(
        npc_nbr=int(value["npc_nbr"]),
        virtual_path=str(value.get("virtual_path", "")),
        base_virtual_path=str(value.get("base_virtual_path", "")),
        base_source_text=str(value.get("base_source_text", "")),
        hooks=[hook_from_dict(item) for item in value.get("hooks", [])],
    )


def asset_to_dict(value: DialogueScriptAssetEdit) -> Dict[str, Any]:
    return {
        "integration": integration_to_dict(value.integration),
        "original_script_text": (
            value.original_script_bytes.decode("latin-1")
            if value.original_script_bytes is not None else None
        ),
    }


def asset_from_dict(value: Dict[str, Any]) -> DialogueScriptAssetEdit:
    original = value.get("original_script_text")
    return DialogueScriptAssetEdit(
        integration=integration_from_dict(dict(value["integration"])),
        original_script_bytes=(
            str(original).encode("latin-1") if original is not None else None
        ),
    )
