# MM9 Mod Editor Handoff

This project is a three-dimensional visual editor for Might and Magic IX
compiled world files (`.DAT`, version 66) directly from the game's REZ
archives. It builds on the bundled `mm9_patcher` parser/serializer and lets
modders open a level, place objects, edit properties, stage fresh NPC
dialogue, and save patched replacement REZ archives.

## Current State

The editor is centered on the OpenGL viewport. Object placement,
selection, movement, height control, yaw rotation, and property editing all
happen through the three-dimensional view and the Properties panel.

The normal workflow is:

```text
Open level from WORLDS.REZ -> select/add object -> click a BSP surface to place
-> drag/nudge/elevate/rotate in the viewport
-> edit fields in Properties
-> Save -> timestamped patched data/*.REZ output + manifest
```

The editor never writes over the live game archives during ordinary Save.
Opened source archives are backed up under `backups/`; committed output goes
under `output/<timestamp>/`. Installing output to the game is an explicit menu
action that creates a separate install backup first.

## Runtime Layout

Expected runtime layout:

```text
Might and Magic IX/
  data/
    WORLDS.REZ
    RUDE.REZ
    SCRIPTS.REZ
    TEXTURES.REZ  # optional but used for textured BSP previews
    SKINS.REZ     # optional but used for object skins
    MODELS.REZ    # optional but used for ABC meshes
    DATA.REZ      # optional but used for actor/material table research
  mm9_editor/
```

Python dependencies for the viewport:

```sh
pip install PyOpenGL PyOpenGL_accelerate pyopengltk
```

If OpenGL packages are unavailable, `view3d.gl_view` shows a placeholder with
the install hint. There is no separate editor mode fallback.

## Main Modules

| Path | Purpose |
|---|---|
| `mm9_editor.py` | Main Tk application. Owns menus, toolbar, project state, level panel, `View3D`, properties panel, save dialog, placement callbacks, movement/rotation/elevation commit logic, and preset commands. |
| `project.py` | In-memory project model. `LevelEdit` owns source level data and pending ops. `AddOp`, `MoveOp`, `EditOp`, and `DeleteOp` materialize pending edits and feed the save plan. |
| `mm9_patcher/mm9_patch.py` | DAT v66 parser/serializer. Trusted core for `World`, `WorldObject`, and `Property`. |
| `catalog.py` | Builds/loads `catalog.json`, indexing WorldObject classes and model filenames from shipped levels. |
| `catalog_panel.py` | Left panel listing level objects and launching the Add Object dialog. |
| `add_object_dialog.py` | Class and preset picker used before entering placement mode. |
| `properties_panel.py` | Right-side inspector. Includes transform controls for `Pos`, yaw from `Rotation[1]`, and `MoveToFloor`. |
| `preset_manager.py` / `preset_dialog.py` | User preset persistence and dialogs. |
| `npc_dialog.py` | Fresh NPC dialogue setup before placement. |
| `diff_panel.py` | Save/commit dialog for patched REZ output and optional RUDE archive entries. |
| `project_io.py` | `.mm9mod` save/open support for pending operations. |
| `bsp.py` | BSP parser plus optional floor raycast utility. The viewport uses parsed BSP geometry for rendering and click placement. |
| `mm9_rezmgr.py` | REZ reader/writer retained for CLI, legacy projects, and advanced tooling. |
| `game_resources.py` | REZ-backed resource provider for game-style virtual paths. Reads from detected `data/*.REZ` archives and materializes cached asset trees for existing loaders. |

## Game Install Detection And Resources

- `autodetect.detect()` requires a nearby MM9 install. It checks the editor
  folder, then its parent, for a `data/` folder containing the required
  `WORLDS.REZ`, `RUDE.REZ`, and `SCRIPTS.REZ` archives. `--game-root <path>`
  can be used to point at an install explicitly.
- `GamePaths.archive_path(key)` returns the detected game archive when
  available, and `GamePaths.has_archive(key)` reports whether a game archive
  was found.
- `GamePaths.resources()` creates a `GameResources` provider. The main editor
  stores it as `EditorApp.resources`.
- `GameResources` accepts game virtual paths such as `WORLDS/STURMFORDCITY`,
  `RUDE/NPC1`, `SCRIPTS/YRSA`, `TEXTURES/...`, `SKINS/...`, and `MODELS/...`.
  It reads from detected REZ archives only.
