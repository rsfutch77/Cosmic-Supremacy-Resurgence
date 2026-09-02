"""
inject_design.py — Add a ship design to a save blob
===================================================
Duplicates an existing `DSGN` record inside a civ's `OWNR` block, renames it and
gives it a fresh object id, so the client builds the design itself on load.

    python inject_design.py ../saves/<capture>.b64 --from probeA --name aiscout \
        -o ../loadgame_blob.b64

Why this and not a memory write: creating a ShipDesign in memory produces a real,
EJBO-tagged object, but nothing registers it and nothing computes its stat block,
so it never reaches the UI and cannot be built.

FORMAT, measured against a capture containing two hand-made designs
-------------------------------------------------------------------
Every section is:

    tag(4)  (version << 26 | length26)(4)  payload[length]

A design is a `DSGN` section whose payload begins with the object id and then
carries one `SDPR` child:

    DSGN  ver 4   objectId(4)  SDPR...
    SDPR  ver 0   nameLen(4)  name[nameLen]  ...rest...

Designs are per-civ and live inside `OWNR`, and immediately BEFORE the first
`DSGN` of each civ there is a `u32` COUNT of that civ's designs.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp

# SAVE's first payload dword is the highest object id in use; the engine
# allocates the next object at this value + 1.
HIGH_WATER_ID = 8

DSGN = b"DSGN"
SDPR = b"SDPR"
OWNR = b"OWNR"


def sec_len(blob, off):
    """(version, payload length) of the section at `off`."""
    lw = struct.unpack_from("<I", blob, off + 4)[0]
    return lw >> 26, lw & 0x3FFFFFF


def sec_end(blob, off):
    return off + 8 + sec_len(blob, off)[1]


def find_all(blob, tag, limit=None):
    out, pos = [], 0
    while True:
        i = blob.find(tag, pos)
        if i < 0 or (limit is not None and i > limit):
            break
        out.append(i)
        pos = i + 1
    return out


KNOWN_TAGS = [
    b'SAVE', b'GSET', b'GLOB', b'TMGX', b'RSMA', b'NWDB', b'GLXY', b'OWNR',
    b'DATA', b'OWPR', b'KNPL', b'EXSY', b'HQAS', b'CVTR', b'SERV', b'DSGN',
    b'SDPR', b'GOVS', b'ADMS', b'SPQS', b'USSE', b'SOLA', b'SUN ', b'PLNT',
    b'PLPR', b'PROD', b'WLTH', b'ENLI', b'SHIP', b'DYNO', b'SHCO', b'SHPR',
    b'ROUT', b'NEBU', b'CZYY', b'NEWS',
]


def containing_sections(blob, at):
    """Take the candidates widest-first and keep one only if it nests
    inside everything already kept. A real ancestor chain is strictly nested by
    definition, which a coincidental tag run is very unlikely to be.
    """
    cands = []
    for tag in KNOWN_TAGS:
        for off in find_all(blob, tag):
            try:
                ver, ln = sec_len(blob, off)
            except Exception:
                continue
            end = off + 8 + ln
            if ln <= 0 or end > len(blob) or not (off < at < end):
                continue
            cands.append((end - off, off, tag, ver, ln, end))

    cands.sort(key=lambda c: (-c[0], c[1]))       # widest span first
    chain, inner = [], None
    for _span, off, tag, ver, ln, end in cands:
        if inner is None:
            # the outermost has to reach the end of the blob, give or take the
            # few bytes SAVE keeps after GLXY
            if not (end == len(blob) or len(blob) - end <= 16):
                continue
        elif not (off >= inner[0] and end <= inner[1]):
            continue                              # not nested in what we kept
        chain.append((off, tag, ver, ln))
        inner = (off, end)
    return sorted(chain)


def design_records(blob):
    """[(dsgn_offset, object_id, name)] for every DSGN in the blob."""
    out = []
    for off in find_all(blob, DSGN):
        # A DSGN must chain cleanly and carry an SDPR child, or it is a
        # coincidental byte run rather than a section.
        try:
            oid = struct.unpack_from("<I", blob, off + 8)[0]
            if blob[off + 12:off + 16] != SDPR:
                continue
            s = off + 12
            nlen = struct.unpack_from("<I", blob, s + 8)[0]
            if nlen > 64:
                continue
            name = blob[s + 12:s + 12 + nlen].decode("latin-1")
        except Exception:
            continue
        out.append((off, oid, name))
    return out


def owner_of_design(blob, dsgn_off):
    """Start offset of the OWNR section containing this DSGN."""
    owners = [o for o in find_all(blob, OWNR) if o < dsgn_off]
    return max(owners) if owners else None


def max_object_id(blob):
    """Upper bound on ids in use.

    Every section's first payload dword is treated as a candidate id and the
    largest plausible one wins. This deliberately over-approximates: it is far
    better to pick an id above everything than to collide with a planet.
    """
    # The authoritative answer is SAVE's high-water counter; the scan below
    # only cross-checks it, since a scan sees only ids it knows to look for.
    best = struct.unpack_from("<I", blob, HIGH_WATER_ID)[0]
    for tag in (b"DSGN", b"SHIP", b"PLNT", b"SUN ", b"SOLA", b"OWNR", b"GOVS",
                b"ADMS", b"DYNO"):
        for off in find_all(blob, tag):
            try:
                v = struct.unpack_from("<I", blob, off + 8)[0]
            except Exception:
                continue
            if 0 < v < 65536:
                best = max(best, v)
    return best


def inject(blob, src_name, new_name, new_id=None, log=print):
    records = design_records(blob)
    if not records:
        raise SystemExit("no DSGN records found; is this a real save blob?")
    log("designs in the blob:")
    for off, oid, name in records:
        log(f"  @{off:<6} id={oid:<6} {name!r}")

    # A name is ambiguous whenever both civs start with an identically named
    # design, which is the normal case at game start, so allow selection by the
    # object id instead.
    if isinstance(src_name, int):
        src = [r for r in records if r[1] == src_name]
        if not src:
            raise SystemExit(f"no design with object id {src_name}")
    else:
        src = [r for r in records if r[2] == src_name]
        if not src:
            raise SystemExit(f"no design named {src_name!r}")
        if len(src) > 1:
            raise SystemExit(f"{len(src)} designs named {src_name!r} (ids "
                             f"{[r[1] for r in src]}); pass --from-id to choose")
    src_off, src_id, _ = src[0]

    if any(r[2] == new_name for r in records):
        raise SystemExit(f"a design named {new_name!r} already exists; the "
                         f"client requires unique design names")
    if len(new_name) > 15:
        raise SystemExit(f"{new_name!r} is longer than the 15-char name buffer")

    owner = owner_of_design(blob, src_off)
    mine = [r for r in records if owner_of_design(blob, r[0]) == owner]
    first = min(r[0] for r in mine)
    last_end = max(sec_end(blob, r[0]) for r in mine)
    count_at = first - 4
    count = struct.unpack_from("<I", blob, count_at)[0]
    log(f"\ncloning {src_name!r} (id {src_id}) inside OWNR @{owner}")
    log(f"  that civ has {len(mine)} design(s); count field @{count_at} reads "
        f"{count}"
        + ("" if count == len(mine) else "   <-- MISMATCH, refusing"))
    if count != len(mine):
        raise SystemExit("design count does not match the records found; "
                         "the layout is not what this tool expects")

    if new_id is None:
        new_id = max_object_id(blob) + 1
    log(f"  new object id {new_id} (max seen in blob: {max_object_id(blob)})")

    # -- build the new record -------------------------------------------
    rec = bytearray(blob[src_off:sec_end(blob, src_off)])
    struct.pack_into("<I", rec, 8, new_id)                  # object id

    s = 12                                                   # SDPR child
    assert bytes(rec[s:s + 4]) == SDPR
    old_nlen = struct.unpack_from("<I", rec, s + 8)[0]
    old_name = bytes(rec[s + 12:s + 12 + old_nlen])
    nb = new_name.encode("ascii")
    delta = len(nb) - old_nlen

    rec[s + 12:s + 12 + old_nlen] = nb
    struct.pack_into("<I", rec, s + 8, len(nb))

    for at in (4, s + 4):                                    # DSGN then SDPR
        lw = struct.unpack_from("<I", rec, at)[0]
        ver, ln = lw >> 26, lw & 0x3FFFFFF
        struct.pack_into("<I", rec, at, (ver << 26) | (ln + delta))

    log(f"  name {old_name.decode()!r} -> {new_name!r} (length delta {delta:+d})")
    log(f"  record {len(blob[src_off:sec_end(blob, src_off)])} -> {len(rec)} bytes")

    # -- splice it in after the civ's last design -----------------------
    # Sections NEST, and the outer ones carry spanning lengths: OWNR's length
    # ends exactly on the next OWNR, and GLXY and SAVE enclose it in turn.
    # Growing the payload without growing every enclosing section leaves a blob
    # that still decompresses and still looks plausible, but parses wrong from
    # the insertion point on. Every ancestor has to be widened by the same delta.
    grow = len(rec)
    ancestors = containing_sections(blob, last_end)
    log(f"\n  sections enclosing offset {last_end}, all widened by {grow}:")
    out = bytearray(blob)
    for off, tag, ver, ln in ancestors:
        struct.pack_into("<I", out, off + 4, (ver << 26) | (ln + grow))
        log(f"    {tag.decode():4} @{off:<6} len {ln} -> {ln + grow}")

    out[last_end:last_end] = rec
    struct.pack_into("<I", out, count_at, count + 1)
    log(f"  inserted at {last_end}; design count {count} -> {count + 1}")

    # THE HIGH-WATER OBJECT ID. SAVE's first payload dword is the highest object
    # id in use, and the engine allocates the next object at that value plus one.
    hi = struct.unpack_from("<I", out, HIGH_WATER_ID)[0]
    if new_id > hi:
        struct.pack_into("<I", out, HIGH_WATER_ID, new_id)
        log(f"  high-water object id {hi} -> {new_id} "
            f"(next engine allocation will be {new_id + 1})")
    else:
        log(f"  high-water object id already {hi}, above the new id {new_id}")
    return bytes(out)



# ── Building a design from scratch ────────────────────────────────────────────
#     u32 nameLen ; char name[nameLen]
#     6 x ( u32 count ; u32 ids[count] )      chassis, scanners, engines,
#                                             weapons, modules, and a sixth list
#                                             that is empty on every design seen
#     u32 ownerObjectId
#
PART_LISTS = ("chassis", "scanners", "engines", "weapons", "modules", "list6")


# A scanner is defaulted in rather than left to the caller to remember. It can
# still be overridden explicitly, but the default matches what the UI does.
DEFAULT_SCANNERS = [0]


def build_sdpr(name, parts, owner_id):
    """The SDPR payload for a design. `parts` maps list name -> [ids]."""
    parts = dict(parts)
    if not parts.get("scanners"):
        parts["scanners"] = list(DEFAULT_SCANNERS)
    nb = name.encode("ascii")
    out = bytearray(struct.pack("<I", len(nb)) + nb)
    for key in PART_LISTS:
        ids = list(parts.get(key, ()))
        out += struct.pack("<I", len(ids))
        for i in ids:
            out += struct.pack("<I", i)
    out += struct.pack("<I", owner_id)
    return bytes(out)


def build_dsgn(new_id, name, parts, owner_id, dsgn_ver=4, sdpr_ver=0):
    """A complete DSGN section: header, object id, and its SDPR child.

    Section versions are taken from real records rather than assumed — DSGN is
    version 4 and SDPR version 0 in every capture examined, and the version
    occupies bits 26..31 of the length word.
    """
    payload = build_sdpr(name, parts, owner_id)
    sdpr = struct.pack("<4sI", SDPR, (sdpr_ver << 26) | len(payload)) + payload
    body = struct.pack("<I", new_id) + sdpr
    return struct.pack("<4sI", DSGN, (dsgn_ver << 26) | len(body)) + body


def make(blob, like, new_name, parts, new_id=None, log=print):
    """Add a NEW design 
    """
    records = design_records(blob)
    if not records:
        raise SystemExit("no DSGN records found; is this a real save blob?")
    if isinstance(like, int):
        ref = [r for r in records if r[1] == like]
        if not ref:
            raise SystemExit(f"no design with object id {like}")
    else:
        ref = [r for r in records if r[2] == like]
        if not ref:
            raise SystemExit(f"no design named {like!r}")
        if len(ref) > 1:
            raise SystemExit(f"{len(ref)} designs named {like!r} (ids "
                             f"{[r[1] for r in ref]}); pass --like-id to choose")
    ref_off = ref[0][0]

    if len(new_name) > 15:
        raise SystemExit(f"{new_name!r} is longer than the 15-char name buffer")

    owner = owner_of_design(blob, ref_off)
    mine = [r for r in records if owner_of_design(blob, r[0]) == owner]

    # Uniqueness is PER CIV, not galaxy-wide. This used to reject any name
    # already in the blob, which is demonstrably too strict: every galaxy starts
    # with each civ owning a design called 'Colony Ship'. The narrow rule is what
    # lets one design be given to every civ in a many-civ game under one name.
    if any(r[2] == new_name for r in mine):
        raise SystemExit(f"that civ already owns a design named {new_name!r}; "
                         f"design names must be unique within a civ")
    first = min(r[0] for r in mine)
    last_end = max(sec_end(blob, r[0]) for r in mine)
    count_at = first - 4
    count = struct.unpack_from("<I", blob, count_at)[0]
    if count != len(mine):
        raise SystemExit(f"design count {count} does not match the {len(mine)} "
                         f"records found; the layout is not what this expects")

    # The owner's OBJECT ID is the trailing dword of any of that civ's designs,
    # which is more reliable than trying to locate the OWNR's own id field.
    ref_owner_id = read_owner_id(blob, ref_off)

    if new_id is None:
        new_id = max_object_id(blob) + 1
    rec = build_dsgn(new_id, new_name, parts, ref_owner_id)

    log(f"building {new_name!r} for the civ owning design @{ref_off} "
        f"(owner object id {ref_owner_id})")
    for key in PART_LISTS:
        if parts.get(key):
            log(f"    {key}: {list(parts[key])}")
    log(f"  new object id {new_id}; record is {len(rec)} bytes")

    grow = len(rec)
    out = bytearray(blob)
    for off, tag, ver, ln in containing_sections(blob, last_end):
        struct.pack_into("<I", out, off + 4, (ver << 26) | (ln + grow))
        log(f"    widened {tag.decode():4} @{off:<6} {ln} -> {ln + grow}")
    out[last_end:last_end] = rec
    struct.pack_into("<I", out, count_at, count + 1)
    log(f"  inserted at {last_end}; design count {count} -> {count + 1}")

    hi = struct.unpack_from("<I", out, HIGH_WATER_ID)[0]
    if new_id > hi:
        struct.pack_into("<I", out, HIGH_WATER_ID, new_id)
        log(f"  high-water object id {hi} -> {new_id}")
    return bytes(out)


def read_design(blob, dsgn_off):
    """Parse one DSGN into (objectId, name, {list: [ids]}, ownerId)."""
    oid = struct.unpack_from("<I", blob, dsgn_off + 8)[0]
    sl = sec_len(blob, dsgn_off + 12)[1]     # (version, length)
    p = dsgn_off + 20
    end = p + sl
    nlen = struct.unpack_from("<I", blob, p)[0]; p += 4
    name = blob[p:p + nlen].decode("latin-1"); p += nlen
    parts = {}
    for key in PART_LISTS:
        n = struct.unpack_from("<I", blob, p)[0]; p += 4
        parts[key] = list(struct.unpack_from(f"<{n}I", blob, p)); p += 4 * n
    owner_id = struct.unpack_from("<I", blob, p)[0]; p += 4
    if p != end:
        raise ValueError(f"SDPR at {dsgn_off} left {end - p} byte(s) unread; "
                         f"the layout is not what this expects")
    return oid, name, parts, owner_id


def read_owner_id(blob, dsgn_off):
    return read_design(blob, dsgn_off)[3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", help="captured savegame .b64")
    ap.add_argument("--from", dest="src", help="name of the design to clone")
    ap.add_argument("--from-id", dest="src_id", type=int,
                    help="object id of the design to clone (unambiguous when "
                         "both civs have an identically named design)")
    ap.add_argument("--name", help="name for the new design")
    ap.add_argument("--id", type=int, default=None,
                    help="object id (default: one above the highest in the blob)")
    ap.add_argument("-o", "--out", default=None,
                    help="output .b64 (default: server/loadgame_blob.b64)")
    ap.add_argument("--dry-run", action="store_true")
    # --- synthesise a design rather than clone one ---
    ap.add_argument("--make", action="store_true",
                    help="build a NEW design from --chassis/--engine/etc "
                         "instead of cloning one")
    ap.add_argument("--like", help="name of any design of the civ to add to")
    ap.add_argument("--like-id", type=int, help="...or its object id")
    for lst in ("chassis", "scanner", "engine", "weapon", "module"):
        ap.add_argument(f"--{lst}", action="append", type=int, default=None,
                        help=f"{lst} part id; repeat for more than one")
    ap.add_argument("--list", action="store_true",
                    help="just print the designs in the capture and exit")
    a = ap.parse_args()

    blob = sp.decode_save(open(a.capture).read())
    print(f"{a.capture}: {len(blob)} bytes inflated\n")
    if a.list:
        for off, oid, name in design_records(blob):
            _, _, parts, owner = read_design(blob, off)
            shown = {k: v for k, v in parts.items() if v}
            print(f"  @{off:<6} id={oid:<6} owner={owner:<6} {name!r:16s} {shown}")
        return

    if a.make:
        if a.like_id is None and not a.like:
            raise SystemExit("--make needs --like <name> or --like-id <id> to "
                             "say which civ the design belongs to")
        parts = {"chassis": a.chassis or [], "scanners": a.scanner or [],
                 "engines": a.engine or [], "weapons": a.weapon or [],
                 "modules": a.module or [], "list6": []}
        if not parts["chassis"]:
            raise SystemExit("a design with no chassis is not buildable; "
                             "pass --chassis <id>")
        if not parts["engines"]:
            print("  [warn] no engine: the UI allows it but nothing built from "
                  "this design can move")
        new = make(blob, a.like_id if a.like_id is not None else a.like,
                   a.name, parts, a.id)
    else:
        if a.src_id is None and not a.src:
            raise SystemExit("need --from <name>, --from-id <id>, or --make")
        new = inject(blob, a.src_id if a.src_id is not None else a.src,
                     a.name, a.id)

    # Re-parse the result and prove the edit before writing anything.
    print("\nverifying the rewritten blob:")
    for off, oid, name in design_records(new):
        _, _, parts, owner = read_design(new, off)
        shown = {k: v for k, v in parts.items() if v}
        print(f"  @{off:<6} id={oid:<6} owner={owner:<6} {name!r:16s} {shown}")

    if a.dry_run:
        print("\n--dry-run: nothing written")
        return
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "loadgame_blob.b64")
    encoded = sp.encode_save(new)
    with open(out, "w") as f:
        f.write(encoded if isinstance(encoded, str)
                else encoded.decode("ascii"))
    print(f"\nwrote {os.path.abspath(out)}")
    print("NOTE: `loadgame` is NOT the load path; the client never requests it "
          "at startup.")
    print("Convert to a raw .dat and pass it on the command line:")
    print("  python save_parser.py <out.b64> --dat ../../client/<name>.dat")
    print("  CosmicSupremacy_Resurgence.exe <abs path>\\<name>.dat")


if __name__ == "__main__":
    main()
