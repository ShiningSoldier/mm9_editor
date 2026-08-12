"""Data-derived world-helper classification shared by catalog and viewport code.

LithTech's ``object.lto`` describes whether a class inherits from an actor or
model object and supplies the class' default properties.  Combined with model
paths observed in level DAT files, that is sufficient to distinguish visible
objects from editor/service objects without maintaining per-game class lists.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional


MODEL_EXTENSIONS = (".abc", ".lta", ".ltb")

_ACTOR_ANCESTORS = {"actor", "aibase"}
_MODEL_ANCESTORS = {"modelobject"}

# Used only when object.lto metadata is unavailable (for example, a converted
# LoMM class opened with an older MM9 catalog).  These properties are specific
# to AI actors and avoid relying on the object's class name.
_ACTOR_SIGNATURE_PROPERTIES = {
    "sightdistance",
    "hearingdistance",
    "rangeattacktype",
    "runawaychance",
    "canberesurrected",
    "givetreasure",
    "buryondeath",
    "fadeondeath",
}
_ACTOR_SIGNATURE_PROPERTY_NAMES = (
    "SightDistance",
    "HearingDistance",
    "RangeAttackType",
    "RunawayChance",
    "CanBeResurrected",
    "GiveTreasure",
    "BuryOnDeath",
    "FadeOnDeath",
)


def is_model_resource(value: object) -> bool:
    """Return whether *value* names a LithTech model resource."""
    return str(value or "").strip().strip('"').casefold().endswith(MODEL_EXTENSIONS)


def object_has_actor_signature(obj: object) -> bool:
    """Detect an actor from its DAT properties when catalog metadata is absent."""
    getter = getattr(obj, "get", None)
    if not callable(getter):
        return False
    missing = object()
    return any(
        getter(name, missing) is not missing
        for name in _ACTOR_SIGNATURE_PROPERTY_NAMES
    )


def object_model_resource(obj: object) -> str:
    """Return an object's explicit DAT model path, or an empty string."""
    getter = getattr(obj, "get", None)
    if not callable(getter):
        return ""
    value = getter("Filename", "")
    return str(value) if is_model_resource(value) else ""


def _template_properties(entry: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    object_lto = entry.get("object_lto")
    if isinstance(object_lto, Mapping):
        props = object_lto.get("template_properties")
        if isinstance(props, list):
            return (prop for prop in props if isinstance(prop, Mapping))

    template = entry.get("template")
    if isinstance(template, Mapping):
        props = template.get("properties")
        if isinstance(props, list):
            return (prop for prop in props if isinstance(prop, Mapping))
    return ()


def _object_lto_ancestry(entry: Mapping[str, Any]) -> set[str]:
    object_lto = entry.get("object_lto")
    if not isinstance(object_lto, Mapping):
        return set()
    ancestry = {
        str(value).casefold()
        for value in (object_lto.get("hierarchy") or ())
        if value
    }
    parent = object_lto.get("parent")
    if parent:
        ancestry.add(str(parent).casefold())
    return ancestry


def _lto_model_resources(entry: Mapping[str, Any]) -> list[str]:
    resources = []
    for prop in _template_properties(entry):
        if str(prop.get("name") or "").casefold() != "filename":
            continue
        value = prop.get("value", prop.get("default_value"))
        if is_model_resource(value):
            resources.append(str(value))
    return resources


def _dat_model_resources(entry: Mapping[str, Any]) -> list[str]:
    values = entry.get("dat_model_filenames") or ()
    return [str(value) for value in values if is_model_resource(value)]


def classify_world_helper_entry(entry: Mapping[str, Any]) -> Dict[str, Any]:
    """Build stable helper metadata for one catalog class entry.

    A world helper is a class with no actor/model inheritance and no model
    resource in either object.lto defaults or observed DAT ``Filename`` values.
    """
    ancestry = _object_lto_ancestry(entry)
    object_lto_available = isinstance(entry.get("object_lto"), Mapping)

    if ancestry & _ACTOR_ANCESTORS:
        return {
            "is_helper": False,
            "reason": "actor_hierarchy",
            "source": "object.lto",
        }

    if ancestry & _MODEL_ANCESTORS:
        return {
            "is_helper": False,
            "reason": "model_hierarchy",
            "source": "object.lto",
        }

    if _lto_model_resources(entry):
        return {
            "is_helper": False,
            "reason": "object_lto_model_resource",
            "source": "object.lto",
        }

    if _dat_model_resources(entry):
        return {
            "is_helper": False,
            "reason": "dat_model_resource",
            "source": "dat",
        }

    property_names = {
        str(name).casefold() for name in (entry.get("property_names") or ())
    }
    if property_names & _ACTOR_SIGNATURE_PROPERTIES:
        return {
            "is_helper": False,
            "reason": "actor_property_signature",
            "source": "dat",
        }

    if not object_lto_available:
        # Older DAT-only catalogs do not distinguish explicit DAT filenames
        # from actor-visual preview resolutions, so use their filenames only
        # when no object.lto evidence exists.
        filenames = entry.get("filenames") or ()
        if any(is_model_resource(value) for value in filenames):
            return {
                "is_helper": False,
                "reason": "catalog_model_resource",
                "source": "dat",
            }

    return {
        "is_helper": True,
        "reason": "non_actor_without_model_resource",
        "source": "object.lto" if object_lto_available else "dat",
    }


def annotate_catalog_world_helpers(
    catalog: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Add or refresh data-derived ``world_helper`` metadata in *catalog*."""
    classes = catalog.get("classes")
    if not isinstance(classes, MutableMapping):
        return catalog
    for entry in classes.values():
        if isinstance(entry, MutableMapping):
            entry["world_helper"] = classify_world_helper_entry(entry)
    return catalog


def helper_value(metadata: object) -> Optional[bool]:
    """Read a helper decision from a catalog metadata value."""
    if isinstance(metadata, bool):
        return metadata
    if isinstance(metadata, Mapping):
        world_helper = metadata.get("world_helper", metadata)
        if isinstance(world_helper, bool):
            return world_helper
        if isinstance(world_helper, Mapping):
            value = world_helper.get("is_helper")
            if isinstance(value, bool):
                return value
    return None
