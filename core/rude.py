"""Lossless domain model for Might and Magic IX RUDE dialogue resources.

RUDE dialogue rows are CSV records with 30 columns.  The runtime gives
meaning to every numeric slot, so this module deliberately keeps the raw slot
layout as well as higher-level state, condition, effect, and action views.
Parsed rows retain their original spelling and line ending and are emitted
unchanged until one of their modeled values is edited.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


RUDE_DIALOGUE_COLUMN_COUNT = 30
RUDE_DEFAULT_LINE_ENDING = "\r\n"


class RudeFormatError(ValueError):
    """Raised when a RUDE resource cannot be represented losslessly."""


class RudeActionKind(str, Enum):
    STATE = "state"
    CLOSE = "close"
    NATIVE = "native"


class RudeNativeAction(IntEnum):
    """Native negative actions with dedicated handlers in the MM9 client."""

    SHOP = -2
    TRAINING_HALL = -3
    SKILL_TRAINING = -4
    TRAVEL = -5
    BANK = -6
    INN = -7
    TEMPLE_HEALING = -8
    HIRE_OR_JOIN = -10
    DISMISS = -11
    PROMOTION = -14
    HIRED_NPC_SERVICE = -15
    TEMPLE_DONATION = -16


@dataclass(frozen=True)
class RudeAction:
    """Lossless interpretation of column 5.

    Unknown negative values remain native actions with ``native_action`` set
    to ``None``; their original integer is never discarded.
    """

    value: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", int(self.value))

    @classmethod
    def state(cls, state_id: int) -> "RudeAction":
        state_id = int(state_id)
        if state_id < 0:
            raise ValueError("a RUDE state action cannot target a negative state")
        return cls(state_id)

    @classmethod
    def close(cls) -> "RudeAction":
        return cls(-1)

    @classmethod
    def native(cls, action: int | RudeNativeAction) -> "RudeAction":
        value = int(action)
        if value >= -1:
            raise ValueError("a native RUDE action must be less than -1")
        return cls(value)

    @property
    def kind(self) -> RudeActionKind:
        if self.value == -1:
            return RudeActionKind.CLOSE
        if self.value < -1:
            return RudeActionKind.NATIVE
        return RudeActionKind.STATE

    @property
    def target_state(self) -> Optional[int]:
        return self.value if self.kind is RudeActionKind.STATE else None

    @property
    def native_action(self) -> Optional[RudeNativeAction]:
        if self.kind is not RudeActionKind.NATIVE:
            return None
        try:
            return RudeNativeAction(self.value)
        except ValueError:
            return None


def _int_tuple(values: Iterable[int], size: int, label: str) -> Tuple[int, ...]:
    result = tuple(int(value) for value in values)
    if len(result) != size:
        raise ValueError(f"{label} must contain exactly {size} slots")
    return result


@dataclass(frozen=True)
class RudeKeyConditions:
    """Key predicates and the four reserved condition-side columns."""

    required: Tuple[int, ...] = (0, 0, 0, 0, 0)
    forbidden: Tuple[int, ...] = (0, 0, 0, 0, 0)
    reserved: Tuple[int, ...] = (0, 0, 0, 0)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "required", _int_tuple(self.required, 5, "required keys"))
        object.__setattr__(
            self, "forbidden", _int_tuple(self.forbidden, 5, "forbidden keys"))
        object.__setattr__(
            self, "reserved", _int_tuple(self.reserved, 4, "reserved columns"))

    @property
    def required_keys(self) -> Tuple[int, ...]:
        return tuple(value for value in self.required if value != 0)

    @property
    def forbidden_keys(self) -> Tuple[int, ...]:
        return tuple(value for value in self.forbidden if value != 0)

    def matches(self, active_keys: Iterable[int]) -> bool:
        keys = {int(value) for value in active_keys}
        return (
            all(value in keys for value in self.required_keys)
            and all(value not in keys for value in self.forbidden_keys)
        )


@dataclass(frozen=True)
class RudeKeyEffects:
    """The five grant and five removal slots from a dialogue row.

    Native actions may consume these values as parameters.  Keeping the fixed
    slot tuples intact avoids reinterpreting or dropping those parameters.
    """

    granted: Tuple[int, ...] = (0, 0, 0, 0, 0)
    removed: Tuple[int, ...] = (0, 0, 0, 0, 0)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "granted", _int_tuple(self.granted, 5, "granted keys"))
        object.__setattr__(
            self, "removed", _int_tuple(self.removed, 5, "removed keys"))

    @property
    def granted_keys(self) -> Tuple[int, ...]:
        return tuple(value for value in self.granted if value != 0)

    @property
    def removed_keys(self) -> Tuple[int, ...]:
        return tuple(value for value in self.removed if value != 0)


def _split_physical_line(physical_line: str) -> Tuple[str, str]:
    if physical_line.endswith("\r\n"):
        return physical_line[:-2], "\r\n"
    if physical_line.endswith("\n") or physical_line.endswith("\r"):
        return physical_line[:-1], physical_line[-1:]
    return physical_line, ""


def _physical_csv_lines(text: str) -> List[str]:
    """Split only on CR/LF, not Latin-1 controls such as NEL (0x85)."""
    lines: List[str] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\r":
            index += 1
            if index < len(text) and text[index] == "\n":
                index += 1
            lines.append(text[start:index])
            start = index
            continue
        if char == "\n":
            index += 1
            lines.append(text[start:index])
            start = index
            continue
        index += 1
    if start < len(text):
        lines.append(text[start:])
    return lines


def _csv_records(
    text: str,
    resource: str,
) -> Iterator[Tuple[int, List[str], str, str]]:
    """Yield parsed rows together with their exact original record text.

    RUDE strings can contain embedded CRLFs inside quoted fields, so physical
    ``splitlines()`` records are not sufficient.  ``csv.reader.line_num`` lets
    us map each logical record back to the physical byte-for-byte slice.
    """
    physical_lines = _physical_csv_lines(text)
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    previous_line = 0
    try:
        for fields in reader:
            end_line = reader.line_num
            line_number = previous_line + 1
            physical = "".join(physical_lines[previous_line:end_line])
            source_line, source_ending = _split_physical_line(physical)
            yield line_number, fields, source_line, source_ending
            previous_line = end_line
    except csv.Error as exc:
        line_number = max(previous_line + 1, reader.line_num)
        raise RudeFormatError(
            f"{resource} line {line_number}: invalid CSV: {exc}") from exc


def _parse_int(value: str, resource: str, line_number: int, column: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RudeFormatError(
            f"{resource} line {line_number}, column {column}: "
            f"expected integer, got {value!r}") from exc


def _quote(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _preferred_line_ending(endings: Iterable[str]) -> str:
    counts: Dict[str, int] = {}
    for ending in endings:
        if ending:
            counts[ending] = counts.get(ending, 0) + 1
    if not counts:
        return RUDE_DEFAULT_LINE_ENDING
    return max(counts, key=counts.get)


@dataclass
class RudeDialogueMetadata:
    npc_nbr: int
    name: str
    initial_state: int
    opening_blurb: str

    def __post_init__(self) -> None:
        self.npc_nbr = int(self.npc_nbr)
        self.name = str(self.name)
        self.initial_state = int(self.initial_state)
        self.opening_blurb = str(self.opening_blurb)


@dataclass
class RudeNameEntry:
    npc_nbr: int
    name: str
    _source_line: Optional[str] = field(default=None, repr=False, compare=False)
    _source_ending: Optional[str] = field(default=None, repr=False, compare=False)
    _source_signature: Optional[Tuple[object, ...]] = field(
        default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.npc_nbr = int(self.npc_nbr)
        self.name = str(self.name)

    def _signature(self) -> Tuple[object, ...]:
        return self.npc_nbr, self.name

    def render(self, default_ending: str) -> str:
        if self._source_line is not None and self._source_signature == self._signature():
            line = self._source_line
        else:
            line = f"{self.npc_nbr},{_quote(self.name)}"
        ending = self._source_ending
        return line + (default_ending if ending is None else ending)


@dataclass
class RudeBlurbEntry:
    npc_nbr: int
    initial_state: int
    opening_blurb: str
    _source_line: Optional[str] = field(default=None, repr=False, compare=False)
    _source_ending: Optional[str] = field(default=None, repr=False, compare=False)
    _source_signature: Optional[Tuple[object, ...]] = field(
        default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.npc_nbr = int(self.npc_nbr)
        self.initial_state = int(self.initial_state)
        self.opening_blurb = str(self.opening_blurb)

    def _signature(self) -> Tuple[object, ...]:
        return self.npc_nbr, self.initial_state, self.opening_blurb

    def render(self, default_ending: str) -> str:
        if self._source_line is not None and self._source_signature == self._signature():
            line = self._source_line
        else:
            line = (
                f"{self.npc_nbr},{self.initial_state},"
                f"{_quote(self.opening_blurb)}"
            )
        ending = self._source_ending
        return line + (default_ending if ending is None else ending)


class RudeMetadataCatalog:
    """Lossless model of the NPCNAME and TOPBLURB resource pair."""

    def __init__(
        self,
        names: Sequence[RudeNameEntry] = (),
        blurbs: Sequence[RudeBlurbEntry] = (),
        *,
        name_line_ending: str = RUDE_DEFAULT_LINE_ENDING,
        blurb_line_ending: str = RUDE_DEFAULT_LINE_ENDING,
    ):
        self.names = list(names)
        self.blurbs = list(blurbs)
        self.name_line_ending = name_line_ending or RUDE_DEFAULT_LINE_ENDING
        self.blurb_line_ending = blurb_line_ending or RUDE_DEFAULT_LINE_ENDING

    @classmethod
    def parse(cls, npcname_text: str, topblurb_text: str) -> "RudeMetadataCatalog":
        names: List[RudeNameEntry] = []
        name_endings: List[str] = []
        for line_number, fields, line, ending in _csv_records(
                str(npcname_text), "NPCNAME"):
            if len(fields) != 2:
                raise RudeFormatError(
                    f"NPCNAME line {line_number}: expected 2 columns, got {len(fields)}")
            entry = RudeNameEntry(
                _parse_int(fields[0], "NPCNAME", line_number, 0), fields[1],
                _source_line=line,
                _source_ending=ending,
            )
            entry._source_signature = entry._signature()
            names.append(entry)
            name_endings.append(ending)

        blurbs: List[RudeBlurbEntry] = []
        blurb_endings: List[str] = []
        for line_number, fields, line, ending in _csv_records(
                str(topblurb_text), "TOPBLURB"):
            if len(fields) != 3:
                raise RudeFormatError(
                    f"TOPBLURB line {line_number}: expected 3 columns, got {len(fields)}")
            entry = RudeBlurbEntry(
                _parse_int(fields[0], "TOPBLURB", line_number, 0),
                _parse_int(fields[1], "TOPBLURB", line_number, 1),
                fields[2],
                _source_line=line,
                _source_ending=ending,
            )
            entry._source_signature = entry._signature()
            blurbs.append(entry)
            blurb_endings.append(ending)

        return cls(
            names,
            blurbs,
            name_line_ending=_preferred_line_ending(name_endings),
            blurb_line_ending=_preferred_line_ending(blurb_endings),
        )

    @classmethod
    def from_bytes(
        cls,
        npcname_bytes: bytes,
        topblurb_bytes: bytes,
    ) -> "RudeMetadataCatalog":
        return cls.parse(
            npcname_bytes.decode("latin-1"),
            topblurb_bytes.decode("latin-1"),
        )

    def has_name(self, npc_nbr: int) -> bool:
        npc_nbr = int(npc_nbr)
        return any(entry.npc_nbr == npc_nbr for entry in self.names)

    def has_blurb(self, npc_nbr: int) -> bool:
        npc_nbr = int(npc_nbr)
        return any(entry.npc_nbr == npc_nbr for entry in self.blurbs)

    def metadata_for(self, npc_nbr: int) -> RudeDialogueMetadata:
        npc_nbr = int(npc_nbr)
        name = next((entry for entry in self.names if entry.npc_nbr == npc_nbr), None)
        blurb = next((entry for entry in self.blurbs if entry.npc_nbr == npc_nbr), None)
        missing = []
        if name is None:
            missing.append("NPCNAME")
        if blurb is None:
            missing.append("TOPBLURB")
        if missing:
            raise KeyError(f"NPC{npc_nbr} is missing from {' and '.join(missing)}")
        return RudeDialogueMetadata(
            npc_nbr=npc_nbr,
            name=name.name,
            initial_state=blurb.initial_state,
            opening_blurb=blurb.opening_blurb,
        )

    def upsert(self, metadata: RudeDialogueMetadata) -> None:
        """Replace the first matching rows in place or append new rows."""
        name = next(
            (entry for entry in self.names if entry.npc_nbr == metadata.npc_nbr),
            None,
        )
        if name is None:
            self.names.append(RudeNameEntry(metadata.npc_nbr, metadata.name))
        else:
            name.name = metadata.name

        blurb = next(
            (entry for entry in self.blurbs if entry.npc_nbr == metadata.npc_nbr),
            None,
        )
        if blurb is None:
            self.blurbs.append(RudeBlurbEntry(
                metadata.npc_nbr,
                metadata.initial_state,
                metadata.opening_blurb,
            ))
        else:
            blurb.initial_state = metadata.initial_state
            blurb.opening_blurb = metadata.opening_blurb

    @staticmethod
    def _render_rows(rows: Sequence[object], default_ending: str) -> str:
        rendered: List[str] = []
        last_index = len(rows) - 1
        for index, row in enumerate(rows):
            text = row.render(default_ending)  # type: ignore[attr-defined]
            if index < last_index and not text.endswith(("\r", "\n")):
                text += default_ending
            rendered.append(text)
        return "".join(rendered)

    def to_npcname_text(self) -> str:
        return self._render_rows(self.names, self.name_line_ending)

    def to_topblurb_text(self) -> str:
        return self._render_rows(self.blurbs, self.blurb_line_ending)

    def to_bytes(self) -> Tuple[bytes, bytes]:
        return (
            self.to_npcname_text().encode("latin-1"),
            self.to_topblurb_text().encode("latin-1"),
        )


@dataclass
class RudeChoice:
    npc_nbr: int
    state_id: int
    branch_id: int
    player_text: str
    npc_response: str
    action: RudeAction
    conditions: RudeKeyConditions = field(default_factory=RudeKeyConditions)
    effects: RudeKeyEffects = field(default_factory=RudeKeyEffects)
    _source_line: Optional[str] = field(default=None, repr=False, compare=False)
    _source_ending: Optional[str] = field(default=None, repr=False, compare=False)
    _source_signature: Optional[Tuple[object, ...]] = field(
        default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.npc_nbr = int(self.npc_nbr)
        self.state_id = int(self.state_id)
        self.branch_id = int(self.branch_id)
        self.player_text = str(self.player_text)
        self.npc_response = str(self.npc_response)
        if not isinstance(self.action, RudeAction):
            self.action = RudeAction(int(self.action))
        if not isinstance(self.conditions, RudeKeyConditions):
            self.conditions = RudeKeyConditions(**self.conditions)
        if not isinstance(self.effects, RudeKeyEffects):
            self.effects = RudeKeyEffects(**self.effects)

    @property
    def next_value(self) -> int:
        return self.action.value

    def _signature(self) -> Tuple[object, ...]:
        return (
            self.npc_nbr,
            self.state_id,
            self.branch_id,
            self.player_text,
            self.npc_response,
            self.action.value,
            self.conditions.required,
            self.conditions.reserved,
            self.effects.granted,
            self.conditions.forbidden,
            self.effects.removed,
        )

    @classmethod
    def from_fields(
        cls,
        fields: Sequence[str],
        *,
        resource: str = "NPC<N>",
        line_number: int = 1,
        source_line: Optional[str] = None,
        source_ending: Optional[str] = None,
    ) -> "RudeChoice":
        if len(fields) != RUDE_DIALOGUE_COLUMN_COUNT:
            raise RudeFormatError(
                f"{resource} line {line_number}: expected "
                f"{RUDE_DIALOGUE_COLUMN_COUNT} columns, got {len(fields)}")
        ints = {
            column: _parse_int(fields[column], resource, line_number, column)
            for column in (0, 1, 2, *range(5, RUDE_DIALOGUE_COLUMN_COUNT))
        }
        choice = cls(
            npc_nbr=ints[0],
            state_id=ints[1],
            branch_id=ints[2],
            player_text=fields[3],
            npc_response=fields[4],
            action=RudeAction(ints[5]),
            conditions=RudeKeyConditions(
                required=tuple(ints[column] for column in (6, 8, 10, 12, 14)),
                reserved=tuple(ints[column] for column in (7, 9, 11, 13)),
                forbidden=tuple(ints[column] for column in range(20, 25)),
            ),
            effects=RudeKeyEffects(
                granted=tuple(ints[column] for column in range(15, 20)),
                removed=tuple(ints[column] for column in range(25, 30)),
            ),
            _source_line=source_line,
            _source_ending=source_ending,
        )
        choice._source_signature = choice._signature()
        return choice

    def _canonical_line(self) -> str:
        columns: List[str] = [
            str(self.npc_nbr),
            str(self.state_id),
            str(self.branch_id),
            _quote(self.player_text),
            _quote(self.npc_response),
            str(self.action.value),
        ]
        for index, value in enumerate(self.conditions.required):
            columns.append(str(value))
            if index < len(self.conditions.reserved):
                columns.append(str(self.conditions.reserved[index]))
        columns.extend(str(value) for value in self.effects.granted)
        columns.extend(str(value) for value in self.conditions.forbidden)
        columns.extend(str(value) for value in self.effects.removed)
        if len(columns) != RUDE_DIALOGUE_COLUMN_COUNT:
            raise AssertionError(f"internal RUDE row has {len(columns)} columns")
        return ",".join(columns)

    def render(self, default_ending: str = RUDE_DEFAULT_LINE_ENDING) -> str:
        if self._source_line is not None and self._source_signature == self._signature():
            line = self._source_line
        else:
            line = self._canonical_line()
        ending = self._source_ending
        return line + (default_ending if ending is None else ending)


@dataclass(frozen=True)
class RudeState:
    state_id: int
    choices: Tuple[RudeChoice, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "state_id", int(self.state_id))
        object.__setattr__(self, "choices", tuple(self.choices))
        for choice in self.choices:
            if choice.state_id != self.state_id:
                raise ValueError(
                    f"choice state {choice.state_id} does not match state {self.state_id}")


@dataclass(frozen=True)
class RudeGraphEdge:
    source_state: int
    branch_id: int
    action: RudeAction

    @property
    def target_state(self) -> Optional[int]:
        return self.action.target_state


class RudeDialogue:
    """One NPC resource with source-ordered rows and state projections."""

    def __init__(
        self,
        metadata: RudeDialogueMetadata,
        choices: Sequence[RudeChoice] = (),
        *,
        line_ending: str = RUDE_DEFAULT_LINE_ENDING,
    ):
        self.metadata = metadata
        self._choices = list(choices)
        self.line_ending = line_ending or RUDE_DEFAULT_LINE_ENDING
        self._validate_npc_numbers()

    @classmethod
    def parse(
        cls,
        metadata: RudeDialogueMetadata,
        text: str,
        *,
        resource: Optional[str] = None,
    ) -> "RudeDialogue":
        resource = resource or f"NPC{metadata.npc_nbr}"
        choices: List[RudeChoice] = []
        endings: List[str] = []
        for line_number, fields, line, ending in _csv_records(str(text), resource):
            choice = RudeChoice.from_fields(
                fields,
                resource=resource,
                line_number=line_number,
                source_line=line,
                source_ending=ending,
            )
            choices.append(choice)
            endings.append(ending)
        return cls(
            metadata,
            choices,
            line_ending=_preferred_line_ending(endings),
        )

    @classmethod
    def from_bytes(
        cls,
        metadata: RudeDialogueMetadata,
        data: bytes,
        *,
        resource: Optional[str] = None,
    ) -> "RudeDialogue":
        return cls.parse(metadata, data.decode("latin-1"), resource=resource)

    @classmethod
    def from_states(
        cls,
        metadata: RudeDialogueMetadata,
        states: Sequence[RudeState],
        *,
        line_ending: str = RUDE_DEFAULT_LINE_ENDING,
    ) -> "RudeDialogue":
        choices = [choice for state in states for choice in state.choices]
        return cls(metadata, choices, line_ending=line_ending)

    @property
    def choices_in_file_order(self) -> Tuple[RudeChoice, ...]:
        return tuple(self._choices)

    @property
    def states(self) -> Tuple[RudeState, ...]:
        grouped: Dict[int, List[RudeChoice]] = {}
        for choice in self._choices:
            grouped.setdefault(choice.state_id, []).append(choice)
        return tuple(
            RudeState(state_id, tuple(choices))
            for state_id, choices in grouped.items()
        )

    def state(self, state_id: int) -> Optional[RudeState]:
        state_id = int(state_id)
        return next((state for state in self.states if state.state_id == state_id), None)

    @property
    def graph_edges(self) -> Tuple[RudeGraphEdge, ...]:
        return tuple(
            RudeGraphEdge(choice.state_id, choice.branch_id, choice.action)
            for choice in self._choices
        )

    def next_branch_id(self, state_id: int) -> int:
        state = self.state(state_id)
        if state is None or not state.choices:
            return 1
        return max(choice.branch_id for choice in state.choices) + 1

    def reorder_choice(self, state_id: int, from_index: int, to_index: int) -> None:
        """Reorder a state's menu choices without disturbing other state rows."""
        positions = [
            index for index, choice in enumerate(self._choices)
            if choice.state_id == int(state_id)
        ]
        if not positions:
            raise KeyError(f"dialogue has no state {state_id}")
        ordered = [self._choices[index] for index in positions]
        choice = ordered.pop(from_index)
        ordered.insert(to_index, choice)
        for position, replacement in zip(positions, ordered):
            self._choices[position] = replacement

    def append_choice(self, choice: RudeChoice) -> None:
        if choice.npc_nbr != self.metadata.npc_nbr:
            raise ValueError(
                f"choice NPC{choice.npc_nbr} does not match metadata "
                f"NPC{self.metadata.npc_nbr}")
        matching = [
            index for index, existing in enumerate(self._choices)
            if existing.state_id == choice.state_id
        ]
        insert_at = matching[-1] + 1 if matching else len(self._choices)
        self._choices.insert(insert_at, choice)

    def remove_choice(self, state_id: int, choice_index: int) -> RudeChoice:
        positions = [
            index for index, choice in enumerate(self._choices)
            if choice.state_id == int(state_id)
        ]
        if not positions:
            raise KeyError(f"dialogue has no state {state_id}")
        return self._choices.pop(positions[choice_index])

    def remove_state(self, state_id: int) -> Tuple[RudeChoice, ...]:
        state_id = int(state_id)
        removed = tuple(
            choice for choice in self._choices if choice.state_id == state_id)
        if not removed:
            raise KeyError(f"dialogue has no state {state_id}")
        self._choices = [
            choice for choice in self._choices if choice.state_id != state_id]
        return removed

    def rename_state(
        self,
        old_state_id: int,
        new_state_id: int,
        *,
        update_inbound_actions: bool = True,
    ) -> None:
        old_state_id = int(old_state_id)
        new_state_id = int(new_state_id)
        if old_state_id == new_state_id:
            return
        if self.state(old_state_id) is None:
            raise KeyError(f"dialogue has no state {old_state_id}")
        if self.state(new_state_id) is not None:
            raise ValueError(f"dialogue already has state {new_state_id}")
        for choice in self._choices:
            if choice.state_id == old_state_id:
                choice.state_id = new_state_id
            if (
                update_inbound_actions
                and choice.action.kind is RudeActionKind.STATE
                and choice.action.target_state == old_state_id
            ):
                choice.action = RudeAction.state(new_state_id)
        if self.metadata.initial_state == old_state_id:
            self.metadata.initial_state = new_state_id

    def _validate_npc_numbers(self) -> None:
        for index, choice in enumerate(self._choices, 1):
            if choice.npc_nbr != self.metadata.npc_nbr:
                raise RudeFormatError(
                    f"NPC{self.metadata.npc_nbr} row {index}: column 0 contains "
                    f"NPC{choice.npc_nbr}")

    def to_text(self) -> str:
        rendered: List[str] = []
        last_index = len(self._choices) - 1
        for index, choice in enumerate(self._choices):
            text = choice.render(self.line_ending)
            if index < last_index and not text.endswith(("\r", "\n")):
                text += self.line_ending
            rendered.append(text)
        return "".join(rendered)

    def to_bytes(self) -> bytes:
        return self.to_text().encode("latin-1")


