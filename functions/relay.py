"""
relay.py: the player's door to a Firebase galaxy

    status, headers, body = relay.handle('GET', '/sandbox/state',
                                         {'Authorization': 'Bearer ...'}, b'')

Holds the admin credential so a launcher, which has no Google Cloud credential,
can reach a Firebase galaxy over `HttpTurnStore`, the protocol `turn_server.py`
answers on a LAN.

A turn blob never passes through: `/turn/<n>` redirects to a signed Cloud
Storage URL. A submission does: the upload ticket names this relay's own route,
the body is checked and stored as a Firestore document in one call, and it is
served back from here with a 304 to a launcher that already holds it.

What it enforces:
  * the caller holds a verified Firebase ID token, or nothing is answered
  * a caller writes only its own submission, resolved through the seat map
  * only the referee publishes a turn, starts a galaxy, archives, or notes;
    no authenticated path here does any of those
  * a submission is accepted only for the turn being played
  * a submission is size capped before it is decoded, again before it is
    stored, and while it inflates
  * a submission that is not a save of this galaxy's current turn is refused
    before it is stored, so it never replaces the previous one
  * a join request is filed under the token's uid, not the body's, and a
    caller reads back only its own request
  * a launcher's log is capped before and while it is inflated, counted per
    sign-in and per day, and never served back to anyone

A join comes from a caller with no seat, so `seat_or_refuse` is not on that
path. Instead:
  * the uid is the token's; the body's is discarded
  * `requested_at` is stamped here, since it decides who gets the last free
    planet when two joins compete
  * one waiting request per sign-in per galaxy, keyed by uid
  * a caller seated on the roster is refused; one whose seat was reclaimed
    may ask again
  * nothing is seated here; the worker grants the request at the next boundary

The galaxy document's `seats` maps Firebase uid to civ name. A uid with no seat
is refused every write and every read of a submission or a note, and may read
the galaxy's public face: the clock, the roster, a published turn, the list of
who has submitted, and the archive.

Three things write `seats`: a granted join (`joins.commit`), the operator with
`server/dev_tools/seat_tool.py`, and, only on a galaxy with
`seat_claim: 'first-use'`, a uid's first submission taking an unheld civ. A
reclaim leaves the uid's entry in place, so a write from it is refused as
reclaimed. A read never takes a seat.
"""
import base64
import datetime
import json
import os
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))

# The store's modules, copied in by `bundle.py`. In a checkout they are
# refreshed from `server/` at import.
import bundle                                                   # noqa: E402

bundle.ensure()
if bundle.LIB not in sys.path:
    sys.path.insert(0, bundle.LIB)

import firebase_store                                           # noqa: E402
import turn_store                                               # noqa: E402

# Lifetime of a signed URL, in seconds.
URL_SECONDS = int(os.environ.get('CS_RELAY_URL_SECONDS', '600'))

# Ceiling on a submission body in its base64 wire form. Must equal
# `turn_server.MAX_SUBMISSION_BYTES`; decoded, it must fit
# `firebase_store.MAX_SUBMISSION_DATA` and a 1 MiB Firestore document.
MAX_BYTES = int(os.environ.get('CS_RELAY_MAX_BYTES', str(1024 * 1024)))

# Ceiling on a join request body (a name, a build string and two numbers).
MAX_JOIN_BYTES = int(os.environ.get('CS_RELAY_MAX_JOIN_BYTES', '4096'))

# Longest build string a join request may carry; read by the minimum-build gate.
MAX_BUILD_CHARS = 64

# Collection prefix for the galaxies; matches `firebase_store.PREFIX`.
PREFIX = os.environ.get('CS_RELAY_PREFIX', firebase_store.PREFIX)

# Galaxy document fields this door never serves. `joined` holds uids, which
# would expose the uid-to-civ mapping that `seats` is kept out of `/state` for.
PRIVATE_FIELDS = (turn_store.JOINED_KEY,)

