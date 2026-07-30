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
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

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

_PHYSICS_SHELL_COVERAGE_ROLES = terrain_reconstruction.PHYSICS_SHELL_COVERAGE_ROLES
ANSKRAMKEEP_BACK_START_POINT = (0.0, -104.0, 16.0)


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


DAT_NATIVE_OBJECT_SOURCE_ORACLE_CLASSES: Tuple[str, ...] = (
    "Door",
    "RotatingDoor",
    "AIRail",
    "TOD_Sky",
    "AmbientSound",
    "Trigger",
    "ExitTrigger",
    "PortalTrigger",
    "Prop",
    "Barrel",
    "BonePile",
    "Cauldron",
    "Cookpot",
    "StatStone",
    "WallTorch",
    "Fire",
    "CandleProp",
    "Brazier",
    "TreasureChest",
    "PropDamager",
    "DestructableProp",
    "DestructableBrush",
)


@dataclass(frozen=True)
class DatNativeObjectClassComparison:
    class_name: str
    dat_count: int = 0
    source_count: int = 0
    generated_count: int = 0
    matched_name_count: int = 0
    source_generated_matched_name_count: int = 0
    dat_only_names: Tuple[str, ...] = ()
    source_only_names: Tuple[str, ...] = ()
    generated_only_names: Tuple[str, ...] = ()
    generated_missing_names: Tuple[str, ...] = ()
    dat_property_keys: Tuple[str, ...] = ()
    source_property_keys: Tuple[str, ...] = ()
    generated_property_keys: Tuple[str, ...] = ()
    property_mismatch_count: int = 0
    source_generated_property_mismatch_count: int = 0
    property_mismatch_names: Tuple[str, ...] = ()
    source_generated_property_mismatch_names: Tuple[str, ...] = ()
    status: str = "unknown"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DatNativeObjectComparisonReport:
    status: str
    source_dat_path: str
    source_ed_path: str = ""
    generated_ed_path: str = ""
    class_names: Tuple[str, ...] = ()
    dat_object_count: int = 0
    source_object_count: int = 0
    generated_object_count: int = 0
    classes: Tuple[DatNativeObjectClassComparison, ...] = ()
    notes: Tuple[str, ...] = ()


DEFAULT_REGRESSION_MATRIX_LEVELS: Tuple[str, ...] = (
    "BOOTCAMP",
    "DOOKSCASTLE",
    "ANSKRAMKEEP",
    "BATHHOUSE",
    "DRAGONSTADIUM",
    "ISLEOFASHES",
)


@dataclass(frozen=True)
class DatToEdRegressionMatrixEntry:
    stem: str
    status: str
    dat_path: str = ""
    source_ed_path: str = ""
    model_count: int = 0
    polygon_count: int = 0
    terrain_model_count: int = 0
    physics_polygon_count: int = 0
    dat_object_count: int = 0
    source_object_count: int = 0
    helper_model_counts: Dict[str, int] = field(default_factory=dict)
    dat_native_status: str = "unknown"
    collision_helper_status: str = "unknown"
    trigger_helper_status: str = "unknown"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DatToEdRegressionMatrixReport:
    status: str
    worlds_dir: str
    levels: Tuple[str, ...] = ()
    entries: Tuple[DatToEdRegressionMatrixEntry, ...] = ()
    ready_count: int = 0
    inventory_only_count: int = 0
    missing_count: int = 0
    failed_count: int = 0
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
    physics_shell_focus_points: Tuple[Tuple[float, float, float], ...] = ()
    physics_shell_focus_radius: float = 0.0
    physics_shell_focus_budget: int = 0
    physics_shell_focus_seed_radius: float = 0.0
    include_door_objects: bool = False
    door_source_ed_path: str = ""
    door_behavior_context: str = ""
    include_airail_objects: bool = False
    airail_source_ed_path: str = ""
    include_sky_objects: bool = False
    sky_source_ed_path: str = ""
    include_sky_marker_brushes: bool = False
    include_sky_marker_residue_brushes: bool = False
    sky_marker_residue_reference_dat_path: str = ""
    include_sound_objects: bool = False
    sound_source_ed_path: str = ""
    include_gameplay_trigger_objects: bool = False
    gameplay_trigger_source_ed_path: str = ""
    include_static_prop_objects: bool = False
    static_prop_source_ed_path: str = ""
    include_low_risk_behavior_prop_objects: bool = False
    low_risk_behavior_prop_source_ed_path: str = ""
    include_wall_torch_objects: bool = False
    wall_torch_source_ed_path: str = ""
    include_fire_objects: bool = False
    fire_source_ed_path: str = ""
    include_candle_prop_objects: bool = False
    candle_prop_source_ed_path: str = ""
    include_brazier_objects: bool = False
    brazier_source_ed_path: str = ""
    include_treasure_chest_objects: bool = False
    treasure_chest_source_ed_path: str = ""
    include_prop_damager_objects: bool = False
    prop_damager_source_ed_path: str = ""
    include_destructable_prop_objects: bool = False
    destructable_prop_source_ed_path: str = ""
    include_destructable_brush_objects: bool = False
    include_collision_helper_objects: bool = False
    include_collision_helper_brushes: bool = False
    collision_helper_source_ed_path: str = ""
    include_trigger_helper_objects: bool = False
    include_trigger_helper_brushes: bool = False
    trigger_helper_source_ed_path: str = ""
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
    physics_shell_source_coverage_manifest_path: str = ""
    physics_shell_source_coverage: Optional["PhysicsShellSourceCoverageReport"] = None
    manual_steps: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()
    physics_shell_packing_mode: str = "balanced"
    physics_shell_packing_source_polygon_count: int = 0
    physics_shell_packing_generated_brush_count: int = 0
    physics_shell_packing_generated_face_count: int = 0
    physics_shell_packing_weighted_value: float = 0.0
    physics_shell_packing_role_weights: Tuple[Tuple[str, float], ...] = ()
    physics_shell_packing_playable_importance_weight: float = 0.0
    physics_shell_stair_assembly_indices: Tuple[int, ...] = ()
    physics_shell_selected_stair_assembly_indices: Tuple[int, ...] = ()
    physics_shell_rejected_stair_assembly_indices: Tuple[int, ...] = ()
    physics_shell_packing_comparison: Optional[
        terrain_reconstruction.PhysicsShellPackingComparison
    ] = None
    preflight_generated_brush_count: int = 0
    preflight_generated_polygon_count: int = 0
    preflight_extra_brush_count: int = 0
    preflight_extra_polygon_count: int = 0
    physics_shell_protected_void_count: int = 0
    physics_shell_protected_roles: Tuple[str, ...] = ()
    preflight_sky_marker_brush_count: int = 0
    preflight_sky_marker_polygon_count: int = 0
    preflight_sky_marker_point_count: int = 0
    stage_timings_seconds: Tuple[Tuple[str, float], ...] = ()


@dataclass(frozen=True)
class PhysicsShellPackingExperimentReport:
    status: str
    source_dat_path: str
    work_dir: str
    output_stem: str = "physics_shell_packing"
    physics_shell_model_name: str = "PhysicsBSP"
    physics_shell_name_prefix: str = "PhysicsShell"
    balanced: Optional[FullWorldSkeletonAcceptanceReport] = None
    cost_aware: Optional[FullWorldSkeletonAcceptanceReport] = None
    comparison: Optional[terrain_reconstruction.PhysicsShellPackingComparison] = None
    balanced_manifest_path: str = ""
    cost_aware_manifest_path: str = ""
    experiment_manifest_path: str = ""
    blockers: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PhysicsShellPackingExperimentValidationReport:
    status: str
    experiment_manifest_path: str
    validation_manifest_path: str = ""
    balanced_compiled_dat_path: str = ""
    cost_aware_compiled_dat_path: str = ""
    balanced_processor_log_path: str = ""
    cost_aware_processor_log_path: str = ""
    balanced: Optional["FullWorldSkeletonCompiledValidationReport"] = None
    cost_aware: Optional["FullWorldSkeletonCompiledValidationReport"] = None
    balanced_source_coverage: Optional["PhysicsShellSourceCoverageReport"] = None
    cost_aware_source_coverage: Optional["PhysicsShellSourceCoverageReport"] = None
    balanced_problem_brush_count: int = 0
    cost_aware_problem_brush_count: int = 0
    balanced_warning_count: int = 0
    cost_aware_warning_count: int = 0
    balanced_physics_polygon_count: int = 0
    cost_aware_physics_polygon_count: int = 0
    balanced_retained_source_polygon_count: int = 0
    cost_aware_retained_source_polygon_count: int = 0
    balanced_lost_source_polygon_count: int = 0
    cost_aware_lost_source_polygon_count: int = 0
    balanced_retained_source_area: float = 0.0
    cost_aware_retained_source_area: float = 0.0
    recommended_mode: str = "undetermined"
    manual_comparison_complete: bool = False
    blockers: Tuple[str, ...] = ()
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
    include_airail_semantics: bool = False
    include_sky_semantics: bool = False
    include_sound_semantics: bool = False
    include_collision_semantics: bool = False
    include_trigger_semantics: bool = False
    total_model_count: int = 0
    selected_model_count: int = 0
    terrain_support_source_count: int = 0
    physics_shell_source_count: int = 0
    helper_semantic_source_count: int = 0
    excluded_model_count: int = 0
    total_point_count: int = 0
    total_polygon_count: int = 0
    selected_point_count: int = 0
    selected_polygon_count: int = 0
    status_counts: Dict[str, int] = field(default_factory=dict)
    helper_only_exclusions_by_role: Dict[str, Dict[str, int]] = field(default_factory=dict)
    helper_semantic_sources_by_role: Dict[str, Dict[str, int]] = field(default_factory=dict)
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
    helper_reference_dat_path: str = ""
    processor_log_paths: Tuple[str, ...] = ()
    start_point: Optional[Tuple[float, float, float]] = None
    move_player_to_floor: Optional[bool] = None
    physics_floor_y: Optional[float] = None
    physics_floor_drop: Optional[float] = None
    max_start_floor_drop: float = 256.0
    dat: Optional[DatOutputSemanticSummary] = None
    helper_leakage: Optional["CompiledDatHelperLeakageReport"] = None
    processor_logs: Tuple[BlackBoxProcessorLogSummary, ...] = ()
    stage_timings_seconds: Tuple[Tuple[str, float], ...] = ()
    manual_validation: BlackBoxCompilerManualValidation = field(default_factory=BlackBoxCompilerManualValidation)
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledDatHelperModelSummary:
    model_name: str
    model_kind: str
    polygon_count: int = 0
    helper_polygon_count: int = 0
    helper_roles: Dict[str, int] = field(default_factory=dict)
    helper_textures: Dict[str, int] = field(default_factory=dict)
    status: str = "no_helper_textures"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledDatHelperRoleComparison:
    role: str
    status: str
    compiled_total: int = 0
    reference_total: int = 0
    compiled_by_model_kind: Dict[str, int] = field(default_factory=dict)
    reference_by_model_kind: Dict[str, int] = field(default_factory=dict)
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledDatHelperLeakageReport:
    status: str
    compiled_dat_path: str
    reference_dat_path: str = ""
    compiled_model_count: int = 0
    reference_model_count: int = 0
    compiled_total_helper_polygon_count: int = 0
    reference_total_helper_polygon_count: int = 0
    compiled_visibility_helper_polygon_count: int = 0
    reference_visibility_helper_polygon_count: int = 0
    compiled_terrain_helper_polygon_count: int = 0
    reference_terrain_helper_polygon_count: int = 0
    compiled_world_model_helper_polygon_count: int = 0
    reference_world_model_helper_polygon_count: int = 0
    model_summaries: Tuple[CompiledDatHelperModelSummary, ...] = ()
    reference_model_summaries: Tuple[CompiledDatHelperModelSummary, ...] = ()
    role_comparisons: Tuple[CompiledDatHelperRoleComparison, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AnskramkeepPhysicsShellRetestMetric:
    metric: str
    status: str
    previous: str = ""
    current: str = ""
    delta: str = ""
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AnskramkeepPhysicsShellRetestReport:
    status: str
    source_dat_path: str
    generated_ed_path: str = ""
    work_dir: str = ""
    reference_processor_log_path: str = ""
    current_processor_log_path: str = ""
    acceptance: Optional["FullWorldSkeletonAcceptanceReport"] = None
    reference_processor_log: Optional[BlackBoxProcessorLogSummary] = None
    current_processor_log: Optional[BlackBoxProcessorLogSummary] = None
    manual_validation: BlackBoxCompilerManualValidation = field(default_factory=BlackBoxCompilerManualValidation)
    comparisons: Tuple[AnskramkeepPhysicsShellRetestMetric, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AirailOracleObject:
    name: str
    class_name: str = "AIRail"
    pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class AirailRailBrushOracle:
    name: str
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    rail_face_count: int = 0


@dataclass(frozen=True)
class AirailReconstructionCandidate:
    source_model_name: str
    source_model_index: int
    polygon_count: int = 0
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    nearest_airail_name: str = ""
    nearest_airail_distance: Optional[float] = None
    nearest_rail_brush_name: str = ""
    nearest_rail_brush_distance: Optional[float] = None
    status: str = "pending_source_oracle"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AirailReconstructionReport:
    status: str
    source_dat_path: str
    source_ed_path: str = ""
    source_helper_model_count: int = 0
    source_helper_polygon_count: int = 0
    source_airail_object_count: int = 0
    source_rail_brush_count: int = 0
    generated_object_count: int = 0
    skipped_candidate_count: int = 0
    ambiguous_candidate_count: int = 0
    candidates: Tuple[AirailReconstructionCandidate, ...] = ()
    source_airail_objects: Tuple[AirailOracleObject, ...] = ()
    source_rail_brushes: Tuple[AirailRailBrushOracle, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SkyObjectOracle:
    name: str
    class_name: str
    pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    property_count: int = 0


@dataclass(frozen=True)
class SkyMarkerBrushOracle:
    name: str
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    sky_face_count: int = 0


@dataclass(frozen=True)
class SkyMarkerCompiledResidueMatch:
    compiled_model_name: str
    compiled_model_kind: str
    compiled_model_index: int
    compiled_polygon_index: int
    compiled_vertex_count: int = 0
    compiled_center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    compiled_normal: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    source_brush_name: str = ""
    source_model_index: int = -1
    source_face_index: int = -1
    source_vertex_count: int = 0
    source_center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    source_normal: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    source_brush_flags: Tuple[str, ...] = ()
    source_texture_flags: Optional[int] = None
    source_surface_flags: Optional[int] = None
    center_distance: Optional[float] = None
    plane_distance: Optional[float] = None
    normal_dot: Optional[float] = None
    status: str = "unmatched"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SkyMarkerSourceFaceCohortSummary:
    cohort: str
    source_face_count: int = 0
    source_brush_count: int = 0
    orientation_counts: Dict[str, int] = field(default_factory=dict)
    brush_flag_counts: Dict[str, int] = field(default_factory=dict)
    brush_flag_set_counts: Dict[str, int] = field(default_factory=dict)
    texture_flag_counts: Dict[str, int] = field(default_factory=dict)
    surface_flag_counts: Dict[str, int] = field(default_factory=dict)
    vertex_count_counts: Dict[str, int] = field(default_factory=dict)
    center_bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    center_bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    nearest_world_geometry_distance_min: Optional[float] = None
    nearest_world_geometry_distance_median: Optional[float] = None
    nearest_world_geometry_distance_average: Optional[float] = None
    nearest_world_geometry_distance_max: Optional[float] = None


@dataclass(frozen=True)
class SkyMarkerResidueRuleCandidate:
    rule_name: str
    selected_source_face_count: int = 0
    matched_source_face_count: int = 0
    unmatched_source_face_count: int = 0
    missed_matched_source_face_count: int = 0
    precision: Optional[float] = None
    recall: Optional[float] = None
    status: str = "heuristic_only"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SkyHelperReconstructionCandidate:
    source_model_name: str
    source_model_index: int
    helper_roles: Dict[str, int] = field(default_factory=dict)
    pure_helper_model: bool = False
    polygon_count: int = 0
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    status: str = "source_visibility_evidence"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SkyHelperReconstructionReport:
    status: str
    source_dat_path: str
    source_ed_path: str = ""
    source_helper_model_count: int = 0
    source_helper_polygon_count: int = 0
    source_sky_object_count: int = 0
    source_sky_marker_brush_count: int = 0
    source_sky_marker_face_count: int = 0
    generated_object_count: int = 0
    pure_helper_model_count: int = 0
    candidates: Tuple[SkyHelperReconstructionCandidate, ...] = ()
    source_sky_objects: Tuple[SkyObjectOracle, ...] = ()
    source_sky_marker_brushes: Tuple[SkyMarkerBrushOracle, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SkyMarkerCompiledResidueReport:
    status: str
    source_ed_path: str
    compiled_dat_path: str
    generated_compiled_dat_path: str = ""
    source_sky_marker_brush_count: int = 0
    source_sky_marker_face_count: int = 0
    source_brush_flag_counts: Dict[str, int] = field(default_factory=dict)
    compiled_sky_visibility_polygon_count: int = 0
    compiled_physics_sky_visibility_polygon_count: int = 0
    compiled_visibility_sky_visibility_polygon_count: int = 0
    compiled_terrain_sky_visibility_polygon_count: int = 0
    compiled_world_model_sky_visibility_polygon_count: int = 0
    generated_sky_visibility_polygon_count: int = 0
    generated_physics_sky_visibility_polygon_count: int = 0
    generated_visibility_sky_visibility_polygon_count: int = 0
    generated_terrain_sky_visibility_polygon_count: int = 0
    generated_world_model_sky_visibility_polygon_count: int = 0
    source_to_compiled_ratio: Optional[float] = None
    compiled_residue_match_count: int = 0
    compiled_residue_unmatched_count: int = 0
    matched_source_sky_marker_face_count: int = 0
    matched_source_sky_marker_brush_count: int = 0
    matched_source_brush_flag_counts: Dict[str, int] = field(default_factory=dict)
    max_match_center_distance: Optional[float] = None
    max_match_plane_distance: Optional[float] = None
    min_match_normal_dot: Optional[float] = None
    source_face_matched_summary: Optional[SkyMarkerSourceFaceCohortSummary] = None
    source_face_unmatched_summary: Optional[SkyMarkerSourceFaceCohortSummary] = None
    residue_rule_candidates: Tuple[SkyMarkerResidueRuleCandidate, ...] = ()
    source_sky_marker_brushes: Tuple[SkyMarkerBrushOracle, ...] = ()
    compiled_residue_matches: Tuple[SkyMarkerCompiledResidueMatch, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SkyMarkerResidueCompileAuditReport:
    status: str
    source_dat_path: str
    source_ed_path: str
    reference_dat_path: str
    work_dir: str = ""
    generated_ed_path: str = ""
    compiled_dat_path: str = ""
    processor_log_paths: Tuple[str, ...] = ()
    acceptance: Optional[FullWorldSkeletonAcceptanceReport] = None
    residue_report: Optional[SkyMarkerCompiledResidueReport] = None
    helper_leakage: Optional[CompiledDatHelperLeakageReport] = None
    processor_logs: Tuple[BlackBoxProcessorLogSummary, ...] = ()
    manual_steps: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SoundObjectOracle:
    name: str
    class_name: str = "AmbientSound"
    pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    filename: str = ""
    outer_radius: Optional[float] = None
    inner_radius: Optional[float] = None
    property_count: int = 0


@dataclass(frozen=True)
class SoundHelperReconstructionCandidate:
    source_model_name: str
    source_model_index: int
    helper_roles: Dict[str, int] = field(default_factory=dict)
    pure_helper_model: bool = False
    polygon_count: int = 0
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    status: str = "source_sound_evidence"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SoundHelperReconstructionReport:
    status: str
    source_dat_path: str
    source_ed_path: str = ""
    source_helper_model_count: int = 0
    source_helper_polygon_count: int = 0
    source_sound_object_count: int = 0
    generated_object_count: int = 0
    pure_helper_model_count: int = 0
    candidates: Tuple[SoundHelperReconstructionCandidate, ...] = ()
    source_sound_objects: Tuple[SoundObjectOracle, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class GameplayTriggerObjectOracle:
    name: str
    class_name: str
    pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    dims: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    target_count: int = 0
    destination_world: str = ""
    portal_name: str = ""
    property_count: int = 0


@dataclass(frozen=True)
class GameplayTriggerReconstructionReport:
    status: str
    source_dat_path: str
    source_ed_path: str = ""
    source_trigger_object_count: int = 0
    generated_object_count: int = 0
    class_counts: Dict[str, int] = field(default_factory=dict)
    target_reference_count: int = 0
    destination_worlds: Tuple[str, ...] = ()
    portal_names: Tuple[str, ...] = ()
    source_trigger_objects: Tuple[GameplayTriggerObjectOracle, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class StaticPropObjectOracle:
    name: str
    class_name: str = "Prop"
    pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    filename: str = ""
    skin: str = ""
    scale: Optional[float] = None
    visible: Optional[bool] = None
    solid: Optional[bool] = None
    move_to_floor: Optional[bool] = None
    property_count: int = 0


@dataclass(frozen=True)
class StaticPropReconstructionReport:
    status: str
    source_dat_path: str
    source_ed_path: str = ""
    source_prop_object_count: int = 0
    generated_object_count: int = 0
    unique_model_count: int = 0
    unique_skin_count: int = 0
    solid_count: int = 0
    move_to_floor_count: int = 0
    top_filenames: Tuple[Tuple[str, int], ...] = ()
    source_prop_objects: Tuple[StaticPropObjectOracle, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BehaviorPropObjectOracle:
    name: str
    class_name: str
    pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    filename: str = ""
    skin: str = ""
    scale: Optional[float] = None
    visible: Optional[bool] = None
    solid: Optional[bool] = None
    move_to_floor: Optional[bool] = None
    semantic_roles: Tuple[str, ...] = ()
    risk_level: str = "unknown"
    on: Optional[bool] = None
    fire: Optional[bool] = None
    sound_file: str = ""
    sound_radius: Optional[float] = None
    light_min_radius: Optional[float] = None
    light_max_radius: Optional[float] = None
    locked: Optional[bool] = None
    trigger_target: str = ""
    damage_trigger_target: str = ""
    hit_points: Optional[float] = None
    property_count: int = 0


@dataclass(frozen=True)
class BehaviorPropClassSummary:
    class_name: str
    object_count: int = 0
    unique_model_count: int = 0
    solid_count: int = 0
    move_to_floor_count: int = 0
    semantic_role_counts: Dict[str, int] = field(default_factory=dict)
    risk_level_counts: Dict[str, int] = field(default_factory=dict)
    copy_pass_key: str = ""
    copy_pass_status: str = "not_implemented"
    validation_status: str = "needs_class_specific_copy_pass"
    sample_names: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BehaviorPropReconstructionReport:
    status: str
    source_dat_path: str
    source_ed_path: str = ""
    source_behavior_prop_object_count: int = 0
    copy_candidate_count: int = 0
    class_counts: Dict[str, int] = field(default_factory=dict)
    semantic_role_counts: Dict[str, int] = field(default_factory=dict)
    risk_level_counts: Dict[str, int] = field(default_factory=dict)
    unique_model_count: int = 0
    unique_skin_count: int = 0
    solid_count: int = 0
    move_to_floor_count: int = 0
    top_filenames: Tuple[Tuple[str, int], ...] = ()
    class_summaries: Tuple[BehaviorPropClassSummary, ...] = ()
    source_behavior_prop_objects: Tuple[BehaviorPropObjectOracle, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CollisionHelperOracleObject:
    name: str
    class_name: str
    pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    property_count: int = 0


@dataclass(frozen=True)
class CollisionHelperBrushOracle:
    name: str
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    helper_roles: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CollisionHelperReconstructionCandidate:
    source_model_name: str
    source_model_index: int
    target_class_name: str = ""
    helper_roles: Dict[str, int] = field(default_factory=dict)
    polygon_count: int = 0
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    matched_object_name: str = ""
    matched_object_class_name: str = ""
    matched_object_distance: Optional[float] = None
    nearest_helper_brush_name: str = ""
    nearest_helper_brush_distance: Optional[float] = None
    status: str = "pending_source_oracle"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CollisionHelperReconstructionReport:
    status: str
    source_dat_path: str
    source_ed_path: str = ""
    source_helper_model_count: int = 0
    source_helper_polygon_count: int = 0
    source_object_count: int = 0
    source_helper_brush_count: int = 0
    matched_object_count: int = 0
    skipped_candidate_count: int = 0
    candidates: Tuple[CollisionHelperReconstructionCandidate, ...] = ()
    source_objects: Tuple[CollisionHelperOracleObject, ...] = ()
    source_helper_brushes: Tuple[CollisionHelperBrushOracle, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TriggerHelperOracleObject:
    name: str
    class_name: str
    pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    portal_name: str = ""
    property_count: int = 0


@dataclass(frozen=True)
class TriggerHelperBrushOracle:
    name: str
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    trigger_face_count: int = 0


@dataclass(frozen=True)
class TriggerHelperReconstructionCandidate:
    source_model_name: str
    source_model_index: int
    helper_roles: Dict[str, int] = field(default_factory=dict)
    polygon_count: int = 0
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    matched_object_name: str = ""
    matched_object_class_name: str = ""
    matched_object_portal_name: str = ""
    matched_object_distance: Optional[float] = None
    nearest_helper_brush_name: str = ""
    nearest_helper_brush_distance: Optional[float] = None
    status: str = "pending_source_oracle"
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TriggerHelperReconstructionReport:
    status: str
    source_dat_path: str
    source_ed_path: str = ""
    source_helper_model_count: int = 0
    source_helper_polygon_count: int = 0
    source_object_count: int = 0
    source_helper_brush_count: int = 0
    matched_object_count: int = 0
    skipped_candidate_count: int = 0
    candidates: Tuple[TriggerHelperReconstructionCandidate, ...] = ()
    source_objects: Tuple[TriggerHelperOracleObject, ...] = ()
    source_helper_brushes: Tuple[TriggerHelperBrushOracle, ...] = ()
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
class PhysicsShellSourceCoverageRoleSummary:
    role: str
    source_polygon_count: int = 0
    generated_polygon_count: int = 0
    uncovered_polygon_count: int = 0


@dataclass(frozen=True)
class PhysicsShellGeneratedBrushAttribution:
    brush_name: str
    source_model_name: str
    source_polygon_index: int
    role: str


@dataclass(frozen=True)
class PhysicsShellSourcePolygonDiagnostic:
    """Reasoned accounting for one source PhysicsBSP polygon."""

    source_polygon_index: int
    role: str
    status: str
    reason: str
    area: float = 0.0
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    generated_brush_names: Tuple[str, ...] = ()
    compiled_match_count: int = 0
    subset_role: str = ""
    subset_batch_index: int = -1
    subset_validation_status: str = "not_run"
    subset_problem_brush_count: Optional[int] = None
    subset_warning_count: int = 0
    loss_class: str = "unclassified"


@dataclass(frozen=True)
class PhysicsShellCoverageHotspot:
    """A ranked playable-region summary over source polygon diagnostics."""

    name: str
    anchor_kind: str
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    radius: float = 0.0
    source_polygon_count: int = 0
    emitted_polygon_count: int = 0
    actionable_missing_polygon_count: int = 0
    protected_polygon_count: int = 0
    invalid_polygon_count: int = 0
    source_area: float = 0.0
    emitted_area: float = 0.0
    actionable_missing_area: float = 0.0
    priority_score: float = 0.0
    role_counts: Tuple[Tuple[str, int], ...] = ()
    status_counts: Tuple[Tuple[str, int], ...] = ()
    top_missing_polygon_indices: Tuple[int, ...] = ()


@dataclass(frozen=True)
class PhysicsShellSourceCoverageReport:
    status: str
    source_dat_path: str
    generated_ed_path: str
    physics_model_name: str = "PhysicsBSP"
    source_polygon_count: int = 0
    classified_source_polygon_count: int = 0
    generated_source_polygon_count: int = 0
    uncovered_source_polygon_count: int = 0
    generated_unknown_polygon_count: int = 0
    compiled_dat_path: str = ""
    compiled_matched_source_polygon_count: int = 0
    compiled_unmatched_source_polygon_count: int = 0
    diagnostic_status_counts: Tuple[Tuple[str, int], ...] = ()
    loss_class_counts: Tuple[Tuple[str, int], ...] = ()
    source_polygon_diagnostics: Tuple[PhysicsShellSourcePolygonDiagnostic, ...] = ()
    coverage_hotspots: Tuple[PhysicsShellCoverageHotspot, ...] = ()
    stair_assemblies: Tuple[terrain_reconstruction.PhysicsShellStairAssembly, ...] = ()
    subset_plan_status: str = "not_supplied"
    subset_validation_status_counts: Tuple[Tuple[str, int], ...] = ()
    subset_failed_batch_count: int = 0
    role_summaries: Tuple[PhysicsShellSourceCoverageRoleSummary, ...] = ()
    generated_brush_attributions: Tuple[PhysicsShellGeneratedBrushAttribution, ...] = ()
    generated_source_polygon_indices: Tuple[int, ...] = ()
    generated_unknown_polygon_indices: Tuple[int, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()
    packing_mode: str = "balanced"


@dataclass(frozen=True)
class PhysicsShellSubsetPlanEntry:
    """One role/index subset to compile when bisecting Processor failures."""

    role: str
    batch_index: int
    polygon_indices: Tuple[int, ...] = ()
    generated_face_count: int = 0
    suggested_output_filename: str = ""
    processor_log_path: str = ""
    processor_log_status: str = "not_supplied"
    processor_problem_brush_count: Optional[int] = None
    processor_warning_count: int = 0
    validation_status: str = "not_run"


@dataclass(frozen=True)
class PhysicsShellSubsetPlan:
    status: str
    source_dat_path: str
    physics_model_name: str = "PhysicsBSP"
    work_dir: str = ""
    batch_size: int = 128
    generated_face_budget: int = 0
    source_polygon_count: int = 0
    valid_candidate_count: int = 0
    role_counts: Tuple[Tuple[str, int], ...] = ()
    processor_log_path: str = ""
    processor_log_status: str = "not_supplied"
    processor_problem_brush_count: Optional[int] = None
    processor_warning_count: int = 0
    entries: Tuple[PhysicsShellSubsetPlanEntry, ...] = ()
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


def _preflight_terrain_support_patch_cost(
    data: bytes,
    parsed: object,
    anchor_models: Sequence[object],
    *,
    surrogate_module: object,
    source_model_name: str,
    name_prefix: str,
    margin: float,
    selection_mode: str,
    radius: float,
    brush_mode: str,
    thickness: float,
    max_polygons: int,
    side_texture: str,
) -> Tuple[int, int, int, Tuple[str, ...], Optional[Tuple[object, ...]]]:
    """Measure normalized Terrain* support cost before shell budgeting.

    The support writer can group source polygons differently from the source
    DAT.  Calling the same Brush builder used for ED emission gives preflight
    the actual normalized Brush/face cost instead of treating each source
    polygon as an equal-cost triangle.
    """
    terrain = terrain_semantics.model_by_name(
        tuple(getattr(parsed, "world_models", ()) or ()),
        str(source_model_name or "Terrain0"),
    )
    if terrain is None:
        return 0, 0, 0, (
            f"Terrain support preflight could not find source model {source_model_name}; support cost is unmeasured.",
        ), None
    anchor_brushes = tuple(
        brush
        for model_index, model in enumerate(anchor_models)
        for brush, summary in (surrogate_module._model_to_legacy_brush(model, model_index),)
        if summary.status == "written" and brush is not None
    )
    if not anchor_brushes:
        return 0, 0, 0, (
            "Terrain support preflight had no anchor model points; support cost is unmeasured.",
        ), None
    try:
        patch_brushes, patch_summaries, placement = (
            surrogate_module._terrain_support_patch_brushes_for_brushes(
                data,
                anchor_brushes,
                parsed_world=parsed,
                source_model_name=source_model_name,
                name_prefix=name_prefix,
                margin=margin,
                selection_mode=selection_mode,
                radius=radius,
                brush_mode=brush_mode,
                thickness=thickness,
                max_polygons=max_polygons,
                side_texture=side_texture,
            )
        )
        brush_count = len(patch_brushes)
        polygon_count = sum(len(getattr(brush, "surfaces", ()) or ()) for brush in patch_brushes)
        point_count = sum(len(getattr(brush, "points", ()) or ()) for brush in patch_brushes)
        source_polygon_count = sum(int(item.polygon_count > 0) for item in patch_summaries)
        return brush_count, polygon_count, point_count, (
            f"Terrain support preflight measured {brush_count} normalized Brush(es), "
            f"{polygon_count} face(s), and {point_count} point(s) across {source_polygon_count} support group(s).",
        ), (patch_brushes, patch_summaries, placement)
    except Exception as exc:
        return 0, 0, 0, (
            f"Terrain support preflight could not measure normalized Brush cost: {exc}",
        ), None


def _preflight_sky_marker_brush_cost(
    *,
    surrogate_module: object,
    source_ed_path: str,
    include_sky_marker_brushes: bool,
    include_sky_marker_residue_brushes: bool,
    residue_reference_dat_path: str,
) -> Tuple[
    int,
    int,
    int,
    Tuple[str, ...],
    Optional[Tuple[object, ...]],
    Optional[Tuple[object, ...]],
]:
    """Measure diagnostic SkyMarker Brush overhead with the emission builders."""
    from features.dat_editing import legacy_ed_writer

    collections: List[Tuple[str, Sequence[object], Sequence[str]]] = []
    notes: List[str] = []
    sky_marker_bundle: Optional[Tuple[object, ...]] = None
    sky_marker_residue_bundle: Optional[Tuple[object, ...]] = None
    try:
        if include_sky_marker_brushes:
            sky_marker_bundle = tuple(
                surrogate_module._sky_marker_brushes_from_source_ed(source_ed_path)
            )
            brushes, _summaries, _properties, builder_notes = sky_marker_bundle
            collections.append(("SkyMarker", brushes, builder_notes))
        if include_sky_marker_residue_brushes:
            sky_marker_residue_bundle = tuple(
                surrogate_module._sky_marker_residue_brushes_from_source_ed(
                    source_ed_path,
                    reference_dat_path=residue_reference_dat_path,
                )
            )
            brushes, _summaries, _properties, builder_notes = sky_marker_residue_bundle
            collections.append(("SkyMarker residue", brushes, builder_notes))
    except Exception as exc:
        return 0, 0, 0, (
            f"SkyMarker preflight could not measure normalized Brush cost: {exc}",
        ), None, None

    brush_count = 0
    polygon_count = 0
    point_count = 0
    for label, brushes, builder_notes in collections:
        try:
            normalized_brushes = tuple(
                legacy_ed_writer.normalize_brush_points(brush)
                for brush in brushes
            )
        except Exception as exc:
            return 0, 0, 0, (
                f"{label} preflight could not normalize Brush cost: {exc}",
            ), None, None
        collection_brush_count = len(normalized_brushes)
        collection_polygon_count = sum(
            len(getattr(brush, "surfaces", ()) or ())
            for brush in normalized_brushes
        )
        collection_point_count = sum(
            len(getattr(brush, "points", ()) or ())
            for brush in normalized_brushes
        )
        brush_count += collection_brush_count
        polygon_count += collection_polygon_count
        point_count += collection_point_count
        notes.append(
            f"{label} preflight measured {collection_brush_count} normalized Brush(es), "
            f"{collection_polygon_count} face(s), and {collection_point_count} point(s)."
        )
        if not brushes and builder_notes:
            notes.append(str(tuple(builder_notes)[0]))
    return (
        brush_count,
        polygon_count,
        point_count,
        tuple(_unique_text(notes)),
        sky_marker_bundle,
        sky_marker_residue_bundle,
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
    physics_shell_packing_mode: str = "balanced",
    physics_shell_packing_role_weights: Optional[Mapping[str, float]] = None,
    physics_shell_packing_playable_importance_weight: float = 0.0,
    include_physics_shell_packing_comparison: bool = False,
    physics_shell_stair_assembly_indices: Sequence[int] = (),
    physics_shell_protected_bounds: Sequence[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = (),
    physics_shell_protected_roles: Sequence[str] = ("side_wall",),
    physics_shell_thickness: float = 16.0,
    physics_shell_side_texture: str = "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx",
    physics_shell_source_polygon_indices: Sequence[int] = (),
    physics_shell_focus_points: Sequence[object] = (),
    physics_shell_focus_radius: float = 0.0,
    physics_shell_focus_budget: int = 0,
    physics_shell_focus_seed_radius: float = 0.0,
    include_door_objects: bool = False,
    door_source_ed_path: str = "",
    include_airail_objects: bool = False,
    airail_source_ed_path: str = "",
    include_sky_objects: bool = False,
    sky_source_ed_path: str = "",
    include_sky_marker_brushes: bool = False,
    include_sky_marker_residue_brushes: bool = False,
    sky_marker_residue_reference_dat_path: str = "",
    include_sound_objects: bool = False,
    sound_source_ed_path: str = "",
    include_gameplay_trigger_objects: bool = False,
    gameplay_trigger_source_ed_path: str = "",
    include_static_prop_objects: bool = False,
    static_prop_source_ed_path: str = "",
    include_low_risk_behavior_prop_objects: bool = False,
    low_risk_behavior_prop_source_ed_path: str = "",
    include_wall_torch_objects: bool = False,
    wall_torch_source_ed_path: str = "",
    include_fire_objects: bool = False,
    fire_source_ed_path: str = "",
    include_candle_prop_objects: bool = False,
    candle_prop_source_ed_path: str = "",
    include_brazier_objects: bool = False,
    brazier_source_ed_path: str = "",
    include_treasure_chest_objects: bool = False,
    treasure_chest_source_ed_path: str = "",
    include_prop_damager_objects: bool = False,
    prop_damager_source_ed_path: str = "",
    include_destructable_prop_objects: bool = False,
    destructable_prop_source_ed_path: str = "",
    include_destructable_brush_objects: bool = False,
    include_collision_helper_objects: bool = False,
    include_collision_helper_brushes: bool = True,
    collision_helper_source_ed_path: str = "",
    include_trigger_helper_objects: bool = False,
    include_trigger_helper_brushes: bool = True,
    trigger_helper_source_ed_path: str = "",
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
    include_physics_shell_source_coverage: bool = False,
    compiled_dat_path: str = "",
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
    generation_started = time.monotonic()
    stage_started = generation_started
    stage_timings: List[Tuple[str, float]] = []

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
    compiled_dat = os.path.abspath(compiled_dat_path) if compiled_dat_path else ""
    source_door_ed = os.path.abspath(door_source_ed_path) if door_source_ed_path else ""
    source_airail_ed = os.path.abspath(airail_source_ed_path) if airail_source_ed_path else ""
    source_sky_ed = os.path.abspath(sky_source_ed_path) if sky_source_ed_path else ""
    sky_marker_residue_reference_dat = os.path.abspath(sky_marker_residue_reference_dat_path) if sky_marker_residue_reference_dat_path else ""
    source_sound_ed = os.path.abspath(sound_source_ed_path) if sound_source_ed_path else ""
    source_gameplay_trigger_ed = os.path.abspath(gameplay_trigger_source_ed_path) if gameplay_trigger_source_ed_path else ""
    source_static_prop_ed = os.path.abspath(static_prop_source_ed_path) if static_prop_source_ed_path else ""
    source_low_risk_behavior_prop_ed = os.path.abspath(low_risk_behavior_prop_source_ed_path) if low_risk_behavior_prop_source_ed_path else ""
    source_wall_torch_ed = os.path.abspath(wall_torch_source_ed_path) if wall_torch_source_ed_path else ""
    source_fire_ed = os.path.abspath(fire_source_ed_path) if fire_source_ed_path else ""
    source_candle_prop_ed = os.path.abspath(candle_prop_source_ed_path) if candle_prop_source_ed_path else ""
    source_brazier_ed = os.path.abspath(brazier_source_ed_path) if brazier_source_ed_path else ""
    source_treasure_chest_ed = os.path.abspath(treasure_chest_source_ed_path) if treasure_chest_source_ed_path else ""
    source_prop_damager_ed = os.path.abspath(prop_damager_source_ed_path) if prop_damager_source_ed_path else ""
    source_destructable_prop_ed = os.path.abspath(destructable_prop_source_ed_path) if destructable_prop_source_ed_path else ""
    source_collision_ed = os.path.abspath(collision_helper_source_ed_path) if collision_helper_source_ed_path else ""
    source_trigger_ed = os.path.abspath(trigger_helper_source_ed_path) if trigger_helper_source_ed_path else ""
    physics_shell_packing_mode_key = (
        str(physics_shell_packing_mode or "balanced").strip().lower().replace("-", "_")
    )
    if physics_shell_packing_mode_key not in {"balanced", "cost_aware"}:
        return FullWorldSkeletonAcceptanceReport(
            status="invalid_physics_shell_packing_mode",
            source_dat_path=source_dat,
            group_name=group_name,
            physics_shell_packing_mode=physics_shell_packing_mode_key,
            blockers=(
                "unsupported PhysicsBSP shell packing mode: "
                f"{physics_shell_packing_mode}; expected balanced or cost_aware",
            ),
        )
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
    physics_shell_source_report: Optional[PhysicsShellSourceCoverageReport] = None
    physics_shell_source_manifest_path = ""
    door_behavior_context = ""
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
            f"PhysicsBSP shell packing mode: {physics_shell_packing_mode_key}."
        )
        notes.append(
            f"PhysicsBSP shell polygon budget: {max(1, int(physics_shell_max_polygons))}; thickness={float(physics_shell_thickness):g}."
        )
        if physics_shell_packing_mode_key == "cost_aware":
            normalized_role_weights = terrain_reconstruction.normalized_physics_shell_role_weights(
                physics_shell_packing_role_weights
            )
            notes.append(
                "PhysicsBSP shell cost-aware role weights: "
                + ", ".join(f"{role}={weight:g}" for role, weight in normalized_role_weights)
                + "."
            )
            if max(0.0, float(physics_shell_packing_playable_importance_weight)) > 0.0:
                notes.append(
                    "PhysicsBSP shell cost-aware playable-importance weight: "
                    f"{max(0.0, float(physics_shell_packing_playable_importance_weight)):g}."
                )
        if physics_shell_protected_bounds:
            notes.append(
                "PhysicsBSP shell explicit protected void bounds: "
                f"{len(tuple(physics_shell_protected_bounds))}; roles="
                + ", ".join(str(role) for role in physics_shell_protected_roles)
                + "."
            )
    if include_door_objects:
        notes.append("Door/RotatingDoor object records will be copied from the source ED oracle when their Name matches a selected DAT world model.")
        notes.append(
            "Copied Door child Brushes replace matching selected model Brushes; preflight does not double-count them as extra Brush cost."
        )
        if source_door_ed:
            notes.append(f"Door source ED oracle: {source_door_ed}.")
        else:
            notes.append("Door source ED oracle is not configured; Door/RotatingDoor object nodes will be skipped.")
    if include_airail_objects:
        notes.append("AIRail object records will be generated from DAT aiRail helper models.")
        if source_airail_ed:
            notes.append(f"AIRail source ED oracle: {source_airail_ed}.")
        else:
            notes.append("AIRail source ED oracle is not configured; RailLink fields will use DAT fallback values.")
    if include_sky_objects:
        notes.append("Sky object records will be copied from the source ED oracle.")
        if source_sky_ed:
            notes.append(f"Sky source ED oracle: {source_sky_ed}.")
        else:
            notes.append("Sky source ED oracle is not configured; DAT-native sky object records will be used.")
    if include_sky_marker_brushes:
        notes.append("SkyMarker Brush records will be copied from the source ED oracle.")
        if source_sky_ed:
            notes.append(f"SkyMarker source ED oracle: {source_sky_ed}.")
        else:
            notes.append("SkyMarker source ED oracle is not configured; sky marker Brush records will be skipped.")
    if include_sky_marker_residue_brushes:
        notes.append("Diagnostic SkyMarker residue Brush records will be copied from source ED faces matched to a compiled DAT reference.")
        if source_sky_ed:
            notes.append(f"SkyMarker residue source ED oracle: {source_sky_ed}.")
        else:
            notes.append("SkyMarker residue source ED oracle is not configured; residue Brush records will be skipped.")
        if sky_marker_residue_reference_dat:
            notes.append(f"SkyMarker residue compiled DAT reference: {sky_marker_residue_reference_dat}.")
        else:
            notes.append("SkyMarker residue compiled DAT reference is not configured; residue Brush records will be skipped.")
    if include_sound_objects:
        notes.append("AmbientSound object records will be copied from the source ED oracle.")
        if source_sound_ed:
            notes.append(f"Sound source ED oracle: {source_sound_ed}.")
        else:
            notes.append("Sound source ED oracle is not configured; DAT-native AmbientSound records will be used.")
    if include_gameplay_trigger_objects:
        notes.append("Gameplay Trigger/ExitTrigger/PortalTrigger object records will be copied from the source ED oracle.")
        if source_gameplay_trigger_ed:
            notes.append(f"Gameplay trigger source ED oracle: {source_gameplay_trigger_ed}.")
        else:
            notes.append("Gameplay trigger source ED oracle is not configured; gameplay trigger object nodes will be skipped.")
    if include_static_prop_objects:
        notes.append("Static Prop object records will be copied from the source ED oracle.")
        if source_static_prop_ed:
            notes.append(f"Static prop source ED oracle: {source_static_prop_ed}.")
        else:
            notes.append("Static prop source ED oracle is not configured; Prop object nodes will be skipped.")
    if include_low_risk_behavior_prop_objects:
        notes.append("Low-risk behavior prop object records will be copied from the source ED oracle for physical-decor subclasses only.")
        if source_low_risk_behavior_prop_ed:
            notes.append(f"Low-risk behavior prop source ED oracle: {source_low_risk_behavior_prop_ed}.")
        else:
            notes.append("Low-risk behavior prop source ED oracle is not configured; Barrel/BonePile/Cauldron/Cookpot/StatStone object nodes will be skipped.")
    if include_wall_torch_objects:
        notes.append("WallTorch object records will be copied from the source ED oracle as an initially validated medium-risk light/fire/sound prop pass.")
        if source_wall_torch_ed:
            notes.append(f"WallTorch source ED oracle: {source_wall_torch_ed}.")
        else:
            notes.append("WallTorch source ED oracle is not configured; WallTorch object nodes will be skipped.")
    if include_fire_objects:
        notes.append("Fire object records will be copied from the source ED oracle as an initially validated standalone medium-risk light/fire/sound prop pass.")
        if source_fire_ed:
            notes.append(f"Fire source ED oracle: {source_fire_ed}.")
        else:
            notes.append("Fire source ED oracle is not configured; Fire object nodes will be skipped.")
    if include_candle_prop_objects:
        notes.append("CandleProp object records will be copied from the source ED oracle as an initially validated medium-risk light/model prop pass.")
        if source_candle_prop_ed:
            notes.append(f"CandleProp source ED oracle: {source_candle_prop_ed}.")
        else:
            notes.append("CandleProp source ED oracle is not configured; CandleProp object nodes will be skipped.")
    if include_brazier_objects:
        notes.append("Brazier object records will be copied from the source ED oracle as an initially validated medium-risk light/fire/sound/model prop pass.")
        if source_brazier_ed:
            notes.append(f"Brazier source ED oracle: {source_brazier_ed}.")
        else:
            notes.append("Brazier source ED oracle is not configured; Brazier object nodes will be skipped.")
    if include_treasure_chest_objects:
        notes.append("TreasureChest object records will be copied from the source ED oracle as an initially validated high-risk loot/trigger prop pass.")
        if source_treasure_chest_ed:
            notes.append(f"TreasureChest source ED oracle: {source_treasure_chest_ed}.")
        else:
            notes.append("TreasureChest source ED oracle is not configured; TreasureChest object nodes will be skipped.")
    if include_prop_damager_objects:
        notes.append("PropDamager object records will be copied from the source ED oracle as an initially validated high-risk damage prop pass.")
        if source_prop_damager_ed:
            notes.append(f"PropDamager source ED oracle: {source_prop_damager_ed}.")
        else:
            notes.append("PropDamager source ED oracle is not configured; PropDamager object nodes will be skipped.")
    if include_destructable_prop_objects:
        notes.append("DestructableProp object records will be copied from the source ED oracle as an explicit high-risk destructible prop pass.")
        if source_destructable_prop_ed:
            notes.append(f"DestructableProp source ED oracle: {source_destructable_prop_ed}.")
        else:
            notes.append("DestructableProp source ED oracle is not configured; DestructableProp object nodes will be skipped.")
    if include_destructable_brush_objects:
        notes.append(
            "DestructableBrush object records will be reconstructed from DAT object records and matched same-name BSP world models."
        )
    if include_collision_helper_objects:
        if include_collision_helper_brushes:
            notes.append("Collision helper object/Brush records will be generated from DAT Invisible/Firethrough helper models.")
        else:
            notes.append("Collision helper object records will be copied from the source ED oracle; DAT Invisible/Firethrough helper Brush shells remain disabled.")
        if source_collision_ed:
            notes.append(f"Collision helper source ED oracle: {source_collision_ed}.")
        else:
            notes.append("Collision helper source ED oracle is not configured; DAT-native helper object records will be used when names/classes match.")
    if include_trigger_helper_objects:
        if include_trigger_helper_brushes:
            notes.append("Trigger helper object/Brush records will be generated from DAT GreenScreen helper models.")
        else:
            notes.append("Trigger helper PortalZone object records will be copied from the source ED oracle; DAT GreenScreen helper Brush shells remain disabled.")
        if source_trigger_ed:
            notes.append(f"Trigger helper source ED oracle: {source_trigger_ed}.")
        else:
            notes.append("Trigger helper source ED oracle is not configured; DAT-native PortalZone records will be used when names match.")
    if cutout_coverage_enabled:
        notes.append(
            "Terrain cutout coverage will be sampled against original non-terrain model footprints."
        )
    if include_terrain_support_source_coverage:
        notes.append(
            "Terrain support source coverage will compare generated ED terrain tops against original playable Terrain0 polygons."
        )
    if include_physics_shell_source_coverage:
        notes.append(
            "PhysicsBSP shell source coverage will compare generated shell slabs against original PhysicsBSP polygons."
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
    if include_door_objects and not source_door_ed:
        cautions.append(
            "Door object emission requires a source ED oracle; no synthetic Door or RotatingDoor template is emitted."
        )
    if include_door_objects:
        if include_terrain_support_patch:
            door_behavior_context = "source_terrain_support_patch"
            notes.append(
                "Door behavior validation context: local Terrain* support patch is included; this is the stable path for BoxPhysics moving world-model probes."
            )
        elif include_physics_shell_patch:
            door_behavior_context = "source_physics_shell_patch"
            notes.append(
                "Door behavior validation context: local PhysicsBSP shell patch is included; this is the stable path for indoor BoxPhysics moving world-model probes."
            )
        else:
            door_behavior_context = "sparse_context_warning"
            cautions.append(
                "Door behavior validation is sparse: copied Door/RotatingDoor objects without local Terrain* or PhysicsBSP support context can move incorrectly in game; use a source-derived support patch before accepting BoxPhysics door movement."
            )
    if include_airail_objects and not source_airail_ed:
        cautions.append(
            "Generated AIRail objects without a source ED oracle have placeholder RailLink fields and need manual route validation."
        )
    if include_sky_objects and not source_sky_ed:
        cautions.append(
            "Sky object emission is DAT-native without a source ED oracle; validate sky class properties before game use."
        )
    if include_sky_marker_brushes and not source_sky_ed:
        cautions.append(
            "SkyMarker Brush copying requires a source ED oracle; no synthetic sky shell is emitted."
        )
    if include_sky_marker_brushes and source_sky_ed:
        cautions.append(
            "Source SkyMarker Brush shell copying is a DEDit diagnostic path; current Processor output can route those helper faces into visible BSP when the surrounding shipped world context is missing."
        )
    if include_sky_marker_residue_brushes:
        cautions.append(
            "SkyMarker residue Brush emission is diagnostic-only and depends on a shipped compiled DAT reference; run helper leakage validation before using the output in game."
        )
    if include_sky_marker_residue_brushes and not source_sky_ed:
        cautions.append(
            "SkyMarker residue Brush emission requires a source ED oracle; no synthetic residue shell is emitted."
        )
    if include_sky_marker_residue_brushes and not sky_marker_residue_reference_dat:
        cautions.append(
            "SkyMarker residue Brush emission requires a compiled DAT reference to choose the matched source faces."
        )
    if include_sound_objects and not source_sound_ed:
        cautions.append(
            "AmbientSound object emission is DAT-native without a source ED oracle; SoundOnly helper Brush volumes are not emitted."
        )
    if include_gameplay_trigger_objects and not source_gameplay_trigger_ed:
        cautions.append(
            "Gameplay trigger object emission requires a source ED oracle; no synthetic Trigger, ExitTrigger, or PortalTrigger template is emitted."
        )
    if include_gameplay_trigger_objects:
        cautions.append(
            "Gameplay trigger objects can change runtime level flow immediately; verify target/message references and exit destinations in DEDit before accepting in-game behavior."
        )
    if include_static_prop_objects and not source_static_prop_ed:
        cautions.append(
            "Static Prop object emission requires a source ED oracle; no synthetic Prop template is emitted."
        )
    if include_static_prop_objects:
        cautions.append(
            "Static Prop copying preserves generic Prop object records only; behavior-rich prop subclasses remain separate explicit semantic passes."
        )
        cautions.append(
            "Copied static Props depend on source Filename, Skin, Solid, and MoveToFloor properties; inspect placement and collision in DEDit before game testing."
        )
    gated_high_risk_behavior_props = []
    if not include_treasure_chest_objects:
        gated_high_risk_behavior_props.append("TreasureChest")
    if not include_prop_damager_objects:
        gated_high_risk_behavior_props.append("PropDamager")
    if not include_destructable_prop_objects:
        gated_high_risk_behavior_props.append("DestructableProp")
    gated_high_risk_behavior_prop_text = (
        ", ".join(gated_high_risk_behavior_props) + " remain controlled by explicit passes."
        if gated_high_risk_behavior_props
        else ""
    )
    if include_low_risk_behavior_prop_objects and not source_low_risk_behavior_prop_ed:
        cautions.append(
            "Low-risk behavior prop object emission requires a source ED oracle; no synthetic Barrel, BonePile, Cauldron, Cookpot, or StatStone template is emitted."
        )
    if include_low_risk_behavior_prop_objects:
        cautions.append(
            "Low-risk behavior prop copying is limited to physical-decor subclasses."
        )
        cautions.append(
            "Copied low-risk behavior props can still affect collision through Solid and MoveToFloor properties; inspect placement before game testing."
        )
        if gated_high_risk_behavior_prop_text:
            cautions.append(gated_high_risk_behavior_prop_text)
    if include_wall_torch_objects and not source_wall_torch_ed:
        cautions.append(
            "WallTorch object emission requires a source ED oracle; no synthetic WallTorch template is emitted."
        )
    if include_wall_torch_objects:
        cautions.append(
            "WallTorch copying preserves medium-risk light/fire/sound behavior; initial ANSKRAMKEEP manual validation passed, but inspect placement and sound/light behavior in each new world."
        )
        if gated_high_risk_behavior_prop_text:
            cautions.append(gated_high_risk_behavior_prop_text)
    if include_fire_objects and not source_fire_ed:
        cautions.append(
            "Fire object emission requires a source ED oracle; no synthetic Fire template is emitted."
        )
    if include_fire_objects:
        cautions.append(
            "Fire copying preserves standalone medium-risk light/fire/sound behavior; initial ANSKRAMKEEP manual validation passed, but inspect placement and sound/light behavior in each new world."
        )
        if gated_high_risk_behavior_prop_text:
            cautions.append(gated_high_risk_behavior_prop_text)
    if include_candle_prop_objects and not source_candle_prop_ed:
        cautions.append(
            "CandleProp object emission requires a source ED oracle; no synthetic CandleProp template is emitted."
        )
    if include_candle_prop_objects:
        cautions.append(
            "CandleProp copying preserves medium-risk light/model prop behavior; initial manual validation passed, but inspect placement and light/model behavior in each new world."
        )
        if gated_high_risk_behavior_prop_text:
            cautions.append(gated_high_risk_behavior_prop_text)
    if include_brazier_objects and not source_brazier_ed:
        cautions.append(
            "Brazier object emission requires a source ED oracle; no synthetic Brazier template is emitted."
        )
    if include_brazier_objects:
        cautions.append(
            "Brazier copying preserves medium-risk light/fire/sound/model prop behavior; initial manual validation passed, but inspect placement and light/fire/sound behavior in each new world."
        )
        if gated_high_risk_behavior_prop_text:
            cautions.append(gated_high_risk_behavior_prop_text)
    if include_treasure_chest_objects and not source_treasure_chest_ed:
        cautions.append(
            "TreasureChest object emission requires a source ED oracle; no synthetic TreasureChest template is emitted."
        )
    if include_treasure_chest_objects:
        cautions.append(
            "TreasureChest copying preserves high-risk loot and trigger-target behavior; initial manual validation passed, but inspect TriggerTarget, sounds, lock, and treasure fields in each new world."
        )
        if not include_destructable_prop_objects:
            cautions.append(
                "DestructableProp remains controlled by its own explicit pass."
            )
    if include_prop_damager_objects and not source_prop_damager_ed:
        cautions.append(
            "PropDamager object emission requires a source ED oracle; no synthetic PropDamager template is emitted."
        )
    if include_prop_damager_objects:
        cautions.append(
            "PropDamager copying preserves high-risk damage behavior; initial manual validation passed, but inspect DamagerStuff, trigger fields, collision, and placement in each new world."
        )
        if not include_destructable_prop_objects:
            cautions.append(
                "Destructible props remain controlled by their own explicit pass."
            )
    if include_destructable_prop_objects and not source_destructable_prop_ed:
        cautions.append(
            "DestructableProp object emission requires a source ED oracle; no synthetic DestructableProp template is emitted."
        )
    if include_destructable_prop_objects:
        cautions.append(
            "DestructableProp copying preserves high-risk hit-point, damage/destroy-state, sound, collision, and explosion behavior; initial BATHHOUSE manual validation passed, but inspect those fields in each new world."
        )
        cautions.append(
            "DamageTriggerTarget references can activate unrelated gameplay; verify copied target/message fields in DEDit."
        )
    if include_destructable_brush_objects:
        cautions.append(
            "DestructableBrush reconstruction is DAT-native and high-risk: it preserves hit points, solid/visible state, physics, debris, and DeathTriggerTarget chains from compiled DAT object records."
        )
        cautions.append(
            "Each reconstructed DestructableBrush depends on a same-name BSP Brush child; inspect the child Brush and trigger chain in DEDit before accepting in-game destruction behavior."
        )
    if include_collision_helper_objects and not source_collision_ed and include_collision_helper_brushes:
        cautions.append(
            "Collision helper Brush geometry can be generated without a source ED oracle, but helper object nodes require same-name source ED records."
        )
    if include_collision_helper_objects and not source_collision_ed and not include_collision_helper_brushes:
        cautions.append(
            "Collision helper object-only emission uses DAT-native records when names/classes match; no helper Brush fallback is enabled."
        )
    if include_trigger_helper_objects and not source_trigger_ed and include_trigger_helper_brushes:
        cautions.append(
            "Trigger helper Brush geometry can be generated without a source ED oracle, but PortalZone object nodes require same-name source ED records."
        )
    if include_trigger_helper_objects and not source_trigger_ed and not include_trigger_helper_brushes:
        cautions.append(
            "Trigger helper object-only emission uses DAT-native PortalZone records when names match; no GreenScreen Brush fallback is enabled."
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
            include_door_objects=include_door_objects,
            door_source_ed_path=source_door_ed,
            include_airail_objects=include_airail_objects,
            airail_source_ed_path=source_airail_ed,
            include_sky_objects=include_sky_objects,
            sky_source_ed_path=source_sky_ed,
            include_sky_marker_brushes=include_sky_marker_brushes,
            include_sound_objects=include_sound_objects,
            sound_source_ed_path=source_sound_ed,
            include_gameplay_trigger_objects=include_gameplay_trigger_objects,
            gameplay_trigger_source_ed_path=source_gameplay_trigger_ed,
            include_static_prop_objects=include_static_prop_objects,
            static_prop_source_ed_path=source_static_prop_ed,
            include_low_risk_behavior_prop_objects=include_low_risk_behavior_prop_objects,
            low_risk_behavior_prop_source_ed_path=source_low_risk_behavior_prop_ed,
            include_wall_torch_objects=include_wall_torch_objects,
            wall_torch_source_ed_path=source_wall_torch_ed,
            include_fire_objects=include_fire_objects,
            fire_source_ed_path=source_fire_ed,
            include_candle_prop_objects=include_candle_prop_objects,
            candle_prop_source_ed_path=source_candle_prop_ed,
            include_brazier_objects=include_brazier_objects,
            brazier_source_ed_path=source_brazier_ed,
            include_treasure_chest_objects=include_treasure_chest_objects,
            treasure_chest_source_ed_path=source_treasure_chest_ed,
            include_prop_damager_objects=include_prop_damager_objects,
            prop_damager_source_ed_path=source_prop_damager_ed,
            include_destructable_prop_objects=include_destructable_prop_objects,
            destructable_prop_source_ed_path=source_destructable_prop_ed,
            include_destructable_brush_objects=include_destructable_brush_objects,
            include_collision_helper_objects=include_collision_helper_objects,
            include_collision_helper_brushes=include_collision_helper_brushes,
            collision_helper_source_ed_path=source_collision_ed,
            include_trigger_helper_objects=include_trigger_helper_objects,
            include_trigger_helper_brushes=include_trigger_helper_brushes,
            trigger_helper_source_ed_path=source_trigger_ed,
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
        include_airail_objects=include_airail_objects,
            airail_source_ed_path=source_airail_ed,
            include_sky_objects=include_sky_objects,
            sky_source_ed_path=source_sky_ed,
            include_sky_marker_brushes=include_sky_marker_brushes,
            include_sound_objects=include_sound_objects,
            sound_source_ed_path=source_sound_ed,
            include_gameplay_trigger_objects=include_gameplay_trigger_objects,
            gameplay_trigger_source_ed_path=source_gameplay_trigger_ed,
            include_static_prop_objects=include_static_prop_objects,
            static_prop_source_ed_path=source_static_prop_ed,
            include_low_risk_behavior_prop_objects=include_low_risk_behavior_prop_objects,
            low_risk_behavior_prop_source_ed_path=source_low_risk_behavior_prop_ed,
            include_wall_torch_objects=include_wall_torch_objects,
            wall_torch_source_ed_path=source_wall_torch_ed,
            include_fire_objects=include_fire_objects,
            fire_source_ed_path=source_fire_ed,
            include_candle_prop_objects=include_candle_prop_objects,
            candle_prop_source_ed_path=source_candle_prop_ed,
            include_brazier_objects=include_brazier_objects,
            brazier_source_ed_path=source_brazier_ed,
            include_treasure_chest_objects=include_treasure_chest_objects,
            treasure_chest_source_ed_path=source_treasure_chest_ed,
            include_prop_damager_objects=include_prop_damager_objects,
            prop_damager_source_ed_path=source_prop_damager_ed,
            include_destructable_prop_objects=include_destructable_prop_objects,
            destructable_prop_source_ed_path=source_destructable_prop_ed,
            include_collision_helper_objects=include_collision_helper_objects,
            include_collision_helper_brushes=include_collision_helper_brushes,
            collision_helper_source_ed_path=source_collision_ed,
            include_trigger_helper_objects=include_trigger_helper_objects,
            include_trigger_helper_brushes=include_trigger_helper_brushes,
            trigger_helper_source_ed_path=source_trigger_ed,
            selected_model_names=requested_names,
            blockers=(f"source DAT was not found: {source_dat}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    try:
        with open(source_dat, "rb") as f:
            source_dat_bytes = f.read()
        parsed = bsp.parse(source_dat_bytes)
    except Exception as exc:
        return FullWorldSkeletonAcceptanceReport(
            status="dat_parse_failed",
            source_dat_path=source_dat,
            world_install_path=install_path,
            group_name=group_name,
            include_validation_floor=include_validation_floor,
            include_terrain_support_patch=include_terrain_support_patch,
            include_physics_shell_patch=include_physics_shell_patch,
            include_airail_objects=include_airail_objects,
            airail_source_ed_path=source_airail_ed,
            include_sky_objects=include_sky_objects,
            sky_source_ed_path=source_sky_ed,
            include_sky_marker_brushes=include_sky_marker_brushes,
            include_sound_objects=include_sound_objects,
            sound_source_ed_path=source_sound_ed,
            include_gameplay_trigger_objects=include_gameplay_trigger_objects,
            gameplay_trigger_source_ed_path=source_gameplay_trigger_ed,
            include_static_prop_objects=include_static_prop_objects,
            static_prop_source_ed_path=source_static_prop_ed,
            include_low_risk_behavior_prop_objects=include_low_risk_behavior_prop_objects,
            low_risk_behavior_prop_source_ed_path=source_low_risk_behavior_prop_ed,
            include_wall_torch_objects=include_wall_torch_objects,
            wall_torch_source_ed_path=source_wall_torch_ed,
            include_fire_objects=include_fire_objects,
            fire_source_ed_path=source_fire_ed,
            include_candle_prop_objects=include_candle_prop_objects,
            candle_prop_source_ed_path=source_candle_prop_ed,
            include_brazier_objects=include_brazier_objects,
            brazier_source_ed_path=source_brazier_ed,
            include_treasure_chest_objects=include_treasure_chest_objects,
            treasure_chest_source_ed_path=source_treasure_chest_ed,
            include_prop_damager_objects=include_prop_damager_objects,
            prop_damager_source_ed_path=source_prop_damager_ed,
            include_destructable_prop_objects=include_destructable_prop_objects,
            destructable_prop_source_ed_path=source_destructable_prop_ed,
            include_collision_helper_objects=include_collision_helper_objects,
            include_collision_helper_brushes=include_collision_helper_brushes,
            collision_helper_source_ed_path=source_collision_ed,
            include_trigger_helper_objects=include_trigger_helper_objects,
            include_trigger_helper_brushes=include_trigger_helper_brushes,
            trigger_helper_source_ed_path=source_trigger_ed,
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

    door_pair_notes: Tuple[str, ...] = ()
    if include_door_objects and source_door_ed:
        requested_names, door_pair_notes = surrogate_ed._expand_model_names_with_source_door_pairs(
            requested_names,
            source_ed_path=source_door_ed,
        )
        notes.extend(door_pair_notes)

    effective_max_models = max(0, int(max_models))
    if door_pair_notes and len(requested_names) > effective_max_models:
        notes.append(
            f"max_models limit raised to {len(requested_names)} so source DoubleDoorName pairs stay together."
        )
        effective_max_models = len(requested_names)

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

    if len(selected) > effective_max_models:
        skipped = [
            str(getattr(model, "name", "") or "WorldModel")
            for model in selected[effective_max_models:]
        ]
        skipped_notes.append(f"max_models limit {effective_max_models} trimmed selected set: {', '.join(skipped)}")
        selected = selected[:effective_max_models]
    if unmatched:
        skipped_notes.extend(
            f"requested model was not found or was filtered out: {name}"
            for name in sorted(unmatched)
        )

    model_summaries = tuple(_composite_model_summary(model) for model in selected)
    selected_names = tuple(item.name for item in model_summaries)
    selected_name_lookup = {name.lower() for name in selected_names}
    collision_helper_models: Tuple[object, ...] = ()
    collision_helper_summaries: Tuple[PrefabSurrogateCompositeModelSummary, ...] = ()
    if include_collision_helper_objects and include_collision_helper_brushes:
        collision_helper_models = tuple(
            model for model in getattr(parsed, "world_models", ()) or ()
            if str(getattr(model, "name", "") or "").lower() not in selected_name_lookup
            and _is_pure_collision_helper_semantic_model(model)
        )
        collision_helper_summaries = tuple(
            _composite_model_summary(model)
            for model in collision_helper_models
        )
        notes.append(
            "Collision helper candidate models: "
            f"{len(collision_helper_summaries)} model(s), "
            f"{sum(item.polygon_count for item in collision_helper_summaries)} polygon(s)."
        )
    trigger_helper_models: Tuple[object, ...] = ()
    trigger_helper_summaries: Tuple[PrefabSurrogateCompositeModelSummary, ...] = ()
    if include_trigger_helper_objects and include_trigger_helper_brushes:
        trigger_helper_models = tuple(
            model for model in getattr(parsed, "world_models", ()) or ()
            if str(getattr(model, "name", "") or "").lower() not in selected_name_lookup
            and _is_pure_trigger_helper_semantic_model(model)
        )
        trigger_helper_summaries = tuple(
            _composite_model_summary(model)
            for model in trigger_helper_models
        )
        notes.append(
            "Trigger helper candidate models: "
            f"{len(trigger_helper_summaries)} model(s), "
            f"{sum(item.polygon_count for item in trigger_helper_summaries)} polygon(s)."
        )
    terrain_support_preflight_brushes = 0
    terrain_support_preflight_polygons = 0
    terrain_support_preflight_points = 0
    precomputed_terrain_support_brush_bundle: Optional[Tuple[object, ...]] = None
    if include_terrain_support_patch:
        (
            terrain_support_preflight_brushes,
            terrain_support_preflight_polygons,
            terrain_support_preflight_points,
            terrain_support_preflight_notes,
            precomputed_terrain_support_brush_bundle,
        ) = _preflight_terrain_support_patch_cost(
            source_dat_bytes,
            parsed,
            selected,
            surrogate_module=surrogate_ed,
            source_model_name=terrain_support_model_name,
            name_prefix=terrain_support_name_prefix,
            margin=terrain_support_margin,
            selection_mode=terrain_support_selection_mode,
            radius=terrain_support_radius,
            brush_mode=terrain_support_brush_mode,
            thickness=terrain_support_thickness,
            max_polygons=terrain_support_max_polygons,
            side_texture=terrain_support_side_texture,
        )
        notes.extend(terrain_support_preflight_notes)
    sky_marker_preflight_brushes = 0
    sky_marker_preflight_polygons = 0
    sky_marker_preflight_points = 0
    precomputed_sky_marker_brush_bundle: Optional[Tuple[object, ...]] = None
    precomputed_sky_marker_residue_brush_bundle: Optional[Tuple[object, ...]] = None
    if include_sky_marker_brushes or include_sky_marker_residue_brushes:
        (
            sky_marker_preflight_brushes,
            sky_marker_preflight_polygons,
            sky_marker_preflight_points,
            sky_marker_preflight_notes,
            precomputed_sky_marker_brush_bundle,
            precomputed_sky_marker_residue_brush_bundle,
        ) = _preflight_sky_marker_brush_cost(
            surrogate_module=surrogate_ed,
            source_ed_path=source_sky_ed,
            include_sky_marker_brushes=include_sky_marker_brushes,
            include_sky_marker_residue_brushes=include_sky_marker_residue_brushes,
            residue_reference_dat_path=sky_marker_residue_reference_dat,
        )
        notes.extend(sky_marker_preflight_notes)
    total_points = sum(item.point_count for item in model_summaries)
    total_polygons = sum(item.polygon_count for item in model_summaries)
    expected_points = (
        total_points
        + sum(item.point_count for item in collision_helper_summaries)
        + sum(item.point_count for item in trigger_helper_summaries)
        + terrain_support_preflight_points
        + sky_marker_preflight_points
        + (8 if include_validation_floor else 0)
    )
    expected_polygons = (
        total_polygons
        + sum(item.polygon_count for item in collision_helper_summaries)
        + sum(item.polygon_count for item in trigger_helper_summaries)
        + terrain_support_preflight_polygons
        + sky_marker_preflight_polygons
        + (6 if include_validation_floor else 0)
    )
    effective_physics_shell_max_polygons = max(0, int(physics_shell_max_polygons))
    physics_shell_generated_face_budget = 0
    physics_shell_comparison_face_budget = 0
    physics_shell_comparison_source_limit = effective_physics_shell_max_polygons
    physics_shell_packing_comparison = None
    physics_shell_analysis_cache: Dict[str, object] = {}
    if include_physics_shell_patch:
        if processor_brush_budget:
            remaining_brushes = max(
                0,
                processor_brush_budget
                - len(model_summaries)
                - len(collision_helper_summaries)
                - len(trigger_helper_summaries)
                - terrain_support_preflight_brushes
                - sky_marker_preflight_brushes
                - (1 if include_validation_floor else 0),
            )
            if remaining_brushes < effective_physics_shell_max_polygons:
                notes.append(
                    f"PhysicsBSP shell source polygon budget capped by Processor brush budget: "
                    f"{effective_physics_shell_max_polygons} -> {remaining_brushes}."
                )
                effective_physics_shell_max_polygons = remaining_brushes
        physics_shell_comparison_source_limit = effective_physics_shell_max_polygons
        if processor_polygon_budget:
            remaining_generated_polygons = max(0, processor_polygon_budget - expected_polygons)
            physics_shell_comparison_face_budget = remaining_generated_polygons
            physics_shell_generated_face_budget = remaining_generated_polygons
            if physics_shell_packing_mode_key == "cost_aware":
                notes.append(
                    "Cost-aware PhysicsBSP shell face budget uses the remaining Processor polygon budget "
                    f"after normalized base overhead: {remaining_generated_polygons}."
                )
            else:
                fitted_shell_polygons = _budgeted_physics_shell_source_polygon_count(
                    parsed,
                    physics_shell_model_name,
                    requested_source_polygon_count=effective_physics_shell_max_polygons,
                    generated_polygon_budget=remaining_generated_polygons,
                    focus_points=physics_shell_focus_points,
                    focus_radius=physics_shell_focus_radius,
                    focus_budget=physics_shell_focus_budget,
                    focus_seed_radius=physics_shell_focus_seed_radius,
                    door_clearance_bounds=(
                        surrogate_ed._source_door_clearance_bounds_from_source_ed(
                            source_door_ed,
                            candidate_names=selected_names,
                        )
                        if include_door_objects and source_door_ed
                        else ()
                    ),
                    analysis_cache=physics_shell_analysis_cache,
                    cache_final_balanced_groups=(
                        not physics_shell_source_polygon_indices
                        and not physics_shell_focus_points
                        and not physics_shell_protected_bounds
                        and not physics_shell_stair_assembly_indices
                        and not (include_door_objects and source_door_ed)
                    ),
                )
                if fitted_shell_polygons < effective_physics_shell_max_polygons:
                    notes.append(
                        f"PhysicsBSP shell source polygon budget capped by predicted generated face count: "
                        f"{effective_physics_shell_max_polygons} -> {fitted_shell_polygons}."
                    )
                    effective_physics_shell_max_polygons = fitted_shell_polygons
        if include_physics_shell_packing_comparison:
            comparison_model = terrain_semantics.model_by_name(
                tuple(getattr(parsed, "world_models", ()) or ()),
                str(physics_shell_model_name or terrain_semantics.PHYSICS_BSP_MODEL),
            )
            if comparison_model is None:
                cautions.append(
                    f"PhysicsBSP packing comparison could not find {physics_shell_model_name}."
                )
            else:
                comparison_candidates = physics_shell_analysis_cache.get("candidates")
                if not isinstance(comparison_candidates, tuple):
                    comparison_candidates = terrain_reconstruction.physics_shell_candidates(
                        comparison_model
                    )
                    physics_shell_analysis_cache["candidates"] = comparison_candidates
                comparison_index = physics_shell_analysis_cache.get("consolidation_index")
                if not isinstance(
                    comparison_index,
                    terrain_reconstruction.PhysicsShellConsolidationIndex,
                ):
                    comparison_index = terrain_reconstruction.build_physics_shell_consolidation_index(
                        comparison_model,
                        comparison_candidates,
                    )
                    physics_shell_analysis_cache["consolidation_index"] = comparison_index
                comparison_door_bounds = (
                    surrogate_ed._source_door_clearance_bounds_from_source_ed(
                        source_door_ed,
                        candidate_names=requested_names,
                    )
                    if include_door_objects and source_door_ed
                    else ()
                )
                physics_shell_packing_comparison = (
                    terrain_reconstruction.compare_physics_shell_packing_plans(
                        comparison_model,
                        comparison_candidates,
                        source_polygon_limit=physics_shell_comparison_source_limit,
                        generated_face_budget=physics_shell_comparison_face_budget,
                        consolidation_index=comparison_index,
                        protected_bounds=(
                            tuple(comparison_door_bounds)
                            + tuple(physics_shell_protected_bounds)
                        ),
                        protected_roles=physics_shell_protected_roles,
                        role_weights=physics_shell_packing_role_weights,
                        playable_importance_points=physics_shell_focus_points,
                        playable_importance_radius=physics_shell_focus_radius,
                        playable_importance_weight=(
                            physics_shell_packing_playable_importance_weight
                        ),
                    )
                )
                notes.append(
                    "PhysicsBSP packing comparison: "
                    f"preferred validation mode={physics_shell_packing_comparison.preferred_validation_mode}, "
                    f"weighted value delta={physics_shell_packing_comparison.weighted_value_delta:g}, "
                    f"area delta={physics_shell_packing_comparison.recovered_source_area_delta:g}."
                )
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
            include_airail_objects=include_airail_objects,
            airail_source_ed_path=source_airail_ed,
            include_sky_objects=include_sky_objects,
            sky_source_ed_path=source_sky_ed,
            include_sky_marker_brushes=include_sky_marker_brushes,
            include_sound_objects=include_sound_objects,
            sound_source_ed_path=source_sound_ed,
            include_gameplay_trigger_objects=include_gameplay_trigger_objects,
            gameplay_trigger_source_ed_path=source_gameplay_trigger_ed,
            include_static_prop_objects=include_static_prop_objects,
            static_prop_source_ed_path=source_static_prop_ed,
            include_low_risk_behavior_prop_objects=include_low_risk_behavior_prop_objects,
            low_risk_behavior_prop_source_ed_path=source_low_risk_behavior_prop_ed,
            include_wall_torch_objects=include_wall_torch_objects,
            wall_torch_source_ed_path=source_wall_torch_ed,
            include_fire_objects=include_fire_objects,
            fire_source_ed_path=source_fire_ed,
            include_candle_prop_objects=include_candle_prop_objects,
            candle_prop_source_ed_path=source_candle_prop_ed,
            include_brazier_objects=include_brazier_objects,
            brazier_source_ed_path=source_brazier_ed,
            include_treasure_chest_objects=include_treasure_chest_objects,
            treasure_chest_source_ed_path=source_treasure_chest_ed,
            include_prop_damager_objects=include_prop_damager_objects,
            prop_damager_source_ed_path=source_prop_damager_ed,
            include_destructable_prop_objects=include_destructable_prop_objects,
            destructable_prop_source_ed_path=source_destructable_prop_ed,
            include_collision_helper_objects=include_collision_helper_objects,
            include_collision_helper_brushes=include_collision_helper_brushes,
            collision_helper_source_ed_path=source_collision_ed,
            include_trigger_helper_objects=include_trigger_helper_objects,
            include_trigger_helper_brushes=include_trigger_helper_brushes,
            trigger_helper_source_ed_path=source_trigger_ed,
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
            include_airail_objects=include_airail_objects,
            airail_source_ed_path=source_airail_ed,
            include_sky_objects=include_sky_objects,
            sky_source_ed_path=source_sky_ed,
            include_sky_marker_brushes=include_sky_marker_brushes,
            include_sound_objects=include_sound_objects,
            sound_source_ed_path=source_sound_ed,
            include_gameplay_trigger_objects=include_gameplay_trigger_objects,
            gameplay_trigger_source_ed_path=source_gameplay_trigger_ed,
            include_static_prop_objects=include_static_prop_objects,
            static_prop_source_ed_path=source_static_prop_ed,
            include_low_risk_behavior_prop_objects=include_low_risk_behavior_prop_objects,
            low_risk_behavior_prop_source_ed_path=source_low_risk_behavior_prop_ed,
            include_wall_torch_objects=include_wall_torch_objects,
            wall_torch_source_ed_path=source_wall_torch_ed,
            include_fire_objects=include_fire_objects,
            fire_source_ed_path=source_fire_ed,
            include_candle_prop_objects=include_candle_prop_objects,
            candle_prop_source_ed_path=source_candle_prop_ed,
            include_brazier_objects=include_brazier_objects,
            brazier_source_ed_path=source_brazier_ed,
            include_treasure_chest_objects=include_treasure_chest_objects,
            treasure_chest_source_ed_path=source_treasure_chest_ed,
            include_prop_damager_objects=include_prop_damager_objects,
            prop_damager_source_ed_path=source_prop_damager_ed,
            include_destructable_prop_objects=include_destructable_prop_objects,
            destructable_prop_source_ed_path=source_destructable_prop_ed,
            include_collision_helper_objects=include_collision_helper_objects,
            include_collision_helper_brushes=include_collision_helper_brushes,
            collision_helper_source_ed_path=source_collision_ed,
            include_trigger_helper_objects=include_trigger_helper_objects,
            include_trigger_helper_brushes=include_trigger_helper_brushes,
            trigger_helper_source_ed_path=source_trigger_ed,
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
            include_airail_objects=include_airail_objects,
            airail_source_ed_path=source_airail_ed,
            include_sky_objects=include_sky_objects,
            sky_source_ed_path=source_sky_ed,
            include_sky_marker_brushes=include_sky_marker_brushes,
            include_sound_objects=include_sound_objects,
            sound_source_ed_path=source_sound_ed,
            include_gameplay_trigger_objects=include_gameplay_trigger_objects,
            gameplay_trigger_source_ed_path=source_gameplay_trigger_ed,
            include_static_prop_objects=include_static_prop_objects,
            static_prop_source_ed_path=source_static_prop_ed,
            include_low_risk_behavior_prop_objects=include_low_risk_behavior_prop_objects,
            low_risk_behavior_prop_source_ed_path=source_low_risk_behavior_prop_ed,
            include_wall_torch_objects=include_wall_torch_objects,
            wall_torch_source_ed_path=source_wall_torch_ed,
            include_fire_objects=include_fire_objects,
            fire_source_ed_path=source_fire_ed,
            include_candle_prop_objects=include_candle_prop_objects,
            candle_prop_source_ed_path=source_candle_prop_ed,
            include_brazier_objects=include_brazier_objects,
            brazier_source_ed_path=source_brazier_ed,
            include_treasure_chest_objects=include_treasure_chest_objects,
            treasure_chest_source_ed_path=source_treasure_chest_ed,
            include_prop_damager_objects=include_prop_damager_objects,
            prop_damager_source_ed_path=source_prop_damager_ed,
            include_destructable_prop_objects=include_destructable_prop_objects,
            destructable_prop_source_ed_path=source_destructable_prop_ed,
            include_collision_helper_objects=include_collision_helper_objects,
            include_collision_helper_brushes=include_collision_helper_brushes,
            collision_helper_source_ed_path=source_collision_ed,
            include_trigger_helper_objects=include_trigger_helper_objects,
            include_trigger_helper_brushes=include_trigger_helper_brushes,
            trigger_helper_source_ed_path=source_trigger_ed,
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
    stage_timings.append(("source_parse_selection_preflight", time.monotonic() - stage_started))
    stage_started = time.monotonic()
    if cutout_coverage_enabled:
        terrain_cutout_report = build_terrain_cutout_coverage_report(
            source_dat_path=source_dat,
            _preparsed_world=parsed,
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

    stage_timings.append(("terrain_cutout_coverage", time.monotonic() - stage_started))
    stage_started = time.monotonic()
    surrogate_report = surrogate_ed.write_full_world_skeleton_surrogate_legacy_ed_from_dat(
        source_dat,
        generated_ed,
        _preloaded_dat_bytes=source_dat_bytes,
        _preparsed_world=parsed,
        _precomputed_terrain_support_brush_bundle=precomputed_terrain_support_brush_bundle,
        _precomputed_physics_shell_consolidation_index=physics_shell_analysis_cache.get(
            "consolidation_index"
        ),
        _physics_shell_analysis_cache=physics_shell_analysis_cache,
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
        physics_shell_packing_mode=physics_shell_packing_mode_key,
        physics_shell_packing_role_weights=physics_shell_packing_role_weights,
        physics_shell_packing_playable_importance_weight=physics_shell_packing_playable_importance_weight,
        physics_shell_protected_bounds=physics_shell_protected_bounds,
        physics_shell_protected_roles=physics_shell_protected_roles,
        physics_shell_generated_face_budget=physics_shell_generated_face_budget,
        physics_shell_stair_assembly_indices=physics_shell_stair_assembly_indices,
        physics_shell_thickness=physics_shell_thickness,
        physics_shell_side_texture=physics_shell_side_texture,
        physics_shell_source_polygon_indices=physics_shell_source_polygon_indices,
        physics_shell_focus_points=physics_shell_focus_points,
        physics_shell_focus_radius=physics_shell_focus_radius,
        physics_shell_focus_budget=physics_shell_focus_budget,
        physics_shell_focus_seed_radius=physics_shell_focus_seed_radius,
        include_door_objects=include_door_objects,
        door_source_ed_path=source_door_ed,
        include_airail_objects=include_airail_objects,
        airail_source_ed_path=source_airail_ed,
        include_sky_objects=include_sky_objects,
        sky_source_ed_path=source_sky_ed,
        include_sky_marker_brushes=include_sky_marker_brushes,
        include_sky_marker_residue_brushes=include_sky_marker_residue_brushes,
        sky_marker_residue_reference_dat_path=sky_marker_residue_reference_dat,
        _precomputed_sky_marker_brush_bundle=precomputed_sky_marker_brush_bundle,
        _precomputed_sky_marker_residue_brush_bundle=precomputed_sky_marker_residue_brush_bundle,
        include_sound_objects=include_sound_objects,
        sound_source_ed_path=source_sound_ed,
        include_gameplay_trigger_objects=include_gameplay_trigger_objects,
        gameplay_trigger_source_ed_path=source_gameplay_trigger_ed,
        include_static_prop_objects=include_static_prop_objects,
        static_prop_source_ed_path=source_static_prop_ed,
        include_low_risk_behavior_prop_objects=include_low_risk_behavior_prop_objects,
        low_risk_behavior_prop_source_ed_path=source_low_risk_behavior_prop_ed,
        include_wall_torch_objects=include_wall_torch_objects,
        wall_torch_source_ed_path=source_wall_torch_ed,
        include_fire_objects=include_fire_objects,
        fire_source_ed_path=source_fire_ed,
        include_candle_prop_objects=include_candle_prop_objects,
        candle_prop_source_ed_path=source_candle_prop_ed,
        include_brazier_objects=include_brazier_objects,
        brazier_source_ed_path=source_brazier_ed,
        include_treasure_chest_objects=include_treasure_chest_objects,
        treasure_chest_source_ed_path=source_treasure_chest_ed,
        include_prop_damager_objects=include_prop_damager_objects,
        prop_damager_source_ed_path=source_prop_damager_ed,
        include_destructable_prop_objects=include_destructable_prop_objects,
        destructable_prop_source_ed_path=source_destructable_prop_ed,
        include_destructable_brush_objects=include_destructable_brush_objects,
        include_collision_helper_objects=include_collision_helper_objects,
        include_collision_helper_brushes=include_collision_helper_brushes,
        collision_helper_source_ed_path=source_collision_ed,
        include_trigger_helper_objects=include_trigger_helper_objects,
        include_trigger_helper_brushes=include_trigger_helper_brushes,
        trigger_helper_source_ed_path=source_trigger_ed,
    )
    stage_timings.append(("ed_emission", time.monotonic() - stage_started))
    emission_timings = physics_shell_analysis_cache.get("emission_timings_seconds", {})
    if isinstance(emission_timings, Mapping):
        stage_timings.extend(
            (f"ed_emission.{name}", float(elapsed))
            for name, elapsed in emission_timings.items()
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
            include_airail_objects=include_airail_objects,
            airail_source_ed_path=source_airail_ed,
            include_sky_objects=include_sky_objects,
            sky_source_ed_path=source_sky_ed,
            include_sky_marker_brushes=include_sky_marker_brushes,
            include_sound_objects=include_sound_objects,
            sound_source_ed_path=source_sound_ed,
            include_gameplay_trigger_objects=include_gameplay_trigger_objects,
            gameplay_trigger_source_ed_path=source_gameplay_trigger_ed,
            include_static_prop_objects=include_static_prop_objects,
            static_prop_source_ed_path=source_static_prop_ed,
            include_low_risk_behavior_prop_objects=include_low_risk_behavior_prop_objects,
            low_risk_behavior_prop_source_ed_path=source_low_risk_behavior_prop_ed,
            include_wall_torch_objects=include_wall_torch_objects,
            wall_torch_source_ed_path=source_wall_torch_ed,
            include_fire_objects=include_fire_objects,
            fire_source_ed_path=source_fire_ed,
            include_candle_prop_objects=include_candle_prop_objects,
            candle_prop_source_ed_path=source_candle_prop_ed,
            include_brazier_objects=include_brazier_objects,
            brazier_source_ed_path=source_brazier_ed,
            include_treasure_chest_objects=include_treasure_chest_objects,
            treasure_chest_source_ed_path=source_treasure_chest_ed,
            include_prop_damager_objects=include_prop_damager_objects,
            prop_damager_source_ed_path=source_prop_damager_ed,
            include_destructable_prop_objects=include_destructable_prop_objects,
            destructable_prop_source_ed_path=source_destructable_prop_ed,
            include_destructable_brush_objects=include_destructable_brush_objects,
            include_collision_helper_objects=include_collision_helper_objects,
            include_collision_helper_brushes=include_collision_helper_brushes,
            collision_helper_source_ed_path=source_collision_ed,
            include_trigger_helper_objects=include_trigger_helper_objects,
            include_trigger_helper_brushes=include_trigger_helper_brushes,
            trigger_helper_source_ed_path=source_trigger_ed,
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

    stage_started = time.monotonic()
    generated_ed_analysis_cache: Dict[str, object] = {}
    try:
        generated_analysis = legacy_ed.load_legacy_ed_analysis_bundle(generated_ed)
        generated_ed_analysis_cache.update({
            "geometry_scene": generated_analysis.geometry_scene,
            "object_scan": generated_analysis.object_scan,
            "node_layout": generated_analysis.node_layout,
        })
    except Exception:
        # Preserve the existing report-specific error paths.  Each consumer
        # below retries its own view and reports the most useful failure.
        pass
    stage_timings.append(("generated_ed_analysis", time.monotonic() - stage_started))
    stage_started = time.monotonic()
    if include_terrain_support_source_coverage:
        terrain_support_source_report = build_terrain_support_source_coverage_report(
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            _preparsed_world=parsed,
            terrain_model_name=terrain_support_model_name or "Terrain0",
            ignored_terrain_textures=terrain_support_source_coverage_ignored_textures,
            sample_grid=terrain_support_source_coverage_sample_grid,
            max_gaps=terrain_support_source_coverage_max_gaps,
            _generated_ed_analysis_cache=generated_ed_analysis_cache,
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

    stage_timings.append(("terrain_source_coverage", time.monotonic() - stage_started))
    stage_started = time.monotonic()
    if include_physics_shell_source_coverage:
        physics_shell_source_report = build_physics_shell_source_coverage_report(
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            _preparsed_world=parsed,
            _precomputed_physics_shell_candidates=physics_shell_analysis_cache.get(
                "candidates"
            ),
            _precomputed_physics_shell_consolidation_index=physics_shell_analysis_cache.get(
                "consolidation_index"
            ),
            _precomputed_physics_shell_selection_reasons=physics_shell_analysis_cache.get(
                "selection_reasons"
            ),
            _generated_ed_analysis_cache=generated_ed_analysis_cache,
            compiled_dat_path=compiled_dat,
            physics_model_name=physics_shell_model_name or "PhysicsBSP",
            packing_mode=physics_shell_packing_mode_key,
            role_weights=physics_shell_packing_role_weights,
            playable_importance_weight=physics_shell_packing_playable_importance_weight,
            generated_shell_name_prefix=physics_shell_name_prefix or "PhysicsShell",
            source_polygon_budget=effective_physics_shell_max_polygons,
            source_polygon_indices=physics_shell_source_polygon_indices,
            focus_points=physics_shell_focus_points,
            focus_radius=physics_shell_focus_radius,
            focus_budget=physics_shell_focus_budget,
            focus_seed_radius=physics_shell_focus_seed_radius,
            generated_face_budget=physics_shell_generated_face_budget,
            include_stair_assembly_detection=bool(physics_shell_stair_assembly_indices),
            stair_assembly_indices=physics_shell_stair_assembly_indices,
            selected_stair_assembly_indices=(
                surrogate_report.physics_shell_selected_stair_assembly_indices
            ),
            rejected_stair_assembly_indices=(
                surrogate_report.physics_shell_rejected_stair_assembly_indices
            ),
            protected_bounds=physics_shell_protected_bounds,
            protected_roles=physics_shell_protected_roles,
            door_clearance_bounds=(
                surrogate_ed._source_door_clearance_bounds_from_source_ed(
                    source_door_ed,
                    candidate_names=selected_names,
                )
                if include_door_objects and source_door_ed
                else ()
            ),
        )
        physics_shell_source_manifest_path = os.path.join(
            source_dir,
            os.path.splitext(filename)[0] + "_physics_shell_source_coverage.json",
        )
        try:
            with open(physics_shell_source_manifest_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(
                    _physics_shell_source_coverage_manifest(physics_shell_source_report),
                    f,
                    indent=2,
                    sort_keys=True,
                )
                f.write("\n")
        except OSError as exc:
            cautions.append(f"PhysicsBSP shell source coverage manifest write failed: {exc}")
            physics_shell_source_manifest_path = ""
        if physics_shell_source_report.blockers:
            cautions.append(
                f"PhysicsBSP shell source coverage report did not complete cleanly: {physics_shell_source_report.status}"
            )
        elif physics_shell_source_report.uncovered_source_polygon_count:
            cautions.append(
                "PhysicsBSP shell source coverage found uncovered source polygon(s): "
                f"{physics_shell_source_report.uncovered_source_polygon_count}/"
                f"{physics_shell_source_report.source_polygon_count}."
            )
        else:
            notes.append("PhysicsBSP shell source coverage report found no uncovered source PhysicsBSP polygons.")

    stage_timings.append(("physics_source_coverage", time.monotonic() - stage_started))
    stage_started = time.monotonic()
    generated_object_count = 0
    generated_property_count = 0
    generated_class_counts: Dict[str, int] = {}
    try:
        scene = generated_ed_analysis_cache.get("geometry_scene")
        if scene is None:
            scene = legacy_ed.load_legacy_ed_geometry_scene(generated_ed)
            generated_ed_analysis_cache["geometry_scene"] = scene
        object_report = generated_ed_analysis_cache.get("object_scan")
        if object_report is None:
            object_report = legacy_ed.load_legacy_ed_object_scan_report(generated_ed)
            generated_ed_analysis_cache["object_scan"] = object_report
        layout = generated_ed_analysis_cache.get("node_layout")
        if layout is None:
            layout = legacy_ed.load_legacy_ed_node_layout_report(generated_ed)
            generated_ed_analysis_cache["node_layout"] = layout
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
                include_airail_objects=include_airail_objects,
                airail_source_ed_path=source_airail_ed,
                include_sky_objects=include_sky_objects,
                sky_source_ed_path=source_sky_ed,
                include_sky_marker_brushes=include_sky_marker_brushes,
                include_sound_objects=include_sound_objects,
                sound_source_ed_path=source_sound_ed,
                include_collision_helper_objects=include_collision_helper_objects,
                include_collision_helper_brushes=include_collision_helper_brushes,
                collision_helper_source_ed_path=source_collision_ed,
                include_trigger_helper_objects=include_trigger_helper_objects,
                include_trigger_helper_brushes=include_trigger_helper_brushes,
                trigger_helper_source_ed_path=source_trigger_ed,
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
                physics_shell_source_coverage_manifest_path=physics_shell_source_manifest_path,
                physics_shell_source_coverage=physics_shell_source_report,
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
            include_airail_objects=include_airail_objects,
            airail_source_ed_path=source_airail_ed,
            include_sky_objects=include_sky_objects,
            sky_source_ed_path=source_sky_ed,
            include_sky_marker_brushes=include_sky_marker_brushes,
            include_sound_objects=include_sound_objects,
            sound_source_ed_path=source_sound_ed,
            include_gameplay_trigger_objects=include_gameplay_trigger_objects,
            gameplay_trigger_source_ed_path=source_gameplay_trigger_ed,
            include_static_prop_objects=include_static_prop_objects,
            static_prop_source_ed_path=source_static_prop_ed,
            include_low_risk_behavior_prop_objects=include_low_risk_behavior_prop_objects,
            low_risk_behavior_prop_source_ed_path=source_low_risk_behavior_prop_ed,
            include_wall_torch_objects=include_wall_torch_objects,
            wall_torch_source_ed_path=source_wall_torch_ed,
            include_fire_objects=include_fire_objects,
            fire_source_ed_path=source_fire_ed,
            include_candle_prop_objects=include_candle_prop_objects,
            candle_prop_source_ed_path=source_candle_prop_ed,
            include_brazier_objects=include_brazier_objects,
            brazier_source_ed_path=source_brazier_ed,
            include_treasure_chest_objects=include_treasure_chest_objects,
            treasure_chest_source_ed_path=source_treasure_chest_ed,
            include_prop_damager_objects=include_prop_damager_objects,
            prop_damager_source_ed_path=source_prop_damager_ed,
            include_destructable_prop_objects=include_destructable_prop_objects,
            destructable_prop_source_ed_path=source_destructable_prop_ed,
            include_destructable_brush_objects=include_destructable_brush_objects,
            include_collision_helper_objects=include_collision_helper_objects,
            include_collision_helper_brushes=include_collision_helper_brushes,
            collision_helper_source_ed_path=source_collision_ed,
            include_trigger_helper_objects=include_trigger_helper_objects,
            include_trigger_helper_brushes=include_trigger_helper_brushes,
            trigger_helper_source_ed_path=source_trigger_ed,
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
            physics_shell_source_coverage_manifest_path=physics_shell_source_manifest_path,
            physics_shell_source_coverage=physics_shell_source_report,
            blockers=(f"generated full-world skeleton could not be parsed: {exc}",),
            cautions=tuple(_unique_text(cautions + list(surrogate_report.cautions))),
            notes=tuple(_unique_text(notes + skipped_notes + list(surrogate_report.notes))),
        )

    stage_timings.append(("roundtrip_validation", time.monotonic() - stage_started))
    stage_started = time.monotonic()
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
            include_airail_objects=include_airail_objects,
            airail_source_ed_path=source_airail_ed,
            include_sky_objects=include_sky_objects,
            sky_source_ed_path=source_sky_ed,
            include_sky_marker_brushes=include_sky_marker_brushes,
            include_sound_objects=include_sound_objects,
            sound_source_ed_path=source_sound_ed,
            include_gameplay_trigger_objects=include_gameplay_trigger_objects,
            gameplay_trigger_source_ed_path=source_gameplay_trigger_ed,
            include_static_prop_objects=include_static_prop_objects,
            static_prop_source_ed_path=source_static_prop_ed,
            include_low_risk_behavior_prop_objects=include_low_risk_behavior_prop_objects,
            low_risk_behavior_prop_source_ed_path=source_low_risk_behavior_prop_ed,
            include_wall_torch_objects=include_wall_torch_objects,
            wall_torch_source_ed_path=source_wall_torch_ed,
            include_fire_objects=include_fire_objects,
            fire_source_ed_path=source_fire_ed,
            include_candle_prop_objects=include_candle_prop_objects,
            candle_prop_source_ed_path=source_candle_prop_ed,
            include_brazier_objects=include_brazier_objects,
            brazier_source_ed_path=source_brazier_ed,
            include_treasure_chest_objects=include_treasure_chest_objects,
            treasure_chest_source_ed_path=source_treasure_chest_ed,
            include_prop_damager_objects=include_prop_damager_objects,
            prop_damager_source_ed_path=source_prop_damager_ed,
            include_destructable_prop_objects=include_destructable_prop_objects,
            destructable_prop_source_ed_path=source_destructable_prop_ed,
            include_destructable_brush_objects=include_destructable_brush_objects,
            include_collision_helper_objects=include_collision_helper_objects,
            include_collision_helper_brushes=include_collision_helper_brushes,
            collision_helper_source_ed_path=source_collision_ed,
            include_trigger_helper_objects=include_trigger_helper_objects,
            include_trigger_helper_brushes=include_trigger_helper_brushes,
            trigger_helper_source_ed_path=source_trigger_ed,
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
            physics_shell_source_coverage_manifest_path=physics_shell_source_manifest_path,
            physics_shell_source_coverage=physics_shell_source_report,
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
            include_airail_objects=include_airail_objects,
            airail_source_ed_path=source_airail_ed,
            include_sky_objects=include_sky_objects,
            sky_source_ed_path=source_sky_ed,
            include_sky_marker_brushes=include_sky_marker_brushes,
            include_sound_objects=include_sound_objects,
            sound_source_ed_path=source_sound_ed,
            include_gameplay_trigger_objects=include_gameplay_trigger_objects,
            gameplay_trigger_source_ed_path=source_gameplay_trigger_ed,
            include_static_prop_objects=include_static_prop_objects,
            static_prop_source_ed_path=source_static_prop_ed,
            include_low_risk_behavior_prop_objects=include_low_risk_behavior_prop_objects,
            low_risk_behavior_prop_source_ed_path=source_low_risk_behavior_prop_ed,
            include_wall_torch_objects=include_wall_torch_objects,
            wall_torch_source_ed_path=source_wall_torch_ed,
            include_fire_objects=include_fire_objects,
            fire_source_ed_path=source_fire_ed,
            include_candle_prop_objects=include_candle_prop_objects,
            candle_prop_source_ed_path=source_candle_prop_ed,
            include_brazier_objects=include_brazier_objects,
            brazier_source_ed_path=source_brazier_ed,
            include_treasure_chest_objects=include_treasure_chest_objects,
            treasure_chest_source_ed_path=source_treasure_chest_ed,
            include_prop_damager_objects=include_prop_damager_objects,
            prop_damager_source_ed_path=source_prop_damager_ed,
            include_destructable_prop_objects=include_destructable_prop_objects,
            destructable_prop_source_ed_path=source_destructable_prop_ed,
            include_collision_helper_objects=include_collision_helper_objects,
            include_collision_helper_brushes=include_collision_helper_brushes,
            collision_helper_source_ed_path=source_collision_ed,
            include_trigger_helper_objects=include_trigger_helper_objects,
            include_trigger_helper_brushes=include_trigger_helper_brushes,
            trigger_helper_source_ed_path=source_trigger_ed,
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
            physics_shell_source_coverage_manifest_path=physics_shell_source_manifest_path,
            physics_shell_source_coverage=physics_shell_source_report,
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
        if physics_shell_packing_mode_key == "cost_aware":
            manual_steps = manual_steps + (
                "Review the cost-aware packing counts and verify that every protected door corridor remains open before promoting this mode beyond controlled validation.",
            )
    if include_door_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm copied Door/RotatingDoor object nodes are present for matching selected DAT door models, keep their source Name, AttachTo, movement, lock, and sound properties, and contain the matching Brush child. When a source child Brush is available, its original projection and Brush flags should be preserved.",
        ) + manual_steps[2:]
        if door_behavior_context in {"source_terrain_support_patch", "source_physics_shell_patch"}:
            manual_steps = manual_steps + (
                "Activate each copied Door/RotatingDoor in game; door behavior can be accepted only if it works with the included local support context.",
            )
        elif door_behavior_context == "sparse_context_warning":
            manual_steps = manual_steps + (
                "Treat this as an object hierarchy/texture diagnostic only; do not accept Door/RotatingDoor movement behavior until a local Terrain* or PhysicsBSP support patch is included.",
            )
    if include_airail_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm the generated AIRail objects are present, keep the expected AITrk names, and have plausible RailLink properties.",
        ) + manual_steps[2:]
    if include_sky_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm the generated sky objects are present, including SkyPointer, DemoSkyWorldModel, and TOD_Sky records when present in the source ED oracle.",
        ) + manual_steps[2:]
    if include_sound_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm the generated AmbientSound objects are present and keep their source Filename and radius properties; no SoundOnly helper Brush volumes should be emitted.",
        ) + manual_steps[2:]
    if include_gameplay_trigger_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm the generated Trigger, ExitTrigger, and PortalTrigger objects are present and keep their source Dims, target/message, portal, and destination properties.",
        ) + manual_steps[2:]
        manual_steps = manual_steps + (
            "In DEDit, inspect copied gameplay trigger target names before game testing; missing target objects can make trigger behavior incomplete even when the trigger object itself is preserved.",
        )
    if include_static_prop_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm the generated static Prop objects are present and keep their source Filename, Skin, Scale, Visible, Solid, and MoveToFloor properties.",
        ) + manual_steps[2:]
    if include_low_risk_behavior_prop_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm the generated low-risk behavior prop objects are present for Barrel, BonePile, Cauldron, Cookpot, and StatStone records, and keep their source Filename, Skin, Solid, and MoveToFloor properties.",
        ) + manual_steps[2:]
    if include_wall_torch_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm the generated WallTorch objects are present and keep their source On, SoundRadius, SoundFile, Fire, LightMinRadius, LightMaxRadius, Filename, Skin, Solid, and MoveToFloor properties.",
        ) + manual_steps[2:]
    if include_fire_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm the generated Fire objects are present and keep their source On, SoundRadius, SoundFile, Fire, LightMinRadius, LightMaxRadius, and MoveToFloor properties.",
        ) + manual_steps[2:]
    if include_candle_prop_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm the generated CandleProp objects are present and keep their source Filename, Skin, Visible, Solid, MoveToFloor, and light-related properties.",
        ) + manual_steps[2:]
    if include_brazier_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm the generated Brazier objects are present and keep their source Filename, Skin, On, SoundRadius, SoundFile, Fire, LightMinRadius, LightMaxRadius, Solid, and MoveToFloor properties.",
        ) + manual_steps[2:]
    if include_treasure_chest_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm the generated TreasureChest objects are present and keep their source Filename, Skin, OpenSoundName, CloseSoundName, Locked, TriggerTarget, TreasureLevel, Solid, and MoveToFloor properties.",
        ) + manual_steps[2:]
    if include_prop_damager_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm the generated PropDamager objects are present and keep their source Filename, Skin, DamagerStuff, Solid, MoveToFloor, and placement properties.",
        ) + manual_steps[2:]
    if include_destructable_prop_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm the generated DestructableProp objects are present and keep their source Filename, Skin, HitPoints, damage/destroy fields, sounds, Solid, MoveToFloor, and placement properties.",
        ) + manual_steps[2:]
    if include_destructable_brush_objects:
        manual_steps = manual_steps[:2] + (
            "Confirm each generated DestructableBrush object is present, keeps its DAT HitPoints, Solid, Visible, debris, physics, and DeathTriggerTarget fields, and contains a same-name Brush child from the DAT BSP model.",
        ) + manual_steps[2:]
        manual_steps = manual_steps + (
            "In game, destroy the DragonStadium destructible brush stack pieces and check that chained DeathTriggerTarget destruction advances through the expected levels without helper textures or missing collision.",
        )
    if include_sky_marker_brushes:
        manual_steps = manual_steps[:2] + (
            "Confirm the copied SkyMarker Brush shell is present around the generated world and uses the expected repeated sky helper texture.",
        ) + manual_steps[2:]
        manual_steps = manual_steps + (
            "After Processor compilation, confirm the SkyMarker helper shell remains editor-only and the repeated sky helper texture is not visible in game.",
        )
    if include_sky_marker_residue_brushes:
        manual_steps = manual_steps[:2] + (
            "Confirm the diagnostic SkyMarker residue Brush records are present and contain only the compiled-reference matched source faces.",
        ) + manual_steps[2:]
        manual_steps = manual_steps + (
            "After Processor compilation, run the compiled-DAT helper leakage report; reject the diagnostic if SkyMarker textures appear in VisBSP or Terrain* output.",
        )
    if include_collision_helper_objects:
        if include_collision_helper_brushes:
            manual_steps = manual_steps[:2] + (
                "Confirm the generated collision helper Brush geometry and helper objects are present for InvisibleBrush, PerceptionBrush, Ladder, and ladder blocker records.",
            ) + manual_steps[2:]
        else:
            manual_steps = manual_steps[:2] + (
                "Confirm the generated collision helper object nodes are present for InvisibleBrush, PerceptionBrush, Ladder, and ladder blocker records, and no helper-textured Brush shells were emitted.",
            ) + manual_steps[2:]
    if include_trigger_helper_objects:
        if include_trigger_helper_brushes:
            manual_steps = manual_steps[:2] + (
                "Confirm the generated trigger helper GreenScreen Brush geometry and PortalZone objects are present and keep their expected PortalName properties.",
            ) + manual_steps[2:]
        else:
            manual_steps = manual_steps[:2] + (
                "Confirm the generated PortalZone objects are present, keep their expected PortalName properties, and no GreenScreen helper Brush shells were emitted.",
            ) + manual_steps[2:]
    if terrain_cutout_report is not None:
        manual_steps = manual_steps[:2] + (
            "Review the terrain cutout coverage manifest; rectangular covered_cutout gaps should align with original model/building footprints before being treated as terrain loss.",
        ) + manual_steps[2:]
    if terrain_support_source_report is not None:
        manual_steps = manual_steps[:2] + (
            "Review the terrain support source coverage manifest; non-covered source Terrain0 gaps are the actionable generator-loss candidates.",
        ) + manual_steps[2:]
    if physics_shell_source_report is not None:
        manual_steps = manual_steps[:2] + (
            "Review the PhysicsBSP shell source coverage manifest; uncovered side-wall and ceiling counts are the actionable shell selector gaps.",
        ) + manual_steps[2:]
    reported_shell_focus_points: List[Tuple[float, float, float]] = []
    for raw_point in physics_shell_focus_points:
        try:
            x, y, z = raw_point  # type: ignore[misc]
            point = (float(x), float(y), float(z))
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(value) for value in point):
            reported_shell_focus_points.append(point)
    stage_timings.append(("manual_plan_and_report", time.monotonic() - stage_started))
    stage_timings.append(("total", time.monotonic() - generation_started))
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
        physics_shell_focus_points=tuple(reported_shell_focus_points),
        physics_shell_focus_radius=max(0.0, float(physics_shell_focus_radius)),
        physics_shell_focus_budget=max(0, int(physics_shell_focus_budget)),
        physics_shell_focus_seed_radius=max(0.0, float(physics_shell_focus_seed_radius)),
        include_door_objects=include_door_objects,
        door_source_ed_path=source_door_ed,
        door_behavior_context=door_behavior_context,
        include_airail_objects=include_airail_objects,
        airail_source_ed_path=source_airail_ed,
        include_sky_objects=include_sky_objects,
        sky_source_ed_path=source_sky_ed,
        include_sky_marker_brushes=include_sky_marker_brushes,
        include_sky_marker_residue_brushes=include_sky_marker_residue_brushes,
        sky_marker_residue_reference_dat_path=sky_marker_residue_reference_dat,
        include_sound_objects=include_sound_objects,
        sound_source_ed_path=source_sound_ed,
        include_gameplay_trigger_objects=include_gameplay_trigger_objects,
        gameplay_trigger_source_ed_path=source_gameplay_trigger_ed,
        include_static_prop_objects=include_static_prop_objects,
        static_prop_source_ed_path=source_static_prop_ed,
        include_low_risk_behavior_prop_objects=include_low_risk_behavior_prop_objects,
        low_risk_behavior_prop_source_ed_path=source_low_risk_behavior_prop_ed,
        include_wall_torch_objects=include_wall_torch_objects,
        wall_torch_source_ed_path=source_wall_torch_ed,
        include_fire_objects=include_fire_objects,
        fire_source_ed_path=source_fire_ed,
        include_candle_prop_objects=include_candle_prop_objects,
        candle_prop_source_ed_path=source_candle_prop_ed,
        include_brazier_objects=include_brazier_objects,
        brazier_source_ed_path=source_brazier_ed,
        include_treasure_chest_objects=include_treasure_chest_objects,
        treasure_chest_source_ed_path=source_treasure_chest_ed,
        include_prop_damager_objects=include_prop_damager_objects,
        prop_damager_source_ed_path=source_prop_damager_ed,
        include_destructable_prop_objects=include_destructable_prop_objects,
        destructable_prop_source_ed_path=source_destructable_prop_ed,
        include_destructable_brush_objects=include_destructable_brush_objects,
        include_collision_helper_objects=include_collision_helper_objects,
        include_collision_helper_brushes=include_collision_helper_brushes,
        collision_helper_source_ed_path=source_collision_ed,
        include_trigger_helper_objects=include_trigger_helper_objects,
        include_trigger_helper_brushes=include_trigger_helper_brushes,
        trigger_helper_source_ed_path=source_trigger_ed,
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
        physics_shell_source_coverage_manifest_path=physics_shell_source_manifest_path,
        physics_shell_source_coverage=physics_shell_source_report,
        physics_shell_packing_mode=surrogate_report.physics_shell_packing_mode,
        physics_shell_packing_source_polygon_count=surrogate_report.physics_shell_packing_source_polygon_count,
        physics_shell_packing_generated_brush_count=surrogate_report.physics_shell_packing_generated_brush_count,
        physics_shell_packing_generated_face_count=surrogate_report.physics_shell_packing_generated_face_count,
        physics_shell_packing_weighted_value=surrogate_report.physics_shell_packing_weighted_value,
        physics_shell_packing_role_weights=surrogate_report.physics_shell_packing_role_weights,
        physics_shell_packing_playable_importance_weight=surrogate_report.physics_shell_packing_playable_importance_weight,
        physics_shell_stair_assembly_indices=surrogate_report.physics_shell_stair_assembly_indices,
        physics_shell_selected_stair_assembly_indices=surrogate_report.physics_shell_selected_stair_assembly_indices,
        physics_shell_rejected_stair_assembly_indices=surrogate_report.physics_shell_rejected_stair_assembly_indices,
        physics_shell_packing_comparison=physics_shell_packing_comparison,
        physics_shell_protected_void_count=surrogate_report.physics_shell_protected_void_count,
        physics_shell_protected_roles=surrogate_report.physics_shell_protected_roles,
        preflight_generated_brush_count=(
            len(model_summaries)
            + len(collision_helper_summaries)
            + len(trigger_helper_summaries)
            + terrain_support_preflight_brushes
            + sky_marker_preflight_brushes
            + (1 if include_validation_floor else 0)
        ),
        preflight_generated_polygon_count=expected_polygons,
        preflight_extra_brush_count=(
            len(collision_helper_summaries)
            + len(trigger_helper_summaries)
            + terrain_support_preflight_brushes
            + sky_marker_preflight_brushes
            + (1 if include_validation_floor else 0)
        ),
        preflight_extra_polygon_count=(
            sum(item.polygon_count for item in collision_helper_summaries)
            + sum(item.polygon_count for item in trigger_helper_summaries)
            + terrain_support_preflight_polygons
            + sky_marker_preflight_polygons
            + (6 if include_validation_floor else 0)
        ),
        preflight_sky_marker_brush_count=sky_marker_preflight_brushes,
        preflight_sky_marker_polygon_count=sky_marker_preflight_polygons,
        preflight_sky_marker_point_count=sky_marker_preflight_points,
        stage_timings_seconds=tuple(stage_timings),
        manual_steps=manual_steps,
        cautions=tuple(_unique_text(cautions + list(surrogate_report.cautions))),
        notes=tuple(_unique_text(notes + skipped_notes + list(surrogate_report.notes))),
    )


def build_physics_shell_packing_experiment(
    *,
    source_dat_path: str,
    model_names: Sequence[str],
    work_dir: str,
    output_stem: str = "physics_shell_packing",
    acceptance_options: Optional[Mapping[str, object]] = None,
) -> PhysicsShellPackingExperimentReport:
    """Generate a controlled balanced/cost-aware ED pair and manifests.

    All caller-supplied acceptance options are cloned into both runs.  Only the
    packing mode, output path, group name, and comparison toggle differ.
    """
    source_dat = os.path.abspath(source_dat_path)
    experiment_root = os.path.abspath(work_dir)
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(output_stem or "").strip())
    safe_stem = safe_stem.strip("._") or "physics_shell_packing"
    options = dict(acceptance_options or {})
    forced_keys = {
        "source_dat_path",
        "model_names",
        "work_dir",
        "output_filename",
        "group_name",
        "include_physics_shell_patch",
        "physics_shell_packing_mode",
        "include_physics_shell_packing_comparison",
    }
    overridden = tuple(sorted(key for key in forced_keys if key in options))
    for key in forced_keys:
        options.pop(key, None)
    notes: List[str] = []
    if overridden:
        notes.append(
            "Packing experiment overrode controlled acceptance option(s): "
            + ", ".join(overridden) + "."
        )
    if not tuple(str(name).strip() for name in model_names if str(name).strip()):
        return PhysicsShellPackingExperimentReport(
            status="physics_shell_packing_experiment_blocked",
            source_dat_path=source_dat,
            work_dir=experiment_root,
            output_stem=safe_stem,
            blockers=("packing experiment requires at least one selected model name",),
            notes=tuple(notes),
        )

    os.makedirs(experiment_root, exist_ok=True)
    reports: Dict[str, FullWorldSkeletonAcceptanceReport] = {}
    for mode in ("balanced", "cost_aware"):
        mode_label = "Balanced" if mode == "balanced" else "CostAware"
        mode_work_dir = os.path.join(experiment_root, mode)
        reports[mode] = build_full_world_skeleton_acceptance_report(
            source_dat_path=source_dat,
            model_names=model_names,
            work_dir=mode_work_dir,
            output_filename=f"{safe_stem}_{mode}.ed",
            group_name=f"{safe_stem}_{mode_label}",
            include_physics_shell_patch=True,
            physics_shell_packing_mode=mode,
            include_physics_shell_packing_comparison=(mode == "cost_aware"),
            **options,
        )

    balanced = reports["balanced"]
    cost_aware = reports["cost_aware"]
    blockers: List[str] = []
    ready_status = "ready_for_manual_full_world_skeleton_test"
    if balanced.status != ready_status:
        blockers.append(f"balanced acceptance failed: {balanced.status}")
    if cost_aware.status != ready_status:
        blockers.append(f"cost-aware acceptance failed: {cost_aware.status}")
    comparison = cost_aware.physics_shell_packing_comparison
    if comparison is None:
        blockers.append("cost-aware acceptance did not produce a packing comparison")
    elif not balanced.physics_shell_stair_assembly_indices:
        balanced = replace(
            balanced,
            physics_shell_packing_source_polygon_count=(
                comparison.balanced.source_polygon_count
            ),
            physics_shell_packing_generated_brush_count=(
                comparison.balanced.generated_brush_count
            ),
            physics_shell_packing_generated_face_count=(
                comparison.balanced.generated_face_count
            ),
            physics_shell_packing_weighted_value=comparison.balanced.weighted_value,
            physics_shell_packing_comparison=comparison,
        )
    else:
        notes.append(
            "Packing comparison metrics describe the non-reserved policy baseline; "
            "run manifests retain the actual atomic stair-reservation costs."
        )

    balanced_manifest_path = os.path.join(
        experiment_root,
        "balanced",
        f"{safe_stem}_balanced_acceptance.json",
    )
    cost_aware_manifest_path = os.path.join(
        experiment_root,
        "cost_aware",
        f"{safe_stem}_cost_aware_acceptance.json",
    )
    write_full_world_skeleton_acceptance_manifest(balanced, balanced_manifest_path)
    write_full_world_skeleton_acceptance_manifest(cost_aware, cost_aware_manifest_path)
    experiment_manifest_path = os.path.join(
        experiment_root,
        f"{safe_stem}_comparison.json",
    )
    report = PhysicsShellPackingExperimentReport(
        status=(
            "physics_shell_packing_experiment_ready"
            if not blockers
            else "physics_shell_packing_experiment_failed"
        ),
        source_dat_path=source_dat,
        work_dir=experiment_root,
        output_stem=safe_stem,
        physics_shell_model_name=str(
            options.get("physics_shell_model_name", "PhysicsBSP") or "PhysicsBSP"
        ),
        physics_shell_name_prefix=str(
            options.get("physics_shell_name_prefix", "PhysicsShell") or "PhysicsShell"
        ),
        balanced=balanced,
        cost_aware=cost_aware,
        comparison=comparison,
        balanced_manifest_path=balanced_manifest_path,
        cost_aware_manifest_path=cost_aware_manifest_path,
        experiment_manifest_path=experiment_manifest_path,
        blockers=tuple(blockers),
        notes=tuple(notes) + (
            "Process both generated ED files with the same Processor settings before comparing DAT and in-game results.",
        ),
    )
    write_physics_shell_packing_experiment_manifest(report, experiment_manifest_path)
    return report


def build_full_world_skeleton_compiled_validation_report(
    *,
    generated_ed_path: str,
    compiled_dat_path: str,
    helper_reference_dat_path: str = "",
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
    helper_reference_path = os.path.abspath(helper_reference_dat_path) if helper_reference_dat_path else ""
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
    helper_leakage: Optional[CompiledDatHelperLeakageReport] = None
    validation_started = time.monotonic()
    stage_timings: List[Tuple[str, float]] = []

    stage_started = time.monotonic()
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
    stage_timings.append(("generated_ed_object_scan", time.monotonic() - stage_started))

    compiled_data: Optional[bytes] = None
    compiled_world: Optional[object] = None
    compiled_parse_error: Optional[Exception] = None
    stage_started = time.monotonic()
    if not os.path.exists(dat_path):
        dat_summary = DatOutputSemanticSummary(
            path=dat_path,
            status="missing",
            notes=(f"compiled DAT was not found: {dat_path}",),
        )
        blockers.append(f"compiled DAT was not found: {dat_path}")
    else:
        try:
            from core import bsp

            with open(dat_path, "rb") as f:
                compiled_data = f.read()
            compiled_world = bsp.parse(compiled_data)
        except Exception as exc:
            compiled_parse_error = exc
        if compiled_parse_error is not None:
            dat_summary = DatOutputSemanticSummary(
                path=dat_path,
                status="parse_failed",
                version=(
                    struct.unpack_from("<I", compiled_data, 0)[0]
                    if compiled_data is not None and len(compiled_data) >= 4
                    else _first_u32(dat_path)
                ),
                notes=(str(compiled_parse_error),),
            )
        else:
            dat_summary = _load_dat_output_semantic_summary(
                dat_path,
                _preloaded_data=compiled_data,
                _preparsed_world=compiled_world,
            )
        if dat_summary.status != "loaded":
            blockers.append(f"compiled DAT did not parse cleanly: {dat_summary.status}")
        elif not dat_summary.physics_bsp_present:
            blockers.append("compiled DAT has no PhysicsBSP")
        elif start_point is not None:
            try:
                from core import bsp

                physics = next(
                    (
                        model
                        for model in getattr(compiled_world, "world_models", ()) or ()
                        if terrain_semantics.is_physics_bsp_model(model)
                    ),
                    None,
                )
                if physics is None:
                    blockers.append("compiled DAT parser did not expose PhysicsBSP geometry")
                else:
                    sub_world = bsp.BspWorld(
                        version=int(getattr(compiled_world, "version", 66) or 66),
                        world_info=str(getattr(compiled_world, "world_info", "") or ""),
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
    stage_timings.append(("compiled_dat_parse_summary_floor", time.monotonic() - stage_started))

    stage_started = time.monotonic()
    if helper_reference_path and os.path.exists(dat_path):
        helper_leakage = build_compiled_dat_helper_leakage_report(
            dat_path,
            reference_dat_path=helper_reference_path,
            _preparsed_compiled_world=compiled_world,
            _preparsed_reference_world=(
                compiled_world
                if os.path.normcase(helper_reference_path) == os.path.normcase(dat_path)
                else None
            ),
        )
        if helper_leakage.status == "helper_leakage_detected":
            blockers.append("compiled DAT helper texture leakage detected")
            blockers.extend(helper_leakage.blockers)
        elif helper_leakage.status == "helper_leakage_cautions":
            cautions.append("compiled DAT helper texture placement differs from reference")
            cautions.extend(helper_leakage.cautions)
        elif helper_leakage.status == "helper_leakage_clear":
            notes.append("compiled DAT helper leakage check passed")
        elif helper_leakage.blockers:
            blockers.append(f"compiled DAT helper leakage check failed: {helper_leakage.status}")
            blockers.extend(helper_leakage.blockers)
        elif helper_leakage.cautions:
            cautions.append(f"compiled DAT helper leakage check warning: {helper_leakage.status}")
            cautions.extend(helper_leakage.cautions)
    stage_timings.append(("helper_leakage", time.monotonic() - stage_started))

    stage_started = time.monotonic()
    parsed_logs = tuple(_parse_processor_log(path) for path in logs)
    missing_logs = [log for log in parsed_logs if log.status == "missing"]
    if missing_logs:
        cautions.append(f"{len(missing_logs)} Processor log file(s) were not found")
    for log in parsed_logs:
        if log.problem_brush_count:
            cautions.append(f"Processor reported {log.problem_brush_count} problem brush(es)")
        if log.warnings:
            cautions.append("Processor emitted warning(s)")
    stage_timings.append(("processor_logs", time.monotonic() - stage_started))

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
    stage_timings.append(("total", time.monotonic() - validation_started))

    return FullWorldSkeletonCompiledValidationReport(
        status=status,
        generated_ed_path=ed_path,
        compiled_dat_path=dat_path,
        helper_reference_dat_path=helper_reference_path,
        processor_log_paths=logs,
        start_point=start_point,
        move_player_to_floor=move_player_to_floor,
        physics_floor_y=physics_floor_y,
        physics_floor_drop=physics_floor_drop,
        max_start_floor_drop=max_drop,
        dat=dat_summary,
        helper_leakage=helper_leakage,
        processor_logs=parsed_logs,
        stage_timings_seconds=tuple(stage_timings),
        manual_validation=manual,
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def validate_physics_shell_packing_experiment(
    experiment: PhysicsShellPackingExperimentReport,
    *,
    balanced_compiled_dat_path: str = "",
    cost_aware_compiled_dat_path: str = "",
    balanced_processor_log_path: str = "",
    cost_aware_processor_log_path: str = "",
    balanced_manual_validation: Optional[BlackBoxCompilerManualValidation] = None,
    cost_aware_manual_validation: Optional[BlackBoxCompilerManualValidation] = None,
    helper_reference_dat_path: str = "",
    max_start_floor_drop: float = 256.0,
) -> PhysicsShellPackingExperimentValidationReport:
    """Ingest paired Processor outputs and compare compiled/manual outcomes."""
    balanced_acceptance = experiment.balanced
    cost_acceptance = experiment.cost_aware
    if balanced_acceptance is None or cost_acceptance is None:
        return PhysicsShellPackingExperimentValidationReport(
            status="physics_shell_packing_validation_blocked",
            experiment_manifest_path=experiment.experiment_manifest_path,
            blockers=("packing experiment does not contain both acceptance runs",),
        )

    def default_output_path(acceptance: FullWorldSkeletonAcceptanceReport, extension: str) -> str:
        return os.path.splitext(acceptance.generated_ed_path)[0] + extension

    balanced_dat = os.path.abspath(
        balanced_compiled_dat_path or default_output_path(balanced_acceptance, ".dat")
    )
    cost_dat = os.path.abspath(
        cost_aware_compiled_dat_path or default_output_path(cost_acceptance, ".dat")
    )
    balanced_log = os.path.abspath(
        balanced_processor_log_path or default_output_path(balanced_acceptance, ".log")
    )
    cost_log = os.path.abspath(
        cost_aware_processor_log_path or default_output_path(cost_acceptance, ".log")
    )
    missing_dat_modes = tuple(
        mode
        for mode, path in (("balanced", balanced_dat), ("cost_aware", cost_dat))
        if not os.path.exists(path)
    )
    validation_manifest_path = os.path.join(
        experiment.work_dir,
        f"{experiment.output_stem}_validation.json",
    )
    if missing_dat_modes:
        report = PhysicsShellPackingExperimentValidationReport(
            status="awaiting_processor_outputs",
            experiment_manifest_path=experiment.experiment_manifest_path,
            validation_manifest_path=validation_manifest_path,
            balanced_compiled_dat_path=balanced_dat,
            cost_aware_compiled_dat_path=cost_dat,
            balanced_processor_log_path=balanced_log,
            cost_aware_processor_log_path=cost_log,
            blockers=(
                "compiled DAT output is still missing for: " + ", ".join(missing_dat_modes),
            ),
            notes=(
                "Process both ED variants with identical Processor settings, then rerun paired validation.",
            ),
        )
        write_physics_shell_packing_experiment_validation_manifest(report)
        return report

    def compiled_validation(
        acceptance: FullWorldSkeletonAcceptanceReport,
        dat_path: str,
        log_path: str,
        manual: Optional[BlackBoxCompilerManualValidation],
    ) -> FullWorldSkeletonCompiledValidationReport:
        return build_full_world_skeleton_compiled_validation_report(
            generated_ed_path=acceptance.generated_ed_path,
            compiled_dat_path=dat_path,
            helper_reference_dat_path=helper_reference_dat_path,
            processor_log_paths=((log_path,) if os.path.exists(log_path) else ()),
            manual_validation=manual,
            max_start_floor_drop=max_start_floor_drop,
        )

    balanced_validation = compiled_validation(
        balanced_acceptance,
        balanced_dat,
        balanced_log,
        balanced_manual_validation,
    )
    cost_validation = compiled_validation(
        cost_acceptance,
        cost_dat,
        cost_log,
        cost_aware_manual_validation,
    )

    def source_coverage(
        acceptance: FullWorldSkeletonAcceptanceReport,
        dat_path: str,
    ) -> PhysicsShellSourceCoverageReport:
        return build_physics_shell_source_coverage_report(
            source_dat_path=experiment.source_dat_path,
            generated_ed_path=acceptance.generated_ed_path,
            compiled_dat_path=dat_path,
            physics_model_name=experiment.physics_shell_model_name,
            generated_shell_name_prefix=experiment.physics_shell_name_prefix,
            packing_mode=acceptance.physics_shell_packing_mode,
            role_weights=dict(acceptance.physics_shell_packing_role_weights),
            playable_importance_weight=(
                acceptance.physics_shell_packing_playable_importance_weight
            ),
            source_polygon_budget=(
                acceptance.physics_shell_packing_source_polygon_count
            ),
            focus_points=acceptance.physics_shell_focus_points,
            focus_radius=acceptance.physics_shell_focus_radius,
            focus_budget=acceptance.physics_shell_focus_budget,
            focus_seed_radius=acceptance.physics_shell_focus_seed_radius,
            protected_roles=acceptance.physics_shell_protected_roles or ("side_wall",),
            generated_face_budget=(
                experiment.comparison.generated_face_budget
                if experiment.comparison is not None
                else 0
            ),
            include_stair_assembly_detection=True,
            stair_assembly_indices=acceptance.physics_shell_stair_assembly_indices,
            selected_stair_assembly_indices=(
                acceptance.physics_shell_selected_stair_assembly_indices
            ),
            rejected_stair_assembly_indices=(
                acceptance.physics_shell_rejected_stair_assembly_indices
            ),
        )

    balanced_coverage = source_coverage(balanced_acceptance, balanced_dat)
    cost_coverage = source_coverage(cost_acceptance, cost_dat)

    def retained_metrics(
        coverage: PhysicsShellSourceCoverageReport,
    ) -> Tuple[int, int, float]:
        emitted = tuple(
            item
            for item in coverage.source_polygon_diagnostics
            if item.generated_brush_names
        )
        retained = tuple(item for item in emitted if item.compiled_match_count > 0)
        return (
            len(retained),
            max(0, len(emitted) - len(retained)),
            sum(float(item.area) for item in retained),
        )

    (
        balanced_retained_count,
        balanced_lost_count,
        balanced_retained_area,
    ) = retained_metrics(balanced_coverage)
    cost_retained_count, cost_lost_count, cost_retained_area = retained_metrics(
        cost_coverage
    )

    def processor_counts(
        validation: FullWorldSkeletonCompiledValidationReport,
    ) -> Tuple[int, int]:
        problem_count = sum(
            max(0, int(log.problem_brush_count or 0)) for log in validation.processor_logs
        )
        warning_count = sum(
            sum(max(0, int(count)) for count in log.warning_counts.values())
            for log in validation.processor_logs
        )
        return problem_count, warning_count

    balanced_problems, balanced_warnings = processor_counts(balanced_validation)
    cost_problems, cost_warnings = processor_counts(cost_validation)
    balanced_physics = (
        balanced_validation.dat.physics_polygon_count
        if balanced_validation.dat is not None
        else 0
    )
    cost_physics = (
        cost_validation.dat.physics_polygon_count
        if cost_validation.dat is not None
        else 0
    )
    balanced_manual_passed = balanced_validation.status == "validated_in_game"
    cost_manual_passed = cost_validation.status == "validated_in_game"
    manual_complete = balanced_manual_passed and cost_manual_passed
    balanced_failed = balanced_validation.status == "compiled_validation_failed"
    cost_failed = cost_validation.status == "compiled_validation_failed"
    if balanced_failed and not cost_failed:
        recommended_mode = "cost_aware"
    elif cost_failed and not balanced_failed:
        recommended_mode = "balanced"
    elif balanced_manual_passed != cost_manual_passed:
        recommended_mode = "balanced" if balanced_manual_passed else "cost_aware"
    elif cost_problems != balanced_problems:
        recommended_mode = (
            "cost_aware" if cost_problems < balanced_problems else "balanced"
        )
    elif cost_warnings != balanced_warnings:
        recommended_mode = (
            "cost_aware" if cost_warnings < balanced_warnings else "balanced"
        )
    elif not math.isclose(
        cost_retained_area,
        balanced_retained_area,
        rel_tol=1.0e-6,
        abs_tol=1.0e-3,
    ):
        recommended_mode = (
            "cost_aware"
            if cost_retained_area > balanced_retained_area
            else "balanced"
        )
    elif cost_retained_count != balanced_retained_count:
        recommended_mode = (
            "cost_aware"
            if cost_retained_count > balanced_retained_count
            else "balanced"
        )
    elif experiment.comparison is not None:
        recommended_mode = experiment.comparison.preferred_validation_mode
    else:
        recommended_mode = "undetermined"

    logs_complete = os.path.exists(balanced_log) and os.path.exists(cost_log)
    blockers: List[str] = []
    if balanced_failed:
        blockers.append("balanced compiled validation failed")
    if cost_failed:
        blockers.append("cost-aware compiled validation failed")
    if blockers:
        status = "physics_shell_packing_validation_failed"
    elif not logs_complete:
        status = "needs_processor_logs"
    elif manual_complete:
        status = "physics_shell_packing_validated_in_game"
    else:
        status = "needs_manual_game_validation"
    notes = [
        "Recommendation remains advisory until both variants pass the same in-game route checklist."
    ]
    if not logs_complete:
        notes.append("One or both Processor logs are missing; warning comparison is incomplete.")
    report = PhysicsShellPackingExperimentValidationReport(
        status=status,
        experiment_manifest_path=experiment.experiment_manifest_path,
        validation_manifest_path=validation_manifest_path,
        balanced_compiled_dat_path=balanced_dat,
        cost_aware_compiled_dat_path=cost_dat,
        balanced_processor_log_path=balanced_log,
        cost_aware_processor_log_path=cost_log,
        balanced=balanced_validation,
        cost_aware=cost_validation,
        balanced_source_coverage=balanced_coverage,
        cost_aware_source_coverage=cost_coverage,
        balanced_problem_brush_count=balanced_problems,
        cost_aware_problem_brush_count=cost_problems,
        balanced_warning_count=balanced_warnings,
        cost_aware_warning_count=cost_warnings,
        balanced_physics_polygon_count=balanced_physics,
        cost_aware_physics_polygon_count=cost_physics,
        balanced_retained_source_polygon_count=balanced_retained_count,
        cost_aware_retained_source_polygon_count=cost_retained_count,
        balanced_lost_source_polygon_count=balanced_lost_count,
        cost_aware_lost_source_polygon_count=cost_lost_count,
        balanced_retained_source_area=balanced_retained_area,
        cost_aware_retained_source_area=cost_retained_area,
        recommended_mode=recommended_mode,
        manual_comparison_complete=manual_complete,
        blockers=tuple(blockers),
        notes=tuple(notes),
    )
    write_physics_shell_packing_experiment_validation_manifest(report)
    return report


def build_physics_shell_subset_plan(
    *,
    source_dat_path: str,
    physics_model_name: str = "PhysicsBSP",
    work_dir: Optional[str] = None,
    output_prefix: str = "PhysicsShellSubset",
    max_indices_per_batch: int = 128,
    max_generated_faces_per_batch: int = 4096,
    processor_log_path: str = "",
    processor_log_paths: Optional[Mapping[Tuple[str, int], str]] = None,
) -> PhysicsShellSubsetPlan:
    """Build role/index ED subset instructions for Processor warning bisection.

    Processor's problem-brush and plane-warning messages are anonymous.  This
    plan partitions valid PhysicsBSP source polygons by reconstructed role and
    source index, so each generated subset can be compiled independently and
    its warnings can be attributed to a small, known provenance set.
    ``processor_log_paths`` optionally maps ``(role, batch_index)`` to the log
    produced by that controlled compile; those results are copied into each
    subset entry and can be joined to the source-coverage report.
    """
    source_dat = os.path.abspath(source_dat_path)
    processor_log = os.path.abspath(processor_log_path) if processor_log_path else ""
    work_root = os.path.abspath(work_dir) if work_dir else ""
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = [
        "Processor logs identify problem brushes anonymously; compile each role/index subset separately to attribute warnings.",
        "Subset entries are instructions only; this report does not launch DEDit or Processor.exe.",
    ]
    batch_size = int(max_indices_per_batch)
    face_budget = int(max_generated_faces_per_batch)
    if batch_size <= 0:
        blockers.append("PhysicsBSP subset batch size must be positive")
    if face_budget < 0:
        blockers.append("PhysicsBSP subset generated-face budget cannot be negative")

    log_status = "not_supplied"
    problem_brush_count: Optional[int] = None
    warning_count = 0
    if processor_log:
        summary = _parse_processor_log(processor_log)
        log_status = summary.status
        problem_brush_count = summary.problem_brush_count
        warning_count = sum(int(count) for count in summary.warning_counts.values())
        if summary.status != "loaded":
            cautions.append(f"Processor log did not load: {summary.status}")
        elif warning_count or problem_brush_count:
            cautions.append(
                "Processor reported warnings/problem brushes; retain only subsets that pass their controlled compile."
            )

    if blockers:
        return PhysicsShellSubsetPlan(
            status="physics_shell_subset_plan_blocked",
            source_dat_path=source_dat,
            physics_model_name=str(physics_model_name or "PhysicsBSP"),
            work_dir=work_root,
            batch_size=max(0, batch_size),
            generated_face_budget=max(0, face_budget),
            processor_log_path=processor_log,
            processor_log_status=log_status,
            processor_problem_brush_count=problem_brush_count,
            processor_warning_count=warning_count,
            blockers=tuple(blockers),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    try:
        from core import bsp

        with open(source_dat, "rb") as handle:
            parsed = bsp.parse(handle.read())
    except Exception as exc:
        return PhysicsShellSubsetPlan(
            status="physics_shell_subset_plan_blocked",
            source_dat_path=source_dat,
            physics_model_name=str(physics_model_name or "PhysicsBSP"),
            work_dir=work_root,
            batch_size=batch_size,
            generated_face_budget=face_budget,
            processor_log_path=processor_log,
            processor_log_status=log_status,
            processor_problem_brush_count=problem_brush_count,
            processor_warning_count=warning_count,
            blockers=(f"PhysicsBSP source DAT parse failed: {exc}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    model_name = str(physics_model_name or "PhysicsBSP")
    physics_model = terrain_semantics.model_by_name(
        tuple(getattr(parsed, "world_models", ()) or ()),
        model_name,
    )
    if physics_model is None:
        return PhysicsShellSubsetPlan(
            status="physics_shell_subset_plan_blocked",
            source_dat_path=source_dat,
            physics_model_name=model_name,
            work_dir=work_root,
            batch_size=batch_size,
            generated_face_budget=face_budget,
            processor_log_path=processor_log,
            processor_log_status=log_status,
            processor_problem_brush_count=problem_brush_count,
            processor_warning_count=warning_count,
            blockers=(f"PhysicsBSP source model was not found: {model_name}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    candidates = terrain_reconstruction.physics_shell_candidates(physics_model)
    batches = terrain_reconstruction.physics_shell_role_index_batches(
        physics_model,
        max_indices_per_batch=batch_size,
        max_generated_faces_per_batch=face_budget,
    )
    prefix = _legacy_name_component(output_prefix or "PhysicsShellSubset") or "PhysicsShellSubset"
    subset_dir = os.path.join(work_root, "physics_shell_subsets") if work_root else ""
    normalized_subset_logs: Dict[Tuple[str, int], str] = {}
    for raw_key, raw_path in (processor_log_paths or {}).items():
        try:
            role, batch_index = raw_key
            normalized_subset_logs[(str(role), int(batch_index))] = os.path.abspath(str(raw_path))
        except (TypeError, ValueError):
            cautions.append(f"ignored invalid PhysicsBSP subset log key: {raw_key!r}")

    def subset_processor_evidence(role: str, batch_index: int) -> Tuple[str, str, Optional[int], int, str]:
        path = normalized_subset_logs.get((str(role), int(batch_index)), "")
        if not path:
            return "", "not_supplied", None, 0, "not_run"
        summary = _parse_processor_log(path)
        warning_count = sum(int(count) for count in summary.warning_counts.values())
        if summary.status != "loaded":
            return path, summary.status, summary.problem_brush_count, warning_count, "unknown"
        validation = "passed" if not warning_count and not summary.problem_brush_count else "failed"
        return path, summary.status, summary.problem_brush_count, warning_count, validation

    entries_list: List[PhysicsShellSubsetPlanEntry] = []
    for batch in batches:
        entry_log_path, entry_log_status, entry_problem_count, entry_warning_count, entry_validation_status = subset_processor_evidence(
            batch.role,
            int(batch.batch_index),
        )
        entries_list.append(PhysicsShellSubsetPlanEntry(
            role=batch.role,
            batch_index=int(batch.batch_index),
            polygon_indices=tuple(int(index) for index in batch.polygon_indices),
            generated_face_count=int(batch.generated_face_count),
            suggested_output_filename=(
                os.path.join(
                    subset_dir,
                    f"{prefix}_{_legacy_name_component(batch.role)}_{batch.batch_index:03d}.ed",
                )
                if subset_dir
                else f"{prefix}_{_legacy_name_component(batch.role)}_{batch.batch_index:03d}.ed"
            ),
            processor_log_path=entry_log_path,
            processor_log_status=entry_log_status,
            processor_problem_brush_count=entry_problem_count,
            processor_warning_count=entry_warning_count,
            validation_status=entry_validation_status,
        ))
    entries = tuple(entries_list)
    role_counts: Dict[str, int] = {}
    for candidate in candidates:
        role = str(candidate.role)
        role_counts[role] = role_counts.get(role, 0) + 1
    if not entries:
        blockers.append("PhysicsBSP contained no valid shell candidates")
    else:
        notes.append(
            f"Generated {len(entries)} subset(s) across {len(role_counts)} shell role(s); each entry carries source polygon indices."
        )
    supplied_subset_count = sum(1 for entry in entries if entry.processor_log_path)
    if supplied_subset_count:
        notes.append(
            f"Attached Processor evidence to {supplied_subset_count}/{len(entries)} subset(s); "
            "passed subsets have no warning or problem-brush counts."
        )
    return PhysicsShellSubsetPlan(
        status=("physics_shell_subset_plan_built" if not blockers else "physics_shell_subset_plan_blocked"),
        source_dat_path=source_dat,
        physics_model_name=model_name,
        work_dir=work_root,
        batch_size=batch_size,
        generated_face_budget=face_budget,
        source_polygon_count=len(tuple(getattr(physics_model, "polygons", ()) or ())),
        valid_candidate_count=len(candidates),
        role_counts=tuple(sorted((role, count) for role, count in role_counts.items())),
        processor_log_path=processor_log,
        processor_log_status=log_status,
        processor_problem_brush_count=problem_brush_count,
        processor_warning_count=warning_count,
        entries=entries,
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def build_compiled_dat_helper_leakage_report(
    compiled_dat_path: str,
    *,
    reference_dat_path: str = "",
    _preparsed_compiled_world: Optional[object] = None,
    _preparsed_reference_world: Optional[object] = None,
) -> CompiledDatHelperLeakageReport:
    """Report helper-textured polygon placement in a compiled DAT.

    This is a post-Processor guardrail.  It does not decide whether helper
    geometry is semantically correct on its own; it makes suspicious placement
    obvious, especially helper textures in ``VisBSP`` or Terrain*/render
    output, and compares those counts to a shipped reference DAT when supplied.
    """
    compiled_path = os.path.abspath(compiled_dat_path)
    reference_path = os.path.abspath(reference_dat_path) if reference_dat_path else ""
    try:
        from core import bsp
    except Exception as exc:
        return CompiledDatHelperLeakageReport(
            status="dat_parser_unavailable",
            compiled_dat_path=compiled_path,
            reference_dat_path=reference_path,
            blockers=(f"DAT parser is unavailable: {exc}",),
        )

    if not os.path.exists(compiled_path):
        return CompiledDatHelperLeakageReport(
            status="compiled_dat_missing",
            compiled_dat_path=compiled_path,
            reference_dat_path=reference_path,
            blockers=(f"compiled DAT was not found: {compiled_path}",),
        )
    compiled_world = _preparsed_compiled_world
    if compiled_world is None:
        try:
            with open(compiled_path, "rb") as f:
                compiled_world = bsp.parse(f.read())
        except Exception as exc:
            return CompiledDatHelperLeakageReport(
                status="compiled_dat_parse_failed",
                compiled_dat_path=compiled_path,
                reference_dat_path=reference_path,
                blockers=(f"compiled DAT parse failed: {exc}",),
            )

    reference_world = _preparsed_reference_world
    reference_blockers: List[str] = []
    reference_cautions: List[str] = []
    if reference_path and reference_world is None:
        if not os.path.exists(reference_path):
            reference_cautions.append(f"reference DAT was not found: {reference_path}")
        else:
            try:
                with open(reference_path, "rb") as f:
                    reference_world = bsp.parse(f.read())
            except Exception as exc:
                reference_cautions.append(f"reference DAT parse failed: {exc}")

    report = build_compiled_dat_helper_leakage_report_from_worlds(
        compiled_world,
        reference_world=reference_world,
        compiled_dat_path=compiled_path,
        reference_dat_path=reference_path,
    )
    if reference_blockers:
        return replace(
            report,
            status="helper_leakage_detected",
            blockers=tuple(_unique_text(tuple(report.blockers) + tuple(reference_blockers))),
            cautions=tuple(_unique_text(tuple(report.cautions) + tuple(reference_cautions))),
        )
    if reference_cautions:
        status = report.status
        if status == "helper_leakage_clear":
            status = "helper_leakage_cautions"
        return replace(
            report,
            status=status,
            cautions=tuple(_unique_text(tuple(report.cautions) + tuple(reference_cautions))),
        )
    return report


def build_compiled_dat_helper_leakage_report_from_worlds(
    compiled_world: object,
    *,
    reference_world: Optional[object] = None,
    compiled_dat_path: str = "",
    reference_dat_path: str = "",
) -> CompiledDatHelperLeakageReport:
    compiled_models = _compiled_dat_helper_model_summaries(compiled_world)
    reference_models = (
        _compiled_dat_helper_model_summaries(reference_world)
        if reference_world is not None
        else ()
    )
    compiled_by_role_kind = _helper_counts_by_role_and_kind(compiled_models)
    reference_by_role_kind = _helper_counts_by_role_and_kind(reference_models)
    compiled_by_model_role = _helper_counts_by_model_and_role(compiled_models)
    reference_by_model_role = _helper_counts_by_model_and_role(reference_models)
    compiled_role_totals = _helper_role_totals(compiled_models)
    reference_role_totals = _helper_role_totals(reference_models)
    roles = sorted(set(compiled_role_totals) | set(reference_role_totals))

    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = []
    role_comparisons: List[CompiledDatHelperRoleComparison] = []
    has_reference = reference_world is not None

    for role in roles:
        compiled_kind_counts = dict(compiled_by_role_kind.get(role, {}))
        reference_kind_counts = dict(reference_by_role_kind.get(role, {}))
        role_notes: List[str] = []
        role_blocked = False
        role_cautioned = False
        for kind in sorted(set(compiled_kind_counts) | set(reference_kind_counts)):
            compiled_count = int(compiled_kind_counts.get(kind, 0))
            reference_count = int(reference_kind_counts.get(kind, 0))
            excess = compiled_count - reference_count
            if compiled_count <= 0:
                continue
            if kind == "visibility_bsp" and excess > 0:
                role_blocked = True
                if has_reference:
                    msg = (
                        f"{role}: {compiled_count} helper polygon(s) in VisBSP, "
                        f"reference has {reference_count}"
                    )
                else:
                    msg = f"{role}: {compiled_count} helper polygon(s) in VisBSP"
                blockers.append(msg)
                role_notes.append(msg)
            elif kind == "terrain" and excess > 0:
                role_blocked = True
                if has_reference:
                    msg = (
                        f"{role}: {compiled_count} helper polygon(s) in Terrain* output, "
                        f"reference has {reference_count}"
                    )
                else:
                    msg = f"{role}: {compiled_count} helper polygon(s) in Terrain* output"
                blockers.append(msg)
                role_notes.append(msg)
            elif has_reference and excess > 0:
                role_cautioned = True
                msg = (
                    f"{role}: {compiled_count} helper polygon(s) in {kind}, "
                    f"reference has {reference_count}"
                )
                cautions.append(msg)
                role_notes.append(msg)
            elif not has_reference and kind == "world_model":
                role_cautioned = True
                msg = f"{role}: {compiled_count} helper polygon(s) in object/world-model output"
                cautions.append(msg)
                role_notes.append(msg)
        if role_blocked:
            status = "leakage_detected"
        elif role_cautioned:
            status = "differs_from_reference" if has_reference else "needs_reference"
        elif int(compiled_role_totals.get(role, 0)) > 0:
            status = "matches_reference" if has_reference else "helper_present"
        else:
            status = "reference_only"
        role_comparisons.append(CompiledDatHelperRoleComparison(
            role=role,
            status=status,
            compiled_total=int(compiled_role_totals.get(role, 0)),
            reference_total=int(reference_role_totals.get(role, 0)),
            compiled_by_model_kind=compiled_kind_counts,
            reference_by_model_kind=reference_kind_counts,
            notes=tuple(_unique_text(role_notes)),
        ))

    if has_reference:
        for (model_name, role), compiled_count in sorted(compiled_by_model_role.items()):
            reference_count = int(reference_by_model_role.get((model_name, role), 0))
            if compiled_count > reference_count and reference_count == 0:
                cautions.append(
                    f"{role}: helper texture appears in compiled model {model_name}, absent from reference"
                )
    else:
        notes.append("No reference DAT supplied; visibility/terrain helper placement is still checked.")

    compiled_visibility = _helper_count_for_kind(compiled_models, "visibility_bsp")
    reference_visibility = _helper_count_for_kind(reference_models, "visibility_bsp")
    compiled_terrain = _helper_count_for_kind(compiled_models, "terrain")
    reference_terrain = _helper_count_for_kind(reference_models, "terrain")
    compiled_world_model = _helper_count_for_kind(compiled_models, "world_model")
    reference_world_model = _helper_count_for_kind(reference_models, "world_model")
    compiled_total = sum(item.helper_polygon_count for item in compiled_models)
    reference_total = sum(item.helper_polygon_count for item in reference_models)

    if blockers:
        status = "helper_leakage_detected"
    elif cautions:
        status = "helper_leakage_cautions"
    else:
        status = "helper_leakage_clear"
    if compiled_total == 0:
        notes.append("Compiled DAT contains no known helper-textured polygons.")
    elif not blockers:
        notes.append("No helper-textured polygons exceeded reference visibility/Terrain placement.")

    return CompiledDatHelperLeakageReport(
        status=status,
        compiled_dat_path=os.path.abspath(compiled_dat_path) if compiled_dat_path else "",
        reference_dat_path=os.path.abspath(reference_dat_path) if reference_dat_path else "",
        compiled_model_count=len(tuple(getattr(compiled_world, "world_models", ()) or ())),
        reference_model_count=(
            len(tuple(getattr(reference_world, "world_models", ()) or ()))
            if reference_world is not None
            else 0
        ),
        compiled_total_helper_polygon_count=compiled_total,
        reference_total_helper_polygon_count=reference_total,
        compiled_visibility_helper_polygon_count=compiled_visibility,
        reference_visibility_helper_polygon_count=reference_visibility,
        compiled_terrain_helper_polygon_count=compiled_terrain,
        reference_terrain_helper_polygon_count=reference_terrain,
        compiled_world_model_helper_polygon_count=compiled_world_model,
        reference_world_model_helper_polygon_count=reference_world_model,
        model_summaries=compiled_models,
        reference_model_summaries=reference_models,
        role_comparisons=tuple(role_comparisons),
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def build_anskramkeep_physics_shell_retest_report(
    *,
    source_dat_path: str,
    work_dir: Optional[str] = None,
    output_filename: str = "ANSKRAMKEEP_reconstructed_physics_shell_retest.ed",
    reference_processor_log_path: str = "",
    current_processor_log_path: str = "",
    manual_validation: Optional[BlackBoxCompilerManualValidation] = None,
    physics_shell_max_polygons: int = 864,
    physics_shell_thickness: float = 16.0,
    physics_shell_focus_points: Sequence[Tuple[float, float, float]] = (ANSKRAMKEEP_BACK_START_POINT,),
    physics_shell_focus_radius: float = 512.0,
    physics_shell_focus_budget: int = 512,
    physics_shell_focus_seed_radius: float = 128.0,
) -> AnskramkeepPhysicsShellRetestReport:
    """Build the current ANSKRAMKEEP shell candidate and compare validation signals.

    This report does not run old DEDit, Processor.exe, or the game. It prepares
    the generated ED candidate, compares available Processor logs, and records
    whether visual/collision validation has been supplied.
    """
    source_dat = os.path.abspath(source_dat_path)
    manual = manual_validation or BlackBoxCompilerManualValidation()
    reference_log_path = os.path.abspath(reference_processor_log_path) if reference_processor_log_path else ""
    current_log_path = os.path.abspath(current_processor_log_path) if current_processor_log_path else ""
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = [
        "ANSKRAMKEEP PhysicsBSP shell retest report rebuilds the no-helper candidate with a connected StartPoint-focused shell reservation.",
        "Processor and in-game validation are external manual steps; pass the current Processor log and manual validation results after running them.",
    ]

    try:
        from core import bsp

        with open(source_dat, "rb") as f:
            parsed = bsp.parse(f.read())
    except Exception as exc:
        return AnskramkeepPhysicsShellRetestReport(
            status="anskramkeep_retest_blocked",
            source_dat_path=source_dat,
            reference_processor_log_path=reference_log_path,
            current_processor_log_path=current_log_path,
            manual_validation=manual,
            blockers=(f"ANSKRAMKEEP DAT parse failed: {exc}",),
            notes=tuple(notes),
        )

    selected_names = terrain_semantics.default_dat_to_ed_model_names(parsed)
    acceptance = build_full_world_skeleton_acceptance_report(
        source_dat_path=source_dat,
        model_names=selected_names,
        group_name="ANSKRAMKEEP_ReconstructedDAT",
        work_dir=work_dir,
        output_filename=output_filename,
        include_physics_shell_patch=True,
        physics_shell_name_prefix="ANSKRAMKEEP_PhysicsShell",
        physics_shell_max_polygons=physics_shell_max_polygons,
        physics_shell_thickness=physics_shell_thickness,
        physics_shell_focus_points=physics_shell_focus_points,
        physics_shell_focus_radius=physics_shell_focus_radius,
        physics_shell_focus_budget=physics_shell_focus_budget,
        physics_shell_focus_seed_radius=physics_shell_focus_seed_radius,
        include_physics_shell_source_coverage=True,
        block_unreconstructed_physics_shell=True,
        max_processor_brushes=1500,
        max_processor_polygons=12000,
        max_models=1500,
        max_total_points=50000,
        max_total_polygons=50000,
    )
    if acceptance.blockers:
        blockers.extend(acceptance.blockers)
    if acceptance.cautions:
        cautions.extend(acceptance.cautions)

    reference_log = _parse_processor_log(reference_log_path) if reference_log_path else None
    current_log = _parse_processor_log(current_log_path) if current_log_path else None
    if reference_log is None:
        cautions.append("reference Processor log was not supplied; generated candidate counts have no old-log baseline")
    elif reference_log.status != "loaded":
        cautions.append(f"reference Processor log did not load: {reference_log.status}")
    if current_log is None:
        cautions.append("current Processor log was not supplied; Processor warning/output/runtime comparison is pending")
    elif current_log.status != "loaded":
        cautions.append(f"current Processor log did not load: {current_log.status}")

    manual_failed = _manual_validation_failed(manual)
    manual_passed = _manual_validation_passed(manual)
    if manual_failed:
        blockers.append("manual ANSKRAMKEEP in-game validation failed")
    elif not manual_passed:
        cautions.append("manual ANSKRAMKEEP fresh-load visual/collision validation is pending")
    else:
        notes.append("manual ANSKRAMKEEP fresh-load visual/collision validation passed")

    comparisons = _anskramkeep_physics_shell_retest_metrics(
        acceptance=acceptance,
        reference_log=reference_log,
        current_log=current_log,
        manual_validation=manual,
    )

    if blockers:
        status = "anskramkeep_retest_blocked"
    elif current_log is not None and current_log.status == "loaded" and manual_passed:
        status = "anskramkeep_retest_validated"
    elif current_log is not None and current_log.status == "loaded":
        status = "anskramkeep_retest_needs_game_validation"
    else:
        status = "anskramkeep_retest_needs_processor_run"

    return AnskramkeepPhysicsShellRetestReport(
        status=status,
        source_dat_path=source_dat,
        generated_ed_path=acceptance.generated_ed_path,
        work_dir=acceptance.work_dir,
        reference_processor_log_path=reference_log_path,
        current_processor_log_path=current_log_path,
        acceptance=acceptance,
        reference_processor_log=reference_log,
        current_processor_log=current_log,
        manual_validation=manual,
        comparisons=tuple(comparisons),
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def _anskramkeep_physics_shell_retest_metrics(
    *,
    acceptance: FullWorldSkeletonAcceptanceReport,
    reference_log: Optional[BlackBoxProcessorLogSummary],
    current_log: Optional[BlackBoxProcessorLogSummary],
    manual_validation: BlackBoxCompilerManualValidation,
) -> Tuple[AnskramkeepPhysicsShellRetestMetric, ...]:
    metrics: List[AnskramkeepPhysicsShellRetestMetric] = []
    reference_loaded = reference_log is not None and reference_log.status == "loaded"
    current_loaded = current_log is not None and current_log.status == "loaded"

    metrics.append(_retest_numeric_metric(
        "generated_input_polygons",
        reference_log.input_polygon_count if reference_loaded else None,
        current_log.input_polygon_count if current_loaded and current_log.input_polygon_count is not None else acceptance.polygon_count,
        current_note="current value is generated ED polygon count" if not current_loaded else "",
    ))
    metrics.append(_retest_numeric_metric(
        "unable_to_generate_plane_warnings",
        _processor_warning_count(reference_log, "** Unable to generate a plane (0)") if reference_loaded else None,
        _processor_warning_count(current_log, "** Unable to generate a plane (0)") if current_loaded else None,
    ))
    metrics.append(_retest_numeric_metric(
        "processor_output_polygons",
        reference_log.output_polygon_count if reference_loaded else None,
        current_log.output_polygon_count if current_loaded else None,
    ))
    metrics.append(_retest_numeric_metric(
        "processor_tree_depth",
        reference_log.tree_depth if reference_loaded else None,
        current_log.tree_depth if current_loaded else None,
    ))
    metrics.append(_retest_numeric_metric(
        "processor_unseen_removed_polygons",
        reference_log.unseen_removed_polygon_count if reference_loaded else None,
        current_log.unseen_removed_polygon_count if current_loaded else None,
    ))
    metrics.append(_retest_numeric_metric(
        "processor_runtime_minutes",
        reference_log.runtime_minutes if reference_loaded else None,
        current_log.runtime_minutes if current_loaded else None,
    ))
    metrics.append(_retest_manual_metric("manual_visible_walls", manual_validation.visuals_ok))
    metrics.append(_retest_manual_metric("manual_collision", manual_validation.collision_ok))
    return tuple(metrics)


def _retest_numeric_metric(
    metric: str,
    previous: Optional[float],
    current: Optional[float],
    *,
    current_note: str = "",
) -> AnskramkeepPhysicsShellRetestMetric:
    notes = (current_note,) if current_note else ()
    if previous is None or current is None:
        return AnskramkeepPhysicsShellRetestMetric(
            metric=metric,
            status="pending",
            previous=_metric_value_text(previous),
            current=_metric_value_text(current),
            notes=notes,
        )
    delta = float(current) - float(previous)
    return AnskramkeepPhysicsShellRetestMetric(
        metric=metric,
        status="same" if abs(delta) <= 1.0e-9 else "changed",
        previous=_metric_value_text(previous),
        current=_metric_value_text(current),
        delta=_metric_value_text(delta, force_sign=True),
        notes=notes,
    )


def _retest_manual_metric(metric: str, value: Optional[bool]) -> AnskramkeepPhysicsShellRetestMetric:
    if value is True:
        status = "passed"
        current = "true"
    elif value is False:
        status = "failed"
        current = "false"
    else:
        status = "pending"
        current = "unknown"
    return AnskramkeepPhysicsShellRetestMetric(metric=metric, status=status, current=current)


def _processor_warning_count(log: Optional[BlackBoxProcessorLogSummary], warning: str) -> Optional[int]:
    if log is None or log.status != "loaded":
        return None
    return int(log.warning_counts.get(warning, 0))


def _metric_value_text(value: Optional[float], *, force_sign: bool = False) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, int) or float(value).is_integer():
        number = int(value)
        return f"{number:+d}" if force_sign else str(number)
    return f"{float(value):+.3f}" if force_sign else f"{float(value):.3f}"


def build_airail_reconstruction_report(
    *,
    source_dat_path: str,
    source_ed_path: str = "",
    max_object_match_distance: float = 512.0,
    ambiguous_distance_epsilon: float = 16.0,
) -> AirailReconstructionReport:
    """Correlate DAT aiRail helper models with optional source ED AIRail evidence."""
    source_dat = os.path.abspath(source_dat_path)
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = [
        "AIRail reconstruction reports are semantic diagnostics; full-world generation can emit AIRail objects when enabled.",
        "DAT aiRail helper models are identified by helper texture role, then matched to shipped ED AIRail objects when a source ED oracle is supplied.",
    ]

    try:
        from core import bsp

        with open(source_dat, "rb") as f:
            parsed = bsp.parse(f.read())
    except Exception as exc:
        return AirailReconstructionReport(
            status="airail_reconstruction_blocked",
            source_dat_path=source_dat,
            source_ed_path=source_ed,
            blockers=(f"DAT parse failed: {exc}",),
            notes=tuple(notes),
        )

    ai_models: List[Tuple[int, object, PrefabSurrogateCompositeModelSummary]] = []
    for model_index, model in enumerate(getattr(parsed, "world_models", ()) or ()):
        helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
        if helper_roles.get("aiRail", 0) <= 0:
            continue
        if not terrain_semantics.model_has_only_helper_textures(model):
            continue
        ai_models.append((int(model_index), model, _composite_model_summary(model)))

    source_airail_objects: Tuple[AirailOracleObject, ...] = ()
    source_rail_brushes: Tuple[AirailRailBrushOracle, ...] = ()
    if source_ed:
        if not os.path.exists(source_ed):
            cautions.append(f"source ED oracle was not found: {source_ed}")
        else:
            try:
                source_airail_objects = _airail_oracle_objects(source_ed)
                source_rail_brushes = _airail_rail_brush_oracles(source_ed)
            except Exception as exc:
                cautions.append(f"source ED oracle scan failed: {exc}")
    else:
        cautions.append("source ED oracle was not supplied; nearest original AIRail pattern is pending")

    candidates: List[AirailReconstructionCandidate] = []
    for model_index, model, summary in ai_models:
        candidate = _airail_reconstruction_candidate(
            model_index,
            summary,
            source_airail_objects=source_airail_objects,
            source_rail_brushes=source_rail_brushes,
            max_object_match_distance=max_object_match_distance,
            ambiguous_distance_epsilon=ambiguous_distance_epsilon,
        )
        candidates.append(candidate)

    generated_count = sum(1 for candidate in candidates if candidate.status == "matched_source_airail")
    ambiguous_count = sum(1 for candidate in candidates if candidate.status == "ambiguous_source_airail")
    skipped_count = sum(1 for candidate in candidates if candidate.status not in {"matched_source_airail"})

    if blockers:
        status = "airail_reconstruction_blocked"
    elif not candidates:
        status = "airail_reconstruction_no_airail_helpers"
    elif not source_airail_objects:
        status = "airail_reconstruction_needs_source_oracle"
    elif generated_count:
        status = "airail_reconstruction_report_built"
    else:
        status = "airail_reconstruction_no_matches"

    return AirailReconstructionReport(
        status=status,
        source_dat_path=source_dat,
        source_ed_path=source_ed,
        source_helper_model_count=len(ai_models),
        source_helper_polygon_count=sum(summary.polygon_count for _index, _model, summary in ai_models),
        source_airail_object_count=len(source_airail_objects),
        source_rail_brush_count=len(source_rail_brushes),
        generated_object_count=generated_count,
        skipped_candidate_count=skipped_count,
        ambiguous_candidate_count=ambiguous_count,
        candidates=tuple(candidates),
        source_airail_objects=source_airail_objects,
        source_rail_brushes=source_rail_brushes,
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


_SKY_OBJECT_CLASSES = {"TOD_Sky", "SkyPointer", "DemoSkyWorldModel"}


def build_sky_helper_reconstruction_report(
    *,
    source_dat_path: str,
    source_ed_path: str = "",
) -> SkyHelperReconstructionReport:
    """Report DAT sky visibility evidence and source ED sky object oracle records."""
    source_dat = os.path.abspath(source_dat_path)
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = [
        "Sky helper reconstruction reports are semantic diagnostics; full-world generation can copy source ED sky objects when enabled.",
        "DAT sky visibility helpers are often embedded in PhysicsBSP/VisBSP rather than isolated same-name helper models.",
        "SkyMarker Brush reconstruction remains evidence-only in this pass.",
    ]

    try:
        from core import bsp

        with open(source_dat, "rb") as f:
            parsed = bsp.parse(f.read())
    except Exception as exc:
        return SkyHelperReconstructionReport(
            status="sky_helper_reconstruction_blocked",
            source_dat_path=source_dat,
            source_ed_path=source_ed,
            blockers=(f"DAT parse failed: {exc}",),
            notes=tuple(notes),
        )

    helper_models: List[Tuple[int, object, PrefabSurrogateCompositeModelSummary, Dict[str, int], bool]] = []
    for model_index, model in enumerate(getattr(parsed, "world_models", ()) or ()):
        helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
        if int(helper_roles.get("skyVisibility", 0)) <= 0:
            continue
        helper_models.append((
            int(model_index),
            model,
            _composite_model_summary(model),
            dict(helper_roles),
            bool(terrain_semantics.model_has_only_helper_textures(model)),
        ))

    source_sky_objects: Tuple[SkyObjectOracle, ...] = ()
    source_sky_marker_brushes: Tuple[SkyMarkerBrushOracle, ...] = ()
    if source_ed:
        if not os.path.exists(source_ed):
            cautions.append(f"source ED oracle was not found: {source_ed}")
        else:
            try:
                source_sky_objects = _sky_object_oracles(source_ed)
                source_sky_marker_brushes = _sky_marker_brush_oracles(source_ed)
            except Exception as exc:
                cautions.append(f"source ED oracle scan failed: {exc}")
    else:
        cautions.append("source ED oracle was not supplied; sky object copying is pending")

    candidates: List[SkyHelperReconstructionCandidate] = []
    for model_index, _model, summary, helper_roles, pure_helper in helper_models:
        notes_for_candidate: List[str] = []
        status = "source_visibility_evidence"
        if pure_helper:
            status = "pure_sky_helper_source"
            notes_for_candidate.append("model uses only helper textures and can be reserved for sky semantics")
        else:
            notes_for_candidate.append("sky visibility faces are embedded in mixed system geometry")
        candidates.append(SkyHelperReconstructionCandidate(
            source_model_name=summary.name,
            source_model_index=model_index,
            helper_roles=dict(helper_roles),
            pure_helper_model=bool(pure_helper),
            polygon_count=summary.polygon_count,
            bounds_min=summary.bounds_min,
            bounds_max=summary.bounds_max,
            center=summary.center,
            status=status,
            notes=tuple(notes_for_candidate),
        ))

    pure_count = sum(1 for _index, _model, _summary, _roles, pure in helper_models if pure)
    sky_marker_face_count = sum(item.sky_face_count for item in source_sky_marker_brushes)
    if blockers:
        status = "sky_helper_reconstruction_blocked"
    elif not helper_models and not source_sky_objects:
        status = "sky_helper_reconstruction_no_sky_evidence"
    elif not source_sky_objects:
        status = "sky_helper_reconstruction_needs_source_oracle"
    else:
        status = "sky_helper_reconstruction_report_built"

    return SkyHelperReconstructionReport(
        status=status,
        source_dat_path=source_dat,
        source_ed_path=source_ed,
        source_helper_model_count=len(helper_models),
        source_helper_polygon_count=sum(int(roles.get("skyVisibility", 0)) for _index, _model, _summary, roles, _pure in helper_models),
        source_sky_object_count=len(source_sky_objects),
        source_sky_marker_brush_count=len(source_sky_marker_brushes),
        source_sky_marker_face_count=sky_marker_face_count,
        generated_object_count=len(source_sky_objects),
        pure_helper_model_count=pure_count,
        candidates=tuple(candidates),
        source_sky_objects=source_sky_objects,
        source_sky_marker_brushes=source_sky_marker_brushes,
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def build_sky_marker_compiled_residue_report(
    *,
    source_ed_path: str,
    compiled_dat_path: str,
    generated_compiled_dat_path: str = "",
) -> SkyMarkerCompiledResidueReport:
    """Compare source ED SkyMarker shell evidence with compiled DAT residues."""
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    compiled_dat = os.path.abspath(compiled_dat_path) if compiled_dat_path else ""
    generated_dat = os.path.abspath(generated_compiled_dat_path) if generated_compiled_dat_path else ""
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = [
        "SkyMarker compiled residue reports measure the shipped compiler target for sky helper shell behavior.",
        "The game-bound generator should preserve sky objects but avoid copying the source SkyMarker Brush shell until this residue pattern can be reproduced safely.",
    ]

    source_brushes: Tuple[SkyMarkerBrushOracle, ...] = ()
    source_faces: Tuple[_SkyMarkerSourceFaceEvidence, ...] = ()
    source_flag_counts: Dict[str, int] = {}
    if not source_ed:
        blockers.append("source ED oracle was not supplied")
    elif not os.path.exists(source_ed):
        blockers.append(f"source ED oracle was not found: {source_ed}")
    else:
        try:
            source_brushes = _sky_marker_brush_oracles(source_ed)
            source_faces = _sky_marker_source_face_evidence(source_ed)
            source_flag_counts = _sky_marker_brush_flag_counts(source_ed)
        except Exception as exc:
            blockers.append(f"source ED SkyMarker scan failed: {exc}")

    compiled_summaries: Tuple[CompiledDatHelperModelSummary, ...] = ()
    compiled_residue_polygons: Tuple[_SkyMarkerCompiledResiduePolygon, ...] = ()
    compiled_structural_centers: Tuple[Tuple[float, float, float], ...] = ()
    if not compiled_dat:
        blockers.append("compiled DAT was not supplied")
    elif not os.path.exists(compiled_dat):
        blockers.append(f"compiled DAT was not found: {compiled_dat}")
    else:
        try:
            from core import bsp

            with open(compiled_dat, "rb") as f:
                compiled_world = bsp.parse(f.read())
            compiled_summaries = _compiled_dat_helper_model_summaries(compiled_world)
            compiled_residue_polygons = _compiled_sky_marker_residue_polygons(compiled_world)
            compiled_structural_centers = _compiled_structural_polygon_centers(compiled_world)
        except Exception as exc:
            blockers.append(f"compiled DAT parse failed: {exc}")

    generated_summaries: Tuple[CompiledDatHelperModelSummary, ...] = ()
    if generated_dat:
        if not os.path.exists(generated_dat):
            cautions.append(f"generated compiled DAT was not found: {generated_dat}")
        else:
            try:
                from core import bsp

                with open(generated_dat, "rb") as f:
                    generated_world = bsp.parse(f.read())
                generated_summaries = _compiled_dat_helper_model_summaries(generated_world)
            except Exception as exc:
                cautions.append(f"generated compiled DAT parse failed: {exc}")

    source_face_count = sum(item.sky_face_count for item in source_brushes)
    compiled_total = _compiled_helper_role_total(compiled_summaries, "skyVisibility")
    compiled_physics = _compiled_helper_role_count_for_kind(compiled_summaries, "skyVisibility", "physics_bsp")
    compiled_visibility = _compiled_helper_role_count_for_kind(compiled_summaries, "skyVisibility", "visibility_bsp")
    compiled_terrain = _compiled_helper_role_count_for_kind(compiled_summaries, "skyVisibility", "terrain")
    compiled_world_models = _compiled_helper_role_count_for_kind(compiled_summaries, "skyVisibility", "world_model")
    generated_total = _compiled_helper_role_total(generated_summaries, "skyVisibility")
    generated_physics = _compiled_helper_role_count_for_kind(generated_summaries, "skyVisibility", "physics_bsp")
    generated_visibility = _compiled_helper_role_count_for_kind(generated_summaries, "skyVisibility", "visibility_bsp")
    generated_terrain = _compiled_helper_role_count_for_kind(generated_summaries, "skyVisibility", "terrain")
    generated_world_models = _compiled_helper_role_count_for_kind(generated_summaries, "skyVisibility", "world_model")

    residue_matches = _sky_marker_compiled_residue_matches(source_faces, compiled_residue_polygons)
    matched_residue_matches = tuple(
        item
        for item in residue_matches
        if item.source_model_index >= 0 and not item.status.startswith("unmatched")
    )
    unmatched_residue_count = len(residue_matches) - len(matched_residue_matches)
    matched_source_faces = {
        (item.source_model_index, item.source_face_index)
        for item in matched_residue_matches
    }
    matched_source_brushes = {
        (item.source_model_index, item.source_brush_name)
        for item in matched_residue_matches
    }
    matched_flag_counts: Dict[str, int] = {}
    for item in matched_residue_matches:
        for flag_name in item.source_brush_flags:
            matched_flag_counts[flag_name] = int(matched_flag_counts.get(flag_name, 0)) + 1
    matched_plane_distances = [
        float(item.plane_distance)
        for item in matched_residue_matches
        if item.plane_distance is not None
    ]
    matched_center_distances = [
        float(item.center_distance)
        for item in matched_residue_matches
        if item.center_distance is not None
    ]
    matched_normal_dots = [
        float(item.normal_dot)
        for item in matched_residue_matches
        if item.normal_dot is not None
    ]
    max_match_plane_distance = max(matched_plane_distances) if matched_plane_distances else None
    max_match_center_distance = max(matched_center_distances) if matched_center_distances else None
    min_match_normal_dot = min(matched_normal_dots) if matched_normal_dots else None
    nearest_world_geometry_distances = _nearest_source_face_world_geometry_distances(
        source_faces,
        compiled_structural_centers,
    )
    matched_source_face_evidence = tuple(
        item
        for item in source_faces
        if _sky_marker_face_key(item) in matched_source_faces
    )
    unmatched_source_face_evidence = tuple(
        item
        for item in source_faces
        if _sky_marker_face_key(item) not in matched_source_faces
    )
    matched_source_face_summary = _sky_marker_source_face_cohort_summary(
        "matched_source_faces",
        matched_source_face_evidence,
        nearest_world_geometry_distances,
    )
    unmatched_source_face_summary = _sky_marker_source_face_cohort_summary(
        "unmatched_source_faces",
        unmatched_source_face_evidence,
        nearest_world_geometry_distances,
    )
    residue_rule_candidates = _sky_marker_residue_rule_candidates(
        source_faces,
        matched_source_faces,
        nearest_world_geometry_distances,
    )
    non_oracle_rule_candidates = tuple(
        item for item in residue_rule_candidates if item.status != "oracle_target"
    )
    exact_non_oracle_rule_candidates = tuple(
        item
        for item in non_oracle_rule_candidates
        if item.unmatched_source_face_count == 0 and item.missed_matched_source_face_count == 0
    )
    best_non_oracle_rule = max(
        non_oracle_rule_candidates,
        key=lambda item: (
            float(item.recall or 0.0),
            float(item.precision or 0.0),
            -int(item.selected_source_face_count),
        ),
        default=None,
    )

    if compiled_visibility:
        blockers.append(
            f"compiled reference has {compiled_visibility} SkyMarker polygon(s) in VisBSP; expected hidden PhysicsBSP-only residue"
        )
    if compiled_terrain:
        blockers.append(
            f"compiled reference has {compiled_terrain} SkyMarker polygon(s) in Terrain* output"
        )
    if compiled_world_models:
        cautions.append(
            f"compiled reference has {compiled_world_models} SkyMarker polygon(s) in ordinary world-model output"
        )
    if compiled_total and not compiled_physics:
        cautions.append("compiled reference has SkyMarker residue but none in PhysicsBSP")
    if source_face_count and not compiled_total and not blockers:
        cautions.append("source ED has SkyMarker shell faces but compiled DAT has no SkyMarker residue")
    if generated_visibility:
        blockers.append(
            f"generated compiled DAT has {generated_visibility} SkyMarker polygon(s) in VisBSP; helper shell is visible-risk"
        )
    if compiled_total != len(compiled_residue_polygons):
        cautions.append(
            f"compiled helper summary counted {compiled_total} SkyMarker polygon(s), but geometry correlation extracted {len(compiled_residue_polygons)}"
        )
    if residue_matches and unmatched_residue_count:
        cautions.append(
            f"{unmatched_residue_count}/{len(residue_matches)} compiled SkyMarker residue polygon(s) could not be correlated to source faces"
        )

    ratio: Optional[float] = None
    if source_face_count:
        ratio = float(compiled_physics) / float(source_face_count)
        notes.append(
            f"Compiled PhysicsBSP keeps {compiled_physics}/{source_face_count} source SkyMarker face-equivalent residue(s)."
        )
    if compiled_physics and not compiled_visibility and not compiled_terrain:
        notes.append("Compiled SkyMarker residue is PhysicsBSP-only, matching the desired hidden-helper target.")
    if source_flag_counts:
        notes.append(
            "Source SkyMarker Brush flags: "
            + ", ".join(f"{name}={count}" for name, count in sorted(source_flag_counts.items()))
            + "."
        )
    if residue_matches:
        notes.append(
            f"Correlated {len(matched_residue_matches)}/{len(residue_matches)} compiled SkyMarker residue polygon(s) to source ED SkyMarker faces."
        )
    if matched_flag_counts:
        notes.append(
            "Matched residue source Brush flags: "
            + ", ".join(f"{name}={count}" for name, count in sorted(matched_flag_counts.items()))
            + "."
        )
    if matched_residue_matches and max_match_plane_distance is not None and min_match_normal_dot is not None:
        if max_match_plane_distance <= 1.0 and min_match_normal_dot >= 0.999:
            notes.append(
                "Matched residues share source SkyMarker face planes/normals; center deltas are expected from Processor clipping or polygon merging."
            )
    if matched_source_face_evidence and unmatched_source_face_evidence:
        notes.append(
            f"Source SkyMarker face cohorts: matched={len(matched_source_face_evidence)}, unmatched={len(unmatched_source_face_evidence)}."
        )
    if residue_rule_candidates and not exact_non_oracle_rule_candidates:
        if best_non_oracle_rule is not None:
            notes.append(
                "No non-oracle SkyMarker source-face rule is exact yet; "
                f"best current heuristic is {best_non_oracle_rule.rule_name} "
                f"(precision={_optional_float_text(best_non_oracle_rule.precision)}, "
                f"recall={_optional_float_text(best_non_oracle_rule.recall)})."
            )
        else:
            notes.append("No non-oracle SkyMarker source-face rule candidates were available.")

    if blockers:
        status = "sky_marker_compiled_residue_blocked"
    elif cautions:
        status = "sky_marker_compiled_residue_with_cautions"
    else:
        status = "sky_marker_compiled_residue_report_built"

    return SkyMarkerCompiledResidueReport(
        status=status,
        source_ed_path=source_ed,
        compiled_dat_path=compiled_dat,
        generated_compiled_dat_path=generated_dat,
        source_sky_marker_brush_count=len(source_brushes),
        source_sky_marker_face_count=source_face_count,
        source_brush_flag_counts=dict(source_flag_counts),
        compiled_sky_visibility_polygon_count=compiled_total,
        compiled_physics_sky_visibility_polygon_count=compiled_physics,
        compiled_visibility_sky_visibility_polygon_count=compiled_visibility,
        compiled_terrain_sky_visibility_polygon_count=compiled_terrain,
        compiled_world_model_sky_visibility_polygon_count=compiled_world_models,
        generated_sky_visibility_polygon_count=generated_total,
        generated_physics_sky_visibility_polygon_count=generated_physics,
        generated_visibility_sky_visibility_polygon_count=generated_visibility,
        generated_terrain_sky_visibility_polygon_count=generated_terrain,
        generated_world_model_sky_visibility_polygon_count=generated_world_models,
        source_to_compiled_ratio=ratio,
        compiled_residue_match_count=len(matched_residue_matches),
        compiled_residue_unmatched_count=unmatched_residue_count,
        matched_source_sky_marker_face_count=len(matched_source_faces),
        matched_source_sky_marker_brush_count=len(matched_source_brushes),
        matched_source_brush_flag_counts=dict(sorted(matched_flag_counts.items())),
        max_match_center_distance=max_match_center_distance,
        max_match_plane_distance=max_match_plane_distance,
        min_match_normal_dot=min_match_normal_dot,
        source_face_matched_summary=matched_source_face_summary,
        source_face_unmatched_summary=unmatched_source_face_summary,
        residue_rule_candidates=residue_rule_candidates,
        source_sky_marker_brushes=source_brushes,
        compiled_residue_matches=residue_matches,
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def build_sky_marker_residue_compile_audit_report(
    *,
    source_dat_path: str,
    source_ed_path: str,
    reference_dat_path: str = "",
    work_dir: Optional[str] = None,
    model_names: Sequence[str] = ("MonsterDoor1",),
    group_name: str = "GeneratedSkyMarkerResidueDiagnostic",
    output_filename: str = "",
    compiled_dat_path: str = "",
    processor_log_paths: Sequence[str] = (),
) -> SkyMarkerResidueCompileAuditReport:
    """Generate and optionally post-audit the SkyMarker matched-face diagnostic.

    The generated ED is a Processor experiment, not a game-bound default.  When
    *compiled_dat_path* is supplied, this report runs the helper-leakage guard
    against *reference_dat_path* (or the source DAT by default).
    """
    source_dat = os.path.abspath(source_dat_path) if source_dat_path else ""
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    reference_dat = os.path.abspath(reference_dat_path) if reference_dat_path else source_dat
    compiled_dat = os.path.abspath(compiled_dat_path) if compiled_dat_path else ""
    logs = tuple(os.path.abspath(path) for path in processor_log_paths if path)
    work_root = os.path.abspath(work_dir) if work_dir else tempfile.mkdtemp(prefix="mm9_skyresidue_")
    source_stem = _safe_filename_component(os.path.splitext(os.path.basename(source_dat or "world"))[0])
    generated_filename = (
        os.path.basename(output_filename)
        if output_filename
        else f"{source_stem}_sky_marker_residue_diagnostic.ed"
    )
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = [
        "SkyMarker residue compile audits prepare a matched-face ED diagnostic and validate the compiled DAT helper placement after Processor.",
        "The residue face set is an oracle diagnostic derived from a shipped compiled DAT reference, not a standalone reconstruction rule.",
    ]
    manual_steps: List[str] = [
        "Open the generated ED in old DEDit and confirm the SkyMarker residue Brush records are present with only the matched source faces.",
        "Save/compile the world through LithTech 2.1 Processor using the normal MM9 project data directory.",
        "Pass the resulting DAT path back into this audit report as compiled_dat_path.",
        "Accept the diagnostic only if helper leakage is clear: no SkyMarker textures in VisBSP or Terrain* output, and no excess helper placement versus the shipped reference.",
    ]

    residue_report: Optional[SkyMarkerCompiledResidueReport] = None
    if source_ed and reference_dat:
        residue_report = build_sky_marker_compiled_residue_report(
            source_ed_path=source_ed,
            compiled_dat_path=reference_dat,
        )
        if residue_report.blockers:
            blockers.append("SkyMarker residue reference correlation failed")
            blockers.extend(residue_report.blockers)
        elif residue_report.compiled_residue_match_count <= 0:
            blockers.append("SkyMarker residue reference correlation produced no matched faces")
        else:
            notes.append(
                f"Reference correlation matched {residue_report.compiled_residue_match_count} compiled residue polygon(s) to {residue_report.matched_source_sky_marker_face_count} source face(s)."
            )
    else:
        if not source_ed:
            blockers.append("source ED oracle was not supplied")
        if not reference_dat:
            blockers.append("compiled DAT reference was not supplied")

    acceptance: Optional[FullWorldSkeletonAcceptanceReport] = None
    if not blockers:
        acceptance = build_full_world_skeleton_acceptance_report(
            source_dat_path=source_dat,
            model_names=model_names,
            group_name=group_name,
            work_dir=work_root,
            output_filename=generated_filename,
            include_sky_objects=True,
            sky_source_ed_path=source_ed,
            include_sky_marker_residue_brushes=True,
            sky_marker_residue_reference_dat_path=reference_dat,
            max_models=512,
            max_model_points=16384,
            max_model_polygons=16384,
            max_total_points=65536,
            max_total_polygons=65536,
        )
        if acceptance.status != "ready_for_manual_full_world_skeleton_test":
            blockers.append(f"SkyMarker residue diagnostic ED generation failed: {acceptance.status}")
            blockers.extend(acceptance.blockers)
        else:
            notes.append(
                f"Generated SkyMarker residue diagnostic ED: {acceptance.generated_ed_path}."
            )
            notes.append(
                f"Generated diagnostic contains {acceptance.model_count} Brush record(s) and {acceptance.polygon_count} polygon(s)."
            )
            cautions.extend(acceptance.cautions)

    helper_leakage: Optional[CompiledDatHelperLeakageReport] = None
    if compiled_dat:
        helper_leakage = build_compiled_dat_helper_leakage_report(
            compiled_dat,
            reference_dat_path=reference_dat,
        )
        if helper_leakage.status == "helper_leakage_detected":
            blockers.append("compiled SkyMarker residue diagnostic has helper texture leakage")
            blockers.extend(helper_leakage.blockers)
        elif helper_leakage.status == "helper_leakage_cautions":
            cautions.append("compiled SkyMarker residue diagnostic differs from helper reference")
            cautions.extend(helper_leakage.cautions)
        elif helper_leakage.status == "helper_leakage_clear":
            notes.append("compiled SkyMarker residue diagnostic helper leakage check passed")
        elif helper_leakage.blockers:
            blockers.append(f"compiled helper leakage check failed: {helper_leakage.status}")
            blockers.extend(helper_leakage.blockers)
        elif helper_leakage.cautions:
            cautions.append(f"compiled helper leakage check warning: {helper_leakage.status}")
            cautions.extend(helper_leakage.cautions)
    else:
        cautions.append("compiled DAT was not supplied; Processor/leakage audit is pending")

    processor_logs = tuple(_parse_processor_log(path) for path in logs)
    for log in processor_logs:
        if log.status == "missing":
            cautions.append(f"Processor log was not found: {log.path}")
        elif log.problem_brush_count:
            cautions.append(f"Processor reported {log.problem_brush_count} problem brush(es)")
        if log.warnings:
            cautions.append("Processor emitted warning(s)")

    if blockers:
        status = "sky_marker_residue_compile_audit_failed"
    elif helper_leakage is None:
        status = "sky_marker_residue_candidate_ready_for_processor"
    elif helper_leakage.status == "helper_leakage_clear":
        status = "sky_marker_residue_helper_leakage_clear"
    else:
        status = "sky_marker_residue_compile_audit_with_cautions"

    return SkyMarkerResidueCompileAuditReport(
        status=status,
        source_dat_path=source_dat,
        source_ed_path=source_ed,
        reference_dat_path=reference_dat,
        work_dir=work_root,
        generated_ed_path=acceptance.generated_ed_path if acceptance is not None else "",
        compiled_dat_path=compiled_dat,
        processor_log_paths=logs,
        acceptance=acceptance,
        residue_report=residue_report,
        helper_leakage=helper_leakage,
        processor_logs=processor_logs,
        manual_steps=tuple(manual_steps),
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def build_sound_helper_reconstruction_report(
    *,
    source_dat_path: str,
    source_ed_path: str = "",
) -> SoundHelperReconstructionReport:
    """Report DAT SoundOnly evidence and source ED AmbientSound object records."""
    source_dat = os.path.abspath(source_dat_path)
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = [
        "Sound helper reconstruction reports are semantic diagnostics; full-world generation can copy source ED AmbientSound objects when enabled.",
        "DAT SoundOnly helpers are often embedded in PhysicsBSP/VisBSP rather than isolated same-name helper models.",
        "SoundOnly Brush volume reconstruction remains diagnostic-only until compiled-DAT leakage is understood.",
    ]

    try:
        from core import bsp

        with open(source_dat, "rb") as f:
            parsed = bsp.parse(f.read())
    except Exception as exc:
        return SoundHelperReconstructionReport(
            status="sound_helper_reconstruction_blocked",
            source_dat_path=source_dat,
            source_ed_path=source_ed,
            blockers=(f"DAT parse failed: {exc}",),
            notes=tuple(notes),
        )

    helper_models: List[Tuple[int, object, PrefabSurrogateCompositeModelSummary, Dict[str, int], bool]] = []
    for model_index, model in enumerate(getattr(parsed, "world_models", ()) or ()):
        helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
        if int(helper_roles.get("sound", 0)) <= 0:
            continue
        helper_models.append((
            int(model_index),
            model,
            _composite_model_summary(model),
            dict(helper_roles),
            bool(terrain_semantics.model_has_only_helper_textures(model)),
        ))

    source_sound_objects: Tuple[SoundObjectOracle, ...] = ()
    if source_ed:
        if not os.path.exists(source_ed):
            cautions.append(f"source ED oracle was not found: {source_ed}")
        else:
            try:
                source_sound_objects = _sound_object_oracles(source_ed)
            except Exception as exc:
                cautions.append(f"source ED oracle scan failed: {exc}")
    else:
        cautions.append("source ED oracle was not supplied; AmbientSound object copying is pending")

    candidates: List[SoundHelperReconstructionCandidate] = []
    for model_index, _model, summary, helper_roles, pure_helper in helper_models:
        candidate_notes: List[str] = []
        status = "source_sound_evidence"
        if pure_helper:
            status = "pure_sound_helper_source"
            candidate_notes.append("model uses only SoundOnly helper textures and can be reserved for sound semantics")
        else:
            candidate_notes.append("SoundOnly faces are embedded in mixed system geometry")
        candidates.append(SoundHelperReconstructionCandidate(
            source_model_name=summary.name,
            source_model_index=model_index,
            helper_roles=dict(helper_roles),
            pure_helper_model=bool(pure_helper),
            polygon_count=summary.polygon_count,
            bounds_min=summary.bounds_min,
            bounds_max=summary.bounds_max,
            center=summary.center,
            status=status,
            notes=tuple(candidate_notes),
        ))

    pure_count = sum(1 for _index, _model, _summary, _roles, pure in helper_models if pure)
    if blockers:
        status = "sound_helper_reconstruction_blocked"
    elif not helper_models and not source_sound_objects:
        status = "sound_helper_reconstruction_no_sound_evidence"
    elif not source_sound_objects:
        status = "sound_helper_reconstruction_needs_source_oracle"
    else:
        status = "sound_helper_reconstruction_report_built"

    return SoundHelperReconstructionReport(
        status=status,
        source_dat_path=source_dat,
        source_ed_path=source_ed,
        source_helper_model_count=len(helper_models),
        source_helper_polygon_count=sum(int(roles.get("sound", 0)) for _index, _model, _summary, roles, _pure in helper_models),
        source_sound_object_count=len(source_sound_objects),
        generated_object_count=len(source_sound_objects),
        pure_helper_model_count=pure_count,
        candidates=tuple(candidates),
        source_sound_objects=source_sound_objects,
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def build_gameplay_trigger_reconstruction_report(
    *,
    source_dat_path: str,
    source_ed_path: str = "",
) -> GameplayTriggerReconstructionReport:
    """Report source ED gameplay Trigger/ExitTrigger/PortalTrigger records."""
    source_dat = os.path.abspath(source_dat_path)
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = [
        "Gameplay trigger reconstruction reports are source-oracle diagnostics; full-world generation can copy Trigger, ExitTrigger, and PortalTrigger object records when enabled.",
        "Trigger helper PortalZone records are handled by the trigger-helper semantic path; this report covers runtime gameplay trigger objects only.",
    ]

    if not os.path.exists(source_dat):
        blockers.append(f"source DAT was not found: {source_dat}")
    else:
        try:
            from core import bsp

            with open(source_dat, "rb") as f:
                bsp.parse(f.read())
        except Exception as exc:
            blockers.append(f"DAT parse failed: {exc}")

    source_trigger_objects: Tuple[GameplayTriggerObjectOracle, ...] = ()
    if source_ed:
        if not os.path.exists(source_ed):
            cautions.append(f"source ED oracle was not found: {source_ed}")
        else:
            try:
                source_trigger_objects = _gameplay_trigger_object_oracles(source_ed)
            except Exception as exc:
                cautions.append(f"source ED oracle scan failed: {exc}")
    else:
        cautions.append("source ED oracle was not supplied; gameplay trigger object copying is pending")

    class_counts: Dict[str, int] = {}
    destination_worlds: List[str] = []
    portal_names: List[str] = []
    target_reference_count = 0
    for item in source_trigger_objects:
        class_counts[item.class_name] = class_counts.get(item.class_name, 0) + 1
        target_reference_count += int(item.target_count)
        if item.destination_world:
            destination_worlds.append(item.destination_world)
        if item.portal_name:
            portal_names.append(item.portal_name)

    if blockers:
        status = "gameplay_trigger_reconstruction_blocked"
    elif not source_trigger_objects:
        status = "gameplay_trigger_reconstruction_needs_source_oracle"
    else:
        status = "gameplay_trigger_reconstruction_report_built"

    return GameplayTriggerReconstructionReport(
        status=status,
        source_dat_path=source_dat,
        source_ed_path=source_ed,
        source_trigger_object_count=len(source_trigger_objects),
        generated_object_count=len(source_trigger_objects),
        class_counts=dict(sorted(class_counts.items())),
        target_reference_count=target_reference_count,
        destination_worlds=tuple(dict.fromkeys(destination_worlds)),
        portal_names=tuple(dict.fromkeys(portal_names)),
        source_trigger_objects=source_trigger_objects,
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def build_static_prop_reconstruction_report(
    *,
    source_dat_path: str,
    source_ed_path: str = "",
) -> StaticPropReconstructionReport:
    """Report source ED generic Prop object records for object-only copying."""
    source_dat = os.path.abspath(source_dat_path)
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = [
        "Static Prop reconstruction reports are source-oracle diagnostics; full-world generation can copy generic Prop object records when enabled.",
        "Behavior-rich prop subclasses are intentionally excluded from this first static-prop pass.",
    ]

    if not os.path.exists(source_dat):
        blockers.append(f"source DAT was not found: {source_dat}")
    else:
        try:
            from core import bsp

            with open(source_dat, "rb") as f:
                bsp.parse(f.read())
        except Exception as exc:
            blockers.append(f"DAT parse failed: {exc}")

    source_prop_objects: Tuple[StaticPropObjectOracle, ...] = ()
    if source_ed:
        if not os.path.exists(source_ed):
            cautions.append(f"source ED oracle was not found: {source_ed}")
        else:
            try:
                source_prop_objects = _static_prop_object_oracles(source_ed)
            except Exception as exc:
                cautions.append(f"source ED oracle scan failed: {exc}")
    else:
        cautions.append("source ED oracle was not supplied; static Prop object copying is pending")

    filename_counts: Dict[str, int] = {}
    skins = set()
    solid_count = 0
    move_to_floor_count = 0
    for item in source_prop_objects:
        if item.filename:
            filename_counts[item.filename] = filename_counts.get(item.filename, 0) + 1
        if item.skin:
            skins.add(item.skin)
        if item.solid is True:
            solid_count += 1
        if item.move_to_floor is True:
            move_to_floor_count += 1

    top_filenames = tuple(
        sorted(filename_counts.items(), key=lambda item: (-item[1], item[0].lower()))[:8]
    )
    if blockers:
        status = "static_prop_reconstruction_blocked"
    elif not source_prop_objects:
        status = "static_prop_reconstruction_needs_source_oracle"
    else:
        status = "static_prop_reconstruction_report_built"

    return StaticPropReconstructionReport(
        status=status,
        source_dat_path=source_dat,
        source_ed_path=source_ed,
        source_prop_object_count=len(source_prop_objects),
        generated_object_count=len(source_prop_objects),
        unique_model_count=len(filename_counts),
        unique_skin_count=len(skins),
        solid_count=solid_count,
        move_to_floor_count=move_to_floor_count,
        top_filenames=top_filenames,
        source_prop_objects=source_prop_objects,
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def build_behavior_prop_reconstruction_report(
    *,
    source_dat_path: str,
    source_ed_path: str = "",
) -> BehaviorPropReconstructionReport:
    """Report source ED behavior-rich prop subclasses before object copying."""
    source_dat = os.path.abspath(source_dat_path)
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = [
        "Behavior prop reconstruction reports are source-oracle diagnostics and manual-validation planning aids.",
        "Low-risk physical-decor and medium-light prop classes are default candidates after initial manual validation; high-risk classes remain explicit until class-specific manual validation succeeds.",
        "This report inventories prop subclasses that can carry fire, sound, light, loot, trigger, destructible, damage, or physical-decor semantics.",
    ]

    if not os.path.exists(source_dat):
        blockers.append(f"source DAT was not found: {source_dat}")
    else:
        try:
            from core import bsp

            with open(source_dat, "rb") as f:
                bsp.parse(f.read())
        except Exception as exc:
            blockers.append(f"DAT parse failed: {exc}")

    source_behavior_prop_objects: Tuple[BehaviorPropObjectOracle, ...] = ()
    if source_ed:
        if not os.path.exists(source_ed):
            cautions.append(f"source ED oracle was not found: {source_ed}")
        else:
            try:
                source_behavior_prop_objects = _behavior_prop_object_oracles(source_ed)
            except Exception as exc:
                cautions.append(f"source ED oracle scan failed: {exc}")
    else:
        cautions.append("source ED oracle was not supplied; behavior prop subclass inventory is pending")

    class_counts: Dict[str, int] = {}
    semantic_role_counts: Dict[str, int] = {}
    risk_level_counts: Dict[str, int] = {}
    filename_counts: Dict[str, int] = {}
    skins = set()
    solid_count = 0
    move_to_floor_count = 0
    for item in source_behavior_prop_objects:
        class_counts[item.class_name] = class_counts.get(item.class_name, 0) + 1
        risk_level_counts[item.risk_level] = risk_level_counts.get(item.risk_level, 0) + 1
        for role in item.semantic_roles:
            semantic_role_counts[role] = semantic_role_counts.get(role, 0) + 1
        if item.filename:
            filename_counts[item.filename] = filename_counts.get(item.filename, 0) + 1
        if item.skin:
            skins.add(item.skin)
        if item.solid is True:
            solid_count += 1
        if item.move_to_floor is True:
            move_to_floor_count += 1

    class_summaries: List[BehaviorPropClassSummary] = []
    for class_name in sorted(class_counts):
        items = tuple(item for item in source_behavior_prop_objects if item.class_name == class_name)
        item_models = {item.filename for item in items if item.filename}
        item_role_counts: Dict[str, int] = {}
        item_risk_counts: Dict[str, int] = {}
        for item in items:
            item_risk_counts[item.risk_level] = item_risk_counts.get(item.risk_level, 0) + 1
            for role in item.semantic_roles:
                item_role_counts[role] = item_role_counts.get(role, 0) + 1
        copy_pass_key = _behavior_prop_copy_pass_key(class_name)
        copy_pass_status = "explicit_copy_pass_available" if copy_pass_key else "not_implemented"
        class_summaries.append(BehaviorPropClassSummary(
            class_name=class_name,
            object_count=len(items),
            unique_model_count=len(item_models),
            solid_count=sum(1 for item in items if item.solid is True),
            move_to_floor_count=sum(1 for item in items if item.move_to_floor is True),
            semantic_role_counts=dict(sorted(item_role_counts.items())),
            risk_level_counts=dict(sorted(item_risk_counts.items())),
            copy_pass_key=copy_pass_key,
            copy_pass_status=copy_pass_status,
            validation_status=_behavior_prop_validation_status(
                item_risk_counts,
                class_name=class_name,
                copy_pass_status=copy_pass_status,
            ),
            sample_names=tuple(item.name for item in items[:5]),
        ))

    top_filenames = tuple(
        sorted(filename_counts.items(), key=lambda item: (-item[1], item[0].lower()))[:8]
    )
    if blockers:
        status = "behavior_prop_reconstruction_blocked"
    elif not source_behavior_prop_objects:
        status = "behavior_prop_reconstruction_needs_source_oracle"
    else:
        status = "behavior_prop_reconstruction_report_built"

    if source_behavior_prop_objects:
        cautions.append(
            "Behavior prop subclasses have class-scoped copy passes; use this report to choose medium/high-risk manual validation candidates before changing broader UI defaults."
        )
    if int(risk_level_counts.get("high", 0)) > 0:
        cautions.append(
            "High-risk behavior props include loot, trigger, destructible, or damage semantics and should not be auto-enabled before class-specific manual validation."
        )

    return BehaviorPropReconstructionReport(
        status=status,
        source_dat_path=source_dat,
        source_ed_path=source_ed,
        source_behavior_prop_object_count=len(source_behavior_prop_objects),
        copy_candidate_count=len(source_behavior_prop_objects),
        class_counts=dict(sorted(class_counts.items())),
        semantic_role_counts=dict(sorted(semantic_role_counts.items())),
        risk_level_counts=dict(sorted(risk_level_counts.items())),
        unique_model_count=len(filename_counts),
        unique_skin_count=len(skins),
        solid_count=solid_count,
        move_to_floor_count=move_to_floor_count,
        top_filenames=top_filenames,
        class_summaries=tuple(class_summaries),
        source_behavior_prop_objects=source_behavior_prop_objects,
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def build_collision_helper_reconstruction_report(
    *,
    source_dat_path: str,
    source_ed_path: str = "",
) -> CollisionHelperReconstructionReport:
    """Correlate DAT collision helper models with optional source ED object evidence."""
    source_dat = os.path.abspath(source_dat_path)
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = [
        "Collision helper reconstruction reports are semantic diagnostics; full-world generation can emit helper objects separately from diagnostic helper Brush shells.",
        "DAT collision helper models are identified by Invisible/Firethrough helper textures, then matched to same-name source ED or DAT object records.",
    ]

    try:
        from core import bsp

        with open(source_dat, "rb") as f:
            parsed = bsp.parse(f.read())
    except Exception as exc:
        return CollisionHelperReconstructionReport(
            status="collision_helper_reconstruction_blocked",
            source_dat_path=source_dat,
            source_ed_path=source_ed,
            blockers=(f"DAT parse failed: {exc}",),
            notes=tuple(notes),
        )

    helper_models: List[Tuple[int, object, PrefabSurrogateCompositeModelSummary, Dict[str, int]]] = []
    for model_index, model in enumerate(getattr(parsed, "world_models", ()) or ()):
        helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
        if int(helper_roles.get("collision", 0)) <= 0:
            continue
        if not terrain_semantics.model_has_only_helper_textures(model):
            continue
        if not set(helper_roles.keys()).issubset({"collision", "sprite"}):
            continue
        helper_models.append((
            int(model_index),
            model,
            _composite_model_summary(model),
            dict(helper_roles),
        ))

    candidate_names = tuple(summary.name for _index, _model, summary, _roles in helper_models)
    source_objects: Tuple[CollisionHelperOracleObject, ...] = ()
    source_helper_brushes: Tuple[CollisionHelperBrushOracle, ...] = ()
    if source_ed:
        if not os.path.exists(source_ed):
            cautions.append(f"source ED oracle was not found: {source_ed}")
        else:
            try:
                source_objects = _collision_helper_oracle_objects(
                    source_ed,
                    candidate_names=candidate_names,
                )
                source_helper_brushes = _collision_helper_brush_oracles(source_ed)
            except Exception as exc:
                cautions.append(f"source ED oracle scan failed: {exc}")
    else:
        cautions.append("source ED oracle was not supplied; same-name DAT helper object records will be used")

    dat_objects = _dat_collision_helper_dat_objects(
        source_dat,
        candidate_names=candidate_names,
    )
    if dat_objects:
        source_by_name = {item.name.lower(): item for item in source_objects}
        for item in dat_objects:
            source_by_name.setdefault(item.name.lower(), item)
        if len(source_by_name) > len(source_objects):
            notes.append(
                f"DAT-native collision helper object fallback added {len(source_by_name) - len(source_objects)} record(s)."
            )
        source_objects = tuple(source_by_name.values())

    objects_by_name = {item.name.lower(): item for item in source_objects}
    candidates: List[CollisionHelperReconstructionCandidate] = []
    for model_index, _model, summary, helper_roles in helper_models:
        candidate = _collision_helper_reconstruction_candidate(
            model_index,
            summary,
            helper_roles=helper_roles,
            source_objects_by_name=objects_by_name,
            source_helper_brushes=source_helper_brushes,
        )
        candidates.append(candidate)

    matched_count = sum(1 for candidate in candidates if candidate.status == "matched_source_collision_helper")
    skipped_count = sum(1 for candidate in candidates if candidate.status != "matched_source_collision_helper")

    if blockers:
        status = "collision_helper_reconstruction_blocked"
    elif not candidates:
        status = "collision_helper_reconstruction_no_collision_helpers"
    elif not source_objects:
        status = "collision_helper_reconstruction_needs_object_records"
    elif matched_count:
        status = "collision_helper_reconstruction_report_built"
    else:
        status = "collision_helper_reconstruction_no_matches"

    return CollisionHelperReconstructionReport(
        status=status,
        source_dat_path=source_dat,
        source_ed_path=source_ed,
        source_helper_model_count=len(helper_models),
        source_helper_polygon_count=sum(summary.polygon_count for _index, _model, summary, _roles in helper_models),
        source_object_count=len(source_objects),
        source_helper_brush_count=len(source_helper_brushes),
        matched_object_count=matched_count,
        skipped_candidate_count=skipped_count,
        candidates=tuple(candidates),
        source_objects=source_objects,
        source_helper_brushes=source_helper_brushes,
        blockers=tuple(_unique_text(blockers)),
        cautions=tuple(_unique_text(cautions)),
        notes=tuple(_unique_text(notes)),
    )


def build_trigger_helper_reconstruction_report(
    *,
    source_dat_path: str,
    source_ed_path: str = "",
) -> TriggerHelperReconstructionReport:
    """Correlate DAT trigger helper models with optional source ED PortalZone evidence."""
    source_dat = os.path.abspath(source_dat_path)
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    blockers: List[str] = []
    cautions: List[str] = []
    notes: List[str] = [
        "Trigger helper reconstruction reports are semantic diagnostics; full-world generation can emit PortalZone objects separately from diagnostic GreenScreen helper Brush shells.",
        "DAT trigger helper models are identified by GreenScreen helper textures, then matched to same-name source ED or DAT PortalZone records.",
    ]

    try:
        from core import bsp

        with open(source_dat, "rb") as f:
            parsed = bsp.parse(f.read())
    except Exception as exc:
        return TriggerHelperReconstructionReport(
            status="trigger_helper_reconstruction_blocked",
            source_dat_path=source_dat,
            source_ed_path=source_ed,
            blockers=(f"DAT parse failed: {exc}",),
            notes=tuple(notes),
        )

    helper_models: List[Tuple[int, object, PrefabSurrogateCompositeModelSummary, Dict[str, int]]] = []
    for model_index, model in enumerate(getattr(parsed, "world_models", ()) or ()):
        helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
        if int(helper_roles.get("trigger", 0)) <= 0:
            continue
        if not terrain_semantics.model_has_only_helper_textures(model):
            continue
        if set(helper_roles.keys()) != {"trigger"}:
            continue
        helper_models.append((
            int(model_index),
            model,
            _composite_model_summary(model),
            dict(helper_roles),
        ))

    candidate_names = tuple(summary.name for _index, _model, summary, _roles in helper_models)
    source_objects: Tuple[TriggerHelperOracleObject, ...] = ()
    source_helper_brushes: Tuple[TriggerHelperBrushOracle, ...] = ()
    if source_ed:
        if not os.path.exists(source_ed):
            cautions.append(f"source ED oracle was not found: {source_ed}")
        else:
            try:
                source_objects = _trigger_helper_oracle_objects(
                    source_ed,
                    candidate_names=candidate_names,
                )
                source_helper_brushes = _trigger_helper_brush_oracles(source_ed)
            except Exception as exc:
                cautions.append(f"source ED oracle scan failed: {exc}")
    else:
        cautions.append("source ED oracle was not supplied; same-name DAT PortalZone records will be used")

    dat_objects = _dat_trigger_helper_dat_objects(
        source_dat,
        candidate_names=candidate_names,
    )
    if dat_objects:
        source_by_name = {item.name.lower(): item for item in source_objects}
        for item in dat_objects:
            source_by_name.setdefault(item.name.lower(), item)
        if len(source_by_name) > len(source_objects):
            notes.append(
                f"DAT-native PortalZone fallback added {len(source_by_name) - len(source_objects)} record(s)."
            )
        source_objects = tuple(source_by_name.values())

    objects_by_name = {item.name.lower(): item for item in source_objects}
    candidates: List[TriggerHelperReconstructionCandidate] = []
    for model_index, _model, summary, helper_roles in helper_models:
        candidate = _trigger_helper_reconstruction_candidate(
            model_index,
            summary,
            helper_roles=helper_roles,
            source_objects_by_name=objects_by_name,
            source_helper_brushes=source_helper_brushes,
        )
        candidates.append(candidate)

    matched_count = sum(1 for candidate in candidates if candidate.status == "matched_source_trigger_helper")
    skipped_count = sum(1 for candidate in candidates if candidate.status != "matched_source_trigger_helper")

    if blockers:
        status = "trigger_helper_reconstruction_blocked"
    elif not candidates:
        status = "trigger_helper_reconstruction_no_trigger_helpers"
    elif not source_objects:
        status = "trigger_helper_reconstruction_needs_object_records"
    elif matched_count:
        status = "trigger_helper_reconstruction_report_built"
    else:
        status = "trigger_helper_reconstruction_no_matches"

    return TriggerHelperReconstructionReport(
        status=status,
        source_dat_path=source_dat,
        source_ed_path=source_ed,
        source_helper_model_count=len(helper_models),
        source_helper_polygon_count=sum(summary.polygon_count for _index, _model, summary, _roles in helper_models),
        source_object_count=len(source_objects),
        source_helper_brush_count=len(source_helper_brushes),
        matched_object_count=matched_count,
        skipped_candidate_count=skipped_count,
        candidates=tuple(candidates),
        source_objects=source_objects,
        source_helper_brushes=source_helper_brushes,
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
    _preparsed_world: Optional[object] = None,
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

    parsed = _preparsed_world
    if parsed is None:
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
    _preparsed_world: Optional[object] = None,
    _generated_ed_analysis_cache: Optional[Dict[str, object]] = None,
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

    parsed = _preparsed_world
    if parsed is None:
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

        scene = (
            _generated_ed_analysis_cache.get("geometry_scene")
            if _generated_ed_analysis_cache is not None
            else None
        )
        if scene is None:
            scene = legacy_ed.load_legacy_ed_geometry_scene(generated_ed)
            if _generated_ed_analysis_cache is not None:
                _generated_ed_analysis_cache["geometry_scene"] = scene
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


def _physics_shell_source_polygon_geometry(
    model: object,
    polygon: object,
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], float, Tuple[float, float, float], float]:
    points = tuple(getattr(model, "points", ()) or ())
    raw_indices = tuple(int(index) for index in (getattr(polygon, "vertex_indices", ()) or ()))
    polygon_points = tuple(
        tuple(float(value) for value in points[index])
        for index in raw_indices
        if 0 <= index < len(points)
    )
    if not polygon_points:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0.0, (0.0, 0.0, 0.0), 0.0
    bounds_min = tuple(min(point[axis] for point in polygon_points) for axis in range(3))
    bounds_max = tuple(max(point[axis] for point in polygon_points) for axis in range(3))
    area = float(terrain_reconstruction.polygon_area(polygon_points))
    normal, distance = terrain_reconstruction.polygon_plane(
        polygon_points,
        tuple(range(len(polygon_points))),
    )
    return bounds_min, bounds_max, area, normal, float(distance)


def _physics_shell_source_selection_reasons(
    model: object,
    candidates: Sequence[terrain_reconstruction.PhysicsShellCandidate],
    *,
    source_polygon_budget: int,
    source_polygon_indices: Sequence[int],
    focus_points: Sequence[object],
    focus_radius: float,
    focus_budget: int,
    focus_seed_radius: float,
    door_clearance_bounds: Sequence[Tuple[Tuple[float, float, float], Tuple[float, float, float]]],
    protected_bounds: Sequence[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = (),
    protected_roles: Sequence[str] = ("side_wall",),
    packing_mode: str = "balanced",
    role_weights: Optional[Mapping[str, float]] = None,
    playable_importance_weight: float = 0.0,
    generated_face_budget: int = 0,
    consolidation_index: Optional[
        terrain_reconstruction.PhysicsShellConsolidationIndex
    ] = None,
) -> Dict[int, str]:
    """Mirror shell selection and return a reason for each attempted index."""
    if int(source_polygon_budget) <= 0:
        return {}
    requested = {int(index) for index in source_polygon_indices}
    eligible = tuple(
        candidate for candidate in candidates
        if not requested or candidate.polygon_index in requested
    )
    if not eligible:
        return {}
    limit = max(1, int(source_polygon_budget))
    focus_selection = terrain_reconstruction.focused_balanced_physics_shell_candidates(
        eligible,
        limit,
        focus_points=focus_points,
        focus_radius=focus_radius,
        focus_budget=focus_budget,
        focus_seed_radius=focus_seed_radius,
    )
    reasons: Dict[int, str] = {}
    attempted_indices = set()
    selected_count = 0
    consolidation_index = (
        consolidation_index
        or terrain_reconstruction.build_physics_shell_consolidation_index(
            model,
            eligible,
        )
    )
    combined_protected_bounds = tuple(door_clearance_bounds) + tuple(protected_bounds)
    packing_mode_key = str(packing_mode or "balanced").strip().lower().replace("-", "_")
    if packing_mode_key == "cost_aware":
        plan = terrain_reconstruction.build_physics_shell_packing_plan(
            model,
            eligible,
            source_polygon_limit=limit,
            generated_face_budget=max(0, int(generated_face_budget)),
            consolidation_index=consolidation_index,
            protected_bounds=combined_protected_bounds,
            protected_roles=protected_roles,
            role_weights=role_weights,
            playable_importance_points=tuple(focus_points),
            playable_importance_radius=focus_radius,
            playable_importance_weight=playable_importance_weight,
        )
        for index in plan.protected_polygon_indices:
            candidate = next(
                (item for item in eligible if int(item.polygon_index) == int(index)),
                None,
            )
            reason = "selected_door_clearance"
            if candidate is not None and not _physics_shell_group_intersects_clearance_bounds_local(
                candidate.points,
                door_clearance_bounds,
            ):
                reason = "selected_protected_void"
            reasons[int(index)] = reason
        for group in plan.groups:
            for index in (int(item.polygon_index) for item in group.candidates):
                reasons[index] = "selected_for_shell_emission"
        return reasons

    def add_groups(ordered_candidates: Sequence[terrain_reconstruction.PhysicsShellCandidate]) -> None:
        nonlocal selected_count
        groups = terrain_reconstruction.consolidated_physics_shell_candidate_groups(
            model,
            ordered_candidates,
            consolidation_index=consolidation_index,
        )
        for group in groups:
            if selected_count + len(group.candidates) > limit:
                break
            indices = tuple(item.polygon_index for item in group.candidates)
            attempted_indices.update(indices)
            if terrain_reconstruction.physics_shell_group_intersects_bounds(
                group,
                combined_protected_bounds,
                roles=protected_roles,
            ):
                for index in indices:
                    reasons[index] = (
                        "selected_door_clearance"
                        if _physics_shell_group_intersects_clearance_bounds_local(
                            group.points,
                            door_clearance_bounds,
                        )
                        else "selected_protected_void"
                    )
                continue
            selected_count += len(indices)
            for index in indices:
                reasons[index] = "selected_for_shell_emission"

    add_groups(focus_selection.selected)
    if selected_count < limit:
        fallback = terrain_reconstruction.balanced_physics_shell_candidates(
            tuple(candidate for candidate in eligible if candidate.polygon_index not in attempted_indices),
            len(eligible),
        )
        add_groups(fallback)
    return reasons


def _physics_shell_group_intersects_clearance_bounds_local(
    points: Sequence[object],
    clearance_bounds: Sequence[Tuple[Tuple[float, float, float], Tuple[float, float, float]]],
) -> bool:
    if not points:
        return False
    group_min = tuple(min(float(point[axis]) for point in points) for axis in range(3))
    group_max = tuple(max(float(point[axis]) for point in points) for axis in range(3))
    return any(
        all(
            group_max[axis] >= bounds_min[axis] - 1.0e-5
            and group_min[axis] <= bounds_max[axis] + 1.0e-5
            for axis in range(3)
        )
        for bounds_min, bounds_max in clearance_bounds
    )


def _physics_shell_compiled_match_counts(
    source_model: object,
    candidates: Sequence[terrain_reconstruction.PhysicsShellCandidate],
    compiled_dat_path: str,
    physics_model_name: str,
) -> Tuple[Dict[int, int], Tuple[str, ...]]:
    if not compiled_dat_path:
        return {}, ()
    try:
        from core import bsp

        with open(compiled_dat_path, "rb") as handle:
            parsed = bsp.parse(handle.read())
    except Exception as exc:
        return {}, (f"compiled DAT parse failed: {exc}",)
    compiled_model = terrain_semantics.model_by_name(
        tuple(getattr(parsed, "world_models", ()) or ()),
        physics_model_name,
    )
    if compiled_model is None:
        return {}, (f"compiled DAT PhysicsBSP model was not found: {physics_model_name}",)

    compiled_geometry_buckets: Dict[Tuple[int, int, int, int], List[Tuple[object, ...]]] = defaultdict(list)
    bucket_size = 16.0
    area_bucket_size = 16.0
    for polygon in tuple(getattr(compiled_model, "polygons", ()) or ()):
        bounds_min, bounds_max, area, normal, distance = _physics_shell_source_polygon_geometry(
            compiled_model,
            polygon,
        )
        if area <= 0.0:
            continue
        item = (
            bounds_min,
            bounds_max,
            area,
            normal,
            distance,
            str(compiled_model.texture_name_for(polygon) or "").lower(),
        )
        center = tuple((bounds_min[axis] + bounds_max[axis]) * 0.5 for axis in range(3))
        key = (
            math.floor(center[0] / bucket_size),
            math.floor(center[1] / bucket_size),
            math.floor(center[2] / bucket_size),
            math.floor(area / area_bucket_size),
        )
        compiled_geometry_buckets[key].append(item)

    matches: Dict[int, int] = {}
    for candidate in candidates:
        bounds_min = tuple(min(point[axis] for point in candidate.points) for axis in range(3))
        bounds_max = tuple(max(point[axis] for point in candidate.points) for axis in range(3))
        area = max(0.0, float(candidate.area))
        normal, distance = terrain_reconstruction.polygon_plane(
            candidate.points,
            tuple(range(len(candidate.points))),
        )
        source_texture = str(source_model.texture_name_for(candidate.polygon) or "").lower()
        center = tuple((bounds_min[axis] + bounds_max[axis]) * 0.5 for axis in range(3))
        center_key = (
            math.floor(center[0] / bucket_size),
            math.floor(center[1] / bucket_size),
            math.floor(center[2] / bucket_size),
            math.floor(area / area_bucket_size),
        )
        nearby_geometry: List[Tuple[object, ...]] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for da in (-1, 0, 1):
                        nearby_geometry.extend(compiled_geometry_buckets.get(
                            (center_key[0] + dx, center_key[1] + dy, center_key[2] + dz, center_key[3] + da),
                            (),
                        ))
        count = 0
        for other_min, other_max, other_area, other_normal, other_distance, other_texture in nearby_geometry:
            if any(
                abs(bounds_min[axis] - other_min[axis]) > 2.0
                or abs(bounds_max[axis] - other_max[axis]) > 2.0
                for axis in range(3)
            ):
                continue
            if abs(area - other_area) > max(2.0, area * 0.15):
                continue
            if source_texture and other_texture and source_texture != other_texture:
                continue
            dot = sum(normal[axis] * other_normal[axis] for axis in range(3))
            if abs(dot) < 0.985:
                continue
            if min(abs(distance - other_distance), abs(distance + other_distance)) > 2.0:
                continue
            count += 1
        if count:
            matches[int(candidate.polygon_index)] = count
    return matches, ()


def _physics_shell_hotspot_anchors(
    diagnostics: Sequence[PhysicsShellSourcePolygonDiagnostic],
    *,
    focus_points: Sequence[object],
    door_clearance_bounds: Sequence[Tuple[Tuple[float, float, float], Tuple[float, float, float]]],
    explicit_anchors: Sequence[Tuple[str, str, object]],
    default_radius: float,
) -> Tuple[Tuple[str, str, Tuple[float, float, float], float], ...]:
    anchors: List[Tuple[str, str, Tuple[float, float, float], float]] = []

    def finite_point(raw_point: object) -> Optional[Tuple[float, float, float]]:
        try:
            values = tuple(float(value) for value in raw_point)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if len(values) != 3 or not all(math.isfinite(value) for value in values):
            return None
        return values  # type: ignore[return-value]

    safe_default_radius = max(64.0, float(default_radius))
    for raw_name, raw_kind, raw_point in explicit_anchors:
        point = finite_point(raw_point)
        if point is None:
            continue
        anchors.append((str(raw_name or "anchor"), str(raw_kind or "anchor"), point, safe_default_radius))

    for index, raw_point in enumerate(focus_points):
        point = finite_point(raw_point)
        if point is None:
            continue
        anchors.append((f"StartPoint{index}", "startpoint", point, safe_default_radius))

    for index, (bounds_min, bounds_max) in enumerate(door_clearance_bounds):
        center = tuple((float(bounds_min[axis]) + float(bounds_max[axis])) * 0.5 for axis in range(3))
        radius = min(192.0, safe_default_radius)
        anchors.append((f"DoorClearance{index}", "door", center, radius))

    # A stair hotspot is deliberately a local diagnostic hint, not a claim that
    # every stepped surface has been semantically identified.  Nearby floor
    # surfaces with a measurable height range are the useful first candidate.
    for index, (name, _kind, point, _radius) in enumerate(tuple(anchors)):
        if _kind != "startpoint":
            continue
        nearby = [
            item for item in diagnostics
            if item.role == "floor"
            and math.dist(item.bounds_min, point) <= safe_default_radius
        ]
        nearby.sort(key=lambda item: math.dist(item.bounds_min, point))
        nearby = nearby[:64]
        if len(nearby) < 3:
            continue
        y_values = [
            (item.bounds_min[1] + item.bounds_max[1]) * 0.5
            for item in nearby
        ]
        y_range = max(y_values) - min(y_values)
        if y_range < 8.0 or y_range > 256.0:
            continue
        center = tuple(
            sum((item.bounds_min[axis] + item.bounds_max[axis]) * 0.5 for item in nearby) / len(nearby)
            for axis in range(3)
        )
        anchors.append((f"{name}_stair_candidate", "stair", center, min(192.0, safe_default_radius)))

    return tuple(anchors)


def _physics_shell_coverage_hotspots(
    diagnostics: Sequence[PhysicsShellSourcePolygonDiagnostic],
    *,
    focus_points: Sequence[object],
    door_clearance_bounds: Sequence[Tuple[Tuple[float, float, float], Tuple[float, float, float]]],
    explicit_anchors: Sequence[Tuple[str, str, object]],
    default_radius: float,
) -> Tuple[PhysicsShellCoverageHotspot, ...]:
    anchors = _physics_shell_hotspot_anchors(
        diagnostics,
        focus_points=focus_points,
        door_clearance_bounds=door_clearance_bounds,
        explicit_anchors=explicit_anchors,
        default_radius=default_radius,
    )
    hotspots: List[PhysicsShellCoverageHotspot] = []
    actionable_statuses = {"not_selected", "selected_not_emitted"}
    protected_statuses = {"excluded_door_clearance", "excluded_protected_void"}
    for name, kind, center, radius in anchors:
        radius_sq = float(radius) * float(radius)
        local = [
            item for item in diagnostics
            if sum(
                ((item.bounds_min[axis] + item.bounds_max[axis]) * 0.5 - center[axis]) ** 2
                for axis in range(3)
            ) <= radius_sq
        ]
        if not local:
            continue
        status_counts = Counter(item.status for item in local)
        role_counts = Counter(item.role for item in local)
        emitted = [item for item in local if item.status == "emitted_ed"]
        actionable = [item for item in local if item.status in actionable_statuses]
        protected = [item for item in local if item.status in protected_statuses]
        invalid = [item for item in local if item.status == "invalid_source_geometry"]
        source_area = sum(max(0.0, item.area) for item in local)
        emitted_area = sum(max(0.0, item.area) for item in emitted)
        actionable_area = sum(max(0.0, item.area) for item in actionable)
        priority_score = actionable_area + len(actionable) * 64.0 + len(protected) * 8.0
        top_missing = tuple(
            item.source_polygon_index
            for item in sorted(actionable, key=lambda item: (-item.area, item.source_polygon_index))[:16]
        )
        hotspots.append(PhysicsShellCoverageHotspot(
            name=name,
            anchor_kind=kind,
            center=center,
            radius=radius,
            source_polygon_count=len(local),
            emitted_polygon_count=len(emitted),
            actionable_missing_polygon_count=len(actionable),
            protected_polygon_count=len(protected),
            invalid_polygon_count=len(invalid),
            source_area=source_area,
            emitted_area=emitted_area,
            actionable_missing_area=actionable_area,
            priority_score=priority_score,
            role_counts=tuple(sorted(role_counts.items())),
            status_counts=tuple(sorted(status_counts.items())),
            top_missing_polygon_indices=top_missing,
        ))
    return tuple(sorted(hotspots, key=lambda item: (-item.priority_score, item.name.lower())))


def build_physics_shell_source_coverage_report(
    *,
    source_dat_path: str,
    generated_ed_path: str,
    physics_model_name: str = "PhysicsBSP",
    packing_mode: str = "balanced",
    role_weights: Optional[Mapping[str, float]] = None,
    playable_importance_weight: float = 0.0,
    generated_shell_name_prefix: str = "PhysicsShell",
    compiled_dat_path: str = "",
    source_polygon_budget: int = 0,
    source_polygon_indices: Sequence[int] = (),
    focus_points: Sequence[object] = (),
    focus_radius: float = 0.0,
    focus_budget: int = 0,
    focus_seed_radius: float = 0.0,
    door_clearance_bounds: Sequence[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = (),
    protected_bounds: Sequence[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = (),
    protected_roles: Sequence[str] = ("side_wall",),
    hotspot_anchor_points: Sequence[Tuple[str, str, object]] = (),
    hotspot_radius: float = 0.0,
    include_stair_assembly_detection: bool = False,
    stair_assembly_indices: Sequence[int] = (),
    selected_stair_assembly_indices: Sequence[int] = (),
    rejected_stair_assembly_indices: Sequence[int] = (),
    subset_plan: Optional[PhysicsShellSubsetPlan] = None,
    generated_face_budget: int = 0,
    _preparsed_world: Optional[object] = None,
    _precomputed_physics_shell_candidates: Optional[
        Sequence[terrain_reconstruction.PhysicsShellCandidate]
    ] = None,
    _precomputed_physics_shell_consolidation_index: Optional[
        terrain_reconstruction.PhysicsShellConsolidationIndex
    ] = None,
    _precomputed_physics_shell_selection_reasons: Optional[Mapping[int, str]] = None,
    _generated_ed_analysis_cache: Optional[Dict[str, object]] = None,
) -> PhysicsShellSourceCoverageReport:
    """Account for source PhysicsBSP polygons through ED and compiled output.

    The historical report only counted provenance indices in generated Brush
    names.  Optional selection parameters reproduce the shell selector so each
    source polygon receives a reason code; ``compiled_dat_path`` adds a
    tolerant geometry match against the processed PhysicsBSP model.  A supplied
    ``subset_plan`` joins controlled Processor log evidence back to source
    polygon diagnostics.
    """
    source_dat = os.path.abspath(source_dat_path)
    generated_ed = os.path.abspath(generated_ed_path)
    compiled_dat = os.path.abspath(compiled_dat_path) if compiled_dat_path else ""
    physics_name = str(physics_model_name or terrain_semantics.PHYSICS_BSP_MODEL)
    shell_prefix = _legacy_name_component(generated_shell_name_prefix or "PhysicsShell")
    packing_mode_key = str(packing_mode or "balanced").strip().lower().replace("-", "_")
    if packing_mode_key not in {"balanced", "cost_aware"}:
        packing_mode_key = "balanced"
    cautions: List[str] = [
        "PhysicsBSP shell provenance is polygon-index based; optional compiled geometry matching still does not prove Processor kept every generated slab surface.",
        "Generated shell slabs are diagnostic source-like brushes, not recovered original authoring CSG.",
    ]
    notes: List[str] = [
        "Classifies every source PhysicsBSP polygon as floor, ceiling, side wall, helper/special, or degenerate.",
    ]
    if protected_bounds:
        notes.append(
            "Selection accounting includes explicit protected void bounds for roles: "
            + ", ".join(str(role) for role in protected_roles)
            + "."
        )
    if not os.path.exists(source_dat):
        return PhysicsShellSourceCoverageReport(
            status="source_dat_missing",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            physics_model_name=physics_name,
            packing_mode=packing_mode_key,
            blockers=(f"source DAT was not found: {source_dat}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )
    if not os.path.exists(generated_ed):
        return PhysicsShellSourceCoverageReport(
            status="generated_ed_missing",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            physics_model_name=physics_name,
            packing_mode=packing_mode_key,
            blockers=(f"generated ED was not found: {generated_ed}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    parsed = _preparsed_world
    if parsed is None:
        try:
            from core import bsp

            with open(source_dat, "rb") as f:
                parsed = bsp.parse(f.read())
        except Exception as exc:
            return PhysicsShellSourceCoverageReport(
                status="dat_parse_failed",
                source_dat_path=source_dat,
                generated_ed_path=generated_ed,
                physics_model_name=physics_name,
                packing_mode=packing_mode_key,
                blockers=(f"DAT parse failed: {exc}",),
                cautions=tuple(cautions),
                notes=tuple(notes),
            )
    model = terrain_semantics.model_by_name(
        tuple(getattr(parsed, "world_models", ()) or ()),
        physics_name,
    )
    if model is None:
        return PhysicsShellSourceCoverageReport(
            status="physics_model_missing",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            physics_model_name=physics_name,
            packing_mode=packing_mode_key,
            blockers=(f"PhysicsBSP shell source model was not found: {physics_name}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )

    try:
        from features.dat_editing import legacy_ed

        layout = (
            _generated_ed_analysis_cache.get("node_layout")
            if _generated_ed_analysis_cache is not None
            else None
        )
        if layout is None:
            layout = legacy_ed.load_legacy_ed_node_layout_report(generated_ed)
            if _generated_ed_analysis_cache is not None:
                _generated_ed_analysis_cache["node_layout"] = layout
    except Exception as exc:
        return PhysicsShellSourceCoverageReport(
            status="generated_ed_parse_failed",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            physics_model_name=physics_name,
            packing_mode=packing_mode_key,
            source_polygon_count=len(getattr(model, "polygons", ()) or ()),
            blockers=(f"generated ED node layout parse failed: {exc}",),
            cautions=tuple(cautions),
            notes=tuple(notes),
        )
    if layout.status != "layout_parsed":
        return PhysicsShellSourceCoverageReport(
            status="generated_ed_layout_failed",
            source_dat_path=source_dat,
            generated_ed_path=generated_ed,
            physics_model_name=physics_name,
            packing_mode=packing_mode_key,
            source_polygon_count=len(getattr(model, "polygons", ()) or ()),
            blockers=tuple(layout.blockers or ("generated ED node layout did not parse",)),
            cautions=tuple(cautions),
            notes=tuple(_unique_text(notes + list(layout.notes))),
        )

    source_roles = _physics_shell_source_polygon_roles(model)
    generated_brush_indices = _generated_physics_shell_brush_indices(
        layout.brush_names,
        shell_prefix,
    )
    generated_indices = tuple(index for _name, index in generated_brush_indices)
    source_index_set = set(source_roles)
    generated_source_indices = tuple(index for index in generated_indices if index in source_index_set)
    generated_source_set = set(generated_source_indices)
    generated_unknown_indices = tuple(index for index in generated_indices if index not in source_index_set)
    generated_brush_attributions = tuple(
        PhysicsShellGeneratedBrushAttribution(
            brush_name=brush_name,
            source_model_name=physics_name,
            source_polygon_index=polygon_index,
            role=source_roles.get(polygon_index, "unknown"),
        )
        for brush_name, polygon_index in generated_brush_indices
        if polygon_index in source_index_set
    )
    generated_names_by_index: Dict[int, List[str]] = defaultdict(list)
    for brush_name, polygon_index in generated_brush_indices:
        if polygon_index in source_index_set:
            generated_names_by_index[int(polygon_index)].append(brush_name)

    source_candidates = tuple(
        _precomputed_physics_shell_candidates
        if _precomputed_physics_shell_candidates is not None
        else terrain_reconstruction.physics_shell_candidates(model)
    )
    source_candidate_by_index = {
        int(candidate.polygon_index): candidate
        for candidate in source_candidates
    }
    stair_assemblies = (
        terrain_reconstruction.detect_physics_shell_stair_assemblies(
            model,
            source_candidates,
            consolidation_index=_precomputed_physics_shell_consolidation_index,
        )
        if (
            include_stair_assembly_detection
            or stair_assembly_indices
            or selected_stair_assembly_indices
            or rejected_stair_assembly_indices
        )
        else ()
    )
    if stair_assemblies:
        notes.append(
            f"Detected {len(stair_assemblies)} conservative PhysicsBSP stair assembly candidate(s); "
            "atomic selection remains disabled pending Processor and route validation."
        )
    selection_reasons = (
        {int(index): str(reason) for index, reason in _precomputed_physics_shell_selection_reasons.items()}
        if _precomputed_physics_shell_selection_reasons is not None
        else _physics_shell_source_selection_reasons(
            model,
            source_candidates,
            source_polygon_budget=source_polygon_budget,
            source_polygon_indices=source_polygon_indices,
            focus_points=focus_points,
            focus_radius=focus_radius,
            focus_budget=focus_budget,
            focus_seed_radius=focus_seed_radius,
            door_clearance_bounds=door_clearance_bounds,
            protected_bounds=protected_bounds,
            protected_roles=protected_roles,
            packing_mode=packing_mode_key,
            role_weights=role_weights,
            playable_importance_weight=playable_importance_weight,
            generated_face_budget=generated_face_budget,
            consolidation_index=_precomputed_physics_shell_consolidation_index,
        )
    )
    stairs_by_index = {item.assembly_index: item for item in stair_assemblies}
    selected_stair_source_indices = set()
    for assembly_index in selected_stair_assembly_indices:
        assembly = stairs_by_index.get(int(assembly_index))
        if assembly is not None:
            for source_index in assembly.source_polygon_indices:
                selected_stair_source_indices.add(int(source_index))
                selection_reasons[int(source_index)] = "selected_for_shell_emission"
    for assembly_index in rejected_stair_assembly_indices:
        assembly = stairs_by_index.get(int(assembly_index))
        if assembly is not None:
            for source_index in assembly.source_polygon_indices:
                if int(source_index) not in selected_stair_source_indices:
                    selection_reasons[int(source_index)] = "rejected_stair_assembly"
    compiled_match_counts, compiled_match_notes = _physics_shell_compiled_match_counts(
        model,
        source_candidates,
        compiled_dat,
        physics_name,
    )
    compiled_match_available = bool(compiled_dat and not compiled_match_notes)
    cautions.extend(compiled_match_notes)
    subset_evidence_by_index: Dict[int, PhysicsShellSubsetPlanEntry] = {}
    if subset_plan is not None:
        for entry in subset_plan.entries:
            for polygon_index in entry.polygon_indices:
                subset_evidence_by_index[int(polygon_index)] = entry
    requested_index_set = {int(index) for index in source_polygon_indices}
    source_polygon_diagnostics: List[PhysicsShellSourcePolygonDiagnostic] = []
    status_counts: Dict[str, int] = defaultdict(int)
    loss_class_counts: Dict[str, int] = defaultdict(int)
    subset_status_counts: Dict[str, int] = defaultdict(int)
    for polygon_index, polygon in enumerate(tuple(getattr(model, "polygons", ()) or ())):
        index = int(polygon_index)
        role = source_roles.get(index, "unknown")
        bounds_min, bounds_max, area, _normal, _distance = _physics_shell_source_polygon_geometry(
            model,
            polygon,
        )
        generated_names = tuple(generated_names_by_index.get(index, ()))
        compiled_match_count = int(compiled_match_counts.get(index, 0))
        subset_entry = subset_evidence_by_index.get(index)
        subset_status = subset_entry.validation_status if subset_entry is not None else "not_run"
        subset_status_counts[subset_status] += 1
        if requested_index_set and index not in requested_index_set:
            status = "not_requested"
            reason = "excluded_by_requested_source_indices"
        elif role == "degenerate" or index not in source_candidate_by_index:
            status = "invalid_source_geometry"
            reason = "failed_shell_candidate_quality_checks"
        elif generated_names:
            status = "emitted_ed"
            reason = "emitted_shell_brush_provenance"
            if index not in selection_reasons:
                reason = "emitted_outside_predicted_selection"
        elif selection_reasons.get(index) == "selected_door_clearance":
            status = "excluded_door_clearance"
            reason = "selected_side_wall_intersects_door_clearance"
        elif selection_reasons.get(index) == "selected_protected_void":
            status = "excluded_protected_void"
            reason = "selected_polygon_intersects_explicit_protected_void"
        elif selection_reasons.get(index) == "rejected_stair_assembly":
            status = "excluded_stair_assembly"
            reason = "atomic_stair_assembly_rejected"
        elif index in selection_reasons:
            status = "selected_not_emitted"
            reason = "selected_but_no_generated_shell_brush"
        else:
            status = "not_selected"
            reason = (
                "not_selected_by_budget_or_focus"
                if int(source_polygon_budget) > 0
                else "not_emitted_without_selection_accounting"
            )
        # Keep the selection/emission status separate from the final loss
        # cause.  A source polygon can be selected and emitted into ED while
        # still disappearing during Processor compilation; compiled geometry
        # matches are the evidence that distinguishes that case from a
        # polygon that was never selected in the first place.
        if status == "not_requested":
            loss_class = "not_requested"
        elif status == "invalid_source_geometry":
            loss_class = "invalid_source_geometry"
        elif status == "excluded_door_clearance":
            loss_class = "protected_door_clearance"
        elif status == "excluded_protected_void":
            loss_class = "protected_void"
        elif status == "excluded_stair_assembly":
            loss_class = "stair_assembly_rejected"
        elif status == "not_selected":
            loss_class = (
                "not_selected"
                if int(source_polygon_budget) > 0
                else "selection_not_run"
            )
        elif status == "selected_not_emitted":
            loss_class = "ed_emission_failure"
        elif status == "emitted_ed":
            if not compiled_dat:
                loss_class = "compiled_match_not_checked"
            elif not compiled_match_available:
                loss_class = "compiled_match_unavailable"
            elif compiled_match_count > 0:
                loss_class = "survived_compilation"
            else:
                loss_class = "processor_removed_or_geometry_mismatch"
        else:
            loss_class = "unclassified"
        loss_class_counts[loss_class] += 1
        status_counts[status] += 1
        source_polygon_diagnostics.append(PhysicsShellSourcePolygonDiagnostic(
            source_polygon_index=index,
            role=role,
            status=status,
            reason=reason,
            loss_class=loss_class,
            area=area,
            bounds_min=bounds_min,
            bounds_max=bounds_max,
            generated_brush_names=generated_names,
            compiled_match_count=compiled_match_count,
            subset_role=subset_entry.role if subset_entry is not None else "",
            subset_batch_index=subset_entry.batch_index if subset_entry is not None else -1,
            subset_validation_status=subset_status,
            subset_problem_brush_count=(
                subset_entry.processor_problem_brush_count if subset_entry is not None else None
            ),
            subset_warning_count=subset_entry.processor_warning_count if subset_entry is not None else 0,
        ))
    coverage_hotspots = _physics_shell_coverage_hotspots(
        source_polygon_diagnostics,
        focus_points=focus_points,
        door_clearance_bounds=door_clearance_bounds,
        explicit_anchors=hotspot_anchor_points,
        default_radius=(
            float(hotspot_radius)
            if float(hotspot_radius) > 0.0
            else (float(focus_radius) if float(focus_radius) > 0.0 else 256.0)
        ),
    )
    compiled_matched_count = sum(1 for item in source_polygon_diagnostics if item.compiled_match_count > 0)
    compiled_unmatched_count = (
        len(source_candidates) - compiled_matched_count
        if compiled_dat
        else 0
    )
    if compiled_dat and compiled_unmatched_count:
        notes.append(
            f"{compiled_unmatched_count} writable source {physics_name} polygon(s) did not match a compiled PhysicsBSP polygon by geometry signature."
        )
    elif compiled_dat:
        notes.append("Every writable source PhysicsBSP polygon matched at least one compiled PhysicsBSP polygon by geometry signature.")
    role_summaries: List[PhysicsShellSourceCoverageRoleSummary] = []
    for role in _PHYSICS_SHELL_COVERAGE_ROLES:
        source_count = sum(1 for value in source_roles.values() if value == role)
        generated_count = sum(1 for index in generated_source_set if source_roles.get(index) == role)
        role_summaries.append(PhysicsShellSourceCoverageRoleSummary(
            role=role,
            source_polygon_count=source_count,
            generated_polygon_count=generated_count,
            uncovered_polygon_count=max(0, source_count - generated_count),
        ))
    uncovered_count = sum(item.uncovered_polygon_count for item in role_summaries)
    if generated_unknown_indices:
        cautions.append(
            f"{len(generated_unknown_indices)} generated shell brush name(s) referenced source polygon indices outside {physics_name}."
        )
    if not generated_source_indices:
        status = "no_generated_physics_shell_coverage"
        notes.append("No generated shell slab brush names matched source PhysicsBSP polygon indices.")
    elif uncovered_count:
        status = "physics_shell_source_coverage_has_gaps"
        notes.append(
            f"{uncovered_count} source {physics_name} polygon(s) are not represented by generated shell slab brushes."
        )
    else:
        status = "physics_shell_source_coverage_complete"
        notes.append("Every classified source PhysicsBSP polygon has a generated shell slab brush.")

    return PhysicsShellSourceCoverageReport(
        status=status,
        source_dat_path=source_dat,
        generated_ed_path=generated_ed,
        physics_model_name=physics_name,
        packing_mode=packing_mode_key,
        source_polygon_count=len(getattr(model, "polygons", ()) or ()),
        classified_source_polygon_count=len(source_roles),
        generated_source_polygon_count=len(generated_source_set),
        uncovered_source_polygon_count=uncovered_count,
        generated_unknown_polygon_count=len(generated_unknown_indices),
        compiled_dat_path=compiled_dat,
        compiled_matched_source_polygon_count=compiled_matched_count,
        compiled_unmatched_source_polygon_count=compiled_unmatched_count,
        diagnostic_status_counts=tuple(sorted(status_counts.items())),
        loss_class_counts=tuple(sorted(loss_class_counts.items())),
        source_polygon_diagnostics=tuple(source_polygon_diagnostics),
        coverage_hotspots=coverage_hotspots,
        stair_assemblies=stair_assemblies,
        subset_plan_status=subset_plan.status if subset_plan is not None else "not_supplied",
        subset_validation_status_counts=tuple(sorted(subset_status_counts.items())),
        subset_failed_batch_count=(
            sum(1 for entry in subset_plan.entries if entry.validation_status == "failed")
            if subset_plan is not None
            else 0
        ),
        role_summaries=tuple(role_summaries),
        generated_brush_attributions=generated_brush_attributions,
        generated_source_polygon_indices=generated_source_indices,
        generated_unknown_polygon_indices=generated_unknown_indices,
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


def _dat_native_object_value(prop: object) -> object:
    code = int(getattr(prop, "code", 0) or 0)
    value = getattr(prop, "value", None)
    if code in (4, 6) and isinstance(value, int):
        try:
            return struct.unpack("<f", struct.pack("<I", int(value) & 0xFFFFFFFF))[0]
        except (OverflowError, struct.error, ValueError):
            return float(value)
    if code == 5:
        return bool(value)
    return value


def _dat_native_object_properties(obj: object) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for prop in getattr(obj, "props", ()) or ():
        name = str(getattr(prop, "name", "") or "")
        if name:
            result[name] = _dat_native_object_value(prop)
    return result


def _legacy_ed_object_properties(record: object) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for prop in getattr(record, "properties", ()) or ():
        name = str(getattr(prop, "name", "") or "")
        if name:
            result[name] = getattr(prop, "value", None)
    return result


def _normalized_dat_native_object_value(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 5)
    if isinstance(value, (tuple, list)):
        return tuple(_normalized_dat_native_object_value(item) for item in value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _dat_native_named_records(records: Sequence[object], class_name: str) -> Dict[str, Dict[str, object]]:
    result: Dict[str, Dict[str, object]] = {}
    for index, record in enumerate(records):
        if hasattr(record, "props"):
            properties = _dat_native_object_properties(record)
        else:
            properties = _legacy_ed_object_properties(record)
        name = str(properties.get("Name", "") or "").strip()
        if not name:
            name = f"{class_name}{index}"
        result[name.lower()] = {"name": name, "properties": properties}
    return result


def _dat_native_records_by_class(
    records: Sequence[object],
    *,
    source: str,
) -> Dict[str, List[object]]:
    result: Dict[str, List[object]] = {}
    for record in records:
        if source == "dat":
            class_name = str(getattr(record, "type_str", "") or "")
        else:
            class_name = str(getattr(record, "class_name", "") or "")
        if class_name:
            result.setdefault(class_name.lower(), []).append(record)
    return result


def _dat_native_compare_named_records(
    left: Dict[str, Dict[str, object]],
    right: Dict[str, Dict[str, object]],
) -> Tuple[int, int, Tuple[str, ...]]:
    matched = 0
    mismatches = 0
    mismatch_names: List[str] = []
    for name in sorted(set(left) & set(right)):
        matched += 1
        left_properties = left[name]["properties"]
        right_properties = right[name]["properties"]
        if set(left_properties) != set(right_properties):
            mismatches += 1
            mismatch_names.append(str(left[name]["name"]))
            continue
        if any(
            _normalized_dat_native_object_value(left_properties[key])
            != _normalized_dat_native_object_value(right_properties[key])
            for key in left_properties
        ):
            mismatches += 1
            mismatch_names.append(str(left[name]["name"]))
    return matched, mismatches, tuple(mismatch_names)


def build_dat_native_object_comparison_report(
    source_dat_path: str,
    *,
    source_ed_path: str = "",
    generated_ed_path: str = "",
    class_names: Sequence[str] = DAT_NATIVE_OBJECT_SOURCE_ORACLE_CLASSES,
) -> DatNativeObjectComparisonReport:
    """Inventory DAT objects and compare selected classes with ED oracles.

    This is intentionally diagnostic: it does not promote any class into the
    generated world.  A class remains eligible for DAT-native reconstruction
    only after this report and the DEDit/Processor/game checks agree.
    """
    source_dat = os.path.abspath(source_dat_path) if source_dat_path else ""
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    generated_ed = os.path.abspath(generated_ed_path) if generated_ed_path else ""
    selected_classes = tuple(
        dict.fromkeys(str(name).strip() for name in class_names if str(name).strip())
    )
    notes: List[str] = []
    try:
        from mm9_patcher import mm9_patch as patcher

        with open(source_dat, "rb") as handle:
            dat_data = handle.read()
        dat_header = patcher.Header.parse(dat_data)
        dat_records, _object_end = patcher.parse_objects(dat_data, dat_header.obj_pos)
    except Exception as exc:
        return DatNativeObjectComparisonReport(
            status="dat_scan_failed",
            source_dat_path=source_dat,
            source_ed_path=source_ed,
            generated_ed_path=generated_ed,
            class_names=selected_classes,
            notes=(f"DAT object scan failed: {exc}",),
        )

    dat_by_class = _dat_native_records_by_class(dat_records, source="dat")
    source_records: List[object] = []
    generated_records: List[object] = []
    source_scan_failed = ""
    generated_scan_failed = ""
    if source_ed:
        try:
            from features.dat_editing import legacy_ed

            source_records = list(legacy_ed.load_legacy_ed_object_scan_report(source_ed).records)
        except Exception as exc:
            source_scan_failed = str(exc)
    if generated_ed:
        try:
            from features.dat_editing import legacy_ed

            generated_records = list(legacy_ed.load_legacy_ed_object_scan_report(generated_ed).records)
        except Exception as exc:
            generated_scan_failed = str(exc)
    source_by_class = _dat_native_records_by_class(source_records, source="ed")
    generated_by_class = _dat_native_records_by_class(generated_records, source="ed")
    if source_scan_failed:
        notes.append(f"Source ED object scan failed: {source_scan_failed}")
    if generated_scan_failed:
        notes.append(f"Generated ED object scan failed: {generated_scan_failed}")
    if not source_ed:
        notes.append("No source ED oracle was supplied; class entries are DAT inventory only.")

    class_reports: List[DatNativeObjectClassComparison] = []
    for class_name in selected_classes:
        key = class_name.lower()
        dat_named = _dat_native_named_records(dat_by_class.get(key, ()), class_name)
        source_named = _dat_native_named_records(source_by_class.get(key, ()), class_name)
        generated_named = _dat_native_named_records(generated_by_class.get(key, ()), class_name)
        matched, property_mismatches, property_mismatch_names = _dat_native_compare_named_records(
            dat_named,
            source_named,
        )
        source_generated_matched, source_generated_mismatches, source_generated_mismatch_names = _dat_native_compare_named_records(
            source_named,
            generated_named,
        )
        dat_keys = sorted({key_name for item in dat_named.values() for key_name in item["properties"]})
        source_keys = sorted({key_name for item in source_named.values() for key_name in item["properties"]})
        generated_keys = sorted({key_name for item in generated_named.values() for key_name in item["properties"]})
        dat_only = tuple(sorted(set(dat_named) - set(source_named)))
        source_only = tuple(sorted(set(source_named) - set(dat_named)))
        generated_only = tuple(sorted(set(generated_named) - set(source_named)))
        generated_missing = tuple(sorted(set(source_named) - set(generated_named)))
        generated_matches_source = (
            len(generated_named) == len(source_named)
            and source_generated_matched == len(source_named)
            and not generated_only
            and not generated_missing
            and source_generated_mismatches == 0
        )
        if not source_ed or source_scan_failed:
            status = "inventory_only"
        elif not dat_named and not source_named:
            status = "empty"
        elif (
            len(dat_named) == len(source_named)
            and matched == len(dat_named)
            and not dat_only
            and not source_only
            and property_mismatches == 0
            and (not generated_ed or generated_matches_source)
        ):
            status = "match"
        else:
            status = "differences_found"
        class_notes: List[str] = []
        if generated_ed and source_generated_mismatches:
            class_notes.append(
                f"{source_generated_mismatches} source/generated object record(s) differ in property keys or values."
            )
        class_reports.append(DatNativeObjectClassComparison(
            class_name=class_name,
            dat_count=len(dat_named),
            source_count=len(source_named),
            generated_count=len(generated_named),
            matched_name_count=matched,
            source_generated_matched_name_count=source_generated_matched,
            dat_only_names=dat_only[:32],
            source_only_names=source_only[:32],
            generated_only_names=generated_only[:32],
            generated_missing_names=generated_missing[:32],
            dat_property_keys=tuple(dat_keys),
            source_property_keys=tuple(source_keys),
            generated_property_keys=tuple(generated_keys),
            property_mismatch_count=property_mismatches,
            source_generated_property_mismatch_count=source_generated_mismatches,
            property_mismatch_names=property_mismatch_names[:32],
            source_generated_property_mismatch_names=source_generated_mismatch_names[:32],
            status=status,
            notes=tuple(class_notes),
        ))

    if source_scan_failed:
        status = "source_scan_failed"
    elif not source_ed:
        status = "inventory_only"
    elif all(item.status in {"match", "empty"} for item in class_reports):
        status = "match"
    else:
        status = "differences_found"
    notes.append(
        "DAT-native class promotion remains opt-in; this report is a pre-promotion comparison gate."
    )
    return DatNativeObjectComparisonReport(
        status=status,
        source_dat_path=source_dat,
        source_ed_path=source_ed,
        generated_ed_path=generated_ed,
        class_names=selected_classes,
        dat_object_count=len(dat_records),
        source_object_count=len(source_records),
        generated_object_count=len(generated_records),
        classes=tuple(class_reports),
        notes=tuple(notes),
    )


def format_dat_native_object_comparison_report(
    report: DatNativeObjectComparisonReport,
) -> str:
    lines = [
        "DAT-native object reconstruction comparison",
        f"status: {report.status}",
        f"DAT: {report.source_dat_path}",
        f"source ED oracle: {report.source_ed_path or 'not supplied'}",
        f"generated ED: {report.generated_ed_path or 'not supplied'}",
        (
            "object counts: "
            f"DAT={report.dat_object_count}, source={report.source_object_count}, "
            f"generated={report.generated_object_count}"
        ),
    ]
    for note in report.notes:
        lines.append(f"note: {note}")
    for item in report.classes:
        lines.append(
            f"- {item.class_name}: status={item.status}, "
            f"DAT={item.dat_count}, source={item.source_count}, generated={item.generated_count}, "
            f"matched={item.matched_name_count}, property_mismatches={item.property_mismatch_count}"
        )
        if item.dat_only_names:
            lines.append("  DAT-only: " + ", ".join(item.dat_only_names))
        if item.source_only_names:
            lines.append("  source-only: " + ", ".join(item.source_only_names))
        if item.generated_only_names:
            lines.append("  generated-only: " + ", ".join(item.generated_only_names))
        if item.generated_missing_names:
            lines.append("  generated-missing: " + ", ".join(item.generated_missing_names))
        if item.property_mismatch_names:
            lines.append("  DAT/source property mismatches: " + ", ".join(item.property_mismatch_names))
        if item.source_generated_property_mismatch_names:
            lines.append(
                "  source/generated property mismatches: "
                + ", ".join(item.source_generated_property_mismatch_names)
            )
        for note in item.notes:
            lines.append(f"  note: {note}")
    return "\n".join(lines)


def build_dat_native_object_comparison_manifest(
    report: DatNativeObjectComparisonReport,
) -> Dict[str, object]:
    return {
        "kind": "mm9_dat_native_object_comparison",
        "schema_version": 1,
        "status": report.status,
        "source_dat_path": report.source_dat_path,
        "source_ed_path": report.source_ed_path,
        "generated_ed_path": report.generated_ed_path,
        "class_names": list(report.class_names),
        "summary": {
            "dat_object_count": report.dat_object_count,
            "source_object_count": report.source_object_count,
            "generated_object_count": report.generated_object_count,
        },
        "classes": [
            {
                "class_name": item.class_name,
                "status": item.status,
                "dat_count": item.dat_count,
                "source_count": item.source_count,
                "generated_count": item.generated_count,
                "matched_name_count": item.matched_name_count,
                "source_generated_matched_name_count": item.source_generated_matched_name_count,
                "dat_only_names": list(item.dat_only_names),
                "source_only_names": list(item.source_only_names),
                "generated_only_names": list(item.generated_only_names),
                "generated_missing_names": list(item.generated_missing_names),
                "dat_property_keys": list(item.dat_property_keys),
                "source_property_keys": list(item.source_property_keys),
                "generated_property_keys": list(item.generated_property_keys),
                "property_mismatch_count": item.property_mismatch_count,
                "source_generated_property_mismatch_count": item.source_generated_property_mismatch_count,
                "property_mismatch_names": list(item.property_mismatch_names),
                "source_generated_property_mismatch_names": list(item.source_generated_property_mismatch_names),
                "notes": list(item.notes),
            }
            for item in report.classes
        ],
        "notes": list(report.notes),
    }


def write_dat_native_object_comparison_manifest(
    report: DatNativeObjectComparisonReport,
    manifest_path: str,
) -> str:
    manifest = build_dat_native_object_comparison_manifest(report)
    absolute = os.path.abspath(manifest_path)
    os.makedirs(os.path.dirname(absolute) or ".", exist_ok=True)
    with open(absolute, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return absolute


def build_dat_to_ed_regression_matrix_report(
    worlds_dir: str,
    *,
    levels: Sequence[str] = DEFAULT_REGRESSION_MATRIX_LEVELS,
) -> DatToEdRegressionMatrixReport:
    """Aggregate safe DAT/source/helper diagnostics for shipped worlds.

    This matrix deliberately stops before DEDit, Processor, or game execution;
    it identifies which fixtures are ready for those manual gates and which
    ones still lack a source ED or helper object evidence.
    """
    from core import bsp

    root = os.path.abspath(worlds_dir)
    requested_levels = tuple(dict.fromkeys(str(level).strip().upper() for level in levels if str(level).strip()))
    entries: List[DatToEdRegressionMatrixEntry] = []
    for stem in requested_levels:
        dat_path = os.path.join(root, f"{stem}.DAT")
        if not os.path.exists(dat_path):
            lower_match = next(
                (
                    os.path.join(root, name)
                    for name in os.listdir(root) if name.lower() == f"{stem}.dat".lower()
                ),
                dat_path,
            ) if os.path.isdir(root) else dat_path
            dat_path = lower_match
        source_ed_path = os.path.join(root, f"{stem}.ED")
        if not os.path.exists(source_ed_path):
            source_ed_path = next(
                (
                    os.path.join(root, name)
                    for name in os.listdir(root) if name.lower() == f"{stem}.ed".lower()
                ),
                "",
            ) if os.path.isdir(root) else ""
        if not os.path.exists(dat_path):
            entries.append(DatToEdRegressionMatrixEntry(
                stem=stem,
                status="dat_missing",
                source_ed_path=os.path.abspath(source_ed_path) if source_ed_path else "",
                notes=(f"DAT fixture was not found under {root}.",),
            ))
            continue
        try:
            with open(dat_path, "rb") as handle:
                data = handle.read()
            parsed = bsp.parse(data)
            from mm9_patcher import mm9_patch as patcher

            header = patcher.Header.parse(data)
            dat_objects, _object_end = patcher.parse_objects(data, header.obj_pos)
        except Exception as exc:
            entries.append(DatToEdRegressionMatrixEntry(
                stem=stem,
                status="dat_parse_failed",
                dat_path=os.path.abspath(dat_path),
                source_ed_path=os.path.abspath(source_ed_path) if source_ed_path else "",
                notes=(f"DAT parse failed: {exc}",),
            ))
            continue

        helper_counts: Dict[str, int] = {}
        terrain_model_count = 0
        physics_polygon_count = 0
        polygon_count = 0
        for model in getattr(parsed, "world_models", ()) or ():
            polygons = len(getattr(model, "polygons", ()) or ())
            polygon_count += polygons
            name = str(getattr(model, "name", "") or "")
            if terrain_semantics.is_terrain_model(model):
                terrain_model_count += 1
            if terrain_semantics.is_physics_bsp_model(model):
                physics_polygon_count += polygons
            for role, count in terrain_semantics.helper_texture_roles_for_model(model).items():
                if int(count) > 0:
                    helper_counts[role] = helper_counts.get(role, 0) + 1

        dat_native = build_dat_native_object_comparison_report(
            dat_path,
            source_ed_path=source_ed_path,
        )
        collision = build_collision_helper_reconstruction_report(
            source_dat_path=dat_path,
            source_ed_path=source_ed_path,
        )
        trigger = build_trigger_helper_reconstruction_report(
            source_dat_path=dat_path,
            source_ed_path=source_ed_path,
        )
        source_exists = bool(source_ed_path and os.path.exists(source_ed_path))
        if source_exists and dat_native.status == "match":
            status = "ready_for_manual_matrix_review"
        elif dat_native.status == "inventory_only":
            status = "inventory_only"
        elif dat_native.status in {"differences_found", "source_scan_failed", "dat_scan_failed"}:
            status = "diagnostic_differences"
        else:
            status = "diagnostic_review"
        entries.append(DatToEdRegressionMatrixEntry(
            stem=stem,
            status=status,
            dat_path=os.path.abspath(dat_path),
            source_ed_path=os.path.abspath(source_ed_path) if source_ed_path else "",
            model_count=len(getattr(parsed, "world_models", ()) or ()),
            polygon_count=polygon_count,
            terrain_model_count=terrain_model_count,
            physics_polygon_count=physics_polygon_count,
            dat_object_count=len(dat_objects),
            source_object_count=dat_native.source_object_count,
            helper_model_counts=dict(sorted(helper_counts.items())),
            dat_native_status=dat_native.status,
            collision_helper_status=collision.status,
            trigger_helper_status=trigger.status,
            notes=tuple(_unique_text(tuple(dat_native.notes) + tuple(collision.notes) + tuple(trigger.notes))),
        ))

    ready_count = sum(item.status == "ready_for_manual_matrix_review" for item in entries)
    inventory_only_count = sum(item.status == "inventory_only" for item in entries)
    missing_count = sum(item.status == "dat_missing" for item in entries)
    failed_count = sum(item.status in {"dat_parse_failed", "diagnostic_differences"} for item in entries)
    if not entries:
        status = "empty_matrix"
    elif failed_count:
        status = "matrix_requires_review"
    elif missing_count:
        status = "matrix_has_missing_fixtures"
    else:
        status = "matrix_built"
    return DatToEdRegressionMatrixReport(
        status=status,
        worlds_dir=root,
        levels=requested_levels,
        entries=tuple(entries),
        ready_count=ready_count,
        inventory_only_count=inventory_only_count,
        missing_count=missing_count,
        failed_count=failed_count,
        notes=(
            "Matrix entries are static DAT/source diagnostics; DEDit, Processor, and game checks remain manual gates.",
            "Helper object statuses may be ready from DAT-native records even when helper Brush oracle counts are zero.",
        ),
    )


def format_dat_to_ed_regression_matrix_report(
    report: DatToEdRegressionMatrixReport,
) -> str:
    lines = [
        "DAT to ED regression matrix",
        f"status: {report.status}",
        f"worlds directory: {report.worlds_dir}",
        (
            "summary: "
            f"levels={len(report.entries)}, ready={report.ready_count}, "
            f"inventory_only={report.inventory_only_count}, missing={report.missing_count}, "
            f"failed_or_different={report.failed_count}"
        ),
    ]
    for note in report.notes:
        lines.append(f"note: {note}")
    for item in report.entries:
        helper_text = ", ".join(f"{key}={value}" for key, value in sorted(item.helper_model_counts.items())) or "none"
        lines.append(
            f"- {item.stem}: status={item.status}, models={item.model_count}, polygons={item.polygon_count}, "
            f"DAT_objects={item.dat_object_count}, source_objects={item.source_object_count}, "
            f"dat_native={item.dat_native_status}, collision={item.collision_helper_status}, "
            f"trigger={item.trigger_helper_status}, helpers={helper_text}"
        )
    return "\n".join(lines)


def build_dat_to_ed_regression_matrix_manifest(
    report: DatToEdRegressionMatrixReport,
) -> Dict[str, object]:
    return {
        "kind": "mm9_dat_to_ed_regression_matrix",
        "schema_version": 1,
        "status": report.status,
        "worlds_dir": report.worlds_dir,
        "levels": list(report.levels),
        "summary": {
            "ready_count": report.ready_count,
            "inventory_only_count": report.inventory_only_count,
            "missing_count": report.missing_count,
            "failed_count": report.failed_count,
        },
        "entries": [
            {
                "stem": item.stem,
                "status": item.status,
                "dat_path": item.dat_path,
                "source_ed_path": item.source_ed_path,
                "model_count": item.model_count,
                "polygon_count": item.polygon_count,
                "terrain_model_count": item.terrain_model_count,
                "physics_polygon_count": item.physics_polygon_count,
                "dat_object_count": item.dat_object_count,
                "source_object_count": item.source_object_count,
                "helper_model_counts": dict(item.helper_model_counts),
                "dat_native_status": item.dat_native_status,
                "collision_helper_status": item.collision_helper_status,
                "trigger_helper_status": item.trigger_helper_status,
                "notes": list(item.notes),
            }
            for item in report.entries
        ],
        "notes": list(report.notes),
    }


def write_dat_to_ed_regression_matrix_manifest(
    report: DatToEdRegressionMatrixReport,
    manifest_path: str,
) -> str:
    manifest = build_dat_to_ed_regression_matrix_manifest(report)
    absolute = os.path.abspath(manifest_path)
    os.makedirs(os.path.dirname(absolute) or ".", exist_ok=True)
    with open(absolute, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return absolute


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
    if report.physics_shell_focus_points and report.physics_shell_focus_radius > 0.0:
        lines.append(
            "PhysicsBSP shell focus: "
            f"anchors={len(report.physics_shell_focus_points)}, "
            f"radius={report.physics_shell_focus_radius:g}, "
            f"budget={report.physics_shell_focus_budget or 'all'}, "
            f"seed_radius={report.physics_shell_focus_seed_radius or report.physics_shell_focus_radius * 0.25:g}"
        )
    if report.include_door_objects:
        detail = "Door/RotatingDoor objects: included"
        if report.door_source_ed_path:
            detail += f", source={report.door_source_ed_path}"
        lines.append(detail)
    if report.door_behavior_context:
        lines.append(f"door behavior context: {report.door_behavior_context}")
    if report.include_airail_objects:
        detail = "AIRail objects: included"
        if report.airail_source_ed_path:
            detail += f", source={report.airail_source_ed_path}"
        lines.append(detail)
    if report.include_sky_objects:
        detail = "sky objects: included"
        if report.sky_source_ed_path:
            detail += f", source={report.sky_source_ed_path}"
        lines.append(detail)
    if report.include_sky_marker_brushes:
        detail = "SkyMarker Brushes: included"
        if report.sky_source_ed_path:
            detail += f", source={report.sky_source_ed_path}"
        lines.append(detail)
    if report.include_sky_marker_residue_brushes:
        detail = "SkyMarker residue Brushes: included"
        if report.sky_source_ed_path:
            detail += f", source={report.sky_source_ed_path}"
        if report.sky_marker_residue_reference_dat_path:
            detail += f", reference={report.sky_marker_residue_reference_dat_path}"
        lines.append(detail)
    if report.include_sound_objects:
        detail = "AmbientSound objects: included"
        if report.sound_source_ed_path:
            detail += f", source={report.sound_source_ed_path}"
        lines.append(detail)
    if report.include_gameplay_trigger_objects:
        detail = "gameplay trigger objects: included"
        if report.gameplay_trigger_source_ed_path:
            detail += f", source={report.gameplay_trigger_source_ed_path}"
        lines.append(detail)
    if report.include_static_prop_objects:
        detail = "static Prop objects: included"
        if report.static_prop_source_ed_path:
            detail += f", source={report.static_prop_source_ed_path}"
        lines.append(detail)
    if report.include_low_risk_behavior_prop_objects:
        detail = "low-risk behavior prop objects: included"
        if report.low_risk_behavior_prop_source_ed_path:
            detail += f", source={report.low_risk_behavior_prop_source_ed_path}"
        lines.append(detail)
    if report.include_wall_torch_objects:
        detail = "WallTorch objects: included"
        if report.wall_torch_source_ed_path:
            detail += f", source={report.wall_torch_source_ed_path}"
        lines.append(detail)
    if report.include_fire_objects:
        detail = "Fire objects: included"
        if report.fire_source_ed_path:
            detail += f", source={report.fire_source_ed_path}"
        lines.append(detail)
    if report.include_candle_prop_objects:
        detail = "CandleProp objects: included"
        if report.candle_prop_source_ed_path:
            detail += f", source={report.candle_prop_source_ed_path}"
        lines.append(detail)
    if report.include_brazier_objects:
        detail = "Brazier objects: included"
        if report.brazier_source_ed_path:
            detail += f", source={report.brazier_source_ed_path}"
        lines.append(detail)
    if report.include_treasure_chest_objects:
        detail = "TreasureChest objects: included"
        if report.treasure_chest_source_ed_path:
            detail += f", source={report.treasure_chest_source_ed_path}"
        lines.append(detail)
    if report.include_prop_damager_objects:
        detail = "PropDamager objects: included"
        if report.prop_damager_source_ed_path:
            detail += f", source={report.prop_damager_source_ed_path}"
        lines.append(detail)
    if report.include_destructable_prop_objects:
        detail = "DestructableProp objects: included"
        if report.destructable_prop_source_ed_path:
            detail += f", source={report.destructable_prop_source_ed_path}"
        lines.append(detail)
    if report.include_destructable_brush_objects:
        lines.append("DestructableBrush objects: included, source=DAT object section")
    if report.include_collision_helper_objects:
        if report.include_collision_helper_brushes:
            detail = "collision helper objects/Brushes: included"
        else:
            detail = "collision helper objects: included; Brushes: not included"
        if report.collision_helper_source_ed_path:
            detail += f", source={report.collision_helper_source_ed_path}"
        lines.append(detail)
    if report.include_trigger_helper_objects:
        if report.include_trigger_helper_brushes:
            detail = "trigger helper objects/Brushes: included"
        else:
            detail = "trigger helper objects: included; Brushes: not included"
        if report.trigger_helper_source_ed_path:
            detail += f", source={report.trigger_helper_source_ed_path}"
        lines.append(detail)
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
    if report.physics_shell_source_coverage is not None:
        coverage = report.physics_shell_source_coverage
        detail = (
            "PhysicsBSP shell source coverage: "
            f"status={coverage.status}, generated={coverage.generated_source_polygon_count}, "
            f"uncovered={coverage.uncovered_source_polygon_count}"
        )
        if coverage.loss_class_counts:
            detail += ", loss_classes=" + ";".join(
                f"{loss_class}:{count}"
                for loss_class, count in coverage.loss_class_counts
            )
        if report.physics_shell_source_coverage_manifest_path:
            detail += f", manifest={report.physics_shell_source_coverage_manifest_path}"
        lines.append(detail)
    if report.include_physics_shell_patch:
        lines.append(
            "PhysicsBSP shell packing: "
            f"mode={report.physics_shell_packing_mode}, "
            f"source={report.physics_shell_packing_source_polygon_count}, "
            f"brushes={report.physics_shell_packing_generated_brush_count}, "
            f"faces={report.physics_shell_packing_generated_face_count}"
        )
        if report.physics_shell_packing_role_weights:
            lines.append(
                "PhysicsBSP shell role weights: "
                + ", ".join(
                    f"{role}={weight:g}"
                    for role, weight in report.physics_shell_packing_role_weights
                )
            )
        if report.physics_shell_packing_playable_importance_weight > 0.0:
            lines.append(
                "PhysicsBSP shell playable-importance weight: "
                f"{report.physics_shell_packing_playable_importance_weight:g}"
            )
        if report.physics_shell_stair_assembly_indices:
            lines.append(
                "PhysicsBSP stair assembly reservation: "
                "requested=" + ",".join(
                    str(index) for index in report.physics_shell_stair_assembly_indices
                )
                + "; selected=" + ",".join(
                    str(index)
                    for index in report.physics_shell_selected_stair_assembly_indices
                )
                + "; rejected=" + ",".join(
                    str(index)
                    for index in report.physics_shell_rejected_stair_assembly_indices
                )
            )
        if report.physics_shell_protected_void_count:
            lines.append(
                "PhysicsBSP shell protected voids: "
                f"{report.physics_shell_protected_void_count}; roles="
                + ", ".join(report.physics_shell_protected_roles)
            )
        comparison = report.physics_shell_packing_comparison
        if comparison is not None:
            lines.append(
                "PhysicsBSP packing comparison: "
                f"preferred={comparison.preferred_validation_mode}, "
                f"value_delta={comparison.weighted_value_delta:g}, "
                f"area_delta={comparison.recovered_source_area_delta:g}, "
                f"brush_delta={comparison.generated_brush_delta:+d}, "
                f"face_delta={comparison.generated_face_delta:+d}, "
                f"protected_sets_match={comparison.protected_sets_match}"
            )
    if report.preflight_generated_brush_count or report.preflight_generated_polygon_count:
        lines.append(
            "preflight normalized base cost (before shell patch): "
            f"brushes={report.preflight_generated_brush_count}, "
            f"polygons={report.preflight_generated_polygon_count}, "
            f"extra_brushes={report.preflight_extra_brush_count}, "
            f"extra_polygons={report.preflight_extra_polygon_count}"
        )
        if report.preflight_sky_marker_brush_count:
            lines.append(
                "preflight SkyMarker cost: "
                f"brushes={report.preflight_sky_marker_brush_count}, "
                f"polygons={report.preflight_sky_marker_polygon_count}, "
                f"points={report.preflight_sky_marker_point_count}"
            )
    for item in report.models:
        lines.append(
            f"- {item.name}: points={item.point_count}, polygons={item.polygon_count}, "
            f"textures={item.texture_count}, bounds={_vec3_text(item.bounds_min)}.."
            f"{_vec3_text(item.bounds_max)}, center={_vec3_text(item.center)}"
        )
        for note in item.notes:
            lines.append(f"  note: {note}")
    for stage, elapsed in report.stage_timings_seconds:
        lines.append(f"timing {stage}: {elapsed:.6f}s")
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
    if report.helper_leakage is not None:
        lines.append(
            "helper leakage: "
            f"status={report.helper_leakage.status}, "
            f"compiled_helpers={report.helper_leakage.compiled_total_helper_polygon_count}, "
            f"reference_helpers={report.helper_leakage.reference_total_helper_polygon_count}, "
            f"VisBSP={report.helper_leakage.compiled_visibility_helper_polygon_count}/"
            f"{report.helper_leakage.reference_visibility_helper_polygon_count}, "
            f"Terrain*={report.helper_leakage.compiled_terrain_helper_polygon_count}/"
            f"{report.helper_leakage.reference_terrain_helper_polygon_count}"
        )
        for comparison in report.helper_leakage.role_comparisons:
            lines.append(
                f"  helper role {comparison.role}: {comparison.status}, "
                f"compiled={comparison.compiled_total}, reference={comparison.reference_total}"
            )
    for stage, elapsed in report.stage_timings_seconds:
        lines.append(f"timing {stage}: {elapsed:.6f}s")
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


def format_anskramkeep_physics_shell_retest_report(
    report: AnskramkeepPhysicsShellRetestReport,
) -> str:
    lines = [
        "ANSKRAMKEEP PhysicsBSP shell retest",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
        f"generated ED: {report.generated_ed_path or 'none'}",
    ]
    if report.reference_processor_log_path:
        lines.append(f"reference Processor log: {report.reference_processor_log_path}")
    if report.current_processor_log_path:
        lines.append(f"current Processor log: {report.current_processor_log_path}")
    if report.acceptance is not None:
        lines.append(
            "generated candidate: "
            f"status={report.acceptance.status}, models={report.acceptance.model_count}, "
            f"points={report.acceptance.point_count}, polygons={report.acceptance.polygon_count}, "
            f"objects={report.acceptance.object_count}"
        )
    for metric in report.comparisons:
        detail = (
            f"comparison: {metric.metric} status={metric.status}, "
            f"previous={metric.previous or 'unknown'}, current={metric.current or 'unknown'}"
        )
        if metric.delta:
            detail += f", delta={metric.delta}"
        lines.append(detail)
        for note in metric.notes:
            lines.append(f"  note: {note}")
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


def build_physics_shell_subset_plan_manifest(
    report: PhysicsShellSubsetPlan,
) -> Dict[str, object]:
    """Return a JSON-serializable manifest for a PhysicsBSP subset plan."""
    return {
        "kind": "mm9_physics_shell_subset_plan",
        "schema_version": 2,
        "status": report.status,
        "source_dat_path": report.source_dat_path,
        "physics_model_name": report.physics_model_name,
        "work_dir": report.work_dir,
        "batch_size": report.batch_size,
        "generated_face_budget": report.generated_face_budget,
        "source_polygon_count": report.source_polygon_count,
        "valid_candidate_count": report.valid_candidate_count,
        "role_counts": {role: count for role, count in report.role_counts},
        "processor_log_path": report.processor_log_path,
        "processor_log_status": report.processor_log_status,
        "processor_problem_brush_count": report.processor_problem_brush_count,
        "processor_warning_count": report.processor_warning_count,
        "entries": [
            {
                "role": entry.role,
                "batch_index": entry.batch_index,
                "polygon_indices": list(entry.polygon_indices),
                "generated_face_count": entry.generated_face_count,
                "suggested_output_filename": entry.suggested_output_filename,
                "processor_log_path": entry.processor_log_path,
                "processor_log_status": entry.processor_log_status,
                "processor_problem_brush_count": entry.processor_problem_brush_count,
                "processor_warning_count": entry.processor_warning_count,
                "validation_status": entry.validation_status,
            }
            for entry in report.entries
        ],
        "blockers": list(report.blockers),
        "cautions": list(report.cautions),
        "notes": list(report.notes),
    }


def write_physics_shell_subset_plan_manifest(
    report: PhysicsShellSubsetPlan,
    manifest_path: str,
) -> str:
    """Write a subset-plan manifest and return its absolute path."""
    absolute = os.path.abspath(manifest_path)
    os.makedirs(os.path.dirname(absolute) or ".", exist_ok=True)
    with open(absolute, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(build_physics_shell_subset_plan_manifest(report), handle, indent=2, sort_keys=True)
        handle.write("\n")
    return absolute


def format_physics_shell_subset_plan(report: PhysicsShellSubsetPlan) -> str:
    """Format a human-readable role/index subset plan."""
    lines = [
        "DAT PhysicsBSP shell subset plan",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
        f"physics model: {report.physics_model_name}",
        f"source polygons: {report.source_polygon_count}, valid candidates: {report.valid_candidate_count}",
        f"batch size: {report.batch_size}, generated-face budget: {report.generated_face_budget}, subsets: {len(report.entries)}",
        "role counts: " + ", ".join(f"{role}={count}" for role, count in report.role_counts),
    ]
    if report.processor_log_path:
        lines.append(
            "processor log: "
            f"status={report.processor_log_status}, path={report.processor_log_path}, "
            f"problem_brushes={report.processor_problem_brush_count}, "
            f"warnings={report.processor_warning_count}"
        )
    for entry in report.entries:
        lines.append(
            f"subset: role={entry.role}, batch={entry.batch_index}, "
            f"source_indices={len(entry.polygon_indices)}, generated_faces={entry.generated_face_count}, "
            f"validation={entry.validation_status}, "
            f"problem_brushes={entry.processor_problem_brush_count}, "
            f"warnings={entry.processor_warning_count}, output={entry.suggested_output_filename}"
        )
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_airail_reconstruction_report(report: AirailReconstructionReport) -> str:
    lines = [
        "DAT AIRail reconstruction report",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.source_ed_path:
        lines.append(f"source ED oracle: {report.source_ed_path}")
    lines.append(
        "summary: "
        f"aiRail_helpers={report.source_helper_model_count}, "
        f"helper_polygons={report.source_helper_polygon_count}, "
        f"source_AIRail={report.source_airail_object_count}, "
        f"rail_brushes={report.source_rail_brush_count}, "
        f"matched_generated_objects={report.generated_object_count}, "
        f"skipped={report.skipped_candidate_count}, ambiguous={report.ambiguous_candidate_count}"
    )
    for candidate in report.candidates[:16]:
        detail = (
            f"candidate: {candidate.source_model_name} status={candidate.status}, "
            f"polygons={candidate.polygon_count}, center={_vec3_text(candidate.center)}"
        )
        if candidate.nearest_airail_name:
            detail += (
                f", nearest_AIRail={candidate.nearest_airail_name}"
                f"@{candidate.nearest_airail_distance:.2f}"
            )
        if candidate.nearest_rail_brush_name:
            detail += (
                f", nearest_rail_brush={candidate.nearest_rail_brush_name}"
                f"@{candidate.nearest_rail_brush_distance:.2f}"
            )
        lines.append(detail)
    if len(report.candidates) > 16:
        lines.append(f"candidate: ... {len(report.candidates) - 16} more")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_sky_helper_reconstruction_report(report: SkyHelperReconstructionReport) -> str:
    lines = [
        "DAT sky helper reconstruction report",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.source_ed_path:
        lines.append(f"source ED oracle: {report.source_ed_path}")
    class_counts: Dict[str, int] = {}
    for item in report.source_sky_objects:
        class_counts[item.class_name] = class_counts.get(item.class_name, 0) + 1
    lines.append(
        "summary: "
        f"sky_helper_models={report.source_helper_model_count}, "
        f"sky_helper_polygons={report.source_helper_polygon_count}, "
        f"pure_helper_models={report.pure_helper_model_count}, "
        f"source_sky_objects={report.source_sky_object_count}, "
        f"generated_sky_objects={report.generated_object_count}, "
        f"sky_marker_brushes={report.source_sky_marker_brush_count}, "
        f"sky_marker_faces={report.source_sky_marker_face_count}, "
        f"classes={_class_counts_text(class_counts)}"
    )
    for item in report.source_sky_objects:
        lines.append(
            f"sky object: {item.name}/{item.class_name}, "
            f"pos={_vec3_text(item.pos)}, properties={item.property_count}"
        )
    for candidate in report.candidates[:16]:
        lines.append(
            f"candidate: {candidate.source_model_name} status={candidate.status}, "
            f"pure={candidate.pure_helper_model}, "
            f"roles={','.join(f'{role}:{count}' for role, count in sorted(candidate.helper_roles.items()))}, "
            f"center={_vec3_text(candidate.center)}"
        )
        for note in candidate.notes:
            lines.append(f"  note: {note}")
    if len(report.candidates) > 16:
        lines.append(f"candidate: ... {len(report.candidates) - 16} more")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_sky_marker_compiled_residue_report(report: SkyMarkerCompiledResidueReport) -> str:
    lines = [
        "DAT SkyMarker compiled residue report",
        f"status: {report.status}",
    ]
    if report.source_ed_path:
        lines.append(f"source ED oracle: {report.source_ed_path}")
    if report.compiled_dat_path:
        lines.append(f"compiled DAT reference: {report.compiled_dat_path}")
    if report.generated_compiled_dat_path:
        lines.append(f"generated compiled DAT: {report.generated_compiled_dat_path}")
    ratio_text = (
        "n/a"
        if report.source_to_compiled_ratio is None
        else f"{report.source_to_compiled_ratio:.4f}"
    )
    lines.append(
        "summary: "
        f"source_brushes={report.source_sky_marker_brush_count}, "
        f"source_faces={report.source_sky_marker_face_count}, "
        f"compiled_sky={report.compiled_sky_visibility_polygon_count}, "
        f"PhysicsBSP={report.compiled_physics_sky_visibility_polygon_count}, "
        f"VisBSP={report.compiled_visibility_sky_visibility_polygon_count}, "
        f"Terrain*={report.compiled_terrain_sky_visibility_polygon_count}, "
        f"world_models={report.compiled_world_model_sky_visibility_polygon_count}, "
        f"physics/source_ratio={ratio_text}"
    )
    if report.compiled_residue_matches:
        lines.append(
            "correlation: "
            f"matched_residues={report.compiled_residue_match_count}, "
            f"unmatched_residues={report.compiled_residue_unmatched_count}, "
            f"matched_source_faces={report.matched_source_sky_marker_face_count}, "
            f"matched_source_brushes={report.matched_source_sky_marker_brush_count}, "
            f"max_plane_delta={_optional_float_text(report.max_match_plane_distance)}, "
            f"min_normal_dot={_optional_float_text(report.min_match_normal_dot)}, "
            f"max_center_delta={_optional_float_text(report.max_match_center_distance, digits=2)}"
        )
    for summary in (
        report.source_face_matched_summary,
        report.source_face_unmatched_summary,
    ):
        if summary is None or summary.source_face_count <= 0:
            continue
        lines.append(
            f"source face cohort: {summary.cohort}, "
            f"faces={summary.source_face_count}, "
            f"brushes={summary.source_brush_count}, "
            f"orientations={_class_counts_text(summary.orientation_counts)}, "
            f"texture_flags={_class_counts_text(summary.texture_flag_counts)}, "
            f"surface_flags={_class_counts_text(summary.surface_flag_counts)}, "
            f"nearest_world=min:{_optional_float_text(summary.nearest_world_geometry_distance_min, digits=2)} "
            f"median:{_optional_float_text(summary.nearest_world_geometry_distance_median, digits=2)} "
            f"avg:{_optional_float_text(summary.nearest_world_geometry_distance_average, digits=2)} "
            f"max:{_optional_float_text(summary.nearest_world_geometry_distance_max, digits=2)}"
        )
        lines.append(
            f"source face cohort flags: {summary.cohort}, "
            f"flag_sets={_class_counts_text(summary.brush_flag_set_counts)}"
        )
    for item in report.residue_rule_candidates[:12]:
        lines.append(
            f"rule: {item.rule_name}, "
            f"selected={item.selected_source_face_count}, "
            f"matched={item.matched_source_face_count}, "
            f"extra={item.unmatched_source_face_count}, "
            f"missed={item.missed_matched_source_face_count}, "
            f"precision={_optional_float_text(item.precision)}, "
            f"recall={_optional_float_text(item.recall)}, "
            f"status={item.status}"
        )
    if len(report.residue_rule_candidates) > 12:
        lines.append(f"rule: ... {len(report.residue_rule_candidates) - 12} more")
    if report.generated_compiled_dat_path:
        lines.append(
            "generated summary: "
            f"compiled_sky={report.generated_sky_visibility_polygon_count}, "
            f"PhysicsBSP={report.generated_physics_sky_visibility_polygon_count}, "
            f"VisBSP={report.generated_visibility_sky_visibility_polygon_count}, "
            f"Terrain*={report.generated_terrain_sky_visibility_polygon_count}, "
            f"world_models={report.generated_world_model_sky_visibility_polygon_count}"
        )
    if report.source_brush_flag_counts:
        lines.append(
            "source brush flags: "
            + ", ".join(
                f"{name}={count}"
                for name, count in sorted(report.source_brush_flag_counts.items())
            )
        )
    if report.matched_source_brush_flag_counts:
        lines.append(
            "matched source brush flags: "
            + ", ".join(
                f"{name}={count}"
                for name, count in sorted(report.matched_source_brush_flag_counts.items())
            )
        )
    for item in report.source_sky_marker_brushes[:16]:
        lines.append(
            f"source brush: {item.name}, faces={item.sky_face_count}, "
            f"center={_vec3_text(item.center)}"
        )
    if len(report.source_sky_marker_brushes) > 16:
        lines.append(f"source brush: ... {len(report.source_sky_marker_brushes) - 16} more")
    for item in report.compiled_residue_matches[:16]:
        flags = ",".join(item.source_brush_flags) if item.source_brush_flags else "none"
        lines.append(
            f"match: {item.compiled_model_name}#{item.compiled_polygon_index} -> "
            f"{item.source_brush_name or 'unmatched'}"
            f"[model={item.source_model_index}, face={item.source_face_index}], "
            f"status={item.status}, "
            f"plane={_optional_float_text(item.plane_distance)}, "
            f"normal_dot={_optional_float_text(item.normal_dot)}, "
            f"center={_optional_float_text(item.center_distance, digits=2)}, "
            f"flags={flags}"
        )
    if len(report.compiled_residue_matches) > 16:
        lines.append(f"match: ... {len(report.compiled_residue_matches) - 16} more")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_sky_marker_residue_compile_audit_report(report: SkyMarkerResidueCompileAuditReport) -> str:
    lines = [
        "DAT SkyMarker residue compile audit",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
        f"source ED oracle: {report.source_ed_path}",
        f"reference DAT: {report.reference_dat_path}",
    ]
    if report.work_dir:
        lines.append(f"work dir: {report.work_dir}")
    if report.generated_ed_path:
        lines.append(f"generated ED: {report.generated_ed_path}")
    if report.compiled_dat_path:
        lines.append(f"compiled DAT: {report.compiled_dat_path}")
    else:
        lines.append("compiled DAT: pending")
    if report.acceptance is not None:
        lines.append(
            "generated candidate: "
            f"status={report.acceptance.status}, "
            f"models={report.acceptance.model_count}, "
            f"polygons={report.acceptance.polygon_count}, "
            f"objects={report.acceptance.object_count}"
        )
    if report.residue_report is not None:
        lines.append(
            "residue correlation: "
            f"status={report.residue_report.status}, "
            f"matched_residues={report.residue_report.compiled_residue_match_count}, "
            f"matched_source_faces={report.residue_report.matched_source_sky_marker_face_count}, "
            f"source_faces={report.residue_report.source_sky_marker_face_count}"
        )
    if report.helper_leakage is not None:
        lines.append(
            "helper leakage: "
            f"status={report.helper_leakage.status}, "
            f"compiled_helpers={report.helper_leakage.compiled_total_helper_polygon_count}, "
            f"reference_helpers={report.helper_leakage.reference_total_helper_polygon_count}, "
            f"VisBSP={report.helper_leakage.compiled_visibility_helper_polygon_count}/"
            f"{report.helper_leakage.reference_visibility_helper_polygon_count}, "
            f"Terrain*={report.helper_leakage.compiled_terrain_helper_polygon_count}/"
            f"{report.helper_leakage.reference_terrain_helper_polygon_count}"
        )
        for comparison in report.helper_leakage.role_comparisons:
            lines.append(
                f"  helper role {comparison.role}: {comparison.status}, "
                f"compiled={comparison.compiled_total}, reference={comparison.reference_total}"
            )
    for log in report.processor_logs:
        lines.append(
            "processor log summary: "
            f"status={log.status}, input_polies={log.input_polygon_count}, "
            f"output_polies={log.output_polygon_count}, problem_brushes={log.problem_brush_count}"
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


def format_sound_helper_reconstruction_report(report: SoundHelperReconstructionReport) -> str:
    lines = [
        "DAT sound helper reconstruction report",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.source_ed_path:
        lines.append(f"source ED oracle: {report.source_ed_path}")
    class_counts: Dict[str, int] = {}
    for item in report.source_sound_objects:
        class_counts[item.class_name] = class_counts.get(item.class_name, 0) + 1
    lines.append(
        "summary: "
        f"sound_helper_models={report.source_helper_model_count}, "
        f"sound_helper_polygons={report.source_helper_polygon_count}, "
        f"pure_helper_models={report.pure_helper_model_count}, "
        f"source_sound_objects={report.source_sound_object_count}, "
        f"generated_sound_objects={report.generated_object_count}, "
        f"classes={_class_counts_text(class_counts)}"
    )
    for item in report.source_sound_objects[:16]:
        outer_radius = "" if item.outer_radius is None else f"{item.outer_radius:g}"
        lines.append(
            f"sound object: {item.name}/{item.class_name}, "
            f"pos={_vec3_text(item.pos)}, filename={item.filename}, "
            f"outer_radius={outer_radius}, properties={item.property_count}"
        )
    if len(report.source_sound_objects) > 16:
        lines.append(f"sound object: ... {len(report.source_sound_objects) - 16} more")
    for candidate in report.candidates[:16]:
        lines.append(
            f"candidate: {candidate.source_model_name} status={candidate.status}, "
            f"pure={candidate.pure_helper_model}, "
            f"roles={','.join(f'{role}:{count}' for role, count in sorted(candidate.helper_roles.items()))}, "
            f"center={_vec3_text(candidate.center)}"
        )
        for note in candidate.notes:
            lines.append(f"  note: {note}")
    if len(report.candidates) > 16:
        lines.append(f"candidate: ... {len(report.candidates) - 16} more")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_gameplay_trigger_reconstruction_report(report: GameplayTriggerReconstructionReport) -> str:
    lines = [
        "DAT gameplay trigger reconstruction report",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.source_ed_path:
        lines.append(f"source ED oracle: {report.source_ed_path}")
    lines.append(
        "summary: "
        f"source_trigger_objects={report.source_trigger_object_count}, "
        f"generated_trigger_objects={report.generated_object_count}, "
        f"target_references={report.target_reference_count}, "
        f"classes={_class_counts_text(report.class_counts)}"
    )
    if report.destination_worlds:
        lines.append("destination worlds: " + ", ".join(report.destination_worlds))
    if report.portal_names:
        lines.append("portal names: " + ", ".join(report.portal_names))
    for item in report.source_trigger_objects[:16]:
        detail = (
            f"trigger object: {item.name}/{item.class_name}, "
            f"pos={_vec3_text(item.pos)}, dims={_vec3_text(item.dims)}, "
            f"targets={item.target_count}, properties={item.property_count}"
        )
        if item.destination_world:
            detail += f", destination={item.destination_world}"
        if item.portal_name:
            detail += f", portal={item.portal_name}"
        lines.append(detail)
    if len(report.source_trigger_objects) > 16:
        lines.append(f"trigger object: ... {len(report.source_trigger_objects) - 16} more")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_static_prop_reconstruction_report(report: StaticPropReconstructionReport) -> str:
    lines = [
        "DAT static Prop reconstruction report",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.source_ed_path:
        lines.append(f"source ED oracle: {report.source_ed_path}")
    lines.append(
        "summary: "
        f"source_prop_objects={report.source_prop_object_count}, "
        f"generated_prop_objects={report.generated_object_count}, "
        f"unique_models={report.unique_model_count}, "
        f"unique_skins={report.unique_skin_count}, "
        f"solid={report.solid_count}, "
        f"move_to_floor={report.move_to_floor_count}"
    )
    if report.top_filenames:
        lines.append(
            "top filenames: "
            + ", ".join(f"{name}={count}" for name, count in report.top_filenames)
        )
    for item in report.source_prop_objects[:16]:
        scale = "" if item.scale is None else f"{item.scale:g}"
        lines.append(
            f"prop object: {item.name}/{item.class_name}, "
            f"pos={_vec3_text(item.pos)}, filename={item.filename}, "
            f"skin={item.skin}, scale={scale}, solid={item.solid}, "
            f"move_to_floor={item.move_to_floor}, properties={item.property_count}"
        )
    if len(report.source_prop_objects) > 16:
        lines.append(f"prop object: ... {len(report.source_prop_objects) - 16} more")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_behavior_prop_reconstruction_report(report: BehaviorPropReconstructionReport) -> str:
    lines = [
        "DAT behavior prop reconstruction report",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.source_ed_path:
        lines.append(f"source ED oracle: {report.source_ed_path}")
    lines.append(
        "summary: "
        f"source_behavior_props={report.source_behavior_prop_object_count}, "
        f"copy_candidates={report.copy_candidate_count}, "
        f"unique_models={report.unique_model_count}, "
        f"unique_skins={report.unique_skin_count}, "
        f"solid={report.solid_count}, "
        f"move_to_floor={report.move_to_floor_count}, "
        f"classes={_class_counts_text(report.class_counts)}, "
        f"roles={_class_counts_text(report.semantic_role_counts)}, "
        f"risk={_class_counts_text(report.risk_level_counts)}"
    )
    if report.top_filenames:
        lines.append(
            "top filenames: "
            + ", ".join(f"{name}={count}" for name, count in report.top_filenames)
        )
    for summary in report.class_summaries:
        lines.append(
            f"class summary: {summary.class_name}, objects={summary.object_count}, "
            f"unique_models={summary.unique_model_count}, solid={summary.solid_count}, "
            f"move_to_floor={summary.move_to_floor_count}, "
            f"roles={_class_counts_text(summary.semantic_role_counts)}, "
            f"risk={_class_counts_text(summary.risk_level_counts)}, "
            f"copy_pass={summary.copy_pass_key or 'none'}, "
            f"pass_status={summary.copy_pass_status}, "
            f"validation={summary.validation_status}, "
            f"samples={', '.join(summary.sample_names)}"
        )
    for item in report.source_behavior_prop_objects[:16]:
        scale = "" if item.scale is None else f"{item.scale:g}"
        detail = (
            f"behavior prop: {item.name}/{item.class_name}, "
            f"risk={item.risk_level}, roles={','.join(item.semantic_roles)}, "
            f"pos={_vec3_text(item.pos)}, filename={item.filename}, "
            f"skin={item.skin}, scale={scale}, solid={item.solid}, "
            f"move_to_floor={item.move_to_floor}"
        )
        if item.sound_file:
            detail += f", sound={item.sound_file}"
        if item.trigger_target:
            detail += f", trigger={item.trigger_target}"
        if item.damage_trigger_target:
            detail += f", damage_trigger={item.damage_trigger_target}"
        if item.hit_points is not None:
            detail += f", hit_points={item.hit_points:g}"
        lines.append(detail)
    if len(report.source_behavior_prop_objects) > 16:
        lines.append(f"behavior prop: ... {len(report.source_behavior_prop_objects) - 16} more")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_collision_helper_reconstruction_report(
    report: CollisionHelperReconstructionReport,
) -> str:
    lines = [
        "DAT collision helper reconstruction report",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.source_ed_path:
        lines.append(f"source ED oracle: {report.source_ed_path}")
    class_counts: Dict[str, int] = {}
    for candidate in report.candidates:
        key = candidate.matched_object_class_name or candidate.target_class_name or "unknown"
        class_counts[key] = class_counts.get(key, 0) + 1
    class_text = _class_counts_text(class_counts)
    lines.append(
        "summary: "
        f"collision_helpers={report.source_helper_model_count}, "
        f"helper_polygons={report.source_helper_polygon_count}, "
        f"source_objects={report.source_object_count}, "
        f"helper_brushes={report.source_helper_brush_count}, "
        f"matched_objects={report.matched_object_count}, "
        f"skipped={report.skipped_candidate_count}, classes={class_text}"
    )
    for candidate in report.candidates[:16]:
        detail = (
            f"candidate: {candidate.source_model_name} status={candidate.status}, "
            f"target={candidate.target_class_name or 'unknown'}, "
            f"roles={','.join(f'{role}:{count}' for role, count in sorted(candidate.helper_roles.items()))}, "
            f"center={_vec3_text(candidate.center)}"
        )
        if candidate.matched_object_name:
            detail += (
                f", source_object={candidate.matched_object_name}"
                f"/{candidate.matched_object_class_name}"
            )
            if candidate.matched_object_distance is not None:
                detail += f"@{candidate.matched_object_distance:.2f}"
        if candidate.nearest_helper_brush_name:
            detail += (
                f", nearest_helper_brush={candidate.nearest_helper_brush_name}"
                f"@{candidate.nearest_helper_brush_distance:.2f}"
            )
        lines.append(detail)
    if len(report.candidates) > 16:
        lines.append(f"candidate: ... {len(report.candidates) - 16} more")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_trigger_helper_reconstruction_report(
    report: TriggerHelperReconstructionReport,
) -> str:
    lines = [
        "DAT trigger helper reconstruction report",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
    ]
    if report.source_ed_path:
        lines.append(f"source ED oracle: {report.source_ed_path}")
    class_counts: Dict[str, int] = {}
    for candidate in report.candidates:
        key = candidate.matched_object_class_name or "PortalZone"
        class_counts[key] = class_counts.get(key, 0) + 1
    class_text = _class_counts_text(class_counts)
    lines.append(
        "summary: "
        f"trigger_helpers={report.source_helper_model_count}, "
        f"helper_polygons={report.source_helper_polygon_count}, "
        f"source_objects={report.source_object_count}, "
        f"helper_brushes={report.source_helper_brush_count}, "
        f"matched_objects={report.matched_object_count}, "
        f"skipped={report.skipped_candidate_count}, classes={class_text}"
    )
    for candidate in report.candidates[:16]:
        detail = (
            f"candidate: {candidate.source_model_name} status={candidate.status}, "
            f"roles={','.join(f'{role}:{count}' for role, count in sorted(candidate.helper_roles.items()))}, "
            f"center={_vec3_text(candidate.center)}"
        )
        if candidate.matched_object_name:
            detail += (
                f", source_object={candidate.matched_object_name}"
                f"/{candidate.matched_object_class_name}"
            )
            if candidate.matched_object_portal_name:
                detail += f", portal={candidate.matched_object_portal_name}"
            if candidate.matched_object_distance is not None:
                detail += f"@{candidate.matched_object_distance:.2f}"
        if candidate.nearest_helper_brush_name:
            detail += (
                f", nearest_helper_brush={candidate.nearest_helper_brush_name}"
                f"@{candidate.nearest_helper_brush_distance:.2f}"
            )
        lines.append(detail)
    if len(report.candidates) > 16:
        lines.append(f"candidate: ... {len(report.candidates) - 16} more")
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
    include_airail_semantics: bool = False,
    include_sky_semantics: bool = False,
    include_sound_semantics: bool = False,
    include_collision_semantics: bool = False,
    include_trigger_semantics: bool = False,
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
        "include_airail_semantics": bool(include_airail_semantics),
        "include_sky_semantics": bool(include_sky_semantics),
        "include_sound_semantics": bool(include_sound_semantics),
        "include_collision_semantics": bool(include_collision_semantics),
        "include_trigger_semantics": bool(include_trigger_semantics),
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
            include_airail_semantics=bool(include_airail_semantics),
            include_sky_semantics=bool(include_sky_semantics),
            include_sound_semantics=bool(include_sound_semantics),
            include_collision_semantics=bool(include_collision_semantics),
            include_trigger_semantics=bool(include_trigger_semantics),
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
            include_airail_semantics=bool(include_airail_semantics),
            include_sky_semantics=bool(include_sky_semantics),
            include_sound_semantics=bool(include_sound_semantics),
            include_collision_semantics=bool(include_collision_semantics),
            include_trigger_semantics=bool(include_trigger_semantics),
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
    helper_semantic_sources_by_role: Dict[str, Dict[str, int]] = {}
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
            include_airail_semantics=bool(include_airail_semantics),
            include_sky_semantics=bool(include_sky_semantics),
            include_sound_semantics=bool(include_sound_semantics),
            include_collision_semantics=bool(include_collision_semantics),
            include_trigger_semantics=bool(include_trigger_semantics),
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
        if model_report.status == "helper_semantic_source":
            _accumulate_helper_exclusion_roles(
                helper_semantic_sources_by_role,
                model_report.helper_roles,
            )

    selected_count = status_counts.get("selected", 0)
    terrain_support_count = status_counts.get("terrain_support_source", 0)
    physics_shell_count = status_counts.get("physics_shell_source", 0)
    helper_semantic_count = status_counts.get("helper_semantic_source", 0)
    excluded_count = (
        len(model_reports)
        - selected_count
        - terrain_support_count
        - physics_shell_count
        - helper_semantic_count
    )
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
    if include_airail_semantics:
        notes.append(
            "AIRail helper semantic source means aiRail helper geometry is reserved for object reconstruction instead of visible Brush output."
        )
    if include_sky_semantics:
        notes.append(
            "Sky helper semantic source means SkyMarker helper geometry is reserved for sky visibility reconstruction instead of visible Brush output."
        )
    if include_sound_semantics:
        notes.append(
            "Sound helper semantic source means SoundOnly helper geometry is reserved for AmbientSound/volume reconstruction instead of visible Brush output."
        )
    if include_collision_semantics:
        notes.append(
            "Collision helper semantic source means Invisible/Firethrough helper geometry is reserved for collision/helper object reconstruction instead of visible Brush output."
        )
    if include_trigger_semantics:
        notes.append(
            "Trigger helper semantic source means GreenScreen helper geometry is reserved for PortalZone reconstruction instead of visible Brush output."
        )
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
        include_airail_semantics=bool(include_airail_semantics),
        include_sky_semantics=bool(include_sky_semantics),
        include_sound_semantics=bool(include_sound_semantics),
        include_collision_semantics=bool(include_collision_semantics),
        include_trigger_semantics=bool(include_trigger_semantics),
        total_model_count=len(model_reports),
        selected_model_count=selected_count,
        terrain_support_source_count=terrain_support_count,
        physics_shell_source_count=physics_shell_count,
        helper_semantic_source_count=helper_semantic_count,
        excluded_model_count=excluded_count,
        total_point_count=total_points,
        total_polygon_count=total_polygons,
        selected_point_count=selected_points,
        selected_polygon_count=selected_polygons,
        status_counts=status_counts,
        helper_only_exclusions_by_role=_ordered_helper_exclusion_role_summary(
            helper_exclusions_by_role
        ),
        helper_semantic_sources_by_role=_ordered_helper_exclusion_role_summary(
            helper_semantic_sources_by_role
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
    include_airail_semantics: bool,
    include_sky_semantics: bool,
    include_sound_semantics: bool,
    include_collision_semantics: bool,
    include_trigger_semantics: bool,
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
        helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
        detail = ""
        if helper_roles:
            detail = ", ".join(f"{role}={count}" for role, count in sorted(helper_roles.items()))
        if (
            include_airail_semantics
            and int(helper_roles.get("aiRail", 0)) > 0
            and set(helper_roles.keys()) == {"aiRail"}
        ):
            status = "helper_semantic_source"
            if detail:
                reasons.append(
                    f"model uses only AIRail helper geometry ({detail}); "
                    "reserved for AIRail object reconstruction"
                )
            else:
                reasons.append("model uses only AIRail helper geometry")
        elif (
            include_sky_semantics
            and int(helper_roles.get("skyVisibility", 0)) > 0
            and set(helper_roles.keys()) == {"skyVisibility"}
        ):
            status = "helper_semantic_source"
            if detail:
                reasons.append(
                    f"model uses only sky helper geometry ({detail}); "
                    "reserved for sky visibility reconstruction"
                )
            else:
                reasons.append("model uses only sky helper geometry")
        elif (
            include_sound_semantics
            and int(helper_roles.get("sound", 0)) > 0
            and set(helper_roles.keys()) == {"sound"}
        ):
            status = "helper_semantic_source"
            if detail:
                reasons.append(
                    f"model uses only SoundOnly helper geometry ({detail}); "
                    "reserved for AmbientSound/volume reconstruction"
                )
            else:
                reasons.append("model uses only SoundOnly helper geometry")
        elif (
            include_collision_semantics
            and int(helper_roles.get("collision", 0)) > 0
            and set(helper_roles.keys()).issubset({"collision", "sprite"})
        ):
            status = "helper_semantic_source"
            if detail:
                reasons.append(
                    f"model uses only collision helper geometry ({detail}); "
                    "reserved for collision helper reconstruction"
                )
            else:
                reasons.append("model uses only collision helper geometry")
        elif (
            include_trigger_semantics
            and int(helper_roles.get("trigger", 0)) > 0
            and set(helper_roles.keys()) == {"trigger"}
        ):
            status = "helper_semantic_source"
            if detail:
                reasons.append(
                    f"model uses only trigger helper geometry ({detail}); "
                    "reserved for PortalZone reconstruction"
                )
            else:
                reasons.append("model uses only trigger helper geometry")
        else:
            status = "excluded_helper_texture"
            if detail:
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
            "physics_shell_source_coverage_manifest_path": acceptance_report.physics_shell_source_coverage_manifest_path,
            "terrain_cutout_coverage": _full_world_manifest_cutout_summary(
                acceptance_report.terrain_cutout_coverage
            ),
            "terrain_support_source_coverage": _full_world_manifest_source_coverage_summary(
                acceptance_report.terrain_support_source_coverage
            ),
            "physics_shell_source_coverage": _full_world_manifest_physics_shell_coverage_summary(
                acceptance_report.physics_shell_source_coverage
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
        "include_airail_semantics": report.include_airail_semantics,
        "include_sky_semantics": report.include_sky_semantics,
        "include_sound_semantics": report.include_sound_semantics,
        "include_collision_semantics": report.include_collision_semantics,
        "include_trigger_semantics": report.include_trigger_semantics,
        "summary": {
            "total_model_count": report.total_model_count,
            "selected_model_count": report.selected_model_count,
            "terrain_support_source_count": report.terrain_support_source_count,
            "physics_shell_source_count": report.physics_shell_source_count,
            "helper_semantic_source_count": report.helper_semantic_source_count,
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
            "helper_semantic_sources_by_role": {
                role: dict(values)
                for role, values in report.helper_semantic_sources_by_role.items()
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


def _physics_shell_packing_plan_manifest(
    plan: terrain_reconstruction.PhysicsShellPackingPlan,
) -> Dict[str, object]:
    return {
        "source_polygon_count": plan.source_polygon_count,
        "generated_brush_count": plan.generated_brush_count,
        "generated_face_count": plan.generated_face_count,
        "recovered_source_area": plan.recovered_source_area,
        "weighted_value": plan.weighted_value,
        "role_counts": dict(plan.role_counts),
        "protected_polygon_indices": list(plan.protected_polygon_indices),
        "selected_source_polygon_indices": [
            int(candidate.polygon_index)
            for group in plan.groups
            for candidate in group.candidates
        ],
    }


def _physics_shell_packing_comparison_manifest(
    comparison: Optional[terrain_reconstruction.PhysicsShellPackingComparison],
) -> Optional[Dict[str, object]]:
    if comparison is None:
        return None
    return {
        "candidate_count": comparison.candidate_count,
        "source_polygon_limit": comparison.source_polygon_limit,
        "generated_face_budget": comparison.generated_face_budget,
        "preferred_validation_mode": comparison.preferred_validation_mode,
        "weighted_value_delta": comparison.weighted_value_delta,
        "recovered_source_area_delta": comparison.recovered_source_area_delta,
        "generated_brush_delta": comparison.generated_brush_delta,
        "generated_face_delta": comparison.generated_face_delta,
        "protected_sets_match": comparison.protected_sets_match,
        "balanced": _physics_shell_packing_plan_manifest(comparison.balanced),
        "cost_aware": _physics_shell_packing_plan_manifest(comparison.cost_aware),
        "notes": list(comparison.notes),
    }


def build_physics_shell_packing_experiment_manifest(
    report: PhysicsShellPackingExperimentReport,
) -> Dict[str, object]:
    def run_summary(
        mode: str,
        acceptance: Optional[FullWorldSkeletonAcceptanceReport],
        acceptance_manifest_path: str,
    ) -> Dict[str, object]:
        generated_ed = acceptance.generated_ed_path if acceptance is not None else ""
        output_base = os.path.splitext(generated_ed)[0] if generated_ed else ""
        return {
            "mode": mode,
            "status": acceptance.status if acceptance is not None else "not_run",
            "generated_ed_path": generated_ed,
            "acceptance_manifest_path": acceptance_manifest_path,
            "suggested_compiled_dat_path": f"{output_base}.dat" if output_base else "",
            "suggested_processor_log_path": f"{output_base}.log" if output_base else "",
            "generated_model_count": acceptance.model_count if acceptance is not None else 0,
            "generated_polygon_count": acceptance.polygon_count if acceptance is not None else 0,
            "shell_source_polygon_count": (
                acceptance.physics_shell_packing_source_polygon_count
                if acceptance is not None
                else 0
            ),
            "shell_generated_brush_count": (
                acceptance.physics_shell_packing_generated_brush_count
                if acceptance is not None
                else 0
            ),
            "shell_generated_face_count": (
                acceptance.physics_shell_packing_generated_face_count
                if acceptance is not None
                else 0
            ),
            "processor_status": "not_run",
            "manual_game_status": "not_run",
        }

    return {
        "kind": "mm9_physics_shell_packing_experiment",
        "status": report.status,
        "source_dat_path": report.source_dat_path,
        "work_dir": report.work_dir,
        "output_stem": report.output_stem,
        "physics_shell_model_name": report.physics_shell_model_name,
        "physics_shell_name_prefix": report.physics_shell_name_prefix,
        "experiment_manifest_path": report.experiment_manifest_path,
        "runs": {
            "balanced": run_summary(
                "balanced",
                report.balanced,
                report.balanced_manifest_path,
            ),
            "cost_aware": run_summary(
                "cost_aware",
                report.cost_aware,
                report.cost_aware_manifest_path,
            ),
        },
        "comparison": _physics_shell_packing_comparison_manifest(report.comparison),
        "blockers": list(report.blockers),
        "notes": list(report.notes),
    }


def write_physics_shell_packing_experiment_manifest(
    report: PhysicsShellPackingExperimentReport,
    manifest_path: str,
) -> str:
    absolute = os.path.abspath(manifest_path)
    os.makedirs(os.path.dirname(absolute) or ".", exist_ok=True)
    with open(absolute, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            build_physics_shell_packing_experiment_manifest(report),
            handle,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")
    return absolute


def format_physics_shell_packing_experiment(
    report: PhysicsShellPackingExperimentReport,
) -> str:
    lines = [
        "PhysicsBSP shell packing experiment",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
        f"work dir: {report.work_dir}",
    ]
    for mode, acceptance in (
        ("balanced", report.balanced),
        ("cost_aware", report.cost_aware),
    ):
        if acceptance is None:
            lines.append(f"{mode}: not run")
            continue
        lines.append(
            f"{mode}: status={acceptance.status}, ED={acceptance.generated_ed_path}, "
            f"source={acceptance.physics_shell_packing_source_polygon_count}, "
            f"brushes={acceptance.physics_shell_packing_generated_brush_count}, "
            f"faces={acceptance.physics_shell_packing_generated_face_count}"
        )
    if report.comparison is not None:
        lines.append(
            "comparison: "
            f"preferred={report.comparison.preferred_validation_mode}, "
            f"value_delta={report.comparison.weighted_value_delta:g}, "
            f"area_delta={report.comparison.recovered_source_area_delta:g}, "
            f"protected_sets_match={report.comparison.protected_sets_match}"
        )
    lines.append(f"manifest: {report.experiment_manifest_path}")
    lines.extend(f"blocker: {item}" for item in report.blockers)
    lines.extend(f"note: {item}" for item in report.notes)
    return "\n".join(lines)


def build_physics_shell_packing_experiment_validation_manifest(
    report: PhysicsShellPackingExperimentValidationReport,
) -> Dict[str, object]:
    def validation_summary(
        validation: Optional[FullWorldSkeletonCompiledValidationReport],
        *,
        coverage: Optional[PhysicsShellSourceCoverageReport],
        dat_path: str,
        log_path: str,
        problem_count: int,
        warning_count: int,
        physics_polygon_count: int,
        retained_source_polygon_count: int,
        lost_source_polygon_count: int,
        retained_source_area: float,
    ) -> Dict[str, object]:
        manual = validation.manual_validation if validation is not None else None
        return {
            "status": validation.status if validation is not None else "not_run",
            "compiled_dat_path": dat_path,
            "processor_log_path": log_path,
            "problem_brush_count": problem_count,
            "warning_count": warning_count,
            "physics_polygon_count": physics_polygon_count,
            "source_coverage_status": (
                coverage.status if coverage is not None else "not_run"
            ),
            "retained_source_polygon_count": retained_source_polygon_count,
            "lost_source_polygon_count": lost_source_polygon_count,
            "retained_source_area": retained_source_area,
            "physics_floor_drop": (
                validation.physics_floor_drop if validation is not None else None
            ),
            "manual_validation": {
                "status": manual.status if manual is not None else "not_validated",
                "tested_at": manual.tested_at if manual is not None else "",
                "fresh_load": manual.fresh_load if manual is not None else None,
                "visuals_ok": manual.visuals_ok if manual is not None else None,
                "collision_ok": manual.collision_ok if manual is not None else None,
                "notes": list(manual.notes) if manual is not None else [],
            },
            "blockers": list(validation.blockers) if validation is not None else [],
            "cautions": list(validation.cautions) if validation is not None else [],
        }

    return {
        "kind": "mm9_physics_shell_packing_experiment_validation",
        "status": report.status,
        "experiment_manifest_path": report.experiment_manifest_path,
        "validation_manifest_path": report.validation_manifest_path,
        "recommended_mode": report.recommended_mode,
        "manual_comparison_complete": report.manual_comparison_complete,
        "runs": {
            "balanced": validation_summary(
                report.balanced,
                coverage=report.balanced_source_coverage,
                dat_path=report.balanced_compiled_dat_path,
                log_path=report.balanced_processor_log_path,
                problem_count=report.balanced_problem_brush_count,
                warning_count=report.balanced_warning_count,
                physics_polygon_count=report.balanced_physics_polygon_count,
                retained_source_polygon_count=(
                    report.balanced_retained_source_polygon_count
                ),
                lost_source_polygon_count=report.balanced_lost_source_polygon_count,
                retained_source_area=report.balanced_retained_source_area,
            ),
            "cost_aware": validation_summary(
                report.cost_aware,
                coverage=report.cost_aware_source_coverage,
                dat_path=report.cost_aware_compiled_dat_path,
                log_path=report.cost_aware_processor_log_path,
                problem_count=report.cost_aware_problem_brush_count,
                warning_count=report.cost_aware_warning_count,
                physics_polygon_count=report.cost_aware_physics_polygon_count,
                retained_source_polygon_count=(
                    report.cost_aware_retained_source_polygon_count
                ),
                lost_source_polygon_count=report.cost_aware_lost_source_polygon_count,
                retained_source_area=report.cost_aware_retained_source_area,
            ),
        },
        "deltas": {
            "problem_brush_count": (
                report.cost_aware_problem_brush_count
                - report.balanced_problem_brush_count
            ),
            "warning_count": (
                report.cost_aware_warning_count - report.balanced_warning_count
            ),
            "physics_polygon_count": (
                report.cost_aware_physics_polygon_count
                - report.balanced_physics_polygon_count
            ),
            "retained_source_polygon_count": (
                report.cost_aware_retained_source_polygon_count
                - report.balanced_retained_source_polygon_count
            ),
            "lost_source_polygon_count": (
                report.cost_aware_lost_source_polygon_count
                - report.balanced_lost_source_polygon_count
            ),
            "retained_source_area": (
                report.cost_aware_retained_source_area
                - report.balanced_retained_source_area
            ),
        },
        "blockers": list(report.blockers),
        "notes": list(report.notes),
    }


def write_physics_shell_packing_experiment_validation_manifest(
    report: PhysicsShellPackingExperimentValidationReport,
) -> str:
    payload = build_physics_shell_packing_experiment_validation_manifest(report)
    absolute = os.path.abspath(report.validation_manifest_path)
    os.makedirs(os.path.dirname(absolute) or ".", exist_ok=True)
    with open(absolute, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    experiment_manifest = os.path.abspath(report.experiment_manifest_path)
    if experiment_manifest:
        try:
            with open(experiment_manifest, "r", encoding="utf-8") as handle:
                experiment_payload = json.load(handle)
        except (OSError, ValueError):
            experiment_payload = {}
        experiment_payload["validation_manifest_path"] = absolute
        experiment_payload["validation"] = payload
        os.makedirs(os.path.dirname(experiment_manifest) or ".", exist_ok=True)
        with open(experiment_manifest, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(experiment_payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    return absolute


def format_physics_shell_packing_experiment_validation(
    report: PhysicsShellPackingExperimentValidationReport,
) -> str:
    lines = [
        "PhysicsBSP shell packing experiment validation",
        f"status: {report.status}",
        f"recommended mode: {report.recommended_mode}",
        f"manual comparison complete: {report.manual_comparison_complete}",
        (
            "balanced: "
            f"problems={report.balanced_problem_brush_count}, "
            f"warnings={report.balanced_warning_count}, "
            f"PhysicsBSP polygons={report.balanced_physics_polygon_count}, "
            f"retained source={report.balanced_retained_source_polygon_count}, "
            f"retained area={report.balanced_retained_source_area:g}"
        ),
        (
            "cost-aware: "
            f"problems={report.cost_aware_problem_brush_count}, "
            f"warnings={report.cost_aware_warning_count}, "
            f"PhysicsBSP polygons={report.cost_aware_physics_polygon_count}, "
            f"retained source={report.cost_aware_retained_source_polygon_count}, "
            f"retained area={report.cost_aware_retained_source_area:g}"
        ),
        f"manifest: {report.validation_manifest_path}",
    ]
    lines.extend(f"blocker: {item}" for item in report.blockers)
    lines.extend(f"note: {item}" for item in report.notes)
    return "\n".join(lines)


def build_full_world_skeleton_acceptance_manifest(
    report: FullWorldSkeletonAcceptanceReport,
    *,
    original_source: str = "",
    staged_source_dat_path: str = "",
    text_report_path: str = "",
    selection_report_path: str = "",
    behavior_prop_report_path: str = "",
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
            "behavior_prop_report_path": behavior_prop_report_path,
            "work_dir": report.work_dir,
            "suggested_world_install_path": report.world_install_path,
            "dedit_saved_ed_path": dedit_saved_ed_path,
            "compiled_dat_path": compiled_dat_path,
            "processor_log_paths": list(processor_log_paths),
            "terrain_cutout_coverage_manifest_path": report.terrain_cutout_coverage_manifest_path,
            "terrain_support_source_coverage_manifest_path": report.terrain_support_source_coverage_manifest_path,
            "physics_shell_source_coverage_manifest_path": report.physics_shell_source_coverage_manifest_path,
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
            "physics_shell_packing_mode": report.physics_shell_packing_mode,
            "physics_shell_packing_source_polygon_count": report.physics_shell_packing_source_polygon_count,
            "physics_shell_packing_generated_brush_count": report.physics_shell_packing_generated_brush_count,
            "physics_shell_packing_generated_face_count": report.physics_shell_packing_generated_face_count,
            "physics_shell_packing_weighted_value": report.physics_shell_packing_weighted_value,
            "physics_shell_packing_role_weights": {
                role: weight for role, weight in report.physics_shell_packing_role_weights
            },
            "physics_shell_packing_playable_importance_weight": report.physics_shell_packing_playable_importance_weight,
            "physics_shell_stair_assembly_indices": list(
                report.physics_shell_stair_assembly_indices
            ),
            "physics_shell_selected_stair_assembly_indices": list(
                report.physics_shell_selected_stair_assembly_indices
            ),
            "physics_shell_rejected_stair_assembly_indices": list(
                report.physics_shell_rejected_stair_assembly_indices
            ),
            "physics_shell_packing_comparison": _physics_shell_packing_comparison_manifest(
                report.physics_shell_packing_comparison
            ),
            "physics_shell_protected_void_count": report.physics_shell_protected_void_count,
            "physics_shell_protected_roles": list(report.physics_shell_protected_roles),
            "preflight_generated_brush_count": report.preflight_generated_brush_count,
            "preflight_generated_polygon_count": report.preflight_generated_polygon_count,
            "preflight_extra_brush_count": report.preflight_extra_brush_count,
            "preflight_extra_polygon_count": report.preflight_extra_polygon_count,
            "preflight_sky_marker_brush_count": report.preflight_sky_marker_brush_count,
            "preflight_sky_marker_polygon_count": report.preflight_sky_marker_polygon_count,
            "preflight_sky_marker_point_count": report.preflight_sky_marker_point_count,
            "physics_shell_focus_points": [list(point) for point in report.physics_shell_focus_points],
            "physics_shell_focus_radius": report.physics_shell_focus_radius,
            "physics_shell_focus_budget": report.physics_shell_focus_budget,
            "physics_shell_focus_seed_radius": report.physics_shell_focus_seed_radius,
            "include_door_objects": report.include_door_objects,
            "door_source_ed_path": report.door_source_ed_path,
            "door_behavior_context": report.door_behavior_context,
            "include_airail_objects": report.include_airail_objects,
            "airail_source_ed_path": report.airail_source_ed_path,
            "include_sky_objects": report.include_sky_objects,
            "sky_source_ed_path": report.sky_source_ed_path,
            "include_sky_marker_brushes": report.include_sky_marker_brushes,
            "include_sky_marker_residue_brushes": report.include_sky_marker_residue_brushes,
            "sky_marker_residue_reference_dat_path": report.sky_marker_residue_reference_dat_path,
            "include_sound_objects": report.include_sound_objects,
            "sound_source_ed_path": report.sound_source_ed_path,
            "include_gameplay_trigger_objects": report.include_gameplay_trigger_objects,
            "gameplay_trigger_source_ed_path": report.gameplay_trigger_source_ed_path,
            "include_static_prop_objects": report.include_static_prop_objects,
            "static_prop_source_ed_path": report.static_prop_source_ed_path,
            "include_low_risk_behavior_prop_objects": report.include_low_risk_behavior_prop_objects,
            "low_risk_behavior_prop_source_ed_path": report.low_risk_behavior_prop_source_ed_path,
            "include_wall_torch_objects": report.include_wall_torch_objects,
            "wall_torch_source_ed_path": report.wall_torch_source_ed_path,
            "include_fire_objects": report.include_fire_objects,
            "fire_source_ed_path": report.fire_source_ed_path,
            "include_candle_prop_objects": report.include_candle_prop_objects,
            "candle_prop_source_ed_path": report.candle_prop_source_ed_path,
            "include_brazier_objects": report.include_brazier_objects,
            "brazier_source_ed_path": report.brazier_source_ed_path,
            "include_treasure_chest_objects": report.include_treasure_chest_objects,
            "treasure_chest_source_ed_path": report.treasure_chest_source_ed_path,
            "include_prop_damager_objects": report.include_prop_damager_objects,
            "prop_damager_source_ed_path": report.prop_damager_source_ed_path,
            "include_destructable_prop_objects": report.include_destructable_prop_objects,
            "destructable_prop_source_ed_path": report.destructable_prop_source_ed_path,
            "include_destructable_brush_objects": report.include_destructable_brush_objects,
            "include_collision_helper_objects": report.include_collision_helper_objects,
            "include_collision_helper_brushes": report.include_collision_helper_brushes,
            "collision_helper_source_ed_path": report.collision_helper_source_ed_path,
            "include_trigger_helper_objects": report.include_trigger_helper_objects,
            "include_trigger_helper_brushes": report.include_trigger_helper_brushes,
            "trigger_helper_source_ed_path": report.trigger_helper_source_ed_path,
            "max_processor_brushes": report.max_processor_brushes,
            "max_processor_polygons": report.max_processor_polygons,
            "models": models,
        },
        "timings_seconds": {
            stage: elapsed for stage, elapsed in report.stage_timings_seconds
        },
        "diagnostics": {
            "terrain_cutout_coverage": _full_world_manifest_cutout_summary(
                report.terrain_cutout_coverage
            ),
            "terrain_support_source_coverage": _full_world_manifest_source_coverage_summary(
                report.terrain_support_source_coverage
            ),
            "physics_shell_source_coverage": _full_world_manifest_physics_shell_coverage_summary(
                report.physics_shell_source_coverage
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


def build_sky_marker_residue_compile_audit_manifest(
    report: SkyMarkerResidueCompileAuditReport,
) -> Dict[str, object]:
    return {
        "kind": "mm9_sky_marker_residue_compile_audit",
        "schema_version": 1,
        "status": report.status,
        "source_dat_path": report.source_dat_path,
        "source_ed_path": report.source_ed_path,
        "reference_dat_path": report.reference_dat_path,
        "artifacts": {
            "work_dir": report.work_dir,
            "generated_ed_path": report.generated_ed_path,
            "compiled_dat_path": report.compiled_dat_path,
            "processor_log_paths": list(report.processor_log_paths),
        },
        "generated_candidate": (
            {
                "status": report.acceptance.status,
                "model_count": report.acceptance.model_count,
                "point_count": report.acceptance.point_count,
                "polygon_count": report.acceptance.polygon_count,
                "object_count": report.acceptance.object_count,
                "object_property_count": report.acceptance.object_property_count,
                "generated_object_class_counts": dict(report.acceptance.generated_object_class_counts),
                "include_sky_marker_residue_brushes": report.acceptance.include_sky_marker_residue_brushes,
                "sky_marker_residue_reference_dat_path": report.acceptance.sky_marker_residue_reference_dat_path,
            }
            if report.acceptance is not None
            else None
        ),
        "residue_correlation": (
            {
                "status": report.residue_report.status,
                "source_sky_marker_face_count": report.residue_report.source_sky_marker_face_count,
                "compiled_sky_visibility_polygon_count": report.residue_report.compiled_sky_visibility_polygon_count,
                "compiled_physics_sky_visibility_polygon_count": report.residue_report.compiled_physics_sky_visibility_polygon_count,
                "compiled_visibility_sky_visibility_polygon_count": report.residue_report.compiled_visibility_sky_visibility_polygon_count,
                "compiled_residue_match_count": report.residue_report.compiled_residue_match_count,
                "compiled_residue_unmatched_count": report.residue_report.compiled_residue_unmatched_count,
                "matched_source_sky_marker_face_count": report.residue_report.matched_source_sky_marker_face_count,
                "matched_source_sky_marker_brush_count": report.residue_report.matched_source_sky_marker_brush_count,
            }
            if report.residue_report is not None
            else None
        ),
        "helper_leakage": (
            build_compiled_dat_helper_leakage_manifest(report.helper_leakage)
            if report.helper_leakage is not None
            else None
        ),
        "processor_logs": [
            {
                "path": log.path,
                "status": log.status,
                "problem_brush_count": log.problem_brush_count,
                "warning_counts": dict(log.warning_counts),
                "warnings": list(log.warnings),
            }
            for log in report.processor_logs
        ],
        "manual_steps": list(report.manual_steps),
        "blockers": list(report.blockers),
        "cautions": list(report.cautions),
        "notes": list(report.notes),
    }


def write_sky_marker_residue_compile_audit_manifest(
    report: SkyMarkerResidueCompileAuditReport,
    manifest_path: str,
) -> str:
    manifest = build_sky_marker_residue_compile_audit_manifest(report)
    os.makedirs(os.path.dirname(os.path.abspath(manifest_path)) or ".", exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    return manifest_path


def build_compiled_dat_helper_leakage_manifest(
    report: CompiledDatHelperLeakageReport,
) -> Dict[str, object]:
    return {
        "kind": "mm9_compiled_dat_helper_leakage",
        "schema_version": 1,
        "status": report.status,
        "compiled_dat_path": report.compiled_dat_path,
        "reference_dat_path": report.reference_dat_path,
        "summary": {
            "compiled_model_count": report.compiled_model_count,
            "reference_model_count": report.reference_model_count,
            "compiled_total_helper_polygon_count": report.compiled_total_helper_polygon_count,
            "reference_total_helper_polygon_count": report.reference_total_helper_polygon_count,
            "compiled_visibility_helper_polygon_count": report.compiled_visibility_helper_polygon_count,
            "reference_visibility_helper_polygon_count": report.reference_visibility_helper_polygon_count,
            "compiled_terrain_helper_polygon_count": report.compiled_terrain_helper_polygon_count,
            "reference_terrain_helper_polygon_count": report.reference_terrain_helper_polygon_count,
            "compiled_world_model_helper_polygon_count": report.compiled_world_model_helper_polygon_count,
            "reference_world_model_helper_polygon_count": report.reference_world_model_helper_polygon_count,
        },
        "role_comparisons": [
            {
                "role": item.role,
                "status": item.status,
                "compiled_total": item.compiled_total,
                "reference_total": item.reference_total,
                "compiled_by_model_kind": dict(item.compiled_by_model_kind),
                "reference_by_model_kind": dict(item.reference_by_model_kind),
                "notes": list(item.notes),
            }
            for item in report.role_comparisons
        ],
        "models": [
            _compiled_dat_helper_model_manifest(item)
            for item in report.model_summaries
            if item.helper_polygon_count > 0
        ],
        "reference_models": [
            _compiled_dat_helper_model_manifest(item)
            for item in report.reference_model_summaries
            if item.helper_polygon_count > 0
        ],
        "blockers": list(report.blockers),
        "cautions": list(report.cautions),
        "notes": list(report.notes),
    }


def write_compiled_dat_helper_leakage_manifest(
    report: CompiledDatHelperLeakageReport,
    manifest_path: str,
) -> str:
    manifest = build_compiled_dat_helper_leakage_manifest(report)
    os.makedirs(os.path.dirname(os.path.abspath(manifest_path)) or ".", exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    return manifest_path


def _compiled_dat_helper_model_manifest(
    item: CompiledDatHelperModelSummary,
) -> Dict[str, object]:
    return {
        "model_name": item.model_name,
        "model_kind": item.model_kind,
        "status": item.status,
        "polygon_count": item.polygon_count,
        "helper_polygon_count": item.helper_polygon_count,
        "helper_roles": dict(item.helper_roles),
        "helper_textures": dict(item.helper_textures),
        "notes": list(item.notes),
    }


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


def _full_world_manifest_physics_shell_coverage_summary(
    report: Optional["PhysicsShellSourceCoverageReport"],
) -> Optional[Dict[str, object]]:
    if report is None:
        return None
    return {
        "status": report.status,
        "physics_model_name": report.physics_model_name,
        "packing_mode": report.packing_mode,
        "source_polygon_count": report.source_polygon_count,
        "classified_source_polygon_count": report.classified_source_polygon_count,
        "generated_source_polygon_count": report.generated_source_polygon_count,
        "uncovered_source_polygon_count": report.uncovered_source_polygon_count,
        "generated_unknown_polygon_count": report.generated_unknown_polygon_count,
        "compiled_dat_path": report.compiled_dat_path,
        "compiled_matched_source_polygon_count": report.compiled_matched_source_polygon_count,
        "compiled_unmatched_source_polygon_count": report.compiled_unmatched_source_polygon_count,
        "diagnostic_status_counts": dict(report.diagnostic_status_counts),
        "loss_class_counts": dict(report.loss_class_counts),
        "subset_plan_status": report.subset_plan_status,
        "subset_validation_status_counts": dict(report.subset_validation_status_counts),
        "subset_failed_batch_count": report.subset_failed_batch_count,
        "hotspot_count": len(report.coverage_hotspots),
        "stair_assembly_count": len(report.stair_assemblies),
        "stair_assembly_confidence_counts": dict(Counter(
            item.confidence for item in report.stair_assemblies
        )),
        "top_hotspots": [
            {
                "name": item.name,
                "anchor_kind": item.anchor_kind,
                "priority_score": item.priority_score,
                "actionable_missing_polygon_count": item.actionable_missing_polygon_count,
                "actionable_missing_area": item.actionable_missing_area,
            }
            for item in report.coverage_hotspots[:8]
        ],
        "role_summaries": [
            {
                "role": item.role,
                "source_polygon_count": item.source_polygon_count,
                "generated_polygon_count": item.generated_polygon_count,
                "uncovered_polygon_count": item.uncovered_polygon_count,
            }
            for item in report.role_summaries
        ],
        "generated_brush_attribution_count": len(report.generated_brush_attributions),
        "blockers": list(report.blockers),
        "cautions": list(report.cautions),
        "notes": list(report.notes),
    }


def _physics_shell_source_coverage_manifest(
    report: PhysicsShellSourceCoverageReport,
) -> Dict[str, object]:
    diagnostics_by_index = {
        item.source_polygon_index: item
        for item in report.source_polygon_diagnostics
    }
    return {
        "kind": "mm9_physics_shell_source_coverage",
        "schema_version": 7,
        "status": report.status,
        "source_dat_path": report.source_dat_path,
        "generated_ed_path": report.generated_ed_path,
        "physics_model_name": report.physics_model_name,
        "packing_mode": report.packing_mode,
        "source_polygon_count": report.source_polygon_count,
        "classified_source_polygon_count": report.classified_source_polygon_count,
        "generated_source_polygon_count": report.generated_source_polygon_count,
        "uncovered_source_polygon_count": report.uncovered_source_polygon_count,
        "generated_unknown_polygon_count": report.generated_unknown_polygon_count,
        "compiled_dat_path": report.compiled_dat_path,
        "compiled_matched_source_polygon_count": report.compiled_matched_source_polygon_count,
        "compiled_unmatched_source_polygon_count": report.compiled_unmatched_source_polygon_count,
        "diagnostic_status_counts": dict(report.diagnostic_status_counts),
        "loss_class_counts": dict(report.loss_class_counts),
        "subset_plan_status": report.subset_plan_status,
        "subset_validation_status_counts": dict(report.subset_validation_status_counts),
        "subset_failed_batch_count": report.subset_failed_batch_count,
        "stair_assemblies": [
            {
                "assembly_index": item.assembly_index,
                "source_polygon_indices": list(item.source_polygon_indices),
                "tread_polygon_indices": list(item.tread_polygon_indices),
                "riser_polygon_indices": list(item.riser_polygon_indices),
                "support_wall_polygon_indices": list(item.support_wall_polygon_indices),
                "elevation_levels": list(item.elevation_levels),
                "bounds_min": list(item.bounds_min),
                "bounds_max": list(item.bounds_max),
                "step_count": item.step_count,
                "min_step_height": item.min_step_height,
                "max_step_height": item.max_step_height,
                "generated_face_count": item.generated_face_count,
                "confidence": item.confidence,
                "emitted_polygon_count": sum(
                    bool(diagnostics_by_index.get(index).generated_brush_names)
                    for index in item.source_polygon_indices
                    if diagnostics_by_index.get(index) is not None
                ),
                "compiled_retained_polygon_count": sum(
                    diagnostics_by_index.get(index).compiled_match_count > 0
                    for index in item.source_polygon_indices
                    if diagnostics_by_index.get(index) is not None
                ),
                "emission_complete": all(
                    diagnostics_by_index.get(index) is not None
                    and bool(diagnostics_by_index[index].generated_brush_names)
                    for index in item.source_polygon_indices
                ),
                "compiled_retention_complete": bool(report.compiled_dat_path) and all(
                    diagnostics_by_index.get(index) is not None
                    and diagnostics_by_index[index].compiled_match_count > 0
                    for index in item.source_polygon_indices
                ),
                "notes": list(item.notes),
            }
            for item in report.stair_assemblies
        ],
        "coverage_hotspots": [
            {
                "name": item.name,
                "anchor_kind": item.anchor_kind,
                "center": list(item.center),
                "radius": item.radius,
                "source_polygon_count": item.source_polygon_count,
                "emitted_polygon_count": item.emitted_polygon_count,
                "actionable_missing_polygon_count": item.actionable_missing_polygon_count,
                "protected_polygon_count": item.protected_polygon_count,
                "invalid_polygon_count": item.invalid_polygon_count,
                "source_area": item.source_area,
                "emitted_area": item.emitted_area,
                "actionable_missing_area": item.actionable_missing_area,
                "priority_score": item.priority_score,
                "role_counts": dict(item.role_counts),
                "status_counts": dict(item.status_counts),
                "top_missing_polygon_indices": list(item.top_missing_polygon_indices),
            }
            for item in report.coverage_hotspots
        ],
        "source_polygon_diagnostics": [
            {
                "source_polygon_index": item.source_polygon_index,
                "role": item.role,
                "status": item.status,
                "reason": item.reason,
                "loss_class": item.loss_class,
                "area": item.area,
                "bounds_min": list(item.bounds_min),
                "bounds_max": list(item.bounds_max),
                "generated_brush_names": list(item.generated_brush_names),
                "compiled_match_count": item.compiled_match_count,
                "subset_role": item.subset_role,
                "subset_batch_index": item.subset_batch_index,
                "subset_validation_status": item.subset_validation_status,
                "subset_problem_brush_count": item.subset_problem_brush_count,
                "subset_warning_count": item.subset_warning_count,
            }
            for item in report.source_polygon_diagnostics
        ],
        "role_summaries": [
            {
                "role": item.role,
                "source_polygon_count": item.source_polygon_count,
                "generated_polygon_count": item.generated_polygon_count,
                "uncovered_polygon_count": item.uncovered_polygon_count,
            }
            for item in report.role_summaries
        ],
        "generated_brush_attributions": [
            {
                "brush_name": item.brush_name,
                "source_model_name": item.source_model_name,
                "source_polygon_index": item.source_polygon_index,
                "role": item.role,
            }
            for item in report.generated_brush_attributions
        ],
        "generated_source_polygon_indices": list(report.generated_source_polygon_indices),
        "generated_unknown_polygon_indices": list(report.generated_unknown_polygon_indices),
        "blockers": list(report.blockers),
        "cautions": list(report.cautions),
        "notes": list(report.notes),
    }


def format_physics_shell_source_coverage_report(
    report: PhysicsShellSourceCoverageReport,
) -> str:
    lines = [
        "DAT PhysicsBSP shell source coverage",
        f"status: {report.status}",
        f"source DAT: {report.source_dat_path}",
        f"generated ED: {report.generated_ed_path}",
        f"physics model: {report.physics_model_name}",
        f"packing mode: {report.packing_mode}",
        (
            "coverage: "
            f"source_polygons={report.source_polygon_count}, "
            f"classified={report.classified_source_polygon_count}, "
            f"generated={report.generated_source_polygon_count}, "
            f"uncovered={report.uncovered_source_polygon_count}, "
            f"unknown_generated={report.generated_unknown_polygon_count}, "
            f"compiled_matched={report.compiled_matched_source_polygon_count}, "
            f"compiled_unmatched={report.compiled_unmatched_source_polygon_count}"
        ),
    ]
    if report.diagnostic_status_counts:
        lines.append(
            "diagnostic statuses: "
            + ", ".join(f"{status}={count}" for status, count in report.diagnostic_status_counts)
        )
    if report.loss_class_counts:
        lines.append(
            "loss classes: "
            + ", ".join(f"{loss_class}={count}" for loss_class, count in report.loss_class_counts)
        )
    if report.subset_plan_status != "not_supplied":
        lines.append(
            "subset evidence: "
            f"plan={report.subset_plan_status}, "
            + ", ".join(
                f"{status}={count}"
                for status, count in report.subset_validation_status_counts
            )
            + f", failed_batches={report.subset_failed_batch_count}"
        )
    if report.stair_assemblies:
        lines.append(
            "stair assemblies: "
            f"count={len(report.stair_assemblies)}, "
            + ", ".join(
                f"{confidence}={count}"
                for confidence, count in sorted(Counter(
                    item.confidence for item in report.stair_assemblies
                ).items())
            )
        )
        for assembly in report.stair_assemblies[:8]:
            lines.append(
                f"- stair {assembly.assembly_index}: confidence={assembly.confidence}, "
                f"steps={assembly.step_count}, treads={len(assembly.tread_polygon_indices)}, "
                f"risers={len(assembly.riser_polygon_indices)}, "
                f"rise={assembly.min_step_height:g}..{assembly.max_step_height:g}"
            )
    for hotspot in report.coverage_hotspots[:8]:
        lines.append(
            f"hotspot {hotspot.name} ({hotspot.anchor_kind}): "
            f"source={hotspot.source_polygon_count}, emitted={hotspot.emitted_polygon_count}, "
            f"actionable_missing={hotspot.actionable_missing_polygon_count}, "
            f"protected={hotspot.protected_polygon_count}, "
            f"missing_area={hotspot.actionable_missing_area:g}, "
            f"priority={hotspot.priority_score:g}"
        )
    for item in report.role_summaries:
        lines.append(
            f"- {item.role}: source={item.source_polygon_count}, "
            f"generated={item.generated_polygon_count}, uncovered={item.uncovered_polygon_count}"
        )
    if report.generated_brush_attributions:
        lines.append(
            "generated brush provenance: "
            f"{len(report.generated_brush_attributions)} brush(es) mapped to source model, polygon, and role"
        )
        for item in report.generated_brush_attributions[:16]:
            lines.append(
                f"- {item.brush_name}: source={item.source_model_name}[{item.source_polygon_index}], "
                f"role={item.role}"
            )
        if len(report.generated_brush_attributions) > 16:
            lines.append(
                f"- ... {len(report.generated_brush_attributions) - 16} more; see the JSON manifest"
            )
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_compiled_dat_helper_leakage_report(
    report: CompiledDatHelperLeakageReport,
) -> str:
    lines = [
        "DAT compiled helper leakage",
        f"status: {report.status}",
        f"compiled DAT: {report.compiled_dat_path}",
    ]
    if report.reference_dat_path:
        lines.append(f"reference DAT: {report.reference_dat_path}")
    lines.append(
        "helper totals: "
        f"compiled={report.compiled_total_helper_polygon_count}, "
        f"reference={report.reference_total_helper_polygon_count}, "
        f"VisBSP={report.compiled_visibility_helper_polygon_count}/"
        f"{report.reference_visibility_helper_polygon_count}, "
        f"Terrain*={report.compiled_terrain_helper_polygon_count}/"
        f"{report.reference_terrain_helper_polygon_count}, "
        f"world_models={report.compiled_world_model_helper_polygon_count}/"
        f"{report.reference_world_model_helper_polygon_count}"
    )
    for comparison in report.role_comparisons:
        compiled_kinds = ", ".join(
            f"{kind}:{count}"
            for kind, count in sorted(comparison.compiled_by_model_kind.items())
        ) or "none"
        reference_kinds = ", ".join(
            f"{kind}:{count}"
            for kind, count in sorted(comparison.reference_by_model_kind.items())
        ) or "none"
        lines.append(
            f"- role {comparison.role}: {comparison.status}, "
            f"compiled={comparison.compiled_total}, reference={comparison.reference_total}, "
            f"compiled_kinds={compiled_kinds}, reference_kinds={reference_kinds}"
        )
        for note in comparison.notes:
            lines.append(f"  note: {note}")
    for item in report.model_summaries:
        if item.helper_polygon_count <= 0:
            continue
        role_text = ", ".join(f"{role}:{count}" for role, count in sorted(item.helper_roles.items()))
        texture_text = ", ".join(
            f"{texture}:{count}"
            for texture, count in sorted(item.helper_textures.items())
        )
        lines.append(
            f"- model {item.model_name}: kind={item.model_kind}, status={item.status}, "
            f"polygons={item.polygon_count}, helpers={item.helper_polygon_count}, "
            f"roles={role_text or 'none'}, textures={texture_text or 'none'}"
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


@dataclass(frozen=True)
class _SkyMarkerSourceFaceEvidence:
    brush_name: str
    model_index: int
    face_index: int
    vertex_count: int = 0
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    normal: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    brush_flags: Tuple[str, ...] = ()
    texture_flags: Optional[int] = None
    surface_flags: Optional[int] = None


@dataclass(frozen=True)
class _SkyMarkerCompiledResiduePolygon:
    model_name: str
    model_kind: str
    model_index: int
    polygon_index: int
    vertex_count: int = 0
    texture_name: str = ""
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    normal: Tuple[float, float, float] = (0.0, 0.0, 0.0)


def _sky_marker_face_key(item: object) -> Tuple[int, int]:
    return (
        int(getattr(item, "model_index", -1)),
        int(getattr(item, "face_index", -1)),
    )


def _compiled_dat_helper_model_summaries(
    bsp_world: object,
) -> Tuple[CompiledDatHelperModelSummary, ...]:
    summaries: List[CompiledDatHelperModelSummary] = []
    for model in getattr(bsp_world, "world_models", ()) or ():
        name = str(getattr(model, "name", "") or "")
        kind = _compiled_dat_model_kind(model)
        polygons = tuple(getattr(model, "polygons", ()) or ())
        helper_roles: Dict[str, int] = {}
        helper_textures: Dict[str, int] = {}
        notes: List[str] = []
        for polygon in polygons:
            try:
                texture = str(model.texture_name_for(polygon) or "")
            except Exception as exc:
                notes.append(f"{name}: texture lookup failed: {exc}")
                texture = ""
            role = terrain_semantics.helper_texture_role(texture)
            if not role:
                continue
            helper_roles[role] = int(helper_roles.get(role, 0)) + 1
            helper_textures[texture] = int(helper_textures.get(texture, 0)) + 1
        helper_count = sum(helper_roles.values())
        if helper_count <= 0:
            status = "no_helper_textures"
        elif kind == "physics_bsp":
            status = "physics_helper_data"
        elif kind == "visibility_bsp":
            status = "visibility_helper_leak"
        elif kind == "terrain":
            status = "terrain_helper_leak"
        elif kind == "world_model":
            status = "world_model_helper_geometry"
        else:
            status = "helper_geometry"
        summaries.append(CompiledDatHelperModelSummary(
            model_name=name,
            model_kind=kind,
            polygon_count=len(polygons),
            helper_polygon_count=helper_count,
            helper_roles=dict(sorted(helper_roles.items())),
            helper_textures=dict(sorted(helper_textures.items())),
            status=status,
            notes=tuple(_unique_text(notes)),
        ))
    return tuple(summaries)


def _compiled_dat_model_kind(model: object) -> str:
    if terrain_semantics.is_physics_bsp_model(model):
        return "physics_bsp"
    if terrain_semantics.is_vis_bsp_model(model):
        return "visibility_bsp"
    if terrain_semantics.is_terrain_model(model):
        return "terrain"
    try:
        if bool(getattr(model, "is_skybox", lambda: False)()):
            return "skybox"
    except Exception:
        pass
    return "world_model"


def _helper_counts_by_role_and_kind(
    model_summaries: Sequence[CompiledDatHelperModelSummary],
) -> Dict[str, Dict[str, int]]:
    result: Dict[str, Dict[str, int]] = {}
    for item in model_summaries:
        for role, count in item.helper_roles.items():
            role_entry = result.setdefault(str(role), {})
            role_entry[item.model_kind] = int(role_entry.get(item.model_kind, 0)) + int(count)
    return {
        role: dict(sorted(kind_counts.items()))
        for role, kind_counts in sorted(result.items())
    }


def _helper_counts_by_model_and_role(
    model_summaries: Sequence[CompiledDatHelperModelSummary],
) -> Dict[Tuple[str, str], int]:
    result: Dict[Tuple[str, str], int] = {}
    for item in model_summaries:
        for role, count in item.helper_roles.items():
            key = (item.model_name, str(role))
            result[key] = int(result.get(key, 0)) + int(count)
    return result


def _helper_role_totals(
    model_summaries: Sequence[CompiledDatHelperModelSummary],
) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for item in model_summaries:
        for role, count in item.helper_roles.items():
            result[str(role)] = int(result.get(str(role), 0)) + int(count)
    return dict(sorted(result.items()))


def _helper_count_for_kind(
    model_summaries: Sequence[CompiledDatHelperModelSummary],
    kind: str,
) -> int:
    return sum(
        int(item.helper_polygon_count)
        for item in model_summaries
        if item.model_kind == kind
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


def _load_dat_output_semantic_summary(
    path: str,
    *,
    _preloaded_data: Optional[bytes] = None,
    _preparsed_world: Optional[object] = None,
) -> DatOutputSemanticSummary:
    absolute = os.path.abspath(path)
    data = _preloaded_data
    if data is None:
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
    else:
        version = struct.unpack_from("<I", data, 0)[0] if len(data) >= 4 else None

    parsed = _preparsed_world
    if parsed is None:
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

        inspections = bsp_record_inspector.inspect_dat(
            data,
            model_names=inspect_names,
            parsed_world=parsed,
        )
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


def _generated_physics_shell_source_polygon_indices(
    brush_names: Sequence[str],
    shell_prefix: str,
) -> Tuple[int, ...]:
    return tuple(
        index
        for _brush_name, index in _generated_physics_shell_brush_indices(
            brush_names,
            shell_prefix,
        )
    )


def _generated_physics_shell_brush_indices(
    brush_names: Sequence[str],
    shell_prefix: str,
) -> Tuple[Tuple[str, int], ...]:
    prefix = _legacy_name_component(shell_prefix or "PhysicsShell")
    if not prefix:
        prefix = "PhysicsShell"
    role_tokens = "floor|ceiling|side_wall|helper_special|degenerate|unknown"
    pattern = re.compile(
        r"(?:^|_)" + re.escape(prefix)
        + rf"_(?:(?:{role_tokens})_)?(\d+(?:p\d+)*)(?:_\d+)?$"
    )
    result: List[Tuple[str, int]] = []
    seen = set()
    for name in brush_names:
        match = pattern.search(str(name or ""))
        if not match:
            continue
        for index_text in match.group(1).split("p"):
            index = int(index_text)
            if index in seen:
                continue
            seen.add(index)
            result.append((str(name or ""), index))
    return tuple(result)


def _physics_shell_source_polygon_roles(model: object) -> Dict[int, str]:
    return terrain_reconstruction.physics_shell_source_polygon_roles(model)


def _legacy_name_component(value: object) -> str:
    result: List[str] = []
    for ch in str(value or ""):
        if ch.isalnum() or ch in {"_", "-"}:
            result.append(ch)
        elif ch.isspace():
            result.append("_")
    return "".join(result).strip("_-")


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


def _airail_oracle_objects(source_ed_path: str) -> Tuple[AirailOracleObject, ...]:
    from features.dat_editing import legacy_ed

    scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed_path)
    result: List[AirailOracleObject] = []
    for record in scan.records:
        if str(record.class_name).lower() != "airail":
            continue
        raw_pos = record.property_value("Pos", (0.0, 0.0, 0.0))
        name = str(record.property_value("Name", "") or f"AIRail{len(result)}")
        result.append(AirailOracleObject(
            name=name,
            class_name=str(record.class_name),
            pos=_safe_vec3(raw_pos),
        ))
    return tuple(result)


def _airail_rail_brush_oracles(source_ed_path: str) -> Tuple[AirailRailBrushOracle, ...]:
    from features.dat_editing import legacy_ed

    scene = legacy_ed.load_legacy_ed_geometry_scene(source_ed_path)
    result: List[AirailRailBrushOracle] = []
    for model in scene.models:
        rail_indices = set()
        rail_face_count = 0
        for face in getattr(model, "faces", ()) or ():
            if "rail.dtx" not in str(getattr(face, "material_name", "") or "").lower():
                continue
            rail_face_count += 1
            rail_indices.update(int(index) for index in getattr(face, "vertex_indices", ()) or ())
        if not rail_indices:
            continue
        points = [
            _safe_vec3(model.points[index])
            for index in rail_indices
            if 0 <= index < len(getattr(model, "points", ()) or ())
        ]
        if not points:
            continue
        bounds_min, bounds_max, center = _points_bounds_center(points)
        result.append(AirailRailBrushOracle(
            name=str(getattr(model, "name", "") or f"RailBrush{len(result)}"),
            bounds_min=bounds_min,
            bounds_max=bounds_max,
            center=center,
            rail_face_count=rail_face_count,
        ))
    return tuple(result)


def _airail_reconstruction_candidate(
    model_index: int,
    summary: PrefabSurrogateCompositeModelSummary,
    *,
    source_airail_objects: Sequence[AirailOracleObject],
    source_rail_brushes: Sequence[AirailRailBrushOracle],
    max_object_match_distance: float,
    ambiguous_distance_epsilon: float,
) -> AirailReconstructionCandidate:
    nearest_object, object_distance, ambiguous_object = _nearest_airail_object(
        summary.center,
        source_airail_objects,
        ambiguous_distance_epsilon=ambiguous_distance_epsilon,
    )
    nearest_brush, brush_distance, _ambiguous_brush = _nearest_airail_rail_brush(
        summary.center,
        source_rail_brushes,
        ambiguous_distance_epsilon=ambiguous_distance_epsilon,
    )

    notes: List[str] = []
    if not source_airail_objects:
        status = "pending_source_oracle"
        notes.append("source ED AIRail object oracle is not available")
    elif nearest_object is None or object_distance is None:
        status = "unmatched_source_airail"
        notes.append("no source AIRail object candidate was found")
    elif object_distance > max(0.0, float(max_object_match_distance)):
        status = "unmatched_source_airail"
        notes.append(
            f"nearest AIRail object is {object_distance:.2f} units away, beyond match distance {float(max_object_match_distance):.2f}"
        )
    elif ambiguous_object:
        status = "ambiguous_source_airail"
        notes.append("nearest AIRail object is ambiguous within the distance epsilon")
    else:
        status = "matched_source_airail"

    return AirailReconstructionCandidate(
        source_model_name=summary.name,
        source_model_index=int(model_index),
        polygon_count=summary.polygon_count,
        bounds_min=summary.bounds_min,
        bounds_max=summary.bounds_max,
        center=summary.center,
        nearest_airail_name=nearest_object.name if nearest_object else "",
        nearest_airail_distance=object_distance,
        nearest_rail_brush_name=nearest_brush.name if nearest_brush else "",
        nearest_rail_brush_distance=brush_distance,
        status=status,
        notes=tuple(notes),
    )


def _sky_object_oracles(source_ed_path: str) -> Tuple[SkyObjectOracle, ...]:
    from features.dat_editing import legacy_ed

    scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed_path)
    result: List[SkyObjectOracle] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name not in _SKY_OBJECT_CLASSES:
            continue
        name = str(record.property_value("Name", "") or f"{class_name}{len(result)}")
        result.append(SkyObjectOracle(
            name=name,
            class_name=class_name,
            pos=_safe_vec3(record.property_value("Pos", (0.0, 0.0, 0.0))),
            property_count=len(record.properties),
        ))
    return tuple(result)


def _sky_marker_brush_oracles(source_ed_path: str) -> Tuple[SkyMarkerBrushOracle, ...]:
    from features.dat_editing import legacy_ed

    scene = legacy_ed.load_legacy_ed_geometry_scene(source_ed_path)
    scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed_path)
    brush_records = [record for record in scan.records if record.class_name == "Brush"]
    result: List[SkyMarkerBrushOracle] = []
    for model_index, model in enumerate(scene.models):
        sky_indices = set()
        sky_face_count = 0
        for face in getattr(model, "faces", ()) or ():
            role = terrain_semantics.helper_texture_role(getattr(face, "material_name", ""))
            if role != "skyVisibility":
                continue
            sky_face_count += 1
            sky_indices.update(int(index) for index in getattr(face, "vertex_indices", ()) or ())
        if sky_face_count <= 0:
            continue
        points = [
            _safe_vec3(model.points[index])
            for index in sky_indices
            if 0 <= index < len(getattr(model, "points", ()) or ())
        ]
        if not points:
            continue
        bounds_min, bounds_max, center = _points_bounds_center(points)
        name = ""
        if model_index < len(brush_records):
            name = str(brush_records[model_index].property_value("Name", "") or "")
        result.append(SkyMarkerBrushOracle(
            name=name or str(getattr(model, "name", "") or f"SkyMarkerBrush{len(result)}"),
            bounds_min=bounds_min,
            bounds_max=bounds_max,
            center=center,
            sky_face_count=sky_face_count,
        ))
    return tuple(result)


_SKY_MARKER_BRUSH_FLAG_NAMES = (
    "SkyPortal",
    "FullyBright",
    "FlatShade",
    "GouraudShade",
    "LightMap",
    "Subdivide",
    "NoSnap",
    "SkyPan",
    "Additive",
    "TimeOfDay",
    "TerrainOccluder",
    "VisBlocker",
    "NotAStep",
)


def _sky_marker_brush_flag_counts(source_ed_path: str) -> Dict[str, int]:
    from features.dat_editing import legacy_ed

    scene = legacy_ed.load_legacy_ed_geometry_scene(source_ed_path)
    scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed_path)
    brush_records = [record for record in scan.records if record.class_name == "Brush"]
    counts: Dict[str, int] = {name: 0 for name in _SKY_MARKER_BRUSH_FLAG_NAMES}
    for model_index, model in enumerate(scene.models):
        role_counts: Dict[str, int] = {}
        for face in getattr(model, "faces", ()) or ():
            role = terrain_semantics.helper_texture_role(getattr(face, "material_name", ""))
            if role:
                role_counts[role] = role_counts.get(role, 0) + 1
        if int(role_counts.get("skyVisibility", 0)) <= 0:
            continue
        if set(role_counts.keys()) != {"skyVisibility"}:
            continue
        if model_index >= len(brush_records):
            continue
        record = brush_records[model_index]
        for flag_name in _SKY_MARKER_BRUSH_FLAG_NAMES:
            if bool(record.property_value(flag_name, False)):
                counts[flag_name] += 1
    return {name: count for name, count in counts.items() if count}


def _sky_marker_source_face_evidence(
    source_ed_path: str,
) -> Tuple[_SkyMarkerSourceFaceEvidence, ...]:
    from features.dat_editing import legacy_ed

    scene = legacy_ed.load_legacy_ed_geometry_scene(source_ed_path)
    scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed_path)
    brush_records = [record for record in scan.records if record.class_name == "Brush"]
    result: List[_SkyMarkerSourceFaceEvidence] = []
    for model_index, model in enumerate(scene.models):
        record = brush_records[model_index] if model_index < len(brush_records) else None
        brush_name = ""
        brush_flags: Tuple[str, ...] = ()
        if record is not None:
            brush_name = str(record.property_value("Name", "") or "")
            brush_flags = tuple(
                flag_name
                for flag_name in _SKY_MARKER_BRUSH_FLAG_NAMES
                if bool(record.property_value(flag_name, False))
            )
        if not brush_name:
            brush_name = str(getattr(model, "name", "") or f"SkyMarkerBrush{model_index}")
        model_points = tuple(_safe_vec3(point) for point in getattr(model, "points", ()) or ())
        for face_index, face in enumerate(getattr(model, "faces", ()) or ()):
            role = terrain_semantics.helper_texture_role(getattr(face, "material_name", ""))
            if role != "skyVisibility":
                continue
            indices = tuple(int(index) for index in getattr(face, "vertex_indices", ()) or ())
            points = [
                model_points[index]
                for index in indices
                if 0 <= index < len(model_points)
            ]
            if len(points) < 3 or len(points) != len(indices):
                continue
            normal = _normalized_vec3(_safe_vec3(getattr(face, "extras", {}).get("normal", (0.0, 0.0, 0.0))))
            if _vec3_length(normal) <= 0.0:
                normal = _polygon_normal(points)
            texture_flags = _optional_int(getattr(face, "extras", {}).get("texture_flags", None))
            surface_flags = _optional_int(getattr(face, "extras", {}).get("surface_flags", None))
            result.append(_SkyMarkerSourceFaceEvidence(
                brush_name=brush_name,
                model_index=int(model_index),
                face_index=int(face_index),
                vertex_count=len(indices),
                center=_polygon_center(points),
                normal=normal,
                brush_flags=brush_flags,
                texture_flags=texture_flags,
                surface_flags=surface_flags,
            ))
    return tuple(result)


def _compiled_sky_marker_residue_polygons(
    bsp_world: object,
) -> Tuple[_SkyMarkerCompiledResiduePolygon, ...]:
    result: List[_SkyMarkerCompiledResiduePolygon] = []
    for model_index, model in enumerate(getattr(bsp_world, "world_models", ()) or ()):
        model_name = str(getattr(model, "name", "") or "")
        model_kind = _compiled_dat_model_kind(model)
        model_points = tuple(_safe_vec3(point) for point in getattr(model, "points", ()) or ())
        for polygon_index, polygon in enumerate(getattr(model, "polygons", ()) or ()):
            try:
                texture_name = str(model.texture_name_for(polygon) or "")
            except Exception:
                texture_name = ""
            role = terrain_semantics.helper_texture_role(texture_name)
            if role != "skyVisibility":
                continue
            indices = tuple(int(index) for index in getattr(polygon, "vertex_indices", ()) or ())
            points = [
                model_points[index]
                for index in indices
                if 0 <= index < len(model_points)
            ]
            center = _polygon_center(points) if points else (0.0, 0.0, 0.0)
            normal = _polygon_normal(points) if len(points) >= 3 else (0.0, 0.0, 0.0)
            result.append(_SkyMarkerCompiledResiduePolygon(
                model_name=model_name,
                model_kind=model_kind,
                model_index=int(model_index),
                polygon_index=int(polygon_index),
                vertex_count=len(indices),
                texture_name=texture_name,
                center=center,
                normal=normal,
            ))
    return tuple(result)


def _compiled_structural_polygon_centers(
    bsp_world: object,
) -> Tuple[Tuple[float, float, float], ...]:
    result: List[Tuple[float, float, float]] = []
    for model in getattr(bsp_world, "world_models", ()) or ():
        if not (
            terrain_semantics.is_physics_bsp_model(model)
            or terrain_semantics.is_terrain_model(model)
        ):
            continue
        model_points = tuple(_safe_vec3(point) for point in getattr(model, "points", ()) or ())
        for polygon in getattr(model, "polygons", ()) or ():
            try:
                texture_name = str(model.texture_name_for(polygon) or "")
            except Exception:
                texture_name = ""
            if terrain_semantics.helper_texture_role(texture_name):
                continue
            indices = tuple(int(index) for index in getattr(polygon, "vertex_indices", ()) or ())
            points = [
                model_points[index]
                for index in indices
                if 0 <= index < len(model_points)
            ]
            if len(points) < 3 or len(points) != len(indices):
                continue
            result.append(_polygon_center(points))
    return tuple(result)


def _nearest_source_face_world_geometry_distances(
    source_faces: Sequence[_SkyMarkerSourceFaceEvidence],
    world_geometry_centers: Sequence[Tuple[float, float, float]],
) -> Dict[Tuple[int, int], float]:
    if not source_faces or not world_geometry_centers:
        return {}
    result: Dict[Tuple[int, int], float] = {}
    for face in source_faces:
        best_sq: Optional[float] = None
        for center in world_geometry_centers:
            distance_sq = terrain_reconstruction.vec3_distance_sq(face.center, center)
            if best_sq is None or distance_sq < best_sq:
                best_sq = distance_sq
        if best_sq is not None:
            result[_sky_marker_face_key(face)] = math.sqrt(float(best_sq))
    return result


def _sky_marker_source_face_cohort_summary(
    cohort: str,
    source_faces: Sequence[_SkyMarkerSourceFaceEvidence],
    nearest_world_geometry_distances: Dict[Tuple[int, int], float],
) -> SkyMarkerSourceFaceCohortSummary:
    if not source_faces:
        return SkyMarkerSourceFaceCohortSummary(cohort=cohort)
    centers = [item.center for item in source_faces]
    center_bounds_min, center_bounds_max, _center = _points_bounds_center(centers)
    brush_keys = {
        (int(item.model_index), str(item.brush_name))
        for item in source_faces
    }
    orientation_counts: Dict[str, int] = {}
    brush_flag_counts: Dict[str, int] = {}
    brush_flag_set_counts: Dict[str, int] = {}
    texture_flag_counts: Dict[str, int] = {}
    surface_flag_counts: Dict[str, int] = {}
    vertex_count_counts: Dict[str, int] = {}
    for item in source_faces:
        orientation = _axis_orientation_label(item.normal)
        orientation_counts[orientation] = int(orientation_counts.get(orientation, 0)) + 1
        flag_set = "+".join(item.brush_flags) if item.brush_flags else "none"
        brush_flag_set_counts[flag_set] = int(brush_flag_set_counts.get(flag_set, 0)) + 1
        for flag_name in item.brush_flags:
            brush_flag_counts[flag_name] = int(brush_flag_counts.get(flag_name, 0)) + 1
        texture_key = _optional_int_count_key(item.texture_flags)
        texture_flag_counts[texture_key] = int(texture_flag_counts.get(texture_key, 0)) + 1
        surface_key = _optional_int_count_key(item.surface_flags)
        surface_flag_counts[surface_key] = int(surface_flag_counts.get(surface_key, 0)) + 1
        vertex_key = str(int(item.vertex_count))
        vertex_count_counts[vertex_key] = int(vertex_count_counts.get(vertex_key, 0)) + 1
    distances = [
        float(nearest_world_geometry_distances[_sky_marker_face_key(item)])
        for item in source_faces
        if _sky_marker_face_key(item) in nearest_world_geometry_distances
    ]
    return SkyMarkerSourceFaceCohortSummary(
        cohort=cohort,
        source_face_count=len(source_faces),
        source_brush_count=len(brush_keys),
        orientation_counts=dict(sorted(orientation_counts.items())),
        brush_flag_counts=dict(sorted(brush_flag_counts.items())),
        brush_flag_set_counts=dict(sorted(brush_flag_set_counts.items())),
        texture_flag_counts=dict(sorted(texture_flag_counts.items())),
        surface_flag_counts=dict(sorted(surface_flag_counts.items())),
        vertex_count_counts=dict(sorted(vertex_count_counts.items())),
        center_bounds_min=center_bounds_min,
        center_bounds_max=center_bounds_max,
        nearest_world_geometry_distance_min=min(distances) if distances else None,
        nearest_world_geometry_distance_median=_median_float(distances),
        nearest_world_geometry_distance_average=(
            sum(distances) / float(len(distances)) if distances else None
        ),
        nearest_world_geometry_distance_max=max(distances) if distances else None,
    )


def _sky_marker_residue_rule_candidates(
    source_faces: Sequence[_SkyMarkerSourceFaceEvidence],
    matched_source_face_keys: set[Tuple[int, int]],
    nearest_world_geometry_distances: Dict[Tuple[int, int], float],
) -> Tuple[SkyMarkerResidueRuleCandidate, ...]:
    source_face_keys = {_sky_marker_face_key(item) for item in source_faces}

    def selected_keys_for(predicate: Callable[[_SkyMarkerSourceFaceEvidence], bool]) -> set[Tuple[int, int]]:
        result: set[Tuple[int, int]] = set()
        for item in source_faces:
            try:
                if bool(predicate(item)):
                    result.add(_sky_marker_face_key(item))
            except Exception:
                continue
        return result

    def candidate(
        rule_name: str,
        selected_keys: set[Tuple[int, int]],
        *,
        notes: Tuple[str, ...] = (),
        oracle: bool = False,
    ) -> SkyMarkerResidueRuleCandidate:
        selected_keys = {key for key in selected_keys if key in source_face_keys}
        true_positive = len(selected_keys & matched_source_face_keys)
        false_positive = len(selected_keys - matched_source_face_keys)
        false_negative = len(matched_source_face_keys - selected_keys)
        precision = (
            float(true_positive) / float(len(selected_keys))
            if selected_keys
            else None
        )
        recall = (
            float(true_positive) / float(len(matched_source_face_keys))
            if matched_source_face_keys
            else None
        )
        status_notes = list(notes)
        if oracle:
            status = "oracle_target"
        elif false_positive == 0 and false_negative == 0:
            status = "exact_candidate"
        elif false_negative == 0:
            status = "complete_but_too_broad"
            status_notes.append("captures every known residue source face but includes extra source faces")
        elif false_positive == 0:
            status = "precise_but_incomplete"
            status_notes.append("selects only known residue source faces but misses some residues")
        else:
            status = "heuristic_only"
            status_notes.append("does not yet match the shipped residue target")
        return SkyMarkerResidueRuleCandidate(
            rule_name=rule_name,
            selected_source_face_count=len(selected_keys),
            matched_source_face_count=true_positive,
            unmatched_source_face_count=false_positive,
            missed_matched_source_face_count=false_negative,
            precision=precision,
            recall=recall,
            status=status,
            notes=tuple(_unique_text(status_notes)),
        )

    rules: List[SkyMarkerResidueRuleCandidate] = [
        candidate(
            "compiled_reference_correlated_faces",
            set(matched_source_face_keys),
            oracle=True,
            notes=("requires a shipped compiled reference; useful as the target set, not as a standalone generator rule",),
        ),
        candidate(
            "texture_flags_1",
            selected_keys_for(lambda item: item.texture_flags == 1),
        ),
        candidate(
            "texture_flags_1_not_positive_y",
            selected_keys_for(
                lambda item: item.texture_flags == 1
                and _axis_orientation_label(item.normal) != "+Y"
            ),
        ),
        candidate(
            "texture_flags_1_not_positive_y_center_y_at_or_above_700",
            selected_keys_for(
                lambda item: item.texture_flags == 1
                and _axis_orientation_label(item.normal) != "+Y"
                and float(item.center[1]) >= 700.0
            ),
        ),
        candidate(
            "texture_flags_1_not_positive_y_near_world_geometry_2304",
            selected_keys_for(
                lambda item: item.texture_flags == 1
                and _axis_orientation_label(item.normal) != "+Y"
                and nearest_world_geometry_distances.get(_sky_marker_face_key(item), float("inf")) <= 2304.0
            ),
        ),
        candidate(
            "texture_flags_1_not_positive_y_near_world_geometry_768",
            selected_keys_for(
                lambda item: item.texture_flags == 1
                and _axis_orientation_label(item.normal) != "+Y"
                and nearest_world_geometry_distances.get(_sky_marker_face_key(item), float("inf")) <= 768.0
            ),
        ),
    ]
    return tuple(rules)


def _sky_marker_compiled_residue_matches(
    source_faces: Sequence[_SkyMarkerSourceFaceEvidence],
    compiled_polygons: Sequence[_SkyMarkerCompiledResiduePolygon],
) -> Tuple[SkyMarkerCompiledResidueMatch, ...]:
    matches: List[SkyMarkerCompiledResidueMatch] = []
    for compiled in compiled_polygons:
        if not source_faces:
            matches.append(SkyMarkerCompiledResidueMatch(
                compiled_model_name=compiled.model_name,
                compiled_model_kind=compiled.model_kind,
                compiled_model_index=compiled.model_index,
                compiled_polygon_index=compiled.polygon_index,
                compiled_vertex_count=compiled.vertex_count,
                compiled_center=compiled.center,
                compiled_normal=compiled.normal,
                status="unmatched_no_source_faces",
            ))
            continue
        ranked: List[Tuple[float, float, float, _SkyMarkerSourceFaceEvidence, float, float, float]] = []
        for source in source_faces:
            normal_dot = abs(_vec3_dot(compiled.normal, source.normal))
            center_distance = terrain_reconstruction.vec3_distance(compiled.center, source.center)
            plane_distance = abs(_vec3_dot(source.normal, _vec3_subtract(compiled.center, source.center)))
            ranked.append((
                plane_distance,
                1.0 - normal_dot,
                center_distance,
                source,
                normal_dot,
                center_distance,
                plane_distance,
            ))
        ranked.sort(key=lambda item: (
            item[0],
            item[1],
            item[2],
            item[3].model_index,
            item[3].face_index,
        ))
        _rank_plane, _rank_normal, _rank_center, source, normal_dot, center_distance, plane_distance = ranked[0]
        notes: List[str] = []
        if normal_dot >= 0.999 and plane_distance <= 1.0:
            status = "source_face_plane_match"
        elif normal_dot >= 0.99 and plane_distance <= 32.0:
            status = "source_face_near_plane_match"
        elif normal_dot >= 0.95:
            status = "nearest_parallel_source_face"
            notes.append("nearest source face is parallel but not on the same plane")
        else:
            status = "nearest_source_face"
            notes.append("nearest source face has a weak normal match")
        if status == "source_face_plane_match" and center_distance > 256.0:
            notes.append("center differs after Processor clipping or polygon merging")
        matches.append(SkyMarkerCompiledResidueMatch(
            compiled_model_name=compiled.model_name,
            compiled_model_kind=compiled.model_kind,
            compiled_model_index=compiled.model_index,
            compiled_polygon_index=compiled.polygon_index,
            compiled_vertex_count=compiled.vertex_count,
            compiled_center=compiled.center,
            compiled_normal=compiled.normal,
            source_brush_name=source.brush_name,
            source_model_index=source.model_index,
            source_face_index=source.face_index,
            source_vertex_count=source.vertex_count,
            source_center=source.center,
            source_normal=source.normal,
            source_brush_flags=source.brush_flags,
            source_texture_flags=source.texture_flags,
            source_surface_flags=source.surface_flags,
            center_distance=center_distance,
            plane_distance=plane_distance,
            normal_dot=normal_dot,
            status=status,
            notes=tuple(notes),
        ))
    return tuple(matches)


def _compiled_helper_role_total(
    summaries: Sequence[CompiledDatHelperModelSummary],
    role: str,
) -> int:
    return sum(int(item.helper_roles.get(role, 0) or 0) for item in summaries)


def _compiled_helper_role_count_for_kind(
    summaries: Sequence[CompiledDatHelperModelSummary],
    role: str,
    model_kind: str,
) -> int:
    return sum(
        int(item.helper_roles.get(role, 0) or 0)
        for item in summaries
        if item.model_kind == model_kind
    )


def _sound_object_oracles(source_ed_path: str) -> Tuple[SoundObjectOracle, ...]:
    from features.dat_editing import legacy_ed

    scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed_path)
    result: List[SoundObjectOracle] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name != "AmbientSound":
            continue
        name = str(record.property_value("Name", "") or f"AmbientSound{len(result)}")
        outer_radius = record.property_value("OuterRadius", None)
        inner_radius = record.property_value("InnerRadius", None)
        result.append(SoundObjectOracle(
            name=name,
            class_name=class_name,
            pos=_safe_vec3(record.property_value("Pos", (0.0, 0.0, 0.0))),
            filename=str(record.property_value("Filename", "") or ""),
            outer_radius=float(outer_radius) if isinstance(outer_radius, (int, float)) else None,
            inner_radius=float(inner_radius) if isinstance(inner_radius, (int, float)) else None,
            property_count=len(record.properties),
        ))
    return tuple(result)


_GAMEPLAY_TRIGGER_CLASSES = {"Trigger", "ExitTrigger", "PortalTrigger"}


def _gameplay_trigger_object_oracles(source_ed_path: str) -> Tuple[GameplayTriggerObjectOracle, ...]:
    from features.dat_editing import legacy_ed

    scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed_path)
    result: List[GameplayTriggerObjectOracle] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name not in _GAMEPLAY_TRIGGER_CLASSES:
            continue
        name = str(record.property_value("Name", "") or f"{class_name}{len(result)}")
        target_count = 0
        for index in range(1, 11):
            if str(record.property_value(f"TargetName{index}", "") or "").strip():
                target_count += 1
        result.append(GameplayTriggerObjectOracle(
            name=name,
            class_name=class_name,
            pos=_safe_vec3(record.property_value("Pos", (0.0, 0.0, 0.0))),
            dims=_safe_vec3(record.property_value("Dims", (0.0, 0.0, 0.0))),
            target_count=target_count,
            destination_world=str(record.property_value("DestinationWorld", "") or ""),
            portal_name=str(record.property_value("PortalName", "") or ""),
            property_count=len(record.properties),
        ))
    return tuple(result)


def _static_prop_object_oracles(source_ed_path: str) -> Tuple[StaticPropObjectOracle, ...]:
    from features.dat_editing import legacy_ed

    def optional_float(value: object) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    def optional_bool(value: object) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        return None

    scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed_path)
    result: List[StaticPropObjectOracle] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name != "Prop":
            continue
        name = str(record.property_value("Name", "") or f"Prop{len(result)}")
        result.append(StaticPropObjectOracle(
            name=name,
            class_name=class_name,
            pos=_safe_vec3(record.property_value("Pos", (0.0, 0.0, 0.0))),
            filename=str(record.property_value("Filename", "") or ""),
            skin=str(record.property_value("Skin", "") or ""),
            scale=optional_float(record.property_value("Scale", None)),
            visible=optional_bool(record.property_value("Visible", None)),
            solid=optional_bool(record.property_value("Solid", None)),
            move_to_floor=optional_bool(record.property_value("MoveToFloor", None)),
            property_count=len(record.properties),
        ))
    return tuple(result)


_BEHAVIOR_PROP_OBJECT_CLASSES = {
    "Barrel",
    "BonePile",
    "Brazier",
    "CandleProp",
    "Cauldron",
    "Cookpot",
    "DestructableProp",
    "Fire",
    "PropDamager",
    "StatStone",
    "TreasureChest",
    "WallTorch",
}


_BEHAVIOR_PROP_COPY_PASS_KEYS = {
    "Barrel": "include_low_risk_behavior_prop_objects",
    "BonePile": "include_low_risk_behavior_prop_objects",
    "Cauldron": "include_low_risk_behavior_prop_objects",
    "Cookpot": "include_low_risk_behavior_prop_objects",
    "StatStone": "include_low_risk_behavior_prop_objects",
    "WallTorch": "include_wall_torch_objects",
    "Fire": "include_fire_objects",
    "CandleProp": "include_candle_prop_objects",
    "Brazier": "include_brazier_objects",
    "TreasureChest": "include_treasure_chest_objects",
    "PropDamager": "include_prop_damager_objects",
    "DestructableProp": "include_destructable_prop_objects",
}


def _behavior_prop_copy_pass_key(class_name: str) -> str:
    return str(_BEHAVIOR_PROP_COPY_PASS_KEYS.get(str(class_name), ""))


def _behavior_prop_validation_status(
    risk_level_counts: Dict[str, int],
    *,
    class_name: str = "",
    copy_pass_status: str,
) -> str:
    if copy_pass_status != "explicit_copy_pass_available":
        return "needs_class_specific_copy_pass"
    if int(risk_level_counts.get("high", 0)) > 0:
        if str(class_name) == "TreasureChest":
            return "high_risk_loot_default_after_initial_manual_validation"
        if str(class_name) == "PropDamager":
            return "high_risk_damage_default_after_initial_manual_validation"
        if str(class_name) == "DestructableProp":
            return "high_risk_destructible_default_after_initial_manual_validation"
        return "needs_high_risk_manual_validation"
    if int(risk_level_counts.get("medium", 0)) > 0:
        if str(class_name) in {"WallTorch", "Fire", "CandleProp", "Brazier"}:
            return "medium_light_default_after_initial_manual_validation"
        return "needs_medium_risk_manual_validation"
    return "low_risk_default_after_initial_manual_validation"


def _behavior_prop_object_oracles(source_ed_path: str) -> Tuple[BehaviorPropObjectOracle, ...]:
    from features.dat_editing import legacy_ed

    def optional_float(value: object) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    def optional_bool(value: object) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        return None

    scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed_path)
    result: List[BehaviorPropObjectOracle] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name not in _BEHAVIOR_PROP_OBJECT_CLASSES:
            continue
        name = str(record.property_value("Name", "") or f"{class_name}{len(result)}")
        semantic_roles = _behavior_prop_semantic_roles(record)
        result.append(BehaviorPropObjectOracle(
            name=name,
            class_name=class_name,
            pos=_safe_vec3(record.property_value("Pos", (0.0, 0.0, 0.0))),
            filename=str(record.property_value("Filename", "") or ""),
            skin=str(record.property_value("Skin", "") or ""),
            scale=optional_float(record.property_value("Scale", None)),
            visible=optional_bool(record.property_value("Visible", None)),
            solid=optional_bool(record.property_value("Solid", None)),
            move_to_floor=optional_bool(record.property_value("MoveToFloor", None)),
            semantic_roles=semantic_roles,
            risk_level=_behavior_prop_risk_level(semantic_roles),
            on=optional_bool(record.property_value("On", None)),
            fire=optional_bool(record.property_value("Fire", None)),
            sound_file=str(record.property_value("SoundFile", "") or ""),
            sound_radius=optional_float(record.property_value("SoundRadius", None)),
            light_min_radius=optional_float(record.property_value("LightMinRadius", None)),
            light_max_radius=optional_float(record.property_value("LightMaxRadius", None)),
            locked=optional_bool(record.property_value("Locked", None)),
            trigger_target=str(record.property_value("TriggerTarget", "") or ""),
            damage_trigger_target=str(record.property_value("DamageTriggerTarget", "") or ""),
            hit_points=optional_float(record.property_value("HitPoints", None)),
            property_count=len(record.properties),
        ))
    return tuple(result)


def _behavior_prop_semantic_roles(record: object) -> Tuple[str, ...]:
    class_name = str(getattr(record, "class_name", "") or "")

    def value(name: str, default: object = "") -> object:
        try:
            return record.property_value(name, default)  # type: ignore[attr-defined]
        except Exception:
            return default

    roles: List[str] = []
    if str(value("Filename", "") or "") or str(value("Skin", "") or ""):
        roles.append("model_prop")
    if class_name in {"CandleProp", "WallTorch", "Fire", "Brazier"}:
        roles.append("light_fire_sound")
    if (
        value("On", None) is not None
        or value("Fire", None) is not None
        or str(value("SoundFile", "") or "").strip()
        or value("LightMinRadius", None) is not None
        or value("LightMaxRadius", None) is not None
        or value("FlameProps", None) is not None
        or value("SmokeProps", None) is not None
    ):
        roles.append("light_fire_sound")
    if class_name == "TreasureChest" or str(value("OpenSoundName", "") or "").strip() or str(value("CloseSoundName", "") or "").strip():
        roles.append("loot_interaction")
    if str(value("TriggerTarget", "") or "").strip():
        roles.append("trigger_reference")
    if class_name == "DestructableProp" or value("HitPoints", None) is not None or str(value("DamageTriggerTarget", "") or "").strip():
        roles.append("destructible")
    if class_name == "PropDamager" or value("DamagerStuff", None) is not None:
        roles.append("damage")
    if not any(role in roles for role in ("light_fire_sound", "loot_interaction", "trigger_reference", "destructible", "damage")):
        roles.append("physical_decor")
    return tuple(_unique_text(roles))


def _behavior_prop_risk_level(semantic_roles: Sequence[str]) -> str:
    roles = set(str(role) for role in semantic_roles)
    if roles.intersection({"loot_interaction", "trigger_reference", "destructible", "damage"}):
        return "high"
    if "light_fire_sound" in roles:
        return "medium"
    return "low"


def _nearest_airail_object(
    center: Tuple[float, float, float],
    objects: Sequence[AirailOracleObject],
    *,
    ambiguous_distance_epsilon: float,
) -> Tuple[Optional[AirailOracleObject], Optional[float], bool]:
    ranked = sorted(
        (
            (terrain_reconstruction.vec3_distance(center, item.pos), item)
            for item in objects
        ),
        key=lambda item: (item[0], item[1].name.lower()),
    )
    if not ranked:
        return None, None, False
    ambiguous = len(ranked) > 1 and ranked[1][0] - ranked[0][0] <= max(0.0, float(ambiguous_distance_epsilon))
    return ranked[0][1], ranked[0][0], ambiguous


def _nearest_airail_rail_brush(
    center: Tuple[float, float, float],
    brushes: Sequence[AirailRailBrushOracle],
    *,
    ambiguous_distance_epsilon: float,
) -> Tuple[Optional[AirailRailBrushOracle], Optional[float], bool]:
    ranked = sorted(
        (
            (terrain_reconstruction.vec3_distance(center, item.center), item)
            for item in brushes
        ),
        key=lambda item: (item[0], item[1].name.lower()),
    )
    if not ranked:
        return None, None, False
    ambiguous = len(ranked) > 1 and ranked[1][0] - ranked[0][0] <= max(0.0, float(ambiguous_distance_epsilon))
    return ranked[0][1], ranked[0][0], ambiguous


def _dat_collision_helper_dat_objects(
    source_dat_path: str,
    *,
    candidate_names: Sequence[str],
) -> Tuple[CollisionHelperOracleObject, ...]:
    wanted = {str(name or "").lower() for name in candidate_names if str(name or "")}
    if not wanted:
        return ()
    try:
        from mm9_patcher import mm9_patch as patcher

        with open(source_dat_path, "rb") as handle:
            data = handle.read()
        header = patcher.Header.parse(data)
        objects, _object_end = patcher.parse_objects(data, header.obj_pos)
    except Exception:
        return ()
    result: List[CollisionHelperOracleObject] = []
    for obj in objects:
        class_name = str(getattr(obj, "type_str", "") or "")
        if class_name not in {"InvisibleBrush", "PerceptionBrush", "Ladder", "WorldObject"}:
            continue
        properties = _dat_native_object_properties(obj)
        name = str(properties.get("Name", "") or "")
        if not name or name.lower() not in wanted:
            continue
        lower = name.lower()
        target = (
            "InvisibleBrush" if lower.startswith("invisiblebrush")
            else "PerceptionBrush" if lower.startswith("perceptionbrush")
            else "WorldObject" if lower.startswith("ladderblocker")
            else "Ladder" if lower.startswith("ladder")
            else ""
        )
        if target and class_name != target:
            continue
        result.append(CollisionHelperOracleObject(
            name=name,
            class_name=class_name,
            pos=_safe_vec3(properties.get("Pos", (0.0, 0.0, 0.0))),
            property_count=len(getattr(obj, "props", ()) or ()),
        ))
    return tuple(result)


def _dat_trigger_helper_dat_objects(
    source_dat_path: str,
    *,
    candidate_names: Sequence[str],
) -> Tuple[TriggerHelperOracleObject, ...]:
    wanted = {str(name or "").lower() for name in candidate_names if str(name or "")}
    if not wanted:
        return ()
    try:
        from mm9_patcher import mm9_patch as patcher

        with open(source_dat_path, "rb") as handle:
            data = handle.read()
        header = patcher.Header.parse(data)
        objects, _object_end = patcher.parse_objects(data, header.obj_pos)
    except Exception:
        return ()
    result: List[TriggerHelperOracleObject] = []
    for obj in objects:
        if str(getattr(obj, "type_str", "") or "") != "PortalZone":
            continue
        properties = _dat_native_object_properties(obj)
        name = str(properties.get("Name", "") or "")
        if not name or name.lower() not in wanted:
            continue
        result.append(TriggerHelperOracleObject(
            name=name,
            class_name="PortalZone",
            pos=_safe_vec3(properties.get("Pos", (0.0, 0.0, 0.0))),
            portal_name=str(properties.get("PortalName", "") or ""),
            property_count=len(getattr(obj, "props", ()) or ()),
        ))
    return tuple(result)


def _collision_helper_oracle_objects(
    source_ed_path: str,
    *,
    candidate_names: Sequence[str],
) -> Tuple[CollisionHelperOracleObject, ...]:
    from features.dat_editing import legacy_ed

    wanted = {str(name or "").lower() for name in candidate_names if str(name or "")}
    scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed_path)
    result: List[CollisionHelperOracleObject] = []
    for record in scan.records:
        name = str(record.property_value("Name", "") or "")
        if not name or name.lower() not in wanted:
            continue
        result.append(CollisionHelperOracleObject(
            name=name,
            class_name=str(record.class_name),
            pos=_safe_vec3(record.property_value("Pos", (0.0, 0.0, 0.0))),
            property_count=len(record.properties),
        ))
    return tuple(result)


def _collision_helper_brush_oracles(source_ed_path: str) -> Tuple[CollisionHelperBrushOracle, ...]:
    from features.dat_editing import legacy_ed

    scene = legacy_ed.load_legacy_ed_geometry_scene(source_ed_path)
    scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed_path)
    brush_records = [record for record in scan.records if record.class_name == "Brush"]
    result: List[CollisionHelperBrushOracle] = []
    for model_index, model in enumerate(scene.models):
        helper_roles: Dict[str, int] = {}
        helper_indices = set()
        for face in getattr(model, "faces", ()) or ():
            role = terrain_semantics.helper_texture_role(getattr(face, "material_name", ""))
            if role not in {"collision", "sprite"}:
                continue
            helper_roles[role] = helper_roles.get(role, 0) + 1
            helper_indices.update(int(index) for index in getattr(face, "vertex_indices", ()) or ())
        if int(helper_roles.get("collision", 0)) <= 0:
            continue
        points = [
            _safe_vec3(model.points[index])
            for index in helper_indices
            if 0 <= index < len(getattr(model, "points", ()) or ())
        ]
        if not points:
            continue
        bounds_min, bounds_max, center = _points_bounds_center(points)
        name = ""
        if model_index < len(brush_records):
            name = str(brush_records[model_index].property_value("Name", "") or "")
        result.append(CollisionHelperBrushOracle(
            name=name or str(getattr(model, "name", "") or f"CollisionHelperBrush{len(result)}"),
            bounds_min=bounds_min,
            bounds_max=bounds_max,
            center=center,
            helper_roles=dict(helper_roles),
        ))
    return tuple(result)


def _collision_helper_reconstruction_candidate(
    model_index: int,
    summary: PrefabSurrogateCompositeModelSummary,
    *,
    helper_roles: Dict[str, int],
    source_objects_by_name: Dict[str, CollisionHelperOracleObject],
    source_helper_brushes: Sequence[CollisionHelperBrushOracle],
) -> CollisionHelperReconstructionCandidate:
    target_class_name = _collision_helper_target_class_name(summary.name)
    matched = source_objects_by_name.get(summary.name.lower())
    nearest_brush, brush_distance = _nearest_collision_helper_brush(
        summary.center,
        source_helper_brushes,
    )
    notes: List[str] = []
    if not source_objects_by_name:
        status = "pending_source_oracle"
        notes.append("source ED collision helper object oracle is not available")
    elif matched is None:
        status = "unmatched_source_collision_helper"
        notes.append("no same-name source ED collision helper object was found")
    else:
        status = "matched_source_collision_helper"
        if target_class_name and matched.class_name != target_class_name:
            notes.append(
                f"same-name source object class {matched.class_name} differs from inferred target {target_class_name}"
            )

    return CollisionHelperReconstructionCandidate(
        source_model_name=summary.name,
        source_model_index=int(model_index),
        target_class_name=target_class_name,
        helper_roles=dict(helper_roles),
        polygon_count=summary.polygon_count,
        bounds_min=summary.bounds_min,
        bounds_max=summary.bounds_max,
        center=summary.center,
        matched_object_name=matched.name if matched else "",
        matched_object_class_name=matched.class_name if matched else "",
        matched_object_distance=(
            terrain_reconstruction.vec3_distance(summary.center, matched.pos)
            if matched is not None else None
        ),
        nearest_helper_brush_name=nearest_brush.name if nearest_brush else "",
        nearest_helper_brush_distance=brush_distance,
        status=status,
        notes=tuple(notes),
    )


def _trigger_helper_oracle_objects(
    source_ed_path: str,
    *,
    candidate_names: Sequence[str],
) -> Tuple[TriggerHelperOracleObject, ...]:
    from features.dat_editing import legacy_ed

    wanted = {str(name or "").lower() for name in candidate_names if str(name or "")}
    scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed_path)
    result: List[TriggerHelperOracleObject] = []
    for record in scan.records:
        if str(record.class_name) != "PortalZone":
            continue
        name = str(record.property_value("Name", "") or "")
        if not name or name.lower() not in wanted:
            continue
        result.append(TriggerHelperOracleObject(
            name=name,
            class_name=str(record.class_name),
            pos=_safe_vec3(record.property_value("Pos", (0.0, 0.0, 0.0))),
            portal_name=str(record.property_value("PortalName", "") or ""),
            property_count=len(record.properties),
        ))
    return tuple(result)


def _trigger_helper_brush_oracles(source_ed_path: str) -> Tuple[TriggerHelperBrushOracle, ...]:
    from features.dat_editing import legacy_ed

    scene = legacy_ed.load_legacy_ed_geometry_scene(source_ed_path)
    scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed_path)
    brush_records = [record for record in scan.records if record.class_name == "Brush"]
    result: List[TriggerHelperBrushOracle] = []
    for model_index, model in enumerate(scene.models):
        trigger_indices = set()
        trigger_face_count = 0
        for face in getattr(model, "faces", ()) or ():
            role = terrain_semantics.helper_texture_role(getattr(face, "material_name", ""))
            if role != "trigger":
                continue
            trigger_face_count += 1
            trigger_indices.update(int(index) for index in getattr(face, "vertex_indices", ()) or ())
        if trigger_face_count <= 0:
            continue
        points = [
            _safe_vec3(model.points[index])
            for index in trigger_indices
            if 0 <= index < len(getattr(model, "points", ()) or ())
        ]
        if not points:
            continue
        bounds_min, bounds_max, center = _points_bounds_center(points)
        name = ""
        if model_index < len(brush_records):
            name = str(brush_records[model_index].property_value("Name", "") or "")
        result.append(TriggerHelperBrushOracle(
            name=name or str(getattr(model, "name", "") or f"TriggerHelperBrush{len(result)}"),
            bounds_min=bounds_min,
            bounds_max=bounds_max,
            center=center,
            trigger_face_count=trigger_face_count,
        ))
    return tuple(result)


def _trigger_helper_reconstruction_candidate(
    model_index: int,
    summary: PrefabSurrogateCompositeModelSummary,
    *,
    helper_roles: Dict[str, int],
    source_objects_by_name: Dict[str, TriggerHelperOracleObject],
    source_helper_brushes: Sequence[TriggerHelperBrushOracle],
) -> TriggerHelperReconstructionCandidate:
    matched = source_objects_by_name.get(summary.name.lower())
    nearest_brush, brush_distance = _nearest_trigger_helper_brush(
        summary.center,
        source_helper_brushes,
    )
    notes: List[str] = []
    if not source_objects_by_name:
        status = "pending_source_oracle"
        notes.append("source ED PortalZone object oracle is not available")
    elif matched is None:
        status = "unmatched_source_trigger_helper"
        notes.append("no same-name source ED PortalZone object was found")
    else:
        status = "matched_source_trigger_helper"
        if matched.class_name != "PortalZone":
            notes.append(
                f"same-name source object class {matched.class_name} differs from inferred PortalZone target"
            )
        if not matched.portal_name:
            notes.append("matched PortalZone object has an empty PortalName property")

    return TriggerHelperReconstructionCandidate(
        source_model_name=summary.name,
        source_model_index=int(model_index),
        helper_roles=dict(helper_roles),
        polygon_count=summary.polygon_count,
        bounds_min=summary.bounds_min,
        bounds_max=summary.bounds_max,
        center=summary.center,
        matched_object_name=matched.name if matched else "",
        matched_object_class_name=matched.class_name if matched else "",
        matched_object_portal_name=matched.portal_name if matched else "",
        matched_object_distance=(
            terrain_reconstruction.vec3_distance(summary.center, matched.pos)
            if matched is not None else None
        ),
        nearest_helper_brush_name=nearest_brush.name if nearest_brush else "",
        nearest_helper_brush_distance=brush_distance,
        status=status,
        notes=tuple(notes),
    )


def _collision_helper_target_class_name(name: str) -> str:
    lower = str(name or "").lower()
    if lower.startswith("invisiblebrush"):
        return "InvisibleBrush"
    if lower.startswith("perceptionbrush"):
        return "PerceptionBrush"
    if lower.startswith("ladderblocker"):
        return "WorldObject"
    if lower.startswith("ladder"):
        return "Ladder"
    return ""


def _is_pure_collision_helper_semantic_model(model: object) -> bool:
    helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
    return (
        int(helper_roles.get("collision", 0)) > 0
        and set(helper_roles.keys()).issubset({"collision", "sprite"})
        and terrain_semantics.model_has_only_helper_textures(model)
    )


def _is_pure_trigger_helper_semantic_model(model: object) -> bool:
    helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
    return (
        int(helper_roles.get("trigger", 0)) > 0
        and set(helper_roles.keys()) == {"trigger"}
        and terrain_semantics.model_has_only_helper_textures(model)
    )


def _nearest_collision_helper_brush(
    center: Tuple[float, float, float],
    brushes: Sequence[CollisionHelperBrushOracle],
) -> Tuple[Optional[CollisionHelperBrushOracle], Optional[float]]:
    ranked = sorted(
        (
            (terrain_reconstruction.vec3_distance(center, item.center), item)
            for item in brushes
        ),
        key=lambda item: (item[0], item[1].name),
    )
    if not ranked:
        return None, None
    return ranked[0][1], ranked[0][0]


def _nearest_trigger_helper_brush(
    center: Tuple[float, float, float],
    brushes: Sequence[TriggerHelperBrushOracle],
) -> Tuple[Optional[TriggerHelperBrushOracle], Optional[float]]:
    ranked = sorted(
        (
            (terrain_reconstruction.vec3_distance(center, item.center), item)
            for item in brushes
        ),
        key=lambda item: (item[0], item[1].name),
    )
    if not ranked:
        return None, None
    return ranked[0][1], ranked[0][0]


def _points_bounds_center(
    points: Sequence[Tuple[float, float, float]],
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]:
    mins = tuple(min(point[index] for point in points) for index in range(3))
    maxs = tuple(max(point[index] for point in points) for index in range(3))
    center = tuple((mins[index] + maxs[index]) * 0.5 for index in range(3))
    return mins, maxs, center  # type: ignore[return-value]


def _polygon_center(
    points: Sequence[Tuple[float, float, float]],
) -> Tuple[float, float, float]:
    if not points:
        return (0.0, 0.0, 0.0)
    return tuple(
        sum(float(point[index]) for point in points) / float(len(points))
        for index in range(3)
    )  # type: ignore[return-value]


def _polygon_normal(
    points: Sequence[Tuple[float, float, float]],
) -> Tuple[float, float, float]:
    if len(points) < 3:
        return (0.0, 0.0, 0.0)
    origin = points[0]
    for index in range(1, len(points) - 1):
        normal = _vec3_cross(
            _vec3_subtract(points[index], origin),
            _vec3_subtract(points[index + 1], origin),
        )
        if _vec3_length(normal) > 0.000001:
            return _normalized_vec3(normal)
    return (0.0, 0.0, 0.0)


def _vec3_subtract(
    a: Tuple[float, float, float],
    b: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    return (float(a[0]) - float(b[0]), float(a[1]) - float(b[1]), float(a[2]) - float(b[2]))


def _vec3_dot(
    a: Tuple[float, float, float],
    b: Tuple[float, float, float],
) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _vec3_cross(
    a: Tuple[float, float, float],
    b: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    return (
        float(a[1]) * float(b[2]) - float(a[2]) * float(b[1]),
        float(a[2]) * float(b[0]) - float(a[0]) * float(b[2]),
        float(a[0]) * float(b[1]) - float(a[1]) * float(b[0]),
    )


def _vec3_length(value: Tuple[float, float, float]) -> float:
    return math.sqrt(_vec3_dot(value, value))


def _normalized_vec3(value: Tuple[float, float, float]) -> Tuple[float, float, float]:
    length = _vec3_length(value)
    if length <= 0.0:
        return (0.0, 0.0, 0.0)
    return (float(value[0]) / length, float(value[1]) / length, float(value[2]) / length)


def _axis_orientation_label(normal: Tuple[float, float, float]) -> str:
    axis_index = max(range(3), key=lambda index: abs(float(normal[index])))
    axis_name = ("X", "Y", "Z")[axis_index]
    sign = "+" if float(normal[axis_index]) >= 0.0 else "-"
    return sign + axis_name


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
    focus_points: Sequence[object] = (),
    focus_radius: float = 0.0,
    focus_budget: int = 0,
    focus_seed_radius: float = 0.0,
    door_clearance_bounds: Sequence[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = (),
    analysis_cache: Optional[Dict[str, object]] = None,
    cache_final_balanced_groups: bool = False,
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
    candidates = terrain_reconstruction.physics_shell_candidates(model)
    consolidation_index = terrain_reconstruction.build_physics_shell_consolidation_index(
        model,
        candidates,
    )
    if analysis_cache is not None:
        analysis_cache["model"] = model
        analysis_cache["candidates"] = candidates
        analysis_cache["consolidation_index"] = consolidation_index

    if focus_points or door_clearance_bounds:
        candidate_count = min(requested, len(candidates))
        generated_count = _predicted_focused_physics_shell_generated_face_count(
            model,
            candidates,
            source_polygon_limit=candidate_count,
            focus_points=focus_points,
            focus_radius=focus_radius,
            focus_budget=focus_budget,
            focus_seed_radius=focus_seed_radius,
            door_clearance_bounds=door_clearance_bounds,
            consolidation_index=consolidation_index,
        )
        if generated_count <= budget:
            return candidate_count

        # Consolidation makes generated-face cost non-uniform, but it remains
        # close enough to proportional over the large UI shell budget.  Keep a
        # one-percent reserve for group-boundary variation and ED scaffolding;
        # repeatedly rebuilding all consolidation groups here made editor
        # preflight take more than a minute on ANSKRAMKEEP.
        reserved_budget = max(0, (budget * 99) // 100)
        return max(
            0,
            min(candidate_count, (candidate_count * reserved_budget) // generated_count),
        )

    return terrain_reconstruction.budgeted_balanced_physics_shell_source_polygon_count(
        candidates,
        requested_source_polygon_count=requested,
        generated_polygon_budget=budget,
        model=model,
        consolidation_index=consolidation_index,
        analysis_cache=analysis_cache if cache_final_balanced_groups else None,
    )


def _predicted_focused_physics_shell_generated_face_count(
    model: object,
    candidates: Sequence[terrain_reconstruction.PhysicsShellCandidate],
    *,
    source_polygon_limit: int,
    focus_points: Sequence[object] = (),
    focus_radius: float = 0.0,
    focus_budget: int = 0,
    focus_seed_radius: float = 0.0,
    door_clearance_bounds: Sequence[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = (),
    consolidation_index: Optional[terrain_reconstruction.PhysicsShellConsolidationIndex] = None,
) -> int:
    from features.dat_editing import surrogate_ed

    limit = max(0, int(source_polygon_limit))
    if limit <= 0:
        return 0
    focus_selection = terrain_reconstruction.focused_balanced_physics_shell_candidates(
        candidates,
        limit,
        focus_points=focus_points,
        focus_radius=focus_radius,
        focus_budget=focus_budget,
        focus_seed_radius=focus_seed_radius,
    )
    attempted_indices = set()
    selected_source_count = 0
    generated_face_count = 0
    index = consolidation_index or terrain_reconstruction.build_physics_shell_consolidation_index(
        model,
        candidates,
    )

    def add_groups(ordered_candidates: Sequence[terrain_reconstruction.PhysicsShellCandidate]) -> None:
        nonlocal selected_source_count, generated_face_count
        groups = terrain_reconstruction.consolidated_physics_shell_candidate_groups(
            model,
            ordered_candidates,
            consolidation_index=index,
        )
        for group in groups:
            if selected_source_count + len(group.candidates) > limit:
                break
            attempted_indices.update(item.polygon_index for item in group.candidates)
            first = group.candidates[0]
            if (
                first.role == "side_wall"
                and surrogate_ed._physics_shell_group_intersects_clearance_bounds(
                    group.points,
                    door_clearance_bounds,
                )
            ):
                continue
            selected_source_count += len(group.candidates)
            generated_face_count += int(group.generated_face_count)

    add_groups(focus_selection.selected)
    if selected_source_count < limit:
        fallback = terrain_reconstruction.balanced_physics_shell_candidates(
            tuple(
                candidate
                for candidate in candidates
                if candidate.polygon_index not in attempted_indices
            ),
            len(candidates),
        )
        add_groups(fallback)
    return generated_face_count


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


def _optional_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _optional_int_count_key(value: Optional[int]) -> str:
    return "none" if value is None else str(int(value))


def _median_float(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) * 0.5


def _is_vec3(value: object) -> bool:
    try:
        x, y, z = value  # type: ignore[misc]
        return all(math.isfinite(float(item)) for item in (x, y, z))
    except Exception:
        return False


def _vec3_text(value: Tuple[float, float, float]) -> str:
    return "(" + ", ".join(f"{float(item):.2f}" for item in value) + ")"


def _optional_float_text(value: Optional[float], *, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{int(digits)}f}"


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
