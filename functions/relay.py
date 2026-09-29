"""
relay.py , the player's door to a Firebase galaxy
==================================================
    status, headers, body = relay.handle('GET', '/sandbox/state',
                                         {'Authorization': 'Bearer ...'}, b'')

`FirebaseTurnStore` is the referee's transport and can never be a player's: it
builds `firestore.Client` and `storage.Client`, both of which are Google Cloud
admin clients, and a player has no Google Cloud credential. Shipping a service
account key inside a distributed executable is not an alternative, because it is
project wide, it would let any holder do anything to any galaxy, and it cannot
be revoked for one player.

So this holds the admin credential, one copy, on a machine the players do not
have, and lets a launcher in through a door it already knows how to open. The
launcher keeps speaking `HttpTurnStore`; what answers is either `turn_server.py`
on a LAN or this. H7 chose that over a second Firebase transport in the launcher
for two reasons worth not relitigating: there is no official Firebase *client*
SDK for Python, only Admin, so a direct-to-Firebase player would mean a
hand-rolled REST store as a fourth implementation of an interface that already
costs work to keep three of in step; and ownership becomes an `if` statement
here rather than a rule Security Rules cannot express, which is the thing H4
recorded as unsolved.

The control plane, not the data plane
-------------------------------------
A blob never passes through this function. Every route that would have carried
one answers with a Cloud Storage URL instead: a redirect for a download, an
upload ticket for a write. The function decides *whether*, Storage moves the
bytes. That keeps the function's memory and its per-invocation time flat in the
size of a galaxy, and it keeps egress on the path Storage's free allowance is
measured against rather than on the function's.

The cost of it is that the function cannot see what a player uploaded, which is
why there is a commit call after the upload. See `_commit_submission`.

What it enforces, which is what rules could not
------------------------------------------------
  * the caller holds a verified Firebase ID token, or nothing is answered
  * a caller writes only its own submission, resolved through the seat map
  * only the referee publishes a turn, starts a galaxy, archives, or notes;
    there is no authenticated path here that does any of those at all
  * a submission is accepted only for the turn being played
  * a submission is size capped, twice: the cap is signed into the upload URL
    so Cloud Storage refuses an oversized body itself, and it is checked again
    at commit because the emulator cannot enforce a signed header
  * a submission that is not a save of this galaxy's current turn is deleted
  * a join request is filed under the uid in the token and not the one in the
    body, and a caller reads back only the request it lodged itself

The one caller with no seat
----------------------------
Everything above is about a player who holds a seat proving they are the player
whose seat it is. A join is the request that arrives from somebody who holds
nothing, and it has to be let in or nobody new ever plays. So `seat_or_refuse`
is not on that path, and what stands in its place is narrower:

  * **the uid is the token's.** The launcher puts a uid in the body because a
    folder galaxy has nowhere else to get one; here the body's is discarded. A
    request filed under another player's uid is that player's next seat taken
    from them at the boundary, by a caller who only had to read a uid once.
  * **the clock is the galaxy's.** `requested_at` decides who gets the last
    free planet when two joins compete for it, so it is stamped here rather
    than taken from a caller who could send a zero and always win.
  * **one waiting request per sign-in per galaxy**, because the document is the
    uid. Pressing Join again replaces, exactly as it does on a folder, and the
    door cannot be used to fill a collection.
  * **a caller already seated on the roster is refused**, and one whose seat
    was reclaimed is not: K4 takes a seat back and leaves the player able to
    come back, and a check that only asked whether a uid appears in `seats`
    would lock them out of the galaxy they were reclaimed from.
  * **nothing is seated here.** The request is a request. The worker grants it
    at the next boundary, on the authoritative blob, which is J3's whole shape
    and the reason a player cannot join a galaxy by writing to it.

Who a uid is
------------
The galaxy document carries `seats`, a map from Firebase uid to civ name. A uid
is safe as a Firestore map key, which is exactly what a civ name is not, so the
map goes this way round rather than the other; it is H4's option 1 with the
resolution in code rather than in a rule, which is what B bought.

**A uid with no seat is refused every write and every read of a submission or a
note, and is allowed the galaxy's public face: the clock, the roster, a
published turn, the list of who has submitted, and the archive.** That is the
Games tab's view of a galaxy it has not joined, so a launcher can show a galaxy
before its player is in it.

**Nothing writes `seats` yet.** Seat binding is J4's, and until it exists a
galaxy has no seats and no player can submit through this door, which is a real
gap rather than a detail: the operator binds them by hand or sets
`seat_claim: 'first-use'` on the galaxy document, which lets a uid take an
unheld civ from the roster on its first submission and holds it to that from
then on. A read never takes a seat, so browsing a galaxy is not joining it.
First use is a land grab among strangers and is off by default for that reason. Neither is
identity; Google login is, and H4 says so.
"""
import base64
import datetime
import json
import os
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))

# The store's own modules, copied in by `bundle.py` so that what deploys is what
# a deploy can carry: a Cloud Functions upload is one directory and cannot reach
# `server/` beside it. In a checkout the copies are refreshed from `server/` at
# import, so editing the store and running the relay against it cannot drift.
import bundle                                                   # noqa: E402

