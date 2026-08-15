"""
expand.py — the Expand rules (STRATEGY.md §4.2)
===============================================
R-XPN-01  build a colony ship when we have none and somewhere to send it
R-XPN-02  score colony targets and dispatch every available colony ship
R-XPN-03  bootstrap a colony the turn it is founded

Runnable on its own for one pass, or driven every turn by ai.py. Both go through
the same `run_*` functions, so what you see in a dry run is exactly what the
loop does.

    python expand.py --civ GoodGuy                 # dry run, prints the ranking
    python expand.py --civ GoodGuy --apply
    python expand.py --civ GoodGuy --top 20
    python expand.py --civ GoodGuy --target 232 --apply    # override one pick

── Scoring ────────────────────────────────────────────────────────────────────
    score(T) = space(T) - TURN_WEIGHT * eta(T) + SAME_SYSTEM_BONUS

Distance is charged in TURNS, not raw units, because the two are not
interchangeable at this galaxy's scale: coordinates run to ~1130, the closest
two suns are 111 apart, and a colony ship moves 13.5 per turn, so the nearest
neighbouring system is already an 8-turn trip while `space` only spans 156..450.
Charging raw distance made distance swamp space about 4:1 and the rule
degenerated to "always take the closest rock".

`space` is used rather than `max_pop` because `max_pop = space/10` truncates and
throws away the tie-break between, say, 288 and 281.
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

# A colony ship counts as available when it is idle (0), merely moving (1) or
# already colonising (3). Move carries no colonisation commitment — a colony
# ship in transit is exactly the thing a human would redirect. Attack and
# conquer are purposeful and are left alone.
#
# SCOUT (2) is a special case. The engine does NOT clear a scout order when the
# ship arrives, so the order type stays 2 for ever. Excluding it outright left a
# ship parked in a system it had just revealed, four unowned planets beside it,
# ignored by this rule for thirty-five turns. A scout whose target system is
# already in the discovery set has finished its job, and that is the test used
# below rather than the order type alone.
AVAILABLE_ORDERS = (0, 1, 3)


def ship_speed(ship, act=None):
    """The ship's speed, warming the engine's cache if it is cold.

    The derived stat block is a lazy cache, so a design reading -1 is valid and
    merely uncached — the engine fills it on the first getter call. Skipping
    such a ship froze the AI after every load. If an elevated handle is
    available, call the getter (0x005480A0) which both answers and caches;
    otherwise return None and let the caller skip this turn.

    Never substitute a default. A silent fallback of 1.0 once turned a 24-unit
    hop into a '24 turn' ETA and reordered the whole ranking while looking
    perfectly plausible on screen.
    """
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

    'all'   — everything in client memory, including undiscovered systems.
    'known' — DEFAULT. Only systems the civ has actually discovered: one it
              holds a planet in, one a ship has been inside, or one named by a
              scan report. Client memory holds every planet in the galaxy
              whether or not it has been found, so reading it directly is
              cheating — in the real game a system's planets are revealed by
              flying to its SUN, which is why the scout order targets a star.
    """
    unowned = snap.unowned_planets()
    if vision == "known" and hist is not None:
        return [p for p in unowned if hist.can_see(snap, p)]
    if vision == "all":
        return unowned
    home_suns = {snap.nearest_sun(p)[0] for p in snap.owned_planets(civ)}
    return [p for p in unowned if snap.nearest_sun(p)[0] in home_suns]


