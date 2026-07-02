"""Experimental DAT-to-legacy-ED surrogate reconstruction.

This module is deliberately modest.  It writes a raw LithTech 2.1 legacy ED
brush stream from selected compiled DAT world models, then validates that our
legacy ED reader can recover the same brush/polygon counts.  The full-world
skeleton path adds the minimum old-DEdit shell we have validated so far:
root/group/brush nodes plus basic load scaffolding.  The output is still a
source-like research artifact, not a complete reconstruction of the original
object graph, portals, visibility metadata, or CSG brush semantics.
"""

from __future__ import annotations

import math
import os
import struct
import zlib
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from features.dat_editing import (
    legacy_ed,
    legacy_ed_writer,
    terrain_reconstruction,
    terrain_semantics,
)


Vec3 = Tuple[float, float, float]

_FULL_LEVEL_ZLIB_BLOCK_SIZE = 50000
_DEFAULT_FULL_LEVEL_INFOSTRING = (
    "AmbientLight 80 80 80 ; PBlockSize 2048 ; LMGridSize 64; MaxLMSize 32"
)

_LEGACY_BRUSH_OBJECT_PROPERTIES = legacy_ed_writer.MM9_BRUSH_OBJECT_PROPERTIES


@dataclass(frozen=True)
class SurrogateEdModelSummary:
    name: str
    status: str
    point_count: int = 0
    polygon_count: int = 0
    skipped_polygon_count: int = 0
    texture_count: int = 0
    byte_count: int = 0
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SurrogateEdBuildReport:
    status: str
    source_dat_path: str = ""
    output_path: str = ""
    selected_model_names: Tuple[str, ...] = ()
    generated_byte_count: int = 0
    decompressed_byte_count: int = 0
    wrapper_kind: str = ""
    wrapper_block_count: int = 0
    node_hierarchy_byte_count: int = 0
    model_count: int = 0
    object_count: int = 0
    object_property_count: int = 0
    point_count: int = 0
    polygon_count: int = 0
    skipped_polygon_count: int = 0
    roundtrip_model_count: int = 0
    roundtrip_polygon_count: int = 0
    processor_readiness: str = "raw_brush_stream_only"
    model_summaries: Tuple[SurrogateEdModelSummary, ...] = ()
    blockers: Tuple[str, ...] = ()
    cautions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class _FullWorldSkeletonObjectPositions:
    world_properties: Vec3
    start_point: Vec3
    light: Vec3


@dataclass(frozen=True)
class _ValidationFloorPlacement:
    center: Vec3
    top_y: float


def build_surrogate_legacy_ed_bytes_from_dat_bytes(
    data: bytes,
    *,
    source_path: str = "",
    model_names: Sequence[str] = (),
    max_models: Optional[int] = None,
    include_skyboxes: bool = False,
) -> Tuple[bytes, SurrogateEdBuildReport]:
    """Return raw legacy ED bytes reconstructed from selected DAT world models."""
    absolute, selected, error_report = _parse_selected_models_from_dat_bytes(
        data,
        source_path=source_path,
        model_names=model_names,
        max_models=max_models,
        include_skyboxes=include_skyboxes,
    )
    if error_report is not None:
        return b"", error_report
    generated, report, _brushes = _build_raw_surrogate_from_selected(
        selected,
        source_path=absolute,
    )
    return generated, report


def write_surrogate_legacy_ed_from_dat(
    dat_path: str,
    output_path: str,
    *,
    model_names: Sequence[str] = (),
    max_models: Optional[int] = None,
    include_skyboxes: bool = False,
) -> SurrogateEdBuildReport:
    """Write a raw surrogate legacy ED stream for selected DAT models."""
    absolute_dat = os.path.abspath(dat_path)
    absolute_output = os.path.abspath(output_path)
    try:
        with open(absolute_dat, "rb") as f:
            data = f.read()
    except OSError as exc:
        return SurrogateEdBuildReport(
            status="dat_read_failed",
            source_dat_path=absolute_dat,
            output_path=absolute_output,
            blockers=(str(exc),),
        )

    generated, report = build_surrogate_legacy_ed_bytes_from_dat_bytes(
        data,
        source_path=absolute_dat,
        model_names=model_names,
        max_models=max_models,
        include_skyboxes=include_skyboxes,
    )
    if report.status not in {"raw_surrogate_ed_built"}:
        return SurrogateEdBuildReport(
            status=report.status,
            source_dat_path=report.source_dat_path,
            output_path=absolute_output,
            selected_model_names=report.selected_model_names,
            generated_byte_count=report.generated_byte_count,
            model_count=report.model_count,
            point_count=report.point_count,
            polygon_count=report.polygon_count,
            skipped_polygon_count=report.skipped_polygon_count,
            roundtrip_model_count=report.roundtrip_model_count,
            roundtrip_polygon_count=report.roundtrip_polygon_count,
            processor_readiness=report.processor_readiness,
            model_summaries=report.model_summaries,
            blockers=report.blockers,
            cautions=report.cautions,
            notes=report.notes,
        )
    os.makedirs(os.path.dirname(absolute_output) or ".", exist_ok=True)
    with open(absolute_output, "wb") as f:
        f.write(generated)
    return SurrogateEdBuildReport(
        status=report.status,
        source_dat_path=report.source_dat_path,
        output_path=absolute_output,
        selected_model_names=report.selected_model_names,
        generated_byte_count=report.generated_byte_count,
        model_count=report.model_count,
        point_count=report.point_count,
        polygon_count=report.polygon_count,
        skipped_polygon_count=report.skipped_polygon_count,
        roundtrip_model_count=report.roundtrip_model_count,
        roundtrip_polygon_count=report.roundtrip_polygon_count,
        processor_readiness=report.processor_readiness,
        model_summaries=report.model_summaries,
        blockers=report.blockers,
        cautions=report.cautions,
        notes=report.notes,
    )


def build_prefab_surrogate_legacy_ed_bytes_from_dat_bytes(
    data: bytes,
    *,
    source_path: str = "",
    model_names: Sequence[str] = (),
    max_models: Optional[int] = None,
    include_skyboxes: bool = False,
    brush_name_prefix: str = "Brush",
) -> Tuple[bytes, SurrogateEdBuildReport]:
    """Return a prefab-style legacy ED with brush objects for selected DAT models.

    This is a narrower artifact than the full-level wrapper.  It follows the
    observed MM9-era prefab shape: legacy version header, zeroed prefab header
    bytes, brush count, raw brush geometry, and DEDit `Brush` object property
    records patterned after real `C:/lithtech/PreFabs` samples.
    """
    absolute, selected, error_report = _parse_selected_models_from_dat_bytes(
        data,
        source_path=source_path,
        model_names=model_names,
        max_models=max_models,
        include_skyboxes=include_skyboxes,
    )
    if error_report is not None:
        return b"", error_report
    _raw_bytes, raw_report, brushes = _build_raw_surrogate_from_selected(
        selected,
        source_path=absolute,
    )
    if raw_report.status != "raw_surrogate_ed_built":
        return b"", raw_report

    brush_count = raw_report.model_count
    object_property_count = brush_count * len(_LEGACY_BRUSH_OBJECT_PROPERTIES)
    generated = legacy_ed_writer.build_direct_root_prefab(
        brushes,
        brush_names=[f"{brush_name_prefix}{index}" for index in range(brush_count)],
    )

    status = "prefab_surrogate_ed_built"
    blockers: List[str] = []
    roundtrip_model_count = 0
    roundtrip_polygon_count = 0
    object_count = 0
    try:
        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path=os.path.abspath(source_path) if source_path else "surrogate_prefab.ed",
        )
        roundtrip_model_count = int(scene.metadata.get("recovered_brush_count", 0) or 0)
        roundtrip_polygon_count = int(scene.metadata.get("recovered_polygon_count", 0) or 0)
        object_count = int(scene.metadata.get("recovered_object_count", 0) or 0)
    except Exception as exc:
        status = "prefab_roundtrip_parse_failed"
        blockers.append(f"generated prefab-style legacy ED did not round-trip through reader: {exc}")

    if status == "prefab_surrogate_ed_built" and (
        roundtrip_model_count != raw_report.model_count
        or roundtrip_polygon_count != raw_report.polygon_count
        or object_count != brush_count
    ):
        status = "prefab_roundtrip_count_mismatch"
        blockers.append(
            f"prefab round-trip recovered {roundtrip_model_count}/{roundtrip_polygon_count}/"
            f"{object_count} brushes/polygons/objects, expected "
            f"{raw_report.model_count}/{raw_report.polygon_count}/{brush_count}"
        )

    cautions = (
        "legacy prefab-style ED with Brush object records, not a full DEdit world",
        "lights, portals, root world hierarchy, gameplay object graph, and compiler metadata are not reconstructed",
        "compiled DAT polygons are BSP output, not original authoring CSG brushes",
    )
    notes = tuple(raw_report.notes) + (
        "Brush object properties are patterned after real MM9 PreFabs Brush records.",
    )
    return generated, replace(
        raw_report,
        status=status,
        generated_byte_count=len(generated),
        object_count=object_count,
        object_property_count=object_property_count if object_count == brush_count else 0,
        roundtrip_model_count=roundtrip_model_count,
        roundtrip_polygon_count=roundtrip_polygon_count,
        processor_readiness="legacy_prefab_object_stream_surrogate",
        blockers=tuple(_unique_text(tuple(raw_report.blockers) + tuple(blockers))),
        cautions=cautions,
        notes=notes,
    )


