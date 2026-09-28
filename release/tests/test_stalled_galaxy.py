"""
test_stalled_galaxy.py , telling a dead referee from a late one
================================================================
    server\\.venv\\Scripts\\python.exe release\\tests\\test_stalled_galaxy.py

Turns are closed by a referee on one person's PC, started by a scheduled task
when they log in. When that machine is off, every galaxy it refs runs its
countdown to zero and then does nothing, which until now looked exactly like a
turn about to be closed. What is under test is the difference: which galaxies
the launcher calls stopped, which it leaves alone, and what it says about the
ones it calls.

**The claim that matters is the second one.** Anything can flag an overdue
galaxy; a rule that flags every one of them is wrong six times a day per
galaxy, because a referee is allowed to be late. So every check here that a
stopped galaxy is named runs beside one on a galaxy that is also past its
deadline and is healthy, at the two distances that break a naive rule: a few
seconds over, which is the ordinary case, and the same absolute lateness in
two galaxies with different turn lengths, which is what says the threshold
scales rather than being a constant someone liked.

Headless and offline. The table is checked against rows built by hand and
against copies of the operator's own `server\\uidemo` galaxies, with their
deadlines rewritten in the copy; the originals are fingerprinted before and
after and checked untouched. The readout and the status line are checked
against a Launcher built without `__init__`, so no window opens, no game
starts and no port is taken. Nothing reads a store more than the code under
test already does, which is itself one of the checks: H5's binding quota is
Firestore reads and this runs once a second.

What is not here is the drawing. Whether the warning colour is visible against
the panel, whether a three-line hint pushes the table about and whether
"stopped" reads as urgent at a glance are questions for a screen.
"""
import hashlib
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import galaxy_directory                                         # noqa: E402
import launcher as L                                            # noqa: E402

# `referee_worker.py` is read rather than imported. Importing it reaches
# `referee` and the whole of `server\\dev_tools`, which is a referee's worth of
# machinery for three numbers, and this file is meant to run wherever the
# launcher does. Reading the source still ties the numbers to the file that
# owns them: a threshold that stopped agreeing with the worker's own backoff
# fails here rather than quietly stopping meaning anything.
WORKER_SRC = (pathlib.Path(REPO) / "server" / "referee_worker.py").read_text(
    encoding="utf-8")


def worker_number(name: str) -> float:
    """One of `referee_worker.py`'s module-level seconds, by name."""
    for line in WORKER_SRC.splitlines():
        names, _, values = line.partition("=")
        names = [n.strip() for n in names.split(",")]
        if name not in names:
            continue
        values = [v.strip() for v in values.split(",")]
        if len(names) == len(values):
            return float(values[names.index(name)])
    raise AssertionError(f"referee_worker.py no longer defines {name}")


GRACE = worker_number("GRACE")
POLL = worker_number("POLL")
BACKOFF_MAX = worker_number("BACKOFF_MAX")

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


dirs = []


def fresh_dir():
    d = tempfile.mkdtemp(prefix="stall_")
    dirs.append(d)
    return d


NOW = 1_000_000.0
HOUR = 3600.0
FOUR_HOURS = 4 * HOUR
QUARTER_HOUR = 900.0


def gx(gid, name=None, status="open", turn=11, over=None, left=None,
       players=2, joined=False, turn_seconds=FOUR_HOURS):
    """One listed galaxy, placed relative to NOW.

    `over` is how long ago its deadline passed and `left` is how long until it
    does, so a row reads as the state it is meant to be in rather than as an
    epoch nobody can check by eye.
    """
    if over is not None:
        deadline = NOW - over
    elif left is not None:
        deadline = NOW + left
    else:
        deadline = None
    return galaxy_directory.Galaxy(gid, name or gid, status, turn, deadline,
                                   players, joined, f"C:\\g\\{gid}",
                                   turn_seconds)


print("\n1. the threshold comes from the referee's own numbers")
# A worker that is up and working can be a whole BACKOFF_MAX behind a turn it
# is retrying, plus the grace it waits out, plus the poll that notices the
# deadline, plus N5's measured tick. A floor under any of that would call a
# working referee dead.
ordinary = GRACE + POLL + 10.0
check("the floor covers the longest gap a live worker leaves between tries",
      L.STALL_FLOOR >= BACKOFF_MAX, True)
