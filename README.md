# MM9 Mod Editor

A visual three-dimensional placement editor for Might and Magic IX compiled
world files. Open levels directly from the game's `WORLDS.REZ`, place and
adjust objects in the level view, configure NPC dialogue, and save patched
`.REZ` archives without hex editing or manually unpacking game data.

## Requirements

- Python 3.9 or later
- A Might and Magic IX install with the required archives in its `data` folder:
  - `WORLDS.REZ` (Level geometry and placements)
  - `RUDE.REZ` (NPC dialogues)
  - `SCRIPTS.REZ` (Scripts)
  - `TEXTURES.REZ` (BSP textures)
  - `SKINS.REZ` (Object and character skins)
  - `MODELS.REZ` (Object and character models)
  - `DATA.REZ` (Game databases and global configuration)
- PyOpenGL dependencies:

```sh
pip install PyOpenGL PyOpenGL_accelerate pyopengltk
```

Optional archives such as `SOUNDS.REZ` are used when present to load sound effects. The editor materializes only the needed files from these archives into its internal cache.

## Installation

1. Put the `mm9_editor` folder inside the Might and Magic IX install folder,
   next to the game's `data` directory, or launch with `--game-root`.
2. Run:

```sh
python mm9_editor.py
# or
python mm9_editor.py --game-root "C:\Path\To\Might and Magic 9"
```

