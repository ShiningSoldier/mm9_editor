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
    start_y: Optional[float] = None


@dataclass(frozen=True)
class _SourceStartSupportBrushAsset:
    brush: legacy_ed_writer.LegacyEdBrush
    summary: SurrogateEdModelSummary
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    placement: _ValidationFloorPlacement
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class _AirailObjectSpec:
    name: str
    pos: Vec3
    rail_links: Tuple[str, str, str, str] = ("", "", "", "")
    source_model_name: str = ""
    source_kind: str = "dat_helper"


@dataclass(frozen=True)
class _DoorObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_model_name: str = ""
    source_kind: str = "source_ed_oracle"
    source_child_brush_index: int = -1
    source_child_brush_name: str = ""
    child_brush: Optional[legacy_ed_writer.LegacyEdBrush] = None
    child_brush_properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...] = ()


@dataclass(frozen=True)
class _SourceEdNodeSnippet:
    node_type: Optional[int]
    brush_index: Optional[int]
    class_name: str
    properties: Dict[str, object]
    property_records: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...] = ()
    children: Tuple["_SourceEdNodeSnippet", ...] = ()


@dataclass(frozen=True)
class _CollisionHelperObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_model_name: str = ""
    source_kind: str = "source_ed_oracle"


@dataclass(frozen=True)
class _TriggerHelperObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_model_name: str = ""
    source_kind: str = "source_ed_oracle"


@dataclass(frozen=True)
class _SkyObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_kind: str = "source_ed_oracle"


@dataclass(frozen=True)
class _SoundObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_kind: str = "source_ed_oracle"


@dataclass(frozen=True)
class _GameplayTriggerObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_kind: str = "source_ed_oracle"


@dataclass(frozen=True)
class _StaticPropObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_kind: str = "source_ed_oracle"


@dataclass(frozen=True)
class _LowRiskBehaviorPropObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_kind: str = "source_ed_oracle"


@dataclass(frozen=True)
class _WallTorchObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_kind: str = "source_ed_oracle"


@dataclass(frozen=True)
class _FireObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_kind: str = "source_ed_oracle"


@dataclass(frozen=True)
class _CandlePropObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_kind: str = "source_ed_oracle"


@dataclass(frozen=True)
class _BrazierObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_kind: str = "source_ed_oracle"


@dataclass(frozen=True)
class _TreasureChestObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_kind: str = "source_ed_oracle"


@dataclass(frozen=True)
class _PropDamagerObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_kind: str = "source_ed_oracle"


@dataclass(frozen=True)
class _DestructablePropObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_kind: str = "source_ed_oracle"


