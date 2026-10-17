"""
inject_civ.py: Add a whole PLAYER to a save blob
=================================================
Clones an existing civ's `OWNR` block under a new name and object id, and hands
the new civ a homeworld by giving an uncolonised planet the donor homeworld's
`PLPR`. The engine's own deserialiser then builds and registers the civ on load,
the same way `inject_design.py` gets a ship design built.

    python inject_civ.py ../saves/<capture>.b64 --list
    python inject_civ.py ../saves/<capture>.b64 --name Ceti --name Draco \
        -o ../loadgame_blob.b64

`--name` is repeatable. Each new civ gets an auto-picked homeworld as far as
possible from every planet already spoken for; an unowned planet a ship is
already ordered to counts as spoken for.

A new civ is levelled to what a generated civ starts with (`starting_kit`),
not to what its donor holds now. Pass `kit=False` for a plain clone of the
donor.
"""
import argparse
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp
import inject_design as idg
import inject_order as ior

OWNR, DATA, PLNT, PLPR, DSGN, SDPR, KNPL, GLXY = (
    b"OWNR", b"DATA", b"PLNT", b"PLPR", b"DSGN", b"SDPR", b"KNPL", b"GLXY")
SOLA, SUN, ROUT = b"SOLA", b"SUN ", b"ROUT"

# How close a ROUT target has to sit to an object's stored position to name
# that object. Measured targets sit exactly on it; the tolerance only avoids
# float equality.
MATCH_TOL = 0.01

# What a generated civ starts with, as every generated civ in the captures
# reads it.
STARTING_SHIPS = 2
STARTING_CREDITS = 200          # 200 at turn 0; the turn-1 captures read 216,
                                # which is 200 and one turn of income
STARTING_RECRUIT = 0
STARTING_PROGRESS = 0

# A generated homeworld's food, store over capacity, as the client shows it.
# Capacity grows with development, not population alone, so it is a constant.
STARTING_FOOD_STORE = 0
STARTING_FOOD_CAP = 600

# The homeworld's facilities and the rest of its settled block, as a generation
# writes them. See `merge_orders.SETTLED_LEN` for the layout.
STARTING_FACILITIES = ((2, 1), (6, 1), (9, 1))
STARTING_SETTLED_COUNTER = 1
STARTING_SETTLED_LAST = 10

# A homeworld's founding turn. Every homeworld carries 0 (a colony carries its
# real founding turn), so a joiner's capital matches the incumbents'.
HOMEWORLD_FOUNDED = 0

# Both research fields read this at generation: "nothing chosen".
RESEARCH_UNSET = b"\xff\xff\xff\xff"

# Four farmers, two workers and one scientist, and no stationed military. The
# array is kept sorted by job.
STARTING_JOBS = (0, 0, 0, 0, 1, 1, 2)


def set_len(buf, off, new_len):
    ver = struct.unpack_from("<I", buf, off + 4)[0] >> 26
    struct.pack_into("<I", buf, off + 4, (ver << 26) | new_len)


def add_len(buf, off, delta):
    lw = struct.unpack_from("<I", buf, off + 4)[0]
    struct.pack_into("<I", buf, off + 4, ((lw >> 26) << 26) |
                     ((lw & 0x3FFFFFF) + delta))


def splice(blob, start, end, new_bytes, log=print, what=""):
    """Replace blob[start:end], widening every section that encloses it.

    Ancestors are found from a point strictly inside the replaced range, so
    enclosing sections are widened and neighbours are not. Missing one
    ancestor makes the client reject the blob.
    """
    delta = len(new_bytes) - (end - start)
    out = bytearray(blob)
    if delta:
        for off, tag, ver, ln in idg.containing_sections(blob, start + 1
                                                         if end > start else start):
            add_len(out, off, delta)
            if what:
                log(f"      {tag.decode():4} @{off:<7} len {ln} -> {ln + delta}")
    out[start:end] = new_bytes
    return bytes(out)


def replace_u32(buf, old, new, start, end):
    """Every 4-byte little-endian `old` in buf[start:end] becomes `new`."""
    needle, repl = struct.pack("<I", old), struct.pack("<I", new)
    n, pos = 0, start
    while True:
        i = buf.find(needle, pos, end)
        if i < 0:
            return n
        buf[i:i + 4] = repl
        n += 1
        pos = i + 4


def owner_records(blob):
    """Every OWNR, with its name and object id located.

    The tag bytes can occur inside binary payloads, so a candidate needs a
    plausible name length, a printable name and a DATA child right behind it.
    """
    out = []
    for off in idg.find_all(blob, OWNR):
        try:
            ver, ln = idg.sec_len(blob, off)
        except Exception:
            continue
        p = off + 8
        if ln <= 0 or p + ln > len(blob):
            continue
        nlen = struct.unpack_from("<I", blob, p)[0]
        if not (0 < nlen <= 64) or p + 8 + nlen > len(blob):
            continue
        name = blob[p + 4:p + 4 + nlen]
        if not all(32 <= c < 127 for c in name):
            continue
        if blob[p + 8 + nlen:p + 12 + nlen] != DATA:
            continue
        out.append({
            "off": off, "ver": ver, "ln": ln, "end": off + 8 + ln,
            "payload": p, "nlen": nlen, "name": name.decode("ascii"),
            "id_at": p + 4 + nlen,
            "oid": struct.unpack_from("<I", blob, p + 4 + nlen)[0],
        })
    return out


