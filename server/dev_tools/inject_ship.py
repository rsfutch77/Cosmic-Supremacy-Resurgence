"""
inject_ship.py , give a civ a ship in a save blob
==========================================================
    python inject_ship.py ../saves/<capture>.b64 --civ Ceti --count 2 \
        -o ../loadgame_blob.b64 --dat ../../client/three_civ.dat

A ship is cloned from one already in the galaxy, because a `SHIP` is the
section this project has never had to build from nothing. Which one is cloned
decides what the new civ gets: a record carries its own crew, so the 104-byte
Colony Ship a generation hands a civ and the 86-byte one the engine builds for
it later are different ships, and taking the smallest available hands a
newcomer two empty ones. `pick_donor` chooses by design name and then by crew,
and `inject_civ.starting_kit` is the caller that cares.

`--allow-ordered` clones a ship that is under orders and clears the order in
the copy, which is the only thing that works in a galaxy far enough along that
every fleet is in flight. Without it such a galaxy has no donor at all.
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


def pick_donor(blob, ships, design_id):
    """The ship to clone, preferring one already of the design being built.

    An unordered donor comes first, because cloning an ordered one means
    stripping its `ROUT` and this does not do that. Among unordered ones the
    donor is chosen by design name and then by crew, and neither is incidental.
    A ship carries its crew in its own `SHPR`, so the clone starts with the
    donor's complement: at generation a Colony Ship is 104 bytes carrying two
    crew, while one the engine has built later in the same galaxy is 86 bytes
    carrying none. Taking the smallest record, which is what this did before,
    hands a newcomer two empty hulls and calls it the starting fleet.

    Matching is by design NAME rather than object id, because a design id is
    galaxy-wide and per-civ: the design a newcomer is given is a fresh clone
    that no existing ship can be flying.
    """
    names = {oid: nm for _off, oid, nm in idg.design_records(blob)}
    want = names.get(design_id)
    return min(ships, key=lambda s: (s["ordered"],
                                     names.get(s["design"]) != want,
                                     -crew_count(blob, s), s["ln"]))


def clear_admiral(rec, at, log=print):
    """Zero a cloned DYNO's admiral id, which would name the donor's officer.

    The blanket owner repointing above catches the civ's object id and not an
    admiral's, so a clone of a ship with an admiral aboard would leave a
    newcomer's hull naming somebody else's.

    **This has never fired.** `admiral_id` reads zero on all 653 ships across
    143 blobs in this repository, so it is a guard against a state nothing on
    disk is in rather than a fix for one that was seen. It is here because
    every other reference inside a cloned record is repointed and this was the
    one that was not.
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
    `make_multiplayer_galaxy.clear_ship_orders` does this to every ship in a
    fresh galaxy and records that the result is byte-identical to the idle
    `DYNO` the engine writes itself. Here it is one ship, because every other
    ship in a galaxy under way belongs to somebody who gave it that order.

    The imports are function-local because `merge_orders` imports this file's
    neighbours and only this one function needs them.
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


def add_ship(blob, civ_name, design_id=None, home_id=None, allow_ordered=False,
             log=print):
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
    donor = pick_donor(blob, ships, design_id)
    if donor["ordered"] and not allow_ordered:
        raise SystemExit("every ship in this blob carries an order; cloning one "
                         "means stripping its ROUT, and this call did not ask "
                         "for that (pass allow_ordered)")

    new_id = idg.max_object_id(blob) + 1
    log(f"\n=== {civ_name!r}: ship {new_id} over planet #{home['id']}, "
        f"design {design_id}")
    log(f"  donor SHIP #{donor['id']} @{donor['off']} ({donor['ln']}B, "
        f"DYNO {donor['dyno_len']}B, unordered, "
        f"{crew_count(blob, donor)} crew)")

    rec = bytearray(blob[donor["off"]:donor["end"]])
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

    # The owner id appears in a SHIP record more than once, and rewriting only
    # the one at +16 leaves the clone half-owned. The consequence is not
    # cosmetic: a colony founded by such a ship comes up with citizens carrying
    # the DONOR civ's id, which looks exactly like a planet whose population
    # belongs to two empires. It is not, it is this bug.
    #
    # Offsets are not fixed, so every remaining occurrence is repointed rather
    # than two known ones, the same idiom inject_civ uses when it clones an
    # OWNR. Matches are unaligned because the format is packed, so each one is
    # logged and can be checked.
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

    # A SHIP record ends with its own object id a second time, and this tool
    # was leaving the donor's there. Every ship the engine wrote reads its own
    # id in both places: 553 of the 685 ships across 151 blobs agree, and all
    # 132 that do not are ships this tool cloned, in galaxies it built. The
    # engine tolerates the stale value , galaxy_demo carried one through five
    # ticks , but a clone that names another ship is a dangling reference of
    # exactly the kind the wipe work went out of its way not to create.
    tail = struct.unpack_from("<I", rec, len(rec) - 4)[0]
    if tail == donor["id"]:
        struct.pack_into("<I", rec, len(rec) - 4, new_id)
        log(f"  trailing object id {tail} -> {new_id}")
    else:
        log(f"  trailing dword reads {tail} and the donor is #{donor['id']}, "
            f"so it is not the self-reference this expects; left alone")

    clear_admiral(rec, donor["dyno"] - donor["off"], log=log)

    at = max(s["end"] for s in ships)
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
