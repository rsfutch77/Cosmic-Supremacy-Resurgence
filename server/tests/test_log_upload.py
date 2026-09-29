"""
test_log_upload.py , a launcher's log reaches the operator and nobody else
==========================================================================
    python functions/emulators.py --only firestore,storage,auth
    (set the three variables it prints)
    server\\.venv\\Scripts\\python.exe server\\tests\\test_log_upload.py

docs\\Public_Beta_Plan.md M1. Done when a turn's log uploads under the cap, and
a failure the operator did not witness is diagnosed from the uploaded copy
alone. Checked here, with the relay in process on the Firestore and Auth
emulators and real anonymous sign-ins:

  * the relay and the operator's tool name the same collections and keep
    period, and the relay takes everything the launcher's cap lets through
  * `POST /_logs` needs a signed-in caller, stores the text it was sent under
    the caller's uid, and serves no log back to anyone
  * a body over the cap, a body built to inflate, and bodies that are not a
    log are each refused with nothing stored
  * one sign-in is held to its daily count, the project to its daily total,
    and the next day opens both again
  * an upload deletes a bounded batch of logs past the keep period
  * `log_tool.py` lists, filters by support code, prints and prunes
  * **the done-when**: the real `player_turn.follow` plays a turn against the
    relay with a client that ticked past its turn, every upload is refused,
    the launcher's own methods log it and offer to send, the player presses
    Send, and the operator's tool reads the stored copy back. The diagnosis
    is made from that text and nothing else.
"""
import base64
import io
import json
import os
import re
import struct
import sys
import tempfile
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
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools'),
          os.path.join(ROOT, 'functions'),
          os.path.join(ROOT, 'release')):
    if d not in sys.path:
        sys.path.insert(0, d)

PROJECT = os.environ.get('CS_RELAY_PROJECT') or 'demo-cs-resurgence'
BUCKET = os.environ.get('CS_RELAY_BUCKET') or f'{PROJECT}.firebasestorage.app'
AUTH_HOST = os.environ.get('FIREBASE_AUTH_EMULATOR_HOST') or '127.0.0.1:9099'
PREFIX = f'logs_{int(time.time())}'
# Set before `relay` is imported, because it reads them at import. The caps are
# small so that reaching them is a handful of uploads.
os.environ['CS_RELAY_PROJECT'] = PROJECT
os.environ['CS_RELAY_BUCKET'] = BUCKET
os.environ['CS_RELAY_PREFIX'] = PREFIX
os.environ['CS_RELAY_LOG_PER_UID'] = '3'
os.environ['CS_RELAY_LOG_PER_DAY'] = '5'
os.environ['CS_RELAY_STATE_CEILING'] = '0'
os.environ['CS_RELAY_LISTING_SECONDS'] = '0'
os.environ.setdefault('FIREBASE_AUTH_EMULATOR_HOST', AUTH_HOST)

import save_parser as sp                                        # noqa: E402
from firebase_store import FirebaseTurnStore                     # noqa: E402
from turn_store import HttpTurnStore                             # noqa: E402

PASS, FAIL = [], []
TURN = 7
SPEC = f'firebase://{PROJECT}?prefix={PREFIX}'


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          got {got!r}' + ('' if callable(want)
                                          else f', wanted {want!r}'))


def section(tag, payload, version=0):
    head = (len(payload) & sp.SIZE_MASK) | (version << sp.VERSION_SHIFT)
    return tag + struct.pack('<I', head) + payload


def make_blob(turn, filler=b'.', size=60):
    """A blob carrying a turn number, which is what `check_save` reads."""
    filler = (filler * size)[:size]
    return section(b'SAVE',
                   section(b'GLOB', struct.pack('<I', turn) + filler) +
                   section(b'KNPL', bytes([0xAA]) * 8))


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
        import relay
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


