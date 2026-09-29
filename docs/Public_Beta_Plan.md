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

**The markers.** `- [x]` is done, `- [ ]` is not started, and `- [ ]~` is
partly done. The tilde sits outside the brackets rather than inside because a
todo extension reading this file filters on `- [ ]` and `- [x]` and hides
anything else, so a `- [~]` item disappears from the list that is supposed to
be tracking it. Do not tidy the tilde back inside.

---

## Where this stands, 28 September 2026

Written as a handover. `docs/Overnight_Push_Findings.md` was deleted on this
date and everything in it that was still open is an item here, so this file is
the only list.

**The beta path works end to end.** A Windows machine with no checkout, no
virtualenv and no Google credential ran the packaged build, listed a galaxy
through the relay, signed in anonymously, claimed a seat, played a turn, and
had the referee merge it. The referee survives a reboot with its local files
deleted, because the galaxy is in Firebase and the machine holds nothing that
matters.

**What the machine is doing right now.** A scheduled task, `CosmicSupremacy
Referee`, is refereeing `firebase://cs-resurgence/h3check`, listed as "Beta
rehearsal", at four-hour turns and has been since 01:46 on 28 September. It was
at turn 18 with no failures when this was written, one seat bound to the second
test machine's anonymous uid. `h3check` was made for the H3 reboot test and
then became the rehearsal galaxy because it was the only Firebase galaxy and
stopping it would have left nothing live to test against. It is still
disposable: nothing depends on it, and the next real decision is what the
actual beta galaxy should be and whether it starts fresh.

**Do not be alarmed by the task's Last Result**, which reads `-2147020576`
while Status reads Running and the worker is healthy. That is left over from
the task being deleted and re-registered on 28 September while an instance of
it was running. The galaxy advancing and `worker_failures` being 0 are the
facts; the task's result column is about a run that was interrupted
administratively and says nothing about the referee.

To point the referee somewhere else, write the task again with a different
`-Store` and re-register it; `run_worker.ps1 -WriteTaskXml` prints the two
`schtasks` commands. Every command in this file is relative to the repository
root, and a fresh PowerShell opens in `C:\WINDOWS\system32`, which has produced
two wasted attempts: `cd` first or give full paths.

**The three things most worth doing next**, in the order they block a beta
rather than the order they were found:

1. **H9**, the polling cost. One player with a launcher open spends more than
   four times the entire monthly Class A allowance. This caps the beta at
   roughly nobody and is the only item that does. **Done 28 September**,
   measured live: eleven polls, no bucket listings.
2. **J5**, the fourteen remaining transplanted bytes. **Done 29 September**:
   a facility list and a settled block, all reset, checked in the real client.
3. **J3's refused half**, which is the one a tester meets. A refused player's
   row says "you have asked to join" forever; the relay serves the answer and
   nothing reads it.

**What needs the operator and cannot be done from here:** a seat rebind path
exists nowhere, so a player who reinstalls Windows is locked out of their own
empire (J4); and no machine but this one and the second test PC has ever run
the build, so the antivirus question is answered for one configuration (M3).

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

- [x] **H0. Provision the project, which is a billing decision.** Nothing in this
  section can be finished until it is made, and neither H1 nor H5 as first
  written knew it existed.

  **As found, `cs-resurgence` had no Firestore database and no Cloud Storage
  bucket**, and `firestore.googleapis.com` was not enabled. Neither H1 nor H5 as
  first written knew a provisioning step existed at all.

  **Cloud Storage for Firebase has required the Blaze plan since 3 February
  2026.** On Spark every cell reads "not applicable" and the API answers 402 or
  403. So a beta that keeps blobs in Cloud Storage cannot be a Spark project: it
  is a Blaze project kept inside the no-cost allowances, which needs a billing
  account attached to a project that also serves the live public website.

  Two irreversible choices sit inside this. A Firestore database's location is
  permanent. And a bucket created now gets the Cloud Storage Always Free
  allowance, 5,000 Class A operations per *month*, rather than the far larger
  legacy `appspot.com` allowance of 20,000 uploads per *day*, and only in
  `us-central1`, `us-west1` or `us-east1`.

  A Firestore-only variant was considered and rejected: it would have stayed on
  Spark, but it trades the Storage allowance for a hard 1 MiB per-document
  ceiling that nothing here has measured a long sandbox against. **Blaze is the
  decision**, kept inside the no-cost allowances.

  **What the operator has to set up.** Every step is in the Firebase or Google
  Cloud console and none of it is scriptable from here.

  1. **Upgrade `cs-resurgence` to Blaze.** Usage and billing, Modify plan. Needs
     a Cloud Billing account. Note what changes: Spark cannot overspend and Blaze
     can, on a project that also serves the live website.
  2. **Set a budget alert before anything else**, in the Google Cloud console
     under Billing, Budgets and alerts. Low, with email at 50, 90 and 100%. This
     is the only thing standing between a misconfigured loop and a real bill.
  3. **Create the Firestore database as `(default)`.** The free quota applies
     only to the default database and a named one gets none at all. **Its
     location is permanent.** Start in production mode, which locks it until
     rules are deployed.
  4. **Create the Cloud Storage bucket in `us-central1`, `us-west1` or
     `us-east1`.** The Always Free allowance exists only in those three.
  5. **Enable Anonymous sign-in**, under Authentication, Sign-in method. That is
     H4's identity and it adds no login screen.
  6. **Decide the worker's credential.** Application default credentials already
     work on this machine and are what the adapter was built against. An
     unattended worker is better served by a service account key, which must live
     outside the repo and never be committed.

  **Deploying the rules is a separate, deliberate, manual step.**
  `server/beta_firestore.rules` and `server/beta_storage.rules` are drafts for
  review and deliberately have no `firebase.json` beside them. A bare
  `firebase deploy` in a directory that has one deploys **hosting** as well, and
  the hosting for this project is the live cosmicresurgence.com. Deploy rules
  only as `firebase deploy --only firestore:rules,storage` from a directory whose
  `firebase.json` names nothing else.

  **Done, 21 September 2026.** Blaze is on, Firestore is the `(default)`
  database in `nam5` with `freeTier: true`, and the bucket
  `cs-resurgence.firebasestorage.app` is in `US-WEST1`, one of the three regions
  carrying the Always Free allowance. **The equivalence test passes against the
  live project, 47 of 47**, and leaves nothing behind in either service.

  **A day was nearly lost to an error that named the wrong thing.** Every
  billable Storage write returned

      403 the billing account for the owning project is disabled in state absent

  while `gcloud storage cp` of the same object to the same bucket succeeded
  throughout. The project was never at fault: `billingEnabled` was true against
  an open account the whole time.

  **The owning project in that message is the credential's quota project, not
  the bucket's.** A user credential from `gcloud auth application-default login`
  carries whichever project it was last run against, here `kalbot-cloudrun`, and
  every request from the Python libraries was attributed there. gcloud does not
  use ADC, which is why it was unaffected and why the two disagreed.

  `FirebaseTurnStore.credentials()` now pins the quota project to the galaxy's
  own project, so the store bills what it is reading and writing whoever is
  logged in. A service account key carries no quota project and was never
  exposed to this.

  **A wrong diagnosis was recorded here first** and is worth keeping rather than
  quietly replacing: this entry claimed the likely cause was a Firebase plan
  record pointing at one of two closed billing accounts on the same login. That
  was plausible, consistent with everything observed at the time, and wrong. What
  distinguished it was not more reasoning but one command, `gcloud storage cp`,
  which isolated the failure to the client rather than the project.

- [x] **H1. A Firebase store adapter.** `server/firebase_store.py` implements the
  `turn_store` interface as `FirebaseTurnStore`, and `open_store` dispatches on a
  `firebase://<project>/<galaxy>` spec. Everything lives under a `beta/` prefix in
  both services so it cannot collide with the website on the same project.

      Firestore   beta/<galaxy>                  clock, roster, hash, directory row
                  beta/<galaxy>/archive/<turn>   the referee's record, as one JSON string
      Storage     beta/<galaxy>/turns/0007.b64
                  beta/<galaxy>/submissions/0007/<civ>.b64
                  beta/<galaxy>/notes/0007/<civ>.txt

  Three choices worth naming. **One document per galaxy holds both the store's
  state and the directory's row**, so a publish is a field merge rather than a
  whole-document replace and the referee cannot clobber a name or status the
  operator set mid-turn. **Anything keyed by a civ name is a Storage object and
  never a Firestore field or document id**, because a civ name is a username a
  player typed and Firestore restricts both, which is also why the archive record
  is stored as a JSON string rather than a map. **Objects hold the same base64
  wire bytes the directory store writes**, so a galaxy moves between a folder and
  Firebase by copying, at the cost of about a third more egress.

  One class of bug disappears rather than being ported. `TurnStore.state` retries
  for two seconds because `os.replace` is not atomic over SMB and a reader can
  see `state.json` briefly absent. A Firestore document replace is atomic, so the
  adapter has no equivalent, and says so rather than omitting it silently.

  **The equivalence test this item's done-when referred to did not exist.** F1
  claimed a 24-check equivalence run against a directory and the HTTP service,
  and that run was ad hoc and never committed, so the plan cited a test nothing
  could execute. `server/tests/test_store_equivalence.py` is that test now, 131
  checks across all three implementations, with 88 when Firebase is skipped and a
  skip that says so. `server/tests/test_galaxy_directory.py` adds 59.

  | | result |
  |---|---|
  | directory, HTTP and Firebase on the emulator | 131 passed, 0 failed |
  | directory and HTTP, Firebase skipped | 88 passed, 0 failed |
  | `referee.py --start` and status against a Firebase spec | resolved, `referee.py` unchanged |

  **The two existing implementations were already not equivalent.**
  `HttpTurnStore.start` cannot take a `turn` argument because the service has no
  such parameter. The test therefore drives `start` through the blob-derived
  path, which is the one the referee uses.

  **All of this ran on the emulator.** Nothing was created in the live project,
  because nothing could be: see H0.

  **Done when:** the equivalence test passes against the live project, and a
  referee resolves a real turn end to end through it with the directory and the
  Firebase store reporting the same canonical hash.

- [x] **H2. The referee machine is pull-only.** It opens outbound connections to
  Firebase and accepts nothing inbound: no port forward, no dynamic DNS, no
  router change. This is the property that keeps a home address out of the beta,
  and it is a constraint rather than a consequence, because the first "just
  expose `turn_server.py`" shortcut removes it without anything failing.

  **Done, 28 September 2026.** The operator read the router's configuration and
  reported no forwarding rule. Turns had already been resolving unattended for
  a day by then, which is the evidence this item specifically refuses to accept:
  a turn works identically whether or not a rule exists, so only the
  configuration can answer it.

  What this closes is today's arrangement, not a property that keeps itself.
  A rule can appear later without anyone deciding to add one, UPnP being the
  usual way, and nothing here would fail if it did. Worth re-reading whenever
  the network changes rather than treating as settled forever.

- [x] **H3. Firebase is the record; the PC is a worker.** State, turns,
  submissions, notes and the archive live in Firebase. The PC holds the game
  client, the client lock, and files it is free to lose.

  Two payoffs, and the second is the reason for the first. A machine that reboots
  for Windows Update loses nothing and resumes. And the cloud offload later swaps
  which machine runs the worker, with no data to migrate.

  **Done, 28 September 2026**, on `firebase://cs-resurgence/h3check`. The first
  reboot, on 27 September, closed N1 and not this: that galaxy was
  `server\uidemo\sandbox`, a folder on the same disk, so every byte that
  survived survived because the machine did.

  This run took the machine's half away first. Before the reboot, **1,867 files
  were deleted**: `server\referee_work`, `server\saves` and `server\worker_work`
  entire. The galaxy read identically before and after that deletion, because it
  is not on this machine. Then the reboot, and at logon:

      01:46:28  worker: galaxy firebase://cs-resurgence/h3check
      01:46:28  worker: turn 12, due 01:42:15 (-253s)
      01:46:31  worker: turn 12 came due 4 minute(s) ago and nothing closed it
      01:46:44  captured save_000_20260928_014644_g0_t13.b64
      01:46:48  referee: published turn 13, canonical c3ed83764b25527e

  Turn 12 to 13, hash `ac809c9532c32a6b` to `c3ed83764b25527e`, read back out of
  Firebase rather than off the machine under test. Twenty seconds from the
  worker starting to the turn being published, with nothing restored by hand.

  **`save_000` is the part worth keeping.** The capture counter restarted at
  zero because `server\saves` was genuinely gone, so the resume cannot have come
  from a capture or a tick file left behind. That is the difference between a
  machine that rebooted and a machine whose state was rebuilt from the store.

  **The worker had never refereed a Firebase galaxy before this.** Every referee
  run in the project until now was against a folder. The first one, an hour
  earlier, closed turn 11 and published turn 12 in fifteen seconds.

