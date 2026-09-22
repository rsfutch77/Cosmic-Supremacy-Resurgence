"""
test_relay_function.py , what the relay lets a player do, and what it does not
==============================================================================
    firebase emulators:start --only firestore,storage,auth
    set FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
    set STORAGE_EMULATOR_HOST=http://127.0.0.1:9199
    set FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
    server\\.venv\\Scripts\\python.exe server\\tests\\test_relay_function.py

The equivalence test proves three stores agree. It cannot prove anything about
this, and H7 recorded why: every run of it, on the emulator and against the live
project, authenticated as an administrator. A store that three implementations
agree about is still a store no player can open.

So this test is the other half, and it is a test of refusals. Half of what the
relay is for is the requests it declines, and a refusal is the easiest thing in
a test suite to fake: a check that a call failed passes just as well when the
call failed for the wrong reason, or when the thing it was meant to do was never
possible. Every refusal below therefore checks the status code **and** that the
store underneath is unchanged, and each one is written next to a note saying
what would have made it fail. A check that cannot fail has gone wrong here
before.

The referee's store is the oracle. It is `FirebaseTurnStore` with an
administrator's credentials, which is what the referee holds, and every write a
player makes through the relay is read back through it: the relay saying 200 is
not evidence that bytes landed, and the relay saying 403 is not evidence that
they did not.

Two things the emulator cannot prove, said here rather than implied
-------------------------------------------------------------------
**Signed URLs are not signed on the emulator.** There is no key to sign with
and nothing checks a signature, so `relay.signed_url` hands out the emulator's
own open endpoint and the `x-goog-content-length-range` header that caps an
upload at the edge on a real bucket is ignored here. The size cap is still
tested, because the relay checks it again at commit for exactly this reason,
and the commit check is the one that runs in both places.

**Token signatures are not checked on the emulator.** The emulator mints
unsigned tokens by design and `relay.verify` reads the claims itself rather than
pulling in `firebase_admin` to be told to skip the signature. The crafted tokens
below therefore exercise the claim checks and say nothing about the signature
check, which is `firebase_admin`'s and runs only against a real project.
"""
import base64
import json
import os
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'functions')):
    if d not in sys.path:
        sys.path.insert(0, d)

PROJECT = os.environ.get('CS_RELAY_PROJECT') or 'demo-cs-resurgence'
BUCKET = os.environ.get('CS_RELAY_BUCKET') or f'{PROJECT}.firebasestorage.app'
AUTH_HOST = os.environ.get('FIREBASE_AUTH_EMULATOR_HOST') or '127.0.0.1:9099'

# Set before `relay` is imported, because it reads both at import. The cap is
# two kilobytes rather than the two megabytes a deployment uses, so that an
# oversized submission is a small object: what is under test is the refusal,
# not the emulator's throughput.
os.environ['CS_RELAY_PROJECT'] = PROJECT
os.environ['CS_RELAY_BUCKET'] = BUCKET
os.environ['CS_RELAY_MAX_BYTES'] = '2048'
os.environ.setdefault('FIREBASE_AUTH_EMULATOR_HOST', AUTH_HOST)

import save_parser as sp                                        # noqa: E402
import turn_store                                               # noqa: E402
from firebase_store import FirebaseTurnStore                     # noqa: E402
from turn_store import HttpTurnStore                             # noqa: E402

import relay                                                    # noqa: E402

PASS, FAIL = [], []

TURN = 7


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def section(tag, payload, version=0):
    head = (len(payload) & sp.SIZE_MASK) | (version << sp.VERSION_SHIFT)
    return tag + struct.pack('<I', head) + payload


def make_blob(turn, filler=b'.', size=60):
    """A blob carrying a turn number, which is what `check_save` reads."""
    if len(filler) < size:
        filler = (filler * size)[:size]
    return section(b'SAVE',
                   section(b'GLOB', struct.pack('<I', turn) + filler[:size]) +
                   section(b'KNPL', bytes([0xAA]) * 8))


BLOB7 = make_blob(TURN)
BLOB8 = make_blob(TURN + 1)
MINE = make_blob(TURN, b'o')
THEIRS = make_blob(TURN, b'n')
MINE8 = make_blob(TURN + 1, b'q')
# Incompressible filler, so that the encoded form is genuinely over the cap
# rather than zlib turning a large blob into a small object.
HUGE = make_blob(TURN + 1, os.urandom(8192), size=8192)


