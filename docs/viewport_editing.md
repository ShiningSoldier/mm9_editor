# `view3d/`

| Path | Purpose |
|---|---|
| `gl_view.py` | Tk/OpenGL viewport. Handles camera modes, BSP rendering, object model rendering, billboard handles, colour-buffer picking, click placement, drag movement, keyboard nudges, height changes, yaw rotation, fog, and status text. |
| `camera.py` | Orbit and fly camera math plus unprojection for picking and surface placement. |
| `gl_mesh.py` | BSP mesh upload/draw. Triangulates polygons, filters non-render helper surfaces, normalizes OPQ texture coordinates, groups triangles by texture, and draws textured or fallback-colour geometry. |
| `sky.py` | Resolves `DemoSkyWorldModel`, `SkyPointer`, and `WorldProperties` records into ordered, camera-relative sky layers and an optional `SoftSky` cloud shell. |
| `gl_objects.py` | Coloured billboard handle batch. Handles selection/picking markers and live VBO position updates during edits. |
| `gl_object_models.py` | ABC object mesh renderer. Resolves object `Filename`, `Skin`, `Pos`, `Rotation`, and `Scale`; draws supported meshes; leaves unsupported objects as handles. |
| `abc_loader.py` | Conservative ABC parser/uploader for static props and supported static NPC/creature poses. |
| `dtx.py` | DTX loader for DXT1, DXT5, and BGRA textures, including alpha inspection for material decisions. |
| `gl_shader.py` | Embedded GLSL programs and shader wrapper. |

# Viewport Editing Controls

Orbit mode is the editing mode:

- Click object handle/model: select.
- Click BSP while placing: create the pending object at the exact hit point.
- `Tools -> Clone Physical Door...` opens a source-door picker, then the next
  BSP click places the cloned physical door at that point.
- `Tools -> Inspect Prefab DAT...` opens a converted prefab `.dat` and shows a
  read-only report of its objects, BSP model roles, bounds, texture counts, and
  warnings.
- `Tools -> Import Static Prefab BSP...` opens a converted prefab `.dat`, asks
  for a new BSP model name, then the next BSP click places a static prefab
  import preview.
- `View -> Toggle object helpers` toggles billboards for objects that already
  have visible 3-D models, such as
  NPCs, monsters, furniture, chests, and props.
- `View -> Toggle world helpers` toggles editor/service billboards such as
  AI rails/barriers, ambient sounds, triggers, weather/world markers, doors,
  lights, and `BlueWater` markers.
- `View -> Helper BSP` controls helper BSP overlays. `Normal` hides helper
  materials while keeping real art and water substitutes; `Helpers
  translucent` shows selected helper roles as colour-coded translucent
  geometry.
- `View -> Helper BSP` role toggles control AI rails, collision/firethrough,
  water volumes, triggers, sound, and sky/visibility helpers.
- The sky/visibility group also contains BSP surfaces marked with the engine's
  `SURF_SKY` flag. These surfaces are sky portals rather than ordinary world
  geometry, so normal mode hides them.
- In normal rendering, those portals reveal the sky world-models named by
  `DemoSkyWorldModel` and `SkyPointer`. The viewport maps the main camera into
  the authored `SkyDims` inner box, preserves object `Index` ordering, and
  applies the `WorldProperties.SoftSky` cloud layer when its texture is
  available. A missing legacy MM9 cloud path falls back to the shipped
  `TEXTURES\Skybox\Clouds1.dtx` texture.
- Translucent BSP materials are composited after the sky pass. This preserves
  authored combinations such as BOOTCAMP's `StainedGlass2` church windows,
  whose glass world-models intentionally sit over circular sky portals.
- The collision/firethrough group also contains BSP surfaces marked with the
  engine's `SURF_INVISIBLE` flag and same-named BSP models owned by invisible
  catalog-classified world helpers. In normal mode these match the game's
  hidden rendering behavior; examples include BOOTCAMP's table collision
  boxes and CHASMOFTHEDEAD's `AIBarrier` geometry.
- Drag selected object: move X/Z while preserving current Y.
- Arrow keys: nudge selected object X/Z relative to camera.
- `PageUp` / `PageDown`, or `E` / `Q`: adjust selected object height.
- `[` / `]`: rotate selected object yaw by editing `Rotation[1]` in radians.
- `Shift`: larger nudge/rotation step.
- `F`: fit camera to level bounds.

Fly mode is for navigation:

