"""Conservative analyzer, planner, and Phase 2-6 behavioral materializer.

This module deliberately separates analysis from materialization.  It retains
typed source properties, builds deterministic names/references, and refuses to
declare an import ready when brush ownership, class policy, bindings, or
resources are unresolved.
"""

from __future__ import annotations

import copy
import hashlib
import math
import os
import re
import struct
from dataclasses import replace
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Set, Tuple

import _path_setup  # noqa: F401
import mm9_patch as patcher
from core import bsp
from features.dat_editing import legacy_ed
from features.prefabs import import_static, inspector
from features.prefabs.graph import (
    BehavioralPrefabImportPlan,
    DiagnosticSeverity,
    PlannedBrush,
    PlannedObject,
    PlannedReference,
    PrefabAnalysis,
    PrefabBrushGroup,
    PrefabDependency,
    PrefabDiagnostic,
    PrefabGraph,
    PrefabObject,
    PrefabProperty,
    PrefabReference,
    SpatialSemantics,
    SupportState,
    Vec3,
)


PLANNER_VERSION = 2

PHASE2_OBJECT_CLASSES = frozenset({
    "Prop",
    "DestructableProp",
    "DirLight",
    "Light",
    "WallTorch",
})

PHASE3_PASSIVE_CLASSES = frozenset({
    *PHASE2_OBJECT_CLASSES,
    "WorldObject",
    "AmbientSound",
    "Fire",
    "ClearWater",
    "BlueWater",
    "DirtyWater",
    "Ladder",
    "DemoSkyWorldModel",
    "Teleporter",
})

PHASE4_MOVING_CLASSES = frozenset({
    "Door",
    "RotatingDoor",
    "RotatingBrush",
    "Lift",
})

PHASE4_SIMPLE_CLASSES = frozenset({
    *PHASE3_PASSIVE_CLASSES,
    *PHASE4_MOVING_CLASSES,
})

PHASE5_LINKED_CLASSES = frozenset({
    *PHASE4_SIMPLE_CLASSES,
    "Trigger",
    "Switch",
})

PHASE6_HAZARD_CLASSES = frozenset({
    "DestructableBrush",
    "PropDamager",
    "Shooter",
})

PHASE6_BEHAVIORAL_CLASSES = frozenset({
    *PHASE5_LINKED_CLASSES,
    *PHASE6_HAZARD_CLASSES,
})

# Phase 6 does not treat the existence of an arbitrary .scr file as proof that
# a prefab is portable.  These four shipped scripts were reviewed separately:
# PropAnim is name-independent, while the Pipe Organ songs use literal
# GetObjectHandle targets and therefore require a per-import rewritten copy.
PHASE6_REVIEWED_SCRIPTS = frozenset({
    "scripts\\propanim.scr",
    "scripts\\tocatta.scr",
    "scripts\\rondo.scr",
    "scripts\\diesirae.scr",
})
PHASE6_REWRITTEN_SCRIPTS = frozenset({
    "scripts\\tocatta.scr",
    "scripts\\rondo.scr",
    "scripts\\diesirae.scr",
})

OMIT_PORTAL_BINDING = "<omit>"

_MOVING_CLASS_KEYS = {value.casefold() for value in PHASE4_MOVING_CLASSES}
_PHASE4_CLASS_KEYS = {value.casefold() for value in PHASE4_SIMPLE_CLASSES}
_PHASE5_CLASS_KEYS = {value.casefold() for value in PHASE5_LINKED_CLASSES}
_PHASE6_CLASS_KEYS = {value.casefold() for value in PHASE6_BEHAVIORAL_CLASSES}
_PHASE6_REVIEWED_SCRIPT_KEYS = {
    value.replace("/", "\\").casefold() for value in PHASE6_REVIEWED_SCRIPTS
}
_PHASE6_REWRITTEN_SCRIPT_KEYS = {
    value.replace("/", "\\").casefold() for value in PHASE6_REWRITTEN_SCRIPTS
}
_SCRIPT_REFERENCE_PROPERTY = "ScriptObjectTarget"
_SCRIPT_OBJECT_HANDLE_PATTERN = re.compile(
    r"(?im)(\bGetObjectHandle\s+)([^,\r\n]+)(\s*,)"
)

_PASSIVE_OWNED_BSP_ROLES = {
    "worldobject": "controller_geometry",
    "clearwater": "water",
    "bluewater": "water",
    "dirtywater": "water",
    "ladder": "ladder",
    "demoskyworldmodel": "skybox",
}

# These properties are present in the legacy DEdit class definitions but are
# absent from MM9's runtime object.lto schema.  Their source defaults do not
# have a runtime field to receive them; retaining the object.lto defaults is
# deliberate and reported instead of silently dropping arbitrary properties.
PHASE2_IGNORED_SOURCE_PROPERTIES = {
    "prop": frozenset({"translucency"}),
    "destructableprop": frozenset({
        "translucency",
        "energy",
        "plastic",
        "flesh",
        "liquid",
    }),
    "rotatingbrush": frozenset({
        "energy",
        "plastic",
        "flesh",
        "liquid",
        "spawnnbr",
    }),
    "destructablebrush": frozenset({
        "energy",
        "plastic",
        "flesh",
        "liquid",
    }),
    "propdamager": frozenset({"translucency"}),
    # Legacy prefab files used an integer projectile enum.  Both shipped game
    # schemas instead expose ProjectileName and default it to FireBolt.  The
    # Phase-6 corpus contains only enum value 2, which is the old FireBolt
    # selection; keep the catalog template's explicit string value.
    "shooter": frozenset({"projectiletype"}),
}

_IGNORED_SOURCE_PROPERTY_DEFAULTS = {
    ("prop", "translucency"): 1.0,
    ("destructableprop", "translucency"): 1.0,
    ("destructableprop", "energy"): False,
    ("destructableprop", "plastic"): False,
    ("destructableprop", "flesh"): False,
    ("destructableprop", "liquid"): False,
    ("rotatingbrush", "energy"): False,
    ("rotatingbrush", "plastic"): False,
    ("rotatingbrush", "flesh"): False,
    ("rotatingbrush", "liquid"): False,
    ("rotatingbrush", "spawnnbr"): 0,
    ("destructablebrush", "energy"): False,
    ("destructablebrush", "plastic"): False,
    ("destructablebrush", "flesh"): False,
    ("destructablebrush", "liquid"): False,
    ("propdamager", "translucency"): 1.0,
    ("shooter", "projectiletype"): 2,
}

# DEdit's legacy AmbientSound class called the audible range Radius.  MM9's
# runtime schema split the field and consumes the old value as OuterRadius.
SOURCE_PROPERTY_ALIASES = {
    "ambientsound": {
        "radius": "OuterRadius",
    },
}

_REFERENCE_NAMES = {
    "attachto",
    "doubledoorname",
    "portalname",
    "teleportdestination",
    "damagetriggertarget",
    "deathtriggertarget",
}
_REFERENCE_PATTERN = re.compile(r"^(?:open|close)triggertarget\d+$|^targetname\d+$", re.I)
_RESOURCE_EXTENSIONS = {
    ".abc": "model",
    ".dtx": "texture",
    ".spr": "sprite",
    ".wav": "sound",
    ".scr": "script",
}
_RESOURCE_ROOTS = {
    "model": "MODELS",
    "skin": "SKINS",
    "texture": "TEXTURES",
    "sprite": "SPRITES",
    "sound": "SOUNDS",
    "script": "SCRIPTS",
}
_TYPE_NAMES = {
    0: "string",
    1: "vector",
    2: "color",
    3: "real",
    4: "flags",
    5: "bool",
    6: "longint",
    7: "rotation",
}

_SPATIAL_BY_PROPERTY = {
    "pos": SpatialSemantics.WORLD_POINT,
    "rotation": SpatialSemantics.QUATERNION,
    "rotationpoint": SpatialSemantics.WORLD_POINT,
    "soundpos": SpatialSemantics.WORLD_POINT,
    "movedir": SpatialSemantics.DIRECTION,
    "current": SpatialSemantics.DIRECTION,
    "dims": SpatialSemantics.EXTENT,
    "triggerdims": SpatialSemantics.EXTENT,
    "damagedims": SpatialSemantics.EXTENT,
    "destroydims": SpatialSemantics.EXTENT,
    "skydims": SpatialSemantics.EXTENT,
    "fireoffset": SpatialSemantics.LOCAL_OFFSET,
    "fireminvel": SpatialSemantics.LOCAL_VELOCITY,
    "firemaxvel": SpatialSemantics.LOCAL_VELOCITY,
    "smokeminvel": SpatialSemantics.LOCAL_VELOCITY,
    "smokemaxvel": SpatialSemantics.LOCAL_VELOCITY,
    "spawnobjectvel": SpatialSemantics.LOCAL_VELOCITY,
    "rotationangles": SpatialSemantics.BEHAVIOR_LOCAL,
}


