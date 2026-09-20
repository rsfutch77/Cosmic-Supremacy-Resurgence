"""
rtti_static.py , the class model, read off the disk
===================================================
    python rtti_static.py                      # the CLASS_FRONT table, derived
    python rtti_static.py --check              # compare it to ejbo_viewer's
    python rtti_static.py --all                # every polymorphic class
    python rtti_static.py --class Owner        # one class in detail
    python rtti_static.py --sizes              # allocation size per class
    python rtti_static.py --exe <path>         # any build; they share RTTI

`ejbo_viewer.resolve_class_name` walks RTTI in the RUNNING process, one vftable
at a time, and answers only "what is this object". That is the right tool when
an address is in hand, and it cannot answer the questions that come before a
galaxy is loaded: what classes exist, which of them carry more than one vftable,
and how far in front of the EJBO tag each one's allocation really starts.

MSVC emits enough to answer all three on disk. Every polymorphic class has a
Complete Object Locator per vftable, carrying that vftable's offset within the
complete object and a pointer to the class hierarchy descriptor, which lists
every base and its displacement. Nothing here needs the game running.

The payoff is CLASS_FRONT. An EJBO tag sits 8 bytes into the `Object` subobject,
so a class whose `Object` base is at a nonzero displacement starts its
allocation that much further forward, and a reader working from tag-8 silently
truncates it. That is how Owner hid its civ name for as long as it did. The rule
is `CLASS_FRONT = Object.mdisp + 8`, and it reproduces both values that were
found by hand.
"""
import argparse
import collections
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.dirname(HERE)
DEFAULT_EXE = os.path.join(CLIENT_DIR, "CosmicSupremacy.exe")

# The EJBO tag's offset within the Object subobject: vftable, then object id.
OBJECT_HEADER = 8


class Image:
    """A PE read as a flat file, with VA to file-offset translation."""

    def __init__(self, path):
        self.data = open(path, "rb").read()
        d = self.data
        pe = struct.unpack_from("<I", d, 0x3C)[0]
        nsec = struct.unpack_from("<H", d, pe + 6)[0]
        optsz = struct.unpack_from("<H", d, pe + 20)[0]
        self.base = struct.unpack_from("<I", d, pe + 24 + 28)[0]
        self.sections = []
        for i in range(nsec):
            o = pe + 24 + optsz + i * 40
            name = d[o:o + 8].rstrip(b"\0").decode("latin1")
            vsz, va, rsz, ra = struct.unpack_from("<IIII", d, o + 8)
            self.sections.append((name, va, vsz, ra, rsz))
        self.lo = self.base
        self.hi = self.base + max(va + max(vsz, rsz)
                                  for _, va, vsz, _, rsz in self.sections)

    def off(self, va):
        r = va - self.base
        for _, sva, vsz, ra, rsz in self.sections:
            if sva <= r < sva + max(vsz, rsz):
                o = ra + (r - sva)
                return o if o < len(self.data) else None
        return None

    def inmod(self, va):
        return self.lo < va < self.hi

    def u32(self, va):
        o = self.off(va)
        if o is None or o + 4 > len(self.data):
            return None
        return struct.unpack_from("<I", self.data, o)[0]

    def i32(self, va):
        o = self.off(va)
        if o is None or o + 4 > len(self.data):
            return None
        return struct.unpack_from("<i", self.data, o)[0]

    def cstr(self, va, limit=256):
        o = self.off(va)
        if o is None:
            return None
        raw = self.data[o:o + limit]
        end = raw.find(b"\0")
        return raw[:end if end >= 0 else limit].decode("latin1")


def demangle(mangled):
    """'.?AVPlanet@@' -> 'Planet'; template forms -> 'Base<Arg>'.

    Kept identical to ejbo_viewer._demangle so the two agree on names.
    """
    if not (mangled.startswith(".?A") and mangled.endswith("@@")):
        return None
    core = mangled[4:-2]
    if core.startswith("?$"):
        parts = [p for p in core[2:].split("@") if p]
        if len(parts) > 1:
            return f"{parts[0]}<{','.join(p.lstrip('V') for p in parts[1:])}>"
        return parts[0] if parts else None
    return core.split("@")[0] or None


