# glTF/GLB to DEDit ED Conversion Contract

Last updated: 2026-08-20

Status: Phases 1 through 9 implemented: contract, glTF reader, topology
analysis, component-to-Brush planning, material/UV conversion, generic ED
assembly, conversion service/CLI/reports, resumable validation, and desktop UI.

This document is the authoritative contract for converting glTF 2.0 geometry
into Might and Magic IX DEDit source data. It deliberately defines a smaller
and safer problem than arbitrary scene conversion:

```text
static glTF/GLB mesh -> validated solid Brush plan -> ED v1249
```

The existing DAT -> ED reconstruction flow is separate and remains documented
in `docs/dat_editing.md`. The existing glTF exporter is inspection-only; this
contract does not reinstate the retired editable mesh-sidecar DAT operations.

The maintained Phase 2 reader is
`features/dat_editing/gltf_import.py`. It stops at `GeometryScene`, returns
geometry in baked glTF world space, and reports unsupported input with stable
`GltfImportError` codes.

The maintained Phase 3 analyzer is
`features/dat_editing/mesh_topology.py`. It performs deterministic DEDit-unit
welding, edge-connected component splitting, winding normalization, manifold
and volume checks, and convex half-space classification. It is side-effect
free and does not select a conversion policy or create Brushes.

The maintained Phase 4 planner is
`features/dat_editing/gltf_brushes.py`. It converts accepted components into
writer-validated `LegacyEdBrush` values, enforces Brush/surface budgets, and
retains source-face provenance. Its fail-closed `write_ready_brushes` view is
empty if any plan blocker remains. It still does not write an ED file.

The maintained Phase 5 converter is
`features/dat_editing/gltf_materials.py`. It resolves DTX paths and dimensions,
converts source UVs to DEDit OPQ, creates explicitly selected fallback and
generated-surface projections, applies known helper-texture flags, and records
per-material resolution and UV-method summaries. Phase 4 consumes this layer
and includes its report in `GltfBrushPlan`.

The maintained Phase 6 assembler is
`features/dat_editing/gltf_ed_assembly.py`. It accepts a fail-closed
`GltfBrushPlan` or generic writer-ready Brushes and creates either contracted
ED layout entirely in memory. It sanitizes and de-duplicates names, assigns
stable full-world node IDs, derives and records the minimal scaffold positions,
uses the core legacy ED writer, and immediately reopens the bytes through the
combined ED reader. The round-trip compares Brush, surface, point, material,
OPQ, node, object, name, wrapper, and full-world scaffold summaries. Failed
validation discards the artifact. Phase 6 does not select or write output paths.

The maintained Phase 7 service and CLI are
`features/dat_editing/gltf_to_ed_service.py` and
`features/dat_editing/gltf_to_ed_cli.py`. The service applies the selected
glTF-world to DEDit coordinate policy, composes Phases 2 through 6, aggregates
their structured reports, and commits successful ED/JSON/text artifacts from
staged files. Existing artifacts are preserved unless overwrite was explicitly
enabled. Blocked conversions write reports but never an ED. The service rejects
artifact paths that resolve to the source glTF/GLB, an external buffer, or a
configuration file.

The CLI is available as:

```powershell
python -m features.dat_editing.gltf_to_ed_cli SOURCE.gltf OUTPUT.ed `
  --coordinate-preset editor_display `
  --fallback-texture-size 128 128
```

Material maps and authoritative texture dimensions use UTF-8 JSON objects:

```json
{
  "Stone": "TEXTURES\\WORLD\\Stone.dtx"
}
```

```json
{
  "TEXTURES\\WORLD\\Stone.dtx": [256, 256]
}
```

Pass them with `--material-map` and `--texture-dimensions`. Use
`--output-mode full_world` for the minimal full-world scaffold,
`--geometry-policy triangle_slab` together with the required slab options for
the explicit approximation policy, and `--overwrite` only when all three
existing artifacts may be replaced. Exit status is zero only for
`ready_prefab` or `ready_full_world`; blocked, validation-failed, and
write-failed results return one after printing the text report.

