# MM9 DAT to ED Reconstruction

Last updated: 2026-07-06

This document describes the current technical contract for reconstructing
Might and Magic IX compiled DAT worlds into old LithTech/DEDit-compatible ED
source worlds.

## Goal

The target workflow is:

```text
MM9 WORLDS.REZ / DAT -> reconstructed ED v1249 -> DEDit 2.1 editing
-> LithTech 2.1 Processor -> rebuilt DAT -> playable in MM9
```

Success requires a freshly loaded DAT in the game with correct rendering,
collision, and reconstructed gameplay object behavior. A DAT that only parses,
opens in DEDit, or compiles is not enough.

## User-Facing Commands

The Tools menu is intentionally small:

- `Clone Physical Door...`
- `Import Static Prefab BSP...`
- `Generate DEDit ED from DAT...`
- `Export DAT Geometry as glTF for Inspection...`

Special validation profiles still exist as internal/test hooks, but they should
not be added back to the menu unless they become stable user workflows.

## Local Resources

- `C:\lithtech\mm9_editor`: editor and reconstruction code.
- `C:\lithtech\mm9_editor\mm9_data`: extracted MM9 data used by tests and
  diagnostics.
- `C:\Program Files (x86)\GOG Galaxy\Games\Might and Magic 9\data`: installed
  game data, including `MM9.dep`, `object.lto`, `WORLDS`, and `PreFabs`.
- `C:\lithtech\Lith21tools`: old LithTech 2.1 DEDit and `Processor.exe`; this
  is the MM9-native compile path.
- `C:\lithtech\Lith22tools`: newer DEDit toolchain, useful only for comparison.
- `C:\lithtech\LTWorldConverter` and `C:\lithtech\EDUnpacker`: external
  reference code. Do not make them runtime dependencies.

## Format Facts

MM9 compiled worlds are LithTech/Talon DAT version `66`. They are usually stored
inside `WORLDS.REZ`, though extracted DAT files can also exist under
`data\WORLDS`.

The DAT v66 header shape used by the editor is:

- `uint32 version`
- `uint32 ObjectDataPos`
- `uint32 RenderDataPos`
- eight unused or dummy `uint32` values

Important compiled world models:

- `Terrain*`: visible terrain and world geometry. `Terrain0` is the main
  outdoor terrain model.
- `PhysicsBSP`: collision geometry, indoor/static shell geometry, and some
  helper residues.
- `VisBSP`: visibility/culling geometry and partitioning.
- Helper-only/system models: source evidence for object semantics, not normal
  visible Brush output.

MM9 old DEDit source worlds use ED version `1249`. Shogo/Blood 2 style worlds
use ED version `1247`.

ED v1249 details that matter for writing DEDit-compatible output:

- Brush surfaces store LithTech OPQ texture projection vectors.
- Surface records include trailing `dwFlags` and RGB shade bytes after the
  texture name.
- Full-level ED files can be zlib-compressed in blocks.
- Prefab ED files are often uncompressed.
- DEDit project metadata comes from `MM9.dep` and `object.lto`.

## Core Modules

- `features/dat_editing/legacy_ed.py`: ED reader/layout scanner and fixture
  analysis.
- `features/dat_editing/legacy_ed_writer.py`: ED v1249 writer primitives.
- `features/dat_editing/surrogate_ed.py`: high-level DAT-derived ED generation.
- `features/dat_editing/compiler_strategy.py`: acceptance reports, selection
  reports, source coverage, terrain cutout diagnostics, and compiler-oriented
  safety checks.
- `features/dat_editing/source_world.py`: source ED parsing helpers.
- `features/dat_editing/geometry_scene.py`: shared geometry representation.
- `features/dat_editing/geometry_mesh.py`: retained OBJ scene parsing and
  `GeometryScene` -> BSP mesh conversion helpers used by legacy fixtures/tests.
- `features/dat_editing/geometry_export_common.py`: read-only geometry export
  helpers.
- `features/dat_editing/gltf_export.py`: DAT geometry glTF inspection export.
- `features/dat_editing/terrain_semantics.py`: shared Terrain*/PhysicsBSP/VisBSP
  identity and helper-role classification.
- `features/dat_editing/terrain_reconstruction.py`: Terrain* boundary cleanup,
  triangulation, support selection, cutout coverage, and generated support
  placement helpers.
- `features/dat_editing/terrain_bsp_patch.py`: legacy/reference terrain BSP
  patch diagnostics. Keep it while DAT -> ED terrain behavior is still being
  validated.
- `features/dat_editing/bsp_record_inspector.py`: DAT/BSP comparison utilities.

## Generated Artifacts

`Generate DEDit ED from DAT...` writes:

