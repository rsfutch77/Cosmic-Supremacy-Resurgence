# Overnight push, findings and what is not verified

**Temporary.** Anything in it that survives review belongs in
`Public_Beta_Plan.md` or `CosmicSupremacy_Reconstruction_Report.md`; the rest
gets deleted with the file.

Its job is the thing that caught three real bugs in the session before it:
**writing down what has not been checked, next to what has.** A claim like "the
blob is correct" and a claim like "a player sees the right thing" are different
claims, and the gap between them is where the last four defects lived.

Pruned 27 September 2026. Everything closed in that session was deleted rather
than ticked: the relay deploy, the galaxy listing, the scheduled task, the
turn-loop death, the polling numbers, the version gate, the starting kit, the
`EXSY` inheritance, the capture cadence, the Save button, the lingering turn
readout, and the live rules. What follows is what is actually left.

---

## Read this first if you are picking the session up

| | |
|---|---|
| client | one game process per machine. An agent holds it; nothing else may launch one |
| stub server | `cs_server.py` must be listening on 8888 or every tick fails after doing the work |
| live site | `cs-resurgence` also hosts cosmicresurgence.com. Never `firebase deploy` without `--only`, never any `firebase hosting:*` command, never touch `site/` |
| relay | deployed to `us-west1`, reached at its `run.app` host. `BETA_DIRECTORY` in the launcher points at it |
| rules | Firestore and Storage are both deny-all live, confirmed by reading them back. Nothing client-side touches either; the relay is admin and bypasses them |

---

## NOT VERIFIED

Each of these is a thing the code claims and nobody has watched happen.

- [x] **H2, the referee is pull-only.** Done 28 September: the operator read the
  router's configuration and reported no forwarding rule. Worth re-reading when
  the network changes, since a rule can appear without anyone adding one, but
  there is nothing left to do about it now.
- [ ] **H3, Firebase is the record.** The reboot happened, and it does not
  count for this. The galaxy under test was `server\uidemo\sandbox`, a folder on
  the worker's own disk, so everything that survived the reboot survived because
  the machine did. A store that is the record is one the machine can be rebuilt
  without. The same test on a `firebase://` galaxy is the one this item means.
  N1 is closed by that reboot; this is not.
- [ ] **J4 and H7's done-when, which are the same test.** No uid has ever been
  refused a seat another holds, and no launcher without a Google credential has
  played a turn end to end. The relay half is proven: a signed-in caller lists
  galaxies, reads state, and pulls a turn blob through a signed URL, measured
  against the deployed service. What is unproven is two installs disagreeing
  about a seat, and that needs a second machine or a faked data directory.
- [x] **K1, the live half.** Done 28 September, in a real client over 60 turns
  of each galaxy. Both colonisations completed on the same turn to the same
  civ with the joiner present, and ships under orders never diverged. The
  contested planet took 52 turns, so the length of the run was what made it
  mean anything. Measurements are in the plan under K1.

**Two things that were listed here and do not belong.** Neither is waiting on a
person; both are unwritten code, and filing them as verifications made the
human list look longer than it is.

- **M1, the upload.** Not "built but never run". The redaction is built and the
  upload is not: M1 says the upload waits on the relay, the relay is deployed
  now, and no route on it accepts a log. Nothing to verify until something
  sends one. Moved to open engineering.
- **L2, `min_build`.** The gate works through all three stores and nothing
  anywhere writes the field. There is no operator tool that sets it, so this is
  not an operator action that nobody got round to. Moved to open engineering.

---

## Needs the operator

1. **The same reboot, on a Firebase galaxy.** The folder-store reboot is done
   and closed N1. H3 is the one that needs a `firebase://` galaxy under it, and
   it needs a beta galaxy to exist first.
2. **Hosting version retention, closed as accepted.** Last counted at 20
   finalized versions, 449.9 MB, 4.4% of the 10 GB tier, growing about 22 MB
   per deploy with no cap. More have been published since and the operator's
   call on 28 September is that the headroom is fine, so no retention limit is
   being set. It is a number that only moves one way, so it is worth a look if
   a deploy ever fails for space, and is otherwise not a task. Read it in the
   Firebase console rather than from the CLI, since no `firebase hosting:*`
   command may be run against this project.

**A note on running any of these.** Every command in this file and in the plan
is written relative to the repository root, and a fresh PowerShell opens in
`C:\WINDOWS\system32`. `-File .\run_worker.ps1` from there fails with a message
about the file not existing, which names neither the working directory nor the
fix. Either `cd` to the repo first or give the script its full path.