def scout_finished(snap, ship, hist):
    """True when a scout order has already delivered what it was for.

    The engine leaves `Ship:52` at 2 after arrival, so "is it scouting?" is not
    the same question as "does it still have something to scout". Ask the
    discovery set instead: if the system it was sent to is known, the mission is
    over and the ship is free.
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


# ── R-XPN-01 ───────────────────────────────────────────────────────────────
COLONY_SHIP_TARGET = 1     # colony ships to keep in existence


def run_xpn01(snap, civ, act, log=print):
    """Build a colony ship when we have none and somewhere to send it.

    Uses only actuator D, which is two plain pointer writes — no allocation and
    no remote calls — so this rule works even with no elevated handle.

    Deliberately conservative about WHEN to build:
      * only when below the target count, so it does not spam shipyards
      * only if a colonisable target exists, so we never pay upkeep for a ship
        with nowhere to go (the cheap half of R-XPN-04)
      * only on a planet that is idle; a planet mid-build is left alone rather
        than having its progress silently replaced
    """
    colony_ships = [s for s in snap.owned_ships(civ) if s.role == "COLONY"]
    if len(colony_ships) >= COLONY_SHIP_TARGET:
        return 0

    # OURS ONLY. Every civ's designs are visible in memory, and filtering by
    # "has a warm stat cache" instead of by owner made this rule select the
    # RIVAL's Colony Ship — ours was excluded merely because its cache was cold.
    designs = [d for d in snap.designs
               if d.is_colony and d.owner is not None
               and d.owner.addr == civ.addr and d.chassis and d.engines]
    if not designs:
        log("R-XPN-01: this civ has no COLONY design with parts")
        return 0
    design = min(designs, key=lambda d: d.cost if d.cost is not None else 1 << 30)

    if not snap.unowned_planets():
        log("R-XPN-01: nowhere left to colonise; not building")
        return 0

    # NO CASH GATE. A ship is paid for in PRODUCTION: the build accumulates in
    # Planet:120 until it reaches the design cost, and cash is untouched
    # throughout — watched across a whole build in an earlier game, cash rose
    # steadily and never dropped by the ship's price. Gating on cash stalled
    # this rule for turns at a time waiting for a currency that is not spent.

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

    # Population is the available stand-in for production rate until the
    # derived-by-observation sensors (STRATEGY.md §2.5) are wired up.
    planet = max(idle, key=lambda p: len(p.population))
    log(f"R-XPN-01: {len(colony_ships)}/{COLONY_SHIP_TARGET} colony ships — "
        f"queueing {design.design_name!r} (cost {design.cost}) at {planet}")
    act.queue_ship(planet, design)
    return 1


# ── R-XPN-02 ───────────────────────────────────────────────────────────────
def run_xpn02(snap, civ, act, vision="known", top=10, forced=None,
              hist=None, log=print):
    """Dispatch every available colony ship to a distinct best target."""
    colony = [s for s in snap.owned_ships(civ) if s.role == "COLONY"]
    available = [s for s in colony
                 if (s.order_type or 0) in AVAILABLE_ORDERS
                 or scout_finished(snap, s, hist)]
    # Crew is a hard precondition. A crewless ship cannot move and the engine
    # cancels the order at the next turn boundary, so ordering one anyway burns
    # an engine allocation every turn while the log reads like success.
    crewless = [s for s in available if not act.is_crewed(s)]
    available = [s for s in available if act.is_crewed(s)]
    available.sort(key=lambda s: (not gs.is_ptr(s.order_ptr), s.id))
    for s in crewless:
        log(f"  SKIP {s}: no crew — it cannot move, and the engine would "
            f"cancel any order at the next turn boundary")

    log(f"R-XPN-02: {len(colony)} colony ship(s), {len(available)} available "
        f"({sum(1 for s in available if gs.is_ptr(s.order_ptr))} retargetable)")
    if not colony:
        log("  no colony ship — that is R-XPN-01, which needs a build rule")
        return 0
    if not available:
        log("  every colony ship is busy with a non-colonize order")
        return 0

    candidates = visible_targets(snap, civ, vision, hist)
    if vision == "known" and hist is not None:
        log(f"  vision=known: {len(hist.discovered)} system(s) discovered, "
            f"{len(candidates)} target(s) we are entitled to see")

    # Do not send two ships to the same rock. An in-flight colonize order claims
    # its target, but a ship we are about to reconsider must not block itself.
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
                f"{ship.design} — the stat cache is cold and no elevated "
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
        log(f"    choice: Planet #{best.id} — space {best.space}, "
            f"{best_eta} turns{', same system' if best_same else ''}")
        claimed.add(best.id)

        # Already going there? Leave it alone. Rewriting an order every turn
        # works — the ship still arrives — but it resets the route leg origin
        # to the ship's current position and zeroes progress each time, so the
        # progress field never accumulates and the writes are pure noise.
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


# ── R-XPN-03 ───────────────────────────────────────────────────────────────
def run_xpn03(snap, civ, act, log=print):
    """Bootstrap a colony on the turn it is founded.

    Everything on a new colony goes to farming first: a colony that starves
    undoes the expansion that created it, and food is the one output with no
    substitute. The build selection is set to a farm for the same reason.

    Uses only actuator A (a plain int per citizen) and B (a type id), so this
    rule needs no allocation and no remote calls.
    """
    if snap.turn == 0:
        # Planet:92 reads 0 on a homeworld AND on every uncolonised planet, so
        # at turn 0 "founded this turn" matches the homeworld — which starts
        # with a deliberate 4 farmer / 2 worker / 1 scientist mix that this rule
        # would flatten to all-farmers. A real colony cannot be founded before
        # turn 1, so turn 0 is never this rule's business.
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


# Planet housekeeping before ship dispatch: a colony founded this turn should
# be set up before the same pass decides where the next ship goes.
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
    print(f"\n=== Expand — turn {snap.turn} — civ {civ.civ_name!r} — "
          f"{'DRY RUN' if dry_run else 'APPLYING'} — vision={vision} ===")

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