def planet_records(blob):
    """Every PLNT, with the fields a homeworld transplant needs."""
    out = []
    for off in idg.find_all(blob, PLNT):
        try:
            ver, ln = idg.sec_len(blob, off)
        except Exception:
            continue
        p = off + 8
        if ln <= 0 or p + ln > len(blob):
            continue
        try:
            pid, x, z, y, owner, f5 = struct.unpack_from("<IfffIf", blob, p)
            nlen = struct.unpack_from("<I", blob, p + 24)[0]
        except Exception:
            continue
        if nlen > 64 or p + 28 + nlen > off + 8 + ln:
            continue
        name = blob[p + 28:p + 28 + nlen]
        if not all(32 <= c < 127 for c in name):
            continue
        plpr = blob.find(PLPR, p, off + 8 + ln)
        if plpr < 0:
            continue
        pver, pln = idg.sec_len(blob, plpr)
        out.append({
            "off": off, "ln": ln, "end": off + 8 + ln, "payload": p,
            "id": pid, "pos": (x, z, y), "owner": owner, "f5": f5,
            "nlen": nlen, "name": name.decode("ascii"), "name_at": p + 24,
            "plpr": plpr, "plpr_ln": pln,
            "plpr_payload": plpr + 8, "plpr_end": plpr + 8 + pln,
        })
    return out


def turn_of(blob):
    """The turn number a blob carries, from GLOB payload +0."""
    glob = next(sp.parse_blob(blob)[0].find("GLOB"))
    return struct.unpack_from("<I", blob, glob.payload)[0]


def civ_count_at(blob):
    """Offset of GLXY's leading civ count."""
    g = blob.find(GLXY)
    if g < 0:
        raise SystemExit("no GLXY section; is this a real save blob?")
    return g + 8


def dist(a, b):
    return math.sqrt(sum((u - v) ** 2 for u, v in zip(a, b)))


def system_records(blob, tree=None):
    """Every solar system: the SUN's id and position, and its planets' ids.

    Membership comes from the tree: `SOLA` holds one `SUN` followed by that
    system's `PLNT` children.
    """
    tree = sp.parse_blob(blob) if tree is None else tree
    out = []
    for sola in (s for s in sp.flatten(tree) if s.tag == SOLA):
        sun = next((c for c in sola.children if c.tag == SUN), None)
        if sun is None:
            continue
        out.append({
            "id": struct.unpack_from("<I", blob, sun.payload)[0],
            "pos": struct.unpack_from("<3f", blob, sun.payload + 4),
            "planets": [struct.unpack_from("<I", blob, c.payload)[0]
                        for c in sola.children if c.tag == PLNT],
        })
    return out


def ship_destinations(blob, tree=None):
    """Where every ship carrying an order is going, as planet ids.

    A `ROUT` records no destination id. Its target XYZ is a copy of the
    destination object's stored position, either a `PLNT` or a `SUN`.
    `ROUT`'s `ref_id` at `+92` is the ship's starting orbit, not the
    destination.

    A target on a planet reserves that planet. A target on a sun reserves every
    planet in that `SOLA`, since the planet is not chosen until the colonise
    order is issued.

    A live order is one with a `ROUT`. The has-orders byte can be clear on an
    engine-issued scout order in flight.
    """
    tree = sp.parse_blob(blob) if tree is None else tree
    planets = planet_records(blob)
    systems = system_records(blob, tree)
    out = []
    for sid, owner, pos, sh, dyno in ior.ship_sections(blob, tree):
        if dyno is None:
            continue
        try:
            d = ior.parse_dyno(blob, dyno)
        except Exception:
            continue
        rout = next((c for c in dyno.children if c.tag == ROUT), None)
        if d["rout"] is None or rout is None:
            continue
        r = sp.parse_rout(blob, rout)
        tgt = (r["target_x"], r["target_y"], r["target_z"])
        hit = min(planets, key=lambda p: dist(p["pos"], tgt), default=None)
        if hit is not None and dist(hit["pos"], tgt) <= MATCH_TOL:
            kind, named = "planet", [hit["id"]]
        else:
            sun = min(systems, key=lambda s: dist(s["pos"], tgt), default=None)
            if sun is not None and dist(sun["pos"], tgt) <= MATCH_TOL:
                kind, named = "system", list(sun["planets"])
            else:
                kind, named = "unmatched", []
        out.append({"ship": sid, "owner": owner, "order_type": d["shco"][0],
                    "target": tgt, "kind": kind, "planets": named,
                    "progress": r["progress"]})
    return out


