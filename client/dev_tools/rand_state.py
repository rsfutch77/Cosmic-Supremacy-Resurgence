"""
rand_state.py: read and set the running client's rand() state
    python rand_state.py show              each thread's _holdrand
    python rand_state.py seed 0x12345678   write one seed into every thread's

The client is built on the Visual C++ 2005 runtime. `rand()` keeps its state,
`_holdrand`, at `+0x14` of the calling thread's runtime block (`_tiddata`,
0x214 bytes). The runtime keeps each block in a fiber-local slot (index at
`0x81C138`), whose layout inside Windows differs between builds, so a block is
found on the heap by what the client's own `_initptd` (`0x67A65F`) writes into
it instead:

    +0x00  the owning thread's id
    +0x04  -1                           (`_thandle`)
    +0x14  _holdrand, 1 when made
    +0x5C  a pointer                    (`ptmbcinfo`; the static `0x79E8D0`
                                         until the game sets its code page,
                                         then a heap copy)
    +0x70  1                            (`_ownlocale`)

A match counts only when its thread id is a thread of the client and it lies
outside the exe's image, where two globals also hold a thread id and -1.
Combat rolls `rand()` on the thread that runs the turn (`0x004DD590`); after a
fresh load that thread's block alone holds `0x2CD60A1E`, and the others hold
1. `seed` writes every block, so the turn's thread starts from the seed.
"""
import argparse
import ctypes
import struct
import sys
from ctypes import wintypes

from client_proc import find_pid
from snapshot import enum_writable_regions, read_bytes, write_bytes

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

PROCESS_ACCESS = 0x0400 | 0x0010 | 0x0020 | 0x0008  # query, read, write, op
TH32CS_SNAPTHREAD = 0x4
HOLDRAND = 0x14
MBCINFO = 0x5C
OWNLOCALE = 0x70
FRESH_LOAD_STATE = 0x2CD60A1E
IMAGE_END = 0x00C2D000      # 0x400000 + SizeOfImage, the same in all four clients
LOWEST_POINTER = 0x10000
CHUNK = 1 << 20
BLOCK = 0x214               # sizeof(_tiddata)


class THREADENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ThreadID', wintypes.DWORD),
                ('th32OwnerProcessID', wintypes.DWORD),
                ('tpBasePri', ctypes.c_long), ('tpDeltaPri', ctypes.c_long),
                ('dwFlags', wintypes.DWORD)]


def threads_of(pid):
    """The ids of every thread in a process."""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap in (0, -1, 0xFFFFFFFF):
        raise OSError('CreateToolhelp32Snapshot failed')
    out = set()
    try:
        te = THREADENTRY32()
        te.dwSize = ctypes.sizeof(te)
        ok = kernel32.Thread32First(snap, ctypes.byref(te))
        while ok:
            if te.th32OwnerProcessID == pid:
                out.add(te.th32ThreadID)
            ok = kernel32.Thread32Next(snap, ctypes.byref(te))
    finally:
        kernel32.CloseHandle(snap)
    return out


def blocks(h, tids):
    """[(thread id, address of its block, _holdrand)] for every runtime block
    of one of these threads."""
    pats = {struct.pack('<Ii', tid, -1): tid for tid in tids}
    found = {}
    for base, size in enum_writable_regions(h):
        if base + size <= IMAGE_END:
            continue
        for at in range(base, base + size, CHUNK):
            # Each read runs one block past the chunk, so a block across the
            # boundary is whole in one of them.
            data = read_bytes(h, at, min(CHUNK + BLOCK, base + size - at))
            if not data:
                continue
            for pat, tid in pats.items():
                i = data.find(pat)
                while i >= 0:
                    if (at + i >= IMAGE_END and i % 4 == 0
                            and i + BLOCK <= len(data)):
                        state, mbc, own = (
                            struct.unpack_from('<I', data, i + HOLDRAND)[0],
                            struct.unpack_from('<I', data, i + MBCINFO)[0],
                            struct.unpack_from('<I', data, i + OWNLOCALE)[0])
                        if mbc >= LOWEST_POINTER and own == 1:
                            found[at + i] = (tid, at + i, state)
                    i = data.find(pat, i + 4)
    return sorted(found.values())


def _open(pid):
    h = kernel32.OpenProcess(PROCESS_ACCESS, False, pid)
    if not h:
        raise OSError(f'cannot open the client, pid {pid}: '
                      f'{ctypes.get_last_error()}')
    return h


def show(pid=None):
    pid = pid or find_pid()[0]
    if not pid:
        raise SystemExit('no client is running')
    h = _open(pid)
    try:
        return blocks(h, threads_of(pid))
    finally:
        kernel32.CloseHandle(h)


def seed(value, pid=None):
    """Write `value` as every client thread's `_holdrand`. Returns the blocks
    as they read after the write; empty when none was found, and nothing is
    written then."""
    pid = pid or find_pid()[0]
    if not pid:
        raise SystemExit('no client is running')
    h = _open(pid)
    try:
        found = blocks(h, threads_of(pid))
        for _tid, addr, _state in found:
            if not write_bytes(h, addr + HOLDRAND,
                               struct.pack('<I', value & 0xFFFFFFFF)):
                raise OSError(f'could not write the rand state at {addr:#x}')
        return blocks(h, threads_of(pid)) if found else []
    finally:
        kernel32.CloseHandle(h)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('show')
    s = sub.add_parser('seed')
    s.add_argument('value', type=lambda v: int(v, 0))
    a = ap.parse_args(argv)
    got = show() if a.cmd == 'show' else seed(a.value)
    if not got:
        print('no rand state found')
        return 1
    for tid, addr, state in got:
        mark = '  (a fresh load)' if state == FRESH_LOAD_STATE else ''
        print(f'thread {tid:6}  block {addr:#010x}  _holdrand {state:#010x}{mark}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
