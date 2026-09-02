"""
diff_saves.py — Compare two save blobs section by section
=========================================================
Capture a save, load it into a fresh client, capture again, and diff.
Any section that differs is state the save/load round trip does not carry, and
therefore state that a server cannot push through a blob alone.

    python diff_saves.py A.b64 B.b64
    python diff_saves.py A.b64 B.b64 --show-bytes

Sections are matched by their position in the tree (tag path plus sibling
index).
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp


def walk(blob, sections, path=()):
    """Yield (path, section) for every section, path being tag/index pairs."""
    counts = {}
    for s in sections:
        tag = s.tag.decode('latin-1')
        i = counts.get(tag, 0)
        counts[tag] = i + 1
        here = path + (f"{tag}[{i}]",)
        yield here, s
        yield from walk(blob, s.children, here)


def index(blob):
    return {p: s for p, s in walk(blob, sp.parse_blob(blob))}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('a')
    ap.add_argument('b')
    ap.add_argument('--show-bytes', action='store_true',
                    help='hex-dump the differing region of each payload')
    ap.add_argument('--limit', type=int, default=40,
                    help='max differing sections to report')
    args = ap.parse_args()

    ba, bb = sp.load_any(args.a), sp.load_any(args.b)
    print(f"A {os.path.basename(args.a)}: {len(ba):,} bytes")
    print(f"B {os.path.basename(args.b)}: {len(bb):,} bytes")
    if ba == bb:
        print("\nIDENTICAL — the blob is a fixpoint across save -> load -> save")
        return 0

    ia, ib = index(ba), index(bb)
    only_a = sorted(set(ia) - set(ib))
    only_b = sorted(set(ib) - set(ia))
    common = sorted(set(ia) & set(ib))
    print(f"\n{len(ia)} sections in A, {len(ib)} in B, {len(common)} matched by path")
    if only_a:
        print(f"  only in A ({len(only_a)}): {only_a[:10]}")
    if only_b:
        print(f"  only in B ({len(only_b)}): {only_b[:10]}")

    diffs = []
    for p in common:
        sa, sb = ia[p], ib[p]
        pa = ba[sa.payload:sa.end]
        pb = bb[sb.payload:sb.end]
        # a container's own bytes are what matters; children are compared
        # separately under their own paths
        if sa.children or sb.children:
            pa = pa[:sa.children[0].start - sa.payload] if sa.children else pa
            pb = pb[:sb.children[0].start - sb.payload] if sb.children else pb
        if sa.version != sb.version or pa != pb:
            diffs.append((p, sa, sb, pa, pb))

    print(f"\n{len(diffs)} sections differ")
    for p, sa, sb, pa, pb in diffs[:args.limit]:
        loc = ' > '.join(p)
        print(f"\n  {loc}")
        print(f"    A v{sa.version} size={sa.size} own={len(pa)}   "
              f"B v{sb.version} size={sb.size} own={len(pb)}")
        if len(pa) == len(pb):
            off = [i for i in range(len(pa)) if pa[i] != pb[i]]
            print(f"    {len(off)} of {len(pa)} own bytes differ, "
                  f"first at +{off[0] if off else '-'}")
            for i in off[:8]:
                # show the dword containing the difference, both ways
                base = i - (i % 4)
                da = pa[base:base + 4]
                db = pb[base:base + 4]
                if len(da) == 4 and len(db) == 4:
                    ua = struct.unpack('<I', da)[0]
                    ub = struct.unpack('<I', db)[0]
                    fa = struct.unpack('<f', da)[0]
                    fb = struct.unpack('<f', db)[0]
                    print(f"      +{base:<4d} A {da.hex(' ')} = {ua:<12d} "
                          f"({fa:.5g})   B {db.hex(' ')} = {ub:<12d} ({fb:.5g})")
        if args.show_bytes:
            print(f"    A: {pa[:64].hex(' ')}")
            print(f"    B: {pb[:64].hex(' ')}")

    if len(diffs) > args.limit:
        print(f"\n  ... {len(diffs) - args.limit} more not shown "
              f"(raise --limit to see them)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
