# MM9 Level Data & Properties

## DAT And Properties

- `.DAT` world files use version 66.
- `WorldObject` properties must preserve type codes and string length quirks.
- Some fields are stored as IEEE-754 float bit patterns in LongInt slots.
  `NPCNbr`, `Scale`, `RangeAttackType`, `TrapLevel`, `TreasureLevel`, and
  several treasure fields can use this pattern.
- `MoveToFloor` is an engine-side object property, not an editor-only flag.
  The viewport previews its initial runtime placement using the ABC
  animation `UserDims` and solid BSP collision surfaces. Dynamic object-on-
  object physics after level startup is still outside the editor simulation.


## Treasure Chests

Chest loot usually comes from native class logic and DAT properties, not an
attached script. Relevant fields include `Random`, `Gold`, `GoldOnly`,
`Item1` through `Item5`, `TrapLevel`, `TreasureLevel`, `TreasureOptions`, and
`TreasureType0_7`.

For example, `DRAGONSTADIUM.DAT` contains black chest objects with no
`ScriptName`, `Random=1`, `TreasureLevel=6.0`, `TrapLevel=20.0`, and
`TreasureType0_7=7.0`; these are max-tier random treasure chests.


## Level Transitions

Cross-level travel is driven by `ExitTrigger` objects in the current DAT and
`StartPoint` objects in the destination DAT.

Important `ExitTrigger` fields:

- `DestinationWorld`: destination world name, usually the DAT stem without
  `.DAT`, such as `Thjorgard` or `DarkPassageway`.
- `StartPointName`: `Name` of a `StartPoint` object in the destination world.
- `Dims`: trigger volume size around `Pos`.
- `StartOn`: whether the trigger starts active.
- `AskPlayer`: whether the game asks the player before travelling.
- `TravelDays`: travel-time value. This is a `LongInt` slot, but shipped data
  often stores IEEE-754 float bit patterns in it; for example raw
  `1073741824` means `2.0`, and raw `3212836864` means `-1.0`.
- `LoadScreen` and `LoadTextID`: loading-screen metadata.

Shipped data contains 107 `ExitTrigger` objects and 117 `StartPoint` objects.
Most exits resolve cleanly to a destination DAT plus a named start point. The
few non-matching names appear to rely on engine fallback or script context, so
the editor should warn but not assume they are invalid.

Same-level teleporting is separate. `Teleporter` objects use
`TeleportDestination` to name another object in the same level, and scripts such
as `TELEPORTER.SCR` can resolve a named destination object and call `SetPOS`.
That mechanism moves actors inside the loaded world; `ExitTrigger` changes the
world.

Suggested editor flow for custom DEdit levels:

1. Let the modder create new geometry in DEdit and export it as a DAT with a
   stable world filename, for example `MYDUNGEON.DAT`.
2. Open that DAT in `mm9_editor`, then add at least one `StartPoint` object
   where the player should arrive. A practical default is `Name=StartPoint0`,
   `PlayerNbr=0`, `TeamNbr=0`, and `MovePlayerToFloor=1`.
3. Add NPCs, props, loot, lights, and any other world objects to the new DAT
   using the existing placement and property workflows.
4. Open an existing source level, add or clone a door/portal prop for the
   visible doorway, and add an `ExitTrigger` volume at the doorway.
5. Set the trigger's `DestinationWorld` to the new DAT stem, for example
   `MYDUNGEON`, and `StartPointName` to the destination start point name.
6. Save both levels into patched REZ output. Installing the output into the
   game archives is an explicit editor action.

Useful editor improvements for this workflow:

- A transition wizard that creates a matched `ExitTrigger` and destination
  `StartPoint`, optionally opening both levels side by side.
- A link validator that scans loaded levels and `WORLDS.REZ` entries and reports
  unresolved `DestinationWorld` or `StartPointName` references.
- A semantic `ExitTrigger` inspector with dropdowns for destination world and
  start point, plus float-bit editing for `TravelDays`.
