"""
prefab_import.py
================

Planning helpers for previewing DEdit source geometry and importing validated
compiled prefab DAT BSP records.

Static import deliberately imports geometry rather than behavior: controller
WorldObjects, scripts, and special door/elevator logic remain outside this
tool.  The writer path reuses the same raw-record transform machinery as
physical door cloning. The minimal additive compiler is retained strictly for
editor preview; Project save planning never accepts its records as game BSP.
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from core import bsp
from features.dat_editing import (
    bsp_compile,
    bsp_record_inspector,
    geometry_mesh,
    geometry_scene,
    legacy_ed,
)
from features.doors import clone as door_clone
from . import inspector as prefab_inspector


Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class PrefabBspImportPlan:
    source_path: str
    new_name: str
    target_pos: Vec3
    target_yaw: float
    placement_anchor: str
    source_pivot: Vec3
    submodels: List[object]
    visible_model_names: List[str]
    collision_model_names: List[str]
    source_model_names: List[str]
    source_model_roles: List[str]
    info_flags_overrides: List[Optional[int]]
    import_mode: str = "static"


@dataclass(frozen=True)
class PrefabBrushImportGroup:
    target_name: str
    source_indices: Tuple[int, ...]
    role: str = "geometry"


def suggest_import_name(
    existing_bsp: bsp.BspWorld,
    prefab_path: str,
    object_names: Optional[Sequence[str]] = None,
) -> str:
    """Return a collision-free BSP model name prefix for *prefab_path*."""
    stem = os.path.splitext(os.path.basename(prefab_path))[0] or "Prefab"
    stem = _sanitize_model_name(stem)
    existing = {
        str(getattr(model, "name", "") or "").lower()
        for model in getattr(existing_bsp, "world_models", []) or []
    }
    existing.update(str(name or "").lower() for name in object_names or ())
    if stem.lower() not in existing:
        return stem
    index = 1
    while f"{stem}{index}".lower() in existing:
        index += 1
    return f"{stem}{index}"


def build_static_import_plan(
    target_bsp: bsp.BspWorld,
    prefab_path: str,
    new_name: Optional[str] = None,
    target_pos: Sequence[float] = (0.0, 0.0, 0.0),
    target_yaw: float = 0.0,
    include_roles: Optional[Sequence[str]] = None,
    collision_mode: str = "none",
    collision_thickness: float = 8.0,
    collision_segment_length: float = 512.0,
    target_dat_bytes: Optional[bytes] = None,
    placement_anchor: str = "bottom_center",
    allow_unsafe_visibility: bool = False,
    target_object_names: Optional[Sequence[str]] = None,
    allow_generated_bsp: bool = True,
    validate_runtime_bsp: bool = False,
) -> PrefabBspImportPlan:
    """
    Build a static BSP import plan from a converted DAT or DEdit ED prefab.

    For compiled DATs, normal/controller geometry is preferred for visuals and
    PhysicsBSP is the system-only fallback. For legacy ED files, visible source
    brushes may be combined into a preview-only model when
    ``allow_generated_bsp`` is true. VisBSP is never selected by default and
    requires a diagnostic opt-in.
    """
    info = prefab_inspector.inspect_prefab(prefab_path)
    if validate_runtime_bsp and abs(float(target_yaw)) > 1.0e-7:
        raise ValueError(
            "Compiled prefab BSP yaw is not runtime-safe yet because its physics "
            "block grid would need to be rebuilt. Place it without rotation."
        )
    if info.source_format == "legacy_ed":
        if not allow_generated_bsp:
            raise ValueError(
                "DEdit ED brush geometry is editor-preview only: the editor cannot "
                "produce the runtime BSP structures required by MM9. Choose a "
                "catalog game model, or compile the prefab to a v66 DAT with DEdit."
            )
        return _build_legacy_ed_import_plan(
            target_bsp,
            prefab_path,
            info,
            new_name=new_name,
            target_pos=target_pos,
            target_yaw=target_yaw,
            include_roles=include_roles,
            collision_mode=collision_mode,
            collision_thickness=collision_thickness,
            collision_segment_length=collision_segment_length,
            placement_anchor=placement_anchor,
            target_object_names=target_object_names,
        )
    with open(prefab_path, "rb") as f:
        prefab_dat = f.read()
    prefab_bsp = bsp.parse(prefab_dat)

    source_models = _select_static_models(
        prefab_bsp,
        info,
        include_roles,
        allow_unsafe_visibility=allow_unsafe_visibility,
    )
    if not source_models:
        raise ValueError(f"{prefab_path!r} has no importable static BSP models")

    target = _as_vec3(target_pos, "target_pos")
    prefix = _sanitize_model_name(
        new_name or suggest_import_name(target_bsp, prefab_path, target_object_names)
    )
    new_names = _new_model_names(prefix, source_models)
    source_pivot = _placement_source_pivot(placement_anchor, source_models, info)
    collision_mode = _normalize_collision_mode(collision_mode)
    physics_models = _models_for_roles(prefab_bsp, info, {"physics"})
    collision_sources = physics_models if physics_models else source_models
    collision_names = (
        _collision_model_names(prefix, collision_sources)
        if collision_mode == "invisible_bsp"
        else []
    )
    if validate_runtime_bsp:
        checked_models = list(source_models)
        if collision_mode == "invisible_bsp":
            seen = {
                str(getattr(model, "name", "") or "").casefold()
                for model in checked_models
            }
            checked_models.extend(
                model for model in collision_sources
                if str(getattr(model, "name", "") or "").casefold() not in seen
            )
        validate_compiled_runtime_models(prefab_dat, prefab_bsp, checked_models)
    _validate_target_names(
        target_bsp,
        [*new_names, *collision_names],
        target_object_names,
    )

    submodels: List[object] = []
    source_names: List[str] = []
    roles: List[str] = []
    info_flags_overrides: List[Optional[int]] = []
    for source_model, model_name in zip(source_models, new_names):
        raw = prefab_bsp.raw_model_bytes(prefab_dat, source_model)
        if raw is None:
            raise ValueError(f"prefab BSP model {source_model.name!r} has no recoverable byte range")
        role = next((m.role for m in info.models if m.name.lower() == source_model.name.lower()), "geometry")
        override = _info_flags_override_for_role(role)
        submodels.append(door_clone.DoorSubmodelClone(
            source_name=source_model.name,
            new_name=model_name,
            source_model=source_model,
            raw_bytes=bytes(raw),
            source_pivot=source_pivot,
            target_pivot=target,
            yaw_radians=float(target_yaw),
            info_flags_override=override,
        ))
        source_names.append(source_model.name)
        roles.append(role)
        info_flags_overrides.append(override)

    if collision_mode == "invisible_bsp":
        for source_model, model_name in zip(collision_sources, collision_names):
            raw = prefab_bsp.raw_model_bytes(prefab_dat, source_model)
            if raw is None:
                raise ValueError(f"prefab BSP model {source_model.name!r} has no recoverable byte range")
            submodels.append(door_clone.DoorSubmodelClone(
                source_name=source_model.name,
                new_name=model_name,
                source_model=source_model,
                raw_bytes=bytes(raw),
                source_pivot=source_pivot,
                target_pivot=target,
                yaw_radians=float(target_yaw),
                info_flags_override=2,
            ))
            source_names.append(source_model.name)
            roles.append("collision_helper")
            info_flags_overrides.append(2)
    elif collision_mode == "box_approx":
        if not allow_generated_bsp:
            raise ValueError(
                "Generated collision boxes are editor-preview BSP and are not safe for "
                "MM9 runtime DAT files. Use authored PhysicsBSP collision or no helper."
            )
        box_collision_names: List[str] = []
        for source_model, model_name in zip(source_models, new_names):
            target_min, target_max = door_clone.transform_bounds(
                source_model.min_box,
                source_model.max_box,
                source_pivot,
                target,
                float(target_yaw),
            )
            target_min, target_max = _thin_collision_bounds(
                target_min, target_max, collision_thickness
            )
            segments = _segment_collision_bounds(
                target_min, target_max, collision_segment_length
            )
            segment_names = _collision_segment_names(model_name, len(segments))
            box_collision_names.extend(segment_names)
            for segment_name, (segment_min, segment_max) in zip(segment_names, segments):
                submodels.append(_compile_collision_box(
                    segment_name,
                    segment_min,
                    segment_max,
                ))
                source_names.append("generated_geometry")
                roles.append("collision_box")
                info_flags_overrides.append(2)
        _validate_target_names(
            target_bsp,
            [*new_names, *box_collision_names],
            target_object_names,
        )
        collision_names = box_collision_names

    return PrefabBspImportPlan(
        source_path=os.path.abspath(prefab_path),
        new_name=prefix,
        target_pos=target,
        target_yaw=float(target_yaw),
        placement_anchor=_normalize_placement_anchor(placement_anchor),
        source_pivot=source_pivot,
        submodels=submodels,
        visible_model_names=list(new_names),
        collision_model_names=list(collision_names),
        source_model_names=source_names,
        source_model_roles=roles,
        info_flags_overrides=info_flags_overrides,
    )


def build_grouped_import_plan(
    target_bsp: bsp.BspWorld,
    prefab_path: str,
    groups: Sequence[PrefabBrushImportGroup],
    *,
    target_pos: Sequence[float],
    target_yaw: float,
    source_pivot: Sequence[float],
    placement_anchor: str,
    allow_generated_bsp: bool = True,
    validate_runtime_bsp: bool = False,
) -> PrefabBspImportPlan:
    """Build role-preserving BSP output for a behavioral prefab graph.

    Each group becomes one independently named submodel. DEdit groups may
    combine several child brushes owned by the same controller; compiled DAT
    groups must refer to a single already-compiled model.
    """
    if not groups:
        raise ValueError("behavioral prefab BSP plan has no brush groups")
    if validate_runtime_bsp and abs(float(target_yaw)) > 1.0e-7:
        raise ValueError(
            "Compiled behavioral BSP yaw is not runtime-safe yet because its "
            "physics block grid would need to be rebuilt. Place it without rotation."
        )
    target = _as_vec3(target_pos, "target_pos")
    pivot = _as_vec3(source_pivot, "source_pivot")
    names = [str(group.target_name or "").strip() for group in groups]
    if any(not name for name in names):
        raise ValueError("behavioral prefab BSP group has an empty target name")
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("behavioral prefab BSP groups create duplicate model names")
    # A behavioral controller intentionally has the same name as its BSP.
    # Only pre-existing target BSP names are collisions here.
    _validate_target_names(target_bsp, names, None)

    with open(prefab_path, "rb") as handle:
        source_data = handle.read()
    version = int.from_bytes(source_data[:4], "little") if len(source_data) >= 4 else -1
    submodels: List[object] = []
    source_names: List[str] = []
    roles: List[str] = []
    overrides: List[Optional[int]] = []
    if version == legacy_ed.LEGACY_ED_VERSION:
        if not allow_generated_bsp:
            raise ValueError(
                "Behavioral prefabs with DEdit ED brushes require a DEdit-compiled "
                "v66 DAT. Object-only ED prefabs remain supported."
            )
        analysis = legacy_ed.analyze_legacy_ed_bytes(
            source_data,
            source_path=os.path.abspath(prefab_path),
        )
        models = list(analysis.geometry_scene.mesh_models())
        for group in groups:
            selected = []
            for index in group.source_indices:
                if not 0 <= int(index) < len(models):
                    raise ValueError(f"DEdit brush index {index} is outside the geometry stream")
                selected.append(models[int(index)])
            if not selected:
                raise ValueError(f"behavioral BSP group {group.target_name!r} is empty")
            source_model = _legacy_ed_combined_mesh(
                analysis,
                selected,
                group.target_name,
            )
            submodels.append(_compile_transformed_source_model(
                source_model,
                group.target_name,
                pivot,
                target,
                float(target_yaw),
            ))
            source_names.append(
                ",".join(str(int(index)) for index in group.source_indices)
            )
            roles.append(group.role)
            overrides.append(2)
    elif version == 66:
        source_bsp = bsp.parse(source_data)
        for group in groups:
            if len(group.source_indices) != 1:
                raise ValueError(
                    "compiled DAT behavioral groups must map one source BSP model "
                    "to one target model"
                )
            index = int(group.source_indices[0])
            if not 0 <= index < len(source_bsp.world_models):
                raise ValueError(f"compiled BSP model index {index} is outside the source")
            model = source_bsp.world_models[index]
            if validate_runtime_bsp:
                validate_compiled_runtime_models(source_data, source_bsp, [model])
            raw = source_bsp.raw_model_bytes(source_data, model)
            if raw is None:
                raise ValueError(f"compiled BSP model {model.name!r} has no recoverable record")
            override = _info_flags_override_for_role(group.role)
            submodels.append(door_clone.DoorSubmodelClone(
                source_name=model.name,
                new_name=group.target_name,
                source_model=model,
                raw_bytes=bytes(raw),
                source_pivot=pivot,
                target_pivot=target,
                yaw_radians=float(target_yaw),
                info_flags_override=override,
            ))
            source_names.append(model.name)
            roles.append(group.role)
            overrides.append(override)
    else:
        raise ValueError(
            f"unsupported prefab version {version}; expected 66 or {legacy_ed.LEGACY_ED_VERSION}"
        )

    root_name = names[0]
    return PrefabBspImportPlan(
        source_path=os.path.abspath(prefab_path),
        new_name=root_name,
        target_pos=target,
        target_yaw=float(target_yaw),
        placement_anchor=_normalize_placement_anchor(placement_anchor),
        source_pivot=pivot,
        submodels=submodels,
        visible_model_names=list(names),
        collision_model_names=[],
        source_model_names=source_names,
        source_model_roles=roles,
        info_flags_overrides=overrides,
        import_mode="behavioral",
    )


def validate_compiled_runtime_models(
    dat_bytes: bytes,
    parsed_world: bsp.BspWorld,
    models: Sequence[object],
) -> None:
    """Reject preview/minimal records masquerading as runtime-compiled BSP."""
    names = [str(getattr(model, "name", "") or "") for model in models]
    inspections = bsp_record_inspector.inspect_dat(
        dat_bytes,
        names,
        parsed_world=parsed_world,
    )
    for name in names:
        record = inspections.get(name)
        reasons = []
        if record is None or not record.present:
            reasons.append("record is missing")
        elif record.raw_error:
            reasons.append(record.raw_error)
        else:
            if record.polygon_count and record.node_count <= 0:
                reasons.append("no runtime BSP node tree")
            if record.node_count and not record.bsp_node_valid_tree:
                reasons.append("invalid runtime BSP node tree")
            if record.node_count and record.bsp_node_root_count != 1:
                reasons.append(
                    f"expected one BSP root, found {record.bsp_node_root_count}"
                )
            if record.polygon_count and record.physics_block_cell_count <= 0:
                reasons.append("no physics block table")
            if record.physics_block_invalid_cell_tree_count:
                reasons.append(
                    f"{record.physics_block_invalid_cell_tree_count} invalid physics cell tree(s)"
                )
        if reasons:
            raise ValueError(
                f"Compiled prefab model {name!r} is not a complete MM9 runtime "
                "BSP record (" + "; ".join(reasons) + "). Compile the source "
                "with DEdit before importing it."
            )


def _build_legacy_ed_import_plan(
    target_bsp: bsp.BspWorld,
    prefab_path: str,
    info: prefab_inspector.PrefabInspection,
    *,
    new_name: Optional[str],
    target_pos: Sequence[float],
    target_yaw: float,
    include_roles: Optional[Sequence[str]],
    collision_mode: str,
    collision_thickness: float,
    collision_segment_length: float,
    placement_anchor: str,
    target_object_names: Optional[Sequence[str]],
) -> PrefabBspImportPlan:
    """Compile the brush geometry recovered from a legacy DEdit source prefab."""
    analysis = legacy_ed.load_legacy_ed_analysis_bundle(prefab_path)
    requested_roles = {str(role or "").strip().lower() for role in include_roles or ()}
    selected_roles = requested_roles or {"geometry"}
    supported_roles = {"geometry", "hidden_geometry"}
    unknown_roles = selected_roles - supported_roles
    if unknown_roles:
        raise ValueError(
            "DEdit source prefabs only expose static 'geometry' and "
            f"'hidden_geometry' roles; requested: {', '.join(sorted(unknown_roles))}"
        )

    source_geometry = _legacy_ed_geometry_models(
        analysis,
        include_hidden="hidden_geometry" in selected_roles,
        hidden_only=selected_roles == {"hidden_geometry"},
    )
    if not source_geometry:
        behavior = info.behavior_object_classes
        suffix = ""
        if behavior:
            classes = ", ".join(f"{name}={count}" for name, count in sorted(behavior.items()))
            suffix = (
                f" It contains resource/controller objects ({classes}), but this tool "
                "imports static brush geometry only."
            )
        raise ValueError(f"{prefab_path!r} has no importable static brush geometry.{suffix}")

    target = _as_vec3(target_pos, "target_pos")
    prefix = _sanitize_model_name(
        new_name or suggest_import_name(target_bsp, prefab_path, target_object_names)
    )
    source_model = _legacy_ed_combined_mesh(analysis, source_geometry, prefix)
    source_models = [source_model]
    source_pivot = _placement_source_pivot(placement_anchor, source_models, info)
    collision_mode = _normalize_collision_mode(collision_mode)
    collision_names = [f"{prefix}_Collision"] if collision_mode == "invisible_bsp" else []
    _validate_target_names(
        target_bsp,
        [prefix, *collision_names],
        target_object_names,
    )

    submodels: List[object] = [
        _compile_transformed_source_model(
            source_model,
            prefix,
            source_pivot,
            target,
            float(target_yaw),
        )
    ]
    source_names = ["legacy_ed_visible_brushes"]
    roles = ["geometry"]
    info_flags_overrides: List[Optional[int]] = [2]

    if collision_mode == "invisible_bsp":
        collision_geometry = _legacy_ed_collision_models(analysis) or source_geometry
        collision_source = _legacy_ed_combined_mesh(
            analysis,
            collision_geometry,
            collision_names[0],
        )
        submodels.append(_compile_transformed_source_model(
            collision_source,
            collision_names[0],
            source_pivot,
            target,
            float(target_yaw),
        ))
        source_names.append("legacy_ed_solid_brushes")
        roles.append("collision_helper")
        info_flags_overrides.append(2)
    elif collision_mode == "box_approx":
        transformed = preview_submodel(submodels[0])
        target_min, target_max = _thin_collision_bounds(
            transformed.min_box,
            transformed.max_box,
            collision_thickness,
        )
        segments = _segment_collision_bounds(
            target_min,
            target_max,
            collision_segment_length,
        )
        collision_names = _collision_segment_names(prefix, len(segments))
        _validate_target_names(
            target_bsp,
            [prefix, *collision_names],
            target_object_names,
        )
        for collision_name, (segment_min, segment_max) in zip(collision_names, segments):
            submodels.append(_compile_collision_box(collision_name, segment_min, segment_max))
            source_names.append("generated_geometry")
            roles.append("collision_box")
            info_flags_overrides.append(2)

    return PrefabBspImportPlan(
        source_path=os.path.abspath(prefab_path),
        new_name=prefix,
        target_pos=target,
        target_yaw=float(target_yaw),
        placement_anchor=_normalize_placement_anchor(placement_anchor),
        source_pivot=source_pivot,
        submodels=submodels,
        visible_model_names=[prefix],
        collision_model_names=list(collision_names),
        source_model_names=source_names,
        source_model_roles=roles,
        info_flags_overrides=info_flags_overrides,
    )


def _legacy_ed_geometry_models(
    analysis: legacy_ed.LegacyEdAnalysisBundle,
    *,
    include_hidden: bool,
    hidden_only: bool,
) -> List[geometry_scene.GeometryModel]:
    brush_records = [
        record for record in analysis.object_scan.records
        if record.class_name.lower() == "brush"
    ]
    selected: List[geometry_scene.GeometryModel] = []
    for index, model in enumerate(analysis.geometry_scene.mesh_models()):
        record = brush_records[index] if index < len(brush_records) else None
        hidden = bool(record and record.property_value("Invisible", False))
        if hidden_only and not hidden:
            continue
        if not include_hidden and hidden:
            continue
        selected.append(model)
    return selected


def _legacy_ed_collision_models(
    analysis: legacy_ed.LegacyEdAnalysisBundle,
) -> List[geometry_scene.GeometryModel]:
    brush_records = [
        record for record in analysis.object_scan.records
        if record.class_name.lower() == "brush"
    ]
    selected: List[geometry_scene.GeometryModel] = []
    for index, model in enumerate(analysis.geometry_scene.mesh_models()):
        record = brush_records[index] if index < len(brush_records) else None
        if record is None or bool(record.property_value("Solid", True)):
            selected.append(model)
    return selected


def _legacy_ed_combined_mesh(
    analysis: legacy_ed.LegacyEdAnalysisBundle,
    source_models: Sequence[geometry_scene.GeometryModel],
    model_name: str,
) -> bsp.WorldModelMesh:
    combined = geometry_scene.GeometryModel(name=model_name)
    for source_model in source_models:
        point_offset = len(combined.points)
        combined.points.extend(tuple(float(value) for value in point) for point in source_model.points)
        for face in source_model.faces:
            copied_face = copy.deepcopy(face)
            copied_face.vertex_indices = [int(index) + point_offset for index in face.vertex_indices]
            combined.faces.append(copied_face)
    mesh = geometry_mesh.geometry_model_to_bsp_mesh(
        combined,
        model_name,
        analysis.geometry_scene.material_texture_map(),
        geometry_mesh.identity_matrix(),
    )
    brush_records = [
        record for record in analysis.object_scan.records
        if record.class_name.lower() == "brush"
    ]
    for polygon in mesh.polygons:
        metadata = getattr(polygon, "mm9_source_face", {}) or {}
        brush_index = metadata.get("brush_index")
        is_solid = True
        if isinstance(brush_index, int) and 0 <= brush_index < len(brush_records):
            is_solid = bool(brush_records[brush_index].property_value("Solid", True))
        if is_solid:
            mesh.surfaces[polygon.surface_index].flags |= bsp.SURF_SOLID
    return mesh


def _compile_transformed_source_model(
    source_model: bsp.WorldModelMesh,
    new_name: str,
    source_pivot: Vec3,
    target_pivot: Vec3,
    yaw_radians: float,
) -> bsp_compile.CompiledWorldModelRecord:
    transformed = door_clone.translated_model_clone(door_clone.DoorSubmodelClone(
        source_name=source_model.name,
        new_name=new_name,
        source_model=source_model,
        raw_bytes=b"",
        source_pivot=source_pivot,
        target_pivot=target_pivot,
        yaw_radians=float(yaw_radians),
        info_flags_override=2,
    ))
    return bsp_compile.compile_world_model_record(transformed, info_flags=2)


def build_preview_bsp(
    target_bsp: bsp.BspWorld,
    import_plans: Sequence[PrefabBspImportPlan],
) -> bsp.BspWorld:
    preview = _shallow_bsp_with_original_models(target_bsp)
    for plan in import_plans or []:
        for submodel in plan.submodels:
            preview.world_models.append(preview_submodel(submodel))
    return preview


def preview_submodel(submodel: object) -> bsp.WorldModelMesh:
    """Return the model represented by a copied or compiled import record."""
    if isinstance(submodel, bsp_compile.CompiledWorldModelRecord):
        return copy.deepcopy(submodel.model)
    return door_clone.translated_model_clone(submodel)


def _shallow_bsp_with_original_models(target_bsp: bsp.BspWorld) -> bsp.BspWorld:
    return bsp.BspWorld(
        version=target_bsp.version,
        world_info=target_bsp.world_info,
        obj_pos=target_bsp.obj_pos,
        ren_pos=target_bsp.ren_pos,
        world_model_table_start=target_bsp.world_model_table_start,
        world_models=list(target_bsp.world_models),
        parse_warnings=list(getattr(target_bsp, "parse_warnings", []) or []),
    )


def _select_static_models(
    prefab_bsp: bsp.BspWorld,
    info: prefab_inspector.PrefabInspection,
    include_roles: Optional[Sequence[str]],
    *,
    allow_unsafe_visibility: bool = False,
) -> List[bsp.WorldModelMesh]:
    role_by_name = {model.name.lower(): model.role for model in info.models}
    requested = {str(role).lower() for role in include_roles or []}
    if "visibility" in requested and not allow_unsafe_visibility:
        raise ValueError(
            "VisBSP import is unsafe because its leaf/PVS data belongs to the "
            "source world; enable the diagnostic visibility override explicitly"
        )
    if requested:
        roles = requested
    else:
        available = set(role_by_name.values())
        visual_roles = available & {"geometry", "controller_geometry"}
        if visual_roles:
            roles = visual_roles
        elif "physics" in available:
            roles = {"physics"}
        else:
            roles = set()

    return [
        model
        for model in prefab_bsp.world_models
        if role_by_name.get(model.name.lower(), "geometry") in roles
    ]


def _models_for_roles(
    prefab_bsp: bsp.BspWorld,
    info: prefab_inspector.PrefabInspection,
    roles: set[str],
) -> List[bsp.WorldModelMesh]:
    role_by_name = {model.name.lower(): model.role for model in info.models}
    return [
        model
        for model in prefab_bsp.world_models
        if role_by_name.get(model.name.lower(), "geometry") in roles
    ]


def _new_model_names(prefix: str, models: Sequence[bsp.WorldModelMesh]) -> List[str]:
    if len(models) == 1:
        return [prefix]
    names: List[str] = []
    seen: set[str] = set()
    for index, model in enumerate(models, start=1):
        suffix = _sanitize_model_name(str(model.name or f"Model{index}"))
        name = f"{prefix}_{suffix}"
        if name.lower() in seen:
            name = f"{name}{index}"
        seen.add(name.lower())
        names.append(name)
    return names


def _collision_model_names(
    prefix: str,
    models: Sequence[bsp.WorldModelMesh],
) -> List[str]:
    if len(models) == 1:
        return [f"{prefix}_Collision"]
    return [
        f"{prefix}_Collision_{_sanitize_model_name(model.name or str(index))}"
        for index, model in enumerate(models, start=1)
    ]


def _collision_segment_names(model_name: str, segment_count: int) -> List[str]:
    if segment_count <= 1:
        return [f"{model_name}_Collision"]
    return [f"{model_name}_Collision{index}" for index in range(1, segment_count + 1)]


def _normalize_collision_mode(value: str) -> str:
    mode = str(value or "none").lower()
    if mode in {"none", "off", "false", "0"}:
        return "none"
    if mode in {"invisible_bsp", "collision_helper", "box", "box_approx"}:
        return "box_approx" if mode in {"box", "box_approx"} else "invisible_bsp"
    raise ValueError(f"unsupported prefab collision mode: {value!r}")


def _normalize_placement_anchor(value: str) -> str:
    anchor = str(value or "bottom_center").strip().lower()
    aliases = {
        "bottom": "bottom_center",
        "floor": "bottom_center",
        "centre": "center",
        "origin": "original_origin",
        "controller": "controller_pivot",
    }
    anchor = aliases.get(anchor, anchor)
    if anchor not in {"bottom_center", "original_origin", "center", "controller_pivot"}:
        raise ValueError(f"unsupported prefab placement anchor: {value!r}")
    return anchor


def _placement_source_pivot(
    placement_anchor: str,
    source_models: Sequence[bsp.WorldModelMesh],
    info: prefab_inspector.PrefabInspection,
) -> Vec3:
    anchor = _normalize_placement_anchor(placement_anchor)
    if anchor == "original_origin":
        return (0.0, 0.0, 0.0)

    min_box, max_box = _combined_model_bounds(source_models)
    if anchor == "center":
        return (
            (min_box[0] + max_box[0]) * 0.5,
            (min_box[1] + max_box[1]) * 0.5,
            (min_box[2] + max_box[2]) * 0.5,
        )
    if anchor == "controller_pivot":
        model_names = {model.name.lower() for model in source_models}
        pivots = [
            item.position
            for item in info.objects
            if item.name.lower() in model_names and item.position is not None
        ]
        if not pivots and info.source_format == "legacy_ed":
            pivots = [
                item.position
                for item in info.behavior_objects
                if item.position is not None
            ]
        if not pivots:
            raise ValueError("prefab has no controller geometry pivot")
        return pivots[0]
    return (
        (min_box[0] + max_box[0]) * 0.5,
        min_box[1],
        (min_box[2] + max_box[2]) * 0.5,
    )


def _combined_model_bounds(models: Sequence[bsp.WorldModelMesh]) -> Tuple[Vec3, Vec3]:
    if not models:
        raise ValueError("cannot calculate placement anchor without BSP models")
    return (
        tuple(min(float(model.min_box[axis]) for model in models) for axis in range(3)),
        tuple(max(float(model.max_box[axis]) for model in models) for axis in range(3)),
    )  # type: ignore[return-value]


def _compile_collision_box(
    name: str,
    min_box: Vec3,
    max_box: Vec3,
) -> bsp_compile.CompiledWorldModelRecord:
    """Compile a self-contained six-face collision submodel."""
    x0, y0, z0 = min_box
    x1, y1, z1 = max_box
    points = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    surface = bsp.Surface(
        uv_o=(x0, y0, z0),
        uv_p=(1.0, 0.0, 0.0),
        uv_q=(0.0, 1.0, 0.0),
        texture_index=0,
        flags=bsp.SURF_SOLID,
        texture_flags=0,
    )
    model = bsp.WorldModelMesh(
        name=name,
        min_box=min_box,
        max_box=max_box,
        translation=(0.0, 0.0, 0.0),
        points=points,
        polygons=[
            bsp.Polygon([0, 3, 2, 1], 0, 0),
            bsp.Polygon([4, 5, 6, 7], 0, 1),
            bsp.Polygon([0, 4, 7, 3], 0, 2),
            bsp.Polygon([1, 2, 6, 5], 0, 3),
            bsp.Polygon([0, 1, 5, 4], 0, 4),
            bsp.Polygon([3, 7, 6, 2], 0, 5),
        ],
        texture_names=[r"TEXTURES\LevelTextures\Misc\Firethrough.dtx"],
        surfaces=[surface],
    )
    return bsp_compile.compile_world_model_record(model, info_flags=2)


def _thin_collision_bounds(min_box: Vec3, max_box: Vec3, thickness: float = 8.0) -> Tuple[Vec3, Vec3]:
    mins = [float(v) for v in min_box]
    maxs = [float(v) for v in max_box]
    x_size = maxs[0] - mins[0]
    z_size = maxs[2] - mins[2]
    if x_size <= 0.0 or z_size <= 0.0:
        return (tuple(mins), tuple(maxs))  # type: ignore[return-value]
    thin_axis = 0 if x_size <= z_size else 2
    target_thickness = max(1.0, float(thickness))
    thickness = min(maxs[thin_axis] - mins[thin_axis], target_thickness)
    center = (mins[thin_axis] + maxs[thin_axis]) * 0.5
    mins[thin_axis] = center - thickness * 0.5
    maxs[thin_axis] = center + thickness * 0.5
    return (tuple(mins), tuple(maxs))  # type: ignore[return-value]


def _segment_collision_bounds(
    min_box: Vec3,
    max_box: Vec3,
    segment_length: float = 512.0,
) -> List[Tuple[Vec3, Vec3]]:
    mins = [float(v) for v in min_box]
    maxs = [float(v) for v in max_box]
    max_segment = max(64.0, float(segment_length))
    x_size = maxs[0] - mins[0]
    z_size = maxs[2] - mins[2]
    axis = 0 if x_size >= z_size else 2
    length = maxs[axis] - mins[axis]
    if length <= max_segment:
        return [(tuple(mins), tuple(maxs))]  # type: ignore[list-item]
    count = max(1, int((length + max_segment - 1.0) // max_segment))
    step = length / count
    segments: List[Tuple[Vec3, Vec3]] = []
    for index in range(count):
        seg_min = list(mins)
        seg_max = list(maxs)
        seg_min[axis] = mins[axis] + step * index
        seg_max[axis] = maxs[axis] if index == count - 1 else mins[axis] + step * (index + 1)
        segments.append((tuple(seg_min), tuple(seg_max)))  # type: ignore[arg-type]
    return segments


def _validate_model_names(target_bsp: bsp.BspWorld, new_names: Sequence[str]) -> None:
    existing = {
        str(getattr(model, "name", "") or "").lower()
        for model in getattr(target_bsp, "world_models", []) or []
    }
    lowered = [name.lower() for name in new_names]
    if len(set(lowered)) != len(lowered):
        raise ValueError(f"imported BSP model names must be unique: {', '.join(new_names)}")
    for name in new_names:
        if not str(name or "").strip():
            raise ValueError("imported BSP model names must be non-empty")
        if name.lower() in existing:
            raise ValueError(f"BSP model named {name!r} already exists in the target level")


def _validate_target_names(
    target_bsp: bsp.BspWorld,
    new_names: Sequence[str],
    target_object_names: Optional[Sequence[str]],
) -> None:
    _validate_model_names(target_bsp, new_names)
    existing_objects = {
        str(name or "").strip().lower()
        for name in target_object_names or ()
        if str(name or "").strip()
    }
    for name in new_names:
        if name.lower() in existing_objects:
            raise ValueError(f"WorldObject named {name!r} already exists in the target level")


def _sanitize_model_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "", str(value or ""))
    return cleaned or "Prefab"


def _info_flags_override_for_role(role: str) -> Optional[int]:
    # Converted prefabs often contain only PhysicsBSP/VisBSP.  VisBSP records
    # carry leaf/visibility payloads that are not safe to splice into another
    # level's model list.  PhysicsBSP has the plain polygon data we need; patch
    # it to the ordinary submodel flag used by normal static world models.
    return 2 if role == "physics" else None


def _as_vec3(value: object, prop_name: str) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{prop_name} must be a 3-vector, got {value!r}")
    return (float(value[0]), float(value[1]), float(value[2]))
