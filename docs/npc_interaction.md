# NPC Dialogue and Quests (RUDE)

This describes the format used by the shipped MM9 runtime and `RUDE.REZ`.
Column numbers below are zero-based.

## Runtime lookup and archive layout

- Automatic NPC dialogue is selected by the object's `NPCNbr`, not its
  `ScriptName`.
- The client requests `RUDE\\NPC<N>.rude`. In the archive this is an
  extensionless entry named `RUDE/NPC<N>` whose REZ resource type is `RUDE`.
  `NPCNAME` and `TOPBLURB` use the same `RUDE` resource type. An extensionless
  entry with resource type `0` can be listed by editor tooling but will not
  satisfy the runtime's typed lookup.
- `NPCNbr=0` is the default/unassigned value; there is no shipped `NPC0` RUDE
  resource. Script-managed NPCs can still call `DoRUDE` with a real positive
  dialogue id.
- `ScriptName` is independent of dialogue selection. It remains important for
  `OnRudeExit`, rewards, completion checks, and world changes.
- The stock archive contains normal dialogue files `NPC1` through `NPC436`.
  The first unused normal id is therefore `437`. Keep custom ids away from the
  reserved `997` through `999` range.

The three resources involved in normal dialogue are:

- `NPCNAME`: 439 two-column rows, `NPCNbr,"display name"`.
- `TOPBLURB`: 439 three-column rows,
  `NPCNbr,initialState,"opening blurb"`.
- `NPC<N>`: one or more 30-column dialogue rows for that id.

The stock `TOPBLURB` rows happen to use the same value for `NPCNbr` and
`initialState`, but the runtime reads them independently. Each call to
`DoRUDE` starts at the `initialState` again; persistent quest progress is
represented by keys rather than a remembered dialogue state.

## Dialogue row schema

All 4,504 rows in the shipped `NPC<N>` resources have 30 columns, and column
0 matches the resource's NPC number.

| Column(s) | Runtime meaning |
| --- | --- |
| 0 | Dialogue NPC id |
| 1 | Current menu/state |
| 2 | Branch/option id |
| 3 | Player option text |
| 4 | NPC response text |
| 5 | Next state or native action |
| 6, 8, 10, 12, 14 | Required keys; every nonzero key must be present |
| 7, 9, 11, 13 | Reserved/ignored; zero in the shipped data |
| 15-19 | Keys granted, or parameters consumed by a native action |
| 20-24 | Forbidden keys; every nonzero key must be absent |
| 25-29 | Keys removed, or parameters consumed by a native action |

Zero means an unused key/effect slot. Normal quest branches can require
existing keys, grant new keys, hide themselves once a key exists, and remove
keys. Native actions may interpret effect slots as service-specific
parameters, so their rows should be copied from a known-good stock pattern
until each action has dedicated editor support.

The runtime copies player option text into a 128-byte buffer and NPC response
text into a 256-byte buffer. Authoring tools should restrict encoded Latin-1
text to at most 127 and 255 bytes respectively, leaving room for the null
terminator.

## State transitions and actions

- Every eligible row whose column 1 matches the current state becomes a menu
  option. Rows remain in file order. Column 2 is used to distinguish branches;
  it is not a numeric sort key.
- A positive column 5 enters that state in the same `NPC<N>` resource. Using
  the current state loops back to the same menu.
- `999` is only a common authoring convention for a goodbye state; the runtime
  does not special-case it. Stock data includes state-999 loops.
- `-1` closes the dialogue.
- Other negative values dispatch native or script-coupled actions. Verified
  client handlers are:

  | Value | Action |
  | --- | --- |
  | -2 | Shop |
  | -3 | Training hall |
  | -4 | Skill training |
  | -5 | Travel/passage |
  | -6 | Bank |
  | -7 | Inn/tavern |
  | -8 | Temple healing |
  | -10 | Hire, board, or join flow |
  | -11 | Dismiss hired NPC |
  | -14 | Promotion flow |
  | -15 | Hired-NPC spell/service |
  | -16 | Temple donation |

`-13` occurs in two shipped Tinker rows but has no dedicated client dispatch;
those rows rely on the common exit/script path. Values `-9` and `-12` do not
occur in the shipped dialogue corpus.

## Journal and quest resources

- `NPC997` is `Quest Notes` and contains quest journal entries.
- `NPC998` is `Auto Notes` and contains learned hints, trainer locations, and
  similar notes.
- `NPC999` is `Awards` and contains completion and promotion records.

These special resources use the same 30-column condition/effect schema rather
than a separate journal format. For example, Yrsa's `NPC1` rows require keys
including `1`, `27`, `29`, `40`, and `92`, and grant keys including `1`, `7`,
`93`, `469`, and `499`. `YRSA.SCR` reacts to keys `1` and `27` after dialogue,
while journal rows use related keys for visibility. A RUDE-only quest can use
unused keys for dialogue and journal gating; scripted rewards, world changes,
and completion behavior still require script support.

