# DAT Geometry Editing Ideas

This note collects the current ideas for making MM9 `.DAT` geometry editable,
including a possible Blender round trip. It is intentionally practical rather
than aspirational: the editor can already parse DAT v66 object data, render BSP
geometry, clone physical doors, import static prefab BSP records, and write
patched DAT output. The missing piece is a safe general workflow for changing
or adding level geometry without corrupting the LithTech world data that the
game still expects.

## Current Baseline

MM9 world files are LithTech DAT version 66 files with a 44-byte header. The
important header fields for editing are `ObjectDataPos` and `RenderDataPos`.
The object section starts at `ObjectDataPos` and the render payload starts at
`RenderDataPos`; changing BSP payload size means these offsets must be patched.

The editor already has a narrow BSP writer path:

- Physical door clones copy existing BSP world-model records from the source
  DAT, rename them, transform points/normals/bounds/UV projection vectors, and
  append the copied records before the object section.
- Static prefab imports use the same raw transform machinery to splice a BSP
  record from a converted prefab DAT into another level.
- The writer updates the world-model count, `NextWorldItem` links, object/render
  offsets, and known terminal-tail cases such as Bootcamp's payload between the
  last parsed model and `ObjectDataPos`.
- Pending edits are previewed in the editor by constructing preview BSP data
  before saving.

This is enough to prove that geometry changes are possible, but only in a
controlled "copy an existing BSP record and transform it" form.

## What We Can Safely Edit First

The safest near-term target is additive geometry as independent BSP submodels:

1. Create or edit geometry outside the original BSP tree.
2. Compile or convert it into a self-contained world-model/submodel record.
3. Add a matching `WorldObject` controller when the game needs one for rendering
   or logic.
4. Leave `PhysicsBSP` and `VisBSP` mostly intact unless a specific feature
   deliberately targets them.

This matches the working prefab-import path and avoids immediately solving the
hardest problem: rebuilding the entire visibility BSP, leaf/PVS data, and
runtime render partitioning.

Good first use cases:

- Add decorative static geometry.
- Add blocking collision helpers, such as generated `InvisibleBrush` boxes.
- Add copied or edited doors as separate door submodels.
- Import small rooms, fences, stairs, platforms, or ramps as new submodels.
- Replace a small standalone submodel when the source record can be preserved
  structurally.

Riskier use cases:

- Editing `PhysicsBSP` in place.
- Editing `VisBSP` in place.
- Deleting or heavily reshaping original world geometry.
- Rebuilding the whole level from arbitrary meshes.
- Changing portal/PVS-sensitive structures without understanding the hidden
  visibility payload.

## Blender Round Trip Shape

OBJ export alone is useful for inspection, but it loses too much information
for a reliable DAT import. A Blender round trip should use a sidecar metadata
file alongside OBJ, or use glTF with custom extras if we want richer native
metadata later.

Recommended first format:

```text
level_edit/
  STURMFORDCITY_geometry.obj
  STURMFORDCITY_geometry.mtl
  STURMFORDCITY_geometry.datmeta.json
  textures/
```

The OBJ carries vertices, faces, UVs, material names, and object/group names.
The JSON sidecar carries everything OBJ cannot safely preserve:

- Source DAT path and checksum.
- Coordinate-system transform used for Blender.
- BSP model names, classes, source record IDs, and roles.
- Original polygon IDs and source model IDs for imported/exported triangles.
- Texture names exactly as DAT/REZ paths, not only sanitized material names.
- UV projection basis (`uv_o`, `uv_p`, `uv_q`) when available.
- Per-model bounds, translation, flags, `NextWorldItem`, and parse warnings.
- Whether a model is visible art, physics, visibility, controller geometry,
  `InvisibleBrush`, helper, sky marker, water marker, or trigger-only.
- For doors, same-named `Door`/`RotatingDoor` controller fields such as
  `MoveDir`, `MoveDist`, `RotationPoint`, `RotationAngles`, `DoubleDoorName`,
  `Locked`, sounds, and `StartOpen`.

