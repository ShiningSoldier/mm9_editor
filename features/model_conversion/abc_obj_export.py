"""Geometry-only OBJ export for MM9 LithTech ABC model files."""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from view3d.abc_loader import AbcModel, AbcPiece, load_abc


@dataclass(frozen=True)
class AbcObjExportResult:
    abc_path: str
    obj_path: str
    mtl_path: str
    model_name: str
    piece_count: int
    vertex_count: int
    triangle_count: int
    uv_count: int
    baked_static_pose: bool = False


def export_abc_to_obj(
    abc_path: str,
    output_dir: str,
    *,
    base_name: str = "",
    bake_static_pose: bool = True,
) -> AbcObjExportResult:
    """Export one ABC model as static LOD0 OBJ with placeholder materials."""
    if not os.path.isfile(abc_path):
        raise FileNotFoundError(f"ABC file was not found: {abc_path}")
    model = load_abc(abc_path, bake_static_bind_pose=bake_static_pose)
    if model is None or model.is_empty():
        raise ValueError(f"ABC file could not be parsed as exportable geometry: {abc_path}")
    os.makedirs(output_dir, exist_ok=True)
    label = _safe_label(base_name or model.name)
    obj_path = os.path.abspath(os.path.join(output_dir, f"{label}.obj"))
    mtl_path = os.path.abspath(os.path.join(output_dir, f"{label}.mtl"))
    lines, vertex_count, triangle_count, uv_count = _obj_lines(model, os.path.basename(mtl_path))
    _write_text(obj_path, "\n".join(lines) + "\n")
    _write_text(mtl_path, "\n".join(_mtl_lines(model.pieces)) + "\n")
    return AbcObjExportResult(
        abc_path=os.path.abspath(abc_path),
        obj_path=obj_path,
        mtl_path=mtl_path,
        model_name=model.name,
        piece_count=len(model.pieces),
        vertex_count=vertex_count,
        triangle_count=triangle_count,
        uv_count=uv_count,
        baked_static_pose=model.baked_bind_pose,
    )


def _obj_lines(model: AbcModel, mtl_name: str) -> Tuple[List[str], int, int, int]:
    lines = [
        "# Exported by mm9_editor static ABC OBJ exporter",
        f"# Source model: {model.name}",
        f"# ABC version: {model.version}",
        f"# Static pose baked: {'yes' if model.baked_bind_pose else 'no'}",
        f"mtllib {mtl_name}",
        "",
    ]
    vertex_offset = 1
    uv_offset = 1
    triangles = 0
    for piece_index, piece in enumerate(model.pieces):
        name = _safe_obj_name(piece.name or f"piece_{piece_index}")
        lines.extend((f"o {name}", f"g {name}", f"usemtl mat_{name}"))
        for triangle in piece.triangles:
            if any(ref.vertex_index < 0 or ref.vertex_index >= len(piece.vertices) for ref in triangle.refs):
                continue
            face: List[str] = []
            for ref in triangle.refs:
                point = piece.vertices[ref.vertex_index].pos
                lines.append(f"v {point[0]:.6f} {point[1]:.6f} {point[2]:.6f}")
                lines.append(f"vt {ref.u:.6f} {ref.v:.6f}")
                face.append(f"{vertex_offset}/{uv_offset}")
                vertex_offset += 1
                uv_offset += 1
            lines.append("f " + " ".join(face))
            triangles += 1
        lines.append("")
    return lines, vertex_offset - 1, triangles, uv_offset - 1


def _mtl_lines(pieces: Sequence[AbcPiece]) -> List[str]:
    lines = ["# Placeholder materials exported by mm9_editor", ""]
    for index, piece in enumerate(pieces):
        name = _safe_obj_name(piece.name or f"piece_{index}")
        color = ((0.72, 0.58, 0.42), (0.42, 0.62, 0.76), (0.60, 0.72, 0.46))[index % 3]
        lines.extend((
            f"newmtl mat_{name}",
            f"Kd {color[0]:.6f} {color[1]:.6f} {color[2]:.6f}",
            "Ka 0.000000 0.000000 0.000000",
            "Ks 0.050000 0.050000 0.050000",
            "Ns 8.000000",
            "d 1.000000",
            "",
        ))
    return lines


def _safe_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._") or "abc_model"


def _safe_obj_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._") or "piece"


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Export an MM9 LithTech ABC model to static LOD0 OBJ.")
    parser.add_argument("abc_path")
    parser.add_argument("output_dir")
    parser.add_argument("--base-name", default="")
    parser.add_argument("--no-bake-static-pose", action="store_true")
    args = parser.parse_args(argv)
    result = export_abc_to_obj(
        args.abc_path,
        args.output_dir,
        base_name=args.base_name,
        bake_static_pose=not args.no_bake_static_pose,
    )
    print(f"Exported {result.obj_path}: {result.piece_count} piece(s), {result.triangle_count} triangles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

