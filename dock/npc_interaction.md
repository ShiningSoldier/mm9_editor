## NPC Dialogue

- Dialogue is keyed by `NPCNbr`, not `ScriptName`.
- `NPCNbr=0` is reserved for script-driven interaction.
- A fresh NPC needs a new NPCNbr plus RUDE entries in `NPCNAME.RUDE`,
  `TOPBLURB.RUDE`, and `NPC{N}.RUDE`.
- In the current extracted data, `NPC1.RUDE` through `NPC437.RUDE` are normal
  NPC dialogue files. `NPC997.RUDE`, `NPC998.RUDE`, and `NPC999.RUDE` are
  special journal/metadata tables rather than normal conversations:
  - `NPC997.RUDE` is labelled `Quest Notes` in `NPCNAME.RUDE` and contains
    quest journal entries.
  - `NPC998.RUDE` is labelled `Auto Notes` and contains automatically learned
    notes such as barrel effects, trainer locations, and promotion hints.
  - `NPC999.RUDE` is labelled `Awards` and contains completion/achievement-like
    records such as cleared quests, promotions, and cleansed town portals.
  Avoid allocating fresh custom NPC dialogue ids `997` through `999`.
- `TOPBLURB.RUDE` has three columns:
  `NPCNbr,initialState,"opening blurb"`. In the extracted shipped data the
  first two columns always match, so a simple fresh NPC can use
  `N,N,"Hello..."`.
- `NPC<N>.RUDE` rows have 30 CSV columns. The practical layout is:
  `NPCNbr,currentState,branchId,"player text","npc response",nextState`,
  followed by 24 numeric condition/effect columns. All 4,507 shipped rows
  observed in the extracted data have this shape, and the first column always
  matches the `NPC<N>.RUDE` filename number.
- `currentState` is the active dialogue menu/state. Every row with the same
  `currentState` becomes one selectable player option in that menu. `branchId`
  is unique within a state and acts as the option/order id; shipped data allows
  gaps and non-contiguous numbering.
- `nextState` controls what happens after the NPC response:
  - a positive value switches to that `currentState` in the same
    `NPC<N>.RUDE` file;
  - the same state value loops back to the same menu;
  - `999` is commonly used as a conventional "Goodbye" state whose single row
    closes the dialogue;
  - `-1` closes the dialogue directly;
  - other negative values call engine-native service/action screens rather
    than another RUDE state.
- Observed negative `nextState` meanings in shipped data include `-2` shop,
  `-3` training, `-4` skill expert/master training, `-5` travel/passage,
  `-6` bank, `-7` inn/tavern room or business flow, `-8` temple healing,
  `-10` hire/join/board flow, `-11` dismiss hired NPC, and `-16` temple
  donation. Treat the less common values (`-13`, `-14`, `-15`) as
  engine/script-coupled until tested in-game.
- The 24 trailing numeric columns appear to combine conditions and effects.
  The most useful quest/journal columns observed so far are:
  - effect column 1 (absolute CSV column 6): require that the player already
    has a key/flag.
  - effect column 10 (absolute CSV column 15): grant a key/flag.
  - effect column 11 (absolute CSV column 16): grant a second key/flag.
  - effect column 15 and nearby later columns (absolute CSV column 20+):
    require that the player does not yet have a key/flag, commonly used to
    hide options or journal rows after they become stale.
- Quest journal entries are linked by these same key/flag ids. For example,
  Yrsa's `NPC1.RUDE` grants keys such as `1`, `27`, `40`, `92`, and `93`;
  `YRSA.SCR` reacts to some of those keys in `OnRudeExit`; and
  `NPC997.RUDE` has quest-note rows gated by the same key ids. A simple custom
  quest can likely be made RUDE-only by granting a new unused key in the NPC's
  dialogue and adding a matching gated row to `NPC997.RUDE`. Scripted rewards,
  completion checks, world changes, or quest-complete sounds still require
  script support.
- `NPC998.RUDE` and `NPC999.RUDE` use the same row structure as normal RUDE
  files: rows are displayed when their key/flag conditions are satisfied.
  `NPC998.RUDE` rows mostly gate on knowledge/trainer/barrel keys, while
  `NPC999.RUDE` rows gate on completion/promotion/award keys.
- A minimal branching fresh NPC can therefore be authored as:

  ```text
  NPCNAME.RUDE: 438,"Test Peasant"
  TOPBLURB.RUDE: 438,438,"Hello! I'm an NPC. Are you heroes?"
  NPC438.RUDE:
  438,438,1,"Yes.","Good!",438,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
  438,438,2,"No.","Too bad!",438,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
  438,438,3,"Goodbye.","Farewell.",-1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
  ```
- For multi-step dialogue, set `nextState` to a new positive state id and add
  rows for that state. For example, `438,438,1,"Ask...","Answer",10,...`
  followed by one or more rows whose second column is `10`.

