# MM9 DAT Geometry Editing

This document is the current technical reference for editing Might and Magic IX
compiled world geometry in `mm9_editor`.

The editor supports conservative DAT patching. It does not try to rebuild a
complete level from arbitrary mesh data. Blender, OBJ, glTF, LTA, and legacy ED
data are treated as modeling or inspection inputs; `mm9_editor` remains the
authority for DAT structure, object records, BSP roles, validation, manifests,
and REZ output.

## Current Capabilities

- Export DAT BSP geometry for Blender as OBJ/MTL/sidecar or glTF/bin/sidecar.
- Import OBJ, `.gltf`, or `.glb` as additive standalone BSP submodels.
- Generate `InvisibleBrush` collision helpers from imported mesh data.
- Treat mesh objects named or tagged as collision-only as hidden helpers.
- Patch topology-preserving vertex edits back into existing exported BSP models.
- Replace topology for standalone non-core BSP submodels.
- Preserve source-world OPQ, surface, and material hints where available.
- Validate generated BSP models before preview and final DAT bytes before save.
- Show a pre-save geometry risk report and write detailed manifest summaries.

The deliberately unsupported path is:

```text
arbitrary full-level Blender/glTF scene -> complete rebuilt MM9 DAT
```

That would require rebuilding visibility data, physics BSPs, render/light data,
world-tree layout, object data, blind object data, and helper semantics. Current
workflows instead patch constrained parts of an existing compiled DAT.

## DAT Structure

MM9 world files are LithTech DAT version `66` files with a 44-byte header:

- `uint32 version`
- `uint32 ObjectDataPos`
- `uint32 RenderDataPos`
- eight unused/dummy `uint32` fields

Important editing offsets:

- `ObjectDataPos`: start of the WorldObject section.
- `RenderDataPos`: start of the render payload.
- `WorldModelTableStart`: start of the BSP world-model table in the pre-object
  payload.

Compiled level data can include:

- BSP world-model records
- `PhysicsBSP`
- `VisBSP`
- world tree layout
- object data
- blind object data
- light grid / lightmaps
- render data
- particle blockers
- helper materials and texture-driven gameplay roles

Changing BSP payload size requires preserving unknown byte ranges and patching
offsets. The writer re-parses final output and checks header offsets, object
parsing, BSP parsing, model names, `NextWorldItem` links, record ranges, bounds,
polygon indices, and required new/replaced BSP names.

## Supported Workflows

### Export For Blender

The editor can export DAT BSP geometry as:

- OBJ: `.obj`, `.mtl`, `.datmeta.json`
- glTF: `.gltf`, external `.bin`, `.gltf.datmeta.json`

OBJ uses the sidecar as the metadata authority. glTF embeds MM9 metadata in
`extras` and also writes a sidecar for tools that strip custom metadata.

Default export omits skyboxes, `VisBSP`, and most helper/world-boundary surfaces
so the level is inspectable in Blender. Raw/debug export paths can include those
models when needed.

### Additive Mesh Import

OBJ, `.gltf`, and `.glb` can be imported as new standalone BSP submodels. The
importer:

- loads the file into `GeometryScene`
- maps materials to DAT texture paths
- converts UVs into LithTech OPQ projection vectors
- builds minimal standalone BSP models
- optionally creates collision helpers
- creates matching `WorldObject` or hidden `InvisibleBrush` controllers
- validates the generated models before preview
- validates final DAT bytes during save

Collision modes include no generated collision, diagnostic duplicate BSP, thin
box approximation, and per-face slab helpers. Explicit collision-only source
objects can be identified by names such as `Collision*`, `*_Collision`,
`*_Collider`, and `UCX*`, or by glTF/node metadata role values containing
collision semantics.

### Vertex Edits

Topology-preserving vertex edits can patch existing exported BSP submodels.
Added faces, removed faces, changed polygon vertex lists, and mismatched source
metadata are rejected.