- [ ]~ **H4. Anonymous auth and security rules.** Firebase Anonymous Auth gives
  each install a stable UID with no login screen, no password and no account.
  The username stays exactly what it is today, a claim typed into the launcher.

  This is not an anti-cheat measure and does not pretend to be. It is there
  because a publicly addressable store with no rules can be emptied by anyone who
  finds it, and because upload and egress are metered.

  The rules: a client may write only its own submission for the current turn; may
  never write a published turn; may never delete. The worker holds a service
  account and is the only writer of turns. Object size is capped and a budget
  alert is set before the first stranger has the launcher.

  **"Its own submission" is not expressible as H1 stores them.** A rule can test
  `request.auth.uid`; a submission object is named for the civ, which is a
  username the player typed, and there is no relation between the two that a rule
  can evaluate. Until one of two things is done, the rule is a size cap and a
  deletion refusal rather than an ownership rule:

  - name the submission object for the uid rather than the civ, and keep the
    mapping in the galaxy document, or
  - mint a custom claim carrying the civ name when a seat is claimed at J4, and
    have the rule compare against the claim.

  The second fits J4's seat binding and is probably the one to take, since that
  binding has to exist anyway.

  **H7 settled this and neither option was taken.** Design B puts the function
  between the player and the store, so ownership is compared in code, uid against
  the seat that claimed the civ, and no claim scheme is needed at all. An earlier
  draft of this item called the custom claim "probably the one to take" and then
  said not to build one, which was a contradiction while it stood.

  **So this item's done-when is unreachable as written.** It asks for a client
  refused *by the rules*. Under design B a player never touches a client SDK, so
  Security Rules govern nothing on the player path, which is also why the bucket
  can carry `allow read, write: if false` while the referee works normally. The
  enforceable version, and the one to hold H7 to: **the function refuses a
  request whose token uid does not hold the seat it is writing.**

  **The rules files stay deny-all, and that is now permanent rather than
  provisional.** An earlier line here said deny-all held "until a client path
  exists for it to govern". Under design B no such path will ever exist: the
  relay hands out **signed URLs**, which are a Cloud Storage credential and
  bypass Security Rules exactly as the admin path does. So the note in
  `server/beta_storage.rules` about the ownership half being inexpressible is
  moot rather than outstanding.

  `server/beta_firestore.rules` and `server/beta_storage.rules` hold drafts for
  review. They are deliberately not accompanied by a `firebase.json`, so no
  deploy can pick them up from beside them; this project serves the live website
  and rules are deployed by hand, deliberately, once.

  **This done-when outlived the design it was written for.** "By the rules"
  presupposes Security Rules doing the enforcement, which is design A. B was
  chosen precisely because no rule can express the thing that matters: a rule
  can test `request.auth.uid` and nothing else about who is asking, and a
  submission object is named for a username the player typed, so no rule can
  relate the two. See H7. Enforcement is an `if` statement in the relay, on
  purpose, and the deny-all rules exist so that nothing reaches the bucket
  except through it.

  **The substance is measured**, against the deployed service rather than the
  emulator: a caller authenticated as one player is refused writing another's
  submission, refused every referee route, and cannot delete anything because
  no route deletes. J4 records the seat half, with two anonymous sign-ins and a
  second machine.

  What is genuinely unverified is the rules themselves, and the only thing they
  have left to say is "nothing gets in except through the relay". Reading the
  live rules back on 27 September showed both Firestore and Storage deny-all,
  which is the whole of their job now.

  **Done when:** a client authenticated as one player is refused writing another
  player's submission, refused publishing a turn, and refused deleting
  anything, and a client bypassing the relay reaches neither Firestore nor the
  bucket.

- [x] **H7. The player's launcher cannot reach Firebase, and nothing in the
  tests could have noticed.** What H1 delivered is the *referee's* transport. It
  is not the player's.

  `launcher.py` opens its store through `open_store(cfg["store"])`, so a
  `firebase://` spec builds `FirebaseTurnStore`, which constructs
  `firestore.Client()` and `storage.Client()`. Both are Google Cloud **admin**
  clients and authenticate with IAM. A player has no Google Cloud credential, and
  shipping a service account key inside a distributed executable is not an
  option: it is project-wide, it would let any holder do anything to any galaxy,
  and it cannot be revoked per player.

  **This is also why the Storage rules look inert.** Firebase Security Rules
  govern the client SDKs. The admin path is not subject to them, demonstrated on
  the live project: listing the bucket succeeded while its rules read
  `allow read, write: if false`. Deny-all is the correct default today and stays
  correct until a client path exists for it to govern.

  **The equivalence test cannot catch this**, because every run so far, emulator
  and live, authenticated as an administrator. Proving three stores agree says
  nothing about whether a player can open any of them.

  Two designs, and they are not close:

  **A. The launcher talks to Firebase directly**, over the Firestore and Storage
  REST APIs with an anonymous Firebase Auth ID token, Security Rules enforcing
  ownership. No new server component. Costs a second Firebase transport that has
  to stay equivalent to the admin one forever, and it inherits H4's unsolved
  problem, that a civ-named submission path cannot express "mine".

  **B. The launcher keeps speaking HTTP, to a Cloud Function in front of the
  store.** `HttpTurnStore` already exists, is already covered by the equivalence
  test, and has worked from the launcher since F3. Pointing it at a function URL
  instead of a LAN host is close to free. The function holds the admin
  credential, verifies the caller's Firebase ID token, and enforces ownership
  **in code**, where it can compare the token's uid against the seat that claimed
  the civ. The home address stays hidden, so H2 still holds.

  **B was chosen and is built**, in `functions/`, passing on the emulator. The
  deciding reason was H4 rather than the transport: ownership becomes an `if`
  statement, and there is one Firebase transport rather than two.

  | run | result |
  |---|---|
  | equivalence, no Firebase | **118 passed** (was 105) |
  | equivalence, Firestore and Storage emulator | **170 passed** |
  | `test_relay_function.py`, with the auth emulator | **77 passed** |
  | end to end through the real Cloud Functions emulator | **6 passed** |

  **"Close to free" was wrong in two ways.** Downloads redirect to a signed URL
  cleanly. Uploads cannot: `urllib` refuses to re-issue a POST on a 307 and never
  re-sends a body, so a submission takes a ticket round trip, `GET /upload/...`
  then a signed PUT then `POST /commit/...`. And blobs fetched from Storage
  arrive in the base64 wire form rather than the raw form the HTTP service
  returns, so the store accepts both. A redirect handler also has to **drop
  `Authorization` when the host changes**, because Cloud Storage reads an
  unsigned bearer header as a credential and rejects an otherwise valid signed
  URL.

  **This item assumed a seat binding that nothing in the tree writes.** "Compare
  the token's uid against the seat that claimed the civ" had no data behind it:
  there was no uid-to-civ mapping anywhere. The relay defines one, a `seats` map
  of `{uid: civ}` on the galaxy document, that way round because a uid is a safe
  Firestore map key and a civ name is a username a player typed. A galaxy without
  it cannot be played through the relay at all, so this item's done-when depends
  on J4, on the operator writing `seats` by hand, or on the opt-in
  `seat_claim: "first-use"`, which binds an unheld roster civ on a uid's first
  **submission** only and is off by default because among strangers first use is
  a land grab. An unseated uid gets the galaxy's public face and 403 on anything
  per-civ.

  **Signing needs one operator action.** A Cloud Functions runtime account has no
  private key, so `generate_signed_url` signs through the IAM Credentials API.
  That needs `iamcredentials.googleapis.com` enabled and
  `roles/iam.serviceAccountTokenCreator` granted to the runtime service account
  **on itself**. Both commands are in `functions/deploy.py`'s docstring.

  **Deployed, 27 September 2026**, to `us-west1`. The deploy could not reach
  Hosting for three independent reasons: `functions/firebase.json` carries no
  `hosting` key and `deploy.py --check` refuses if one appears,
  `--only functions:relay` names one function of one codebase, and the CLI reads
  only the config in the directory it runs from, which is never `site/`.
  Container images are on a three-day cleanup policy so they stop accumulating.

  Measured against the live service with an anonymous token and a throwaway
  galaxy, since deleted: `/state` answered 200, `/turn/11` answered 302 to a
  `storage.googleapis.com` URL carrying `X-Goog-Signature`, and following it
  returned 12,908 bytes that parse as a save blob. The signed URL is what proves
  the `serviceAccountTokenCreator` grant, because nothing else in the relay
  needs it.

  **A deployed function has two hostnames and they are gated separately.** The
  `cloudfunctions.net` URL the deploy prints answers 401 with an HTML body,
  which is Cloud Run refusing the request before any of this code runs: that
  hostname checks a function-level IAM policy which is empty. The `run.app`
  hostname checks the Cloud Run policy, which `firebase deploy` had already set
  to `allUsers: roles/run.invoker`, and answers from the relay itself. So the
  beta reaches the relay at its `run.app` hostname and no permission had to be
  granted to make that work. Diagnosing this the other way round cost a wrong
  guess: an HTML 401 was read as a missing invoker binding, and the binding was
  already there.

  **The `run.app` hostname is the one the beta ships with**, decided 27
  September rather than opening the function resource to `allUsers` as well for
  a tidier URL. It carries a project-and-region hash rather than a chosen name.
  Recreating the service in the same project and region returns the same host,
  so a redeploy is safe; changing region or project would not, and every shipped
  launcher reaches it through `BETA_DIRECTORY`. A custom domain is the answer if
  that ever has to move.

  **Done, 28 September 2026, on a second machine.** A Windows PC with no
  checkout, no virtualenv, no `gcloud` and no Google credential of any kind ran
  the packaged v0.1.3 build, listed `h3check` through the relay's directory
  route, signed in anonymously, claimed the `Neighbor` seat, played a turn and
  sent it. The referee then closed that turn:

      referee: no submission from ['DemoPlayer', 'BadGuy']
      referee: closing turn 17 with 1 submission(s)
      referee: published turn 18, canonical 75962d6e6cace503

  Naming only the other two as missing is the proof it counted the stranger's.

  **The order survived, not just the bytes.** The operator moved one farmer to
  a miner on a non-homeworld planet. Diffed against the served turn, planet
  #138 went from 7 farmers to 6 farmers and one miner, planet #140 was
  untouched, and `DemoPlayer` and `BadGuy` were byte-identical. One citizen
  moved, on the planet it was moved on, and nobody else's empire changed.

  **It found a bug that made this impossible for everyone.** The turn loop asks
  whether this civ has already played before serving it, and that read required
  a seat, while under `first-use` a seat binds only when a player submits. So
  every player's first turn on every galaxy was unreachable: to play you had to
  read your submission, to read it you needed a seat, to get a seat you had to
  submit. It had never shown up because every previous test seated its players
  by hand. A seatless caller is now told there is no submission of theirs,
  which is true, rather than refused.

- [x] **H8. A player can find a galaxy without being the project.** The relay
  answered every question about a galaxy a player already knew the id of, and no
  question about which galaxies exist. The launcher's default directory was
  `firebase://cs-resurgence`, which builds a `firestore.Client` and streams the
  collection as an administrator, so the Galaxies page worked on the operator's
  machine and could not have worked on anyone else's. The same shape of gap as
  H7 and in the half H7 did not cover.

  `GET /` on the relay is the one route that is about no galaxy. It serves each
  galaxy the fields `/state` serves plus `id` and `name`, through the same
  allowlist and the same private-field filter, so a galaxy says the same about
  itself in a listing as it does when asked directly. Signed in like every other
  route: anonymous sign-up is open, so the gate costs a stranger nothing and
  keeps one rule rather than two.

  `HttpGalaxyDirectory` is the launcher's side of it, and the row it hands back
  names a store back through the same relay, so what is listed is also what is
  playable. Registering a galaxy and opening or closing it refuse rather than
  silently doing nothing: those are the operator's, and the relay refuses them
  for the same reason it refuses the referee's routes.

  **Done when:** done. 20 checks in `test_relay_function.py`, covering the
  refusals, the filtered fields, the row a directory builds from them, and a
  store opened from that row reading the same turn the row showed.