- `full_world_skeleton_source\<LEVEL>_reconstructed.ed`
- `<LEVEL>_dat_to_ed_report.txt`
- `<LEVEL>_dat_to_ed_selection_report.json`
- `<LEVEL>_dat_to_ed_acceptance_manifest.json`
- optional coverage/leakage/behavior reports depending on the active level and
  available source ED oracle

The acceptance manifest is the durable handoff artifact. It records source DAT
paths, generated ED paths, selected models, generated counts, object-class
coverage, linked diagnostics, cautions, blockers, and manual validation fields.

## Generation Policy

### General Defaults

Normal DAT -> ED generation should prefer stable, game-bound output over broad
geometry recovery.

- Keep generated Brush and polygon counts inside Processor-safe budgets.
- Default budget: `1500` generated Brush objects and `12000` generated polygons.
- Write over-budget EDs only as diagnostics and mark them blocked.
- Exclude helper-only DAT models from normal visible Brush output.
- Preserve helper semantics through objects/properties where possible.
- Do not emit helper-textured Brush shells by default unless leakage diagnostics
  prove they are safe.
- Prefer source ED object records when a same-stem source ED oracle exists.
- Prefer DAT-native object reconstruction when the DAT object record contains
  enough evidence and no source ED oracle is available.

### Terrain0

BOOTCAMP Terrain0 reconstruction is based on generated ED support brushes, not
in-place DAT patching.

Important rules:

- `Terrain0` is the primary outdoor terrain source.
- BOOTCAMP has a reachable grass/rock terrain layer and an unreachable lower
  sand plane. Coverage diagnostics ignore the lower sand plane by default.
- Terrain polygons can be large n-gons.
- DAT terrain point lists can contain repeated or collinear boundary points.
- Canonicalize terrain polygon boundaries before generating ED faces.
- Rectangular terrain holes can be intentional building/support cutouts; use the
  cutout coverage report before treating them as generator loss.

Known validated BOOTCAMP terrain candidate:

```text
C:\lithtech\mm9_editor\output\full_world_skeleton_source\
bootcamp_connected_terrain_patch_twin_clusters_v30_diagnostics.ed
```

Associated diagnostics:

```text
bootcamp_connected_terrain_patch_twin_clusters_v30_diagnostics_terrain_cutout_coverage.json
bootcamp_connected_terrain_patch_twin_clusters_v30_diagnostics_terrain_support_source_coverage.json
```

The accepted source coverage report has `missing_sample_count = 0`.

### PhysicsBSP Static Shell

Some indoor/static levels store important room and wall shell geometry in
`PhysicsBSP`. ANSKRAMKEEP is the main known case.

Current behavior:

- If no Terrain0 support path is active and a `PhysicsBSP` model exists, the
  app can generate budgeted shell slab brushes from selected `PhysicsBSP`
  polygons.
- Each slab is extruded along the source polygon normal and checked for a valid
  closed brush.
- Degenerate, sliver, unstable, or non-enclosing polygons are skipped.
- Selection is role-aware: side walls and floor/ceiling support are prioritized
  over helper/special polygons.
- Selection is spatial/connected: high-value neighborhoods are preferred before
  distant components.
- Acceptance reports can compare generated slab brushes against source
  `PhysicsBSP` polygon indices and classify uncovered polygons by role.

Known limitations:

- This is not original authoring CSG recovery.
- ANSKRAMKEEP still has missing stair/PhysicsBSP faces. Vertical stair
  surfaces can survive while horizontal are missing, which can cause
  fall-through around source spawn/stairwell areas.
- ANSKRAMKEEP Processor logs have shown large numbers of
  `Unable to generate a plane` warnings and problem brushes. Future shell work
  should attribute those warnings to generated Brush names/roles.

Source ED StartPoint anchoring is active when a source ED oracle exists. For
ANSKRAMKEEP, generated worlds use the source `Anskramkeepback` StartPoint and a
matching solid source floor Brush under that point instead of relying only on a
generated PhysicsShell slab that Processor may drop.

### Helper Textures

Helper textures are authoring/semantic evidence and should not become ordinary
visible game geometry.

Known helper textures:

- `TEXTURES\LevelTextures\Misc\rail.dtx`: AI rail/path helper geometry.
- `TEXTURES\LevelTextures\Misc\Invisible.dtx`: collision/invisible helper.
- `TEXTURES\LevelTextures\Misc\Firethrough.dtx`: collision/fire-through helper.
- `TEXTURES\LevelTextures\Misc\greenscreen.dtx`: trigger/zone helper.
- `TEXTURES\LevelTextures\Misc\soundonly.dtx`: sound helper.
- `TEXTURES\SkyBox\SkyMarker.dtx`: sky visibility helper.

