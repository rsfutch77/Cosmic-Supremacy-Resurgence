"""
The planet step of a tick: food, corruption, growth, production, mining and
income.

Measured against engine ticks (docs/Fast_Sim_Plan.md, the economy section):
every rule here reproduces the archive pairs exactly unless its comment says
otherwise.
"""
import math

from . import load as sl
from . import state as st
from . import tables as tb
from . import traits

import facility_rules as fr                                     # noqa: E402

FARMER = 0
WORKER = 1
MILITARY = 3
MINER = 5
BANKER = 6
FACTORY, AUTOMATED_FACTORY = 1, 3       # facility ids
MINE, ROBO_MINE = 13, 14
BANKING_CENTER = 16
FACTORY_BONUS = 20              # percent of the base rate, per facility
AUTOMATED_FACTORY_BONUS = 50
MINE_BONUS = 40
ROBO_MINE_BONUS = 100
BANKING_BONUS = 40              # the manual's percent per banking center; no capture has one
SCIENTIST = 2
UNIVERSITY, SCIENCE_LAB = 4, 5
UNIVERSITY_BONUS = 25
SCIENCE_LAB_BONUS = 60
SCIENCE_TRAIT = 4
PRODUCTION_TRAIT = 3            # engine trait ids, traits.py
MINING_TRAIT = 5
INCOME_PER_CITIZEN = 10         # plus trait 1, an amount
INCOME_PER_BANKER = 50          # on top of the citizen's own, raised by trait 37 as a percent
EXTRA_METAL_TRAIT = 41          # taken as metal a turn; Metallurgy's 50, unconfirmed
WASTAGE_CAP, WASTAGE_SCALE, WASTAGE_FROM = 30.0, 14427.0, 1000
WEALTH_PER_CREDIT = 4           # production points a credit costs on Wealth
CREW_UPKEEP = 3                 # credits per crew member aboard a ship or soldier stationed
SOLDIER_FOOD = 300              # food to recruit a soldier, trait 33 as a percent; 255 at -15
LOST_FACILITY_REFUND = 20       # a facility lost to debt returns its cost over this; fits 70 and 29
COLONY_MODULE, TROOP_BAY, PILOT_CABIN, CLOAKING_DEVICE = 0, 1, 2, 3     # module ids
BATTLESHIP = 5
SCAN_COSTS = {2: 3500, 3: 5000, 0: 9000, 5: 12000, 4: 16000, 1: 20000, 6: 10000, 7: 20000}  # scans.SCANS
COLONY_SURCHARGE = 100          # per planet and colony ship the civ has, less one; approximate
FOOD_PER_CITIZEN = 10           # eaten by every citizen each turn, plus trait 0
FARM_BONUS = 25                 # percent of the base rate, per farm
FOOD_TRAIT = 2                  # engine trait id of the food output bonus
CORRUPTION_PLANETS = 0.025      # the manual's empire-wide corruption terms
CORRUPTION_REPUTATION = 25
CORRUPTION_FULL_AT = 0.75       # share of max population at full corruption
MILITARY_CAMP, MILITARY_ACADEMY = 6, 7
TRAINING = 0.1                  # experience a turn; each camp or academy doubles it
RANK_CAP = (2, 4, 6)            # without either, with a camp, with an academy
RANK_LOYALTY = 0.2              # a soldier counts 1.2 at rank 1, 1.4 at rank 2...
LOYALTY_PER_SOLDIER = 10        # percent, per effective soldier per citizen
PROPAGANDA_OFFICE = 12
PROPAGANDA_LOYALTY, PROPAGANDA_CITIZENS = 10, 10    # +10% for 10 citizens each
LOYALTY_GROWTH = 1.0            # points a turn on a resisting planet
INFLUENCE_SPAN = 100            # turns of a rule that count toward its influence
FOREIGN_INFLUENCE = 0.5         # a ruler's pull on a citizen of another origin
BLIGHT_RECOVERY = 1             # bio-blighted space that recovers a turn
REBELLION_LINE, REBELLION_GRACE = 30, 3
REBELLION_CHANCE = 20           # percent a turn once the grace is up; not given anywhere
SPACE_PER_CITIZEN = 10
HISTORY_SLOTS = 8
HISTORY_OLD = 0x1000            # set on every entry older than a birth
INACTIVITY_GRACE = 72           # turns after the civ was last active
INACTIVITY_TURNS_PER_POINT = 2  # food loses half a point a turn, the tooltip a whole one
IDLE_OUTPUT_PER_TURN = 0.009    # share of production and mining lost per idle turn
IDLE_INCOME_PER_TURN = 1 / 144  # fitted; idle civs land 1 or 2 credits off on some turns


