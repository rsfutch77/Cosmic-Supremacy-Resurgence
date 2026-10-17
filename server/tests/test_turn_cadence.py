"""
test_turn_cadence.py: captures are cheap, uploads are not, so count both
=========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_turn_cadence.py

`follow` captures and uploads on separate cadences. Each case drives a fake
clock across a whole turn and counts captures, uploads and store reads, and
checks that the last capture of the turn reaches the store before it closes.

Store reads follow a schedule taken from the deadline: often near it, rarely
far from it. The cases also check that a turn published late is picked up
promptly and that nothing a player presses waits out a sleep.

The resume and orderless-capture guards (see test_loop_guards.py) are checked
again here, including after Play reopens a turn whose game window has closed.
Reopen checks look at the bytes handed to the client and the submission left
in the store.

The clock, the client and the store are all fakes.
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


class Clock:
    """Time the loop is given, which only moves when the loop sleeps."""

    def __init__(self, start=1000.0):
        self.t = float(start)

    def now(self):
        return self.t

    def sleep(self, seconds):
        # A zero-length sleep still advances, so a nap cannot spin forever.
        self.t += max(float(seconds), 0.01)


class Store:
    """Just enough of a TurnStore for one turn, recording every write."""

    def __init__(self, clock, turn=7, seconds=600.0, blob=b'BASE' * 64):
        self.clock = clock
        self.turn = turn
        self.opened = turn              # the turn this store started on
        self.seconds = seconds
        self.deadline = clock.now() + seconds
        self.blob = blob
        self.subs = {}
        self.writes = []                # (when, blob) per submit that landed
        # Every look at the clock or the turn number; each is a Firestore
        # document read.
        self.reads = 0
        # When the referee closes this turn and publishes the next.
        self.next_at = None

    def _tick(self):
        if self.next_at is not None and self.clock.now() >= self.next_at:
            self.turn += 1
            self.deadline = self.clock.now() + self.seconds
            self.next_at = None

    def current(self):
        self._tick()
        self.reads += 1
        return self.turn, self.deadline

    def seconds_left(self):
        self._tick()
        self.reads += 1
        return self.deadline - self.clock.now()

    def turn_blob(self, turn):
        """Each turn's own pristine blob, so a serve names the turn it served."""
        return self.blob if turn == self.opened else self.blob + b'NEXT'

    def submission(self, civ, turn):
        return self.subs.get((civ, turn))

    def submit(self, civ, turn, blob):
        self.subs[(civ, turn)] = blob
        self.writes.append((self.clock.now() - (self.deadline - self.seconds),
                            blob))

    def note(self, civ, turn):
        return []