---

## Open engineering

### The largest instance of the Firestore steer

`has_submitted` over HTTP is a **bucket listing per poll**. The Games page polls
it every 120 seconds per joined galaxy: roughly **21,900 Class A operations a
month from one player with the launcher open**, against a 5,000 a month
allowance. A `submitted` array on the galaxy document, written where the relay
already commits, rides the `/state` read the launcher already makes and costs
nothing.

`abandonment.find_template` is the same shape, downloading up to ten whole turn
blobs to find one with a free planet.

### A refused player is still never told

The reason is written to `notes/` and to the answered-joins collection, and
nothing reads either, because a refused player is on no roster and
`player_turn.follow` never runs for them. Their row says "you have asked to
join" forever, which is exactly where J3 started.

The relay serves it: `GET /<galaxy>/join/<key>/answer` exists and is covered.
Nothing in the launcher calls it. This is a launcher change, not a server one.

### Nothing writes `min_build`, so no galaxy is gated

L2's gate reads the minimum out of the galaxy's state, refuses an older build
with a message, and survives all three stores since the allowlist fix. No code
anywhere sets the field, and there is no operator tool that would. So on the
one deployment that will have strangers in it, every build passes.

Small: somewhere for the operator to say it, and the value carried into the
galaxy document. The gate itself needs nothing.

### The launcher still cannot send its log

M1's redaction is built and measured, capped at 256 KiB by keeping the tail.
The upload was written as waiting on the relay. The relay is deployed now and
carries no route that accepts a log, and the launcher calls nothing, so a
failure an operator did not witness is still a slow conversation.

Two halves: a route that takes a redacted log for a galaxy and a caller in the
launcher. Worth having before strangers arrive, because it is the difference
between a bug report and a bug.

### Three small ones, all recorded before and all still true

- `server/referee.py:269` spawns `advance_turns.py` on **every tick** without
  `CREATE_NO_WINDOW`, so a console flashes per turn on an unattended machine.
  The client-side tools are fixed; this one is the referee's. Checked across the
  tree: every other console-spawning site is either a dev tool run by hand or
  the game's own window, which has to be visible.
- `functions/relay.py` keeps its own copy of the names-only submission listing,
  `_submitted_civs`. It agrees with the store's `submitted_civs` by inspection,
  not by a shared call or a test.
- `operator_view`'s allowlist does not carry `submitted_civs`, which is exactly
  the cheap accessor it wants.

### A killed worker no longer takes the galaxy down with it

Closed 28 September 2026. It stopped being theoretical when a console window
was closed on 27 September: `CTRL_CLOSE_EVENT` reached the worker, it died
without a log line, and the `cs_server` it had started went on holding 8888.
The replacement refused to start and named a `taskkill` for somebody to run,
which is correct and is also a galaxy that stays down until a person reads a
log. Nobody reads a log on an unattended machine.

That case is ended and replaced now rather than refused. It was never the guess
the refusal exists to prevent: the process is one this galaxy's own worker
started and wrote down, and it is about to be replaced by one started here, so
what directory it was given stops mattering the moment it is gone.

**The pid is checked against what is running under it**, because Windows reuses
pids and a worker that killed a number out of a file would be committing a
worse version of the error the refusal was written for. The command line has to
name this checkout's `cs_server.py`. A recorded pid that now belongs to
somebody else is a stranger with a familiar number and is refused like any
other stranger.

`--adopt-server` is answered first, so an operator who would rather share a
leftover than replace it still can.

Fifteen checks, including the recovery end to end: a real server is started,
orphaned without `stop`, and the next worker ends it and starts its own.

### `adopt_server` is a hand-edited key

A launcher on the same machine as the referee finds port 8888 held and refuses
it, because nothing in the protocol can ask a running server where it writes.
The operator names that directory in `multiplayer.json` and the launcher shares
it. That works and is verified on this machine.

It is wrong for anyone else. Every beta player who runs the referee and plays on
the same PC meets the refusal and has no reason to know the key exists. It
probably wants to become something the launcher works out for itself.

---

## Windows Defender quarantines a build artifact, not the product

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

A dev-side fix that needs no antivirus exclusion, and which is **not done**: stop
writing bytecode for these tools, with `PYTHONDONTWRITEBYTECODE` or
`sys.dont_write_bytecode`, so the file Defender objects to never exists. An
exclusion would also work and is worse, since it trains the habit and hides the
next thing.