# How long a warm instance serves `/state`, `/submissions` and `/turn` from a
# galaxy document it has already read (see `hold_for`). Routes that check a
# seat, a closed galaxy or the current turn always read afresh.
STATE_FLOOR = 1.0       # shortest hold, in seconds
STATE_SHARE = 20.0      # past the deadline, hold for overdue time / this
STATE_CEILING = float(os.environ.get('CS_RELAY_STATE_CEILING', '60'))  # 0 = off

# Longest hold for the galaxy listing, in seconds; 0 turns it off.
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


def project() -> str:
    """The project this relay serves.

    `CS_RELAY_PROJECT` wins over the runtime's `GCLOUD_PROJECT`, so a test can
    point a relay at a `demo-` project.
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

    Against the Auth emulator (`FIREBASE_AUTH_EMULATOR_HOST` set) tokens are
    unsigned, so the claims are checked here. Anywhere else `firebase_admin`
    verifies the signature, and if it is not installed the request is refused.
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


def store_for(galaxy: str):
    """This galaxy's admin store, kept for the life of the instance.

    The first Firestore call in a process pays for grpc.
    """
    if galaxy not in _STORES:
        _STORES[galaxy] = firebase_store.FirebaseTurnStore(
            project(), galaxy,
            bucket=os.environ.get('CS_RELAY_BUCKET')
            or os.environ.get('FIREBASE_STORAGE_BUCKET'),
            prefix=PREFIX)
    return _STORES[galaxy]


# Placeholder galaxy name for the store `catalog` borrows its Firestore client
# from. It is never asked about a galaxy.
CATALOG = '_catalog'


def catalog():
    """The collection every galaxy document sits in."""
    store = store_for(CATALOG)
    return store.fs.collection(store.prefix)


def catalog_rows() -> list:
    """Every galaxy, with the fields `/state` serves plus `id` and `name`."""
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

    Before the deadline, up to the deadline. Past it, overdue time divided by
    STATE_SHARE, floored at STATE_FLOOR. With no deadline, the ceiling. All
    capped at the ceiling.
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
    """`catalog_rows`, held for the shortest window of any galaxy in it.

    A row held across a publish would send a launcher to a past turn.
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

    `FirebaseTurnStore.state` returns only the store interface's fields, so the
    relay reads the document itself. The read replaces the copy `public_doc`
    holds.
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

    A copy read inside its window is served again. Only `/state`,
    `/submissions` and `/turn` use this, and none of them decides what a caller
    may do.
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


def reclaimed_refusal(store, doc: dict, civ: str) -> Refused:
    """The refusal for writing as a civ that is no longer on the roster.

    Names the turn and the missed count when the galaxy recorded a reclaim, so
    the player reads why the seat went rather than that it never existed.
    """
    rec = (doc.get(turn_store.RECLAIMED_KEY) or {}).get(civ)
    if rec is None:
        return Refused(403, f'{civ} is no longer on the roster of '
                            f'{store.galaxy}')
    text = f'{civ} was reclaimed in {store.galaxy}'
    if rec.get('turn') is not None:
        text += f' at turn {rec["turn"]}'
    if rec.get('missed'):
        text += f' after {rec["missed"]} missed turns in a row'
    return Refused(403, text + '; the seat is no longer on the roster. Ask to '
                               'join again to play.')