On first launch the editor builds `catalog/data/catalog.json` from `data/WORLDS.REZ`.
Output files are written under `mm9_editor/output/`, and source archives are
backed up under `mm9_editor/backups/`. If those folders are not writable, the
editor falls back to `%LOCALAPPDATA%\mm9_editor\`.

## Workflow

### Opening A Level

Click **Open from WORLDS.REZ...** or press `Ctrl+O`, then choose a level from
the archive picker. You can load multiple levels in one session and switch
between them with the **Level** dropdown.

### Placing Objects

1. Click **Add Object** in the left panel or press `A`.
2. Choose a shipped class or one of your saved presets.
3. Click **Place in View**.
4. Click the target surface in the three-dimensional level view.

The editor uses the exact BSP hit point, so placement preserves X, Y, and Z.
After placement, the object can be moved, elevated, rotated, or edited in the
Properties panel.

If the chosen class is an NPC, the editor asks whether to inherit the cloned
NPC's dialogue or create a fresh NPCNbr and staged RUDE dialogue entries.

### Selecting And Editing

Click an object handle or rendered object in the level view to select it.
The right-side **Properties** panel shows editable fields, including a
dedicated transform block:

- `X`, `Y`, `Z`
- yaw in degrees
- `Move to floor`

Press Enter or move focus away from a field to commit a property edit. Click
**Delete** to remove the selected object.

Viewport transforms preview immediately. Dragging commits once on mouse
release, while keyboard nudges, height changes, and yaw rotation commit after a
short debounce so repeated input stays responsive.

### View Controls

The viewport has **Orbit** and **Fly** modes.

Orbit mode:

| Action | Effect |
|---|---|
| Left-drag empty space | Orbit camera |
| Alt + left-drag | Pan camera |
| Middle-drag | Pan camera |
| Scroll | Zoom |
| `F` | Fit level bounds |
| Click object | Select |
| Drag object | Move on X/Z while preserving current Y |
| Arrow keys | Nudge selected object on X/Z relative to camera |
| `PageUp` / `PageDown` | Nudge selected object vertically |
| `Q` / `E` | Nudge selected object down / up |
| `[` / `]` | Rotate selected object yaw |
| Hold `Shift` | Larger nudge / rotation steps |
| `P` | Toggle render profiling output in the console |

Fly mode:

| Action | Effect |
|---|---|
| Left-drag | Look |
| `W` / `S` | Move forward / back |
| `A` / `D` | Strafe left / right |
| `Q` / `E` | Move down / up |
| Hold `Shift` | Faster camera movement |

### Performance Notes

The 3D viewport caches static BSP draw batches per loaded level and caches
ABC object render items per materialized object set. While dragging an object,
the editor draws only the dragged ABC mesh plus object handles, then restores
full detail after release.

Press `P`, or launch with `MM9_EDITOR_PROFILE=1`, to print averaged render
timings for frame, BSP, ABC, and sprite passes to stderr.
| `F` | Reset to fit-to-bounds position |

The **Fog** toggle fades distant geometry into the background colour, which
can make large outdoor levels easier to read.

### User Presets

Presets save reusable object configurations. For example, you can save a prop
with a specific `Filename`, `Skin`, `Solid`, `MoveToFloor`, script, and name.

To create a preset from a selected object, click **Save as Preset...** in the
Properties panel. To create or manage presets manually, use the **Presets**
menu. Presets are stored in `mm9_editor/user_presets.json`.

### Blender Geometry Round Trip

The editor can export level BSP geometry to Blender-friendly OBJ or glTF files
and import edited geometry back as pending DAT operations. This workflow is
meant for additive static geometry, collision helpers, vertex edits to existing
submodels, and small standalone submodel replacements. It is not a full
Blender-to-DAT level compiler.

1. Open a level from `WORLDS.REZ`.
2. Choose **Tools -> Export DAT Geometry for Blender...**.
3. Pick an output folder. The editor writes:

```text
<level>_geometry.obj
<level>_geometry.mtl
<level>_geometry.datmeta.json
```

The OBJ is the mesh Blender imports. The `.datmeta.json` sidecar is required
for import; it records the source DAT checksum, coordinate transform, BSP model
names, texture names, and record metadata that OBJ cannot preserve.

**Tools -> Export DAT Geometry as glTF...** writes `.gltf` plus `.bin`, embeds
the same MM9 metadata in glTF `extras`, and also writes a
`.gltf.datmeta.json` fallback sidecar. The Blender geometry import path accepts
exported `.gltf` and `.glb` files as well as OBJ.

When opening the OBJ in Blender, increase viewport clipping if the level seems
empty. MM9 levels use large world coordinates, so Blender's default clipping can
hide otherwise valid geometry. The default export omits skyboxes, `VisBSP`,
and most helper/world-boundary surfaces so the level does not appear as a solid
box. Debug/raw exports can still include helper geometry through the lower-level
export options.

After editing in Blender, export OBJ or glTF and keep the sidecar next to it
when using exported MM9 geometry. Then use one of the editor import commands:

- **Tools -> Import Blender OBJ/glTF Geometry...** adds mesh data as new static
  BSP geometry. The import dialog can generate `InvisibleBrush` collision
  helpers, including box approximation and per-face slab helpers. Mesh objects
  named or tagged as collision-only become hidden collision helpers.
- **Tools -> Import Blender Vertex Edits...** patches existing BSP vertices
  while preserving the original topology. Added or removed faces are rejected.
- **Tools -> Import Blender Submodel Replacement...** replaces the topology of
  exported standalone BSP submodels. `PhysicsBSP`, `VisBSP`, and skyboxes are
  blocked on this path.

For the most reliable import, keep exported object names intact, export mesh
objects rather than curves, and keep material assignments on faces. If the mesh
file and sidecar do not match the currently open level, the importer rejects the
operation with a checksum or missing-object error instead of guessing. Generic
third-party glTF files can be imported as additive triangle geometry, but they
do not carry original DAT polygon identity.

Imported geometry appears in the editor preview and can be saved like other
pending operations. The save preview shows a geometry risk report with visible
model counts, collision/helper counts, UV methods, source formats, and targeted
warnings for glTF or unsupported full-level semantics. On save, the editor
rebuilds or patches the affected BSP records, updates DAT object/render
offsets, validates the final DAT bytes, and writes the patched `WORLDS.REZ`
under `mm9_editor/output/`. Reopen the changed level and test in game before
relying on the result; the mesh-to-BSP compiler is intentionally conservative
for arbitrary topology.

For the deeper technical reference, source-world findings, PreProcessor
findings, and future plan, see `docs/dat_editing.md`. Full DEdit
source-world-to-DAT compilation is intentionally not the default path; the
current supported approach is conservative DAT patching of additive or
replacement standalone BSP submodels.

### Saving

Click **Save...** or press `Ctrl+S`. The save dialog shows pending DAT writes,
geometry validation/risk summaries, and optional RUDE registrations. Committing
writes timestamped output such as:

```text
mm9_editor/output/20260510_144200/data/WORLDS.REZ
mm9_editor/output/20260510_144200/changed_entries/WORLDS/BOOTCAMP.DAT
mm9_editor/output/20260510_144200/manifest.json
```

The editor never modifies the live game archives on save. Use
**File -> Install Output to Game...** when you are ready to back up and replace
the patched archives in the game `data` folder.

### Saving And Resuming A Session

**File -> Save Project...** (`Ctrl+Shift+S`) writes a `.mm9mod` project file
containing pending operations. **File -> Open Project...** (`Ctrl+Shift+O`)
reloads the source REZ entries and reapplies those operations.

## Changes in the game
Start a new game to see the changes - currently they're not displayed in previously saved games.

## Object Rendering

The view renders BSP geometry with DTX textures loaded from `TEXTURES.REZ`.
WorldObjects with supported ABC models render as static meshes using
`MODELS.REZ` and `SKINS.REZ`; unsupported objects remain selectable coloured
handles.

NPC and creature ABCs have partial static-pose support. Geometry can render
correctly for the supported subset, but full character material binding,
attachments, weighted complex meshes, and animation playback are still future
work.

## Known Caveats

- **Mirrored Level Editing:** The editor renders the level in OpenGL's
  right-handed coordinate system, but the LithTech game engine uses a
  left-handed coordinate system. This results in the horizontal layout
  (left-to-right) being mirrored between the editor view and the running game.
  When positioning objects, remember to swap left and right relative to the target entities.
- No undo UI yet. Close without saving to discard pending edits.
- `.mm9mod` project files store operations, not full level bytes. The source
  game archives must remain accessible.

## CLI Tools

The bundled `mm9_patcher/` folder contains standalone tools:

```sh
python mm9_rezmgr.py list "C:\Path\To\Might and Magic 9\data\WORLDS.REZ"
python catalog.py build-from-rez "C:\Path\To\Might and Magic 9\data\WORLDS.REZ"
```

The catalog builder can also attach a generated `object.lto` class dump for
object-schema work and include valid classes that do not appear in shipped
levels:

```sh
python catalog.py build-from-rez "C:\Path\To\Might and Magic 9\data\WORLDS.REZ" ^
  --object-lto "C:\Path\To\Might and Magic 9\data\object.lto"
