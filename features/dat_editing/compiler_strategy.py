"""Stage 7 compiler-strategy diagnostics for DAT geometry editing.

The editor has several useful geometry readers and a tiny v66 world-model
compiler, but none of those are a complete MM9/Talon world packer.  This module
keeps that distinction explicit so a future external compiler candidate can be
evaluated against the same requirements every time.
"""

from __future__ import annotations

import os
import json
import math
import re
import shutil
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from features.dat_editing import terrain_reconstruction, terrain_semantics


REQUIRED_FULL_WORLD_SYSTEMS: Tuple[str, ...] = (
    "header_v66",
    "world_tree",
    "Terrain*",
    "PhysicsBSP",
    "VisBSP",
    "portals",
    "polygon_lightmaps",
    "light_grid",
    "object_data",
    "render_data",
)

DEFAULT_LITH21_PROCESSOR_OPTIONS: Tuple[str, ...] = (
    "-textureflags",
    "-bsp",
    "-light",
    "-optimize",
    "-fast",
    "-logfile",
    "-projectdir",
    "{project_dir}",
    "-splitweight",
    "0.690000",
)

DEFAULT_BLACK_BOX_ACCEPTED_REGENERATED_SYSTEMS: Tuple[str, ...] = (
    "terrain",
    "physics",
    "visibility",
    "render_data",
    "top_level_sections",
)


@dataclass(frozen=True)
class CompilerCandidate:
    candidate_id: str
    name: str
    source: str
    input_formats: Tuple[str, ...] = ()
    output_scope: str = "unknown"
    expected_dat_version: Optional[int] = None
    can_compile_full_world: bool = False
    rebuilt_systems: Tuple[str, ...] = ()
    evidence: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    status: str = "unknown"


@dataclass(frozen=True)
class CompilerStrategyReport:
    candidates: List[CompilerCandidate] = field(default_factory=list)
    compatible_candidate_ids: Tuple[str, ...] = ()
    required_systems: Tuple[str, ...] = REQUIRED_FULL_WORLD_SYSTEMS
    recommendation: str = "continue_internal_v66_rebuild_pipeline"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class LtWorldConverterEdWriterGapReport:
    ltworldconverter_root: str
    edunpacker_root: str
    status: str = "unknown"
    writer_version: Optional[int] = None
    reader_versions: Tuple[int, ...] = ()
    target_version: int = 1249
    required_changes: Tuple[str, ...] = ()
    reusable_components: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    evidence: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceWorldArtifact:
    path: str
    stem: str
    format: str
    size: int
    version: Optional[int] = None
    status: str = "unknown"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceWorldPair:
    stem: str
    dat: Optional[SourceWorldArtifact] = None
    sources: Tuple[SourceWorldArtifact, ...] = ()
    status: str = "unknown"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceWorldComparisonReport:
    artifacts: List[SourceWorldArtifact] = field(default_factory=list)
    pairs: List[SourceWorldPair] = field(default_factory=list)
    dat_count: int = 0
    v66_dat_count: int = 0
    legacy_ed_count: int = 0
    lta_count: int = 0
    ltc_count: int = 0
    paired_source_count: int = 0
    paired_v66_dat_count: int = 0
    recommendation: str = "use_paired_sources_as_golden_fixtures_only"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceGeometrySummary:
    path: str
    format: str
    status: str = "unknown"
    model_count: int = 0
    point_count: int = 0
    polygon_count: int = 0
    material_count: int = 0
    metadata: Dict[str, object] = field(default_factory=dict)
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DatWorldModelSemanticSummary:
    index: int
    name: str
    point_count: int = 0
    polygon_count: int = 0
    texture_count: int = 0
    surface_count: int = 0
    raw_size: int = 0


