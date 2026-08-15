"""
trigger_save.py — Make the running client POST a savegame, with no user click
============================================================================
Calls the game's own save routine in a remote thread.  The point is to capture
real save blobs (`server/cs_server.py` now persists them to `server/saves/`) so
the blob format — `ROUT` in particular — can be checked against bytes the engine
actually produced instead of against a layout read out of the disassembly.

    python trigger_save.py                      # save into slot 0
    python trigger_save.py --name "rout probe"
    python trigger_save.py --gameid -1          # client's "new slot" sentinel
    python trigger_save.py --dry-run            # print the stub, touch nothing

Why this is safe to call off-thread
-----------------------------------
`SaveGame` at 0x0048B350 is fully synchronous and self-contained: it serialises
the game state (0x0056D310), base64+zlib encodes it (0x00482040), sprintfs the
request body, POSTs it, and checks the reply for "DONE".  The POST is a direct
call chain to WinInet's blocking `HttpSendRequestA`
(0x0048B350 -> 0x00579DE0 -> 0x005F5160), so there is no message pump to service
and no worker thread to hand off to — nothing about it needs the UI thread.

Signature, from the call site at 0x0048BE23 and the `ret 8` epilogue:

    bool __thiscall SaveGame(void *this, int gameid, std::string *gamename)

`this` is never read: between the prologue and the first instruction that
clobbers ECX the register is never stored anywhere, so any value will do.

The gamename argument is a VC9 `std::string`.  0x0048B4C1 shows the short-string
optimisation in the clear — `cmp [edi+0x18], 0x10` then either `mov eax,[edi+4]`
or `lea eax,[edi+4]` — which pins the layout well enough to fabricate one:

    +0x00  _Myproxy    (never read on this path)
    +0x04  16-byte inline character buffer
    +0x14  _Mysize
    +0x18  _Myres      capacity; keep it under 16 to stay in the inline buffer

CAUTION: the serialiser walks the whole object graph.  Trigger it while the turn
pipeline is idle — mid-turn-resolution is a race against the main thread.
"""
import argparse
import ctypes
import struct
import sys
from ctypes import wintypes

PROCESS_VM_READ           = 0x0010
PROCESS_VM_WRITE          = 0x0020
PROCESS_VM_OPERATION      = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_CREATE_THREAD     = 0x0002

MEM_COMMIT             = 0x1000
MEM_RESERVE            = 0x2000
MEM_RELEASE            = 0x8000
PAGE_EXECUTE_READWRITE = 0x40

SAVEGAME_FN = 0x0048B350     # bool __thiscall SaveGame(this, int, std::string*)

STRING_SIZE = 0x1C           # VC9 std::string with the inline buffer
STRING_CAP  = 15             # keep under 16 so the chars stay inline