@dataclass(frozen=True)
class _DestructableBrushObjectSpec:
    name: str
    class_name: str
    properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]
    source_model_name: str = ""
    source_kind: str = "dat_object"


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
) -> Tuple[bytes, SurrogateEdBuildReport]:
    """Return a zlib-wrapped ED with brush records plus a root/group/brush tree."""
    source_start_point_ed_path = _first_existing_path(
        door_source_ed_path,
        airail_source_ed_path,
        sky_source_ed_path,
        sound_source_ed_path,
        gameplay_trigger_source_ed_path,
        static_prop_source_ed_path,
        low_risk_behavior_prop_source_ed_path,
        wall_torch_source_ed_path,
        fire_source_ed_path,
        candle_prop_source_ed_path,
        brazier_source_ed_path,
        treasure_chest_source_ed_path,
        prop_damager_source_ed_path,
        destructable_prop_source_ed_path,
        collision_helper_source_ed_path,
        trigger_helper_source_ed_path,
    )
    effective_model_names = tuple(model_names)
    effective_max_models = max_models
    door_pair_notes: Tuple[str, ...] = ()
    if include_door_objects:
        effective_model_names, door_pair_notes = _expand_model_names_with_source_door_pairs(
            effective_model_names,
            source_ed_path=door_source_ed_path,
        )
        if max_models is not None and len(effective_model_names) > int(max_models):
            effective_max_models = len(effective_model_names)
    absolute, selected, error_report = _parse_selected_models_from_dat_bytes(
        data,
        source_path=source_path,
        model_names=effective_model_names,
        max_models=effective_max_models,
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
    if door_pair_notes:
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(door_pair_notes))),
        )
    brush_node_properties: List[Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]] = [
        () for _brush in brushes
    ]

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
        brush_node_properties.extend(() for _brush in patch_brushes)
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
        brush_node_properties.extend(() for _brush in shell_brushes)
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

    if include_sky_marker_brushes:
        try:
            sky_marker_brushes, sky_marker_summaries, sky_marker_node_properties, sky_marker_notes = (
                _sky_marker_brushes_from_source_ed(sky_source_ed_path)
            )
        except ValueError as exc:
            blockers = tuple(_unique_text(tuple(raw_report.blockers) + (str(exc),)))
            return b"", replace(
                raw_report,
                status="sky_marker_brush_generation_failed",
                generated_byte_count=0,
                processor_readiness="full_world_skeleton_generation_failed",
                blockers=blockers,
            )
        brushes = tuple(brushes) + tuple(sky_marker_brushes)
        brush_node_properties.extend(sky_marker_node_properties)
        for sky_brush in sky_marker_brushes:
            raw_bytes += legacy_ed_writer.write_brush_record(sky_brush)
        raw_report = replace(
            raw_report,
            model_count=raw_report.model_count + len(sky_marker_summaries),
            point_count=raw_report.point_count + sum(item.point_count for item in sky_marker_summaries),
            polygon_count=raw_report.polygon_count + sum(item.polygon_count for item in sky_marker_summaries),
            generated_byte_count=len(raw_bytes),
            model_summaries=tuple(raw_report.model_summaries) + tuple(sky_marker_summaries),
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(sky_marker_notes))),
        )

    if include_sky_marker_residue_brushes:
        try:
            sky_marker_residue_brushes, sky_marker_residue_summaries, sky_marker_residue_node_properties, sky_marker_residue_notes = (
                _sky_marker_residue_brushes_from_source_ed(
                    sky_source_ed_path,
                    reference_dat_path=sky_marker_residue_reference_dat_path,
                )
            )
        except ValueError as exc:
            blockers = tuple(_unique_text(tuple(raw_report.blockers) + (str(exc),)))
            return b"", replace(
                raw_report,
                status="sky_marker_residue_brush_generation_failed",
                generated_byte_count=0,
                processor_readiness="full_world_skeleton_generation_failed",
                blockers=blockers,
            )
        brushes = tuple(brushes) + tuple(sky_marker_residue_brushes)
        brush_node_properties.extend(sky_marker_residue_node_properties)
        for sky_brush in sky_marker_residue_brushes:
            raw_bytes += legacy_ed_writer.write_brush_record(sky_brush)
        raw_report = replace(
            raw_report,
            model_count=raw_report.model_count + len(sky_marker_residue_summaries),
            point_count=raw_report.point_count + sum(item.point_count for item in sky_marker_residue_summaries),
            polygon_count=raw_report.polygon_count + sum(item.polygon_count for item in sky_marker_residue_summaries),
            generated_byte_count=len(raw_bytes),
            model_summaries=tuple(raw_report.model_summaries) + tuple(sky_marker_residue_summaries),
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(sky_marker_residue_notes))),
        )

    collision_helper_objects: Tuple[_CollisionHelperObjectSpec, ...] = ()
    if include_collision_helper_objects:
        try:
            helper_brushes, helper_summaries, collision_helper_objects, helper_notes = (
                _collision_helper_assets_from_dat_bytes(
                    data,
                    source_ed_path=collision_helper_source_ed_path,
                    selected_model_names=raw_report.selected_model_names,
                    include_brushes=include_collision_helper_brushes,
                )
            )
        except ValueError as exc:
            blockers = tuple(_unique_text(tuple(raw_report.blockers) + (str(exc),)))
            return b"", replace(
                raw_report,
                status="collision_helper_generation_failed",
                generated_byte_count=0,
                processor_readiness="full_world_skeleton_generation_failed",
                blockers=blockers,
            )
        brushes = tuple(brushes) + tuple(helper_brushes)
        brush_node_properties.extend(() for _brush in helper_brushes)
        for helper in helper_brushes:
            raw_bytes += legacy_ed_writer.write_brush_record(helper)
        raw_report = replace(
            raw_report,
            model_count=raw_report.model_count + len(helper_summaries),
            point_count=raw_report.point_count + sum(item.point_count for item in helper_summaries),
            polygon_count=raw_report.polygon_count + sum(item.polygon_count for item in helper_summaries),
            generated_byte_count=len(raw_bytes),
            model_summaries=tuple(raw_report.model_summaries) + tuple(helper_summaries),
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(helper_notes))),
        )

    trigger_helper_objects: Tuple[_TriggerHelperObjectSpec, ...] = ()
    if include_trigger_helper_objects:
        try:
            trigger_brushes, trigger_summaries, trigger_helper_objects, trigger_notes = (
                _trigger_helper_assets_from_dat_bytes(
                    data,
                    source_ed_path=trigger_helper_source_ed_path,
                    selected_model_names=raw_report.selected_model_names,
                    include_brushes=include_trigger_helper_brushes,
                )
            )
        except ValueError as exc:
            blockers = tuple(_unique_text(tuple(raw_report.blockers) + (str(exc),)))
            return b"", replace(
                raw_report,
                status="trigger_helper_generation_failed",
                generated_byte_count=0,
                processor_readiness="full_world_skeleton_generation_failed",
                blockers=blockers,
            )
        brushes = tuple(brushes) + tuple(trigger_brushes)
        brush_node_properties.extend(() for _brush in trigger_brushes)
        for helper in trigger_brushes:
            raw_bytes += legacy_ed_writer.write_brush_record(helper)
        raw_report = replace(
            raw_report,
            model_count=raw_report.model_count + len(trigger_summaries),
            point_count=raw_report.point_count + sum(item.point_count for item in trigger_summaries),
            polygon_count=raw_report.polygon_count + sum(item.polygon_count for item in trigger_summaries),
            generated_byte_count=len(raw_bytes),
            model_summaries=tuple(raw_report.model_summaries) + tuple(trigger_summaries),
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(trigger_notes))),
        )

    if include_validation_floor:
        floor_brush, floor_summary, floor_placement = _validation_floor_brush_for_brushes(
            brushes,
            name=validation_floor_name,
            margin=validation_floor_margin,
            thickness=validation_floor_thickness,
            texture_name=validation_floor_texture,
        )
        brushes = tuple(brushes) + (floor_brush,)
        brush_node_properties.append(())
        raw_bytes = raw_bytes + legacy_ed_writer.write_brush_record(floor_brush)
        raw_report = replace(
            raw_report,
            model_count=raw_report.model_count + 1,
            point_count=raw_report.point_count + floor_summary.point_count,
            polygon_count=raw_report.polygon_count + floor_summary.polygon_count,
            generated_byte_count=len(raw_bytes),
            model_summaries=tuple(raw_report.model_summaries) + (floor_summary,),
        )

    door_objects: Tuple[_DoorObjectSpec, ...] = ()
    if include_door_objects:
        door_objects, door_notes = _door_object_specs_from_source_ed(
            door_source_ed_path,
            candidate_names=raw_report.selected_model_names,
        )
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(door_notes))),
        )
        if door_objects:
            brushes, brush_node_properties, raw_bytes, raw_report = (
                _replace_matching_door_brushes_with_source_children(
                    brushes,
                    brush_node_properties,
                    raw_bytes,
                    raw_report,
                    door_objects=door_objects,
                    brush_name_prefix=brush_name_prefix,
                )
            )

    airail_objects: Tuple[_AirailObjectSpec, ...] = ()
    if include_airail_objects:
        airail_objects, airail_notes = _airail_object_specs_from_dat_bytes(
            data,
            source_ed_path=airail_source_ed_path,
        )
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(airail_notes))),
        )

    sky_objects: Tuple[_SkyObjectSpec, ...] = ()
    if include_sky_objects:
        sky_objects, sky_notes = _sky_object_specs_from_source_ed(sky_source_ed_path)
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(sky_notes))),
        )

    sound_objects: Tuple[_SoundObjectSpec, ...] = ()
    if include_sound_objects:
        sound_objects, sound_notes = _sound_object_specs_from_source_ed(sound_source_ed_path)
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(sound_notes))),
        )

    gameplay_trigger_objects: Tuple[_GameplayTriggerObjectSpec, ...] = ()
    if include_gameplay_trigger_objects:
        gameplay_trigger_objects, gameplay_trigger_notes = _gameplay_trigger_object_specs_from_source_ed(
            gameplay_trigger_source_ed_path
        )
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(gameplay_trigger_notes))),
        )

    static_prop_objects: Tuple[_StaticPropObjectSpec, ...] = ()
    if include_static_prop_objects:
        static_prop_objects, static_prop_notes = _static_prop_object_specs_from_source_ed(
            static_prop_source_ed_path
        )
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(static_prop_notes))),
        )

    low_risk_behavior_prop_objects: Tuple[_LowRiskBehaviorPropObjectSpec, ...] = ()
    if include_low_risk_behavior_prop_objects:
        low_risk_behavior_prop_objects, low_risk_behavior_prop_notes = (
            _low_risk_behavior_prop_object_specs_from_source_ed(
                low_risk_behavior_prop_source_ed_path
            )
        )
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(low_risk_behavior_prop_notes))),
        )

    wall_torch_objects: Tuple[_WallTorchObjectSpec, ...] = ()
    if include_wall_torch_objects:
        wall_torch_objects, wall_torch_notes = _wall_torch_object_specs_from_source_ed(
            wall_torch_source_ed_path
        )
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(wall_torch_notes))),
        )

    fire_objects: Tuple[_FireObjectSpec, ...] = ()
    if include_fire_objects:
        fire_objects, fire_notes = _fire_object_specs_from_source_ed(fire_source_ed_path)
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(fire_notes))),
        )

    candle_prop_objects: Tuple[_CandlePropObjectSpec, ...] = ()
    if include_candle_prop_objects:
        candle_prop_objects, candle_prop_notes = _candle_prop_object_specs_from_source_ed(
            candle_prop_source_ed_path
        )
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(candle_prop_notes))),
        )

    brazier_objects: Tuple[_BrazierObjectSpec, ...] = ()
    if include_brazier_objects:
        brazier_objects, brazier_notes = _brazier_object_specs_from_source_ed(
            brazier_source_ed_path
        )
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(brazier_notes))),
        )

    treasure_chest_objects: Tuple[_TreasureChestObjectSpec, ...] = ()
    if include_treasure_chest_objects:
        treasure_chest_objects, treasure_chest_notes = _treasure_chest_object_specs_from_source_ed(
            treasure_chest_source_ed_path
        )
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(treasure_chest_notes))),
        )

    prop_damager_objects: Tuple[_PropDamagerObjectSpec, ...] = ()
    if include_prop_damager_objects:
        prop_damager_objects, prop_damager_notes = _prop_damager_object_specs_from_source_ed(
            prop_damager_source_ed_path
        )
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(prop_damager_notes))),
        )

    destructable_prop_objects: Tuple[_DestructablePropObjectSpec, ...] = ()
    if include_destructable_prop_objects:
        destructable_prop_objects, destructable_prop_notes = _destructable_prop_object_specs_from_source_ed(
            destructable_prop_source_ed_path
        )
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(destructable_prop_notes))),
        )

    destructable_brush_objects: Tuple[_DestructableBrushObjectSpec, ...] = ()
    if include_destructable_brush_objects:
        destructable_brush_objects, destructable_brush_notes = _destructable_brush_object_specs_from_dat_bytes(
            data,
            selected_model_names=raw_report.selected_model_names,
        )
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(destructable_brush_notes))),
        )

    if source_start_point_ed_path and include_physics_shell_patch:
        source_start_points, source_start_notes = _source_ed_start_point_positions(
            source_start_point_ed_path
        )
        if source_start_points:
            source_support_asset, source_support_notes = _source_start_point_support_brush_asset_from_source_ed(
                source_start_point_ed_path,
                source_start_points,
            )
            source_start_notes = tuple(source_start_notes) + tuple(source_support_notes)
            if source_support_asset is not None:
                (
                    brushes,
                    brush_node_properties,
                    raw_bytes,
                    raw_report,
                    floor_placement,
                    apply_notes,
                ) = _apply_source_start_support_brush_asset(
                    brushes,
                    brush_node_properties,
                    raw_bytes,
                    raw_report,
                    source_support_asset,
                    replacement_name_prefix=physics_shell_name_prefix,
                )
                source_start_notes = tuple(source_start_notes) + tuple(apply_notes)
            else:
                source_start_placement = _source_start_point_floor_placement(
                    brushes,
                    source_start_points,
                )
                if source_start_placement is not None:
                    floor_placement = source_start_placement[0]
                    source_start_notes = tuple(source_start_notes) + (source_start_placement[1],)
        raw_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(source_start_notes))),
        )

    written_summaries = [summary for summary in raw_report.model_summaries if summary.status == "written"]
    brush_names = tuple(
        _full_world_skeleton_brush_name(summary.name, index, brush_name_prefix)
        for index, summary in enumerate(written_summaries)
    )
    brush_source_model_names = tuple(summary.name for summary in written_summaries)
    node_hierarchy = _full_world_skeleton_node_hierarchy(
        brush_names,
        group_name=group_name,
        brush_source_model_names=brush_source_model_names,
        brush_node_properties=brush_node_properties,
        object_positions=_full_world_skeleton_object_positions(
            brushes,
            start_floor=floor_placement,
        ),
        door_objects=door_objects,
        airail_objects=airail_objects,
        sky_objects=sky_objects,
        sound_objects=sound_objects,
        gameplay_trigger_objects=gameplay_trigger_objects,
        static_prop_objects=static_prop_objects,
        low_risk_behavior_prop_objects=low_risk_behavior_prop_objects,
        wall_torch_objects=wall_torch_objects,
        fire_objects=fire_objects,
        candle_prop_objects=candle_prop_objects,
        brazier_objects=brazier_objects,
        treasure_chest_objects=treasure_chest_objects,
        prop_damager_objects=prop_damager_objects,
        destructable_prop_objects=destructable_prop_objects,
        destructable_brush_objects=destructable_brush_objects,
        collision_helper_objects=collision_helper_objects,
        trigger_helper_objects=trigger_helper_objects,
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
    if include_airail_objects:
        notes += (
            f"Generated AIRail object records: {len(airail_objects)}.",
            "AIRail object records are derived from DAT aiRail helper geometry; source ED oracle links are used when supplied.",
        )
    if include_door_objects:
        notes += (
            f"Generated Door/RotatingDoor object records: {len(door_objects)}.",
            "Door object records are copied from the source ED oracle only when their Name matches a selected DAT world model.",
            "Matching Brush nodes are nested under their Door/RotatingDoor object; when the source hierarchy exposes a child Brush, that source Brush record, projection, and flags replace the DAT-derived fallback.",
        )
    if include_sky_objects:
        notes += (
            f"Generated sky object records: {len(sky_objects)}.",
            "Sky object records are copied from the source ED oracle.",
        )
    if include_sky_marker_brushes:
        notes += (
            "Generated SkyMarker Brush records from the source ED oracle.",
            "SkyMarker Brush records preserve source ED SkyMarker.dtx geometry, surface flags, and Brush object flags.",
        )
    if include_sky_marker_residue_brushes:
        notes += (
            "Generated diagnostic SkyMarker residue Brush records from source ED faces matched to a compiled DAT reference.",
            "SkyMarker residue Brush records are not a game-bound default; compile and run helper leakage diagnostics before manual game testing.",
        )
    if include_sound_objects:
        notes += (
            f"Generated AmbientSound object records: {len(sound_objects)}.",
            "AmbientSound object records are copied from the source ED oracle; SoundOnly helper Brush volumes are not emitted.",
        )
    if include_gameplay_trigger_objects:
        notes += (
            f"Generated gameplay trigger object records: {len(gameplay_trigger_objects)}.",
            "Gameplay Trigger/ExitTrigger/PortalTrigger object records are copied from the source ED oracle without helper Brush geometry.",
        )
    if include_static_prop_objects:
        notes += (
            f"Generated static Prop object records: {len(static_prop_objects)}.",
            "Static Prop object records are copied from the source ED oracle; behavior-rich prop subclasses are left for later semantic passes.",
        )
    if include_low_risk_behavior_prop_objects:
        notes += (
            f"Generated low-risk behavior prop object records: {len(low_risk_behavior_prop_objects)}.",
            "Low-risk behavior prop object records are copied from the source ED oracle for physical-decor subclasses only; behavior-rich subclasses are handled by their own class-specific passes.",
        )
    if include_wall_torch_objects:
        notes += (
            f"Generated WallTorch object records: {len(wall_torch_objects)}.",
            "WallTorch object records are copied from the source ED oracle as a validated medium-risk light/fire/sound pass.",
        )
    if include_fire_objects:
        notes += (
            f"Generated Fire object records: {len(fire_objects)}.",
            "Fire object records are copied from the source ED oracle as a validated standalone medium-risk light/fire/sound pass.",
        )
    if include_candle_prop_objects:
        notes += (
            f"Generated CandleProp object records: {len(candle_prop_objects)}.",
            "CandleProp object records are copied from the source ED oracle as a validated medium-risk light/model prop pass.",
        )
    if include_brazier_objects:
        notes += (
            f"Generated Brazier object records: {len(brazier_objects)}.",
            "Brazier object records are copied from the source ED oracle as a validated medium-risk light/fire/sound/model prop pass.",
        )
    if include_treasure_chest_objects:
        gated_classes = []
        if not include_prop_damager_objects:
            gated_classes.append("PropDamager")
        if not include_destructable_prop_objects:
            gated_classes.append("DestructableProp")
        gated_suffix = (
            f"; {', '.join(gated_classes)} remain separately gated."
            if gated_classes
            else "."
        )
        notes += (
            f"Generated TreasureChest object records: {len(treasure_chest_objects)}.",
            "TreasureChest object records are copied from the source ED oracle as a validated high-risk loot/trigger pass"
            + gated_suffix,
        )
    if include_prop_damager_objects:
        gated_suffix = (
            "; DestructableProp remains separately gated."
            if not include_destructable_prop_objects
            else "."
        )
        notes += (
            f"Generated PropDamager object records: {len(prop_damager_objects)}.",
            "PropDamager object records are copied from the source ED oracle as a validated high-risk damage pass"
            + gated_suffix,
        )
    if include_destructable_prop_objects:
        notes += (
            f"Generated DestructableProp object records: {len(destructable_prop_objects)}.",
            "DestructableProp object records are copied from the source ED oracle as a validated high-risk destructible behavior pass.",
        )
    if include_destructable_brush_objects:
        notes += (
            f"Generated DestructableBrush object records: {len(destructable_brush_objects)}.",
            "DestructableBrush object records are copied from DAT object records and nested around same-name DAT-derived Brush children.",
        )
    if include_collision_helper_objects:
        notes += (f"Generated collision helper object records: {len(collision_helper_objects)}.",)
        if include_collision_helper_brushes:
            notes += (
                "Collision helper Brush records are derived from DAT Invisible/Firethrough helper geometry; object properties use the source ED oracle when supplied.",
            )
        else:
            notes += (
                "Collision helper Brush records were intentionally skipped; only source ED object nodes are emitted.",
            )
    if include_trigger_helper_objects:
        notes += (f"Generated trigger helper object records: {len(trigger_helper_objects)}.",)
        if include_trigger_helper_brushes:
            notes += (
                "Trigger helper Brush records are derived from DAT GreenScreen helper geometry; object properties use the source ED oracle when supplied.",
            )
        else:
            notes += (
                "Trigger helper Brush records were intentionally skipped; only source ED PortalZone object nodes are emitted.",
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
        include_door_objects=include_door_objects,
        door_source_ed_path=door_source_ed_path,
        include_airail_objects=include_airail_objects,
        airail_source_ed_path=airail_source_ed_path,
        include_sky_objects=include_sky_objects,
        sky_source_ed_path=sky_source_ed_path,
        include_sky_marker_brushes=include_sky_marker_brushes,
        include_sky_marker_residue_brushes=include_sky_marker_residue_brushes,
        sky_marker_residue_reference_dat_path=sky_marker_residue_reference_dat_path,
        include_sound_objects=include_sound_objects,
        sound_source_ed_path=sound_source_ed_path,
        include_gameplay_trigger_objects=include_gameplay_trigger_objects,
        gameplay_trigger_source_ed_path=gameplay_trigger_source_ed_path,
        include_static_prop_objects=include_static_prop_objects,
        static_prop_source_ed_path=static_prop_source_ed_path,
        include_low_risk_behavior_prop_objects=include_low_risk_behavior_prop_objects,
        low_risk_behavior_prop_source_ed_path=low_risk_behavior_prop_source_ed_path,
        include_wall_torch_objects=include_wall_torch_objects,
        wall_torch_source_ed_path=wall_torch_source_ed_path,
        include_fire_objects=include_fire_objects,
        fire_source_ed_path=fire_source_ed_path,
        include_candle_prop_objects=include_candle_prop_objects,
        candle_prop_source_ed_path=candle_prop_source_ed_path,
        include_brazier_objects=include_brazier_objects,
        brazier_source_ed_path=brazier_source_ed_path,
        include_treasure_chest_objects=include_treasure_chest_objects,
        treasure_chest_source_ed_path=treasure_chest_source_ed_path,
        include_prop_damager_objects=include_prop_damager_objects,
        prop_damager_source_ed_path=prop_damager_source_ed_path,
        include_destructable_prop_objects=include_destructable_prop_objects,
        destructable_prop_source_ed_path=destructable_prop_source_ed_path,
        include_destructable_brush_objects=include_destructable_brush_objects,
        include_collision_helper_objects=include_collision_helper_objects,
        include_collision_helper_brushes=include_collision_helper_brushes,
        collision_helper_source_ed_path=collision_helper_source_ed_path,
        include_trigger_helper_objects=include_trigger_helper_objects,
        include_trigger_helper_brushes=include_trigger_helper_brushes,
        trigger_helper_source_ed_path=trigger_helper_source_ed_path,
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


def _replace_matching_door_brushes_with_source_children(
    brushes: Sequence[legacy_ed_writer.LegacyEdBrush],
    brush_node_properties: Sequence[Sequence[legacy_ed_writer.LegacyEdObjectProperty]],
    raw_bytes: bytes,
    raw_report: SurrogateEdBuildReport,
    *,
    door_objects: Sequence[_DoorObjectSpec],
    brush_name_prefix: str,
) -> Tuple[
    Tuple[legacy_ed_writer.LegacyEdBrush, ...],
    List[Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]],
    bytes,
    SurrogateEdBuildReport,
]:
    written_summary_indices = [
        index for index, summary in enumerate(raw_report.model_summaries)
        if summary.status == "written"
    ]
    written_summaries = [raw_report.model_summaries[index] for index in written_summary_indices]
    source_name_to_brush_index: Dict[str, int] = {}
    for index, summary in enumerate(written_summaries):
        key = str(summary.name or "").lower()
        if key and key not in source_name_to_brush_index:
            source_name_to_brush_index[key] = index

    brush_list = list(brushes)
    property_list = [tuple(properties) for properties in brush_node_properties]
    while len(property_list) < len(brush_list):
        property_list.append(())

    summary_list = list(raw_report.model_summaries)
    replaced_count = 0
    bounds_skip_names: List[str] = []
    for item in door_objects:
        if item.child_brush is None:
            continue
        key = str(item.source_model_name or item.name or "").lower()
        brush_index = source_name_to_brush_index.get(key)
        if brush_index is None or brush_index >= len(brush_list):
            continue
        target_name = _full_world_skeleton_brush_name(
            written_summaries[brush_index].name,
            brush_index,
            brush_name_prefix,
        )
        child_brush = replace(item.child_brush, name=target_name)
        if not _legacy_brush_bounds_match(child_brush, brush_list[brush_index]):
            bounds_skip_names.append(item.name or item.source_model_name or target_name)
            continue
        brush_list[brush_index] = child_brush
        if item.child_brush_properties:
            property_list[brush_index] = _writer_object_properties_with_name(
                item.child_brush_properties,
                target_name,
            )
        else:
            property_list[brush_index] = legacy_ed_writer.full_world_brush_node_properties(target_name)

        summary_index = written_summary_indices[brush_index]
        old_summary = summary_list[summary_index]
        source_name = item.source_child_brush_name or f"source brush {item.source_child_brush_index}"
        summary_list[summary_index] = replace(
            old_summary,
            point_count=len(child_brush.points),
            polygon_count=len(child_brush.surfaces),
            texture_count=len({surface.texture_name for surface in child_brush.surfaces}),
            byte_count=len(legacy_ed_writer.write_brush_record(child_brush)),
            notes=tuple(_unique_text(tuple(old_summary.notes) + (
                f"door child Brush copied from source ED {source_name}",
            ))),
        )
        replaced_count += 1

    replacement_notes: List[str] = []
    if bounds_skip_names:
        preview = ", ".join(bounds_skip_names[:8])
        suffix = "" if len(bounds_skip_names) <= 8 else f", +{len(bounds_skip_names) - 8} more"
        replacement_notes.append(
            "Door source ED child Brush replacement skipped for "
            f"{len(bounds_skip_names)} object(s) because source bounds did not match DAT model bounds: "
            f"{preview}{suffix}."
        )

    if not replaced_count:
        if not replacement_notes:
            return tuple(brush_list), property_list, raw_bytes, raw_report
        updated_report = replace(
            raw_report,
            notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(replacement_notes))),
        )
        return tuple(brush_list), property_list, raw_bytes, updated_report

    rebuilt_raw_bytes = legacy_ed_writer.build_raw_brush_stream(brush_list)
    written_summaries_after = [summary for summary in summary_list if summary.status == "written"]
    replacement_notes.append(
        f"Door source ED child Brush replacement applied to {replaced_count} Brush record(s)."
    )
    updated_report = replace(
        raw_report,
        generated_byte_count=len(rebuilt_raw_bytes),
        point_count=sum(summary.point_count for summary in written_summaries_after),
        polygon_count=sum(summary.polygon_count for summary in written_summaries_after),
        model_summaries=tuple(summary_list),
        notes=tuple(_unique_text(tuple(raw_report.notes) + tuple(replacement_notes))),
    )
    return tuple(brush_list), property_list, rebuilt_raw_bytes, updated_report


