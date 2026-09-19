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

- [x] **A1. Package the referee loop as a component.** `server/referee.py`
  exposes `apply_orders(blob, submissions)` and `tick(blob, turns)`, kept apart so
  a Python reimplementation of the rules would replace `tick` alone and every
  caller would keep working. `tick` writes the blob out, starts a client on it,
  drives the clock, captures and closes. Confirmed advancing a three-civ galaxy
  from turn 3 to turn 7 with no human present.
  [x] **The referee's client is stamped as one of the civs**, since every blob
  names a local player, and it does not matter. If the engine had stopped
  playing whoever the client is, every tick would quietly disadvantage one
  empire, and in a hosted galaxy that empire is whoever `GLOB` happens to name.

  Measured: one state ticked 40 turns three times, twice stamped as the same civ
  and once as the other. The control reproduced exactly, and the two stamps came
  out **byte-identical once the stamp itself is normalised away**. Normalising
  matters, because stamping two runs differently guarantees the local-player
  field differs and a differing hash on its own says nothing; the first reading
  of this experiment was "DIFFERENT" and meant only that.

  Scope, since the galaxy has to be able to show a difference for the absence of
  one to mean anything: the blob grew 544 bytes over those turns, so research,
  production and stores all progressed, but ship and planet counts never moved.
  This covers economic decisions, not expansion or combat. An 8-turn version ran
  first and proved less than it appeared to, since nothing in the galaxy changed
  at all over that span.
- [x] **A2. Canonical hash.** `server/canonical.py` zeroes the trailing `u32`
  of every `KNPL` payload and hashes what is left. Two independent ticks of
  turn 9, in separate processes, produced the same canonical hash. As it
  happened they produced the same raw hash too, so the volatile field did not
  move in that pair; the mask still earns its place from the six captures that
  first found it, where an AI civ's low bytes differed between otherwise
  identical runs and the top byte read `0xFF` after any load.

  **The mask is deliberately narrow: 8 to 12 bytes, 0.03% of a blob**, measured
  across the archive. Everything else is compared, so a divergence anywhere else
  shows up rather than being quietly forgiven. Passing two blobs compares them
  after masking and names where they still differ, which is how a second
  volatile field would be found.
- [x] **A3. Archive every turn** as blob plus orders plus hash. Each turn the
  referee closes writes a record holding the canonical hash of the state it
  started from, of the state it published, and of every submission it used.
  Hashing the submissions matters: without them a rerun that disagrees cannot be
  told apart from a rerun given different orders.

  `referee.py --store <dir> --verify <turn>` asks two questions that fail
  differently. Does the stored blob still match the hash written when it was
  published, which catches an archive that has been corrupted or edited; and
  does running the turn again produce the same answer, which catches a referee
  that computed something different. The second costs a real tick, so
  `--no-recompute` skips it.

  **Verified with orders that actually apply.** The first run verified a turn
  whose merge took nothing, which is a weaker claim than it looks: a
  recomputation that applies no orders cannot show that applying them is
  reproducible. Redone by submitting the capture holding a research topic, a
  production queue and a job reassignment: both the close and the recomputation
  took 3 orders and produced `c2f1e1e08bd7cd48`.

  A turn archived before hashes existed reports that rather than passing
  vacuously.
