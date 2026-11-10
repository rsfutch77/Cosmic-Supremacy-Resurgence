"""
The planet step of a tick: food, corruption and growth.

Measured against engine ticks (docs/Fast_Sim_Plan.md, the economy section):
every rule here reproduces the archive pairs exactly unless its comment says
otherwise.
"""
import math

from . import load as sl

import facility_rules as fr                                     # noqa: E402

FARMER = 0
MILITARY = 3
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
        for p in civ_planets:
            food(p, civ, bonus, empire, idle)


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
