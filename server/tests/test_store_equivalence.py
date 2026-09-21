"""
test_store_equivalence.py , three stores, one interface, the same answers
=========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_store_equivalence.py
    ... test_store_equivalence.py --firebase firebase://demo-cs/g1?bucket=b
    ... test_store_equivalence.py --only firebase --firebase <spec> --keep

`open_store` hands a caller a directory, a service or Firebase and the caller
never learns which. That is only true while the three behave the same, and the
places they can quietly stop are not the obvious ones: what a missing turn
raises, whether a second submission replaces or joins, whether publishing keeps
the roster, what `state` holds, and what an archive record survives being
stored as.

So the same sequence runs against each of them and the answers are compared,
rather than each implementation being tested against its own expectations. The
sequence is the F1 sequence: start, publish, submit, the submission list, the
archive, the notes, missing turns and the factory.

The HTTP run is made against a directory this test can also read, so the
service's answers are checked against the directory underneath it as well as
against the other implementations. Firebase has nothing underneath it to
compare with, which is the point of comparing the three summaries.

The Firebase run needs somewhere to run against, and takes it either way:

    firebase emulators:start --only firestore,storage
    set FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
    set STORAGE_EMULATOR_HOST=http://127.0.0.1:9199

With those set and no `--firebase`, the run uses a `demo-` project, which the
emulator suite treats as offline-only, and a galaxy named for the clock so two
runs cannot collide. Whatever it creates it deletes, unless `--keep`.

The blobs are built here rather than loaded, because two of the things under
test read inside one: `start` takes the turn number from the blob when it is
not told one, and the state carries the blob's canonical hash. Ninety-six bytes
of real framing gives both, and a `KNPL` whose trailing dword the canonical
form masks, so a hash that agreed for the wrong reason would show. The save
format itself is exercised against real captures elsewhere.
"""
import argparse
import os
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import canonical
import save_parser as sp
import turn_store
from turn_store import HttpTurnStore, TurnStore, open_store

PASS, FAIL = [], []

TURN = 7


def section(tag, payload, version=0):
    """One section of the save format: the tag, the size, the payload."""
    head = (len(payload) & sp.SIZE_MASK) | (version << sp.VERSION_SHIFT)
    return tag + struct.pack('<I', head) + payload


def make_blob(turn, filler=b'.'):
    """A blob that carries a turn number and a field the canonical form masks.

    `start` reads the turn out of the blob when it is not given one, which is
    the path the referee uses and the only path the HTTP store offers, so a
    test blob that cannot be parsed would quietly test something narrower.
    """
    return section(b'SAVE',
                   section(b'GLOB', struct.pack('<I', turn) + filler * 60) +
                   section(b'KNPL', bytes([0xAA]) * 8))


BLOB1 = make_blob(TURN)
BLOB2 = make_blob(TURN + 1)
BLOB3 = make_blob(TURN + 2)
MINE = make_blob(TURN, b'o')
MINE2 = make_blob(TURN, b'p')
THEIRS = make_blob(TURN, b'n')

# A civ name with a dot in it, which is why the Firebase archive record is one
# JSON string and not a Firestore map: a map key may not be any string a player
# typed, and this is a name a player could type.
DOTTED = 'Player.One'


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def raises(fn, *a):
    """What a call raised, by class name, or None. Missing things differ."""
    try:
        fn(*a)
    except Exception as exc:                                # noqa: BLE001
        return type(exc).__name__
    return None