def post(root, path, body, token=None):
    """(status, reply) for one raw POST."""
    req = urllib.request.Request(root + path, data=body, method='POST',
                                 headers={'Content-Type': 'application/json'})
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b'{}')
        except ValueError:
            return exc.code, {}


def get(root, path, token=None):
    req = urllib.request.Request(root + path, method='GET')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def stored(fs, relay):
    """{id: record} of every log in the test's collection."""
    return {s.id: s.to_dict() for s in
            fs.collection(relay.LOG_COLLECTION).stream()}


def tool(*args):
    """(exit code, output) of log_tool.py run on this test's prefix."""
    import log_tool
    out = io.StringIO()
    code = log_tool.main([SPEC] + list(args), out=out)
    return code, out.getvalue()


# ── the agreements ───────────────────────────────────────────────────────────
def test_agreements(relay, L):
    print('the relay, the tool and the launcher agree')
    import log_tool
    check('the log collection', relay.LOG_COLLECTION,
          PREFIX + log_tool.LOG_SUFFIX)
    check('the daily counts', relay.LOG_QUOTA_COLLECTION,
          PREFIX + log_tool.LOG_QUOTA_SUFFIX)
    check('the keep period', relay.LOG_KEEP_DAYS, log_tool.LOG_KEEP_DAYS)
    check('the route', relay.LOG_ROUTE, L.LOG_ROUTE)
    # Fails if the relay refused a copy the launcher's own cap produced.
    check('the relay takes a copy at the launcher cap',
          relay.LOG_TEXT_MAX >= L.LOG_UPLOAD_CAP, True)
    check('the reasons the launcher sends are the relay\'s',
          {L.LOG_WHY_TURN, L.LOG_WHY_PLAYER} <= set(relay.LOG_REASONS), True)
    check('a log document fits under Firestore\'s 1 MiB',
          relay.LOG_BODY_MAX < 1024 * 1024 and relay.LOG_TEXT_MAX < 1024 * 1024,
          True)


