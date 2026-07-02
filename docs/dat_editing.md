# MM9 DAT to ED Reconstruction

Last updated: 2026-07-02

This document is the technical handoff for reconstructing Might and Magic IX
compiled DAT worlds into old DEDit-compatible ED source worlds. It intentionally
focuses on DAT -> ED reconstruction. Read-only glTF export remains useful for
inspection in external 3D tools, but source editing should happen in DEDit once
the ED is reconstructed.

## Goal

The project goal is:

```text
MM9 WORLDS.REZ / DAT -> reconstructed ED v1249 -> old DEDit editing
-> LithTech 2.1 Processor -> rebuilt DAT -> playable in MM9
```

The success condition is a DAT that fresh-loads in the game with correct
rendering and collision. A DAT that merely parses is not enough.

## Current Status

The DAT -> ED path is now the main direction.

- DAT-derived ED prefab generation works for old DEDit. Generated real-object
  and generated-object prefabs can be inserted into BOOTCAMP, compiled by the
  LithTech 2.1 Processor, and loaded in game with visible geometry and collision.
- DAT-derived full-world ED skeleton generation works for small and medium
  generated worlds. DEDit accepts the generated ED, Processor compiles it, and
  the game loads the resulting DAT.
- Generated full-world ED files now include minimal gameplay scaffolding:
  `WorldProperties`, `StartPoint`, and `Light`.
- BOOTCAMP Terrain0 reconstruction reached a manually validated state in
  `bootcamp_connected_terrain_patch_twin_clusters_v30_diagnostics.ed`.
  The user loaded it in DEDit and in game, and reported that everything looks
  and works correctly.
- The v30 terrain source coverage report has no blockers and no gaps:
  3,901 sampled playable Terrain0 polygons are covered by generated ED terrain
  top faces.
- The v30 terrain cutout report identifies 9 candidate cutouts, 7 of them as
  covered cutouts. The rectangular holes observed during testing correspond to
  original building/support footprints rather than generator loss.
- The lower BOOTCAMP sand plane is ignored for playable terrain coverage by
  default. It exists below the playable green ground and is not reachable during
  normal gameplay.
- The live editor UI has been cleaned up around the new direction. It keeps the
  glTF inspection export and removes the old editable mesh sidecar import/export
  commands. Project loading/appending now rejects retired mesh-sidecar operation
  kinds with an explicit error.
- The Tools menu now includes `Generate DEDit ED from DAT...`, which stages the
  active DAT, generates a full-world ED candidate, writes coverage diagnostics
  when Terrain0 is available, and saves a text report, selection report, and
  JSON acceptance manifest beside the generated ED.
- Indoor/static-shell DAT -> ED generation now has an initial budgeted
  `PhysicsBSP` shell reconstruction path. For levels without Terrain0, the app
  can emit closed slab brushes from capped `PhysicsBSP` collision polygons
  instead of blocking solely because the main shell lives in `PhysicsBSP`.
- DAT -> ED default model selection now skips models whose polygons are all
  known helper textures such as `rail.dtx`, `Invisible.dtx`, `Firethrough.dtx`,
  `GreenScreen.dtx`, `SoundOnly.dtx`, or `SkyMarker.dtx`. Those helpers need
  object-class reconstruction, not normal visible Brush output.
- ANSKRAMKEEP no-helper PhysicsBSP shell candidate was manually validated
  through DEDit, Processor, and the game. It generated much more visible
  geometry than the non-PhysicsBSP pass, including most walls. Helper textures
  no longer appear in DEDit or in game. Some shell geometry is still missing,
  so PhysicsBSP source-polygon selection and helper/object reconstruction remain
  active work.
- The ANSKRAMKEEP no-helper candidate is now locked as a generated-candidate
  regression: selected normal models, helper-only exclusions by semantic role,
  generated Brush/polygon counts, zero `rail.dtx` Brush faces, and local
  Processor log counters are covered by focused tests.
