"""
explore.py: the Explore rules (STRATEGY.md §4.1)

R-EXP-02  send a ship to an undiscovered star so its system is revealed

    python explore.py            # dry run, one pass
    python explore.py --apply
"""
import sys

import actions
import cli
import exploit
import gamestate as gs
import remote
import sensors

# A ship is "in" a system within this radius. Widest orbit is 39.6 and the
# tightest gap between two stars is 111.7.
SYSTEM_RADIUS = 55.0

# Colony ships stop scouting once this many free planets are known.
ENOUGH_KNOWN_TARGETS = 3

# Dedicated scouts to keep in the field. Cheap hulls, so several is normal.
SCOUT_TARGET = 3

# A scout slower than this fraction of the fastest scout design we can build
# no longer counts toward SCOUT_TARGET, so a faster engine gets built and flown.
SCOUT_OBSOLETE = 0.5


def scoutable(snap, civ, hist, act):
    """Ships we may send scouting.

    A ship busy with a purposeful order is left alone. A COLONIZE order whose
    target we cannot legitimately see is not purposeful. Troop and armed hulls
    never scout: R-XTM-08 keeps warships at the staging planet.
    """
    out = []
    for s in snap.owned_ships(civ):
        if not act.is_crewed(s):
            continue
        if s.role in ("TROOP", "WARSHIP"):
            continue
        ot = s.order_type or 0
        if ot in (0, 1):
            out.append(s)
        elif ot == 2 and _scout_done(snap, s, hist):
            # The engine never clears a fulfilled scout order, so a ship that
            # has already revealed its target is free to be sent somewhere new.
            out.append(s)
        elif ot == 3:
            t = _colonize_target(snap, s)
            if t is None or not hist.can_see(snap, t):
                out.append(s)
    return out


def _scout_done(snap, ship, hist):
    """A scout order whose target system is already discovered is finished."""
    import struct
    if not gs.is_ptr(ship.order_ptr):
        return False
    xyz = struct.unpack("<fff", snap.read(ship.order_ptr + 16, 12))
    sun = min(snap.suns, key=lambda q: gs.dist(xyz, q.pos))
    return sun.id in hist.discovered


def _colonize_target(snap, ship):
    import struct
    if not gs.is_ptr(ship.order_ptr):
        return None
    xyz = struct.unpack("<fff", snap.read(ship.order_ptr + 16, 12))
    near = min(snap.planets, key=lambda q: gs.dist(xyz, q.pos))
    return near if gs.dist(xyz, near.pos) < 1.0 else None


def undiscovered_suns(snap, hist):
    return [s for s in snap.suns if s.id not in hist.discovered]


def free_targets_known(snap, hist):
    """Unowned planets we are already entitled to see."""
    return [p for p in snap.unowned_planets() if hist.can_see(snap, p)]


def run_exp02(snap, civ, act, hist, log=print):
    """Send each free ship to the nearest star nobody has visited."""
    known_free = free_targets_known(snap, hist)
    ships = scoutable(snap, civ, hist, act)
    if not ships:
        return 0

    # With enough known targets colony ships are held back.
    if len(known_free) >= ENOUGH_KNOWN_TARGETS:
        ships = [s for s in ships if s.role != "COLONY"]
        if not ships:
            return 0
    targets = undiscovered_suns(snap, hist)
    if not targets:
        log("R-EXP-02: every system is already discovered")
        return 0

    log(f"R-EXP-02: {len(known_free)} known free planet(s), "
        f"{len(ships)} ship(s) free to scout, "
        f"{len(targets)} undiscovered system(s)")

    claimed, acted = set(), 0
    for ship in ships:
        if act.is_claimed(ship):
            # Expand already committed this ship this pass; the snapshot
            # still shows its old order.
            continue
        speed = ship.design.speed if ship.design and ship.design.computed else None
        pool = [s for s in targets if s.id not in claimed]
        if not pool:
            break
        sun = min(pool, key=lambda s: gs.dist(ship.pos, s.pos))
        d = gs.dist(ship.pos, sun.pos)
        eta = f"{-(-d // speed):.0f}" if speed else "?"
        log(f"  {ship} -> Sun #{sun.id} at {d:.0f} units ({eta} turns)")
        claimed.add(sun.id)
        try:
            new_ptr = None
            if not gs.is_ptr(ship.order_ptr):
                new_ptr = act.create_order(ship, sun, civ)
                if new_ptr is None:
                    log("    [WOULD WRITE] scout order into the new object")
                    act.claim(ship)
                    acted += 1
                    continue
            act.retarget_order(ship, 2, sun.pos, order_ptr=new_ptr)
            act.claim(ship)
            acted += 1
        except (actions.NeedsEngineOrder, ValueError, RuntimeError) as e:
            log(f"    could not order the scout: {e}")
    return acted


