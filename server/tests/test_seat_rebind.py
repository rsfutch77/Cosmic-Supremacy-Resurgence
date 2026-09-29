"""
test_seat_rebind.py , the operator moves a seat to a new install (J4)
=====================================================================
    python functions/emulators.py --only firestore,storage,auth

    set FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
    set STORAGE_EMULATOR_HOST=http://127.0.0.1:9199
    set FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
    server\\.venv\\Scripts\\python.exe server\\tests\\test_seat_rebind.py

J4's third half. A seat is bound to the anonymous uid that claimed it, and a
player on a new install has a new uid. The run is the story the plan asks for,
with two real sign-ins from the Auth emulator and the relay in process behind
a socket, the way `test_relay_function.py` runs it: A holds `DemoPlayer`, B is
refused it, the operator rebinds it to B with `seat_tool.py`, B plays it, and A
is refused it from then on.

Every refusal checks the seat map afterwards as well as the answer, because a
tool that printed "refused" after writing half a move would otherwise pass. The
referee's own store is the oracle for what a player's submission did.
"""
import io
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (HERE, os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'functions')):
    if d not in sys.path:
        sys.path.insert(0, d)

# Sets the relay's environment and imports it, so it comes first.
import test_relay_function as rf                                # noqa: E402
from http.server import ThreadingHTTPServer                     # noqa: E402

from firebase_store import FirebaseTurnStore                     # noqa: E402
from turn_store import HttpTurnStore                             # noqa: E402
import relay                                                    # noqa: E402
import seat_tool                                                # noqa: E402

PASS, FAIL = [], []

TURN = rf.TURN
FROM_A = rf.make_blob(TURN, b'a')
FROM_B = rf.make_blob(TURN, b'b')


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def tool(*argv):
    """(exit code, printed text) from the command line entry."""
    out = io.StringIO()
    code = seat_tool.main(list(argv), out=out)
    return code, out.getvalue()


def main():
    if not os.environ.get('FIRESTORE_EMULATOR_HOST'):
        print('SKIPPED: no FIRESTORE_EMULATOR_HOST. This run says nothing '
              'about seat rebinding.')
        return 0

    galaxy = f'rebind_{int(time.time())}'
    spec = f'firebase://{rf.PROJECT}/{galaxy}?bucket={rf.BUCKET}'
    referee = FirebaseTurnStore(rf.PROJECT, galaxy, bucket=rf.BUCKET)
    referee.start(rf.BLOB7, ['DemoPlayer', 'Neighbor', 'Player.One', 'Fourth'],
                  turn_seconds=1800)

    uid_c, _ = rf.sign_up()
    uid_d, _ = rf.sign_up()
    uid_a, token_a = rf.sign_up()
    uid_b, token_b = rf.sign_up()
    # First use on, which is how the two measured halves ran: B is refused
    # because A holds the civ, not because B may not claim anything.
    referee.doc.set({'seat_claim': 'first-use',
                     'seats': {uid_a: 'DemoPlayer', uid_c: 'Neighbor',
                               uid_d: 'Player.One'}}, merge=True)

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), rf.RelayHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}/{galaxy}'
    player_a = HttpTurnStore(base, token=lambda: token_a)
    player_b = HttpTurnStore(base, token=lambda: token_b)
    try:
        run(referee, galaxy, spec, player_a, player_b,
            uid_a, uid_b, uid_c, uid_d, token_b)
    finally:
        httpd.shutdown()
        httpd.server_close()
        n = referee.delete_everything()
        print(f'  removed {n} object(s) and document(s) from {galaxy}')

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for name in FAIL:
        print(f'  failed: {name}')
    return 1 if FAIL else 0


def seat_map(referee):
    return dict((referee.doc.get().to_dict() or {}).get('seats') or {})