- This no-helper candidate is the current preferred game-validation baseline,
  but it is not yet the final editor-authoring experience: helper-textured
  systems are hidden instead of reconstructed as editable semantic objects.

## Local Resources

Known local resources for this work:

- `C:\lithtech\mm9_editor`: this editor and reconstruction code.
- `C:\lithtech\mm9_editor\mm9_data`: local extracted MM9 data used by tests and
  diagnostics.
- `C:\Program Files (x86)\GOG Galaxy\Games\Might and Magic 9\data`: installed
  game data, including `MM9.dep`, `object.lto`, `WORLDS`, and `PreFabs`.
- `C:\lithtech\Lith21tools`: old LithTech 2.1 DEDit and `Processor.exe`, the
  toolchain that matches MM9's ED/DAT pipeline.
- `C:\lithtech\Lith22tools`: newer DEDit toolchain. Useful for comparison, but
  not the MM9-native compiler path.
- `C:\lithtech\LTWorldConverter`: external reference repository. Treat as
  read-only reference code.
- `C:\lithtech\EDUnpacker`: external Pascal reference repository. Treat as
  read-only reference code.
- `C:\lithtech\PreFabs` and the installed game's `data\PreFabs`: real source
  prefabs used as ED layout fixtures.

The external repositories should not become runtime dependencies of the editor.
Use them to cross-check format behavior and then implement the required behavior
inside `mm9_editor`.

## Format Facts

MM9 compiled worlds are LithTech/Talon DAT version `66`. They are usually stored
inside `WORLDS.REZ`; extracted world DAT files may also exist under the game
`data\WORLDS` directory.

The DAT v66 header observed by the editor is:

- `uint32 version`
- `uint32 ObjectDataPos`
- `uint32 RenderDataPos`
- eight unused or dummy `uint32` values

Important compiled world models:

- `Terrain*`: visible terrain and world geometry. `Terrain0` is the main outdoor
  terrain model.
- `PhysicsBSP`: collision geometry and physics helper data.
- `VisBSP`: visibility/culling geometry and partitioning.
- Additional skybox/helper/system BSP models may exist but are not primary ED
  reconstruction targets.

MM9-compatible old DEDit uses binary ED source files plus project metadata from
`MM9.dep` and `object.lto`.

Known ED version facts:

- MM9 old DEDit source worlds use ED version `1249`.
- Shogo/Blood 2 style source worlds use ED version `1247`.
- ED v1247 brush surfaces store texture scale/offset/rotation fields.
- ED v1249 brush surfaces store LithTech OPQ texture projection vectors.
- ED v1249 stores trailing per-surface `dwFlags` and RGB shade bytes after the
  texture name.
- Full-level ED files can be zlib-compressed in blocks. Prefab ED files are
  often uncompressed.

## Implemented Components

Core DAT -> ED modules to keep:

- `features/dat_editing/legacy_ed.py`: ED reader/layout scanner and real-fixture
  analysis helpers.
- `features/dat_editing/legacy_ed_writer.py`: clean ED v1249 writer primitives.
- `features/dat_editing/surrogate_ed.py`: high-level DAT-derived ED generation.
- `features/dat_editing/compiler_strategy.py`: acceptance reports, terrain
  support generation, cutout coverage, source coverage, and compiler diagnostics.
- `features/dat_editing/source_world.py`: source-world parsing helpers.
- `features/dat_editing/geometry_scene.py`: shared geometry representation used
  by source-world and glTF export paths.
- `features/dat_editing/geometry_mesh.py`: retained OBJ scene parsing and
  `GeometryScene` -> BSP mesh conversion helpers used by DAT -> ED tests and
  legacy source-prefab fixtures.
- `features/dat_editing/geometry_export_common.py`: read-only DAT geometry export
  helpers shared by supported glTF inspection and legacy reference code.
- `features/dat_editing/terrain_bsp_patch.py`: legacy/reference Terrain* BSP
  patch diagnostics, render-tail classification audits, local edit-plan
  containers, topology-preserving BSP record patch helpers, and regression
  helpers preserved while DAT -> ED terrain reconstruction is validated.
