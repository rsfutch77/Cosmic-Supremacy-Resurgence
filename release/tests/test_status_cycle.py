"""
Integration test for the launcher's game-exit detection.

Drives the real Launcher against the real client: clicks a mode, confirms the
status line reports it running, kills the game, and confirms the status returns
to the ready state on its own. Pumps the Tk event loop with update() instead of
mainloop() so the test stays in control.
"""
import json
import os
import subprocess
import sys
import time
import tkinter as tk

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
os.chdir(REPO)

import launcher as L

cfg = json.load(open(os.path.join(REPO, "release", "manifest.json"), encoding="utf-8"))
mode = next(m for m in cfg["modes"] if m["id"] == (sys.argv[1] if len(sys.argv) > 1 else "tutorial"))

root = tk.Tk()
app = L.Launcher(root, cfg)


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.05)


def status():
    return app.status.cget("text")


failures = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        failures.append(label)


print("1. after boot, before launching")
pump(1.5)
ready = status()
check("status mentions the server", ready, lambda s: "server" in s.lower())

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
root.destroy()

print("\n" + ("ALL PASSED" if not failures else f"FAILURES: {failures}"))
sys.exit(1 if failures else 0)