The import side should treat the metadata as authoritative. Blender object names
and materials are editable UI labels, not enough by themselves to reconstruct a
valid DAT.

## Coordinate And Texture Concerns

The editor viewport already uses a display-space X-axis flip compared with DAT
coordinates. Any Blender export/import must make this transform explicit:

- Document DAT-to-Blender axes in the metadata.
- Store the transform matrix used at export.
- Apply the inverse transform on import.
- Keep an option to export in raw DAT coordinates for debugging.

Texture mapping is a bigger issue than plain OBJ UVs suggest. The existing BSP
writer transforms surface UV projection vectors so moved/rotated clones retain
texture alignment. For Blender-authored geometry, we need one of two strategies:

1. Accept OBJ UVs and synthesize compatible BSP surface texture data from them.
2. Preserve original BSP UV projection data only when modifying an existing
   polygon without changing its topology.

The first strategy is probably required for new geometry. It should start with
simple static submodels and be validated on a small prefab-style test DAT before
we trust it on large shipped levels.

## Import Strategies

### Strategy A: Additive Mesh To New BSP Submodel

This is the recommended first real implementation.

Workflow:

1. Export selected geometry or an empty placement template to Blender.
2. User creates a named mesh object, assigns MM9 texture/material names, and
   exports OBJ plus sidecar.
3. Importer triangulates faces if needed and builds one or more new BSP
   submodel records.
4. Editor creates a matching `WorldObject` controller for each visible submodel.
5. Optional generated `InvisibleBrush` collision helpers are added as separate
   hidden BSP/controller pairs.
6. Save path appends records before the object section, patches offsets and
   world-model links, and writes a validation manifest.

Advantages:

- Builds on existing prefab import and door clone writer.
- Does not require rewriting original `VisBSP`.
- Easy to preview and undo.
- Lower chance of breaking existing doors, portals, and triggers.

Open questions:

- Exact minimum fields required for a brand-new BSP record built from scratch.
- Whether the game accepts a simple flat polygon-list model without all data
  that DEdit would normally emit.
- How to synthesize reliable surface and plane data for arbitrary triangles.

### Strategy B: Edit Existing Submodel In Place

This is a useful second-stage goal for standalone objects such as simple doors,
grates, fences, or helper brushes.

Workflow:

1. Export one BSP model with original polygon/model IDs.
2. User edits vertex positions without changing topology.
3. Importer maps edited vertices back to the original polygon records.
4. Writer patches only point positions, normals, bounds, polygon centers, and
   UV projection if necessary.

Advantages:

- Much safer than arbitrary topology replacement.
- Preserves unknown record fields and payload layout.
- Good for reshaping misplaced collision or small visible elements.

Limitations:

- No new faces or deleted faces in the first version.
- Works only where the source model is structurally simple enough.
- Still needs strong validation against game loading.

### Strategy C: Replace Submodel Topology

This is the natural Blender dream, but it should wait until Strategy A and B are
stable.

Workflow:

1. Export a submodel with metadata.
2. User edits topology freely.
3. Importer builds a replacement BSP model record from the new mesh.
4. Writer replaces the old record, patches lengths, links, bounds, and offsets.

This is feasible only after we can build valid BSP model records from arbitrary
mesh data. It also needs a plan for collision, visibility, helper roles, and
texture projection.

### Strategy D: Full World Rebuild

This means creating an entire level in Blender and converting it into a valid
MM9 DAT. It is the least feasible near-term path.

A full rebuild would need:

- Valid DAT v66 header and object section.
- World properties, start points, lights, triggers, sounds, and gameplay
  objects.
- Render BSP data.
- Physics BSP data.
- Visibility BSP/PVS data or a safe fallback the game accepts.
- Correct texture, light, portal, and helper semantics.

The more practical version of this idea is to create a small converted prefab or
mini-world and import it into an existing world as an additive submodel.

