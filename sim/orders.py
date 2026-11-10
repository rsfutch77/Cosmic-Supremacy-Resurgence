"""
What a civ may order, checked the way the client's own screens check it, and
applied to simulator state before a tick.

Each function returns (ok, reason) and changes nothing when it refuses. The
checks are the UI's rules, not inferences from what a save happens to hold:
facility_rules.can_build for buildings, a shipyard and the design's resources
for ships, half done and the credits in hand for a hurry, stationed soldiers
for crew.

Rules a capture has not confirmed say so where they are applied.
"""
from . import economy as ec
from . import load as sl
from . import movement as mv
from . import research as rs
from . import state as st
from . import tables as tb
from . import traits

JOBS = (ec.FARMER, ec.WORKER, ec.SCIENTIST, ec.MINER, ec.BANKER)
HURRY_PER_POINT = 4             # credits per production point left, the manual's
SHIPYARD = 2
ONCE = {2, 7, 8, 19}            # facility_rules.ONCE, one per planet
TRANSMITTER, RECEIVER = 18, 19
FACILITY_CAP = 99
RECRUIT_BASE, RECRUIT_CAMP = 20, 20     # percent; the highest captured rates fit, unconfirmed
WAR = 1


def _own_planet(g, civ, planet):
    p = g.planets.get(planet)
    if p is None or p.owner != civ:
        return None, 'not one of the civ\'s planets'
    return p, ''


def _own_ship(g, civ, ship):
    s = g.ships.get(ship)
    if s is None or s.owner != civ:
        return None, 'not one of the civ\'s ships'
    return s, ''


def set_jobs(g, civ, planet, jobs):
    """Reassign a planet's citizens: {job: count}, every citizen placed."""
    p, why = _own_planet(g, civ, planet)
    if p is None:
        return False, why
    if any(j not in JOBS or n < 0 for j, n in jobs.items()):
        return False, f'jobs are {JOBS}'
    if sum(jobs.values()) != len(p.citizens):
        return False, f'{len(p.citizens)} citizens to place'
    p.citizens = sorted((j, civ) for j, n in jobs.items() for _ in range(n))
    return True, ''


def free_space(g, p):
    civ = g.civs[p.owner]
    space = p.base_space + ec.R(p.base_space * ec.space_bonus(civ) / 100)
    used = sum(tb.FACILITIES[t].space * n for t, n in p.facilities.items()
               if t < len(tb.FACILITIES))
    return space - (p.lost_space or 0) - used


def build_facility(g, civ, planet, kind):
    p, why = _own_planet(g, civ, planet)
    if p is None:
        return False, why
    if not 0 <= kind < len(tb.FACILITIES):
        return False, f'there is no facility type {kind}'
    unlocked = {oid for t in g.civs[civ].completed or ()
                for k, oid, _v in traits.GRANTS.get(t, ()) if k == traits.FACILITY}
    if kind not in unlocked:
        return False, 'it is not yet researched'
    if free_space(g, p) < tb.FACILITIES[kind].space:
        return False, 'the planet has too little free space'
    have = p.facilities
    if (kind == RECEIVER and have.get(TRANSMITTER)) or (kind == TRANSMITTER and have.get(RECEIVER)):
        return False, 'a hyperspace transmitter and receiver cannot share a planet'
    if have.get(kind) and (kind in ONCE or have[kind] >= FACILITY_CAP):
        return False, 'no more of it may stand on the planet'
    _switch(p, 'FCLT', kind)
    return True, ''


def build_ship(g, civ, planet, design):
    """Start a ship. Its resources leave the civ's store now, as the client
    charges them when the build is set."""
    p, why = _own_planet(g, civ, planet)
    if p is None:
        return False, why
    if not p.facilities.get(SHIPYARD):
        return False, 'the planet has no shipyard'
    d = g.designs.get(design)
    if d is None or d.owner != civ or d.inactive:
        return False, 'not an active design of the civ\'s'
    store = g.civs[civ].resources or [0] * 5
    if any(have < need for have, need in zip(store, d.resources)):
        return False, 'not enough ship resources'
    g.civs[civ].resources = [have - need for have, need in zip(store, d.resources)]
    _switch(p, 'SHIP', design)
    return True, ''


