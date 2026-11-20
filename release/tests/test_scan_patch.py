"""
test_scan_patch.py: the multiplayer client sends its scans to the launcher
    server\\.venv\\Scripts\\python.exe release\\tests\\test_scan_patch.py

Patches a copy of the tracked TestBed client, in memory and then through the
same chain of patch scripts the build runs for CosmicSupremacy_Player.exe. No
game, no window.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
TOOLS = os.path.join(REPO, "client", "dev_tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(REPO, "release"))

import patch_scan_online as P                                   # noqa: E402
import scan_endpoint                                            # noqa: E402

fails = []
SOURCE = os.path.join(REPO, "client", "CosmicSupremacy_TestBed.exe")


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


def ranges():
    """The byte ranges the patch may write, as file offsets."""
    _c, _k, code = P.stubs()
    spans = [(P.HOOK, len(P.HOOK_ORIGINAL)), (P.STUB, len(code)),
             (P.WAIT_SITE, 4)] + [(a, 5) for a in P.EXITS]
    return {P.offset(a) + i for a, n in spans for i in range(n)}


with open(SOURCE, "rb") as f:
    original = f.read()
data = bytearray(original)

print("1. the tracked client is unpatched and the patch takes")
check("the tracked TestBed reads as original", P.state(original), "original")
check("the patch changes it", P.patch(data), 1)
check("and it reads back as patched", P.state(data), "patched")
again = bytearray(data)
check("a second run changes nothing",
      (P.patch(again), bytes(again) == bytes(data)), (0, True))

print("\n2. only the hook, the two exits, the stub and the wait change")
changed = {i for i, (a, b) in enumerate(zip(original, data)) if a != b}
check("no byte outside them changed", changed - ranges(), set())
check("the file is the same size", len(data), len(original))
_c, _k, code = P.stubs()
check("the stub fits its room", len(code) <= P.STUB_ROOM, True)

print("\n3. the scan's wait outlasts the launcher's limit")
TWENTY = 0x00750290 - 0x0074E000 + 0x0034C800   # .rdata, file offset 0x34C800
at = P.offset(P.WAIT_SITE)
check("the wait's fcomp reads the 20.0 in the original",
      (original[at - 2:at], struct.unpack_from("<d", original,
       TWENTY)[0]), (b"\xDC\x1D", 20.0))
target = struct.unpack_from("<I", data, at)[0]
check("and the stub's value once patched",
      struct.unpack_from("<d", data, P.offset(target))[0], P.WAIT_SECONDS)
check("the 20.0 itself is left for the routines that share it",
      data[TWENTY:TWENTY + 8],
      original[TWENTY:TWENTY + 8])
check("the wait is longer than the launcher's limit",
      P.WAIT_SECONDS > scan_endpoint.LIMIT, True)
check("the client and the launcher agree on the port",
      P.SCAN_PORT, scan_endpoint.PORT)

print("\n4. it stacks with the patches the build runs before it")
work = tempfile.mkdtemp(prefix="scanpatch_")
try:
    out = os.path.join(work, "CosmicSupremacy_Player.exe")
    py = sys.executable
    steps = [
        ["patch_launch_messages.py", "--source", SOURCE, "--out", out],
        ["patch_window_size.py", "--source", out, "--out", out],
        ["patch_save_message.py", "--source", out, "--out", out],
        ["patch_status_bar.py", "--source", out, "--out", out],
        ["patch_scan_online.py", "--source", out, "--out", out],
    ]
    codes = []
    for step in steps:
        r = subprocess.run([py, os.path.join(TOOLS, step[0])] + step[1:],
                           capture_output=True, text=True)
        codes.append(r.returncode)
    check("the build's chain runs", codes, [0] * len(steps))
    with open(out, "rb") as f:
        staged = f.read()
    check("and leaves the scan patched", P.state(staged), "patched")
    import patch_save_message
    import patch_status_bar
    check("    with the save handler and the status bar still patched",
          (patch_save_message.state(staged), patch_status_bar.state(staged)),
          ("patched", "patched"))
finally:
    shutil.rmtree(work, ignore_errors=True)

print()
if fails:
    print(f"FAILURES: {fails}")
    sys.exit(1)
print("ALL PASSED")