def claim_seat(store, doc: dict, uid: str, civ: str) -> str:
    """Bind an unheld civ to this uid, when the galaxy allows it.

    Only with `seat_claim: 'first-use'` on the galaxy document, since the first
    uid to ask takes whichever civ it names. Runs in a transaction so two
    launchers claiming one civ produce one holder and one refusal.
    """
    if (doc.get('seat_claim') or '') != 'first-use':
        raise Refused(403, f'no seat in {store.galaxy} is bound to this '
                           f'sign-in')
    if civ not in list(doc.get('civs') or []):
        if civ in (doc.get(turn_store.RECLAIMED_KEY) or {}):
            raise reclaimed_refusal(store, doc, civ)
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

    Only the two steps of a submission may `claim`: the upload ticket and the
    commit. A read never takes a seat, even though the ticket is fetched with a
    GET.
    """
    held = seat_of(doc, uid)
    # Writes only: a reclaimed player may still read back earlier submissions.
    if claim and held is not None and held not in (doc.get('civs') or []):
        raise reclaimed_refusal(store, doc, held)
    if held is None and claim:
        held = claim_seat(store, doc, uid, civ)
    if held is None:
        raise Refused(403, f'no seat in {store.galaxy} is bound to this '
                           f'sign-in')
    if held != civ:
        raise Refused(403, f'this sign-in plays {held} in {store.galaxy}, '
                           f'not {civ}')
    return held


def signing_identity(store):
    """Whatever this process can sign a Cloud Storage URL with.

    A Cloud Functions runtime service account has no private key, so it signs
    through the IAM Credentials API with its email and an access token. That
    needs `roles/iam.serviceAccountTokenCreator` granted to the runtime account
    on itself and `iamcredentials.googleapis.com` enabled (see deploy.py). A
    service account key signs by itself.

    `CS_RELAY_SIGNER` names the identity to sign as, for a user credential that
    impersonates a service account.
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

    Against the Storage emulator, which checks no signatures, the emulator's
    unauthenticated JSON API endpoint is returned instead.
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


def _json(obj, code=200):
    return code, {'Content-Type': 'application/json'}, \
        json.dumps(obj).encode('utf-8')


def _text(body: bytes, code=200, ctype='application/octet-stream'):
    return code, {'Content-Type': ctype}, body


def _redirect(url: str):
    """A 302 to Storage.

    `HttpTurnStore` drops the Authorization header when following it, since
    Cloud Storage refuses a bearer token alongside a signed URL.
    """
    return 302, {'Location': url, 'Cache-Control': 'no-store'}, b''


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
    """A closed galaxy takes no more submissions.

    The relay writes through `write_submission`, not the store's `submit`, so
    the store's own closed check does not apply.
    """
    if doc.get(turn_store.STATUS_KEY) == turn_store.CLOSED:
        why = doc.get(turn_store.CLOSED_REASON_KEY)
        raise Refused(409, f'{store.galaxy} is closed'
                           + (f': {why}' if why else ''))


def _may_submit(store, doc, uid, turn, civ):
    """Everything that has to be true before a submission is stored.

    The galaxy is open, this caller is that civ, taking the seat when the
    galaxy allows first use, and the turn is the one being played. `doc` is a
    fresh read, never a held copy.
    """
    refuse_if_closed(store, doc)
    seat_or_refuse(store, doc, uid, civ, claim=True)
    current = _current_turn(doc)
    if turn != current:
        raise Refused(409, f'{store.galaxy} is playing turn {current}, '
                           f'not turn {turn}')


def _upload_ticket(turn, civ):
    """Where to put a submission: this relay's own route.

    The shape `turn_server.upload_ticket` answers with: `url` relative to the
    launcher's base, `method`, `encoding`, `headers`, `commit` (None, since the
    POST stores the body in one call) and `max_bytes`. A ticket authorises
    nothing; the POST makes every check afresh. `b64` is the smaller form a
    launcher can send.
    """
    quoted = urllib.parse.quote(civ)
    return _json({
        'url': f'/submission/{turn}/{quoted}',
        'method': 'POST',
        'encoding': 'b64',
        'headers': {'Content-Type': 'text/plain'},
        'commit': None,
        'max_bytes': MAX_BYTES,
    })


