"""
The movement step of a tick: ships follow their routes, and scouts pick the
next system when they arrive.

Measured against engine ticks (docs/Fast_Sim_Plan.md, the movement section).
A route is straight legs whose ends stand off each body by a little more than
its radius; a ship's position is its progress along them.
"""
import math
import struct

from . import economy
from . import state as st
from . import tables as tb
from . import traits

ORBIT_CLEARANCE = 1.02          # radii between a body's centre and a route's end
MOVE, SCOUT, COLONISE = 1, 2, 3
ATTACK, CONQUER = 4, 5          # inject_order.py's kinds; both stay given on arrival
MIN_CREW_SHARE = 0.800000011920929   # of the crew range, at a full hull
CREW_MODULES = (0, 1)           # a Colony Module or Troop Bay needs 2 crew at least


def hull_space(civ, d):
    """The chassis's space raised by trait 16, and by trait 20 on a
    battleship, added as percents like the speed traits."""
    pct = traits.value(civ, traits.HULL_SPACE)
    if d.chassis == economy.BATTLESHIP:
        pct += traits.value(civ, traits.BATTLESHIP_HULL_SPACE)
    return economy.R(d.space * (100 + pct) / 100)


def crew_needed(d, civ=None):
    """Crew a ship needs to fly and fight, the engine's design getter
    (0x005490E0): the chassis's least crew plus a share of its range that
    grows with the space the parts fill, at most 80% of the range."""
    if d is None or d.chassis is None:
        return 0
    lo, hi = tb.CHASSIS[d.chassis].crew
    used = d.space - (hull_space(civ, d) - d.space_used)
    share = struct.unpack('<f', struct.pack('<f', used / d.space * MIN_CREW_SHARE))[0]
    need = lo + int((hi - lo) * min(1.0, share))
    if any(m in CREW_MODULES for m in d.parts.get('modules') or ()):
        need = max(need, 2)
    return need
BIO_BOMB = 6                    # inferred: inject_order lists 1 to 5 and 7


def speed(g, ship):
    """Thrust over hull units, as the client's design screen shows it."""
    d = g.designs.get(ship.design)
    if d is None or not d.units:
        return 0.0
    # Units take trait 11: Lightweight's -25 sped a Colony Ship up by a third.
    v = d.thrust / traits.pct(g.civs.get(ship.owner), 11, d.units)
    civ = g.civs.get(ship.owner)
    pct = traits.value(civ, traits.SHIP_SPEED)
    if not d.heavy:
        pct += traits.value(civ, traits.LIGHT_SHIP_SPEED)
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
    """A fleet's ships share one order and move at the slowest one's speed."""
    pace = {}
    for s in g.ships.values():
        if s.order is not None:
            v = speed(g, s)
            pace[id(s.order)] = min(pace.get(id(s.order), v), v)
    moved = set()
    for s in list(g.ships.values()):
        o = s.order
        if o is None or not o.legs:
            continue
        if not s.fleet and len(s.crew) < crew_needed(g.designs.get(s.design),
                                                     g.civs.get(s.owner)):
            # Undermanned Destroyers sat still and lost their orders.
            s.order = None
            continue
        if id(o) in moved:
            s.pos, s.orbit = along(o.legs, o.progress), 0
            continue
        moved.add(id(o))
        o.progress += pace[id(o)]
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
    s.arrived = g.turn
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
    elif o.kind == COLONISE:
        p = g.planets.get(here)
        if p is not None and not p.owner:
            colonise(g, s, p)


def colonise(g, s, p):
    """The ship lands its crew as farmers and is gone, in the tick it arrives."""
    p.owner = s.owner
    p.citizens = [(economy.FARMER, s.owner)] * len(s.crew)
    p.military = []
    p.military_xp = []
    p.food = (0, economy.food_capacity(len(p.citizens)))
    p.production = st.Production('WLTH')
    p.pop_history = [len(p.citizens)] + [0] * (economy.HISTORY_SLOTS - 1)
    p.history_len = 1
    p.founded = g.turn + 1
    p.recruit = 0
    remove_ship(g, s)


def remove_ship(g, s):
    g.ships.pop(s.oid, None)
    f = g.fleets.get(s.fleet)
    if f is not None and s.oid in f.ships:
        f.ships.remove(s.oid)
