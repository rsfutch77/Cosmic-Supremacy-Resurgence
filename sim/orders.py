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
from . import combat
from . import economy as ec
from . import load as sl
from . import movement as mv
from . import research as rs
from . import scans as sc
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
WAR, CEASE_FIRE, PEACE = 1, 2, 3        # treaty kinds and the stances they set
PROPOSED, ACCEPTED, DECLINED = 0, 1, 2  # treaty status
BINDS_FOR = {CEASE_FIRE: 20, PEACE: 50}  # turns, the manual's; only the cease-fire's is measured


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
    people = sorted((ec.origin(c), ec.arrival(c)) for c in p.citizens)
    wanted = sorted(j for j, n in jobs.items() for _ in range(n))
    p.citizens = sorted((j, o, t) for j, (o, t) in zip(wanted, people))
    return True, ''


def free_space(g, p):
    """Space a new facility may take: the usable space, blight included, less
    what the planet's facilities fill."""
    return ec.usable_space(p, g.civs[p.owner]) - ec.facility_space(p)


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
    prod = p.production
    if prod is not None and prod.kind == 'SHIP' and prod.item == design:
        return False, 'the planet is already building that design'
    store = g.civs[civ].resources or [0] * 5
    if any(have < need for have, need in zip(store, d.resources)):
        return False, 'not enough ship resources'
    g.civs[civ].resources = [have - need for have, need in zip(store, d.resources)]
    _switch(p, 'SHIP', design)
    return True, ''


def build_scan(g, civ, planet, kind):
    """Start a scan: the planet needs a Central Defense Agency and the civ the
    research that unlocks that kind."""
    p, why = _own_planet(g, civ, planet)
    if p is None:
        return False, why
    if not p.facilities.get(sc.CDA):
        return False, 'the planet has no Central Defense Agency'
    if kind not in sc.SCANS or kind not in sc.unlocked(g.civs[civ]):
        return False, 'that scan is not researched'
    _switch(p, 'SCAN', kind)
    return True, ''


def scan(g, civ, kind, target):
    return sc.use(g, civ, kind, target)


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
        return ec.facility_cost(prod.item, p.facilities.get(prod.item, 0), g.civs.get(p.owner))
    if prod.kind == 'SHIP' and prod.item in g.designs:
        return ec.ship_cost(g, p.owner, g.designs[prod.item])
    if prod.kind == 'SCAN' and prod.item in sc.SCANS:
        return sc.cost(g.civs[p.owner], prod.item)
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
    # Every Military Camp adds 20, the client's recruitment tooltip says.
    cap = RECRUIT_BASE + RECRUIT_CAMP * p.facilities.get(ec.MILITARY_CAMP, 0)
    if not 0 <= rate <= cap:
        return False, f'the rate runs 0 to {cap}'
    p.recruit = rate
    return True, ''


def seats_of(d):
    """The chassis's crew maximum, 4 more per Large Pilot Cabin and 5 troopers
    per Troop Bay, the ship components page's figures."""
    if d.chassis is None:
        return 0
    modules = d.parts.get('modules') or ()
    return (tb.CHASSIS[d.chassis].crew[1] + 4 * modules.count(ec.PILOT_CABIN)
            + 5 * modules.count(ec.TROOP_BAY))


def crew(g, civ, ship, count):
    """Move soldiers between a ship and the own planet it orbits, the most
    experienced first, up to the chassis's seats; a negative count lands them."""
    s, why = _own_ship(g, civ, ship)
    if s is None:
        return False, why
    p = g.planets.get(s.orbit)
    if p is None or p.owner != civ:
        return False, 'the ship is not in orbit of one of the civ\'s planets'
    seats = seats_of(g.designs[s.design])
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
    d = g.designs.get(s.design)
    need = combat.crew_needed(d, g.civs.get(s.owner))
    if len(s.crew) < need:
        return False, f'the ship needs {need} crew to fly'
    if s.fleet:
        return False, 'the ship is in a fleet; order the fleet'
    s.order = st.Order(kind, end, 0.0, [mv.leg(s.pos, start_r, end, end_r)], s.orbit)
    return True, ''


def attack(g, civ, ship, target):
    """Send a ship to a sun or planet with the Attack order: its guns fire at
    the ships of civs it is at war with there."""
    return _go(g, civ, ship, target, mv.ATTACK)


def conquer(g, civ, ship, target):
    """The Conquer order: Attack, to take the planet."""
    return _go(g, civ, ship, target, mv.CONQUER)


def bio_bomb(g, civ, ship, planet, runs, bombs):
    """Send a ship with Bio Bombs to bomb a planet for `runs` turns, `bombs`
    sets a turn."""
    s, why = _own_ship(g, civ, ship)
    if s is None:
        return False, why
    sets = (g.designs[s.design].parts.get('modules') or ()).count(4)
    if not sets:
        return False, 'the ship carries no bio-bombs'
    p = g.planets.get(planet)
    if p is None or not p.owner or p.owner == civ:
        return False, 'not another civ\'s planet'
    if runs < 1 or not 1 <= bombs <= sets:
        return False, f'1 or more runs of 1 to {sets} sets'
    ok, why = _go(g, civ, ship, planet, mv.BIO_BOMB)
    if ok:
        s.order.runs, s.order.bombs = runs, bombs
    return ok, why


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
    bonus = traits.value(c, 42)
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


hull_space = mv.hull_space


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
    space = hull_space(g.civs[civ], d)
    if d.space_used > space:
        return False, f'the parts need {d.space_used} space and the chassis has {space}'
    g.high_water += 1
    g.designs[d.oid] = d
    return True, ''


