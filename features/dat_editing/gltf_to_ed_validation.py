"""Phase 8 validation pipeline for Phase 7 glTF/GLB -> ED artifacts.

Routine use rechecks the immutable ED identity and reopens it with the
maintained legacy reader.  DEdit, Processor, compiled-DAT, and in-game stages
run only when their evidence/options are explicitly supplied.  A schema-v1
manifest can be resumed only while the ED SHA-256 remains unchanged.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import struct
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from features.dat_editing import gltf_ed_assembly
from features.dat_editing import gltf_to_ed_service
from features.dat_editing import legacy_ed


PASS = "pass"
FAILED = "failed"
BLOCKED = "blocked"
NOT_RUN = "not_run"
NOT_APPLICABLE = "not_applicable"

_EXTERNAL_STAGES = ("dedit", "processor", "compiled_dat", "in_game")


@dataclass(frozen=True)
class ValidationStage:
    state: str
    evidence: Dict[str, object] = field(default_factory=dict)
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "state": self.state,
            "evidence": copy.deepcopy(self.evidence),
            "blockers": list(self.blockers),
            "cautions": list(self.cautions),
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ValidationStage":
        if not isinstance(value, dict):
            raise ValueError("validation stage must be an object")
        evidence = value.get("evidence", {})
        if not isinstance(evidence, dict):
            raise ValueError("validation stage evidence must be an object")
        return cls(
            state=str(value.get("state") or NOT_RUN),
            evidence=copy.deepcopy(evidence),
            blockers=tuple(str(item) for item in value.get("blockers", []) or []),
            cautions=tuple(str(item) for item in value.get("cautions", []) or []),
            notes=tuple(str(item) for item in value.get("notes", []) or []),
        )


@dataclass(frozen=True)
class GltfToEdValidationOptions:
    manifest_path: str = ""
    resume: bool = True
    update_conversion_report: bool = True

    dedit_opened: Optional[bool] = None
    dedit_saved: Optional[bool] = None
    dedit_saved_ed_path: str = ""
    dedit_evidence_paths: Tuple[str, ...] = ()
    dedit_notes: Tuple[str, ...] = ()

    run_processor: bool = False
    processor_path: str = ""
    processor_work_dir: str = ""
    processor_project_dir: str = ""
    processor_timeout_seconds: float = 900.0
    processor_log_path: str = ""
    compiled_dat_path: str = ""

    in_game_fresh_load: Optional[bool] = None
    in_game_visuals_ok: Optional[bool] = None
    in_game_collision_ok: Optional[bool] = None
    in_game_evidence_paths: Tuple[str, ...] = ()
    in_game_notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class GltfToEdValidationReport:
    status: str
    conversion: Dict[str, object]
    ed: Dict[str, object]
    stages: Dict[str, ValidationStage]
    artifacts: Dict[str, object]
    resume: Dict[str, object]
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()

    @property
    def json_manifest_path(self) -> str:
        return str(self.artifacts.get("json_manifest_path") or "")

    @property
    def text_manifest_path(self) -> str:
        return str(self.artifacts.get("text_manifest_path") or "")

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": 1,
            "kind": "mm9_gltf_to_ed_validation",
            "status": self.status,
            "conversion": copy.deepcopy(self.conversion),
            "ed": copy.deepcopy(self.ed),
            "stages": {
                name: stage.to_dict()
                for name, stage in self.stages.items()
            },
            "artifacts": copy.deepcopy(self.artifacts),
            "resume": copy.deepcopy(self.resume),
            "blockers": list(self.blockers),
            "cautions": list(self.cautions),
            "notes": list(self.notes),
        }


ProcessorRunner = Callable[..., object]
DatValidator = Callable[..., object]


def validation_paths_for_report(
    conversion_report_path: str,
    manifest_path: str = "",
) -> Tuple[str, str]:
    if manifest_path:
        json_path = os.path.abspath(os.fspath(manifest_path))
        if os.path.splitext(json_path)[1].lower() != ".json":
            raise ValueError("validation manifest path must use the .json extension")
        return json_path, os.path.splitext(json_path)[0] + ".txt"
    absolute = os.path.abspath(os.fspath(conversion_report_path))
    suffix = ".gltf_to_ed_report.json"
    if absolute.lower().endswith(suffix):
        stem = absolute[:-len(suffix)]
    else:
        stem = os.path.splitext(absolute)[0]
    return stem + ".gltf_to_ed_validation.json", stem + ".gltf_to_ed_validation.txt"


def validate_gltf_to_ed(
    conversion_report_path: str,
    *,
    options: Optional[GltfToEdValidationOptions] = None,
    processor_runner: Optional[ProcessorRunner] = None,
    dat_validator: Optional[DatValidator] = None,
) -> GltfToEdValidationReport:
    """Run/resume Phase 8 without invoking external tools unless requested."""
    selected = options or GltfToEdValidationOptions()
    report_path = os.path.abspath(os.fspath(conversion_report_path))
    json_manifest, text_manifest = validation_paths_for_report(
        report_path, selected.manifest_path
    )
    artifacts = {
        "json_manifest_path": json_manifest,
        "text_manifest_path": text_manifest,
        "conversion_report_updated": False,
        "written": False,
    }
    conversion_info: Dict[str, object] = {
        "report_path": report_path,
        "status": None,
        "output_mode": None,
    }
    stages = _empty_stages()
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = []
    ed_info = _empty_ed_report()
    prior, prior_error = _load_prior_manifest(json_manifest)
    if prior_error:
        blockers.append(prior_error)

    try:
        conversion_report = gltf_to_ed_service.load_gltf_to_ed_conversion_report(report_path)
        conversion_info.update({
            "status": conversion_report.status,
            "output_mode": conversion_report.options.get("output_mode"),
        })
        if conversion_report.status not in {"ready_prefab", "ready_full_world"}:
            raise ValueError(
                f"conversion report status is {conversion_report.status!r}; expected a ready artifact"
            )
        if os.path.abspath(conversion_report.json_report_path) != report_path:
            raise ValueError(
                "supplied report path does not match the JSON report path recorded by Phase 7"
            )
        stages["conversion_report"] = ValidationStage(PASS, {
            "schema_version": 1,
            "kind": "mm9_gltf_to_ed_conversion",
            "status": conversion_report.status,
        })
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        conversion_report = None
        message = f"conversion report could not be accepted: {exc}"
        stages["conversion_report"] = ValidationStage(FAILED, blockers=(message,))
        blockers.append(message)

    if conversion_report is not None:
        ed_path = os.path.abspath(str(conversion_report.output.get("final_path") or ""))
        expected = conversion_report.output
        ed_info["path"] = ed_path
        try:
            with open(ed_path, "rb") as stream:
                ed_bytes = stream.read()
            actual_sha = hashlib.sha256(ed_bytes).hexdigest()
            actual_version = struct.unpack_from("<I", ed_bytes, 0)[0] if len(ed_bytes) >= 4 else None
            ed_info.update({
                "sha256": actual_sha,
                "byte_size": len(ed_bytes),
                "version": actual_version,
                "expected_sha256": expected.get("sha256"),
                "expected_byte_size": expected.get("byte_size"),
                "expected_version": expected.get("ed_version"),
            })
            identity_mismatches = []
            _compare(identity_mismatches, "SHA-256", actual_sha, expected.get("sha256"))
            _compare(identity_mismatches, "byte size", len(ed_bytes), expected.get("byte_size"))
            _compare(identity_mismatches, "ED version", actual_version, expected.get("ed_version"))
            if identity_mismatches:
                stages["ed_integrity"] = ValidationStage(
                    FAILED,
                    evidence=copy.deepcopy(ed_info),
                    blockers=tuple(identity_mismatches),
                )
                blockers.extend(identity_mismatches)
            else:
                stages["ed_integrity"] = ValidationStage(PASS, copy.deepcopy(ed_info))
        except (OSError, struct.error) as exc:
            ed_bytes = b""
            message = f"ED artifact could not be read: {exc}"
            stages["ed_integrity"] = ValidationStage(FAILED, blockers=(message,))
            blockers.append(message)

        if stages["ed_integrity"].state == PASS:
            roundtrip = _validate_ed_roundtrip(ed_bytes, ed_path, conversion_report.output)
            stages["ed_roundtrip"] = roundtrip
            if roundtrip.state == FAILED:
                blockers.extend(roundtrip.blockers)
        else:
            stages["ed_roundtrip"] = ValidationStage(
                BLOCKED, blockers=("ED integrity must pass before reader round-trip",)
            )

    same_ed = bool(
        prior
        and ed_info.get("sha256")
        and prior.get("ed", {}).get("sha256") == ed_info.get("sha256")
    )
    resume_info = {
        "requested": bool(selected.resume),
        "used": bool(selected.resume and same_ed),
        "prior_manifest_path": json_manifest if prior else None,
        "invalidated_reason": None,
    }
    if prior and selected.resume and not same_ed:
        resume_info["invalidated_reason"] = "ED SHA-256 changed; external evidence was not reused"
        cautions.append(str(resume_info["invalidated_reason"]))
    if prior and selected.resume and same_ed:
        _restore_external_stages(stages, prior)

    output_mode = str(conversion_info.get("output_mode") or "")
    automatic_ready = all(
        stages[name].state == PASS
        for name in ("conversion_report", "ed_integrity", "ed_roundtrip")
    ) and not prior_error
    if automatic_ready:
        stages["dedit"] = _dedit_stage(selected, ed_info, stages["dedit"])
        if output_mode == gltf_ed_assembly.PREFAB:
            for name in ("processor", "compiled_dat", "in_game"):
                stages[name] = ValidationStage(
                    NOT_APPLICABLE,
                    notes=("stage applies only to full_world output",),
                )
            if _full_world_evidence_requested(selected):
                cautions.append("Processor/DAT/in-game evidence was ignored for prefab output")
        else:
            compiled_path = str(selected.compiled_dat_path or "")
            stages["processor"], generated_dat = _processor_stage(
                selected,
                ed_path=str(ed_info.get("path") or ""),
                previous=stages["processor"],
                runner=processor_runner,
            )
            if generated_dat:
                compiled_path = generated_dat
            stages["compiled_dat"] = _compiled_dat_stage(
                compiled_path,
                previous=stages["compiled_dat"],
                validator=dat_validator,
            )
            stages["in_game"] = _in_game_stage(
                selected,
                previous=stages["in_game"],
                compiled_stage=stages["compiled_dat"],
            )

    for name, stage in stages.items():
        if name in {"conversion_report", "ed_integrity", "ed_roundtrip"}:
            continue
        blockers.extend(stage.blockers)
        cautions.extend(stage.cautions)
        notes.extend(stage.notes)

    status = _validation_status(stages, output_mode)
    result = GltfToEdValidationReport(
        status=status,
        conversion=conversion_info,
        ed=ed_info,
        stages=stages,
        artifacts=artifacts,
        resume=resume_info,
        blockers=_unique(blockers),
        cautions=_unique(cautions),
        notes=_unique(notes),
    )
    if prior_error:
        return replace(result, status="write_failed")
    return _commit_validation_result(result, conversion_report, report_path, selected)


def format_gltf_to_ed_validation_report(report: GltfToEdValidationReport) -> str:
    lines = [
        "glTF/GLB to DEDit ED validation",
        f"status: {report.status}",
        f"conversion report: {report.conversion.get('report_path') or '<none>'}",
        f"ED: {report.ed.get('path') or '<none>'}",
        f"ED SHA-256: {report.ed.get('sha256') or '<unavailable>'}",
        "stages:",
    ]
    for name in (
        "conversion_report", "ed_integrity", "ed_roundtrip", "dedit",
        "processor", "compiled_dat", "in_game",
    ):
        lines.append(f"- {name}: {report.stages[name].state}")
    for label, values in (
        ("blockers", report.blockers),
        ("cautions", report.cautions),
        ("notes", report.notes),
    ):
        if values:
            lines.append(label + ":")
            lines.extend(f"- {item}" for item in values)
    return "\n".join(lines) + "\n"


def _empty_stages() -> Dict[str, ValidationStage]:
    return {
        name: ValidationStage(NOT_RUN)
        for name in (
            "conversion_report", "ed_integrity", "ed_roundtrip", "dedit",
            "processor", "compiled_dat", "in_game",
        )
    }


def _empty_ed_report() -> Dict[str, object]:
    return {
        "path": None,
        "sha256": None,
        "byte_size": 0,
        "version": None,
        "expected_sha256": None,
        "expected_byte_size": 0,
        "expected_version": legacy_ed.LEGACY_ED_VERSION,
    }


def _validate_ed_roundtrip(
    data: bytes,
    path: str,
    expected: Mapping[str, object],
) -> ValidationStage:
    try:
        analysis = legacy_ed.analyze_legacy_ed_bytes(data, source_path=path)
    except Exception as exc:
        return ValidationStage(FAILED, blockers=(f"ED reader rejected artifact: {exc}",))
    scene = analysis.geometry_scene
    models = scene.mesh_models()
    recovered = {
        "brush_count": int(scene.metadata.get("recovered_brush_count", 0) or 0),
        "surface_count": int(scene.metadata.get("recovered_polygon_count", 0) or 0),
        "point_count": sum(len(model.points) for model in models),
        "node_count": _node_count(analysis.node_tree),
        "object_count": analysis.object_scan.object_count,
        "brush_names": list(analysis.node_layout.brush_names),
        "node_layout_kind": analysis.node_layout.node_layout_kind,
        "wrapper_kind": analysis.node_layout.wrapper or gltf_ed_assembly.UNCOMPRESSED_NAMED_GROUP,
    }
    mismatches: List[str] = []
    for key in ("brush_count", "surface_count", "point_count", "node_count", "object_count"):
        _compare(mismatches, key.replace("_", " "), recovered[key], expected.get(key))
    _compare(mismatches, "Brush names", recovered["brush_names"], expected.get("brush_names"))
    _compare(mismatches, "wrapper kind", recovered["wrapper_kind"], expected.get("wrapper_kind"))
    return ValidationStage(
        PASS if not mismatches else FAILED,
        evidence={
            "expected": {
                key: copy.deepcopy(expected.get(key))
                for key in (
                    "brush_count", "surface_count", "point_count", "node_count",
                    "object_count", "brush_names", "wrapper_kind",
                )
            },
            "recovered": recovered,
        },
        blockers=tuple(mismatches),
    )


def _dedit_stage(
    options: GltfToEdValidationOptions,
    ed_info: Mapping[str, object],
    previous: ValidationStage,
) -> ValidationStage:
    supplied = options.dedit_opened is not None or options.dedit_saved is not None
    supplied = supplied or bool(options.dedit_saved_ed_path or options.dedit_evidence_paths)
    if not supplied:
        return previous
    saved_ed = _file_evidence(
        options.dedit_saved_ed_path or str(ed_info.get("path") or "")
    )
    evidence = {
        "opened": options.dedit_opened,
        "saved": options.dedit_saved,
        "saved_ed": saved_ed,
        "attachments": _evidence_files(options.dedit_evidence_paths),
    }
    if options.dedit_opened is None or options.dedit_saved is None:
        return ValidationStage(
            BLOCKED, evidence, blockers=("DEdit evidence requires both open and save results",),
            notes=tuple(options.dedit_notes),
        )
    if options.dedit_saved and not saved_ed["exists"]:
        return ValidationStage(
            BLOCKED, evidence,
            blockers=("DEdit reported a save but the saved ED evidence file was not found",),
            notes=tuple(options.dedit_notes),
        )
    state = PASS if options.dedit_opened and options.dedit_saved else FAILED
    blockers = () if state == PASS else ("DEdit open/save validation failed",)
    return ValidationStage(state, evidence, blockers=blockers, notes=tuple(options.dedit_notes))


def _processor_stage(
    options: GltfToEdValidationOptions,
    *,
    ed_path: str,
    previous: ValidationStage,
    runner: Optional[ProcessorRunner],
) -> Tuple[ValidationStage, str]:
    if options.run_processor:
        if not options.processor_path or not options.processor_work_dir:
            return ValidationStage(
                BLOCKED,
                blockers=("--run-processor requires processor_path and processor_work_dir",),
            ), ""
        if runner is None:
            from features.dat_editing import compiler_strategy
            runner = compiler_strategy.run_black_box_ed_to_dat_harness
        try:
            harness = runner(
                processor_path=os.path.abspath(options.processor_path),
                source_ed_path=ed_path,
                work_dir=os.path.abspath(options.processor_work_dir),
                processor_project_dir=(
                    os.path.abspath(options.processor_project_dir)
                    if options.processor_project_dir else None
                ),
                timeout_seconds=float(options.processor_timeout_seconds),
                preseed_reference_dat=False,
            )
        except Exception as exc:
            return ValidationStage(FAILED, blockers=(f"Processor harness failed: {exc}",)), ""
        output_dat = str(getattr(harness, "output_dat_path", "") or "")
        harness_status = str(getattr(harness, "status", "unknown"))
        evidence = {
            "mode": "executed",
            "harness_status": harness_status,
            "processor_path": str(getattr(harness, "processor_path", options.processor_path)),
            "work_dir": str(getattr(harness, "work_dir", options.processor_work_dir)),
            "output_dat": _file_evidence(output_dat),
            "returncode": getattr(harness, "returncode", None),
            "elapsed_seconds": getattr(harness, "elapsed_seconds", None),
            "stdout": _file_evidence(str(getattr(harness, "stdout_path", "") or "")),
            "stderr": _file_evidence(str(getattr(harness, "stderr_path", "") or "")),
            "logs": _evidence_files(tuple(getattr(harness, "log_paths", ()) or ())),
        }
        passed = harness_status in {
            "compiled", "compiled_and_compared", "compiled_with_semantic_differences",
        } and bool(output_dat) and os.path.isfile(output_dat)
        blockers = () if passed else (f"Processor harness status was {harness_status}",)
        return ValidationStage(
            PASS if passed else FAILED,
            evidence,
            blockers=blockers,
            notes=tuple(str(item) for item in getattr(harness, "notes", ()) or ()),
        ), output_dat if passed else ""

    log_path = str(options.processor_log_path or "")
    dat_path = str(options.compiled_dat_path or "")
    if not log_path:
        return previous, ""
    if not dat_path:
        return ValidationStage(
            BLOCKED,
            evidence={"log": _file_evidence(log_path)},
            blockers=("Processor log evidence requires a compiled DAT path",),
        ), ""
    from features.dat_editing import compiler_strategy
    summary = compiler_strategy.parse_processor_log_summary(log_path)
    evidence = {
        "mode": "existing_evidence",
        "log": _file_evidence(log_path),
        "compiled_dat": _file_evidence(dat_path),
        "log_status": summary.status,
        "problem_brush_count": summary.problem_brush_count,
        "warning_counts": dict(summary.warning_counts),
        "input_polygon_count": summary.input_polygon_count,
        "output_polygon_count": summary.output_polygon_count,
        "object_count": summary.object_count,
    }
    blockers = []
    if summary.status != "loaded":
        blockers.append(f"Processor log status was {summary.status}")
    if not os.path.isfile(os.path.abspath(dat_path)):
        blockers.append("compiled DAT evidence file was not found")
    if summary.problem_brush_count not in {None, 0}:
        blockers.append(f"Processor reported {summary.problem_brush_count} problem brush(es)")
    return ValidationStage(
        PASS if not blockers else FAILED,
        evidence,
        blockers=tuple(blockers),
        cautions=tuple(summary.warnings),
        notes=tuple(summary.notes),
    ), os.path.abspath(dat_path) if not blockers else ""


def _compiled_dat_stage(
    path: str,
    *,
    previous: ValidationStage,
    validator: Optional[DatValidator],
) -> ValidationStage:
    if not path:
        return previous
    absolute = os.path.abspath(os.fspath(path))
    try:
        with open(absolute, "rb") as stream:
            data = stream.read()
    except OSError as exc:
        return ValidationStage(FAILED, blockers=(f"compiled DAT could not be read: {exc}",))
    evidence = _file_evidence(absolute)
    version = struct.unpack_from("<I", data, 0)[0] if len(data) >= 4 else None
    evidence["version"] = version
    if version != 66:
        return ValidationStage(
            FAILED, evidence, blockers=(f"compiled DAT version is {version!r}; expected 66",)
        )
    if validator is None:
        from features.dat_editing import output_validation
        validator = output_validation.validate_geometry_dat
    try:
        validated = validator(data)
    except Exception as exc:
        return ValidationStage(
            FAILED, evidence, blockers=(f"maintained DAT validation raised: {exc}",)
        )
    errors = tuple(str(item) for item in getattr(validated, "errors", ()) or ())
    warnings = tuple(str(item) for item in getattr(validated, "warnings", ()) or ())
    evidence.update({
        "object_count": getattr(validated, "object_count", None),
        "world_model_count": (
            len(getattr(getattr(validated, "parsed_bsp", None), "world_models", ()) or ())
        ),
    })
    return ValidationStage(
        PASS if not errors else FAILED,
        evidence,
        blockers=errors,
        cautions=warnings,
    )


def _in_game_stage(
    options: GltfToEdValidationOptions,
    *,
    previous: ValidationStage,
    compiled_stage: ValidationStage,
) -> ValidationStage:
    values = (
        options.in_game_fresh_load,
        options.in_game_visuals_ok,
        options.in_game_collision_ok,
    )
    supplied = any(value is not None for value in values) or bool(options.in_game_evidence_paths)
    if not supplied:
        previous_dat_sha = previous.evidence.get("compiled_dat_sha256")
        current_dat_sha = compiled_stage.evidence.get("sha256")
        if previous.state == PASS and (
            compiled_stage.state != PASS
            or not previous_dat_sha
            or previous_dat_sha != current_dat_sha
        ):
            return ValidationStage(
                BLOCKED,
                blockers=("compiled DAT evidence changed; in-game evidence must be recorded again",),
            )
        return previous
    evidence = {
        "fresh_load": options.in_game_fresh_load,
        "visuals_ok": options.in_game_visuals_ok,
        "collision_ok": options.in_game_collision_ok,
        "compiled_dat_sha256": compiled_stage.evidence.get("sha256"),
        "attachments": _evidence_files(options.in_game_evidence_paths),
    }
    if compiled_stage.state != PASS:
        return ValidationStage(
            BLOCKED, evidence,
            blockers=("compiled DAT validation must pass before in-game evidence is accepted",),
            notes=tuple(options.in_game_notes),
        )
    if any(value is None for value in values):
        return ValidationStage(
            BLOCKED, evidence,
            blockers=("in-game evidence requires fresh-load, visuals, and collision results",),
            notes=tuple(options.in_game_notes),
        )
    state = PASS if all(values) else FAILED
    blockers = () if state == PASS else ("in-game validation failed",)
    return ValidationStage(state, evidence, blockers=blockers, notes=tuple(options.in_game_notes))


def _validation_status(stages: Mapping[str, ValidationStage], output_mode: str) -> str:
    states = [stage.state for stage in stages.values()]
    if FAILED in states:
        return "validation_failed"
    if BLOCKED in states:
        return "blocked"
    if output_mode == gltf_ed_assembly.PREFAB:
        return "validated_prefab" if stages["dedit"].state == PASS else "awaiting_external_validation"
    if all(stages[name].state == PASS for name in _EXTERNAL_STAGES):
        return "validated_full_world"
    return "awaiting_external_validation"


def _commit_validation_result(
    result: GltfToEdValidationReport,
    conversion_report: Optional[gltf_to_ed_service.GltfToEdConversionReport],
    report_path: str,
    options: GltfToEdValidationOptions,
) -> GltfToEdValidationReport:
    prepared = replace(result, artifacts={**result.artifacts, "written": True})
    payloads: List[Tuple[str, bytes]] = [
        (prepared.json_manifest_path, _validation_json_bytes(prepared)),
        (prepared.text_manifest_path, format_gltf_to_ed_validation_report(prepared).encode("utf-8")),
    ]
    if options.update_conversion_report and conversion_report is not None:
        validation = dict(conversion_report.validation)
        for name in _EXTERNAL_STAGES:
            validation[name] = prepared.stages[name].state
        validation["phase8_status"] = prepared.status
        validation["phase8_manifest_path"] = prepared.json_manifest_path
        updated = replace(conversion_report, validation=validation)
        stored_report_path = os.path.abspath(updated.json_report_path)
        if stored_report_path != report_path:
            return replace(
                result,
                status="write_failed",
                blockers=_unique(result.blockers + (
                    "supplied conversion report path does not match its recorded JSON report path",
                )),
            )
        payloads.extend((
            (report_path, gltf_to_ed_service.conversion_report_json_bytes(updated)),
            (updated.text_report_path, gltf_to_ed_service.conversion_report_text_bytes(updated)),
        ))
        prepared = replace(prepared, artifacts={
            **prepared.artifacts,
            "conversion_report_updated": True,
        })
        payloads[0] = (prepared.json_manifest_path, _validation_json_bytes(prepared))
        payloads[1] = (
            prepared.text_manifest_path,
            format_gltf_to_ed_validation_report(prepared).encode("utf-8"),
        )
    try:
        gltf_to_ed_service.commit_artifacts(payloads, overwrite=True)
        return prepared
    except OSError as exc:
        return replace(
            result,
            status="write_failed",
            blockers=_unique(result.blockers + (f"validation artifacts could not be written: {exc}",)),
        )


def _load_prior_manifest(path: str) -> Tuple[Optional[Dict[str, object]], str]:
    if not os.path.exists(path):
        return None, ""
    try:
        with open(path, "r", encoding="utf-8-sig") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"existing validation manifest could not be read safely: {exc}"
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return None, "existing validation manifest has an unsupported schema"
    if value.get("kind") != "mm9_gltf_to_ed_validation":
        return None, "refusing to overwrite a JSON file that is not a Phase 8 manifest"
    return value, ""


def _restore_external_stages(
    stages: Dict[str, ValidationStage],
    prior: Mapping[str, object],
) -> None:
    values = prior.get("stages", {})
    if not isinstance(values, dict):
        return
    for name in _EXTERNAL_STAGES:
        if name not in values:
            continue
        try:
            restored = ValidationStage.from_dict(values[name])
        except ValueError:
            continue
        changed = _changed_evidence_paths(restored.evidence)
        if restored.state == PASS and changed:
            stages[name] = ValidationStage(
                BLOCKED,
                evidence=restored.evidence,
                blockers=(
                    "recorded evidence file changed or disappeared: " + ", ".join(changed),
                ),
            )
        else:
            stages[name] = restored


def _changed_evidence_paths(value: object) -> Tuple[str, ...]:
    changed: List[str] = []

    def visit(item: object) -> None:
        if isinstance(item, dict):
            path = item.get("path")
            expected_sha = item.get("sha256")
            if path and expected_sha:
                current = _file_evidence(str(path))
                if current.get("sha256") != expected_sha:
                    changed.append(str(path))
            for nested in item.values():
                if isinstance(nested, (dict, list, tuple)):
                    visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)
    return _unique(changed)


def _file_evidence(path: str) -> Dict[str, object]:
    absolute = os.path.abspath(os.fspath(path)) if path else ""
    result: Dict[str, object] = {
        "path": absolute or None,
        "exists": False,
        "byte_size": 0,
        "sha256": None,
    }
    if not absolute:
        return result
    try:
        with open(absolute, "rb") as stream:
            data = stream.read()
    except OSError:
        return result
    result.update({
        "exists": True,
        "byte_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    })
    return result


def _evidence_files(paths: Sequence[str]) -> List[Dict[str, object]]:
    return [_file_evidence(str(path)) for path in paths]


def _node_count(root: Optional[legacy_ed.LegacyEdNode]) -> int:
    if root is None:
        return 0
    return 1 + sum(_node_count(child) for child in root.children)


def _compare(mismatches: List[str], label: str, actual: object, expected: object) -> None:
    if actual != expected:
        mismatches.append(f"{label} is {actual!r}; expected {expected!r}")


def _full_world_evidence_requested(options: GltfToEdValidationOptions) -> bool:
    return bool(
        options.run_processor
        or options.processor_log_path
        or options.compiled_dat_path
        or any(value is not None for value in (
            options.in_game_fresh_load,
            options.in_game_visuals_ok,
            options.in_game_collision_ok,
        ))
        or options.in_game_evidence_paths
    )


def _validation_json_bytes(report: GltfToEdValidationReport) -> bytes:
    return (
        json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _unique(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


__all__ = [
    "BLOCKED",
    "FAILED",
    "GltfToEdValidationOptions",
    "GltfToEdValidationReport",
    "NOT_APPLICABLE",
    "NOT_RUN",
    "PASS",
    "ValidationStage",
    "format_gltf_to_ed_validation_report",
    "validate_gltf_to_ed",
    "validation_paths_for_report",
]
