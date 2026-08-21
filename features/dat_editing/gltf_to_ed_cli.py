"""Command-line entry point for the Phase 7 glTF/GLB -> ED service."""

from __future__ import annotations

import argparse
from typing import Optional, Sequence

from features.dat_editing import gltf_brushes
from features.dat_editing import gltf_ed_assembly
from features.dat_editing import gltf_materials
from features.dat_editing import gltf_to_ed_service


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a static glTF 2.0/GLB mesh into a DEDit ED v1249 document."
    )
    parser.add_argument("source", help="Input .gltf or .glb file")
    parser.add_argument("output", help="Requested output .ed file")
    parser.add_argument(
        "--output-mode",
        choices=gltf_ed_assembly.OUTPUT_MODES,
        default=gltf_ed_assembly.PREFAB,
        help="Named-group prefab or minimal full-world ED",
    )
    parser.add_argument(
        "--geometry-policy",
        choices=(gltf_brushes.STRICT_CONVEX, gltf_brushes.TRIANGLE_SLAB),
        default=gltf_brushes.STRICT_CONVEX,
        help="Exact convex Brushes or explicitly approximated triangle slabs",
    )
    coordinates = parser.add_mutually_exclusive_group()
    coordinates.add_argument(
        "--coordinate-preset",
        choices=(gltf_to_ed_service.EDITOR_DISPLAY, gltf_to_ed_service.RAW_DEDIT),
        default=gltf_to_ed_service.EDITOR_DISPLAY,
        help="glTF-world to DEDit coordinate conversion",
    )
    coordinates.add_argument(
        "--coordinate-matrix",
        nargs=16,
        type=float,
        metavar="N",
        help="Explicit affine 4x4 row-major coordinate matrix",
    )
    parser.add_argument(
        "--unit-scale",
        type=float,
        default=1.0,
        help="Positive scale applied after the coordinate matrix",
    )
    parser.add_argument(
        "--weld-tolerance",
        type=float,
        default=0.01,
        help="Non-negative DEDit-space point weld tolerance",
    )
    parser.add_argument(
        "--material-map",
        default="",
        metavar="JSON",
        help="JSON object mapping glTF material names to DTX paths",
    )
    parser.add_argument(
        "--texture-dimensions",
        default="",
        metavar="JSON",
        help="JSON object mapping DTX paths to [width, height]",
    )
    parser.add_argument(
        "--fallback-texture",
        default=None,
        help="Explicit fallback DTX path for unresolved materials",
    )
    parser.add_argument(
        "--fallback-texture-size",
        nargs=2,
        type=float,
        metavar=("WIDTH", "HEIGHT"),
        help="Explicit dimensions used when no authoritative DTX size is available",
    )
    parser.add_argument(
        "--default-uv-projection",
        choices=(gltf_materials.WORLD_ALIGNED_PROJECTION,),
        default=None,
        help="Explicit projection for missing or degenerate source UVs",
    )
    parser.add_argument("--slab-thickness", type=float, default=None)
    parser.add_argument("--slab-back-texture", default=None)
    parser.add_argument("--slab-side-texture", default=None)
    parser.add_argument(
        "--max-brushes",
        type=int,
        default=gltf_brushes.DEFAULT_MAX_BRUSHES,
    )
    parser.add_argument(
        "--max-surfaces",
        type=int,
        default=gltf_brushes.DEFAULT_MAX_SURFACES,
    )
    parser.add_argument("--group-name", default="ImportedGLTF")
    parser.add_argument(
        "--infostring",
        default=None,
        help="Optional full-world compiler infostring override",
    )
    parser.add_argument(
        "--block-size",
        type=int,
        default=50000,
        help="Positive full-world zlib block size",
    )
    parser.add_argument(
        "--world-properties-position",
        nargs=3,
        type=float,
        metavar=("X", "Y", "Z"),
    )
    parser.add_argument(
        "--start-point-position",
        nargs=3,
        type=float,
        metavar=("X", "Y", "Z"),
    )
    parser.add_argument(
        "--light-position",
        nargs=3,
        type=float,
        metavar=("X", "Y", "Z"),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing ED/report artifact transactionally",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_argument_parser().parse_args(argv)
    matrix = None
    if args.coordinate_matrix is not None:
        values = tuple(args.coordinate_matrix)
        matrix = tuple(
            tuple(values[row * 4:(row + 1) * 4])
            for row in range(4)
        )
    options = gltf_to_ed_service.GltfToEdConversionOptions(
        output_mode=args.output_mode,
        geometry_policy=args.geometry_policy,
        coordinate_preset=args.coordinate_preset,
        coordinate_matrix=matrix,
        unit_scale=args.unit_scale,
        weld_tolerance=args.weld_tolerance,
        material_map_path=args.material_map,
        fallback_texture=args.fallback_texture,
        texture_dimensions_path=args.texture_dimensions,
        fallback_texture_size=args.fallback_texture_size,
        default_uv_projection=args.default_uv_projection,
        slab_thickness=args.slab_thickness,
        slab_back_texture=args.slab_back_texture,
        slab_side_texture=args.slab_side_texture,
        max_brushes=args.max_brushes,
        max_surfaces=args.max_surfaces,
        overwrite=args.overwrite,
        group_name=args.group_name,
        infostring=args.infostring,
        block_size=args.block_size,
        world_properties_position=args.world_properties_position,
        start_point_position=args.start_point_position,
        light_position=args.light_position,
    )
    report = gltf_to_ed_service.convert_gltf_to_ed(
        args.source,
        args.output,
        options=options,
    )
    print(gltf_to_ed_service.format_gltf_to_ed_conversion_report(report), end="")
    return 0 if report.status in {"ready_prefab", "ready_full_world"} else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_argument_parser", "main"]
