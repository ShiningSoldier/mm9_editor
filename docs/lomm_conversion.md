# LoMM to MM9 Conversion Reference

## Purpose

`lomm_to_mm9.py` converts a Legends of Might and Magic level from LoMM `WORLDS.REZ` into MM9-compatible DAT bytes.

The converter is available in two forms:

- Standalone CLI launcher: `lomm_to_mm9.py`
- Editor workflow: `Conversion -> LoMM to MM9`

The editor workflow creates a separate installable staging batch and opens the converted level from the staged `WORLDS.REZ`. It does not modify the live game. The standalone CLI retains its transactional live-insertion command for backward compatibility; use `--dry-run` for a non-writing CLI preview. Live insertion refuses unresolved actors unless the advanced `--allow-incompatible-actors` override is supplied.

## Tested Baseline

The implementation and findings were tested against `CHATEAUESCAPE.DAT` from Legends of Might and Magic.

Key observed facts for this level:

- 742 WorldObjects
- 29 classes
- 16 `StartPoint`s clustered around two arrival areas
- 0 `ExitTrigger`s
- No custom `ScriptName` values, so no `SCRIPTS.REZ` patching is required for this level to load

## Required Game Archives

The MM9 install must include:

- `data/WORLDS.REZ`
- `data/RUDE.REZ`
- `data/SCRIPTS.REZ`

For normal editor operation, the broader MM9 editor also uses:

- `data/TEXTURES.REZ`
- `data/SKINS.REZ`
- `data/MODELS.REZ`
- `data/DATA.REZ`

For LoMM conversion, the selected LoMM install must include:

- `data/WORLDS.REZ`
- `data/RUDE.REZ`
- `data/SCRIPTS.REZ`

Asset copying may also use LoMM `MODELS.REZ`, `SKINS.REZ`, and `SOUNDS.REZ` when LoMM-only assets need to be copied into MM9.

## DAT Container Compatibility

LoMM and MM9 use closely related LithTech engine formats. LoMM `.DAT` files are version 66 and share the same 44-byte header layout:

- version
- `obj_pos`
- `ren_pos`
- 32 zero bytes

The following regions transfer cleanly to MM9 without being rewritten:

- binary header
- BSP data
- lightmaps
- PVS data

`World.load()` can parse the LoMM DAT directly, and `serialize_objects()` round-trips the object section byte-identically for the tested `CHATEAUESCAPE.DAT` payload of 3,132,910 bytes.

Additional compatible details:

- property type codes match
- Pascal string layout matches
- byte-reversed REZ type tags match
- the float-bits-in-`LongInt` quirk matches

## Conversion Pipeline

The current pipeline is YAML-driven and implemented by `conversion/lomm_to_mm9.py` plus `conversion/lomm_to_mm9_service.py`. The root `lomm_to_mm9.py` remains a compatibility launcher.

Pipeline stages:

1. **Analyze actor compatibility**
   - Reads class hierarchies from each game's active `data/object.lto` when possible.
   - Falls back to the generated MM9/LoMM catalog LTO layers, then to conservative DAT property signatures.
   - Treats an actor as supported only when the MM9 runtime registry marks its class as loadable. Hidden classes such as MM9 `Dwarf` therefore remain valid.
   - Records a per-object decision and registry provenance in the conversion report.

2. **Apply the selected actor policy and non-actor template rules**
   - `preserve` (default) keeps unsupported actors for manual editing and does not apply historical actor substitutions.
   - `legacy` explicitly enables the historical nearest-MM9 actor substitutions in the YAML.
   - `remove` explicitly removes only actors that MM9 cannot construct.
   - Non-actor conversions such as `TreasureChest`, `Fire`, and `Brazier` continue to use MM9 templates.

3. **Drop unknown non-actor classes**
   - Removes non-actor WorldObjects absent from MM9's LTO/catalog layers.
   - Unsupported actors are never silently removed under `preserve` or `legacy`.

4. **Patch shared classes**
   - Adds missing MM9 properties to shared classes such as `StartPoint` and `WorldProperties`.