bundle.ensure()
if bundle.LIB not in sys.path:
    sys.path.insert(0, bundle.LIB)

import firebase_store                                           # noqa: E402
import turn_store                                               # noqa: E402

# How long a signed URL lives. Long enough for a slow upload of a save, short
# enough that one copied out of a log is not a standing grant.
URL_SECONDS = int(os.environ.get('CS_RELAY_URL_SECONDS', '600'))

# The ceiling on a submission. `turn_server.MAX_SUBMISSION_BYTES` is the same
# number, because a launcher must not be told two different limits by two
# services that are meant to be interchangeable. Real submissions measured
# against this are tens of kilobytes; the cap is there to stop a bucket being
# filled, not to be tight.
MAX_BYTES = int(os.environ.get('CS_RELAY_MAX_BYTES', str(2 * 1024 * 1024)))

# The ceiling on a join request, which is a name, a build string and two
# numbers. Three orders of magnitude above what one is and small enough that
# the door cannot be used to write a document of any size into Firestore.
MAX_JOIN_BYTES = int(os.environ.get('CS_RELAY_MAX_JOIN_BYTES', '4096'))

# The longest build string a request may carry. It is a claim the launcher
# makes about itself, read by L2's minimum-build gate, so it is kept rather
# than dropped and capped rather than trusted.
MAX_BUILD_CHARS = 64

# Where the galaxies are. The prefix matches `firebase_store.PREFIX` for the
# reason that module gives: the same project hosts the public website.
PREFIX = os.environ.get('CS_RELAY_PREFIX', firebase_store.PREFIX)

# Fields of the galaxy document that the store carries and this door does not
# serve. `joined` is the worker's record of who was seated at which boundary
# and it holds their uids, so serving it would hand every player the uid to
# civ mapping that `seats` is kept out of `/state` to protect. The store has to
# carry it, because `joins.commit` writes it and the acceptance tool reads it
# back; a player has no use for anybody's record but their own, and
# `GET /<galaxy>/join` is where they get that.
PRIVATE_FIELDS = (turn_store.JOINED_KEY,)

# How long a warm instance answers a galaxy's public face, `/state`,
# `/submissions` and `/turn`, from a galaxy document it has already read. Every
# launcher following a galaxy polls `/state`, so without this the project's
# Firestore reads grow with the number of launchers open; with it they grow
# with the number of galaxies and instances, and a launcher costs a request and
# no read. A copy is held up to the deadline and then for a twentieth of how
# overdue the turn is, floored at STATE_FLOOR and capped at STATE_CEILING, so
# it is shortest exactly where a turn is published. See `hold_for` and the
# plan's H12.
#
# Only those routes. Every route that checks a seat, a closed galaxy or the
# turn being played reads the document afresh, so what a player may do is
# never decided from a held copy. CS_RELAY_STATE_CEILING of 0 turns it off.
STATE_FLOOR = 1.0
STATE_SHARE = 20.0
STATE_CEILING = float(os.environ.get('CS_RELAY_STATE_CEILING', '60'))

# The same for the listing, which streams every galaxy document in the catalog
# and so costs one read per galaxy per request. A listing is held for the
# shortest window any galaxy in it would be held for, and never longer than
# this. CS_RELAY_LISTING_SECONDS of 0 turns it off.
LISTING_SECONDS = float(os.environ.get('CS_RELAY_LISTING_SECONDS', '60'))

_STORES = {}
_APP = None
# galaxy -> (when it was read, how long it may be served, the document)
_HELD = {}
# (when it was read, how long it may be served, the rows), or None
_LISTING = None


def _now() -> float:
    """The clock the held copies age by. A function so a test can move it."""
    return time.monotonic()


class Refused(Exception):
    """A refusal with a status code, so every one of them reads the same."""

    def __init__(self, code, why):
        super().__init__(why)
        self.code = code


# ── identity ─────────────────────────────────────────────────────────────────
def project() -> str:
    """The project this relay serves.

    Cloud Functions sets `GCLOUD_PROJECT`; the emulator sets it too. The
    explicit name wins so that a test can point a relay at a `demo-` project
    without the ambient environment deciding otherwise.
    """
    for key in ('CS_RELAY_PROJECT', 'GCLOUD_PROJECT', 'GOOGLE_CLOUD_PROJECT'):
        value = os.environ.get(key)
        if value:
            return value
    raise Refused(500, 'this relay has no project configured')


def _jwt_claims(token: str) -> dict:
    """The claims of a JWT, without checking who signed it."""
    parts = token.split('.')
    if len(parts) != 3:
        raise Refused(401, 'not an ID token')
    pad = '=' * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except Exception:                                           # noqa: BLE001
        raise Refused(401, 'not an ID token')