# ── identities ───────────────────────────────────────────────────────────────
def sign_up():
    """A new anonymous user from the Auth emulator: (uid, id token).

    The same call the launcher's `fb_auth` makes against the real service, with
    the emulator's stand-in API key. Anonymous sign-in is H4's identity: a
    stable uid per install, no login screen and no account.
    """
    url = (f'http://{AUTH_HOST}/identitytoolkit.googleapis.com/v1/'
           f'accounts:signUp?key=fake-api-key')
    req = urllib.request.Request(
        url, data=json.dumps({'returnSecureToken': True}).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        out = json.loads(r.read())
    return out['localId'], out['idToken']


def craft_token(**claims):
    """An unsigned token with whatever claims are asked for.

    For the checks that a claim is read rather than assumed. The signature is
    three characters of nonsense, which the emulator verifier does not look at
    and `firebase_admin` would refuse outright.
    """
    base = {'aud': PROJECT,
            'iss': f'https://securetoken.google.com/{PROJECT}',
            'sub': 'crafted', 'user_id': 'crafted',
            'exp': int(time.time()) + 3600}
    base.update(claims)

    def part(obj):
        raw = json.dumps(obj).encode('utf-8')
        return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')

    return f'{part({"alg": "none"})}.{part(base)}.xxx'


# ── the relay behind an HTTP port ────────────────────────────────────────────
class RelayHandler(BaseHTTPRequestHandler):
    """`relay.handle` behind a socket, so `HttpTurnStore` can reach it.

    The adapter `main.py` is, minus Cloud Functions. Everything the deployed
    relay decides is in `relay.handle` and is what runs here; what is not
    covered is the three lines that turn a Cloud Functions request into its
    arguments.
    """

    def log_message(self, fmt, *args):
        pass

    def _run(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        path = urllib.parse.urlparse(self.path).path
        status, headers, out = relay.handle(
            self.command, path, dict(self.headers), body)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = _run
    do_POST = _run


def status_of(exc_or_none):
    """The HTTP status a call refused with, or None when it did not refuse."""
    return getattr(exc_or_none, 'code', None)


def refusal(fn, *a, **kw):
    """Call something and hand back the HTTPError it raised, or None."""
    try:
        fn(*a, **kw)
    except urllib.error.HTTPError as exc:
        return exc
    except Exception as exc:                                    # noqa: BLE001
        return exc
    return None


def body_of(exc):
    try:
        return json.loads(exc.read()).get('error', '')
    except Exception:                                           # noqa: BLE001
        return ''


# ── the run ──────────────────────────────────────────────────────────────────
def main():
    if not os.environ.get('FIRESTORE_EMULATOR_HOST'):
        print('SKIPPED: no FIRESTORE_EMULATOR_HOST. This run says nothing '
              'about the relay.')
        return 0

    galaxy = f'relay_{int(time.time())}'
    referee = FirebaseTurnStore(PROJECT, galaxy, bucket=BUCKET)
    roster = ['DemoPlayer', 'Neighbor', 'Third']
    referee.start(BLOB7, roster, turn_seconds=1800)

    uid_a, token_a = sign_up()
    uid_b, token_b = sign_up()
    uid_c, token_c = sign_up()
    uid_d, token_d = sign_up()
    referee.doc.set({'seats': {uid_a: 'DemoPlayer', uid_b: 'Neighbor'}},
                    merge=True)

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), RelayHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}/{galaxy}'
    player_a = HttpTurnStore(base, token=lambda: token_a)
    player_b = HttpTurnStore(base, token=lambda: token_b)
    player_c = HttpTurnStore(base, token=lambda: token_c)
    anonymous = HttpTurnStore(base)

    try:
        run(referee, galaxy, base, player_a, player_b, player_c, anonymous,
            {'a': (uid_a, token_a), 'b': (uid_b, token_b),
             'c': (uid_c, token_c), 'd': (uid_d, token_d)})
    finally:
        httpd.shutdown()
        httpd.server_close()
        n = referee.delete_everything()
        print(f'  removed {n} object(s) and document(s) from {galaxy}')

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for name in FAIL:
        print(f'  failed: {name}')
    return 1 if FAIL else 0