5. **Audit and stage assets**
   - Walks each remaining object's model, skin, and sound references.
   - Classifies each referenced asset as `in MM9`, `in LoMM only`, or `missing`.
   - Adds LoMM-only assets to staged `MODELS.REZ`, `SKINS.REZ`, or `SOUNDS.REZ` archives.

6. **Write and verify the editor batch**
   - Writes `<output>/lomm_to_mm9_<timestamp>/data/*.REZ` plus `manifest.json` and `conversion_log.txt`.
   - Verifies the new level and copied assets by reading them back from the staged archives.
   - Marks the batch with a blocking issue while unsupported actors remain. Installation is rejected by default, with an explicit advanced override available.

The live-insertion CLI path still creates backups and `install_manifest.json`. Both paths include the structured compatibility report in their conversion statistics.

## Standalone CLI Usage

```sh
python lomm_to_mm9.py \
    --mm9_root "C:\Path\To\Might and Magic IX" \
    --lomm_root "C:\Path\To\Legends of Might and Magic" \
    --lomm-catalog "C:\Path\To\catalog_lomm.json" \
    --level_to_convert CHATEAUESCAPE \
    --converted_level_name CHATEAUESCAPE_MM9
```

If the selected LoMM catalog does not exist, the converter builds it from the
LoMM install before starting conversion. An existing catalog is never
overwritten automatically.

Preview without modifying MM9 archives:

```sh
python lomm_to_mm9.py \
    --mm9_root "C:\Path\To\Might and Magic IX" \
    --lomm_root "C:\Path\To\Legends of Might and Magic" \
    --level_to_convert CHATEAUESCAPE \
    --converted_level_name CHATEAUESCAPE_MM9 \
    --dry-run
```

Use a different rule file:

```sh
python lomm_to_mm9.py ... --config my_rules.yaml
```

Choose actor handling explicitly:

```sh
python lomm_to_mm9.py ... --actor-policy preserve
python lomm_to_mm9.py ... --actor-policy legacy
python lomm_to_mm9.py ... --actor-policy remove
```

Force a fresh class scan instead of using `catalog.json`:

```sh
python lomm_to_mm9.py ... --catalog ""
```

Both install roots are validated before conversion. The requested LoMM source level must exist in LoMM `WORLDS.REZ`, and the requested converted MM9 level name must not already exist in MM9 `WORLDS.REZ`, whether looked up with or without `.DAT`.

`_Mm9Catalog.load_level()` accepts the level path with or without `.DAT` and transparently retries the alternate form if the literal lookup fails.

`RezWriter.add()` uses a DAT restype inferred from the converted payload magic. This matters because MM9 world entries are usually extensionless, such as `WORLDS/BOOTCAMP`, rather than `WORLDS/BOOTCAMP.DAT`.

## Editor Workflow

In the editor:

1. Open the `Conversion` dropdown.
2. Choose `LoMM to MM9`.
3. Select the LoMM install folder.
4. Pick a LoMM level from its `WORLDS.REZ`.
5. Enter the new MM9 level name.
6. Choose how actors are handled. Preserve is recommended and selected by default.
7. Confirm creation of the staging batch.

After success, the editor:

- remembers the last successful LoMM install folder in `editor_settings.json`
- offers that folder the next time the dialog opens
- leaves the live MM9 install unchanged
- writes an installable batch with `manifest.json` and `conversion_log.txt`
- opens the staged MM9 level for inspection
- marks incompatible actors with `!` in red in the object list
- offers an incompatible-only filter and a bulk delete action
- carries staged model/skin/sound archives into later editor save batches
- blocks installation by default while incompatible actors remain

## Class Compatibility

Compatibility is based on the runtime class registry rather than the visible catalog alone. A same-named MM9 actor is reported as `same_name_mm9_implementation`: MM9 can construct it, but this does not promise identical LoMM behavior, stats, or visuals. An LoMM-only actor is reported as `unsupported_actor_preserved` until the user deletes it or opts into a substitution.