def _legacy_brush_bounds_match(
    source_brush: legacy_ed_writer.LegacyEdBrush,
    target_brush: legacy_ed_writer.LegacyEdBrush,
    *,
    tolerance: float = 2.0,
) -> bool:
    source_bounds = _legacy_brush_bounds(source_brush)
    target_bounds = _legacy_brush_bounds(target_brush)
    if source_bounds is None or target_bounds is None:
        return False
    for axis in range(3):
        source_min, source_max = source_bounds[axis]
        target_min, target_max = target_bounds[axis]
        source_span = source_max - source_min
        target_span = target_max - target_min
        source_center = (source_min + source_max) * 0.5
        target_center = (target_min + target_max) * 0.5
        if abs(source_span - target_span) > tolerance:
            return False
        if abs(source_center - target_center) > tolerance:
            return False
    return True


def _legacy_brush_bounds(
    brush: legacy_ed_writer.LegacyEdBrush,
) -> Optional[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]]]:
    points = tuple(getattr(brush, "points", ()) or ())
    if not points:
        return None
    bounds = [
        (min(float(point[axis]) for point in points), max(float(point[axis]) for point in points))
        for axis in range(3)
    ]
    return bounds[0], bounds[1], bounds[2]


def _full_world_skeleton_node_hierarchy(
    brush_names: Sequence[str],
    *,
    group_name: str,
    object_positions: _FullWorldSkeletonObjectPositions,
    brush_source_model_names: Sequence[str] = (),
    brush_node_properties: Sequence[Sequence[legacy_ed_writer.LegacyEdObjectProperty]] = (),
    door_objects: Sequence[_DoorObjectSpec] = (),
    airail_objects: Sequence[_AirailObjectSpec] = (),
    sky_objects: Sequence[_SkyObjectSpec] = (),
    sound_objects: Sequence[_SoundObjectSpec] = (),
    gameplay_trigger_objects: Sequence[_GameplayTriggerObjectSpec] = (),
    static_prop_objects: Sequence[_StaticPropObjectSpec] = (),
    low_risk_behavior_prop_objects: Sequence[_LowRiskBehaviorPropObjectSpec] = (),
    wall_torch_objects: Sequence[_WallTorchObjectSpec] = (),
    fire_objects: Sequence[_FireObjectSpec] = (),
    candle_prop_objects: Sequence[_CandlePropObjectSpec] = (),
    brazier_objects: Sequence[_BrazierObjectSpec] = (),
    treasure_chest_objects: Sequence[_TreasureChestObjectSpec] = (),
    prop_damager_objects: Sequence[_PropDamagerObjectSpec] = (),
    destructable_prop_objects: Sequence[_DestructablePropObjectSpec] = (),
    destructable_brush_objects: Sequence[_DestructableBrushObjectSpec] = (),
    collision_helper_objects: Sequence[_CollisionHelperObjectSpec] = (),
    trigger_helper_objects: Sequence[_TriggerHelperObjectSpec] = (),
) -> bytes:
    def make_brush_node(brush_index: int, node_id: int) -> legacy_ed_writer.LegacyEdNode:
        name = str(brush_names[brush_index])
        return legacy_ed_writer.brush_node(
            brush_index,
            name,
            node_id=node_id,
            properties=(
                _writer_object_properties_with_name(brush_node_properties[brush_index], name)
                if brush_index < len(brush_node_properties) and brush_node_properties[brush_index]
                else legacy_ed_writer.full_world_brush_node_properties(name)
            ),
        )

    source_name_to_brush_index: Dict[str, int] = {}
    for index, source_name in enumerate(brush_source_model_names):
        key = str(source_name or "").lower()
        if key and key not in source_name_to_brush_index:
            source_name_to_brush_index[key] = index
    door_brush_indices = {
        source_name_to_brush_index[key]
        for item in door_objects
        for key in (str(item.source_model_name or item.name or "").lower(),)
        if key in source_name_to_brush_index
    }
    destructable_brush_indices = {
        source_name_to_brush_index[key]
        for item in destructable_brush_objects
        for key in (str(item.source_model_name or item.name or "").lower(),)
        if key in source_name_to_brush_index
    }
    next_node_id = 3
    brush_nodes_list: List[legacy_ed_writer.LegacyEdNode] = []
    for index, _name in enumerate(brush_names):
        if index in door_brush_indices or index in destructable_brush_indices:
            continue
        brush_nodes_list.append(make_brush_node(index, next_node_id))
        next_node_id += 1
    brush_nodes = tuple(brush_nodes_list)
    group = legacy_ed_writer.group_node(
        str(group_name or "GeneratedWorldModels"),
        brush_nodes,
        node_id=2,
        unknown2=16,
    )
    gameplay_nodes = (
        legacy_ed_writer.object_node(
            "WorldProperties",
            "",
            node_id=next_node_id,
            properties=legacy_ed_writer.world_properties_object_properties(
                pos=object_positions.world_properties,
            ),
        ),
        legacy_ed_writer.object_node(
            "StartPoint",
            "",
            node_id=next_node_id + 1,
            properties=legacy_ed_writer.start_point_object_properties(
                pos=object_positions.start_point,
            ),
        ),
        legacy_ed_writer.object_node(
            "Light",
            "",
            node_id=next_node_id + 2,
            properties=legacy_ed_writer.light_object_properties(
                pos=object_positions.light,
            ),
        ),
    )
    next_node_id += 3
    door_nodes_list: List[legacy_ed_writer.LegacyEdNode] = []
    for item in door_objects:
        door_node_id = next_node_id
        next_node_id += 1
        key = str(item.source_model_name or item.name or "").lower()
        children: Tuple[legacy_ed_writer.LegacyEdNode, ...] = ()
        if key in source_name_to_brush_index:
            children = (make_brush_node(source_name_to_brush_index[key], next_node_id),)
            next_node_id += 1
        door_nodes_list.append(legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=door_node_id,
            properties=item.properties,
            children=children,
        ))
    door_nodes = tuple(door_nodes_list)
    airail_start_node_id = next_node_id
    airail_nodes = tuple(
        legacy_ed_writer.object_node(
            "AIRail",
            "",
            node_id=airail_start_node_id + index,
            properties=legacy_ed_writer.airail_object_properties(
                name=item.name,
                pos=item.pos,
                rail_links=item.rail_links,
            ),
        )
        for index, item in enumerate(airail_objects)
    )
    sky_start_node_id = airail_start_node_id + len(airail_nodes)
    sky_nodes = tuple(
        legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=sky_start_node_id + index,
            properties=item.properties,
        )
        for index, item in enumerate(sky_objects)
    )
    sound_start_node_id = sky_start_node_id + len(sky_nodes)
    sound_nodes = tuple(
        legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=sound_start_node_id + index,
            properties=item.properties,
        )
        for index, item in enumerate(sound_objects)
    )
    gameplay_trigger_start_node_id = sound_start_node_id + len(sound_nodes)
    gameplay_trigger_nodes = tuple(
        legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=gameplay_trigger_start_node_id + index,
            properties=item.properties,
        )
        for index, item in enumerate(gameplay_trigger_objects)
    )
    static_prop_start_node_id = gameplay_trigger_start_node_id + len(gameplay_trigger_nodes)
    static_prop_nodes = tuple(
        legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=static_prop_start_node_id + index,
            properties=item.properties,
        )
        for index, item in enumerate(static_prop_objects)
    )
    low_risk_behavior_prop_start_node_id = static_prop_start_node_id + len(static_prop_nodes)
    low_risk_behavior_prop_nodes = tuple(
        legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=low_risk_behavior_prop_start_node_id + index,
            properties=item.properties,
        )
        for index, item in enumerate(low_risk_behavior_prop_objects)
    )
    wall_torch_start_node_id = low_risk_behavior_prop_start_node_id + len(low_risk_behavior_prop_nodes)
    wall_torch_nodes = tuple(
        legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=wall_torch_start_node_id + index,
            properties=item.properties,
        )
        for index, item in enumerate(wall_torch_objects)
    )
    fire_start_node_id = wall_torch_start_node_id + len(wall_torch_nodes)
    fire_nodes = tuple(
        legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=fire_start_node_id + index,
            properties=item.properties,
        )
        for index, item in enumerate(fire_objects)
    )
    candle_prop_start_node_id = fire_start_node_id + len(fire_nodes)
    candle_prop_nodes = tuple(
        legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=candle_prop_start_node_id + index,
            properties=item.properties,
        )
        for index, item in enumerate(candle_prop_objects)
    )
    brazier_start_node_id = candle_prop_start_node_id + len(candle_prop_nodes)
    brazier_nodes = tuple(
        legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=brazier_start_node_id + index,
            properties=item.properties,
        )
        for index, item in enumerate(brazier_objects)
    )
    treasure_chest_start_node_id = brazier_start_node_id + len(brazier_nodes)
    treasure_chest_nodes = tuple(
        legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=treasure_chest_start_node_id + index,
            properties=item.properties,
        )
        for index, item in enumerate(treasure_chest_objects)
    )
    prop_damager_start_node_id = treasure_chest_start_node_id + len(treasure_chest_nodes)
    prop_damager_nodes = tuple(
        legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=prop_damager_start_node_id + index,
            properties=item.properties,
        )
        for index, item in enumerate(prop_damager_objects)
    )
    destructable_prop_start_node_id = prop_damager_start_node_id + len(prop_damager_nodes)
    destructable_prop_nodes = tuple(
        legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=destructable_prop_start_node_id + index,
            properties=item.properties,
        )
        for index, item in enumerate(destructable_prop_objects)
    )
    next_node_id = destructable_prop_start_node_id + len(destructable_prop_nodes)
    destructable_brush_nodes_list: List[legacy_ed_writer.LegacyEdNode] = []
    for item in destructable_brush_objects:
        destructable_brush_node_id = next_node_id
        next_node_id += 1
        key = str(item.source_model_name or item.name or "").lower()
        children: Tuple[legacy_ed_writer.LegacyEdNode, ...] = ()
        if key in source_name_to_brush_index:
            children = (make_brush_node(source_name_to_brush_index[key], next_node_id),)
            next_node_id += 1
        destructable_brush_nodes_list.append(legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=destructable_brush_node_id,
            properties=item.properties,
            children=children,
        ))
    destructable_brush_nodes = tuple(destructable_brush_nodes_list)
    collision_start_node_id = next_node_id
    collision_helper_nodes = tuple(
        legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=collision_start_node_id + index,
            properties=item.properties,
        )
        for index, item in enumerate(collision_helper_objects)
    )
    trigger_start_node_id = collision_start_node_id + len(collision_helper_nodes)
    trigger_helper_nodes = tuple(
        legacy_ed_writer.object_node(
            item.class_name,
            "",
            node_id=trigger_start_node_id + index,
            properties=item.properties,
        )
        for index, item in enumerate(trigger_helper_objects)
    )
    root = legacy_ed_writer.world_root_node(
        (
            (group,)
            + gameplay_nodes
            + door_nodes
            + airail_nodes
            + sky_nodes
            + sound_nodes
            + gameplay_trigger_nodes
            + static_prop_nodes
            + low_risk_behavior_prop_nodes
            + wall_torch_nodes
            + fire_nodes
            + candle_prop_nodes
            + brazier_nodes
            + treasure_chest_nodes
            + prop_damager_nodes
            + destructable_prop_nodes
            + destructable_brush_nodes
            + collision_helper_nodes
            + trigger_helper_nodes
        ),
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
            float(start_floor.start_y) if start_floor.start_y is not None else float(start_floor.top_y) + 128.0,
            float(start_floor.center[2]),
        )
    return _FullWorldSkeletonObjectPositions(
        world_properties=(center[0], max_y + 512.0, center[2]),
        start_point=start_point,
        light=(center[0], max_y + light_clearance, center[2]),
    )


