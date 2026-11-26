"""
test_replay.py: every store serves a civ its own view of any turn it played
===========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_replay.py

The launcher's replay asks for one turn at a time (`release/replay_endpoint.py`).
Every store keeps each turn's views and answers through
`turn_store.replay_view`: the civ's own view, never the turn itself, of a turn
up to the current one and not before the civ was seated. The folder store and
`turn_server.py` run here; the relay runs too when the emulators are up
(`test_relay_function.py` says how), and is skipped otherwise.

Fixtures: `server/tests/fixtures/diplomacy/d0.b64` (turn 180) and `d9.b64`
(turn 181), published here as turns 180 to 183.
"""
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'server', 'tests'),
          os.path.join(ROOT, 'functions')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import referee
import turn_server
import turn_store
from turn_store import (JOINED_KEY, PUBLIC_VIEW, VIEWS_KEY, HttpTurnStore,
                        NotServed, TurnStore, replay_problem)

FIXTURES = os.path.join(ROOT, 'server', 'tests', 'fixtures', 'diplomacy')
FIRST = sp.load_any(os.path.join(FIXTURES, 'd0.b64'))
BLOB = sp.load_any(os.path.join(FIXTURES, 'd9.b64'))
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


def why(fn, *args):
    """The NotServed reason when `fn` raises it, else 'served'."""
    try:
        fn(*args)
    except NotServed as exc:
        return str(exc)
    return 'served'


def held_views():
    held = referee.views(BLOB, ROSTER, log=quiet)
    return held, referee.published_views(BLOB, held)


def publish_three(store, published):
    """Turns 181 to 183 over a first turn 180. 182 names no GoodGuy view, so
    its turn is GoodGuy's served copy and the replay has nothing for it."""
    store.start(FIRST, ROSTER, turn=180)
    store.publish(181, BLOB, views=published)
    store.publish(182, BLOB, views={c: v for c, v in published.items()
                                    if c != 'GoodGuy'})
    store.publish(183, BLOB, views=published)


def run_rule():
    print('the rule')
    state = {'turn': 9, 'civs': ['A', 'B'], JOINED_KEY: {'B': {'turn': 6}}}
    check('a roster civ may replay any played turn',
          (replay_problem(state, 'A', 1), replay_problem(state, 'A', 9)),
          ('', ''))
    check('a turn not yet played is refused',
          'has not been played' in replay_problem(state, 'A', 10))
    check('and turn 0', 'has not been played' in replay_problem(state, 'A', 0))
    check('a civ off the roster is refused',
          'on the roster' in replay_problem(state, 'Stranger', 5))
    check('and a caller with no civ',
          'on the roster' in replay_problem(state, None, 5))
    check('a civ that joined is refused the turns before its seat',
          replay_problem(state, 'B', 5), 'B was seated at turn 6')
    check('and served from that turn on', replay_problem(state, 'B', 6), '')


def run_folder(tmp, held, published):
    print('\na folder store')
    store = TurnStore(os.path.join(tmp, 'folder'))
    publish_three(store, published)
    check('the state still names only the last two turns\' views',
          sorted(store.state()[VIEWS_KEY]), ['182', '183'])
    check('and turn 181\'s views stay on disk',
          store.kept_view('BadGuy', 181) == held['BadGuy'])
    check('BadGuy replays turn 181 as its own view',
          store.replay_view('BadGuy', 181) == held['BadGuy'])
    check('and the current turn',
          store.replay_view('BadGuy', 183) == held['BadGuy'])
    check('a turn with no view for the civ is refused, never served whole',
          why(store.replay_view, 'GoodGuy', 182),
          'turn 182 is not kept for GoodGuy')
    check('the view without news is no civ\'s replay',
          'on the roster' in why(store.replay_view, PUBLIC_VIEW, 181))
    check('a turn past the current one is refused',
          'has not been played' in why(store.replay_view, 'BadGuy', 184))
    store.update_state({JOINED_KEY: {'BadGuy': {'turn': 182}}})
    check('a civ seated at 182 is refused 181',
          why(store.replay_view, 'BadGuy', 181), 'BadGuy was seated at turn 182')
    check('and replays 183', store.replay_view('BadGuy', 183) == held['BadGuy'])


