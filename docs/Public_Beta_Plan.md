# Public beta, plan

The strategy: one permanent sandbox galaxy, relayed through Firebase, refereed
by one PC at home. Players join whenever they arrive, are merged into the live
galaxy at the next turn boundary, and play 4-hour turns. Identity is a username
and nothing more.

This is the phase after the blob push plan, which established that a turn is a
pure function of state plus orders, that a referee can resolve one unattended,
and that two machines can share a galaxy over a store. All of that ran on a LAN
between machines that trusted each other. This phase puts a stranger at the
other end of it.

That plan is finished and is no longer in the tree. Its findings that outlive it
are in `docs/CosmicSupremacy_Reconstruction_Report.md`; the plan itself, and the
item ids this document cites, read at
`git show ae2b06b:docs/Multiplayer_Blob_Push_Plan.md`.

**What this phase is not.** Turns are still resolved on one PC with a real game
client attached. Moving the referee to a cloud VM is the phase after this one,
and every item here is written so that move is a deployment change rather than a
rearchitecture.

Each item names what "done" looks like.

---

## What this phase inherits

These are measured, in the blob push plan at the commit named above, and this
plan depends on them. The letters in brackets are that plan's item ids.

- **The store seam holds.** `open_store(spec)` takes a directory or a URL and
  every caller above it takes a string and never learns which it got. A third
  implementation is the whole of the Firebase work. (F1, F3)
- **An unplayed civ is scenery.** The engine issues no orders for a civ nobody
  plays: no ship order, no production, no research, only population growth. So
  an abandoned empire drifts rather than acting. (D3)
- **A player's client cannot tick.** The player build carries no turn-advance
  patches and offers no Next Turn control. (B, G)
- **Every player holds the whole galaxy.** Per-player projection does not load,
  so distribution is full-state and a modified client is a maphack. The engine's
  own visibility rules hide rivals' planets and ships from an unmodified one.
  (D1, D2)
- **A civ can be added to a live blob.** `inject_civ.add_civ` clones a donor
  `OWNR`, places a homeworld, and maintains the `GLXY` civ count and the
  high-water object id. Confirmed live with a third civ that played four turns.
  (D4)
- **One game process per machine, with an advisory lock.** Every tick serialises
  through it. (G)

`F4` in that plan, the Firebase adapter, is deferred there and is `H1` here.

---

## Decisions this plan is built on

Settled before writing it, and recorded so an item that contradicts one is
visibly wrong rather than quietly inconsistent.

| | |
|---|---|
| galaxies | one permanent sandbox, ended at the operator's discretion, no season timer |
| turn length | 4 hours |
| joining | civs merged into the live galaxy on demand, never pre-generated as empty seats |
| identity | a username, plus an anonymous per-install UID the player never sees |
| abandonment | the civ is wiped from the galaxy; the seat is not passed to anyone |
| cheating | possible and accepted, and players are told so |

Inheriting an abandoned empire rather than wiping it is a future game mode and
is out of scope here.

---

## H. The relay

- [ ] **H1. A Firebase store adapter.** A third implementation of the
  `turn_store` interface, alongside `TurnStore` and `HttpTurnStore`. Blobs and
  submissions in Cloud Storage, the clock and roster and archive index in
  Firestore, because a blob has no size ceiling worth betting on and a Firestore
  document does.

  One class of bug disappears rather than being ported. `TurnStore.state` retries
  for two seconds because `os.replace` is not atomic over SMB and a reader can
  see `state.json` briefly absent. A Firestore document replace is atomic and
  transactional, so the adapter needs no equivalent.

  **Done when:** the 24-check equivalence test F1 ran against a directory and
  against the HTTP service passes a third time against Firebase, and a referee
  resolves a real turn end to end through it, reading the state, ticking,
  publishing and archiving, with the directory and the Firebase store reporting
  the same canonical hash.

- [ ] **H2. The referee machine is pull-only.** It opens outbound connections to
  Firebase and accepts nothing inbound: no port forward, no dynamic DNS, no
  router change. This is the property that keeps a home address out of the beta,
  and it is a constraint rather than a consequence, because the first "just
  expose `turn_server.py`" shortcut removes it without anything failing.

  **Done when:** a full turn resolves with the machine's inbound ports closed and
  no forwarding rule anywhere, confirmed by reading the router's configuration
  rather than by the turn having worked.

