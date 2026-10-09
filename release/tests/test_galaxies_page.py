"""
The Galaxies page: what its table prints, what order a column puts it in, what
each row offers to do, and which rows there are at all.

Headless and offline. Everything here is the launcher's module-level code
against rows built by hand and against galaxies made in a temporary folder. No
game, no window, no network: the galaxies any of this opens are folders on this
disk, which is also the case that must never mint an identity or reach Firebase.

The half that is not here is the drawing. Whether the table lines up, whether
Back is where the eye looks for it and whether the page reads as a table are
questions for the screen, and the last review is the reason this file exists at
all. What can be checked here is that a column sorts on its value rather than
on its text, which is the thing a table gets wrong invisibly: the rows below are
built deliberately out of order, and every ordering check names a column whose
printed cell and underlying value disagree.
"""
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))
WEBUI = os.path.join(REPO, "release", "webui")

import galaxy_directory                                         # noqa: E402
import launcher as L                                            # noqa: E402
import turn_store                                               # noqa: E402
from fake_view import FakeView                                  # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


dirs = []


def fresh_dir():
    d = tempfile.mkdtemp(prefix="page_")
    dirs.append(d)
    return d


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def make_galaxy(root, civs, turn=4, deadline=None, extra=None):
    """A galaxy a launcher can read: a clock, a roster, and a turns folder."""
    os.makedirs(os.path.join(root, "turns"), exist_ok=True)
    state = {"turn": turn, "turn_seconds": 1800, "civs": list(civs),
             "deadline": time.time() + 900 if deadline is None else deadline,
             "hash": "x"}
    state.update(extra or {})
    write_json(os.path.join(root, "state.json"), state)
    return root


NOW = 1_000_000.0


def gx(gid, name, status="open", turn=4, left=None, players=2, joined=False,
       store=None):
    """One listed galaxy. `left` is seconds from NOW, not an absolute time."""
    return galaxy_directory.Galaxy(gid, name, status, turn,
                                   None if left is None else NOW + left,
                                   players, joined, store or f"C:\\g\\{gid}")


# Deliberately out of order, in every column, and with one row whose value each
# column cannot supply. A sort test on rows that already read correctly passes
# without sorting anything.
ROWS = [
    gx("lapsed", "Lapsed", turn=9, left=600, players=2),
    gx("retired", "Retired", status="closed", turn=110, left=3660, players=3),
    gx("crowded", "crowded", turn=11, left=3840, players=12),
    gx("beacon", "Beacon", status="forming", turn=None, left=None, players=0),
    gx("sandbox", "sandbox", turn=100, left=59, players=1, joined=True),
]
# What this launcher has joined, keyed by galaxy id. One entry here, because
# the rows above are the one-galaxy case; section 5a joins a second.
JOINING = {"crowded": {"galaxy": "crowded", "store": "C:\\g\\crowded",
                       "name": "Alice"}}
VIEW = L.View(now=NOW, recs=JOINING)


def order(key, reverse=False, view=VIEW, rows=None):
    return [g.id for g in L.sort_galaxies(rows if rows is not None else ROWS,
                                          key, reverse, view)]


def printed(key, view=VIEW, rows=None):
    col = L.galaxy_column(key)
    return [col.text(g, view) for g in (rows if rows is not None else ROWS)]


def by_text(key, reverse=False, view=VIEW):
    """What a sort on the rendered cell would have produced, for contrast."""
    col = L.galaxy_column(key)
    return [g.id for g in sorted(ROWS, key=lambda g: col.text(g, view),
                                 reverse=reverse)]


print("1. the columns, and where their text and their value part company")
check("every column has a key and a heading",
      [(c.key, bool(c.heading)) for c in L.GALAXY_COLUMNS],
      lambda got: all(k and h for k, h in got))
check("the page shows name, status, turn, time left, players and your state",
      [c.key for c in L.GALAXY_COLUMNS],
      ["name", "status", "turn", "left", "players", "you"])
check("a column is found by key", L.galaxy_column("turn").heading, "Turn")
check("and an unknown one is not invented", L.galaxy_column("nope"), None)
check("the countdown column is the one the clock rewrites",
      L.GALAXY_LEFT.key, "left")

check("turn prints 9, 11, 100 and 110 in the order the rows are in",
      printed("turn"), ["9", "110", "11", "", "100"])