def _submission_body(body: bytes, turn: int) -> bytes:
    """The decompressed blob a submission body carries, checked.

    Either form is taken, by the rule `save_parser.load_any` uses: a body that
    opens with a section tag is the blob itself, and anything else is the
    base64 wire form. The wire form is inflated under the store's cap, so a
    body built to expand is refused while it is read.
    """
    if len(body) > MAX_BYTES:
        raise Refused(413, f'a submission may be {MAX_BYTES:,} bytes and this '
                           f'one is {len(body):,}')
    import save_parser as sp
    try:
        if body[:4] in sp.KNOWN_TAGS:
            blob = body
        else:
            blob = turn_store.decode_capped(body.strip())
        turn_store.check_save(blob, turn)
    except turn_store.SubmissionTooLarge as exc:
        raise Refused(413, str(exc))
    except Exception as exc:                                    # noqa: BLE001
        raise Refused(400, f'this is not a save of turn {turn}: '
                           f'{type(exc).__name__}: {exc}')
    return blob


def _take_submission(store, doc, uid, turn, civ, body):
    """Check a submission and store it, in one call.

    Every refusal happens before anything is written, so a bad upload leaves
    the player's previous submission for this turn standing.
    """
    _may_submit(store, doc, uid, turn, civ)
    blob = _submission_body(body, turn)
    try:
        tag = store.write_submission(civ, turn, blob, doc=doc)
    except turn_store.SubmissionTooLarge as exc:
        raise Refused(413, str(exc))
    forget_doc(store)
    return _json({'turn': turn, 'civ': civ, 'bytes': len(blob), 'tag': tag})


def _serve_submission(store, doc, turn, civ, headers):
    """One civ's submission in base64 wire form, or 304 when the caller has it.

    The ETag is the blob's `submission_tag`, which `HttpTurnStore` sends back
    as `If-None-Match`. A turn whose submissions are Storage objects
    (`before_documents`) is served as a redirect to the object.
    """
    rec = store.submission_record(civ, turn)
    if rec is not None:
        tag = str(rec.get('tag') or '')
        etag = f'"{tag}"'
        asked = [t.strip() for t in
                 (_header(headers, 'if-none-match') or '').split(',')]
        out = {'ETag': etag, 'Cache-Control': 'no-cache'}
        if tag and etag in asked:
            return 304, out, b''
        out['Content-Type'] = 'text/plain'
        return 200, out, base64.b64encode(bytes(rec['data']))
    if store.before_documents(turn, doc):
        name = store.submission_object(civ, turn)
        if store.bucket.blob(name).exists():
            return _redirect(signed_url(store, name, 'GET'))
    raise Refused(404, f'{civ} has not submitted for {turn}')


def _commit_submission(store, doc, uid, turn, civ):
    """Bring in an upload made to a Cloud Storage ticket.

    The object is read once, checked as `_take_submission` checks a body,
    stored as a document, and deleted either way. The current ticket carries no
    commit, so only older tickets arrive here.
    """
    _may_submit(store, doc, uid, turn, civ)
    blob = store.bucket.blob(store.submission_object(civ, turn))
    # A range read, so an oversized object is never downloaded whole.
    from google.api_core import exceptions
    try:
        data = blob.download_as_bytes(start=0, end=MAX_BYTES)
    except exceptions.NotFound:
        if store.submission_record(civ, turn) is not None:
            return _json({'turn': turn, 'civ': civ})
        raise Refused(404, f'{civ} has uploaded nothing for turn {turn}')
    try:
        decoded = _submission_body(data, turn)
        tag = store.write_submission(civ, turn, decoded, doc=doc)
    except (Refused, turn_store.SubmissionTooLarge) as exc:
        # The upload replaced any earlier object, so without a document the
        # civ now has nothing stored for this turn.
        blob.delete()
        if store.submission_record(civ, turn) is None:
            store.forget_submitted(civ, turn)
        forget_doc(store)
        if isinstance(exc, Refused):
            raise
        raise Refused(413, str(exc))
    blob.delete()
    forget_doc(store)
    return _json({'turn': turn, 'civ': civ, 'bytes': len(decoded),
                  'tag': tag})


def _header(headers: dict, name: str):
    """One request header, whatever its case."""
    for key, value in (headers or {}).items():
        if key.lower() == name:
            return value
    return None


