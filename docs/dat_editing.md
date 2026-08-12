# MM9 DAT to ED Reconstruction

Last updated: 2026-07-14

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

## Test Tiers

The default suite keeps deterministic parsing, writer, selector, report, and
small-fixture coverage. Expensive compiler-strategy, surrogate-world, terrain
BSP, and door-BSP cases are retained as an opt-in investigation tier because
repeatedly rebuilding shipped levels takes several minutes. These tests cover
functionality that is expected to be investigated and updated in the future.

Run the normal suite with:

```powershell
python -m unittest discover -s tests
```

Run the complete suite, including investigation regressions, with:

```powershell
$env:MM9_RUN_INVESTIGATION_TESTS = "1"
python -m unittest discover -s tests
Remove-Item Env:MM9_RUN_INVESTIGATION_TESTS
```

The investigation tests are skipped, not deleted, in the default run. The old
`MM9_RUN_SLOW_DAT_TO_ED_TESTS=1` flag remains supported for compatibility. Use
the complete tier before accepting a reconstruction change that affects
real-level geometry, door corridors, SkyMarker residue, Processor budgets, or
baseline counts.

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
- DEDit rewrites dirty Brush records by welding coincident points at roughly
  `0.01` world units, removing redundant compiled-BSP boundary paths, and
  rebuilding polygon planes. Generated Brush records now perform the same
  cleanup before their first Processor run; point welding alone reached 12,339
  vertices but retained 1,228 malformed face loops and still produced 732 plane
  warnings.

## Core Modules

- `features/dat_editing/legacy_ed.py`: ED reader/layout scanner and fixture
  analysis.
- `features/dat_editing/legacy_ed_writer.py`: ED v1249 writer primitives.
- `features/dat_editing/surrogate_ed.py`: high-level DAT-derived ED generation.
- `features/dat_editing/compiler_strategy.py`: acceptance reports, selection
  reports, DAT-native object comparison, source coverage, terrain cutout
  diagnostics, and compiler-oriented safety checks.
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
- ANSKRAMKEEP internal retests reserve the connected shell neighborhood around
  `Anskramkeepback` before filling the remaining global shell budget. The inner
  seed radius is 128 units and the connected outer radius is 512 units.
- Acceptance reports can compare generated slab brushes against source
  `PhysicsBSP` polygon indices and classify uncovered polygons by role.
- Generated shell Brush names encode their structural role and source polygon
  index. The shell coverage manifest records Brush name -> source model ->
  polygon index -> role provenance.
- The shell coverage manifest also records one diagnostic entry per source
  polygon: role, area/bounds, selection/emission reason, generated Brush names,
  and (when a processed DAT path is supplied) tolerant compiled-geometry match
  counts. Selection parameters reproduce the focused selector and door-
  clearance/protected-void exclusions, so `not_selected`,
  `selected_not_emitted`, `excluded_door_clearance`,
  `excluded_protected_void`, and `invalid_source_geometry` are distinguishable.
- Coverage reports rank local hotspots around supplied StartPoint/focus anchors,
  door-clearance corridors, and nearby stair-like floor height ranges. Each
  hotspot reports emitted/actionable/protected/invalid counts, source and
  missing area, role/status distributions, and highest-area missing indices.
- Coverage diagnostics also classify the likely loss cause for every source
  polygon. `not_selected` means the shell selector never chose it;
  `selection_not_run` means no shell budget was supplied, so selection was not
  attempted;
  `ed_emission_failure` means it was selected but produced no shell Brush;
  `protected_door_clearance` records an intentional side-wall exclusion;
  `protected_void` records an intentional caller-supplied protected-void
  exclusion;
  `not_requested` marks polygons outside an explicit diagnostic index subset;
  `survived_compilation` confirms a compiled-DAT geometry match;
  `processor_removed_or_geometry_mismatch` identifies emitted geometry with no
  compiled match; `compiled_match_not_checked` is used when no processed DAT
  was supplied; and `compiled_match_unavailable` prevents a parse/model failure
  from being mistaken for Processor removal. The report and manifest aggregate
  these classes for cost-aware packing decisions.
- Internal diagnostics can restrict shell generation to explicit source polygon
  indices for Processor warning bisection. A PhysicsBSP subset-plan manifest
  now partitions valid source polygons by role/index and keeps each batch below
  a generated-face budget before manual Processor runs.