def build_grouped_prefab_surrogate_legacy_ed_bytes_from_dat_bytes(
    data: bytes,
    *,
    source_path: str = "",
    model_names: Sequence[str] = (),
    max_models: Optional[int] = None,
    include_skyboxes: bool = False,
    brush_name_prefix: str = "Brush",
    group_name: str = "Group",
) -> Tuple[bytes, SurrogateEdBuildReport]:
    """Return a prefab-style legacy ED with a named null/group node.

    This is the explicit Stage 7R variant of the direct-root prefab writer.  It
    follows real multi-brush MM9 prefabs such as Furniture/Bench.ed: root has
    one null/group child, that group owns the generated Brush children, and the
    group label is serialized in the node-tree tail.
    """
    absolute, selected, error_report = _parse_selected_models_from_dat_bytes(
        data,
        source_path=source_path,
        model_names=model_names,
        max_models=max_models,
        include_skyboxes=include_skyboxes,
    )
    if error_report is not None:
        return b"", error_report
    _raw_bytes, raw_report, brushes = _build_raw_surrogate_from_selected(
        selected,
        source_path=absolute,
    )
    if raw_report.status != "raw_surrogate_ed_built":
        return b"", raw_report

    brush_count = raw_report.model_count
    object_property_count = brush_count * len(_LEGACY_BRUSH_OBJECT_PROPERTIES)
    label = str(group_name or "Group")
    generated = legacy_ed_writer.build_named_group_prefab(
        brushes,
        group_name=label,
        brush_names=[f"{brush_name_prefix}{index}" for index in range(brush_count)],
    )

    status = "grouped_prefab_surrogate_ed_built"
    blockers: List[str] = []
    roundtrip_model_count = 0
    roundtrip_polygon_count = 0
    object_count = 0
    try:
        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path=os.path.abspath(source_path) if source_path else "grouped_surrogate_prefab.ed",
        )
        roundtrip_model_count = int(scene.metadata.get("recovered_brush_count", 0) or 0)
        roundtrip_polygon_count = int(scene.metadata.get("recovered_polygon_count", 0) or 0)
        object_count = int(scene.metadata.get("recovered_object_count", 0) or 0)
    except Exception as exc:
        status = "grouped_prefab_roundtrip_parse_failed"
        blockers.append(f"generated grouped prefab-style legacy ED did not round-trip through reader: {exc}")

    if status == "grouped_prefab_surrogate_ed_built" and (
        roundtrip_model_count != raw_report.model_count
        or roundtrip_polygon_count != raw_report.polygon_count
        or object_count != brush_count
    ):
        status = "grouped_prefab_roundtrip_count_mismatch"
        blockers.append(
            f"grouped prefab round-trip recovered {roundtrip_model_count}/{roundtrip_polygon_count}/"
            f"{object_count} brushes/polygons/objects, expected "
            f"{raw_report.model_count}/{raw_report.polygon_count}/{brush_count}"
        )

    cautions = (
        "legacy grouped prefab-style ED with a named null/group node, not a full DEdit world",
        "lights, portals, root world hierarchy, gameplay object graph, and compiler metadata are not reconstructed",
        "compiled DAT polygons are BSP output, not original authoring CSG brushes",
    )
    notes = tuple(raw_report.notes) + (
        "Brush object properties are patterned after real MM9 PreFabs Brush records.",
        "Node hierarchy is patterned after real named null/group prefabs such as Furniture/Bench.ed.",
        f"Generated group node label: {label}",
    )
    return generated, replace(
        raw_report,
        status=status,
        generated_byte_count=len(generated),
        object_count=object_count,
        object_property_count=object_property_count if object_count == brush_count else 0,
        roundtrip_model_count=roundtrip_model_count,
        roundtrip_polygon_count=roundtrip_polygon_count,
        processor_readiness="legacy_grouped_prefab_object_stream_surrogate",
        blockers=tuple(_unique_text(tuple(raw_report.blockers) + tuple(blockers))),
        cautions=cautions,
        notes=notes,
    )


def write_prefab_surrogate_legacy_ed_from_dat(
    dat_path: str,
    output_path: str,
    *,
    model_names: Sequence[str] = (),
    max_models: Optional[int] = None,
    include_skyboxes: bool = False,
    brush_name_prefix: str = "Brush",
) -> SurrogateEdBuildReport:
    """Write a prefab-style surrogate legacy ED file for selected DAT models."""
    absolute_dat = os.path.abspath(dat_path)
    absolute_output = os.path.abspath(output_path)
    try:
        with open(absolute_dat, "rb") as f:
            data = f.read()
    except OSError as exc:
        return SurrogateEdBuildReport(
            status="dat_read_failed",
            source_dat_path=absolute_dat,
            output_path=absolute_output,
            blockers=(str(exc),),
        )

    generated, report = build_prefab_surrogate_legacy_ed_bytes_from_dat_bytes(
        data,
        source_path=absolute_dat,
        model_names=model_names,
        max_models=max_models,
        include_skyboxes=include_skyboxes,
        brush_name_prefix=brush_name_prefix,
    )
    if report.status != "prefab_surrogate_ed_built":
        return replace(report, output_path=absolute_output)
    os.makedirs(os.path.dirname(absolute_output) or ".", exist_ok=True)
    with open(absolute_output, "wb") as f:
        f.write(generated)
    return replace(report, output_path=absolute_output)


def write_grouped_prefab_surrogate_legacy_ed_from_dat(
    dat_path: str,
    output_path: str,
    *,
    model_names: Sequence[str] = (),
    max_models: Optional[int] = None,
    include_skyboxes: bool = False,
    brush_name_prefix: str = "Brush",
    group_name: str = "Group",
) -> SurrogateEdBuildReport:
    """Write a named-group prefab-style surrogate legacy ED file."""
    absolute_dat = os.path.abspath(dat_path)
    absolute_output = os.path.abspath(output_path)
    try:
        with open(absolute_dat, "rb") as f:
            data = f.read()
    except OSError as exc:
        return SurrogateEdBuildReport(
            status="dat_read_failed",
            source_dat_path=absolute_dat,
            output_path=absolute_output,
            blockers=(str(exc),),
        )

    generated, report = build_grouped_prefab_surrogate_legacy_ed_bytes_from_dat_bytes(
        data,
        source_path=absolute_dat,
        model_names=model_names,
        max_models=max_models,
        include_skyboxes=include_skyboxes,
        brush_name_prefix=brush_name_prefix,
        group_name=group_name,
    )
    if report.status != "grouped_prefab_surrogate_ed_built":
        return replace(report, output_path=absolute_output)
    os.makedirs(os.path.dirname(absolute_output) or ".", exist_ok=True)
    with open(absolute_output, "wb") as f:
        f.write(generated)
    return replace(report, output_path=absolute_output)


def build_full_level_surrogate_legacy_ed_bytes_from_dat_bytes(
    data: bytes,
    *,
    source_path: str = "",
    model_names: Sequence[str] = (),
    max_models: Optional[int] = None,
    include_skyboxes: bool = False,
    infostring: str = "",
    block_size: int = _FULL_LEVEL_ZLIB_BLOCK_SIZE,
) -> Tuple[bytes, SurrogateEdBuildReport]:
    """Return full-level wrapped legacy ED bytes for selected DAT world models.

    The inner stream is still the surrogate brush stream from compiled DAT
    polygons.  This function only adds the same zlib-blocked full-level wrapper
    shape used by shipped legacy `.ED` files.
    """
    raw_bytes, raw_report = build_surrogate_legacy_ed_bytes_from_dat_bytes(
        data,
        source_path=source_path,
        model_names=model_names,
        max_models=max_models,
        include_skyboxes=include_skyboxes,
    )
    if raw_report.status != "raw_surrogate_ed_built":
        return b"", raw_report

    try:
        generated, wrapper_metadata = wrap_raw_surrogate_legacy_ed_bytes(
            raw_bytes,
            brush_count=raw_report.model_count,
            infostring=infostring or _infer_full_level_infostring(data),
            block_size=block_size,
        )
    except ValueError as exc:
        blockers = tuple(_unique_text(tuple(raw_report.blockers) + (str(exc),)))
        return b"", replace(
            raw_report,
            status="full_level_wrapper_failed",
            generated_byte_count=0,
            processor_readiness="wrapper_generation_failed",
            blockers=blockers,
        )

    status = "full_level_surrogate_ed_built"
    blockers: List[str] = []
    roundtrip_model_count = 0
    roundtrip_polygon_count = 0
    try:
        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path=os.path.abspath(source_path) if source_path else "surrogate_full.ed",
        )
        roundtrip_model_count = int(scene.metadata.get("recovered_brush_count", 0) or 0)
        roundtrip_polygon_count = int(scene.metadata.get("recovered_polygon_count", 0) or 0)
    except Exception as exc:
        status = "full_level_roundtrip_parse_failed"
        blockers.append(f"generated full-level legacy ED wrapper did not round-trip through reader: {exc}")

    if status == "full_level_surrogate_ed_built" and (
        roundtrip_model_count != raw_report.model_count
        or roundtrip_polygon_count != raw_report.polygon_count
    ):
        status = "full_level_roundtrip_count_mismatch"
        blockers.append(
            f"full-level round-trip recovered {roundtrip_model_count}/{roundtrip_polygon_count} "
            f"brushes/polygons, expected {raw_report.model_count}/{raw_report.polygon_count}"
        )

    cautions = (
        "full-level zlib ED wrapper around a surrogate DAT brush stream only",
        "object graph, lights, portals, node hierarchy, and gameplay properties are not reconstructed",
        "compiled DAT polygons are BSP output, not original authoring CSG brushes",
    )
    notes = tuple(raw_report.notes) + (
        "wrapper block tables match the observed LithTech 2.1 full-level ED table rotation",
    )
    return generated, replace(
        raw_report,
        status=status,
        generated_byte_count=len(generated),
        decompressed_byte_count=int(wrapper_metadata["decompressed_byte_count"]),
        wrapper_kind="zlib_blocked_full_level",
        wrapper_block_count=int(wrapper_metadata["block_count"]),
        roundtrip_model_count=roundtrip_model_count,
        roundtrip_polygon_count=roundtrip_polygon_count,
        processor_readiness="full_level_wrapper_surrogate",
        blockers=tuple(_unique_text(tuple(raw_report.blockers) + tuple(blockers))),
        cautions=cautions,
        notes=notes,
    )


