"""Static LOD0 glTF/GLB export for MM9 LithTech ABC model files."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from features.model_conversion import dtx_png_export, skin_resolver
from view3d import dtx
from view3d.abc_loader import AbcModel, AbcPiece, load_abc


Vec2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class AbcGltfExportResult:
    abc_path: str
    gltf_path: str
    bin_path: str
    glb_path: str
    model_name: str
    piece_count: int
    vertex_count: int
    triangle_count: int
    uv_count: int
    baked_static_pose: bool = False
    texture_count: int = 0
    skin_warnings: Tuple[str, ...] = ()
    variant_names: Tuple[str, ...] = ()


@dataclass(frozen=True)
class _TextureAsset:
    dtx_path: str
    png_name: str
    png_data: bytes
    width: int
    height: int
    pixel_format: int
    has_useful_alpha: bool
    alpha_mode: str


@dataclass(frozen=True)
class _ResolvedVariant:
    name: str
    resolution: skin_resolver.SkinResolutionResult
    source_keys: Tuple[str, ...] = ()


def export_abc_to_gltf(
    abc_path: str,
    output_dir: str,
    *,
    base_name: str = "",
    bake_static_pose: bool = True,
    write_glb: bool = False,
    skin_paths: Optional[Sequence[str]] = None,
    skins_root: str = "",
    object_type: str = "",
    appearance_key: str = "",
    catalog_path: str = "",
    all_variants: bool = False,
    broadcast_skin: bool = False,
    unit_scale: float = 1.0,
) -> AbcGltfExportResult:
    """Export one LithTech ``.ABC`` model as static LOD0 glTF or GLB."""
    if not os.path.isfile(abc_path):
        raise FileNotFoundError(f"ABC file was not found: {abc_path}")
    if not math.isfinite(unit_scale) or unit_scale <= 0.0:
        raise ValueError("unit_scale must be a finite positive number")
    model = load_abc(abc_path, bake_static_bind_pose=bake_static_pose)
    if model is None or model.is_empty():
        raise ValueError(f"ABC file could not be parsed as exportable geometry: {abc_path}")
    os.makedirs(output_dir, exist_ok=True)
    label = _safe_label(base_name or model.name)

    variants, warnings = _resolve_variants(
        model,
        abc_path,
        skin_paths=skin_paths,
        skins_root=skins_root,
        object_type=object_type,
        appearance_key=appearance_key,
        catalog_path=catalog_path,
        all_variants=all_variants,
        broadcast_skin=broadcast_skin,
    )
    gltf, binary, counts, texture_files = _build_gltf(
        model,
        os.path.abspath(abc_path),
        label,
        variants=variants,
        embed_binary=write_glb,
        unit_scale=unit_scale,
        warnings=warnings,
    )

    gltf_path = ""
    bin_path = ""
    glb_path = ""
    if write_glb:
        glb_path = os.path.abspath(os.path.join(output_dir, f"{label}.glb"))
        _write_glb(glb_path, gltf, binary)
    else:
        gltf_path = os.path.abspath(os.path.join(output_dir, f"{label}.gltf"))
        bin_path = os.path.abspath(os.path.join(output_dir, f"{label}.bin"))
        for png_name, png_data in texture_files.items():
            with open(os.path.join(output_dir, png_name), "wb") as stream:
                stream.write(png_data)
        with open(bin_path, "wb") as stream:
            stream.write(binary)
        _write_text(gltf_path, json.dumps(gltf, indent=2, ensure_ascii=False) + "\n")

    return AbcGltfExportResult(
        abc_path=os.path.abspath(abc_path),
        gltf_path=gltf_path,
        bin_path=bin_path,
        glb_path=glb_path,
        model_name=model.name,
        piece_count=len(model.pieces),
        vertex_count=counts["vertices"],
        triangle_count=counts["triangles"],
        uv_count=counts["uvs"],
        baked_static_pose=model.baked_bind_pose,
        texture_count=counts["textures"],
        skin_warnings=tuple(warnings),
        variant_names=tuple(variant.name for variant in variants),
    )


def _resolve_variants(
    model: AbcModel,
    abc_path: str,
    *,
    skin_paths: Optional[Sequence[str]],
    skins_root: str,
    object_type: str,
    appearance_key: str,
    catalog_path: str,
    all_variants: bool,
    broadcast_skin: bool,
) -> Tuple[List[_ResolvedVariant], List[str]]:
    warnings: List[str] = []
    if all_variants:
        if skin_paths:
            raise ValueError("--skin cannot be combined with --all-variants")
        if not catalog_path:
            catalog_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "catalog", "data", "catalog.json"))
        catalog_variants, catalog_warnings = skin_resolver.catalog_variants_for_model(
            model,
            abc_path,
            catalog_path,
            skins_root,
        )
        warnings.extend(catalog_warnings)
        if not catalog_variants:
            raise ValueError(f"catalog contains no DTX variants for model {model.name!r}")
        variants: List[_ResolvedVariant] = []
        for catalog_variant in catalog_variants:
            resolution = skin_resolver.resolve_model_skins(
                model,
                skins_root,
                explicit_skins=catalog_variant.skin_paths,
                broadcast_skin=True,
            )
            warnings.extend(resolution.warnings)
            variants.append(_ResolvedVariant(
                name=catalog_variant.name,
                resolution=resolution,
                source_keys=catalog_variant.source_keys,
            ))
        return variants, list(dict.fromkeys(warnings))

    resolution = skin_resolver.resolve_model_skins(
        model,
        skins_root,
        explicit_skins=skin_paths,
        object_type=object_type,
        appearance_key=appearance_key,
        broadcast_skin=broadcast_skin,
    )
    warnings.extend(resolution.warnings)
    return [_ResolvedVariant(name=model.name, resolution=resolution)], list(dict.fromkeys(warnings))


def _build_gltf(
    model: AbcModel,
    abc_path: str,
    base_name: str,
    *,
    variants: Sequence[_ResolvedVariant],
    embed_binary: bool,
    unit_scale: float,
    warnings: List[str],
) -> Tuple[Dict[str, Any], bytes, Dict[str, int], Dict[str, bytes]]:
    binary = bytearray()
    buffer_views: List[Dict[str, Any]] = []
    accessors: List[Dict[str, Any]] = []
    meshes: List[Dict[str, Any]] = []
    nodes: List[Dict[str, Any]] = []
    primitives: List[Dict[str, Any]] = []
    vertex_count = 0
    triangle_count = 0

    for piece_index, piece in enumerate(model.pieces):
        positions, normals, texcoords, indices = _flatten_piece(piece, unit_scale=unit_scale)
        if not indices:
            warnings.append(f"piece {piece.name!r} has no non-degenerate export triangles")
            continue
        position_accessor = _append_vec3_accessor(binary, buffer_views, accessors, positions, target=34962, include_bounds=True)
        normal_accessor = _append_vec3_accessor(binary, buffer_views, accessors, normals, target=34962)
        texcoord_accessor = _append_vec2_accessor(binary, buffer_views, accessors, texcoords, target=34962)
        index_accessor = _append_u32_accessor(binary, buffer_views, accessors, indices, target=34963)
        primitive: Dict[str, Any] = {
            "attributes": {
                "POSITION": position_accessor,
                "NORMAL": normal_accessor,
                "TEXCOORD_0": texcoord_accessor,
            },
            "indices": index_accessor,
            "mode": 4,
            "extras": {
                "MM9_abc_piece_index": piece_index,
                "MM9_abc_piece_name": piece.name,
            },
        }
        mesh_index = len(meshes)
        meshes.append({
            "name": piece.name or f"piece_{piece_index}",
            "primitives": [primitive],
            "extras": {
                "MM9_abc_piece_index": piece_index,
                "MM9_abc_piece_name": piece.name,
                "MM9_abc_source_vertex_count": len(piece.vertices),
                "MM9_abc_source_triangle_count": len(piece.triangles),
            },
        })
        nodes.append({
            "name": piece.name or f"piece_{piece_index}",
            "mesh": mesh_index,
            "extras": {
                "MM9_abc_piece_index": piece_index,
                "MM9_abc_piece_name": piece.name,
            },
        })
        primitives.append(primitive)
        vertex_count += len(positions)
        triangle_count += len(indices) // 3

    texture_assets = _collect_textures(variants, base_name, warnings)
    images: List[Dict[str, Any]] = []
    textures: List[Dict[str, Any]] = []
    texture_index_by_path: Dict[str, int] = {}
    texture_files: Dict[str, bytes] = {}
    for asset in texture_assets:
        image: Dict[str, Any] = {
            "name": os.path.splitext(asset.png_name)[0],
            "extras": {
                "MM9_dtx_source": asset.dtx_path,
                "MM9_dtx_pixel_format": asset.pixel_format,
                "MM9_dtx_has_useful_alpha": asset.has_useful_alpha,
            },
        }
        if embed_binary:
            image["bufferView"] = _append_buffer_view(binary, buffer_views, asset.png_data)
            image["mimeType"] = "image/png"
        else:
            image["uri"] = asset.png_name
            texture_files[asset.png_name] = asset.png_data
        image_index = len(images)
        images.append(image)
        textures.append({"source": image_index})
        texture_index_by_path[os.path.normcase(asset.dtx_path)] = len(textures) - 1

    asset_by_path = {os.path.normcase(asset.dtx_path): asset for asset in texture_assets}
    materials: List[Dict[str, Any]] = []
    material_indices: Dict[Tuple[int, str], int] = {}
    for primitive in primitives:
        piece_index = int(primitive["extras"]["MM9_abc_piece_index"])
        variant_mappings: List[Dict[str, Any]] = []
        for variant_index, variant in enumerate(variants):
            resolved = next((item for item in variant.resolution.pieces if item.piece_index == piece_index), None)
            skin_path = os.path.abspath(resolved.skin_path) if resolved and resolved.skin_path else ""
            material_key = (piece_index, os.path.normcase(skin_path))
            material_index = material_indices.get(material_key)
            if material_index is None:
                asset = asset_by_path.get(os.path.normcase(skin_path)) if skin_path else None
                texture_index = texture_index_by_path.get(os.path.normcase(skin_path)) if skin_path else None
                material_index = len(materials)
                materials.append(_material(
                    model.pieces[piece_index],
                    piece_index,
                    texture_index=texture_index,
                    texture_asset=asset,
                    variant_name=variant.name,
                ))
                material_indices[material_key] = material_index
            else:
                material_variants = materials[material_index]["extras"].setdefault("MM9_variants", [])
                if variant.name not in material_variants:
                    material_variants.append(variant.name)
            if variant_index == 0:
                primitive["material"] = material_index
            if len(variants) > 1:
                variant_mappings.append({"material": material_index, "variants": [variant_index]})
        if variant_mappings:
            primitive.setdefault("extensions", {})["KHR_materials_variants"] = {"mappings": variant_mappings}

    gltf: Dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "mm9_editor static ABC glTF exporter"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "extras": {
            "MM9_abc_conversion": {
                "kind": "mm9_abc_model_conversion",
                "format": "glb" if embed_binary else "gltf",
                "source": {
                    "path": abc_path,
                    "model_name": model.name,
                    "abc_version": model.version,
                    "command_string": model.command_string,
                },
                "conversion": {
                    "static_lod0_only": True,
                    "baked_static_pose": model.baked_bind_pose,
                    "unit_scale": unit_scale,
                    "omitted": ["armature", "animations", "sockets", "child_models", "additional_lods"],
                },
                "variants": [
                    {"name": variant.name, "source_keys": list(variant.source_keys)}
                    for variant in variants
                ],
                "counts": {
                    "pieces": len(meshes),
                    "vertices": vertex_count,
                    "triangles": triangle_count,
                    "uvs": vertex_count,
                    "textures": len(textures),
                },
                "warnings": list(dict.fromkeys(warnings)),
            },
        },
    }
    if not embed_binary:
        gltf["buffers"][0]["uri"] = f"{base_name}.bin"
    if images:
        gltf["images"] = images
        gltf["textures"] = textures
    if len(variants) > 1:
        gltf["extensionsUsed"] = ["KHR_materials_variants"]
        gltf["extensions"] = {
            "KHR_materials_variants": {
                "variants": [{"name": variant.name} for variant in variants],
            },
        }
    counts = {
        "vertices": vertex_count,
        "triangles": triangle_count,
        "uvs": vertex_count,
        "textures": len(textures),
    }
    return gltf, bytes(binary), counts, texture_files


def _flatten_piece(piece: AbcPiece, *, unit_scale: float) -> Tuple[List[Vec3], List[Vec3], List[Vec2], List[int]]:
    positions: List[Vec3] = []
    normals: List[Vec3] = []
    texcoords: List[Vec2] = []
    indices: List[int] = []
    for triangle in piece.triangles:
        if len(triangle.refs) != 3:
            continue
        try:
            raw = [piece.vertices[ref.vertex_index].pos for ref in triangle.refs]
        except (IndexError, TypeError):
            continue
        if not all(math.isfinite(value) for point in raw for value in point):
            continue
        edge1 = tuple(raw[1][axis] - raw[0][axis] for axis in range(3))
        edge2 = tuple(raw[2][axis] - raw[0][axis] for axis in range(3))
        cross = (
            edge1[1] * edge2[2] - edge1[2] * edge2[1],
            edge1[2] * edge2[0] - edge1[0] * edge2[2],
            edge1[0] * edge2[1] - edge1[1] * edge2[0],
        )
        length = math.sqrt(sum(value * value for value in cross))
        if length <= 1.0e-12:
            continue
        normal = tuple(value / length for value in cross)
        first = len(positions)
        for point, ref in zip(raw, triangle.refs):
            positions.append(tuple(float(value) * unit_scale for value in point))
            normals.append(normal)
            texcoords.append((float(ref.u), float(ref.v)))
        indices.extend((first, first + 1, first + 2))
    return positions, normals, texcoords, indices


def _collect_textures(
    variants: Sequence[_ResolvedVariant],
    base_name: str,
    warnings: List[str],
) -> List[_TextureAsset]:
    assets: List[_TextureAsset] = []
    seen = set()
    for variant in variants:
        for piece in variant.resolution.pieces:
            if not piece.skin_path:
                continue
            path = os.path.abspath(piece.skin_path)
            key = os.path.normcase(path)
            if key in seen:
                continue
            seen.add(key)
            try:
                with open(path, "rb") as stream:
                    data = stream.read()
                converted = dtx_png_export.dtx_to_png_bytes(data, force_opaque_unused_alpha=True)
            except OSError as error:
                warnings.append(f"skin could not be read: {path}: {error}")
                continue
            if converted is None:
                warnings.append(f"skin could not be decoded: {path}")
                continue
            pixel_format, width, height, useful_alpha, png_data = converted
            info = dtx.inspect_dtx_alpha_bytes(data)
            alpha_mode = _alpha_mode(info)
            png_name = f"{base_name}_skin_{len(assets) + 1}_{_safe_label(os.path.splitext(os.path.basename(path))[0])}.png"
            assets.append(_TextureAsset(
                dtx_path=path,
                png_name=png_name,
                png_data=png_data,
                width=width,
                height=height,
                pixel_format=pixel_format,
                has_useful_alpha=useful_alpha,
                alpha_mode=alpha_mode,
            ))
    return assets


def _alpha_mode(info) -> str:
    if info is None or not info.has_useful_alpha:
        return "OPAQUE"
    mid = float(getattr(info, "mid_fraction", 0.0))
    transparent = float(getattr(info, "transparent_fraction", 0.0))
    return "BLEND" if mid > 0.05 and mid >= transparent else "MASK"


def _material(
    piece: AbcPiece,
    piece_index: int,
    *,
    texture_index: Optional[int],
    texture_asset: Optional[_TextureAsset],
    variant_name: str,
) -> Dict[str, Any]:
    pbr: Dict[str, Any] = {
        "baseColorFactor": [1.0, 1.0, 1.0, 1.0] if texture_index is not None else [*_fallback_color(piece_index), 1.0],
        "metallicFactor": 0.0,
        "roughnessFactor": 1.0,
    }
    if texture_index is not None:
        pbr["baseColorTexture"] = {"index": texture_index}
    material: Dict[str, Any] = {
        "name": f"mat_{_safe_label(piece.name or f'piece_{piece_index}')}_{_safe_label(variant_name)}",
        "pbrMetallicRoughness": pbr,
        "extras": {
            "MM9_abc_piece_name": piece.name,
            "MM9_texture_status": "dtx_png" if texture_asset else "unresolved_placeholder",
            "MM9_dtx_source": texture_asset.dtx_path if texture_asset else "",
            "MM9_variants": [variant_name],
        },
    }
    if texture_asset and texture_asset.alpha_mode != "OPAQUE":
        material["alphaMode"] = texture_asset.alpha_mode
        if texture_asset.alpha_mode == "MASK":
            material["alphaCutoff"] = 0.5
    return material


def _append_vec3_accessor(
    binary: bytearray,
    buffer_views: List[Dict[str, Any]],
    accessors: List[Dict[str, Any]],
    values: Sequence[Vec3],
    *,
    target: int,
    include_bounds: bool = False,
) -> int:
    payload = b"".join(struct.pack("<fff", *value) for value in values)
    minimum = [min(value[axis] for value in values) for axis in range(3)] if include_bounds else None
    maximum = [max(value[axis] for value in values) for axis in range(3)] if include_bounds else None
    return _append_accessor(binary, buffer_views, accessors, payload, 5126, len(values), "VEC3", target, minimum, maximum)


def _append_vec2_accessor(
    binary: bytearray,
    buffer_views: List[Dict[str, Any]],
    accessors: List[Dict[str, Any]],
    values: Sequence[Vec2],
    *,
    target: int,
) -> int:
    payload = b"".join(struct.pack("<ff", *value) for value in values)
    return _append_accessor(binary, buffer_views, accessors, payload, 5126, len(values), "VEC2", target)


def _append_u32_accessor(
    binary: bytearray,
    buffer_views: List[Dict[str, Any]],
    accessors: List[Dict[str, Any]],
    values: Sequence[int],
    *,
    target: int,
) -> int:
    payload = b"".join(struct.pack("<I", value) for value in values)
    return _append_accessor(binary, buffer_views, accessors, payload, 5125, len(values), "SCALAR", target)


def _append_accessor(
    binary: bytearray,
    buffer_views: List[Dict[str, Any]],
    accessors: List[Dict[str, Any]],
    payload: bytes,
    component_type: int,
    count: int,
    accessor_type: str,
    target: int,
    minimum: Optional[Sequence[float]] = None,
    maximum: Optional[Sequence[float]] = None,
) -> int:
    view_index = _append_buffer_view(binary, buffer_views, payload, target=target)
    accessor: Dict[str, Any] = {
        "bufferView": view_index,
        "componentType": component_type,
        "count": count,
        "type": accessor_type,
    }
    if minimum is not None:
        accessor["min"] = list(minimum)
    if maximum is not None:
        accessor["max"] = list(maximum)
    accessors.append(accessor)
    return len(accessors) - 1


def _append_buffer_view(
    binary: bytearray,
    buffer_views: List[Dict[str, Any]],
    payload: bytes,
    *,
    target: Optional[int] = None,
) -> int:
    _align(binary, 4)
    offset = len(binary)
    binary.extend(payload)
    view: Dict[str, Any] = {"buffer": 0, "byteOffset": offset, "byteLength": len(payload)}
    if target is not None:
        view["target"] = target
    buffer_views.append(view)
    return len(buffer_views) - 1


def _write_glb(path: str, gltf: Dict[str, Any], binary: bytes) -> None:
    json_data = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_data += b" " * ((-len(json_data)) % 4)
    bin_data = binary + b"\x00" * ((-len(binary)) % 4)
    total_length = 12 + 8 + len(json_data) + 8 + len(bin_data)
    with open(path, "wb") as stream:
        stream.write(struct.pack("<III", 0x46546C67, 2, total_length))
        stream.write(struct.pack("<II", len(json_data), 0x4E4F534A))
        stream.write(json_data)
        stream.write(struct.pack("<II", len(bin_data), 0x004E4942))
        stream.write(bin_data)


def _align(binary: bytearray, alignment: int) -> None:
    binary.extend(b"\x00" * ((-len(binary)) % alignment))


def _safe_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._") or "abc_model"


def _fallback_color(index: int) -> Tuple[float, float, float]:
    palette = ((0.72, 0.58, 0.42), (0.42, 0.62, 0.76), (0.60, 0.72, 0.46), (0.72, 0.46, 0.62))
    return palette[index % len(palette)]


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Export an MM9 LithTech ABC model to static LOD0 glTF or GLB.")
    parser.add_argument("abc_path", help="Path to the .ABC model file")
    parser.add_argument("output_dir", help="Directory for the exported glTF/GLB")
    parser.add_argument("--base-name", default="", help="Optional output filename stem")
    parser.add_argument("--glb", action="store_true", help="Write a self-contained binary .glb")
    parser.add_argument("--skin", action="append", default=[], help="DTX path; repeat by piece order, or use PIECE=PATH")
    parser.add_argument("--skins-root", default="", help="Extracted SKINS directory for inference or variants")
    parser.add_argument("--object-type", default="", help="Optional object/class type hint for skin inference")
    parser.add_argument("--appearance-key", default="", help="Optional actor/civilian appearance hint")
    parser.add_argument("--catalog", default="", help="catalog.json used by --all-variants")
    parser.add_argument("--all-variants", action="store_true", help="Export every deduplicated catalog material variant")
    parser.add_argument("--broadcast-skin", action="store_true", help="Intentionally apply one --skin to every model piece")
    parser.add_argument("--unit-scale", type=float, default=1.0, help="Positive multiplier applied to exported positions")
    parser.add_argument("--no-bake-static-pose", action="store_true", help="Disable supported static-pose baking")
    args = parser.parse_args(argv)
    result = export_abc_to_gltf(
        args.abc_path,
        args.output_dir,
        base_name=args.base_name,
        bake_static_pose=not args.no_bake_static_pose,
        write_glb=args.glb,
        skin_paths=args.skin,
        skins_root=args.skins_root,
        object_type=args.object_type,
        appearance_key=args.appearance_key,
        catalog_path=args.catalog,
        all_variants=args.all_variants,
        broadcast_skin=args.broadcast_skin,
        unit_scale=args.unit_scale,
    )
    output_path = result.glb_path or result.gltf_path
    print(
        f"Exported {result.model_name}: {result.piece_count} piece(s), "
        f"{result.vertex_count} vertices, {result.triangle_count} triangles -> {output_path}"
    )
    if result.variant_names:
        print("Variants: " + ", ".join(result.variant_names))
    for warning in result.skin_warnings:
        print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
