"""
expand.py: the Expand rules (STRATEGY.md §4.2).

R-XPN-01  build a colony ship when we have none and somewhere to send it
R-XPN-02  score colony targets and dispatch every available colony ship
R-XPN-03  bootstrap a colony the turn it is founded

Runs one pass on its own, or every turn from ai.py, through the same `run_*`
functions. Targets score space - TURN_WEIGHT * eta + SAME_SYSTEM_BONUS.

    python expand.py --civ GoodGuy                 # dry run, prints the ranking
    python expand.py --civ GoodGuy --apply
    python expand.py --civ GoodGuy --top 20
    python expand.py --civ GoodGuy --target 232 --apply    # override one pick
"""
import math
import struct
import sys

import actions
import cli
import gamestate as gs
import remote
import sensors

TURN_WEIGHT       = 10.0   # score points charged per turn of travel
SAME_SYSTEM_BONUS = 30.0   # shared defence, no exposed transit

# Colony ships that may be retargeted: idle (0), moving (1) or colonising (3).
# Scout (2) is never cleared on arrival; see scout_finished().
AVAILABLE_ORDERS = (0, 1, 3)


def ship_speed(ship, act=None):
    """The ship's speed, warming the engine's cache if it is cold."""
    d = ship.design
    if d is None:
        return None
    if d.speed and d.speed > 0:
        return d.speed
    rem = getattr(act, "remote", None) if act is not None else None
    if rem is None:
        return None
    import struct as _s
    bits = rem.design_speed(d.addr)
    val = _s.unpack("<f", _s.pack("<I", bits))[0]
    return val if val > 0 else None


def eta_turns(ship, target, speed):
    return math.ceil(gs.dist(ship.pos, target.pos) / speed)


def score_targets(snap, civ, ship, candidates, speed):
    """Rank colony targets. Returns [(score, planet, eta, same_system)]."""
    home_suns = {snap.nearest_sun(p)[0] for p in snap.owned_planets(civ)}
    out = []
    for t in candidates:
        eta  = eta_turns(ship, t, speed)
        same = snap.nearest_sun(t)[0] in home_suns
        s = t.space - TURN_WEIGHT * eta + (SAME_SYSTEM_BONUS if same else 0.0)
        out.append((s, t, eta, same))
    out.sort(key=lambda r: -r[0])
    return out


def visible_targets(snap, civ, vision, hist=None):
    """Unowned planets the AI is allowed to consider.

    'all':   everything in client memory, including undiscovered systems.
    'known': default. Only systems the civ has discovered.
    """
    unowned = snap.unowned_planets()
    if vision == "known" and hist is not None:
        return [p for p in unowned if hist.can_see(snap, p)]
    if vision == "all":
        return unowned
    home_suns = {snap.nearest_sun(p)[0] for p in snap.owned_planets(civ)}
    return [p for p in unowned if snap.nearest_sun(p)[0] in home_suns]


def scout_finished(snap, ship, hist):
    """True when a scout order's target system is already discovered.

    The engine leaves `Ship:52` at 2 after arrival.
    """
    if (ship.order_type or 0) != 2 or not gs.is_ptr(ship.order_ptr):
        return False
    if hist is None:
        return False
    xyz = struct.unpack("<fff", snap.read(ship.order_ptr + 16, 12))
    sun = min(snap.suns, key=lambda q: gs.dist(xyz, q.pos))
    return sun.id in hist.discovered


def _order_target(snap, ship):
    """Which planet a ship's colonize order currently points at, or None."""
    if not gs.is_ptr(ship.order_ptr):
        return None
    t = struct.unpack("<fff", snap.read(ship.order_ptr + 16, 12))
    near = min(snap.planets, key=lambda q: gs.dist(t, q.pos))
    return near if gs.dist(t, near.pos) < 1.0 else None


COLONY_SHIP_TARGET = 1     # colony ships to keep in existence