check("and the ordinary lateness of a turn that closes normally",
      L.STALL_FLOOR > ordinary, True)
check("a four-hour galaxy is given an hour", L.stall_after(FOUR_HOURS), HOUR)
check("a one-hour galaxy a quarter of it", L.stall_after(HOUR), HOUR / 4)
check("a fifteen-minute galaxy gets the floor, not a quarter of 900s",
      L.stall_after(QUARTER_HOUR), L.STALL_FLOOR)
check("and a day-long one the ceiling, not a quarter of a day",
      L.stall_after(24 * HOUR), L.STALL_CEILING)
check("no turn length at all is judged by the ceiling, the latest this fires",
      [L.stall_after(v) for v in (None, 0, -1, "", "soon", {})],
      lambda got: all(v == L.STALL_CEILING for v in got))
check("and every threshold sits between the floor and the ceiling",
      [L.stall_after(v) for v in (1, 60, 900, 3600, 14400, 999999)],
      lambda got: all(L.STALL_FLOOR <= v <= L.STALL_CEILING for v in got))


print("\n2. late is not stopped")
# The case a naive threshold gets wrong, and the reason this file exists. N5
# measures a tick at about ten seconds and the worker reads the clock every
# five, so a turn a few seconds past its deadline is a turn being closed now.
four = dict(turn_seconds=FOUR_HOURS, now=NOW)
quarter = dict(turn_seconds=QUARTER_HOUR, now=NOW)
check("a turn with time left on it is not stopped",
      L.stalled_for(NOW + 600, **four), 0.0)
check("one that came due four seconds ago is not stopped either",
      L.stalled_for(NOW - 4, **four), 0.0)
check("nor one a minute past its deadline",
      L.stalled_for(NOW - 60, **four), 0.0)
check("nor a fifteen-minute galaxy four seconds over",
      L.stalled_for(NOW - 4, **quarter), 0.0)
check("nor one still inside the worker's longest retry gap",
      L.stalled_for(NOW - (BACKOFF_MAX - 1), **quarter), 0.0)
check("a four-hour galaxy two hours past its deadline is stopped",
      L.stalled_for(NOW - 7200, **four), 7200.0)
check("and the answer is how long it has been, not a flag",
      L.stalled_for(NOW - 9000, **four), 9000.0)

print("\n   the same lateness, two turn lengths, two answers")
SAME = 400.0        # six and a half minutes, past the floor and under an hour
check("six minutes stops a fifteen-minute galaxy",
      L.stalled_for(NOW - SAME, **quarter), SAME)
check("and does not stop a four-hour one",
      L.stalled_for(NOW - SAME, **four), 0.0)
check("which is the whole of the scaling claim",
      bool(L.stalled_for(NOW - SAME, **quarter))
      != bool(L.stalled_for(NOW - SAME, **four)), True)

print("\n   the states that are not a referee being off")
check("a closed galaxy a day past its deadline is not stopped",
      L.stalled_for(NOW - 86400, status=L.CLOSED, **four), 0.0)
check("because the worker deliberately ticks nothing in one",
      "if turn_store.status_of(state) == turn_store.CLOSED:" in WORKER_SRC,
      True)
check("a galaxy with no deadline has no clock to be behind on",
      L.stalled_for(None, **four), 0.0)
check("and a forming row is not stopped however old the listing is",
      L.row_stalled_for(gx("beacon", status="forming", turn=None,
                           turn_seconds=None), L.View(now=NOW)), 0.0)


