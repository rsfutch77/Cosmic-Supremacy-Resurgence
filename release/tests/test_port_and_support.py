"""
Two things a player reads out loud to somebody else: what holds port 8888 when
the launcher cannot have it (N6), and the support code the operator matches to
a sign-in when a seat has to be moved to a reinstalled copy (J4).

    server\\.venv\\Scripts\\python.exe release\\tests\\test_port_and_support.py

Headless. The port half runs on command lines written the way Windows reports
them, and then reads whatever really holds 8888 on this machine, read only:
nothing here ends, binds or connects to that process. On the referee's own PC
that holder is the live worker's cs_server, which is exactly the case the
message is for. The support half makes a relay refusal on a local HTTP server
on a free port and reads it back through the store the launcher uses.

The launcher object is not built.
"""
import http.server
import inspect
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

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
    d = tempfile.mkdtemp(prefix="portsupport_")
    dirs.append(d)
    return d


HERE = r"C:\Games\resurgence"
OURS = HERE + r"\server\cs_server.py"
PY = r'"C:\Python312\python.exe"'
VENV = HERE + r"\server\.venv\Scripts\python.exe"


def proc(pid, ppid, cmd, name="python.exe"):
    return {"pid": pid, "ppid": ppid, "name": name, "cmd": cmd}


# The shape measured on the referee's PC: the listener, the venv redirector
# that started it, the worker, and the worker's own redirector.
REFEREE = [proc(28420, 27412, f"{PY}  {OURS}"),
           proc(27412, 7416, f"{VENV} {OURS}"),
           proc(7416, 28016, f"{PY}  {HERE}\\server\\referee_worker.py "
                             "--store firebase://cs-resurgence/h3check"),
           proc(28016, 24884, f'"{VENV}" {HERE}\\server\\referee_worker.py '
                              "--store firebase://cs-resurgence/h3check"),
           proc(24884, 2748, '"powershell.exe" -File run_worker.ps1',
                "powershell.exe")]

print("1. what holds the port, from its command line (N6)")
got = L.port_holder(REFEREE, [OURS])
check("this install's cs_server with its worker running is the referee",
      got["kind"], L.HOLDER_REFEREE)
check("named by the listener's pid", got["pid"], 28420)
check("with the worker found among its parents", got["worker"], 7416)
check("and the worker's captures where it documents them",
      got["saves"], HERE + r"\server\saves")
# Fails if the worker's presence were not looked for: a cs_server outliving a
# killed worker is the case the operator got stuck on.
left = L.port_holder(REFEREE[:2] + [proc(7416, 1, "explorer.exe",
                                         "explorer.exe")], [OURS])
check("the same cs_server with no worker left is a leftover",
      left["kind"], L.HOLDER_LEFT_OVER)
check("and a leftover has no worker", left["worker"], None)
# Fails if any cs_server.py counted as ours, which is the check
# `is_our_cs_server` makes by path for the same reason.
other = L.port_holder(REFEREE, [r"D:\elsewhere\server\cs_server.py"])
check("a cs_server from another folder is another copy's",
      other["kind"], L.HOLDER_OTHER_COPY)
check("whose folder is read off its command line",
      os.path.dirname(other["script"]), HERE + r"\server")
check("case and slashes as Windows writes them still match",
      L.port_holder(REFEREE, [OURS.upper()])["kind"], L.HOLDER_REFEREE)
check("another launcher is another launcher",
      L.port_holder([proc(9, 1, f'"{HERE}\\CosmicSupremacyLauncher.exe"',
                          "CosmicSupremacyLauncher.exe")], [OURS])["kind"],
      L.HOLDER_LAUNCHER)
check("and so is a checkout's",
      L.port_holder([proc(9, 1, f"{PY} {HERE}\\release\\launcher.py")],
                    [OURS])["kind"], L.HOLDER_LAUNCHER)
check("anything else is another program",
      L.port_holder([proc(5, 1, r'"C:\Tools\proxy.exe" -p 8888',
                          "proxy.exe")], [OURS])["kind"], L.HOLDER_OTHER)