psapi    = ctypes.WinDLL("psapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.VirtualAllocEx.restype  = ctypes.c_void_p
kernel32.CreateRemoteThread.restype = wintypes.HANDLE


def find_pid(exe_substr="CosmicSupremacy"):
    arr = (wintypes.DWORD * 4096)()
    cb  = ctypes.c_ulong()
    if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(cb)):
        raise OSError("EnumProcesses failed")
    for i in range(cb.value // ctypes.sizeof(wintypes.DWORD)):
        pid = arr[i]
        if not pid:
            continue
        h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(260)
            if psapi.GetModuleBaseNameW(h, None, buf, 260) and \
               exe_substr.lower() in buf.value.lower():
                return pid, buf.value
        finally:
            kernel32.CloseHandle(h)
    return None, None


def build_string(name: str) -> bytes:
    """A VC9 std::string holding a short name, entirely in its inline buffer."""
    raw = name.encode("latin-1")[:STRING_CAP]
    buf = bytearray(STRING_SIZE)
    buf[4:4 + len(raw)] = raw                      # inline chars, NUL already there
    struct.pack_into("<I", buf, 0x14, len(raw))    # _Mysize
    struct.pack_into("<I", buf, 0x18, STRING_CAP)  # _Myres, < 16 -> inline
    return bytes(buf)


def build_call_stub(fn_addr: int, args, this_ptr: int) -> bytes:
    """
    A thread-proc stub that makes one __thiscall and returns its AL.

        push  argN ... push arg1     ; pushed in reverse, arg1 lands lowest
        mov   ecx, this
        mov   eax, fn
        call  eax                    ; callee cleans its own stack args
        ret   4                      ; the thread proc took one parameter

    Only valid for callee-cleaning targets (a `ret N` epilogue), which both
    SaveGame (ret 8) and LoadGame (ret 4) are.
    """
    code = b""
    for a in reversed(args):
        code += b"\x68" + struct.pack("<i", a)
    code += b"\xB9" + struct.pack("<I", this_ptr)
    code += b"\xB8" + struct.pack("<I", fn_addr)
    code += b"\xFF\xD0"
    code += b"\xC2\x04\x00"
    return code


def build_stub(string_addr: int, gameid: int) -> bytes:
    """SaveGame(gameid, &gamename); `this` is unused so the string doubles for it."""
    return build_call_stub(SAVEGAME_FN, [gameid, string_addr], string_addr)


def write(h, addr, data):
    n = ctypes.c_size_t()
    buf = (ctypes.c_ubyte * len(data))(*data)
    if not kernel32.WriteProcessMemory(h, ctypes.c_void_p(addr), buf, len(data),
                                       ctypes.byref(n)) or n.value != len(data):
        raise OSError(f"WriteProcessMemory failed at {addr:#x}: "
                      f"{ctypes.get_last_error()}")


def main():
    ap = argparse.ArgumentParser(description="Trigger a savegame in the running client")
    ap.add_argument("--name", default="devsave",
                    help=f"save name, truncated to {STRING_CAP} chars")
    ap.add_argument("--gameid", type=int, default=0,
                    help="save slot; 0 matches the slot cs_server advertises, "
                         "-1 is the client's allocate-new sentinel")
    ap.add_argument("--timeout", type=int, default=30, help="seconds to wait")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be injected and exit")
    args = ap.parse_args()

    if args.dry_run:
        # addresses are illustrative in a dry run; the real ones come from
        # VirtualAllocEx in the target
        s = build_string(args.name)
        stub = build_stub(0x11110000, args.gameid)
        print(f"std::string ({len(s)} bytes): {s.hex(' ')}")
        print(f"  chars={args.name[:STRING_CAP]!r} size={args.name[:STRING_CAP].__len__()} "
              f"cap={STRING_CAP}")
        print(f"stub ({len(stub)} bytes): {stub.hex(' ')}")
        print(f"  push string / push gameid={args.gameid} / mov ecx,string / "
              f"call {SAVEGAME_FN:#x} / ret 4")
        return 0

    pid, pname = find_pid()
    if not pid:
        sys.exit("ERROR: CosmicSupremacy not found. Launch the game first.")
    access = (PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION |
              PROCESS_QUERY_INFORMATION | PROCESS_CREATE_THREAD)
    h = kernel32.OpenProcess(access, False, pid)
    if not h:
        sys.exit(f"ERROR: could not open PID {pid}: {ctypes.get_last_error()}")
    print(f"attached to {pname} (pid {pid})")

    string_bytes = build_string(args.name)
    remote = kernel32.VirtualAllocEx(h, None, 0x1000, MEM_COMMIT | MEM_RESERVE,
                                     PAGE_EXECUTE_READWRITE)
    if not remote:
        kernel32.CloseHandle(h)
        sys.exit(f"ERROR: VirtualAllocEx failed: {ctypes.get_last_error()}")
    remote = int(remote)

    try:
        str_addr  = remote
        stub_addr = remote + 0x100
        write(h, str_addr, string_bytes)
        write(h, stub_addr, build_stub(str_addr, args.gameid))
        print(f"  std::string at {str_addr:#010x} = {args.name[:STRING_CAP]!r}")
        print(f"  stub at {stub_addr:#010x}, calling SaveGame({args.gameid}, name)")

        tid = wintypes.DWORD()
        th = kernel32.CreateRemoteThread(h, None, 0, ctypes.c_void_p(stub_addr),
                                         None, 0, ctypes.byref(tid))
        if not th:
            sys.exit(f"ERROR: CreateRemoteThread failed: {ctypes.get_last_error()}")
        print(f"  thread {tid.value} started; waiting up to {args.timeout}s")

        rc = kernel32.WaitForSingleObject(th, args.timeout * 1000)
        if rc != 0:
            print(f"  WARNING: wait returned {rc} — the thread has not finished. "
                  f"Leaving its page allocated so it cannot execute freed memory.")
            kernel32.CloseHandle(th)
            kernel32.CloseHandle(h)
            return 1

        code = wintypes.DWORD()
        kernel32.GetExitCodeThread(th, ctypes.byref(code))
        kernel32.CloseHandle(th)
        ok = code.value & 0xFF
        print(f"  SaveGame returned {ok} "
              f"({'saved' if ok else 'FAILED — client will have shown a dialog'})")
        print("  check server/saves/ for the captured blob")
        kernel32.VirtualFreeEx(h, ctypes.c_void_p(remote), 0, MEM_RELEASE)
        return 0 if ok else 1
    finally:
        kernel32.CloseHandle(h)


if __name__ == "__main__":
    sys.exit(main())
