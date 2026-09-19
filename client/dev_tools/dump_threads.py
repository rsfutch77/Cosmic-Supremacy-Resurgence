"""
dump_threads.py , where every thread was when the client wrote a dump
=====================================================================
    python dump_threads.py <file.dmp> [<file.dmp> ...]
    python dump_threads.py --common <dir>      # addresses shared by all dumps

`read_minidump.py` answers "what faulted" from the exception stream. The dumps
that matter here have **no exception stream**: the client writes them
deliberately when it decides a galaxy is unusable, so there is no faulting
instruction to report and the useful question is instead *where was the code
when it gave up*.

Reads the thread list (stream 3) and each thread's `CONTEXT`, and reports `EIP`
against the module it falls in. On x86 `CONTEXT.Eip` sits at `+0xB8`:

    +0x00  ContextFlags
    +0x04  Dr0 Dr1 Dr2 Dr3 Dr6 Dr7
    +0x1C  FLOATING_SAVE_AREA, 112 bytes
    +0x8C  SegGs SegFs SegEs SegDs
    +0x9C  Edi Esi Ebx Edx Ecx Eax
    +0xB4  Ebp
    +0xB8  Eip

With several dumps from the same failure, `--common` prints the addresses that
appear in all of them, which is what separates the bail path from seventeen
threads sitting in the usual waits.
"""
import argparse
import glob
import os
import struct
import sys

STREAM_THREADS, STREAM_MODULES = 3, 4
THREAD_ENTRY = 48
EIP_OFF = 0xB8


def u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def u64(b, o):
    return struct.unpack_from('<Q', b, o)[0]


def utf16(b, o):
    n = u32(b, o)
    return b[o + 4:o + 4 + n].decode('utf-16-le', 'replace')


def streams(data):
    if data[:4] != b'MDMP':
        raise SystemExit('not a minidump')
    count, rva = u32(data, 0x08), u32(data, 0x0C)
    out = {}
    for i in range(count):
        e = rva + i * 12
        out[u32(data, e)] = (u32(data, e + 4), u32(data, e + 8))
    return out


def modules(data, st):
    if STREAM_MODULES not in st:
        return []
    _size, rva = st[STREAM_MODULES]
    n = u32(data, rva)
    out = []
    for i in range(n):
        e = rva + 4 + i * 108
        base = u64(data, e)
        size = u32(data, e + 8)
        # MINIDUMP_MODULE: BaseOfImage u64, SizeOfImage u32, CheckSum u32,
        # TimeDateStamp u32, ModuleNameRva at +0x14.
        name = os.path.basename(utf16(data, u32(data, e + 0x14)))
        out.append((base, size, name))
    return out


def owner(mods, addr):
    for base, size, name in mods:
        if base <= addr < base + size:
            return name, addr - base
    return None, None


def thread_eips(path):
    data = open(path, 'rb').read()
    st = streams(data)
    mods = modules(data, st)
    if STREAM_THREADS not in st:
        return []
    _size, rva = st[STREAM_THREADS]
    n = u32(data, rva)
    out = []
    for i in range(n):
        e = rva + 4 + i * THREAD_ENTRY
        tid = u32(data, e)
        ctx_size, ctx_rva = u32(data, e + 0x28), u32(data, e + 0x2C)
        if ctx_size < EIP_OFF + 4:
            continue
        eip = u32(data, ctx_rva + EIP_OFF)
        mod, off = owner(mods, eip)
        out.append((tid, eip, mod, off))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='*')
    ap.add_argument('--common', help='directory of dumps to intersect')
    ap.add_argument('--module', default='CosmicSupremacy',
                    help='only report addresses inside this module')
    a = ap.parse_args()

    paths = list(a.files)
    if a.common:
        paths += sorted(glob.glob(os.path.join(a.common, '*.dmp')))
    if not paths:
        raise SystemExit('no dumps given')

    per_dump = {}
    for p in paths:
        try:
            rows = thread_eips(p)
        except Exception as exc:                            # noqa: BLE001
            print(f'{os.path.basename(p)}: unreadable, {exc}')
            continue
        keep = {(mod, off) for _tid, _eip, mod, off in rows
                if mod and a.module.lower() in mod.lower()}
        per_dump[p] = keep
        if not a.common:
            print(f'\n{os.path.basename(p)}: {len(rows)} thread(s)')
            for tid, eip, mod, off in rows:
                where = f'{mod}+{off:#x}' if mod else 'outside any module'
                print(f'  tid {tid:<6} eip {eip:#010x}  {where}')

    if a.common and per_dump:
        sets = list(per_dump.values())
        common = set.intersection(*sets) if sets else set()
        print(f'\n{len(per_dump)} dump(s); {len(common)} address(es) in '
              f'{a.module} common to all:')
        for mod, off in sorted(common, key=lambda r: r[1]):
            print(f'  {mod}+{off:#x}')
        only_some = set.union(*sets) - common if sets else set()
        print(f'\n{len(only_some)} address(es) in some but not all:')
        for mod, off in sorted(only_some, key=lambda r: r[1])[:20]:
            n = sum(1 for s in sets if (mod, off) in s)
            print(f'  {mod}+{off:#x}   in {n}/{len(sets)}')


if __name__ == '__main__':
    main()
