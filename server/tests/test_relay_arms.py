"""
test_relay_arms.py: coats of arms go through the relay and wait for approval
============================================================================
    python functions/emulators.py --only firestore,storage,auth

    set FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
    set STORAGE_EMULATOR_HOST=http://127.0.0.1:9199
    set FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
    server\\.venv\\Scripts\\python.exe server\\tests\\test_relay_arms.py

The path a coat of arms takes: the dialog's `uploadcoa`, `listcoa`, `getcoa`
and `getapprovedcoa` reach `cs_server.py`, which sends them to the galaxy's
relay while the launcher plays multiplayer. The relay numbers each upload
across the project and keeps it pending. Its owner sees it, another player
is answered `FAIL` until `arms_tool.py` approves it, and the referee lets a
civ choose only its own uploads that were not refused.
"""
import base64
import os
import struct
import sys
import threading
import time
import urllib.parse
import zlib
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'server', 'tests'),
          os.path.join(ROOT, 'functions')):
    if d not in sys.path:
        sys.path.insert(0, d)

# cs_server keeps its log and uploads under CS_DATA_DIR, read at import.
import tempfile                                                 # noqa: E402
os.environ['CS_DATA_DIR'] = tempfile.mkdtemp(prefix='cs_arms_')

import test_relay_function as trf                               # noqa: E402

import arms_tool                                                # noqa: E402
import cs_server                                                # noqa: E402
import firebase_store                                           # noqa: E402
import referee                                                  # noqa: E402
import relay                                                    # noqa: E402
from firebase_store import FirebaseTurnStore                     # noqa: E402
from turn_store import HttpTurnStore                             # noqa: E402

PASS, FAIL = trf.PASS, trf.FAIL
check = trf.check
PROJECT, BUCKET = trf.PROJECT, trf.BUCKET


def png(width=56, height=66, shade=0):
    """A real PNG of one colour."""
    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))
    rows = b''.join(b'\x00' + bytes([shade, 0, 0]) * width
                    for _ in range(height))
    return (relay.PNG_SIGNATURE
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0,
                                         0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def account(uid):
    """A Google sign-in's token, which may upload."""
    return trf.craft_token(sub=uid, user_id=uid,
                           firebase={'sign_in_provider': 'google.com'})


def asked(action, **params):
    """What cs_server answers the client for one coat-of-arms action."""
    query = urllib.parse.urlencode(dict(action=action, userid=1, **params))
    status, _ctype, body = cs_server.handle_action(
        action, urllib.parse.parse_qs(query), '')
    return status, body


def run_names():
    """The civ name dialog in multiplayer, which needs no relay."""
    print('the civ name dialog in multiplayer')
    cs_server.MP_STORE = lambda: object()
    try:
        check('no names are offered', asked('listcivnames'), (200, ''))
        check('a chosen name is answered OK',
              asked('uploadcivname', civname="'Star Kingdom'"), (200, 'OK'))
        check('and not kept for single player',
              cs_server._get_civ('1')['civname'] != 'Star Kingdom')
    finally:
        cs_server.MP_STORE = None
    check('single player still offers its stored name',
          asked('listcivnames')[1].startswith(
              cs_server._get_civ('1')['civname'] + '#SPC#'))


def main():
    run_names()
    if not os.environ.get('FIRESTORE_EMULATOR_HOST'):
        print('SKIPPED: no FIRESTORE_EMULATOR_HOST. This run says nothing '
              'about coats of arms.')
        print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
        return 1 if FAIL else 0

    stamp = int(time.time())
    galaxy = f'arms_{stamp}'
    store = FirebaseTurnStore(PROJECT, galaxy, bucket=BUCKET)
    store.start(trf.BLOB7, ['DemoPlayer', 'Neighbor', 'Gone'],
                turn_seconds=1800)
    a, b, c, gone = (f'a{stamp}', f'b{stamp}', f'c{stamp}', f'g{stamp}')
    store.doc.set({'seats': {a: 'DemoPlayer', b: 'Neighbor', gone: 'Lost'}},
                  merge=True)

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), trf.RelayHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}/{galaxy}'
    player_a = HttpTurnStore(base, token=lambda: account(a))
    player_b = HttpTurnStore(base, token=lambda: account(b))
    fs, prefix = store.fs, store.prefix
    made = []
    try:
        run(store, galaxy, player_a, player_b, account, made, (a, b, c, gone))
    finally:
        cs_server.MP_STORE = None
        httpd.shutdown()
        httpd.server_close()
        for n in made:
            firebase_store.arms_collection(fs, prefix).document(
                str(n)).delete()
        for uid in (a, b, c, gone):
            firebase_store.arms_owner_doc(fs, prefix, uid).delete()
        print(f'  removed {store.delete_everything()} object(s) and '
              f'document(s) from {galaxy}, and {len(made)} upload(s)')

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for name in FAIL:
        print(f'  failed: {name}')
    return 1 if FAIL else 0