def R(x):
    """Round half away from zero, as the engine's yield code does."""
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def food_capacity(pop):
    """Food that fills the store once, for a population."""
    if pop < 1:
        return 40
    return int((pop - 1) ** 1.5) * 40 + 40


def space_bonus(civ):
    """The percent the civ's research adds to every planet's space: trait 38's
    grants, Stronghold's 10 and Habitat's 20 among them."""
    return fr.unlocked(civ.completed or [])[1]


def max_pop(planet, bonus):
    space = planet.base_space + (R(planet.base_space * bonus / 100) if bonus else 0)
    return (space - (planet.lost_space or 0) - planet.blight) // SPACE_PER_CITIZEN


def empire_corruption(planets, reputation=0):
    """Percent, negative: the share of output every planet loses."""
    scale = 100 * math.atan(planets * CORRUPTION_PLANETS) / (math.pi / 2)
    return scale * (1.5 ** ((reputation - CORRUPTION_REPUTATION)
                            / CORRUPTION_REPUTATION) - 1)


def planet_corruption(empire, pop, cap):
    """The empire's corruption, scaled by how full the planet is."""
    if cap <= 0:
        return empire
    return empire * min(1.0, pop / (CORRUPTION_FULL_AT * cap))


def trait(civ, t):
    return traits.value(civ, t)


def farmer_yield(planet, civ):
    base = planet.rates[0]
    y = base + R(FARM_BONUS * planet.facilities.get(0, 0) * base / 100)
    return y + R(trait(civ, FOOD_TRAIT) * y / 100)


def boosted(base, *bonuses):
    """A per-worker rate plus each facility's percent of it, rounded apiece."""
    return base + sum(R(pct * n * base / 100) for pct, n in bonuses if n)


def worker_yield(planet, civ):
    base = planet.rates[1]
    y = boosted(base, (FACTORY_BONUS, planet.facilities.get(FACTORY, 0)),
                (AUTOMATED_FACTORY_BONUS, planet.facilities.get(AUTOMATED_FACTORY, 0)))
    return y + R(trait(civ, PRODUCTION_TRAIT) * y / 100)


def scientist_yield(planet, civ):
    base = planet.rates[2]
    y = boosted(base, (UNIVERSITY_BONUS, planet.facilities.get(UNIVERSITY, 0)),
                (SCIENCE_LAB_BONUS, planet.facilities.get(SCIENCE_LAB, 0)))
    return y + R(trait(civ, SCIENCE_TRAIT) * y / 100)


def science(p, civ, corruption, idle):
    """Research points a planet adds, losing inactivity at food's half rate."""
    n = p.jobs().get(SCIENTIST, 0)
    if not n:
        return 0
    return after_inactivity(corrupted(n * scientist_yield(p, civ), corruption), idle)


def miner_yield(planet, civ):
    base = planet.ore_rate or 0
    y = boosted(base, (MINE_BONUS, planet.facilities.get(MINE, 0)),
                (ROBO_MINE_BONUS, planet.facilities.get(ROBO_MINE, 0)))
    return y + R(trait(civ, MINING_TRAIT) * y / 100)


FACILITY_COST_TRAIT = {0: 26, 1: 27, 3: 27, 4: 28, 5: 28, 13: 29, 14: 29,
                       9: 30, 10: 30, 11: 30}   # by name; the upgrades' share is assumed


def facility_cost(kind, built, civ=None):
    """Production points for one more of a facility, the manual's escalation,
    then the civ's production cost trait for that building."""
    cost = (4 + (3 + built) * built) * tb.FACILITIES[kind].cost // 4
    t = FACILITY_COST_TRAIT.get(kind)
    return R(traits.pct(civ, t, cost)) if t is not None and civ is not None else cost