Selection reports classify helper-only models as `excluded_helper_texture` by
default. When a semantic object path exists, they can be reported as
`helper_semantic_source`.

Current safe rule:

- Preserve helper objects when possible.
- Keep helper Brush shells disabled in game-bound output.
- Use compiled-DAT helper leakage reports before considering any helper Brush
  shell as a default.

Important SkyMarker finding:

- Shipped BOOTCAMP shows a large `SkyMarker.dtx` shell in DEDit.
- The shipped compiled DAT keeps only hidden `PhysicsBSP` residues and does not
  render the repeated `sky` texture in game.
- Sparse reconstructed SkyMarker Brush output leaks `SkyMarker.dtx` into
  `VisBSP`/visible output.
- Therefore game-bound generation preserves sky objects, but does not emit
  SkyMarker shell/residue Brush records by default.

### Object Reconstruction

The table below describes current object handling.

| Class or system | Source | Normal generation | Notes |
| --- | --- | --- | --- |
| `WorldProperties`, `StartPoint`, `Light` | Generated/source anchored | Enabled | Minimal load scaffolding. Source ED StartPoint anchors are preferred when available. |
| `Door`, `RotatingDoor` | Source ED oracle | Enabled when selected door-like DAT models match source names | Source child Brush records are copied from the ED node hierarchy when bounds match. |
| Door pair expansion | Source ED oracle | Enabled | `DoubleDoorName` pairs are selected together. |
| `AIRail` | Source ED oracle plus DAT helper evidence | Enabled with oracle | Preserves shipped rail links. Placeholder DAT-bounds rails remain unsafe without oracle. |
| Sky objects (`SkyPointer`, `DemoSkyWorldModel`, `TOD_Sky`) | Source ED oracle | Enabled with oracle | SkyMarker Brush shells remain disabled. |
| `AmbientSound` | Source ED oracle plus sound helper evidence | Enabled with oracle | `SoundOnly.dtx` Brush volumes remain disabled. |
| Collision helper objects | Source ED oracle | Enabled with oracle | Preserves `Ladder`, ladder blockers, `InvisibleBrush`, `PerceptionBrush`; helper Brush shells disabled. |
| Trigger helper objects | Source ED oracle | Enabled with oracle | Preserves `PortalZone` records such as `Tavernzone`/`Storezone`; GreenScreen Brush shells disabled. |
| Gameplay triggers | Source ED oracle | Internal/explicit | `Trigger`, `ExitTrigger`, `PortalTrigger`; high impact, inspect references before accepting. |
| Generic `Prop` | Source ED oracle | Internal/explicit | Static prop copy path exists; not all prop subclasses should be swept into generic prop copying. |
| Low-risk behavior props | Source ED oracle | Enabled with oracle | `Barrel`, `BonePile`, `Cauldron`, `Cookpot`, `StatStone`. |
| Medium light/model props | Source ED oracle | Enabled with oracle | `WallTorch`, standalone `Fire`, `CandleProp`, `Brazier`. |
| High-risk loot/damage/destructible props | Source ED oracle | Enabled with oracle | `TreasureChest`, `PropDamager`, `DestructableProp`; manually validated in focused levels. |
| `DestructableBrush` | DAT object records and same-name BSP models | Enabled for no-Terrain0 DATs with same-name pairs | DAT-native path validated on DRAGONSTADIUM. Uses validation floor and suppresses PhysicsBSP shell patch. |

For no-Terrain0 source-oracle worlds that include `DestructableProp`, normal
generation keeps the automatic `PhysicsBSP` shell patch off and emits a
synthetic validation floor. This matches the safer BATHHOUSE validation path.

## Validation Policy

Every candidate that matters must pass the old MM9 toolchain and the game.

Manual validation steps:

1. Load the ED in old LithTech 2.1 DEDit through the MM9 project.
2. Save a copy from DEDit when testing editor survivability.
3. Compile with LithTech 2.1 `Processor.exe`.
4. Fresh-load the level in MM9.
5. Check visible geometry from normal camera angles.
6. Check party collision on terrain, floors, stairs, doors, and generated
   static objects.
7. Exercise reconstructed gameplay objects relevant to the stage.
8. Keep the generated ED, DEDit-saved ED, Processor log, output DAT, and JSON
   diagnostics when a test passes or reveals a useful failure.

Processor warnings are not automatically fatal. Treat them as fatal only when
they correlate with missing render geometry, broken collision, DEDit rejection,
or game-load failure.

Compiled-DAT helper leakage reports are required before enabling helper Brush
shells. Helper textures in `VisBSP`, `Terrain*`, or visible object models are
blockers for game-bound output.

## Known Stable Manual Baselines