- [x] **A4. Determinism under combat.** A played-versus-loaded pair agrees
  across a war, and so does a loaded-versus-loaded pair, which is the case the
  referee actually exercises.

  The setup: `cycle.dat` at turn 110, war declared between the two civs through
  the AI's own `declare_war` actuator, and both of GoodGuy's warships sent at
  BadGuy's only planet some 450 units away. Both branches then ran 70 turns with
  **no external driver**: the orders were already in the state, so the engine
  alone decided the outcome. Combat happened, GoodGuy losing a warship in every
  branch.

  | comparison | result |
  |---|---|
  | loaded against loaded | identical, reproduced three times across separate invocations |
  | played against loaded | 756 bytes apart, and identical once star names are removed |

  **Those 756 bytes are not a divergence.** Every `SUN` section is 43 bytes in a
  client that has been running and 36 in one that just loaded, because a
  long-running client materialises the default display name `Unnamed` where the
  blob holds an empty string. 108 suns times 7 bytes is exactly 756. Strip the
  names and the two branches are byte-identical, same canonical hash, after 70
  turns and a war.

  It was never about combat: a control on the same fixture with **no war and no
  actuator writes** produced the same 756-byte difference. Running the war
  experiment without that control would have recorded "combat diverges", which
  was the available and wrong conclusion.

  [ ] **The engagement was one-sided**, an armed attacker against an unarmed
  defender and its planet. It exercises targeting, damage and destruction, not a
  pitched two-sided battle, and BadGuy had no warship design to fight back with.
  Worth repeating once both sides can shoot.

  Star names are deliberately **not** masked in `canonical.py`, because masking
  a name field to forgive a default would also forgive a change to it.

  `Unnamed` is what a running client puts in a `SUN ` section when the blob
  leaves the name empty. Each civ's `EXSY` also holds names, which briefly
  looked like evidence that naming is per player; it is not. `EXSY` is that
  civ's cache of names it has seen, and renaming is gated in game on owning the
  majority of a planet, so a name is authoritative rather than private.

## B. Client lifecycle, once per turn

- [x] **B1. Push a turn and relaunch, launcher-driven.** Confirmed live on
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

  [ ] **Not packaged.** The launcher's multiplayer path imports from the
  checkout, so it runs only from a clone. Shipping it means bundling
  `save_parser`, `set_blob_player`, `turn_store` and the serve and collect logic
  into the frozen build, which is a `build.ps1` change.
- [x] **B2. Stop the player's client ticking on its own.** Confirmed live: a
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
- [x] **B3. Restore non-blob state after load.** Nothing needs restoring. The
  four homeworld allowance counters were the only candidate, and they turned out
  to be the prompt's own UI state rather than game state, cleared whenever the
  prompt opens. On the player build the prompt does not open, so there is no
  allowance to re-offer and nothing to put back. `homeworld_clicks.py` remains
  useful for driving the prompt deliberately.
- [x] **B4. Neutralise the setup prompts on the served path.** Confirmed live
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
  [~] **Build the one-time civilisation setup step the prompt exists for.** In
  the original a player chose a name and coat of arms once and the blob carried
  it forever, which is why the field was non-zero and the prompt never returned.
  Our galaxies are generated locally and skip that flow entirely. The patch
  stands in for it; the launcher or the site should eventually offer it, and then
  `Owner:384` would be set by the engine rather than guessed at. deferred for a later phase, for now we should just set the player name as their user id and ignore the coat of arms image until we come back to this feature. 

  **The name half of that exists now, in the launcher.** `release/launcher.py`
  keeps one name per player in `identity.json` in the data directory, asks for
  it in a dialog the first time a player opens a multiplayer galaxy, and uses it
  as the civ it follows and submits under. A player's user id and their
  civilisation name are therefore the same string, which is what this bullet
  asked for. The coat of arms is still untouched and `Owner:384` is still the
  patch's business, not the launcher's.

  What the launcher does **not** do is write the name into the blob. The name a
  player enters has to match a seat the galaxy was generated with, and the
  launcher checks it against `TurnStore.civs()` and names the seats that exist
  when it does not. Renaming a seat from the client is the part still waiting on
  this feature.
- [x] **B5. Tell a client which civ it plays.** Two clients sharing a galaxy
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