def corrupted(gross, corruption):
    """Output after corruption, or with a loyalty bonus when that is positive."""
    return gross - R(gross * -corruption / 100) if corruption else gross


def rank(xp):
    """2 experience is rank 1, and each rank after takes twice as much."""
    return max(0, int(math.log2(xp))) if xp >= 2 else 0


def effective_soldiers(p, civ=None):
    """The manual's soldier count for loyalty: each counts 1 + 0.2 a rank, and
    the more there are the less each adds; military-induced loyalty (trait 34)
    scales it."""
    xps = p.military_xp or [0.0] * len(p.military)
    count = sum(1 + RANK_LOYALTY * rank(x) for x in xps)
    return traits.pct(civ, 34, count * (0.2 + 0.8 / (1.0 + 0.0005 * count * count)))


def loyalty_bonus(p, civ=None):
    """Percent added to (or, below 100% loyalty, taken from) a planet's output.

    A loyal planet's soldiers add the manual's formula, halved because loyalty
    is already full; that is measured (4 soldiers on 24 farmers). Propaganda
    offices add 10% for 10 citizens each and trait 36 adds its percent; those
    follow the manual and the trait list. A resisting planet loses its shortfall
    below 100%, the manual's 'a lot less productive' read as one for one."""
    if not p.citizens:
        return 0.0
    if p.loyalty < 100:
        return p.loyalty - 100
    bonus = 0.0
    if p.military:
        bonus += LOYALTY_PER_SOLDIER * effective_soldiers(p, civ) / 2 / len(p.citizens)
    offices = p.facilities.get(PROPAGANDA_OFFICE, 0)
    if offices:
        swayed = min(len(p.citizens), PROPAGANDA_CITIZENS * offices)
        bonus += traits.pct(civ, 35, PROPAGANDA_LOYALTY * swayed / len(p.citizens))
    return bonus + traits.value(civ, 36)


def citizen_loyalty(arrived, origin, ruler, rulers, penalties=None):
    """One citizen's loyalty to the ruler, the client's 0x004ED270.

    Every other ruler in the planet's conqueror list pulls it down by that
    ruler's influence: all of it on a citizen there before the rule began,
    the share of the rule it lived through (each span capped at 100 turns) on
    one that arrived during it, none on one that came after. A citizen of
    another origin feels half. A citizen not of the ruler's origin then loses
    the ruler's bio-bomb penalty toward its origin, `penalties[origin]`."""
    pull = 0.0
    for owner, influence, first, last in rulers:
        if owner == ruler:
            continue
        if arrived <= first:
            share = influence
        elif arrived <= last:
            share = influence * min(INFLUENCE_SPAN, last - arrived) / min(INFLUENCE_SPAN, last - first)
        else:
            share = 0.0
        pull += share if origin == owner else share * FOREIGN_INFLUENCE
    if origin != ruler and penalties:
        pull += penalties.get(origin, 0)
    return min(100.0, max(0.0, 100.0 - pull))


def planet_loyalty(citizens, ruler, rulers, penalties=None):
    """A planet's loyalty, the mean over (arrived, origin) citizens; 100 with none.
    `penalties` is the ruler's bio-bomb penalty by citizen origin."""
    if not citizens:
        return 100.0
    return sum(citizen_loyalty(t, o, ruler, rulers, penalties)
               for t, o in citizens) / len(citizens)


def conquered(g, p, owner):
    """The people stay loyal to the ruler they lived under for as long as it
    held them, up to all of them after 100 turns (the manual's example)."""
    held = g.turn - (p.founded or 0)
    p.prior_owner, p.unrest = p.owner, 0
    p.loyalty = max(0.0, 100.0 - min(100, held))
    p.founded = g.turn


def settle(g, p, civ):
    """A resisting planet comes round 1 point a turn plus 10% of its
    effective soldiers per citizen, unhalved; below 30% for more than 3 turns
    it may rebel back to the ruler it was taken from."""
    if p.loyalty >= 100 or not p.citizens:
        return
    p.loyalty = min(100.0, p.loyalty + LOYALTY_GROWTH
                    + LOYALTY_PER_SOLDIER * effective_soldiers(p, civ) / len(p.citizens))
    p.unrest = p.unrest + 1 if p.loyalty < REBELLION_LINE else 0
    if p.unrest > REBELLION_GRACE and p.prior_owner in g.civs:
        if (hash((g.turn, p.oid)) % 100) < REBELLION_CHANCE:
            # Untested: the chance is not given anywhere; the defection is.
            p.owner, p.prior_owner = p.prior_owner, p.owner
            p.citizens = [(job, p.owner) for job, _w in p.citizens]
            p.military, p.military_xp = [], []
            p.loyalty, p.unrest = 100.0, 0
            p.production = st.Production('WLTH')


