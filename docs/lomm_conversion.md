# Legends of Might and Magic Interop

The editor and patcher were tested against `CHATEAUESCAPE.DAT` from
Legends of Might and Magic (LoMM, 2001). LoMM is built on the same LithTech
engine family as MM9 and its DAT files use the same v66 container, which
makes large parts of a LoMM level directly reusable. The findings below
are the basis of the `lomm_to_mm9.py` converter and of the editor's preview behaviour
for LoMM content.

## Container Compatibility

- LoMM `.DAT` files are version 66 with the same 44-byte header layout
  (version, obj_pos, ren_pos, 32 zero bytes).
- `World.load()` parses the file without modification, and
  `serialize_objects()` round-trips byte-identical
  (3,132,910 bytes for `CHATEAUESCAPE.DAT`). This means the BSP,
  lightmap, and PVS regions transfer to MM9 unchanged.
- Property type codes, Pascal string layout, byte-reversed REZ type
  tags, and the float-bits-in-LongInt quirk all match.

## Class Differences

In `CHATEAUESCAPE.DAT` (742 objects, 29 classes), 5 classes are not
registered in MM9 and account for 46 objects. They must be removed or
retyped before the level loads cleanly in MM9:

| LoMM class | Count | LoMM purpose | MM9 nearest match |
|---|---|---|---|
| `CandleWall` | 24 | wall candle prop with engine-driven flame | `CandleProp` or `WallTorch` |
| `Orc` | 17 | LoMM enemy AI; instances use `models\Goblin.abc` | `HalfOrcSoldier`, `LizardOrc`, or any MM9 monster |
| `GoodKingRescueZone` | 2 | LoMM multiplayer "rescue the king" volume | none — delete |
| `BuyZone` | 2 | LoMM Counter-Strike-style buy region | none — delete |
| `Timer` | 1 | LoMM scripted timer | none — delete |

The remaining 24 classes (`AIRail`, `Door`, `Prop`, `Light`,
`DirLight`, `AmbientSound`, `StartPoint`, `WorldObject`, `BlueWater`,
`OutsideDef`, `TreasureChest`, `Brazier`, `Fire`, etc.) are all
registered in MM9.

## Property Differences Within Shared Classes

The parser keys properties by name, so unknown properties are silently
dropped by the engine and missing properties fall back to engine
defaults. The differences that matter in practice:

- `StartPoint` is missing MM9's `MovePlayerToFloor`. Without it the
  player may spawn floating or below the floor. Always add this with
  value `1` during conversion.
- `WorldProperties` is missing MM9's `CanSaveGame` and
  `CanMiniSaveGame`. Without them the player cannot save in the level.
  Add both with value `1` during conversion.
- `WorldProperties` carries LoMM-only `MusicDirectory`,
  `InstrumentFiles`, `AmbientList`, `CruisingList`, `HarddrivingList`,
  `CDTrack`, and `ScenarioNbr`. They are harmless; MM9 ignores them.
- `Brazier` and `Fire` in LoMM use a single `Type` field instead of
  MM9's full `Fire*`/`Smoke*`/`Light*` particle parameter set. Without
  conversion, the fire and smoke effects do not render.
- `TreasureChest` in LoMM uses `KeyItemId` and `SpawnItem`. MM9
  expects `Random`, `Gold`, `GoldOnly`, `Item1`..`Item5`, `TrapLevel`,
  `TreasureLevel`, `TreasureOptions`, `TreasureType0_7`, plus AI
  reachability fields. Without conversion the chest opens but is
  empty.
- `Cow` in LoMM has `PickRandomWeapon`, `TeamNbr`, `WeaponItemNbr`
  (LoMM faction combat) and is missing the MM9 AI rail / wander /
  range-attack / repopulation stack. The cow loads but is inert.
- Most other shared classes (`Door`, `Prop`, `AIRail`, `AIBarrier`,
  `AmbientSound`, water variants, `WorldObject`, `Ladder`) are
  missing MM9's engine-level additions: `Alpha`, `BoxPhysics`,
  `DisableFog`, `NeedsTick`, `TouchNotify`, `ShouldMiniSave`,
  `OneTimeDamage`, `DamageAIOnly`, `DamagePlayerOnly`. All harmless —
  engine defaults apply.
