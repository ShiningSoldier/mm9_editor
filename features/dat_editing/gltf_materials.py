"""Material, DTX-dimension, and UV/OPQ conversion for glTF -> ED.

The converter implements the Phase-5 precedence and fallback contract without
writing files or creating Brushes.  It can read dimensions from caller-supplied
DTX bytes, use an explicit lookup, or use an explicitly configured fallback.
"""

from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from features.dat_editing import geometry_scene
from features.dat_editing import mesh_topology
from features.dat_editing import terrain_semantics
from features.dat_editing import uv_projection


Vec2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]
TextureSizeLookup = Callable[[str], Optional[Sequence[float]]]
TextureBytesLookup = Callable[[str], Optional[bytes]]

WORLD_ALIGNED_PROJECTION = "world_aligned"
DTX_HEADER_SIZE = 164


@dataclass(frozen=True)
class MaterialDiagnostic:
    severity: str
    code: str
    message: str
    component_id: str = ""
    source_face_index: Optional[int] = None
    material_name: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "component_id": self.component_id or None,
            "source_face_index": self.source_face_index,
            "material_name": self.material_name or None,
        }


@dataclass(frozen=True)
class DtxTextureInfo:
    source: str
    version: int
    width: int
    height: int
    mip_count: int
    section_count: int
    pixel_format: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "source": self.source,
            "version": self.version,
            "width": self.width,
            "height": self.height,
            "mip_count": self.mip_count,
            "section_count": self.section_count,
            "pixel_format": self.pixel_format,
        }


@dataclass(frozen=True)
class ResolvedTexture:
    material_name: str
    texture_name: str
    resolution_source: str
    width: float
    height: float
    dimension_source: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "material_name": self.material_name,
            "texture_name": self.texture_name,
            "resolution_source": self.resolution_source,
            "width": self.width,
            "height": self.height,
            "dimension_source": self.dimension_source,
        }


@dataclass(frozen=True)
class ResolvedSurfaceProjection:
    texture: ResolvedTexture
    uv_o: Vec3
    uv_p: Vec3
    uv_q: Vec3
    uv_method: str
    texture_flags: int
    surface_flags: int
    diagnostics: Tuple[MaterialDiagnostic, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "texture": self.texture.to_dict(),
            "uv_o": list(self.uv_o),
            "uv_p": list(self.uv_p),
            "uv_q": list(self.uv_q),
            "uv_method": self.uv_method,
            "texture_flags": self.texture_flags,
            "surface_flags": self.surface_flags,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True)
class MaterialConversionSummary:
    material_name: str
    source_material_index: Optional[int]
    source_material_name: str
    resolved_texture_name: Optional[str]
    resolution_source: Optional[str]
    texture_width: Optional[float]
    texture_height: Optional[float]
    dimension_source: Optional[str]
    surface_count: int
    uv_method_counts: Dict[str, int]
    diagnostics: Tuple[MaterialDiagnostic, ...]

    @property
    def blockers(self) -> Tuple[MaterialDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "blocker")

    @property
    def cautions(self) -> Tuple[MaterialDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "caution")

    @property
    def notes(self) -> Tuple[MaterialDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "note")

    def to_dict(self) -> Dict[str, object]:
        return {
            "material_name": self.material_name,
            "source_material_index": self.source_material_index,
            "source_material_name": self.source_material_name,
            "resolved_texture_name": self.resolved_texture_name,
            "resolution_source": self.resolution_source,
            "texture_width": self.texture_width,
            "texture_height": self.texture_height,
            "dimension_source": self.dimension_source,
            "surface_count": self.surface_count,
            "uv_method_counts": dict(self.uv_method_counts),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True)