def design_speed(design):
    """Speed from the stat block, else total thrust over total hull units.

    The block is a lazy cache that reads None until something warms it. The
    engine's speed is exactly thrust over units: probe1's 15.43, the Colony
    Ship's 13.5, f1's 6.43, b1's 3.38 and the engines-only shuttles all match.
    """
    if design.speed and design.speed > 0:
        return design.speed
    import exterminate
    tables = exterminate.part_tables(design.snap)
    thrust = sum(tables["engines"].get(i, {}).get("thrust", 0)
                 for i in design.engines or [])
    units = sum(tables[attr].get(i, {}).get("units", 0)
                for attr in tables for i in getattr(design, attr) or [])
    return thrust / units if thrust and units else None


def scout_designs(snap, civ):
    """Our own purpose-built scouts: chassis, engines and scanners, nothing else.

    Stricter than the SCOUT role, which includes any unarmed hull.
    """
    import research
    done = {t for t, _ in civ.completed}
    out = []
    for d in snap.designs:
        own = d.owner
        if own is None or own.addr != civ.addr:
            continue
        if not research.design_legal(d, done):
            continue
        # A scanner is required: the engine crashes constructing a ship from a
        # design without one. Game-made designs carry scanners=[0].
        if (d.chassis and d.engines and d.scanners
                and not d.weapons and not d.modules):
            out.append(d)
    return out


def run_exp01(snap, civ, act, hist, log=print):
    """R-EXP-01: keep dedicated scouts in the field."""
    designs = scout_designs(snap, civ)
    fastest = max((design_speed(d) or 0 for d in designs), default=0)
    have = [s for s in snap.owned_ships(civ) if s.role == "SCOUT"
            and not _obsolete(s, fastest)]
    if len(have) >= SCOUT_TARGET:
        return 0
    stranded = exploit.awaiting_crew(snap, civ, act, role="SCOUT")
    if stranded:
        log(f"R-EXP-01: {len(stranded)} scout(s) already built and waiting "
            f"for crew; not queueing another until they can fly")
        return 0
    if not designs:
        log(f"R-EXP-01: {len(have)}/{SCOUT_TARGET} scout(s) and no "
            f"engines-only design exists. Synthesise one: game_cycle.py "
            f"--design 'scout:chassis=0,engine=<fastest unlocked>'")
        return 0
    import exterminate
    reserved = exterminate.war_yard(snap, civ, hist, act)
    yards = [p for p in snap.owned_planets(civ)
             if exploit.SHIPYARD in p.facilities and not p.building_now
             and not act.production_claimed(p) and p is not reserved]
    if not yards:
        return 0
    best = max(designs, key=lambda d: (design_speed(d) or 0, len(d.engines)))
    planet = max(yards, key=lambda p: len(p.population))
    speed = design_speed(best)
    log(f"R-EXP-01: {len(have)}/{SCOUT_TARGET} current scout(s); queueing "
        f"{best.design_name!r} (speed {speed and round(speed, 1)}, "
        f"{len(best.engines)} engine(s)) at {planet}")
    try:
        act.queue_ship(planet, best)
        return 1
    except (ValueError, RuntimeError) as e:
        log(f"  could not queue: {e}")
        return 0


def _obsolete(ship, fastest):
    """A scout too slow to be worth counting, now that a faster one can be built."""
    d = ship.design
    speed = design_speed(d) if d is not None else None
    return bool(speed and fastest and speed < SCOUT_OBSOLETE * fastest)


RULES = [("R-EXP-01", run_exp01), ("R-EXP-02", run_exp02)]


def main():
    args = sys.argv[1:]
    dry_run = not cli.flag(args, "--apply")
    snap = gs.Snapshot()
    civ = gs.resolve_civ(snap, cli.opt(args, "--civ"))
    if civ is None:
        return
    print(f"\n=== Explore, turn {snap.turn}, {civ.civ_name!r}, "
          f"{'DRY RUN' if dry_run else 'APPLYING'} ===")
    hist = sensors.History().observe(snap, civ)
    print(f"  {len(hist.discovered)} system(s) discovered of {len(snap.suns)}")
    rem = None
    if not dry_run:
        rem = remote.Remote(snap.state.pid)
    act = actions.Actuator(snap, dry_run=dry_run, remote_=rem)
    try:
        run_exp02(snap, civ, act, hist)
    finally:
        if rem is not None:
            rem.close()
    print(f"\n{act.summary()}")
    if dry_run:
        print("re-run with --apply to perform these writes")


if __name__ == "__main__":
    main()
