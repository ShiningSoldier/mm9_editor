"""
dtx.py
======

LithTech 2 / v66 DTX texture loader for the MM9 3-D viewer.

Parses the 164-byte DTX header, identifies the pixel format, and uploads the
available mip chain to the GPU as an OpenGL texture object.

Supported pixel formats  (field at header offset 26)
------------------------------------------------------
  4 -> DXT1   compressed GPU upload; RGBA8 fallback  ~73 % of MM9 textures
  6 -> DXT5   compressed GPU upload; RGBA8 fallback  ~ 7 % of MM9 textures
  3 -> BGRA32 GL_BGRA / GL_UNSIGNED_BYTE         ~14 % (uncompressed, tiled)
  0 -> BGRA32 same; used in a handful of v4 files

DTX header layout  (little-endian, all fields are contiguous)
-------------------------------------------------------------
  offset  0 : uint32  reserved (always 0)
  offset  4 : int32   version  (-5 for almost all MM9 files; -4 for a few)
  offset  8 : uint16  width
  offset 10 : uint16  height
  offset 12 : uint16  mip_count  (1-4)
  offset 14 : uint16  sections   (ignored)
  offset 16 : uint32  flags      (ignored)
  offset 20 : uint32  user_flags (ignored)
  offset 24 : uint16  extra_0    (ignored)
  offset 26 : uint16  pixel_format  <- the only field we act on
  offset 28 : ...     more metadata + 128-byte command string (ignored)
  offset 164: pixel data, mip 0 first, then mip 1, 2, ...

Pixel data sizes  (bytes per mip level)
----------------------------------------
  DXT1  :  ceil(w/4) x ceil(h/4) x 8
  DXT5  :  ceil(w/4) x ceil(h/4) x 16
  BGRA32:  w x h x 4

Implementation note
-------------------
DXT1/DXT5 textures use their authored mip chain and remain compressed when
the OpenGL driver accepts S3TC uploads.  Unsupported drivers and wrappers
fall back transparently to the existing CPU decoder and RGBA8 upload path.

TextureCache
------------
Builds a case-insensitive path index by scanning *textures_root* once at
construction.  BSP texture names use Windows backslash separators and mixed
case (e.g. ``World\\Tiles\\floor01.dtx``); the cache normalises them to
upper-case forward-slash keys for lookup.  GL texture IDs are cached after
the first successful upload; failed lookups are stored as None so the same
path is never retried.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import struct
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Header constants
# ---------------------------------------------------------------------------

_HEADER_SIZE = 164

_FMT_DXT1 = 4
_FMT_DXT5 = 6
_FMT_BGRA = frozenset({0, 3})   # f26=3 (v5 files) and f26=0 (older v4 files)

# ---------------------------------------------------------------------------
# Alpha metadata
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TextureAlphaInfo:
    """Small CPU-side summary of mip-0 alpha usage for material heuristics."""

    pixel_format: int
    width: int
    height: int
    min_alpha: int
    max_alpha: int
    transparent_fraction: float  # alpha < 16
    mid_fraction: float          # 16 <= alpha < 240
    nonopaque_fraction: float    # alpha < 240

    @property
    def has_useful_alpha(self) -> bool:
        # Many MM9 prop skins have an all-zero/undefined alpha channel.  Treat
        # those as opaque; enabling alpha would erase the whole model.
        if self.max_alpha <= 16:
            return False
        # Some actor skins, for example Orbus*.dtx, carry a very low-range
        # alpha channel where almost every pixel is below the shader's cutout
        # threshold.  Honouring that alpha makes the whole model disappear, so
        # treat it as unused/opaque metadata rather than real transparency.
        if self.max_alpha <= 32 and self.transparent_fraction > 0.95:
            return False
        if self.min_alpha >= 240 and self.mid_fraction <= 0.0:
            return False
        return self.transparent_fraction > 0.0 or self.mid_fraction > 0.0


# ---------------------------------------------------------------------------
# Pixel-data size helpers
# ---------------------------------------------------------------------------

def _mip0_size(pixel_fmt: int, w: int, h: int) -> int:
    """Return the byte count for mip level 0."""
    if pixel_fmt == _FMT_DXT1:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 8
    if pixel_fmt == _FMT_DXT5:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16
    # BGRA32
    return max(1, w) * max(1, h) * 4


def _rgb565(c: int) -> Tuple[int, int, int]:
    """Expand a packed RGB565 colour to 8-bit RGB."""
    r5 = (c >> 11) & 0x1F
    g6 = (c >> 5) & 0x3F
    b5 = c & 0x1F
    return (
        (r5 << 3) | (r5 >> 2),
        (g6 << 2) | (g6 >> 4),
        (b5 << 3) | (b5 >> 2),
    )


def _decode_dxt1_rgba(pix: bytes, w: int, h: int) -> np.ndarray:
    """Decode DXT1 mip-0 bytes into an ``(h, w, 4)`` uint8 RGBA array."""
    out = np.zeros((h, w, 4), dtype=np.uint8)
    bw = max(1, (w + 3) // 4)
    bh = max(1, (h + 3) // 4)

    for by in range(bh):
        for bx in range(bw):
            off = (by * bw + bx) * 8
            if off + 8 > len(pix):
                continue
            c0, c1, bits = struct.unpack_from("<HHI", pix, off)
            r0, g0, b0 = _rgb565(c0)
            r1, g1, b1 = _rgb565(c1)
            colors = [
                (r0, g0, b0, 255),
                (r1, g1, b1, 255),
            ]
            if c0 > c1:
                colors.extend([
                    ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255),
                    ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255),
                ])
            else:
                colors.extend([
                    ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255),
                    (0, 0, 0, 0),
                ])

            for py in range(4):
                y = by * 4 + py
                if y >= h:
                    continue
                for px in range(4):
                    x = bx * 4 + px
                    if x >= w:
                        continue
                    idx = (bits >> (2 * (py * 4 + px))) & 0x03
                    out[y, x] = colors[idx]
    return out


def _dxt5_alpha_table(a0: int, a1: int) -> List[int]:
    table = [a0, a1]
    if a0 > a1:
        table.extend([
            (6 * a0 + 1 * a1) // 7,
            (5 * a0 + 2 * a1) // 7,
            (4 * a0 + 3 * a1) // 7,
            (3 * a0 + 4 * a1) // 7,
            (2 * a0 + 5 * a1) // 7,
            (1 * a0 + 6 * a1) // 7,
        ])
    else:
        table.extend([
            (4 * a0 + 1 * a1) // 5,
            (3 * a0 + 2 * a1) // 5,
            (2 * a0 + 3 * a1) // 5,
            (1 * a0 + 4 * a1) // 5,
            0,
            255,
        ])
    return table


def _decode_dxt5_rgba(pix: bytes, w: int, h: int) -> np.ndarray:
    """Decode DXT5 mip-0 bytes into an ``(h, w, 4)`` uint8 RGBA array."""
    out = np.zeros((h, w, 4), dtype=np.uint8)
    bw = max(1, (w + 3) // 4)
    bh = max(1, (h + 3) // 4)

    for by in range(bh):
        for bx in range(bw):
            off = (by * bw + bx) * 16
            if off + 16 > len(pix):
                continue

            a0 = pix[off]
            a1 = pix[off + 1]
            alpha_table = _dxt5_alpha_table(a0, a1)
            alpha_bits = int.from_bytes(pix[off + 2 : off + 8], "little")

            c0, c1, color_bits = struct.unpack_from("<HHI", pix, off + 8)
            r0, g0, b0 = _rgb565(c0)
            r1, g1, b1 = _rgb565(c1)
            colors = [
                (r0, g0, b0),
                (r1, g1, b1),
                ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3),
                ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3),
            ]

            for py in range(4):
                y = by * 4 + py
                if y >= h:
                    continue
                for px in range(4):
                    x = bx * 4 + px
                    if x >= w:
                        continue
                    i = py * 4 + px
                    color_idx = (color_bits >> (2 * i)) & 0x03
                    alpha_idx = (alpha_bits >> (3 * i)) & 0x07
                    r, g, b = colors[color_idx]
                    out[y, x] = (r, g, b, alpha_table[alpha_idx])
    return out


def parse_header(data: bytes) -> Optional[Tuple[int, int, int, int]]:
    """
    Parse the first 28 bytes of a DTX blob.

    Returns ``(pixel_format, width, height, mip_count)`` or ``None`` if
    the data is too short or the format is unrecognised.
    """
    if len(data) < 28:
        return None
    pixel_fmt = struct.unpack_from("<H", data, 26)[0]
    w         = struct.unpack_from("<H", data,  8)[0]
    h         = struct.unpack_from("<H", data, 10)[0]
    mips      = struct.unpack_from("<H", data, 12)[0]
    if w == 0 or h == 0:
        return None
    if pixel_fmt not in (_FMT_DXT1, _FMT_DXT5) and pixel_fmt not in _FMT_BGRA:
        return None
    return pixel_fmt, w, h, mips


def _alpha_fractions(values: List[int]) -> Tuple[int, int, float, float, float]:
    """Return min/max and useful alpha fractions for a sequence of alpha bytes."""
    if not values:
        return 255, 255, 0.0, 0.0, 0.0
    n = float(len(values))
    min_a = min(values)
    max_a = max(values)
    transparent = sum(1 for v in values if v < 16) / n
    mid = sum(1 for v in values if 16 <= v < 240) / n
    nonopaque = sum(1 for v in values if v < 240) / n
    return min_a, max_a, transparent, mid, nonopaque


def _dxt5_alpha_values(pix: bytes) -> List[int]:
    """
    Decode only the alpha component of DXT5 mip-0 blocks.

    The colour block is intentionally ignored; this is for material
    classification, not texture rendering.
    """
    values: List[int] = []
    for off in range(0, len(pix), 16):
        if off + 8 > len(pix):
            break
        a0 = pix[off]
        a1 = pix[off + 1]
        table = _dxt5_alpha_table(a0, a1)

        bits = int.from_bytes(pix[off + 2 : off + 8], "little")
        for i in range(16):
            values.append(table[(bits >> (3 * i)) & 7])
    return values


def inspect_dtx_alpha_bytes(data: bytes) -> Optional[TextureAlphaInfo]:
    """
    Return CPU-side alpha statistics for a DTX blob, or None on parse failure.

    DXT1 files are reported as fully opaque because this viewer uploads them
    with an RGB internal format.
    """
    hdr = parse_header(data)
    if hdr is None:
        return None
    pixel_fmt, w, h, _mips = hdr

    expected = _mip0_size(pixel_fmt, w, h)
    if len(data) < _HEADER_SIZE + expected:
        return None
    pix = data[_HEADER_SIZE : _HEADER_SIZE + expected]

    if pixel_fmt == _FMT_DXT5:
        values = _dxt5_alpha_values(pix)
    elif pixel_fmt in _FMT_BGRA:
        values = list(pix[3::4])
    else:
        values = [255]

    min_a, max_a, transparent, mid, nonopaque = _alpha_fractions(values)
    return TextureAlphaInfo(
        pixel_format=pixel_fmt,
        width=w,
        height=h,
        min_alpha=min_a,
        max_alpha=max_a,
        transparent_fraction=transparent,
        mid_fraction=mid,
        nonopaque_fraction=nonopaque,
    )


def inspect_dtx_alpha_file(path: str) -> Optional[TextureAlphaInfo]:
    """Read *path* and return alpha metadata without creating a GL texture."""
    try:
        with open(path, "rb") as f:
            return inspect_dtx_alpha_bytes(f.read())
    except OSError:
        return None


# ---------------------------------------------------------------------------
# GL upload
# ---------------------------------------------------------------------------

def _dxt_mip_payloads(
    data: bytes,
    pixel_fmt: int,
    w: int,
    h: int,
    mip_count: int,
) -> Optional[List[Tuple[int, int, int, bytes]]]:
    """Return complete authored DXT mip payloads, or ``None`` if truncated."""
    if pixel_fmt not in (_FMT_DXT1, _FMT_DXT5):
        return None
    payloads: List[Tuple[int, int, int, bytes]] = []
    offset = _HEADER_SIZE
    mip_w = int(w)
    mip_h = int(h)
    for level in range(max(1, int(mip_count))):
        size = _mip0_size(pixel_fmt, mip_w, mip_h)
        end = offset + size
        if end > len(data):
            return None
        payloads.append((level, mip_w, mip_h, data[offset:end]))
        offset = end
        mip_w = max(1, mip_w // 2)
        mip_h = max(1, mip_h // 2)
    return payloads


def _clear_gl_errors(GL) -> None:
    get_error = getattr(GL, "glGetError", None)
    if get_error is None:
        return
    no_error = int(getattr(GL, "GL_NO_ERROR", 0))
    for _ in range(8):
        if int(get_error()) == no_error:
            return


def _try_upload_compressed_dxt(
    GL,
    data: bytes,
    pixel_fmt: int,
    w: int,
    h: int,
    mip_count: int,
) -> bool:
    """Upload authored S3TC mips; return False when the path is unavailable.

    A driver rejection raises so the caller can discard the partially defined
    texture object and retry through the CPU RGBA fallback.
    """
    upload = getattr(GL, "glCompressedTexImage2D", None)
    if upload is None:
        return False
    payloads = _dxt_mip_payloads(data, pixel_fmt, w, h, mip_count)
    if not payloads:
        return False

    if pixel_fmt == _FMT_DXT1:
        internal_format = int(
            getattr(GL, "GL_COMPRESSED_RGBA_S3TC_DXT1_EXT", 0x83F1)
        )
    elif pixel_fmt == _FMT_DXT5:
        internal_format = int(
            getattr(GL, "GL_COMPRESSED_RGBA_S3TC_DXT5_EXT", 0x83F3)
        )
    else:
        return False

    _clear_gl_errors(GL)
    for level, mip_w, mip_h, payload in payloads:
        upload(
            GL.GL_TEXTURE_2D,
            level,
            internal_format,
            mip_w,
            mip_h,
            0,
            len(payload),
            payload,
        )
        get_error = getattr(GL, "glGetError", None)
        if get_error is not None:
            error = int(get_error())
            if error != int(getattr(GL, "GL_NO_ERROR", 0)):
                raise RuntimeError(f"compressed texture upload GL error 0x{error:04x}")

    max_level = getattr(GL, "GL_TEXTURE_MAX_LEVEL", None)
    if max_level is not None:
        GL.glTexParameteri(
            GL.GL_TEXTURE_2D,
            max_level,
            payloads[-1][0],
        )
    return True


def _configure_bound_texture(GL) -> None:
    GL.glTexParameteri(
        GL.GL_TEXTURE_2D,
        GL.GL_TEXTURE_MIN_FILTER,
        GL.GL_LINEAR_MIPMAP_LINEAR,
    )
    GL.glTexParameteri(
        GL.GL_TEXTURE_2D,
        GL.GL_TEXTURE_MAG_FILTER,
        GL.GL_LINEAR,
    )
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)


def load_dtx_bytes(data: bytes) -> Optional[int]:
    """
    Parse a DTX blob and upload it to the GPU.

    Returns the OpenGL texture ID on success, or ``None`` if the format is
    unrecognised, the data is too short, or a GL error occurs.
    Requires a live GL context.
    """
    from OpenGL import GL  # type: ignore

    hdr = parse_header(data)
    if hdr is None:
        return None
    pixel_fmt, w, h, mip_count = hdr

    expected = _mip0_size(pixel_fmt, w, h)
    if len(data) < _HEADER_SIZE + expected:
        return None

    pix = data[_HEADER_SIZE : _HEADER_SIZE + expected]

    tex = None
    try:
        tex = int(GL.glGenTextures(1))
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        _configure_bound_texture(GL)

        compressed_uploaded = False
        if pixel_fmt in (_FMT_DXT1, _FMT_DXT5):
            try:
                compressed_uploaded = _try_upload_compressed_dxt(
                    GL,
                    data,
                    pixel_fmt,
                    w,
                    h,
                    mip_count,
                )
            except Exception:
                # A failed compressed call can leave texture storage partially
                # defined.  Retry with a fresh object through the proven CPU
                # decoder rather than relying on driver-specific recovery.
                GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
                GL.glDeleteTextures([tex])
                tex = int(GL.glGenTextures(1))
                GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
                _configure_bound_texture(GL)

        if compressed_uploaded:
            pass
        elif pixel_fmt == _FMT_DXT1:
            rgba = _decode_dxt1_rgba(pix, w, h)
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D, 0,
                GL.GL_RGBA,
                w, h, 0,
                GL.GL_RGBA,
                GL.GL_UNSIGNED_BYTE,
                rgba,
            )
        elif pixel_fmt == _FMT_DXT5:
            rgba = _decode_dxt5_rgba(pix, w, h)
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D, 0,
                GL.GL_RGBA,
                w, h, 0,
                GL.GL_RGBA,
                GL.GL_UNSIGNED_BYTE,
                rgba,
            )
        else:
            # BGRA32: LithTech stores pixels as [B, G, R, A] bytes in memory.
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D, 0,
                GL.GL_RGBA,
                w, h, 0,
                GL.GL_BGRA,            # matches LithTech's byte order
                GL.GL_UNSIGNED_BYTE,
                np.frombuffer(pix, dtype=np.uint8),
            )

        if not compressed_uploaded:
            # CPU/BGRA fallback uploads mip 0, then lets the GPU generate a
            # complete chain.  Compressed uploads retain the authored mips.
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)

        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        return tex

    except Exception as exc:
        print(f"[dtx] GL upload error ({w}x{h} fmt={pixel_fmt}): {exc}",
              file=sys.stderr)
        try:
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            if tex is not None:
                GL.glDeleteTextures([tex])
        except Exception:
            pass
        return None


def load_dtx_file(path: str) -> Optional[int]:
    """Load a .dtx file and upload it.  Returns GL texture ID or ``None``."""
    try:
        with open(path, "rb") as f:
            return load_dtx_bytes(f.read())
    except OSError as exc:
        print(f"[dtx] cannot read {path!r}: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# TextureCache
# ---------------------------------------------------------------------------

class TextureCache:
    """
    Resolves BSP texture names to GL texture IDs with lazy loading.

    Parameters
    ----------
    textures_root : str
        Path to a cached TEXTURES tree materialized from ``TEXTURES.REZ``.
        An index of all ``.DTX`` files under this root is built at
        construction time; subsequent :meth:`get` calls are O(1) hash
        lookups.
    """

    def __init__(self, textures_root: str) -> None:
        self._root  = textures_root
        # upper-case relative path (forward slashes) -> absolute path on disk
        self._index: Dict[str, str]           = {}
        # upper-case relative path -> GL texture ID (int) or None (failed/missing)
        self._cache: Dict[str, Optional[int]] = {}
        # upper-case relative path -> alpha metadata or None (failed/missing)
        self._alpha_cache: Dict[str, Optional[TextureAlphaInfo]] = {}
        # upper-case relative path -> (width, height) or None (failed/missing)
        self._size_cache: Dict[str, Optional[Tuple[int, int]]] = {}
        self._build_index()

    # ------------------------------------------------------------------
    # Index
    # ------------------------------------------------------------------

    def _build_index(self) -> None:
        """Scan *textures_root* and populate the case-insensitive lookup."""
        if not os.path.isdir(self._root):
            print(f"[dtx] textures root not found: {self._root!r}",
                  file=sys.stderr)
            return
        count = 0
        for dirpath, _dirs, files in os.walk(self._root):
            for fn in files:
                if not fn.upper().endswith(".DTX"):
                    continue
                full = os.path.join(dirpath, fn)
                rel  = os.path.relpath(full, self._root)
                key  = rel.replace(os.sep, "/").upper()
                self._index[key] = full
                count += 1

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _resolve(self, bsp_name: str) -> Optional[str]:
        """
        Return the index key for *bsp_name*, or ``None`` if not found.

        Tries three strategies in order:
        1. Strip common root prefixes (our root may already point at
           TEXTURES or SKINS).
        2. The name as-is, with normalised separators. Extensionless DTX
           names are accepted.
        3. Basename only -- last resort when the caller omits the directory
           part.
        """
        norm = bsp_name.replace("\\", "/").upper().lstrip("/")
        candidates = [norm]

        # Strategy 1: strip common leading prefixes
        for prefix in ("TEXTURES/", "SKINS/"):
            if norm.startswith(prefix):
                candidates.append(norm[len(prefix):])

        expanded = []
        seen = set()
        for candidate in candidates:
            if not candidate:
                continue
            names = [candidate]
            if not candidate.endswith(".DTX"):
                names.append(candidate + ".DTX")
            for name in names:
                if name not in seen:
                    expanded.append(name)
                    seen.add(name)

        # Strategy 2: exact relative path
        for candidate in expanded:
            if candidate in self._index:
                return candidate

        # Strategy 3: basename match (linear scan, cached after first hit)
        for candidate in expanded:
            base = candidate.rsplit("/", 1)[-1]
            for key in self._index:
                if key == base or key.endswith("/" + base):
                    return key

        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, bsp_name: str) -> int:
        """
        Return the GL texture ID for *bsp_name*, or ``0`` if not found or
        the upload failed.  Results are cached after the first lookup.
        """
        # Normalise for cache key (strip prefix once)
        norm = bsp_name.replace("\\", "/").upper().lstrip("/")
        if norm.startswith("TEXTURES/"):
            norm = norm[len("TEXTURES/"):]

        if norm in self._cache:
            return self._cache[norm] or 0

        key = self._resolve(bsp_name)
        if key is None:
            self._cache[norm] = None
            return 0

        tex_id = load_dtx_file(self._index[key])
        self._cache[norm] = tex_id
        return tex_id or 0

    def has(self, bsp_name: str) -> bool:
        """
        Return True when *bsp_name* resolves to an indexed DTX file.

        This is intentionally CPU-only and does not upload the texture.  Object
        material binding uses it to choose among inferred skin candidates
        without polluting the GL texture cache or producing miss logs.
        """
        return self._resolve(bsp_name) is not None

    def alpha_info(self, bsp_name: str) -> Optional[TextureAlphaInfo]:
        """
        Return alpha metadata for *bsp_name* without uploading the texture.

        The lookup rules mirror :meth:`get`, so callers can ask about the same
        mixed-case paths they use for rendering.
        """
        norm = bsp_name.replace("\\", "/").upper().lstrip("/")
        if norm.startswith("TEXTURES/"):
            norm = norm[len("TEXTURES/"):]

        if norm in self._alpha_cache:
            return self._alpha_cache[norm]

        key = self._resolve(bsp_name)
        if key is None:
            self._alpha_cache[norm] = None
            return None

        info = inspect_dtx_alpha_file(self._index[key])
        self._alpha_cache[norm] = info
        return info

    def image_size(self, bsp_name: str) -> Optional[Tuple[int, int]]:
        """
        Return ``(width, height)`` for *bsp_name* without uploading texture data.

        Used by the BSP triangulator because LithTech OPQ vectors produce
        pixel-space coordinates that must be normalised by the texture's real
        dimensions.
        """
        norm = bsp_name.replace("\\", "/").upper().lstrip("/")
        if norm.startswith("TEXTURES/"):
            norm = norm[len("TEXTURES/"):]

        if norm in self._size_cache:
            return self._size_cache[norm]

        key = self._resolve(bsp_name)
        if key is None:
            self._size_cache[norm] = None
            return None

        try:
            with open(self._index[key], "rb") as f:
                hdr = parse_header(f.read(28))
        except OSError:
            hdr = None

        size = (hdr[1], hdr[2]) if hdr is not None else None
        self._size_cache[norm] = size
        return size

    def invalidate(self) -> None:
        """Delete all uploaded GL textures and clear the ID cache."""
        try:
            from OpenGL import GL  # type: ignore
            ids = [v for v in self._cache.values() if v]
            if ids:
                GL.glDeleteTextures(ids)
        except Exception:
            pass
        self._cache.clear()
        self._alpha_cache.clear()
        self._size_cache.clear()

    @property
    def loaded_count(self) -> int:
        """Number of successfully uploaded textures."""
        return sum(1 for v in self._cache.values() if v)

    @property
    def miss_count(self) -> int:
        """Number of lookups that resolved to no file or failed to upload."""
        return sum(1 for v in self._cache.values() if v is None)

    @property
    def index_size(self) -> int:
        """Total number of .DTX files found in the textures root."""
        return len(self._index)
