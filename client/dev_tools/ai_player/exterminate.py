"""
exterminate.py: the Exterminate rules (STRATEGY.md §4.4)

R-XTM-01  keep a standing fleet
R-XTM-03  send the fleet at the weakest reachable enemy planet
R-XTM-05  pull damaged ships out

    python exterminate.py            # dry run, one pass
    python exterminate.py --apply
"""
import struct
import sys

import actions
import cli
import exploit
import gamestate as gs
import remote
import sensors

WARSHIP_TARGET = 2        # warships to keep once we are at war with anyone
TROOP_TARGET = 1          # troop hulls to keep while anything is takeable
GARRISON_WINDOW = 3       # turns after a capture (Planet:92) that count as
                          # newly taken
RETREAT_AT = 0.30         # Ship:136 below this and the ship goes home
THREAT_DIST = 120.0       # "near": one sun separation (measured minimum 111.7)

WAR = 1                   # Owner:248 relation code


_REL_CACHE = {}


def clear_relation_cache():
    """Drop the cached relation codes.

    Must be called after anything changes a relation: the cache key is
    (turn, civ, other), which a declaration does not change.
    """
    _REL_CACHE.clear()


def relation_code(snap, civ, other, act=None):
    """The relation code between two civs. None when it cannot be determined.

    0 Neutral, 1 War, 2 Cease-Fire, 3 Peace, 4 Alliance.

    Asks the engine (GetRelationCode). Owner:248 is not a std::map: it holds
    four pointers where a map holds {head, size}. Cached per (turn, civ pair).
    """
    if act is None or act.remote is None:
        return None
    key = (snap.turn, civ.addr, other.addr)
    if key not in _REL_CACHE:
        try:
            _REL_CACHE[key] = act.remote.relation_code(civ.addr, other.addr)
        except (RuntimeError, OSError):
            return None
    return _REL_CACHE[key]


def at_war_with(snap, civ, other, act=None):
    return relation_code(snap, civ, other, act) == WAR


def enemies(snap, civ, act=None):
    """Civs we are actually at war with."""
    return [c for c in snap.civs
            if c.addr != civ.addr and at_war_with(snap, civ, c, act)]


def already_heading(ship, target, order_type, tol=1.0):
    """Is this ship already under this order, aimed at this target?

    Re-issuing an order resets its progress: `retarget_order` zeroes `+76`.
    """
    if (ship.order_type or 0) != order_type:
        return False
    ptr = ship.order_ptr
    if not gs.is_ptr(ptr):
        return False
    blob = ship.snap.read(ptr + actions.ORD_TARGET, 12)
    if not blob or len(blob) < 12:
        return False
    aim = struct.unpack("<fff", blob)
    return gs.dist(aim, target.pos) <= tol


def send_to(act, ship, target, civ, order_type=1):
    """Move `ship` to `target` with a route-only order, not actuator L.

    `set_command` kills the client at the next turn boundary for both move and
    attack; create_order plus retarget_order is safe.
    """
    if already_heading(ship, target, order_type):
        act.log(f"  {ship} is already under order {order_type} for {target}; "
                f"leaving it alone; re-issuing would zero its progress")
        return ship.order_ptr
    ptr = (ship.order_ptr if gs.is_ptr(ship.order_ptr)
           else act.create_order(ship, target, civ))
    if ptr is None:
        raise RuntimeError(f"could not originate an order for {ship}")
    act.retarget_order(ship, order_type, target.pos, order_ptr=ptr)
    return ptr


def _why(snap, civ, act, foes):
    """'at war with [...]' or 'in contact with [...]', whichever is true."""
    names = [f.civ_name for f in foes]
    at_war = [f.civ_name for f in foes if at_war_with(snap, civ, f, act)]
    return (f"at war with {at_war}" if at_war
            else f"in contact with {names} (not yet at war)")


def contacted(snap, civ, hist):
    """Rival civs we have met: a planet of theirs is in our discovery set."""
    out = []
    for other in snap.civs:
        if other.addr == civ.addr:
            continue
        if any(p.owner is not None and p.owner.addr == other.addr
               and hist.can_see(snap, p) for p in snap.planets):
            out.append(other)
    return out


