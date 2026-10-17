"""
test_relay_function.py: what the relay lets a player do, and what it does not
==============================================================================
    python functions/emulators.py --only firestore,storage,auth

    set FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
    set STORAGE_EMULATOR_HOST=http://127.0.0.1:9199
    set FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
    server\\.venv\\Scripts\\python.exe server\\tests\\test_relay_function.py

`emulators.py` resolves each port before anything binds and prints the three
variables. Add `--print-env` to have it resolve and print without starting.

The emulators must run as project `demo-cs-resurgence`, which `emulators.py`
names. Without a `--project` the Auth emulator mints tokens for the live
project, and every token is refused at "a real emulator token is accepted".

Every refusal checks the status code and that the store underneath is
unchanged. The referee's store (`FirebaseTurnStore` with administrator
credentials) is the oracle: every write a player makes through the relay is
read back through it.

The emulator neither signs URLs nor checks token signatures. Signed URLs point
at the emulator's open endpoint, and the crafted tokens exercise the claim
checks only. The signature check is `firebase_admin`'s and runs only against a
real project.
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

# Set before `relay` is imported, which reads them at import. The 2 KB cap
# keeps an oversized submission small.
os.environ['CS_RELAY_PROJECT'] = PROJECT
os.environ['CS_RELAY_BUCKET'] = BUCKET
os.environ['CS_RELAY_MAX_BYTES'] = '2048'
os.environ.setdefault('FIREBASE_AUTH_EMULATOR_HOST', AUTH_HOST)
# Held copies are off, so every answer reflects the referee's last write.
# test_relay_costs.py covers the held copies.
os.environ['CS_RELAY_STATE_CEILING'] = '0'
os.environ['CS_RELAY_LISTING_SECONDS'] = '0'

import galaxy_directory                                         # noqa: E402
import save_parser as sp                                        # noqa: E402
import turn_store                                               # noqa: E402
import firebase_store                                           # noqa: E402
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
MINE8_B = make_blob(TURN + 1, b'r')
# Incompressible filler, so the encoded form is over the cap.
HUGE = make_blob(TURN + 1, os.urandom(8192), size=8192)


def sign_up():
    """A new anonymous user from the Auth emulator: (uid, id token).

    The same call the launcher's `fb_auth` makes, with the emulator's stand-in
    API key.
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

    The signature is placeholder text, which the emulator verifier ignores.
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