```

If the native dump helper is unavailable or cannot load `object.lto`, the
builder logs a warning and still produces the current DAT-scanned catalog.
Classes found only in `object.lto` are added with `0` instances and use
inherited `PropDef` defaults for their first-placement template.

Some lower-level patcher utilities still accept ordinary DAT/RUDE file paths
for reverse-engineering work, but the GUI editor workflow is REZ-only.

### Converting Legends of Might and Magic Levels

In the editor, select **LoMM to MM9** from the **Conversion** dropdown menu, choose the
LoMM install folder, pick a LoMM level from its `WORLDS.REZ`, and enter the
new MM9 level name. The editor uses the same transactional backup and
archive-replacement flow as the standalone tool, then opens the converted
level from MM9 `WORLDS.REZ` for inspection. The last successful LoMM install
folder is remembered in `editor_settings.json` and offered automatically the
next time you open the conversion dialog. The selected LoMM install must have
`data/WORLDS.REZ`, `data/RUDE.REZ`, and `data/SCRIPTS.REZ`.

The converter prints a per-stage summary, writes a complete temporary
`WORLDS.REZ`, backs up the original archive under
`<mm9_root>/mm9_editor/backups/lomm_to_mm9_<timestamp>/data/`, installs the
new archive with `os.replace()`, and verifies that the new level can be
read back. The backup folder also gets an `install_manifest.json` with a
`conversion` section recording the LoMM source level and new MM9 entry, so it
can be inspected or restored through the existing backup-restore flow. PyYAML
is a soft dependency; if it is not installed the loader falls back to JSON
parsing for the config file.

#### Editing the YAML config

The default rules live in `conversion/lomm_to_mm9.yaml`. Three top-level sections:

```yaml
remove_unknown_classes: true       # drop classes not in MM9 catalog
extra_remove_classes: []           # additional classes to drop
keep_classes: []                   # exempt classes (e.g. custom-registered)

patch_class:
  StartPoint:
    add_props:
      MovePlayerToFloor: { code: 5, value: 1 }
  WorldProperties:
    add_props:
      CanSaveGame:     { code: 5, value: 1 }
      CanMiniSaveGame: { code: 5, value: 1 }

convert_class:
  TreasureChest:
    template: "WORLDS/1000TERRORS.DAT::TreasureChest4"
    preserve: [Name, Pos, Rotation, Filename, Skin, ...]
  Fire:
    template: "WORLDS/1000TERRORS.DAT::Brazier46"
    new_type: Brazier
    preserve: [Name, Pos, Rotation, Filename, Skin, ...]
```