- `Open Level from WORLDS.REZ...` launches the
  REZ picker.
- Catalog template lookup falls through to `GameResources`, so placing a
  catalog class/model can load the recorded source level from `WORLDS.REZ`
  without manually extracted files.
- Fresh NPC number suggestion scans `RUDE/NPC<N>` through
  `GameResources`, excluding the special `NPC997`-`NPC999` journal/note/award
  files. In the bundled extracted data this currently suggests `438`.
- The cache bridge is in place for viewport assets. `autodetect` resolves
  a writable cache folder, and `GameResources.cache_archive_tree()` can
  materialize REZ entries into a versioned cache keyed by archive path, size,
  and mtime. `EditorApp` passes cached folders to the existing DTX/ABC
  loaders. Cache extraction strips the archive root, so
  `TEXTURES/A/B.DTX` becomes `<cache>/A/B.DTX`, matching the existing loader
  lookup rules.
- REZ output is in place for level saves. Normal Save never
  overwrites the live game archive. For REZ-sourced levels, the save plan
  writes patched archives to `output/<batch>/data/<archive>.REZ`; for example,
  editing `WORLDS/STURMFORDCITY` from `WORLDS.REZ` writes
  `output/<batch>/data/WORLDS.REZ`. Multiple edited levels from the same
  source archive are grouped into one `RezWriter` pass so edits do not clobber
  each other. A loose review copy of each patched entry is also written under
  `output/<batch>/changed_entries/<virtual-path>.DAT`, and `manifest.json`
  includes an `archives` section listing source archives, output archives, and
  replaced virtual entries.
- Archive patch planning is in place. `SavePlan.archive_patches`
  describes every archive output before commit. Fresh NPC RUDE registrations
  now patch `RUDE.REZ` directly when a game `data/RUDE.REZ` archive is
  detected: `NPCNAME`, `TOPBLURB`, and `NPC<N>` are written to
  `output/<batch>/data/RUDE.REZ`, with review copies under
  `changed_entries/RUDE/*.RUDE`. There is no loose RUDE staging workflow.
- Explicit install is in place. `File -> Install Output to Game...`
  asks the user to choose an `output/<batch>` folder, reads its manifest-aware
  patched archive list, confirms the affected archives, backs up the live game
  `data/*.REZ` files under `backups/install_<timestamp>/data/`, then replaces
  only those archives in the detected game `data` folder. The installer logic
  lives in `install_manager.py`; it writes an `install_manifest.json` next to
  the backup and uses a temporary `<archive>.installing` copy before
  `os.replace()`.
- Restore is in place. `File -> Restore Installed Backup...` accepts
  an install backup folder, its `data` subfolder, or a folder with
  `install_manifest.json`. It backs up the current live archives under
  `backups/restore_<timestamp>_current/data/`, then restores the original REZ
  files from the selected install backup. `restore_manifest.json` records what
  was restored and where the pre-restore live files were saved.

## `view3d/`

| Path | Purpose |
|---|---|
| `gl_view.py` | Tk/OpenGL viewport. Handles camera modes, BSP rendering, object model rendering, billboard handles, colour-buffer picking, click placement, drag movement, keyboard nudges, height changes, yaw rotation, fog, and status text. |
| `camera.py` | Orbit and fly camera math plus unprojection for picking and surface placement. |
| `gl_mesh.py` | BSP mesh upload/draw. Triangulates polygons, filters non-render helper surfaces, normalizes OPQ texture coordinates, groups triangles by texture, and draws textured or fallback-colour geometry. |
| `gl_objects.py` | Coloured billboard handle batch. Handles selection/picking markers and live VBO position updates during edits. |
| `gl_object_models.py` | ABC object mesh renderer. Resolves object `Filename`, `Skin`, `Pos`, `Rotation`, and `Scale`; draws supported meshes; leaves unsupported objects as handles. |
| `abc_loader.py` | Conservative ABC parser/uploader for static props and supported static NPC/creature poses. |
| `dtx.py` | DTX loader for DXT1, DXT5, and BGRA textures, including alpha inspection for material decisions. |
| `gl_shader.py` | Embedded GLSL programs and shader wrapper. |

## Viewport Editing Controls

Orbit mode is the editing mode:

- Click object handle/model: select.
- Click BSP while placing: create the pending object at the exact hit point.
- Drag selected object: move X/Z while preserving current Y.
- Arrow keys: nudge selected object X/Z relative to camera.
- `PageUp` / `PageDown`, or `E` / `Q`: adjust selected object height.
- `[` / `]`: rotate selected object yaw by editing `Rotation[1]` in radians.
- `Shift`: larger nudge/rotation step.
- `F`: fit camera to level bounds.
- `P`: toggle render profiling to stderr.

Fly mode is for navigation:

- Drag: look.
- `W/A/S/D`: move horizontally.
- `Q/E`: move down/up.
- `Shift`: faster camera.

## Placement And Transform Commit Flow

`View3D` exposes callbacks:

- `on_select(world_index)`
- `on_place_xyz(wx, wy, wz)`
- `on_move_xyz(world_index, wx, wy, wz)`
- `on_elevate(world_index, new_y)`
- `on_rotate(world_index, rotation_tuple)`

`mm9_editor.py` maps these to project operations:

- New objects become `AddOp(template, overrides={"Pos": [...]})`.
- Existing object movement/elevation coalesces into `MoveOp.new_pos`.
- Existing yaw rotation coalesces into `MoveOp.new_rot`.
- Pending added object movement/rotation updates `AddOp.overrides`.
- Property panel edits use `EditOp` for existing objects and override updates
  for pending added objects.

The selected object is tracked as `_selected_world_index`; this avoids relying
on object identity because the panels and viewport frequently work with
materialized copies.

Transform interaction is optimized for responsiveness:

- Mouse dragging previews movement inside `View3D` by patching the local
  materialized object copy and sprite VBO position; the project model is
  committed once on mouse release.
- Keyboard nudges, wheel height changes, and yaw rotation preview immediately
  in the viewport and debounce their project commit until input settles.
- `View3D.flush_pending_transforms()` commits any pending preview before save,
  project save/load, level changes, property edits, or sprite reloads.
- `_GLCanvas._request_render()` coalesces redraw requests so drag/key bursts do
  not queue unbounded `tkExpose()` calls.
- Transform commits no longer rebuild the level object list or 3-D sprite VBO;
  they update the patch model and refresh the selected property panel only.

## Rendering Performance

- `gl_mesh.build_bsp_draw_batch()` uploads visible BSP meshes and resolves
  texture ranges once when a level loads. Per-frame drawing now calls
  `draw_bsp_batch()` instead of rewalking `bsp_world.world_models`.
- `gl_object_models.build_render_items()` caches the object-to-ABC-mesh
  mapping, split skin list, texture IDs, and alpha modes for the current
  materialized object set. Per-frame object drawing only recomputes the
  transform matrix for objects whose cached mesh is visible.
- During a 3D object drag, the viewport skips non-dragged ABC meshes and avoids
  the back-to-front sprite sort. The dragged object/model and handles still
  preview immediately.
- Lightweight profiling is built into `_GLCanvas._profile_record()`. Press
  `P` in the viewport, or set `MM9_EDITOR_PROFILE=1`, to print average frame,
  BSP, ABC, and sprite timings every 120 frames.

## Important Reverse-Engineering Findings

### DAT And Properties

- `.DAT` world files use version 66.
- `WorldObject` properties must preserve type codes and string length quirks.
- Some fields are stored as IEEE-754 float bit patterns in LongInt slots.
  `NPCNbr`, `Scale`, `RangeAttackType`, `TrapLevel`, `TreasureLevel`, and
  several treasure fields can use this pattern.
- `MoveToFloor` is an engine-side object property, not an editor-only flag.
  The editor exposes it as a checkbox but does not simulate all runtime
  physics behavior.

### NPC Dialogue

- Dialogue is keyed by `NPCNbr`, not `ScriptName`.
- `NPCNbr=0` is reserved for script-driven interaction.
- A fresh NPC needs a new NPCNbr plus RUDE entries in `NPCNAME.RUDE`,
  `TOPBLURB.RUDE`, and `NPC{N}.RUDE`.
