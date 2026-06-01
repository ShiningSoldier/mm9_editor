"""
collision_sidecar.py
====================

Reader for MM9 collision sidecars produced by the LithTech compatibility tools.
The binary format is intentionally tiny:

    magic      MM9COLL\0
    u32        version
    u32        vertex_count
    u32        index_count
    u32        triangle_count
    vertices   vertex_count * vec3 float32
    indices    index_count * uint32
    triangles  triangle_count * (role_id, source_id, flags) uint32 triples
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import struct
from typing import Dict, List, Optional, Tuple

import numpy as np


ROLE_NAMES: Dict[int, str] = {
    1: "static",
    2: "floor",
    3: "wall",
    4: "blockingHelper",
    5: "dynamicDoor",
    6: "triggerOnly",
    7: "water",
}


@dataclass(frozen=True)
class CollisionTriangleMeta:
    role_id: int
    source_id: int
    flags: int

    @property
    def role(self) -> str:
        return ROLE_NAMES.get(int(self.role_id), "unknown")


@dataclass(frozen=True)
class CollisionSourceModel:
    id: int
    name: str = ""
    class_name: str = ""
    base_role: str = ""
    door: Dict[str, object] = field(default_factory=dict)


@dataclass
class CollisionSidecar:
    path: str
    vertices: np.ndarray
    indices: np.ndarray
    triangles: List[CollisionTriangleMeta]
    source_models: Dict[int, CollisionSourceModel] = field(default_factory=dict)
    includes_render_floors: bool = False
    render_floor_triangles: int = 0

    def role_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for tri in self.triangles:
            counts[tri.role] = counts.get(tri.role, 0) + 1
        return counts

    def source_label(self, source_id: int) -> str:
        source = self.source_models.get(int(source_id))
        if source is None:
            return f"source {source_id}"
        if source.name and source.class_name:
            return f"{source.name} ({source.class_name})"
        return source.name or source.class_name or f"source {source_id}"


def _manifest_collision_section(manifest_path: Optional[str]) -> dict:
    if not manifest_path:
        return {}
    try:
        with open(manifest_path, "r", encoding="latin-1") as f:
            manifest = json.load(f)
    except Exception:
        return {}
    section = manifest.get("collisionMesh")
    return section if isinstance(section, dict) else {}


def _source_models_from_manifest(section: dict) -> Dict[int, CollisionSourceModel]:
    out: Dict[int, CollisionSourceModel] = {}
    for item in section.get("sourceModels", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            source_id = int(item.get("id", -1))
        except Exception:
            continue
        if source_id < 0:
            continue
        door = item.get("door")
        out[source_id] = CollisionSourceModel(
            id=source_id,
            name=str(item.get("name") or ""),
            class_name=str(item.get("class") or ""),
            base_role=str(item.get("baseRole") or ""),
            door=door if isinstance(door, dict) else {},
        )
    return out


def read_collision_sidecar(path: str, manifest_path: Optional[str] = None) -> CollisionSidecar:
    """Read an ``MM9COLL`` sidecar and optional manifest source metadata."""
    with open(path, "rb") as f:
        data = f.read()

    if len(data) < 24 or data[:8] != b"MM9COLL\0":
        raise ValueError(f"{path} is not an MM9COLL collision sidecar")

    version, vertex_count, index_count, triangle_count = struct.unpack_from("<IIII", data, 8)
    if version != 1:
        raise ValueError(f"unsupported MM9COLL version {version}")

    expected = 24 + vertex_count * 12 + index_count * 4 + triangle_count * 12
    if len(data) != expected:
        raise ValueError(f"MM9COLL size mismatch: expected {expected} bytes, got {len(data)}")

    off = 24
    vertices = np.frombuffer(data, dtype="<f4", count=vertex_count * 3, offset=off).copy().reshape((-1, 3))
    off += vertex_count * 12
    indices = np.frombuffer(data, dtype="<u4", count=index_count, offset=off).copy()
    off += index_count * 4

    triangles: List[CollisionTriangleMeta] = []
    for _ in range(triangle_count):
        role_id, source_id, flags = struct.unpack_from("<III", data, off)
        triangles.append(CollisionTriangleMeta(role_id, source_id, flags))
        off += 12

    if index_count != triangle_count * 3:
        raise ValueError("MM9COLL index count must equal triangle_count * 3")
    if index_count and int(indices.max()) >= vertex_count:
        raise ValueError("MM9COLL index references a missing vertex")

    manifest_section = _manifest_collision_section(manifest_path)
    return CollisionSidecar(
        path=os.path.abspath(path),
        vertices=vertices,
        indices=indices,
        triangles=triangles,
        source_models=_source_models_from_manifest(manifest_section),
        includes_render_floors=bool(manifest_section.get("includesRenderFloors", False)),
        render_floor_triangles=int(manifest_section.get("renderFloorTriangles", 0) or 0),
    )