class Rtti:
    """Every polymorphic class in an image, with vftables and bases."""

    def __init__(self, img):
        self.img = img
        self.types = self._type_descriptors()
        self.cols = self._locators()
        self.vftables = self._vftables()
        self.by_class = collections.defaultdict(list)
        for vft, col in self.vftables.items():
            self.by_class[col["name"]].append((vft, col))

    def _type_descriptors(self):
        """TypeDescriptor VA -> class name. The name lives at descriptor+8."""
        img, out = self.img, {}
        for name, sva, _vsz, ra, rsz in img.sections:
            if name not in (".data", ".rdata"):
                continue
            blob = img.data[ra:ra + rsz]
            start = 0
            while True:
                j = blob.find(b".?A", start)
                if j < 0:
                    break
                start = j + 1
                text = img.cstr(img.base + sva + j)
                if not text:
                    continue
                cls = demangle(text)
                if cls:
                    out[img.base + sva + j - 8] = cls
        return out

    def _locators(self):
        """Complete Object Locators, keyed by VA.

        {signature, offset, cdOffset, pTypeDescriptor, pClassHierarchyDescriptor}
        A 32-bit locator has signature 0, so the shape is confirmed by requiring
        a known type descriptor and a hierarchy descriptor that is itself well
        formed, rather than by the signature alone.
        """
        img, out = self.img, {}
        for name, sva, _vsz, ra, rsz in img.sections:
            if name != ".rdata":
                continue
            for o in range(ra, ra + rsz - 20, 4):
                sig, voff, cd, td, chd = struct.unpack_from("<IIIII", img.data, o)
                if sig != 0 or td not in self.types:
                    continue
                if not img.inmod(chd) or img.u32(chd) != 0:
                    continue
                out[img.base + sva + (o - ra)] = dict(
                    offset=voff, cd=cd, td=td, chd=chd, name=self.types[td])
        return out

    def _vftables(self):
        """A dword equal to a locator's VA; the vftable starts 4 bytes later."""
        img, out = self.img, {}
        for _name, sva, _vsz, ra, rsz in img.sections:
            for o in range(ra, ra + max(0, rsz) - 4, 4):
                v = struct.unpack_from("<I", img.data, o)[0]
                if v in self.cols:
                    out[img.base + sva + (o - ra) + 4] = self.cols[v]
        return out

    def bases(self, chd):
        """(attributes, [(name, mdisp, pdisp, vdisp), ...]) for a hierarchy.

        The array is a preorder walk of the whole base graph, the class itself
        first, so `mdisp` is where each base's subobject begins.
        """
        img = self.img
        attrs = img.u32(chd + 4)
        count = img.u32(chd + 8)
        array = img.u32(chd + 12)
        out = []
        if not count or count > 64 or not img.inmod(array):
            return attrs, out
        for i in range(count):
            p = img.u32(array + 4 * i)
            if not img.inmod(p):
                continue
            out.append((self.types.get(img.u32(p), "?"),
                        img.i32(p + 8), img.i32(p + 12), img.i32(p + 16)))
        return attrs, out

    def object_derived(self):
        """[(class, Object.mdisp, CLASS_FRONT, [vftable offsets])], the EJBO set."""
        rows = []
        for cls, entries in self.by_class.items():
            _attrs, bases = self.bases(entries[0][1]["chd"])
            obj = [m for n, m, _p, _v in bases if n == "Object"]
            if not obj:
                continue
            offs = sorted(c["offset"] for _v, c in entries)
            rows.append((cls, obj[0], obj[0] + OBJECT_HEADER, offs))
        rows.sort(key=lambda r: (-r[1], r[0]))
        return rows