@dataclass(frozen=True)
class DatOutputSemanticSummary:
    path: str
    status: str = "unknown"
    version: Optional[int] = None
    world_model_count: int = 0
    world_model_summaries: Tuple[DatWorldModelSemanticSummary, ...] = ()
    terrain_model_count: int = 0
    terrain_model_names: Tuple[str, ...] = ()
    terrain_point_count: int = 0
    terrain_polygon_count: int = 0
    texture_count: int = 0
    object_count: Optional[int] = None
    world_tree_node_count: int = 0
    world_tree_leaf_count: int = 0
    lightmap_grid_size: float = 0.0
    object_data_size: int = 0
    render_data_size: int = 0
    physics_bsp_present: bool = False
    physics_point_count: int = 0
    physics_polygon_count: int = 0
    physics_node_count: int = 0
    physics_block_cell_count: int = 0
    vis_bsp_present: bool = False
    vis_leaf_count: int = 0
    vis_node_count: int = 0
    portal_reference_count: int = 0
    terrain_tail_node_count: int = 0
    terrain_tail_polygon_list_count: int = 0
    terrain_render_chunk_count: int = 0
    terrain_render_fully_decoded_count: int = 0
    terrain_lightmapped_polygon_count: int = 0
    terrain_lightmap_pixel_count: int = 0
    top_level_section_sizes: Dict[str, int] = field(default_factory=dict)
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticSystemComparison:
    system: str
    status: str
    source_detail: str = ""
    dat_detail: str = ""
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceOutputSemanticComparison:
    stem: str
    source_path: str
    dat_path: str
    status: str
    source: SourceGeometrySummary
    dat: DatOutputSemanticSummary
    systems: Tuple[SemanticSystemComparison, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceOutputSemanticReport:
    comparisons: List[SourceOutputSemanticComparison] = field(default_factory=list)
    paired_fixture_count: int = 0
    compared_source_count: int = 0
    comparable_source_count: int = 0
    recommendation: str = "use_semantic_fixture_gaps_to_drive_internal_v66_rebuild"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BlackBoxCompilerSystemComparison:
    system: str
    status: str
    reference_detail: str = ""
    generated_detail: str = ""
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BlackBoxWorldModelComparison:
    index: int
    name: str
    status: str
    reference_detail: str = ""
    generated_detail: str = ""
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BlackBoxProcessorLogSummary:
    path: str
    status: str = "unknown"
    processing_path: str = ""
    world_tree_nodes: Optional[int] = None
    world_tree_depth: Optional[int] = None
    tree_depth: Optional[int] = None
    runtime_minutes: Optional[float] = None
    lightmap_grid_size: Optional[float] = None
    btw_poly_split_count: Optional[int] = None
    joined_polygon_count: Optional[int] = None
    joined_removed_polygon_count: Optional[int] = None
    problem_brush_count: Optional[int] = None
    unseen_removed_polygon_count: Optional[int] = None
    t_junction_vertex_count: Optional[int] = None
    input_polygon_count: Optional[int] = None
    input_vertex_count: Optional[int] = None
    output_polygon_count: Optional[int] = None
    output_vertex_count: Optional[int] = None
    lightmap_data_size: Optional[int] = None
    object_count: Optional[int] = None
    model_polygon_counts: Tuple[Tuple[str, int], ...] = ()
    warning_counts: Dict[str, int] = field(default_factory=dict)
    warnings: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BlackBoxCompilerHarnessReport:
    status: str
    processor_path: str
    source_ed_path: str
    reference_dat_path: str = ""
    work_dir: str = ""
    project_dir: str = ""
    processor_project_dir: str = ""
    worlds_dir: str = ""
    copied_ed_path: str = ""
    output_dat_path: str = ""
    command: Tuple[str, ...] = ()
    returncode: Optional[int] = None
    elapsed_seconds: float = 0.0
    stdout_path: str = ""
    stderr_path: str = ""
    log_paths: Tuple[str, ...] = ()
    captured_output: bool = False
    output_preseeded: bool = False
    output_rewritten: bool = False
    reference: Optional[DatOutputSemanticSummary] = None
    generated: Optional[DatOutputSemanticSummary] = None
    comparisons: Tuple[BlackBoxCompilerSystemComparison, ...] = ()
    world_model_comparisons: Tuple[BlackBoxWorldModelComparison, ...] = ()
    processor_logs: Tuple[BlackBoxProcessorLogSummary, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BlackBoxCompilerCorpusRun:
    stem: str
    source_ed_path: str
    reference_dat_path: str
    status: str
    report: BlackBoxCompilerHarnessReport


@dataclass(frozen=True)
class BlackBoxCompilerCorpusReport:
    status: str
    processor_path: str
    worlds_dir: str = ""
    work_dir: str = ""
    processor_project_dir: str = ""
    fixture_count: int = 0
    ran_count: int = 0
    matched_count: int = 0
    differing_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    runs: Tuple[BlackBoxCompilerCorpusRun, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BlackBoxCompilerManualValidation:
    status: str = "not_validated"
    tested_at: str = ""
    fresh_load: Optional[bool] = None
    visuals_ok: Optional[bool] = None
    collision_ok: Optional[bool] = None
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BlackBoxCompilerAcceptanceReport:
    status: str
    harness: BlackBoxCompilerHarnessReport
    manual_validation: BlackBoxCompilerManualValidation = field(default_factory=BlackBoxCompilerManualValidation)
    accepted_difference_systems: Tuple[str, ...] = DEFAULT_BLACK_BOX_ACCEPTED_REGENERATED_SYSTEMS
    mismatched_systems: Tuple[str, ...] = ()
    accepted_differences: Tuple[str, ...] = ()
    unaccepted_differences: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SurrogateBlackBoxCompilerHarnessReport:
    status: str
    processor_path: str
    source_dat_path: str
    reference_dat_path: str = ""
    generated_ed_path: str = ""
    work_dir: str = ""
    selected_model_names: Tuple[str, ...] = ()
    surrogate_status: str = ""
    surrogate_model_count: int = 0
    surrogate_point_count: int = 0
    surrogate_polygon_count: int = 0
    surrogate_byte_count: int = 0
    surrogate_wrapper_kind: str = ""
    surrogate_wrapper_block_count: int = 0
    harness: Optional[BlackBoxCompilerHarnessReport] = None
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PrefabSurrogateAcceptanceReport:
    status: str
    source_dat_path: str
    generated_ed_path: str = ""
    work_dir: str = ""
    prefab_install_path: str = ""
    selected_model_names: Tuple[str, ...] = ()
    surrogate_status: str = ""
    surrogate_model_count: int = 0
    surrogate_point_count: int = 0
    surrogate_polygon_count: int = 0
    surrogate_object_count: int = 0
    surrogate_object_property_count: int = 0
    surrogate_byte_count: int = 0
    generated_brush_count: int = 0
    generated_polygon_count: int = 0
    generated_object_count: int = 0
    generated_object_class_counts: Dict[str, int] = field(default_factory=dict)
    reference_prefab_path: str = ""
    reference_brush_count: int = 0
    reference_polygon_count: int = 0
    reference_object_count: int = 0
    reference_object_class_counts: Dict[str, int] = field(default_factory=dict)
    manual_steps: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PrefabSurrogateCorpusCandidate:
    model_name: str
    status: str
    point_count: int = 0
    polygon_count: int = 0
    texture_count: int = 0
    generated_ed_path: str = ""
    prefab_install_path: str = ""
    acceptance: Optional[PrefabSurrogateAcceptanceReport] = None
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PrefabSurrogateAcceptanceCorpusReport:
    status: str
    source_dat_path: str
    work_dir: str = ""
    prefab_install_dir: str = ""
    candidate_count: int = 0
    generated_count: int = 0
    ready_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    candidates: Tuple[PrefabSurrogateCorpusCandidate, ...] = ()
    manual_steps: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PrefabSurrogateCompositeModelSummary:
    name: str
    point_count: int = 0
    polygon_count: int = 0
    texture_count: int = 0
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PrefabSurrogateCompositeAcceptanceReport:
    status: str
    source_dat_path: str
    generated_ed_path: str = ""
    work_dir: str = ""
    prefab_install_path: str = ""
    hierarchy_kind: str = "direct_root"
    group_name: str = ""
    selected_model_names: Tuple[str, ...] = ()
    model_count: int = 0
    point_count: int = 0
    polygon_count: int = 0
    object_count: int = 0
    generated_byte_count: int = 0
    generated_object_class_counts: Dict[str, int] = field(default_factory=dict)
    models: Tuple[PrefabSurrogateCompositeModelSummary, ...] = ()
    acceptance: Optional[PrefabSurrogateAcceptanceReport] = None
    manual_steps: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PrefabSurrogateCompositeCorpusCandidate:
    group_name: str
    hierarchy_kind: str = "direct_root"
    model_names: Tuple[str, ...] = ()
    status: str = "unknown"
    model_count: int = 0
    point_count: int = 0
    polygon_count: int = 0
    generated_ed_path: str = ""
    prefab_install_path: str = ""
    acceptance: Optional[PrefabSurrogateCompositeAcceptanceReport] = None
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PrefabSurrogateCompositeCorpusReport:
    status: str
    source_dat_path: str
    work_dir: str = ""
    prefab_install_dir: str = ""
    hierarchy_kind: str = "direct_root"
    group_count: int = 0
    generated_count: int = 0
    ready_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    candidates: Tuple[PrefabSurrogateCompositeCorpusCandidate, ...] = ()
    manual_steps: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FullWorldSkeletonAcceptanceReport:
    status: str
    source_dat_path: str
    generated_ed_path: str = ""
    work_dir: str = ""
    world_install_path: str = ""
    group_name: str = ""
    include_validation_floor: bool = False
    include_terrain_support_patch: bool = False
    include_physics_shell_patch: bool = False
    selected_model_names: Tuple[str, ...] = ()
    model_count: int = 0
    point_count: int = 0
    polygon_count: int = 0
    object_count: int = 0
    object_property_count: int = 0
    generated_byte_count: int = 0
    node_hierarchy_byte_count: int = 0
    wrapper_kind: str = ""
    wrapper_block_count: int = 0
    generated_object_class_counts: Dict[str, int] = field(default_factory=dict)
    max_processor_brushes: int = 0
    max_processor_polygons: int = 0
    models: Tuple[PrefabSurrogateCompositeModelSummary, ...] = ()
    terrain_cutout_coverage_manifest_path: str = ""
    terrain_cutout_coverage: Optional["TerrainCutoutCoverageReport"] = None
    terrain_support_source_coverage_manifest_path: str = ""
    terrain_support_source_coverage: Optional["TerrainSupportSourceCoverageReport"] = None
    manual_steps: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DatToEdSelectionModelReport:
    index: int
    name: str
    status: str
    point_count: int = 0
    polygon_count: int = 0
    texture_count: int = 0
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    helper_roles: Dict[str, int] = field(default_factory=dict)
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DatToEdSelectionReport:
    status: str
    source_dat_path: str
    requested_model_names: Tuple[str, ...] = ()
    selected_model_names: Tuple[str, ...] = ()
    terrain_model_names: Tuple[str, ...] = ()
    terrain_support_model_name: str = ""
    include_terrain_support_patch: bool = False
    physics_shell_model_name: str = ""
    include_physics_shell_patch: bool = False
    total_model_count: int = 0
    selected_model_count: int = 0
    terrain_support_source_count: int = 0
    physics_shell_source_count: int = 0
    excluded_model_count: int = 0
    total_point_count: int = 0
    total_polygon_count: int = 0
    selected_point_count: int = 0
    selected_polygon_count: int = 0
    status_counts: Dict[str, int] = field(default_factory=dict)
    helper_only_exclusions_by_role: Dict[str, Dict[str, int]] = field(default_factory=dict)
    limits: Dict[str, object] = field(default_factory=dict)
    models: Tuple[DatToEdSelectionModelReport, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FullWorldSkeletonCompiledValidationReport:
    status: str
    generated_ed_path: str
    compiled_dat_path: str
    processor_log_paths: Tuple[str, ...] = ()
    start_point: Optional[Tuple[float, float, float]] = None
    move_player_to_floor: Optional[bool] = None
    physics_floor_y: Optional[float] = None
    physics_floor_drop: Optional[float] = None
    max_start_floor_drop: float = 256.0
    dat: Optional[DatOutputSemanticSummary] = None
    processor_logs: Tuple[BlackBoxProcessorLogSummary, ...] = ()
    manual_validation: BlackBoxCompilerManualValidation = field(default_factory=BlackBoxCompilerManualValidation)
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TerrainCutoutCoverageCandidate:
    candidate_id: str
    classification: str
    model_names: Tuple[str, ...] = ()
    model_indices: Tuple[int, ...] = ()
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    footprint_area: float = 0.0
    sample_count: int = 0
    missing_sample_count: int = 0
    missing_ratio: float = 0.0
    terrain_hit_count: int = 0
    terrain_texture_hits: Tuple[Tuple[str, int], ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TerrainCutoutCoverageReport:
    status: str
    source_dat_path: str
    terrain_model_name: str = "Terrain0"
    terrain_polygon_count: int = 0
    terrain_coverage_polygon_count: int = 0
    sampled_model_count: int = 0
    candidate_count: int = 0
    covered_cutout_count: int = 0
    partial_cutout_count: int = 0
    terrain_present_count: int = 0
    uncertain_count: int = 0
    skipped_model_count: int = 0
    candidates: Tuple[TerrainCutoutCoverageCandidate, ...] = ()
    ignored_terrain_textures: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TerrainSupportSourceCoverageGap:
    source_polygon_index: int
    texture_name: str
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    sample_count: int = 0
    missing_sample_count: int = 0
    missing_ratio: float = 0.0


@dataclass(frozen=True)
class TerrainSupportSourceCoverageReport:
    status: str
    source_dat_path: str
    generated_ed_path: str
    terrain_model_name: str = "Terrain0"
    source_polygon_count: int = 0
    sampled_source_polygon_count: int = 0
    generated_coverage_polygon_count: int = 0
    sample_count: int = 0
    covered_sample_count: int = 0
    missing_sample_count: int = 0
    missing_polygon_count: int = 0
    missing_ratio: float = 0.0
    source_texture_counts: Tuple[Tuple[str, int], ...] = ()
    generated_texture_counts: Tuple[Tuple[str, int], ...] = ()
    missing_texture_sample_counts: Tuple[Tuple[str, int], ...] = ()
    gaps: Tuple[TerrainSupportSourceCoverageGap, ...] = ()
    ignored_terrain_textures: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PrefabSurrogatePackEntry:
    group_name: str
    status: str
    hierarchy_kind: str = "named_group"
    model_names: Tuple[str, ...] = ()
    model_count: int = 0
    point_count: int = 0
    polygon_count: int = 0
    generated_ed_path: str = ""
    staged_prefab_path: str = ""
    filename: str = ""
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PrefabSurrogatePackReport:
    status: str
    source_dat_path: str
    work_dir: str = ""
    staging_prefab_dir: str = ""
    manifest_path: str = ""
    hierarchy_kind: str = "named_group"
    entry_count: int = 0
    ready_count: int = 0
    staged_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    corpus: Optional[PrefabSurrogateCompositeCorpusReport] = None
    entries: Tuple[PrefabSurrogatePackEntry, ...] = ()
    manual_steps: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


def build_compiler_strategy_report(
    *,
    lithtech_root: Optional[str] = None,
    extra_candidates: Sequence[CompilerCandidate] = (),
) -> CompilerStrategyReport:
    """Return the current Stage 7 compiler-backend assessment."""
    candidates = list(_built_in_candidates())
    candidates.extend(_local_lithtech_candidates(lithtech_root))
    candidates.extend(extra_candidates)
    evaluated = [evaluate_candidate(candidate) for candidate in candidates]
    compatible = tuple(
        candidate.candidate_id
        for candidate in evaluated
        if candidate.status == "compatible"
    )
    recommendation = (
        "integrate_optional_backend_after_golden_tests"
        if compatible
        else "continue_internal_v66_rebuild_pipeline"
    )
    notes = (
        "A compatible backend must emit MM9 DAT version 66 full-world files and rebuild all required derived systems.",
        "Partial readers/compilers remain useful as inspection fixtures but are not full terrain rebuild backends.",
    )
    return CompilerStrategyReport(
        candidates=evaluated,
        compatible_candidate_ids=compatible,
        recommendation=recommendation,
        notes=notes,
    )


def build_source_world_comparison_report(
    *,
    worlds_dir: Optional[str] = None,
    source_roots: Sequence[str] = (),
    recursive_source_roots: bool = True,
    max_source_files: int = 4096,
) -> SourceWorldComparisonReport:
    """Catalog source-world artifacts and pair same-stem sources with DATs."""
    artifacts: List[SourceWorldArtifact] = []
    if worlds_dir:
        artifacts.extend(_scan_artifact_dir(worlds_dir, recursive=False, max_files=max_source_files))
    for root in source_roots:
        artifacts.extend(_scan_artifact_dir(root, recursive=recursive_source_roots, max_files=max_source_files))

    by_stem: Dict[str, List[SourceWorldArtifact]] = {}
    for artifact in artifacts:
        by_stem.setdefault(artifact.stem.upper(), []).append(artifact)

    pairs: List[SourceWorldPair] = []
    for stem_key in sorted(by_stem):
        items = by_stem[stem_key]
        dats = [item for item in items if item.format == "dat"]
        sources = tuple(item for item in items if item.format in {"ed", "lta", "ltc"})
        if not dats and not sources:
            continue
        dat = next((item for item in dats if item.status == "v66_dat"), dats[0] if dats else None)
        notes: List[str] = []
        if dat is None:
            status = "source_without_dat"
            notes.append("source artifact has no same-stem DAT output")
        elif not sources:
            status = "dat_without_source"
            notes.append("compiled DAT has no same-stem source artifact")
        elif dat.status != "v66_dat":
            status = "source_with_non_v66_dat"
            notes.append("same-stem DAT is not an MM9 v66 compiled level")
        else:
            status = "paired_v66_dat_with_source"
            notes.append("same-stem source artifact can be compared with compiled v66 DAT")
        pairs.append(SourceWorldPair(
            stem=stem_key,
            dat=dat,
            sources=sources,
            status=status,
            notes=tuple(notes),
        ))

    dat_count = sum(1 for item in artifacts if item.format == "dat")
    v66_dat_count = sum(1 for item in artifacts if item.status == "v66_dat")
    legacy_ed_count = sum(1 for item in artifacts if item.format == "ed")
    lta_count = sum(1 for item in artifacts if item.format == "lta")
    ltc_count = sum(1 for item in artifacts if item.format == "ltc")
    paired_sources = [
        source
        for pair in pairs
        if pair.status == "paired_v66_dat_with_source"
        for source in pair.sources
    ]
    paired_v66_dat_count = sum(1 for pair in pairs if pair.status == "paired_v66_dat_with_source")
    notes = (
        "Same-stem ED/LTA/LTC sources are comparison fixtures, not compiler backends.",
        "A paired source/DAT fixture is useful only after geometry, object, visibility, lighting, and render outputs are compared.",
    )
    return SourceWorldComparisonReport(
        artifacts=artifacts,
        pairs=pairs,
        dat_count=dat_count,
        v66_dat_count=v66_dat_count,
        legacy_ed_count=legacy_ed_count,
        lta_count=lta_count,
        ltc_count=ltc_count,
        paired_source_count=len(paired_sources),
        paired_v66_dat_count=paired_v66_dat_count,
        notes=notes,
    )


def build_source_output_semantic_report(
    *,
    worlds_dir: Optional[str] = None,
    source_roots: Sequence[str] = (),
    stems: Optional[Sequence[str]] = None,
    max_pairs: Optional[int] = None,
    recursive_source_roots: bool = True,
    max_source_files: int = 4096,
) -> SourceOutputSemanticReport:
    """Compare same-stem source-world fixtures with compiled DAT semantics.

    This report is deliberately a coverage/semantics report, not an output
    equivalence claim.  The source readers recover brush/material geometry; the
    DAT side exposes compiled systems such as object data, PhysicsBSP, VisBSP,
    lightmaps, light grid headers, and render topology.
    """
    corpus = build_source_world_comparison_report(
        worlds_dir=worlds_dir,
        source_roots=source_roots,
        recursive_source_roots=recursive_source_roots,
        max_source_files=max_source_files,
    )
    allowed_stems = {str(stem).upper() for stem in stems or ()}
    comparisons: List[SourceOutputSemanticComparison] = []
    paired = [
        pair
        for pair in corpus.pairs
        if pair.status == "paired_v66_dat_with_source" and pair.dat is not None
    ]
    if allowed_stems:
        paired = [pair for pair in paired if pair.stem.upper() in allowed_stems]
    if max_pairs is not None:
        paired = paired[:max(0, int(max_pairs))]

    dat_cache: Dict[str, DatOutputSemanticSummary] = {}
    for pair in paired:
        dat_path = pair.dat.path if pair.dat is not None else ""
        dat_summary = dat_cache.get(dat_path)
        if dat_summary is None:
            dat_summary = _load_dat_output_semantic_summary(dat_path)
            dat_cache[dat_path] = dat_summary
        for source in pair.sources:
            source_summary = _load_source_geometry_summary(source.path, source.format)
            systems = _compare_semantic_systems(source_summary, dat_summary)
            status = _semantic_comparison_status(source_summary, dat_summary)
            notes = _semantic_comparison_notes(source_summary, dat_summary, systems)
            comparisons.append(SourceOutputSemanticComparison(
                stem=pair.stem,
                source_path=source.path,
                dat_path=dat_path,
                status=status,
                source=source_summary,
                dat=dat_summary,
                systems=tuple(systems),
                notes=tuple(notes),
            ))

    comparable = sum(
        1
        for comparison in comparisons
        if comparison.status == "compared_with_compiled_only_gaps"
    )
    notes = (
        "Source-world readers currently recover geometry/materials, not a complete source scene graph.",
        "DAT summaries expose compiled-only systems that any future backend must reproduce.",
    )
    return SourceOutputSemanticReport(
        comparisons=comparisons,
        paired_fixture_count=len(paired),
        compared_source_count=len(comparisons),
        comparable_source_count=comparable,
        notes=notes,
    )


def run_black_box_surrogate_ed_to_dat_harness(
    *,
    processor_path: str,
    source_dat_path: str,
    reference_dat_path: Optional[str] = None,
    model_names: Sequence[str] = (),
    max_models: Optional[int] = None,
    include_skyboxes: bool = False,
    work_dir: Optional[str] = None,
    processor_project_dir: Optional[str] = None,
    processor_prefix_args: Sequence[str] = (),
    option_template: Sequence[str] = DEFAULT_LITH21_PROCESSOR_OPTIONS,
    world_argument_template: str = "{ed_no_ext}",
    timeout_seconds: float = 900.0,
    preseed_reference_dat: bool = True,
) -> SurrogateBlackBoxCompilerHarnessReport:
    """Generate a full-level surrogate ED from DAT data and run the black-box harness."""
    from features.dat_editing import surrogate_ed

    processor = os.path.abspath(processor_path)
    source_dat = os.path.abspath(source_dat_path)
    reference_dat = os.path.abspath(reference_dat_path) if reference_dat_path else source_dat
    selected_names = tuple(str(name) for name in model_names)
    notes: List[str] = [
        "Stage 7K surrogate compiler runs are research-only and do not make a processor save-backend compatible.",
        "The generated ED is a full-level wrapper around DAT-derived surrogate brushes, not a full source reconstruction.",
        "LithTech 2.1 Processor.exe can show a Processing Options modal; current harness runs may require manual OK or explicit GUI automation.",
    ]
    if not os.path.exists(source_dat):
        return SurrogateBlackBoxCompilerHarnessReport(
            status="source_dat_missing",
            processor_path=processor,
            source_dat_path=source_dat,
            reference_dat_path=reference_dat,
            selected_model_names=selected_names,
            notes=tuple(notes + [f"source DAT was not found: {source_dat}"]),
        )
    if reference_dat and not os.path.exists(reference_dat):
        return SurrogateBlackBoxCompilerHarnessReport(
            status="reference_dat_missing",
            processor_path=processor,
            source_dat_path=source_dat,
            reference_dat_path=reference_dat,
            selected_model_names=selected_names,
            notes=tuple(notes + [f"reference DAT was not found: {reference_dat}"]),
        )

    work_root = os.path.abspath(work_dir) if work_dir else tempfile.mkdtemp(prefix="mm9_stage7k_")
    source_dir = os.path.join(work_root, "surrogate_source")
    os.makedirs(source_dir, exist_ok=True)
    source_stem = os.path.splitext(os.path.basename(source_dat))[0]
    generated_ed = os.path.join(source_dir, f"{source_stem}_surrogate.ED")

    surrogate_report = surrogate_ed.write_full_level_surrogate_legacy_ed_from_dat(
        source_dat,
        generated_ed,
        model_names=selected_names,
        max_models=max_models,
        include_skyboxes=include_skyboxes,
    )
    if surrogate_report.status != "full_level_surrogate_ed_built":
        return SurrogateBlackBoxCompilerHarnessReport(
            status="surrogate_ed_build_failed",
            processor_path=processor,
            source_dat_path=source_dat,
            reference_dat_path=reference_dat,
            generated_ed_path=generated_ed,
            work_dir=work_root,
            selected_model_names=selected_names,
            surrogate_status=surrogate_report.status,
            surrogate_model_count=surrogate_report.model_count,
            surrogate_point_count=surrogate_report.point_count,
            surrogate_polygon_count=surrogate_report.polygon_count,
            surrogate_byte_count=surrogate_report.generated_byte_count,
            surrogate_wrapper_kind=surrogate_report.wrapper_kind,
            surrogate_wrapper_block_count=surrogate_report.wrapper_block_count,
            notes=tuple(_unique_text(notes + list(surrogate_report.blockers) + list(surrogate_report.cautions))),
        )

    harness = run_black_box_ed_to_dat_harness(
        processor_path=processor,
        source_ed_path=generated_ed,
        reference_dat_path=reference_dat,
        work_dir=os.path.join(work_root, "harness"),
        processor_project_dir=processor_project_dir,
        processor_prefix_args=processor_prefix_args,
        option_template=option_template,
        world_argument_template=world_argument_template,
        timeout_seconds=timeout_seconds,
        preseed_reference_dat=preseed_reference_dat,
    )
    if harness.status in {
        "compiled_and_compared",
        "compiled_with_semantic_differences",
        "compiled",
    }:
        status = harness.status
    else:
        status = f"surrogate_{harness.status}"
    if reference_dat == source_dat:
        notes.append("Reference comparison defaults to the source DAT; subset surrogates are expected to differ if compilation succeeds.")
    return SurrogateBlackBoxCompilerHarnessReport(
        status=status,
        processor_path=processor,
        source_dat_path=source_dat,
        reference_dat_path=reference_dat,
        generated_ed_path=generated_ed,
        work_dir=work_root,
        selected_model_names=selected_names,
        surrogate_status=surrogate_report.status,
        surrogate_model_count=surrogate_report.model_count,
        surrogate_point_count=surrogate_report.point_count,
        surrogate_polygon_count=surrogate_report.polygon_count,
        surrogate_byte_count=surrogate_report.generated_byte_count,
        surrogate_wrapper_kind=surrogate_report.wrapper_kind,
        surrogate_wrapper_block_count=surrogate_report.wrapper_block_count,
        harness=harness,
        notes=tuple(_unique_text(notes)),
    )


def build_prefab_surrogate_acceptance_report(
    *,
    source_dat_path: str,
    model_names: Sequence[str] = (),
    max_models: Optional[int] = None,
    include_skyboxes: bool = False,
    work_dir: Optional[str] = None,
    prefab_install_dir: Optional[str] = None,
    reference_prefab_path: Optional[str] = None,
    output_filename: str = "",
    brush_name_prefix: str = "Brush",
    grouped_hierarchy: bool = False,
    group_name: str = "Group",
) -> PrefabSurrogateAcceptanceReport:
    """Build a prefab-style surrogate ED and prepare a manual DEDit acceptance checklist.

    This harness deliberately stops before launching DEDit or writing into the
    user's prefab library.  It creates an isolated generated `.ed`, verifies the
    generated file with the legacy ED reader, optionally summarizes a real
    prefab sample for comparison, and returns the exact manual test steps.
    """
    from features.dat_editing import legacy_ed, surrogate_ed

    source_dat = os.path.abspath(source_dat_path)
    selected_names = tuple(str(name) for name in model_names)
    notes: List[str] = [
        "Stage 7N prefab acceptance reports are research-only and do not make surrogate ED output a save backend.",
        "The generated file must still be accepted manually by old DEDit before it is useful as a prefab source.",
    ]
    if not os.path.exists(source_dat):
        return PrefabSurrogateAcceptanceReport(
            status="source_dat_missing",
            source_dat_path=source_dat,
            selected_model_names=selected_names,
            notes=tuple(notes + [f"source DAT was not found: {source_dat}"]),
            blockers=(f"source DAT was not found: {source_dat}",),
        )

    work_root = os.path.abspath(work_dir) if work_dir else tempfile.mkdtemp(prefix="mm9_stage7n_")
    source_dir = os.path.join(work_root, "prefab_surrogate_source")
    os.makedirs(source_dir, exist_ok=True)
    source_stem = os.path.splitext(os.path.basename(source_dat))[0]
    filename = os.path.basename(output_filename) if output_filename else f"{source_stem}_surrogate_prefab.ed"
    generated_ed = os.path.join(source_dir, filename)
    install_path = ""
    if prefab_install_dir:
        install_path = os.path.join(os.path.abspath(prefab_install_dir), filename)

    if grouped_hierarchy:
        surrogate_report = surrogate_ed.write_grouped_prefab_surrogate_legacy_ed_from_dat(
            source_dat,
            generated_ed,
            model_names=selected_names,
            max_models=max_models,
            include_skyboxes=include_skyboxes,
            brush_name_prefix=brush_name_prefix,
            group_name=group_name,
        )
        expected_surrogate_status = "grouped_prefab_surrogate_ed_built"
    else:
        surrogate_report = surrogate_ed.write_prefab_surrogate_legacy_ed_from_dat(
            source_dat,
            generated_ed,
            model_names=selected_names,
            max_models=max_models,
            include_skyboxes=include_skyboxes,
            brush_name_prefix=brush_name_prefix,
        )
        expected_surrogate_status = "prefab_surrogate_ed_built"
    if surrogate_report.status != expected_surrogate_status:
        blockers = tuple(_unique_text(tuple(surrogate_report.blockers)))
        return PrefabSurrogateAcceptanceReport(
            status="prefab_surrogate_build_failed",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            work_dir=work_root,
            prefab_install_path=install_path,
            selected_model_names=selected_names,
            surrogate_status=surrogate_report.status,
            surrogate_model_count=surrogate_report.model_count,
            surrogate_point_count=surrogate_report.point_count,
            surrogate_polygon_count=surrogate_report.polygon_count,
            surrogate_object_count=surrogate_report.object_count,
            surrogate_object_property_count=surrogate_report.object_property_count,
            surrogate_byte_count=surrogate_report.generated_byte_count,
            blockers=blockers,
            cautions=surrogate_report.cautions,
            notes=tuple(_unique_text(notes + list(surrogate_report.blockers) + list(surrogate_report.cautions))),
        )

    generated_brush_count = 0
    generated_polygon_count = 0
    generated_object_count = 0
    generated_class_counts: Dict[str, int] = {}
    try:
        scene = legacy_ed.load_legacy_ed_geometry_scene(generated_ed)
        object_report = legacy_ed.load_legacy_ed_object_scan_report(generated_ed)
        generated_brush_count = int(scene.metadata.get("recovered_brush_count", 0) or 0)
        generated_polygon_count = int(scene.metadata.get("recovered_polygon_count", 0) or 0)
        generated_object_count = int(object_report.object_count)
        generated_class_counts = {
            str(name): int(count)
            for name, count in object_report.class_counts.items()
        }
    except Exception as exc:
        return PrefabSurrogateAcceptanceReport(
            status="prefab_surrogate_parse_failed",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            work_dir=work_root,
            prefab_install_path=install_path,
            selected_model_names=selected_names,
            surrogate_status=surrogate_report.status,
            surrogate_model_count=surrogate_report.model_count,
            surrogate_point_count=surrogate_report.point_count,
            surrogate_polygon_count=surrogate_report.polygon_count,
            surrogate_object_count=surrogate_report.object_count,
            surrogate_object_property_count=surrogate_report.object_property_count,
            surrogate_byte_count=surrogate_report.generated_byte_count,
            blockers=(f"generated prefab surrogate could not be parsed: {exc}",),
            cautions=surrogate_report.cautions,
            notes=tuple(_unique_text(notes + [f"generated prefab surrogate could not be parsed: {exc}"])),
        )

    reference_path = os.path.abspath(reference_prefab_path) if reference_prefab_path else ""
    reference_brush_count = 0
    reference_polygon_count = 0
    reference_object_count = 0
    reference_class_counts: Dict[str, int] = {}
    cautions = list(surrogate_report.cautions)
    if reference_path:
        if not os.path.exists(reference_path):
            cautions.append(f"reference prefab was not found: {reference_path}")
        else:
            try:
                reference_scene = legacy_ed.load_legacy_ed_geometry_scene(reference_path)
                reference_objects = legacy_ed.load_legacy_ed_object_scan_report(reference_path)
                reference_brush_count = int(reference_scene.metadata.get("recovered_brush_count", 0) or 0)
                reference_polygon_count = int(reference_scene.metadata.get("recovered_polygon_count", 0) or 0)
                reference_object_count = int(reference_objects.object_count)
                reference_class_counts = {
                    str(name): int(count)
                    for name, count in reference_objects.class_counts.items()
                }
            except Exception as exc:
                cautions.append(f"reference prefab could not be parsed: {exc}")

    target_text = install_path or "the old DEDit PreFabs directory, for example C:\\lithtech\\PreFabs"
    manual_steps = (
        f"Copy {generated_ed} to {target_text}.",
        "Open old LithTech 2.1 DEDit with the MM9.dep project and open a known-good world.",
        "Find the generated prefab in the prefab browser, instantiate it, and confirm DEDit does not crash.",
        "Save the world through DEDit only if the prefab instantiates; then run Processor.exe as a separate compiler experiment.",
    )
    cautions.extend((
        "This report does not launch DEDit or press the Processor.exe options dialog.",
        "Prefab acceptance is only an editor-source compatibility test; it is not full-world DAT output compatibility.",
    ))
    return PrefabSurrogateAcceptanceReport(
        status="ready_for_manual_prefab_test",
        source_dat_path=source_dat,
        generated_ed_path=generated_ed,
        work_dir=work_root,
        prefab_install_path=install_path,
        selected_model_names=selected_names,
        surrogate_status=surrogate_report.status,
        surrogate_model_count=surrogate_report.model_count,
        surrogate_point_count=surrogate_report.point_count,
        surrogate_polygon_count=surrogate_report.polygon_count,
        surrogate_object_count=surrogate_report.object_count,
        surrogate_object_property_count=surrogate_report.object_property_count,
        surrogate_byte_count=surrogate_report.generated_byte_count,
        generated_brush_count=generated_brush_count,
        generated_polygon_count=generated_polygon_count,
        generated_object_count=generated_object_count,
        generated_object_class_counts=generated_class_counts,
        reference_prefab_path=reference_path,
        reference_brush_count=reference_brush_count,
        reference_polygon_count=reference_polygon_count,
        reference_object_count=reference_object_count,
        reference_object_class_counts=reference_class_counts,
        manual_steps=manual_steps,
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes + list(surrogate_report.notes))),
    )


def build_prefab_surrogate_acceptance_corpus_report(
    *,
    source_dat_path: str,
    model_names: Sequence[str] = (),
    max_models: int = 8,
    max_points: int = 256,
    max_polygons: int = 256,
    include_skyboxes: bool = False,
    work_dir: Optional[str] = None,
    prefab_install_dir: Optional[str] = None,
    output_prefix: str = "",
    brush_name_prefix: str = "Brush",
) -> PrefabSurrogateAcceptanceCorpusReport:
    """Generate a small corpus of one-brush prefab surrogates for manual DEDit tests."""
    try:
        from core import bsp
    except Exception as exc:
        return PrefabSurrogateAcceptanceCorpusReport(
            status="dat_parser_unavailable",
            source_dat_path=os.path.abspath(source_dat_path),
            blockers=(f"DAT parser is unavailable: {exc}",),
        )

    source_dat = os.path.abspath(source_dat_path)
    selected_names = tuple(str(name) for name in model_names)
    install_dir = os.path.abspath(prefab_install_dir) if prefab_install_dir else ""
    notes: List[str] = [
        "Stage 7O corpus reports generate individual one-brush prefab surrogates for manual DEDit acceptance testing.",
        "A ready corpus still requires manual DEDit insertion, Processor.exe compilation, and in-game validation.",
    ]
    cautions = [
        "Generated prefabs are DAT-derived compiled polygons, not original authoring CSG brushes.",
        "Keep early manual batches small; DEDit/Processor acceptance has only been proven for simple one-brush prefabs so far.",
    ]
    if not os.path.exists(source_dat):
        return PrefabSurrogateAcceptanceCorpusReport(
            status="source_dat_missing",
            source_dat_path=source_dat,
            prefab_install_dir=install_dir,
            blockers=(f"source DAT was not found: {source_dat}",),
            notes=tuple(notes),
            cautions=tuple(cautions),
        )

    try:
        with open(source_dat, "rb") as f:
            parsed = bsp.parse(f.read())
    except Exception as exc:
        return PrefabSurrogateAcceptanceCorpusReport(
            status="dat_parse_failed",
            source_dat_path=source_dat,
            prefab_install_dir=install_dir,
            blockers=(f"DAT parse failed: {exc}",),
            notes=tuple(notes),
            cautions=tuple(cautions),
        )

    source_stem = os.path.splitext(os.path.basename(source_dat))[0]
    work_root = os.path.abspath(work_dir) if work_dir else tempfile.mkdtemp(prefix="mm9_stage7o_")
    selected_lookup = {name.lower(): index for index, name in enumerate(selected_names)}
    candidates: List[Tuple[int, object, int, int, int, Tuple[str, ...]]] = []
    unmatched_selected = set(selected_lookup)
    for index, model in enumerate(getattr(parsed, "world_models", ()) or ()):
        name = str(getattr(model, "name", "") or f"WorldModel{index}")
        name_key = name.lower()
        if selected_lookup and name_key not in selected_lookup:
            continue
        unmatched_selected.discard(name_key)
        try:
            is_skybox = bool(getattr(model, "is_skybox", lambda: False)())
        except Exception:
            is_skybox = False
        if is_skybox and not include_skyboxes:
            continue
        point_count = len(getattr(model, "points", []) or [])
        polygon_count = len(getattr(model, "polygons", []) or [])
        texture_count = len(getattr(model, "texture_names", []) or [])
        if point_count <= 0 or polygon_count <= 0:
            continue
        skip_notes: List[str] = []
        if point_count > int(max_points):
            skip_notes.append(f"point count {point_count} exceeds corpus limit {int(max_points)}")
        if polygon_count > int(max_polygons):
            skip_notes.append(f"polygon count {polygon_count} exceeds corpus limit {int(max_polygons)}")
        order = selected_lookup.get(name_key, index)
        candidates.append((order, model, point_count, polygon_count, texture_count, tuple(skip_notes)))

    if unmatched_selected:
        notes.extend(
            f"requested model was not found or was filtered out: {name}"
            for name in sorted(unmatched_selected)
        )
    if not candidates:
        return PrefabSurrogateAcceptanceCorpusReport(
            status="no_models_selected",
            source_dat_path=source_dat,
            work_dir=work_root,
            prefab_install_dir=install_dir,
            candidate_count=0,
            blockers=("no DAT world models matched the prefab corpus selection",),
            notes=tuple(_unique_text(notes)),
            cautions=tuple(cautions),
        )

    if selected_lookup:
        candidates.sort(key=lambda item: item[0])
    else:
        candidates.sort(key=lambda item: (item[3], item[2], str(getattr(item[1], "name", "")).lower()))

    generated: List[PrefabSurrogateCorpusCandidate] = []
    generated_count = 0
    for _, model, point_count, polygon_count, texture_count, skip_notes in candidates:
        model_name = str(getattr(model, "name", "") or "WorldModel")
        if skip_notes:
            generated.append(PrefabSurrogateCorpusCandidate(
                model_name=model_name,
                status="skipped_model_too_large",
                point_count=point_count,
                polygon_count=polygon_count,
                texture_count=texture_count,
                notes=skip_notes,
            ))
            continue
        if generated_count >= max(0, int(max_models)):
            generated.append(PrefabSurrogateCorpusCandidate(
                model_name=model_name,
                status="skipped_corpus_limit",
                point_count=point_count,
                polygon_count=polygon_count,
                texture_count=texture_count,
                notes=(f"max_models limit {int(max_models)} was reached",),
            ))
            continue

        prefix = _safe_filename_component(output_prefix or source_stem)
        model_component = _safe_filename_component(model_name)
        output_filename = f"{prefix}_{model_component}_surrogate_prefab.ed"
        acceptance = build_prefab_surrogate_acceptance_report(
            source_dat_path=source_dat,
            model_names=(model_name,),
            max_models=1,
            include_skyboxes=include_skyboxes,
            work_dir=os.path.join(work_root, model_component),
            prefab_install_dir=install_dir,
            output_filename=output_filename,
            brush_name_prefix=brush_name_prefix,
        )
        generated_count += 1
        status = "ready_for_manual_prefab_test" if acceptance.status == "ready_for_manual_prefab_test" else acceptance.status
        generated.append(PrefabSurrogateCorpusCandidate(
            model_name=model_name,
            status=status,
            point_count=point_count,
            polygon_count=polygon_count,
            texture_count=texture_count,
            generated_ed_path=acceptance.generated_ed_path,
            prefab_install_path=acceptance.prefab_install_path,
            acceptance=acceptance,
            notes=tuple(acceptance.blockers or acceptance.cautions[:2]),
        ))

    ready_count = sum(1 for item in generated if item.status == "ready_for_manual_prefab_test")
    failed_count = sum(
        1 for item in generated
        if item.generated_ed_path and item.status != "ready_for_manual_prefab_test"
    )
    skipped_count = sum(1 for item in generated if item.status.startswith("skipped_"))
    if ready_count and failed_count == 0:
        status = "ready_for_manual_prefab_corpus_test"
    elif ready_count:
        status = "completed_with_prefab_failures"
    elif skipped_count == len(generated):
        status = "no_eligible_models"
    else:
        status = "prefab_corpus_build_failed"

    ready_files = [item.generated_ed_path for item in generated if item.status == "ready_for_manual_prefab_test"]
    if install_dir:
        manual_steps = (
            f"Copy the ready `.ed` files from {work_root} into {install_dir}.",
            "Open old LithTech 2.1 DEDit through the MM9.dep project in the same game data directory.",
            "Test the generated prefabs from smallest to largest; insert one at a time into a known-good world.",
            "After DEDit insertion succeeds, compile the source world with Processor.exe and fresh-load the DAT in game.",
        )
    else:
        manual_steps = (
            f"Copy the ready `.ed` files from {work_root} into the real MM9 project data\\PreFabs directory.",
            "Open old LithTech 2.1 DEDit through that project's MM9.dep file.",
            "Test the generated prefabs from smallest to largest; insert one at a time into a known-good world.",
            "After DEDit insertion succeeds, compile the source world with Processor.exe and fresh-load the DAT in game.",
        )
    notes.append(f"ready prefab file count: {len(ready_files)}")
    return PrefabSurrogateAcceptanceCorpusReport(
        status=status,
        source_dat_path=source_dat,
        work_dir=work_root,
        prefab_install_dir=install_dir,
        candidate_count=len(generated),
        generated_count=generated_count,
        ready_count=ready_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        candidates=tuple(generated),
        manual_steps=manual_steps,
        cautions=tuple(cautions),
        notes=tuple(_unique_text(notes)),
    )


def build_prefab_surrogate_composite_acceptance_report(
    *,
    source_dat_path: str,
    model_names: Sequence[str],
    max_models: int = 8,
    max_total_points: int = 2048,
    max_total_polygons: int = 2048,
    max_model_points: int = 1024,
    max_model_polygons: int = 1024,
    include_skyboxes: bool = False,
    work_dir: Optional[str] = None,
    prefab_install_dir: Optional[str] = None,
    output_filename: str = "",
    output_prefix: str = "",
    brush_name_prefix: str = "Brush",
    hierarchy_kind: str = "direct_root",
    group_name: str = "",
) -> PrefabSurrogateCompositeAcceptanceReport:
    """Generate one multi-brush prefab surrogate from an explicit DAT model set.

    Stage 7O proved separate one-brush prefab files.  This Stage 7P harness
    keeps the same conservative byte writer, but writes several related DAT
    world models into one prefab so old DEDit can test whether their relative
    placement survives as a small source-style group.
    """
    try:
        from core import bsp
    except Exception as exc:
        return PrefabSurrogateCompositeAcceptanceReport(
            status="dat_parser_unavailable",
            source_dat_path=os.path.abspath(source_dat_path),
            blockers=(f"DAT parser is unavailable: {exc}",),
        )

    source_dat = os.path.abspath(source_dat_path)
    requested_names = tuple(str(name) for name in model_names if str(name).strip())
    install_dir = os.path.abspath(prefab_install_dir) if prefab_install_dir else ""
    hierarchy = str(hierarchy_kind or "direct_root").strip().lower().replace("-", "_")
    grouped_hierarchy = hierarchy in {"named_group", "group", "grouped"}
    hierarchy = "named_group" if grouped_hierarchy else "direct_root"
    label = str(group_name or _composite_group_label(requested_names, 0) or "Group")
    notes: List[str] = [
        "Stage 7P composite prefab reports generate one multi-brush prefab from explicitly selected DAT world models.",
        "Relative placement is preserved by copying all selected brush points in the same DAT coordinate frame.",
        "Generated Brush object Pos properties remain zero, matching the Stage 7M/7N prefab path that DEDit accepted.",
    ]
    if grouped_hierarchy:
        notes.append(
            "Stage 7R named-group reports wrap the generated Brush children in a Bench/Table-style null group node."
        )
    cautions = [
        "Generated composite prefabs are DAT-derived compiled polygons, not original authoring CSG brushes.",
        "This is a grouped-source acceptance experiment, not a full-world DAT rebuild backend.",
    ]
    if not requested_names:
        return PrefabSurrogateCompositeAcceptanceReport(
            status="no_models_selected",
            source_dat_path=source_dat,
            prefab_install_path=install_dir,
            hierarchy_kind=hierarchy,
            group_name=label if grouped_hierarchy else "",
            blockers=("composite prefab generation requires at least two explicit related model names",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )
    if len(requested_names) < 2:
        return PrefabSurrogateCompositeAcceptanceReport(
            status="needs_multiple_models",
            source_dat_path=source_dat,
            selected_model_names=requested_names,
            prefab_install_path=install_dir,
            hierarchy_kind=hierarchy,
            group_name=label if grouped_hierarchy else "",
            blockers=("composite prefab generation needs at least two selected DAT models",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )
    if not os.path.exists(source_dat):
        return PrefabSurrogateCompositeAcceptanceReport(
            status="source_dat_missing",
            source_dat_path=source_dat,
            selected_model_names=requested_names,
            prefab_install_path=install_dir,
            hierarchy_kind=hierarchy,
            group_name=label if grouped_hierarchy else "",
            blockers=(f"source DAT was not found: {source_dat}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    try:
        with open(source_dat, "rb") as f:
            parsed = bsp.parse(f.read())
    except Exception as exc:
        return PrefabSurrogateCompositeAcceptanceReport(
            status="dat_parse_failed",
            source_dat_path=source_dat,
            selected_model_names=requested_names,
            prefab_install_path=install_dir,
            hierarchy_kind=hierarchy,
            group_name=label if grouped_hierarchy else "",
            blockers=(f"DAT parse failed: {exc}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    requested_lookup = {name.lower(): order for order, name in enumerate(requested_names)}
    unmatched = set(requested_lookup)
    selected: List[object] = []
    skipped_notes: List[str] = []
    for model in getattr(parsed, "world_models", ()) or ():
        name = str(getattr(model, "name", "") or "")
        key = name.lower()
        if key not in requested_lookup:
            continue
        unmatched.discard(key)
        try:
            is_skybox = bool(getattr(model, "is_skybox", lambda: False)())
        except Exception:
            is_skybox = False
        if is_skybox and not include_skyboxes:
            skipped_notes.append(f"{name}: skybox/system model skipped")
            continue
        point_count = len(getattr(model, "points", []) or [])
        polygon_count = len(getattr(model, "polygons", []) or [])
        if point_count <= 0 or polygon_count <= 0:
            skipped_notes.append(f"{name}: no writable points or polygons")
            continue
        if point_count > int(max_model_points):
            skipped_notes.append(f"{name}: point count {point_count} exceeds per-model limit {int(max_model_points)}")
            continue
        if polygon_count > int(max_model_polygons):
            skipped_notes.append(f"{name}: polygon count {polygon_count} exceeds per-model limit {int(max_model_polygons)}")
            continue
        selected.append(model)

    selected.sort(key=lambda model: requested_lookup.get(str(getattr(model, "name", "") or "").lower(), 0))
    if len(selected) > max(0, int(max_models)):
        skipped_names = [
            str(getattr(model, "name", "") or "WorldModel")
            for model in selected[max(0, int(max_models)):]
        ]
        skipped_notes.append(
            f"max_models limit {int(max_models)} trimmed selected set: {', '.join(skipped_names)}"
        )
        selected = selected[:max(0, int(max_models))]

    model_summaries = tuple(
        _composite_model_summary(model)
        for model in selected
    )
    total_points = sum(item.point_count for item in model_summaries)
    total_polygons = sum(item.polygon_count for item in model_summaries)
    if unmatched:
        skipped_notes.extend(
            f"requested model was not found or was filtered out: {name}"
            for name in sorted(unmatched)
        )
    if len(selected) < 2:
        return PrefabSurrogateCompositeAcceptanceReport(
            status="needs_multiple_models",
            source_dat_path=source_dat,
            selected_model_names=tuple(str(getattr(model, "name", "") or "") for model in selected),
            prefab_install_path=install_dir,
            hierarchy_kind=hierarchy,
            group_name=label if grouped_hierarchy else "",
            model_count=len(selected),
            point_count=total_points,
            polygon_count=total_polygons,
            models=model_summaries,
            blockers=("fewer than two eligible DAT models remained after filtering",),
            cautions=tuple(cautions),
            notes=tuple(_unique_text(notes + skipped_notes)),
        )
    if total_points > int(max_total_points):
        return PrefabSurrogateCompositeAcceptanceReport(
            status="composite_too_large",
            source_dat_path=source_dat,
            selected_model_names=tuple(item.name for item in model_summaries),
            prefab_install_path=install_dir,
            hierarchy_kind=hierarchy,
            group_name=label if grouped_hierarchy else "",
            model_count=len(selected),
            point_count=total_points,
            polygon_count=total_polygons,
            models=model_summaries,
            blockers=(f"combined point count {total_points} exceeds composite limit {int(max_total_points)}",),
            cautions=tuple(cautions),
            notes=tuple(_unique_text(notes + skipped_notes)),
        )
    if total_polygons > int(max_total_polygons):
        return PrefabSurrogateCompositeAcceptanceReport(
            status="composite_too_large",
            source_dat_path=source_dat,
            selected_model_names=tuple(item.name for item in model_summaries),
            prefab_install_path=install_dir,
            hierarchy_kind=hierarchy,
            group_name=label if grouped_hierarchy else "",
            model_count=len(selected),
            point_count=total_points,
            polygon_count=total_polygons,
            models=model_summaries,
            blockers=(f"combined polygon count {total_polygons} exceeds composite limit {int(max_total_polygons)}",),
            cautions=tuple(cautions),
            notes=tuple(_unique_text(notes + skipped_notes)),
        )

    work_root = os.path.abspath(work_dir) if work_dir else tempfile.mkdtemp(prefix="mm9_stage7p_")
    source_stem = os.path.splitext(os.path.basename(source_dat))[0]
    prefix = _safe_filename_component(output_prefix or source_stem)
    default_suffix = "named_group_surrogate_prefab.ed" if grouped_hierarchy else "composite_surrogate_prefab.ed"
    filename = os.path.basename(output_filename) if output_filename else f"{prefix}_{default_suffix}"
    acceptance = build_prefab_surrogate_acceptance_report(
        source_dat_path=source_dat,
        model_names=tuple(item.name for item in model_summaries),
        max_models=len(model_summaries),
        include_skyboxes=include_skyboxes,
        work_dir=work_root,
        prefab_install_dir=install_dir,
        output_filename=filename,
        brush_name_prefix=brush_name_prefix,
        grouped_hierarchy=grouped_hierarchy,
        group_name=label,
    )
    install_path = acceptance.prefab_install_path
    generated_path = acceptance.generated_ed_path
    generated_classes = dict(acceptance.generated_object_class_counts)
    combined_cautions = list(cautions) + list(acceptance.cautions)
    if len(model_summaries) > 2:
        if grouped_hierarchy:
            combined_cautions.append(
                "Three-or-more-brush named-group prefabs use the real null/group-node pattern seen in Furniture/Bench.ed."
            )
        else:
            combined_cautions.append(
                "Three-or-more-brush composite prefabs use the real direct-root pattern seen in Sign1.ed; named null/group-node prefabs are available through the explicit grouped acceptance path."
            )
    if acceptance.status != "ready_for_manual_prefab_test":
        return PrefabSurrogateCompositeAcceptanceReport(
            status="composite_prefab_build_failed",
            source_dat_path=source_dat,
            generated_ed_path=generated_path,
            work_dir=work_root,
            prefab_install_path=install_path,
            hierarchy_kind=hierarchy,
            group_name=label if grouped_hierarchy else "",
            selected_model_names=tuple(item.name for item in model_summaries),
            model_count=len(model_summaries),
            point_count=total_points,
            polygon_count=total_polygons,
            object_count=acceptance.generated_object_count,
            generated_byte_count=acceptance.surrogate_byte_count,
            generated_object_class_counts=generated_classes,
            models=model_summaries,
            acceptance=acceptance,
            blockers=tuple(_unique_text(tuple(acceptance.blockers))),
            cautions=tuple(_unique_text(combined_cautions)),
            notes=tuple(_unique_text(notes + skipped_notes + list(acceptance.notes))),
        )
    if acceptance.generated_brush_count < 2 or acceptance.generated_object_count < 2:
        return PrefabSurrogateCompositeAcceptanceReport(
            status="composite_roundtrip_count_mismatch",
            source_dat_path=source_dat,
            generated_ed_path=generated_path,
            work_dir=work_root,
            prefab_install_path=install_path,
            hierarchy_kind=hierarchy,
            group_name=label if grouped_hierarchy else "",
            selected_model_names=tuple(item.name for item in model_summaries),
            model_count=len(model_summaries),
            point_count=total_points,
            polygon_count=total_polygons,
            object_count=acceptance.generated_object_count,
            generated_byte_count=acceptance.surrogate_byte_count,
            generated_object_class_counts=generated_classes,
            models=model_summaries,
            acceptance=acceptance,
            blockers=(
                "generated composite did not round-trip as at least two brush/object records",
            ),
            cautions=tuple(_unique_text(combined_cautions)),
            notes=tuple(_unique_text(notes + skipped_notes + list(acceptance.notes))),
        )

    target_text = install_path or "the real MM9 project data\\PreFabs directory"
    manual_steps = (
        f"Copy {generated_path} to {target_text}.",
        "Open old LithTech 2.1 DEDit through the MM9.dep project and open a known-good world.",
        "Instantiate the generated named-group prefab once and confirm the whole group appears with relative offsets intact."
        if grouped_hierarchy
        else "Instantiate the generated composite prefab once and confirm all brushes appear together with their relative offsets intact.",
        "Move or duplicate the composite inside DEDit only after the first insert looks coherent.",
        "Compile with Processor.exe, then fresh-load the DAT in game and check rendering plus collision for every brush in the group.",
    )
    return PrefabSurrogateCompositeAcceptanceReport(
        status="ready_for_manual_named_group_prefab_test"
        if grouped_hierarchy
        else "ready_for_manual_composite_prefab_test",
        source_dat_path=source_dat,
        generated_ed_path=generated_path,
        work_dir=work_root,
        prefab_install_path=install_path,
        hierarchy_kind=hierarchy,
        group_name=label if grouped_hierarchy else "",
        selected_model_names=tuple(item.name for item in model_summaries),
        model_count=len(model_summaries),
        point_count=total_points,
        polygon_count=total_polygons,
        object_count=acceptance.generated_object_count,
        generated_byte_count=acceptance.surrogate_byte_count,
        generated_object_class_counts=generated_classes,
        models=model_summaries,
        acceptance=acceptance,
        manual_steps=manual_steps,
        cautions=tuple(_unique_text(combined_cautions)),
        notes=tuple(_unique_text(notes + skipped_notes + list(acceptance.notes))),
    )


def build_prefab_surrogate_named_group_acceptance_report(
    *,
    source_dat_path: str,
    model_names: Sequence[str],
    group_name: str = "Group",
    max_models: int = 8,
    max_total_points: int = 2048,
    max_total_polygons: int = 2048,
    max_model_points: int = 1024,
    max_model_polygons: int = 1024,
    include_skyboxes: bool = False,
    work_dir: Optional[str] = None,
    prefab_install_dir: Optional[str] = None,
    output_filename: str = "",
    output_prefix: str = "",
    brush_name_prefix: str = "Brush",
) -> PrefabSurrogateCompositeAcceptanceReport:
    """Generate one named null/group-node multi-brush prefab surrogate."""
    return build_prefab_surrogate_composite_acceptance_report(
        source_dat_path=source_dat_path,
        model_names=model_names,
        max_models=max_models,
        max_total_points=max_total_points,
        max_total_polygons=max_total_polygons,
        max_model_points=max_model_points,
        max_model_polygons=max_model_polygons,
        include_skyboxes=include_skyboxes,
        work_dir=work_dir,
        prefab_install_dir=prefab_install_dir,
        output_filename=output_filename,
        output_prefix=output_prefix,
        brush_name_prefix=brush_name_prefix,
        hierarchy_kind="named_group",
        group_name=group_name,
    )


def build_prefab_surrogate_composite_acceptance_corpus_report(
    *,
    source_dat_path: str,
    model_groups: Sequence[Sequence[str]] = (),
    group_names: Sequence[str] = (),
    max_groups: int = 8,
    min_models_per_group: int = 2,
    max_models_per_group: int = 8,
    max_total_points: int = 2048,
    max_total_polygons: int = 2048,
    max_model_points: int = 1024,
    max_model_polygons: int = 1024,
    include_skyboxes: bool = False,
    work_dir: Optional[str] = None,
    prefab_install_dir: Optional[str] = None,
    output_prefix: str = "",
    brush_name_prefix: str = "Brush",
    hierarchy_kind: str = "direct_root",
) -> PrefabSurrogateCompositeCorpusReport:
    """Generate a corpus of composite prefab surrogates.

    Explicit ``model_groups`` are preferred because "related" world models are
    a source-world judgment.  When no groups are supplied, the fallback only
    groups small non-system models whose names share an obvious base prefix,
    such as ``MonsterDoor1``/``MonsterDoor2``.
    """
    hierarchy = str(hierarchy_kind or "direct_root").strip().lower().replace("-", "_")
    grouped_hierarchy = hierarchy in {"named_group", "group", "grouped"}
    hierarchy = "named_group" if grouped_hierarchy else "direct_root"
    try:
        from core import bsp
    except Exception as exc:
        return PrefabSurrogateCompositeCorpusReport(
            status="dat_parser_unavailable",
            source_dat_path=os.path.abspath(source_dat_path),
            hierarchy_kind=hierarchy,
            blockers=(f"DAT parser is unavailable: {exc}",),
        )

    source_dat = os.path.abspath(source_dat_path)
    install_dir = os.path.abspath(prefab_install_dir) if prefab_install_dir else ""
    ready_status = (
        "ready_for_manual_named_group_prefab_test"
        if grouped_hierarchy
        else "ready_for_manual_composite_prefab_test"
    )
    ready_corpus_status = (
        "ready_for_manual_named_group_prefab_corpus_test"
        if grouped_hierarchy
        else "ready_for_manual_composite_prefab_corpus_test"
    )
    completed_with_failures_status = (
        "completed_with_named_group_failures"
        if grouped_hierarchy
        else "completed_with_composite_failures"
    )
    failed_status = (
        "named_group_prefab_corpus_build_failed"
        if grouped_hierarchy
        else "composite_prefab_corpus_build_failed"
    )
    hierarchy_text = "named-group" if grouped_hierarchy else "direct-root"
    notes: List[str] = [
        (
            "Stage 7S named-group corpus reports generate multiple Bench/Table-style grouped prefab surrogates for manual DEDit acceptance testing."
            if grouped_hierarchy
            else "Stage 7Q composite corpus reports generate multiple direct-root brush-only prefab surrogates for manual DEDit acceptance testing."
        ),
    ]
    if grouped_hierarchy:
        notes.append("Each generated file uses a root child named group that owns the selected generated Brush children.")
    else:
        notes.append("Named null/group-node prefab hierarchy is available through the explicit Stage 7S named-group corpus mode.")
    cautions = [
        "Generated composite prefabs are DAT-derived compiled polygons, not original authoring CSG brushes.",
        "Test corpus entries one at a time in old DEDit before compiling a source world.",
    ]
    if not os.path.exists(source_dat):
        return PrefabSurrogateCompositeCorpusReport(
            status="source_dat_missing",
            source_dat_path=source_dat,
            prefab_install_dir=install_dir,
            hierarchy_kind=hierarchy,
            blockers=(f"source DAT was not found: {source_dat}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    try:
        with open(source_dat, "rb") as f:
            parsed = bsp.parse(f.read())
    except Exception as exc:
        return PrefabSurrogateCompositeCorpusReport(
            status="dat_parse_failed",
            source_dat_path=source_dat,
            prefab_install_dir=install_dir,
            hierarchy_kind=hierarchy,
            blockers=(f"DAT parse failed: {exc}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    world_model_names = {
        str(getattr(model, "name", "") or "").lower()
        for model in getattr(parsed, "world_models", ()) or ()
    }
    supplied_groups = [
        tuple(str(name) for name in group if str(name).strip())
        for group in model_groups
    ]
    groups = [group for group in supplied_groups if len(group) >= int(min_models_per_group)]
    if not groups:
        groups = _auto_direct_root_composite_groups(
            getattr(parsed, "world_models", ()) or (),
            min_models=int(min_models_per_group),
            max_models=int(max_models_per_group),
            max_model_points=int(max_model_points),
            max_model_polygons=int(max_model_polygons),
            include_skyboxes=include_skyboxes,
        )
        if groups:
            notes.append("No explicit groups were supplied; generated conservative name-family groups automatically.")
    if not groups:
        return PrefabSurrogateCompositeCorpusReport(
            status="no_groups_selected",
            source_dat_path=source_dat,
            prefab_install_dir=install_dir,
            hierarchy_kind=hierarchy,
            blockers=("no composite groups were supplied or discovered",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    temp_prefix = "mm9_stage7s_" if grouped_hierarchy else "mm9_stage7q_"
    work_root = os.path.abspath(work_dir) if work_dir else tempfile.mkdtemp(prefix=temp_prefix)
    source_stem = os.path.splitext(os.path.basename(source_dat))[0]
    prefix = _safe_filename_component(output_prefix or source_stem)
    label_overrides = tuple(str(name) for name in group_names)
    candidates: List[PrefabSurrogateCompositeCorpusCandidate] = []
    generated_count = 0
    skipped_count = 0
    for index, group in enumerate(groups):
        group = tuple(group[:max(0, int(max_models_per_group))])
        label = (
            _safe_filename_component(label_overrides[index])
            if index < len(label_overrides) and label_overrides[index].strip()
            else _composite_group_label(group, index)
        )
        missing = [name for name in group if name.lower() not in world_model_names]
        if len(group) < int(min_models_per_group):
            skipped_count += 1
            candidates.append(PrefabSurrogateCompositeCorpusCandidate(
                group_name=label,
                hierarchy_kind=hierarchy,
                model_names=group,
                status="skipped_too_few_models",
                notes=(f"group has fewer than {int(min_models_per_group)} model(s)",),
            ))
            continue
        if missing:
            skipped_count += 1
            candidates.append(PrefabSurrogateCompositeCorpusCandidate(
                group_name=label,
                hierarchy_kind=hierarchy,
                model_names=group,
                status="skipped_missing_models",
                notes=(f"missing model(s): {', '.join(missing)}",),
            ))
            continue
        if generated_count >= max(0, int(max_groups)):
            skipped_count += 1
            candidates.append(PrefabSurrogateCompositeCorpusCandidate(
                group_name=label,
                hierarchy_kind=hierarchy,
                model_names=group,
                status="skipped_corpus_limit",
                notes=(f"max_groups limit {int(max_groups)} was reached",),
            ))
            continue

        suffix = "named_group_surrogate_prefab.ed" if grouped_hierarchy else "directroot_composite_surrogate_prefab.ed"
        output_filename = f"{prefix}_{label}_{suffix}"
        acceptance = build_prefab_surrogate_composite_acceptance_report(
            source_dat_path=source_dat,
            model_names=group,
            max_models=len(group),
            max_total_points=max_total_points,
            max_total_polygons=max_total_polygons,
            max_model_points=max_model_points,
            max_model_polygons=max_model_polygons,
            include_skyboxes=include_skyboxes,
            work_dir=os.path.join(work_root, label),
            prefab_install_dir=install_dir,
            output_filename=output_filename,
            brush_name_prefix=brush_name_prefix,
            hierarchy_kind=hierarchy,
            group_name=label,
        )
        generated_count += 1
        candidates.append(PrefabSurrogateCompositeCorpusCandidate(
            group_name=label,
            hierarchy_kind=hierarchy,
            model_names=tuple(acceptance.selected_model_names or group),
            status=acceptance.status,
            model_count=acceptance.model_count,
            point_count=acceptance.point_count,
            polygon_count=acceptance.polygon_count,
            generated_ed_path=acceptance.generated_ed_path,
            prefab_install_path=acceptance.prefab_install_path,
            acceptance=acceptance,
            notes=tuple(acceptance.blockers or acceptance.cautions[:2]),
        ))

    ready_count = sum(1 for item in candidates if item.status == ready_status)
    failed_count = sum(
        1
        for item in candidates
        if item.generated_ed_path and item.status != ready_status
    )
    skipped_count = sum(1 for item in candidates if item.status.startswith("skipped_"))
    if ready_count and failed_count == 0:
        status = ready_corpus_status
    elif ready_count:
        status = completed_with_failures_status
    elif skipped_count == len(candidates):
        status = "no_eligible_groups"
    else:
        status = failed_status

    if install_dir:
        manual_steps = (
            f"Copy the ready {hierarchy_text} composite `.ed` files from {work_root} into {install_dir}.",
            "Open old LithTech 2.1 DEDit through the MM9.dep project in the same game data directory.",
            "Test one generated composite prefab at a time, starting with the smallest model count.",
            "For named-group candidates, confirm DEDit shows each inserted prefab as one group with the requested group name."
            if grouped_hierarchy
            else "Confirm all direct-root brushes appear together with their relative offsets intact.",
            "After DEDit insertion succeeds, compile the source world with Processor.exe and fresh-load the DAT in game.",
        )
    else:
        manual_steps = (
            f"Copy the ready {hierarchy_text} composite `.ed` files from {work_root} into the real MM9 project data\\PreFabs directory.",
            "Open old LithTech 2.1 DEDit through that project's MM9.dep file.",
            "Test one generated composite prefab at a time, starting with the smallest model count.",
            "For named-group candidates, confirm DEDit shows each inserted prefab as one group with the requested group name."
            if grouped_hierarchy
            else "Confirm all direct-root brushes appear together with their relative offsets intact.",
            "After DEDit insertion succeeds, compile the source world with Processor.exe and fresh-load the DAT in game.",
        )
    notes.append(f"ready {hierarchy_text} composite prefab file count: {ready_count}")
    return PrefabSurrogateCompositeCorpusReport(
        status=status,
        source_dat_path=source_dat,
        work_dir=work_root,
        prefab_install_dir=install_dir,
        hierarchy_kind=hierarchy,
        group_count=len(candidates),
        generated_count=generated_count,
        ready_count=ready_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        candidates=tuple(candidates),
        manual_steps=manual_steps,
        cautions=tuple(cautions),
        notes=tuple(_unique_text(notes)),
    )


def build_prefab_surrogate_named_group_corpus_report(
    *,
    source_dat_path: str,
    model_groups: Sequence[Sequence[str]] = (),
    group_names: Sequence[str] = (),
    max_groups: int = 8,
    min_models_per_group: int = 2,
    max_models_per_group: int = 8,
    max_total_points: int = 2048,
    max_total_polygons: int = 2048,
    max_model_points: int = 1024,
    max_model_polygons: int = 1024,
    include_skyboxes: bool = False,
    work_dir: Optional[str] = None,
    prefab_install_dir: Optional[str] = None,
    output_prefix: str = "",
    brush_name_prefix: str = "Brush",
) -> PrefabSurrogateCompositeCorpusReport:
    """Generate a corpus of named null/group-node composite prefab surrogates."""
    return build_prefab_surrogate_composite_acceptance_corpus_report(
        source_dat_path=source_dat_path,
        model_groups=model_groups,
        group_names=group_names,
        max_groups=max_groups,
        min_models_per_group=min_models_per_group,
        max_models_per_group=max_models_per_group,
        max_total_points=max_total_points,
        max_total_polygons=max_total_polygons,
        max_model_points=max_model_points,
        max_model_polygons=max_model_polygons,
        include_skyboxes=include_skyboxes,
        work_dir=work_dir,
        prefab_install_dir=prefab_install_dir,
        output_prefix=output_prefix,
        brush_name_prefix=brush_name_prefix,
        hierarchy_kind="named_group",
    )


def build_prefab_surrogate_named_group_pack_report(
    *,
    source_dat_path: str,
    model_groups: Sequence[Sequence[str]] = (),
    group_names: Sequence[str] = (),
    max_groups: int = 8,
    min_models_per_group: int = 2,
    max_models_per_group: int = 8,
    max_total_points: int = 2048,
    max_total_polygons: int = 2048,
    max_model_points: int = 1024,
    max_model_polygons: int = 1024,
    include_skyboxes: bool = False,
    work_dir: Optional[str] = None,
    staging_prefab_dir: Optional[str] = None,
    output_prefix: str = "",
    brush_name_prefix: str = "Brush",
    manifest_filename: str = "prefab_pack_manifest.json",
) -> PrefabSurrogatePackReport:
    """Generate a staged named-group prefab pack plus a JSON manifest."""
    source_dat = os.path.abspath(source_dat_path)
    work_root = os.path.abspath(work_dir) if work_dir else tempfile.mkdtemp(prefix="mm9_stage7t_")
    staging_dir = (
        os.path.abspath(staging_prefab_dir)
        if staging_prefab_dir
        else os.path.join(work_root, "PreFabs")
    )
    manifest_name = os.path.basename(manifest_filename or "prefab_pack_manifest.json")
    manifest_path = os.path.join(work_root, manifest_name)
    notes: List[str] = [
        "Stage 7T named-group prefab packs stage generated source-compatible prefab files and a manifest for manual DEDit insertion.",
        "The pack does not write into the real MM9 PreFabs directory automatically.",
    ]
    cautions: List[str] = [
        "Generated prefabs are DAT-derived compiled polygons, not original authoring CSG brushes.",
        "Manual DEDit and Processor.exe validation is still required before treating a pack as usable source content.",
    ]

    corpus = build_prefab_surrogate_named_group_corpus_report(
        source_dat_path=source_dat,
        model_groups=model_groups,
        group_names=group_names,
        max_groups=max_groups,
        min_models_per_group=min_models_per_group,
        max_models_per_group=max_models_per_group,
        max_total_points=max_total_points,
        max_total_polygons=max_total_polygons,
        max_model_points=max_model_points,
        max_model_polygons=max_model_polygons,
        include_skyboxes=include_skyboxes,
        work_dir=os.path.join(work_root, "corpus"),
        prefab_install_dir=staging_dir,
        output_prefix=output_prefix,
        brush_name_prefix=brush_name_prefix,
    )

    ready_status = "ready_for_manual_named_group_prefab_test"
    entries: List[PrefabSurrogatePackEntry] = []
    blockers: List[str] = list(corpus.blockers)
    os.makedirs(staging_dir, exist_ok=True)
    for candidate in corpus.candidates:
        staged_path = ""
        status = candidate.status
        entry_notes = list(candidate.notes)
        filename = os.path.basename(candidate.generated_ed_path) if candidate.generated_ed_path else ""
        if candidate.status == ready_status and candidate.generated_ed_path:
            staged_path = os.path.join(staging_dir, filename)
            try:
                shutil.copyfile(candidate.generated_ed_path, staged_path)
                status = "ready_staged_named_group_prefab"
            except OSError as exc:
                status = "stage_copy_failed"
                entry_notes.append(f"stage copy failed: {exc}")
                blockers.append(f"{candidate.group_name}: stage copy failed: {exc}")
        entries.append(PrefabSurrogatePackEntry(
            group_name=candidate.group_name,
            hierarchy_kind=candidate.hierarchy_kind,
            status=status,
            model_names=candidate.model_names,
            model_count=candidate.model_count,
            point_count=candidate.point_count,
            polygon_count=candidate.polygon_count,
            generated_ed_path=candidate.generated_ed_path,
            staged_prefab_path=staged_path,
            filename=filename,
            notes=tuple(entry_notes),
        ))

    ready_count = sum(1 for item in corpus.candidates if item.status == ready_status)
    staged_count = sum(1 for item in entries if item.status == "ready_staged_named_group_prefab")
    failed_count = sum(1 for item in entries if item.status not in {"ready_staged_named_group_prefab"} and not item.status.startswith("skipped_"))
    skipped_count = sum(1 for item in entries if item.status.startswith("skipped_"))
    if staged_count and staged_count == ready_count and failed_count == 0:
        status = "ready_for_manual_named_group_pack_test"
    elif staged_count:
        status = "completed_with_pack_failures"
    elif skipped_count == len(entries) and entries:
        status = "no_eligible_pack_entries"
    else:
        status = "named_group_pack_build_failed"

    manual_steps = (
        f"Copy the staged `.ed` files from {staging_dir} into the real MM9 project data\\PreFabs directory.",
        "Open old LithTech 2.1 DEDit through the MM9.dep project in the same game data directory.",
        "Insert one staged prefab at a time and confirm DEDit shows it as one group with the manifest group name.",
        "Compile the source world with Processor.exe, then fresh-load the DAT in game and check rendering plus collision.",
        f"Keep {manifest_path} beside test notes so source DAT models, group labels, and staged filenames stay traceable.",
    )
    manifest = _prefab_surrogate_pack_manifest(
        source_dat_path=source_dat,
        work_dir=work_root,
        staging_prefab_dir=staging_dir,
        report_status=status,
        entries=entries,
        manual_steps=manual_steps,
        cautions=tuple(_unique_text(cautions + list(corpus.cautions))),
        notes=tuple(_unique_text(notes + list(corpus.notes))),
    )
    try:
        os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
            f.write("\n")
    except OSError as exc:
        blockers.append(f"manifest write failed: {exc}")
        status = "pack_manifest_write_failed" if not staged_count else "completed_with_pack_failures"

    return PrefabSurrogatePackReport(
        status=status,
        source_dat_path=source_dat,
        work_dir=work_root,
        staging_prefab_dir=staging_dir,
        manifest_path=manifest_path,
        hierarchy_kind="named_group",
        entry_count=len(entries),
        ready_count=ready_count,
        staged_count=staged_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        corpus=corpus,
        entries=tuple(entries),
        manual_steps=manual_steps,
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions + list(corpus.cautions))),
        notes=tuple(_unique_text(notes + list(corpus.notes))),
    )


def build_full_world_skeleton_acceptance_report(
    *,
    source_dat_path: str,
    model_names: Sequence[str],
    group_name: str = "GeneratedWorldModels",
    max_models: int = 32,
    max_total_points: int = 4096,
    max_total_polygons: int = 4096,
    max_model_points: int = 2048,
    max_model_polygons: int = 2048,
    include_skyboxes: bool = False,
    work_dir: Optional[str] = None,
    worlds_install_dir: Optional[str] = None,
    output_filename: str = "",
    output_prefix: str = "",
    brush_name_prefix: str = "Brush",
    include_validation_floor: bool = False,
    validation_floor_name: str = "ValidationFloor",
    validation_floor_margin: float = 512.0,
    validation_floor_thickness: float = 32.0,
    validation_floor_texture: str = "TEXTURES\\LevelTextures\\Terrain\\MainGrass.dtx",
    include_terrain_support_patch: bool = False,
    terrain_support_model_name: str = "Terrain0",
    terrain_support_name_prefix: str = "TerrainSupportPatch",
    terrain_support_margin: float = 0.0,
    terrain_support_selection_mode: str = "bounds",
    terrain_support_radius: float = 0.0,
    terrain_support_brush_mode: str = "single_polygon",
    terrain_support_thickness: float = 96.0,
    terrain_support_max_polygons: int = 128,
    terrain_support_side_texture: str = "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx",
    include_physics_shell_patch: bool = False,
    physics_shell_model_name: str = "PhysicsBSP",
    physics_shell_name_prefix: str = "PhysicsShell",
    physics_shell_max_polygons: int = 128,
    physics_shell_thickness: float = 16.0,
    physics_shell_side_texture: str = "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx",
    include_terrain_cutout_coverage: Optional[bool] = None,
    terrain_cutout_ignored_textures: Sequence[str] = ("TEXTURES\\LevelTextures\\Terrain\\sand.dtx",),
    terrain_cutout_sample_grid: int = 7,
    terrain_cutout_cluster_gap: float = 64.0,
    terrain_cutout_min_cluster_footprint_area: float = 4096.0,
    terrain_cutout_max_candidates: int = 64,
    include_terrain_support_source_coverage: bool = False,
    terrain_support_source_coverage_ignored_textures: Sequence[str] = ("TEXTURES\\LevelTextures\\Terrain\\sand.dtx",),
    terrain_support_source_coverage_sample_grid: int = 3,
    terrain_support_source_coverage_max_gaps: int = 64,
    max_processor_brushes: int = 0,
    max_processor_polygons: int = 0,
    block_unreconstructed_physics_shell: bool = False,
    min_unreconstructed_physics_shell_polygons: int = 512,
) -> FullWorldSkeletonAcceptanceReport:
    """Generate a selected-model full-world skeleton ED for manual compiler tests.

    Unlike prefab reports, this writes a standalone world source candidate with
    root/group/Brush nodes plus minimal load scaffolding.  The report is still
    a validation harness, not a DAT save backend: DEDit, Processor.exe, and a
    fresh game load remain the acceptance gate.
    """
    from features.dat_editing import legacy_ed, surrogate_ed

    try:
        from core import bsp
    except Exception as exc:
        return FullWorldSkeletonAcceptanceReport(
            status="dat_parser_unavailable",
            source_dat_path=os.path.abspath(source_dat_path),
            group_name=group_name,
            blockers=(f"DAT parser is unavailable: {exc}",),
        )

    source_dat = os.path.abspath(source_dat_path)
    requested_names = tuple(str(name).strip() for name in model_names if str(name).strip())
    install_path = ""
    cutout_coverage_enabled = (
        bool(include_terrain_support_patch)
        if include_terrain_cutout_coverage is None
        else bool(include_terrain_cutout_coverage)
    )
    terrain_cutout_report: Optional[TerrainCutoutCoverageReport] = None
    terrain_cutout_manifest_path = ""
    terrain_support_source_report: Optional[TerrainSupportSourceCoverageReport] = None
    terrain_support_source_manifest_path = ""
    notes: List[str] = [
        "Full-world skeleton reports generate standalone ED worlds from explicitly selected DAT world models.",
        "The generated world uses a root Container, one generated model group, Brush children, and minimal load objects.",
    ]
    if include_validation_floor:
        notes.append(
            "A synthetic validation floor is included so isolated object clusters can be walked in game."
        )
    if include_terrain_support_patch:
        notes.append(
            f"A closed terrain support patch is generated from local {terrain_support_model_name} polygons."
        )
        if str(terrain_support_selection_mode or "bounds").lower().replace("-", "_") != "bounds":
            notes.append(
                f"Terrain support selection mode: {terrain_support_selection_mode}, radius={terrain_support_radius:g}."
            )
        if str(terrain_support_brush_mode or "single_polygon").lower().replace("-", "_") != "single_polygon":
            notes.append(f"Terrain support brush mode: {terrain_support_brush_mode}.")
    if include_physics_shell_patch:
        notes.append(
            f"A budgeted PhysicsBSP shell patch is generated from {physics_shell_model_name} collision polygons."
        )
        notes.append(
            f"PhysicsBSP shell polygon budget: {max(1, int(physics_shell_max_polygons))}; thickness={float(physics_shell_thickness):g}."
        )
    if cutout_coverage_enabled:
        notes.append(
            "Terrain cutout coverage will be sampled against original non-terrain model footprints."
        )
    if include_terrain_support_source_coverage:
        notes.append(
            "Terrain support source coverage will compare generated ED terrain tops against original playable Terrain0 polygons."
        )
    processor_brush_budget = max(0, int(max_processor_brushes))
    processor_polygon_budget = max(0, int(max_processor_polygons))
    if processor_brush_budget or processor_polygon_budget:
        detail = []
        if processor_brush_budget:
            detail.append(f"brushes<={processor_brush_budget}")
        if processor_polygon_budget:
            detail.append(f"polygons<={processor_polygon_budget}")
        notes.append(
            "LithTech 2.1 Processor budget guard is enabled: " + ", ".join(detail) + "."
        )
    cautions: List[str] = [
        "Generated full-world skeletons are DAT-derived compiled polygons, not original authoring CSG brushes.",
        "Original gameplay objects, portals, visibility hints, and source-world organization are not reconstructed.",
        "Manual old DEDit, Processor.exe, and in-game validation are still required.",
    ]
    if include_validation_floor:
        cautions.append(
            "The validation floor is test scaffolding and should not be treated as reconstructed DAT source geometry."
        )
    if include_terrain_support_patch:
        cautions.append(
            "Terrain support patches are closed source-like test brushes derived from compiled Terrain* polygons, not recovered original terrain CSG."
        )
    if include_physics_shell_patch:
        cautions.append(
            "PhysicsBSP shell patches are closed source-like test brushes derived from compiled collision polygons, not recovered original authoring CSG."
        )
    if worlds_install_dir:
        install_path = os.path.join(
            os.path.abspath(worlds_install_dir),
            os.path.basename(output_filename) if output_filename else "",
        )
    if not requested_names:
        return FullWorldSkeletonAcceptanceReport(
            status="no_models_selected",
            source_dat_path=source_dat,
            world_install_path=install_path,
            group_name=group_name,
            include_validation_floor=include_validation_floor,
            include_terrain_support_patch=include_terrain_support_patch,
            include_physics_shell_patch=include_physics_shell_patch,
            blockers=("full-world skeleton acceptance generation requires explicit model names",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )
    if not os.path.exists(source_dat):
        return FullWorldSkeletonAcceptanceReport(
            status="source_dat_missing",
            source_dat_path=source_dat,
            world_install_path=install_path,
            group_name=group_name,
            include_validation_floor=include_validation_floor,
            include_terrain_support_patch=include_terrain_support_patch,
            include_physics_shell_patch=include_physics_shell_patch,
            selected_model_names=requested_names,
            blockers=(f"source DAT was not found: {source_dat}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    try:
        with open(source_dat, "rb") as f:
            parsed = bsp.parse(f.read())
    except Exception as exc:
        return FullWorldSkeletonAcceptanceReport(
            status="dat_parse_failed",
            source_dat_path=source_dat,
            world_install_path=install_path,
            group_name=group_name,
            include_validation_floor=include_validation_floor,
            include_terrain_support_patch=include_terrain_support_patch,
            include_physics_shell_patch=include_physics_shell_patch,
            selected_model_names=requested_names,
            blockers=(f"DAT parse failed: {exc}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    unreconstructed_physics_shell = next(
        (
            model for model in getattr(parsed, "world_models", ()) or ()
            if terrain_semantics.is_physics_bsp_model(model)
            and len(getattr(model, "polygons", ()) or ()) >= max(0, int(min_unreconstructed_physics_shell_polygons))
        ),
        None,
    )
    should_block_unreconstructed_physics_shell = (
        bool(block_unreconstructed_physics_shell)
        and not bool(include_terrain_support_patch)
        and not bool(include_physics_shell_patch)
        and unreconstructed_physics_shell is not None
    )
    if should_block_unreconstructed_physics_shell:
        notes.append(
            "A large PhysicsBSP static shell is present but is not reconstructed into source brushes yet."
        )

    requested_lookup = {name.lower(): order for order, name in enumerate(requested_names)}
    unmatched = set(requested_lookup)
    selected: List[object] = []
    skipped_notes: List[str] = []
    for model in getattr(parsed, "world_models", ()) or ():
        name = str(getattr(model, "name", "") or "")
        key = name.lower()
        if key not in requested_lookup:
            continue
        unmatched.discard(key)
        try:
            is_skybox = bool(getattr(model, "is_skybox", lambda: False)())
        except Exception:
            is_skybox = False
        if is_skybox and not include_skyboxes:
            skipped_notes.append(f"{name}: skybox/system model skipped")
            continue
        point_count = len(getattr(model, "points", []) or [])
        polygon_count = len(getattr(model, "polygons", []) or [])
        if point_count <= 0 or polygon_count <= 0:
            skipped_notes.append(f"{name}: no writable points or polygons")
            continue
        if point_count > int(max_model_points):
            skipped_notes.append(f"{name}: point count {point_count} exceeds per-model limit {int(max_model_points)}")
            continue
        if polygon_count > int(max_model_polygons):
            skipped_notes.append(
                f"{name}: polygon count {polygon_count} exceeds per-model limit {int(max_model_polygons)}"
            )
            continue
        selected.append(model)

    if len(selected) > max(0, int(max_models)):
        skipped = [
            str(getattr(model, "name", "") or "WorldModel")
            for model in selected[max(0, int(max_models)):]
        ]
        skipped_notes.append(f"max_models limit {int(max_models)} trimmed selected set: {', '.join(skipped)}")
        selected = selected[:max(0, int(max_models))]
    if unmatched:
        skipped_notes.extend(
            f"requested model was not found or was filtered out: {name}"
            for name in sorted(unmatched)
        )

    model_summaries = tuple(_composite_model_summary(model) for model in selected)
    selected_names = tuple(item.name for item in model_summaries)
    total_points = sum(item.point_count for item in model_summaries)
    total_polygons = sum(item.polygon_count for item in model_summaries)
    expected_points = total_points + (8 if include_validation_floor else 0)
    expected_polygons = total_polygons + (6 if include_validation_floor else 0)
    effective_physics_shell_max_polygons = max(0, int(physics_shell_max_polygons))
    if include_physics_shell_patch:
        if processor_brush_budget:
            remaining_brushes = max(
                0,
                processor_brush_budget - len(model_summaries) - (1 if include_validation_floor else 0),
            )
            if remaining_brushes < effective_physics_shell_max_polygons:
                notes.append(
                    f"PhysicsBSP shell source polygon budget capped by Processor brush budget: "
                    f"{effective_physics_shell_max_polygons} -> {remaining_brushes}."
                )
                effective_physics_shell_max_polygons = remaining_brushes
        if processor_polygon_budget:
            remaining_generated_polygons = max(0, processor_polygon_budget - expected_polygons)
            fitted_shell_polygons = _budgeted_physics_shell_source_polygon_count(
                parsed,
                physics_shell_model_name,
                requested_source_polygon_count=effective_physics_shell_max_polygons,
                generated_polygon_budget=remaining_generated_polygons,
            )
            if fitted_shell_polygons < effective_physics_shell_max_polygons:
                notes.append(
                    f"PhysicsBSP shell source polygon budget capped by predicted generated face count: "
                    f"{effective_physics_shell_max_polygons} -> {fitted_shell_polygons}."
                )
                effective_physics_shell_max_polygons = fitted_shell_polygons
    if any(terrain_semantics.is_terrain_name(name) for name in selected_names):
        cautions.append(
            "Terrain* models are emitted as DAT-derived compiled polygon brushes; this tests Processor tolerance and is not original terrain source reconstruction."
        )
    if not selected:
        return FullWorldSkeletonAcceptanceReport(
            status="no_eligible_models",
            source_dat_path=source_dat,
            world_install_path=install_path,
            group_name=group_name,
            include_validation_floor=include_validation_floor,
            include_terrain_support_patch=include_terrain_support_patch,
            include_physics_shell_patch=include_physics_shell_patch,
            selected_model_names=selected_names,
            blockers=("no requested DAT world models remained after filtering",),
            cautions=tuple(cautions),
            notes=tuple(_unique_text(notes + skipped_notes)),
        )
    if expected_points > int(max_total_points):
        return FullWorldSkeletonAcceptanceReport(
            status="full_world_skeleton_too_large",
            source_dat_path=source_dat,
            world_install_path=install_path,
            group_name=group_name,
            include_validation_floor=include_validation_floor,
            include_terrain_support_patch=include_terrain_support_patch,
            include_physics_shell_patch=include_physics_shell_patch,
            selected_model_names=selected_names,
            model_count=len(model_summaries),
            point_count=expected_points,
            polygon_count=expected_polygons,
            models=model_summaries,
            blockers=(f"combined point count {expected_points} exceeds skeleton limit {int(max_total_points)}",),
            cautions=tuple(cautions),
            notes=tuple(_unique_text(notes + skipped_notes)),
        )
    if expected_polygons > int(max_total_polygons):
        return FullWorldSkeletonAcceptanceReport(
            status="full_world_skeleton_too_large",
            source_dat_path=source_dat,
            world_install_path=install_path,
            group_name=group_name,
            include_validation_floor=include_validation_floor,
            include_terrain_support_patch=include_terrain_support_patch,
            include_physics_shell_patch=include_physics_shell_patch,
            selected_model_names=selected_names,
            model_count=len(model_summaries),
            point_count=expected_points,
            polygon_count=expected_polygons,
            models=model_summaries,
            blockers=(f"combined polygon count {expected_polygons} exceeds skeleton limit {int(max_total_polygons)}",),
            cautions=tuple(cautions),
            notes=tuple(_unique_text(notes + skipped_notes)),
        )

    work_root = os.path.abspath(work_dir) if work_dir else tempfile.mkdtemp(prefix="mm9_stage7u_")
    source_dir = os.path.join(work_root, "full_world_skeleton_source")
    os.makedirs(source_dir, exist_ok=True)
    source_stem = os.path.splitext(os.path.basename(source_dat))[0]
    prefix = _safe_filename_component(output_prefix or source_stem)
    label = _safe_filename_component(group_name or "GeneratedWorldModels")
    filename = os.path.basename(output_filename) if output_filename else f"{prefix}_{label}_full_world_skeleton.ed"
    generated_ed = os.path.join(source_dir, filename)
    if worlds_install_dir:
        install_path = os.path.join(os.path.abspath(worlds_install_dir), filename)
    if cutout_coverage_enabled:
        terrain_cutout_report = build_terrain_cutout_coverage_report(
            source_dat_path=source_dat,
            terrain_model_name=terrain_support_model_name or "Terrain0",
            ignored_terrain_textures=terrain_cutout_ignored_textures,
            sample_grid=terrain_cutout_sample_grid,
            cluster_gap=terrain_cutout_cluster_gap,
            min_cluster_footprint_area=terrain_cutout_min_cluster_footprint_area,
            max_candidates=terrain_cutout_max_candidates,
            include_skyboxes=include_skyboxes,
        )
        terrain_cutout_manifest_path = os.path.join(
            source_dir,
            os.path.splitext(filename)[0] + "_terrain_cutout_coverage.json",
        )
        try:
            with open(terrain_cutout_manifest_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(_terrain_cutout_coverage_manifest(terrain_cutout_report), f, indent=2, sort_keys=True)
                f.write("\n")
        except OSError as exc:
            cautions.append(f"terrain cutout coverage manifest write failed: {exc}")
            terrain_cutout_manifest_path = ""
        if terrain_cutout_report.blockers:
            cautions.append(
                f"terrain cutout coverage report did not complete cleanly: {terrain_cutout_report.status}"
            )
        else:
            notes.append(
                "Terrain cutout coverage report built: "
                f"covered={terrain_cutout_report.covered_cutout_count}, "
                f"partial={terrain_cutout_report.partial_cutout_count}, "
                f"present={terrain_cutout_report.terrain_present_count}."
            )

    surrogate_report = surrogate_ed.write_full_world_skeleton_surrogate_legacy_ed_from_dat(
        source_dat,
        generated_ed,
        model_names=selected_names,
        max_models=len(selected_names),
        include_skyboxes=include_skyboxes,
        group_name=group_name,
        brush_name_prefix=brush_name_prefix,
        include_validation_floor=include_validation_floor,
        validation_floor_name=validation_floor_name,
        validation_floor_margin=validation_floor_margin,
        validation_floor_thickness=validation_floor_thickness,
        validation_floor_texture=validation_floor_texture,
        include_terrain_support_patch=include_terrain_support_patch,
        terrain_support_model_name=terrain_support_model_name,
        terrain_support_name_prefix=terrain_support_name_prefix,
        terrain_support_margin=terrain_support_margin,
        terrain_support_selection_mode=terrain_support_selection_mode,
        terrain_support_radius=terrain_support_radius,
        terrain_support_brush_mode=terrain_support_brush_mode,
        terrain_support_thickness=terrain_support_thickness,
        terrain_support_max_polygons=terrain_support_max_polygons,
        terrain_support_side_texture=terrain_support_side_texture,
        include_physics_shell_patch=include_physics_shell_patch,
        physics_shell_model_name=physics_shell_model_name,
        physics_shell_name_prefix=physics_shell_name_prefix,
        physics_shell_max_polygons=effective_physics_shell_max_polygons,
        physics_shell_thickness=physics_shell_thickness,
        physics_shell_side_texture=physics_shell_side_texture,
    )
    if surrogate_report.status != "full_world_skeleton_surrogate_ed_built":
        return FullWorldSkeletonAcceptanceReport(
            status="full_world_skeleton_build_failed",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            work_dir=work_root,
            world_install_path=install_path,
            group_name=group_name,
            include_validation_floor=include_validation_floor,
            include_terrain_support_patch=include_terrain_support_patch,
            include_physics_shell_patch=include_physics_shell_patch,
            selected_model_names=selected_names,
            model_count=surrogate_report.model_count,
            point_count=surrogate_report.point_count,
            polygon_count=surrogate_report.polygon_count,
            object_count=surrogate_report.object_count,
            object_property_count=surrogate_report.object_property_count,
            generated_byte_count=surrogate_report.generated_byte_count,
            node_hierarchy_byte_count=surrogate_report.node_hierarchy_byte_count,
            wrapper_kind=surrogate_report.wrapper_kind,
            wrapper_block_count=surrogate_report.wrapper_block_count,
            models=model_summaries,
            terrain_cutout_coverage_manifest_path=terrain_cutout_manifest_path,
            terrain_cutout_coverage=terrain_cutout_report,
            blockers=tuple(_unique_text(tuple(surrogate_report.blockers))),
            cautions=tuple(_unique_text(cautions + list(surrogate_report.cautions))),
            notes=tuple(_unique_text(notes + skipped_notes + list(surrogate_report.notes))),
        )

    if include_terrain_support_source_coverage:
        terrain_support_source_report = build_terrain_support_source_coverage_report(
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            terrain_model_name=terrain_support_model_name or "Terrain0",
            ignored_terrain_textures=terrain_support_source_coverage_ignored_textures,
            sample_grid=terrain_support_source_coverage_sample_grid,
            max_gaps=terrain_support_source_coverage_max_gaps,
        )
        terrain_support_source_manifest_path = os.path.join(
            source_dir,
            os.path.splitext(filename)[0] + "_terrain_support_source_coverage.json",
        )
        try:
            with open(terrain_support_source_manifest_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(
                    _terrain_support_source_coverage_manifest(terrain_support_source_report),
                    f,
                    indent=2,
                    sort_keys=True,
                )
                f.write("\n")
        except OSError as exc:
            cautions.append(f"terrain support source coverage manifest write failed: {exc}")
            terrain_support_source_manifest_path = ""
        if terrain_support_source_report.blockers:
            cautions.append(
                f"terrain support source coverage report did not complete cleanly: {terrain_support_source_report.status}"
            )
        elif terrain_support_source_report.missing_sample_count:
            cautions.append(
                "Terrain support source coverage found uncovered original Terrain0 sample(s): "
                f"{terrain_support_source_report.missing_sample_count}/"
                f"{terrain_support_source_report.sample_count}."
            )
        else:
            notes.append("Terrain support source coverage report found no uncovered source Terrain0 samples.")

    generated_object_count = 0
    generated_property_count = 0
    generated_class_counts: Dict[str, int] = {}
    try:
        scene = legacy_ed.load_legacy_ed_geometry_scene(generated_ed)
        object_report = legacy_ed.load_legacy_ed_object_scan_report(generated_ed)
        layout = legacy_ed.load_legacy_ed_node_layout_report(generated_ed)
        generated_brush_count = int(scene.metadata.get("recovered_brush_count", 0) or 0)
        generated_polygon_count = int(scene.metadata.get("recovered_polygon_count", 0) or 0)
        generated_object_count = int(object_report.object_count)
        generated_property_count = int(object_report.property_count)
        generated_class_counts = {
            str(name): int(count)
            for name, count in object_report.class_counts.items()
        }
        if (
            generated_brush_count != surrogate_report.model_count
            or generated_polygon_count != surrogate_report.polygon_count
            or layout.status != "layout_parsed"
        ):
            return FullWorldSkeletonAcceptanceReport(
                status="full_world_skeleton_roundtrip_count_mismatch",
                source_dat_path=source_dat,
                generated_ed_path=generated_ed,
                work_dir=work_root,
                world_install_path=install_path,
                group_name=group_name,
                include_validation_floor=include_validation_floor,
                include_terrain_support_patch=include_terrain_support_patch,
                include_physics_shell_patch=include_physics_shell_patch,
                selected_model_names=selected_names,
                model_count=generated_brush_count,
                point_count=surrogate_report.point_count,
                polygon_count=generated_polygon_count,
                object_count=generated_object_count,
                object_property_count=generated_property_count,
                generated_byte_count=surrogate_report.generated_byte_count,
                node_hierarchy_byte_count=surrogate_report.node_hierarchy_byte_count,
                wrapper_kind=surrogate_report.wrapper_kind,
                wrapper_block_count=surrogate_report.wrapper_block_count,
                generated_object_class_counts=generated_class_counts,
                max_processor_brushes=processor_brush_budget,
                max_processor_polygons=processor_polygon_budget,
                models=model_summaries,
                terrain_cutout_coverage_manifest_path=terrain_cutout_manifest_path,
                terrain_cutout_coverage=terrain_cutout_report,
                terrain_support_source_coverage_manifest_path=terrain_support_source_manifest_path,
                terrain_support_source_coverage=terrain_support_source_report,
                blockers=("generated full-world skeleton did not round-trip with expected brush/polygon/layout counts",),
                cautions=tuple(_unique_text(cautions + list(surrogate_report.cautions))),
                notes=tuple(_unique_text(notes + skipped_notes + list(surrogate_report.notes))),
            )
    except Exception as exc:
        return FullWorldSkeletonAcceptanceReport(
            status="full_world_skeleton_parse_failed",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            work_dir=work_root,
            world_install_path=install_path,
            group_name=group_name,
            include_validation_floor=include_validation_floor,
            include_terrain_support_patch=include_terrain_support_patch,
            include_physics_shell_patch=include_physics_shell_patch,
            selected_model_names=selected_names,
            model_count=surrogate_report.model_count,
            point_count=surrogate_report.point_count,
            polygon_count=surrogate_report.polygon_count,
            generated_byte_count=surrogate_report.generated_byte_count,
            node_hierarchy_byte_count=surrogate_report.node_hierarchy_byte_count,
            wrapper_kind=surrogate_report.wrapper_kind,
            wrapper_block_count=surrogate_report.wrapper_block_count,
            max_processor_brushes=processor_brush_budget,
            max_processor_polygons=processor_polygon_budget,
            models=model_summaries,
            terrain_cutout_coverage_manifest_path=terrain_cutout_manifest_path,
            terrain_cutout_coverage=terrain_cutout_report,
            terrain_support_source_coverage_manifest_path=terrain_support_source_manifest_path,
            terrain_support_source_coverage=terrain_support_source_report,
            blockers=(f"generated full-world skeleton could not be parsed: {exc}",),
            cautions=tuple(_unique_text(cautions + list(surrogate_report.cautions))),
            notes=tuple(_unique_text(notes + skipped_notes + list(surrogate_report.notes))),
        )

    processor_budget_blockers: List[str] = []
    if processor_brush_budget and generated_brush_count > processor_brush_budget:
        processor_budget_blockers.append(
            f"generated Brush count {generated_brush_count} exceeds LithTech 2.1 Processor budget {processor_brush_budget}"
        )
    if processor_polygon_budget and generated_polygon_count > processor_polygon_budget:
        processor_budget_blockers.append(
            f"generated polygon count {generated_polygon_count} exceeds LithTech 2.1 Processor budget {processor_polygon_budget}"
        )
    if processor_budget_blockers:
        processor_budget_blockers.append(
            "reduce selected models or terrain support radius before running Processor.exe; previous over-budget terrain support produced millions of BTW poly splits during Joining polies"
        )
        return FullWorldSkeletonAcceptanceReport(
            status="full_world_skeleton_processor_budget_exceeded",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            work_dir=work_root,
            world_install_path=install_path,
            group_name=group_name,
            include_validation_floor=include_validation_floor,
            include_terrain_support_patch=include_terrain_support_patch,
            include_physics_shell_patch=include_physics_shell_patch,
            selected_model_names=selected_names,
            model_count=generated_brush_count,
            point_count=surrogate_report.point_count,
            polygon_count=generated_polygon_count,
            object_count=generated_object_count,
            object_property_count=generated_property_count,
            generated_byte_count=surrogate_report.generated_byte_count,
            node_hierarchy_byte_count=surrogate_report.node_hierarchy_byte_count,
            wrapper_kind=surrogate_report.wrapper_kind,
            wrapper_block_count=surrogate_report.wrapper_block_count,
            generated_object_class_counts=generated_class_counts,
            max_processor_brushes=processor_brush_budget,
            max_processor_polygons=processor_polygon_budget,
            models=model_summaries,
            terrain_cutout_coverage_manifest_path=terrain_cutout_manifest_path,
            terrain_cutout_coverage=terrain_cutout_report,
            terrain_support_source_coverage_manifest_path=terrain_support_source_manifest_path,
            terrain_support_source_coverage=terrain_support_source_report,
            blockers=tuple(_unique_text(tuple(processor_budget_blockers))),
            cautions=tuple(_unique_text(cautions + list(surrogate_report.cautions))),
            notes=tuple(_unique_text(notes + skipped_notes + list(surrogate_report.notes))),
        )

    if should_block_unreconstructed_physics_shell and unreconstructed_physics_shell is not None:
        physics_name = str(getattr(unreconstructed_physics_shell, "name", "") or "PhysicsBSP")
        physics_points = len(getattr(unreconstructed_physics_shell, "points", ()) or ())
        physics_polygons = len(getattr(unreconstructed_physics_shell, "polygons", ()) or ())
        blockers = (
            f"{physics_name} has {physics_polygons} compiled collision polygon(s) and likely contains the main static room/wall shell",
            "DAT -> ED generation does not yet reconstruct PhysicsBSP into source Brush geometry; generated ED is sparse diagnostics only",
            "implement PhysicsBSP shell reconstruction before treating this indoor/static level as ready for Processor.exe",
        )
        return FullWorldSkeletonAcceptanceReport(
            status="full_world_skeleton_static_shell_unreconstructed",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            work_dir=work_root,
            world_install_path=install_path,
            group_name=group_name,
            include_validation_floor=include_validation_floor,
            include_terrain_support_patch=include_terrain_support_patch,
            include_physics_shell_patch=include_physics_shell_patch,
            selected_model_names=selected_names,
            model_count=generated_brush_count,
            point_count=surrogate_report.point_count,
            polygon_count=generated_polygon_count,
            object_count=generated_object_count,
            object_property_count=generated_property_count,
            generated_byte_count=surrogate_report.generated_byte_count,
            node_hierarchy_byte_count=surrogate_report.node_hierarchy_byte_count,
            wrapper_kind=surrogate_report.wrapper_kind,
            wrapper_block_count=surrogate_report.wrapper_block_count,
            generated_object_class_counts=generated_class_counts,
            max_processor_brushes=processor_brush_budget,
            max_processor_polygons=processor_polygon_budget,
            models=model_summaries,
            terrain_cutout_coverage_manifest_path=terrain_cutout_manifest_path,
            terrain_cutout_coverage=terrain_cutout_report,
            terrain_support_source_coverage_manifest_path=terrain_support_source_manifest_path,
            terrain_support_source_coverage=terrain_support_source_report,
            blockers=blockers,
            cautions=tuple(_unique_text(cautions + list(surrogate_report.cautions))),
            notes=tuple(_unique_text(
                notes
                + skipped_notes
                + list(surrogate_report.notes)
                + [f"{physics_name}: points={physics_points}, polygons={physics_polygons}"]
            )),
        )

    target_text = install_path or "the real MM9 project data\\WORLDS directory"
    manual_steps = (
        f"Copy {generated_ed} to {target_text}.",
        "Open old LithTech 2.1 DEDit through the MM9.dep project and open the generated world.",
        "Confirm the generated group appears, the selected brushes keep their relative positions, and the StartPoint/Light objects are present.",
        "Save the world through DEDit under a short test name, then compile that ED with Processor.exe.",
        "Fresh-load the compiled DAT in game and check rendering plus collision around every generated brush.",
    )
    if include_validation_floor:
        manual_steps = manual_steps[:2] + (
            "Confirm the synthetic validation floor is present below the cluster and the StartPoint is above it.",
        ) + manual_steps[2:]
    if include_terrain_support_patch:
        manual_steps = manual_steps[:2] + (
            f"Confirm the closed {terrain_support_model_name} support patch is present under the generated cluster and the StartPoint is above it.",
        ) + manual_steps[2:]
    if include_physics_shell_patch:
        manual_steps = manual_steps[:2] + (
            f"Confirm the generated {physics_shell_model_name} shell slab brushes are present around the reconstructed static room/wall shell.",
        ) + manual_steps[2:]
    if terrain_cutout_report is not None:
        manual_steps = manual_steps[:2] + (
            "Review the terrain cutout coverage manifest; rectangular covered_cutout gaps should align with original model/building footprints before being treated as terrain loss.",
        ) + manual_steps[2:]
    if terrain_support_source_report is not None:
        manual_steps = manual_steps[:2] + (
            "Review the terrain support source coverage manifest; non-covered source Terrain0 gaps are the actionable generator-loss candidates.",
        ) + manual_steps[2:]
    return FullWorldSkeletonAcceptanceReport(
        status="ready_for_manual_full_world_skeleton_test",
        source_dat_path=source_dat,
        generated_ed_path=generated_ed,
        work_dir=work_root,
        world_install_path=install_path,
        group_name=group_name,
        include_validation_floor=include_validation_floor,
        include_terrain_support_patch=include_terrain_support_patch,
        include_physics_shell_patch=include_physics_shell_patch,
        selected_model_names=selected_names,
        model_count=surrogate_report.model_count,
        point_count=surrogate_report.point_count,
        polygon_count=surrogate_report.polygon_count,
        object_count=generated_object_count,
        object_property_count=generated_property_count,
        generated_byte_count=surrogate_report.generated_byte_count,
        node_hierarchy_byte_count=surrogate_report.node_hierarchy_byte_count,
        wrapper_kind=surrogate_report.wrapper_kind,
        wrapper_block_count=surrogate_report.wrapper_block_count,
        generated_object_class_counts=generated_class_counts,
        max_processor_brushes=processor_brush_budget,
        max_processor_polygons=processor_polygon_budget,
        models=model_summaries,
        terrain_cutout_coverage_manifest_path=terrain_cutout_manifest_path,
        terrain_cutout_coverage=terrain_cutout_report,
        terrain_support_source_coverage_manifest_path=terrain_support_source_manifest_path,
        terrain_support_source_coverage=terrain_support_source_report,
        manual_steps=manual_steps,
        cautions=tuple(_unique_text(cautions + list(surrogate_report.cautions))),
        notes=tuple(_unique_text(notes + skipped_notes + list(surrogate_report.notes))),
    )


def build_full_world_skeleton_compiled_validation_report(
    *,
    generated_ed_path: str,
    compiled_dat_path: str,
    processor_log_paths: Sequence[str] = (),
    manual_validation: Optional[BlackBoxCompilerManualValidation] = None,
    max_start_floor_drop: float = 256.0,
) -> FullWorldSkeletonCompiledValidationReport:
    """Validate a manually compiled full-world skeleton DAT.

    This is a post-Processor audit.  It does not launch old DEDit or
    ``Processor.exe``; it consumes the generated ED, compiled DAT, and any
    Processor log(s), then checks the specific gameplay condition that matters
    for terrain-support skeletons: a walkable ``PhysicsBSP`` floor below the
    generated ``StartPoint``.
    """
    ed_path = os.path.abspath(generated_ed_path)
    dat_path = os.path.abspath(compiled_dat_path)
    logs = tuple(os.path.abspath(path) for path in processor_log_paths if path)
    manual = manual_validation or BlackBoxCompilerManualValidation()
    max_drop = max(0.0, float(max_start_floor_drop))
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = []
    start_point: Optional[Tuple[float, float, float]] = None
    move_player_to_floor: Optional[bool] = None
    physics_floor_y: Optional[float] = None
    physics_floor_drop: Optional[float] = None

    if not os.path.exists(ed_path):
        blockers.append(f"generated ED was not found: {ed_path}")
    else:
        try:
            from features.dat_editing import legacy_ed

            object_scan = legacy_ed.load_legacy_ed_object_scan_report(ed_path)
            start_records = [
                record
                for record in object_scan.records
                if str(record.class_name) == "StartPoint"
            ]
            if not start_records:
                blockers.append("generated ED has no StartPoint object")
            elif len(start_records) > 1:
                cautions.append(f"generated ED has {len(start_records)} StartPoint objects; using the first")
            if start_records:
                raw_pos = start_records[0].property_value("Pos")
                if _is_vec3(raw_pos):
                    start_point = (float(raw_pos[0]), float(raw_pos[1]), float(raw_pos[2]))
                else:
                    blockers.append("StartPoint Pos property was not decoded as a vector")
                raw_move = start_records[0].property_value("MovePlayerToFloor")
                move_player_to_floor = bool(raw_move) if raw_move is not None else None
                if move_player_to_floor is not True:
                    cautions.append("StartPoint MovePlayerToFloor is not enabled")
        except Exception as exc:
            blockers.append(f"generated ED object scan failed: {exc}")

    if not os.path.exists(dat_path):
        dat_summary = DatOutputSemanticSummary(
            path=dat_path,
            status="missing",
            notes=(f"compiled DAT was not found: {dat_path}",),
        )
        blockers.append(f"compiled DAT was not found: {dat_path}")
    else:
        dat_summary = _load_dat_output_semantic_summary(dat_path)
        if dat_summary.status != "loaded":
            blockers.append(f"compiled DAT did not parse cleanly: {dat_summary.status}")
        elif not dat_summary.physics_bsp_present:
            blockers.append("compiled DAT has no PhysicsBSP")
        elif start_point is not None:
            try:
                from core import bsp

                with open(dat_path, "rb") as f:
                    parsed = bsp.parse(f.read())
                physics = next(
                    (
                        model
                        for model in getattr(parsed, "world_models", ()) or ()
                        if terrain_semantics.is_physics_bsp_model(model)
                    ),
                    None,
                )
                if physics is None:
                    blockers.append("compiled DAT parser did not expose PhysicsBSP geometry")
                else:
                    sub_world = bsp.BspWorld(
                        version=int(getattr(parsed, "version", 66) or 66),
                        world_info=str(getattr(parsed, "world_info", "") or ""),
                        world_models=[physics],
                    )
                    physics_floor_y = bsp.raycast_floor_y(
                        sub_world,
                        start_point[0],
                        start_point[2],
                        y_hint_min=start_point[1] - max(1024.0, max_drop * 4.0),
                        y_hint_max=start_point[1] + 64.0,
                    )
                    if physics_floor_y is None:
                        blockers.append("compiled PhysicsBSP has no upward floor below StartPoint")
                    else:
                        physics_floor_drop = start_point[1] - physics_floor_y
                        if physics_floor_drop < -8.0:
                            blockers.append(
                                f"compiled PhysicsBSP floor is above StartPoint by {-physics_floor_drop:.2f} units"
                            )
                        elif physics_floor_drop > max_drop:
                            blockers.append(
                                f"compiled PhysicsBSP floor is {physics_floor_drop:.2f} units below StartPoint, "
                                f"above allowed drop {max_drop:.2f}"
                            )
            except Exception as exc:
                blockers.append(f"PhysicsBSP floor probe failed: {exc}")

    parsed_logs = tuple(_parse_processor_log(path) for path in logs)
    missing_logs = [log for log in parsed_logs if log.status == "missing"]
    if missing_logs:
        cautions.append(f"{len(missing_logs)} Processor log file(s) were not found")
    for log in parsed_logs:
        if log.problem_brush_count:
            cautions.append(f"Processor reported {log.problem_brush_count} problem brush(es)")
        if log.warnings:
            cautions.append("Processor emitted warning(s)")

    manual_failed = _manual_validation_failed(manual)
    manual_passed = _manual_validation_passed(manual)
    if manual_failed:
        blockers.append("manual in-game validation failed")
    elif not manual_passed:
        cautions.append("manual fresh-load in-game validation is still required")
    else:
        notes.append("manual fresh-load in-game validation passed")
    notes.extend(str(note) for note in manual.notes)

    if blockers:
        status = "compiled_validation_failed"
    elif manual_passed:
        status = "validated_in_game"
    else:
        status = "compiled_floor_probe_passed_needs_game_validation"

    return FullWorldSkeletonCompiledValidationReport(
        status=status,
        generated_ed_path=ed_path,
        compiled_dat_path=dat_path,
        processor_log_paths=logs,
        start_point=start_point,
        move_player_to_floor=move_player_to_floor,
        physics_floor_y=physics_floor_y,
        physics_floor_drop=physics_floor_drop,
        max_start_floor_drop=max_drop,
        dat=dat_summary,
        processor_logs=parsed_logs,
        manual_validation=manual,
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def build_terrain_cutout_coverage_report(
    *,
    source_dat_path: str,
    terrain_model_name: str = "Terrain0",
    ignored_terrain_textures: Sequence[str] = ("TEXTURES\\LevelTextures\\Terrain\\sand.dtx",),
    sample_grid: int = 7,
    cluster_gap: float = 64.0,
    min_cluster_footprint_area: float = 4096.0,
    min_model_footprint_area: float = 1024.0,
    covered_cutout_missing_ratio: float = 0.65,
    partial_cutout_missing_ratio: float = 0.25,
    max_candidates: int = 64,
    include_skyboxes: bool = False,
) -> TerrainCutoutCoverageReport:
    """Report likely intentional Terrain0 cutouts covered by original models.

    This does not rewrite generated ED terrain.  It samples grouped non-terrain
    model footprints against the playable Terrain0 coverage so remaining
    rectangular gaps can be separated from true source-generation loss.
    """
    source_dat = os.path.abspath(source_dat_path)
    ignored_textures = tuple(str(item).lower() for item in ignored_terrain_textures)
    cautions: List[str] = [
        "Terrain cutout coverage is sample-based evidence, not a source CSG reconstruction.",
        "Covered cutouts should still be checked in old DEDit and in game after Processor compilation.",
    ]
    if ignored_textures:
        cautions.append(
            "Ignored terrain textures are not treated as playable cutout coverage: "
            + ", ".join(ignored_terrain_textures)
        )
    notes: List[str] = [
        "This diagnostic is intended for DAT-derived full-world ED skeleton terrain support.",
        "It clusters nearby original non-terrain world models and samples their X/Z footprint against Terrain0.",
    ]
    if not os.path.exists(source_dat):
        return TerrainCutoutCoverageReport(
            status="source_dat_missing",
            source_dat_path=source_dat,
            terrain_model_name=terrain_model_name,
            ignored_terrain_textures=tuple(ignored_terrain_textures),
            blockers=(f"source DAT was not found: {source_dat}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    try:
        from core import bsp

        with open(source_dat, "rb") as f:
            parsed = bsp.parse(f.read())
    except Exception as exc:
        return TerrainCutoutCoverageReport(
            status="dat_parse_failed",
            source_dat_path=source_dat,
            terrain_model_name=terrain_model_name,
            ignored_terrain_textures=tuple(ignored_terrain_textures),
            blockers=(f"DAT parse failed: {exc}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    terrain_name = str(terrain_model_name or "Terrain0")
    terrain = next(
        (
            model
            for model in getattr(parsed, "world_models", ()) or ()
            if str(getattr(model, "name", "") or "").lower() == terrain_name.lower()
        ),
        None,
    )
    if terrain is None:
        return TerrainCutoutCoverageReport(
            status="terrain_model_missing",
            source_dat_path=source_dat,
            terrain_model_name=terrain_name,
            ignored_terrain_textures=tuple(ignored_terrain_textures),
            blockers=(f"terrain model was not found: {terrain_name}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    terrain_items = _terrain_cutout_coverage_items(terrain, ignored_textures=ignored_textures)
    terrain_polygon_count = len(getattr(terrain, "polygons", ()) or ())
    if not terrain_items:
        return TerrainCutoutCoverageReport(
            status="no_terrain_coverage_polygons",
            source_dat_path=source_dat,
            terrain_model_name=terrain_name,
            terrain_polygon_count=terrain_polygon_count,
            ignored_terrain_textures=tuple(ignored_terrain_textures),
            blockers=(f"{terrain_name} has no usable coverage polygons after texture filters",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    model_infos = terrain_reconstruction.terrain_cutout_model_infos(
        getattr(parsed, "world_models", ()) or (),
        include_skyboxes=include_skyboxes,
        min_model_footprint_area=float(min_model_footprint_area),
    )
    clusters = terrain_reconstruction.terrain_cutout_model_clusters(
        model_infos,
        cluster_gap=max(0.0, float(cluster_gap)),
        min_cluster_footprint_area=max(0.0, float(min_cluster_footprint_area)),
    )
    sample_side = _clamp_int(int(sample_grid), 3, 15)
    candidates: List[TerrainCutoutCoverageCandidate] = []
    for cluster_index, cluster in enumerate(clusters):
        candidate = _terrain_cutout_coverage_candidate(
            cluster,
            cluster_index=cluster_index,
            terrain_items=terrain_items,
            sample_grid=sample_side,
            covered_cutout_missing_ratio=float(covered_cutout_missing_ratio),
            partial_cutout_missing_ratio=float(partial_cutout_missing_ratio),
        )
        candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            _terrain_cutout_class_rank(item.classification),
            -item.footprint_area,
            item.model_names[0].lower() if item.model_names else "",
        )
    )
    limit = max(0, int(max_candidates))
    if limit:
        candidates = candidates[:limit]
    status = "terrain_cutout_coverage_built" if candidates else "no_cutout_candidates"
    covered = sum(1 for item in candidates if item.classification == "covered_cutout")
    partial = sum(1 for item in candidates if item.classification == "partial_cutout")
    present = sum(1 for item in candidates if item.classification == "terrain_present_under_models")
    uncertain = len(candidates) - covered - partial - present
    if covered:
        notes.append(
            f"{covered} sampled model footprint cluster(s) look like intentional Terrain0 cutouts."
        )
    if partial:
        notes.append(
            f"{partial} sampled model footprint cluster(s) have partial terrain coverage and need visual review."
        )

    return TerrainCutoutCoverageReport(
        status=status,
        source_dat_path=source_dat,
        terrain_model_name=terrain_name,
        terrain_polygon_count=terrain_polygon_count,
        terrain_coverage_polygon_count=len(terrain_items),
        sampled_model_count=sum(len(cluster) for cluster in clusters),
        candidate_count=len(candidates),
        covered_cutout_count=covered,
        partial_cutout_count=partial,
        terrain_present_count=present,
        uncertain_count=uncertain,
        skipped_model_count=max(0, len(getattr(parsed, "world_models", ()) or ()) - len(model_infos)),
        candidates=tuple(candidates),
        ignored_terrain_textures=tuple(ignored_terrain_textures),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def build_terrain_support_source_coverage_report(
    *,
    source_dat_path: str,
    generated_ed_path: str,
    terrain_model_name: str = "Terrain0",
    ignored_terrain_textures: Sequence[str] = ("TEXTURES\\LevelTextures\\Terrain\\sand.dtx",),
    sample_grid: int = 3,
    max_gaps: int = 64,
) -> TerrainSupportSourceCoverageReport:
    """Compare original Terrain0 source polygons against generated ED terrain tops."""
    source_dat = os.path.abspath(source_dat_path)
    generated_ed = os.path.abspath(generated_ed_path)
    ignored_textures = tuple(str(item).lower() for item in ignored_terrain_textures)
    cautions: List[str] = [
        "Terrain support source coverage is a source-vs-generated diagnostic; it does not prove Processor kept every polygon.",
        "Use this with full Terrain0 support candidates. Local support patches are expected to report uncovered source polygons.",
    ]
    notes: List[str] = [
        "Samples original playable Terrain0 polygon interiors and tests whether generated ED terrain-top faces cover them.",
    ]
    if not os.path.exists(source_dat):
        return TerrainSupportSourceCoverageReport(
            status="source_dat_missing",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            terrain_model_name=terrain_model_name,
            ignored_terrain_textures=tuple(ignored_terrain_textures),
            blockers=(f"source DAT was not found: {source_dat}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )
    if not os.path.exists(generated_ed):
        return TerrainSupportSourceCoverageReport(
            status="generated_ed_missing",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            terrain_model_name=terrain_model_name,
            ignored_terrain_textures=tuple(ignored_terrain_textures),
            blockers=(f"generated ED was not found: {generated_ed}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    try:
        from core import bsp

        with open(source_dat, "rb") as f:
            parsed = bsp.parse(f.read())
    except Exception as exc:
        return TerrainSupportSourceCoverageReport(
            status="dat_parse_failed",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            terrain_model_name=terrain_model_name,
            ignored_terrain_textures=tuple(ignored_terrain_textures),
            blockers=(f"DAT parse failed: {exc}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )
    terrain_name = str(terrain_model_name or "Terrain0")
    terrain = next(
        (
            model
            for model in getattr(parsed, "world_models", ()) or ()
            if str(getattr(model, "name", "") or "").lower() == terrain_name.lower()
        ),
        None,
    )
    if terrain is None:
        return TerrainSupportSourceCoverageReport(
            status="terrain_model_missing",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            terrain_model_name=terrain_name,
            ignored_terrain_textures=tuple(ignored_terrain_textures),
            blockers=(f"terrain model was not found: {terrain_name}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    source_items = _terrain_source_coverage_items(terrain, ignored_textures=ignored_textures)
    source_polygon_count = len(getattr(terrain, "polygons", ()) or ())
    if not source_items:
        return TerrainSupportSourceCoverageReport(
            status="no_source_terrain_polygons",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            terrain_model_name=terrain_name,
            source_polygon_count=source_polygon_count,
            ignored_terrain_textures=tuple(ignored_terrain_textures),
            blockers=(f"{terrain_name} has no usable source coverage polygons after texture filters",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    source_textures = {item[3].lower() for item in source_items}
    try:
        from features.dat_editing import legacy_ed

        scene = legacy_ed.load_legacy_ed_geometry_scene(generated_ed)
        generated_items = _generated_ed_terrain_coverage_items(
            scene,
            source_texture_names=source_textures,
            ignored_textures=ignored_textures,
        )
    except Exception as exc:
        return TerrainSupportSourceCoverageReport(
            status="generated_ed_parse_failed",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            terrain_model_name=terrain_name,
            source_polygon_count=source_polygon_count,
            sampled_source_polygon_count=len(source_items),
            ignored_terrain_textures=tuple(ignored_terrain_textures),
            blockers=(f"generated ED terrain coverage parse failed: {exc}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )
    if not generated_items:
        return TerrainSupportSourceCoverageReport(
            status="no_generated_terrain_coverage",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            terrain_model_name=terrain_name,
            source_polygon_count=source_polygon_count,
            sampled_source_polygon_count=len(source_items),
            ignored_terrain_textures=tuple(ignored_terrain_textures),
            blockers=("generated ED has no terrain-top faces matching source Terrain0 textures",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    generated_by_texture: Dict[str, List[terrain_reconstruction.GeneratedTerrainCoverageItem]] = {}
    for item in generated_items:
        generated_by_texture.setdefault(item.texture_name.lower(), []).append(item)

    sample_side = _clamp_int(int(sample_grid), 1, 9)
    total_samples = 0
    covered_samples = 0
    missing_samples = 0
    missing_texture_counts: Dict[str, int] = {}
    gaps: List[TerrainSupportSourceCoverageGap] = []
    for item in source_items:
        polygon_index, bounds_min, bounds_max, texture_name, points_xz = item
        samples = terrain_reconstruction.xz_polygon_interior_sample_points(points_xz, sample_side)
        if not samples:
            continue
        local_missing = 0
        candidates = generated_by_texture.get(texture_name.lower(), ())
        for sample_x, sample_z in samples:
            total_samples += 1
            if terrain_reconstruction.generated_coverage_point_hit(sample_x, sample_z, candidates):
                covered_samples += 1
            else:
                missing_samples += 1
                local_missing += 1
                missing_texture_counts[texture_name] = missing_texture_counts.get(texture_name, 0) + 1
        if local_missing:
            gaps.append(TerrainSupportSourceCoverageGap(
                source_polygon_index=int(polygon_index),
                texture_name=texture_name,
                bounds_min=bounds_min,
                bounds_max=bounds_max,
                sample_count=len(samples),
                missing_sample_count=local_missing,
                missing_ratio=float(local_missing) / float(len(samples)),
            ))

    gaps.sort(key=lambda gap: (-gap.missing_ratio, -gap.missing_sample_count, gap.source_polygon_index))
    all_missing_polygon_count = len(gaps)
    gap_limit = max(0, int(max_gaps))
    if gap_limit:
        gaps = gaps[:gap_limit]
    missing_ratio = float(missing_samples) / float(total_samples) if total_samples else 0.0
    status = (
        "terrain_support_source_coverage_complete"
        if total_samples and missing_samples == 0
        else "terrain_support_source_coverage_has_gaps"
        if total_samples
        else "terrain_support_source_coverage_no_samples"
    )
    if missing_samples:
        notes.append(
            f"{missing_samples} source Terrain0 sample(s) are not covered by generated ED terrain-top faces."
        )
    else:
        notes.append("All sampled source Terrain0 points are covered by generated ED terrain-top faces.")

    return TerrainSupportSourceCoverageReport(
        status=status,
        source_dat_path=source_dat,
        generated_ed_path=generated_ed,
        terrain_model_name=terrain_name,
        source_polygon_count=source_polygon_count,
        sampled_source_polygon_count=len(source_items),
        generated_coverage_polygon_count=len(generated_items),
        sample_count=total_samples,
        covered_sample_count=covered_samples,
        missing_sample_count=missing_samples,
        missing_polygon_count=all_missing_polygon_count,
        missing_ratio=missing_ratio,
        source_texture_counts=_texture_count_pairs(item[3] for item in source_items),
        generated_texture_counts=_texture_count_pairs(item[4] for item in generated_items),
        missing_texture_sample_counts=tuple(sorted(
            missing_texture_counts.items(),
            key=lambda pair: (-pair[1], pair[0].lower()),
        )),
        gaps=tuple(gaps),
        ignored_terrain_textures=tuple(ignored_terrain_textures),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def run_black_box_ed_to_dat_harness(
    *,
    processor_path: str,
    source_ed_path: str,
    reference_dat_path: Optional[str] = None,
    work_dir: Optional[str] = None,
    processor_project_dir: Optional[str] = None,
    processor_prefix_args: Sequence[str] = (),
    option_template: Sequence[str] = DEFAULT_LITH21_PROCESSOR_OPTIONS,
    world_argument_template: str = "{ed_no_ext}",
    timeout_seconds: float = 900.0,
    preseed_reference_dat: bool = True,
) -> BlackBoxCompilerHarnessReport:
    """Run an ED->DAT compiler candidate in an isolated project workspace.

    The old LithTech 2.1 processor is a black-box executable.  This harness
    keeps it at arm's length: copy the ED fixture and optional reference DAT
    into a temporary ``WORLDS`` directory, run the candidate there, capture
    stdout/stderr/log files, and compare the generated DAT with the reference
    through the same semantic summaries used by Stage 7D.
    """
    processor = os.path.abspath(processor_path)
    source_ed = os.path.abspath(source_ed_path)
    reference_dat = os.path.abspath(reference_dat_path) if reference_dat_path else ""
    notes: List[str] = []
    if not os.path.exists(processor):
        return BlackBoxCompilerHarnessReport(
            status="processor_missing",
            processor_path=processor,
            source_ed_path=source_ed,
            reference_dat_path=reference_dat,
            notes=(f"processor was not found: {processor}",),
        )
    if not os.path.exists(source_ed):
        return BlackBoxCompilerHarnessReport(
            status="source_ed_missing",
            processor_path=processor,
            source_ed_path=source_ed,
            reference_dat_path=reference_dat,
            notes=(f"source ED was not found: {source_ed}",),
        )
    if reference_dat and not os.path.exists(reference_dat):
        return BlackBoxCompilerHarnessReport(
            status="reference_dat_missing",
            processor_path=processor,
            source_ed_path=source_ed,
            reference_dat_path=reference_dat,
            notes=(f"reference DAT was not found: {reference_dat}",),
        )

    work_root = os.path.abspath(work_dir) if work_dir else tempfile.mkdtemp(prefix="mm9_stage7e_")
    project_dir = os.path.join(work_root, "project")
    command_project_dir = os.path.abspath(processor_project_dir) if processor_project_dir else project_dir
    worlds_dir = os.path.join(project_dir, "WORLDS")
    os.makedirs(worlds_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(source_ed))[0]
    copied_ed = os.path.join(worlds_dir, stem + ".ed")
    output_dat = os.path.join(worlds_dir, stem + ".DAT")
    shutil.copyfile(source_ed, copied_ed)
    output_preseeded = bool(reference_dat and preseed_reference_dat)
    if output_preseeded:
        shutil.copyfile(reference_dat, output_dat)
    output_before = _file_signature(output_dat)

    command = build_black_box_processor_command(
        processor_path=processor,
        ed_path=copied_ed,
        project_dir=command_project_dir,
        processor_prefix_args=processor_prefix_args,
        option_template=option_template,
        world_argument_template=world_argument_template,
    )
    stdout_path = os.path.join(work_root, "processor.stdout.txt")
    stderr_path = os.path.join(work_root, "processor.stderr.txt")
    run_started_at = time.time() - 1.0
    start = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            cwd=os.path.dirname(processor) or None,
            timeout=float(timeout_seconds),
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
        elapsed = time.monotonic() - start
        with open(stdout_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(completed.stdout or "")
        with open(stderr_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(completed.stderr or "")
        returncode: Optional[int] = int(completed.returncode)
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - start
        with open(stdout_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(_subprocess_output_text(exc.stdout))
        with open(stderr_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(_subprocess_output_text(exc.stderr))
        log_paths = tuple(_black_box_run_log_paths(work_root, processor, stem, run_started_at))
        return BlackBoxCompilerHarnessReport(
            status="processor_timeout",
            processor_path=processor,
            source_ed_path=source_ed,
            reference_dat_path=reference_dat,
            work_dir=work_root,
            project_dir=project_dir,
            processor_project_dir=command_project_dir,
            worlds_dir=worlds_dir,
            copied_ed_path=copied_ed,
            output_dat_path=output_dat,
            command=tuple(command),
            returncode=None,
            elapsed_seconds=float(elapsed),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            log_paths=log_paths,
            output_preseeded=output_preseeded,
            output_rewritten=_file_signature(output_dat) != output_before,
            processor_logs=tuple(_parse_processor_log(path) for path in log_paths),
            notes=tuple(_unique_text((
                f"processor timed out after {timeout_seconds:g} second(s)",
                "LithTech 2.1 Processor.exe may be waiting for its Processing Options modal dialog",
            ))),
        )

    output_after = _file_signature(output_dat)
    output_rewritten = output_after != output_before
    if returncode != 0:
        status = "processor_failed"
        notes.append(f"processor returned exit code {returncode}")
    elif not os.path.exists(output_dat):
        status = "output_dat_missing"
        notes.append(f"expected output DAT was not found: {output_dat}")
    elif output_preseeded and not output_rewritten:
        status = "output_dat_unchanged"
        notes.append("preseeded reference DAT was not rewritten by the processor")
    else:
        status = "compiled"

    reference_summary = _load_dat_output_semantic_summary(reference_dat) if reference_dat else None
    generated_summary = (
        _load_dat_output_semantic_summary(output_dat)
        if os.path.exists(output_dat)
        else None
    )
    comparisons: Tuple[BlackBoxCompilerSystemComparison, ...] = ()
    if reference_summary is not None and generated_summary is not None:
        comparisons = tuple(_compare_black_box_dat_summaries(reference_summary, generated_summary))
        world_model_comparisons = tuple(_compare_black_box_world_model_summaries(reference_summary, generated_summary))
        if status == "compiled":
            if generated_summary.status != "loaded":
                status = "generated_dat_parse_failed"
                notes.append("generated DAT did not parse as an MM9 v66 DAT")
            elif reference_summary.status != "loaded":
                status = "reference_dat_parse_failed"
                notes.append("reference DAT did not parse as an MM9 v66 DAT")
            else:
                mismatches = [item for item in comparisons if item.status == "mismatch"]
                status = "compiled_and_compared" if not mismatches else "compiled_with_semantic_differences"
    else:
        world_model_comparisons = ()

    log_paths = tuple(_black_box_run_log_paths(work_root, processor, stem, run_started_at))
    return BlackBoxCompilerHarnessReport(
        status=status,
        processor_path=processor,
        source_ed_path=source_ed,
        reference_dat_path=reference_dat,
        work_dir=work_root,
        project_dir=project_dir,
        processor_project_dir=command_project_dir,
        worlds_dir=worlds_dir,
        copied_ed_path=copied_ed,
        output_dat_path=output_dat,
        command=tuple(command),
        returncode=returncode,
        elapsed_seconds=float(elapsed),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        log_paths=log_paths,
        output_preseeded=output_preseeded,
        output_rewritten=output_rewritten,
        reference=reference_summary,
        generated=generated_summary,
        comparisons=comparisons,
        world_model_comparisons=world_model_comparisons,
        processor_logs=tuple(_parse_processor_log(path) for path in log_paths),
        notes=tuple(_unique_text(notes)),
    )


def run_black_box_ed_to_dat_corpus_harness(
    *,
    processor_path: str,
    worlds_dir: str,
    source_roots: Sequence[str] = (),
    stems: Optional[Sequence[str]] = None,
    max_fixtures: Optional[int] = None,
    work_dir: Optional[str] = None,
    processor_project_dir: Optional[str] = None,
    processor_prefix_args: Sequence[str] = (),
    option_template: Sequence[str] = DEFAULT_LITH21_PROCESSOR_OPTIONS,
    world_argument_template: str = "{ed_no_ext}",
    timeout_seconds: float = 900.0,
    preseed_reference_dat: bool = True,
) -> BlackBoxCompilerCorpusReport:
    """Run the black-box ED->DAT harness over paired legacy ED/DAT fixtures."""
    processor = os.path.abspath(processor_path)
    worlds = os.path.abspath(worlds_dir)
    if not os.path.exists(worlds):
        return BlackBoxCompilerCorpusReport(
            status="worlds_dir_missing",
            processor_path=processor,
            worlds_dir=worlds,
            notes=(f"worlds directory was not found: {worlds}",),
        )

    corpus = build_source_world_comparison_report(
        worlds_dir=worlds,
        source_roots=source_roots,
    )
    allowed_stems = {str(stem).upper() for stem in stems or ()}
    fixtures: List[Tuple[str, SourceWorldArtifact, SourceWorldArtifact]] = []
    for pair in corpus.pairs:
        if pair.dat is None or pair.status != "paired_v66_dat_with_source":
            continue
        if allowed_stems and pair.stem.upper() not in allowed_stems:
            continue
        ed_sources = [
            source
            for source in pair.sources
            if source.format == "ed" and source.status == "legacy_ed"
        ]
        if not ed_sources:
            continue
        fixtures.append((pair.stem, ed_sources[0], pair.dat))

    fixtures.sort(key=lambda item: item[0])
    skipped_count = 0
    if max_fixtures is not None and len(fixtures) > max(0, int(max_fixtures)):
        skipped_count = len(fixtures) - max(0, int(max_fixtures))
        fixtures = fixtures[:max(0, int(max_fixtures))]

    work_root = os.path.abspath(work_dir) if work_dir else tempfile.mkdtemp(prefix="mm9_stage7f_")
    command_project_dir = os.path.abspath(processor_project_dir) if processor_project_dir else ""
    if not fixtures:
        return BlackBoxCompilerCorpusReport(
            status="no_paired_legacy_ed_fixtures",
            processor_path=processor,
            worlds_dir=worlds,
            work_dir=work_root,
            processor_project_dir=command_project_dir,
            skipped_count=skipped_count,
            notes=("no same-stem legacy ED/v66 DAT fixture pairs were selected",),
        )

    runs: List[BlackBoxCompilerCorpusRun] = []
    for stem, source_ed, reference_dat in fixtures:
        run_work = os.path.join(work_root, stem)
        report = run_black_box_ed_to_dat_harness(
            processor_path=processor,
            source_ed_path=source_ed.path,
            reference_dat_path=reference_dat.path,
            work_dir=run_work,
            processor_project_dir=processor_project_dir,
            processor_prefix_args=processor_prefix_args,
            option_template=option_template,
            world_argument_template=world_argument_template,
            timeout_seconds=timeout_seconds,
            preseed_reference_dat=preseed_reference_dat,
        )
        runs.append(BlackBoxCompilerCorpusRun(
            stem=stem,
            source_ed_path=source_ed.path,
            reference_dat_path=reference_dat.path,
            status=report.status,
            report=report,
        ))

    matched_count = sum(1 for run in runs if _black_box_match_status(run.status))
    differing_count = sum(1 for run in runs if _black_box_difference_status(run.status))
    failed_count = len(runs) - matched_count - differing_count
    status = _black_box_corpus_status(len(runs), matched_count, failed_count)
    notes = (
        "Stage 7F corpus runs are research-only and do not make a processor save-backend compatible.",
        "A preseeded reference DAT must be rewritten by the processor before a semantic match is trusted.",
    )
    return BlackBoxCompilerCorpusReport(
        status=status,
        processor_path=processor,
        worlds_dir=worlds,
        work_dir=work_root,
        processor_project_dir=command_project_dir,
        fixture_count=len(fixtures),
        ran_count=len(runs),
        matched_count=matched_count,
        differing_count=differing_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        runs=tuple(runs),
        notes=notes,
    )


def build_black_box_captured_output_report(
    *,
    generated_dat_path: str,
    reference_dat_path: str,
    source_ed_path: str = "",
    processor_path: str = "",
    log_paths: Sequence[str] = (),
) -> BlackBoxCompilerHarnessReport:
    """Compare a manually captured black-box compiler DAT against a reference DAT."""
    generated_dat = os.path.abspath(generated_dat_path)
    reference_dat = os.path.abspath(reference_dat_path)
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    processor = os.path.abspath(processor_path) if processor_path else ""
    logs = tuple(os.path.abspath(path) for path in log_paths if path)
    notes: List[str] = []
    if not os.path.exists(reference_dat):
        return BlackBoxCompilerHarnessReport(
            status="reference_dat_missing",
            processor_path=processor,
            source_ed_path=source_ed,
            reference_dat_path=reference_dat,
            output_dat_path=generated_dat,
            log_paths=logs,
            captured_output=True,
            notes=(f"reference DAT was not found: {reference_dat}",),
        )
    if not os.path.exists(generated_dat):
        return BlackBoxCompilerHarnessReport(
            status="generated_dat_missing",
            processor_path=processor,
            source_ed_path=source_ed,
            reference_dat_path=reference_dat,
            output_dat_path=generated_dat,
            log_paths=logs,
            captured_output=True,
            notes=(f"generated DAT was not found: {generated_dat}",),
        )
    if source_ed and not os.path.exists(source_ed):
        notes.append(f"source ED was not found locally: {source_ed}")
    missing_logs = [path for path in logs if not os.path.exists(path)]
    for path in missing_logs:
        notes.append(f"log file was not found: {path}")

    reference_summary = _load_dat_output_semantic_summary(reference_dat)
    generated_summary = _load_dat_output_semantic_summary(generated_dat)
    notes.extend(f"reference DAT note: {note}" for note in reference_summary.notes)
    notes.extend(f"generated DAT note: {note}" for note in generated_summary.notes)
    comparisons = tuple(_compare_black_box_dat_summaries(reference_summary, generated_summary))
    world_model_comparisons = tuple(_compare_black_box_world_model_summaries(reference_summary, generated_summary))
    if generated_summary.status != "loaded":
        status = "captured_generated_dat_parse_failed"
        notes.append("captured generated DAT did not parse as an MM9 v66 DAT")
    elif reference_summary.status != "loaded":
        status = "captured_reference_dat_parse_failed"
        notes.append("reference DAT did not parse as an MM9 v66 DAT")
    else:
        mismatches = [item for item in comparisons if item.status == "mismatch"]
        status = "captured_and_compared" if not mismatches else "captured_with_semantic_differences"

    return BlackBoxCompilerHarnessReport(
        status=status,
        processor_path=processor,
        source_ed_path=source_ed,
        reference_dat_path=reference_dat,
        work_dir=os.path.dirname(generated_dat),
        worlds_dir=os.path.dirname(generated_dat),
        output_dat_path=generated_dat,
        log_paths=tuple(path for path in logs if os.path.exists(path)),
        captured_output=True,
        output_rewritten=True,
        reference=reference_summary,
        generated=generated_summary,
        comparisons=comparisons,
        world_model_comparisons=world_model_comparisons,
        processor_logs=tuple(_parse_processor_log(path) for path in logs if os.path.exists(path)),
        notes=tuple(_unique_text(notes)),
    )


def build_black_box_captured_output_corpus_report(
    *,
    generated_dir: str,
    worlds_dir: str,
    processor_path: str = "",
    source_roots: Sequence[str] = (),
    stems: Optional[Sequence[str]] = None,
    max_fixtures: Optional[int] = None,
    log_dir: Optional[str] = None,
) -> BlackBoxCompilerCorpusReport:
    """Compare manually captured same-stem DAT outputs against paired ED/DAT fixtures."""
    generated_root = os.path.abspath(generated_dir)
    worlds = os.path.abspath(worlds_dir)
    processor = os.path.abspath(processor_path) if processor_path else ""
    if not os.path.exists(worlds):
        return BlackBoxCompilerCorpusReport(
            status="worlds_dir_missing",
            processor_path=processor,
            worlds_dir=worlds,
            work_dir=generated_root,
            notes=(f"worlds directory was not found: {worlds}",),
        )
    if not os.path.exists(generated_root):
        return BlackBoxCompilerCorpusReport(
            status="generated_dir_missing",
            processor_path=processor,
            worlds_dir=worlds,
            work_dir=generated_root,
            notes=(f"captured generated-DAT directory was not found: {generated_root}",),
        )

    corpus = build_source_world_comparison_report(
        worlds_dir=worlds,
        source_roots=source_roots,
    )
    allowed_stems = {str(stem).upper() for stem in stems or ()}
    fixtures: List[Tuple[str, SourceWorldArtifact, SourceWorldArtifact]] = []
    for pair in corpus.pairs:
        if pair.dat is None or pair.status != "paired_v66_dat_with_source":
            continue
        if allowed_stems and pair.stem.upper() not in allowed_stems:
            continue
        ed_sources = [
            source
            for source in pair.sources
            if source.format == "ed" and source.status == "legacy_ed"
        ]
        if not ed_sources:
            continue
        fixtures.append((pair.stem, ed_sources[0], pair.dat))
    fixtures.sort(key=lambda item: item[0])
    skipped_count = 0
    if max_fixtures is not None and len(fixtures) > max(0, int(max_fixtures)):
        skipped_count = len(fixtures) - max(0, int(max_fixtures))
        fixtures = fixtures[:max(0, int(max_fixtures))]

    if not fixtures:
        return BlackBoxCompilerCorpusReport(
            status="no_paired_legacy_ed_fixtures",
            processor_path=processor,
            worlds_dir=worlds,
            work_dir=generated_root,
            skipped_count=skipped_count,
            notes=("no same-stem legacy ED/v66 DAT fixture pairs were selected",),
        )

    logs_root = os.path.abspath(log_dir) if log_dir else generated_root
    runs: List[BlackBoxCompilerCorpusRun] = []
    for stem, source_ed, reference_dat in fixtures:
        generated_dat = os.path.join(generated_root, stem + ".DAT")
        log_path = os.path.join(logs_root, stem + "_0.log")
        report = build_black_box_captured_output_report(
            processor_path=processor,
            source_ed_path=source_ed.path,
            reference_dat_path=reference_dat.path,
            generated_dat_path=generated_dat,
            log_paths=(log_path,),
        )
        runs.append(BlackBoxCompilerCorpusRun(
            stem=stem,
            source_ed_path=source_ed.path,
            reference_dat_path=reference_dat.path,
            status=report.status,
            report=report,
        ))

    matched_count = sum(1 for run in runs if _black_box_match_status(run.status))
    differing_count = sum(1 for run in runs if _black_box_difference_status(run.status))
    failed_count = len(runs) - matched_count - differing_count
    status = _black_box_corpus_status(len(runs), matched_count, failed_count)
    notes = (
        "Stage 7F captured-output corpus reports compare manually generated DAT files; they do not execute the processor.",
        "Captured outputs are research evidence only and do not make a processor save-backend compatible.",
    )
    return BlackBoxCompilerCorpusReport(
        status=status,
        processor_path=processor,
        worlds_dir=worlds,
        work_dir=generated_root,
        fixture_count=len(fixtures),
        ran_count=len(runs),
        matched_count=matched_count,
        differing_count=differing_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        runs=tuple(runs),
        notes=notes,
    )


def build_black_box_compiler_acceptance_report(
    harness: BlackBoxCompilerHarnessReport,
    *,
    manual_validation: Optional[BlackBoxCompilerManualValidation] = None,
    accepted_difference_systems: Sequence[str] = DEFAULT_BLACK_BOX_ACCEPTED_REGENERATED_SYSTEMS,
    require_manual_validation: bool = True,
) -> BlackBoxCompilerAcceptanceReport:
    """Classify a black-box compiler result as acceptable, pending, or blocked.

    Exact byte/semantic equivalence with a shipped DAT is not required for an
    old full-world compiler.  Regenerated terrain, physics, visibility, and
    render data may differ from the shipped DAT if the stable systems still
    match and the generated level has passed fresh in-game validation.
    """
    manual = manual_validation or BlackBoxCompilerManualValidation()
    accepted = tuple(str(item) for item in accepted_difference_systems)
    accepted_set = {item.lower() for item in accepted}
    comparable_statuses = {
        "compiled_and_compared",
        "compiled_with_semantic_differences",
        "captured_and_compared",
        "captured_with_semantic_differences",
    }
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = []

    if harness.status not in comparable_statuses:
        blockers.append(f"compiler result is not comparable: {harness.status}")
    if harness.generated is None or harness.generated.status != "loaded":
        blockers.append("generated DAT is not loaded/parseable")
    if harness.reference is None or harness.reference.status != "loaded":
        blockers.append("reference DAT is not loaded/parseable")
    if harness.output_preseeded and not harness.output_rewritten:
        blockers.append("preseeded output DAT was not rewritten by the processor")

    mismatched = tuple(
        item.system
        for item in harness.comparisons
        if item.status == "mismatch"
    )
    accepted_differences = tuple(
        system
        for system in mismatched
        if system.lower() in accepted_set
    )
    unaccepted = tuple(
        system
        for system in mismatched
        if system.lower() not in accepted_set
    )
    if unaccepted:
        blockers.append("unaccepted semantic differences: " + ", ".join(unaccepted))

    manual_failed = _manual_validation_failed(manual)
    manual_passed = _manual_validation_passed(manual)
    if manual_failed:
        blockers.append("manual in-game validation failed")
    elif require_manual_validation and not manual_passed:
        cautions.append("manual fresh-load in-game validation is required before acceptance")

    if any(log.warnings for log in harness.processor_logs):
        cautions.append("processor emitted warning(s); keep them in acceptance notes even if game validation passed")

    notes.append(
        "Accepted regenerated systems are treated as compiler-output drift only after parse and manual game validation."
    )
    notes.extend(str(note) for note in manual.notes)

    if blockers:
        if unaccepted:
            status = "blocked_unaccepted_differences"
        elif manual_failed:
            status = "failed_manual_validation"
        else:
            status = "blocked_harness_failure"
    elif require_manual_validation and not manual_passed:
        status = "needs_manual_validation"
    elif mismatched:
        status = "accepted_with_validated_differences"
    else:
        status = "accepted_exact_match"

    return BlackBoxCompilerAcceptanceReport(
        status=status,
        harness=harness,
        manual_validation=manual,
        accepted_difference_systems=accepted,
        mismatched_systems=mismatched,
        accepted_differences=accepted_differences,
        unaccepted_differences=unaccepted,
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def build_black_box_processor_command(
    *,
    processor_path: str,
    ed_path: str,
    project_dir: str,
    processor_prefix_args: Sequence[str] = (),
    option_template: Sequence[str] = DEFAULT_LITH21_PROCESSOR_OPTIONS,
    world_argument_template: str = "{ed_no_ext}",
) -> Tuple[str, ...]:
    """Build the command line used by the Stage 7E black-box harness."""
    context = _processor_command_context(
        ed_path=os.path.abspath(ed_path),
        project_dir=os.path.abspath(project_dir),
    )
    command: List[str] = [os.path.abspath(processor_path)]
    command.extend(str(arg).format(**context) for arg in processor_prefix_args)
    if world_argument_template:
        command.append(str(world_argument_template).format(**context))
    command.extend(str(arg).format(**context) for arg in option_template)
    return tuple(command)


def evaluate_candidate(candidate: CompilerCandidate) -> CompilerCandidate:
    """Classify *candidate* against the MM9 v66 full-world compiler target."""
    blockers = list(candidate.blockers)
    rebuilt = set(candidate.rebuilt_systems)
    if candidate.expected_dat_version is None:
        blockers.append("DAT output version is unknown")
    elif int(candidate.expected_dat_version) != 66:
        if candidate.output_scope.startswith("dat_to_"):
            blockers.append(
                f"reads DAT version {candidate.expected_dat_version}, not MM9 v66"
            )
        else:
            blockers.append(f"emits DAT version {candidate.expected_dat_version}, not MM9 v66")
    if not candidate.can_compile_full_world:
        blockers.append("does not compile complete MM9 world DAT files")
    missing = [
        system
        for system in REQUIRED_FULL_WORLD_SYSTEMS
        if system not in rebuilt
    ]
    if missing:
        blockers.append("does not rebuild required systems: " + ", ".join(missing))

    status = "compatible" if not blockers else _candidate_failure_status(candidate, blockers)
    return CompilerCandidate(
        candidate_id=candidate.candidate_id,
        name=candidate.name,
        source=candidate.source,
        input_formats=tuple(candidate.input_formats),
        output_scope=candidate.output_scope,
        expected_dat_version=candidate.expected_dat_version,
        can_compile_full_world=bool(candidate.can_compile_full_world),
        rebuilt_systems=tuple(candidate.rebuilt_systems),
        evidence=tuple(candidate.evidence),
        blockers=tuple(_unique_text(blockers)),
        status=status,
    )


def analyze_pcworldpacker_source(path: str) -> CompilerCandidate:
    """Inspect a LithTech ``PCWorldPacker.cpp`` source file as a backend candidate."""
    source = os.path.abspath(path)
    evidence: List[str] = []
    blockers: List[str] = []
    version: Optional[int] = None
    if not os.path.exists(path):
        return CompilerCandidate(
            candidate_id="lithtech_pcworldpacker",
            name="LithTech PCWorldPacker",
            source=source,
            input_formats=("lta", "ltc"),
            output_scope="full_world_dat",
            can_compile_full_world=True,
            evidence=("source file was not found",),
            blockers=("candidate source file was not found",),
            status="missing",
        )

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    match = re.search(r"#define\s+CURRENT_DAT_VERSION\s+(\d+)", text)
    if match:
        version = int(match.group(1))
        evidence.append(f"CURRENT_DAT_VERSION={version}")
    else:
        blockers.append("CURRENT_DAT_VERSION was not found")
    if "m_vWorldOffset" in text or "Added the world offset" in text:
        evidence.append("source references the later world-offset DAT layout")
    if "SaveBlindObjectData" in text:
        evidence.append("source writes blind object data")
    if "SaveFastLight_LightGrid" in text:
        evidence.append("source writes a light-grid section")
    if "SaveRenderData" in text:
        evidence.append("source writes render data")

    return CompilerCandidate(
        candidate_id="lithtech_pcworldpacker",
        name="LithTech PCWorldPacker",
        source=source,
        input_formats=("lta", "ltc"),
        output_scope="full_world_dat",
        expected_dat_version=version,
        can_compile_full_world=True,
        rebuilt_systems=(
            "world_tree",
            "Terrain*",
            "PhysicsBSP",
            "VisBSP",
            "portals",
            "polygon_lightmaps",
            "light_grid",
            "object_data",
            "render_data",
        ),
        evidence=tuple(evidence),
        blockers=tuple(blockers),
    )


def analyze_mm9_dat_to_jupiter_probe_source(path: str) -> CompilerCandidate:
    """Inspect the local v66-to-Jupiter probe as a compiler candidate."""
    source = os.path.abspath(path)
    evidence: List[str] = []
    blockers: List[str] = []
    if not os.path.exists(path):
        return CompilerCandidate(
            candidate_id="mm9_dat_to_jupiter_probe",
            name="MM9 DAT to Jupiter probe",
            source=source,
            input_formats=("dat",),
            output_scope="diagnostic_converter_probe",
            expected_dat_version=None,
            can_compile_full_world=False,
            evidence=("source file was not found",),
            blockers=("candidate source file was not found",),
            status="missing",
        )

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    if re.search(r"MM9_DAT_VERSION\s*=\s*66", text):
        evidence.append("reads MM9 DAT version 66")
    if "convertV66WorldModels" in text:
        evidence.append("converts v66 WorldBsp records for research")
    if "generatedRender" in text or "Jupiter" in text:
        evidence.append("generates Jupiter-side render scaffolding, not MM9 v66 output")
    if "ObjectDataPos" in text and "RenderDataPos" in text:
        evidence.append("documents v66 ObjectDataPos/RenderDataPos layout")
    blockers.append("diagnostic converter/probe, not a source-world-to-MM9-DAT packer")
    blockers.append("does not emit MM9 v66 full-world output")
    return CompilerCandidate(
        candidate_id="mm9_dat_to_jupiter_probe",
        name="MM9 DAT to Jupiter probe",
        source=source,
        input_formats=("dat",),
        output_scope="diagnostic_converter_probe",
        expected_dat_version=66,
        can_compile_full_world=False,
        rebuilt_systems=("Terrain*", "object_data"),
        evidence=tuple(evidence),
        blockers=tuple(blockers),
    )


def analyze_ltworldconverter_source(root: str) -> CompilerCandidate:
    """Inspect LTWorldConverter as a DAT-to-source research candidate."""
    source = os.path.abspath(root)
    readme = os.path.join(source, "README.md")
    main_path = os.path.join(source, "LTWorldConv.lpr")
    reader_path = os.path.join(source, "ltworldreader.pas")
    exporter_path = os.path.join(source, "ltaworldexporter.pas")
    ed_exporter_path = os.path.join(source, "edworldexporter.pas")
    classes_with_brushes_path = os.path.join(source, "classes_with_brushes.txt")
    evidence: List[str] = []
    blockers: List[str] = []
    if not os.path.exists(source):
        return CompilerCandidate(
            candidate_id="ltworldconverter",
            name="LTWorldConverter",
            source=source,
            input_formats=("dat",),
            output_scope="dat_to_source_research_converter",
            expected_dat_version=None,
            can_compile_full_world=False,
            evidence=("candidate source tree was not found",),
            blockers=("candidate source tree was not found",),
            status="missing",
        )

    combined_parts: List[str] = []
    for path in (
        readme,
        main_path,
        reader_path,
        exporter_path,
        ed_exporter_path,
        classes_with_brushes_path,
    ):
        if not os.path.exists(path):
            if os.path.basename(path) not in {"edworldexporter.pas", "classes_with_brushes.txt"}:
                blockers.append(f"expected source file was not found: {os.path.basename(path)}")
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                combined_parts.append(f.read())
        except OSError as exc:
            blockers.append(f"could not read {os.path.basename(path)}: {exc}")
    text = "\n".join(combined_parts)
    expected_version: Optional[int] = None
    supported_versions = [
        int(match.group(1))
        for match in re.finditer(r"Version\s+(\d+)\s+is\s+supported", text, re.IGNORECASE)
    ]
    if supported_versions:
        expected_version = supported_versions[0]
        game_hint = ""
        if expected_version == 56:
            game_hint = " (Blood 2/Shogo)"
        elif expected_version == 70:
            game_hint = " (Aliens vs Predator 2)"
        evidence.append(
            f"README/command help state DAT version {expected_version} support{game_hint}"
        )

    has_lta_export = (
        "Output LTA world file" in text
        or "ExportText" in text
        or "'.lta'" in text
        or '".lta"' in text
    )
    has_ed_export = (
        "TEDWorldExporter" in text
        or "edworldexporter" in text
        or "Convert into ED" in text
        or "'.ed'" in text
        or '".ed"' in text
    )
    ed_version_match = re.search(r"\bED_VERSION\s*=\s*(\d+)", text)
    ed_version: Optional[int] = int(ed_version_match.group(1)) if ed_version_match else None

    if has_lta_export:
        evidence.append("converts DAT input to an LTA source-world text file")
    if has_ed_export:
        evidence.append("contains a DAT-to-binary-ED export path")
    if ed_version is not None:
        evidence.append(f"binary ED exporter writes ED version {ed_version}")
    if "WorldHeader.dwObjectDataPos" in text and "WorldHeader.dwRenderDataPos" in text:
        evidence.append("reads ObjectDataPos/RenderDataPos style DAT headers")
    if "ReadWorldTree" in text:
        evidence.append("decodes world-tree layout")
    if "ReadObjects" in text:
        evidence.append("decodes world object/property records")
    if "ReadRenderData" in text:
        evidence.append("contains light/render-data reader code")
    if "RemoveWorldModel(BSP_VIS)" in text and "RemoveWorldModel(BSP_PHYSICS)" in text:
        evidence.append("exports from either PhysicsBSP or VisBSP and drops the other BSP from source output")
    if "BuildPolyBrushObject" in text or "BuildSimpleBrushObject" in text:
        evidence.append("reconstructs DEdit brush objects from compiled world-model polygons")
    if "classes_with_brushes" in text or "CLASSES_WITH_BRUSHES" in text:
        evidence.append("can attach generated brushes to configured object classes")

    blockers.append("targets DAT-to-source conversion, not source-world-to-MM9-DAT compilation")
    if expected_version is not None and expected_version != 66:
        blockers.append(
            f"input DAT reader targets DAT version {expected_version}, not MM9 DAT v66"
        )
    if has_ed_export:
        if ed_version is None:
            blockers.append("binary ED writer version was not identified")
        elif ed_version != 1249:
            blockers.append(
                f"binary ED writer targets ED_VERSION={ed_version}, not MM9 legacy ED 1249"
            )
    else:
        blockers.append("does not emit legacy MM9 binary ED")
    if has_lta_export and not has_ed_export:
        blockers.append("emits LTA source text rather than legacy MM9 binary ED")
    if "RemoveWorldModel(BSP_VIS)" in text and "RemoveWorldModel(BSP_PHYSICS)" in text:
        blockers.append("LTA export path drops either PhysicsBSP or VisBSP from source output")
    blockers.append("reconstructed brushes come from compiled polygons, not original authoring CSG")

    if has_ed_export and has_lta_export:
        output_scope = "dat_to_ed_lta_research_converter"
    elif has_ed_export:
        output_scope = "dat_to_ed_research_converter"
    elif has_lta_export:
        output_scope = "dat_to_lta_research_converter"
    else:
        output_scope = "dat_to_source_research_converter"
    return CompilerCandidate(
        candidate_id="ltworldconverter",
        name="LTWorldConverter",
        source=source,
        input_formats=("dat",),
        output_scope=output_scope,
        expected_dat_version=expected_version,
        can_compile_full_world=False,
        rebuilt_systems=("Terrain*", "object_data", "render_data"),
        evidence=tuple(_unique_text(evidence)),
        blockers=tuple(_unique_text(blockers)),
    )


def build_ltworldconverter_ed_writer_gap_report(
    *,
    ltworldconverter_root: str,
    edunpacker_root: str,
    target_version: int = 1249,
) -> LtWorldConverterEdWriterGapReport:
    """Compare LTWorldConverter's ED writer with EDUnpacker's legacy ED reader.

    The external Pascal repositories are reference material only.  This report is
    a local, static checklist for what an MM9-compatible ED writer must change
    from LTWorldConverter's Shogo/Blood 2 v1247 writer.
    """
    lt_root = os.path.abspath(ltworldconverter_root)
    ed_root = os.path.abspath(edunpacker_root)
    paths = {
        "lt_ed_writer": os.path.join(lt_root, "edworldexporter.pas"),
        "lt_world_data": os.path.join(lt_root, "ltworlddata.pas"),
        "lt_world_reader": os.path.join(lt_root, "ltworldreader.pas"),
        "lt_lta_writer": os.path.join(lt_root, "ltaworldexporter.pas"),
        "lt_main": os.path.join(lt_root, "LTWorldConv.lpr"),
        "lt_readme": os.path.join(lt_root, "README.md"),
        "lt_types": os.path.join(lt_root, "ltworldtypes.pas"),
        "ed_reader": os.path.join(ed_root, "ed.pas"),
        "ed_cli": os.path.join(ed_root, "EDUnpacker.lpr"),
    }

    texts: Dict[str, str] = {}
    blockers: List[str] = []
    evidence: List[str] = []
    notes: List[str] = []
    for key, path in paths.items():
        if not os.path.exists(path):
            if key in {"lt_ed_writer", "lt_world_data", "ed_reader"}:
                blockers.append(f"reference file was not found: {path}")
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                texts[key] = f.read()
        except OSError as exc:
            blockers.append(f"could not read reference file {path}: {exc}")

    lt_ed_text = texts.get("lt_ed_writer", "")
    lt_data_text = texts.get("lt_world_data", "")
    lt_combined_text = "\n".join(
        texts.get(key, "")
        for key in (
            "lt_ed_writer",
            "lt_world_data",
            "lt_world_reader",
            "lt_lta_writer",
            "lt_main",
            "lt_readme",
            "lt_types",
        )
    )
    ed_text = texts.get("ed_reader", "")
    writer_version = _extract_pascal_int_constant(lt_ed_text, "ED_VERSION")
    reader_versions = tuple(sorted(set(
        int(match.group(1))
        for match in re.finditer(r"\bED_VERSION_[A-Z0-9_]+\s*=\s*(\d+)", ed_text)
    )))
    if writer_version is not None:
        evidence.append(f"LTWorldConverter edworldexporter.pas writes ED_VERSION={writer_version}")
    else:
        blockers.append("LTWorldConverter ED writer version constant was not identified")
    if reader_versions:
        evidence.append(
            "EDUnpacker ed.pas accepts ED versions " + ", ".join(str(item) for item in reader_versions)
        )
    else:
        blockers.append("EDUnpacker accepted ED versions were not identified")

    supported_dat_versions = tuple(sorted(set(
        int(match.group(1))
        for match in re.finditer(r"Version\s+(\d+)\s+is\s+supported", lt_combined_text, re.IGNORECASE)
    )))
    if supported_dat_versions:
        evidence.append(
            "LTWorldConverter declares DAT input support for version(s) "
            + ", ".join(str(item) for item in supported_dat_versions)
        )
        if 66 not in supported_dat_versions:
            blockers.append(
                "LTWorldConverter input reader targets non-MM9 DAT layouts; this is a writer-port reference, not a direct MM9 DAT v66 converter"
            )
    else:
        notes.append("LTWorldConverter DAT input version support was not identified from the reference source")

    required_changes: List[str] = []
    reusable: List[str] = []
    if writer_version != target_version:
        required_changes.append(
            f"Change the binary ED header version from {writer_version or 'unknown'} to {target_version} for MM9 legacy ED output"
        )
    if target_version not in reader_versions:
        blockers.append(f"EDUnpacker reference does not advertise target ED version {target_version}")

    writer_uses_v1247_surface_projection = bool(
        "aFloat5" in lt_ed_text
        and re.search(r"WriteBuffer\s*\(\s*aFloat5\s*,\s*SizeOf\s*\(\s*LTFloat\s*\)\s*\*\s*5\s*\)", lt_ed_text)
    )
    reader_uses_v1247_projection = "fUScale" in ed_text and "fVScale" in ed_text and "ED_VERSION_SHOGO" in ed_text
    reader_uses_v1249_opq = "avOPQ" in ed_text and re.search(
        r"Read\s*\(\s*avOPQ\s*\[\s*0\s*\]\s*,\s*SizeOf\s*\(\s*TLTVector\s*\)\s*\*\s*3\s*\)",
        ed_text,
    )
    if writer_uses_v1247_surface_projection:
        evidence.append("LTWorldConverter writes ED v1247 surface projection as five floats (`aFloat5`)")
    if reader_uses_v1247_projection and reader_uses_v1249_opq:
        evidence.append("EDUnpacker reads five projection floats for v1247 but three OPQ vectors for v1249")
    if writer_uses_v1247_surface_projection and reader_uses_v1249_opq:
        required_changes.append(
            "Replace ED v1247 five-float surface projection writes with ED v1249 three-vector OPQ writes (36 bytes per surface)"
        )
    elif not reader_uses_v1249_opq and ed_text:
        blockers.append("EDUnpacker v1249 OPQ surface reader pattern was not identified")

    has_source_uv_vectors = all(
        token in lt_combined_text
        for token in ("m_fUV1", "m_fUV2", "m_fUV3", "UVData1", "UVData2", "UVData3")
    )
    if has_source_uv_vectors:
        evidence.append("LTWorldConverter already decodes DAT surface/poly UV vectors (`m_fUV1/2/3` and `UVData1/2/3`)")
        required_changes.append(
            "Source MM9 OPQ vectors from decoded surface/poly UV data instead of LTWorldConverter's hard-coded `(1, 1, 0, 0, 0)` projection tuple"
        )
    elif writer_uses_v1247_surface_projection:
        blockers.append("no decoded UV/OPQ source vectors were identified for a v1249 surface writer")

    writer_has_surface_tail = (
        "WriteDWord(0)" in lt_ed_text
        and ("g_anShade" in lt_ed_text or "Shade" in lt_ed_text)
        and (
            "pSurface.m_szTextureName" in lt_ed_text
            or "TextureNames" in lt_ed_text
        )
    )
    reader_has_surface_tail = all(
        token in ed_text
        for token in ("dwStickFlag", "strTextureName", "dwFlags", "Shade")
    )
    if writer_has_surface_tail and reader_has_surface_tail:
        reusable.append(
            "Per-surface tail order is reusable: stick flag, texture name, surface flags, RGB shade"
        )
        required_changes.append(
            "Preserve real per-surface flags and shade values where available instead of LTWorldConverter's placeholder zeros/default shade"
        )
    elif ed_text:
        blockers.append("surface tail fields could not be matched between writer and reader references")

    writer_brush_props = _extract_pascal_string_constants(
        lt_ed_text,
        r"\bBRUSH_PROP_[A-Z0-9_]+_STR\s*=\s*'([^']*)'",
    )
    mm9_brush_props = _mm9_legacy_brush_property_names()
    missing_mm9_props = tuple(name for name in mm9_brush_props if name not in writer_brush_props)
    if writer_brush_props:
        evidence.append(f"LTWorldConverter ED brush writer defines {len(writer_brush_props)} brush properties")
    if missing_mm9_props:
        required_changes.append(
            "Extend brush object properties to the MM9 26-property set, adding/reordering: "
            + ", ".join(missing_mm9_props)
        )
    else:
        reusable.append("Brush property names already cover the current MM9 surrogate set")

    if "WriteProperty" in lt_ed_text and "ReadProperty" in ed_text:
        reusable.append(
            "Generic object-property record layout is reusable: name, type, flags, byte-size, payload"
        )
    else:
        blockers.append("generic property writer/reader layout could not be matched")
    if _property_type_constants_match(texts.get("lt_types", "") + "\n" + lt_ed_text):
        evidence.append("LTWorldConverter property type codes match the legacy ED reader convention")
    else:
        notes.append("Property type constants were not fully identified; keep using mm9_editor's known type codes")

    writer_has_node_containers = all(
        token in lt_ed_text
        for token in ("WriteRootNode", "WriteBrushGroupNode", "WriteBrushNode", "WriteObjectNode")
    )
    reader_has_node_containers = "ReadNodeContainers" in ed_text and "ReadNodeItem" in ed_text
    if writer_has_node_containers and reader_has_node_containers:
        reusable.append(
            "Node container/item order is broadly reusable, but MM9 prefab/full-level grouping must be validated with legacy_ed layout scans"
        )
    elif ed_text:
        blockers.append("node container writer/reader patterns could not be matched")

    if "WriteHeader" in lt_ed_text and "ReadHeader" in ed_text:
        reusable.append(
            "Header skeleton is reusable after the version change: version, compression flag, world info string, eight dwords"
        )

    required_changes.append(
        "Keep the generated ED as source-like prefab/full-level data only; compiled DAT-derived polygons do not recover original CSG authoring semantics"
    )
    blockers.append(
        "A clean writer still needs DEDit/Processor validation because parseable ED bytes are not enough to prove compiler acceptance"
    )
    status = "missing_reference" if any("reference file was not found" in item for item in blockers) else "requires_port"
    return LtWorldConverterEdWriterGapReport(
        ltworldconverter_root=lt_root,
        edunpacker_root=ed_root,
        status=status,
        writer_version=writer_version,
        reader_versions=reader_versions,
        target_version=int(target_version),
        required_changes=tuple(_unique_text(required_changes)),
        reusable_components=tuple(_unique_text(reusable)),
        blockers=tuple(_unique_text(blockers)),
        evidence=tuple(_unique_text(evidence)),
        notes=tuple(_unique_text(notes)),
    )


def analyze_lith21_processor_executable(path: str) -> CompilerCandidate:
    """Inspect an old LithTech 2.1 ``Processor.exe`` as a black-box candidate."""
    source = os.path.abspath(path)
    if not os.path.exists(path):
        return CompilerCandidate(
            candidate_id="lith21_processor_exe",
            name="LithTech 2.1 Processor.exe",
            source=source,
            input_formats=("ed",),
            output_scope="black_box_full_world_dat_candidate",
            expected_dat_version=66,
            can_compile_full_world=True,
            rebuilt_systems=REQUIRED_FULL_WORLD_SYSTEMS,
            evidence=("processor executable was not found",),
            blockers=("candidate executable was not found",),
            status="missing",
        )

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        return CompilerCandidate(
            candidate_id="lith21_processor_exe",
            name="LithTech 2.1 Processor.exe",
            source=source,
            input_formats=("ed",),
            output_scope="black_box_full_world_dat_candidate",
            expected_dat_version=66,
            can_compile_full_world=True,
            rebuilt_systems=REQUIRED_FULL_WORLD_SYSTEMS,
            evidence=(f"could not read executable: {exc}",),
            blockers=("candidate executable could not be inspected",),
        )

    strings = _binary_ascii_strings(data)
    evidence: List[str] = []
    patterns = {
        "Processing %s.ed": "accepts ED world input",
        "Invalid ED file version": "validates legacy ED input",
        "%s.dat": "writes same-stem DAT output",
        "DAT file %s is an invalid version": "validates DAT output/input versions",
        "PhysicsBSP": "mentions PhysicsBSP generation",
        "VisBSP": "mentions VisBSP generation",
        "WorldTree nodes": "reports world-tree generation",
        "Lightmap Grid Size": "reports light-grid/lightmap settings",
        "Creating physics BSP": "creates physics BSP",
        "Creating visibility BSP": "creates visibility BSP",
        "Number of objects": "reports object output",
    }
    for needle, message in patterns.items():
        if any(needle.lower() in item.lower() for item in strings):
            evidence.append(message)
    blockers = (
        "black-box compiler candidate must pass Stage 7E golden harness before backend integration",
        "source code is unavailable; output compatibility must be proven by generated DAT diffs",
    )
    return CompilerCandidate(
        candidate_id="lith21_processor_exe",
        name="LithTech 2.1 Processor.exe",
        source=source,
        input_formats=("ed",),
        output_scope="black_box_full_world_dat_candidate",
        expected_dat_version=66,
        can_compile_full_world=True,
        rebuilt_systems=REQUIRED_FULL_WORLD_SYSTEMS,
        evidence=tuple(_unique_text(evidence)),
        blockers=blockers,
    )


def format_strategy_report(report: CompilerStrategyReport) -> str:
    lines = [
        "DAT compiler strategy",
        f"recommendation: {report.recommendation}",
        "required systems: " + ", ".join(report.required_systems),
    ]
    for note in report.notes:
        lines.append(f"note: {note}")
    for candidate in report.candidates:
        version = (
            "unknown"
            if candidate.expected_dat_version is None
            else str(candidate.expected_dat_version)
        )
        lines.append(
            f"- {candidate.candidate_id}: status={candidate.status}, "
            f"version={version}, scope={candidate.output_scope}"
        )
        if candidate.evidence:
            lines.append("  evidence: " + "; ".join(candidate.evidence))
        if candidate.blockers:
            lines.append("  blockers: " + "; ".join(candidate.blockers))
    return "\n".join(lines)


def format_ltworldconverter_ed_writer_gap_report(report: LtWorldConverterEdWriterGapReport) -> str:
    lines = [
        "LTWorldConverter ED writer v1249 port gap",
        f"status: {report.status}",
        f"ltworldconverter: {report.ltworldconverter_root}",
        f"edunpacker: {report.edunpacker_root}",
        f"writer version: {report.writer_version if report.writer_version is not None else 'unknown'}",
        "reader versions: " + (", ".join(str(item) for item in report.reader_versions) or "unknown"),
        f"target version: {report.target_version}",
    ]
    for item in report.evidence:
        lines.append(f"evidence: {item}")
    for item in report.required_changes:
        lines.append(f"required change: {item}")
    for item in report.reusable_components:
        lines.append(f"reusable: {item}")
    for item in report.blockers:
        lines.append(f"blocker: {item}")
    for item in report.notes:
        lines.append(f"note: {item}")
    return "\n".join(lines)


def format_source_world_comparison_report(report: SourceWorldComparisonReport) -> str:
    lines = [
        "DAT source-world comparison corpus",
        (
            "artifacts: "
            f"DAT={report.dat_count} (v66={report.v66_dat_count}), "
            f"ED={report.legacy_ed_count}, LTA={report.lta_count}, LTC={report.ltc_count}"
        ),
        (
            "paired fixtures: "
            f"sources={report.paired_source_count}, v66_dat_pairs={report.paired_v66_dat_count}"
        ),
        f"recommendation: {report.recommendation}",
    ]
    for note in report.notes:
        lines.append(f"note: {note}")
    for pair in report.pairs:
        source_text = ", ".join(
            f"{source.format}:{os.path.basename(source.path)}"
            for source in pair.sources
        ) or "none"
        dat_text = os.path.basename(pair.dat.path) if pair.dat is not None else "none"
        lines.append(
            f"- {pair.stem}: status={pair.status}, dat={dat_text}, sources={source_text}"
        )
    return "\n".join(lines)


def format_source_output_semantic_report(report: SourceOutputSemanticReport) -> str:
    lines = [
        "DAT source-output semantic comparison",
        (
            "fixtures: "
            f"paired={report.paired_fixture_count}, "
            f"sources_compared={report.compared_source_count}, "
            f"fully_loaded={report.comparable_source_count}"
        ),
        f"recommendation: {report.recommendation}",
    ]
    for note in report.notes:
        lines.append(f"note: {note}")
    for comparison in report.comparisons:
        lines.append(
            f"- {comparison.stem}: status={comparison.status}, "
            f"source={os.path.basename(comparison.source_path)}, "
            f"dat={os.path.basename(comparison.dat_path)}"
        )
        lines.append(
            "  source geometry: "
            f"models={comparison.source.model_count}, "
            f"points={comparison.source.point_count}, "
            f"polygons={comparison.source.polygon_count}, "
            f"materials={comparison.source.material_count}, "
            f"status={comparison.source.status}"
        )
        lines.append(
            "  compiled DAT: "
            f"models={comparison.dat.world_model_count}, "
            f"terrain_models={comparison.dat.terrain_model_count}, "
            f"terrain_polygons={comparison.dat.terrain_polygon_count}, "
            f"objects={comparison.dat.object_count}, "
            f"render_data={comparison.dat.render_data_size}, "
            f"status={comparison.dat.status}"
        )
        for system in comparison.systems:
            detail = f"  {system.system}: {system.status}"
            if system.source_detail:
                detail += f"; source={system.source_detail}"
            if system.dat_detail:
                detail += f"; dat={system.dat_detail}"
            if system.notes:
                detail += "; " + "; ".join(system.notes)
            lines.append(detail)
        for note in comparison.notes:
            lines.append(f"  note: {note}")
    return "\n".join(lines)


def format_black_box_compiler_harness_report(report: BlackBoxCompilerHarnessReport) -> str:
    lines = [
        "DAT black-box compiler harness",
        f"status: {report.status}",
        f"processor: {report.processor_path}",
        f"source ED: {report.source_ed_path}",
    ]
    if report.reference_dat_path:
        lines.append(f"reference DAT: {report.reference_dat_path}")
    if report.output_dat_path:
        lines.append(f"output DAT: {report.output_dat_path}")
    if report.processor_project_dir:
        lines.append(f"processor project dir: {report.processor_project_dir}")
    if report.command:
        lines.append("command: " + _command_text(report.command))
    if report.returncode is not None:
        lines.append(f"return code: {report.returncode}")
    if report.elapsed_seconds:
        lines.append(f"elapsed seconds: {report.elapsed_seconds:.3f}")
    if report.stdout_path:
        lines.append(f"stdout: {report.stdout_path}")
    if report.stderr_path:
        lines.append(f"stderr: {report.stderr_path}")
    if report.captured_output:
        lines.append("captured output: true")
    lines.append(
        f"output preseeded: {report.output_preseeded}, output rewritten: {report.output_rewritten}"
    )
    for path in report.log_paths:
        lines.append(f"log: {path}")
    for log in report.processor_logs:
        lines.append(
            "processor log summary: "
            f"status={log.status}, processing={log.processing_path or 'unknown'}, "
            f"world_tree_nodes={log.world_tree_nodes}, world_tree_depth={log.world_tree_depth}, "
            f"tree_depth={log.tree_depth}, runtime_minutes={log.runtime_minutes}, "
            f"output_polies={log.output_polygon_count}, output_vertices={log.output_vertex_count}, "
            f"objects={log.object_count}, problem_brushes={log.problem_brush_count}, "
            f"btw_splits={log.btw_poly_split_count}, model_lines={len(log.model_polygon_counts)}"
        )
        for warning, count in sorted(log.warning_counts.items()):
            lines.append(f"  processor warning count: {count} x {warning}")
        for warning in log.warnings:
            lines.append(f"  processor warning: {warning}")
    if report.reference is not None:
        lines.append(
            "reference summary: "
            f"status={report.reference.status}, version={report.reference.version}, "
            f"models={report.reference.world_model_count}, "
            f"terrain_polygons={report.reference.terrain_polygon_count}, "
            f"objects={report.reference.object_count}, "
            f"render_data={report.reference.render_data_size}"
        )
    if report.generated is not None:
        lines.append(
            "generated summary: "
            f"status={report.generated.status}, version={report.generated.version}, "
            f"models={report.generated.world_model_count}, "
            f"terrain_polygons={report.generated.terrain_polygon_count}, "
            f"objects={report.generated.object_count}, "
            f"render_data={report.generated.render_data_size}"
        )
    for item in report.comparisons:
        lines.append(
            f"- {item.system}: status={item.status}, "
            f"reference={item.reference_detail}, generated={item.generated_detail}"
        )
        for note in item.notes:
            lines.append(f"  note: {note}")
    model_diffs = [
        item
        for item in report.world_model_comparisons
        if item.status != "match"
    ]
    if model_diffs:
        lines.append(f"world model diffs: {len(model_diffs)}")
        for item in model_diffs[:20]:
            lines.append(
                f"- model #{item.index} {item.name}: status={item.status}, "
                f"reference={item.reference_detail}, generated={item.generated_detail}"
            )
        if len(model_diffs) > 20:
            lines.append(f"- ... {len(model_diffs) - 20} more world model diff(s)")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_black_box_surrogate_compiler_harness_report(
    report: SurrogateBlackBoxCompilerHarnessReport,
) -> str:
    lines = [
        "DAT surrogate black-box compiler harness",
        f"status: {report.status}",
        f"processor: {report.processor_path}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.reference_dat_path:
        lines.append(f"reference DAT: {report.reference_dat_path}")
    if report.generated_ed_path:
        lines.append(f"generated surrogate ED: {report.generated_ed_path}")
    if report.work_dir:
        lines.append(f"work dir: {report.work_dir}")
    if report.selected_model_names:
        lines.append("selected models: " + ", ".join(report.selected_model_names))
    lines.append(
        "surrogate: "
        f"status={report.surrogate_status or 'unknown'}, "
        f"models={report.surrogate_model_count}, "
        f"points={report.surrogate_point_count}, "
        f"polygons={report.surrogate_polygon_count}, "
        f"bytes={report.surrogate_byte_count}, "
        f"wrapper={report.surrogate_wrapper_kind or 'none'}, "
        f"blocks={report.surrogate_wrapper_block_count}"
    )
    if report.harness is not None:
        lines.append(
            "black-box result: "
            f"status={report.harness.status}, returncode={report.harness.returncode}, "
            f"output={report.harness.output_dat_path or 'none'}"
        )
        if report.harness.command:
            lines.append("command: " + _command_text(report.harness.command))
        lines.append(
            f"output preseeded: {report.harness.output_preseeded}, "
            f"output rewritten: {report.harness.output_rewritten}"
        )
        if report.harness.output_preseeded and not report.harness.output_rewritten:
            lines.append(
                "black-box comparison note: output DAT was preseeded and not rewritten; "
                "matching comparisons reflect the seed DAT, not generated compiler output"
            )
        if report.harness.stdout_path:
            lines.append(f"stdout: {report.harness.stdout_path}")
        if report.harness.stderr_path:
            lines.append(f"stderr: {report.harness.stderr_path}")
        for path in report.harness.log_paths:
            lines.append(f"log: {path}")
        for log in report.harness.processor_logs:
            lines.append(
                "processor log summary: "
                f"status={log.status}, processing={log.processing_path or 'unknown'}, "
                f"world_tree_nodes={log.world_tree_nodes}, world_tree_depth={log.world_tree_depth}, "
                f"tree_depth={log.tree_depth}, runtime_minutes={log.runtime_minutes}, "
                f"output_polies={log.output_polygon_count}, output_vertices={log.output_vertex_count}, "
                f"objects={log.object_count}, problem_brushes={log.problem_brush_count}, "
                f"btw_splits={log.btw_poly_split_count}, model_lines={len(log.model_polygon_counts)}"
            )
            for warning, count in sorted(log.warning_counts.items()):
                lines.append(f"  processor warning count: {count} x {warning}")
            for warning in log.warnings:
                lines.append(f"  processor warning: {warning}")
        for item in report.harness.comparisons:
            lines.append(
                f"- {item.system}: status={item.status}, "
                f"reference={item.reference_detail}, generated={item.generated_detail}"
            )
        for note in report.harness.notes:
            lines.append(f"  harness note: {note}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_prefab_surrogate_acceptance_report(
    report: PrefabSurrogateAcceptanceReport,
) -> str:
    lines = [
        "DAT surrogate prefab acceptance harness",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.generated_ed_path:
        lines.append(f"generated prefab ED: {report.generated_ed_path}")
    if report.work_dir:
        lines.append(f"work dir: {report.work_dir}")
    if report.prefab_install_path:
        lines.append(f"suggested prefab install path: {report.prefab_install_path}")
    if report.selected_model_names:
        lines.append("selected models: " + ", ".join(report.selected_model_names))
    lines.append(
        "surrogate: "
        f"status={report.surrogate_status or 'unknown'}, "
        f"models={report.surrogate_model_count}, "
        f"points={report.surrogate_point_count}, "
        f"polygons={report.surrogate_polygon_count}, "
        f"objects={report.surrogate_object_count}, "
        f"properties={report.surrogate_object_property_count}, "
        f"bytes={report.surrogate_byte_count}"
    )
    lines.append(
        "generated parse: "
        f"brushes={report.generated_brush_count}, "
        f"polygons={report.generated_polygon_count}, "
        f"objects={report.generated_object_count}, "
        f"classes={_class_counts_text(report.generated_object_class_counts)}"
    )
    if report.reference_prefab_path:
        lines.append(f"reference prefab: {report.reference_prefab_path}")
        lines.append(
            "reference parse: "
            f"brushes={report.reference_brush_count}, "
            f"polygons={report.reference_polygon_count}, "
            f"objects={report.reference_object_count}, "
            f"classes={_class_counts_text(report.reference_object_class_counts)}"
        )
    for index, step in enumerate(report.manual_steps, start=1):
        lines.append(f"manual step {index}: {step}")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_prefab_surrogate_acceptance_corpus_report(
    report: PrefabSurrogateAcceptanceCorpusReport,
) -> str:
    lines = [
        "DAT surrogate prefab acceptance corpus",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.work_dir:
        lines.append(f"work dir: {report.work_dir}")
    if report.prefab_install_dir:
        lines.append(f"prefab install dir: {report.prefab_install_dir}")
    lines.append(
        "candidates: "
        f"total={report.candidate_count}, generated={report.generated_count}, "
        f"ready={report.ready_count}, failed={report.failed_count}, "
        f"skipped={report.skipped_count}"
    )
    for item in report.candidates:
        detail = (
            f"- {item.model_name}: status={item.status}, "
            f"points={item.point_count}, polygons={item.polygon_count}, "
            f"textures={item.texture_count}"
        )
        if item.generated_ed_path:
            detail += f", output={item.generated_ed_path}"
        if item.prefab_install_path:
            detail += f", install={item.prefab_install_path}"
        lines.append(detail)
        for note in item.notes:
            lines.append(f"  note: {note}")
    for index, step in enumerate(report.manual_steps, start=1):
        lines.append(f"manual step {index}: {step}")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_prefab_surrogate_composite_acceptance_report(
    report: PrefabSurrogateCompositeAcceptanceReport,
) -> str:
    lines = [
        "DAT surrogate composite prefab acceptance",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.generated_ed_path:
        lines.append(f"generated composite prefab ED: {report.generated_ed_path}")
    if report.work_dir:
        lines.append(f"work dir: {report.work_dir}")
    if report.prefab_install_path:
        lines.append(f"suggested prefab install path: {report.prefab_install_path}")
    if report.hierarchy_kind or report.group_name:
        hierarchy = report.hierarchy_kind or "direct_root"
        detail = f"hierarchy: {hierarchy}"
        if report.group_name:
            detail += f", group={report.group_name}"
        lines.append(detail)
    if report.selected_model_names:
        lines.append("selected models: " + ", ".join(report.selected_model_names))
    lines.append(
        "composite: "
        f"models={report.model_count}, points={report.point_count}, "
        f"polygons={report.polygon_count}, objects={report.object_count}, "
        f"classes={_class_counts_text(report.generated_object_class_counts)}, "
        f"bytes={report.generated_byte_count}"
    )
    for item in report.models:
        lines.append(
            f"- {item.name}: points={item.point_count}, polygons={item.polygon_count}, "
            f"textures={item.texture_count}, bounds={_vec3_text(item.bounds_min)}.."
            f"{_vec3_text(item.bounds_max)}, center={_vec3_text(item.center)}"
        )
        for note in item.notes:
            lines.append(f"  note: {note}")
    for index, step in enumerate(report.manual_steps, start=1):
        lines.append(f"manual step {index}: {step}")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_prefab_surrogate_composite_acceptance_corpus_report(
    report: PrefabSurrogateCompositeCorpusReport,
) -> str:
    hierarchy = report.hierarchy_kind or "direct_root"
    header = (
        "DAT surrogate named-group composite prefab corpus"
        if hierarchy == "named_group"
        else "DAT surrogate direct-root composite prefab corpus"
    )
    lines = [
        header,
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.work_dir:
        lines.append(f"work dir: {report.work_dir}")
    if report.prefab_install_dir:
        lines.append(f"prefab install dir: {report.prefab_install_dir}")
    lines.append(f"hierarchy: {hierarchy}")
    lines.append(
        "groups: "
        f"total={report.group_count}, generated={report.generated_count}, "
        f"ready={report.ready_count}, failed={report.failed_count}, "
        f"skipped={report.skipped_count}"
    )
    for item in report.candidates:
        detail = (
            f"- {item.group_name}: status={item.status}, "
            f"hierarchy={item.hierarchy_kind}, "
            f"models={item.model_count}, points={item.point_count}, "
            f"polygons={item.polygon_count}, model_names={','.join(item.model_names)}"
        )
        if item.generated_ed_path:
            detail += f", output={item.generated_ed_path}"
        if item.prefab_install_path:
            detail += f", install={item.prefab_install_path}"
        lines.append(detail)
        for note in item.notes:
            lines.append(f"  note: {note}")
    for index, step in enumerate(report.manual_steps, start=1):
        lines.append(f"manual step {index}: {step}")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_prefab_surrogate_pack_report(report: PrefabSurrogatePackReport) -> str:
    lines = [
        "DAT surrogate named-group prefab pack",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.work_dir:
        lines.append(f"work dir: {report.work_dir}")
    if report.staging_prefab_dir:
        lines.append(f"staging prefab dir: {report.staging_prefab_dir}")
    if report.manifest_path:
        lines.append(f"manifest: {report.manifest_path}")
    lines.append(f"hierarchy: {report.hierarchy_kind}")
    lines.append(
        "entries: "
        f"total={report.entry_count}, ready={report.ready_count}, "
        f"staged={report.staged_count}, failed={report.failed_count}, "
        f"skipped={report.skipped_count}"
    )
    for item in report.entries:
        detail = (
            f"- {item.group_name}: status={item.status}, models={item.model_count}, "
            f"points={item.point_count}, polygons={item.polygon_count}, "
            f"model_names={','.join(item.model_names)}"
        )
        if item.staged_prefab_path:
            detail += f", staged={item.staged_prefab_path}"
        elif item.generated_ed_path:
            detail += f", generated={item.generated_ed_path}"
        lines.append(detail)
        for note in item.notes:
            lines.append(f"  note: {note}")
    for index, step in enumerate(report.manual_steps, start=1):
        lines.append(f"manual step {index}: {step}")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_full_world_skeleton_acceptance_report(
    report: FullWorldSkeletonAcceptanceReport,
) -> str:
    lines = [
        "DAT surrogate full-world skeleton acceptance",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.generated_ed_path:
        lines.append(f"generated world ED: {report.generated_ed_path}")
    if report.work_dir:
        lines.append(f"work dir: {report.work_dir}")
    if report.world_install_path:
        lines.append(f"suggested WORLDS path: {report.world_install_path}")
    if report.group_name:
        lines.append(f"group: {report.group_name}")
    if report.include_validation_floor:
        lines.append("validation floor: included")
    if report.include_terrain_support_patch:
        lines.append("terrain support patch: included")
    if report.include_physics_shell_patch:
        lines.append("PhysicsBSP shell patch: included")
    if report.max_processor_brushes or report.max_processor_polygons:
        limits = []
        if report.max_processor_brushes:
            limits.append(f"brushes<={report.max_processor_brushes}")
        if report.max_processor_polygons:
            limits.append(f"polygons<={report.max_processor_polygons}")
        lines.append("Processor budget: " + ", ".join(limits))
    if report.selected_model_names:
        lines.append("selected models: " + ", ".join(report.selected_model_names))
    lines.append(
        "skeleton: "
        f"models={report.model_count}, points={report.point_count}, "
        f"polygons={report.polygon_count}, objects={report.object_count}, "
        f"properties={report.object_property_count}, "
        f"classes={_class_counts_text(report.generated_object_class_counts)}, "
        f"bytes={report.generated_byte_count}"
    )
    if report.wrapper_kind:
        lines.append(
            "wrapper: "
            f"{report.wrapper_kind}, blocks={report.wrapper_block_count}, "
            f"node_bytes={report.node_hierarchy_byte_count}"
        )
    if report.terrain_cutout_coverage is not None:
        cutouts = report.terrain_cutout_coverage
        detail = (
            "terrain cutout coverage: "
            f"status={cutouts.status}, candidates={cutouts.candidate_count}, "
            f"covered={cutouts.covered_cutout_count}, partial={cutouts.partial_cutout_count}, "
            f"present={cutouts.terrain_present_count}"
        )
        if report.terrain_cutout_coverage_manifest_path:
            detail += f", manifest={report.terrain_cutout_coverage_manifest_path}"
        lines.append(detail)
    if report.terrain_support_source_coverage is not None:
        coverage = report.terrain_support_source_coverage
        detail = (
            "terrain support source coverage: "
            f"status={coverage.status}, samples={coverage.sample_count}, "
            f"missing={coverage.missing_sample_count}, gap_polygons={coverage.missing_polygon_count}"
        )
        if report.terrain_support_source_coverage_manifest_path:
            detail += f", manifest={report.terrain_support_source_coverage_manifest_path}"
        lines.append(detail)
    for item in report.models:
        lines.append(
            f"- {item.name}: points={item.point_count}, polygons={item.polygon_count}, "
            f"textures={item.texture_count}, bounds={_vec3_text(item.bounds_min)}.."
            f"{_vec3_text(item.bounds_max)}, center={_vec3_text(item.center)}"
        )
        for note in item.notes:
            lines.append(f"  note: {note}")
    for index, step in enumerate(report.manual_steps, start=1):
        lines.append(f"manual step {index}: {step}")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_full_world_skeleton_compiled_validation_report(
    report: FullWorldSkeletonCompiledValidationReport,
) -> str:
    lines = [
        "DAT surrogate full-world compiled validation",
        f"status: {report.status}",
        f"generated ED: {report.generated_ed_path}",
        f"compiled DAT: {report.compiled_dat_path}",
    ]
    if report.start_point is not None:
        lines.append(
            "StartPoint: "
            f"pos={_vec3_text(report.start_point)}, "
            f"MovePlayerToFloor={report.move_player_to_floor}"
        )
    if report.physics_floor_y is not None:
        lines.append(
            "PhysicsBSP floor probe: "
            f"floor_y={report.physics_floor_y:.2f}, "
            f"drop={report.physics_floor_drop:.2f}, "
            f"max_drop={report.max_start_floor_drop:.2f}"
        )
    else:
        lines.append("PhysicsBSP floor probe: no floor hit")
    if report.dat is not None:
        lines.append(
            "compiled DAT summary: "
            f"status={report.dat.status}, version={report.dat.version}, "
            f"models={report.dat.world_model_count}, objects={report.dat.object_count}, "
            f"PhysicsBSP={report.dat.physics_point_count}/{report.dat.physics_polygon_count}, "
            f"VisBSP={report.dat.vis_bsp_present}, world_tree={report.dat.world_tree_node_count}/"
            f"{report.dat.world_tree_leaf_count}, portals={report.dat.portal_reference_count}, "
            f"render_data={report.dat.render_data_size}"
        )
    for path in report.processor_log_paths:
        lines.append(f"log: {path}")
    for log in report.processor_logs:
        lines.append(
            "processor log summary: "
            f"status={log.status}, processing={log.processing_path or 'unknown'}, "
            f"tree_depth={log.tree_depth}, runtime_minutes={log.runtime_minutes}, "
            f"input_polies={log.input_polygon_count}, input_vertices={log.input_vertex_count}, "
            f"output_polies={log.output_polygon_count}, output_vertices={log.output_vertex_count}, "
            f"Physics/Vis={','.join(f'{name}:{count}' for name, count in log.model_polygon_counts) or 'none'}, "
            f"problem_brushes={log.problem_brush_count}, btw_splits={log.btw_poly_split_count}, "
            f"joined_left={log.joined_polygon_count}, joined_removed={log.joined_removed_polygon_count}, "
            f"unseen_removed={log.unseen_removed_polygon_count}, t_verts={log.t_junction_vertex_count}"
        )
        for warning, count in sorted(log.warning_counts.items()):
            lines.append(f"  processor warning count: {count} x {warning}")
        for warning in log.warnings:
            lines.append(f"  processor warning: {warning}")
    lines.append(
        "manual validation: "
        f"status={report.manual_validation.status}, "
        f"fresh_load={report.manual_validation.fresh_load}, "
        f"visuals_ok={report.manual_validation.visuals_ok}, "
        f"collision_ok={report.manual_validation.collision_ok}, "
        f"tested_at={report.manual_validation.tested_at or 'unknown'}"
    )
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def build_dat_to_ed_selection_report(
    *,
    source_dat_path: str,
    requested_model_names: Sequence[str] = (),
    selected_model_names: Sequence[str] = (),
    terrain_support_model_name: str = "Terrain0",
    include_terrain_support_patch: bool = False,
    physics_shell_model_name: str = "PhysicsBSP",
    include_physics_shell_patch: bool = False,
    include_skyboxes: bool = False,
    max_models: int = 32,
    max_model_points: int = 2048,
    max_model_polygons: int = 2048,
    max_total_points: int = 4096,
    max_total_polygons: int = 4096,
) -> DatToEdSelectionReport:
    """Explain which DAT world models a generated ED run selected or skipped."""
    from core import bsp

    source_dat = os.path.abspath(source_dat_path)
    requested = tuple(str(name).strip() for name in requested_model_names if str(name).strip())
    selected = tuple(str(name).strip() for name in selected_model_names if str(name).strip())
    limits = {
        "max_models": int(max_models),
        "max_model_points": int(max_model_points),
        "max_model_polygons": int(max_model_polygons),
        "max_total_points": int(max_total_points),
        "max_total_polygons": int(max_total_polygons),
        "include_skyboxes": bool(include_skyboxes),
    }
    if not os.path.exists(source_dat):
        return DatToEdSelectionReport(
            status="source_dat_missing",
            source_dat_path=source_dat,
            requested_model_names=requested,
            selected_model_names=selected,
            terrain_support_model_name=terrain_support_model_name,
            include_terrain_support_patch=bool(include_terrain_support_patch),
            physics_shell_model_name=physics_shell_model_name,
            include_physics_shell_patch=bool(include_physics_shell_patch),
            limits=limits,
            blockers=(f"source DAT was not found: {source_dat}",),
        )
    try:
        with open(source_dat, "rb") as f:
            parsed = bsp.parse(f.read())
    except Exception as exc:
        return DatToEdSelectionReport(
            status="dat_parse_failed",
            source_dat_path=source_dat,
            requested_model_names=requested,
            selected_model_names=selected,
            terrain_support_model_name=terrain_support_model_name,
            include_terrain_support_patch=bool(include_terrain_support_patch),
            physics_shell_model_name=physics_shell_model_name,
            include_physics_shell_patch=bool(include_physics_shell_patch),
            limits=limits,
            blockers=(f"DAT parse failed: {exc}",),
        )

    requested_set = {name.lower() for name in requested}
    selected_set = {name.lower() for name in selected}
    terrain_support_key = str(terrain_support_model_name or "Terrain0").lower()
    physics_shell_key = str(physics_shell_model_name or "PhysicsBSP").lower()
    model_reports: List[DatToEdSelectionModelReport] = []
    status_counts: Dict[str, int] = {}
    helper_exclusions_by_role: Dict[str, Dict[str, int]] = {}
    total_points = 0
    total_polygons = 0
    selected_points = 0
    selected_polygons = 0
    for index, model in enumerate(getattr(parsed, "world_models", ()) or ()):
        model_report = _dat_to_ed_selection_model_report(
            index,
            model,
            requested_set=requested_set,
            selected_set=selected_set,
            terrain_support_key=terrain_support_key,
            include_terrain_support_patch=bool(include_terrain_support_patch),
            physics_shell_key=physics_shell_key,
            include_physics_shell_patch=bool(include_physics_shell_patch),
            include_skyboxes=bool(include_skyboxes),
            max_model_points=int(max_model_points),
            max_model_polygons=int(max_model_polygons),
        )
        model_reports.append(model_report)
        status_counts[model_report.status] = status_counts.get(model_report.status, 0) + 1
        total_points += model_report.point_count
        total_polygons += model_report.polygon_count
        if model_report.status == "selected":
            selected_points += model_report.point_count
            selected_polygons += model_report.polygon_count
        if model_report.status == "excluded_helper_texture":
            _accumulate_helper_exclusion_roles(
                helper_exclusions_by_role,
                model_report.helper_roles,
            )

    selected_count = status_counts.get("selected", 0)
    terrain_support_count = status_counts.get("terrain_support_source", 0)
    physics_shell_count = status_counts.get("physics_shell_source", 0)
    excluded_count = len(model_reports) - selected_count - terrain_support_count - physics_shell_count
    cautions: List[str] = []
    if selected_count > int(max_models):
        cautions.append(
            f"selected model count {selected_count} exceeds requested max_models {int(max_models)}"
        )
    if selected_points > int(max_total_points):
        cautions.append(
            f"selected point count {selected_points} exceeds max_total_points {int(max_total_points)}"
        )
    if selected_polygons > int(max_total_polygons):
        cautions.append(
            f"selected polygon count {selected_polygons} exceeds max_total_polygons {int(max_total_polygons)}"
        )
    notes = [
        "Statuses explain generator selection, not DEDit or Processor acceptance.",
        "Terrain support source means the model feeds generated terrain support instead of direct Brush output.",
        "Physics shell source means the model feeds generated static-shell slab brushes instead of direct Brush output.",
    ]
    return DatToEdSelectionReport(
        status="selection_report_built",
        source_dat_path=source_dat,
        requested_model_names=requested,
        selected_model_names=selected,
        terrain_model_names=tuple(terrain_semantics.terrain_model_names(parsed)),
        terrain_support_model_name=terrain_support_model_name,
        include_terrain_support_patch=bool(include_terrain_support_patch),
        physics_shell_model_name=physics_shell_model_name,
        include_physics_shell_patch=bool(include_physics_shell_patch),
        total_model_count=len(model_reports),
        selected_model_count=selected_count,
        terrain_support_source_count=terrain_support_count,
        physics_shell_source_count=physics_shell_count,
        excluded_model_count=excluded_count,
        total_point_count=total_points,
        total_polygon_count=total_polygons,
        selected_point_count=selected_points,
        selected_polygon_count=selected_polygons,
        status_counts=status_counts,
        helper_only_exclusions_by_role=_ordered_helper_exclusion_role_summary(
            helper_exclusions_by_role
        ),
        limits=limits,
        models=tuple(model_reports),
        cautions=tuple(cautions),
        notes=tuple(notes),
    )


def write_dat_to_ed_selection_report(
    report: DatToEdSelectionReport,
    output_path: str,
    *,
    acceptance_report: Optional[FullWorldSkeletonAcceptanceReport] = None,
) -> str:
    """Write a DAT -> ED selection/coverage explanation report as JSON."""
    manifest = _dat_to_ed_selection_manifest(report, acceptance_report=acceptance_report)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    return output_path


def _dat_to_ed_selection_model_report(
    index: int,
    model: object,
    *,
    requested_set: set,
    selected_set: set,
    terrain_support_key: str,
    include_terrain_support_patch: bool,
    physics_shell_key: str,
    include_physics_shell_patch: bool,
    include_skyboxes: bool,
    max_model_points: int,
    max_model_polygons: int,
) -> DatToEdSelectionModelReport:
    summary = _composite_model_summary(model)
    name_key = summary.name.lower()
    reasons: List[str] = []
    helper_roles: Dict[str, int] = {}
    try:
        is_skybox = bool(getattr(model, "is_skybox", lambda: False)())
    except Exception:
        is_skybox = False

    if name_key in selected_set:
        status = "selected"
        reasons.append("selected for generated ED Brush output")
    elif include_terrain_support_patch and name_key == terrain_support_key:
        status = "terrain_support_source"
        reasons.append("used to generate Terrain* support/coverage brushes")
    elif include_physics_shell_patch and name_key == physics_shell_key:
        status = "physics_shell_source"
        reasons.append("used to generate PhysicsBSP static-shell slab brushes")
    elif terrain_semantics.is_terrain_model(model):
        status = "excluded_system"
        if include_terrain_support_patch:
            reasons.append("Terrain* is handled by support generation, not direct Brush selection")
        else:
            reasons.append("Terrain* is excluded from default direct Brush selection")
    elif terrain_semantics.is_physics_bsp_model(model):
        status = "excluded_system"
        if include_physics_shell_patch:
            reasons.append("PhysicsBSP shell generation is configured for a different source model")
        else:
            reasons.append("PhysicsBSP is compiler/engine collision data")
    elif terrain_semantics.is_vis_bsp_model(model):
        status = "excluded_system"
        reasons.append("VisBSP is compiler/engine visibility data")
    elif is_skybox and not include_skyboxes:
        status = "excluded_skybox"
        reasons.append("skybox/system render model skipped")
    elif summary.point_count <= 0 or summary.polygon_count <= 0:
        status = "excluded_empty"
        reasons.append("model has no writable points or polygons")
    elif terrain_semantics.model_has_only_helper_textures(model):
        status = "excluded_helper_texture"
        helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
        if helper_roles:
            detail = ", ".join(f"{role}={count}" for role, count in sorted(helper_roles.items()))
            reasons.append(f"model uses only helper/non-render textures ({detail})")
        else:
            reasons.append("model uses only helper/non-render textures")
    elif summary.point_count > int(max_model_points) or summary.polygon_count > int(max_model_polygons):
        status = "excluded_oversized"
        if summary.point_count > int(max_model_points):
            reasons.append(
                f"point count {summary.point_count} exceeds per-model limit {int(max_model_points)}"
            )
        if summary.polygon_count > int(max_model_polygons):
            reasons.append(
                f"polygon count {summary.polygon_count} exceeds per-model limit {int(max_model_polygons)}"
            )
    elif requested_set and name_key not in requested_set:
        status = "excluded_not_requested"
        reasons.append("not requested by the generated ED command")
    elif requested_set and name_key in requested_set:
        status = "excluded_filtered"
        reasons.append("requested, but filtered out before ED generation")
    else:
        status = "excluded_unselected"
        reasons.append("not selected for this generated ED run")

    return DatToEdSelectionModelReport(
        index=index,
        name=summary.name,
        status=status,
        point_count=summary.point_count,
        polygon_count=summary.polygon_count,
        texture_count=summary.texture_count,
        bounds_min=summary.bounds_min,
        bounds_max=summary.bounds_max,
        center=summary.center,
        helper_roles=helper_roles,
        reasons=tuple(reasons + list(summary.notes)),
    )


def _accumulate_helper_exclusion_roles(
    target: Dict[str, Dict[str, int]],
    helper_roles: Dict[str, int],
) -> None:
    for role, polygon_count in sorted((helper_roles or {}).items()):
        text = str(role or "").strip() or "unknown"
        entry = target.setdefault(text, {"model_count": 0, "polygon_count": 0})
        entry["model_count"] = int(entry.get("model_count", 0)) + 1
        entry["polygon_count"] = int(entry.get("polygon_count", 0)) + int(polygon_count)


def _ordered_helper_exclusion_role_summary(
    values: Dict[str, Dict[str, int]],
) -> Dict[str, Dict[str, int]]:
    preferred_roles = (
        "aiRail",
        "collision",
        "skyVisibility",
        "trigger",
        "sound",
        "water",
    )
    ordered: Dict[str, Dict[str, int]] = {}
    for role in preferred_roles:
        entry = values.get(role, {})
        ordered[role] = {
            "model_count": int(entry.get("model_count", 0)),
            "polygon_count": int(entry.get("polygon_count", 0)),
        }
    for role in sorted(key for key in values if key not in ordered):
        entry = values[role]
        ordered[role] = {
            "model_count": int(entry.get("model_count", 0)),
            "polygon_count": int(entry.get("polygon_count", 0)),
        }
    return ordered


def _dat_to_ed_selection_manifest(
    report: DatToEdSelectionReport,
    *,
    acceptance_report: Optional[FullWorldSkeletonAcceptanceReport] = None,
) -> Dict[str, object]:
    diagnostics: Dict[str, object] = {}
    if acceptance_report is not None:
        diagnostics = {
            "terrain_cutout_coverage_manifest_path": acceptance_report.terrain_cutout_coverage_manifest_path,
            "terrain_support_source_coverage_manifest_path": acceptance_report.terrain_support_source_coverage_manifest_path,
            "terrain_cutout_coverage": _full_world_manifest_cutout_summary(
                acceptance_report.terrain_cutout_coverage
            ),
            "terrain_support_source_coverage": _full_world_manifest_source_coverage_summary(
                acceptance_report.terrain_support_source_coverage
            ),
        }
    return {
        "kind": "mm9_dat_to_ed_selection_report",
        "schema_version": 1,
        "status": report.status,
        "source_dat_path": report.source_dat_path,
        "requested_model_names": list(report.requested_model_names),
        "selected_model_names": list(report.selected_model_names),
        "terrain_model_names": list(report.terrain_model_names),
        "terrain_support_model_name": report.terrain_support_model_name,
        "include_terrain_support_patch": report.include_terrain_support_patch,
        "physics_shell_model_name": report.physics_shell_model_name,
        "include_physics_shell_patch": report.include_physics_shell_patch,
        "summary": {
            "total_model_count": report.total_model_count,
            "selected_model_count": report.selected_model_count,
            "terrain_support_source_count": report.terrain_support_source_count,
            "physics_shell_source_count": report.physics_shell_source_count,
            "excluded_model_count": report.excluded_model_count,
            "total_point_count": report.total_point_count,
            "total_polygon_count": report.total_polygon_count,
            "selected_point_count": report.selected_point_count,
            "selected_polygon_count": report.selected_polygon_count,
            "status_counts": dict(report.status_counts),
            "helper_only_exclusions_by_role": {
                role: dict(values)
                for role, values in report.helper_only_exclusions_by_role.items()
            },
            "limits": dict(report.limits),
        },
        "models": [
            {
                "index": item.index,
                "name": item.name,
                "status": item.status,
                "point_count": item.point_count,
                "polygon_count": item.polygon_count,
                "texture_count": item.texture_count,
                "bounds_min": list(item.bounds_min),
                "bounds_max": list(item.bounds_max),
                "center": list(item.center),
                "helper_roles": dict(item.helper_roles),
                "reasons": list(item.reasons),
            }
            for item in report.models
        ],
        "diagnostics": diagnostics,
        "blockers": list(report.blockers),
        "cautions": list(report.cautions),
        "notes": list(report.notes),
    }


def build_full_world_skeleton_acceptance_manifest(
    report: FullWorldSkeletonAcceptanceReport,
    *,
    original_source: str = "",
    staged_source_dat_path: str = "",
    text_report_path: str = "",
    selection_report_path: str = "",
    dedit_saved_ed_path: str = "",
    compiled_dat_path: str = "",
    processor_log_paths: Sequence[str] = (),
    manual_notes: str = "",
    manual_status: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Return a structured manifest for a DAT -> ED generated-world run."""
    status = dict(manual_status or {})
    manual_validation = {
        "dedit_opened": status.get("dedit_opened"),
        "dedit_saved": status.get("dedit_saved"),
        "processor_compiled": status.get("processor_compiled"),
        "game_loaded": status.get("game_loaded"),
        "render_checked": status.get("render_checked"),
        "collision_checked": status.get("collision_checked"),
        "notes": manual_notes or str(status.get("notes") or ""),
    }
    models = [
        {
            "name": item.name,
            "point_count": item.point_count,
            "polygon_count": item.polygon_count,
            "texture_count": item.texture_count,
            "bounds_min": list(item.bounds_min),
            "bounds_max": list(item.bounds_max),
            "center": list(item.center),
            "notes": list(item.notes),
        }
        for item in getattr(report, "models", ()) or ()
    ]
    return {
        "kind": "mm9_dat_to_ed_acceptance",
        "schema_version": 1,
        "status": report.status,
        "source": {
            "original_source": original_source,
            "source_dat_path": report.source_dat_path,
            "staged_source_dat_path": staged_source_dat_path or report.source_dat_path,
        },
        "artifacts": {
            "generated_ed_path": report.generated_ed_path,
            "text_report_path": text_report_path,
            "selection_report_path": selection_report_path,
            "work_dir": report.work_dir,
            "suggested_world_install_path": report.world_install_path,
            "dedit_saved_ed_path": dedit_saved_ed_path,
            "compiled_dat_path": compiled_dat_path,
            "processor_log_paths": list(processor_log_paths),
            "terrain_cutout_coverage_manifest_path": report.terrain_cutout_coverage_manifest_path,
            "terrain_support_source_coverage_manifest_path": report.terrain_support_source_coverage_manifest_path,
        },
        "generation": {
            "group_name": report.group_name,
            "selected_model_names": list(report.selected_model_names),
            "selected_model_count": len(report.selected_model_names),
            "generated_model_count": report.model_count,
            "point_count": report.point_count,
            "polygon_count": report.polygon_count,
            "object_count": report.object_count,
            "object_property_count": report.object_property_count,
            "generated_byte_count": report.generated_byte_count,
            "node_hierarchy_byte_count": report.node_hierarchy_byte_count,
            "wrapper_kind": report.wrapper_kind,
            "wrapper_block_count": report.wrapper_block_count,
            "generated_object_class_counts": dict(report.generated_object_class_counts),
            "include_validation_floor": report.include_validation_floor,
            "include_terrain_support_patch": report.include_terrain_support_patch,
            "include_physics_shell_patch": report.include_physics_shell_patch,
            "max_processor_brushes": report.max_processor_brushes,
            "max_processor_polygons": report.max_processor_polygons,
            "models": models,
        },
        "diagnostics": {
            "terrain_cutout_coverage": _full_world_manifest_cutout_summary(
                report.terrain_cutout_coverage
            ),
            "terrain_support_source_coverage": _full_world_manifest_source_coverage_summary(
                report.terrain_support_source_coverage
            ),
            "manual_steps": list(report.manual_steps),
            "blockers": list(report.blockers),
            "cautions": list(report.cautions),
            "notes": list(report.notes),
        },
        "manual_validation": manual_validation,
    }


def write_full_world_skeleton_acceptance_manifest(
    report: FullWorldSkeletonAcceptanceReport,
    manifest_path: str,
    **kwargs: object,
) -> str:
    """Write a DAT -> ED acceptance manifest and return its path."""
    manifest = build_full_world_skeleton_acceptance_manifest(report, **kwargs)
    os.makedirs(os.path.dirname(os.path.abspath(manifest_path)) or ".", exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    return manifest_path


def _full_world_manifest_cutout_summary(
    report: Optional["TerrainCutoutCoverageReport"],
) -> Optional[Dict[str, object]]:
    if report is None:
        return None
    return {
        "status": report.status,
        "candidate_count": report.candidate_count,
        "covered_cutout_count": report.covered_cutout_count,
        "partial_cutout_count": report.partial_cutout_count,
        "terrain_present_count": report.terrain_present_count,
        "uncertain_count": report.uncertain_count,
        "skipped_model_count": report.skipped_model_count,
        "blockers": list(report.blockers),
        "cautions": list(report.cautions),
        "notes": list(report.notes),
    }


def _full_world_manifest_source_coverage_summary(
    report: Optional["TerrainSupportSourceCoverageReport"],
) -> Optional[Dict[str, object]]:
    if report is None:
        return None
    return {
        "status": report.status,
        "source_polygon_count": report.source_polygon_count,
        "sampled_source_polygon_count": report.sampled_source_polygon_count,
        "generated_coverage_polygon_count": report.generated_coverage_polygon_count,
        "sample_count": report.sample_count,
        "covered_sample_count": report.covered_sample_count,
        "missing_sample_count": report.missing_sample_count,
        "missing_polygon_count": report.missing_polygon_count,
        "missing_ratio": report.missing_ratio,
        "blockers": list(report.blockers),
        "cautions": list(report.cautions),
        "notes": list(report.notes),
    }


def format_terrain_cutout_coverage_report(report: TerrainCutoutCoverageReport) -> str:
    lines = [
        "DAT Terrain0 cutout coverage",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
        f"terrain model: {report.terrain_model_name}",
        (
            "coverage: "
            f"terrain_polygons={report.terrain_polygon_count}, "
            f"sampled_coverage_polygons={report.terrain_coverage_polygon_count}, "
            f"sampled_models={report.sampled_model_count}, "
            f"candidates={report.candidate_count}, "
            f"covered={report.covered_cutout_count}, "
            f"partial={report.partial_cutout_count}, "
            f"present={report.terrain_present_count}, "
            f"uncertain={report.uncertain_count}"
        ),
    ]
    if report.ignored_terrain_textures:
        lines.append("ignored terrain textures: " + ", ".join(report.ignored_terrain_textures))
    for item in report.candidates:
        texture_hits = ", ".join(f"{name}:{count}" for name, count in item.terrain_texture_hits) or "none"
        lines.append(
            f"- {item.candidate_id}: {item.classification}, "
            f"models={len(item.model_names)}, area={item.footprint_area:.1f}, "
            f"missing={item.missing_sample_count}/{item.sample_count} "
            f"({item.missing_ratio:.2f}), terrain_hits={item.terrain_hit_count}, "
            f"bounds={_vec3_text(item.bounds_min)}..{_vec3_text(item.bounds_max)}, "
            f"terrain_textures={texture_hits}, "
            f"model_names={','.join(item.model_names)}"
        )
        for note in item.notes:
            lines.append(f"  note: {note}")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_terrain_support_source_coverage_report(report: TerrainSupportSourceCoverageReport) -> str:
    lines = [
        "DAT Terrain0 support source coverage",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
        f"generated ED: {report.generated_ed_path}",
        f"terrain model: {report.terrain_model_name}",
        (
            "coverage: "
            f"source_polygons={report.source_polygon_count}, "
            f"sampled_source_polygons={report.sampled_source_polygon_count}, "
            f"generated_polygons={report.generated_coverage_polygon_count}, "
            f"samples={report.sample_count}, covered={report.covered_sample_count}, "
            f"missing={report.missing_sample_count}, missing_ratio={report.missing_ratio:.4f}, "
            f"gap_polygons={report.missing_polygon_count}"
        ),
    ]
    if report.ignored_terrain_textures:
        lines.append("ignored terrain textures: " + ", ".join(report.ignored_terrain_textures))
    if report.source_texture_counts:
        lines.append(
            "source textures: "
            + ", ".join(f"{name}:{count}" for name, count in report.source_texture_counts)
        )
    if report.generated_texture_counts:
        lines.append(
            "generated textures: "
            + ", ".join(f"{name}:{count}" for name, count in report.generated_texture_counts)
        )
    if report.missing_texture_sample_counts:
        lines.append(
            "missing samples by texture: "
            + ", ".join(f"{name}:{count}" for name, count in report.missing_texture_sample_counts)
        )
    for gap in report.gaps:
        lines.append(
            f"- source polygon {gap.source_polygon_index}: texture={gap.texture_name}, "
            f"missing={gap.missing_sample_count}/{gap.sample_count} "
            f"({gap.missing_ratio:.2f}), bounds={_vec3_text(gap.bounds_min)}.."
            f"{_vec3_text(gap.bounds_max)}"
        )
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def _prefab_surrogate_pack_manifest(
    *,
    source_dat_path: str,
    work_dir: str,
    staging_prefab_dir: str,
    report_status: str,
    entries: Sequence[PrefabSurrogatePackEntry],
    manual_steps: Sequence[str],
    cautions: Sequence[str],
    notes: Sequence[str],
) -> Dict[str, object]:
    return {
        "kind": "mm9_surrogate_named_group_prefab_pack",
        "status": report_status,
        "source_dat_path": source_dat_path,
        "work_dir": work_dir,
        "staging_prefab_dir": staging_prefab_dir,
        "hierarchy_kind": "named_group",
        "entry_count": len(entries),
        "staged_count": sum(1 for item in entries if item.status == "ready_staged_named_group_prefab"),
        "entries": [
            {
                "group_name": item.group_name,
                "status": item.status,
                "hierarchy_kind": item.hierarchy_kind,
                "model_names": list(item.model_names),
                "model_count": item.model_count,
                "point_count": item.point_count,
                "polygon_count": item.polygon_count,
                "generated_ed_path": item.generated_ed_path,
                "staged_prefab_path": item.staged_prefab_path,
                "filename": item.filename,
                "notes": list(item.notes),
            }
            for item in entries
        ],
        "manual_steps": list(manual_steps),
        "cautions": list(cautions),
        "notes": list(notes),
    }


def _terrain_cutout_coverage_manifest(report: TerrainCutoutCoverageReport) -> Dict[str, object]:
    return {
        "kind": "mm9_terrain_cutout_coverage",
        "status": report.status,
        "source_dat_path": report.source_dat_path,
        "terrain_model_name": report.terrain_model_name,
        "terrain_polygon_count": report.terrain_polygon_count,
        "terrain_coverage_polygon_count": report.terrain_coverage_polygon_count,
        "sampled_model_count": report.sampled_model_count,
        "candidate_count": report.candidate_count,
        "covered_cutout_count": report.covered_cutout_count,
        "partial_cutout_count": report.partial_cutout_count,
        "terrain_present_count": report.terrain_present_count,
        "uncertain_count": report.uncertain_count,
        "skipped_model_count": report.skipped_model_count,
        "ignored_terrain_textures": list(report.ignored_terrain_textures),
        "candidates": [
            {
                "candidate_id": item.candidate_id,
                "classification": item.classification,
                "model_names": list(item.model_names),
                "model_indices": list(item.model_indices),
                "bounds_min": list(item.bounds_min),
                "bounds_max": list(item.bounds_max),
                "footprint_area": item.footprint_area,
                "sample_count": item.sample_count,
                "missing_sample_count": item.missing_sample_count,
                "missing_ratio": item.missing_ratio,
                "terrain_hit_count": item.terrain_hit_count,
                "terrain_texture_hits": [
                    {"texture": texture, "sample_count": count}
                    for texture, count in item.terrain_texture_hits
                ],
                "notes": list(item.notes),
            }
            for item in report.candidates
        ],
        "blockers": list(report.blockers),
        "cautions": list(report.cautions),
        "notes": list(report.notes),
    }


def _terrain_support_source_coverage_manifest(report: TerrainSupportSourceCoverageReport) -> Dict[str, object]:
    return {
        "kind": "mm9_terrain_support_source_coverage",
        "status": report.status,
        "source_dat_path": report.source_dat_path,
        "generated_ed_path": report.generated_ed_path,
        "terrain_model_name": report.terrain_model_name,
        "source_polygon_count": report.source_polygon_count,
        "sampled_source_polygon_count": report.sampled_source_polygon_count,
        "generated_coverage_polygon_count": report.generated_coverage_polygon_count,
        "sample_count": report.sample_count,
        "covered_sample_count": report.covered_sample_count,
        "missing_sample_count": report.missing_sample_count,
        "missing_polygon_count": report.missing_polygon_count,
        "missing_ratio": report.missing_ratio,
        "source_texture_counts": [
            {"texture": texture, "polygon_count": count}
            for texture, count in report.source_texture_counts
        ],
        "generated_texture_counts": [
            {"texture": texture, "polygon_count": count}
            for texture, count in report.generated_texture_counts
        ],
        "missing_texture_sample_counts": [
            {"texture": texture, "sample_count": count}
            for texture, count in report.missing_texture_sample_counts
        ],
        "gaps": [
            {
                "source_polygon_index": gap.source_polygon_index,
                "texture_name": gap.texture_name,
                "bounds_min": list(gap.bounds_min),
                "bounds_max": list(gap.bounds_max),
                "sample_count": gap.sample_count,
                "missing_sample_count": gap.missing_sample_count,
                "missing_ratio": gap.missing_ratio,
            }
            for gap in report.gaps
        ],
        "ignored_terrain_textures": list(report.ignored_terrain_textures),
        "blockers": list(report.blockers),
        "cautions": list(report.cautions),
        "notes": list(report.notes),
    }


def format_black_box_compiler_corpus_report(report: BlackBoxCompilerCorpusReport) -> str:
    lines = [
        "DAT black-box compiler corpus",
        f"status: {report.status}",
        f"processor: {report.processor_path}",
    ]
    if report.worlds_dir:
        lines.append(f"worlds dir: {report.worlds_dir}")
    if report.work_dir:
        lines.append(f"work dir: {report.work_dir}")
    if report.processor_project_dir:
        lines.append(f"processor project dir: {report.processor_project_dir}")
    lines.append(
        "fixtures: "
        f"selected={report.fixture_count}, ran={report.ran_count}, "
        f"matched={report.matched_count}, differing={report.differing_count}, "
        f"failed={report.failed_count}, skipped={report.skipped_count}"
    )
    for run in report.runs:
        mismatches = [
            item.system
            for item in run.report.comparisons
            if item.status == "mismatch"
        ]
        model_diff_count = sum(
            1 for item in run.report.world_model_comparisons
            if item.status != "match"
        )
        detail = (
            f"- {run.stem}: status={run.status}, "
            f"source={os.path.basename(run.source_ed_path)}, "
            f"reference={os.path.basename(run.reference_dat_path)}, "
            f"captured={run.report.captured_output}, "
            f"output_rewritten={run.report.output_rewritten}, "
            f"world_model_diffs={model_diff_count}"
        )
        if mismatches:
            detail += ", mismatches=" + ",".join(mismatches)
        lines.append(detail)
        for note in run.report.notes:
            lines.append(f"  note: {note}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_black_box_compiler_acceptance_report(report: BlackBoxCompilerAcceptanceReport) -> str:
    lines = [
        "DAT black-box compiler acceptance",
        f"status: {report.status}",
        f"harness status: {report.harness.status}",
        f"source ED: {report.harness.source_ed_path}",
        f"output DAT: {report.harness.output_dat_path}",
        "accepted difference systems: " + ", ".join(report.accepted_difference_systems),
        "mismatched systems: " + (", ".join(report.mismatched_systems) or "none"),
        "accepted differences: " + (", ".join(report.accepted_differences) or "none"),
        "unaccepted differences: " + (", ".join(report.unaccepted_differences) or "none"),
        (
            "manual validation: "
            f"status={report.manual_validation.status}, "
            f"fresh_load={report.manual_validation.fresh_load}, "
            f"visuals_ok={report.manual_validation.visuals_ok}, "
            f"collision_ok={report.manual_validation.collision_ok}, "
            f"tested_at={report.manual_validation.tested_at or 'unknown'}"
        ),
    ]
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def _built_in_candidates() -> List[CompilerCandidate]:
    return [
        CompilerCandidate(
            candidate_id="mm9_editor_minimal_bsp_compiler",
            name="mm9_editor minimal BSP compiler",
            source="features/dat_editing/bsp_compile.py",
            input_formats=("obj", "gltf", "geometry_scene"),
            output_scope="standalone_world_model_record",
            expected_dat_version=66,
            can_compile_full_world=False,
            rebuilt_systems=("Terrain*",),
            evidence=(
                "compile_world_model_record emits a single v66 WorldModel record",
                "record deliberately avoids portals, leaves, nodes, and terrain sections",
            ),
        ),
        CompilerCandidate(
            candidate_id="legacy_ed_reader",
            name="legacy ED reader",
            source="features/dat_editing/legacy_ed.py",
            input_formats=("ed",),
            output_scope="read_only_source_geometry",
            expected_dat_version=None,
            can_compile_full_world=False,
            rebuilt_systems=(),
            evidence=("recovers legacy brush geometry for inspection/import hints",),
        ),
        CompilerCandidate(
            candidate_id="lta_reader",
            name="LTA source-world reader",
            source="features/dat_editing/source_world.py",
            input_formats=("lta",),
            output_scope="read_only_source_geometry",
            expected_dat_version=None,
            can_compile_full_world=False,
            rebuilt_systems=(),
            evidence=("parses DEdit LTA brush geometry for inspection/import hints",),
        ),
    ]


def _local_lithtech_candidates(lithtech_root: Optional[str]) -> List[CompilerCandidate]:
    root = lithtech_root or os.path.join("C:\\", "lithtech", "lithtech")
    tool_root = os.path.dirname(os.path.abspath(root))
    pcworldpacker = os.path.join(
        root,
        "tools",
        "PreProcessor",
        "Packer_PC",
        "PCWorldPacker.cpp",
    )
    dat_to_jupiter = os.path.join(
        root,
        "tools",
        "mm9_dat_to_jupiter_probe",
        "mm9_dat_to_jupiter_probe.cpp",
    )
    lith21_processor = os.path.join(
        tool_root,
        "Lith21tools",
        "Processor.exe",
    )
    ltworldconverter = os.path.join(
        tool_root,
        "LTWorldConverter",
    )
    return [
        analyze_pcworldpacker_source(pcworldpacker),
        analyze_mm9_dat_to_jupiter_probe_source(dat_to_jupiter),
        analyze_lith21_processor_executable(lith21_processor),
        analyze_ltworldconverter_source(ltworldconverter),
    ]


def _scan_artifact_dir(
    root: str,
    *,
    recursive: bool,
    max_files: int,
) -> List[SourceWorldArtifact]:
    if not root or not os.path.exists(root):
        return []
    result: List[SourceWorldArtifact] = []
    if os.path.isfile(root):
        artifact = _artifact_for_path(root)
        return [artifact] if artifact is not None else []

    if recursive:
        walker = os.walk(root)
        for dirpath, _dirnames, filenames in walker:
            for filename in sorted(filenames):
                if len(result) >= int(max_files):
                    return result
                artifact = _artifact_for_path(os.path.join(dirpath, filename))
                if artifact is not None:
                    result.append(artifact)
    else:
        for filename in sorted(os.listdir(root)):
            if len(result) >= int(max_files):
                return result
            path = os.path.join(root, filename)
            if not os.path.isfile(path):
                continue
            artifact = _artifact_for_path(path)
            if artifact is not None:
                result.append(artifact)
    return result


def _artifact_for_path(path: str) -> Optional[SourceWorldArtifact]:
    ext = os.path.splitext(path)[1].lower()
    if ext not in {".dat", ".ed", ".lta", ".ltc"}:
        return None
    fmt = ext[1:]
    size = os.path.getsize(path)
    version = _first_u32(path)
    status = "unknown"
    notes: List[str] = []
    if fmt == "dat":
        if version == 66:
            status = "v66_dat"
        elif version is None:
            status = "dat_unreadable"
            notes.append("could not read DAT version")
        else:
            status = "non_v66_dat"
            notes.append(f"DAT version {version}, expected 66")
    elif fmt == "ed":
        if version == 1249:
            status = "legacy_ed"
        elif version is None:
            status = "ed_unreadable"
            notes.append("could not read ED version")
        else:
            status = "unsupported_ed"
            notes.append(f"ED version {version}, expected 1249")
    elif fmt == "lta":
        status = "lta_source"
        if size <= 0:
            notes.append("empty LTA source file")
    elif fmt == "ltc":
        status = "compressed_ltc_unsupported"
        notes.append("compressed LTC source worlds are not parsed yet")
    return SourceWorldArtifact(
        path=os.path.abspath(path),
        stem=os.path.splitext(os.path.basename(path))[0],
        format=fmt,
        size=int(size),
        version=version,
        status=status,
        notes=tuple(notes),
    )


def _first_u32(path: str) -> Optional[int]:
    try:
        with open(path, "rb") as f:
            data = f.read(4)
    except OSError:
        return None
    if len(data) < 4:
        return None
    return struct.unpack("<I", data)[0]


def _load_source_geometry_summary(path: str, fmt: str) -> SourceGeometrySummary:
    fmt = str(fmt or os.path.splitext(path)[1].lstrip(".")).lower()
    if fmt == "ltc":
        return SourceGeometrySummary(
            path=os.path.abspath(path),
            format=fmt,
            status="unsupported",
            notes=("compressed LTC source worlds are not parsed yet",),
        )
    try:
        if fmt == "ed":
            from features.dat_editing import legacy_ed

            scene = legacy_ed.load_legacy_ed_geometry_scene(path)
        elif fmt == "lta":
            from features.dat_editing import source_world

            scene = source_world.load_lta_geometry_scene(path)
        else:
            return SourceGeometrySummary(
                path=os.path.abspath(path),
                format=fmt,
                status="unsupported",
                notes=(f"source format {fmt!r} is not parsed as source geometry",),
            )
    except Exception as exc:
        return SourceGeometrySummary(
            path=os.path.abspath(path),
            format=fmt,
            status="parse_failed",
            notes=(str(exc),),
        )

    metadata = _compact_source_metadata(scene.metadata)
    notes: List[str] = []
    skipped = scene.metadata.get("skipped_range_count")
    if skipped:
        notes.append(f"source parser skipped {skipped} byte range(s)")
    unknown = scene.metadata.get("unknown_ranges")
    if isinstance(unknown, list) and unknown:
        notes.append("source parser has unknown byte ranges")
    return SourceGeometrySummary(
        path=os.path.abspath(path),
        format=fmt,
        status="loaded",
        model_count=len(scene.models),
        point_count=sum(len(model.points) for model in scene.models),
        polygon_count=sum(len(model.faces) for model in scene.models),
        material_count=len(scene.materials),
        metadata=metadata,
        notes=tuple(notes),
    )


def _load_dat_output_semantic_summary(path: str) -> DatOutputSemanticSummary:
    absolute = os.path.abspath(path)
    version = _first_u32(path)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        return DatOutputSemanticSummary(
            path=absolute,
            status="read_failed",
            version=version,
            notes=(str(exc),),
        )

    try:
        from core import bsp

        parsed = bsp.parse(data)
    except Exception as exc:
        return DatOutputSemanticSummary(
            path=absolute,
            status="parse_failed",
            version=version,
            notes=(str(exc),),
        )

    notes: List[str] = list(getattr(parsed, "parse_warnings", []) or [])
    terrain_models = [
        model
        for model in parsed.world_models
        if terrain_semantics.is_terrain_model(model)
    ]
    world_model_summaries = tuple(
        DatWorldModelSemanticSummary(
            index=index,
            name=str(model.name or ""),
            point_count=len(getattr(model, "points", []) or []),
            polygon_count=len(getattr(model, "polygons", []) or []),
            texture_count=len(getattr(model, "texture_names", []) or []),
            surface_count=len(getattr(model, "surfaces", []) or []),
            raw_size=max(0, int(getattr(model, "raw_end", 0) or 0) - int(getattr(model, "raw_start", 0) or 0)),
        )
        for index, model in enumerate(parsed.world_models)
    )
    texture_names = {
        texture
        for model in parsed.world_models
        for texture in getattr(model, "texture_names", []) or []
    }
    inspect_names = list(dict.fromkeys(
        [model.name for model in terrain_models]
        + ["PhysicsBSP", "VisBSP"]
    ))
    inspections = {}
    try:
        from features.dat_editing import bsp_record_inspector

        inspections = bsp_record_inspector.inspect_dat(data, model_names=inspect_names)
    except Exception as exc:
        notes.append(f"BSP record inspection failed: {exc}")

    terrain_inspections = [
        inspections.get(model.name)
        for model in terrain_models
        if inspections.get(model.name) is not None and inspections[model.name].present
    ]
    physics = inspections.get("PhysicsBSP")
    vis = inspections.get("VisBSP")
    object_count, object_notes = _dat_object_count(data)
    notes.extend(object_notes)
    tree = getattr(parsed, "world_tree", None)

    return DatOutputSemanticSummary(
        path=absolute,
        status="loaded",
        version=int(version) if version is not None else None,
        world_model_count=len(parsed.world_models),
        world_model_summaries=world_model_summaries,
        terrain_model_count=len(terrain_models),
        terrain_model_names=tuple(model.name for model in terrain_models),
        terrain_point_count=sum(len(model.points) for model in terrain_models),
        terrain_polygon_count=sum(len(model.polygons) for model in terrain_models),
        texture_count=len(texture_names),
        object_count=object_count,
        world_tree_node_count=int(getattr(tree, "decoded_node_count", 0) or 0),
        world_tree_leaf_count=int(getattr(tree, "leaf_node_count", 0) or 0),
        lightmap_grid_size=float(getattr(parsed, "lightmap_grid_size", 0.0) or 0.0),
        object_data_size=max(0, int(getattr(parsed, "ren_pos", 0) or 0) - int(getattr(parsed, "obj_pos", 0) or 0)),
        render_data_size=max(0, len(data) - int(getattr(parsed, "ren_pos", 0) or 0)),
        physics_bsp_present=bool(physics and physics.present),
        physics_point_count=int(getattr(physics, "point_count", 0) or 0) if physics else 0,
        physics_polygon_count=int(getattr(physics, "polygon_count", 0) or 0) if physics else 0,
        physics_node_count=int(getattr(physics, "node_count", 0) or 0) if physics else 0,
        physics_block_cell_count=int(getattr(physics, "physics_block_cell_count", 0) or 0) if physics else 0,
        vis_bsp_present=bool(vis and vis.present),
        vis_leaf_count=int(getattr(vis, "leaf_count", 0) or 0) if vis else 0,
        vis_node_count=int(getattr(vis, "node_count", 0) or 0) if vis else 0,
        portal_reference_count=sum(
            int(getattr(item, "user_portal_count", 0) or 0)
            + int(getattr(item, "leaf_portal_reference_count", 0) or 0)
            for item in list(terrain_inspections) + ([vis] if vis and vis.present else [])
        ),
        terrain_tail_node_count=sum(int(getattr(item, "terrain_tail_node_count", 0) or 0) for item in terrain_inspections),
        terrain_tail_polygon_list_count=sum(
            int(getattr(item, "terrain_tail_polygon_list_count", 0) or 0)
            for item in terrain_inspections
        ),
        terrain_render_chunk_count=sum(
            int(getattr(item, "terrain_tail_render_chunk_count", 0) or 0)
            for item in terrain_inspections
        ),
        terrain_render_fully_decoded_count=sum(
            1 for item in terrain_inspections
            if bool(getattr(item, "terrain_tail_render_fully_decoded", False))
        ),
        terrain_lightmapped_polygon_count=sum(
            int(getattr(item, "lightmapped_polygon_count", 0) or 0)
            for item in terrain_inspections
        ),
        terrain_lightmap_pixel_count=sum(
            int(getattr(item, "lightmap_pixel_count", 0) or 0)
            for item in terrain_inspections
        ),
        top_level_section_sizes=_dat_top_level_section_sizes(data, parsed),
        notes=tuple(notes),
    )


def _compare_semantic_systems(
    source: SourceGeometrySummary,
    dat: DatOutputSemanticSummary,
) -> List[SemanticSystemComparison]:
    source_loaded = source.status == "loaded"
    dat_loaded = dat.status == "loaded"
    systems = [
        SemanticSystemComparison(
            system="geometry",
            status=(
                "source_and_compiled_available"
                if source_loaded and dat_loaded
                else ("source_only" if source_loaded else ("compiled_only" if dat_loaded else "unavailable"))
            ),
            source_detail=f"models={source.model_count}, polygons={source.polygon_count}",
            dat_detail=f"terrain_models={dat.terrain_model_count}, terrain_polygons={dat.terrain_polygon_count}",
            notes=("source and compiled polygon counts are not expected to match before CSG/BSP compilation",),
        ),
        SemanticSystemComparison(
            system="objects",
            status="compiled_only" if dat_loaded and dat.object_count is not None else "absent_or_unparsed",
            source_detail="not decoded by current ED/LTA source readers",
            dat_detail=(
                f"objects={dat.object_count}, object_data_bytes={dat.object_data_size}"
                if dat_loaded
                else ""
            ),
        ),
        SemanticSystemComparison(
            system="physics",
            status="compiled_only" if dat.physics_bsp_present else "absent_or_unparsed",
            source_detail="source brush physics semantics are not rebuilt",
            dat_detail=(
                f"PhysicsBSP polygons={dat.physics_polygon_count}, nodes={dat.physics_node_count}, "
                f"block_cells={dat.physics_block_cell_count}"
                if dat.physics_bsp_present
                else ""
            ),
        ),
        SemanticSystemComparison(
            system="visibility",
            status=(
                "compiled_only"
                if dat_loaded and (
                    dat.vis_bsp_present
                    or dat.world_tree_node_count > 0
                    or dat.terrain_tail_node_count > 0
                    or dat.portal_reference_count > 0
                )
                else "absent_or_unparsed"
            ),
            source_detail="source visibility partitions are not decoded by current readers",
            dat_detail=(
                f"world_tree_nodes={dat.world_tree_node_count}, terrain_tail_nodes={dat.terrain_tail_node_count}, "
                f"VisBSP present={dat.vis_bsp_present}, portal_refs={dat.portal_reference_count}"
                if dat_loaded
                else ""
            ),
        ),
        SemanticSystemComparison(
            system="lighting",
            status=(
                "compiled_only"
                if dat_loaded and (
                    dat.lightmap_grid_size > 0.0
                    or dat.terrain_lightmapped_polygon_count > 0
                    or dat.render_data_size > 0
                )
                else "absent_or_unparsed"
            ),
            source_detail="source light objects/lightmap intent are not decoded into rebuildable data",
            dat_detail=(
                f"light_grid={dat.lightmap_grid_size:.2f}, terrain_lightmaps={dat.terrain_lightmapped_polygon_count}, "
                f"lightmap_pixels={dat.terrain_lightmap_pixel_count}"
                if dat_loaded
                else ""
            ),
        ),
        SemanticSystemComparison(
            system="render_data",
            status=(
                "compiled_only"
                if dat_loaded and (dat.render_data_size > 0 or dat.terrain_render_chunk_count > 0)
                else "absent_or_unparsed"
            ),
            source_detail="source render output is not emitted by current readers",
            dat_detail=(
                f"render_data_bytes={dat.render_data_size}, terrain_render_chunks={dat.terrain_render_chunk_count}, "
                f"fully_decoded_terrain_models={dat.terrain_render_fully_decoded_count}"
                if dat_loaded
                else ""
            ),
        ),
    ]
    return systems


def _semantic_comparison_status(
    source: SourceGeometrySummary,
    dat: DatOutputSemanticSummary,
) -> str:
    if source.status == "loaded" and dat.status == "loaded":
        return "compared_with_compiled_only_gaps"
    if source.status == "unsupported":
        return "source_unsupported"
    if source.status == "parse_failed":
        return "source_parse_failed"
    if dat.status in {"read_failed", "parse_failed"}:
        return "dat_parse_failed"
    return "incomplete"


def _semantic_comparison_notes(
    source: SourceGeometrySummary,
    dat: DatOutputSemanticSummary,
    systems: Sequence[SemanticSystemComparison],
) -> List[str]:
    notes: List[str] = []
    notes.extend(source.notes)
    notes.extend(dat.notes)
    if source.status == "loaded" and dat.status == "loaded":
        compiled_only = [
            item.system
            for item in systems
            if item.status == "compiled_only"
        ]
        if compiled_only:
            notes.append("compiled-only systems: " + ", ".join(compiled_only))
    return _unique_text(notes)


def _compact_source_metadata(metadata: Dict[str, object]) -> Dict[str, object]:
    keep = {
        "kind",
        "format",
        "version",
        "versioncode",
        "infostring",
        "brush_count",
        "recovered_brush_count",
        "recovered_polygon_count",
        "declared_brush_count",
        "wrapper",
        "block_count",
        "decompressed_size",
        "has_globalproplist",
        "has_nodehierarchy",
        "skipped_candidate_count",
        "skipped_range_count",
    }
    return {
        key: value
        for key, value in metadata.items()
        if key in keep
    }


def _dat_object_count(data: bytes) -> Tuple[Optional[int], List[str]]:
    try:
        from mm9_patcher import mm9_patch as patcher

        header = patcher.Header.parse(data)
        objects, obj_end = patcher.parse_objects(data, header.obj_pos)
        notes: List[str] = []
        if obj_end != header.ren_pos:
            notes.append(
                f"WorldObject section ended at {obj_end}, expected RenderDataPos {header.ren_pos}"
            )
        return len(objects), notes
    except Exception as exc:
        return None, [f"WorldObject parse failed: {exc}"]


def _dat_top_level_section_sizes(data: bytes, parsed: object) -> Dict[str, int]:
    size = len(data)
    header_end = min(size, 44)
    table_start = _clamp_int(int(getattr(parsed, "world_model_table_start", 0) or header_end), header_end, size)
    obj_pos = _clamp_int(int(getattr(parsed, "obj_pos", 0) or table_start), table_start, size)
    ren_pos = _clamp_int(int(getattr(parsed, "ren_pos", 0) or obj_pos), obj_pos, size)
    return {
        "header": header_end,
        "world_setup_tree": max(0, table_start - header_end),
        "world_model_records": max(0, obj_pos - table_start),
        "object_data": max(0, ren_pos - obj_pos),
        "render_data": max(0, size - ren_pos),
    }


def _compare_black_box_dat_summaries(
    reference: DatOutputSemanticSummary,
    generated: DatOutputSemanticSummary,
) -> List[BlackBoxCompilerSystemComparison]:
    comparisons = [
        _black_box_field_comparison(
            "header",
            (reference.version,),
            (generated.version,),
            f"version={reference.version}",
            f"version={generated.version}",
        ),
        _black_box_field_comparison(
            "world_models",
            (reference.world_model_count,),
            (generated.world_model_count,),
            f"models={reference.world_model_count}",
            f"models={generated.world_model_count}",
        ),
        _black_box_field_comparison(
            "terrain",
            (
                reference.terrain_model_count,
                reference.terrain_model_names,
                reference.terrain_point_count,
                reference.terrain_polygon_count,
            ),
            (
                generated.terrain_model_count,
                generated.terrain_model_names,
                generated.terrain_point_count,
                generated.terrain_polygon_count,
            ),
            (
                f"models={reference.terrain_model_count}, points={reference.terrain_point_count}, "
                f"polygons={reference.terrain_polygon_count}"
            ),
            (
                f"models={generated.terrain_model_count}, points={generated.terrain_point_count}, "
                f"polygons={generated.terrain_polygon_count}"
            ),
        ),
        _black_box_field_comparison(
            "objects",
            (reference.object_count, reference.object_data_size),
            (generated.object_count, generated.object_data_size),
            f"objects={reference.object_count}, bytes={reference.object_data_size}",
            f"objects={generated.object_count}, bytes={generated.object_data_size}",
        ),
        _black_box_field_comparison(
            "physics",
            (
                reference.physics_bsp_present,
                reference.physics_point_count,
                reference.physics_polygon_count,
                reference.physics_node_count,
                reference.physics_block_cell_count,
            ),
            (
                generated.physics_bsp_present,
                generated.physics_point_count,
                generated.physics_polygon_count,
                generated.physics_node_count,
                generated.physics_block_cell_count,
            ),
            (
                f"present={reference.physics_bsp_present}, points={reference.physics_point_count}, "
                f"polygons={reference.physics_polygon_count}, nodes={reference.physics_node_count}, "
                f"cells={reference.physics_block_cell_count}"
            ),
            (
                f"present={generated.physics_bsp_present}, points={generated.physics_point_count}, "
                f"polygons={generated.physics_polygon_count}, nodes={generated.physics_node_count}, "
                f"cells={generated.physics_block_cell_count}"
            ),
        ),
        _black_box_field_comparison(
            "visibility",
            (
                reference.world_tree_node_count,
                reference.world_tree_leaf_count,
                reference.vis_bsp_present,
                reference.vis_leaf_count,
                reference.vis_node_count,
                reference.portal_reference_count,
                reference.terrain_tail_node_count,
                reference.terrain_tail_polygon_list_count,
            ),
            (
                generated.world_tree_node_count,
                generated.world_tree_leaf_count,
                generated.vis_bsp_present,
                generated.vis_leaf_count,
                generated.vis_node_count,
                generated.portal_reference_count,
                generated.terrain_tail_node_count,
                generated.terrain_tail_polygon_list_count,
            ),
            (
                f"world_tree={reference.world_tree_node_count}, VisBSP={reference.vis_bsp_present}, "
                f"portals={reference.portal_reference_count}, terrain_tail={reference.terrain_tail_node_count}"
            ),
            (
                f"world_tree={generated.world_tree_node_count}, VisBSP={generated.vis_bsp_present}, "
                f"portals={generated.portal_reference_count}, terrain_tail={generated.terrain_tail_node_count}"
            ),
        ),
        _black_box_field_comparison(
            "lighting",
            (
                reference.lightmap_grid_size,
                reference.terrain_lightmapped_polygon_count,
                reference.terrain_lightmap_pixel_count,
            ),
            (
                generated.lightmap_grid_size,
                generated.terrain_lightmapped_polygon_count,
                generated.terrain_lightmap_pixel_count,
            ),
            (
                f"grid={reference.lightmap_grid_size:.2f}, "
                f"terrain_lightmaps={reference.terrain_lightmapped_polygon_count}, "
                f"pixels={reference.terrain_lightmap_pixel_count}"
            ),
            (
                f"grid={generated.lightmap_grid_size:.2f}, "
                f"terrain_lightmaps={generated.terrain_lightmapped_polygon_count}, "
                f"pixels={generated.terrain_lightmap_pixel_count}"
            ),
        ),
        _black_box_field_comparison(
            "render_data",
            (
                reference.render_data_size,
                reference.terrain_render_chunk_count,
                reference.terrain_render_fully_decoded_count,
            ),
            (
                generated.render_data_size,
                generated.terrain_render_chunk_count,
                generated.terrain_render_fully_decoded_count,
            ),
            (
                f"bytes={reference.render_data_size}, chunks={reference.terrain_render_chunk_count}, "
                f"fully_decoded={reference.terrain_render_fully_decoded_count}"
            ),
            (
                f"bytes={generated.render_data_size}, chunks={generated.terrain_render_chunk_count}, "
                f"fully_decoded={generated.terrain_render_fully_decoded_count}"
            ),
        ),
        _black_box_field_comparison(
            "top_level_sections",
            (tuple(sorted(reference.top_level_section_sizes.items())),),
            (tuple(sorted(generated.top_level_section_sizes.items())),),
            _section_size_text(reference.top_level_section_sizes),
            _section_size_text(generated.top_level_section_sizes),
        ),
    ]
    return comparisons


def _compare_black_box_world_model_summaries(
    reference: DatOutputSemanticSummary,
    generated: DatOutputSemanticSummary,
) -> List[BlackBoxWorldModelComparison]:
    comparisons: List[BlackBoxWorldModelComparison] = []
    max_count = max(len(reference.world_model_summaries), len(generated.world_model_summaries))
    for index in range(max_count):
        ref = reference.world_model_summaries[index] if index < len(reference.world_model_summaries) else None
        gen = generated.world_model_summaries[index] if index < len(generated.world_model_summaries) else None
        name = (ref.name if ref is not None else (gen.name if gen is not None else f"#{index}"))
        if ref is None:
            comparisons.append(BlackBoxWorldModelComparison(
                index=index,
                name=name,
                status="extra_generated",
                generated_detail=_world_model_summary_detail(gen),
            ))
            continue
        if gen is None:
            comparisons.append(BlackBoxWorldModelComparison(
                index=index,
                name=name,
                status="missing_generated",
                reference_detail=_world_model_summary_detail(ref),
            ))
            continue
        ref_values = (
            ref.name,
            ref.point_count,
            ref.polygon_count,
            ref.texture_count,
            ref.surface_count,
            ref.raw_size,
        )
        gen_values = (
            gen.name,
            gen.point_count,
            gen.polygon_count,
            gen.texture_count,
            gen.surface_count,
            gen.raw_size,
        )
        comparisons.append(BlackBoxWorldModelComparison(
            index=index,
            name=name,
            status="match" if ref_values == gen_values else "mismatch",
            reference_detail=_world_model_summary_detail(ref),
            generated_detail=_world_model_summary_detail(gen),
        ))
    return comparisons


def _world_model_summary_detail(summary: Optional[DatWorldModelSemanticSummary]) -> str:
    if summary is None:
        return "none"
    return (
        f"name={summary.name}, points={summary.point_count}, polygons={summary.polygon_count}, "
        f"textures={summary.texture_count}, surfaces={summary.surface_count}, bytes={summary.raw_size}"
    )


def _black_box_field_comparison(
    system: str,
    reference_values: Tuple[object, ...],
    generated_values: Tuple[object, ...],
    reference_detail: str,
    generated_detail: str,
) -> BlackBoxCompilerSystemComparison:
    status = "match" if reference_values == generated_values else "mismatch"
    return BlackBoxCompilerSystemComparison(
        system=system,
        status=status,
        reference_detail=reference_detail,
        generated_detail=generated_detail,
    )


def _section_size_text(sizes: Dict[str, int]) -> str:
    return ", ".join(f"{name}={sizes[name]}" for name in sorted(sizes))


def _processor_command_context(*, ed_path: str, project_dir: str) -> Dict[str, str]:
    stem = os.path.splitext(os.path.basename(ed_path))[0]
    worlds_dir = os.path.dirname(ed_path)
    return {
        "ed_path": ed_path,
        "ed_no_ext": os.path.splitext(ed_path)[0],
        "ed_stem": stem,
        "worlds_dir": worlds_dir,
        "project_dir": project_dir,
    }


def _black_box_log_paths(work_root: str) -> List[str]:
    result: List[str] = []
    if not work_root or not os.path.exists(work_root):
        return result
    for dirpath, _dirnames, filenames in os.walk(work_root):
        for filename in sorted(filenames):
            if filename.lower().endswith(".log"):
                result.append(os.path.join(dirpath, filename))
    return sorted(result)


def _black_box_run_log_paths(
    work_root: str,
    processor_path: str,
    stem: str,
    since_time: float,
) -> List[str]:
    result = list(_black_box_log_paths(work_root))
    processor_dir = os.path.dirname(os.path.abspath(processor_path))
    if processor_dir and os.path.isdir(processor_dir):
        prefix = stem.lower()
        try:
            filenames = os.listdir(processor_dir)
        except OSError:
            filenames = []
        for filename in filenames:
            lower = filename.lower()
            if not lower.endswith(".log") or not lower.startswith(prefix):
                continue
            path = os.path.join(processor_dir, filename)
            try:
                if os.path.getmtime(path) < since_time:
                    continue
            except OSError:
                continue
            result.append(path)
    return sorted(_unique_text(result))


def parse_processor_log_summary(path: str) -> BlackBoxProcessorLogSummary:
    """Parse a LithTech Processor log into the shared summary dataclass."""
    return _parse_processor_log(path)


def _parse_processor_log(path: str) -> BlackBoxProcessorLogSummary:
    absolute = os.path.abspath(path)
    if not os.path.exists(absolute):
        return BlackBoxProcessorLogSummary(
            path=absolute,
            status="missing",
            notes=(f"log file was not found: {absolute}",),
        )
    try:
        with open(absolute, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as exc:
        return BlackBoxProcessorLogSummary(
            path=absolute,
            status="read_failed",
            notes=(str(exc),),
        )

    processing_path = ""
    warnings: List[str] = []
    warning_counts: Dict[str, int] = {}
    model_counts: List[Tuple[str, int]] = []
    world_tree_nodes: Optional[int] = None
    world_tree_depth: Optional[int] = None
    tree_depth: Optional[int] = None
    runtime_minutes: Optional[float] = None
    lightmap_grid_size: Optional[float] = None
    btw_poly_splits: Optional[int] = None
    joined_polies_left: Optional[int] = None
    joined_polies_removed: Optional[int] = None
    problem_brushes: Optional[int] = None
    unseen_polies_removed: Optional[int] = None
    t_junction_vertices: Optional[int] = None
    input_polies: Optional[int] = None
    input_vertices: Optional[int] = None
    output_polies: Optional[int] = None
    output_vertices: Optional[int] = None
    lightmap_data_size: Optional[int] = None
    object_count: Optional[int] = None

    model_pattern = re.compile(r"^\s*-\s+(.+?)\s+\((\d+)\s+pol\w*\)\s*$", re.IGNORECASE)
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Processing "):
            processing_path = line[len("Processing "):].strip()
            continue
        if line.startswith("**"):
            warnings.append(line)
            warning_counts[line] = warning_counts.get(line, 0) + 1
            continue
        match = model_pattern.match(line)
        if match:
            model_counts.append((match.group(1).strip(), int(match.group(2))))
            continue
        value = _processor_log_int_after(line, "WorldTree nodes:")
        if value is not None:
            world_tree_nodes = value
            continue
        value = _processor_log_int_after(line, "WorldTree depth:")
        if value is not None:
            world_tree_depth = value
            continue
        value = _processor_log_int_after(line, "Tree depth:")
        if value is not None:
            tree_depth = value
            continue
        match = re.match(r"^Done in\s+(-?\d+(?:\.\d+)?)\s+minutes\s*$", line, re.IGNORECASE)
        if match:
            runtime_minutes = float(match.group(1))
            continue
        fvalue = _processor_log_float_after(line, "Lightmap Grid Size:")
        if fvalue is not None:
            lightmap_grid_size = fvalue
            continue
        match = re.match(r"^(\d+)\s+BTW poly splits\.\s*$", line, re.IGNORECASE)
        if match:
            btw_poly_splits = int(match.group(1))
            continue
        match = re.match(r"^(\d+)\s+polies removed\.\s*$", line, re.IGNORECASE)
        if match:
            joined_polies_removed = int(match.group(1))
            continue
        match = re.match(r"^(\d+)\s+polies left\.\s*$", line, re.IGNORECASE)
        if match:
            joined_polies_left = int(match.group(1))
            continue
        match = re.match(r"^Found\s+(\d+)\s+problem brushes\s*$", line, re.IGNORECASE)
        if match:
            problem_brushes = int(match.group(1))
            continue
        value = _processor_log_int_after(line, "Number of (unseen) polies removed:")
        if value is not None:
            unseen_polies_removed = value
            continue
        match = re.match(r"^Added\s+(\d+)\s+verts for T's\s*$", line, re.IGNORECASE)
        if match:
            t_junction_vertices = int(match.group(1))
            continue
        value = _processor_log_int_after(line, "Number of input polies:")
        if value is not None:
            input_polies = value
            continue
        value = _processor_log_int_after(line, "Number of input vertices:")
        if value is not None:
            input_vertices = value
            continue
        value = _processor_log_int_after(line, "Number of output polies:")
        if value is not None:
            output_polies = value
            continue
        value = _processor_log_int_after(line, "Number of output vertices:")
        if value is not None:
            output_vertices = value
            continue
        value = _processor_log_int_after(line, "Lightmap data size:")
        if value is not None:
            lightmap_data_size = value
            continue
        value = _processor_log_int_after(line, "Number of objects:")
        if value is not None:
            object_count = value
            continue

    return BlackBoxProcessorLogSummary(
        path=absolute,
        status="loaded",
        processing_path=processing_path,
        world_tree_nodes=world_tree_nodes,
        world_tree_depth=world_tree_depth,
        tree_depth=tree_depth,
        runtime_minutes=runtime_minutes,
        lightmap_grid_size=lightmap_grid_size,
        btw_poly_split_count=btw_poly_splits,
        joined_polygon_count=joined_polies_left,
        joined_removed_polygon_count=joined_polies_removed,
        problem_brush_count=problem_brushes,
        unseen_removed_polygon_count=unseen_polies_removed,
        t_junction_vertex_count=t_junction_vertices,
        input_polygon_count=input_polies,
        input_vertex_count=input_vertices,
        output_polygon_count=output_polies,
        output_vertex_count=output_vertices,
        lightmap_data_size=lightmap_data_size,
        object_count=object_count,
        model_polygon_counts=tuple(model_counts),
        warning_counts=dict(sorted(warning_counts.items())),
        warnings=tuple(_unique_text(warnings)),
    )


def _processor_log_int_after(line: str, prefix: str) -> Optional[int]:
    if not line.startswith(prefix):
        return None
    match = re.search(r"-?\d+", line[len(prefix):])
    return int(match.group(0)) if match else None


def _processor_log_float_after(line: str, prefix: str) -> Optional[float]:
    if not line.startswith(prefix):
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", line[len(prefix):])
    return float(match.group(0)) if match else None


def _file_signature(path: str) -> Optional[Tuple[int, int]]:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return int(stat.st_size), int(stat.st_mtime_ns)


def _subprocess_output_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _binary_ascii_strings(data: bytes, *, min_length: int = 4) -> List[str]:
    text = data.decode("latin-1", errors="ignore")
    return [
        item
        for item in re.split(r"[^\x20-\x7e]+", text)
        if len(item) >= int(min_length)
    ]


def _terrain_source_coverage_items(
    terrain: object,
    *,
    ignored_textures: Sequence[str],
) -> Tuple[Tuple[int, Tuple[float, float, float], Tuple[float, float, float], str, Tuple[Tuple[float, float], ...]], ...]:
    return tuple(
        (
            item.polygon_index,
            item.bounds_min,
            item.bounds_max,
            item.texture_name,
            item.xz_points,
        )
        for item in terrain_reconstruction.terrain_coverage_items(
            terrain,
            ignored_textures=ignored_textures,
            require_texture=True,
        )
    )


def _generated_ed_terrain_coverage_items(
    scene: object,
    *,
    source_texture_names: Sequence[str],
    ignored_textures: Sequence[str],
) -> Tuple[terrain_reconstruction.GeneratedTerrainCoverageItem, ...]:
    return terrain_reconstruction.generated_terrain_coverage_items(
        scene,
        source_texture_names=source_texture_names,
        ignored_textures=ignored_textures,
    )


def _texture_count_pairs(values: Iterable[str]) -> Tuple[Tuple[str, int], ...]:
    counts: Dict[str, int] = {}
    display_names: Dict[str, str] = {}
    for value in values:
        text = str(value)
        key = text.lower()
        counts[key] = counts.get(key, 0) + 1
        display_names.setdefault(key, text)
    return tuple(
        (display_names[key], count)
        for key, count in sorted(counts.items(), key=lambda pair: (-pair[1], display_names[pair[0]].lower()))
    )


def _terrain_cutout_coverage_items(
    terrain: object,
    *,
    ignored_textures: Sequence[str],
) -> Tuple[terrain_reconstruction.TerrainCoverageItem, ...]:
    return terrain_reconstruction.terrain_coverage_items(
        terrain,
        ignored_textures=ignored_textures,
        require_texture=False,
    )


def _terrain_cutout_coverage_candidate(
    cluster: Sequence[terrain_reconstruction.TerrainCutoutModelInfo],
    *,
    cluster_index: int,
    terrain_items: Sequence[terrain_reconstruction.TerrainCoverageItem],
    sample_grid: int,
    covered_cutout_missing_ratio: float,
    partial_cutout_missing_ratio: float,
) -> TerrainCutoutCoverageCandidate:
    min_x = min(item[2][0] for item in cluster)
    min_y = min(item[2][1] for item in cluster)
    min_z = min(item[2][2] for item in cluster)
    max_x = max(item[3][0] for item in cluster)
    max_y = max(item[3][1] for item in cluster)
    max_z = max(item[3][2] for item in cluster)
    sample_points = terrain_reconstruction.xz_rect_sample_points(min_x, max_x, min_z, max_z, sample_grid)
    texture_hits: Dict[str, int] = {}
    hit_count = 0
    missing_count = 0
    for sample_x, sample_z in sample_points:
        texture = terrain_reconstruction.terrain_coverage_point_texture_hit(sample_x, sample_z, terrain_items)
        if texture is None:
            missing_count += 1
        else:
            hit_count += 1
            texture_hits[texture] = texture_hits.get(texture, 0) + 1
    sample_count = len(sample_points)
    missing_ratio = float(missing_count) / float(sample_count) if sample_count else 1.0
    if sample_count <= 0:
        classification = "uncertain"
    elif missing_ratio >= float(covered_cutout_missing_ratio):
        classification = "covered_cutout"
    elif missing_ratio >= float(partial_cutout_missing_ratio):
        classification = "partial_cutout"
    else:
        classification = "terrain_present_under_models"
    names = tuple(item[1] for item in cluster)
    notes: List[str] = []
    if len(cluster) > 1:
        notes.append("candidate is a nearby-model cluster, useful for building-footprint cutouts")
    if classification == "covered_cutout":
        notes.append("most footprint samples have no playable Terrain0 polygon below the original model cluster")
    elif classification == "partial_cutout":
        notes.append("footprint samples mix Terrain0 coverage and gaps; inspect the generated ED visually")
    candidate_name = _safe_filename_component(names[0] if names else "cluster")
    texture_hit_pairs = tuple(sorted(texture_hits.items(), key=lambda item: (-item[1], item[0].lower())))
    return TerrainCutoutCoverageCandidate(
        candidate_id=f"cluster_{int(cluster_index):03d}_{candidate_name}",
        classification=classification,
        model_names=names,
        model_indices=tuple(int(item[0]) for item in cluster),
        bounds_min=(min_x, min_y, min_z),
        bounds_max=(max_x, max_y, max_z),
        footprint_area=max(0.0, (max_x - min_x) * (max_z - min_z)),
        sample_count=sample_count,
        missing_sample_count=missing_count,
        missing_ratio=missing_ratio,
        terrain_hit_count=hit_count,
        terrain_texture_hits=texture_hit_pairs,
        notes=tuple(notes),
    )


def _terrain_cutout_class_rank(classification: str) -> int:
    order = {
        "covered_cutout": 0,
        "partial_cutout": 1,
        "terrain_present_under_models": 2,
        "uncertain": 3,
    }
    return order.get(str(classification), 9)


def _command_text(command: Sequence[str]) -> str:
    return " ".join(_quote_command_arg(item) for item in command)


def _class_counts_text(counts: Dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(
        f"{name}={count}"
        for name, count in sorted(counts.items())
    )


def _composite_model_summary(model: object) -> PrefabSurrogateCompositeModelSummary:
    points = list(getattr(model, "points", []) or [])
    name = str(getattr(model, "name", "") or "WorldModel")
    point_count = len(points)
    polygon_count = len(getattr(model, "polygons", []) or [])
    texture_count = len(getattr(model, "texture_names", []) or [])
    if not points:
        return PrefabSurrogateCompositeModelSummary(
            name=name,
            point_count=point_count,
            polygon_count=polygon_count,
            texture_count=texture_count,
            notes=("model has no points",),
        )
    finite_points = [_safe_vec3(point) for point in points]
    mins = tuple(min(point[index] for point in finite_points) for index in range(3))
    maxs = tuple(max(point[index] for point in finite_points) for index in range(3))
    center = tuple((mins[index] + maxs[index]) * 0.5 for index in range(3))
    return PrefabSurrogateCompositeModelSummary(
        name=name,
        point_count=point_count,
        polygon_count=polygon_count,
        texture_count=texture_count,
        bounds_min=mins,  # type: ignore[arg-type]
        bounds_max=maxs,  # type: ignore[arg-type]
        center=center,  # type: ignore[arg-type]
    )


def _budgeted_physics_shell_source_polygon_count(
    parsed: object,
    physics_model_name: str,
    *,
    requested_source_polygon_count: int,
    generated_polygon_budget: int,
) -> int:
    requested = max(0, int(requested_source_polygon_count))
    budget = max(0, int(generated_polygon_budget))
    if requested <= 0 or budget <= 0:
        return 0
    model = terrain_semantics.model_by_name(
        tuple(getattr(parsed, "world_models", ()) or ()),
        str(physics_model_name or terrain_semantics.PHYSICS_BSP_MODEL),
    )
    if model is None:
        return requested
    points = tuple(_safe_vec3(point) for point in (getattr(model, "points", ()) or ()))
    candidates: List[Tuple[float, int, int]] = []
    for polygon_index, polygon in enumerate(getattr(model, "polygons", ()) or ()):
        indices = tuple(int(index) for index in (getattr(polygon, "vertex_indices", ()) or ()))
        if not (3 <= len(indices) <= 64) or any(index < 0 or index >= len(points) for index in indices):
            continue
        polygon_points = tuple(points[index] for index in indices)
        area = terrain_reconstruction.polygon_area(polygon_points)
        if not math.isfinite(area) or area <= 1.0e-4:
            continue
        candidates.append((float(area), int(polygon_index), len(indices) + 2))

    generated_count = 0
    source_count = 0
    for _area, _polygon_index, face_count in sorted(candidates, key=lambda item: (-item[0], item[1])):
        if source_count >= requested:
            break
        if generated_count + face_count > budget:
            break
        generated_count += face_count
        source_count += 1
    return source_count


def _auto_direct_root_composite_groups(
    models: Sequence[object],
    *,
    min_models: int,
    max_models: int,
    max_model_points: int,
    max_model_polygons: int,
    include_skyboxes: bool,
) -> List[Tuple[str, ...]]:
    by_key: Dict[str, List[str]] = {}
    for model in models:
        name = str(getattr(model, "name", "") or "")
        if not name or _composite_auto_model_blocked(name):
            continue
        try:
            is_skybox = bool(getattr(model, "is_skybox", lambda: False)())
        except Exception:
            is_skybox = False
        if is_skybox and not include_skyboxes:
            continue
        point_count = len(getattr(model, "points", []) or [])
        polygon_count = len(getattr(model, "polygons", []) or [])
        if point_count <= 0 or polygon_count <= 0:
            continue
        if point_count > int(max_model_points) or polygon_count > int(max_model_polygons):
            continue
        key = _direct_root_auto_group_key(name)
        if not key:
            continue
        by_key.setdefault(key, []).append(name)
    groups = [
        tuple(names[:max(0, int(max_models))])
        for _key, names in sorted(by_key.items())
        if len(names) >= int(min_models)
    ]
    groups.sort(key=lambda group: (len(group), group[0].lower()))
    return groups


def _direct_root_auto_group_key(name: str) -> str:
    text = str(name).strip()
    if not text:
        return ""
    lowered = text.lower()
    for suffix in ("left", "right"):
        if lowered.endswith(suffix) and len(text) > len(suffix):
            return lowered[:-len(suffix)]
    stripped = re.sub(r"\d+$", "", lowered)
    return stripped if stripped and stripped != lowered else ""


def _composite_auto_model_blocked(name: str) -> bool:
    lowered = str(name).lower()
    if (
        terrain_semantics.is_terrain_name(name)
        or terrain_semantics.is_physics_bsp_name(name)
        or terrain_semantics.is_vis_bsp_name(name)
    ):
        return True
    blocked_prefixes = (
        "skybox",
        "tod_sky",
        "aitrk",
        "perceptionbrush",
        "ocean",
    )
    return any(lowered.startswith(prefix) for prefix in blocked_prefixes)


def _composite_group_label(group: Sequence[str], index: int) -> str:
    if not group:
        return f"group_{int(index):02d}"
    key = _direct_root_auto_group_key(str(group[0]))
    if not key:
        key = "_".join(str(name) for name in group[:3])
    return _safe_filename_component(f"{int(index):02d}_{key}")


def _safe_vec3(value: object) -> Tuple[float, float, float]:
    try:
        x, y, z = value  # type: ignore[misc]
        return (float(x), float(y), float(z))
    except Exception:
        return (0.0, 0.0, 0.0)


def _is_vec3(value: object) -> bool:
    try:
        x, y, z = value  # type: ignore[misc]
        return all(math.isfinite(float(item)) for item in (x, y, z))
    except Exception:
        return False


def _vec3_text(value: Tuple[float, float, float]) -> str:
    return "(" + ", ".join(f"{float(item):.2f}" for item in value) + ")"


def _safe_filename_component(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    text = text.strip("._")
    return text[:80] or "item"


def _quote_command_arg(value: object) -> str:
    text = str(value)
    if not text or any(ch.isspace() for ch in text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(int(minimum), min(int(maximum), int(value)))


def _candidate_failure_status(candidate: CompilerCandidate, blockers: Sequence[str]) -> str:
    if any("not found" in blocker.lower() for blocker in blockers):
        return "missing"
    if any("black-box" in blocker.lower() or "golden harness" in blocker.lower() for blocker in blockers):
        return "unverified"
    if candidate.expected_dat_version is not None and int(candidate.expected_dat_version) != 66:
        return "incompatible"
    if not candidate.can_compile_full_world:
        return "partial"
    return "incompatible"


def _black_box_match_status(status: str) -> bool:
    return status in {"compiled_and_compared", "captured_and_compared"}


def _black_box_difference_status(status: str) -> bool:
    return status in {
        "compiled_with_semantic_differences",
        "captured_with_semantic_differences",
    }


def _black_box_corpus_status(total: int, matched_count: int, failed_count: int) -> str:
    if total <= 0:
        return "no_runs"
    if matched_count == total:
        return "all_matched"
    if failed_count:
        return "completed_with_failures"
    return "completed_with_semantic_differences"


def _manual_validation_passed(validation: BlackBoxCompilerManualValidation) -> bool:
    if str(validation.status).lower() != "passed":
        return False
    return all(
        value is not False
        for value in (
            validation.fresh_load,
            validation.visuals_ok,
            validation.collision_ok,
        )
    )


def _manual_validation_failed(validation: BlackBoxCompilerManualValidation) -> bool:
    if str(validation.status).lower() == "failed":
        return True
    return any(
        value is False
        for value in (
            validation.fresh_load,
            validation.visuals_ok,
            validation.collision_ok,
        )
    )


def _extract_pascal_int_constant(text: str, name: str) -> Optional[int]:
    match = re.search(r"\b" + re.escape(name) + r"\s*=\s*(\d+)", text or "")
    return int(match.group(1)) if match else None


def _extract_pascal_string_constants(text: str, pattern: str) -> Tuple[str, ...]:
    return tuple(match.group(1) for match in re.finditer(pattern, text or ""))


def _mm9_legacy_brush_property_names() -> Tuple[str, ...]:
    try:
        from features.dat_editing import surrogate_ed

        return tuple(item[0] for item in surrogate_ed._LEGACY_BRUSH_OBJECT_PROPERTIES)
    except Exception:
        return (
            "Name",
            "Pos",
            "Rotation",
            "Solid",
            "Nonexistant",
            "Invisible",
            "Translucent",
            "SkyPortal",
            "FullyBright",
            "FlatShade",
            "GouraudShade",
            "LightMap",
            "Subdivide",
            "HullMaker",
            "AlwaysLightMap",
            "DirectionalLight",
            "Portal",
            "NoSnap",
            "SkyPan",
            "Additive",
            "Terrain",
            "TimeOfDay",
            "DetailLevel",
            "Effect",
            "EffectParam",
            "FrictionCoefficient",
        )


def _property_type_constants_match(text: str) -> bool:
    expected = {
        "PT_STRING": 0,
        "PT_VECTOR": 1,
        "PT_COLOR": 2,
        "PT_REAL": 3,
        "PT_FLAGS": 4,
        "PT_BOOL": 5,
        "PT_LONGINT": 6,
        "PT_ROTATION": 7,
    }
    return all(
        re.search(r"\b" + re.escape(name) + r"\s*=\s*" + str(value) + r"\b", text or "")
        for name, value in expected.items()
    )


def _unique_text(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        text = str(value)
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