def reserved_planets(blob, tree=None):
    """Planet ids already spoken for by a ship order in flight."""
    return {pid for d in ship_destinations(blob, tree) for pid in d["planets"]}


def system_spacing(blob, tree=None):
    """Median distance from a system to its nearest neighbour.

    The placement margin is quoted in this unit because galaxy density varies.
    """
    pos = [s["pos"] for s in system_records(blob, tree)]
    if len(pos) < 2:
        return 0.0
    nearest = sorted(min(dist(a, b) for j, b in enumerate(pos) if j != i)
                     for i, a in enumerate(pos))
    return nearest[len(nearest) // 2]


def seat_one(owners):
    """The civ to clone when the caller names no donor.

    The lowest object id: the civ the generator allocated first. That seat
    carries the homeworld customisation (the `PLPR` bytes
    `make_multiplayer_galaxy.RATE_OFFSETS` names), which a clone inherits. A
    new civ takes `max_object_id + 1`, so the rule never picks a newcomer and
    every civ in a multi-name run clones the same donor. Blob order and `OWNR`
    size are not seat order.
    """
    return min(owners, key=lambda o: o["oid"])


def pick_homeworld(planets, taken, margin=0.0):
    """The uncolonised planet furthest from everything already claimed.

    Maximin over every claimed position. `margin` is a floor under the winning
    distance: the pick is refused when even the furthest free planet sits
    closer than it.
    """
    claimed = [p["pos"] for p in planets if p["owner"]] + \
              [p["pos"] for p in planets if p["id"] in taken]
    free = [p for p in planets
            if not p["owner"] and p["id"] not in taken and p["nlen"] == 0]
    if not free:
        raise SystemExit("no uncolonised planet left to use as a homeworld")
    if not claimed:
        return free[0]
    best = max(free, key=lambda p: min(dist(p["pos"], c) for c in claimed))
    got = min(dist(best["pos"], c) for c in claimed)
    if margin and got < margin:
        raise SystemExit(
            f"no room left: the furthest free planet is #{best['id']} at "
            f"{got:.0f} from the nearest claimed position, under the "
            f"{margin:.0f} margin. Lower it with --margin, or use a bigger "
            f"galaxy")
    return best


def starting_citizens(owner_id):
    """The citizen array a generated homeworld carries, owned by `owner_id`.

    Nine bytes a citizen: the job id, two zero bytes, the owning civ's object
    id, a per-citizen value (zero at generation) and a trailing zero.
    """
    return b"".join(bytes((job, 0, 0)) + struct.pack("<I", owner_id)
                    + b"\x00\x00" for job in STARTING_JOBS)


def empty_prod(blob):
    """A production queue out of this galaxy that nobody has ever used.

    A `PROD` is 37 bytes and ends in a nested section naming what is queued,
    `WLTH` for nothing and `FCLT` or `SHIP` for something. A never-colonised
    planet's is the `WLTH` shape, byte for byte what a generated homeworld
    carries. None when no uncolonised planet is left.
    """
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find("GLXY"))
    for sola in (c for c in glxy.children if c.tag == SOLA):
        for sec in sola.find("PLNT"):
            owner = struct.unpack_from("<I", blob, sec.payload + 16)[0]
            nlen = struct.unpack_from("<I", blob, sec.payload + 24)[0]
            if owner or nlen:
                continue
            plpr = next(sec.find("PLPR"), None)
            if plpr is None:
                continue
            prod = next(plpr.find("PROD"), None)
            if prod is None:
                continue
            return bytes(blob[prod.start:prod.end])
    return None


def planet_prod(blob, planet_oid):
    """One planet's PROD section bytes, or None."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find("GLXY"))
    for sola in (c for c in glxy.children if c.tag == SOLA):
        for sec in sola.find("PLNT"):
            if struct.unpack_from("<I", blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find("PLPR"), None)
            prod = next(plpr.find("PROD"), None) if plpr else None
            return bytes(blob[prod.start:prod.end]) if prod else None
    return None


def planet_plpr(blob, planet_oid):
    """One planet's PLPR payload bytes, or None."""
    rec = next((p for p in planet_records(blob) if p["id"] == planet_oid), None)
    if rec is None:
        return None
    return bytes(blob[rec["plpr_payload"]:rec["plpr_end"]])


def remove_section(buf, off):
    """Delete the whole section at `off`, shrinking every section above it.

    Only strict ancestors are adjusted; `splice` would also adjust the deleted
    section itself.
    """
    _ver, ln = idg.sec_len(bytes(buf), off)
    out = bytearray(buf)
    for at, _tag, _ver, _ln in idg.containing_sections(bytes(buf), off + 1):
        if at != off:
            add_len(out, at, -(8 + ln))
    del out[off:off + 8 + ln]
    return out