Use this for small shape corrections where the original BSP record layout
should remain intact.

### Standalone Submodel Replacement

Standalone submodel replacement rebuilds selected non-core BSP model records
with the minimal mesh-to-BSP compiler. It blocks `PhysicsBSP`, `VisBSP`,
skyboxes, and other system/core geometry.

Use this for isolated submodels where replacing the full record is acceptable.

### Source-World Inspection

The editor has read-only parsers for:

- uncompressed DEdit `.lta`
- legacy raw `.ed` prefab brush streams
- zlib-blocked full-level legacy `.ED` wrappers shipped with MM9 by mistake

These are diagnostic inputs and regression fixtures, not a full DAT compiler
backend. They feed `GeometryScene`, preserve authoring metadata, and can export
inspection-only glTF.

## Shared Geometry Model

`GeometryScene` is the format-neutral bridge:

- `GeometryScene`: source path, materials, models, metadata
- `GeometryModel`: name, points, faces, extras
- `GeometryFace`: vertex indices, material name, optional UVs, extras
- `GeometryMaterial`: material name, DAT texture name, extras

The preferred import route is:

```text
source file -> GeometryScene -> mesh_import -> bsp_compile -> DAT patch writer
```

Keeping OBJ, glTF, LTA, and ED on the same model prevents format-specific drift.

## Texture Projection

LithTech BSP surfaces do not store ordinary per-corner UVs. They store OPQ
projection vectors:

- `uv_o`: projection origin
- `uv_p`: U projection vector
- `uv_q`: V projection vector

The importer prefers a Python port of DEdit's `ConvertUVToOPQ` math. If that is
not possible, it falls back to least-squares fitting and then to a safe default
projection. Source-world faces can carry original OPQ values; when present, the
importer preserves them and tags the method as `source_opq`.

Manifests and save-preview reports summarize UV provenance with
`uv_method_counts`, including `source_opq`, `dedit_opq`, `least_squares`,
`default`, `collision_box`, `collision_helper`, and `unknown`.

## Coordinates

DAT coordinates and Blender-facing export space differ by an X-axis flip.
Export metadata records the exact transform matrix. Import applies the inverse
transform before generating DAT geometry.

MM9 levels use large world coordinates. Blender may require a larger viewport
clipping distance to show exported geometry.

## Validation And Manifests

Before preview:

- mesh imports validate generated standalone BSP models
- degenerate polygons and invalid UV/OPQ data fail early
- duplicate or unsafe BSP names are rejected

Before save:

- the save preview shows a geometry risk report
- collision helpers are checked for hidden `InvisibleBrush` controllers
- risky paths warn that `PhysicsBSP`, `VisBSP`, portals, and lighting are not
  rebuilt
- generic glTF without MM9 metadata is called out as additive triangle geometry,
  not a full-level DAT round trip

After writing bytes:

- the DAT header, object section, BSP section, record order, model links, model
  names, bounds, and polygon indices are validated
- warnings and geometry summaries are written to `manifest.json`
- changed DAT entries are written under `changed_entries/` for inspection

Game validation should use a fresh load from the patched `WORLDS.REZ`. Old save
files can contain active runtime object state and may not fully reload changed
DAT data.

## Source-World And PreProcessor Findings

The LithTech source tree contains:

```text
C:\lithtech\lithtech\tools\DEdit
C:\lithtech\lithtech\tools\shared\world
C:\lithtech\lithtech\tools\PreProcessor
```

Current/newer DEdit source-world formats include `.lta`, `.ltc`, and `.tbw`.
Legacy MM9 assets include raw `.ed` prefabs under `C:\lithtech\PreFabs` and
full-level `.ED` wrappers under `mm9_data\WORLDS`. Legacy `.ed` files begin
with version `1249`; full-level wrappers add an info string, block tables, and
contiguous zlib chunks.

Source-world brush polygons can preserve plane data, OPQ vectors, physics
material, surface key, and surface flags. These are useful authoring hints and
regression fixtures.