- In the current extracted data, `NPC1.RUDE` through `NPC437.RUDE` are normal
  NPC dialogue files. `NPC997.RUDE`, `NPC998.RUDE`, and `NPC999.RUDE` are
  special journal/metadata tables rather than normal conversations:
  - `NPC997.RUDE` is labelled `Quest Notes` in `NPCNAME.RUDE` and contains
    quest journal entries.
  - `NPC998.RUDE` is labelled `Auto Notes` and contains automatically learned
    notes such as barrel effects, trainer locations, and promotion hints.
  - `NPC999.RUDE` is labelled `Awards` and contains completion/achievement-like
    records such as cleared quests, promotions, and cleansed town portals.
  Avoid allocating fresh custom NPC dialogue ids `997` through `999`.
- `TOPBLURB.RUDE` has three columns:
  `NPCNbr,initialState,"opening blurb"`. In the extracted shipped data the
  first two columns always match, so a simple fresh NPC can use
  `N,N,"Hello..."`.
- `NPC<N>.RUDE` rows have 30 CSV columns. The practical layout is:
  `NPCNbr,currentState,branchId,"player text","npc response",nextState`,
  followed by 24 numeric condition/effect columns. All 4,507 shipped rows
  observed in the extracted data have this shape, and the first column always
  matches the `NPC<N>.RUDE` filename number.
- `currentState` is the active dialogue menu/state. Every row with the same
  `currentState` becomes one selectable player option in that menu. `branchId`
  is unique within a state and acts as the option/order id; shipped data allows
  gaps and non-contiguous numbering.
- `nextState` controls what happens after the NPC response:
  - a positive value switches to that `currentState` in the same
    `NPC<N>.RUDE` file;
  - the same state value loops back to the same menu;
  - `999` is commonly used as a conventional "Goodbye" state whose single row
    closes the dialogue;
  - `-1` closes the dialogue directly;
  - other negative values call engine-native service/action screens rather
    than another RUDE state.
- Observed negative `nextState` meanings in shipped data include `-2` shop,
  `-3` training, `-4` skill expert/master training, `-5` travel/passage,
  `-6` bank, `-7` inn/tavern room or business flow, `-8` temple healing,
  `-10` hire/join/board flow, `-11` dismiss hired NPC, and `-16` temple
  donation. Treat the less common values (`-13`, `-14`, `-15`) as
  engine/script-coupled until tested in-game.
- The 24 trailing numeric columns appear to combine conditions and effects.
  The most useful quest/journal columns observed so far are:
  - effect column 1 (absolute CSV column 6): require that the player already
    has a key/flag.
  - effect column 10 (absolute CSV column 15): grant a key/flag.
  - effect column 11 (absolute CSV column 16): grant a second key/flag.
  - effect column 15 and nearby later columns (absolute CSV column 20+):
    require that the player does not yet have a key/flag, commonly used to
    hide options or journal rows after they become stale.
- Quest journal entries are linked by these same key/flag ids. For example,
  Yrsa's `NPC1.RUDE` grants keys such as `1`, `27`, `40`, `92`, and `93`;
  `YRSA.SCR` reacts to some of those keys in `OnRudeExit`; and
  `NPC997.RUDE` has quest-note rows gated by the same key ids. A simple custom
  quest can likely be made RUDE-only by granting a new unused key in the NPC's
  dialogue and adding a matching gated row to `NPC997.RUDE`. Scripted rewards,
  completion checks, world changes, or quest-complete sounds still require
  script support.
- `NPC998.RUDE` and `NPC999.RUDE` use the same row structure as normal RUDE
  files: rows are displayed when their key/flag conditions are satisfied.
  `NPC998.RUDE` rows mostly gate on knowledge/trainer/barrel keys, while
  `NPC999.RUDE` rows gate on completion/promotion/award keys.
- A minimal branching fresh NPC can therefore be authored as:

  ```text
  NPCNAME.RUDE: 438,"Test Peasant"
  TOPBLURB.RUDE: 438,438,"Hello! I'm an NPC. Are you heroes?"
  NPC438.RUDE:
  438,438,1,"Yes.","Good!",438,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
  438,438,2,"No.","Too bad!",438,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
  438,438,3,"Goodbye.","Farewell.",-1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
  ```
- For multi-step dialogue, set `nextState` to a new positive state id and add
  rows for that state. For example, `438,438,1,"Ask...","Answer",10,...`
  followed by one or more rows whose second column is `10`.

### Treasure Chests

