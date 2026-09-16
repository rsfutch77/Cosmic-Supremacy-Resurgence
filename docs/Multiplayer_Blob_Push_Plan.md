# Multiplayer by blob push , plan

The strategy: the server owns one authoritative galaxy blob, hands each player a
state, the player plays, the server takes their state back and extracts their
orders from it, applies them, ticks, and distributes the next turn.

This is the path that does **not** use the client's own `CMND` protocol. That work
lives on the `galaxy-protocol` branch and is parked, not abandoned: it reached a
client logging in and opening a galaxy served by us, and stopped one reply format
short of live play.

Each item below names what "done" looks like, so none of them is open-ended.

---

## What is already measured

These came out of the September 2026 experiments and the plan depends on them.
The determinism result, the played-versus-loaded result and the `KNPL` field are
written up in `docs/CosmicSupremacy_Reconstruction_Report.md` on this branch.
The `CMND` protocol work is on `galaxy-protocol`.

- **A turn is a pure function of state plus orders.** Three separate process
  launches drove one fixture through 20 turns and produced byte-identical blobs
  apart from one field, the trailing dword of each `KNPL` payload, which holds a
  value the loader does not preserve. 57% of the blob changes over those turns,
  so this is a real workload, not an idle galaxy.
- **The blob carries everything the tick reads.** A client that *loaded* a blob
  and one that *played* to the same turn then ran 40 further turns and agreed
  byte for byte. What the blob does not carry is the engine AI's working block,
  the `ShipDesign` derived-stat cache and render counters, none of which the tick
  reads back.
- **The homeworld customisation allowance is the one piece of player state no
  blob can carry.** Its *effects* are in the blob (`Planet:104` space,
  `Planet:96` the per-unit rates) but the four spent-counters at `0x00842AE4`
  are `.data` globals. `client/dev_tools/homeworld_clicks.py` reads and restores
  them.
- **Re-spending that allowance is additive and unbounded**, so a pushed state
  that re-offers it lets a player ratchet their economy upward every turn.

---

## A. Referee, the tick

- `[ ]` **A1. Package the referee loop as a component.** Launch a client on a
  `.dat`, wait for the galaxy, drive the turn clock, capture, exit. This ran
  unattended many times during the experiments; it needs to become
  `server/referee.py` behind two calls, `apply_orders(blob, orders)` and
  `tick(blob)`, so a Python reimplementation could replace it later.
  **Done when:** the server advances a galaxy by one turn with no human present.
- `[ ]` **A2. Canonical hash.** Mask the trailing dword of every `KNPL` payload
  before hashing, or two honest computations of the same turn disagree.
  **Done when:** two independent runs of one turn produce the same hash.
- `[ ]` **A3. Archive every turn** as blob plus orders plus hash.
  **Done when:** any past turn can be recomputed and checked against its hash.
- `[ ]` **A4. Determinism under combat.** The 40-turn agreement above covered a
  two-civ galaxy with no war, and combat is where an accumulated AI state would
  most plausibly diverge. **Done when:** a played-versus-loaded pair agrees
  across a war.

## B. Client lifecycle, once per turn

- `[ ]` **B1. Push a turn and relaunch, launcher-driven.** The `.dat` path is
  startup-only, so a player's client restarts each turn. Acceptable at 1 to 4 hour
  turns, and it is the launcher's job to make it invisible: at 00:00 say "time is
  up, orders are in", close the client, collect the turn, relaunch on it. That
  also subsumes B2, since a client that is closed at the boundary never reaches
  one.
  **Done when:** a player gets turn N+1 without doing anything manual.
- `[ ]` **B2. Stop the player's client ticking on its own.** A player's client
  must never advance a turn, or it shows a future the referee has not computed.

  **Mostly solved by build choice.** Patches **T1-T5** are what let a client tick
  without a server, and they are applied **only to the Resurgence EXE**. An
  unpatched client waits at 00:00 for a server tick, which is what the original
  did. So a player's client should be a build *without* T1-T5, and the ticking
  problem largely disappears. The bail recorded in D1 was on a Resurgence build
  with a boundary forced by `advance_turns.py`, so it is a harness artifact rather
  than a property of filtered blobs.

  **`GSET.turnlength` is not a usable lever and we are not chasing it.** A blob
  carrying `turnlength = 43200` came up live reading 3600, and the in-game "next
  turn" display showed 45 minutes, so the countdown the UI shows is tied to
  neither value. **Decision: NOP the in-game turn timer and show the real
  countdown in our launcher**, which owns the turn clock anyway.

  **Done when:** a player's client left open past its turn boundary neither ticks
  nor bails, and the launcher shows the countdown.
- `[ ]` **B3. Restore non-blob state after load.** Today that means the four
  homeworld allowance counters, via `homeworld_clicks.py --restore`.
  **Done when:** a pushed state comes up with the right allowance spent.
- `[ ]` **B4. Neutralise the customisation popup on the served path.** It re-offers
  a spent allowance on every push, and re-spending is additive. B3 may be
  sufficient on its own if the counters are the trigger; the other candidate is
  the `listcivnames` / `coaid` answer.
  **Done when:** a returning player is not offered the allowance again.

