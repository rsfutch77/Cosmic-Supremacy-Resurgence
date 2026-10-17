"""
inject_ship.py: give a civ a ship in a save blob
    python inject_ship.py ../saves/<capture>.b64 --civ Ceti --count 2 \
        -o ../loadgame_blob.b64 --dat ../../client/three_civ.dat

A ship is cloned from one already in the galaxy. A record carries its own
crew, so the donor decides the new ship's complement; `pick_donor` chooses by
design name and then by crew.

`--allow-ordered` clones a ship that is under orders and clears the order in
the copy, for a galaxy where every ship is in flight.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp
import inject_design as idg
import inject_civ as icv

SHIP, DYNO, SHPR = b"SHIP", b"DYNO", b"SHPR"


def ship_records(blob):
    """Every SHIP that chains cleanly, with the offsets a clone needs."""
    out = []
    for off in idg.find_all(blob, SHIP):
        try:
            ver, ln = idg.sec_len(blob, off)
        except Exception:
            continue
        p, end = off + 8, off + 8 + ln
        if ln <= 0 or end > len(blob):
            continue
        dyno = blob.find(DYNO, p, end)
        shpr = blob.find(SHPR, p, end)
        if dyno < 0 or shpr < 0:
            continue
        try:
            oid, x, z, y, owner = struct.unpack_from("<IfffI", blob, p)
            dver, dln = idg.sec_len(blob, dyno)
            sver, sln = idg.sec_len(blob, shpr)
        except Exception:
            continue
        out.append({
            "off": off, "ln": ln, "end": end, "payload": p,
            "id": oid, "pos": (x, z, y), "owner": owner,
            "dyno": dyno, "dyno_len": dln, "shpr": shpr, "shpr_len": sln,
            "design": struct.unpack_from("<I", blob, shpr + 8)[0],
            "ordered": dln > 40,
        })
    return out


def civ_designs(blob, owner_rec):
    """Design ids belonging to one civ, in blob order."""
    return [oid for off, oid, _name in idg.design_records(blob)
            if owner_rec["off"] < off < owner_rec["end"]]


def crew_count(blob, ship):
    """How many crew a SHIP record carries. SHPR+4 is the count."""
    try:
        return struct.unpack_from("<I", blob, ship["shpr"] + 8 + 4)[0]
    except Exception:                                       # noqa: BLE001
        return 0


def pick_donor(blob, ships, design_id, want=None):
    """The ship to clone, preferring one already of the design being built.

    Unordered donors first, then by design name, then by most crew. A ship
    carries its crew in its own `SHPR` (a generated Colony Ship is 104 bytes
    with two crew, an engine-built one 86 bytes with none). Designs match by
    name because a newcomer's design is a fresh clone no ship is flying yet.
    """
    names = {oid: nm for _off, oid, nm in idg.design_records(blob)}
    if want is None:
        want = names.get(design_id)
    return min(ships, key=lambda s: (s["ordered"],
                                     names.get(s["design"]) != want,
                                     -crew_count(blob, s), s["ln"]))


def clear_admiral(rec, at, log=print):
    """Zero a cloned DYNO's admiral id, which would name the donor's officer.

    Owner repointing does not cover admiral ids. Every captured ship reads
    zero here, so this is a guard.
    """
    try:
        payload = at + 8
        shco = struct.unpack_from("<I", rec, payload + 8)[0] & sp.SIZE_MASK
        off = payload + 4 + 8 + shco + 4 + 1
        if off + 4 > len(rec):
            return
        admiral = struct.unpack_from("<I", rec, off)[0]
        if admiral:
            struct.pack_into("<I", rec, off, 0)
            log(f"  admiral {admiral} -> 0, it was the donor's")
    except Exception as exc:                                # noqa: BLE001
        log(f"  could not read the cloned DYNO's admiral id, "
            f"{type(exc).__name__}: {exc}")


def clear_order(blob, ship_oid, log=print):
    """Make one ship idle, leaving a record the engine would have written.

    The three edits `inject_order.py` makes, reversed: the `SHCO` order-type
    byte, the has-orders byte, and the conditional `ROUT` and trailing word.
    The result is byte-identical to an idle `DYNO` the engine writes.
    """
    import inject_order as ior
    import merge_orders as mo

    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find("GLXY"))
    for sec in glxy.find("SHIP"):
        if struct.unpack_from("<I", blob, sec.payload)[0] != ship_oid:
            continue
        dyno = next(sec.find("DYNO"), None)
        if dyno is None:
            return blob
        d = ior.parse_dyno(blob, dyno)
        if not d["shco"][0] and not d["has_orders"] and d["rout"] is None:
            return blob
        was = d["shco"][0]
        d["shco"][0] = 0
        d["has_orders"] = 0
        d["rout"] = None
        d["tail_u32"] = None
        log(f"  ship {ship_oid}: order type {was} cleared, it came from the "
            f"donor")
        return mo.replace_dyno(
            blob, ship_oid,
            sp.build_section(b"DYNO", dyno.version, ior.build_dyno(d)))
    return blob


def ships_at(blob):
    """Where a SHIP goes in a galaxy that holds none: after the last SOLA.

    Inside GLXY the sections run OWNR, SOLA, SHIP, NEBU, and nothing counts the
    ships.
    """
    at = max(o["end"] for o in icv.owner_records(blob))
    while blob[at:at + 4] == b"SOLA":
        at = idg.sec_end(blob, at)
    return at


def add_ship(blob, civ_name, design_id=None, home_id=None, allow_ordered=False,
             template=None, log=print):
    """Clone a ship over `civ_name`'s homeworld.

    `template` is an earlier turn of this galaxy, used only when `blob` holds
    no ship to clone, which abandonment wipes can leave.
    """
    owners = icv.owner_records(blob)
    civ = next((o for o in owners if o["name"] == civ_name), None)
    if civ is None:
        raise SystemExit(f"no civ named {civ_name!r}; saw "
                         f"{[o['name'] for o in owners]}")
    planets = icv.planet_records(blob)
    mine = [p for p in planets if p["owner"] == civ["oid"]]
    if not mine:
        raise SystemExit(f"{civ_name!r} owns no planet to put a ship over")
    home = (next(p for p in mine if p["id"] == home_id) if home_id
            else max(mine, key=lambda p: p["plpr_ln"]))

    if design_id is None:
        ds = civ_designs(blob, civ)
        if not ds:
            raise SystemExit(f"{civ_name!r} owns no design to build the ship on")
        design_id = ds[0]

    ships = ship_records(blob)
    source, pool, want = blob, ships, None
    if not ships and template is not None:
        source, pool = template, ship_records(template)
        want = dict((oid, nm) for _o, oid, nm
                    in idg.design_records(blob)).get(design_id)
    if not pool:
        raise SystemExit("no SHIP records to clone")
    donor = pick_donor(source, pool, design_id, want=want)
    if donor["ordered"] and not allow_ordered:
        raise SystemExit("every ship in this blob carries an order; cloning one "
                         "means stripping its ROUT, and this call did not ask "
                         "for that (pass allow_ordered)")

    new_id = idg.max_object_id(blob) + 1
    log(f"\n=== {civ_name!r}: ship {new_id} over planet #{home['id']}, "
        f"design {design_id}")
    log(f"  donor SHIP #{donor['id']} @{donor['off']} ({donor['ln']}B, "
        f"DYNO {donor['dyno_len']}B, unordered, "
        f"{crew_count(source, donor)} crew)")

    rec = bytearray(source[donor["off"]:donor["end"]])
    base = 8                                   # payload start within rec
    donor_owner = struct.unpack_from("<I", rec, base + 16)[0]
    struct.pack_into("<I", rec, base, new_id)
    struct.pack_into("<fff", rec, base + 4, *home["pos"])
    struct.pack_into("<I", rec, base + 16, civ["oid"])
    struct.pack_into("<I", rec, donor["dyno"] - donor["off"] + 8, home["id"])
    struct.pack_into("<I", rec, donor["shpr"] - donor["off"] + 8, design_id)
    log(f"  owner -> {civ['oid']}, orbit -> #{home['id']}, "
        f"position -> ({home['pos'][0]:.0f}, {home['pos'][1]:.0f}, "
        f"{home['pos'][2]:.0f})")

    # The owner id appears in a SHIP record more than once, at offsets that are
    # not fixed. Every occurrence is repointed (unaligned, since the format is
    # packed) and logged. A missed one gives a colony founded by the ship
    # citizens owned by the donor civ.
    repointed = []
    for off in range(base, len(rec) - 3):
        if off == base + 16:
            continue
        if struct.unpack_from("<I", rec, off)[0] == donor_owner:
            struct.pack_into("<I", rec, off, civ["oid"])
            repointed.append(off - base)
    if repointed:
        log(f"  repointed {len(repointed)} further owner reference(s) at "
            f"payload offset(s) {repointed}")

    # A SHIP record ends with its own object id a second time.
    tail = struct.unpack_from("<I", rec, len(rec) - 4)[0]
    if tail == donor["id"]:
        struct.pack_into("<I", rec, len(rec) - 4, new_id)
        log(f"  trailing object id {tail} -> {new_id}")
    else:
        log(f"  trailing dword reads {tail} and the donor is #{donor['id']}, "
            f"so it is not the self-reference this expects; left alone")

    clear_admiral(rec, donor["dyno"] - donor["off"], log=log)

    at = max(s["end"] for s in ships) if ships else ships_at(blob)
    log(f"  inserting {len(rec)} bytes at {at}:")
    blob = icv.splice(blob, at, at, bytes(rec), log=log, what="ship")

    buf = bytearray(blob)
    hi = struct.unpack_from("<I", buf, idg.HIGH_WATER_ID)[0]
    if new_id > hi:
        struct.pack_into("<I", buf, idg.HIGH_WATER_ID, new_id)
        log(f"  high-water object id {hi} -> {new_id}")
    blob = bytes(buf)

    if donor["ordered"]:
        blob = clear_order(blob, new_id, log=log)
    return blob


def describe(blob, log=print):
    owners = {o["oid"]: o["name"] for o in icv.owner_records(blob)}
    ships = ship_records(blob)
    log(f"{len(ships)} ship(s):")
    by_civ = {}
    for s in ships:
        by_civ.setdefault(owners.get(s["owner"], s["owner"]), []).append(s)
    for name, group in by_civ.items():
        log(f"  {name!r:16} {len(group)}: "
            + ", ".join(f"#{s['id']}(design {s['design']}"
                        + (", ordered" if s["ordered"] else "") + ")"
                        for s in group))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--civ", action="append", default=[],
                    help="civ to give a ship to; repeatable")
    ap.add_argument("--count", type=int, default=1,
                    help="ships per civ (the generator starts civs with 2)")
    ap.add_argument("--design", type=int, default=None)
    ap.add_argument("--home", type=int, default=None)
    ap.add_argument("--allow-ordered", action="store_true",
                    help="clone a ship that is under orders and clear the "
                         "order in the copy, for a galaxy where every ship is")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--dat", default=None)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    blob = sp.decode_save(open(a.capture).read())
    print(f"{os.path.basename(a.capture)}: {len(blob):,} bytes")
    describe(blob)
    if a.list or not a.civ:
        return

    for name in a.civ:
        for _ in range(a.count):
            blob = add_ship(blob, name, design_id=a.design, home_id=a.home,
                            allow_ordered=a.allow_ordered)
    print()
    describe(blob)

    out = a.out or os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "loadgame_blob.b64")
    enc = sp.encode_save(blob)
    with open(out, "w") as f:
        f.write(enc if isinstance(enc, str) else enc.decode("ascii"))
    print(f"\nwrote {out}")
    if a.dat:
        with open(a.dat, "wb") as f:
            f.write(blob)
        print(f"wrote {a.dat} ({len(blob):,} bytes)")


if __name__ == "__main__":
    main()
