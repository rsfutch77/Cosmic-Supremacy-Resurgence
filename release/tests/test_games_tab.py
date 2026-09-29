"""
The Games tab: what it lists, what a Join writes down, and what a seat refusal
is allowed to say.

Headless and offline. Everything here is the launcher's module-level code
against a galaxy directory and a turn store made in a temporary folder, so it
runs in about a second. No game, no window, no network: the one galaxy any of
this opens is a folder on this disk, which is also the case that must never
mint an identity or reach Firebase.

The half that is not here is the worker's. A join request is written and read
back as a file; nothing in this checkout merges it into a galaxy yet (J3), so
no check below claims a seat was granted.
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
    d = tempfile.mkdtemp(prefix="games_")
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


def row(gid="sandbox", name="Sandbox", status="open", turn=4, deadline=None,
        players=2, joined=False, store="C:\\g\\sandbox"):
    return galaxy_directory.Galaxy(gid, name, status, turn, deadline, players,
                                   joined, store)


print("1. multiplayer.json is read once, and still means what it meant")
d = fresh_dir()
check("no file at all is no configuration", L.multiplayer_file(d), None)
check("and no galaxy either", L.multiplayer_config(d), None)

store_only = fresh_dir()
write_json(os.path.join(store_only, L.MP_CONFIG), {"store": "C:\\galaxies\\d"})
check("a file naming a store is still a galaxy",
      (L.multiplayer_config(store_only) or {}).get("store"), "C:\\galaxies\\d")

dir_only = fresh_dir()
write_json(os.path.join(dir_only, L.MP_CONFIG), {"directory": "D:\\galaxies"})
check("a file naming only a directory is read",
      (L.multiplayer_file(dir_only) or {}).get("directory"), "D:\\galaxies")
check("and is not mistaken for a galaxy", L.multiplayer_config(dir_only), None)

print("\n2. which directory the tab lists")
check("multiplayer.json wins",
      L.directory_spec({"multiplayer": {"directory": "M"}},
                       {"directory": "P"}), "P")
check("then the manifest",
      L.directory_spec({"multiplayer": {"directory": "M"}}, None), "M")
check("then the beta's own", L.directory_spec({}, None), L.BETA_DIRECTORY)
check("a blank entry does not count",
      L.directory_spec({}, {"directory": "   "}), L.BETA_DIRECTORY)

print("\n3. what the launcher writes down when a player joins")
d = fresh_dir()
check("nothing joined yet", L.load_joined(d), {})
g = row(store=os.path.join(fresh_dir(), "sandbox"))
rec = L.joined_record(g, "Alice", "uid-1", "D:\\galaxies", now=1000.0)
L.save_joined(d, rec)
check("the record lands beside identity.json",
      os.path.basename(L.joined_path(d)), L.JOINED_FILE)
check("and is keyed by the galaxy it is for", list(L.load_joined(d)),
      ["sandbox"])
held = L.load_joined(d)["sandbox"]
check("it names the galaxy", held.get("galaxy"), "sandbox")
check("and the store the directory gave, not one a player typed",
      held.get("store"), g.store)
check("and the name the seat was asked for under", held.get("name"), "Alice")
check("and the identity that asked", held.get("uid"), "uid-1")
check("and which turn it was asked at", held.get("requested_turn"), 4)
broken = fresh_dir()
write_json(L.joined_path(broken), {"galaxy": "sandbox"})
check("a record with no store is no record", L.load_joined(broken), {})
L.clear_joined(d)
check("and it can be cleared", L.load_joined(d), {})
L.clear_joined(d)
check("clearing twice is not an error", os.path.exists(L.joined_path(d)), False)

print("\n4. a second galaxy is joined, not swapped for the first")
# The check that matters here is that the first galaxy is still named after
# the second is joined. Counting the records would pass an implementation that
# overwrote one and left a stale one behind, so every record is asked for by
# name and the order they are in is asserted as well.
two = fresh_dir()
first = L.joined_record(row(gid="sandbox", store="C:\\g\\sandbox"), "Alice",
                        None, "D:\\galaxies", now=1000.0)
second = L.joined_record(row(gid="crowded", store="C:\\g\\crowded"), "Alice",
                         None, "D:\\galaxies", now=2000.0)
L.save_joined(two, first)
L.save_joined(two, second)
both_held = L.load_joined(two)
check("the galaxy joined first is still held by name",
      (both_held.get("sandbox") or {}).get("galaxy"), "sandbox")
check("with the store it was joined at",
      (both_held.get("sandbox") or {}).get("store"), "C:\\g\\sandbox")
check("and the second is held beside it",
      (both_held.get("crowded") or {}).get("store"), "C:\\g\\crowded")
check("neither record was written over the other",
      len({r["store"] for r in both_held.values()}), 2)
check("and they are in the order they were joined",
      list(both_held), ["sandbox", "crowded"])
check("which is the order a re-read gives too",
      list(L.load_joined(two)), ["sandbox", "crowded"])
L.save_joined(two, dict(first, name="Alicia"))
check("joining one of them again keeps its place",
      list(L.load_joined(two)), ["sandbox", "crowded"])
check("and replaces that record rather than adding one",
      L.load_joined(two)["sandbox"]["name"], "Alicia")
L.clear_joined(two, "sandbox")
check("forgetting one galaxy leaves the rest alone",
      list(L.load_joined(two)), ["crowded"])
check("and the record left is the whole one",
      L.load_joined(two)["crowded"]["store"], "C:\\g\\crowded")
L.clear_joined(two, "nowhere")
check("forgetting a galaxy that was never joined changes nothing",
      list(L.load_joined(two)), ["crowded"])
L.clear_joined(two, "crowded")
check("and the file goes with the last record",
      os.path.exists(L.joined_path(two)), False)

print("\n5. a joined.json written before a player could be in two")
# Read off disk in the shape the launcher used to write, rather than from a
# dict built here to look like one: the thing being checked is that a file an
# install already has still holds the seat it recorded.
old = fresh_dir()
with open(L.joined_path(old), "w", encoding="utf-8") as fh:
    fh.write('{\n  "directory": "firebase://cs-resurgence",\n'
             '  "galaxy": "sandbox",\n  "name": "Alice",\n'
             '  "store": "C:\\\\g\\\\sandbox",\n  "uid": "uid-1",\n'
             '  "requested_at": 1000.0,\n  "requested_turn": 4\n}\n')
carried = L.load_joined(old)
check("the seat it recorded is still held", list(carried), ["sandbox"])
check("under the name it was claimed with", carried["sandbox"]["name"],
      "Alice")
check("with its store", carried["sandbox"]["store"], "C:\\g\\sandbox")
check("its identity", carried["sandbox"]["uid"], "uid-1")
check("the turn it was asked at", carried["sandbox"]["requested_turn"], 4)
check("and the directory it was joined from",
      carried["sandbox"]["directory"], "firebase://cs-resurgence")
check("that galaxy is still what this launcher plays",
      L.galaxy_to_play(old).get("store"), "C:\\g\\sandbox")
check("and its row still reads as a join waiting to land",
      L.pending_join(row(), carried), True)
L.save_joined(old, second)
check("joining a second does not take the migrated seat away",
      sorted(L.load_joined(old)), ["crowded", "sandbox"])
check("and the one that was there keeps its name",
      L.load_joined(old)["sandbox"]["name"], "Alice")
with open(L.joined_path(old), encoding="utf-8") as fh:
    raw = json.load(fh)
check("the old shape is never written again", "store" in raw, False)
check("what is written is a collection keyed by galaxy id",
      sorted(raw.get(L.JOINED_KEY) or {}), ["crowded", "sandbox"])
check("and it says which shape it is", raw.get("version"), L.JOINED_VERSION)

nameless = fresh_dir()
write_json(L.joined_path(nameless), {"store": "C:\\g\\somewhere",
                                     "name": "Alice"})
check("an old record with no galaxy id is kept under its store",
      list(L.load_joined(nameless)), ["C:\\g\\somewhere"])
check("so the galaxy it named is still played",
      L.galaxy_to_play(nameless).get("store"), "C:\\g\\somewhere")

print("\n6. which galaxy this launcher plays")
check("neither a config nor a join is nothing to play",
      L.galaxy_to_play(fresh_dir()), None)

joined_only = fresh_dir()
L.save_joined(joined_only, rec)
played = L.galaxy_to_play(joined_only)
check("a joined galaxy is played", played.get("store"), g.store)
check("and carries the record it came from",
      (played.get("joined") or {}).get("name"), "Alice")

several = fresh_dir()
for gid, when in (("one", 1000.0), ("three", 3000.0), ("two", 2000.0)):
    L.save_joined(several, L.joined_record(
        row(gid=gid, store=f"C:\\g\\{gid}"), "Alice", None, "D:\\galaxies",
        now=when))
check("with several joined and no row named, the newest join is played",
      L.galaxy_to_play(several).get("store"), "C:\\g\\three")
check("which is neither the first nor the last the file lists, so it is a "
      "choice rather than whichever came to hand",
      [list(L.load_joined(several))[0], list(L.load_joined(several))[-1]],
      ["one", "two"])
check("and the same call gives the same galaxy every time",
      len({L.galaxy_to_play(several)["store"] for _ in range(5)}), 1)

tied = fresh_dir()
for gid in ("bravo", "alpha"):
    L.save_joined(tied, L.joined_record(row(gid=gid, store=f"C:\\g\\{gid}"),
                                        "Alice", None, "D:\\g", now=5000.0))
check("two joins written in the same moment are settled by galaxy id",
      L.galaxy_to_play(tied).get("store"), "C:\\g\\bravo")

undated = fresh_dir()
write_json(L.joined_path(undated),
           {"version": L.JOINED_VERSION,
            L.JOINED_KEY: {
                "recent": {"galaxy": "recent", "store": "C:\\g\\recent",
                           "requested_at": 10.0},
                "undated": {"galaxy": "undated",
                            "store": "C:\\g\\undated"}}})
check("a record carrying no time is the older one",
      L.galaxy_to_play(undated).get("store"), "C:\\g\\recent")

both = fresh_dir()
write_json(os.path.join(both, L.MP_CONFIG), {"store": "C:\\galaxies\\mine"})
L.save_joined(both, rec)
L.save_joined(both, second)
check("multiplayer.json still wins over every join",
      L.galaxy_to_play(both).get("store"), "C:\\galaxies\\mine")

check("a folder galaxy wants no identity",
      L.store_wants_token(L.galaxy_to_play(joined_only)), False)
https = fresh_dir()
L.save_joined(https, dict(rec, store="https://relay.example/sandbox"))
check("one behind the relay does",
      L.store_wants_token(L.galaxy_to_play(https)), True)
authed = fresh_dir()
L.save_joined(authed, dict(rec, store="https://relay.example/s", auth=False))
check("and an auth in the record still overrides both ways",
      L.store_wants_token(L.galaxy_to_play(authed)), False)

print("\n7. an install with no identity does not go and get one")
quiet = fresh_dir()
check("no uid without minting", L.install_uid(quiet), None)
check("and nothing was written for one",
      os.path.exists(os.path.join(quiet, "fb_identity.json")), False)

print("\n8. the cells a galaxy's row prints")
# Rewritten from the one-line summary the old Games panel drew. The panel is
# gone and the page draws a table, so each rule the sentence carried is
# asserted against the cell that carries it now.
now = 1_000_000.0


def cells(g, **kw):
    """What one row prints, column by column."""
    view = L.View(now=now, **kw)
    return {c.key: c.text(g, view) for c in L.GALAXY_COLUMNS}


live = cells(row(deadline=now + 3660, players=5))
check("the name cell", live["name"], "Sandbox")
check("the status cell", live["status"], "open")
check("the turn cell", live["turn"], "4")
check("the countdown cell", live["left"], "1h 01m left")
check("the player count cell", live["players"], "5")
forming = cells(row(status="forming", turn=None, deadline=None, players=0))
check("a forming galaxy prints no turn", forming["turn"], "")
check("and no clock", forming["left"], "")
check("and says what it is", forming["status"], "forming")
check("and counts nobody", forming["players"], "0")
check("an overdue turn says so", cells(row(deadline=now - 5))["left"],
      "time is up")
check("a galaxy this player is in says so",
      cells(row(joined=True))["you"], "you are in")
mine = {"sandbox": {"galaxy": "sandbox", "store": "x", "name": "Alice"}}
check("having played this turn is shown",
      cells(row(joined=True), recs=mine,
            submitted={"sandbox": True})["you"], "turn played")
check("and not having played is shown",
      cells(row(joined=True), recs=mine,
            submitted={"sandbox": False})["you"], "your turn")
check("a readout for another galaxy is not read into this row",
      cells(row(gid="other", joined=True), recs=mine,
            submitted={"sandbox": True})["you"], "you are in")
check("a galaxy whose readout could not be had says only that you are in it",
      cells(row(joined=True), recs=mine, submitted={})["you"], "you are in")
check("and two galaxies read their own answers",
      [cells(row(gid=gid, joined=True), recs=mine,
             submitted={"sandbox": True, "crowded": False})["you"]
       for gid in ("sandbox", "crowded")], ["turn played", "your turn"])
check("a galaxy nobody here has joined claims nothing",
      cells(row())["you"], "")
check("a join that has not landed yet says when it will",
      cells(row(), recs=mine)["you"], "joining next turn")

print("\n9. which control a row offers")
check("an open galaxy takes a join", L.joinable(row()), True)
check("a forming one does not", L.joinable(row(status="forming")), False)
check("a closed one does not", L.joinable(row(status="closed")), False)
check("one this player is in does not", L.joinable(row(joined=True)), False)
check("and one already asked for does not", L.joinable(row(), mine), False)
check("a request that has not landed is pending",
      L.pending_join(row(), mine), True)
check("and stops being pending once the seat exists",
      L.pending_join(row(joined=True), mine), False)
check("a record for another galaxy is not this row's",
      L.pending_join(row(gid="other"), mine), False)
check("a galaxy you could join is offered View", L.row_action(row()), L.VIEW)
check("a galaxy you are in is offered Play",
      L.row_action(row(joined=True)), L.PLAY)
check("a forming galaxy is offered neither",
      L.row_action(row(status="forming")), None)
check("a closed one is offered neither",
      L.row_action(row(status="closed")), None)
check("nor Play on a closed one you are in, which offers Reason",
      L.row_action(row(status="closed", joined=True)), L.ENDED)
check("nor one you have already asked for", L.row_action(row(), mine), None)

waiting = {"sandbox": {"galaxy": "sandbox", "store": "x", "name": "Alice"},
           "crowded": {"galaxy": "crowded", "store": "y", "name": "Alice"}}
check("two galaxies asked for at once are both waiting",
      [L.pending_join(row(gid=gid), waiting)
       for gid in ("sandbox", "crowded")], [True, True])
check("and neither offers a second Join",
      [L.row_action(row(gid=gid), waiting)
       for gid in ("sandbox", "crowded")], [None, None])
check("two galaxies this player is seated in are both offered Play",
      [L.row_action(row(gid=gid, joined=True), waiting)
       for gid in ("sandbox", "crowded")], [L.PLAY, L.PLAY])
check("and a third they are in neither of is still offered View",
      L.row_action(row(gid="beacon"), waiting), L.VIEW)

print("\n10. a seat belongs to the install that claimed it")
seats = ["Alice", "Bob", "Carol"]
check("a free name is no problem", L.seat_claim_problem("Dave", seats), None)
check("an empty galaxy is no problem", L.seat_claim_problem("Dave", []), None)
taken = L.seat_claim_problem("Bob", seats)
check("a name already in the roster is refused", bool(taken), True)
check("the refusal echoes the name that was typed", "'Bob'" in taken, True)
check("and says how many seats there are", "3 seats" in taken, True)
for other in ("Alice", "Carol"):
    check(f"and never names {other}", other in taken, False)
cased = L.seat_claim_problem("bob", seats)
check("a seat differing only in case is refused too", bool(cased), True)
check("the typed spelling comes back", "'bob'" in cased, True)
check("and the other spelling does not", "'Bob'" in cased, False)
check("one seat is not '1 seats'",
      "one seat" in L.seat_claim_problem("Alice", ["Alice"]), True)
check("this install's own seat is not a refusal",
      L.seat_claim_problem("Bob", seats, held="Bob"), None)
check("but somebody else's still is",
      bool(L.seat_claim_problem("Alice", seats, held="Bob")), True)

print("\n11. the request a Join writes")
req = L.join_request("Alice", "uid-1", "0.1.2", turn=4, now=1000.0)
check("it carries the name", req["name"], "Alice")
check("and the identity claiming the seat", req["uid"], "uid-1")
check("and the build that asked", req["build"], "0.1.2")
check("and the turn it was asked at", req["requested_turn"], 4)
check("and when", req["requested_at"], 1000.0)
check("a galaxy with no identity asks anyway",
      L.join_request("Alice", None, "0.1.2")["uid"], None)

print("\n12. and where it is put")
gx = make_galaxy(os.path.join(fresh_dir(), "sandbox"), ["Bob"])
store = turn_store.open_store(gx)
where = L.send_join_request(store, req)
check("a folder galaxy takes it as a file", os.path.exists(where), True)
check("named by the identity", os.path.basename(where), "uid-1.json")
check("under the galaxy, beside the submissions",
      os.path.basename(os.path.dirname(where)), L.JOIN_DIR)
check("and it reads back whole",
      json.load(open(where, encoding="utf-8")), req)
check("with no half-written file left behind",
      [f for f in os.listdir(os.path.dirname(where)) if f.endswith(".tmp")], [])
named = L.send_join_request(store, L.join_request("Carol", None, "0.1.2"))
check("an install with no identity is named by the player",
      os.path.basename(named), "Carol.json")


class RelayStore:
    """A store that knows its own route, which is what the relay will be."""

    def __init__(self):
        self.asked = []

    def request_join(self, req):
        self.asked.append(req)
        return "beta/sandbox/joins/uid-1"


relay = RelayStore()
check("a store with a route is asked instead",
      L.send_join_request(relay, req), "beta/sandbox/joins/uid-1")
check("and gets the whole request", relay.asked, [req])


class MuteStore:
    """Neither a folder nor a route: nowhere to put a request."""


try:
    L.send_join_request(MuteStore(), req)
    check("a store that cannot take a join says so", "no refusal", "a refusal")
except L.JoinNotAccepted as exc:
    check("a store that cannot take a join says so", "MuteStore" in str(exc),
          True)

print("\n13. the whole path, from a directory to a request, on one disk")
root = fresh_dir()
sandbox = make_galaxy(os.path.join(root, "sandbox"), ["Bob", "Carol"], turn=7)
make_galaxy(os.path.join(root, "quiet"), [], turn=1)
directory = L.open_galaxy_directory(root)
check("a folder of galaxies opens as a directory", directory is not None, True)
rows = {gx.id: gx for gx in directory.galaxies(player="Alice")}
check("both galaxies are listed", sorted(rows), ["quiet", "sandbox"])
listed = rows["sandbox"]
check("the row counts the players without naming them", listed.players, 2)
check("and knows this player is not one of them", listed.joined, False)
check("and hands back a store spec rather than a path a player typed",
      turn_store.open_store(listed.store).civs(), ["Bob", "Carol"])
check("the row is offered a Join", L.joinable(listed), True)

data = fresh_dir()
opened = turn_store.open_store(listed.store)
check("the name is free to claim",
      L.seat_claim_problem("Alice", opened.civs()), None)
placed = L.send_join_request(opened, L.join_request("Alice", None, "0.1.2",
                                                    turn=listed.turn))
L.save_joined(data, L.joined_record(listed, "Alice", None, root))
check("the request is waiting in the galaxy", os.path.exists(placed), True)
check("the roster has not changed, because a worker does that",
      opened.civs(), ["Bob", "Carol"])
check("the launcher now plays what it joined",
      L.galaxy_to_play(data).get("store"), listed.store)
check("no galaxy path was written into a config file",
      os.path.exists(os.path.join(data, L.MP_CONFIG)), False)
again = {gx.id: gx for gx in directory.galaxies(player="Alice")}["sandbox"]
check("and the row shows the join as waiting, not as a seat",
      L.pending_join(again, L.load_joined(data)), True)
check("so it offers no second Join",
      L.joinable(again, L.load_joined(data)), False)

print("\n14. a galaxy that is a folder reaches no directory and no network")
lan = fresh_dir()
write_json(os.path.join(lan, L.MP_CONFIG), {"store": "http://10.0.0.5:7000"})
check("a LAN galaxy is played as it was", L.galaxy_to_play(lan).get("store"),
      "http://10.0.0.5:7000")
check("with no token", L.store_wants_token(L.galaxy_to_play(lan)), False)
check("and a folder galaxy the same",
      L.store_wants_token({"store": root}), False)


class NoDirectory:
    """An import hook standing in for a build frozen without galaxy_directory."""

    def find_module(self, name, path=None):
        return None

    def find_spec(self, name, path=None, target=None):
        if name == "galaxy_directory":
            raise ImportError("no galaxy_directory in this build")
        return None


hook = NoDirectory()
saved = sys.modules.pop("galaxy_directory")
sys.meta_path.insert(0, hook)
try:
    check("a build without the directory lists nothing rather than failing",
          L.open_galaxy_directory(root), None)
finally:
    sys.meta_path.remove(hook)
    sys.modules["galaxy_directory"] = saved
check("and the directory opens again once it is there",
      L.open_galaxy_directory(root) is not None, True)

for d in dirs:
    shutil.rmtree(d, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
