# Architecture

**Status: Contributor reference**

The editor is a Tk/PyOpenGL application built around a project model of pending
operations. It reads source resources from game REZ archives, materializes an
edited view for UI/preview, and produces complete replacement archives in a
separate output batch.

## Repository layout

| Path | Responsibility |
| --- | --- |
| `app/` | Startup, menus, resource setup, workflow coordination, and main editor state |
| `ui/` | Tk panels, dialogs, and workspaces |
| `core/` | Project operations, project persistence, DAT/BSP/REZ primitives, resources, save/install/restore |
| `catalog/` | Catalog generation, runtime class metadata, actor visuals, and generated catalog data |
| `features/doors/` | Legacy physical-door linking/cloning/writer compatibility |
| `features/prefabs/` | Prefab inspection, representation planning, behavioral graphs, resources, and validation |
| `features/model_conversion/` | ABC/DTX export to glTF/GLB, OBJ, and PNG |
| `features/dat_editing/` | DAT inspection export, DAT-to-ED reconstruction, glTF-to-ED conversion, and validation |
| `features/presets/` | User preset persistence |
| `conversion/` | LoMM-to-MM9 conversion service and rules |
| `view3d/` | OpenGL viewport, cameras, coordinates, BSP/ABC/DTX rendering, picking, and helpers |
| `mm9_patcher/` | DAT/RUDE patching primitives retained as a stable lower-level layer |
| `tests/` | Package- and feature-grouped tests |

Thin root launchers preserve existing commands: `mm9_editor.py`, `catalog.py`,
`lomm_to_mm9.py`, `bsp.py`, and `mm9_rezmgr.py`.

## Project model

`core/project.py` keeps a loaded baseline `World` and a sequence of operations
for each level. `LevelEdit.materialize()` produces the edited view used by the
panels and viewport. Existing-object indices are mapped back to baseline indices
before creating edit/move/delete operations.

Undo and redo move top-level operations between the per-level operation stacks.
Transforms are coalesced so a gesture remains useful as one history step.
Pending added objects update their `AddOp`; behavioral prefab assemblies remain
atomic operations. Project format 22 persists pending operations and independent
RUDE/script assets, but not redo history.

## Resource and save boundaries

`core/autodetect.py` discovers a complete game `data` directory and writable
runtime folders. `core/game_resources.py` reads virtual paths from archives and
materializes cache entries. Catalog templates and viewport resources resolve
through this layer rather than an extracted game-data tree.

Save planning groups changes by archive and performs complete output rewrites.
Ordinary Save never touches the live install. `core/install_manager.py` handles
explicit manifested installation and reversible backups.

## Viewport coordinates

DAT data remains in MM9 game space. `view3d/coords.py` reflects X into editor
display space and converts placement/movement hits back before project mutation.
Rendering and editing code must use these helpers consistently; it must not
persist display-space coordinates.

## Prefab boundary

Prefab analysis distinguishes authored ED data, catalog-backed model objects,
native object graphs, real compiled DAT BSP, and generated preview BSP. Only a
representation MM9 can load may pass game-bound validation. The planning result
and selected resources/bindings are persisted so Save does not rematch a source
silently.

## Import conventions

Use package-relative imports inside packages. Keep generated/debug resources
outside code packages except stable catalog artifacts under `catalog/data/`.
Do not introduce dependencies on local game extractions, DEDit installations,
or unrelated repositories.