class MaterialUvReport:
    status: str
    materials: Tuple[MaterialConversionSummary, ...]
    generated_surface_count: int
    generated_uv_method_counts: Dict[str, int]
    diagnostics: Tuple[MaterialDiagnostic, ...]

    @property
    def blockers(self) -> Tuple[MaterialDiagnostic, ...]:
        result = [item for item in self.diagnostics if item.severity == "blocker"]
        for material in self.materials:
            result.extend(material.blockers)
        return tuple(result)

    @property
    def cautions(self) -> Tuple[MaterialDiagnostic, ...]:
        result = [item for item in self.diagnostics if item.severity == "caution"]
        for material in self.materials:
            result.extend(material.cautions)
        return tuple(result)

    @property
    def notes(self) -> Tuple[MaterialDiagnostic, ...]:
        result = [item for item in self.diagnostics if item.severity == "note"]
        for material in self.materials:
            result.extend(material.notes)
        return tuple(result)

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "materials": [item.to_dict() for item in self.materials],
            "generated_surface_count": self.generated_surface_count,
            "generated_uv_method_counts": dict(self.generated_uv_method_counts),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "blockers": [item.to_dict() for item in self.blockers],
            "cautions": [item.to_dict() for item in self.cautions],
            "notes": [item.to_dict() for item in self.notes],
        }


@dataclass
class _MaterialUsage:
    material_name: str
    source_material_index: Optional[int]
    source_material_name: str
    resolved_texture: Optional[ResolvedTexture] = None
    surface_count: int = 0
    uv_method_counts: Dict[str, int] = field(default_factory=dict)
    diagnostics: List[MaterialDiagnostic] = field(default_factory=list)


