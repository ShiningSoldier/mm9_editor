# object_lto_dump

`object_lto_dump.exe` is a small 32-bit Windows helper for dumping LithTech
`object.lto` class metadata as JSON.

It mirrors DEdit's class-metadata loading path:

1. Load `object.lto`.
2. Resolve and call `ObjectDLLSetup`.
3. Walk each `ClassDef`.
4. Flatten inherited `PropDef` entries from base to child, with child
   overrides replacing parent properties of the same name.
5. Move hidden properties and grouped non-owner properties to the end of the
   flattened list, matching DEdit's `BuildClassDefPropList()` ordering.

Build:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\object_lto_dump\build.ps1
```

Usage:

```powershell
$mm9Root = "C:\Path\To\Might and Magic IX"
.\tools\object_lto_dump\bin\object_lto_dump.exe "$mm9Root\data\object.lto" > object_lto_dump.json
```

The helper tools no longer assume local install paths. Pass the MM9 and LoMM
install roots explicitly:

```powershell
$mm9Root = "C:\Path\To\Might and Magic IX"
$lommRoot = "C:\Path\To\Legends of Might and Magic"

.\tools\object_lto_dump\validate_dump.ps1 -ObjectLto "$mm9Root\data\object.lto"

.\tools\object_lto_patch\build_lomm_orc_object_lto.ps1 `
    -MM9Root $mm9Root

python .\tools\lomm_orc_asset_batch.py `
    --mm9-root $mm9Root `
    --lomm-root $lommRoot

python .\tools\lomm_orc_runtime_batch.py `
    --mm9-root $mm9Root
```

The JSON schema is `mm9_editor.object_lto_dump.v1`. Each class includes:

- `name`, `parent`, and `hierarchy`
- raw class `flags`, decoded `flag_names`, `hidden_in_dedit`, and
  `runtime_loadable`
- `abi` metadata with per-pointer module names and RVAs for the `ClassDef`,
  class-name string, parent `ClassDef`, constructor, destructor, and plugin /
  message callback pointer
- `declared_properties`
- flattened `properties`

Each property includes:

- `name` and `source_class`
- raw `type_id`, decoded `type`, raw `flags`, decoded `flag_names`, and `group`
- `hidden_in_dedit`
- typed `default_value`
- `default_raw` with vector, float, and string fields preserved