The maintained Phase 8 validation service and CLI are
`features/dat_editing/gltf_to_ed_validation.py` and
`features/dat_editing/gltf_to_ed_validation_cli.py`. An ordinary invocation is
read-only with respect to DEDit, Processor, DAT, and the game: it verifies the
Phase 7 report/ED identity and reopens the exact ED through the maintained
reader. It then writes a schema-v1 validation manifest and updates the Phase 7
JSON/text validation states in the same rollback-capable artifact transaction:

```powershell
python -m features.dat_editing.gltf_to_ed_validation_cli `
  example.gltf_to_ed_report.json
```

The generated companions are
`example.gltf_to_ed_validation.json` and
`example.gltf_to_ed_validation.txt`. Re-running the command resumes external
evidence only when the current ED SHA-256 matches the manifest. `--reset`
discards resumable evidence. `--no-update-conversion-report` writes only the
Phase 8 companions.

Record DEDit open/save evidence explicitly:

```powershell
python -m features.dat_editing.gltf_to_ed_validation_cli `
  example.gltf_to_ed_report.json `
  --dedit-opened pass --dedit-saved pass --dedit-saved-ed example.ed
```

For `full_world` output, existing Processor/DAT evidence can be ingested with
`--processor-log LOG --compiled-dat WORLD.DAT`. The DAT is accepted only when
it is version 66 and passes `output_validation.validate_geometry_dat`.
Processor execution never happens implicitly. It requires the separate
`--run-processor`, `--processor-path`, and `--processor-work-dir` options and
uses the maintained isolated black-box harness. In-game acceptance likewise
requires explicit `--fresh-load`, `--visuals`, and `--collision` results;
attachments can be hashed into the manifest with `--in-game-evidence`.

The maintained Phase 9 desktop workspace is
`ui/gltf_to_ed_dialog.py`. Open it from
**Conversion -> glTF/GLB to DEDit ED...**. It exposes the supported output,
geometry, coordinate, scale, weld, missing-UV, material-map, dimension,
fallback, slab, and overwrite options. **Convert and validate ED** runs Phase 7 followed by
only the lightweight automatic Phase 8 identity/reader checks on a worker
thread. The result panel shows the output/report paths and validation states.

The same workspace can reopen an existing Phase 7 JSON report for automatic
validation. **Record DEDit open/save pass** is a deliberately explicit manual
attestation guarded by a confirmation prompt. It does not launch or inspect
DEdit. The UI never launches Processor or the game; those higher-cost release
checks remain available through the explicit Phase 8 CLI.

## Success Definition

A conversion is successful only when all of the following are true:

- every emitted polyhedron is a valid DEDit Brush under the selected geometry
  policy;
- the ED file round-trips through the maintained ED reader with matching Brush,
  surface, material, and bounds summaries;
- the output stays within the configured Processor budgets;
- all required texture mappings resolve to DTX paths or to an explicitly chosen
  fallback; and
- the report contains no blockers.

An ED that merely has version `1249`, parses, or opens in DEDit is not by itself
a successful conversion. Processor and in-game validation are later acceptance
gates for game-bound output.

## Initial Scope

The first implementation supports static geometry only.

Supported input:

- glTF `2.x` JSON files with the `.gltf` extension;
- binary glTF version 2 files with the `.glb` extension;
- mesh primitives whose effective mode is `TRIANGLES` (`4`);
- `POSITION` accessors containing finite `FLOAT` `VEC3` values;
- indexed primitives using unsigned-byte, unsigned-short, or unsigned-int
  scalar indices, and non-indexed triangle primitives;
- optional `TEXCOORD_0` accessors containing finite `FLOAT` `VEC2` values;
- multiple materials and multiple static mesh nodes;
- node `matrix` or TRS transforms, including nested transforms and repeated
  instances of the same mesh; and
- external buffers, base64 buffer data URIs, and the first GLB binary chunk.

The importer must honor accessor and buffer-view offsets and byte strides. It
must traverse only the selected scene and bake the complete parent-to-child
transform into emitted positions.

Scene selection is deterministic:

1. Use the root `scene` index when present.
2. If it is absent and exactly one scene exists, use that scene.
3. Otherwise report a blocker rather than guessing which scene to convert.

The following are outside the initial input subset and are blockers when they
affect a selected mesh:

- sparse accessors;
- Draco, meshopt, or other compressed geometry;
- quantized positions or texture coordinates;
- triangle strips, triangle fans, lines, and points;
- skins, joints, and vertex weights;
- morph targets;
- animations that target selected mesh-node transforms;
- non-finite or singular selected-mesh transforms;
- non-finite positions or UV values; and
- missing, truncated, or out-of-range buffers, views, accessors, indices,
  nodes, meshes, primitives, materials, or scenes.

Cameras, non-mesh nodes, glTF punctual lights, vertex normals, tangents, vertex
colors, and PBR parameters do not become DEDit objects or properties. Their
presence is reported as ignored information unless it changes selected mesh
geometry, in which case the relevant rule above applies.

## Resource And Path Safety

The importer is local-file-only.

- HTTP, HTTPS, and other network buffer URIs are not supported.
- An external buffer path must resolve inside the directory containing the
  `.gltf` file. Absolute paths and directory escapes are blockers.
- Declared byte lengths and every accessor read must be checked before use.
- Parsing and conversion limits must be explicit configuration values and must
  be recorded in the report. Exceeding a limit is a blocker, not a reason to
  truncate the scene silently.
- Input files are read-only. The converter does not rewrite the glTF, its
  buffers, or its images.

## Coordinate Contract

glTF node transforms are evaluated first. The resulting position is then
converted to DEDit space:

```text
P_dedit = unit_scale * coordinate_matrix * P_gltf_world
```

`unit_scale` must be finite and greater than zero. It defaults to `1.0` but the
chosen value is always recorded because generic glTF files do not reliably
communicate an MM9 world-unit scale.

The initial coordinate presets are:

| Preset | Coordinate matrix | Intended use |
| --- | --- | --- |
| `editor_display` | reflect X: `diag(-1, 1, 1, 1)` | Default for Blender geometry aligned with the editor's normal glTF inspection export. |
| `raw_dedit` | identity | Geometry already authored in raw MM9/DEdit coordinates. |

A caller may later supply an explicit finite 4x4 coordinate matrix, but silent
axis inference is not allowed.

If the determinant of the accumulated transform and coordinate matrix is
negative, triangle winding must be reversed. Surface planes are rebuilt from
the final transformed points; source normals and tangents are not trusted for
Brush construction.

## Geometry Policies

DEDit geometry is a set of solid Brush polyhedra. A triangle surface mesh is not
automatically a valid Brush. Conversion therefore requires an explicit policy.

### `strict_convex`

This is the default and the only game-bound policy in the first implementation.

For each baked mesh instance, triangles from all its primitives are combined
for topology analysis while retaining per-face material provenance. Vertices
at coincident positions may be welded using the existing DEDit-compatible
`0.01` world-unit tolerance. The normalized triangles are then split into
edge-connected components.

Each component must:

- contain at least four non-coplanar points and have non-zero volume;
- be closed, with every undirected edge used exactly twice;
- have consistent outward winding;
- contain no degenerate face after point welding;
- satisfy the convex half-space test, with every component point on or behind
  every outward face plane; and
- stay within the ED writer's point, surface, and per-surface vertex limits.

One accepted component becomes one `LegacyEdBrush`. Adjacent coplanar triangles
may be merged only when the merged boundary, material, and OPQ texture
projection are compatible. Otherwise the original triangles remain separate
Brush surfaces.

Open, non-manifold, zero-volume, inconsistently wound, or concave components
are blockers in this mode. The converter must not replace them silently with a
bounding box or convex hull.

### `triangle_slab`

This is an explicit approximation mode planned alongside the strict path. It
is never selected automatically.

Each accepted source triangle is extruded along its rebuilt normal into a
closed five-surface prism. The caller must provide a positive slab thickness
that also exceeds the configured point-weld tolerance, and must select the
back/side texture policy. The original material and UV projection are used on
the front surface; generated back and side surfaces use the explicitly
selected DTX mapping.

The initial slab planner accepts only `exact_convex` and `slab_candidate`
topology components. Invalid, duplicate, non-manifold, and concave components
remain blockers even when the approximation policy was selected; Phase 4 does
not use slab extrusion to hide a failed topology analysis.

The report marks every resulting component and Brush as approximated. It also
reports the added volume and the source-triangle-to-Brush multiplication. A
slab output may be useful for low-polygon floors or walls, but it must not be
described as an exact solid conversion.

### Deferred geometry policies

Automatic convex decomposition and arbitrary open-mesh thickening are not part
of the initial contract. They require separate policy names, reports, fixtures,
and acceptance work before they can be enabled.

## Material And Texture Contract

ED stores a DTX texture path and LithTech OPQ projection per surface; it does
not embed glTF images or PBR materials.

A glTF material resolves to a DTX path in this order:

1. the string `material.extras.MM9_texture`;
2. an explicit user material-map entry keyed by material name;
3. the material name itself when it is already a `.dtx` path; or
4. an explicitly configured fallback DTX path.

Missing mappings are blockers when no fallback was explicitly selected. The
converter does not turn PNG, JPEG, WebP, or embedded glTF images into DTX files.
Image conversion is a separate future feature.

When all three vertices of a triangle have usable `TEXCOORD_0` values, the
converter calculates OPQ using the maintained DEDit-compatible projection
routine. Actual DTX dimensions are preferred. A configured `128x128` dimension
fallback is allowed but must generate a caution and be recorded per material.
Actual dimensions may come from an authoritative size lookup or directly from
the 164-byte LithTech DTX header returned by a resource-byte lookup; archive
extraction is not required.
Missing or degenerate UVs use an explicitly selected default projection and
also generate a caution. The initial implemented default is the deterministic
`world_aligned` planar projection; it is never enabled implicitly.

Smooth normals, normal maps, metallic/roughness values, alpha modes, and other
PBR state do not have a general ED Brush equivalent and are not converted.
Material summaries identify the glTF PBR fields ignored for each used
material.

## Output Modes

### `prefab`

The converter writes an uncompressed ED v1249 named-group prefab containing
one Brush node per generated Brush. It is intended to be inserted into a DEDit
world. It is not claimed to be a standalone Processor input.

### `full_world`

The converter writes a zlib-blocked ED v1249 world containing:

- a root container and named Brush group;
- all generated Brush polyhedra and Brush node properties;
- `WorldProperties`;
- a `StartPoint`; and
- a `Light`.

The generated object positions and world infostring are deterministic and are
recorded in the report. A full-world result is eligible for Processor
validation, but is not game-ready until the resulting DAT has been validated
and tested in a fresh game load.

Names are sanitized to deterministic, Latin-1-compatible ED names. Collisions
are resolved with stable numeric suffixes, and the source-to-output name map is
included in the report.

## Processor And Writer Limits

The initial default game-bound budgets are inherited from the maintained
DAT -> ED workflow:

- at most `1500` generated Brushes; and
- at most `12000` generated Brush surfaces/polygons.

These are practical Processor safety budgets, not claims about absolute format
maxima. They may be lowered by the caller but may not be exceeded silently.

The ED writer also requires:

- no more than `65535` points in one Brush; and
- between `3` and `64` vertices on one Brush surface.

Point welding, redundant-loop cleanup, and plane rebuilding must occur before
the first Processor run. A DEDit edit/save cycle is never a required
normalization step.

## Diagnostics And Failure Policy

Diagnostics use three severities:

- `blocker`: conversion cannot produce the requested ED;
- `caution`: conversion may proceed, but the result uses a fallback, ignores
  information, or requires focused validation; and
- `note`: informational provenance or inventory data.

The converter is fail-closed:

- blockers prevent ED creation;
- strict-mode topology failures cannot be downgraded to slab conversion;
- an approximation requires the caller to select its policy explicitly;
- unsupported data is never dropped without a blocker or caution defined by
  this contract; and
- a partially written ED is never left at the requested output path.

Existing output is not overwritten unless the caller explicitly enables
replacement. Successful output uses a staged write followed by an atomic
replace. A report is still written for blocked conversions when its report path
is safe and writable.

## Report Artifacts

For an output named `example.ed`, the converter writes:

```text
example.ed
example.gltf_to_ed_report.json
example.gltf_to_ed_report.txt
```

The text report is a human-readable rendering of the JSON report. The JSON
report is authoritative and starts with:

```json
{
  "schema_version": 1,
  "kind": "mm9_gltf_to_ed_conversion",
  "status": "blocked",
  "source": {},
  "options": {},
  "inventory": {},
  "materials": [],
  "components": [],
  "budgets": {},
  "output": {},
  "validation": {},
  "blockers": [],
  "cautions": [],
  "notes": []
}
```

Allowed top-level status values are:

- `blocked`;
- `ready_prefab`;
- `ready_full_world`;
- `write_failed`; and
- `validation_failed`.

### Required source and option fields

`source` contains:

- absolute input path;
- SHA-256 and byte size of the JSON/GLB file;
- format (`gltf` or `glb`);
- glTF asset version, minimum version, and generator when present; and
- external buffer paths, hashes, and byte sizes.

`options` contains:

- output mode;
- geometry policy;
- coordinate preset or matrix;
- unit scale;
- weld tolerance;
- material-map path and hash when supplied;
- fallback and slab texture settings;
- slab thickness when applicable;
- Brush and surface budgets; and
- overwrite policy.

### Required inventory fields

`inventory` contains source counts for scenes, selected nodes, mesh instances,
meshes, primitives, triangles, materials, ignored non-mesh nodes, and ignored
or unsupported glTF features. It also contains generated component, Brush,
surface, and point totals.

Each `materials` item contains:

- source material index and name;
- resolution source (`extras`, `material_map`, `material_name`, or `fallback`);
- resolved DTX path;
- texture dimensions and their source;
- UV/OPQ method counts; and
- material-specific cautions.

Each `components` item contains:

- stable component ID;
- source scene/node/mesh/primitive provenance;
- source and welded point/triangle counts;
- bounds and signed volume;
- boundary, non-manifold, and inconsistent-edge counts;
- topology and convexity status;
- selected conversion policy and exact/approximated classification;
- generated Brush IDs and counts; and
- component blockers, cautions, and notes.

`budgets` contains configured limits, generated totals, remaining headroom, and
an explicit pass/fail result for every limit.

`output` contains the requested and final absolute paths, ED version, wrapper
kind, byte size, SHA-256, Brush/node/object counts, and deterministic name map.
Fields remain present with null or zero values when no ED was written.

`validation` contains separate states for ED writer completion, ED reader
round-trip, DEDit manual validation, Processor validation, compiled DAT v66
validation, and in-game validation. Unperformed external checks use
`not_run`; they are never reported as passes.

## Validation Levels

The converter distinguishes these validation levels:

1. `preflight`: glTF structure, transforms, topology, materials, and budgets.
2. `ed_roundtrip`: generated bytes reopen with matching structural summaries.
3. `dedit`: manual or automated DEDit open/save evidence.
4. `processor`: LithTech 2.1 Processor output and log evidence.
5. `compiled_dat`: resulting DAT is v66 and passes maintained structural checks.
6. `in_game`: fresh-load rendering and collision evidence.

`ready_prefab` requires successful preflight and ED round-trip. `ready_full_world`
requires the same; it does not imply that the later external validation levels
have run.

Phase 8 stage states are `not_run`, `pass`, `failed`, `blocked`, or
`not_applicable`. Its top-level status is `awaiting_external_validation`,
`validated_prefab`, `validated_full_world`, `blocked`, `validation_failed`, or
`write_failed`. A prefab can reach `validated_prefab` after DEDit open/save;
Processor, compiled-DAT, and in-game stages are `not_applicable` to a standalone
prefab. A full world reaches `validated_full_world` only after DEDit,
Processor, compiled-DAT, and in-game stages all pass. Manual result flags are
recorded as user-supplied evidence and are never inferred from file existence.

Supplying a compiled DAT opts into the maintained structural BSP parser and may
take materially longer for a large world. Routine Phase 8 invocation does not
read or validate any DAT and does not launch external programs.

## Manual Conversion And Acceptance Test

Use a small model first. A tetrahedron or cube exported as triangles is a good
strict-mode smoke test. It must be a closed, consistently wound, convex solid;
an ordinary open floor plane is expected to be rejected by `strict_convex`.

### 1. Prepare the glTF and textures

1. Apply or bake object transforms before export when practical. The importer
   also evaluates glTF node transforms, but baked inputs are easier to inspect.
2. Export static mesh geometry as glTF 2.0 `.gltf` or `.glb`, using triangle
   primitives and `TEXCOORD_0` UVs.
3. Make each glTF material name an MM9 DTX path such as
   `TEXTURES\WORLD\Stone.dtx`, add `extras.MM9_texture`, or prepare a material
   map JSON. The converter does not turn PNG/JPEG images into DTX files.
4. Make the referenced DTX files available under the same texture paths in the
   DEDit MM9 project. The installed local toolchain normally uses
   `C:\lithtech\Lith21tools\mm9_new` and its `Textures` directory.

### 2. Convert in the editor

1. Start the editor normally, for example:

   ```powershell
   python mm9_editor.py --game-root "C:\Program Files (x86)\GOG Galaxy\Games\Might and Magic 9"
   ```

2. Choose **Conversion -> glTF/GLB to DEDit ED...**.
3. Select the `.gltf`/`.glb` and an output `.ed` path outside the source/config
   files.
4. Choose **Prefab** when the result will be inserted into another DEDit world,
   or **Full world** for a standalone ED eligible for Processor testing.
5. Start with **Strict convex solids**. Use triangle slabs only when the
   approximation is intentional and back/side DTX paths and thickness have
   been reviewed.
6. Use **Editor display coordinates** for geometry aligned with this editor's
   normal glTF inspection export. Use **Raw DEDit coordinates** only for data
   already authored in the engine's raw coordinate convention.
7. Set an explicit unit scale. `1` preserves glTF numeric units; generic glTF
   files do not state how those units should map to the desired MM9 size.
8. Supply material/dimension maps as needed. The visible `128 x 128` fallback
   permits conversion when authoritative DTX dimensions are unavailable and is
   reported as a caution. Keep **Reject missing/degenerate UVs** unless a
   deliberate world-aligned fallback projection is acceptable.
9. Click **Convert and validate ED**. Expected smoke-test results are
   `ready_prefab` or `ready_full_world`, ED integrity `pass`, reader round-trip
   `pass`, and top-level Phase 8 status `awaiting_external_validation`.
10. Review the result panel and the generated files:

    ```text
    model.ed
    model.gltf_to_ed_report.json
    model.gltf_to_ed_report.txt
    model.gltf_to_ed_validation.json
    model.gltf_to_ed_validation.txt
    ```

    Check Brush/surface counts, coordinate policy, scale, material mappings,
    texture dimensions, topology classification, cautions, and blockers.

### 3. Open and save in DEDit 2.1

1. Keep the generated ED unchanged so its Phase 8 SHA-256 still identifies the
   tested artifact. If DEDit will save over it, first retain the generated copy.
2. Launch `C:\lithtech\Lith21tools\dedit.exe` and select the MM9 project, usually
   `C:\lithtech\Lith21tools\mm9_new\mm9_new.dep`.
3. For **full-world** output, copy the ED into the project's `Worlds` directory
   if the project browser requires it, then open it as a world.
4. For **prefab** output, copy it into the project's `PreFabs` directory if
   required, open a disposable test world, and insert the prefab. A prefab ED
   is not a standalone Processor world.
5. In DEDit, verify that:

   - the file opens without an error or repair prompt;
   - the expected named group and Brush count are present;
   - geometry has the intended orientation, size, and location;
   - all faces carry the expected DTX paths;
   - UV scale/orientation is plausible on several non-coplanar faces; and
   - DEDit can save a copy and reopen that saved copy.

6. Return to the conversion workspace, select the corresponding Phase 7 JSON
   report if necessary, and click **Record DEDit open/save pass** only after all
   checks above actually succeeded. If DEDit overwrote the generated ED, do not
   record a pass against it: regenerate the original artifact with explicit
   overwrite, then test by saving a separate DEDit copy. Phase 8 correctly
   rejects an ED whose hash no longer matches its Phase 7 report.

### 4. Optional full-world Processor and game acceptance

These steps are not routine per-change tests. They can parse or compile a full
world and may take substantially longer.

1. For a full-world ED only, run the explicit isolated Processor harness:

   ```powershell
   python -m features.dat_editing.gltf_to_ed_validation_cli `
     model.gltf_to_ed_report.json `
     --run-processor `
     --processor-path "C:\lithtech\Lith21tools\Processor.exe" `
     --processor-work-dir "C:\lithtech\mm9_editor\processor_validation\model" `
     --processor-project-dir "C:\lithtech\Lith21tools\mm9_new"
   ```