class RudeSimulationError(ValueError):
    """Raised when a mock dialogue session cannot take a requested step."""


@dataclass(frozen=True)
class RudeSimulationResult:
    choice: RudeChoice
    response: str
    action: RudeAction
    granted_keys: Tuple[int, ...]
    removed_keys: Tuple[int, ...]
    active_keys: frozenset[int]
    current_state: Optional[int]
    terminal: bool


class RudeSimulator:
    """Deterministic RUDE flow simulator backed by a mock party key set."""

    def __init__(
        self,
        dialogue: RudeDialogue,
        active_keys: Iterable[int] = (),
    ):
        self.dialogue = dialogue
        self.active_keys = {int(value) for value in active_keys if int(value) != 0}
        self.current_state: Optional[int] = None
        self.terminal = False
        self.last_result: Optional[RudeSimulationResult] = None
        self.reset()

    def reset(self, active_keys: Optional[Iterable[int]] = None) -> None:
        if active_keys is not None:
            self.active_keys = {
                int(value) for value in active_keys if int(value) != 0
            }
        self.current_state = int(self.dialogue.metadata.initial_state)
        self.terminal = False
        self.last_result = None

    @property
    def available_choices(self) -> Tuple[RudeChoice, ...]:
        if self.terminal or self.current_state is None:
            return ()
        state = self.dialogue.state(self.current_state)
        if state is None:
            return ()
        return tuple(
            choice for choice in state.choices
            if choice.conditions.matches(self.active_keys)
        )

    def choose(self, visible_choice_index: int) -> RudeSimulationResult:
        choices = self.available_choices
        try:
            choice = choices[int(visible_choice_index)]
        except (IndexError, ValueError) as exc:
            raise RudeSimulationError(
                f"visible choice index {visible_choice_index} is unavailable") from exc

        granted = choice.effects.granted_keys
        removed = choice.effects.removed_keys
        for key in granted:
            self.active_keys.add(key)
        for key in removed:
            self.active_keys.discard(key)

        action = choice.action
        if action.kind is RudeActionKind.STATE:
            self.current_state = action.target_state
            self.terminal = False
        else:
            self.current_state = None
            self.terminal = True

        result = RudeSimulationResult(
            choice=choice,
            response=choice.npc_response,
            action=action,
            granted_keys=granted,
            removed_keys=removed,
            active_keys=frozenset(self.active_keys),
            current_state=self.current_state,
            terminal=self.terminal,
        )
        self.last_result = result
        return result