- `features/dat_editing/terrain_semantics.py`: shared Terrain*/PhysicsBSP/VisBSP
  identity helpers and preferred Terrain0 selection behavior.
- `features/dat_editing/terrain_reconstruction.py`: Terrain* polygon boundary
  cleanup, triangulation, X/Z polygon hit sampling, coverage footprint building,
  cutout model footprint clustering, walkability classification, support mode
  normalization, support item preparation/selection/splitting, and support
  placement helpers used by generated ED terrain support and coverage reports.
- `features/dat_editing/gltf_export.py`: read-only inspection export.
- `features/dat_editing/bsp_record_inspector.py`: DAT/BSP comparison utilities.

Current ED writer behavior:

- Writes ED v1249 header/version data.
- Writes the world info string used by old DEDit.
- Writes OPQ texture projection data.
- Writes surface flags and RGB shade bytes.
- Writes prefab and full-world node containers.
- Supports generated root/world hierarchy and DEDit-loadable object nodes.
- Supports generated brush nodes from DAT world model polygons.
- Supports minimal generated gameplay objects for test worlds.
- Supports budgeted `PhysicsBSP` shell slab brushes for indoor/static-shell
  diagnostics.

## Terrain0 Reconstruction

BOOTCAMP Terrain0 source reconstruction is currently based on generated support
brushes rather than in-place DAT patching.

Important Terrain0 findings:

- BOOTCAMP has two terrain layers: playable grass/rock terrain and an
  inaccessible lower sand plane. Coverage diagnostics ignore the lower sand
  plane unless explicitly asked otherwise.
- Many grass polygons are large n-gons, not only triangles or quads.
- Some DAT terrain polygon point lists include repeated coarse/refined boundary
  vertices. Naive triangulation can produce degenerate triangles.
- Canonicalizing terrain polygon boundaries by removing duplicate and collinear
  boundary points produced better source faces for DEDit/Processor.
- Rectangular holes in the playable terrain can be intentional building/support
  cutouts. They should be checked with cutout coverage diagnostics before being
  treated as missing generated terrain.

The current accepted BOOTCAMP candidate is:

```text
C:\lithtech\mm9_editor\output\full_world_skeleton_source\
bootcamp_connected_terrain_patch_twin_clusters_v30_diagnostics.ed
```

Associated diagnostics:

```text
bootcamp_connected_terrain_patch_twin_clusters_v30_diagnostics_terrain_cutout_coverage.json
bootcamp_connected_terrain_patch_twin_clusters_v30_diagnostics_terrain_support_source_coverage.json
```

The source coverage report status is `terrain_support_source_coverage_complete`
with `missing_sample_count = 0`. The cutout coverage report status is
`terrain_cutout_coverage_built` with `covered_cutout_count = 7`.

## PhysicsBSP Static Shell Reconstruction

Some indoor/static MM9 levels store important room and wall shell geometry in
`PhysicsBSP`. ANSKRAMKEEP is the clearest known case:

- Original shipped ED: `2953` Brush records / `16763` polygons.
- Current DAT-derived non-PhysicsBSP selection: `362` Brush records /
  `2601` polygons.
- DAT `PhysicsBSP`: `12007` points / `6450` polygons.

The implemented first pass is controlled and budgeted:

- `surrogate_ed.py` can generate one closed slab Brush per selected
  `PhysicsBSP` polygon.
- Slabs are extruded along the source polygon normal and validated with the
  same outward-plane enclosure check used by Terrain0 support brushes.
- Invalid, degenerate, or non-enclosing polygons are skipped.
- The app enables this path by default only when no Terrain0 support path is
  active and a `PhysicsBSP` model is present.
- Selection reports mark `PhysicsBSP` as `physics_shell_source` when it feeds
  shell slab generation.
