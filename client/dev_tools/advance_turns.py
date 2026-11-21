"""
advance_turns.py: Advance the game N turns unattended

Shortens the turn length, watches the turn counter until it has advanced N
times, then stops the clock. Reports progress and detects a stalled turn
pipeline.

    python advance_turns.py 50              # 50 turns, 10s each
    python advance_turns.py 50 --secs 5     # faster, if the machine keeps up
    python advance_turns.py 1 --hold 86400  # one turn, then a day's length
    python advance_turns.py --restore       # just put turnlength back to 3600

After a turn boundary, a write that shortens the turn length ends another
turn at once, even when the new length is a day. The engine re-applies the
galaxy's own length at the boundary, so once the target shows the length is
only ever lengthened: the length left behind is the longer of the galaxy's
and `--hold` (or the length found on attach). The counter is then watched
for `SETTLE` seconds. Exit codes: 0 at exactly the target, 2 short of it
(stalled, interrupted, process lost), 3 past it.

Addresses (both .data, stable across launches):
    0x0080AA08  turn length in seconds; shortening it can end the current turn
    0x008578E8  turn counter
"""
import ctypes
from ctypes import wintypes
import struct
import sys
import time
from client_proc import find_pid, NOT_THE_CLIENT

PROCESS_VM_READ           = 0x0010
PROCESS_VM_WRITE          = 0x0020
PROCESS_VM_OPERATION      = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400

TURNLENGTH_ADDR = 0x0080AA08
TURN_COUNTER    = 0x008578E8
DEFAULT_LENGTH  = 3600

POLL   = 0.05     # seconds between counter reads
SETTLE = 3.0      # seconds the counter is watched at the target

SHORT, PAST = 2, 3    # exit codes

psapi    = ctypes.WinDLL("psapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)





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


def lengthen(h, value):
    """Write `value` as the turn length only if it is longer than the live
    one. Returns the length left."""
    now = rd32(h, TURNLENGTH_ADDR)
    if now is None or value > now:
        wr32(h, TURNLENGTH_ADDR, value)
        return value
    return now


def option(args, name, cast):
    """The value after `name` in `args`, removed with it, or None."""
    if name not in args:
        return None
    i = args.index(name)
    value = cast(args[i + 1])
    del args[i:i + 2]
    return value


def main():
    args = [a for a in sys.argv[1:]]
    restore_only = "--restore" in args
    secs = option(args, "--secs", int) or 10
    hold = option(args, "--hold", int)
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
        sys.exit("turn length uninitialised, load a galaxy first")

    if restore_only:
        wr32(h, TURNLENGTH_ADDR, DEFAULT_LENGTH)
        print(f"turn length restored to {DEFAULT_LENGTH}s")
        kernel32.CloseHandle(h)
        return

    if hold is None:
        hold = original if original != secs else DEFAULT_LENGTH
    start = rd32(h, TURN_COUNTER)
    target = start + want
    print(f"turn {start}, advancing {want} turns at {secs}s each "
          f"(turn length was {original}s, at least {hold}s after)")

    now, reached = start, False
    # A slow machine or a modal prompt can hold a turn past its length.
    stall_limit = max(secs * 6, 60)
    try:
        wr32(h, TURNLENGTH_ADDR, secs)
        last, stalled_since = start, time.time()
        while True:
            time.sleep(POLL)
            now = rd32(h, TURN_COUNTER)
            if now is None:
                print("lost the process")
                break
            if now >= target:
                lengthen(h, hold)
                reached = True
                print(f"reached turn {now}")
                break
            if now != last:
                print(f"  turn {now}  ({now - start}/{want})")
                last, stalled_since = now, time.time()
                # The engine re-applies the galaxy's own length at a boundary.
                if rd32(h, TURNLENGTH_ADDR) != secs:
                    wr32(h, TURNLENGTH_ADDR, secs)
            if time.time() - stalled_since > stall_limit:
                print(f"STALLED at turn {now} for {stall_limit}s, the turn "
                      f"pipeline is not firing unattended.")
                print("  If the game is showing a dialog, dismiss it and re-run.")
                break
        if reached:
            settle = time.time() + SETTLE
            while time.time() < settle:
                time.sleep(POLL)
                now = rd32(h, TURN_COUNTER)
                if now is None:
                    print("lost the process")
                    break
                if now > target:
                    break
    except KeyboardInterrupt:
        now = rd32(h, TURN_COUNTER)
        print(f"\ninterrupted at turn {now}")
    finally:
        print(f"turn length left at {lengthen(h, hold)}s")
        kernel32.CloseHandle(h)

    if now is not None and now > target:
        print(f"PAST the target: turn {now}, wanted {target}")
        sys.exit(PAST)
    if now != target:
        sys.exit(SHORT)
    print(f"held at turn {target}")


if __name__ == "__main__":
    main()
