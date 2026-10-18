"""
test_continue.py: Single Player's Play continues and New Game starts over
    server\\.venv\\Scripts\\python.exe release\\tests\\test_continue.py

Play on a mode that `continues` starts its client on the newest .dat in
data\\games, and keeps the opponent's memory; New Game starts the shipped
galaxy and resets it. Headless: the Launcher is built without __init__ and the
client launch is recorded, not run.
"""
import json
import os
import shutil
import sys
import tempfile
import time

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


with open(os.path.join(REPO, "release", "manifest.json"), encoding="utf-8") as f:
    MODES = json.load(f)["modes"]
MODE = next(m for m in MODES if m.get("continues"))
data = tempfile.mkdtemp(prefix="continue_")
games = os.path.join(data, "games")
os.makedirs(games)

launched, ai = [], []
L.running_clients = lambda exes: []
L.launch_mode = lambda mode, game_root, galaxy_root, galaxy=None: (
    launched.append(galaxy) or object())


def bare():
    app = object.__new__(L.Launcher)
    app.view = FakeView()
    app.data_dir = data
    app.game_root = app.galaxy_root = data
    app.client_exes = []
    app.child = None
    app.running_mode = None
    app.said = []
    app.say = app.said.append
    app.set_status = lambda *a: None
    app.start_ai = lambda mode, fresh_galaxy=True: ai.append(fresh_galaxy)
    app.say_threadsafe = app.said.append
    # The background thread runs inline, and its result reaches `done` as the
    # message queue would deliver it.
    app._in_background = lambda label, work, done=None: done(work())
    return app


generated = []


def fake_generate(game_root, pass_file, save_dir, out, log=print, timeout=None):
    generated.append(pass_file)
    with open(out, "wb") as f:
        f.write(b"FRESH")
    return out


def failing_generate(*a, **k):
    raise RuntimeError("the client never came up")


print("\n1. the manifest marks Single Player, and only it, as continuing")
check("Single Player continues", MODE["id"], "testbed")
check("no other mode does",
      [m["id"] for m in MODES if m.get("continues")], ["testbed"])

print("\n2. with no save, Play starts the shipped galaxy")
bare().on_play(MODE)
check("no save is passed", launched[-1], None)
check("and the opponent starts fresh", ai[-1], True)

print("\n3. with saves, Play continues the newest")
older = os.path.join(games, "before war_turn8.dat")
newer = os.path.join(games, "after war_turn12.dat")
for path, age in ((older, 100), (newer, 10)):
    with open(path, "wb") as f:
        f.write(b"SAVE")
    os.utime(path, (time.time() - age, time.time() - age))
with open(os.path.join(games, "notes.txt"), "w") as f:
    f.write("not a save")
app = bare()
app.on_play(MODE)
check("the newest .dat is passed", launched[-1], newer)
check("and the opponent keeps what it knew", ai[-1], False)
check("the log says which save", any("after war_turn12.dat" in s for s in app.said),
      True)

print("\n4. New Game builds a fresh galaxy even with saves")
L.generate_galaxy = fake_generate
bare().on_play(MODE, fresh=True)
fresh_dat = os.path.join(games, L.NEW_GAME_FILE)
check("from the mode's pass file", [os.path.basename(p) for p in generated],
      [MODE["generate"]])
check("and plays the galaxy it built", launched[-1], fresh_dat)
check("with the opponent starting fresh", ai[-1], True)
check("the other saves are left alone", sorted(os.listdir(games)),
      sorted(["after war_turn12.dat", "before war_turn8.dat", "notes.txt",
              L.NEW_GAME_FILE]))
check("and Play continues the new game next time",
      L.latest_save(games), fresh_dat)

print("\n4a. the galaxy is built with a TestBed-based client, the shipped size")
builders = os.path.join(data, "builders")
os.makedirs(builders)
check("from a checkout, the tracked TestBed when the game folder has none",
      os.path.basename(L.galaxy_builder(builders) or ""),
      "CosmicSupremacy_TestBed.exe")
for exe in ("CosmicSupremacy_TestBed.exe", "CosmicSupremacy_Player.exe"):
    open(os.path.join(builders, exe), "wb").close()
check("the player client first, as a release has it",
      L.galaxy_builder(builders),
      os.path.join(builders, "CosmicSupremacy_Player.exe"))

print("\n4b. a galaxy that cannot be built falls back to the shipped one")
L.generate_galaxy = failing_generate
app = bare()
app.on_play(MODE, fresh=True)
check("the mode's own galaxy is played", launched[-1], None)
check("with the opponent starting fresh", ai[-1], True)
check("and the log says why", any("never came up" in s for s in app.said), True)

print("\n5. the page offers New Game beside a continuing mode")
js = open(os.path.join(REPO, "release", "webui", "app.js"), encoding="utf-8").read()
check("the button calls new_game", 'call("new_game", m.id)' in js, True)

print("\n6. a finished Tutorial is marked on its row")
tutorial = next(m for m in MODES if m.get("id") == "tutorial")
check("not before it is passed", L.mode_done(tutorial, data), False)
with open(os.path.join(data, L.TUTORIAL_FILE), "w") as f:
    f.write('{"passed": true}')
check("and once it is", L.mode_done(tutorial, data), True)
check("no other mode is marked", L.mode_done(MODE, data), False)
check("the page draws the mark", 'el("div", "done", "Completed")' in js, True)
import inspect                                                  # noqa: E402
check("the rows are redrawn when a game exits",
      "self.view.show_modes(self)" in inspect.getsource(L.Launcher._watch_game),
      True)

shutil.rmtree(data, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