def keep_starting_design(rec, log=print):
    """Drop every design from a cloned `OWNR` but the one it was generated with.

    A generated civ owns exactly one `DSGN`, its Colony Ship, allocated right
    after the civ, so the lowest design object id inside an `OWNR` is the one
    it started with. The count dword sits four bytes in front of the first
    `DSGN`; a record whose count does not match the designs present is left
    alone.
    """
    found = idg.design_records(bytes(rec))
    if len(found) < 2:
        return bytes(rec)
    offs = [off for off, _oid, _nm in found]
    keep = min(found, key=lambda d: d[1])
    count_at = offs[0] - 4
    if count_at < 0:
        log("  design book left alone: nothing in front of the first DSGN to "
            "read a count from")
        return bytes(rec)
    count = struct.unpack_from("<I", rec, count_at)[0]
    if count != len(offs):
        log(f"  design book left alone: {len(offs)} DSGN(s) and a count field "
            f"reading {count}")
        return bytes(rec)
    out = bytearray(rec)
    struct.pack_into("<I", out, count_at, 1)
    for off in sorted(offs, reverse=True):
        if off != keep[0]:
            out = remove_section(out, off)
    dropped = ", ".join(f"{oid} {nm!r}" for off, oid, nm in found
                        if off != keep[0])
    log(f"  design book {len(offs)} -> 1, keeping {keep[1]} {keep[2]!r} and "
        f"dropping {dropped}")
    return bytes(out)


def starting_kit(blob, name, home_id, design_id, ships=STARTING_SHIPS,
                 credits=STARTING_CREDITS, template=None, log=print):
    """Level a civ this tool has just cloned to what a generation hands one.

    Resets the homeworld's citizen array and stationed military, recruitment
    rate, production queue and progress, food, facilities and settled block,
    plus the civ's credits and research topic, and adds the starting hulls.
    The design book is trimmed earlier, inside the clone.

    The homeworld `PLPR` is reset field by field, because some planet fields
    are uniform within a galaxy and differ between galaxies. The facility list
    is variable length, so later fields shift with it. The rate pair at `+4`
    and `+11` (`make_multiplayer_galaxy.RATE_OFFSETS`) is kept from the donor.

    Not reset: `OWPR` (138 bytes, 178 on a civ that has researched), `CVTR`,
    `SERV`, `GOVS`, `ADMS`, `SPQS` and `USSE`, the civ traits among them.

    Nothing raises: each step logs what it could not do and the rest go on,
    since an exception out of `add_civ` refuses the join.
    """
    import merge_orders as mo
    import inject_ship as ish

    civ = next((o for o in owner_records(blob) if o["name"] == name), None)
    if civ is None:
        log(f"  starting kit skipped: no civ named {name!r} in the blob")
        return blob
    oid = civ["oid"]

    def people(b):
        plpr = planet_plpr(b, home_id)
        before = len(mo.citizens_of(plpr) or [])
        mil = len(mo.military_of(plpr)[1] or [])
        out = mo.set_people(b, home_id, starting_citizens(oid), [])
        log(f"  homeworld population {before} -> {len(STARTING_JOBS)}, "
            f"stationed military {mil} -> 0")
        return out

    def recruit(b):
        before = mo.recruit_of(planet_plpr(b, home_id))
        if before == STARTING_RECRUIT:
            return b
        log(f"  recruitment rate {before} -> {STARTING_RECRUIT}")
        return mo.set_recruit(b, home_id, STARTING_RECRUIT)

    def progress(b):
        before = mo.progress_of(planet_plpr(b, home_id))
        if before == STARTING_PROGRESS:
            return b
        log(f"  production points {before} -> {STARTING_PROGRESS}")
        return mo.set_progress(b, home_id, STARTING_PROGRESS)

    def food(b):
        want = (STARTING_FOOD_STORE, STARTING_FOOD_CAP)
        before = mo.food_of(planet_plpr(b, home_id))
        if before is None or before == want:
            return b
        log(f"  food {before[0]}/{before[1]} -> {want[0]}/{want[1]}")
        return mo.set_food(b, home_id, *want)

    def facilities(b):
        want = [tuple(p) for p in STARTING_FACILITIES]
        before = mo.facilities_of(planet_plpr(b, home_id))
        if before is None or before == want:
            return b
        log(f"  facilities {before} -> {want}")
        return mo.set_facilities(b, home_id, want)

    def settled(b):
        now = mo.settled_of(b, home_id)
        if now is None:
            log("  settled block left alone: this homeworld has none")
            return b
        turn = turn_of(b)
        want = {'pop': len(STARTING_JOBS),
                'history': [0] * mo.SETTLED_HISTORY_LEN,
                'counter': STARTING_SETTLED_COUNTER,
                'age': float(turn - HOMEWORLD_FOUNDED),
                'founded': HOMEWORLD_FOUNDED,
                'last': STARTING_SETTLED_LAST}
        changed = {k: (now[k], v) for k, v in want.items() if now[k] != v}
        if now['founded_head'] != HOMEWORLD_FOUNDED:
            changed['founded_head'] = (now['founded_head'], HOMEWORLD_FOUNDED)
        if not changed:
            return b
        log("  settled block " + ", ".join(
            f"{k} {a} -> {v}" for k, (a, v) in changed.items()))
        return mo.set_settled(b, home_id, **want)

    def queue(b):
        fresh = empty_prod(b)
        if fresh is None:
            log("  production queue left alone: no uncolonised planet to take "
                "an empty one from")
            return b
        current = planet_prod(b, home_id)
        if current == fresh:
            return b
        was = mo.prod_builds(current) if current else None
        log(f"  production queue emptied"
            + (f", it was building design {was}" if was else ""))
        return mo.replace_prod(b, home_id, fresh)

    def money(b):
        before = mo.credits_of(b, oid)
        if before == credits:
            return b
        log(f"  credits {before} -> {credits}")
        return mo.set_credits(b, oid, credits)

    def research(b):
        before = mo.research_of(b, oid)
        want = tuple(RESEARCH_UNSET for _ in mo.RESEARCH_FIELDS)
        if before == want:
            return b
        log(f"  research topic {before} -> unset")
        return mo.set_research(b, oid, want)

    for what, step in (("the homeworld population", people),
                       ("the recruitment rate", recruit),
                       ("the production points", progress),
                       ("the homeworld food", food),
                       ("the homeworld facilities", facilities),
                       ("the settled block", settled),
                       ("the production queue", queue),
                       ("the credit balance", money),
                       ("the research topic", research)):
        try:
            blob = step(blob)
        except Exception as exc:                            # noqa: BLE001
            log(f"  starting kit: {what} left alone, "
                f"{type(exc).__name__}: {exc}")

    made = 0
    for _ in range(int(ships)):
        try:
            blob = ish.add_ship(blob, name, design_id=design_id,
                                home_id=home_id, allow_ordered=True,
                                template=template, log=lambda *a: None)
            made += 1
        except (Exception, SystemExit) as exc:              # noqa: BLE001
            log(f"  starting kit: {made} hull(s) of {ships}, "
                f"{type(exc).__name__}: {exc}")
            break
    if made:
        log(f"  {made} hull(s) on design {design_id}, the fleet a generated "
            f"civ starts with")
    return blob


