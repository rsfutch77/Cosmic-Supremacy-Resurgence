"""
xrefs.py — who calls this address, and what does that caller call?
==================================================================
    python xrefs.py 0x00546EC0                 # call sites of the ShipDesign ctor
    python xrefs.py 0x00546EC0 --callees       # ...and what each caller also calls
    python xrefs.py --data 0x0080B540          # who references a .data address
    python xrefs.py 0x005480A0 --window 200

Every address pinned in this project so far was found by anchoring on something
already known — SellFacility off the treasury field, the inactivity ramp off the
turn counter — and then reading outward by hand in Ghidra. This does the
mechanical half of that from the command line: find the `call` instructions that
target an address, which is the question asked every single time.

WHAT IT IS AND IS NOT. It decodes exactly two things: the 5-byte relative call
`E8 rel32`, and 32-bit absolute operands that happen to equal the target. That
covers direct calls and most pointer loads, and it covers NOTHING dispatched
through a vftable — which is a real limitation in an MFC/C++ binary where the
interesting call is often virtual. A negative result here is therefore weak
evidence; a positive one is exact and checkable.

It also cannot tell a real instruction from four bytes of data that happen to
look like one. Treat every hit as a candidate to confirm, not a fact. The
`--callees` output exists for that: a genuine caller of a constructor almost
always calls `operator new` a few bytes earlier, and that pattern is far more
convincing than any single hit.

IMAGE MAPPING. VA - file offset = 0x400C00 in this image, the same constant
patch_turn_floor.py uses and derived the same way.
"""
import argparse
import struct
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.dirname(HERE)
DEFAULT_EXE = os.path.join(CLIENT_DIR, "CosmicSupremacy_Resurgence.exe")

VA_MINUS_FILE = 0x400C00

# Addresses this project has already pinned. Printed alongside a hit so a
# caller's other calls are readable without going back to the docs each time.
KNOWN = {
    0x0065165A: "operator new(size_t)",
    0x00546EC0: "ShipDesign default ctor",
    0x005480A0: "ShipDesign speed getter",
    0x00548CC0: "ShipDesign firepower slot 1",
    0x00548E20: "ShipDesign firepower slot 2",
    0x00548F80: "ShipDesign firepower slot 3",
    0x0054A6F0: "research GetScale (=800)",
    0x0054B140: "research GetCost",
    0x004F8580: "PlanetProperties::SellFacility",
    0x004EFFF0: "PlanetProperties::HurryProduction",
    0x00555660: "hurry cost",
    0x004E9BA0: "ShipCommand ctor",
    0x004D9DC0: "Ship::SetCommand",
    0x004E8420: "order-object ctor",
    0x00574180: "ChangeCitizenJobs",
    0x00516060: "GetDraftCost",
    0x00534D90: "GetRelationCode",
    0x00534F90: "FirstContact",
    0x00534340: "RelationFind",
    0x00535080: "IsAtWarWith",
    0x0055BD40: "declare-war writer",
    0x00538450: "inactivity ramp",
    0x00543B90: "turn stage 26 (activity latch)",
    0x004F0800: "food production",
    0x004F1580: "food/military splitter",
    0x0048B350: "SaveGame",
    0x0056D310: "serialise game state",
    0x00542850: "testbed TLS-tree init",
    0x00541240: "standard save loader",
}


def va_to_file(va):
    return va - VA_MINUS_FILE


def file_to_va(off):
    return off + VA_MINUS_FILE


def find_calls(data, target_va):
    """Offsets of `E8 rel32` instructions whose target is target_va."""
    hits = []
    end = len(data) - 5
    off = 0
    while True:
        off = data.find(b"\xe8", off, end)
        if off < 0:
            break
        rel = struct.unpack_from("<i", data, off + 1)[0]
        # The relative displacement is measured from the END of the instruction.
        if file_to_va(off + 5) + rel == target_va:
            hits.append(off)
        off += 1
    return hits


def find_absolute(data, target_va):
    """Offsets where the 4-byte little-endian value equals target_va.

    Catches `mov reg, imm32`, `push imm32` and vftable slots — the ways an
    address travels when it is not called directly.
    """
    needle = struct.pack("<I", target_va)
    hits = []
    off = 0
    while True:
        off = data.find(needle, off)
        if off < 0:
            break
        hits.append(off)
        off += 1
    return hits