def analyze_prefab(
    path: str,
    *,
    catalog: Optional[Mapping[str, Any]] = None,
    supported_classes: Iterable[str] = (),
    resource_exists: Optional[Callable[[str, str], Optional[bool]]] = None,
    allow_scripts: bool = False,
    allowed_script_names: Optional[Iterable[str]] = None,
    script_loader: Optional[Callable[[str], str]] = None,
    allow_generated_bsp: bool = True,
) -> PrefabAnalysis:
    graph = load_prefab_graph(path, resource_exists=resource_exists)
    diagnostics = list(graph.diagnostics)

    if graph.brushes:
        static_state = SupportState.STATIC_READY
    else:
        static_state = SupportState.BLOCKED
        diagnostics.append(PrefabDiagnostic(
            "static_no_geometry",
            DiagnosticSeverity.BLOCKING,
            "This prefab has no brush/BSP geometry for static import.",
        ))

    runtime_objects = graph.runtime_objects
    if not runtime_objects:
        behavioral_state = SupportState.BLOCKED
        diagnostics.append(PrefabDiagnostic(
            "behavioral_no_objects",
            DiagnosticSeverity.BLOCKING,
            "This prefab has no runtime objects to import behaviorally.",
        ))
    else:
        supported = {str(value).casefold() for value in supported_classes}
        legacy_brush_blocked = bool(
            graph.brushes
            and graph.source_format == "legacy_ed"
            and not allow_generated_bsp
        )
        if legacy_brush_blocked:
            diagnostics.append(PrefabDiagnostic(
                "behavioral_ed_bsp_requires_compilation",
                DiagnosticSeverity.BLOCKING,
                "This behavioral prefab contains DEdit ED brush geometry. "
                "Compile it to a v66 DAT with DEdit before importing the "
                "controller/BSP assembly; object-only ED prefabs remain supported.",
            ))
        missing_catalog = _missing_catalog_classes(runtime_objects, catalog)
        if missing_catalog:
            diagnostics.append(PrefabDiagnostic(
                "behavioral_missing_catalog_classes",
                DiagnosticSeverity.BLOCKING,
                "MM9 catalog templates are missing for: " + ", ".join(missing_catalog),
            ))
        unsupported = sorted(
            {obj.class_name for obj in runtime_objects if obj.class_name.casefold() not in supported},
            key=str.casefold,
        )
        if unsupported:
            diagnostics.append(PrefabDiagnostic(
                "behavioral_class_policy_pending",
                DiagnosticSeverity.BLOCKING,
                "Behavioral import policies are not enabled yet for: " + ", ".join(unsupported),
            ))
        schema_blockers, ignored_properties = _catalog_property_issues(
            runtime_objects,
            catalog,
        )
        if schema_blockers:
            diagnostics.append(PrefabDiagnostic(
                "behavioral_catalog_property_mismatch",
                DiagnosticSeverity.BLOCKING,
                "Source properties do not match the MM9 object.lto schema: "
                + "; ".join(schema_blockers[:8]),
            ))
        if ignored_properties:
            diagnostics.append(PrefabDiagnostic(
                "behavioral_obsolete_source_properties",
                DiagnosticSeverity.WARNING,
                "Legacy properties with no MM9 object.lto field will retain runtime defaults: "
                + ", ".join(ignored_properties),
            ))
        scripted = [
            obj for obj in runtime_objects
            if str(obj.property_value("ScriptName", "") or "").strip()
        ]
        script_policy_blocked = False
        if scripted and not allow_scripts:
            details = ", ".join(
                f"{obj.source_name or obj.class_name} ({obj.property_value('ScriptName')})"
                for obj in scripted
            )
            diagnostics.append(PrefabDiagnostic(
                "behavioral_script_policy_pending",
                DiagnosticSeverity.BLOCKING,
                "Scripted prefab behavior requires the scripted-behavior phase: " + details,
            ))
            script_policy_blocked = True
        elif scripted and allowed_script_names is not None:
            graph, script_diagnostics, script_policy_blocked = _review_scripted_objects(
                graph,
                scripted,
                allowed_script_names=allowed_script_names,
                script_loader=script_loader,
            )
            diagnostics.extend(script_diagnostics)
        linked_teleporters = [
            obj for obj in runtime_objects
            if obj.class_name.casefold() == "teleporter"
            and str(obj.property_value("TeleportDestination", "") or "").strip()
        ]
        phase5_enabled = _PHASE5_CLASS_KEYS <= supported
        phase6_enabled = _PHASE6_CLASS_KEYS <= supported
        if linked_teleporters and not phase5_enabled:
            diagnostics.append(PrefabDiagnostic(
                "behavioral_linked_teleporter_policy_pending",
                DiagnosticSeverity.BLOCKING,
                "Linked teleporter graphs require the linked-behavior phase; "
                "Phase 3 only supports unlinked teleporter components.",
            ))
        phase4_diagnostics = []
        if _phase4_policy_is_active(graph, supported):
            phase4_diagnostics = list(_simple_moving_policy_diagnostics(graph))
            diagnostics.extend(phase4_diagnostics)
        materializable_graph = (
            _is_passive_graph(graph)
            or _is_phase4_graph(graph)
            or (phase5_enabled and _is_phase5_graph(graph))
            or (phase6_enabled and _is_phase6_graph(graph))
        )
        compiled_system_brushes = [
            brush for brush in graph.brushes
            if brush.ownership == "system"
        ] if materializable_graph and graph.source_format == "compiled_dat" else []
        if compiled_system_brushes:
            diagnostics.append(PrefabDiagnostic(
                "behavioral_compiled_system_bsp_omitted",
                DiagnosticSeverity.WARNING,
                "Compiled PhysicsBSP/VisBSP helper records will be omitted; "
                "controller-owned compiled models remain importable.",
            ))
        unresolved_ownership = [brush for brush in graph.brushes if brush.ownership == "unresolved"]
        if unresolved_ownership:
            diagnostics.append(PrefabDiagnostic(
                "behavioral_brush_ownership_unresolved",
                DiagnosticSeverity.BLOCKING,
                f"Brush ownership is unresolved for {len(unresolved_ownership)} brush group(s).",
            ))
        binding_refs = _binding_references(graph)
        missing_dependencies = [item for item in graph.dependencies if item.availability == "missing"]
        has_blocker = bool(
            legacy_brush_blocked
            or missing_catalog
            or unsupported
            or schema_blockers
            or unresolved_ownership
            or script_policy_blocked
            or (linked_teleporters and not phase5_enabled)
            or phase4_diagnostics
        )
        if has_blocker:
            behavioral_state = SupportState.BLOCKED
        elif binding_refs or missing_dependencies:
            behavioral_state = SupportState.ACTION_REQUIRED
            if binding_refs:
                diagnostics.append(PrefabDiagnostic(
                    "behavioral_external_bindings_required",
                    DiagnosticSeverity.ACTION_REQUIRED,
                    f"{len(binding_refs)} target-level object/portal binding(s) must be resolved.",
                ))
            if missing_dependencies:
                diagnostics.append(PrefabDiagnostic(
                    "behavioral_missing_resources",
                    DiagnosticSeverity.ACTION_REQUIRED,
                    f"{len(missing_dependencies)} resource dependency decision(s) are required.",
                ))
        else:
            behavioral_state = SupportState.BEHAVIORAL_READY

    return PrefabAnalysis(
        graph=graph,
        static_state=static_state,
        behavioral_state=behavioral_state,
        diagnostics=tuple(diagnostics),
    )


def load_prefab_graph(
    path: str,
    *,
    resource_exists: Optional[Callable[[str, str], Optional[bool]]] = None,
) -> PrefabGraph:
    absolute = os.path.abspath(path)
    with open(absolute, "rb") as handle:
        data = handle.read()
    if len(data) < 4:
        raise ValueError(f"prefab file is too short: {absolute}")
    version = int.from_bytes(data[:4], "little")
    fingerprint = hashlib.sha256(data).hexdigest()
    if version == legacy_ed.LEGACY_ED_VERSION:
        return _load_ed_graph(absolute, data, fingerprint, resource_exists)
    if version == 66:
        return _load_dat_graph(absolute, data, fingerprint, resource_exists)
    raise ValueError(
        f"unsupported prefab version {version}; expected 66 or {legacy_ed.LEGACY_ED_VERSION}"
    )