# ── the route ────────────────────────────────────────────────────────────────
def test_route(relay, L, root, fs, ids):
    print('the route')
    uid_a, token_a = ids['a']
    uid_b, token_b = ids['b']
    text = ('[04:00:01] Cosmic Supremacy: Resurgence v0.1.6\n'
            '[04:00:02] multiplayer: turn 7 sent\n') * 20
    body = L.log_payload(text, '0.1.6', L.LOG_WHY_PLAYER, galaxy='sandbox',
                         civ='DemoPlayer', turn=7)

    code, reply = post(root, '/_logs', body)
    check('an unsigned upload is refused', code, 401)
    check('and stores nothing', stored(fs, relay), {})

    reply = L.send_log(root, lambda: token_a, body)
    got = stored(fs, relay)
    check('a signed-in upload is stored', list(got), [reply.get('id')])
    rec = got.get(reply.get('id')) or {}
    check('under the token\'s uid', rec.get('uid'), uid_a)
    check('with what the launcher said about it',
          (rec.get('why'), rec.get('build'), rec.get('galaxy'),
           rec.get('civ'), rec.get('turn')),
          ('player', '0.1.6', 'sandbox', 'DemoPlayer', 7))
    # Fails if the stored bytes were the upload's rather than text that
    # inflates back to exactly what was sent.
    check('and the text comes back exactly',
          zlib.decompress(bytes(rec.get('data') or b'')).decode('utf-8'),
          text)
    check('its size is the text\'s', rec.get('bytes'),
          len(text.encode('utf-8')))
    check('the id starts with when and the support code',
          reply.get('id'), lambda i: bool(i) and f'-{uid_a[:8]}-' in i)
    check('it can expire', rec.get('expire_at') is not None, True)

    # Nothing reads a log back through the relay, the sender included.
    check('GET of the collection is refused',
          get(root, '/_logs', token_a), 403)
    check('GET of the log itself is refused, even to its sender',
          get(root, f'/_logs/{reply["id"]}', token_a), 403)
    check('and to anyone else', get(root, f'/_logs/{reply["id"]}', token_b),
          403)
    check('a POST under the collection is refused',
          post(root, f'/_logs/{reply["id"]}', body, token_b)[0], 403)

    before = stored(fs, relay)
    big = b'{"log": "' + b'A' * (relay.LOG_BODY_MAX) + b'"}'
    check('a body over the cap is refused 413',
          post(root, '/_logs', big, token_b)[0], 413)
    bomb = base64.b64encode(zlib.compress(b'x' * (4 * relay.LOG_TEXT_MAX), 9))
    code, said = post(root, '/_logs', json.dumps(
        {'log': bomb.decode(), 'why': 'player'}).encode(), token_b)
    # Fails if the text were inflated whole before its size was looked at:
    # the body is small, so only the inflate cap refuses it.
    check('a small body that inflates past the cap is refused 413',
          (code, len(bomb) < relay.LOG_BODY_MAX), (413, True))
    check('not JSON is refused 400',
          post(root, '/_logs', b'not json', token_b)[0], 400)
    check('JSON with no log is refused 400',
          post(root, '/_logs', b'{"text": "hi"}', token_b)[0], 400)
    check('a log that is not zlib is refused 400',
          post(root, '/_logs', json.dumps(
              {'log': base64.b64encode(b'plain').decode()}).encode(),
              token_b)[0], 400)
    cut = base64.b64encode(zlib.compress(text.encode(), 6)[:-8]).decode()
    check('a zlib stream cut short is refused 400',
          post(root, '/_logs', json.dumps({'log': cut}).encode(),
               token_b)[0], 400)
    check('none of those stored anything', stored(fs, relay), before)

    odd = json.dumps({'log': base64.b64encode(zlib.compress(b'x')).decode(),
                      'why': 'because', 'galaxy': 'g' * 500, 'turn': '12',
                      'civ': None}).encode()
    reply = L.send_log(root, lambda: token_b, odd)
    rec = stored(fs, relay).get(reply.get('id')) or {}
    check('an unknown reason is stored as other', rec.get('why'), 'other')
    check('a long field is cut to the cap', len(rec.get('galaxy') or ''),
          relay.LOG_FIELD_CHARS)
    check('a turn that is not a number is dropped', rec.get('turn'), None)


def test_caps(relay, L, root, fs, ids):
    print('the daily caps')
    uid_a, token_a = ids['a']
    _uid_b, token_b = ids['b']
    uid_c, token_c = ids['c']
    body = L.log_payload('one line\n', '0.1.6', L.LOG_WHY_PLAYER)
    # a sent one in test_route, b one; the per-uid cap is 3 and the day's 5.
    sent = [post(root, '/_logs', body, token_a)[0] for _ in range(2)]
    check('a sign-in sends up to its daily count', sent, [200, 200])
    code, said = post(root, '/_logs', body, token_a)
    check('and is refused the next with a sentence',
          (code, 'most one may send in a day' in said.get('error', '')),
          (429, True))
    check('the count is the uid\'s',
          sum(1 for r in stored(fs, relay).values() if r['uid'] == uid_a), 3)
    check('another sign-in is not held to it',
          post(root, '/_logs', body, token_b)[0], 200)
    before = stored(fs, relay)
    check('the day\'s total is now reached', len(before), 5)
    code, said = post(root, '/_logs', body, token_c)
    # Fails if the daily total were not checked: c has sent nothing.
    check('a fresh sign-in is refused once the day is full',
          (code, 'as many logs today' in said.get('error', '')), (429, True))
    check('and nothing was stored', stored(fs, relay), before)

    real = relay._wall
    start = real()
    try:
        relay._wall = lambda: start + 86400
        check('the next day takes it', post(root, '/_logs', body, token_c)[0],
              200)
        check('and a sign-in capped yesterday again',
              post(root, '/_logs', body, token_a)[0], 200)

        # Fifteen days and an hour on, the first two days are past the keep
        # period: seven logs, more than one upload's batch deletes.
        now = start + 15 * 86400 + 3600
        cutoff = now - relay.LOG_KEEP_DAYS * 86400
        relay._wall = lambda: now
        old = [i for i, r in stored(fs, relay).items()
               if r['received_at'] < cutoff]
        check('seven logs are past the keep period', len(old), 7)
        check('an upload fifteen days on is taken',
              post(root, '/_logs', body, token_c)[0], 200)
        left = [i for i, r in stored(fs, relay).items()
                if r['received_at'] < cutoff]
        # Fails if the batch were unbounded (0 left) or never ran (7 left).
        check('it deleted a batch of five of them', len(left), 2)
        check('and kept the new one', len(stored(fs, relay)), 3)
    finally:
        relay._wall = real


