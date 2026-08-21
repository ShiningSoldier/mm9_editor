# Convert glTF/GLB to DEDit ED

**Status: Supported static-geometry conversion; external acceptance required
for game-bound output**

Choose **Conversion → glTF/GLB to DEDit ED…** to convert a local glTF 2.0 mesh
into an ED v1249 prefab or a minimal full world. The editor does not launch
DEdit, Processor, or the game.

## Prepare the source

- Use static triangle geometry with finite positions.
- A strict solid must be closed, consistently wound, nondegenerate, and convex.
- Name materials with MM9 DTX paths, add `extras.MM9_texture`, or supply a
  material-map JSON file.
- The converter does not convert PNG/JPEG/WebP images into DTX.
- Apply an intentional unit scale; generic glTF does not define the desired MM9
  world-unit size.

Start with a tetrahedron or cube. An open plane is expected to fail the strict
policy.

## Convert in the editor

1. Select the `.gltf` or `.glb` and an output `.ed` path.
2. Choose **Prefab** for insertion into another DEDit world, or **Full world**
   for standalone Processor validation.
3. Start with **Strict convex solids**.
4. Choose **Editor display coordinates** for geometry aligned with this
   editor's DAT-to-glTF inspection export. Choose **Raw DEDit coordinates** only
   for already-authored engine coordinates.
5. Supply material and texture-dimension maps when needed. Any fallback is
   explicit and is recorded as a caution.
6. Select **Convert and validate ED**.

`triangle_slab` is implemented as an explicit approximation. It extrudes each
accepted triangle into a closed prism and requires reviewed thickness and
back/side textures. It is not selected automatically and does not turn invalid,
non-manifold, or concave input into accepted geometry.

For `model.ed`, a successful conversion writes:

```text
model.ed
model.gltf_to_ed_report.json
model.gltf_to_ed_report.txt
model.gltf_to_ed_validation.json
model.gltf_to_ed_validation.txt
```

The JSON report is authoritative. Review coordinate policy, scale, material
mappings, topology classification, budgets, cautions, and blockers.

## CLI

```powershell
python -m features.dat_editing.gltf_to_ed_cli `
  "<source.gltf>" "<output.ed>" `
  --coordinate-preset editor_display `
  --fallback-texture-size 128 128
```

Run `python -m features.dat_editing.gltf_to_ed_cli --help` for the complete
policy, material, UV, slab, budget, and overwrite options.

## External validation

The automatic conversion checks ED identity and maintained-reader round-trip.
Before treating a prefab as accepted, open it in LithTech 2.1 DEDit, insert it
into a disposable world, inspect geometry/materials, save a separate copy, and
record the manual result.

A full world additionally requires an explicit Processor run, compiled-DAT
validation, and a fresh in-game rendering/collision test. The validation CLI
records these stages without inferring success from file existence:

```powershell
python -m features.dat_editing.gltf_to_ed_validation_cli `
  "<output.gltf_to_ed_report.json>"
```

See the [conversion contract](../../reference/conversion-contracts/gltf-to-ed.md)
for supported glTF features, topology rules, reports, and validation states.

