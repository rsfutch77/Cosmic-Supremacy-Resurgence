"""
exterminate.py: the Exterminate rules (STRATEGY.md §4.4)

R-XTM-01  keep a standing fleet
R-XTM-03  send the fleet at the weakest reachable enemy planet
R-XTM-05  pull damaged ships out, scouts included (R-EXP-04)
R-XTM-08  gather idle warships at the staging planet
R-XTM-09  probe an enemy planet we know nothing about
R-XTM-02  hold a planet an enemy ship or fleet is in scan range of

Enemy strength comes only from this civ's battle reports (intel.py).

    python exterminate.py            # dry run, one pass
    python exterminate.py --apply
"""
import struct
import sys

import actions
import cli
import expand
import exploit
import explore
import gamestate as gs
import intel
import remote
import sensors

WARSHIP_TARGET = 2        # staged warships before declaring war or raiding
FLEET_CAP = 8             # warships R-XTM-01 builds toward a target it cannot beat
STRENGTH_MARGIN = 1.5     # a target must take this many times longer to clear us
PROBES_IN_FLIGHT = 1      # unarmed hulls sent for a battle report at once
PLANET_SCAN_RANGE = 30.0  # a planet sees other civs' ships this close (stock client)
SHIP_SCAN_RANGE = 30.0    # a ship whose scanner range cannot be read
DEFEND_REACH = 12         # turns; defenders farther out than this stay home
ARRIVE_WITHIN = 0.8       # a ship this close to its target has arrived (battle bucket)
TROOP_TARGET = 1          # troop hulls to keep while anything is takeable
GARRISON_WINDOW = 3       # turns after a capture (Planet:92) that count as
                          # newly taken
RETREAT_AT = 0.30         # Ship:136 below this and the ship goes home
SCOUT_RETREAT_AT = 0.50   # a scout is worth more alive than at its target
REPAIRED_AT = 0.90        # a withdrawn ship stays at the shipyard until this
AT_PLANET = 1.0           # a ship within this distance of a planet is at it

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
    light, heavy, bomb, _hp = unit_stats(design, act)
    return bomb > max(light, heavy)


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


def enemy_planets(snap, civ, hist, foes):
    """Planets of the civs in `foes` inside our discovery set."""
    foe_addrs = {f.addr for f in foes}
    return [p for p in snap.planets
            if p.owner is not None and p.owner.addr in foe_addrs
            and hist.can_see(snap, p)]


def pass_intel(snap, civ, hist, act):
    """This pass's intel.Intel, after recording quiet enemy planets.

    One of our ships in orbit of a planet we are at war with, undamaged, with
    no battle report there this turn, shows the planet has no defence.
    """
    cached = getattr(hist, "_intel", None)
    if cached is not None and cached[0] == snap.turn:
        return cached[1]
    info = intel.Intel(snap, civ, hist)
    foes = {f.addr for f in enemies(snap, civ, act)}
    for s in snap.owned_ships(civ):
        p = s.orbiting
        if (p is None or p.owner is None or p.owner.addr not in foes
                or (s.condition or 0) < 0.999
                or info.fought_at(p, snap.turn or 0)):
            continue
        hist.quiet[p.id] = snap.turn
    hist._intel = (snap.turn, info)
    return info


_PART_TABLES = {}          # part list name -> {id: component record}, per process


def part_tables(snap):
    """{part list name: {id: component record}}, read once per process."""
    import components
    if not _PART_TABLES:
        for attr, cat in (("chassis", "chassis"), ("scanners", "scanners"),
                          ("engines", "engines"), ("weapons", "weapons"),
                          ("modules", "modules")):
            _PART_TABLES[attr] = components.read_table(snap, cat)
    return _PART_TABLES