- Acceptance reports and manifests include `include_physics_shell_patch`.
- The Processor budget guard still applies; the default cap remains
  `1500` generated Brush objects and `12000` generated polygons.
- Acceptance generation reduces the requested `PhysicsBSP` source-polygon cap
  when the predicted generated slab face count would exceed the Processor
  polygon budget.

This is not complete source CSG recovery. It is a diagnostic reconstruction
that should make indoor/static-shell levels less sparse while staying within
Processor-safe budgets. ANSKRAMKEEP should be the next manual validation target.

## Helper Textures

Old DEDit displays helper textures that are not meant to appear as ordinary
game render geometry. Known examples:

- `TEXTURES\LevelTextures\Misc\rail.dtx`: AI rail/path helper geometry.
- `TEXTURES\LevelTextures\Misc\Invisible.dtx` and `Firethrough.dtx`: collision
  or invisible helper geometry.
- `TEXTURES\LevelTextures\Misc\greenscreen.dtx`: trigger/helper geometry.
- `TEXTURES\LevelTextures\Misc\soundonly.dtx`: sound/helper geometry.
- `TEXTURES\SkyBox\SkyMarker.dtx`: sky visibility helper geometry.

In shipped ANSKRAMKEEP, `rail.dtx` appears in DEDit along with `AIRail` object
records, but those rail helpers are not rendered as ordinary visible level
geometry in game. The DAT -> ED path now excludes helper-only DAT models by
default and records them as `excluded_helper_texture` in selection reports.

Generated synthetic helper faces, such as `PhysicsBSP` slab side/back faces
using `Invisible.dtx`, use helper texture flags patterned after the shipped ED
fixtures.

Important caveat: helper-only models are not meant to be discarded forever.
The current filter prevents helper polygons from being emitted as ordinary
visible/solid Brush geometry. The final converter must reconstruct those helper
systems semantically, for example as `AIRail`, `InvisibleBrush`, sky/portal,
trigger, or sound helper objects and brush properties.

This explains the current ANSKRAMKEEP behavior: removing visible helper-texture
Brush output makes the rebuilt DAT look more like the shipped game level, but it
also hides some authoring aids in DEDit until those helpers are reconstructed as
their real object/property classes.

## Validation Policy

For any generated ED candidate:

1. Load the ED in old DEDit.
2. Save a copy from DEDit if the test is checking editor survivability.
3. Compile with LithTech 2.1 `Processor.exe`.
4. Install the resulting DAT into the game data path or test archive.
5. Fresh-load the level in MM9.
6. Check visible geometry from normal camera angles.
7. Check party collision on terrain and generated static objects.
8. Keep the generated ED, Processor log, output DAT, and JSON diagnostics when a
   test passes or reveals a useful failure.

Processor warnings are not automatically fatal. Treat them as fatal only when
they correlate with missing render geometry, broken collision, DEDit rejection,
or game-load failure.

## Cleanup Plan

The editor should be simplified around DAT -> ED reconstruction plus read-only
inspection export.

### Phase 1: UI Surface

Status: implemented.

- The Tools menu now keeps the supported geometry commands:
  `Generate DEDit ED from DAT...` for DAT -> ED candidate generation and
  `Export DAT Geometry as glTF for Inspection...` for read-only inspection.
- DAT -> ED generation writes a `*_dat_to_ed_acceptance_manifest.json` file
  that records source/staged DAT paths, generated ED path, selected models,
  generated counts, diagnostic manifest links, manual test placeholders,
  blockers, cautions, and next manual steps.
- DAT -> ED generation also writes `*_dat_to_ed_selection_report.json`, a
  per-world-model explanation of selected models, Terrain* support sources,
  system exclusions, skyboxes, empty/oversized models, filtered requested
  models, and linked terrain coverage diagnostics.
- The app command uses budgeted connected Terrain* support selection with a
  positive default radius, so levels with Terrain0 can generate local playable
  support without selecting every connected Terrain0 polygon in a large world.