def build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
    data: bytes,
    *,
    source_path: str = "",
    model_names: Sequence[str] = (),
    max_models: Optional[int] = None,
    include_skyboxes: bool = False,
    infostring: Optional[str] = None,
    block_size: int = _FULL_LEVEL_ZLIB_BLOCK_SIZE,
    group_name: str = "GeneratedWorldModels",
    brush_name_prefix: str = "Brush",
    include_validation_floor: bool = False,
    validation_floor_name: str = "ValidationFloor",
    validation_floor_margin: float = 512.0,
    validation_floor_thickness: float = 32.0,
    validation_floor_texture: str = "TEXTURES\\LevelTextures\\Terrain\\MainGrass.dtx",
    include_terrain_support_patch: bool = False,
    terrain_support_model_name: str = terrain_semantics.DEFAULT_TERRAIN_MODEL,
    terrain_support_name_prefix: str = "TerrainSupportPatch",
    terrain_support_margin: float = 0.0,
    terrain_support_selection_mode: str = "bounds",
    terrain_support_radius: float = 0.0,
    terrain_support_brush_mode: str = "single_polygon",
    terrain_support_thickness: float = 96.0,
    terrain_support_max_polygons: int = 128,
    terrain_support_side_texture: str = "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx",
    include_physics_shell_patch: bool = False,
    physics_shell_model_name: str = terrain_semantics.PHYSICS_BSP_MODEL,
    physics_shell_name_prefix: str = "PhysicsShell",
    physics_shell_max_polygons: int = 128,
    physics_shell_thickness: float = 16.0,
    physics_shell_side_texture: str = "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx",
) -> Tuple[bytes, SurrogateEdBuildReport]:
    """Return a zlib-wrapped ED with brush records plus a root/group/brush tree."""
    absolute, selected, error_report = _parse_selected_models_from_dat_bytes(
        data,
        source_path=source_path,
        model_names=model_names,
        max_models=max_models,
        include_skyboxes=include_skyboxes,
    )
    if error_report is not None:
        return b"", error_report
    raw_bytes, raw_report, brushes = _build_raw_surrogate_from_selected(
        selected,
        source_path=absolute,
    )
    if raw_report.status != "raw_surrogate_ed_built":
        return b"", raw_report

    floor_placement: Optional[_ValidationFloorPlacement] = None
    if include_terrain_support_patch:
        try:
            patch_brushes, patch_summaries, terrain_placement = _terrain_support_patch_brushes_for_brushes(
                data,
                brushes,
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
        except ValueError as exc:
            blockers = tuple(_unique_text(tuple(raw_report.blockers) + (str(exc),)))
            return b"", replace(
                raw_report,
                status="terrain_support_patch_failed",
                generated_byte_count=0,
                processor_readiness="full_world_skeleton_generation_failed",
                blockers=blockers,
            )
        brushes = tuple(brushes) + tuple(patch_brushes)
        for patch in patch_brushes:
            raw_bytes += legacy_ed_writer.write_brush_record(patch)
        raw_report = replace(
            raw_report,
            model_count=raw_report.model_count + len(patch_summaries),
            point_count=raw_report.point_count + sum(item.point_count for item in patch_summaries),
            polygon_count=raw_report.polygon_count + sum(item.polygon_count for item in patch_summaries),
            generated_byte_count=len(raw_bytes),
            model_summaries=tuple(raw_report.model_summaries) + tuple(patch_summaries),
        )
        floor_placement = terrain_placement

    if include_physics_shell_patch:
        try:
            shell_brushes, shell_summaries, shell_placement, shell_notes = _physics_shell_patch_brushes(
                data,
                source_model_name=physics_shell_model_name,
                name_prefix=physics_shell_name_prefix,
                max_polygons=physics_shell_max_polygons,
                thickness=physics_shell_thickness,
                side_texture=physics_shell_side_texture,
            )
        except ValueError as exc:
            blockers = tuple(_unique_text(tuple(raw_report.blockers) + (str(exc),)))
            return b"", replace(
                raw_report,
                status="physics_shell_patch_failed",
                generated_byte_count=0,
                processor_readiness="full_world_skeleton_generation_failed",
                blockers=blockers,
            )
        brushes = tuple(brushes) + tuple(shell_brushes)
        for shell in shell_brushes:
            raw_bytes += legacy_ed_writer.write_brush_record(shell)
        raw_report = replace(
            raw_report,
            model_count=raw_report.model_count + len(shell_summaries),
            point_count=raw_report.point_count + sum(item.point_count for item in shell_summaries),
            polygon_count=raw_report.polygon_count + sum(item.polygon_count for item in shell_summaries),
            generated_byte_count=len(raw_bytes),
            model_summaries=tuple(raw_report.model_summaries) + tuple(shell_summaries),
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(shell_notes))),
        )
        if shell_placement is not None and floor_placement is None:
            floor_placement = shell_placement

    if include_validation_floor:
        floor_brush, floor_summary, floor_placement = _validation_floor_brush_for_brushes(
            brushes,
            name=validation_floor_name,
            margin=validation_floor_margin,
            thickness=validation_floor_thickness,
            texture_name=validation_floor_texture,
        )
        brushes = tuple(brushes) + (floor_brush,)
        raw_bytes = raw_bytes + legacy_ed_writer.write_brush_record(floor_brush)
        raw_report = replace(
            raw_report,
            model_count=raw_report.model_count + 1,
            point_count=raw_report.point_count + floor_summary.point_count,
            polygon_count=raw_report.polygon_count + floor_summary.polygon_count,
            generated_byte_count=len(raw_bytes),
            model_summaries=tuple(raw_report.model_summaries) + (floor_summary,),
        )

    written_summaries = [summary for summary in raw_report.model_summaries if summary.status == "written"]
    brush_names = tuple(
        _full_world_skeleton_brush_name(summary.name, index, brush_name_prefix)
        for index, summary in enumerate(written_summaries)
    )
    node_hierarchy = _full_world_skeleton_node_hierarchy(
        brush_names,
        group_name=group_name,
        object_positions=_full_world_skeleton_object_positions(
            brushes,
            start_floor=floor_placement,
        ),
    )
    try:
        generated, wrapper_metadata = wrap_raw_surrogate_legacy_ed_bytes(
            raw_bytes,
            brush_count=raw_report.model_count,
            infostring=_infer_full_level_infostring(data) if infostring is None else infostring,
            block_size=block_size,
            inner_suffix=node_hierarchy,
        )
    except ValueError as exc:
        blockers = tuple(_unique_text(tuple(raw_report.blockers) + (str(exc),)))
        return b"", replace(
            raw_report,
            status="full_world_skeleton_wrapper_failed",
            generated_byte_count=0,
            processor_readiness="full_world_skeleton_generation_failed",
            blockers=blockers,
        )

    status = "full_world_skeleton_surrogate_ed_built"
    blockers: List[str] = []
    roundtrip_model_count = 0
    roundtrip_polygon_count = 0
    roundtrip_object_count = 0
    roundtrip_object_property_count = 0
    try:
        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path=os.path.abspath(source_path) if source_path else "surrogate_full_world_skeleton.ed",
        )
        roundtrip_model_count = int(scene.metadata.get("recovered_brush_count", 0) or 0)
        roundtrip_polygon_count = int(scene.metadata.get("recovered_polygon_count", 0) or 0)
        roundtrip_object_count = int(scene.metadata.get("recovered_object_count", 0) or 0)
        roundtrip_object_property_count = int(scene.metadata.get("recovered_object_property_count", 0) or 0)
        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path=os.path.abspath(source_path) if source_path else "surrogate_full_world_skeleton.ed",
        )
        if layout.status != "layout_parsed" or not layout.node_start:
            status = "full_world_skeleton_node_layout_failed"
            blockers.append("generated full-world skeleton node hierarchy was not located after brush records")
    except Exception as exc:
        status = "full_world_skeleton_roundtrip_parse_failed"
        blockers.append(f"generated full-world skeleton ED did not round-trip through reader: {exc}")

    if status == "full_world_skeleton_surrogate_ed_built" and (
        roundtrip_model_count != raw_report.model_count
        or roundtrip_polygon_count != raw_report.polygon_count
    ):
        status = "full_world_skeleton_roundtrip_count_mismatch"
        blockers.append(
            f"full-world skeleton round-trip recovered {roundtrip_model_count}/{roundtrip_polygon_count} "
            f"brushes/polygons, expected {raw_report.model_count}/{raw_report.polygon_count}"
        )

    cautions = (
        "generated full-world ED skeleton with root/group/brush nodes and minimal load scaffolding, not a complete source level",
        "original gameplay objects, portals, visibility hints, and compiler metadata are not reconstructed",
        "compiled DAT polygons are BSP output, not original authoring CSG brushes",
    )
    notes = tuple(raw_report.notes) + (
        "Root node hierarchy contains Container -> generated model group -> Brush children.",
        "Root node hierarchy also includes generated WorldProperties, StartPoint, and Light objects.",
        f"Generated world-model group node label: {group_name}",
    )
    if include_validation_floor:
        notes += (
            f"Generated validation floor brush label: {validation_floor_name}",
            "Validation floor is synthetic test scaffolding, not reconstructed DAT source geometry.",
        )
    if include_terrain_support_patch:
        notes += (
            f"Generated terrain support patch from {terrain_support_model_name}.",
            "Terrain support patches are closed source-like prism brushes derived from local Terrain* polygons.",
            "StartPoint is placed over the broadest upward-facing generated terrain support face.",
        )
    if include_physics_shell_patch:
        notes += (
            f"Generated PhysicsBSP shell patch from {physics_shell_model_name}.",
            "PhysicsBSP shell patches are closed slab brushes derived from compiled collision polygons.",
            "This is a budgeted indoor/static-shell reconstruction experiment, not recovered original CSG.",
        )
    return generated, replace(
        raw_report,
        status=status,
        generated_byte_count=len(generated),
        decompressed_byte_count=int(wrapper_metadata["decompressed_byte_count"]),
        wrapper_kind="zlib_blocked_full_world_skeleton",
        wrapper_block_count=int(wrapper_metadata["block_count"]),
        node_hierarchy_byte_count=len(node_hierarchy),
        object_count=roundtrip_object_count,
        object_property_count=roundtrip_object_property_count,
        roundtrip_model_count=roundtrip_model_count,
        roundtrip_polygon_count=roundtrip_polygon_count,
        processor_readiness="full_world_skeleton_surrogate",
        blockers=tuple(_unique_text(tuple(raw_report.blockers) + tuple(blockers))),
        cautions=cautions,
        notes=notes,
    )