def _parts_total(design):
    """(light, heavy, bomb, units) summed from the component tables.

    A design's own stat block stays uncomputed until a ship of it is built.
    """
    l = h = b = units = 0
    for attr, table in part_tables(design.snap).items():
        for pid in getattr(design, attr) or []:
            rec = table.get(pid, {})
            units += rec.get("units", 0)
            fl, fh, fb = rec.get("firepower", (0, 0, 0))
            l, h, b = l + fl, h + fh, b + fb
    return l, h, b, units


def unit_stats(design, act=None):
    """(light, heavy, bomb, hitpoints) of one of our own designs."""
    fp = design.firepower
    # The getters write the lazy stat block, as the ship-design tab does on the
    # UI thread, so only an armed design with a cold cache is warmed.
    if (act is not None and act.remote is not None
            and design.weapons and not any(fp)):
        try:
            fp = act.remote.design_firepower(design.addr)
        except (RuntimeError, OSError):
            pass
    hp = design.hp or design.units
    if not any(fp) or not hp:
        l, h, b, units = _parts_total(design)
        fp = fp if any(fp) else (l, h, b)
        hp = hp or units
    return fp[0], fp[1], fp[2], hp


def margin(units, info, act=None):
    """How many times faster `units` clear the defenders in `info` than they clear us.

    `units` are (design, condition) pairs. Our hulls are light classes, so
    enemy heavy firepower hits them at half. Bombs are the only damage to
    planetary defence; light and heavy firepower hit ships, each at half
    against the other class. 0 when we cannot hurt the defenders at all.
    """
    L = H = B = hp = 0.0
    for d, cond in units:
        l, h, b, u = unit_stats(d, act)
        L, H, B = L + l, H + h, B + b
        hp += u * (1.0 if cond is None else cond)
    need = 0.0
    if info["planet_hp"]:
        if B <= 0:
            return 0.0
        need += info["planet_hp"] / B
    light, heavy = info["ship_hp"]
    if light or heavy:
        if L + H <= 0:
            return 0.0
        need += light / (L + H / 2) + heavy / (H + L / 2)
    tl, th, _tb = info["firepower"]
    incoming = tl + th / 2
    if need == 0 or incoming <= 0:
        return float("inf")
    return (hp / incoming) / need


def known_targets(snap, civ, hist, act, foes):
    """[(planet, defence)] for visible enemy planets whose defence we know."""
    info = pass_intel(snap, civ, hist, act)
    out = []
    for p in enemy_planets(snap, civ, hist, foes):
        d = info.defence(p)
        if intel.defended(d):
            out.append((p, d))
    return out


def undefended_planets(snap, civ, hist, act, foes):
    """Visible enemy planets observed with no defence, recently enough to trust."""
    info = pass_intel(snap, civ, hist, act)
    out = []
    for p in enemy_planets(snap, civ, hist, foes):
        d = info.defence(p)
        if d is not None and not intel.defended(d):
            out.append(p)
    return out


def fleet_designs(snap, civ):
    """Designs of our warships, built and in production."""
    queued = [d for d in (production_design(snap, p)
                          for p in snap.owned_planets(civ))
              if d is not None and d.role == "WARSHIP"]
    return [s.design for s in warships(snap, civ) if s.design] + queued


def fleet_wants_ships(snap, civ, hist, act):
    """(wants, goal): whether R-XTM-01 wants another warship, and the target.

    Wanted once contact is made, until there are WARSHIP_TARGET hulls of both
    firepower classes and they beat a known defence or reach FLEET_CAP. `goal`
    is _fleet_goal's tuple when a target drives the build, else None.
    """
    if not (enemies(snap, civ, act) or contacted(snap, civ, hist)):
        return False, None
    fleet = fleet_designs(snap, civ)
    # The fleet needs both firepower classes: slots 1-2 hit ships, slot 3
    # hits planets.
    have_ap = any(anti_planet(d, act) for d in fleet)
    have_as = any(not anti_planet(d, act) for d in fleet)
    if len(fleet) >= WARSHIP_TARGET and have_ap and have_as:
        goal = _fleet_goal(snap, civ, hist, act, fleet)
        return goal is not None, goal
    return True, None


