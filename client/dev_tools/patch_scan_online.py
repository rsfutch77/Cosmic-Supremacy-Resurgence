"""
patch_scan_online.py: a client sends its scans to the launcher

    python patch_scan_online.py --source CosmicSupremacy_TestBed.exe --out <exe>
    python patch_scan_online.py --status <exe> [<exe> ...]

`--out` may be `--source` when neither is tracked by git.

Every client here runs offline (`[0x0086F1A0]`), so `DOSC` (`0x00576D40`)
scans its own copy of the turn. Online, the routine sends `DOSC` on the
channel `[0x0086F198]` and applies the server's answer. A command takes the
online branch only while the connection `[0x0086F194]` is up, and that
connection serves every command at once.

This patch gives `DOSC` a connection of its own for the length of one scan.
The 21 bytes at `0x00576DB4` (the port-4710 test and its jump to the
offline branch) become a jump to a stub in the `.text` padding at
`0x006D2200`, clear of the save handler at `0x006D20A0`. The stub sets the
host to `SCAN_HOST` (`0x00563000`) and connects to `SCAN_PORT`
(`0x00563310`). A failed connect takes the offline branch, as an unpatched
client does. Otherwise the online branch runs, and both of its exits to
`0x00577090` jump to a second stub that closes the socket (the connection's
slot 2, which `0x00562EA0` also calls before connecting) and clears the up
byte `+0x4CD`. No other command sees the connection up.
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

SCAN_HOST = b"127.0.0.1"
SCAN_PORT = 8890                        # the launcher's scan endpoint

TEXT_VA = 0x00401000                    # .text, file offset 0x400
TEXT_RAW = 0x400

HOOK = 0x00576DB4                       # cmp [86F1A2],1; mov [esp+D8],1; je offline
HOOK_ORIGINAL = bytes.fromhex("803da2f1860001" "c68424d800000001" "0f8480010000")
ONLINE = 0x00576DC9                     # the online branch's connection test
OFFLINE = 0x00576F49                    # the offline branch
EXITS = (0x00576F2D, 0x00576F44)        # the online branch's jmp 0x00577090
EXIT_ORIGINAL = (bytes.fromhex("e95e010000"), bytes.fromhex("e947010000"))
TAIL = 0x00577090
STUB = 0x006D2200                       # inside 3947 bytes of int3 padding
STUB_ROOM = 0x100

SET_HOST = 0x00563000                   # void (std::string*), cdecl
CONNECT = 0x00563310                    # bool Connection::Connect(int port), ret 4
CONNECTION = 0x0086F194                 # Connection*
UP = 0x4CD                              # Connection: the socket is connected
CLOSE_SLOT = 8                          # Connection vtable: close the socket
PUSHAD_EAX = 0x1C                       # eax's slot in what pushad pushed


def offset(va):
    return va - TEXT_VA + TEXT_RAW


def _rel(at, to, size=5):
    """The rel32 of a jump or call of `size` bytes at `at` reaching `to`."""
    return struct.pack("<i", to - (at + size))


def stubs():
    """(connect stub address, close stub address, bytes at STUB)."""
    code = bytearray()

    def here():
        return STUB + len(code)

    def emit(b):
        code.extend(b)

    def jmp(to):
        emit(b"\xE9" + _rel(here(), to))

    def jcc(op, to):                    # 0F 8x rel32
        emit(b"\x0F" + bytes([op]) + _rel(here(), to, 6))

    def call(to):
        emit(b"\xE8" + _rel(here(), to))

    connect_at = here()
    emit(b"\xC6\x84\x24\xD8\x00\x00\x00\x01")             # mov byte [esp+D8h], 1
    emit(b"\x60")                                          # pushad
    host_at = len(code) + 1
    emit(b"\x68\0\0\0\0")                                  # push host
    call(SET_HOST)
    emit(b"\x83\xC4\x04")                                  # add esp, 4
    emit(b"\x8B\x0D" + struct.pack("<I", CONNECTION))      # mov ecx, [connection]
    emit(b"\x33\xC0")                                      # xor eax, eax
    emit(b"\x85\xC9")                                      # test ecx, ecx
    emit(b"\x74\x0A")                                      # jz +10
    emit(b"\x68" + struct.pack("<I", SCAN_PORT))           # push port
    call(CONNECT)
    emit(b"\x89\x44\x24" + bytes([PUSHAD_EAX]))            # mov [esp+1Ch], eax
    emit(b"\x61")                                          # popad
    emit(b"\x84\xC0")                                      # test al, al
    jcc(0x84, OFFLINE)                                     # jz OFFLINE
    jmp(ONLINE)

    close_at = here()
    emit(b"\x60")                                          # pushad
    emit(b"\x8B\x35" + struct.pack("<I", CONNECTION))      # mov esi, [connection]
    emit(b"\x85\xF6")                                      # test esi, esi
    emit(b"\x74\x0E")                                      # jz +14
    emit(b"\x8B\x06")                                      # mov eax, [esi]
    emit(b"\x8B\xCE")                                      # mov ecx, esi
    emit(b"\xFF\x50" + bytes([CLOSE_SLOT]))                # call [eax+8]
    emit(b"\xC6\x86" + struct.pack("<I", UP) + b"\x00")    # mov byte [esi+4CDh], 0
    emit(b"\x61")                                          # popad
    jmp(TAIL)

    # A VC9 std::string with its characters inline: allocator, 16 bytes,
    # size, capacity 15.
    while len(code) % 4:
        emit(b"\xCC")
    host = here()
    emit(b"\0" * 4 + SCAN_HOST.ljust(16, b"\0")
         + struct.pack("<II", len(SCAN_HOST), 15))
    struct.pack_into("<I", code, host_at, host)
    assert len(code) <= STUB_ROOM
    return connect_at, close_at, bytes(code)


def hook_bytes():
    connect_at, _close, _code = stubs()
    return b"\xE9" + _rel(HOOK, connect_at) + b"\x90" * (len(HOOK_ORIGINAL) - 5)


def exit_bytes(at):
    _connect, close_at, _code = stubs()
    return b"\xE9" + _rel(at, close_at)


def state(data):
    _c, _k, code = stubs()
    hook = bytes(data[offset(HOOK):offset(HOOK) + len(HOOK_ORIGINAL)])
    exits = [bytes(data[offset(a):offset(a) + 5]) for a in EXITS]
    room = bytes(data[offset(STUB):offset(STUB) + len(code)])
    if (hook == HOOK_ORIGINAL and tuple(exits) == EXIT_ORIGINAL
            and room == b"\xCC" * len(code)):
        return "original"
    if (hook == hook_bytes() and exits == [exit_bytes(a) for a in EXITS]
            and room == code):
        return "patched"
    return "unknown"


def patch(data):
    """Patch `data` (a bytearray) in place. Returns 1 if it changed, else 0."""
    now = state(data)
    if now == "unknown":
        raise SystemExit("DOSC or the padding is not where it was measured")
    if now == "patched":
        return 0
    _c, _k, code = stubs()
    data[offset(STUB):offset(STUB) + len(code)] = code
    data[offset(HOOK):offset(HOOK) + len(HOOK_ORIGINAL)] = hook_bytes()
    for a in EXITS:
        data[offset(a):offset(a) + 5] = exit_bytes(a)
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
                    help="report the scan patch in these clients")
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
          f"scan patch {'applied' if done else 'already there'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
