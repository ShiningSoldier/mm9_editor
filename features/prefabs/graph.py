"""Canonical, source-format-independent prefab analysis structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple


Vec3 = Tuple[float, float, float]


class SupportState(str, Enum):
    STATIC_READY = "static_ready"
    BEHAVIORAL_READY = "behavioral_ready"
    ACTION_REQUIRED = "action_required"
    BLOCKED = "blocked"

    @property
    def label(self) -> str:
        return {
            SupportState.STATIC_READY: "Static ready",
            SupportState.BEHAVIORAL_READY: "Behavioral ready",
            SupportState.ACTION_REQUIRED: "Action required",
            SupportState.BLOCKED: "Blocked",
        }[self]


class DiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ACTION_REQUIRED = "action_required"
    BLOCKING = "blocking"


class SpatialSemantics(str, Enum):
    WORLD_POINT = "world_point"
    DIRECTION = "direction"
    QUATERNION = "quaternion"
    EXTENT = "extent"
    LOCAL_OFFSET = "local_offset"
    LOCAL_VELOCITY = "local_velocity"
    BEHAVIOR_LOCAL = "behavior_local"
    NON_SPATIAL = "non_spatial"


@dataclass(frozen=True)
class PrefabProperty:
    name: str
    type_code: int
    type_name: str
    flags: int
    value: Any


@dataclass(frozen=True)
class PrefabObject:
    index: int
    class_name: str
    source_name: str
    properties: Tuple[PrefabProperty, ...] = ()
    owned_brush_indices: Tuple[int, ...] = ()

    def property_value(self, name: str, default: Any = None) -> Any:
        wanted = str(name).casefold()
        for prop in self.properties:
            if prop.name.casefold() == wanted:
                return prop.value
        return default


@dataclass(frozen=True)
class PrefabBrushGroup:
    index: int
    source_name: str
    role: str
    owner_object_index: Optional[int] = None
    ownership: str = "unowned"
    polygon_count: int = 0


@dataclass(frozen=True)
class PrefabReference:
    object_index: int
    property_name: str
    source_value: str
    target_kind: str
    target_object_index: Optional[int] = None


@dataclass(frozen=True)
class PrefabDependency:
    object_index: int
    property_name: str
    resource_type: str
    path: str
    availability: str = "unchecked"


@dataclass(frozen=True)
class PrefabDiagnostic:
    code: str
    severity: DiagnosticSeverity
    message: str
    object_index: Optional[int] = None
    brush_index: Optional[int] = None


@dataclass(frozen=True)
class PrefabGraph:
    source_path: str
    source_format: str
    source_version: int
    source_fingerprint: str
    objects: Tuple[PrefabObject, ...] = ()
    brushes: Tuple[PrefabBrushGroup, ...] = ()
    references: Tuple[PrefabReference, ...] = ()
    dependencies: Tuple[PrefabDependency, ...] = ()
    diagnostics: Tuple[PrefabDiagnostic, ...] = ()

    @property
    def runtime_objects(self) -> Tuple[PrefabObject, ...]:
        ignored = {"brush", "worldproperties"}
        return tuple(
            obj for obj in self.objects
            if obj.class_name.strip().casefold() not in ignored
        )


@dataclass(frozen=True)
class PrefabAnalysis:
    graph: PrefabGraph
    static_state: SupportState
    behavioral_state: SupportState
    diagnostics: Tuple[PrefabDiagnostic, ...] = ()

    def diagnostics_for(self, mode: str) -> Tuple[PrefabDiagnostic, ...]:
        prefix = "static_" if str(mode).casefold() == "static" else "behavioral_"
        return tuple(
            item for item in self.diagnostics
            if item.code.startswith(prefix) or not item.code.startswith(("static_", "behavioral_"))
        )


@dataclass(frozen=True)
class PlannedObject:
    source_index: int
    class_name: str
    source_name: str
    target_name: str
    synthetic: bool = False


@dataclass(frozen=True)
class PlannedBrush:
    source_index: int
    source_name: str
    target_name: str
    role: str
    owner_target_name: str = ""


@dataclass(frozen=True)
class PlannedReference:
    object_index: int
    property_name: str
    source_value: str
    target_value: str
    binding_kind: str


@dataclass(frozen=True)
class BehavioralPrefabImportPlan:
    source_path: str
    source_fingerprint: str
    root_name: str
    support_state: SupportState
    target_pos: Vec3
    target_yaw: float
    objects: Tuple[PlannedObject, ...] = ()
    brushes: Tuple[PlannedBrush, ...] = ()
    references: Tuple[PlannedReference, ...] = ()
    dependencies: Tuple[PrefabDependency, ...] = ()
    diagnostics: Tuple[PrefabDiagnostic, ...] = ()
    name_map: Tuple[Tuple[str, str], ...] = ()
    planner_version: int = 1

    @property
    def ready(self) -> bool:
        return self.support_state == SupportState.BEHAVIORAL_READY

    def require_ready(self) -> None:
        if self.ready:
            return
        messages = [
            item.message for item in self.diagnostics
            if item.severity in {DiagnosticSeverity.BLOCKING, DiagnosticSeverity.ACTION_REQUIRED}
        ]
        detail = "; ".join(messages[:4]) or self.support_state.label
        raise ValueError(f"behavioral prefab import is not ready: {detail}")

    def manifest_dict(self) -> Dict[str, Any]:
        return {
            "planner_version": self.planner_version,
            "source_path": self.source_path,
            "source_fingerprint": self.source_fingerprint,
            "root_name": self.root_name,
            "support_state": self.support_state.value,
            "target_pos": list(self.target_pos),
            "target_yaw": float(self.target_yaw),
            "objects": [item.__dict__ for item in self.objects],
            "brushes": [item.__dict__ for item in self.brushes],
            "references": [item.__dict__ for item in self.references],
            "dependencies": [item.__dict__ for item in self.dependencies],
            "diagnostics": [
                {**item.__dict__, "severity": item.severity.value}
                for item in self.diagnostics
            ],
            "name_map": [list(item) for item in self.name_map],
        }


def diagnostics_with_severity(
    diagnostics: Iterable[PrefabDiagnostic],
    severities: Sequence[DiagnosticSeverity],
) -> Tuple[PrefabDiagnostic, ...]:
    wanted = set(severities)
    return tuple(item for item in diagnostics if item.severity in wanted)