Chest loot usually comes from native class logic and DAT properties, not an
attached script. Relevant fields include `Random`, `Gold`, `GoldOnly`,
`Item1` through `Item5`, `TrapLevel`, `TreasureLevel`, `TreasureOptions`, and
`TreasureType0_7`.

For example, `DRAGONSTADIUM.DAT` contains black chest objects with no
`ScriptName`, `Random=1`, `TreasureLevel=6.0`, `TrapLevel=20.0`, and
`TreasureType0_7=7.0`; these are max-tier random treasure chests.

### Level Transitions

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

### REZ Archives

- REZ type tags are byte-reversed on disk.
- Level entries are named like `WORLDS/BOOTCAMP` with no extension.
- Editability should be detected by payload magic bytes, not by entry name.
- `NextWritePos` points to the directory-tree boundary, so safe writing is a
  full output rewrite, not append-in-place.

## ABC Rendering Notes

Current support is intentionally conservative:

- Rigid/static props with validated layouts render as meshes.
- Top-level NPC/creature ABCs from `MODELS.REZ` render as static posed
  meshes, including weighted/complex models via a conservative LOD0 preview.
- A few known script-applied `SetModelFilenames` swaps are previewed when the
  raw DAT stores a placeholder or missing model.  For example,
  BATHHOUSE.DAT's Ebora and concubines store `models\Honk.abc`, but their
  scripts swap them to `models\ebora.abc`; Ebora uses
  `skins\succubusredgstrng.dtx`, while concubines use a deterministic
  `skins\Siren1.dtx` preview for the game's randomized Siren skin.
- Colloidal Soldier/Warrior/Guardian variants are data-table aliases over the
  shared `models\ColloidalWarrior.abc` mesh.  DAT references to
  `models\ColloidalSoldier.abc` or `models\ColloidalGuardian.abc` are previewed
  with the shared mesh plus the matching `Colloidal1` or `Colloidal3` skin.
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

## Current Caveats

- Source REZ archives must remain available for `.mm9mod` project replay.
- The editor saves patched REZ output under `output/<batch>/data/`; installing
  it into the game folder is an explicit backed-up action.
- Socket attachments are optional visual polish, not a current priority.
- Undo/redo is operation-based.  Top-level adds, deletes, property edits, and
  transform commits are undoable/redoable; edits to not-yet-saved added objects
  still mutate that object's `AddOp.overrides` and are undone as part of the
  whole add rather than as separate property-level steps.
- Editor helper billboards are hidden by default in the 3-D view.  Use the
  `Helpers` toolbar toggle to show trigger/sound/marker/world handles and
  `BlueWater` markers while debugging level logic.

## Suggested Next Work

1. Optional finer-grained pending-add history if editing newly added objects
   before save becomes a common workflow.

### Editor Billboard Visibility Notes

Implemented goal: hide non-gameplay-visual helper billboards by default while
keeping a single control-panel checkbox for debugging/level-authoring work that
needs them.

Implementation summary:

1. Added an editor-only visibility predicate for WorldObject billboards:
   - classes like `BlueWater`, `ExitTrigger`, `AIRail`, `AmbientSound`
   - categories like `trigger`, `sound`, `marker`, and `world`
   - real selectable/gameplay visuals such as NPCs, monsters, props and visible interactive objects.
2. Added a checkbox to the 3-D control bar beside the existing Camera/Fog
   controls:
   - label: `Helpers`
   - default: off
   - on: show all editor helper billboards
   - off: hide the helper/control billboard classes above
3. The checkbox value is stored on `_GLCanvas` as
   `_show_helper_billboards = False`.  Toggling it rebuilds sprites from the
   current materialized object list and requests a render.
4. Filtering happens at sprite-upload time in `view3d/gl_objects.py`, not
   while drawing:
   - `_build_arrays()` / `upload_objects()` accept `include_helpers` and
     `selected_index`.
   - This keeps rendering and picking consistent because hidden helpers are
     not in the billboard VBO or pick pass.
5. Selection behavior:
   - the left object list still shows every object.
   - selected helper objects stay included in the billboard VBO even when
     helpers are off, so list selection remains spatially understandable.
6. Object meshes are independent:
   - this filter is for billboards/handles only.
   - actual BSP geometry and ABC object meshes keep their existing
     visibility rules.
