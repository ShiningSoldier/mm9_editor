# Might and Magic IX: Boat Travel System Analysis

This document details the mechanics of the "travel by boat" system in Might and Magic IX, mapping the dock NPCs, scheduling, and engine-level behavior, and outlines how a developer can bypass or customize this travel behavior.

---

## How the Native Travel System Works

The native boat travel system is triggered when a dialogue choice in a `.RUDE` file sets the `nextState` column to `-5`.

### 1. Dialogue to Travel Screen Transition
When `nextState` is `-5`, the game engine suspends the dialogue interface and opens the **Travel Map Overlay**.
* **Starting Port Determination:** The engine identifies which city/region the player is currently at based on either the current active level's filename or the **Shop/Dock ID** of the NPC they spoke with.
* **Dock mapping in `MMIXSHOPS.TXT`:** Docks in the game are registered as service shops of type `Dock`. The dock IDs match the NPC IDs of the boat captains:
  * **NPC 18** (Sailor) at Thjorgard -> **Dock ID 18** (Sea's Fang)
  * **NPC 60** (Olaf the Thronish) at Sturmford -> **Dock ID 60** (Dragon Passage)
  * **NPC 103** (Svein Hjarrandssen) at Drangheim -> **Dock ID 103** (The Black Hammer)
  * **NPC 144** (Olaf Ullson) at Guberland -> **Dock ID 144** (The Flying Bird)
  * **NPC 209** (Jozka Atlia) at Frosgard -> **Dock ID 209** (The Racing Fire)
  * **NPC 257** (Moenach A'Tryht) at Thronheim -> **Dock ID 257** (The Raider)
  * **NPC 288** (Herimgr the Ropewise) at Lindisfarne -> **Dock ID 288** (The Dragon)

### 2. The Travel Calendar (Hardcoded Schedule)
The destinations available on the travel screen depend on the **day of the week** in the in-game calendar. This schedule is documented in the game's cosmetic book text (Item 562, `BOOK AND SCROLL TEXT.CSV`):

| Starting Port | Monday | Tuesday | Wednesday | Thursday | Friday | Saturday | Sunday |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Thjorgard** | Sturmford | Drangheim | Frosgard | Guberland | Lindisfarne | Isle of Ashes | Thronheim |
| **Sturmford** | *No Boat* | Drangheim | *No Boat* | Drangheim | Drangheim | Drangheim | Drangheim |
| **Drangheim** | Guberland | Sturmford | Guberland | *No Boat* | Sturmford | Guberland | *No Boat* |
| **Guberland** | Lindisfarne | Isle of Ashes | Thronheim | Thjorgard | Frosgard | Drangheim | Sturmford |
| **Frosgard** | Lindisfarne | Thronheim | Sturmford | Guberland | Thjorgard | *No Boat* | Drangheim |
| **Thronheim** | Isle of Ashes | Thjorgard | Drangheim | Sturmford | Guberland | Lindisfarne | Frosgard |
| **Lindisfarne** | Drangheim | Sturmford | Guberland | *No Boat* | Thronheim | Thjorgard | *No Boat* |

> [!WARNING]
> **No External Data Files Control Travel Routing**
> Thorough search of all extracted databases under the `DATA` folder (`DATA.REZ`) and compiled scripts under the `SCRIPTS` folder (`SCRIPTS.REZ`) reveals that the actual calendar route mappings (which days route where) do **not** exist in any configuration text files, CSV tables, or level scripts.
>
> The calendar routing logic and starting-to-destination mappings are **hardcoded directly inside the compiled engine binaries** (`CShell.dll` or `Object.lto`). Thus, you **cannot** edit the native `-5` calendar destinations by changing text configs.

---

## How to Customize or Change Destinations

To modify the destinations, add new ones, or ignore the day-of-week calendar constraint, you must **bypass the native `-5` travel handler** and build a scripted/DAT-driven travel flow.

### Step 1: Place `ExitTrigger` and `StartPoint` Objects in the Levels
Cross-level travel is driven by `ExitTrigger` objects in the source level and `StartPoint` objects in the destination level.
1. Open the source world `.DAT` (e.g., `Drangheim.dat`) in the editor.
2. Create an `ExitTrigger` object representing each potential destination.
3. Configure the following properties on the `ExitTrigger`:
   * **Name**: A unique descriptor (e.g., `BoatExitToGuberland`, `BoatExitToSturmford`).
   * **DestinationWorld**: The filename of the destination world, sans extension (e.g., `Guberland` or `Sturmford`).
   * **DestinationStartPoint**: The name of the `StartPoint` object in the destination map where the player will spawn (e.g., `DockArrivalPoint`).
4. Ensure the destination world `.DAT` has a matching `StartPoint` object with that exact name.

### Step 2: Configure Dialog Options in the `.RUDE` File
Instead of routing dialogue to nextState `-5` (which opens the hardcoded calendar UI), set `nextState` to positive dialogue state IDs, listing destination choices explicitly.

**Example RUDE dialogue flow for NPC 103 (Svein):**
```csv
# NPCNbr,currentState,branchId,"player_text","npc_response",nextState,effect_columns...
103,103,1,"We'd like passage.","Where would you like to sail?",2,0,0,0...
103,2,1,"Sail to Sturmford.","Set sail for Sturmford!",-1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1031,0,0,0,0,0
103,2,2,"Sail to Guberland.","Set sail for Guberland!",-1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1032,0,0,0,0,0
103,2,3,"Actually, change my mind.","Suit yourself.",-1,0,0,0...
```
* Selecting a destination sets `nextState` to `-1` (exits dialogue).
* Each destination option uses one of the effect/condition columns to grant a **temporary Key/Flag ID** to the player party (e.g., Key `1031` for Sturmford, Key `1032` for Guberland).

### Step 3: Trigger Transitions via Scripts (`.SCR`)
Each NPC has an associated JSL Script file in `SCRIPTS.REZ` (named e.g. `NPC103.SCR`).
1. Set the script to handle RUDE dialogue exits via the `OnRudeExit` callback:
   ```js
   OnRudeExit OnRude
   ```
2. In the script's exit handler, check if the party has the destination key, clean up the key, obtain the handle to the corresponding `ExitTrigger` object placed in the level, and trigger the transition:
   ```js
   :OnRude
   
   haskey 1031 g_ntemp
   if (g_ntemp == TRUE)
       takekey 1031
       getobjecthandle BoatExitToSturmford g_hobject
       trigger g_hobject trigger
       exit
   endif
   
   haskey 1032 g_ntemp
   if (g_ntemp == TRUE)
       takekey 1032
       getobjecthandle BoatExitToGuberland g_hobject
       trigger g_hobject trigger
       exit
   endif
   
   exit
   ```

Using this method, you can implement fully custom travel schedules, check for specific quest statuses, charge variable gold fees, or unlock new travel paths dynamically as the story progresses.
