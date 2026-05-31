# MM9 Mod Editor Handoff

This project is a three-dimensional visual editor for Might and Magic IX
compiled world files (`.DAT`, version 66) directly from the game's REZ
archives. It builds on the bundled `mm9_patcher` parser/serializer and lets
modders open a level, place objects, edit properties, stage fresh NPC
dialogue, and save patched replacement REZ archives.

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
conversion workflow is the explicit live-archive exception: it transactionally
adds a converted level to `data/WORLDS.REZ` only after writing an automatic
conversion backup.

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
  render with 3-D models. Use `View -> Toggle world helpers` to show
  trigger/sound/marker/world handles, AI rails/barriers, doors, lights,
  weather helpers, and `BlueWater` markers while debugging level logic.

## Suggested Next Work

1. Optional finer-grained pending-add history if editing newly added objects
   before save becomes a common workflow.
2. The OpenGL viewport renders DAT coordinates through a display-space X-axis
   reflection so level orientation matches the game.  Editing and saving still
   use the original MM9 game-space coordinates; placement and move callbacks
   convert display hits back before mutating object `Pos`.

