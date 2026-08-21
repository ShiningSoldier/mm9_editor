# Model and Texture Export

**Status: Supported CLI**

The model-conversion tools operate on extracted LithTech `.ABC` and `.DTX`
files. A fresh clone does not include an `mm9_data` or `lomm_data` extraction.
Extract only the resources you need from the legally installed game archives.

## Extract a resource

List an archive to discover its virtual entry names, then extract the selected
entry to a working directory:

```powershell
python mm9_rezmgr.py list "<game-root>\data\MODELS.REZ"
python mm9_rezmgr.py extract `
  "<game-root>\data\MODELS.REZ" `
  "MODELS/BANDIT.ABC" `
  "<work-dir>\BANDIT.ABC"
```

Use the entry name printed by `list`; do not assume that every archive entry
stores a filename extension.

## Export ABC to glTF or GLB

```powershell
python -m features.model_conversion.abc_gltf_export `
  "<work-dir>\BANDIT.ABC" `
  "<output-dir>" `
  --base-name Bandit `
  --glb
```

The exporter emits the highest-detail LOD as a static mesh. Supported character
models are baked to a useful static pose by default. Armatures, animations,
sockets, child models, and additional LODs are not exported.

Repeat `--skin` in ABC piece order, or prefer `PIECE=PATH` assignments:

```powershell
python -m features.model_conversion.abc_gltf_export `
  "<work-dir>\MODEL.ABC" `
  "<output-dir>" `
  --base-name Model_Variant `
  --glb `
  --skin "<piece-name>=<work-dir>\PRIMARY.DTX" `
  --skin "<accessory-piece>=<work-dir>\ACCESSORY.DTX"
```

A single unnamed skin is broadcast for compatibility and produces a warning;
use `--broadcast-skin` to make that choice explicit.

## Export catalog variants

`--all-variants` uses `model_variants` from a catalog, combines primary and
accessory skins, and removes duplicate aliases. Supply an extracted skins root
and the catalog path:

```powershell
python -m features.model_conversion.abc_gltf_export `
  "<work-dir>\MODEL.ABC" `
  "<output-dir>" `
  --base-name Model `
  --glb `
  --all-variants `
  --skins-root "<work-dir>\SKINS" `
  --catalog "catalog\data\catalog.json"
```

The resulting GLB uses `KHR_materials_variants`. Without `--glb`, the exporter
writes `.gltf`, `.bin`, and external PNG files. Useful DTX alpha is preserved;
an unused all-zero alpha channel is made opaque.

Starting the editor with a valid `--lomm-root` creates a missing LoMM catalog.
For manual catalog generation, run:

```powershell
python catalog.py build-lomm "<lomm-root>" `
  --out "catalog\data\catalog_lomm.json"
```

## Other exporters

```powershell
python -m features.model_conversion.dtx_png_export `
  "<work-dir>\TEXTURE.DTX" "<output-dir>"

python -m features.model_conversion.abc_obj_export `
  "<work-dir>\MODEL.ABC" "<output-dir>"
```

`--unit-scale` applies an explicit positive scale to exported positions. The
default `1.0` preserves raw model units. Output remains Y-up and right-handed.