def cmd_front(rtti, check):
    rows = rtti.object_derived()
    nonzero = [r for r in rows if r[1]]
    print(f"{len(rows)} classes derive from Object. "
          f"{len(nonzero)} start in front of the EJBO tag and need an entry; "
          f"the rest take the generic 8-byte header.\n")
    print(f"{'CLASS_FRONT':>11}  {'Object@':>7}  vftable offsets   class")
    for cls, mdisp, front, offs in rows:
        print(f"{front:>11}  {mdisp:>7}  {str(offs):<16}  {cls}")

    if not check:
        return 0
    try:
        sys.path.insert(0, HERE)
        import ejbo_viewer as ev
    except ImportError as exc:
        print(f"\ncannot import ejbo_viewer to compare: {exc}")
        return 1
    print("\nAgainst ejbo_viewer.CLASS_FRONT:")
    have = dict(getattr(ev, "CLASS_FRONT", {}))
    bad = 0
    for cls, mdisp, front, _offs in nonzero:
        got = have.pop(cls, None)
        if got == front:
            print(f"  ok       {cls} = {front}")
        elif got is None:
            print(f"  MISSING  {cls} should be {front}; a reader working from "
                  f"tag-8 loses its first {mdisp} bytes")
            bad += 1
        else:
            print(f"  DIFFERS  {cls} is {got}, RTTI says {front}")
            bad += 1
    for cls, got in have.items():
        print(f"  EXTRA    {cls} = {got}, but RTTI puts its Object base at 0")
        bad += 1
    return 1 if bad else 0


# ── allocation sizes ───────────────────────────────────────────────────────
# `operator new`, identified by shape rather than by name: a hotpatch prologue
# followed by the retry loop that calls the new-handler held at 0x0081A92C. It
# takes 669 call sites in this binary, far more than any other function reached
# with a small pushed immediate.
OPERATOR_NEW = 0x0065165A


def allocation_sizes(rtti, img):
    """class -> Counter of sizes passed to operator new at its allocation sites.

    Heuristic, and honest about it. For each `push <size>; call operator new`
    the next call is taken to be the constructor, and a constructor is
    recognised by its store of a known primary vftable. It therefore misses a
    class whose constructor is inlined, which is why ShipDesign and Fleet come
    back empty, and a size seen at several sites is worth more than one seen
    once.

    Where it does answer, it agrees with the live stride measurements in
    ejbo_viewer.CLASS_EXTENTS: Planet 600 against a 608 stride, Sun 96 against
    104, both of which are the allocation plus an 8-byte heap block header.
    """
    try:
        import capstone
    except ImportError:
        return None
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)

    prim = {vft: col["name"] for vft, col in rtti.vftables.items()
            if col["offset"] == 0}
    d = img.data
    _n, sva, _vsz, ra, rsz = [s for s in img.sections if s[0] == ".text"][0]
    lo, hi = ra, ra + rsz

    def va_of(o):
        return img.base + sva + (o - ra)

    def stores_vftable(fn, depth=70):
        o = img.off(fn)
        if o is None:
            return None
        for k, ins in enumerate(md.disasm(d[o:o + depth * 8], fn)):
            if k > depth:
                break
            if ins.mnemonic == "mov" and ins.op_str.startswith("dword ptr ["):
                parts = ins.op_str.split(", ")
                if len(parts) == 2 and parts[1].startswith("0x"):
                    cls = prim.get(int(parts[1], 16))
                    if cls:
                        return cls
            if ins.mnemonic == "ret":
                break
        return None

    out = collections.defaultdict(collections.Counter)
    for i in range(lo, hi - 10):
        if d[i] == 0x6A:
            size, nxt = d[i + 1], i + 2
        elif d[i] == 0x68:
            size, nxt = struct.unpack_from("<I", d, i + 1)[0], i + 5
        else:
            continue
        if d[nxt] != 0xE8:
            continue
        rel = struct.unpack_from("<i", d, nxt + 1)[0]
        if va_of(nxt) + 5 + rel != OPERATOR_NEW:
            continue
        for j in range(nxt + 5, min(nxt + 69, hi - 5)):
            if d[j] != 0xE8:
                continue
            tgt = va_of(j) + 5 + struct.unpack_from("<i", d, j + 1)[0]
            if tgt == OPERATOR_NEW:
                continue
            cls = stores_vftable(tgt)
            if cls:
                out[cls][size] += 1
                break
    return out


