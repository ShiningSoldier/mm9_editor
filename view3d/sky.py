"""Resolve LithTech sky objects into a renderer-friendly scene description."""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Iterable, Optional, Sequence, Tuple

import numpy as np

from core import bsp


_DEFAULT_INNER_PERCENT = 0.1
_SOFT_SKY_FALLBACK = r"TEXTURES\Skybox\Clouds1.dtx"


def _property(obj, name: str, default=None):
    getter = getattr(obj, "get", None)
    if callable(getter):
        return getter(name, default)
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default


def _type_name(obj) -> str:
    return str(getattr(obj, "type_str", "") or _property(obj, "type_str", ""))


def _vector3(value, default=(0.0, 0.0, 0.0)) -> Tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return tuple(float(v) for v in default)
    try:
        result = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return tuple(float(v) for v in default)
    if not all(math.isfinite(v) for v in result):
        return tuple(float(v) for v in default)
    return result


def _number(obj, name: str, default: float = 0.0) -> float:
    value = _property(obj, name, default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)

    # MM9 stores several numeric properties as IEEE-754 bits in a LongInt
    # property. SkyPointer.Index is the common sky example (5.0 -> 0x40a00000).
    if isinstance(value, int) and value > 0x00FFFFFF:
        decoded = struct.unpack("<f", struct.pack("<I", value & 0xFFFFFFFF))[0]
        if math.isfinite(decoded):
            number = float(decoded)
    return number if math.isfinite(number) else float(default)


def _visible(obj) -> bool:
    value = _property(obj, "Visible", 1)
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return bool(value)


@dataclass(frozen=True)
class SkyLayer:
    model_name: str
    index: float
    source_class: str
    source_name: str


@dataclass(frozen=True)
class SkyScene:
    layers: Tuple[SkyLayer, ...]
    definition_center: Tuple[float, float, float]
    definition_dims: Tuple[float, float, float]
    view_min: Tuple[float, float, float]
    view_max: Tuple[float, float, float]
    soft_sky_texture: str = ""
    all_sky_portals: bool = False

    def view_position(
        self,
        camera_game_position: Sequence[float],
        world_min: Sequence[float],
        world_max: Sequence[float],
    ) -> Tuple[float, float, float]:
        """Map the main camera into the inner sky box like LithTech does."""
        camera = np.asarray(camera_game_position, dtype=np.float64)
        lo = np.asarray(world_min, dtype=np.float64)
        hi = np.asarray(world_max, dtype=np.float64)
        span = hi - lo
        percentages = np.full(3, 0.5, dtype=np.float64)
        valid = np.abs(span) > 1.0e-9
        percentages[valid] = (camera[valid] - lo[valid]) / span[valid]

        view_lo = np.asarray(self.view_min, dtype=np.float64)
        view_hi = np.asarray(self.view_max, dtype=np.float64)
        position = view_lo + (view_hi - view_lo) * percentages
        return tuple(float(v) for v in position)

    @property
    def far_distance(self) -> float:
        dims = np.asarray(self.definition_dims, dtype=np.float64)
        return max(float(np.linalg.norm(dims)) * 8.0, 1024.0)


