# Dialogue and Quests

**Status: Supported**

MM9 selects ordinary NPC dialogue by the object's positive `NPCNbr`. Its
`ScriptName` is separate and is used for runtime callbacks, rewards, and world
changes.

## Create dialogue with an NPC

When placing an NPC, choose whether to inherit the cloned object's dialogue or
create a fresh dialogue resource. The editor scans the active `RUDE.REZ` and
suggests an unused normal NPC number; do not assume a number is free merely
because it is unused in the stock game.

Fresh dialogue stages these resources:

- `RUDE/NPCNAME` — display-name metadata;
- `RUDE/TOPBLURB` — initial state and opening text; and
- `RUDE/NPC<N>` — the dialogue rows.

They are independent project assets and are written only to a saved output
batch.

**Run Current Level** can preview these assets before they are saved. The
preview keeps the logical archive names internally and writes typed loose files
(`NPC<N>.RUDE`, `NPCNAME.RUDE`, and `TOPBLURB.RUDE`) for the game runtime.

The fresh-NPC form's default rows produce two choices: `Hello.` loops back to
the menu and `Goodbye.` closes it. A row whose player text is plainly
`Goodbye`, `Bye`, or `Farewell` is used as the closing choice; the editor adds
its fallback `Goodbye. / Farewell.` row only when no such row was authored.
This avoids a second, non-closing Goodbye option.

After **Install Output to Game**, an exact installed copy of a new RUDE asset
is promoted to that asset's clean source baseline. Running the current level
therefore treats the project's own installed `NPC<N>`, `NPCNAME`, and
`TOPBLURB` rows as an idempotent match, while a different resource using the
same NPC number remains a blocking collision. Later dialogue edits continue
from the installed baseline normally.

## Dialogue and Quest Editor

Open **Dialogues → Dialogue and Quest Editor…** without opening a level. Enter
an existing NPC number or create a new normal dialogue. The special resources
`NPC997`, `NPC998`, and `NPC999` contain Quest Notes, Auto Notes, and Awards.

The state graph preserves real source-row/menu order. You can add, rename, and
delete states; edit every condition, effect, reserved, and native-action field;
and simulate positive-state transitions against a mock set of party keys.
Native actions end the mock session because the editor does not emulate shops,
training, travel, or other engine services.

The Quest Tools tab can:

- index literal and locally resolvable key use across RUDE and scripts;
- report unresolved dynamic script operands instead of guessing;
- suggest an apparently unused key;
- add stock-shaped Quest Note and Award entries; and
- validate reachability, key predicates/effects, native-action parameters,
  encoding, and runtime text limits.

An unused-key suggestion is a collision-avoidance aid, not proof that unknown
runtime code never uses that value.

Changes remain in the dialogue window until **Apply to Project**. Ordinary Save
then writes a replacement `RUDE.REZ` containing only the changed logical
resources plus every untouched archive entry.

## Dialogue script integration

Open **Dialogues → Dialogue Script Integration…** to author an `OnRudeExit`
effect as a separate script asset. The supported workflow is:

1. A terminal dialogue choice grants a temporary completion key.
2. The NPC script receives `OnRudeExit`.
3. The generated handler checks and normally consumes that key.
4. It performs supported rewards, the completion sound, or named-object
   trigger messages in the configured order.

Generated resources live below `SCRIPTS\MM9EDITOR\`. For an NPC without other
behavior, the editor creates a small standalone script. To extend an existing
script, load the exact resource selected by the object's `ScriptName`; automatic
integration is blocked when callback structure is ambiguous.

**Apply & Attach to Selected NPC** stages the script and changes the matching
object's `ScriptName`. **Apply Script to Project** stages it independently for
later attachment. Save writes a complete staged `SCRIPTS.REZ` and refuses to
overwrite an untracked same-named script.

See [RUDE format](../reference/rude-format.md) for the 30-column schema and
native action values.
