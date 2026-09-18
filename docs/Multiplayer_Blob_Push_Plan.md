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
- **The homeworld customisation allowance was never the problem it looked like.**
  Its *effects* are in the blob (`Planet:104` space, `Planet:96` the per-unit
  rates), and re-spending it is additive and unbounded, so a state that re-offers
  it would let a player ratchet their economy upward every turn. But the four
  spent-counters at `0x00842AE4` are **not `.data` globals**: they have no
  absolute references in `.text` and are fields of the prompt's own singleton at
  `0x00840BC8 + 0x1F1C`, which `0x00498E10` clears every time the prompt opens.
  They read zero after a load because the dialog is built once per process, not
  because a save failed to carry them. And the prompt only appeared at all
  because we had NOPped its guards, see B4.

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

- `[x]` **B1. Push a turn and relaunch, launcher-driven.** Confirmed live on
  18 September 2026: a player clicked Multiplayer once, played turn 8, walked
  away, and came back to turn 9 open and playable. Nothing manual in between.

  The pieces:

  | | |
  |---|---|
  | `server/turn_store.py` | the one thing a player's launcher, the other players' launchers and the referee agree through |
  | `server/player_turn.py follow` | the player-side loop: serve, hold the clock, collect at the deadline, submit, wait |
  | `server/referee.py --loop` | resolves each turn as its deadline passes |
  | `release/launcher.py` | runs `follow` on a worker thread and shows the countdown |

  The measured handoff was **35 seconds** from "time is up" to the next turn
  being playable:

      00:03:05  serving turn 8
      00:09:09  time is up, captured, submitted
      00:09:42  the referee's tick capture arrives
      00:09:44  serving turn 9
      00:09:50  turn 9 up

  The turn store is deliberately the whole interface. A directory is enough for
  one machine and for a shared folder between two, and the Firebase adapter the
  deployment wants replaces that class without changing a caller.

  Three things this cost that are worth not rediscovering:

  - **The referee and a player's launcher must agree where captures land.**
    A capture arrives over `cs_server`'s `savegame` endpoint, so it lands in
    whatever data directory that server was started with. The launcher hosts its
    own against `release/data`, while the referee defaulted to the checkout's
    `server/saves`, and the referee then waited for a file being written
    somewhere else. Both take the directory explicitly now, `--save-dir`.
  - **On one machine the referee and the players share a single game process.**
    `tick` closes whatever is running before starting its own, so resolving the
    instant a deadline passed closed the player's client mid-capture and lost the
    orders it was writing. The referee now holds a grace period for submissions
    and then waits for the client to exit. With the referee on another host both
    waits become free.
  - **A mode that starts the game itself names no EXE.** Making the multiplayer
    card playable made `find_game_root` demand an EXE the manifest never claimed,
    and the launcher decided the whole install was broken. `is_playable` now
    recognises a `"session"` mode.

  `[ ]` **Not packaged.** The launcher's multiplayer path imports from the
  checkout, so it runs only from a clone. Shipping it means bundling
  `save_parser`, `set_blob_player`, `turn_store` and the serve and collect logic
  into the frozen build, which is a `build.ps1` change.
- `[x]` **B2. Stop the player's client ticking on its own.** Confirmed live: a
  player's client sat on a served turn for six minutes, never advanced, never
  bailed, and the launcher showed the real countdown beside it. Two things get
  that: the player build is the one without T1-T5, so the engine's own sync
  checks are intact, and `player_turn.py serve` writes a day onto `0x0080AA08`
  after load so no boundary arrives during a session. The in-game countdown then
  reads about 24 hours, which is the artefact of the hold and is why the launcher
  owns the display.

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
- `[x]` **B3. Restore non-blob state after load.** Nothing needs restoring. The
  four homeworld allowance counters were the only candidate, and they turned out
  to be the prompt's own UI state rather than game state, cleared whenever the
  prompt opens. On the player build the prompt does not open, so there is no
  allowance to re-offer and nothing to put back. `homeworld_clicks.py` remains
  useful for driving the prompt deliberately.