- [ ] **H3. Firebase is the record; the PC is a worker.** State, turns,
  submissions, notes and the archive live in Firebase. The PC holds the game
  client, the client lock, and files it is free to lose.

  Two payoffs, and the second is the reason for the first. A machine that reboots
  for Windows Update loses nothing and resumes. And the cloud offload later swaps
  which machine runs the worker, with no data to migrate.

  **Done when:** the referee is killed mid-galaxy and the machine rebooted, and
  the loop resumes and closes the next turn with nothing restored by hand.

- [ ] **H4. Anonymous auth and security rules.** Firebase Anonymous Auth gives
  each install a stable UID with no login screen, no password and no account.
  The username stays exactly what it is today, a claim typed into the launcher.

  This is not an anti-cheat measure and does not pretend to be. It is there
  because a publicly addressable store with no rules can be emptied by anyone who
  finds it, and because upload and egress are metered.

  The rules: a client may write only its own submission for the current turn; may
  never write a published turn; may never delete. The worker holds a service
  account and is the only writer of turns. Object size is capped and a budget
  alert is set before the first stranger has the launcher.

  **Done when:** a test client authenticated as one player is refused writing
  another player's submission, refused publishing a turn, and refused deleting
  anything, each by the rules rather than by the application declining to try.

- [ ] **H5. Cost and quota arithmetic, written down.** One galaxy at 4-hour turns
  is 6 ticks a day. Per player per turn that is one blob down and one blob up, on
  the order of tens of kilobytes each, which is negligible. The cost that is not
  obviously negligible is reads, because those are driven by how often a launcher
  polls.

  So the launcher polls against the deadline it already knows rather than on a
  fixed short interval: often near the boundary, rarely in the middle of a turn.

  **Done when:** a measured day of a live galaxy is inside the current free tier
  with margin, or the expected monthly bill is written down. The quota numbers
  are read from Firebase's current documentation at the time, not assumed.

---

## J. The lobby

- [ ] **J1. A galaxy directory above the store.** `open_store` addresses one
  galaxy and nothing in the tree has a concept of more than one. The launcher
  needs a list: name, status, turn, deadline, player count, whether you are in
  it.

  Kept as a separate small interface rather than folded into the store, so the
  directory and HTTP store paths keep working for development and for the
  two-machine test in F2.

  **Done when:** the launcher lists the sandbox from the directory and opens its
  store from what the directory gave it, with no galaxy path in any config file.

- [ ] **J2. A Games tab in the launcher.** Replaces the old flow, where the
  website listed active galaxies and a downloaded file was both the galaxy and
  the key. The tab shows the sandbox, its turn, its deadline, how many players
  are in it, and a Join button, and after joining it shows whether this player
  has submitted.

  `multiplayer.json` stops naming a galaxy. What the player has joined is written
  by the launcher, beside `identity.json`, rather than hand-edited.

  **Done when:** a player who has never opened a config file joins the sandbox
  and plays a turn.

- [ ] **J3. Joining is a request resolved at a turn boundary.** The launcher
  writes a join request; the worker applies it during the next tick, on the
  authoritative blob, never on a copy a player holds. A player who clicks Join
  during turn N is playing at turn N+1.

  **Done when:** a join clicked mid-turn produces a playable civ at the next
  turn, and the player is told which system they landed in by a note rather than
  having to find it.

- [ ] **J4. A seat is bound to the anonymous UID that claimed it.** A second
  install claiming a name already in the roster is refused. Because the UID is
  per-install, a player who reinstalls Windows would otherwise be locked out of
  their own empire, so the operator can rebind a seat to a new UID.

  The refusal still does not name the other players, for the reason F4 already
  gives: a launcher that could list the roster makes "is there a seat for me"
  into a way to enumerate who is playing.

  **Done when:** a name in the roster is refused from a second install, allowed
  from the install that claimed it, and rebindable by the operator.

---

## K. The sandbox galaxy

