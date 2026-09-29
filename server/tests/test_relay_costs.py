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

from google.cloud.firestore_v1 import batch, document, query   # noqa: E402
from google.cloud.storage import blob as gcs_blob               # noqa: E402
from google.cloud.storage import client as gcs_client           # noqa: E402

import relay                                                    # noqa: E402
from firebase_store import FirebaseTurnStore                     # noqa: E402
import save_parser as sp                                        # noqa: E402
from test_store_equivalence import make_blob                    # noqa: E402
from turn_store import HttpTurnStore                             # noqa: E402

PASS, FAIL = [], []
TURN = 7
FOUR_HOURS = 14400

COUNT = {'reads': 0, 'exists': 0, 'writes': 0, 'storage': 0}
# (status, body length) of every submission read the socket served.
SERVED = []


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


def _count_commit(orig):
    # A batch is billed a write per operation in it, and a lone `set` is a
    # batch of one inside the client library, so this sees both.
    def commit(self, *a, **k):
        COUNT['writes'] += len(getattr(self, '_write_pbs', []) or [])
        return orig(self, *a, **k)
    return commit


def _count_storage(orig):
    def call(self, *a, **k):
        COUNT['storage'] += 1
        return orig(self, *a, **k)
    return call


document.DocumentReference.get = _count_get(document.DocumentReference.get)
query.Query.stream = _count_stream(query.Query.stream)
gcs_blob.Blob.exists = _count_exists(gcs_blob.Blob.exists)
batch.WriteBatch.commit = _count_commit(batch.WriteBatch.commit)
for _name in ('exists', 'upload_from_string', 'download_as_bytes', 'delete'):
    setattr(gcs_blob.Blob, _name,
            _count_storage(getattr(gcs_blob.Blob, _name)))
