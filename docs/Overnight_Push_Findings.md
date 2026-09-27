# Overnight push, findings and what is not verified

**Temporary.** This file exists for one working session, 21 to 22 September 2026.
Anything in it that survives review belongs in `Public_Beta_Plan.md` or
`CosmicSupremacy_Reconstruction_Report.md`; the rest gets deleted with the file.

Its job is the thing that caught three real bugs in the session before it:
**writing down what has not been checked, next to what has.** A claim like "the
blob is correct" and a claim like "a player sees the right thing" are different
claims, and the gap between them is where the last four defects lived.

---

## Read this first if you are picking the session up

| | |
|---|---|
| client | one game process per machine. An agent holds it; nothing else may launch one |
| stub server | `cs_server.py` must be listening on 8888 or every tick fails after doing the work |
| live site | `cs-resurgence` also hosts cosmicresurgence.com. Never `firebase deploy` without `--only`, never touch `site/` |
| relay | built, **not deployed**. The operator deploys it |

---

## NOT VERIFIED, carried in from before this push

Each of these is a thing the code claims and nobody has watched happen.

- [ ] **H2, the referee is pull-only.** Nothing has confirmed the router carries
  no forwarding rule. The property is architectural, the evidence is absent.
- [ ] **H3, Firebase is the record.** The referee has never been killed
  mid-galaxy and the machine rebooted.
- [ ] **H4 and H7 done-when.** Both need the relay deployed, which is the
  operator's call. Everything so far is emulator-only.
- [ ] **J4, a second install.** One uid has never been refused a seat held by
  another, because that needs two installs or a faked data directory.
- [ ] **M1, the upload.** Redaction is built and measured; nothing has ever
  uploaded anything.
- [ ] **L2, `min_build`.** The gate works and nothing writes the field, so no
  galaxy is gated in practice.
- [ ] **K1, the live half.** An injected galaxy has not been loaded since the
  seat-order fix, and no colony ship has been watched completing after a join.

---

## Needs the operator

Collected as they arise, so the morning has one list rather than a reread.

1. **Deploy the relay.** `firebase deploy --only functions:relay` from
   `functions/`, plus the one-time signing grant in `functions/deploy.py`'s
   docstring: enable `iamcredentials.googleapis.com` and grant
   `roles/iam.serviceAccountTokenCreator` to the runtime service account on
   itself.
2. **Deploy the rules**, deliberately and once, as
   `firebase deploy --only firestore:rules,storage` from a directory whose
   `firebase.json` names nothing else.
3. **Hosting version retention.** 20 finalized versions, 449.9 MB, 4.4% of the
   10 GB tier, growing about 22 MB per deploy with no cap.

---

## Findings from this push

Appended as agents land. Nothing here is folded into the plan until it has been
read.

### J2, the Games tab, built (launcher agent)

A `Games` panel lists galaxies from `open_directory`, with a `Join` button on an
open galaxy this player is not already in. Joined state lives in `joined.json`
beside `identity.json`, holding the store spec the **directory** handed back, so
no config file names a galaxy. `multiplayer.json` keeps its exact old meaning and
still wins when present, so a folder store and a LAN HTTP store behave as before
and open no directory, mint no token and make no network call. 96 headless
checks pass.

**NOT VERIFIED: nothing was seen on screen.** No window was opened. Layout,
wrapping, panel width against the card column, button placement and window
sizing with several rows are all unknown. `_submitted_state`, `refresh_games`,
`_on_games`, `on_join` and the new `_drain` branch are untested code paths that
need a window.

**To check it:** launch the launcher from a checkout with **no**
`multiplayer.json`; confirm a `Games` panel appears under the mode cards. Then
put `{"directory": "C:\galaxies"}` in `multiplayer.json` pointing at a folder
holding a galaxy, click `refresh`, then `Join`. Confirm `joined.json` appears in
the data directory, `joins\<name>.json` in the galaxy folder, and that the row
flips to "you join at the next turn" with the Join button gone.

**The join request shape is a proposal, not a contract.**
`send_join_request` calls `store.request_join(req)` if a store grows one, else
writes `<store>/joins/<uid or name>.json`, else raises and tells the player
nothing was sent. K3's and J3's work has to agree with that shape or say what it
should be. One line either way.

