"""
wipe_civ.py , remove a civ's presence from a save blob
======================================================
Returns every planet a civ owns to uncolonised and deletes every ship it owns.
The civ's `OWNR` record is left in place, so no object id is freed, the `GLXY`
civ count is unchanged and `SAVE+0` is unchanged. To every player the empire is
gone; structurally nothing was removed.

    python wipe_civ.py ../galaxy_demo/turns/0011.b64 --list
    python wipe_civ.py ../galaxy_demo/turns/0011.b64 --civ Neighbor \
        -o wiped.b64 --dat ../../client/wiped.dat

This is what K4 of the public beta plan needs: a player who misses too many
turns has their civ taken off the board, and the galaxy carries on.

What the engine itself does to the id space
-------------------------------------------
Measured before any surgery was attempted, because the fog projection work (D1)
found that deleting a `SOLA` from the middle of the contiguous id space makes
the client bail four seconds into a load, and ships sit in that same space.

`client/war_fork.dat` at turn 110 carries ships 662, 663, 669, 670, 672, 677 and
678. `server/referee_work/war.L1.dat`, the same galaxy 70 turns later after a
two-sided battle, carries 662, 663, 672, 677 and 678. GoodGuy's 669 and 670 are
**gone**: their `SHIP`, `DYNO`, `SHCO`, `SHPR` and `ROUT` sections are absent and
no dword anywhere in the blob still reads 669 or 670. The surviving higher ids
are untouched, so nothing was renumbered or compacted, and `SAVE+0` still reads
678 rather than being lowered to the largest id in use.

So the engine punches holes in the id space itself, every time a ship dies, and
it does not tidy up afterwards.

**It is not a property of combat.** The same thing happens when a colony ship
arrives. `server/referee_work/serve_Neighbor.dat` is the demo galaxy at turn 3
with ships 200, 201, 204, 205, 208 and 209 and three civs holding one planet
each. `server/galaxy_demo/turns/0007.b64` is that galaxy four turns later with
DemoPlayer holding 136 as well and Neighbor holding 138, and ships 201, 204, 205
and 208: the two colony ships were consumed founding those colonies, 200 from
the bottom of the ship range and 209 from the top, and `SAVE+0` still reads 209.

**And the client loads such blobs.** `referee.tick` writes the blob it is about
to tick to `server/referee_work/tick_<epoch>.dat` and hands that file to a fresh
client on its command line, so every tick input is a blob the client loaded.
`tick_1789882262.dat` and `tick_1789882347.dat` are that galaxy at turn 180 with
669 and 670 missing from the middle of the ship range; `tick_1789856007.dat` is a
32-system galaxy at turn 17 holding ship 206 alone after 200 and 205 were
destroyed, with `SAVE+0` still reading 206.

That is why this tool deletes `SHIP` sections outright rather than emptying them:
it is the operation the engine performs on itself several times a galaxy.

What returning a planet costs
-----------------------------
A planet's owner is a `u32` at `PLNT` payload `+16`, so un-owning it is a write.
That on its own leaves a planet carrying the wiped civ's population, stores,
facilities and production queue, which is not an uncolonised planet and is not
what the next player to arrive should inherit.

So the whole `PLPR` is replaced by one taken from a planet in the same galaxy
that nobody has ever colonised, and the planet's name is cleared. The rock keeps
its own `PLNT` fields: object id, position, the float at `+20` and the six bytes
behind the name, all of which vary independently of ownership.

**The cost is the per-unit output rates at `PLPR+4`, which the template's own
values replace.** Those are the rock's and survive colonisation unchanged on an
ordinary colony, so a wiped colony comes back as a slightly different rock than
it was. Carrying them across instead would be faithful there and wrong on a
homeworld, where the customisation allowance has raised them: measured on
`client/custom_full_t2.dat`, a homeworld reads 62 where no uncolonised planet in
that galaxy reads above 55. Keeping that would leave a boosted rock lying in
space for whoever colonises it next, which is a worse outcome than a flattened
one, so the template wins and this is the trade.

What is NOT touched
-------------------
The `OWNR` record, including its designs, research, treasury, `EXSY`, `KNPL` and
`NEWS`, and the object ids those designs hold. Every other civ's `KNPL` record
naming the wiped civ therefore still resolves, which is the reason the shell is
the safe form: nothing in the blob is left pointing at something that is no
longer there.

Removing the `OWNR` as well is `--delete-owner`, and it is the weaker claim of
the two. There is no engine-authored precedent for it: the archive shows the
engine deleting `SHIP` sections routinely and never an `OWNR`, and a civ left
with no planets and no ships kept its `OWNR` through 20 ticks rather than being
cleaned up. What it has is one measurement, on the demo galaxy at turn 11, where
it loaded and ticked 20 turns twice to the same canonical hash. That galaxy is
the easy case, because no other civ's records named the wiped one, so
`delete_owner` refuses any galaxy where they do. See it for what that leaves
open.

What it buys over the shell is the `OWNR` itself, 866 bytes in that galaxy, and
a row that stops appearing in the diplomacy and score lists. The operator has
accepted the dead row and resets the galaxy before they accumulate, so the shell
is the default and this is not a decision anything is waiting on.
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

    Uncolonised planets are uniform in a way colonised ones are not: across the
    541 free planets of `client/cycle.dat` every `PLPR` is 137 bytes and every
    byte agrees except the output rates at `+4..+6` and four further bytes at
    `+11`, `+12`, `+120` and `+121`. The modal length is taken rather than the
    first record found, so a galaxy holding one odd free planet cannot decide
    what a blank planet looks like, and the lowest object id among those breaks
    the tie, so two runs of this tool choose the same template.
    """
    planets = icv.planet_records(blob) if planets is None else planets
    free = [p for p in planets if not p["owner"] and p["nlen"] == 0]
    if not free:
        raise SystemExit("this galaxy has no uncolonised planet to copy a "
                         "blank PLPR from")
    sizes = {}
    for p in free:
        sizes.setdefault(p["plpr_ln"], []).append(p)
    modal = max(sizes.values(), key=len)
    return min(modal, key=lambda p: p["id"])