- Subset plans can attach one Processor log per `(role, batch_index)`; each
  source diagnostic then records the subset validation state, warning count, and
  problem-brush count. Coverage manifests retain that evidence beside the
  generated-ED diagnostics.
- Shell preflight and generation now reuse a coplanar-adjacency index instead
  of rebuilding the full consolidation graph for every budget estimate. An
  opt-in packing plan ranks consolidated regions by role-weighted source area
  per normalized slab face and enforces independent source-polygon and
  generated-face ceilings. Cost-aware packing can grow exact convex regions
  beyond the legacy four-polygon bound (up to eight source fragments); larger
  groups require hull area to match source area, so concave gaps and holes are
  not filled. The legacy role-balanced selector remains the default until the
  plan is validated against Processor and door routes.
- Controlled acceptance reports can enable it with
  `physics_shell_packing_mode="cost_aware"`; the report records the selected
  source count, generated Brush/face cost, weighted value, and protected door
  polygons. Normal editor generation continues to use `balanced`.
- Cost-aware reports accept deterministic role-weight overrides and an optional
  playable-importance bias around the existing focus anchors. The selected
  weights and bias are recorded in the acceptance manifest so controlled
  worlds can be tuned without changing the balanced selector.
- Controlled acceptance reports can also accept explicit protected void bounds
  and protected roles. Door-clearance bounds remain the default protected void;
  caller-supplied bounds are combined with them, recorded in the report, and
  never inferred automatically from arbitrary geometry.
- Full-world acceptance preflight now runs the same Terrain* support and
  diagnostic SkyMarker/residue Brush builders used for emission, counts their
  normalized Brush/face/point cost, and subtracts measured
  support/sky/helper/floor overhead before applying Processor ceilings. Door
  child Brushes that replace matching model Brushes are not double-counted.
- SkyMarker and compiled-residue preflight bundles are retained for the current
  acceptance request and reused verbatim during ED emission. This avoids a
  second source-ED parse and, for residue output, a second compiled-geometry
  correlation without introducing a cross-request cache that could become
  stale after an oracle file changes.

Known limitations:

- This is not original authoring CSG recovery.
- ANSKRAMKEEP still has missing stair/PhysicsBSP faces. Vertical stair
  surfaces can survive while horizontal are missing, which can cause
  fall-through around source spawn/stairwell areas.
- ANSKRAMKEEP Processor logs have shown large numbers of
  `Unable to generate a plane` warnings and problem brushes. LithTech 2.1 logs
  emit those warnings anonymously, so exact attribution requires controlled
  role/index subset compiles rather than parsing one full-world log.

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
| `AIRail` | DAT helper geometry plus optional source ED oracle | Enabled with helper evidence | Preserves source rail links when available; otherwise uses DAT helper positions with empty links. |
| Sky objects (`SkyPointer`, `DemoSkyWorldModel`, `TOD_Sky`) | DAT object records with optional source ED oracle | Enabled with sky-helper evidence | Source properties are preferred; DAT-native fallback is now available. SkyMarker Brush shells remain disabled. |
| `AmbientSound` | DAT object records with optional source ED oracle | Enabled with sound-helper evidence | Source properties are preferred; DAT-native fallback is now available. `SoundOnly.dtx` Brush volumes remain disabled. |
| Collision helper objects | DAT object records with optional source ED oracle | Enabled with collision-helper evidence | Preserves `Ladder`, ladder blockers, `InvisibleBrush`, `PerceptionBrush`; source properties are preferred and helper Brush shells remain disabled. |
| Trigger helper objects | DAT `PortalZone` records with optional source ED oracle | Enabled with trigger-helper evidence | Source properties are preferred; DAT-native `PortalZone` fallback is now available. GreenScreen Brush shells remain disabled. |
| Gameplay triggers | Source ED oracle | Internal/explicit | `Trigger`, `ExitTrigger`, `PortalTrigger`; high impact, inspect references before accepting. |
| Generic `Prop` | Source ED oracle | Internal/explicit | Static prop copy path exists; not all prop subclasses should be swept into generic prop copying. |
| Low-risk behavior props | Source ED oracle | Enabled with oracle | `Barrel`, `BonePile`, `Cauldron`, `Cookpot`, `StatStone`. |
| Medium light/model props | Source ED oracle | Enabled with oracle | `WallTorch`, standalone `Fire`, `CandleProp`, `Brazier`. |
| High-risk loot/damage/destructible props | Source ED oracle | Enabled with oracle | `TreasureChest`, `PropDamager`, `DestructableProp`; manually validated in focused levels. |
| `DestructableBrush` | DAT object records and same-name BSP models | Enabled automatically for validated DRAGONSTADIUM normal generation, or by an explicit internal validation profile | Uses a validation floor and suppresses the PhysicsBSP shell patch only in that focused destructible-only path. Other indoor worlds retain their normal model set and static shell. |

