"""
test_submission_path.py , a submission through the relay, since H13
====================================================================
    python functions/emulators.py --only firestore,storage,auth
    (set the three variables it prints)
    server\\.venv\\Scripts\\python.exe server\\tests\\test_submission_path.py

docs\\Public_Beta_Plan.md H13. A submission used to be a Cloud Storage object,
uploaded to a signed URL the relay handed out and checked by a commit call
afterwards. Each upload was a Class A operation against 5,000 a month for the
whole project, which capped the beta at about 27 players. Now the relay takes
the body itself and stores it as a Firestore document, one write against
20,000 a day.

What has to be true of that, each checked here:

  * **how many uploads a player makes**, counted through the real
    `player_turn.follow` on a fake clock at four-hour turns, because that
    number is what every figure in H13 is multiplied by
  * **the launchers already in players' hands still submit**, run as the
    `turn_store.py` those builds carry, taken out of git at the commit each
    was stamped with, against this relay
  * a read of a submission the launcher already holds is a 304 with no body,
    and one it does not hold is the bytes
  * the size caps: a body over the relay's cap, a body built to inflate, and
    a save too large for a document, each refused with nothing stored
  * a Storage upload made on a ticket handed out before the relay was
    redeployed is brought in by its commit, and one that is not a save is
    deleted
  * `publish` keeps `SUBMISSION_TURNS_KEPT` turns of submissions and deletes
    the rest

The old launchers need `git` to recover their `turn_store.py`; without it those
checks are skipped and say so.
"""
import base64
import json
import os
import struct
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'), os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'functions'), HERE):
    if d not in sys.path:
        sys.path.insert(0, d)

PROJECT = os.environ.get('CS_RELAY_PROJECT') or 'demo-cs-resurgence'
BUCKET = os.environ.get('CS_RELAY_BUCKET') or f'{PROJECT}.firebasestorage.app'
AUTH_HOST = os.environ.get('FIREBASE_AUTH_EMULATOR_HOST') or '127.0.0.1:9099'
PREFIX = f'subpath_{int(time.time())}'
os.environ['CS_RELAY_PROJECT'] = PROJECT
os.environ['CS_RELAY_BUCKET'] = BUCKET
os.environ['CS_RELAY_PREFIX'] = PREFIX
os.environ.setdefault('FIREBASE_AUTH_EMULATOR_HOST', AUTH_HOST)
# The deployed cap, whatever the shell carries: the bomb and the document cap
# below are sized against it.
os.environ.pop('CS_RELAY_MAX_BYTES', None)
# Every answer read fresh, because the referee writes and the relay is asked
# straight after. test_relay_costs.py covers the held copies.
os.environ['CS_RELAY_STATE_CEILING'] = '0'
os.environ['CS_RELAY_LISTING_SECONDS'] = '0'

import save_parser as sp                                        # noqa: E402
import turn_store                                               # noqa: E402
import firebase_store                                           # noqa: E402
from turn_store import HttpTurnStore                             # noqa: E402

PASS, FAIL, SKIP = [], [], []
TURN = 7
FOUR_HOURS = 14400

# The commits the two builds in players' hands were stamped with, read out of
# `build.json` inside each packaged launcher. Both stamps carry `+`, a dirty
# tree, so what is recovered is the committed file; `git diff` of
# `server/turn_store.py` between the two shows no change to `submit`.
OLD_BUILDS = (('v0.1.4', '7940532'), ('v0.1.5', '9b39f0d'))


def check(name, got, want):
    ok = want(got) if callable(want) else got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}: {got!r}')
    if not ok and not callable(want):
        print(f'          wanted {want!r}')


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


# ── how many uploads a player makes ──────────────────────────────────────────
def test_uploads_per_turn():
    """The count every H13 figure is multiplied by, at four-hour turns.

    Through the real `follow` with its real defaults, on the fake clock and
    fake client test_turn_cadence.py drives. Fails if the loop's cadence
    changes, which would make H13's arithmetic describe a loop that no longer
    exists.
    """
    print('uploads per player per turn, measured through follow')
    import test_turn_cadence as T
    T.PASS, T.FAIL = [], []
    cases = (
        ('idle all turn with the window open', {}, 1),
        ('played the first half hour, window left open',
         {'edits_at': tuple(range(60, 1800, 120))}, 1),
        ('played the first half hour, then closed the window',
         {'edits_at': tuple(range(60, 1800, 120)), 'close_at': 2100}, 1),
        ('played across the whole turn',
         {'edits_at': tuple(range(600, FOUR_HOURS, 600))}, 2),
        ('played across the turn and pressed Save three times',
         {'edits_at': tuple(range(600, FOUR_HOURS, 600)),
          'ask_at': (1000 + 1000, 1000 + 5000, 1000 + 9000)}, 4),
    )
    for name, kw, want in cases:
        r = T.run_turn(seconds=float(FOUR_HOURS), **kw)
        check(f'{name}: uploads', len(r.uploads), want)


