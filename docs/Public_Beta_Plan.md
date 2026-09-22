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

- [~] **H4. Anonymous auth and security rules.** Firebase Anonymous Auth gives
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

  **Done when:** a test client authenticated as one player is refused writing
  another player's submission, refused publishing a turn, and refused deleting
  anything, each by the rules rather than by the application declining to try.

- [~] **H7. The player's launcher cannot reach Firebase, and nothing in the
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

  **Not deployed.** That is the operator's call. The deploy cannot reach Hosting
  for three independent reasons: `functions/firebase.json` carries no `hosting`
  key and `deploy.py --check` refuses if one appears, `--only functions:relay`
  names one function of one codebase, and the CLI reads only the config in the
  directory it runs from, which is never `site/`.

  **Done when:** a launcher holding no Google Cloud credentials plays a turn in a
  galaxy hosted on Firebase.

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

- [~] **H5. Cost and quota arithmetic, written down.** One galaxy at 4-hour turns
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

  The launcher polls against the deadline it already knows rather than on a fixed
  short interval: often near the boundary, rarely in the middle of a turn.

  **Done when:** a measured day of a live galaxy is inside the allowances with
  margin, or the expected monthly bill is written down.

---

## J. The lobby

- [~] **J1. A galaxy directory above the store.** `server/galaxy_directory.py`
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

- [~] **J4. A seat is bound to the anonymous UID that claimed it.** A second
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

  **Done when:** a name in the roster is refused from a second install, allowed
  from the install that claimed it, and rebindable by the operator.

---

## K. The sandbox galaxy

- [~] **K1. Merge a new civ into a live galaxy without taking a planet someone
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

  **Still open:** the live half. An injected galaxy has not been loaded in the
  engine since this change, and no colony ship has been watched completing its
  colonisation after a join. The injection path itself is unchanged from what D4
  confirmed and only the planet chosen differs, which is a reason to expect it
  holds and not evidence that it does.

  **Done when:** a civ is injected into a galaxy with a colony ship in flight,
  the newcomer's homeworld is not that ship's destination, and the colony ship
  completes its colonisation on schedule in a client that actually ran.

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

  **Still open, none of it blocking:** the replaced `PLPR` carries the template
  rock's rates rather than the original's, which is the right choice because
  carrying the original across would preserve a homeworld's customisation boost,
  but `+5`, `+6`, `+12`, `+120` and `+121` still have no established meaning.
  `OWNR` deletion where another civ has met the victim is guarded rather than
  answered. And **nobody has looked at a wiped civ on screen**: how the dead
  shell reads in the diplomacy and overview lists is unchecked.

- [ ] **K5. Ending a galaxy is an operator action.** No season timer. The
  operator calls a galaxy over and starts a fresh one, so there has to be a way
  to close one that stops accepting submissions, keeps the archive readable, and
  tells every launcher why.

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

- [~] **M1. The launcher uploads its own log.** Every finding in the blob push
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

---

## N. Operating it

- [ ] **N1. The worker runs unattended.** A scheduled task that starts on boot,
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