7. Added focused tests around the pure predicate:
   - `BlueWater`, `ExitTrigger`, `AIRail`, `AmbientSound` hidden by default.
   - normal props/NPCs/monsters still visible.
   - selected hidden helper is included when the selected-index override is
     active.

### Undo/Redo Notes

Implemented goal: let the user undo and redo top-level adds, deletes, property
edits, and transform commits without changing the existing explicit save/review
flow.

Implementation summary:

1. Per-level history state now lives on `LevelEdit`:
   - `redo_ops: List[Any] = field(default_factory=list)`
   - helper methods such as `append_op()`, `undo_last_op()`,
     `redo_last_op()`, and `clear_redo()`.
   - All new user edits should go through the helper so redo is cleared after
     a fresh branch of work.
2. `L.world` remains the loaded baseline; `L.materialize()` is the edited
   view.  Edit and transform callbacks no longer mutate
   `L.world.objects[...]` directly after appending an op.
3. Materialized row indices are mapped back to baseline object indices before
   creating `EditOp`, `MoveOp`, or `DeleteOp`, so pending deletes do not shift
   future edits onto the wrong DAT object.
4. Drag/rotation/elevation updates coalesce into one undoable `MoveOp`:
   - If a `MoveOp` already exists for the selected existing object, update it.
   - Do not push each intermediate drag tick onto history.
   - When a new transform action begins after another action, append a fresh
     `MoveOp` and clear redo.
5. Pending added objects are represented by an `AddOp`, not by baseline
   indices:
   - Editing or moving a pending add can mutate that `AddOp.overrides`.
   - For now, the whole pending add is the undo unit until override-level undo
     is worth adding.
6. Menu/keyboard/UI entry points:
   - `Edit > Undo`, `Ctrl+Z`
   - `Edit > Redo`, `Ctrl+Y`, `Ctrl+Shift+Z`
   - labels enable/disable based on active level history.
7. After undo/redo, refresh consistently:
   - rebuild materialized object list and 3-D render items,
   - refresh the level panel,
   - reselect the best surviving object index when possible,
   - clear the properties panel if the selected object was undone/deleted.
8. Save/project interactions:
   - `.mm9mod` persists pending `ops` only; redo history is omitted.
   - On save commit, promote materialized worlds to baseline, then clear both
     `ops` and `redo_ops`.
   - Opening a project starts with empty redo stacks.

## Legends of Might and Magic Interop

The editor and patcher were tested against `CHATEAUESCAPE.DAT` from
Legends of Might and Magic (LoMM, 2001). LoMM is built on the same LithTech
engine family as MM9 and its DAT files use the same v66 container, which
makes large parts of a LoMM level directly reusable. The findings below
are the basis of the `lomm_to_mm9.py` converter and of the editor's preview behaviour
for LoMM content.

### Container Compatibility

- LoMM `.DAT` files are version 66 with the same 44-byte header layout
  (version, obj_pos, ren_pos, 32 zero bytes).
- `World.load()` parses the file without modification, and
  `serialize_objects()` round-trips byte-identical
  (3,132,910 bytes for `CHATEAUESCAPE.DAT`). This means the BSP,
  lightmap, and PVS regions transfer to MM9 unchanged.
- Property type codes, Pascal string layout, byte-reversed REZ type
  tags, and the float-bits-in-LongInt quirk all match.

### Class Differences

In `CHATEAUESCAPE.DAT` (742 objects, 29 classes), 5 classes are not
registered in MM9 and account for 46 objects. They must be removed or
retyped before the level loads cleanly in MM9:

| LoMM class | Count | LoMM purpose | MM9 nearest match |
|---|---|---|---|
| `CandleWall` | 24 | wall candle prop with engine-driven flame | `CandleProp` or `WallTorch` |
| `Orc` | 17 | LoMM enemy AI; instances use `models\Goblin.abc` | `HalfOrcSoldier`, `LizardOrc`, or any MM9 monster |
| `GoodKingRescueZone` | 2 | LoMM multiplayer "rescue the king" volume | none — delete |
| `BuyZone` | 2 | LoMM Counter-Strike-style buy region | none — delete |
| `Timer` | 1 | LoMM scripted timer | none — delete |

The remaining 24 classes (`AIRail`, `Door`, `Prop`, `Light`,
`DirLight`, `AmbientSound`, `StartPoint`, `WorldObject`, `BlueWater`,
`OutsideDef`, `TreasureChest`, `Brazier`, `Fire`, etc.) are all
registered in MM9.

