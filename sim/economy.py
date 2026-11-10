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
FOOD_PER_CITIZEN = 10           # eaten by every citizen each turn
FARM_BONUS = 25                 # percent of the base rate, per farm
FOOD_TRAIT = 2                  # CVTR index of the civ's food output bonus
CORRUPTION_PLANETS = 0.025      # the manual's empire-wide corruption terms
CORRUPTION_REPUTATION = 25
CORRUPTION_FULL_AT = 0.75       # share of max population at full corruption
SPACE_PER_CITIZEN = 10


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
        for p in civ_planets:
            food(p, civ, bonus, empire)


def food(p, civ, bonus, empire):
    pop = len(p.citizens)
    farmers = sum(1 for job, _w in p.citizens if job == FARMER)
    corruption = planet_corruption(empire, pop, max_pop(p, bonus))
    made = corrupted(farmers * farmer_yield(p, civ), corruption)
    surplus = made - FOOD_PER_CITIZEN * pop
    store, cap = p.food
    store += surplus
    if store >= cap:
        store -= cap
        p.citizens.insert(0, (FARMER, p.owner))
        cap = food_capacity(len(p.citizens))
    elif store < 0:
        # The manual's starvation rule; no capture has shown it yet.
        store = 0
        if p.citizens:
            p.citizens.pop(0)
    p.food = (store, cap)