def run_xpn01(snap, civ, act, hist=None, log=print):
    """Build a colony ship when we have none and somewhere to send it.

    Uses only actuator D (plain pointer writes), so it needs no elevated
    handle. Builds only below the target count, only if a target exists
    (R-XPN-04), and only on an idle planet.
    """
    colony_ships = [s for s in snap.owned_ships(civ) if s.role == "COLONY"]
    # Count only ships able to settle: a starting colony ship can hold an
    # unfinished scout order for dozens of turns.
    settlers = [s for s in colony_ships
                if (s.order_type or 0) != 2 or scout_finished(snap, s, hist)]
    if len(settlers) >= COLONY_SHIP_TARGET:
        return 0
    if colony_ships and not settlers:
        log(f"R-XPN-01: {len(colony_ships)} colony ship(s), but every one is "
            f"away on an unfinished scout order; building a replacement so "
            f"expansion does not wait for it")

    # Our designs only; every civ's designs are in memory.
    import research
    done = {t for t, _ in civ.completed}
    designs = [d for d in snap.designs
               if d.is_colony and d.owner is not None
               and d.owner.addr == civ.addr and d.chassis and d.engines
               and research.design_legal(d, done)]
    if not designs:
        log("R-XPN-01: this civ has no COLONY design with parts")
        return 0
    design = min(designs, key=lambda d: d.cost if d.cost is not None else 1 << 30)

    if not snap.unowned_planets():
        log("R-XPN-01: nowhere left to colonise; not building")
        return 0

    # No cash gate: a ship is paid in production accumulated at Planet:120.

    yards = [p for p in snap.owned_planets(civ) if 2 in p.facilities]
    if not yards:
        log("R-XPN-01: no planet has a shipyard")
        return 0
    idle = [p for p in yards
            if not p.building_now and not act.production_claimed(p)]
    if not idle:
        log(f"R-XPN-01: all {len(yards)} shipyard planet(s) are already "
            f"building; leaving them alone")
        return 0

    # Population stands in for production rate (STRATEGY.md §2.5).
    planet = max(idle, key=lambda p: len(p.population))
    log(f"R-XPN-01: {len(settlers)}/{COLONY_SHIP_TARGET} colony ships able to "
        f"settle ({len(colony_ships)} owned); queueing "
        f"{design.design_name!r} (cost {design.cost}) at {planet}")
    act.queue_ship(planet, design)
    return 1


def run_xpn02(snap, civ, act, vision="known", top=10, forced=None,
              hist=None, log=print):
    """Dispatch every available colony ship to a distinct best target."""
    colony = [s for s in snap.owned_ships(civ) if s.role == "COLONY"]
    available = [s for s in colony
                 if (s.order_type or 0) in AVAILABLE_ORDERS
                 or scout_finished(snap, s, hist)]
    # A crewless ship cannot move; the engine cancels its order at the next turn.
    crewless = [s for s in available if not act.is_crewed(s)]
    available = [s for s in available if act.is_crewed(s)]
    available.sort(key=lambda s: (not gs.is_ptr(s.order_ptr), s.id))
    for s in crewless:
        log(f"  SKIP {s}: no crew; it cannot move, and the engine would "
            f"cancel any order at the next turn boundary")

    log(f"R-XPN-02: {len(colony)} colony ship(s), {len(available)} available "
        f"({sum(1 for s in available if gs.is_ptr(s.order_ptr))} retargetable)")
    if not colony:
        log("  no colony ship; that is R-XPN-01, which needs a build rule")
        return 0
    if not available:
        log("  every colony ship is busy with a non-colonize order")
        return 0

    candidates = visible_targets(snap, civ, vision, hist)
    if vision == "known" and hist is not None:
        log(f"  vision=known: {len(hist.discovered)} system(s) discovered, "
            f"{len(candidates)} target(s) we are entitled to see")

    # One ship per target. Ships being reconsidered do not claim their own.
    claimed = set()
    for s in colony:
        t = _order_target(snap, s) if s.order_type == 3 else None
        if t is not None and s not in available:
            claimed.add(t.id)

    acted = 0
    for ship in available:
        if act.is_claimed(ship):
            continue
        speed = ship_speed(ship, act)
        if speed is None:
            log(f"  SKIP {ship}: could not obtain a speed for "
                f"{ship.design}; the stat cache is cold and no elevated "
                f"handle is available to warm it, so every ETA would be junk")
            continue
        pool = [p for p in candidates if p.id not in claimed]
        ranked = score_targets(snap, civ, ship, pool, speed)
        if not ranked:
            log(f"  SKIP {ship}: no unclaimed target left")
            continue

        if top:
            log(f"  --- top {top} for {ship} (speed {speed:.2f}/turn) ---")
            log(f"    {'score':>8}  {'planet':>7}  {'space':>5}  {'eta':>4}  sys")
            for sc, t, eta, same in ranked[:top]:
                log(f"    {sc:8.1f}  #{t.id:<6}  {t.space:5}  {eta:4}  "
                    f"{'home' if same else '--'}")

        best_score, best, best_eta, best_same = ranked[0]
        if forced is not None:
            pick = next((r for r in ranked if r[1].id == int(forced)), None)
            if pick is None:
                log(f"  --target {forced} is not among the candidates; ignoring")
            else:
                best_score, best, best_eta, best_same = pick
                forced = None       # the override applies to one ship only
                log(f"    --target override: Planet #{best.id} "
                    f"(score {best_score:.1f})")
        log(f"    choice: Planet #{best.id}; space {best.space}, "
            f"{best_eta} turns{', same system' if best_same else ''}")
        claimed.add(best.id)

        # Rewriting an order resets the route leg and progress, so leave it.
        if ship.order_type == 3:
            cur = _order_target(snap, ship)
            if cur is not None and cur.id == best.id:
                log(f"    already en route to #{best.id}; leaving the order "
                    f"untouched")
                act.claim(ship)     # committed, even though nothing was written
                continue

        try:
            new_ptr = None
            if not gs.is_ptr(ship.order_ptr):
                new_ptr = act.create_order(ship, best, civ)
                if new_ptr is None:      # dry run, or creation refused
                    log("    [WOULD WRITE] target, origin, route leg, "
                        "progress=0, order type 3 and the pending flag")
                    act.claim(ship)
                    acted += 1
                    continue
            act.retarget_order(ship, 3, best.pos, order_ptr=new_ptr)
            act.claim(ship)
            acted += 1
        except actions.NeedsEngineOrder as e:
            log(f"    BLOCKED: {e}")
    return acted


