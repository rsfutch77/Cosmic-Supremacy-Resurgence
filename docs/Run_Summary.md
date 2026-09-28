# Run summary, 27 September 2026

**Temporary.** Delete this before the next run of agents and write a fresh one.

Not the same thing as `Overnight_Push_Findings.md`, which is still open and
still being worked through.

Rewritten after the relay went live. The scheduled-task walkthrough that used to
be here is gone because you did it, and "deploy the relay, still" is gone
because it is deployed.

---

## What you need to do

### 1. Reopen the launcher

Your launcher had the "another server already holds port 8888" popup because the
referee task you registered is doing exactly what it should: it keeps a
`cs_server` on 8888 so captures have somewhere to land, and the launcher refuses
to bind over a port it did not take.

`release\data\multiplayer.json` now carries `adopt_server` pointing at
`server\saves`, which is where that server writes. Close the launcher and open
it again and the red popup should become a green **sharing the server on
127.0.0.1:8888**.

### 2. Decide whether the ugly hostname is acceptable

The beta reaches the relay at

    https://relay-r5t6py5oxa-uw.a.run.app

rather than the tidier `https://us-west1-cs-resurgence.cloudfunctions.net/relay`.
Both are the same function. They are gated separately, and only the first is
open to callers who are not members of the project; the second answers 401
before any of the relay's code runs. Making the tidy one work means granting
`allUsers` the invoker role on the **function** resource, which is a second
public grant for a cosmetic gain. Nothing needs it, so nothing was granted.

Say the word if you would rather have the readable URL, or a custom domain.

### 3. One UI check that needs no client

Still outstanding, and now a single command rather than a recipe:

    python server\dev_tools\set_demo_deadlines.py

It prints what the Galaxies page should show and how long that will stay true,
then you look at the page and see whether it agrees. Two rows in amber reading
`stopped`, two past their deadline and **not** stopped, one closed, one counting
down.

The pair that carries it is `outpost` against `lapsed`: the same twelve minutes
late, opposite verdicts, because one is late for a fifteen-minute turn and the
other for a four-hour one.

**Why this needed a tool.** The fixture's deadlines are absolute timestamps, so
the arrangement they were set up to produce lasted about an hour and then
decayed into every row reading `stopped`. That is what you saw tonight: four
stopped rows, all of them correct, against a fixture roughly 22 hours stale.
The code was never wrong, but a stale fixture and a broken threshold look
identical on screen, which is the failure the tool removes. The automated
coverage was never affected, because `test_stalled_galaxy.py` copies the fixture
and rewrites the deadlines in the copy.

---

## What changed

| | |
|---|---|
| plan | H7 deployed, **H8 new and done** |
| server suite | every suite passes, relay at **137 checks** |
| release suite | every suite passes |
| machine | tree clean, two commits, throwaway galaxies deleted |

**The relay is live** in `us-west1`, on a three-day artifact cleanup policy so
container images stop accumulating. Measured against the real service with an
anonymous token: `/state` answered 200, `/turn/11` answered 302 to a
`storage.googleapis.com` URL carrying `X-Goog-Signature`, and following it
returned bytes that parse as a save blob. The signed URL is what proves the
`serviceAccountTokenCreator` grant, because nothing else in the relay needs it.

**A player can now find a galaxy.** This was the half of H7 that H7 did not
cover: the relay answered every question about a galaxy a player already knew
the id of, and no question about which galaxies exist. The launcher's default
directory was `firebase://cs-resurgence`, which streams the collection as an
administrator, so the Galaxies page worked on your machine and could not have
worked on anybody else's. `GET /` on the relay now serves each galaxy the fields
`/state` serves plus its id and name, through the same allowlist and the same
private-field filter, and `HttpGalaxyDirectory` is the launcher's side of it.

Confirmed against the deployed service rather than only the emulator: an
unauthenticated listing is refused, a signed-in caller gets the galaxy with its
turn and player count, and the store named by that row opens through the same
relay and downloads a 39,119-byte turn blob.

---

## Two things that were wrong on the way

**An HTML 401 was read as a missing invoker binding, and the binding was already
there.** A deployed 2nd-gen function has two hostnames with two separate IAM
policies. `firebase deploy` had already opened the Cloud Run one. Nothing needed
granting, and the command that was blocked would have added a second public role
for no gain.

**The relay's own test suite could not run, and said so in a way that named the
wrong thing.** Started without `--project`, the emulator takes its default from
`.firebaserc`, which is the live project, and then mints tokens whose `aud` is
`cs-resurgence`. The tests run as `demo-cs-resurgence`, so every token was
refused as minted for another project and the run died at "a real emulator token
is accepted" with nothing on screen about projects. `emulators.py` now names
`demo-cs-resurgence` itself. That one flag took the suite from unrunnable to 137
checks.

---

## Still open

**`has_submitted` over HTTP is a bucket listing per poll**, roughly 21,900 Class
A operations a month from one player with the launcher open, against a 5,000 a
month allowance. A `submitted` array on the galaxy document, written where the
relay already commits, would ride the `/state` read the launcher already makes
and cost nothing. This is the biggest instance of your cheap-boolean steer.
`abandonment.find_template` is the same shape, downloading up to ten whole turn
blobs to find one with a free planet.

**J5, a joiner still inherits seat one's buildings and stockpiles.** A
transplanted homeworld differs from a turn-0 one at 17 offsets, which are
stores, food and facilities, so joining a turn-80 galaxy lands you on a starting
world carrying the leader's warehouses. The bytes were deliberately not guessed
at.

**H2 and H3** are both "done when" items that need the router read and a reboot
respectively, neither of which is a code change.

**`adopt_server` is a hand-edited JSON key.** Fine for you, wrong for anyone
else who runs the referee and plays on the same PC, which is every beta player
who hosts. It probably wants to become something the launcher works out for
itself rather than something a player is told to type.