def _first_existing_path(*paths: str) -> str:
    for path in paths:
        candidate = os.path.abspath(path) if path else ""
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


def _source_ed_start_point_positions(
    source_ed_path: str,
) -> Tuple[Tuple[Tuple[str, Vec3], ...], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), ()
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (f"Source StartPoint oracle scan failed: {exc}",)

    points: List[Tuple[str, Vec3]] = []
    for record in scan.records:
        if str(record.class_name) != "StartPoint":
            continue
        raw_pos = record.property_value("Pos", None)
        if not isinstance(raw_pos, tuple) or len(raw_pos) != 3:
            continue
        name = str(record.property_value("Name", "") or f"StartPoint{len(points)}")
        points.append((name, _finite_vec3(raw_pos)))
    if not points:
        return (), (f"Source StartPoint oracle loaded 0 StartPoint record(s): {source_ed}.",)
    return tuple(points), (
        f"Source StartPoint oracle loaded {len(points)} StartPoint record(s): {source_ed}.",
    )


def _source_start_point_support_brush_asset_from_source_ed(
    source_ed_path: str,
    source_start_points: Sequence[Tuple[str, Vec3]],
) -> Tuple[Optional[_SourceStartSupportBrushAsset], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return None, ()
    try:
        scene = legacy_ed.load_legacy_ed_geometry_scene(source_ed)
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return None, (f"Source StartPoint support Brush oracle scan failed: {exc}",)

    brush_records = [record for record in scan.records if record.class_name == "Brush"]
    for source_name, source_pos in source_start_points:
        support = _source_start_point_support_candidate(
            scene.models,
            brush_records,
            source_name=source_name,
            source_pos=source_pos,
        )
        if support is None:
            continue
        model_index, model, source_record, floor_y, texture_name = support
        source_brush_name = str(
            source_record.property_value("Name", "")
            if source_record is not None
            else getattr(model, "name", "")
        ) or f"SourceStartSupport{model_index}"
        support_name = (
            "SourceStartSupport_"
            + (_safe_legacy_name_component(source_name) or "StartPoint")
            + "_"
            + (_safe_legacy_name_component(source_brush_name) or str(model_index))
        )[:120]
        brush = _legacy_brush_from_source_geometry_model(
            model,
            name=support_name,
        )
        if source_record is not None:
            properties = _writer_object_properties_from_scan(
                source_record.properties,
                overrides={"Name": support_name},
            )
        else:
            properties = legacy_ed_writer.full_world_brush_node_properties(support_name)
        placement = _ValidationFloorPlacement(
            center=(float(source_pos[0]), float(floor_y), float(source_pos[2])),
            top_y=float(floor_y),
            start_y=float(source_pos[1]),
        )
        summary = SurrogateEdModelSummary(
            name=support_name,
            status="written",
            point_count=len(brush.points),
            polygon_count=len(brush.surfaces),
            texture_count=len({surface.texture_name for surface in brush.surfaces}),
            byte_count=len(legacy_ed_writer.write_brush_record(brush)),
            notes=(f"source ED StartPoint support Brush copied from {source_brush_name}",),
        )
        return _SourceStartSupportBrushAsset(
            brush=brush,
            summary=summary,
            properties=tuple(properties),
            placement=placement,
            notes=(
                f"Source StartPoint support Brush copied for {source_name}: "
                f"{source_brush_name} at floor y={float(floor_y):.2f}, texture={texture_name}.",
            ),
        ), ()
    return None, (
        "Source StartPoint support Brush oracle found no solid non-helper floor Brush below source StartPoint record(s).",
    )


def _source_start_point_support_candidate(
    models: Sequence[object],
    brush_records: Sequence[legacy_ed.LegacyEdObjectRecord],
    *,
    source_name: str,
    source_pos: Vec3,
) -> Optional[Tuple[int, object, Optional[legacy_ed.LegacyEdObjectRecord], float, str]]:
    best: Optional[Tuple[float, int, object, Optional[legacy_ed.LegacyEdObjectRecord], str]] = None
    source_y = float(source_pos[1])
    for model_index, model in enumerate(models):
        source_record: Optional[legacy_ed.LegacyEdObjectRecord] = None
        if model_index < len(brush_records):
            source_record = brush_records[model_index]
            if source_record.property_value("Solid", True) is False:
                continue
            if source_record.property_value("Portal", False) is True:
                continue
            if source_record.property_value("Nonexistant", False) is True:
                continue
        points = tuple(_finite_vec3(point) for point in getattr(model, "points", ()) or ())
        if not points:
            continue
        for face in getattr(model, "faces", ()) or ():
            texture_name = str(getattr(face, "material_name", "") or "")
            if terrain_semantics.helper_texture_role(texture_name):
                continue
            indices = tuple(int(index) for index in getattr(face, "vertex_indices", ()) or ())
            if len(indices) < 3:
                continue
            for offset in range(1, len(indices) - 1):
                try:
                    p0 = points[indices[0]]
                    p1 = points[indices[offset]]
                    p2 = points[indices[offset + 1]]
                except IndexError:
                    continue
                if _triangle_normal_y(p0, p1, p2) <= 1.0e-7:
                    continue
                y = _point_in_triangle_xz_y(float(source_pos[0]), float(source_pos[2]), p0, p1, p2)
                if y is None or y > source_y + 8.0:
                    continue
                if best is None or y > best[0]:
                    best = (float(y), int(model_index), model, source_record, texture_name)
    if best is None:
        return None
    floor_y, model_index, model, source_record, texture_name = best
    return model_index, model, source_record, floor_y, texture_name


def _apply_source_start_support_brush_asset(
    brushes: Sequence[legacy_ed_writer.LegacyEdBrush],
    brush_node_properties: Sequence[Sequence[legacy_ed_writer.LegacyEdObjectProperty]],
    raw_bytes: bytes,
    raw_report: SurrogateEdBuildReport,
    asset: _SourceStartSupportBrushAsset,
    *,
    replacement_name_prefix: str,
) -> Tuple[
    Tuple[legacy_ed_writer.LegacyEdBrush, ...],
    List[Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]],
    bytes,
    SurrogateEdBuildReport,
    _ValidationFloorPlacement,
    Tuple[str, ...],
]:
    brush_list = list(brushes)
    property_list = [tuple(properties) for properties in brush_node_properties]
    while len(property_list) < len(brush_list):
        property_list.append(())

    written_summary_indices = [
        index for index, summary in enumerate(raw_report.model_summaries)
        if summary.status == "written"
    ]
    summary_list = list(raw_report.model_summaries)
    replacement_prefix = str(replacement_name_prefix or "")
    replace_index: Optional[int] = None
    for brush_index, existing_brush in enumerate(brush_list):
        if brush_index >= len(written_summary_indices):
            continue
        summary = summary_list[written_summary_indices[brush_index]]
        if replacement_prefix and not str(summary.name or "").startswith(replacement_prefix):
            continue
        if _legacy_brush_bounds_match(asset.brush, existing_brush, tolerance=1.0):
            replace_index = brush_index
            break

    action_note: str
    if replace_index is None:
        brush_list.append(asset.brush)
        property_list.append(tuple(asset.properties))
        summary_list.append(asset.summary)
        action_note = "Source StartPoint support Brush appended as an additional generated Brush."
    else:
        brush_list[replace_index] = asset.brush
        property_list[replace_index] = tuple(asset.properties)
        summary_index = written_summary_indices[replace_index]
        old_name = str(summary_list[summary_index].name or f"Brush{replace_index}")
        summary_list[summary_index] = asset.summary
        action_note = f"Source StartPoint support Brush replaced generated support Brush {old_name}."

    rebuilt_raw_bytes = legacy_ed_writer.build_raw_brush_stream(brush_list)
    written_summaries = [summary for summary in summary_list if summary.status == "written"]
    updated_report = replace(
        raw_report,
        generated_byte_count=len(rebuilt_raw_bytes),
        model_count=len(written_summaries),
        point_count=sum(summary.point_count for summary in written_summaries),
        polygon_count=sum(summary.polygon_count for summary in written_summaries),
        model_summaries=tuple(summary_list),
    )
    return (
        tuple(brush_list),
        property_list,
        rebuilt_raw_bytes,
        updated_report,
        asset.placement,
        tuple(asset.notes) + (action_note,),
    )


def _source_start_point_floor_placement(
    brushes: Sequence[legacy_ed_writer.LegacyEdBrush],
    source_start_points: Sequence[Tuple[str, Vec3]],
) -> Optional[Tuple[_ValidationFloorPlacement, str]]:
    for source_name, source_pos in source_start_points:
        source_y = float(source_pos[1])
        floor_y = _raycast_brush_floor_y_at_xz(
            brushes,
            source_pos[0],
            source_pos[2],
            y_max=source_y + 8.0,
        )
        if floor_y is None:
            continue
        floor_y = float(floor_y)
        placement = _ValidationFloorPlacement(
            center=(float(source_pos[0]), floor_y, float(source_pos[2])),
            top_y=floor_y,
            start_y=source_y,
        )
        return placement, (
            f"StartPoint is anchored to source ED StartPoint {source_name} at source y={source_y:.2f} "
            f"over generated interior support floor y={floor_y:.2f}."
        )
    return None


def _raycast_brush_floor_y_at_xz(
    brushes: Sequence[legacy_ed_writer.LegacyEdBrush],
    x: float,
    z: float,
    *,
    y_max: Optional[float] = None,
) -> Optional[float]:
    hits: List[float] = []
    for brush in brushes:
        points = tuple(getattr(brush, "points", ()) or ())
        for surface in getattr(brush, "surfaces", ()) or ():
            indices = tuple(int(index) for index in getattr(surface, "vertex_indices", ()) or ())
            if len(indices) < 3:
                continue
            for offset in range(1, len(indices) - 1):
                try:
                    p0 = points[indices[0]]
                    p1 = points[indices[offset]]
                    p2 = points[indices[offset + 1]]
                except IndexError:
                    continue
                if _triangle_normal_y(p0, p1, p2) <= 1.0e-7:
                    continue
                y = _point_in_triangle_xz_y(float(x), float(z), p0, p1, p2)
                if y is not None and (y_max is None or y <= float(y_max)):
                    hits.append(y)
    return max(hits) if hits else None


def _triangle_normal_y(a: Vec3, b: Vec3, c: Vec3) -> float:
    return (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2])


