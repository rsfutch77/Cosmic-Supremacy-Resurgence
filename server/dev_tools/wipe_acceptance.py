"""
wipe_acceptance.py: does a galaxy with a civ wiped out of it still play?
    python wipe_acceptance.py prepare  --civ Neighbor
    python wipe_acceptance.py load     --which wiped
    python wipe_acceptance.py ticks    --turns 20 --runs 2
    python wipe_acceptance.py colonise --which wiped  --planet 138
    python wipe_acceptance.py colonise --which base   --planet 138

    python wipe_acceptance.py prepare  --case governed
    python wipe_acceptance.py ticks    --turns 3 --runs 1

`game_cycle.launch` waits for a local civ with at least one planet, so
`prepare` stamps every blob it writes for a civ that survives the wipe.

`--case governed` wipes DemoPlayer out of
`server/tests/fixtures/governors/g_switched.dat`, where its governor runs its
homeworld, and stamps the blobs for BadGuy. The governor's record is first
replaced by `CRASHING_GOVERNOR`, since the fixture's own rule runs harmlessly
on an unowned planet and this one closes the client. A wipe that leaves the
homeworld linked to it fails `ticks`; one that clears the link passes.
`prepare` refuses the case if the victim governs no planet.

`ticks` runs without the referee's own repair of governor links
(`referee.tick(ungovern=False)`), so it tests the wipe and not the repair that
covers for it. `--repair` puts the repair back.

The runs, and what counts as a failure:

    load      the client comes up on the wiped galaxy and its state is
              readable. Failure: the process exits during the load, or the
              state never becomes readable inside the timeout.
    ticks     the wiped galaxy ticks N turns, twice, from separate process
              launches, and the two agree on their canonical hash; the result
              is then ticked again to show the post-tick blob loads.
              Failure: a tick that does not reach the target turn, two runs
              that disagree, or a third launch that will not open the result.
    colonise  a surviving civ is given a colony ship and an order to settle a
              planet the wiped civ used to own, and the galaxy is ticked until
              it arrives. Failure: the planet does not change hands.

`colonise --which base` is the control: the same injection against the galaxy
with the civ not wiped, where the planet is still owned, expecting the
colonisation to fail.

Nothing here writes to a turn store. The galaxy under test is a copy on disk.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
REPO = os.path.dirname(SERVER)
CLIENT_DEV = os.path.join(REPO, "client", "dev_tools")
for d in (HERE, SERVER, CLIENT_DEV, os.path.join(CLIENT_DEV, "ai_player")):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import inject_civ as icv
import inject_order as ior
import inject_ship as ish
import set_blob_player
import wipe_civ
import canonical
import merge_orders

WORK = os.path.join(SERVER, "wipe_work")
DEFAULT_FIXTURE = os.path.join(SERVER, "galaxy_demo", "turns", "0011.b64")
# fixture, the civ wiped, the civ the blobs are stamped for
CASES = {
    "governed": (os.path.join(SERVER, "tests", "fixtures", "governors",
                              "g_switched.dat"), "DemoPlayer", "BadGuy"),
}
# A whole GVNR section, named "new", whose one rule is a production rule
# (RLPR) with condition type 6. A planet left linked to it after its owner is
# zeroed closes the client at end of turn; the fixture's own governors do not.
# It is copied from the sandbox's turn 238 and holds no player's data.
CRASHING_GOVERNOR = bytes.fromhex(
    "47564e52410000006f01000003000000004756445430000004030000006e657701"
    "000100000052554c450100000401524c50521200000000000000434f4e44060000"
    "04000100000000")
COLONISE = 3                  # SHCO order type; 1 is a move


def log(msg=""):
    print(msg, flush=True)


def path(name):
    return os.path.join(WORK, name)


def load(name):
    return sp.load_any(path(name))


def turn_of(blob):
    tree = sp.parse_blob(blob)
    return struct.unpack_from("<I", blob, next(tree[0].find("GLOB")).payload)[0]


def owner_of(blob, planet_id):
    return next(p["owner"] for p in icv.planet_records(blob)
                if p["id"] == planet_id)


def write(name, blob):
    os.makedirs(WORK, exist_ok=True)
    with open(path(name), "wb") as f:
        f.write(blob)
    log(f"  wrote {name} ({len(blob):,} bytes, turn {turn_of(blob)})")


def governed_planets(blob, civ):
    """The planets `civ` owns that name a governor."""
    oid = next(o["oid"] for o in icv.owner_records(blob) if o["name"] == civ)
    links = merge_orders.governor_links(blob)
    return [p["id"] for p in icv.planet_records(blob)
            if p["owner"] == oid and links.get(p["id"])]


def with_crashing_governor(blob, civ):
    """`blob` with the civ's one governor given `CRASHING_GOVERNOR`'s record,
    keeping its object id and the count of planets it governs."""
    oid = next(o["oid"] for o in icv.owner_records(blob) if o["name"] == civ)
    govs = merge_orders.governors_of(blob, oid) or []
    if len(govs) != 1:
        raise SystemExit(f"{civ!r} holds {len(govs)} governor(s); the governed "
                         f"case replaces exactly one")
    gid, record = govs[0]
    count = struct.unpack_from("<I", record, 8 + merge_orders.GVNR_GOVERNS_AT)[0]
    swapped = merge_orders.with_governed(
        merge_orders.with_governor_id(CRASHING_GOVERNOR, gid), count)
    log(f"  governor {gid}: record replaced by the crashing governor's "
        f"({len(swapped)} bytes), still governing {count}")
    return merge_orders.set_governors(blob, oid, [swapped])


def prepare(fixture, civ, stamp=None, need_governed=False,
            crashing_governor=False):
    """Write base.dat and wiped.dat, both stamped for a surviving civ."""
    blob = sp.load_any(fixture)
    log(f"{os.path.basename(fixture)}: {len(blob):,} bytes, turn {turn_of(blob)}")
    icv.describe(blob, log=log)
    if crashing_governor:
        blob = with_crashing_governor(blob, civ)
    governed = governed_planets(blob, civ)
    log(f"\n{civ!r} governs planet(s) {governed or 'none'}")
    if need_governed and not governed:
        raise SystemExit(f"{civ!r} governs no planet here, so this is not the "
                         f"governed case")

    survivors = [o["name"] for o in icv.owner_records(blob) if o["name"] != civ]
    if not survivors:
        raise SystemExit(f"{civ!r} is the only civ in this galaxy")
    stamp = stamp or survivors[0]
    if stamp == civ:
        raise SystemExit(f"refusing to stamp the blob for {civ!r}, the civ "
                         f"being wiped: it will own no planet and the launch "
                         f"check would report a load failure that is not one")
    log(f"\nstamping both blobs for {stamp!r}, a civ that survives the wipe")

    base = set_blob_player.set_player(blob, stamp, log=log)
    wiped, planets, ships = wipe_civ.wipe(blob, civ, log=log)
    wiped = set_blob_player.set_player(wiped, stamp, log=log)
    wipe_civ.check(wiped, civ, planets, ships, log=log)

    write("base.dat", base)
    write("wiped.dat", wiped)
    log(f"\n{civ!r} held planets {planets} and ships {ships}")
    log(f"  colonisation target: any of {planets}")
    return planets, ships


def load_check(which, timeout=180):
    """Launch the client on one blob and report whether it opened the galaxy."""
    import game_cycle as gc
    dat = path(f"{which}.dat")
    log(f"\n=== load {which}.dat ===")
    snap = gc.restart(dat, purpose=f"K4 load check, {which}",
                      wait_for_lock=240.0)
    civ = None
    try:
        import gamestate as gs
        civ = gs.resolve_civ(snap, None, quiet=True)
    except Exception:
        pass
    log(f"  opened: turn {snap.turn}, {len(snap.suns)} sun(s), "
        f"local civ {civ.civ_name if civ else '?'}")
    gc.close_client()
    return snap.turn


def tick_run(which, turns, label, secs=10, repair=False):
    """One referee tick of N turns from a cold launch. Returns the result.

    Without `repair` the referee leaves governor links as the blob has them.
    """
    import referee
    blob = load(f"{which}.dat")
    start = turn_of(blob)
    log(f"\n=== tick {which} {turns} turn(s), run {label}, referee repair "
        f"{'on' if repair else 'off'} ===")
    out = referee.tick(blob, turns=turns, secs=secs, log=log, ungovern=repair)
    got = turn_of(out)
    h = canonical.canonical_hash(out)
    log(f"  turn {start} -> {got}, {len(out):,} bytes, canonical {h[:16]}")
    if got < start + turns:
        log(f"  FAIL: asked for {turns} turns and got {got - start}")
    write(f"{which}.after.{label}.dat", out)
    return out, got, h


def order(blob, ship_id, target, order_type, log=log):
    """Attach an order to one ship, the three edits a `DYNO` needs.

    The same construction as `inject_order.py`: the `SHCO` type byte, the
    has-orders byte, and an appended `ROUT` carrying one leg from the ship's
    position to the target.
    """
    tree = sp.parse_blob(blob)
    match = [t for t in ior.ship_sections(blob, tree) if t[0] == ship_id]
    if not match:
        raise SystemExit(f"no ship with object id {ship_id}")
    _sid, _owner, pos, _sh, dyno = match[0]
    d = ior.parse_dyno(blob, dyno)
    leg = sp.make_leg(pos, target)
    rout = sp.build_rout({
        "order_kind": 1,           # non-zero or Ship::SetOrder frees the order
        "flag_1": 0,
        "origin_x": pos[0], "origin_y": pos[1], "origin_z": pos[2],
        "target_x": target[0], "target_y": target[1], "target_z": target[2],
        "legs_a": [leg], "legs_b": [],
        "progress": 0.0,
        "field_80": struct.unpack("<I", struct.pack("<f", pos[0]))[0],
        "field_84": struct.unpack("<I", struct.pack("<f", pos[1]))[0],
        "field_88": struct.unpack("<I", struct.pack("<f", pos[2]))[0],
        "ref_id": 0,
    }, version=1)[8:]
    d["shco"][0] = order_type
    d["has_orders"] = 1
    d["rout"] = (1, rout)
    d["tail_u32"] = d["tail_u32"] or 0
    out = sp.replace_payload(blob, tree, dyno, ior.build_dyno(d))

    # Re-parse to confirm the splice, as inject_order does before writing.
    got = [t for t in ior.ship_sections(out, sp.parse_blob(out))
           if t[0] == ship_id]
    if not got:
        raise SystemExit("the re-parse lost the ship")
    back = ior.parse_dyno(out, got[0][4])
    if not back["rout"] or back["shco"][0] != order_type:
        raise SystemExit("the re-parse did not see the injected order")
    log(f"  ship {ship_id} order type {order_type}, leg {leg['length']:.1f} "
        f"units, DYNO {dyno.size} -> {len(ior.build_dyno(d))} bytes")
    return out


def stage_colonisation(blob, civ, planet_id, log=log):
    """Give `civ` a colony ship over its own world, ordered to settle a planet.

    The ship and order are injected, so no production turns are spent.
    """
    import inject_design as idg
    owner = next(o for o in icv.owner_records(blob) if o["name"] == civ)
    design = next((oid for off, oid, _name in idg.design_records(blob)
                   if owner["off"] < off < owner["end"]), None)
    if design is None:
        raise SystemExit(f"{civ!r} owns no design to build a ship on")
    blob = ish.add_ship(blob, civ, design_id=design, log=log)
    ship = max(s["id"] for s in ish.ship_records(blob))

    target = next(p for p in icv.planet_records(blob) if p["id"] == planet_id)
    blob = order(blob, ship, target["pos"], COLONISE, log=log)
    log(f"  ship {ship} ordered to colonise planet #{planet_id} at "
        f"({target['pos'][0]:.0f}, {target['pos'][1]:.0f}, "
        f"{target['pos'][2]:.0f}), whose owner reads {target['owner']}")
    return blob, ship


def colonise_run(which, civ, planet_id, turns, secs=10):
    import referee
    blob = load(f"{which}.dat")
    before = owner_of(blob, planet_id)
    log(f"\n=== colonise {which}.dat: {civ!r} -> planet #{planet_id} "
        f"(owner {before}) ===")
    blob, ship = stage_colonisation(blob, civ, planet_id)
    write(f"{which}.colonise.dat", blob)
    out = referee.tick(blob, turns=turns, secs=secs, log=log)
    after = owner_of(out, planet_id)
    mine = next(o["oid"] for o in icv.owner_records(out) if o["name"] == civ)
    write(f"{which}.colonised.dat", out)
    log(f"  planet #{planet_id} owner {before} -> {after} "
        f"({civ!r} is {mine})")
    return before, after, mine


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("step", choices=["prepare", "load", "ticks", "colonise"])
    ap.add_argument("--case", choices=sorted(CASES),
                    help="a named fixture, victim and stamp; overrides "
                         "--fixture, --civ and --stamp")
    ap.add_argument("--repair", action="store_true",
                    help="ticks: let the referee unlink governors from "
                         "unowned planets first, as it does live")
    ap.add_argument("--fixture", default=DEFAULT_FIXTURE)
    ap.add_argument("--civ", default="Neighbor", help="the civ to wipe")
    ap.add_argument("--stamp", default=None,
                    help="which surviving civ the blobs are stamped for")
    ap.add_argument("--which", default="wiped",
                    help="the stem of a .dat in server/wipe_work. 'wiped' and "
                         "'base' are what prepare writes; a result such as "
                         "'wiped.after.1' can be named to tick it again")
    ap.add_argument("--turns", type=int, default=20)
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--secs", type=int, default=10)
    ap.add_argument("--planet", type=int, default=None)
    ap.add_argument("--player", default="DemoPlayer",
                    help="the surviving civ that does the colonising")
    a = ap.parse_args()

    if a.step == "prepare":
        if a.case:
            fixture, civ, stamp = CASES[a.case]
            governed = a.case == "governed"
            prepare(fixture, civ, stamp=stamp, need_governed=governed,
                    crashing_governor=governed)
        else:
            prepare(a.fixture, a.civ, stamp=a.stamp)
        return 0

    if a.step == "load":
        load_check(a.which)
        return 0

    if a.step == "ticks":
        results = [tick_run(a.which, a.turns, str(i + 1), secs=a.secs,
                            repair=a.repair)
                   for i in range(a.runs)]
        hashes = {h for _b, _t, h in results}
        log(f"\n=== {a.runs} run(s) of {a.turns} turns on {a.which}.dat ===")
        for i, (_b, t, h) in enumerate(results):
            log(f"  run {i + 1}: turn {t}, canonical {h}")
        log(f"  agree: {len(hashes) == 1}")
        if len(hashes) != 1:
            a0, b0 = results[0][0], results[1][0]
            for off, x, y in canonical.differences(a0, b0):
                log(f"    {off:#08x} {x:#04x} -> {y:#04x} "
                    f"{canonical.locate(a0, off)}")
        return 0

    if a.step == "colonise":
        if a.planet is None:
            raise SystemExit("--planet is required: name a planet the wiped "
                             "civ used to own")
        before, after, mine = colonise_run(a.which, a.player, a.planet,
                                           a.turns, secs=a.secs)
        took = after == mine
        want = a.which != "base"
        expected = ("yes" if want else
                    "NO, this is the control and the planet is still owned")
        log(f"\n=== verdict, {a.which} ===")
        log(f"  planet #{a.planet}: {before} -> {after}, "
            f"{a.player} took it: {took}")
        log(f"  expected: {expected}")
        return 0 if took == want else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
