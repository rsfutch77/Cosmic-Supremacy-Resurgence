"""
find_refs.py — who points at this object?
=========================================
    python find_refs.py --designs           # every ShipDesign, and its referrers
    python find_refs.py --addr 0x071AD690
    python find_refs.py --designs --near 64 # widen the attribution window

STRATEGY.md §3 records that a design built in memory "is never REGISTERED (zero
`tag-12` refs vs three per UI design)". That sentence is the whole blocker on
actuator I, and it describes a DATA difference — so the cheapest way to learn
what registration is, is to look at a design the engine registered itself and
find out who points at it.

This scans the client's writable memory for the object's several addresses (an
EJBO object is referred to by more than one, see below) and then tries to say
what each hit belongs to: which class, which instance, which field offset. A hit
attributed to `Owner:440` is a finding; a bare address is a lead.

THE SEVERAL ADDRESSES. An EJBO allocation is referred to at different offsets
depending on who is doing the referring: reference nodes store `tag-8`, the
known-players vector stores `tag-52` for `Owner`, and `ShipDesign` has a second
vftable at `-12` because it is multiple-inheritance — so a `ShipDesign*` held by
a `Ship` is `tag-12`. A search for only one form finds a fraction of the truth,
which is exactly how "zero refs" could be measured for an object that something
does point at. All the plausible forms are searched and reported separately.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ai_player"))

import ejbo_viewer as ev
import gamestate as gs

# Offsets from an object's EJBO tag at which other code refers to it.
REF_FORMS = {
    "tag-0": 0,
    "tag-8": -8,
    "tag-12": -12,
    "tag-52": -52,
}


def scan_regions(handle):
    """Committed, writable, non-guard regions — where object data lives."""
    return ev.enum_writable_regions(handle)


def find_dwords(handle, regions, values):
    """Every address holding one of `values`, as {value: [addresses]}."""
    want = {v: [] for v in values}
    needles = {struct.pack("<I", v): v for v in values}
    for base, size in regions:
        data = ev.read_bytes(handle, base, size)
        if not data:
            continue
        for needle, value in needles.items():
            off = 0
            while True:
                off = data.find(needle, off)
                if off < 0:
                    break
                if off % 4 == 0:            # pointers are aligned; skip stray matches
                    want[value].append(base + off)
                off += 1
    return want


# Measured extents, from the memory report's stride analysis. Attribution has to
# use the REAL size of each class: a single generous window applied to all of
# them turns every nearby heap block into a confident false claim, and "Sun#145
# at tag+724" reads exactly like a finding while being noise — a Sun is 92 bytes.
CLASS_EXTENT = {
    "Owner": 1344,
    "Planet": 596,
    "Ship": 152,
    "ShipDesign": 280,      # 0x118, the allocation size both call sites use
    "Sun": 92,
}
# How far BEFORE the tag an object's own data can start. Owner is the extreme
# case at -52; ShipDesign's second vftable sits at -12.
CLASS_FRONT = 64


def build_attribution(snap):
    """(start, end, label) for every object the snapshot knows, for lookup."""
    spans = []
    for kind, items in (("Owner", snap.civs), ("Planet", snap.planets),
                        ("Ship", snap.ships), ("ShipDesign", snap.designs),
                        ("Sun", snap.suns)):
        extent = CLASS_EXTENT[kind]
        for o in items:
            name = ""
            for attr in ("civ_name", "design_name", "name"):
                v = getattr(o, attr, None)
                if isinstance(v, str) and v:
                    name = f" {v!r}"
                    break
            spans.append((o.addr - CLASS_FRONT, o.addr + extent,
                          f"{kind}#{o.id}{name}", o.addr))
    spans.sort()
    return spans


def attribute(spans, addr):
    for lo, hi, label, base in spans:
        if lo <= addr < hi:
            return f"{label} at tag{addr - base:+d}"
    return None


def neighbourhood(handle, addr, known, span=6):
    """The dwords around `addr`, naming any that are themselves known objects.

    A referrer sitting in an anonymous heap block is not anonymous if the slots
    beside it point at the OTHER designs: that shape is a vector, and a vector of
    designs is the registration this whole exercise is looking for. Printing the
    neighbours is what tells those apart from a lone pointer in some unrelated
    struct.
    """
    base = addr - span * 4
    raw = ev.read_bytes(handle, base, span * 8 + 4)
    if not raw:
        return []
    out = []
    for i in range(0, len(raw) - 3, 4):
        val = struct.unpack_from("<I", raw, i)[0]
        at = base + i
        out.append((at, val, known.get(val), at == addr))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--designs", action="store_true")
    ap.add_argument("--addr", type=lambda s: int(s, 0))
    ap.add_argument("--max-hits", type=int, default=12)
    ap.add_argument("--context", action="store_true",
                    help="dump the dwords around each referrer, naming any that "
                         "are themselves known objects")
    a = ap.parse_args()

    snap = gs.Snapshot()
    handle = snap.state.handle
    regions = scan_regions(handle)
    total = sum(size for _b, size in regions)
    print(f"attached to pid {snap.state.pid}; {len(regions)} writable regions, "
          f"{total / 1e6:.1f} MB")
    spans = build_attribution(snap)

    # Every address by which any known object can be referred to, so a dword
    # sitting next to our hit can be named rather than stared at.
    known = {}
    for lo, hi, label, base in spans:
        for form, delta in REF_FORMS.items():
            known[base + delta] = f"-> {label} ({form})"

    targets = []
    if a.designs:
        targets = [(f"{d.design_name!r} (owner "
                    f"{d.owner.civ_name if d.owner else None!r})", d.addr)
                   for d in snap.designs]
    if a.designs and not targets:
        # Distinguish "you passed no flag" from "the galaxy is not up yet",
        # which is the same empty list and a completely different problem.
        sys.exit("--designs found no ShipDesign objects — the galaxy is "
                 "probably still loading; wait and re-run")
    if a.addr:
        targets.append((f"0x{a.addr:08X}", a.addr))
    if not targets:
        ap.error("give --designs or --addr")

    # One scan for every form of every target, rather than one scan per target:
    # the memory is tens of megabytes and reading it once per design would turn
    # a five-second answer into a minute of re-reading the same pages.
    values = {}
    for label, addr in targets:
        for form, delta in REF_FORMS.items():
            values[addr + delta] = (label, form)
    hits = find_dwords(handle, regions, list(values))

    for label, addr in targets:
        print(f"\n{label}  tag 0x{addr:08X}")
        for form, delta in REF_FORMS.items():
            got = hits.get(addr + delta, [])
            # The object's own header holds its vftables; that is not a referrer.
            external = [h for h in got if not (addr - 64 <= h < addr + 1400)]
            print(f"  {form:7} (0x{addr + delta:08X}): {len(external)} external "
                  f"reference(s)")
            for h in external[:a.max_hits]:
                who = attribute(spans, h)
                print(f"      0x{h:08X}  {who or 'unattributed'}")
                if a.context:
                    for at, val, name, is_hit in neighbourhood(handle, h, known):
                        mark = " <<<" if is_hit else ""
                        tail = f"  {name}" if name else ""
                        print(f"           {at:08X}: {val:08X}{tail}{mark}")
            if len(external) > a.max_hits:
                print(f"      ... {len(external) - a.max_hits} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