def pick_donor(blob, template=None):
    """(source blob, donor OWNR record, how) for a clone that names no donor.

    Seat one of `blob` when it still holds a planet, else seat one of
    `template` (an earlier turn of the same galaxy), else any civ in `blob`
    that holds a planet. An abandonment wipe can leave seat one, or every
    civ, holding nothing.
    """
    owners = owner_records(blob)
    held = {p["owner"] for p in planet_records(blob) if p["owner"]}
    donor = seat_one(owners)
    if donor["oid"] in held:
        return blob, donor, "lowest object id, seat one"
    if template is not None:
        then = seat_one(owner_records(template))
        if any(p["owner"] == then["oid"] for p in planet_records(template)):
            return template, then, "seat one, from an earlier turn"
    donor = next((o for o in owners if o["oid"] in held), None)
    if donor is None:
        raise SystemExit("no civ in this galaxy holds a planet to copy, and "
                         "no earlier turn of it was given to copy one from")
    return blob, donor, "first civ holding a planet, seat one holds none"


def add_civ(blob, new_name, donor_name=None, home_id=None, taken=(),
            user_id=None, margin=None, kit=True, ships=None, template=None,
            log=print):
    """Clone a civ into the blob and level it to a generated civ's start.

    `kit` is on by default; the referee's join path passes only a name.
    `ships` overrides the starting hull count. `template` is an earlier turn
    of this galaxy, used by `pick_donor` and by the starting kit when no ship
    is left to copy.
    """
    owners = owner_records(blob)
    if not owners:
        raise SystemExit("no OWNR records found")
    names = {o["name"] for o in owners}
    if new_name in names:
        raise SystemExit(f"a civ named {new_name!r} already exists; the name is "
                         f"what the engine derives the registry key from, so it "
                         f"must be unique")
    if len(new_name) > 15:
        raise SystemExit(f"{new_name!r} is longer than the 15-char name buffer")

    if donor_name:
        source = blob
        donor = next((o for o in owners if o["name"] == donor_name), None)
        if donor is None:
            raise SystemExit(f"no civ named {donor_name!r}; saw {sorted(names)}")
        how = "named"
    else:
        source, donor, how = pick_donor(blob, template)

    planets = planet_records(blob)
    donor_home = max((p for p in planet_records(source)
                      if p["owner"] == donor["oid"]),
                     key=lambda p: p["plpr_ln"], default=None)
    if donor_home is None:
        raise SystemExit(f"{donor['name']!r} owns no planet to copy")

    # An unowned planet a ship is already ordered to is reserved.
    dests = ship_destinations(blob)
    reserved = {pid for d in dests for pid in d["planets"]}
    for d in dests:
        if d["kind"] == "unmatched":
            log(f"  ship {d['ship']} order type {d['order_type']} targets "
                f"({d['target'][0]:.2f}, {d['target'][1]:.2f}, "
                f"{d['target'][2]:.2f}), which is no planet and no sun")
    if dests:
        log(f"  {len(dests)} ship(s) under orders reserve "
            f"{len(reserved)} planet(s): " +
            ", ".join(f"{d['ship']}->{d['planets'][0]}" if d["kind"] == "planet"
                      else f"{d['ship']}->system of {len(d['planets'])}"
                      for d in dests if d["planets"]))

    if home_id is not None:
        target = next((p for p in planets if p["id"] == home_id), None)
        if target is None:
            raise SystemExit(f"no planet with object id {home_id}")
        if target["owner"]:
            raise SystemExit(f"planet {home_id} already belongs to civ id "
                             f"{target['owner']}")
        if target["id"] in reserved:
            bound = sorted({d["ship"] for d in dests
                            if target["id"] in d["planets"]})
            log(f"  WARNING: planet {home_id} is where ship(s) {bound} are "
                f"already headed. Naming it with --home takes a colonisation "
                f"that is in flight")
    else:
        if margin is None:
            margin = system_spacing(blob)
        target = pick_homeworld(planets, set(taken) | reserved, margin=margin)

    new_id = idg.max_object_id(blob) + 1
    log(f"\n=== {new_name!r}: cloning {donor['name']!r} (id {donor['oid']}, "
        f"{how}) as object id {new_id}")
    nearest = min((dist(target['pos'], p['pos']) for p in planets
                   if p['owner']), default=None)
    log(f"  homeworld: planet #{target['id']} at "
        f"({target['pos'][0]:.0f}, {target['pos'][1]:.0f}, {target['pos'][2]:.0f})"
        + (", no planet is owned" if nearest is None
           else f", {nearest:.0f} from the nearest owned planet")
        + f"{'' if not margin else f', margin {margin:.0f}'}")
    log(f"  donor homeworld: planet #{donor_home['id']} {donor_home['name']!r} "
        f"(PLPR {donor_home['plpr_ln']} bytes)")

    # The homeworld first, before anything shifts the planet offsets.
    donor_plpr = bytearray(
        source[donor_home["plpr_payload"]:donor_home["plpr_end"]])
    n = replace_u32(donor_plpr, donor["oid"], new_id, 0, len(donor_plpr))
    log(f"  PLPR transplant: {len(donor_plpr)} bytes, {n} owner reference(s) "
        f"repointed to {new_id}")
    out = splice(blob, target["plpr_payload"], target["plpr_end"],
                 bytes(donor_plpr), log=log, what="plpr")

    # The name sits in front of the PLPR, so re-read the record after a splice.
    target = next(p for p in planet_records(out) if p["id"] == target["id"])
    hq = f"{new_name}'s HQ".encode("ascii")
    out = splice(out, target["name_at"], target["name_at"] + 4 + target["nlen"],
                 struct.pack("<I", len(hq)) + hq, log=log, what="name")

    target = next(p for p in planet_records(out) if p["id"] == target["id"])
    buf = bytearray(out)
    struct.pack_into("<I", buf, target["payload"] + 16, new_id)
    log(f"  planet #{target['id']} owner -> {new_id}, named {hq.decode()!r}")
    blob = bytes(buf)

    # The OWNR clone.
    owners = owner_records(blob)
    if source is not blob:
        donor = next(o for o in owner_records(source)
                     if o["name"] == donor["name"])
        rec = bytearray(source[donor["off"]:donor["end"]])
    else:
        donor = next(o for o in owners if o["name"] == donor["name"])
        rec = bytearray(blob[donor["off"]:donor["end"]])

    # Ownership by id, everywhere inside the clone: design owners, citizens,
    # and the prologue's own object id.
    n = replace_u32(rec, donor["oid"], new_id, 0, len(rec))
    log(f"  OWNR clone: {len(rec)} bytes, {n} embedded owner reference(s) "
        f"repointed")

    nb = new_name.encode("ascii")
    rec = bytearray(splice(bytes(rec), 8, 12 + donor["nlen"],
                           struct.pack("<I", len(nb)) + nb))
    log(f"  name {donor['name']!r} -> {new_name!r}")

    # Trim the design book before new design ids are handed out.
    if kit:
        rec = bytearray(keep_starting_design(rec, log=log))

    # Fresh ids for the cloned designs: names may repeat across civs, object
    # ids are galaxy-wide.
    next_id = new_id
    kit_design = None
    for off in idg.find_all(bytes(rec), DSGN):
        if rec[off + 12:off + 16] != SDPR:
            continue
        next_id += 1
        old = struct.unpack_from("<I", rec, off + 8)[0]
        struct.pack_into("<I", rec, off + 8, next_id)
        if kit_design is None:
            kit_design = next_id
        log(f"  design object id {old} -> {next_id}")

    # KNPL is the known-players vector: a leading count, then one record per
    # civ met. A new civ has met nobody.
    k = bytes(rec).find(KNPL)
    if k >= 0:
        kver, kln = idg.sec_len(bytes(rec), k)
        met = struct.unpack_from("<I", rec, k + 8)[0]
        if kln >= 4 and met < 64:
            rec = bytearray(splice(bytes(rec), k + 8, k + 8 + kln,
                                   struct.pack("<I", 0)))
            log(f"  KNPL: {met} known player(s) -> 0")
        else:
            log(f"  KNPL left alone (count field reads {met}, not a count)")

    # The last u32 of the payload is Owner:4, a per-civ number distinct from the
    # object id (generated civs read 20, 21, ...). It must be unique.
    donor_uid = struct.unpack_from("<I", source, donor["end"] - 4)[0]
    uid = (max(struct.unpack_from("<I", blob, o["end"] - 4)[0]
               for o in owners) + 1) if user_id is None else user_id
    struct.pack_into("<I", rec, len(rec) - 4, uid)
    forced = "" if user_id is None else ("  (FORCED: a value already in use "
                                         "hides the civ from the highscore list)")
    log(f"  Owner:4 {donor_uid} -> {uid}{forced}")

    # Splice the clone in after the last OWNR.
    at = max(o["end"] for o in owner_records(blob))
    log(f"  inserting {len(rec)} bytes at {at}:")
    blob = splice(blob, at, at, bytes(rec), log=log, what="ownr")

    # The GLXY civ count and the high-water object id.
    buf = bytearray(blob)
    ca = civ_count_at(buf)
    civs = struct.unpack_from("<I", buf, ca)[0]
    struct.pack_into("<I", buf, ca, civs + 1)
    log(f"  GLXY civ count {civs} -> {civs + 1}")

    hi = struct.unpack_from("<I", buf, idg.HIGH_WATER_ID)[0]
    if next_id > hi:
        struct.pack_into("<I", buf, idg.HIGH_WATER_ID, next_id)
        log(f"  high-water object id {hi} -> {next_id}")
    blob = bytes(buf)

    # The starting kit comes last: the hulls need a civ that exists.
    if kit:
        blob = starting_kit(
            blob, new_name, target["id"], kit_design,
            ships=STARTING_SHIPS if ships is None else ships,
            template=template, log=log)

    donor_rows = (score_rows(source) or (0, {}, 0))[1]
    blob = add_score_row(blob, uid, new_name, donor_rows.get(donor_uid), log=log)
    return blob, target["id"]


