# World Data

**Status: Reference**

MM9 and LoMM compiled worlds use LithTech DAT version 66. A DAT contains
compiled BSP/render data followed by runtime `WorldObject` records. Property
type codes, raw values, ordering, and string representation must be preserved
unless a feature deliberately replaces them.

Some `LongInt` properties contain IEEE-754 float bit patterns. Their raw integer
appearance is not sufficient reason to normalize or retype them.

## Placement and floor behavior

`Pos` and `Rotation` are game-space values. The viewport uses a display-space
X reflection and converts edits back before saving.

`MoveToFloor` is an engine property, not an editor-only preference. The viewport
can approximate its initial effect using model dimensions and solid BSP, but it
does not simulate dynamic runtime object-on-object physics.

## Cross-level transitions

An `ExitTrigger` in the source world names a `StartPoint` in the destination.

Important `ExitTrigger` properties include:

- `DestinationWorld` — destination DAT stem without `.DAT`;
- `StartPointName` — destination `StartPoint.Name`;
- `Pos` and `Dims` — trigger volume;
- `StartOn` and `AskPlayer` — activation behavior;
- `TravelDays` — a LongInt slot that may carry float bits; and
- `LoadScreen` and `LoadTextID` — loading metadata.

Same-level `Teleporter` behavior is separate and uses named objects within the
loaded world.

There is no transition wizard. To add a connection, create and name the
destination StartPoint, create the source ExitTrigger, set the two fields above,
save both levels, and test a fresh load. A missing target should be reviewed,
but stock data can contain engine/script-specific exceptions.

## Doors and moving BSP

Many physical doors are not ABC props. A `Door` or `RotatingDoor` controller
owns a same-named compiled BSP world model. Linear doors use properties such as
`MoveDir`, `MoveDist`, and speed values; rotating doors use `RotationPoint` and
`RotationAngles`. Paired doors can reference one another through
`DoubleDoorName`.

Changing only the controller does not create new physical geometry. New
installable door geometry must come from a validated compiled v66 assembly.
Legacy projects containing `CloneDoorOp` remain loadable, but new door creation
uses **Tools → Import Prefab…** and its fail-closed representation checks.

## Prefab geometry

DEdit ED v1249 files preserve authoring brushes, hierarchy, controller
ownership, and object properties. They are not compiled runtime BSP. The
editor's generated ED-brush representation is suitable for preview and research
but lacks the complete node/physics structures required by MM9 and is blocked
from game-bound Save.

Installable prefab paths are:

- a catalog-backed model object with no imported BSP;
- an object-only/native graph that requires no new BSP; or
- structurally validated compiled BSP records copied from v66 DAT.

See [Prefab import](../user-guide/prefab-import.md).

## Treasure chests

Chest loot is normally driven by class behavior and properties rather than an
attached script. Relevant fields include `Random`, `Gold`, `GoldOnly`,
`Item1`–`Item5`, `TrapLevel`, `TreasureLevel`, `TreasureOptions`, and the
treasure-type fields. Preserve each field's original property type.

## Existing save games

Save files contain runtime records for many active-level objects rather than a
complete embedded DAT. Loading an old save can restore object state that
predates an installed world edit. Static/setup objects and BSP behavior differ,
so the effect is not equivalent to embedding the whole level.

A save migrator is not supported. Runtime handles, AI state, script state, and
class-specific records make blind replacement unsafe. Use a new game or fresh
load path for validation.

## Model preview

Supported rigid ABCs and conservative static poses for NPC/creature models are
rendered from `MODELS.REZ` and `SKINS.REZ`. Actor visuals can be resolved from
catalog data derived from `DATA.REZ` rather than unreliable DAT placeholder
filenames. Full animation and runtime-created attachments remain outside the
viewport contract.

