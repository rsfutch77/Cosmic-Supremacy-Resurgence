"""
test_views.py: every store serves a caller only its own copy of a turn
======================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_views.py

`turn_store.served_copy` decides which copy of a turn a caller is served. The
folder store and `turn_server.py` both answer through it; the relay's half is
in `test_relay_function.py`, which needs the emulators.

Fixtures: `server/tests/fixtures/diplomacy/d9.b64` (turn 181, tracked), where
both civs have planets stale in their own view, and `d0.b64`, turn 180.
"""
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import inject_civ as icv
import player_turn
import referee
import turn_server
import turn_store
from turn_store import (PUBLIC_VIEW, VIEWS_KEY, HttpTurnStore, NotServed,
                        TurnStore, served_copy)

FIXTURES = os.path.join(ROOT, 'server', 'tests', 'fixtures', 'diplomacy')
GALAXY = os.path.join(FIXTURES, 'd9.b64')
BEFORE = os.path.join(FIXTURES, 'd0.b64')      # turn 180 of the same galaxy
ROSTER = ['GoodGuy', 'BadGuy']
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def refused(fn, *args):
    """'refused' when `fn` raises NotServed, else what it returned."""
    try:
        return fn(*args)
    except NotServed:
        return 'refused'


def run_rule():
    print('the rule')
    state = {'turn': 9, 'civs': ['A', 'B', 'C'],
             VIEWS_KEY: {'8': ['A', PUBLIC_VIEW], '9': ['A', PUBLIC_VIEW]}}
    check('a civ the views name gets its own', served_copy(state, 9, 'A'), 'A')
    check('a roster civ they do not name gets the turn, which is its view',
          served_copy(state, 9, 'B'), None)
    check('a caller off the roster gets the view without news',
          served_copy(state, 9, 'Stranger'), PUBLIC_VIEW)
    check('and so does a caller with no seat', served_copy(state, 9), PUBLIC_VIEW)
    check('the previous turn is served by the same rule while its views are kept',
          (served_copy(state, 8, 'A'), served_copy(state, 8, 'B')), ('A', None))
    check('a turn with no views kept is refused once it is not current',
          refused(served_copy, state, 7, 'A'), 'refused')
    bare = {'turn': 9, 'civs': ['A'], VIEWS_KEY: {'9': []}}
    check('a current turn published with no views is served whole to anyone',
          (served_copy(bare, 9, 'A'), served_copy(bare, 9)), (None, None))
    old = {'turn': 9, 'civs': ['A'], VIEWS_KEY: {'9': ['A']}}
    check('views without a view without news refuse a caller with no seat',
          refused(served_copy, old, 9), 'refused')
    check('a galaxy that never published a view serves its current turn',
          served_copy({'turn': 3, 'civs': ['A']}, 3, 'A'), None)

    print('\nwhat the referee publishes')
    blob = sp.load_any(GALAXY)
    held = referee.views(blob, ROSTER, log=quiet)
    published = referee.published_views(blob, held)
    check('every civ whose view differs, and the view without news',
          sorted(published), sorted(['BadGuy', 'GoodGuy', PUBLIC_VIEW]))
    one = dict(held, GoodGuy=blob)
    check('a civ whose view is the turn is left out',
          sorted(referee.published_views(blob, one)), sorted(['BadGuy', PUBLIC_VIEW]))
    plain = {c: blob for c in held}
    check('and nothing is published when every view is the turn',
          referee.published_views(blob, plain), {})
    return blob, held


def run_folder(tmp, blob, held):
    print('\na folder store')
    store = TurnStore(os.path.join(tmp, 'folder'))
    turn = icv.turn_of(blob)
    store.start(blob, ROSTER + ['Absent'], turn=turn - 1)
    store.publish(turn, blob,
                  views={c: v for c, v in held.items() if c != 'GoodGuy'})
    check('BadGuy is served its view',
          player_turn.served_blob(store, 'BadGuy', turn) == held['BadGuy'], True)
    check('GoodGuy, on the roster and not named, is served the turn',
          player_turn.served_blob(store, 'GoodGuy', turn) == blob, True)
    check('a name off the roster is served the view without news',
          player_turn.served_blob(store, 'Stranger', turn) == held[PUBLIC_VIEW],
          True)
    check('an earlier turn is refused',
          refused(player_turn.served_blob, store, 'BadGuy', turn - 1), 'refused')
    return store


def run_http(tmp, blob, held):
    print('\nturn_server.py')
    root = os.path.join(tmp, 'http')
    os.makedirs(root)
    kept = turn_server.VERBOSE
    turn_server.VERBOSE = False
    httpd = turn_server.serve(root, '127.0.0.1', 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}'
    turn = icv.turn_of(blob)
    try:
        ref = HttpTurnStore(base, state_seconds=0)
        ref.start(sp.load_any(BEFORE), ROSTER, turn_seconds=600)
        n = turn
        ref.publish(n, blob, views=referee.published_views(blob, held))
        under = TurnStore(root)
        check('the views land beside the turn and are named in the state',
              sorted(under.state()[VIEWS_KEY][str(n)]),
              sorted(['BadGuy', 'GoodGuy', PUBLIC_VIEW]))
        check('and hold what was sent',
              under.view_blob('BadGuy', n) == held['BadGuy'], True)
        player = HttpTurnStore(base, state_seconds=0)
        check('a launcher playing BadGuy is served its view',
              player_turn.served_blob(player, 'BadGuy', n) == held['BadGuy'],
              True)
        check('a launcher playing GoodGuy is served its own',
              player_turn.served_blob(player, 'GoodGuy', n) == held['GoodGuy'],
              True)
        check('a caller naming no civ is served the view without news',
              player.served_turn(None, n) == held[PUBLIC_VIEW], True)
        with urllib.request.urlopen(f'{base}/turn/{n}') as r:
            plain = r.read()
        check('and so is a bare GET', plain == held[PUBLIC_VIEW], True)
        check('the referee reads the turn itself',
              ref.turn_blob(n) == blob, True)
        check('an earlier turn published with no views is refused to a player',
              refused(player.served_turn, 'BadGuy', n - 1), 'refused')
        check('and still read whole by the referee',
              ref.turn_blob(n - 1) == sp.load_any(BEFORE), True)

        def post(path, body=b''):
            req = urllib.request.Request(base + path, data=body, method='POST')
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status
            except urllib.error.HTTPError as exc:
                return exc.code
        check('a view filed under a name that is not a civ name is refused',
              post(f'/view/{n}/..%2Fescape', blob), 400)
        check('and a view of another turn',
              post(f'/view/{n + 1}/BadGuy', blob), 400)
        check('and a publish naming a view that was never sent',
              post(f'/turn/{n + 1}?view=Nobody', blob), 400)
        check('neither published anything', under.current()[0], n)
    finally:
        httpd.shutdown()
        httpd.server_close()
        turn_server.VERBOSE = kept


def main():
    blob, held = run_rule()
    with tempfile.TemporaryDirectory() as tmp:
        run_folder(tmp, blob, held)
        run_http(tmp, blob, held)
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