In `HIDEOUT.DAT`, for example, MM9's active LTO contains a hidden but runtime-loadable `Dwarf`, so the three Dwarfs remain `Dwarf`. The five `Orc` instances and one `Princess` are preserved and flagged as unsupported instead of becoming `LizardOrc` or disappearing silently.

In `CHATEAUESCAPE.DAT`, 5 classes are absent from MM9. Non-actor multiplayer helpers are removed by the general class filter; `Orc` remains editable under the default actor policy.

| LoMM class | Count | LoMM purpose | MM9 nearest match / action |
|---|---:|---|---|
| `CandleWall` | 24 | wall candle prop with engine-driven flame | `CandleProp` or `WallTorch` |
| `Orc` | 17 | LoMM enemy AI using `models\Goblin.abc` | `HalfOrcSoldier`, `LizardOrc`, or another MM9 monster |
| `GoodKingRescueZone` | 2 | multiplayer rescue-the-king volume | delete |
| `BuyZone` | 2 | Counter-Strike-style buy region | delete |
| `Timer` | 1 | scripted timer | delete |

The remaining 24 observed classes are registered in MM9, including:

- `AIRail`
- `Door`
- `Prop`
- `Light`
- `DirLight`
- `AmbientSound`
- `StartPoint`
- `WorldObject`
- `BlueWater`
- `OutsideDef`
- `TreasureChest`
- `Brazier`
- `Fire`

## Unsupported-LoMM Editor Preview

Preservation-first conversion lets unsupported LoMM actors use their original
visuals inside the editor without implying MM9 runtime compatibility.

1. **The editor bootstraps a missing LoMM catalog from `--lomm-root`.**

   - The editor accepts `--lomm-root` and validates it using the same install
     checks as the conversion dialog.
   - When `catalog/data/catalog_lomm.json` (or the path supplied with
     `--lomm-catalog`) is absent and a valid LoMM root was provided, the editor
     generates it from that install's `WORLDS.REZ`,
     `object.lto`, and available model/skin resource indexes.
   - Existing catalogs are not overwritten automatically, and generation
     failures are reported before the editor opens.
   - The builder writes through a temporary output and replaces the destination
     only after a complete catalog has been validated, so interruption cannot
     leave a partially written catalog.

2. **Converted-level previews use staged model and skin archives.**

   - A converted `LevelEdit` retains its conversion staging directory.
   - When that level is active, prefer staged `MODELS.REZ` and `SKINS.REZ` for
     viewport extraction and cache construction whenever those archives are
     present. They are complete patched MM9 archives, not delta archives, so
     normal MM9 assets remain available through the same resource view.
   - Fall back independently to the detected live MM9 archive when a staged
     archive is absent.
   - The editor rebuilds the viewport's model and skin caches when switching
     between staged and ordinary MM9 levels, and restores the normal MM9
     resource view when the converted level is no longer active.

3. **Conversion stages implicit LoMM skins discovered through the catalog.**

   - Explicit DAT `Skin` references remain the first source of truth.
   - For a model with no explicit skin, resolve normalized `Filename` entries
     through `catalog_lomm.json` `model_variants`, including primary and
     accessory skins when present.
   - Copy resolved LoMM-only skins into a staged `SKINS.REZ` and record their
     catalog provenance in the conversion report. For example,
     `models\\dragonred.abc` resolves to `skins\\dragonred.dtx` even though the
     placed `DragonRed` object has no `Skin` property.
   - The incompatible-actor warning and installation blocker remain. Successful
     editor preview must remain explicitly separate from MM9 runtime support.

4. **Class-named LoMM models can replace misleading DAT fallbacks in previews.**

   - The LoMM catalog inventories `MODELS.REZ` or the extracted `MODELS`
     directory as well as skins.
   - For actor classes whose DAT `Filename` is only a placeholder, conversion
     can select a class-named LoMM resource when its catalog variants match the
     class. The original DAT property is not changed.
   - For example, `Princess` stores `models\\player\\king.abc` in the DAT and
     `object.lto`, while LoMM ships `models\\princess.abc` and four color skins.
     The editor deterministically previews `Princess Blue`, stages that model
     and skin, and continues to mark the object as MM9-incompatible.

