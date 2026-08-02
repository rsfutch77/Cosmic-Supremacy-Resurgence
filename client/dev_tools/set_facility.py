"""
set_facility.py — List or set facility counts on a planet
=========================================================
Each planet's built facilities live in an MSVC std::map at Planet:204. Every node
holds an embedded Facility at node+12, with the facility TYPE ID at node+16 and
its COUNT at node+20. The count is a plain int, so raising it needs no
allocation and changes no container structure — it is the one safe way to give a
planet more of something it already has.

    python set_facility.py --list
    python set_facility.py --planet 41 --type 0 --count 10

Limitation: a facility TYPE the planet does not already have cannot be added,
because that needs a new map node and external code cannot call the game's
allocator. Build one of the type first, then raise its count.

Known type ids: 0 farm, 2 shipyard, 4 university, 6 military camp,
                8 defence agency, 9 light turret
"""
import sys
import struct
import collections

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import ejbo_viewer as ev

FACILITY_VFTABLE = 0x00752BA4
NAMES = {0: "farm", 2: "shipyard", 4: "university", 6: "military camp",
         8: "defence agency", 9: "light turret"}


def walk_facilities(h, head):
    """Return [(typeId, count, countAddr)] from a facility map head node."""
    out, seen, stack = [], set(), [head]
    while stack and len(seen) < 256:
        n = stack.pop()
        if not n or n in seen or not (0x10000 < n < 0x7FFF0000):
            continue
        seen.add(n)
        blob = ev.read_bytes(h, n, 24)
        if not blob or len(blob) < 24:
            continue
        left, parent, right, vf, tid, cnt = struct.unpack_from("<IIIIII", blob, 0)
        if vf == FACILITY_VFTABLE:
            out.append((tid, cnt, n + 20))
        stack.extend((left, parent, right))
    return sorted(set(out))


def main():
    args = sys.argv[1:]
    def opt(name, cast=int):
        return cast(args[args.index(name) + 1]) if name in args else None
    want_planet, want_type, want_count = opt("--planet"), opt("--type"), opt("--count")

    st = ev.ViewerState()
    if not st.connect():
        sys.exit("ERROR: CosmicSupremacy not found. Launch the game first.")
    st.scan()
    h = st.handle
    by = collections.defaultdict(list)
    for o in st.objects:
        by[o["type"]].append(o)
    planets, owners = by["Planet"], by["Owner"]
    pidx = {f["offset"]: i for i, f in enumerate(planets[0]["fields"])}
    owner_starts = {o["addr"] - 8: o["id"] for o in owners}

    def rd32(a):
        x = ev.read_bytes(h, a, 4)
        return struct.unpack("<I", x)[0] if x and len(x) == 4 else None

    def owner_of(p):
        v = p["fields"][pidx[40]]["u32"]
        t = rd32(v) if 0x10000 < v < 0x7FFF0000 else None
        return owner_starts.get(t)

    def pname(p):
        n = p["fields"][pidx[68]]["i32"]
        if not (0 < n <= 16):
            return ""
        b = b"".join(bytes.fromhex(p["fields"][pidx[k]]["raw"])
                     for k in range(52, 68, 4))
        return b[:n].decode("latin-1", "replace")

    targets = [p for p in planets if owner_of(p) is not None]
    if want_planet is not None:
        targets = [p for p in targets if p["id"] == want_planet]
        if not targets:
            sys.exit(f"no colonised planet with id {want_planet}")

    for p in targets:
        head = p["fields"][pidx[204]]["u32"]
        facs = walk_facilities(h, head) if 0x10000 < head < 0x7FFF0000 else []
        print(f"\nPlanet #{p['id']} {pname(p)!r} owner={owner_of(p)}  "
              f"{len(facs)} facility type(s), Planet:208 reads "
              f"{p['fields'][pidx[208]]['i32']}")
        for tid, cnt, addr in facs:
            mark = ""
            if want_type is not None and tid == want_type and want_count is not None:
                ok = ev.write_bytes(h, addr, struct.pack("<I", want_count))
                now = rd32(addr)
                mark = f"   -> set to {now}" if ok else "   -> WRITE FAILED"
            print(f"  type {tid:<3} {NAMES.get(tid, '?'):<15} count={cnt:<4} "
                  f"@0x{addr:08X}{mark}")
        if want_type is not None and not any(t == want_type for t, _, _ in facs):
            print(f"  !! this planet has no facility of type {want_type}; a new type "
                  f"cannot be added without allocating a map node. Build one first.")
    ev.kernel32.CloseHandle(h)


if __name__ == "__main__":
    main()