print("\n3. the table says which galaxy it is")
# Two of these are past their deadline and healthy. If the rule were "the
# deadline has passed", crowded and lapsed would read stopped as well and the
# column would be telling a player the wrong thing about four rows out of six.
ROWS = [
    gx("sandbox", "Sandbox", over=7200, joined=True),           # stopped
    gx("crowded", "Crowded Galaxy", over=30),                   # 30s late
    gx("lapsed", "Lapsed Galaxy", over=SAME),                   # 6m late, 4h
    gx("outpost", "Outpost", over=SAME,                         # 6m late, 15m
       turn_seconds=QUARTER_HOUR),
    gx("frontier", "Frontier", left=600, turn_seconds=HOUR),    # healthy
    gx("retired", "Retired Galaxy", status="closed", over=86400),
]
VIEW = L.View(now=NOW)
status_col = L.galaxy_column("status")
printed = {g.id: status_col.text(g, VIEW) for g in ROWS}
check("the stopped galaxy says so", printed["sandbox"], "stopped")
check("and so does the fifteen-minute one six minutes over",
      printed["outpost"], "stopped")
check("a galaxy thirty seconds past its deadline still reads open",
      printed["crowded"], "open")
check("a four-hour galaxy six minutes over still reads open",
      printed["lapsed"], "open")
check("a galaxy with time left on it still reads open",
      printed["frontier"], "open")
check("and the one the operator ended still reads closed",
      printed["retired"], "closed")
check("so two rows out of six are flagged and four are not",
      sorted(k for k, v in printed.items() if v == "stopped"),
      ["outpost", "sandbox"])

check("the countdown column never offers a countdown on a stopped galaxy",
      [L.galaxy_column("left").text(g, VIEW)
       for g in ROWS if L.row_stalled_for(g, VIEW)],
      lambda got: all(t == "time is up" for t in got))
check("and the stored word is still what the column sorts on",
      [status_col.value(g, VIEW) for g in ROWS if g.id in
       ("sandbox", "crowded")],
      lambda got: got[0] == got[1])
check("so sorting by status does not move a stopped galaxy to the end",
      [g.id for g in L.sort_galaxies(ROWS, "status", view=VIEW)][-1],
      "retired")

print("\n   and draws it differently")


class Cell:
    """A Tk label as far as the table uses one."""

    def __init__(self, **kw):
        self.kw = dict(kw)

    def cget(self, key):
        return self.kw.get(key, "")

    def configure(self, **kw):
        self.kw.update(kw)

    def winfo_exists(self):
        return True


app = object.__new__(L.Launcher)
by_id = {g.id: g for g in ROWS}
check("a stopped galaxy takes the warning colour in the status cell",
      app._cell_colour(status_col, by_id["sandbox"], VIEW, near=True), L.WARN)
check("even when the player is not in it",
      app._cell_colour(status_col, by_id["outpost"], VIEW, near=False), L.WARN)
check("a healthy galaxy the player is in is lit as before",
      app._cell_colour(status_col, by_id["crowded"], VIEW, near=True), L.TEXT)
check("and one they are not is dim as before",
      app._cell_colour(status_col, by_id["frontier"], VIEW, near=False), L.DIM)
check("the colour is the status cell's, not the whole row's",
      app._cell_colour(L.galaxy_column("name"), by_id["sandbox"], VIEW,
                       near=False), L.DIM)


print("\n4. and what to do about it, in a player's words")
hint = L.stall_hint([(by_id["sandbox"], 7200.0)])
print(f"       {hint}")
check("it says something has stopped", "stopped" in hint.lower(), True)
check("it tells them to come back, which is true most of the time",
      "check back" in hint.lower(), True)
check("in a player's words rather than the plan's",
      hint, lambda t: not any(w in t for w in (
          "referee", "worker", "scheduled task", "N1", "store", "Firestore")))
# One sentence, by the operator's call. The table marks which galaxies are
# stopped a word per row, and repeating that underneath was not worth the
# length.
check("it is one short line", hint.count(".") <= 1 and len(hint) < 80, True)
# Fails if the line started varying again. Whatever is stopped and however
# long ago, this reads the same, which is what makes it one string to review
# rather than four.
check("the same line however many galaxies are stopped",
      L.stall_hint([(by_id["sandbox"], 7200.0), (by_id["outpost"], 400.0)]),
      hint)
check("and however long they have been stopped",
      L.stall_hint([(by_id["outpost"], 60.0)]), hint)


