# Static ABC Model Conversion

`features.model_conversion` exports extracted LithTech `.ABC` model geometry
to Blender-compatible glTF 2.0. The exporter intentionally emits only the
highest-detail LOD as a static mesh. Supported character models are baked to a
usable static pose by default.

The static exporter does not emit armatures, animations, sockets, child
models, or additional LODs. Source nodes and animation blocks remain untouched
in the ABC file and are not represented in the glTF output.

## Explicit skins

Repeat `--skin` in ABC piece order, or use `PIECE=PATH` assignments. Named
assignments are preferred for multi-piece character models:

```powershell
python -m features.model_conversion.abc_gltf_export `
  C:\lithtech\mm9_editor\mm9_data\MODELS\GUARD.ABC `
  C:\lithtech\mm9_editor\output `
  --base-name Guard_C `
  --glb `
  --skin guard=C:\lithtech\mm9_editor\mm9_data\SKINS\GUARD3.DTX `
  --skin pole=C:\lithtech\mm9_editor\mm9_data\SKINS\GUARDPOLE2.DTX
```

A single unqualified skin is still broadcast to every piece for compatibility
with the former CLI, but the exporter prints a warning. Pass
`--broadcast-skin` to state that choice explicitly.

## Catalog variants

`--all-variants` reads `catalog.json`, matches the game-neutral
`model_variants` table to the ABC path, combines body and accessory skins, and
removes duplicate aliases. Older MM9 catalogs remain supported through their
`actor_visuals` records.
The resulting material sets are stored in one GLB through
`KHR_materials_variants`. Blender 4.2 and later can display these variants from
its glTF Variants UI.

```powershell
python -m features.model_conversion.abc_gltf_export `
  C:\lithtech\mm9_editor\mm9_data\MODELS\GUARD.ABC `
  C:\lithtech\mm9_editor\output `
  --base-name Guard `
  --glb `
  --all-variants `
  --skins-root C:\lithtech\mm9_editor\mm9_data\SKINS `
  --catalog C:\lithtech\mm9_editor\catalog\data\catalog.json
```

For `GUARD.ABC`, this exports the three game-defined combinations:

| Variant | `guard` | `pole` |
|---|---|---|
| Guard A | `GUARD1.DTX` | `GUARDPOLE.DTX` |
| Guard B | `GUARD2.DTX` | `GUARDPOLE.DTX` |
| Guard C | `GUARD3.DTX` | `GUARDPOLE2.DTX` |

LoMM catalogs use `object.lto`, level properties, complete model/skin resource
inventories, and conservative resource-name matching. Starting the editor with
`--lomm-root` builds a missing catalog automatically. To build one manually and
export all Goblin appearances:

```powershell
python catalog.py build-from-rez `
  C:\lithtech\mm9_editor\lomm_data\worlds.rez `
  --object-lto C:\lithtech\mm9_editor\lomm_data\object.lto `
  --out C:\lithtech\mm9_editor\catalog\data\catalog_lomm.json

python -m features.model_conversion.abc_gltf_export `
  C:\lithtech\mm9_editor\lomm_data\MODELS\GOBLIN.ABC `
  C:\lithtech\mm9_editor\output `
  --base-name Goblin `
  --glb `
  --all-variants `
  --skins-root C:\lithtech\mm9_editor\lomm_data\SKINS `
  --catalog C:\lithtech\mm9_editor\catalog\data\catalog_lomm.json
```

When `SKINS.REZ` or an extracted `SKINS` directory is beside `worlds.rez`, the
catalog builder indexes it automatically. Use `--skins-rez` or `--skins-dir`
to select a different location.

`--glb` embeds geometry and PNG-converted skins in one self-contained file.
Without it, the command writes `.gltf`, `.bin`, and external `.png` files.
DTX textures with an unused all-zero alpha channel are made opaque; useful
alpha is preserved and mapped to glTF `MASK` or `BLEND` materials.

## Other commands

Convert one DTX texture to PNG:

```powershell
python -m features.model_conversion.dtx_png_export INPUT.DTX OUTPUT_DIR
```

Export geometry-only OBJ/MTL with placeholder materials:

```powershell
python -m features.model_conversion.abc_obj_export INPUT.ABC OUTPUT_DIR
```

Use `--unit-scale` to apply an explicit positive scale to positions. The
default `1.0` preserves raw MM9 model units. Coordinates remain Y-up and
right-handed; Blender performs its normal glTF Y-up to Blender Z-up import
conversion.
