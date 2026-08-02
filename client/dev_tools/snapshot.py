"""
snapshot.py — Save and restore full game memory state
=====================================================
Takes a snapshot of all writable memory regions in the running game process
and can restore them later (same session only — heap addresses are only valid
while the process is still running).

This captures everything: EJBO objects, .data section globals (turn counter,
sync flag, dirty flags), heap-allocated state, order lists, etc.

Usage:
    python snapshot.py save                 # save to snapshot_<timestamp>.bin
    python snapshot.py save my_save         # save to my_save.bin
    python snapshot.py restore my_save      # restore from my_save.bin
    python snapshot.py list                 # list available snapshots
    python snapshot.py info my_save         # show snapshot details

Same-session only: the game process must not have been restarted between
save and restore, because heap addresses change on every launch.
"""
import ctypes
from ctypes import wintypes
import struct
import sys
import os
import json
import time
from datetime import datetime

# ── Windows API ───────────────────────────────────────────────────────────
PROCESS_VM_READ           = 0x0010
PROCESS_VM_WRITE          = 0x0020
PROCESS_VM_OPERATION      = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT                = 0x1000
PAGE_READWRITE            = 0x04
PAGE_WRITECOPY            = 0x08
PAGE_EXECUTE_READWRITE    = 0x40
PAGE_EXECUTE_WRITECOPY    = 0x80
PAGE_GUARD                = 0x100

psapi    = ctypes.WinDLL("psapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress",       ctypes.c_void_p),
        ("AllocationBase",    ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize",        ctypes.c_size_t),
        ("State",             wintypes.DWORD),
        ("Protect",           wintypes.DWORD),
        ("Type",              wintypes.DWORD),
    ]


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


def enum_writable_regions(h):
    """Enumerate all committed, writable memory regions."""
    writable = PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY
    addr    = 0
    regions = []
    mbi     = MEMORY_BASIC_INFORMATION()
    while True:
        ret = kernel32.VirtualQueryEx(h, ctypes.c_void_p(addr),
                                       ctypes.byref(mbi), ctypes.sizeof(mbi))
        if ret == 0:
            break
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize
        if (mbi.State == MEM_COMMIT
            and not (mbi.Protect & PAGE_GUARD)
            and mbi.Protect & writable):
            regions.append((base, size))
        addr = base + size
        if addr >= 0x7fff0000:
            break
    return regions


def read_bytes(h, addr, size):
    buf  = (ctypes.c_ubyte * size)()
    read = ctypes.c_size_t()
    ok   = kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(read))
    if ok and read.value > 0:
        return bytes(buf[:read.value])
    return None


def write_bytes(h, addr, data):
    buf     = (ctypes.c_ubyte * len(data))(*data)
    written = ctypes.c_size_t()
    ok = kernel32.WriteProcessMemory(h, ctypes.c_void_p(addr),
                                      buf, len(data), ctypes.byref(written))
    return bool(ok and written.value == len(data))


# ── Known .data section addresses to read for summary ─────────────────────
DATA_SECTION_SIGNALS = {
    0x0080AA08: ("turn_countdown", "i32"),
    0x0086F1A1: ("sync_flag", "u8"),
    0x008292C8: ("timer_display", "str8"),
    0x00853D24: ("action_counter", "i32"),
    0x0082A828: ("dirty_flag_1", "u8"),
}

EJBO_TAG = b'EJBO'

KNOWN_TYPES = {
    0x00768ddc: "Planet",
    0x00784934: "Admiral",
    0x00771df8: "ShipDesign",
    0x00771df0: "ShipDesign",
    0x00768B04: "Ship",
    0x007707E0: "CivStats",
}


def count_ejbo_objects(data_chunks):
    """Count EJBO objects across all memory chunks."""
    count = 0
    type_counts = {}
    for base, chunk in data_chunks:
        off = 0
        while True:
            idx = chunk.find(EJBO_TAG, off)
            if idx < 0:
                break
            count += 1
            # Try to classify — type pointer is 8 bytes before EJBO tag
            if idx >= 8:
                type_ptr = struct.unpack_from("<I", chunk, idx - 8)[0]
                type_name = KNOWN_TYPES.get(type_ptr, "Unknown")
                type_counts[type_name] = type_counts.get(type_name, 0) + 1
            off = idx + 4
    return count, type_counts


def read_signal_values(h):
    """Read key .data section values for the snapshot summary."""
    values = {}
    for addr, (name, fmt) in DATA_SECTION_SIGNALS.items():
        if fmt == "i32":
            raw = read_bytes(h, addr, 4)
            if raw:
                values[name] = struct.unpack("<i", raw)[0]
        elif fmt == "u8":
            raw = read_bytes(h, addr, 1)
            if raw:
                values[name] = raw[0]
        elif fmt == "str8":
            raw = read_bytes(h, addr, 8)
            if raw:
                null = raw.find(b'\x00')
                if null >= 0:
                    raw = raw[:null]
                values[name] = raw.decode('ascii', errors='replace')
    return values


