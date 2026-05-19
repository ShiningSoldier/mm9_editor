# MM9 Mod Editor

A visual three-dimensional placement editor for Might and Magic IX compiled
world files. Open levels directly from the game's `WORLDS.REZ`, place and
adjust objects in the level view, configure NPC dialogue, and save patched
`.REZ` archives without hex editing or manually unpacking game data.

## Requirements

- Python 3.9 or later
- A Might and Magic IX install with `data/WORLDS.REZ`, `data/RUDE.REZ`, and
  `data/SCRIPTS.REZ`
- PyOpenGL dependencies:

```sh
pip install PyOpenGL PyOpenGL_accelerate pyopengltk
```

Optional archives such as `TEXTURES.REZ`, `SKINS.REZ`, `MODELS.REZ`, and
`DATA.REZ` improve textured rendering and actor/material previews. When they
are present, the editor materializes only the needed files into its internal
cache.

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

### Saving

Click **Save...** or press `Ctrl+S`. The save dialog shows pending DAT writes
and optional RUDE registrations. Committing writes timestamped output such as:

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

The view renders BSP geometry with DTX textures when `TEXTURES.REZ` is available.
WorldObjects with supported ABC models render as static meshes using
`MODELS.REZ` and `SKINS.REZ`; unsupported objects remain selectable coloured
handles.

NPC and creature ABCs have partial static-pose support. Geometry can render
correctly for the supported subset, but full character material binding,
attachments, weighted complex meshes, and animation playback are still future
work.

## Known Caveats

- No undo UI yet. Close without saving to discard pending edits.
- `.mm9mod` project files store operations, not full level bytes. The source
  game archives must remain accessible.
- Older `.mm9mod` files that referenced loose DAT files are no longer
  supported.
- Some ABC models still fall back to handles because their weighted mesh,
  material, attachment, or animation layouts are not fully decoded yet.

## CLI Tools

The bundled `mm9_patcher/` folder contains standalone tools:

```sh
python mm9_rezmgr.py list "C:\Path\To\Might and Magic 9\data\WORLDS.REZ"
python catalog.py build-from-rez "C:\Path\To\Might and Magic 9\data\WORLDS.REZ"
```

Some lower-level patcher utilities still accept ordinary DAT/RUDE file paths
for reverse-engineering work, but the GUI editor workflow is REZ-only.

### Converting Legends of Might and Magic Levels

`lomm_to_mm9.py` is a converter that takes a Legends of Might and Magic
level from LoMM `WORLDS.REZ`, converts it to MM9-compatible DAT bytes,
and transactionally adds it to MM9 `WORLDS.REZ` after backing up the
original archive. Both games run on the same LithTech engine family and
share the v66 DAT container, so the binary header, BSP, lightmap and PVS
regions transfer cleanly. The converter only rewrites the WorldObject
section, applying three kinds of rules:

1. **Drop unknown classes.** Any WorldObject whose class is not present in
   MM9's class registry (`catalog.json`) is removed. This handles
   LoMM-only classes such as `CandleWall`, `Orc`, `GoodKingRescueZone`,
   `BuyZone`, and `Timer`.
2. **Patch shared classes.** Adds missing properties to existing objects.
   By default this adds `MovePlayerToFloor = 1` to every `StartPoint`
   and `CanSaveGame = 1` plus `CanMiniSaveGame = 1` to
   `WorldProperties` so the player can save in the converted level.

In the editor, use **LoMM to MM9 conversion** from the top menu, choose the
LoMM install folder, pick a LoMM level from its `WORLDS.REZ`, and enter the
new MM9 level name. The editor uses the same transactional backup and
archive-replacement flow as the standalone tool, then opens the converted
level from MM9 `WORLDS.REZ` for inspection. The last successful LoMM install
folder is remembered in `editor_settings.json` and offered automatically the
next time you open the conversion dialog. The selected LoMM install must have
`data/WORLDS.REZ`, `data/RUDE.REZ`, and `data/SCRIPTS.REZ`.

#### Usage

```sh
python lomm_to_mm9.py \
    --mm9_root "C:\Path\To\Might and Magic 9" \
    --lomm_root "C:\Path\To\Legends of Might and Magic" \
    --level_to_convert CHATEAUESCAPE \
    --converted_level_name CHATEAUESCAPE_MM9

# Preview only; MM9 WORLDS.REZ is not modified
python lomm_to_mm9.py \
    --mm9_root "C:\Path\To\Might and Magic 9" \
    --lomm_root "C:\Path\To\Legends of Might and Magic" \
    --level_to_convert CHATEAUESCAPE \
    --converted_level_name CHATEAUESCAPE_MM9 \
    --dry-run

# Different rule set, or force a fresh class scan instead of catalog.json
python lomm_to_mm9.py ... --config my_rules.yaml
python lomm_to_mm9.py ... --catalog ""
```

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

Property `code` values match the LithTech v66 DAT type codes:

| code | type            |
|------|-----------------|
| 0    | LT string       |
| 1, 2 | vec3 (3 floats) |
| 3    | float32         |
| 5    | bool (1 byte)   |
| 6    | uint32          |
| 7    | quaternion      |

#### Experimental: porting LoMM enemies to MM9

Each rule there clones an MM9 host class instance (which
brings MM9-compatible stats, AI, sound table, and animation state
machine). The default rule set covers Orc, Goblin, LizardMan, LizardWarrior,
Dwarf, Soldier, Mummy, Wight, EvilEye, and EvilEyeTerror. Add your own
rules in the YAML; each rule may use:

- `template` and `new_type` - which MM9 host class to clone.
- `preserve` - source fields to copy onto the clone.
- `overrides` - update existing template fields with absolute values
  (the prop code is auto-detected from the template; if the field
  isn't on the template it's added as a string).
- `add_props` - add new fields not on the template, with explicit
  `code` and `value`.

#### Asset audit

After conversion, the script walks every remaining object's
`Filename` (`.abc`/`.lta`/`.ltb`) and `Skin` (`.dtx`) and reports a
three-way classification:

- **in MM9** - resolves inside the MM9 install's `MODELS.REZ` /
  `SKINS.REZ`. Nothing to do.
- **in LoMM only** - found inside the LoMM install's `MODELS.REZ` /
  `SKINS.REZ` but not in the MM9 archives. These are the files you need
  to add to MM9's REZ archives before the level renders correctly.
- **missing** - not found in MM9 or LoMM assets. Either provide the file
  or substitute a different model/skin in the YAML.

The audit prints the punch list under the conversion summary every
run; pass `--dry-run` to preview without writing the output DAT.

The editor's static-pose ABC preview assumes single-weight skinning,
so heavily multi-weighted LoMM characters such as `Goblin.abc` render
as imploded shards in the viewport even though the game executable
displays them correctly at runtime. See `HANDOFF.md` for details.