The DAT-native object comparison diagnostic now inventories the source-oracle
classes above and compares DAT names, property keys, and normalized property
values against a source ED (and, when supplied, a generated ED). It emits a
text report or `mm9_dat_native_object_comparison` JSON manifest. This is a
promotion gate, not a default object-generation switch: a class still needs
DEDit, Processor, and in-game validation before its source-ED path is replaced.

For no-Terrain0 source-oracle worlds that actually contain `DestructableProp`
records, normal generation keeps the automatic `PhysicsBSP` shell patch off and
emits a synthetic validation floor. Merely finding a source ED oracle is not
enough to activate this policy. This preserves the safer BATHHOUSE path without
disabling ANSKRAMKEEP shell reconstruction.

Normal ANSKRAMKEEP editor generation keeps the complete eligible DAT model set,
uses the focused PhysicsBSP shell selector, and copies matching source
`Door`/`RotatingDoor` object hierarchies and child Brushes. It must not fall back
to the DRAGONSTADIUM destructible-only validation floor path.

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

Collision and trigger helper reconstruction reports now use same-name DAT
object records when no source ED oracle is available. Their Brush-oracle counts
remain zero in that mode, so the report distinguishes semantic object recovery
from helper Brush-shell recovery.

## Known Stable Manual Baselines

- BOOTCAMP Terrain0 connected terrain support: opens in DEDit, compiles, loads
  in game, and has complete sampled playable Terrain0 coverage.
- ISLEOFASHES Terrain0 stage candidate: 66 eligible visible models plus a
  multi-anchor 1,467-polygon Terrain0 support budget stay within the 1,500-brush
  / 12,000-polygon Processor budget. The selector improves sampled source
  coverage over the single-anchor baseline (1,467 vs. 1,449 covered samples),
  but substantial gaps remain, so this is a manual-review candidate rather than
  a stable baseline.
- ANSKRAMKEEP no-helper PhysicsBSP shell baseline: compiles and loads with no
  visible helper textures; many walls recovered, but stair/shell gaps remain.
- ANSKRAMKEEP PhysicsBSP subset plan: 6,442 valid shell candidates are split
  into 16 role/index batches (512 source indices or 4,096 generated faces per
  batch) for controlled Processor warning bisection. The current focused-shell
  log records 574 problem brushes and four plane warnings.
- ANSKRAMKEEP first-save normalization: generated full-world ED Brush point
  tables contain 12,339 unique coordinates and all but ten face loops match the
  DEDit-saved version immediately, instead of requiring a harmless edit/save
  before compile.
- ANSKRAMKEEP source-anchored StartPoint support: uses source floor Brush under
  the shipped StartPoint instead of relying on generated shell slabs.
- ANSKRAMKEEP focused shell plus source-door regression: generates the connected
  StartPoint shell neighborhood together with 31 `Door` and 66 `RotatingDoor`
  object records. DEDit, Processor, and in-game interaction validation remain
  required before promoting it to a stable manual baseline.
- Object-only helper baseline: collision/trigger helper objects can be present
  without visible helper textures when helper Brush shells are disabled.
- DAT-native helper fallback baseline: BOOTCAMP object-only generation recovers
  AIRail, sky, AmbientSound, collision-helper, and PortalZone records without a
  source ED or helper-textured Brush faces. DEDit, Processor, and game behavior
  validation remain required.
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

### Constraints To Preserve

These are implementation constraints, not remaining milestones:

- Treat DAT polygons as compiled BSP fragments, not original CSG brushes. A
  one-polygon-per-Brush reconstruction wastes the Processor budget and often
  produces unstable planes.
- Generated ED files must compile correctly on their first Processor run. Brush
  point welding, boundary cleanup, plane rebuilding, and deterministic output
  are required; a DEDit edit/save cycle must never be part of the workflow.
- Keep generated Brush provenance traceable to source model and polygon indices.
  LithTech 2.1 plane warnings are anonymous, so controlled provenance subsets
  remain the only reliable warning-attribution method.
- Respect the practical Processor ceilings of 1,500 Brushes and 12,000 polygons.
  ANSKRAMKEEP currently fits 1,090 focused, door-aware PhysicsBSP source polygons
  under that ceiling.