def _point_in_triangle_xz_y(
    x: float,
    z: float,
    a: Vec3,
    b: Vec3,
    c: Vec3,
) -> Optional[float]:
    ax, az = float(a[0]), float(a[2])
    bx, bz = float(b[0]), float(b[2])
    cx, cz = float(c[0]), float(c[2])
    v0 = (cx - ax, cz - az)
    v1 = (bx - ax, bz - az)
    v2 = (float(x) - ax, float(z) - az)
    denom = v0[0] * v1[1] - v1[0] * v0[1]
    if abs(denom) <= 1.0e-7:
        return None
    u = (v2[0] * v1[1] - v1[0] * v2[1]) / denom
    v = (v0[0] * v2[1] - v2[0] * v0[1]) / denom
    if u < -1.0e-5 or v < -1.0e-5 or u + v > 1.00001:
        return None
    return float(a[1]) + u * (float(c[1]) - float(a[1])) + v * (float(b[1]) - float(a[1]))


_DOOR_OBJECT_CLASSES = {"Door", "RotatingDoor"}


def _door_object_specs_from_source_ed(
    source_ed_path: str,
    *,
    candidate_names: Sequence[str],
) -> Tuple[Tuple[_DoorObjectSpec, ...], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), ("Door source ED oracle was not supplied; Door/RotatingDoor object nodes will be skipped.",)
    if not os.path.exists(source_ed):
        return (), (f"Door source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (f"Door source ED oracle scan failed: {exc}",)
    try:
        scene = legacy_ed.load_legacy_ed_geometry_scene(source_ed)
    except Exception as exc:
        scene = None
        scene_note = f"Door source ED oracle geometry scan failed; child Brush records cannot be copied: {exc}"
    else:
        scene_note = ""
    try:
        door_child_brush_nodes = _source_ed_door_child_brush_nodes(source_ed)
    except Exception as exc:
        door_child_brush_nodes = {}
        hierarchy_note = f"Door source ED hierarchy scan failed; child Brush records cannot be matched: {exc}"
    else:
        hierarchy_note = ""

    wanted = {str(name or "").lower() for name in candidate_names if str(name or "")}
    brush_records = [record for record in scan.records if record.class_name == "Brush"]
    specs: List[_DoorObjectSpec] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name not in _DOOR_OBJECT_CLASSES:
            continue
        name = str(record.property_value("Name", "") or "")
        if not name or name.lower() not in wanted:
            continue
        child_node = door_child_brush_nodes.get(name.lower())
        child_brush_index = (
            int(child_node.brush_index)
            if child_node is not None and child_node.brush_index is not None
            else -1
        )
        child_brush_name = ""
        child_brush: Optional[legacy_ed_writer.LegacyEdBrush] = None
        child_brush_properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...] = ()
        if (
            child_brush_index >= 0
            and scene is not None
            and child_brush_index < len(getattr(scene, "models", ()) or ())
        ):
            child_model = scene.models[child_brush_index]
            child_brush_name = str(
                (child_node.properties.get("Name", "") if child_node is not None else "")
                or getattr(child_model, "name", "")
                or f"DoorChildBrush{child_brush_index}"
            )
            if child_node is not None:
                child_brush_properties = tuple(child_node.property_records)
            if not child_brush_properties and child_brush_index < len(brush_records):
                child_record = brush_records[child_brush_index]
                child_record_name = str(child_record.property_value("Name", "") or "")
                if child_record_name.lower() == child_brush_name.lower():
                    child_brush_properties = _writer_object_properties_from_scan(child_record.properties)
            child_brush = _legacy_brush_from_source_geometry_model(
                child_model,
                name=child_brush_name,
            )
        specs.append(_DoorObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_model_name=name,
            source_kind="source_ed_oracle",
            source_child_brush_index=child_brush_index,
            source_child_brush_name=child_brush_name,
            child_brush=child_brush,
            child_brush_properties=child_brush_properties,
        ))

    class_counts: Dict[str, int] = {}
    for item in specs:
        class_counts[item.class_name] = class_counts.get(item.class_name, 0) + 1
    detail = ", ".join(f"{name}={class_counts[name]}" for name in sorted(class_counts)) or "none"
    child_copy_count = sum(1 for item in specs if item.child_brush is not None)
    notes = [
        f"Door source ED oracle loaded {len(specs)} matched Door/RotatingDoor object record(s): {detail}.",
        f"Door source ED oracle copied source child Brush records for {child_copy_count}/{len(specs)} matched door object(s).",
    ]
    if scene_note:
        notes.append(scene_note)
    if hierarchy_note:
        notes.append(hierarchy_note)
    return tuple(specs), tuple(_unique_text(notes))


def _expand_model_names_with_source_door_pairs(
    model_names: Sequence[str],
    *,
    source_ed_path: str,
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    requested = [str(name or "").strip() for name in model_names if str(name or "").strip()]
    if not requested:
        return tuple(requested), ()
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed or not os.path.exists(source_ed):
        return tuple(requested), ()
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return tuple(requested), (
            f"Door pair expansion skipped because the source ED oracle scan failed: {exc}",
        )

    by_key = {name.lower(): name for name in requested}
    expanded = list(requested)
    added: List[str] = []
    for record in scan.records:
        if str(record.class_name) not in _DOOR_OBJECT_CLASSES:
            continue
        name = str(record.property_value("Name", "") or "").strip()
        pair_name = str(record.property_value("DoubleDoorName", "") or "").strip()
        if not name or not pair_name:
            continue
        name_key = name.lower()
        pair_key = pair_name.lower()
        if name_key in by_key and pair_key not in by_key:
            expanded.append(pair_name)
            by_key[pair_key] = pair_name
            added.append(pair_name)
        elif pair_key in by_key and name_key not in by_key:
            expanded.append(name)
            by_key[name_key] = name
            added.append(name)
    if not added:
        return tuple(expanded), ()
    return tuple(expanded), (
        "Door source ED oracle expanded selected model names with paired DoubleDoorName leaf/leaves: "
        + ", ".join(added)
        + ".",
    )


def _source_ed_door_child_brush_indices(source_ed_path: str) -> Dict[str, int]:
    return {
        name: int(node.brush_index)
        for name, node in _source_ed_door_child_brush_nodes(source_ed_path).items()
        if node.brush_index is not None
    }


def _source_ed_door_child_brush_nodes(source_ed_path: str) -> Dict[str, _SourceEdNodeSnippet]:
    with open(source_ed_path, "rb") as f:
        data = f.read()
    layout = legacy_ed.scan_legacy_ed_node_layout(data, source_path=source_ed_path)
    if layout.status != "layout_parsed" or not layout.node_start:
        return {}
    wrapper = legacy_ed._try_decompress_full_level_wrapper(data)
    scan_data = wrapper["decompressed"] if wrapper is not None else data
    root, _end = _read_source_ed_node_snippet(scan_data, int(layout.node_start), include_entry=False)

    result: Dict[str, int] = {}
    for node in _walk_source_ed_node_snippets(root):
        if node.class_name not in _DOOR_OBJECT_CLASSES:
            continue
        name = str(node.properties.get("Name", "") or "")
        if not name:
            continue
        child_node = _first_child_brush_node(node)
        if child_node is None:
            continue
        result[name.lower()] = child_node
    return result


def _first_child_brush_index(node: _SourceEdNodeSnippet) -> Optional[int]:
    child = _first_child_brush_node(node)
    if child is None or child.brush_index is None:
        return None
    return int(child.brush_index)


def _first_child_brush_node(node: _SourceEdNodeSnippet) -> Optional[_SourceEdNodeSnippet]:
    for child in node.children:
        if child.node_type == legacy_ed_writer.NODE_BRUSH and child.brush_index is not None:
            return child
    return None


def _walk_source_ed_node_snippets(node: _SourceEdNodeSnippet) -> Iterable[_SourceEdNodeSnippet]:
    yield node
    for child in node.children:
        yield from _walk_source_ed_node_snippets(child)


def _read_source_ed_node_snippet(
    data: bytes,
    pos: int,
    *,
    include_entry: bool,
) -> Tuple[_SourceEdNodeSnippet, int]:
    node_type: Optional[int] = None
    brush_index: Optional[int] = None
    if include_entry:
        node_type = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if node_type == legacy_ed_writer.NODE_BRUSH:
            brush_index = struct.unpack_from("<I", data, pos)[0]
            pos += 4
    child_count = struct.unpack_from("<H", data, pos)[0]
    pos += 2
    children: List[_SourceEdNodeSnippet] = []
    for _ in range(child_count):
        child, pos = _read_source_ed_node_snippet(data, pos, include_entry=True)
        children.append(child)
    class_name, properties, property_records, pos = _read_source_ed_node_item_snippet(data, pos)
    return _SourceEdNodeSnippet(
        node_type=node_type,
        brush_index=brush_index,
        class_name=class_name,
        properties=properties,
        property_records=property_records,
        children=tuple(children),
    ), pos


def _read_source_ed_node_item_snippet(
    data: bytes,
    pos: int,
) -> Tuple[str, Dict[str, object], Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...], int]:
    payload_len = struct.unpack_from("<H", data, pos)[0]
    payload_start = pos + 2
    payload_end = payload_start + payload_len
    class_name, cursor = _read_source_ed_prefixed_string(data, payload_start)
    property_count = struct.unpack_from("<I", data, cursor)[0]
    cursor += 4
    properties: Dict[str, object] = {}
    property_records: List[legacy_ed_writer.LegacyEdObjectProperty] = []
    for _ in range(property_count):
        prop_name, cursor = _read_source_ed_prefixed_string(data, cursor)
        type_code = data[cursor]
        cursor += 1
        flags = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        value_len = struct.unpack_from("<H", data, cursor)[0]
        cursor += 2
        value_start = cursor
        if type_code in getattr(legacy_ed, "_PROP_TYPE_NAMES", {}):
            value = legacy_ed._decode_object_property_value(data, value_start, value_len, type_code)
        else:
            value = None
        if value is None:
            if type_code == 0:
                try:
                    value, _value_end = _read_source_ed_prefixed_string(data, value_start)
                except (struct.error, UnicodeDecodeError, ValueError):
                    value = data[value_start:value_start + value_len]
            else:
                value = data[value_start:value_start + value_len]
        properties[prop_name] = value
        if type_code in getattr(legacy_ed, "_PROP_TYPE_NAMES", {}) and not isinstance(value, (bytes, bytearray)):
            property_records.append(legacy_ed_writer.LegacyEdObjectProperty(prop_name, type_code, flags, value))
        cursor = value_start + value_len
    return class_name, properties, tuple(property_records), _skip_source_ed_node_item_footer(data, payload_end)


def _skip_source_ed_node_item_footer(data: bytes, payload_end: int) -> int:
    cursor = payload_end + 8
    _display_name, cursor = _read_source_ed_prefixed_string(data, cursor)
    return cursor


def _read_source_ed_prefixed_string(data: bytes, pos: int) -> Tuple[str, int]:
    length = struct.unpack_from("<H", data, pos)[0]
    start = pos + 2
    end = start + length
    if end > len(data):
        raise ValueError("legacy ED string payload is outside the buffer")
    return data[start:end].decode("latin1"), end


def _airail_object_specs_from_dat_bytes(
    data: bytes,
    *,
    source_ed_path: str = "",
) -> Tuple[Tuple[_AirailObjectSpec, ...], Tuple[str, ...]]:
    try:
        from core import bsp

        parsed = bsp.parse(data)
    except Exception as exc:
        return (), (f"AIRail object generation skipped because DAT parse failed: {exc}",)

    source_specs, source_notes = _airail_object_specs_from_source_ed(source_ed_path)
    specs: List[_AirailObjectSpec] = []
    oracle_match_count = 0
    for model in getattr(parsed, "world_models", ()) or ():
        helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
        if int(helper_roles.get("aiRail", 0)) <= 0:
            continue
        if set(helper_roles.keys()) != {"aiRail"}:
            continue
        if not terrain_semantics.model_has_only_helper_textures(model):
            continue
        name = str(getattr(model, "name", "") or "").strip()
        if not name:
            name = f"AIRail{len(specs)}"
        source_spec = source_specs.get(name.lower())
        if source_spec is not None:
            specs.append(replace(source_spec, source_model_name=name, source_kind="source_ed_oracle"))
            oracle_match_count += 1
            continue
        pos = _airail_position_from_helper_model(model)
        specs.append(_AirailObjectSpec(
            name=name,
            pos=pos,
            source_model_name=name,
            source_kind="dat_helper",
        ))

    notes = list(source_notes)
    notes.append(
        f"AIRail object generation found {len(specs)} DAT aiRail helper model(s); "
        f"source ED oracle matches={oracle_match_count}."
    )
    if specs and oracle_match_count < len(specs):
        notes.append(
            "AIRail objects without a source ED oracle match use DAT helper bounds and empty RailLink fields."
        )
    return tuple(specs), tuple(_unique_text(notes))