def _lodge_join(store, doc, uid, body):
    """Ask this galaxy for a seat. The one write an unseated caller may make.

    Returns where the request landed and the turn it was lodged during. A
    galaxy with no first turn is refused, since `joins.apply` needs a published
    blob to merge a newcomer into.
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
    # Checked here too: a request need not come from the launcher, and a name
    # the engine cannot hold would fail at the referee on every tick.
    problem = turn_store.name_problem(name)
    if problem:
        raise Refused(400, problem)

    turn = _current_turn(doc)
    roster = [str(c) for c in (doc.get('civs') or [])]
    held = seat_of(doc, uid)
    if held and held in roster:
        # Already playing. A reclaimed seat is not in the roster and falls
        # through, since `joins.py` can grant a returning player.
        raise Refused(409, f'this sign-in already plays {held} in '
                           f'{store.galaxy}')
    if any(c == name or c.lower() == name.lower() for c in roster):
        # Also refused at the launcher's button, but the launcher is not a
        # boundary.
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
        # Every reason the store refuses a request is about the caller.
        raise Refused(400, str(exc))
    return _json({'where': where, 'key': uid, 'requested_turn': turn})


def _own_key(uid: str, asked: str) -> str:
    """The request key in a path, once it has been shown to be this caller's.

    A request is filed under the token's uid, so that is the only key a caller
    may name. The key is unquoted first, because `HttpTurnStore` quotes it
    whole.
    """
    key = urllib.parse.unquote(asked or '')
    if key != uid:
        raise Refused(403, 'a join request is read back by the sign-in that '
                           'lodged it')
    return key


def _own_join(store, doc, uid):
    """What became of the request this caller lodged, or that it is waiting.

    Keyed by the token's uid only; no route here lists what is waiting on a
    galaxy, which would tell a stranger who else is joining. The waiting
    request is checked first: answering consumes a request, so a waiting one is
    always newer than any answer. At most two named document reads, since
    launchers poll this.
    """
    waiting = store.join_request(uid)
    if waiting is not None:
        return _json({'state': 'waiting', 'request': waiting})
    answer = store.join_answer(uid)
    if answer is not None:
        return _json({'state': 'answered', 'answer': answer})
    return _json({'state': 'none'})


# A launcher uploads its redacted log here when a turn fails or the player
# presses Send log. Each is one Firestore document in its own collection beside
# the galaxies. Nothing here reads one back; the operator uses
# `server/dev_tools/log_tool.py`. The route is about no galaxy: the body's
# galaxy, civ and turn are claims, and the uid is the token's.
LOG_ROUTE = '_logs'
LOG_COLLECTION = f'{PREFIX}_logs'
LOG_QUOTA_COLLECTION = f'{PREFIX}_log_quota'

# `launcher.LOG_UPLOAD_CAP` plus room for the trim notice. The zlib text is
# inflated under this cap.
LOG_TEXT_MAX = 256 * 1024 + 1024

# The ceiling on the request body, the compressed text in base64 inside a
# small JSON object. Checked before anything is decoded.
LOG_BODY_MAX = 512 * 1024

# Uploads allowed per sign-in, and for the whole project, per UTC day.
LOG_PER_UID_PER_DAY = int(os.environ.get('CS_RELAY_LOG_PER_UID', '5'))
LOG_PER_DAY = int(os.environ.get('CS_RELAY_LOG_PER_DAY', '100'))

# Each upload deletes up to LOG_PRUNE_BATCH logs older than LOG_KEEP_DAYS.
# `expire_at` is also written, for a Firestore TTL policy.
LOG_KEEP_DAYS = 14
LOG_PRUNE_BATCH = 5

# Why a log was sent. Anything else is recorded as `other`.
LOG_REASONS = ('turn_failed', 'player')

# The longest string any other field of the body may carry.
LOG_FIELD_CHARS = 128


def _wall() -> float:
    """The calendar clock a log is stamped and counted by. A function so a
    test can move it."""
    return time.time()


def _log_text(body: bytes) -> tuple:
    """(request, text) from a log upload body, checked, or a refusal."""
    import zlib
    if len(body or b'') > LOG_BODY_MAX:
        raise Refused(413, f'a log upload may be {LOG_BODY_MAX:,} bytes and '
                           f'this one is {len(body):,}')
    try:
        req = json.loads(body or b'{}')
    except ValueError:
        raise Refused(400, 'a log upload is a JSON object')
    if not isinstance(req, dict) or not isinstance(req.get('log'), str):
        raise Refused(400, 'a log upload carries `log`, the text compressed '
                           'with zlib and then in base64')
    try:
        packed = base64.b64decode(req['log'], validate=True)
        inflate = zlib.decompressobj()
        raw = inflate.decompress(packed, LOG_TEXT_MAX + 1)
    except Exception:                                           # noqa: BLE001
        raise Refused(400, 'the log is not zlib-compressed text in base64')
    if len(raw) > LOG_TEXT_MAX or inflate.unconsumed_tail:
        raise Refused(413, f'a log may be {LOG_TEXT_MAX:,} bytes once '
                           f'inflated')
    if not inflate.eof:
        raise Refused(400, 'the log is not zlib-compressed text in base64')
    return req, raw.decode('utf-8', 'replace')


def _log_field(req: dict, key: str):
    value = req.get(key)
    if value is None:
        return None
    return str(value)[:LOG_FIELD_CHARS]


def log_day(when: float) -> str:
    """The UTC day a log is counted against, as `YYYY-MM-DD`."""
    return datetime.datetime.fromtimestamp(
        when, datetime.timezone.utc).strftime('%Y-%m-%d')


def _take_log(uid: str, body: bytes):
    """Store a launcher's redacted log, inside the per-sign-in and daily caps.

    The count and the log are written in one transaction. The text is
    recompressed here, so every stored log is known to inflate. Pruning old
    logs afterwards is best effort.
    """
    import secrets
    import zlib
    from google.cloud import firestore

    req, text = _log_text(body)
    now = _wall()
    day = log_day(now)
    fs = store_for(CATALOG).fs
    quota = fs.collection(LOG_QUOTA_COLLECTION).document(day)
    stamp = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    log_id = (stamp.strftime('%Y%m%dT%H%M%SZ') + f'-{uid[:8]}-'
              + secrets.token_hex(3))
    ref = fs.collection(LOG_COLLECTION).document(log_id)
    why = req.get('why')
    record = {
        'uid': uid,
        'received_at': now,
        'expire_at': stamp + datetime.timedelta(days=LOG_KEEP_DAYS),
        'why': why if why in LOG_REASONS else 'other',
        'build': _log_field(req, 'build'),
        'galaxy': _log_field(req, 'galaxy'),
        'civ': _log_field(req, 'civ'),
        'turn': req['turn'] if isinstance(req.get('turn'), int) else None,
        'bytes': len(text.encode('utf-8')),
        'data': zlib.compress(text.encode('utf-8'), 6),
    }

    @firestore.transactional
    def _put(tx):
        counts = quota.get(transaction=tx).to_dict() or {}
        mine = int((counts.get('uids') or {}).get(uid) or 0)
        total = int(counts.get('total') or 0)
        if mine >= LOG_PER_UID_PER_DAY:
            raise Refused(429, f'this sign-in has sent {mine} logs today, '
                               f'which is the most one may send in a day')
        if total >= LOG_PER_DAY:
            raise Refused(429, 'the operator has had as many logs today as '
                               'can be kept; try again tomorrow')
        tx.set(quota, {'total': total + 1, 'uids': {uid: mine + 1}},
               merge=True)
        tx.set(ref, record)

    _put(fs.transaction())
    _prune_logs(fs, now)
    return _json({'id': log_id, 'bytes': record['bytes'],
                  'stored': len(record['data'])})


def _prune_logs(fs, now: float) -> int:
    """Delete up to `LOG_PRUNE_BATCH` logs older than `LOG_KEEP_DAYS`."""
    from google.cloud.firestore_v1.base_query import FieldFilter
    cutoff = now - LOG_KEEP_DAYS * 86400
    gone = 0
    try:
        old = fs.collection(LOG_COLLECTION).where(
            filter=FieldFilter('received_at', '<', cutoff)).limit(
                LOG_PRUNE_BATCH).stream()
        for snap in old:
            snap.reference.delete()
            gone += 1
    except Exception:                                           # noqa: BLE001
        pass
    return gone


REFEREE_ONLY = ('only the referee publishes a turn, and it does not come '
                'through here')


def _route(method: str, path: str, headers: dict, body: bytes):
    # A query is dropped: only referee routes carry one, and those are refused.
    path = (path or '').split('?', 1)[0]
    parts = [p for p in path.split('/') if p]
    if not parts:
        # The galaxy listing. Signed in like every other route.
        if method in ('GET', 'HEAD'):
            verify(bearer(headers))
            return _json({'galaxies': listing()})
        raise Refused(404, 'the galaxy is the first part of the path')
    if parts[0] == LOG_ROUTE:
        # About no galaxy, and signed in like every other route.
        uid = verify(bearer(headers))
        if method == 'POST' and len(parts) == 1:
            return _take_log(uid, body)
        raise Refused(403, 'a log is sent here and read by the operator, and '
                           'this door serves none back')
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
            # The document's current turn always exists: `start` and `publish`
            # write the object before the document names it.
            doc = public_doc(store)
            if turn != doc.get('turn') and not store.has_turn(turn):
                raise Refused(404, f'no turn {turn}')
            return _redirect(signed_url(store, store.turn_object(turn), 'GET'))
        if len(parts) == 2 and parts[0] == 'submissions':
            # The store answers the current turn from the document already
            # read; see `FirebaseTurnStore.submitted_civs`.
            return _json(store.submitted_civs(_turn_number(parts[1]),
                                              doc=public_doc(store)))
        if len(parts) == 4 and parts[:2] == ['upload', 'submission']:
            # No document read: the POST it names checks everything afresh.
            return _upload_ticket(_turn_number(parts[2]), parts[3])

    doc = galaxy_doc(store)

    if method in ('GET', 'HEAD'):
        if len(parts) == 3 and parts[0] == 'submission':
            turn, civ = _turn_number(parts[1]), parts[2]
            # A seatless caller has no submission, so the answer is 404. Under
            # `first-use` every player is seatless before their first
            # submission, and the turn loop asks this route before serving them.
            if seat_of(doc, uid) is None:
                raise Refused(404, f'no submission of yours for {turn}')
            seat_or_refuse(store, doc, uid, civ)
            return _serve_submission(store, doc, turn, civ, headers)
        if len(parts) == 2 and parts[0] == 'archive':
            record = store.archive_record(_turn_number(parts[1]))
            if record is None:
                raise Refused(404, f'no archive for turn {parts[1]}')
            return _json(record)
        if len(parts) == 3 and parts[0] == 'note':
            turn, civ = _turn_number(parts[1]), parts[2]
            # A seatless caller has no note, as for submissions.
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
            # What is waiting on a galaxy is the worker's question
            # (`turn_server.py` answers it on a LAN). Here it would tell a
            # stranger who else is trying to join.
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
            return _take_submission(store, doc, uid, _turn_number(parts[1]),
                                    parts[2], body)
        if parts and parts[0] in ('start', 'turn', 'archive', 'note'):
            raise Refused(403, REFEREE_ONLY)

    raise Refused(404, f'no route for {method} {path}')


def handle(method: str, path: str, headers: dict = None, body: bytes = b''):
    """(status, headers, body) for one request.

    Framework free: `main.py` adapts a Cloud Functions request to this, and
    the relay's tests call it directly.
    """
    try:
        return _route(method, path, headers or {}, body or b'')
    except Refused as exc:
        return _json({'error': str(exc)}, exc.code)
    except Exception as exc:                                    # noqa: BLE001
        return _json({'error': f'{type(exc).__name__}: {exc}'}, 500)