def war_yard(snap, civ, hist, act):
    """The shipyard kept for warships while the fleet wants them, or None.

    It is the staging planet, where R-XTM-01 builds when it can. Expansion,
    scouting and facilities leave it alone so a long build there cannot
    hold up the fleet.
    """
    rally = staging_planet(snap, civ, hist)
    if rally is None or exploit.SHIPYARD not in rally.facilities:
        return None
    wants, _goal = fleet_wants_ships(snap, civ, hist, act)
    return rally if wants else None


def _fleet_goal(snap, civ, hist, act, fleet):
    """(margin, planet, defence) of the easiest known target `fleet` cannot beat.

    None when there is no known defended target, the fleet already beats one,
    or it has reached FLEET_CAP.
    """
    foes = enemies(snap, civ, act)
    targets = known_targets(snap, civ, hist, act, foes) if foes else []
    if not targets:
        return None
    units = [(d, 1.0) for d in fleet]
    scored = [(margin(units, d, act), p, d) for p, d in targets]
    if any(m >= STRENGTH_MARGIN for m, _p, _d in scored):
        return None
    if len(fleet) >= FLEET_CAP:
        return None
    return max(scored, key=lambda x: x[0])


def run_xtm01(snap, civ, act, hist, log=print):
    """R-XTM-01: keep a standing fleet once there is someone to fight.

    The trigger is contact, not war (STRATEGY.md §4.4): R-XTM-00 declares no
    war until a crewed warship exists, and this is the only rule that queues one.
    """
    foes = enemies(snap, civ, act) or contacted(snap, civ, hist)
    if not foes:
        return 0
    have = warships(snap, civ)
    fleet = fleet_designs(snap, civ)
    wants, goal = fleet_wants_ships(snap, civ, hist, act)
    if not wants:
        return 0
    have_ap = any(anti_planet(d, act) for d in fleet)
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
    # Ships are born at the staging yard when it is free.
    rally = staging_planet(snap, civ, hist)
    planet = (rally if rally in yards
              else max(yards, key=lambda p: len(p.population)))
    if goal is not None:
        # The design that most improves the margin against the easiest target.
        m, target, info = goal
        units = [(d, 1.0) for d in fleet]
        design = max(designs, key=lambda d: margin(units + [(d, 1.0)], info, act))
        log(f"R-XTM-01: {len(fleet)} warship(s) beat no known defence; best "
            f"is {target} at {m:.2f}x, need {STRENGTH_MARGIN}x; queueing "
            f"{design.design_name!r} at {planet}")
    else:
        # Builds the missing class, counting queued hulls as well as flying ones.
        want_anti_planet = not have_ap
        pool = [d for d in designs
                if anti_planet(d, act) == want_anti_planet] or designs
        design = max(pool, key=lambda d: max(unit_stats(d, act)[:3]))
        log(f"R-XTM-01: {len(have)}/{WARSHIP_TARGET} warship(s); queueing "
            f"{design.design_name!r} at {planet}")
    try:
        act.queue_ship(planet, design)
        return 1
    except (ValueError, RuntimeError) as e:
        log(f"  could not queue: {e}")
        return 0