check("time left prints a mixture of formats",
      printed("left"), ["10:00 left", "", "1h 04m left", "", "0:59 left"])
check("and your own state prints three different things",
      printed("you"),
      ["", "", "joining next turn", "", "you are in"])

print("\n2. a column sorts on its value and never on its cell")
check("turn ascending is 9, 11, 100, 110",
      order("turn"), ["lapsed", "crowded", "sandbox", "retired", "beacon"])
check("which is not what the printed cell would have given",
      by_text("turn"), lambda got: got != order("turn"))
check("turn 9 sorts below turn 11",
      order("turn").index("lapsed") < order("turn").index("crowded"), True)
check("and below turn 100, which as text it sorts above",
      order("turn").index("lapsed") < order("turn").index("sandbox"), True)
check("turn descending turns the known ones round",
      order("turn", True), ["retired", "sandbox", "crowded", "lapsed",
                            "beacon"])

check("time left ascending is 0:59, 10:00, 1h 04m",
      order("left"), ["sandbox", "lapsed", "crowded", "retired", "beacon"])
check("which is not what the printed cell would have given",
      by_text("left"), lambda got: got != order("left"))
check("'1h 04m left' sorts after '10:00', which as text it sorts before",
      order("left").index("crowded") > order("left").index("lapsed"), True)
check("time left descending is the furthest away first",
      order("left", True), ["crowded", "lapsed", "sandbox", "retired",
                            "beacon"])

check("status sorts by how far along a galaxy is, open first",
      order("status"), ["lapsed", "crowded", "sandbox", "beacon", "retired"])
check("which is the opposite end from the alphabet, where closed leads",
      by_text("status")[0], "retired")
check("status descending puts the closed galaxies first",
      order("status", True)[0], "retired")

check("your own state sorts the galaxy you are in to the top",
      order("you"), ["sandbox", "crowded", "lapsed", "retired", "beacon"])
check("which as text would have put the empty cells there instead",
      by_text("you")[0], lambda got: got != "sandbox")

check("name sorts without regard to case",
      order("name"), ["beacon", "crowded", "lapsed", "retired", "sandbox"])
check("which a case-sensitive sort of the cell would not have",
      by_text("name"), lambda got: got != order("name"))
check("name descending is the same order backwards",
      order("name", True), ["sandbox", "retired", "lapsed", "crowded",
                            "beacon"])

print("\n3. a row a column cannot speak for keeps to the bottom")
check("a forming galaxy has no turn", L.galaxy_column("turn").value(
    ROWS[3], VIEW), None)
check("a closed galaxy has no countdown, whatever deadline it holds",
      L.GALAXY_LEFT.value(ROWS[1], VIEW), None)
check("and it is last in the time column ascending",
      order("left")[-2:], ["retired", "beacon"])
check("and still last descending, rather than swapping ends",
      order("left", True)[-2:], ["retired", "beacon"])
check("the turn column does the same",
      order("turn")[-1] == order("turn", True)[-1] == "beacon", True)
check("a closed galaxy still prints its turn", printed("turn")[1], "110")
check("and its player count",
      L.galaxy_column("players").text(ROWS[1], VIEW), "3")
check("and prints no countdown at all", printed("left")[1], "")

print("\n4. sorting is stable, and an unknown column does nothing")
check("rows a column cannot tell apart keep the listing's own order",
      order("status")[:3], ["lapsed", "crowded", "sandbox"])
check("a column nobody has is not a reordering",
      order("nope"), [g.id for g in ROWS])
check("and neither is a blank one", order(""), [g.id for g in ROWS])
check("sorting does not disturb the list it was given",
      [g.id for g in ROWS],
      ["lapsed", "retired", "crowded", "beacon", "sandbox"])
check("the default column is one that exists",
      L.galaxy_column(L.DEFAULT_SORT) is not None, True)

print("\n5. what each row offers to do")
check("a galaxy you are in is offered Play",
      L.row_action(ROWS[4], JOINING), L.PLAY)
check("one you could join is offered View",
      L.row_action(ROWS[0], JOINING), L.VIEW)
check("one you have already asked for is offered neither",
      L.row_action(ROWS[2], JOINING), None)
check("a forming galaxy is offered neither",
      L.row_action(ROWS[3], JOINING), None)
check("a closed galaxy is offered neither",
      L.row_action(ROWS[1], JOINING), None)
