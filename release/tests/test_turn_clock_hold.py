"""
test_turn_clock_hold.py: Single Player turns happen only on Next Turn
    server\\.venv\\Scripts\\python.exe release\\tests\\test_turn_clock_hold.py

Left alone, the engine resolves a turn every hour of wall clock, and after a
sleep catches up on every turn it owes, none of them seen by the opponent. The
launcher holds the turn length far out and puts the hold back after each
boundary, where the engine re-applies its own.

Needs nothing running: gamectl.Client against a stand-in for process memory,
and the Launcher built without __init__ against a FakeView.
"""
import os
import pathlib
import queue
import sys

REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import gamectl                                                  # noqa: E402
import launcher as L                                            # noqa: E402
from fake_view import FakeView                                  # noqa: E402

fails = []
GALAXY_LENGTH = 3600     # what the engine re-applies at a boundary


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


def client(length=GALAXY_LENGTH, turn=7, pid=4242):
    """A gamectl.Client whose process memory is a dict.

    A turn length of 1 resolves a turn on the next read of the counter, and
    the boundary puts the galaxy's own length back, as the engine does.
    """
    c = object.__new__(gamectl.Client)
    c.h, c.pid = None, pid
    mem = {gamectl.TURNLENGTH_ADDR: length, gamectl.TURN_COUNTER: turn}
    c.writes = []

    def rd32(addr):
        if addr == gamectl.TURN_COUNTER and mem[gamectl.TURNLENGTH_ADDR] == 1:
            mem[gamectl.TURN_COUNTER] += 1
            mem[gamectl.TURNLENGTH_ADDR] = GALAXY_LENGTH
        return mem.get(addr)

    def wr32(addr, value):
        c.writes.append((addr, value))
        mem[addr] = value

    c.rd32, c.wr32, c.mem = rd32, wr32, mem
    return c


print("gamectl.Client.hold_clock")
c = client()
check("replaces the galaxy's length and says what it was",
      c.hold_clock(), GALAXY_LENGTH)
check("leaving the hold in place",
      c.mem[gamectl.TURNLENGTH_ADDR], gamectl.HOLD_LENGTH)
check("an already held clock is not written again",
      (c.hold_clock(), len(c.writes)), (None, 1))
c = client(length=gamectl.UNSET_LENGTH)
check("a galaxy that is not up yet is left alone",
      (c.hold_clock(), c.writes), (None, []))
check("the hold is past a weekend asleep",
      gamectl.HOLD_LENGTH >= 2 * 86400, True)
check("and survives conversion to milliseconds in a signed 32-bit int",
      gamectl.HOLD_LENGTH * 1000 < 2 ** 31, True)

print("gamectl.Client.advance_turn")
c = client()
c.hold_clock()
check("resolves exactly one turn",
      c.advance_turn(poll=0, restore=gamectl.HOLD_LENGTH), 8)
check("and leaves the clock held, though the boundary re-applied 3600",
      c.mem[gamectl.TURNLENGTH_ADDR], gamectl.HOLD_LENGTH)
# A click straight after a timed boundary, before the tick held it again.
c = client(length=GALAXY_LENGTH)
c.advance_turn(poll=0, restore=gamectl.HOLD_LENGTH)
check("a click that finds the galaxy's length still leaves the hold",
      c.mem[gamectl.TURNLENGTH_ADDR], gamectl.HOLD_LENGTH)
c = client(length=900)
c.advance_turn(poll=0)
check("without `restore`, what was there is put back, as before",
      c.mem[gamectl.TURNLENGTH_ADDR], 900)


def bare(ctl):
    """A Launcher with no window, on a Single Player game with no opponent."""
    app = object.__new__(L.Launcher)
    app.mp_store = None
    app.view = FakeView()
    app._ctl_client = ctl
    app._ctl_busy = False
    app._last_seen_turn = None
    app._waiting_since = None
    app._held_pid = None
    app.running_mode = None
    app.ai_child = None
    app.said = []
    app.say = app.said.append
    app.msgs = queue.Queue()
    return app


print("Launcher._refresh_turn")
c = client()
app = bare(c)
app._refresh_turn()
check("the once-a-second tick holds the clock",
      c.mem[gamectl.TURNLENGTH_ADDR], gamectl.HOLD_LENGTH)
check("and says so once",
      sum("turn clock held" in s for s in app.said), 1)
c.mem[gamectl.TURNLENGTH_ADDR] = GALAXY_LENGTH      # a boundary re-applied it
app._refresh_turn()
check("a boundary's re-applied length is held again on the next tick",
      c.mem[gamectl.TURNLENGTH_ADDR], gamectl.HOLD_LENGTH)
check("without saying it again",
      sum("turn clock held" in s for s in app.said), 1)
app._ctl_busy = True
c.mem[gamectl.TURNLENGTH_ADDR] = 1                  # Next Turn is driving
writes = len(c.writes)
app._refresh_turn()
check("nothing is written while Next Turn is driving the clock",
      len(c.writes), writes)
app._ctl_busy = False
app._ctl_client = c2 = client(pid=5151)             # a Load restarts the game
app._refresh_turn()
check("a restarted game is held, and announced, again",
      (c2.mem[gamectl.TURNLENGTH_ADDR],
       sum("turn clock held" in s for s in app.said)),
      (gamectl.HOLD_LENGTH, 2))

print()
if fails:
    print(f"FAILED: {len(fails)} check(s): {', '.join(fails)}")
    sys.exit(1)
print("all checks passed")