- Preserve the 96-unit approach corridor around copied doors. Side-wall shell
  geometry may not intersect it; floors, ceilings, and door-frame geometry can.
- Keep helper-textured Brush shells out of game-bound output. Restore AIRail,
  sound, sky, collision, and trigger behavior through objects and properties
  unless compiled-DAT leakage tests prove a Brush representation safe.
- Keep source-ED object paths as the correctness baseline until a DAT-native
  replacement has passed DEDit, Processor, and in-game interaction tests.

### 1. Account For The Remaining Indoor Geometry Loss

Before increasing budgets again, make every missing surface explainable.

- Validate `selected_not_emitted`, `excluded_door_clearance`, and the new loss
  classes with controlled role/index subset compiles. Retain the results beside
  the acceptance manifest and compare them with manual routes and Processor
  output so “Processor removed” is not conflated with a geometry-signature
  mismatch.
- Compare hotspot rankings against manual routes and Processor output to tune
  the radius/height heuristics, especially for stair assemblies that cross
  more than one connected PhysicsBSP component.
- Feed only validated failure classes into the next cost-aware packing pass;
  do not spend budget on `protected_door_clearance` or already surviving
  polygons.

Success condition: an ANSKRAMKEEP report can distinguish geometry omitted by
selection from geometry rejected during ED construction or lost by Processor.

### 2. Replace Fixed-Size Consolidation With Cost-Aware Region Packing

Recover more source geometry per generated Brush and polygon.

- Validate the opt-in `cost_aware` shell packing mode against the current
  selector using controlled subsets before making it the default generation
  policy. Keep `balanced` for normal worlds until Processor and door-route
  results are recorded. Acceptance generation now has an opt-in
  `include_physics_shell_packing_comparison` diagnostic that evaluates both
  policies from the same candidate inventory, consolidation index, source and
  generated-face ceilings, protected bounds, role weights, and playable-region
  inputs. Its manifest records each policy's exact source polygon indices,
  recovered area/value, Brush/face cost, role counts, protected indices, deltas,
  and an advisory validation preference; it never changes the default mode.
  `build_physics_shell_packing_experiment` now turns that diagnostic into a
  reproducible A/B package: it runs the normal acceptance pipeline once per
  mode in isolated `balanced` and `cost_aware` directories, writes both regular
  acceptance manifests, and writes a root comparison manifest with exact ED
  paths plus suggested DAT/log paths and pending Processor/manual result fields.
  All supplied acceptance options are cloned into both runs; only mode-specific
  names and the packing policy differ. Balanced packing metrics are normalized
  from the shared comparison plan so its manifest no longer reports zero cost.
  Cost-aware acceptance now applies its own exact generated-face ceiling
  directly instead of first shrinking its source limit with the balanced-policy
  estimator; paired runs therefore compare the same source limit and remaining
  Processor face budget. A BOOTCAMP eight-polygon probe produced balanced
  `8 source / 6 Brushes / 89 faces` versus cost-aware
  `8 source / 4 Brushes / 33 faces`, with matching protected sets.
  `validate_physics_shell_packing_experiment` completes the paired workflow:
  it accepts or discovers each compiled DAT and Processor log, runs the normal
  compiled-DAT/floor/helper audit for both, compares problem Brushes, warnings,
  and compiled PhysicsBSP polygon counts, and incorporates separate manual
  fresh-load/visual/collision results. Missing DATs produce an explicit
  `awaiting_processor_outputs` state instead of a misleading failure. Completed
  evidence is written to a deterministic validation manifest and embedded back
  into the root experiment manifest; any mode recommendation remains advisory
  until both variants pass the same in-game route checklist.
  Paired validation now also runs the maintained compiled source-attribution
  report for each variant. It records how many emitted source polygons matched
  the compiled PhysicsBSP, how many were lost, and the retained source area,
  with per-policy reports and deltas in the validation manifest. The advisory
  chooser considers hard validation failures, manual outcomes, Processor
  problems/warnings, retained source area/count, and only then the pre-compile
  packing preference; total compiled polygon count is retained as context but
  is no longer treated as a geometry-quality proxy.
- Cost-aware packing now grows exact convex regions beyond the legacy
  four-polygon bound (up to eight source fragments) without filling concave
  gaps or holes. The remaining geometry work is to investigate safe convex
  partitioning for larger concave regions, without sealing doorways or gaps
  between source fragments.