def _airail_object_specs_from_source_ed(source_ed_path: str) -> Tuple[Dict[str, _AirailObjectSpec], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return {}, ("AIRail source ED oracle was not supplied; generated RailLink fields will use DAT fallback values.",)
    if not os.path.exists(source_ed):
        return {}, (f"AIRail source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return {}, (f"AIRail source ED oracle scan failed: {exc}",)

    specs: Dict[str, _AirailObjectSpec] = {}
    for record in scan.records:
        if str(record.class_name).lower() != "airail":
            continue
        name = str(record.property_value("Name", "") or f"AIRail{len(specs)}")
        links = tuple(
            str(record.property_value(f"RailLink{index}", "") or "")
            for index in range(4)
        )
        specs[name.lower()] = _AirailObjectSpec(
            name=name,
            pos=_finite_vec3(record.property_value("Pos", (0.0, 0.0, 0.0))),
            rail_links=(links[0], links[1], links[2], links[3]),
            source_model_name=name,
            source_kind="source_ed_oracle",
        )
    return specs, (f"AIRail source ED oracle loaded {len(specs)} AIRail object record(s).",)


_SKY_OBJECT_CLASSES = {"TOD_Sky", "SkyPointer", "DemoSkyWorldModel"}


def _sky_object_specs_from_source_ed(
    source_ed_path: str,
) -> Tuple[Tuple[_SkyObjectSpec, ...], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), ("Sky source ED oracle was not supplied; sky object nodes will be skipped.",)
    if not os.path.exists(source_ed):
        return (), (f"Sky source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (f"Sky source ED oracle scan failed: {exc}",)

    specs: List[_SkyObjectSpec] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name not in _SKY_OBJECT_CLASSES:
            continue
        name = str(record.property_value("Name", "") or f"{class_name}{len(specs)}")
        specs.append(_SkyObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_kind="source_ed_oracle",
        ))
    class_counts: Dict[str, int] = {}
    for item in specs:
        class_counts[item.class_name] = class_counts.get(item.class_name, 0) + 1
    detail = ", ".join(f"{name}={class_counts[name]}" for name in sorted(class_counts)) or "none"
    return tuple(specs), (
        f"Sky source ED oracle loaded {len(specs)} sky object record(s): {detail}.",
    )


_SOUND_OBJECT_CLASSES = {"AmbientSound"}


def _sound_object_specs_from_source_ed(
    source_ed_path: str,
) -> Tuple[Tuple[_SoundObjectSpec, ...], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), ("Sound source ED oracle was not supplied; AmbientSound object nodes will be skipped.",)
    if not os.path.exists(source_ed):
        return (), (f"Sound source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (f"Sound source ED oracle scan failed: {exc}",)

    specs: List[_SoundObjectSpec] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name not in _SOUND_OBJECT_CLASSES:
            continue
        name = str(record.property_value("Name", "") or f"{class_name}{len(specs)}")
        specs.append(_SoundObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_kind="source_ed_oracle",
        ))
    return tuple(specs), (
        f"Sound source ED oracle loaded {len(specs)} AmbientSound object record(s).",
    )


_GAMEPLAY_TRIGGER_OBJECT_CLASSES = {"Trigger", "ExitTrigger", "PortalTrigger"}


def _gameplay_trigger_object_specs_from_source_ed(
    source_ed_path: str,
) -> Tuple[Tuple[_GameplayTriggerObjectSpec, ...], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), ("Gameplay trigger source ED oracle was not supplied; Trigger/ExitTrigger/PortalTrigger object nodes will be skipped.",)
    if not os.path.exists(source_ed):
        return (), (f"Gameplay trigger source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (f"Gameplay trigger source ED oracle scan failed: {exc}",)

    specs: List[_GameplayTriggerObjectSpec] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name not in _GAMEPLAY_TRIGGER_OBJECT_CLASSES:
            continue
        name = str(record.property_value("Name", "") or f"{class_name}{len(specs)}")
        specs.append(_GameplayTriggerObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_kind="source_ed_oracle",
        ))
    class_counts: Dict[str, int] = {}
    for item in specs:
        class_counts[item.class_name] = class_counts.get(item.class_name, 0) + 1
    detail = ", ".join(f"{name}={class_counts[name]}" for name in sorted(class_counts)) or "none"
    return tuple(specs), (
        f"Gameplay trigger source ED oracle loaded {len(specs)} Trigger/ExitTrigger/PortalTrigger object record(s): {detail}.",
    )


_STATIC_PROP_OBJECT_CLASSES = {"Prop"}


def _static_prop_object_specs_from_source_ed(
    source_ed_path: str,
) -> Tuple[Tuple[_StaticPropObjectSpec, ...], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), ("Static prop source ED oracle was not supplied; Prop object nodes will be skipped.",)
    if not os.path.exists(source_ed):
        return (), (f"Static prop source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (f"Static prop source ED oracle scan failed: {exc}",)

    specs: List[_StaticPropObjectSpec] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name not in _STATIC_PROP_OBJECT_CLASSES:
            continue
        name = str(record.property_value("Name", "") or f"{class_name}{len(specs)}")
        specs.append(_StaticPropObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_kind="source_ed_oracle",
        ))
    return tuple(specs), (
        f"Static prop source ED oracle loaded {len(specs)} Prop object record(s).",
    )


_LOW_RISK_BEHAVIOR_PROP_OBJECT_CLASSES = {"Barrel", "BonePile", "Cauldron", "Cookpot", "StatStone"}


def _low_risk_behavior_prop_object_specs_from_source_ed(
    source_ed_path: str,
) -> Tuple[Tuple[_LowRiskBehaviorPropObjectSpec, ...], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), (
            "Low-risk behavior prop source ED oracle was not supplied; Barrel/BonePile/Cauldron/Cookpot/StatStone object nodes will be skipped.",
        )
    if not os.path.exists(source_ed):
        return (), (f"Low-risk behavior prop source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (f"Low-risk behavior prop source ED oracle scan failed: {exc}",)

    specs: List[_LowRiskBehaviorPropObjectSpec] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name not in _LOW_RISK_BEHAVIOR_PROP_OBJECT_CLASSES:
            continue
        name = str(record.property_value("Name", "") or f"{class_name}{len(specs)}")
        specs.append(_LowRiskBehaviorPropObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_kind="source_ed_oracle",
        ))
    class_counts: Dict[str, int] = {}
    for item in specs:
        class_counts[item.class_name] = class_counts.get(item.class_name, 0) + 1
    detail = ", ".join(f"{name}={class_counts[name]}" for name in sorted(class_counts)) or "none"
    return tuple(specs), (
        f"Low-risk behavior prop source ED oracle loaded {len(specs)} physical-decor object record(s): {detail}.",
    )


def _wall_torch_object_specs_from_source_ed(
    source_ed_path: str,
) -> Tuple[Tuple[_WallTorchObjectSpec, ...], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), ("WallTorch source ED oracle was not supplied; WallTorch object nodes will be skipped.",)
    if not os.path.exists(source_ed):
        return (), (f"WallTorch source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (f"WallTorch source ED oracle scan failed: {exc}",)

    specs: List[_WallTorchObjectSpec] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name != "WallTorch":
            continue
        name = str(record.property_value("Name", "") or f"{class_name}{len(specs)}")
        specs.append(_WallTorchObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_kind="source_ed_oracle",
        ))
    return tuple(specs), (
        f"WallTorch source ED oracle loaded {len(specs)} WallTorch object record(s).",
    )


def _fire_object_specs_from_source_ed(
    source_ed_path: str,
) -> Tuple[Tuple[_FireObjectSpec, ...], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), ("Fire source ED oracle was not supplied; Fire object nodes will be skipped.",)
    if not os.path.exists(source_ed):
        return (), (f"Fire source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (f"Fire source ED oracle scan failed: {exc}",)

    specs: List[_FireObjectSpec] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name != "Fire":
            continue
        name = str(record.property_value("Name", "") or f"{class_name}{len(specs)}")
        specs.append(_FireObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_kind="source_ed_oracle",
        ))
    return tuple(specs), (
        f"Fire source ED oracle loaded {len(specs)} Fire object record(s).",
    )


def _candle_prop_object_specs_from_source_ed(
    source_ed_path: str,
) -> Tuple[Tuple[_CandlePropObjectSpec, ...], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), ("CandleProp source ED oracle was not supplied; CandleProp object nodes will be skipped.",)
    if not os.path.exists(source_ed):
        return (), (f"CandleProp source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (f"CandleProp source ED oracle scan failed: {exc}",)

    specs: List[_CandlePropObjectSpec] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name != "CandleProp":
            continue
        name = str(record.property_value("Name", "") or f"{class_name}{len(specs)}")
        specs.append(_CandlePropObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_kind="source_ed_oracle",
        ))
    return tuple(specs), (
        f"CandleProp source ED oracle loaded {len(specs)} CandleProp object record(s).",
    )


def _brazier_object_specs_from_source_ed(
    source_ed_path: str,
) -> Tuple[Tuple[_BrazierObjectSpec, ...], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), ("Brazier source ED oracle was not supplied; Brazier object nodes will be skipped.",)
    if not os.path.exists(source_ed):
        return (), (f"Brazier source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (f"Brazier source ED oracle scan failed: {exc}",)

    specs: List[_BrazierObjectSpec] = []
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name != "Brazier":
            continue
        name = str(record.property_value("Name", "") or f"{class_name}{len(specs)}")
        specs.append(_BrazierObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_kind="source_ed_oracle",
        ))
    return tuple(specs), (
        f"Brazier source ED oracle loaded {len(specs)} Brazier object record(s).",
    )


def _treasure_chest_object_specs_from_source_ed(
    source_ed_path: str,
) -> Tuple[Tuple[_TreasureChestObjectSpec, ...], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), ("TreasureChest source ED oracle was not supplied; TreasureChest object nodes will be skipped.",)
    if not os.path.exists(source_ed):
        return (), (f"TreasureChest source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (f"TreasureChest source ED oracle scan failed: {exc}",)

    specs: List[_TreasureChestObjectSpec] = []
    trigger_target_count = 0
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name != "TreasureChest":
            continue
        if str(record.property_value("TriggerTarget", "") or "").strip():
            trigger_target_count += 1
        name = str(record.property_value("Name", "") or f"{class_name}{len(specs)}")
        specs.append(_TreasureChestObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_kind="source_ed_oracle",
        ))
    return tuple(specs), (
        f"TreasureChest source ED oracle loaded {len(specs)} TreasureChest object record(s); {trigger_target_count} trigger target reference(s).",
    )


def _prop_damager_object_specs_from_source_ed(
    source_ed_path: str,
) -> Tuple[Tuple[_PropDamagerObjectSpec, ...], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), ("PropDamager source ED oracle was not supplied; PropDamager object nodes will be skipped.",)
    if not os.path.exists(source_ed):
        return (), (f"PropDamager source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (f"PropDamager source ED oracle scan failed: {exc}",)

    specs: List[_PropDamagerObjectSpec] = []
    damage_trigger_count = 0
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name != "PropDamager":
            continue
        if str(record.property_value("DamageTriggerTarget", "") or "").strip():
            damage_trigger_count += 1
        name = str(record.property_value("Name", "") or f"{class_name}{len(specs)}")
        specs.append(_PropDamagerObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_kind="source_ed_oracle",
        ))
    return tuple(specs), (
        f"PropDamager source ED oracle loaded {len(specs)} PropDamager object record(s); {damage_trigger_count} damage trigger target reference(s).",
    )


def _destructable_prop_object_specs_from_source_ed(
    source_ed_path: str,
) -> Tuple[Tuple[_DestructablePropObjectSpec, ...], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), ("DestructableProp source ED oracle was not supplied; DestructableProp object nodes will be skipped.",)
    if not os.path.exists(source_ed):
        return (), (f"DestructableProp source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (f"DestructableProp source ED oracle scan failed: {exc}",)

    specs: List[_DestructablePropObjectSpec] = []
    damage_trigger_count = 0
    for record in scan.records:
        class_name = str(record.class_name)
        if class_name != "DestructableProp":
            continue
        if str(record.property_value("DamageTriggerTarget", "") or "").strip():
            damage_trigger_count += 1
        name = str(record.property_value("Name", "") or f"{class_name}{len(specs)}")
        specs.append(_DestructablePropObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_kind="source_ed_oracle",
        ))
    return tuple(specs), (
        f"DestructableProp source ED oracle loaded {len(specs)} DestructableProp object record(s); {damage_trigger_count} damage trigger target reference(s).",
    )


def _destructable_brush_object_specs_from_dat_bytes(
    data: bytes,
    *,
    selected_model_names: Sequence[str] = (),
) -> Tuple[Tuple[_DestructableBrushObjectSpec, ...], Tuple[str, ...]]:
    try:
        from mm9_patcher import mm9_patch as patcher

        header = patcher.Header.parse(data)
        objects, _object_end = patcher.parse_objects(data, header.obj_pos)
    except Exception as exc:
        return (), (f"DestructableBrush DAT object scan failed: {exc}",)

    wanted = {str(name or "").lower() for name in selected_model_names if str(name or "")}
    specs: List[_DestructableBrushObjectSpec] = []
    death_trigger_count = 0
    skipped_unselected = 0
    for obj in objects:
        if str(getattr(obj, "type_str", "") or "") != "DestructableBrush":
            continue
        name = str(obj.get("Name", "") or f"DestructableBrush{len(specs)}")
        if wanted and name.lower() not in wanted:
            skipped_unselected += 1
            continue
        if str(obj.get("DeathTriggerTarget", "") or "").strip():
            death_trigger_count += 1
        specs.append(_DestructableBrushObjectSpec(
            name=name,
            class_name="DestructableBrush",
            properties=_writer_object_properties_from_dat_object(obj.props),
            source_model_name=name,
            source_kind="dat_object",
        ))

    matched = {item.source_model_name.lower() for item in specs if item.source_model_name}
    missing = sorted(name for name in wanted if name not in matched)
    notes = [
        f"DestructableBrush DAT object records loaded {len(specs)} selected DestructableBrush object record(s); {death_trigger_count} death trigger target reference(s).",
    ]
    if skipped_unselected:
        notes.append(
            f"DestructableBrush DAT object scan skipped {skipped_unselected} unselected record(s)."
        )
    if missing:
        preview = ", ".join(missing[:8])
        suffix = "" if len(missing) <= 8 else f", +{len(missing) - 8} more"
        notes.append(
            "Selected DAT model names without matching DestructableBrush object records: "
            f"{preview}{suffix}."
        )
    return tuple(specs), tuple(_unique_text(notes))


def _sky_marker_brushes_from_source_ed(
    source_ed_path: str,
) -> Tuple[
    Tuple[legacy_ed_writer.LegacyEdBrush, ...],
    Tuple[SurrogateEdModelSummary, ...],
    Tuple[Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...], ...],
    Tuple[str, ...],
]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return (), (), (), ("SkyMarker source ED oracle was not supplied; sky marker Brush records will be skipped.",)
    if not os.path.exists(source_ed):
        return (), (), (), (f"SkyMarker source ED oracle was not found: {source_ed}",)
    try:
        scene = legacy_ed.load_legacy_ed_geometry_scene(source_ed)
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (), (), (f"SkyMarker source ED oracle scan failed: {exc}",)

    brush_records = [record for record in scan.records if record.class_name == "Brush"]
    brushes: List[legacy_ed_writer.LegacyEdBrush] = []
    summaries: List[SurrogateEdModelSummary] = []
    node_properties: List[Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]] = []
    sky_face_count = 0
    mixed_brush_count = 0
    for model_index, model in enumerate(scene.models):
        role_counts: Dict[str, int] = {}
        for face in getattr(model, "faces", ()) or ():
            role = terrain_semantics.helper_texture_role(getattr(face, "material_name", ""))
            if role:
                role_counts[role] = role_counts.get(role, 0) + 1
        if int(role_counts.get("skyVisibility", 0)) <= 0:
            continue
        if set(role_counts.keys()) != {"skyVisibility"}:
            mixed_brush_count += 1
            continue
        source_name = str(getattr(model, "name", "") or f"SkyMarkerBrush{len(brushes)}")
        source_properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...] = ()
        if model_index < len(brush_records):
            source_record = brush_records[model_index]
            source_name = str(source_record.property_value("Name", "") or source_name)
        brush = _legacy_brush_from_source_geometry_model(
            model,
            name=f"SkyMarker_{_safe_legacy_name_component(source_name) or len(brushes)}",
        )
        if model_index < len(brush_records):
            source_properties = _writer_object_properties_from_scan(
                brush_records[model_index].properties,
                overrides={"Name": brush.name},
            )
        if not source_properties:
            source_properties = legacy_ed_writer.full_world_brush_node_properties(brush.name)
        brushes.append(brush)
        node_properties.append(source_properties)
        sky_face_count += int(role_counts.get("skyVisibility", 0))
        summaries.append(SurrogateEdModelSummary(
            name=brush.name or f"SkyMarkerBrush{len(summaries)}",
            status="written",
            point_count=len(brush.points),
            polygon_count=len(brush.surfaces),
            texture_count=len({surface.texture_name for surface in brush.surfaces}),
            byte_count=len(legacy_ed_writer.write_brush_record(brush)),
            notes=(f"source ED SkyMarker Brush copied from {source_name}",),
        ))

    notes = [
        f"SkyMarker source ED oracle copied {len(brushes)} Brush record(s) with {sky_face_count} SkyMarker face(s)."
    ]
    if mixed_brush_count:
        notes.append(
            f"Skipped {mixed_brush_count} mixed source Brush record(s) that combine SkyMarker faces with other helper roles."
        )
    if not brushes:
        notes.append("No pure source ED SkyMarker Brush records were found.")
    return tuple(brushes), tuple(summaries), tuple(node_properties), tuple(_unique_text(notes))


