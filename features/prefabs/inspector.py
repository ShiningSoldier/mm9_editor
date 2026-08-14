"""
prefab_inspector.py
===================

Read-only inspection for DEdit source prefabs and converted prefab DAT files.

Converted prefabs are valid LithTech v66 mini-worlds.  Some contain only BSP
records, while door-like prefabs can also contain controller WorldObjects.  The
inspector keeps this analysis separate from any future import/mutation path.
"""

from __future__ import annotations

import os
import struct
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

import _path_setup  # noqa: F401
from core import bsp
from features.dat_editing import legacy_ed
import mm9_patch as patcher


Vec3 = Tuple[float, float, float]

SYSTEM_MODEL_NAMES = {"physicsbsp", "visbsp"}


@dataclass(frozen=True)
class PrefabObjectInfo:
    index: int
    class_name: str
    name: str = ""
    prop_count: int = 0
    position: Optional[Vec3] = None


@dataclass(frozen=True)
class PrefabModelInfo:
    index: int
    name: str
    role: str
    polygon_count: int
    point_count: int
    texture_count: int
    min_box: Vec3
    max_box: Vec3
    raw_start: Optional[int] = None
    raw_end: Optional[int] = None
    next_world_item: Optional[int] = None

    @property
    def is_system(self) -> bool:
        return self.role in {"physics", "visibility"}


@dataclass(frozen=True)
class PrefabInspection:
    path: str
    file_size: int
    version: int
    object_data_pos: int
    render_data_pos: int
    object_count: int
    model_count: int
    objects: List[PrefabObjectInfo] = field(default_factory=list)
    models: List[PrefabModelInfo] = field(default_factory=list)
    parse_warnings: List[str] = field(default_factory=list)
    bounds_min: Optional[Vec3] = None
    bounds_max: Optional[Vec3] = None
    source_format: str = "compiled_dat"
    has_authored_collision: bool = False

    @property
    def object_classes(self) -> Dict[str, int]:
        return dict(Counter(obj.class_name for obj in self.objects))

    @property
    def model_roles(self) -> Dict[str, int]:
        return dict(Counter(model.role for model in self.models))

    @property
    def total_polygons(self) -> int:
        return sum(model.polygon_count for model in self.models)

    @property
    def has_only_system_geometry(self) -> bool:
        return bool(self.models) and all(model.is_system for model in self.models)

    @property
    def behavior_objects(self) -> List[PrefabObjectInfo]:
        """Source objects whose runtime behavior a static import cannot retain."""
        source_only_classes = {"brush", "worldproperties"}
        return [
            obj for obj in self.objects
            if str(obj.class_name or "").strip().lower() not in source_only_classes
        ]

    @property
    def behavior_object_classes(self) -> Dict[str, int]:
        return dict(Counter(obj.class_name for obj in self.behavior_objects))


def inspect_prefab(path: str) -> PrefabInspection:
    """Parse *path* as a compiled DAT or legacy DEdit ED prefab."""
    with open(path, "rb") as f:
        data = f.read()

    if len(data) < 4:
        raise ValueError(f"prefab file is too short: {path}")
    version = struct.unpack_from("<I", data, 0)[0]
    if version == legacy_ed.LEGACY_ED_VERSION:
        return _inspect_legacy_ed(path, data)
    if version != 66:
        raise ValueError(
            f"unsupported prefab version {version}; expected a compiled DAT "
            f"(version 66) or DEdit source prefab (version {legacy_ed.LEGACY_ED_VERSION})"
        )

    header = patcher.Header.parse(data)
    world = patcher.World.load(path)
    bsp_world = bsp.parse(data)

    objects = [
        PrefabObjectInfo(
            index=index,
            class_name=obj.type_str,
            name=str(obj.get("Name") or ""),
            prop_count=len(obj.props),
            position=_optional_vec3(obj.get("Pos")),
        )
        for index, obj in enumerate(world.objects)
    ]

    models = [
        PrefabModelInfo(
            index=index,
            name=model.name,
            role=classify_model(model, objects),
            polygon_count=len(model.polygons),
            point_count=len(model.points),
            texture_count=len(model.texture_names),
            min_box=model.min_box,
            max_box=model.max_box,
            raw_start=model.raw_start,
            raw_end=model.raw_end,
            next_world_item=model.next_world_item,
        )
        for index, model in enumerate(bsp_world.world_models)
    ]
    bounds_min, bounds_max = _combined_bounds((m.min_box, m.max_box) for m in models)

    return PrefabInspection(
        path=os.path.abspath(path),
        file_size=len(data),
        version=header.version,
        object_data_pos=header.obj_pos,
        render_data_pos=header.ren_pos,
        object_count=len(objects),
        model_count=len(models),
        objects=objects,
        models=models,
        parse_warnings=list(bsp_world.parse_warnings),
        bounds_min=bounds_min,
        bounds_max=bounds_max,
        source_format="compiled_dat",
        has_authored_collision=any(model.role == "physics" for model in models),
    )