- A door preset that places both a visible door/portal object and an aligned
  trigger volume.
- A custom-level packaging helper that records the new DAT entry name expected
  by the game, for example `WORLDS/MYDUNGEON`.

## Doors


Door geometry is usually not an ABC prop. In MM9 levels, many visible doors
are BSP submodels whose names match `Door` or `RotatingDoor` world objects.
The world object is the controller/logic record; the same-named BSP submodel is
the visible and colliding geometry that the engine moves. For example, in
`STURMFORDCITY.DAT` there is both a `Door` object named `Door32` and a BSP
submodel named `Door32`; likewise `ChurchdoorR` is a `RotatingDoor` object and
a BSP submodel.

`Door` objects appear to be linear/sliding doors. Their movement is governed by
fields such as `MoveDir`, `MoveDist`, `Speed`, `ClosingSpeed`, and the usual
sound/lock fields. `RotatingDoor` objects are hinged doors, using
`RotationPoint` and `RotationAngles` instead of a linear move vector. Paired
doors use `DoubleDoorName`, such as `ChurchdoorR` <-> `ChurchdoorL`.

Openable vs. non-openable behavior is mostly data-driven. `ChurchdoorR` is
openable because it is `Locked=0`, has `RotationAngles=(0, -90, 0)`, and has
door open/close sounds. `Door32` only knocks because it is `Locked=1`, has
`JiggleSound=Sounds\Door\knock.wav`, has empty open/close sounds, and has no
useful movement (`MoveDist=0`). Scripts can still trigger doors with commands
such as `Trigger hDoor Use`, `Unlock`, or `Open` (see `BASEDOOR.INC` for AI
door handling), but these two Sturmford examples are explained by their DAT
properties alone.

Legacy physical-door clone compatibility:

- `core/bsp.py` preserves source byte ranges for parsed world-model records, so
  door submodels such as `Door32`, `ChurchdoorR`, and `ChurchdoorL` can be
  copied from the original DAT bytes.
- `features/doors/links.py` links door controller objects to same-named BSP submodels
  and resolves paired rotating doors through `DoubleDoorName`.
- `features/doors/clone.py` builds an in-memory clone plan from an existing physical
  door. It deep-copies one or two controller objects, translates `Pos` and
  `RotationPoint`, updates paired `DoubleDoorName` values, checks name
  collisions, and carries copied BSP source records with source/new names.
- `core/project.py` has `CloneDoorOp`, materialized object support, undo/redo
  compatibility through the existing op stack, pending-object index mapping,
  and save-plan `door_clones` metadata. `core/project_io.py` serializes this op in
  `.mm9mod` files.
- Phase 7 retired the `Clone Physical Door` creation command and its dialog.
  New doors use `Tools -> Import Prefab...`, which supports simple, paired,
  rotating, and compound authored door assemblies.
- The `CloneDoorOp` model, materialization, editing, preview, writer, and
  serializer remain intentionally available for old `.mm9mod` files. Loading an
  existing project therefore never orphans a cloned controller or BSP model.
- The level object list displays pending clone objects. Pending clone objects
  can be selected, dragged/elevated, and deleted before save.
- Pending clone preview is BSP-aware. `LevelEdit.preview_bsp()` appends
  translated/rotated clones to the viewport BSP so the physical door appears
  in the editor before save/reopen. `View3D.refresh()` rebuilds the BSP draw
  batch when pending clone ops change.
- Pending clone transform edits refresh the preview BSP immediately. This is
  required after drag, keyboard nudge/elevate/rotate, or Properties-panel
  `Pos`/`Rotation` edits; otherwise the billboard and physical BSP can drift or
  snap back to the op's previous transform.
- Save preview includes physical door clone counts plus validation warnings.
  The manifest records `door_clones` and `validation_warnings` per DAT write.
- Current warnings include reused `PortalName`, incomplete clone/controller
  data, BSP/controller name mismatches, and terminal BSP-tail handling.
