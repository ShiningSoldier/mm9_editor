# MM9 Mod Editor Handoff

This project is a three-dimensional visual editor for Might and Magic IX (MM9)
compiled world files (`.DAT`, version 66) directly from the game's REZ
archives. It builds on the bundled `mm9_patcher` parser/serializer and lets
modders open a level, place objects, edit properties, stage fresh NPC
dialogue, and save patched replacement REZ archives.

The editor is also capable to edit Legends of Might and Magic(LoMM) files,
because these games share almost the same engine and `.DAT` version.
Current limitatations regarding LoMM are described in
[the related document](lomm_conversion.md).

## Current State

The editor is centered on the OpenGL viewport. Object placement,
selection, movement, height control, yaw rotation, and property editing all
happen through the three-dimensional view and the Properties panel.

The normal workflow is:

```text
Open level from WORLDS.REZ -> select/add object -> click a BSP surface to place
-> drag/nudge/elevate/rotate in the viewport
-> edit fields in Properties
-> Save -> timestamped patched data/*.REZ output + manifest
```

The editor never writes over the live game archives during ordinary Save.
Opened source archives are backed up under `backups/`; committed output goes
under `output/<timestamp>/`. Installing output to the game is an explicit menu
action that creates a separate install backup first. The LoMM-to-MM9
conversion workflow follows the same rule: it creates a separate installable
staging batch, opens that batch for editing, and leaves the live game unchanged.

## Important files

IMPORTANT: we shouldn't modify the game files directly when adding new functionality to the editor!

- ```C:\Program Files (x86)\GOG Galaxy\Games\Might and Magic 9``` - Might and Magic 9 installation folder
- ```C:\games\Legends of Might and Magic``` - Legends of Might and Magic installation directory

The following directories contain copied REZ archives from both games, and their extracted versions. The files in these directories
can be used when adding new functionality to the editor.

- ```C:\lithtech\mm9_editor\mm9_data``` - MM9 resources that can be used when adding new functionality to the editor
- ```C:\lithtech\mm9_editor\lomm_data``` - LoMM resources that can be used when adding new functionality to the editor

The following directory contains the DEDit utility used by the makers of the game to create levels:

- ```C:\lithtech\Lith21tools```

The utility should not be called from the command line, because it requires user interactions by modal windows.

## Runtime Layout

Expected runtime layout:

```text
Might and Magic IX/
  data/
    WORLDS.REZ
    RUDE.REZ
    SCRIPTS.REZ
    TEXTURES.REZ
    SKINS.REZ
    MODELS.REZ
    DATA.REZ
  mm9_editor/
```

Python dependencies for the viewport:

```sh
pip install PyOpenGL PyOpenGL_accelerate pyopengltk
```

If OpenGL packages are unavailable, `view3d.gl_view` shows a placeholder with
the install hint. There is no separate editor mode fallback.

## Project layout

```text
app/                 editor startup, game detection, resource/cache setup
ui/                  Tk dialogs and panels
core/                project model, BSP/REZ primitives, save/load logic
catalog/             catalog builder, actor visual table parsing, catalog data
features/doors/      physical-door matching, cloning, validation, BSP writing
features/prefabs/    prefab inspection, static import planning, validation
features/presets/    preset persistence
features/model_conversion/  static ABC/DTX export to glTF, GLB, OBJ, and PNG
conversion/          LoMM-to-MM9 conversion tools and config
view3d/              existing OpenGL preview package
mm9_patcher/         DAT/RUDE patcher tools, kept stable until core refactors settle
tests/               package/feature-grouped test suites
```
Command launchers are kept at the package root.

Import/refactor rules:

- Use package-relative imports inside packages after each module is moved.
- Keep thin root launcher wrappers for existing commands:
  `mm9_editor.py`, `catalog.py`, `lomm_to_mm9.py`, `bsp.py`, and
  `mm9_rezmgr.py`.
- Move one cluster at a time and run `python -m unittest discover -s tests`
  after each cluster.