## Validation Rules

Every DAT geometry writer should run validation before saving:

- Header offsets are internally consistent.
- Object section round-trips and all object property type codes are preserved.
- World-model count and `NextWorldItem` chain are valid.
- Known terminal-tail payloads are preserved and shifted correctly.
- BSP model names do not collide unless the operation is an intentional replace.
- Matching visible BSP and `WorldObject` controller names are present when
  required.
- Door controller pairs preserve `DoubleDoorName` consistency.
- `PhysicsBSP` and `VisBSP` are not modified unless the operation explicitly
  allows it.
- Textures resolve case-insensitively through `TEXTURES.REZ` or are reported.
- Helper roles such as sky, trigger, water, AI rails, invisible/fire-through,
  and sound-only are classified instead of accidentally becoming visible art.
- Output DAT can be re-opened by the editor and pass a second parse/preview.

For game validation, start with a copy of a small test level and a tiny imported
mesh. Then verify:

- Level loads from `WORLDS.REZ`.
- New geometry renders.
- Collision behaves as intended.
- Existing doors/triggers/transitions still work.
- Old saves are not used as proof of DAT behavior, because saves contain active
  runtime object records and may not fully reload changed DAT data.

## Suggested Implementation Stages

### Stage 1: Export Metadata For Round Trip

Extend current OBJ export with a `.datmeta.json` sidecar. Include enough source
identity to map Blender meshes back to DAT/BSP records and enough material data
to avoid losing MM9 texture names.

Exit criteria:

- Exported OBJ can be imported into Blender for editing.
- Re-import can identify unchanged objects and source BSP records.
- No DAT writing is required in this stage.

### Stage 2: Re-import As Pending Additive Static BSP

Build a command that imports a Blender OBJ object as a pending static BSP import,
similar to `Import Static Prefab BSP`.

Exit criteria:

- User can place or keep the imported mesh in editor preview.
- Saving writes a new visible BSP/controller pair.
- The output DAT reopens in the editor and loads in the game.

### Stage 3: Generated Collision Helpers

Allow the user to mark imported Blender objects as visible, collision-only, or
visible-plus-generated-collision.

Exit criteria:

- Imported visible geometry can have a matching `InvisibleBrush` collision
  helper.
- Helper BSP preview shows the generated collision separately.
- Save manifest reports visible and collision submodel counts.

### Stage 4: Restricted In-Place Vertex Editing

Support editing vertices of selected simple existing submodels without changing
topology.

Exit criteria:

- Original record structure is preserved.
- Bounds, centers, normals, and texture data are updated.
- Validation blocks topology changes until replacement is supported.

### Stage 5: Arbitrary Submodel Replacement

Build a real mesh-to-BSP-record compiler for replacing a selected submodel's
topology.

Exit criteria:

- Replacement records are created from mesh data, not copied from an existing
  source record.
- Small standalone submodels load and render correctly in-game.
- The editor can roll back the operation from project metadata.

### Stage 6: Output DAT Validation

Validate the final DAT bytes produced by geometry-editing operations before
committing them into the output REZ.

Exit criteria:

- Header offsets, object-section parsing, and BSP parsing are checked after
  the final bytes are assembled.
- Required imported/replaced BSP model names are verified in the output.
- Invalid world-model links, ranges, bounds, polygon indices, and unexpected
  object-count changes fail the save before a patched REZ is written.
- Non-fatal parser quirks are surfaced as validation warnings in the save
  manifest.

## Design Recommendation

Do not start with "Blender to full DAT." Start with "Blender mesh to additive
BSP submodel." This is the shortest path that can actually change geometry in
the shipped game while reusing the editor's working save machinery.

The important architectural choice is to keep DAT/BSP metadata outside Blender
in a sidecar file and keep imports conservative. Blender should be the modeling
surface; `mm9_editor` should remain the authority for DAT structure, object
properties, BSP roles, validation, save manifests, and REZ installation.