# The highscore list is saved in GLOB (version 17) at payload +13 as a count,
# then one row per Owner:4: u32 Owner:4, u32 name length, the name, u32 score
# (Owner:28), f32 Owner:32, u8 flag. Load reads it back as saved; only a turn
# boundary rebuilds it, so a civ added between boundaries needs its own row.
SCORE_GLOB_VERSION = 17
SCORE_COUNT_AT = 9
SCORE_TAIL = 9


def score_rows(blob):
    """(GLOB payload offset, {Owner:4: row tail}, offset past the last row),
    or None for a GLOB this layout was not measured on."""
    glob = next(sp.parse_blob(blob)[0].find("GLOB"), None)
    if glob is None or glob.version != SCORE_GLOB_VERSION:
        return None
    p = glob.payload
    n = struct.unpack_from("<I", blob, p + SCORE_COUNT_AT)[0]
    at, rows = p + SCORE_COUNT_AT + 4, {}
    for _ in range(n):
        uid, ln = struct.unpack_from("<II", blob, at)
        rows[uid] = blob[at + 8 + ln:at + 8 + ln + SCORE_TAIL]
        at += 8 + ln + SCORE_TAIL
    return p, rows, at


def add_score_row(blob, uid, name, donor_tail, log=print):
    """`blob` with a highscore row for `uid`, carrying the donor's score, which
    the clone's OWNR also carries. A list with no rows yet (turn 0) is left for
    the first boundary to fill."""
    got = score_rows(blob)
    if got is None:
        log("  score list: GLOB is not the measured version, left alone")
        return blob
    p, rows, end = got
    if not rows or uid in rows:
        return blob
    nb = name.encode("ascii")
    tail = donor_tail or struct.pack("<IfB", 0, 0.0, 0)
    out = bytearray(splice(blob, end, end,
                           struct.pack("<II", uid, len(nb)) + nb + tail))
    struct.pack_into("<I", out, p + SCORE_COUNT_AT, len(rows) + 1)
    log(f"  score list: row {uid} {name!r} added, {len(rows)} -> {len(rows) + 1}")
    return bytes(out)