- [x] **H9. `has_submitted` is a bucket listing per poll, and it is the item
  that caps how many players the beta can have.** The Games page asks it every
  120 seconds for each joined galaxy. Over HTTP that is a Class A listing
  operation each time: roughly **21,900 Class A operations a month from one
  player with a launcher open**, against the **5,000 a month** H0 bought by
  creating the bucket after February 2026. One player exceeds the whole
  allowance more than four times over.

  A `submitted` array on the galaxy document, written where the relay already
  commits a submission, would ride the `/state` read the launcher already makes
  and cost nothing extra. That is the operator's own steer, to prefer a cheap
  Firestore field over a repeated bucket question, and this is the largest
  instance of it left.

  `abandonment.find_template` is the same shape and smaller: it downloads up to
  ten whole turn blobs to find one with a free planet.

  **Built and verified on the emulator, 28 September 2026, and measured live
  the same night.**
  The galaxy document carries `submitted`, a map from the current turn to the
  civs that have committed for it. `start` and `publish` write the new turn
  with an empty list, `publish` replacing the map whole so it never grows, and
  a relay commit adds the civ with an array union once it has read the upload
  back and found it a save. A commit that refuses and deletes an upload takes
  the civ off again, because that upload may have replaced a submission
  recorded earlier in the turn.

  `FirebaseTurnStore.submitted_civs` answers the current turn from that field
  and any other turn from the bucket. The relay's `/submissions` route passes
  in the document it already reads for every request, so a Games page poll is
  that one Firestore read and no Storage operation at all. Measured by counting
  `list_blobs` at the client the relay holds: three polls of the current turn,
  **zero listings**; the control, one poll of a past turn, one listing.

  Three decisions worth naming. **The field is keyed by turn and only the
  document's own turn is answered.** A commit that read turn N and landed just
  after the publish of N+1 writes a key for N holding one civ, and answering a
  past turn from that would drop everyone who committed before the publish.
  **`has_submitted` on the Firebase store still asks the bucket**, one Class B,
  because `abandonment` asks it of past turns and wants the answer the referee
  merges from. **A galaxy without the field falls back to listing**, which is
  every live galaxy until the worker restarts on this code, so nothing breaks
  in between.

  No launcher change was needed. The launcher reaches a Firebase galaxy
  through `HttpTurnStore`, whose `has_submitted` was already the relay's
  `/submissions` route, so builds already in players' hands get the saving
  once the relay is redeployed.

  Mutation-confirmed: the route listing again, `publish` merging rather than
  replacing, a refused commit not forgetting, the turn guard removed, and a
  commit never recording each fail a named check.

  **Measured live, 28 September 2026.** The relay was redeployed, the worker
  restarted on the new code, and turn 18's field backfilled once from the
  bucket so the live turn did not fall back to listing. The second machine ran
  v0.1.4 on the Galaxies page from 23:25 to 23:46 and made **eleven
  `/submissions/18` polls**, one every two minutes, each answered 200, read
  from the relay's request log. Cloud Monitoring's
  `storage.googleapis.com/api/request_count` for the bucket shows **no
  operation of any kind** in that window, `ListObjects` included.

  An empty series can also mean Monitoring had not caught up, so a control:
  one deliberate listing at 23:46:47 was counted in the 23:48 point, three
  minutes later. So the zero is a count and not a gap.

  | | `/submissions` polls | `ListObjects` |
  |---|---|---|
  | before, by the code | one per poll | one per poll |
  | after, 23:25 to 23:46 | 11 | **0** |
  | control, one deliberate listing | | 1, seen 3 minutes later |

  **Getting a launcher to poll at all took two findings of its own.** The poll
  runs only while the Galaxies page is on screen, and only for galaxies with a
  record in `joined.json`, which only the Join button writes; see J6. Four
  hours of the second machine's traffic before tonight held no `/submissions`
  call. So the 21,900 a month above is a launcher parked on that page all
  month by a player who clicked Join: still the worst case the allowance has to
  survive, and not what an ordinary session does. The same log is where H11
  came from.

  The turn live at the deploy was transitional: a submission made before it
  was missing from the field until backfilled, and a galaxy without a backfill
  would read it as not submitted until the player submitted again.

  `abandonment.find_template` is untouched and still downloads up to ten turn
  blobs when a join needs a free planet. It runs on the referee at a turn
  boundary, not on a poll.

  **Done when:** a launcher left open on a joined galaxy costs no Class A
  operations beyond the reads it was already making, measured rather than
  reasoned about.

- [x] **H10. The relay and the store keep separate copies of the submission
  listing.** `functions/relay.py` has its own `_submitted_civs` and
  `turn_store` has `submitted_civs`. They agree by inspection, not by a shared
  call or a test, so the two can drift and the first sign would be a galaxy
  disagreeing with itself about who has played.

  `operator_view`'s allowlist does not carry `submitted_civs` either, which is
  exactly the cheap accessor it wants and would want more once H9 makes it a
  field rather than a listing.

  **Done, 28 September 2026, with H9.** `_submitted_civs` is gone from the
  relay, and its route calls `FirebaseTurnStore.submitted_civs` with the
  document it already holds. The relay test checks that the relay has no
  listing of its own and that the document agrees with the bucket for the
  current turn.

  `operator_view` now asks `submitted_civs` once per refresh rather than
  `has_submitted` once per civ, which on Firebase was a Class B per player,
  and it is on the allowlist.

  **Done when:** one implementation answers both, or a test fails when they
  disagree.

- [x] **H11. A follow poll reads the galaxy state five times.** Seen in the
  relay's request log on 28 September, not yet traced in the code: each time
  the second machine's v0.1.3 launcher began following h3check it made five
  `GET /h3check/state` requests inside one second, before fetching the turn.
  Each is a relay invocation and a Firestore document read. H5's read figures
  assume one `store.current()` per poll, so if this repeats on the steady
  poll and not only at the start of a follow, those figures are low by up to
  five times. The likely shape is several `HttpTurnStore` methods, `current`,
  `is_closed`, `reclaimed` and the like, each fetching `/state` for itself.

  Firestore reads are not the binding allowance, so this does not block the
  beta the way H9 did, but each request is also a Cloud Run invocation.

  **Done when:** one follow poll makes one `/state` request, or the count is
  measured and written into H5.

  **Done, 29 September 2026.** Traced and measured with a real `HttpTurnStore`
  against `turn_server.py`, counting requests at the server, with the real
  `player_turn.follow` and the client stubbed out. The five are not the loop
  repeating itself. Four come from the launcher before the loop starts:
  `start_multiplayer` asks `exists`, `state` and `civs`, and `_refresh_turn`
  asks `current` once while it has heard nothing from the loop. The fifth is
  the loop's own first `current`. The harness reproduced exactly five before
  the first `/turn/7`. The steady poll was already one `current` per read,
  plus one `/state` per upload from `submit`'s own closed check.

  `HttpTurnStore` now answers every state accessor asked inside
  `STATE_SECONDS`, 1.5 seconds, from one `/state` answer. Any write through
  the store forgets it, so a caller reads its own writes at once, and an
  answer whose request overlapped a write is not kept. A write made
  elsewhere, which is the referee publishing, is seen at most 1.5 seconds
  late. The window is under the loop's 2-second floor, so every read the loop
  schedules still reaches the service.

  | measured at the service | before | after |
  |---|---|---|
  | `/state` before the turn is fetched | 5 | **1** |
  | `/state` inside the first second of a follow | 6 | **1** |
  | scheduled loop reads answered from memory | | **0** |

  The equivalence test's HTTP run passes `state_seconds=0`, because it writes
  through the directory and reads straight back over HTTP. It had been
  passing only because the check before it ends on a write. The window has
  its own test, `test_state_reads.py`, 18 checks, each mutation-confirmed:
  no window, a write that keeps the answer, an overlapping read kept, one
  parsed object handed to every caller, and a window over the floor.

  **This reaches a player only in a new launcher build.** A frozen launcher
  carries its own copy of `turn_store.py`, so builds already in players'
  hands keep making five.

- [ ] **H12. A poll budget for hundreds of always-open launchers.** The target
  is hundreds of players who leave the launcher open all month. Everything
  below is at 4-hour turns, one launcher following one galaxy, and the worst
  case H9 named: the Galaxies page left on screen with one joined galaxy.

  **Free tiers**, read from Google's "Free Tier usage limits" page on 29
  September 2026. Firestore: 50,000 reads a day per project. Cloud Storage:
  5,000 Class A and 50,000 Class B operations a month, in `us-west1` among
  others. Cloud Run: 2 million requests, 180,000 vCPU-seconds and 360,000
  GB-seconds a month. The same page lists Cloud Run functions separately at
  2 million invocations, 400,000 GB-seconds and 200,000 GHz-seconds. **Which
  of those two allowances a Firebase 2nd gen function draws on needs
  verifying against current Google billing documentation.** The request
  count is 2 million either way. No price beyond a free tier is given here,
  since none was verified.

  **What one launcher asks, per day.** The follow loop was counted on a fake
  clock across a whole day with the referee publishing 10 seconds after each
  deadline. The Games page figure is the code's 120-second timer, one listing
  and one `/submissions` per joined galaxy per tick.

  | per launcher per day | relay requests | how known |
  |---|---|---|
  | follow loop, window closed 5 minutes into each turn | 941, of which `/state` 893 | measured |
  | follow loop, window open all day | 960, of which `/state` 888 | measured |
  | Games page on screen, one joined galaxy | 1,440 | from the code |
  | **both, all day** | **about 2,390** | |

  **What each request cost at the relay, before this item**, counted on the
  emulator at the client libraries: every galaxy route one Firestore read of
  the galaxy document, `/turn` and `/submission` one Class B existence check
  each, `/note` one Class B download, and `GET /` one read per galaxy in the
  catalog, because it streams them all. So a launcher's Firestore reads were
  its request count plus the listing's multiplier, and the project's reads
  grew with launchers times galaxies.

  **Built, 29 September 2026: a warm relay instance holds a galaxy's public
  face.** `/state`, `/submissions` and `/turn` are served from a copy of the
  galaxy document read by that instance. A copy is held up to the galaxy's
  deadline, because the referee publishes only once a turn is due, and past
  it for a twentieth of how overdue the turn is, floored at 1 second and
  capped at 60 (`STATE_CEILING`). The listing is held the same way, for the
  shortest window of the galaxies in it, so a row is never held across a
  publish and the Games page is not sent to ask about a past turn, which
  would be a bucket listing. Every route that checks a seat, a closed galaxy
  or the turn being played still reads the document afresh, and a commit
  through the instance drops its copy. Two Storage checks went with it: the
  turn the document is on is redirected without asking whether it exists,
  since `start` and `publish` write the object before the document names it,
  and a submission the document records is redirected the same way. A
  submission it does not record is still asked of the bucket, so a missing
  record can never make a submission look absent to the turn loop's guard.

  Measured on the emulator by `test_relay_costs.py`: a cold listing of three
  galaxies reads 3 and the next inside its window reads 0; twenty `/state`
  requests inside a window read 0; the current turn and a recorded submission
  redirect with 0 existence checks where they had 1. Beside each saving, the
  thing it must not cost: a galaxy closed while a copy says open is refused
  its upload ticket at once, a seat moved while a copy is held is refused its
  old civ at once, and a player's own commit is read back at once. 32 checks,
  and each of the 12 changes is mutation-confirmed.

  **What that makes the project's reads.** Held copies cap reads per galaxy
  per warm instance however many launchers ask, so the per-launcher term is
  only the routes that read afresh. Those per-instance caps were computed by
  stepping the relay's own `hold_for` through a day of demand that never
  stops; the per-launcher remainder is the day count above.

  | Firestore reads a day | 100 launchers | 500 launchers |
  |---|---|---|
  | before, 1 galaxy | 239,000, 478% | 1,195,000, 2,390% |
  | before, 5 galaxies | 527,000, 1,054% | 2,635,000, 5,270% |
  | after, 1 galaxy, 1 instance, estimate | 8,500, 17% | 30,500, 61% |
  | after, 1 galaxy, 3 instances, estimate | 14,500, 29% | 36,500, 73% |
  | after, 5 galaxies, 3 instances, estimate | 54,600, 109% | 76,600, 153% |

  The parts of the estimate: 1,506 reads a day per galaxy per instance for
  the public face, 1,501 a day for the listing at one galaxy and 8,850 at
  five with staggered deadlines, and about 36 to 55 reads a launcher a day
  for the routes read afresh (its own submission, the upload ticket and
  commit, the note). **The number of warm instances is not measured.** It
  depends on concurrency, which for this function is Firebase's default and
  was not verified, and `max_instances` caps it at 10.

  **What is left, in the order it binds.**

  | allowance | per launcher per month | launchers inside it |
  |---|---|---|
  | Cloud Run requests, 2 million | about 71,700 | about 28 |
  | Storage Class A, 5,000 | at least 180, one upload a turn | about 27 |
  | Storage Class B, 50,000 | 1,440 before, about 1,080 after | 34 before, about 46 after |
  | Firestore reads, 50,000 a day | see above | inside at 1 galaxy |
  | Cloud Run CPU, 180,000 vCPU-seconds | not measured | not known |

  Requests are the binding poll cost now, and nothing in the relay reduces
  them: only asking less often does. The Games page is 60% of a launcher's
  requests, so its 120-second timer is the largest lever, and it is the
  launcher's. Class A is H5's constraint and is uploads rather than polls.
  The Class B figures are per turn from the code paths, 8 operations before
  and 6 after, with the route-level savings measured. CPU needs Cloud
  Monitoring from the live function, since the emulator says nothing about
  billed instance time.

  **Decided, and why.** The listing is held in the instance's memory rather
  than served from a catalog summary document. A warm hit costs no read at
  all where a summary costs one, and it needs no second copy of every
  galaxy's row kept in step by every writer, which is H10's failure shape.
  The cost is the listing term growing with galaxies squared, 21,000 reads a
  day per instance at ten galaxies, so a summary document is the next step if
  the catalog grows past five or so. Cache-Control headers were not added:
  the launcher's `urllib` keeps no HTTP cache, and nothing stands in front of
  the function that would.

  **The trade the held copy makes.** A galaxy's public face can be up to 60
  seconds old in the middle of a turn: an operator's close, a new minimum
  build, or another player's submission on the Games page. A turn the
  operator resolves by hand before its deadline is seen up to 60 seconds late
  by a following launcher. At the deadline, where the referee publishes, a
  copy lives 1 second. `CS_RELAY_STATE_CEILING` and `CS_RELAY_LISTING_SECONDS`
  set the windows, and 0 turns either off. `test_relay_function.py` runs with
  both at 0, because it writes through the referee and reads straight back.

  **Done when:** the relay is redeployed and a measured day of the live
  function's request count, Firestore reads and CPU seconds is written here
  against the estimates above, and the Games page poll is set from that
  measurement.

