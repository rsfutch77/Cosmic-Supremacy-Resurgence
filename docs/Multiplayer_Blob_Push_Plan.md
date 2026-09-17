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

## The two-player round, end to end (September 2026)

**Two players took turns in one shared galaxy and each saw the other's action.**
Run by hand from the command line, one machine, the two players sequentially:

1. `player_turn.py serve turn3.b64 --civ DemoPlayer` stamped the state, started a
   client and stopped its clock. The player ordered a colony ship.
2. `player_turn.py collect` took the state back.
3. The same two steps for `--civ Neighbor`, a second civ whose homeworld sits in
   DemoPlayer's own star system.
4. `referee.py duel3b.b64 --from DemoPlayer=... --from Neighbor=... --turns 4`
   took three orders, dropped two changes, and advanced the galaxy to turn 7.
5. Each player was served the result, stamped for their own civ.

Planet 136 came back owned by DemoPlayer and planet 138 by Neighbor, each from an
order given in a separate client session. Both players then saw the other's new
colony in their own game, and once one player's ship came within scan range of
the other's, that ship became visible too.

What this does **not** yet show: the two clients ran one after another rather
than at once, and on one machine rather than two; every step was a command typed
by hand rather than the launcher's job (B1); only ship orders are extracted, and
everything else in section C is untouched; and the customisation popup had to be
dismissed by hand on every serve (B4).

**Visibility is the engine's own, and better than feared.** A player sees every
planet in a system they have entered, including who owns it, and sees another
civ's ships only within scan range, about 30 units. So a full-state blob does not
show a player their rivals' fleets, which is why the colony was the visible
signal and why ship moves stayed invisible for most of the session. It remains
true that the blob carries the whole galaxy and a modified client is a maphack,
see D1.

---

## A. Referee, the tick

- `[x]` **A1. Package the referee loop as a component.** `server/referee.py`
  exposes `apply_orders(blob, submissions)` and `tick(blob, turns)`, kept apart so
  a Python reimplementation of the rules would replace `tick` alone and every
  caller would keep working. `tick` writes the blob out, starts a client on it,
  drives the clock, captures and closes. Confirmed advancing a three-civ galaxy
  from turn 3 to turn 7 with no human present.
  `[ ]` **The referee's client is stamped as one of the civs**, since every blob
  names a local player. Whether the engine AI still plays that civ during a
  referee tick is unmeasured, and if it does not, one civ silently stops acting.
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

  **Holding the clock works, and it is required rather than optional.** A client
  served turn 3 and left alone for a few minutes returned **turn 4**, and the diff
  against what it was served showed **174 sections changed instead of one**. An
  order cannot be recovered from that: the change list carries every consequence
  of the tick, and copying a ship's `DYNO` out of it copies post-tick positions,
  which is merging state. `player_turn.py serve` therefore writes a large turn
  length to `0x0080AA08` after load, and the next two player turns both came back
  still at turn 3 with a single section changed.

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
- `[x]` **B5. Tell a client which civ it plays.** Two clients sharing a galaxy
  have to control different civs, and **`GLOB` carries the answer**: one `u32`
  holding the object id of the civ the loading client will play.
  `server/dev_tools/set_blob_player.py` writes it. A blob stamped 198 came up as
  DemoPlayer and the same blob stamped 202 came up as BadGuy, UI included, which
  is what makes per-player distribution a data operation.

  **The field is not at a fixed offset**, and assuming it was produced a save the
  client rejected with an exception dialog. `GLOB` holds a variable-length list of
  the players the galaxy knows about, each entry a user id and a name, so the id
  moves once any civ has met another: `+40` with no contact, `+65` once one entry
  exists, and `GLOB` itself grew from 87 bytes to 112. The stable landmark is the
  tag that follows it,

      ... u32 99999 ; u32 localPlayerObjectId ; 'TMGX' ...

  so the tool finds `TMGX`, steps back four bytes, and checks the value against
  the civs the blob contains before writing anything.

  Two other candidates were tested first and are dead ends worth not retesting:
  swapping the two `OWNR` sections' order changed nothing, and swapping `Owner:4`,
  the trailing `u32` that reads 0 on one civ and 21 on the other, changed nothing.

  At runtime the selection comes from a **TLS red-black tree of player slots**,
  the roster the testbed galaxy join populates. `0x0052DE10` walks it and takes
  the first slot whose `+0x38` is non-zero, reading the civ's object id from
  `+0x30`; `0x00537BF0` maps that id to a reference cell through the map at
  `0x00857C7C`, and the cell is stored in `0x00857904`.
  `client/dev_tools/set_local_player.py` assigns that cell directly in a running
  client, which is useful while testing and is not how a turn should be served.