def _sky_marker_residue_brushes_from_source_ed(
    source_ed_path: str,
    *,
    reference_dat_path: str,
) -> Tuple[
    Tuple[legacy_ed_writer.LegacyEdBrush, ...],
    Tuple[SurrogateEdModelSummary, ...],
    Tuple[Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...], ...],
    Tuple[str, ...],
]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    reference_dat = os.path.abspath(reference_dat_path) if reference_dat_path else ""
    if not source_ed:
        return (), (), (), ("SkyMarker residue source ED oracle was not supplied; residue Brush records will be skipped.",)
    if not os.path.exists(source_ed):
        return (), (), (), (f"SkyMarker residue source ED oracle was not found: {source_ed}",)
    if not reference_dat:
        return (), (), (), ("SkyMarker residue compiled DAT reference was not supplied; residue Brush records will be skipped.",)
    if not os.path.exists(reference_dat):
        return (), (), (), (f"SkyMarker residue compiled DAT reference was not found: {reference_dat}",)
    try:
        from features.dat_editing import compiler_strategy

        residue_report = compiler_strategy.build_sky_marker_compiled_residue_report(
            source_ed_path=source_ed,
            compiled_dat_path=reference_dat,
        )
    except Exception as exc:
        return (), (), (), (f"SkyMarker residue correlation failed: {exc}",)
    if residue_report.blockers:
        return (), (), (), tuple(
            _unique_text(
                ["SkyMarker residue Brush generation skipped because the residue report has blockers."]
                + list(residue_report.blockers)
            )
        )

    matched_by_model: Dict[int, set[int]] = defaultdict(set)
    for item in residue_report.compiled_residue_matches:
        if item.source_model_index < 0 or item.source_face_index < 0:
            continue
        if item.status.startswith("unmatched"):
            continue
        matched_by_model[int(item.source_model_index)].add(int(item.source_face_index))
    if not matched_by_model:
        return (), (), (), ("SkyMarker residue correlation did not produce matched source faces; residue Brush records will be skipped.",)

    try:
        scene = legacy_ed.load_legacy_ed_geometry_scene(source_ed)
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return (), (), (), (f"SkyMarker residue source ED oracle scan failed: {exc}",)

    brush_records = [record for record in scan.records if record.class_name == "Brush"]
    brushes: List[legacy_ed_writer.LegacyEdBrush] = []
    summaries: List[SurrogateEdModelSummary] = []
    node_properties: List[Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]] = []
    matched_face_count = 0
    for model_index, face_indices in sorted(matched_by_model.items()):
        if model_index < 0 or model_index >= len(scene.models):
            continue
        model = scene.models[model_index]
        sky_face_indices = set()
        for face_index, face in enumerate(getattr(model, "faces", ()) or ()):
            if face_index not in face_indices:
                continue
            role = terrain_semantics.helper_texture_role(getattr(face, "material_name", ""))
            if role != "skyVisibility":
                continue
            sky_face_indices.add(int(face_index))
        if not sky_face_indices:
            continue
        source_name = str(getattr(model, "name", "") or f"SkyMarkerBrush{len(brushes)}")
        source_properties: Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...] = ()
        if model_index < len(brush_records):
            source_record = brush_records[model_index]
            source_name = str(source_record.property_value("Name", "") or source_name)
        brush = _legacy_brush_from_source_geometry_model(
            model,
            name=f"SkyMarkerResidue_{_safe_legacy_name_component(source_name) or len(brushes)}",
            face_indices=sky_face_indices,
        )
        if not brush.surfaces:
            continue
        if model_index < len(brush_records):
            source_properties = _writer_object_properties_from_scan(
                brush_records[model_index].properties,
                overrides={"Name": brush.name},
            )
        if not source_properties:
            source_properties = legacy_ed_writer.full_world_brush_node_properties(brush.name)
        brushes.append(brush)
        node_properties.append(source_properties)
        matched_face_count += len(brush.surfaces)
        summaries.append(SurrogateEdModelSummary(
            name=brush.name or f"SkyMarkerResidueBrush{len(summaries)}",
            status="written",
            point_count=len(brush.points),
            polygon_count=len(brush.surfaces),
            texture_count=len({surface.texture_name for surface in brush.surfaces}),
            byte_count=len(legacy_ed_writer.write_brush_record(brush)),
            notes=(f"diagnostic source ED SkyMarker residue faces copied from {source_name}",),
        ))

    notes = [
        f"SkyMarker residue source ED oracle copied {len(brushes)} diagnostic Brush record(s) with {matched_face_count} matched SkyMarker face(s).",
        f"Residue face set came from compiled DAT reference: {reference_dat}.",
        "SkyMarker residue Brush output is diagnostic-only; compile it and check helper leakage before game testing.",
    ]
    if matched_face_count != int(residue_report.matched_source_sky_marker_face_count):
        notes.append(
            f"Residue report matched {residue_report.matched_source_sky_marker_face_count} source face(s), but only {matched_face_count} could be copied."
        )
    for rule in residue_report.residue_rule_candidates:
        if rule.status == "oracle_target":
            continue
        if rule.unmatched_source_face_count == 0 and rule.missed_matched_source_face_count == 0:
            notes.append(f"Non-oracle residue rule candidate is exact: {rule.rule_name}.")
            break
    else:
        notes.append("No non-oracle SkyMarker residue rule is exact yet; this output uses the compiled-reference oracle face set.")
    return tuple(brushes), tuple(summaries), tuple(node_properties), tuple(_unique_text(notes))


def _legacy_brush_from_source_geometry_model(
    model: object,
    *,
    name: str,
    face_indices: Optional[set[int]] = None,
) -> legacy_ed_writer.LegacyEdBrush:
    points = tuple(_finite_vec3(point) for point in getattr(model, "points", ()) or ())
    surfaces: List[legacy_ed_writer.LegacyEdSurface] = []
    face_index_filter = set(face_indices) if face_indices is not None else None
    for face_index, face in enumerate(getattr(model, "faces", ()) or ()):
        if face_index_filter is not None and int(face_index) not in face_index_filter:
            continue
        extras = getattr(face, "extras", {}) or {}
        shade = extras.get("shade_rgb", (0, 0, 0))
        if not isinstance(shade, (tuple, list)) or len(shade) != 3:
            shade_rgb = (0, 0, 0)
        else:
            shade_rgb = tuple(max(0, min(255, int(item))) for item in shade)  # type: ignore[assignment]
        surfaces.append(legacy_ed_writer.LegacyEdSurface(
            vertex_indices=tuple(int(index) for index in getattr(face, "vertex_indices", ()) or ()),
            plane_normal=_finite_vec3(extras.get("normal", (0.0, 1.0, 0.0))),
            plane_dist=float(extras.get("dist", 0.0) or 0.0),
            texture_name=str(getattr(face, "material_name", "") or "Default"),
            uv_o=_finite_vec3(extras.get("uv_o", (0.0, 0.0, 0.0))),
            uv_p=_finite_vec3(extras.get("uv_p", (1.0, 0.0, 0.0))),
            uv_q=_finite_vec3(extras.get("uv_q", (0.0, 0.0, 1.0))),
            texture_flags=int(extras.get("texture_flags", 0) or 0),
            surface_flags=int(extras.get("surface_flags", 0) or 0),
            shade_rgb=shade_rgb,  # type: ignore[arg-type]
        ))
    color = getattr(model, "extras", {}).get("color", (128, 128, 128))
    if not isinstance(color, (tuple, list)) or len(color) != 3:
        color_rgb = (128, 128, 128)
    else:
        color_rgb = tuple(max(0, min(255, int(item))) for item in color)  # type: ignore[assignment]
    return legacy_ed_writer.LegacyEdBrush(
        points=points,
        surfaces=tuple(surfaces),
        color_rgb=color_rgb,  # type: ignore[arg-type]
        name=str(name),
    )


def _collision_helper_assets_from_dat_bytes(
    data: bytes,
    *,
    source_ed_path: str = "",
    selected_model_names: Sequence[str] = (),
    include_brushes: bool = True,
) -> Tuple[
    Tuple[legacy_ed_writer.LegacyEdBrush, ...],
    Tuple[SurrogateEdModelSummary, ...],
    Tuple[_CollisionHelperObjectSpec, ...],
    Tuple[str, ...],
]:
    try:
        from core import bsp

        parsed = bsp.parse(data)
    except Exception as exc:
        return (), (), (), (f"Collision helper generation skipped because DAT parse failed: {exc}",)

    selected_lookup = {str(name or "").lower() for name in selected_model_names if str(name or "")}
    helper_models: List[object] = []
    for model in getattr(parsed, "world_models", ()) or ():
        name = str(getattr(model, "name", "") or "").strip()
        if name.lower() in selected_lookup:
            continue
        if not _is_pure_collision_helper_model(model):
            continue
        helper_models.append(model)

    candidate_names = tuple(str(getattr(model, "name", "") or "") for model in helper_models)
    source_specs, source_notes = _collision_helper_object_specs_from_source_ed(
        source_ed_path,
        candidate_names=candidate_names,
    )
    brushes: List[legacy_ed_writer.LegacyEdBrush] = []
    summaries: List[SurrogateEdModelSummary] = []
    object_specs: List[_CollisionHelperObjectSpec] = []
    oracle_match_count = 0
    for model in helper_models:
        name = str(getattr(model, "name", "") or f"CollisionHelper{len(summaries)}")
        if include_brushes:
            brush, summary = _model_to_legacy_brush(model, len(summaries))
            if summary.status == "written" and brush is not None:
                brushes.append(brush)
                summaries.append(replace(
                    summary,
                    notes=tuple(_unique_text(tuple(summary.notes) + (
                        "collision helper Brush generated from DAT helper geometry",
                    ))),
                ))
            else:
                summaries.append(summary)
        source_spec = source_specs.get(name.lower())
        if source_spec is not None:
            object_specs.append(replace(source_spec, source_model_name=name))
            oracle_match_count += 1

    notes = list(source_notes)
    notes.append(
        f"Collision helper generation found {len(helper_models)} DAT collision helper model(s); "
        f"emitted Brush records={len(brushes)}; source ED object matches={oracle_match_count}."
    )
    if helper_models and not include_brushes:
        notes.append(
            "Collision helper Brush records skipped by request; helper output is limited to source ED object nodes."
        )
    if helper_models and oracle_match_count < len(helper_models) and include_brushes:
        notes.append(
            "Collision helper models without a source ED object match emit Brush geometry only; helper object nodes are skipped."
        )
    elif helper_models and oracle_match_count < len(helper_models):
        notes.append(
            "Collision helper models without a source ED object match are skipped while helper Brush output is disabled."
        )
    return tuple(brushes), tuple(summaries), tuple(object_specs), tuple(_unique_text(notes))


