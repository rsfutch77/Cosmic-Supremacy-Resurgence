"""
test_turn_played.py: a sent turn shows as played on the Galaxies page at once
    server\\.venv\\Scripts\\python.exe release\\tests\\test_turn_played.py

The turn loop's `submitted` event marks the followed galaxy's row "turn played"
without waiting for the next listing.

Headless: the Launcher is built without __init__, against a FakeView.
"""
import os
import queue
import sys
import types

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import launcher as L                                            # noqa: E402
from fake_view import FakeView                                  # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


def row(gid="sandbox", turn=14):
    return types.SimpleNamespace(id=gid, turn=turn)


def bare(page="galaxies", rows=(), submitted=None):
    """A Launcher with no window, recording draws and listings."""
    app = object.__new__(L.Launcher)
    app.view = FakeView()
    app.msgs = queue.Queue()
    app.page = page
    app.games_rows = list(rows)
    app.games_extra = {} if submitted is None else {"submitted": submitted}
    app.mp_civ = "DemoPlayer"
    app.mp_playing = {"id": "sandbox", "name": "Sandbox", "store": "x"}
    app.mp_store = "x"
    app.mp_capture = None
    app.mp_sending = False
    app.mp_turn_open = True
    app.mp_reopening = False
    app.mp_closed = False
    app.mp_log_offered = True
    app.said = []
    app.say_threadsafe = app.said.append
    app.drawn = 0
    app.listed = 0
    app._draw_galaxies = lambda: setattr(app, "drawn", app.drawn + 1)
    app.refresh_games = lambda: setattr(app, "listed", app.listed + 1)
    return app


def queued(app):
    out = []
    while not app.msgs.empty():
        out.append(app.msgs.get_nowait())
    return out


print("\n1. the turn loop's submission reaches the UI thread")
app = bare()
app._mp_state("submitted", turn=14, civ="DemoPlayer", final=False)
check("a submission posts the followed galaxy and its turn",
      [m for m in queued(app) if m[0] == "__mp_played__"],
      [("__mp_played__", "sandbox", 14)])
app = bare()
app.mp_playing = None
app._mp_state("submitted", turn=14, civ="DemoPlayer", final=True)
check("with no galaxy followed nothing is posted",
      [m for m in queued(app) if m[0] == "__mp_played__"], [])

print("\n2. the row reads turn played without a listing")
app = bare(rows=[row(), row("crowded")],
           submitted={"sandbox": False, "crowded": False})
app._on_mp_played("sandbox", 14)
check("the followed galaxy is marked played",
      app.games_extra["submitted"], {"sandbox": True, "crowded": False})
check("and the page is repainted", app.drawn, 1)
check("and nothing is listed again", app.listed, 0)
check("so the cell reads turn played",
      L._you_text(types.SimpleNamespace(id="sandbox", joined=True,
                                        status="open"),
                  types.SimpleNamespace(submitted=app.games_extra["submitted"],
                                        recs={})),
      "turn played")
app._on_mp_played("sandbox", 14)
check("a second upload of the same turn repaints nothing", app.drawn, 1)

app = bare(rows=[row()])
app._on_mp_played("sandbox", 14)
check("a listing that never read the readout is given one",
      app.games_extra.get("submitted"), {"sandbox": True})

print("\n3. a listing from another turn is listed again")
app = bare(rows=[row(turn=13)], submitted={"sandbox": True})
app._on_mp_played("sandbox", 14)
check("the readout is not written against the wrong turn",
      app.games_extra["submitted"], {"sandbox": True})
check("the galaxies are listed again", app.listed, 1)
check("and nothing is drawn from the stale listing", app.drawn, 0)

print("\n4. off the Galaxies page the readout waits for the page")
app = bare(page="home", rows=[row()], submitted={"sandbox": False})
app._on_mp_played("sandbox", 14)
check("the readout is still updated", app.games_extra["submitted"],
      {"sandbox": True})
check("but nothing is drawn", app.drawn, 0)
app = bare(page="home", rows=[row(turn=13)])
app._on_mp_played("sandbox", 14)
check("and nothing is listed", app.listed, 0)

print("\n5. nothing to mark")
app = bare(rows=[row("crowded")], submitted={})
app._on_mp_played("sandbox", 14)
check("a galaxy not in the listing changes nothing",
      (app.games_extra, app.drawn, app.listed), ({"submitted": {}}, 0, 0))
app = bare(rows=[row()], submitted={})
app._on_mp_played("sandbox", None)
check("a submission without a turn changes nothing",
      (app.games_extra, app.drawn, app.listed), ({"submitted": {}}, 0, 0))

print()
print("turn played checks failed: " + ", ".join(fails) if fails
      else "turn played checks passed")
sys.exit(1 if fails else 0)
