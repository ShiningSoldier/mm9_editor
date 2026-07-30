"""Skin and catalog-variant resolution for static ABC model conversion."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import _path_setup  # noqa: F401
from view3d import gl_object_models
from view3d.abc_loader import AbcModel


@dataclass(frozen=True)
class ResolvedPieceSkin:
    piece_name: str
    piece_index: int
    skin_name: str
    skin_path: str
    inferred: bool


@dataclass(frozen=True)
class SkinResolutionResult:
    model_name: str
    skins_root: str
    pieces: List[ResolvedPieceSkin]
    warnings: List[str]


@dataclass(frozen=True)
class CatalogSkinVariant:
    name: str
    skin_paths: Tuple[str, ...]
    source_keys: Tuple[str, ...] = ()


class _SkinIndex:
    def __init__(self, root: str = "") -> None:
        self.root = os.path.abspath(root) if root else ""
        self._paths: Dict[str, str] = {}
        if self.root and os.path.isdir(self.root):
            for current, _dirs, names in os.walk(self.root):
                for name in names:
                    if not name.lower().endswith(".dtx"):
                        continue
                    path = os.path.abspath(os.path.join(current, name))
                    relative = os.path.relpath(path, self.root)
                    for key in _lookup_keys(relative):
                        self._paths.setdefault(key, path)

    def has(self, skin_name: str) -> bool:
        return bool(self.resolve(skin_name))

    def resolve(self, skin_name: str) -> str:
        value = str(skin_name or "").strip().strip('"')
        if os.path.isfile(value):
            return os.path.abspath(value)
        for key in _lookup_keys(value):
            path = self._paths.get(key)
            if path:
                return path
        return ""


def resolve_model_skins(
    model: AbcModel,
    skins_root: str = "",
    *,
    explicit_skins: Optional[Sequence[str]] = None,
    object_type: str = "",
    appearance_key: str = "",
    broadcast_skin: bool = False,
) -> SkinResolutionResult:
    """Resolve one DTX path per LOD0 ABC piece."""
    if skins_root and not os.path.isdir(skins_root):
        raise ValueError(f"skins root was not found: {skins_root}")
    index = _SkinIndex(skins_root)
    explicit = [str(value) for value in (explicit_skins or ()) if str(value).strip()]
    named, ordered = _split_explicit_skins(explicit)
    warnings: List[str] = []
    if len(ordered) == 1 and len(model.pieces) > 1 and not named and not broadcast_skin:
        warnings.append(
            f"one skin was supplied for {len(model.pieces)} pieces; it is broadcast for backward compatibility"
        )
    if ordered and len(ordered) not in (1, len(model.pieces)) and not named:
        warnings.append(
            f"{len(ordered)} ordered skins were supplied for {len(model.pieces)} pieces; piece-name heuristics will be used"
        )

    resolved: List[ResolvedPieceSkin] = []
    ordered_names = [_skin_name_from_path(value) for value in ordered]
    for piece_index, piece in enumerate(model.pieces):
        piece_key = _token(piece.name)
        selected = named.get(piece_key, "")
        inferred = False
        if not selected and ordered:
            if len(ordered) == 1:
                selected = ordered[0]
            elif len(ordered) == len(model.pieces):
                selected = ordered[piece_index]
            else:
                selected_name = gl_object_models._skin_for_piece(
                    piece.name,
                    piece_index,
                    len(model.pieces),
                    ordered_names,
                )
                selected = next(
                    (value for value in ordered if _skin_name_from_path(value).casefold() == selected_name.casefold()),
                    ordered[min(piece_index, len(ordered) - 1)],
                )
        if selected:
            skin_path = index.resolve(selected)
            if not skin_path and os.path.isfile(selected):
                skin_path = os.path.abspath(selected)
            skin_name = _skin_name_from_path(selected)
        else:
            inferred = True
            skin_name = gl_object_models._resolve_skin_for_piece(
                piece.name,
                piece_index,
                len(model.pieces),
                [],
                model.name,
                object_type=object_type,
                appearance_key=appearance_key,
                skin_cache=index,
            )
            skin_path = index.resolve(skin_name)
        if not skin_path:
            warnings.append(f"no skin resolved for piece {piece.name!r}")
        resolved.append(ResolvedPieceSkin(
            piece_name=piece.name,
            piece_index=piece_index,
            skin_name=skin_name,
            skin_path=skin_path,
            inferred=inferred,
        ))
    return SkinResolutionResult(
        model_name=model.name,
        skins_root=os.path.abspath(skins_root) if skins_root else "",
        pieces=resolved,
        warnings=warnings,
    )


def resolve_skin_paths_for_model(
    model: AbcModel,
    skins_root: str = "",
    **kwargs,
) -> List[str]:
    return [piece.skin_path for piece in resolve_model_skins(model, skins_root, **kwargs).pieces]


def catalog_variants_for_model(
    model: AbcModel,
    abc_path: str,
    catalog_path: str,
    skins_root: str,
) -> Tuple[List[CatalogSkinVariant], List[str]]:
    """Return deduplicated actor material sets for an ABC model."""
    if not os.path.isfile(catalog_path):
        raise FileNotFoundError(f"catalog was not found: {catalog_path}")
    if not os.path.isdir(skins_root):
        raise ValueError(f"skins root was not found: {skins_root}")
    with open(catalog_path, "r", encoding="utf-8") as stream:
        catalog = json.load(stream)
    expected = _model_catalog_key(abc_path)
    index = _SkinIndex(skins_root)
    grouped: Dict[Tuple[str, ...], Dict[str, List[str]]] = {}
    warnings: List[str] = []

    def add_variant(
        requested: Sequence[object],
        *,
        name_candidates: Sequence[object],
        source_keys: Sequence[object],
        warning_label: str,
    ) -> None:
        if not requested:
            return
        paths: List[str] = []
        missing: List[str] = []
        for skin_name in requested:
            if not str(skin_name).lower().endswith(".dtx"):
                missing.append(str(skin_name))
                continue
            path = index.resolve(str(skin_name))
            if path:
                paths.append(path)
            else:
                missing.append(str(skin_name))
        if missing:
            warnings.append(
                f"catalog variant {warning_label!r} has unsupported or missing skins: {', '.join(missing)}"
            )
            return
        if not paths:
            return
        identity = tuple(os.path.normcase(os.path.abspath(path)) for path in paths)
        entry = grouped.setdefault(identity, {"names": [], "keys": []})
        for candidate in name_candidates:
            text = str(candidate or "").strip()
            if text and text not in entry["names"]:
                entry["names"].append(text)
        for source_key in source_keys:
            text = str(source_key or "").strip()
            if text and text not in entry["keys"]:
                entry["keys"].append(text)

    model_variants = catalog.get("model_variants") or {}
    if isinstance(model_variants, dict):
        for model_path, rows in model_variants.items():
            if _normalise_model(model_path) != expected or not isinstance(rows, list):
                continue
            for row_index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                name = row.get("name") or model.name
                add_variant(
                    list(row.get("skins") or ()),
                    name_candidates=[name],
                    source_keys=list(row.get("source_keys") or ()),
                    warning_label=str(name or f"{model_path}#{row_index + 1}"),
                )

    actor_visuals = catalog.get("actor_visuals") or {}
    for key, value in actor_visuals.items():
        if not isinstance(value, dict) or _normalise_model(value.get("model", "")) != expected:
            continue
        requested = list(value.get("skins") or ()) + list(value.get("accessory_skins") or ())
        add_variant(
            requested,
            name_candidates=(value.get("type_picture"), value.get("monster_name"), key),
            source_keys=(key,),
            warning_label=str(key),
        )

    variants: List[CatalogSkinVariant] = []
    used_names: Dict[str, int] = {}
    for identity, details in grouped.items():
        base_name = details["names"][0] if details["names"] else model.name
        count = used_names.get(base_name.casefold(), 0) + 1
        used_names[base_name.casefold()] = count
        name = base_name if count == 1 else f"{base_name} {count}"
        variants.append(CatalogSkinVariant(name=name, skin_paths=identity, source_keys=tuple(details["keys"])))
    variants.sort(key=lambda item: _natural_key(item.name))
    return variants, _deduplicate(warnings)


def _split_explicit_skins(values: Sequence[str]) -> Tuple[Dict[str, str], List[str]]:
    named: Dict[str, str] = {}
    ordered: List[str] = []
    for value in values:
        left, separator, right = value.partition("=")
        if separator and _token(left) and right.strip():
            named[_token(left)] = right.strip().strip('"')
        else:
            ordered.append(value.strip().strip('"'))
    return named, ordered


def _lookup_keys(value: str) -> Iterable[str]:
    norm = str(value or "").replace("/", "\\").strip().strip('"').lstrip("\\")
    if norm.lower().startswith("skins\\"):
        norm = norm[6:]
    if norm and not os.path.splitext(norm)[1]:
        norm += ".DTX"
    if norm:
        yield norm.casefold()
        yield os.path.basename(norm).casefold()


def _skin_name_from_path(path: str) -> str:
    return os.path.basename(str(path or "").replace("\\", "/").strip().strip('"'))


def _normalise_model(value: str) -> str:
    norm = str(value or "").replace("/", "\\").strip().strip('"').lstrip("\\")
    if not norm.lower().startswith("models\\"):
        norm = "models\\" + norm
    return norm.casefold()


def _model_catalog_key(path: str) -> str:
    absolute = os.path.abspath(path)
    parts = absolute.replace("/", "\\").split("\\")
    model_index = next((index for index in range(len(parts) - 1, -1, -1) if parts[index].casefold() == "models"), None)
    relative = "\\".join(parts[model_index:]) if model_index is not None else os.path.basename(path)
    return _normalise_model(relative)


def _token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _natural_key(value: str):
    return tuple(int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value))


def _deduplicate(values: Sequence[str]) -> List[str]:
    return list(dict.fromkeys(values))
