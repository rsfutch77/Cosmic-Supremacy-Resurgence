"""
test_galaxy_close.py , a galaxy the operator has called over
=============================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_galaxy_close.py
    ... test_galaxy_close.py --firebase firebase://demo-cs-resurgence

K5 has no season timer. The operator ends a galaxy and starts a fresh one, and
closing one has to stop submissions, keep everything readable, and leave every
launcher able to say why rather than fail.

The three stores are checked rather than one, because the word has to mean the
same thing wherever a player's launcher found the galaxy. The HTTP half matters
most: that is the transport a player actually holds, and a galaxy closed only in
the directory the operator was looking at would go on taking that player's turns
for as long as they had the store spec.

Every refusal here is paired with the same call against an open galaxy, so a
store that refused every submission for some unrelated reason could not pass.
The readable-after-closing checks are made against turns, an archive record and
a note that were **written before the close**, because a galaxy with an empty
archive would answer them all without reading anything.
"""
import argparse
import os
import shutil
import struct
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import turn_store
from turn_store import GalaxyClosed, open_store
from galaxy_directory import CLOSED, FORMING, OPEN, open_directory

ROSTER = ['DemoPlayer', 'Neighbor']
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def raises(name, fn, want=Exception):
    try:
        fn()
    except want as exc:                                     # noqa: BLE001
        check(f'{name} ({type(exc).__name__})', True)
        return
    except Exception as exc:                                # noqa: BLE001
        check(f'{name}, but it raised {type(exc).__name__}: {exc}', False)
        return
    check(f'{name}, but nothing was raised', False)


def make_blob(turn):
    """The same 96-byte blob the store equivalence test uses."""
    def section(tag, payload):
        return tag + struct.pack('<I', len(payload) & sp.SIZE_MASK) + payload
    return section(b'SAVE',
                   struct.pack('<I', 0) +
                   section(b'GLOB', struct.pack('<I', turn) + b'\0' * 32))


def furnish(store, turn=12):
    """A galaxy with something in it worth still being able to read.

    A turn, a submission, an archive record and a note, all written while the
    galaxy was open. Without these the readable-after-closing checks would be
    asking an empty store whether it could answer nothing.
    """
    store.start(make_blob(turn), ROSTER, turn_seconds=14400)
    store.submit('DemoPlayer', turn, make_blob(turn))
    store.archive(turn, {'turn': turn, 'published': turn + 1,
                         'submitted': ['DemoPlayer'], 'missing': ['Neighbor']})
    store.put_note('Neighbor', turn, ['a rename was refused'])
    return turn


def closing_a_store(store, label, turn, closer=None):
    """What closing does to one store, whichever kind it is.

    `closer` is where the operator's write goes when it cannot go through the
    store under test. An `HttpTurnStore` refuses to close a galaxy on purpose:
    it is the player's transport, and neither `turn_server.py` nor the relay
    offers a route for the two writes that end a player's game. So the galaxy
    is closed where it lives and the HTTP store is asked what it now sees,
    which is exactly the arrangement a real close happens in.
    """
    print(f'\nclosing a galaxy, {label}')
    closer = closer or store
    check('an open galaxy takes a submission',
          bool(store.submit('Neighbor', turn, make_blob(turn))))
    check('and reports itself open', store.status(), OPEN)
    if closer is not store:
        raises('a player\'s transport will not close a galaxy',
               lambda: store.close(), NotImplementedError)

    closer.close(reason='the sandbox has been replaced')
    check('a closed galaxy says so', store.status(), CLOSED)
    check('and says why', store.closed_reason(), 'the sandbox has been '
          'replaced')
    raises('a submission into a closed galaxy is refused',
           lambda: store.submit('Neighbor', turn, make_blob(turn)),
           GalaxyClosed)

    check('the clock still answers', store.current()[0], turn)
    check('the roster still answers', store.civs(), ROSTER)
    check('the turn is still readable', store.turn_blob(turn), make_blob(turn))
    check('the archive record is still readable',
          store.archive_record(turn)['submitted'], ['DemoPlayer'])
    check('a submission made before the close is still readable',
          store.submission('DemoPlayer', turn), make_blob(turn))
    check('and so is a note', store.note('Neighbor', turn),
          ['a rename was refused'])

    closer.reopen()
    check('reopening restores it', store.status(), OPEN)
    check('and drops the reason, so a second close cannot report the first',
          store.closed_reason(), None)
    check('a submission is taken again',
          bool(store.submit('Neighbor', turn, make_blob(turn))))
    closer.close(reason='closed again')


