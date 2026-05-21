# MM9 Level Data & Properties

## DAT And Properties

- `.DAT` world files use version 66.
- `WorldObject` properties must preserve type codes and string length quirks.
- Some fields are stored as IEEE-754 float bit patterns in LongInt slots.
  `NPCNbr`, `Scale`, `RangeAttackType`, `TrapLevel`, `TreasureLevel`, and
  several treasure fields can use this pattern.
- `MoveToFloor` is an engine-side object property, not an editor-only flag.
  The editor exposes it as a checkbox but does not simulate all runtime
  physics behavior.


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

Physical-door clone implementation status:

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
- `app/editor.py` exposes the workflow through `Tools -> Clone Physical
  Door...` and a toolbar `Clone Door...` button. The dialog lists existing
  physical door links in the active level and suggests a collision-free clone
  name. After the user confirms, the next BSP click creates the pending clone.
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
- The clone dialog updates the suggested new name when the source door changes.
  Numeric and side-suffix paired doors use pair-friendly names, for example
  `MonsterDoor1` -> `MonsterDoorClone1`/`MonsterDoorClone2` and
  `StoreDoorLeft` -> `StoreDoorCloneLeft`/`StoreDoorCloneRight`.
- The clone dialog also shows source details: door class, paired leaf, portal
  name, and polygon count. This makes it easier to avoid cloning a control door
  or unexpected portal-linked door.
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


## Converted Prefabs

Converted DEdit prefab `.dat` files are valid DAT v66 mini-worlds, but they do
not all have the same shape:

- `PreFabs/Doors/A1_Door.dat` contains one `RotatingDoor` object named
  `Door1`, plus BSP models named `Door1`, `PhysicsBSP`, and `VisBSP`.
  The same-name `Door1` BSP model is controller geometry, similar to physical
  doors in shipped levels.
- `PreFabs/Fences&Gates/OldWoodFence1.dat` contains no WorldObjects. Its
  geometry is only in system-named BSP records: `PhysicsBSP` and `VisBSP`, each
  with 345 polygons and matching bounds.

`features/prefabs/inspector.py` is the Stage 1 read-only layer for this work. It parses a
converted prefab DAT, classifies BSP model roles (`geometry`,
`controller_geometry`, `physics`, `visibility`, `skybox`), reports object
classes, bounds, polygon/point/texture counts, and carries BSP parse warnings.

Stage 2 backend import is in place in `features/prefabs/import_static.py` and
`project.ImportPrefabBspOp`:

- It imports static BSP records only. It does not yet import prefab
  WorldObjects, scripts, doors, elevators, triggers, or traps.
- By default it imports `PhysicsBSP` when a converted prefab only contains the
  system records `PhysicsBSP`/`VisBSP`. `VisBSP` can carry leaf/PVS payloads
  that are not safe to splice into another level; `PhysicsBSP` has the plain
  polygon data we need, and the writer patches it to normal static-submodel
  flags before insertion.
- Imported records are renamed with a collision-free target model name, then
  translated/rotated using the same raw BSP transform machinery as physical
  door clones. Surface UV projection vectors and point normals are transformed
  too.
- `LevelEdit.preview_bsp()` includes pending prefab imports, and saving writes
  them through the generalized `door_bsp_writer.serialize_world_with_bsp_clones`
  path. Save preview and `manifest.json` record `prefab_imports` and imported
  BSP model counts.
- Stage 3 placement UI is in place through `Tools -> Import Static Prefab
  BSP...` and the toolbar `Import Prefab...` button. The command validates the
  converted prefab, asks for a target BSP model name, enters one-shot place
  mode, and creates an `ImportPrefabBspOp` at the clicked BSP surface. The
  viewport refreshes through `LevelEdit.preview_bsp()` so the imported static
  geometry appears before save.
- Static prefab imports now create a real same-named `WorldObject` controller
  while pending and on save. This is required for the imported BSP to render in
  the game.
- Pending prefab objects can be selected, dragged/elevated, rotated with
  `[`/`]`, edited through Properties `Pos`/`Rotation`, deleted, and undone.
  These edits mutate `ImportPrefabBspOp.target_pos` / `target_yaw` and rebuild
  the BSP preview. The operation remains the source of truth, so the visible
  object and imported BSP stay together instead of drifting.
- Stage 4 validation is in place through `features/prefabs/validation.py`.
  Save Preview and `manifest.json` now report non-blocking warnings when a
  static import ignores prefab WorldObjects, imports `PhysicsBSP` polygon data
  as a normal visible submodel, uses source models with `Default` texture
  names, creates duplicate/colliding BSP names, has empty/no-polygon source
  models, or imports `VisBSP`.