- The app command also applies a LithTech 2.1 Processor budget guard
  (`1500` generated Brush objects, `12000` generated polygons by default).
  Over-budget EDs are written as diagnostics but reported as blocked, because
  very large DAT-derived terrain support patches can explode into millions of
  Processor split polygons during `Joining polies`.
- The app command can generate a budgeted `PhysicsBSP` shell patch for
  indoor/static-shell candidates when no Terrain* support path is active.
  If that path is disabled and a large `PhysicsBSP` is present, the generated
  ED is still written for inspection but is marked as a sparse blocked
  diagnostic.
- Legacy editable mesh import/export commands were removed from the menu and
  from `app/editor.py`.
- Object manipulation callbacks no longer special-case retired mesh imports.

### Phase 2: Project Operations

Status: partially implemented.

- Project file loading and operation appending now reject retired mesh-sidecar
  operation kinds.
- Save preview no longer reports the retired operation rows.
- glTF inspection export no longer depends on the retired sidecar exporter.
  Shared read-only export helpers now live in
  `features/dat_editing/geometry_export_common.py`.
- Terrain*/PhysicsBSP/VisBSP identity helpers have been moved into
  `features/dat_editing/terrain_semantics.py` and are used by both DAT -> ED
  code and the remaining legacy patch module.
- Terrain* polygon boundary canonicalization and collinear cleanup have been
  moved into `features/dat_editing/terrain_reconstruction.py`. DAT -> ED
  coverage diagnostics and terrain support generation now call this module
  directly; the old `surrogate_ed.py` private helper aliases were removed.
- Terrain* support patch item preparation and bounds/connected-radius selection
  also live in `terrain_reconstruction.py`; `surrogate_ed.py` now focuses on
  turning selected terrain support items and placements into ED brush records.
- Terrain* n-gon triangulation helpers have also moved into
  `terrain_reconstruction.py`; `surrogate_ed.py` calls them when a generated
  terrain support brush mode needs triangle support items.
- Terrain support selection and brush mode normalization are centralized in
  `terrain_reconstruction.py`; `surrogate_ed.py` keeps only ED brush grouping
  choices that depend on generated brush convexity checks.
- Terrain polygon normal and walkable-vertex classification helpers are now in
  `terrain_reconstruction.py`; the old Terrain* vertex patch module delegates to
  them for compatibility.
- X/Z polygon point tests and sample-grid helpers are centralized in
  `terrain_reconstruction.py`; source coverage and terrain cutout reports call
  these shared helpers.
- Retained source-prefab tests now use `geometry_mesh.py` instead of private
  helpers from the retired mesh-sidecar import module.
- DAT Terrain coverage footprint building and DAT polygon texture lookup are
  centralized in `terrain_reconstruction.py`; cutout coverage now keeps the
  shared `TerrainCoverageItem` shape through report sampling.
- Generated ED terrain coverage footprint building is also centralized in
  `terrain_reconstruction.py`; source coverage reports now compare source and
  generated coverage through shared footprint and point-hit helpers.
- Terrain cutout model footprint filtering and nearby-model clustering are
  centralized in `terrain_reconstruction.py`; the compiler strategy code keeps
  the report-specific cutout classification and manifest formatting.
- Basic Terrain* vector bounds, box-overshoot, distance/dot, and split-plane
  classification primitives are centralized in `terrain_reconstruction.py`; the
  old in-place vertex patch module delegates to them for compatibility.
- Terrain polygon area and plane helpers are centralized in
  `terrain_reconstruction.py`; generated ED brush construction delegates to
  those helpers when writing surface planes.
- Topology-preserving BSP record patch helpers and derived polygon plane/center
  and point-normal recomputation are folded into `terrain_bsp_patch.py`, which
  is the only remaining reference module that needs them.
- Shared topology-preserving BSP edit plan dataclasses are folded into
  `terrain_bsp_patch.py`, which is the only remaining reference module that
  needs them.
- The lower-level classes and modules still exist temporarily because Terrain0
  reconstruction and compatibility/reference code still share some old helpers.