- [x] **C1. Diff engine.** Compare the exact bytes handed to a player against
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

  [x] **Every submission is judged against the state served**, not against the
  authoritative blob as it evolves. Applying one player's orders first moved the
  state under the next player, whose untouched copy of a ship then differed from
  it and read as an attempt to change what they did not own: the two-player round
  logged two such drops with neither player having done anything. Re-running that
  same round now reports **3 orders taken and 0 dropped**, same merged size.

  [x] **A forged owner field cannot take a ship.** Ownership is read from the
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

  **What the per-citizen owner id is for is not established, and it is not
  shared populations.** It equals the planet's owner on every naturally created
  planet measured. One colony held citizens carrying another civ's id, which was
  briefly written up here as evidence that a population can be split between
  empires. It is not: the planet's owner owns the working population, and that
  colony was founded by a ship `inject_ship.py` had cloned, which kept the donor
  civ's id at two places the tool rewrote nothing at. Fixed, and the fixture
  galaxy still carries the artifact. The field may exist for mid-tick
  bookkeeping, ownership of soldiers during a battle being the obvious
  candidate, but that is a guess and nothing depends on it.

  The checks still compare per-owner job multisets, because whatever the field
  means, a turn in which a citizen changes hands is not a job reassignment.

  **The rules were tested against a submission containing all three changes at
  once.** Two were applied and nothing else: the job change, the `EXSY`/`KNPL`
  movement and the `OWPR` flag byte were all left behind.

  **`EXSY` and `KNPL` move on their own**, so they cannot be taken wholesale. A
  player who only set a research topic still returned a changed explored-systems
  table and a known-planets table that grew from 0 records to 1. Two captures
  taken in the same session with nothing done between them were byte-identical,
  so this is not drift: it is bookkeeping the client does when it loads.

  **`EXSY` holds names, but it is a cache rather than a record of decisions.**
  Each civ's `EXSY` pairs an object id with that civ's name for it, defaulting
  to `Unnamed`, which briefly looked like per-player naming and is not. One
  player's table gained `Neighbor's HQ` purely from loading a turn, with that
  player having done nothing: it is what the civ has seen, and the referee
  recomputes it. It stays excluded.

  **A rename is authored on the object.** `PLNT` own payload `+24` is a `u32`
  length then the characters, measured by having a player rename a colony, which
  changed that field and nothing else on the object. The game gates it, refusing
  with "you need to own the majority of the planet to rename", so a name is
  authoritative galaxy data, not a private label. Carried for planets the
  submitter owns, with the name checked for length and for printable bytes.

  | forged | refused with |
  |---|---|
  | rename another civ's planet | rename DROPPED, owned by Neighbor |
  | control bytes in the name | name holds bytes outside printable ASCII |
  | a 200-byte name | the name field is unreadable or longer than 63 bytes |

  **Renaming a system** writes the `SUN ` section's name, at the same `+24` in
  its own payload. Measured on a galaxy where one civ held five of a system's
  six planets: the name appeared on the `SUN `, and **the renamer's own `EXSY`
  cache picked it up while the other civ's did not**, which is why another
  player keeps seeing the old name until they observe the change. A system
  belongs to nobody, so the right to rename it is the game's own rule, owning
  more than half its planets, read from the served state.

  | forged | refused with |
  |---|---|
  | a minority holder renames it | BadGuy holds 0 of 6 planets, not a majority |
  | control bytes in the name | name holds bytes outside printable ASCII |
  | a 200-byte name | the name field is unreadable or longer than 63 bytes |

  **`order_diff.py` could not see this change at all** until `SUN ` was indexed.
  It keyed objects on `OWNR`, `PLNT` and `SHIP`, so a system rename showed up as
  a change to the renamer's `EXSY` and nothing else, hiding the write that
  actually mattered. Galaxy-level objects are now reported as unowned.

  Still unmeasured, and therefore not accepted: facility selection outside the
  queue, ship designs, governors, admirals, diplomacy proposals. Orders issued
  through an admiral are also out of scope, since the admiral id sits inside
  `DYNO` and would be copied with the order.
- [ ] **C3. Immediate-effect actions.** Hurry production, conscription, crew
  assignment and job reallocation take effect the moment they are clicked, so the
  diff shows the *effect* and not the intent. Each needs a reverse mapping, effect
  back to intent, which the referee then performs itself so it applies the cost.
  **Done when:** each action is either mapped or explicitly refused, with a list
  of which.
- [~] **C4. Legality gate.** Encode the rule the UI enforces rather than
  inferring legality from an observed state.
  **Done when:** the gate cites a rule for every accepted order type. defferred for a later cheat prevention phase. 
- [x] **C5. Get a player's state back without their help.** `SaveGame` at
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
- `[~]` **D3. Turn clock and absent players.** The clock half is done and the
  absent-player half has an answer nobody had asked for.

  **The galaxy advances on schedule with a player missing.** Measured repeatedly
  during the two-machine runs: turn 11 closed with one player's submission never
  arriving and the referee logged `no submission from ['Neighbor']; the clock
  does not wait`, published the next turn, and the other player carried on.

  **An absent empire coasts, and then stalls.** Governors and admirals are the
  original's answer and they are not in the beta, so what an absent player
  actually gets is worth knowing rather than assuming. One galaxy ticked 40
  turns with no orders from anyone:

  | | |
  |---|---|
  | population | grew on every planet, 15 to 19 and so on |
  | new citizens | **all went to farming**, the default, never rebalanced |
  | production | the queued facility completed and the queue went to the empty marker, never refilled |
  | research | one civ held its topic, the other had **none and never chose one** |
  | ships, colonies | unchanged, nothing built, nothing settled |

  So an absent player is not destroyed, and in this fixture they were not
  played for either: their economy drifts toward farmers, their factories go
  idle and their research stops. That is survivable for a turn or two and is a
  slow death over a week, which is exactly what governors existed to prevent.

  **The design settles what an unplayed civ should get, and it is nothing.** In
  multiplayer no civ receives orders unless a human sent them, whether that civ
  is an absent player or a leftover `BadGuy`. Single player is the opposite and
  already built: `BadGuy` there is driven by the external order generator in
  `client/dev_tools/ai_player/`, which is published and does not depend on the
  engine deciding anything.

  **That reframes the open question rather than closing it.** If the engine
  issues nothing for an unplayed civ, the design holds for free. If it does
  issue orders, then an absent player's empire is acting on decisions nobody
  authorised, which is a fault to suppress rather than a feature to keep. So
  the answer still matters; what changed is which answer is the bad one.

  [x] **Measured: the engine issues nothing during a tick, and the `ROUT` was
  there before it.** The instrument is a galaxy built by
  `server/dev_tools/make_multiplayer_galaxy.py`, where every ship starts with
  order type 0, no `ROUT` and has-orders clear, so anything non-zero afterwards
  was issued during the tick rather than carried in. Eight turns, three civs,
  nothing submitted by anyone: no ship gained an order, no production queue
  changed, no research field was set, and the only movement in the whole blob
  was population growth. Stamping the tick client as the second civ rather than
  the first gave the same answer, so the result is not an artefact of which seat
  the client plays.

  What the second machine saw is explained without the engine acting. **Every
  generation gives the second civ's first ship a Scout order, type 2, with an 82-byte
  `ROUT` at turn 0**, before anyone has played, in all five generations captured
  here. A ship advancing along that order looks exactly like a ship being given
  one, unless the `DYNO` is compared against the state served, which is the
  discriminator that was asked for. The generator now clears it.

  Narrow where it should be: this was a fresh galaxy whose queues were the ones
  generation left. A mid-game empire whose queue drains to nothing is the case
  the 40-turn fixture covered, and there too nothing refilled it.

  **The same is true of every civ with no human in it, and by design that case
  does not arise.** A multiplayer galaxy is humans only: no `BadGuy`, no engine
  opponent, one civ per player. So the fact that an unplayed civ is scenery
  rather than an opponent costs nothing, and the finding narrows to the case
  that does matter, a human who misses their turn.

  `BadGuy` is present in the test galaxies on this branch because they were
  grown from single-player fixtures. It holds a seat and does nothing, which is
  harmless for testing and would be wrong in a real galaxy, so galaxy creation
  for multiplayer makes exactly as many civs as there are players:
  `server/dev_tools/make_multiplayer_galaxy.py`. Single-player is unaffected:
  there the external `ai_player` is the opponent, and it does not rely on the
  engine deciding anything.

  **A generation does not hand its two civs equal worlds.** The second civ's
  homeworld reads the same two `PLPR` bytes in every generation captured here,
  `+4` = 32 and `+11` = 44, while the first civ's read 62/94 or 42/194: the
  first carries the homeworld customisation the setup screens apply and the
  second gets the engine's opponent default. `PLPR+4` is `Planet:96`, the
  per-unit output rates. It is worth two bytes of attention because it decides
  games: eight turns with no orders from anyone grew seat one from 7 citizens
  to 9 and left the other seats at 7, and after levelling the same run gives
  9, 9, 9. A civ added by `inject_civ` inherits its donor's world, so it
  inherits whichever of the two it was cloned from.

  [ ] **Whether this is the engine or our own patching is untested, and it
  matters.** The referee ticks on the Resurgence build, which carries the six
  T1-T5 sites in the turn pipeline, and whether one of them skips an AI phase is
  unknown. The obvious control, ticking the same galaxy on the unpatched build,
  is not available: without T1-T5 a client will not advance a turn without a
  server, which is the whole reason those patches exist. If our patches are the
  cause it is fixable; if the engine simply does not run AI down this path, the
  empty seats need filling another way.
- [x] **D4. More than two civs in a galaxy.** `server/dev_tools/inject_civ.py`
  adds a whole player to a blob, confirmed live with a third civ that owned a
  homeworld and played four turns.

## G. Operating it

- [x] **One machine, one game process, now with a lock.** A machine that both
  plays and referees has two things wanting the single client, and nothing
  arbitrates. `referee.tick` closes whatever is running before starting its
  own, and `wait_for_client_free` gives up after 180 seconds and ticks anyway,
  so a referee waking on its deadline will close a player's client mid-turn, or
  an experiment will close the referee's. Nearly happened twice in one session:
  once a referee loop and an unrelated experiment were started against the same
  machine, and it was luck that the player loop had already exited.

  `game_cycle.take_client_lock` settles it. `launch` claims the machine's one
  game process naming what it is for, `close_client` releases it, and a second
  tool is refused with the holder's pid and purpose rather than silently winning
  the race. The referee passes `wait_for_lock=240`, because it can afford to
  wait and a player mid-turn cannot afford for it not to; a player's serve
  refuses at once. A holder that died leaves a stale lock, cleared by asking
  whether the pid is alive rather than by a timeout, since a legitimate hold
  lasts a whole turn.

  It is advisory: nothing stops a tool calling `Popen` itself, and every path in
  this project goes through `launch`. On separate machines the problem does not
  exist, which is why it survived this long unnoticed.

## F. Off one machine

- [x] **F1. Address the turn store over HTTP.** `server/turn_server.py` puts a
  store behind a URL and `HttpTurnStore` speaks to it, so
  `open_store("http://host:8899")` and `open_store("some/dir")` are
  interchangeable. Every caller takes a string and never learns which it got.

  Verified two ways. An equivalence test runs the same sequence against a
  directory and against the service in front of that same directory and checks
  the directory's own view agrees, 24 checks covering start, publish, submit,
  the submission list, the archive, missing turns and the factory. Then the
  referee resolved a real turn **entirely over HTTP**: it read the state,
  fetched the turn, read submissions, ticked, published turn 11 and archived
  turn 10, and the directory and the URL then reported the same canonical hash.

  This is the seam the deployment needs. A Firebase adapter replaces the service
  and `HttpTurnStore` keeps working, or replaces `HttpTurnStore` and the service
  is no longer needed. Nothing above the store changes either way.

  [~] **There is no authentication.** Anyone who can reach the port can
  publish a turn or submit as any civ. Deliberate for a closed beta among people
  who know each other, and the first thing that has to change before a galaxy is
  open to strangers. The ownership rules in `merge_orders.py` still hold, so the
  worst a stranger can do through this door is submit nonsense as someone else,
  not acquire their ships. we don't need auth in the client just yet, but we will at least need people to identify themselves by a username within the launcher. it could be something very simply. no password. no hashing. eventually we'll have a google login in the launcher or login on the website.

  **The username half exists now.** The launcher asks for a name once, keeps it
  in `identity.json` beside its other per-player state, and shows it as "Playing
  as" with a link to change it. There is still no password, no hashing and no
  account: the name is a claim, not a credential, and anyone who can reach the
  store can still submit as anyone. What it buys is that a player types their
  name in one place instead of hand-writing `civ` into `multiplayer.json`, and
  that a name which is not in the galaxy's roster is refused with the list of
  seats that are, rather than submitting into a void.

  A Google login in the launcher or on the website replaces where the name comes
  from and nothing else: everything above `player_name()` already takes a string.
- [x] **F2. Two machines.** Done, 18 September 2026. Two PCs played one
  galaxy over a shared folder, each with its own client, its own `cs_server`
  and its own launcher config, sharing only the store. Turn 12 closed with both
  submissions present and the other machine's colonise applied:

      referee: closing turn 12 with 2 submission(s)
      Neighbor (object 206):
          ship 208: order taken (38 -> 132 bytes)
      submitted: ['DemoPlayer', 'Neighbor']   missing: []

  The store was addressed as `\\HOST\Sharing\cosmic\galaxy1` from one side
  and by a different spelling of the same host from the other, which is itself
  a finding: **a host spelled as an address and the same host spelled by name
  are different SMB targets**, and a machine holding a session to one is
  refused on the other. That cost a round of wrong guesses between the two
  machines, and `check_store.py` now tries the other spelling and says so.

  Turn 11 was lost first: that machine's `cs_server` was not running, so
  `SaveGame` failed and there was nothing to submit, and the referee reported a
  missing player, which reads as somebody who did not turn up rather than a
  write that failed. Three changes came out of it, all in `player_turn.py`:
  the save path is checked **before** a turn is served rather than at the
  deadline when it is already spent; submissions go every 20 seconds through
  the turn instead of once at the end; and each one reports what it carries, so
  an empty turn is visible as an empty turn.
- [x] **F3. The launcher takes a URL.** It does now. It did not: the plan said
  the `store` string was "passed to `open_store`", and the launcher in fact
  named `turn_store.TurnStore` directly, so a URL meant a directory called
  `http:` and the player was told there was no galaxy there. One line, now
  `open_store`.

  Verified through the launcher's own path rather than around it:
  `multiplayer_config` read a `multiplayer.json` holding
  `"store": "http://127.0.0.1:8907"`, `multiplayer_modules` supplied the
  checkout's `turn_store`, and `open_store` returned an `HttpTurnStore` that
  reported `exists`, turn 8, both civs and the live countdown off a
  `turn_server.py` in front of a throwaway store.

  **This is the shape of bug F2 will keep finding.** Nothing about the store
  seam is wrong; the callers above it were written when a store was always a
  folder, and each one has to be looked at rather than assumed.
- [~] **F4. A Firebase adapter**, replacing either side of the seam. deffered until we prove it works using my own PC as the server with some real beta players. 

## E. Not applicable on this path

- The `CMND` command protocol, `RQSV`'s reply format, and live-versus-replay
  mode. All on `galaxy-protocol`.
- The client build-id checksum and the machine and user names, which the client
  sends in `LGIN`. This path never issues `LGIN`, so neither the tamper-detection
  opportunity nor the privacy question arises.
