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

## LoMM Creature Porting Levels

There are three increasingly difficult ways to bring LoMM creatures into MM9.

### 1. Reuse an Existing MM9 Class

This is the safest path.

Use an existing MM9 monster class whose behavior is close enough, then override
or extend its actor-table visual data and copy LoMM assets into MM9 archives.

Expected work:

- Pick an MM9 host class, such as `LizardOrc`, `HalfOrcSoldier`, or another
  compatible monster.
- Copy LoMM model, skin, and sound assets into MM9 archives.
- Add or edit actor/monster table rows to point at the LoMM assets.
- Place the existing MM9 class with the new row/visual mapping.
- Add documented visual quirks only if class/name/table context cannot resolve
  the intended row automatically.

This approach does not require changing `object.lto`, because the runtime class
already exists.

### 2. Add a New Variant Under an Existing MM9 Base Class

This is plausible but requires `object.lto` changes.

The new class would inherit from a compatible MM9 class and mostly reuse its
behavior, while exposing a distinct class name for placement and table lookup.

Expected work:

- Add a new `ClassDef` to `object.lto`, probably inheriting from the closest
  existing monster class.
- Keep property definitions minimal and inherited where possible.
- Add actor/monster table rows for the new class or variant.
- Copy LoMM assets.
- Confirm DEdit/editor class visibility and in-game spawning.

This path depends on being able to rebuild or patch `object.lto` safely.

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

For each candidate creature, audit:

- model file loads in the editor and in game;
- all referenced skins exist after copy;
- accessory/weapon skins are preserved;
- animation names match what the MM9 class behavior expects;
- sounds referenced by the actor row or scripts exist;
- projectile/effect references exist, if the creature uses ranged attacks.

## Candidate Implementation Plan

1. Start with existing-class creature reskins.
   - Pick one LoMM creature with compatible MM9 behavior.
   - Copy its model/skin/sound assets into MM9 archives through the existing
     transactional archive flow.
   - Add or modify actor-table data in a reversible output patch.
   - Place it as an existing MM9 class and verify editor preview plus in-game
     spawning.

2. Add a creature import/audit command.
   - Input: LoMM install, creature asset names, target MM9 class, target table
     row strategy.
   - Output: asset-copy plan, missing-asset report, suggested actor row,
     suggested catalog/visual mapping, and validation checklist.

3. Make actor-table patching first-class.
   - Read and write `ACTOR.TXT` / `MONSTERS.TXT` from `DATA.REZ`.
   - Preserve table formatting where possible.
   - Write patched `DATA.REZ` into an output batch, never directly into the live
     install.
   - Include restore-compatible install manifests.

4. Add optional visual mapping rules.
   - Support explicit class/name/script-to-row mappings for imported variants.
   - Keep quirks data-driven and commented with source rows.
   - Preserve primary and accessory skins.

5. Only then attempt a new `object.lto` class.
   - Choose a variant that can inherit from an existing MM9 monster.
   - Produce a minimal experimental class definition.
   - Validate the dumped class list before placing it.
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

Do not begin with a new `object.lto` class. Begin with a LoMM creature that can
reuse an existing MM9 class, and build the missing actor-table patching and
asset-audit workflow around that. Once that path is boring and reversible, use
the same validation harness for experimental `object.lto` class additions.
