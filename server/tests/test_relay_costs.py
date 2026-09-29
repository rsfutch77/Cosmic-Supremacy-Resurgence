"""
test_relay_costs.py , what a relay request costs, counted at the client library
===============================================================================
    python functions/emulators.py --only firestore,storage,auth
    (set the three variables it prints)
    server\\.venv\\Scripts\\python.exe server\\tests\\test_relay_costs.py

docs\\Public_Beta_Plan.md H12. Hundreds of launchers left open all month each
poll `/state` while they follow a galaxy and the listing while the Galaxies
page is on screen. Read afresh, every one of those is a Firestore document
read, and the listing is one per galaxy in the catalog, so the project's reads
grew with the number of launchers open. A warm instance now serves a galaxy's
public face from a copy it read inside a window shaped like the launcher's own
poll gap, and the listing the same way.

What has to be true of that is counted here rather than argued: Firestore
reads at `DocumentReference.get` and `Query.stream`, Storage existence checks
at `Blob.exists`. And beside every saving, the thing it must not cost: a route
that decides what a caller may do still reads the document afresh, so a galaxy
closed or a seat moved while a copy is held is refused at once.

The relay's clock for its held copies is replaced with one this test moves, so
a window can be waited out without waiting.
"""
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'), os.path.join(ROOT, 'functions'), HERE):
    if d not in sys.path:
        sys.path.insert(0, d)

PROJECT = os.environ.get('CS_RELAY_PROJECT') or 'demo-cs-resurgence'
BUCKET = os.environ.get('CS_RELAY_BUCKET') or f'{PROJECT}.firebasestorage.app'
AUTH_HOST = os.environ.get('FIREBASE_AUTH_EMULATOR_HOST') or '127.0.0.1:9099'
# A collection of its own, so galaxies other runs left behind are not in the
# listing this counts.
PREFIX = f'relaycost_{int(time.time())}'
os.environ['CS_RELAY_PROJECT'] = PROJECT
os.environ['CS_RELAY_BUCKET'] = BUCKET
os.environ['CS_RELAY_PREFIX'] = PREFIX
os.environ.setdefault('FIREBASE_AUTH_EMULATOR_HOST', AUTH_HOST)
# The deployed defaults, whatever the shell running this carries.
os.environ.pop('CS_RELAY_STATE_CEILING', None)
os.environ.pop('CS_RELAY_LISTING_SECONDS', None)

from google.cloud.firestore_v1 import document, query          # noqa: E402
from google.cloud.storage import blob as gcs_blob               # noqa: E402

import relay                                                    # noqa: E402
from firebase_store import FirebaseTurnStore                     # noqa: E402
from test_store_equivalence import make_blob                    # noqa: E402
from turn_store import HttpTurnStore                             # noqa: E402

PASS, FAIL = [], []
TURN = 7
FOUR_HOURS = 14400

COUNT = {'reads': 0, 'exists': 0}


def check(name, got, want):
    ok = want(got) if callable(want) else got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}: {got!r}')
    if not ok and not callable(want):
        print(f'          wanted {want!r}')


# ── counting at the client libraries ─────────────────────────────────────────
def _count_get(orig):
    def get(self, *a, **k):
        COUNT['reads'] += 1
        return orig(self, *a, **k)
    return get


def _count_stream(orig):
    # A query is billed a read per document it returns, and one when it
    # returns none.
    def stream(self, *a, **k):
        n = 0
        for snap in orig(self, *a, **k):
            n += 1
            yield snap
        COUNT['reads'] += max(1, n)
    return stream


def _count_exists(orig):
    def exists(self, *a, **k):
        COUNT['exists'] += 1
        return orig(self, *a, **k)
    return exists


document.DocumentReference.get = _count_get(document.DocumentReference.get)
query.Query.stream = _count_stream(query.Query.stream)
gcs_blob.Blob.exists = _count_exists(gcs_blob.Blob.exists)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def move(self, seconds):
        self.t += seconds


CLOCK = Clock()
relay._now = CLOCK


def costs(fn):
    """What one call cost: (Firestore reads, Storage existence checks)."""
    COUNT['reads'] = COUNT['exists'] = 0
    out = fn()
    return (COUNT['reads'], COUNT['exists']), out