def verify(token: str) -> str:
    """The uid this token belongs to, or a refusal.

    Two verifiers, and the choice between them is made by the environment
    rather than by a flag, so there is no way to ask for the weaker one in
    production. Against the Auth emulator the tokens are unsigned by design and
    `firebase_admin` skips the signature itself, so the claims are read here and
    the same checks applied, which keeps the emulator runs free of the admin
    library and lets the relay's own tests run in the store's virtualenv.

    Anywhere else the signature is the whole point and `firebase_admin` does it,
    against Google's rotating public keys. If it is not installed the request is
    refused rather than admitted: a verifier that is missing must not become a
    verifier that passes everything, which is how this kind of fallback usually
    fails.
    """
    if not token:
        raise Refused(401, 'this galaxy needs a signed-in caller')
    name = project()
    if os.environ.get('FIREBASE_AUTH_EMULATOR_HOST'):
        claims = _jwt_claims(token)
        if claims.get('aud') != name:
            raise Refused(401, 'this token was minted for another project')
        if claims.get('iss') != f'https://securetoken.google.com/{name}':
            raise Refused(401, 'this token was not minted by Firebase Auth')
        if float(claims.get('exp') or 0) < time.time():
            raise Refused(401, 'this token has expired')
        uid = claims.get('user_id') or claims.get('sub')
        if not uid:
            raise Refused(401, 'this token names no user')
        return uid
    try:
        import firebase_admin
        from firebase_admin import auth
    except ImportError:
        raise Refused(500, 'no token verifier is installed')
    global _APP
    if _APP is None:
        try:
            _APP = firebase_admin.get_app()
        except ValueError:
            _APP = firebase_admin.initialize_app(options={'projectId': name})
    try:
        return auth.verify_id_token(token, app=_APP)['uid']
    except Exception as exc:                                    # noqa: BLE001
        raise Refused(401, f'this token was refused: {type(exc).__name__}')


def bearer(headers: dict) -> str:
    """The token out of an Authorization header, whatever its case."""
    for key, value in (headers or {}).items():
        if key.lower() == 'authorization':
            value = (value or '').strip()
            if value.lower().startswith('bearer '):
                return value[7:].strip()
            raise Refused(401, 'only a bearer token is accepted here')
    return ''


# ── the store, and the seat map beside it ────────────────────────────────────
def store_for(galaxy: str):
    """This galaxy's admin store, kept for the life of the instance.

    Building one is cheap and building its clients is not: the first Firestore
    call in a process pays for grpc. A warm instance serving a second request
    for the same galaxy should not pay it again.
    """
    if galaxy not in _STORES:
        _STORES[galaxy] = firebase_store.FirebaseTurnStore(
            project(), galaxy,
            bucket=os.environ.get('CS_RELAY_BUCKET')
            or os.environ.get('FIREBASE_STORAGE_BUCKET'),
            prefix=PREFIX)
    return _STORES[galaxy]


# A store is one galaxy, and a listing is about the collection they sit in, so
# `catalog` borrows a store for its Firestore client and never asks it about a
# galaxy. The name is a placeholder the constructor accepts; a galaxy that
# happened to carry it would share the client and nothing else.
CATALOG = '_catalog'


def catalog():
    """The collection every galaxy document sits in.

    Borrowed from a store rather than built from a second client, because the
    first Firestore call in a process pays for grpc and a cold instance
    answering a listing should pay it once.
    """
    store = store_for(CATALOG)
    return store.fs.collection(store.prefix)


def catalog_rows() -> list:
    """Every galaxy, carrying what `/state` serves about it plus which it is.

    The same allowlist and the same private fields as `/state`, so a galaxy
    says the same about itself in a listing as it does when asked directly.
    `name` is added to that because it is the directory's field rather than the
    store's, and a row without it could only be labelled by its id.
    """
    rows = []
    for snap in catalog().stream():
        doc = snap.to_dict() or {}
        row = {k: doc[k] for k in firebase_store.STATE_FIELDS
               if k in doc and k not in PRIVATE_FIELDS}
        row['id'] = snap.id
        row['name'] = doc.get('name') or snap.id
        rows.append(row)
    return sorted(rows, key=lambda r: r['id'])


def hold_for(doc: dict, ceiling: float = None) -> float:
    """How long a copy of this galaxy's public face may be served.

    Before the deadline a copy is held up to the deadline and no further,
    because the referee publishes at or after it. Past the deadline the turn
    can change at any moment, so the copy is held for a twentieth of how
    overdue it is, which is the floor while the referee's tick is due and
    backs off if the referee is down. Measured on the wall clock, because that
    is the clock a deadline is written in. A galaxy with no deadline is held
    for the ceiling, since nothing is due to change in it.
    """
    ceiling = STATE_CEILING if ceiling is None else ceiling
    if ceiling <= 0:
        return 0.0
    deadline = (doc or {}).get('deadline')
    if deadline is None:
        return ceiling
    left = float(deadline) - time.time()
    return min(ceiling, max(STATE_FLOOR,
                            left if left > 0 else -left / STATE_SHARE))