def run_local(tmp, base_url, served_root):
    print('\na closed galaxy in a directory of galaxies')
    d = open_directory(tmp)
    ended = d.register('ended', name='The First Sandbox')
    turn = furnish(ended)

    d.set_status('ended', CLOSED, reason='season one is over')
    check('the listing says closed', d.galaxy('ended').status, CLOSED)
    check('and the galaxy is still listed',
          [g.id for g in d.galaxies()].count('ended'), 1)
    check('its turn is still in the row', d.galaxy('ended').turn, turn)

    # The check that matters: a launcher holds a store spec, not a directory.
    spec = d.galaxy('ended').store
    alone = open_store(spec)
    check('a store opened from the spec alone knows it is closed',
          alone.status(), CLOSED)
    check('and can say why', alone.closed_reason(), 'season one is over')
    raises('and refuses a turn',
           lambda: alone.submit('Neighbor', turn, make_blob(turn)),
           GalaxyClosed)

    print('\nand a fresh one started beside it')
    fresh = d.register('season_two', name='The Second Sandbox')
    fresh_turn = furnish(fresh, turn=1)
    rows = {g.id: g for g in d.galaxies(player='DemoPlayer')}
    check('both galaxies are listed', sorted(rows), ['ended', 'season_two'])
    check('the closed one is closed', rows['ended'].status, CLOSED)
    check('CONTROL: the fresh one is open', rows['season_two'].status, OPEN)
    check('CONTROL: and takes a submission',
          bool(open_store(rows['season_two'].store).submit(
              'Neighbor', fresh_turn, make_blob(fresh_turn))))

    print('\na galaxy closed before it ever started')
    d.register('never_ran', name='Abandoned Before It Began')
    d.set_status('never_ran', CLOSED)
    check('has no state to carry the word and lists as forming',
          d.galaxy('never_ran').status, FORMING)

    print('\nthe store, as a directory')
    root = os.path.join(tmp, 'direct')
    store = turn_store.TurnStore(root)
    closing_a_store(store, 'a directory', furnish(store))

    print('\nthe store, over HTTP, which is what a player holds')
    # `state_seconds=0` because the close is written through the directory and
    # read back over HTTP at once, which a held `/state` would answer from the
    # moment before the close.
    served = turn_store.HttpTurnStore(base_url, state_seconds=0)
    closing_a_store(served, 'an HTTP store', 12,
                    closer=turn_store.TurnStore(served_root))


def run_firebase(spec, keep=False):
    """The beta deployment's half, as far as Firestore alone can answer it.

    Turns, submissions and notes are Cloud Storage and are not touched here:
    `functions/firebase.json` names no storage rules file, so the storage
    emulator refuses to start, and a galaxy cannot be `start`ed without it.
    What is left is exactly the part K5 turns on, because status, the reason
    and the reclaimed seats are Firestore fields on the galaxy document, and
    the relay serves them out of `STATE_FIELDS`. The submission refusal on this
    store is **not** covered by this run; see the report.
    """
    import firebase_store
    gid = f'closetest_{int(time.time())}'
    d = open_directory(spec)
    store = d.register(gid, name='Closing Test')
    try:
        check('status is one of the fields the firebase store returns, so a '
              'launcher behind the relay can read it',
              turn_store.STATUS_KEY in firebase_store.STATE_FIELDS)
        check('and so are the reclaimed seats',
              turn_store.RECLAIMED_KEY in firebase_store.STATE_FIELDS)
        check('the name the directory registered stays out of the state, '
              'which is the rule that field list exists for',
              'name' in store.state(), False)

        store.update_state({'turn': 12, 'deadline': time.time() + 14400,
                            'turn_seconds': 14400, 'civs': ROSTER})
        store.archive(12, {'turn': 12, 'published': 13,
                           'submitted': ['DemoPlayer'], 'missing': ['Neighbor']})
        check('an open galaxy reports itself open', store.status(), OPEN)

        store.close(reason='season one is over')
        check('a closed galaxy says so', store.status(), CLOSED)
        check('and says why', store.closed_reason(), 'season one is over')
        check('the clock still answers', store.current()[0], 12)
        check('the roster still answers', store.civs(), ROSTER)
        check('the archive record is still readable',
              store.archive_record(12)['submitted'], ['DemoPlayer'])
        check('the directory row agrees, because it is the same document',
              d.galaxy(gid).status, CLOSED)

        store.update_state({turn_store.RECLAIMED_KEY: {
            'Neighbor': {'turn': 12, 'missed': 12, 'at': time.time()}}})
        check('a reclaimed seat comes back through the state',
              (store.reclaimed('Neighbor') or {}).get('missed'), 12)
        check('and asking about a seat nobody took answers nothing',
              store.reclaimed('DemoPlayer'), None)

        d.set_status(gid, OPEN)
        check('reopening through the directory reaches the store',
              store.status(), OPEN)
        check('and takes the reason with it', store.closed_reason(), None)
    finally:
        if not keep:
            # The document and its archive, and not `delete_everything`: that
            # lists Cloud Storage, and with no storage emulator running the
            # client reaches the real service. Nothing in this run wrote a
            # blob, so there is nothing in Storage to remove.
            for snap in store.doc.collection('archive').stream():
                snap.reference.delete()
            store.doc.delete()


def firebase_project(given):
    if given:
        return given
    if os.environ.get('CS_FIREBASE_DIRECTORY'):
        return os.environ['CS_FIREBASE_DIRECTORY']
    if not os.environ.get('FIRESTORE_EMULATOR_HOST'):
        return None
    return ('firebase://demo-cs-resurgence'
            f'?bucket=demo-cs-resurgence.firebasestorage.app'
            f'&prefix=beta_closetest_{int(time.time())}')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--firebase', help='a firebase:// project to run against')
    ap.add_argument('--keep', action='store_true')
    a = ap.parse_args()

    import turn_server
    tmp = tempfile.mkdtemp(prefix='gclose_')
    # Outside `tmp`, because `tmp` is the directory of galaxies under test and
    # a folder directory lists its subdirectories: a served galaxy inside it
    # would turn up as a third row nothing put there.
    elsewhere = tempfile.mkdtemp(prefix='gserve_')
    served = os.path.join(elsewhere, 'served')
    os.makedirs(served, exist_ok=True)
    furnish(turn_store.TurnStore(served))
    turn_server.VERBOSE = False
    httpd = turn_server.serve(served, '127.0.0.1', 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        run_local(tmp, f'http://127.0.0.1:{httpd.server_address[1]}',
                  served)
    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(elsewhere, ignore_errors=True)

    spec = firebase_project(a.firebase)
    if spec:
        run_firebase(spec, keep=a.keep)
    else:
        print('\nfirebase: SKIPPED, no emulator and no --firebase project. '
              'This run does not say whether a galaxy closes in the beta '
              'deployment.')

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for name in FAIL:
        print(f'  failed: {name}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
