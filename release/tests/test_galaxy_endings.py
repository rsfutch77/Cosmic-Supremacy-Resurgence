"""
Two ways a galaxy stops being one a player can play, and what the launcher
says about each: the operator ended it (K5), or the referee took the player's
seat back after missed turns and warned them first (K3).

    server\\.venv\\Scripts\\python.exe release\\tests\\test_galaxy_endings.py

Headless and offline. Galaxies are made in a temporary folder and changed with
the store methods the operator's tools and the referee use: `close` for an
ended galaxy, `abandonment.enforce` for a warning, and the same state write
`abandonment.reclaim` makes for a seat taken back.

The launcher object is not built. The methods under test are called on a stub
that carries only the attributes they read, so nothing reaches Tk or a port.
"""
import inspect
import json
import os
import queue
import shutil
import sys
import tempfile
import time

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))
sys.path.insert(0, os.path.join(REPO, "server", "dev_tools"))

import launcher as L                                            # noqa: E402
import turn_store                                               # noqa: E402
import abandonment                                              # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


dirs = []


def fresh_dir():
    d = tempfile.mkdtemp(prefix="endings_")
    dirs.append(d)
    return d


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def make_galaxy(root, civs, turn=4, **extra):
    os.makedirs(os.path.join(root, "turns"), exist_ok=True)
    state = {"turn": turn, "turn_seconds": 1800, "civs": list(civs),
             "deadline": time.time() + 900, "hash": "x"}
    state.update(extra)
    write_json(os.path.join(root, "state.json"), state)
    return root


class Label:
    """Enough of a tk label for the page's status and hint lines."""

    def __init__(self):
        self.text, self.fg, self.mapped = "", None, False

    def configure(self, text=None, fg=None, **kw):
        if text is not None:
            self.text = text
        if fg is not None:
            self.fg = fg

    def cget(self, key):
        return self.text

    def winfo_ismapped(self):
        return self.mapped

    def pack(self, **kw):
        self.mapped = True

    def pack_forget(self):
        self.mapped = False


class Page:
    """The Galaxies page's state, without the window it hangs off."""

    def __init__(self, data_dir, games_dir, player):
        self.data_dir, self.games_dir, self.player = data_dir, games_dir, player
        self.own_store = None
        self.games_busy = False
        self.games_status, self.games_hint = Label(), Label()
        self.games_rows, self.games_extra, self.games_err = None, {}, None
        self.games_recs = {}
        self.msgs = queue.Queue()
        self.said = []
        self.drawn = 0
        self.dialogs = []
        self.answer = None

    def say(self, line):
        self.said.append(line)

    def _draw_galaxies(self):
        self.drawn += 1
        self.games_recs = L.load_joined(self.data_dir)

    def _choice_dialog(self, title, body, choices):
        self.dialogs.append((title, body, [c[1] for c in choices]))
        return self.answer

    def _submitted_state(self, rows, recs, player):
        return L.Launcher._submitted_state(self, rows, recs, player)

    def _join_answers(self, rows, recs):
        return L.Launcher._join_answers(self, rows, recs)

    def _reclaims(self, rows, recs):
        return L.Launcher._reclaims(self, rows, recs)

    def refresh(self):
        """One Games refresh, run to the end: listed, posted, taken in."""
        L.Launcher.refresh_games(self)
        kind, rows, extra, err = self.msgs.get(timeout=30)
        L.Launcher._on_games(self, rows, extra, err)
        return {g.id: g for g in rows}, extra

    def view(self):
        return L.Launcher._galaxy_view(self)

    def hint(self, rows):
        L.Launcher._show_hint(self, list(rows), self.view())
        return self.games_hint.text if self.games_hint.mapped else ""


asked = []
real_open = L.open_player_store


def counting_open(ts, spec, token=None):
    asked.append(spec)
    return real_open(ts, spec, token)


def counted_refresh(page):
    asked.clear()
    L.open_player_store = counting_open
    try:
        return page.refresh()
    finally:
        L.open_player_store = real_open


you = L.galaxy_column("you")
REASON_TEXT = ("Season one is over. Season two starts on Friday in "
               "sandbox2.")