- `features/doors/bsp_writer.py` implements the current save path for physical-door
  clones. It appends copied world-model records before the WorldObject section,
  increments the world-model count, patches `NextWorldItem`, renames the BSP
  submodels, transforms `min_box`, `max_box`, `translation`, and point
  positions by the controller translation/yaw, transforms point normals, and
  transforms surface UV projection (`uv_o`, `uv_p`, `uv_q`) so textures remain
  aligned after moved/rotated clones. It then recomputes header object/render
  offsets.
- Some DATs, confirmed in `BOOTCAMP.DAT`, have a terminal/dummy world-model
  record or payload after the last parsed model (`PhysicsBSP`) and before the
  WorldObject section. Cloned door records must be inserted before this tail,
  not simply appended at `ObjectDataPos`, and the shifted tail's first
  `NextWorldItem` must be updated to the new object section. Skipping this tail
  makes the editor parse the level but can crash the game loader.
- Current limitation: this writer is intentionally narrow. It supports cloned
  physical door submodels appended to the BSP list; it is not a general-purpose
  BSP editor for arbitrary new geometry, deleting submodels, or editing the
  original BSP tree.


## Static Prefabs

The shared `C:\lithtech\PreFabs` corpus contains DEdit source `.ed` files
(version 1249). These sources contain useful objects, brush geometry, names,
textures, and ownership information, but they are not runtime-compiled BSP.
The editor's small additive serializer produces polygons for preview/research;
it does not produce the complete node tree, root, physics block table, and
other structures required by the MM9 loader. Direct ED brush output is
therefore **editor preview only** and is blocked by the DAT save planner.

The unified `Tools -> Import Prefab...` workspace now chooses among runtime
representations instead of treating every prefab as new BSP:

- **Use catalog game model** resolves a brush-shaped prefab such as Bookcase
  to ranked MM9 `Prop` model/skin variants. The selected stock-style object is
  materialized from an observed/catalog template and emits no BSP record.
- **Import DEdit-compiled BSP** accepts only v66 DAT input and validates every
  selected record for a real node tree/root and physics block table before
  placement and again before save. Raw compiled records are copied; they are
  not rebuilt by the editor.
- **Full behavior** continues to materialize supported runtime object graphs.
  Object-only ED sources remain supported. Any behavioral ED source with
  brushes must first be compiled to v66 DAT by DEdit.
- **ED brush preview only** keeps the recovered mesh visible and editable in
  the viewport, while the save/install path reports a non-overridable error
  until the operation is replaced by a game model or compiled DAT.

`features/prefabs/inspector.py` is the Stage 1 read-only layer for this work. It
dispatches DEdit ED v1249 and compiled DAT v66 files, classifies model roles (`geometry`,
`controller_geometry`, `physics`, `visibility`, `skybox`), reports object
classes, bounds, polygon/point/texture counts, and carries BSP parse warnings.

`features/prefabs/resource_backed.py` ranks model candidates only when the
catalog observed that ABC on `Prop`; inherited actor and unreviewed subclass
model properties are not accepted. The current implementation promotes `Prop` and
keeps the exact selected model, skin list, catalog/stock object template, and
source fingerprint in `ImportResourcePrefabOp`. Because it subclasses the
normal add operation, move, yaw, properties, delete, undo/redo, project reopen,
and save use the same existing object workflow. Project format v20 persists
this provenance and the preview-only flag on legacy BSP operations.

The old generated thin-box collision choice remains readable for old projects
and available to low-level preview tests, but is disabled in the workspace and
blocked at save. A runtime-safe collision helper must come from authored,
validated compiled `PhysicsBSP` data (or be omitted). Helper BSP viewport
controls remain under `View -> Helper BSP` and do not alter saved data.

The full design and remaining compiled-assembly/catalog-index work is tracked
in [Runtime-Backed Prefab Import Implementation Plan](resource_backed_prefab_import_plan.md).

