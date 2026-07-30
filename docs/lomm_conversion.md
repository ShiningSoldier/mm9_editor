# LoMM to MM9 Conversion Reference

## Purpose

`lomm_to_mm9.py` converts a Legends of Might and Magic level from LoMM `WORLDS.REZ` into MM9-compatible DAT bytes and inserts the converted level into MM9 `WORLDS.REZ` using a transactional archive replacement flow.

The converter is available in two forms:

- Standalone CLI launcher: `lomm_to_mm9.py`
- Editor workflow: `Conversion -> LoMM to MM9`

The editor workflow uses the same conversion service as the CLI, then opens the converted level from MM9 `WORLDS.REZ` for inspection.

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

1. **Convert classes via templates**
   - Runs first.
   - Replaces matching source objects, including LoMM-only enemies such as `Orc`, with clones of named MM9 template objects.
   - Retyped objects survive the later unknown-class removal stage.

2. **Drop unknown classes**
   - Removes any WorldObject whose class is not registered in the MM9 catalog and was not converted by a rule.

3. **Patch shared classes**
   - Adds missing MM9 properties to shared classes such as `StartPoint` and `WorldProperties`.

4. **Audit and copy assets**
   - Walks each remaining object's model, skin, and sound references.
   - Classifies each referenced asset as `in MM9`, `in LoMM only`, or `missing`.
   - Copies LoMM-only assets transactionally into MM9 `MODELS.REZ`, `SKINS.REZ`, or `SOUNDS.REZ`.

5. **Write and verify archives**
   - Writes a complete temporary `WORLDS.REZ`.
   - Creates a backup under `<mm9_root>/mm9_editor/backups/lomm_to_mm9_<timestamp>/data/`.
   - Replaces archives using `os.replace()`.
   - Verifies that the new level can be read back.

The backup folder also receives:

- `install_manifest.json`, including a `conversion` section with the LoMM source level and new MM9 entry
- `conversion_log.txt`, describing copied assets and conversion actions

## Standalone CLI Usage

```sh
python lomm_to_mm9.py \
    --mm9_root "C:\Path\To\Might and Magic IX" \
    --lomm_root "C:\Path\To\Legends of Might and Magic" \
    --level_to_convert CHATEAUESCAPE \
    --converted_level_name CHATEAUESCAPE_MM9
```

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
6. Confirm the live archive replacement.

After success, the editor:

- remembers the last successful LoMM install folder in `editor_settings.json`
- offers that folder the next time the dialog opens
- writes an automatic conversion backup of all modified archives
- writes `install_manifest.json` and `conversion_log.txt`
- opens the newly inserted MM9 level for inspection
- refreshes the viewport cache so newly copied models and skins render immediately

## Class Compatibility

In `CHATEAUESCAPE.DAT`, 5 classes are not registered in MM9. They account for 46 objects and must be removed or retyped before the level loads cleanly in MM9.

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

- `remove_unknown_classes`: drop classes not registered in the MM9 catalog.
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

Enemy conversion rules clone MM9 host class instances. This brings MM9-compatible stats, AI, sound tables, and animation state machines.

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

Custom enemy conversion rules can be added in YAML using `template`, `new_type`, `preserve`, `overrides`, and `add_props`.

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

## LoMM Catalog for Rule Research

A separate LoMM catalog can be generated for experimental conversion-rule design:

```powershell
python catalog.py build-from-rez `
  C:\lithtech\mm9_editor\lomm_data\worlds.rez `
  --object-lto C:\lithtech\mm9_editor\lomm_data\object.lto `
  --out C:\lithtech\mm9_editor\catalog\data\catalog_lomm.json
```

`catalog_lomm.json` mirrors the shape of MM9's `catalog/data/catalog.json` but indexes LoMM levels.
The builder automatically indexes a sibling `SKINS.REZ` or extracted `SKINS`
directory and writes model/skin combinations to `model_variants`. Exact DAT
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
