"""
The planet step of a tick: food, corruption, growth, production, mining and
income.

Measured against engine ticks (docs/Fast_Sim_Plan.md, the economy section):
every rule here reproduces the archive pairs exactly unless its comment says
otherwise.
"""
import math

from . import load as sl
from . import tables as tb

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
PRODUCTION_TRAIT = 3            # CVTR indices
MINING_TRAIT = 5
INCOME_PER_CITIZEN = 10
INCOME_PER_BANKER = 50          # on top of the citizen's own
WEALTH_PER_CREDIT = 4           # production points a credit costs on Wealth
CREW_UPKEEP = 3                 # credits per crew member aboard a ship
FOOD_PER_CITIZEN = 10           # eaten by every citizen each turn
FARM_BONUS = 25                 # percent of the base rate, per farm
FOOD_TRAIT = 2                  # CVTR index of the civ's food output bonus
CORRUPTION_PLANETS = 0.025      # the manual's empire-wide corruption terms
CORRUPTION_REPUTATION = 25
CORRUPTION_FULL_AT = 0.75       # share of max population at full corruption
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
    """The percent the civ's research adds to every planet's space."""
    return fr.unlocked(civ.completed or [])[1]


def max_pop(planet, bonus):
    space = planet.base_space + (R(planet.base_space * bonus / 100) if bonus else 0)
    return (space - (planet.lost_space or 0)) // SPACE_PER_CITIZEN


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


def trait(civ, index):
    return (civ.traits or ())[index] if civ.traits and index < len(civ.traits) else 0


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


def miner_yield(planet, civ):
    base = planet.ore_rate or 0
    y = boosted(base, (MINE_BONUS, planet.facilities.get(MINE, 0)),
                (ROBO_MINE_BONUS, planet.facilities.get(ROBO_MINE, 0)))
    return y + R(trait(civ, MINING_TRAIT) * y / 100)


def facility_cost(kind, built):
    """Production points for one more of a facility, the manual's escalation."""
    return (4 + (3 + built) * built) * tb.FACILITIES[kind].cost // 4


def corrupted(gross, corruption):
    """Output left after corruption takes its share."""
    return gross - R(gross * -corruption / 100) if corruption < 0 else gross


def planets(g, reputation=None):
    """Food, corruption and growth on every owned planet."""
    reputation = reputation or {}
    owned = {}
    for p in g.planets.values():
        if p.owner and p.food is not None and p.rates:
            owned.setdefault(p.owner, []).append(p)
    for civ_oid, civ_planets in owned.items():
        civ = g.civs.get(civ_oid)
        if civ is None:
            continue
        bonus = space_bonus(civ)
        empire = empire_corruption(len(civ_planets), reputation.get(civ_oid, 0))
        idle = inactivity(g.turn, civ.active_turn)
        share = idle_share(g.turn, civ.active_turn)
        income = 0
        for p in civ_planets:
            corruption = planet_corruption(empire, len(p.citizens), max_pop(p, bonus))
            income += work(p, civ, corruption, share)
            food(p, civ, bonus, empire, idle)
        if civ.credits is not None:
            civ.credits += income - fleet_upkeep(g, civ_oid)


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
    lost = -corruption / 100 if corruption < 0 else 0
    lost += share * IDLE_INCOME_PER_TURN / IDLE_OUTPUT_PER_TURN
    return gross - R(gross * lost)


def work(p, civ, corruption, share):
    """Production, mining and the planet's income, before its people grow.
    Returns the planet's credits net of the upkeep of the facilities it had
    when the turn began."""
    jobs = p.jobs()
    upkeep = sum(tb.FACILITIES[f].upkeep * n for f, n in p.facilities.items()
                 if f < len(tb.FACILITIES))
    made = after_idle(corrupted(jobs.get(WORKER, 0) * worker_yield(p, civ),
                                corruption), share)
    wealth = build(p, made)
    if p.ore is not None and civ.resources is not None and jobs.get(MINER):
        civ.resources[p.ore] += after_idle(
            corrupted(jobs[MINER] * miner_yield(p, civ), corruption), share)
    gross = (INCOME_PER_CITIZEN * len(p.citizens)
             + INCOME_PER_BANKER * jobs.get(BANKER, 0))
    gross = boosted(gross, (BANKING_BONUS, p.facilities.get(BANKING_CENTER, 0)))
    return income_after(gross, corruption, share) + wealth - upkeep


def build(p, made):
    """Add this turn's production to the current item and finish it when paid
    for. Returns the credits a planet on Wealth makes instead."""
    prod = p.production
    if prod is None:
        return 0
    if prod.kind == 'WLTH':
        return R(made / WEALTH_PER_CREDIT)
    prod.progress += made
    if prod.kind == 'FCLT' and prod.item is not None:
        cost = facility_cost(prod.item, p.facilities.get(prod.item, 0))
        if prod.progress >= cost:
            prod.progress -= cost
            p.facilities[prod.item] = p.facilities.get(prod.item, 0) + 1
            # No capture has shown a queue advance; an empty queue falls to Wealth.
            prod.kind, prod.item = 'WLTH', None
    return 0


def fleet_upkeep(g, civ_oid):
    total = 0
    for s in g.ships.values():
        if s.owner != civ_oid:
            continue
        d = g.designs.get(s.design)
        total += (d.upkeep if d else 0) + CREW_UPKEEP * len(s.crew)
    return total


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


def food(p, civ, bonus, empire, idle=0.0):
    pop = len(p.citizens)
    farmers = sum(1 for job, _w in p.citizens if job == FARMER)
    corruption = planet_corruption(empire, pop, max_pop(p, bonus))
    made = after_inactivity(
        corrupted(farmers * farmer_yield(p, civ), corruption), idle)
    surplus = made - FOOD_PER_CITIZEN * pop
    store, cap = p.food
    if p.recruit and surplus > 0 and p.military_food is not None:
        share = int(surplus * p.recruit / 100)
        surplus -= share
        recruit(p, after_inactivity(share, 2 * idle))
    store += surplus
    grew = store >= cap
    if grew:
        store -= cap
        p.citizens.insert(0, (FARMER, p.owner))
    elif store < 0:
        # The manual's starvation rule; no capture has shown it yet.
        store = 0
        if p.citizens:
            p.citizens.pop(0)
    cap = push_history(p, grew)
    p.food = (store, cap)


def recruit(p, food):
    """The recruitment share goes to the military store, which makes a soldier
    each time it fills. Approximate: on a civ inactive for 38 turns this
    matches the military store, and the civilian store comes out 1 to 3 food
    under the engine's."""
    p.military_food += food
    while p.military_cost and p.military_food >= p.military_cost:
        p.military_food -= p.military_cost
        p.military.append((MILITARY, p.owner))


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
