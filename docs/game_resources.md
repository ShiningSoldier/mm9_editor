# Game Install Detection And Resources

- `core/autodetect.detect()` requires a nearby MM9 install. It checks the editor
  folder, then its parent, for a `data/` folder containing the required
  `WORLDS.REZ`, `RUDE.REZ`, and `SCRIPTS.REZ` archives. `--game-root <path>`
  can be used to point at an install explicitly.
- `GamePaths.archive_path(key)` returns the detected game archive when
  available, and `GamePaths.has_archive(key)` reports whether a game archive
  was found.
- `GamePaths.resources()` creates a `GameResources` provider. The main editor
  stores it as `EditorApp.resources`.
- `GameResources` accepts game virtual paths such as `WORLDS/STURMFORDCITY`,
  `RUDE/NPC1`, `SCRIPTS/YRSA`, `TEXTURES/...`, `SKINS/...`, and `MODELS/...`.
  It reads from detected REZ archives only.
- `Open Level from WORLDS.REZ...` launches the
  REZ picker.
- Catalog template lookup falls through to `GameResources`, so placing a
  catalog class/model can load the recorded source level from `WORLDS.REZ`
  without manually extracted files.
- Fresh NPC number suggestion scans `RUDE/NPC<N>` through
  `GameResources`, excluding the special `NPC997`-`NPC999` journal/note/award
  files. In the bundled extracted data this currently suggests `438`.
- The cache bridge is in place for viewport assets. `core/autodetect` resolves
  a writable cache folder, and `GameResources.cache_archive_tree()` can
  materialize REZ entries into a versioned cache keyed by archive path, size,
  and mtime. `EditorApp` passes cached folders to the existing DTX/ABC
  loaders. Cache extraction strips the archive root, so
  `TEXTURES/A/B.DTX` becomes `<cache>/A/B.DTX`, matching the existing loader
  lookup rules.
- REZ output is in place for level saves. Normal Save never
  overwrites the live game archive. For REZ-sourced levels, the save plan
  writes patched archives to `output/<batch>/data/<archive>.REZ`; for example,
  editing `WORLDS/STURMFORDCITY` from `WORLDS.REZ` writes
  `output/<batch>/data/WORLDS.REZ`. Multiple edited levels from the same
  source archive are grouped into one `RezWriter` pass so edits do not clobber
  each other. A loose review copy of each patched entry is also written under
  `output/<batch>/changed_entries/<virtual-path>.DAT`, and `manifest.json`
  includes an `archives` section listing source archives, output archives, and
  replaced virtual entries.
- Archive patch planning is in place. `SavePlan.archive_patches`
  describes every archive output before commit. Fresh NPC RUDE registrations
  now patch `RUDE.REZ` directly when a game `data/RUDE.REZ` archive is
  detected: `NPCNAME`, `TOPBLURB`, and `NPC<N>` are written to
  `output/<batch>/data/RUDE.REZ`, with review copies under
  `changed_entries/RUDE/*.RUDE`. There is no loose RUDE staging workflow.
- Explicit install is in place. `File -> Install Output to Game...`
  asks the user to choose an `output/<batch>` folder, reads its manifest-aware
  patched archive list, confirms the affected archives, backs up the live game
  `data/*.REZ` files under `backups/install_<timestamp>/data/`, then replaces
  only those archives in the detected game `data` folder. The installer logic
  lives in `core/install_manager.py`; it writes an `install_manifest.json` next to
  the backup and uses a temporary `<archive>.installing` copy before
  `os.replace()`.
- Restore is in place. `File -> Restore Installed Backup...` accepts
  an install backup folder, its `data` subfolder, or a folder with
  `install_manifest.json`. It backs up the current live archives under
  `backups/restore_<timestamp>_current/data/`, then restores the original REZ
  files from the selected install backup. `restore_manifest.json` records what
  was restored and where the pre-restore live files were saved.
- LoMM-to-MM9 conversion is in place. The dropdown option
  `Conversion -> LoMM to MM9` opens a dialog that accepts a LoMM install folder,
  lists v66 DAT levels from LoMM `WORLDS.REZ`, asks for the new MM9 level name,
  converts the DAT through the YAML pipeline, copies missing models, skins, and
  sounds from LoMM archives/loose files into MM9's `MODELS.REZ`, `SKINS.REZ`, and
  `SOUNDS.REZ` (if present), and transactionally applies these updates. Before
  replacing the live archives, it writes backups of all modified archives under
  `backups/lomm_to_mm9_<timestamp>/data/`, creates a `conversion_log.txt` detailing
  all copied assets, and writes an `install_manifest.json` registering all changed
  archives. The backup is fully compatible with the existing restore flow. After a
  successful conversion, the editor opens the new level immediately. The last
  successful LoMM install path is remembered in `editor_settings.json`.


## REZ Archives

- REZ type tags are byte-reversed on disk.
- Level entries are named like `WORLDS/BOOTCAMP` with no extension.
- Editability should be detected by payload magic bytes, not by entry name.
- `NextWritePos` points to the directory-tree boundary, so safe writing is a
  full output rewrite, not append-in-place.

