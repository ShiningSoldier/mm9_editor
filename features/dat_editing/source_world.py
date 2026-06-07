"""Read-only parser for LithTech DEdit LTA source-world geometry."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from features.dat_editing import geometry_scene


Vec3 = Tuple[float, float, float]
LtaNode = Union[str, List["LtaNode"]]


@dataclass(frozen=True)
class LtaParseError(ValueError):
    message: str
    path: str = ""
    offset: int = 0

    def __str__(self) -> str:
        loc = f"{self.path}:{self.offset}: " if self.path else ""
        return f"{loc}{self.message}"


def load_lta_geometry_scene(path: str) -> geometry_scene.GeometryScene:
    """Load DEdit `.lta` source-world brushes into a format-neutral scene."""
    if not os.path.exists(path):
        raise ValueError(f"LTA file was not found: {path}")
    if os.path.splitext(path)[1].lower() == ".ltc":
        raise ValueError("compressed .ltc source worlds are not supported yet")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return lta_text_to_geometry_scene(text, source_path=os.path.abspath(path))


def lta_text_to_geometry_scene(text: str, *, source_path: str = "") -> geometry_scene.GeometryScene:
    roots = _parse_lta(text, source_path)
    world = next((node for node in roots if _list_name(node) == "world"), None)
    if world is None:
        raise LtaParseError("LTA source world is missing top-level world node", source_path)

    header = _first_child_list(world, "header")
    version = _atom_text(_pair_value(header, "versioncode")) if header else ""
    info = _atom_text(_pair_value(header, "infostring")) if header else ""
    brush_labels = _brush_labels_by_index(_first_child_list(world, "nodehierarchy"))

    models: List[geometry_scene.GeometryModel] = []
    material_names: Dict[str, str] = {}
    polyhedron_list = _first_child_list(world, "polyhedronlist")
    for brush_index, polyhedron in enumerate(_polyhedron_nodes(polyhedron_list)):
        model = _polyhedron_to_model(polyhedron, brush_index, brush_labels.get(brush_index))
        models.append(model)
        for face in model.faces:
            if face.material_name:
                material_names[face.material_name] = face.material_name

    materials = [
        geometry_scene.GeometryMaterial(name=name, texture_name=name)
        for name in sorted(material_names)
    ]
    return geometry_scene.GeometryScene(
        source_path=os.path.abspath(source_path) if source_path else "",
        models=models,
        materials=materials,
        metadata={
            "kind": "lithtech_lta_source_world",
            "format": "lta",
            "versioncode": version,
            "infostring": info,
            "brush_count": len(models),
            "has_globalproplist": _first_child_list(world, "globalproplist") is not None,
            "has_nodehierarchy": _first_child_list(world, "nodehierarchy") is not None,
        },
    )


def _parse_lta(text: str, path: str) -> List[LtaNode]:
    parser = _LtaParser(text, path)
    nodes = parser.parse_all()
    if not nodes:
        raise LtaParseError("empty LTA file", path)
    return nodes


class _LtaParser:
    def __init__(self, text: str, path: str):
        self.text = text
        self.path = path
        self.index = 0

    def parse_all(self) -> List[LtaNode]:
        nodes: List[LtaNode] = []
        while True:
            self._skip_ws_and_comments()
            if self.index >= len(self.text):
                return nodes
            nodes.append(self._parse_node())

    def _parse_node(self) -> LtaNode:
        self._skip_ws_and_comments()
        if self.index >= len(self.text):
            raise self._error("unexpected end of file")
        ch = self.text[self.index]
        if ch == "(":
            return self._parse_list()
        if ch == '"':
            return self._parse_string()
        if ch == ")":
            raise self._error("unexpected ')'")
        return self._parse_atom()

    def _parse_list(self) -> List[LtaNode]:
        self.index += 1
        result: List[LtaNode] = []
        while True:
            self._skip_ws_and_comments()
            if self.index >= len(self.text):
                raise self._error("unterminated list")
            if self.text[self.index] == ")":
                self.index += 1
                return result
            result.append(self._parse_node())

    def _parse_string(self) -> str:
        self.index += 1
        chars: List[str] = []
        while self.index < len(self.text):
            ch = self.text[self.index]
            self.index += 1
            if ch == '"':
                return "".join(chars)
            if ch == "\\" and self.index < len(self.text):
                next_ch = self.text[self.index]
                if next_ch in {'"', "\\"}:
                    chars.append(next_ch)
                    self.index += 1
                else:
                    chars.append(ch)
            else:
                chars.append(ch)
        raise self._error("unterminated string")

    def _parse_atom(self) -> str:
        start = self.index
        while self.index < len(self.text):
            ch = self.text[self.index]
            if ch.isspace() or ch in "();":
                break
            self.index += 1
        if self.index == start:
            raise self._error(f"unexpected character {self.text[self.index]!r}")
        return self.text[start:self.index]

    def _skip_ws_and_comments(self) -> None:
        while self.index < len(self.text):
            ch = self.text[self.index]
            if ch.isspace():
                self.index += 1
                continue
            if ch == ";":
                while self.index < len(self.text) and self.text[self.index] not in "\r\n":
                    self.index += 1
                continue
            break

    def _error(self, message: str) -> LtaParseError:
        return LtaParseError(message, self.path, self.index)


def _polyhedron_nodes(polyhedron_list: Optional[List[LtaNode]]) -> Iterator[List[LtaNode]]:
    if not polyhedron_list or len(polyhedron_list) < 2:
        return
    container = polyhedron_list[1]
    if not isinstance(container, list):
        return
    for item in container:
        if _list_name(item) == "polyhedron" and isinstance(item, list):
            yield item


def _polyhedron_to_model(polyhedron: List[LtaNode], brush_index: int, label: Optional[str]) -> geometry_scene.GeometryModel:
    points_node = _first_child_list(polyhedron, "pointlist")
    polylist_node = _first_child_list(polyhedron, "polylist")
    points = _points_from_pointlist(points_node)
    name = label or f"Brush{brush_index}"
    model = geometry_scene.GeometryModel(
        name=name,
        points=points,
        extras={
            "source_format": "lta",
            "brush_index": brush_index,
            "color": _numbers_from_pair(polyhedron, "color"),
        },
    )
    for poly_index, editpoly in enumerate(_editpoly_nodes(polylist_node)):
        face = _editpoly_to_face(editpoly, poly_index)
        if face is not None:
            model.faces.append(face)
    return model


def _points_from_pointlist(pointlist: Optional[List[LtaNode]]) -> List[Vec3]:
    if not pointlist:
        return []
    points: List[Vec3] = []
    for item in pointlist[1:]:
        if not isinstance(item, list) or len(item) < 3:
            continue
        points.append((_as_float(item[0]), _as_float(item[1]), _as_float(item[2])))
    return points


def _editpoly_nodes(polylist_node: Optional[List[LtaNode]]) -> Iterator[List[LtaNode]]:
    if not polylist_node or len(polylist_node) < 2:
        return
    container = polylist_node[1]
    if not isinstance(container, list):
        return
    for item in container:
        if _list_name(item) == "editpoly" and isinstance(item, list):
            yield item


def _editpoly_to_face(editpoly: List[LtaNode], poly_index: int) -> Optional[geometry_scene.GeometryFace]:
    indices = [int(_as_float(value)) for value in _pair_values(editpoly, "f")]
    if len(indices) < 3:
        return None
    texture_info = _first_child_list(editpoly, "textureinfo")
    texture_name = _atom_text(_pair_value(texture_info, "name")) if texture_info else "Default"
    extras: Dict[str, object] = {
        "source_format": "lta",
        "polygon_index": poly_index,
        "normal": _numbers_from_pair(editpoly, "n"),
        "dist": _number_from_pair(editpoly, "dist"),
    }
    surface_flags = _atoms_from_pair(editpoly, "flags")
    if surface_flags:
        extras["surface_flags"] = surface_flags
    if texture_info:
        extras["uv_o"] = _vector_child(texture_info, 1)
        extras["uv_p"] = _vector_child(texture_info, 2)
        extras["uv_q"] = _vector_child(texture_info, 3)
    physics_material = _atom_text(_pair_value(editpoly, "physicsmaterial"))
    surface_key = _atom_text(_pair_value(editpoly, "surfacekey"))
    if physics_material:
        extras["physics_material"] = physics_material
    if surface_key:
        extras["surface_key"] = surface_key
    return geometry_scene.GeometryFace(
        vertex_indices=indices,
        material_name=texture_name or "Default",
        uv_coords=[None for _ in indices],
        extras=extras,
    )


def _brush_labels_by_index(nodehierarchy: Optional[List[LtaNode]]) -> Dict[int, str]:
    labels: Dict[int, str] = {}
    if not nodehierarchy:
        return labels
    for node in _walk_lists(nodehierarchy):
        if _list_name(node) != "worldnode":
            continue
        node_type = _atom_text(_pair_value(node, "type"))
        if node_type != "brush":
            continue
        index_value = _pair_value(node, "brushindex")
        if index_value is None:
            continue
        label = _atom_text(_pair_value(node, "label"))
        if label:
            labels[int(_as_float(index_value))] = label
    return labels


def _walk_lists(node: LtaNode) -> Iterator[List[LtaNode]]:
    if isinstance(node, list):
        yield node
        for child in node:
            yield from _walk_lists(child)


def _first_child_list(node: Optional[LtaNode], name: str) -> Optional[List[LtaNode]]:
    if not isinstance(node, list):
        return None
    for child in node:
        if _list_name(child) == name and isinstance(child, list):
            return child
    return None


def _pair_value(node: Optional[LtaNode], name: str) -> Optional[LtaNode]:
    values = _pair_values(node, name)
    return values[0] if values else None


def _pair_values(node: Optional[LtaNode], name: str) -> List[LtaNode]:
    child = _first_child_list(node, name)
    if not child:
        return []
    return child[1:]


def _numbers_from_pair(node: Optional[LtaNode], name: str) -> List[float]:
    return [_as_float(value) for value in _pair_values(node, name)]


def _atoms_from_pair(node: Optional[LtaNode], name: str) -> List[str]:
    result: List[str] = []
    for value in _pair_values(node, name):
        if isinstance(value, list):
            result.extend(_atom_text(item) for item in value if _atom_text(item))
        else:
            text = _atom_text(value)
            if text:
                result.append(text)
    return result


def _number_from_pair(node: Optional[LtaNode], name: str) -> Optional[float]:
    value = _pair_value(node, name)
    return _as_float(value) if value is not None else None


def _vector_child(node: List[LtaNode], index: int) -> Optional[List[float]]:
    if index >= len(node) or not isinstance(node[index], list):
        return None
    values = node[index]
    if len(values) < 3:
        return None
    return [_as_float(values[0]), _as_float(values[1]), _as_float(values[2])]


def _list_name(node: Optional[LtaNode]) -> str:
    if not isinstance(node, list) or not node:
        return ""
    first = node[0]
    return first if isinstance(first, str) else ""


def _atom_text(node: Optional[LtaNode]) -> str:
    return node if isinstance(node, str) else ""


def _as_float(node: LtaNode) -> float:
    if isinstance(node, list):
        raise ValueError(f"expected atom, got list {node!r}")
    return float(node)
