# object.lto Creature Porting Notes

## Purpose

These notes capture the technical findings that matter for adding new creature
support to MM9, especially creatures ported from Legends of Might and Magic.

The important conclusion is that `object.lto` is necessary for real new class
work, but it is not a complete creature definition. It defines the runtime
object class tree and editable/default properties. Final creature behavior and
visuals also depend on actor data tables, assets, scripts, animations, sounds,
and possibly game-side C++ logic.

## Relevant Runtime Facts

DEdit and the game use `data/object.lto` as executable class metadata:

- `object.lto` is a 32-bit Windows PE module.
- It exports `ObjectDLLSetup`.
- `ObjectDLLSetup` returns the `ClassDef` tree and each class's `PropDef`
  metadata.
- DEdit builds its add-object class tree from these class definitions.
- DEdit hides classes marked `CF_HIDDEN`.
- New object defaults come from flattened inherited `PropDef` defaults, with
  child properties overriding parent properties.

For the editor, this means addable runtime classes should come from
`object.lto`, not from scanning shipped DAT files. DAT files remain useful for
observed examples, placement patterns, and real level metadata, but they are not
the authoritative class list.

## Creature Data Is Layered

An MM9 creature is not described by one file. At minimum, a usable creature may
touch these layers:

- `object.lto`: runtime class name, parent class, flags, properties, defaults.
- `ACTOR.TXT` / `MONSTERS.TXT`: actor row, model, primary skin, accessory skins,
  picture/type metadata, stats, combat settings, and related table data.
- `MODELS.REZ`: model files such as `.abc`.
- `SKINS.REZ`: texture files such as `.dtx`.
- `SOUNDS.REZ` / `DATA.REZ`: sound and table references, depending on creature.
- `SCRIPTS.REZ`: optional behavior scripts attached by world objects.
- World DAT entries: placed instances and per-instance property values.

The editor should keep these layers separate. `object.lto` should answer
"which class can exist and which properties does it have?" Actor tables should
answer "what does this actor look like and how is it configured as a creature?"

## Visual Resolution Findings

`object.lto` is not reliable as the final visual source for actors.

The Honk Accountant case is the clearest example:

- The placed object in `TEMPLEOFHONK.DAT` has class `Honk`.
- Its DAT `Filename` is a misleading placeholder, `models\Skeleton.abc`.
- The scripts `HONK.SCR`, `HONKACCOUNTANT.SCR`, and `HONKACCOUNTANT.INC` do not
  contain model, skin, row-number, or visual class remapping.
- The correct editor visual is `MONSTERS.TXT` row 217:
  `honkfemale.abc`, `honkf3.dtx`, and accessory skin `honkhat.dtx`.

So actor visuals must continue to resolve through `ACTOR.TXT` and
`MONSTERS.TXT`, with small documented data quirks when class/name context is
needed. `object.lto` visual-like defaults are useful as a fallback template
source, not as the primary actor visual authority.

Accessory skins matter. Rows such as Honks and Lizard-Orcs use `SkinName2` for
hats or weapons:

- Honk Accountant / row 217: `SkinName2 = honkhat.dtx`.
- Lizard-Orc Mage / row 191: `SkinName2 = LizOrcCutlass.dtx`.

Any new creature workflow must preserve `SkinName`, `SkinName2`, and
`SkinName3` so the viewport and game-facing data do not lose attachments.

The editor must not treat class/name visual quirks as proof of game behavior.
The LoMM Orc row-304 experiment showed this clearly:

- `DATA.REZ` contained new `ACTOR` / `MONSTERS` row 304 for `LoMM Orc`.
- The placed world object was class `LizardOrcMage`, name `LoMMOrc1`, and
  `Filename = models\OrcMM9.abc`.
- The editor preview selected row 304 through the `LoMMOrc*` visual quirk and
  rendered the LoMM Orc.
- In game, the same object rendered and behaved as the stock Lizard-Orc Mage.

So the game appears to bind existing actor classes to their known runtime table
row. For `LizardOrcMage`, that row is 191. A placed actor's DAT `Name` and
`Filename`, and an appended actor row with `BaseName = LizardOrcMage`, are not
enough to redirect the game to row 304.

For an existing MM9 class, a runtime visual/behavior change therefore requires
editing the row the class actually uses, not merely adding a new row. Adding a
new row is still useful for editor preview metadata and future class work, but
it is not by itself a game-visible creature variant.

