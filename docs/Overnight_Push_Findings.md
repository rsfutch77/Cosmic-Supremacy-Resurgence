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