class Hint:
    """The hint label, which is packed only when there is something in it."""

    def __init__(self):
        self.kw, self.mapped = {"text": ""}, False

    def cget(self, key):
        return self.kw.get(key, "")

    def configure(self, **kw):
        self.kw.update(kw)

    def pack(self, **kw):
        self.mapped = True

    def pack_forget(self):
        self.mapped = False

    def winfo_ismapped(self):
        return self.mapped


def hint_for(rows, recs=None, now=NOW):
    """The line under the table, or "" when the table has nothing to say.

    `now` is the moment the rows are read against, because the hand-built rows
    below sit around NOW and the ones read off disk carry real epoch deadlines.
    """
    app = object.__new__(L.Launcher)
    app.games_hint = Hint()
    app._show_hint(rows, L.View(now=now, recs=recs))
    return app.games_hint.cget("text") if app.games_hint.mapped else ""


healthy = [g for g in ROWS if not L.row_stalled_for(g, VIEW)]
check("a table with a stopped galaxy in it carries the line",
      hint_for(ROWS), lambda t: "stopped" in t)
check("a table with none does not, however late two of its rows are",
      hint_for(healthy), lambda t: "stopped" not in t)
check("and the player with no seat still gets the line that was there before",
      hint_for([gx("frontier", "Frontier", left=600)]),
      lambda t: "View" in t)
check("which the stopped line takes precedence over, being the one to act on",
      hint_for([gx("frontier", "Frontier", left=600),
                gx("sandbox", "Sandbox", over=7200)]),
      lambda t: "stopped" in t and "View" not in t)


print("\n5. the galaxies the operator tests against, read off disk")
# Copies. `sandbox` in the original was advanced to turn 12 by the worker and
# nothing here may touch it, so every file is fingerprinted before and after.
SRC = os.path.join(REPO, "server", "uidemo")


def fingerprint(root):
    out = {}
    for base, _dirs, names in os.walk(root):
        for name in sorted(names):
            path = os.path.join(base, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root)] = hashlib.sha256(
                    fh.read()).hexdigest()
    return out


before = fingerprint(SRC)
copy = os.path.join(fresh_dir(), "uidemo")
shutil.copytree(SRC, copy)

# Where each copied galaxy's deadline is put, and why. The pairs that matter
# are crowded against sandbox, which are the same turn length at two distances
# past the deadline, and outpost against lapsed, which are the same distance
# past it at two turn lengths.
PLACED = {
    "sandbox": -7200,       # 4h turn, 2h over            stopped
    "crowded": -30,         # 4h turn, half a minute over open
    "lapsed": -SAME,        # 4h turn, 6m over            open
    "outpost": -SAME,       # 15m turn, 6m over           stopped
    "frontier": +600,       # 1h turn, still running      open
    "retired": -86400,      # closed a day ago            closed
}
clock = time.time()
for gid, offset in PLACED.items():
    path = os.path.join(copy, gid, "state.json")
    with open(path, encoding="utf-8") as fh:
        state = json.load(fh)
    state["deadline"] = clock + offset
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
# The index names the originals by absolute path, so the copy has to be
# repointed at itself or this would list the galaxies it must not read.
index = os.path.join(copy, galaxy_directory.INDEX)
with open(index, encoding="utf-8") as fh:
    listed = json.load(fh)
for entry in listed["galaxies"]:
    entry["store"] = os.path.join(copy, entry["id"])
with open(index, "w", encoding="utf-8") as fh:
    json.dump(listed, fh, indent=2)

data_dir = fresh_dir()      # no multiplayer.json, so no galaxy of its own
rows, problem = L.galaxy_rows(data_dir, copy, "DemoPlayer")
check("all six galaxies listed", sorted(g.id for g in rows),
      sorted(PLACED))
check("with no problem reported", problem, None)
check("every row carries the turn length it is judged by",
      sorted({g.turn_seconds for g in rows}), [900, 3600, 14400])
live = L.View(now=time.time())
said = {g.id: status_col.text(g, live) for g in rows}
print(f"       {said}")
check("the two that have stopped say so",
      sorted(k for k, v in said.items() if v == "stopped"),
      ["outpost", "sandbox"])
