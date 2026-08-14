"""Catalog-backed runtime representations for model-like prefab sources.

DEdit ``.ed`` brushes are useful authoring and preview data, but the editor's
small mesh serializer is not a replacement for DEdit's runtime BSP compiler.
This module finds shipped MM9 model/skin combinations that can represent a
prefab as an ordinary runtime object instead of manufacturing BSP records.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple


# DestructableProp needs a separately reviewed damage/destruction profile;
# sharing Filename/Skin with Prop is not enough to import it generically.
_PROP_CLASSES = ("Prop",)


@dataclass(frozen=True)
class ResourceBackedCandidate:
    candidate_id: str
    target_class: str
    model_path: str
    skin_paths: Tuple[str, ...]
    display_name: str
    usage_count: int
    source_keys: Tuple[str, ...]
    score: int
    reasons: Tuple[str, ...]


def find_resource_backed_candidates(
    prefab_path: str,
    catalog: Mapping[str, Any],
    *,
    resource_exists: Optional[Callable[[str], bool]] = None,
    limit: int = 30,
) -> Tuple[ResourceBackedCandidate, ...]:
    """Return ranked stock Prop representations for *prefab_path*.

    Results are intentionally conservative: only model paths observed on a
    stock ``Prop``/``DestructableProp`` and catalog model/skin variants are
    considered.  A weak filename coincidence is not shown to the user.
    """
    stem = os.path.splitext(os.path.basename(str(prefab_path or "")))[0]
    source_token = _token(stem)
    if not source_token:
        return ()
    filenames = catalog.get("filenames")
    variants = catalog.get("model_variants")
    if not isinstance(filenames, Mapping) or not isinstance(variants, Mapping):
        return ()

    candidates = []
    for raw_model, raw_rows in variants.items():
        model_path = _canonical_path(raw_model)
        if not model_path.lower().endswith(".abc"):
            continue
        filename_entry = filenames.get(str(raw_model))
        if not isinstance(filename_entry, Mapping):
            filename_entry = filenames.get(model_path.lower())
        if not isinstance(filename_entry, Mapping):
            continue
        classes = tuple(str(value) for value in filename_entry.get("classes") or ())
        target_class = next((name for name in _PROP_CLASSES if name in classes), "")
        if not target_class:
            continue

        model_stem = os.path.splitext(os.path.basename(model_path))[0]
        model_token = _token(model_stem)
        score, reasons = _match_score(source_token, model_token)
        if score < 70:
            continue
        if resource_exists is not None and not resource_exists(model_path):
            continue

        rows = raw_rows if isinstance(raw_rows, Sequence) and not isinstance(raw_rows, (str, bytes)) else ()
        for raw_row in rows:
            if not isinstance(raw_row, Mapping):
                continue
            skins = tuple(
                _canonical_path(value)
                for value in raw_row.get("skins") or ()
                if str(value or "").strip()
            )
            if resource_exists is not None and any(
                not resource_exists(skin) for skin in skins
            ):
                continue
            source_keys = tuple(str(value) for value in raw_row.get("source_keys") or ())
            uses = int(filename_entry.get("uses") or len(source_keys) or 0)
            row_name = str(raw_row.get("name") or target_class)
            candidate_id = "|".join((target_class, model_path.casefold(), *(s.casefold() for s in skins)))
            skin_label = ", ".join(skins) if skins else "catalog/default skin"
            display = f"{model_stem} — {target_class}; {skin_label} ({uses} stock use(s))"
            candidates.append(ResourceBackedCandidate(
                candidate_id=candidate_id,
                target_class=target_class,
                model_path=model_path,
                skin_paths=skins,
                display_name=display,
                usage_count=uses,
                source_keys=source_keys,
                score=score + min(uses, 40) + (3 if row_name == target_class else 0),
                reasons=tuple(reasons),
            ))

    # De-duplicate equivalent model/skin rows while preserving the strongest
    # evidence, then keep ordering deterministic for project/UI tests.
    unique = {}
    for item in candidates:
        previous = unique.get(item.candidate_id)
        if previous is None or item.score > previous.score:
            unique[item.candidate_id] = item
    return tuple(sorted(
        unique.values(),
        key=lambda item: (-item.score, -item.usage_count, item.display_name.casefold()),
    )[:max(0, int(limit))])


def candidate_by_id(
    candidates: Sequence[ResourceBackedCandidate],
    candidate_id: str,
) -> Optional[ResourceBackedCandidate]:
    key = str(candidate_id or "").casefold()
    return next((item for item in candidates if item.candidate_id.casefold() == key), None)


def _match_score(source: str, model: str) -> Tuple[int, Tuple[str, ...]]:
    if source == model:
        return 120, ("exact prefab/model name",)
    if model.startswith(source):
        suffix = model[len(source):]
        # MM9 commonly appends a numeric variant and EW/NS orientation.
        if not suffix or re.fullmatch(r"\d*(?:ew|ns)?", suffix):
            return 105, ("model is a numbered/oriented prefab variant",)
        return 90, ("model name starts with prefab name",)
    if source.startswith(model) and len(model) >= 5:
        return 82, ("prefab name starts with model name",)
    if len(source) >= 6 and source in model:
        return 74, ("prefab name occurs in model name",)
    return 0, ()


def _token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _canonical_path(value: Any) -> str:
    return str(value or "").replace("/", "\\").strip("\\")