# MSVC pads between functions with int3 (0xCC). A function therefore starts just
# after a run of them — which is a heuristic, not a guarantee: 0xCC also occurs
# inside data and inside some instruction encodings. It is checked against a
# plausible prologue before being believed, and the caller is expected to verify
# the result the same way every other address in this project was verified.
PROLOGUES = (
    b"\x55\x8b\xec",          # push ebp; mov ebp, esp
    b"\x53",                  # push ebx        (frame-pointer-omitted)
    b"\x56",                  # push esi
    b"\x57",                  # push edi
    b"\x83\xec",              # sub esp, imm8
    b"\x81\xec",              # sub esp, imm32
    b"\x8b\xff",              # mov edi, edi    (hot-patch pad)
    b"\x6a",                  # push imm8
    b"\xa1",                  # mov eax, [imm32]
    b"\x8b\x44\x24",          # mov eax, [esp+n]
    b"\x8b\x4c\x24",          # mov ecx, [esp+n]
)


def enclosing_function(data, off, limit=0x4000):
    """Best guess at the start of the function containing `off`.

    Walks back to the nearest int3 padding run and returns the first byte after
    it, when what follows looks like a prologue. Returns None rather than a
    guess it cannot support — a wrong function start sends the next xref query
    somewhere arbitrary, which is worse than no answer.
    """
    i = off
    stop = max(0, off - limit)
    while i > stop:
        if data[i] == 0xCC and data[i - 1] == 0xCC:
            j = i + 1
            while j < off and data[j] == 0xCC:
                j += 1
            if any(data[j:j + len(p)] == p for p in PROLOGUES):
                return j
            # Padding that is not followed by a recognised prologue: keep going
            # rather than return a start we do not believe.
        i -= 1
    return None


def callees(data, off, window):
    """Every direct call in the `window` bytes either side of `off`."""
    out = []
    lo = max(0, off - window)
    hi = min(len(data) - 5, off + window)
    i = lo
    while i < hi:
        if data[i] == 0xE8:
            rel = struct.unpack_from("<i", data, i + 1)[0]
            tgt = file_to_va(i + 5) + rel
            # Anything outside the image is a bad decode, not a call.
            if 0x00401000 <= tgt <= 0x00800000:
                out.append((i, tgt))
            i += 5
            continue
        i += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("address", nargs="?", help="target VA, e.g. 0x00546EC0")
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--data", action="store_true",
                    help="also report 32-bit absolute references")
    ap.add_argument("--callees", action="store_true",
                    help="for each hit, list the other calls around it")
    ap.add_argument("--window", type=int, default=120,
                    help="bytes either side to scan for --callees")
    ap.add_argument("--list-known", action="store_true")
    a = ap.parse_args()

    if a.list_known:
        for va, name in sorted(KNOWN.items()):
            print(f"  0x{va:08X}  {name}")
        return 0
    if not a.address:
        ap.error("an address is required (or --list-known)")

    target = int(a.address, 0)
    with open(a.exe, "rb") as fh:
        data = fh.read()
    print(f"{os.path.basename(a.exe)}  {len(data)} bytes\n"
          f"target 0x{target:08X}"
          + (f"  ({KNOWN[target]})" if target in KNOWN else ""))

    calls = find_calls(data, target)
    print(f"\ndirect calls: {len(calls)}")
    for off in calls:
        start = enclosing_function(data, off)
        where = (f"  in function 0x{file_to_va(start):08X}"
                 f" (+0x{off - start:X})" if start is not None
                 else "  [function start not resolved]")
        print(f"  file 0x{off:06X}  VA 0x{file_to_va(off):08X}{where}")
        if a.callees:
            for coff, tgt in callees(data, off, a.window):
                if coff == off:
                    continue
                mark = "  <-- " + KNOWN[tgt] if tgt in KNOWN else ""
                rel = coff - off
                print(f"        {rel:+5d}  call 0x{tgt:08X}{mark}")

    if a.data:
        abs_hits = find_absolute(data, target)
        print(f"\nabsolute 32-bit references: {len(abs_hits)}")
        for off in abs_hits[:60]:
            print(f"  file 0x{off:06X}  VA 0x{file_to_va(off):08X}")
        if len(abs_hits) > 60:
            print(f"  ... {len(abs_hits) - 60} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