def cmd_sizes(rtti, img):
    sizes = allocation_sizes(rtti, img)
    if sizes is None:
        print("size derivation needs capstone, which this project does not "
              "otherwise depend on:\n    pip install capstone")
        return 1
    fronts = {cls: front for cls, _m, front, _o in rtti.object_derived()}
    print(f"{len(sizes)} classes allocated through operator new. For an "
          f"Object-derived class the window past the EJBO tag is the "
          f"allocation minus CLASS_FRONT.\n")
    print(f"{'alloc':>7}  {'sites':>5}  {'past tag':>8}  class")
    for cls in sorted(sizes, key=lambda c: c not in fronts):
        counts = sizes[cls]
        best = counts.most_common(1)[0]
        past = best[0] - fronts[cls] if cls in fronts else None
        print(f"{best[0]:>7}  {best[1]:>5}  {str(past if past else '-'):>8}  "
              f"{cls}" + (f"   also {sorted(set(counts) - {best[0]})}"
                          if len(counts) > 1 else ""))
    return 0


def cmd_all(rtti):
    print(f"{len(rtti.by_class)} polymorphic classes, "
          f"{len(rtti.vftables)} vftables\n")
    for cls in sorted(rtti.by_class):
        entries = rtti.by_class[cls]
        offs = sorted(c["offset"] for _v, c in entries)
        extra = f"  vftables at {offs}" if len(offs) > 1 else ""
        print(f"  {cls}{extra}")
    return 0


def cmd_class(rtti, name):
    entries = rtti.by_class.get(name)
    if not entries:
        near = [c for c in rtti.by_class if name.lower() in c.lower()]
        print(f"no class named {name!r}" +
              (f"; did you mean {', '.join(sorted(near)[:8])}?" if near else ""))
        return 1
    attrs, bases = rtti.bases(entries[0][1]["chd"])
    print(f"{name}   attributes={attrs}")
    for vft, col in sorted(entries, key=lambda t: t[1]["offset"]):
        print(f"    vftable 0x{vft:08X}  at offset {col['offset']} "
              f"within the complete object")
    print("    bases, in the order MSVC lists them:")
    for bname, mdisp, pdisp, vdisp in bases:
        tail = "" if pdisp == -1 else f"  pdisp={pdisp} vdisp={vdisp}"
        print(f"      {bname:<28} at {mdisp}{tail}")
    obj = [m for n, m, _p, _v in bases if n == "Object"]
    if obj:
        print(f"    EJBO tag at Object+{OBJECT_HEADER}, so "
              f"CLASS_FRONT = {obj[0] + OBJECT_HEADER}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[1])
    ap.add_argument("--exe", default=DEFAULT_EXE,
                    help="client to read (default the unpatched original; all "
                         "builds carry the same RTTI)")
    ap.add_argument("--all", action="store_true",
                    help="list every polymorphic class")
    ap.add_argument("--class", dest="cls", help="one class in detail")
    ap.add_argument("--check", action="store_true",
                    help="compare the derived table to ejbo_viewer's")
    ap.add_argument("--sizes", action="store_true",
                    help="allocation size per class, from operator new call "
                         "sites (needs capstone)")
    args = ap.parse_args()

    if not os.path.exists(args.exe):
        sys.exit(f"no such client: {args.exe}")
    rtti = Rtti(Image(args.exe))

    if args.sizes:
        return cmd_sizes(rtti, rtti.img)
    if args.cls:
        return cmd_class(rtti, args.cls)
    if args.all:
        return cmd_all(rtti)
    return cmd_front(rtti, args.check)


if __name__ == "__main__":
    sys.exit(main())
