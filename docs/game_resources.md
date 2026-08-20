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
  files. The shipped archive has normal dialogues `NPC1`-`NPC436`, so this
  currently suggests `437`.
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
  `changed_entries/RUDE/*.RUDE`. New extensionless archive entries carry the
  required `RUDE` resource type so the runtime can resolve its `.rude` lookup.
  There is no loose RUDE staging workflow.
- `core/rude.py` is the lossless RUDE domain layer. It models metadata,
  source-ordered state choices, all condition/effect slots, and known or
  unknown native actions. The fresh-NPC archive path now serializes through
  this model instead of assembling partial CSV rows directly.
- `Project.rude_assets` owns normal dialogues and the `NPC997`-`NPC999`
  journal assets independently of loaded levels. RUDE-only save plans and
  manifests are supported, and archive patches include only the metadata or
  dialogue resources whose bytes changed.
- `Tools -> Dialogue & Quest Editor...` opens those assets without a level.
  Its graph/state surface keeps choice order and every RUDE slot editable, and
  its simulator evaluates required/forbidden keys and grant/remove effects
  against a user-provided mock party key set. Native actions are identified
  and reported as terminal mock outcomes rather than emulating engine UI or
  script services.
- The editor's Quest Tools tab builds a cross-archive key index from all RUDE
  rows plus `HasKey`/`GiveKey`/`TakeKey` script calls, with current project
  assets overlaid on the source archives. It can author stock-shaped Quest
  Notes (`NPC997`) and Awards (`NPC999`) rows and validates graph reachability,
  key predicates/effects, text limits, and action-specific native parameters.
  Dynamic script operands remain explicitly unresolved instead of being
  assigned speculative key ids.
- Explicit install is in place. `File -> Install Output to Game...`
  asks the user to choose an `output/<batch>` folder, reads its manifest-aware
  patched archive list and any manifest-declared loose files, confirms the
  affected files, backs up the live game files under
  `backups/install_<timestamp>/data/`, then replaces only those files in the
  detected game `data` folder. Loose files are used by experimental
  `object.lto` batches. The installer logic lives in `core/install_manager.py`;
  it writes an `install_manifest.json` next to the backup and uses a temporary
  `<name>.installing` copy before `os.replace()`.
- Restore is in place. `File -> Restore Installed Backup...` accepts
  an install backup folder, its `data` subfolder, or a folder with
  `install_manifest.json`. It backs up the current live files under
  `backups/restore_<timestamp>_current/data/`, then restores the original REZ
  and loose files from the selected install backup. Loose files that did not
  exist before install are removed on restore. `restore_manifest.json` records
  what was restored and where the pre-restore live files were saved.
- LoMM-to-MM9 conversion is in place. The dropdown option
  `Conversion -> LoMM to MM9` opens a dialog that accepts a LoMM install folder,
  lists v66 DAT levels from LoMM `WORLDS.REZ`, asks for the new MM9 level name,
  converts the DAT through the YAML pipeline, and writes a separate installable
  staging batch. The staged `MODELS.REZ`, `SKINS.REZ`, and `SOUNDS.REZ` are
  complete patched MM9 archives; the live install is not modified. When a
  converted level is active, the viewport uses its staged model and skin archives
  and falls back independently to the live MM9 archive when either one is absent.
  Switching back to an ordinary level restores the live MM9 resource view.
- The conversion audit stages explicit DAT asset references and implicit skins
  resolved from the LoMM catalog's `model_variants`. Each implicit resolution is
  included in `manifest.json` and `conversion_log.txt`. A missing catalog is built
  automatically when the editor is launched with a valid `--lomm-root`; existing
  catalogs are never overwritten automatically. The last successful LoMM install
  path is remembered in `editor_settings.json`.
- LoMM catalogs also inventory model resources. Converted levels may carry
  editor-only actor visual overrides when a class uses a misleading DAT model
  fallback. The viewport uses the staged LoMM model/skin, while saved DAT
  properties and MM9 compatibility classification remain unchanged.


## REZ Archives

- REZ type tags are byte-reversed on disk.
- Level entries are named like `WORLDS/BOOTCAMP` with no extension.
- Editability should be detected by payload magic bytes, not by entry name.
- `NextWritePos` points to the directory-tree boundary, so safe writing is a
  full output rewrite, not append-in-place.

