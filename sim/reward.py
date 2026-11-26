"""
Dominance, the training reward's shaping term (docs/RL_Training_Plan.md).

    S   fleet strength: effective firepower times effective hitpoints, counting
        only the share of the fleet the civ's income can keep paying for
    T   ground strength: the same product for soldiers, stationed on the civ's
        planets and aboard its troop ships, by the manual's ground combat
    G   surge capacity: the strength the civ could add in K turns, building its
        best design with its production after corruption, ship resources and
        upkeep headroom; reputation reaches it through corruption
    D   each civ's share of the galaxy's citizens times its security, the
        chance no rival takes its people, normalised to sum to 1

A rival threatens a planet by winning its orbit, S and G against the owner's
S and the planet's turrets, and then its ground, T against the garrison; T
counts only for a civ with a Troop Bay design. A civ no rival can break keeps
its whole share, whether it holds by fleet, turrets or garrison, and a civ
that can break its rivals lowers their D and so raises its own.

Strength is a product so that a fleet twice the size counts four times, as it
does in the manual's combat, where each side takes the other's hitpoints off at
its own firepower per round.
"""
import math

from . import combat
from . import economy as ec
from . import tables as tb
from . import traits

K_TURNS = 35                    # the median warship trip between rival homeworlds
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


def corruption(g, civ):
    """{planet: percent} as the economy step applies it, reputation and
    loyalty included; negative is output lost."""
    c = g.civs.get(civ)
    mine = [p for p in g.planets.values() if p.owner == civ and p.food is not None and p.rates]
    if c is None or not mine:
        return {}
    empire = ec.empire_corruption(len(mine), c.reputation)
    bonus = ec.space_bonus(c)
    return {p.oid: ec.corruption_of(p, c, bonus, empire) for p in mine}


def income_before_fleet(g, civ):
    """A civ's credits a turn before fleet upkeep, after corruption."""
    c = g.civs.get(civ)
    citizen = ec.INCOME_PER_CITIZEN + traits.value(c, 1)
    soldier = ec.CREW_UPKEEP + traits.value(c, 32)
    lost = corruption(g, civ)
    total = 0
    for p in g.planets.values():
        if p.owner != civ:
            continue
        jobs = p.jobs()
        gross = citizen * len(p.citizens) + traits.pct(c, 37, ec.INCOME_PER_BANKER * jobs.get(ec.BANKER, 0))
        total += ec.corrupted(gross, lost.get(p.oid, 0.0))
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
        if len(s.crew) >= combat.crew_needed(d, c) and _fire(d):
            f, h = _armed(c, d)
            fire += f * (1 + combat.RANK_BONUS * combat.ship_rank(s))
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
    lost = corruption(g, civ)
    made = sum(ec.corrupted(p.jobs().get(ec.WORKER, 0) * ec.worker_yield(p, c), lost.get(p.oid, 0.0))
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


def planet_defence(g, p):
    """A planet's turrets and shields as firepower times hitpoints."""
    if not p.owner:
        return 0.0
    d = combat.Planet(g, p)
    return (d.fire[0] + d.fire[1]) * d.hp


def garrison(g, p):
    """A planet's soldiers as firepower times health, bunkers and fortresses
    included."""
    if not p.military:
        return 0.0
    side = combat.ground_side(p.military_xp or [0.0] * len(p.military),
                              p.facilities.get(combat.BUNKER, 0),
                              p.facilities.get(combat.FORTRESS, 0), g.civs.get(p.owner))
    return sum(f for _h, f in side) * sum(h for h, _f in side)


def can_land(g, civ):
    """The civ holds a design with a Troop Bay, so its soldiers can attack."""
    return any(d.owner == civ and not d.inactive and ec.TROOP_BAY in (d.parts.get('modules') or ())
               for d in g.designs.values())


def odds(attack, defence):
    """The attacker's share of the two strengths; nothing attacks at 0."""
    return attack / (attack + defence) if attack > 0 else 0.0


def threat(g, a, b, space=None, troops=None):
    """How much of civ b's population civ a could take: for each of b's
    planets, the chance a's fleet and surge win its orbit against b's fleet
    and the planet's turrets, times the chance a's soldiers beat its garrison,
    weighted by the planet's citizens."""
    space = space if space is not None else strength(g, a) + surge(g, a)
    troops = troops if troops is not None else (ground(g, a) if can_land(g, a) else 0.0)
    held = [p for p in g.planets.values() if p.owner == b]
    people = sum(len(p.citizens) for p in held)
    if not held or not space or not troops:
        return 0.0
    fleet = strength(g, b)
    taken = 0.0
    for p in held:
        weight = len(p.citizens) / people if people else 1 / len(held)
        taken += weight * odds(space, fleet + planet_defence(g, p)) * odds(troops, garrison(g, p))
    return taken


def dominance(g, civs=None):
    """{civ: D} for the given civs: each civ's share of the galaxy's citizens
    times its security, the chance no rival takes its people, normalised so
    the civs' D sum to 1. A civ no rival can break, by fleet, turrets or
    garrison, keeps its whole share; a civ that can break its rivals lowers
    theirs and so raises its own."""
    everyone = list(g.civs)
    alive = {c for c in everyone if any(p.owner == c for p in g.planets.values())
             or any(x.owner == c for x in g.ships.values())}
    people = {c: sum(len(p.citizens) for p in g.planets.values() if p.owner == c) + EPSILON
              for c in everyone}
    total = sum(people[c] for c in alive) or 1.0
    space = {c: strength(g, c) + surge(g, c) for c in alive}
    troops = {c: ground(g, c) if can_land(g, c) else 0.0 for c in alive}
    raw = {}
    for b in alive:
        secure = 1.0
        for a in alive:
            if a != b:
                secure *= 1.0 - threat(g, a, b, space[a], troops[a])
        raw[b] = people[b] / total * secure
    norm = sum(raw.values()) or 1.0
    return {c: raw.get(c, 0.0) / norm for c in (civs or everyone)}
