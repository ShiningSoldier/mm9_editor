# Release Validation

**Status: Contributor reference**

Use validation proportional to the feature and preserve external evidence for
game-bound geometry or behavior changes.

## Documentation

- `python tools/check_docs.py` passes.
- The root README and documentation index point to maintained guides.
- Menu labels and shortcuts match `app/editor.py`.
- Documented CLI options match `--help`.
- No maintained guide treats a research finding as supported behavior.

## Output safety

- Ordinary Save writes only below the selected output root.
- The manifest lists every changed archive and loose file.
- Installation creates a backup and touches only manifested targets.
- Restore is tested against a disposable installation or controlled fixture.

## World and object changes

- Saved DAT reopens through the maintained parser.
- Property names, codes, and reference targets retain intended values.
- Viewport display-space transforms serialize to correct game-space values.
- Validate through a fresh load when old saves can restore runtime object state.

## Prefabs and moving BSP

- Every imported component has an explicit runtime representation.
- Generated preview BSP does not pass game-bound validation.
- Controller/BSP names, ownership, links, pivots, and transforms survive reopen.
- Test rendering, collision, movement, sound, and scripted behavior as
  applicable.

## Conversion output

- glTF-to-ED reports have no blockers and the ED round-trips.
- DAT-to-ED results stay within budgets and retain source provenance.
- DEDit/Processor checks use the intended LithTech 2.1 MM9 project.
- A compiled DAT's structure, rendering, collision, spawn, and important routes
  pass a fresh game test before release.

## LoMM conversion

- Every unsupported actor is removed, replaced, or explicitly accepted as a
  blocking incompatibility.
- Missing assets and substitutions are reviewed.
- Transitions, StartPoints, treasure, effects, sounds, and scripts are checked.
- Editor preview is not used as proof of MM9 runtime-class compatibility.

