# Runtime-Backed Prefab Import Implementation Plan

> **Archived design — non-normative.** This document explains the safety
> boundary introduced after behavioral prefab import was implemented. The
> catalog-backed `Prop` route exists; compiled-assembly archetype indexing and
> broader runtime acceptance described below are design work, not promises of
> current editor behavior. See `docs/user-guide/prefab-import.md`.

Status: hybrid safety boundary and initial resource-backed `Prop` route
implemented on 2026-08-14; compiled-assembly archetype indexing and in-game
door acceptance remain planned.

This plan extends the existing prefab workspace so authored brush geometry can
be used as a placement and matching reference without forcing that geometry
through the editor's incomplete additive BSP compiler. It covers the immediate
resource-backed `Prop` case and defines a separate, compatible route for doors
and other objects whose runtime representation is compiled moving BSP.

The feature should be presented as one import workflow, but it must not pretend
that all MM9 objects use the same storage model.

## Decision summary

Use a per-component **runtime representation** decision:

| Representation | Runtime output | Initial object families |
| --- | --- | --- |
| Game model object | Catalog-backed object with `Filename`/`Skin`; no imported BSP | `Prop`, `DestructableProp` |
| Compiled BSP assembly | Controller object(s) plus validated, copied compiled BSP record(s) | `Door`, `RotatingDoor` |
| Native object graph | Existing object-only/behavioral materialization | Lights, sounds, torches, triggers, existing resource objects |
| Newly compiled BSP | Controller plus newly compiled brush geometry | Blocked for game output until a real MM9/DEdit-equivalent compiler exists |

Consequently, the resource-backed design can be extended to other object types,
but doors are not resource-backed in the `Prop` sense. Stock doors do not use
an ABC `Filename` and skin. A physical MM9 door is a `Door` or `RotatingDoor`
controller paired with same-named compiled BSP. To import a door exactly like an
existing door, the editor must copy a complete, validated compiled assembly and
rewrite its identity and placement atomically.

An arbitrary ED brush door cannot yet be made runtime-equivalent merely by
changing its class. It needs either:

1. a selected stock/precompiled door assembly to replace it; or
2. a future real world-model compiler capable of producing the BSP tree,
   physics block table, and other runtime sections expected by MM9.

## Why a new layer is needed

The implemented Phase-2 prefab path supports resource-backed objects only when
the source already contains runtime objects. A brush-only prefab is currently
blocked as `behavioral_no_objects`, so a brush-only Bookcase cannot choose the
catalog-backed object route.

The current additive serializer emits a deliberately minimal v66 world model:
it has polygons and points but no leaves or BSP nodes, an empty physics block
table, and a root-node value of `-1`. That data is useful for editor preview and
format research, but it is not a safe runtime representation. The importer must
therefore stop treating every authored brush as BSP that can be installed into
the game.

The MM9 catalog already provides most of the data needed for model-object
replacement:

- object.lto-derived class templates and defaults;
- model-to-class and model-to-skin variants;
- usage counts and source level/object provenance;
- game-resource availability checks; and
- ABC model bounds and user dimensions in the viewport resource pipeline.

For example, the catalog records `models\props\Bookcase02EW.abc` as a `Prop`
using `skins\props\Bookcase02.dtx`, with 45 stock uses across 11 levels. The NS
variant is also widely used. These are strong candidates, but the source brush
and stock model have different bounds, so the choice and scale must remain
visible and reviewable rather than being silently substituted.

## Target architecture

### 1. Runtime representation plan

Extend the canonical prefab plan with explicit, persisted representation
records. Suggested types:

```text
RepresentationKind
  NATIVE_OBJECT
  MODEL_OBJECT
  COMPILED_BSP_ASSEMBLY
  GENERATED_BSP_PREVIEW

PrefabRepresentationBinding
  source_object_indices
  source_brush_indices
  representation_kind
  candidate_id
  target_class
  target_resources
  source_bounds
  target_bounds
  placement_policy
  property_profile
  provenance
  fingerprint
```

The binding is part of the deterministic import plan and project operation. It
must not be inferred again during save. Reopening a project must reproduce the
same objects and BSP bytes or fail with an actionable stale-source diagnostic.