- Cost-aware role weights and optional playable-importance bias are now
  configurable and recorded per acceptance report. Validate those values
  against controlled Processor subsets and manual routes, then derive
  per-world defaults rather than treating every source polygon as equal cost;
  both Processor ceilings remain hard limits.
- Acceptance preflight now measures normalized Terrain* support and diagnostic
  SkyMarker/residue Brush overhead with the real emission builders. Extend the
  same accounting contract to any future object class that adds rather than
  replaces child Brushes; retain the proportional safety reserve only as a
  fallback for unmeasured Processor overhead.
- Derive explicit protected void bounds only from validated semantic or
  topology evidence (for example, a matched opening/object pair); add
  per-world detectors and controlled subset tests before supplying new bounds.

Success condition: ANSKRAMKEEP recovers materially more than the current 1,090
source polygons, compiles on the first run below both ceilings, and keeps all
currently validated doors unobstructed.

### 3. Reconstruct Walkable Structures As Connected Assemblies

Polygon coverage alone is not enough for stairs and collision-critical paths.

- Detect stair assemblies from adjacent treads, risers, landings, and supporting
  side walls. Reserve or reject the assembly as a unit so selection does not
  leave isolated horizontal steps. A conservative detector now consolidates
  coplanar floor fragments into tread regions, connects only nearby regions
  with plausible 3..32-unit rises, requires at least three distinct elevations,
  and classifies nearby short vertical faces as risers versus taller supporting
  walls. PhysicsBSP coverage schema v7 records each assembly's exact source
  indices, elevations, bounds, face cost, confidence, and ED/compiled retention
  completeness. Detection is opt-in for ordinary coverage and automatically
  enabled by paired packing validation. On ANSKRAMKEEP it runs in about 0.5s
  after the reusable consolidation index and finds 13 candidates, including
  four high-confidence assemblies and two with at least 12 step transitions.
  Atomic reservation is now available as the explicit
  `physics_shell_stair_assembly_indices` generation option. Requested IDs are
  detected deterministically and staged before normal balanced or cost-aware
  fill: all tread/riser/support groups must be writable, outside protected
  bounds, and fit both source and generated-face ceilings before any are
  committed. A failed reservation excludes the entire assembly from fallback
  selection and receives the distinct `stair_assembly_rejected` coverage loss
  class, preventing isolated-step leakage. Acceptance text/JSON records
  requested, selected, and rejected IDs. Real ANSKRAMKEEP assembly 3 is selected
  atomically by both policies with a 128-source budget; at a 32-source budget it
  is rejected and none of its polygons are emitted. The editor exposes the
  detector through **Tools > Generate DEDit ED with Reserved Stairs...**. It
  lists every candidate with confidence, step count, source-polygon/face cost,
  and bounds, but permits only high-confidence IDs. Nothing is preselected.
  Reserved runs use a distinct suffix such as
  `ANSKRAMKEEP_reconstructed_stairs_3.ed`, and the completion dialog reports
  requested, selected, and rejected IDs. This remains opt-in and should
  initially be used only for high-confidence IDs confirmed against the original
  level.
- Prioritize a navigation corridor connecting each StartPoint to nearby doors
  and major connected shell components before spending budget on remote surfaces.
- Add geometric walkability probes for floor continuity, step height, headroom,
  and fall-through gaps. Keep a small manual route checklist for behavior that
  cannot be inferred from ED/DAT geometry.
- Compare original and reconstructed assemblies by bounds and surface roles,
  then use focused subset compiles when Processor removes part of an assembly.

Success condition: the `Anskramkeepback` stair/door route is continuous and
walkable in game, with no synthetic floor needed along the validated route.

### 4. Generalize Indoor Reconstruction Beyond ANSKRAMKEEP

Turn the focused fixes into data-driven policies rather than more level-name
special cases.

- Run the same coverage, packing, first-compile, and walkability reports on
  DOOKSCASTLE, BATHHOUSE, and DRAGONSTADIUM.
- Derive policy inputs from world traits: Terrain0 presence, PhysicsBSP size,
  StartPoint distribution, door/object density, destructible models, and
  Processor headroom.
- Record per-world manual baselines in manifests and fail regressions when a
  previously recovered surface class, object class, or doorway disappears.
- Retain explicit per-world overrides only when the source data demonstrates a
  real semantic difference that cannot yet be inferred.

Success condition: at least two additional indoor levels generate, compile on
the first run, load, and preserve their tested navigation routes without adding
new hard-coded level branches.

