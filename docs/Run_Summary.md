# Run summary, 27 September 2026

**Temporary.** One agent run, written after the agents finished so the whole
result is in one place rather than spread through a conversation. Delete this
before the next run and write a fresh one.

Not the same thing as `Overnight_Push_Findings.md`, which is still open and
still being worked through.

---

## What you need to do

### 1. The scheduled task

The task starts at **logon**, not at boot, so no automatic logon is needed.
Computing a turn launches the game client, and a task that runs whether or not a
user is logged on has no desktop to draw it on. Your plan, that you log in and
turns resume, is what this is built for.

```
# Watch it decide, closing nothing you care about
.\run_worker.ps1 -Store <your galaxy> -Once

# Write the definition for that galaxy. This registers nothing.
.\run_worker.ps1 -Store <your galaxy> -WriteTaskXml .\referee_task.xml

# Register it, then start it without waiting for a logon
schtasks /Create /TN "CosmicSupremacy Referee" /XML ".\referee_task.xml"
schtasks /Run /TN "CosmicSupremacy Referee"

# Later
.\run_worker.ps1 -Store <your galaxy> -Status
type server\worker_work\referee_worker.log
schtasks /Query /TN "CosmicSupremacy Referee" /V /FO LIST
```

Verified in the generated XML: logon trigger present, boot trigger absent,
`InteractiveToken`, a 10-minute repetition so a crash costs at most ten minutes,
and the three Windows defaults that would otherwise stop the galaxy on their own
(start on battery, stop on battery, three-day execution limit) all inverted.

Ignore the script's closing paragraph recommending automatic logon. It is
generic advice and you have decided otherwise.

A dry run tonight closed turn 11 of `uidemo/sandbox` using your own orders from
the UI testing, ticked a real client and published turn 12.

### 2. Deploy the relay, still

Nothing built tonight reaches a real player without it. `firebase deploy --only
functions:relay` from `functions/`, plus the one-time signing grant in
`functions/deploy.py`'s docstring: enable `iamcredentials.googleapis.com` and
grant `roles/iam.serviceAccountTokenCreator` to the runtime service account on
itself.

### 3. One UI check that needs no client

Six galaxies with hand-set deadlines, where exactly two should read `stopped` in
amber and two past-deadline rows should **not**. The recipe is in
`Overnight_Push_Findings.md` under the stall section.

---

## What changed

| | |
|---|---|
| plan | 12 done, 12 partial, **4 open** (J5 is new, below) |
| server suite | **1,177 checks, 0 failures** |
| machine | tree clean, no clients, no emulator, all ports free |

**Joins work end to end.** A request becomes a seated empire at the next turn,
verified live against a control run of the same turn with no join: every
incumbent's planets, ships and `OWNR` bytes byte-identical. Requests now lodge in
**Firestore** rather than as objects in the bucket, which is your steer and it
was the right one: an object costs a Class A write, a Class A listing per tick
even when nobody is joining, and a copy-and-delete to file the answer, against
the 5,000 a month that is already 29% spent. Firestore is one write and a stream
against 20K writes a day, and the batch gives atomicity a bucket has no
operation for.

**The referee runs unattended** and records `worker_seen` and `worker_failures`
into the galaxy, so a machine that is off reads differently from a worker that is
up and failing. Written on tick and on failure and never on an idle pass: per
poll would be 17,280 writes a day for one galaxy against a 20,000 a day tier.

**A stalled galaxy says so to players**, at `max(300, min(3600, turn_seconds/4))`
past the deadline. The floor is `BACKOFF_MAX` from the worker itself, the longest
a live worker waits between retries, so anything under five minutes is a referee
that is recovering rather than gone. The check that carries it: 400 seconds over
stops a 15-minute galaxy and does not stop a 4-hour one.

**Polling fell from 43,200 store reads a day per player to 888.** The before
column was measured by running the same harness against the old file out of git
rather than calculated.

---

## Three bugs worth more than the features they were found under

**The Firebase store was throwing away the version gate.**
`FirebaseTurnStore.state()` is an allowlist and it silently dropped `min_build`.
L2's gate reads that out of the state, so on the one deployment that will have
strangers in it, **every build passed**. Not a missing write: written, then
discarded on read. `joined` was going the same way.

**A joiner was a copy of the leader, not a pauper.** I reported the opposite and
you made a balance call on my description. Measured afterwards: a joiner was
receiving seat one's 418-byte capital with 21 citizens, 8 designs, 10,820
credits, a chosen research field. Your ruling is right for the real situation
too, so nothing was decided wrongly, but the description was never measured
before it was reported.

**A cloned ship carried the donor's object id.** A `SHIP` record holds its id
twice and `add_ship` left the donor's in the second slot. 553 of 685 ships across
151 blobs read their own id in both places, and all 132 that do not are ships
this tool cloned.

---

## New in the plan

**J5, a joiner still inherits seat one's buildings and stockpiles.** The
unfinished half of your balance ruling. A transplanted homeworld still differs
from a turn-0 one at 17 offsets, which are stores, food and facilities, so
joining a turn-80 galaxy lands you on a starting world carrying the leader's
warehouses. The bytes were deliberately not guessed at. In the plan rather than
the reconstruction report so it is settled before the beta opens.

---

## The largest thing still outstanding

`has_submitted` over HTTP is a **bucket listing per poll**. The Games page polls
it every 120 seconds per joined galaxy: roughly **21,900 Class A operations a
month from one player with the launcher open**, against a 5,000 a month
allowance. A `submitted` array on the galaxy document, written where the relay
already commits, would ride the `/state` read the launcher already makes and cost
nothing.

This is your cheap-boolean case and it is the biggest one left.
`abandonment.find_template` is the same shape, downloading up to ten whole turn
blobs to find one with a free planet.