def train(p, civ=None):
    """Training speed takes trait 31 as a percent: Elite's 70 trained 1.7 times as
    fast and Police State's 50 more 2.2 times. Trait 9 left training alone."""
    if not p.military:
        return
    camp = 1 if p.facilities.get(MILITARY_CAMP) else 0
    academy = 1 if p.facilities.get(MILITARY_ACADEMY) else 0
    rate = TRAINING * 2 ** (camp + academy)
    rate = traits.pct(civ, 31, rate)
    cap = 2.0 ** RANK_CAP[2 if academy else camp]
    xps = p.military_xp or [0.0] * len(p.military)
    p.military_xp = [min(cap, x + rate) if x < cap else x for x in xps]


def empires(g):
    """(civ, its planets, space bonus, empire corruption, food idle percent,
    output idle share) for every civ that owns a working planet."""
    owned = {}
    for p in g.planets.values():
        if p.owner and p.food is not None and p.rates:
            owned.setdefault(p.owner, []).append(p)
    for civ_oid, civ_planets in owned.items():
        civ = g.civs.get(civ_oid)
        if civ is None:
            continue
        # Reputation is Owner:32, loaded from the save and moved by battles.
        empire = empire_corruption(len(civ_planets), civ.reputation)
        yield (civ, civ_planets, space_bonus(civ), empire,
               inactivity(g.turn, civ.active_turn), idle_share(g.turn, civ.active_turn))


def corruption_of(p, civ, bonus, empire):
    return planet_corruption(empire, len(p.citizens), max_pop(p, bonus)) + loyalty_bonus(p, civ)


def bank_science(g):
    """The turn's science goes into each civ's store first: the research step
    pays a topic from it in the same tick, and a doctrine bought then shapes
    the rest of the tick's output (the doctrine probes)."""
    for civ, civ_planets, bonus, empire, idle, _share in empires(g):
        points = sum(science(p, civ, corruption_of(p, civ, bonus, empire), idle)
                     for p in civ_planets)
        if civ.uid and civ.research is not None:
            civ.research += points - R(points * wastage(points, civ) / 100)


def planets(g):
    """Production, mining, income, food and growth on every owned planet,
    after the research step."""
    for civ, civ_planets, bonus, empire, idle, share in empires(g):
        civ_oid = civ.oid
        # Ships finished this tick are charged from the next one.
        income = -fleet_upkeep(g, civ_oid)
        for p in civ_planets:
            corruption = corruption_of(p, civ, bonus, empire)
            start = dict(p.facilities)
            income += work(p, civ, corruption, share, g)
            # A facility finished this tick works from the next one.
            built, p.facilities = p.facilities, start
            # Soldiers train before recruitment: a new one starts next tick.
            train(p, civ)
            food(p, civ, bonus, empire, idle, corruption)
            p.facilities = built
            settle(g, p, civ)
            if p.blight:
                p.blight -= BLIGHT_RECOVERY
        if civ.credits is not None:
            civ.credits += income
            if civ.credits < 0:
                pay_debt(g, civ)
        extra = traits.value(civ, EXTRA_METAL_TRAIT)
        if extra and civ.resources is not None:
            civ.resources[0] += extra
        if getattr(civ, 'golden_age', 0) > 0:
            civ.golden_age -= 1