def write_full_level_surrogate_legacy_ed_from_dat(
    dat_path: str,
    output_path: str,
    *,
    model_names: Sequence[str] = (),
    max_models: Optional[int] = None,
    include_skyboxes: bool = False,
    infostring: str = "",
    block_size: int = _FULL_LEVEL_ZLIB_BLOCK_SIZE,
) -> SurrogateEdBuildReport:
    """Write a full-level wrapped surrogate legacy ED file for selected DAT models."""
    absolute_dat = os.path.abspath(dat_path)
    absolute_output = os.path.abspath(output_path)
    try:
        with open(absolute_dat, "rb") as f:
            data = f.read()
    except OSError as exc:
        return SurrogateEdBuildReport(
            status="dat_read_failed",
            source_dat_path=absolute_dat,
            output_path=absolute_output,
            blockers=(str(exc),),
        )

    generated, report = build_full_level_surrogate_legacy_ed_bytes_from_dat_bytes(
        data,
        source_path=absolute_dat,
        model_names=model_names,
        max_models=max_models,
        include_skyboxes=include_skyboxes,
        infostring=infostring,
        block_size=block_size,
    )
    if report.status != "full_level_surrogate_ed_built":
        return replace(report, output_path=absolute_output)
    os.makedirs(os.path.dirname(absolute_output) or ".", exist_ok=True)
    with open(absolute_output, "wb") as f:
        f.write(generated)
    return replace(report, output_path=absolute_output)


def write_full_world_skeleton_surrogate_legacy_ed_from_dat(
    dat_path: str,
    output_path: str,
    *,
    model_names: Sequence[str] = (),
    max_models: Optional[int] = None,
    include_skyboxes: bool = False,
    infostring: Optional[str] = None,
    block_size: int = _FULL_LEVEL_ZLIB_BLOCK_SIZE,
    group_name: str = "GeneratedWorldModels",
    brush_name_prefix: str = "Brush",
    include_validation_floor: bool = False,
    validation_floor_name: str = "ValidationFloor",
    validation_floor_margin: float = 512.0,
    validation_floor_thickness: float = 32.0,
    validation_floor_texture: str = "TEXTURES\\LevelTextures\\Terrain\\MainGrass.dtx",
    include_terrain_support_patch: bool = False,
    terrain_support_model_name: str = terrain_semantics.DEFAULT_TERRAIN_MODEL,
    terrain_support_name_prefix: str = "TerrainSupportPatch",
    terrain_support_margin: float = 0.0,
    terrain_support_selection_mode: str = "bounds",
    terrain_support_radius: float = 0.0,
    terrain_support_brush_mode: str = "single_polygon",
    terrain_support_thickness: float = 96.0,
    terrain_support_max_polygons: int = 128,
    terrain_support_side_texture: str = "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx",
    include_physics_shell_patch: bool = False,
    physics_shell_model_name: str = terrain_semantics.PHYSICS_BSP_MODEL,
    physics_shell_name_prefix: str = "PhysicsShell",
    physics_shell_max_polygons: int = 128,
    physics_shell_thickness: float = 16.0,
    physics_shell_side_texture: str = "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx",
) -> SurrogateEdBuildReport:
    """Write a full-world skeleton surrogate legacy ED file."""
    absolute_dat = os.path.abspath(dat_path)
    absolute_output = os.path.abspath(output_path)
    try:
        with open(absolute_dat, "rb") as f:
            data = f.read()
    except OSError as exc:
        return SurrogateEdBuildReport(
            status="dat_read_failed",
            source_dat_path=absolute_dat,
            output_path=absolute_output,
            blockers=(str(exc),),
        )

    generated, report = build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
        data,
        source_path=absolute_dat,
        model_names=model_names,
        max_models=max_models,
        include_skyboxes=include_skyboxes,
        infostring=infostring,
        block_size=block_size,
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
        physics_shell_max_polygons=physics_shell_max_polygons,
        physics_shell_thickness=physics_shell_thickness,
        physics_shell_side_texture=physics_shell_side_texture,
    )
    if report.status != "full_world_skeleton_surrogate_ed_built":
        return replace(report, output_path=absolute_output)
    os.makedirs(os.path.dirname(absolute_output) or ".", exist_ok=True)
    with open(absolute_output, "wb") as f:
        f.write(generated)
    return replace(report, output_path=absolute_output)


def wrap_raw_surrogate_legacy_ed_bytes(
    raw_ed_bytes: bytes,
    *,
    brush_count: int,
    infostring: Optional[str] = None,
    block_size: int = _FULL_LEVEL_ZLIB_BLOCK_SIZE,
    inner_suffix: bytes = b"",
) -> Tuple[bytes, Dict[str, int]]:
    """Wrap a raw surrogate brush stream in the observed full-level ED shell."""
    if len(raw_ed_bytes) < 4:
        raise ValueError("raw surrogate ED stream is too short to wrap")
    version = struct.unpack_from("<I", raw_ed_bytes, 0)[0]
    if version != legacy_ed.LEGACY_ED_VERSION:
        raise ValueError(
            f"raw surrogate ED version {version} does not match {legacy_ed.LEGACY_ED_VERSION}"
        )
    if brush_count <= 0:
        raise ValueError("full-level surrogate wrapper requires at least one brush")
    if block_size <= 0:
        raise ValueError("full-level surrogate wrapper block size must be positive")

    inner_payload = struct.pack("<I", int(brush_count)) + raw_ed_bytes[4:] + bytes(inner_suffix)
    chunks = [
        inner_payload[offset:offset + block_size]
        for offset in range(0, len(inner_payload), block_size)
    ]
    if not chunks:
        chunks = [b""]
    compressed_chunks = [zlib.compress(chunk) for chunk in chunks]
    uncompressed_sizes = [len(chunk) for chunk in chunks]
    compressed_sizes = [len(chunk) for chunk in compressed_chunks]
    encoded_info = (
        _DEFAULT_FULL_LEVEL_INFOSTRING if infostring is None else str(infostring)
    ).encode(
        "latin1",
        errors="replace",
    )
    if len(encoded_info) > 4096:
        raise ValueError("full-level surrogate ED infostring is too long")

    out = bytearray()
    out.extend(struct.pack("<I", legacy_ed.LEGACY_ED_VERSION))
    out.append(1)
    out.extend(struct.pack("<I", len(encoded_info)))
    out.extend(encoded_info)
    out.extend(b"\x00" * 32)
    out.extend(struct.pack("<I", len(chunks)))
    out.extend(struct.pack("<I", int(block_size)))
    for value in compressed_sizes:
        out.extend(struct.pack("<I", value))
    for value in uncompressed_sizes:
        out.extend(struct.pack("<I", value))
    for chunk in compressed_chunks:
        out.extend(chunk)

    return bytes(out), {
        "block_count": len(chunks),
        "decompressed_byte_count": len(inner_payload),
        "compressed_byte_count": sum(compressed_sizes),
    }


def format_surrogate_ed_build_report(report: SurrogateEdBuildReport) -> str:
    lines = [
        "DAT surrogate legacy ED build",
        f"status: {report.status}",
    ]
    if report.source_dat_path:
        lines.append(f"source DAT: {report.source_dat_path}")
    if report.output_path:
        lines.append(f"output ED: {report.output_path}")
    lines.append(
        "summary: "
        f"models={report.model_count}, points={report.point_count}, "
        f"polygons={report.polygon_count}, skipped_polygons={report.skipped_polygon_count}, "
        f"bytes={report.generated_byte_count}"
    )
    if report.object_count or report.object_property_count:
        lines.append(
            "objects: "
            f"records={report.object_count}, properties={report.object_property_count}"
        )
    if report.wrapper_kind:
        lines.append(
            "wrapper: "
            f"{report.wrapper_kind}, blocks={report.wrapper_block_count}, "
            f"decompressed_bytes={report.decompressed_byte_count}"
        )
    if report.node_hierarchy_byte_count:
        lines.append(f"node hierarchy: bytes={report.node_hierarchy_byte_count}")
    lines.append(
        "roundtrip: "
        f"models={report.roundtrip_model_count}, polygons={report.roundtrip_polygon_count}"
    )
    lines.append(f"processor readiness: {report.processor_readiness}")
    for summary in report.model_summaries:
        lines.append(
            f"- {summary.name}: status={summary.status}, points={summary.point_count}, "
            f"polygons={summary.polygon_count}, skipped={summary.skipped_polygon_count}, "
            f"textures={summary.texture_count}, bytes={summary.byte_count}"
        )
        for note in summary.notes:
            lines.append(f"  note: {note}")
    for blocker in report.blockers:
        lines.append(f"blocker: {blocker}")
    for caution in report.cautions:
        lines.append(f"caution: {caution}")
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def _parse_selected_models_from_dat_bytes(
    data: bytes,
    *,
    source_path: str,
    model_names: Sequence[str],
    max_models: Optional[int],
    include_skyboxes: bool,
) -> Tuple[str, List[object], Optional[SurrogateEdBuildReport]]:
    absolute = os.path.abspath(source_path) if source_path else ""
    try:
        from core import bsp

        parsed = bsp.parse(data)
    except Exception as exc:
        return absolute, [], SurrogateEdBuildReport(
            status="dat_parse_failed",
            source_dat_path=absolute,
            blockers=(f"DAT parse failed: {exc}",),
        )

    selected = _select_models(
        parsed.world_models,
        model_names=model_names,
        max_models=max_models,
        include_skyboxes=include_skyboxes,
    )
    if not selected:
        return absolute, [], SurrogateEdBuildReport(
            status="no_models_selected",
            source_dat_path=absolute,
            selected_model_names=tuple(model_names),
            blockers=("no DAT world models matched the surrogate ED selection",),
        )
    return absolute, selected, None