- [x] **H6. A store fetches one submission, not all of them.** `player_turn.py`
  reached its own submission through `store.submissions(turn).get(civ)`, which
  lists and downloads every player's submission and throws all but one away. Free
  on a folder, a download per player per poll on Firebase, and refused outright
  by the rule at H4 that stops a player reading another player's orders.

  `submission(civ, turn)` now exists on all three stores, argument order matching
  `has_submitted(civ, turn)`, returning the blob or **`None`** when there is not
  one. None rather than raising, because absence is the ordinary answer on a path
  a launcher polls.

  **One deliberate asymmetry:** only a *missing* submission answers None. One
  that is present but unreadable, the SMB sharing-violation case, still raises,
  because the caller is the guard deciding whether there is a submission worth
  protecting and answering None for a file that exists would let it be
  overwritten.

  **This item undercounted the readers.** It said three sites, all in
  `player_turn.py`. There were five. `turn_server.py`'s own
  `GET /submission/<n>/<civ>` route was building the entire `submissions(n)` dict
  server-side to pick one entry out of it, so fixing only the stores and
  `player_turn.py` would have left the HTTP store's per-civ path still listing
  everything. And `test_loop_guards.py` carried a hand-copy of `follow`'s push
  body that had drifted from the code it claims to replay; it mirrors it again.

  | equivalence test | before | after |
  |---|---|---|
  | with the Firebase emulator | 131 | **157 passed, 0 failed** |
  | without it, Firebase skipped | 88 | **105 passed, 0 failed** |

  Above the seam and unchanged: the referee's status, `--verify 9
  --no-recompute`, and a real merge of turn 9's 39,210-byte `DemoPlayer`
  submission, 3 orders taken and 0 dropped. The same real submission read out of
  all three stores through both `submission()` and `submissions()` gives
  identical bytes.

  **Left alone deliberately:** `referee.py` at three sites, which legitimately
  wants every submission at the turn boundary. **A recorded follow-up:**
  `dev_tools/check_store.py` does `sorted(store.submissions(turn))` when it only
  wants the names, which on Firebase downloads every blob to discard it. It is an
  operator diagnostic rather than a polled path, so it is cheap to leave and
  cheaper still to fix when someone is next in that file.

  **Now exercised with a real client.** Both halves that needed one have been
  run: `player_turn.serve` stamped and launched a player build that came up as
  `DemoPlayer` at turn 10, `collect` captured 39,125 bytes out of it, and the
  new accessor read those exact bytes back and agreed with `submissions()`. So
  the accessor is proved against a real capture rather than a synthetic blob.

  `referee.resolve_turn` then closed turn 9 with that store's real 39,210-byte
  submission and produced canonical **`c2f1e1e08bd7cd48`**, which is the value
  the blob push plan's A3 recorded for this turn **before** this refactor
  existed, and closing turn 10 reproduced the archived `9d63be7a3e9c3cc8`. The
  store refactor did not change what the referee computes.

- [x] **H5. Cost and quota arithmetic, written down.** One galaxy at 4-hour turns
  is 6 ticks a day, 182 a month. Quotas below read from Firebase's own
  documentation on 20 September 2026, and worth re-reading before the beta opens
  because one of them changed in February.

  **Firestore**, the same on Spark and Blaze: 50K document reads, 20K writes and
  20K deletes a day, 1 GiB stored, 10 GiB egress a month. Per project and **only
  for the default database**; a named database gets no free quota at all.

  **Cloud Storage**, on Blaze only since 3 February 2026, and a bucket created
  now gets the Cloud Storage Always Free allowance rather than the legacy one:
  5 GB-months stored, **5,000 Class A operations a month**, 50K Class B a month,
  100 GB egress a month from North America.

  | | per month | of free |
  |---|---|---|
  | Firestore writes, galaxy doc plus archive doc per tick | 364 | negligible |
  | Firestore reads, launcher polls | poll-driven | 50K/day is ~8,300 polls per player per day at six players |
  | Storage Class B, turn downloads and existence checks | ~5,500 | 11% |
  | Storage stored | ~68 MB added | 5 GB in about six years |
  | Storage egress | ~60 MB | negligible |
  | **Storage Class A, uploads and lists** | **1,456 at six players saving once** | **29%** |

  **The relay changes this less than expected.** A submission commit costs one
  Class B read, not a Class A write, so the binding quota below is untouched.
  What is new is one Firestore document read per relay request, which this item
  counted only for launcher polls.

  **The binding constraint is Storage Class A operations, and the driver is how
  many times a player saves within a turn rather than how many players there
  are.** Six players saving three times a turn is 73% of the allowance; ten
  players saving three times is over it.

  **That interacts with a deliberate design decision.** `player_turn.py` submits
  every 20 seconds through the turn rather than once at the deadline, because the
  first two-machine game lost a player's whole turn to a single write failing in
  a narrow window. A submission identical to the last one is skipped, so the cost
  is driven by how often a player's state actually changes rather than by the
  clock, but an active player still produces many uploads per turn. Three ways
  out, and they are not exclusive: lengthen the interval, keep interim
  submissions in Firestore and write only the final one to Storage, or take the
  Firestore-only variant in H0 and the constraint disappears.

  **This sentence described a design, not the code, and the code is the opposite.**
  It read: the launcher polls against the deadline it already knows rather than
  on a fixed short interval. `player_turn.follow` in fact polls `store.current()`
  every `poll` seconds without pause, and the launcher passes `poll=2.0`, through
  the whole turn **and through the wait between turns**. A launcher left open is
  a launcher reading the store every two seconds all day.

  | poll | store reads per day per player | of the 50K Firestore free tier |
  |---|---|---|
  | **2s, today** | **43,200** | **86%** |
  | 15s | 5,760 | 12% |
  | 60s | 1,440 | 3% |

  So the beta cannot have two players with their launchers open, and the
  constraint is Firestore reads rather than the Storage writes this item spends
  its length on. It predates the capture and upload split and is not caused by
  it, though that made it about 20% worse by waking the nap more often.

  **Done.** `poll_gap` makes the gap a twentieth of the distance to the deadline,
  floored at 2 seconds and capped at 300. The distance is signed, so the same
  rule runs on both sides: it is a V, tightest exactly at the deadline, which is
  the one moment that matters. A turn published on its deadline is still served
  within 2 seconds, and one published 20 minutes late is served 3.5 seconds after
  it appears, having spent 239 reads waiting rather than 36,000.

  | turn length | before | after | players inside 50K |
  |---|---|---|---|
  | 4 hours | 44,484 a day, 89% | **888, 1.8%** | 1 to **55** |
  | 15 minutes | 104,064 a day, 208% | **7,872, 16%** | 0 to **6** |

  The before column was measured by running the same harness against the old
  `player_turn.py` out of git rather than by arithmetic, and it reproduces the
  43,200 above independently, which is what says the harness counts the right
  thing.

  **A second and larger source was found that this item never mentioned.**
  `_refresh_turn` called `current()` **and** `seconds_left()` from `_watch_game`,
  which runs once a second whenever a game window is open: 7,200 reads an hour,
  172,800 a day at 15-minute turns, more than the loop it was reporting on. The
  countdown now subtracts from a deadline the loop already emits and reads the
  store once, when it has heard nothing yet. Measured 1 read across 601 ticks
  against 1,202.

  **Not measured:** that one `store.current()` is exactly one Firestore document
  read. That is taken from this item and from `FirebaseTurnStore.state()` being a
  single fetch, not from a billing console. The Games page also polls the
  directory every 120 seconds, which is a separate path and is not in the figures
  above.

  **Done when:** a measured day of a live galaxy is inside the allowances with
  margin, or the expected monthly bill is written down.

---

## J. The lobby

- [x] **J1. A galaxy directory above the store.** `server/galaxy_directory.py`
  holds `LocalGalaxyDirectory` and `FirebaseGalaxyDirectory` behind
  `open_directory(spec)`, mirroring `open_store`. 59 checks pass against both.

  Kept as a separate interface rather than folded into the store, so the
  directory and HTTP store paths keep working for development and for the
  two-machine test in F2. The local one reads either a folder of galaxies or a
  `galaxies.json` naming store specs, which is what lets a local directory list a
  galaxy that is actually served over HTTP.

  **A row carries a player count and a flag, never the roster**, which is the one
  thing J4 asks of it. Statuses are `forming`, `open` and `closed`, and a closed
  galaxy stays listed and readable rather than vanishing, which is what K5 needs.
  A store that cannot be reached lists as forming rather than taking the whole
  listing down.

  **Still open:** the launcher half. Nothing in the UI reads this yet and
  `multiplayer.json` still names a store, which is J2's work.

  **Done, 28 September 2026**, and by the hardest available route: a second
  machine with no checkout listed `h3check` through the relay's directory,
  opened its store from the row the listing gave it, and played a turn. No
  config file on that machine named a galaxy; the launcher fell back to
  `BETA_DIRECTORY`, which is the relay, exactly as a beta player's would.

  A third implementation landed with it. `HttpGalaxyDirectory` sits behind the
  same `open_directory(spec)` as the folder and Firebase ones, because
  `FirebaseGalaxyDirectory` authenticates as the project and a player is not
  the project. See H8.

  **Done when:** the launcher lists the sandbox from the directory and opens its
  store from what the directory gave it, with no galaxy path in any config file.

- [ ]~ **J2. A Games tab in the launcher.** Replaces the old flow, where the
  website listed active galaxies and a downloaded file was both the galaxy and
  the key. The tab shows the sandbox, its turn, its deadline, how many players
  are in it, and a Join button, and after joining it shows whether this player
  has submitted.

  `multiplayer.json` stops naming a galaxy. What the player has joined is written
  by the launcher, beside `identity.json`, rather than hand-edited.

  **Most of it is done and the remaining word is "joins".** On 28 September a
  second machine with no checkout listed galaxies in the launcher rather than
  on a website, picked one, and played a turn with no downloaded galaxy file
  and no config file naming a galaxy. That is the flow this item exists to
  replace, gone.

  What it did was take a seat in an existing roster under `seat_claim:
  first-use`, which is not the same act as joining: a join is a request the
  referee resolves at a turn boundary, and that path, J3, has never been
  exercised from a machine that was not this one. Until it is, this reads
  "a player played" rather than "a player joined and played".

  **Done when:** a player who has never opened a config file joins the sandbox
  and plays a turn.

- [ ]~ **J3. Joining is a request resolved at a turn boundary.** The launcher
  writes a join request; the worker applies it during the next tick, on the
  authoritative blob, never on a copy a player holds. A player who clicks Join
  during turn N is playing at turn N+1.

  **The referee half is done and verified with a real client.** `server/joins.py`
  applies requests in `resolve_turn`, on the blob the tick produced, **after
  `abandonment.enforce` and before `publish`**, with the durable writes in a
  second call after `publish`.

  **The order was chosen and then proved.** A reclaim frees planets and a name a
  join in the same tick can use, which is the state a full sandbox is actually
  in; and a wipe needs an uncolonised planet for its blank `PLPR`, which is the
  one resource a join consumes. Mutation-confirmed: joins first and the same
  galaxy refuses for want of room.

  **The split across `publish` is deliberate.** A referee killed between the two
  leaves an empire nobody is seated on, which an operator fixes by adding a
  name. The other order leaves a roster naming a civ the galaxy does not hold,
  which fails `screen_submission` for that player every turn forever.

  **A granted join binds its seat, 29 September 2026.** `joins.commit` writes
  `seats[uid] = civ` for the uid that asked, in the same `update_state` merge
  as the roster, which on a Firebase galaxy is one document write. So there is
  no moment when the roster names the new civ and no seat holds it, which is
  the window a stranger's first-use claim would use, and a joiner plays with
  first-use off and no `seat_tool.py bind`. A referee killed before that write
  leaves the recoverable half as before, and the operator adds the name and
  binds the uid the waiting request still names. Three rules decide what
  moves: a uid already playing a civ on the roster keeps it and the new civ is
  left for `bind`, a uid whose seat was reclaimed moves to the new civ, and any
  other uid still holding the new civ's name from before a reclaim loses it,
  since otherwise the reclaimed sign-in could play the newcomer's empire. The
  directory and HTTP stores get no seat map: nothing on those transports
  checks a uid, and writing one into their state would serve every uid to
  every player. `server/tests/test_join_seat.py` runs it on the emulator with
  the relay in process, 33 checks: a join lodged through the relay by X, the
  turn closed through `resolve_turn`, X playing the new civ with first-use off,
  a second sign-in refused with first-use off and on, and a leftover seat of a
  reclaimed name removed. Each of seven mutations fails a named check.

  Live: `Joiner` seated on planet #6 at turn 12 of a real galaxy, **every
  incumbent's planets, ships and `OWNR` bytes byte-identical to a control run of
  the same turn with no join**, counters agreeing, then the turn-13 blob
  cold-loaded in the player build as `Joiner` with one planet. 121 headless
  checks and 40 live ones.

  **A newcomer no longer inherits the donor's explored map.** `add_civ` clones
  seat one, a live player, so in a fifty-turn galaxy that was their whole map
  including where everybody lives.

  **Still open, and it is the other half of the dead end:** a **refused** player
  is never told. The reason is written to `notes/` and to
  `joins/done/<key>.json`, and nothing in the launcher reads either, because a
  refused player is on no roster and `player_turn.follow` never runs for them.
  Their Games row says "you have asked to join" forever, which is where this
  item started.

  **All three stores take a join now.** The relay lodges a request in
  Firestore (`POST /<galaxy>/join`) and serves the caller its own answer by
  key, and `HttpTurnStore` and `FirebaseTurnStore` both carry `request_join`
  and `join_answer`, so the launcher no longer raises `JoinNotAccepted` against
  a Firebase galaxy. What had never reached a player, the refusal, is below.

  **The balance question is settled and mostly built.** The description above was
  wrong and is kept because a decision was taken on it: a joiner was not starting
  behind, they were receiving a **copy of seat one** minus the fleet, measured on
  the turn-180 war galaxy at 21 citizens, 8 designs, 10,820 credits, a chosen
  research field, recruitment 20 and 97 production points. The operator's ruling,
  everyone starts with what everyone starts with, is the right answer to the real
  situation as well as to the one reported.

  A joiner now gets the generation kit, counted across eleven civs in five
  independent generations: one homeworld with a 240-byte `PLPR`, 7 citizens as 4
  farmers, 2 workers and 1 scientist, no military, recruitment 0, no production
  points, an empty queue, **2 Colony Ship hulls with 2 crew**, 1 design, 200
  credits, research unset, plus seat one's `PLPR` rate pair, and since 28
  September the generated food pair rather than the donor's. See J5.

  **This item had no done-when until 28 September**, which is how the refused
  half stayed open while the item read as nearly finished. The two halves are
  not the same size: the accept path is verified against a real client and the
  refuse path has never reached a player at all.

  **The launcher half of the refusal is built, 29 September 2026, and checked
  headless and on the emulator.** For each Games row with a pending joined
  record and no answer kept in it, the refresh asks the store's `join_answer`
  under the request's key (`turn_store.join_key`, which is the uid on the
  relay). That is `GET /<galaxy>/join/<uid>/answer`, one request per waiting
  galaxy per refresh at the existing two-minute cadence, and none for galaxies
  the player is in or has no request in. An answer is written into the joined
  record, so a refusal left on screen costs no further read.

  A refused row reads **join refused** in the You column and offers a
  **Reason** button, and the line under the table says a request was refused
  and to press Reason. Reason shows the worker's own reason string with **Ask
  again**, which goes through the ordinary Join path with every check and the
  notice, and **Clear**, which forgets that galaxy's record and puts View back
  on the row. A granted answer changes nothing on screen: the civ is in the
  roster by the time the answer is filed, so the row is already Play.

  **An old answer stays filed under the same key after a second request**, and
  `_own_join` on the relay reports `answered` ahead of `waiting`, so a player
  who asked again would be shown the old refusal. The launcher tells them apart
  by turn: the worker stamps an answer with the turn it seats into, which is
  later than the turn the request was lodged during, and the record now keeps
  the turn read from the store at the moment of joining rather than the
  listing's, which can be a boundary behind.

  `release/tests/test_join_answers.py` covers it, and its section 7 runs on the
  emulator: a request lodged through the relay as an anonymous user, refused by
  `joins.apply` and `joins.commit` for an overlong name, and read back by the
  launcher's own method with the worker's sentence intact. Each of nine
  mutations to the launcher change fails a named check.

  **Done when:** a player who clicks Join during turn N is playing at turn N+1,
  and a player whose request is refused is told so and why, from a machine that
  is not the referee's.

- [x] **J5. A joiner still inherits seat one's buildings and stockpiles.** The
  unfinished half of the balance ruling, here rather than in the reconstruction
  report because it has to be settled **before the beta opens** and not merely
  recorded as a fact about the format.

  A transplanted homeworld still differs from the same galaxy's own turn-0
  homeworld at **17 offsets** once the owner id is masked: `+15`, `+19`, `+20`,
  `+177`, `+179` to `+188`, `+193`, `+207`, `+208`. Those are the planet's
  stores, food and facilities. So a player joining a turn-80 galaxy lands on a
  world carrying the leader's warehouses on an otherwise starting planet.

  The bytes were deliberately not guessed at: two galaxies agreeing on an offset
  is not evidence of what it means, and the `PLPR` work at K4 records the same
  discipline. Decoding them is the task, and `server/tests/test_starting_kit.py`
  already compares a joiner against a generated civ field by field, so it is the
  place a decoded field gets asserted.

  **Two of the seventeen are now named, 28 September 2026, and the screen is
  what named them.** The operator opened a joined galaxy as the civ that had
  just joined and read **104/1280** food on its homeworld. Those two numbers sit
  at `+15` and `+19`, both already in the list above.

  | planet | owner | `+15` | `+19` | citizens |
  |---|---|---|---|---|
  | #138 | Neighbor | 141 | 240 | 4 |
  | #136 | DemoPlayer | 25 | 360 | 5 |
  | #196 | BadGuy | 38 | 760 | 8 |
  | #140 | Neighbor | 38 | 760 | 8 |
  | #139 | DemoPlayer | 104 | **1280** | 11 |
  | #47 | Joiner | 104 | **1280** | **7** |

  `+19` is the food capacity and `+15` the store. Across six planets and two
  turns of the same galaxy `+19` rises monotonically with population, 2 citizens
  to 80 and 11 to 1280, and `+15` is never above it anywhere. The joiner carries
  **an eleven-citizen capital's capacity on a seven-citizen world**, byte for
  byte its donor's, where a seven-citizen world in the same galaxy reads 600.

  **Fixed the same day.** `starting_kit` now sets the pair, and
  `test_starting_kit.py` compares it against a generated civ field by field
  like every other kit field:

      food 104/1280 -> 0/600

  0/600 is measured, not computed. `client\SinglePlayerGalaxy.dat` is a
  generated galaxy and **both** its starting civs, on separately placed
  homeworlds, read exactly 0/600. Capacity is not a plain function of
  population, since 4, 5, 8 and 11 citizens give 240, 360, 760 and 1280, so
  nothing but a generated homeworld can say what a generated homeworld holds.

  **A reference that looked right and was not**, recorded because it nearly
  became evidence. `make_multiplayer_galaxy.build` **renames** a fixture's
  existing civs rather than generating new ones, so building "fresh" civs on a
  turn-11 galaxy returns that galaxy's turn-11 civs under new names, one of
  which is the donor. Read straight, it says a generated civ has 1280 and the
  transplant is innocent. `test_starting_kit.py` uses the same call correctly
  because its fixture is already a generated galaxy. The check that settled it
  instead was planet #47 itself, unowned at 0/40 in the base galaxy and 104/1280
  after the transplant.

  **Fourteen of the seventeen are still open**, `+177`, `+179` to `+188`, `+193`,
  `+207` and `+208`. A generated homeworld is nearly empty there, `+177 = 7` and
  `+193 = 1` with everything else zero, while a joiner reads `+177 = 11` and
  `+179` to `+188` as `10, 16, 10, 16, 10, 16, 9, 16, 7, 16`. That matches
  neither the generated civ nor the donor, so something other than the clone is
  writing them and what it is should be known before they are reset to anything.

  **Done, 29 September 2026. The fourteen were not fourteen bytes.** Read as a
  section tree rather than at fixed offsets, the rest of `PLPR` is a facility
  list and a 51-byte settled block after `PROD`, and the offsets only looked
  scattered because the facility list is variable length: a donor that has
  built one more facility shifts everything after it by eight bytes. The
  "10, 16, 10, 16" above is a u16 population history, `0x100A`, read a byte at
  a time. Decoded across 627 owned planets in 94 local blobs and 18 turns of
  h3check, with the layout in `merge_orders.SETTLED_LEN`:

  | field | generated homeworld | what a joiner carried |
  |---|---|---|
  | facilities, `(type, count)` | `(2,1) (6,1) (9,1)` | the donor's, plus `(0,1)` it built |
  | population mirror | 7 | the donor's 14 |
  | population history, 7 turns | zeros | the donor's |
  | counter at +16, undecoded | 1 | the donor's |
  | colony age, f32 turns | 0.0 at turn 0 | the donor's, equal to the turn |
  | founding turn, twice | 0 | the donor's 0 |
  | value at +47, undecoded | 10 | the donor's 7 |

  **The facilities are the leader's buildings the ruling was about.** Every
  generated homeworld has types 2, 6 and 9 once each, colonies have none, and
  type 0 appears on played planets with counts up to 8. The h3check capital
  built its first between turns 11 and 18.

  `starting_kit` resets each to the generation value, field by field. A whole
  generated `PLPR` is not copied in, because `wipe_civ` measured fields that
  are uniform within a galaxy and differ between galaxies.

  **One deliberate difference, and it is a judgment.** The founding turn stays
  0 and the age equals the turn, which is how every incumbent homeworld reads,
  rather than a colony founded today. Whether the engine treats founded-at-0 as
  the capital is not known, and matching every capital in the galaxy is the
  reading that cannot be wrong about it.

  **Measured.** A joiner on h3check's turn 18 now differs from a generated
  homeworld at twelve offsets and every one is named: the rate pair at `+4` and
  `+11`, the owner id in the seven citizens and the settled block, and the age.
  `test_starting_kit.py` goes from 61 to 66, including a whole-record byte
  comparison, and removing any one of the resets fails it. The real client
  closed the join turn and two more in `join_kit_acceptance.py`, 57 passed, 0
  failed, and through those ticks the engine ran the reset fields as decoded:
  age 12.0, 13.0, 14.0; the history filling from the front; the counter rising
  from 1.

  **Done when:** a joiner's homeworld is byte-identical to a generated one except
  for the object id and seat one's rate pair, or each surviving difference is
  named and deliberate.

  **Done when:** a join clicked mid-turn produces a playable civ at the next
  turn, and the player is told which system they landed in by a note rather than
  having to find it.

- [ ]~ **J4. A seat is bound to the anonymous UID that claimed it.** A second
  install claiming a name already in the roster is refused. Because the UID is
  per-install, a player who reinstalls Windows would otherwise be locked out of
  their own empire, so the operator can rebind a seat to a new UID.

  **The identity half exists.** `server/fb_auth.py` signs up anonymously on first
  use, keeps the uid and refresh token in `fb_identity.json` beside
  `identity.json`, refreshes before expiry rather than after a failure, and
  returns `None` rather than raising when the network is down so an offline
  launcher degrades instead of crashing. `identity.json` keeps its existing
  meaning: the username is still a claim, and the uid is never shown to the
  player. Google login replaces `_sign_in` and nothing else.

  **"Per-install" is too narrow.** The uid follows the **data directory**, and
  `find_data_dir()` falls back from the install directory to
  `%LOCALAPPDATA%\CosmicSupremacyResurgence` when the former is not writable. So
  a player who moves an unpacked build into Program Files, or runs two copies,
  gets two uids without reinstalling anything. The operator rebind this item
  already needs is therefore routine rather than rare.

  **Anonymous users can expire out from under a seat.** Firebase can delete
  anonymous accounts inactive for 30 days, which would hand a returning player a
  dead refresh token, a fresh uid, and a seat naming a uid nobody holds.
  Measured on `cs-resurgence`: `autodeleteAnonymousUsers` is **disabled**, so
  this does not bite today. It has to stay disabled for as long as identity is
  anonymous, and that is a reason to reach Google login sooner rather than a
  thing to engineer around.

  The refusal still does not name the other players, for the reason F4 already
  gives: a launcher that could list the roster makes "is there a seat for me"
  into a way to enumerate who is playing.

  **Two of the three are done, 28 September 2026**, measured against the
  deployed relay with two anonymous sign-ins rather than reasoned about:

  | | |
  |---|---|
  | A claims `DemoPlayer` | ticket issued |
  | B asks for `DemoPlayer` | **403, already held by another sign-in** |
  | B reads the galaxy's public face | allowed |
  | B claims `Neighbor` | allowed |
  | A asks again for its own seat | still A's |
  | A also asks for `Neighbor` | refused |
  | seats on the document | two, no civ held twice |

  The last two rows are the ones worth having. A refusal that also stopped B
  playing anything, or that let A quietly accumulate a second seat, would have
  passed a test written only around the headline.

  **Rebinding is the third, and is built on the emulator, 29 September 2026.**
  `server/dev_tools/seat_tool.py` runs with the operator's administrator
  credentials, the ones the referee holds, and never through the relay:

      python server/dev_tools/seat_tool.py firebase://cs-resurgence/<galaxy> list
      python server/dev_tools/seat_tool.py firebase://cs-resurgence/<galaxy> candidates
      python server/dev_tools/seat_tool.py firebase://cs-resurgence/<galaxy> rebind <civ> <uid>

  `rebind` moves a held seat in one Firestore transaction and records the last
  move of each civ under `seats_rebound`, which is not a state field and so is
  never served to a player. It refuses a civ not on the roster (a reclaimed one
  included), a civ no uid holds, a target uid that already holds another seat,
  a string that is not a uid, and a uid Firebase Auth does not know, because a
  typo would lock the player out a second time. `--dry-run` says what it would
  do and writes nothing.

  **How the operator learns the new uid.** Not from the player: the uid is
  never shown, and `fb_identity.json`, the only place it is written, also holds
  the refresh token, which is a credential nobody should ask for. `candidates`
  answers from the operator's side instead. It lists every Firebase sign-in
  that holds no seat in the galaxy, most recently active first, with when it
  was created and when it last refreshed a token, and marks sign-ins seated in
  another galaxy. A launcher refreshes on start, so a player who has just been
  refused on a fresh install is the unseated sign-in created minutes ago. With
  more than a handful of strangers signing in at once this becomes a judgement,
  and the launcher showing a short support code would make it exact.

  **Release was considered and not built.** Deleting the seat and letting the
  next first-use claim take it hands an established empire to whichever sign-in
  submits as that civ first, and the roster is public. With first-use off it
  leaves the civ unplayable until the operator binds it anyway. A rebind names
  the uid and leaves no window.

  **`bind` seats a civ nobody holds**, with the same refusals, and refuses a
  held one. It exists because a join granted at a boundary records the asking
  uid under `joined` and does not write `seats`, so on a galaxy with first-use
  off a joiner is on the roster and cannot play through the relay until the
  operator binds them. The operator view names that uid.

  Measured by `server/tests/test_seat_rebind.py`, the relay in process on the
  Firestore, Storage and Auth emulators with real anonymous sign-ins, 59 checks:

  | | |
  |---|---|
  | A plays `DemoPlayer` | submission lands |
  | B asks for `DemoPlayer` | **403, already held by another sign-in**; A's submission stands |
  | six operator refusals | each refused with its reason, seat map unchanged |
  | `candidates` | B is the newest unseated sign-in; no seated uid offered |
  | operator rebinds `DemoPlayer` to B | A's entry gone, B's written, every other seat unchanged |
  | B plays `DemoPlayer` | submission lands |
  | A asks for `DemoPlayer` again | **403**; B's submission stands |

  Not yet run against `cs-resurgence`. The Auth lookups use the Identity
  Toolkit admin API with default credentials there, and the emulator takes a
  fixed stand-in token, so the first live `candidates` is also the first test
  of that credential path.

  **Done when:** a name in the roster is refused from a second install, allowed
  from the install that claimed it, and rebindable by the operator.

- [ ]~ **J6. A player seated without clicking Join has no joined record, so the
  Galaxies page never says whether they have played.** Found on 28 September
  while measuring H9 on the second machine. `joined.json` is written in one
  place, the Join path (`launcher.py`, `save_joined` after
  `send_join_request`), and the Games refresh asks `/submissions/<turn>` only
  for galaxies with a record in it (`if recs and player`, then
  `_submitted_state`).

  Neighbor was seeded into h3check's roster by the operator and took its seat
  by playing under `seat_claim: first-use`, which is every seat on a galaxy an
  operator starts with names in it. So its row showed a Play button and never
  a submitted state. The directory row said `joined=True` the whole time,
  because that flag is `player in civs`; the two notions of "in this galaxy"
  disagree and nothing reconciles them.

  Found by reading the relay's request log rather than the screen: four hours
  of that launcher's traffic held no `/submissions` call at all. Writing the
  record a Join would have written by hand started the polls at the next
  refresh, with no restart.

  Two ways to close it: write a record when a directory row that reads
  `joined` is played, or have `_submitted_state` take `g.joined` rows with no
  record under the player's current name. The first keeps one source of truth
  for "which galaxies am I in"; the second is smaller.

  **Built 29 September 2026, the second way, and checked headless only.** The
  Games refresh asks `/submissions/<turn>` for every row whose directory entry
  reads `joined`, record or not. A row with a record is asked under the record's
  name, as before; one without is asked under the player's current name, which
  is the name `joined` was computed for. Nothing is written into `joined.json`.

  The first way was not taken because a record means "this player asked for a
  seat", and J3 now reads it that way: a pending record with no answer is what
  the refresh polls a join answer for. A record written on Play for a seat that
  is later reclaimed would put the row back into "joining next turn" and start
  answer polls for a request nobody made. With the second way the directory's
  `joined` is the single answer to "am I in this galaxy" and the record is only
  the history of Join presses.

  The poll cost is unchanged per galaxy: one `/submissions` call per refresh for
  each galaxy the player is in, which is what a player who had clicked Join
  already paid. `release/tests/test_join_answers.py` section 1 seats a player by
  roster alone and reads "turn played" and "your turn" off the row; restoring
  either half of the old condition fails it.

  **Done when:** a player seated by first-use sees their submitted state on the
  Galaxies page without having clicked Join.

---

## K. The sandbox galaxy

- [x] **K1. Merge a new civ into a live galaxy without taking a planet someone
  was about to colonise.** `pick_homeworld` already refuses owned planets and
  picks the uncolonised planet furthest from everything claimed. What it does not
  know about is a planet nobody owns yet that a colony ship is flying toward,
  which is the collision that matters in a sandbox. Reserved planets now feed
  into its existing `taken` argument, and it takes a `margin`.

  **An order carries no destination id. It carries a position.** The `ROUT`
  target XYZ at `+16/+20/+24` is a bit-exact copy of the destination object's own
  stored position, and resolving an order means matching that position back to an
  object. Across 234 unique galaxies all **381** `ROUT` targets landed exactly on
  either a `PLNT` or a `SUN ` position with nothing in between, so there is no
  ambiguity band: a 0.01 tolerance and a 1.0 tolerance give the same answer.

  | order type | planet-exact | sun-exact |
  |---|---|---|
  | Move (1) | 37 | 83 |
  | Scout (2) | 0 | 242 |
  | Colonize (3) | 19 | 0 |

  **Most targets name a system, not a planet**, 325 of 381, so every `PLNT` in
  that `SOLA` is reserved, with membership read from the section tree rather than
  inferred from the contiguous id blocks.

  **Two things that read like the destination and are not.** `ref_id` at `+92` is
  non-zero on 29 of the 381 orders and on every one of them equals the enclosing
  `DYNO`'s orbit id, which is where the ship *started*: reading it as a
  destination points a colony ship at its own homeworld. And liveness is `ROUT`
  presence, not the has-orders byte, because engine-issued scouts carry a `ROUT`
  with advancing progress and has-orders clear.

  Confirmed against the referee's own archive: over 152 consecutive same-galaxy
  tick pairs, 7 colonise orders were seen completing, and in 7 of 7 the planet
  that changed from unowned to owned is the one the target resolves to, owned by
  the ordering ship's civ.

  **The reservation changes no pick on any galaxy in this repo**, and that is
  stated rather than hidden. Maximin lands newcomers 615 to 870 away while
  contested planets sit near the incumbents, so the collision never arises
  naturally on existing data. What demonstrates the fix works is a forced test of
  209 cases, hiding every free planet but one a ship is flying to: unfixed, the
  pick takes the contested planet every time; fixed, it refuses every time. The
  reservation costs at most 11 of 162 free planets across a 28-blob sample.

  **The margin is derived, not a constant**, defaulting to the galaxy's own
  median nearest-neighbour system spacing, measured at 143 to 144 in the
  108-system galaxies and 171 to 174 in the 32-system ones. One number cannot
  mean "a system hop" in both densities. It does not bind on any galaxy on disk;
  it is a crowding guard, not part of the placement policy.

  **Donors have to be levelled first.** A civ added by `inject_civ` inherits its
  donor's world, and a generated galaxy does not hand its civs equal worlds: seat
  one carries the homeworld customisation and the rest get the engine default,
  which is worth two `PLPR` bytes and decides games (D3).

  **Seat one is the lowest object id, and two tools were picking it wrongly.**
  `--donor` defaulted to the smallest `OWNR`, which after the first injection is
  the civ just added, so a multi-name run chained each clone off the previous
  newcomer. `inject_civ.seat_one` now takes the lowest object id instead, which
  is both the right answer and a fixed point under its own mutation: a newcomer
  takes `max_object_id + 1` and so can never be selected, which is what makes a
  multi-name run safe rather than an exclusion list bolted on.

  The rule was chosen from the data, over 472 multi-civ galaxies on disk. Of the
  183 holding exactly one homeworld whose rate pair is not the engine default:

  | candidate rule | names the customised seat |
  |---|---|
  | **lowest object id** | **183 of 183** |
  | first `OWNR` in blob order | 60 of 183 |
  | smallest `OWNR` (the old default) | 39 of 183 |

  Narrow where it should be: those 183 are five distinct rosters captured at many
  turns each, so this measures consistency across a galaxy's life rather than 183
  independent generations. What it also shows is stability: `OWNR` length names
  two different civs within one galaxy in 3 of 7, and the lowest object id names
  one in all 7.

  Order independence is proved rather than asserted, and mutation-tested:
  reverting the rule fails four checks. The test also asserts the reverse, that
  naming seat two as donor really does hand newcomers seat two's rates, so the
  passing assertion is not vacuous.

  **The same bug was live in galaxy creation and mattered more.**
  `make_multiplayer_galaxy.build` mapped player names to civs by
  `owner_records(blob)[i]`, which is blob order, and `equalise_homeworlds` then
  levels everyone to `players[0]`. The engine's serialiser does not keep civs in
  allocation order: 171 of the 472 galaxies present something other than the
  lowest object id first, and in the three-civ demo galaxy the injected civ comes
  back first. So the reference world was whichever civ happened to serialise
  first. Measured on `galaxy_demo/turns/0007.b64`, whose blob order begins with
  the injected `Neighbor`:

  | `build(['Alpha','Beta','Gamma'])` | every player's rates |
  |---|---|
  | by blob order | `(32, 44)`, levelled **down** to the engine default |
  | by seat order | `(62, 94)`, levelled up to seat one's |

  `seated(blob)` sorts by object id and both call sites use it. This is the
  difference D3 measured as worth two `PLPR` bytes and a citizen every few turns,
  applied to every player in the galaxy at creation.

  **The live half, done 28 September 2026.** `join_acceptance.py k1 --turns 60`,
  in a real client, 60 turns of each galaxy:

  | | base | with `Joiner` injected |
  |---|---|---|
  | planet #141, the near target | taken turn 13 | **turn 13** |
  | planet #6, the contested one | taken turn 63 | **turn 63** |
  | ships under orders, 60 turns | | **0 divergences** |

  Both colonisations completed on the same turn and to the same civ. At turn 71
  both galaxies read the same ship with the same waypoints and the same fuel.

  **The contested planet is the whole point.** #6 is what `pick_homeworld`
  returns when reservations are not fed to it, and the colony ship needed 52
  turns to reach it, so a run short enough to see #141 settle would have said
  nothing about the case the reservation exists for. `Joiner` was placed on
  #47, neither ship's destination and none of the 12 reserved planets, and grew
  from 7 population to 27 while #6 was colonised on schedule by its owner.

  The injected civ also arrived with the starting kit rather than the leader's:
  200 credits, 0 production points, an empty queue, research unset, 2 hulls. So
  the K1 placement and the starting kit hold together on one galaxy rather than
  only in separate tests.

  **Done when:** a civ is injected into a galaxy with a colony ship in flight,
  the newcomer's homeworld is not that ship's destination, and the colony ship
  completes its colonisation on schedule in a client that actually ran.

- [x] **K2. Repeated joins across a long galaxy.** D4 confirmed one injection. A
  permanent sandbox does this dozens of times, object ids grow monotonically, and
  the `GLXY` civ count and high-water id are rewritten on every join.

  **Done when:** a galaxy takes 12 joins spread across 100 turns and every one of
  them loads, plays and ticks, with the civ count and high-water id correct at
  the end.

- [ ]~ **K3. Detect abandonment.** Countable out of the store already: the archive
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

  **The server half, 29 September 2026.** Most of it was already there:
  `server/abandonment.py` counts consecutive misses out of the archive,
  `warn_after_misses` and `reclaim_after_misses` on the galaxy's state set the
  two thresholds (defaults 6 and 12, 0 turns a stage off), the reclaim wipes
  the civ with `wipe_civ`, takes it off the roster and records `reclaimed[civ]
  = {turn, missed, at}`, which every store serves in its state, and
  `launcher.roster_problem` turns that record into "This galaxy took the seat
  back at turn T after N missed turns in a row".

  **One gap was real: the warning was written where nobody reads it.** It went
  on the note of the turn just missed, and a launcher reads the note of a turn
  it plays. A player who missed a turn played none of the turns that note sat
  on, so the warning never reached anyone. It now goes on the note of the turn
  the silent player is served next, once per turn, carrying the count as that
  turn opened, and a referee rerun over a turn it already closed does not add
  it twice.

  `test_abandonment.py` runs a galaxy through `resolve_turn` with one player
  silent and the thresholds at 2 and 4: no note after one miss, the warning on
  turn 3 saying two turns are left, the next count alone on turn 4, the seat
  reclaimed at the fourth miss with `reclaimed` reading turn 4 and 4 missed,
  and the launcher's own refusal built from that state. 43 checks became 57,
  and each of four mutations fails a named check.

  **Still open, in files this half does not own.** The relay still lets a
  reclaimed sign-in's seat pass `seat_or_refuse`, because the seat map keeps
  the reclaimed entry, so its upload is accepted and then dropped by the
  referee as not on the roster; the relay should refuse it saying the seat was
  reclaimed, when and after how many misses. And `player_turn.report_refusals`
  prints every note line under "the referee refused N of your change(s)",
  which is the wrong heading for a warning.

- [x] **K4. Wipe a reclaimed civ from the galaxy.** Answered, and the unknown this
  item was built around does not exist. `server/dev_tools/wipe_civ.py` does the
  wipe and `server/dev_tools/wipe_acceptance.py` is the live harness.

  **The engine already does to itself exactly what the wipe needs to do.** When a
  ship is destroyed it deletes the `SHIP` section outright, frees the id,
  renumbers nothing, compacts nothing, and never lowers `SAVE+0`. Established by
  diffing archived blobs before any surgery was attempted:

  | pair | what went | what stayed |
  |---|---|---|
  | `war_fork.dat` t110 against `war.L1.dat` t180 | ships 669 and 670, every section and every dword referring to them | higher ids untouched, `SAVE+0` still 678 |
  | `serve_Neighbor.dat` t3 against `turns/0007.b64` t7 | ships 200 and 209, consumed founding two colonies | `SAVE+0` still 209 |

  The second pair matters most: **this is not a combat property.** Two colony
  ships were consumed in ordinary play, holing the ship range at both the bottom
  and the top. And the client demonstrably loads such blobs, because
  `referee.tick` writes its input to `referee_work/tick_<epoch>.dat` and hands
  that file to a fresh client, so every tick input on disk is a blob that loaded.

  **So D1's precedent does not transfer.** D1's failure is positional in the
  system and planet blocks; the ship range above them is holed by the engine
  several times a galaxy.

  **Planets needed more than the field write this item promised.** `PLNT+16` is
  the owner, but un-owning alone leaves the wiped civ's population, stores,
  facilities, production queue and name on the rock. The whole `PLPR` is replaced
  with one taken from a never-colonised planet in the same galaxy.

  **Civs are never cleaned up by the engine.** No archived blob shows an `OWNR`
  removed, and a civ with no planets and no ships kept its `OWNR` through 20
  ticks.

  **The shell won, but not as the fallback this item called it.** Full deletion
  also loads and ticks. It buys 866 bytes and a row off the diplomacy list, and
  the shell wins instead because it creates no dangling reference. `--delete-owner`
  exists and refuses any galaxy where another civ's `OWNR` holds a dword reading
  the victim's object id, which is precisely the abandonment case;
  `--force-delete-owner` overrides.

  **Verified on `galaxy_demo/turns/0011.b64`**, three civs, wiping Neighbor
  (object 206, planets 138 and 140, ship 208). Each check is listed with what
  would have failed it, because a check that could not fail is how the fog
  results went wrong:

  | check | result | failure would have been |
  |---|---|---|
  | load the wiped blob | opened, turn 11, 32 suns | a process exit during load, the D1 shape |
  | 20 turns ×3, across two invocations | all turn 11 to 31, canonical `6b283170b191fbf9` | a short tick, or two hashes differing |
  | tick the post-tick blob again | turn 31 to 32 | the published blob failing to open |
  | colonise planet 138 as DemoPlayer | owner 0 to 198 | the planet staying unowned |
  | **control**, same order against the un-wiped blob | owner stayed 206 | the planet changing hands anyway, which would have made the positive result mean nothing |
  | offline invariants | 42 of 42 | any renumbering, a moved `SAVE+0` or civ count |

  **A trap this item did not mention, and the one most likely to have produced a
  false failure.** A wiped civ owns nothing, and `game_cycle.launch` waits for a
  local civ with at least one planet. A blob stamped for the wiped civ reports
  "did not become readable", which is exactly the shape of the harness bug that
  forced the fog retraction. `wipe_acceptance.prepare` stamps for a survivor and
  refuses to stamp for the victim.

  **A late galaxy has no template to copy, and the first version simply
  failed.** The blank `PLPR` is taken from a never-colonised planet, and in a
  permanent sandbox played for months there may not be one. It raised rather
  than writing something wrong, so nothing in the tree needs repairing, but it
  failed in exactly the situation abandonment exists for. **No blob in the
  archive has reached that state, 0 of 475**, which is why nothing caught it;
  it was pointed out rather than measured.

  **What a blank record actually contains decided the fix.** Across 475 blobs
  and 110,399 free planets every blank `PLPR` is 137 bytes, and 24 of those
  bytes vary:

  | bytes | behaviour |
  |---|---|
  | `+4 +5 +6 +11 +12 +120 +121` | differ between planets in one galaxy, stable over time |
  | `+90 .. +105` | identical for every planet in a galaxy, **different between galaxies** |
  | `+106` | changes from turn to turn |

  So the obvious fix, shipping one canonical blank record in the tree, is the
  wrong one: its 90 to 105 run would belong to another galaxy. A template has to
  come from **this** galaxy, and an earlier turn of it does fine, since that run
  is stable over time. `wipe_civ --template-from` takes one, the refusal names
  it, and `wipe()` takes `template_blob`. Verified against a galaxy with all 160
  planets colonised: refused without a template, and with one the planets come
  back unowned at 137 bytes with this galaxy's own 90 to 105 run.

  **Looked at on screen, and the shell is not a preference but the only option.**
  A wiped civ was compared before and after on the turn-180 war galaxy, where
  GoodGuy and BadGuy have met and fought, played from GoodGuy's seat:

  | screen | after the wipe |
  |---|---|
  | planet ownership icon | gone, correct |
  | recon | gone, correct |
  | news | still lists the history, correct, it is historical |
  | overview score | row still present, as intended and documented |
  | diplomacy | BadGuy still listed |
  | **capital system marker** | **still drawn** |
  | **hovering the planet** | **still names BadGuy as owner** |

  The last two are wrong and the blob is not. Planet 384 reads `owner = 0`, an
  empty name and a blank `PLPR`, and all four ships are gone. So the client is
  drawing those from per-civ state inside the preserved `OWNR`, which the shell
  keeps on purpose.

  **`--delete-owner` cannot be the answer.** Its guard refuses when another civ
  still names the victim's object id, which after any contact is always, and the
  forced result **does not load**: the client starts and exits without opening
  the galaxy, the D1 refusal shape, where the same harness had opened the shell
  version of the same galaxy at the same turn minutes earlier. That closes the
  question this item previously recorded as guarded rather than answered.

  So in a real sandbox, where players have met, the shell is mandatory and the
  stale capital marker comes with it. The score row and the stale marker are the
  same fact, not two choices.

  **Fixed, and it was not `OWNR` at all.** The hover label and the system's 3D
  name come from `EXSY`, each civ's remembered map, which carries a last-known
  owner and the planet's name as that civ last saw it. Both civs' tables still
  held `BadGuy's HQ` against owner 660. `wipe_civ.forget_civ` clears the wiped
  civ from every table using the existing `exsy` decoder, which refuses a table
  it cannot walk exactly rather than flattening it. Confirmed on screen: the
  hover no longer names the civ and the 3D label is gone.

  An earlier note here claimed the record still referenced "SOLA 3" at three
  offsets. The system is **383**; that was a misread field, and scanning for a
  small integer is how it happened. See the report for the decoded layout and
  for the fog question this leaves open.

  **Still open, none of it blocking:** the replaced `PLPR` carries the template
  rock's rates rather than the original's, which is the right choice because
  carrying the original across would preserve a homeworld's customisation boost,
  but `+5`, `+6`, `+12`, `+120` and `+121` still have no established meaning.

