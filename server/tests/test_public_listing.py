"""
test_public_listing.py: the galaxy listing the website reads
============================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_public_listing.py

The referee writes `public/galaxies.json` after each tick, listing the open
galaxies by title. Without the emulators only the rows and the skips are
checked; with them (`functions/emulators.py --only firestore,storage,auth`),
the object is written, and an unsigned read gets it and nothing else.
"""
import json
import os
import struct
import sys
import tempfile
import time
import types
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import public_listing                                           # noqa: E402
import turn_store                                               # noqa: E402
from galaxy_directory import CLOSED, FORMING, OPEN              # noqa: E402

PROJECT = os.environ.get('CS_RELAY_PROJECT') or 'demo-cs-resurgence'
BUCKET = f'{PROJECT}.firebasestorage.app'
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


class Directory:
    def __init__(self, rows):
        self.rows = rows

    def galaxies(self, player=None):
        return [types.SimpleNamespace(name=n, status=s) for n, s in self.rows]


def offline():
    print('\nthe rows')
    d = Directory([('Sandbox', OPEN), ('Old One', CLOSED), ('Next', FORMING)])
    check('only open galaxies are listed, by title alone',
          public_listing.rows(d), [{'title': 'Sandbox'}])
    check('the document is the rows under "galaxies"',
          json.loads(public_listing.document(d)), {'galaxies': [{'title': 'Sandbox'}]})
    check('no open galaxy lists nothing',
          json.loads(public_listing.document(Directory([('Old', CLOSED)]))),
          {'galaxies': []})

    print('\nwhat is skipped')
    folder = turn_store.open_store(tempfile.mkdtemp())
    check('a folder store has no bucket, so nothing is published',
          public_listing.publish(folder, log=lambda *_: None), False)

    class Broken:
        project, prefix, fs = PROJECT, 'x', None

        @property
        def bucket(self):
            raise RuntimeError('no network')

    said = []
    check('a failure is logged, not raised',
          public_listing.publish(Broken(), log=said.append), False)
    check('and the log says the turn is unaffected',
          any('the turn is unaffected' in s for s in said), True)


def unsigned_get(name):
    host = os.environ['STORAGE_EMULATOR_HOST'].rstrip('/')
    url = (f'{host}/v0/b/{BUCKET}/o/{urllib.parse.quote(name, safe="")}'
           f'?alt=media')
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b''


def blob_at(turn):
    """The 96-byte blob the store equivalence test uses."""
    import save_parser as sp

    def section(tag, payload):
        return tag + struct.pack('<I', len(payload) & sp.SIZE_MASK) + payload
    return section(b'SAVE', struct.pack('<I', 0) +
                   section(b'GLOB', struct.pack('<I', turn) + b'\0' * 32))


def emulated():
    import firebase_store
    print('\nagainst the emulators')
    prefix = f'publiclisting_{int(time.time())}'
    store = firebase_store.FirebaseTurnStore(PROJECT, 'sandbox', bucket=BUCKET,
                                             prefix=prefix)
    d = public_listing.directory_for(store)
    d.register('sandbox', name='Sandbox', status=OPEN)
    store.start(blob_at(12), ['DemoPlayer'], turn_seconds=14400)
    d.register('waiting', name='Waiting', status=OPEN)
    d.register('ended', name='Ended', status=CLOSED)
    check('the listing is published', public_listing.publish(store), True)
    blob = store.bucket.get_blob(public_listing.OBJECT)
    check('as JSON', blob.content_type, 'application/json')
    check('cached for five minutes', blob.cache_control,
          public_listing.CACHE_CONTROL)
    check('naming the open galaxy only, not one still forming',
          json.loads(blob.download_as_bytes()), {'galaxies': [{'title': 'Sandbox'}]})
    status, body = unsigned_get(public_listing.OBJECT)
    check('an unsigned read gets it', (status, json.loads(body or b'null')),
          (200, {'galaxies': [{'title': 'Sandbox'}]}))
    other = f'{prefix}/probe.json'
    store.bucket.blob(other).upload_from_string(b'{}')
    check('and is refused anything else', unsigned_get(other)[0] in (401, 403),
          True)


def main():
    offline()
    if os.environ.get('STORAGE_EMULATOR_HOST') and os.environ.get(
            'FIRESTORE_EMULATOR_HOST'):
        emulated()
    else:
        print('\nSKIPPED the emulator half: no STORAGE_EMULATOR_HOST.')
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
