"""
The build a launcher reports, the galaxy gate that reads it, and the copy of
the log that is fit to send.

All of it is the launcher's own module-level code, so it runs headless in about
a second. No game and no window: nothing here starts a client, and nothing here
uploads anything.

The redaction is exercised against release\\data\\launcher.log when a checkout
has one, because a real log is the only thing that proves the body dumps match
what cs_server actually writes. A checkout without one still runs every other
check, on a log built here.
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

import launcher as L                                            # noqa: E402
import stamp_build                                              # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


dirs = []


def fresh_dir():
    d = tempfile.mkdtemp(prefix="buildlog_")
    dirs.append(d)
    return d


print("1. a checkout names itself, and says it is not a release")
build = L.build_id()
check("build_id() answers", bool(build), True)
check("and marks a clone as a development build",
      build.endswith("+" + L.DEV_MARK), True)
check("build_info() always carries a build", "build" in L.build_info(), True)

print("\n2. a stamp written at package time is what gets reported")
stamp_dir = fresh_dir()
path = stamp_build.stamp("9.9.9", stamp_dir)
stamped = json.load(open(path, encoding="utf-8"))
check("the stamp carries the version it was given", stamped.get("build"), "9.9.9")
check("and when it was packaged", bool(stamped.get("stamped_at")), True)
check("and the checkout it came from, in a checkout",
      bool(stamped.get("commit")), True)

# bundled() finds a data file beside the launcher when frozen and beside the
# source when not, so pointing it at the stamp directory is what a frozen build
# does for real.
real_bundled = L.bundled
L.bundled = lambda name: os.path.join(stamp_dir, name)
check("a stamped build reports the stamp", L.build_id(), "9.9.9")
L.bundled = real_bundled
check("and the checkout goes back to a development build", L.build_id(), build)

print("\n3. builds order by their leading number only")
for name, want in (("0.1.1", (0, 1, 1)), ("0.1.10+dev", (0, 1, 10)),
                   ("2", (2,)), ("", ()), ("dev", ()), (None, ())):
    check(f"build_parts({name!r})", L.build_parts(name), want)

for lower, higher in (("0.1.0", "0.1.1"), ("0.1.9", "0.1.10"),
                      ("0.9.9", "1.0.0"), ("0.1", "0.1.1")):
    check(f"{lower} is below {higher}", L.build_is_below(lower, higher), True)
    check(f"{higher} is not below {lower}", L.build_is_below(higher, lower), False)

check("0.2 and 0.2.0 are the same build", L.build_is_below("0.2", "0.2.0"), False)
check("and in the other direction too", L.build_is_below("0.2.0", "0.2"), False)
check("a development build of the minimum passes",
      L.build_is_below("0.1.1+dev", "0.1.1"), False)
check("a build that cannot name itself is below any minimum",
      L.build_is_below("", "0.1.0"), True)

print("\n4. the gate, at, below, above and absent")
GATE = "0.1.2"
check("a galaxy with no min_build lets anyone in",
      L.version_problem("0.0.1", {"turn": 3, "civs": ["Alice"]}), None)
check("a min_build of None is the same as none at all",
      L.version_problem("0.0.1", {L.MIN_BUILD_KEY: None}), None)
check("so is a min_build that is not a build",
      L.version_problem("0.0.1", {L.MIN_BUILD_KEY: "soon"}), None)
check("a state of None does not raise", L.version_problem("0.0.1", None), None)
check("exactly at the minimum plays",
      L.version_problem(GATE, {L.MIN_BUILD_KEY: GATE}), None)
check("above the minimum plays",
      L.version_problem("0.2.0", {L.MIN_BUILD_KEY: GATE}), None)

refused = L.version_problem("0.1.1", {L.MIN_BUILD_KEY: GATE})
check("below the minimum is refused", bool(refused), True)
check("and the refusal names the launcher's own build", "0.1.1" in refused, True)
check("and the build the galaxy wants", GATE in refused, True)
check("and where to get it", L.UPDATE_URL in refused, True)

print("\n5. the gate reads a real store's state")
root = fresh_dir()
os.makedirs(os.path.join(root, "turns"))
state = {"turn": 4, "deadline": 0, "turn_seconds": 1800,
         "civs": ["Alice"], "hash": "x"}
state_path = os.path.join(root, "state.json")
json.dump(state, open(state_path, "w", encoding="utf-8"))
import turn_store                                               # noqa: E402
store = turn_store.open_store(root)
check("a galaxy written before min_build existed still plays",
      L.version_problem("0.0.1", store.state()), None)

state[L.MIN_BUILD_KEY] = GATE
json.dump(state, open(state_path, "w", encoding="utf-8"))
check("and one that names a minimum refuses an older build",
      bool(L.version_problem("0.1.1", store.state())), True)
check("and admits a newer one",
      L.version_problem("0.9.9", store.state()), None)

print("\n6. the account name comes out of paths")
for raw, want in (
        (r"data    C:\Users\tmog7\Desktop\resurgence\release\data",
         r"data    C:\Users\<user>\Desktop\resurgence\release\data"),
        (r"icon    c:\users\TMOG7\AppData\Local\cosmic.ico",
         r"icon    c:\users\<user>\AppData\Local\cosmic.ico"),
        (r"D:/Users/bob/games/cs", r"D:/Users/<user>/games/cs"),
        (r"C:\Users\Public\Documents\shared.dat",
         r"C:\Users\Public\Documents\shared.dat"),
        ("nothing to scrub here", "nothing to scrub here")):
    check(f"{raw[:38]!r}", L.scrub_paths(raw), want)

check("this machine's own profile is scrubbed",
      L.USER_MARK in L.scrub_paths(os.path.expanduser("~")), True)

print("\n7. body dumps become a byte count")
LOG = "\n".join([
    "=== launcher started ===",
    "[10:00:00] Cosmic Supremacy: Resurgence v0.1.1",
    r"[10:00:00] data    C:\Users\tmog7\Desktop\release\data",
    "[10:00:01] POST /clientinterface.php?action=savegame",
    "[10:00:01]   action=savegame",
    "[10:00:01]   body: userid=0&gamename='demo'&turn=8&version=1&data=" + "A" * 300,
    "[10:00:01]   body+: " + "B" * 400,
    "[10:00:01]   body+: " + "C" * 400,
    "[10:00:01]   body\u2026 (9452 more chars not logged)",
    "[10:00:02]   <- 200  4 bytes  b'DONE'",
    "[10:00:03] [Alice] turn 8: submitted",
]) + "\n"

out = L.redact_log_text(LOG)
check("no body+ line survives", "body+" in out, False)
check("the head of the body does, as far as data=",
      "gamename='demo'&turn=8&version=1&data=" in out, True)
check("but not the blob after it", "AAAA" in out, False)
check("one elision line replaces the run",
      out.count("bytes of request body elided"), 1)
check("and counts the head, the chunks and the tail line",
      "[1189 bytes of request body elided]" in out, True)
check("the account name is gone", "tmog7" in out, False)
check("the player's name is not", "[Alice] turn 8: submitted" in out, True)
check("and neither is the response line", "b'DONE'" in out, True)

two_runs = L.redact_log_text(LOG + LOG)
check("two requests give two elision lines",
      two_runs.count("bytes of request body elided"), 2)

print("\n8. the cap keeps the tail")
long_log = "".join(f"[10:00:00] line {n}\n" for n in range(4000))
capped = L.redact_log_text(long_log, cap=4096)
check("the result is inside the cap",
      len(capped.encode("utf-8")) <= 4096, True)
check("the last line is still there", capped.rstrip().endswith("line 3999"), True)
check("the first is not", "line 0\n" in capped, False)
check("and the copy says what it dropped",
      "bytes of older log dropped" in capped, True)
check("a log under the cap is left alone",
      L.redact_log_text(LOG, cap=1 << 20), out)

print("\n9. written to a file, from a data directory")
data_dir = fresh_dir()
with open(os.path.join(data_dir, L.LOG_NAME), "w", encoding="utf-8") as fh:
    fh.write(LOG)
written = L.write_redacted_log(data_dir)
check("the copy lands beside the log",
      os.path.basename(written), L.REDACTED_LOG_NAME)
check("and holds the redacted text",
      open(written, encoding="utf-8").read(), out)
check("a data directory with no log still answers",
      "no launcher log to send" in L.redacted_log(fresh_dir()), True)

print("\n10. against this checkout's real launcher.log")
real = os.path.join(REPO, "release", "data", L.LOG_NAME)
if not os.path.exists(real):
    print("  [SKIP] no release\\data\\launcher.log in this checkout")
else:
    raw = open(real, encoding="utf-8", errors="replace").read()
    red = L.redact_log_text(raw)
    before, after = len(raw.encode("utf-8")), len(red.encode("utf-8"))
    print(f"  {before} bytes in, {after} bytes out "
          f"({100 * after // max(before, 1)}%)")
    check("it shrank", after < before, True)
    check("no body+ line survives", "body+" in red, False)
    check("no body\u2026 line survives", "body\u2026" in red, False)
    check("every dropped run is accounted for",
          red.count("bytes of request body elided") > 0, True)
    check("scrubbing it a second time changes nothing",
          L.scrub_paths(red) == red, True)
    check("the player's civ still appears", "DemoPlayer" in red, True)

for d in dirs:
    shutil.rmtree(d, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