def _collision_helper_object_specs_from_source_ed(
    source_ed_path: str,
    *,
    candidate_names: Sequence[str],
) -> Tuple[Dict[str, _CollisionHelperObjectSpec], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return {}, ("Collision helper source ED oracle was not supplied; helper object nodes will be skipped.",)
    if not os.path.exists(source_ed):
        return {}, (f"Collision helper source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return {}, (f"Collision helper source ED oracle scan failed: {exc}",)

    wanted = {str(name or "").lower() for name in candidate_names if str(name or "")}
    specs: Dict[str, _CollisionHelperObjectSpec] = {}
    for record in scan.records:
        name = str(record.property_value("Name", "") or "")
        if not name or name.lower() not in wanted:
            continue
        expected_class = _collision_helper_target_class_name(name)
        class_name = str(record.class_name)
        if expected_class and class_name != expected_class:
            continue
        if class_name not in {"InvisibleBrush", "PerceptionBrush", "Ladder", "WorldObject"}:
            continue
        specs[name.lower()] = _CollisionHelperObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_model_name=name,
            source_kind="source_ed_oracle",
        )
    return specs, (
        f"Collision helper source ED oracle loaded {len(specs)} helper object record(s).",
    )


def _trigger_helper_assets_from_dat_bytes(
    data: bytes,
    *,
    source_ed_path: str = "",
    selected_model_names: Sequence[str] = (),
    include_brushes: bool = True,
) -> Tuple[
    Tuple[legacy_ed_writer.LegacyEdBrush, ...],
    Tuple[SurrogateEdModelSummary, ...],
    Tuple[_TriggerHelperObjectSpec, ...],
    Tuple[str, ...],
]:
    try:
        from core import bsp

        parsed = bsp.parse(data)
    except Exception as exc:
        return (), (), (), (f"Trigger helper generation skipped because DAT parse failed: {exc}",)

    selected_lookup = {str(name or "").lower() for name in selected_model_names if str(name or "")}
    helper_models: List[object] = []
    for model in getattr(parsed, "world_models", ()) or ():
        name = str(getattr(model, "name", "") or "").strip()
        if name.lower() in selected_lookup:
            continue
        if not _is_pure_trigger_helper_model(model):
            continue
        helper_models.append(model)

    candidate_names = tuple(str(getattr(model, "name", "") or "") for model in helper_models)
    source_specs, source_notes = _trigger_helper_object_specs_from_source_ed(
        source_ed_path,
        candidate_names=candidate_names,
    )
    brushes: List[legacy_ed_writer.LegacyEdBrush] = []
    summaries: List[SurrogateEdModelSummary] = []
    object_specs: List[_TriggerHelperObjectSpec] = []
    oracle_match_count = 0
    for model in helper_models:
        name = str(getattr(model, "name", "") or f"TriggerHelper{len(summaries)}")
        if include_brushes:
            brush, summary = _model_to_legacy_brush(model, len(summaries))
            if summary.status == "written" and brush is not None:
                brushes.append(brush)
                summaries.append(replace(
                    summary,
                    notes=tuple(_unique_text(tuple(summary.notes) + (
                        "trigger helper Brush generated from DAT GreenScreen helper geometry",
                    ))),
                ))
            else:
                summaries.append(summary)
        source_spec = source_specs.get(name.lower())
        if source_spec is not None:
            object_specs.append(replace(source_spec, source_model_name=name))
            oracle_match_count += 1

    notes = list(source_notes)
    notes.append(
        f"Trigger helper generation found {len(helper_models)} DAT GreenScreen helper model(s); "
        f"emitted Brush records={len(brushes)}; source ED object matches={oracle_match_count}."
    )
    if helper_models and not include_brushes:
        notes.append(
            "Trigger helper Brush records skipped by request; helper output is limited to source ED PortalZone object nodes."
        )
    if helper_models and oracle_match_count < len(helper_models) and include_brushes:
        notes.append(
            "Trigger helper models without a source ED object match emit Brush geometry only; helper object nodes are skipped."
        )
    elif helper_models and oracle_match_count < len(helper_models):
        notes.append(
            "Trigger helper models without a source ED object match are skipped while helper Brush output is disabled."
        )
    return tuple(brushes), tuple(summaries), tuple(object_specs), tuple(_unique_text(notes))


def _trigger_helper_object_specs_from_source_ed(
    source_ed_path: str,
    *,
    candidate_names: Sequence[str],
) -> Tuple[Dict[str, _TriggerHelperObjectSpec], Tuple[str, ...]]:
    source_ed = os.path.abspath(source_ed_path) if source_ed_path else ""
    if not source_ed:
        return {}, ("Trigger helper source ED oracle was not supplied; helper object nodes will be skipped.",)
    if not os.path.exists(source_ed):
        return {}, (f"Trigger helper source ED oracle was not found: {source_ed}",)
    try:
        scan = legacy_ed.load_legacy_ed_object_scan_report(source_ed)
    except Exception as exc:
        return {}, (f"Trigger helper source ED oracle scan failed: {exc}",)

    wanted = {str(name or "").lower() for name in candidate_names if str(name or "")}
    specs: Dict[str, _TriggerHelperObjectSpec] = {}
    for record in scan.records:
        name = str(record.property_value("Name", "") or "")
        if not name or name.lower() not in wanted:
            continue
        class_name = str(record.class_name)
        if class_name != "PortalZone":
            continue
        specs[name.lower()] = _TriggerHelperObjectSpec(
            name=name,
            class_name=class_name,
            properties=_writer_object_properties_from_scan(record.properties),
            source_model_name=name,
            source_kind="source_ed_oracle",
        )
    return specs, (
        f"Trigger helper source ED oracle loaded {len(specs)} PortalZone object record(s).",
    )


def _writer_object_properties_from_scan(
    properties: Sequence[legacy_ed.LegacyEdObjectProperty],
    *,
    overrides: Optional[Dict[str, object]] = None,
) -> Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]:
    override_values = dict(overrides or {})
    return tuple(
        legacy_ed_writer.LegacyEdObjectProperty(
            name=str(prop.name),
            type_code=int(prop.type_code),
            flags=int(prop.flags),
            value=override_values.get(str(prop.name), prop.value),
        )
        for prop in properties
    )


def _writer_object_properties_from_dat_object(
    properties: Sequence[object],
) -> Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]:
    return tuple(
        legacy_ed_writer.LegacyEdObjectProperty(
            name=str(getattr(prop, "name", "")),
            type_code=int(getattr(prop, "code", 0)),
            flags=int(getattr(prop, "flags", 0)),
            value=_legacy_ed_value_from_dat_property(prop),
        )
        for prop in properties
    )


def _legacy_ed_value_from_dat_property(prop: object) -> object:
    code = int(getattr(prop, "code", 0))
    value = getattr(prop, "value", None)
    if code in (4, 6) and isinstance(value, int):
        try:
            return struct.unpack("<f", struct.pack("<I", int(value) & 0xFFFFFFFF))[0]
        except (OverflowError, struct.error, ValueError):
            return float(value)
    if code == 5:
        return bool(value)
    return value


def _writer_object_properties_with_name(
    properties: Sequence[legacy_ed_writer.LegacyEdObjectProperty],
    name: str,
) -> Tuple[legacy_ed_writer.LegacyEdObjectProperty, ...]:
    result: List[legacy_ed_writer.LegacyEdObjectProperty] = []
    found_name = False
    for prop in properties:
        if prop.name == "Name":
            result.append(replace(prop, value=str(name)))
            found_name = True
        else:
            result.append(prop)
    if not found_name:
        result.insert(0, legacy_ed_writer.LegacyEdObjectProperty("Name", 0, 0, str(name)))
    return tuple(result)


def _is_pure_collision_helper_model(model: object) -> bool:
    helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
    return (
        int(helper_roles.get("collision", 0)) > 0
        and set(helper_roles.keys()).issubset({"collision", "sprite"})
        and terrain_semantics.model_has_only_helper_textures(model)
    )


def _is_pure_trigger_helper_model(model: object) -> bool:
    helper_roles = terrain_semantics.helper_texture_roles_for_model(model)
    return (
        int(helper_roles.get("trigger", 0)) > 0
        and set(helper_roles.keys()) == {"trigger"}
        and terrain_semantics.model_has_only_helper_textures(model)
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


def _airail_position_from_helper_model(model: object) -> Vec3:
    points = tuple(_finite_vec3(point) for point in getattr(model, "points", ()) or ())
    if not points:
        return (0.0, 0.0, 0.0)
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    max_y = max(point[1] for point in points)
    min_z = min(point[2] for point in points)
    max_z = max(point[2] for point in points)
    return ((min_x + max_x) * 0.5, max_y, (min_z + max_z) * 0.5)


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

    polygons = tuple(getattr(model, "polygons", ()) or ())
    candidates = terrain_reconstruction.physics_shell_candidates(model)
    invalid_polygon_count = max(0, len(polygons) - len(candidates))

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
    generated_role_counts: Dict[str, int] = defaultdict(int)

    primary_candidates = terrain_reconstruction.balanced_physics_shell_candidates(candidates, limit)
    attempted_indices = set()

    def add_candidate_slabs(ordered_candidates: Sequence[terrain_reconstruction.PhysicsShellCandidate]) -> None:
        nonlocal skipped_slab_count
        for candidate in ordered_candidates:
            if len(brushes) >= limit:
                break
            attempted_indices.add(candidate.polygon_index)
            brush_summary = _physics_shell_polygon_slab_brush(
                model,
                candidate.polygon,
                candidate.points,
                polygon_index=candidate.polygon_index,
                name=f"{prefix}_{candidate.polygon_index:04d}",
                thickness=safe_thickness,
                side_texture=side_texture_name,
            )
            if brush_summary is None:
                skipped_slab_count += 1
                continue
            brush, summary = brush_summary
            brushes.append(brush)
            summaries.append(summary)
            generated_role_counts[str(candidate.role)] += 1
            if brush.surfaces:
                normal = brush.surfaces[0].plane_normal
                if normal[1] > 0.45:
                    center = (
                        sum(point[0] for point in candidate.points) / len(candidate.points),
                        sum(point[1] for point in candidate.points) / len(candidate.points),
                        sum(point[2] for point in candidate.points) / len(candidate.points),
                    )
                    floor_candidates.append((candidate.area, center, center[1]))

    add_candidate_slabs(primary_candidates)
    if len(brushes) < limit:
        fallback_candidates = terrain_reconstruction.balanced_physics_shell_candidates(
            tuple(candidate for candidate in candidates if candidate.polygon_index not in attempted_indices),
            len(candidates),
        )
        add_candidate_slabs(fallback_candidates)

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
        "PhysicsBSP shell connected balanced selector generated roles: "
        + ", ".join(
            f"{role}={generated_role_counts.get(role, 0)}"
            for role in ("side_wall", "floor", "ceiling", "helper/special")
        )
        + ".",
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
        notes.append("Default StartPoint support candidate is the broadest upward-facing generated PhysicsBSP shell face.")

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
    safe_thickness = max(4.0, float(thickness))
    if not terrain_reconstruction.physics_shell_slab_quality_ok(front, thickness=safe_thickness):
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
        texture_name = _texture_name_for_polygon(model, polygon)
        uv_o = _finite_vec3(getattr(surface, "uv_o", (0.0, 0.0, 0.0)) if surface else (0.0, 0.0, 0.0))
        uv_p = _finite_vec3(getattr(surface, "uv_p", (1.0, 0.0, 0.0)) if surface else (1.0, 0.0, 0.0))
        uv_q = _finite_vec3(getattr(surface, "uv_q", (0.0, 0.0, 1.0)) if surface else (0.0, 0.0, 1.0))
        texture_flags = int(getattr(surface, "texture_flags", 0) or 0) if surface else 0
        texture_flags = max(texture_flags, _helper_texture_flags(texture_name))
        surfaces.append(legacy_ed_writer.LegacyEdSurface(
            vertex_indices=indices,
            plane_normal=normal,
            plane_dist=dist,
            texture_name=texture_name,
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