def resolve_sky_scene(objects: Iterable[object]) -> Optional[SkyScene]:
    """Resolve DemoSkyWorldModel/SkyPointer records and WorldProperties."""
    records = list(objects or ())
    named = {
        str(_property(obj, "Name", "") or "").casefold(): obj
        for obj in records
        if _property(obj, "Name", "")
    }

    layers = []
    definition = None
    soft_sky = ""
    all_portals = False

    for order, obj in enumerate(records):
        class_name = _type_name(obj)
        folded_class = class_name.casefold()

        if folded_class == "worldproperties":
            soft_sky = str(_property(obj, "SoftSky", "") or "")
            all_portals = bool(_property(obj, "AllSkyPortals", 0))
            continue

        if folded_class not in {"demoskyworldmodel", "skypointer"}:
            continue

        center = _vector3(_property(obj, "Pos", (0.0, 0.0, 0.0)))
        dims = tuple(abs(v) for v in _vector3(_property(obj, "SkyDims", (0.0, 0.0, 0.0))))
        if all(v > 1.0e-6 for v in dims):
            inner = (
                max(0.0, abs(_number(obj, "InnerPercentX", _DEFAULT_INNER_PERCENT))),
                max(0.0, abs(_number(obj, "InnerPercentY", _DEFAULT_INNER_PERCENT))),
                max(0.0, abs(_number(obj, "InnerPercentZ", _DEFAULT_INNER_PERCENT))),
            )
            inner_dims = tuple(dims[i] * inner[i] for i in range(3))
            definition = (
                center,
                dims,
                tuple(center[i] - inner_dims[i] for i in range(3)),
                tuple(center[i] + inner_dims[i] for i in range(3)),
            )

        source_name = str(_property(obj, "Name", "") or "")
        if folded_class == "demoskyworldmodel":
            model_name = source_name
            target = obj
        else:
            model_name = str(_property(obj, "SkyObjectName", "") or "")
            target = named.get(model_name.casefold())

        if not model_name or (target is not None and not _visible(target)):
            continue
        layers.append((
            SkyLayer(
                model_name=model_name,
                index=_number(obj, "Index", 0.0),
                source_class=class_name,
                source_name=source_name,
            ),
            order,
        ))

    if not layers:
        return None

    layers.sort(key=lambda entry: (entry[0].index, entry[1]))
    ordered_layers = tuple(entry[0] for entry in layers)

    if definition is None:
        # A constant sky viewpoint is still useful for older/simple levels
        # that add a sky model without setting SkyDims.
        source = named.get(ordered_layers[0].model_name.casefold())
        center = _vector3(_property(source, "Pos", (0.0, 0.0, 0.0)))
        dims = (128.0, 128.0, 128.0)
        definition = (center, dims, center, center)

    center, dims, view_min, view_max = definition
    return SkyScene(
        layers=ordered_layers,
        definition_center=center,
        definition_dims=dims,
        view_min=view_min,
        view_max=view_max,
        soft_sky_texture=soft_sky,
        all_sky_portals=all_portals,
    )


def resolve_soft_sky_texture(scene: SkyScene, texture_cache) -> str:
    """Resolve SoftSky, using MM9's shipped Clouds1 texture as fallback."""
    requested = str(scene.soft_sky_texture or "")
    if not requested or texture_cache is None:
        return ""
    has_texture = getattr(texture_cache, "has", None)
    if not callable(has_texture):
        return requested
    if has_texture(requested):
        return requested
    if has_texture(_SOFT_SKY_FALLBACK):
        return _SOFT_SKY_FALLBACK
    return ""


def build_soft_sky_model(scene: SkyScene, texture_name: str) -> Optional[bsp.WorldModelMesh]:
    """Build a translucent five-face cloud shell inside the authored sky box."""
    if not texture_name:
        return None
    center = np.asarray(scene.definition_center, dtype=np.float64)
    dims = np.maximum(np.asarray(scene.definition_dims, dtype=np.float64) * 0.88, 1.0)
    x0, y0, z0 = center - dims
    x1, y1, z1 = center + dims
    points = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    # Top plus four sides. OPQ axes are unit vectors, giving roughly one
    # repeat across MM9's usual 256-unit sky cube and 256px cloud texture.
    faces = [
        ([3, 2, 6, 7], (x0, y1, z0), (1, 0, 0), (0, 0, 1)),
        ([0, 1, 2, 3], (x0, y0, z0), (1, 0, 0), (0, 1, 0)),
        ([5, 4, 7, 6], (x0, y0, z1), (-1, 0, 0), (0, 1, 0)),
        ([4, 0, 3, 7], (x0, y0, z0), (0, 0, 1), (0, 1, 0)),
        ([1, 5, 6, 2], (x1, y0, z0), (0, 0, -1), (0, 1, 0)),
    ]
    surfaces = [
        bsp.Surface(
            uv_o=tuple(float(v) for v in origin),
            uv_p=tuple(float(v) for v in p_axis),
            uv_q=tuple(float(v) for v in q_axis),
            texture_index=0,
            flags=0,
            texture_flags=0,
        )
        for _indices, origin, p_axis, q_axis in faces
    ]
    polygons = [
        bsp.Polygon(list(indices), surface_index=i, plane_index=0)
        for i, (indices, _origin, _p_axis, _q_axis) in enumerate(faces)
    ]
    return bsp.WorldModelMesh(
        name="_EditorSoftSky",
        min_box=(float(x0), float(y0), float(z0)),
        max_box=(float(x1), float(y1), float(z1)),
        translation=tuple(float(v) for v in center),
        points=points,
        polygons=polygons,
        texture_names=[texture_name],
        surfaces=surfaces,
    )
