"""
explore.py — the Explore rules (STRATEGY.md §4.1)
=================================================
R-EXP-02  send a ship to an undiscovered star so its system is revealed

    python explore.py            # dry run, one pass
    python explore.py --apply

── Why this exists, and why it is now the bottleneck ──────────────────────────
Client memory holds every planet in the galaxy whether or not it has been found.
Reading them all is cheating, so R-XPN-02 only considers systems in the
discovery set. The honest consequence is that a civ sitting in a fully colonised
home system has nowhere legal to go: it must physically send a ship somewhere
new before it can expand again. That is the actual game.

A SCOUT ORDER TARGETS A STAR, NOT A PLANET. `Ship:52 = 2` with the order's
destination set to a Sun's exact coordinates — confirmed at 0.0000 against two
independent scouts, with the nearest planet 12.6 away. That is precisely the
mechanic that makes fog of war real: you fly to the sun, and the system's
planets become visible.

No new actuator is needed. `create_order` and `retarget_order` already take any
object with coordinates at its allocation start, and `Sun` has them at the same
+0xc/+0x10/+0x14 the constructor reads from a `Ship` or a `Planet`.
"""
import sys

import actions
import cli
import exploit
import gamestate as gs
import remote
import sensors

# A ship is "in" a system within this radius. Widest orbit measured is 39.6 and
# the tightest gap between two stars is 111.7, so anything in this band is
# unambiguous.
SYSTEM_RADIUS = 55.0

# Stop scouting once this many systems are known and still hold free planets;
# past that the civ has more room than it can use and the ship is better spent
# colonising.
ENOUGH_KNOWN_TARGETS = 3

# Dedicated scouts to keep in the field. Cheap hulls, so several is normal.
SCOUT_TARGET = 3


def scoutable(snap, civ, hist, act):
    """Ships we may send scouting.

    A ship busy with a purposeful order is left alone. A COLONIZE order whose
    target we cannot legitimately see is not purposeful — it is a leftover from
    before fog of war was enforced, and repurposing it is a correction, not an
    interruption.
    """
    out = []
    for s in snap.owned_ships(civ):
        if not act.is_crewed(s):
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

    # "Plenty to colonise, scouting can wait" is right for a COLONY ship and
    # wrong for everything else. Applying it to every ship parked three crewed
    # warships for hundreds of turns while the civ was at war and R-XTM-03 said
    # every turn that it had no visible enemy planet: the one thing that could
    # have found the enemy was being held back because there were still rocks to
    # settle. At turn 494 the civ had explored 2 of 108 systems.
    #
    # A warship has nothing better to do than look, and at war looking IS the
    # objective. Only colony ships are held back.
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
            # Expand already committed this ship this pass. The snapshot
            # still shows its old order, so without this it would be
            # redirected to a star and the colonisation quietly lost.
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


def scout_designs(snap, civ):
    """Our own PURPOSE-BUILT scouts: a chassis, engines, and nothing else.

    Deliberately stricter than the SCOUT role, which is defined by absence and so
    quietly includes any unarmed hull — a colony ship with its module stripped, a
    half-finished experiment. Those are slow and expensive. A scout is a hull
    spending its whole cost on engines, which makes it both the cheapest ship in
    the empire and the fastest.
    """
    out = []
    for d in snap.designs:
        own = d.owner
        if own is None or own.addr != civ.addr:
            continue
        # A SCANNER IS REQUIRED, not cosmetic. Every design the game itself
        # produced carries scanners=[0], and a synthesised design without
        # one crashed the client at the turn boundary when a build
        # completed and the engine tried to construct the ship. The design
        # could be selected and queued perfectly happily right up to that
        # point, which is what made it look fine.
        if (d.chassis and d.engines and d.scanners
                and not d.weapons and not d.modules):
            out.append(d)
    return out


def run_exp01(snap, civ, act, hist, log=print):
    """R-EXP-01: keep dedicated scouts in the field.

    Exploration is the gate on everything under fog of war — expansion needs
    targets and Exterminate needs an enemy — and doing the looking with colony
    ships is why a galaxy took hundreds of turns to survey. A scout is cheap
    enough to build several and fast enough that each one covers far more ground.
    """
    have = [s for s in snap.owned_ships(civ) if s.role == "SCOUT"]
    if len(have) >= SCOUT_TARGET:
        return 0
    stranded = exploit.awaiting_crew(snap, civ, act, role="SCOUT")
    if stranded:
        log(f"R-EXP-01: {len(stranded)} scout(s) already built and waiting "
            f"for crew; not queueing another until they can fly")
        return 0
    designs = scout_designs(snap, civ)
    if not designs:
        log(f"R-EXP-01: {len(have)}/{SCOUT_TARGET} scout(s) and no "
            f"engines-only design exists. Synthesise one: game_cycle.py "
            f"--design 'scout:chassis=0,engine=<fastest unlocked>'")
        return 0
    yards = [p for p in snap.owned_planets(civ)
             if exploit.SHIPYARD in p.facilities and not p.building_now
             and not act.production_claimed(p)]
    if not yards:
        return 0
    # Fastest, not cheapest: the whole point of the hull is speed, and speed is
    # what decides how much galaxy one ship covers before the game is decided.
    # speed is a lazy stat and reads None until warmed, so engine count is
    # the tiebreak that still ranks sensibly on a cold cache.
    best = max(designs, key=lambda d: (d.speed or 0, len(d.engines)))
    planet = max(yards, key=lambda p: len(p.population))
    log(f"R-EXP-01: {len(have)}/{SCOUT_TARGET} scout(s) — queueing "
        f"{best.design_name!r} (speed {best.speed}, {len(best.engines)} engine(s)) "
        f"at {planet}")
    try:
        act.queue_ship(planet, best)
        return 1
    except (ValueError, RuntimeError) as e:
        log(f"  could not queue: {e}")
        return 0


RULES = [("R-EXP-01", run_exp01), ("R-EXP-02", run_exp02)]


def main():
    args = sys.argv[1:]
    dry_run = not cli.flag(args, "--apply")
    snap = gs.Snapshot()
    civ = gs.resolve_civ(snap, cli.opt(args, "--civ"))
    if civ is None:
        return
    print(f"\n=== Explore — turn {snap.turn} — {civ.civ_name!r} — "
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