check("and crowded, thirty seconds past its deadline, does not",
      said["crowded"], "open")
check("nor lapsed, six minutes past a four-hour one",
      said["lapsed"], "open")
check("the closed galaxy is closed rather than stopped",
      said["retired"], "closed")
check("and the running one is untouched", said["frontier"], "open")
check("the line under the table reports them",
      hint_for(rows, now=live.clock),
      lambda t: "stopped" in t)
check("originals untouched", fingerprint(SRC), before)


print("\n6. the readout a player waiting for a turn is looking at")


class Widget:
    def __init__(self, **kw):
        self.kw = dict(kw)

    def cget(self, key):
        return self.kw.get(key, "")

    def configure(self, **kw):
        self.kw.update(kw)

    def pack(self, **kw):
        pass

    def pack_forget(self):
        pass

    def winfo_ismapped(self):
        return False


class Store:
    """A galaxy clock, counting every read as a Firestore document read."""

    def __init__(self, turn=12, left=-7200.0):
        self.turn, self.left = turn, left
        self.reads = 0

    def current(self):
        self.reads += 1
        return self.turn, time.time() + self.left

    def seconds_left(self):
        self.reads += 1
        return self.left


class Thread:
    def is_alive(self):
        return True


def bare(**over):
    """A Launcher with no window behind it."""
    app = object.__new__(L.Launcher)
    app.mp_store = None
    app.mp_note = ""
    app.mp_turn = None
    app.mp_deadline = None
    app.mp_turn_seconds = FOUR_HOURS
    app.mp_waiting = False
    app.mp_civ = "DemoPlayer"
    app.mp_capture = None
    app.mp_playing = None
    app.mp_thread = None
    app.mp_client_seen = False
    app.mp_client_gone = False
    app.mp_turn_open = True
    app.mp_sending = False
    app.mp_send_now = threading.Event()
    app.mp_reopen = threading.Event()
    app.mp_reopening = False
    app.mp_stop = False
    app.data_dir = data_dir
    app.turn_label = Widget(text="turn ,")
    app.cap_dot = Widget(fg=L.FAINT, text="\u25cf")
    app.cap_status = Widget(text="")
    app.ctl_buttons = {k: Widget(text=t, state="normal")
                       for k, t in (("load", "Load"), ("save", "Save"),
                                    ("turn", "Next Turn"))}
    app._ctl_client = None
    app._ctl_busy = False
    app._last_seen_turn = None
    app._waiting_since = None
    app.running_mode = None
    app.said = []
    app.say = app.said.append
    app.say_threadsafe = app.said.append
    app.msgs = queue.Queue()
    for k, v in over.items():
        setattr(app, k, v)
    return app


def waiting_label(overdue, turn_seconds=FOUR_HOURS):
    """The readout between turns, with the last deadline `overdue` ago."""
    app = bare(mp_store=Store(), mp_thread=Thread(),
               mp_turn_seconds=turn_seconds)
    app.mp_turn, app.mp_deadline = 12, time.time() - overdue
    app._mp_state("waiting", turn=12, civ="DemoPlayer")
    app.mp_client_gone = True
    app._refresh_turn()
    return app.turn_label.cget("text")


check("a turn two hours overdue says the turns have stopped",
      waiting_label(7200), lambda t: "stopped" in t and "2h ago" in t)
check("a turn thirty seconds overdue says what it always said",
      waiting_label(30), "waiting for the next turn")
check("and so does one six minutes overdue in a four-hour galaxy",
      waiting_label(SAME), "waiting for the next turn")
check("while six minutes stops a fifteen-minute galaxy",
      waiting_label(SAME, QUARTER_HOUR), lambda t: "stopped" in t)

print("\n   and it does not outrank what the launcher is doing itself")
app = bare(mp_store=Store(), mp_thread=Thread())
app.mp_turn, app.mp_deadline = 12, time.time() - 7200
app._mp_state("waiting", turn=12, civ="DemoPlayer")
app.mp_client_gone = True
app.mp_sending = True
app._refresh_turn()
check("a turn still on its way is said instead",
      app.turn_label.cget("text"), "sending your turn")