print("1. a closed galaxy a player is in says it has ended (K5)")
root = fresh_dir()
old = make_galaxy(os.path.join(root, "season1"), ["Ada", "Bob"], turn=40)
make_galaxy(os.path.join(root, "season2"), ["Bob"], turn=1)
make_galaxy(os.path.join(root, "strangers"), ["Carol"], turn=9)
turn_store.open_store(old).close(reason=REASON_TEXT)
turn_store.open_store(os.path.join(root, "strangers")).close()
data = fresh_dir()
page = Page(data, root, "Ada")
rows, extra = counted_refresh(page)
view = page.view()
check("the directory lists the closed galaxy as closed",
      rows["season1"].status, L.CLOSED)
check("and still as one the player is in", rows["season1"].joined, True)
# Fails if the You column kept its "your turn" reading: a closed galaxy has
# no turn left to play.
check("the You column says it has ended",
      you.text(rows["season1"], view), "galaxy ended")
# Fails with the old rule, which offered a closed galaxy nothing at all.
check("the row offers Reason", L.row_action(rows["season1"], view.recs),
      L.ENDED)
check("labelled Reason", L.ACTION_TEXT[L.ENDED], "Reason")
check("never Play", L.row_action(rows["season1"], view.recs) == L.PLAY,
      False)
check("a closed galaxy the player is not in still offers nothing",
      L.row_action(rows["strangers"], view.recs), None)
check("the fresh galaxy is offered View",
      L.row_action(rows["season2"], view.recs), L.VIEW)
# Fails if the hint went on about View only: the player has to be told the
# galaxy they were playing is over before they are told where else to go.
check("the line under the table says it ended and what to do",
      page.hint(rows.values()),
      lambda t: "season1 has ended" in t and "Reason" in t
      and "another galaxy" in t)
# Fails if the submitted poll still asked a closed galaxy every refresh.
check("the refresh did not ask the closed galaxy whether a turn was played",
      extra.get("submitted", {}).get("season1"), None)
check("nor open its store at all", old in asked, False)

print("\n2. Reason reads the operator's own words")
L.Launcher.show_ended(page, rows["season1"])
title, body, _choices = page.dialogs[-1]
check("the box is titled for an ended galaxy", title, "Galaxy ended")
check("it says the galaxy has been closed", "has been closed" in body, True)
# Fails if the reason were not read from the state: the directory row does
# not carry it.
check("with the operator's reason in it", REASON_TEXT in body, True)
check("and says what to do", "Pick another galaxy" in body, True)
check("a state that cannot be read still gets a sentence",
      L.ended_text(rows["season1"], None),
      lambda t: "closed" in t and "another galaxy" in t)
check("a galaxy reopened since the listing says so",
      L.ended_text(rows["season1"], {"status": "open"}),
      lambda t: "no longer closed" in t)

print("\n3. a request lodged before the galaxy ended")
root = fresh_dir()
ended = make_galaxy(os.path.join(root, "ended"), ["Bob"], turn=12)
data = fresh_dir()
directory = L.open_galaxy_directory(root)
listed = {g.id: g for g in directory.galaxies(player="Ada")}
store = turn_store.open_store(ended)
L.send_join_request(store, L.join_request("Ada", None, "0.1.5", turn=12))
L.save_joined(data, L.joined_record(listed["ended"], "Ada", None, root,
                                    turn=12))
store.close(reason=REASON_TEXT)
page = Page(data, root, "Ada")
rows, extra = counted_refresh(page)
view = page.view()
# Fails if the row still said "joining next turn": a closed galaxy resolves
# no more requests, so that is a wait with no end.
check("the row says the galaxy ended rather than joining next turn",
      you.text(rows["ended"], view), "galaxy ended")
check("and offers Reason", L.row_action(rows["ended"], view.recs), L.ENDED)
# Fails if awaiting_answer still polled a closed galaxy.
check("no answer is asked for from a closed galaxy",
      L.awaiting_answer(rows["ended"], view.recs), False)
check("so the refresh did not open its store", ended in asked, False)

print("\n4. the galaxy multiplayer.json names, closed")
page.own_store = ended
view = page.view()
g = rows["ended"]._replace(joined=False)
check("offers Reason rather than Play",
      L.row_action(g, {}, own=ended), L.ENDED)

print("\n5. a followed galaxy that closes stops the loop and says why")