# ── the sequence ─────────────────────────────────────────────────────────────
def run_sequence(store, label, turn=TURN):
    """The same checks against whichever store this is.

    Returns what the store ended up holding, so the three runs can be compared
    against each other rather than only against their own expectations.
    """
    print(f'{label}: an empty store')
    check(f'{label}: exists() is False before a galaxy is started',
          store.exists(), False)
    check(f'{label}: state() on an empty store raises FileNotFoundError',
          raises(store.state), 'FileNotFoundError')

    print(f'{label}: start')
    started = store.start(BLOB1, ['DemoPlayer', 'Neighbor'], turn_seconds=1800)
    check(f'{label}: start takes the turn number from the blob', started, turn)
    check(f'{label}: exists() is True once it has', store.exists(), True)
    state = store.state()
    check(f'{label}: state holds exactly the five fields the interface names',
          sorted(state), ['civs', 'deadline', 'hash', 'turn', 'turn_seconds'])
    check(f'{label}: current() is the started turn', store.current()[0], turn)
    check(f'{label}: the deadline is turn_seconds away',
          round(store.seconds_left() / 60), 30)
    check(f'{label}: civs() is the roster it was started with',
          store.civs(), ['DemoPlayer', 'Neighbor'])
    check(f'{label}: the state carries the blob canonical hash',
          state['hash'], canonical.canonical_hash(BLOB1))

    print(f'{label}: turns')
    check(f'{label}: the turn blob comes back as it went in',
          store.turn_blob(turn), BLOB1)
    check(f'{label}: has_turn is True for a turn that was published',
          store.has_turn(turn), True)
    check(f'{label}: has_turn is False for one that was not',
          store.has_turn(turn + 5), False)
    check(f'{label}: a missing turn raises FileNotFoundError',
          raises(store.turn_blob, turn + 5), 'FileNotFoundError')

    print(f'{label}: submissions')
    check(f'{label}: nobody has submitted yet',
          store.has_submitted('DemoPlayer', turn), False)
    where = store.submit('DemoPlayer', turn, MINE)
    check(f'{label}: submit names where it landed',
          bool(where) and 'DemoPlayer' in where, True)
    check(f'{label}: has_submitted is True once they have',
          store.has_submitted('DemoPlayer', turn), True)
    check(f'{label}: the submission comes back as it went in',
          store.submissions(turn), {'DemoPlayer': MINE})
    store.submit('DemoPlayer', turn, MINE2)
    check(f'{label}: submitting twice replaces rather than joins',
          store.submissions(turn), {'DemoPlayer': MINE2})
    store.submit('Neighbor', turn, THEIRS)
    check(f'{label}: another civ joins the list',
          store.submissions(turn), {'DemoPlayer': MINE2, 'Neighbor': THEIRS})
    check(f'{label}: a turn nobody submitted for lists nothing',
          store.submissions(turn + 1), {})

    print(f'{label}: notes')
    check(f'{label}: a turn nothing was refused on has no note',
          store.note('DemoPlayer', turn), [])
    reasons = ['system 193: rename DROPPED, not a majority',
               'people DROPPED, 9 served and 10 came back']
    store.put_note('DemoPlayer', turn, reasons)
    check(f'{label}: a note comes back as it was written',
          store.note('DemoPlayer', turn), reasons)
    check(f'{label}: and only to the player it was left for',
          store.note('Neighbor', turn), [])
    check(f'{label}: notes are per turn',
          store.note('DemoPlayer', turn + 1), [])

    print(f'{label}: archive')
    check(f'{label}: an unarchived turn records None',
          store.archive_record(turn), None)
    record = {'turn': turn, 'published': turn + 1,
              'submitted': ['DemoPlayer', 'Neighbor'], 'missing': [],
              'refused': {DOTTED: list(reasons)},
              'hash_in': canonical.canonical_hash(BLOB1),
              'hash_out': canonical.canonical_hash(BLOB2),
              'hash_submissions': {DOTTED: canonical.canonical_hash(MINE2)}}
    store.archive(turn, record)
    check(f'{label}: the archive record comes back as it was written',
          store.archive_record(turn), record)

    print(f'{label}: publish')
    before = store.state()['deadline']
    time.sleep(0.01)
    store.publish(turn + 1, BLOB2)
    check(f'{label}: the clock moves to the published turn',
          store.current()[0], turn + 1)
    check(f'{label}: publishing restarts the clock',
          store.state()['deadline'] > before, True)
    check(f'{label}: publishing keeps the roster',
          store.civs(), ['DemoPlayer', 'Neighbor'])
    check(f'{label}: publishing keeps the turn length',
          store.state()['turn_seconds'], 1800)
    check(f'{label}: the published blob comes back as it went in',
          store.turn_blob(turn + 1), BLOB2)
    check(f'{label}: and the state carries its hash',
          store.state()['hash'], canonical.canonical_hash(BLOB2))
    store.publish(turn + 2, BLOB3, turn_seconds=14400)
    check(f'{label}: a publish may change the turn length',
          store.state()['turn_seconds'], 14400)
    check(f'{label}: the older turn is still there',
          store.turn_blob(turn), BLOB1)
    check(f'{label}: and so is its archive record',
          store.archive_record(turn), record)

    return {
        'turn': store.current()[0],
        'turn_seconds': store.state()['turn_seconds'],
        'civs': store.civs(),
        'hash': store.state()['hash'],
        'blob': store.turn_blob(store.current()[0]),
        'submissions': store.submissions(turn),
        'note': store.note('DemoPlayer', turn),
        'archive': store.archive_record(turn),
    }