def _build_raw_surrogate_from_selected(
    selected: Sequence[object],
    *,
    source_path: str,
) -> Tuple[bytes, SurrogateEdBuildReport, Tuple[legacy_ed_writer.LegacyEdBrush, ...]]:
    brushes: List[legacy_ed_writer.LegacyEdBrush] = []
    summaries: List[SurrogateEdModelSummary] = []
    for model_index, model in enumerate(selected):
        brush, summary = _model_to_legacy_brush(model, model_index)
        summaries.append(summary)
        if summary.status == "written" and brush is not None:
            brushes.append(brush)

    blockers: List[str] = []
    if not brushes:
        blockers.append("no selected DAT model could be written as a legacy ED brush")

    generated = legacy_ed_writer.build_raw_brush_stream(brushes)
    roundtrip_model_count = 0
    roundtrip_polygon_count = 0
    status = "raw_surrogate_ed_built"
    if blockers:
        status = "blocked"
    else:
        try:
            scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
                generated,
                source_path=source_path or "surrogate.ed",
            )
            roundtrip_model_count = int(scene.metadata.get("recovered_brush_count", 0) or 0)
            roundtrip_polygon_count = int(scene.metadata.get("recovered_polygon_count", 0) or 0)
        except Exception as exc:
            status = "roundtrip_parse_failed"
            blockers.append(f"generated legacy ED did not round-trip through reader: {exc}")

    written = [summary for summary in summaries if summary.status == "written"]
    polygon_count = sum(summary.polygon_count for summary in written)
    point_count = sum(summary.point_count for summary in written)
    skipped = sum(summary.skipped_polygon_count for summary in summaries)
    if status == "raw_surrogate_ed_built" and (
        roundtrip_model_count != len(written) or roundtrip_polygon_count != polygon_count
    ):
        status = "roundtrip_count_mismatch"
        blockers.append(
            f"round-trip recovered {roundtrip_model_count}/{roundtrip_polygon_count} "
            f"brushes/polygons, expected {len(written)}/{polygon_count}"
        )

    report = SurrogateEdBuildReport(
        status=status,
        source_dat_path=source_path,
        selected_model_names=tuple(getattr(model, "name", "") for model in selected),
        generated_byte_count=len(generated),
        model_count=len(written),
        point_count=point_count,
        polygon_count=polygon_count,
        skipped_polygon_count=skipped,
        roundtrip_model_count=roundtrip_model_count,
        roundtrip_polygon_count=roundtrip_polygon_count,
        model_summaries=tuple(summaries),
        blockers=tuple(_unique_text(blockers)),
        cautions=(
            "raw legacy ED brush stream only; not a full DEdit level wrapper",
            "object graph, lights, portals, node hierarchy, and gameplay properties are not reconstructed",
            "compiled DAT polygons are BSP output, not original authoring CSG brushes",
        ),
    )
    return generated, report, tuple(brushes)


def _full_world_skeleton_node_hierarchy(
    brush_names: Sequence[str],
    *,
    group_name: str,
    object_positions: _FullWorldSkeletonObjectPositions,
) -> bytes:
    brush_nodes = tuple(
        legacy_ed_writer.brush_node(
            index,
            name,
            node_id=3 + index,
            properties=legacy_ed_writer.full_world_brush_node_properties(name),
        )
        for index, name in enumerate(brush_names)
    )
    group = legacy_ed_writer.group_node(
        str(group_name or "GeneratedWorldModels"),
        brush_nodes,
        node_id=2,
        unknown2=16,
    )
    first_object_node_id = 3 + len(brush_nodes)
    gameplay_nodes = (
        legacy_ed_writer.object_node(
            "WorldProperties",
            "",
            node_id=first_object_node_id,
            properties=legacy_ed_writer.world_properties_object_properties(
                pos=object_positions.world_properties,
            ),
        ),
        legacy_ed_writer.object_node(
            "StartPoint",
            "",
            node_id=first_object_node_id + 1,
            properties=legacy_ed_writer.start_point_object_properties(
                pos=object_positions.start_point,
            ),
        ),
        legacy_ed_writer.object_node(
            "Light",
            "",
            node_id=first_object_node_id + 2,
            properties=legacy_ed_writer.light_object_properties(
                pos=object_positions.light,
            ),
        ),
    )
    root = legacy_ed_writer.world_root_node(
        (group,) + gameplay_nodes,
        node_id=1,
        display_name="Container",
        unknown2=24,
    )
    return legacy_ed_writer.build_node_hierarchy(root) + b"\x00" * 4


def _full_world_skeleton_object_positions(
    brushes: Sequence[legacy_ed_writer.LegacyEdBrush],
    *,
    start_floor: Optional[_ValidationFloorPlacement] = None,
) -> _FullWorldSkeletonObjectPositions:
    points = [point for brush in brushes for point in brush.points]
    if not points:
        return _FullWorldSkeletonObjectPositions(
            world_properties=(0.0, 512.0, 0.0),
            start_point=(0.0, 128.0, 0.0),
            light=(0.0, 512.0, 0.0),
        )

    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    min_z = min(point[2] for point in points)
    max_z = max(point[2] for point in points)
    center = (
        (min_x + max_x) * 0.5,
        (min_y + max_y) * 0.5,
        (min_z + max_z) * 0.5,
    )
    y_span = max_y - min_y
    start_clearance = max(64.0, y_span * 0.25)
    light_clearance = max(256.0, y_span)
    start_point = (center[0], max_y + start_clearance, center[2])
    if start_floor is not None:
        start_point = (
            float(start_floor.center[0]),
            float(start_floor.top_y) + 128.0,
            float(start_floor.center[2]),
        )
    return _FullWorldSkeletonObjectPositions(
        world_properties=(center[0], max_y + 512.0, center[2]),
        start_point=start_point,
        light=(center[0], max_y + light_clearance, center[2]),
    )


def _validation_floor_brush_for_brushes(
    brushes: Sequence[legacy_ed_writer.LegacyEdBrush],
    *,
    name: str,
    margin: float,
    thickness: float,
    texture_name: str,
) -> Tuple[legacy_ed_writer.LegacyEdBrush, SurrogateEdModelSummary, _ValidationFloorPlacement]:
    points = [point for brush in brushes for point in brush.points]
    if not points:
        min_x = -512.0
        max_x = 512.0
        min_y = 0.0
        min_z = -512.0
        max_z = 512.0
    else:
        min_x = min(point[0] for point in points)
        max_x = max(point[0] for point in points)
        min_y = min(point[1] for point in points)
        min_z = min(point[2] for point in points)
        max_z = max(point[2] for point in points)

    safe_margin = max(64.0, float(margin))
    safe_thickness = max(8.0, float(thickness))
    top_y = min_y - 4.0
    bottom_y = top_y - safe_thickness
    floor_points: Tuple[Vec3, ...] = (
        (min_x - safe_margin, bottom_y, min_z - safe_margin),
        (max_x + safe_margin, bottom_y, min_z - safe_margin),
        (max_x + safe_margin, bottom_y, max_z + safe_margin),
        (min_x - safe_margin, bottom_y, max_z + safe_margin),
        (min_x - safe_margin, top_y, min_z - safe_margin),
        (max_x + safe_margin, top_y, min_z - safe_margin),
        (max_x + safe_margin, top_y, max_z + safe_margin),
        (min_x - safe_margin, top_y, max_z + safe_margin),
    )
    face_indices: Tuple[Tuple[int, ...], ...] = (
        (4, 7, 6, 5),  # top
        (0, 1, 2, 3),  # bottom
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    )
    surfaces: List[legacy_ed_writer.LegacyEdSurface] = []
    for indices in face_indices:
        normal, dist = _polygon_plane(floor_points, indices)
        surfaces.append(legacy_ed_writer.LegacyEdSurface(
            vertex_indices=indices,
            plane_normal=normal,
            plane_dist=dist,
            texture_name=str(texture_name or "TEXTURES\\LevelTextures\\Terrain\\MainGrass.dtx"),
            uv_o=floor_points[indices[0]],
            uv_p=(1.0, 0.0, 0.0),
            uv_q=(0.0, 0.0, 1.0),
            texture_flags=0,
            surface_flags=0,
            shade_rgb=(0, 0, 0),
        ))

    floor_name = str(name or "ValidationFloor")
    brush = legacy_ed_writer.LegacyEdBrush(
        points=floor_points,
        surfaces=tuple(surfaces),
        color_rgb=(96, 128, 96),
        name=floor_name,
    )
    summary = SurrogateEdModelSummary(
        name=floor_name,
        status="written",
        point_count=len(floor_points),
        polygon_count=len(surfaces),
        texture_count=1,
        byte_count=len(legacy_ed_writer.write_brush_record(brush)),
        notes=("synthetic validation floor brush generated for isolated full-world skeleton testing",),
    )
    placement = _ValidationFloorPlacement(
        center=((min_x + max_x) * 0.5, top_y, (min_z + max_z) * 0.5),
        top_y=top_y,
    )
    return brush, summary, placement


def _terrain_support_patch_brushes_for_brushes(
    data: bytes,
    anchor_brushes: Sequence[legacy_ed_writer.LegacyEdBrush],
    *,
    source_model_name: str,
    name_prefix: str,
    margin: float,
    selection_mode: str = "bounds",
    radius: float = 0.0,
    brush_mode: str = "single_polygon",
    thickness: float,
    max_polygons: int,
    side_texture: str,
) -> Tuple[Tuple[legacy_ed_writer.LegacyEdBrush, ...], Tuple[SurrogateEdModelSummary, ...], _ValidationFloorPlacement]:
    anchor_points = [point for brush in anchor_brushes for point in brush.points]
    if not anchor_points:
        raise ValueError("terrain support patch requires at least one anchor brush")
    try:
        from core import bsp

        parsed = bsp.parse(data)
    except Exception as exc:
        raise ValueError(f"terrain support source DAT parse failed: {exc}") from exc

    terrain_name = str(source_model_name or terrain_semantics.DEFAULT_TERRAIN_MODEL)
    terrain = terrain_semantics.model_by_name(
        tuple(getattr(parsed, "world_models", ()) or ()),
        terrain_name,
    )
    if terrain is None:
        raise ValueError(f"terrain support source model was not found: {terrain_name}")

    terrain_items = terrain_reconstruction.terrain_support_items(terrain)
    selected = terrain_reconstruction.select_terrain_support_items(
        terrain_items,
        tuple(anchor_points),
        margin=margin,
        selection_mode=selection_mode,
        radius=radius,
        max_items=max_polygons,
    )

    if not selected:
        raise ValueError(
            f"terrain support patch found no {terrain_name} polygons inside selected model bounds"
        )
    limit = max(1, int(max_polygons))
    if len(selected) > limit:
        raise ValueError(
            f"terrain support patch selected {len(selected)} polygon(s), above limit {limit}"
        )

    side_texture_name = str(side_texture or "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx")
    prefix = _safe_legacy_name_component(name_prefix) or "TerrainSupportPatch"
    patch_brushes: List[legacy_ed_writer.LegacyEdBrush] = []
    patch_summaries: List[SurrogateEdModelSummary] = []
    support_placement = terrain_reconstruction.terrain_support_start_placement(
        selected,
        tuple(anchor_points),
        margin=margin,
    )

    support_groups = _terrain_support_item_groups(selected, brush_mode=brush_mode)
    for patch_index, group in enumerate(support_groups):
        first_polygon_index = int(group[0][0])
        brush, summary = _terrain_polygon_group_prism_brush(
            terrain,
            group,
            name=f"{prefix}_{first_polygon_index:04d}",
            patch_index=patch_index,
            thickness=thickness,
            side_texture=side_texture_name,
        )
        patch_brushes.append(brush)
        patch_summaries.append(summary)

    placement = _ValidationFloorPlacement(
        center=support_placement.center,
        top_y=support_placement.top_y,
    )
    return tuple(patch_brushes), tuple(patch_summaries), placement


