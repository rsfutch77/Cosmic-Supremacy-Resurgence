"""
test_starting_galaxy.py: a galaxy waiting for its first turn reads "starting"
    server\\.venv\\Scripts\\python.exe release\\tests\\test_starting_galaxy.py

An open galaxy on turn 0 shows "starting" in the status column, in the good
colour, and goes back to "open" once its first turn closes. A stopped or
closed galaxy keeps its own word. The column still sorts on the stored word.

Headless and offline. Rows are built by hand; the colour is read from a
Launcher built without `__init__`. Drawing is not covered.
"""
import os
import sys

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import galaxy_directory                                         # noqa: E402
import launcher as L                                            # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


NOW = 1_000_000.0
TWO_HOURS = 7200.0
FOUR_DAYS = 4 * 86400.0


def gx(gid, status="open", turn=0, over=None, left=None, joined=False):
    """One listed galaxy with a 2-hour turn, its deadline placed relative to NOW."""
    deadline = NOW - over if over is not None else NOW + left if left is not None else None
    return galaxy_directory.Galaxy(gid, gid, status, turn, deadline, 1, joined,
                                   f"C:\\g\\{gid}", TWO_HOURS)


ROWS = {
    "waiting": gx("waiting", left=FOUR_DAYS),                 # turn 0, days to go
    "joined": gx("joined", left=FOUR_DAYS, joined=True),       # the player is in
    "late": gx("late", over=60),                               # turn 0, a minute late
    "stuck": gx("stuck", over=86400),                          # turn 0, referee gone
    "running": gx("running", turn=1, left=3600),               # first turn closed
    "ended": gx("ended", status="closed", turn=0, over=86400),
    "forming": gx("forming", status="forming", turn=None),
}
VIEW = L.View(now=NOW)
status_col = L.galaxy_column("status")
printed = {k: status_col.text(g, VIEW) for k, g in ROWS.items()}

print("\n1. the word")
check("an open galaxy on turn 0 reads starting", printed["waiting"], "starting")
check("whether or not the player is in it", printed["joined"], "starting")
check("and still a minute past its first deadline", printed["late"], "starting")
check("a turn 0 galaxy whose referee has stopped says stopped", printed["stuck"],
      "stopped")
check("after its first turn closes it reads open", printed["running"], "open")
check("a closed galaxy reads closed, whatever its turn", printed["ended"], "closed")
check("a forming galaxy keeps its own word", printed["forming"], "forming")

print("\n2. the colour")
app = object.__new__(L.Launcher)
check("a starting galaxy takes the good colour in the status cell",
      app._cell_colour(status_col, ROWS["waiting"], VIEW, near=False), L.OK)
check("lit or not",
      app._cell_colour(status_col, ROWS["joined"], VIEW, near=True), L.OK)
check("a stopped turn 0 galaxy takes the warning colour instead",
      app._cell_colour(status_col, ROWS["stuck"], VIEW, near=False), L.WARN)
check("an open galaxy past turn 0 is dim as before",
      app._cell_colour(status_col, ROWS["running"], VIEW, near=False), L.DIM)
check("the colour is the status cell's, not the whole row's",
      app._cell_colour(L.galaxy_column("name"), ROWS["waiting"], VIEW, near=False),
      L.DIM)

print("\n3. the order")
check("starting sorts with open, on the stored word",
      status_col.value(ROWS["waiting"], VIEW) == status_col.value(ROWS["running"], VIEW),
      True)

print()
if fails:
    print(f"FAILED: {len(fails)}")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("all passed")
