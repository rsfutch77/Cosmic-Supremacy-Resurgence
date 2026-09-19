"""
The player's name: what is accepted, where it is kept, and what a galaxy that
has no seat for it says back.

Everything here is the launcher's own module-level code, so it runs headless and
in about a second. The dialog that collects the name is not covered, because it
is tkinter; what the dialog decides with is.

The roster check runs against a store built here, in a temporary directory.
"""
import json
import os
import shutil
import sys
import tempfile

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import launcher as L

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


def fresh_dir():
    d = tempfile.mkdtemp(prefix="identity_")
    dirs.append(d)
    return d


dirs = []

print("1. a name is accepted, trimmed, or refused with a reason")
for raw, want in (("Alice", "Alice"),
                  ("  Alice  ", "Alice"),
                  ("Alice Smith", "Alice Smith"),
                  ("Bob_2", "Bob_2"),
                  ("O'Neill", "O'Neill"),
                  ("Fifteen chars!!", "Fifteen chars!!")):
    name, why = L.validate_player_name(raw)
    check(f"{raw!r} accepted", (name, why), (want, None))

for raw, because in (("", "empty"),
                     ("   ", "whitespace only"),
                     ("Sixteen chars!!!", "one over the buffer"),
                     ("a" * 40, "far over the buffer"),
                     ("Ali\\ce", "backslash"),
                     ("Ali/ce", "forward slash"),
                     ("Ali:ce", "colon"),
                     ('Ali"ce', "quote"),
                     ("Ali*ce", "star"),
                     ("Alice.", "trailing full stop"),
                     ("Aliçe", "not ASCII"),
                     ("Ali\tce", "control character")):
    name, why = L.validate_player_name(raw)
    check(f"{raw!r} refused ({because})", (name, bool(why)), (None, True))

print("\n2. the name is written to the data directory and read back")
d = fresh_dir()
check("nothing stored yet", L.load_identity(d), None)
check("no name at all yet", L.player_name(d), None)
L.save_identity(d, "Alice")
check("stored beside the launcher's other data",
      os.path.exists(os.path.join(d, "identity.json")), True)
check("read back", L.load_identity(d), "Alice")
check("that is the player's name", L.player_name(d), "Alice")

print("\n3. a data directory that survives an upgrade keeps the name")
# An upgrade replaces the launcher and manifest.json, never the data directory.
# Nothing to simulate but the file still being there, which is the point.
check("still there", L.player_name(d), "Alice")

print("\n4. unreadable or unusable stored names count as no name")
for content, because in (("not json at all", "corrupt file"),
                         ('{"name": ""}', "empty name"),
                         ('{"name": "Sixteen chars!!!"}', "too long"),
                         ('{"name": 7}', "not a string"),
                         ('["Alice"]', "not an object")):
    bad = fresh_dir()
    open(os.path.join(bad, "identity.json"), "w", encoding="utf-8").write(content)
    check(f"asked again ({because})", L.load_identity(bad), None)

print("\n5. an older multiplayer.json that still has a civ keeps working")
old = fresh_dir()
json.dump({"store": "\\\\HOST\\Sharing\\cosmic\\galaxy1", "civ": "Neighbor"},
          open(os.path.join(old, "multiplayer.json"), "w", encoding="utf-8"))
check("the hand-typed civ becomes the player's name", L.player_name(old),
      "Neighbor")
check("and is adopted, so it is typed in one place from now on",
      L.load_identity(old), "Neighbor")
check("the config file is left alone, so a downgrade still works",
      json.load(open(os.path.join(old, "multiplayer.json"),
                     encoding="utf-8")).get("civ"), "Neighbor")

print("\n6. the name the player entered wins over a leftover civ")
both = fresh_dir()
json.dump({"store": "somewhere", "civ": "Stale"},
          open(os.path.join(both, "multiplayer.json"), "w", encoding="utf-8"))
L.save_identity(both, "Current")
check("identity.json is the one place", L.player_name(both), "Current")

print("\n7. multiplayer.json now needs only the store")
store_only = fresh_dir()
json.dump({"store": "somewhere"},
          open(os.path.join(store_only, "multiplayer.json"), "w",
               encoding="utf-8"))
cfg = L.multiplayer_config(store_only)
check("accepted without a civ", cfg and cfg.get("store"), "somewhere")

no_store = fresh_dir()
json.dump({"civ": "Alice"},
          open(os.path.join(no_store, "multiplayer.json"), "w",
               encoding="utf-8"))
check("still refused without a store", L.multiplayer_config(no_store), None)
check("and refused when there is no file", L.multiplayer_config(fresh_dir()),
      None)

print("\n8. a name that is not in the roster is refused without naming anyone")
seats = ["Alice", "Bob", "Carol"]
check("a seat that exists is no problem", L.roster_problem("Alice", seats), None)

missing = L.roster_problem("Dave", seats)
check("a stranger is refused", bool(missing), True)
for seat in seats:
    check(f"does not name the seat {seat!r}", seat in missing, False)
check("says which name was used", "'Dave'" in missing, True)
check("and how many seats there are", "3 seats" in missing, True)

check("one seat reads as one seat",
      "one seat" in L.roster_problem("Dave", ["Alice"]), True)

# The only name it will echo is the one the player just typed, spelled the way
# the galaxy spells it. That discloses nobody new and is the mistake a player
# makes when they were told their name out loud.
wrong_case = L.roster_problem("alice", seats)
check("a case mismatch is called out on its own",
      "spells your seat 'Alice'" in wrong_case, True)
for seat in ("Bob", "Carol"):
    check(f"and still does not name {seat!r}", seat in wrong_case, False)

empty = L.roster_problem("Alice", [])
check("an empty roster still answers", bool(empty), True)
check("and says so without listing nobody", "no seats at all" in empty, True)

print("\n9. the roster comes out of a real store")
root = fresh_dir()
os.makedirs(os.path.join(root, "turns"))
json.dump({"turn": 4, "deadline": 0, "turn_seconds": 1800, "civs": seats,
           "hash": "x"},
          open(os.path.join(root, "state.json"), "w", encoding="utf-8"))
import turn_store
store = turn_store.open_store(root)
check("the store exists", store.exists(), True)
check("and reports the roster the launcher checks against", store.civs(), seats)
check("a name in it plays", L.roster_problem("Bob", store.civs()), None)
check("a name not in it does not",
      bool(L.roster_problem("Bobby", store.civs())), True)

for d in dirs:
    shutil.rmtree(d, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
