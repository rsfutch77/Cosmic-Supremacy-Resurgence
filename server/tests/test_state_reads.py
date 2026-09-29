"""
test_state_reads.py , one galaxy state read per poll, counted at the service
============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_state_reads.py

docs\\Public_Beta_Plan.md H11. The relay's request log showed a launcher make
five `GET /<galaxy>/state` requests inside one second each time it began
following a galaxy. Traced to `release/launcher.py` asking `exists`, `state`
and `civs` before it starts the loop, `_refresh_turn` asking `current` once
the loop is running, and the loop's own first `current`: five accessors, each
a request, each a relay invocation and a Firestore document read.

`HttpTurnStore` now answers every state accessor asked inside `state_seconds`
from one `/state` answer. What has to be true of that is counted here at the
service rather than reasoned about: a burst of accessors is one request, a
write this store makes is read back at once, a write made elsewhere is seen
once the window has passed, and every read the turn loop schedules still
reaches the service.

No client and no Firebase: `turn_server.py` in front of a temporary directory,
a real `HttpTurnStore`, and the real `player_turn.follow` with the client
stubbed out.
"""
import collections
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'), os.path.join(ROOT, 'server', 'dev_tools'),
          HERE):
    if d not in sys.path:
        sys.path.insert(0, d)

import player_turn                                              # noqa: E402
import turn_server                                              # noqa: E402
import turn_store                                               # noqa: E402
from turn_store import HttpTurnStore, TurnStore                 # noqa: E402
from test_store_equivalence import make_blob                    # noqa: E402

PASS, FAIL = [], []
TURN = 7


def check(name, got, want):
    ok = want(got) if callable(want) else got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}: {got!r}')
    if not ok and not callable(want):
        print(f'          wanted {want!r}')


class Service:
    """`turn_server.py` over a fresh galaxy, counting every request by path.

    `hold` is an Event a `/state` request waits on before it answers, so a
    case can put a write in the middle of a read.
    """

    def __init__(self, turn_seconds=1800):
        self.root = tempfile.mkdtemp()
        TurnStore(self.root).start(make_blob(TURN), ['DemoPlayer', 'Neighbor'],
                                   turn_seconds=turn_seconds)
        self.hits = collections.Counter()
        self.hold = None
        self.reading = threading.Event()
        svc = self

        class Counting(turn_server.Handler):
            def do_GET(self):
                path = self.path.split('?')[0]
                svc.hits[('GET', path)] += 1
                if path == '/state' and svc.hold is not None:
                    # Read the answer first, then wait, so what comes back is
                    # the galaxy as it was before anything done meanwhile.
                    body = json.dumps(turn_server.STORE.state()).encode()
                    svc.reading.set()
                    svc.hold.wait(10)
                    return self._send(200, 'application/json', body)
                return super().do_GET()

            def do_POST(self):
                svc.hits[('POST', self.path.split('?')[0])] += 1
                return super().do_POST()

        self.kept = turn_server.STORE, turn_server.VERBOSE
        turn_server.VERBOSE = False
        self.httpd = turn_server.serve(self.root, '127.0.0.1', 0)
        self.httpd.RequestHandlerClass = Counting
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.httpd.server_address[1]}'

    @property
    def state_reads(self):
        return self.hits[('GET', '/state')]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        turn_server.STORE, turn_server.VERBOSE = self.kept
        shutil.rmtree(self.root, ignore_errors=True)


def test_a_burst_of_accessors_is_one_request():
    """What `start_multiplayer` and `_refresh_turn` ask, in the order they ask.

    Fails with nine requests where there should be one if the accessors each
    fetch `/state` for themselves again.
    """
    print('a burst of state accessors')
    svc = Service()
    try:
        store = HttpTurnStore(svc.base)
        store.exists()
        state = store.state()
        store.civs()
        store.current()
        store.is_closed()
        store.status()
        store.closed_reason()
        store.reclaimed('DemoPlayer')
        store.seconds_left()
        check('nine accessors asked in a row are one /state request',
              svc.state_reads, 1)
        check('and they answer from it', (state['turn'], store.civs()),
              (TURN, ['DemoPlayer', 'Neighbor']))
    finally:
        svc.close()


def test_each_caller_gets_its_own_copy():
    """A caller that edits the dict it was handed does not edit the next one.

    Fails if the window hands out the one parsed object it keeps.
    """
    print('an answer is a copy')
    svc = Service()
    try:
        store = HttpTurnStore(svc.base)
        store.state()['civs'].append('Intruder')
        check('the next caller reads the roster the service gave',
              store.civs(), ['DemoPlayer', 'Neighbor'])
    finally:
        svc.close()


def test_own_writes_are_read_at_once():
    """A write through this store forgets the answer it was holding.

    Fails if the window outlives a write: the publish would be read back as the
    turn before it, which is exactly the answer a caller has just replaced.
    """
    print('reading your own writes')
    svc = Service()
    try:
        store = HttpTurnStore(svc.base)
        check('the turn before', store.current()[0], TURN)
        store.publish(TURN + 1, make_blob(TURN + 1))
        check('a publish through this store is read back at once',
              store.current()[0], TURN + 1)
        # `submit` asks `is_closed` first, which the answer just read serves;
        # the read after it is the one that has to reach the service.
        before = svc.state_reads
        store.submit('DemoPlayer', TURN + 1, make_blob(TURN + 1, b'o'))
        store.current()
        check('and a submission forgets the answer as well',
              svc.state_reads - before, 1)
    finally:
        svc.close()


