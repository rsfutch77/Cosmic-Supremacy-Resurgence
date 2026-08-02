"""
advance_turns.py — Advance the game N turns unattended
======================================================
Shortens the turn length, watches the turn counter until it has advanced N
times, then restores the original turn length. Unlike fast_turns.py this reads
the turn counter, so it reports actual progress and detects a stalled pipeline
instead of leaving you guessing.

    python advance_turns.py 50            # 50 turns, 10s each
    python advance_turns.py 50 --secs 5   # faster, if the machine keeps up
    python advance_turns.py --restore     # just put turnlength back to 3600

Addresses (both .data, stable across launches):
    0x0080AA08  turn length in seconds; writing it collapses the current turn
    0x008578E8  turn counter, located by capturing every address holding the
                UI's turn number and keeping those that incremented together
"""
import ctypes
from ctypes import wintypes
import struct
import sys
import time

PROCESS_VM_READ           = 0x0010
PROCESS_VM_WRITE          = 0x0020
PROCESS_VM_OPERATION      = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400

TURNLENGTH_ADDR = 0x0080AA08
TURN_COUNTER    = 0x008578E8
DEFAULT_LENGTH  = 3600

psapi    = ctypes.WinDLL("psapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


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


def rd32(h, addr):
    buf, n = (ctypes.c_ubyte * 4)(), ctypes.c_size_t()
    if kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, 4, ctypes.byref(n)):
        return struct.unpack("<I", bytes(buf))[0]
    return None


def wr32(h, addr, value):
    data = struct.pack("<I", value)
    buf, n = (ctypes.c_ubyte * 4)(*data), ctypes.c_size_t()
    return bool(kernel32.WriteProcessMemory(h, ctypes.c_void_p(addr), buf, 4,
                                            ctypes.byref(n)))


def main():
    args = [a for a in sys.argv[1:]]
    restore_only = "--restore" in args
    secs = 10
    if "--secs" in args:
        secs = int(args[args.index("--secs") + 1])
        args = args[:args.index("--secs")]
    want = int(args[0]) if args and args[0].isdigit() else 10

    pid, name = find_pid()
    if not pid:
        sys.exit("ERROR: CosmicSupremacy not found. Launch the game first.")
    access = (PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION
              | PROCESS_QUERY_INFORMATION)
    h = kernel32.OpenProcess(access, False, pid)
    if not h:
        sys.exit(f"ERROR: could not open PID {pid}")
    print(f"attached to {name} (pid {pid})")

    original = rd32(h, TURNLENGTH_ADDR)
    if original == 0xFFFFFFFF:
        kernel32.CloseHandle(h)
        sys.exit("turn length is uninitialised — load a galaxy first")

    if restore_only:
        wr32(h, TURNLENGTH_ADDR, DEFAULT_LENGTH)
        print(f"turn length restored to {DEFAULT_LENGTH}s")
        kernel32.CloseHandle(h)
        return

    start = rd32(h, TURN_COUNTER)
    print(f"turn {start}, advancing {want} turns at {secs}s each "
          f"(turn length was {original}s)")
    wr32(h, TURNLENGTH_ADDR, secs)

    target = start + want
    last, stalled_since = start, time.time()
    # generous per-turn allowance: the engine resolves a turn in well under the
    # turn length, but a slow machine or a modal prompt can hold it up
    stall_limit = max(secs * 6, 60)
    try:
        while True:
            time.sleep(1.0)
            now = rd32(h, TURN_COUNTER)
            if now is None:
                print("lost the process")
                break
            if now != last:
                done, total = now - start, want
                print(f"  turn {now}  ({done}/{total})")
                last, stalled_since = now, time.time()
                # the game may reset turn length itself at a turn boundary
                if rd32(h, TURNLENGTH_ADDR) != secs:
                    wr32(h, TURNLENGTH_ADDR, secs)
            if now >= target:
                print(f"reached turn {now}")
                break
            if time.time() - stalled_since > stall_limit:
                print(f"STALLED at turn {now} for {stall_limit}s — the turn "
                      f"pipeline is not firing unattended.")
                print("  If the game is showing a dialog, dismiss it and re-run.")
                break
    except KeyboardInterrupt:
        print(f"\ninterrupted at turn {rd32(h, TURN_COUNTER)}")
    finally:
        wr32(h, TURNLENGTH_ADDR, original if original != secs else DEFAULT_LENGTH)
        print(f"turn length restored to {rd32(h, TURNLENGTH_ADDR)}s")
        kernel32.CloseHandle(h)


if __name__ == "__main__":
    main()