def listing() -> list:
    """`catalog_rows`, held on this instance while no galaxy in it is due.

    The rows carry the turn each galaxy is on, and a launcher asks
    `/submissions` about that turn next. A row held across a publish sends it
    to a past turn, which is a bucket listing, so the listing takes the
    shortest window of the galaxies in it: held to the soonest deadline, and
    for the floor while any galaxy in it is overdue.
    """
    global _LISTING
    held = _LISTING
    if held is not None and _now() - held[0] < held[1]:
        return held[2]
    rows = catalog_rows()
    window = min([LISTING_SECONDS] +
                 [hold_for(r, LISTING_SECONDS) for r in rows])
    _LISTING = (_now(), window, rows) if window > 0 else None
    return rows


def galaxy_doc(store) -> dict:
    """The whole galaxy document, seats included, read afresh.

    `FirebaseTurnStore.state` deliberately returns only the five fields the
    store interface names, so the seat map is invisible above the store seam and
    stays that way. The relay is below that seam and reads the document itself,
    once per request.

    A fresh read is also the newest copy this instance has, so it replaces the
    one `public_doc` holds.
    """
    snap = store.doc.get()
    if not snap.exists:
        _HELD.pop(store.galaxy, None)
        raise Refused(404, f'no galaxy {store.galaxy} in {store.project}')
    doc = snap.to_dict() or {}
    window = hold_for(doc)
    if window > 0:
        _HELD[store.galaxy] = (_now(), window, doc)
    return doc


def public_doc(store) -> dict:
    """The galaxy document for a route that serves only its public face.

    A copy read inside its window is served again rather than read again. Only
    `/state`, `/submissions` and `/turn` take this, and none of them decides
    what a caller may do.
    """
    held = _HELD.get(store.galaxy)
    if held is not None and _now() - held[0] < held[1]:
        return held[2]
    return galaxy_doc(store)


def forget_doc(store) -> None:
    """Drop the held copy after this instance has written the document."""
    _HELD.pop(store.galaxy, None)


def seat_of(doc: dict, uid: str):
    """The civ this uid plays, or None when it holds no seat."""
    return (doc.get('seats') or {}).get(uid)


def claim_seat(store, doc: dict, uid: str, civ: str) -> str:
    """Bind an unheld civ to this uid, when the operator allowed it.

    Off unless the galaxy document says `seat_claim: 'first-use'`, because
    among strangers first use is a land grab: the first uid to ask takes
    whichever civ it names, and the player that name belongs to arrives to find
    their seat held. It exists because the alternative while J4 is unbuilt is
    binding every seat by hand before a rehearsal.

    In a transaction, so two launchers claiming the same civ in the same second
    produce one holder and one refusal rather than two holders.
    """
    if (doc.get('seat_claim') or '') != 'first-use':
        raise Refused(403, f'no seat in {store.galaxy} is bound to this '
                           f'sign-in')
    if civ not in list(doc.get('civs') or []):
        raise Refused(403, f'{civ} is not a civ in {store.galaxy}')
    from google.cloud import firestore

    @firestore.transactional
    def _take(tx, ref):
        snap = ref.get(transaction=tx)
        seats = (snap.to_dict() or {}).get('seats') or {}
        held = seats.get(uid)
        if held:
            return held
        if civ in seats.values():
            raise Refused(403, f'{civ} is already held by another sign-in')
        tx.update(ref, {f'seats.{uid}': civ})
        return civ

    return _take(store.fs.transaction(), store.doc)


def seat_or_refuse(store, doc: dict, uid: str, civ: str,
                   claim: bool = False) -> str:
    """Check that this caller is that civ, and say so when it is not.

    The one rule H4 could not write. A Storage rule can test `request.auth.uid`
    and nothing else about who is asking, and a submission object is named for a
    username the player typed, so no rule can relate the two. Here they are two
    strings and a comparison.

    Only the two steps of a submission may `claim`: the upload ticket, which
    authorises a write, and the commit that closes it. A read never does, even
    though the ticket is fetched with a GET. A launcher looking at a galaxy has
    not joined it, and under `first-use` a seat taken by a read would go to
    whoever browsed first rather than to whoever played.
    """
    held = seat_of(doc, uid)
    if held is None and claim:
        held = claim_seat(store, doc, uid, civ)
    if held is None:
        raise Refused(403, f'no seat in {store.galaxy} is bound to this '
                           f'sign-in')
    if held != civ:
        raise Refused(403, f'this sign-in plays {held} in {store.galaxy}, '
                           f'not {civ}')
    return held