- The retired operation classes and save-plan branches have been deleted from
  `core/project.py`; only `core/project_io.py` keeps old operation-kind strings
  so older `.mm9mod` files fail with an explicit retired-workflow error.
- The old `features/dat_editing/vertex_edit.py` module has been deleted after
  its remaining reference-only BSP record patch helpers and edit-plan containers
  were folded into `terrain_bsp_patch.py`.
- The old `features/dat_editing/export_roundtrip.py` OBJ/sidecar exporter has
  been deleted. Read-only inspection export is handled by `gltf_export.py` and
  `geometry_export_common.py`.
- The old `features/dat_editing/terrain_vertex.py` module name has been retired.
  Its remaining reference-only Terrain* BSP patch diagnostics now live in
  `terrain_bsp_patch.py`.

Remaining work:

- Keep shrinking `terrain_bsp_patch.py` when reference-only diagnostics become
  unnecessary, but leave it intact while DAT -> ED terrain reconstruction is
  still being validated against compiled DAT behavior.
- Keep DAT parsing, DAT diffing, generated ED reports, compiler diagnostics, and
  glTF inspection export.

### Phase 3: Module And Test Retirement

Status: sidecar module retirement implemented; Terrain0 diagnostics preserved
under a legacy/reference module.

No standalone retired mesh-sidecar modules remain. The old
`features/dat_editing/terrain_vertex.py` name is deleted; retained Terrain*
render-tail and guarded in-place patch diagnostics are isolated in
`features/dat_editing/terrain_bsp_patch.py` as reference code.

Deleted modules:

- `features/dat_editing/export_roundtrip.py`
- `features/dat_editing/bsp_edit_plan.py`
- `features/dat_editing/bsp_record_patch.py`
- `features/dat_editing/gltf_import.py`
- `features/dat_editing/mesh_import.py`
- `features/dat_editing/obj_workflow.py`
- `features/dat_editing/replace_submodel.py`
- `features/dat_editing/terrain_vertex.py`
- `features/dat_editing/vertex_edit.py`

Retired tests:

- `test_export_roundtrip.py`
- `test_obj_workflow.py`
- `test_mesh_import.py`
- `test_replace_submodel.py`
- `test_vertex_edit.py`

Added tests:

- `test_retired_mesh_workflow.py`

Tests to keep and strengthen:

- `test_legacy_ed.py`
- `test_surrogate_ed.py`
- `test_source_prefab_golden.py`
- `test_compiler_strategy.py`
- `test_source_world.py`
- `test_geometry_scene.py`
- `test_gltf_export.py`
- `test_bsp_record_inspector.py`
- `test_terrain_bsp_patch.py`

### Phase 4: Safety Checks

After cleanup, run focused tests for:

- ED v1249 read/write fixtures.
- Prefab surrogate generation.
- Full-world skeleton generation.
- Terrain cutout and source coverage reports.
- glTF inspection export.
- App startup and Tools menu construction.

## Next Engineering Steps

### Stage A: Lock The ANSKRAMKEEP No-Helper Baseline

Status: implemented.

Goal: preserve the first useful indoor/static-shell result as a regression
fixture.

- Add a generated-candidate regression for ANSKRAMKEEP no-helper selection:
  expected selected normal models, excluded helper-only models, generated brush
  count, generated polygon count, and zero `rail.dtx` Brush faces.
- Treat the no-helper ANSKRAMKEEP result as the current baseline for game
  validation: helper textures absent from DEDit/game, collision present, most
  walls recovered, some shell geometry still missing.
- Parse the latest ANSKRAMKEEP Processor log and record warning counts:
  `Unable to generate a plane`, input polygons, output polygons, tree depth,
  unseen removed polygons, and runtime.
- Add a report summary field for helper-only exclusions by role:
  `aiRail`, `collision`, `skyVisibility`, `trigger`, `sound`, and `water`.