Keep `ImportBehavioralPrefabOp` compatible with existing projects and add
optional representation bindings to it. Bump the planner and project format.
Old operations retain their current behavior; new brush-replacement plans use
the explicit bindings. This avoids adding another top-level import command and
keeps preview, editing, undo, deletion, and save atomic.

### 2. Class capability registry

Do not decide support merely from the presence of a `Filename` property. Many
actor and inventory classes inherit model-related properties but also require
runtime-specific state. Add an allowlisted registry:

```text
ClassImportCapability
  representation kinds allowed
  required resources
  safe source-property overlays
  required defaults/profiles
  placement semantics
  collision semantics
  owned-BSP requirement
  external-link policy
  runtime acceptance status
```

Initial policies:

- `Prop`: game-model replacement, with `Filename`, `Skin`, `Scale`, `Solid`,
  `Gravity`, `RayHit`, `Visible`, `Shadow`, and `MoveToFloor` reviewed.
- `DestructableProp`: same representation, plus a reviewed destruction/damage
  profile and required effect/resource dependencies.
- `Door` and `RotatingDoor`: compiled-assembly only; no model-object fallback.
- Existing object-only families continue through native behavioral import.
- NPC/AI, inventory, treasure, weapons, and scripted specializations remain
  blocked from generic brush replacement until they have dedicated policies.

### 3. Resource candidate index

Add a catalog-builder section for `resource_replacement_candidates`. It should
combine:

- `filenames` usage/class/level data;
- `model_variants` model/skin/source-instance data;
- object.lto class hierarchy and defaults;
- a small snapshot of safe properties from representative stock instances;
- ABC bounds/user dimensions; and
- verified resource presence in MODELS.REZ and SKINS.REZ.

Candidate discovery can use source node names, brush/group names, texture names,
bounds, aspect ratio, and curated aliases. Matching produces ranked suggestions,
not an automatic conversion. A candidate has a stable ID, confidence reasons,
and provenance such as `BOOTCAMP.DAT:Bookcase57`.

Curated mappings should live in data, not code. They are overrides for names
that cannot be matched reliably and can be extended without changing the
importer.

### 4. Compiled assembly archetype index

Add a separate `compiled_assembly_archetypes` catalog section. A door archetype
contains:

- all controller objects in the owned assembly;
- every same-named raw BSP record;
- controller class and complete property snapshot;
- internal links, paired-door membership, portal requirements, and external
  references;
- bounds, pivot, motion vectors/angles, and collision flags;
- source archive, level, object names, and hashes; and
- a semantic BSP-validation result.

Build the index offline with the catalog rather than scanning every MM9 world
when the workspace opens. Support stock MM9 levels first. Later, allow an
external DEdit-processed DAT as an archetype source and copy its required
records into a fingerprinted project asset so the project remains reproducible.

## Import workspace changes

Keep one `Tools -> Import Prefab` workspace. After source analysis, show a
**Runtime representation** section for each unresolved component:

- **Use a game model** — recommended when a matching stock model exists;
- **Use a compiled assembly** — required for stock-style doors and movers;
- **Use authored runtime objects** — existing behavioral route; and
- **BSP preview only** — visible in the editor but blocked from game save.

For a game-model candidate show:

- target class, model, skin(s), and resource availability;
- source usage count and representative stock instances;
- source-brush versus ABC bounds and recommended scale;
- EW/NS or similar variants side by side;
- final `Solid`, `Gravity`, `RayHit`, and `MoveToFloor` policy; and
- a viewport preview of the actual target model, optionally with the source
  brush as a translucent comparison envelope.

For a compiled door candidate show:

- door class, single/double assembly, dimensions, pivot, and motion;
- source level and object provenance;
- portal and external-link requirements;
- supported transforms and any restriction; and
- a preview of the copied compiled BSP, not a recompilation of the ED brush.

No candidate is applied silently. Import remains disabled until every source
component either has a valid runtime representation or is explicitly excluded.
Exclusion means omission, never hidden fallback to unsafe static BSP.