def _review_scripted_objects(
    graph: PrefabGraph,
    scripted: Sequence[PrefabObject],
    *,
    allowed_script_names: Iterable[str],
    script_loader: Optional[Callable[[str], str]],
) -> Tuple[PrefabGraph, Tuple[PrefabDiagnostic, ...], bool]:
    """Validate reviewed scripts and expose literal object targets as links."""
    allowed = {
        _canonical_resource_path("script", value).casefold()
        for value in allowed_script_names
    }
    object_names = {
        obj.source_name.casefold(): obj.index
        for obj in graph.runtime_objects
        if obj.source_name
    }
    references = list(graph.references)
    diagnostics = []
    blocked = False
    rewritten_count = 0

    for obj in scripted:
        raw_name = str(obj.property_value("ScriptName", "") or "").strip()
        canonical = _canonical_resource_path("script", raw_name)
        key = canonical.casefold()
        label = f"{obj.source_name or obj.class_name} ({raw_name})"
        if key not in allowed:
            diagnostics.append(PrefabDiagnostic(
                "behavioral_script_not_reviewed",
                DiagnosticSeverity.BLOCKING,
                f"Script {label} has no reviewed Phase-6 import policy.",
                object_index=obj.index,
            ))
            blocked = True
            continue
        if script_loader is None:
            diagnostics.append(PrefabDiagnostic(
                "behavioral_script_source_unavailable",
                DiagnosticSeverity.BLOCKING,
                f"The source text for reviewed script {canonical} is unavailable.",
                object_index=obj.index,
            ))
            blocked = True
            continue
        try:
            text = script_loader(canonical)
        except Exception as exc:
            diagnostics.append(PrefabDiagnostic(
                "behavioral_script_source_unavailable",
                DiagnosticSeverity.BLOCKING,
                f"Could not read reviewed script {canonical}: {exc}",
                object_index=obj.index,
            ))
            blocked = True
            continue
        if not isinstance(text, str):
            diagnostics.append(PrefabDiagnostic(
                "behavioral_script_source_unavailable",
                DiagnosticSeverity.BLOCKING,
                f"Reviewed script {canonical} did not provide text content.",
                object_index=obj.index,
            ))
            blocked = True
            continue

        tokens = []
        malformed = []
        handle_matches = list(_SCRIPT_OBJECT_HANDLE_PATTERN.finditer(text))
        if text.casefold().count("getobjecthandle") != len(handle_matches):
            malformed.append("<unparsed GetObjectHandle command>")
        for match in handle_matches:
            token = match.group(2).strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_#]*", token):
                malformed.append(token)
                continue
            if token.casefold() not in {item.casefold() for item in tokens}:
                tokens.append(token)

        if malformed:
            diagnostics.append(PrefabDiagnostic(
                "behavioral_script_dynamic_reference_unsupported",
                DiagnosticSeverity.BLOCKING,
                f"Reviewed script {canonical} contains non-literal object lookup(s): "
                + ", ".join(malformed[:5]),
                object_index=obj.index,
            ))
            blocked = True
            continue
        if key not in _PHASE6_REWRITTEN_SCRIPT_KEYS:
            if tokens:
                diagnostics.append(PrefabDiagnostic(
                    "behavioral_script_policy_changed",
                    DiagnosticSeverity.BLOCKING,
                    f"Pass-through script {canonical} now contains object-name lookups; "
                    "its import policy must be reviewed again.",
                    object_index=obj.index,
                ))
                blocked = True
            continue
        if not tokens:
            diagnostics.append(PrefabDiagnostic(
                "behavioral_script_policy_changed",
                DiagnosticSeverity.BLOCKING,
                f"Rewritten script {canonical} no longer contains the reviewed literal lookups.",
                object_index=obj.index,
            ))
            blocked = True
            continue

        rewritten_count += 1
        for token in tokens:
            folded = token.casefold()
            references.append(PrefabReference(
                object_index=obj.index,
                property_name=_SCRIPT_REFERENCE_PROPERTY,
                source_value=token,
                target_kind="local" if folded in object_names else "external",
                target_object_index=object_names.get(folded),
            ))

    if rewritten_count:
        diagnostics.append(PrefabDiagnostic(
            "behavioral_script_namespace_rewrite",
            DiagnosticSeverity.WARNING,
            f"{rewritten_count} reviewed script assignment(s) will receive a "
            "per-import namespaced copy in SCRIPTS.REZ.",
        ))
    if references != list(graph.references):
        graph = replace(graph, references=tuple(references))
    return graph, tuple(diagnostics), blocked


def collect_reviewed_script_sources(
    analysis: PrefabAnalysis,
    script_loader: Callable[[str], str],
) -> Dict[str, str]:
    """Capture the exact reviewed script text used by a persistent import op."""
    sources: Dict[str, str] = {}
    for obj in analysis.graph.runtime_objects:
        raw_name = str(obj.property_value("ScriptName", "") or "").strip()
        if not raw_name:
            continue
        canonical = _canonical_resource_path("script", raw_name)
        if canonical.casefold() not in _PHASE6_REVIEWED_SCRIPT_KEYS:
            raise ValueError(f"script {raw_name!r} has no reviewed Phase-6 policy")
        sources[canonical] = script_loader(canonical)
    return sources


def script_loader_from_sources(
    sources: Mapping[str, str],
) -> Callable[[str], str]:
    by_key = {
        _canonical_resource_path("script", key).casefold(): str(value)
        for key, value in sources.items()
    }

    def _load(path: str) -> str:
        key = _canonical_resource_path("script", path).casefold()
        if key not in by_key:
            raise FileNotFoundError(path)
        return by_key[key]

    return _load