# ── the three runs ───────────────────────────────────────────────────────────
def run_directory(tmp):
    root = os.path.join(tmp, 'galaxy_dir')
    return run_sequence(TurnStore(root), 'directory')


def run_http(tmp):
    """The service, in front of a directory this test can also read."""
    import turn_server
    root = os.path.join(tmp, 'galaxy_http')
    os.makedirs(root, exist_ok=True)
    turn_server.VERBOSE = False
    httpd = turn_server.serve(root, '127.0.0.1', 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{httpd.server_address[1]}'
        summary = run_sequence(HttpTurnStore(base), 'http')
    finally:
        httpd.shutdown()
        httpd.server_close()

    print('http: the directory underneath agrees')
    under = TurnStore(root)
    check('http: the directory sees the same turn',
          under.current()[0], summary['turn'])
    check('http: the directory sees the same hash',
          under.state()['hash'], summary['hash'])
    check('http: the directory holds the same blob bytes',
          under.turn_blob(summary['turn']), summary['blob'])
    check('http: the directory sees the same submissions',
          under.submissions(7), summary['submissions'])
    check('http: the directory holds the same archive record',
          under.archive_record(7), summary['archive'])
    return summary


def firebase_spec(given):
    """Where the Firebase run goes, and whether there is anywhere for it.

    An explicit spec wins, because that is how the live smoke check is aimed.
    Otherwise the emulator, which announces itself through the same environment
    variables the client libraries read, and a `demo-` project, which the
    emulator suite refuses to let reach a real one.
    """
    if given:
        return given
    if os.environ.get('CS_FIREBASE_STORE'):
        return os.environ['CS_FIREBASE_STORE']
    if not os.environ.get('FIRESTORE_EMULATOR_HOST'):
        return None
    galaxy = f'equiv_{int(time.time())}'
    return (f'firebase://demo-cs-resurgence/{galaxy}'
            '?bucket=demo-cs-resurgence.firebasestorage.app')


def run_firebase(spec, keep=False):
    store = open_store(spec)
    print(f'firebase: {store!r}')
    try:
        return run_sequence(store, 'firebase')
    finally:
        if keep:
            print(f'  kept {spec}')
        else:
            n = store.delete_everything()
            print(f'  removed {n} object(s) and document(s) from {spec}')


# ── the factory ──────────────────────────────────────────────────────────────
def check_factory():
    print('the factory hands back the right one')
    import firebase_store
    check('a path is a directory store',
          type(open_store('some/dir')).__name__, 'TurnStore')
    check('a URL is the HTTP store',
          type(open_store('http://host:8899')).__name__, 'HttpTurnStore')
    check('a firebase spec is the Firebase store',
          type(open_store('firebase://proj/gal')).__name__,
          'FirebaseTurnStore')
    check('the spec parses into project, galaxy, bucket and prefix',
          firebase_store.parse_spec(
              'firebase://proj/gal?bucket=b&prefix=beta2'),
          ('proj', 'gal', 'b', 'beta2'))
    check('a spec with no galaxy is refused by the store',
          raises(open_store, 'firebase://proj'), 'ValueError')


def compare(summaries):
    """Every implementation ended up holding the same galaxy."""
    print('the implementations agree')
    names = sorted(summaries)
    first = names[0]
    for other in names[1:]:
        a, b = summaries[first], summaries[other]
        for field in sorted(a):
            check(f'{first} and {other} agree about {field}',
                  a[field] == b[field], True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--firebase', help='a firebase:// spec to run against')
    ap.add_argument('--only', choices=['dir', 'http', 'firebase'],
                    action='append',
                    help='run one implementation rather than all of them')
    ap.add_argument('--keep', action='store_true',
                    help='leave the Firebase galaxy behind')
    a = ap.parse_args()
    want = a.only or ['dir', 'http', 'firebase']

    import tempfile
    tmp = tempfile.mkdtemp(prefix='equiv_')
    summaries = {}
    if 'dir' in want:
        summaries['directory'] = run_directory(tmp)
    if 'http' in want:
        summaries['http'] = run_http(tmp)
    if 'firebase' in want:
        spec = firebase_spec(a.firebase)
        if spec:
            summaries['firebase'] = run_firebase(spec, keep=a.keep)
        else:
            print('firebase: SKIPPED, no emulator and no --firebase spec. '
                  'This run does not say whether H1 is done.')
    if len(summaries) > 1:
        compare(summaries)
    check_factory()

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    if FAIL:
        for name in FAIL:
            print(f'  failed: {name}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
