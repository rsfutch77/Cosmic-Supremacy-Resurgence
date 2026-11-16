"""
What each civ knows: the systems it has looked at, as they were when it last
looked, and the ships its scanners reach.

Measured on the Single Player chain (EXSY, November 2026): a civ's row for a
system is refreshed every turn it owns a planet there, and every turn any
planet of the system is within the scanner range of one of its ships; the
scout's 30-unit scanner refreshed system 135 with a planet 19.3 away and not
with the nearest 32.6 away. The row keeps the turn and each planet's owner
and population.

From the manual, not checked against a capture: a cloaking device cuts the
range at which a ship is seen to 40%, less the owner's cloaking trait; a
system scan shows a system for 8 turns, and ships within twice the civ's best
scanner range of its sun. Ships in a system where the civ owns a planet are
taken to be seen; the client's planet scanning is not measured.
"""
import math

from . import tables as tb
from . import traits

CLOAKED_SEEN_AT = 0.4           # share of scanner range a cloaked ship is seen at
CLOAKING_DEVICE = 3             # module id
SYSTEM_SCAN_TURNS = 8
SYSTEM_SCAN_REACH = 2.0         # times the best scanner range, around the sun


def scanner_range(g, ship):
    d = g.designs.get(ship.design)
    if d is None:
        return 0.0
    base = max((tb.SCANNERS[i].stat for i in d.parts.get('scanners') or ()), default=0)
    return traits.pct(g.civs.get(ship.owner), 13, base)


def best_range(g, civ):
    best = 0
    for d in g.designs.values():
        if d.owner == civ:
            best = max([best] + [tb.SCANNERS[i].stat for i in d.parts.get('scanners') or ()])
    return traits.pct(g.civs.get(civ), 13, best)


def eyes_of(g, civ_id):
    """[(position, scanner range)] of the civ's ships, one per position with
    the longest range there: ships stacked in one orbit see no further than
    the best of them."""
    best = {}
    for s in g.ships.values():
        if s.owner == civ_id:
            r = scanner_range(g, s)
            if r > best.get(s.pos, -1.0):
                best[s.pos] = r
    return list(best.items())


def cloaked(g, ship):
    d = g.designs.get(ship.design)
    return d is not None and CLOAKING_DEVICE in (d.parts.get('modules') or ())


def seen_at(g, ship, reach):
    """The range at which `reach` of scanning finds this ship."""
    if not cloaked(g, ship):
        return reach
    bonus = traits.value(g.civs.get(ship.owner), 12)
    return reach * max(0.0, CLOAKED_SEEN_AT - bonus / 100)


def look(g, civ_id, system):
    civ = g.civs[civ_id]
    civ.explored.add(system)
    civ.seen[system] = (g.turn, {oid: (g.planets[oid].owner, len(g.planets[oid].citizens))
                                 for oid in g.system_planets.get(system, [])})


def update(g):
    """Refresh every civ's rows for the systems it can see this turn, and run
    down its system scans. Runs after combat, as the turn's last word."""
    by_system = {}
    for oid, p in g.planets.items():
        by_system.setdefault(p.system, []).append(p)
    for civ_id, civ in g.civs.items():
        if civ.uid == 0 and civ_id != g.player:
            # Owner:4 = 0 is a civ the client does not simulate: its rows
            # freeze unless it is the civ the client plays as, which the
            # client still draws for. Its research freezes either way.
            continue
        systems = {p.system for p in g.planets.values() if p.owner == civ_id}
        eyes = eyes_of(g, civ_id)
        for sid, planets in by_system.items():
            if sid in systems:
                continue
            if any(math.dist(pos, p.pos) <= r for pos, r in eyes for p in planets):
                systems.add(sid)
        for sid, left in list(civ.watching.items()):
            systems.add(sid)
            if left <= 1:
                del civ.watching[sid]
            else:
                civ.watching[sid] = left - 1
        for sid in systems:
            look(g, civ_id, sid)


def visible_ships(g, civ_id):
    """Other civs' ships this civ sees now."""
    eyes = eyes_of(g, civ_id)
    civ = g.civs[civ_id]
    reach = SYSTEM_SCAN_REACH * best_range(g, civ_id)
    watched = [g.suns[sid] for sid in civ.watching if sid in g.suns]
    held = {p.system for p in g.planets.values() if p.owner == civ_id}
    out = []
    for s in g.ships.values():
        if s.owner == civ_id:
            continue
        orbit = g.planets.get(s.orbit)
        if orbit is not None and orbit.system in held or s.orbit in held:
            out.append(s)
            continue
        # A ship's reach to be seen at scales every scanner's range alike.
        share = seen_at(g, s, 1.0)
        if any(math.dist(s.pos, pos) <= r * share for pos, r in eyes):
            out.append(s)
        elif any(math.dist(s.pos, sun) <= reach * share for sun in watched):
            out.append(s)
    return out