### 5. Reduce Source-ED Object Dependence In Risk Order

- Promote object-only classes whose DAT/source property comparison is already
  equivalent, beginning with non-interactive ambient and helper semantics.
- Reconstruct doors from DAT object records plus same-name BSP models while
  preserving pair links, transforms, child-Brush ownership, and clearance
  metadata. Keep the source-ED implementation available as an oracle and
  fallback during comparison.
- Validate low-risk static props before lights, loot, damage, destructibles, and
  gameplay triggers. Reference-bearing or interactive classes require in-game
  behavior tests, not property equality alone.
- Report which generated objects still require a source ED and why, so oracle
  removal is measurable per level and class.

Success condition: a selected indoor regression level retains working doors and
validated helper behavior when generated without its source ED oracle.

### 6. Revisit Outdoor Coverage After Indoor Packing Stabilizes

- Apply the cost-aware region selector to Terrain0 support and measure remaining
  BOOTCAMP and ISLEOFASHES gaps by playable area rather than sample count alone.
- Derive anchor placement from StartPoints and connected playable components;
  keep single- and multi-anchor modes as consequences of world topology.
- Test cutout boundaries around buildings and transitions so improved terrain
  coverage does not seal entrances or fill intentional holes.
- Add another outdoor level only after the same first-compile and in-game route
  checks are automated enough to make cross-world regressions visible.

Success condition: BOOTCAMP and ISLEOFASHES show improved playable-area coverage
without filling intentional cutouts or regressing their validated routes.

### 7. Validation And Workflow

- Emit one deterministic acceptance manifest containing source coverage,
  Processor budget accounting, exclusions, object-oracle dependence, helper
  leakage, and manual validation status.
- SkyMarker/residue analysis and the source DAT bytes/parsed world are now
  reused between preflight and emission within one generation request. Model
  selection, inferred world settings, AIRail/helper discovery, Terrain0 support,
  and PhysicsBSP shell generation no longer re-read or reparse that DAT. Terrain
  support preflight now hands its exact Brush bundle to emission, and a
  PhysicsBSP consolidation index built for polygon-budget preflight is reused by
  shell emission and PhysicsBSP source-coverage accounting. Terrain cutout and
  terrain source-coverage reports also reuse the request's parsed world instead
  of reopening the DAT. Generated-ED geometry scenes and node-layout reports are
  now cached within the request and shared by source-coverage and final
  round-trip validation. Their combined legacy-ED analyzer reads the file once,
  decompresses the wrapper once, and shares one object scan across the geometry,
  object, and layout views. The post-Processor compiled validation audit now
  parses its DAT once and reuses that world for semantic summaries, record
  inspection, floor probing, and helper-leakage comparison; it reports timings
  for each audit stage. Generation reports and manifests now time source
  parse/preflight, cutout coverage, ED emission, generated-ED analysis, terrain
  and PhysicsBSP coverage, round-trip validation, and total duration. On the
  BOOTCAMP 32-polygon PhysicsBSP probe, reusing emission's exact selection
  reasons reduced PhysicsBSP coverage from about 3.08s to 1.25s and total time
  from about 8.30s to 6.30s. Balanced polygon-budget preflight now also hands
  its final consolidated group plan to emission when there are no focus,
  requested-index, protected-bound, door-clearance, or cost-aware inputs that
  could change selection. Emission now reports PhysicsBSP setup, group planning,
  closed-brush construction, serialization, node hierarchy assembly, wrapper
  compression, and disk write separately. That split showed the remaining cost
  was not brush construction: emission was rebuilding the full candidate order
  before consuming the cached final group plan. Reusing preflight's candidate
  inventory and bypassing that redundant selector reduced the same probe's ED
  emission from about 2.42s to 0.16s and total time from about 6.34s to 4.30s.
  PhysicsBSP Brush records are also appended with one joined serialization pass
  instead of repeated immutable-byte concatenation. Next, split the now-dominant
  source preflight into candidate extraction, consolidation-index construction,
  and polygon-budget fitting, then optimize the largest measured component while
  preserving the exact cached plan consumed by emission.
- Keep diagnostic subset generation and experimental class promotion behind
  internal/test hooks. Keep the user-facing Tools menu limited to stable flows.
- Remove obsolete diagnostic modules only after their evidence is represented by
  maintained reports and regression fixtures.

Success condition: one editor action produces the ED and all evidence needed to
decide whether it is safe to process and test, with no manual index collation.
