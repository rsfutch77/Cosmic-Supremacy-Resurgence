"""
test_galaxy_directory.py , the list a launcher's Games tab is made of
=====================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_galaxy_directory.py
    ... test_galaxy_directory.py --firebase firebase://demo-cs-resurgence

The directory answers what a store cannot: which galaxies there are, what they
are called, whether they are open, and whether this player already has a seat.
Both implementations are checked against the same expectations, for the reason
the store equivalence test gives, and the local one is checked against a galaxy
served over HTTP as well as one in a folder, because that is the arrangement
the two-machine test runs on and it is the one a listing could silently drop.

What is checked beyond the obvious: that a row carries a count and a flag and
never the roster, because a launcher that could list the roster turns "is there
a seat for me" into a way to enumerate who is playing; that a galaxy which has
been registered but never started lists as forming rather than not at all; and
that the spec a directory hands out opens through `open_store` without the
caller learning what kind it was.

The Firebase half runs when the emulator's environment variables are set, or
when a spec is given. Without either it is skipped and says so.
"""
import argparse
import os
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
from galaxy_directory import CLOSED, FORMING, OPEN, open_directory

PASS, FAIL = [], []

ROSTER = ['DemoPlayer', 'Neighbor']


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def make_blob(turn):
    """The same 96-byte blob the store equivalence test uses."""
    def section(tag, payload):
        return tag + struct.pack('<I', len(payload) & sp.SIZE_MASK) + payload
    return section(b'SAVE',
                   section(b'GLOB', struct.pack('<I', turn) + b'.' * 60) +
                   section(b'KNPL', bytes([0xAA]) * 8))


# ── the shared expectations ──────────────────────────────────────────────────
def run_sequence(d, label, started_id, forming_id):
    """The same checks against whichever directory this is.

    `started_id` names a galaxy with a turn published and a roster of two,
    `forming_id` one that has been registered and never started.
    """
    print(f'{label}: listing')
    rows = {g.id: g for g in d.galaxies()}
    check(f'{label}: both galaxies are listed',
          sorted(rows), sorted([started_id, forming_id]))

    g = rows[started_id]
    check(f'{label}: the row carries the galaxy name', g.name, 'The Sandbox')
    check(f'{label}: a started galaxy is open', g.status, OPEN)
    check(f'{label}: the row carries the current turn', g.turn, 12)
    check(f'{label}: the row carries the deadline in the future',
          g.deadline > time.time(), True)
    check(f'{label}: the row counts the players', g.players, 2)
    check(f'{label}: the row does not carry the roster',
          any('civ' in f or 'roster' in f for f in g._fields), False)

    f = rows[forming_id]
    check(f'{label}: a registered galaxy with no turn is forming',
          f.status, FORMING)
    check(f'{label}: and has no turn, no deadline and nobody in it',
          (f.turn, f.deadline, f.players), (None, None, 0))

    print(f'{label}: whether this player is in it')
    check(f'{label}: nobody is joined when no player is named',
          d.galaxy(started_id).joined, False)
    check(f'{label}: a player on the roster is joined',
          d.galaxy(started_id, player='Neighbor').joined, True)
    check(f'{label}: a player who is not is not',
          d.galaxy(started_id, player='Stranger').joined, False)
    check(f'{label}: the flag is per galaxy',
          d.galaxy(forming_id, player='Neighbor').joined, False)
    check(f'{label}: galaxies(player) sets the same flag',
          [g.joined for g in d.galaxies(player='Neighbor')
           if g.id == started_id], [True])
    check(f'{label}: a galaxy that is not there is None',
          d.galaxy('no-such-galaxy'), None)

    print(f'{label}: the store the row points at')
    store = d.store(started_id)
    check(f'{label}: it opens the galaxy the row described',
          store.current()[0], 12)
    check(f'{label}: and the row names a spec that opens the same one',
          turn_store.open_store(d.galaxy(started_id).store).current()[0], 12)

    print(f'{label}: closing one')
    d.set_status(started_id, CLOSED)
    check(f'{label}: a closed galaxy says so', d.galaxy(started_id).status,
          CLOSED)
    check(f'{label}: and is still listed, with its turn readable',
          d.galaxy(started_id).turn, 12)
    check(f'{label}: and its store still opens',
          d.store(started_id).current()[0], 12)
    d.set_status(started_id, OPEN)

    rows = d.galaxies(player='Neighbor')
    return {g.id: g._replace(store=None) for g in rows}