The available `tools\PreProcessor` is not an MM9-compatible compiler path:

- no PreProcessor build target, project file, or built binary is present
- the original flow was `DEdit -> winpacker -> packer DLL`
- `PreProcPackerImpl.cpp` is a plugin entry point, not a standalone compiler
- the current `Packer_PC\PCWorldPacker.cpp` hardcodes DAT version `85`
- v85 output includes object, blind-object, light-grid, physics, particle, and
  render offsets plus a world offset, unlike MM9 v66's 44-byte header
- its objects-only save path rejects files whose version is not v85

`C:\lithtech\lithtech\handoff.md` independently supports the same conclusion:
MM9 appears to be a custom MMIX LithTech Talon / LithTech 2.x-era branch, not a
native Jupiter game. Direct MM9 v66 loading into the Jupiter v85 tree is not
viable.

## Implementation Map

- `features/dat_editing/export_roundtrip.py`: OBJ/MTL/sidecar export
- `features/dat_editing/gltf_export.py`: glTF and inspection glTF export
- `features/dat_editing/gltf_import.py`: glTF/GLB import into `GeometryScene`
- `features/dat_editing/mesh_import.py`: OBJ/glTF import planning and
  `GeometryScene` to BSP mesh conversion
- `features/dat_editing/bsp_compile.py`: minimal standalone BSP model compiler
- `features/dat_editing/vertex_edit.py`: topology-preserving vertex edits
- `features/dat_editing/replace_submodel.py`: standalone submodel replacement
- `features/dat_editing/output_validation.py`: post-write DAT validation
- `features/dat_editing/source_world.py`: read-only LTA parser
- `features/dat_editing/legacy_ed.py`: read-only legacy ED scanner
- `features/dat_editing/uv_projection.py`: DEdit-style UV-to-OPQ math
- `features/dat_editing/geometry_scene.py`: format-neutral scene model

Important coverage includes OBJ/glTF export/import, source checksum checks,
UV-to-OPQ projection, mesh import collision helpers, vertex edits, submodel
replacement, DAT output validation, LTA fixtures, legacy ED fixtures, and
source-prefab golden tests.

## Future Plan

### 1. In-Game Validation Harness

Automate a small set of fresh-load checks against patched output archives:

- open patched `WORLDS.REZ` in a controlled game or compatibility runtime
- verify required BSP model names are present
- verify new visible geometry and collision helpers are reachable/inspectable
- capture load failures and geometry warnings in a save manifest companion log

### 2. Improve Physics And Collision Fidelity

Keep additive helper collision as the supported path, but make it more useful:

- improve face-slab generation for stairs, ramps, and thin vertical surfaces
- classify helper material roles more completely
- add targeted diagnostics for geometry that probably needs collision but lacks
  helper models
- continue avoiding implicit `PhysicsBSP` rebuilds until the format is better
  understood

### 3. Broaden Source-World Metadata Use

Use LTA/ED data as authoring hints without making it a compiler backend:

- preserve more texture flags and helper semantics
- compare source-world OPQ and compiled DAT OPQ across more fixtures
- export richer inspection glTF for source prefabs and legacy ED wrappers
- use source metadata to improve import warnings and material classification

### 4. Mature glTF As The Preferred Interchange

OBJ remains stable, but glTF should become the richer round-trip format:

- support more safe glTF transform combinations
- improve diagnostics for generic third-party glTF files
- preserve more MM9 metadata in `extras`
- keep sidecar fallback for Blender or tools that strip metadata

### 5. Optional MMIX/Talon Compiler Research

Continue this outside the editor save path:

- search specifically for an MMIX/Talon-era v66 world packer
- if found, compile a tiny LTA fixture and compare output against shipped MM9
  DAT structure
- if not found, derive a v66 writer only from parser knowledge and golden DAT
  comparisons
- integrate only as an optional diagnostic backend after golden tests prove
  compatibility
