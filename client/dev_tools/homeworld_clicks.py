"""
homeworld_clicks.py , read, save and restore the homeworld customisation record
================================================================================
The start-of-game popup distributes an allowance (GSET `homeworld_changes`,
default 30) across four options. What the engine stores is the click counts,
four int32 in the uninitialised tail of `.data`:

    0x00842AE4   space       clicks
    0x00842AE8   food        clicks
    0x00842AEC   production  clicks
    0x00842AF0   science     clicks

Everything the UI shows is derived from those counts plus the base table
`[300, 32, 30, 40]`:

    space              = 300 + 50 + 5 x spaceClicks
    food per farmer    = 32 + foodClicks
    production/worker  = 30 + productionClicks
    science/scientist  = 40 + scienceClicks

Only the space result is written to an object (`Planet:104`, high 16 bits). The
other three are recomputed from the counts, so a client that comes up with the
counts at zero produces base rates on a homeworld that was customised, even
though the save blob restored everything else about it.

A save blob serialises objects, and these are not on an object, so no blob can
carry them. They have to travel beside the blob and be written back into the
process after the galaxy loads. That is what `--restore` is for.

Usage:
    python homeworld_clicks.py                       # read and show derived values
    python homeworld_clicks.py --set 15,3,5,7        # write the four counts
    python homeworld_clicks.py --save clicks.json    # read and write to a file
    python homeworld_clicks.py --restore clicks.json # write a saved record back
    python homeworld_clicks.py --scan                # dump the words either side
"""
import argparse
import ctypes
import json
import os
import struct
import sys
from ctypes import wintypes
from client_proc import find_pid, NOT_THE_CLIENT  # one copy, see client_proc.py

PROCESS_VM_READ           = 0x0010
PROCESS_VM_WRITE          = 0x0020
PROCESS_VM_OPERATION      = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400

CLICKS_ADDR = 0x00842AE4
SLOTS       = ('space', 'food', 'production', 'science')

BASE_TABLE       = (300, 32, 30, 40)
SPACE_FLAT_BONUS = 50
SPACE_PER_CLICK  = 5

# Enough either side to show a sibling record at the same stride, which is what
# a per-civ layout would look like.
SCAN_BEFORE = 0x20
SCAN_AFTER  = 0x40

psapi    = ctypes.WinDLL('psapi', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)



def open_process(pid):
    access = (PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION |
              PROCESS_QUERY_INFORMATION)
    h = kernel32.OpenProcess(access, False, pid)
    if not h:
        sys.exit(f'ERROR: could not open PID {pid}')
    return h


def read_bytes(h, addr, size):
    buf  = (ctypes.c_ubyte * size)()
    done = ctypes.c_size_t()
    if not kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size,
                                      ctypes.byref(done)):
        sys.exit(f'ERROR: read of {size} bytes at 0x{addr:08X} failed')
    return bytes(buf)


def write_bytes(h, addr, data):
    buf  = (ctypes.c_ubyte * len(data))(*data)
    done = ctypes.c_size_t()
    if not kernel32.WriteProcessMemory(h, ctypes.c_void_p(addr), buf, len(data),
                                       ctypes.byref(done)):
        sys.exit(f'ERROR: write of {len(data)} bytes at 0x{addr:08X} failed')


def read_clicks(h):
    return list(struct.unpack('<4i', read_bytes(h, CLICKS_ADDR, 16)))


def write_clicks(h, counts):
    write_bytes(h, CLICKS_ADDR, struct.pack('<4i', *counts))


def derived(counts):
    space, food, prod, sci = counts
    return {
        'space':             BASE_TABLE[0] + SPACE_FLAT_BONUS + SPACE_PER_CLICK * space,
        'food_per_farmer':   BASE_TABLE[1] + food,
        'production_per_worker': BASE_TABLE[2] + prod,
        'science_per_scientist': BASE_TABLE[3] + sci,
    }


def show(counts):
    print(f'clicks at 0x{CLICKS_ADDR:08X}')
    for name, value in zip(SLOTS, counts):
        print(f'  {name:11s} {value}')
    print(f'  total       {sum(counts)}')
    print('derived')
    for name, value in derived(counts).items():
        print(f'  {name:23s} {value}')
    if sum(counts) == 0:
        print('\nAll four read zero. Either nothing has been spent, or this is a '
              'pushed state and the record did not travel with the blob.')


def scan(h):
    start = CLICKS_ADDR - SCAN_BEFORE
    size  = SCAN_BEFORE + 16 + SCAN_AFTER
    raw   = read_bytes(h, start, size)
    print(f'words 0x{start:08X} .. 0x{start + size:08X}')
    for off in range(0, size, 4):
        addr = start + off
        val  = struct.unpack_from('<i', raw, off)[0]
        mark = ''
        if CLICKS_ADDR <= addr < CLICKS_ADDR + 16:
            mark = f'  <- {SLOTS[(addr - CLICKS_ADDR) // 4]}'
        print(f'  0x{addr:08X}  {val:12d}  0x{val & 0xFFFFFFFF:08X}{mark}')


def parse_counts(text):
    parts = [p.strip() for p in text.split(',')]
    if len(parts) != 4:
        sys.exit('ERROR: --set wants four comma-separated counts, '
                 'space,food,production,science')
    try:
        return [int(p) for p in parts]
    except ValueError:
        sys.exit('ERROR: --set counts must be integers')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--set', metavar='S,F,P,C',
                    help='write the four counts')
    ap.add_argument('--save', metavar='PATH',
                    help='read the counts and write them to a JSON file')
    ap.add_argument('--restore', metavar='PATH',
                    help='write the counts from a JSON file back into the process')
    ap.add_argument('--scan', action='store_true',
                    help='dump the words either side of the record')
    args = ap.parse_args()

    pid, name = find_pid()
    if not pid:
        sys.exit('ERROR: CosmicSupremacy is not running. Launch it first.')
    h = open_process(pid)
    print(f'{name} pid {pid}')

    try:
        if args.scan:
            scan(h)
            return

        if args.restore:
            with open(args.restore, 'r', encoding='utf-8') as f:
                record = json.load(f)
            counts = [int(record[s]) for s in SLOTS]
            before = read_clicks(h)
            write_clicks(h, counts)
            after = read_clicks(h)
            print(f'before  {before}')
            print(f'wrote   {counts}  (from {os.path.basename(args.restore)})')
            print(f'after   {after}')
            if after != counts:
                sys.exit('ERROR: read-back does not match what was written')
            print()
            show(after)
            return

        if args.set:
            counts = parse_counts(args.set)
            before = read_clicks(h)
            write_clicks(h, counts)
            after = read_clicks(h)
            print(f'before  {before}')
            print(f'after   {after}')
            if after != counts:
                sys.exit('ERROR: read-back does not match what was written')
            print()
            show(after)
            return

        counts = read_clicks(h)
        show(counts)

        if args.save:
            record = dict(zip(SLOTS, counts))
            record['_addr'] = f'0x{CLICKS_ADDR:08X}'
            with open(args.save, 'w', encoding='utf-8') as f:
                json.dump(record, f, indent=2)
            print(f'\nwrote {args.save}')
    finally:
        kernel32.CloseHandle(h)


if __name__ == '__main__':
    main()