The staged design for object-only and behavioral prefab support is documented
in [Complete Prefab Import Implementation Plan](prefab_import_plan.md).
Compiled-BSP mode remains available beside the capability-gated behavioral and
model-backed paths; unsupported behavior never falls back to installable
preview BSP.

Behavioral-plan Phase 1 is implemented as a fail-closed planning layer. The
workspace analyzes ED/DAT sources off the Tk thread, caches results by source
path/size/mtime, and shows independent static/behavioral states plus brush
ownership, links, resource dependencies, and stable diagnostics. Canonical
`PrefabGraph` and behavioral plan records retain typed source properties,
deterministic namespaces, internal link rewrites, external bindings, dependency
decisions, and explicit spatial semantics. Project format v15 introduced
`ImportBehavioralPrefabOp`, including its source fingerprint and decisions;
unsupported capabilities still refuse to create a partial assembly.

The Phase 3-7 ownership/link/controller policies below still describe what the
editor can analyze and preview. After the runtime-BSP safety update, an ED
behavioral graph that owns brushes is not installable directly: the same source
must be DEdit-compiled to v66 DAT so those owned records can be copied and
semantically validated. Object-only ED graphs are unaffected.

Object-only Phase 2 promotes `Prop`, `DestructableProp`, `DirLight`, `Light`,
and `WallTorch` sources with no brush geometry. The importer creates every
object from its object.lto catalog template, overlays type-compatible ED
values, retains MM9-only defaults, transforms the assembly as one unit, and
persists the templates and per-object edits in project format v16. Known
obsolete DEdit fields are diagnosed explicitly. Model, skin, texture, sound,
and script dependencies are checked before placement. Eight authored
object-only prefabs are supported. `shopkeeper.ed` now has a reviewed
`PropAnim.scr` pass-through policy, but remains action-required on a stock MM9
install because its model and skin resources are absent; those dependencies
are reported before placement.

Passive-mixed Phase 3 adds recursive ED v1249 hierarchy parsing, so brush
ownership comes from the authored object parent and brush index rather than
names or flat record order. It promotes passive `WorldObject`, light, ambient
sound, fire, water, ladder, sky, prop, and unlinked teleporter families. Owned
BSP is compiled as a same-named controller model; unowned brushes are combined
only within the same geometry/helper role and receive a catalog-backed
`WorldObject` controller. The same placement pivot and yaw transform objects
and every BSP group. Preview and DAT save consume the same atomic plan, and
water/ladder/marker geometry follows the existing Helper BSP visibility rules.
Linked teleporter assemblies remain gated for the linked-graph phase.

Simple-moving Phase 4 promotes a single owned `Door`, `RotatingDoor`,
`RotatingBrush`, or `Lift` controller when the source has no live links,
portals, scripts, paired leaf, or second moving controller. Its controller and
same-named BSP are placed, previewed, edited, undone, and saved atomically.
Placement yaw rotates `MoveDir` and absolute point properties while leaving
behavior-local `RotationAngles` unchanged; the engine's zero `SoundPos`
sentinel is preserved. Legacy ED pivots that remained in the original level's
coordinate space are detected from their distance to the owned BSP and safely
rebased to the controller position with a warning. Portal-backed doors,
elevators/gears, linked traps, and all other compound graphs are handled by
Phase 5.

Linked-graph Phase 5 promotes `Trigger` and `Switch` and permits links among
all Phase 2-4 classes. Internal `AttachTo`, double-door, trigger-target,
teleporter, and similar references are rewritten into the imported root
namespace. External object bindings are collected in the import workspace and
validated against the target level during placement and save planning. The
same prefab can therefore be imported twice without cross-linking its copies.
Project format v17 records behavioral-assembly removal as an undoable
tombstone, so the complete graph remains one atomic delete/undo operation.

DEdit portal brushes are VisBSP/PVS compiler inputs, not additive world
models. They are deliberately excluded from the BSP import. Each `PortalName`
must instead bind to a user portal already compiled into the target VisBSP, or
the user must explicitly choose `<omit>` to clear that property. The importer
does not pretend that a standalone portal brush can reproduce the original
compiler's visibility graph.