def wealth(g, civ, planet):
    p, why = _own_planet(g, civ, planet)
    if p is None:
        return False, why
    _switch(p, 'WLTH', None)
    return True, ''


def _switch(p, kind, item):
    """New work keeps the points banked, as the captures show."""
    prod = p.production or st.Production('WLTH')
    if prod.hurried:
        return
    prod.kind, prod.item, prod.queue, prod.entry = kind, item, 0, 0
    p.production = prod


def cost_of(g, p):
    prod = p.production
    if prod is None or prod.item is None:
        return None
    if prod.kind == 'FCLT':
        return ec.facility_cost(prod.item, p.facilities.get(prod.item, 0))
    if prod.kind == 'SHIP' and prod.item in g.designs:
        return ec.ship_cost(g, p.owner, g.designs[prod.item])
    return None


def hurry(g, civ, planet):
    """Pay 4 credits a point to finish the current item next tick; it must be
    at least half done and cannot be changed afterwards."""
    p, why = _own_planet(g, civ, planet)
    if p is None:
        return False, why
    cost = cost_of(g, p)
    if cost is None or p.production.hurried:
        return False, 'nothing to hurry'
    left = cost - p.production.progress
    if left > cost / 2:
        return False, 'it is not yet half done'
    price = HURRY_PER_POINT * max(0, left)
    if g.civs[civ].credits < price:
        return False, f'it costs {price} credits'
    g.civs[civ].credits -= price
    p.production.progress, p.production.hurried = cost, True
    return True, ''


def research(g, civ, goal):
    c = g.civs[civ]
    if goal not in rs.TECHS or goal in (c.completed or ()):
        return False, 'not a technology the civ can still research'
    topic = rs.next_topic(goal, c.completed or [])
    if topic < 0:
        return False, 'no path to it'
    c.topic, c.goal = topic, goal
    return True, ''


def recruit(g, civ, planet, rate):
    p, why = _own_planet(g, civ, planet)
    if p is None:
        return False, why
    cap = RECRUIT_BASE + RECRUIT_CAMP * (1 if p.facilities.get(ec.MILITARY_CAMP) else 0)
    if not 0 <= rate <= cap:
        return False, f'the rate runs 0 to {cap}'
    p.recruit = rate
    return True, ''


def crew(g, civ, ship, count):
    """Move soldiers between a ship and the own planet it orbits, the most
    experienced first, up to the chassis's seats; a negative count lands them."""
    s, why = _own_ship(g, civ, ship)
    if s is None:
        return False, why
    p = g.planets.get(s.orbit)
    if p is None or p.owner != civ:
        return False, 'the ship is not in orbit of one of the civ\'s planets'
    d = g.designs[s.design]
    seats = tb.CHASSIS[d.chassis].crew[1] if d.chassis is not None else 0
    xp = list(p.military_xp or [0.0] * len(p.military))
    cxp = list(s.crew_xp or [0.0] * len(s.crew))
    if count >= 0:
        if count > len(p.military) or len(s.crew) + count > seats:
            return False, 'not enough soldiers or seats'
        order = sorted(range(len(xp)), key=lambda i: -xp[i])[:count]
        moving = set(order)
        cxp += [xp[i] for i in order]
        p.military_xp = [x for i, x in enumerate(xp) if i not in moving]
        p.military = p.military[:len(p.military) - count]
        s.crew = s.crew + [(ec.MILITARY, civ)] * count
    else:
        n = -count
        if n > len(s.crew):
            return False, 'not that many aboard'
        p.military_xp = xp + cxp[:n]
        cxp = cxp[n:]
        p.military = p.military + [(ec.MILITARY, civ)] * n
        s.crew = s.crew[n:]
    s.crew_xp = cxp
    return True, ''


def _go(g, civ, ship, target, kind):
    s, why = _own_ship(g, civ, ship)
    if s is None:
        return False, why
    if target in g.suns:
        end, end_r = g.suns[target], g.radius.get(target, 0)
    elif target in g.planets:
        end, end_r = g.planets[target].pos, g.radius.get(target, 0)
    else:
        return False, 'no such sun or planet'
    start_r = g.radius.get(s.orbit, 0) if s.orbit else 0
    if s.fleet:
        return False, 'the ship is in a fleet; order the fleet'
    s.order = st.Order(kind, end, 0.0, [mv.leg(s.pos, start_r, end, end_r)], s.orbit)
    return True, ''