def run(referee, galaxy, base, player_a, player_b, player_c, anonymous, ids):
    uid_a, token_a = ids['a']
    uid_b, _token_b = ids['b']
    uid_c, token_c = ids['c']
    uid_d, token_d = ids['d']

    def call(method, path, token=None, body=b''):
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        return relay.handle(method, f'/{galaxy}{path}', headers, body)

    print('who is asking')
    # Fails if the relay answered a request that carried no token, which is the
    # whole of what stands between an open bucket and a closed one.
    check('a request with no token is refused',
          call('GET', '/state')[0], 401)
    check('and the store is still only reachable as the referee',
          referee.state()['turn'], 7)
    check('a request whose token is not a token is refused',
          call('GET', '/state', 'not-a-token')[0], 401)
    # Fails if `aud` were not compared: an ID token from any other Firebase
    # project is a real signed token, and the only thing that says it is not
    # for this project is the claim.
    check('a token minted for another project is refused',
          call('GET', '/state', craft_token(aud='demo-somewhere-else'))[0],
          401)
    check('a token minted by something else is refused',
          call('GET', '/state', craft_token(iss='https://example.test/x'))[0],
          401)
    check('an expired token is refused',
          call('GET', '/state', craft_token(exp=int(time.time()) - 60))[0],
          401)
    # Fails if a crafted token were accepted for any reason at all, which would
    # make the four checks above pass for the wrong reason.
    check('a real emulator token is accepted',
          call('GET', '/state', token_a)[0], 200)

    print('what any signed-in caller may read')
    state = player_a.state()
    check('state holds exactly the five fields the interface names',
          sorted(state), ['civs', 'deadline', 'hash', 'turn', 'turn_seconds'])
    # Fails if the relay returned the galaxy document rather than the five
    # fields: the seat map lives in that document, and handing it out would
    # tell every player which uid is which civ.
    check('and not the seat map beside them', 'seats' in state, False)
    check('current() is the turn the referee published',
          player_a.current()[0], 7)
    # Fails if the blob did not come back through a signed URL and decode: the
    # relay never carries a blob, so these bytes came from Cloud Storage in the
    # base64 wire form and were decoded by the store.
    check('a turn blob comes back through Storage as it went in',
          player_a.turn_blob(7), BLOB7)
    check('has_turn is True for a published turn', player_a.has_turn(7), True)
    check('has_turn is False for one that was not',
          player_a.has_turn(99), False)
    check('a missing turn raises FileNotFoundError',
          type(refusal(player_a.turn_blob, 99)).__name__, 'FileNotFoundError')
    check('the archive of a turn nobody archived is None',
          player_a.archive_record(7), None)

    print('a uid with no seat')
    # The Games tab's view of a galaxy this player has not joined. Fails if the
    # relay refused an unseated caller outright, which would stop a launcher
    # showing a galaxy before its player is in it.
    check('an unseated caller reads the clock', player_c.current()[0], 7)
    check('an unseated caller reads a published turn',
          player_c.turn_blob(7), BLOB7)
    refused = refusal(player_c.submit, 'DemoPlayer', 7, MINE)
    check('an unseated caller may not submit as anyone',
          status_of(refused), 403)
    check('and is told that it holds no seat',
          'no seat' in body_of(refused), True)
    # Fails if the refusal were only the relay declining to answer while the
    # write happened anyway.
    check('nothing of theirs reached the store',
          referee.submission('DemoPlayer', 7), None)

    print('a seated player submits')
    where = player_a.submit('DemoPlayer', 7, MINE)
    check('submit names where it landed',
          bool(where) and 'DemoPlayer' in where, True)
    # The oracle: the referee reads the object, so this says bytes landed in
    # the store rather than that the relay said 200.
    check('the referee reads back exactly what was submitted',
          referee.submission('DemoPlayer', 7), MINE)
    check('has_submitted sees it through the relay',
          player_a.has_submitted('DemoPlayer', 7), True)
    check('and the submission list names them',
          json.loads(call('GET', '/submissions/7', token_a)[2]),
          ['DemoPlayer'])

    print('a seated player may not be another player')
    refused = refusal(player_a.submit, 'Neighbor', 7, THEIRS)
    # Fails if the seat map were not consulted. This is the rule H4 recorded as
    # inexpressible in Security Rules: here it is a string comparison.
    check('submitting as another civ is refused', status_of(refused), 403)
    check('and the refusal names the seat this caller does hold',
          'DemoPlayer' in body_of(refused), True)
    check('and nothing was written for that civ',
          referee.submission('Neighbor', 7), None)
    refused = refusal(player_a.submission, 'Neighbor', 7)
    check('reading another player submission is refused',
          status_of(refused), 403)
    check('and reading their own is not',
          player_a.submission('DemoPlayer', 7), MINE)
    player_b.submit('Neighbor', 7, THEIRS)
    check('the other player submits for themselves',
          referee.submission('Neighbor', 7), THEIRS)
    # Fails if the read rule were dropped once the object existed, which is the
    # version of this bug that a test written before the object exists misses.
    check('and it is still unreadable by the first',
          status_of(refusal(player_a.submission, 'Neighbor', 7)), 403)

    print('only the referee publishes')
    for method, path, what in (
            ('POST', '/turn/8', 'publishing a turn'),
            ('POST', '/start', 'starting a galaxy'),
            ('POST', '/archive/7', 'writing an archive record'),
            ('POST', '/note/7/DemoPlayer', 'leaving a note')):
        code, _h, body = call(method, path, token_a, b'anything')
        check(f'{what} through the relay is refused', code, 403)
        check(f'and {what} says the referee does it, not the caller',
              'referee' in json.loads(body)['error'], True)
    # Fails if a publish had gone through: the turn the referee published is
    # still the turn, and turn 8 does not exist.
    check('the clock did not move', referee.current()[0], 7)
    check('and no turn 8 was written', referee.has_turn(8), False)
    check('the legacy inline submission route is refused with the way in',
          call('POST', '/submission/7/DemoPlayer', token_a, MINE)[0], 405)
    # A query is not a way past the routing. Fails if the path were split
    # before the query was cut off, which is how `/start?civ=A` became a route
    # nothing matched and a 404 where a 403 belonged.
    check('a query does not turn a refused route into an unknown one',
          call('POST', '/start?civ=A&seconds=60', token_a, BLOB8)[0], 403)

    print('a submission is for the turn being played')
    referee.publish(8, BLOB8)
    refused = refusal(player_a.submit, 'DemoPlayer', 7, MINE)
    # Fails if the turn were taken from the caller rather than from the galaxy
    # document. A player who kept a stale ticket, or a launcher that lost the
    # clock, would otherwise write into a turn the referee has closed.
    check('a submission for the previous turn is refused',
          status_of(refused), 409)
    check('and the refusal names the turn being played',
          'turn 8' in body_of(refused), True)
    check('the closed turn still holds what it held',
          referee.submission('DemoPlayer', 7), MINE)
    player_a.submit('DemoPlayer', 8, MINE8)
    check('and a submission for the current turn is taken',
          referee.submission('DemoPlayer', 8), MINE8)

    print('a submission is capped and has to be a save')
    refused = refusal(player_a.submit, 'DemoPlayer', 8, HUGE)
    # On a real bucket Cloud Storage refuses this at the edge, because the cap
    # is signed into the URL. The emulator does not check signed headers, so
    # what runs here is the commit-time check, which is the one that runs in
    # both places. Fails if neither were there.
    check('an oversized submission is refused', status_of(refused), 413)
    check('and it is not left where the referee would read it',
          referee.submission('DemoPlayer', 8), None)

    ticket = json.loads(call('GET', '/upload/submission/8/DemoPlayer',
                             token_a)[2])
    put(ticket, b'this is not a save at all')
    code, _h, body = call('POST', ticket['commit'], token_a)
    # Fails if the relay trusted the upload. It never sees the bytes, so the
    # only place left to ask whether they were a save is afterwards.
    check('a submission that is not a save is refused', code, 400)
    check('and says so rather than raising',
          'not a save' in json.loads(body)['error'], True)
    check('and is deleted rather than left for the referee',
          referee.submission('DemoPlayer', 8), None)

    ticket = json.loads(call('GET', '/upload/submission/8/DemoPlayer',
                             token_a)[2])
    put(ticket, sp.encode_save(make_blob(3)))
    code, _h, body = call('POST', ticket['commit'], token_a)
    # A real save, of the wrong turn. Fails if the check stopped at "does this
    # parse", which is the check that is easy to write and easy to pass.
    check('a save of another turn is refused', code, 400)
    check('and is deleted too', referee.submission('DemoPlayer', 8), None)
    player_a.submit('DemoPlayer', 8, MINE8)
    check('and a good one still goes through afterwards',
          referee.submission('DemoPlayer', 8), MINE8)

    print('notes go to the player they were left for')
    reasons = ['system 193: rename DROPPED, not a majority']
    referee.put_note('DemoPlayer', 8, reasons)
    check('a player reads their own note', player_a.note('DemoPlayer', 8),
          reasons)
    check('and not another player note',
          status_of(refusal(player_b.note, 'DemoPlayer', 8)), 403)
    check('a turn with no note for them is not a refusal',
          player_b.note('Neighbor', 8), [])

    print('the archive is readable and not writable')
    record = {'turn': 7, 'published': 8, 'refused': {'Player.One': reasons}}
    referee.archive(7, record)
    check('a player reads the archive record', player_a.archive_record(7),
          record)

    print('a seat the operator did not bind')
    refused = refusal(player_c.submit, 'Third', 8, MINE8)
    # Fails if an unseated uid could take a seat without the operator saying
    # so. First use is a land grab among strangers and is off unless asked for.
    check('claiming a seat is refused until the operator allows it',
          status_of(refused), 403)
    referee.doc.set({'seat_claim': 'first-use'}, merge=True)
    # Reading is not joining. Fails if a read could claim a seat, which under
    # first-use would hand the seat to whoever browsed the galaxy first rather
    # than to whoever played it.
    check('a read does not take a seat even when first use is allowed',
          status_of(refusal(player_c.note, 'Third', 8)), 403)
    check('and no seat was bound by the attempt',
          (referee.doc.get().to_dict().get('seats') or {}).get(uid_c), None)
    player_c.submit('Third', 8, MINE8)
    check('with first-use allowed, an unheld civ is taken',
          referee.submission('Third', 8), MINE8)
    doc = referee.doc.get().to_dict()
    check('and the seat is written down rather than assumed each time',
          (doc.get('seats') or {}).get(uid_c), 'Third')
    # Fails if the claim did not hold: the whole point of writing it down is
    # that the second caller cannot take the same civ.
    player_d = HttpTurnStore(base, token=lambda: token_d)
    refused = refusal(player_d.submit, 'Third', 8, MINE8)
    check('a second caller may not take a seat that is held',
          status_of(refused), 403)
    check('and is told the seat is held rather than that it does not exist',
          'already held' in body_of(refused), True)
    refused = refusal(player_c.submit, 'DemoPlayer', 8, MINE8)
    check('and the first caller is now that civ and no other',
          status_of(refused), 403)
    refused = refusal(player_d.submit, 'NotInTheRoster', 8, MINE8)
    check('a civ that is not in the roster cannot be claimed',
          status_of(refused), 403)
    check('and is told it is not a civ here',
          'not a civ' in body_of(refused), True)
    check('the referee still sees the roster it started with',
          referee.civs(), ['DemoPlayer', 'Neighbor', 'Third'])

    print('the token does not follow a redirect off site')
    # A signed URL carries its own authorisation, and Cloud Storage reads an
    # Authorization header it was not given in the signature as a credential
    # and refuses the request over it. `urllib` copies every header onto a
    # redirect unless something stops it. Fails if that handler were dropped,
    # which on the emulator nothing else would notice, because the emulator
    # ignores the header.
    handler = turn_store._drop_auth_on_redirect()()
    req = urllib.request.Request('http://one.test/a')
    req.add_header('Authorization', 'Bearer secret')
    same = handler.redirect_request(req, None, 302, 'Found', {},
                                    'http://one.test/b')
    other = handler.redirect_request(req, None, 302, 'Found', {},
                                     'http://two.test/b')
    check('a redirect within the relay keeps the token',
          [v for k, v in same.headers.items() if k.lower() == 'authorization'],
          ['Bearer secret'])
    check('a redirect to Cloud Storage does not',
          [v for k, v in other.headers.items()
           if k.lower() == 'authorization'], [])

    print('the launcher sends no header when it has no identity')
    # The contract with the launcher: a token callable that answers None, or no
    # callable at all, behaves exactly as this store did before there was one.
    # Fails if the header were sent unconditionally, or an empty one sent.
    check('a store with no token is refused by the relay rather than crashing',
          status_of(refusal(anonymous.state)), 401)
    empty = HttpTurnStore(base, token=lambda: None)
    check('and a token callable that answers None is the same thing',
          status_of(refusal(empty.state)), 401)

    print('what the relay never sees')
    # The design property: the function is the control plane. Fails if a blob
    # route were ever answered with a body rather than a redirect to Storage.
    code, headers, body = call('GET', '/turn/8', token_a)
    check('a turn download is a redirect, not a body', code, 302)
    check('and carries no bytes of the blob', body, b'')
    check('and points at Cloud Storage rather than at the relay',
          headers['Location'].startswith(
              os.environ['STORAGE_EMULATOR_HOST'].rstrip('/')), True)


def put(ticket, data: bytes):
    """Upload straight to the ticket's URL, the way the launcher does."""
    req = urllib.request.Request(ticket['url'], data=data,
                                 method=ticket.get('method', 'PUT'))
    for key, value in (ticket.get('headers') or {}).items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


if __name__ == '__main__':
    sys.exit(main())