def _inspect_legacy_ed(path: str, data: bytes) -> PrefabInspection:
    analysis = legacy_ed.analyze_legacy_ed_bytes(data, source_path=os.path.abspath(path))
    scene = analysis.geometry_scene
    scan = analysis.object_scan
    brush_names = list(analysis.node_layout.brush_names)
    brush_records = [record for record in scan.records if record.class_name.lower() == "brush"]

    objects = [
        PrefabObjectInfo(
            index=index,
            class_name=record.class_name,
            name=str(record.property_value("Name") or ""),
            prop_count=len(record.properties),
            position=_optional_vec3(record.property_value("Pos")),
        )
        for index, record in enumerate(scan.records)
    ]
    models: List[PrefabModelInfo] = []
    for index, model in enumerate(scene.mesh_models()):
        min_box, max_box = _point_bounds(model.points)
        source_record = brush_records[index] if index < len(brush_records) else None
        invisible = bool(source_record and source_record.property_value("Invisible", False))
        name = (
            brush_names[index]
            if index < len(brush_names) and str(brush_names[index] or "").strip()
            else model.name
        )
        models.append(PrefabModelInfo(
            index=index,
            name=str(name),
            role="hidden_geometry" if invisible else "geometry",
            polygon_count=len(model.faces),
            point_count=len(model.points),
            texture_count=len({face.material_name for face in model.faces}),
            min_box=min_box,
            max_box=max_box,
        ))
    bounds_min, bounds_max = _combined_bounds(
        (model.min_box, model.max_box)
        for model in models
        if model.role == "geometry"
    )
    warnings: List[str] = []
    if not models and objects:
        warnings.append(
            "This source prefab contains object/resource references but no static brush geometry."
        )
    return PrefabInspection(
        path=os.path.abspath(path),
        file_size=len(data),
        version=analysis.object_scan.version,
        object_data_pos=0,
        render_data_pos=0,
        object_count=len(objects),
        model_count=len(models),
        objects=objects,
        models=models,
        parse_warnings=warnings,
        bounds_min=bounds_min,
        bounds_max=bounds_max,
        source_format="legacy_ed",
        has_authored_collision=any(
            bool(record.property_value("Solid", False))
            for record in brush_records
        ),
    )


def classify_model(model: bsp.WorldModelMesh, objects: Iterable[PrefabObjectInfo]) -> str:
    """Return a conservative role label for a prefab BSP model."""
    name = str(model.name or "")
    key = name.lower()
    if key == "physicsbsp":
        return "physics"
    if key == "visbsp":
        return "visibility"
    if model.is_skybox():
        return "skybox"

    object_names = {obj.name.lower() for obj in objects if obj.name}
    if key in object_names:
        return "controller_geometry"
    return "geometry"


def _optional_vec3(value: object) -> Optional[Vec3]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        return None


def format_report(info: PrefabInspection, max_models: int = 12, max_objects: int = 12) -> str:
    """Format a human-readable inspector report for UI dialogs and logs."""
    lines: List[str] = []
    lines.append(f"Prefab: {info.path}")
    lines.append(f"Source format: {info.source_format}")
    lines.append(f"Version: {info.version}")
    lines.append(f"Size: {info.file_size} bytes")
    lines.append(f"Objects: {info.object_count}")
    lines.append(f"BSP models: {info.model_count}")
    lines.append(f"Total polygons: {info.total_polygons}")
    if info.bounds_min and info.bounds_max:
        lines.append(f"Bounds: {_fmt_vec(info.bounds_min)} -> {_fmt_vec(info.bounds_max)}")
    if info.model_roles:
        lines.append("Model roles: " + _fmt_counts(info.model_roles))
    if info.object_classes:
        lines.append("Object classes: " + _fmt_counts(info.object_classes))
    if info.has_only_system_geometry:
        lines.append("Note: this prefab contains only system-named BSP models.")
    if info.parse_warnings:
        lines.append("Warnings:")
        for warning in info.parse_warnings[:8]:
            lines.append(f"  - {warning}")

    if info.objects:
        lines.append("")
        lines.append("Objects:")
        for obj in info.objects[:max_objects]:
            suffix = f" name={obj.name}" if obj.name else ""
            lines.append(f"  [{obj.index}] {obj.class_name}{suffix} ({obj.prop_count} props)")
        if len(info.objects) > max_objects:
            lines.append(f"  ... {len(info.objects) - max_objects} more")

    if info.models:
        lines.append("")
        lines.append("BSP models:")
        for model in info.models[:max_models]:
            lines.append(
                f"  [{model.index}] {model.name} [{model.role}] "
                f"{model.polygon_count} polys, {model.point_count} points, "
                f"{model.texture_count} textures, bounds "
                f"{_fmt_vec(model.min_box)} -> {_fmt_vec(model.max_box)}"
            )
        if len(info.models) > max_models:
            lines.append(f"  ... {len(info.models) - max_models} more")

    return "\n".join(lines)


def _combined_bounds(bounds: Iterable[Tuple[Vec3, Vec3]]) -> Tuple[Optional[Vec3], Optional[Vec3]]:
    mins: List[float] = []
    maxs: List[float] = []
    for min_box, max_box in bounds:
        if not mins:
            mins = [float(v) for v in min_box]
            maxs = [float(v) for v in max_box]
            continue
        for i in range(3):
            mins[i] = min(mins[i], float(min_box[i]))
            maxs[i] = max(maxs[i], float(max_box[i]))
    if not mins:
        return None, None
    return (mins[0], mins[1], mins[2]), (maxs[0], maxs[1], maxs[2])


def _point_bounds(points: Iterable[Vec3]) -> Tuple[Vec3, Vec3]:
    values = list(points)
    if not values:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    return (
        tuple(min(float(point[axis]) for point in values) for axis in range(3)),
        tuple(max(float(point[axis]) for point in values) for axis in range(3)),
    )  # type: ignore[return-value]


def _fmt_vec(vec: Vec3) -> str:
    return f"({vec[0]:.1f}, {vec[1]:.1f}, {vec[2]:.1f})"


def _fmt_counts(counts: Dict[str, int]) -> str:
    return ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Inspect MM9 DEdit ED or compiled DAT prefabs")
    parser.add_argument("path", nargs="+", help="one or more prefab .ED/.DAT files")
    args = parser.parse_args(argv)

    for index, path in enumerate(args.path):
        if index:
            print()
        print(format_report(inspect_prefab(path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