2. Review the Processor return code/log, problem-Brush count, generated DAT,
   and compiled-DAT structural result in the validation manifest. Do not mark a
   compile as accepted merely because a DAT file exists.
3. Install the generated DAT only through a reversible staging/backup workflow.
   Fresh-load it in MM9 rather than reusing an already loaded level.
4. Check visible geometry, texture orientation, scale, spawn position, and
   collision from both sides of every test solid. For slab approximations,
   specifically check side/back visibility and collision.
5. Record successful game evidence after those checks:

   ```powershell
   python -m features.dat_editing.gltf_to_ed_validation_cli `
     model.gltf_to_ed_report.json `
     --fresh-load pass --visuals pass --collision pass `
     --in-game-note "Fresh MM9 load; inspected every test Brush"
   ```

`validated_full_world` is reached only when DEDit, Processor, compiled-DAT,
and in-game stages all pass. Keep the ED, DEDit-saved copy, Processor log, DAT,
and both JSON manifests for any accepted or diagnostically useful run.

## Test Contract

Routine glTF -> ED implementation work uses only targeted, deterministic unit
tests with synthetic or very small checked-in fixtures.

- Do not enable `MM9_RUN_INVESTIGATION_TESTS` or
  `MM9_RUN_SLOW_DAT_TO_ED_TESTS` for routine changes to this feature.
- Do not run full-world terrain, PhysicsBSP, door, compiler-strategy, or other
  multi-minute geometry suites unless a change explicitly affects them and the
  user requests that validation.
- Importer, topology, material, Brush, report, and ED round-trip tests must not
  invoke DEDit or Processor.
- Processor and in-game checks are explicit integration/release validation,
  not per-change tests.
- Test fixtures should stay small enough that the targeted glTF -> ED suite
  completes in seconds.

## Non-Goals

The initial converter does not:

- rebuild or patch an existing DAT;
- restore `PhysicsBSP`, `VisBSP`, PVS, portals, world trees, or original CSG
  authoring intent from a generic render mesh;
- convert arbitrary concave meshes automatically;
- convert images into DTX textures;
- create MM9 gameplay objects from node names or glTF extras;
- convert animation, skinning, morphs, cameras, or lights;
- guarantee collision or playability merely because an ED opens or compiles;
  or
- invoke DEDit or Processor automatically during ordinary conversion.

Any future expansion of these boundaries must update this contract, increment
the report schema when compatibility changes, and add focused fixtures before
the behavior becomes user-facing.
