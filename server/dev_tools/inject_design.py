"""
inject_design.py — Add a ship design to a save blob
===================================================
Duplicates an existing `DSGN` record inside a civ's `OWNR` block, renames it and
gives it a fresh object id, so the client builds the design itself on load.

    python inject_design.py ../saves/<capture>.b64 --from probeA --name aiscout \
        -o ../loadgame_blob.b64

Why this and not a memory write: creating a ShipDesign in memory produces a real,
EJBO-tagged object, but nothing registers it and nothing computes its stat block,
so it never reaches the UI and cannot be built. The blob route hands the work to
the engine's own deserialising constructor (`ShipDesign::ShipDesign(Stream*)` at
0x005470A0), which does the registration and the stat computation as a matter of
course. In a blob a design is just bytes — no allocator, no tree invariants.

FORMAT, measured against a capture containing two hand-made designs
-------------------------------------------------------------------
Every section is:

    tag(4)  (version << 26 | length26)(4)  payload[length]

The version occupies bits 26..31 and the length bits 0..25 — read out of
`Archive::BeginSection` (0x005E6260) and `Archive::EndSection` (0x005E6320), and
matching every capture. The length EXCLUDES the 8-byte header, which is also
verified by chaining: each record's computed end lands exactly on the next
record's start.

Splitting the word at 24 bits instead of 26 happens to round-trip correctly for
any section under 16 MB, because bits 24..25 of such a length are zero and get
preserved along with the version — every section in a real save qualifies. But it
misreads the version by a factor of four (`DSGN` is version 4, not 0x10), and it
would corrupt anything larger, so the split is at 26.

A design is a `DSGN` section whose payload begins with the object id and then
carries one `SDPR` child:

    DSGN  ver 4   objectId(4)  SDPR...
    SDPR  ver 0   nameLen(4)  name[nameLen]  ...rest...

The object ids in the blob are the SAME ids the running client shows — 648, 655,
656 and 652 in the reference capture matched memory exactly — so the engine
adopts stored ids rather than renumbering. A new design therefore needs an id
that is not already in use.

Designs are per-civ and live inside `OWNR`, and immediately BEFORE the first
`DSGN` of each civ there is a `u32` COUNT of that civ's designs. Confirmed on
both civs of the reference capture: 3 for the civ with three designs, 1 for the
civ with one. Adding a record without bumping that count would leave the last
design unread.
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
    """Every validated section whose span strictly contains `at`.

    A four-byte tag can occur by coincidence inside binary payloads, and widening
    a false positive would corrupt the blob, so candidates need validating.

    The validation is **nesting**, not "the end lands on another tag". That
    earlier rule silently dropped a real ancestor and produced a blob the client
    rejected with `unexpected chunk-id ... expected 0x41444d53 (ADMS), found 0x0`:
    `DATA` inside `OWNR` ends on `OWNR`'s own trailing u32 (`14 00 00 00`), not on
    a tag, so it was thrown away as a coincidence and never widened. The designs
    then overran DATA's declared end and the reader met zeros where ADMS should
    have been. A section's end genuinely need not land on a tag — it can land on
    a parent's own trailing field.

    So instead: take the candidates widest-first and keep one only if it nests
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
    # Adding a design without raising it hands our id straight back out: the
    # first ship built after the load took the same id as the injected design,
    # an id lookup then returned the ShipDesign where a Ship was expected, and
    # the engine read [obj+0x30] — an owner reference node on a Ship, but the
    # SPEED float on a ShipDesign — and dereferenced -1.0f. Access violation at
    # 0x0053C7FC reading 0xBF800000, with a minidump to prove it.
    hi = struct.unpack_from("<I", out, HIGH_WATER_ID)[0]
    if new_id > hi:
        struct.pack_into("<I", out, HIGH_WATER_ID, new_id)
        log(f"  high-water object id {hi} -> {new_id} "
            f"(next engine allocation will be {new_id + 1})")
    else:
        log(f"  high-water object id already {hi}, above the new id {new_id}")
    return bytes(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", help="captured savegame .b64")
    ap.add_argument("--from", dest="src", help="name of the design to clone")
    ap.add_argument("--from-id", dest="src_id", type=int,
                    help="object id of the design to clone (unambiguous when "
                         "both civs have an identically named design)")
    ap.add_argument("--name", required=True, help="name for the new design")
    ap.add_argument("--id", type=int, default=None,
                    help="object id (default: one above the highest in the blob)")
    ap.add_argument("-o", "--out", default=None,
                    help="output .b64 (default: server/loadgame_blob.b64)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    blob = sp.decode_save(open(a.capture).read())
    print(f"{a.capture}: {len(blob)} bytes inflated\n")
    if a.src_id is None and not a.src:
        raise SystemExit("need --from <name> or --from-id <id>")
    new = inject(blob, a.src_id if a.src_id is not None else a.src, a.name, a.id)

    # Re-parse the result and prove the edit before writing anything.
    print("\nverifying the rewritten blob:")
    for off, oid, name in design_records(new):
        print(f"  @{off:<6} id={oid:<6} {name!r}")

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
