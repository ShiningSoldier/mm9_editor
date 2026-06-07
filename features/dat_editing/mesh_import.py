"""Preview-only OBJ + DAT sidecar import for additive geometry."""

from __future__ import annotations

import copy
import math
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from core import bsp
from features.dat_editing import geometry_scene
from features.dat_editing import gltf_import
from features.dat_editing import obj_workflow
from features.dat_editing import uv_projection
from features.dat_editing import bsp_compile


Vec3 = Tuple[float, float, float]
Vec2 = Tuple[float, float]
COLLISION_TEXTURE = "TEXTURES\\LevelTextures\\Misc\\Firethrough.dtx"


@dataclass(frozen=True)
class ImportedMeshModel:
    name: str
    mesh: bsp.WorldModelMesh
    source_object_name: str = ""
    role: str = "visible"


@dataclass(frozen=True)
class MeshBspImportPlan:
    obj_path: str
    meta_path: str
    new_name: str
    target_pos: Vec3
    target_yaw: float
    original_center: Vec3
    collision_mode: str = "none"
    models: List[ImportedMeshModel] = field(default_factory=list)
    source_format: str = ""
    metadata_source: str = ""
    import_warnings: List[str] = field(default_factory=list)


def role_counts(models: Sequence[ImportedMeshModel]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in models:
        role = str(item.role or "visible")
        counts[role] = counts.get(role, 0) + 1
    return counts


def suggest_import_name(target_bsp: bsp.BspWorld, obj_path: str) -> str:
    stem = os.path.splitext(os.path.basename(obj_path))[0] or "MeshImport"
    stem = re.sub(r"_geometry$", "", stem, flags=re.IGNORECASE)
    stem = _sanitize_model_name(stem)
    existing = {
        str(getattr(model, "name", "") or "").lower()
        for model in getattr(target_bsp, "world_models", []) or []
    }
    if stem.lower() not in existing:
        return stem
    index = 1
    while f"{stem}{index}".lower() in existing:
        index += 1
    return f"{stem}{index}"


def build_mesh_import_plan(
    target_bsp: bsp.BspWorld,
    obj_path: str,
    meta_path: Optional[str] = None,
    new_name: Optional[str] = None,
    target_pos: Optional[Sequence[float]] = None,
    target_yaw: float = 0.0,
    collision_mode: str = "none",
    collision_thickness: float = 8.0,
    collision_segment_length: float = 512.0,
) -> MeshBspImportPlan:
    meta_path = _effective_meta_path(obj_path, meta_path)
    scene = load_geometry_scene(obj_path, meta_path or None)
    meta = dict(scene.metadata or {})
    material_to_texture = scene.material_texture_map()
    if not material_to_texture:
        material_to_texture = _material_texture_map_from_meta(meta)
    export_to_dat = meta.get("coordinate_system", {}).get("export_to_dat_matrix")
    if not export_to_dat:
        export_to_dat = _identity_matrix()

    parsed = scene.mesh_models()
    if not parsed:
        raise ValueError(f"{obj_path!r} has no mesh objects with faces")

    prefix = _sanitize_model_name(new_name or suggest_import_name(target_bsp, obj_path))
    names = _new_model_names(prefix, [obj.name for obj in parsed])
    collision_mode = _normalize_collision_mode(collision_mode)

    visible_imports: List[ImportedMeshModel] = []
    explicit_collision: List[ImportedMeshModel] = []
    for parsed_obj, model_name in zip(parsed, names):
        mesh = _parsed_obj_to_mesh(parsed_obj, model_name, material_to_texture, export_to_dat)
        role = _parsed_model_role(parsed_obj)
        if role in {"collision", "collision_only", "collision_helper"}:
            collision_name = _explicit_collision_name(model_name)
            explicit_collision.append(ImportedMeshModel(
                name=collision_name,
                mesh=_collision_helper_mesh(mesh, collision_name),
                source_object_name=parsed_obj.name,
                role="collision_explicit",
            ))
        else:
            visible_imports.append(ImportedMeshModel(
                name=model_name,
                mesh=mesh,
                source_object_name=parsed_obj.name,
                role="visible",
            ))

    if not visible_imports and not explicit_collision:
        raise ValueError(f"{obj_path!r} has no importable visible or collision mesh objects")

    original_center = _combined_center([item.mesh for item in [*visible_imports, *explicit_collision]])
    target = _as_vec3(target_pos, "target_pos") if target_pos is not None else original_center
    transformed = [
        ImportedMeshModel(
            name=item.name,
            mesh=_transform_mesh(item.mesh, original_center, target, float(target_yaw)),
            source_object_name=item.source_object_name,
            role=item.role,
        )
        for item in visible_imports
    ]
    transformed_explicit_collision = [
        ImportedMeshModel(
            name=item.name,
            mesh=_transform_mesh(item.mesh, original_center, target, float(target_yaw)),
            source_object_name=item.source_object_name,
            role=item.role,
        )
        for item in explicit_collision
    ]
    collision_models = _build_collision_models(
        transformed,
        collision_mode=collision_mode,
        collision_thickness=float(collision_thickness),
        collision_segment_length=float(collision_segment_length),
    )
    all_models = [*transformed, *transformed_explicit_collision, *collision_models]
    _validate_model_names(target_bsp, [item.name for item in all_models])
    for item in all_models:
        try:
            bsp_compile.analyze_model(item.mesh)
        except ValueError as exc:
            raise ValueError(f"imported BSP model {item.name!r} is invalid: {exc}") from exc
    return MeshBspImportPlan(
        obj_path=os.path.abspath(obj_path),
        meta_path=os.path.abspath(meta_path) if meta_path else "",
        new_name=prefix,
        target_pos=target,
        target_yaw=float(target_yaw),
        original_center=original_center,
        collision_mode=collision_mode,
        models=all_models,
        source_format=str(meta.get("format") or os.path.splitext(obj_path)[1].lstrip(".").lower() or "unknown"),
        metadata_source=str(meta.get("import_metadata_source") or ("sidecar" if meta else "missing")),
        import_warnings=[
            str(item)
            for item in (meta.get("import_warnings") or [])
            if str(item).strip()
        ],
    )


def build_preview_bsp(
    target_bsp: bsp.BspWorld,
    import_plans: Sequence[MeshBspImportPlan],
) -> bsp.BspWorld:
    preview = bsp.BspWorld(
        version=target_bsp.version,
        world_info=target_bsp.world_info,
        obj_pos=target_bsp.obj_pos,
        ren_pos=target_bsp.ren_pos,
        world_model_table_start=target_bsp.world_model_table_start,
        world_models=list(target_bsp.world_models),
        parse_warnings=list(getattr(target_bsp, "parse_warnings", []) or []),
    )
    for plan in import_plans or []:
        for item in plan.models:
            preview.world_models.append(copy.deepcopy(item.mesh))
    return preview


def object_specs(plan: MeshBspImportPlan) -> List[Tuple[str, Vec3]]:
    return [
        (item.name, _bounds_center(item.mesh.min_box, item.mesh.max_box))
        for item in plan.models
    ]


def is_collision_model(item: ImportedMeshModel) -> bool:
    return str(item.role or "") in {"collision_helper", "collision_box", "collision_slab", "collision_explicit"}


def validate_collision_controllers(
    import_plans: Sequence[MeshBspImportPlan],
    objects: Sequence[object],
) -> List[str]:
    object_by_name = {
        str(getattr(obj, "get", lambda _key, _default=None: _default)("Name") or "").lower(): obj
        for obj in objects or []
    }
    warnings: List[str] = []
    for plan in import_plans or []:
        for item in plan.models:
            if not is_collision_model(item):
                continue
            obj = object_by_name.get(item.name.lower())
            if obj is None:
                warnings.append(f"mesh import {plan.new_name!r}: collision helper {item.name!r} has no controller object")
                continue
            obj_type = str(getattr(obj, "type_str", "") or "")
            visible = getattr(obj, "get", lambda _key, _default=None: _default)("Visible")
            if obj_type != "InvisibleBrush" or int(visible or 0) != 0:
                warnings.append(
                    f"mesh import {plan.new_name!r}: collision helper {item.name!r} "
                    "must use a hidden InvisibleBrush controller"
                )
    return warnings


def import_summary(plan: MeshBspImportPlan, *, max_models: int = 8, max_warnings: int = 5) -> str:
    visible = [item for item in plan.models if not is_collision_model(item)]
    collision = [item for item in plan.models if is_collision_model(item)]
    polygon_count = sum(len(item.mesh.polygons) for item in plan.models)
    texture_names = sorted({
        texture
        for item in visible
        for texture in (getattr(item.mesh, "texture_names", []) or [])
        if texture
    })
    model_names = ", ".join(item.name for item in plan.models[:max_models])
    if len(plan.models) > max_models:
        model_names += f" (+{len(plan.models) - max_models} more)"
    texture_text = ", ".join(texture_names[:max_models]) if texture_names else "none"
    if len(texture_names) > max_models:
        texture_text += f" (+{len(texture_names) - max_models} more)"
    warning_lines = [
        f"- {warning}"
        for warning in plan.import_warnings[:max_warnings]
    ]
    if len(plan.import_warnings) > max_warnings:
        warning_lines.append(f"- +{len(plan.import_warnings) - max_warnings} more warning(s)")
    warnings_text = "\n".join(warning_lines) if warning_lines else "None"
    role_text = ", ".join(
        f"{role}={count}"
        for role, count in sorted(role_counts(plan.models).items())
    ) or "none"
    return (
        f"Source: {os.path.basename(plan.obj_path)} ({plan.source_format or 'unknown'})\n"
        f"Metadata: {plan.metadata_source or 'unknown'}\n"
        f"Models: {len(visible)} visible, {len(collision)} collision helper(s)\n"
        f"Roles: {role_text}\n"
        f"Polygons: {polygon_count}\n"
        f"DAT textures: {texture_text}\n"
        f"Created BSP model(s): {model_names or 'none'}\n\n"
        f"Warnings:\n{warnings_text}"
    )


def _material_texture_map_from_meta(meta: Dict[str, object]) -> Dict[str, str]:
    return {
        str(item.get("material_name") or ""): str(item.get("texture_name") or "Default")
        for item in meta.get("materials", []) or []
        if isinstance(item, dict)
    }


def load_obj_geometry_scene(path: str, meta: Optional[Dict[str, object]] = None) -> geometry_scene.GeometryScene:
    if not os.path.exists(path):
        raise ValueError(f"OBJ file was not found: {path}")
    objects: List[geometry_scene.GeometryModel] = []
    current = geometry_scene.GeometryModel(name=os.path.splitext(os.path.basename(path))[0] or "Mesh")
    material = "Default"
    global_points: List[Vec3] = []
    global_uvs: List[Vec2] = []
    local_map: Dict[int, int] = {}
    seen_materials: set[str] = set()

    def finish_current() -> None:
        nonlocal current, local_map
        if current.faces:
            objects.append(current)
        current = geometry_scene.GeometryModel(name=current.name)
        local_map = {}

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_number, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            head = parts[0]
            if head == "v" and len(parts) >= 4:
                try:
                    point = (float(parts[1]), float(parts[2]), float(parts[3]))
                except ValueError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid OBJ vertex coordinates") from exc
                if not all(math.isfinite(item) for item in point):
                    raise ValueError(f"{path}:{line_number}: OBJ vertex coordinates must be finite")
                global_points.append(point)
                continue
            if head == "vt" and len(parts) >= 3:
                try:
                    uv = (float(parts[1]), float(parts[2]))
                except ValueError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid OBJ texture coordinates") from exc
                if not all(math.isfinite(item) for item in uv):
                    raise ValueError(f"{path}:{line_number}: OBJ texture coordinates must be finite")
                global_uvs.append(uv)
                continue
            if head in {"o", "g"} and len(parts) >= 2:
                if current.faces:
                    finish_current()
                current.name = " ".join(parts[1:])
                continue
            if head == "usemtl" and len(parts) >= 2:
                material = " ".join(parts[1:])
                seen_materials.add(material)
                continue
            if head == "f" and len(parts) >= 4:
                face_indices: List[int] = []
                face_uvs: List[Optional[Vec2]] = []
                for token in parts[1:]:
                    token_parts = token.split("/")
                    vertex_text = token_parts[0]
                    if not vertex_text:
                        continue
                    try:
                        obj_index = int(vertex_text)
                    except ValueError as exc:
                        raise ValueError(f"{path}:{line_number}: invalid OBJ face vertex {token!r}") from exc
                    global_index = obj_index - 1 if obj_index > 0 else len(global_points) + obj_index
                    if global_index < 0 or global_index >= len(global_points):
                        raise ValueError(
                            f"{path}:{line_number}: OBJ face references missing vertex index {obj_index}"
                        )
                    if global_index not in local_map:
                        local_map[global_index] = len(current.points)
                        current.points.append(global_points[global_index])
                    face_indices.append(local_map[global_index])
                    uv_coord: Optional[Vec2] = None
                    if len(token_parts) >= 2 and token_parts[1]:
                        try:
                            uv_obj_index = int(token_parts[1])
                        except ValueError as exc:
                            raise ValueError(f"{path}:{line_number}: invalid OBJ face texture vertex {token!r}") from exc
                        uv_global_index = uv_obj_index - 1 if uv_obj_index > 0 else len(global_uvs) + uv_obj_index
                        if not (0 <= uv_global_index < len(global_uvs)):
                            raise ValueError(
                                f"{path}:{line_number}: OBJ face references missing texture vertex index {uv_obj_index}"
                            )
                        uv_coord = global_uvs[uv_global_index]
                    face_uvs.append(uv_coord)
                if len(face_indices) >= 3:
                    current.faces.append(geometry_scene.GeometryFace(face_indices, material, face_uvs))
    if current.faces:
        objects.append(current)
    material_to_texture = _material_texture_map_from_meta(meta or {})
    materials = [
        geometry_scene.GeometryMaterial(
            name=name,
            texture_name=material_to_texture.get(name, name or "Default"),
        )
        for name in sorted(seen_materials or {"Default"})
    ]
    return geometry_scene.GeometryScene(
        source_path=os.path.abspath(path),
        models=objects,
        materials=materials,
        metadata=dict(meta or {}),
    )


def load_geometry_scene(path: str, meta_path: Optional[str] = None) -> geometry_scene.GeometryScene:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".obj":
        meta = obj_workflow.load_roundtrip_meta(meta_path or _default_meta_path(path))
        return load_obj_geometry_scene(path, meta)
    if ext == ".gltf":
        return gltf_import.load_gltf_geometry_scene(path, meta_path)
    if ext == ".glb":
        return gltf_import.load_gltf_geometry_scene(path, meta_path)
    raise ValueError(f"unsupported mesh import format {ext!r}; expected .obj or .gltf")


def _parse_obj(path: str) -> List[geometry_scene.GeometryModel]:
    return load_obj_geometry_scene(path).mesh_models()


def _parsed_obj_to_mesh(
    parsed: geometry_scene.GeometryModel,
    model_name: str,
    material_to_texture: Dict[str, str],
    export_to_dat: Sequence[Sequence[float]],
) -> bsp.WorldModelMesh:
    raw_points = [_matrix_point(export_to_dat, point) for point in parsed.points]
    points: List[Vec3] = []
    point_index_by_position: Dict[Tuple[float, float, float], int] = {}
    texture_names: List[str] = []
    texture_index_by_name: Dict[str, int] = {}
    surfaces: List[bsp.Surface] = []
    polygons: List[bsp.Polygon] = []

    for face in parsed.faces:
        remapped_indices: List[int] = []
        for vertex_index in face.vertex_indices:
            if vertex_index < 0 or vertex_index >= len(raw_points):
                continue
            point = raw_points[vertex_index]
            key = _point_dedupe_key(point)
            if key not in point_index_by_position:
                point_index_by_position[key] = len(points)
                points.append(point)
            remapped_indices.append(point_index_by_position[key])
        if len(set(remapped_indices)) < 3:
            continue
        remapped_face = geometry_scene.GeometryFace(
            vertex_indices=remapped_indices,
            material_name=face.material_name,
            uv_coords=list(face.uv_coords or []),
            extras=dict(face.extras or {}),
        )
        texture = material_to_texture.get(face.material_name, face.material_name or "Default")
        if texture not in texture_index_by_name:
            texture_index_by_name[texture] = len(texture_names)
            texture_names.append(texture)
        surface_index = len(surfaces)
        surfaces.append(_surface_from_obj_face(
            points,
            remapped_face,
            texture_index_by_name[texture],
        ))
        # Imported from the default exporter, OBJ faces were reversed for the
        # Blender-space reflection; after applying the inverse reflection, turn
        # them back into DAT winding.
        indices = list(reversed(remapped_indices))
        polygons.append(bsp.Polygon(
            vertex_indices=indices,
            surface_index=surface_index,
            plane_index=0,
        ))
        source_face = _source_face_metadata(parsed, remapped_face)
        if source_face:
            setattr(polygons[-1], "mm9_source_face", source_face)

    min_box, max_box = _bounds(points)
    return bsp.WorldModelMesh(
        name=model_name,
        min_box=min_box,
        max_box=max_box,
        translation=(0.0, 0.0, 0.0),
        points=points,
        polygons=polygons,
        texture_names=texture_names or ["Default"],
        surfaces=surfaces,
    )


def _point_dedupe_key(point: Vec3) -> Tuple[float, float, float]:
    return (round(float(point[0]), 6), round(float(point[1]), 6), round(float(point[2]), 6))


def _surface_from_obj_face(
    points: Sequence[Vec3],
    face: geometry_scene.GeometryFace,
    texture_index: int,
) -> bsp.Surface:
    fallback = bsp.Surface(
        uv_o=(0.0, 0.0, 0.0),
        uv_p=(1.0, 0.0, 0.0),
        uv_q=(0.0, 0.0, 1.0),
        texture_index=texture_index,
        flags=0,
        texture_flags=0,
    )
    source_surface = _surface_from_source_face_extras(face, texture_index)
    if source_surface is not None:
        return source_surface
    if len(face.uv_coords) != len(face.vertex_indices) or any(uv is None for uv in face.uv_coords):
        setattr(fallback, "mm9_uv_method", "default")
        return fallback
    if len(face.vertex_indices) < 3:
        setattr(fallback, "mm9_uv_method", "default")
        return fallback
    dedit_surface = _dedit_surface_from_obj_face(points, face, texture_index)
    if dedit_surface is not None:
        return dedit_surface
    fitted_surface = _least_squares_surface_from_obj_face(points, face, texture_index)
    if fitted_surface is not None:
        return fitted_surface
    setattr(fallback, "mm9_uv_method", "default")
    return fallback


def _surface_from_source_face_extras(
    face: geometry_scene.GeometryFace,
    texture_index: int,
) -> Optional[bsp.Surface]:
    extras = face.extras or {}
    uv_o = _vec3_extra(extras, "uv_o")
    uv_p = _vec3_extra(extras, "uv_p")
    uv_q = _vec3_extra(extras, "uv_q")
    if uv_o is None or uv_p is None or uv_q is None:
        return None
    surface = bsp.Surface(
        uv_o=uv_o,
        uv_p=uv_p,
        uv_q=uv_q,
        texture_index=texture_index,
        flags=0,
        texture_flags=int(extras.get("texture_flags") or 0) & 0xFFFF,
    )
    setattr(surface, "mm9_uv_method", "source_opq")
    return surface


def _source_face_metadata(
    model: geometry_scene.GeometryModel,
    face: geometry_scene.GeometryFace,
) -> Dict[str, object]:
    metadata: Dict[str, object] = {}
    model_extras = model.extras or {}
    face_extras = face.extras or {}
    for key in (
        "source_format",
        "brush_index",
        "polygon_index",
        "record_start",
        "record_end",
        "physics_material",
        "surface_key",
        "surface_flags",
        "texture_flags",
        "normal",
        "dist",
    ):
        if key in face_extras:
            metadata[key] = face_extras[key]
        elif key in model_extras:
            metadata[key] = model_extras[key]
    source_format = metadata.get("source_format")
    if source_format:
        metadata["model_name"] = model.name
        metadata["material_name"] = face.material_name
    return metadata


def _parsed_model_role(model: geometry_scene.GeometryModel) -> str:
    extras = model.extras or {}
    for key in ("role", "MM9_role", "collision_role"):
        value = str(extras.get(key) or "").strip().lower()
        if value:
            if "collision" in value or value in {"invisiblebrush", "physics"}:
                return "collision_only"
            return value
    name = re.sub(r"[^a-z0-9]+", "_", str(model.name or "").lower()).strip("_")
    tokens = {token for token in name.split("_") if token}
    if (
        "collision" in tokens
        or "collider" in tokens
        or "invisiblebrush" in tokens
        or "ucx" in tokens
        or name.startswith("ucx_")
        or name.startswith("collision_")
        or name.startswith("collision")
        or name.endswith("_collision")
        or name.endswith("_collider")
    ):
        return "collision_only"
    return "visible"


def _vec3_extra(extras: Dict[str, object], key: str) -> Optional[Vec3]:
    value = extras.get(key)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        result = (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in result):
        return None
    return result


def _dedit_surface_from_obj_face(
    points: Sequence[Vec3],
    face: geometry_scene.GeometryFace,
    texture_index: int,
) -> Optional[bsp.Surface]:
    first_positions: List[Vec3] = []
    first_uvs: List[Vec2] = []
    for vertex_index, uv in zip(face.vertex_indices, face.uv_coords):
        if uv is None or vertex_index < 0 or vertex_index >= len(points):
            return None
        first_positions.append(points[vertex_index])
        # DEdit's OBJ path flips V inside ConvertUVToOPQ.  Our parser exposes
        # Blender/OBJ UVs directly, so pre-flip here to preserve authored V.
        first_uvs.append((float(uv[0]), -float(uv[1])))
        if len(first_positions) == 3:
            break
    opq = uv_projection.dedit_uv_to_opq(first_positions, first_uvs)
    if opq is None:
        return None
    uv_o, uv_p, uv_q = opq
    surface = bsp.Surface(
        uv_o=uv_o,
        uv_p=uv_p,
        uv_q=uv_q,
        texture_index=texture_index,
        flags=0,
        texture_flags=0,
    )
    setattr(surface, "mm9_uv_method", "dedit_opq")
    return surface


def _least_squares_surface_from_obj_face(
    points: Sequence[Vec3],
    face: geometry_scene.GeometryFace,
    texture_index: int,
) -> Optional[bsp.Surface]:
    origin = points[face.vertex_indices[0]]
    uv0 = face.uv_coords[0]
    if uv0 is None:
        return None
    offsets: List[Vec3] = []
    u_values: List[float] = []
    v_values: List[float] = []
    for vertex_index, uv in zip(face.vertex_indices[1:], face.uv_coords[1:]):
        if uv is None:
            return None
        point = points[vertex_index]
        offsets.append((
            float(point[0]) - float(origin[0]),
            float(point[1]) - float(origin[1]),
            float(point[2]) - float(origin[2]),
        ))
        u_values.append((float(uv[0]) - float(uv0[0])) * 128.0)
        v_values.append((float(uv[1]) - float(uv0[1])) * 128.0)
    uv_p = _least_squares_vector(offsets, u_values)
    uv_q = _least_squares_vector(offsets, v_values)
    if uv_p is None or uv_q is None:
        return None
    uv_o = _surface_origin_for_uv(origin, uv_p, uv_q, uv0)
    if uv_o is None:
        return None
    surface = bsp.Surface(
        uv_o=uv_o,
        uv_p=uv_p,
        uv_q=uv_q,
        texture_index=texture_index,
        flags=0,
        texture_flags=0,
    )
    setattr(surface, "mm9_uv_method", "least_squares")
    return surface


def _surface_origin_for_uv(origin: Vec3, uv_p: Vec3, uv_q: Vec3, uv0: Vec2) -> Optional[Vec3]:
    pp = _dot(uv_p, uv_p)
    pq = _dot(uv_p, uv_q)
    qq = _dot(uv_q, uv_q)
    target_u = float(uv0[0]) * 128.0
    target_v = float(uv0[1]) * 128.0
    det = pp * qq - pq * pq
    if abs(det) <= 1.0e-8:
        return None
    a = (target_u * qq - target_v * pq) / det
    b = (target_v * pp - target_u * pq) / det
    offset = (
        a * float(uv_p[0]) + b * float(uv_q[0]),
        a * float(uv_p[1]) + b * float(uv_q[1]),
        a * float(uv_p[2]) + b * float(uv_q[2]),
    )
    return (
        float(origin[0]) - offset[0],
        float(origin[1]) - offset[1],
        float(origin[2]) - offset[2],
    )


def _least_squares_vector(offsets: Sequence[Vec3], values: Sequence[float]) -> Optional[Vec3]:
    basis = _face_basis(offsets)
    if basis is None:
        return None
    axis_a, axis_b = basis
    ata = [[0.0, 0.0], [0.0, 0.0]]
    atb = [0.0, 0.0]
    for offset, value in zip(offsets, values):
        row = [_dot(offset, axis_a), _dot(offset, axis_b)]
        for i in range(2):
            atb[i] += row[i] * float(value)
            for j in range(2):
                ata[i][j] += row[i] * row[j]
    det = ata[0][0] * ata[1][1] - ata[0][1] * ata[1][0]
    if abs(det) <= 1.0e-8:
        return None
    coeff_a = (atb[0] * ata[1][1] - atb[1] * ata[0][1]) / det
    coeff_b = (ata[0][0] * atb[1] - ata[1][0] * atb[0]) / det
    return (
        coeff_a * axis_a[0] + coeff_b * axis_b[0],
        coeff_a * axis_a[1] + coeff_b * axis_b[1],
        coeff_a * axis_a[2] + coeff_b * axis_b[2],
    )


def _face_basis(offsets: Sequence[Vec3]) -> Optional[Tuple[Vec3, Vec3]]:
    axis_a = next((_unit(offset) for offset in offsets if _length(offset) > 1.0e-6), None)
    if axis_a is None:
        return None
    normal = None
    for offset in offsets:
        candidate = _cross(axis_a, offset)
        if _length(candidate) > 1.0e-6:
            normal = _unit(candidate)
            break
    if normal is None:
        return None
    axis_b = _unit(_cross(normal, axis_a))
    return axis_a, axis_b


def _transform_mesh(
    mesh: bsp.WorldModelMesh,
    source_center: Vec3,
    target_center: Vec3,
    yaw_radians: float,
) -> bsp.WorldModelMesh:
    moved = copy.deepcopy(mesh)
    moved.points = [
        _transform_point(point, source_center, target_center, yaw_radians)
        for point in moved.points
    ]
    moved.min_box, moved.max_box = _bounds(moved.points)
    moved.translation = _transform_point(mesh.translation, source_center, target_center, yaw_radians)
    for surface in moved.surfaces:
        surface.uv_o = _transform_point(surface.uv_o, source_center, target_center, yaw_radians)
        surface.uv_p = _rotate_y(surface.uv_p, yaw_radians)
        surface.uv_q = _rotate_y(surface.uv_q, yaw_radians)
    return moved


def _build_collision_models(
    visible_models: Sequence[ImportedMeshModel],
    *,
    collision_mode: str,
    collision_thickness: float,
    collision_segment_length: float,
) -> List[ImportedMeshModel]:
    if collision_mode == "none":
        return []
    if collision_mode == "invisible_bsp":
        return [
            ImportedMeshModel(
                name=f"{item.name}_Collision",
                mesh=_rename_mesh(item.mesh, f"{item.name}_Collision"),
                source_object_name=item.source_object_name,
                role="collision_helper",
            )
            for item in visible_models
        ]
    if collision_mode == "face_slabs":
        result: List[ImportedMeshModel] = []
        for item in visible_models:
            result.extend(_build_face_slab_collision_models(item, collision_thickness))
        return result
    if collision_mode != "box_approx":
        raise ValueError(f"unsupported mesh collision mode: {collision_mode!r}")

    result = []
    for item in visible_models:
        thin_min, thin_max = _thin_collision_bounds(item.mesh.min_box, item.mesh.max_box, collision_thickness)
        segments = _segment_collision_bounds(thin_min, thin_max, collision_segment_length)
        names = _collision_segment_names(item.name, len(segments))
        for name, (min_box, max_box) in zip(names, segments):
            result.append(ImportedMeshModel(
                name=name,
                mesh=_make_box_mesh(name, min_box, max_box),
                source_object_name=item.source_object_name,
                role="collision_box",
            ))
    return result


def _build_face_slab_collision_models(
    item: ImportedMeshModel,
    collision_thickness: float,
) -> List[ImportedMeshModel]:
    result: List[ImportedMeshModel] = []
    for poly_index, polygon in enumerate(item.mesh.polygons, start=1):
        points = [
            item.mesh.points[index]
            for index in polygon.vertex_indices
            if 0 <= index < len(item.mesh.points)
        ]
        if len(points) < 3:
            continue
        min_box, max_box = _expanded_face_bounds(points, collision_thickness)
        name = f"{item.name}_CollisionFace{poly_index}"
        result.append(ImportedMeshModel(
            name=name,
            mesh=_make_box_mesh(name, min_box, max_box),
            source_object_name=item.source_object_name,
            role="collision_slab",
        ))
    return result


def _expanded_face_bounds(points: Sequence[Vec3], thickness: float) -> Tuple[Vec3, Vec3]:
    min_box, max_box = _bounds(points)
    mins = [float(value) for value in min_box]
    maxs = [float(value) for value in max_box]
    target = max(1.0, float(thickness))
    for axis in range(3):
        size = maxs[axis] - mins[axis]
        if size < target:
            center = (mins[axis] + maxs[axis]) * 0.5
            mins[axis] = center - target * 0.5
            maxs[axis] = center + target * 0.5
    return (tuple(mins), tuple(maxs))  # type: ignore[return-value]


def _collision_helper_mesh(mesh: bsp.WorldModelMesh, name: str) -> bsp.WorldModelMesh:
    helper = _rename_mesh(mesh, name)
    helper.texture_names = [COLLISION_TEXTURE]
    helper.surfaces = [
        bsp.Surface(
            uv_o=surface.uv_o,
            uv_p=surface.uv_p,
            uv_q=surface.uv_q,
            texture_index=0,
            flags=surface.flags,
            texture_flags=surface.texture_flags,
        )
        for surface in helper.surfaces
    ]
    for surface in helper.surfaces:
        setattr(surface, "mm9_uv_method", getattr(surface, "mm9_uv_method", "collision_helper"))
    return helper


def _explicit_collision_name(model_name: str) -> str:
    return model_name if "_collision" in model_name.lower() else f"{model_name}_Collision"


def _rename_mesh(mesh: bsp.WorldModelMesh, name: str) -> bsp.WorldModelMesh:
    renamed = copy.deepcopy(mesh)
    renamed.name = name
    return renamed


def _make_box_mesh(name: str, min_box: Vec3, max_box: Vec3) -> bsp.WorldModelMesh:
    min_box, max_box = _ensure_min_bounds(min_box, max_box)
    x0, y0, z0 = min_box
    x1, y1, z1 = max_box
    points = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    polygons = [
        bsp.Polygon([0, 1, 2, 3], 0, 0),
        bsp.Polygon([5, 4, 7, 6], 0, 0),
        bsp.Polygon([4, 0, 3, 7], 0, 0),
        bsp.Polygon([1, 5, 6, 2], 0, 0),
        bsp.Polygon([3, 2, 6, 7], 0, 0),
        bsp.Polygon([4, 5, 1, 0], 0, 0),
    ]
    mesh = bsp.WorldModelMesh(
        name=name,
        min_box=min_box,
        max_box=max_box,
        translation=(0.0, 0.0, 0.0),
        points=points,
        polygons=polygons,
        texture_names=[COLLISION_TEXTURE],
        surfaces=[
            bsp.Surface(
                uv_o=min_box,
                uv_p=(1.0, 0.0, 0.0),
                uv_q=(0.0, 0.0, 1.0),
                texture_index=0,
                flags=0,
                texture_flags=0,
            )
        ],
    )
    for surface in mesh.surfaces:
        setattr(surface, "mm9_uv_method", "collision_box")
    return mesh


def _collision_segment_names(model_name: str, segment_count: int) -> List[str]:
    if segment_count <= 1:
        return [f"{model_name}_Collision"]
    return [f"{model_name}_Collision{index}" for index in range(1, segment_count + 1)]


def _normalize_collision_mode(value: str) -> str:
    mode = str(value or "none").lower()
    if mode in {"none", "off", "false", "0"}:
        return "none"
    if mode in {"invisible_bsp", "collision_helper"}:
        return "invisible_bsp"
    if mode in {"box", "box_approx"}:
        return "box_approx"
    if mode in {"face_slabs", "slabs", "per_face", "per_face_slabs"}:
        return "face_slabs"
    raise ValueError(f"unsupported mesh collision mode: {value!r}")


def _thin_collision_bounds(min_box: Vec3, max_box: Vec3, thickness: float = 8.0) -> Tuple[Vec3, Vec3]:
    mins = [float(v) for v in min_box]
    maxs = [float(v) for v in max_box]
    x_size = maxs[0] - mins[0]
    z_size = maxs[2] - mins[2]
    target_thickness = max(1.0, float(thickness))
    if x_size <= 0.0 or z_size <= 0.0:
        return _ensure_min_bounds(tuple(mins), tuple(maxs), target_thickness)
    thin_axis = 0 if x_size <= z_size else 2
    thickness = min(maxs[thin_axis] - mins[thin_axis], target_thickness)
    center = (mins[thin_axis] + maxs[thin_axis]) * 0.5
    mins[thin_axis] = center - thickness * 0.5
    maxs[thin_axis] = center + thickness * 0.5
    return _ensure_min_bounds(tuple(mins), tuple(maxs), target_thickness)


def _segment_collision_bounds(
    min_box: Vec3,
    max_box: Vec3,
    segment_length: float = 512.0,
) -> List[Tuple[Vec3, Vec3]]:
    min_box, max_box = _ensure_min_bounds(min_box, max_box)
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


def _ensure_min_bounds(
    min_box: Vec3,
    max_box: Vec3,
    minimum_size: float = 1.0,
) -> Tuple[Vec3, Vec3]:
    mins = [float(value) for value in min_box]
    maxs = [float(value) for value in max_box]
    target = max(1.0, float(minimum_size))
    for axis in range(3):
        size = maxs[axis] - mins[axis]
        if size < target:
            center = (mins[axis] + maxs[axis]) * 0.5
            mins[axis] = center - target * 0.5
            maxs[axis] = center + target * 0.5
    return (tuple(mins), tuple(maxs))  # type: ignore[return-value]


def _transform_point(point: Vec3, source_center: Vec3, target_center: Vec3, yaw_radians: float) -> Vec3:
    dx = float(point[0]) - float(source_center[0])
    dy = float(point[1]) - float(source_center[1])
    dz = float(point[2]) - float(source_center[2])
    c = math.cos(yaw_radians)
    s = math.sin(yaw_radians)
    return (
        float(target_center[0]) + dx * c + dz * s,
        float(target_center[1]) + dy,
        float(target_center[2]) - dx * s + dz * c,
    )


def _rotate_y(vector: Vec3, yaw_radians: float) -> Vec3:
    x, y, z = vector
    c = math.cos(yaw_radians)
    s = math.sin(yaw_radians)
    return (x * c + z * s, y, -x * s + z * c)


def _dot(a: Vec3, b: Vec3) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        float(a[1]) * float(b[2]) - float(a[2]) * float(b[1]),
        float(a[2]) * float(b[0]) - float(a[0]) * float(b[2]),
        float(a[0]) * float(b[1]) - float(a[1]) * float(b[0]),
    )