def run_xpn03(snap, civ, act, log=print):
    """Bootstrap a colony on the turn it is founded: all farmers, build a farm.

    Uses only actuators A and B, so it needs no remote calls.
    """
    if snap.turn == 0:
        # Planet:92 reads 0 on the homeworld too; no colony is founded on turn 0.
        return 0
    acted = 0
    for p in snap.owned_planets(civ):
        if p.colonised_turn != snap.turn:
            continue
        jobs = p.population
        log(f"R-XPN-03: {p} founded this turn, pop {len(jobs)}")
        for i, job in enumerate(jobs):
            if job != 0:
                act.set_job(p, i, 0)          # 0 = farmer
                acted += 1
        if p.selected_building != 0:
            act._u32(p.addr + 284, 0, "Planet:284 -> farm")
            acted += 1
    return acted


# Colony setup runs before ship dispatch in the same pass.
RULES = [("R-XPN-03", run_xpn03),      # set up anything founded this turn
         ("R-XPN-02", run_xpn02),      # dispatch the ships we have
         ("R-XPN-01", run_xpn01)]      # then build more if we need them


def main():
    args = sys.argv[1:]
    def opt(name, default=None, cast=None):
        return cli.opt(args, name, default, cast)
    civ_name = opt("--civ")
    top      = opt("--top", 10, int)
    vision   = opt("--vision", "known")
    forced   = opt("--target")
    dry_run  = "--apply" not in args

    snap = gs.Snapshot()
    civ = gs.resolve_civ(snap, civ_name)
    if civ is None:
        sys.exit(f"no civ named {civ_name!r}; saw "
                 f"{[c.civ_name for c in snap.civs]}")
    print(f"\n=== Expand, turn {snap.turn}, civ {civ.civ_name!r}, "
          f"{'DRY RUN' if dry_run else 'APPLYING'}, vision={vision} ===")

    rem = None
    if not dry_run:
        rem = remote.Remote(snap.state.pid)
    act = actions.Actuator(snap, dry_run=dry_run, remote_=rem)
    try:
        run_xpn03(snap, civ, act)
        hist = sensors.History().observe(snap, civ)
        run_xpn02(snap, civ, act, vision=vision, top=top, forced=forced,
                  hist=hist)
        run_xpn01(snap, civ, act)
    finally:
        if rem is not None:
            rem.close()

    print(f"\n{act.summary()}")
    if dry_run:
        print("re-run with --apply to perform these writes")


if __name__ == "__main__":
    main()
