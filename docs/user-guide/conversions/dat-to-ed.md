# Reconstruct DAT as DEDit ED

**Status: Experimental**

Choose **Conversion → DAT to ED (Experimental)…** to reconstruct an MM9 v66
compiled world as an ED v1249 source world. This is not a lossless decompiler:
compiled BSP polygons do not preserve the original CSG brush structure.

The intended validation path is:

```text
DAT/WORLDS.REZ → reconstructed ED → DEDit 2.1 → Processor → DAT
→ fresh MM9 rendering/collision/behavior test
```

Opening in DEDit or producing a DAT is not proof that the result is playable.

## Output

The workflow writes the reconstructed ED plus a text report, selection report,
and acceptance manifest. Additional geometry/source-coverage diagnostics may be
written for levels that have applicable evidence.

The acceptance manifest is the durable handoff artifact. It records selected
models, generated counts, object coverage, cautions, blockers, and external
validation fields.

## Safety boundaries

- Generation uses practical ceilings of 1,500 Brushes and 12,000 surfaces.
- Over-budget output is diagnostic and blocked from game-bound acceptance.
- Helper textures are semantic evidence and are excluded from ordinary visible
  Brush output unless a specific diagnostic explicitly requests them.
- Terrain and PhysicsBSP reconstruction are approximations of compiled data.
- Source ED data can be used as an oracle when available, but is not assumed to
  exist in a clone or game installation.
- Generated ED must be structurally clean on its first Processor run; a manual
  DEDit save is not a required normalization step.

Indoor PhysicsBSP shells, stairs, door approaches, portals, sky helpers, and
collision-sensitive routes require focused testing. A level that works is not
evidence that every world shape is supported.

## Acceptance checklist

1. Review every blocker and caution in the acceptance manifest.
2. Open the ED in LithTech 2.1 DEDit without a repair prompt.
3. Run Processor with the correct MM9 project and retain its complete log.
4. Validate that the compiled DAT is v66 and can be parsed by the editor.
5. Install only through a reversible output/backup workflow.
6. Test a fresh game load for visible geometry, collision, StartPoints, doors,
   transitions, helper behavior, and the routes important to that level.

The stable generation rules are in the
[DAT-to-ED contract](../../reference/conversion-contracts/dat-to-ed.md). The
level-specific ANSKRAMKEEP and BOOTCAMP experiments are retained only in the
[research archive](../../research/archived-plans/dat-to-ed-investigation.md).