check("and nothing read is unknown", L.port_holder([], [OURS])["kind"],
      L.HOLDER_UNKNOWN)

print("\n   where a worker's captures land")
check("--save-dir wins", L.referee_saves(
    r'w.py --store x --save-dir "D:\my saves" --data-dir D:\data', OURS),
    r"D:\my saves")
check("--data-dir means its saves folder", L.referee_saves(
    r"w.py --store x --data-dir=D:\data", OURS), r"D:\data\saves")
check("neither means the server directory's own",
      L.referee_saves("w.py --store x", OURS), HERE + r"\server\saves")

print("\n2. what the player is told")
data = r"C:\Users\p\AppData\Local\CosmicSupremacyResurgence"
status, line, box = L.port_held_message(8888, got, True, data)
check("the status line says the referee holds it", status,
      "port 8888 is held by this PC's referee")
check("the box says it belongs to the referee on this PC",
      "referee running on this PC" in box, True)
# Fails if the advice could be pasted wrong: the entry has to be valid JSON
# naming the worker's own saves folder.
entry = next(x.strip() for x in box.splitlines() if "adopt_server" in x)
try:
    pasted = json.loads("{" + entry + "}")
except ValueError as exc:
    pasted = f"not JSON: {exc}"
check("and gives the multiplayer.json entry that shares it",
      pasted, {"adopt_server": HERE + r"\server\saves"})
check("naming the file and where it is", L.MP_CONFIG in box and data in box,
      True)
check("and does not tell them to close a referee",
      "Close it and start" in box, False)
status, line, box = L.port_held_message(8888, left, True, data)
check("a leftover is said to be one", "left running when it stopped" in box,
      True)
check("with the pid to end it by, by hand",
      "Task Manager" in box and "28420" in box, True)
status, line, box = L.port_held_message(8888, other, True, data)
check("another copy's server names its folder",
      HERE + r"\server" in box and "another copy" in box, True)
status, line, box = L.port_held_message(
    8888, L.port_holder([proc(5, 1, "proxy.exe", "proxy.exe")], [OURS]),
    False, data)
check("another program is named and said not to be the game",
      "proxy.exe" in box and "not part of this game" in box, True)
status, line, box = L.port_held_message(8888, L.port_holder([], [OURS]),
                                        True, data)
check("with nothing read, the old refusal stands",
      "Another server already holds port 8888" in box
      and "not reusing it" in line, True)

print("\n   and nothing is ended to get there")
boot = inspect.getsource(L.Launcher._boot)
taken = boot[boot.index("if not port_is_free("):
             boot.index("self.servers, logfile = start_server")]
check("the holder is read only once the port is known to be taken",
      "port_holder_chain(port)" in taken
      and "port_holder_chain" not in boot.replace(taken, ""), True)
reads = inspect.getsource(L.port_holder_chain) + inspect.getsource(
    L.port_held_message) + taken
check("nothing on this path kills a process",
      any(w in reads for w in ("taskkill", "Stop-Process", "terminate(",
                               ".kill(")), False)

print("\n3. this install's own cs_server")
scripts = L.install_server_scripts()
check("a checkout counts its own server directory",
      os.path.join(REPO, "server", "cs_server.py") in scripts, True)

print("\n4. what really holds 8888 here, read only")
# Read from the TCP table and the process list only. No socket is opened to
# the port, not even to see whether it answers.
chain = L.port_holder_chain(8888)
if not chain:
    print("  (skipped: nothing listens on 8888 here)")
else:
    top = chain[0]
    print(f"  listener pid {top.get('pid')}: {top.get('cmd')}")
    if L.SERVER_SCRIPT in (top.get("cmd") or ""):
        script = L._script_in(top["cmd"], L.SERVER_SCRIPT)
        live = L.port_holder(chain, [script])
        check("a live cs_server of that install is recognised as its "
              "referee or its leftover", live["kind"],
              lambda k: k in (L.HOLDER_REFEREE, L.HOLDER_LEFT_OVER))
        print(f"  classified {live['kind']}, worker {live.get('worker')}, "
              f"saves {live.get('saves')}")
        check("and it is still the listener afterwards",
              (L.port_holder_chain(8888) or [{}])[0].get("pid"), top["pid"])