- [ ]~ **K5. Ending a galaxy is an operator action.** No season timer. The
  operator calls a galaxy over and starts a fresh one, so there has to be a way
  to close one that stops accepting submissions, keeps the archive readable, and
  tells every launcher why.

  **The operator's half is built, 29 September 2026, and proved on the
  emulator.** Closing already existed underneath: `status: closed` and a
  `closed_reason` on the galaxy's state, every store refusing a submission
  with `GalaxyClosed`, the relay refusing an upload ticket with the reason in
  the refusal, the worker, `joins` and `abandonment` all leaving a closed galaxy
  alone, and `launcher.closed_problem` saying the galaxy has ended in the
  operator's words. What was missing was a way to do it that is not a Python
  prompt. `server/dev_tools/galaxy_tool.py` is that, run with the referee's
  administrator credentials and never through the relay:

      python server/dev_tools/galaxy_tool.py firebase://cs-resurgence list
      python server/dev_tools/galaxy_tool.py firebase://cs-resurgence close sandbox --reason "..."
      python server/dev_tools/galaxy_tool.py firebase://cs-resurgence start season2 \
          --name "Season Two" --generate fresh.b64 --player Alice --player Bob \
          --replaces sandbox --dry-run

  `close` requires a reason, since a player reads it, deletes nothing, and
  reads the current turn and its archive back afterwards. `start` registers
  the galaxy in the directory, which is what puts it on the Galaxies page, and
  publishes turn one from either a generated galaxy made into one civ per
  player by `make_multiplayer_galaxy.build`, or a blob that already holds a
  civ for every player. It refuses an id that already has a turn, and takes
  `--warn`, `--reclaim` and, on Firebase only, `--seat-claim first-use`.
  `--replaces` closes the old galaxy only after the new one is published, so a
  failed start leaves players with the galaxy they had, and its default reason
  names the new galaxy. `--dry-run` checks everything, building a generated
  galaxy in memory, and writes nothing.

  A directory row now carries `closed_reason` for a closed galaxy, so the
  Galaxies page can say why a galaxy ended from the listing it already holds.

  `server/tests/test_galaxy_tool.py`, 54 checks, runs it against a folder and
  then against Firebase on the emulator, and looks at the result through the
  relay in process: the listing shows the old galaxy closed with its reason and
  the new one open on turn one, `closed_problem` on the old galaxy's `/state`
  gives the reason, a submission is refused by the launcher's store and the
  relay's upload ticket is refused 409 with the reason, and the old turn is
  still served. Each of nine mutations fails a named check.

  **Not done by the tool, on purpose.** The referee worker ticks the one galaxy
  its `--store` names, so a fresh galaxy is not ticked until the scheduled task
  is pointed at it; the tool prints the command. Nothing has been closed or
  started on `cs-resurgence`.

  **Done when:** a galaxy is closed and a fresh one started, and a launcher
  pointed at the closed one says so rather than failing.