- Do not move generated/debug data into code packages except stable catalog
  artifacts, which now live under `catalog/data/`.

## Main Modules

| Path | Purpose |
|---|---|
| `app/editor.py` + `mm9_editor.py` | Main Tk application. `app/editor.py` owns menus, toolbar, project state, level panel, `View3D`, properties panel, save dialog, placement callbacks, movement/rotation/elevation commit logic, and preset commands. `mm9_editor.py` is the compatibility launcher. |
| `core/` | Shared editor infrastructure: project model, `.mm9mod` save/open support, BSP parser, REZ reader/writer, game resource provider, game-path autodetection, and install/restore logic. Root `bsp.py` and `mm9_rezmgr.py` remain command launchers only. |
| `mm9_patcher/mm9_patch.py` | DAT v66 parser/serializer. Trusted core for `World`, `WorldObject`, and `Property`. |
| `catalog/` + `catalog.py` | Builds/loads catalog data. `catalog.py` is a compatibility CLI wrapper; implementation lives in `catalog/builder.py`, actor table parsing in `catalog/actor_visuals.py`, and generated JSON in `catalog/data/`. |
| `conversion/` + `lomm_to_mm9.py` | LoMM-to-MM9 conversion. `lomm_to_mm9.py` is a compatibility CLI wrapper; reusable service/insertion logic and default YAML rules live under `conversion/`. |
| `ui/` | Tk panels and dialogs: level catalog panel, Add Object dialog, properties inspector, fresh NPC dialog, save/commit dialog, preset dialogs, REZ picker, LoMM conversion dialog, and door clone dialog. |
| `features/presets/manager.py` | User preset persistence. |
| `features/doors/` | Physical-door matching, clone planning, validation, and BSP writing. |
| `features/prefabs/` | Converted prefab inspection, static BSP import planning, and save-plan validation. |
| `features/model_conversion/` | Static LOD0 ABC export to glTF/GLB or OBJ, DTX-to-PNG conversion, explicit piece skins, and catalog-driven material variants. See `docs/model_conversion.md`. |
| `tests/` | Test package grouped by area: `tests/app_tests/`, `tests/catalog_tests/`, `tests/core_tests/`, `tests/feature_tests/doors/`, `tests/feature_tests/prefabs/`, and `tests/view3d_tests/`. Shared test path setup lives in `tests/_path.py`; folder names avoid shadowing production packages during `unittest discover`. |


## Current Caveats

- Source REZ archives must remain available for `.mm9mod` project replay.
- The editor saves patched REZ output under `output/<batch>/data/`; installing
  it into the game folder is an explicit backed-up action.
- Socket attachments are optional visual polish, not a current priority.
- Undo/redo is operation-based.  Top-level adds, deletes, property edits, and
  transform commits are undoable/redoable; edits to not-yet-saved added objects
  still mutate that object's `AddOp.overrides` and are undone as part of the
  whole add rather than as separate property-level steps.
- Object and world helper billboards are both hidden by default in the 3-D
  view.  Use the viewport `Helpers` toolbar toggle, or
  `View -> Toggle object helpers`, to show billboards for objects that already
  render with 3-D models. Use `View -> Toggle world helpers` to show handles for
  model-free service/control objects while debugging level logic. The catalog
  derives that distinction from the active game's object.lto inheritance and
  model resources observed in object.lto/DAT data, rather than a class-name
  list, so the same behavior covers MM9 and LoMM classes.

## A note regarding levels geometry

The levels for both games are stored as DAT files in the data/worlds directory.
Each DAT file represents a separate level.
MM9 also contains 8 ED files. They can be edited with the DEDit tool, and then converted to the game-ready DAT files.
The ED files were shipped with the released game by mistake.

## Suggested Next Work

1. Optional finer-grained pending-add history if editing newly added objects
   before save becomes a common workflow.
2. The OpenGL viewport renders DAT coordinates through a display-space X-axis
   reflection so level orientation matches the game.  Editing and saving still
   use the original MM9 game-space coordinates; placement and move callbacks
   convert display hits back before mutating object `Pos`.