def make_simple_dialogue(
    metadata: RudeDialogueMetadata,
    lines: Sequence[Tuple[str, str]],
) -> RudeDialogue:
    """Build the one-state looping dialogue used by the current fresh-NPC UI."""
    zero_conditions = RudeKeyConditions()
    zero_effects = RudeKeyEffects()
    choices: List[RudeChoice] = []
    has_explicit_close = False
    for index, (player_text, npc_response) in enumerate(lines, 1):
        # The fresh-NPC form has historically included ``Goodbye.`` as one
        # of its sample rows.  Treat a plainly authored farewell as the close
        # action; otherwise adding the automatic close below produces two
        # identical menu choices and the first one only loops the state.
        normalized_prompt = str(player_text).strip().rstrip(".!?").casefold()
        is_close = normalized_prompt in {"goodbye", "bye", "farewell"}
        has_explicit_close = has_explicit_close or is_close
        choices.append(RudeChoice(
            npc_nbr=metadata.npc_nbr,
            state_id=metadata.initial_state,
            branch_id=index,
            player_text=player_text,
            npc_response=npc_response,
            action=(
                RudeAction.close()
                if is_close else RudeAction.state(metadata.initial_state)
            ),
            conditions=zero_conditions,
            effects=zero_effects,
        ))
    if not has_explicit_close:
        choices.append(RudeChoice(
            npc_nbr=metadata.npc_nbr,
            state_id=metadata.initial_state,
            branch_id=len(choices) + 1,
            player_text="Goodbye.",
            npc_response="Farewell.",
            action=RudeAction.close(),
            conditions=zero_conditions,
            effects=zero_effects,
        ))
    return RudeDialogue(metadata, choices)


__all__ = [
    "RUDE_DEFAULT_LINE_ENDING",
    "RUDE_DIALOGUE_COLUMN_COUNT",
    "RudeAction",
    "RudeActionKind",
    "RudeBlurbEntry",
    "RudeChoice",
    "RudeDialogue",
    "RudeDialogueMetadata",
    "RudeFormatError",
    "RudeGraphEdge",
    "RudeKeyConditions",
    "RudeKeyEffects",
    "RudeMetadataCatalog",
    "RudeNameEntry",
    "RudeNativeAction",
    "RudeSimulationError",
    "RudeSimulationResult",
    "RudeSimulator",
    "RudeState",
    "make_simple_dialogue",
]
