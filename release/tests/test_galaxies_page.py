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
import shutil
import sys
import tempfile
import time

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import galaxy_directory                                         # noqa: E402
import launcher as L                                            # noqa: E402
import turn_store                                               # noqa: E402

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
JOINING = {"galaxy": "crowded", "store": "C:\\g\\crowded", "name": "Alice"}
VIEW = L.View(now=NOW, rec=JOINING)


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
check("every column has a key, a heading and a width",
      [(c.key, bool(c.heading), c.width > 0) for c in L.GALAXY_COLUMNS],
      lambda got: all(h and w for _k, h, w in got))
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
check("nor a closed galaxy you are in",
      L.row_action(gx("x", "X", status="closed", joined=True)), None)
check("View and Play are the only two labels",
      sorted(L.ACTION_TEXT.values()), ["Play", "View"])

own = ROWS[3].store
check("the galaxy multiplayer.json names is played whatever its state says",
      L.row_action(ROWS[3], JOINING, own=own), L.PLAY)
check("which it is not without that, being a galaxy with no first turn",
      L.row_action(ROWS[3], JOINING), None)
check("and not even it is played once it is closed",
      L.row_action(ROWS[1], JOINING, own=ROWS[1].store), None)

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

print("\n11. the window's padding, and the page's own bottom edge")
check("the window has one horizontal inset", L.PAD_X > 0, True)
check("and one vertical one", L.PAD_Y > 0, True)
check("a page is inset by both", (L.PAGE_PACK["padx"], L.PAGE_PACK["pady"]),
      (L.PAD_X, (L.PAD_Y, L.PAD_Y)))
check("and its bottom edge is padded, which is the defect this replaces",
      L.PAGE_PACK["pady"][1] > 0, True)
check("by the same amount as its top", L.PAGE_PACK["pady"][0],
      L.PAGE_PACK["pady"][1])
check("a panel is inset from its own border", L.PANEL_PAD > 0, True)
build = inspect.getsource(L.Launcher._build)
check("both pages sit in the window on those terms",
      "self.pages.pack(**PAGE_PACK)" in build, True)
page = inspect.getsource(L.Launcher._build_galaxies)
check("and the table is inset from the panel edge equally all round",
      "padx=PANEL_PAD," in page and "pady=PANEL_PAD)" in page, True)
check("no page invents a padding of its own",
      "padx=26" in build + page, False)

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

draw = inspect.getsource(L.Launcher._draw_galaxies)
check("the table's buttons are the ones row_action names",
      "row_action(" in draw and "ACTION_TEXT[action]" in draw, True)
check("and the cells are the ones the columns name",
      "col.text(g, view)" in draw, True)
check("a repaint destroys the cells it made and nothing else",
      ("for widget in self._cells" in draw, draw.count("destroy()")),
      (True, 1))
check("and never rebuilds the header Back sits in",
      any(w in draw for w in ("games_refresh", "show_home",
                              "_build_galaxies")), False)
check("Back is built before the table it sits above",
      page.index("command=self.show_home") < page.index("games_table"), True)
check("and neither a failed listing nor a loading one takes it off screen",
      [fn.__name__ for fn in (L.Launcher._show_source, L.Launcher._on_games,
                              L.Launcher.refresh_games)
       if "pack_forget" in inspect.getsource(fn)], [])

refresh = inspect.getsource(L.Launcher.refresh_games)
check("a listing runs off the Tk thread",
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


class Frame:
    """Enough of a tk frame to say whether it is on screen."""

    def __init__(self, packed=False):
        self.packed = packed
        self.packs = 0

    def pack(self, **kw):
        self.packed = True
        self.packs += 1

    def pack_forget(self):
        self.packed = False


class Nav:
    """A launcher's two pages, and which of them is showing."""

    def __init__(self):
        self.page = "home"
        self.home_page = Frame(packed=True)
        self.galaxies_page = Frame()

    def show_page(self, name):
        return L.Launcher.show_page(self, name)

    def where(self):
        return self.page, self.home_page.packed, self.galaxies_page.packed


n = Nav()
L.Launcher.show_page(n, "galaxies")
check("opening the page puts the home page away",
      n.where(), ("galaxies", False, True))
L.Launcher.show_home(n)
check("and Back brings it back", n.where(), ("home", True, False))
before = n.home_page.packs
L.Launcher.show_home(n)
check("asking for the page you are already on changes nothing",
      (n.where(), n.home_page.packs), (("home", True, False), before))

for d in dirs:
    shutil.rmtree(d, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