def move(g, civ, ship, target):
    """Fly to a sun or planet. Into orbit of a planet at war with the civ, the
    ship is the aggressor and the planet's defence fights it."""
    return _go(g, civ, ship, target, mv.MOVE)


def colonise(g, civ, ship, planet):
    s, why = _own_ship(g, civ, ship)
    if s is None:
        return False, why
    d = g.designs[s.design]
    if ec.COLONY_MODULE not in (d.parts.get('modules') or ()):
        return False, 'the ship has no colony module'
    p = g.planets.get(planet)
    if p is None or p.owner:
        return False, 'not an unowned planet'
    if not s.crew:
        return False, 'the ship carries nobody to settle'
    return _go(g, civ, ship, planet, mv.COLONISE)


def scout(g, civ, ship):
    s, why = _own_ship(g, civ, ship)
    if s is None:
        return False, why
    here = s.orbit or mv.body_at(g, s.pos)
    nxt = mv.next_system(g, g.civs[civ], here)
    if nxt is None:
        return False, 'nothing left to explore'
    return _go(g, civ, ship, nxt, mv.SCOUT)


def trade(g, civ, kind, units):
    """Sell (units > 0) or buy (units < 0) a ship resource at the market,
    priced lot by lot as the engine quotes it (merge_orders.market_total)."""
    mo = sl.mo
    c = g.civs[civ]
    if g.market is None or not 0 <= kind < len(g.market) or not units:
        return False, 'no such trade'
    held = g.market[kind]
    bonus = mo.market_trait(c.completed or [])
    if units > 0:
        if c.resources[kind] < units:
            return False, 'not that much in store'
        c.credits += mo.market_total(kind, units, held, bonus)
        c.resources[kind] -= units
        g.market[kind] = held + units
    else:
        n = -units
        if held < n:
            return False, 'the market does not hold that much'
        price = mo.market_total(kind, n, held, bonus, buying=True)
        if c.credits < price:
            return False, f'it costs {price} credits'
        c.credits -= price
        c.resources[kind] += n
        g.market[kind] = held - n
    return True, ''


PART_GRANTS = {1: 'chassis', 2: 'modules', 3: 'weapons', 4: 'engines', 5: 'shields',
               6: 'scanners'}                   # research grant kind -> part list


def unlocked_parts(g, civ):
    out = {cat: set() for cat in tb.CATEGORIES}
    for t in g.civs[civ].completed or ():
        for kind, oid, _v in traits.GRANTS.get(t, ()):
            if kind in PART_GRANTS:
                out[PART_GRANTS[kind]].add(oid)
    return out


def design(g, civ, name, parts):
    """A new design from researched parts that fit one chassis's space. The
    client gives it the next object id."""
    if len(parts.get('chassis') or ()) != 1:
        return False, 'a design takes exactly one chassis'
    have = unlocked_parts(g, civ)
    for cat, ids in parts.items():
        if cat not in have or any(i not in have[cat] for i in ids):
            return False, f'{cat} not researched'
    d = st.Design(g.high_water + 1, civ, name,
                  {cat: list(parts.get(cat) or ()) for cat in tb.CATEGORIES})
    if d.space_used > d.space:
        return False, f'the parts need {d.space_used} space and the chassis has {d.space}'
    g.high_water += 1
    g.designs[d.oid] = d
    return True, ''


def declare_war(g, civ, other):
    """War is the one stance a civ takes alone; peace needs a treaty."""
    if other not in g.civs or other == civ:
        return False, 'no such civ'
    g.civs[civ].stances[other] = WAR
    g.civs[other].stances[civ] = WAR
    return True, ''


ACTIONS = {
    'jobs': set_jobs, 'facility': build_facility, 'ship': build_ship,
    'wealth': wealth, 'hurry': hurry, 'research': research, 'recruit': recruit,
    'crew': crew, 'move': move, 'colonise': colonise, 'scout': scout,
    'trade': trade, 'war': declare_war, 'design': design,
}


def apply(g, civ, action):
    """One action, as (name, *arguments)."""
    name, *args = action
    fn = ACTIONS.get(name)
    if fn is None:
        return False, f'no action {name!r}'
    return fn(g, civ, *args)