class Ask:
    """A player pressing a button the loop watches for, Save or Play.

    Stands in for the launcher's threading.Event. `at` is when they press it,
    or a list of times. A press that has not happened yet is not cleared.
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
             prior=None, orders=True, poll=2.0, poll_max=None, rounds=1,
             reopen_at=None, next_turn_at=None, loses_orders=False,
             stop_after=None):
    """One whole turn against a fake clock. Returns what it did.

    `edits_at` is when the player changes something, in seconds from the start
    of the turn, so that captures taken after one differ from captures before
    it. `close_at` is the player closing the game window, after which no
    capture can be taken until the turn is opened again.

    `reopen_at` is Play pressed with the window closed, and `next_turn_at` is
    the referee publishing the next turn. A reopen needs `rounds=2`: the first
    pass ends when the window closes.

    `loses_orders` is a client that comes up without what was loaded into it.

    `stop_after` is the player pressing stop that many seconds in. With
    `rounds` of 0 it is the only thing that ends the loop.
    """
    clock = Clock()
    store = Store(clock, seconds=seconds)
    start = clock.now()
    base = store.blob
    if next_turn_at is not None:
        store.next_at = start + next_turn_at
    if prior is not None:
        store.subs[('DemoPlayer', store.turn)] = prior
        store.writes.clear()            # a submission that was already there

    captures, served, serve_times, states = [], [], [], []
    # What the client holds: the blob last loaded into it, and when.
    loaded = {'blob': base, 'at': 0.0}

    def player_state(now):
        done = sum(1 for e in edits_at if loaded['at'] <= e <= now - start)
        return loaded['blob'] + b'ORDER' * done

    attempts = []
    # The game window closes at `close_at` and the next serve opens it again.
    shut = {'after': None}

    def window_gone():
        if close_at is None or clock.now() - start < close_at:
            return False
        if shut['after'] is None:
            shut['after'] = len(served)
        return len(served) == shut['after']

    def fake_serve(blob, civ, **kw):
        served.append(blob)
        serve_times.append(clock.now() - start)
        loaded['blob'] = base if loses_orders else blob
        loaded['at'] = clock.now() - start
        return 'x.dat'

    def fake_collect(name, save_dir=None, log=print, announce=True):
        now = clock.now()
        attempts.append(now - start)
        if window_gone():
            raise SystemExit('SaveGame did not report success')
        blob = player_state(now)
        captures.append((now - start, blob))
        return blob

    saved = (player_turn.serve, player_turn.collect, player_turn.close,
             player_turn.carries_orders, player_turn.describe_orders,
             player_turn.sp)
    player_turn.serve = fake_serve
    player_turn.collect = fake_collect
    player_turn.close = lambda: None
    # A predicate when a test needs the guards to tell a played turn from an
    # empty one.
    player_turn.carries_orders = (
        orders if callable(orders) else
        lambda served_b, sub, civ: orders)
    player_turn.describe_orders = lambda *a: 'summary'
    # The fake `collect` hands back bytes, so loading one is the identity.
    player_turn.sp = types.SimpleNamespace(load_any=lambda b: b)
    # Answers for the fake client when `follow` asks whether it is running.
    import game_cycle
    saved_pids = game_cycle.client_pids
    game_cycle.client_pids = lambda: [] if window_gone() else [4242]
    try:
        player_turn.follow(
            store, 'DemoPlayer', poll=poll,
            poll_max=player_turn.POLL_CEILING if poll_max is None else poll_max,
            rounds=rounds,
            on_state=lambda kind, **f: states.append((clock.now() - start, kind, f)),
            capture_every=capture_every, uploads_per_turn=uploads_per_turn,
            submit_every=submit_every, send_now=Ask(clock, ask_at),
            reopen=Ask(clock, reopen_at),
            stop=(None if stop_after is None
                  else lambda: clock.now() - start >= stop_after),
            log=lambda *a: None, clock=clock.now, sleep=clock.sleep)
    finally:
        (player_turn.serve, player_turn.collect, player_turn.close,
         player_turn.carries_orders, player_turn.describe_orders,
         player_turn.sp) = saved
        game_cycle.client_pids = saved_pids
    return types.SimpleNamespace(store=store, captures=captures,
                                 attempts=attempts, reads=store.reads,
                                 uploads=store.writes, served=served,
                                 serve_times=serve_times,
                                 states=states, clock=clock, start=start,
                                 elapsed=clock.now() - start,
                                 final=player_state(clock.now()))


def test_two_cadences():
    """A capture every 5s and two uploads across a ten-minute turn."""
    print('two cadences across one turn')
    # An edit every minute, so no capture is skipped as identical.
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
    """Each cadence follows its own parameter."""
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
    """A turn nobody plays costs one upload: identical captures are skipped."""
    print('an idle turn')
    r = run_turn(capture_every=5.0, uploads_per_turn=4, seconds=600.0,
                 edits_at=())
    check('an idle turn is still captured throughout',
          len(r.captures), lambda n: n >= 110)
    check('and costs one upload, not one per interval', len(r.uploads), 1)


def test_last_capture_always_sent():
    """Whatever the cadence did, the last capture reaches the store.

    Covers an edit after the last interim upload and one in the last seconds
    of the turn: the final upload carries the state as of the deadline.
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
    """Save during a turn sends at once, on top of the upload cadence."""
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
    """The player closes the game window; the last capture still goes, and the
    loop stops asking the gone client for captures."""
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
    # Counts attempts, since failed captures never appear in `captures`.
    after = [t for t in r.attempts if t >= 400.0]
    check('it stops asking a client that has gone', len(after), lambda n: n <= 1)
    check('and the turn ends there rather than running to the deadline',
          r.clock.now() - r.start, lambda t: t < 600.0)