def run(store, galaxy, player_a, player_b, account, made, uids):
    a, b, c, gone = uids
    fs, prefix = store.fs, store.prefix

    def call(method, path, token=None, body=b''):
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        return relay.handle(method, f'/{galaxy}{path}', headers, body)

    def upload(token, data):
        return call('POST', '/arms', token, body=(
            b'{"png": "' + base64.b64encode(data) + b'"}'))

    print('who may upload')
    guest = trf.craft_token(sub=c, user_id=c,
                            firebase={'sign_in_provider': 'anonymous'})
    check('a guest is refused', upload(guest, png())[0], 403)
    unverified = trf.craft_token(sub=c, user_id=c, email_verified=False,
                                 firebase={'sign_in_provider': 'password'})
    check('an unverified email is refused', upload(unverified, png())[0], 403)
    check('an account with no seat here is refused',
          upload(account(c), png())[0], 403)
    check('a reclaimed seat is refused', upload(account(gone), png())[0], 403)
    check('no token is refused', upload(None, png())[0], 401)

    print('what may be uploaded')
    for name, body in (
            ('not JSON', b'nope'),
            ('not base64', b'{"png": "!!"}'),
            ('not a PNG', b'{"png": "' + base64.b64encode(b'GIF89a' * 9)
             + b'"}'),
            ('too wide', b'{"png": "' + base64.b64encode(png(257, 10))
             + b'"}')):
        check(f'{name} is refused', call('POST', '/arms', account(a),
                                         body)[0], 400)
    check('too large is refused', upload(account(a), relay.PNG_SIGNATURE
                                         + b'\0' * relay.ARMS_MAX_BYTES)[0],
          413)
    check('nothing was kept', player_a.list_arms(), [])

    print('an upload is numbered across the project and kept pending')
    cs_server.MP_STORE = lambda: player_a
    red = png(shade=200)
    status, first = asked('uploadcoa', data=base64.b64encode(red).decode())
    first = int(first)
    made.append(first)
    check('cs_server answers the relay\'s id', status == 200 and first > 0)
    second = player_a.upload_arms(png(shade=10))
    made.append(second)
    check('the next upload gets the next id', second, first + 1)
    check('the owner lists both', asked('listcoa'), (200, f'{first} {second}'))
    check('the owner sees a pending upload',
          asked('getcoa', coaid=first), (200, base64.b64encode(red).decode()))
    rec = arms_tool.arms_record(fs, prefix, first)
    check('it is pending and names the galaxy and civ',
          (rec['status'], rec['galaxy'], rec['civ'], rec['width'],
           rec['height']), ('pending', galaxy, 'DemoPlayer', 56, 66))
    check('the operator lists it as waiting',
          [r['id'] for r in arms_tool.list_arms(fs, prefix)
           if r['galaxy'] == galaxy], [first, second])

    print('another player sees it only once it is approved')
    cs_server.MP_STORE = lambda: player_b
    check('pending, another client is answered FAIL',
          asked('getapprovedcoa', coaid=first), (200, 'FAIL'))
    check('another player lists none of it', asked('listcoa'), (200, ''))
    check('approving says what it was',
          arms_tool.decide(fs, prefix, first, 'approved'), 'pending')
    check('approved, the same request is answered with the image',
          asked('getapprovedcoa', coaid=first),
          (200, base64.b64encode(red).decode()))
    check('an unknown id is FAIL', asked('getapprovedcoa', coaid=999999),
          (200, 'FAIL'))
    check('a malformed id is FAIL', asked('getapprovedcoa', coaid='x'),
          (200, 'FAIL'))

    print('the referee lets a civ choose its own uploads that were not refused')
    check('each civ may choose its own',
          referee.arms_of(store, ['DemoPlayer', 'Neighbor'],
                          log=lambda *_: None),
          {'DemoPlayer': [first, second]})
    arms_tool.decide(fs, prefix, second, 'refused')
    check('a refused upload is no longer listed',
          player_a.list_arms(), [first])
    check('nor offered to the referee', store.arms(),
          {'DemoPlayer': {first}})
    check('nor shown to its owner', player_a.arms_png(second), None)
    arms_tool.decide(fs, prefix, second, 'approved')
    check('a refusal can be reversed', store.arms(),
          {'DemoPlayer': {first, second}})
    check('a store that keeps no arms gives none',
          referee.arms_of(object(), ['DemoPlayer']), {})

    print('a galaxy without a relay offers none')

    class Folder:
        pass

    cs_server.MP_STORE = lambda: Folder()
    check('uploading is refused', asked(
        'uploadcoa', data=base64.b64encode(red).decode()), (200, '-1'))
    check('nothing is listed', asked('listcoa'), (200, ''))
    check('nothing is shown', asked('getcoa', coaid=first), (200, 'FAIL'))

    print('one sign-in uploads at most ARMS_PER_UID')
    cap, relay.ARMS_PER_UID = relay.ARMS_PER_UID, 3
    try:
        third = player_a.upload_arms(png(shade=30))
        made.append(third)
        check('a fourth upload is refused',
              upload(account(a), png())[0], 429)
    finally:
        relay.ARMS_PER_UID = cap


if __name__ == '__main__':
    sys.exit(main())