app.mp_sending, app.mp_reopening = False, True
app._refresh_turn()
check("and so is a game being opened again",
      app.turn_label.cget("text"), "opening the game again")
app.mp_reopening = False
app._refresh_turn()
check("with the stopped line underneath both",
      app.turn_label.cget("text"), lambda t: "stopped" in t)

print("\n   the turn a player is still in is never called stopped")
app = bare(mp_store=Store(turn=12, left=900.0), mp_thread=Thread())
app._mp_state("playing", turn=12, civ="DemoPlayer", seconds_left=900.0)
app._refresh_turn()
check("a turn with time left counts down as before",
      app.turn_label.cget("text"), lambda t: t.endswith("left"))
app._mp_state("playing", turn=12, civ="DemoPlayer", seconds_left=-30.0)
app._refresh_turn()
check("one thirty seconds over does not say stopped",
      app.turn_label.cget("text"), lambda t: "stopped" not in t)
app._mp_state("playing", turn=12, civ="DemoPlayer", seconds_left=-7200.0)
app._refresh_turn()
check("a player who reopened into a turn nothing will close is told",
      app.turn_label.cget("text"),
      lambda t: t.startswith("turn 12") and "stopped" in t)
check("rather than being shown a countdown that reads 0:00 forever",
      app.turn_label.cget("text"), lambda t: "0:00 left" not in t)

print("\n   and none of it reads the store")
store = Store(turn=12, left=-7200.0)
app = bare(mp_store=store, mp_thread=Thread())
app._refresh_turn()
check("the first tick asks once, because nothing has been heard yet",
      store.reads, 1)
app._mp_state("waiting", turn=12, civ="DemoPlayer")
app.mp_client_gone = True
for _ in range(600):
    app._refresh_turn()
check("ten minutes of ticks over a stopped galaxy ask it nothing more",
      store.reads, 1)
check("while going on saying it has stopped",
      app.turn_label.cget("text"), lambda t: "stopped" in t)


print("\n7. the status line, which says what is running and nothing more")
READY = ("server running on 127.0.0.1:8888", L.OK)


def status_for(overdue, waiting=True, turn_seconds=FOUR_HOURS):
    """The `mp_live and mp_client_gone` branch of `_watch_game`."""
    app = bare(mp_turn_seconds=turn_seconds)
    app.mp_deadline = time.time() - overdue
    app.mp_waiting = waiting
    app._ready = READY
    said = []
    app._status_if_changed = lambda text, colour: said.append((text, colour))
    stopped = app._mp_stalled()
    if stopped:
        app._status_if_changed(
            f"the galaxy has stopped , its turn was due {L.fmt_ago(stopped)}",
            L.WARN)
    else:
        app._status_if_changed(*app._ready)
    return said[0]


stopped_line, stopped_colour = status_for(7200)
late_line, late_colour = status_for(30)
check("a stopped galaxy is said on the line the player is watching",
      stopped_line, lambda t: "stopped" in t and "2h ago" in t)
check("in the warning colour", stopped_colour, L.WARN)
check("a galaxy merely late falls back to the ready line as before",
      (late_line, late_colour), READY)
check("the stopped line says nothing about a game running",
      stopped_line, lambda t: "Multiplayer" not in t and "running" not in t)
check("which is the confusion this branch exists to avoid",
      stopped_line != "Multiplayer , DemoPlayer", True)
check("and it is not said mid-turn, where the deadline is ahead",
      status_for(7200, waiting=False), READY)
check("the real launcher still falls back to its ready line",
      "_status_if_changed(*self._ready)"
      in __import__("inspect").getsource(L.Launcher._watch_game), True)
watch = __import__("inspect").getsource(L.Launcher._watch_game)
check("and the branch above is the one it runs, not a copy of it",
      ("self._mp_stalled()" in watch, "self._ready" in watch), (True, True))
check("which says it in the warning colour there too",
      watch[watch.index('f"the galaxy has stopped'):][:200],
      lambda tail: "WARN" in tail)


for d in dirs:
    shutil.rmtree(d, ignore_errors=True)

print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
