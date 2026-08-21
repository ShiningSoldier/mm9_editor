# DAT to ED Contract

**Status: Reference for an experimental feature**

The generator reconstructs a DEDit ED v1249 world from compiled DAT v66
evidence. It cannot recover original CSG authoring intent and must prefer a
smaller validated result over broad but unsafe geometry recovery.

## Format boundary

A DAT header contains version, object-data position, render-data position, and
unused/dummy fields. Important compiled models include visible `Terrain*`,
collision/static-shell `PhysicsBSP`, visibility `VisBSP`, and helper/system
models.

Compiled polygons are fragments of an optimized BSP, not original Brushes.
One-polygon-per-Brush output is expensive and frequently unstable. Generated
Brushes must weld compatible points, clean redundant loops, rebuild planes,
remain deterministic, and retain source model/polygon provenance.

## Generation policy

- Default practical ceilings are 1,500 Brushes and 12,000 surfaces.
- Over-budget ED may be written for diagnostics but is not accepted as
  game-bound output.
- Visible Terrain reconstruction and selected PhysicsBSP shell reconstruction
  use closed writer-validated Brushes.
- Selection prioritizes useful connected geometry within measured budgets.
- `VisBSP`, PVS, portals, and original CSG cannot be recreated by copying
  visible polygons.
- Source ED objects may be used as an oracle when available; DAT-native records
  remain the only assumed source in a normal installation.

## Helper semantics

Textures such as rail, invisible, fire-through, green-screen trigger,
sound-only, and sky-marker materials identify authoring semantics. Their Brush
shells are excluded from normal visible output. Prefer reconstructing the
corresponding objects and properties. Diagnostic helper output must be explicit
and must not be presented as safe merely because DEDit displays it.

## Doors and interactive objects

Moving/interactive objects can depend on same-named child Brushes, ownership,
links, pivots, local support geometry, and compiler context. Property equality
or DEDit visibility alone is insufficient. Controller/BSP identity, transforms,
clearance, behavior, and the compiled result require validation together.

## Acceptance evidence

A reconstruction is not accepted until all applicable gates pass:

1. deterministic generation below configured budgets;
2. maintained-reader ED round-trip;
3. DEDit 2.1 open without repair;
4. first Processor run and reviewed log;
5. compiled DAT v66 structural validation; and
6. fresh in-game rendering, collision, navigation, and interaction checks.

The acceptance manifest records source/output identity, selections, exclusions,
budget accounting, object coverage, cautions, blockers, and manual evidence.
Unperformed external checks are never reported as passes.

## Maintained implementation

- `features/dat_editing/legacy_ed.py`
- `features/dat_editing/legacy_ed_writer.py`
- `features/dat_editing/surrogate_ed.py`
- `features/dat_editing/compiler_strategy.py`
- `features/dat_editing/terrain_semantics.py`
- `features/dat_editing/terrain_reconstruction.py`

Level-specific packing benchmarks, failure-attribution experiments, and future
optimization ideas belong in the research archive rather than this contract.

