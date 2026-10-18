"""
Integration test for the launcher's game-exit detection.

Drives the real Launcher against the real client: clicks a mode, confirms the
status line reports it running, kills the game, and confirms the status returns
to the ready state on its own. The window is the real web_view.WebView with a
recording stand-in for the page; its controller loop runs here for a set time.
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
mode = next(m for m in cfg["modes"] if m["id"] == (sys.argv[1] if len(sys.argv) > 1 else "tutorial"))

view = WebView(cfg)
view.window = PageWindow(view)
app = L.Launcher(view, cfg)


def pump(seconds):
    done = threading.Event()
    view.after(int(seconds * 1000), done.set)
    view._pump(until=done)


def status():
    """What the page's status line was last told to read."""
    return (view.window.last("status") or {}).get("text")


failures = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        failures.append(label)


print("1. after boot, before launching")
pump(1.5)
ready = status()
check("idle status reads ready", ready, "ready")

print("\n2. click the Tutorial button")
app.on_play(mode)
pump(2.0)
check("status reports the game running", status(), f"{L._short(mode)} is running")
check("child handle held", app.child is not None, True)
check("running_mode recorded", (app.running_mode or {}).get("id"), mode["id"])

print("\n3. let the client settle, then close it as a player would")
pump(6.0)
still_up = app.child is not None and app.child.poll() is None
check("client still alive before we kill it", still_up, True)
subprocess.run(["taskkill", "/PID", str(app.child.pid), "/F"],
               capture_output=True)

print("\n4. wait for the watcher to notice (polls once a second)")
pump(4.0)
check("status returned to ready", status(), ready)
check("child handle cleared", app.child is None, True)
check("running_mode cleared", app.running_mode is None, True)

subprocess.run(["taskkill", "/IM", "CosmicSupremacy.exe", "/F"], capture_output=True)
for srv in app.servers:
    srv.shutdown()

print("\n" + ("ALL PASSED" if not failures else f"FAILURES: {failures}"))
sys.exit(1 if failures else 0)
