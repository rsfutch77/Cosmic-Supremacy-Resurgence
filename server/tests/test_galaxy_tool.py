"""
test_galaxy_tool.py , the operator ends a galaxy and starts the next (K5)
=========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_galaxy_tool.py

    python functions/emulators.py --only firestore,storage,auth
    set FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
    set STORAGE_EMULATOR_HOST=http://127.0.0.1:9199
    set FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
    server\\.venv\\Scripts\\python.exe server\\tests\\test_galaxy_tool.py

K5's done-when is that a galaxy is closed and a fresh one started, and a
launcher pointed at the closed one says so rather than failing. The first part
runs `galaxy_tool.py` against a folder of galaxies, offline. The second runs it
against Firebase on the emulator and then looks at the result the way a player
does, through the relay in process: the Galaxies listing, the closed galaxy's
`/state` read by the launcher's own `closed_problem`, a submission into it, and
its turn still served.

Every refusal is checked against what is on disk or in Firestore afterwards,
because a tool that printed "refused" after writing half a start would
otherwise pass. Every dry run is checked for having written nothing.
"""
import io
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (HERE, os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools'),
          os.path.join(ROOT, 'release')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp                                        # noqa: E402
import inject_civ as icv                                        # noqa: E402
import galaxy_tool                                              # noqa: E402
import turn_store                                               # noqa: E402
from galaxy_directory import CLOSED, OPEN, open_directory       # noqa: E402

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
CIVS = [o['name'] for o in icv.owner_records(sp.load_any(GALAXY))]
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def tool(*argv):
    """(exit code, printed text) from the command line entry."""
    out = io.StringIO()
    code = galaxy_tool.main([str(a) for a in argv], out=out)
    return code, out.getvalue()


def snapshot(root):
    """Every file under a folder with its size, to show a dry run wrote
    nothing."""
    out = {}
    for base, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            out[os.path.relpath(p, root)] = os.path.getsize(p)
    return out


# ── a folder of galaxies ─────────────────────────────────────────────────────
def run_folder(tmp):
    root = os.path.join(tmp, 'galaxies')
    os.makedirs(root)
    print('starting a galaxy from a folder directory')
    code, text = tool(root, 'start', 'one', '--name', 'Season One',
                      '--blob', GALAXY, *sum([['--player', c] for c in CIVS],
                                             []), '--dry-run')
    check('a dry run of a start says what it would do',
          (code, 'would start one' in text), (0, True))
    check('and writes nothing', snapshot(root), {})

    code, text = tool(root, 'start', 'one', '--name', 'Season One',
                      '--blob', GALAXY, *sum([['--player', c] for c in CIVS],
                                             []), '--warn', 3, '--reclaim', 6)
    check('a start succeeds', code, 0)
    d = open_directory(root)
    g = d.galaxy('one')
    check('the directory lists it open, under its name, with its roster',
          (g.status, g.name, g.players), (OPEN, 'Season One', len(CIVS)))
    store = d.store('one')
    check('its first turn is the blob it was given',
          store.turn_blob(store.current()[0]), sp.load_any(GALAXY))
    check('and it carries the abandonment thresholds',
          (store.state().get('warn_after_misses'),
           store.state().get('reclaim_after_misses')), (3, 6))
    check('the tool says how to point the worker at it',
          'referee_worker.py --store' in text, True)

    print('what a start refuses')
    before = snapshot(root)
    cases = [
        ('an id that already has a turn',
         ['one', '--blob', GALAXY, '--player', CIVS[0], '--player', CIVS[1]],
         'already has a turn'),
        ('a player the galaxy holds no civ for',
         ['two', '--blob', GALAXY, '--player', CIVS[0], '--player', 'Nobody'],
         'no civ called'),
        ('one player', ['two', '--blob', GALAXY, '--player', CIVS[0]],
         'at least two players'),
        ('both a blob and a generation',
         ['two', '--blob', GALAXY, '--generate', GALAXY, '--player', 'A',
          '--player', 'B'], 'exactly one of'),
        ('a seat claim on a galaxy the relay does not serve',
         ['two', '--blob', GALAXY, '--player', CIVS[0], '--player', CIVS[1],
          '--seat-claim', 'first-use'], 'relay setting'),
        ('a galaxy replacing itself',
         ['two', '--blob', GALAXY, '--player', CIVS[0], '--player', CIVS[1],
          '--replaces', 'two'], 'cannot replace itself'),
        ('replacing a galaxy that does not exist',
         ['two', '--blob', GALAXY, '--player', CIVS[0], '--player', CIVS[1],
          '--replaces', 'nowhere'], 'there is no galaxy nowhere'),
    ]
    for label, args, says in cases:
        code, text = tool(root, 'start', *args)
        check(f'{label}: refused and says why', (code, says in text),
              (2, True))
        check(f'{label}: and nothing was written', snapshot(root), before)

    print('closing it')
    code, text = tool(root, 'close', 'one', '--reason', '  ')
    check('a close with no reason is refused', (code, 'with a reason' in text),
          (2, True))
    code, text = tool(root, 'close', 'one', '--reason', 'Season one is over.',
                      '--dry-run')
    check('a dry run of a close says what it would do',
          (code, 'would close one' in text), (0, True))
    check('and writes nothing', snapshot(root), before)
    code, text = tool(root, 'close', 'one', '--reason', 'Season one is over.')
    check('the close succeeds', code, 0)
    check('and reads the turn back', 'still reads' in text, True)
    check('the galaxy says it is closed, and why',
          (store.status(), store.closed_reason()),
          (CLOSED, 'Season one is over.'))
    check('the directory row agrees', d.galaxy('one').closed_reason,
          'Season one is over.')
    try:
        store.submit(CIVS[0], store.current()[0], sp.load_any(GALAXY))
        refused = False
    except turn_store.GalaxyClosed:
        refused = True
    check('a submission is refused', refused, True)
    check('and its turn still reads',
          store.turn_blob(store.current()[0]) == sp.load_any(GALAXY), True)
    code, text = tool(root, 'close', 'one', '--reason', 'again')
    check('closing it twice is refused', (code, 'already closed' in text),
          (2, True))
    code, text = tool(root, 'reopen', 'one')
    check('reopen undoes it', (code, store.status()), (0, OPEN))
    tool(root, 'close', 'one', '--reason', 'Season one is over.')

    print('a generated galaxy replacing a closed one fails cleanly')
    code, text = tool(root, 'start', 'two', '--generate', GALAXY,
                      '--player', 'Alice', '--player', 'Bob',
                      '--replaces', 'one')
    check('replacing a galaxy already closed is refused before anything is '
          'written', (code, 'already closed' in text, d.galaxy('two')),
          (2, True, None))
    return root


def run_replace(tmp):
    print('\nstarting one galaxy in place of another')
    root = os.path.join(tmp, 'replace')
    os.makedirs(root)
    tool(root, 'start', 'old', '--blob', GALAXY, '--player', CIVS[0],
         '--player', CIVS[1])
    d = open_directory(root)
    before = snapshot(root)
    code, text = tool(root, 'start', 'new', '--generate', GALAXY,
                      '--player', 'Alice', '--player', 'Alice',
                      '--replaces', 'old')
    check('a start that fails leaves the old galaxy open',
          (code, d.galaxy('old').status, snapshot(root) == before),
          (2, OPEN, True))
    code, text = tool(root, 'start', 'new', '--name', 'Season Two',
                      '--generate', GALAXY, '--player', 'Alice',
                      '--player', 'Bob', '--replaces', 'old')
    check('the replacement succeeds', code, 0)
    new, old = d.galaxy('new'), d.galaxy('old')
    check('the new galaxy is open with one civ per player',
          (new.status, new.players), (OPEN, 2))
    blob = d.store('new').turn_blob(new.turn)
    check('and a generated galaxy holds exactly the players\' civs',
          sorted(o['name'] for o in icv.owner_records(blob)),
          ['Alice', 'Bob'])
    check('the old one is closed', old.status, CLOSED)
    check('and tells its players where to go',
          old.closed_reason, 'This galaxy has ended. Season Two has started '
                             'in its place.')
    check('both are listed', sorted(g.id for g in d.galaxies()),
          ['new', 'old'])


# ── Firebase, seen through the relay ─────────────────────────────────────────
def run_firebase():
    import test_relay_function as rf
    import launcher
    from http.server import ThreadingHTTPServer
    from turn_store import HttpTurnStore

    spec = f'firebase://{rf.PROJECT}?bucket={rf.BUCKET}'
    stamp = int(time.time())
    one, two = f'k5one_{stamp}', f'k5two_{stamp}'
    d = open_directory(spec)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), rf.RelayHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}'
    _uid, token = rf.sign_up()
    players = sum([['--player', c] for c in CIVS], [])
    try:
        print('\nFirebase: starting a galaxy')
        code, text = tool(spec, 'start', one, '--name', 'K5 One', '--blob',
                          GALAXY, *players, '--dry-run')
        check('a dry run writes no document',
              (code, d.fs.collection(d.prefix).document(one).get().exists),
              (0, False))
        code, text = tool(spec, 'start', one, '--name', 'K5 One', '--blob',
                          GALAXY, *players)
        check('the start succeeds', code, 0)
        first = d.galaxy(one).turn

        print('Firebase: closing it by starting its replacement')
        code, text = tool(spec, 'start', two, '--name', 'K5 Two', '--blob',
                          GALAXY, *players, '--replaces', one, '--reason',
                          'Season one is over; K5 Two is open.',
                          '--seat-claim', 'first-use', '--dry-run')
        check('a dry run of a replacement changes neither galaxy',
              (code, d.galaxy(one).status,
               d.fs.collection(d.prefix).document(two).get().exists),
              (0, OPEN, False))
        code, text = tool(spec, 'start', two, '--name', 'K5 Two', '--blob',
                          GALAXY, *players, '--replaces', one, '--reason',
                          'Season one is over; K5 Two is open.',
                          '--seat-claim', 'first-use')
        check('the replacement succeeds', code, 0)
        doc = d.fs.collection(d.prefix).document(two).get().to_dict() or {}
        check('the new galaxy takes first-use claims',
              doc.get('seat_claim'), 'first-use')

        print('Firebase: what a player\'s launcher sees, through the relay')
        listing = open_directory(base, token=lambda: token)
        rows = {g.id: g for g in listing.galaxies() if g.id in (one, two)}
        check('the Galaxies page lists both', sorted(rows), [one, two])
        check('the old one as closed, with the operator\'s reason',
              (rows[one].status, rows[one].closed_reason),
              (CLOSED, 'Season one is over; K5 Two is open.'))
        check('the new one as open, on its first turn, under its name',
              (rows[two].status, rows[two].turn, rows[two].name),
              (OPEN, first, 'K5 Two'))

        player = HttpTurnStore(f'{base}/{one}', token=lambda: token,
                               state_seconds=0)
        said = launcher.closed_problem(player.state())
        check('a launcher pointed at the closed galaxy says it has ended',
              'This galaxy has been closed.' in (said or ''), True)
        check('and gives the operator\'s reason',
              'Season one is over' in (said or ''), True)
        check('CONTROL: and says nothing of the kind about the open one',
              launcher.closed_problem(HttpTurnStore(
                  f'{base}/{two}', token=lambda: token,
                  state_seconds=0).state()), None)
        try:
            player.submit(CIVS[0], first, rf.make_blob(first))
            refused = None
        except turn_store.GalaxyClosed as exc:
            refused = exc
        check('the launcher\'s store refuses a submission into it',
              type(refused).__name__, 'GalaxyClosed')
        # And the relay refuses it as well, for a launcher that skipped that
        # check: the upload ticket is the door a submission goes through.
        import relay
        code, _h, body = relay.handle(
            'GET', f'/{one}/upload/submission/{first}/{CIVS[0]}',
            {'Authorization': f'Bearer {token}'})
        check('the relay refuses the upload ticket as closed, with the reason',
              (code, b'is closed' in body, b'Season one is over' in body),
              (409, True, True))
        check('and its turn is still served',
              player.turn_blob(first) == sp.load_any(GALAXY), True)
    finally:
        httpd.shutdown()
        httpd.server_close()
        n = 0
        for gid in (one, two):
            n += d.store(gid).delete_everything()
        print(f'  removed {n} object(s) and document(s)')


def main():
    tmp = tempfile.mkdtemp(prefix='galaxytool_')
    try:
        run_folder(tmp)
        run_replace(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if os.environ.get('FIRESTORE_EMULATOR_HOST'):
        run_firebase()
    else:
        print('\nfirebase: SKIPPED, no FIRESTORE_EMULATOR_HOST. This run does '
              'not say whether a Firebase galaxy closes and is replaced.')
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for name in FAIL:
        print(f'  failed: {name}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