## Materialization rules

### Model-object replacement

1. Treat selected source brush geometry as a matching/placement reference only.
2. Create the target object from its object.lto catalog template.
3. Overlay the selected, allowlisted stock property profile and chosen resources.
4. Apply the import name, position, yaw, scale, and placement policy.
5. Emit no world-model record for the replaced brush.
6. Validate all resources before placement and again before save.
7. Persist the exact candidate ID, properties, resource paths, and source/target
   bounds in the project operation.

The initial Bookcase acceptance case should offer at least Bookcase02EW and
Bookcase02NS, preview both actual ABCs, and default only when the match is high
confidence. `MoveToFloor` should follow the selected stock profile; the source
brush origin must not be assumed to equal the ABC foot point.

### Compiled door assembly replacement

1. Select a validated stock/precompiled assembly; do not compile the ED brush.
2. Copy the complete controller object(s) and raw BSP record(s).
3. Allocate a deterministic namespace and give each controller and its owned BSP
   the same rewritten name.
4. Rewrite internal pair, attachment, and trigger links.
5. Require explicit bindings or omission for portal/external references.
6. Transform controller positions, `RotationPoint`, `MoveDir`, `SoundPos`, BSP
   bounds, points, planes, UV projections, polygon centers, and all spatial BSP
   metadata as one operation.
7. Preserve the selected door's behavior profile unless the user chooses a
   separately validated compatible profile.
8. Emit the assembly atomically and validate it after DAT reopen.

"Exactly like an existing door" should initially mean cloning the selected
existing assembly's geometry and behavior, with only identity, placement, and
explicit external bindings changed. Mixing arbitrary ED behavior with unrelated
stock door geometry is a later capability and needs compatibility validation.

The current raw-BSP transformer already handles names, bounds, planes, surfaces,
polygon centers, points, and normals. Before it becomes the general door import
path, extend it to locate, transform/rebuild, and validate the trailing physics
block table. Scaling remains disabled until every affected BSP section is proven
correct. Translation and yaw are enabled only for archetypes that pass the new
post-transform semantic validator.

## Phased implementation

### Phase 0 - Runtime BSP safety interlock

Goal: prevent another invalid world from reaching the game.

- Mark records produced by the minimal additive compiler as editor-preview-only.
- Add a save/install validation error for newly generated polygonal world models
  with no valid node tree/root or required physics structure.
- Keep source terrain and untouched stock records exempt through provenance, not
  loose shape guesses.
- Report the two safe alternatives: select a game-model replacement or provide a
  validated precompiled assembly.
- Add the failed Bookcase import as a regression fixture.

Exit gate: the unsafe brush-based Bookcase plan cannot be installed, while an
unchanged stock level still saves and installs.

### Phase 1 - Representation model and persistence

- Add representation bindings, capability policies, and provenance records.
- Teach analysis that a brush-only prefab can become behaviorally ready only
  after every brush has an explicit supported binding.
- Bump planner/project versions and add migration/round-trip tests.
- Ensure preview, move, yaw edit, delete, undo/redo, and save all consume the same
  stored resolution.

Exit gate: deterministic plans survive project reopen without rematching.

### Phase 2 - Catalog candidate builders

- Generate resource-replacement candidates and safe stock profiles.
- Generate compiled-assembly archetypes for stock `Door`/`RotatingDoor` sets.
- Add ABC bounds and semantic BSP audit summaries.
- Add curated alias/mapping data and a corpus audit for unresolved or ambiguous
  prefab names.
- Cache by archive/catalog fingerprints so startup and workspace analysis remain
  fast.

Exit gate: Bookcase candidates and representative single, rotating, and paired
doors resolve deterministically from a stock installation.

### Phase 3 - Workspace and preview

- Add the representation selector and candidate browser to the existing import
  workspace.
- Render the actual resource model or compiled BSP candidate in the viewport.
- Add bounds comparison, scale controls, provenance, confidence, and blocking
  diagnostics.
- Preserve existing Static/Full behavior choices for sources that already have a
  valid representation; do not create another Tools-menu command.