gcs_client.Client.list_blobs = _count_storage(gcs_client.Client.list_blobs)


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
        if self.command == 'GET' and '/submission/' in path:
            SERVED.append((status, len(out)))
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
    a submission would be stored into a closed galaxy, and a seat moved to
    another civ would still read its old submission.
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
        'POST', f'/{g.galaxy}/submission/{turn}/DemoPlayer', H,
        sp.encode_save(make_blob(turn, b'c')))
    check('while a submission is refused as closed',
          (code, 'closed' in json.loads(body).get('error', '')), (409, True))
    check('and nothing was stored', g.submission('DemoPlayer', turn), None)
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
    """The turn the document is on, and a submission stored as a document.

    Fails if either asks the bucket whether an object exists, which is one
    Class B operation per launcher per turn for each. The control beside the
    turn is a case the document cannot answer, which still asks.
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
    check('a stored submission is served from two document reads and no '
          'Storage operation', (out[0], cost), (200, (2, 0)))
    g.doc.set({'submitted': {str(turn): []}}, merge=True)
    cost, out = costs(lambda: relay.handle(
        'GET', f'/{g.galaxy}/submission/{turn}/DemoPlayer', H, b''))
    check('and still found when the galaxy document does not record it',
          (out[0], cost), (200, (2, 0)))
    cost, out = costs(lambda: relay.handle(
        'GET', f'/{g.galaxy}/submission/{turn + 1}/DemoPlayer', H, b''))
    check('and a submission that is not there is 404 without asking the '
          'bucket', (out[0], cost), (404, (2, 0)))


def test_a_submission_costs(stores, token, base):
    """What one upload costs at the relay, since submissions are documents.

    H13. Every upload was a Cloud Storage Class A operation, and Class A is
    5,000 a month for the whole project. Counted here at the client
    libraries: Firestore reads and writes, and every Storage call a
    submission could make. Fails if an upload reaches Storage at all, if a
    player's second upload in a turn still writes the galaxy document, or if
    the ticket reads anything.
    """
    print('what a submission costs')
    fresh()
    g = stores[2]
    H = {'Authorization': f'Bearer {token}'}
    turn = g.current()[0]
    cost, out = costs(lambda: relay.handle(
        'GET', f'/{g.galaxy}/upload/submission/{turn}/DemoPlayer', H, b''))
    check('the upload ticket reads nothing and names the relay route',
          (cost, json.loads(out[2])['url']),
          ((0, 0), f'/submission/{turn}/DemoPlayer'))
    first = make_blob(turn, b'1')
    COUNT['writes'] = COUNT['storage'] = 0
    cost, out = costs(lambda: relay.handle(
        'POST', f'/{g.galaxy}/submission/{turn}/DemoPlayer', H,
        sp.encode_save(first)))
    check('a first upload of the turn: one read, two writes, no Storage',
          (out[0], cost[0], COUNT['writes'], COUNT['storage']),
          (200, 1, 2, 0))
    COUNT['writes'] = COUNT['storage'] = 0
    cost, out = costs(lambda: relay.handle(
        'POST', f'/{g.galaxy}/submission/{turn}/DemoPlayer', H,
        sp.encode_save(make_blob(turn, b'2'))))
    check('a second upload: one read, one write, no Storage',
          (out[0], cost[0], COUNT['writes'], COUNT['storage']),
          (200, 1, 1, 0))

    # The same through a store, the way a launcher does it: ticket, POST, and
    # the read back `player_turn` makes, answered 304 with no body.
    player = HttpTurnStore(f'{base}/{g.galaxy}', token=lambda: token,
                           state_seconds=0)
    mine = make_blob(turn, b'3')
    COUNT['writes'] = COUNT['storage'] = 0
    player.submit('DemoPlayer', turn, mine)
    check('a launcher upload makes no Storage call', COUNT['storage'], 0)
    SERVED.clear()
    check('the read back is the bytes sent', player.submission(
        'DemoPlayer', turn), mine)
    check('and came back as 304 with no body', SERVED, [(304, 0)])
    check('the referee reads the same bytes', g.submission('DemoPlayer', turn),
          mine)
    COUNT['writes'] = COUNT['storage'] = 0
    g.submissions(turn)
    check('the referee reading the turn makes no Storage call',
          COUNT['storage'], 0)


def test_before_documents(stores, token):
    """A galaxy started before H13, whose current turn has Storage objects.

    The relay and the worker change over within a turn, and a submission
    uploaded before that is an object. Fails if it went missing: the player's
    launcher skips an upload identical to the last, so the object would be
    their turn.
    """
    print('a turn from before submissions were documents')
    fresh()
    g = stores[0]
    H = {'Authorization': f'Bearer {token}'}
    turn = g.current()[0]
    from google.cloud import firestore
    import firebase_store
    g.doc.update({firebase_store.SUBMISSIONS_FROM_KEY: firestore.DELETE_FIELD})
    old = make_blob(turn, b'o')
    g._put(g.submission_object('DemoPlayer', turn), sp.encode_save(old),
           'text/plain')
    check('the referee still reads the object',
          g.submissions(turn).get('DemoPlayer'), old)
    check('and so does one civ read', g.submission('DemoPlayer', turn), old)
    check('and has_submitted', g.has_submitted('DemoPlayer', turn), True)
    cost, out = costs(lambda: relay.handle(
        'GET', f'/{g.galaxy}/submission/{turn}/DemoPlayer', H, b''))
    check('the relay redirects to it, asking the bucket once',
          (out[0], cost[1]), (302, 1))
    new = make_blob(turn, b'n')
    relay.handle('POST', f'/{g.galaxy}/submission/{turn}/DemoPlayer', H,
                 sp.encode_save(new))
    check('a document written after it wins, for the referee',
          g.submissions(turn).get('DemoPlayer'), new)
    check('and for one civ read', g.submission('DemoPlayer', turn), new)
    g.publish(turn + 1, make_blob(turn + 1), turn_seconds=FOUR_HOURS)
    check('the first publish on this code marks where documents begin',
          (g.doc.get().to_dict() or {}).get(
              firebase_store.SUBMISSIONS_FROM_KEY), turn + 1)
    check('after which a missing submission does not ask the bucket',
          costs(lambda: g.submission('Neighbor', turn + 1)), ((2, 0), None))


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
        test_a_submission_costs(stores, token, base)
        test_before_documents(stores, token)
    finally:
        httpd.shutdown()
        httpd.server_close()
        for s in stores:
            s.delete_everything()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