- Keep the current no-helper ED path as the default for indoor/static-shell
  testing until semantic helper reconstruction exists.

### Stage B: Improve PhysicsBSP Shell Coverage

Status: next after Stage A.

Goal: recover more missing walls without exceeding old Processor limits.

- Add PhysicsBSP shell coverage diagnostics that compare generated shell slabs
  against source `PhysicsBSP` polygons and report uncovered source polygon
  counts by role: floor, ceiling, side wall, helper/special, and degenerate.
- Replace pure largest-area selection with a balanced selector:
  include important vertical/side-wall polygons first, keep enough floor/ceiling
  support for collision, then fill remaining budget by area.
- Add a connected/spatial shell selector so large indoor rooms do not spend the
  whole budget on distant or low-value polygons.
- Add a slab-quality filter for Processor warnings:
  reject or simplify polygons with tiny area, repeated/near-duplicate points,
  near-zero thickness after extrusion, or non-stable plane generation.
- Re-test ANSKRAMKEEP after each selector change and compare:
  visible walls, collision, warning count, output polygon count, and Processor
  runtime.

### Stage C: Reconstruct Helper Semantics

Status: blocked on source/DAT correlation work.

Goal: restore helper systems without rendering helper textures as normal game
geometry.

- Use shipped ANSKRAMKEEP ED as the first oracle. Correlate `rail.dtx` Brush
  geometry and `AIRail` object records against helper-only DAT models such as
  `AITrk*`.
- Implement an `AIRail` reconstruction report first:
  source helper model name, rail-textured geometry bounds, nearest original
  `AIRail` object pattern, generated object count, and skipped/ambiguous rails.
- Once the object layout is understood, emit generated `AIRail` object records
  instead of visible rail Brush geometry.
- Repeat the same semantic reconstruction pattern for:
  `Invisible.dtx`/`Firethrough.dtx` collision helpers, `SkyMarker` sky helpers,
  `GreenScreen` trigger helpers, and `SoundOnly` sound helpers.
- Update selection reports so helper-only models move from
  `excluded_helper_texture` to `helper_semantic_source` as each helper class is
  implemented.

### Stage D: Reconstruct Gameplay Objects

Status: after helper semantics starts.

Goal: make reconstructed EDs usable as game levels, not only geometry probes.

- Use `MM9.dep` and `object.lto` as local class/property references.
- Compare shipped ED object records with compiled DAT object data for
  ANSKRAMKEEP and BOOTCAMP.
- Implement object reconstruction in priority order:
  `WorldProperties`, `StartPoint`, `Light` are already generated;
  next are `Door`, `Trigger`/`ExitTrigger`, `InvisibleBrush`, `AIRail`,
  `AmbientSound`, and static props.
- Preserve original object names and positions where DAT object data exposes
  them.
- Extend acceptance manifests with object-class coverage:
  original DAT object count, generated ED object count, matched classes,
  skipped classes, and blockers.

### Stage E: Return To Outdoor/Terrain Worlds

Status: after ANSKRAMKEEP indoor path stabilizes.

Goal: apply the same reporting discipline to larger outdoor levels.

- Use ISLEOFASHES Processor logs to reduce invalid/problem terrain support
  brushes instead of only lowering total brush count.
- Compare BOOTCAMP, DOOKSCASTLE, ANSKRAMKEEP, and ISLEOFASHES reports to derive
  per-world default selection rules.
- Repeat BOOTCAMP-style Terrain0 reconstruction on another shipped level without
  relying on original ED source files.

### Stage F: Workflow And Cleanup

Status: ongoing.

Goal: keep the tool usable while reconstruction broadens.

- Document the manual ED -> DAT -> game install/test workflow, including backup
  rules and BOOTCAMP routing to test worlds.
- Keep `terrain_bsp_patch.py` as reference/regression code until multiple
  DAT -> ED worlds pass DEDit, Processor, and game validation.
- Revisit archived/retired diagnostics only after helper semantics and object
  reconstruction have stable tests.
