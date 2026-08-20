"""Quest-oriented indexing, authoring, and validation for RUDE resources."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

from core import rude


QUEST_NOTES_NPC_NBR = 997
AUTO_NOTES_NPC_NBR = 998
AWARDS_NPC_NBR = 999


class QuestKeyRole(str, Enum):
    RUDE_REQUIRED = "rude_required"
    RUDE_FORBIDDEN = "rude_forbidden"
    RUDE_GRANTED = "rude_granted"
    RUDE_REMOVED = "rude_removed"
    RUDE_NATIVE_EFFECT = "rude_native_effect"
    SCRIPT_CHECK = "script_check"
    SCRIPT_GRANT = "script_grant"
    SCRIPT_REMOVE = "script_remove"


@dataclass(frozen=True)
class QuestKeyUsage:
    key_id: int
    role: QuestKeyRole
    source: str
    line_number: int
    detail: str
    npc_nbr: Optional[int] = None
    state_id: Optional[int] = None
    branch_id: Optional[int] = None
    certain: bool = True

    @property
    def location(self) -> str:
        if self.npc_nbr is not None:
            return (
                f"NPC{self.npc_nbr} state {self.state_id} "
                f"branch {self.branch_id}"
            )
        return f"{self.source}:{self.line_number}"


@dataclass(frozen=True)
class UnresolvedScriptKeyUsage:
    role: QuestKeyRole
    source: str
    line_number: int
    operand: str
    detail: str


@dataclass
class QuestKeyIndex:
    usages: Dict[int, List[QuestKeyUsage]] = field(default_factory=dict)
    unresolved_script_usages: List[UnresolvedScriptKeyUsage] = field(
        default_factory=list)
    scan_warnings: List[str] = field(default_factory=list)
    rude_resource_count: int = 0
    script_resource_count: int = 0

    def add(self, usage: QuestKeyUsage) -> None:
        if int(usage.key_id) == 0:
            return
        self.usages.setdefault(int(usage.key_id), []).append(usage)

    @property
    def used_keys(self) -> frozenset[int]:
        return frozenset(self.usages)

    @property
    def usage_count(self) -> int:
        return sum(len(values) for values in self.usages.values())

    def usage_for(self, key_id: int) -> Tuple[QuestKeyUsage, ...]:
        return tuple(self.usages.get(int(key_id), ()))

    def next_unused(self, start: int = 1) -> int:
        candidate = max(1, int(start))
        while candidate in self.usages:
            candidate += 1
        return candidate


class QuestIssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class QuestValidationIssue:
    severity: QuestIssueSeverity
    code: str
    message: str
    state_id: Optional[int] = None
    branch_id: Optional[int] = None


@dataclass(frozen=True)
class DialogueValidationReport:
    issues: Tuple[QuestValidationIssue, ...]
    reachable_states: frozenset[int]
    unreachable_states: frozenset[int]
    states_with_terminal_path: frozenset[int]

    @property
    def errors(self) -> Tuple[QuestValidationIssue, ...]:
        return tuple(
            issue for issue in self.issues
            if issue.severity is QuestIssueSeverity.ERROR
        )

    @property
    def warnings(self) -> Tuple[QuestValidationIssue, ...]:
        return tuple(
            issue for issue in self.issues
            if issue.severity is QuestIssueSeverity.WARNING
        )


_SCRIPT_COMMAND_ROLES = {
    "haskey": QuestKeyRole.SCRIPT_CHECK,
    "givekey": QuestKeyRole.SCRIPT_GRANT,
    "takekey": QuestKeyRole.SCRIPT_REMOVE,
}
_SCRIPT_KEY_COMMAND = re.compile(
    r"^\s*(haskey|givekey|takekey)\s*[,\s(]+([A-Za-z_]\w*|-?\d+)\b",
    re.IGNORECASE,
)
_NUMBER_DECLARATION = re.compile(
    r"^\s*#number\s+([A-Za-z_]\w*)\s*(?:=|,)\s*(-?\d+)\b",
    re.IGNORECASE,
)
_SET_ASSIGNMENT = re.compile(
    r"^\s*set\s+([A-Za-z_]\w*)\s*[,\s]+(-?\d+)\b",
    re.IGNORECASE,
)
_PLAIN_ASSIGNMENT = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*=\s*(-?\d+)\b",
    re.IGNORECASE,
)
_NPC_RESOURCE = re.compile(r"^RUDE/NPC(\d+)(?:\.RUDE)?$", re.IGNORECASE)


def _code_lines(text: str) -> List[Tuple[int, str]]:
    return [
        (line_number, raw_line.split(";", 1)[0].strip())
        for line_number, raw_line in enumerate(str(text).splitlines(), 1)
    ]


def index_script_text(
    index: QuestKeyIndex,
    source: str,
    text: str,
) -> None:
    """Index literal and locally resolvable HasKey/GiveKey/TakeKey calls."""
    lines = _code_lines(text)
    possible_values: Dict[str, Set[int]] = {}
    for _line_number, code in lines:
        if not code:
            continue
        match = (
            _NUMBER_DECLARATION.match(code)
            or _SET_ASSIGNMENT.match(code)
            or _PLAIN_ASSIGNMENT.match(code)
        )
        if match is not None:
            possible_values.setdefault(match.group(1).casefold(), set()).add(
                int(match.group(2)))

    for line_number, code in lines:
        match = _SCRIPT_KEY_COMMAND.match(code)
        if match is None:
            continue
        command = match.group(1).casefold()
        operand = match.group(2)
        role = _SCRIPT_COMMAND_ROLES[command]
        try:
            values = {int(operand)}
        except ValueError:
            values = possible_values.get(operand.casefold(), set())
        if not values:
            index.unresolved_script_usages.append(UnresolvedScriptKeyUsage(
                role=role,
                source=source,
                line_number=line_number,
                operand=operand,
                detail=code,
            ))
            continue
        certain = len(values) == 1
        for key_id in sorted(values):
            index.add(QuestKeyUsage(
                key_id=key_id,
                role=role,
                source=source,
                line_number=line_number,
                detail=code,
                certain=certain,
            ))


def index_dialogue(index: QuestKeyIndex, dialogue: rude.RudeDialogue) -> None:
    for line_number, choice in enumerate(dialogue.choices_in_file_order, 1):
        common = {
            "source": f"RUDE/NPC{dialogue.metadata.npc_nbr}",
            "line_number": line_number,
            "npc_nbr": dialogue.metadata.npc_nbr,
            "state_id": choice.state_id,
            "branch_id": choice.branch_id,
        }
        for key_id in choice.conditions.required_keys:
            index.add(QuestKeyUsage(
                key_id=key_id,
                role=QuestKeyRole.RUDE_REQUIRED,
                detail="required for choice visibility",
                **common,
            ))
        for key_id in choice.conditions.forbidden_keys:
            index.add(QuestKeyUsage(
                key_id=key_id,
                role=QuestKeyRole.RUDE_FORBIDDEN,
                detail="must be absent for choice visibility",
                **common,
            ))

        is_native = choice.action.kind is rude.RudeActionKind.NATIVE
        for slot_kind, values, normal_role in (
            ("grant", choice.effects.granted_keys, QuestKeyRole.RUDE_GRANTED),
            ("remove", choice.effects.removed_keys, QuestKeyRole.RUDE_REMOVED),
        ):
            for key_id in values:
                if is_native:
                    role = QuestKeyRole.RUDE_NATIVE_EFFECT
                    detail = (
                        f"native action {choice.action.value} {slot_kind} slot; "
                        "may be an engine parameter rather than a key"
                    )
                else:
                    role = normal_role
                    detail = f"{slot_kind}ed when the choice is selected"
                index.add(QuestKeyUsage(
                    key_id=key_id,
                    role=role,
                    detail=detail,
                    certain=not is_native,
                    **common,
                ))


def build_quest_key_index(
    rude_rez_path: Optional[str],
    scripts_rez_path: Optional[str],
    *,
    dialogue_overrides: Optional[Mapping[int, rude.RudeDialogue]] = None,
    script_overrides: Optional[Mapping[str, str]] = None,
) -> QuestKeyIndex:
    """Build a cross-archive key index, overlaying current project edits."""
    dialogue_overrides = dialogue_overrides or {}
    script_overrides = script_overrides or {}
    index = QuestKeyIndex()
    indexed_dialogues: Set[int] = set()

    if rude_rez_path and os.path.isfile(rude_rez_path):
        from core import rezmgr

        with rezmgr.RezReader(rude_rez_path) as reader:
            try:
                catalog = rude.RudeMetadataCatalog.from_bytes(
                    reader.extract_to_bytes("RUDE/NPCNAME"),
                    reader.extract_to_bytes("RUDE/TOPBLURB"),
                )
            except Exception as exc:
                catalog = None
                index.scan_warnings.append(f"RUDE metadata: {exc}")
            for virtual_path in reader.list_paths():
                match = _NPC_RESOURCE.fullmatch(virtual_path.replace("\\", "/"))
                if match is None:
                    continue
                npc_nbr = int(match.group(1))
                if npc_nbr in indexed_dialogues:
                    continue
                indexed_dialogues.add(npc_nbr)
                override = dialogue_overrides.get(npc_nbr)
                if override is not None:
                    index_dialogue(index, override)
                    index.rude_resource_count += 1
                    continue
                try:
                    metadata = (
                        catalog.metadata_for(npc_nbr)
                        if catalog is not None
                        else rude.RudeDialogueMetadata(
                            npc_nbr, f"NPC {npc_nbr}", npc_nbr, "")
                    )
                    dialogue = rude.RudeDialogue.from_bytes(
                        metadata,
                        reader.extract_to_bytes(virtual_path),
                        resource=virtual_path,
                    )
                    index_dialogue(index, dialogue)
                    index.rude_resource_count += 1
                except Exception as exc:
                    index.scan_warnings.append(f"{virtual_path}: {exc}")
    elif rude_rez_path:
        index.scan_warnings.append(f"RUDE archive not found: {rude_rez_path}")

    for npc_nbr, dialogue in sorted(dialogue_overrides.items()):
        if int(npc_nbr) in indexed_dialogues:
            continue
        index_dialogue(index, dialogue)
        index.rude_resource_count += 1

    indexed_scripts: Set[str] = set()
    normalized_script_overrides = {
        str(path).replace("\\", "/").casefold(): str(text)
        for path, text in script_overrides.items()
    }
    if scripts_rez_path and os.path.isfile(scripts_rez_path):
        from core import rezmgr

        with rezmgr.RezReader(scripts_rez_path) as reader:
            for virtual_path in reader.list_paths():
                normalized = virtual_path.replace("\\", "/").casefold()
                if not normalized.startswith("scripts/"):
                    continue
                text = normalized_script_overrides.get(normalized)
                try:
                    if text is None:
                        text = reader.extract_to_bytes(virtual_path).decode("latin-1")
                    index_script_text(index, virtual_path, text)
                    indexed_scripts.add(normalized)
                    index.script_resource_count += 1
                except Exception as exc:
                    index.scan_warnings.append(f"{virtual_path}: {exc}")
    elif scripts_rez_path:
        index.scan_warnings.append(f"SCRIPTS archive not found: {scripts_rez_path}")

    for virtual_path, text in sorted(script_overrides.items()):
        normalized = str(virtual_path).replace("\\", "/").casefold()
        if normalized in indexed_scripts:
            continue
        index_script_text(index, str(virtual_path), str(text))
        index.script_resource_count += 1
    return index


def _text_issues(choice: rude.RudeChoice) -> List[QuestValidationIssue]:
    issues: List[QuestValidationIssue] = []
    for label, value, limit, code in (
        ("Player text", choice.player_text, 127, "PLAYER_TEXT_TOO_LONG"),
        ("NPC response", choice.npc_response, 255, "NPC_RESPONSE_TOO_LONG"),
    ):
        try:
            size = len(value.encode("latin-1"))
        except UnicodeEncodeError:
            issues.append(QuestValidationIssue(
                QuestIssueSeverity.ERROR,
                "TEXT_NOT_LATIN1",
                f"{label} is not Latin-1 encodable",
                choice.state_id,
                choice.branch_id,
            ))
            continue
        if size > limit:
            issues.append(QuestValidationIssue(
                QuestIssueSeverity.ERROR,
                code,
                f"{label} is {size} bytes; runtime limit is {limit}",
                choice.state_id,
                choice.branch_id,
            ))
    return issues


def _reachable_states(dialogue: rude.RudeDialogue) -> Set[int]:
    state_ids = {state.state_id for state in dialogue.states}
    reachable: Set[int] = set()
    pending = [dialogue.metadata.initial_state]
    while pending:
        state_id = pending.pop()
        if state_id in reachable or state_id not in state_ids:
            continue
        reachable.add(state_id)
        state = dialogue.state(state_id)
        if state is None:
            continue
        pending.extend(
            int(choice.action.target_state)
            for choice in state.choices
            if (
                choice.action.kind is rude.RudeActionKind.STATE
                and choice.action.target_state is not None
            )
        )
    return reachable


def _states_with_terminal_path(dialogue: rude.RudeDialogue) -> Set[int]:
    reverse_edges: Dict[int, Set[int]] = {}
    terminal_states: Set[int] = set()
    for state in dialogue.states:
        for choice in state.choices:
            if choice.action.kind is rude.RudeActionKind.STATE:
                target = choice.action.target_state
                if target is not None and dialogue.state(target) is not None:
                    reverse_edges.setdefault(target, set()).add(state.state_id)
            else:
                terminal_states.add(state.state_id)
    pending = list(terminal_states)
    result = set(terminal_states)
    while pending:
        target = pending.pop()
        for source in reverse_edges.get(target, ()):
            if source not in result:
                result.add(source)
                pending.append(source)
    return result


def validate_dialogue(dialogue: rude.RudeDialogue) -> DialogueValidationReport:
    """Validate graph structure, key predicates, text, and action contracts."""
    issues: List[QuestValidationIssue] = []
    states = dialogue.states
    state_ids = {state.state_id for state in states}
    initial_state = dialogue.metadata.initial_state
    is_special_table = dialogue.metadata.npc_nbr in {
        QUEST_NOTES_NPC_NBR,
        AUTO_NOTES_NPC_NBR,
        AWARDS_NPC_NBR,
    }
    if not states:
        issues.append(QuestValidationIssue(
            QuestIssueSeverity.ERROR,
            "EMPTY_DIALOGUE",
            "Dialogue contains no states or choices",
        ))
    elif initial_state not in state_ids:
        issues.append(QuestValidationIssue(
            QuestIssueSeverity.ERROR,
            "MISSING_INITIAL_STATE",
            f"Initial state {initial_state} does not exist",
            initial_state,
        ))

    for state in states:
        branch_counts: Dict[int, int] = {}
        for choice in state.choices:
            branch_counts[choice.branch_id] = branch_counts.get(choice.branch_id, 0) + 1
            issues.extend(_text_issues(choice))
            required = set(choice.conditions.required_keys)
            forbidden = set(choice.conditions.forbidden_keys)
            impossible = sorted(required & forbidden)
            if impossible:
                issues.append(QuestValidationIssue(
                    QuestIssueSeverity.ERROR,
                    "IMPOSSIBLE_KEY_CONDITION",
                    "Keys are both required and forbidden: "
                    + ", ".join(str(value) for value in impossible),
                    state.state_id,
                    choice.branch_id,
                ))
            if any(choice.conditions.reserved):
                issues.append(QuestValidationIssue(
                    QuestIssueSeverity.WARNING,
                    "NONZERO_RESERVED_SLOTS",
                    "Reserved condition slots are nonzero; the shipped runtime data uses zero",
                    state.state_id,
                    choice.branch_id,
                ))

            action = choice.action
            if action.kind is not rude.RudeActionKind.NATIVE:
                overlap = sorted(
                    set(choice.effects.granted_keys)
                    & set(choice.effects.removed_keys)
                )
                if overlap:
                    issues.append(QuestValidationIssue(
                        QuestIssueSeverity.WARNING,
                        "KEY_GRANTED_AND_REMOVED",
                        "Choice grants and removes the same keys: "
                        + ", ".join(str(value) for value in overlap),
                        state.state_id,
                        choice.branch_id,
                    ))
            if action.kind is rude.RudeActionKind.STATE:
                target = action.target_state
                if not is_special_table and target not in state_ids and target != 0:
                    issues.append(QuestValidationIssue(
                        QuestIssueSeverity.ERROR,
                        "MISSING_STATE_TARGET",
                        f"Transition targets missing state {target}",
                        state.state_id,
                        choice.branch_id,
                    ))
                elif not is_special_table and target == 0 and target not in state_ids:
                    issues.append(QuestValidationIssue(
                        QuestIssueSeverity.WARNING,
                        "ZERO_ACTION_WITHOUT_STATE",
                        "Action 0 has no state 0 target; stock data sometimes uses 0 as a no-op",
                        state.state_id,
                        choice.branch_id,
                    ))
            elif action.kind is rude.RudeActionKind.NATIVE:
                if action.native_action is None:
                    issues.append(QuestValidationIssue(
                        QuestIssueSeverity.WARNING,
                        "UNKNOWN_NATIVE_ACTION",
                        f"Native action {action.value} has no dedicated runtime handler",
                        state.state_id,
                        choice.branch_id,
                    ))
                elif action.native_action is rude.RudeNativeAction.SKILL_TRAINING:
                    if choice.effects.granted[0] == 0:
                        issues.append(QuestValidationIssue(
                            QuestIssueSeverity.ERROR,
                            "SKILL_TRAINING_PARAMETER_MISSING",
                            "Skill training action -4 requires its skill parameter in column 15",
                            state.state_id,
                            choice.branch_id,
                        ))
                    if any(choice.effects.granted[1:]) or any(choice.effects.removed):
                        issues.append(QuestValidationIssue(
                            QuestIssueSeverity.WARNING,
                            "SKILL_TRAINING_EXTRA_PARAMETERS",
                            "Skill training action -4 has nonstandard extra effect parameters",
                            state.state_id,
                            choice.branch_id,
                        ))
                elif action.native_action is rude.RudeNativeAction.DISMISS:
                    removed_key = choice.effects.removed[0]
                    if removed_key == 0:
                        issues.append(QuestValidationIssue(
                            QuestIssueSeverity.WARNING,
                            "DISMISS_KEY_MISSING",
                            "Dismiss action -11 normally removes the hired-NPC key in column 25",
                            state.state_id,
                            choice.branch_id,
                        ))
                    required_key = choice.conditions.required[0]
                    if required_key and removed_key and required_key != removed_key:
                        issues.append(QuestValidationIssue(
                            QuestIssueSeverity.WARNING,
                            "DISMISS_KEY_MISMATCH",
                            f"Dismiss action requires key {required_key} but removes {removed_key}",
                            state.state_id,
                            choice.branch_id,
                        ))

        for branch_id, count in branch_counts.items():
            if count > 1:
                issues.append(QuestValidationIssue(
                    QuestIssueSeverity.ERROR,
                    "DUPLICATE_BRANCH_ID",
                    f"State {state.state_id} contains branch {branch_id} {count} times",
                    state.state_id,
                    branch_id,
                ))

    reachable = (
        ({initial_state} & state_ids)
        if is_special_table
        else _reachable_states(dialogue)
    )
    unreachable = state_ids - reachable
    for state_id in sorted(unreachable):
        issues.append(QuestValidationIssue(
            (
                QuestIssueSeverity.INFO
                if is_special_table
                else QuestIssueSeverity.WARNING
            ),
            "UNREACHABLE_STATE",
            (
                f"Special table state {state_id} is outside its main state "
                f"{initial_state}"
                if is_special_table
                else f"State {state_id} cannot be reached from initial state {initial_state}"
            ),
            state_id,
        ))

    terminal_path = (
        set(reachable)
        if is_special_table
        else _states_with_terminal_path(dialogue)
    )
    if not is_special_table:
        for state_id in sorted(reachable - terminal_path):
            issues.append(QuestValidationIssue(
                QuestIssueSeverity.WARNING,
                "NO_TERMINAL_PATH",
                f"Reachable state {state_id} has no path to close or a native action",
                state_id,
            ))
    return DialogueValidationReport(
        issues=tuple(issues),
        reachable_states=frozenset(reachable),
        unreachable_states=frozenset(unreachable),
        states_with_terminal_path=frozenset(terminal_path),
    )


def _fixed_key_slots(values: Iterable[int], label: str) -> Tuple[int, ...]:
    ordered: List[int] = []
    for raw_value in values:
        value = int(raw_value)
        if value != 0 and value not in ordered:
            ordered.append(value)
    result = tuple(ordered)
    if len(result) > 5:
        raise ValueError(f"{label} accepts at most five keys")
    return result + (0,) * (5 - len(result))


def append_special_entry(
    dialogue: rude.RudeDialogue,
    *,
    title: str,
    body: str,
    required_keys: Iterable[int],
    forbidden_keys: Iterable[int] = (),
) -> rude.RudeChoice:
    """Append a stock-shaped Quest Notes or Awards row."""
    npc_nbr = dialogue.metadata.npc_nbr
    if npc_nbr not in {QUEST_NOTES_NPC_NBR, AWARDS_NPC_NBR}:
        raise ValueError("special entries can only be added to NPC997 or NPC999")
    title = str(title).strip()
    if not title:
        raise ValueError("entry title is required")
    body = str(body) if npc_nbr == QUEST_NOTES_NPC_NBR else "blank"
    required = _fixed_key_slots(required_keys, "Required keys")
    forbidden = _fixed_key_slots(forbidden_keys, "Forbidden keys")
    overlap = sorted((set(required) & set(forbidden)) - {0})
    if overlap:
        raise ValueError(
            "entry keys cannot be both required and forbidden: "
            + ", ".join(str(value) for value in overlap)
        )
    if npc_nbr == AWARDS_NPC_NBR and not any(required):
        raise ValueError("an award entry requires at least one visibility key")
    if not any(required) and not any(forbidden):
        raise ValueError("a quest note requires a required or forbidden visibility key")
    try:
        title_size = len(title.encode("latin-1"))
        body_size = len(body.encode("latin-1"))
    except UnicodeEncodeError as exc:
        raise ValueError("entry text must be Latin-1 encodable") from exc
    if title_size > 127:
        raise ValueError("entry title exceeds the runtime limit of 127 Latin-1 bytes")
    if body_size > 255:
        raise ValueError("entry body exceeds the runtime limit of 255 Latin-1 bytes")

    state_id = dialogue.metadata.initial_state
    choice = rude.RudeChoice(
        npc_nbr=npc_nbr,
        state_id=state_id,
        branch_id=dialogue.next_branch_id(state_id),
        player_text=title,
        npc_response=body,
        action=rude.RudeAction.state(state_id),
        conditions=rude.RudeKeyConditions(
            required=required,
            forbidden=forbidden,
        ),
    )
    dialogue.append_choice(choice)
    return choice


def append_quest_note(
    dialogue: rude.RudeDialogue,
    title: str,
    body: str,
    required_keys: Iterable[int],
    forbidden_keys: Iterable[int] = (),
) -> rude.RudeChoice:
    if dialogue.metadata.npc_nbr != QUEST_NOTES_NPC_NBR:
        raise ValueError("quest notes must be added to NPC997")
    return append_special_entry(
        dialogue,
        title=title,
        body=body,
        required_keys=required_keys,
        forbidden_keys=forbidden_keys,
    )


def append_award(
    dialogue: rude.RudeDialogue,
    title: str,
    required_keys: Iterable[int],
    forbidden_keys: Iterable[int] = (),
) -> rude.RudeChoice:
    if dialogue.metadata.npc_nbr != AWARDS_NPC_NBR:
        raise ValueError("awards must be added to NPC999")
    return append_special_entry(
        dialogue,
        title=title,
        body="blank",
        required_keys=required_keys,
        forbidden_keys=forbidden_keys,
    )


__all__ = [
    "AUTO_NOTES_NPC_NBR",
    "AWARDS_NPC_NBR",
    "DialogueValidationReport",
    "QUEST_NOTES_NPC_NBR",
    "QuestIssueSeverity",
    "QuestKeyIndex",
    "QuestKeyRole",
    "QuestKeyUsage",
    "QuestValidationIssue",
    "UnresolvedScriptKeyUsage",
    "append_award",
    "append_quest_note",
    "append_special_entry",
    "build_quest_key_index",
    "index_dialogue",
    "index_script_text",
    "validate_dialogue",
]