def test_warned_event():
    """The loop names the warning part of a note, so a launcher need not.

    Checked against the note `abandonment` actually writes: refusals first,
    the warning appended. Fails if the warning were matched by a sentence
    that `warning_note` no longer opens with, or if refusals leaked into it.
    """
    print('the warning part of a note is its own event')
    import abandonment
    import player_turn
    refusals = ['system 193: rename DROPPED, not a majority']
    warning = abandonment.warning_note('DemoPlayer', 2, 4)
    events = []

    class Notes:
        def note(self, civ, turn):
            return refusals + warning

    player_turn.report_refusals(Notes(), 'DemoPlayer', 9, log=lambda *a: None,
                                emit=lambda kind, **f: events.append((kind, f)))
    kinds = [k for k, _f in events]
    check('refused_orders is still emitted, with every line',
          (kinds[:1], events[0][1].get('lines')),
          (['refused_orders'], refusals + warning))
    check('and warned carries the warning and nothing else',
          [f.get('lines') for k, f in events if k == 'warned'], [warning])
    check('a note with no warning emits no warned event',
          player_turn.warning_lines(refusals), [])
    check('a one-line warning is found too',
          player_turn.warning_lines(abandonment.warning_note('X', 1, 0)),
          abandonment.warning_note('X', 1, 0))


# ── the relay behind a socket ────────────────────────────────────────────────
class RelayHandler(BaseHTTPRequestHandler):
    """`relay.handle` behind a socket, as test_relay_function.py has it."""

    served = []

    def log_message(self, fmt, *args):
        pass

    def _run(self):
        import relay
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        path = urllib.parse.urlparse(self.path).path
        status, headers, out = relay.handle(
            self.command, path, dict(self.headers), body)
        RelayHandler.served.append((self.command, path, status, len(out)))
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = _run
    do_POST = _run