## Minimal fresh dialogue

The next stock-free id is `437`, so a minimal looping conversation is:

```text
NPCNAME: 437,"Test Peasant"
TOPBLURB: 437,437,"Hello! I'm an NPC. Are you heroes?"
NPC437:
437,437,1,"Yes.","Good!",437,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
437,437,2,"No.","Too bad!",437,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
437,437,3,"Goodbye.","Farewell.",-1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
```

For multi-step dialogue, set column 5 to a new positive state and add rows
whose column 1 is that state.

## Current editor scope

`core/rude.py` now provides the lossless authoring model used by RUDE output:

- `RudeDialogueMetadata` combines the `NPCNAME` and `TOPBLURB` fields while
  keeping `NPCNbr` and `initialState` independent.
- `RudeDialogue` retains source row order and exposes first-seen states with
  their choices in runtime menu order. This matters because stock files often
  interleave rows belonging to different states.
- `RudeChoice` retains all 30 columns through `RudeKeyConditions`,
  `RudeKeyEffects`, and `RudeAction`. Known native actions have named enum
  values; unknown negative actions retain their exact integer.
- `RudeMetadataCatalog` preserves `NPCNAME`/`TOPBLURB` row order, quoting,
  CRLFs, embedded CSV newlines, and unchanged source spelling. Parsed stock
  resources round-trip byte-for-byte; edited rows are emitted as valid quoted
  Latin-1 CSV.

The fresh-NPC dialog uses this model to create `NPCNAME`, `TOPBLURB`, and a
single looping `NPC<N>` menu, then writes an output `RUDE.REZ` with the
runtime-required resource type.

## Independent project assets

RUDE resources are project assets rather than children of NPC placement
operations:

- `Project.open_rude_asset(N)` loads any existing dialogue into a
  `RudeAssetEdit`. This includes the special `NPC997`, `NPC998`, and `NPC999`
  journal tables; no level or world object needs to be open.
- `Project.create_rude_asset(dialogue)` stages a new resource without placing
  an NPC. Fresh-NPC placement now calls this API separately and its `AddOp`
  contains no RUDE payload.
- Merely opening an asset is clean. Changes to its name metadata, blurb/initial
  state, and dialogue rows are tracked independently. A RUDE-only project can
  therefore pass `has_pending()`, produce a save plan, write `RUDE.REZ`, and
  generate a manifest with no DAT writes.
- Save plans list only the resources that changed. For example, editing a
  quest-note row patches only `RUDE/NPC997`; renaming the Awards table patches
  only `RUDE/NPCNAME`.
- Existing assets retain their source metadata and dialogue bytes. Saving is
  rejected if that same source resource changed after it was opened, avoiding
  an unnoticed overwrite.
- Project format 22 persists the current lossless asset plus its original
  baseline and independent dialogue-script integrations. Format 21 remains
  readable, and version-20 and earlier placement-attached RUDE registrations
  remain supported as a legacy compatibility path.

## Dialogue and quest editor

`Tools -> Dialogue & Quest Editor...` (also available from the toolbar) opens
an `NPC<N>` resource without requiring a level or placed NPC. Enter `997`,
`998`, or `999` to edit the shipped Quest Notes, Auto Notes, or Awards tables.
If a normal id does not exist, the editor can create it as a standalone
project asset with one closing choice.

The **State Graph & Choices** tab provides:

- A graph of transitions between states. The initial state is green, the
  selected state is blue, and a transition to a nonexistent state is red.
- State add, rename, and delete operations. Renaming also updates inbound
  state transitions. Deleting deliberately leaves inbound transitions visible
  as missing targets so an accidental broken quest path is not hidden.
- Choices in their real runtime/file order, with add, delete, and move-up/down
  controls. A state exists in RUDE only while it has at least one row, so a
  newly added state starts with a closing choice.
- Editing for branch id, player and NPC text, raw next-state/native-action
  value, all required/forbidden key slots, all grant/remove or native-parameter
  slots, and the four reserved columns. Player and response strings are
  checked against the Latin-1 runtime buffer limits before they are accepted.

The **Simulator** starts from `TOPBLURB.initialState` with a comma-separated
mock party key set. It filters choices using required and forbidden keys,
preserves menu order, applies nonzero grant slots and then removal slots, and
follows positive state transitions. Close and native actions end the mock
session and report the dispatched action. For native rows the effect slots
can be service parameters rather than quest keys; the simulator shows
its generic mock-key interpretation, but it does not attempt to emulate shops,
training, travel, scripts, rewards, or other engine services.

Edits remain in the dialogue window's working copy until **Apply to Project**
is pressed. `File -> Save...` then creates a reviewed output batch containing
the patched `RUDE.REZ`; it never writes directly into the live game archive.

## Quest tools

The **Quest Tools** tab combines four authoring checks that use the same
lossless working dialogue as the graph editor.

### Key usage index

