# Testing

**Status: Contributor reference**

Run commands from the repository root.

## Default suite

```powershell
python -m unittest discover -s tests
```

The default suite covers deterministic parsers, writers, project behavior, UI
logic, small fixtures, and supported feature paths. It must not require a local
game installation, DEDit, Processor, or another repository unless a test is
explicitly an integration test and skipped by default.

## Investigation tier

Large real-world DAT reconstruction, terrain, PhysicsBSP, and compiler-strategy
tests are opt-in:

```powershell
$env:MM9_RUN_INVESTIGATION_TESTS = "1"
try {
  python -m unittest discover -s tests
} finally {
  Remove-Item Env:MM9_RUN_INVESTIGATION_TESTS -ErrorAction SilentlyContinue
}
```

The legacy `MM9_RUN_SLOW_DAT_TO_ED_TESTS=1` flag remains recognized for
compatibility. Use these tiers only when a change affects their evidence or for
an explicit release-validation run.

## Prefab corpus audit

The shipped DEdit prefab corpus is external to the repository. When it is
available through the selected game/tool project, run:

```powershell
python tools/audit_prefab_corpus.py --game-root "<mm9-root>"
```

`--include-all-bsp` enables the slower all-assembly investigation pass. Keep
corpus counts in generated reports or historical baselines, not as timeless
claims in user guides.

## Documentation checks

Run:

```powershell
python tools/check_docs.py
```

The audit checks local Markdown links, repository file references, and
workstation-specific absolute paths. Research notes may describe virtual game
paths, but no document may depend on a contributor's drive layout.

## Command smoke checks

When updating CLI documentation, confirm the maintained parsers still load:

```powershell
python mm9_editor.py --help
python mm9_rezmgr.py --help
python catalog.py --help
python lomm_to_mm9.py --help
python -m features.model_conversion.abc_gltf_export --help
python -m features.dat_editing.gltf_to_ed_cli --help
python -m features.dat_editing.gltf_to_ed_validation_cli --help
```