def uncolonise(blob, planet_id, template_plpr, log=print):
    """Return one planet to the state a colony ship would find it in.

    Three edits, each re-reading the record because the one before it moved the
    offsets behind it: the `PLPR` is replaced, the name is emptied, and the
    owner is zeroed. The owner goes last so a run that fails part way leaves a
    planet that is still visibly owned rather than one that reads as free and is
    not blank.
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


def cut_section(blob, start, end, log=print, what=""):
    """Remove a whole section, narrowing the sections that enclose it.

    `inject_civ.splice` cannot do this. It looks for the enclosing sections from
    a point one byte inside the range it is replacing, which for a whole section
    finds that section itself and tries to take its own length off its own
    header. The first attempt at deleting a `SHIP` failed there, on a 104-byte
    payload asked to shrink by the 112 bytes of the section including its header.

    Searching from the section's first byte instead excludes it, because
    `containing_sections` wants `off < at < end` and a section's own offset is
    not strictly less than itself, while every real ancestor starts earlier.
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

    The section carries its `DYNO`, `SHCO`, `SHPR` and any `ROUT` inside it, and
    the crew with them, so removing the range removes the ship entirely. The
    enclosing sections, `GLXY` and `SAVE`, are narrowed to match.
    """
    rec = next((s for s in ish.ship_records(blob) if s["id"] == ship_id), None)
    if rec is None:
        raise SystemExit(f"no SHIP with object id {ship_id}")
    log(f"  ship {ship_id}: removing {rec['ln'] + 8} bytes at {rec['off']}")

    # A destroyed ship's id appears nowhere in a blob the engine produced, so
    # anything outside the section that reads it is a reference this tool does
    # not know about and is worth understanding before the blob is served.
    #
    # Counted against the blob going in rather than the blob coming out. The
    # format is packed and unaligned, so four bytes of unrelated payload spell
    # an id regularly: this galaxy's ship 208 also reads out of a planet's
    # `PLPR+11`, which is one byte of that rock and two of its neighbours.
    outside = [i for i in idg.find_all(blob, struct.pack("<I", ship_id))
               if not rec["off"] <= i < rec["end"]]
    blob = cut_section(blob, rec["off"], rec["end"], log=log, what="ship")
    if outside:
        log(f"  note: {len(outside)} dword(s) elsewhere in the blob already "
            f"read {ship_id} before the cut, at {outside}")
    return blob


def referenced_by(blob, oid, exclude):
    """Civs whose own `OWNR` range holds a dword reading `oid`.

    A civ another has met is named by object id inside that civ's records, so
    this is what says whether removing an `OWNR` would leave a reference behind.
    It over-reports: the format is packed and four unrelated bytes can spell an
    id. Over-reporting is the right direction for a guard on an unproven path,
    and every hit is printed so the operator can see which it is.
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

    This is the form K4 called the stretch, and what is known about it is
    narrower than the shell. It loaded and ticked 20 turns twice, reproducibly,
    on the demo galaxy at turn 11, and that galaxy is the easy case: no other
    civ's records named the wiped one anywhere, so the reference that would
    dangle was not there to dangle. An abandoned player in a live beta has been
    met, which is the case that has not been run.

    `GLOB`'s own player list keeps naming the civ either way, since it holds a
    name and an `Owner:4` rather than an object id and nothing here rewrites it.
    That blob loaded, so the list tolerates a name with no `OWNR` behind it, but
    it is one more thing the shell never creates.

    So a civ another civ knows about is refused rather than guessed at.
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


