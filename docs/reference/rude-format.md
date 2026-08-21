# RUDE Dialogue Format

**Status: Reference**

Column numbers are zero-based. The editor's `core/rude.py` layer preserves
source row order, all columns, metadata ordering, quoting, CRLFs, embedded CSV
newlines, and unknown native actions.

## Runtime resources

- An NPC's positive `NPCNbr` selects `RUDE/NPC<N>`.
- `NPCNAME` maps NPC numbers to display names.
- `TOPBLURB` maps NPC numbers to independent initial states and opening text.
- `NPC997`, `NPC998`, and `NPC999` are Quest Notes, Auto Notes, and Awards.
- `NPCNbr=0` is unassigned. Script-managed NPCs can still invoke another
  dialogue explicitly.
- `ScriptName` does not select automatic dialogue.

Archive entries are extensionless and require the `RUDE` resource type. Tools
must not assume that an extensionless entry with a generic type will satisfy the
runtime lookup.

## Dialogue row

Each dialogue choice contains 30 columns:

| Column(s) | Meaning |
| --- | --- |
| 0 | NPC number |
| 1 | Current state/menu |
| 2 | Branch identifier |
| 3 | Player option text |
| 4 | NPC response text |
| 5 | Next state or native action |
| 6, 8, 10, 12, 14 | Required keys |
| 7, 9, 11, 13 | Reserved fields |
| 15–19 | Granted keys or native-action parameters |
| 20–24 | Forbidden keys |
| 25–29 | Removed keys or native-action parameters |

Every eligible row for the current state becomes a menu option in source order.
A positive column 5 enters another state in the same resource. `-1` closes the
dialogue. `999` is a convention, not a special runtime close value.

Verified negative actions include:

| Value | Action |
| ---: | --- |
| -2 | Shop |
| -3 | Training hall |
| -4 | Skill training |
| -5 | Travel/passage |
| -6 | Bank |
| -7 | Inn/tavern |
| -8 | Temple healing |
| -10 | Hire/board/join flow |
| -11 | Dismiss hired NPC |
| -14 | Promotion flow |
| -15 | Hired-NPC service/spell |
| -16 | Temple donation |

Unknown negative values must retain their exact integer. Effect slots on native
actions can be service parameters rather than keys and must not be normalized
without action-specific evidence.

## Text limits

The runtime copies player text into a 128-byte buffer and response text into a
256-byte buffer. Authoring is therefore limited to 127 and 255 encoded Latin-1
bytes respectively, leaving space for a null terminator.

## Identifier allocation

The editor determines used dialogue numbers and keys from the active archives
plus staged project assets. Stock corpus counts are observations, not allocation
rules: mods can already occupy values that are free in an unmodified install.

