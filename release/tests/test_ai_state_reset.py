"""
A new game must not start the opponent with the last game's knowledge.

Both of the opponent's state files outlive the game that wrote them, and both
were reported by players after v0.1: a fog-of-war map carried over lets BadGuy
colonise inside the player's home system on turn 1, and a carried-over pass
marker holds the launcher's readiness gate open for the whole early game.

Needs nothing running: it only exercises the file handling.
"""
import os
import pathlib
import shutil
import sys
import tempfile

REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
import gamectl

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


def populate(state_dir):
    """The three files a finished game leaves behind."""
    os.makedirs(state_dir, exist_ok=True)
    names = ["discovered_108_a1b2c3d4_BadGuy.json",
             "discovered_96_deadbeef_BadGuy.json",
             "pass_BadGuy.json"]
    for name in names:
        with open(os.path.join(state_dir, name), "w", encoding="utf-8") as fh:
            fh.write('{"discovered": [1, 2, 3], "turn": 180}')
    return names


def listing(state_dir):
    return sorted(os.listdir(state_dir))


tmp = tempfile.mkdtemp(prefix="cs_ai_state_")
try:
    # A new game clears everything the opponent knew.
    state = os.path.join(tmp, "new_game", "ai_state")
    populate(state)
    removed = gamectl.reset_ai_state(state, "BadGuy")
    check("new game removes all three", sorted(removed),
          ["discovered_108_a1b2c3d4_BadGuy.json",
           "discovered_96_deadbeef_BadGuy.json", "pass_BadGuy.json"])
    check("new game leaves the directory empty", listing(state), [])

    # Load resumes the galaxy the map belongs to, so the map stays and only the
    # pass marker goes: the save reopens at a turn the marker has passed.
    state = os.path.join(tmp, "load", "ai_state")
    populate(state)
    removed = gamectl.reset_ai_state(state, "BadGuy", keep_discovery=True)
    check("load removes only the pass marker", removed, ["pass_BadGuy.json"])
    check("load keeps both maps", listing(state),
          ["discovered_108_a1b2c3d4_BadGuy.json",
           "discovered_96_deadbeef_BadGuy.json"])

    # Another civ's files are not ours to delete. duel.py and the multiplayer
    # work both run several civs against one state directory.
    state = os.path.join(tmp, "other_civ", "ai_state")
    populate(state)
    with open(os.path.join(state, "pass_GoodGuy.json"), "w",
              encoding="utf-8") as fh:
        fh.write("{}")
    with open(os.path.join(state, "discovered_108_a1b2c3d4_GoodGuy.json"), "w",
              encoding="utf-8") as fh:
        fh.write("{}")
    gamectl.reset_ai_state(state, "BadGuy")
    check("another civ's state survives", listing(state),
          ["discovered_108_a1b2c3d4_GoodGuy.json", "pass_GoodGuy.json"])

    # First ever run: the directory does not exist yet.
    removed = gamectl.reset_ai_state(os.path.join(tmp, "absent"), "BadGuy")
    check("a missing state directory is not an error", removed, [])

    # The gate the reset exists to close: with no marker, the launcher waits.
    state = os.path.join(tmp, "gate", "ai_state")
    populate(state)
    check("a stale marker reads back", gamectl.ai_pass_marker(state, "BadGuy"),
          lambda got: got[0] == 180)
    gamectl.reset_ai_state(state, "BadGuy")
    check("after the reset there is nothing to satisfy the gate",
          gamectl.ai_pass_marker(state, "BadGuy"), (None, None))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if fails:
    print(f"FAILED: {len(fails)} check(s): {', '.join(fails)}")
    sys.exit(1)
print("all checks passed")
