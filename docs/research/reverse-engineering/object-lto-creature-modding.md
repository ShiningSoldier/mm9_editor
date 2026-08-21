# LoMM Creature Modding Plan

> **Experimental reverse engineering — unsupported.** This document discusses
> patching native MM9 runtime inputs, including `object.lto`. The normal editor
> does not provide this workflow. Findings are retained for research and must
> not be treated as a safe modding recipe without independent runtime testing
> and reversible backups.

## Scope

This document captures the technical findings and implementation plan for the
`new object.lto class + safe runtime actor row + script-selected visuals` path.

This is no longer just an `mm9_editor` catalog feature. It is a modding
workflow that creates a patched runtime package for Might and Magic IX. The
editor should help build, audit, and install that package, but the package
itself changes game runtime inputs such as `object.lto`, `DATA.REZ`,
`MODELS.REZ`, `SKINS.REZ`, and possibly `SCRIPTS.REZ` / `SOUNDS.REZ`.

## Core Technical Findings

`object.lto` is the authoritative runtime class source:

- It is a 32-bit Windows PE module exporting `ObjectDLLSetup`.
- `ObjectDLLSetup` exposes the `ClassDef` tree and `PropDef` metadata used by
  DEdit and by MM9 runtime object creation.
- DEdit builds its visible object class tree from these class definitions and
  filters hidden classes by class flags.
- Add-object defaults come from flattened inherited `PropDef` defaults, with
  child overrides winning.

Creature behavior and visuals are layered:

- `object.lto` defines runtime class names, parent chains, flags, properties,
  defaults, and constructors.
- `ACTOR.TXT` / `MONSTERS.TXT` define actor rows: model, skins, stats, scripts,
  sound references, combat data, and creature metadata.
- `MODELS.REZ`, `SKINS.REZ`, and `SOUNDS.REZ` provide referenced assets.
- `SCRIPTS.REZ` can run logic that changes model and skin at spawn time.
- World DAT files only contain placed instances and per-instance properties.

Actor-table rows are selected by runtime class constructors, not by appended
table names alone:

- Appending a new row to `ACTOR.TXT` / `MONSTERS.TXT` does not make an existing
  class use that row.
- Stock classes have native constructor code that selects a runtime actor ID.
- `LizardOrcMage` selects row/runtime actor ID 191.
- A wrapper class that only inherits the stock constructor keeps the stock row.
- A patched wrapper constructor can call the shared actor-row constructor with a
  chosen runtime actor ID, then install the expected parent vtable.

Arbitrary new runtime actor IDs are not safe:

- Row 304 displayed Forad Darre because stock `ForadDarre` already selects
  runtime actor ID 304.
- A truly appended actor ID such as 306 crashed on BOOTCAMP load, which suggests
  fixed internal actor-definition tables or bounds in the game executable.
- Runtime actor IDs must be treated as scarce, pre-existing slots unless the
  executable-side actor table limits are understood and patched.

The current safe baseline is row/runtime actor ID 121:

- Row 120 is `Dwarven Guard` and has shipped instances, so it should stay stock.
- Rows 121 and 122 are present in the stock actor tables but their corresponding
  stock classes are not observed in shipped DAT files.
- `DwarvenSoldier` / `DwarvenCommander` class constructors appear to reuse row
  120, so stock placement does not naturally select rows 121 / 122.
- A new wrapper class can explicitly select row 121 while inheriting suitable
  behavior from a compatible class such as `LizardOrcMage`.
- This makes row 121 a reasonable first sacrificial slot for LoMM Orc testing.

## Visual Resolution Findings

`object.lto` is not the final visual authority for actors. The editor and mod
tools must keep resolving creature visuals from `ACTOR.TXT` / `MONSTERS.TXT`
unless an explicit preview-only mapping says otherwise.

Known examples:

- Honk Accountant is placed as class `Honk`, but its correct visual is
  `MONSTERS.TXT` row 217: `honkfemale.abc`, `honkf3.dtx`, and accessory skin
  `honkhat.dtx`.
- Lizard-Orc Mage row 191 uses `SkinName2 = LizOrcCutlass.dtx`.
- LoMM Orc model import required animation-name compatibility work before it
  could render correctly in MM9.

Accessory and secondary skins must be preserved:

- `SkinName` is the primary skin.
- `SkinName2` and `SkinName3` can be required for hats, weapons, or other model
  parts.
- Any actor-row patcher, visual preview mapping, or script-selected visual rule
  must carry all available skin fields forward.

## Script-Selected Visuals

MM9 scripts can set visuals at runtime.

Useful script evidence:

- `SCRIPTS\EBORACONCUBINE.SCR` calls `SetModelFilenames` before `BaseInit`.
- `AICOMMON.INC` uses `GetClassName`, so scripts can inspect the current
  object's class name.
- Base combat scripts such as `BASE.SCR`, `BASERANGE.SCR`, and `BASE2.SCR`
  centralize common enemy initialization.

Feasible design:

- Give each imported creature a real `object.lto` class such as
  `LoMMOrcMage`.
- Bind that class to a safe runtime actor row such as 121.
- Put generic stats, behavior family, and script selection in the sacrificed
  actor row.
- Use script logic to select the final model and skin from class name, object
  name, script name, or script parameters.

Preferred script structure:

- Add a small include such as `LOMMVISUALS.INC`.
- Implement `LoMMVisualInit`, which checks the current class or configured
  variant key and calls `SetModelFilenames`.
- Call `LoMMVisualInit` before `BaseInit` in the relevant LoMM wrapper script,
  or in a minimal shared base-script hook if global patching is acceptable.
- Keep the visual map data-driven and commented with source game, source asset,
  target runtime row, and whether the mapping is editor-preview-only or
  game-runtime behavior.

Use dedicated wrapper scripts when possible:

- A dedicated script such as `LOMM_BASERANGE.SCR` limits risk to imported
  creatures.
- Patching common scripts such as `BASE.SCR` / `BASERANGE.SCR` affects many
  stock monsters and should be reserved for a carefully tested shared mod layer.

## Known Limitations

Script-selected visuals do not remove the need for a valid runtime actor row.
The game still needs a constructor-selected actor ID that it can load safely.

Rows reused by this approach are sacrificed runtime slots:

- The selected row's stock creature becomes unavailable or changed while the mod
  is installed.
- Use rows with no shipped DAT instances first.
- Avoid spell-created or engine-special rows. `PhantomFighter` is not a good
  slot because it is referenced by spell data.

One row may not be enough for all imported creatures:

- Creatures sharing row 121 will share baseline stats and behavior unless
  scripts or additional safe rows override enough data.
- A practical mod may need one safe row per behavior/stat family.

`SetModelFilenames` is confirmed useful for model and primary skin selection,
but secondary/accessory skin handling is still uncertain:

- If `SetModelFilenames` cannot set `SkinName2` / `SkinName3`, accessory parts
  may need actor-row data, model changes, `AttachProp`, or another script API.
- Each imported creature must be audited in the editor and in game.

Assets must be MM9-compatible:

- ABC model animation names must match the behavior script and engine
  expectations.
- Texture names and paths must match the row/script references.
- Sounds, missiles, spells, and effects need separate compatibility checks.

Loose extracted archive folders can shadow REZ contents:

- The game root `rez.txt` includes a final loose `DATA` root.
- Extracted folders such as `DATA\WORLDS` can override `WORLDS.REZ` and make
  testing misleading.
- Keep the live install free of stale extracted archive folders during smoke
  tests.

## Implementation Plan

1. Keep the current LoMM Orc replacement as the baseline smoke test.

   - Copy the patched LoMM Orc model and required skins into output
     `MODELS.REZ` / `SKINS.REZ`.
   - Patch `DATA.REZ` row 121 to describe the LoMM Orc behavior/visual baseline.
   - Leave stock rows 120 and 191 intact.
   - Treat row 121 as a reversible, documented sacrificial slot.

2. Produce a patched `object.lto` with a real imported class.

   - Preserve `ObjectDLLSetup`, server object version, class layout, parent
     chains, class flags, and property metadata.
   - Add `LoMMOrcMage` as a visible/runtime-loadable child of a compatible MM9
     monster class such as `LizardOrcMage`.
   - Implement a wrapper constructor that selects runtime actor ID 121 and then
     installs the compatible parent vtable.
   - Write the patched module only into an output batch, never directly into the
     live install.
   - Validate the dumped class list before placing the class in a test level.

3. Add script-selected visual support.

   - Create `LOMMVISUALS.INC` with a data-driven class-to-visual map.
   - Create dedicated LoMM wrapper scripts first, for example
     `LOMM_BASERANGE.SCR`, so stock base scripts remain untouched.
   - For `LoMMOrcMage`, call the visual initializer before common AI init.
   - Start with model and primary skin through `SetModelFilenames`.
   - Investigate and document the correct path for accessory skins.

4. Extend the mod build/audit command.

   - Inputs: source LoMM install, creature asset names, target MM9 behavior
     class, target runtime row, script visual mapping, and output batch path.
   - Outputs: patched `object.lto`, patched `DATA.REZ`, copied assets, optional
     script files, install manifest, restore manifest, and validation report.
   - Report whether each mapping is true runtime behavior, editor-preview-only,
     or experimental.
   - Record source row, target row, constructor-selected runtime actor ID, and
     sacrificed stock creature.

5. Validate in layers.

   - Dump patched `object.lto` and confirm `LoMMOrcMage` is visible and has the
     expected parent/default properties.
   - Confirm row 121 is patched and rows 120 / 191 remain stock.
   - Place `LoMMOrcMage` in a throwaway test level.
   - Confirm editor preview uses the intended visual mapping.
   - Install the output batch into a clean MM9 test install.
   - Confirm the level loads, the actor appears as the LoMM creature, and combat
     behavior matches the selected MM9 behavior family.
   - Repeat with loose extracted archive folders removed or renamed.

## Validation Checklist

- `object.lto` exports `ObjectDLLSetup`.
- Dumped class list includes visible `LoMMOrcMage`.
- `LoMMOrcMage` parent chain and inherited properties match expectations.
- Constructor-selected runtime actor ID is 121.
- `DATA.REZ` row 121 contains the intended LoMM baseline row.
- Stock row 120 remains Dwarven Guard.
- Stock row 191 remains Lizard-Orc Mage.
- Required model and skin assets exist in the output archives.
- Script visual mapping runs before common AI initialization.
- Editor preview and in-game rendering agree for primary model/skin.
- Accessory skins are either verified or explicitly listed as unsupported.
- The test install has no stale loose archive folders shadowing REZ files.
