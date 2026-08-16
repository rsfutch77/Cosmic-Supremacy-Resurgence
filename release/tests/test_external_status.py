"""
The launcher must notice a game it did not start.

Reproduces the reported nit: TestBed launched outside the launcher left the
status line on "server running" instead of "Single Player is running".
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
mode = next(m for m in cfg["modes"] if m["id"] == "testbed")

root = tk.Tk()
app = L.Launcher(root, cfg)

fails = []


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.05)


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


pump(1.5)
ready = app.status.cget("text")
check("idle status", ready, lambda s: "server" in s.lower())

print("\nstart the client EXTERNALLY - the launcher gets no child handle")
exe = os.path.join(app.game_root, mode["exe"])
gal = os.path.join(app.galaxy_root, mode["galaxy"])
proc = subprocess.Popen([exe, gal], cwd=app.game_root)
check("launcher has no child handle", app.child is None, True)

pump(6.0)
check("status names the running mode", app.status.cget("text"),
      "Single Player is running")

print("\nclose it - status must return to ready")
subprocess.run(["taskkill", "/PID", str(proc.pid), "/F"], capture_output=True)
pump(5.0)
check("status back to ready", app.status.cget("text"), ready)

print("\nan ambiguous EXE (Tutorial and Demo share one) must not guess")
exe2 = os.path.join(app.game_root, "CosmicSupremacy.exe")
gal2 = os.path.join(app.galaxy_root, "DemoGalaxy_local.csgalaxy")
p2 = subprocess.Popen([exe2, gal2], cwd=app.game_root)
pump(6.0)
check("generic wording, not a wrong guess", app.status.cget("text"),
      "A game is running")
subprocess.run(["taskkill", "/PID", str(p2.pid), "/F"], capture_output=True)
pump(5.0)
check("ready again", app.status.cget("text"), ready)

subprocess.run(["taskkill", "/IM", "CosmicSupremacy.exe", "/F"], capture_output=True)
subprocess.run(["taskkill", "/IM", "CosmicSupremacy_TestBed.exe", "/F"],
               capture_output=True)
root.destroy()
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