- `[x]` **B4. Neutralise the setup prompts on the served path.** Confirmed live
  in September 2026: a served turn now comes up straight into the galaxy with no
  prompt of any kind, from a blob with nothing faked, and is playable.

  There are **three** prompts, not one, each its own Win32 dialog resource. They
  are real dialogs rather than in-engine overlays, and a child dialog leaves the
  main window's title reading `Galaxy Map`, so
  `client/dev_tools/list_dialogs.py` walks the process's windows instead. That
  turns "is a prompt up" into a command rather than a question for a human.

  | id | prompt | how it is handled |
  |---|---|---|
  | 210 | Customize Your Home World | gated as shipped, no patch needed |
  | 218 | Customize Your Civilization | gated as shipped, no patch needed |
  | 225 | Civilization Name and Coat of Arms | one byte, `0x0056E700` returns |

  **210 and 218 needed no fix at all, because the bug was ours.** Their decision
  routine `0x0056E0D0` has two guards, and two of the six T1-T5 bypasses land
  exactly on them: `0x0056E0EF` (`JNZ`, 6 bytes) and `0x0056E133` (`JZ`, 2
  bytes). With both NOPped the prompt is offered unconditionally. A player's
  client should be `CosmicSupremacy_TestBed.exe`, which is the same binary
  without T1-T5, so this and B2 have one answer. `game_cycle.resolve_exe` makes
  the build a parameter and `player_turn.py` defaults players to testbed while
  the referee keeps resurgence.

  **225 is gated on the local civ's `Owner:384`, and that field is a count, not a
  flag.** A civ's `OWPR` section is exactly `138 + Owner:384` bytes, measured at
  0, 1 and 7, so writing a value appends that many one-byte records of undecoded
  meaning; civilisation traits are the obvious candidate, `GSET
  .civilization_changes` being 5. Setting it to 1 does silence the prompt and
  does survive a save and reload, and it was still rejected as the fix: it
  fabricates per-civ state to suppress a cosmetic dialog.
  `client/dev_tools/patch_hide_setup_prompts.py` returns from the decision
  routine instead.

  A `coaid` answer was also tried and is **not** the gate: the client fetched
  `getcoa&coaid=1` happily and offered the prompt anyway, so `cs_server.py` is
  unchanged.
  `[ ]` **Build the one-time civilisation setup step the prompt exists for.** In
  the original a player chose a name and coat of arms once and the blob carried
  it forever, which is why the field was non-zero and the prompt never returned.
  Our galaxies are generated locally and skip that flow entirely. The patch
  stands in for it; the launcher or the site should eventually offer it, and then
  `Owner:384` would be set by the engine rather than guessed at.
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

  `[x]` **Every submission is judged against the state served**, not against the
  authoritative blob as it evolves. Applying one player's orders first moved the
  state under the next player, whose untouched copy of a ship then differed from
  it and read as an attempt to change what they did not own: the two-player round
  logged two such drops with neither player having done anything. Re-running that
  same round now reports **3 orders taken and 0 dropped**, same merged size.

  `[x]` **A forged owner field cannot take a ship.** Ownership is read from the
  served state, never from the submission. Tested by rewriting one of
  DemoPlayer's ships to claim Neighbor owned it, planting a real order on it and
  submitting as Neighbor: the change was dropped and named, `ship 201: DROPPED,
  owned by DemoPlayer`, the ship came through the merge untouched, and Neighbor's
  own legitimate order still applied.

  `server/dev_tools/order_diff.py` is the measurement tool for everything below:
  given the bytes served and the save returned, it reports which objects changed,
  who owned each one at the start of the turn, and which section inside it moved.
  Objects are matched by object id rather than by tree position, because the
  client writes the civs back in its own order and a positional diff of one
  galaxy reports every civ as changed.

  **Three more order types were measured in September 2026**, each by serving a
  turn, having a player perform exactly one action, and diffing. Two are
  accepted and one is refused with a reason.

  | action | where it lives | verdict |
  |---|---|---|
  | research topic | `OWNR > DATA > OWPR` `+32` and `+40` | accepted, field by field |
  | production queue | `PLNT > PLPR > PROD` | accepted, whole section |
  | job allocation | the citizen array at `PLPR+40` | accepted, array only |

  **Research topic.** Setting one writes the technology id to both `OWPR+32` and
  `OWPR+40`, eight bytes apart, matching the predicted `Owner:144`/`Owner:152`
  pair. Confirmed against the AI's own table: the id written for "Advanced
  Magnetism" was 12, which is what `research.py` calls it. Copied field by field
  rather than by section, because `OWPR` is the civ's whole property block and
  also holds the coat-of-arms count and several flag bytes.

  **Production queue.** Queuing a facility changed only `PLPR > PROD`, whose
  payload holds the queue's own fields and a nested section naming what is
  queued, `FCLT` for a facility. Self-contained, so it is copied whole.

  **Job allocation, once `PLPR` was decoded.** `PLPR+36` is a `u32` population
  count and `PLPR+40` begins that many nine-byte records: `+0` the job id, `+3`
  the owning civ's object id, `+7` a per-citizen value. Confirmed by having a
  player move one farmer to a banker, which took the array from seven farmers,
  two workers and a scientist to six farmers, two workers, a scientist and a
  banker, matching the live population vector at `Planet:144` exactly. The
  array is kept sorted by job, so a reassignment reorders it rather than editing
  one byte, which is why the first diff looked like values shifting along.

  Only the array is copied, never the rest of `PLPR`, which carries the planet's
  stores and derived economy. Four checks gate it, each tested by forging a
  submission that breaks it:

  | forged | refused with |
  |---|---|
  | a citizen changes hands | the citizens changed hands |
  | population invented | population changed, 10 to 9 |
  | an unknown job id | unknown job id(s) [99] |
  | a per-citizen value invented | values were invented rather than reordered |
  | an unknown byte written | unknown bytes in a citizen record were written |

  All five refused, with the honest submission still accepted.

  **A planet's population can belong to more than one civ**, so the per-citizen
  owner id is load-bearing: one colony in the test galaxy holds citizens of two
  different civs. The checks therefore compare per-owner job multisets and drop
  any submission that reassigns a citizen the submitter does not own, even on a
  planet they do.
  `[ ]` **Citizens a player owns on someone else's planet cannot be managed**,
  since only planets the submitter owns are considered. Allowing it is probably
  right, the per-citizen owner already says whose it is, but it has not been
  measured.

  **The rules were tested against a submission containing all three changes at
  once.** Two were applied and nothing else: the job change, the `EXSY`/`KNPL`
  movement and the `OWPR` flag byte were all left behind.

  **`EXSY` and `KNPL` move on their own and must never be taken as orders.** A
  player who only set a research topic still returned a changed explored-systems
  table and a known-planets table that grew from 0 records to 1. Two captures
  taken in the same session with nothing done between them were byte-identical,
  so this is not drift: it is bookkeeping the client does when it loads. The
  referee recomputes both when it ticks the authoritative state, so a player's
  copy has no business overwriting them.

  Still unmeasured, and therefore not accepted: facility selection outside the
  queue, ship designs, governors, admirals, diplomacy proposals. Orders issued
  through an admiral are also out of scope, since the admiral id sits inside
  `DYNO` and would be copied with the order.
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