def test_a_write_elsewhere_is_seen_after_the_window():
    """The referee writes where the galaxy lives, not through this store.

    The bound that the window costs, stated as a check: inside it a turn the
    referee has published is not seen yet, and once it has passed the next
    accessor asks the service again. Fails in the second half if an answer is
    kept past its window, and in the first half if the window is not there.
    """
    print('a write made somewhere else')
    svc = Service()
    try:
        store = HttpTurnStore(svc.base, state_seconds=0.5)
        check('the turn before', store.current()[0], TURN)
        TurnStore(svc.root).publish(TURN + 1, make_blob(TURN + 1))
        check('inside the window the answer held is the one given',
              store.current()[0], TURN)
        time.sleep(0.6)
        check('after it, the referee published turn is read',
              store.current()[0], TURN + 1)
        check('in two requests', svc.state_reads, 2)
        zero = HttpTurnStore(svc.base, state_seconds=0)
        before = svc.state_reads
        zero.current()
        zero.current()
        check('a window of 0 asks the service every time',
              svc.state_reads - before, 2)
    finally:
        svc.close()


def test_a_read_across_a_write_is_not_kept():
    """An answer fetched while a write was in flight may predate the write.

    The launcher reads the state on its Tk thread while the turn loop submits
    on its own, so this interleaving is ordinary. Fails if the window keeps the
    older answer, which would then be served for the rest of it.
    """
    print('a read that overlaps a write')
    svc = Service()
    try:
        store = HttpTurnStore(svc.base, state_seconds=30.0)
        svc.hold = threading.Event()
        got = {}
        reader = threading.Thread(
            target=lambda: got.__setitem__('turn', store.current()[0]))
        reader.start()
        svc.reading.wait(5)
        store.publish(TURN + 1, make_blob(TURN + 1))
        svc.hold.set()
        reader.join(5)
        svc.hold = None
        check('the overlapping read answers with what it fetched',
              got.get('turn'), TURN)
        check('and the next read asks again rather than keeping it',
              store.current()[0], TURN + 1)
    finally:
        svc.close()


def test_the_window_is_under_the_loop_floor():
    """The window is only safe while it is shorter than the loop's floor.

    Fails if either constant moves so that a read the loop schedules could be
    answered from memory.
    """
    print('the window and the floor')
    check('STATE_SECONDS is under player_turn.POLL_FLOOR',
          turn_store.STATE_SECONDS < player_turn.POLL_FLOOR, True)
    check('and a store built without one takes it',
          HttpTurnStore('http://127.0.0.1:1').state_seconds,
          turn_store.STATE_SECONDS)


def test_a_follow_poll_is_one_request():
    """The launcher's start, then the real loop, against the real service.

    H11's done-when, measured: the reads before the turn is fetched are one
    request rather than five, and each read the loop schedules at least a
    floor after the one before it reaches the service. A few real seconds,
    because the window runs on the real clock.
    """
    print('following a galaxy')
    svc = Service(turn_seconds=4)
    store = HttpTurnStore(svc.base)
    order = []
    real_get = store._get

    def spy(path, want_json=True):
        order.append(path)
        return real_get(path, want_json)
    store._get = spy

    # start_multiplayer, then _refresh_turn once the loop is running.
    store.exists()
    store.state()
    store.civs()
    store.current()

    trace = []
    real_current = store.current

    def current():
        before = svc.state_reads
        got = real_current()
        trace.append((time.monotonic(), svc.state_reads > before))
        return got
    store.current = current

    saved = (player_turn.serve, player_turn.collect, player_turn.close,
             player_turn.carries_orders, player_turn.describe_orders,
             player_turn.sp)
    player_turn.serve = lambda blob, civ, **kw: 'x.dat'
    player_turn.collect = lambda *a, **k: make_blob(TURN, b'o')
    player_turn.close = lambda: None
    player_turn.carries_orders = lambda *a: True
    player_turn.describe_orders = lambda *a: 'summary'
    player_turn.sp = types.SimpleNamespace(load_any=lambda b: b)
    t0 = time.monotonic()
    try:
        player_turn.follow(
            store, 'DemoPlayer', poll=player_turn.POLL_FLOOR, rounds=0,
            capture_every=1.0, uploads_per_turn=2,
            stop=lambda: time.monotonic() - t0 >= 9.0, log=lambda *a: None)
    finally:
        (player_turn.serve, player_turn.collect, player_turn.close,
         player_turn.carries_orders, player_turn.describe_orders,
         player_turn.sp) = saved
        svc.close()

    first_turn = order.index(f'/turn/{TURN}')
    check('the reads before the turn is fetched are one /state request',
          order[:first_turn].count('/state'), 1)
    scheduled = [net for (t, net), (p, _) in zip(trace[1:], trace)
                 if t - p >= player_turn.POLL_FLOOR]
    check('the loop scheduled several reads a floor apart',
          len(scheduled), lambda n: n >= 3)
    check('and every one of them reached the service',
          all(scheduled), True)


if __name__ == '__main__':
    test_a_burst_of_accessors_is_one_request()
    test_each_caller_gets_its_own_copy()
    test_own_writes_are_read_at_once()
    test_a_write_elsewhere_is_seen_after_the_window()
    test_a_read_across_a_write_is_not_kept()
    test_the_window_is_under_the_loop_floor()
    test_a_follow_poll_is_one_request()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    sys.exit(1 if FAIL else 0)