- [ ] **K1. Merge a new civ into a live galaxy without taking a planet someone
  was about to colonise.** `pick_homeworld` already refuses owned planets and
  picks the uncolonised planet furthest from everything claimed. What it does not
  know about is a planet nobody owns yet that a colony ship is flying toward,
  which is the collision that matters in a sandbox.

  The fix is to populate its existing `taken` argument with every planet named as
  a destination by a live ship order. Ship orders and their `ROUT` are in the
  blob and `order_diff` and `inject_order` already read that structure. A
  distance margin on top, since maximin already pushes arrivals to the rim.

  **Donors have to be levelled first.** A civ added by `inject_civ` inherits its
  donor's world, and a generated galaxy does not hand its civs equal worlds: seat
  one carries the homeworld customisation and the rest get the engine default,
  which is worth two `PLPR` bytes and decides games (D3).

  **Done when:** a civ is injected into a galaxy with a colony ship in flight,
  the newcomer's homeworld is not that ship's destination, verified by reading
  the ship's `ROUT` before and after rather than by the join having looked fine,
  and the colony ship completes its colonisation on schedule.

- [ ] **K2. Repeated joins across a long galaxy.** D4 confirmed one injection. A
  permanent sandbox does this dozens of times, object ids grow monotonically, and
  the `GLXY` civ count and high-water id are rewritten on every join.

  **Done when:** a galaxy takes 12 joins spread across 100 turns and every one of
  them loads, plays and ticks, with the civ count and high-water id correct at
  the end.

- [ ] **K3. Detect abandonment.** Countable out of the store already: the archive
  records which civs submitted for each turn, so consecutive misses need no new
  bookkeeping. Two stages, a warning and a reclaim, with the thresholds
  configurable per galaxy. At 4-hour turns, 12 missed turns is two days.

  The warning goes through the existing `put_note` path and is shown in the
  launcher. A reclaimed player's launcher will then fail `roster_problem`, which
  is the right behaviour, but the message has to say the seat was reclaimed after
  so many missed turns rather than that no seat exists for that name.

  **Done when:** a galaxy run with one player silent produces a warning note the
  launcher displays, a reclaim at the configured threshold, and a refusal message
  that explains itself.

- [ ] **K4. Wipe a reclaimed civ from the galaxy.** This is the one item in this
  phase with a real unknown in it, and it gates K3, so it goes early.

  Planets are the easy half: `planet_records` reads `owner` as a `u32`, so
  returning a planet to uncolonised is a field write rather than a deletion.

  **Ships are the risk.** Object ids tile the galaxy contiguously and civs and
  ships sit at the top of that space, and deleting from the middle of a
  contiguous id range is the shape of the thing that made every fog projection
  fail to load (D1). `inject_civ` appends, so a civ being wiped is almost never
  the last one, which is the case D1 found survivable.

  **The fallback, if deletion does not load:** keep the `OWNR` shell in place
  with no planets and no ships, leaving the id space, the civ count and the
  high-water id untouched. To every player that is a civ wiped from the galaxy;
  structurally nothing was removed. This costs a dead record per abandonment,
  which a permanent galaxy accumulates, and that is the trade being accepted.

  **Done when:** a galaxy where a civ has been wiped loads, ticks 20 turns, and
  another player can colonise a planet the wiped civ used to own. A blob that
  loads once is not the bar; the fog work established that a structural break can
  show up as a clean exit four seconds in.

- [ ] **K5. Ending a galaxy is an operator action.** No season timer. The
  operator calls a galaxy over and starts a fresh one, so there has to be a way
  to close one that stops accepting submissions, keeps the archive readable, and
  tells every launcher why.

  **Done when:** a galaxy is closed and a fresh one started, and a launcher
  pointed at the closed one says so rather than failing.

---

## L. Build versioning

- [ ] **L1. The launcher carries a version.** It does not today: there is no
  version constant in `release/launcher.py` and the only version anywhere is the
  `dist` folder name. Everything below depends on this existing.

  **Done when:** the launcher reports its own build, and the build is stamped at
  package time rather than hand-edited.

- [ ] **L2. A version gate on the galaxy.** The galaxy carries a minimum build; a
  launcher below it refuses to play and says where to get the update.

  This is the item that hurts most if skipped, because the failure it prevents is
  silent: a blob produced by a client that does not match the referee, whose
  symptom is a mystery rather than an error.

  **Done when:** a launcher below the minimum is refused with a message naming
  its own build, the required build, and a link, and one at or above it plays.