## Existing Unplaced-Class Proof

`LizardOrcMage` proves that a valid class can exist in MM9 without appearing in
any shipped level:

- DAT observations: 0 shipped instances.
- `object.lto`: class exists and is DEdit-visible/runtime-loadable.
- Parent class: `LizardOrc`.
- Model-like default: `models\lizardorc.abc`.
- `MONSTERS.TXT` row 191:
  - `Monster Name = Lizard-Orc Mage`
  - `ModelName = lizardorc.abc`
  - `SkinName = LizardOrc.dtx`
  - `SkinName2 = LizOrcCutlass.dtx`
  - `Type/Picture = Lizard-Orc C`
  - `BaseName = LizardOrc`

A generated `LizardOrcMage` placement works visually in game. This is the best
current model for first-class editor support of classes that are valid in MM9
but absent from shipped worlds.

Important follow-up from the LoMM Orc tests:

- Stock `LizardOrcMage` selects row 191 at runtime.
- Replacing row 191 is the meaningful existing-class experiment.
- Appending row 304 while keeping class `LizardOrcMage` does not affect runtime
  selection.

## LoMM Creature Porting Levels

There are three increasingly difficult ways to bring LoMM creatures into MM9.

### 1. Replace/Reskin an Existing MM9 Class Row

This is the safest path.

Use an existing MM9 monster class whose behavior is close enough, then modify
the actor/monster table row that the game already uses for that class. Copy
LoMM assets into MM9 archives under the names referenced by that row.

Expected work:

- Pick an MM9 host class, such as `LizardOrc`, `HalfOrcSoldier`, or another
  compatible monster.
- Copy LoMM model, skin, and sound assets into MM9 archives.
- Edit the host class's existing actor/monster table row to point at the LoMM
  assets.
- Place the existing MM9 class.
- Verify both editor preview and in-game spawning.

This approach does not require changing `object.lto`, because the runtime class
already exists. It also does not produce a new independent creature. Every use
of that host row/class will become the replacement creature while the patch is
installed.

Known result:

- Replacing stock MM9 Dwarf model/skin archive entries with LoMM Dwarf assets
  worked in game after loose extracted archive folders were removed from the
  live `DATA` directory.

### 2. Add a New Variant Under an Existing MM9 Base Class

This is the first path that can produce a new independent creature variant.
It likely requires `object.lto` changes and possibly object-code changes.

The new class would inherit from a compatible MM9 class and mostly reuse its
behavior, while exposing a distinct class name for placement and table lookup.

Expected work:

- Add a new `ClassDef` to `object.lto`, probably inheriting from the closest
  existing monster class.
- Keep property definitions minimal and inherited where possible.
- Add actor/monster table rows for the new class or variant.
- Copy LoMM assets.
- Confirm DEdit/editor class visibility.
- Confirm the game selects the new row, not the inherited/parent row.
- Confirm in-game spawning.

This path depends on being able to rebuild or patch `object.lto` safely. It is
not yet proven that adding only a `ClassDef` is sufficient. The row lookup may
live in the class constructor or other server-side code inside `object.lto`, so
new-class work must validate the class-to-table-row binding explicitly.

### 3. Port a Completely New Creature Behavior

This is the hardest path.

If the LoMM creature relies on behavior that MM9 does not already have, then a
new class name and actor row are not enough. The game server must have behavior
code for that class.

Expected work may include:

- Rebuilding or patching `object.lto` with new server object code.
- Porting AI/combat/projectile logic from LoMM or recreating it using MM9
  systems.
- Adding or adapting scripts.
- Verifying animation names and state transitions expected by MM9 AI.
- Copying models, skins, sounds, and any referenced effects.
- Extending editor conversion rules so LoMM DAT objects can be retyped or kept.

This should be treated as engine/modding work, not just catalog work.

## object.lto Editing Requirements

Real new classes require the game and tools to agree on the class schema.

The practical requirement is one of:

- Build a compatible replacement `object.lto`.
- Patch the existing MM9 `object.lto` without breaking exports, class layout,
  or runtime assumptions.
- Avoid new classes and reuse existing MM9 classes instead.

Any replacement or patch must preserve:

- `ObjectDLLSetup` export behavior.
- Server object version compatibility.
- `ClassDef` / `PropDef` memory layout expected by the engine.
- Parent class chains.
- Class flags, especially hidden/runtime-loadable behavior.
- Property names, types, flags, and default values.

