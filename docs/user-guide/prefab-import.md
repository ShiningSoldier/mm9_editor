# Prefab Import

**Status: Supported with fail-closed runtime safety**

Open **Tools → Import Prefab…** to inspect a DEdit `.ed` prefab or compiled v66
`.dat` source. The workspace reports source structure, objects, brush ownership,
links, dependencies, proposed names, and blockers before placement.

## Support states

| State | Meaning |
| --- | --- |
| Static ready | The selected static representation can be previewed. Check the runtime representation before assuming it is installable. |
| Behavioral ready | The supported object graph can be materialized with its required resources and bindings. |
| Action required | Supply an explicit resource choice or target-level binding. |
| Blocked | Required semantics cannot be preserved safely. |

The importer never silently changes a requested full-behavior import into
static geometry.

## Runtime representations

MM9 objects do not all use the same storage model:

- **Game model object**: a catalog-backed `Prop` with an observed ABC/skin
  combination. This is the preferred installable representation for a matching
  brush-shaped prop and adds no BSP record.
- **Native object graph**: supported runtime objects materialized from catalog
  templates, with typed property overlays, rewritten internal links, and
  validated dependencies.
- **Compiled BSP assembly**: controller objects plus BSP records copied from a
  real v66 DAT after structural validation.
- **Generated ED brush preview**: useful for inspection, but not a complete
  runtime BSP. It is blocked from game-bound Save/Install.

An authored `.ed` brush does not become safe game BSP merely because it looks
correct in the viewport. Installable brush geometry must come from a validated
compiled DAT or be represented by a game model. Object-only ED graphs are not
subject to that geometry restriction.

## Behavioral capabilities

The planner has explicit policies for object-only props and lights, passive
world roles, moving controllers, links, teleporters, destructibles, hazards,
shooters, and reviewed scripts. Support is capability-based: a familiar class
name does not override an unresolved resource, unsupported property shape,
unreviewed script, missing target, or unsafe BSP representation.

Internal names are rewritten into a deterministic namespace. External object
targets must already exist in the active level and be bound explicitly. A
`PortalName` must reference a portal already compiled into the target VisBSP,
or be deliberately cleared with `<omit>` when visibility culling is not needed.

Reviewed Pipe Organ scripts receive unique rewritten resources; unknown scripts
remain blocked. Generated scripts are staged in a replacement `SCRIPTS.REZ`,
never written directly to the installed archive.

## Placement and editing

Choose the placement anchor and collision/representation settings, resolve all
required bindings, then select **Start placement** and click a BSP surface. A
behavioral assembly remains one atomic project operation: movement, yaw,
deletion, undo, save, and project reload cannot split its members or orphan
internal links.

Legacy `.mm9mod` files containing `CloneDoorOp` remain readable, but the editor
does not expose a command for creating new door clones. New door work goes
through the prefab workspace and its current safety checks.

## Release checks

Before installing a behavior-bearing prefab:

1. Resolve every resource and external binding.
2. Confirm that any BSP comes from validated v66 compiled data.
3. Save and reopen the output DAT.
4. Test rendering, collision, movement, links, sounds, and scripts in a fresh
   game load as applicable.

The completed implementation history and later compiled-assembly design are
preserved under [research](../research/README.md); they are not the current user
contract.