Exit gate: the user can understand exactly what will be emitted before placing
the prefab.

### Phase 4 - Resource-backed Prop import

- Materialize `Prop` replacements without any BSP output.
- Add `DestructableProp` only after its damage/death profile tests pass.
- Implement Bookcase EW/NS selection, floor placement, resource validation, and
  source-envelope preview.
- Save, reopen, and install through the normal project pipeline.

Exit gate: importing Bookcase into BOOTCAMP adds one valid stock-style `Prop`,
does not increase the world-model count, and BOOTCAMP loads in MM9.

### Phase 5 - Compiled assembly import engine

- Generalize the retained door-clone/raw-record code into a source-independent
  compiled-assembly materializer.
- Parse and validate the entire raw record, including BSP nodes/root and physics
  block table.
- Complete transform support for all spatial sections; block unsupported scale
  or transform combinations.
- Support stock archive provenance and project-local snapshots for external
  compiled sources.

Exit gate: copied records remain semantically valid after translation/yaw and
DAT reopen, with byte differences limited to the documented transformed fields,
names, and record links.

### Phase 6 - Doors

- Enable single `Door`, then `RotatingDoor`, followed by paired doors.
- Preserve complete controller behavior and same-named BSP ownership.
- Validate pivots, movement, collision, sounds, pair links, trigger links, portal
  binding, and duplicate imports.
- Compare each generated assembly with its selected stock archetype and run
  manual in-game open/close/collision acceptance tests.

Exit gate: imported doors render, collide, open, close, save, and reload exactly
like their selected stock door archetypes.

### Phase 7 - Other families and compiler boundary

- Extend compiled-assembly policies, one family at a time, to `Lift`,
  `RotatingBrush`, and `DestructableBrush` after dedicated runtime tests.
- Extend model-object policies to reviewed Prop descendants such as
  `PropDamager`, `RollingProp`, and special prop classes only when their extra
  behavior and dependencies are understood.
- Keep inventory, treasure, actor/NPC, weapon, and scripted classes behind
  specialized policies; having a model property alone is insufficient.
- Integrate a real DEdit/MM9 world-model compiler if arbitrary new BSP shapes are
  required. Only then remove the preview-only restriction for generated BSP.

Exit gate: every enabled class family has a representation-specific corpus audit
and in-game acceptance fixture; unsupported classes continue to fail closed.

## Validation and tests

### Fast automated tests

- deterministic candidate ranking and stable candidate IDs;
- class-policy allowlist and required-property checks;
- resource existence and model/skin pairing;
- source/target bounds and scale calculation;
- project serialization and stale fingerprint behavior;
- unique namespaces and internal/external link rewriting;
- replaced brushes emit no BSP records; and
- exclusion never falls back to static import.

### BSP semantic tests

- reject the current zero-node, root-`-1`, empty-physics Bookcase record;
- require valid node references and root indices for copied polygonal submodels;
- parse, transform, and reparse physics block tables;
- confirm every moving controller owns a same-named BSP;
- reject unsupported scaling and transforms before save; and
- verify record-chain offsets and DAT terminal-tail preservation.

### Integration fixtures

- brush-only Bookcase -> Bookcase02EW/NS `Prop` in BOOTCAMP;
- one stock sliding `Door`;
- one `RotatingDoor` with a non-central hinge;
- paired doors with rewritten `DoubleDoorName`;
- a portal-backed door using both explicit binding and `<omit>` policies;
- two imports of the same door with no cross-links; and
- save/reopen plus manual MM9 load, render, collision, and behavior checks.

## Recommended delivery boundary

Implement Phases 0-4 as the first deliverable. This solves the Bookcase class of
failures without depending on unresolved BSP compilation and creates the shared
workspace/data model.

Implement Phases 5-6 as the second deliverable. Door import should not be enabled
until the copied BSP's physics table and post-transform runtime structure are
validated. At that point the result can genuinely match existing MM9 doors,
rather than merely looking like one in the editor.

Phase 7 remains an allowlisted expansion. The reusable concept is not
"everything becomes a Prop"; it is "every prefab component must resolve to a
runtime representation MM9 already knows how to load."
