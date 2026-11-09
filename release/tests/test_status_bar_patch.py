"""
test_status_bar_patch.py: the multiplayer client's status bar loses its
countdown and connection label
    server\\.venv\\Scripts\\python.exe release\\tests\\test_status_bar_patch.py

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

import patch_status_bar as P                                    # noqa: E402

fails = []
SOURCE = os.path.join(REPO, "client", "CosmicSupremacy_TestBed.exe")


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


def sections(data):
    """{name: (va, raw, size)} and the image base."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    count = struct.unpack_from("<H", data, pe + 6)[0]
    optional = struct.unpack_from("<H", data, pe + 20)[0]
    base = struct.unpack_from("<I", data, pe + 24 + 28)[0]
    out, at = {}, pe + 24 + optional
    for _ in range(count):
        name = data[at:at + 8].rstrip(b"\0").decode()
        _vsize, va, size, raw = struct.unpack_from("<IIII", data, at + 8)
        out[name] = (va, raw, size)
        at += 40
    return out, base


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

print("\n2. only the four strings change, each keeping its length")
inside = set()
for off, orig, new in P.SITES:
    inside.update(range(off, off + len(orig)))
    check(f"same length at {off:#x}", len(new), len(orig))
changed = {i for i, (a, b) in enumerate(zip(original, data)) if a != b}
check("no byte outside them changed", changed - inside, set())
check("the file is the same size", len(data), len(original))
secs, base = sections(original)
rva, raw, size = secs[".rdata"]
check("every string is in .rdata",
      all(raw <= off and off + len(o) <= raw + size for off, o, _n in P.SITES),
      True)

print("\n3. what the status bar can still say")
text = {off: bytes(data[off:off + len(o)]) for off, o, _n in P.SITES}
countdown = text[0x34E9E8].split(b"\0")[0]
check("the turn line keeps the turn number and its suffix",
      countdown, b"Turn: %d%s")
check("before the game starts it keeps the turn number",
      text[0x34EA10].split(b"\0")[0], b"Turn: %d")
check("the connection labels keep their colour tags and lose their words",
      [t.count(b"\r<") for t in (text[0x34E990], text[0x34E9B0])]
      + [b"onnected" in t for t in (text[0x34E990], text[0x34E9B0])],
      [2, 2, False, False])
check("the turn line formats only the two values it prints",
      countdown.count(b"%"), 2)

print("\n4. each string is read at one site, in the status bar's function")
tva, traw, tsize = secs[".text"]
code = original[traw:traw + tsize]
for off, orig, _new in P.SITES:
    va = base + rva + off - raw
    hits = []
    at = code.find(struct.pack("<I", va))
    while at >= 0:
        hits.append(base + tva + at)
        at = code.find(struct.pack("<I", va), at + 1)
    check(f"{orig[:24]!r} is read once, near 0x00405D00",
          hits, lambda h: len(h) == 1 and 0x405D00 <= h[0] < 0x406200)

print("\n5. it stacks with the patches the build runs before it")
work = tempfile.mkdtemp(prefix="statusbar_")
try:
    out = os.path.join(work, "CosmicSupremacy_Player.exe")
    py = sys.executable
    steps = [
        ["patch_launch_messages.py", "--source", SOURCE, "--out", out],
        ["patch_window_size.py", "--source", out, "--out", out],
        ["patch_status_bar.py", "--source", out, "--out", out],
    ]
    codes = []
    for step in steps:
        r = subprocess.run([py, os.path.join(TOOLS, step[0])] + step[1:],
                           capture_output=True, text=True)
        codes.append(r.returncode)
    check("the build's chain runs", codes, [0, 0, 0])
    with open(out, "rb") as f:
        staged = f.read()
    check("and leaves the status bar patched", P.state(staged), "patched")
finally:
    shutil.rmtree(work, ignore_errors=True)

print()
if fails:
    print(f"FAILURES: {fails}")
    sys.exit(1)
print("ALL PASSED")
