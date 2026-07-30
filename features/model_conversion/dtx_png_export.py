"""PNG export for MM9 LithTech DTX textures."""

from __future__ import annotations

import argparse
import os
import re
import struct
import zlib
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from view3d import dtx


@dataclass(frozen=True)
class DtxPngExportResult:
    dtx_path: str
    png_path: str
    width: int
    height: int
    pixel_format: int
    has_useful_alpha: bool


def decode_dtx_rgba(data: bytes) -> Optional[Tuple[int, int, int, bytes]]:
    """Return ``(pixel_format, width, height, rgba_bytes)`` for DTX mip 0."""
    header = dtx.parse_header(data)
    if header is None:
        return None
    pixel_format, width, height, _mip_count = header
    size = dtx._mip0_size(pixel_format, width, height)
    start = dtx._HEADER_SIZE
    pixels = data[start : start + size]
    if len(pixels) != size:
        return None

    if pixel_format == dtx._FMT_DXT1:
        rgba = dtx._decode_dxt1_rgba(pixels, width, height).tobytes()
    elif pixel_format == dtx._FMT_DXT5:
        rgba = dtx._decode_dxt5_rgba(pixels, width, height).tobytes()
    elif pixel_format in dtx._FMT_BGRA:
        converted = bytearray(width * height * 4)
        for offset in range(0, len(pixels), 4):
            blue, green, red, alpha = pixels[offset : offset + 4]
            converted[offset : offset + 4] = bytes((red, green, blue, alpha))
        rgba = bytes(converted)
    else:
        return None
    return pixel_format, width, height, rgba


def png_bytes_rgba(width: int, height: int, rgba: bytes) -> bytes:
    """Encode tightly packed RGBA8 pixels as a PNG byte string."""
    if len(rgba) != width * height * 4:
        raise ValueError("RGBA payload size does not match image dimensions")
    scanlines = b"".join(
        b"\x00" + rgba[row * width * 4 : (row + 1) * width * 4]
        for row in range(height)
    )
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return signature + _png_chunk(b"IHDR", header) + _png_chunk(b"IDAT", zlib.compress(scanlines)) + _png_chunk(b"IEND", b"")


def dtx_to_png_bytes(
    data: bytes,
    *,
    force_opaque_unused_alpha: bool = True,
) -> Optional[Tuple[int, int, int, bool, bytes]]:
    """Decode DTX data and return metadata plus encoded PNG bytes."""
    decoded = decode_dtx_rgba(data)
    if decoded is None:
        return None
    pixel_format, width, height, rgba = decoded
    alpha_info = dtx.inspect_dtx_alpha_bytes(data)
    useful_alpha = bool(alpha_info and alpha_info.has_useful_alpha)
    if force_opaque_unused_alpha and not useful_alpha:
        opaque = bytearray(rgba)
        opaque[3::4] = b"\xff" * (width * height)
        rgba = bytes(opaque)
    return pixel_format, width, height, useful_alpha, png_bytes_rgba(width, height, rgba)


def export_dtx_to_png(
    dtx_path: str,
    output_dir: str,
    *,
    base_name: str = "",
    force_opaque_unused_alpha: bool = True,
) -> DtxPngExportResult:
    """Decode one MM9 ``.DTX`` texture and write mip 0 as RGBA PNG."""
    if not os.path.isfile(dtx_path):
        raise FileNotFoundError(f"DTX file was not found: {dtx_path}")
    with open(dtx_path, "rb") as stream:
        converted = dtx_to_png_bytes(
            stream.read(),
            force_opaque_unused_alpha=force_opaque_unused_alpha,
        )
    if converted is None:
        raise ValueError(f"DTX file could not be decoded: {dtx_path}")
    pixel_format, width, height, useful_alpha, png_data = converted
    os.makedirs(output_dir, exist_ok=True)
    label = _safe_label(base_name or os.path.splitext(os.path.basename(dtx_path))[0])
    png_path = os.path.abspath(os.path.join(output_dir, f"{label}.png"))
    with open(png_path, "wb") as stream:
        stream.write(png_data)
    return DtxPngExportResult(
        dtx_path=os.path.abspath(dtx_path),
        png_path=png_path,
        width=width,
        height=height,
        pixel_format=pixel_format,
        has_useful_alpha=useful_alpha,
    )


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _safe_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._") or "texture"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Export an MM9 LithTech DTX texture to PNG.")
    parser.add_argument("dtx_path", help="Path to the .DTX texture file")
    parser.add_argument("output_dir", help="Directory for the exported PNG")
    parser.add_argument("--base-name", default="", help="Optional output filename stem")
    args = parser.parse_args(argv)
    result = export_dtx_to_png(args.dtx_path, args.output_dir, base_name=args.base_name)
    print(f"Exported {result.png_path}: {result.width}x{result.height} fmt={result.pixel_format}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