class RelayHandler(BaseHTTPRequestHandler):
    """`relay.handle` behind a socket, so `HttpTurnStore` can reach it.

    Stands in for the Cloud Functions adapter in `main.py`.
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

    root = f'http://127.0.0.1:{httpd.server_address[1]}'

    try:
        run(referee, galaxy, base, player_a, player_b, player_c, anonymous,
            {'a': (uid_a, token_a), 'b': (uid_b, token_b),
             'c': (uid_c, token_c), 'd': (uid_d, token_d)})
        run_catalog(referee, galaxy, root, token_a, token_c)
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
    check('a request with no token is refused',
          call('GET', '/state')[0], 401)
    check('and the store is still only reachable as the referee',
          referee.state()['turn'], 7)
    check('a request whose token is not a token is refused',
          call('GET', '/state', 'not-a-token')[0], 401)
    # A token from another Firebase project is validly signed; only `aud`
    # rejects it.
    check('a token minted for another project is refused',
          call('GET', '/state', craft_token(aud='demo-somewhere-else'))[0],
          401)
    check('a token minted by something else is refused',
          call('GET', '/state', craft_token(iss='https://example.test/x'))[0],
          401)
    check('an expired token is refused',
          call('GET', '/state', craft_token(exp=int(time.time()) - 60))[0],
          401)
    # Shows the refusals above are not refusing every token.
    check('a real emulator token is accepted',
          call('GET', '/state', token_a)[0], 200)

    print('what any signed-in caller may read')
    state = player_a.state()
    check('state holds exactly the five fields the interface names',
          sorted(state), ['civs', 'deadline', 'hash', 'turn', 'turn_seconds'])
    # The seat map would tell every player which uid is which civ.
    check('and not the seat map beside them', 'seats' in state, False)
    check('current() is the turn the referee published',
          player_a.current()[0], 7)
    # The relay never carries a turn blob: these bytes came from Cloud Storage
    # in the base64 wire form and were decoded by the store.
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
    # The Games tab's view of a galaxy this player has not joined.
    check('an unseated caller reads the clock', player_c.current()[0], 7)
    check('an unseated caller reads a published turn',
          player_c.turn_blob(7), BLOB7)
    refused = refusal(player_c.submit, 'DemoPlayer', 7, MINE)
    check('an unseated caller may not submit as anyone',
          status_of(refused), 403)
    check('and is told that it holds no seat',
          'no seat' in body_of(refused), True)
    check('nothing of theirs reached the store',
          referee.submission('DemoPlayer', 7), None)

    # Under `first-use` a seat binds on the first submission, so a seatless
    # player must be able to ask whether it has already played. Only the
    # submission route writes a submission, and it needs a seat, so a seatless
    # caller has none.
    check('an unseated caller asking for its own submission is told there '
          'is none, rather than refused',
          player_c.submission('DemoPlayer', 7), None)
    check('and the same for a note', player_c.note('DemoPlayer', 7), [])
    denied = refusal(player_a.submission, 'Neighbor', 7)
    check('while a seated caller reading another civ is still refused',
          status_of(denied), 403)
    check('and told which civ it actually plays',
          'DemoPlayer' in body_of(denied), True)

    print('a seated player submits')
    where = player_a.submit('DemoPlayer', 7, MINE)
    check('submit names where it landed',
          bool(where) and 'DemoPlayer' in where, True)
    # The referee reads the object back, so this shows the bytes landed.
    check('the referee reads back exactly what was submitted',
          referee.submission('DemoPlayer', 7), MINE)
    check('has_submitted sees it through the relay',
          player_a.has_submitted('DemoPlayer', 7), True)
    check('and the submission list names them',
          json.loads(call('GET', '/submissions/7', token_a)[2]),
          ['DemoPlayer'])

    print('a seated player may not be another player')
    refused = refusal(player_a.submit, 'Neighbor', 7, THEIRS)
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
    # The read rule still holds once the object exists.
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
    check('the clock did not move', referee.current()[0], 7)
    check('and no turn 8 was written', referee.has_turn(8), False)
    # The ticket names the relay's own route, so a body posted there is a
    # submission.
    ticket = json.loads(call('GET', '/upload/submission/7/DemoPlayer',
                             token_a)[2])
    check('the upload ticket names the relay route, a POST, and no commit',
          (ticket['url'], ticket['method'], ticket['encoding'],
           ticket['commit']),
          ('/submission/7/DemoPlayer', 'POST', 'b64', None))
    check('a body posted straight to the submission route is taken',
          call('POST', '/submission/7/DemoPlayer', token_a,
               sp.encode_save(MINE))[0], 200)
    check('and is what the referee reads',
          referee.submission('DemoPlayer', 7), MINE)
    # The query is cut off before the path is routed, so `/start?civ=A` is
    # still a refused route (403).
    check('a query does not turn a refused route into an unknown one',
          call('POST', '/start?civ=A&seconds=60', token_a, BLOB8)[0], 403)

    print('a submission is for the turn being played')
    referee.publish(8, BLOB8)
    refused = refusal(player_a.submit, 'DemoPlayer', 7, MINE)
    # The turn is taken from the galaxy document, so a stale ticket cannot
    # write into a turn the referee has closed.
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
    check('an oversized submission is refused', status_of(refused), 413)
    # Every refusal comes before the write, so the earlier submission stands.
    check('and the submission it was sent over still stands',
          referee.submission('DemoPlayer', 8), MINE8)
    check('and the galaxy document still says they have submitted',
          firebase_store.submitted_of(referee.doc.get().to_dict(), 8),
          ['DemoPlayer'])

    code, _h, body = call('POST', ticket_url(call, token_a, 8, 'DemoPlayer'),
                          token_a, b'this is not a save at all')
    check('a submission that is not a save is refused', code, 400)
    check('and says so rather than raising',
          'not a save' in json.loads(body)['error'], True)
    check('and does not replace the one before it',
          referee.submission('DemoPlayer', 8), MINE8)

    code, _h, body = call('POST', ticket_url(call, token_a, 8, 'DemoPlayer'),
                          token_a, sp.encode_save(make_blob(3)))
    # A real save of the wrong turn: parsing alone is not enough.
    check('a save of another turn is refused', code, 400)
    check('and does not replace it either',
          referee.submission('DemoPlayer', 8), MINE8)
    player_a.submit('DemoPlayer', 8, MINE8_B)
    check('and a good one still goes through afterwards',
          referee.submission('DemoPlayer', 8), MINE8_B)
    player_a.submit('DemoPlayer', 8, MINE8)

    print('who has submitted is answered without listing the bucket')
    # A launcher asks this every poll. Listings are counted at the relay's
    # Storage client, so any listing the route makes is seen.
    under = relay.store_for(galaxy)
    client = under.gcs
    listed = []
    real_list = client.list_blobs

    def counting_list(*a, **kw):
        listed.append(kw.get('prefix'))
        return real_list(*a, **kw)

    client.list_blobs = counting_list
    try:
        check('the relay names who has submitted for the current turn',
              json.loads(call('GET', '/submissions/8', token_a)[2]),
              ['DemoPlayer'])
        check('has_submitted through the relay is True for them',
              player_a.has_submitted('DemoPlayer', 8), True)
        check('and False for a player who has not',
              player_b.has_submitted('Neighbor', 8), False)
        check('and none of those three polls listed the bucket', listed, [])
        # A past turn is answered from the submission documents.
        check('a past turn is answered from the stored submissions',
              json.loads(call('GET', '/submissions/7', token_a)[2]),
              ['DemoPlayer', 'Neighbor'])
        check('and that did not list the bucket either', listed, [])
        # Shows the counter can see a listing at all.
        under._objects(7)
        check('CONTROL: a listing made on purpose is counted', len(listed), 1)
    finally:
        client.list_blobs = real_list
    check('the relay has no listing of its own',
          hasattr(relay, '_submitted_civs'), False)
    check('the document agrees with the stored submissions for the current '
          'turn', referee.submitted_civs(8), referee.listed_civs(8))
    # A commit that read turn 7 and landed after the publish of turn 8 must
    # neither count for turn 8 nor hide the turn-7 submissions.
    referee.record_submitted('Third', 7)
    check('a commit landing after the publish is not read as this turn',
          'Third' in referee.submitted_civs(8), False)
    check('and a past turn is still answered from the stored submissions',
          referee.submitted_civs(7), ['DemoPlayer', 'Neighbor'])

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
    # First-use seat claims are off unless the operator turns them on.
    check('claiming a seat is refused until the operator allows it',
          status_of(refused), 403)
    referee.doc.set({'seat_claim': 'first-use'}, merge=True)
    # A read never claims a seat under first-use. A seatless caller has no
    # note, so the read answers empty.
    check('a read answers empty rather than claiming',
          player_c.note('Third', 8), [])
    check('and no seat was bound by the attempt',
          (referee.doc.get().to_dict().get('seats') or {}).get(uid_c), None)
    player_c.submit('Third', 8, MINE8)
    check('with first-use allowed, an unheld civ is taken',
          referee.submission('Third', 8), MINE8)
    doc = referee.doc.get().to_dict()
    check('and the seat is written down rather than assumed each time',
          (doc.get('seats') or {}).get(uid_c), 'Third')
    # The recorded claim stops a second caller taking the same civ.
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
    # Cloud Storage refuses a signed URL request that also carries an
    # Authorization header, and `urllib` copies headers onto a redirect. The
    # emulator ignores the header, so only this check covers the handler.
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
    # A token callable that answers None, or no callable at all, sends no
    # Authorization header.
    check('a store with no token is refused by the relay rather than crashing',
          status_of(refusal(anonymous.state)), 401)
    empty = HttpTurnStore(base, token=lambda: None)
    check('and a token callable that answers None is the same thing',
          status_of(refusal(empty.state)), 401)

    print('what the relay carries and what it does not')
    # A submission is served from its document with the blob's ETag; a
    # matching If-None-Match gets 304 and no body.
    code, headers, body = call('GET', '/submission/8/DemoPlayer', token_a)
    check('a submission is served as its wire form, with its tag',
          (code, sp.decode_save(body), headers.get('ETag')),
          (200, MINE8, f'"{turn_store.submission_tag(MINE8)}"'))
    code, _h, body = relay.handle(
        'GET', f'/{galaxy}/submission/8/DemoPlayer',
        {'Authorization': f'Bearer {token_a}',
         'If-None-Match': headers['ETag']}, b'')
    check('asked with that tag, it is 304 and sends nothing', (code, body),
          (304, b''))
    code, _h, body = relay.handle(
        'GET', f'/{galaxy}/submission/8/DemoPlayer',
        {'Authorization': f'Bearer {token_a}',
         'If-None-Match': f'"{turn_store.submission_tag(MINE8_B)}"'}, b'')
    check('asked with another tag, it sends the bytes',
          (code, sp.decode_save(body) if code == 200 else None),
          (200, MINE8))
    # The turn blob grows with the galaxy, so it is served from Storage by
    # redirect.
    code, headers, body = call('GET', '/turn/8', token_a)
    check('a turn download is a redirect, not a body', code, 302)
    check('and carries no bytes of the blob', body, b'')
    check('and points at Cloud Storage rather than at the relay',
          headers['Location'].startswith(
              os.environ['STORAGE_EMULATOR_HOST'].rstrip('/')), True)

    run_joins(referee, galaxy, call, ids)


def run_catalog(referee, galaxy, root, token_a, token_c):
    """The galaxy listing route and the directory over it.

    A listing row may say only what `/state` may say about a galaxy.
    """
    def call(method, path, token=None):
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        return relay.handle(method, path, headers, b'')

    print('the listing, which is about no galaxy')
    check('a listing with no token is refused', call('GET', '/')[0], 401)
    check('and one with a token that is not a token is refused',
          call('GET', '/', 'not-a-token')[0], 401)
    code, _headers, body = call('GET', '/', token_a)
    check('a signed-in caller gets a listing', code, 200)
    rows = json.loads(body)['galaxies']
    mine = [r for r in rows if r['id'] == galaxy]
    check('the galaxy the referee started is in it', len(mine), 1)
    row = mine[0]
    # A row is built from the `/state` allowlist, so it carries no uid to civ
    # mapping.
    check('a row carries no seat map', 'seats' in row, False)
    check('and not the record of who was seated when',
          turn_store.JOINED_KEY in row, False)
    check('a row says which galaxy it is', row.get('id'), galaxy)
    check('and carries the turn the referee published',
          row.get('turn'), referee.state()['turn'])

    print('the directory a launcher holds over it')
    directory = galaxy_directory.open_directory(root, token=lambda: token_a)
    check('an https spec opens the HTTP directory',
          type(directory).__name__, 'HttpGalaxyDirectory')
    listed = {g.id: g for g in directory.galaxies(player='DemoPlayer')}
    check('it lists the galaxy', galaxy in listed, True)
    g = listed[galaxy]
    check('a row is joined for a player who holds a civ', g.joined, True)
    check('and not for one who does not',
          directory.galaxy(galaxy, player='Nobody').joined, False)
    check('players is how many civs the galaxy has',
          g.players, len(referee.state()['civs']))
    check('the row names a store back through this same relay',
          g.store, f'{root}/{galaxy}')
    # The store a row names is one the player can open.
    played = directory.store(galaxy)
    check('and that store reads the same turn the row showed',
          played.state()['turn'], g.turn)
    check('galaxy() answers None for one that is not there',
          directory.galaxy('no-such-galaxy'), None)

    print('what a player may not do to the listing')
    check('a POST to the listing is not a route',
          call('POST', '/', token_a)[0], 404)
    # Registering, opening and closing a galaxy are the operator's.
    check('registering through the relay is refused',
          type(refusal(directory.register, galaxy)).__name__,
          'NotImplementedError')
    check('and so is opening or closing a galaxy',
          type(refusal(directory.set_status, galaxy, 'closed')).__name__,
          'NotImplementedError')
    other = galaxy_directory.open_directory(root, token=lambda: token_c)
    check('another signed-in caller sees the same galaxies',
          sorted(r.id for r in other.galaxies()),
          sorted(r.id for r in directory.galaxies()))


def run_joins(referee, galaxy, call, ids):
    """Joins through the relay: the one request from a caller with no seat.

    Each check reads back through the referee's store.
    """
    uid_a, token_a = ids['a']
    uid_c, token_c = ids['c']
    uid_d, token_d = ids['d']

    def lodge(token, **fields):
        return call('POST', '/join', token, json.dumps(fields).encode('utf-8'))

    print('a join is the one request from somebody with no seat')
    code, _h, body = lodge(token_d, name='Newcomer', build='0.1.0+dev',
                           uid='somebody-elses-uid', requested_at=0.0)
    check('an unseated caller may lodge a join', code, 200)
    waiting = referee.join_requests()
    check('and the referee sees it waiting',
          [r['key'] for r in waiting], [uid_d])
    # A request filed under another player's uid would take that player's next
    # seat.
    check('filed under the uid in the token and not the one in the body',
          waiting[0]['uid'], uid_d)
    check('carrying the name that was asked for', waiting[0]['name'],
          'Newcomer')
    check('and the turn the galaxy is actually playing',
          waiting[0]['requested_turn'], referee.current()[0])
    # `requested_at` decides which of two joins gets the last free planet, so
    # the caller may not set it.
    check('and a clock the caller did not set',
          waiting[0]['requested_at'] > 1.0, True)
    # A join is granted by the worker on the authoritative blob; lodging one
    # binds nothing.
    check('lodging a join takes no seat',
          (referee.doc.get().to_dict().get('seats') or {}).get(uid_d), None)
    check('and adds nobody to the roster', referee.civs(),
          ['DemoPlayer', 'Neighbor', 'Third'])

    print('a request is read back by the caller that lodged it')
    check('the caller reads their own request',
          json.loads(call('GET', '/join', token_d)[2])['state'], 'waiting')
    # The route answers about the caller only, so it does not reveal who else
    # is asking to join.
    check('another caller sees nothing of it',
          json.loads(call('GET', '/join', token_a)[2])['state'], 'none')
    check('and may not ask for it by key',
          call('GET', f'/join/{uid_d}', token_a)[0], 403)
    check('the caller may ask for it by its own key',
          json.loads(call('GET', f'/join/{uid_d}', token_d)[2])['name'],
          'Newcomer')
    # The relay has no route listing waiting joins: that list would name
    # everyone trying to join.
    check('there is no route here that lists what is waiting',
          call('GET', '/joins', token_d)[0], 403)

    print('the worker answers a join, and nobody else does')
    code, _h, body = call('POST', f'/join/{uid_d}', token_d,
                          b'{"outcome": "granted"}')
    check('a caller may not answer its own request', code, 403)
    check('and says the worker does it at a boundary',
          'worker' in json.loads(body)['error'], True)
    check('and nothing was answered', referee.join_answer(uid_d), None)
    check('the request is still waiting',
          [r['key'] for r in referee.join_requests()], [uid_d])

    print('who may ask, and for what name')
    code, _h, body = lodge(token_a, name='Another')
    # A seated player lodging a join would be asking for a second empire.
    check('a caller already on the roster is refused', code, 409)
    check('and is told which seat it holds',
          'DemoPlayer' in json.loads(body)['error'], True)
    check('a name already seated is refused', lodge(token_d,
                                                    name='DemoPlayer')[0], 409)
    check('and so is the same name spelled differently',
          lodge(token_d, name='demoplayer')[0], 409)
    check('and the request that was waiting is untouched',
          referee.join_request(uid_d)['name'], 'Newcomer')
    lodge(token_d, name='Newcomer2')
    check('pressing Join again replaces rather than joining',
          [r['name'] for r in referee.join_requests()], ['Newcomer2'])

    print('a seat that was reclaimed is not a seat')
    # A reclaim leaves the uid in `seats`, so whether a caller may ask again is
    # decided by the roster.
    referee.update_state({'civs': ['DemoPlayer', 'Neighbor']})
    check('the reclaimed player may ask for a seat again',
          lodge(token_c, name='Third')[0], 200)
    check('and the referee sees both requests',
          sorted(r['key'] for r in referee.join_requests()),
          sorted([uid_c, uid_d]))
    # The seat map still names Third for this uid, as a reclaim leaves it. A
    # write from it is refused with the reclaim details.
    referee.update_state({turn_store.RECLAIMED_KEY: {
        'Third': {'turn': 8, 'missed': 12}}})
    now_turn = referee.current()[0]
    code, _h, body = call('POST', f'/submission/{now_turn}/Third', token_c,
                          sp.encode_save(make_blob(now_turn)))
    check('a reclaimed seat may not submit', code, 403)
    check('and is told it was reclaimed, when and after how many misses',
          all(w in body.decode('utf-8', 'replace')
              for w in ('reclaimed', 'turn 8', '12 missed', 'join again')),
          True)
    referee.update_state({turn_store.RECLAIMED_KEY: {}})
    referee.update_state({'civs': ['DemoPlayer', 'Neighbor', 'Third']})

    print('what a join request may not be')
    big = 'y' * (relay.MAX_JOIN_BYTES + 100)
    check('an oversized join request is refused',
          lodge(token_d, name='Newcomer2', build=big)[0], 413)
    check('one that is not JSON is refused',
          call('POST', '/join', token_d, b'not json at all')[0], 400)
    check('one that names nobody is refused', lodge(token_d, build='x')[0],
          400)
    check('one with no token at all is refused',
          call('POST', '/join', None, b'{"name": "Nobody"}')[0], 401)
    check('and none of them changed what is waiting',
          [r['name'] for r in referee.join_requests()
           if r['key'] == uid_d], ['Newcomer2'])

    print('a refused player is told, which is where J3 stopped')
    referee.answer_join(uid_d, {'key': uid_d, 'name': 'Newcomer2',
                                'outcome': 'refused',
                                'reason': 'this galaxy had no room'})
    out = json.loads(call('GET', '/join', token_d)[2])
    check('the answer reaches the player through the same door',
          out['state'], 'answered')
    # A refused player is on no roster and cannot read the worker's note, so
    # the reason arrives here.
    check('and carries the reason rather than only the outcome',
          out['answer']['reason'], 'this galaxy had no room')
    check('the request is no longer waiting',
          [r['key'] for r in referee.join_requests()], [uid_c])
    check('and another caller cannot read that answer',
          call('GET', f'/join/{uid_d}/answer', token_a)[0], 403)

    print('a refused player who asks again is waiting, not refused')
    # Answering a request consumes it, so a waiting request is always newer
    # than any answer on file and is reported first.
    lodge(token_d, name='Newcomer3')
    out = json.loads(call('GET', '/join', token_d)[2])
    check('the second request is what the player reads back',
          (out['state'], (out.get('request') or {}).get('name')),
          ('waiting', 'Newcomer3'))
    check('while the first answer is still on file',
          referee.join_answer(uid_d)['reason'], 'this galaxy had no room')
    referee.answer_join(uid_d, {'key': uid_d, 'name': 'Newcomer3',
                                'outcome': 'refused',
                                'reason': 'this galaxy had no room'})
    check('and once that one is answered, the answer is what it reads',
          json.loads(call('GET', '/join', token_d)[2])['state'], 'answered')

    print('the record of who was seated stays behind the door')
    referee.update_state({turn_store.JOINED_KEY: {
        'Third': {'turn': 8, 'uid': uid_c, 'planet': 6, 'system': 'Tau Ceti'}}})
    # `joined` holds the uid of everyone ever seated, so `/state` leaves it out
    # along with `seats`.
    state = json.loads(call('GET', '/state', token_d)[2])
    check('the seated record is not in the state a player reads',
          turn_store.JOINED_KEY in state, False)
    check('and neither is the seat map', 'seats' in state, False)
    check('while the referee holding the galaxy still reads it',
          list(referee.state().get(turn_store.JOINED_KEY) or {}), ['Third'])

    print('a closed galaxy takes no more joins')
    referee.close('the beta ended')
    code, _h, body = lodge(token_d, name='Latecomer')
    # The relay reaches the store with admin credentials, so the closed check
    # must be on the relay's route.
    check('a join into a closed galaxy is refused', code, 409)
    check('and says the galaxy is closed',
          'closed' in json.loads(body)['error'], True)
    check('and nothing was lodged',
          [r['key'] for r in referee.join_requests()], [uid_c])
    referee.reopen()


def ticket_url(call, token, turn, civ) -> str:
    """Where the upload ticket says a submission goes, as a launcher reads it.
    """
    ticket = json.loads(call('GET', f'/upload/submission/{turn}/{civ}',
                             token)[2])
    return ticket['url']


if __name__ == '__main__':
    sys.exit(main())
