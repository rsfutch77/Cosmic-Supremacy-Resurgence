"""
patch_save_message.py: a client saves when it is sent a window message

    python patch_save_message.py --source CosmicSupremacy_TestBed.exe --out <stage>\\game\\CosmicSupremacy_Player.exe
    python patch_save_message.py --status <exe> [<exe> ...]

`--out` may be `--source` when neither is tracked by git; the release build
patches each staged client in place.

`SaveGame` (`0x0048B350`) has one caller, the Save/Load dialog, which no
multiplayer client opens, so the launcher has made a client save by calling it
in a remote thread. This patch gives the client its own handler instead.

Every message a window of the client receives passes through MFC's
`AfxWndProc` (`0x0065488D`), which first compares the message with
`WM_QUERYAFXWNDPROC` (`0x360`). Those 9 bytes at `0x00654892` become a jump to
a stub in the `.text` padding at `0x006D20A0`, clear of the Tutorial's stubs
at `0x006D30A0`. The stub answers `0x360` as before. A registered message
(`0xC000` and up) equal to `RegisterWindowMessageA(SAVE_MESSAGE_NAME)` calls
`SaveGame(wParam, "resident")` with a name built on the stack and returns its
result, 1 for a save. Every other message goes on to the rest of
`AfxWndProc`.

The handler runs on the client's main thread, the one that owns the game's
state, so a sent message (`SendMessageTimeoutW`) returns when the save is
done. The bytes are at the same offsets in all four clients.
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(CLIENT_DIR)

CREATE_NO_WINDOW = 0x08000000

# Registered by both the launcher and the handler; the two must match.
SAVE_MESSAGE_NAME = b"CosmicSupremacyResurgenceSave"
SAVE_NAME = b"resident"                 # the name SaveGame is given

IMAGE_BASE = 0x00400000
TEXT_VA = 0x00401000                    # .text, file offset 0x400
TEXT_RAW = 0x400

HOOK = 0x00654892                       # cmp [ebp+0Ch], 360h; jne 0x006548A0
HOOK_ORIGINAL = bytes.fromhex("817d0c60030000" "7505")
REST = 0x006548A0                       # AfxWndProc past the 0x360 check
EXIT = 0x006548D6                       # pop ebp; ret 10h
STUB = 0x006D20A0                       # inside 3947 bytes of int3 padding
STUB_ROOM = 0x100

SAVEGAME = 0x0048B350                   # bool SaveGame(int, std::string*), ret 8
REGISTER_WINDOW_MESSAGE_A = 0x0074E5AC  # import address table slot

WM_QUERYAFXWNDPROC = 0x360
FIRST_REGISTERED = 0xC000


def offset(va):
    return va - TEXT_VA + TEXT_RAW


def _rel(at, to, size=5):
    """The rel32 of a jump or call of `size` bytes at `at` reaching `to`."""
    return struct.pack("<i", to - (at + size))


def hook_bytes():
    """`jmp STUB` and four NOPs, the same 9 bytes as the original."""
    return b"\xE9" + _rel(HOOK, STUB) + b"\x90" * 4


def stub_bytes():
    """The handler, assembled at STUB, then the message name it registers."""
    code = bytearray()

    def here():
        return STUB + len(code)

    def emit(b):
        code.extend(b)

    def jmp(to):
        emit(b"\xE9" + _rel(here(), to))

    def jcc(op, to):                    # 0F 8x rel32
        emit(b"\x0F" + bytes([op]) + _rel(here(), to, 6))

    emit(b"\x8B\x45\x0C")                                  # mov eax, [ebp+0Ch]
    emit(b"\x3D" + struct.pack("<I", WM_QUERYAFXWNDPROC))  # cmp eax, 360h
    emit(b"\x75\x08")                                      # jne +8
    emit(b"\x33\xC0\x40")                                  # xor eax, eax; inc eax
    jmp(EXIT)                                              # return 1
    emit(b"\x3D" + struct.pack("<I", FIRST_REGISTERED))    # cmp eax, 0C000h
    jcc(0x82, REST)                                        # jb REST
    name_at = len(code) + 1
    emit(b"\x68\0\0\0\0")                                  # push name
    emit(b"\xFF\x15" + struct.pack("<I", REGISTER_WINDOW_MESSAGE_A))
    emit(b"\x3B\x45\x0C")                                  # cmp eax, [ebp+0Ch]
    jcc(0x85, REST)                                        # jne REST
    # A VC9 std::string with its characters inline, as trigger_save builds.
    emit(b"\x83\xEC\x1C")                                  # sub esp, 1Ch
    emit(b"\x33\xC0")                                      # xor eax, eax
    for disp in range(0, 0x1C, 4):
        emit(b"\x89\x44\x24" + bytes([disp]))              # mov [esp+d], eax
    chars = SAVE_NAME.ljust(8, b"\0")
    for i in range(0, len(chars), 4):
        emit(b"\xC7\x44\x24" + bytes([4 + i]) + chars[i:i + 4])
    emit(b"\xC7\x44\x24\x14" + struct.pack("<I", len(SAVE_NAME)))  # size
    emit(b"\xC7\x44\x24\x18" + struct.pack("<I", 15))      # capacity, inline
    emit(b"\x8B\xC4")                                      # mov eax, esp
    emit(b"\x50")                                          # push eax (name)
    emit(b"\xFF\x75\x10")                                  # push [ebp+10h]
    emit(b"\x8B\xC8")                                      # mov ecx, eax
    emit(b"\xE8" + _rel(here(), SAVEGAME))                 # call SaveGame
    emit(b"\x83\xC4\x1C")                                  # add esp, 1Ch
    emit(b"\x0F\xB6\xC0")                                  # movzx eax, al
    jmp(EXIT)
    struct.pack_into("<I", code, name_at, STUB + len(code))
    emit(SAVE_MESSAGE_NAME + b"\0")
    assert len(code) <= STUB_ROOM
    return bytes(code)


def state(data):
    hook = bytes(data[offset(HOOK):offset(HOOK) + len(HOOK_ORIGINAL)])
    stub = stub_bytes()
    room = bytes(data[offset(STUB):offset(STUB) + len(stub)])
    if hook == HOOK_ORIGINAL and room == b"\xCC" * len(stub):
        return "original"
    if hook == hook_bytes() and room == stub:
        return "patched"
    return "unknown"


def patch(data):
    """Patch `data` (a bytearray) in place. Returns 1 if it changed, else 0."""
    now = state(data)
    if now == "unknown":
        raise SystemExit("AfxWndProc or the padding is not where it was measured")
    if now == "patched":
        return 0
    stub = stub_bytes()
    data[offset(STUB):offset(STUB) + len(stub)] = stub
    data[offset(HOOK):offset(HOOK) + len(HOOK_ORIGINAL)] = hook_bytes()
    return 1


def is_tracked(path):
    try:
        r = subprocess.run(["git", "ls-files", "--error-unmatch", path],
                           cwd=REPO_ROOT, capture_output=True,
                           creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    except OSError:
        return False
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--status", nargs="+", metavar="EXE",
                    help="report the save handler in these clients")
    ap.add_argument("--source", help="client to read")
    ap.add_argument("--out", help="patched client to write")
    a = ap.parse_args()

    if a.status:
        for path in a.status:
            if not os.path.exists(path):
                print(f"  {os.path.basename(path):<34} missing")
                continue
            with open(path, "rb") as f:
                print(f"  {os.path.basename(path):<34} {state(f.read())}")
        return 0

    if not (a.source and a.out):
        ap.error("give --status, or both --source and --out")
    src = a.source if os.path.isabs(a.source) else os.path.join(CLIENT_DIR, a.source)
    out = os.path.abspath(a.out)
    if not os.path.exists(src):
        sys.exit(f"no such client: {src}")
    if is_tracked(out):
        sys.exit(f"{out} is tracked by git; refusing to patch it")

    with open(src, "rb") as f:
        data = bytearray(f.read())
    done = patch(data)
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    shutil.copystat(src, tmp)
    os.replace(tmp, out)
    print(f"wrote {os.path.basename(out)} from {os.path.basename(src)}, "
          f"save handler {'patched' if done else 'already patched'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
