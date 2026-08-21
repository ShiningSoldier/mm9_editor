# Game Resources

**Status: Reference**

The editor is REZ-backed. It reads the installed archives directly and
materializes only required entries into its cache. A manually extracted
`mm9_data` tree is neither required nor supported by the normal editor runtime.

## Installation detection

Use `--game-root "<mm9-root>"` to identify the folder containing `data`. If the
option is omitted, the editor checks its own folder and its parent.

Required MM9 archives:

- `WORLDS.REZ`
- `RUDE.REZ`
- `SCRIPTS.REZ`
- `TEXTURES.REZ`
- `SKINS.REZ`
- `MODELS.REZ`
- `DATA.REZ`

`SOUNDS.REZ` is optional and is used when present. An explicit incomplete game
root is rejected with a list of missing archives.

## Virtual paths

`GameResources` accepts normalized virtual paths such as:

```text
WORLDS/BOOTCAMP
RUDE/NPC1
SCRIPTS/YRSA
TEXTURES/...
SKINS/...
MODELS/...
```

Entries are commonly extensionless inside an archive even though the runtime
looks them up by resource type. For example, a world entry can be named
`WORLDS/BOOTCAMP` and a dialogue entry `RUDE/NPC1` with type `RUDE`.

Viewport materialization uses a cache keyed by the source archive path, size,
and modification time. Archive roots are stripped so the extracted layout
matches the existing DTX/ABC loaders.

## Writable directories

- Output is normally written below `<repo-root>/output/`.
- Backups are normally written below `<repo-root>/backups/`.
- Cache is normally stored below the user's local application-data directory.

When the preferred output or backup location is not writable, the editor uses
the platform-specific local application-data directory. The selected paths are
reported at startup. These folders are runtime data and are not part of the
Git checkout.

## Save batches

Normal Save builds complete replacement archives below
`output/<batch>/data/`. Multiple changed resources from the same source archive
are grouped into one rewrite. The batch also contains review copies and a
manifest describing source archives, outputs, and replaced virtual entries.

Fresh NPC metadata/dialogue updates patch `RUDE.REZ`. Dialogue integration and
reviewed prefab scripts patch `SCRIPTS.REZ`. Unrelated resources are preserved.
The archive resource type is retained or assigned as required by the runtime.

The editor never appends to a live REZ archive. REZ `NextWritePos` identifies
the directory-tree boundary, so safe modification is a complete output rewrite.

## Installation and restore

Installation reads the output manifest, confirms the affected files, saves the
current live files below an install backup, and replaces only manifested files.
Restore first backs up the current live state and then restores the selected
install backup. Manifest-declared loose files are supported for exceptional
experimental batches; they are not the normal resource path.

## LoMM staging

LoMM conversion reads a separate install and writes complete staged MM9
archives. While a staged converted level is active, its staged model and skin
archives take precedence, with independent fallback to the installed MM9
archives. Switching to an ordinary level restores the normal MM9 resource view.