def _physics_shell_patch_brushes(
    data: bytes,
    *,
    source_model_name: str,
    name_prefix: str,
    max_polygons: int,
    thickness: float,
    side_texture: str,
) -> Tuple[
    Tuple[legacy_ed_writer.LegacyEdBrush, ...],
    Tuple[SurrogateEdModelSummary, ...],
    Optional[_ValidationFloorPlacement],
    Tuple[str, ...],
]:
    try:
        from core import bsp

        parsed = bsp.parse(data)
    except Exception as exc:
        raise ValueError(f"PhysicsBSP shell source DAT parse failed: {exc}") from exc

    source_name = str(source_model_name or terrain_semantics.PHYSICS_BSP_MODEL)
    model = terrain_semantics.model_by_name(
        tuple(getattr(parsed, "world_models", ()) or ()),
        source_name,
    )
    if model is None:
        raise ValueError(f"PhysicsBSP shell source model was not found: {source_name}")

    points = tuple(_finite_vec3(point) for point in (getattr(model, "points", ()) or ()))
    polygons = tuple(getattr(model, "polygons", ()) or ())
    candidates: List[Tuple[float, int, object, Tuple[int, ...], Tuple[Vec3, ...]]] = []
    invalid_polygon_count = 0
    for polygon_index, polygon in enumerate(polygons):
        indices = tuple(int(index) for index in (getattr(polygon, "vertex_indices", ()) or ()))
        if not (3 <= len(indices) <= 64) or any(index < 0 or index >= len(points) for index in indices):
            invalid_polygon_count += 1
            continue
        polygon_points = tuple(points[index] for index in indices)
        area = _polygon_area(polygon_points)
        if not math.isfinite(area) or area <= 1.0e-4:
            invalid_polygon_count += 1
            continue
        candidates.append((float(area), int(polygon_index), polygon, indices, polygon_points))

    if not candidates:
        raise ValueError(f"PhysicsBSP shell patch found no writable polygons in {source_name}")

    limit = max(1, int(max_polygons))
    prefix = _safe_legacy_name_component(name_prefix) or "PhysicsShell"
    side_texture_name = str(side_texture or "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx")
    safe_thickness = max(4.0, float(thickness))
    brushes: List[legacy_ed_writer.LegacyEdBrush] = []
    summaries: List[SurrogateEdModelSummary] = []
    skipped_slab_count = 0
    floor_candidates: List[Tuple[float, Vec3, float]] = []

    for area, polygon_index, polygon, _indices, polygon_points in sorted(
        candidates,
        key=lambda item: (-item[0], item[1]),
    ):
        if len(brushes) >= limit:
            break
        brush_summary = _physics_shell_polygon_slab_brush(
            model,
            polygon,
            polygon_points,
            polygon_index=polygon_index,
            name=f"{prefix}_{polygon_index:04d}",
            thickness=safe_thickness,
            side_texture=side_texture_name,
        )
        if brush_summary is None:
            skipped_slab_count += 1
            continue
        brush, summary = brush_summary
        brushes.append(brush)
        summaries.append(summary)
        if brush.surfaces:
            normal = brush.surfaces[0].plane_normal
            if normal[1] > 0.45:
                center = (
                    sum(point[0] for point in polygon_points) / len(polygon_points),
                    sum(point[1] for point in polygon_points) / len(polygon_points),
                    sum(point[2] for point in polygon_points) / len(polygon_points),
                )
                floor_candidates.append((area, center, center[1]))

    if not brushes:
        raise ValueError(
            f"PhysicsBSP shell patch could not build any closed slab brushes from {source_name}"
        )

    placement = None
    if floor_candidates:
        _area, center, top_y = max(floor_candidates, key=lambda item: item[0])
        placement = _ValidationFloorPlacement(center=center, top_y=top_y)

    notes = [
        f"PhysicsBSP shell patch emitted {len(brushes)}/{len(candidates)} writable {source_name} polygon slab brush(es).",
        f"PhysicsBSP shell polygon budget: {limit}; slab thickness: {safe_thickness:g}.",
    ]
    if len(brushes) >= limit and len(candidates) > limit:
        notes.append(
            f"PhysicsBSP shell patch stopped at the polygon budget; {len(candidates) - len(brushes)} candidate polygon(s) remain ungenerated."
        )
    if invalid_polygon_count or skipped_slab_count:
        notes.append(
            f"PhysicsBSP shell patch skipped invalid={invalid_polygon_count}, non-closed={skipped_slab_count} polygon(s)."
        )
    if placement is not None:
        notes.append("StartPoint is placed over the broadest upward-facing generated PhysicsBSP shell face.")

    return tuple(brushes), tuple(summaries), placement, tuple(notes)


def _physics_shell_polygon_slab_brush(
    model: object,
    polygon: object,
    front_points: Sequence[Vec3],
    *,
    polygon_index: int,
    name: str,
    thickness: float,
    side_texture: str,
) -> Optional[Tuple[legacy_ed_writer.LegacyEdBrush, SurrogateEdModelSummary]]:
    front = tuple(_finite_vec3(point) for point in front_points)
    if len(front) < 3:
        return None
    n = len(front)
    base_indices = tuple(range(n))
    surface = _surface_for_polygon(model, polygon)
    front_texture = _texture_name_for_polygon(model, polygon)
    uv_o = _finite_vec3(getattr(surface, "uv_o", front[0]) if surface else front[0])
    uv_p = _finite_vec3(getattr(surface, "uv_p", (1.0, 0.0, 0.0)) if surface else (1.0, 0.0, 0.0))
    uv_q = _finite_vec3(getattr(surface, "uv_q", (0.0, 0.0, 1.0)) if surface else (0.0, 0.0, 1.0))
    texture_flags = int(getattr(surface, "texture_flags", 0) or 0) if surface else 0
    side_texture_name = str(side_texture or "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx")
    side_texture_flags = _helper_texture_flags(side_texture_name)
    safe_thickness = max(4.0, float(thickness))

    for front_indices in (base_indices, tuple(reversed(base_indices))):
        normal, _dist = _polygon_plane(front, front_indices)
        normal_len = math.sqrt(normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2])
        if normal_len <= 1.0e-6:
            continue
        back = tuple(
            (
                point[0] - normal[0] * safe_thickness,
                point[1] - normal[1] * safe_thickness,
                point[2] - normal[2] * safe_thickness,
            )
            for point in front
        )
        points = front + back
        back_indices = tuple(reversed(tuple(index + n for index in front_indices)))
        for side_variant in (0, 1):
            surfaces: List[legacy_ed_writer.LegacyEdSurface] = []
            front_normal, front_dist = _polygon_plane(points, front_indices)
            back_normal, back_dist = _polygon_plane(points, back_indices)
            surfaces.append(legacy_ed_writer.LegacyEdSurface(
                vertex_indices=front_indices,
                plane_normal=front_normal,
                plane_dist=front_dist,
                texture_name=front_texture,
                uv_o=uv_o,
                uv_p=uv_p,
                uv_q=uv_q,
                texture_flags=texture_flags,
                surface_flags=0,
                shade_rgb=(0, 0, 0),
            ))
            surfaces.append(legacy_ed_writer.LegacyEdSurface(
                vertex_indices=back_indices,
                plane_normal=back_normal,
                plane_dist=back_dist,
                texture_name=side_texture_name,
                uv_o=points[back_indices[0]],
                uv_p=(1.0, 0.0, 0.0),
                uv_q=(0.0, 0.0, 1.0),
                texture_flags=side_texture_flags,
                surface_flags=0,
                shade_rgb=(0, 0, 0),
            ))
            for offset, first in enumerate(front_indices):
                second = front_indices[(offset + 1) % len(front_indices)]
                if side_variant == 0:
                    side_indices = (first, first + n, second + n, second)
                else:
                    side_indices = (first, second, second + n, first + n)
                side_normal, side_dist = _polygon_plane(points, side_indices)
                surfaces.append(legacy_ed_writer.LegacyEdSurface(
                    vertex_indices=side_indices,
                    plane_normal=side_normal,
                    plane_dist=side_dist,
                    texture_name=side_texture_name,
                    uv_o=points[first],
                    uv_p=(1.0, 0.0, 0.0),
                    uv_q=(0.0, 1.0, 0.0),
                    texture_flags=side_texture_flags,
                    surface_flags=0,
                    shade_rgb=(0, 0, 0),
                ))
            brush = legacy_ed_writer.LegacyEdBrush(
                points=points,
                surfaces=tuple(surfaces),
                color_rgb=(160, 112, 96),
                name=str(name),
            )
            if not _legacy_brush_faces_enclose_points(brush):
                continue
            summary = SurrogateEdModelSummary(
                name=str(name),
                status="written",
                point_count=len(points),
                polygon_count=len(surfaces),
                texture_count=len({front_texture, side_texture_name}),
                byte_count=len(legacy_ed_writer.write_brush_record(brush)),
                notes=(
                    f"closed PhysicsBSP slab generated from {getattr(model, 'name', 'PhysicsBSP')} polygon {int(polygon_index)}",
                ),
            )
            return brush, summary
    return None