def production_design(snap, planet):
    """The ShipDesign this planet is currently building, if it is building one.

    Planet:296 points at the shared design in its allocation-start form
    (tag - 12) for a ship build, and at the planet's embedded Facility for a
    facility build, so matching on tag-12 distinguishes the two.
    """
    ptr = planet.u32(296)
    if not gs.is_ptr(ptr):
        return None
    return next((d for d in snap.designs if d.addr - 12 == ptr), None)


def anti_planet(design, act=None):
    """True when this design's firepower is concentrated in the third slot.

    The three firepower values are independent damage pools: the first two are
    anti-ship and the third is anti-planet.
    """
    fp = design.firepower
    # The getters write the lazy stat block, as the ship-design tab does on the
    # UI thread, so only an armed design with a cold cache is warmed.
    if (act is not None and act.remote is not None
            and design.weapons and not any(fp)):
        try:
            fp = act.remote.design_firepower(design.addr)
        except (RuntimeError, OSError):
            pass
    return fp[2] > max(fp[0], fp[1])


def warships(snap, civ):
    return [s for s in snap.owned_ships(civ) if s.role == "WARSHIP"]


def warship_designs(snap, civ, done=None):
    """Our own armed designs, buildable ones only.

    Filtered by owner and research. The derived stat block is a lazy cache
    reading -1 until a getter touches it. Single Player seeds `f1` and `b1` at
    turn 0, before their weapons are unlocked.
    """
    if done is None:
        done = {t for t, _ in civ.completed}
    import research
    out = []
    for d in snap.designs:
        own = d.owner
        if own is None or own.addr != civ.addr:
            continue
        if not d.chassis or not d.engines:
            continue
        if not d.weapons:        # cache-independent test for 'armed'
            continue
        if not research.design_legal(d, done):
            continue
        out.append(d)
    return out


def unresearched_warships(snap, civ):
    """Armed designs we own but may not build yet, with what each is waiting on."""
    import research
    done = {t for t, _ in civ.completed}
    out = []
    for d in snap.designs:
        own = d.owner
        if own is None or own.addr != civ.addr:
            continue
        if not d.weapons or research.design_legal(d, done):
            continue
        out.append((d, research.explain_illegal(d, done)))
    return out


def target_planets(snap, civ, hist, foes):
    """Enemy planets we can legitimately see, weakest first.

    Weakness is stationed military plus the strength of facilities with a
    non-zero defence class; the battle code skips every other type.
    """
    import facilities
    foe_addrs = {f.addr for f in foes}
    tbl = facilities.read_table(snap)
    out = []
    for p in snap.planets:
        own = p.owner
        if own is None or own.addr not in foe_addrs:
            continue
        if not hist.can_see(snap, p):
            continue
        defence = sum(tbl[t]["strength_1"] + tbl[t]["strength_2"]
                      for t, n in p.facilities.items()
                      for _ in range(n) if t in tbl and tbl[t]["defence_units"])
        out.append((p.military + defence, p))
    out.sort(key=lambda x: x[0])
    return out