- Drag: look.
- `W/A/S/D`: move horizontally.
- `Q/E`: move down/up.
- Mouse wheel: dolly forward/back along the viewing direction.
- `Shift`: faster camera.
- `F`: fit camera to normal visible level geometry.

# Placement And Transform Commit Flow

`View3D` exposes callbacks:

- `on_select(world_index)`
- `on_place_xyz(wx, wy, wz)`
- `on_move_xyz(world_index, wx, wy, wz)`
- `on_elevate(world_index, new_y)`
- `on_rotate(world_index, rotation_tuple)`

`app/editor.py` maps these to project operations:

- New objects become `AddOp(template, overrides={"Pos": [...]})`.
- Physical door clones become `CloneDoorOp(source_name, new_name,
  target_pos)`. Placement is one-shot because clone names must stay unique.
- Existing object movement/elevation coalesces into `MoveOp.new_pos`.
- Existing yaw rotation coalesces into `MoveOp.new_rot`.
- Pending added object movement/rotation updates `AddOp.overrides`.
- Pending cloned door movement/elevation retargets the whole `CloneDoorOp`.
  Moving either leaf of a paired pending clone preserves the pair spacing.
- Deleting either pending leaf removes the pending `CloneDoorOp`.
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

# Rendering Performance

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
- Lightweight profiling is built into `_GLCanvas._profile_record()`. Set
  `MM9_EDITOR_PROFILE=1` before launch to print average frame, BSP, ABC, and
  sprite timings every 120 frames. There is no user-facing profiling shortcut.


## Editor Billboard Visibility Notes

Implemented goal: split billboard helper visibility into object helpers and
world/service helpers.

Implementation summary:

1. World/service helper classification is derived from the active catalog:
   - `object.lto` inheritance identifies actor and model-object classes.
   - model-valued `Filename` defaults from `object.lto` and explicit model
     paths observed in level DAT files identify other visible objects.
   - a class with neither actor/model inheritance nor a model resource is a
     world helper. This covers MM9 and LoMM service classes without maintaining
     separate class-name lists.
   - converted/custom objects fall back to their per-instance `Filename` and
     actor-property signature when their class is absent from the catalog.
   - old catalogs are annotated in memory when loaded; regenerated catalogs
     persist the `world_helper` decision, reason, and evidence source per class.
2. Added `View -> Toggle object helpers`:
   - default: off
   - on: show billboards for objects that already render as 3-D models
   - off: hide those model-backed billboards unless the object is selected or
     actively dragged
3. Added `View -> Toggle world helpers` for editor/service billboards:
   - default: off
   - on: show all world/service helper billboards
   - off: hide data-classified helper/control billboards unless selected.
4. Added `View -> Helper BSP` and moved helper BSP controls out of the
   viewport toolbar. The menu exposes `Normal`, `Helpers translucent`, and
   role toggles for AI rails, collision/firethrough, water volumes, triggers,
   sound, and sky/visibility helpers.
5. State is split on `_GLCanvas`:
   - `_show_object_helper_billboards = False`
   - `_show_world_helper_billboards = False`
   - `_helper_bsp_mode = "normal"`
   - `_helper_role_groups = all helper role groups`
6. Filtering happens in two places:
   - world/service helpers are filtered at sprite-upload time in
     `view3d/gl_objects.py` via `_build_arrays()` / `upload_objects()` with
     `include_world_helpers`, `object_helper_indices`, and `selected_index`.
   - model-backed object billboards are suppressed at draw time via
     `should_draw_billboard_for_modeled_object()` because the viewport only
     knows which objects successfully rendered as ABC meshes after
     `build_render_items()`.
7. Selection behavior:
   - the left object list still shows every object.
   - selected world/service helper objects stay included in the billboard VBO
     even when world helpers are off, so list selection remains spatially
     understandable.
   - selected or dragged model-backed objects keep their billboard visible
     even when object helpers are off.
8. Object meshes are independent:
   - these controls are for billboards/handles only.
   - actual BSP geometry and ABC object meshes keep their existing visibility
     rules.
9. Added focused tests around the pure predicates:
   - MM9 and LoMM object.lto actor/model inheritance and Filename defaults.
   - model-free controls and audio Filename values are helpers.
   - explicit per-instance models and converted-LoMM actor signatures override
     helper classification.
   - modeled objects can keep object-helper billboards even if their catalog
     category is otherwise a world-helper category.
   - selected hidden world helpers are included when the selected-index
     override is active.
   - model-backed billboards are hidden, shown, selected, and dragged according
     to the object-helper toggle.

## Undo/Redo Notes

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