def run_xtm03(snap, civ, act, hist, log=print):
    """R-XTM-03: send the staged fleet at a defended enemy planet it can beat.

    A Move order is enough: a ship in orbit of a hostile planet is always the
    aggressor, and the planet's turrets and ships always defend. A planet with
    nothing on it produces no battle, so only defended targets are raided.
    Ships that fought at their planet this turn or last stay in the fight.
    """
    foes = enemies(snap, civ, act)
    if not foes:
        return 0
    info = pass_intel(snap, civ, hist, act)
    foe_addrs = {f.addr for f in foes}
    ready = [s for s in warships(snap, civ)
             if act.is_crewed(s) and not act.is_claimed(s)
             and (s.condition is None or s.condition >= RETREAT_AT)]
    acted = 0
    for s in ready:
        p = s.orbiting
        if (p is not None and p.owner is not None and p.owner.addr in foe_addrs
                and info.fought_at(p, (snap.turn or 0) - 1)):
            act.claim(s)
            acted += 1
    if acted:
        log(f"R-XTM-03: {acted} warship(s) in a fight at an enemy planet stay")
    # A raid in flight is claimed, or R-XTM-08 would recall it.
    targets = enemy_planets(snap, civ, hist, foes)
    en_route = [s for s in ready if not act.is_claimed(s)
                and (s.order_type or 0) == 1
                and any(already_heading(s, p, 1) for p in targets)]
    for s in en_route:
        act.claim(s)
    acted += len(en_route)

    rally = staging_planet(snap, civ, hist)
    if rally is None:
        return acted
    staged = [s for s in ready if not act.is_claimed(s) and at_planet(s, rally)]

    # A raid departs in stages so every ship arrives on the same turn:
    # hist.raid = {"target": planet id, "arrive": turn, "members": ship ids}.
    # A ship staged after the raid was planned waits for the next one.
    plan = hist.raid
    by_id = {p.id: p for p in targets}
    if plan is not None and ((snap.turn or 0) >= plan["arrive"]
                             or plan["target"] not in by_id):
        plan = hist.raid = None
    if plan is None:
        if len(staged) < WARSHIP_TARGET:
            return acted
        target = _choose_raid(snap, civ, hist, act, foes, rally, staged, log)
        if target is None:
            return acted
        etas = [_eta(s, target, act) for s in staged]
        plan = hist.raid = {"target": target.id,
                            "arrive": (snap.turn or 0) + max(e or 0 for e in etas),
                            "members": [s.id for s in staged]}
    target = by_id[plan["target"]]
    # A ship in flight can run later than its ETA said; the waiting ships
    # follow the slowest one's arrival as it stands now.
    for s in en_route:
        if s.id in plan["members"] and already_heading(s, target, 1):
            eta = _eta(s, target, act)
            if eta is not None and (snap.turn or 0) + eta > plan["arrive"]:
                log(f"R-XTM-03: {s} now reaches {target} on turn "
                    f"{(snap.turn or 0) + eta}, not {plan['arrive']}; the "
                    f"rest wait for it")
                plan["arrive"] = (snap.turn or 0) + eta
    waiting = 0
    for ship in staged:
        if ship.id not in plan["members"]:
            continue
        need = exploit.min_crew(ship, act)
        if len(ship.crew) < need:
            log(f"  {ship} has {len(ship.crew)}/{need} crew and would "
                f"contribute zero firepower; leaving it")
            continue
        act.claim(ship)
        eta = _eta(ship, target, act)
        if eta is not None and (snap.turn or 0) + eta < plan["arrive"]:
            waiting += 1
            continue
        try:
            send_to(act, ship, target, civ, order_type=1)
            acted += 1
        except (ValueError, RuntimeError) as e:
            log(f"  could not order {ship}: {e}")
    if waiting:
        log(f"R-XTM-03: {waiting} faster warship(s) wait at {rally} to reach "
            f"{target} on turn {plan['arrive']} with the rest")
    return acted


def _eta(ship, target, act):
    """Turns for `ship` to arrive at `target`, or None when its speed is unknown."""
    import math
    speed = expand.ship_speed(ship, act)
    if not speed:
        return None
    left = max(0.0, gs.dist(ship.pos, target.pos) - ARRIVE_WITHIN)
    return math.ceil(left / speed)


