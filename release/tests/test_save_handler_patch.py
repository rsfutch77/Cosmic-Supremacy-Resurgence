"""
test_save_handler_patch.py: every shipped client saves on a sent message
    server\\.venv\\Scripts\\python.exe release\\tests\\test_save_handler_patch.py

Patches copies of the tracked clients, in memory and then through the chain of
patch scripts the build runs, and reads the stub back. No game, no window.
"""
import os
import pathlib
import struct
import subprocess
import sys
import tempfile

REPO = str(pathlib.Path(__file__).resolve().parents[2])
TOOLS = os.path.join(REPO, "client", "dev_tools")
sys.path.insert(0, TOOLS)

import patch_save_message as P                                  # noqa: E402
import trigger_save_message as T                                # noqa: E402

fails = []
CLIENTS = ["CosmicSupremacy.exe", "CosmicSupremacy_TestBed.exe",
           "CosmicSupremacy_Resurgence.exe"]
BUILD_PS1 = os.path.join(REPO, "release", "build.ps1")


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


def read(name):
    with open(os.path.join(REPO, "client", name), "rb") as f:
        return f.read()


def rel_target(data, va, size=5):
    """Where the rel32 jump or call of `size` bytes at `va` lands."""
    at = P.offset(va) + size - 4
    return va + size + struct.unpack_from("<i", data, at)[0]


print("1. each tracked client is unpatched and the patch takes")
for name in CLIENTS:
    original = read(name)
    data = bytearray(original)
    check(f"{name} reads as original", P.state(original), "original")
    check(f"{name} is patched", (P.patch(data), P.state(data)), (1, "patched"))
    again = bytearray(data)
    check(f"{name}: a second run changes nothing",
          (P.patch(again), bytes(again) == bytes(data)), (0, True))
    changed = [i for i, (a, b) in enumerate(zip(original, data)) if a != b]
    inside = (set(range(P.offset(P.HOOK), P.offset(P.HOOK) + 9))
              | set(range(P.offset(P.STUB), P.offset(P.STUB) + P.STUB_ROOM)))
    check(f"{name}: only the hook and the stub change",
          set(changed) - inside, set())

print("\n2. the hook and the stub reach where they should")
data = bytearray(read("CosmicSupremacy_TestBed.exe"))
P.patch(data)
check("AfxWndProc jumps to the stub", rel_target(data, P.HOOK), P.STUB)
stub = P.stub_bytes()
calls = [P.STUB + i for i in range(len(stub)) if stub[i] == 0xE8]
check("the stub calls SaveGame once",
      [rel_target(data, c) for c in calls], [P.SAVEGAME])
jumps = [P.STUB + i for i in range(len(stub) - 5) if stub[i] == 0xE9]
check("its returns go to AfxWndProc's epilogue",
      sorted({rel_target(data, j) for j in jumps}), [P.EXIT])
far = [P.STUB + i for i in range(len(stub) - 6)
       if stub[i] == 0x0F and stub[i + 1] in (0x82, 0x85)]
check("every other message goes back to AfxWndProc",
      sorted({rel_target(data, j, 6) for j in far}), [P.REST])
check("the epilogue is pop ebp; ret 10h",
      bytes(data[P.offset(P.EXIT):P.offset(P.EXIT) + 4]).hex(), "5dc21000")
check("the rest begins push esi; mov esi, [ebp+8]",
      bytes(data[P.offset(P.REST):P.offset(P.REST) + 4]).hex(), "56 8b 75 08".replace(" ", ""))
check("SaveGame ends ret 8",
      b"\xC2\x08\x00" in bytes(data[P.offset(P.SAVEGAME):P.offset(P.SAVEGAME) + 0x600]),
      True)
check("the import slot is RegisterWindowMessageA's",
      P.REGISTER_WINDOW_MESSAGE_A, 0x0074E5AC)
check("the stub and the launcher register one name",
      P.SAVE_MESSAGE_NAME.decode(), T.SAVE_MESSAGE_NAME)
check("the stub clears the Tutorial's stubs at 0x006D30A0",
      P.STUB + P.STUB_ROOM <= 0x006D30A0, True)

print("\n3. it stacks with the patches the build runs")
work = tempfile.mkdtemp(prefix="savehandler_")
py = sys.executable
for name, extra in (("CosmicSupremacy.exe", ["patch_tutorial.py"]),
                    ("CosmicSupremacy_TestBed.exe", ["patch_status_bar.py"]),
                    ("CosmicSupremacy_Resurgence.exe", ["patch_civ_name.py"])):
    out = os.path.join(work, name)
    src = os.path.join(REPO, "client", name)
    steps = ([["patch_launch_messages.py", "--source", src, "--out", out],
              ["patch_window_size.py", "--source", out, "--out", out],
              ["patch_save_message.py", "--source", out, "--out", out]]
             + [[e, "--source", out, "--out", out] for e in extra])
    codes = [subprocess.run([py, os.path.join(TOOLS, s[0])] + s[1:],
                            capture_output=True, text=True).returncode
             for s in steps]
    check(f"{name}: the chain runs", codes, [0] * len(steps))
    with open(out, "rb") as f:
        check(f"{name}: the handler survives it", P.state(f.read()), "patched")

print("\n4. the build applies it to every client it stages")
build = open(BUILD_PS1, encoding="utf-8-sig").read()
copy_client = build[build.index("function Copy-Client"):]
copy_client = copy_client[:copy_client.index("\n}\n")]
check("Copy-Client runs patch_save_message.py", "$PatchSave" in copy_client,
      True)
check("the launcher bundles both modules",
      all(f"'--hidden-import', '{m}'" in build
          for m in ("trigger_save_message", "patch_save_message")), True)

print()
if fails:
    print("FAILED: " + ", ".join(fails))
    sys.exit(1)
print("save handler patch checks passed")
