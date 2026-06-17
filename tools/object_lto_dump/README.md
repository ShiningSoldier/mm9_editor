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
.\tools\object_lto_dump\bin\object_lto_dump.exe "C:\Program Files (x86)\GOG Galaxy\Games\Might and Magic 9\data\object.lto" > object_lto_dump.json
```

The JSON schema is `mm9_editor.object_lto_dump.v1`. Each class includes:

- `name`, `parent`, and `hierarchy`
- raw class `flags`, decoded `flag_names`, `hidden_in_dedit`, and
  `runtime_loadable`
- `declared_properties`
- flattened `properties`

Each property includes:

- `name` and `source_class`
- raw `type_id`, decoded `type`, raw `flags`, decoded `flag_names`, and `group`
- `hidden_in_dedit`
- typed `default_value`
- `default_raw` with vector, float, and string fields preserved