Hazard/script Phase 6 promotes `DestructableBrush`, `PropDamager`, and
`Shooter` in addition to the earlier `DestructableProp`. Owned destructible BSP
is kept as one same-named controller model, and damage/death trigger links use
the Phase-5 namespace and target validation. The legacy shooter-only
`ProjectileType=2` field is accepted only at its shipped default and retains
the MM9 `object.lto` template's explicit `ProjectileName=FireBolt`; other enum
values fail closed instead of being guessed.

Script support is deliberately allowlisted. `PropAnim.scr` was verified to use
only `ScriptParams` and is preserved. `tocatta.scr`, `rondo.scr`, and
`diesirae.scr` use literal `GetObjectHandle` names; the importer parses those
lookups, rewrites internal Pipe Organ note targets, requests explicit bindings
for the external `Bell1`-`Bell5` objects, and gives every import unique script
copies. Project format v18 stores the reviewed source snapshot and generated
scripts. Saving adds them to a complete staged `SCRIPTS.REZ`; the original game
archive is not modified. Any unreviewed script, changed lookup shape, missing
script source, unresolved binding, or missing resource remains blocked.

Corpus-closure Phase 7 promotes behavioral import out of its experimental gate
and bumps project persistence to format v19. The explicit
`tools/audit_prefab_corpus.py` release audit parses and plans all 171 authored
prefabs twice for determinism, materializes every behavioral plan, checks links
and dependencies, and compiles a representative BSP set against a real target
level. The current closure result is 75 `Static ready`, 51 `Behavioral ready`,
45 `Action required`, and zero failures. Door-specific parity checks require
every moving controller to retain a same-named owned BSP and require all paired
and trigger targets to resolve. New door creation now uses the unified prefab
workspace; the former clone command is gone, while legacy `CloneDoorOp` project
records continue to load and save.


## Save Game Files

MM9 save games are split into paired `.HDR` and `.MM9` files.  The `.HDR`
file begins with the current world path, for example
`worlds\bootcamp`, and appears to carry metadata/preview data for the save
slot.  The `.MM9` file contains the actual runtime game state.

The sample saves under `mm9_data/saves/` (`autosave.*` and `d11.*`) were both
standing in Bootcamp.  Their `.MM9` files do not embed a full v66 world DAT:
there is no DAT header, no full BSP/render payload, and no matching 4 KiB
chunks from `WORLDS/BOOTCAMP.DAT`.  Instead, they contain a linked list of
runtime object records for the active level:

- The file header starts with small counters.  In both samples, the second
  `uint32` is `401`, matching the number of linked runtime object records.
- Runtime object records are marked by the magic bytes `AF EF CD AB`.
- Each record stores a next-record pointer at `magic + 4`.  The pointer value
  is four bytes past the next record's magic offset; the terminal record uses
  `0xFFFFFFFF`.
- The class name is stored as a LithTech-style `uint16 length + bytes` string
  at `magic + 12`.
- The record body then contains object state such as position, rotation,
  object name, model/skin paths, script names, trigger targets, and
  class-specific runtime fields.

The saved Bootcamp object list contains many DAT-backed live objects:
`Prop`, `AIRail`, `CandleProp`, `RotatingDoor`, `AmbientSound`,
`WorldObject`, `Door`, `PerceptionBrush`, creatures/NPCs, and similar
interactive/runtime classes.  It omits many static/setup DAT classes such as
`Light`, `DirLight`, `WorldProperties`, `StartPoint`, `StaticSunLight`, and
`Terrain`.  In the two sample saves, roughly 398-399 of Bootcamp's 591 named
DAT objects were found by name in the `.MM9` data.

Practical consequence: when a player loads an existing save that is already
standing in a level, the engine restores these saved runtime objects instead
of rebuilding them from the patched `WORLDS.REZ` entry.  Object-section edits
therefore do not reliably appear in old saves for that level, while a new game
does see them because the runtime object list is created from the patched DAT.