The catalog and conversion paths were validated against `ISLEOFFIRE`: the
`DragonRed` model resolves its implicit `skins\\dragonred.dtx`, that skin is
staged and reported with catalog provenance, and `DragonRed` remains classified
as unsupported by the MM9 runtime.

## Shared-Class Property Differences

The parser keys properties by name. Unknown properties are silently dropped by the engine, and missing properties fall back to engine defaults.

Differences that matter during conversion:

- `StartPoint` is missing MM9's `MovePlayerToFloor`.
  - Add `MovePlayerToFloor = 1` so the player does not spawn floating or below the floor.

- `WorldProperties` is missing MM9's `CanSaveGame` and `CanMiniSaveGame`.
  - Add both with value `1` so the player can save in the converted level.

- `Brazier` and `Fire` in LoMM use a single `Type` field instead of MM9's full particle parameter set.
  - Without conversion, fire and smoke effects do not render.

- `TreasureChest` in LoMM uses `KeyItemId` and `SpawnItem`.
  - MM9 expects fields such as `Random`, `Gold`, `GoldOnly`, `Item1` through `Item5`, `TrapLevel`, `TreasureLevel`, `TreasureOptions`, `TreasureType0_7`, and AI reachability fields.
  - Without conversion, the chest opens but is empty.

- `Cow` in LoMM has faction-combat fields such as `PickRandomWeapon`, `TeamNbr`, and `WeaponItemNbr`.
  - It lacks MM9 AI rail, wander, range-attack, and repopulation fields.
  - The cow loads but is inert.

Harmless differences:

- `WorldProperties` carries LoMM-only `MusicDirectory`, `InstrumentFiles`, `AmbientList`, `CruisingList`, `HarddrivingList`, `CDTrack`, and `ScenarioNbr`; MM9 ignores them.
- Many shared classes are missing MM9 engine-level additions such as `Alpha`, `BoxPhysics`, `DisableFog`, `NeedsTick`, `TouchNotify`, `ShouldMiniSave`, `OneTimeDamage`, `DamageAIOnly`, and `DamagePlayerOnly`; engine defaults apply.
- `StartPoint.PlayerNbr` in `CHATEAUESCAPE` is stored as the IEEE-754 float bit pattern of the slot number, for example `1090519040 = 8.0` bits. MM9 reads `PlayerNbr` as raw `uint32`. Because MM9 is single-player, non-zero values are usually irrelevant, and the converter leaves them unchanged.

## Default YAML Rule Structure

Default rules live in `conversion/lomm_to_mm9.yaml`.

Top-level sections:

```yaml
remove_unknown_classes: true
extra_remove_classes: []
keep_classes: []
actor_policy: preserve

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
    preserve: [Name, Pos, Rotation, Filename, Skin]
  Fire:
    template: "WORLDS/1000TERRORS.DAT::Brazier46"
    new_type: Brazier
    preserve: [Name, Pos, Rotation, Filename, Skin]
```

Meaning of major rule fields:

- `actor_policy`: `preserve` (default), `legacy`, or `remove`.
- `remove_unknown_classes`: drop non-actor classes not present in the MM9 LTO/catalog layers.
- `extra_remove_classes`: explicitly drop additional classes.
- `keep_classes`: exempt custom classes from unknown-class removal.
- `patch_class`: add or override properties on classes that remain shared.
- `convert_class`: clone MM9 template objects to replace LoMM objects.
- `template`: MM9 source object to clone.
- `new_type`: replacement class type.
- `preserve`: source fields copied onto the clone.
- `overrides`: update existing template fields with absolute values.
- `add_props`: add fields not present on the template.

If PyYAML is unavailable, the config loader falls back to JSON parsing.

## DAT Property Type Codes