def _choose_raid(snap, civ, hist, act, foes, rally, staged, log):
    """The nearest known defended planet `staged` beats by STRENGTH_MARGIN."""
    units = [(s.design, s.condition) for s in staged if s.design]
    options = [(margin(units, d, act), p)
               for p, d in known_targets(snap, civ, hist, act, foes)]
    if not options:
        log(f"R-XTM-03: {len(staged)} warship(s) staged at {rally}; no visible "
            f"enemy planet has a known defence; R-XTM-09 probes the unknown ones")
        return None
    beatable = [(m, p) for m, p in options if m >= STRENGTH_MARGIN]
    if not beatable:
        m, p = max(options, key=lambda o: o[0])
        log(f"R-XTM-03: {len(staged)} warship(s) staged at {rally} beat no "
            f"known defence; best is {p} at {m:.2f}x, need {STRENGTH_MARGIN}x")
        return None
    m, target = min(beatable, key=lambda o: gs.dist(rally.pos, o[1].pos))
    log(f"R-XTM-03: {len(staged)} warship(s) from {rally} -> {target} "
        f"({m:.2f}x, from the battle report)")
    return target


def _scan_range(design):
    """A design's scan range: its best scanner's, from the component table."""
    import components
    if design is None or not design.scanners:
        return SHIP_SCAN_RANGE
    table = _PART_TABLES.get("scanners")
    if table is None:
        table = components.read_table(design.snap, "scanners")
    return max((table.get(i, {}).get("range") or SHIP_SCAN_RANGE)
               for i in design.scanners)


def contacts(snap, civ, foes):
    """Enemy ships and fleets inside our scan range, as the map shows them.

    [(pos, hull class ids, is_fleet)]. A player sees only a ship's class, and
    for a fleet every class in it but no count.
    """
    eyes = [(p.pos, PLANET_SCAN_RANGE) for p in snap.owned_planets(civ)]
    eyes += [(s.pos, _scan_range(s.design)) for s in snap.owned_ships(civ)
             if None not in s.pos]
    foe_addrs = {f.addr for f in foes}

    def seen(pos):
        return None not in pos and any(gs.dist(pos, at) <= r for at, r in eyes)

    out = []
    for s in snap.ships:
        if s.owner is None or s.owner.addr not in foe_addrs or not seen(s.pos):
            continue
        out.append((s.pos, set(s.design.chassis) if s.design else set(), False))
    for f in snap.fleets:
        if f.owner is None or f.owner.addr not in foe_addrs or not seen(f.pos):
            continue
        classes = {c for d in f.member_designs for c in (d.chassis or [])}
        out.append((f.pos, classes, True))
    return out


def _is_threat(group):
    """A fleet, two or more ships, or any hull above shuttle class.

    A lone shuttle may be a probe or a decoy; the map cannot tell.
    """
    return (len(group) >= 2
            or any(is_fleet for _pos, _cls, is_fleet in group)
            or any(c >= 1 for _pos, cls, _f in group for c in cls))


