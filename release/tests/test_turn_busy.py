"""
test_turn_busy.py: Play greys out while a turn is sent and processed

    python release/tests/test_turn_busy.py

From the followed galaxy's orders going until the next turn opens, every row's
Play is drawn disabled with a line saying why. Before the deadline a player
who closed the game early can still press Play to change their orders, and a
stopped or unreachable galaxy keeps Play, since the relay takes orders until
the turn changes. Headless, against a Launcher built without `__init__`.
"""
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import galaxy_directory                                         # noqa: E402
import launcher as L                                            # noqa: E402
from fake_view import FakeView                                  # noqa: E402

fails = []
dirs = []
FOUR_HOURS = 4 * 3600


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


def fresh_dir():
    d = tempfile.mkdtemp(prefix="busy_")
    dirs.append(d)
    return d


class Thread:
    def is_alive(self):
        return True


def row(gid, joined=True, status="open"):
    return galaxy_directory.Galaxy(gid, gid.title(), status, 4,
                                   time.time() + 3600, 3, joined,
                                   f"C:\\g\\{gid}")


def app(**over):
    """Following "sandbox" with the game closed and the turn not yet due."""
    a = object.__new__(L.Launcher)
    a.mp_thread = Thread()
    a.mp_playing = {"name": "Sandbox", "store": "C:\\g\\sandbox"}
    a.mp_client_gone = True
    a.mp_sending = False
    a.mp_waiting = True
    a.mp_reopening = False
    a.mp_deadline = time.time() + 600
    a.mp_turn_seconds = FOUR_HOURS
    a.mp_unreachable = None
    a.data_dir = fresh_dir()
    a.view = FakeView()
    a.games_rows = [row("sandbox"), row("frontier"), row("draco", joined=False)]
    a.games_extra, a.games_err, a.games_dir = {}, None, "relay"
    a.games_recs = {}
    a.own_store = None
    a.sort_key, a.sort_desc = "name", False
    a.said = []
    a.say = a.said.append
    for k, v in over.items():
        setattr(a, k, v)
    return a


print("1. when a turn counts as busy")
check("following nothing is never busy",
      app(mp_thread=None).turn_busy(), None)
check("with the game open it is not",
      app(mp_client_gone=False, mp_sending=True).turn_busy(), None)
check("closed early, before the deadline, Play still changes the orders",
      app().turn_busy(), None)
check("while the orders are going it is sending",
      app(mp_sending=True, mp_waiting=False).turn_busy(), L.TURN_SENDING)
due = time.time() - 30
check("past the deadline, waiting for the referee, it is processing",
      app(mp_deadline=due).turn_busy(), L.TURN_PROCESSING)
check("not while the game is reopening",
      app(mp_deadline=due, mp_reopening=True).turn_busy(), None)
check("not once the galaxy has stopped, which the relay still takes orders for",
      app(mp_deadline=time.time() - 2 * 3600).turn_busy(), None)
check("nor while the galaxy cannot be reached",
      app(mp_deadline=due, mp_unreachable=time.time() - 5).turn_busy(), None)
check("nor once it has closed",
      app(mp_deadline=due, mp_closed=True).turn_busy(), None)

print("\n2. every row's Play is drawn disabled, with a line saying why")
a = app(mp_deadline=due)
L.Launcher._draw_galaxies(a)
by_name = {r.cells[0][0]: r for r in a.view.rows}
check("the followed galaxy's Play is greyed out",
      (by_name["Sandbox"].action, by_name["Sandbox"].enabled,
       by_name["Sandbox"].on_action), ("Play", False, None))
check("and another galaxy's Play too",
      (by_name["Frontier"].enabled, by_name["Frontier"].on_action),
      (False, None))
check("a View is left live", (by_name["Draco"].action,
                              by_name["Draco"].enabled,
                              by_name["Draco"].on_action is not None),
      ("View", True, True))
check("the line under the table says the turn is being processed",
      a.view.hint, L.TURN_BUSY_HINT[L.TURN_PROCESSING])
a = app(mp_sending=True, mp_waiting=False)
L.Launcher._draw_galaxies(a)
check("while sending it says the turn is being sent",
      a.view.hint, L.TURN_BUSY_HINT[L.TURN_SENDING])
a = app()
L.Launcher._draw_galaxies(a)
check("before the deadline Play is live",
      [r.enabled for r in a.view.rows], [True, True, True])
check("and the line says nothing about processing",
      a.view.hint in L.TURN_BUSY_HINT.values(), False)

print("\n3. Play comes back without a new listing")
a = app()
L.Launcher._draw_galaxies(a)
a.mp_deadline = due
L.Launcher._tick_clocks(a)
check("the deadline passing greys it out on the next tick",
      {r.cells[0][0]: r.enabled for r in a.view.rows},
      {"Draco": True, "Frontier": False, "Sandbox": False})
a.mp_waiting = False
a.mp_client_gone = False
L.Launcher._tick_clocks(a)
check("and the next turn opening brings it back",
      [r.enabled for r in a.view.rows], [True, True, True])

print("\n4. a press that raced the greying out does nothing")
a = app(mp_deadline=due)
played = []
a.play_galaxy = played.append
L.Launcher.on_row(a, a.games_rows[0], L.PLAY)
check("Play is not acted on", played, [])
check("and the log says why", a.said, lambda s: any("processing" in x
                                                    for x in s))
a = app()
a.play_galaxy = played.append
L.Launcher.on_row(a, a.games_rows[0], L.PLAY)
check("before the deadline it plays", len(played), 1)

for d in dirs:
    shutil.rmtree(d, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
