# MM9 Mod Editor Documentation

This index separates supported workflows from technical reference and
historical research.

## Support labels

- **Supported**: available through the current editor or a maintained CLI.
- **Experimental**: available, but output requires additional validation and
  may not be suitable for a real mod.
- **Reference**: stable format or architecture information for contributors.
- **Research**: observations, completed plans, and proposals. Research files
  are not feature promises or user instructions.

## User guides

- [Editor workflow](user-guide/editor-workflow.md) — open, edit, preview, save,
  install, restore, and persist a project.
- [Viewport](user-guide/viewport.md) — selection, camera, transforms, helpers,
  and undo/redo.
- [Prefab import](user-guide/prefab-import.md) — supported representations and
  runtime-safety boundaries.
- [Dialogue and quests](user-guide/dialogue-and-quests.md) — RUDE resources,
  quest keys, and dialogue-script integration.
- [Model export](user-guide/model-export.md) — ABC/DTX export to glTF, GLB,
  OBJ, and PNG.
- [LoMM to MM9](user-guide/conversions/lomm-to-mm9.md) — preservation-first
  level conversion.
- [glTF/GLB to ED](user-guide/conversions/gltf-to-ed.md) — static mesh to DEDit
  source conversion.
- [DAT to ED](user-guide/conversions/dat-to-ed.md) — experimental compiled-world
  reconstruction.

## Reference

- [Game resources](reference/game-resources.md)
- [World data](reference/world-data.md)
- [RUDE format](reference/rude-format.md)
- [glTF-to-ED contract](reference/conversion-contracts/gltf-to-ed.md)
- [DAT-to-ED contract](reference/conversion-contracts/dat-to-ed.md)

## Contributor documentation

- [Architecture](development/architecture.md)
- [Testing](development/testing.md)
- [Release validation](development/release-validation.md)

## Research archive

The [research index](research/README.md) contains reverse-engineering notes,
completed implementation plans, and proposals. It is intentionally excluded
from the supported product documentation.

