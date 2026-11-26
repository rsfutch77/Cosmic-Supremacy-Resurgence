"""
test_tick_turn_count.py: a referee tick ends at exactly the turn it asks for
=============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_tick_turn_count.py

`advance_turns.py` runs against a stand-in engine: a clock that moves only
when the tool sleeps, a turn that ends once its length has passed, and the
galaxy's own length re-applied at each boundary. `referee.tick` runs with the
client, the capture and `advance_turns` replaced, and the captured turn chosen
per attempt.

Nothing here launches a client. Whether the real engine ends a second turn is
a client question, answered by ticking a galaxy on it.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'client', 'dev_tools'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'server')):
    if d not in sys.path:
        sys.path.insert(0, d)

import advance_turns as at
import referee

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(*_args):
    pass


class Engine:
    """The client's turn clock, as far as `advance_turns` can see it.

    A turn ends when `length` seconds have passed since the last boundary;
    the boundary re-applies `galaxy` as the length. After a boundary, a write
    that shortens the length ends a turn at once, as the client did on 21
    November 2026 with d1 (30 days shortened to a day). `double` ends two
    turns at one boundary.
    """

    def __init__(self, turn=180, length=3600, galaxy=1, double=False):
        self.mem = {at.TURNLENGTH_ADDR: length, at.TURN_COUNTER: turn}
        self.galaxy, self.double = galaxy, double
        self.now = self.boundary = 0.0
        self.crossed = False

    def end_turn(self):
        self.mem[at.TURN_COUNTER] += 2 if self.double else 1
        self.mem[at.TURNLENGTH_ADDR] = self.galaxy
        self.crossed = True

    def sleep(self, secs):
        self.now += secs
        while self.now - self.boundary >= self.mem[at.TURNLENGTH_ADDR]:
            self.boundary += self.mem[at.TURNLENGTH_ADDR]
            self.end_turn()

    def time(self):
        return self.now

    def rd32(self, _h, addr):
        return self.mem[addr]

    def wr32(self, _h, addr, value):
        shorter = addr == at.TURNLENGTH_ADDR and value < self.mem[addr]
        self.mem[addr] = value
        if shorter and self.crossed:
            self.boundary = self.now
            self.end_turn()
        return True


class Kernel:
    @staticmethod
    def OpenProcess(*_args):
        return 1

    @staticmethod
    def CloseHandle(_h):
        return True


def advance(engine, argv):
    """Run `advance_turns.main` on `engine`. Returns its exit code."""
    kept = (at.find_pid, at.kernel32, at.rd32, at.wr32, at.time, sys.argv,
            sys.stdout)
    at.find_pid = lambda: (4242, 'CosmicSupremacy.exe')
    at.kernel32 = Kernel
    at.rd32, at.wr32, at.time = engine.rd32, engine.wr32, engine
    sys.argv = ['advance_turns.py'] + argv
    sys.stdout = open(os.devnull, 'w')
    try:
        at.main()
        return 0
    except SystemExit as exc:
        return exc.code
    finally:
        sys.stdout.close()
        (at.find_pid, at.kernel32, at.rd32, at.wr32, at.time, sys.argv,
         sys.stdout) = kept


def test_advance_turns():
    print('advance_turns stops the clock at the target')
    e = Engine(galaxy=1)
    code = advance(e, ['1', '--secs', '10', '--hold', '86400'])
    check('a one-second galaxy length still ends one turn',
          e.mem[at.TURN_COUNTER], 181)
    check('and exits 0', code, 0)
    check('and leaves the hold behind', e.mem[at.TURNLENGTH_ADDR], 86400)

    e = Engine(galaxy=1)
    code = advance(e, ['3', '--secs', '2', '--hold', '86400'])
    check('three turns end at start + 3', (e.mem[at.TURN_COUNTER], code),
          (183, 0))

    e = Engine(galaxy=2592000)
    code = advance(e, ['1', '--secs', '10', '--hold', '86400'])
    check('a 30-day galaxy ends one turn under a one-day hold',
          (e.mem[at.TURN_COUNTER], code), (181, 0))
    check('and keeps its own longer length',
          e.mem[at.TURNLENGTH_ADDR], 2592000)

    e = Engine(galaxy=60)
    code = advance(e, ['1', '--secs', '10'])
    check('with no hold given, the length found on attach is left at least',
          (e.mem[at.TURN_COUNTER], code, e.mem[at.TURNLENGTH_ADDR]),
          (181, 0, 3600))

    e = Engine(galaxy=2592000)
    e.mem[at.TURN_COUNTER] = 180
    e.wr32(None, at.TURNLENGTH_ADDR, 10)
    e.sleep(10)
    e.wr32(None, at.TURNLENGTH_ADDR, 86400)
    check('the stand-in ends a turn when the length is shortened after one, '
          'as the client does', e.mem[at.TURN_COUNTER], 182)

    e = Engine(double=True)
    code = advance(e, ['1', '--secs', '10', '--hold', '86400'])
    check('a boundary that ends two turns exits PAST', code, at.PAST)

    e = Engine(galaxy=3600)
    # An engine that ignores the drive never reaches a boundary.
    e.wr32 = lambda h, addr, value: True
    code = advance(e, ['1', '--secs', '10'])
    check('a stalled pipeline exits SHORT', code, at.SHORT)


class Snap:
    turn = 180


class FakeClient:
    restarts, closed = 0, 0

    @classmethod
    def restart(cls, dat, purpose=None, wait_for_lock=None):
        cls.restarts += 1
        return Snap()

    @classmethod
    def close_client(cls):
        cls.closed += 1


def tick(results, turns):
    """Run `referee.tick` with each attempt's (advance exit code, captured
    turn) from `results`. Returns (blob or the SystemExit, restarts, the
    advance_turns command lines)."""
    tmp = tempfile.mkdtemp(prefix='tick_turns_')
    capture = os.path.join(tmp, 'capture.raw')
    blob = b'SAVE' + bytes(8)
    with open(capture, 'wb') as f:
        f.write(blob)
    codes = [code for code, _turn in results]
    captured = [turn for code, turn in results if code == 0]
    calls = []

    class FakePlayerTurn:
        HOLD_SECONDS = 86400

        @staticmethod
        def collect(who, save_dir=None, log=None):
            return capture

    def run(args, **_kw):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, codes.pop(0), stdout='',
                                           stderr='')

    FakeClient.restarts = FakeClient.closed = 0
    kept_mods = {n: sys.modules.get(n) for n in ('game_cycle', 'player_turn')}
    kept = (referee.stamp_for_a_holder, referee.turn_of, referee.subprocess.run)
    sys.modules['game_cycle'] = FakeClient
    sys.modules['player_turn'] = FakePlayerTurn
    referee.stamp_for_a_holder = lambda b, log=None: b
    referee.turn_of = lambda b: captured.pop(0)
    referee.subprocess.run = run
    try:
        got = referee.tick(blob, turns=turns, secs=10, work_dir=tmp,
                           log=quiet, check_save_path=False)
    except SystemExit as exc:
        got = exc
    finally:
        (referee.stamp_for_a_holder, referee.turn_of,
         referee.subprocess.run) = kept
        for n, m in kept_mods.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m
    return got, FakeClient.restarts, calls


def test_referee_tick():
    print('referee.tick publishes only the turn it asked for')
    got, restarts, calls = tick([(0, 181)], 1)
    check('a capture at start + 1 is returned', isinstance(got, bytes))
    check('after one load', restarts, 1)
    check('and advance_turns is asked to hold the clock after',
          calls[0][-2:], ['--hold', '86400'])

    got, restarts, _calls = tick([(0, 182), (0, 181)], 1)
    check('a capture at start + 2 is ticked again from a fresh load',
          (isinstance(got, bytes), restarts), (True, 2))

    got, restarts, _calls = tick([(at.PAST, None), (0, 181)], 1)
    check('an advance that went past is ticked again before any capture',
          (isinstance(got, bytes), restarts), (True, 2))

    got, restarts, _calls = tick([(0, 182)] * referee.TICK_ATTEMPTS, 1)
    check('every attempt past the target is refused',
          isinstance(got, SystemExit))
    check('after TICK_ATTEMPTS loads', restarts, referee.TICK_ATTEMPTS)
    check('and the refusal names both turns',
          'turn 181' in str(got) and 'captured 182' in str(got))

    got, restarts, _calls = tick([(0, 183)], 3)
    check('three turns are judged against start + 3', isinstance(got, bytes))

    got, restarts, _calls = tick([(at.SHORT, None)], 1)
    check('an advance that fell short still fails the tick at once',
          (str(got), restarts), ('advance_turns failed', 1))

    check('the referee reads PAST the way advance_turns writes it',
          referee.ADVANCE_PAST, at.PAST)


if __name__ == '__main__':
    test_advance_turns()
    test_referee_tick()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for name in FAIL:
        print(f'  failed: {name}')
    sys.exit(1 if FAIL else 0)