def _terrain_support_item_groups(
    items: Sequence[Tuple[int, object, Tuple[int, ...], Tuple[Vec3, ...], Vec3, Tuple[float, float, float, float]]],
    *,
    brush_mode: str,
) -> Tuple[Tuple[Tuple[int, object, Tuple[int, ...], Tuple[Vec3, ...], Vec3, Tuple[float, float, float, float]], ...], ...]:
    mode = terrain_reconstruction.normalize_terrain_support_brush_mode(brush_mode)
    if mode == "single_polygon":
        return tuple((item,) for item in items)
    if mode == "triangulated_ngons":
        groups: List[Tuple[Tuple[int, object, Tuple[int, ...], Tuple[Vec3, ...], Vec3, Tuple[float, float, float, float]], ...]] = []
        for item in items:
            if len(item[2]) <= 8:
                groups.append((item,))
                continue
            triangle_items = terrain_reconstruction.triangulated_terrain_support_items(item)
            probe_brush, _summary = _terrain_polygon_group_prism_brush(
                None,
                triangle_items,
                name="triangulated_probe",
                patch_index=0,
                thickness=128.0,
                side_texture="Default",
            )
            if _legacy_brush_faces_enclose_points(probe_brush):
                groups.append(triangle_items)
            else:
                groups.extend((triangle_item,) for triangle_item in triangle_items)
        return tuple(groups)
    if mode != "paired_triangles":
        raise ValueError(f"unsupported terrain support brush mode: {brush_mode}")

    by_polygon = {item[0]: item for item in items}
    by_edge: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for item in items:
        indices = item[2]
        if len(indices) != 3:
            continue
        for offset, first in enumerate(indices):
            second = indices[(offset + 1) % len(indices)]
            by_edge[tuple(sorted((int(first), int(second))))].append(int(item[0]))

    used: set[int] = set()
    groups: List[Tuple[Tuple[int, object, Tuple[int, ...], Tuple[Vec3, ...], Vec3, Tuple[float, float, float, float]], ...]] = []
    for item in items:
        polygon_index = int(item[0])
        if polygon_index in used:
            continue
        indices = item[2]
        best_neighbor: Optional[Tuple[int, object, Tuple[int, ...], Tuple[Vec3, ...], Vec3, Tuple[float, float, float, float]]] = None
        if len(indices) == 3:
            for offset, first in enumerate(indices):
                second = indices[(offset + 1) % len(indices)]
                edge = tuple(sorted((int(first), int(second))))
                candidates = [
                    by_polygon[index] for index in by_edge.get(edge, ())
                    if index != polygon_index and index not in used and index in by_polygon
                ]
                for candidate in candidates:
                    if len(set(indices) | set(candidate[2])) != 4:
                        continue
                    candidate_brush, _summary = _terrain_polygon_group_prism_brush(
                        None,
                        (item, candidate),
                        name="convex_probe",
                        patch_index=0,
                        thickness=128.0,
                        side_texture="Default",
                    )
                    if _legacy_brush_faces_enclose_points(candidate_brush):
                        best_neighbor = candidate
                        break
                if best_neighbor is not None:
                    break
        if best_neighbor is not None:
            used.add(polygon_index)
            used.add(int(best_neighbor[0]))
            groups.append((item, best_neighbor))
        else:
            used.add(polygon_index)
            groups.append((item,))
    return tuple(groups)


def _legacy_brush_faces_enclose_points(brush: legacy_ed_writer.LegacyEdBrush) -> bool:
    for surface in brush.surfaces:
        normal = surface.plane_normal
        dist = surface.plane_dist
        for point in brush.points:
            delta = normal[0] * point[0] + normal[1] * point[1] + normal[2] * point[2] - dist
            if delta > 1.0e-3:
                return False
    return True


def _polygon_area(points: Sequence[Vec3]) -> float:
    return terrain_reconstruction.polygon_area(points)


def _terrain_polygon_prism_brush(
    terrain_model: object,
    polygon: object,
    top_points: Sequence[Vec3],
    *,
    name: str,
    patch_index: int,
    thickness: float,
    side_texture: str,
) -> Tuple[legacy_ed_writer.LegacyEdBrush, SurrogateEdModelSummary]:
    item = (
        int(getattr(polygon, "surface_index", patch_index)),
        polygon,
        tuple(range(len(top_points))),
        tuple(_finite_vec3(point) for point in top_points),
        (
            sum(float(point[0]) for point in top_points) / len(top_points),
            sum(float(point[1]) for point in top_points) / len(top_points),
            sum(float(point[2]) for point in top_points) / len(top_points),
        ),
        (
            min(float(point[0]) for point in top_points),
            max(float(point[0]) for point in top_points),
            min(float(point[2]) for point in top_points),
            max(float(point[2]) for point in top_points),
        ),
    )
    return _terrain_polygon_group_prism_brush(
        terrain_model,
        (item,),
        name=name,
        patch_index=patch_index,
        thickness=thickness,
        side_texture=side_texture,
    )


def _terrain_polygon_group_prism_brush(
    terrain_model: object,
    items: Sequence[Tuple[int, object, Tuple[int, ...], Tuple[Vec3, ...], Vec3, Tuple[float, float, float, float]]],
    *,
    name: str,
    patch_index: int,
    thickness: float,
    side_texture: str,
) -> Tuple[legacy_ed_writer.LegacyEdBrush, SurrogateEdModelSummary]:
    safe_thickness = max(16.0, float(thickness))
    top_points_by_source: Dict[int, Vec3] = {}
    top_order: List[int] = []
    for item in items:
        source_indices = item[2]
        top_points = item[3]
        for source_index, point in zip(source_indices, top_points):
            key = int(source_index)
            if key not in top_points_by_source:
                top_points_by_source[key] = _finite_vec3(point)
                top_order.append(key)
    top = tuple(top_points_by_source[key] for key in top_order)
    bottom = tuple((point[0], point[1] - safe_thickness, point[2]) for point in top)
    points = top + bottom
    n = len(top)
    local_by_source = {source_index: local_index for local_index, source_index in enumerate(top_order)}
    bottom_offset = n
    side_texture_name = str(side_texture or "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx")
    side_texture_flags = _helper_texture_flags(side_texture_name)
    surfaces: List[legacy_ed_writer.LegacyEdSurface] = []
    texture_names = set()
    directed_edges: List[Tuple[int, int]] = []
    edge_use_counts: Dict[Tuple[int, int], int] = defaultdict(int)

    for item in items:
        _polygon_index, polygon, source_indices, top_points, _center, _bounds = item
        top_indices = tuple(local_by_source[int(source_index)] for source_index in source_indices)
        normal, _dist = _polygon_plane(points, top_indices)
        if normal[1] < 0.0:
            top_indices = tuple(reversed(top_indices))
            normal, _dist = _polygon_plane(points, top_indices)
        bottom_indices = tuple(reversed(tuple(index + bottom_offset for index in top_indices)))
        top_texture = _texture_name_for_polygon(terrain_model, polygon) if terrain_model is not None else "Default"
        surface = _surface_for_polygon(terrain_model, polygon) if terrain_model is not None else None
        uv_o = _finite_vec3(getattr(surface, "uv_o", points[top_indices[0]]) if surface else points[top_indices[0]])
        uv_p = _finite_vec3(getattr(surface, "uv_p", (1.0, 0.0, 0.0)) if surface else (1.0, 0.0, 0.0))
        uv_q = _finite_vec3(getattr(surface, "uv_q", (0.0, 0.0, 1.0)) if surface else (0.0, 0.0, 1.0))
        texture_flags = int(getattr(surface, "texture_flags", 0) or 0) if surface else 0
        top_normal, top_dist = _polygon_plane(points, top_indices)
        bottom_normal, bottom_dist = _polygon_plane(points, bottom_indices)
        texture_names.add(top_texture)
        surfaces.append(legacy_ed_writer.LegacyEdSurface(
            vertex_indices=top_indices,
            plane_normal=top_normal,
            plane_dist=top_dist,
            texture_name=top_texture,
            uv_o=uv_o,
            uv_p=uv_p,
            uv_q=uv_q,
            texture_flags=texture_flags,
            surface_flags=0,
            shade_rgb=(0, 0, 0),
        ))
        surfaces.append(legacy_ed_writer.LegacyEdSurface(
            vertex_indices=bottom_indices,
            plane_normal=bottom_normal,
            plane_dist=bottom_dist,
            texture_name=side_texture_name,
            uv_o=points[bottom_indices[0]],
            uv_p=(1.0, 0.0, 0.0),
            uv_q=(0.0, 0.0, 1.0),
            texture_flags=side_texture_flags,
            surface_flags=0,
            shade_rgb=(0, 0, 0),
        ))
        for offset, first in enumerate(top_indices):
            second = top_indices[(offset + 1) % len(top_indices)]
            directed_edges.append((int(first), int(second)))
            edge_use_counts[tuple(sorted((int(first), int(second))))] += 1

    for first, second in directed_edges:
        if edge_use_counts[tuple(sorted((first, second)))] != 1:
            continue
        side_indices = (first, first + bottom_offset, second + bottom_offset, second)
        normal, dist = _polygon_plane(points, side_indices)
        surfaces.append(legacy_ed_writer.LegacyEdSurface(
            vertex_indices=side_indices,
            plane_normal=normal,
            plane_dist=dist,
            texture_name=side_texture_name,
            uv_o=points[first],
            uv_p=(1.0, 0.0, 0.0),
            uv_q=(0.0, 1.0, 0.0),
            texture_flags=side_texture_flags,
            surface_flags=0,
            shade_rgb=(0, 0, 0),
        ))

    brush = legacy_ed_writer.LegacyEdBrush(
        points=points,
        surfaces=tuple(surfaces),
        color_rgb=(96, 160, 96),
        name=str(name),
    )
    summary = SurrogateEdModelSummary(
        name=str(name),
        status="written",
        point_count=len(points),
        polygon_count=len(surfaces),
        texture_count=len(texture_names | {side_texture_name}),
        byte_count=len(legacy_ed_writer.write_brush_record(brush)),
        notes=(
            f"closed terrain support prism generated from {getattr(terrain_model, 'name', 'Terrain')} polygon group "
            + ",".join(str(item[0]) for item in items),
        ),
    )
    return brush, summary