def test_tool(relay, fs, ids):
    print('the operator\'s tool')
    uid_a, _ = ids['a']
    uid_c, _ = ids['c']
    code, out = tool('list')
    lines = out.strip().splitlines()
    check('list answers', code, 0)
    check('one line a log', len(lines), len(stored(fs, relay)))
    check('newest first', lines[0].split()[0] > lines[-1].split()[0], True)
    check('each names a support code', all(' code ' in x for x in lines),
          True)
    code, out = tool('list', '--code', uid_c[:8])
    check('--code keeps one sign-in\'s logs',
          out.strip().splitlines(),
          lambda ls: len(ls) >= 1 and all(f'code {uid_c[:8]}' in x
                                          for x in ls))
    code, out = tool('list', '--code', 'zzzzzzzz')
    check('a code with no logs says so', out.strip(),
          'no logs from support code zzzzzzzz')
    code, out = tool('show', 'no-such-log')
    check('show of a missing log is refused', (code, 'refused' in out),
          (2, True))
    code, out = tool('prune', '--dry-run', '--days', '0')
    check('prune --dry-run counts and deletes nothing',
          (code, 'would delete' in out, len(stored(fs, relay)) > 0),
          (0, True, True))


def test_prune_all(relay, fs):
    # The logs above were stamped up to fifteen days ahead of the real clock,
    # so a period of minus thirty days is what makes every one of them old.
    code, out = tool('prune', '--days=-30')
    check('prune removes logs past the period', (code, stored(fs, relay)),
          (0, {}))
    check('and the daily counts with them',
          list(fs.collection(relay.LOG_QUOTA_COLLECTION).stream()), [])


# ── the done-when ────────────────────────────────────────────────────────────
class FakeClock:
    def __init__(self):
        self.t = time.time()

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.05, s)


def launcher_app(L, data_dir, store, spec, galaxy, token):
    """The launcher's turn-loop and log methods, without a window."""
    app = types.SimpleNamespace()
    app.data_dir = data_dir
    app.games_dir = None
    app.mp_store = store
    app.mp_civ = 'DemoPlayer'
    app.mp_turn = None
    app.mp_note = ''
    app.mp_deadline = None
    app.mp_turn_seconds = 60
    app.mp_waiting = False
    app.mp_capture = None
    app.mp_playing = {'id': galaxy, 'name': galaxy, 'store': spec}
    app.mp_reopening = False
    app.mp_sending = False
    app.mp_turn_open = False
    app.mp_stop = False
    app.mp_closed = False
    app.mp_probed = None
    app.mp_log_offered = False
    app.log_sending = False
    app.msgs = __import__('queue').Queue()
    app._pending, app._logfh = [], None
    app.warned, app.dialogs, app.opened = [], [], []
    app.choose = ['send']

    def say(line):
        L.Launcher._persist(app, line)
    app.say = say
    app.say_threadsafe = app.msgs.put
    app.warn = app.warned.append

    def dialog(title, body, choices):
        app.dialogs.append((title, body))
        return app.choose.pop(0) if app.choose else None
    app._choice_dialog = dialog
    app._open_file = app.opened.append
    for name in ('_mp_state', '_mp_probe_closed', '_on_log_offer',
                 'offer_log', '_on_log_sent', '_on_mp_closed'):
        setattr(app, name, getattr(L.Launcher, name).__get__(app))
    app.refresh_games = lambda: None

    def drain(wait=0.0, until=None):
        """What the Tk thread's queue would hand on, handed on. Waits up to
        `wait` seconds for a message of kind `until`."""
        seen = []
        end = time.time() + wait
        while True:
            if until is not None and until in seen:
                return seen
            try:
                msg = app.msgs.get(timeout=0.05)
            except Exception:                               # noqa: BLE001
                if time.time() >= end:
                    return seen
                continue
            if isinstance(msg, tuple):
                seen.append(msg[0])
                if msg[0] == '__log_offer__':
                    app._on_log_offer(msg[1])
                elif msg[0] == '__log_sent__':
                    app._on_log_sent(*msg[1:])
                elif msg[0] == '__mp_closed__':
                    app._on_mp_closed(msg[1], msg[2])
            else:
                app.say(msg)
    app.drain = drain
    L.Launcher._open_log(app, data_dir)
    return app