def run_xtm02(snap, civ, act, hist, log=print):
    """R-XTM-02: hold a planet that enemy ships are in scan range of.

    Ships meeting in open space fight only under an Attack order, which the
    AI cannot give, so defence means being in orbit when the enemy arrives:
    the planet's owner always defends. Defenders come from the staging planet
    and from idle warships; a raid in flight is not recalled.
    """
    foes = enemies(snap, civ, act)
    if not foes:
        return 0
    ours = snap.owned_planets(civ)
    groups = {}
    for c in contacts(snap, civ, foes):
        p = min(ours, key=lambda q: gs.dist(q.pos, c[0]))
        groups.setdefault(p.id, (p, []))[1].append(c)
    threats = [(p, g) for p, g in groups.values() if _is_threat(g)]
    if not threats:
        return 0
    rally = staging_planet(snap, civ, hist)
    pool = [s for s in warships(snap, civ)
            if act.is_crewed(s) and not act.is_claimed(s)
            and (s.condition is None or s.condition >= RETREAT_AT)
            and ((s.order_type or 0) == 0
                 or (rally is not None and already_heading(s, rally, 1)))]
    acted = 0
    # The nearest threat to the staging planet first.
    if rally is not None:
        threats.sort(key=lambda t: gs.dist(t[0].pos, rally.pos))
    for planet, group in threats:
        seen = ", ".join(sorted({"fleet" if f else "ship" for _p, _c, f in group}))
        classes = sorted({gs.CHASSIS.get(c, c) for _p, cls, _f in group
                          for c in cls})
        here = [s for s in pool if at_planet(s, planet)]
        going = []
        for s in pool:
            if s in here or act.is_claimed(s):
                continue
            eta = _eta(s, planet, act)
            if eta is not None and eta <= DEFEND_REACH:
                going.append(s)
        if not here and not going:
            log(f"R-XTM-02: enemy {seen} ({', '.join(map(str, classes))}) in "
                f"scan range of {planet}; no warship can reach it within "
                f"{DEFEND_REACH} turns")
            continue
        log(f"R-XTM-02: enemy {seen} ({', '.join(map(str, classes))}) in scan "
            f"range of {planet}; holding {len(here)}, sending {len(going)}")
        for s in here:
            act.claim(s)
            acted += 1
        for s in going:
            try:
                send_to(act, s, planet, civ, order_type=1)
                act.claim(s)
                acted += 1
            except (ValueError, RuntimeError) as e:
                log(f"  could not send {s}: {e}")
    return acted


def run_xtm09(snap, civ, act, hist, log=print):
    """R-XTM-09: send an unarmed hull at an enemy planet we know nothing about.

    Its battle report is the intel; if nothing fires on it, the planet is
    undefended. Probes in flight are claimed so Explore does not retask them.
    """
    foes = enemies(snap, civ, act)
    if not foes:
        return 0
    info = pass_intel(snap, civ, hist, act)
    unknown = [p for p in enemy_planets(snap, civ, hist, foes)
               if info.defence(p) is None]
    if not unknown:
        return 0
    probes = [s for s in snap.owned_ships(civ)
              if s.role == "SCOUT" and act.is_crewed(s)
              and not act.is_claimed(s) and None not in s.pos]
    flying = [s for s in probes
              if any(already_heading(s, p, 1) for p in unknown)]
    for s in flying:
        act.claim(s)
    if len(flying) >= PROBES_IN_FLIGHT:
        return len(flying)
    free = [s for s in probes if s not in flying]
    if not free:
        log(f"R-XTM-09: {len(unknown)} enemy planet(s) of unknown defence and "
            f"no free scout to probe them")
        return len(flying)
    # One scout keeps exploring while any system is undiscovered.
    if explore.undiscovered_suns(snap, hist) and len(free) < 2:
        return len(flying)
    ship, target = min(((s, p) for s in free for p in unknown),
                       key=lambda sp: gs.dist(sp[0].pos, sp[1].pos))
    log(f"R-XTM-09: probing {target} with {ship} "
        f"({gs.dist(ship.pos, target.pos):.0f} units); "
        f"{len(unknown)} enemy planet(s) of unknown defence")
    try:
        send_to(act, ship, target, civ, order_type=1)
        act.claim(ship)
        return len(flying) + 1
    except (ValueError, RuntimeError) as e:
        log(f"  could not send the probe: {e}")
        return len(flying)


