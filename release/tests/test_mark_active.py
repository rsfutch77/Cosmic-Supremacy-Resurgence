"""
test_mark_active.py: a local game marks every civ active before Next Turn
    server\\.venv\\Scripts\\python.exe release\\tests\\test_mark_active.py

Nothing in the client sets a civ's activity flags, so a local game's
Inactivity penalty grows from turn 73. Next Turn sets them for every civ, or
for none. Headless: the snapshot is a dict of bytes and the client is recorded,
not run. That the engine then resets the penalty is checked in the client, not
here.
"""
import os
import sys

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import launcher as L                                            # noqa: E402
from fake_view import FakeView                                  # noqa: E402

L.multiplayer_modules()
import gamestate as gs                                          # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


class Civ:
    def __init__(self, addr, name):
        self.addr, self.civ_name = addr, name


class Snap:
    def __init__(self, *civs):
        self.civs = list(civs)
        self.h = None
        self.mem = {c.addr + off: 0 for c in civs for off in L.ACTIVE_FLAGS}

    def read(self, addr, n):
        return bytes([self.mem[addr]])

    def flags(self):
        return sorted(set(self.mem.values()))


writes, refuse = [], set()


def write_bytes(_h, addr, data):
    writes.append(addr)
    if addr in refuse:
        return False
    snap.mem[addr] = data[0]
    return True


gs.ev.write_bytes = write_bytes

print("mark_civs_active")
snap = Snap(Civ(0x1000, "DemoPlayer"), Civ(0x3000, "BadGuy"))
check("marks both civs", L.mark_civs_active(snap), ["BadGuy", "DemoPlayer"])
check("sets both flags on each", snap.flags(), [1])
writes.clear()
check("a second pass changes nothing", L.mark_civs_active(snap), [])
check("and writes nothing", writes, [])

snap = Snap(Civ(0x1000, "DemoPlayer"), Civ(0x3000, "BadGuy"))
refuse.add(0x3000 + L.ACTIVE_FLAGS[1])
try:
    L.mark_civs_active(snap)
    raised = False
except RuntimeError:
    raised = True
check("a refused write raises", raised, True)
check("and leaves no civ marked", snap.flags(), [0])
refuse.clear()


print("\non_next_turn")
order = []


class Client:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def advance_turn(self, restore=None):
        order.append("advance")
        return 1


def bare(playing):
    app = object.__new__(L.Launcher)
    app.view = FakeView()
    app.data_dir = "."
    app.said = []
    app.say_threadsafe = app.said.append
    app._autosaves = lambda: False
    app.playing_now = lambda: playing
    app._client = Client
    app._mark_active = lambda: order.append("mark")
    app._in_background = lambda label, work, done=None: work()
    return app


bare(None).on_next_turn()
check("a local game marks the civs, then ends the turn", order,
      ["mark", "advance"])
order.clear()
bare({"galaxy": "sandbox"}).on_next_turn()
check("multiplayer leaves it to the referee", order, ["advance"])

print("\n_mark_active")
app = bare(None)
del app._mark_active
snap = Snap(Civ(0x1000, "DemoPlayer"), Civ(0x3000, "BadGuy"))
gs.Snapshot = lambda: snap
app._mark_active()
check("names the civs it marked", app.said, ["marked active: BadGuy, DemoPlayer"])


def broken():
    raise RuntimeError("CosmicSupremacy is not running")


gs.Snapshot = broken
app.said.clear()
app._mark_active()
check("a failure is said, not raised", app.said,
      lambda s: len(s) == 1 and s[0].startswith("could not mark the civs active"))

print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'all passed'}")
sys.exit(1 if fails else 0)
