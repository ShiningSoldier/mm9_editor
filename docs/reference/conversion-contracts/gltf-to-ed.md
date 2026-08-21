# glTF/GLB to ED Contract

**Status: Reference for the maintained converter**

The converter implements this deliberately narrow pipeline:

```text
static glTF/GLB → validated solid Brush plan → ED v1249
```

It does not patch an existing DAT or reconstruct gameplay objects from glTF
names/extras.

## Input subset

Supported input is glTF 2.x JSON or GLB 2 with static `TRIANGLES` primitives,
finite float `POSITION`, optional float `TEXCOORD_0`, supported unsigned scalar
indices or non-indexed triangles, multiple materials/mesh nodes, and nested
matrix/TRS transforms. External buffers must resolve inside the source
directory; local data URIs and the first GLB binary chunk are supported.

The selected scene is the declared root scene, or the only scene when exactly
one exists. Ambiguous selection is a blocker.

Selected-mesh sparse/compressed/quantized accessors, non-triangle modes, skins,
morphs, relevant animations, invalid transforms, non-finite data, and malformed
references are blockers. Cameras, lights, normals, tangents, colors, and PBR
fields that do not alter geometry are reported as ignored.

The importer is local-file-only. Absolute or escaping external-buffer URIs and
network resources are rejected. Inputs are never rewritten.

## Coordinates

Node transforms are evaluated before applying:

```text
P_dedit = unit_scale * coordinate_matrix * P_gltf_world
```

`editor_display` reflects X and is the default for geometry aligned with the
editor's DAT-to-glTF inspection export. `raw_dedit` is identity. Unit scale must
be finite and positive. Negative transform determinant reverses winding, and
planes are rebuilt from final positions.

## Geometry policies

### `strict_convex`

The default exact policy welds coincident points with the configured tolerance,
splits edge-connected components, normalizes winding, and requires each
component to be a closed, nondegenerate, consistently wound convex volume. One
accepted component becomes one ED Brush. Open, non-manifold, zero-volume,
inconsistent, or concave components are blockers.

### `triangle_slab`

This implemented approximation is explicit. Each accepted triangle becomes a
closed five-surface prism. Positive thickness and back/side texture decisions
are required. Invalid, duplicate, non-manifold, and concave topology remains a
blocker; slab mode is not a fallback for failed input.

Automatic convex decomposition and arbitrary open-mesh thickening are not
supported.

## Materials and UVs

A glTF material resolves to a DTX path in this order:

1. `material.extras.MM9_texture`;
2. an explicit material-map entry;
3. a material name that is already a DTX path; or
4. an explicitly chosen fallback.

The converter does not create DTX from source images. Usable `TEXCOORD_0` is
converted to LithTech OPQ projection using authoritative or explicitly supplied
texture dimensions. A dimension fallback or world-aligned projection is
allowed only when selected explicitly and is reported as a caution.

## Output modes and limits

`prefab` writes an uncompressed named-group ED intended for insertion into
another world. `full_world` writes a compressed minimal world containing the
Brush group, `WorldProperties`, a `StartPoint`, and a `Light`.

Default practical Processor budgets are 1,500 Brushes and 12,000 surfaces.
Writer limits include at most 65,535 points per Brush and 3–64 vertices per
surface. Exceeding a budget is a blocker, never silent truncation.

## Failure and artifacts

Diagnostics are `blocker`, `caution`, or `note`. Blockers prevent ED creation.
Writes are staged and committed transactionally; existing artifacts are
preserved unless overwrite is explicit.

For `example.ed`, the converter writes the ED plus
`example.gltf_to_ed_report.json` and `.txt`. The JSON report is authoritative
and records source hashes, options, inventory, materials, components, budgets,
output identity, validation states, and diagnostics.

Automatic success requires preflight and ED reader round-trip. It does not imply
DEDit, Processor, compiled-DAT, or in-game acceptance. Those stages are recorded
separately by the validation service.

## Maintained implementation

- `features/dat_editing/gltf_import.py`
- `features/dat_editing/mesh_topology.py`
- `features/dat_editing/gltf_materials.py`
- `features/dat_editing/gltf_brushes.py`
- `features/dat_editing/gltf_ed_assembly.py`
- `features/dat_editing/gltf_to_ed_service.py`
- `features/dat_editing/gltf_to_ed_validation.py`
- `ui/gltf_to_ed_dialog.py`