def _length(value: Vec3) -> float:
    return math.sqrt(_dot(value, value))


def _unit(value: Vec3) -> Vec3:
    length = _length(value)
    if length <= 1.0e-6:
        return (0.0, 1.0, 0.0)
    return (
        float(value[0]) / length,
        float(value[1]) / length,
        float(value[2]) / length,
    )


def _matrix_point(matrix: Sequence[Sequence[float]], point: Vec3) -> Vec3:
    x, y, z = point
    return (
        float(matrix[0][0]) * x + float(matrix[0][1]) * y + float(matrix[0][2]) * z + float(matrix[0][3]),
        float(matrix[1][0]) * x + float(matrix[1][1]) * y + float(matrix[1][2]) * z + float(matrix[1][3]),
        float(matrix[2][0]) * x + float(matrix[2][1]) * y + float(matrix[2][2]) * z + float(matrix[2][3]),
    )


def _bounds(points: Sequence[Vec3]) -> Tuple[Vec3, Vec3]:
    if not points:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    return (
        (min(p[0] for p in points), min(p[1] for p in points), min(p[2] for p in points)),
        (max(p[0] for p in points), max(p[1] for p in points), max(p[2] for p in points)),
    )


def _combined_center(meshes: Sequence[bsp.WorldModelMesh]) -> Vec3:
    mins, maxs = _bounds([
        point
        for mesh in meshes
        for point in (mesh.min_box, mesh.max_box)
    ])
    return _bounds_center(mins, maxs)