---

## L. Build versioning

- [x] **L1. The launcher carries a version.** It did not: there was no version
  constant in `release/launcher.py` and the only version anywhere was the `dist`
  folder name, which is how a build made with `-Version 0.1.1` shipped a launcher
  that called itself 0.1.0.

  `release/stamp_build.py` writes `build.json` and `build.ps1` packs it into the
  bundle beside `manifest.json`, where `build_info()` reads it back through the
  existing `bundled()` helper rather than a parallel mechanism. Unstamped builds
  are honest rather than silent: a checkout reports `0.1.0+dev` and a frozen
  build with no stamp reports `0.1.0+unstamped`, so neither can be mistaken for a
  release.

  **The stamp survives a real build.** `release/build/` is gitignored and
  PyInstaller regenerates the spec on every run, so an earlier attempt to stamp
  from the spec was inert; the call sits in `build.ps1` ahead of the PyInstaller
  invocation instead. `build.ps1 -Version 0.1.2` wrote
  `{"build": "0.1.2", "stamped_at": ..., "commit": "a84c528"}` and `build.json`
  appears in the frozen launcher's `PKG-00.toc`, so it is inside the exe rather
  than merely beside it.

  That run also found an unrelated packaging fault: the final archive step failed
  with the staged `game\CosmicSupremacy.exe` held by another process. The freeze
  and the staging completed; only the zip did not.

  **Confirmed by running the packaged build.** `dist\CosmicSupremacy-Resurgence-v0.1.2\CosmicSupremacyLauncher.exe`
  wrote, on its first line:

      Cosmic Supremacy: Resurgence v0.1.2
      build   stamped_at 2026-09-21T06:48:19Z commit a84c528

  `release/manifest.json` still reads `0.1.0`, so the launcher is reporting the
  version `build.ps1 -Version 0.1.2` decided rather than the manifest's. That is
  precisely the bug the stamp exists for, and the mismatch is what makes the
  result mean something rather than being a number that happened to agree.

