"""
wipe_civ.py: remove a civ's presence from a save blob
Returns every planet a civ owns to uncolonised and deletes every ship and fleet
it owns.
The civ's `OWNR` record is left in place, so no object id is freed, the `GLXY`
civ count is unchanged and `SAVE+0` is unchanged.

    python wipe_civ.py ../galaxy_demo/turns/0011.b64 --list
    python wipe_civ.py ../galaxy_demo/turns/0011.b64 --civ Neighbor \
        -o wiped.b64 --dat ../../client/wiped.dat

Ships are deleted outright. The engine does the same when a ship dies or a
colony ship founds a colony: the `SHIP` section and everything in it vanish,
higher ids are not renumbered, `SAVE+0` is not lowered, and the client loads
the result.

A wiped civ keeps its row in the overview score list. The list holds one row per
distinct `Owner:4`, the `u32` ending the `OWNR` payload, and this tool leaves
`OWNR` untouched unless `--delete-owner` is passed.

Returning a planet: the owner is a `u32` at `PLNT` payload `+16`. The whole
`PLPR` is replaced by one from a never-colonised planet in the same galaxy, and
the name is cleared. The `PLNT` fields (object id, position, the float at `+20`,
the six bytes behind the name) are kept. The template's per-unit output rates at
`PLPR+4` replace the planet's own, which flattens a homeworld's customisation
boost.

Not touched: the `OWNR` record, including its designs, research, treasury,
`KNPL` and `NEWS`, so every other civ's `KNPL` naming the wiped civ still
resolves. Only the `EXSY` entries remembering the wiped civ's planets change.

`--delete-owner` also removes the `OWNR`. It has loaded and ticked only on a
galaxy where no other civ's records named the wiped one, so `delete_owner`
refuses any galaxy where they do.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp
import inject_civ as icv
import inject_design as idg
import inject_ship as ish

PLNT_OWNER_OFF = 16          # PLNT payload +16, the owning civ's object id


def pristine_planet(blob, planets=None):
    """A planet nobody has colonised, to take a blank `PLPR` from.

    A blank `PLPR` is 137 bytes, of which 24 vary:

        +4, +5, +6, +11, +12, +120, +121   differ between planets in one galaxy
        +90 .. +105                        same for every planet in a galaxy,
                                           different between galaxies
        +106                               changes from turn to turn

    The template must come from the same galaxy, since +90..+105 are
    galaxy-wide. `+4` and `+11` are the homeworld customisation pair.

    The modal length is taken, and the lowest object id among those breaks the
    tie, so the choice is deterministic.
    """
    planets = icv.planet_records(blob) if planets is None else planets
    free = [p for p in planets if not p["owner"] and p["nlen"] == 0]
    if not free:
        raise SystemExit(
            "this galaxy has no uncolonised planet to copy a blank PLPR from. "
            "Pass --template-from with an earlier state of THIS galaxy, where "
            "one was still free; a blob from a different galaxy will not do, "
            "see pristine_planet")
    sizes = {}
    for p in free:
        sizes.setdefault(p["plpr_ln"], []).append(p)
    modal = max(sizes.values(), key=len)
    return min(modal, key=lambda p: p["id"])


def blank_plpr(blob, template_blob=None, log=print) -> bytes:
    """The blank `PLPR` to write over a wiped planet.

    From this galaxy when anything is still free, otherwise from an earlier
    state of it supplied by the caller (needed once every planet is colonised).
    """
    source, where = blob, "this galaxy"
    if not [p for p in icv.planet_records(blob)
            if not p["owner"] and p["nlen"] == 0]:
        if template_blob is None:
            pristine_planet(blob)                 # raises, naming the fix
        source, where = template_blob, "the template blob"
    template = pristine_planet(source)
    log(f"  blank PLPR taken from planet #{template['id']} in {where} "
        f"({template['plpr_ln']} bytes)")
    return bytes(source[template["plpr_payload"]:template["plpr_end"]])


def uncolonise(blob, planet_id, template_plpr, log=print):
    """Return one planet to the state a colony ship would find it in.

    Three edits, each re-reading the record since the one before moved its
    offsets: the `PLPR` is replaced, the name is emptied, and the owner is
    zeroed last, so a partial failure leaves the planet visibly owned.
    """
    rec = next(p for p in icv.planet_records(blob) if p["id"] == planet_id)
    was_owner, was_name, was_ln = rec["owner"], rec["name"], rec["plpr_ln"]
    blob = icv.splice(blob, rec["plpr_payload"], rec["plpr_end"],
                      template_plpr, log=log, what="plpr")

    rec = next(p for p in icv.planet_records(blob) if p["id"] == planet_id)
    if rec["nlen"]:
        blob = icv.splice(blob, rec["name_at"], rec["name_at"] + 4 + rec["nlen"],
                          struct.pack("<I", 0), log=log, what="name")

    rec = next(p for p in icv.planet_records(blob) if p["id"] == planet_id)
    buf = bytearray(blob)
    struct.pack_into("<I", buf, rec["payload"] + PLNT_OWNER_OFF, 0)
    log(f"  planet #{planet_id}: owner {was_owner} -> 0, name {was_name!r} -> "
        f"'', PLPR {was_ln} -> {len(template_plpr)} bytes")
    return bytes(buf)


def forget_civ(blob, oid, log=print):
    """Clear the wiped civ out of every civ's remembered map.

    Per planet, `EXSY` holds a last-known owner and name, from which the client
    draws the planet name and the capital marker. Every table is rewritten, the
    victim's own included. `exsy.parse` refuses a table it cannot walk exactly,
    so an unrecognised table stops the wipe.

    Deliberately not fog-correct: other civs forget the wiped civ's planets.
    """
    import exsy
    changed = 0
    for owner in icv.owner_records(blob):
        off = next((i for i in idg.find_all(blob, b"EXSY")
                    if owner["off"] <= i < owner["end"]), None)
        if off is None:
            continue
        ver, ln = idg.sec_len(blob, off)
        table = exsy.parse(bytes(blob[off + 8:off + 8 + ln]))
        hits = 0
        for system in table:
            for planet in system["planets"]:
                if planet["owner"] == oid:
                    planet["owner"] = 0
                    planet["name"] = b""
                    hits += 1
        if not hits:
            continue
        blob = icv.splice(blob, off + 8, off + 8 + ln, exsy.build(table),
                          log=lambda *a: None, what="exsy")
        log(f"  {owner['name']}'s EXSY: {hits} remembered planet(s) cleared")
        changed += hits
    if not changed:
        log("  no remembered planets to clear")
    return blob


def cut_section(blob, start, end, log=print, what=""):
    """Remove a whole section, narrowing the sections that enclose it.

    Ancestors are found from the section's first byte: `containing_sections`
    wants `off < at < end`, which excludes the section itself.
    """
    delta = -(end - start)
    out = bytearray(blob)
    for off, tag, ver, ln in idg.containing_sections(blob, start):
        icv.add_len(out, off, delta)
        if what:
            log(f"      {tag.decode():4} @{off:<7} len {ln} -> {ln + delta}")
    del out[start:end]
    return bytes(out)


def delete_ship(blob, ship_id, log=print):
    """Cut one `SHIP` section out, the way the engine does when a hull dies.

    The section carries its `DYNO`, `SHCO`, `SHPR`, any `ROUT` and the crew.
    The enclosing `GLXY` and `SAVE` are narrowed to match.
    """
    rec = next((s for s in ish.ship_records(blob) if s["id"] == ship_id), None)
    if rec is None:
        raise SystemExit(f"no SHIP with object id {ship_id}")
    log(f"  ship {ship_id}: removing {rec['ln'] + 8} bytes at {rec['off']}")

    # Dwords outside the section that read the ship id may be unknown
    # references. The format is packed and unaligned, so unrelated bytes can
    # also spell an id.
    outside = [i for i in idg.find_all(blob, struct.pack("<I", ship_id))
               if not rec["off"] <= i < rec["end"]]
    blob = cut_section(blob, rec["off"], rec["end"], log=log, what="ship")
    if outside:
        log(f"  note: {len(outside)} dword(s) elsewhere in the blob already "
            f"read {ship_id} before the cut, at {outside}")
    return blob


def fleet_records(blob):
    """[{id, owner, members, off, end}] for every `GLXY > FLET`.

    A member ship lives inside its fleet's `FLDT` as an `SHPR` ending with the
    ship's own id, so cutting the fleet removes its ships too.
    """
    glxy = next(sp.parse_blob(blob)[0].find('GLXY'))
    out = []
    for f in glxy.find('FLET'):
        oid, _x, _z, _y, owner = struct.unpack_from('<IfffI', blob, f.payload)
        fldt = next(f.find('FLDT'), None)
        members = ([struct.unpack_from('<I', blob, s.end - 4)[0]
                    for s in fldt.find('SHPR')] if fldt is not None else [])
        out.append({'id': oid, 'owner': owner, 'members': members,
                    'off': f.start, 'end': f.end})
    return out


def referenced_by(blob, oid, exclude):
    """Civs whose own `OWNR` range holds a dword reading `oid`.

    A civ another has met is named by object id inside that civ's records. May
    over-report, since four unrelated packed bytes can spell an id.
    """
    needle = struct.pack("<I", oid)
    out = {}
    for o in icv.owner_records(blob):
        if o["name"] == exclude:
            continue
        hits = [i for i in idg.find_all(blob, needle)
                if o["off"] <= i < o["end"]]
        if hits:
            out[o["name"]] = hits
    return out


def delete_owner(blob, name, force=False, log=print):
    """Remove a civ's `OWNR` and decrement the `GLXY` civ count.

    Refused when another civ's records name this one, unless forced.
    `GLOB`'s player list (name and `Owner:4`) still names the civ afterwards.
    """
    rec = next((o for o in icv.owner_records(blob) if o["name"] == name), None)
    if rec is None:
        raise SystemExit(f"no civ named {name!r}")
    held = referenced_by(blob, rec["oid"], name)
    if held:
        for who, hits in held.items():
            log(f"  {who!r}'s OWNR holds {len(hits)} dword(s) reading "
                f"{rec['oid']} at {hits}")
        if not force:
            raise SystemExit(
                f"refusing to delete {name!r}'s OWNR: {sorted(held)} still "
                f"name object id {rec['oid']} inside their own records, and a "
                f"galaxy in that state has not been loaded. Wipe without "
                f"--delete-owner, or pass --force-delete-owner and verify the "
                f"result with wipe_acceptance.py before serving it")
    log(f"  OWNR {name!r}: removing {rec['end'] - rec['off']} bytes at "
        f"{rec['off']}")
    blob = cut_section(blob, rec["off"], rec["end"], log=log, what="ownr")
    buf = bytearray(blob)
    at = icv.civ_count_at(buf)
    civs = struct.unpack_from("<I", buf, at)[0]
    struct.pack_into("<I", buf, at, civs - 1)
    log(f"  GLXY civ count {civs} -> {civs - 1}")
    return bytes(buf)


def wipe(blob, name, remove_owner=False, force_owner=False, log=print,
         template_blob=None):
    """Take `name` off the board. Returns (blob, planet ids, ship ids).

    `template_blob` is an earlier state of **this** galaxy, needed only when
    every planet in it has been colonised. See `blank_plpr`.
    """
    owners = icv.owner_records(blob)
    civ = next((o for o in owners if o["name"] == name), None)
    if civ is None:
        raise SystemExit(f"no civ named {name!r}; saw "
                         f"{sorted(o['name'] for o in owners)}")
    if len(owners) < 2:
        raise SystemExit("this galaxy holds one civ; wiping it leaves nobody "
                         "to play")

    mine = [p["id"] for p in icv.planet_records(blob) if p["owner"] == civ["oid"]]
    ships = [s["id"] for s in ish.ship_records(blob) if s["owner"] == civ["oid"]]
    log(f"\n=== wiping {name!r} (object id {civ['oid']})")
    log(f"  {len(mine)} planet(s) {mine}, {len(ships)} ship(s) {ships}")
    blank = blank_plpr(blob, template_blob, log=log)

    blob = forget_civ(blob, civ["oid"], log=log)
    for pid in mine:
        blob = uncolonise(blob, pid, blank, log=log)
    for sid in ships:
        blob = delete_ship(blob, sid, log=log)
    # From the last fleet back, so the offsets still to cut stay valid.
    fleets = [f for f in fleet_records(blob) if f["owner"] == civ["oid"]]
    for f in sorted(fleets, key=lambda f: f["off"], reverse=True):
        log(f"  fleet {f['id']}: removing {f['end'] - f['off']} bytes at "
            f"{f['off']}, with ship(s) {f['members']}")
        blob = cut_section(blob, f["off"], f["end"], log=log, what="fleet")
        ships = ships + f["members"]

    # SAVE+0 and the civ count are left alone, as the engine does after a ship
    # dies; the civ count still matches the OWNR records present.
    hi = struct.unpack_from("<I", blob, idg.HIGH_WATER_ID)[0]
    civs = struct.unpack_from("<I", blob, icv.civ_count_at(blob))[0]
    if remove_owner:
        blob = delete_owner(blob, name, force=force_owner, log=log)
    else:
        log(f"  OWNR kept; GLXY civ count stays {civs}, SAVE+0 stays {hi}")
    return blob, mine, ships


def check(blob, name, planets, ships, log=print):
    """Report whether the wipe holds in the blob that came out.

    Each failure is logged separately. Returns True when the wipe holds.
    """
    ok = True
    owners = icv.owner_records(blob)
    civ = next((o for o in owners if o["name"] == name), None)
    still = [p for p in icv.planet_records(blob) if civ and p["owner"] == civ["oid"]]
    if still:
        log(f"  FAIL: {name!r} still owns {[p['id'] for p in still]}")
        ok = False
    live = [s["id"] for s in ish.ship_records(blob)]
    left = [s for s in ships if s in live]
    if left:
        log(f"  FAIL: ship(s) {left} are still in the blob")
        ok = False
    kept = [f["id"] for f in fleet_records(blob) if civ and f["owner"] == civ["oid"]]
    if kept:
        log(f"  FAIL: fleet(s) {kept} are still in the blob")
        ok = False
    blanks = {p["id"]: p for p in icv.planet_records(blob)}
    for pid in planets:
        p = blanks[pid]
        pop = struct.unpack_from("<I", blob, p["plpr_payload"] + 36)[0]
        if p["owner"] or p["nlen"] or pop:
            log(f"  FAIL: planet #{pid} owner={p['owner']} nlen={p['nlen']} "
                f"population={pop}")
            ok = False
    log(f"  check: {'wiped' if ok else 'NOT wiped'}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("capture", help=".b64 wire capture or raw .dat blob")
    ap.add_argument("--civ", action="append", default=[],
                    help="name of a civ to wipe; repeatable")
    ap.add_argument("--delete-owner", action="store_true",
                    help="also remove the civ's OWNR and decrement the GLXY "
                         "civ count. Refused when another civ's records name "
                         "this one, which is the case nothing has loaded")
    ap.add_argument("--force-delete-owner", action="store_true",
                    help="delete the OWNR anyway. Verify the result with "
                         "wipe_acceptance.py before serving it to anyone")
    ap.add_argument("-o", "--out", default=None, help="output .b64")
    ap.add_argument("--dat", default=None,
                    help="also write the decompressed blob here, ready to be "
                         "passed to the client on its command line")
    ap.add_argument("--template-from", default=None, metavar="BLOB",
                    help="an earlier state of THIS galaxy, to take a blank "
                         "PLPR from when every planet has been colonised. It "
                         "has to be the same galaxy: bytes 90 to 105 of a "
                         "blank record are galaxy-wide, so another galaxy's "
                         "would be wrong. See pristine_planet")
    ap.add_argument("--list", action="store_true",
                    help="describe the capture and exit")
    a = ap.parse_args()

    blob = sp.load_any(a.capture)
    print(f"{os.path.basename(a.capture)}: {len(blob):,} bytes")
    icv.describe(blob)
    if a.list or not a.civ:
        return 0

    template_blob = None
    if a.template_from:
        template_blob = sp.load_any(a.template_from)
        print(f"template: {os.path.basename(a.template_from)}, "
              f"{len(template_blob):,} bytes")

    wiped = []
    for name in a.civ:
        blob, planets, ships = wipe(
            blob, name,
            remove_owner=a.delete_owner or a.force_delete_owner,
            force_owner=a.force_delete_owner,
            template_blob=template_blob)
        wiped.append((name, planets, ships))

    print()
    icv.describe(blob)
    print()
    ok = all(check(blob, name, planets, ships)
             for name, planets, ships in wiped)

    if a.out:
        enc = sp.encode_save(blob)
        with open(a.out, "w") as f:
            f.write(enc if isinstance(enc, str) else enc.decode("ascii"))
        print(f"wrote {a.out} ({len(blob):,} bytes of blob)")
    if a.dat:
        if not a.dat.lower().endswith(".dat"):
            raise SystemExit("--dat path must end in .dat or the client will "
                             "ignore it")
        with open(a.dat, "wb") as f:
            f.write(blob)
        print(f"wrote {a.dat}")
    if not a.out and not a.dat:
        print(f"{len(blob):,} bytes (nothing written; pass -o or --dat)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