BSP-only changes are a separate concern.  The current samples do not show a
full embedded Bootcamp BSP/DAT payload in the `.MM9` file, so missing BSP-only
changes in an old save would point more toward install/archive/cache behavior
or another not-yet-identified save section than toward a copied DAT blob.

Potential tooling:

- A safe first step is a save analyzer that reports the current world path,
  runtime object count, saved classes, and whether the save already contains
  object state for a patched level.
- A save migrator may be possible, but should be treated as experimental.
  Records contain runtime handles, AI/script state, and class-specific fields,
  so blindly replacing them with DAT objects could corrupt a save or reset
  quest/AI state.  Any migrator should create backups, preserve unknown fields,
  operate by stable object names where possible, and be validated in-game with
  paired before/after saves.


# ABC Rendering Notes

Current support is intentionally conservative:

- Rigid/static props with validated layouts render as meshes.
- Top-level NPC/creature ABCs from `MODELS.REZ` render as static posed
  meshes, including weighted/complex models via a conservative LOD0 preview.
- NPC/creature model and skin previews prefer the `actor_visuals` table stored
  in `catalog/data/catalog.json`, generated from `DATA.REZ`'s
  `ACTOR`/`MONSTERS` resources.
  This fixes DAT placeholder filenames such as BATHHOUSE's `models\Honk.abc`
  Ebora/concubines and MOUNTAINPASS wolf objects with `models\sheep.abc`.
- Unsupported models fall back to coloured handles, which remain selectable.
- Full animation playback is out of scope.
- Socket/attachment rendering is intentionally deferred.  Many accessories are
  runtime `AttachProp` script effects rather than DAT object properties, and
  previewing them would require socket parsing plus partial script
  interpretation for modest editor value.  Current static NPC/creature meshes
  are good enough for placement and property editing.

Confirmed ABC details:

- Named blocks use `uint16 name_len`, raw name bytes, then `uint32 next_sibling`.
- Top-level blocks are usually Header, Pieces, Nodes, ChildModels, Animation,
  Sockets, AnimBindings.
- Several character ABCs use old `compression_type=0xFFFFFFFF` raw
  `NodeKeyFrame` animation data.
- Static pose baking uses frame 0 of `stand*` or `idle*` when present.
- Static pose baking preserves the ABC's authored model-space vertex normals;
  only models without valid normals fall back to flat per-triangle shading.
- Rigid `NoAnimation` props use their saved model-space vertex positions. This
  preserves the authored pivot instead of drawing Bone01-local coordinates.
- Animation `UserDims` are retained and used for `MoveToFloor` placement.
  Static furniture whose DAT position is authored directly on a solid support
  keeps that position as its model-bottom anchor.
- Rigid models with a data-detectable bottom-oriented source pivot (including
  MM9 vegetation) retain the exact raw-to-saved ABC Y offset when positioned.
  Placement raycasts ignore solid AI/helper materials such as `Rail.dtx`.
- Object-model lighting is transformed into model space per instance so yaw,
  scale, and the viewport coordinate reflection do not rotate lighting away
  from the geometry.
- Node transforms use column translation; using row-vector math collapses
  animated characters into imploded-looking geometry.
- ABC vertex weight node indices are direct and zero-based. Rigid props often
  reference node 1 (`Bone01`) because node 0 is their `Scene Root`; subtracting
  one from that value assigns the geometry to the wrong node.


# Issues to fix

 - Invisible CandleProp controller instances (`Visible=0`) intentionally have
   neither a model nor a fallback billboard in the viewport. They remain
   addressable through the object list and display a marker while selected.
 - Levels are mirrored between the editor and the game. Example: added an ExitTrigger
   to the left side of the peasant in the BOOTCAMP, but in the game it appears to the right side
 - In order to see the changes in the game, a new game has to be started. It looks like the
   saved game files store the level data state.
 - Interface improvements:
    - When an object is selected, don't display it's parameters right now (except position/rotation) - add "Edit params" button instead
