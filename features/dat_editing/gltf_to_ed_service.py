"""Phase 7 service and reports for the glTF/GLB -> DEDit ED flow.

The service composes the maintained Phase 2-6 layers, keeps all preflight work
in memory, and commits an ED plus its JSON/text reports only after assembly and
reader round-trip succeed.  Blocked conversions still produce reports when the
report targets are safe and writable.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from features.dat_editing import geometry_scene
from features.dat_editing import gltf_brushes
from features.dat_editing import gltf_ed_assembly
from features.dat_editing import gltf_import
from features.dat_editing import gltf_materials
from features.dat_editing import legacy_ed
from features.dat_editing import legacy_ed_writer
from features.dat_editing import mesh_topology


Mat4 = Tuple[Tuple[float, float, float, float], ...]
Vec3 = Tuple[float, float, float]

EDITOR_DISPLAY = "editor_display"
RAW_DEDIT = "raw_dedit"
CUSTOM_MATRIX = "custom_matrix"

COORDINATE_PRESETS: Dict[str, Mat4] = {
    EDITOR_DISPLAY: (
        (-1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    ),
    RAW_DEDIT: (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    ),
}


@dataclass(frozen=True)
class GltfToEdConversionOptions:
    output_mode: str = gltf_ed_assembly.PREFAB
    geometry_policy: str = gltf_brushes.STRICT_CONVEX
    coordinate_preset: str = EDITOR_DISPLAY
    coordinate_matrix: Optional[Sequence[Sequence[float]]] = None
    unit_scale: float = 1.0
    weld_tolerance: float = mesh_topology.DEFAULT_WELD_TOLERANCE
    material_map: Optional[Mapping[str, str]] = None
    material_map_path: str = ""
    fallback_texture: Optional[str] = None
    texture_dimensions: Optional[Mapping[str, Sequence[float]]] = None
    texture_dimensions_path: str = ""
    texture_size_lookup: Optional[gltf_materials.TextureSizeLookup] = None
    texture_bytes_lookup: Optional[gltf_materials.TextureBytesLookup] = None
    fallback_texture_size: Optional[Sequence[float]] = None
    default_uv_projection: Optional[str] = None
    slab_thickness: Optional[float] = None
    slab_back_texture: Optional[str] = None
    slab_side_texture: Optional[str] = None
    max_brushes: int = gltf_brushes.DEFAULT_MAX_BRUSHES
    max_surfaces: int = gltf_brushes.DEFAULT_MAX_SURFACES
    overwrite: bool = False
    group_name: str = "ImportedGLTF"
    infostring: Optional[str] = None
    block_size: int = legacy_ed_writer.DEFAULT_FULL_LEVEL_ZLIB_BLOCK_SIZE
    world_properties_position: Optional[Sequence[float]] = None
    start_point_position: Optional[Sequence[float]] = None
    light_position: Optional[Sequence[float]] = None


@dataclass(frozen=True)
class ConversionDiagnostic:
    severity: str
    code: str
    message: str
    stage: str
    location: str = ""
    component_id: str = ""
    source_face_index: Optional[int] = None
    material_name: str = ""

    def to_dict(self) -> Dict[str, object]:
        result: Dict[str, object] = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "stage": self.stage,
        }
        if self.location:
            result["location"] = self.location
        if self.component_id:
            result["component_id"] = self.component_id
        if self.source_face_index is not None:
            result["source_face_index"] = self.source_face_index
        if self.material_name:
            result["material_name"] = self.material_name
        return result


@dataclass(frozen=True)
class GltfToEdConversionReport:
    status: str
    source: Dict[str, object]
    options: Dict[str, object]
    inventory: Dict[str, object]
    materials: Tuple[Dict[str, object], ...]
    components: Tuple[Dict[str, object], ...]
    budgets: Dict[str, object]
    output: Dict[str, object]
    validation: Dict[str, object]
    diagnostics: Tuple[ConversionDiagnostic, ...]

    @property
    def blockers(self) -> Tuple[ConversionDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "blocker")

    @property
    def cautions(self) -> Tuple[ConversionDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "caution")

    @property
    def notes(self) -> Tuple[ConversionDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "note")

    @property
    def output_path(self) -> str:
        return str(self.output.get("final_path") or "")

    @property
    def json_report_path(self) -> str:
        return str(self.output.get("json_report_path") or "")

    @property
    def text_report_path(self) -> str:
        return str(self.output.get("text_report_path") or "")

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": 1,
            "kind": "mm9_gltf_to_ed_conversion",
            "status": self.status,
            "source": copy.deepcopy(self.source),
            "options": copy.deepcopy(self.options),
            "inventory": copy.deepcopy(self.inventory),
            "materials": copy.deepcopy(list(self.materials)),
            "components": copy.deepcopy(list(self.components)),
            "budgets": copy.deepcopy(self.budgets),
            "output": copy.deepcopy(self.output),
            "validation": copy.deepcopy(self.validation),
            "blockers": [item.to_dict() for item in self.blockers],
            "cautions": [item.to_dict() for item in self.cautions],
            "notes": [item.to_dict() for item in self.notes],
        }


@dataclass(frozen=True)
class _ResolvedOptions:
    matrix: Mat4
    coordinate_label: str
    material_map: Dict[str, str]
    texture_dimensions: Dict[str, Tuple[float, float]]
    report: Dict[str, object]
    protected_paths: Tuple[str, ...]


class _ConfigurationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class _ArtifactWriteError(OSError):
    pass


def report_paths_for_output(output_path: str) -> Tuple[str, str]:
    absolute = os.path.abspath(os.fspath(output_path))
    stem, _extension = os.path.splitext(absolute)
    return (
        stem + ".gltf_to_ed_report.json",
        stem + ".gltf_to_ed_report.txt",
    )


def convert_gltf_to_ed(
    source_path: str,
    output_path: str,
    *,
    options: Optional[GltfToEdConversionOptions] = None,
) -> GltfToEdConversionReport:
    """Convert one local glTF/GLB into ED and its authoritative reports."""
    selected = options or GltfToEdConversionOptions()
    source_absolute = os.path.abspath(os.fspath(source_path))
    output_absolute = os.path.abspath(os.fspath(output_path))
    json_report_path, text_report_path = report_paths_for_output(output_absolute)
    diagnostics: List[ConversionDiagnostic] = []
    source_report = _source_file_report(source_absolute)
    output_report = _empty_output_report(
        output_absolute,
        json_report_path,
        text_report_path,
    )
    validation = _empty_validation()
    initial_options = _unresolved_options_report(selected)
    report = GltfToEdConversionReport(
        status="blocked",
        source=source_report,
        options=initial_options,
        inventory=_empty_inventory(),
        materials=(),
        components=(),
        budgets=_empty_budgets(selected),
        output=output_report,
        validation=validation,
        diagnostics=(),
    )

    try:
        resolved = _resolve_options(selected)
        report = replace(report, options=resolved.report)
    except _ConfigurationError as exc:
        diagnostics.append(_diagnostic("blocker", exc.code, str(exc), "options"))
        report = replace(
            report,
            diagnostics=_unique_diagnostics(diagnostics),
            validation={**validation, "preflight": "failed"},
        )
        return _write_report_only(
            report,
            overwrite=bool(selected.overwrite),
            protected_paths=(source_absolute,) + _option_protected_paths(selected),
        )

    if os.path.splitext(output_absolute)[1].lower() != ".ed":
        diagnostics.append(_diagnostic(
            "blocker",
            "invalid_output_extension",
            "output path must use the .ed extension",
            "output",
        ))
        report = replace(
            report,
            diagnostics=_unique_diagnostics(diagnostics),
            validation={**validation, "preflight": "failed"},
        )
        return _write_report_only(
            report,
            overwrite=bool(selected.overwrite),
            protected_paths=(source_absolute,) + resolved.protected_paths,
        )

    unsafe = _artifact_path_collision(
        (output_absolute, json_report_path, text_report_path),
        (source_absolute,) + resolved.protected_paths,
    )
    if unsafe:
        diagnostics.append(_diagnostic(
            "blocker",
            "artifact_path_collides_with_input",
            f"refusing to replace input/configuration path: {unsafe}",
            "output",
        ))
        report = replace(
            report,
            diagnostics=_unique_diagnostics(diagnostics),
            validation={**validation, "preflight": "failed", "report_write": "failed"},
        )
        return report

    try:
        imported_scene = gltf_import.load_gltf_geometry_scene(source_absolute)
    except gltf_import.GltfImportError as exc:
        diagnostics.append(ConversionDiagnostic(
            severity="blocker",
            code=exc.code,
            message=exc.detail,
            stage="gltf_import",
            location=exc.location,
        ))
        report = replace(
            report,
            diagnostics=_unique_diagnostics(diagnostics),
            validation={**validation, "preflight": "failed"},
        )
        return _write_report_only(report, overwrite=bool(selected.overwrite))

    source_report = _source_report_from_scene(imported_scene)
    source_metadata = imported_scene.metadata
    external_paths = tuple(
        str(item.get("path") or "")
        for item in source_report.get("external_buffers", ())
        if isinstance(item, dict) and item.get("path")
    )
    unsafe = _artifact_path_collision(
        (output_absolute, json_report_path, text_report_path),
        external_paths,
    )
    if unsafe:
        diagnostics.append(_diagnostic(
            "blocker",
            "artifact_path_collides_with_external_buffer",
            f"refusing to replace glTF external buffer: {unsafe}",
            "output",
        ))
        report = replace(
            report,
            source=source_report,
            inventory=_inventory_from_scene(imported_scene),
            diagnostics=_unique_diagnostics(diagnostics),
            validation={**validation, "preflight": "failed"},
        )
        return _write_report_only(
            report,
            overwrite=bool(selected.overwrite),
            protected_paths=external_paths,
        )
    for warning in source_metadata.get("warnings", ()) or ():
        diagnostics.append(_diagnostic(
            "caution", "gltf_import_warning", str(warning), "gltf_import"
        ))
    for feature in source_metadata.get("ignored_features", ()) or ():
        diagnostics.append(_diagnostic(
            "caution",
            "ignored_gltf_feature",
            f"glTF feature was ignored: {feature}",
            "gltf_import",
        ))

    converted_scene = _convert_scene_coordinates(
        imported_scene,
        resolved.matrix,
        unit_scale=float(selected.unit_scale),
        coordinate_label=resolved.coordinate_label,
    )
    diagnostics.append(_diagnostic(
        "note",
        "coordinate_conversion_applied",
        f"applied {resolved.coordinate_label} coordinates with unit scale {float(selected.unit_scale):g}",
        "coordinate_conversion",
    ))

    try:
        topology = mesh_topology.analyze_geometry_scene(
            converted_scene,
            weld_tolerance=float(selected.weld_tolerance),
        )
        plan = gltf_brushes.build_gltf_brush_plan(
            converted_scene,
            topology,
            geometry_policy=str(selected.geometry_policy),
            material_map=resolved.material_map,
            fallback_texture=selected.fallback_texture,
            texture_dimensions=resolved.texture_dimensions,
            texture_size_lookup=selected.texture_size_lookup,
            texture_bytes_lookup=selected.texture_bytes_lookup,
            fallback_texture_size=selected.fallback_texture_size,
            default_uv_projection=selected.default_uv_projection,
            slab_thickness=selected.slab_thickness,
            slab_back_texture=selected.slab_back_texture,
            slab_side_texture=selected.slab_side_texture,
            max_brushes=int(selected.max_brushes),
            max_surfaces=int(selected.max_surfaces),
        )
    except (TypeError, ValueError) as exc:
        diagnostics.append(_diagnostic(
            "blocker", "preflight_configuration_failed", str(exc), "preflight"
        ))
        report = replace(
            report,
            source=source_report,
            inventory=_inventory_from_scene(imported_scene),
            diagnostics=_unique_diagnostics(diagnostics),
            validation={**validation, "preflight": "failed"},
        )
        return _write_report_only(report, overwrite=bool(selected.overwrite))

    diagnostics.extend(_topology_diagnostics(topology))
    diagnostics.extend(_brush_plan_diagnostics(plan))
    assembly = gltf_ed_assembly.assemble_gltf_ed(
        plan,
        output_mode=str(selected.output_mode),
        group_name=str(selected.group_name),
        infostring=selected.infostring,
        block_size=int(selected.block_size),
        world_properties_position=selected.world_properties_position,
        start_point_position=selected.start_point_position,
        light_position=selected.light_position,
    )
    diagnostics.extend(
        _diagnostic(item.severity, item.code, item.message, "ed_assembly")
        for item in assembly.diagnostics
    )

    inventory = _inventory_from_scene(imported_scene)
    inventory.update({
        "generated_component_count": len(plan.components),
        "generated_brush_count": len(plan.planned_brushes),
        "generated_surface_count": sum(
            len(item.brush.surfaces) for item in plan.planned_brushes
        ),
        "generated_point_count": sum(
            len(item.brush.points) for item in plan.planned_brushes
        ),
    })
    materials = _material_reports(plan)
    components = _component_reports(topology, plan)
    budgets = _budget_report(plan)
    output_report = _output_report_from_assembly(
        assembly,
        requested_path=output_absolute,
        json_report_path=json_report_path,
        text_report_path=text_report_path,
    )
    validation = {
        **validation,
        "preflight": "pass" if plan.status == "ready" else "failed",
        "ed_writer": assembly.validation.writer,
        "ed_reader_roundtrip": assembly.validation.reader_roundtrip,
        "ed_roundtrip_summary": assembly.validation.to_dict(),
    }
    report = GltfToEdConversionReport(
        status=assembly.status,
        source=source_report,
        options=resolved.report,
        inventory=inventory,
        materials=materials,
        components=components,
        budgets=budgets,
        output=output_report,
        validation=validation,
        diagnostics=_unique_diagnostics(diagnostics),
    )

    if not assembly.status.startswith("ready_"):
        return _write_report_only(
            replace(report, output=_unwritten_output(report.output)),
            overwrite=bool(selected.overwrite),
        )

    ready_output = {
        **report.output,
        "final_path": output_absolute,
        "reports_written": True,
    }
    ready_validation = {
        **report.validation,
        "artifact_write": "pass",
        "report_write": "pass",
    }
    ready_report = replace(report, output=ready_output, validation=ready_validation)
    payloads = (
        (output_absolute, assembly.ed_bytes),
        (json_report_path, _json_report_bytes(ready_report)),
        (text_report_path, _text_report_bytes(ready_report)),
    )
    try:
        _commit_payloads(payloads, overwrite=bool(selected.overwrite))
        return ready_report
    except _ArtifactWriteError as exc:
        failure = _unwritten_output(report.output)
        failure_validation = {
            **report.validation,
            "artifact_write": "failed",
        }
        failure_diagnostics = list(report.diagnostics)
        failure_diagnostics.append(_diagnostic(
            "blocker", "artifact_write_failed", str(exc), "artifact_write"
        ))
        failure_report = replace(
            report,
            status="write_failed",
            output=failure,
            validation=failure_validation,
            diagnostics=_unique_diagnostics(failure_diagnostics),
        )
        return _write_report_only(failure_report, overwrite=bool(selected.overwrite))


def format_gltf_to_ed_conversion_report(report: GltfToEdConversionReport) -> str:
    lines = [
        "glTF/GLB to DEDit ED conversion",
        f"status: {report.status}",
        f"source: {report.source.get('path') or '<unavailable>'}",
        f"requested ED: {report.output.get('requested_path') or '<none>'}",
        (
            "options: "
            f"mode={report.options.get('output_mode')}, "
            f"geometry={report.options.get('geometry_policy')}, "
            f"coordinates={report.options.get('coordinate_preset')}, "
            f"unit_scale={report.options.get('unit_scale')}"
        ),
        (
            "source inventory: "
            f"scenes={report.inventory.get('scene_count', 0)}, "
            f"mesh_instances={report.inventory.get('mesh_instance_count', 0)}, "
            f"triangles={report.inventory.get('triangle_count', 0)}, "
            f"materials={report.inventory.get('material_count', 0)}"
        ),
        (
            "generated: "
            f"components={report.inventory.get('generated_component_count', 0)}, "
            f"Brushes={report.inventory.get('generated_brush_count', 0)}, "
            f"surfaces={report.inventory.get('generated_surface_count', 0)}, "
            f"points={report.inventory.get('generated_point_count', 0)}"
        ),
        (
            "ED output: "
            f"path={report.output.get('final_path') or '<not written>'}, "
            f"bytes={report.output.get('byte_size', 0)}, "
            f"wrapper={report.output.get('wrapper_kind') or '<none>'}"
        ),
        "validation:",
    ]
    for name in (
        "preflight",
        "ed_writer",
        "ed_reader_roundtrip",
        "artifact_write",
        "report_write",
        "dedit",
        "processor",
        "compiled_dat",
        "in_game",
    ):
        lines.append(f"- {name}: {report.validation.get(name, 'not_run')}")
    for severity, values in (
        ("blocker", report.blockers),
        ("caution", report.cautions),
        ("note", report.notes),
    ):
        if not values:
            continue
        lines.append(f"{severity}s:")
        for item in values:
            lines.append(f"- [{item.stage}/{item.code}] {item.message}")
    return "\n".join(lines) + "\n"


def load_gltf_to_ed_conversion_report(path: str) -> GltfToEdConversionReport:
    """Load and validate one schema-v1 Phase 7 conversion report."""
    absolute = os.path.abspath(os.fspath(path))
    with open(absolute, "r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("conversion report root must be a JSON object")
    if value.get("schema_version") != 1:
        raise ValueError("unsupported conversion report schema_version")
    if value.get("kind") != "mm9_gltf_to_ed_conversion":
        raise ValueError("JSON file is not an MM9 glTF-to-ED conversion report")

    diagnostics: List[ConversionDiagnostic] = []
    for severity, key in (
        ("blocker", "blockers"),
        ("caution", "cautions"),
        ("note", "notes"),
    ):
        entries = value.get(key, [])
        if not isinstance(entries, list):
            raise ValueError(f"conversion report {key} must be an array")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"conversion report {key} entries must be objects")
            diagnostics.append(ConversionDiagnostic(
                severity=severity,
                code=str(entry.get("code") or "diagnostic"),
                message=str(entry.get("message") or ""),
                stage=str(entry.get("stage") or "conversion"),
                location=str(entry.get("location") or ""),
                component_id=str(entry.get("component_id") or ""),
                source_face_index=(
                    int(entry["source_face_index"])
                    if entry.get("source_face_index") is not None
                    else None
                ),
                material_name=str(entry.get("material_name") or ""),
            ))

    def mapping(name: str) -> Dict[str, object]:
        item = value.get(name)
        if not isinstance(item, dict):
            raise ValueError(f"conversion report {name} must be an object")
        return copy.deepcopy(item)

    def mapping_array(name: str) -> Tuple[Dict[str, object], ...]:
        items = value.get(name)
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError(f"conversion report {name} must be an array of objects")
        return tuple(copy.deepcopy(item) for item in items)

    return GltfToEdConversionReport(
        status=str(value.get("status") or "blocked"),
        source=mapping("source"),
        options=mapping("options"),
        inventory=mapping("inventory"),
        materials=mapping_array("materials"),
        components=mapping_array("components"),
        budgets=mapping("budgets"),
        output=mapping("output"),
        validation=mapping("validation"),
        diagnostics=_unique_diagnostics(diagnostics),
    )


def conversion_report_json_bytes(report: GltfToEdConversionReport) -> bytes:
    """Serialize a Phase 7 report with its stable schema-v1 JSON layout."""
    return _json_report_bytes(report)


def conversion_report_text_bytes(report: GltfToEdConversionReport) -> bytes:
    """Serialize the human-readable companion to a Phase 7 report."""
    return _text_report_bytes(report)


def commit_artifacts(
    payloads: Sequence[Tuple[str, bytes]],
    *,
    overwrite: bool,
) -> None:
    """Commit a related artifact set with the Phase 7 rollback behavior."""
    _commit_payloads(payloads, overwrite=overwrite)


def _resolve_options(options: GltfToEdConversionOptions) -> _ResolvedOptions:
    output_mode = str(options.output_mode)
    if output_mode not in gltf_ed_assembly.OUTPUT_MODES:
        raise _ConfigurationError("invalid_output_mode", "output_mode must be prefab or full_world")
    geometry_policy = str(options.geometry_policy)
    if geometry_policy not in {gltf_brushes.STRICT_CONVEX, gltf_brushes.TRIANGLE_SLAB}:
        raise _ConfigurationError(
            "invalid_geometry_policy",
            "geometry_policy must be strict_convex or triangle_slab",
        )
    unit_scale = _positive_finite("unit_scale", options.unit_scale)
    weld_tolerance = _nonnegative_finite("weld_tolerance", options.weld_tolerance)
    if type(options.max_brushes) is not int or not 1 <= options.max_brushes <= gltf_brushes.DEFAULT_MAX_BRUSHES:
        raise _ConfigurationError(
            "invalid_brush_budget",
            f"max_brushes must be an integer in 1..{gltf_brushes.DEFAULT_MAX_BRUSHES}",
        )
    if type(options.max_surfaces) is not int or not 1 <= options.max_surfaces <= gltf_brushes.DEFAULT_MAX_SURFACES:
        raise _ConfigurationError(
            "invalid_surface_budget",
            f"max_surfaces must be an integer in 1..{gltf_brushes.DEFAULT_MAX_SURFACES}",
        )
    if type(options.block_size) is not int or options.block_size <= 0:
        raise _ConfigurationError("invalid_block_size", "block_size must be a positive integer")
    if options.texture_size_lookup is not None and not callable(options.texture_size_lookup):
        raise _ConfigurationError(
            "invalid_texture_size_lookup", "texture_size_lookup must be callable"
        )
    if options.texture_bytes_lookup is not None and not callable(options.texture_bytes_lookup):
        raise _ConfigurationError(
            "invalid_texture_bytes_lookup", "texture_bytes_lookup must be callable"
        )
    if options.default_uv_projection not in {None, gltf_materials.WORLD_ALIGNED_PROJECTION}:
        raise _ConfigurationError(
            "invalid_default_uv_projection",
            "default_uv_projection must be omitted or world_aligned",
        )
    fallback_texture = (
        None
        if options.fallback_texture is None
        else _validated_texture_path(options.fallback_texture, "fallback_texture")
    )
    fallback_texture_size = (
        None
        if options.fallback_texture_size is None
        else _dimension_mapping_value(options.fallback_texture_size, "fallback_texture_size")
    )
    slab_thickness = options.slab_thickness
    if slab_thickness is not None:
        slab_thickness = _positive_finite("slab_thickness", slab_thickness)
    slab_back_texture = options.slab_back_texture
    slab_side_texture = options.slab_side_texture
    if geometry_policy == gltf_brushes.TRIANGLE_SLAB:
        if slab_thickness is None:
            raise _ConfigurationError(
                "missing_slab_thickness", "triangle_slab requires slab_thickness"
            )
        if slab_thickness <= weld_tolerance:
            raise _ConfigurationError(
                "slab_thickness_within_weld_tolerance",
                "triangle_slab thickness must exceed weld_tolerance",
            )
        slab_back_texture = _validated_texture_path(
            slab_back_texture, "slab_back_texture"
        )
        slab_side_texture = _validated_texture_path(
            slab_side_texture, "slab_side_texture"
        )
    elif slab_back_texture is not None or slab_side_texture is not None:
        if slab_back_texture is not None:
            slab_back_texture = _validated_texture_path(
                slab_back_texture, "slab_back_texture"
            )
        if slab_side_texture is not None:
            slab_side_texture = _validated_texture_path(
                slab_side_texture, "slab_side_texture"
            )
    encoded_info = (
        None
        if options.infostring is None
        else str(options.infostring).encode("latin1", errors="replace")
    )
    if encoded_info is not None and len(encoded_info) > 4096:
        raise _ConfigurationError(
            "invalid_infostring", "full-world infostring must be at most 4096 Latin-1 bytes"
        )
    world_properties_position = _optional_vec3_report(options.world_properties_position)
    start_point_position = _optional_vec3_report(options.start_point_position)
    light_position = _optional_vec3_report(options.light_position)

    if options.coordinate_matrix is None:
        coordinate_label = str(options.coordinate_preset)
        if coordinate_label not in COORDINATE_PRESETS:
            raise _ConfigurationError(
                "invalid_coordinate_preset",
                f"coordinate_preset must be {EDITOR_DISPLAY} or {RAW_DEDIT}",
            )
        matrix = COORDINATE_PRESETS[coordinate_label]
    else:
        coordinate_label = CUSTOM_MATRIX
        matrix = _coordinate_matrix(options.coordinate_matrix)
    determinant = _linear_determinant(matrix)
    if abs(determinant) <= 1.0e-12:
        raise _ConfigurationError("singular_coordinate_matrix", "coordinate matrix must be invertible")

    material_map: Dict[str, str] = {}
    protected_paths: List[str] = []
    material_map_info = _empty_config_file_report()
    if options.material_map_path:
        loaded, material_map_info = _load_json_mapping_file(
            options.material_map_path,
            kind="material map",
            value_parser=_string_mapping_value,
        )
        material_map.update(loaded)
        protected_paths.append(str(material_map_info["path"]))
    if options.material_map is not None:
        if not isinstance(options.material_map, Mapping):
            raise _ConfigurationError("invalid_material_map", "material_map must be a mapping")
        for key, value in options.material_map.items():
            material_map[str(key)] = _string_mapping_value(value, str(key))

    texture_dimensions: Dict[str, Tuple[float, float]] = {}
    dimension_map_info = _empty_config_file_report()
    if options.texture_dimensions_path:
        loaded_dimensions, dimension_map_info = _load_json_mapping_file(
            options.texture_dimensions_path,
            kind="texture dimensions",
            value_parser=_dimension_mapping_value,
        )
        texture_dimensions.update(loaded_dimensions)
        protected_paths.append(str(dimension_map_info["path"]))
    if options.texture_dimensions is not None:
        if not isinstance(options.texture_dimensions, Mapping):
            raise _ConfigurationError(
                "invalid_texture_dimensions", "texture_dimensions must be a mapping"
            )
        for key, value in options.texture_dimensions.items():
            texture_dimensions[str(key)] = _dimension_mapping_value(value, str(key))

    report = {
        "output_mode": output_mode,
        "geometry_policy": geometry_policy,
        "coordinate_preset": coordinate_label,
        "coordinate_matrix": [list(row) for row in matrix],
        "coordinate_determinant": determinant,
        "unit_scale": unit_scale,
        "weld_tolerance": weld_tolerance,
        "material_map": material_map_info,
        "material_map_entry_count": len(material_map),
        "fallback_texture": fallback_texture,
        "texture_dimensions": dimension_map_info,
        "texture_dimension_entry_count": len(texture_dimensions),
        "fallback_texture_size": (
            list(fallback_texture_size)
            if fallback_texture_size is not None
            else None
        ),
        "default_uv_projection": options.default_uv_projection,
        "slab": {
            "thickness": slab_thickness,
            "back_texture": slab_back_texture,
            "side_texture": slab_side_texture,
        },
        "budgets": {
            "max_brushes": options.max_brushes,
            "max_surfaces": options.max_surfaces,
        },
        "overwrite": bool(options.overwrite),
        "group_name": str(options.group_name),
        "full_world": {
            "infostring": options.infostring,
            "block_size": options.block_size,
            "world_properties_position": world_properties_position,
            "start_point_position": start_point_position,
            "light_position": light_position,
        },
    }
    return _ResolvedOptions(
        matrix=matrix,
        coordinate_label=coordinate_label,
        material_map=material_map,
        texture_dimensions=texture_dimensions,
        report=report,
        protected_paths=tuple(protected_paths),
    )


def _convert_scene_coordinates(
    scene: geometry_scene.GeometryScene,
    matrix: Mat4,
    *,
    unit_scale: float,
    coordinate_label: str,
) -> geometry_scene.GeometryScene:
    reverse_winding = _linear_determinant(matrix) < 0.0
    models: List[geometry_scene.GeometryModel] = []
    for model in scene.models:
        faces: List[geometry_scene.GeometryFace] = []
        for face in model.faces:
            indices = list(face.vertex_indices)
            uv_coords = list(face.uv_coords)
            if reverse_winding:
                indices.reverse()
                uv_coords.reverse()
            faces.append(geometry_scene.GeometryFace(
                vertex_indices=indices,
                material_name=face.material_name,
                uv_coords=uv_coords,
                extras=copy.deepcopy(face.extras),
            ))
        models.append(geometry_scene.GeometryModel(
            name=model.name,
            points=[_transform_point(matrix, point, unit_scale) for point in model.points],
            faces=faces,
            extras=copy.deepcopy(model.extras),
        ))
    metadata = copy.deepcopy(scene.metadata)
    metadata["coordinate_system"] = {
        "space": "dedit",
        "node_transforms_baked": True,
        "dedit_coordinate_conversion_applied": True,
        "coordinate_preset": coordinate_label,
        "coordinate_matrix": [list(row) for row in matrix],
        "unit_scale": unit_scale,
        "winding_reversed": reverse_winding,
    }
    return geometry_scene.GeometryScene(
        source_path=scene.source_path,
        models=models,
        materials=[geometry_scene.GeometryMaterial(
            name=item.name,
            texture_name=item.texture_name,
            extras=copy.deepcopy(item.extras),
        ) for item in scene.materials],
        metadata=metadata,
    )


def _component_reports(
    topology: mesh_topology.MeshTopologyReport,
    plan: gltf_brushes.GltfBrushPlan,
) -> Tuple[Dict[str, object], ...]:
    plans = {item.component_id: item for item in plan.components}
    result: List[Dict[str, object]] = []
    for component in topology.components:
        item = component.to_dict()
        conversion = plans.get(component.component_id)
        conversion_report = conversion.to_dict() if conversion is not None else None
        item["conversion"] = conversion_report
        diagnostic_values = list(item.get("diagnostics", ()) or ())
        if isinstance(conversion_report, dict):
            diagnostic_values.extend(conversion_report.get("diagnostics", ()) or ())
        item["blockers"] = [
            value for value in diagnostic_values
            if isinstance(value, dict) and value.get("severity") == "blocker"
        ]
        item["cautions"] = [
            value for value in diagnostic_values
            if isinstance(value, dict) and value.get("severity") == "caution"
        ]
        item["notes"] = [
            value for value in diagnostic_values
            if isinstance(value, dict) and value.get("severity") == "note"
        ]
        result.append(item)
    return tuple(result)


def _material_reports(
    plan: gltf_brushes.GltfBrushPlan,
) -> Tuple[Dict[str, object], ...]:
    result: List[Dict[str, object]] = []
    for material in plan.material_uv_report.materials:
        item = material.to_dict()
        item["blockers"] = [value.to_dict() for value in material.blockers]
        item["cautions"] = [value.to_dict() for value in material.cautions]
        item["notes"] = [value.to_dict() for value in material.notes]
        result.append(item)
    return tuple(result)


def _topology_diagnostics(
    report: mesh_topology.MeshTopologyReport,
) -> Tuple[ConversionDiagnostic, ...]:
    result = [_from_diagnostic(item.to_dict(), "topology") for item in report.diagnostics]
    for component in report.components:
        result.extend(
            _from_diagnostic(item.to_dict(), "topology", component_id=component.component_id)
            for item in component.diagnostics
        )
    return tuple(result)


def _brush_plan_diagnostics(
    plan: gltf_brushes.GltfBrushPlan,
) -> Tuple[ConversionDiagnostic, ...]:
    result = [_from_diagnostic(item.to_dict(), "brush_planning") for item in plan.diagnostics]
    for component in plan.components:
        result.extend(
            _from_diagnostic(item.to_dict(), "brush_planning", component_id=component.component_id)
            for item in component.diagnostics
        )
    result.extend(
        _from_diagnostic(item.to_dict(), "material_uv")
        for item in plan.material_uv_report.diagnostics
    )
    for material in plan.material_uv_report.materials:
        result.extend(
            _from_diagnostic(item.to_dict(), "material_uv", material_name=material.material_name)
            for item in material.diagnostics
        )
    return tuple(result)


def _from_diagnostic(
    value: Mapping[str, object],
    stage: str,
    *,
    component_id: str = "",
    material_name: str = "",
) -> ConversionDiagnostic:
    return ConversionDiagnostic(
        severity=str(value.get("severity") or "note"),
        code=str(value.get("code") or "diagnostic"),
        message=str(value.get("message") or ""),
        stage=stage,
        component_id=str(value.get("component_id") or component_id),
        source_face_index=(
            int(value["source_face_index"])
            if value.get("source_face_index") is not None
            else None
        ),
        material_name=str(value.get("material_name") or material_name),
    )


def _source_file_report(path: str) -> Dict[str, object]:
    result: Dict[str, object] = {
        "path": path,
        "sha256": None,
        "byte_size": 0,
        "format": os.path.splitext(path)[1].lower().lstrip(".") or None,
        "asset": {"version": None, "min_version": None, "generator": None},
        "external_buffers": [],
    }
    try:
        with open(path, "rb") as stream:
            data = stream.read()
    except OSError:
        return result
    result["sha256"] = hashlib.sha256(data).hexdigest()
    result["byte_size"] = len(data)
    return result


def _source_report_from_scene(scene: geometry_scene.GeometryScene) -> Dict[str, object]:
    metadata = scene.metadata
    source = metadata.get("source") if isinstance(metadata.get("source"), dict) else {}
    asset = metadata.get("asset") if isinstance(metadata.get("asset"), dict) else {}
    buffers = source.get("buffers") if isinstance(source, dict) else []
    external_buffers = [
        {
            "index": item.get("index"),
            "path": item.get("resolved_path"),
            "sha256": item.get("sha256"),
            "byte_size": item.get("byte_length", 0),
        }
        for item in (buffers or [])
        if isinstance(item, dict) and item.get("resolved_path")
    ]
    return {
        "path": str(source.get("path") or scene.source_path),
        "sha256": source.get("sha256"),
        "byte_size": int(source.get("byte_length", 0) or 0),
        "format": metadata.get("format"),
        "asset": {
            "version": asset.get("version"),
            "min_version": asset.get("min_version"),
            "generator": asset.get("generator"),
        },
        "external_buffers": external_buffers,
    }


def _inventory_from_scene(scene: geometry_scene.GeometryScene) -> Dict[str, object]:
    metadata = scene.metadata
    source = metadata.get("inventory") if isinstance(metadata.get("inventory"), dict) else {}
    return {
        "scene_count": int(source.get("scene_count", 0) or 0),
        "selected_node_count": int(source.get("selected_node_count", 0) or 0),
        "ignored_non_mesh_node_count": int(source.get("ignored_non_mesh_node_count", 0) or 0),
        "mesh_instance_count": int(source.get("mesh_instance_count", 0) or 0),
        "mesh_count": int(source.get("mesh_count", 0) or 0),
        "primitive_count": int(source.get("primitive_count", 0) or 0),
        "triangle_count": int(source.get("triangle_count", 0) or 0),
        "material_count": int(source.get("material_count", 0) or 0),
        "ignored_features": list(metadata.get("ignored_features", ()) or ()),
        "unsupported_features": [],
        "generated_component_count": 0,
        "generated_brush_count": 0,
        "generated_surface_count": 0,
        "generated_point_count": 0,
    }


def _budget_report(plan: gltf_brushes.GltfBrushPlan) -> Dict[str, object]:
    generated_brushes = len(plan.planned_brushes)
    generated_surfaces = sum(len(item.brush.surfaces) for item in plan.planned_brushes)
    return {
        "brushes": {
            "limit": plan.max_brushes,
            "estimated": plan.estimated_brush_count,
            "generated": generated_brushes,
            "remaining": max(0, plan.max_brushes - generated_brushes),
            "pass": plan.estimated_brush_count <= plan.max_brushes,
        },
        "surfaces": {
            "limit": plan.max_surfaces,
            "estimated": plan.estimated_surface_count,
            "generated": generated_surfaces,
            "remaining": max(0, plan.max_surfaces - generated_surfaces),
            "pass": plan.estimated_surface_count <= plan.max_surfaces,
        },
    }


def _output_report_from_assembly(
    assembly: gltf_ed_assembly.GltfEdAssembly,
    *,
    requested_path: str,
    json_report_path: str,
    text_report_path: str,
) -> Dict[str, object]:
    output = assembly.to_dict()["output"]
    return {
        "requested_path": requested_path,
        "final_path": None,
        "json_report_path": json_report_path,
        "text_report_path": text_report_path,
        "reports_written": False,
        **output,
    }


def _empty_output_report(
    requested_path: str,
    json_report_path: str,
    text_report_path: str,
) -> Dict[str, object]:
    return {
        "requested_path": requested_path,
        "final_path": None,
        "json_report_path": json_report_path,
        "text_report_path": text_report_path,
        "reports_written": False,
        "ed_version": legacy_ed.LEGACY_ED_VERSION,
        "wrapper_kind": None,
        "byte_size": 0,
        "sha256": None,
        "brush_count": 0,
        "surface_count": 0,
        "point_count": 0,
        "node_count": 0,
        "object_count": 0,
        "group_name": None,
        "brush_names": [],
        "name_map": [],
        "node_assignments": [],
        "wrapper_block_count": 0,
        "wrapper_block_size": 0,
        "full_world_scaffold": None,
    }


def _unwritten_output(value: Mapping[str, object]) -> Dict[str, object]:
    result = dict(value)
    result.update({
        "final_path": None,
        "reports_written": False,
        "wrapper_kind": None,
        "byte_size": 0,
        "sha256": None,
        "brush_count": 0,
        "surface_count": 0,
        "point_count": 0,
        "node_count": 0,
        "object_count": 0,
        "group_name": None,
        "brush_names": [],
        "name_map": [],
        "node_assignments": [],
        "wrapper_block_count": 0,
        "wrapper_block_size": 0,
        "full_world_scaffold": None,
    })
    return result


def _write_report_only(
    report: GltfToEdConversionReport,
    *,
    overwrite: bool,
    protected_paths: Sequence[str] = (),
) -> GltfToEdConversionReport:
    json_path = report.json_report_path
    text_path = report.text_report_path
    unsafe = _artifact_path_collision((json_path, text_path), protected_paths)
    if unsafe:
        diagnostics = list(report.diagnostics)
        diagnostics.append(_diagnostic(
            "blocker",
            "report_path_collides_with_input",
            f"refusing to replace input/configuration path: {unsafe}",
            "report_write",
        ))
        return replace(
            report,
            validation={**report.validation, "report_write": "failed"},
            diagnostics=_unique_diagnostics(diagnostics),
        )
    prepared = replace(
        report,
        output={**report.output, "reports_written": True},
        validation={**report.validation, "report_write": "pass"},
    )
    try:
        _commit_payloads(
            (
                (json_path, _json_report_bytes(prepared)),
                (text_path, _text_report_bytes(prepared)),
            ),
            overwrite=overwrite,
        )
        return prepared
    except _ArtifactWriteError as exc:
        diagnostics = list(report.diagnostics)
        diagnostics.append(_diagnostic(
            "caution",
            "report_write_failed",
            str(exc),
            "report_write",
        ))
        return replace(
            report,
            output={**report.output, "reports_written": False},
            validation={**report.validation, "report_write": "failed"},
            diagnostics=_unique_diagnostics(diagnostics),
        )


def _commit_payloads(
    payloads: Sequence[Tuple[str, bytes]],
    *,
    overwrite: bool,
) -> None:
    targets = tuple(os.path.abspath(path) for path, _payload in payloads)
    if len(set(os.path.normcase(path) for path in targets)) != len(targets):
        raise _ArtifactWriteError("artifact targets must be distinct")
    staged: Dict[str, str] = {}
    backups: Dict[str, str] = {}
    committed_new: List[str] = []
    try:
        for (raw_target, payload), target in zip(payloads, targets):
            del raw_target
            directory = os.path.dirname(target) or os.curdir
            os.makedirs(directory, exist_ok=True)
            if not os.path.isdir(directory):
                raise OSError(f"artifact parent is not a directory: {directory}")
            fd, stage = tempfile.mkstemp(
                prefix=f".{os.path.basename(target)}.",
                suffix=".tmp",
                dir=directory,
            )
            staged[target] = stage
            with os.fdopen(fd, "wb") as stream:
                stream.write(bytes(payload))
                stream.flush()
                os.fsync(stream.fileno())

        if overwrite:
            for target in targets:
                if not os.path.lexists(target):
                    continue
                directory = os.path.dirname(target) or os.curdir
                fd, backup = tempfile.mkstemp(
                    prefix=f".{os.path.basename(target)}.",
                    suffix=".backup",
                    dir=directory,
                )
                os.close(fd)
                os.replace(target, backup)
                backups[target] = backup
            for target in targets:
                os.replace(staged[target], target)
                staged.pop(target, None)
                if target not in backups:
                    committed_new.append(target)
        else:
            existing = next((path for path in targets if os.path.lexists(path)), "")
            if existing:
                raise FileExistsError(f"artifact already exists (use overwrite to replace it): {existing}")
            for target in targets:
                os.link(staged[target], target)
                committed_new.append(target)
                os.unlink(staged[target])
                staged.pop(target, None)
    except OSError as exc:
        for target, backup in reversed(tuple(backups.items())):
            try:
                os.replace(backup, target)
            except OSError:
                pass
        for target in reversed(committed_new):
            _safe_unlink(target)
        raise _ArtifactWriteError(str(exc)) from exc
    finally:
        for stage in tuple(staged.values()):
            _safe_unlink(stage)
    for backup in tuple(backups.values()):
        _safe_unlink(backup)


def _json_report_bytes(report: GltfToEdConversionReport) -> bytes:
    return (
        json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _text_report_bytes(report: GltfToEdConversionReport) -> bytes:
    return format_gltf_to_ed_conversion_report(report).encode("utf-8")


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _artifact_path_collision(
    targets: Sequence[str],
    protected_paths: Sequence[str],
) -> str:
    protected = {
        os.path.normcase(os.path.realpath(os.path.abspath(path)))
        for path in protected_paths
        if path
    }
    for target in targets:
        if not target:
            continue
        resolved = os.path.normcase(os.path.realpath(os.path.abspath(target)))
        if resolved in protected:
            return os.path.abspath(target)
    return ""


def _load_json_mapping_file(
    path: str,
    *,
    kind: str,
    value_parser: Callable[[object, str], object],
) -> Tuple[Dict[str, object], Dict[str, object]]:
    absolute = os.path.abspath(os.fspath(path))
    try:
        with open(absolute, "rb") as stream:
            data = stream.read()
    except OSError as exc:
        raise _ConfigurationError(
            f"{kind.replace(' ', '_')}_read_failed",
            f"could not read {kind} {absolute}: {exc}",
        ) from exc
    try:
        document = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _ConfigurationError(
            f"invalid_{kind.replace(' ', '_')}",
            f"{kind} must be a UTF-8 JSON object: {exc}",
        ) from exc
    if not isinstance(document, dict):
        raise _ConfigurationError(
            f"invalid_{kind.replace(' ', '_')}",
            f"{kind} root must be a JSON object",
        )
    result: Dict[str, object] = {}
    for key, value in document.items():
        result[str(key)] = value_parser(value, str(key))
    return result, {
        "path": absolute,
        "sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
    }


def _string_mapping_value(value: object, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _ConfigurationError(
            "invalid_material_map",
            f"material map value for {key!r} must be a non-empty string",
        )
    return _validated_texture_path(value, f"material map value for {key!r}")


def _validated_texture_path(value: object, option_name: str) -> str:
    try:
        return gltf_materials.validate_dtx_texture_path(value)
    except ValueError as exc:
        raise _ConfigurationError(
            "invalid_texture_path", f"{option_name} {exc}"
        ) from exc


def _dimension_mapping_value(value: object, key: str) -> Tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _ConfigurationError(
            "invalid_texture_dimensions",
            f"texture dimensions for {key!r} must be [width, height]",
        )
    width = _positive_finite(f"texture width for {key!r}", value[0])
    height = _positive_finite(f"texture height for {key!r}", value[1])
    return width, height


def _coordinate_matrix(value: Sequence[Sequence[float]]) -> Mat4:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise _ConfigurationError("invalid_coordinate_matrix", "coordinate matrix must have four rows")
    rows: List[Tuple[float, float, float, float]] = []
    for row_index, row in enumerate(value):
        if not isinstance(row, (list, tuple)) or len(row) != 4:
            raise _ConfigurationError(
                "invalid_coordinate_matrix",
                f"coordinate matrix row {row_index} must contain four values",
            )
        numbers = tuple(_finite_number("coordinate matrix value", item) for item in row)
        rows.append((numbers[0], numbers[1], numbers[2], numbers[3]))
    if any(abs(rows[3][index] - expected) > 1.0e-12 for index, expected in enumerate((0.0, 0.0, 0.0, 1.0))):
        raise _ConfigurationError(
            "invalid_coordinate_matrix",
            "coordinate matrix must be affine with final row [0, 0, 0, 1]",
        )
    return tuple(rows)


def _transform_point(matrix: Mat4, point: Sequence[float], scale: float) -> Vec3:
    x, y, z = (float(item) for item in point)
    return (
        scale * (matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3]),
        scale * (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3]),
        scale * (matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3]),
    )


def _linear_determinant(matrix: Mat4) -> float:
    a, b, c = matrix[0][:3]
    d, e, f = matrix[1][:3]
    g, h, i = matrix[2][:3]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def _positive_finite(name: str, value: object) -> float:
    number = _finite_number(name, value)
    if number <= 0.0:
        raise _ConfigurationError(
            f"invalid_{name.replace(' ', '_')}", f"{name} must be greater than zero"
        )
    return number


def _nonnegative_finite(name: str, value: object) -> float:
    number = _finite_number(name, value)
    if number < 0.0:
        raise _ConfigurationError(
            f"invalid_{name.replace(' ', '_')}", f"{name} must be non-negative"
        )
    return number


def _finite_number(name: str, value: object) -> float:
    if isinstance(value, bool):
        raise _ConfigurationError(
            f"invalid_{name.replace(' ', '_')}", f"{name} must be finite"
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise _ConfigurationError(
            f"invalid_{name.replace(' ', '_')}", f"{name} must be finite"
        ) from exc
    if not math.isfinite(number):
        raise _ConfigurationError(
            f"invalid_{name.replace(' ', '_')}", f"{name} must be finite"
        )
    return number


def _optional_vec3_report(value: Optional[Sequence[float]]) -> Optional[List[float]]:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _ConfigurationError("invalid_full_world_position", "full-world positions require three values")
    return [_finite_number("full-world position", item) for item in value]


def _unresolved_options_report(options: GltfToEdConversionOptions) -> Dict[str, object]:
    return {
        "output_mode": str(options.output_mode),
        "geometry_policy": str(options.geometry_policy),
        "coordinate_preset": str(options.coordinate_preset),
        "coordinate_matrix": None,
        "unit_scale": options.unit_scale,
        "weld_tolerance": options.weld_tolerance,
        "material_map": _empty_config_file_report(options.material_map_path),
        "material_map_entry_count": (
            len(options.material_map) if isinstance(options.material_map, Mapping) else 0
        ),
        "fallback_texture": options.fallback_texture,
        "texture_dimensions": _empty_config_file_report(options.texture_dimensions_path),
        "texture_dimension_entry_count": (
            len(options.texture_dimensions)
            if isinstance(options.texture_dimensions, Mapping)
            else 0
        ),
        "fallback_texture_size": (
            list(options.fallback_texture_size)
            if isinstance(options.fallback_texture_size, (list, tuple))
            else None
        ),
        "default_uv_projection": options.default_uv_projection,
        "slab": {
            "thickness": options.slab_thickness,
            "back_texture": options.slab_back_texture,
            "side_texture": options.slab_side_texture,
        },
        "budgets": {
            "max_brushes": options.max_brushes,
            "max_surfaces": options.max_surfaces,
        },
        "overwrite": bool(options.overwrite),
        "group_name": str(options.group_name),
    }


def _empty_config_file_report(path: str = "") -> Dict[str, object]:
    return {
        "path": os.path.abspath(path) if path else None,
        "sha256": None,
        "byte_size": 0,
    }


def _option_protected_paths(
    options: GltfToEdConversionOptions,
) -> Tuple[str, ...]:
    return tuple(
        os.path.abspath(path)
        for path in (options.material_map_path, options.texture_dimensions_path)
        if path
    )


def _empty_inventory() -> Dict[str, object]:
    return {
        "scene_count": 0,
        "selected_node_count": 0,
        "ignored_non_mesh_node_count": 0,
        "mesh_instance_count": 0,
        "mesh_count": 0,
        "primitive_count": 0,
        "triangle_count": 0,
        "material_count": 0,
        "ignored_features": [],
        "unsupported_features": [],
        "generated_component_count": 0,
        "generated_brush_count": 0,
        "generated_surface_count": 0,
        "generated_point_count": 0,
    }


def _empty_budgets(options: GltfToEdConversionOptions) -> Dict[str, object]:
    return {
        "brushes": {
            "limit": options.max_brushes,
            "estimated": 0,
            "generated": 0,
            "remaining": options.max_brushes,
            "pass": False,
        },
        "surfaces": {
            "limit": options.max_surfaces,
            "estimated": 0,
            "generated": 0,
            "remaining": options.max_surfaces,
            "pass": False,
        },
    }


def _empty_validation() -> Dict[str, object]:
    return {
        "preflight": "not_run",
        "ed_writer": "not_run",
        "ed_reader_roundtrip": "not_run",
        "ed_roundtrip_summary": None,
        "artifact_write": "not_run",
        "report_write": "not_run",
        "dedit": "not_run",
        "processor": "not_run",
        "compiled_dat": "not_run",
        "in_game": "not_run",
    }


def _diagnostic(severity: str, code: str, message: str, stage: str) -> ConversionDiagnostic:
    return ConversionDiagnostic(severity, code, message, stage)


def _unique_diagnostics(
    values: Sequence[ConversionDiagnostic],
) -> Tuple[ConversionDiagnostic, ...]:
    result: List[ConversionDiagnostic] = []
    seen = set()
    for item in values:
        key = (
            item.severity,
            item.code,
            item.message,
            item.stage,
            item.location,
            item.component_id,
            item.source_face_index,
            item.material_name,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


__all__ = [
    "COORDINATE_PRESETS",
    "CUSTOM_MATRIX",
    "EDITOR_DISPLAY",
    "GltfToEdConversionOptions",
    "GltfToEdConversionReport",
    "RAW_DEDIT",
    "commit_artifacts",
    "conversion_report_json_bytes",
    "conversion_report_text_bytes",
    "convert_gltf_to_ed",
    "format_gltf_to_ed_conversion_report",
    "load_gltf_to_ed_conversion_report",
    "report_paths_for_output",
]
