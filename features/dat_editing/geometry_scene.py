"""Format-neutral geometry scene model for DAT mesh imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


Vec2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]


@dataclass
class GeometryMaterial:
    name: str
    texture_name: str = ""
    extras: Dict[str, object] = field(default_factory=dict)


@dataclass
class GeometryFace:
    vertex_indices: List[int]
    material_name: str
    uv_coords: List[Optional[Vec2]] = field(default_factory=list)
    extras: Dict[str, object] = field(default_factory=dict)


@dataclass
class GeometryModel:
    name: str
    points: List[Vec3] = field(default_factory=list)
    faces: List[GeometryFace] = field(default_factory=list)
    extras: Dict[str, object] = field(default_factory=dict)


@dataclass
class GeometryScene:
    source_path: str
    models: List[GeometryModel] = field(default_factory=list)
    materials: List[GeometryMaterial] = field(default_factory=list)
    metadata: Dict[str, object] = field(default_factory=dict)

    def mesh_models(self) -> List[GeometryModel]:
        return [model for model in self.models if model.faces]

    def material_texture_map(self) -> Dict[str, str]:
        return {
            material.name: material.texture_name or material.name or "Default"
            for material in self.materials
        }