- [ ] **L3. One-click update now, silent auto-update later.** The gate's message
  becomes a button that downloads and runs the installer. Replacing a running
  executable in place is a separate problem and is deferred; the metadata is the
  same either way, so the later version is a change to the launcher's UI and not
  to anything on the server.

  **Done when:** a player below the minimum build reaches a current one without
  being told where to click by a human.

---

## M. Diagnostics

- [ ] **M1. The launcher uploads its own log.** Every finding in the blob push
  plan came from watching a screen, and that stops being available the moment
  players are elsewhere. Without this, every beta report is a slow conversation.

  **The log cannot be uploaded as it stands.** It currently records the HTTP
  bodies of the save protocol, which means the whole save blob base64-encoded in
  `body+` lines, tens of kilobytes per turn, and it is the one part of the log
  that diagnoses nothing. Those lines come out first, and a size cap goes on what
  is left.

  **Done when:** a turn's log uploads under the cap, and a failure the operator
  did not witness is diagnosed from the uploaded copy alone.

- [ ] **M2. Say what the upload contains.** The log carries Windows paths, which
  carry the player's account name, along with their username and galaxy. Either
  scrub the paths or disclose it in the warning. Uploading a player's machine
  details without telling them is not a thing to discover later.

  **Done when:** what is uploaded is either free of anything identifying beyond
  the username, or named in the text at N4.

---

## N. Operating it

- [ ] **N1. The worker runs unattended.** A scheduled task that starts on boot,
  the machine set not to sleep, and a startup rule that immediately closes any
  turn whose deadline has already passed. A 4-hour clock over a permanent galaxy
  means the machine's uptime is the galaxy's uptime.

  **Done when:** the machine is rebooted mid-galaxy with nobody watching and the
  next turn closes on time.

- [ ] **N2. One bad submission cannot stall the galaxy.** The referee already
  does the right thing structurally, extracting orders and applying them to its
  own authoritative blob rather than trusting a submitted one as state. What is
  missing is that a submission which fails to parse should be dropped with a note
  rather than end the tick. A size cap on top.

  This is robustness, not security: cheating is accepted for this beta, a tick
  that dies is not.

  **Done when:** a truncated blob, an oversized one and a blob from a different
  galaxy are each dropped with a note, and the turn closes for everyone else.

- [ ] **N3. An operator view.** One page showing the galaxy, its turn, who has
  submitted, when the last tick ran and what it took, and any errors. Otherwise
  the only way to know the beta is healthy is to read a log on one machine.

  **Done when:** the state of the galaxy can be read without opening a log file
  or a terminal.

- [ ] **N4. What players are told, in the launcher and wherever they sign up.**
  Not a footnote: several of these are properties the design has accepted rather
  than faults waiting to be fixed, and a beta tester who learns them by discovery
  reports them as bugs.

  - A username is a claim and not a credential. Cheating is possible and easy.
  - Every player's client holds the whole galaxy. An unmodified client hides what
    it should; a modified one is a maphack.
  - An inactive player's civ is wiped from the galaxy after a stated number of
    missed turns.
  - The galaxy may be ended by the operator at any time.
  - What the diagnostic upload contains.

  **Done when:** the text exists, is shown before a player joins rather than
  buried, and matches what the code actually does.

- [ ] **N5. Record a measured tick duration.** Capacity is not a constraint at one
  galaxy and six ticks a day, and the number is worth having anyway, because it is
  what the second galaxy and the cloud offload will be planned against.

  **Done when:** the seconds a tick takes on a real sandbox galaxy are written
  down, with the galaxy's size beside them.

---

## Out of scope, named so it stays out

- **Resolving turns anywhere but this PC.** The cloud offload is the next phase.
  H2 and H3 exist so that it is a deployment change.
- **Inheriting an abandoned empire.** A future game mode, and an interesting one.
  K4 wipes.
- **Formed or ranked galaxies with a lobby.** The sandbox is the only galaxy in
  this phase. J1's directory is the layer they would be added to.
- **Passwords, Google login, real accounts.** Everything above `player_name()`
  already takes a string, so the change is where the name comes from and nothing
  else.
- **Per-player projection and real fog.** Blocked in D1 and not blocked on this
  phase.
- **Governors and admirals.** The original's answer to an absent player. Their
  absence is why K3 and K4 exist at all.
