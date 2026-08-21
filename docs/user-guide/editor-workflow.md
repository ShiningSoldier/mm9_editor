# Editor Workflow

**Status: Supported**

The editor reads the original game archives, keeps changes in memory, and
writes replacement archives to a separate output batch. Ordinary Save never
overwrites the installed game.

## Start the editor

Run from the repository root:

```powershell
python mm9_editor.py --game-root "<mm9-root>"
```

`<mm9-root>` is the folder containing the game's `data` directory. You may
omit `--game-root` when the repository is inside the game folder or is itself a
folder containing a complete `data` directory.

For LoMM conversion support, also supply:

```powershell
python mm9_editor.py `
  --game-root "<mm9-root>" `
  --lomm-root "<lomm-root>"
```

The required archives and writable-directory behavior are described in
[Game resources](../reference/game-resources.md).

## Open and edit a level

1. Choose **File → Open Level from WORLDS.REZ…** or press `Ctrl+O`.
2. Select a level in the archive picker.
3. Select an existing object in the viewport or object list, or press `A` to
   add an object from the catalog.
4. Use the viewport and Properties panel to place and edit it.
5. Use **Edit → Undo** (`Ctrl+Z`) and **Edit → Redo** (`Ctrl+Y` or
   `Ctrl+Shift+Z`) while the changes remain pending.

See [Viewport](viewport.md) for camera and transform controls.

## Preview the current level

Choose **File → Run Current Level** or press `Ctrl+Alt+R`. The editor creates an
isolated preview under `output/run-preview/` containing unsaved DAT changes and
staged RUDE/script resources. It launches the game from that workspace; the
installed archives are read but not modified.

Close the running preview before launching another one. A preview is a smoke
test, not a replacement for validating an installable output batch.

## Save an output batch

Choose **File → Save…** or press `Ctrl+S`. Review the pending DAT, RUDE, script,
geometry, and safety diagnostics before committing. A typical batch contains:

```text
output/<timestamp>/
  data/WORLDS.REZ
  changed_entries/WORLDS/<level>.DAT
  manifest.json
```

Other patched archives appear only when the project changes their resources.

## Install and restore

Choose **File → Install Output to Game…** and select a completed output batch.
The editor reads its manifest, displays the affected files, and creates an
install backup before replacing live files.

Choose **File → Restore Installed Backup…** to restore such a backup. The
restore operation first backs up the current live files. Do not manually copy
partial output batches into the game directory when the editor can install the
manifested batch.

## Save a project

**File → Save Project…** (`Ctrl+Shift+S`) writes a `.mm9mod` file containing
pending operations and independently staged RUDE/script assets. **File → Open
Project…** (`Ctrl+Shift+O`) reloads the source resources and reapplies those
operations.

A project does not embed complete source levels or game archives. Keep the same
game installation accessible when reopening it. Redo history is session-only
and is not stored in the project file.

## Existing save games

MM9 saves runtime state for many objects in the active level. A change can be
installed correctly yet remain hidden when an old save restores that previous
state. Validate object changes with a new game or a fresh load path that has not
already persisted the affected level. This is not a claim that every edit
always requires starting the entire campaign again.