def binding(g, a, b):
    """The cease-fire or peace between two civs that still binds them, or None."""
    for t in g.treaties.values():
        if {t.proposer, t.recipient} == {a, b} and t.kind in BINDS_FOR and t.status == ACCEPTED \
                and g.turn < t.start + t.length:
            return t
    return None


def declare_war(g, civ, other):
    """War is the one stance a civ takes alone; peace needs a treaty. Refused
    while a cease-fire or peace still binds the two, as the referee does
    (merge_orders); the client warns of a reputation penalty instead."""
    if other not in g.civs or other == civ:
        return False, 'no such civ'
    if g.civs[civ].stances.get(other) == WAR:
        return False, 'already at war'
    if binding(g, civ, other) is not None:
        return False, 'a cease-fire or peace still binds the two'
    g.civs[civ].stances[other] = WAR
    g.civs[other].stances[civ] = WAR
    g.declared.add((civ, other))
    return True, ''


def propose(g, civ, other, kind):
    """Propose a cease-fire, only while at war, or a peace."""
    if other not in g.civs or other == civ:
        return False, 'no such civ'
    if kind not in BINDS_FOR:
        return False, 'only a cease-fire or a peace is modelled'
    stance = g.civs[civ].stances.get(other, 0)
    if kind == CEASE_FIRE and stance != WAR:
        return False, 'a cease-fire needs a war'
    if stance == kind:
        return False, 'the two already hold that stance'
    g.high_water += 1
    g.treaties[g.high_water] = st.Treaty(g.high_water, civ, other, kind, PROPOSED,
                                         BINDS_FOR[kind], g.turn)
    return True, ''


def accept(g, civ, treaty):
    """Accept a treaty proposed to the civ: both take its stance, and it binds
    from this turn."""
    t = g.treaties.get(treaty)
    if t is None or t.recipient != civ or t.status != PROPOSED:
        return False, 'no proposal to this civ'
    t.status, t.start = ACCEPTED, g.turn
    g.civs[t.proposer].stances[t.recipient] = t.kind
    g.civs[t.recipient].stances[t.proposer] = t.kind
    if t.kind not in (WAR, CEASE_FIRE):
        # Any treaty but war or cease-fire clears the significant battle on both sides.
        g.fought.discard((t.proposer, t.recipient))
        g.fought.discard((t.recipient, t.proposer))
    return True, ''


def decline(g, civ, treaty):
    t = g.treaties.get(treaty)
    if t is None or t.recipient != civ or t.status != PROPOSED:
        return False, 'no proposal to this civ'
    t.status = DECLINED
    return True, ''


DRAFT_BASE, DRAFT_STEP = 105, 5        # credits a soldier, and 5 more for each held (0x00516060)


def draft_cost(g, civ, n):
    """Credits to draft n citizens, the client's 0x00516060: each costs 105
    plus 5 for every soldier the civ then holds, on its planets and aboard
    its ships, counting the ones drafted before it."""
    held = (sum(len(p.military) for p in g.planets.values() if p.owner == civ)
            + sum(len(s.crew) for s in g.ships.values() if s.owner == civ))
    return sum(DRAFT_BASE + DRAFT_STEP * (held + i) for i in range(n))


def draft(g, civ, planet, n, jobs=None):
    """Make n of a planet's citizens soldiers for credits; refused when the
    civ is short, as the client's dialog (0x00425120) is. `jobs` picks them
    ({job: count}), as selecting citizens does; without it they come from the
    planet's largest job."""
    p, why = _own_planet(g, civ, planet)
    if p is None:
        return False, why
    if not 1 <= n <= len(p.citizens):
        return False, f'1 to {len(p.citizens)} citizens'
    have = p.jobs()
    if jobs is None:
        big = max(have, key=lambda j: (have[j], -j))
        jobs = {big: n}
    if sum(jobs.values()) != n or any(have.get(j, 0) < k for j, k in jobs.items()):
        return False, 'the citizens picked are not on the planet'
    cost = draft_cost(g, civ, n)
    if cost > (g.civs[civ].credits or 0):
        return False, f'drafting {n} costs {cost} credits'
    g.civs[civ].credits -= cost
    left = dict(jobs)
    kept = []
    for c in p.citizens:
        if left.get(c[0], 0) > 0:
            left[c[0]] -= 1
        else:
            kept.append(c)
    p.citizens = kept
    p.military = p.military + [(ec.MILITARY, civ)] * n
    p.military_xp = (p.military_xp or [0.0] * (len(p.military) - n)) + [0.0] * n
    return True, ''


def retire(g, civ, planet, n):
    """Send home some of a planet's soldiers, to save their upkeep. Which go
    is not documented: the least experienced here."""
    p, why = _own_planet(g, civ, planet)
    if p is None:
        return False, why
    if not 1 <= n <= len(p.military):
        return False, f'1 to {len(p.military)} soldiers'
    xp = sorted(p.military_xp or [0.0] * len(p.military), reverse=True)[:len(p.military) - n]
    p.military, p.military_xp = p.military[:len(xp)], xp
    return True, ''


ACTIONS = {
    'jobs': set_jobs, 'facility': build_facility, 'ship': build_ship,
    'wealth': wealth, 'hurry': hurry, 'research': research, 'recruit': recruit,
    'crew': crew, 'move': move, 'colonise': colonise, 'scout': scout,
    'trade': trade, 'war': declare_war, 'design': design, 'propose': propose,
    'accept': accept, 'decline': decline, 'retire': retire, 'draft': draft,
    'build_scan': build_scan, 'scan': scan, 'bio_bomb': bio_bomb, 'attack': attack, 'conquer': conquer,
}


def apply(g, civ, action):
    """One action, as (name, *arguments)."""
    name, *args = action
    fn = ACTIONS.get(name)
    if fn is None:
        return False, f'no action {name!r}'
    return fn(g, civ, *args)