- Collision investigation found that shipped blocking helper brushes usually
  use an `InvisibleBrush` controller paired with a same-named normal
  `info_flags=2` BSP submodel. In `BOOTCAMP.DAT`, six `InvisibleBrush` rows
  follow the pattern `Visible=0`, `Solid=1`, `RayHit=1`, `BoxPhysics=0`; most
  are simple 6-polygon boxes. Imported visible `WorldObject`/BSP pairs render
  in-game but do not collide by themselves.
- Stage 1 prefab collision experiment is now opt-in in the import UI. When the
  user enables it, `ImportPrefabBspOp.collision_mode="box_approx"` adds a
  second hidden controller/BSP pair named `<PrefabName>_Collision`. The
  controller is cloned from a real target-level `InvisibleBrush` template, not
  synthesized from `WorldObject`, so class-specific fields such as
  `DamagerStuff`, `SurfaceType`, and damage flags are preserved. Its `Pos` is
  set to the generated collision BSP bounds center.
- `STURMFORDCITY.DAT` has an existing fence/blocker pattern worth mirroring:
  long, thin 6-polygon `InvisibleBrush` boxes using `Firethrough.dtx`, often
  only 8 units thick and 56 units tall. The prefab collision helper now thins
  the shortest horizontal axis to 8 units instead of using the full visual
  bounding-box depth.
- The BSP writer now patches scaled clone bounds, points, polygon centers,
  planes, point normals, and OPQ texture projection vectors. This is needed
  because collision/render helpers use internal plane/polygon data, not just
  min/max bounds. Older `collision_mode="invisible_bsp"` remains supported as
  a diagnostic fallback that duplicates the prefab geometry instead of using
  the scaled box.
- The viewport treats `Firethrough.dtx` as a helper material. It still draws
  the helper BSP geometry for inspection/selection, but it does not bind the
  repeated red texture; those triangles fall back to the solid editor BSP
  colour. This is editor-only and does not alter saved DAT texture data.
- Stage 2 collision controls are in place in the prefab import flow. The old
  yes/no prompt is now a small options dialog with modes `box_approx`, `none`,
  and diagnostic `invisible_bsp`; `box_approx` exposes configurable blocker
  thickness and defaults to the proven 8-unit Sturmford-style thin box. The
  selected thickness is stored on `ImportPrefabBspOp.collision_thickness` and
  persisted in `.mm9mod` format version 6.
- Preview performance pass 1 is in place for cloned doors and imported
  prefabs. `door_clone.build_preview_bsp()` and
  `prefab_import.build_preview_bsp()` now wrap the target BSP shallowly, so
  unchanged base level `WorldModelMesh` objects keep identity across preview
  rebuilds. `View3D.reload_level_state()` now asks `MeshCache.retain_models()`
  to prune only stale preview meshes instead of invalidating every uploaded
  BSP mesh. This keeps static terrain/building meshes cached while a door or
  prefab preview submodel moves.
- Stage 3 multi-segment collision is in place for imported prefab
  `box_approx` helpers. The import options dialog exposes "Max segment length"
  (default 512 units). If the generated thin collision box is longer than that
  along its long horizontal axis, it is split into multiple same-template
  `InvisibleBrush` BSP segments named `<PrefabName>_Collision1`,
  `<PrefabName>_Collision2`, etc. Each segment gets its own hidden
  `InvisibleBrush` WorldObject controller, and the segment length is persisted
  as `ImportPrefabBspOp.collision_segment_length` in `.mm9mod` format version
  7.
- Stage 4 helper BSP preview modes are in place under
  `View -> Collision BSP` as `hidden`, `solid`, `wireframe`, and `raw`.
  Helper BSPs are detected by
  `_Collision` model names or helper textures such as `Firethrough.dtx`.
  `hidden` skips them, `solid` draws a translucent magenta helper, `wireframe`
  draws a magenta wire overlay, and `raw` uses normal BSP texture/range
  handling.
- Stage 5 save-time validation now warns when a static prefab import has
  visible geometry but no collision helper, and checks generated
  `collision_box` helpers for suspicious dimensions: extremely thin brushes,
  very tall brushes, or extreme aspect ratios.


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
- Node transforms use column translation; using row-vector math collapses
  animated characters into imploded-looking geometry.
- Animated top-level character vertices observed so far use 0-based bone
  indices; rigid props often use 1-based indices.


# Issues to fix

 - CandleProp are not rendered in any level (examples: 1000TERRORS.DAT). CandleProps
   have visible = 0, so perhaps this is the intended behavior? Requires investigation.
 - Levels are mirrored between the editor and the game. Example: added an ExitTrigger
   to the left side of the peasant in the BOOTCAMP, but in the game it appears to the right side
 - In order to see the changes in the game, a new game has to be started. It looks like the
   saved game files store the level data state.
 - Interface improvements:
    - When an object is selected, don't display it's parameters right now (except position/rotation) - add "Edit params" button instead