def build_script_import_assets(
    analysis: PrefabAnalysis,
    plan: BehavioralPrefabImportPlan,
    *,
    operation_id: str,
    script_loader: Callable[[str], str],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """Rewrite reviewed literal script links and return object/archive changes."""
    plan.require_ready()
    reference_values: Dict[Tuple[int, str], str] = {}
    for ref in plan.references:
        if ref.property_name != _SCRIPT_REFERENCE_PROPERTY:
            continue
        if not ref.target_value:
            raise ValueError(
                f"script target {ref.source_value!r} has no resolved object binding"
            )
        reference_values[(ref.object_index, ref.source_value.casefold())] = ref.target_value

    overrides: Dict[str, Dict[str, Any]] = {}
    assets: Dict[str, str] = {}
    safe_id = _sanitize_name(operation_id) or "import"
    for obj in analysis.graph.runtime_objects:
        raw_name = str(obj.property_value("ScriptName", "") or "").strip()
        if not raw_name:
            continue
        canonical = _canonical_resource_path("script", raw_name)
        if canonical.casefold() not in _PHASE6_REWRITTEN_SCRIPT_KEYS:
            continue
        source_text = script_loader(canonical)

        def _replace_target(match: re.Match[str]) -> str:
            token = match.group(2).strip()
            target = reference_values.get((obj.index, token.casefold()))
            if not target:
                raise ValueError(
                    f"reviewed script {canonical} target {token!r} was not planned"
                )
            return f"{match.group(1)}{target}{match.group(3)}"

        rewritten = _SCRIPT_OBJECT_HANDLE_PATTERN.sub(_replace_target, source_text)
        stem = _sanitize_name(os.path.splitext(os.path.basename(canonical))[0]) or "script"
        virtual_path = (
            f"SCRIPTS\\MM9EDITOR\\PREFAB_{safe_id}_{obj.index}_{stem}.SCR"
        )
        assets[virtual_path] = rewritten
        overrides.setdefault(str(obj.index), {})["ScriptName"] = virtual_path
    return overrides, assets


def build_behavioral_import_plan(
    analysis: PrefabAnalysis,
    *,
    root_name: str,
    target_pos: Vec3 = (0.0, 0.0, 0.0),
    target_yaw: float = 0.0,
    existing_names: Iterable[str] = (),
    external_bindings: Optional[Mapping[str, str]] = None,
    dependency_decisions: Optional[Mapping[str, str]] = None,
    fixed_object_names: Optional[Mapping[str, str]] = None,
) -> BehavioralPrefabImportPlan:
    graph = analysis.graph
    bindings = {str(k).casefold(): str(v) for k, v in (external_bindings or {}).items()}
    decisions = {str(k).casefold(): str(v) for k, v in (dependency_decisions or {}).items()}
    fixed_names = {
        str(key): str(value)
        for key, value in (fixed_object_names or {}).items()
        if str(value).strip()
    }
    used = {str(value).casefold() for value in existing_names if str(value)}
    namespace = _sanitize_name(root_name) or "ImportedPrefab"

    planned_objects = []
    name_map: Dict[str, str] = {}
    target_by_index: Dict[int, str] = {}
    runtime_objects = graph.runtime_objects
    for ordinal, obj in enumerate(runtime_objects, 1):
        source_token = obj.source_name or f"{obj.class_name}{ordinal}"
        base = namespace if len(runtime_objects) == 1 else f"{namespace}_{_sanitize_name(source_token)}"
        if (
            obj.class_name.casefold() == "demoskyworldmodel"
            and "skybox" not in base.casefold()
        ):
            # The viewport's ordinary BSP pass recognizes sky submodels by
            # name; the DemoSky controller still resolves the same model by
            # exact name in the dedicated sky pass.
            base = f"{base}_Skybox"
        target = fixed_names.get(str(obj.index)) or _allocate_name(base, used)
        used.add(target.casefold())
        target_by_index[obj.index] = target
        if obj.source_name:
            name_map[obj.source_name.casefold()] = target
        planned_objects.append(PlannedObject(
            source_index=obj.index,
            class_name=obj.class_name,
            source_name=obj.source_name,
            target_name=target,
        ))

    supported_mixed = bool(graph.brushes) and _is_materializable_graph(graph)
    unowned_target_by_brush: Dict[int, str] = {}
    if supported_mixed:
        # Authored portal brushes are compiler inputs for VisBSP/PVS data, not
        # additive movable submodels.  Phase 5 binds PortalName to a portal
        # already present in the target level (or explicitly clears it), so no
        # synthetic WorldObject is created for this source helper geometry.
        unowned = [
            brush for brush in graph.brushes
            if brush.ownership == "unowned" and brush.role != "portal"
        ]
        group_keys = (
            sorted({brush.role for brush in unowned})
            if graph.source_format == "legacy_ed"
            else [f"{brush.role}:{brush.index}" for brush in unowned]
        )
        for ordinal, group_key in enumerate(group_keys, 1):
            role = group_key.split(":", 1)[0]
            role_token = _sanitize_name(role) or f"Geometry{ordinal}"
            if graph.source_format != "legacy_ed":
                role_token = f"{role_token}{ordinal}"
            target = fixed_names.get(str(-ordinal)) or _allocate_name(
                f"{namespace}_{role_token}", used
            )
            used.add(target.casefold())
            for brush in unowned:
                candidate = brush.role if graph.source_format == "legacy_ed" else f"{brush.role}:{brush.index}"
                if candidate == group_key:
                    unowned_target_by_brush[brush.index] = target
            planned_objects.append(PlannedObject(
                source_index=-ordinal,
                class_name="WorldObject",
                source_name="",
                target_name=target,
                synthetic=True,
            ))

    planned_brushes = []
    for ordinal, brush in enumerate(graph.brushes, 1):
        if brush.role == "portal":
            planned_brushes.append(PlannedBrush(
                source_index=brush.index,
                source_name=brush.source_name,
                target_name="",
                role=brush.role,
                owner_target_name="",
            ))
            continue
        owner_target = (
            target_by_index.get(brush.owner_object_index, "")
            if brush.owner_object_index is not None
            else ""
        )
        if owner_target:
            target = owner_target
        elif brush.index in unowned_target_by_brush:
            target = unowned_target_by_brush[brush.index]
        else:
            suffix = _sanitize_name(brush.source_name) or f"Geometry{ordinal}"
            target = _allocate_name(f"{namespace}_{suffix}", used)
        if brush.source_name:
            name_map.setdefault(brush.source_name.casefold(), target)
        planned_brushes.append(PlannedBrush(
            source_index=brush.index,
            source_name=brush.source_name,
            target_name=target,
            role=brush.role,
            owner_target_name=owner_target,
        ))

    # Re-evaluate resolvable action diagnostics against this plan's concrete
    # bindings/decisions instead of carrying the source-analysis summary over.
    diagnostics = [
        item for item in analysis.diagnostics_for("behavioral")
        if item.code not in {
            "behavioral_external_bindings_required",
            "behavioral_missing_resources",
        }
    ]
    planned_references = []
    unresolved_bindings = 0
    for ref in graph.references:
        is_portal = ref.property_name.casefold() == "portalname"
        if is_portal:
            target_value = bindings.get(ref.source_value.casefold(), "")
            if target_value.casefold() == OMIT_PORTAL_BINDING:
                target_value = ""
                binding_kind = "omitted_portal"
            else:
                binding_kind = "external_portal"
                if not target_value:
                    unresolved_bindings += 1
        elif ref.target_kind == "local":
            target_value = name_map.get(ref.source_value.casefold(), "")
            binding_kind = "internal"
            if not target_value:
                diagnostics.append(PrefabDiagnostic(
                    "behavioral_internal_reference_unmapped",
                    DiagnosticSeverity.BLOCKING,
                    f"Cannot rewrite internal reference {ref.property_name}={ref.source_value!r}.",
                    object_index=ref.object_index,
                ))
        else:
            target_value = bindings.get(ref.source_value.casefold(), "")
            binding_kind = "external"
            if not target_value:
                unresolved_bindings += 1
        planned_references.append(PlannedReference(
            object_index=ref.object_index,
            property_name=ref.property_name,
            source_value=ref.source_value,
            target_value=target_value,
            binding_kind=binding_kind,
        ))
    if unresolved_bindings:
        diagnostics.append(PrefabDiagnostic(
            "behavioral_external_bindings_unresolved",
            DiagnosticSeverity.ACTION_REQUIRED,
            f"{unresolved_bindings} external target binding(s) remain unresolved.",
        ))

    dependencies = []
    unresolved_dependencies = 0
    for dependency in graph.dependencies:
        decision = decisions.get(dependency.path.casefold(), "")
        availability = dependency.availability
        if availability == "missing" and decision not in {"stage", "provide"}:
            unresolved_dependencies += 1
        dependencies.append(
            replace(dependency, availability=f"decision:{decision}" if decision else availability)
        )
    if unresolved_dependencies:
        diagnostics.append(PrefabDiagnostic(
            "behavioral_resource_decisions_unresolved",
            DiagnosticSeverity.ACTION_REQUIRED,
            f"{unresolved_dependencies} missing resource decision(s) remain unresolved.",
        ))

    blocking = any(item.severity == DiagnosticSeverity.BLOCKING for item in diagnostics)
    action = any(item.severity == DiagnosticSeverity.ACTION_REQUIRED for item in diagnostics)
    if blocking:
        state = SupportState.BLOCKED
    elif action:
        state = SupportState.ACTION_REQUIRED
    else:
        state = SupportState.BEHAVIORAL_READY

    return BehavioralPrefabImportPlan(
        source_path=graph.source_path,
        source_fingerprint=graph.source_fingerprint,
        root_name=namespace,
        support_state=state,
        target_pos=tuple(float(value) for value in target_pos),
        target_yaw=float(target_yaw),
        objects=tuple(planned_objects),
        brushes=tuple(planned_brushes),
        references=tuple(planned_references),
        dependencies=tuple(dependencies),
        diagnostics=tuple(diagnostics),
        name_map=tuple(sorted(name_map.items())),
        planner_version=PLANNER_VERSION,
    )


def required_external_bindings(
    analysis: PrefabAnalysis,
) -> Tuple[Tuple[str, str], ...]:
    """Return unique source target names requiring an explicit level binding.

    The second tuple item is ``portal`` or ``object`` and is intended for the
    import workspace. PortalName is always target-level because authored ED
    portal brushes cannot be merged into an already compiled VisBSP safely.
    """
    required: Dict[str, Tuple[str, str]] = {}
    for ref in _binding_references(analysis.graph):
        kind = "portal" if ref.property_name.casefold() == "portalname" else "object"
        key = ref.source_value.casefold()
        previous = required.get(key)
        if previous is None or kind == "portal":
            required[key] = (ref.source_value, kind)
    return tuple(required[key] for key in sorted(required))


def _binding_references(graph: PrefabGraph) -> Tuple[PrefabReference, ...]:
    return tuple(
        ref for ref in graph.references
        if ref.target_kind == "external" or ref.property_name.casefold() == "portalname"
    )


def validate_plan_target_bindings(
    plan: BehavioralPrefabImportPlan,
    *,
    target_object_names: Iterable[str],
    target_bsp: Optional[bsp.BspWorld] = None,
    target_dat_bytes: bytes = b"",
) -> Tuple[str, ...]:
    """Validate all explicit links against the current target level."""
    object_names = {
        str(value).casefold() for value in target_object_names if str(value).strip()
    }
    issues = []
    for ref in plan.references:
        if ref.binding_kind == "external":
            if not ref.target_value or ref.target_value.casefold() not in object_names:
                issues.append(
                    f"{ref.property_name} binding {ref.target_value or ref.source_value!r} "
                    "does not name an object in the target level"
                )
        elif ref.binding_kind == "external_portal":
            if not ref.target_value:
                issues.append(
                    f"PortalName {ref.source_value!r} requires an existing target portal "
                    f"or the explicit {OMIT_PORTAL_BINDING!r} choice"
                )
            elif not target_has_user_portal(
                target_bsp,
                target_dat_bytes,
                ref.target_value,
            ):
                issues.append(
                    f"PortalName binding {ref.target_value!r} is not an existing "
                    "user portal in the target level's VisBSP"
                )
    return tuple(dict.fromkeys(issues))


def target_has_user_portal(
    target_bsp: Optional[bsp.BspWorld],
    target_dat_bytes: bytes,
    name: str,
) -> bool:
    """Conservatively test whether VisBSP contains a named user portal.

    Portal record payloads differ between shipped compiler variants, so this
    reads only the stable WorldBSP header count and then searches that record
    for LithTech's length-prefixed portal name. A positive portal count keeps
    an unrelated string elsewhere in a portal-free VisBSP from being accepted.
    """
    if target_bsp is None or not target_dat_bytes or not str(name).strip():
        return False
    model = target_bsp.model_by_name("VisBSP")
    if (
        model is None
        or model.raw_start is None
        or model.raw_end is None
        or model.world_bsp_start is None
    ):
        return False
    raw = bytes(target_dat_bytes[int(model.raw_start):int(model.raw_end)])
    start = int(model.world_bsp_start) - int(model.raw_start)
    try:
        cursor = start + 8
        string_length = struct.unpack_from("<H", raw, cursor)[0]
        cursor += 2 + int(string_length)
        # point, plane, surface, then user-portal count
        user_portal_count = struct.unpack_from("<I", raw, cursor + 12)[0]
    except (IndexError, struct.error):
        return False
    if user_portal_count <= 0:
        return False
    try:
        encoded = str(name).strip().encode("latin-1")
    except UnicodeEncodeError:
        return False
    token = struct.pack("<H", len(encoded)) + encoded
    return token.lower() in raw[start:].lower()


def _is_passive_graph(graph: PrefabGraph) -> bool:
    runtime = graph.runtime_objects
    allowed = {value.casefold() for value in PHASE3_PASSIVE_CLASSES}
    return bool(runtime) and all(obj.class_name.casefold() in allowed for obj in runtime)


def _moving_objects(graph: PrefabGraph) -> Tuple[PrefabObject, ...]:
    return tuple(
        obj for obj in graph.runtime_objects
        if obj.class_name.casefold() in _MOVING_CLASS_KEYS
    )


def _is_phase4_graph(graph: PrefabGraph) -> bool:
    runtime = graph.runtime_objects
    return (
        bool(runtime)
        and bool(_moving_objects(graph))
        and all(obj.class_name.casefold() in _PHASE4_CLASS_KEYS for obj in runtime)
    )


def _is_phase5_graph(graph: PrefabGraph) -> bool:
    runtime = graph.runtime_objects
    movers = _moving_objects(graph)
    return (
        bool(runtime)
        and all(obj.class_name.casefold() in _PHASE5_CLASS_KEYS for obj in runtime)
        and (
            bool(graph.references)
            or len(movers) > 1
            or any(
                obj.class_name.casefold() in {"trigger", "switch"}
                for obj in runtime
            )
        )
    )


def _is_phase6_graph(graph: PrefabGraph) -> bool:
    runtime = graph.runtime_objects
    return (
        bool(runtime)
        and all(obj.class_name.casefold() in _PHASE6_CLASS_KEYS for obj in runtime)
        and any(
            obj.class_name.casefold()
            in {value.casefold() for value in PHASE6_HAZARD_CLASSES}
            or bool(str(obj.property_value("ScriptName", "") or "").strip())
            for obj in runtime
        )
    )


def _is_materializable_graph(graph: PrefabGraph) -> bool:
    return (
        _is_passive_graph(graph)
        or _is_phase4_graph(graph)
        or _is_phase5_graph(graph)
        or _is_phase6_graph(graph)
    )


def _phase4_policy_is_active(graph: PrefabGraph, supported: Set[str]) -> bool:
    """Apply the simple-mover policy only to the Phase-4 capability surface.

    The generic analyzer is also used by planner tests and future phases that
    explicitly opt into linked classes such as Trigger.  A capability set
    outside Phase 4 therefore keeps the analyzer's format-neutral behavior.
    """
    return bool(_moving_objects(graph)) and bool(supported) and supported <= _PHASE4_CLASS_KEYS


def _simple_moving_policy_diagnostics(
    graph: PrefabGraph,
) -> Tuple[PrefabDiagnostic, ...]:
    movers = _moving_objects(graph)
    diagnostics = []
    if len(movers) != 1:
        diagnostics.append(PrefabDiagnostic(
            "behavioral_compound_moving_graph_pending",
            DiagnosticSeverity.BLOCKING,
            "Phase 4 supports exactly one moving BSP controller; this assembly "
            f"contains {len(movers)}. Compound and paired assemblies belong to Phase 5.",
        ))
        return tuple(diagnostics)
    mover = movers[0]
    if not mover.owned_brush_indices:
        diagnostics.append(PrefabDiagnostic(
            "behavioral_moving_bsp_missing",
            DiagnosticSeverity.BLOCKING,
            f"{mover.source_name or mover.class_name} has no owned BSP geometry.",
            object_index=mover.index,
        ))
    if graph.references:
        properties = sorted({item.property_name for item in graph.references}, key=str.casefold)
        diagnostics.append(PrefabDiagnostic(
            "behavioral_linked_moving_graph_pending",
            DiagnosticSeverity.BLOCKING,
            "Phase 4 does not import linked moving assemblies ("
            + ", ".join(properties)
            + "); target rewriting and portals belong to Phase 5.",
            object_index=mover.index,
        ))
    portal_brushes = [brush for brush in graph.brushes if brush.role == "portal"]
    if portal_brushes:
        diagnostics.append(PrefabDiagnostic(
            "behavioral_moving_portal_pending",
            DiagnosticSeverity.BLOCKING,
            "This moving assembly contains authored portal geometry. Portal compilation "
            "and binding belong to Phase 5.",
            object_index=mover.index,
        ))
    return tuple(diagnostics)


def spatial_semantics_for(class_name: str, property_name: str) -> SpatialSemantics:
    del class_name  # Reserved for class-specific overrides introduced by later capabilities.
    return _SPATIAL_BY_PROPERTY.get(str(property_name).casefold(), SpatialSemantics.NON_SPATIAL)


def transform_spatial_value(
    semantics: SpatialSemantics,
    value: Sequence[float],
    *,
    target_pos: Vec3,
    target_yaw: float,
    source_anchor: Vec3 = (0.0, 0.0, 0.0),
) -> Tuple[float, ...]:
    values = tuple(float(item) for item in value)
    if semantics in {SpatialSemantics.NON_SPATIAL, SpatialSemantics.BEHAVIOR_LOCAL}:
        return values
    if semantics == SpatialSemantics.QUATERNION:
        if len(values) != 4:
            raise ValueError("rotation values must contain four components")
        return (values[0], values[1] + float(target_yaw), values[2], values[3])
    if len(values) != 3:
        raise ValueError(f"{semantics.value} values must contain three components")
    x, y, z = values
    # MM9 stores the editable yaw component in radians (despite the DAT type
    # code's historical "rotation/quaternion" label).
    angle = float(target_yaw)
    cos_yaw, sin_yaw = math.cos(angle), math.sin(angle)
    if semantics == SpatialSemantics.WORLD_POINT:
        x -= float(source_anchor[0])
        y -= float(source_anchor[1])
        z -= float(source_anchor[2])
        rx, rz = x * cos_yaw - z * sin_yaw, x * sin_yaw + z * cos_yaw
        return (rx + target_pos[0], y + target_pos[1], rz + target_pos[2])
    if semantics in {
        SpatialSemantics.DIRECTION,
        SpatialSemantics.LOCAL_OFFSET,
        SpatialSemantics.LOCAL_VELOCITY,
    }:
        return (x * cos_yaw - z * sin_yaw, y, x * sin_yaw + z * cos_yaw)
    if semantics == SpatialSemantics.EXTENT:
        return (
            abs(x * cos_yaw) + abs(z * sin_yaw),
            abs(y),
            abs(x * sin_yaw) + abs(z * cos_yaw),
        )
    return values


def _is_zero_vector(value: Sequence[float], epsilon: float = 1.0e-6) -> bool:
    return len(value) == 3 and all(abs(float(item)) <= epsilon for item in value)


def source_anchor_for_graph(graph: PrefabGraph, placement_anchor: str) -> Vec3:
    """Resolve an object-only placement anchor from authored object positions."""
    mode = str(placement_anchor or "original_origin").casefold()
    if mode == "original_origin":
        return (0.0, 0.0, 0.0)
    positioned_objects = []
    for obj in graph.runtime_objects:
        value = obj.property_value("Pos")
        if isinstance(value, (tuple, list)) and len(value) == 3:
            positioned_objects.append((obj, tuple(float(item) for item in value)))
    if not positioned_objects:
        return (0.0, 0.0, 0.0)
    if mode == "controller_pivot":
        moving = [
            position for obj, position in positioned_objects
            if obj.class_name.casefold() in _MOVING_CLASS_KEYS
        ]
        return moving[0] if moving else positioned_objects[0][1]
    positions = [position for _obj, position in positioned_objects]
    min_box = tuple(min(point[axis] for point in positions) for axis in range(3))
    max_box = tuple(max(point[axis] for point in positions) for axis in range(3))
    center = tuple((min_box[axis] + max_box[axis]) * 0.5 for axis in range(3))
    if mode == "bottom_center":
        return (center[0], min_box[1], center[2])
    if mode == "center":
        return center  # type: ignore[return-value]
    raise ValueError(f"unsupported behavioral placement anchor {placement_anchor!r}")


def source_anchor_for_analysis(
    analysis: PrefabAnalysis,
    placement_anchor: str,
) -> Vec3:
    """Resolve one placement pivot shared by passive objects and their BSP."""
    graph = analysis.graph
    mode = str(placement_anchor or "original_origin").casefold()
    if mode in {"original_origin", "controller_pivot"} or not graph.brushes:
        return source_anchor_for_graph(graph, placement_anchor)
    points = []
    if graph.source_format == "legacy_ed":
        source = legacy_ed.load_legacy_ed_analysis_bundle(graph.source_path)
        wanted = {
            brush.index for brush in graph.brushes
            if brush.ownership != "system" and brush.role != "portal"
        }
        for index, model in enumerate(source.geometry_scene.mesh_models()):
            if index in wanted:
                points.extend(tuple(float(value) for value in point) for point in model.points)
    elif graph.source_format == "compiled_dat":
        with open(graph.source_path, "rb") as handle:
            source_bsp = bsp.parse(handle.read())
        wanted = {
            brush.index for brush in graph.brushes
            if brush.ownership != "system" and brush.role != "portal"
        }
        for index, model in enumerate(source_bsp.world_models):
            if index in wanted:
                points.extend(tuple(float(value) for value in point) for point in model.points)
    if not points:
        return source_anchor_for_graph(graph, placement_anchor)
    min_box = tuple(min(point[axis] for point in points) for axis in range(3))
    max_box = tuple(max(point[axis] for point in points) for axis in range(3))
    center = tuple((min_box[axis] + max_box[axis]) * 0.5 for axis in range(3))
    if mode == "bottom_center":
        return (center[0], min_box[1], center[2])
    if mode == "center":
        return center  # type: ignore[return-value]
    raise ValueError(f"unsupported behavioral placement anchor {placement_anchor!r}")


def materialize_behavioral_plan(
    analysis: PrefabAnalysis,
    plan: BehavioralPrefabImportPlan,
    *,
    class_templates: Mapping[str, patcher.WorldObject],
    placement_anchor: str = "original_origin",
    object_overrides: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Tuple[patcher.WorldObject, ...]:
    """Build catalog-backed runtime objects for promoted Phase 2-6 plans."""
    plan.require_ready()
    graph = analysis.graph
    allowed = _PHASE6_CLASS_KEYS
    unsupported = sorted({
        obj.class_name for obj in graph.runtime_objects
        if obj.class_name.casefold() not in allowed
    }, key=str.casefold)
    if unsupported:
        raise ValueError(
            "behavioral materialization does not support: " + ", ".join(unsupported)
        )
    moving_policy = (
        _simple_moving_policy_diagnostics(graph)
        if _moving_objects(graph)
        and not (_is_phase5_graph(graph) or _is_phase6_graph(graph))
        else ()
    )
    if moving_policy:
        raise ValueError(moving_policy[0].message)
    template_by_class = {
        str(name).casefold(): value for name, value in class_templates.items()
    }
    planned_by_index = {item.source_index: item for item in plan.objects}
    reference_values = {
        (item.object_index, item.property_name.casefold()): item.target_value
        for item in plan.references
    }
    overrides = {
        str(key): dict(value) for key, value in (object_overrides or {}).items()
    }
    stale_rotation_points = {
        item.object_index
        for item in analysis.diagnostics
        if item.code == "behavioral_legacy_rotation_point_rebased"
        and item.object_index is not None
    }
    source_anchor = source_anchor_for_analysis(analysis, placement_anchor)
    created = []
    for source in graph.runtime_objects:
        planned = planned_by_index.get(source.index)
        if planned is None:
            raise ValueError(f"behavioral plan omitted source object {source.index}")
        template = template_by_class.get(source.class_name.casefold())
        if template is None:
            raise ValueError(f"missing catalog template for {source.class_name}")
        result = copy.deepcopy(template)
        target_props = {prop.name.casefold(): prop for prop in result.props}
        ignored = PHASE2_IGNORED_SOURCE_PROPERTIES.get(
            source.class_name.casefold(),
            frozenset(),
        )
        for source_prop in source.properties:
            key = source_prop.name.casefold()
            if key == "name":
                continue
            target_name = _target_property_name(source.class_name, source_prop.name)
            target_prop = target_props.get(target_name.casefold())
            if target_prop is None:
                if key in ignored:
                    if not _ignored_source_value_supported(
                        source.class_name, source_prop.name, source_prop.value
                    ):
                        expected = _IGNORED_SOURCE_PROPERTY_DEFAULTS.get(
                            (source.class_name.casefold(), key)
                        )
                        raise ValueError(
                            f"{source.class_name}.{source_prop.name}={source_prop.value!r} "
                            f"cannot retain the MM9 runtime default {expected!r}"
                        )
                    continue
                raise ValueError(
                    f"{source.class_name}.{source_prop.name} has no MM9 object.lto field"
                )
            if int(target_prop.code) != int(source_prop.type_code):
                raise ValueError(
                    f"{source.class_name}.{source_prop.name} type mismatch: "
                    f"ED={source_prop.type_code}, object.lto={target_prop.code}"
                )
            value = source_prop.value
            reference_value = reference_values.get((source.index, key))
            if reference_value is not None:
                value = reference_value
            if key == "rotationpoint" and source.index in stale_rotation_points:
                value = source.property_value("Pos", value)
            semantics = spatial_semantics_for(source.class_name, source_prop.name)
            if semantics != SpatialSemantics.NON_SPATIAL:
                if not isinstance(value, (tuple, list)):
                    raise ValueError(
                        f"{source.class_name}.{source_prop.name} is not a spatial sequence"
                    )
                # MM9 uses an all-zero SoundPos as a sentinel for the moving
                # controller position. Translating it would turn that sentinel
                # into an unrelated absolute point.
                if not (
                    key == "soundpos"
                    and source.class_name.casefold() in _MOVING_CLASS_KEYS
                    and _is_zero_vector(value)
                ):
                    value = transform_spatial_value(
                        semantics,
                        value,
                        target_pos=plan.target_pos,
                        target_yaw=plan.target_yaw,
                        source_anchor=source_anchor,
                    )
            result.set(target_prop.name, value)
        name_prop = target_props.get("name")
        if name_prop is None:
            raise ValueError(f"catalog template for {source.class_name} has no Name property")
        result.set(name_prop.name, planned.target_name)
        for name, value in overrides.get(str(source.index), {}).items():
            target_prop = target_props.get(str(name).casefold())
            if target_prop is None:
                raise ValueError(
                    f"override {source.class_name}.{name} has no MM9 object.lto field"
                )
            result.set(target_prop.name, value)
        created.append(result)
    for planned in plan.objects:
        if not planned.synthetic:
            continue
        template = template_by_class.get(planned.class_name.casefold())
        if template is None:
            raise ValueError(f"missing catalog template for synthetic {planned.class_name}")
        result = copy.deepcopy(template)
        target_props = {prop.name.casefold(): prop for prop in result.props}
        if "name" not in target_props:
            raise ValueError(
                f"catalog template for synthetic {planned.class_name} has no Name property"
            )
        result.set(target_props["name"].name, planned.target_name)
        if "pos" in target_props:
            result.set(target_props["pos"].name, tuple(float(value) for value in plan.target_pos))
        if "rotation" in target_props:
            result.set(
                target_props["rotation"].name,
                (0.0, float(plan.target_yaw), 0.0, 0.0),
            )
        if "movetofloor" in target_props:
            result.set(target_props["movetofloor"].name, 0)
        created.append(result)
    return tuple(created)


def materialize_passive_plan(
    analysis: PrefabAnalysis,
    plan: BehavioralPrefabImportPlan,
    **kwargs: Any,
) -> Tuple[patcher.WorldObject, ...]:
    """Backward-compatible Phase 2/3 entry point with a passive-only guard."""
    if not _is_passive_graph(analysis.graph):
        raise ValueError("passive materialization only supports passive assemblies")
    return materialize_behavioral_plan(analysis, plan, **kwargs)


def materialize_object_only_plan(
    analysis: PrefabAnalysis,
    plan: BehavioralPrefabImportPlan,
    **kwargs: Any,
) -> Tuple[patcher.WorldObject, ...]:
    """Backward-compatible Phase 2 entry point with its strict shape check."""
    if analysis.graph.brushes:
        raise ValueError("Phase 2 materialization only supports object-only prefabs")
    return materialize_passive_plan(analysis, plan, **kwargs)


def build_behavioral_bsp_import_plan(
    target_bsp: bsp.BspWorld,
    analysis: PrefabAnalysis,
    plan: BehavioralPrefabImportPlan,
    *,
    placement_anchor: str,
    allow_generated_bsp: bool = True,
    validate_runtime_bsp: bool = False,
) -> Optional[import_static.PrefabBspImportPlan]:
    """Compile/copy the role-preserving BSP portion of a Phase 3-6 assembly."""
    plan.require_ready()
    if not analysis.graph.brushes:
        return None
    if analysis.graph.source_format == "legacy_ed" and not allow_generated_bsp:
        raise ValueError(
            "Behavioral prefabs with DEdit ED brushes require a DEdit-compiled "
            "v66 DAT; only object-only ED prefabs can be imported directly."
        )
    if not _is_materializable_graph(analysis.graph):
        raise ValueError("BSP materialization is not enabled for this behavioral graph")
    moving_policy = (
        _simple_moving_policy_diagnostics(analysis.graph)
        if _moving_objects(analysis.graph)
        and not (_is_phase5_graph(analysis.graph) or _is_phase6_graph(analysis.graph))
        else ()
    )
    if moving_policy:
        raise ValueError(moving_policy[0].message)
    planned_by_index = {item.source_index: item for item in plan.brushes}
    grouped: Dict[Tuple[str, str], list[int]] = {}
    for brush in analysis.graph.brushes:
        if brush.ownership == "system" or brush.role == "portal":
            continue
        planned = planned_by_index.get(brush.index)
        if planned is None:
            raise ValueError(f"behavioral plan omitted source brush {brush.index}")
        grouped.setdefault((planned.target_name, planned.role), []).append(brush.index)
    groups = [
        import_static.PrefabBrushImportGroup(
            target_name=name,
            source_indices=tuple(indices),
            role=role,
        )
        for (name, role), indices in grouped.items()
    ]
    if not groups:
        return None
    return import_static.build_grouped_import_plan(
        target_bsp,
        analysis.graph.source_path,
        groups,
        target_pos=plan.target_pos,
        target_yaw=plan.target_yaw,
        source_pivot=source_anchor_for_analysis(analysis, placement_anchor),
        placement_anchor=placement_anchor,
        allow_generated_bsp=allow_generated_bsp,
        validate_runtime_bsp=validate_runtime_bsp,
    )


def validate_door_import_parity(
    analysis: PrefabAnalysis,
    plan: BehavioralPrefabImportPlan,
    created_objects: Sequence[patcher.WorldObject],
    bsp_plan: Optional[import_static.PrefabBspImportPlan],
) -> Tuple[str, ...]:
    """Check the structural guarantees that replaced new same-level clones.

    Behavioral import is not expected to produce byte-identical records to a
    door copied from an unrelated target level.  Parity means every authored
    moving controller keeps its class/properties, owns complete same-named BSP,
    receives the common placement transform, and has all pair/link targets
    resolved by the deterministic import plan.
    """
    movers = _moving_objects(analysis.graph)
    if not movers:
        return ()
    issues = []
    planned_objects = {item.source_index: item for item in plan.objects}
    planned_brushes = {item.source_index: item for item in plan.brushes}
    created_by_name = {
        str(obj.get("Name") or "").casefold(): obj for obj in created_objects
    }
    bsp_names = {
        str(getattr(item, "new_name", "") or "").casefold()
        for item in (bsp_plan.submodels if bsp_plan is not None else ())
    }
    for source in movers:
        planned = planned_objects.get(source.index)
        label = source.source_name or source.class_name
        if planned is None:
            issues.append(f"{label}: controller is missing from the object plan")
            continue
        created = created_by_name.get(planned.target_name.casefold())
        if created is None:
            issues.append(f"{label}: controller {planned.target_name!r} was not materialized")
        elif created.type_str.casefold() != source.class_name.casefold():
            issues.append(
                f"{label}: controller class changed from {source.class_name} "
                f"to {created.type_str}"
            )
        if not source.owned_brush_indices:
            issues.append(f"{label}: moving controller has no owned BSP")
            continue
        missing_brushes = []
        wrong_names = []
        for index in source.owned_brush_indices:
            brush = planned_brushes.get(index)
            if brush is None:
                missing_brushes.append(index)
            elif brush.target_name.casefold() != planned.target_name.casefold():
                wrong_names.append(brush.target_name)
        if missing_brushes:
            issues.append(
                f"{label}: owned brush groups are missing from the BSP plan: "
                + ", ".join(str(value) for value in missing_brushes)
            )
        if wrong_names:
            issues.append(
                f"{label}: owned BSP does not share the controller name: "
                + ", ".join(sorted(set(wrong_names), key=str.casefold))
            )
        if bsp_plan is None or planned.target_name.casefold() not in bsp_names:
            issues.append(
                f"{label}: same-named BSP model {planned.target_name!r} was not compiled"
            )

    for ref in plan.references:
        key = ref.property_name.casefold()
        if key != "doubledoorname" and not _REFERENCE_PATTERN.match(ref.property_name):
            continue
        if not ref.target_value:
            issues.append(
                f"{ref.property_name}={ref.source_value!r} has no resolved target"
            )
    return tuple(issues)


def build_passive_bsp_import_plan(
    target_bsp: bsp.BspWorld,
    analysis: PrefabAnalysis,
    plan: BehavioralPrefabImportPlan,
    *,
    placement_anchor: str,
) -> Optional[import_static.PrefabBspImportPlan]:
    """Backward-compatible Phase 3 entry point with a passive-only guard."""
    if not _is_passive_graph(analysis.graph):
        raise ValueError("passive BSP materialization only supports passive assemblies")
    return build_behavioral_bsp_import_plan(
        target_bsp,
        analysis,
        plan,
        placement_anchor=placement_anchor,
    )


def _load_dat_graph(
    path: str,
    data: bytes,
    fingerprint: str,
    resource_exists: Optional[Callable[[str, str], Optional[bool]]],
) -> PrefabGraph:
    world = patcher.World.load(path)
    bsp_world = bsp.parse(data)
    objects = tuple(
        PrefabObject(
            index=index,
            class_name=obj.type_str,
            source_name=str(obj.get("Name") or ""),
            properties=tuple(
                PrefabProperty(prop.name, int(prop.code), _TYPE_NAMES.get(int(prop.code), "unknown"), int(prop.flags), prop.value)
                for prop in obj.props
            ),
        )
        for index, obj in enumerate(world.objects)
    )
    object_by_name = {
        obj.source_name.casefold(): obj.index for obj in objects if obj.source_name
    }
    brush_groups = []
    owned_by_object: Dict[int, list[int]] = {}
    info_objects = [
        inspector.PrefabObjectInfo(obj.index, obj.class_name, obj.source_name)
        for obj in objects
    ]
    for index, model in enumerate(bsp_world.world_models):
        role = inspector.classify_model(model, info_objects)
        owner = (
            object_by_name.get(str(model.name).casefold())
            if role not in {"physics", "visibility"}
            else None
        )
        if owner is not None:
            owner_object = objects[owner]
            role = _PASSIVE_OWNED_BSP_ROLES.get(
                owner_object.class_name.casefold(),
                "controller_geometry",
            )
        ownership = "owned" if owner is not None else "system" if role in {"physics", "visibility"} else "unowned"
        brush_groups.append(PrefabBrushGroup(
            index=index,
            source_name=str(model.name or ""),
            role=role,
            owner_object_index=owner,
            ownership=ownership,
            polygon_count=len(model.polygons),
        ))
        if owner is not None:
            owned_by_object.setdefault(owner, []).append(index)
    objects = tuple(
        replace(obj, owned_brush_indices=tuple(owned_by_object.get(obj.index, ())))
        for obj in objects
    )
    references = _extract_references(objects, {brush.source_name for brush in brush_groups})
    dependencies = _extract_dependencies(objects, resource_exists)
    return PrefabGraph(
        source_path=path,
        source_format="compiled_dat",
        source_version=66,
        source_fingerprint=fingerprint,
        objects=objects,
        brushes=tuple(brush_groups),
        references=references,
        dependencies=dependencies,
    )


def _load_ed_graph(
    path: str,
    data: bytes,
    fingerprint: str,
    resource_exists: Optional[Callable[[str, str], Optional[bool]]],
) -> PrefabGraph:
    bundle = legacy_ed.analyze_legacy_ed_bytes(data, source_path=path)
    node_tree = bundle.node_tree
    diagnostics = []
    if node_tree is None:
        diagnostics.append(PrefabDiagnostic(
            "source_node_hierarchy_unavailable",
            DiagnosticSeverity.BLOCKING,
            "The recursive DEdit node hierarchy could not be decoded; brush ownership is unavailable.",
        ))
        objects = tuple(
            PrefabObject(
                index=index,
                class_name=record.class_name,
                source_name=str(record.property_value("Name") or ""),
                properties=tuple(
                    PrefabProperty(prop.name, prop.type_code, prop.type_name, prop.flags, prop.value)
                    for prop in record.properties
                ),
            )
            for index, record in enumerate(bundle.object_scan.records)
        )
        brush_nodes: Dict[int, Tuple[legacy_ed.LegacyEdNode, Optional[int]]] = {}
    else:
        object_nodes = []

        def collect_objects(node: legacy_ed.LegacyEdNode) -> None:
            if node.class_name and node.class_name.casefold() != "brush":
                object_nodes.append(node)
            for child in node.children:
                collect_objects(child)

        collect_objects(node_tree)
        object_index_by_node = {id(node): index for index, node in enumerate(object_nodes)}
        objects = tuple(
            PrefabObject(
                index=index,
                class_name=node.class_name,
                source_name=str(node.property_value("Name") or ""),
                properties=tuple(
                    PrefabProperty(prop.name, prop.type_code, prop.type_name, prop.flags, prop.value)
                    for prop in node.properties
                ),
            )
            for index, node in enumerate(object_nodes)
        )
        brush_nodes = {}

        def collect_brushes(
            node: legacy_ed.LegacyEdNode,
            owner_index: Optional[int] = None,
        ) -> None:
            node_index = object_index_by_node.get(id(node))
            if node_index is not None:
                source = objects[node_index]
                if source.class_name.casefold() != "worldproperties":
                    owner_index = node_index
            if node.node_type == legacy_ed.NODE_BRUSH and node.brush_index is not None:
                brush_nodes[int(node.brush_index)] = (node, owner_index)
            for child in node.children:
                collect_brushes(child, owner_index)

        collect_brushes(node_tree)

    object_by_index = {obj.index: obj for obj in objects}
    brush_groups = []
    for index, model in enumerate(bundle.geometry_scene.mesh_models()):
        node_entry = brush_nodes.get(index)
        node = node_entry[0] if node_entry is not None else None
        owner_index = node_entry[1] if node_entry is not None else None
        name = str(node.property_value("Name") or "") if node is not None else str(model.name or "")
        owner = object_by_index.get(owner_index) if owner_index is not None else None
        role = _ed_brush_role(node, model, owner)
        brush_groups.append(PrefabBrushGroup(
            index=index,
            source_name=name,
            role=role,
            owner_object_index=owner_index,
            ownership=(
                "owned" if owner_index is not None
                else "unowned" if node is not None
                else "unresolved"
            ),
            polygon_count=len(model.faces),
        ))
    owned_by_object: Dict[int, list[int]] = {}
    for brush in brush_groups:
        if brush.owner_object_index is not None:
            owned_by_object.setdefault(brush.owner_object_index, []).append(brush.index)
    objects = tuple(
        replace(obj, owned_brush_indices=tuple(owned_by_object.get(obj.index, ())))
        for obj in objects
    )
    diagnostics.extend(_legacy_stale_rotation_point_diagnostics(
        objects,
        brush_groups,
        bundle.geometry_scene.mesh_models(),
    ))
    references = _extract_references(objects, {brush.source_name for brush in brush_groups})
    dependencies = _extract_dependencies(objects, resource_exists)
    if bundle.node_layout.blockers:
        diagnostics.append(PrefabDiagnostic(
            "source_node_layout_blockers",
            DiagnosticSeverity.WARNING,
            "; ".join(bundle.node_layout.blockers),
        ))
    return PrefabGraph(
        source_path=path,
        source_format="legacy_ed",
        source_version=legacy_ed.LEGACY_ED_VERSION,
        source_fingerprint=fingerprint,
        objects=objects,
        brushes=tuple(brush_groups),
        references=references,
        dependencies=dependencies,
        diagnostics=tuple(diagnostics),
    )


def _ed_brush_role(
    node: Optional[legacy_ed.LegacyEdNode],
    model: Any,
    owner: Optional[PrefabObject],
) -> str:
    if owner is not None:
        owned_role = _PASSIVE_OWNED_BSP_ROLES.get(owner.class_name.casefold())
        if owned_role:
            return owned_role
        if owner.class_name.casefold() in (
            _MOVING_CLASS_KEYS | {"destructablebrush"}
        ):
            # DEdit compiles the complete brush subtree below one moving
            # controller into a single same-named world model. Individual
            # child brushes may use collision/hidden marker materials, but
            # splitting those roles would create duplicate controller model
            # names and break the engine's object-to-BSP lookup.
            return "controller_geometry"
    if node is not None and bool(node.property_value("Portal", False)):
        return "portal"
    if node is not None and bool(node.property_value("SkyPortal", False)):
        return "sky_visibility"
    if node is not None and bool(node.property_value("Invisible", False)):
        return "hidden_geometry"
    materials = {
        str(getattr(face, "material_name", "") or "").replace("/", "\\").casefold()
        for face in getattr(model, "faces", ()) or ()
    }
    if any("\\water\\" in name or "watermarker.dtx" in name for name in materials):
        return "water"
    if any(name.endswith("\\rail.dtx") for name in materials):
        return "ai_rail"
    if any(name.endswith(("\\invisible.dtx", "\\firethrough.dtx")) for name in materials):
        return "collision"
    if any(name.endswith("\\greenscreen.dtx") for name in materials):
        return "trigger"
    if any(name.endswith("\\soundonly.dtx") for name in materials):
        return "sound"
    if any(name.endswith("\\skymarker.dtx") for name in materials):
        return "sky_visibility"
    return "controller_geometry" if owner is not None else "geometry"


def _legacy_stale_rotation_point_diagnostics(
    objects: Sequence[PrefabObject],
    brushes: Sequence[PrefabBrushGroup],
    models: Sequence[Any],
) -> Tuple[PrefabDiagnostic, ...]:
    """Identify point properties left in the source world's coordinate space.

    Legacy DEdit's Save As Prefab path offsets node positions and brush points
    relative to the editor marker, but its prefab instantiation code only
    transforms the standard Pos/Rotation pair. RotationPoint therefore remains
    absolute in a number of shipped MM9 prefabs. A pivot far outside all BSP
    owned by its controller cannot describe that local assembly; Phase 4 uses
    the controller Pos as the local hinge in that case.
    """
    diagnostics = []
    brush_by_owner: Dict[int, list[int]] = {}
    for brush in brushes:
        if brush.owner_object_index is not None:
            brush_by_owner.setdefault(brush.owner_object_index, []).append(brush.index)
    for obj in objects:
        if obj.class_name.casefold() not in _MOVING_CLASS_KEYS:
            continue
        pivot = obj.property_value("RotationPoint")
        position = obj.property_value("Pos")
        if not (
            isinstance(pivot, (tuple, list)) and len(pivot) == 3
            and isinstance(position, (tuple, list)) and len(position) == 3
        ):
            continue
        points = []
        for index in brush_by_owner.get(obj.index, ()):
            if 0 <= int(index) < len(models):
                points.extend(getattr(models[int(index)], "points", ()) or ())
        if not points:
            continue
        minimum = tuple(min(float(point[axis]) for point in points) for axis in range(3))
        maximum = tuple(max(float(point[axis]) for point in points) for axis in range(3))
        diagonal = math.sqrt(sum(
            (maximum[axis] - minimum[axis]) ** 2 for axis in range(3)
        ))
        outside = math.sqrt(sum(
            (
                minimum[axis] - float(pivot[axis])
                if float(pivot[axis]) < minimum[axis]
                else float(pivot[axis]) - maximum[axis]
                if float(pivot[axis]) > maximum[axis]
                else 0.0
            ) ** 2
            for axis in range(3)
        ))
        if outside <= max(128.0, diagonal * 2.0):
            continue
        diagnostics.append(PrefabDiagnostic(
            "behavioral_legacy_rotation_point_rebased",
            DiagnosticSeverity.WARNING,
            f"{obj.source_name or obj.class_name} has a legacy RotationPoint outside its "
            "owned BSP; the imported controller position will be used as its local pivot.",
            object_index=obj.index,
        ))
    return tuple(diagnostics)


def _extract_references(
    objects: Sequence[PrefabObject],
    extra_local_names: Set[str],
) -> Tuple[PrefabReference, ...]:
    object_names = {
        obj.source_name.casefold(): obj.index for obj in objects if obj.source_name
    }
    local_names = set(object_names) | {str(value).casefold() for value in extra_local_names if value}
    references = []
    for obj in objects:
        for prop in obj.properties:
            key = prop.name.casefold()
            if key not in _REFERENCE_NAMES and not _REFERENCE_PATTERN.match(prop.name):
                continue
            value = str(prop.value or "").strip()
            if not value:
                continue
            folded = value.casefold()
            references.append(PrefabReference(
                object_index=obj.index,
                property_name=prop.name,
                source_value=value,
                target_kind="local" if folded in local_names else "external",
                target_object_index=object_names.get(folded),
            ))
    return tuple(references)


def _extract_dependencies(
    objects: Sequence[PrefabObject],
    resource_exists: Optional[Callable[[str, str], Optional[bool]]],
) -> Tuple[PrefabDependency, ...]:
    found: Dict[Tuple[int, str, str], PrefabDependency] = {}
    for obj in objects:
        for prop in obj.properties:
            if not isinstance(prop.value, str):
                continue
            for raw_value in prop.value.split(";"):
                path = raw_value.strip().replace("/", "\\")
                extension = os.path.splitext(path)[1].casefold()
                resource_type = _RESOURCE_EXTENSIONS.get(extension)
                if not resource_type:
                    continue
                if extension == ".dtx" and "skin" in prop.name.casefold():
                    resource_type = "skin"
                path = _canonical_resource_path(resource_type, path)
                availability = "unchecked"
                if resource_exists is not None:
                    result = resource_exists(resource_type, path)
                    availability = "available" if result is True else "missing" if result is False else "unchecked"
                item = PrefabDependency(
                    object_index=obj.index,
                    property_name=prop.name,
                    resource_type=resource_type,
                    path=path,
                    availability=availability,
                )
                found[(obj.index, prop.name.casefold(), path.casefold())] = item
    return tuple(found[key] for key in sorted(found))


def _canonical_resource_path(resource_type: str, path: str) -> str:
    normalized = str(path or "").strip().replace("/", "\\").lstrip("\\")
    root = _RESOURCE_ROOTS.get(resource_type, "")
    first = normalized.split("\\", 1)[0].casefold() if normalized else ""
    known_roots = {value.casefold() for value in _RESOURCE_ROOTS.values()}
    if root and first not in known_roots:
        normalized = f"{root}\\{normalized}"
    return normalized


def _missing_catalog_classes(
    objects: Sequence[PrefabObject],
    catalog: Optional[Mapping[str, Any]],
) -> Tuple[str, ...]:
    if catalog is None:
        return ()
    classes = catalog.get("classes")
    if not isinstance(classes, Mapping):
        return tuple(sorted({obj.class_name for obj in objects}, key=str.casefold))
    class_keys = {str(value).casefold() for value in classes}
    return tuple(sorted(
        {obj.class_name for obj in objects if obj.class_name.casefold() not in class_keys},
        key=str.casefold,
    ))


def _catalog_property_issues(
    objects: Sequence[PrefabObject],
    catalog: Optional[Mapping[str, Any]],
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    if catalog is None:
        return (), ()
    classes = catalog.get("classes")
    if not isinstance(classes, Mapping):
        return (), ()
    entries = {str(name).casefold(): value for name, value in classes.items()}
    blockers = []
    ignored_names = set()
    for obj in objects:
        entry = entries.get(obj.class_name.casefold())
        if not isinstance(entry, Mapping):
            continue
        object_lto = entry.get("object_lto")
        items = object_lto.get("template_properties") if isinstance(object_lto, Mapping) else None
        if not isinstance(items, list):
            continue
        target_props = {
            str(item.get("name") or "").casefold(): item
            for item in items
            if isinstance(item, Mapping) and item.get("name")
        }
        ignored = PHASE2_IGNORED_SOURCE_PROPERTIES.get(
            obj.class_name.casefold(),
            frozenset(),
        )
        for prop in obj.properties:
            key = prop.name.casefold()
            target_name = _target_property_name(obj.class_name, prop.name)
            target = target_props.get(target_name.casefold())
            if target is None:
                if key in ignored:
                    if _ignored_source_value_supported(
                        obj.class_name, prop.name, prop.value
                    ):
                        ignored_names.add(f"{obj.class_name}.{prop.name}")
                    else:
                        expected = _IGNORED_SOURCE_PROPERTY_DEFAULTS.get(
                            (obj.class_name.casefold(), key)
                        )
                        blockers.append(
                            f"{obj.class_name}.{prop.name}={prop.value!r} "
                            f"cannot use runtime default {expected!r}"
                        )
                else:
                    blockers.append(f"{obj.class_name}.{prop.name} is absent")
                continue
            try:
                target_code = int(target.get("code"))
            except (TypeError, ValueError):
                blockers.append(f"{obj.class_name}.{prop.name} has no target type")
                continue
            if target_code != int(prop.type_code):
                blockers.append(
                    f"{obj.class_name}.{prop.name} type {prop.type_code}!={target_code}"
                )
    return (
        tuple(sorted(set(blockers), key=str.casefold)),
        tuple(sorted(ignored_names, key=str.casefold)),
    )


def _target_property_name(class_name: str, source_property_name: str) -> str:
    aliases = SOURCE_PROPERTY_ALIASES.get(str(class_name).casefold(), {})
    return aliases.get(str(source_property_name).casefold(), source_property_name)


def _ignored_source_value_supported(
    class_name: str,
    property_name: str,
    value: Any,
) -> bool:
    key = (str(class_name).casefold(), str(property_name).casefold())
    if key not in _IGNORED_SOURCE_PROPERTY_DEFAULTS:
        return True
    expected = _IGNORED_SOURCE_PROPERTY_DEFAULTS[key]
    if isinstance(expected, bool):
        return bool(value) is expected
    if isinstance(expected, (int, float)) and isinstance(value, (int, float)):
        return abs(float(value) - float(expected)) <= 1.0e-6
    return value == expected


def _sanitize_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip())
    return text.strip("_")


def _allocate_name(base: str, used: Set[str]) -> str:
    candidate = base or "ImportedPrefab"
    suffix = 2
    while candidate.casefold() in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate
