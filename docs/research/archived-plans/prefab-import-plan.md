# Complete Prefab Import Implementation Plan

> **Historical implementation plan — non-normative.** This plan records the
> phased behavioral-prefab work completed in August 2026. Its early generated-
> BSP assumptions were superseded by the runtime-safety boundary documented in
> the companion archived plan. See `docs/user-guide/prefab-import.md` for the
> current supported workflow and capability matrix.

> Runtime safety update (2026-08-14): the behavioral graph/object work in this
> document remains applicable, but direct ED brush compilation is now
> editor-preview-only. Installable geometry must resolve to a catalog game
> model or validated DEdit-compiled v66 BSP. See
> [Runtime-Backed Prefab Import Implementation Plan](resource-backed-prefab-import-plan.md).

## Goal

Extend the existing prefab workspace so every authored MM9 DEdit prefab can be
handled deliberately and safely:

- brush-only prefabs import as static BSP;
- object-only prefabs import their runtime objects and resource references;
- mixed prefabs import their object graph, owned moving/interactive BSP, static
  brushes, links, transforms, and dependencies;
- a source that cannot be imported faithfully is reported as blocked before
  placement. It is never silently reduced to static geometry.

The existing `Tools -> Import Static Prefab...` workspace should become one
`Tools -> Import Prefab...` workspace. Static and behavioral imports remain
separate backend pipelines because they have different correctness and runtime
requirements.

Original DEdit `.ed` files are the preferred source for behavioral import. They
retain the authoring hierarchy needed to associate brushes with controller
objects. Compiled DAT v66 mini-worlds remain accepted when their runtime object
and BSP associations are unambiguous.

## Corpus baseline

The inventory of `<dedit-project>\PreFabs` found 175 `.ed` files. Four files under
`MM9SurrogatesDiagnostics` are generated diagnostics and are excluded from the
product acceptance corpus. The remaining 171 authored prefabs divide into:

| Source shape | Files | Required path |
|---|---:|---|
| Brush-only | 75 | Existing static BSP importer |
| Object-only | 9 | New behavioral/object importer |
| Mixed brushes and behavior | 87 | New behavioral graph and owned-BSP importer |

The authored corpus contains 24 non-Brush runtime classes and all 24 currently
exist in the generated MM9 object catalog. Important families include doors,
rotating brushes, elevators, triggers, switches, teleporters, water, lights,
sounds, destructibles, props, shooters, traps, ladders, and sky world models.

The corpus also contains:

- 23 reference-bearing property names, including `AttachTo`,
  `DoubleDoorName`, `PortalName`, `TeleportDestination`, trigger targets, and
  numbered target lists;
- resource references to models, skins, sounds, sprites, and scripts;
- two explicitly scripted prefabs (`shopkeeper.ed` and
  `Furniture\PipeOrgan.ed`);
- 17 zero-byte extensionless `DirTypePrefabs` marker files. These are folder
  metadata, not importable prefabs.

No authored prefab has duplicate non-Brush object names. This is useful but is
not enough to infer ownership: only a small fraction of controller objects have
a same-named source brush. Brush ownership must come from the DEdit node tree.

## Product behavior

The workspace analyzes a selected source and assigns one of these states:

| State | Meaning |
|---|---|
| Static ready | Geometry can use the existing static import pipeline. |
| Behavioral ready | Objects, owned BSP, links, transforms, and resources are complete. |
| Action required | Import becomes valid after explicit target bindings or resource decisions. |
| Blocked | The importer cannot preserve required semantics and explains why. |

The user chooses `Static geometry` or `Full behavior` when both are possible.
Choosing static for a behavior-bearing source requires the current explicit
acknowledgement. Full behavior is the default recommendation for object-only
and mixed sources. There must be no automatic fallback from full behavior to
static geometry.

The workspace should show, before placement:

- source format and hierarchy;
- runtime objects and their classes;
- owned and unowned brush groups;
- generated target names and internal link rewrites;
- unresolved external targets and their bindings;
- resource and script dependencies;
- warnings and blocking errors;
- the BSP/controller plan that will be created.

Inspection and dependency checks should run off the Tk UI thread and be cached
by canonical path, size, and modification time.

## Target architecture

```text
ED v1249 adapter -------+
                        +--> canonical prefab graph --> analyzer/workspace
DAT v66 adapter --------+              |
                                       +--> static planner
                                       |
                                       +--> behavioral planner
                                                |
                                      atomic project operation
                                                |
                                      viewport preview and save
```

### 1. Source adapters

Add a full recursive ED node parser alongside the current flat legacy scanner.
It should mirror the node structures already emitted by
`legacy_ed_writer.py` and preserve:

- node type, id, display name, class, properties, and child order;
- object-to-brush parent/child ownership;
- named null/group nodes;
- brush index and source brush flags;
- unknown fields as diagnostics rather than discarding them.

The DAT adapter should expose runtime WorldObjects, BSP models, their roles,
and exact-name controller/BSP associations. If a compiled source has ambiguous
ownership that the ED source would have resolved, full behavioral import is
blocked and the workspace recommends selecting the original `.ed` file.

Both adapters produce a format-independent `PrefabGraph` instead of letting UI
or project code branch on ED versus DAT.

### 2. Canonical prefab graph

Introduce data structures for:

- `PrefabNode`: hierarchy and display/group metadata;
- `PrefabObject`: class, typed properties, source name, and owned brushes;
- `PrefabBrushGroup`: brushes, material/flags, owner, and runtime role;
- `PrefabReference`: source property, local target, external target, or
  unresolved value;
- `PrefabDependency`: resource type, normalized path, availability, and source;
- `PrefabDiagnostic`: severity, stable code, source location, and remediation;
- `PrefabImportPlan`: deterministic target objects, BSP records, name map,
  link map, dependency manifest, and warnings.

The graph analyzer, not the import writer, decides ownership, support state,
references, and dependencies. Analysis must be deterministic so the workspace
preview, project reload, save preview, and final save all produce the same plan.

### 3. Object construction and property overlay

Use the object.lto-derived MM9 catalog template as the canonical schema for
each imported class:

1. Create a fresh target object from its catalog template.
2. Overlay source values only when property name and type are compatible.
3. Retain catalog-only defaults.
4. Report unknown or type-incompatible source properties; required behavior
   properties block import rather than being silently dropped.

This makes imported objects match the current MM9 runtime schema while
preserving authored values. Build an explicit compatibility table for all 24
classes found in the corpus.

### 4. Names and references

Give each import a deterministic, collision-free namespace. Build one mapping
from every source object, owned BSP, helper, and relevant named group to its
target name. Apply the same map everywhere.

Rewrite known internal reference properties case-insensitively. Classify a
reference as external only after checking the complete node hierarchy; some
values that look external to the current flat scanner refer to named groups or
owned nodes.

True external references are shown as binding fields in the workspace. A user
may bind them to compatible objects in the active level. Leaving a target
unbound is allowed only when the class/property policy explicitly says it is
optional. Script parameters can contain embedded names but cannot be rewritten
generically; they require a script-specific rule or a blocking warning.

### 5. Transform semantics

Do not transform properties using name suffixes or a generic three-float rule.
Create a class-and-property semantics registry with these categories:

- world point: rotate around the prefab anchor, then translate;
- direction: rotate only;
- quaternion: compose with placement yaw;
- extent/dimensions: apply the class-specific axis policy;
- local offset or velocity: retain or rotate according to the class policy;
- behavior-local vector, such as rotation angles: preserve;
- scalar/non-spatial: preserve.

The observed properties requiring policies include `Pos`, `Rotation`,
`RotationPoint`, `SoundPos`, `MoveDir`, `Current`, `Dims`, `TriggerDims`,
`DamageDims`, `DestroyDims`, `SkyDims`, fire offsets/velocities, spawned-object
velocity, and `RotationAngles`.

The same placement anchor and yaw must transform object positions, controller
pivots, owned BSP, unowned static BSP, and applicable linked helper volumes.

### 6. Brush ownership and BSP output

For ED sources, compile brushes according to their node owner:

- brushes owned by a runtime controller become a distinct BSP submodel for
  that controller;
- the generated BSP and controller names are synchronized when the runtime
  relies on same-name lookup;
- unowned visible brushes become ordinary static geometry;
- collision, portal, visibility, sky, trigger, sound, and other helper brushes
  retain their roles and flags instead of being merged indiscriminately;
- authored controller geometry does not receive the generic static collision
  approximation unless an explicit class policy requires it.

This is the central distinction from the current static importer, which safely
combines selected visible brushes but intentionally discards behavior.

### 7. Resource and script dependencies

Resolve normalized dependency paths against loose/staged resources and the
active MM9 REZ archives:

- `MODELS.REZ` and `SKINS.REZ`;
- sounds, sprites, textures, and scripts;
- any class-specific indirect model/skin dependency known to the catalogs.

Existing resources are referenced in place. Missing resources make the import
`Action required` or `Blocked`; the first implementation must not silently copy
files into game archives. If resource staging is later offered, it must be an
explicit decision recorded in the operation and save manifest.

Scripts receive a separate risk indicator. An available script is not proof
that it is namespace-safe or independent of level-specific objects.

### 8. Atomic project operation

Add `ImportBehavioralPrefabOp` rather than expanding
`ImportPrefabBspOp` until it contains two unrelated implementations. Store:

- source path and source fingerprint;
- import mode and placement anchor;
- target position and yaw;
- namespace/root name;
- external bindings and dependency decisions;
- planner/schema version and policy overrides.

Materialization produces the complete object/BSP set atomically. Selection,
move, rotate, delete, undo, redo, `.mm9mod` serialization, preview, save
preview, final save, and manifest generation treat the imported assembly as
one operation. Bump the project format and retain backward loading for existing
static prefab operations.

## Delivery phases

### Phase 0 - Parser and corpus audit foundation

Status: recursive ED v1249 node parsing and exact object/brush ownership were
completed with Phase 3 on 2026-08-13. All 171 authored prefabs decode; the two
non-authoring generated diagnostics deliberately fall back to the older flat
scan and remain fail-closed for behavioral import.

Deliver:

- recursive ED hierarchy parser and canonical source adapters;
- corpus inventory command/report excluding diagnostics and folder markers;
- ownership, reference, property, class, and dependency diagnostics;
- stable support-state and diagnostic codes.

Exit criteria:

- all 171 authored ED files parse without an unknown node layout;
- every brush is classified as owned, unowned, or blocked with a reason;
- all 24 runtime classes and every observed property are present in the audit;
- apparent external links are reclassified using the full hierarchy.

### Phase 1 - Planner, workspace, and atomic operation shell

Status: implemented on 2026-08-13 as a fail-closed planning shell. No runtime
class capability is promoted by this phase, so full behavioral placement stays
blocked until the later capability phases pass their acceptance gates.

Deliver:

- `PrefabGraph`, analyzer, deterministic name/reference mapper, transform
  registry, dependency resolver, and behavioral import-plan types;
- the four workspace support states and mode selector;
- graph, ownership, link, resource, and diagnostic previews;
- serializable `ImportBehavioralPrefabOp` with preview/save integration. It was
  initially gated and was promoted to the supported path in Phase 7.

Exit criteria:

- repeated analysis creates byte-for-byte equivalent plans;
- validation prevents placement on any blocking diagnostic;
- the workspace never silently changes the selected import mode.

Implemented components include `PrefabGraph`, deterministic behavioral plans,
typed property/dependency/reference retention, the spatial-semantics registry,
cached background workspace analysis, explicit mode/support states, and
project-format-v15 `ImportBehavioralPrefabOp` persistence. ED controller-brush
ownership is now supplied by the recursive hierarchy parser completed in
Phase 3.

### Phase 2 - Object-only and resource-backed prefabs

Status: implemented on 2026-08-13 for the non-scripted object-only capability
set: `Prop`, `DestructableProp`, `DirLight`, `Light`, and `WallTorch`.

Enable catalog-backed object creation for simple props and destructibles, then
multi-object light/torch assemblies. This covers the nine current object-only
prefabs, including barrels, crates, hanging skeleton, and torch assemblies.

Exit criteria:

- all eight non-scripted object-only prefabs import, preview, move, rotate,
  save, and reopen; the ninth remains an explicit scripted-behavior gate;
- source property overlays and resource paths survive a round trip;
- missing resources are reported before placement.

Eight of the nine authored object-only corpus prefabs now pass catalog-backed
materialization and DAT save/reopen checks. `shopkeeper.ed` is intentionally
still blocked because it runs `PropAnim.scr` with `ScriptParams=Innkeeper2`;
importing it while discarding or assuming the script semantics would violate
the no-partial-import rule. It remains assigned to the scripted-behavior phase.

### Phase 3 - Passive mixed assemblies

Status: implemented on 2026-08-13 for the passive class set. The importer now
uses the recursive DEdit hierarchy's brush indices and nearest object ancestor,
combines unowned brushes only within the same runtime role, keeps each owned
controller model separate, and writes the object and BSP halves through one
atomic behavioral operation. Placement, yaw changes, preview, undo/redo,
project reload, save, and DAT reopen use the same deterministic plan.

Enable unowned static geometry plus non-moving object families: `WorldObject`,
`Light`, `DirLight`, `AmbientSound`, `Fire`, water classes, `Ladder`,
`DemoSkyWorldModel`, simple `Prop`, and unlinked teleporter components.

Exit criteria:

- role-specific brushes remain separated;
- water, light, sound, ladder, sky, and prop representatives render and behave
  correctly in the editor and game;
- helper visibility follows the existing `View -> Helper BSP` controls.

The real authored corpus currently yields 15 ready passive mixed prefabs:
four skyboxes, six static/WorldObject furniture or structural assemblies, the
hanging brazier, wall fountain, curvy stairs, ladder, and chimney/fireplace.
All 15 pass catalog-backed object materialization and role-preserving BSP
compilation. Water controller objects remain invisible while their same-named
water BSP stays visible; ladder and marker-material geometry follows the
existing helper-BSP filters. Four teleporter assemblies remain blocked because
their live `TeleportDestination` links belong to Phase 5. Final in-game visual,
collision, water, ladder, and sky acceptance remains a manual release check.

### Phase 4 - Simple owned moving BSP

Status: implemented on 2026-08-13 for fail-closed single-controller
assemblies. `Door`, `RotatingDoor`, `RotatingBrush`, and `Lift` are promoted
only when the prefab contains exactly one moving controller, that controller
owns BSP, and the graph has no portal, pair, trigger-target, attachment, or
other live reference. Controller objects and their same-named BSP are written
atomically and share the placement translation/yaw. `MoveDir` rotates with the
placement, `RotationAngles` remains behavior-local, zero `SoundPos` retains its
engine sentinel meaning, and nonzero sound positions move with the assembly.

Legacy DEdit's Save As Prefab path offsets ordinary node positions and brush
points but not arbitrary point properties. The importer detects the shipped
prefabs whose `RotationPoint` is implausibly far outside their owned BSP, warns
about the stale value, and uses the controller's authored position as the local
hinge. Plausible authored pivots are retained.

Enable one-controller assemblies for `Door`, `RotatingDoor`, `RotatingBrush`,
and `Lift`, including pivots, motion vectors/angles, sounds, and owned BSP.

Exit criteria:

- representative doors, rotating panels, and lifts have correct render,
  collision, closed/open position, pivot, direction, timing, and sound in game;
- placement translation and yaw keep controller, pivot, and BSP synchronized.

The current authored MM9 corpus yields seven ready Phase-4 sources:
`RicketyWoodPlankDoor`, `SwingingSign`, `rack`, `AxeTrap`, `Pendulum`,
`RakeTrap`, and `spikewall`. The remaining 57 moving sources are blocked as
linked, paired, scripted, portal-backed, multi-controller, or otherwise
compound assemblies. No shipped standalone `Lift` or `RotatingBrush` source is
simple enough for Phase 4; those classes are enabled for genuinely simple
custom prefabs but the shipped elevator/gear assemblies remain Phase 5 work.
Generated fixtures verify DAT save/reopen, owned BSP, stale and explicit
pivots, movement-vector yaw, motion-property retention, sound sentinel retention,
preview, and compound/portal rejection. Final moving/collision/sound behavior
in the MM9 runtime remains a manual release check.

### Phase 5 - Linked behavioral graphs

Status: implemented on 2026-08-13 for non-scripted graphs composed of the
Phase 2-4 classes plus `Trigger` and `Switch`. Internal object links,
attachments, trigger lists, paired doors, compound movers, elevators, and
linked teleporters are rewritten through one deterministic namespace.
Repeated imports therefore bind only to their own generated object names.

External object names are entered in the prefab workspace and must resolve to
an object in the active level both at placement and at save planning. A later
edit that removes such a target produces a blocking save/install issue. The
whole object/BSP graph remains one `ImportBehavioralPrefabOp`. Project format
v17 adds an undoable assembly tombstone, so deletion, undo, and redo cannot
split its members or orphan its BSP.

Authored ED portal brushes are inputs to the original level compiler's
VisBSP/PVS construction; the additive BSP compiler cannot safely recreate or
merge them. Consequently every `PortalName` is an explicit target-level
binding. The user must either name an existing portal already compiled into
the target VisBSP or enter `<omit>`, which clears `PortalName` while retaining
the door/assembly behavior. Portal helper brushes are recorded in the plan but
are not emitted as ordinary BSP submodels. This is an intentional, visible
lossy choice rather than a silently dangling portal.

At the Phase-5 milestone, the authored corpus contained 61 prefabs with live
references. Seven remained blocked for Phase-6 classes or scripts. The other 54 pass catalog-backed
object materialization and role-preserving BSP planning after required target
bindings are supplied; 29 need no target-level decision and 25 require object
or portal bindings. Legacy `AmbientSound.Radius` maps to MM9
`OuterRadius`, and obsolete default-only `RotatingBrush` material fields are
diagnosed without preventing the linked elevator/trap graphs from importing.

Enable compound/double doors, switches, trigger target lists, elevator
assemblies, paired teleporters, attachments, portals, and explicit target-level
bindings.

Exit criteria:

- every internal target resolves after namespace rewriting;
- required external targets cannot remain accidentally dangling;
- importing the same prefab twice creates two independent working assemblies;
- deletion/undo removes or restores the entire assembly without orphaned BSP
  or references.

### Phase 6 - Hazards, destruction, shooters, and scripts

Status: implemented on 2026-08-13 with fail-closed schema and script policies.
The ten remaining authored behavioral prefabs now pass object/BSP planning
when their explicit external bindings and genuinely absent resources are
provided. Five are ready on the stock archives; five remain action-required
for portal/object bindings or missing model/skin/sound assets.

Enable `DestructableBrush`, `DestructableProp`, `PropDamager`, `Shooter`, and
compound trap patterns. Add reviewed rules for scripted prefabs and embedded
script parameters.

`DestructableBrush` owned geometry is emitted as one same-named controller
model. The shipped default-only destructible material fields and
`PropDamager.Translucency` retain their MM9 runtime defaults. The three shipped
Shooter prefabs all use obsolete `ProjectileType=2`; this is accepted only at
that reviewed value and retains the catalog template's `FireBolt` name.

Script files are not accepted by existence alone. `PropAnim.scr` is verified
as parameter-only. The Pipe Organ scripts are parsed for literal
`GetObjectHandle` calls: note targets are namespaced, `Bell1`-`Bell5` become
explicit target-level bindings, and unique rewritten copies are added to a
complete staged `SCRIPTS.REZ`. Project format v18 persists both reviewed source
text and generated assets. Unknown scripts and changed/dynamic lookup forms are
blocked.

Exit criteria:

- damage, destruction, spawning/shooting, and trap sequences pass in-game
  fixtures;
- `shopkeeper.ed` and `PipeOrgan.ed` either import with verified script rules
  or remain clearly blocked with a precise remediation. They may not be marked
  ready based only on the presence of a `.scr` file.

### Phase 7 - Corpus closure and door-clone retirement

Status: implemented on 2026-08-13. The deterministic closure audit processes
all 171 shipped prefabs with zero failures: 75 are `Static ready`, 51 are
`Behavioral ready`, and 45 are `Action required` only because they need an
explicit target binding or resource decision. No prefab remains blocked by an
unknown layout, runtime class, property, ownership rule, transform, or link.

Run the entire acceptance corpus, remove the experimental flag, finalize user
documentation, and compare behavioral door import with `Clone Physical Door`.

`Clone Physical Door` can be deprecated and then removed only when prefab door
import provides all of the following:

- equivalent controller and BSP creation;
- collision and motion/pivot fidelity;
- placement, selection, editing, undo, save, and reload parity;
- clear handling of source-level dependencies and target bindings;
- passing in-game tests for simple, double, rotating, and compound doors.

The parity validator now checks controller class/name retention, owned-BSP
coverage, controller/BSP name identity, and resolved pair/trigger targets. Its
golden BSP set covers simple and double doors, rotating and compound doors,
three elevators, destructibles, teleporters, scripted assemblies, and four trap
families. Behavioral import is no longer experimental and the editor no longer
offers creation of new same-level door clones.

`CloneDoorOp` is retained as a legacy project migration surface, not as a user
workflow. Older `.mm9mod` files still materialize, edit, preview, save, and
reload their cloned door controllers and BSP. This is deliberately safer than
invalidating projects written by project format v3 and later.