### Property Differences Within Shared Classes

The parser keys properties by name, so unknown properties are silently
dropped by the engine and missing properties fall back to engine
defaults. The differences that matter in practice:

- `StartPoint` is missing MM9's `MovePlayerToFloor`. Without it the
  player may spawn floating or below the floor. Always add this with
  value `1` during conversion.
- `WorldProperties` is missing MM9's `CanSaveGame` and
  `CanMiniSaveGame`. Without them the player cannot save in the level.
  Add both with value `1` during conversion.
- `WorldProperties` carries LoMM-only `MusicDirectory`,
  `InstrumentFiles`, `AmbientList`, `CruisingList`, `HarddrivingList`,
  `CDTrack`, and `ScenarioNbr`. They are harmless; MM9 ignores them.
- `Brazier` and `Fire` in LoMM use a single `Type` field instead of
  MM9's full `Fire*`/`Smoke*`/`Light*` particle parameter set. Without
  conversion, the fire and smoke effects do not render.
- `TreasureChest` in LoMM uses `KeyItemId` and `SpawnItem`. MM9
  expects `Random`, `Gold`, `GoldOnly`, `Item1`..`Item5`, `TrapLevel`,
  `TreasureLevel`, `TreasureOptions`, `TreasureType0_7`, plus AI
  reachability fields. Without conversion the chest opens but is
  empty.
- `Cow` in LoMM has `PickRandomWeapon`, `TeamNbr`, `WeaponItemNbr`
  (LoMM faction combat) and is missing the MM9 AI rail / wander /
  range-attack / repopulation stack. The cow loads but is inert.
- Most other shared classes (`Door`, `Prop`, `AIRail`, `AIBarrier`,
  `AmbientSound`, water variants, `WorldObject`, `Ladder`) are
  missing MM9's engine-level additions: `Alpha`, `BoxPhysics`,
  `DisableFog`, `NeedsTick`, `TouchNotify`, `ShouldMiniSave`,
  `OneTimeDamage`, `DamageAIOnly`, `DamagePlayerOnly`. All harmless —
  engine defaults apply.
- `StartPoint.PlayerNbr` in CHATEAUESCAPE is stored as the IEEE-754
  float bit pattern of the slot number (for example `1090519040 = 8.0`
  bits). MM9 reads `PlayerNbr` as raw `uint32`. MM9 is single-player
  so non-zero `PlayerNbr` values are usually irrelevant; the converter
  leaves them as-is.

### Asset Compatibility

`CHATEAUESCAPE.DAT` references 32 distinct `Filename` values and 29
distinct `Skin` values. Resolving them against MM9's `MODELS.REZ`
(case- and extension-insensitive) shows:

- 29 of 32 models exist in MM9. Missing: `Barrel02`, `Chest-Lacquer`,
  `Painting_Rectangle`, `Goblin`
- 24 of 29 skins exist. Missing: `Barrel02`, `Chest-Lacquer`,
  `HorseStatue2` (MM9 has `HorseStatue` without the `2`),
  `Painting_Rectangle5`, `Chest-Rusty01` (MM9 ships the matching
  model but under a different skin name), `Goblin`
- Ambient sound paths (`Sounds/Ambient/...`) were not verifiable against
  the unavailable `SOUNDS.REZ` / `DATA.REZ` in the test workspace, but
  missing sounds are silent rather than fatal.

The level references no custom `ScriptName` values, so no
`SCRIPTS.REZ` patching is needed for the level to load.

### Goblin ABC Preview Limitation

`Goblin.abc` (`MODELS/GOBLIN`, 985,190 bytes, 17 parent animations,
68 nodes, 3 pieces, `nVerts = 602`, `nVertWeights = 1863`) is a valid
LithTech ABC, but the editor's static-pose preview renders it as an
imploded shard cluster. Cause: `view3d/abc_loader.py` walks vertex
records at a fixed 48-byte stride that assumes single-weight skinning.
LoMM's Goblin has ~3 weights per vertex on average, so the stride
desyncs after the first multi-weight vertex and subsequent
"bone_index" reads return garbage values. `_bake_with_node_matrices()`
short-circuits the moment any vertex's bone index is missing from the
node pose dict, so no baking happens and vertices stay in their
bone-local coordinates.