class App:
    """The turn loop's side of the launcher, without its window."""

    def __init__(self, store):
        self.mp_store = store
        self.mp_civ = "Ada"
        self.mp_note = ""
        self.mp_turn = None
        self.mp_deadline = None
        self.mp_turn_seconds = 1800
        self.mp_waiting = False
        self.mp_capture = None
        self.mp_playing = {"id": "live", "name": "Live"}
        self.mp_reopening = False
        self.mp_sending = False
        self.mp_turn_open = False
        self.mp_stop = False
        self.mp_closed = False
        self.mp_probed = None
        self.data_dir = fresh_dir()
        self.msgs = queue.Queue()
        self.said, self.warned, self.refreshed = [], [], 0

    def say(self, line):
        self.said.append(line)

    say_threadsafe = say

    def warn(self, text):
        self.warned.append(text)

    def refresh_games(self):
        self.refreshed += 1

    def _mp_probe_closed(self, store):
        return L.Launcher._mp_probe_closed(self, store)

    def _warning_note(self, store, civ, current):
        return L.Launcher._warning_note(self, store, civ, current)

    def state(self, kind, **facts):
        return L.Launcher._mp_state(self, kind, **facts)

    def drain(self):
        """What the Tk thread's queue would hand on, handed on."""
        got = []
        while True:
            try:
                msg = self.msgs.get_nowait()
            except queue.Empty:
                return got
            got.append(msg[0])
            if msg[0] == "__mp_closed__":
                L.Launcher._on_mp_closed(self, msg[1], msg[2])
            elif msg[0] == "__mp_warning__":
                L.Launcher._on_mp_warning(self, msg[1], msg[2])


class CountingStore:
    """A real store, counting state reads."""

    def __init__(self, inner):
        self.inner, self.reads = inner, 0

    def state(self):
        self.reads += 1
        return self.inner.state()

    def note(self, civ, turn):
        return self.inner.note(civ, turn)


root = fresh_dir()
live = turn_store.open_store(make_galaxy(os.path.join(root, "live"),
                                         ["Ada"], turn=7))
live.close(reason=REASON_TEXT)
# What `player_turn.follow` meets at the deadline, and reports as `lost`.
try:
    live.submit("Ada", 7, b"orders")
    refused = None
except turn_store.GalaxyClosed as exc:
    refused = exc
check("a submission into a closed galaxy is refused as closed",
      type(refused).__name__, "GalaxyClosed")
app = App(CountingStore(live))
app.state("lost", turn=7, civ="Ada", error=str(refused))
check("a turn that could not be sent reads the state once",
      app.mp_store.reads, 1)
check("and hands the closure to the Tk thread", app.drain(),
      ["__mp_closed__"])
# Fails without the stop: the loop waited for a turn that never comes.
check("which stops the loop", app.mp_stop, True)
check("says so beside the turn number", app.mp_note, L.GALAXY_ENDED_NOTE)
check("and shows the operator's reason", app.warned,
      lambda w: len(w) == 1 and REASON_TEXT in w[0])
check("and relists, so the row changes too", app.refreshed, 1)
app.state("stopped", turns=0)
# Fails if the loop's own "stopped" on the way out overwrote the reason it
# stopped.
check("the loop's own stop does not overwrite that", app.mp_note,
      L.GALAXY_ENDED_NOTE)
app.state("lost", turn=7, civ="Ada", error="again")
check("once known closed, it is not read again", app.mp_store.reads, 1)

print("\n   and one that is merely failing is left alone")
open_store = turn_store.open_store(make_galaxy(os.path.join(root, "open"),
                                               ["Ada"], turn=7))
app = App(CountingStore(open_store))
app.state("lost", turn=7, civ="Ada", error="network")
check("an open galaxy's lost turn posts nothing", app.drain(), [])
check("and does not stop the loop", app.mp_stop, False)
check("the readout says the turn could not be sent", app.mp_note,
      "that turn could not be sent")

print("\n   and one that stops ticking while the loop waits")
app = App(CountingStore(live))
app.mp_waiting = True
app.mp_deadline = time.time() - 7200
stopped = L.Launcher._mp_stalled(app)
deadline_probe = time.time() + 10
while app.msgs.empty() and time.time() < deadline_probe:
    time.sleep(0.05)
# Fails without the probe: a closed galaxy's wait read "turns have stopped",
# which sends a player to the operator about a machine that is fine.
check("a wait past the stall line asks the state once",
      app.mp_store.reads, 1)
check("and finds the galaxy closed", app.drain(), ["__mp_closed__"])
for _ in range(5):
    L.Launcher._mp_stalled(app)
check("after which it is not called stopped", L.Launcher._mp_stalled(app),
      0.0)
check("and nothing more is read", app.mp_store.reads, 1)
app = App(CountingStore(open_store))
app.mp_waiting = True
app.mp_deadline = time.time() - 7200
L.Launcher._mp_stalled(app)
time.sleep(0.5)
for _ in range(5):
    L.Launcher._mp_stalled(app)
time.sleep(0.2)
check("an open galaxy that has stopped is probed once per deadline",
      app.mp_store.reads, 1)
check("and is still called stopped", L.Launcher._mp_stalled(app),
      lambda s: s > 0)
check("with nothing posted", app.drain(), [])
check("a report about a store no longer followed is dropped",
      (L.Launcher._on_mp_closed(app, object(), "x"), app.mp_stop)[1], False)

print("\n6. the referee's warning about missed turns (K3)")
said = abandonment.warning_note("Ada", 6, 12)
# Fails if abandonment's opening line and the launcher's constant drift.
check("the launcher recognises abandonment's own warning",
      L.abandonment_warning(said), said)
check("a note of refusals only is not a warning",
      L.abandonment_warning(["system rename dropped"]), None)
check("a warning after refusals is taken from its first line",
      L.abandonment_warning(["rename dropped"] + said), said)
check("nothing is not a warning", L.abandonment_warning(None), None)
box = L.warning_text("sandbox", said)
check("the box names the galaxy", "in sandbox" in box, True)
check("and carries the referee's lines whole",
      all(line in box for line in said), True)

print("\n   a warning left by the referee's own enforcement is found at Play")
root = fresh_dir()
path = make_galaxy(os.path.join(root, "quiet"), ["Ada", "Bob"], turn=4,
                   **{abandonment.WARN_KEY: 2, abandonment.RECLAIM_KEY: 5})
quiet = turn_store.open_store(path)
quiet.archive(3, {"submitted": ["Bob"], "missing": ["Ada"]})
quiet.submit("Bob", 4, b"orders")
# Closing turn 4 with Ada silent for a second turn in a row.
abandonment.enforce(quiet, 4, b"", log=lambda *_a: None)
check("the referee left Ada a note on turn 4", bool(quiet.note("Ada", 4)),
      True)
app = App(quiet)
got = L.Launcher._warning_note(app, quiet, "Ada", 5)
# Fails if Play read the wrong turn's note: the warning is on the turn that
# was missed, which is the one before the turn now open.
check("Play at turn 5 reads it", got, lambda g: bool(g) and
      g[0].startswith("You have missed 2 turns"))
check("and it says how many turns are left", " 3 more turn" in " ".join(got),
      True)
check("Bob, who played, has none", L.Launcher._warning_note(app, quiet,
                                                            "Bob", 5), None)
check("a galaxy with no turn closed yet has none",
      L.Launcher._warning_note(app, quiet, "Ada", 0), None)


class Broken:
    def note(self, civ, turn):
        raise OSError("share gone")


check("a note that cannot be read is no warning, not a failed Play",
      L.Launcher._warning_note(app, Broken(), "Ada", 5), None)
start = inspect.getsource(L.Launcher.start_multiplayer)
at = [start.find(s) for s in ("roster_problem(", "self._warning_note(",
                              "self.mp_store = store")]
check("Play reads the warning after the roster check and before following",
      -1 not in at and at == sorted(at), True)

print("\n   and one met by a followed loop is shown too")
app = App(quiet)
app.mp_note = "waiting for the next turn"
app.state("refused_orders", turn=4, civ="Ada", count=len(got))
check("the note the loop logged is read as a warning", app.drain(),
      ["__mp_warning__"])
check("and shown", app.warned, lambda w: len(w) == 1 and "missed" in w[0])
# Fails if the raw kind leaked into the readout, which it used to.
check("the readout is left alone", app.mp_note, "waiting for the next turn")
quiet.put_note("Bob", 4, ["system rename dropped"])
app = App(quiet)
app.mp_civ = "Bob"
app.state("refused_orders", turn=4, civ="Bob", count=1)
check("a note of refusals only shows nothing extra", app.drain(), [])