Run the maintained closure gate with:

```powershell
python tools/audit_prefab_corpus.py --game-root "<mm9-root>"
```

Use `--include-all-bsp` for the slower investigation pass that compiles every
behavior-bearing BSP assembly instead of the representative golden set.

## Capability matrix

Implementation and release should be capability-driven rather than based only
on file names:

| Capability | Representative classes/prefabs | Planned phase |
|---|---|---:|
| Static brush geometry | Chair, tables, fences | Existing / 0 |
| Resource-backed objects | Prop, DestructableProp, Barrel, Crate | 2 |
| Light/torch assemblies | DirLight, Light, WallTorch | 2 |
| Passive world roles | WorldObject, water, sound, sky, ladder, fire | 3 |
| Single moving controller | Door, RotatingDoor, RotatingBrush, Lift | 4 |
| Cross-object links | Trigger, Switch, Teleporter, double doors | 5 |
| Damage/destruction/spawn | DestructableBrush, PropDamager, Shooter | 6 |
| Scripted behavior | shopkeeper, PipeOrgan | 6 |

All listed importer capabilities are now implemented in the supported path.
Automated parser/planner/materializer, project round-trip, and golden BSP tests
are release gates; representative in-game acceptance remains the final runtime
check for behavior changes.

## Test strategy

### Fast automated tests

- recursive node parser fixtures for direct-root, named-group, object-owned,
  and nested mixed layouts;
- ED and DAT adapter equivalence where both source forms are available;
- schema overlay/type compatibility for all 24 classes;
- complete transform-policy coverage for every observed spatial property;
- deterministic namespace and case-insensitive link rewriting;
- external binding validation and duplicate-import independence;
- dependency lookup across loose, staged, and REZ resources;
- atomic operation materialize/move/rotate/delete/undo/serialize/reload tests;
- workspace state and no-silent-downgrade UI tests.

### Corpus tests

Run a read-only audit over all 171 authored `.ed` files and require:

- zero parse failures or unknown node types;
- no unknown runtime classes;
- no unclassified brushes or references;
- a deterministic support state and diagnostic set for every prefab;
- no dangling internal target after planning.

`tools/audit_prefab_corpus.py` is the explicit full-corpus release gate. Small
checked-in fixtures exercise the same parser/planner/materializer branches in
the default suite, while the real corpus and game resources remain an explicit
integration run.

### Golden representatives

At minimum, retain goldens for:

- brush-only `Furniture\Chair.ed`;
- object-only Barrel/Crate and WallTorch/TonysTorch;
- simple and double doors (`A1_Door`, `A1_DoubleDoor`);
- complex rotating gates/panels (`HexDoor`, `RoundSlidingPanelGate`);
- gear, rope, and tower elevators;
- a switch/trigger graph;
- teleporter and wavy-mirror assemblies;
- a skybox and water volume;
- breakable door/fire globe destructibles;
- `PipeOrgan.ed` as a scripted case;
- representative floor saw, spike, spinning-blade, and projectile traps.

### End-to-end and in-game tests

For each promoted capability:

1. Import into a controlled test level at a non-zero position and yaw.
2. Save to a test output REZ and reopen the DAT.
3. Compare object classes, property types/values, generated names, links,
   resource paths, and BSP ownership with the plan.
4. Verify editor rendering, selection, helper visibility, and transforms.
5. Verify in game: rendering, collision, movement/pivot, triggers, paired
   sequences, damage/destruction, teleporting, light/water/sound, and scripts as
   applicable.

Compiler/game launches stay in the explicitly invoked slow or investigation
suite; deterministic unit and golden tests stay in the normal suite.

## Definition of complete

The feature is complete when:

- all 171 authored corpus prefabs are `Static ready`, `Behavioral ready`, or
  `Action required` only for genuine user-supplied external bindings/resources;
- no file is `Blocked` because of an unknown layout, class, property, brush
  owner, transform rule, or reference type;
- every ready import is atomic, deterministic, editable, undoable, serializable,
  and stable across save/reopen;
- every internal link resolves, every required external link is bound, and all
  dependencies are accounted for;
- representative behavior for every capability passes in the MM9 runtime;
- static import remains available as an explicit choice, never an implicit
  downgrade;
- new `Clone Physical Door` creation is retired after its parity gate passes;
  legacy `CloneDoorOp` replay remains compatible with existing projects.