print("\n5. the support code (J4)")
UID = "Zq3kP9sXvT2mLw8aB1cD4eF5gH6i"
TOKEN = "AMf-vBx-refresh-token-that-signs-in"
check("is the first eight characters of the uid", L.support_code(UID),
      "Zq3kP9sX")
check("and nothing when there is no sign-in", L.support_code(None), None)
check("or a uid too short to be one", L.support_code("abc"), None)
data = fresh_dir()
check("an install that never signed in shows the build alone",
      L.build_line("0.1.6", L.support_code(L.install_uid(data))), "v0.1.6")
with open(os.path.join(data, "fb_identity.json"), "w", encoding="utf-8") as f:
    json.dump({"uid": UID, "refresh_token": TOKEN}, f)
import fb_auth                                                  # noqa: E402
fb_auth._identities.clear()
shown = L.build_line("0.1.6", L.support_code(L.install_uid(data)))
check("one that has shows its code beside the build", shown,
      lambda t: t.startswith("v0.1.6") and "support code Zq3kP9sX" in t)
check("never the whole uid", UID in shown, False)
check("nor the refresh token", TOKEN[:8] in shown, False)
told = L.seat_held_text("DemoPlayer", L.support_code(UID))
check("the held-seat refusal says whose seat and why",
      "DemoPlayer" in told and "another sign-in" in told, True)
check("and gives the code to send the operator",
      "Zq3kP9sX" in told and "operator" in told, True)
check("and says where else it is shown", "under the launcher's title" in told,
      True)
check("without the uid or the token",
      UID in told or TOKEN[:8] in told, False)
check("with no sign-in it still says what to do",
      L.seat_held_text("DemoPlayer", None),
      lambda t: "operator" in t and "code" not in t)
check("the relay's refusal is the sentence matched",
      L.SEAT_HELD in open(os.path.join(REPO, "functions", "relay.py"),
                          encoding="utf-8").read(), True)
src = inspect.getsource(L.Launcher.start_multiplayer)
check("the turn loop's failure turns a held seat into that box",
      "SEAT_HELD in said[1]" in src and "seat_held_text(" in src, True)

print("\n6. a relay refusal is read in the relay's words")
body = json.dumps({"error": "DemoPlayer is already held by another "
                            "sign-in"}).encode()
err = urllib.error.HTTPError("https://x/submission/4/DemoPlayer", 403,
                             "Forbidden", {}, io.BytesIO(body))
check("the status and the sentence come out of an HTTP error",
      L.relay_refusal(err), (403, "DemoPlayer is already held by another "
                                  "sign-in"))
check("a body that is not the relay's is no refusal",
      L.relay_refusal(urllib.error.HTTPError("u", 500, "x", {},
                                             io.BytesIO(b"<html>"))), None)
check("nor is an error that is not HTTP", L.relay_refusal(OSError("x")),
      None)


class Refusing(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/state"):
            payload = json.dumps({"turn": 4, "civs": ["DemoPlayer"],
                                  "deadline": 0}).encode()
            self.send_response(200)
        else:
            payload = body
            self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Refusing)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    store = turn_store.open_store(f"http://127.0.0.1:{server.server_port}")
    try:
        store.submission("DemoPlayer", 4)
        raised = None
    except Exception as exc:                                    # noqa: BLE001
        raised = exc
    # Fails if the store consumed or closed the body before raising, in which
    # case the player would read "HTTP Error 403: Forbidden" again.
    check("the store the launcher uses raises it with the body still there",
          L.relay_refusal(raised), lambda r: r is not None
          and L.SEAT_HELD in r[1])
finally:
    server.shutdown()

for d in dirs:
    shutil.rmtree(d, ignore_errors=True)
print()
print(f"FAILURES: {fails}" if fails else "all checks passed")
sys.exit(1 if fails else 0)
