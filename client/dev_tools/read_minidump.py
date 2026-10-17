"""
read_minidump.py: what faulted, from the game's own crash dump

    python read_minidump.py                       # newest dump beside the EXE
    python read_minidump.py <file.dmp>

Reads the minidump directly: the exception stream (type 6) for the code and
address, the module list (type 4) to name the module the address falls in, and
the thread list (type 3) for the count. No debugger or symbols needed.
"""
import argparse
import glob
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.dirname(HERE)

EXCEPTION_NAMES = {
    0xC0000005: "ACCESS_VIOLATION",
    0xC000001D: "ILLEGAL_INSTRUCTION",
    0xC0000094: "INTEGER_DIVIDE_BY_ZERO",
    0xC0000096: "PRIVILEGED_INSTRUCTION",
    0xC00000FD: "STACK_OVERFLOW",
    0x80000003: "BREAKPOINT",
}
STREAM_THREADS, STREAM_MODULES, STREAM_EXCEPTION = 3, 4, 6


def read_u32(b, off):
    return struct.unpack_from("<I", b, off)[0]


def read_u64(b, off):
    return struct.unpack_from("<Q", b, off)[0]


def utf16(b, off):
    n = read_u32(b, off)
    return b[off + 4:off + 4 + n].decode("utf-16-le", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", nargs="?")
    a = ap.parse_args()

    path = a.dump
    if not path:
        dumps = sorted(glob.glob(os.path.join(CLIENT, "*.dmp")),
                       key=os.path.getmtime)
        if not dumps:
            sys.exit("no .dmp beside the client")
        path = dumps[-1]
    data = open(path, "rb").read()
    print(f"{os.path.basename(path)}: {len(data):,} bytes")
    if data[:4] != b"MDMP":
        sys.exit("not a minidump")

    n_streams = read_u32(data, 8)
    dir_rva = read_u32(data, 12)
    streams = {}
    for i in range(n_streams):
        o = dir_rva + i * 12
        stype, size, rva = struct.unpack_from("<III", data, o)
        streams[stype] = (size, rva)

    modules = []
    if STREAM_MODULES in streams:
        _size, rva = streams[STREAM_MODULES]
        count = read_u32(data, rva)
        for i in range(count):
            o = rva + 4 + i * 108
            # MINIDUMP_MODULE is 108 bytes: BaseOfImage u64 @0, SizeOfImage
            # u32 @8, CheckSum @12, TimeDateStamp @16, ModuleNameRva @20, then
            # a 52-byte VS_FIXEDFILEINFO and two location descriptors.
            base = read_u64(data, o)
            size = read_u32(data, o + 8)
            name_rva = read_u32(data, o + 20)
            modules.append((base, size, os.path.basename(utf16(data, name_rva))))
        modules.sort()

    def owner(addr):
        for base, size, name in modules:
            if base <= addr < base + size:
                return name, addr - base
        return None, None

    if STREAM_THREADS in streams:
        _size, rva = streams[STREAM_THREADS]
        print(f"threads: {read_u32(data, rva)}")

    if STREAM_EXCEPTION not in streams:
        print("no exception stream, the dump was not written for a crash")
        return
    _size, rva = streams[STREAM_EXCEPTION]
    thread_id = read_u32(data, rva)
    code = read_u32(data, rva + 8)
    flags = read_u32(data, rva + 12)
    addr = read_u64(data, rva + 24)
    n_params = read_u32(data, rva + 32)
    params = [read_u64(data, rva + 40 + 8 * i) for i in range(min(n_params, 15))]

    name = EXCEPTION_NAMES.get(code, "")
    mod, off = owner(addr)
    print(f"\nfaulting thread : {thread_id}")
    print(f"exception       : 0x{code:08X} {name}  flags 0x{flags:X}")
    print(f"address         : 0x{addr:016X}"
          + (f"   {mod}+0x{off:X}" if mod else "   (no module)"))
    if code == 0xC0000005 and len(params) >= 2:
        kind = {0: "read", 1: "write", 8: "execute"}.get(params[0], params[0])
        print(f"                  tried to {kind} 0x{params[1]:016X}")
    print("\nmodules holding the fault address are the ones to care about; "
          "an address inside the client can be matched against the VAs this "
          "project already documents.")


if __name__ == "__main__":
    main()