# ── signed URLs ──────────────────────────────────────────────────────────────
def signing_identity(store):
    """Whatever this process can sign a Cloud Storage URL with.

    **A Cloud Functions runtime service account has no private key**, so
    `generate_signed_url` cannot sign locally the way it does from a key file:
    it raises, naming a missing private key, and nothing about the message says
    what to do. The supported answer is to sign through the IAM Credentials API,
    which `generate_signed_url` will do when it is given a service account email
    and an access token instead of a signer. That needs
    `iam.serviceAccounts.signBlob` on the runtime account, which is
    `roles/iam.serviceAccountTokenCreator` granted to the runtime account on
    itself, and `iamcredentials.googleapis.com` enabled. Both are in the deploy
    notes in `firebase.json`'s directory.

    A service account key, which is what an operator running this locally has,
    signs by itself and takes neither. So the credential is asked which it is
    rather than the environment being guessed at.

    `CS_RELAY_SIGNER` names the identity to sign as, for the case where the two
    differ: a user credential from `gcloud auth application-default login` can
    sign through the IAM API as a service account it is allowed to impersonate,
    and cannot name one on its own.
    """
    import google.auth
    import google.auth.credentials
    import google.auth.transport.requests
    creds = store.credentials()
    if isinstance(creds, google.auth.credentials.Signing) \
            and not os.environ.get('CS_RELAY_SIGNER'):
        return {'credentials': creds}
    if not creds.valid:
        creds.refresh(google.auth.transport.requests.Request())
    email = (os.environ.get('CS_RELAY_SIGNER')
             or getattr(creds, 'service_account_email', None))
    if not email:
        raise Refused(500, 'this relay has no identity to sign URLs with; '
                           'set CS_RELAY_SIGNER')
    return {'credentials': creds, 'service_account_email': email,
            'access_token': creds.token}


def signed_url(store, name: str, method: str, content_type: str = None,
               headers: dict = None) -> str:
    """A short-lived URL for one object and one method.

    Against the Firebase emulator there is nothing to sign with and nothing
    checking signatures, so the emulator's own unauthenticated JSON API endpoint
    is handed out instead. That is a real difference and not a shim: the
    emulator is an open bucket by design, and the code path that produces a
    signed URL is exercised only against a real project. What the emulator does
    prove is everything around it, which is where the rules live.
    """
    emulator = os.environ.get('STORAGE_EMULATOR_HOST')
    if emulator:
        bucket = store.bucket.name
        quoted = urllib.parse.quote(name, safe='')
        if method == 'GET':
            return (f'{emulator.rstrip("/")}/storage/v1/b/{bucket}/o/'
                    f'{quoted}?alt=media')
        return (f'{emulator.rstrip("/")}/upload/storage/v1/b/{bucket}/o'
                f'?uploadType=media&name={quoted}')
    return store.bucket.blob(name).generate_signed_url(
        version='v4',
        expiration=datetime.timedelta(seconds=URL_SECONDS),
        method=method,
        content_type=content_type,
        headers=headers,
        **signing_identity(store))


# ── replies ──────────────────────────────────────────────────────────────────
def _json(obj, code=200):
    return code, {'Content-Type': 'application/json'}, \
        json.dumps(obj).encode('utf-8')


def _text(body: bytes, code=200, ctype='application/octet-stream'):
    return code, {'Content-Type': ctype}, body


def _redirect(url: str):
    """A 302 to Storage, which is how a blob leaves without passing through.

    `HttpTurnStore` follows it and drops the Authorization header on the way,
    because Cloud Storage reads a bearer token it was not given in the signature
    as a credential and refuses the request over it.
    """
    return 302, {'Location': url, 'Cache-Control': 'no-store'}, b''


