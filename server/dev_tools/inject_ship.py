"""
inject_ship.py — give a civ a starting ship in a save blob
==========================================================
    python inject_ship.py ../saves/<capture>.b64 --civ Ceti --count 2 \
        -o ../loadgame_blob.b64 --dat ../../client/three_civ.dat

WHY: `inject_civ.py` can add a whole player, but the new civ gets no ships,
because `SHIP` records are galaxy-level rather than per-civ. Against civs that
the generator starts with two colony ships each, that is not a handicap, it is a
different game — the newcomer cannot colonise anything until it has built and
crewed a hull, which takes ~30 turns it does not have.

WHAT A SHIP IS IN A BLOB, measured against captures with 5, 9, 13 and 15 ships:

    SHIP payload:  u32 objectId ; f32 x, z, y ; u32 ownerObjectId
                   DYNO { u32 orbitPlanetId ; SHCO v2 (9B, byte 0 = order type)
                          u32 ; u8 hasOrders ; u32 admiralId
                          [ROUT + u32, only when the ship has an order] }
                   SHPR { u32 designId ; u32 crew ; f32 condition ; ... }

The head is the same idiom as `PLNT`: id, position, owner id. An UNORDERED ship
is the donor to want — its DYNO is 30 bytes and the whole record is 86, against
124 and 189 for one carrying a route, and cloning the short one means never
having to strip a ROUT section that points at coordinates we are replacing.

`[ ]` NO COUNT FIELD IS BUMPED, because none was found. Four captures holding 5,
9, 13 and 15 ships share no offset that carries the ship count, unlike the civ
count in front of `GLXY`'s owners and the design count in front of each civ's
`DSGN` run. The likely reason is that the reader peeks the next tag instead —
the same pattern the ship reader already uses for `ROUT`, where it builds an
order only if the next section says `ROUT`. If a ship injected this way does not
appear in the client, this assumption is the first thing to doubt.

The new ship is given NO CREW, which is honest rather than lazy: a hull with no
crew cannot move, and the AI player already conscripts and loads one. Handing it
a crewed ship would hand it two free citizens as well.
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


def add_ship(blob, civ_name, design_id=None, home_id=None, log=print):
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
    if not ships:
        raise SystemExit("no SHIP records to clone")
    # Prefer an UNORDERED donor: 86 bytes and no ROUT to strip.
    donor = min(ships, key=lambda s: (s["ordered"], s["ln"]))
    if donor["ordered"]:
        raise SystemExit("every ship in this blob carries an order; cloning one "
                         "means stripping its ROUT, which this does not do yet")

    new_id = idg.max_object_id(blob) + 1
    log(f"\n=== {civ_name!r}: ship {new_id} over planet #{home['id']}, "
        f"design {design_id}")
    log(f"  donor SHIP #{donor['id']} @{donor['off']} ({donor['ln']}B, "
        f"DYNO {donor['dyno_len']}B, unordered)")

    rec = bytearray(blob[donor["off"]:donor["end"]])
    base = 8                                   # payload start within rec
    struct.pack_into("<I", rec, base, new_id)
    struct.pack_into("<fff", rec, base + 4, *home["pos"])
    struct.pack_into("<I", rec, base + 16, civ["oid"])
    struct.pack_into("<I", rec, donor["dyno"] - donor["off"] + 8, home["id"])
    struct.pack_into("<I", rec, donor["shpr"] - donor["off"] + 8, design_id)
    log(f"  owner -> {civ['oid']}, orbit -> #{home['id']}, "
        f"position -> ({home['pos'][0]:.0f}, {home['pos'][1]:.0f}, "
        f"{home['pos'][2]:.0f})")

    at = max(s["end"] for s in ships)
    log(f"  inserting {len(rec)} bytes at {at}:")
    blob = icv.splice(blob, at, at, bytes(rec), log=log, what="ship")

    buf = bytearray(blob)
    hi = struct.unpack_from("<I", buf, idg.HIGH_WATER_ID)[0]
    if new_id > hi:
        struct.pack_into("<I", buf, idg.HIGH_WATER_ID, new_id)
        log(f"  high-water object id {hi} -> {new_id}")
    return bytes(buf)


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
            blob = add_ship(blob, name, design_id=a.design, home_id=a.home)
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
