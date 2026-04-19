"""
fast_turns.py — Set the turn length to 60 seconds for testing
==============================================================
Pokes the GSET turnlength value in memory from 3600 (60 min) to 60 (1 min).
Run after the game has loaded a galaxy.

Usage:
    python fast_turns.py          # 60-second turns
    python fast_turns.py 10       # 10-second turns
    python fast_turns.py 3600     # restore to 60-minute turns
"""
import ctypes
from ctypes import wintypes
import struct
import sys

PROCESS_VM_READ      = 0x0010
PROCESS_VM_WRITE     = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400

TURNLENGTH_ADDR = 0x0080AA08  # .data section — stable across launches

psapi    = ctypes.WinDLL("psapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def find_pid(exe_substr="CosmicSupremacy"):
    arr = (wintypes.DWORD * 4096)()
    cb  = ctypes.c_ulong()
    if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(cb)):
        raise OSError("EnumProcesses failed")
    count = cb.value // ctypes.sizeof(wintypes.DWORD)
    for i in range(count):
        pid = arr[i]
        if pid == 0:
            continue
        h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(260)
            n = psapi.GetModuleBaseNameW(h, None, buf, 260)
            if n and exe_substr.lower() in buf.value.lower():
                return pid, buf.value
        finally:
            kernel32.CloseHandle(h)
    return None, None


def main():
    new_length = 60
    if len(sys.argv) > 1:
        new_length = int(sys.argv[1])

    pid, name = find_pid()
    if not pid:
        print("ERROR: CosmicSupremacy not found. Launch the game first.")
        sys.exit(1)

    access = PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION
    h = kernel32.OpenProcess(access, False, pid)
    if not h:
        print(f"ERROR: Could not open PID {pid}")
        sys.exit(1)

    # Read current value
    buf = (ctypes.c_ubyte * 4)()
    read = ctypes.c_size_t()
    ok = kernel32.ReadProcessMemory(h, ctypes.c_void_p(TURNLENGTH_ADDR),
                                     buf, 4, ctypes.byref(read))
    if ok:
        current = struct.unpack('<I', bytes(buf))[0]
        if current == 0xFFFFFFFF:
            print(f"Turn length at 0x{TURNLENGTH_ADDR:08X}: uninitialized (galaxy not loaded yet?)")
        else:
            mins = current // 60
            secs = current % 60
            print(f"Turn length at 0x{TURNLENGTH_ADDR:08X}: {current} sec ({mins}m {secs}s)")
    else:
        print("Could not read current value")

    # Write new value
    data = struct.pack('<I', new_length)
    wbuf = (ctypes.c_ubyte * 4)(*data)
    written = ctypes.c_size_t()
    ok = kernel32.WriteProcessMemory(h, ctypes.c_void_p(TURNLENGTH_ADDR),
                                      wbuf, 4, ctypes.byref(written))
    if ok:
        mins = new_length // 60
        secs = new_length % 60
        print(f"Set turn length to: {new_length} sec ({mins}m {secs}s)")
    else:
        print("FAILED to write")

    kernel32.CloseHandle(h)


if __name__ == '__main__':
    main()