def parse_dtx_texture_info(data: bytes, *, source: str = "") -> DtxTextureInfo:
    """Read the stable LithTech DTX header fields needed for UV conversion."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("DTX data must be bytes-like")
    payload = bytes(data)
    if len(payload) < DTX_HEADER_SIZE:
        raise ValueError(f"DTX data is {len(payload)} bytes; expected at least {DTX_HEADER_SIZE}")
    version = struct.unpack_from("<i", payload, 4)[0]
    width, height, mip_count, section_count = struct.unpack_from("<4H", payload, 8)
    pixel_format = struct.unpack_from("<H", payload, 26)[0]
    if width <= 0 or height <= 0:
        raise ValueError(f"DTX dimensions must be positive, got {width}x{height}")
    if mip_count <= 0:
        raise ValueError(f"DTX mip count must be positive, got {mip_count}")
    return DtxTextureInfo(
        source=str(source),
        version=version,
        width=width,
        height=height,
        mip_count=mip_count,
        section_count=section_count,
        pixel_format=pixel_format,
    )


def load_dtx_texture_info(path: str) -> DtxTextureInfo:
    absolute = os.path.abspath(os.fspath(path))
    with open(absolute, "rb") as source_file:
        return parse_dtx_texture_info(source_file.read(DTX_HEADER_SIZE), source=absolute)


def validate_dtx_texture_path(value: object) -> str:
    """Return a normalized ED texture string or raise ``ValueError``."""
    reason = _invalid_texture_reason(value)
    if reason:
        raise ValueError(reason)
    return str(value).strip()


class MaterialUvConverter:
    """Resolve source materials and build DEDit-compatible OPQ projections."""

    def __init__(
        self,
        scene: geometry_scene.GeometryScene,
        *,
        material_map: Optional[Mapping[str, str]] = None,
        fallback_texture: Optional[str] = None,
        texture_dimensions: Optional[Mapping[str, Sequence[float]]] = None,
        texture_size_lookup: Optional[TextureSizeLookup] = None,
        texture_bytes_lookup: Optional[TextureBytesLookup] = None,
        fallback_texture_size: Optional[Sequence[float]] = None,
        default_uv_projection: Optional[str] = None,
    ) -> None:
        if not isinstance(scene, geometry_scene.GeometryScene):
            raise TypeError("scene must be a GeometryScene")
        if material_map is not None and not isinstance(material_map, Mapping):
            raise TypeError("material_map must be a mapping")
        if texture_dimensions is not None and not isinstance(texture_dimensions, Mapping):
            raise TypeError("texture_dimensions must be a mapping")
        if texture_size_lookup is not None and not callable(texture_size_lookup):
            raise TypeError("texture_size_lookup must be callable")
        if texture_bytes_lookup is not None and not callable(texture_bytes_lookup):
            raise TypeError("texture_bytes_lookup must be callable")
        projection = None if default_uv_projection is None else str(default_uv_projection)
        if projection not in {None, WORLD_ALIGNED_PROJECTION}:
            raise ValueError("default_uv_projection must be None or 'world_aligned'")

        self.scene = scene
        self.material_map = dict(material_map or {})
        self.fallback_texture = (
            validate_dtx_texture_path(fallback_texture)
            if fallback_texture is not None
            else None
        )
        self.texture_dimensions = dict(texture_dimensions or {})
        self.texture_size_lookup = texture_size_lookup
        self.texture_bytes_lookup = texture_bytes_lookup
        self.fallback_texture_size = (
            None
            if fallback_texture_size is None
            else _texture_size("fallback_texture_size", fallback_texture_size)
        )
        self.default_uv_projection = projection
        self._materials = _material_lookup(scene.materials)
        self._texture_cache: Dict[
            Tuple[str, str],
            Tuple[Optional[ResolvedTexture], Tuple[MaterialDiagnostic, ...]],
        ] = {}
        self._usage: Dict[str, _MaterialUsage] = {}
        self._usage_order: List[str] = []
        self._generated_surface_count = 0
        self._generated_uv_methods: Dict[str, int] = {}
        self._diagnostics: List[MaterialDiagnostic] = []

    def resolve_face(
        self,
        component: mesh_topology.TopologyComponent,
        face: mesh_topology.TopologyFace,
    ) -> Tuple[Optional[ResolvedSurfaceProjection], Tuple[MaterialDiagnostic, ...]]:
        texture, texture_diagnostics = self.resolve_material_texture(
            face.material_name,
            component_id=component.component_id,
            source_face_index=face.source_face_index,
        )
        diagnostics = list(texture_diagnostics)
        uv_method = ""
        projection = None
        if texture is not None:
            face_points = tuple(component.points[index] for index in face.vertex_indices)
            normal = _plane_normal(face_points)
            usable_uvs = all(value is not None for value in face.uv_coords)
            if usable_uvs:
                projection = uv_projection.dedit_uv_to_opq(
                    face_points,
                    tuple(value for value in face.uv_coords if value is not None),
                    tex_width=texture.width,
                    tex_height=texture.height,
                )
                if projection is not None:
                    uv_method = "dedit_uv_to_opq"
            if projection is None and self.default_uv_projection == WORLD_ALIGNED_PROJECTION:
                projection = _world_aligned_opq(
                    face_points,
                    normal,
                    texture.width,
                    texture.height,
                )
                uv_method = WORLD_ALIGNED_PROJECTION
                reason = "missing" if not usable_uvs else "degenerate"
                diagnostics.append(_diagnostic(
                    "caution",
                    "default_uv_projection",
                    f"face {face.source_face_index} used the explicit world-aligned projection "
                    f"because source UVs were {reason}",
                    component_id=component.component_id,
                    source_face_index=face.source_face_index,
                    material_name=face.material_name,
                ))
            if projection is None:
                code = "missing_uv_projection" if not usable_uvs else "degenerate_uv_projection"
                diagnostics.append(_diagnostic(
                    "blocker",
                    code,
                    f"face {face.source_face_index} cannot produce OPQ without an explicit default projection",
                    component_id=component.component_id,
                    source_face_index=face.source_face_index,
                    material_name=face.material_name,
                ))

        diagnostics = list(_unique_diagnostics(diagnostics))
        self._record_material_use(face.material_name, texture, uv_method, diagnostics)
        if texture is None or projection is None or any(item.severity == "blocker" for item in diagnostics):
            return None, tuple(diagnostics)
        resolved = ResolvedSurfaceProjection(
            texture=texture,
            uv_o=projection[0],
            uv_p=projection[1],
            uv_q=projection[2],
            uv_method=uv_method,
            texture_flags=_texture_flags(texture.texture_name),
            surface_flags=0,
            diagnostics=tuple(diagnostics),
        )
        return resolved, tuple(diagnostics)

    def resolve_generated_surface(
        self,
        points: Sequence[Vec3],
        texture_name: str,
        *,
        resolution_source: str,
        component_id: str,
        source_face_index: Optional[int],
    ) -> Tuple[Optional[ResolvedSurfaceProjection], Tuple[MaterialDiagnostic, ...]]:
        texture, diagnostics = self.resolve_named_texture(
            texture_name,
            resolution_source=resolution_source,
            component_id=component_id,
            source_face_index=source_face_index,
        )
        if texture is None:
            self._diagnostics.extend(diagnostics)
            return None, diagnostics
        normal = _plane_normal(points)
        projection = _world_aligned_opq(points, normal, texture.width, texture.height)
        result_diagnostics = list(diagnostics)
        if projection is None:
            result_diagnostics.append(_diagnostic(
                "blocker",
                "generated_projection_failed",
                "generated surface could not produce a stable world-aligned projection",
                component_id=component_id,
                source_face_index=source_face_index,
            ))
        result_diagnostics = list(_unique_diagnostics(result_diagnostics))
        self._diagnostics.extend(result_diagnostics)
        if projection is None or any(item.severity == "blocker" for item in result_diagnostics):
            return None, tuple(result_diagnostics)
        self._generated_surface_count += 1
        self._generated_uv_methods[WORLD_ALIGNED_PROJECTION] = (
            self._generated_uv_methods.get(WORLD_ALIGNED_PROJECTION, 0) + 1
        )
        resolved = ResolvedSurfaceProjection(
            texture=texture,
            uv_o=projection[0],
            uv_p=projection[1],
            uv_q=projection[2],
            uv_method=WORLD_ALIGNED_PROJECTION,
            texture_flags=_texture_flags(texture.texture_name),
            surface_flags=0,
            diagnostics=tuple(result_diagnostics),
        )
        return resolved, tuple(result_diagnostics)

    def resolve_material_texture(
        self,
        material_name: str,
        *,
        component_id: str = "",
        source_face_index: Optional[int] = None,
    ) -> Tuple[Optional[ResolvedTexture], Tuple[MaterialDiagnostic, ...]]:
        material = self._materials.get(str(material_name).casefold())
        texture_name = ""
        resolution_source = ""
        extras = material.extras if material is not None and isinstance(material.extras, dict) else {}
        gltf_extras = extras.get("gltf_extras") if isinstance(extras.get("gltf_extras"), dict) else {}
        extras_texture = gltf_extras.get("MM9_texture")
        if isinstance(extras_texture, str) and extras_texture.strip():
            texture_name = extras_texture.strip()
            resolution_source = "extras"
        else:
            mapped = _mapping_value(self.material_map, material_name)
            if mapped is None and material is not None:
                mapped = _mapping_value(self.material_map, material.name)
            if mapped is None and material is not None:
                source_name = extras.get("source_name")
                if isinstance(source_name, str):
                    mapped = _mapping_value(self.material_map, source_name)
            if mapped is not None:
                texture_name = str(mapped).strip()
                resolution_source = "material_map"
            elif str(material_name).strip().lower().endswith(".dtx"):
                texture_name = str(material_name).strip()
                resolution_source = "material_name"
            elif material is not None and str(material.texture_name).strip().lower().endswith(".dtx"):
                texture_name = str(material.texture_name).strip()
                resolution_source = str(extras.get("resolution_source") or "scene_material")
            elif self.fallback_texture is not None:
                texture_name = self.fallback_texture
                resolution_source = "fallback"

        if not texture_name:
            return None, (_diagnostic(
                "blocker",
                "unresolved_material_texture",
                f"material {material_name!r} has no resolved DTX texture",
                component_id=component_id,
                source_face_index=source_face_index,
                material_name=material_name,
            ),)
        texture, diagnostics = self.resolve_named_texture(
            texture_name,
            resolution_source=resolution_source,
            component_id=component_id,
            source_face_index=source_face_index,
            material_name=material_name,
        )
        result = list(diagnostics)
        if texture is not None and resolution_source == "fallback":
            result.append(_diagnostic(
                "caution",
                "fallback_material_texture",
                f"material {material_name!r} uses explicitly configured fallback {texture_name!r}",
                component_id=component_id,
                source_face_index=source_face_index,
                material_name=material_name,
            ))
        return texture, tuple(result)

    def resolve_named_texture(
        self,
        texture_name: str,
        *,
        resolution_source: str,
        component_id: str = "",
        source_face_index: Optional[int] = None,
        material_name: str = "",
    ) -> Tuple[Optional[ResolvedTexture], Tuple[MaterialDiagnostic, ...]]:
        raw_name = str(texture_name)
        cache_key = (raw_name.casefold(), str(resolution_source))
        cached = self._texture_cache.get(cache_key)
        if cached is None:
            cached = self._resolve_named_texture_uncached(
                raw_name,
                resolution_source=str(resolution_source),
                material_name=str(material_name),
            )
            self._texture_cache[cache_key] = cached
        texture, cached_diagnostics = cached
        diagnostics = tuple(
            _relocate_diagnostic(
                item,
                component_id=component_id,
                source_face_index=source_face_index,
                material_name=material_name,
            )
            for item in cached_diagnostics
        )
        if texture is not None and texture.material_name != str(material_name):
            texture = ResolvedTexture(
                material_name=str(material_name),
                texture_name=texture.texture_name,
                resolution_source=texture.resolution_source,
                width=texture.width,
                height=texture.height,
                dimension_source=texture.dimension_source,
            )
        return texture, diagnostics

    def report(self) -> MaterialUvReport:
        summaries: List[MaterialConversionSummary] = []
        for key in self._usage_order:
            usage = self._usage[key]
            texture = usage.resolved_texture
            summaries.append(MaterialConversionSummary(
                material_name=usage.material_name,
                source_material_index=usage.source_material_index,
                source_material_name=usage.source_material_name,
                resolved_texture_name=texture.texture_name if texture is not None else None,
                resolution_source=texture.resolution_source if texture is not None else None,
                texture_width=texture.width if texture is not None else None,
                texture_height=texture.height if texture is not None else None,
                dimension_source=texture.dimension_source if texture is not None else None,
                surface_count=usage.surface_count,
                uv_method_counts=dict(sorted(usage.uv_method_counts.items())),
                diagnostics=_unique_diagnostics(usage.diagnostics),
            ))
        report_diagnostics = _unique_diagnostics(self._diagnostics)
        blockers = [item for item in report_diagnostics if item.severity == "blocker"]
        blockers.extend(
            diagnostic
            for summary in summaries
            for diagnostic in summary.blockers
        )
        return MaterialUvReport(
            status="blocked" if blockers else "ready",
            materials=tuple(summaries),
            generated_surface_count=self._generated_surface_count,
            generated_uv_method_counts=dict(sorted(self._generated_uv_methods.items())),
            diagnostics=report_diagnostics,
        )

    def _resolve_named_texture_uncached(
        self,
        texture_name: str,
        *,
        resolution_source: str,
        material_name: str,
    ) -> Tuple[Optional[ResolvedTexture], Tuple[MaterialDiagnostic, ...]]:
        reason = _invalid_texture_reason(texture_name)
        if reason:
            return None, (_diagnostic(
                "blocker",
                "invalid_texture_path",
                f"texture {texture_name!r} {reason}",
                material_name=material_name,
            ),)
        texture_name = texture_name.strip()
        diagnostics: List[MaterialDiagnostic] = []
        explicit_size = _mapping_value(self.texture_dimensions, texture_name)
        if explicit_size is not None:
            try:
                size = _texture_size("texture_dimensions", explicit_size)
            except ValueError as exc:
                return None, (_diagnostic(
                    "blocker",
                    "invalid_texture_dimensions",
                    f"texture {texture_name!r}: {exc}",
                    material_name=material_name,
                ),)
            dimension_source = "texture_dimensions"
        else:
            size, dimension_source, lookup_diagnostics = self._lookup_texture_size(texture_name)
            diagnostics.extend(lookup_diagnostics)
            if size is None and self.fallback_texture_size is not None:
                size = self.fallback_texture_size
                dimension_source = "fallback"
                diagnostics.append(_diagnostic(
                    "caution",
                    "fallback_texture_dimensions",
                    f"texture {texture_name!r} uses configured {size[0]:g}x{size[1]:g} fallback dimensions",
                    material_name=material_name,
                ))
            elif size is None:
                diagnostics.append(_diagnostic(
                    "blocker",
                    "missing_texture_dimensions",
                    f"texture {texture_name!r} has no dimensions and no explicit fallback size",
                    material_name=material_name,
                ))
        if size is None or any(item.severity == "blocker" for item in diagnostics):
            return None, tuple(diagnostics)
        return ResolvedTexture(
            material_name=material_name,
            texture_name=texture_name,
            resolution_source=resolution_source,
            width=size[0],
            height=size[1],
            dimension_source=dimension_source,
        ), tuple(diagnostics)

    def _lookup_texture_size(
        self,
        texture_name: str,
    ) -> Tuple[Optional[Tuple[float, float]], str, Tuple[MaterialDiagnostic, ...]]:
        diagnostics: List[MaterialDiagnostic] = []
        if self.texture_size_lookup is not None:
            try:
                value = self.texture_size_lookup(texture_name)
                if value is not None:
                    return _texture_size("texture_size_lookup result", value), "texture_size_lookup", ()
            except Exception as exc:
                diagnostics.append(_diagnostic(
                    "caution",
                    "texture_size_lookup_failed",
                    f"texture size lookup failed for {texture_name!r}: {exc}",
                ))
        if self.texture_bytes_lookup is not None:
            try:
                payload = self.texture_bytes_lookup(texture_name)
            except (FileNotFoundError, KeyError):
                payload = None
            except Exception as exc:
                diagnostics.append(_diagnostic(
                    "caution",
                    "texture_bytes_lookup_failed",
                    f"texture byte lookup failed for {texture_name!r}: {exc}",
                ))
                payload = None
            if payload is not None:
                try:
                    info = parse_dtx_texture_info(payload, source=texture_name)
                except (TypeError, ValueError) as exc:
                    diagnostics.append(_diagnostic(
                        "blocker",
                        "invalid_dtx_header",
                        f"texture {texture_name!r} has an invalid DTX header: {exc}",
                    ))
                    return None, "", tuple(diagnostics)
                return (float(info.width), float(info.height)), "dtx_header", tuple(diagnostics)
        return None, "", tuple(diagnostics)

    def _record_material_use(
        self,
        material_name: str,
        texture: Optional[ResolvedTexture],
        uv_method: str,
        diagnostics: Sequence[MaterialDiagnostic],
    ) -> None:
        key = str(material_name).casefold()
        usage = self._usage.get(key)
        if usage is None:
            material = self._materials.get(key)
            extras = material.extras if material is not None and isinstance(material.extras, dict) else {}
            usage = _MaterialUsage(
                material_name=str(material_name),
                source_material_index=_optional_int(extras.get("source_index")),
                source_material_name=str(extras.get("source_name") or (material.name if material else "")),
            )
            ignored_pbr_fields = extras.get("ignored_pbr_fields")
            if isinstance(ignored_pbr_fields, (list, tuple)) and ignored_pbr_fields:
                fields = ", ".join(str(value) for value in ignored_pbr_fields)
                usage.diagnostics.append(_diagnostic(
                    "note",
                    "ignored_gltf_pbr_fields",
                    f"material {material_name!r} ignores glTF PBR field(s): {fields}",
                    material_name=material_name,
                ))
            self._usage[key] = usage
            self._usage_order.append(key)
        usage.surface_count += 1
        if texture is not None:
            usage.resolved_texture = texture
        if uv_method:
            usage.uv_method_counts[uv_method] = usage.uv_method_counts.get(uv_method, 0) + 1
        usage.diagnostics.extend(diagnostics)


def _world_aligned_opq(
    points: Sequence[Vec3],
    normal: Vec3,
    texture_width: float,
    texture_height: float,
) -> Optional[Tuple[Vec3, Vec3, Vec3]]:
    if len(points) < 3:
        return None
    world_axes: Tuple[Vec3, Vec3, Vec3] = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    reference_axis = min(world_axes, key=lambda axis: abs(_dot(normal, axis)))
    tangent = _cross(reference_axis, normal)
    tangent_length = math.sqrt(_dot(tangent, tangent))
    if tangent_length <= 1.0e-8:
        return None
    tangent = _scale(tangent, 1.0 / tangent_length)
    bitangent = _cross(normal, tangent)
    bitangent_length = math.sqrt(_dot(bitangent, bitangent))
    if bitangent_length <= 1.0e-8:
        return None
    bitangent = _scale(bitangent, 1.0 / bitangent_length)
    origin = points[0]
    projected: List[Vec2] = []
    for point in points[:3]:
        delta = _subtract(point, origin)
        projected.append((_dot(delta, tangent), -_dot(delta, bitangent)))
    u_span = max(value[0] for value in projected) - min(value[0] for value in projected)
    v_span = max(value[1] for value in projected) - min(value[1] for value in projected)
    if u_span <= 1.0e-8 or v_span <= 1.0e-8:
        return None
    uvs = tuple((value[0] / u_span, value[1] / v_span) for value in projected)
    result = uv_projection.dedit_uv_to_opq(
        points[:3],
        uvs,
        tex_width=texture_width,
        tex_height=texture_height,
    )
    if result is not None:
        return result
    return uv_projection.dedit_uv_to_opq(
        points[:3],
        ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),
        tex_width=texture_width,
        tex_height=texture_height,
    )


def _material_lookup(
    materials: Sequence[geometry_scene.GeometryMaterial],
) -> Dict[str, geometry_scene.GeometryMaterial]:
    result: Dict[str, geometry_scene.GeometryMaterial] = {}
    for material in materials:
        result.setdefault(str(material.name).casefold(), material)
    return result


def _mapping_value(mapping: Mapping[str, object], key: str) -> Optional[object]:
    if key in mapping:
        return mapping[key]
    wanted = str(key).casefold()
    for candidate, value in mapping.items():
        if str(candidate).casefold() == wanted:
            return value
    return None


def _texture_size(name: str, value: object) -> Tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must contain width and height")
    width = _positive_finite(f"{name} width", value[0])
    height = _positive_finite(f"{name} height", value[1])
    return width, height


def _invalid_texture_reason(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty DTX path"
    text = value.strip()
    if not text.lower().endswith(".dtx"):
        return "must end with .dtx"
    if any(ord(char) < 32 for char in text):
        return "contains control characters"
    try:
        encoded = text.encode("latin1")
    except UnicodeEncodeError:
        return "must be Latin-1 encodable"
    if len(encoded) > 512:
        return "exceeds the legacy ED 512-byte texture-path limit"
    return ""


def _texture_flags(texture_name: str) -> int:
    return 1 if terrain_semantics.helper_texture_role(texture_name) else 0


def _plane_normal(points: Sequence[Vec3]) -> Vec3:
    if len(points) < 3:
        raise ValueError("surface requires at least three points")
    first = points[0]
    for offset in range(1, len(points) - 1):
        normal = _cross(_subtract(points[offset], first), _subtract(points[offset + 1], first))
        length = math.sqrt(_dot(normal, normal))
        if length > 1.0e-8:
            return _scale(normal, 1.0 / length)
    raise ValueError("surface has no stable plane")


def _relocate_diagnostic(
    diagnostic: MaterialDiagnostic,
    *,
    component_id: str,
    source_face_index: Optional[int],
    material_name: str,
) -> MaterialDiagnostic:
    return MaterialDiagnostic(
        severity=diagnostic.severity,
        code=diagnostic.code,
        message=diagnostic.message,
        component_id=component_id,
        source_face_index=source_face_index,
        material_name=material_name or diagnostic.material_name,
    )


def _diagnostic(
    severity: str,
    code: str,
    message: str,
    *,
    component_id: str = "",
    source_face_index: Optional[int] = None,
    material_name: str = "",
) -> MaterialDiagnostic:
    return MaterialDiagnostic(
        severity=severity,
        code=code,
        message=message,
        component_id=component_id,
        source_face_index=source_face_index,
        material_name=material_name,
    )


def _unique_diagnostics(values: Sequence[MaterialDiagnostic]) -> Tuple[MaterialDiagnostic, ...]:
    result: List[MaterialDiagnostic] = []
    seen = set()
    for item in values:
        key = (
            item.severity,
            item.code,
            item.message,
            item.component_id,
            item.source_face_index,
            item.material_name,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


def _optional_int(value: object) -> Optional[int]:
    return int(value) if type(value) is int else None


def _positive_finite(name: str, value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite positive number") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be a finite positive number")
    return result


def _subtract(a: Vec3, b: Vec3) -> Vec3:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _scale(value: Vec3, factor: float) -> Vec3:
    return value[0] * factor, value[1] * factor, value[2] * factor


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )
