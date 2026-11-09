"""
test_galaxy_list_kept.py: a failed listing keeps the last rows, and a change
of sign-in lists again
============================================================================
    python release/tests/test_galaxy_list_kept.py

When listing galaxies fails (no internet, the relay down), the table keeps the
rows from the listing before, with the status, turn, countdown and player
count drawn as "?", and the line above says when they were read. Each row
keeps its button, so a galaxy the player is in still reads Play.

Logging in or out lists again at once, so a galaxy the new sign-in is in reads
Play without waiting for the slow timer. A listing already in flight asked
under the old sign-in, so its answer is dropped and replaced. Headless,
against a Launcher built without `__init__`.
"""
import os
import shutil
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import galaxy_directory                                         # noqa: E402
import launcher as L                                            # noqa: E402
from fake_view import FakeView                                  # noqa: E402

fails = []
dirs = []
OFFLINE = "Could not list galaxies: no internet connection, check yours; retrying."


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


def row(gid, joined=True, players=3):
    return galaxy_directory.Galaxy(gid, gid.title(), "open", 4,
                                   time.time() + 3600, players, joined,
                                   f"C:\\g\\{gid}")


def app(**over):
    """The Galaxies page on screen, nothing followed."""
    d = tempfile.mkdtemp(prefix="kept_")
    dirs.append(d)
    a = object.__new__(L.Launcher)
    a.mp_thread = None
    a.mp_playing = None
    a.mp_client_gone = False
    a.mp_sending = False
    a.mp_waiting = False
    a.mp_unreachable = None
    a.data_dir = d
    a.view = FakeView()
    a.page = "galaxies"
    a.games_rows = None
    a.games_listed = None
    a.games_stale = False
    a.games_again = False
    a.games_busy = False
    a.games_extra, a.games_err, a.games_dir = {}, None, "relay"
    a.games_recs = {}
    a.own_store = None
    a.sort_key, a.sort_desc = "name", False
    a.said = []
    a.say = a.said.append
    a.listings = 0

    def refresh():
        a.listings += 1
        a.games_busy = True
    a.refresh_games = refresh
    for k, v in over.items():
        setattr(a, k, v)
    return a


def cells(a):
    """{galaxy name: [cell texts]} from the last drawn table."""
    return {r.cells[0][0]: [t for t, _c in r.cells] for r in a.view.rows}


def buttons(a):
    return {r.cells[0][0]: (r.action, r.on_action is not None)
            for r in a.view.rows}


heads = [c.key for c in L.GALAXY_COLUMNS]

print("1. a listing that answers is drawn as it is")
a = app()
a._on_games([row("sandbox"), row("draco", joined=False)], {}, None)
listed = a.games_listed
got = cells(a)["Sandbox"]
check("the countdown and player count are real",
      (got[heads.index("players")], got[heads.index("left")] != "?"),
      ("3", True))
check("and the rows are not marked stale", a.games_stale, False)

print("\n2. the next listing fails")
a.games_listed = listed = time.time() - 120
a._on_games([], {}, OFFLINE)
check("the rows are kept", sorted(cells(a)), ["Draco", "Sandbox"])
check("marked stale", a.games_stale, True)
got = cells(a)["Sandbox"]
check("the status, turn, countdown and players read ?",
      [got[heads.index(k)] for k in L.STALE_KEYS], ["?"] * len(L.STALE_KEYS))
check("the name stays", got[heads.index("name")], "Sandbox")
check("a galaxy the player is in still reads Play", buttons(a)["Sandbox"],
      ("Play", True))
check("and one they are not in still reads View", buttons(a)["Draco"],
      ("View", True))
check("the time those rows were read is kept", a.games_listed, listed)
check("the line above names the failure and the age of the rows",
      a.view.games_status[0],
      lambda t: t.startswith(OFFLINE) and "Last listed 2m ago" in t)
check("a held-over row is not called stopped",
      L.row_stalled_for(row("old"), a._galaxy_view()), 0.0)
L.Launcher._tick_clocks(a)
check("the clock tick keeps the ?",
      [t for (r, i), (t, _c) in a.view.cells.items()
       if heads[i] in L.STALE_KEYS], lambda ts: ts and set(ts) == {"?"})

print("\n3. the next good listing replaces them")
a._on_games([row("sandbox", players=4)], {}, None)
check("not stale any more", a.games_stale, False)
check("with the new rows", cells(a)["Sandbox"][heads.index("players")], "4")
check("and a fresh line", a.view.games_status[0], lambda t: OFFLINE not in t)

print("\n4. a first listing that fails has nothing to keep")
a = app()
a._on_games([], {}, OFFLINE)
check("the table is empty", a.view.rows, [])
check("and says why", a.view.games_status[0], OFFLINE)

print("\n5. a change of sign-in lists again")
a = app()
a._on_games([row("sandbox", joined=False)], {}, None)
a.relist_games()
check("idle, it lists at once", a.listings, 1)
check("and drops the old sign-in's rows meanwhile", a.games_rows, None)
a = app()
a._on_games([row("sandbox", joined=False)], {}, None)
a.games_busy = True
a.relist_games()
check("with a listing in flight it waits for that one", a.listings, 0)
a._on_games([row("sandbox", joined=False)], {}, None)
check("then drops its answer and lists again",
      (a.listings, a.games_rows), (1, None))
a._on_games([row("sandbox", joined=True)], {}, None)
check("and the new answer shows Play", buttons(a)["Sandbox"], ("Play", True))

print("\n6. logging in and out is what asks for it")


class Ident:
    def __init__(self, account=None):
        self.uid = "guest" if account is None else "acct"
        self.account = account


def account_app(ident, change):
    a = app()
    a._account_locked = lambda: False
    a._show_account = lambda: None
    a._show_player_code = lambda: None
    relisted = []
    a.relist_games = lambda: relisted.append(True)

    def log_in(i):
        if change:
            i.uid, i.account = "acct", {"email": "a@b.c"}
    a._log_in = log_in

    def panel(i):
        if change:
            i.uid, i.account = "guest2", None
    a._account_panel = panel
    saved = L.account_identity
    L.account_identity = lambda _d: ident
    try:
        a.on_account()
    finally:
        L.account_identity = saved
    return relisted


check("a log in lists again", account_app(Ident(), True), [True])
check("a log in that was cancelled does not", account_app(Ident(), False), [])
check("a log out lists again",
      account_app(Ident({"email": "a@b.c"}), True), [True])
check("closing the account panel does not",
      account_app(Ident({"email": "a@b.c"}), False), [])

for d in dirs:
    shutil.rmtree(d, ignore_errors=True)
print()
if fails:
    print(f"FAILURES: {fails}")
    sys.exit(1)
print("ALL PASSED")