def pay_debt(g, civ):
    """A civ whose credits would go negative loses a ship orbiting its own
    planet, whose crew lands as soldiers, then its costliest facility, then
    soldiers, each only while still in debt, and ends at 0 or above. The
    upkeep probes saw these losses; the engine sometimes retires more
    soldiers than the debt needs."""
    oid = civ.oid
    ships = sorted((s for s in g.ships.values() if s.owner == oid and s.orbit
                    and g.planets.get(s.orbit) is not None and g.planets[s.orbit].owner == oid),
                   key=lambda s: (s.design in g.designs and g.designs[s.design].upkeep, s.oid))
    if ships:
        s = ships[0]
        d = g.designs.get(s.design)
        civ.credits += R(design_upkeep(civ, d)) if d is not None else 0
        p = g.planets[s.orbit]
        p.military = p.military + [(MILITARY, oid)] * len(s.crew)
        p.military_xp = (p.military_xp or [0.0] * (len(p.military) - len(s.crew))) + list(
            s.crew_xp or [0.0] * len(s.crew))
        g.ships.pop(s.oid, None)
        f = g.fleets.get(s.fleet)
        if f is not None and s.oid in f.ships:
            f.ships.remove(s.oid)
    owned = [p for p in g.planets.values() if p.owner == oid]
    if civ.credits < 0:
        built = [(tb.FACILITIES[k].cost, p.oid, k) for p in owned for k, n in p.facilities.items()
                 if n and k < len(tb.FACILITIES)]
        if built:
            cost, poid, k = max(built)
            p = g.planets[poid]
            p.facilities[k] -= 1
            if not p.facilities[k]:
                del p.facilities[k]
            civ.credits += tb.FACILITIES[k].upkeep + cost // LOST_FACILITY_REFUND
    soldier = CREW_UPKEEP + traits.value(civ, 32)
    for p in sorted(owned, key=lambda p: -len(p.military)):
        if civ.credits >= 0:
            break
        n = min(len(p.military), -(civ.credits // soldier))
        if n:
            order = sorted(range(len(p.military)), key=lambda i: (p.military_xp or [0.0] * len(p.military))[i])
            gone = set(order[:n])
            xp = p.military_xp or [0.0] * len(p.military)
            p.military = [m for i, m in enumerate(p.military) if i not in gone]
            p.military_xp = [x for i, x in enumerate(xp) if i not in gone]
            civ.credits += n * soldier
    # Debt the home losses leave costs ships anywhere, then more facilities,
    # so a civ cannot keep a fleet it does not pay for.
    away = sorted((s for s in g.ships.values() if s.owner == oid),
                  key=lambda s: (s.design in g.designs and g.designs[s.design].upkeep, s.oid))
    for s in away:
        if civ.credits >= 0:
            break
        d = g.designs.get(s.design)
        civ.credits += (R(design_upkeep(civ, d)) if d is not None else 0) + CREW_UPKEEP * len(s.crew)
        g.ships.pop(s.oid, None)
        f = g.fleets.get(s.fleet)
        if f is not None and s.oid in f.ships:
            f.ships.remove(s.oid)
    while civ.credits < 0:
        built = [(tb.FACILITIES[k].cost, p.oid, k) for p in owned for k, n in p.facilities.items()
                 if n and k < len(tb.FACILITIES)]
        if not built:
            break
        cost, poid, k = max(built)
        p = g.planets[poid]
        p.facilities[k] -= 1
        if not p.facilities[k]:
            del p.facilities[k]
        civ.credits += tb.FACILITIES[k].upkeep + cost // LOST_FACILITY_REFUND
    civ.credits = max(0, civ.credits)


def wastage(points, civ):
    """Percent of a turn's science lost: the manual's curve, nothing to speak
    of below 1,000 points, 15% at 10,000, 27% at 30,000 and never above 30,
    plus trait 39. The curve is fitted to those three figures; trait 39 is
    measured: Doctrine: Knowledge's -7 turned 40 points into 43."""
    base = WASTAGE_CAP * (1 - math.exp(-points / WASTAGE_SCALE)) if points > WASTAGE_FROM else 0.0
    return min(WASTAGE_CAP, base + traits.value(civ, 39))


def idle_share(turn, active_turn):
    """The fraction of production, mining and income lost for not playing."""
    if active_turn is None:
        return 0.0
    return max(0.0, min(1.0, (turn + 1 - INACTIVITY_GRACE - active_turn)
                        * IDLE_OUTPUT_PER_TURN))


def after_idle(made, share):
    return made - R(made * share) if share else made


def income_after(gross, corruption, share):
    """Income loses corruption and inactivity in one rounding."""
    lost = -corruption / 100
    lost += share * IDLE_INCOME_PER_TURN / IDLE_OUTPUT_PER_TURN
    return gross - R(gross * lost)


def work(p, civ, corruption, share, g=None):
    """Production, mining and the planet's income, before its people grow.
    Returns the planet's credits net of the upkeep of the facilities it had
    when the turn began."""
    jobs = p.jobs()
    upkeep = sum(tb.FACILITIES[f].upkeep * n for f, n in p.facilities.items()
                 if f < len(tb.FACILITIES)) + (CREW_UPKEEP + traits.value(civ, 32)) * len(p.military)
    made = after_idle(corrupted(jobs.get(WORKER, 0) * worker_yield(p, civ),
                                corruption), share)
    if p.ore is not None and civ.resources is not None and jobs.get(MINER):
        civ.resources[p.ore] += after_idle(
            corrupted(jobs[MINER] * miner_yield(p, civ), corruption), share)
    wealth = build(p, made, g, civ)
    gross = ((INCOME_PER_CITIZEN + traits.value(civ, 1)) * len(p.citizens)
             + R(traits.pct(civ, 37, INCOME_PER_BANKER * jobs.get(BANKER, 0))))
    gross = boosted(gross, (BANKING_BONUS, p.facilities.get(BANKING_CENTER, 0)))
    return income_after(gross, corruption, share) + wealth - upkeep


def ship_cost(g, owner, design):
    """Production points for a ship. A colony ship costs more the bigger the
    empire: fitted on five builds, four exactly and one 100 short."""
    civ = g.civs.get(owner)
    cost = design.cost
    modules = design.parts.get('modules') or ()
    for m, t in ((COLONY_MODULE, 23), (CLOAKING_DEVICE, 24)):
        if m in modules and civ is not None:
            part = tb.MODULES[m].cost * modules.count(m)
            cost += R(traits.pct(civ, t, part)) - part
    if civ is not None:
        cost = R(traits.pct(civ, 14, cost))
        if design.chassis == BATTLESHIP:
            cost = R(traits.pct(civ, 21, cost))
    if COLONY_MODULE in modules:
        planets = sum(1 for q in g.planets.values() if q.owner == owner)
        colony = sum(1 for s in g.ships.values() if s.owner == owner and s.design in g.designs
                     and COLONY_MODULE in (g.designs[s.design].parts.get('modules') or ()))
        cost += COLONY_SURCHARGE * max(0, planets + colony - 1)
    return cost


def launch(g, p, design):
    """A finished ship: in orbit, uncrewed, unhurt and without orders. Its
    resources were charged when the build was ordered."""
    g.high_water += 1
    g.ships[g.high_water] = st.Ship(g.high_water, p.owner, p.pos, design.oid, [], 1.0,
                                    orbit=p.oid)


def build(p, made, g=None, civ=None):
    """Add this turn's production to the current item and finish it when paid
    for. Returns the credits a planet on Wealth makes instead."""
    prod = p.production
    if prod is None:
        return 0
    if prod.kind == 'WLTH':
        return R(traits.pct(civ, 40, made / WEALTH_PER_CREDIT))
    prod.progress += made
    if prod.kind == 'FCLT' and prod.item is not None:
        cost = facility_cost(prod.item, p.facilities.get(prod.item, 0), civ)
        if prod.progress >= cost:
            prod.progress -= cost
            p.facilities[prod.item] = p.facilities.get(prod.item, 0) + 1
            # Queues are not modelled; a finished item falls to Wealth.
            prod.kind, prod.item, prod.hurried = 'WLTH', None, False
    elif prod.kind == 'SCAN' and prod.item in SCAN_COSTS and civ is not None:
        cost = round(traits.pct(civ, 25, SCAN_COSTS[prod.item]))
        if prod.progress >= cost:
            prod.progress -= cost
            civ.scans[prod.item] = civ.scans.get(prod.item, 0) + 1
            prod.kind, prod.item, prod.hurried = 'WLTH', None, False
    elif prod.kind == 'SHIP' and g is not None and prod.item in g.designs:
        design = g.designs[prod.item]
        cost = ship_cost(g, p.owner, design)
        if prod.progress >= cost:
            prod.progress -= cost
            launch(g, p, design)
            prod.kind, prod.item, prod.hurried = 'WLTH', None, False
    return 0


def fleet_upkeep(g, civ_oid):
    """Design upkeep, raised by trait 15 (and 21 for battleships), and 3 a
    crew member."""
    civ = g.civs.get(civ_oid)
    ships = crew = 0
    for s in g.ships.values():
        if s.owner != civ_oid:
            continue
        d = g.designs.get(s.design)
        if d is not None:
            ships += design_upkeep(civ, d)
        crew += len(s.crew)
    return R(ships) + CREW_UPKEEP * crew


def design_upkeep(civ, d):
    u = traits.pct(civ, 15, d.upkeep)
    return traits.pct(civ, 21, u) if d.chassis == BATTLESHIP else u


def inactivity(turn, active_turn):
    """Percent of output lost for not playing, as the tick applies it to food:
    half a point a turn, from the tick that ends turn 72 after the civ was
    last active."""
    if active_turn is None:
        return 0.0
    return max(0.0, min(100.0, (turn + 1 - INACTIVITY_GRACE - active_turn)
                        / INACTIVITY_TURNS_PER_POINT))


def after_inactivity(made, idle):
    return made - R(made * idle / 100) if idle else made


def activity(g):
    """A civ seen active this turn has its baseline moved to this turn. The
    engine then copies a pending flag the save does not carry, so the flag
    reads clear."""
    for civ in g.civs.values():
        if civ.active:
            civ.active_turn = g.turn
        civ.active = False


def food(p, civ, bonus, empire, idle=0.0, corruption=None):
    pop = len(p.citizens)
    farmers = sum(1 for job, _w in p.citizens if job == FARMER)
    if corruption is None:
        corruption = planet_corruption(empire, pop, max_pop(p, bonus))
    made = after_inactivity(
        corrupted(farmers * farmer_yield(p, civ), corruption), idle)
    surplus = made - (FOOD_PER_CITIZEN + traits.value(civ, 0)) * pop
    store, cap = p.food
    if p.military_food is not None:
        p.military_cost = R(traits.pct(civ, traits.RECRUIT_FOOD, SOLDIER_FOOD))
    if p.recruit and surplus > 0 and p.military_food is not None:
        share = int(surplus * p.recruit / 100)
        surplus -= share
        recruit(p, after_inactivity(share, 2 * idle))
    store += surplus
    full = pop >= max_pop(p, bonus)
    if full:
        # The manual's limit: no births past the planet's space, and the
        # store holds at its size.
        store = min(store, cap)
    grew = store >= cap and not full
    if grew:
        store -= cap
        p.citizens.insert(0, (FARMER, p.owner))
        if p.loyalty < 100:
            # A citizen born under the ruler is wholly loyal to it.
            p.loyalty += (100 - p.loyalty) / len(p.citizens)
    elif store < 0:
        # The manual's starvation rule; no capture has shown it yet.
        store = 0
        if p.citizens:
            p.citizens.pop(0)
    cap = push_history(p, grew)
    p.food = (store, cap)


def recruit(p, food):
    """The recruitment share goes to the military store, which makes a soldier
    each time it fills. Both stores match on 67 of 72 recruiting planet-turns;
    the rest are 1 food off on an idle civ."""
    p.military_food += food
    while p.military_cost and p.military_food >= p.military_cost:
        p.military_food -= p.military_cost
        p.military.append((MILITARY, p.owner))
        p.military_xp = (p.military_xp or [0.0] * (len(p.military) - 1)) + [0.0]


def push_history(p, grew):
    """Record this turn's population and return the store's new size: the
    manual's size for the average of the entries since the last birth,
    rounded up."""
    pop = len(p.citizens)
    if p.pop_history is None:
        return food_capacity(pop)
    older = p.pop_history[:HISTORY_SLOTS - 1]
    if grew:
        older = [w | HISTORY_OLD if w else w for w in older]
    p.pop_history = [pop] + older
    p.history_len = min(HISTORY_SLOTS, p.history_len + 1)
    kept = [w for w in p.pop_history[:p.history_len] if not w & HISTORY_OLD]
    return food_capacity(math.ceil(sum(kept) / len(kept)) if kept else pop)