print("\n7. a seat taken back says so on the Galaxies page (K3)")
root = fresh_dir()
path = make_galaxy(os.path.join(root, "sandbox"), ["Ada", "Bob"], turn=8)
sandbox = turn_store.open_store(path)
data = fresh_dir()
listed = {g.id: g for g in L.open_galaxy_directory(root).galaxies(
    player="Ada")}
L.save_joined(data, L.joined_record(listed["sandbox"]._replace(joined=False),
                                    "Ada", None, root, turn=2))
key = turn_store.join_key({"uid": None, "name": "Ada"})
sandbox.answer_join(key, {"key": key, "name": "Ada", "outcome": "granted",
                          "turn": 3, "requested_turn": 2})
# What `abandonment.reclaim` writes once the wipe has produced a blob.
sandbox.update_state({"civs": ["Bob"], turn_store.RECLAIMED_KEY: {
    "Ada": {"turn": 20, "missed": 12, "at": time.time()},
    "Erin": {"turn": 15, "missed": 12, "at": time.time()}}})
page = Page(data, root, "Ada")
rows, extra = counted_refresh(page)
view = page.view()
check("the directory no longer has Ada in the galaxy",
      rows["sandbox"].joined, False)
# Fails without the reclaim read: the granted record read "joining next
# turn", with no button, for as long as the galaxy ran.
check("the refresh found the reclaim in the same pass as the answer",
      sorted(extra.get("reclaims") or {}), ["sandbox"])
check("and kept it in the joined record",
      (L.load_joined(data)["sandbox"].get("reclaimed") or {}).get("missed"),
      12)
check("the row says the seat was reclaimed",
      you.text(rows["sandbox"], view), "seat reclaimed")
check("and offers Reason", L.row_action(rows["sandbox"], view.recs),
      L.RECLAIMED)
check("the line under the table says so",
      page.hint(rows.values()),
      lambda t: "taken back" in t and "Reason" in t)
told = L.reclaimed_text(rows["sandbox"], L.load_joined(data)["sandbox"])
# Fails if the message said no seat exists, which is what K3 asks it not to.
check("Reason says the seat was taken back after the missed turns",
      "took the seat 'Ada' back at turn 20 after 12 missed turns" in told,
      True)
check("and not that there is no seat", "no seat for" in told, False)
check("it names nobody else who was reclaimed", "Erin" in told, False)
check("and says what to do", "Clear" in told and "new seat" in told, True)
rows, extra = counted_refresh(page)
check("a known reclaim is not read again", path in asked, False)

page.answer = "clear"
L.Launcher.show_reclaimed(page, rows["sandbox"])
check("Clear forgets the record", "sandbox" in L.load_joined(data), False)
rows, extra = page.refresh()
check("which puts View back on the row",
      L.row_action(rows["sandbox"], page.view().recs), L.VIEW)

print("\n   and a granted seat still being merged is not called reclaimed")
root = fresh_dir()
path = make_galaxy(os.path.join(root, "merging"), ["Bob"], turn=8)
merging = turn_store.open_store(path)
data = fresh_dir()
listed = {g.id: g for g in L.open_galaxy_directory(root).galaxies(
    player="Ada")}
L.save_joined(data, L.joined_record(listed["merging"], "Ada", None, root,
                                    turn=7))
merging.answer_join(key, {"key": key, "name": "Ada", "outcome": "granted",
                          "turn": 8, "requested_turn": 7})
page = Page(data, root, "Ada")
rows, extra = page.refresh()
check("no reclaim is recorded without one in the state",
      extra.get("reclaims"), {})
check("and the row reads as it did", you.text(rows["merging"], page.view()),
      "joining next turn")

print("\n   Play on a reclaimed seat still explains itself")
check("roster_problem says it was taken back and what to do",
      L.roster_problem("Ada", ["Bob"], {"turn": 20, "missed": 12}),
      lambda t: "took the seat 'Ada' back at turn 20" in t
      and "12 missed turns" in t and "new seat" in t)

for d in dirs:
    shutil.rmtree(d, ignore_errors=True)
print()
print(f"FAILURES: {fails}" if fails else "all checks passed")
sys.exit(1 if fails else 0)
