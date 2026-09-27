"""
The three things a launcher has to say before a player gets anywhere: that a
seat was taken back, that a galaxy has been ended, and what the beta is.

Headless and offline. Every galaxy here is a folder made in a temporary
directory and read through turn_store, which is also the case that must keep
working with no token, no directory and no network call. No game is started and
no window is opened.

Every refusal below is paired with the same call against a healthy galaxy that
has to go through. A launcher that refused everything would pass a file of
refusal checks and would be worse than the one that refuses nothing.
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

import abandonment                                              # noqa: E402
import beta_notice                                              # noqa: E402
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
    d = tempfile.mkdtemp(prefix="gates_")
    dirs.append(d)
    return d


def make_galaxy(civs, turn=12, extra=None):
    """A galaxy a launcher can read: a clock, a roster, and a turns folder."""
    root = os.path.join(fresh_dir(), "sandbox")
    os.makedirs(os.path.join(root, "turns"), exist_ok=True)
    state = {"turn": turn, "turn_seconds": 14400, "civs": list(civs),
             "deadline": time.time() + 900, "hash": "x"}
    state.update(extra or {})
    with open(os.path.join(root, "state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    return root


def row(status="open", turn=12, deadline=None, players=3, joined=False):
    return galaxy_directory.Galaxy("sandbox", "Sandbox", status, turn,
                                   deadline, players, joined, "C:\\g\\sandbox")


print("1. a reclaimed player is told why, and told nothing about anyone else")
seated = ["Alice", "Bob", "Carol"]
check("a seated player is not stopped", L.roster_problem("Alice", seated), None)
check("and is not stopped by somebody else's reclaim",
      L.roster_problem("Alice", seated, {"turn": 9, "missed": 12}), None)

absent = L.roster_problem("Dave", seated)
check("a name that was never here still reads as no seat",
      "no seat for 'Dave'" in absent, True)
check("and does not claim anything was taken back",
      "took the seat" in absent, False)

took = L.roster_problem("Dave", seated, {"turn": 41, "missed": 12,
                                         "at": 1_700_000_000})
check("a reclaimed seat says it was taken back", "took the seat" in took, True)
check("and when", "at turn 41" in took, True)
check("and after how many misses", "after 12 missed turns in a row" in took,
      True)
check("it echoes the name that was typed", "'Dave'" in took, True)
check("and does not say no seat exists", "no seat for" in took, False)
for other in seated:
    check(f"and never names {other}", other in took, False)

one = L.roster_problem("Dave", seated, {"turn": 41, "missed": 1})
check("one missed turn is not '1 missed turns'",
      "after 1 missed turn." in one, True)
part = L.roster_problem("Dave", seated, {"missed": 12})
check("a record with no turn says the rest of it rather than 'turn None'",
      "at turn" in part, False)
check("and still says the misses", "after 12 missed turns" in part, True)
check("a record with no count says when and stops there",
      L.roster_problem("Dave", seated, {"turn": 41}),
      lambda s: "at turn 41." in s and "missed" not in s)
check("a true is not a turn number",
      L.roster_problem("Dave", seated, {"turn": True, "missed": True}),
      lambda s: "at turn" not in s and "missed" not in s)
check("an empty record is not a reclaim",
      "no seat for 'Dave'" in L.roster_problem("Dave", seated, {}), True)
check("and neither is anything that is not a record",
      "no seat for 'Dave'" in L.roster_problem("Dave", seated, "reclaimed"),
      True)
check("a seat that differs only in case still reads as a spelling",
      "no seat" in L.roster_problem("alice", seated), False)

print("\n2. the record is read out of the state, one name at a time")
taken = {"Dave": {"turn": 41, "missed": 12, "at": 1.0},
         "Erin": {"turn": 44, "missed": 12, "at": 2.0}}
state = {"civs": seated, turn_store.RECLAIMED_KEY: taken}
check("this player's own record comes back",
      L.reclaimed_seat(state, "Dave"), taken["Dave"])
check("and nobody else's does", L.reclaimed_seat(state, "Alice"), None)
check("a galaxy that has reclaimed nothing answers None",
      L.reclaimed_seat({"civs": seated}, "Dave"), None)
check("and so does a state that could not be read",
      L.reclaimed_seat(None, "Dave"), None)
check("a key holding something else is not a record",
      L.reclaimed_seat({turn_store.RECLAIMED_KEY: ["Dave"]}, "Dave"), None)
check("and neither is an entry holding something else",
      L.reclaimed_seat({turn_store.RECLAIMED_KEY: {"Dave": 12}}, "Dave"), None)
check("the launcher reads the key turn_store writes",
      L.RECLAIMED_KEY, turn_store.RECLAIMED_KEY)

print("\n3. against a galaxy on this disk, with no network and no token")
live = turn_store.open_store(make_galaxy(seated))
check("a seated player starts",
      L.roster_problem("Alice", live.civs(),
                       L.reclaimed_seat(live.state(), "Alice")), None)

reclaimed = turn_store.open_store(
    make_galaxy(["Bob", "Carol"], extra={turn_store.RECLAIMED_KEY: taken}))
said = L.roster_problem("Dave", reclaimed.civs(),
                        L.reclaimed_seat(reclaimed.state(), "Dave"))
check("a player whose seat went is told so", "took the seat" in said, True)
check("with the galaxy's own numbers",
      "at turn 41" in said and "12 missed turns" in said, True)
check("and the other reclaimed player is not in it", "Erin" in said, False)
check("the store's own reader agrees with the launcher's",
      reclaimed.reclaimed("Dave"), L.reclaimed_seat(reclaimed.state(), "Dave"))

old = turn_store.open_store(make_galaxy(["Bob"]))
check("a galaxy written before any of this has no reclaims",
      L.reclaimed_seat(old.state(), "Dave"), None)
check("and refuses a stranger the way it always did",
      "no seat for 'Dave'" in L.roster_problem("Dave", old.civs(),
                                               L.reclaimed_seat(old.state(),
                                                                "Dave")), True)
check("and lets its own player in",
      L.roster_problem("Bob", old.civs(),
                       L.reclaimed_seat(old.state(), "Bob")), None)

print("\n4. a closed galaxy says so")
check("a galaxy with no status at all is open", L.closed_problem({}), None)
check("a state that could not be read stops nothing",
      L.closed_problem(None), None)
check("an open galaxy is not refused",
      L.closed_problem({turn_store.STATUS_KEY: "open"}), None)
check("and neither is one still forming",
      L.closed_problem({turn_store.STATUS_KEY: "forming"}), None)

shut = L.closed_problem({turn_store.STATUS_KEY: turn_store.CLOSED})
check("a closed galaxy is refused", bool(shut), True)
check("in words rather than a failure", "has been closed" in shut, True)
check("and says the archive is still there", "still reads" in shut, True)
why = L.closed_problem({turn_store.STATUS_KEY: turn_store.CLOSED,
                        turn_store.CLOSED_REASON_KEY:
                            "Season one is over. Thanks for playing."})
check("the operator's reason is shown", "Season one is over" in why, True)
check("a blank reason adds nothing",
      L.closed_problem({turn_store.STATUS_KEY: turn_store.CLOSED,
                        turn_store.CLOSED_REASON_KEY: "  "}), shut)
check("and neither does one that is not text",
      L.closed_problem({turn_store.STATUS_KEY: turn_store.CLOSED,
                        turn_store.CLOSED_REASON_KEY: 7}), shut)
check("the launcher reads the keys turn_store writes",
      (L.STATUS_KEY, L.CLOSED, L.CLOSED_REASON_KEY),
      (turn_store.STATUS_KEY, turn_store.CLOSED, turn_store.CLOSED_REASON_KEY))

running = turn_store.open_store(make_galaxy(seated))
ended = turn_store.open_store(make_galaxy(seated))
ended.close(reason="The operator has started a fresh galaxy.")
check("a live galaxy on this disk is played",
      L.closed_problem(running.state()), None)
check("and its own reader agrees", running.is_closed(), False)
check("one the operator ended is refused",
      "has been closed" in L.closed_problem(ended.state()), True)
check("with what they said about it",
      "fresh galaxy" in L.closed_problem(ended.state()), True)
check("and its own reader agrees", ended.is_closed(), True)
check("a seated player of a closed galaxy still passes the roster, which is "
      "why the closed check has to come first",
      L.roster_problem("Alice", ended.civs()), None)

print("\n5. and the Galaxies list shows it")
now = 1_000_000.0


def cells(g):
    """What one row prints, column by column."""
    view = L.View(now=now)
    return {c.key: c.text(g, view) for c in L.GALAXY_COLUMNS}


open_row = cells(row(deadline=now + 3660))
check("an open row still counts down", open_row["left"], "1h 01m left")
check("and says it is open", open_row["status"], "open")
shut = cells(row(status="closed", deadline=now + 3660))
check("a closed row says it is closed", shut["status"], "closed")
check("and counts down to nothing", shut["left"], "")
check("while still saying where it got to",
      (shut["turn"], shut["players"]), ("12", "3"))
check("the row is still drawn rather than dropped", bool(shut["name"]), True)
check("a closed galaxy's countdown is absent rather than zero",
      L.GALAXY_LEFT.value(row(status="closed", deadline=now + 3660),
                          L.View(now=now)), None)
check("an open galaxy is offered a View", L.row_action(row()), L.VIEW)
check("and a closed one is offered nothing",
      L.row_action(row(status="closed")), None)
check("and neither is a closed one this player is in",
      L.row_action(row(status="closed", joined=True)), None)

print("\n6. the notice a player reads before joining")
body, why = L.beta_notice_text({})
check("there is a notice", body is not None, True)
check("and no reason not to show it", why, None)
check("nothing in it is still unfilled", beta_notice.unfilled(body), [])
check("no marker survives", "SET BEFORE THE BETA OPENS" in (body or ""), False)
check("it names itself", beta_notice.title(body), "Before you join the beta")
# The notice states no turn limits and no per-galaxy value, by an editorial
# decision: those are explained somewhere other than this page. The checks that
# used to assert both thresholds appeared, that a galaxy's own overrides were
# the ones shown, and that a build unable to fill them refused the join, went
# with the markers they were about. What is left is the property that outlived
# them: the notice exists, it is readable, and nothing in it is a placeholder.
check("no key is left without a marker to fill",
      [k for k in beta_notice.KEYS if k not in beta_notice.markers(body)],
      ["log_upload"])
check("and log_upload is the only one, pending M1",
      list(beta_notice.KEYS), ["log_upload"])

print("\n7. and the beta does not open without it")
real_text = beta_notice.text


def instead(body_text):
    def fake():
        if isinstance(body_text, BaseException):
            raise body_text
        return body_text
    return fake


beta_notice.text = instead(FileNotFoundError("beta_notice.txt is not in this "
                                             "build"))
try:
    body, why = L.beta_notice_text({})
    check("a notice that is not there refuses the join", body, None)
    check("and says what was missing", "beta_notice.txt" in (why or ""), True)
finally:
    beta_notice.text = real_text
check("and a build that has it opens", L.beta_notice_text({})[0] is not None,
      True)

MARKER = ("[SET BEFORE THE BETA OPENS: turn_hours, how long a "
          "turn is]")
beta_notice.text = instead(real_text() + "\n" + MARKER + "\n")
try:
    body, why = L.beta_notice_text({})
    check("a marker nobody filled refuses the join", body, None)
    check("and names the key", "turn_hours" in (why or ""), True)
finally:
    beta_notice.text = real_text
check("and the notice as written opens", L.beta_notice_text({})[0] is not None,
      True)


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


# `abandonment` was in this list, for a build that could not fill the miss
# thresholds. The notice no longer states them, so such a build has nothing it
# cannot say and refusing would be wrong.
for name, expect in (("beta_notice", ["beta_notice"]),):
    hook = NoModule(name)
    saved = sys.modules.pop(name)
    sys.meta_path.insert(0, hook)
    try:
        body, why = L.beta_notice_text({})
        check(f"a build without {name} refuses the join", body, None)
        check("and names what it could not say",
              all(k in (why or "") for k in expect), True)
    finally:
        sys.meta_path.remove(hook)
        sys.modules[name] = saved
    check(f"and one carrying {name} opens",
          L.beta_notice_text({})[0] is not None, True)

print("\n8. where each check sits in the launcher's own order")
mp = inspect.getsource(L.Launcher.start_multiplayer)
join = inspect.getsource(L.Launcher.on_join)
for label, text, order in (
        ("multiplayer answers the build, then the close, then the roster",
         mp, ("version_problem(", "closed_problem(", "roster_problem(")),
        ("a join answers the build, then the close, then the seat",
         join, ("version_problem(", "closed_problem(",
                "seat_claim_problem(")),
        ("and reads the notice before it asks for a seat",
         join, ("beta_notice_text(", "show_beta_notice(",
                "send_join_request("))):
    at = [text.find(part) for part in order]
    check(label, all(x > 0 for x in at) and at == sorted(at), True)
check("the roster refusal is given this player's own record",
      "roster_problem(civ, seats, taken)" in mp, True)
check("and the record is read for one name",
      "reclaimed_seat(state, civ)" in mp, True)

for d in dirs:
    shutil.rmtree(d, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