def run_xtm01(snap, civ, act, hist, log=print):
    """R-XTM-01: keep a standing fleet once there is someone to fight.

    The trigger is contact, not war (STRATEGY.md §4.4): R-XTM-00 declares no
    war until a crewed warship exists, and this is the only rule that queues one.
    """
    foes = enemies(snap, civ, act) or contacted(snap, civ, hist)
    if not foes:
        return 0
    have = warships(snap, civ)
    queued_now = [d for d in (production_design(snap, p)
                              for p in snap.owned_planets(civ)) if d is not None]
    fleet = [s.design for s in have if s.design] + queued_now
    # The fleet needs both firepower classes: slots 1-2 hit ships, slot 3
    # hits planets.
    have_ap = any(anti_planet(d, act) for d in fleet)
    have_as = any(not anti_planet(d, act) for d in fleet)
    if len(fleet) >= WARSHIP_TARGET and have_ap and have_as:
        return 0
    stranded = exploit.awaiting_crew(snap, civ, act, role="WARSHIP")
    if stranded:
        log(f"R-XTM-01: {len(stranded)} warship(s) built and waiting for "
            f"crew; another hull would not add any firepower")
        return 0
    designs = warship_designs(snap, civ)
    if not designs:
        import research
        done = {t for t, _ in civ.completed}
        arms = sorted(research.unlocked(done, research.WEAPON))
        # A seeded galaxy owns armed designs before it may build them.
        waiting = unresearched_warships(snap, civ)
        if waiting:
            log(f"R-XTM-01: {_why(snap, civ, act, foes)} and "
                f"{len(have)}/{WARSHIP_TARGET} warship(s); "
                + "; ".join(f"{d.design_name!r} is waiting on {why}"
                            for d, why in waiting)
                + ". R-XPL-04 is prioritising the research that grants it.")
            return 0
        log(f"R-XTM-01: {_why(snap, civ, act, foes)} and "
            f"{len(have)}/{WARSHIP_TARGET} warship(s), but this civ has no "
            f"armed design."
            + (f" Weapons {arms} are unlocked, so one can be synthesised: "
               f"client/dev_tools/game_cycle.py --design "
               f"'f1:chassis=0,scanner=0,engine=0,weapon={arms[0]}'"
               if arms else
               " No weapon is unlocked yet either; R-XPL-04 is prioritising "
               "the research that grants one."))
        return 0
    yards = [p for p in snap.owned_planets(civ)
             if exploit.SHIPYARD in p.facilities and not p.u32(344)
             and not act.production_claimed(p)]
    if not yards:
        log(f"R-XTM-01: {len(have)}/{WARSHIP_TARGET} warship(s); every shipyard "
            f"is already building")
        return 0
    # Builds the missing class, counting queued hulls as well as flying ones.
    want_anti_planet = not have_ap
    pool = [d for d in designs
            if anti_planet(d, act) == want_anti_planet] or designs
    design = max(pool, key=lambda d: max(d.firepower))
    planet = max(yards, key=lambda p: len(p.population))
    log(f"R-XTM-01: {len(have)}/{WARSHIP_TARGET} warship(s); queueing "
        f"{design.design_name!r} at {planet}")
    try:
        act.queue_ship(planet, design)
        return 1
    except (ValueError, RuntimeError) as e:
        log(f"  could not queue: {e}")
        return 0


def run_xtm03(snap, civ, act, hist, log=print):
    """R-XTM-03: send crewed warships at the weakest visible enemy planet.

    A Move order is enough. Combat is positional: arriving inside the 0.8-unit
    bucket of an enemy-held planet, at war, starts a fight.
    """
    foes = enemies(snap, civ, act)
    if not foes:
        return 0
    fleet = [s for s in warships(snap, civ)
             if act.is_crewed(s) and not act.is_claimed(s)
             and (s.condition is None or s.condition >= RETREAT_AT)]
    if not fleet:
        return 0
    ranked = target_planets(snap, civ, hist, foes)
    if not ranked:
        log(f"R-XTM-03: at war with {[f.civ_name for f in foes]} but no enemy "
            f"planet is visible yet; Explore has to find one first")
        return 0
    weakness, target = ranked[0]
    log(f"R-XTM-03: {len(fleet)} warship(s) -> {target} "
        f"(weakness {weakness}, weakest of {len(ranked)} visible)")
    acted = 0
    for ship in fleet:
        need = exploit.min_crew(ship, act)
        if len(ship.crew) < need:
            log(f"  {ship} has {len(ship.crew)}/{need} crew and would "
                f"contribute zero firepower; leaving it")
            continue
        try:
            # Move (1) via send_to; combat triggers on co-location.
            send_to(act, ship, target, civ, order_type=1)
            act.claim(ship)
            acted += 1
        except (ValueError, RuntimeError) as e:
            log(f"  could not order {ship}: {e}")
    return acted