The editor can dump and consume `object.lto`, but consuming class metadata is
much easier than producing a safe replacement module.

## Asset Compatibility Notes

LoMM and MM9 share enough LithTech formats that many assets are promising, but
not every asset is automatically safe.

Known useful findings:

- LoMM and MM9 DAT level object/property formats are closely compatible.
- LoMM `.abc` character models can be valid MM9/LithTech assets.
- Some LoMM character models use multi-weight vertex records.
- The editor viewport may need model-parser support beyond old fixed-stride
  assumptions, even when the game runtime displays the model correctly.
- The LoMM Dwarf and MM9 Dwarf are structurally close enough that replacing the
  stock MM9 Dwarf model/skin archive entries with LoMM assets is a good
  compatibility smoke test.
- The LoMM Orc model can be loaded by the editor after animation-name patching,
  but the row-304 append experiment did not prove game compatibility because
  the game continued to use stock `LizardOrcMage` row 191.
- MM9's live `DATA` directory is mounted as a loose resource root by `rez.txt`.
  Extracted archive folders such as `DATA\WORLDS`, `DATA\MODELS`,
  `DATA\SKINS`, or `DATA\SCRIPTS` can shadow or interfere with patched REZ
  archives. Keep the live install clean when testing patches.

For each candidate creature, audit:

- model file loads in the editor and in game;
- all referenced skins exist after copy;
- accessory/weapon skins are preserved;
- animation names match what the MM9 class behavior expects;
- sounds referenced by the actor row or scripts exist;
- projectile/effect references exist, if the creature uses ranged attacks.

## Candidate Implementation Plan

1. Keep existing-class replacement as the baseline smoke test.
   - Pick one LoMM creature with compatible MM9 behavior.
   - Copy its model/skin/sound assets into MM9 archives through the existing
     transactional archive flow.
   - Modify the existing actor/monster row used by the host class, or replace
     the exact asset names referenced by that row.
   - Place the host MM9 class and verify editor preview plus in-game spawning.
   - Treat appended rows as metadata only unless a new runtime class selects
     them.

2. Add a creature import/audit command.
   - Input: LoMM install, creature asset names, target MM9 class, target table
     row strategy.
   - Output: asset-copy plan, missing-asset report, suggested actor row,
     suggested catalog/visual mapping, and validation checklist.
   - Report whether the strategy is a true runtime replacement, editor-only
     preview mapping, or experimental new-class mapping.

3. Keep actor-table patching reversible and explicit.
   - Read and write `ACTOR.TXT` / `MONSTERS.TXT` from `DATA.REZ`.
   - Preserve table formatting where possible.
   - Write patched `DATA.REZ` into an output batch, never directly into the live
     install.
   - Include restore-compatible install manifests.
   - Record source row, target row, and whether the row is known to be selected
     by the runtime class.

4. Add optional visual mapping rules.
   - Support explicit class/name/script-to-row mappings for imported variants.
   - Keep quirks data-driven and commented with source rows.
   - Preserve primary and accessory skins.
   - Mark mappings that are editor-preview-only so they are not mistaken for
     game runtime behavior.

5. Attempt a new `object.lto` class only after the replacement path is stable.
   - Choose a variant that can inherit from an existing MM9 monster.
   - Produce a minimal experimental class definition.
   - Validate the dumped class list before placing it.
   - Verify which actor/monster row the new runtime class selects.
   - Place it in a throwaway test level.
   - Run an in-game smoke test from a temporary patched install.

## Validation Checklist

For every imported creature:

- The class appears in the editor only if it is visible and runtime-loadable.
- Placement template properties come from `object.lto` or a deliberate host
  template, not from an unrelated DAT object.
- Actor visual resolution selects the intended table row.
- `SkinName`, `SkinName2`, and `SkinName3` are preserved.
- Model and skins render in the editor viewport.
- The patched archives install and restore cleanly.
- A fresh in-game test level can spawn the creature.
- The creature idles, moves, attacks, takes damage, dies, and plays sounds.
- Save/load around the test level does not break the creature instance.

## Recommended Next Step

Use existing-class replacement, not appended-row variants, for the next game
smoke tests. For the LoMM Orc, the meaningful test is replacing row 191 or the
row-191 asset references used by `LizardOrcMage`. If that works, the remaining
problem is not asset compatibility; it is creating a new runtime class that can
select a new actor/monster row without sacrificing the stock class.