- [x] **L2. A version gate on the galaxy.** The galaxy carries a minimum build; a
  launcher below it refuses to play and says where to get the update.

  `version_problem(build, state)` reads an optional `min_build` from the store's
  state dict and is wired into `start_multiplayer` after `store.exists()` and
  ahead of the `roster_problem` check, since a stale build is one reason the
  roster could look wrong. Ordering compares only the leading dotted number, so
  `0.1.1+dev` is not below `0.1.1`; if dev builds should be refused outright that
  is a one-line change and a decision, not a bug. A galaxy with no `min_build`, a
  null one, or a non-numeric one is not gated at all.

  Exercised below, at and above the minimum, with the key absent, null and
  non-numeric, and with no state, against both dict literals and a real
  `state.json` through `open_store`.

  **Nothing writes `min_build` yet.** That is the referee's side and arrives with
  the relay in H1, so the gate is live but every galaxy today is ungated.

  It earns its place because the failure it prevents is silent: a blob produced
  by a client that does not match the referee, whose symptom is a mystery rather
  than an error.

  The update link is
  `https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/releases/latest`,
  taken from the README's download button. It needs changing if the beta gets its
  own landing page.

- [ ] **L3. One-click update now, silent auto-update later.** The gate's message
  becomes a button that downloads and runs the installer. Replacing a running
  executable in place is a separate problem and is deferred; the metadata is the
  same either way, so the later version is a change to the launcher's UI and not
  to anything on the server.

  **Done when:** a player below the minimum build reaches a current one without
  being told where to click by a human.