## C. Orders, the hard part

The client applies orders locally in offline mode, so intent has to be recovered
from the state it hands back. This is the part the `CMND` protocol would have
made unnecessary.

- `[ ]` **C1. Diff engine.** Compare the exact bytes handed to a player against
  the save they return, over the section tree.
  **Done when:** a diff of two blobs yields a field-level change list, not a byte
  list. `server/dev_tools/diff_saves.py` is the seed.
- `[ ]` **C2. Whitelist v1.** Accept only order-bearing fields on objects the
  player owns: ship order bytes and `ROUT`, production queues, research topic
  (`Owner:144`/`Owner:152`), job allocation, facility selection, ship designs,
  governors and admirals, diplomacy proposals.
  **Done when:** a change outside a player's own objects is dropped, and the drop
  is logged.
- `[ ]` **C3. Immediate-effect actions.** Hurry production, conscription, crew
  assignment and job reallocation take effect the moment they are clicked, so the
  diff shows the *effect* and not the intent. Each needs a reverse mapping, effect
  back to intent, which the referee then performs itself so it applies the cost.
  **Done when:** each action is either mapped or explicitly refused, with a list
  of which.
- `[ ]` **C4. Legality gate.** Encode the rule the UI enforces rather than
  inferring legality from an observed state.
  **Done when:** the gate cites a rule for every accepted order type.
- `[ ]` **C5. Get a player's state back without their help.** `SaveGame` at
  `0x0048B350` has exactly one caller, the Save/Load dialog, so the client never
  uploads on its own. A companion process beside the client has to trigger it;
  the launcher is the place.
  **Done when:** a turn's orders arrive with the player having done nothing.

## D. The galaxy

- `[~]` **D1. Fog of war: a filtered blob LOADS but does not TICK.** Tested
  September 2026 with `server/dev_tools/filter_blob.py`, which drops whole
  uncolonised star systems (a `SOLA` subtree, self-contained, so no dangling
  owner references) and corrects the two counts that matter, `SAVE`'s object
  count and every enclosing section size.

  Three systems and 19 objects removed from a turn-2 two-civ galaxy, 37,851 bytes
  down to 34,570:

  | | result |
  |---|---|
  | blob re-parses | yes, 381 sections, full byte coverage |
  | client loads it | **yes** , 2 civs, 149 planets, 29 suns, both homeworlds intact with correct rates |
  | first turn boundary | **no** , diagnostic minidump (no exception stream, so a deliberate bail) and exit |

  So projection is not impossible, but removing galaxy content is not sufficient
  on its own: something still refers to what was removed. The likely culprits are
  the **per-civ reference tables**, `EXSY` (explored systems) and `KNPL` (known
  planets, 36-byte records keyed by object id), which would still name systems
  and planets that no longer exist. `EXSY`'s record layout is undecoded and is
  already an open item in the reconstruction report; its leading `u32` read 63 for
  a civ that had explored 105 systems, so it is not a plain count.

  **This matters less than it first appeared.** Players' clients do not need to
  tick, the referee does, and it gets the **full** unfiltered state. A client that
  can only view and issue orders is all the design asks for, and the launcher
  closes it at the boundary anyway (B1). Filtering also gives correct fog
  behaviour for free: a player cannot order a ship to a system that is not in
  their blob, because it genuinely is not there.

  **So the open question narrows to: can orders be issued in a filtered galaxy?**
  That needs one click in a filtered client and a diff of what comes back.

  **Next, if a player's client ever does need to tick:** filter `EXSY` and `KNPL`
  consistently with the dropped content, which needs `EXSY` decoded first. Until
  then, **full-state distribution is the fallback**, and that should be treated as a known property rather than an
  oversight: any player's client holds the whole galaxy and a modified one is a
  maphack.
  **Done when:** orders can be issued and captured in a filtered galaxy. A
  filtered blob surviving a turn boundary is a separate, lower-priority goal.
- `[ ]` **D2. Per-player projection.** Follows from D1. Identity for now.
  **Done when:** each player is handed a state derived from the authoritative one
  rather than the authoritative one itself.
- `[ ]` **D3. Turn clock and absent players.** The turn advances on the clock and
  never waits for submissions; governors and admirals are the original's answer to
  a player who is not there, and they run inside the referee's tick.
  **Done when:** a galaxy advances on schedule with a player missing.
- `[x]` **D4. More than two civs in a galaxy.** `server/dev_tools/inject_civ.py`
  adds a whole player to a blob, confirmed live with a third civ that owned a
  homeworld and played four turns.

## E. Not applicable on this path

- The `CMND` command protocol, `RQSV`'s reply format, and live-versus-replay
  mode. All on `galaxy-protocol`.
- The client build-id checksum and the machine and user names, which the client
  sends in `LGIN`. This path never issues `LGIN`, so neither the tamper-detection
  opportunity nor the privacy question arises.