- `StartPoint.PlayerNbr` in CHATEAUESCAPE is stored as the IEEE-754
  float bit pattern of the slot number (for example `1090519040 = 8.0`
  bits). MM9 reads `PlayerNbr` as raw `uint32`. MM9 is single-player
  so non-zero `PlayerNbr` values are usually irrelevant; the converter
  leaves them as-is.

## Asset Compatibility

`CHATEAUESCAPE.DAT` references 32 distinct `Filename` values and 29
distinct `Skin` values. Resolving them against MM9's `MODELS.REZ`
(case- and extension-insensitive) shows:

- 29 of 32 models exist in MM9. Missing: `Barrel02`, `Chest-Lacquer`,
  `Painting_Rectangle`, `Goblin`
- 24 of 29 skins exist. Missing: `Barrel02`, `Chest-Lacquer`,
  `HorseStatue2` (MM9 has `HorseStatue` without the `2`),
  `Painting_Rectangle5`, `Chest-Rusty01` (MM9 ships the matching
  model but under a different skin name), `Goblin`
- Ambient sound paths (`Sounds/Ambient/...`) were not verifiable against
  the unavailable `SOUNDS.REZ` / `DATA.REZ` in the test workspace, but
  missing sounds are silent rather than fatal.

The level references no custom `ScriptName` values, so no
`SCRIPTS.REZ` patching is needed for the level to load.

## Goblin ABC Preview Notes

`Goblin.abc` (`MODELS/GOBLIN`, 985,190 bytes, 17 parent animations,
68 nodes, 3 pieces, `nVerts = 602`, `nVertWeights = 1863`) is a valid
LithTech ABC. LoMM's Goblin uses true multi-weight vertex records, so
the editor must not walk its vertex array at the older fixed 48-byte
stride. The observed vertex record layout is:

- `uint16 n_weights`
- `uint16 weight_set_index_or_flags`
- `n_weights` entries of `uint32 bone_index`, `float x`, `float y`,
  `float z`, `float weight`
- `float x`, `float y`, `float z` saved model-space vertex position
- `float nx`, `float ny`, `float nz` saved model-space normal

Total record size is therefore `28 + 20 * n_weights`; single-weight
records remain 48 bytes. For static editor previews, multi-weight
characters should use the saved model-space position and normal instead
of reconstructing bind pose from the weight list. The weight list is
still useful for future animated/skinned preview work, but reconstructing
from the current node matrices is slightly wrong on Goblin and visibly
flattens details such as the head.

## Level Connectivity

`CHATEAUESCAPE.DAT` has 16 `StartPoint`s clustered around two arrival
areas and **0** `ExitTrigger`s. The level has no DAT-driven exit back
to the rest of the world. To wire it into MM9's world graph, add an
`ExitTrigger` whose `DestinationWorld` is the source MM9 level and add
a matching `StartPoint` there. The transition wizard described in the
"Level Transitions" section above applies.

## Converter Pipeline

`conversion/lomm_to_mm9.py` implements the findings above as a YAML-driven
pipeline (`conversion/lomm_to_mm9.yaml`). The root `lomm_to_mm9.py` remains a
compatibility launcher, while `conversion/lomm_to_mm9_service.py` provides the
shared install-root validation, LoMM level listing, conversion-to-bytes, and
transactional multi-archive insertion (updating `WORLDS.REZ`, `MODELS.REZ`,
`SKINS.REZ`, and `SOUNDS.REZ`) used by both the CLI and editor:

1. **Convert classes via templates.** Replaces instances of a class
   (including LoMM-only enemies like `Orc`) with a clone of a named MM9
   template. This stage runs first so retyped objects survive the
   unknown-class drop.
2. **Drop unknown classes.** Any WorldObject whose class is still not
   in MM9's catalog (and wasn't retyped in stage 1) is removed.
3. **Add missing properties** to shared classes (`StartPoint`,
   `WorldProperties`).
4. **Asset audit** walks every remaining object's `Filename` (`.abc`/`.lta`/`.ltb`),
   `Skin` (`.dtx`), and `AmbientSound` (`.wav`) properties and three-way classifies:
   in MM9, in LoMM only, or missing entirely. The "in LoMM only" bucket (which includes
   loose files and entries inside LoMM's `MODELS.REZ`, `SKINS.REZ`, and `SOUNDS.REZ`)
   is transactionally copied into MM9's respective archives (`MODELS.REZ`, `SKINS.REZ`,
   and `SOUNDS.REZ`) so the level renders and plays correctly.

