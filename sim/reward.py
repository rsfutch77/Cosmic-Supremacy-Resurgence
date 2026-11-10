"""
Dominance, the training reward (docs/Fast_Sim_Plan.md, section 1a).

    S   fleet strength: effective firepower times effective hitpoints, counting
        only the share of the fleet the civ's income can keep paying for
    G   surge capacity: the strength the civ could add in K turns, building its
        best design with its production, ship resources and upkeep headroom
    D   sqrt(share of S x share of G), each share of the galaxy's total

Strength is a product so that a fleet twice the size counts four times, as it
does in the manual's combat, where each side takes the other's hitpoints off at
its own firepower per round. Untested against outcomes: the plan's open item is
self-play showing the higher-D civ wins its wars.
"""
import math

from . import combat
from . import economy as ec
from . import tables as tb

K_TURNS = 20                    # until a rival fleet arrives; open in the plan
EPSILON = 1.0                   # keeps shares defined before anyone arms


def _fire(design):
    light, heavy, _bomb = design.firepower
    return light + heavy


def fleet_upkeep(g, civ):
    return ec.fleet_upkeep(g, civ)


def income_before_fleet(g, civ):
    """A civ's credits a turn before fleet upkeep, without corruption."""
    total = 0
    for p in g.planets.values():
        if p.owner != civ:
            continue
        jobs = p.jobs()
        total += ec.INCOME_PER_CITIZEN * len(p.citizens) + ec.INCOME_PER_BANKER * jobs.get(ec.BANKER, 0)
        total -= sum(tb.FACILITIES[f].upkeep * n for f, n in p.facilities.items() if f < len(tb.FACILITIES))
        total -= ec.CREW_UPKEEP * len(p.military)
    return total


def strength(g, civ):
    fire = hp = 0.0
    for s in g.ships.values():
        if s.owner != civ or s.design not in g.designs:
            continue
        d = g.designs[s.design]
        need = tb.CHASSIS[d.chassis].crew[0] if d.chassis is not None else 0
        if len(s.crew) >= need and _fire(d):
            fire += _fire(d) * (1 + combat.RANK_BONUS * combat.ship_rank(s, need))
            hp += d.hitpoints * s.condition
    upkeep = fleet_upkeep(g, civ)
    budget = income_before_fleet(g, civ)
    sustain = 1.0 if upkeep <= max(budget, 0) else max(budget, 0) / upkeep
    return fire * hp * sustain


def best_design(g, civ):
    """The armed design giving the most strength per production point squared."""
    best, score = None, 0.0
    for d in g.designs.values():
        if d.owner != civ or d.inactive or not _fire(d) or not d.cost:
            continue
        v = _fire(d) * d.hitpoints / d.cost ** 2
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
    n = made * k / d.cost
    store = c.resources or [0] * 5
    for have, need in zip(store, d.resources):
        if need:
            n = min(n, have / need)
    headroom = income_before_fleet(g, civ) - fleet_upkeep(g, civ)
    if d.upkeep:
        n = min(n, max(0.0, headroom) / d.upkeep)
    return (n * _fire(d)) * (n * d.hitpoints)


def dominance(g, civs=None):
    """{civ: D} for the given civs, shares taken over every civ in the galaxy."""
    everyone = list(g.civs)
    s = {c: strength(g, c) + EPSILON for c in everyone}
    gr = {c: surge(g, c) + EPSILON for c in everyone}
    ts, tg = sum(s.values()), sum(gr.values())
    alive = {c for c in everyone if any(p.owner == c for p in g.planets.values())
             or any(x.owner == c for x in g.ships.values())}
    return {c: (math.sqrt(s[c] / ts * gr[c] / tg) if c in alive else 0.0)
            for c in (civs or everyone)}