| Code | Type |
|---:|---|
| 0 | LT string |
| 1, 2 | vec3, three floats |
| 3 | float32 |
| 5 | bool, one byte |
| 6 | uint32 |
| 7 | quaternion |

## Enemy Porting

The bundled historical enemy rules clone MM9 host class instances. They are now applied only when `actor_policy: legacy` or `--actor-policy legacy` is selected. This makes the lossy nearest-equivalent conversion explicit.

The default rule set covers:

- `Orc`
- `Goblin`
- `LizardMan`
- `LizardWarrior`
- `Dwarf`
- `Soldier`
- `Mummy`
- `Wight`
- `EvilEye`
- `EvilEyeTerror`

Custom enemy conversion rules can be added in YAML using `template`, `new_type`, `preserve`, `overrides`, and `add_props`. Preserving an unsupported LoMM class does not make MM9 able to render it; importing LoMM assets alone is insufficient because MM9 also lacks the class/AI implementation. Such actors must be removed, replaced, or supported by a separately developed MM9 runtime extension.

## Asset Compatibility and Audit

The converter audits references from each remaining object:

- `Filename` values for `.abc`, `.lta`, and `.ltb`
- `Skin` values for `.dtx`
- referenced sounds for `.wav`
- `AmbientSound` `.wav` paths

Each asset is classified as:

- **in MM9**: already exists in MM9 archives, so no action is needed
- **in LoMM only**: exists in LoMM and is copied into the corresponding MM9 archive
- **missing**: absent from both MM9 and LoMM; provide the file or substitute a different asset in the YAML

For the tested `CHATEAUESCAPE.DAT`:

- 32 distinct `Filename` values were observed.
- 29 distinct `Skin` values were observed.
- 29 of 32 models exist in MM9.
- Missing model names: `Barrel02`, `Chest-Lacquer`, `Painting_Rectangle`, `Goblin`.
- 24 of 29 skins exist in MM9.
- Missing skin names: `Barrel02`, `Chest-Lacquer`, `HorseStatue2`, `Painting_Rectangle5`, `Chest-Rusty01`, `Goblin`.
- `HorseStatue2` has a near MM9 match named `HorseStatue`.
- `Chest-Rusty01` has a matching model in MM9 but a different skin name.
- Ambient sound paths could not be verified against unavailable `SOUNDS.REZ` / `DATA.REZ` in the test workspace, but missing sounds are silent rather than fatal.

The audit summary is printed on every conversion run. Use `--dry-run` to preview it without writing the output DAT or replacing archives.

## Goblin ABC Preview Notes

`Goblin.abc` is a valid LithTech ABC model:

- path: `MODELS/GOBLIN`
- size: 985,190 bytes
- 17 parent animations
- 68 nodes
- 3 pieces
- `nVerts = 602`
- `nVertWeights = 1863`

LoMM Goblin uses true multi-weight vertex records, so the editor must not parse it using the older fixed 48-byte stride.

Observed vertex record layout:

- `uint16 n_weights`
- `uint16 weight_set_index_or_flags`
- `n_weights` entries of:
  - `uint32 bone_index`
  - `float x`
  - `float y`
  - `float z`
  - `float weight`
- saved model-space vertex position: `float x`, `float y`, `float z`
- saved model-space normal: `float nx`, `float ny`, `float nz`

Record size formula:

```text
28 + 20 * n_weights
```

Single-weight records remain 48 bytes.

For static editor previews, multi-weight characters should use the saved model-space position and normal instead of reconstructing bind pose from the weight list. Reconstructing from the current node matrices is slightly wrong on Goblin and visibly flattens details such as the head.

Heavily multi-weighted LoMM characters such as `Goblin.abc` may render as imploded shards in the editor viewport even though the game executable displays them correctly at runtime.

## Level Connectivity

`CHATEAUESCAPE.DAT` has no DAT-driven exit back to the MM9 world graph.

To connect it to MM9 gameplay:

1. Add an `ExitTrigger` to the converted LoMM level.
2. Set its `DestinationWorld` to the source MM9 level.
3. Add a matching `StartPoint` in the destination/source MM9 level.