def run_xtm04(snap, civ, act, hist, log=print):
    """R-XTM-04: land troops on an undefended enemy planet and take it."""
    foes = enemies(snap, civ, act)
    if not foes:
        return 0
    troopers = [s for s in snap.owned_ships(civ)
                if s.role == "TROOP" and not act.is_claimed(s)]
    if not troopers:
        return 0

    undefended = undefended_planets(snap, civ, hist, act, foes)
    if not undefended:
        log(f"R-XTM-04: {len(troopers)} troop ship(s) but no visible enemy "
            f"planet is known to be undefended")
        return 0

    ours = snap.owned_planets(civ)
    acted = 0
    for ship in troopers:
        # A hull carrying only its flight crew (ShipDesign:76) cannot invade.
        need = exploit.min_crew(ship, act)
        if len(ship.crew) <= need:
            if exploit.crew_gap(ship, act):
                log(f"R-XTM-04: {ship} is waiting for its first crew")
                continue
            # A spent hull cannot be topped up (exploit.crew_gap), so it is
            # parked at home and R-XTM-07 builds a replacement.
            here = ship.orbiting
            if here is not None and here.owner is not None \
                    and here.owner.addr == civ.addr:
                log(f"R-XTM-04: {ship} is spent and parked at {here}")
                continue
            if not ours:
                continue
            home = min(ours, key=lambda p: gs.dist(ship.pos, p.pos))
            log(f"R-XTM-04: {ship} carries {len(ship.crew)} and cannot land "
                f"anyone; parking it at {home} "
                f"({gs.dist(ship.pos, home.pos):.0f} units)")
            try:
                send_to(act, ship, home, civ, order_type=1)
                act.claim(ship)
                acted += 1
            except (ValueError, RuntimeError) as e:
                log(f"  could not send it home: {e}")
            continue
        target = min(undefended, key=lambda p: gs.dist(ship.pos, p.pos))
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
    if not undefended_planets(snap, civ, hist, act, foes):
        return 0                       # nothing takeable; build warships instead

    # A hull that has landed its troops cannot be refilled, so it no longer
    # counts as a transport.
    have = [s for s in snap.owned_ships(civ) if s.role == "TROOP"
            and (len(s.crew) > exploit.min_crew(s, act)
                 or exploit.crew_gap(s, act))]
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


def retreat_threshold(ship):
    """Condition below which this ship withdraws. R-EXP-04 is the scout case."""
    return SCOUT_RETREAT_AT if ship.role == "SCOUT" else RETREAT_AT


def run_xtm05(snap, civ, act, hist, log=print):
    """R-XTM-05 and R-EXP-04: withdraw a damaged ship to a shipyard and hold it.

    Damage compounds across turns, the round loop runs up to 200 rounds with no
    early exit, and condition never recovers on its own. Shipyards repair, so
    a ship goes to the nearest one, or the nearest owned planet when the civ
    has none, and is held there until REPAIRED_AT. Without the hold, the next
    pass would send a ship at RETREAT_AT + 0.01 straight back out.
    """
    ours = snap.owned_planets(civ)
    if not ours:
        return 0
    yards = [p for p in ours if exploit.SHIPYARD in p.facilities] or ours
    acted = 0
    for ship in snap.owned_ships(civ):
        c = ship.condition
        if c is None or act.is_claimed(ship):
            continue
        home = min(yards, key=lambda p: gs.dist(ship.pos, p.pos))
        if at_planet(ship, home):
            if c < REPAIRED_AT:
                log(f"R-XTM-05: {ship} ({ship.role}) at condition {c:.2f}; "
                    f"holding at {home} until {REPAIRED_AT:.2f}")
                act.claim(ship)
            continue
        if c >= retreat_threshold(ship):
            continue
        log(f"R-XTM-05: {ship} ({ship.role}) at condition {c:.2f}, below "
            f"{retreat_threshold(ship):.2f}; withdrawing to {home}")
        try:
            send_to(act, ship, home, civ, order_type=1)   # not actuator L
            act.claim(ship)
            acted += 1
        except (ValueError, RuntimeError) as e:
            log(f"  could not withdraw: {e}")
    return acted


def rival_planets(snap, civ, hist):
    """Planets of other civs inside our discovery set."""
    return [p for p in snap.planets
            if p.owner is not None and p.owner.addr != civ.addr
            and hist.can_see(snap, p)]