The standalone CLI now takes install roots rather than loose/debug data paths:

```text
python lomm_to_mm9.py \
  --mm9_root "C:\Path\To\Might and Magic IX" \
  --lomm_root "C:\Path\To\Legends of Might and Magic" \
  --level_to_convert CHATEAUESCAPE \
  --converted_level_name CHATEAUESCAPE_MM9
```

Both roots are validated before conversion. MM9 requires `data/WORLDS.REZ`,
`data/RUDE.REZ`, and `data/SCRIPTS.REZ`; LoMM uses the same required archive
set. The requested source level must exist in LoMM `WORLDS.REZ`, and the
requested converted level name must not already exist in MM9 `WORLDS.REZ`
under either extensionless or `.DAT` lookup forms.

`_Mm9Catalog.load_level()` accepts the level path with or without the
`.DAT` suffix, so the loader transparently retries the alternate form
when the literal lookup fails.

The converter reuses `mm9_patcher.mm9_patch` so its output round-trips through
the MM9 parser. `RezWriter.add()` is used with a DAT restype inferred from the
converted payload magic, which is important because MM9 world entries are
usually extensionless (`WORLDS/BOOTCAMP`, not `WORLDS/BOOTCAMP.DAT`).

The editor exposes the same workflow through the dropdown
`Conversion -> LoMM to MM9` menu item. The dialog remembers the last successful
LoMM install folder in `editor_settings.json`, loads LoMM levels into a
combobox, confirms the live archive replacement, writes an automatic conversion
backup of all modified archives plus `install_manifest.json` and `conversion_log.txt`,
and opens the newly inserted MM9 level for inspection after success (triggering a
dynamic viewport cache refresh so copied models and skins render immediately).

A separate `catalog/data/catalog_lomm.json` can still be built from a LoMM
`WORLDS.REZ` for conversion-rule research, for example:

```text
python catalog.py build-from-rez "C:\Path\To\LoMM\data\WORLDS.REZ" \
  --out catalog/data/catalog_lomm.json
```

It mirrors `catalog/data/catalog.json`'s shape but indexes the LoMM levels.
It's useful when designing experimental conversion rules: each LoMM-only class
entry includes the `template` (source level + instance) you can clone from, the
union of `property_names` actually observed on instances, and the set of
`filenames` (model paths) the class uses across LoMM levels.


# Music

There are fundamental differences in how music is configured between LoMM and MM9:

- **Legends of Might and Magic (LoMM):** Level music configurations reside directly inside the level `.DAT` files on the `WorldProperties` object (via properties such as `MusicDirectory`, `InstrumentFiles`, `AmbientList`, `CruisingList`, `HarddrivingList`, `CDTrack`, and `ScenarioNbr`). However, in all shipping LoMM levels, these fields are left as empty strings, and the level relies entirely on placement of environmental `AmbientSound` objects.
- **Might and Magic IX (MM9):** Background level music is completely decoupled from the level `.DAT` files. Instead, the game engine uses a global lookup table in the game configuration file MAPSTATS.TXT (located in `data.rez/DATA/MAPSTATS.TXT`). This table maps the map's file name to a **Music Track** index and **Battle Track** index.

## How to Add Music to a Newly Converted Level

Since converted levels are not part of the original MM9 campaign, they lack an entry in MM9's MAPSTATS.TXT. Consequently, the game client plays no background music when loading them.

To assign music to a converted level:
1. Open the active MM9 game configuration file `data.rez/DATA/MAPSTATS.TXT`.
2. Add a new row mapping your converted level's filename to your desired track index. For example, if your converted level file name is `ChateauEscape_mm9`, append a new entry:
   ```text
   #  Name                 File name        Music Track   Battle Track   ...
   81 Chateau Escape       ChateauEscape_mm9    8             3              ...
   ```
   *(Track numbers typically range from 1 to 18; for example, track `8` is Thjorgard, track `7` is Sturmford, and track `12` is Drangheim).*