def run(referee, galaxy, spec, player_a, player_b,
        uid_a, uid_b, uid_c, uid_d, token_b):
    before = seat_map(referee)

    print('the install that claimed it, and a second install')
    player_a.submit('DemoPlayer', TURN, FROM_A)
    check('A plays DemoPlayer', referee.submission('DemoPlayer', TURN), FROM_A)
    refused = rf.refusal(player_b.submit, 'DemoPlayer', TURN, FROM_B)
    check('B is refused DemoPlayer', rf.status_of(refused), 403)
    check('and says the seat is held rather than something else',
          'already held' in rf.body_of(refused), True)
    check('and A\'s submission is the one on the store',
          referee.submission('DemoPlayer', TURN), FROM_A)
    check('and the seat map did not move', seat_map(referee), before)

    print('what the operator is refused')
    # Each of these fails if the tool wrote before deciding. The seat map is
    # compared whole after every one.
    cases = [
        ('a civ not on the roster', ('Nobody', uid_b), 'not a civ'),
        ('a civ spelled in the wrong case, with the right name offered',
         ('demoplayer', uid_b), 'the roster has DemoPlayer'),
        ('a civ nobody holds', ('Fourth', uid_b), 'no seat to move'),
        ('a target that already holds another seat',
         ('DemoPlayer', uid_c), 'already plays Neighbor'),
        ('a target that is not a uid', ('DemoPlayer', 'seats.x'),
         'is not a Firebase uid'),
        ('a target Firebase Auth has never heard of',
         ('DemoPlayer', 'NoSuchUid0000000000000000000'), 'has no user'),
    ]
    for label, (civ, uid), says in cases:
        code, text = tool(spec, 'rebind', civ, uid)
        check(f'{label}: refused', code, 2)
        check(f'{label}: and says why', says in text, True)
        check(f'{label}: and the seat map is unchanged',
              seat_map(referee), before)
    code, text = tool(spec, 'rebind', 'DemoPlayer', uid_b, '--dry-run')
    check('a dry run says what it would do',
          code == 0 and 'would move DemoPlayer' in text and uid_a in text,
          True)
    check('and writes nothing', seat_map(referee), before)
    code, text = tool(f'firebase://{rf.PROJECT}/no_such_galaxy_{int(time.time())}',
                      'list')
    check('a galaxy that does not exist is refused, not created',
          code == 2 and 'there is no galaxy' in text, True)

    print('how the operator finds the new uid')
    code, text = tool(spec, 'candidates', '--limit', '500')
    rows = seat_tool.candidates(seat_tool.open_galaxy(spec))
    uids = [r['uid'] for r in rows]
    # B signed up last, so it is the newest unseated sign-in. Fails if the
    # listing were not ordered by activity or left seated uids in.
    check('B is the most recently active unseated sign-in',
          uids[:1], [uid_b])
    check('and no seated sign-in is offered',
          any(u in uids for u in (uid_a, uid_c, uid_d)), False)
    check('the printed listing names B', uid_b in text, True)
    check('and no refresh token or ID token appears in it',
          token_b in text, False)

    print('the operator rebinds DemoPlayer to B')
    code, text = tool(spec, 'rebind', 'DemoPlayer', uid_b)
    check('the rebind succeeds', code, 0)
    check('and says where the seat went from and to',
          f'from {uid_a} to {uid_b}' in text, True)
    after = seat_map(referee)
    check('B holds DemoPlayer', after.get(uid_b), 'DemoPlayer')
    check('A holds nothing', uid_a in after, False)
    check('every other seat is where it was',
          {u: c for u, c in after.items() if u != uid_b},
          {u: c for u, c in before.items() if u != uid_a})
    moved = (referee.doc.get().to_dict() or {}).get(seat_tool.REBOUND_KEY) or {}
    check('the move is recorded on the document',
          (moved.get('DemoPlayer') or {}).get('from'), uid_a)
    state = relay.handle('GET', f'/{galaxy}/state',
                         {'Authorization': f'Bearer {token_b}'})
    check('and the record is not served on the galaxy\'s public face',
          seat_tool.REBOUND_KEY.encode() in state[2], False)

    print('after the rebind')
    player_b.submit('DemoPlayer', TURN, FROM_B)
    check('B plays DemoPlayer', referee.submission('DemoPlayer', TURN), FROM_B)
    refused = rf.refusal(player_a.submit, 'DemoPlayer', TURN, FROM_A)
    check('A is refused DemoPlayer', rf.status_of(refused), 403)
    check('and B\'s submission still stands',
          referee.submission('DemoPlayer', TURN), FROM_B)
    check('and A did not win the seat back by asking',
          seat_map(referee).get(uid_b), 'DemoPlayer')

    code, text = tool(spec, 'rebind', 'DemoPlayer', uid_b)
    check('rebinding to the uid that already holds it changes nothing',
          code == 0 and 'nothing changed' in text, True)
    check('and the map is as it was', seat_map(referee), after)

    print('a civ whose name holds a dot')
    # Fails if the name were joined into a field path, which would write
    # `seats_rebound.Player` and a nested `One` rather than the civ's record.
    code, text = tool(spec, 'rebind', 'Player.One', uid_a)
    check('the rebind succeeds', code, 0)
    doc = referee.doc.get().to_dict() or {}
    check('A holds Player.One', (doc.get('seats') or {}).get(uid_a),
          'Player.One')
    check('the record is filed under the whole name',
          sorted((doc.get(seat_tool.REBOUND_KEY) or {}).keys()),
          ['DemoPlayer', 'Player.One'])

    print('binding a civ nobody holds')
    # A civ seeded into the roster by hand, or one a join left unseated, gets
    # its seat this way on a galaxy with first-use off.
    uid_e, _ = rf.sign_up()
    held = seat_map(referee)
    code, text = tool(spec, 'bind', 'DemoPlayer', uid_e)
    check('bind refuses a civ somebody holds',
          code == 2 and f'held by {uid_b}' in text, True)
    check('and moves nothing', seat_map(referee), held)
    code, text = tool(spec, 'bind', 'Fourth', uid_e, '--dry-run')
    check('a dry run of a bind writes nothing',
          code == 0 and 'from nobody' in text and seat_map(referee) == held,
          True)
    code, text = tool(spec, 'bind', 'Fourth', uid_e)
    check('bind seats a civ nobody held', code == 0 and 'seated Fourth' in text,
          True)
    check('and only that seat was added',
          seat_map(referee), dict(held, **{uid_e: 'Fourth'}))
    check('and the record says it came from nobody',
          ((referee.doc.get().to_dict() or {}).get(seat_tool.REBOUND_KEY)
           or {}).get('Fourth', {}).get('from', 'missing'), None)

    code, text = tool(spec, 'list')
    check('list shows each civ with its uid',
          f'DemoPlayer       {uid_b}' in text and f'Player.One       {uid_a}'
          in text, True)
    check('and which seats were moved', text.count('rebound'), 3)
    check('and the civ that was bound', f'Fourth           {uid_e}' in text,
          True)

    print('the operator view reads the same document, and only reads it')
    import operator_view as ov
    referee.update_state({ov.WORKER_SEEN_KEY: time.time() - 30,
                          ov.WORKER_FAILURES_KEY: 2})
    stamp = referee.doc.get().update_time
    r = ov.galaxy_report(referee)
    rows = {row['civ']: row['uid'] for row in r['seats'] or []}
    check('the view shows each seat as the tool left it',
          rows, {'DemoPlayer': uid_b, 'Neighbor': uid_c, 'Player.One': uid_a,
                 'Fourth': uid_e})
    check('and the heartbeat a Firebase store\'s state leaves out',
          r['worker']['failures'], 2)
    check('and calls the failures a problem',
          any('failed 2 time(s)' in p for p in r['problems']), True)
    check('and the document was not written by reading it',
          referee.doc.get().update_time, stamp)


if __name__ == '__main__':
    sys.exit(main())