**Fixed while committing:** `build.ps1` hidden-imported `player_turn` and
`turn_store` but not `galaxy_directory` or `firebase_store`, so a frozen build
would have reported that it cannot list galaxies. Both added. Unexercised: no
frozen build has been made since.

**An instruction was violated and it could have cost the K2 run.** The agent ran
`release/tests/test_external_status.py` without reading it first. That test opens
a Tk window, launches two game clients, and ends with
`taskkill /IM CosmicSupremacy.exe /F` and `/IM CosmicSupremacy_TestBed.exe /F`.
Neither name matches the Resurgence or Player build, so the client-holding
agent's session survived, but by coincidence rather than by design. The lock was
checked immediately afterwards and still read `join_acceptance K2 segment 8`, so
K2 was not disturbed. **A test that kills processes by name is not safe to run
for regression while another agent holds the client**, and `run_all.ps1` is not
the only file that does it.

### K1 and K2, both passed in a client that actually ran (injection agent)

**K1.** The collision was forced rather than hoped for: a colony ship was ordered
at the exact planet `pick_homeworld` returns with nothing reserved (#6). With the
reservation the newcomer lands on #47 instead, all four `ROUT` sections survive
the injection byte-identical, and **the contested planet #6 was settled on turn
63 in both the control and the joined run**, 52 turns after the order. A second,
shorter colonisation completed on turn 13 in both. 60 turns compared ship by
ship: 0 divergences.

**K2.** Turn 11 to 112, 12 joins, 13 client launches, each segment stamped for
the civ that joined last so every load proves a newcomer is playable rather than
merely present. Object ids 210 to 232 strictly increasing, final civ count 15
matching 15 `OWNR` records, high-water 233 matching the largest id, then
cold-loaded from a fresh client.

**NOT VERIFIED.** No human saw any of it; every claim is read out of blobs the
client wrote. To look: `server\join_work\joined_as_new.dat` (turn 11, stamped
`Joiner`) or `server\join_work\k2_out.dat` (turn 112, 15 civs). The referee path
was not exercised on a joined galaxy: `resolve_turn` publishing one, `merge_orders`
accepting a submission from a joined civ, and the launcher admitting a joined
name are all untested. Joined civs were given nothing to do, so K2 shows such a
galaxy loads and ticks, not that a newcomer can act. The margin never bound, all
12 picks landing 576+ away against a margin of 174. One galaxy shape only.
**The `EXSY` cheat is still live**: every joined civ inherits the donor's
explored map, which is recorded in `inject_civ.py` and out of K1 and K2's scope.

It also closed the `war_after.dat` client that was left open for you, under a
lock whose pid was already dead. The restore command is in its report if you want
that view back; you had already confirmed the fix, so I did not reopen it.

### K3 and K5, built, referee not wired (abandonment agent)

`server/abandonment.py` with `enforce(store, turn, blob)`, plus `review()` as a
read-only dry run and a CLI. Defaults warn at 6 missed turns and reclaim at 12.
43 checks, three of them mutation-confirmed. Closing a galaxy is on all three
stores; 40 local checks plus 14 against the Firestore emulator.

Two things it got right that are easy to get wrong: a seat taken at turn 19 has
missed 2 turns and not 18, so joining does not instantly reclaim; and the wipe
takes its blank `PLPR` template from an **earlier archived turn** when the galaxy
has no free planet left, which is K4's constraint and the state abandonment
actually happens in.

**NOT VERIFIED.** Nothing was loaded or ticked in a client. `enforce` has never
run inside a real `resolve_turn` and the call ordering is a specification, not
something observed. The launcher still says "no seat for you" rather than naming
a reclaim. **Retroactive enforcement was never run against a real galaxy**: switching
it on does not start counting from today, it reads the archive already there, so
run the CLI's dry run first.

**Fixed while committing:** the relay minted upload tickets without ever calling
`submit`, so a closed galaxy was enforced only by the launcher's own client-side
check and a modified one could still upload. `functions/relay.py` now refuses at
both halves of the ticket. Unexercised: no test covers it yet.

### N3 and N4, built, unseen (operator agent)

`server/operator_view.py` is read-only by construction: a fail-closed allowlist
proxy, with `submissions(turn)` deliberately off it so H6's mistake cannot be
reintroduced. It refuses to invent a tick duration, since the archive records
`closed_at` and no duration, and prints a clearly labelled derived figure
instead. 92 checks, including a SHA-256 fingerprint of every file before and
after a full render.

`server/beta_notice.txt` is the player text, with `beta_notice.py` as the loader.
It carries no invented numbers: the miss thresholds are markers the operator
fills, and a missing notice returns a reason rather than an empty string so it
cannot silently read as "there are no rules". 63 checks, including one that
counts the launcher's own redaction bullets and fails if the notice does not
match.

**NOT VERIFIED.** Nobody has looked at either. Run
`python server/operator_view.py --directory <folder> --serve` and open it; read
`server/beta_notice.txt` end to end as a player. Firebase was never exercised by
this agent. The notice's "three things leave your computer" claim is derived from
reading `fb_auth.py`, submissions and the log, **not an audit of all 2,066 lines
of `launcher.py`** — if the launcher makes another network call, that section is
wrong and it is the section a player is most entitled to rely on.

**Fixed while committing:** `build.ps1` now packs `beta_notice.txt`, which
PyInstaller would not have carried, so a frozen build would have raised
`FileNotFoundError` where the notice belongs. My first attempt at that line was
itself broken, `` being eaten as a backspace escape and producing
`$ServerDireta_notice.txt`; caught by reading the file back rather than trusting
the edit.

**Also fixed:** the storage emulator refused to start for want of a rules file,
which is why the abandonment agent could test the Firestore half of a store and
not the Storage half. The reference is injected into the **generated** emulator
config rather than `functions/firebase.json`, because adding a `storage` key to
the committed file would make rules deployable from the one config whose whole
purpose is that it can reach nothing but functions.

### N2, and a bug it found rather than hardening (robustness agent)

The baseline was measured against `merge` as it stood, on real fixtures. A
truncated blob, an empty one, an oversized one and bytes that are not a save
**each raised out of the merge and ended the turn for the whole galaxy**. Worse,
a submission from **another turn** and one from **another galaxy** did not raise:
they merged, writing an order out of a state nobody is playing into the live
galaxy. So this item was not hardening, it closed a path by which one player's
stale or foreign save silently altered everyone's game.

Two layers now: `screen_submission` for the cheap cases, and a per-submission
guard inside `merge` that rolls the blob back to what it was when that civ was
reached. A dropped submission is invisible to the rest of the galaxy, landing in
`missing` rather than `submitted`, with the reason in the player's note and in a
new `dropped` field of the archive record. 78 checks, every bad case run **with
two good submissions beside it**, so a referee that dropped everything fails.

**NOT VERIFIED.** No client was launched: every `resolve_turn` test stubs the
tick, so nothing here shows a real turn computing or a real capture arriving.
`HttpTurnStore` and `FirebaseTurnStore` were not exercised. 300 random byte
corruptions of a real submission produced 265 that passed the screen and **zero**
that raised deeper, so the merge guard could not be triggered through the screen
and is tested by calling `merge` directly. Repeated against the final code, same
seed, same answer.

**What that result means is worth stating, because it is easy to read as
reassurance.** Random single-byte and four-byte corruption leaves the galaxy
identity intact, so the screen passes it, and the merge then judges the corrupted
bytes as ordinary orders. A corrupted submission therefore produces **wrong
orders rather than a rejection**. That is inside the domain this beta has already
accepted, since a player who wanted to send wrong orders could simply send them,
and it is not a stall, which is what N2 exists to prevent. It is not evidence
that submissions are validated, because they are not and were never meant to be.
`screen_submission` asks whether bytes can be used at all, never whether an order
is legal, which is C4 and is not built.

**Two findings in files it did not change.** The first is now fixed, see below.
And
`duel3.b64` is a fixture trap: it looks like a different galaxy and is the same
one at turn 3, so it tests the turn check rather than the galaxy check.

**A zlib bomb is still possible.** Nothing caps the wire form before the store
decompresses it, so a small compressed submission can expand past the 8 MB limit
inside `turn_store` before the referee ever sees it. Outside that agent's files
and not fixed.

### `abandonment.enforce` wired into the referee

Both agents that owned the two sides finished, so I joined them: `resolve_turn`
calls `enforce` on the blob the tick produced, **before** `publish`, because a
reclaim rewrites that blob and publishing first would restart the clock on a turn
players already hold. Full server suite green afterwards, 682 checks across 16
files, with `test_relay_function` skipping for want of an emulator and
`test_rename_materialisation` skipping for a missing fixture, both pre-existing.

**NOT VERIFIED:** the wiring itself has never run inside a real turn with a real
client. The seam is exercised only by tests that stub the tick.

### The launcher gates: reclaim, closed, notice (launcher-wiring agent)

All three server-side pieces now reach a player. `roster_problem` takes the
player's own reclaim record and says the seat was taken back at turn N after M
missed turns, echoing nothing but the name they typed; a leak of any other
player's name fails a check. `closed_problem` sits directly after
`version_problem` in both entry points, ahead of the roster and seat checks,
because a seated player of a closed galaxy still passes `roster_problem` and
would otherwise get a confusing refusal. The notice is a modal shown by the Join
button and by nothing else, filled with that galaxy's own thresholds, refusing to
open the beta at all when a marker is unfilled.

Two consequences it reasoned out rather than being told: a reclaim does **not**
offer the "change your name" prompt, since a new spelling is not the remedy; and
a reclaim clears `joined.json`, because otherwise the Games row reads "you join
at the next turn" forever with no Join button, a dead end. 89 checks, each
refusal paired with the same call on a healthy galaxy, plus source-order
assertions so a future reordering that put the notice after the request fails.

**NOT VERIFIED: no window was opened.** `show_beta_notice` has never been
rendered. Layout, scrollbar, wrapping at 78 columns, whether 22 lines is the
right height and the modal grab are all unexercised. Nothing ran against a live
galaxy, the relay, or a Firebase store; every test used folder stores. Nothing
here produced a reclaim or a close, both were written by hand, so
`abandonment.enforce` has never driven a launcher refusal end to end.

**Fixed while committing:** `build.ps1` needed `beta_notice` and `abandonment` as
hidden imports. Without them a release refuses **every** join, loudly and by
name. That is the intended fail-safe rather than a silent wrong answer, but it
would still have made the first packaged beta unjoinable. Both added, and the
build script now carries five modules the launcher imports inside functions.
Unexercised: no frozen build has been made since any of tonight's launcher work.

### The research refusal was firing at honest players

`merge` snapshots `served_ships`, `served_planets` and `served_designs` once at
entry, because every player played offline against that state and it is the only
thing a submission can honestly be compared against. The research rules did not:
they read the accumulating blob. Nothing in the merge ever writes another civ's
research, so the two differ only where an earlier submission in the same turn was
honestly applied, and the result was that **the second player to merge in any
turn where two of them changed topic was told they had edited the first
player's research.**

Their own orders still applied, so nothing was lost. What was wrong is the note,
in a galaxy where notes are the only thing telling a player what the referee
refused, and it named another player as the owner of an edit that never
happened. In a two-player test it needs both to change topic in one turn; in a
sandbox with several players it is most turns, hitting everyone except whoever
merges first.

`served_research` is now taken alongside the other three indexes and both rules
read it. `server/tests/test_research_merge.py`, 10 checks, each run with **both**
submissions present because one submission cannot show the bug: with only one,
the accumulating blob and the served blob are the same thing and the old code
passed. Mutation-tested by reverting the one line, which fails exactly the
second civ to merge in both orderings.

The control matters as much: a player who really does edit someone else's topic
is still refused, and the refusal still names whose it is. Without that check the
fix would be indistinguishable from deleting the rule.

---

## UI review, 26 September 2026

The first review of anything built in the overnight push, on screen, by the
operator. The Games panel was **rejected on shape** rather than on behaviour, so
this is a redesign and not a patch.

**What the screen showed that no test could.** Every check in
`test_games_tab.py` and `test_join_gates.py` passed against a panel that was the
wrong thing to have built. 96 and 89 checks say the rows render correct text; not
one of them could say the player has no idea what to click. Worth remembering
the next time a headless suite reads as coverage.

**Findings:**

- **A galaxy you are already in has no Play control.** The row says "you are in"
  and offers nothing. This is a hole, not a preference: the sandbox could be
  listed and not opened.
- **The Multiplayer button's job is now unclear**, because there are two
  overlapping entry points. With a `directory` configured and nothing joined it
  has no galaxy to play and falls through to a roster refusal with a rename
  prompt, which is what the operator actually hit. Nothing says "go and pick
  one".
- **Bottom padding of the Games box is 0** and matches nothing else in the
  window.
- **`Join` is the wrong word in the main window.** The modal is where a player
  reads about the galaxy and confirms, so the row's button should say **View**.
- The join modal itself was approved as it stands. Beta text edits are coming
  from the operator separately.

**Decided: option A.** The Multiplayer button becomes navigation to a Galaxies
page holding a sortable table, with per-row View and Play, and a Back button to
the home page. The Games panel comes out of the main window. Chosen over
patching the panel because the sandbox is meant to become many galaxies, and a
table with one row beats a panel that has to be rebuilt later.

**A walkthrough error of mine, recorded because it wasted a step:** I asked the
operator to test the reclaim refusal while `multiplayer.json` still pointed at
the directory rather than at the lapsed galaxy, so they got the ordinary "no
seat" path and a rename prompt instead. The test said nothing about the reclaim
message. Repointed and retried.

**Still not verified after this session:** the closed-galaxy refusal, the reclaim
refusal now that it is pointed correctly, the operator view, and every frozen
build path. The launcher under review is the checkout at `0.1.0+dev`, so the
notice and the two new hidden imports remain unexercised in a real package.

## Second UI review, 26 September 2026

The Galaxies page was accepted. "The rest looks good on that window now."

**Done from this round:** `Time left` is now `Next turn`. Joining several
galaxies is being fixed, see below.

### Windows Defender quarantines a build artifact, not the product

`Exploit:Python/Leivion.C` on
`client/dev_tools/__pycache__/trigger_save.cpython-312.pyc`, twice, on 20 and 26
September. The diagnosis is not a false positive in the interesting sense:
`trigger_save.py` really does use `OpenProcess`, `VirtualAllocEx`,
`WriteProcessMemory` and `CreateRemoteThread` to make the client save on demand,
which is textbook remote code injection and the only mechanism this path has for
a save. Defender is describing what the file does.

**What matters is which file.** Both detections name the **compiled bytecode** in
a checkout `__pycache__`. The `.py` source beside it is not flagged. A frozen
build has no `__pycache__` at all, and a full custom scan of
`dist/CosmicSupremacy-Resurgence-v0.1.2` came back **clean, exe intact**. So this
is a developer-machine problem and not, on this evidence, something a beta player
meets.

**NOT VERIFIED and worth knowing before the beta opens:** an on-disk scan is not
behavioural detection. A packaged launcher that actually calls
`CreateRemoteThread` into the game process at runtime could still be stopped, and
nothing here has run a packaged save. That is the test, and it needs a real
frozen build taking a real turn.

A dev-side fix that needs no antivirus exclusion: stop writing bytecode for these
tools, with `PYTHONDONTWRITEBYTECODE` or `sys.dont_write_bytecode`, so the file
Defender objects to never exists. An exclusion would also work and is worse,
since it trains the habit and hides the next thing.

### Joining is membership; playing is a client constraint

`joined.json` held one record, so joining a second galaxy offered to replace the
first: "this launcher plays one galaxy at a time". Playing one at a time is real,
one machine has one game process. Being a **member** of one at a time is not, and
the restriction was sitting on the wrong verb.

### Recorded, not yet investigated

- **A lot of cmd windows appear when the sandbox launches.** Operator asked for
  this as a later TODO. Likely subprocess calls without `CREATE_NO_WINDOW`, which
  `stamp_build.py` already uses and the client tools mostly do not.
- **A scientist reassignment did not survive closing and reopening the client.**
  Resume is implemented: `player_turn.follow` reloads this civ's own submission
  when `carries_orders` says it holds any, rather than re-serving the pristine
  turn. So either no interim submission had been made in that window, or a job
  reassignment is not what `carries_orders` counts. Needs reproduction with a
  client before anything is changed, and the operator noted it may be waiting on
  features anyway.
- **Join on `lapsed` and `crowded` both answered "you have asked to join".**
  Expected while J3 is unbuilt, since nothing on the server consumes a join
  request. It also means the reclaim refusal on `lapsed` is still unverified: the
  row offers View rather than Play, because a reclaimed player is no longer in
  the roster.
