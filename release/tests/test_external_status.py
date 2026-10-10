"""
The launcher must notice a game it did not start.

Reproduces the reported nit: TestBed launched outside the launcher left the
status line on "server running" instead of "Single Player is running". The
window is the real web_view.WebView with a recording stand-in for the page.
"""
import json
import os
import subprocess
import sys
import threading

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
os.chdir(REPO)
import launcher as L
from fake_view import PageWindow
from web_view import WebView

cfg = json.load(open(os.path.join(REPO, "release", "manifest.json"), encoding="utf-8"))
mode = next(m for m in cfg["modes"] if m["id"] == "testbed")

view = WebView(cfg)
view.window = PageWindow(view)
app = L.Launcher(view, cfg)

fails = []


def pump(seconds):
    done = threading.Event()
    view.after(int(seconds * 1000), done.set)
    view._pump(until=done)


def status():
    """What the page's status line was last told to read."""
    return (view.window.last("status") or {}).get("text")


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


pump(1.5)
ready = status()
check("idle status", ready, "ready")

print("\nstart the client EXTERNALLY - the launcher gets no child handle")
exe = os.path.join(app.game_root, mode["exe"])
gal = os.path.join(app.galaxy_root, mode["galaxy"])
proc = subprocess.Popen([exe, gal], cwd=app.game_root)
check("launcher has no child handle", app.child is None, True)

pump(6.0)
check("status names the running mode", status(), "Single Player is running")

print("\nclose it - status must return to ready")
subprocess.run(["taskkill", "/PID", str(proc.pid), "/F"], capture_output=True)
pump(5.0)
check("status back to ready", status(), ready)

print("\nan ambiguous EXE (Tutorial and Demo share one) must not guess")
exe2 = os.path.join(app.game_root, "CosmicSupremacy.exe")
gal2 = os.path.join(app.galaxy_root, "DemoGalaxy_local.csgalaxy")
p2 = subprocess.Popen([exe2, gal2], cwd=app.game_root)
pump(6.0)
check("generic wording, not a wrong guess", status(), "A game is running")
subprocess.run(["taskkill", "/PID", str(p2.pid), "/F"], capture_output=True)
pump(5.0)
check("ready again", status(), ready)

subprocess.run(["taskkill", "/IM", "CosmicSupremacy.exe", "/F"], capture_output=True)
subprocess.run(["taskkill", "/IM", "CosmicSupremacy_TestBed.exe", "/F"],
               capture_output=True)
for srv in app.servers:
    srv.shutdown()
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
