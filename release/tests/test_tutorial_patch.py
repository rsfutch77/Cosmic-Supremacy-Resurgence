"""
test_tutorial_patch.py: the Tutorial and window-size patches the build applies
    server\\.venv\\Scripts\\python.exe release\\tests\\test_tutorial_patch.py

Patches a copy of the tracked client in memory and reads the result back. No
game, no window.
"""
import os
import struct
import sys

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "client", "dev_tools"))

import patch_tutorial as P                                      # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


with open(os.path.join(REPO, "client", "CosmicSupremacy.exe"), "rb") as f:
    original = f.read()
data = bytearray(original)

print("\n1. the tracked client is unpatched and the patch takes")
parts = ("panel", "orders", "scout", "texts")
check("the tracked client reads as original", P.state(original),
      dict.fromkeys(parts, "original"))
check("all four parts are patched", P.patch(data), 4)
check("and read back as patched", P.state(data), dict.fromkeys(parts, "patched"))
again = bytearray(data)
check("a second run changes nothing", (P.patch(again), bytes(again) == bytes(data)),
      (0, True))

img = P.Image(data)
orig = P.Image(bytearray(original))

print("\n2. the panel is taller and wider, and Next moves with it")
for off, before, growth, what in P.PANEL_WORDS:
    check(what, struct.unpack_from("<H", data, off)[0], before + growth)

print("\n3. Next runs the stub, which still calls Unlock")
site = img.offset(P.CALL_SITE)
check("the call site targets the stub", bytes(data[site:site + 5]),
      b"\xe8" + P._rel(P.CALL_SITE, P.STUB))
stub = P.stub_bytes()
check("the stub calls Unlock first", stub[4:9], b"\xe8" + P._rel(P.STUB + 4, P.UNLOCK))
check("and returns as Unlock does", stub[-3:], b"\xc2\x04\x00")
end = img.offset(P.STUB) + len(stub)
check("it ends inside the padding", bytes(data[end:end + 4]), b"\xcc" * 4)
check("and before the Scout Ship stubs", P.STUB + len(stub) <= P.COST_STUB, True)

print("\n3b. the Scout Ship hooks call their stubs, which fit the padding")
for site, at, body in ((P.COST_SITE, P.COST_STUB, P.cost_stub()),
                       (P.SHIP_DONE_SITE, P.SHIP_DONE_STUB, P.ship_done_stub())):
    off = img.offset(site)
    check(f"{site:#010x} calls {at:#010x}", bytes(data[off:off + 5]),
          b"\xe8" + P._rel(site, at))
    end = img.offset(at) + len(body)
    check(f"the stub at {at:#010x} ends in padding", bytes(data[end:end + 2]),
          b"\xcc" * 2)
check("the cost stub still fetches the cost first", P.cost_stub()[:5],
      P.COST_ORIGINAL)
check("the cost stub ends before the ship stub",
      P.COST_STUB + len(P.cost_stub()) <= P.SHIP_DONE_STUB, True)
check("the ship stub still raises Event first", P.ship_done_stub()[4:9],
      b"\xe8" + P._rel(P.SHIP_DONE_STUB + 4, P.EVENT))

print("\n4. every step with edits points into the new section")
section = img.section(P.SECTION_NAME)
lo = P.IMAGE_BASE + section[2]
hi = lo + section[1]
for step in range(P.STEP_COUNT):
    va = struct.unpack_from("<I", data, P.table_entry(img, step))[0]
    was = struct.unpack_from("<I", original, P.table_entry(orig, step))[0]
    if step in P.EDITS:
        text = img.cstring(va)
        ok = (lo <= va < hi and text == P.edited(step, orig.cstring(was)))
        check(f"step {step} is its edited text", ok, True)
    else:
        check(f"step {step} is untouched", va, was)

print("\n5. the texts say what the issues asked for")
for step in P.EDITS:
    if step == 21:
        continue                # waits for the ship; nothing for the player to do
    text = img.cstring(struct.unpack_from("<I", data, P.table_entry(img, step))[0])
    check(f"step {step} colours its task and restores the colour",
          (text.count(P.TASK_COLOUR), text.count(P.TASK_END)),
          lambda n: n[0] == n[1] >= 1)
step20 = img.cstring(struct.unpack_from("<I", data, P.table_entry(img, 20))[0])
check("step 20 says the ship takes one turn", "one turn" in step20, True)
step11 = img.cstring(struct.unpack_from("<I", data, P.table_entry(img, 11))[0])
check("step 11 selects both ships before Create Fleet",
      ("CTRL" in step11, "Double-click" in step11), (True, False))
step22 = img.cstring(struct.unpack_from("<I", data, P.table_entry(img, 22))[0])
check("step 22 offers drafting a citizen", "draft it" in step22, True)

print("\n6. every client's main window opens 10% larger")
import patch_window_size as W                                   # noqa: E402
for name in ("CosmicSupremacy.exe", "CosmicSupremacy_Resurgence.exe",
             "CosmicSupremacy_TestBed.exe"):
    with open(os.path.join(REPO, "client", name), "rb") as f:
        client = bytearray(f.read())
    check(f"{name} starts at 950 x 609", W.state(client), "original")
    W.patch(client)
    size = (struct.unpack_from("<H", client, 0x30EF)[0],
            struct.unpack_from("<H", client, 0x30F5)[0])
    check(f"and opens at {W.WIDTH} x {W.HEIGHT}", size, (W.WIDTH, W.HEIGHT))
both = bytearray(original)
P.patch(both)
W.patch(both)
check("the tutorial and window patches do not overlap",
      (P.state(both)["panel"], W.state(both)), ("patched", "patched"))

print("\n7. Single Player's civ name dialog always sends the name")
import patch_civ_name as C                                      # noqa: E402
with open(os.path.join(REPO, "client", "CosmicSupremacy_Resurgence.exe"), "rb") as f:
    sp_client = bytearray(f.read())
check("the tracked client skips the upload when an entry is selected",
      C.state(sp_client), "original")
check("one site is patched", C.patch(sp_client), 1)
check("and the branch is gone", bytes(sp_client[C.SITE:C.SITE + 6]), b"\x90" * 6)
check("a second run changes nothing", C.patch(sp_client), 0)
W.patch(sp_client)
check("and the window patch still applies beside it",
      (C.state(sp_client), W.state(sp_client)), ("patched", "patched"))

print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
