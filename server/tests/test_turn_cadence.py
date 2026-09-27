"""
test_turn_cadence.py , captures are cheap, uploads are not, so count both
=========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_turn_cadence.py

`follow` used to capture and submit on one 20-second cadence. The two halves
cost different things: a capture is a SaveGame and a local write, an upload is
a Cloud Storage Class A operation, and docs\\Public_Beta_Plan.md H5 measures
those as the binding quota for the whole beta. So they now run on two cadences,
and what has to be true of them is a matter of counts rather than of one
interval firing.

A cadence is easy to test vacuously. One interval proves nothing about two, so
every case here drives a fake clock across a whole turn and asserts how many
captures happened against how many uploads did, plus that the last capture of
the turn reaches the store before the turn closes wherever the upload cadence
happened to fall.

The guards that predate this are checked again in the same harness, because
they are what a rewrite of this loop is most likely to lose: a turn already
submitted for is resumed rather than re-served, and a capture carrying no
orders never overwrites a submission this loop did not write. See
test_loop_guards.py for the incidents behind both.

No client, no store on disk and no real time: the clock, the client and the
store are all fakes, so the loop under test is the only real thing here.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'), os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import player_turn                                              # noqa: E402

PASS, FAIL = [], []


def check(name, got, want):
    ok = want(got) if callable(want) else got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}: {got!r}')
    if not ok and not callable(want):
        print(f'          wanted {want!r}')


# ── the fakes ────────────────────────────────────────────────────────────────
class Clock:
    """Time the loop is given, which only moves when the loop sleeps."""

    def __init__(self, start=1000.0):
        self.t = float(start)

    def now(self):
        return self.t

    def sleep(self, seconds):
        # A zero-length sleep still has to move, or a nap whose remainder
        # rounds to nothing spins forever against a clock that never advances.
        self.t += max(float(seconds), 0.01)


class Store:
    """Just enough of a TurnStore for one turn, recording every write."""

    def __init__(self, clock, turn=7, seconds=600.0, blob=b'BASE' * 64):
        self.clock = clock
        self.turn = turn
        self.seconds = seconds
        self.deadline = clock.now() + seconds
        self.blob = blob
        self.subs = {}
        self.writes = []                # (when, blob) per submit that landed

    def current(self):
        return self.turn, self.deadline

    def seconds_left(self):
        return self.deadline - self.clock.now()

    def turn_blob(self, turn):
        return self.blob

    def submission(self, civ, turn):
        return self.subs.get((civ, turn))

    def submit(self, civ, turn, blob):
        self.subs[(civ, turn)] = blob
        self.writes.append((self.clock.now() - (self.deadline - self.seconds),
                            blob))

    def note(self, civ, turn):
        return []


class Ask:
    """A player pressing Save, which is a threading.Event in the launcher.

    `at` is when they press it, or several times if they press it more than
    once. A press that has not happened yet is not cleared by the loop tidying
    up after a turn, which is what makes a press made between turns reach the
    loop the way a real Event would.
    """

    def __init__(self, clock, at=None):
        self.clock = clock
        self.times = ([] if at is None else
                      [at] if isinstance(at, (int, float)) else list(at))

    def is_set(self):
        return bool(self.times) and self.clock.now() >= self.times[0]

    def clear(self):
        if self.is_set():
            self.times.pop(0)


def run_turn(capture_every=5.0, uploads_per_turn=2, submit_every=None,
             seconds=600.0, edits_at=(), close_at=None, ask_at=None,
             prior=None, orders=True, poll=2.0):
    """One whole turn against a fake clock. Returns what it did.

    `edits_at` is when the player changes something, in seconds from the start
    of the turn, so that captures taken after one differ from captures before
    it. `close_at` is the player closing the game window, after which no
    capture can be taken at all.
    """
    clock = Clock()
    store = Store(clock, seconds=seconds)
    start = clock.now()
    base = store.blob
    if prior is not None:
        store.subs[('DemoPlayer', store.turn)] = prior
        store.writes.clear()            # a submission that was already there

    captures, served, states = [], [], []

    def player_state(now):
        done = sum(1 for e in edits_at if now - start >= e)
        return base + b'ORDER' * done

    def fake_collect(name, save_dir=None, log=print, announce=True):
        now = clock.now()
        if close_at is not None and now - start >= close_at:
            raise SystemExit('SaveGame did not report success')
        blob = player_state(now)
        captures.append((now - start, blob))
        return blob

    saved = (player_turn.serve, player_turn.collect, player_turn.close,
             player_turn.carries_orders, player_turn.describe_orders,
             player_turn.sp)
    player_turn.serve = lambda b, civ, **kw: served.append(b) or 'x.dat'
    player_turn.collect = fake_collect
    player_turn.close = lambda: None
    player_turn.carries_orders = lambda served_b, sub, civ: orders
    player_turn.describe_orders = lambda *a: 'summary'
    # `collect` hands back a path that `sp.load_any` reads. The fake hands back
    # the bytes themselves, so loading one is the identity.
    player_turn.sp = types.SimpleNamespace(load_any=lambda b: b)
    try:
        player_turn.follow(
            store, 'DemoPlayer', poll=poll, rounds=1,
            on_state=lambda kind, **f: states.append((clock.now() - start, kind, f)),
            capture_every=capture_every, uploads_per_turn=uploads_per_turn,
            submit_every=submit_every, send_now=Ask(clock, ask_at),
            log=lambda *a: None, clock=clock.now, sleep=clock.sleep)
    finally:
        (player_turn.serve, player_turn.collect, player_turn.close,
         player_turn.carries_orders, player_turn.describe_orders,
         player_turn.sp) = saved
    return types.SimpleNamespace(store=store, captures=captures,
                                 uploads=store.writes, served=served,
                                 states=states, clock=clock, start=start,
                                 final=player_state(clock.now()))


# ── the counts ───────────────────────────────────────────────────────────────
def test_two_cadences():
    """A capture every 5s and two uploads across a ten-minute turn.

    Fails if the two halves share one interval, in either direction: equal
    counts means nothing was separated, and 30 uploads is the old 20-second
    submit still running the show.
    """
    print('two cadences across one turn')
    # A player doing something every minute, so no capture is a repeat of the
    # one before it and nothing is skipped for being identical.
    r = run_turn(capture_every=5.0, uploads_per_turn=2, seconds=600.0,
                 edits_at=tuple(range(60, 600, 60)))
    check('captures land on the capture cadence, not the upload one',
          len(r.captures), lambda n: 115 <= n <= 121)
    check('and the turn is uploaded exactly twice', len(r.uploads), 2)
    check('the interim upload is at the halfway point',
          round(r.uploads[0][0]), lambda s: 295 <= s <= 310)
    check('the last upload is at the deadline and not before',
          round(r.uploads[-1][0]), lambda s: 600 <= s <= 605)
    check('captures outnumber uploads by the ratio of the two intervals',
          len(r.captures) // len(r.uploads), lambda n: n >= 55)


def test_cadences_are_parameters():
    """Both halves move when their parameter moves, and neither drags the other.

    Fails if either interval is a constant buried in the loop: the counts
    would not change with the arguments.
    """
    print('both cadences are parameters')
    r = run_turn(capture_every=2.0, uploads_per_turn=4, seconds=600.0,
                 edits_at=tuple(range(30, 600, 30)))
    check('a shorter capture interval captures more often',
          len(r.captures), lambda n: 290 <= n <= 302)
    check('and a bigger upload budget uploads that many times',
          len(r.uploads), 4)
    check('the uploads are spread across the turn',
          [round(w[0] / 10) * 10 for w in r.uploads], [150, 300, 450, 600])

    r = run_turn(capture_every=5.0, submit_every=120.0, seconds=600.0,
                 edits_at=tuple(range(30, 600, 30)))
    check('a fixed interval overrides the budget derived from the turn',
          len(r.uploads), 5)


def test_idle_turn_costs_one_upload():
    """A turn nobody plays must not spend the quota once per interval.

    Fails if the identical-capture skip was lost in the split: the counts
    would be one upload per interval instead of one for the turn.
    """
    print('an idle turn')
    r = run_turn(capture_every=5.0, uploads_per_turn=4, seconds=600.0,
                 edits_at=())
    check('an idle turn is still captured throughout',
          len(r.captures), lambda n: n >= 110)
    check('and costs one upload, not one per interval', len(r.uploads), 1)


def test_last_capture_always_sent():
    """Whatever the cadence did, the last capture reaches the store.

    Two shapes: a player who does something after the final interim upload has
    already gone, and a player who does something in the last second of the
    turn. Fails if the final upload carries the state as of the last cadence
    rather than as of the deadline.
    """
    print('the last capture of the turn')
    r = run_turn(capture_every=5.0, uploads_per_turn=2, seconds=600.0,
                 edits_at=(450.0,))
    check('an order played after the last interim upload is still sent',
          r.uploads[-1][1], r.final)
    check('and it took two uploads to do it', len(r.uploads), 2)

    r = run_turn(capture_every=5.0, uploads_per_turn=2, seconds=600.0,
                 edits_at=(598.0,))
    check('an order played in the last seconds is sent',
          r.uploads[-1][1], r.final)
    check('the final upload is not before the deadline',
          round(r.uploads[-1][0]), lambda s: s >= 600)

    r = run_turn(capture_every=5.0, uploads_per_turn=1, seconds=600.0,
                 edits_at=(120.0, 480.0))
    check('a budget of one is the deadline upload alone', len(r.uploads), 1)
    check('and it still carries everything played', r.uploads[0][1], r.final)


def test_save_sends_now():
    """Save during a turn sends at once rather than at the next cadence.

    Fails if the request waits for the upload interval, which is the whole
    complaint: at a 300-second cadence an ignored request is invisible for five
    minutes.
    """
    print('send now')
    played = (60.0, 350.0, 500.0)
    quiet = run_turn(capture_every=5.0, uploads_per_turn=2, seconds=600.0,
                     edits_at=played)
    check('two uploads is what this turn costs when nobody presses Save',
          len(quiet.uploads), 2)
    r = run_turn(capture_every=5.0, uploads_per_turn=2, seconds=600.0,
                 edits_at=played, ask_at=Clock().now() + 100.0)
    check('the request is honoured within a poll of being made',
          round(r.uploads[0][0]), lambda s: 100 <= s <= 103)
    check('and it is an upload on top of the cadence, not instead of one',
          len(r.uploads), 3)
    check('the capture it sends is taken at the same moment',
          any(abs(t - 100.0) <= 3 for t, _b in r.captures), True)


def test_client_closing_does_not_lose_the_turn():
    """The player closes the game window; the last capture still goes.

    Fails if the loop reports the turn lost, or stores nothing, because the
    final capture could not be taken from a client that is gone.
    """
    print('the game window closing')
    r = run_turn(capture_every=5.0, uploads_per_turn=2, seconds=600.0,
                 edits_at=(120.0,), close_at=400.0)
    check('nothing is captured after the client goes',
          max(t for t, _b in r.captures), lambda t: t < 400)
    check('the turn is still in the store', len(r.uploads), lambda n: n >= 1)
    check('and it carries what was played before the window closed',
          r.uploads[-1][1], b'BASE' * 64 + b'ORDER')
    check('the loop does not call the turn lost',
          [k for _t, k, _f in r.states if k == 'lost'], [])


def test_indicator_states():
    """The three things the launcher's indicator has to be able to say.

    Fails if a capture starting, a capture held and a capture stored are not
    distinguishable from the emitted states, which is what the launcher paints
    from.
    """
    print('what the loop tells a UI')
    r = run_turn(capture_every=5.0, uploads_per_turn=2, seconds=600.0,
                 edits_at=(60.0, 400.0))
    kinds = [k for _t, k, _f in r.states]
    check('a capture says it is starting', kinds.count('capturing'),
          lambda n: n >= 100)
    check('and says it finished', kinds.count('captured'),
          lambda n: n >= 100)
    check('a capture not yet in the store is marked pending',
          any(k == 'captured' and f.get('pending')
              for _t, k, f in r.states), True)
    check('a capture the store already holds is not',
          any(k == 'captured' and not f.get('pending')
              for _t, k, f in r.states), True)
    check('and an upload says so', kinds.count('submitted'), 2)


def test_send_now_between_turns_does_not_spin():
    """A Save pressed after the turn closed must not turn the wait into a spin.

    `nap` wakes early when a send is asked for, and between turns nothing
    clears that request, so an ungated version returns from every sleep at
    once and the loop hammers the store while the clock never moves. Fails as
    a store read count in the thousands, or as a clock that never advances
    past the deadline.
    """
    print('a request made between turns')
    clock = Clock()
    store = Store(clock, seconds=60.0)
    start = clock.now()
    reads = {'n': 0}
    real_current = store.current
    store.current = lambda: (reads.__setitem__('n', reads['n'] + 1),
                             real_current())[1]
    calls = {'n': 0}

    def stop():
        calls['n'] += 1
        return calls['n'] > 4000

    saved = (player_turn.serve, player_turn.collect, player_turn.close,
             player_turn.carries_orders, player_turn.describe_orders,
             player_turn.sp)
    player_turn.serve = lambda b, civ, **kw: 'x.dat'
    player_turn.collect = lambda *a, **k: b'BASE' * 64
    player_turn.close = lambda: None
    player_turn.carries_orders = lambda *a: False
    player_turn.describe_orders = lambda *a: 'summary'
    player_turn.sp = types.SimpleNamespace(load_any=lambda b: b)
    try:
        # Pressed as the turn closes, and again well after it, while the loop
        # is waiting for the referee. The second press is the one that has
        # nowhere to go: there is no turn to send, and nothing clears it.
        player_turn.follow(store, 'DemoPlayer', poll=2.0, rounds=0, stop=stop,
                           send_now=Ask(clock, (start + 60.0,
                                                start + 200.0)),
                           log=lambda *a: None, clock=clock.now,
                           sleep=clock.sleep)
    finally:
        (player_turn.serve, player_turn.collect, player_turn.close,
         player_turn.carries_orders, player_turn.describe_orders,
         player_turn.sp) = saved

    elapsed = clock.now() - start
    check('the clock goes on moving through the wait',
          elapsed, lambda s: s > 120)
    check('and the store is read once a poll, not once a loop',
          round(reads['n'] / elapsed, 2), lambda r: r <= 1.0)


# ── the guards that predate the two cadences ─────────────────────────────────
def test_guards_survive():
    """The two turn-12 guards, in the rewritten loop.

    Fails exactly as the 18 September incident did: the pristine turn served
    over a played one, or an orderless capture written over a stranger's
    submission.
    """
    print('the guards from turn 12')
    played = b'BASE' * 64 + b'COLONISE'
    r = run_turn(seconds=120.0, prior=played, orders=True)
    check('a turn already submitted for is resumed, not re-served',
          r.served[0], played)

    r = run_turn(seconds=120.0, prior=played, orders=False, edits_at=())
    check('an orderless capture never replaces a submission this loop did '
          'not write',
          r.store.subs[('DemoPlayer', r.store.turn)], played)
    check('and nothing was written at all', len(r.uploads), 0)

    r = run_turn(seconds=120.0, prior=b'BASE' * 64, orders=False)
    check('an orderless submission is served fresh rather than resumed',
          r.served[0], b'BASE' * 64)


if __name__ == '__main__':
    test_two_cadences()
    test_cadences_are_parameters()
    test_idle_turn_costs_one_upload()
    test_last_capture_always_sent()
    test_save_sends_now()
    test_client_closing_does_not_lose_the_turn()
    test_send_now_between_turns_does_not_spin()
    test_indicator_states()
    test_guards_survive()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for n in FAIL:
        print('  FAILED:', n)
    sys.exit(1 if FAIL else 0)