def staging_planet(snap, civ, hist):
    """Where idle warships gather.

    The shipyard planet nearest any rival planet we can see; before contact,
    the most populous one. Warships are built at shipyards and repaired there,
    so a frontier colony far from them would keep the fleet in transit. Any
    owned planet when there is no shipyard.
    """
    ours = snap.owned_planets(civ)
    if not ours:
        return None
    yards = [p for p in ours if exploit.SHIPYARD in p.facilities]
    ours = yards or ours
    rivals = rival_planets(snap, civ, hist)
    if not rivals:
        return max(ours, key=lambda p: (len(p.population), -p.id))
    return min(ours, key=lambda p: (min(gs.dist(p.pos, r.pos) for r in rivals),
                                    p.id))


def at_planet(ship, planet):
    return gs.dist(ship.pos, planet.pos) <= AT_PLANET


def run_xtm08(snap, civ, act, hist, log=print):
    """R-XTM-08: send every idle crewed warship to the staging planet.

    Runs after R-XTM-03, so a warship committed to a raid is already claimed.
    Idle covers no order, a move order and a leftover scout order.
    """
    rally = staging_planet(snap, civ, hist)
    if rally is None:
        return 0
    fleet = [s for s in warships(snap, civ)
             if act.is_crewed(s) and not act.is_claimed(s)
             and (s.order_type or 0) in (0, 1, 2)]
    away = [s for s in fleet
            if not at_planet(s, rally) and not already_heading(s, rally, 1)]
    if not away:
        return 0
    log(f"R-XTM-08: staging at {rally}; {len(away)} of {len(fleet)} idle "
        f"warship(s) are elsewhere")
    acted = 0
    for ship in away:
        try:
            send_to(act, ship, rally, civ, order_type=1)
            act.claim(ship)
            acted += 1
        except (ValueError, RuntimeError) as e:
            log(f"  could not send {ship}: {e}")
    return acted


# R-XTM-05 runs first so R-XTM-03 does not send a retreating ship back into
# the fight in the same pass.
def run_xtm00(snap, civ, act, hist, log=print):
    """R-XTM-00: declare war on a civ we can reach and are equipped to fight.

    Through the engine's own declaration (actions.declare_war). A civ still in
    its build-up protection is left alone, as the declare-war validator
    refuses it. Gated by ALLOW_DECLARE_WAR.
    """
    if not ALLOW_DECLARE_WAR:
        return 0
    rally = staging_planet(snap, civ, hist)
    ready = [s for s in warships(snap, civ)
             if act.is_crewed(s) and rally is not None and at_planet(s, rally)]
    if len(ready) < WARSHIP_TARGET:
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
        if act.remote is not None:
            try:
                protected, played, need = act.remote.war_protection(other.addr)
            except (RuntimeError, OSError) as e:
                log(f"R-XTM-00: cannot read {other.civ_name!r}'s build-up "
                    f"protection ({e}); not declaring")
                continue
            if protected:
                log(f"R-XTM-00: {other.civ_name!r} is in its build-up phase "
                    f"({played} of {need} turns played); war is refused")
                continue
        log(f"R-XTM-00: {len(seen)} visible planet(s) of {other.civ_name!r} and "
            f"{len(ready)} warship(s) staged at {rally}; declaring war")
        try:
            act.declare_war(civ, other)
            clear_relation_cache()
            acted += 1
        except (ValueError, RuntimeError) as e:
            log(f"  could not declare war: {e}")
    return acted


ALLOW_DECLARE_WAR = True     # off leaves war to the other side to declare

RULES = [("R-XTM-05", run_xtm05), ("R-XTM-00", run_xtm00),
         ("R-XTM-01", run_xtm01), ("R-XTM-03", run_xtm03),
         ("R-XTM-02", run_xtm02), ("R-XTM-08", run_xtm08),
         ("R-XTM-09", run_xtm09)]


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
