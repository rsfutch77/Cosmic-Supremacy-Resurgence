"""
Dominance, the training reward (docs/Fast_Sim_Plan.md, section 1a).

    S   fleet strength: effective firepower times effective hitpoints, counting
        only the share of the fleet the civ's income can keep paying for
    T   ground strength: the same product for soldiers, stationed on the civ's
        planets and aboard its troop ships, by the manual's ground combat
    G   surge capacity: the strength the civ could add in K turns, building its
        best design with its production, ship resources and upkeep headroom
    D   the geometric mean of the shares of S, T and G, each of the galaxy's
        total

T is there because the first self-play check (docs/Fast_Sim_Plan.md, 5b) saw
a fleet that D rated far above its target fail to take a planet held by 21
soldiers: space strength wins orbit, and soldiers decide who keeps a planet.

Strength is a product so that a fleet twice the size counts four times, as it
does in the manual's combat, where each side takes the other's hitpoints off at
its own firepower per round. Untested against outcomes: the plan's open item is
self-play showing the higher-D civ wins its wars.
"""
import math

from . import combat
from . import economy as ec
from . import tables as tb
from . import traits

K_TURNS = 20                    # until a rival fleet arrives; open in the plan
EPSILON = 1.0                   # keeps shares defined before anyone arms


def _fire(design):
    light, heavy, _bomb = design.firepower
    return light + heavy


def _armed(civ, d):
    """(firepower, hitpoints) of a design with the civ's traits, as combat
    fights it."""
    return _fire(d) * combat.fire_bonus(civ, d), combat.hitpoints(civ, d)


def fleet_upkeep(g, civ):
    return ec.fleet_upkeep(g, civ)


def income_before_fleet(g, civ):
    """A civ's credits a turn before fleet upkeep, without corruption."""
    c = g.civs.get(civ)
    citizen = ec.INCOME_PER_CITIZEN + traits.value(c, 1)
    soldier = ec.CREW_UPKEEP + traits.value(c, 32)
    total = 0
    for p in g.planets.values():
        if p.owner != civ:
            continue
        jobs = p.jobs()
        total += citizen * len(p.citizens) + traits.pct(c, 37, ec.INCOME_PER_BANKER * jobs.get(ec.BANKER, 0))
        total -= sum(tb.FACILITIES[f].upkeep * n for f, n in p.facilities.items() if f < len(tb.FACILITIES))
        total -= soldier * len(p.military)
    return total


def strength(g, civ):
    c = g.civs.get(civ)
    fire = hp = 0.0
    for s in g.ships.values():
        if s.owner != civ or s.design not in g.designs:
            continue
        d = g.designs[s.design]
        need = tb.CHASSIS[d.chassis].crew[0] if d.chassis is not None else 0
        if len(s.crew) >= need and _fire(d):
            f, h = _armed(c, d)
            fire += f * (1 + combat.RANK_BONUS * combat.ship_rank(s, need))
            hp += h * s.condition
    upkeep = fleet_upkeep(g, civ)
    budget = income_before_fleet(g, civ)
    sustain = 1.0 if upkeep <= max(budget, 0) else max(budget, 0) / upkeep
    return fire * hp * sustain


def best_design(g, civ):
    """The armed design giving the most strength per production point squared."""
    c = g.civs.get(civ)
    best, score = None, 0.0
    for d in g.designs.values():
        if d.owner != civ or d.inactive or not _fire(d) or not d.cost:
            continue
        f, h = _armed(c, d)
        v = f * h / ec.ship_cost(g, civ, d) ** 2
        if v > score:
            best, score = d, v
    return best


def surge(g, civ, k=K_TURNS):
    d = best_design(g, civ)
    if d is None:
        return 0.0
    c = g.civs[civ]
    made = sum(p.jobs().get(ec.WORKER, 0) * ec.worker_yield(p, c)
               for p in g.planets.values() if p.owner == civ
               and p.facilities.get(combat.SHIPYARD))
    n = made * k / ec.ship_cost(g, civ, d)
    store = c.resources or [0] * 5
    for have, need in zip(store, d.resources):
        if need:
            n = min(n, have / need)
    headroom = income_before_fleet(g, civ) - fleet_upkeep(g, civ)
    upkeep = ec.design_upkeep(c, d)
    if upkeep:
        n = min(n, max(0.0, headroom) / upkeep)
    f, h = _armed(c, d)
    return (n * f) * (n * h)


def ground(g, civ):
    """Soldiers' total firepower times total health, the manual's ground
    combat: 1 health and 2 firepower each, +10% and +20% a rank, bunkers and
    fortresses for the best of a planet's garrison, and troops aboard ships
    with a Troop Bay, who are the ones that can take a planet."""
    fire = health = 0.0
    for p in g.planets.values():
        if p.owner != civ or not p.military:
            continue
        side = combat.ground_side(p.military_xp or [0.0] * len(p.military),
                                  p.facilities.get(combat.BUNKER, 0),
                                  p.facilities.get(combat.FORTRESS, 0), g.civs.get(civ))
        health += sum(h for h, _f in side)
        fire += sum(f for _h, f in side)
    for s in g.ships.values():
        if s.owner == civ and s.design in g.designs:
            side = combat.ground_side(combat.troops_aboard(g, s), 0, 0, g.civs.get(civ))
            health += sum(h for h, _f in side)
            fire += sum(f for _h, f in side)
    return fire * health


def dominance(g, civs=None):
    """{civ: D} for the given civs, shares taken over every civ in the galaxy."""
    everyone = list(g.civs)
    terms = [{c: fn(g, c) + EPSILON for c in everyone} for fn in (strength, ground, surge)]
    totals = [sum(t.values()) for t in terms]
    alive = {c for c in everyone if any(p.owner == c for p in g.planets.values())
             or any(x.owner == c for x in g.ships.values())}
    out = {}
    for c in (civs or everyone):
        product = 1.0
        for t, total in zip(terms, totals):
            product *= t[c] / total
        out[c] = product ** (1 / len(terms)) if c in alive else 0.0
    return out