# ── local ────────────────────────────────────────────────────────────────────
def run_local(tmp, http_base=None):
    """A folder of galaxies, and one served over HTTP from a galaxies.json."""
    root = os.path.join(tmp, 'galaxies')
    os.makedirs(root, exist_ok=True)
    d = open_directory(root)

    started = d.register('sandbox', name='The Sandbox')
    started.start(make_blob(12), ROSTER, turn_seconds=14400)
    d.register('forming', name='Not Started Yet')

    print('local: a folder nobody registered anything in')
    bare_root = os.path.join(tmp, 'bare')
    turn_store.TurnStore(os.path.join(bare_root, 'handmade')).start(
        make_blob(12), ROSTER, turn_seconds=14400)
    bare = open_directory(bare_root)
    check('a subdirectory holding a state.json is a galaxy without any index',
          [(g.id, g.status, g.players) for g in bare.galaxies()],
          [('handmade', OPEN, 2)])
    check('and it opens', bare.store('handmade').current()[0], 12)

    summary = run_sequence(d, 'local', 'sandbox', 'forming')

    if http_base:
        print('local: a galaxy served over HTTP')
        d.register('remote', name='Over HTTP', store=http_base)
        row = d.galaxy('remote', player='Neighbor')
        check('an HTTP galaxy lists like any other',
              (row.status, row.turn, row.players, row.joined),
              (OPEN, 12, 2, True))
        check('and its store is the HTTP one',
              type(d.store('remote')).__name__, 'HttpTurnStore')
        check('a directory with no galaxy path in it still opened one',
              d.store('remote').current()[0], 12)

    print('local: an unreachable store is a galaxy this machine cannot see')
    d.register('gone', name='Unreachable', store='http://127.0.0.1:1/')
    check('it lists as forming rather than taking the listing down',
          d.galaxy('gone').status, FORMING)
    check('and the others are still listed',
          'sandbox' in {g.id for g in d.galaxies()}, True)
    return summary


# ── firebase ─────────────────────────────────────────────────────────────────
def firebase_project(given):
    if given:
        return given
    if os.environ.get('CS_FIREBASE_DIRECTORY'):
        return os.environ['CS_FIREBASE_DIRECTORY']
    if not os.environ.get('FIRESTORE_EMULATOR_HOST'):
        return None
    return ('firebase://demo-cs-resurgence'
            f'?bucket=demo-cs-resurgence.firebasestorage.app'
            f'&prefix=beta_dirtest_{int(time.time())}')


def run_firebase(spec, keep=False):
    d = open_directory(spec)
    print(f'firebase: {d!r}')
    started_id = f'sandbox_{int(time.time())}'
    forming_id = f'forming_{int(time.time())}'
    try:
        check('an empty directory lists nothing', d.galaxies(), [])
        started = d.register(started_id, name='The Sandbox')
        started.start(make_blob(12), ROSTER, turn_seconds=14400)
        d.register(forming_id, name='Not Started Yet')
        check('starting a galaxy does not lose the name it was registered '
              'under', d.galaxy(started_id).name, 'The Sandbox')
        return run_sequence(d, 'firebase', started_id, forming_id)
    finally:
        if keep:
            print(f'  kept {spec}')
        else:
            n = 0
            for gid in (started_id, forming_id):
                n += d.store(gid).delete_everything()
            print(f'  removed {n} object(s) and document(s)')


def compare(summaries, ids):
    """Both directories describe the same galaxy the same way."""
    print('the implementations agree')
    local, remote = summaries['local'], summaries['firebase']
    for label, lid, rid in ids:
        a, b = local[lid], remote[rid]
        for field in ('name', 'status', 'turn', 'players', 'joined'):
            check(f'local and firebase agree about a {label} galaxy\'s '
                  f'{field}', getattr(a, field), getattr(b, field))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--firebase', help='a firebase:// project to run against')
    ap.add_argument('--keep', action='store_true')
    a = ap.parse_args()

    import turn_server
    tmp = tempfile.mkdtemp(prefix='gdir_')
    served = os.path.join(tmp, 'served')
    os.makedirs(served, exist_ok=True)
    turn_store.TurnStore(served).start(make_blob(12), ROSTER,
                                       turn_seconds=14400)
    turn_server.VERBOSE = False
    httpd = turn_server.serve(served, '127.0.0.1', 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        summaries = {'local': run_local(
            tmp, f'http://127.0.0.1:{httpd.server_address[1]}')}
    finally:
        httpd.shutdown()
        httpd.server_close()

    spec = firebase_project(a.firebase)
    if spec:
        summaries['firebase'] = run_firebase(spec, keep=a.keep)
    else:
        print('firebase: SKIPPED, no emulator and no --firebase project. '
              'This run does not say whether J1 is done.')

    if len(summaries) > 1:
        local_started = 'sandbox'
        remote = [k for k in summaries['firebase'] if k.startswith('sandbox')]
        forming = [k for k in summaries['firebase'] if k.startswith('forming')]
        compare(summaries, [('started', local_started, remote[0]),
                            ('forming', 'forming', forming[0])])

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    if FAIL:
        for name in FAIL:
            print(f'  failed: {name}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
