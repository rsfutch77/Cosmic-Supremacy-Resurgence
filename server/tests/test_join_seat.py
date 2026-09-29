"""
test_join_seat.py , a granted join binds its seat (J3 and J4)
==============================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_join_seat.py

    python functions/emulators.py --only firestore,storage,auth
    set FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
    set STORAGE_EMULATOR_HOST=http://127.0.0.1:9199
    set FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
    server\\.venv\\Scripts\\python.exe server\\tests\\test_join_seat.py

A join granted at a boundary writes `seats[uid] = civ` on the galaxy document,
in the same write as the roster. The first part runs offline: the rules that
decide which seats move, and a folder galaxy, which carries no seat map and
must not grow one. The second part needs the emulator and runs the story end
to end with the relay in process: a sign-in lodges a join through the relay,
the referee closes the turn with the engine stubbed, the joiner submits as the
new civ with `seat_claim` off and lands, and a different sign-in is refused,
with first-use off and with it on.

Every refusal is paired with the store underneath, because the relay saying 403
is not evidence that nothing landed. The referee's store is the oracle.
"""
import os
import shutil
import struct
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (HERE, os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp                                        # noqa: E402
import joins                                                    # noqa: E402
import referee                                                  # noqa: E402
import player_turn                                              # noqa: E402
import turn_store                                               # noqa: E402
from turn_store import TurnStore                                 # noqa: E402

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
ROSTER = ['DemoPlayer', 'BadGuy']
PASS, FAIL = [], []

UID_A = 'A' * 28
UID_B = 'B' * 28
UID_X = 'X' * 28


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def set_turn(blob, turn):
    tree = sp.parse_blob(blob)
    glob = next(tree[0].find('GLOB'))
    out = bytearray(blob)
    struct.pack_into('<I', out, glob.payload, turn)
    return bytes(out)


def close_turn(store):
    """`resolve_turn` with the engine stubbed to advance the turn number."""
    def fake_tick(blob, turns=1, secs=10, save_dir=None, log=print, **kw):
        return set_turn(blob, turn_store.turn_of(blob) + 1)

    real_tick, real_ready = referee.tick, player_turn.save_path_ready
    referee.tick = fake_tick
    player_turn.save_path_ready = lambda: (True, '')
    lines = []
    try:
        return referee.resolve_turn(store, log=lines.append), lines
    finally:
        referee.tick, player_turn.save_path_ready = real_tick, real_ready


def granted(name, uid):
    return {'name': name, 'uid': uid, 'outcome': joins.GRANTED}


# ── offline ──────────────────────────────────────────────────────────────────
def test_rules():
    print('which seats a commit moves')
    quiet = []
    bind, drop = joins.seat_changes({UID_A: 'DemoPlayer'}, ROSTER,
                                    [granted('Joiner', UID_X)], quiet.append)
    check('a new sign-in is bound to the civ it asked for',
          (bind, drop), ({UID_X: 'Joiner'}, []))

    bind, drop = joins.seat_changes({UID_A: 'DemoPlayer'}, ROSTER,
                                    [granted('Joiner', UID_A)], quiet.append)
    check('a sign-in playing a civ on the roster keeps it',
          (bind, drop), ({}, []))
    check('and the log says the new civ is left for the operator',
          any('left unseated' in line for line in quiet), True)

    bind, drop = joins.seat_changes({UID_A: 'Ghost'}, ROSTER,
                                    [granted('Joiner', UID_A)], quiet.append)
    check('a sign-in whose civ was reclaimed moves to the new one',
          (bind, drop), ({UID_A: 'Joiner'}, []))

    bind, drop = joins.seat_changes({UID_A: 'Ghost', UID_B: 'DemoPlayer'},
                                    ROSTER, [granted('Ghost', UID_X)],
                                    quiet.append)
    check('a reclaimed name taken by somebody else leaves the old holder '
          'with nothing', (bind, drop), ({UID_X: 'Ghost'}, [UID_A]))

    bind, drop = joins.seat_changes({UID_A: 'Ghost'}, ROSTER,
                                    [granted('Ghost', UID_A)], quiet.append)
    check('and the same sign-in coming back under the same name keeps it',
          (bind, drop), ({}, []))

    bind, drop = joins.seat_changes({}, ROSTER,
                                    [granted('Ada', None),
                                     granted('Bo', 'seats.x'),
                                     granted('Cy', UID_X),
                                     granted('Di', UID_X)], quiet.append)
    check('a request with no uid or a malformed one binds nothing, and one '
          'uid granted twice holds the first', (bind, drop),
          ({UID_X: 'Cy'}, []))


def test_folder(blob):
    print('\na folder galaxy carries no seat map')
    root = tempfile.mkdtemp(prefix='joinseat_')
    try:
        store = TurnStore(root)
        store.start(blob, list(ROSTER), turn_seconds=1800,
                    turn=turn_store.turn_of(blob))
        store.request_join({'name': 'Joiner', 'uid': UID_X,
                            'build': '0.1.0+dev',
                            'requested_at': time.time(),
                            'requested_turn': store.current()[0]})
        close_turn(store)
        state = store.state()
        check('the join is granted', 'Joiner' in state['civs'], True)
        check('and the uid is recorded under joined',
              (state.get(joins.JOINED_KEY) or {}).get('Joiner', {}).get('uid'),
              UID_X)
        check('and no seat map was written into the state',
              joins.SEATS_KEY in state, False)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── on the emulator, with the relay in process ───────────────────────────────
def run_emulator(blob):
    import test_relay_function as rf
    from http.server import ThreadingHTTPServer
    from firebase_store import FirebaseTurnStore
    from turn_store import HttpTurnStore

    galaxy = f'joinseat_{int(time.time())}'
    ref = FirebaseTurnStore(rf.PROJECT, galaxy, bucket=rf.BUCKET)
    ref.start(blob, list(ROSTER), turn_seconds=1800,
              turn=turn_store.turn_of(blob))
    uid_a, token_a = rf.sign_up()
    uid_x, token_x = rf.sign_up()
    uid_y, token_y = rf.sign_up()
    uid_z, token_z = rf.sign_up()
    uid_w, token_w = rf.sign_up()
    # A holds DemoPlayer. Z holds a seat left over from a reclaimed `Ghost`,
    # the state a reclaim leaves: the roster no longer names it and the seat
    # map still does. First use is off, the default.
    ref.doc.set({'seats': {uid_a: 'DemoPlayer', uid_z: 'Ghost'}}, merge=True)

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), rf.RelayHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}/{galaxy}'

    def player(token):
        return HttpTurnStore(base, token=lambda: token, state_seconds=0)

    def seats():
        return dict((ref.doc.get().to_dict() or {}).get('seats') or {})

    try:
        turn = ref.current()[0]
        x, y, w = player(token_x), player(token_y), player(token_w)
        print('\nbefore the boundary')
        x.request_join({'name': 'Joiner', 'build': '0.1.0+dev'})
        w.request_join({'name': 'Ghost', 'build': '0.1.0+dev'})
        check('two joins are waiting',
              sorted(r['key'] for r in ref.join_requests()),
              sorted([uid_x, uid_w]))
        # The control. Fails if X could already play, which would make the
        # check after the boundary say nothing about the join.
        refused = rf.refusal(x.submit, 'Joiner', turn, rf.make_blob(turn))
        check('X cannot play Joiner before it exists', rf.status_of(refused),
              403)
        check('and nothing landed', ref.submission('Joiner', turn), None)

        print('\nthe boundary')
        new_turn, lines = close_turn(ref)
        check('the turn advanced', new_turn, turn + 1)
        state = ref.state()
        check('both joiners are on the roster',
              [c for c in state['civs'] if c not in ROSTER],
              ['Joiner', 'Ghost'])
        held = seats()
        check('X holds Joiner', held.get(uid_x), 'Joiner')
        check('W holds Ghost', held.get(uid_w), 'Ghost')
        check('the leftover Ghost seat is gone', uid_z in held, False)
        check('A still holds DemoPlayer', held.get(uid_a), 'DemoPlayer')
        check('and nothing else is seated', len(held), 3)
        check('the seat map is not in the store\'s state',
              joins.SEATS_KEY in state, False)
        check('and the referee log names the binding',
              any(f'seated Joiner on sign-in {uid_x}' in line
                  for line in lines), True)
        check('X is told it was granted',
              (ref.join_answer(uid_x) or {}).get('outcome'), joins.GRANTED)

        print('\nafter it, with first use off')
        mine = rf.make_blob(new_turn, b'x')
        x.submit('Joiner', new_turn, mine)
        check('X plays Joiner with no operator bind',
              ref.submission('Joiner', new_turn), mine)
        refused = rf.refusal(y.submit, 'Joiner', new_turn,
                             rf.make_blob(new_turn, b'y'))
        check('a different sign-in is refused Joiner', rf.status_of(refused),
              403)
        check('and X\'s submission stands', ref.submission('Joiner', new_turn),
              mine)
        refused = rf.refusal(player(token_z).submit, 'Ghost', new_turn,
                             rf.make_blob(new_turn, b'z'))
        check('the sign-in whose Ghost was reclaimed cannot play the new '
              'Ghost', rf.status_of(refused), 403)
        check('and nothing of theirs landed', ref.submission('Ghost', new_turn),
              None)

        print('\nand with first use on')
        ref.doc.set({'seat_claim': 'first-use'}, merge=True)
        refused = rf.refusal(y.submit, 'Joiner', new_turn,
                             rf.make_blob(new_turn, b'y'))
        check('a stranger still cannot claim the joiner\'s civ',
              rf.status_of(refused), 403)
        check('because it is held', 'already held' in rf.body_of(refused),
              True)
        check('and the seat did not move', seats().get(uid_x), 'Joiner')
        check('and X\'s submission stands', ref.submission('Joiner', new_turn),
              mine)
    finally:
        httpd.shutdown()
        httpd.server_close()
        n = ref.delete_everything()
        print(f'  removed {n} object(s) and document(s) from {galaxy}')


def main():
    blob = sp.load_any(GALAXY)
    test_rules()
    test_folder(blob)
    if os.environ.get('FIRESTORE_EMULATOR_HOST'):
        run_emulator(blob)
    else:
        print('\nemulator: SKIPPED, no FIRESTORE_EMULATOR_HOST. This run does '
              'not say whether a Firebase join binds its seat.')
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for name in FAIL:
        print(f'  failed: {name}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