def describe(blob, log=print):
    owners = owner_records(blob)
    planets = planet_records(blob)
    ca = civ_count_at(blob)
    log(f"civ count field @{ca} reads "
        f"{struct.unpack_from('<I', blob, ca)[0]}; {len(owners)} OWNR record(s)")
    seen = {}
    donor = seat_one(owners)["name"] if owners else None
    for o in owners:
        mine = [p for p in planets if p["owner"] == o["oid"]]
        uid = struct.unpack_from("<I", blob, o["end"] - 4)[0]
        clash = f"  <-- SAME Owner:4 AS {seen[uid]!r}" if uid in seen else ""
        seen.setdefault(uid, o["name"])
        log(f"  {o['name']!r:16} id={o['oid']:<6} Owner:4={uid:<4} "
            f"OWNR {o['ln']:>6} bytes  {len(mine)} planet(s): "
            f"{', '.join(str(p['id']) for p in mine)}"
            f"{'  <-- default donor' if o['name'] == donor else ''}{clash}")
    free = [p for p in planets if not p["owner"]]
    log(f"  {len(planets)} planet(s), {len(free)} uncolonised")
    dests = ship_destinations(blob)
    reserved = {pid for d in dests for pid in d["planets"]}
    log(f"  {len(dests)} ship(s) under orders, reserving {len(reserved)} "
        f"uncolonised planet(s) from placement")
    for d in dests:
        where = (f"planet {d['planets'][0]}" if d["kind"] == "planet"
                 else f"a system of {len(d['planets'])} planet(s)"
                 if d["kind"] == "system" else "NOTHING THE BLOB NAMES")
        log(f"    ship {d['ship']:<6} owner {d['owner']:<6} order type "
            f"{d['order_type']} progress {d['progress']:>8.2f} -> {where}")
    log(f"  system spacing (median nearest neighbour) {system_spacing(blob):.0f}")
    log(f"  high-water object id "
        f"{struct.unpack_from('<I', blob, idg.HIGH_WATER_ID)[0]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture",
                    help=".b64 wire capture or raw .dat blob to edit")
    ap.add_argument("--name", action="append", default=[],
                    help="name of a civ to add; repeatable")
    ap.add_argument("--home", action="append", default=[], type=int,
                    help="planet object id for the matching --name; "
                         "auto-picked when omitted")
    ap.add_argument("--userid", action="append", default=[], type=int,
                    help="Owner:4 for the matching --name; -1 or omitted takes "
                         "max(existing) + 1. Pass a value another civ already "
                         "holds to reproduce the missing-from-highscores bug")
    ap.add_argument("--margin", type=float, default=None,
                    help="refuse an auto-picked homeworld closer than this to "
                         "anything already claimed or under way. Default is the "
                         "galaxy's own median system spacing; 0 disables it")
    ap.add_argument("--donor", default=None,
                    help="civ to clone (default: the lowest object id, which "
                         "is the seat the generator filled first and the one "
                         "carrying the homeworld customisation)")
    ap.add_argument("--no-starting-kit", action="store_true",
                    help="clone the donor as it stands instead of levelling "
                         "the newcomer to a generated civ's start. In a played "
                         "galaxy that hands them the donor's capital, design "
                         "book and bank")
    ap.add_argument("--ships", type=int, default=None,
                    help=f"hulls for each new civ (default {STARTING_SHIPS}, "
                         f"which is what a generation hands one)")
    ap.add_argument("-o", "--out", default=None, help="output .b64")
    ap.add_argument("--dat", default=None,
                    help="also write the decompressed blob here, ready to be "
                         "passed to the client on its command line")
    ap.add_argument("--list", action="store_true",
                    help="describe the capture and exit")
    a = ap.parse_args()

    blob = sp.load_any(a.capture)
    print(f"{os.path.basename(a.capture)}: {len(blob):,} bytes")
    describe(blob)
    if a.list or not a.name:
        return

    taken = []
    for i, name in enumerate(a.name):
        home = a.home[i] if i < len(a.home) else None
        uid = a.userid[i] if i < len(a.userid) else -1
        blob, used = add_civ(blob, name, donor_name=a.donor, home_id=home,
                             taken=taken, user_id=None if uid < 0 else uid,
                             margin=a.margin, kit=not a.no_starting_kit,
                             ships=a.ships)
        taken.append(used)

    print()
    describe(blob)

    out = a.out or os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "loadgame_blob.b64")
    enc = sp.encode_save(blob)
    with open(out, "w") as f:
        f.write(enc if isinstance(enc, str) else enc.decode("ascii"))
    print(f"\nwrote {out} ({len(blob):,} bytes of blob)")
    if a.dat:
        with open(a.dat, "wb") as f:
            f.write(blob)
        print(f"wrote {a.dat}")


if __name__ == "__main__":
    main()