def _bounds_center(min_box: Vec3, max_box: Vec3) -> Vec3:
    return (
        (float(min_box[0]) + float(max_box[0])) * 0.5,
        (float(min_box[1]) + float(max_box[1])) * 0.5,
        (float(min_box[2]) + float(max_box[2])) * 0.5,
    )


def _new_model_names(prefix: str, source_names: Sequence[str]) -> List[str]:
    if len(source_names) == 1:
        return [prefix]
    names: List[str] = []
    seen: set[str] = set()
    for index, source_name in enumerate(source_names, start=1):
        suffix = _sanitize_model_name(source_name or f"Mesh{index}")
        name = f"{prefix}_{suffix}"
        if name.lower() in seen:
            name = f"{name}{index}"
        seen.add(name.lower())
        names.append(name)
    return names


def _validate_model_names(target_bsp: bsp.BspWorld, names: Sequence[str]) -> None:
    existing = {
        str(getattr(model, "name", "") or "").lower()
        for model in getattr(target_bsp, "world_models", []) or []
    }
    lowered = [name.lower() for name in names]
    if len(set(lowered)) != len(lowered):
        raise ValueError(f"imported mesh model names must be unique: {', '.join(names)}")
    for name in names:
        if not name.strip():
            raise ValueError("imported mesh model names must be non-empty")
        if name.lower() in existing:
            raise ValueError(f"BSP model named {name!r} already exists in the target level")


def _sanitize_model_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "", str(value or ""))
    return cleaned or "MeshImport"


def _default_meta_path(obj_path: str) -> str:
    base, ext = os.path.splitext(obj_path)
    if ext.lower() in {".gltf", ".glb"}:
        return f"{obj_path}.datmeta.json"
    if base.lower().endswith("_geometry"):
        return f"{base}.datmeta.json"
    return f"{base}.datmeta.json" if ext else f"{obj_path}.datmeta.json"


def _effective_meta_path(obj_path: str, meta_path: Optional[str]) -> str:
    if meta_path:
        return meta_path
    default = _default_meta_path(obj_path)
    ext = os.path.splitext(obj_path)[1].lower()
    if ext in {".gltf", ".glb"} and not os.path.exists(default):
        return ""
    return default


def _identity_matrix() -> List[List[float]]:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _as_vec3(value: object, prop_name: str) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{prop_name} must be a 3-vector, got {value!r}")
    return (float(value[0]), float(value[1]), float(value[2]))