The editor's transition wizard can apply this kind of transition setup.

## LoMM Catalog

The recommended editor startup form generates a missing LoMM catalog
automatically:

```powershell
python mm9_editor.py `
  --game-root "C:\Path\To\Might and Magic 9" `
  --lomm-root "C:\Path\To\Legends of Might and Magic"
```

Use `--lomm-catalog <path>` to select a non-default catalog. Existing catalogs
are loaded as-is and are not rebuilt automatically.

A catalog can also be generated manually from a LoMM install:

```powershell
python catalog.py build-lomm `
  "C:\Path\To\Legends of Might and Magic" `
  --out C:\lithtech\mm9_editor\catalog\data\catalog_lomm.json
```

This install-root profile deliberately does not read MM9-style
`ACTOR.TXT`/`MONSTERS.TXT` tables. It combines the LoMM `object.lto`, observed
DAT properties, and the LoMM skin inventory.

`catalog_lomm.json` mirrors the shape of MM9's `catalog/data/catalog.json` but indexes LoMM levels.
The builder automatically indexes sibling `MODELS.REZ`/`SKINS.REZ` archives or
extracted `MODELS`/`SKINS` directories. It records `model_resources` and writes
model/skin combinations to `model_variants`. Exact DAT
and `object.lto` associations take precedence. For actor classes whose skin is
implicit in the engine, conservative same-name variants such as
`Goblin.dtx`/`GoblinChief.dtx` are included without treating accessory names
such as `GoblinPole.dtx` as appearances.

For each LoMM-only class entry, it can expose:

- source template level and instance
- observed `property_names`
- model filenames used across LoMM levels
- default class metadata and properties from `object.lto`
- exact and inferred model skin variants for static glTF/GLB export

## Music Conversion Notes

LoMM and MM9 configure music differently.

### LoMM

LoMM stores level music-related fields directly in the level `.DAT` on `WorldProperties`, including:

- `MusicDirectory`
- `InstrumentFiles`
- `AmbientList`
- `CruisingList`
- `HarddrivingList`
- `CDTrack`
- `ScenarioNbr`

In shipping LoMM levels, these fields are empty strings. LoMM relies on environmental `AmbientSound` object placement instead.

### MM9

MM9 background music is decoupled from level DAT files. The engine uses `data.rez/DATA/MAPSTATS.TXT` to map a map filename to:

- `Music Track`
- `Battle Track`

Converted LoMM levels are not part of the original MM9 campaign, so they have no `MAPSTATS.TXT` entry and therefore no background music by default.

To assign music (UNCOMFIRMED):

1. Open the active MM9 configuration file `data.rez/DATA/MAPSTATS.TXT`.
2. Add a row for the converted level filename.

Example:

```text
#  Name                 File name          Music Track   Battle Track   ...
81 Chateau Escape       ChateauEscape_mm9  8             3              ...
```

Example track notes from the source material:

- track `8`: Thjorgard
- track `7`: Sturmford
- track `12`: Drangheim

## Practical Conversion Checklist

Before conversion:

- Verify both MM9 and LoMM install roots.
- Confirm required archives exist.
- Choose a converted level name that does not already exist in MM9 `WORLDS.REZ`.
- Run `--dry-run` when testing a new rule set.

During rule tuning:

- Retype important LoMM-only gameplay objects before unknown-class removal.
- Drop multiplayer-only volumes such as `GoodKingRescueZone` and `BuyZone` unless a custom MM9 replacement exists.
- Patch `StartPoint` and `WorldProperties` every time.
- Convert `TreasureChest`, `Fire`, and `Brazier` where gameplay or visuals matter.
- Review the asset audit for `missing` entries.

After conversion:

- Inspect the converted level in the editor.
- Add exits and matching start points if the level should connect to the campaign.
- Add a `MAPSTATS.TXT` entry if background music is desired.
- Test in a new game or appropriate fresh load path, because some changes may not appear in previously saved games.
