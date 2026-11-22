"""
test_notes_label_patch.py: the News screen's Notes row says it makes a note
    server\\.venv\\Scripts\\python.exe release\\tests\\test_notes_label_patch.py

Patches a copy of each tracked client in memory, then the TestBed through the
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

import patch_notes_label as P                                   # noqa: E402
import patch_save_message as S                                  # noqa: E402
import patch_scan_online as SC                                  # noqa: E402

fails = []
CLIENTS = ("CosmicSupremacy.exe", "CosmicSupremacy_Resurgence.exe",
           "CosmicSupremacy_TestBed.exe")
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


def file_offset(data, va):
    secs, base = sections(data)
    for sva, raw, size in secs.values():
        if base + sva <= va < base + sva + size:
            return va - base - sva + raw
    return None


print("1. every tracked client is unpatched and the patch takes")
for name in CLIENTS:
    with open(os.path.join(REPO, "client", name), "rb") as f:
        original = f.read()
    data = bytearray(original)
    check(f"{name} reads as original", P.state(original), "original")
    check("    the patch changes it", P.patch(data), 1)
    check("    and it reads back as patched", P.state(data), "patched")
    again = bytearray(data)
    check("    a second run changes nothing",
          (P.patch(again), bytes(again) == bytes(data)), (0, True))

with open(SOURCE, "rb") as f:
    original = f.read()
data = bytearray(original)
P.patch(data)

print("\n2. the addresses are the ones measured")
check("the table entry is at 0x007FA874",
      file_offset(original, 0x007FA874), P.POINTER_AT)
check("the new label is at 0x006D2400", file_offset(original, P.LABEL),
      P.LABEL_AT)
check("the old label is at 0x00751268", file_offset(original, P.ORIGINAL),
      P.ORIGINAL_AT)
check("only the table entry names the old label",
      original.count(struct.pack("<I", P.ORIGINAL)), 1)

print("\n3. only the table entry and the new label change")
inside = set(range(P.POINTER_AT, P.POINTER_AT + 4)) | set(
    range(P.LABEL_AT, P.LABEL_AT + len(P.LABEL_TEXT)))
changed = {i for i, (a, b) in enumerate(zip(original, data)) if a != b}
check("no other byte changed", changed - inside, set())
check("the file is the same size", len(data), len(original))
check("the label keeps the icon and the link after the new words",
      P.LABEL_TEXT.endswith(P.ORIGINAL_TEXT[len(b"Notes "):]), True)
check("and is clear of the save handler and the scan stubs",
      P.LABEL >= max(S.STUB + S.STUB_ROOM, SC.STUB + SC.STUB_ROOM), True)

print("\n4. it stacks with the patches the build runs for the player's client")
work = tempfile.mkdtemp(prefix="noteslabel_")
try:
    out = os.path.join(work, "CosmicSupremacy_Player.exe")
    py = sys.executable
    steps = [
        ["patch_launch_messages.py", "--source", SOURCE, "--out", out],
        ["patch_window_size.py", "--source", out, "--out", out],
        ["patch_save_message.py", "--source", out, "--out", out],
        ["patch_notes_label.py", "--source", out, "--out", out],
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
    check("and leaves the Notes row patched", P.state(staged), "patched")
    check("and the save and scan patches in place",
          (S.state(staged), SC.state(staged)),
          ("patched", "patched"))
finally:
    shutil.rmtree(work, ignore_errors=True)

print()
if fails:
    print(f"FAILURES: {fails}")
    sys.exit(1)
print("ALL PASSED")