---

## M. Diagnostics

- [ ]~ **M1. The launcher uploads its own log.** Every finding in the blob push
  plan came from watching a screen, and that stops being available the moment
  players are elsewhere. Without this, every beta report is a slow conversation.

  **The redaction is built; nothing uploads yet.** `redact_log_text`,
  `redacted_log` and `write_redacted_log` produce the sendable copy, capped at
  256 KiB by keeping the tail. The upload itself waits on the relay in H1.

  **The log records the save protocol's HTTP bodies**, which is base64 of the
  save blob in `body+` lines and diagnoses nothing. Each run of them now
  collapses to one line saying how many bytes were elided, while the chunk-0
  `body:` line is kept as far as `data=`, because ahead of that field it carries
  userid, gamename, turn and version.

  | `release/data/launcher.log` | bytes | lines |
  |---|---|---|
  | before | 23,310 | 181 |
  | after | 10,963 | 154 |

  Body dumps were 55% of the file, from 3 save requests. What survives: the
  session headers, mode launches and exit codes, the AI reasoning stream, the
  submission lines, the savegame summaries and the server's responses.

  **An earlier draft of this item overstated the volume.** It said tens of
  kilobytes per turn. `server/cs_server.py:558` already caps a `savegame` or
  `savegov` body at its first 4000 characters, so it is about 4.2 KB per request
  and two requests per multiplayer turn. Still worth removing, since it is over
  half the log and grows monotonically, but not for the reason first given.

  **Done when:** a turn's log uploads under the cap, and a failure the operator
  did not witness is diagnosed from the uploaded copy alone.

- [x] **M2. Say what the upload contains.** The Windows account name is scrubbed
  out of paths by generalising `<drive>:\Users\<name>\`, with Public and Default
  excepted, plus a literal match on the expanded home directory for a redirected
  profile. No account name is hard-coded. The redacted copy's contents are
  itemised in the launcher's own section header, which is what N4 gets written
  from.

  **Two things are deliberately not scrubbed** and are named rather than left to
  be found: the referee's machine name in a UNC store path, and bare occurrences
  of the account name outside a path.

- [ ]~ **M3. Antivirus and the save path, which is real remote code injection.**
  `trigger_save.py` uses `OpenProcess`, `VirtualAllocEx`, `WriteProcessMemory`
  and `CreateRemoteThread` to make the client save on demand. That is textbook
  injection and it is the only mechanism this path has, so a scanner objecting
  is describing what the file does rather than making a mistake.

  **Both halves measured, 28 September 2026, and both came back clean.**
  Defender twice quarantined the **compiled bytecode** in a checkout
  `__pycache__`, never the `.py` beside it; a frozen build carries no
  `__pycache__` and no `.pyc` at all, checked directly against v0.1.3. And the
  behavioural half, which an on-disk scan cannot answer: a packaged build took
  a real multiplayer turn, the capture fired, the turn sent, and there was no
  dialog, no quarantine and no interference. `trigger_save` is reached only
  from the multiplayer turn loop, so a single-player save would have tested the
  wrong path and come back clean for the wrong reason.

  **What is still open is other people's machines.** That is one Defender
  install with one configuration and no third-party AV in the way. A beta
  report of "it will not save" belongs near the top of the list of things to
  suspect.

  **A dev-side fix that needs no exclusion, and is not done:** stop writing
  bytecode for these tools, with `PYTHONDONTWRITEBYTECODE` or
  `sys.dont_write_bytecode`, so the file a scanner objects to never exists. An
  exclusion would also work and is worse, since it trains the habit and hides
  the next thing.

  **Done when:** the checkout stops producing the file that gets quarantined,
  and a beta tester on a machine nobody here configured takes a turn.

---

## N. Operating it

- [x] **N1. The worker runs unattended.** A scheduled task that starts on boot,
  the machine set not to sleep, and a startup rule that immediately closes any
  turn whose deadline has already passed. A 4-hour clock over a permanent galaxy
  means the machine's uptime is the galaxy's uptime.

  **A referee that cannot save no longer burns the turn.** `referee.py`'s header
  has always named `cs_server.py` on port 8888 as a requirement and nothing
  verified it, while `player_turn.serve` has checked the same thing since F2 lost
  a player's turn to exactly this. Measured with the port closed: a real
  resolution ran the whole merge, launched the client, advanced the galaxy, and
  only then failed with `SaveGame returned 0` from inside the client, which reads
  as a client fault rather than a missing server.

  `tick` and `resolve_turn` both call `save_path_ready` now. `resolve_turn`
  checks first because it writes each player's refusal notes before ticking, so
  a referee that cannot save changes nothing at all rather than leaving notes for
  a turn that did not close. Verified both ways: with the port closed it refuses
  before launching anything and leaves no client process; with the server up the
  same turn resolves normally.

  **Done, 27 September 2026, twice and by two different routes.**

  Unattended, with nobody watching and nothing arranged: at 20:07:34 the worker
  closed turn 12 with zero submissions, launched the client, captured and
  published turn 13. Nobody was at the machine.

  Then across a reboot. The worker was killed at about 22:35 by a console
  window being closed, the machine was rebooted, and at logon:

      23:16:03  worker: turn 13, due 23:07:48 (-495s)
      23:16:06  worker: turn 13 came due 8 minute(s) ago and nothing closed
                        it; closing it now rather than at the next deadline
      23:16:19  referee: published turn 14, canonical 12d6da0d0baedcc5

  Sixteen seconds from the worker starting to the turn being published, with
  nothing restored by hand. The hash moved from `633f3da78cadb602`, so the
  galaxy genuinely advanced rather than being rewritten.

  **The startup rule is what made it sixteen seconds.** A worker that waited
  for the next deadline would have taken four hours to show the same thing.
  `close_overdue` is the difference between a machine that resumes and a
  machine that is merely running again.

  **How it was killed is the finding.** The task ran the worker with
  `-WindowStyle Minimized`, which is a button in the taskbar, and closing a
  console window sends `CTRL_CLOSE_EVENT` to everything attached to it. One
  tidying-up click ended the galaxy, wrote nothing to the log, and left only
  `0xC000013A` in the task's result column. The task runs hidden now, and two
  checks in `test_referee_worker.py` keep it that way. A worker run by hand
  still shows its window, which is the case where one is wanted.

- [x] **N2. One bad submission cannot stall the galaxy.** The referee already
  does the right thing structurally, extracting orders and applying them to its
  own authoritative blob rather than trusting a submitted one as state. What is
  missing is that a submission which fails to parse should be dropped with a note
  rather than end the tick. A size cap on top.

  This is robustness, not security: cheating is accepted for this beta, a tick
  that dies is not.

  **Done when:** a truncated blob, an oversized one and a blob from a different
  galaxy are each dropped with a note, and the turn closes for everyone else.

- [ ]~ **N3. An operator view.** One page showing the galaxy, its turn, who has
  submitted, when the last tick ran and what it took, and any errors. Otherwise
  the only way to know the beta is healthy is to read a log on one machine.

  **Done when:** the state of the galaxy can be read without opening a log file
  or a terminal.

  **Seats and the worker's heartbeat, 29 September 2026.** On Firebase, which is
  where the live galaxy is, the view showed neither: `FirebaseTurnStore.state`
  returns only the store interface's fields, and `seats`, `worker_seen` and
  `worker_failures` are not among them. The view now makes one `get` of the
  galaxy document for those fields and nothing else, still behind the
  `STORE_READS` allowlist for every other read. Each galaxy gains a `referee`
  line, when the worker was last heard from and how many failures in a row, and
  a `seats` block, each civ with the first eight characters of its uid and when
  it last moved. Three new problems: a worker failing and retrying, a worker
  silent since before an overdue turn fell due, which is a stopped machine
  rather than a failing one, and a roster civ nobody can play because no uid
  holds it and first-use is off.

  What stands between this and the done-when is the terminal: `--serve` and
  `--html` are both started from one.
 What players are told, in the launcher and wherever they sign up.**
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

- [x] **N5. Record a measured tick duration.** Three real resolutions of the
  32-system, 3-civ demo galaxy at about 39,100 bytes, each closing a turn and
  publishing the next through a real client:

  | turn | orders | seconds |
  |---|---|---|
  | 9 to 10 | 3 taken, 0 dropped | 11.7 |
  | 10 to 11 | 0 | 11.0 |
  | 11 to 12 | 0 | 8.6 |

  So a tick is **about 10 seconds**, and six a day is under a minute of client
  time. Capacity is not a constraint at one galaxy, and this is the number the
  second galaxy and the cloud offload get planned against. A 108-system galaxy
  has not been timed.

- [ ] **N6. A launcher on the referee's own machine has to be told about the
  port.** The worker keeps a `cs_server` on 8888 so captures have somewhere to
  land. A launcher on the same machine finds the port held and refuses it,
  correctly: nothing in the protocol can ask a running server where it writes,
  and reusing one silently sent every turn to a folder the launcher never read.

  The operator's way out is `adopt_server` in `multiplayer.json`, naming that
  server's save directory. It works and is verified here. It is also a
  hand-edited JSON key that a player has no reason to know exists, and every
  beta player who referees and plays on one PC meets the refusal.

  **A second shape of the same problem has no answer at all.** When a worker is
  killed rather than stopped, its `cs_server` outlives it and goes on holding
  the port. The worker recovers from that now, ending a leftover it recorded
  the pid of once it has checked the pid was not reused. The launcher has no
  equivalent and says only "port 8888 is held", which is where the operator got
  stuck on 28 September after closing a console window. The launcher's version
  is harder, since it has no status file naming a pid it started, but it can
  read the holder's command line the way `is_our_cs_server` does and at least
  say that the port belongs to a referee from this install.

  **Done when:** a player who referees and plays on one machine never edits a
  config file to do it, and a launcher meeting an abandoned `cs_server` says
  what it is.

- [ ]~ **N7. The referee flashes a console window on every tick.**
  `server/referee.py:269` spawns `advance_turns.py` without `CREATE_NO_WINDOW`.
  The client-side tools were fixed; this one is the referee's own. Checked
  across the tree in September 2026: every other console-spawning site is
  either a dev tool run by hand or the game's own window, which has to be
  visible. On an unattended machine closing six turns a day it is six windows
  appearing and vanishing, which is cosmetic until somebody clicks one.

  **Done when:** a turn closes with nothing appearing on the desktop but the
  game client.

  **The flag is set, 29 September 2026**, the `_NO_WINDOW` way
  `referee_worker.py` already does it. `test_no_console_windows.py` runs
  `referee.tick` whole with the client stood in for and reads the flag off the
  one call it makes, and its sweep now covers `referee.py` and
  `referee_worker.py`, so a new bare spawn in either fails it. What is left is
  the done-when itself, which only a person watching the desktop through a
  real tick can confirm, and it takes effect once the worker is restarted.

---

## Out of scope, named so it stays out

- **Resolving turns anywhere but this PC.** The cloud offload is the next phase.
  H2 and H3 exist so that it is a deployment change.
- **Inheriting an abandoned empire.** A future game mode, and an interesting one.
  K4 wipes.
- **Formed or ranked galaxies with a lobby.** The sandbox is the only galaxy in
  this phase. J1's directory is the layer they would be added to.
- **Passwords, Google login, real accounts.** Out of this phase but next after
  it, and not a maybe: Google login is what makes H4's submission-ownership rule
  expressible and closes J4's roster leak, so both are deliberately left as they
  are rather than worked around. Everything above `player_name()` already takes a
  string, so the change is where the name comes from and nothing else.
- **Per-player projection and real fog.** Blocked in D1 and not blocked on this
  phase.
- **Governors and admirals.** The original's answer to an absent player. Their
  absence is why K3 and K4 exist at all.