def wipe(blob, name, remove_owner=False, force_owner=False, log=print):
    """Take `name` off the board. Returns (blob, planet ids, ship ids)."""
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
    template = pristine_planet(blob)
    log(f"\n=== wiping {name!r} (object id {civ['oid']})")
    log(f"  {len(mine)} planet(s) {mine}, {len(ships)} ship(s) {ships}")
    log(f"  blank PLPR taken from planet #{template['id']} "
        f"({template['plpr_ln']} bytes)")
    blank = bytes(blob[template["plpr_payload"]:template["plpr_end"]])

    for pid in mine:
        blob = uncolonise(blob, pid, blank, log=log)
    for sid in ships:
        blob = delete_ship(blob, sid, log=log)

    # The two counters inject_civ maintains are deliberately left alone. Holding
    # SAVE+0 above every id in use is what the engine itself does after a ship
    # dies, and the civ count still matches the OWNR records present.
    hi = struct.unpack_from("<I", blob, idg.HIGH_WATER_ID)[0]
    civs = struct.unpack_from("<I", blob, icv.civ_count_at(blob))[0]
    if remove_owner:
        blob = delete_owner(blob, name, force=force_owner, log=log)
    else:
        log(f"  OWNR kept; GLXY civ count stays {civs}, SAVE+0 stays {hi}")
    return blob, mine, ships


def check(blob, name, planets, ships, log=print):
    """Report whether the wipe holds in the blob that came out.

    Written as a report rather than an assertion so a partial result is visible:
    a planet that kept its population is a different fault from a ship that is
    still there, and both are worth naming.
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
    ap.add_argument("--list", action="store_true",
                    help="describe the capture and exit")
    a = ap.parse_args()

    blob = sp.load_any(a.capture)
    print(f"{os.path.basename(a.capture)}: {len(blob):,} bytes")
    icv.describe(blob)
    if a.list or not a.civ:
        return 0

    wiped = []
    for name in a.civ:
        blob, planets, ships = wipe(
            blob, name,
            remove_owner=a.delete_owner or a.force_delete_owner,
            force_owner=a.force_delete_owner)
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