# ── Snapshot file format ──────────────────────────────────────────────────
#
# The .bin file is a simple concatenation:
#   [JSON header as UTF-8, null-terminated]
#   [region 1 data bytes]
#   [region 2 data bytes]
#   ...
#
# The JSON header contains the region table (base address + size for each
# region) plus metadata (PID, timestamp, signal values, EJBO counts).
# On restore, each region's data is written back to its original base address.


def snapshot_path(name):
    """Get the full path for a snapshot file, stored next to this script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    snap_dir = os.path.join(script_dir, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    return os.path.join(snap_dir, f"{name}.bin")


def cmd_save(name=None):
    pid, proc_name = find_pid()
    if not pid:
        print("ERROR: CosmicSupremacy not found. Launch the game first.")
        sys.exit(1)

    access = PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION
    h = kernel32.OpenProcess(access, False, pid)
    if not h:
        print(f"ERROR: Could not open PID {pid}")
        sys.exit(1)

    print(f"[+] Attached to {proc_name} (PID {pid})")

    # Enumerate writable regions
    print("[*] Scanning memory regions...")
    regions = enum_writable_regions(h)
    print(f"    {len(regions)} writable regions found")

    # Read all regions
    print("[*] Reading memory...")
    data_chunks = []
    total_bytes = 0
    skipped = 0
    for base, size in regions:
        # Skip very large regions (>16MB) — likely video memory or mapped files
        if size > 16 * 1024 * 1024:
            skipped += 1
            continue
        chunk = read_bytes(h, base, size)
        if chunk:
            data_chunks.append((base, chunk))
            total_bytes += len(chunk)

    if skipped:
        print(f"    Skipped {skipped} regions >16MB (likely video/mapped memory)")
    print(f"    Read {len(data_chunks)} regions, {total_bytes:,} bytes total")

    # Gather metadata
    signals = read_signal_values(h)
    ejbo_count, type_counts = count_ejbo_objects(data_chunks)

    print(f"    {ejbo_count} EJBO objects found")
    for t, c in sorted(type_counts.items()):
        print(f"      {t}: {c}")
    if "turn_countdown" in signals:
        tc = signals["turn_countdown"]
        print(f"    Turn countdown: {tc}s ({tc//60}m {tc%60}s)")
    if "timer_display" in signals:
        print(f"    Timer display: {signals['timer_display']}")

    # Build header
    if name is None:
        name = f"snapshot_{datetime.now():%Y%m%d_%H%M%S}"

    header = {
        "version": 1,
        "name": name,
        "timestamp": datetime.now().isoformat(),
        "pid": pid,
        "process": proc_name,
        "total_bytes": total_bytes,
        "region_count": len(data_chunks),
        "ejbo_count": ejbo_count,
        "ejbo_types": type_counts,
        "signals": signals,
        "regions": [
            {"base": base, "size": len(chunk)}
            for base, chunk in data_chunks
        ],
    }

    # Write file
    fpath = snapshot_path(name)
    header_json = json.dumps(header, indent=2).encode("utf-8")

    with open(fpath, "wb") as f:
        f.write(header_json)
        f.write(b'\x00')  # null terminator separates header from data
        for base, chunk in data_chunks:
            f.write(chunk)

    file_size = os.path.getsize(fpath)
    print(f"\n[+] Saved: {fpath}")
    print(f"    File size: {file_size:,} bytes ({file_size / 1024 / 1024:.1f} MB)")

    kernel32.CloseHandle(h)


def cmd_restore(name):
    # Load snapshot
    fpath = snapshot_path(name)
    if not os.path.exists(fpath):
        # Try as full path
        if os.path.exists(name):
            fpath = name
        else:
            print(f"ERROR: Snapshot not found: {fpath}")
            sys.exit(1)

    print(f"[*] Loading snapshot: {fpath}")
    with open(fpath, "rb") as f:
        raw = f.read()

    # Split header and data
    null_pos = raw.index(b'\x00')
    header = json.loads(raw[:null_pos].decode("utf-8"))
    data_blob = raw[null_pos + 1:]

    print(f"    Name: {header['name']}")
    print(f"    Saved: {header['timestamp']}")
    print(f"    PID at save time: {header['pid']}")
    print(f"    Regions: {header['region_count']}")
    print(f"    Total data: {header['total_bytes']:,} bytes")
    print(f"    EJBO objects: {header['ejbo_count']}")

    # Connect to game
    pid, proc_name = find_pid()
    if not pid:
        print("ERROR: CosmicSupremacy not found. Launch the game first.")
        sys.exit(1)

    if pid != header["pid"]:
        print(f"\n*** WARNING: Current PID ({pid}) differs from snapshot PID ({header['pid']}) ***")
        print("*** Heap addresses may have changed — restore may corrupt game state! ***")
        resp = input("Continue anyway? (yes/no): ").strip().lower()
        if resp != "yes":
            print("Aborted.")
            sys.exit(0)

    access = PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION
    h = kernel32.OpenProcess(access, False, pid)
    if not h:
        print(f"ERROR: Could not open PID {pid}")
        sys.exit(1)

    print(f"[+] Attached to {proc_name} (PID {pid})")

    # Write regions back
    print("[*] Restoring memory...")
    offset = 0
    restored = 0
    failed = 0
    for region in header["regions"]:
        base = region["base"]
        size = region["size"]
        chunk = data_blob[offset:offset + size]
        offset += size

        if len(chunk) != size:
            print(f"    ERROR: Data truncated for region 0x{base:08X}")
            failed += 1
            continue

        ok = write_bytes(h, base, chunk)
        if ok:
            restored += 1
        else:
            # Try writing in smaller blocks — some regions may have
            # changed protection since the snapshot
            block_size = 4096
            partial_ok = 0
            partial_fail = 0
            for i in range(0, size, block_size):
                block = chunk[i:i + block_size]
                if write_bytes(h, base + i, block):
                    partial_ok += 1
                else:
                    partial_fail += 1
            if partial_fail == 0:
                restored += 1
            else:
                total_blocks = partial_ok + partial_fail
                print(f"    PARTIAL: 0x{base:08X} ({size:,} bytes) — "
                      f"{partial_ok}/{total_blocks} blocks written")
                failed += 1

    print(f"\n[+] Restore complete:")
    print(f"    Regions restored: {restored}/{header['region_count']}")
    if failed:
        print(f"    Regions with errors: {failed}")

    # Read back signals to confirm
    signals = read_signal_values(h)
    if "turn_countdown" in signals:
        tc = signals["turn_countdown"]
        print(f"    Turn countdown now: {tc}s ({tc//60}m {tc%60}s)")
    if "timer_display" in signals:
        print(f"    Timer display now: {signals['timer_display']}")

    kernel32.CloseHandle(h)


def cmd_list():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    snap_dir = os.path.join(script_dir, "snapshots")
    if not os.path.exists(snap_dir):
        print("No snapshots directory found.")
        return

    files = sorted(f for f in os.listdir(snap_dir) if f.endswith(".bin"))
    if not files:
        print("No snapshots found.")
        return

    print(f"{'Name':<35} {'Date':<22} {'Size':>10} {'EJBO':>6} {'Regions':>8}")
    print("-" * 85)

    for fname in files:
        fpath = os.path.join(snap_dir, fname)
        try:
            with open(fpath, "rb") as f:
                raw = f.read(8192)  # just read enough for header
                null_pos = raw.index(b'\x00')
                header = json.loads(raw[:null_pos].decode("utf-8"))

            size = os.path.getsize(fpath)
            name = header.get("name", fname)
            ts = header.get("timestamp", "?")[:19]
            ejbo = header.get("ejbo_count", "?")
            regions = header.get("region_count", "?")
            size_str = f"{size / 1024 / 1024:.1f} MB"
            print(f"{name:<35} {ts:<22} {size_str:>10} {ejbo:>6} {regions:>8}")
        except Exception as e:
            print(f"{fname:<35} ERROR: {e}")


def cmd_info(name):
    fpath = snapshot_path(name)
    if not os.path.exists(fpath):
        if os.path.exists(name):
            fpath = name
        else:
            print(f"ERROR: Snapshot not found: {fpath}")
            sys.exit(1)

    with open(fpath, "rb") as f:
        raw = f.read(65536)  # header should be well within this
        null_pos = raw.index(b'\x00')
        header = json.loads(raw[:null_pos].decode("utf-8"))

    print(json.dumps(header, indent=2))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "save":
        name = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_save(name)
    elif cmd == "restore":
        if len(sys.argv) < 3:
            print("Usage: python snapshot.py restore <name>")
            sys.exit(1)
        cmd_restore(sys.argv[2])
    elif cmd == "list":
        cmd_list()
    elif cmd == "info":
        if len(sys.argv) < 3:
            print("Usage: python snapshot.py info <name>")
            sys.exit(1)
        cmd_info(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}")
        print("Commands: save, restore, list, info")
        sys.exit(1)


if __name__ == "__main__":
    main()