**Build / Refresh Index** scans every `NPC<N>` resource in `RUDE.REZ` and
every resource in `SCRIPTS.REZ`. Current project assets and unapplied dialogue
windows override their archived versions, and generated project script assets
are included. RUDE usages are classified as required, forbidden, granted,
removed, or an ambiguous native-action effect/parameter.

The script scanner indexes `HasKey`, `GiveKey`, and `TakeKey` calls. It resolves
literal operands and simple local `#number`, assignment, and `Set` values. If a
variable can hold several locally assigned values, all are listed as possible
usages. Operands populated through parameters, includes, arithmetic, or other
runtime logic are listed separately as unresolved rather than guessed.

**Suggest Unused** chooses the first value absent from the resolved RUDE/script
index, starting at the entered key or just above the largest resolved value
when the field is empty. This is a collision-avoidance aid, not proof that the
engine or an unindexed dynamic script never uses or limits that value.

### Journal and award entries

The entry form creates stock-shaped rows without manually editing the special
tables:

- **Add Quest Note to NPC997** writes the title to player text, the journal
  detail to the NPC-response column, and uses required/forbidden keys for
  visibility.
- **Add Award to NPC999** writes the award text, uses `"blank"` as the stock
  response, and requires at least one visibility key.

Both entries loop to the special table's initial state and receive the next
branch id in that state. The related asset editor is opened or focused so the
new row can be reviewed. It still must be applied to the project before Save.

### Reachability and action-aware validation

**Run Validation** reports errors, warnings, and informational findings.
Double-clicking an issue selects its state/branch in the graph editor. Checks
include:

- missing initial or transition states, duplicate branch ids, structurally
  unreachable states, and reachable state groups with no close/native exit;
- impossible required-and-forbidden key combinations, grant/remove overlap,
  nonzero reserved columns, Latin-1 encoding, and runtime text-buffer limits;
- unknown negative actions, the required skill parameter for native skill
  training (`-4`), and hired-NPC key consistency for dismiss (`-11`).

Validation is resource-aware. `NPC997`-`NPC999` are display tables, not normal
conversation graphs, and shipped rows sometimes use column 5 values such as
`0` or `994` without defining transition states. Those values are not reported
as missing dialogue targets. Extra states in special tables are still listed
as informational unreachable rows. Likewise, nonzero effect slots on native
actions remain labeled as possible parameters rather than definite key grants
or removals.

## Dialogue script integration

`Tools -> Dialogue Script Integration...` authors runtime effects as a
separate project asset. It does not put script text into a RUDE row and does
not require an NPC placement to be open. Each generated resource is written
under `SCRIPTS\MM9EDITOR\` and is shown in the Save review as a patch to the
complete staged `SCRIPTS.REZ`.

The workflow follows the shipped game pattern:

1. A terminal RUDE choice grants a temporary completion key.
2. The NPC script registers an `OnRudeExit` callback.
3. The callback checks that key and, normally, consumes it for one-shot
   behavior.
4. It performs the configured actions in authoring order.

The editor emits only primitives verified in stock MM9 scripts:

| Tooling group | Generated JSL |
| --- | --- |
| OnRudeExit hook | `OnRudeExit`, `HasKey`, optional `TakeKey` |
| Rewards | `GiveExp`, `GiveGold`, ordered `GiveItem` calls |
| Completion sound | `PlaySound "sounds\events\quest.wav", DoNothing, 100, 240, FALSE, 100` by default |
| World changes | `GetObjectHandle` followed by `Trigger` with an authored message |

World changes therefore cover named-object behavior such as `open`, `unlock`,
`destroy`, `on`, and `off`, as well as an `ExitTrigger`'s `trigger` message.
Object names and messages are restricted to safe JSL tokens, reward values and
item ids must be nonnegative/positive as appropriate, completion keys must be
unique within the script, and sound resources must be WAV files below
`sounds\`.

For an NPC with no other behavior, leave the base ScriptName empty and the
tool creates a small standalone script. For an NPC that already has a script,
enter its exact current ScriptName and choose **Load Existing Script**. The
tool copies that source losslessly to the generated resource, inserts a single
`Gosub MM9EditorRudeExit` into its existing local callback, and appends the
reviewed handler. Automatic integration is deliberately blocked when the base
script changes `OnRudeExit` more than once or its callback label is not local;
those scripts need a manual, script-specific review.

**Apply & Attach to Selected NPC** stages the script and changes the selected
matching NPC object's `ScriptName`. If that object already has a ScriptName,
attachment is refused unless the same source was loaded as the base, avoiding
silent loss of existing AI or quest behavior. Script assets and this DAT
property change remain independently reviewable and undoable. **Apply Script
to Project** can instead be used without a level; attach its displayed
ScriptName later through the ordinary Properties panel.

Generated assets participate in the quest key index and runtime preview
overlay. Save combines dialogue scripts and any reviewed prefab scripts into
one `SCRIPTS.REZ` rewrite, preserves every unrelated archive resource, assigns
new entries the runtime `SCR` resource type, and refuses to overwrite an
untracked same-named script.
