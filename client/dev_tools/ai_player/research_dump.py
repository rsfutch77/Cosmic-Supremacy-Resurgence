"""
research_dump.py — read the technology table out of the running client
======================================================================
    python research_dump.py              # human-readable dump of all 80
    python research_dump.py --emit       # Python literals for research.py
    python research_dump.py --diff       # compare live against research.py

Why this exists: research.py's table was extracted STATICALLY from the copy at
0x00857F10, which the engine uses when the content version at [0x0080AA00] is
>= 688. This client reads 565 and uses 0x0085D410. Rather than guess whether the
two agree, read the one the client is actually using.

The whole table is plain memory. `0x0054AA70` maps an id to its record with
`record = [0x00857F08] + id * 0x110`, and [0x857F08] is written by the table
builder at startup — so the pointer read gives the live table for free, and the
records are read directly. No engine call and no remote thread are needed, which
makes this the safest possible way to get the data (see STRATEGY.md 7).

── Record layout (0x110 bytes) ────────────────────────────────────────────────
    +0x00  int   id (== the array index)
    +0x04  int   flags; ids 73-76 carry 1 and are empty stubs, doctrines carry 2
    +0x08  float costFactor
    +0x10  std::string name          <- NOT +0x0C
    +0x50  std::string description   <- NOT +0x4C
    +0x6C  int countA, +0x70..0x7C  prerequisite list A (4 slots)
    +0x80  int countB, +0x84..0x90  prerequisite list B (4 slots)
    +0x94  int effectCount, +0x98.. effect[8], 12 bytes {kind, param, value}
    +0xF8  int exclusionCount, +0xFC.. mutually exclusive ids (4 slots)

The static analysis put the two strings at +0x0C and +0x4C. Live, both are four
bytes further on: reading at +0x0C yields four leading NULs followed by the text
and then the length, which is the signature of starting one field early. Every
other offset in the layout reproduced exactly, so this is a correction to two
fields, not to the layout.

Prerequisites are satisfied by ANY one entry (lists A and B concatenated).
"""
import struct
import sys

import cli
import gamestate as gs
import research

TABLE_PTR   = 0x00857F08     # written by the startup table builder
CONTENT_VER = 0x0080AA00     # selects which of six tables that builder fills
RECORD_SIZE = 0x110
COUNT       = 80

OFF_ID, OFF_FLAGS, OFF_FACTOR = 0x00, 0x04, 0x08
OFF_NAME, OFF_DESC = 0x10, 0x50
OFF_CA, OFF_PA, OFF_CB, OFF_PB = 0x6C, 0x70, 0x80, 0x84
OFF_NEFF, OFF_EFF = 0x94, 0x98
OFF_NEXCL, OFF_EXCL = 0xF8, 0xFC


def read_string(snap, addr):
    """MSVC std::string: 16-byte buffer or pointer, _Mysize +0x10, _Myres +0x14."""
    raw = snap.read(addr, 0x18)
    if not raw or len(raw) < 0x18:
        return ""
    size, res = struct.unpack_from("<II", raw, 0x10)
    if size > 4096:
        return ""
    if res > 15:                       # heap-allocated, buffer holds the pointer
        ptr = struct.unpack_from("<I", raw, 0)[0]
        return (snap.read(ptr, size) or b"").decode("latin-1")
    return raw[:size].decode("latin-1")


def read_table(snap):
    base = struct.unpack("<I", snap.read(TABLE_PTR, 4))[0]
    out = {}
    for i in range(COUNT):
        rec = base + i * RECORD_SIZE
        d = snap.read(rec, RECORD_SIZE)
        if not d or len(d) < RECORD_SIZE:
            continue
        ca = struct.unpack_from("<i", d, OFF_CA)[0]
        cb = struct.unpack_from("<i", d, OFF_CB)[0]
        pa = struct.unpack_from("<4i", d, OFF_PA)
        pb = struct.unpack_from("<4i", d, OFF_PB)
        ne = struct.unpack_from("<i", d, OFF_NEFF)[0]
        nx = struct.unpack_from("<i", d, OFF_NEXCL)[0]
        out[i] = {
            "id": struct.unpack_from("<i", d, OFF_ID)[0],
            "flags": struct.unpack_from("<i", d, OFF_FLAGS)[0],
            "factor": round(struct.unpack_from("<f", d, OFF_FACTOR)[0], 4),
            "name": read_string(snap, rec + OFF_NAME),
            "desc": read_string(snap, rec + OFF_DESC),
            "prereq": tuple(pa[:max(ca, 0)]) + tuple(pb[:max(cb, 0)]),
            "effects": tuple(struct.unpack_from("<3i", d, OFF_EFF + 12 * k)
                             for k in range(max(0, min(ne, 8)))),
            "excludes": tuple(struct.unpack_from("<4i", d, OFF_EXCL)[:max(nx, 0)]),
        }
    return base, out


def tree_of(name):
    """The UI presents three trees. The split is derivable from the names."""
    if name.startswith("Doctrine:"):
        return "doctrine"
    if name.startswith("Planetary Defense"):
        return "planetary defense"
    return "technology"


def main():
    args = sys.argv[1:]
    snap = gs.Snapshot()
    ver = struct.unpack("<i", snap.read(CONTENT_VER, 4))[0]
    base, tbl = read_table(snap)
    print(f"content version {ver}, live table 0x{base:08X}, {len(tbl)} records")

    if cli.flag(args, "--diff"):
        fields = 0
        for i, rec in sorted(tbl.items()):
            s = research.TECHS.get(i)
            if s is None:
                if rec["name"]:
                    print(f"  id {i}: live {rec['name']!r}, absent from research.py")
                continue
            for label, static, livev in (
                    ("name", s[0], rec["name"]),
                    ("factor", s[1], rec["factor"]),
                    ("prereq", tuple(s[2]), rec["prereq"]),
                    ("excludes", tuple(s[3]), rec["excludes"]),
                    ("grants", tuple(research.grants(i)), rec["effects"])):
                if static != livev:
                    print(f"  id {i:2d} {label:9s} static {static} != live {livev}")
                    fields += 1
        print(f"\n{fields} differing field(s)")
        return

    if cli.flag(args, "--emit"):
        print("\nTECHS = {")
        for i, rec in sorted(tbl.items()):
            if not rec["name"]:
                continue
            print(f'    {i}: ({rec["name"]!r}, {rec["factor"]}, '
                  f'{rec["prereq"]}, {rec["excludes"]}),')
        print("}\n\nGRANTS = {")
        for i, rec in sorted(tbl.items()):
            if rec["effects"]:
                print(f'    {i}: {rec["effects"]},')
        print("}")
        return

    by_tree = {}
    for i, rec in sorted(tbl.items()):
        if not rec["name"]:
            print(f"  id {i:2d}  <empty stub, flags {rec['flags']}>")
            continue
        by_tree.setdefault(tree_of(rec["name"]), []).append((i, rec))
    for tree, items in by_tree.items():
        print(f"\n=== {tree} ({len(items)}) ===")
        for i, rec in items:
            pre = ", ".join(str(p) for p in rec["prereq"]) or "-"
            print(f"  {i:2d} {rec['name']:26s} factor {rec['factor']:6.2f}  "
                  f"needs any of [{pre}]  -> "
                  f"{research.describe_grants(i) if i in research.GRANTS else rec['effects']}")


if __name__ == "__main__":
    main()
