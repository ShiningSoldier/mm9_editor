"""Minimal mesh-to-WorldModel record compiler for additive DAT geometry."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

import _path_setup  # noqa: F401
from core import bsp


Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class CompiledWorldModelRecord:
    name: str
    model: bsp.WorldModelMesh
    raw_bytes: bytes

    @property
    def new_name(self) -> str:
        """Match the copied-record interface consumed by the DAT writer."""
        return self.name

    @property
    def source_name(self) -> str:
        return "generated_geometry"

    @property
    def info_flags_override(self) -> int:
        return 2


@dataclass(frozen=True)
class CompiledPolygonDiagnostics:
    polygon_index: int
    vertex_indices: Tuple[int, ...]
    center: Vec3
    plane_normal: Vec3
    plane_distance: float
    surface_index: int
    texture_index: int
    uv_method: str


@dataclass(frozen=True)
class WorldModelCompileDiagnostics:
    name: str
    min_box: Vec3
    max_box: Vec3
    polygon_count: int
    point_count: int
    surface_count: int
    texture_count: int
    surfaces: List[bsp.Surface]
    surface_plane_indices: List[int]
    polygons: List[CompiledPolygonDiagnostics]


def compile_world_model_record(model: bsp.WorldModelMesh, info_flags: int = 2) -> CompiledWorldModelRecord:
    """Build a minimal v66 WorldModel record for one independent submodel.

    The record deliberately avoids portals, leaves, nodes, and terrain sections.
    It is intended for additive static submodels, not PhysicsBSP/VisBSP rebuilds.
    """
    diagnostics = analyze_model(model)
    raw = bytearray()
    raw += struct.pack("<I", 0)      # NextWorldItem, patched by the DAT writer.
    raw += b"\x00" * 32             # WorldModel padding.
    raw += _pack_world_bsp(model, info_flags=info_flags, diagnostics=diagnostics)
    return CompiledWorldModelRecord(
        name=model.name,
        model=model,
        raw_bytes=bytes(raw),
    )


def analyze_model(model: bsp.WorldModelMesh) -> WorldModelCompileDiagnostics:
    """Validate *model* and return derived values the compiler will serialize."""
    _validate_model(model)
    points = list(model.points)
    polygons: List[CompiledPolygonDiagnostics] = []
    min_box, max_box = _bounds(points)
    source_surface_count = len(model.surfaces or [])
    texture_count = len(model.texture_names or ["Default"])
    for polygon_index, polygon in enumerate(model.polygons):
        normal, distance = _plane_for_polygon(points, polygon)
        source_surface_index = max(0, min(int(polygon.surface_index), source_surface_count - 1))
        surface = model.surfaces[source_surface_index]
        texture_index = max(0, min(int(surface.texture_index), texture_count - 1))
        polygons.append(CompiledPolygonDiagnostics(
            polygon_index=polygon_index,
            vertex_indices=tuple(int(index) for index in polygon.vertex_indices),
            center=_polygon_center(points, polygon),
            plane_normal=normal,
            plane_distance=distance,
            # V66 stores its plane reference on the surface.  Give every
            # polygon a dedicated serialized surface so source meshes that
            # share one material surface across non-coplanar faces remain
            # representable without changing their appearance.
            surface_index=polygon_index,
            texture_index=texture_index,
            uv_method=str(getattr(surface, "mm9_uv_method", "") or "unknown"),
        ))
    serialized_surfaces = [
        model.surfaces[int(polygon.surface_index)]
        for polygon in model.polygons
    ]
    surface_plane_indices = list(range(len(polygons)))
    return WorldModelCompileDiagnostics(
        name=str(model.name),
        min_box=min_box,
        max_box=max_box,
        polygon_count=len(model.polygons),
        point_count=len(points),
        surface_count=len(serialized_surfaces),
        texture_count=texture_count,
        surfaces=serialized_surfaces,
        surface_plane_indices=surface_plane_indices,
        polygons=polygons,
    )


def patch_next_world_item(record: CompiledWorldModelRecord, next_world_item: int) -> bytes:
    raw = bytearray(record.raw_bytes)
    struct.pack_into("<I", raw, 0, int(next_world_item) & 0xFFFFFFFF)
    return bytes(raw)


def _pack_world_bsp(
    model: bsp.WorldModelMesh,
    info_flags: int,
    diagnostics: WorldModelCompileDiagnostics,
) -> bytes:
    polygons = list(model.polygons)
    points = list(model.points)
    surfaces = list(diagnostics.surfaces)
    texture_names = [str(name or "Default") for name in (model.texture_names or ["Default"])]
    if not surfaces:
        surfaces = [
            bsp.Surface(
                uv_o=(0.0, 0.0, 0.0),
                uv_p=(1.0, 0.0, 0.0),
                uv_q=(0.0, 0.0, 1.0),
                texture_index=0,
                flags=0,
                texture_flags=0,
            )
        ]

    planes = [
        (item.plane_normal, item.plane_distance)
        for item in diagnostics.polygons
    ]
    point_normals = _point_normals(len(points), polygons, planes)
    texture_blob = b"".join(_cstring(name) for name in texture_names)

    out = bytearray()
    out += struct.pack("<I", int(info_flags) & 0xFFFFFFFF)
    out += struct.pack("<I", 0)  # LT2 unknown field.
    out += _lt_string(model.name)

    out += struct.pack("<I", len(points))
    out += struct.pack("<I", len(planes))
    out += struct.pack("<I", len(surfaces))
    out += struct.pack("<I", 0)  # user portals
    out += struct.pack("<I", len(polygons))
    out += struct.pack("<I", 0)  # leaves
    out += struct.pack("<I", sum(len(poly.vertex_indices) for poly in polygons))
    out += struct.pack("<I", 0)  # total vis
    out += struct.pack("<I", 0)  # leaf list count
    out += struct.pack("<I", 0)  # nodes
    out += struct.pack("<I", 0)  # unknown_value_2
    out += struct.pack("<I", 0)  # unknown_value_3

    min_box, max_box = diagnostics.min_box, diagnostics.max_box
    out += _vec3(min_box)
    out += _vec3(max_box)
    out += _vec3(model.translation)

    out += struct.pack("<I", len(texture_blob))
    out += struct.pack("<I", len(texture_names))
    out += texture_blob

    for polygon in polygons:
        count = len(polygon.vertex_indices)
        if count <= 0xFF:
            out += struct.pack("<BB", count, 0)
        else:
            out += struct.pack("<BB", 0xFF, count - 0xFF)

    for normal, distance in planes:
        out += _vec3(normal)
        out += struct.pack("<f", float(distance))

    for surface_index, surface in enumerate(surfaces):
        texture_index = max(0, min(int(surface.texture_index), len(texture_names) - 1))
        out += _vec3(surface.uv_o)
        out += _vec3(surface.uv_p)
        out += _vec3(surface.uv_q)
        out += struct.pack("<H", texture_index)
        # In MM9 v66 the plane reference belongs to the surface, not the
        # polygon.  Each value indexes the plane table emitted above.
        out += struct.pack("<I", diagnostics.surface_plane_indices[surface_index])
        out += struct.pack("<I", int(surface.flags) & 0xFFFFFFFF)
        out += struct.pack("<I", 0)
        out += struct.pack("<B", 0)  # no effect strings
        out += struct.pack("<H", int(surface.texture_flags) & 0xFFFF)

    for polygon, polygon_diag in zip(polygons, diagnostics.polygons):
        center = polygon_diag.center
        surface_index = polygon_diag.surface_index
        out += _vec3(center)
        out += struct.pack("<HHH", 0, 0, 0)  # lightmap width, height, unknown flag
        # The retail v66 loader reads a single uint32 here.  A previous
        # uint16-surface/uint16-plane encoding produced values such as
        # 0x00010001 and caused LT_INVALIDWORLDFILE.
        out += struct.pack("<I", surface_index)
        for vertex_index in polygon.vertex_indices:
            out += struct.pack("<H", int(vertex_index) & 0xFFFF)
            out += b"\xFF\xFF\xFF"

    for point, normal in zip(points, point_normals):
        out += _vec3(point)
        out += _vec3(normal)

    # Empty PhysicsBlockTable: dimensions 0, zero vectors, no blocks.
    out += struct.pack("<III", 0, 0, 0)
    out += _vec3((0.0, 0.0, 0.0))
    out += _vec3((0.0, 0.0, 0.0))
    out += struct.pack("<i", -1)  # root_node_index
    out += struct.pack("<i", 0)   # section count
    return bytes(out)


def _validate_model(model: bsp.WorldModelMesh) -> None:
    if not str(model.name or "").strip():
        raise ValueError("compiled BSP model name cannot be empty")
    if len(model.points) > 0xFFFF:
        raise ValueError(f"BSP model {model.name!r} has too many points ({len(model.points)})")
    if len(model.polygons) > 0xFFFF:
        raise ValueError(f"BSP model {model.name!r} has too many polygons ({len(model.polygons)})")
    if not model.points or not model.polygons:
        raise ValueError(f"BSP model {model.name!r} has no geometry")
    if not model.surfaces:
        raise ValueError(f"BSP model {model.name!r} has no surfaces")
    if not model.texture_names:
        raise ValueError(f"BSP model {model.name!r} has no texture names")
    for point_index, point in enumerate(model.points):
        _validate_vec3(point, f"BSP model {model.name!r} point {point_index}")
    for surface_index, surface in enumerate(model.surfaces):
        if surface.texture_index < 0 or surface.texture_index >= len(model.texture_names):
            raise ValueError(
                f"BSP model {model.name!r} surface {surface_index} references invalid texture "
                f"{surface.texture_index}"
            )
        _validate_vec3(surface.uv_o, f"BSP model {model.name!r} surface {surface_index} uv_o")
        _validate_vec3(surface.uv_p, f"BSP model {model.name!r} surface {surface_index} uv_p")
        _validate_vec3(surface.uv_q, f"BSP model {model.name!r} surface {surface_index} uv_q")
        if _length(surface.uv_p) <= 1.0e-8:
            raise ValueError(f"BSP model {model.name!r} surface {surface_index} has degenerate uv_p projection")
        if _length(surface.uv_q) <= 1.0e-8:
            raise ValueError(f"BSP model {model.name!r} surface {surface_index} has degenerate uv_q projection")
    for index, polygon in enumerate(model.polygons):
        if len(polygon.vertex_indices) < 3:
            raise ValueError(f"BSP model {model.name!r} polygon {index} has fewer than 3 vertices")
        if len(polygon.vertex_indices) > 0x1FE:
            raise ValueError(f"BSP model {model.name!r} polygon {index} has too many vertices")
        if polygon.surface_index < 0 or polygon.surface_index >= len(model.surfaces):
            raise ValueError(
                f"BSP model {model.name!r} polygon {index} references invalid surface "
                f"{polygon.surface_index}"
            )
        for vertex_index in polygon.vertex_indices:
            if vertex_index < 0 or vertex_index >= len(model.points):
                raise ValueError(f"BSP model {model.name!r} polygon {index} references invalid point {vertex_index}")
        if len(set(polygon.vertex_indices)) < 3:
            raise ValueError(f"BSP model {model.name!r} polygon {index} is degenerate; fewer than 3 unique vertices")
        normal = _polygon_normal([model.points[vertex_index] for vertex_index in polygon.vertex_indices])
        if _length(normal) <= 1.0e-8:
            raise ValueError(f"BSP model {model.name!r} polygon {index} is degenerate; plane normal is zero")


def _plane_for_polygon(points: Sequence[Vec3], polygon: bsp.Polygon) -> Tuple[Vec3, float]:
    verts = [points[index] for index in polygon.vertex_indices]
    normal = _polygon_normal(verts)
    distance = _dot(normal, verts[0])
    return normal, distance


def _polygon_normal(vertices: Sequence[Vec3]) -> Vec3:
    # Newell's method is stable for triangles and n-gons.
    nx = ny = nz = 0.0
    for i, current in enumerate(vertices):
        nxt = vertices[(i + 1) % len(vertices)]
        nx += (current[1] - nxt[1]) * (current[2] + nxt[2])
        ny += (current[2] - nxt[2]) * (current[0] + nxt[0])
        nz += (current[0] - nxt[0]) * (current[1] + nxt[1])
    return _unit((nx, ny, nz))


def _point_normals(
    point_count: int,
    polygons: Sequence[bsp.Polygon],
    planes: Sequence[Tuple[Vec3, float]],
) -> List[Vec3]:
    accum = [[0.0, 0.0, 0.0] for _ in range(point_count)]
    for polygon, (normal, _distance) in zip(polygons, planes):
        for index in polygon.vertex_indices:
            accum[index][0] += normal[0]
            accum[index][1] += normal[1]
            accum[index][2] += normal[2]
    return [_unit((value[0], value[1], value[2])) for value in accum]


def _polygon_center(points: Sequence[Vec3], polygon: bsp.Polygon) -> Vec3:
    verts = [points[index] for index in polygon.vertex_indices]
    count = float(len(verts))
    return (
        sum(point[0] for point in verts) / count,
        sum(point[1] for point in verts) / count,
        sum(point[2] for point in verts) / count,
    )


def _bounds(points: Sequence[Vec3]) -> Tuple[Vec3, Vec3]:
    return (
        (min(p[0] for p in points), min(p[1] for p in points), min(p[2] for p in points)),
        (max(p[0] for p in points), max(p[1] for p in points), max(p[2] for p in points)),
    )


def _dot(a: Vec3, b: Vec3) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _unit(value: Vec3) -> Vec3:
    length = math.sqrt(_dot(value, value))
    if length <= 1.0e-6:
        return (0.0, 0.0, 0.0)
    return (float(value[0]) / length, float(value[1]) / length, float(value[2]) / length)


def _length(value: Vec3) -> float:
    return math.sqrt(_dot(value, value))


def _validate_vec3(value: Sequence[float], label: str) -> None:
    if len(value) != 3:
        raise ValueError(f"{label} must be a 3-vector")
    try:
        converted = (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} contains non-numeric values") from exc
    if not all(math.isfinite(item) for item in converted):
        raise ValueError(f"{label} contains non-finite values")


def _lt_string(value: str) -> bytes:
    raw = str(value or "").encode("latin-1")
    if len(raw) > 0xFFFF:
        raise ValueError(f"string is too long: {value!r}")
    return struct.pack("<H", len(raw)) + raw


def _cstring(value: str) -> bytes:
    return str(value or "").encode("latin-1") + b"\x00"


def _vec3(value: Vec3) -> bytes:
    return struct.pack("<3f", float(value[0]), float(value[1]), float(value[2]))