- BOOTCAMP Terrain0 connected terrain support: opens in DEDit, compiles, loads
  in game, and has complete sampled playable Terrain0 coverage.
- ANSKRAMKEEP no-helper PhysicsBSP shell baseline: compiles and loads with no
  visible helper textures; many walls recovered, but stair/shell gaps remain.
- ANSKRAMKEEP source-anchored StartPoint support: uses source floor Brush under
  the shipped StartPoint instead of relying on generated shell slabs.
- Object-only helper baseline: collision/trigger helper objects can be present
  without visible helper textures when helper Brush shells are disabled.
- AmbientSound baseline: ambient sound objects are present and audio plays in
  game without visible `SoundOnly.dtx` helpers.
- Door source-context probe: copied BOOTCAMP monster-door pair works when local
  source-like terrain support is present.
- DRAGONSTADIUM `DestructableBrush`: DAT-native same-name object/model pairs
  compile and work with validation floor and no PhysicsBSP shell patch.
- BATHHOUSE `DestructableProp`: generated destructible props play their roles
  correctly; normal generation now includes the class with source ED oracle.

## Known Risks And Constraints

- DAT polygons are compiled BSP output, not original authoring CSG brushes.
- Sparse moving-object probes can behave incorrectly without local world/physics
  support context.
- Helper Brush shells often compile into visible/visibility BSP when emitted in
  sparse generated worlds.
- SkyMarker shell behavior depends on missing compiler/source-world context and
  must remain diagnostic-only.
- ANSKRAMKEEP stairwell/shell geometry still needs focused work; missing
  vertical risers/faces can break collision even when horizontal surfaces exist.
- Source ED oracles make object reconstruction much safer, but the long-term
  goal is DAT-native reconstruction where DAT object records contain enough
  information.
- Do not rely on DEDit visibility alone. A helper shell that looks correct in
  DEDit can still be visible or broken in the compiled game DAT.

## Future Plan

### 1. Default-Path Validation

Validate the promoted normal path through the actual UI command rather than
hidden validation profiles.

- Generate BATHHOUSE with `Generate DEDit ED from DAT...`.
- Confirm `DestructableProp`, `Fire`, and `TreasureChest` behavior still matches
  the focused validation candidate.
- Confirm the generated report shows `include_physics_shell_patch=false` and
  `include_validation_floor=true` for no-Terrain0 `DestructableProp` output.
- Keep this as a regression artifact.

### 2. ANSKRAMKEEP Stair And PhysicsBSP Repair

Recover more indoor shell geometry without exceeding Processor limits.

- Attribute `Unable to generate a plane` warnings to generated Brush names,
  source model names, and shell polygon roles.
- Focus on stair polygons around `Anskramkeepback`.
- Compare horizontal step surfaces, vertical risers, ceilings, and walls before
  and after Processor.
- Tighten slab selection/quality filters before increasing shell budgets.

### 3. DAT-Native Object Reconstruction

Reduce reliance on shipped source ED oracles.

- Generalize the `DestructableBrush` DAT-object property conversion path.
- Inventory DAT object records for classes currently copied from source ED.
- For levels with both DAT and source ED, compare DAT-native object properties
  against source-oracle generated output.
- Promote classes only after DEDit, Processor, and game validation.

### 4. Helper Semantics Without Helper Brush Leakage

Continue restoring helper systems as objects/properties first.

- Keep collision, trigger, sky, sound, and AIRail helper Brush shells disabled
  for game-bound output.
- Improve object-only reconstruction for helper systems.
- Use compiled-DAT helper leakage reports as the acceptance gate for any future
  helper Brush-shell work.
- Do not revisit SkyMarker shell emission until enough surrounding source-world
  context can explain the shipped compile reduction.

### 5. Large-World And Outdoor Regression Matrix

Derive per-world selection rules instead of one global heuristic.

- Compare BOOTCAMP, DOOKSCASTLE, ANSKRAMKEEP, BATHHOUSE, DRAGONSTADIUM, and
  ISLEOFASHES reports.
- Use Processor logs to reduce invalid/problem terrain and shell brushes.
- Repeat Terrain0 reconstruction on another shipped outdoor level.
- Track selected model counts, helper exclusions, generated object classes,
  Processor warnings, and manual game-validation results in manifests.

### 6. Workflow And Cleanup

Keep the tool usable as reconstruction broadens.

- Keep the Tools menu limited to the four supported user-facing actions.
- Keep one-off validation paths behind tests/internal hooks.
- Keep `terrain_bsp_patch.py` only as long as its reference diagnostics remain
  useful.
- Strengthen tests around ED v1249 writing, full-world skeleton generation,
  object-class coverage, Terrain0 coverage, helper leakage, glTF export, and
  app menu construction.
