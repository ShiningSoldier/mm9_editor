"""Read-only Phase-7 acceptance audit for an authored prefab corpus."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple

from catalog.templates import class_template_from_catalog
from core import bsp
from features.prefabs import behavioral
from features.prefabs.graph import DiagnosticSeverity, PrefabAnalysis, SupportState


GOLDEN_BSP_RELATIVE_PATHS = frozenset({
    "Doors/A1_Door.ed",
    "Doors/A1_DoubleDoor.ed",
    "Doors/HexDoor.ed",
    "Doors/RoundSlidingPanelGate.ed",
    "Doors/BreakableDoor.ed",
    "Elevators/GearElevator.ed",
    "Elevators/RopeElevator.ed",
    "Elevators/TowerElevator.ed",
    "Furniture/PipeOrgan.ed",
    "Skybox.ed",
    "Special/FireGlobeFountain.ed",
    "Special/Teleporters.ed",
    "Special/WavyMirrorTeleporters.ed",
    "Traps/FloorSawTrap.ed",
    "Traps/ShooterPanel.ed",
    "Traps/SpikePitSmall.ed",
    "Traps/SpinningBladeTrap.ed",
})


@dataclass(frozen=True)
class CorpusAuditRecord:
    relative_path: str
    source_format: str
    static_state: str
    behavioral_state: str
    effective_state: str
    runtime_object_count: int
    brush_count: int
    reference_count: int
    external_binding_count: int
    missing_resource_count: int
    planned_object_count: int = 0
    planned_bsp_count: int = 0
    generated_script_count: int = 0
    deterministic: bool = True
    diagnostic_codes: Tuple[str, ...] = ()
    failure: str = ""


@dataclass(frozen=True)
class CorpusAuditReport:
    root: str
    records: Tuple[CorpusAuditRecord, ...]

    @property
    def total_files(self) -> int:
        return len(self.records)

    @property
    def failures(self) -> Tuple[CorpusAuditRecord, ...]:
        return tuple(record for record in self.records if record.failure)

    @property
    def passed(self) -> bool:
        return bool(self.records) and not self.failures

    @property
    def state_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for record in self.records:
            counts[record.effective_state] = counts.get(record.effective_state, 0) + 1
        return dict(sorted(counts.items()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "total_files": self.total_files,
            "passed": self.passed,
            "failure_count": len(self.failures),
            "state_counts": self.state_counts,
            "records": [asdict(record) for record in self.records],
        }


def discover_corpus_files(root: str) -> Tuple[str, ...]:
    absolute = os.path.abspath(str(root or ""))
    if not os.path.isdir(absolute):
        raise FileNotFoundError(f"prefab corpus directory was not found: {absolute}")
    paths = []
    for current, dir_names, file_names in os.walk(absolute):
        dir_names[:] = sorted(
            name for name in dir_names if not name.startswith(".")
        )
        for file_name in sorted(file_names):
            if os.path.splitext(file_name)[1].casefold() not in {".ed", ".dat"}:
                continue
            paths.append(os.path.abspath(os.path.join(current, file_name)))
    return tuple(sorted(paths, key=lambda path: os.path.relpath(path, absolute).casefold()))


def audit_prefab_corpus(
    root: str,
    *,
    catalog: Mapping[str, Any],
    resource_exists: Optional[Callable[[str, str], Optional[bool]]] = None,
    script_loader: Optional[Callable[[str], str]] = None,
    target_bsp: Optional[bsp.BspWorld] = None,
    bsp_relative_paths: Optional[Iterable[str]] = None,
    verify_determinism: bool = True,
) -> CorpusAuditReport:
    """Analyze and materialize every prefab without changing game resources."""
    absolute = os.path.abspath(root)
    bsp_paths = (
        None
        if bsp_relative_paths is None
        else {
            str(value).replace("\\", "/").casefold()
            for value in bsp_relative_paths
        }
    )
    records = [
        _audit_one(
            path,
            root=absolute,
            catalog=catalog,
            resource_exists=resource_exists,
            script_loader=script_loader,
            target_bsp=target_bsp,
            compile_bsp=(
                target_bsp is not None
                and (bsp_paths is None or os.path.relpath(path, absolute).replace("\\", "/").casefold() in bsp_paths)
            ),
            verify_determinism=verify_determinism,
        )
        for path in discover_corpus_files(absolute)
    ]
    return CorpusAuditReport(root=absolute, records=tuple(records))


def _audit_one(
    path: str,
    *,
    root: str,
    catalog: Mapping[str, Any],
    resource_exists: Optional[Callable[[str, str], Optional[bool]]],
    script_loader: Optional[Callable[[str], str]],
    target_bsp: Optional[bsp.BspWorld],
    compile_bsp: bool,
    verify_determinism: bool,
) -> CorpusAuditRecord:
    relative = os.path.relpath(path, root).replace("\\", "/")
    try:
        analysis = _analyze(
            path,
            catalog=catalog,
            resource_exists=resource_exists,
            script_loader=script_loader,
        )
    except Exception as exc:
        return CorpusAuditRecord(
            relative_path=relative,
            source_format="unknown",
            static_state=SupportState.BLOCKED.value,
            behavioral_state=SupportState.BLOCKED.value,
            effective_state=SupportState.BLOCKED.value,
            runtime_object_count=0,
            brush_count=0,
            reference_count=0,
            external_binding_count=0,
            missing_resource_count=0,
            deterministic=False,
            failure=f"parse/analyze failed: {exc}",
        )

    deterministic = True
    if verify_determinism:
        try:
            repeated = _analyze(
                path,
                catalog=catalog,
                resource_exists=resource_exists,
                script_loader=script_loader,
            )
            deterministic = _analysis_signature(analysis) == _analysis_signature(repeated)
        except Exception:
            deterministic = False

    runtime = analysis.graph.runtime_objects
    binding_targets = behavioral.required_external_bindings(analysis)
    missing = tuple(
        item for item in analysis.graph.dependencies
        if item.availability == "missing"
    )
    effective_state = (
        analysis.behavioral_state.value
        if runtime
        else analysis.static_state.value
    )
    base = dict(
        relative_path=relative,
        source_format=analysis.graph.source_format,
        static_state=analysis.static_state.value,
        behavioral_state=analysis.behavioral_state.value,
        effective_state=effective_state,
        runtime_object_count=len(runtime),
        brush_count=len(analysis.graph.brushes),
        reference_count=len(analysis.graph.references),
        external_binding_count=len(binding_targets),
        missing_resource_count=len(missing),
        deterministic=deterministic,
        diagnostic_codes=tuple(item.code for item in analysis.diagnostics),
    )
    if not deterministic:
        return CorpusAuditRecord(**base, failure="analysis result changed between identical runs")
    if not runtime:
        failure = "" if analysis.static_state == SupportState.STATIC_READY else (
            "prefab is neither statically nor behaviorally importable"
        )
        return CorpusAuditRecord(**base, failure=failure)

    blockers = [
        item.message for item in analysis.diagnostics_for("behavioral")
        if item.severity == DiagnosticSeverity.BLOCKING
    ]
    if blockers:
        return CorpusAuditRecord(
            **base,
            failure="behavioral import blocked: " + "; ".join(blockers[:4]),
        )

    try:
        external_bindings = {
            name: (
                behavioral.OMIT_PORTAL_BINDING
                if kind == "portal"
                else f"AuditExternal_{_safe_token(name)}"
            )
            for name, kind in binding_targets
        }
        dependency_decisions = {item.path: "provide" for item in missing}
        root_name = _audit_root_name(relative)
        plan = behavioral.build_behavioral_import_plan(
            analysis,
            root_name=root_name,
            target_pos=(128.0, 32.0, -256.0),
            target_yaw=0.375,
            external_bindings=external_bindings,
            dependency_decisions=dependency_decisions,
        )
        plan.require_ready()
        dangling = [
            ref for ref in plan.references
            if ref.binding_kind == "internal" and not ref.target_value
        ]
        if dangling:
            raise ValueError(f"{len(dangling)} internal reference(s) stayed unresolved")

        templates = {}
        for obj in runtime:
            if obj.class_name in templates:
                continue
            template = class_template_from_catalog(catalog, obj.class_name)
            if template is None:
                raise ValueError(f"catalog template missing for {obj.class_name}")
            templates[obj.class_name] = template
        if analysis.graph.brushes and "WorldObject" not in templates:
            template = class_template_from_catalog(catalog, "WorldObject")
            if template is None:
                raise ValueError("catalog template missing for WorldObject")
            templates["WorldObject"] = template

        sources = behavioral.collect_reviewed_script_sources(
            analysis,
            script_loader or _missing_script_loader,
        )
        object_overrides, script_assets = behavioral.build_script_import_assets(
            analysis,
            plan,
            operation_id="corpus_" + hashlib.sha1(relative.encode("utf-8")).hexdigest()[:12],
            script_loader=behavioral.script_loader_from_sources(sources),
        )
        created = behavioral.materialize_behavioral_plan(
            analysis,
            plan,
            class_templates=templates,
            placement_anchor="original_origin",
            object_overrides=object_overrides,
        )
        bsp_count = 0
        bsp_plan = None
        if analysis.graph.brushes and target_bsp is not None and compile_bsp:
            bsp_plan = behavioral.build_behavioral_bsp_import_plan(
                target_bsp,
                analysis,
                plan,
                placement_anchor="original_origin",
            )
            bsp_count = len(bsp_plan.submodels) if bsp_plan is not None else 0
        if compile_bsp:
            parity_issues = behavioral.validate_door_import_parity(
                analysis,
                plan,
                created,
                bsp_plan,
            )
            if parity_issues:
                raise ValueError("door parity: " + "; ".join(parity_issues[:6]))
        return CorpusAuditRecord(
            **base,
            planned_object_count=len(created),
            planned_bsp_count=bsp_count,
            generated_script_count=len(script_assets),
        )
    except Exception as exc:
        return CorpusAuditRecord(**base, failure=f"planning/materialization failed: {exc}")


def _analyze(
    path: str,
    *,
    catalog: Mapping[str, Any],
    resource_exists: Optional[Callable[[str, str], Optional[bool]]],
    script_loader: Optional[Callable[[str], str]],
) -> PrefabAnalysis:
    return behavioral.analyze_prefab(
        path,
        catalog=catalog,
        supported_classes=behavioral.PHASE6_BEHAVIORAL_CLASSES,
        resource_exists=resource_exists,
        allow_scripts=True,
        allowed_script_names=behavioral.PHASE6_REVIEWED_SCRIPTS,
        script_loader=script_loader,
    )


def _analysis_signature(analysis: PrefabAnalysis) -> Tuple[Any, ...]:
    graph = analysis.graph
    return (
        analysis.static_state.value,
        analysis.behavioral_state.value,
        tuple((item.code, item.severity.value, item.message) for item in analysis.diagnostics),
        tuple(
            (obj.index, obj.class_name, obj.source_name, obj.owned_brush_indices)
            for obj in graph.objects
        ),
        tuple(
            (item.index, item.source_name, item.role, item.ownership, item.owner_object_index)
            for item in graph.brushes
        ),
        tuple(
            (item.object_index, item.property_name, item.source_value, item.target_kind)
            for item in graph.references
        ),
        tuple(
            (item.object_index, item.property_name, item.path, item.availability)
            for item in graph.dependencies
        ),
    )


def _audit_root_name(relative_path: str) -> str:
    stem = os.path.splitext(relative_path)[0]
    token = _safe_token(stem)[:48] or "Prefab"
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:8]
    return f"Audit_{token}_{digest}"


def _safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "")).strip("_") or "Target"


def _missing_script_loader(path: str) -> str:
    raise FileNotFoundError(path)
