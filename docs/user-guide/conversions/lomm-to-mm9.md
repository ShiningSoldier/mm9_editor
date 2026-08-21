# Convert a LoMM Level to MM9

**Status: Supported conversion with explicit compatibility blockers**

The converter copies a Legends of Might and Magic v66 world into an MM9 output
batch, patches known shared-class differences, audits referenced assets, and
reports actors the MM9 runtime cannot construct. It does not make an LoMM-only
runtime class compatible merely by copying its model.

## Editor workflow

1. Start the editor with `--game-root "<mm9-root>"` and, optionally,
   `--lomm-root "<lomm-root>"`.
2. Choose **Conversion → LoMM to MM9**.
3. Select the LoMM installation and a level from its `WORLDS.REZ`.
4. Enter a new MM9 level name.
5. Choose an actor policy:
   - **Preserve** keeps unsupported actors visible for manual replacement and
     blocks installation while they remain. This is the default.
   - **Legacy** applies the bundled nearest-MM9 substitutions.
   - **Remove** deletes actors MM9 cannot construct.
6. Create the staging batch and inspect the opened converted level.

The editor writes `output/lomm_to_mm9_<timestamp>/`, including patched archives,
`manifest.json`, and `conversion_log.txt`. It does not modify the live game.
Unsupported actors are marked in the object list and can be filtered or removed.
An advanced installation override exists, but preserving an unknown runtime
class can make a level fail to load.

The converter stages LoMM-only model, skin, and sound assets when they can be
resolved. Successful editor preview is not evidence that MM9 implements the
actor class or behavior.

## CLI

The compatibility CLI performs live insertion unless `--dry-run` is supplied.
Prefer the editor staging workflow for normal work.

```powershell
python lomm_to_mm9.py `
  --mm9_root "<mm9-root>" `
  --lomm_root "<lomm-root>" `
  --level_to_convert CHATEAUESCAPE `
  --converted_level_name CHATEAUESCAPE_MM9 `
  --dry-run
```

Use `--actor-policy preserve`, `legacy`, or `remove` to override the rule-file
default. Use `--config` for another YAML/JSON rule file and `--lomm-catalog` for
another LoMM catalog. Existing catalogs are not rebuilt automatically.

## What conversion changes

- The binary DAT header, BSP, lightmaps, and PVS are retained.
- Known shared classes receive required MM9 properties, including
  `StartPoint.MovePlayerToFloor` and the save flags on `WorldProperties`.
- Configured non-actor classes such as treasure and fire objects can be cloned
  from MM9 templates.
- Unknown non-actor classes are removed unless explicitly retained by rules.
- Actor compatibility is based on the MM9 runtime registry, not visual
  similarity or catalog visibility.
- Referenced resources are classified as present in MM9, present only in LoMM,
  or missing; resolvable LoMM-only resources are staged into complete patched
  archives.

Default rules live in `conversion/lomm_to_mm9.yaml`. PyYAML is optional; without
it, the rule file must also be valid JSON.

## Connect the converted level

There is no transition wizard. Add the link manually:

1. Add a `StartPoint` in the destination level.
2. Add an `ExitTrigger` in the source level.
3. Set `DestinationWorld` to the destination DAT stem.
4. Set `StartPointName` to the destination `StartPoint.Name`.
5. Save both levels into the same output workflow and test a fresh load.

Converted levels do not automatically receive an MM9 `MAPSTATS.TXT` row, so
campaign integration and background music remain manual data work.

## Validation

- Resolve or deliberately remove every incompatible actor.
- Review missing assets and substitutions in `conversion_log.txt`.
- Confirm StartPoints, exits, treasure, fire, sounds, and scripted objects.
- Save/reopen the staged DAT and test the level through a fresh game load.