def sign_up():
    url = (f'http://{AUTH_HOST}/identitytoolkit.googleapis.com/v1/'
           f'accounts:signUp?key=fake-api-key')
    req = urllib.request.Request(
        url, data=json.dumps({'returnSecureToken': True}).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        out = json.loads(r.read())
    return out['localId'], out['idToken']


def status_of(fn, *a):
    try:
        fn(*a)
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception as exc:                                    # noqa: BLE001
        return type(exc).__name__
    return None


def old_turn_store(label, commit):
    """The `turn_store` module a shipped build carries, or None without git.

    Executed under its own name, so it keeps its own `HttpTurnStore` and its
    own helpers and shares nothing with the module under test but
    `save_parser`, which is what the build carries too.
    """
    try:
        src = subprocess.run(
            ['git', '-C', ROOT, 'show', f'{commit}:server/turn_store.py'],
            capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        SKIP.append(f'{label}: {exc}')
        return None
    mod = types.ModuleType(f'turn_store_{label.replace(".", "_")}')
    mod.__file__ = os.path.join(ROOT, 'server', 'turn_store.py')
    exec(compile(src, f'{commit}:server/turn_store.py', 'exec'), mod.__dict__)
    return mod


# ── the cases against the relay ──────────────────────────────────────────────
def test_old_launchers(referee, base, token):
    """The two builds in players' hands, against this relay.

    What they do is read the ticket's `url`, `method`, `encoding` and
    `commit`, send the body there, and commit only when a commit is named.
    Fails if the ticket's shape moved away from what they read, or if the
    route it names refused what they send.
    """
    print('launchers already shipped')
    turn = referee.current()[0]
    for label, commit in OLD_BUILDS:
        mod = old_turn_store(label, commit)
        if mod is None:
            print(f'  SKIP  {label}: git could not recover its turn_store.py')
            continue
        store = mod.HttpTurnStore(base, token=lambda: token)
        mine = make_blob(turn, label.encode())
        RelayHandler.served.clear()
        store.submit('DemoPlayer', turn, mine)
        check(f'{label}: its submission is what the referee reads',
              referee.submission('DemoPlayer', turn), mine)
        # Its own closed check, the ticket, and the POST the ticket names.
        # Before H13 the third was a PUT to Storage and a fourth, the commit,
        # came back here.
        check(f'{label}: in three relay requests and no commit',
              [(m, p.split('/')[2], s)
               for m, p, s, _n in RelayHandler.served],
              [('GET', 'state', 200), ('GET', 'upload', 200),
               ('POST', 'submission', 200)])
        check(f'{label}: its read back is the same bytes',
              store.submission('DemoPlayer', turn), mine)
        check(f'{label}: it is told it has submitted',
              store.has_submitted('DemoPlayer', turn), True)
        check(f'{label}: a body that is not a save raises for it',
              status_of(store.submit, 'DemoPlayer', turn,
                        make_blob(turn - 3)), 400)
        check(f'{label}: and leaves its submission standing',
              referee.submission('DemoPlayer', turn), mine)


def test_reads_by_tag(referee, base, token):
    """A read of bytes the launcher holds is 304; anything else is the bytes.

    Fails if the tag were ignored, which sends the whole submission on every
    read `player_turn` makes before and after an upload, or if a stale tag
    were answered 304, which would hide a submission written elsewhere.
    """
    print('reading a submission back by its tag')
    turn = referee.current()[0]
    player = HttpTurnStore(base, token=lambda: token, state_seconds=0)
    mine = make_blob(turn, b'm')
    player.submit('DemoPlayer', turn, mine)
    RelayHandler.served.clear()
    check('a read after an upload is those bytes',
          player.submission('DemoPlayer', turn), mine)
    check('and was answered 304 with no body',
          [(s, n) for _m, _p, s, n in RelayHandler.served], [(304, 0)])
    elsewhere = make_blob(turn, b'e')
    referee.submit('DemoPlayer', turn, elsewhere)
    RelayHandler.served.clear()
    check('a submission written elsewhere is read as itself',
          player.submission('DemoPlayer', turn), elsewhere)
    check('and was sent whole, not 304',
          [s for _m, _p, s, _n in RelayHandler.served], [200])
    RelayHandler.served.clear()
    player.submission('DemoPlayer', turn)
    check('and the tag it came with is used the next time',
          [s for _m, _p, s, _n in RelayHandler.served], [304])
    fresh = HttpTurnStore(base, token=lambda: token, state_seconds=0)
    check('a store holding nothing is sent the bytes',
          fresh.submission('DemoPlayer', turn), elsewhere)


def test_caps(referee, call, turn):
    """Each size cap refuses, and nothing is stored when it does."""
    import relay
    print('the size caps')
    standing = referee.submission('DemoPlayer', turn)
    over = b'A' * (relay.MAX_BYTES + 4)
    code, _h, _b = call('POST', f'/submission/{turn}/DemoPlayer', over)
    check('a body over the relay cap is refused', code, 413)
    # A few kilobytes of zlib that inflate past the store's decompressed cap,
    # with a size header that lies. Fails if the relay inflated it whole
    # before asking how big it was.
    bomb = base64.b64encode(
        struct.pack('<I', 100)
        + zlib.compress(b'\0' * (turn_store.MAX_DECODED_BYTES + 4096), 9))
    code, _h, _b = call('POST', f'/submission/{turn}/DemoPlayer', bomb)
    check('a body that inflates past the cap is refused as too large',
          (len(bomb) < relay.MAX_BYTES, code), (True, 413))
    # A real save whose compressed form is past what a document may hold,
    # sent raw so it is under the body cap. Fails if the store wrote it and
    # left Firestore to refuse, or if the relay turned it into a 500.
    size = firebase_store.MAX_SUBMISSION_DATA + 16 * 1024
    big = make_blob(turn, os.urandom(size), size=size)
    code, _h, body = call('POST', f'/submission/{turn}/DemoPlayer', big)
    check('a save too large for a document is refused as too large',
          (len(big) < relay.MAX_BYTES, code), (True, 413))
    check('and nothing any of them sent was stored',
          referee.submission('DemoPlayer', turn), standing)


def test_old_ticket_commit(referee, call, turn):
    """An upload made on a Storage ticket from before the redeploy.

    Fails if its commit left the upload as an object the referee no longer
    reads, or left a refused one in the bucket.
    """
    print('an upload on a ticket handed out before the redeploy')
    mine = make_blob(turn, b'k')
    name = referee.submission_object('DemoPlayer', turn)
    referee._put(name, sp.encode_save(mine), 'text/plain')
    code, _h, _b = call('POST', f'/commit/submission/{turn}/DemoPlayer', b'')
    check('its commit is taken', code, 200)
    check('and the upload is now the stored submission',
          referee.submission_record('DemoPlayer', turn) is not None
          and referee.submission('DemoPlayer', turn) == mine, True)
    check('and the object is gone', referee._has(name), False)
    referee._put(name, sp.encode_save(b'not a save'), 'text/plain')
    code, _h, _b = call('POST', f'/commit/submission/{turn}/DemoPlayer', b'')
    check('a commit of an upload that is not a save is refused', code, 400)
    check('and the object is deleted', referee._has(name), False)
    check('and the stored submission still stands',
          referee.submission('DemoPlayer', turn), mine)


def test_notes(referee, call):
    """A note is a document, read by the relay with no Storage operation.

    Fails if the note were still an object, which costs every launcher a
    Class B each turn whether or not there is a note, or if a note left
    before H13 could no longer be read.
    """
    print('notes are documents')
    turn = referee.current()[0]
    reasons = ['people DROPPED, 9 served and 10 came back']
    referee.put_note('DemoPlayer', turn, reasons)
    check('a note is written as a document',
          referee.note_doc('DemoPlayer', turn).get().exists, True)
    check('and not as an object',
          referee._has(referee.note_object('DemoPlayer', turn)), False)
    code, _h, body = call('GET', f'/note/{turn}/DemoPlayer')
    check('the relay serves it', (code, body.decode('utf-8')),
          (200, reasons[0]))
    check('a turn with no note is empty',
          referee.note('DemoPlayer', turn + 1), [])
    from google.cloud import firestore
    referee.doc.update(
        {firebase_store.SUBMISSIONS_FROM_KEY: firestore.DELETE_FIELD})
    referee._put(referee.note_object('Neighbor', turn), b'an old note',
                 'text/plain; charset=utf-8')
    check('a note left as an object before H13 is still read',
          referee.note('Neighbor', turn), ['an old note'])
    referee.doc.update({firebase_store.SUBMISSIONS_FROM_KEY: turn})


def test_pruning(referee):
    """`publish` keeps `SUBMISSION_TURNS_KEPT` turns and deletes the rest.

    Fails if nothing is deleted, which fills Firestore's 1 GiB at a hundred
    players within months, or if a kept turn goes, which breaks
    `referee --verify` for the turns it is meant to reach.
    """
    print('old submissions are deleted at publish')
    kept = firebase_store.SUBMISSION_TURNS_KEPT
    firebase_store.SUBMISSION_TURNS_KEPT = 3
    try:
        start = referee.current()[0]
        for turn in range(start, start + 5):
            referee.submit('DemoPlayer', turn, make_blob(turn, b'p'))
            referee.put_note('DemoPlayer', turn, [f'turn {turn}'])
            referee.publish(turn + 1, make_blob(turn + 1))
        last = start + 5
        have = sorted({(s.to_dict() or {}).get('turn')
                       for s in referee.submission_collection.stream()})
        check('after five publishes only the two turns before the one being '
              'played are kept, three with it',
              have, [last - 2, last - 1])
        notes = sorted({(s.to_dict() or {}).get('turn') for s in
                        referee.doc.collection(
                            firebase_store.NOTE_COLLECTION).stream()})
        check('and notes are kept for the same turns', notes,
              [last - 2, last - 1])
        check('the newest closed turn is still readable',
              referee.submission('DemoPlayer', last - 1),
              make_blob(last - 1, b'p'))
        check('and one past the window reads as nothing',
              referee.submission('DemoPlayer', last - 3), None)
    finally:
        firebase_store.SUBMISSION_TURNS_KEPT = kept


def main():
    test_uploads_per_turn()
    test_warned_event()
    if not os.environ.get('FIRESTORE_EMULATOR_HOST'):
        print('SKIPPED the relay half: no FIRESTORE_EMULATOR_HOST.')
    else:
        import relay
        referee = firebase_store.FirebaseTurnStore(
            PROJECT, 'g', bucket=BUCKET, prefix=PREFIX)
        referee.start(make_blob(TURN), ['DemoPlayer', 'Neighbor'],
                      turn_seconds=FOUR_HOURS)
        uid, token = sign_up()
        referee.doc.set({'seats': {uid: 'DemoPlayer'}}, merge=True)
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), RelayHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{httpd.server_address[1]}/g'

        def call(method, path, body=b''):
            return relay.handle(method, f'/g{path}',
                                {'Authorization': f'Bearer {token}'}, body)

        try:
            test_old_launchers(referee, base, token)
            test_reads_by_tag(referee, base, token)
            test_caps(referee, call, TURN)
            test_old_ticket_commit(referee, call, TURN)
            test_notes(referee, call)
            test_pruning(referee)
        finally:
            httpd.shutdown()
            httpd.server_close()
            referee.delete_everything()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed'
          + (f', {len(SKIP)} skipped' if SKIP else ''))
    for name in FAIL:
        print(f'  failed: {name}')
    for why in SKIP:
        print(f'  skipped: {why}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