def test_indicator_states():
    """The emitted states tell a capture starting, held and stored apart."""
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
    """A Save pressed after the turn closed does not turn the wait into a spin.

    `nap` wakes early for a send request, and between turns nothing clears it.
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
        # Pressed as the turn closes, and again while the loop waits for the
        # referee, when there is no turn to send.
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


BASE = b'BASE' * 64


def carries(served_b, sub, civ):
    """A capture carries orders when it differs from what was served."""
    return sub != served_b


def tail(blob):
    """What a blob carries beyond the pristine turn, for readable checks."""
    return blob[len(BASE):] if blob.startswith(BASE) else blob


def test_play_again_opens_the_turn_on_the_submission():
    """Play with the window closed reopens the turn on the player's submission.

    Checks the bytes that went into the client and what the store holds
    afterwards, so the pristine turn is never served over a played one.
    """
    print('opening the turn again')
    r = run_turn(seconds=600.0, edits_at=(30.0, 400.0), close_at=100.0,
                 reopen_at=Clock().now() + 200.0, rounds=2, orders=carries)
    check('the turn is opened a second time', len(r.served), 2)
    check('when the player asked and not before',
          round(r.serve_times[-1]), lambda t: 200 <= t <= 203)
    check('on the submission carrying their order, not the pristine turn',
          tail(r.served[1]), b'ORDER')
    check('which is not the blob the store would have served',
          r.served[1] == r.store.turn_blob(r.store.turn), False)
    check('and it is the same turn, not the next one',
          [f.get('turn') for _t, k, f in r.states if k == 'serving'], [7, 7])
    check('the loop says what it is doing',
          [k for _t, k, _f in r.states if k == 'reopening'], ['reopening'])
    # What is done in the reopened window is captured and stored.
    check('captures resume after it opens',
          len([t for t, _b in r.captures if t > 205.0]), lambda n: n >= 70)
    check('an edit made after it opened reaches the store',
          tail(r.store.subs[('DemoPlayer', 7)]), b'ORDERORDER')
    check('and what was played before it closed is still under that',
          r.store.subs[('DemoPlayer', 7)].startswith(BASE + b'ORDER'), True)
    check('nothing orderless was ever written over it',
          [t for t, b in r.uploads if b == BASE], [])


def test_reopen_after_the_turn_closed_serves_the_new_turn():
    """The referee publishes while the window is shut, then Play is pressed.

    The loop opens the turn the store is on, and the closed turn keeps its
    submission.
    """
    print('opening again after the turn closed')
    r = run_turn(seconds=600.0, edits_at=(30.0,), close_at=100.0,
                 next_turn_at=150.0, reopen_at=Clock().now() + 150.0,
                 rounds=2, orders=carries)
    check('the second serve is the turn the store is on',
          [f.get('turn') for _t, k, f in r.states if k == 'serving'], [7, 8])
    check('served on that turn rather than on the old submission',
          tail(r.served[1]), b'NEXT')
    check('the closed turn keeps the submission it had',
          tail(r.store.subs[('DemoPlayer', 7)]), b'ORDER')
    check('and the new turn is the one being played into',
          ('DemoPlayer', 8) in r.store.subs, True)
    check('the request was spent on the new turn, not carried past it',
          [k for _t, k, _f in r.states if k == 'reopening'], [])
    check('so the game was opened twice in all, not three times',
          len(r.served), 2)


def test_the_read_schedule():
    """The poll gap follows the distance to the deadline on both sides of it."""
    print('the read schedule')
    g = player_turn.poll_gap
    check('four hours out, the ceiling', g(14400.0), 300.0)
    check('an hour out, three minutes', g(3600.0), 180.0)
    check('a minute out, three seconds', g(60.0), 3.0)
    check('the last stretch before a deadline is the floor', g(20.0), 2.0)
    check('and so is the deadline itself', g(0.0), 2.0)
    check('a turn ten seconds overdue is still watched at the floor',
          g(-10.0), 2.0)
    check('while a referee down for an hour is asked every three minutes',
          g(-3600.0), 180.0)
    check('the floor it is given is honoured', g(600.0, floor=45.0), 45.0)
    check('and so is the ceiling', g(600.0, ceiling=10.0), 10.0)


def test_reads_across_a_whole_turn():
    """How many times the store is read across a whole turn.

    The lower bounds fail a schedule that has stopped reading the store.
    """
    print('what a turn costs at the store')
    # A fifteen-minute turn played to its deadline with the game window open.
    r = run_turn(seconds=900.0, edits_at=tuple(range(60, 900, 60)))
    check('a fifteen-minute turn played out is under a hundred reads',
          r.reads, lambda n: 50 <= n <= 110)
    check('and the capture cadence is untouched by that',
          len(r.captures), lambda n: 176 <= n <= 182)
    check('as is the upload budget', len(r.uploads), 2)

    # A four-hour turn whose player closes the game window five minutes in.
    r = run_turn(seconds=14400.0, close_at=300.0, stop_after=14400.0,
                 rounds=0, edits_at=(120.0,))
    check('a four-hour turn spent waiting is under two hundred',
          r.reads, lambda n: 60 <= n <= 200)
    check('having waited the whole turn out rather than ending early',
          round(r.elapsed), lambda t: t >= 14400)
    check('with the five minutes of play still captured every five seconds',
          len(r.captures), lambda n: 56 <= n <= 62)


def test_a_turn_published_late_is_not_waited_out():
    """A turn published after its deadline is served promptly.

    Within a floor gap when it is just late, and within a twentieth of the
    lateness when it is long overdue.
    """
    print('a turn published late')
    r = run_turn(seconds=600.0, close_at=100.0, next_turn_at=630.0,
                 rounds=2, orders=carries, edits_at=(30.0,))
    check('a turn 30 seconds late is served within a floor of appearing',
          r.serve_times[1], lambda t: 630.0 <= t <= 632.5)

    r = run_turn(seconds=600.0, close_at=100.0, next_turn_at=1800.0,
                 rounds=2, orders=carries, edits_at=(30.0,))
    check('a turn twenty minutes late is served within a minute of appearing',
          r.serve_times[1], lambda t: 1800.0 <= t <= 1860.0)
    check('and those twenty minutes were not spent reading the store',
          r.reads, lambda n: n <= 300)


def test_the_wide_gap_does_not_stretch_the_capture_cadence():
    """A wide store-read gap leaves the capture cadence at five seconds."""
    print('a wide gap and a five-second capture')
    r = run_turn(seconds=3600.0, edits_at=tuple(range(60, 3600, 60)))
    check('an hour-long turn is still captured every five seconds',
          len(r.captures), lambda n: 715 <= n <= 725)
    check('while the store was read about a hundred times, not four thousand',
          r.reads, lambda n: 60 <= n <= 140)
    check('and the turn still cost two uploads', len(r.uploads), 2)


def test_stop_and_send_survive_the_long_sleep():
    """Stop, Send Turn and Play are answered within a `nap`, not a poll gap.

    All three land where the schedule is at its widest.
    """
    print('what a player presses')
    r = run_turn(seconds=14400.0, close_at=100.0, stop_after=5000.0, rounds=0)
    check('stop, pressed inside a five-minute sleep, ends the loop at once',
          r.elapsed, lambda t: 5000.0 <= t <= 5001.0)

    # Asked between two captures, so only `nap` waking can answer it in time.
    r = run_turn(seconds=14400.0, edits_at=(60.0,), close_at=200.0, rounds=1,
                 ask_at=Clock().now() + 102.0)
    check('Send Turn is answered when asked, not when the store is next read',
          r.uploads[0][0], lambda t: 102.0 <= t <= 103.0)

    r = run_turn(seconds=14400.0, close_at=100.0, rounds=2, orders=carries,
                 edits_at=(30.0,), reopen_at=Clock().now() + 5000.0)
    check('Play between turns opens the game when asked',
          r.serve_times[1], lambda t: 5000.0 <= t <= 5001.0)


def test_guards_survive():
    """A played submission is resumed, and an orderless capture never replaces
    a submission this loop did not write."""
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

    # The same guard after a reopen. A reopen starts a fresh pass, so the
    # standing submission is one this pass did not write, and an empty
    # client's capture must not land.
    r = run_turn(seconds=600.0, edits_at=(30.0,), close_at=100.0,
                 reopen_at=Clock().now() + 200.0, rounds=2, orders=carries,
                 loses_orders=True)
    check('a reopened turn whose client comes up empty keeps the submission',
          tail(r.store.subs[('DemoPlayer', 7)]), b'ORDER')
    check('and the loop says it refused rather than doing it quietly',
          [k for _t, k, _f in r.states if k == 'refused'],
          lambda ks: len(ks) >= 1)


if __name__ == '__main__':
    test_two_cadences()
    test_cadences_are_parameters()
    test_idle_turn_costs_one_upload()
    test_last_capture_always_sent()
    test_save_sends_now()
    test_client_closing_does_not_lose_the_turn()
    test_send_now_between_turns_does_not_spin()
    test_indicator_states()
    test_play_again_opens_the_turn_on_the_submission()
    test_reopen_after_the_turn_closed_serves_the_new_turn()
    test_the_read_schedule()
    test_reads_across_a_whole_turn()
    test_a_turn_published_late_is_not_waited_out()
    test_the_wide_gap_does_not_stretch_the_capture_cadence()
    test_stop_and_send_survive_the_long_sleep()
    test_guards_survive()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for n in FAIL:
        print('  FAILED:', n)
    sys.exit(1 if FAIL else 0)
