"""
The movement step of a tick: ships follow their routes, and scouts pick the
next system when they arrive.

Measured against engine ticks (docs/Fast_Sim_Plan.md, the movement section).
A route is straight legs whose ends stand off each body by a little more than
its radius; a ship's position is its progress along them.
"""
import math

ORBIT_CLEARANCE = 1.02          # radii between a body's centre and a route's end
SPEED_TRAIT = 6                 # CVTR index, percent; no capture has a nonzero one
MOVE, SCOUT, COLONISE = 1, 2, 3


def speed(g, ship):
    """Thrust over hull units, as the client's design screen shows it."""
    d = g.designs.get(ship.design)
    if d is None or not d.units:
        return 0.0
    v = d.thrust / d.units
    civ = g.civs.get(ship.owner)
    pct = civ.traits[SPEED_TRAIT] if civ and civ.traits else 0
    return v * (1 + pct / 100) if pct else v


def along(legs, progress):
    """The point `progress` units along a list of (origin, dest, length)."""
    for origin, dest, length in legs:
        if progress <= length or (origin, dest, length) == legs[-1]:
            f = progress / length if length else 1.0
            return tuple(o + (t - o) * f for o, t in zip(origin, dest))
        progress -= length
    return legs[-1][1] if legs else None


def leg(start, start_r, end, end_r):
    """One straight leg between two bodies, clear of both."""
    gap = math.dist(start, end)
    if gap == 0:
        return (start, end, 0.0)
    u = [(e - s) / gap for s, e in zip(start, end)]
    a = tuple(s + c * ORBIT_CLEARANCE * start_r for s, c in zip(start, u))
    b = tuple(e - c * ORBIT_CLEARANCE * end_r for e, c in zip(end, u))
    return (a, b, math.dist(a, b))


def body_at(g, pos):
    """The sun or planet a route's target point names."""
    best, near = 0, None
    for oid, p in g.suns.items():
        d = math.dist(p, pos)
        if near is None or d < near:
            best, near = oid, d
    for oid, pl in g.planets.items():
        d = math.dist(pl.pos, pos)
        if d < near:
            best, near = oid, d
    return best


def body_pos(g, oid):
    return g.suns[oid] if oid in g.suns else g.planets[oid].pos


def next_system(g, civ, here):
    """The nearest system the civ has not entered."""
    seen = civ.explored if civ else set()
    origin = body_pos(g, here)
    left = [(math.dist(origin, p), oid) for oid, p in g.suns.items()
            if oid != here and oid not in seen]
    return min(left)[1] if left else None


def ships(g):
    for s in g.ships.values():
        o = s.order
        if o is None or not o.legs:
            continue
        o.progress += speed(g, s)
        total = sum(length for *_e, length in o.legs)
        if o.progress < total:
            s.pos = along(o.legs, o.progress)
            s.orbit = 0
            o.ref = 0
            continue
        arrive(g, s)


def arrive(g, s):
    o = s.order
    here = body_at(g, o.target)
    s.pos = body_pos(g, here)
    s.orbit = here
    if o.kind == SCOUT:
        civ = g.civs.get(s.owner)
        if civ is not None and here in g.suns:
            civ.explored.add(here)
        nxt = next_system(g, civ, here)
        if nxt is None:
            s.order = None
            return
        o.target = g.suns[nxt]
        o.legs = [leg(s.pos, g.radius.get(here, 0), o.target, g.radius.get(nxt, 0))]
        o.progress = 0.0
        o.ref = here
    elif o.kind == MOVE:
        # Assumed: the client clears a finished move; no capture has one.
        s.order = None
    # A colonise order waits at the target for the colonisation step, which
    # no capture has shown yet.