def run_xtm04(snap, civ, act, hist, log=print):
    """R-XTM-04: land troops on an undefended enemy planet and take it."""
    foes = enemies(snap, civ, act)
    if not foes:
        return 0
    troopers = [s for s in snap.owned_ships(civ)
                if s.role == "TROOP" and not act.is_claimed(s)]
    if not troopers:
        return 0

    ranked = target_planets(snap, civ, hist, foes)
    undefended = [(w, p) for w, p in ranked if w == 0]
    if not undefended:
        log(f"R-XTM-04: {len(troopers)} troop ship(s) but every visible enemy "
            f"planet is defended; that is R-XTM-03's job first")
        return 0

    ours = snap.owned_planets(civ)
    acted = 0
    for ship in troopers:
        # A hull carrying only its flight crew (ShipDesign:76) cannot invade.
        need = exploit.min_crew(ship, act)
        if len(ship.crew) <= need:
            # The crew rules load only at a planet we own, so a spent hull
            # elsewhere goes home.
            here = ship.orbiting
            if here is not None and here.owner is not None \
                    and here.owner.addr == civ.addr:
                log(f"R-XTM-04: {ship} is empty at {here}, where R-XPL-06 can "
                    f"reload it")
                continue
            if not ours:
                continue
            home = min(ours, key=lambda p: gs.dist(ship.pos, p.pos))
            log(f"R-XTM-04: {ship} carries {len(ship.crew)} and cannot land "
                f"anyone; sending it home to {home} to re-arm "
                f"({gs.dist(ship.pos, home.pos):.0f} units)")
            try:
                send_to(act, ship, home, civ, order_type=1)
                act.claim(ship)
                acted += 1
            except (ValueError, RuntimeError) as e:
                log(f"  could not send it home: {e}")
            continue
        _w, target = min(undefended,
                         key=lambda wp: gs.dist(ship.pos, wp[1].pos))
        log(f"R-XTM-04: {ship} ({len(ship.crew)} aboard, {need} to fly) "
            f"-> conquer {target}, undefended, "
            f"{gs.dist(ship.pos, target.pos):.0f} units")
        try:
            send_to(act, ship, target, civ, order_type=5)
            act.claim(ship)
            acted += 1
        except (ValueError, RuntimeError) as e:
            log(f"  could not order {ship}: {e}")
    return acted


def run_xtm06(snap, civ, act, hist, log=print):
    """R-XTM-06: garrison a freshly taken planet with the troops still aboard.

    A captured planet is disloyal and in civil disorder, and the repair is
    occupying infantry. The invasion leaves one unit as the garrison.

    Loyalty is not located. `Planet:368` is `min(8, turn - Planet:92 + 1)`,
    a timer a garrison does not change; `Planet:100` bytes 0-2 are not it.
    """
    acted = 0
    for ship in snap.owned_ships(civ):
        if ship.role != "TROOP":
            continue
        p = ship.orbiting
        if p is None or p.owner is None or p.owner.addr != civ.addr:
            continue
        # Only a newly taken planet; elsewhere the hull is loading. Planet:92
        # is rewritten on capture as well as founding, and the capture
        # resolves in the boundary the hull arrives in, hence a window.
        age = None if p.colonised_turn is None else snap.turn - p.colonised_turn
        if age is None or not (0 <= age <= GARRISON_WINDOW):
            continue
        keep = exploit.min_crew(ship, act)
        spare = len(ship.crew) - keep
        if spare <= 0:
            continue
        log(f"R-XTM-06: garrisoning {spare} unit(s) from {ship} onto {p}, "
            f"newly taken and in disorder (keeping {keep} to fly)")
        try:
            act.unload_crew(ship, p, count=spare, keep=keep)
            acted += 1
        except (ValueError, RuntimeError) as e:
            log(f"  could not garrison: {e}")
    return acted


def run_xtm07(snap, civ, act, hist, log=print):
    """R-XTM-07: keep a troop transport while there is anything to take."""
    foes = enemies(snap, civ, act)
    if not foes:
        return 0
    ranked = target_planets(snap, civ, hist, foes)
    if not any(w == 0 for w, _p in ranked):
        return 0                       # nothing takeable; build warships instead

    have = [s for s in snap.owned_ships(civ) if s.role == "TROOP"]
    if len(have) >= TROOP_TARGET:
        return 0
    # No new hull while a built one is waiting for crew.
    stranded = exploit.awaiting_crew(snap, civ, act, role="TROOP")
    if stranded:
        log(f"R-XTM-07: {len(stranded)} troop ship(s) built and waiting for "
            f"crew; another hull would not land anyone sooner")
        return 0

    import research
    done = {t for t, _ in civ.completed}
    designs = [d for d in snap.designs
               if d.owner is not None and d.owner.addr == civ.addr
               and d.role == "TROOP" and research.design_legal(d, done)]
    if not designs:
        log(f"R-XTM-07: {len(have)}/{TROOP_TARGET} troop ship(s) and this civ "
            f"has no design with a troop bay. Synthesise one: "
            f"client/dev_tools/game_cycle.py --design "
            f"'t1:chassis=0,scanner=0,engine=1,engine=1,module=1'")
        return 0
    yards = [p for p in snap.owned_planets(civ)
             if exploit.SHIPYARD in p.facilities and not p.u32(344)
             and not act.production_claimed(p)]
    if not yards:
        log(f"R-XTM-07: {len(have)}/{TROOP_TARGET} troop ship(s); every "
            f"shipyard is already building")
        return 0
    design = designs[0]
    planet = max(yards, key=lambda p: len(p.population))
    log(f"R-XTM-07: {len(have)}/{TROOP_TARGET} troop ship(s); queueing "
        f"{design.design_name!r} at {planet}")
    try:
        act.queue_ship(planet, design)
        return 1
    except (ValueError, RuntimeError) as e:
        log(f"  could not queue: {e}")
        return 0