## C. Orders, the hard part

The client applies orders locally in offline mode, so intent has to be recovered
from the state it hands back. This is the part the `CMND` protocol would have
made unnecessary.

- `[x]` **C1. Diff engine.** Compare the exact bytes handed to a player against
  the save they return, over the section tree. `server/dev_tools/diff_saves.py`
  reports the change at section level and `merge_orders.py` reads the fields.

  **A ship move order is three edits and they are contiguous.** A human given a
  turn-2 galaxy, asked for one move and nothing else, changed exactly one section
  of a 37,851-byte blob: that ship's `DYNO`, 38 bytes to 132. The edits are the
  `SHCO` order-type byte (0 to 1), the has-orders byte `Ship:76` (0 to 1), and an
  appended `ROUT` section plus its trailing `u32`. The `ROUT` payload is a
  waypoint list of position triples ending in the destination object id. Nothing
  else in the blob moved, and `inject_order.py` already writes exactly this
  shape.

  **Colonising is a ship order too**, not an immediate-effect action. In the UI it
  is right-click a ship, colonize, pick the planet; in the blob it is the same
  three edits with `SHCO` byte 0 reading **3** where a move writes **1**. So both
  orders the two-player round needed share one extraction path.
- `[~]` **C2. Whitelist v1, ship orders only.** `server/dev_tools/merge_orders.py`
  copies a submitted `DYNO` onto the authoritative blob for ships the
  **authoritative** blob says the player owns, and names every other change it
  drops. Ownership is never read from the submission, so a player cannot claim a
  ship by rewriting the owner field in their own copy.

  Verified both ways on the capture pair above: the owning civ's submission
  rebuilt the returned blob byte for byte from the baseline, and the same file
  submitted under the other civ's name was rejected with the owner named.

  `[ ]` **Diff each submission against the state served to that player**, not
  against the authoritative blob as it evolves. Applying one player's orders first
  moves the authoritative state under the next player, whose untouched copy then
  looks like an attempt to change ships they do not own: the two-player round
  logged two such drops and neither player had done anything. They were harmless,
  being on ships the submitter did not own, but a log that cries wolf will hide a
  real rejection, and the legality gate in C4 has to be able to trust it.

  Still unhandled, each needing its own measurement before it can be accepted:
  production queues, research topic (`Owner:144`/`Owner:152`), job allocation,
  facility selection, ship designs, governors, admirals, diplomacy proposals.
  Orders issued through an admiral are also out of scope, since the admiral id
  sits inside `DYNO` and would be copied with the order.
- `[ ]` **C3. Immediate-effect actions.** Hurry production, conscription, crew
  assignment and job reallocation take effect the moment they are clicked, so the
  diff shows the *effect* and not the intent. Each needs a reverse mapping, effect
  back to intent, which the referee then performs itself so it applies the cost.
  **Done when:** each action is either mapped or explicitly refused, with a list
  of which.
- `[ ]` **C4. Legality gate.** Encode the rule the UI enforces rather than
  inferring legality from an observed state.
  **Done when:** the gate cites a rule for every accepted order type.
- `[x]` **C5. Get a player's state back without their help.** `SaveGame` at
  `0x0048B350` has exactly one caller, the Save/Load dialog, so the client never
  uploads on its own. `client/dev_tools/trigger_save.py` calls it in a remote
  thread and `player_turn.py collect` wraps that. Every submission in the
  two-player round arrived this way, the player having only given their order.

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
- `[~]` **D2. Per-player projection.** Every player is now handed a state
  *derived* from the authoritative one rather than the authoritative one itself,
  because each copy is stamped with that player's own civ (B5). That is the whole
  of the derivation so far: the content is identical and only the identity
  differs.

  **The engine hides more than expected on its own.** A player who holds the full
  galaxy still sees only the planets in systems they have entered and only the
  ships within scan range, roughly 30 units. Measured across a whole session: two
  civs nine systems apart could see nothing of each other, two civs sharing a
  system saw each other's planets but not each other's ships, and a ship became
  visible the moment it closed the distance. So the leak in full-state
  distribution is not what the UI shows, it is what a modified client could show.
  **Done when:** a player's copy omits what that player has not discovered,
  rather than relying on the client to decline to draw it.
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
