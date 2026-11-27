"""
test_data_request.py: what data_request.py finds for one player
===============================================================
    python functions/emulators.py --only firestore,storage,auth
    (set the three variables it prints)
    server\\.venv\\Scripts\\python.exe server\\tests\\test_data_request.py

Seeds one player across two galaxies, a log and a coat of arms in a collection
of its own, then asks for them by player code and by uid. The rules that need
no project run first.
"""
import io
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import data_request                                             # noqa: E402
import firebase_store                                           # noqa: E402

PROJECT = os.environ.get('CS_RELAY_PROJECT') or 'demo-cs-resurgence'
AUTH_HOST = os.environ.get('FIREBASE_AUTH_EMULATOR_HOST')
PREFIX = f'datareq_{int(time.time())}'
SPEC = f'firebase://{PROJECT}?prefix={PREFIX}'

PASS, FAIL = [], []


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def test_rules():
    print('the rules')
    doc = {'seats': {'abcd1234XYZ': 'Baron', 'other999': 'Duke'},
           'seat_providers': {'abcd1234XYZ': 'anonymous'},
           'seats_rebound': {'Baron': {'from': 'old0000', 'to': 'abcd1234XYZ'}},
           'civs': ['Baron', 'Duke']}
    found = dict(data_request.mentions(doc, 'abcd1234XYZ'))
    check('a seat is found by its key', found.get('seats.abcd1234XYZ'), 'Baron')
    check('a provider is found by its key',
          found.get('seat_providers.abcd1234XYZ'), 'anonymous')
    check('a seat move is found by its value',
          found.get('seats_rebound.Baron.to'), 'abcd1234XYZ')
    check('another player is not', 'seats.other999' in found, False)
    check('a code finds a uid seen only in a galaxy',
          data_request.uids_for('abcd1234', [], {'g': doc}, []),
          ['abcd1234XYZ'])
    check('or only in a log',
          data_request.uids_for('zzzz0000', [], {}, ['zzzz0000Q', None]),
          ['zzzz0000Q'])
    check('a code is case-sensitive',
          data_request.uids_for('ABCD1234', ['abcd1234XYZ'], {'g': doc}, []),
          [])
    for bad in ('', 'ab', 'abcd/123', 'abcd.1234'):
        try:
            data_request.find(None, code=bad)
            check(f'{bad!r} is refused as a code', 'answered', 'refused')
        except data_request.RequestRefused:
            check(f'{bad!r} is refused as a code', 'refused', 'refused')


def sign_up():
    url = (f'http://{AUTH_HOST}/identitytoolkit.googleapis.com/v1/'
           f'accounts:signUp?key=fake-api-key')
    req = urllib.request.Request(
        url, data=json.dumps({'returnSecureToken': True}).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())['localId']


def test_project():
    print('one player in the emulator')
    uid = sign_up()
    stranger = sign_up()
    store = data_request.open_project(SPEC)
    fs, prefix = store.fs, store.prefix
    galaxies = fs.collection(prefix)
    galaxies.document('galaxyone').set({
        'civs': ['Baron', 'Duke'], 'seats': {uid: 'Duke', stranger: 'Baron'},
        'seat_providers': {uid: 'anonymous'}})
    galaxies.document('sandbox').set({'civs': ['Baron'],
                                      'seats': {stranger: 'Baron'}})
    key = firebase_store.join_doc_id(uid)
    galaxies.document('sandbox').collection(
        firebase_store.JOIN_COLLECTION).document(key).set(
        {'uid': uid, 'name': 'Duke', 'requested_at': time.time()})
    fs.collection(prefix + '_logs').document('log1').set(
        {'uid': uid, 'received_at': time.time(), 'why': 'data request',
         'build': '0.8.1', 'bytes': 10})
    fs.collection(prefix + '_logs').document('log2').set(
        {'uid': stranger, 'received_at': time.time(), 'why': 'bug',
         'build': '0.8.1', 'bytes': 10})
    firebase_store.arms_owner_doc(fs, prefix, uid).set({'ids': [7]})
    firebase_store.arms_collection(fs, prefix).document('7').set(
        {'id': 7, 'uid': uid, 'galaxy': 'galaxyone', 'civ': 'Duke',
         'status': 'pending', 'png': b'x', 'uploaded_at': time.time()})

    answer = data_request.find(store, code=uid[:8])
    check('the code finds one uid', [a['uid'] for a in answer['uids']], [uid])
    a = answer['uids'][0]
    check('its Auth record', (a['auth'] or {}).get('localId'), uid)
    check('the galaxies that hold it',
          [g['galaxy'] for g in a['galaxies']], ['galaxyone', 'sandbox'])
    check('its civ', a['galaxies'][0]['civ'], 'Duke')
    check('its join request', a['galaxies'][1]['join_request']['name'], 'Duke')
    check('its log and not the stranger\'s',
          [r['id'] for r in a['logs']], ['log1'])
    check('its coat of arms, without the image',
          a['arms']['uploads'], [{'id': 7, 'galaxy': 'galaxyone',
                                  'civ': 'Duke', 'status': 'pending',
                                  'uploaded_at': a['arms']['uploads'][0]
                                  .get('uploaded_at')}])
    check('the stranger is nowhere in it', stranger in json.dumps(
        answer, default=str), False)
    by_uid = data_request.find(store, uid=uid)
    check('asked by uid, the same galaxies',
          [g['galaxy'] for g in by_uid['uids'][0]['galaxies']],
          ['galaxyone', 'sandbox'])
    out = io.StringIO()
    data_request.report(answer, out)
    text = out.getvalue()
    check('the report names the civ\'s copies in past turns',
          'Duke in galaxyone' in text, True)
    check('nothing for an unknown code',
          data_request.find(store, code='QQQQ9999')['uids'], [])


test_rules()
if AUTH_HOST and os.environ.get('FIRESTORE_EMULATOR_HOST'):
    test_project()
else:
    print('the emulator part is skipped: no emulator variables are set')
    FAIL.append('emulators not running')
print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
sys.exit(1 if FAIL else 0)