def diagnose(text: str) -> dict:
    """What went wrong, read from an uploaded log and from nothing else.

    What the operator does with `log_tool.py show`: find the build, the turn
    that was lost, and the reason it gives.
    """
    out = {}
    m = re.search(r'Cosmic Supremacy: Resurgence v(\S+)', text)
    out['build'] = m.group(1) if m else None
    m = re.search(r'\[([^\]]+)\] turn (\d+): LOST, (.+)', text)
    if m:
        out['civ'], out['turn'], out['why'] = m.group(1), int(m.group(2)), \
            m.group(3)
    m = re.search(r'interim submit failed, (.+)', text)
    out['interim'] = m.group(1) if m else None
    return out


def test_done_when(relay, L, root, ids):
    print('the done-when: a turn nobody watched fail, diagnosed from the log')
    import player_turn
    import game_cycle
    uid, token = ids['d']
    galaxy = f'logturn_{int(time.time())}'
    referee = FirebaseTurnStore(PROJECT, galaxy, bucket=BUCKET, prefix=PREFIX)
    referee.start(make_blob(TURN), ['DemoPlayer', 'Other'], turn_seconds=60)
    referee.doc.set({'seats': {uid: 'DemoPlayer'}}, merge=True)
    spec = f'{root}/{galaxy}'
    store = HttpTurnStore(spec, token=lambda: token)

    data_dir = tempfile.mkdtemp(prefix='logturn_')
    with open(os.path.join(data_dir, L.MP_CONFIG), 'w') as f:
        json.dump({'store': spec, 'auth': True}, f)
    app = launcher_app(L, data_dir, store, spec, galaxy, token)
    app.say('Cosmic Supremacy: Resurgence v0.1.6')
    app.say(f'data    {data_dir}')
    # One save request's body as cs_server logs it, which redaction removes.
    app.say("  body: userid=1&gamename='DemoPlayt7'&turn=7&version=1&data="
            + 'Q' * 300)

    # The client ticked past its turn, which the module docstring of
    # player_turn describes: every capture is a save of turn 8.
    ticked = make_blob(TURN + 1, b't')
    clock = FakeClock()
    saved = (player_turn.serve, player_turn.collect, player_turn.close,
             player_turn.sp, game_cycle.client_pids)
    player_turn.serve = lambda *a, **k: 'x.dat'
    player_turn.collect = lambda *a, **k: ticked
    player_turn.close = lambda: None
    player_turn.sp = types.SimpleNamespace(load_any=lambda b: b)
    game_cycle.client_pids = lambda: [4242]
    real_token = L.player_token
    L.player_token = lambda d: (lambda: token)
    try:
        states = []

        def on_state(kind, **facts):
            states.append(kind)
            app._mp_state(kind, **facts)

        player_turn.follow(store, 'DemoPlayer', poll=2.0, rounds=1,
                           on_state=on_state, log=app.say_threadsafe,
                           clock=clock.now, sleep=clock.sleep)
        kinds = app.drain()
        check('the turn was lost', 'lost' in states and 'submitted'
              not in states, True)
        check('and the player was offered their log once',
              kinds.count('__log_offer__'), 1)
        check('the box said what goes and that nothing goes unasked',
              app.dialogs, lambda d: len(d) >= 1
              and d[0][0] == L.LOG_SEND_TITLE
              and 'Windows account name is taken out' in d[0][1]
              and 'Nothing is sent unless you press Send' in d[0][1]
              and d[0][1].startswith('Something went wrong with your turn'))
        kinds += app.drain(wait=30, until='__log_sent__')
        check('the player pressed Send and it went',
              '__log_sent__' in kinds and not app.warned, True)
        box = app.dialogs[-1] if app.dialogs else ('', '')
        ref = re.search(r'^\s+(\S+)$', box[1], re.M)
        check('they were given a reference', box[0] == 'Log sent' and ref,
              lambda v: bool(v))
    finally:
        (player_turn.serve, player_turn.collect, player_turn.close,
         player_turn.sp, game_cycle.client_pids) = saved
        L.player_token = real_token
        if app._logfh:
            app._logfh.close()
        referee.delete_everything()

    # The operator, later, with only the uploaded copy.
    log_id = ref.group(1) if ref else ''
    code, out = tool('list', '--code', uid[:8])
    check('the operator finds it by the player\'s support code',
          out, lambda o: log_id in o and 'turn_failed' in o
          and f'{galaxy}/DemoPlayer' in o)
    code, out = tool('show', log_id)
    text = out.split('\n', 1)[1] if '\n' in out else ''
    check('under the cap', len(text.encode('utf-8')) <= L.LOG_UPLOAD_CAP,
          True)
    found = diagnose(text)
    print(f'          diagnosis: {found}')
    check('it names the build', found.get('build'), '0.1.6')
    check('the turn and civ that were lost', (found.get('civ'),
                                               found.get('turn')),
          ('DemoPlayer', TURN))
    # Fails without `player_turn.failure_text`: the log then says only
    # "HTTP Error 400: Bad Request", which diagnoses nothing.
    check('and why: the save was of another turn',
          found.get('why') or '',
          lambda w: f'is not a save of turn {TURN}' in w)
    check('the interim upload says the same',
          found.get('interim') or '',
          lambda w: f'is not a save of turn {TURN}' in w)
    account = os.path.basename(os.path.expanduser('~'))
    check('the Windows account name is not in it',
          account.lower() in text.lower(), False)
    check('nor the save data', 'Q' * 50 in text, False)
    check('but the elided body is counted', 'bytes of request body elided'
          in text, True)


def main():
    if not os.environ.get('FIRESTORE_EMULATOR_HOST'):
        print('SKIPPED: no FIRESTORE_EMULATOR_HOST. This run says nothing '
              'about the log upload.')
        return 0
    import relay
    import launcher as L
    fs = relay.store_for(relay.CATALOG).fs
    ids = {k: sign_up() for k in 'abcd'}

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), RelayHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    root = f'http://127.0.0.1:{httpd.server_address[1]}'
    try:
        test_agreements(relay, L)
        test_route(relay, L, root, fs, ids)
        test_caps(relay, L, root, fs, ids)
        test_tool(relay, fs, ids)
        test_prune_all(relay, fs)
        # The done-when runs on a fresh day's count.
        for snap in fs.collection(relay.LOG_QUOTA_COLLECTION).stream():
            snap.reference.delete()
        test_done_when(relay, L, root, ids)
    finally:
        httpd.shutdown()
        httpd.server_close()
        for name in (relay.LOG_COLLECTION, relay.LOG_QUOTA_COLLECTION):
            for snap in fs.collection(name).stream():
                snap.reference.delete()

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for name in FAIL:
        print(f'  failed: {name}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
