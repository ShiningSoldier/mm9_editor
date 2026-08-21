# Viewport

**Status: Supported**

The viewport displays DAT BSP geometry in editor display space. It reflects the
game-space X axis so orientation matches the running game; all committed
positions are converted back to MM9 game coordinates before serialization.

## Orbit mode

| Action | Effect |
| --- | --- |
| Left-drag empty space | Orbit |
| `Alt` + left-drag or middle-drag | Pan |
| Mouse wheel | Zoom |
| `F` | Fit normal visible geometry |
| Click an object or handle | Select |
| Drag selected object | Move on X/Z while preserving Y |
| Arrow keys | Nudge on camera-relative X/Z |
| `PageUp` / `PageDown` or `E` / `Q` | Raise/lower |
| `[` / `]` | Rotate yaw |
| Hold `Shift` | Use larger movement/rotation steps |

While placing an object, click a BSP surface to use the exact hit position.

## Fly mode

| Action | Effect |
| --- | --- |
| Left-drag | Look |
| `W` / `S` | Forward/back |
| `A` / `D` | Strafe |
| `Q` / `E` | Down/up |
| Mouse wheel | Dolly along the view direction |
| Hold `Shift` | Move faster |
| `F` | Fit normal visible geometry |

## Helpers

Object and world helper billboards are hidden by default.

- **View → Toggle object helpers** shows billboards for objects that already
  have visible model previews.
- **View → Toggle world helpers** shows model-free service/control objects such
  as triggers, sounds, rails, and world markers.
- **View → Helper BSP** selects normal rendering or translucent helper geometry
  and filters AI rail, collision, water, trigger, sound, and sky/visibility
  roles.

Selected hidden helpers remain visible enough to locate. Helper visibility does
not change saved world data.

## Editing and undo

Dragging previews continuously and commits one operation on release. Repeated
keyboard movement or rotation is debounced into a useful transform operation.
Top-level adds, deletes, property edits, and transform commits support undo and
redo.

A newly added object is one pending add operation. Edits to that object update
the add operation, so undo removes the whole pending object rather than stepping
through each of its unsaved property changes.

## Rendering scope

The viewport renders BSP surfaces with DTX textures and supported ABC objects
as static meshes. Weighted NPC and creature models use a conservative static
LOD0 pose. Animation playback and runtime-created attachments are not
simulated. Unsupported assets remain selectable through handles or the object
list.

Set `MM9_EDITOR_PROFILE=1` before launch for averaged frame-stage timings, or
`MM9_EDITOR_PROFILE_LOAD=1` for one-time level-loading timings. These are
developer diagnostics, not viewport controls.