def run_xtm05(snap, civ, act, hist, log=print):
    """R-XTM-05: withdraw a badly damaged ship to the nearest owned planet.

    Damage compounds across turns, the round loop runs up to 200 rounds with no
    early exit, and condition never recovers on its own.
    """
    ours = snap.owned_planets(civ)
    if not ours:
        return 0
    acted = 0
    for ship in snap.owned_ships(civ):
        c = ship.condition
        if c is None or c >= RETREAT_AT or act.is_claimed(ship):
            continue
        home = min(ours, key=lambda p: gs.dist(ship.pos, p.pos))
        log(f"R-XTM-05: {ship} at condition {c:.2f}; withdrawing to {home}")
        try:
            send_to(act, ship, home, civ, order_type=1)   # not actuator L
            act.claim(ship)
            acted += 1
        except (ValueError, RuntimeError) as e:
            log(f"  could not withdraw: {e}")
    return acted


# R-XTM-05 runs first so R-XTM-03 does not send a retreating ship back into
# the fight in the same pass.
def run_xtm00(snap, civ, act, hist, log=print):
    """R-XTM-00: declare war on a civ we can reach and are equipped to fight.

    Gated by ALLOW_DECLARE_WAR. A raw relation write costs no reputation and
    generates no news item, which against a human is a §1.1 violation (§4.4);
    the command path is the fix.
    """
    if not ALLOW_DECLARE_WAR:
        return 0
    ready = [s for s in warships(snap, civ) if act.is_crewed(s)]
    if not ready:
        return 0
    acted = 0
    for other in snap.civs:
        if other.addr == civ.addr or at_war_with(snap, civ, other, act):
            continue
        seen = [p for p in snap.planets
                if p.owner is not None and p.owner.addr == other.addr
                and hist.can_see(snap, p)]
        if not seen:
            continue
        log(f"R-XTM-00: {len(seen)} visible planet(s) of {other.civ_name!r} and "
            f"{len(ready)} crewed warship(s); declaring war")
        try:
            act.declare_war(civ, other)
            clear_relation_cache()
            acted += 1
        except (ValueError, RuntimeError) as e:
            log(f"  could not declare war: {e}")
    return acted


# See run_xtm00 for the fairness caveat against a human.
ALLOW_DECLARE_WAR = True

RULES = [("R-XTM-05", run_xtm05), ("R-XTM-00", run_xtm00),
         ("R-XTM-01", run_xtm01), ("R-XTM-03", run_xtm03)]


def main():
    args = sys.argv[1:]
    dry_run = not cli.flag(args, "--apply")
    snap = gs.Snapshot()
    civ = gs.resolve_civ(snap, cli.opt(args, "--civ"))
    if civ is None:
        return
    print(f"\n=== Exterminate, turn {snap.turn}, {civ.civ_name!r}, "
          f"{'DRY RUN' if dry_run else 'APPLYING'} ===")
    hist = sensors.History().observe(snap, civ)
    # A remote handle even in dry run: the war sensor reads through it.
    rem = remote.Remote(snap.state.pid, log=lambda *a: None)
    act = actions.Actuator(snap, dry_run=dry_run, remote_=rem)
    for c in snap.civs:
        if c.addr != civ.addr:
            code = relation_code(snap, civ, c, act)
            print(f"  relation with {c.civ_name!r}: "
                  f"{code} ({'WAR' if code == WAR else 'not at war'})")
    try:
        for name, fn in RULES:
            fn(snap, civ, act, hist)
    finally:
        if rem is not None:
            rem.close()
    print(f"\n{act.summary()}")
    if dry_run:
        print("re-run with --apply to perform these writes")


if __name__ == "__main__":
    main()