Shipped MM9 characters parse cleanly because their
`nVertWeights / nVerts` ratio is between 1.05 and 1.15 (Bjarni,
EvilSorcerer, Ghoul, Dagrell, Cow): the fixed-stride read happens
to align well enough for the static bake to succeed.

The MM9 game executable has the engine's full weighted-skinning
pipeline and renders the Goblin correctly at runtime — the imploded
appearance is a preview-only artifact of the editor's conservative
ABC parser. A fix would change `_parse_lod_geometry` to consume
`4 + 8 * n_weights + 12 + 28 = 44 + 8 * n_weights` bytes per vertex
and use the first weight's bone for the static bake.

### Level Connectivity

`CHATEAUESCAPE.DAT` has 16 `StartPoint`s clustered around two arrival
areas and **0** `ExitTrigger`s. The level has no DAT-driven exit back
to the rest of the world. To wire it into MM9's world graph, add an
`ExitTrigger` whose `DestinationWorld` is the source MM9 level and add
a matching `StartPoint` there. The transition wizard described in the
"Level Transitions" section above applies.

### Converter Pipeline

`lomm_to_mm9.py` implements the findings above as a YAML-driven
pipeline (`lomm_to_mm9.yaml`):

1. **Convert classes via templates.** Replaces instances of a class
   (including LoMM-only enemies like `Orc`) with a clone of a named MM9
   template. This stage runs first so retyped objects survive the
   unknown-class drop.
2. **Drop unknown classes.** Any WorldObject whose class is still not
   in MM9's catalog (and wasn't retyped in stage 1) is removed.
3. **Add missing properties** to shared classes (`StartPoint`,
   `WorldProperties`).
4. **Asset audit** walks every remaining object's `Filename`
   (`.abc`/`.lta`/`.ltb`) and `Skin` (`.dtx`) and three-way classifies:
   in MM9, in `lomm_data/` only, or missing entirely. The "in LoMM
   only" bucket is the punch list of files to copy into MM9's
   `MODELS.REZ` / `SKINS.REZ` before the level renders correctly.

`_Mm9Catalog.load_level()` accepts the level path with or without the
`.DAT` suffix, so the loader transparently retries the alternate form
when the literal lookup fails.

The converter reuses `mm9_patcher.mm9_patch` so its output round-trips
byte-identical through the MM9 parser. See README.md for invocation
syntax.

A separate `catalog_lomm.json` (built by running `catalog.py
build-from-rez mm9_data/worlds_lomm.rez --out catalog_lomm.json`)
mirrors `catalog.json`'s shape but indexes the LoMM levels. It's
useful when designing experimental conversion rules: each LoMM-only
class entry includes the `template` (source level + instance) you can
clone from, the union of `property_names` actually observed on
instances, and the set of `filenames` (model paths) the class uses
across LoMM levels.

## Issues to fix
 - MOUNTAINPASS level: All Wolf enemies (`GreyWolf`, `BlackWolf`, `RedWolf`)
   use the `sheep` model (filename: `models/sheep.abc`). Proposed fix: replace with
   `models/wolf.abc`. Or perhaps leave it in place? It looks like some kind of an
   Easter egg rather than error. But anyway, it's worth investigating, why the game
   uses wrong FileNames for certain objects
 - MOUNTAINPASS level: most Harpy enemies (`WingedAberration`, `WingedMutant`, `WingedOddity`)
   use the `flyingicky` model (filename: `models/flyingicky.abc`). Proposed fix: replace
   with `models/wingedmutant.abc`
 - CandleProp are not rendered in any level (examples: 1000TERRORS.DAT). CandleProps
   have visible = 0, so perhaps this is the intended behavior? Requires investigation.
 - 1000TERRORS level: Njam and NjamtheMeddler are not rendered
 - Filtering in the left "Objects" panel works weird and displays console errors
 - Levels are mirrored between the editor and the game. Example: added an ExitTrigger
   to the left side of the peasant in the BOOTCAMP, but in the game it appears to the right side
 - DragonKing in DRAGONSTADIUM is rendered without textures
 - Children models either not rendered at all or appear imploded.
   Example: DRANGHEIM, CommonerChildElfChildA
 - STURMFORTCITY level: most NPCs aren't rendered at all, other are imploded