# A closed galaxy you are in offers Review, which reads why it ended, and
# never Play: see test_galaxy_endings.py.
check("nor Play on a closed galaxy you are in",
      L.row_action(gx("x", "X", status="closed", joined=True)), L.ENDED)
# Review is the third label. A refused join, an ended galaxy and a seat taken
# back all offer it: see test_join_answers.py and test_galaxy_endings.py.
check("View, Play and Review are the only labels",
      sorted(set(L.ACTION_TEXT.values())), ["Play", "Review", "View"])

own = ROWS[3].store
check("the galaxy multiplayer.json names is played whatever its state says",
      L.row_action(ROWS[3], JOINING, own=own), L.PLAY)
check("which it is not without that, being a galaxy with no first turn",
      L.row_action(ROWS[3], JOINING), None)
check("and not even it is played once it is closed, only explained",
      L.row_action(ROWS[1], JOINING, own=ROWS[1].store), L.ENDED)

print("\n5a. a player who is in several galaxies is in several galaxies")
# Being in a galaxy is membership; playing one is the client's limit. So every
# galaxy this player is in offers Play at the same time, and the rows are
# asked for one at a time by name: a count would pass a page that offered Play
# on the wrong two.
IN_BOTH = {"sandbox": {"galaxy": "sandbox", "store": "C:\\g\\sandbox",
                       "name": "Alice"},
           "lapsed": {"galaxy": "lapsed", "store": "C:\\g\\lapsed",
                      "name": "Alice"}}
seated = [gx("sandbox", "sandbox", joined=True),
          gx("lapsed", "Lapsed", joined=True),
          gx("crowded", "crowded")]
check("both galaxies this player is in offer Play",
      {g.id: L.row_action(g, IN_BOTH) for g in seated},
      {"sandbox": L.PLAY, "lapsed": L.PLAY, "crowded": L.VIEW})
check("and both say so in the column that answers for the player",
      [L.galaxy_column("you").text(g, L.View(now=NOW, recs=IN_BOTH))
       for g in seated], ["you are in", "you are in", ""])
check("a seat held in one galaxy and a join waiting in another are both kept",
      {g.id: L.row_action(g, {**IN_BOTH, "crowded": JOINING["crowded"]})
       for g in seated},
      {"sandbox": L.PLAY, "lapsed": L.PLAY, "crowded": None})
check("and the waiting one still says when it lands",
      L.galaxy_column("you").text(
          seated[2], L.View(now=NOW,
                            recs={**IN_BOTH, "crowded": JOINING["crowded"]})),
      "joining next turn")
check("the page draws every one of them rather than the newest",
      len(L.sort_galaxies(seated, "you", False,
                          L.View(now=NOW, recs=IN_BOTH))), 3)

print("\n6. the row for a galaxy no directory lists")
spec = "C:\\galaxies\\demo"
empty = L.named_galaxy(spec, None)
check("a store with no state yet is forming", empty.status, L.FORMING)
check("named by the last part of the path it was given", empty.name, "demo")
check("identified by the spec, because that is all there is", empty.id, spec)
check("and it is its own store", empty.store, spec)
check("with no turn to show", empty.turn, None)
check("and nobody in it", (empty.players, empty.joined), (0, False))

state = {"turn": 12, "deadline": NOW + 300, "civs": ["Alice", "Bob"]}
seated = L.named_galaxy(spec, state, "Alice")
check("a store with a state carries its turn", seated.turn, 12)
check("and its deadline", seated.deadline, NOW + 300)
check("and counts its players without naming them", seated.players, 2)
check("and knows this player is one of them", seated.joined, True)
check("and that a stranger is not",
      L.named_galaxy(spec, state, "Dave").joined, False)
check("a state that says it is closed says so here",
      L.named_galaxy(spec, dict(state, status=L.CLOSED)).status, L.CLOSED)
check("a URL is named by its last part too",
      L.named_galaxy("http://10.0.0.5:7000/sandbox", None).name, "sandbox")
check("the row sorts like any other",
      L.galaxy_column("turn").value(seated, VIEW), 12)

print("\n7. which rows the page has at all")
root = fresh_dir()
make_galaxy(os.path.join(root, "alpha"), ["Alice", "Bob"], turn=7)
make_galaxy(os.path.join(root, "quiet"), [], turn=1)

listed = fresh_dir()
rows, problem = L.galaxy_rows(listed, root, "Alice")
check("a directory alone lists what is in it",
      sorted(g.id for g in rows), ["alpha", "quiet"])
check("with nothing to report", problem, None)
check("and the seated player is seated", rows[0].joined, True)

# Two seats, on this disk, through the file the launcher actually keeps.
two = fresh_dir()
pair = fresh_dir()
make_galaxy(os.path.join(pair, "alpha"), ["Alice", "Bob"], turn=7)
make_galaxy(os.path.join(pair, "bravo"), ["Alice", "Carol"], turn=3)
for gid in ("alpha", "bravo"):
    L.save_joined(two, {"galaxy": gid, "store": os.path.join(pair, gid),
                        "name": "Alice", "uid": None,
                        "directory": pair, "requested_at": 1.0,
                        "requested_turn": 1})
rows, problem = L.galaxy_rows(two, pair, "Alice")
recs = L.load_joined(two)
check("a player seated in two galaxies is seated in both",
      {g.id: g.joined for g in rows}, {"alpha": True, "bravo": True})
check("and is offered Play on each of them",
      {g.id: L.row_action(g, recs) for g in rows},
      {"alpha": L.PLAY, "bravo": L.PLAY})
check("with both records still on disk, named",
      [recs["alpha"]["galaxy"], recs["bravo"]["galaxy"]], ["alpha", "bravo"])
check("and nothing to report", problem, None)

mine = fresh_dir()
folder = make_galaxy(os.path.join(fresh_dir(), "mine"), ["Alice"], turn=3)
write_json(os.path.join(mine, L.MP_CONFIG), {"store": folder})
rows, problem = L.galaxy_rows(mine, None, "Alice")
check("a store and no directory is still one row", len(rows), 1)
check("which is the store", rows[0].store, folder)
check("read from the galaxy itself", rows[0].turn, 3)
check("and offered Play", L.row_action(rows[0], None, own=folder), L.PLAY)
check("with nothing to report", problem, None)
check("and no identity was minted for a folder",
      os.path.exists(os.path.join(mine, "fb_identity.json")), False)

both = fresh_dir()
write_json(os.path.join(both, L.MP_CONFIG),
           {"store": folder, "directory": root})
rows, problem = L.galaxy_rows(both, L.directory_to_list({}, {
    "store": folder, "directory": root}), "Alice")
check("a store and a directory is both, the store first",
      [g.id for g in rows], [folder, "alpha", "quiet"])
check("and still nothing to report", problem, None)

quiet = os.path.join(root, "quiet")
inside = fresh_dir()
write_json(os.path.join(inside, L.MP_CONFIG),
           {"store": quiet, "directory": root})
rows, problem = L.galaxy_rows(inside, root, "Alice")
check("a galaxy the directory already lists is not listed twice",
      [g.id for g in rows], ["alpha", "quiet"])
check("and it is the directory's row that is played",
      L.row_action(rows[1], None, own=quiet), L.PLAY)
check("which without that would only be offered a View",
      L.row_action(rows[1], None), L.VIEW)
check("and the same folder spelled another way is still the same galaxy",
      L.row_action(rows[1], None, own=os.path.join(root, ".", "quiet")),
      L.PLAY)
check("two different specs are not", L.same_store(quiet, root), False)
check("a URL matches itself",
      L.same_store("http://10.0.0.5:7000", "http://10.0.0.5:7000"), True)
check("and not another one",
      L.same_store("http://10.0.0.5:7000", "http://10.0.0.6:7000"), False)
check("and nothing matches nothing", L.same_store(None, None), False)

gone = fresh_dir()
write_json(os.path.join(gone, L.MP_CONFIG), {"store": folder})
rows, problem = L.galaxy_rows(gone, os.path.join(fresh_dir(), "nothing here"),
                              "Alice")
check("a directory that lists nothing does not lose the store",
      [g.id for g in rows], [folder])
check("a store that cannot be read is still a row",
      L.galaxy_rows(fresh_dir() and gone, None, "Alice")[0][0].store, folder)

print("\n8. which directory the page lists, and when it lists none")
check("a multiplayer.json naming a directory is listed",
      L.directory_to_list({}, {"directory": "D:\\galaxies"}), "D:\\galaxies")
check("and is still listed beside a store it also names",
      L.directory_to_list({}, {"store": "C:\\g\\d",
                               "directory": "D:\\galaxies"}), "D:\\galaxies")
check("a store on its own reaches no directory, so no network call",
      L.directory_to_list({}, {"store": "C:\\g\\d"}), None)
check("and neither does a LAN store",
      L.directory_to_list({}, {"store": "http://10.0.0.5:7000"}), None)
check("no multiplayer.json at all falls back to the manifest",
      L.directory_to_list({"multiplayer": {"directory": "M"}}, None), "M")
check("and then to the beta's own", L.directory_to_list({}, None),
      L.BETA_DIRECTORY)
check("a blank directory does not count",
      L.directory_to_list({}, {"directory": "   "}), L.BETA_DIRECTORY)


class NoModule:
    """An import hook standing in for a build frozen without a module."""

    def __init__(self, name):
        self.name = name

    def find_module(self, name, path=None):
        return None

    def find_spec(self, name, path=None, target=None):
        if name == self.name:
            raise ImportError(f"no {self.name} in this build")
        return None


print("\n9. a build that was frozen without the multiplayer half")
hook = NoModule("galaxy_directory")
saved = sys.modules.pop("galaxy_directory")
sys.meta_path.insert(0, hook)
try:
    rows, problem = L.galaxy_rows(mine, root, "Alice")
    check("the directory is reported as missing rather than raising",
          "cannot list galaxies" in (problem or ""), True)
    check("and the store multiplayer.json names is still a row",
          [g.id for g in rows], [folder])
    check("and still playable", L.row_action(rows[0], None, own=folder),
          L.PLAY)
finally:
    sys.meta_path.remove(hook)
    sys.modules["galaxy_directory"] = saved
check("and the directory lists again once it is there",
      sorted(g.id for g in L.galaxy_rows(mine, root, "Alice")[0]),
      [folder, "alpha", "quiet"])

hook = NoModule("player_turn")
saved = sys.modules.pop("player_turn", None)
sys.meta_path.insert(0, hook)
try:
    check("a build with no turn machinery cannot read the store",
          L.multiplayer_modules(), None)
    row = L.own_row(mine, {"store": folder}, "Alice")
    check("so its row says the galaxy is forming rather than failing",
          row.status, L.FORMING)
    check("and it is still the store's row", row.store, folder)
finally:
    sys.meta_path.remove(hook)
    if saved is not None:
        sys.modules["player_turn"] = saved
check("and the state is read again once the machinery is there",
      L.own_row(mine, {"store": folder}, "Alice").turn, 3)

print("\n10. an unreadable store is a row, not a failure")
broken = os.path.join(fresh_dir(), "broken")
os.makedirs(os.path.join(broken, "turns"), exist_ok=True)
with open(os.path.join(broken, "state.json"), "w", encoding="utf-8") as fh:
    fh.write("{ this is not json")
row = L.own_row(fresh_dir(), {"store": broken}, "Alice")
check("a state that will not parse is a forming row", row.status, L.FORMING)
check("and the row still names the store", row.store, broken)
check("a store that is not there at all is the same",
      L.own_row(fresh_dir(), {"store": os.path.join(fresh_dir(), "nope")},
                "Alice").status, L.FORMING)

print("\n11. the page's layout in the window")
css = open(os.path.join(WEBUI, "app.css"), encoding="utf-8").read()
js = open(os.path.join(WEBUI, "app.js"), encoding="utf-8").read()
tracks = re.search(r"\.grid \{[^}]*grid-template-columns:([^;]+);", css).group(1)
check("the table has a track per column and one for the row's button",
      len(re.findall(r"minmax\([^)]*\)|\d+px", tracks)),
      len(L.GALAXY_COLUMNS) + 1)
check("its headings are the columns the launcher sends",
      "for (const c of columns)" in js and "c.heading" in js, True)
check("every page is inset from the frame on all four sides",
      re.search(r"main \{[^}]*padding: \d+px \d+px;", css) is not None, True)

print("\n12. how the page is wired to the rest of the launcher")
play = inspect.getsource(L.Launcher.on_play)
check("Multiplayer opens the page", "self.show_galaxies()" in play, True)
check("and plays nothing itself", "start_multiplayer" in play, False)
row_src = inspect.getsource(L.Launcher.on_row)
check("View opens the notice, which is where a join is confirmed",
      "self.on_join(g)" in row_src, True)
check("and Play runs the turn loop", "self.play_galaxy(g)" in row_src, True)
started = inspect.getsource(L.Launcher.play_galaxy)
check("Play hands the row to the turn loop",
      "self.start_multiplayer(mode, g)" in started, True)
check("and goes back to the page the turn readout is on",
      started.index("self.show_home()") < started.index("start_multiplayer"),
      True)
check("the turn loop takes a galaxy, or none, as it always did",
      list(inspect.signature(L.Launcher.start_multiplayer).parameters),
      ["self", "mode", "g"])
check("and asks what to play through one place",
      "self.galaxy_config(g)" in
      inspect.getsource(L.Launcher.start_multiplayer), True)
start = inspect.getsource(L.Launcher.start_multiplayer)
check("the turn loop asks one place whether it is free to start",
      "self._take_turn_loop(g)" in start, True)
check("and carries no second rule about a galaxy already running",
      "already running" in start.split("running_clients")[0], False)
joining = inspect.getsource(L.Launcher.on_join)
check("a join no longer offers to replace the galaxy already joined",
      "Join anyway?" in joining, False)
check("nor says the launcher plays one galaxy at a time",
      "one galaxy at a time" in joining, False)
check("it writes the record beside the others",
      "save_joined(self.data_dir, joined_record(" in joining, True)
check("a reclaimed seat forgets that galaxy rather than all of them",
      ("clear_joined(self.data_dir," in start,
       "clear_joined(self.data_dir)" in start), (True, False))

draw = inspect.getsource(L.Launcher._draw_galaxies)
# The Launcher decides what the table holds and the view draws it, so the
# repaint is checked in both: the rows in `_draw_galaxies`, the page's half in
# app.js's drawRows.
paint = js[js.index("function drawRows"):js.index("// ── prompts")]
check("the table's buttons are the ones row_action names",
      "row_action(" in draw and "ACTION_TEXT[action]" in draw, True)
check("and the cells are the ones the columns name",
      "col.text(g, view)" in draw, True)
check("and the window is handed the rows rather than drawing its own",
      "self.view.draw_galaxies(drawn)" in draw, True)
check("a repaint replaces the rows it drew and nothing else",
      ('$("grows")' in paint, paint.count("replaceChildren")), (True, 1))
check("and never rebuilds the headings or the toolbar above them",
      any(w in paint for w in ("colhead", "games-status", "refresh")), False)
check("neither a failed listing nor a loading one hides the page",
      [fn.__qualname__ for fn in (L.Launcher._show_source,
                                  L.Launcher._on_games,
                                  L.Launcher.refresh_games)
       if "show_page" in inspect.getsource(fn)], [])

refresh = inspect.getsource(L.Launcher.refresh_games)
check("a listing runs off the UI thread",
      "threading.Thread(" in refresh, True)
check("and comes back through the queue the launcher already drains",
      "self.msgs.put((\"__games__\"" in refresh, True)
check("nothing is listed while the page is out of sight",
      'self.page == "galaxies"' in inspect.getsource(L.Launcher._games_tick),
      True)
check("and the table is drawn through the sort rather than as listed",
      "sort_galaxies(" in draw, True)

print("\n13. the two behaviours a window is not needed to exercise")


class Sorting:
    """A launcher's sort state, without the window it usually hangs off."""

    def __init__(self):
        self.sort_key, self.sort_desc, self.drawn = L.DEFAULT_SORT, False, 0

    def _draw_galaxies(self):
        self.drawn += 1


s = Sorting()
L.Launcher.sort_by(s, "turn")
check("clicking a heading sorts on that column",
      (s.sort_key, s.sort_desc), ("turn", False))
L.Launcher.sort_by(s, "turn")
check("clicking the same one again turns it round", s.sort_desc, True)
L.Launcher.sort_by(s, "turn")
check("and a third click puts it back", s.sort_desc, False)
L.Launcher.sort_by(s, "left")
check("a different column starts the right way up",
      (s.sort_key, s.sort_desc), ("left", False))
check("and every click repaints the table", s.drawn, 4)


class PageView(FakeView):
    """A view that counts how often it is asked to swap pages."""

    def __init__(self, **answers):
        super().__init__(**answers)
        self.swaps = 0

    def show_page(self, name):
        self.swaps += 1
        super().show_page(name)


class Nav:
    """A launcher's two pages, and which of them is showing."""

    def __init__(self):
        self.page = "home"
        self.view = PageView()

    def show_page(self, name):
        return L.Launcher.show_page(self, name)

    def where(self):
        return self.page, self.view.page


n = Nav()
L.Launcher.show_page(n, "galaxies")
check("opening the page puts the home page away",
      n.where(), ("galaxies", "galaxies"))
L.Launcher.show_home(n)
check("and Back brings it back", n.where(), ("home", "home"))
before = n.view.swaps
L.Launcher.show_home(n)
check("asking for the page you are already on changes nothing",
      (n.where(), n.view.swaps), (("home", "home"), before))
swap = js[js.index("function showPage"):js.index("function drawModes")]
check("and the window shows one page and puts the others away",
      'p.classList.toggle("on", p.id === "page-" + name)' in swap, True)

print("\n14. one galaxy is played at a time, and that is about playing")
# The limit the page does not carry. Every joined row offers Play; the turn
# loop is what there is one of, because it starts the game client and the
# machine runs one of those.
#
# The dialog is stood in for rather than shown: each launcher below is given a
# view that answers the question from `answers` and records what it was asked
# in `asked`, so the offer to switch is exercised with no window and no
# display.
asked, answers = [], []


class Asking(FakeView):
    """A view whose yes-or-no questions all land in one shared list."""

    def ask_yes_no(self, msg):
        asked.append(msg)
        return super().ask_yes_no(msg)


class Loop:
    """A turn loop, as far as this rule can see it: alive, or stopped."""

    def __init__(self, finishes=True):
        self.finishes = finishes
        self.asked_to_stop = False

    def is_alive(self):
        return not (self.asked_to_stop and self.finishes)


class Player:
    """A launcher following one galaxy, without the window or the thread."""

    def __init__(self, playing=None, finishes=True, client_gone=False,
                 turn=None):
        self.cfg = {"product": "Cosmic Supremacy"}
        self.view = Asking(yes_no=answers)
        self.mp_playing = playing
        self.mp_thread = Loop(finishes) if playing is not None else None
        # The loop outlives the game window, so Play means two things here:
        # a live client is a second game and is refused, a closed one is a
        # request to open the galaxy again. These are what that request sets.
        self.mp_client_gone = client_gone
        self.mp_turn = turn
        self.mp_reopen = threading.Event()
        self.mp_reopening = False
        self.warned, self.said, self.stopped = [], [], []

    def warn(self, msg):
        self.warned.append(msg)

    def say(self, msg):
        self.said.append(msg)

    def stop_multiplayer(self, why=""):
        self.stopped.append(why)
        self.mp_thread.asked_to_stop = True
        self.mp_thread = None
        self.mp_playing = None

    def playing_now(self):
        return L.Launcher.playing_now(self)

    def take(self, g):
        return L.Launcher._take_turn_loop(self, g)


CROWDED = {"id": "crowded", "name": "crowded", "store": "C:\\g\\crowded"}
sandbox, crowded = ROWS[4], ROWS[2]

free = Player()
check("a launcher following nothing starts the galaxy asked for",
      free.take(sandbox), True)
check("without a word about it", (free.warned, asked), ([], []))
check("and one following nothing has no galaxy to name",
      L.Launcher.playing_now(free), None)

again = Player(CROWDED)
check("pressing Play on the galaxy already being played does not start it",
      again.take(crowded), False)
check("it says you are already playing that one",
      "already playing crowded" in again.warned[0], True)
check("and asks nothing, because there is nothing to switch to",
      asked, [])
check("the loop is left alone", again.stopped, [])

refused = Player(CROWDED)
answers.append(False)
check("pressing Play on another galaxy while one is playing asks first",
      refused.take(sandbox), False)
check("and the question names the galaxy being played",
      "crowded" in asked[-1], True)
check("and the one it would switch to", "sandbox" in asked[-1], True)
check("and says why there is only one", "one game" in asked[-1], True)
check("answering no stops nothing", refused.stopped, [])
check("and leaves that galaxy being followed",
      (L.Launcher.playing_now(refused) or {}).get("name"), "crowded")
check("and says so in the log rather than in a window",
      (refused.warned, "not switching" in refused.said[-1]), ([], True))

switched = Player(CROWDED)
answers.append(True)
check("answering yes starts the galaxy asked for", switched.take(sandbox),
      True)
check("having stopped the one that was playing", len(switched.stopped), 1)
check("with a reason naming what it switched to",
      "sandbox" in switched.stopped[0], True)
check("and nothing was warned about", switched.warned, [])

busy = Player(CROWDED, finishes=False)
answers.append(True)
check("a loop that has not finished its turn does not start a second one",
      busy.take(sandbox), False)
check("it says which galaxy is still finishing",
      "crowded is still finishing" in busy.warned[0], True)
check("and what to press once it has", "Play on sandbox" in busy.warned[0],
      True)

unnamed = Player(CROWDED)
answers.append(False)
check("a Play that named no row is asked about too", unnamed.take(None),
      False)
check("and the question still names the galaxy being played",
      "crowded" in asked[-1], True)

nameless = Player({})
answers.append(False)
check("a loop with no galaxy recorded against it still holds the turn loop",
      nameless.take(sandbox), False)
check("and is asked about rather than talked over", len(asked), 5)
check("every question asked was answered", answers, [])

for d in dirs:
    shutil.rmtree(d, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))

print()
print("Play on a galaxy whose game window has been closed")
here = {"name": sandbox.name, "store": sandbox.store}
open_p = Player(playing=here)
refused_again = open_p.take(sandbox)
gone_p = Player(playing=here, client_gone=True, turn=11)
reopened = gone_p.take(sandbox)
# A live client is refused, because two games on one machine is the whole of
# what this rule is for. A closed one is not a refusal: the player has sent
# their turn and wants another look at the galaxy, and the loop still
# following it is the only thing allowed to start a client, so it is asked.
# Neither answers True: what must not happen either way is a second loop.
check("a live client reads as playing",
      "already playing" in open_p.warned[0], True)
check("and starts no second loop", refused_again, False)
check("and asks the loop for nothing", open_p.mp_reopen.is_set(), False)
check("a closed one starts no second loop either", reopened, False)
check("and is not turned away in a window at all", gone_p.warned, [])
check("it asks the loop following that galaxy to open the game again",
      gone_p.mp_reopen.is_set(), True)
check("and says so where the turn readout can read it",
      gone_p.mp_reopening, True)
check("the log names the galaxy and the turn being opened",
      ("sandbox" in gone_p.said[-1], "turn 11" in gone_p.said[-1]),
      (True, True))
check("the loop it asked is left running", gone_p.stopped, [])
check("a galaxy with no turn recorded is still opened",
      Player(playing=here, client_gone=True).take(sandbox), False)

# The rule this must never break: one game process per machine, held by the
# advisory lock in client/dev_tools/game_cycle.py. Fails if Play reaches for a
# client instead of signalling the loop that owns one.
take_src = inspect.getsource(L.Launcher._take_turn_loop)
check("Play on a closed game starts nothing here",
      ("start_multiplayer" in take_src, "serve(" in take_src,
       "restart(" in take_src), (False, False, False))
check("the event it sets is the one the turn loop is given",
      "reopen=self.mp_reopen" in
      inspect.getsource(L.Launcher.start_multiplayer), True)
check("and it is cleared when a loop starts and when one stops",
      ("self.mp_reopen.clear()" in
       inspect.getsource(L.Launcher.start_multiplayer),
       "self.mp_reopen.clear()" in
       inspect.getsource(L.Launcher.stop_multiplayer)), (True, True))
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))

print()
print("switching away from a galaxy whose game window is closed")
here_rec = {"name": sandbox.name, "store": sandbox.store}
asked.clear()
open_sw = Player(playing=here_rec)
answers.append(True)
check("with a game open it asks first", open_sw.take(crowded), True)
check("and the question names both galaxies",
      len(asked) == 1 and sandbox.name in asked[0], True)

asked.clear()
gone_sw = Player(playing=here_rec, client_gone=True, turn=11)
# The dialog exists so a player mid-turn is not dropped out of it. With the
# window already closed there is nothing to interrupt, so asking made a
# switch feel like it needed permission it did not need. Fails if the
# prompt still appears, which is what the operator saw.
check("with no game open it does not ask", gone_sw.take(crowded), True)
check("and nothing was put to the player", asked, [])
check("and the first galaxy was let go of", len(gone_sw.stopped), 1)
check("and it said so in the log",
      any("no game open" in m for m in gone_sw.said), True)
sys.exit(1 if fails else 0)
