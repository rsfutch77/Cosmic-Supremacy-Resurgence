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

# Where the galaxies are. The prefix matches `firebase_store.PREFIX` for the
# reason that module gives: the same project hosts the public website.
PREFIX = os.environ.get('CS_RELAY_PREFIX', firebase_store.PREFIX)

_STORES = {}
_APP = None


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


def galaxy_doc(store) -> dict:
    """The whole galaxy document, seats included.

    `FirebaseTurnStore.state` deliberately returns only the five fields the
    store interface names, so the seat map is invisible above the store seam and
    stays that way. The relay is below that seam and reads the document itself,
    once per request, which is also the read that answers `/state`.
    """
    snap = store.doc.get()
    if not snap.exists:
        raise Refused(404, f'no galaxy {store.galaxy} in {store.project}')
    return snap.to_dict() or {}


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


def _submitted_civs(store, turn: int) -> list:
    """Who has handed something back, by name and without the blobs.

    `FirebaseTurnStore.submissions` downloads every submission, which is right
    for the referee at a turn boundary and wrong here: a launcher polls this,
    and it would be a download of every player's orders per poll, refused by the
    rule at H4 that stops one player reading another's. So the listing is done
    through the store's own client and stops at the names.
    """
    start = store.submission_prefix(turn)
    out = []
    for blob in store.gcs.list_blobs(store.bucket, prefix=start):
        name = blob.name[len(start):]
        if name.endswith('.b64') and '/' not in name:
            out.append(name[:-4])
    return sorted(out)


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
    something is refused. The alternative, staging the upload elsewhere and
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
        raise Refused(413, f'a submission may be {MAX_BYTES:,} bytes and this '
                           f'one is larger')
    try:
        decoded = sp_decode(data)
        turn_store.check_save(decoded, turn)
    except ValueError as exc:
        blob.delete()
        raise Refused(400, f'this is not a save of turn {turn}: {exc}')
    return _json({'turn': turn, 'civ': civ, 'bytes': len(decoded)})


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
        raise Refused(404, 'the galaxy is the first part of the path')
    galaxy, parts = parts[0], parts[1:]
    uid = verify(bearer(headers))
    store = store_for(galaxy)
    doc = galaxy_doc(store)

    if method in ('GET', 'HEAD'):
        if parts == ['state']:
            return _json({k: doc[k] for k in firebase_store.STATE_FIELDS
                          if k in doc})
        if len(parts) == 2 and parts[0] == 'turn':
            turn = _turn_number(parts[1])
            if not store.has_turn(turn):
                raise Refused(404, f'no turn {turn}')
            return _redirect(signed_url(store, store.turn_object(turn), 'GET'))
        if len(parts) == 2 and parts[0] == 'submissions':
            return _json(_submitted_civs(store, _turn_number(parts[1])))
        if len(parts) == 3 and parts[0] == 'submission':
            turn, civ = _turn_number(parts[1]), parts[2]
            seat_or_refuse(store, doc, uid, civ)
            name = store.submission_object(civ, turn)
            if not store.bucket.blob(name).exists():
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
            seat_or_refuse(store, doc, uid, civ)
            lines = store.note(civ, turn)
            if not lines:
                raise Refused(404, f'no note for {civ} on turn {turn}')
            return _text('\n'.join(lines).encode('utf-8'))

    if method == 'POST':
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
