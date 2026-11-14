"""
Scans: built at a Central Defense Agency, kept in stock, spent for a report.

From the manual's scanning screen, with no capture to check them against:
costs, a planetary scan's view of facilities, production, jobs and soldiers
(not ships in orbit), a technology scan's research, a military scan's ship
statistics, a route scan's destination and ETA, a fleet scan's ship count,
and a system scan's 8 turns of sight. A scan is spotted half the time, and
half of those the target learns who sent it, never with anonymous scanning
(trait 45, Doctrine: Intelligence).

Which research unlocks which scan id is the technology table's (kind 9
grants). Which id is which scan, and its cost, is the client's: its factory
(0x54E030) builds each id's class, and its descriptor table (0x80AB60) holds
each id's cost. The leader-name scan reveals a civ's player name and is not
modelled.
"""
import math

from . import fog
from . import movement as mv
from . import traits

CDA = 8                                 # Central Defense Agency, facility id
SCANS = {                               # id: (name, production cost)
    2: ('route', 3500), 3: ('fleet', 5000), 0: ('planetary', 9000),
    5: ('technology', 12000), 4: ('military', 16000), 1: ('system', 20000),
    6: ('energy grid', 10000), 7: ('leader name', 20000),
}
SCAN_GRANT = 9                          # research grant kind
SPOTTED, NAMED = 50, 50                 # percent


def unlocked(civ):
    return {oid for t in civ.completed or () for k, oid, _v in traits.GRANTS.get(t, ())
            if k == SCAN_GRANT}


def cost(civ, kind):
    return round(traits.pct(civ, 25, SCANS[kind][1]))


def chance(g, *key):
    """A repeatable draw in 0..99 for this turn and key."""
    return hash((g.turn,) + key) % 100


def use(g, civ_id, kind, target):
    """Spend one scan of `kind` on a ship, planet or sun; (ok, reason)."""
    civ = g.civs[civ_id]
    if civ.scans.get(kind, 0) < 1:
        return False, 'no scan of that kind in stock'
    name = SCANS[kind][0]
    report = {'turn': g.turn, 'kind': name, 'target': target}
    ship, planet = g.ships.get(target), g.planets.get(target)
    if name in ('route', 'fleet', 'military'):
        if ship is None or ship.owner == civ_id:
            return False, 'not another civ\'s ship'
        if name == 'route':
            o = ship.order
            left = sum(l for *_e, l in o.legs) - o.progress if o and o.legs else 0
            speed = mv.speed(g, ship)
            report.update(destination=o and o.target, eta=math.ceil(left / speed) if speed else None)
        elif name == 'fleet':
            mates = [s for s in g.ships.values() if s.fleet and s.fleet == ship.fleet] or [ship]
            report.update(ships=len(mates), designs=sorted(s.design for s in mates))
        else:
            d = g.designs[ship.design]
            report.update(design=ship.design, speed=mv.speed(g, ship), firepower=d.firepower,
                          units=d.units, hitpoints=d.hitpoints, condition=ship.condition,
                          crew=len(ship.crew))
        owner = ship.owner
    elif name in ('planetary', 'technology', 'energy grid'):
        if planet is None or not planet.owner or planet.owner == civ_id:
            return False, 'not another civ\'s planet'
        owner = planet.owner
        if name == 'planetary':
            report.update(owner=owner, facilities=dict(planet.facilities), jobs=planet.jobs(),
                          soldiers=len(planet.military), production=planet.production and
                          (planet.production.kind, planet.production.item))
            turn, rows = civ.seen.get(planet.system, (g.turn, {}))
            rows = dict(rows)
            rows[planet.oid] = (owner, len(planet.citizens))
            civ.seen[planet.system] = (turn, rows)
        elif name == 'technology':
            other = g.civs[owner]
            report.update(completed=list(other.completed or ()), topic=other.topic)
        else:
            report.update(planets=[p.oid for p in g.planets.values() if p.owner == owner
                                   and (p.facilities.get(18) or p.facilities.get(19))])
    elif name == 'system':
        if target not in g.suns:
            return False, 'not a sun'
        civ.watching[target] = fog.SYSTEM_SCAN_TURNS
        fog.look(g, civ_id, target)
        owner = None
    else:
        return False, f'the {name} scan is not modelled'
    civ.scans[kind] -= 1
    civ.intel.append(report)
    if owner and chance(g, civ_id, kind, target) < SPOTTED:
        named = (not traits.value(civ, traits.ANONYMOUS_SCANNING)
                 and chance(g, target, kind, civ_id) < NAMED)
        who = civ_id if named else None
        g.civs[owner].news.append((g.turn, ('scanned', name, target, who)))
    return True, ''