def run_http(tmp, held, published):
    print('\nturn_server.py')
    root = os.path.join(tmp, 'http')
    os.makedirs(root)
    kept = turn_server.VERBOSE
    turn_server.VERBOSE = False
    httpd = turn_server.serve(root, '127.0.0.1', 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}'
    try:
        # Written beside the server, since `turn_server` takes a view only
        # of the turn its blob carries, and every turn here is one blob.
        publish_three(TurnStore(root), published)
        player = HttpTurnStore(base, state_seconds=0)
        check('a launcher replays its civ\'s view of turn 181',
              player.replay_view('BadGuy', 181) == held['BadGuy'])
        check('a turn with no view for the civ is refused with the reason',
              why(player.replay_view, 'GoodGuy', 182),
              'turn 182 is not kept for GoodGuy')
        TurnStore(root).update_state({JOINED_KEY: {'GoodGuy': {'turn': 183}}})
        check('and a turn before the civ was seated',
              why(player.replay_view, 'GoodGuy', 181),
              'GoodGuy was seated at turn 183')
    finally:
        httpd.shutdown()
        httpd.server_close()
        turn_server.VERBOSE = kept


def run_relay(held, published):
    print('\nthe relay')
    if not os.environ.get('FIRESTORE_EMULATOR_HOST'):
        print('  SKIPPED: no FIRESTORE_EMULATOR_HOST. This run says nothing '
              'about the relay.')
        return
    import time
    from http.server import ThreadingHTTPServer
    os.environ.setdefault('CS_DATA_DIR', tempfile.mkdtemp(prefix='cs_replay_'))
    import test_relay_function as trf
    from firebase_store import FirebaseTurnStore

    def account(uid):
        return trf.craft_token(sub=uid, user_id=uid,
                               firebase={'sign_in_provider': 'google.com'})

    stamp = int(time.time())
    galaxy = f'replay_{stamp}'
    store = FirebaseTurnStore(trf.PROJECT, galaxy, bucket=trf.BUCKET)
    publish_three(store, published)
    good, bad, none = f'g{stamp}', f'b{stamp}', f'n{stamp}'
    store.doc.set({'seats': {good: 'GoodGuy', bad: 'BadGuy'}}, merge=True)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), trf.RelayHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}/{galaxy}'
    try:
        as_bad = HttpTurnStore(base, token=lambda: account(bad))
        as_good = HttpTurnStore(base, token=lambda: account(good))
        stranger = HttpTurnStore(base, token=lambda: account(none))
        check('every view object stays after two more publishes',
              store.has_kept_view('BadGuy', 181))
        check('a seat replays its own view of turn 181',
              as_bad.replay_view('BadGuy', 181) == held['BadGuy'])
        check('the civ named in the query is ignored for the seat\'s own',
              as_bad.replay_view('GoodGuy', 181) == held['BadGuy'])
        check('a turn with no view for the seat is refused, never served whole',
              why(as_good.replay_view, 'GoodGuy', 182),
              'turn 182 is not kept for GoodGuy')
        check('a sign-in with no seat is refused',
              'only to a seat' in why(stranger.replay_view, 'GoodGuy', 181))
        store.update_state({JOINED_KEY: {'BadGuy': {'turn': 183}}})
        check('a seat is refused the turns before it was seated',
              why(as_bad.replay_view, 'BadGuy', 182),
              'BadGuy was seated at turn 183')
    finally:
        httpd.shutdown()
        httpd.server_close()
        print(f'  removed {store.delete_everything()} object(s) and '
              f'document(s) from {galaxy}')


def main():
    run_rule()
    held, published = held_views()
    with tempfile.TemporaryDirectory() as tmp:
        run_folder(tmp, held, published)
        run_http(tmp, held, published)
    run_relay(held, published)
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
