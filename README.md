# MM9 Mod Editor

A three-dimensional editor for Might and Magic IX compiled worlds. It opens
levels directly from the game's REZ archives, edits object placement and
properties, authors NPC dialogue/quest resources, previews a pending level, and
writes installable replacement archives without hex editing or a required
full-game extraction.

## Requirements

- Python 3.9 or later
- A Might and Magic IX installation containing:
  - `data/WORLDS.REZ`
  - `data/RUDE.REZ`
  - `data/SCRIPTS.REZ`
  - `data/TEXTURES.REZ`
  - `data/SKINS.REZ`
  - `data/MODELS.REZ`
  - `data/DATA.REZ`
- Viewport dependencies:

```powershell
python -m pip install PyOpenGL PyOpenGL_accelerate pyopengltk numpy
```

`SOUNDS.REZ` is optional and is used when available.

## Start

Clone the repository anywhere writable and run from its root:

```powershell
python mm9_editor.py --game-root "<mm9-root>"
```

`<mm9-root>` is the folder containing the game's `data` directory. If the
repository is inside the game folder, `--game-root` can be omitted.

For Legends of Might and Magic conversion support:

```powershell
python mm9_editor.py `
  --game-root "<mm9-root>" `
  --lomm-root "<lomm-root>"
```

The editor reads the installed REZ archives directly. A copied/extracted
`mm9_data` or `lomm_data` folder is not required for normal operation.

On first launch, a missing MM9 catalog is generated at
`catalog/data/catalog.json`. A missing LoMM catalog is generated when a valid
`--lomm-root` is supplied. Existing catalogs are not overwritten automatically.

## Safety model

Ordinary Save never modifies the installed game. It writes a timestamped batch
under `output/`, including complete patched archives, review copies, and a
manifest. Use **File → Install Output to Game…** to perform an explicit,
manifested installation with a backup. Use **File → Restore Installed Backup…**
to reverse one.

If the repository's output or backup directories are not writable, the editor
uses the user's local application-data directory and reports the selected path
at startup.

## Basic workflow

1. Choose **File → Open Level from WORLDS.REZ…** (`Ctrl+O`).
2. Select an object or press `A` to add one from the catalog.
3. Place or transform it in the viewport and edit fields in Properties.
4. Use **Edit → Undo** (`Ctrl+Z`) or **Edit → Redo** (`Ctrl+Y` or
   `Ctrl+Shift+Z`) while changes remain pending.
5. Optionally choose **File → Run Current Level** (`Ctrl+Alt+R`) for an isolated
   preview containing unsaved DAT, dialogue, and script changes.
6. Choose **File → Save…** (`Ctrl+S`), review the plan, and create an output
   batch.
7. Install the completed batch explicitly when it is ready for game testing.

Multiple levels can be open in one session. `.mm9mod` project files persist
pending operations through **File → Save Project…** and **File → Open Project…**.
They do not embed the original game archives, which must remain accessible.

## Viewport

Orbit mode supports surface placement, selection, drag movement, camera-relative
arrow-key nudging, vertical movement with `PageUp`/`PageDown` or `E`/`Q`, yaw
rotation with `[`/`]`, and larger steps while holding `Shift`. `F` fits the
normal visible level geometry.

Fly mode uses `W/A/S/D`, `Q/E`, left-drag look, wheel dolly, and `Shift` for
faster movement.

Object and service/control helpers are hidden by default. Use the **View** menu
to show object helpers, world helpers, or translucent helper BSP roles.

The viewport renders BSP with DTX textures and supported ABC objects as static
meshes. Weighted NPC/creature meshes have conservative static LOD0 previews.
Animation playback and runtime-created attachments are not simulated.

See [Viewport](docs/user-guide/viewport.md).

## Dialogues and quests

When placing an NPC, create fresh dialogue or inherit the cloned object's
dialogue. Open **Dialogues → Dialogue and Quest Editor…** to edit RUDE state
graphs, simulate key-gated choices, index quest-key use, and author Quest Notes
or Awards without opening a level.

Use **Dialogues → Dialogue Script Integration…** for reviewed `OnRudeExit`
rewards, completion sound, and named-object world changes. Generated scripts are
staged below `SCRIPTS\MM9EDITOR\` in a replacement `SCRIPTS.REZ`.

See [Dialogue and quests](docs/user-guide/dialogue-and-quests.md).

## Prefabs

Open **Tools → Import Prefab…** for DEdit `.ed` or compiled v66 `.dat` sources.
The workspace reports object graphs, brush ownership, links, dependencies,
runtime representations, and blockers before placement.

Authored ED brushes are not runtime-compiled BSP. Installable prefab geometry
must resolve to a catalog-backed game model or validated compiled v66 BSP;
generated brush BSP remains preview-only and is blocked from game-bound Save.
Unsupported behavior never silently falls back to static geometry.

See [Prefab import](docs/user-guide/prefab-import.md).

## Conversion workflows

The **Conversion** menu contains:

- **LoMM to MM9** — creates a separate preservation-first staging batch and
  reports unsupported runtime actors.
- **glTF/GLB to DEDit ED…** — converts static mesh geometry into an ED prefab
  or minimal world using explicit topology, material, coordinate, and safety
  policies.
- **DAT to ED (Experimental)…** — reconstructs a compiled world as DEDit source;
  DEDit, Processor, and fresh in-game validation remain required.
- **DAT to glTF…** — exports BSP geometry for inspection. Edited glTF is not an
  import sidecar for patching an existing DAT.

See the [documentation index](docs/README.md) for conversion guides and
contracts.

## Existing save games

MM9 save files persist runtime state for many objects in the active level. An
installed object change may therefore be hidden when an old save restores its
previous state. Validate with a new game or a fresh load path that has not
already persisted the affected level. This does not mean every edit always
requires restarting the campaign.

## CLI tools

Discover current options with `--help`:

```powershell
python mm9_rezmgr.py --help
python catalog.py --help
python lomm_to_mm9.py --help
python -m features.model_conversion.abc_gltf_export --help
python -m features.dat_editing.gltf_to_ed_cli --help
python -m features.dat_editing.gltf_to_ed_validation_cli --help
```

Examples and resource-extraction prerequisites are in
[Model and texture export](docs/user-guide/model-export.md). The LoMM
compatibility CLI performs live insertion unless `--dry-run` is supplied; the
editor staging workflow is preferred for normal use.

## Documentation

Start with [docs/README.md](docs/README.md). It separates supported user guides,
format/reference contracts, contributor documentation, and non-normative
research.

Contributors can validate documentation with:

```powershell
python tools/check_docs.py
```

## License

See [LICENSE](LICENSE).