def sign_up():
    url = (f'http://{AUTH_HOST}/identitytoolkit.googleapis.com/v1/'
           f'accounts:signUp?key=fake-api-key')
    req = urllib.request.Request(
        url, data=json.dumps({'returnSecureToken': True}).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        out = json.loads(r.read())
    return out['localId'], out['idToken']


class RelayHandler(BaseHTTPRequestHandler):
    """`relay.handle` behind a socket, as test_relay_function.py has it."""

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


def fresh():
    """Forget every held copy, so a case starts from a cold instance."""
    relay._HELD.clear()
    relay._LISTING = None


# ── the cases ────────────────────────────────────────────────────────────────
def test_the_window(stores):
    """The window's shape, which every count below comes out of.

    Fails if it stops following the deadline: a constant would hold a copy
    across the moment a turn is published, and a window that ignored which
    side of the deadline it is on would either hold a copy past it or read
    every few seconds through the hours before it, when nothing can change.
    """
    print('the window')
    now = time.time()
    h = relay.hold_for
    check('four hours from a deadline, the ceiling',
          h({'deadline': now + FOUR_HOURS}), relay.STATE_CEILING)
    check('twenty seconds out, held up to the deadline and no further',
          round(h({'deadline': now + 20})), 20)
    check('at the deadline, the floor', h({'deadline': now}),
          relay.STATE_FLOOR)
    check('ten seconds overdue, still the floor',
          h({'deadline': now - 10}), relay.STATE_FLOOR)
    check('ten minutes overdue, thirty seconds',
          round(h({'deadline': now - 600})), 30)
    check('an hour overdue, the ceiling', h({'deadline': now - 3600}),
          relay.STATE_CEILING)
    check('a ceiling of 0 turns it off', h({'deadline': now}, 0), 0.0)


def test_the_listing(stores, token):
    """Three galaxies, listed again and again inside and outside the window.

    Fails if the listing streams the catalog per request, which is one read
    per galaxy per request: the cost that grows with the galaxy count.
    """
    print('the listing')
    fresh()
    H = {'Authorization': f'Bearer {token}'}
    listed = lambda: relay.handle('GET', '/', H, b'')      # noqa: E731
    cost, first = costs(listed)
    check('a cold listing reads each galaxy once', cost, (len(stores), 0))
    rows = json.loads(first[2])['galaxies']
    check('and lists them', [r['id'] for r in rows],
          sorted(s.galaxy for s in stores))
    cost, again = costs(listed)
    check('a second listing inside the window reads nothing', cost, (0, 0))
    check('and says the same', again[2], first[2])
    CLOCK.move(relay.LISTING_SECONDS + 1)
    cost, _ = costs(listed)
    check('once the window has passed it streams the catalog again', cost,
          (len(stores), 0))
    check('a caller with no token is still refused inside the window',
          relay.handle('GET', '/', {}, b'')[0], 401)

    # One galaxy nearly due holds the whole listing to its deadline, and one
    # overdue holds it to the floor, because a row held across a publish sends
    # the Games page to a past turn.
    stores[2].doc.set({'deadline': time.time() + 5}, merge=True)
    CLOCK.move(relay.LISTING_SECONDS + 1)
    costs(listed)
    CLOCK.move(4)
    cost, _ = costs(listed)
    check('with a galaxy due in five seconds, held four seconds later',
          cost, (0, 0))
    CLOCK.move(1.5)
    cost, _ = costs(listed)
    check('and read again once that deadline has passed',
          cost, (len(stores), 0))
    stores[2].doc.set({'deadline': time.time() - 10}, merge=True)
    CLOCK.move(relay.LISTING_SECONDS + 1)
    costs(listed)
    CLOCK.move(relay.STATE_FLOOR + 0.1)
    cost, _ = costs(listed)
    check('with a galaxy overdue the listing is held for the floor',
          cost, (len(stores), 0))
    stores[2].doc.set({'deadline': time.time() + FOUR_HOURS}, merge=True)


def test_the_public_face(stores, token):
    """`/state` polled by many launchers, and what a held copy may cost.

    Fails if `/state` reads the document per request, and in the second half if
    a copy is held past its window.
    """
    print('the public face')
    fresh()
    g = stores[0]
    H = {'Authorization': f'Bearer {token}'}
    path = f'/{g.galaxy}/state'
    cost, first = costs(lambda: relay.handle('GET', path, H, b''))
    check('a cold /state reads the document', cost, (1, 0))
    total = 0
    for _ in range(20):
        c, _out = costs(lambda: relay.handle('GET', path, H, b''))
        total += c[0]
    check('twenty more inside the window read nothing', total, 0)
    check('/submissions rides the same copy',
          costs(lambda: relay.handle(
              'GET', f'/{g.galaxy}/submissions/{TURN}', H, b''))[0], (0, 0))
    check('a caller with no token is still refused while a copy is held',
          relay.handle('GET', path, {}, b'')[0], 401)

    g.publish(TURN + 1, make_blob(TURN + 1), turn_seconds=FOUR_HOURS)
    held = json.loads(relay.handle('GET', path, H, b'')[2])['turn']
    check('inside the window the copy is the turn before the publish',
          held, TURN)
    CLOCK.move(relay.STATE_CEILING + 1)
    cost, out = costs(lambda: relay.handle('GET', path, H, b''))
    check('after it, one read and the published turn',
          (cost, json.loads(out[2])['turn']), ((1, 0), TURN + 1))


def test_decisions_are_read_fresh(stores, uid, token):
    """A held copy says the galaxy is open. The door still reads it closed.

    Fails if any route that decides what a caller may do took the held copy:
    the upload ticket would be issued into a closed galaxy, and a seat moved
    to another civ would still read its old submission.
    """
    print('what a caller may do is never decided from a held copy')
    fresh()
    g = stores[0]
    H = {'Authorization': f'Bearer {token}'}
    turn = g.current()[0]
    relay.handle('GET', f'/{g.galaxy}/state', H, b'')
    g.close('closed while a copy is held')
    state = json.loads(relay.handle('GET', f'/{g.galaxy}/state', H, b'')[2])
    check('the held /state still reads open', state.get('status'), None)
    code, _h, body = relay.handle(
        'GET', f'/{g.galaxy}/upload/submission/{turn}/DemoPlayer', H, b'')
    check('while the upload ticket is refused as closed',
          (code, 'closed' in json.loads(body).get('error', '')), (409, True))
    g.reopen()

    relay.handle('GET', f'/{g.galaxy}/state', H, b'')
    g.doc.set({'seats': {uid: 'Neighbor'}}, merge=True)
    code = relay.handle(
        'GET', f'/{g.galaxy}/submission/{turn}/DemoPlayer', H, b'')[0]
    check('a seat moved while a copy is held is refused its old civ',
          code, 403)
    g.doc.set({'seats': {uid: 'DemoPlayer'}}, merge=True)


def test_own_commit_is_read_at_once(stores, token, base):
    """A player submits through this instance and asks who has submitted.

    Fails if the commit leaves the held copy standing: the Games page and the
    turn loop on this machine would be told the submission is not there for
    a whole window.
    """
    print('a commit through this instance')
    fresh()
    g = stores[1]
    H = {'Authorization': f'Bearer {token}'}
    turn = g.current()[0]
    path = f'/{g.galaxy}/submissions/{turn}'
    check('nobody has submitted, and that answer is now held',
          json.loads(relay.handle('GET', path, H, b'')[2]), [])
    HttpTurnStore(f'{base}/{g.galaxy}', token=lambda: token,
                  state_seconds=0).submit('DemoPlayer', turn,
                                          make_blob(turn, b'o'))
    check('the commit is read back at once, without the clock moving',
          json.loads(relay.handle('GET', path, H, b'')[2]), ['DemoPlayer'])


def test_storage_is_not_asked_what_the_document_says(stores, token):
    """The turn the document is on, and a submission the document records.

    Fails if either asks the bucket whether an object exists, which is one
    Class B operation per launcher per turn for each. The control beside each
    is a case the document cannot answer, which still asks.
    """
    print('what the document already says')
    fresh()
    g = stores[1]
    H = {'Authorization': f'Bearer {token}'}
    turn = g.current()[0]
    cost, out = costs(lambda: relay.handle(
        'GET', f'/{g.galaxy}/turn/{turn}', H, b''))
    check('the current turn redirects without an existence check',
          (out[0], cost[1]), (302, 0))
    cost, out = costs(lambda: relay.handle(
        'GET', f'/{g.galaxy}/turn/{turn + 5}', H, b''))
    check('a turn that is not the current one is still asked, and is 404',
          (out[0], cost[1]), (404, 1))
    cost, out = costs(lambda: relay.handle(
        'GET', f'/{g.galaxy}/submission/{turn}/DemoPlayer', H, b''))
    check('a recorded submission redirects without an existence check',
          (out[0], cost[1]), (302, 0))
    g.doc.set({'submitted': {str(turn): []}}, merge=True)
    cost, out = costs(lambda: relay.handle(
        'GET', f'/{g.galaxy}/submission/{turn}/DemoPlayer', H, b''))
    check('one the document does not record is asked of the bucket, and found',
          (out[0], cost[1]), (302, 1))
    cost, out = costs(lambda: relay.handle(
        'GET', f'/{g.galaxy}/submission/{turn - 1}/DemoPlayer', H, b''))
    check('and a submission that is not there is still 404',
          (out[0], cost[1]), (404, 1))


def main():
    if not os.environ.get('FIRESTORE_EMULATOR_HOST'):
        print('SKIPPED: no FIRESTORE_EMULATOR_HOST. This run says nothing '
              'about the relay.')
        return 0
    stores = []
    for i in range(3):
        s = FirebaseTurnStore(PROJECT, f'g{i}', bucket=BUCKET, prefix=PREFIX)
        s.start(make_blob(TURN), ['DemoPlayer', 'Neighbor'],
                turn_seconds=FOUR_HOURS)
        stores.append(s)
    uid, token = sign_up()
    for s in stores:
        s.doc.set({'seats': {uid: 'DemoPlayer'}}, merge=True)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), RelayHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}'
    try:
        test_the_window(stores)
        test_the_listing(stores, token)
        test_the_public_face(stores, token)
        test_decisions_are_read_fresh(stores, uid, token)
        test_own_commit_is_read_at_once(stores, token, base)
        test_storage_is_not_asked_what_the_document_says(stores, token)
    finally:
        httpd.shutdown()
        httpd.server_close()
        for s in stores:
            s.delete_everything()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
