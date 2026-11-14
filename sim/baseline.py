"""
A scripted baseline: grow, settle and arm, with no learning in it.

It is a sparring partner for a learned policy and a smoke test for the
simulator: recruit a little, crew colony ships and send them to the nearest
unowned planet in a known system, build farms then factories, and a warship
from the best armed design once a planet has a shipyard and the resources;
research the cheapest open technology and keep one miner on each planet.
"""
import math

from . import economy as ec
from . import orders as od
from . import research as rs
from . import reward as rw

RECRUIT = 10                    # percent of surplus food to soldiers


def act(g, civ):
    """This turn's actions for one civ, from full state (the baseline does not
    need to be fog-fair to serve as a smoke test)."""
    out = []
    claimed = set()
    mine = [p for p in g.planets.values() if p.owner == civ]
    known = set(g.civs[civ].explored) | {p.system for p in mine}
    best = rw.best_design(g, civ)
    c = g.civs[civ]
    if c.topic is None or c.topic < 0:
        open_now = [t for t in rs.TECHS if rs.available(t, c.completed or [])
                    and not rs.is_doctrine(t)]
        if open_now:
            out.append(('research', min(open_now, key=lambda t: (rs.TECHS[t][1], t))))
    for p in mine:
        jobs = p.jobs()
        if p.ore_rate and len(p.citizens) >= 6 and not jobs.get(ec.MINER):
            jobs[ec.FARMER] = jobs.get(ec.FARMER, 0) - 1
            jobs[ec.MINER] = 1
            if jobs[ec.FARMER] >= 0:
                out.append(('jobs', p.oid, jobs))
        if p.recruit != RECRUIT:
            out.append(('recruit', p.oid, RECRUIT))
        prod = p.production
        if prod is None or prod.kind == 'WLTH':
            if best and p.facilities.get(od.SHIPYARD):
                out.append(('ship', p.oid, best.oid))
            elif p.facilities.get(0, 0) < 3:
                out.append(('facility', p.oid, 0))
            else:
                out.append(('facility', p.oid, 1))
    for s in g.ships.values():
        if s.owner != civ or s.order is not None:
            continue
        d = g.designs.get(s.design)
        if d is None:
            continue
        colony = ec.COLONY_MODULE in (d.parts.get('modules') or ())
        here = g.planets.get(s.orbit)
        if colony and not s.crew and here is not None and here.owner == civ and len(here.military) >= 2:
            out.append(('crew', s.oid, 2))
            continue
        if colony and s.crew:
            targets = [q for q in g.planets.values() if not q.owner and q.system in known
                       and q.oid not in claimed and q.rates]
            if targets:
                t = min(targets, key=lambda q: math.dist(q.pos, s.pos))
                claimed.add(t.oid)
                out.append(('colonise', s.oid, t.oid))
            else:
                out.append(('scout', s.oid))
    return out


def policy(g, civs):
    return {c: act(g, c) for c in civs}
