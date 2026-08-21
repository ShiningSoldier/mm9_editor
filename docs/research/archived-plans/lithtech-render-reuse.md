# Reusing LithTech MM9 Runtime Work In The Editor

> **Archived proposal — non-normative.** None of the proposed cache stages in
> this document should be read as an implemented editor feature. It is retained
> as design context for future performance work.

This note compares the current Python editor viewport with a separate
`<lithtech-runtime-repo>` MM9 compatibility runtime and identifies which pieces
are realistic to bring back into `mm9_editor`.

## Short Answer

Yes, parts of the LithTech work can help the editor, but the most useful pieces
are the data products and batching strategy, not the Jupiter/D3D renderer
itself.

The editor is already doing the right kind of OpenGL caching for live editing:
it uploads BSP meshes once per level, resolves texture ranges once, caches ABC
model uploads, and only recomputes object transforms per frame. The LithTech
runtime is faster mostly because it goes one step further: it consumes
pre-flattened binary sidecars and draws large contiguous vertex/index buffers
through an engine render path.

The best candidates to port are:

1. A fast static BSP sidecar/cache inspired by `MM9SMESH`.
2. A baked static-prop batch inspired by `MM9PMESH`.
3. Shared actor/static-prop visual resolution rules from the exporter.
4. Optional preconverted texture cache for faster level-load time.
5. Collision/interaction sidecars for editor overlays and picking, not raw
   render speed.

## Current Editor Rendering Path

The editor viewport lives in `view3d/`:

- `gl_mesh.py` triangulates BSP world models, groups triangles by texture, and
  uploads each model as a VAO/VBO/IBO. `build_bsp_draw_batch()` resolves the
  visible model list and texture ranges once when a level or helper mode
  changes. Per-frame rendering calls `draw_bsp_batch()`.
- `gl_object_models.py` loads ABC models, caches GPU meshes, and
  `build_render_items()` precomputes the object-to-mesh/material mapping for
  the materialized object list. Per-frame rendering still loops object
  instances and recomputes each transform matrix.
- `gl_objects.py` renders object/editor handles as a point-sprite batch.
- `dtx.py` decodes DTX textures on the CPU and uploads them lazily.

This is a good live-editor architecture. The remaining cost is mostly Python
overhead, per-model/per-object draw-call count, texture decode/load time, and
maintaining rich editor picking/visibility behavior.

## LithTech Runtime Pieces

The LithTech compatibility path generates and consumes static-world packages:

- Manifest JSON: `*_static_world_package.json`
- Static BSP mesh: `*.meshbin` with magic `MM9SMESH`
- Static prop/actor mesh: `*.propmeshbin` with magic `MM9PMESH`
- Collision mesh: `*.collisionmeshbin` with magic `MM9COLL`

The sidecar vertex format is intentionally simple:

```text
header:  8-byte magic, u32 version, u32 vertex_count, u32 index_count
vertex:  float x, y, z, u, v       # 20 bytes
index:   u32
```

The manifest stores draw ranges by render block/section or prop texture. The
runtime then prepares one large static mesh and one large prop mesh instead of
walking source objects every frame.

## Feasibility Matrix

| Candidate | Feasible? | Expected Benefit | Risk / Caveat |
|---|---:|---|---|
| Load `MM9SMESH`-style static BSP cache in editor | High, if generated from editor BSP data | Faster level load, fewer Python triangulation passes, possible fewer VAOs | Existing LithTech packages are derived from the Jupiter/static package path; editor needs its own cache invalidation and helper-mode variants |
| Batch static props into one `MM9PMESH`-style OpenGL buffer | High | Biggest likely frame-time win in prop-heavy scenes; replaces many object ABC draw iterations | Editing selected/moving objects must be excluded or overdrawn separately so live transforms remain correct |
| Reuse actor/static-prop resolver from `mm9_export_static_props.py` | Medium-high | More consistent object visuals between runtime and editor | Needs API cleanup; exporter currently lives in the other repo and uses manifest-shaped object dicts |
| Use converted texture files instead of decoding DTX at viewport load | Medium | Faster texture load and less CPU work | More disk cache management; DTX path is already lazy and stable |
| Port Jupiter/D3D renderer directly | Low | Potentially high raw render speed | Wrong boundary for Tk/PyOpenGL editor; C++ engine window is not an embeddable editor viewport |
| Use collision/interaction sidecars in editor | Medium | Better overlays, floor picking, door/trigger diagnostics | Helps editor function more than render FPS |

## Recommended Integration Plan

### Stage 1: Shared Baked Prop Batch

Add an editor-side baked prop cache using the `MM9PMESH` idea.

The cache should be generated from the current materialized object list using
the same visual rules as the normal ABC preview. Store:

- Source level identity and object-list fingerprint.
- Flat vertices with world-space positions and UVs.
- Texture/material draw ranges.
- Object index per draw range for selection suppression and invalidation.

Draw most static props in one batch grouped by texture. Keep the current
per-object ABC path for:

- selected object,
- actively dragged object,
- pending added/cloned objects,
- objects changed since the baked cache was built,
- unsupported/animated objects.

This preserves editor responsiveness while removing most per-frame Python
object-loop cost in populated levels.

### Stage 2: Static BSP Binary Cache

Add a Python `MM9SMESH`-like cache writer/reader for `view3d.gl_mesh`.

Unlike the LithTech package, generate this from the editor's `WorldModelMesh`
objects so the cache respects editor-specific helper filtering, water
substitution, display-space reflection, and picking needs. Useful cache
variants:

- normal visible art,
- helper translucent overlays by helper role,
- raw/debug view if needed.

The first implementation can still draw multiple ranges, but it should avoid
re-triangulating BSP polygons on every level load. A later version can merge
more ranges into one VAO and reduce draw calls.

### Stage 3: Optional Texture Conversion Cache

The LithTech runtime converts DTX references to runtime-friendly texture files.
The editor can copy that idea behind `TextureCache`:

- Decode a DTX once into an editor cache directory.
- Key by source path, size, mtime/hash, and decode mode.
- Upload from cached RGBA/TGA/PNG bytes on subsequent launches.

This is mainly a level-load improvement; it is less important than prop/BSP
batching for frame time.

### Stage 4: Collision And Interaction Overlays

Import the sidecar concepts from:

- `mm9_export_collision.py`
- `mm9_export_interactions.py`

These should feed editor tooling:

- source-colored collision overlays,
- dynamic-door source diagnostics,
- trigger/transition/water volume inspection,
- more reliable floor placement/picking tests.

They are valuable, but not the first place to optimize rendering.

## What Not To Port Directly

Do not try to embed or copy the Jupiter D3D render loop into the Python editor.
The runtime speed comes from a native engine render stack, static buffers, and
command/resource preparation. The editor needs Tk integration, live object
editing, picking, helper overlays, and undo-friendly materialized state. A
native D3D viewport would be a separate application architecture.

Also avoid depending directly on the current generated LithTech manifests as
the editor's only fast path. They are useful references and fixtures, but the
editor should be able to build its own caches from the opened DAT/REZ data.

## Concrete Next Step

Prototype Stage 1 first.

The proof of concept should add a `view3d` module that bakes static object ABC
meshes into one GPU batch, reusing the existing ABC loader and texture cache.
Enable it behind an environment flag such as:

```text
MM9_EDITOR_BAKED_PROPS=1
```

Then compare viewport profiling with `MM9_EDITOR_PROFILE=1` in a prop-heavy
level such as Bootcamp or Sturmford City. If `abc=` frame time drops
substantially without breaking selection/dragging, make it the default and move
to BSP cache work.
