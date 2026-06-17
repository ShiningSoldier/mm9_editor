# MM9 Debug Scripts

## MM9ED_DEBUG_ACTOR.SCR

`MM9ED_DEBUG_ACTOR.SCR` is a minimal object script for checking whether an
object exists in-game and whether its script VM starts.

To use it:

1. Build an installable `SCRIPTS.REZ` patch:

   ```text
   python tools\build_debug_script_patch.py
   ```

2. Install the generated `output\debug_scripts` batch with the editor's
   normal install flow.
3. Set the test object's `ScriptName` property to:

   ```text
   scripts\MM9ED_DEBUG_ACTOR.scr
   ```

4. Load the level in-game and check the console/log output.

Expected output starts with:

```text
MM9ED_DEBUG_ACTOR main
MM9ED_DEBUG_ACTOR report
```

Each report also logs the object's `X`, `Y`, `Z` position values and current
animation name. The generated `SCRIPTS.REZ` patch also adds
`MMIXScriptText.csv` row `300`, and the script calls `RollOverText 300`, so a
visible HUD message should appear even when `DebugOut`/`CPrint` are hidden by
the retail executable.

If the HUD message appears while the actor remains invisible, the object is
being created and the likely problem is model, skin, animation, or render
compatibility. If it does not appear, test the same script on a known stock
actor to verify the script path before treating it as an object-spawn failure.
