"""
set_population.py — Set a planet's population, growing past capacity if needed
==============================================================================
Population is a std::vector of 16-byte citizen records at Planet:144, with
begin/end/cap at +144/+148/+152 and the current count mirrored in the low half of
Planet:352.

Three cases, in increasing risk:

  reduce            shrink `end`. Capacity is retained, nothing leaks.
  grow in capacity  write records into spare capacity, advance `end`.
  grow beyond       VirtualAllocEx a new buffer, copy, repoint begin/end/cap.

The third case is made safe by sizing the new buffer to the planet's HARD MAXIMUM
population (Planet:104 high half / 10). Population cannot legally exceed that, so
the engine never needs to grow the vector, never reallocates, and therefore never
calls its own free() on our foreign pointer. The planet's original buffer leaks —
a leak, not a crash.

    python set_population.py --list
    python set_population.py --planet 41 --pop 35
    python set_population.py --planet 41 --pop 35 --job 0

Job ids: 0 farmer, 1 worker, 2 scientist, 5 miner, 6 banker.

VERIFIED against the UI: growing a homeworld from 18 to 35 through the
VirtualAllocEx path showed 35 in game. The panel does NOT repaint on its own —
alt-tab away and back (or otherwise force a redraw) before reading the UI, or the
old figure stays on screen. Memory is correct immediately; only the display lags.

The planet's original buffer leaks, one leak per beyond-capacity write. Harmless
within a session.
"""
import ctypes
import struct
import sys
import collections

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import ejbo_viewer as ev

MEM_COMMIT_RESERVE = 0x3000
PAGE_READWRITE     = 0x04
ev.kernel32.VirtualAllocEx.restype = ctypes.c_void_p
ev.kernel32.VirtualAllocEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.c_ulong,
                                       ctypes.c_ulong]
JOBS = {0: "farmer", 1: "worker", 2: "scientist", 5: "miner", 6: "banker"}


def main():
    args = sys.argv[1:]
    def opt(name):
        return int(args[args.index(name) + 1]) if name in args else None
    want_planet, want_pop, want_job = opt("--planet"), opt("--pop"), opt("--job")

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
    turn = struct.unpack("<I", ev.read_bytes(h, 0x008578E8, 4))[0]

    def rd32(a):
        x = ev.read_bytes(h, a, 4)
        return struct.unpack("<I", x)[0] if x and len(x) == 4 else None

    def g(p, o):
        return p["fields"][pidx[o]]["u32"]

    def owner_of(p):
        v = g(p, 40)
        t = rd32(v) if 0x10000 < v < 0x7FFF0000 else None
        return owner_starts.get(t)

    def pname(p):
        n = p["fields"][pidx[68]]["i32"]
        if not (0 < n <= 16):
            return ""
        b = b"".join(bytes.fromhex(p["fields"][pidx[k]]["raw"])
                     for k in range(52, 68, 4))
        return b[:n].decode("latin-1", "replace")

    owned = [p for p in planets if owner_of(p) is not None]
    if want_planet is None or want_pop is None:
        print(f"turn {turn}\n")
        for p in owned:
            b, e, c = g(p, 144), g(p, 148), g(p, 152)
            size, cap = (e - b) // 16, (c - b) // 16
            mx = (g(p, 104) >> 16) // 10
            jobs = collections.Counter()
            raw = ev.read_bytes(h, b, e - b) if e > b else b""
            for i in range(0, len(raw) - 15, 16):
                jobs[struct.unpack_from("<I", raw, i)[0]] += 1
            js = ", ".join(f"{JOBS.get(k, k)}={v}" for k, v in sorted(jobs.items()))
            print(f"  Planet #{p['id']:<5} {pname(p)!r:16} owner={owner_of(p)} "
                  f"pop={size:<4} cap={cap:<4} max={mx:<4}  {js}")
        print("\nusage: --planet <id> --pop <n> [--job <id>]")
        ev.kernel32.CloseHandle(h)
        return

    p = next((x for x in owned if x["id"] == want_planet), None)
    if p is None:
        sys.exit(f"no colonised planet with id {want_planet}")
    b, e, c = g(p, 144), g(p, 148), g(p, 152)
    size, cap = (e - b) // 16, (c - b) // 16
    mx = (g(p, 104) >> 16) // 10
    print(f"Planet #{p['id']} {pname(p)!r}: pop={size} cap={cap} max={mx}")
    if want_pop > mx:
        sys.exit(f"refusing: {want_pop} exceeds the planet's maximum of {mx} "
                 f"(space {g(p,104) >> 16} / 10)")
    if want_pop == size:
        print("already at that population")
        ev.kernel32.CloseHandle(h)
        return

    raw = bytearray(ev.read_bytes(h, b, max(e - b, 16)) or b"")
    if len(raw) < 16:
        sys.exit("cannot read a template citizen record; planet has no population")
    template = bytearray(raw[-16:])
    if want_job is not None:
        struct.pack_into("<I", template, 0, want_job)
    struct.pack_into("<H", template, 12, turn & 0xFFFF)

    if want_pop < size:
        new_end = b + want_pop * 16
        ev.write_bytes(h, p["addr"] + 148, struct.pack("<I", new_end))
        print(f"  shrank end to 0x{new_end:08X} ({want_pop} citizens); "
              f"capacity untouched")
    elif want_pop <= cap:
        for k in range(size, want_pop):
            ev.write_bytes(h, b + k * 16, bytes(template))
        new_end = b + want_pop * 16
        ev.write_bytes(h, p["addr"] + 148, struct.pack("<I", new_end))
        print(f"  wrote {want_pop - size} citizens into spare capacity, "
              f"end -> 0x{new_end:08X}")
    else:
        need = mx * 16
        buf = ev.kernel32.VirtualAllocEx(h, None, need,
                                         MEM_COMMIT_RESERVE, PAGE_READWRITE)
        if not buf:
            sys.exit(f"VirtualAllocEx failed (error {ctypes.get_last_error()})")
        buf = int(buf)
        print(f"  allocated {need} bytes in-process at 0x{buf:08X} "
              f"(sized to the planet maximum of {mx}, so the engine can never "
              f"need to grow it)")
        payload = bytearray(raw[:size * 16])
        payload += bytes(template) * (want_pop - size)
        ev.write_bytes(h, buf, bytes(payload))
        ev.write_bytes(h, p["addr"] + 144, struct.pack("<I", buf))
        ev.write_bytes(h, p["addr"] + 148, struct.pack("<I", buf + want_pop * 16))
        ev.write_bytes(h, p["addr"] + 152, struct.pack("<I", buf + need))
        print(f"  repointed begin/end/cap; the original buffer at 0x{b:08X} leaks")

    old352 = g(p, 352)
    new352 = (old352 & 0xFFFF0000) | (want_pop & 0xFFFF)
    ev.write_bytes(h, p["addr"] + 352, struct.pack("<I", new352))
    st.refresh()
    p2 = next(x for x in st.objects if x["type"] == "Planet" and x["id"] == p["id"])
    nb, ne, nc = (p2["fields"][pidx[o]]["u32"] for o in (144, 148, 152))
    print(f"  read back: pop={(ne-nb)//16}  capacity={(nc-nb)//16}  "
          f"Planet:352 lo={p2['fields'][pidx[352]]['u32'] & 0xFFFF}")
    ev.kernel32.CloseHandle(h)


if __name__ == "__main__":
    main()