def _full_world_skeleton_brush_name(model_name: str, index: int, prefix: str) -> str:
    safe_model = _safe_legacy_name_component(model_name) or f"Model{index}"
    safe_prefix = _safe_legacy_name_component(prefix) or "Brush"
    return f"{safe_prefix}_{safe_model}_{int(index)}"[:120]


def _safe_legacy_name_component(value: object) -> str:
    result = []
    for ch in str(value or ""):
        if ch.isalnum() or ch in {"_", "-"}:
            result.append(ch)
        elif ch.isspace():
            result.append("_")
    return "".join(result).strip("_-")


def _select_models(
    models: Sequence[object],
    *,
    model_names: Sequence[str],
    max_models: Optional[int],
    include_skyboxes: bool,
) -> List[object]:
    allowed = {str(name).lower() for name in model_names}
    result: List[object] = []
    for model in models:
        name = str(getattr(model, "name", "") or "")
        if allowed and name.lower() not in allowed:
            continue
        if not include_skyboxes and bool(getattr(model, "is_skybox", lambda: False)()):
            continue
        if not getattr(model, "points", None) or not getattr(model, "polygons", None):
            continue
        result.append(model)
        if max_models is not None and len(result) >= max(0, int(max_models)):
            break
    return result


def _model_to_legacy_brush_bytes(model: object, model_index: int) -> Tuple[bytes, SurrogateEdModelSummary]:
    brush, summary = _model_to_legacy_brush(model, model_index)
    if brush is None:
        return b"", summary
    return legacy_ed_writer.write_brush_record(brush), summary


def _model_to_legacy_brush(
    model: object,
    model_index: int,
) -> Tuple[Optional[legacy_ed_writer.LegacyEdBrush], SurrogateEdModelSummary]:
    points = list(getattr(model, "points", []) or [])
    polygons = list(getattr(model, "polygons", []) or [])
    name = str(getattr(model, "name", "") or f"WorldModel{model_index}")
    notes: List[str] = []
    if len(points) > 65535:
        return None, SurrogateEdModelSummary(
            name=name,
            status="blocked",
            point_count=len(points),
            polygon_count=len(polygons),
            notes=("legacy ED polygon indices are uint16; model has too many points",),
        )
    written_polygons = [
        polygon for polygon in polygons
        if 3 <= len(getattr(polygon, "vertex_indices", []) or []) <= 64
        and all(0 <= int(index) < len(points) for index in getattr(polygon, "vertex_indices", []) or [])
    ]
    skipped = len(polygons) - len(written_polygons)
    if skipped:
        notes.append(f"skipped {skipped} polygon(s) with unsupported vertex counts or indices")
    if not written_polygons:
        return None, SurrogateEdModelSummary(
            name=name,
            status="blocked",
            point_count=len(points),
            polygon_count=0,
            skipped_polygon_count=skipped,
            texture_count=len(getattr(model, "texture_names", []) or []),
            notes=tuple(notes or ["model has no writable polygons"]),
        )

    legacy_points = tuple(_finite_vec3(point) for point in points)
    surfaces: List[legacy_ed_writer.LegacyEdSurface] = []
    for polygon in written_polygons:
        indices = tuple(int(index) for index in polygon.vertex_indices)
        normal, dist = _polygon_plane(points, indices)
        surface = _surface_for_polygon(model, polygon)
        uv_o = _finite_vec3(getattr(surface, "uv_o", (0.0, 0.0, 0.0)) if surface else (0.0, 0.0, 0.0))
        uv_p = _finite_vec3(getattr(surface, "uv_p", (1.0, 0.0, 0.0)) if surface else (1.0, 0.0, 0.0))
        uv_q = _finite_vec3(getattr(surface, "uv_q", (0.0, 0.0, 1.0)) if surface else (0.0, 0.0, 1.0))
        texture_flags = int(getattr(surface, "texture_flags", 0) or 0) if surface else 0
        surfaces.append(legacy_ed_writer.LegacyEdSurface(
            vertex_indices=indices,
            plane_normal=normal,
            plane_dist=dist,
            texture_name=_texture_name_for_polygon(model, polygon),
            uv_o=uv_o,
            uv_p=uv_p,
            uv_q=uv_q,
            texture_flags=texture_flags,
            surface_flags=0,
            shade_rgb=(0, 0, 0),
        ))
    brush = legacy_ed_writer.LegacyEdBrush(
        points=legacy_points,
        surfaces=tuple(surfaces),
        color_rgb=tuple(_brush_color(model_index)),  # type: ignore[arg-type]
        name=name,
    )
    summary = SurrogateEdModelSummary(
        name=name,
        status="written",
        point_count=len(points),
        polygon_count=len(written_polygons),
        skipped_polygon_count=skipped,
        texture_count=len(getattr(model, "texture_names", []) or []),
        byte_count=len(legacy_ed_writer.write_brush_record(brush)),
        notes=tuple(notes),
    )
    return brush, summary


def _surface_for_polygon(model: object, polygon: object) -> Optional[object]:
    surfaces = list(getattr(model, "surfaces", []) or [])
    index = int(getattr(polygon, "surface_index", -1) or -1)
    if 0 <= index < len(surfaces):
        return surfaces[index]
    return None


def _texture_name_for_polygon(model: object, polygon: object) -> str:
    try:
        texture = model.texture_name_for(polygon)
    except Exception:
        texture = None
    return str(texture or "Default")


def _helper_texture_flags(texture_name: object) -> int:
    if terrain_semantics.helper_texture_role(texture_name):
        return 1
    return 0


def _infer_full_level_infostring(data: bytes) -> str:
    try:
        from core import bsp

        parsed = bsp.parse(data)
        grid = float(getattr(parsed, "lightmap_grid_size", 0.0) or 0.0)
    except Exception:
        grid = 64.0
    if not math.isfinite(grid) or grid <= 0.0:
        grid = 64.0
    if abs(grid - round(grid)) < 1.0e-4:
        grid_text = str(int(round(grid)))
    else:
        grid_text = f"{grid:g}"
    return f"AmbientLight 80 80 80 ; PBlockSize 2048 ; LMGridSize {grid_text}; MaxLMSize 32"


def _polygon_plane(points: Sequence[Vec3], indices: Sequence[int]) -> Tuple[Vec3, float]:
    return terrain_reconstruction.polygon_plane(points, indices)


def _finite_vec3(value: object) -> Vec3:
    try:
        x, y, z = value  # type: ignore[misc]
        result = (float(x), float(y), float(z))
    except Exception:
        return (0.0, 0.0, 0.0)
    if not all(math.isfinite(item) for item in result):
        return (0.0, 0.0, 0.0)
    return result


def _brush_color(index: int) -> bytes:
    return bytes((
        96 + (index * 37) % 128,
        96 + (index * 53) % 128,
        96 + (index * 71) % 128,
    ))


def _legacy_prefab_node_intro(brush_count: int) -> bytes:
    count_byte = max(0, min(255, int(brush_count)))
    # Matches the direct-root prefab object-list prelude used by real
    # MM9-compatible prefabs such as HallwayT, 45archroof, and Sign1.
    return bytes([count_byte]) + b"\x00\x01" + b"\x00" * 9


def _legacy_grouped_prefab_node_intro(brush_count: int) -> bytes:
    count_byte = max(0, min(255, int(brush_count)))
    # Matches the named null/group prelude used by real MM9 prefabs such as
    # Furniture/Bench.ed: root child count 1, then group child count N.
    return b"\x01" + b"\x00" * 5 + bytes([count_byte]) + b"\x00\x01" + b"\x00" * 9


def _legacy_prefab_between_brush_objects(index: int) -> bytes:
    marker = 0x1994 + max(0, int(index))
    return struct.pack("<IIIII", marker, 0, 0x00010000, int(index) << 16, 0)


def _legacy_grouped_prefab_between_brush_objects(index: int, brush_count: int) -> bytes:
    count = max(0, int(brush_count))
    child_index = max(0, int(index))
    marker_base = 0x1FE0
    if child_index == 1:
        marker = marker_base + count
    else:
        marker = marker_base + max(1, count - child_index)
    return struct.pack("<IIIII", marker, 0, 0x00010000, child_index << 16, 0)


def _legacy_prefab_tail(brush_count: int) -> bytes:
    count = max(0, int(brush_count))
    if count == 1:
        return (
            struct.pack("<II", 0x1BAB, 0)
            + _legacy_prefixed_string("Brush")
            + struct.pack("<IIII", 6, 0, 0x1B6A, 8)
            + b"\x00" * 6
        )
    marker = 0x1994 + count
    return (
        struct.pack("<IIII", marker, 0, 0x00060000, 0)
        + struct.pack("<HHHHII", 0, 0x1994, 0, 8, 0, 0)
    )


def _legacy_grouped_prefab_tail(brush_count: int, group_name: str) -> bytes:
    count = max(0, int(brush_count))
    marker_base = 0x1FE0
    brush_tail_marker = marker_base
    group_marker = marker_base + max(0, count - 1)
    root_marker = marker_base + count + 1
    return (
        struct.pack("<IIII", brush_tail_marker, 0, 0x00060000, 0)
        + b"\x00\x00"
        + struct.pack("<I", group_marker)
        + struct.pack("<I", 16)
        + _legacy_prefixed_string(group_name)
        + struct.pack("<II", 6, 0)
        + struct.pack("<I", root_marker)
        + struct.pack("<I", 8)
        + b"\x00" * 6
    )


def _legacy_brush_object_record_bytes(brush_name: str) -> bytes:
    return legacy_ed_writer.write_brush_object_record(brush_name)


def _legacy_object_property_bytes(name: str, type_code: int, flags: int, value: object) -> bytes:
    return legacy_ed_writer.write_object_property(
        legacy_ed_writer.LegacyEdObjectProperty(name, type_code, flags, value)
    )


def _legacy_property_value_bytes(type_code: int, value: object) -> bytes:
    return legacy_ed_writer.property_value_bytes(type_code, value)


def _legacy_prefixed_string(value: str) -> bytes:
    return legacy_ed_writer.prefixed_string(value)


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