# ── routes ───────────────────────────────────────────────────────────────────
def _turn_number(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise Refused(400, 'a turn number has to be an integer')


def _current_turn(doc: dict) -> int:
    turn = doc.get('turn')
    if turn is None:
        raise Refused(409, 'this galaxy has no first turn yet')
    return int(turn)


def refuse_if_closed(store, doc):
    """A closed galaxy takes no more submissions, enforced here.

    Every store's `submit` already refuses one, but the relay never calls
    `submit`: it mints a signed Cloud Storage ticket and the launcher uploads
    straight to Storage. So the store's own guard sits on the far side of the
    door a player actually uses, and a launcher that skipped its client-side
    check could upload into a galaxy the operator had ended. Both halves of the
    ticket check, because an upload authorised before a close would otherwise
    still commit after one.
    """
    if doc.get(turn_store.STATUS_KEY) == turn_store.CLOSED:
        why = doc.get(turn_store.CLOSED_REASON_KEY)
        raise Refused(409, f'{store.galaxy} is closed'
                           + (f': {why}' if why else ''))


def _upload_ticket(store, doc, uid, turn, civ):
    """Where to put a submission, and what the far end will accept.

    The same shape `turn_server.upload_ticket` answers with, so that one
    launcher speaks to both and neither has to know which it reached.

    `x-goog-content-length-range` is signed into the URL, so the cap is Cloud
    Storage's to enforce and not this function's: an oversized body is refused
    at the edge without the bytes ever being offered here. `commit` is the
    second half, and exists because a function that never sees the bytes cannot
    say whether they were a save.

    `url` is absolute because it is Storage's, and `commit` is relative because
    it is this relay's: the launcher's base already carries the galaxy, and a
    ticket that repeated it would send the next request to
    `/<galaxy>/<galaxy>/commit`.
    """
    refuse_if_closed(store, doc)
    seat_or_refuse(store, doc, uid, civ, claim=True)
    current = _current_turn(doc)
    if turn != current:
        raise Refused(409, f'{store.galaxy} is playing turn {current}, '
                           f'not turn {turn}')
    name = store.submission_object(civ, turn)
    cap = f'0,{MAX_BYTES}'
    quoted = urllib.parse.quote(civ)
    return _json({
        'url': signed_url(store, name, 'PUT', content_type='text/plain',
                          headers={'x-goog-content-length-range': cap}),
        'method': 'POST' if os.environ.get('STORAGE_EMULATOR_HOST') else 'PUT',
        'encoding': 'b64',
        'headers': {'Content-Type': 'text/plain',
                    'x-goog-content-length-range': cap},
        'commit': f'/commit/submission/{turn}/{quoted}',
        'max_bytes': MAX_BYTES,
        'expires': int(time.time() + URL_SECONDS),
    })


def _commit_submission(store, doc, uid, turn, civ):
    """Read back what was uploaded, and remove it if it was not a save.

    The price of never carrying a blob. The function authorised an upload to one
    object and Cloud Storage took the bytes, so the only place left to ask
    whether they were a save of this turn is afterwards, against the object.

    One Class B operation on the happy path, and a Class A delete only when
    something is refused. Either way one Firestore write follows, recording or
    forgetting the civ on the galaxy document, which is what lets the
    `/submissions` poll answer without listing the bucket. The alternative, staging the upload elsewhere and
    copying it into place once it checked out, costs a copy and a delete on
    *every* submission, and H5 measured Class A operations as the binding free
    quota: 1,456 a month of 5,000 at six players saving once a turn. Tripling
    the one that binds to protect against a case the referee already survives
    badly is the wrong trade.

    The window this leaves is between the upload and this call, when an object
    that is not a save sits where the referee would read it. It is milliseconds
    of a turn that is hours long, the caller that opened it is the only one who
    can close it, and before this existed the window was the whole turn.
    """
    refuse_if_closed(store, doc)
    seat_or_refuse(store, doc, uid, civ, claim=True)
    current = _current_turn(doc)
    if turn != current:
        raise Refused(409, f'{store.galaxy} is playing turn {current}, '
                           f'not turn {turn}')
    name = store.submission_object(civ, turn)
    blob = store.bucket.blob(name)
    # One range read rather than a size check and then a read: an object above
    # the cap must not be downloaded to discover that it is above the cap.
    from google.api_core import exceptions
    try:
        data = blob.download_as_bytes(start=0, end=MAX_BYTES)
    except exceptions.NotFound:
        raise Refused(404, f'{civ} has uploaded nothing for turn {turn}')
    if len(data) > MAX_BYTES:
        blob.delete()
        store.forget_submitted(civ, turn)
        forget_doc(store)
        raise Refused(413, f'a submission may be {MAX_BYTES:,} bytes and this '
                           f'one is larger')
    try:
        decoded = sp_decode(data)
        turn_store.check_save(decoded, turn)
    except ValueError as exc:
        blob.delete()
        store.forget_submitted(civ, turn)
        forget_doc(store)
        raise Refused(400, f'this is not a save of turn {turn}: {exc}')
    store.record_submitted(civ, turn)
    forget_doc(store)
    return _json({'turn': turn, 'civ': civ, 'bytes': len(decoded)})


def _lodge_join(store, doc, uid, body):
    """Ask this galaxy for a seat. The one write an unseated caller may make.

    What comes back is where it landed and the turn it was lodged during, so a
    launcher can tell the player which turn their empire appears at without
    reading the clock again.

    A galaxy with no first turn is refused rather than queued. `joins.apply`
    runs on the blob a tick produced and a galaxy that has never published one
    has nothing for a newcomer to be merged into, so a request lodged against
    it would wait for a boundary that cannot come.
    """
    refuse_if_closed(store, doc)
    if len(body or b'') > MAX_JOIN_BYTES:
        raise Refused(413, f'a join request may be {MAX_JOIN_BYTES:,} bytes')
    try:
        req = json.loads(body or b'{}')
    except ValueError:
        raise Refused(400, 'a join request is a JSON object')
    if not isinstance(req, dict):
        raise Refused(400, 'a join request is a JSON object')
    name = req.get('name')
    name = name.strip() if isinstance(name, str) else ''
    if not name:
        raise Refused(400, 'a join request names the player asking')

    turn = _current_turn(doc)
    roster = [str(c) for c in (doc.get('civs') or [])]
    held = seat_of(doc, uid)
    if held and held in roster:
        # Already playing. Refused here rather than at the boundary, where the
        # answer would be a second empire refused for a name clash and would
        # read to the player as the galaxy having lost their seat. A seat that
        # was reclaimed is not in the roster and falls through, because a
        # reclaimed player coming back is a join `joins.py` knows how to grant.
        raise Refused(409, f'this sign-in already plays {held} in '
                           f'{store.galaxy}')
    if any(c == name or c.lower() == name.lower() for c in roster):
        # The launcher refuses this at the button and says more about it than
        # a status code can. It is refused here as well because the launcher is
        # not a boundary: what stops a second player taking a seat has to be on
        # this side of the door.
        raise Refused(409, f'{store.galaxy} already has a seat called {name}')

    build = req.get('build')
    clean = {'name': name,
             'uid': uid,
             'build': str(build)[:MAX_BUILD_CHARS] if build else None,
             'requested_at': time.time(),
             'requested_turn': turn}
    try:
        where = store.request_join(clean)
    except ValueError as exc:
        # The store refuses a request it could not file, and every reason it
        # has is about the caller. Unreachable as the record is built above,
        # where the name is checked and the key is a uid; turned into a
        # refusal rather than left to become a 500, because a caller told
        # "internal error" has nothing to do about it.
        raise Refused(400, str(exc))
    return _json({'where': where, 'key': uid, 'requested_turn': turn})


def _own_key(uid: str, asked: str) -> str:
    """The request key in a path, once it has been shown to be this caller's.

    Through this door a request is filed under the uid in the token, so the
    only key a caller may name is its own. The comparison is the same one
    `seat_or_refuse` makes about a civ, one step earlier in a player's life:
    there they have a seat and here they are asking for one.

    Unquoted first, because `HttpTurnStore` quotes a key whole and a uid that
    came back with an escape in it would never match.
    """
    key = urllib.parse.unquote(asked or '')
    if key != uid:
        raise Refused(403, 'a join request is read back by the sign-in that '
                           'lodged it')
    return key


def _own_join(store, doc, uid):
    """What became of the request this caller lodged, or that it is waiting.

    Keyed by the token's uid and by nothing else, so there is no route here
    that lists what is waiting on a galaxy. `turn_server.py` has one, because
    on a LAN the worker is whoever holds the port; through this door the same
    list would tell a stranger who else is trying to join, which is the
    enumeration F4 refuses to let a launcher make.

    This is what closes J3's other half: a refused player is on no roster, so
    `player_turn.follow` never runs for them and the note the worker left is on
    a path they cannot read. The answer is on a path they can.

    The waiting request is looked for first. Answering a request consumes it,
    so one that is waiting is always newer than any answer on file, and a
    player refused once who asks again is waiting on the new request rather
    than refused by the old one.

    At most two named document reads and no listing, for the reason H6 gives
    about a submission: this is a path a launcher polls while it waits for a
    boundary, and reading the whole waiting collection to find one row in it
    would cost a read per player waiting, per poll.
    """
    waiting = store.join_request(uid)
    if waiting is not None:
        return _json({'state': 'waiting', 'request': waiting})
    answer = store.join_answer(uid)
    if answer is not None:
        return _json({'state': 'answered', 'answer': answer})
    return _json({'state': 'none'})


def sp_decode(data: bytes) -> bytes:
    """The wire form a submission is stored in, decoded, or a ValueError.

    `save_parser.decode_save` raises whatever base64 and zlib raise, and this
    turns all of them into the one exception the caller refuses on, so a
    corrupt upload reads as a refusal rather than a 500.
    """
    import save_parser as sp
    try:
        return sp.decode_save(data.strip())
    except ValueError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        raise ValueError(f'{type(exc).__name__}: {exc}')


REFEREE_ONLY = ('only the referee publishes a turn, and it does not come '
                'through here')


def _route(method: str, path: str, headers: dict, body: bytes):
    # A query is dropped rather than parsed. Every route that carries one is a
    # referee's route, and every referee's route is refused here, so there is
    # nothing in a query this relay would act on. Cutting it here rather than
    # trusting the adapter means a caller that passes a whole URL path gets the
    # same answer as one that passes a path.
    path = (path or '').split('?', 1)[0]
    parts = [p for p in path.split('/') if p]
    if not parts:
        # The one route that is about no galaxy. A player holds no Google
        # credential and so cannot read the collection the referee reads, and
        # without this there is no way for them to learn a galaxy exists.
        # Signed in like every other route: anonymous sign-up is open, so the
        # gate costs a stranger nothing and keeps one rule rather than two.
        if method in ('GET', 'HEAD'):
            verify(bearer(headers))
            return _json({'galaxies': listing()})
        raise Refused(404, 'the galaxy is the first part of the path')
    galaxy, parts = parts[0], parts[1:]
    uid = verify(bearer(headers))
    store = store_for(galaxy)

    if method in ('GET', 'HEAD'):
        if parts == ['state']:
            doc = public_doc(store)
            return _json({k: doc[k] for k in firebase_store.STATE_FIELDS
                          if k in doc and k not in PRIVATE_FIELDS})
        if len(parts) == 2 and parts[0] == 'turn':
            turn = _turn_number(parts[1])
            # `start` and `publish` write the turn's object before the
            # document names it, so the turn the document is on exists and
            # asking Storage would be a Class B operation to learn nothing.
            doc = public_doc(store)
            if turn != doc.get('turn') and not store.has_turn(turn):
                raise Refused(404, f'no turn {turn}')
            return _redirect(signed_url(store, store.turn_object(turn), 'GET'))
        if len(parts) == 2 and parts[0] == 'submissions':
            # A launcher polls this. The store answers the current turn from
            # the document already read, so the poll costs no Storage
            # operation; see `FirebaseTurnStore.submitted_civs`.
            return _json(store.submitted_civs(_turn_number(parts[1]),
                                              doc=public_doc(store)))

    doc = galaxy_doc(store)

    if method in ('GET', 'HEAD'):
        if len(parts) == 3 and parts[0] == 'submission':
            turn, civ = _turn_number(parts[1]), parts[2]
            # A caller holding no seat at all has no submission, because the
            # only way an object gets written is the ticket and commit pair and
            # both of those need a seat. So 404 is the true answer and 403 was
            # a wrong one, in the way that mattered most: under `first-use` a
            # seat binds when a player submits, so before their first
            # submission every player is seatless, and the turn loop asks this
            # route whether they have already played before it serves them.
            # Refusing it made a player's first turn on a galaxy impossible,
            # which is the whole of the beta path.
            if seat_of(doc, uid) is None:
                raise Refused(404, f'no submission of yours for {turn}')
            seat_or_refuse(store, doc, uid, civ)
            name = store.submission_object(civ, turn)
            # A civ the document records for the turn it is on has an object,
            # because it is recorded only once the object is in place. Any
            # other answer is asked of the bucket, so a record that is missing
            # can never make a submission look absent.
            recorded = firebase_store.submitted_of(doc, turn) or []
            if civ not in recorded and not store.bucket.blob(name).exists():
                raise Refused(404, f'{civ} has not submitted for {turn}')
            return _redirect(signed_url(store, name, 'GET'))
        if len(parts) == 4 and parts[:2] == ['upload', 'submission']:
            return _upload_ticket(store, doc, uid, _turn_number(parts[2]),
                                  parts[3])
        if len(parts) == 2 and parts[0] == 'archive':
            record = store.archive_record(_turn_number(parts[1]))
            if record is None:
                raise Refused(404, f'no archive for turn {parts[1]}')
            return _json(record)
        if len(parts) == 3 and parts[0] == 'note':
            turn, civ = _turn_number(parts[1]), parts[2]
            # Seatless is no note, for the reason the submission route gives.
            # The turn loop survives a refusal here because it catches one, so
            # this was not what broke a first turn; it is the same untruth and
            # it is fixed the same way.
            if seat_of(doc, uid) is None:
                raise Refused(404, f'no note for you on turn {turn}')
            seat_or_refuse(store, doc, uid, civ)
            lines = store.note(civ, turn)
            if not lines:
                raise Refused(404, f'no note for {civ} on turn {turn}')
            return _text('\n'.join(lines).encode('utf-8'))
        if parts == ['join']:
            return _own_join(store, doc, uid)
        if len(parts) == 2 and parts[0] == 'join':
            req = store.join_request(_own_key(uid, parts[1]))
            if req is None:
                raise Refused(404, 'no join request of yours is waiting here')
            return _json(req)
        if len(parts) == 3 and parts[0] == 'join' and parts[2] == 'answer':
            answer = store.join_answer(_own_key(uid, parts[1]))
            if answer is None:
                raise Refused(404, 'nothing has been decided about a join '
                                   'request of yours here')
            return _json(answer)
        if parts and parts[0] == 'joins':
            # What is waiting on a galaxy is the worker's question.
            # `turn_server.py` answers it, because on a LAN the worker is
            # whoever holds the port. Answering it here would tell any stranger
            # who else is trying to join, which is the enumeration F4 refuses
            # to let a launcher make.
            raise Refused(403, 'what is waiting on this galaxy is the '
                               'worker\'s to read, and this door answers a '
                               'caller only about its own request')

    if method == 'POST':
        if parts == ['join']:
            return _lodge_join(store, doc, uid, body)
        if parts and parts[0] in ('join', 'joins'):
            raise Refused(403, 'a join request is answered by the worker at a '
                               'turn boundary, and it does not come through '
                               'here')
        if len(parts) == 4 and parts[:2] == ['commit', 'submission']:
            return _commit_submission(store, doc, uid,
                                      _turn_number(parts[2]), parts[3])
        if len(parts) == 3 and parts[0] == 'submission':
            raise Refused(405, 'a submission is uploaded to the URL that '
                               f'/{galaxy}/upload/submission/{parts[1]}/'
                               f'{parts[2]} hands out')
        if parts and parts[0] in ('start', 'turn', 'archive', 'note'):
            raise Refused(403, REFEREE_ONLY)

    raise Refused(404, f'no route for {method} {path}')


def handle(method: str, path: str, headers: dict = None, body: bytes = b''):
    """(status, headers, body) for one request.

    Framework free on purpose. `main.py` adapts a Cloud Functions request to
    this and the relay's tests call it directly, so what the tests exercise is
    the whole function apart from the adapter, in a virtualenv that does not
    have the functions runtime in it.
    """
    try:
        return _route(method, path, headers or {}, body or b'')
    except Refused as exc:
        return _json({'error': str(exc)}, exc.code)
    except Exception as exc:                                    # noqa: BLE001
        return _json({'error': f'{type(exc).__name__}: {exc}'}, 500)
