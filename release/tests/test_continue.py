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
    return app


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

print("\n4. New Game starts over even with saves")
bare().on_play(MODE, fresh=True)
check("no save is passed", launched[-1], None)
check("and the opponent starts fresh", ai[-1], True)
check("and the saves are left alone", sorted(os.listdir(games)),
      ["after war_turn12.dat", "before war_turn8.dat", "notes.txt"])

print("\n5. the page offers New Game beside a continuing mode")
js = open(os.path.join(REPO, "release", "webui", "app.js"), encoding="utf-8").read()
check("the button calls new_game", 'call("new_game", m.id)' in js, True)

shutil.rmtree(data, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
