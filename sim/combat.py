"""
The combat step of a tick: battles at hostile planets, then repairs.

The rules are the manual's combat pages (ship vs ship, planetary defense),
checked against the one battle in the oracle corpus: at turn 110 a fighter
and a bomber arrived at a planet with a light turret and two fighters. The
engine killed the fighter in its 8th of 100 sub-rounds, the fighter's 0.4 a
round took 3.2 off one defender, and the bomber, unarmed against ships, died
last; this module reproduces those numbers.

Approximate where the manual is vague and the corpus is silent: fire goes to
one target at a time, weakest first, where the engine spreads it at random;
mid-space battles from attack orders, collateral damage and ground combat are
not modelled yet, and no captured battle had a ranked crew.
"""
from . import economy
from . import movement
from . import tables as tb

SUB_ROUNDS = 100
OFF_CLASS = 0.5                 # light fire on heavy ships, and the reverse
RANK_BONUS = 0.10               # firepower per rank of the ship's crew
WAR = 1                         # state.STANCES
SHIPYARD = 2
LIGHT_TURRET, HEAVY_TURRET, SHIELD_GENERATOR = 9, 10, 11
TURRETS = {LIGHT_TURRET: (1000, 400, 100), HEAVY_TURRET: (2000, 200, 1000)}
SHIELD_UNITS = 3000
PD_TECHS = (27, 28, 29, 30, 31, 32, 33, 34)          # Planetary Defense Lvl 1 to 8
PD_BONUS = (0, 30, 60, 120, 240, 450, 750, 1200)     # percent, per level
PD_SHIELD = (40, 52, 64, 88, 136, 220, 340, 520)     # shield generator percent
REPAIR = {False: 0.15, True: 0.08}                   # per turn, light and heavy


class Unit:
    """A ship in a battle: its live hitpoints and what it fires each round."""

    def __init__(self, g, ship):
        d = g.designs[ship.design]
        self.ship, self.heavy = ship, d.heavy
        self.max = d.hitpoints
        self.hp = self.max * ship.condition
        light, heavy, bomb = d.firepower
        need = tb.CHASSIS[d.chassis].crew[0] if d.chassis is not None else 0
        crewed = d.chassis is not None and len(ship.crew) >= need
        boost = 1 + RANK_BONUS * ship_rank(ship, need)
        self.fire = (light * boost, heavy * boost, bomb * boost) if crewed else (0, 0, 0)
        self.armed = crewed and (light or heavy)


def ship_rank(ship, need):
    """The crew's total experience over the crew the ship needs, as a rank."""
    if not need or not ship.crew_xp:
        return 0
    return economy.rank(sum(ship.crew_xp) / need)


class Planet:
    """A planet's defence: turret fire and one pool of hitpoints."""

    def __init__(self, g, p):
        level = sum(1 for t in PD_TECHS if t in (g.civs[p.owner].completed or ()))
        bonus = 1 + PD_BONUS[max(level, 1) - 1] / 100
        units = light = heavy = 0
        for kind, (u, fl, fh) in TURRETS.items():
            n = p.facilities.get(kind, 0)
            units += n * u
            light += n * fl * bonus
            heavy += n * fh * bonus
        shields = p.facilities.get(SHIELD_GENERATOR, 0)
        units += shields * SHIELD_UNITS
        shield = PD_SHIELD[max(level, 1) - 1] if shields else 0
        self.planet, self.fire = p, (light, heavy)
        self.max = self.hp = units * (1 + shield / 100)


def at_war(g, a, b):
    civ = g.civs.get(a)
    return civ is not None and civ.stances.get(b) == WAR


def battles(g):
    # A ship damaged this tick is mended from the next one.
    repairs(g)
    for p in list(g.planets.values()):
        if not p.owner:
            continue
        here = [s for s in g.ships.values() if s.orbit == p.oid]
        foes = [s for s in here if s.owner != p.owner and at_war(g, s.owner, p.owner)]
        if not foes:
            continue
        friends = [s for s in here if s.owner == p.owner]
        fight(g, p, friends, foes)


def fight(g, p, friends, foes):
    home = [Unit(g, s) for s in friends if s.design in g.designs]
    away = [Unit(g, s) for s in foes if s.design in g.designs]
    planet = Planet(g, p)
    for _round in range(SUB_ROUNDS):
        if not alive(away):
            break
        hits_home = volley(home, planet.fire if planet.hp > 0 else (0, 0), away)
        hits_away = volley(away, (0, 0), home)
        bombs = sum(u.fire[2] for u in away if u.hp > 0) / SUB_ROUNDS
        for u, dmg in hits_home + hits_away:
            u.hp -= dmg
        if planet.max:
            planet.hp = max(0.0, planet.hp - bombs)
    for u in home + away:
        if u.hp <= 1e-9:
            movement.remove_ship(g, u.ship)
        else:
            u.ship.condition = u.hp / u.max


def alive(units):
    return [u for u in units if u.hp > 1e-9]


def volley(shooters, extra, targets):
    """One sub-round of a side's fire: [(target, damage)]."""
    light = sum(u.fire[0] for u in shooters if u.hp > 1e-9 and u.armed) + extra[0]
    heavy = sum(u.fire[1] for u in shooters if u.hp > 1e-9 and u.armed) + extra[1]
    light, heavy = light / SUB_ROUNDS, heavy / SUB_ROUNDS
    pool = [u for u in alive(targets) if u.armed] or alive(targets)
    left = {id(u): u.hp for u in pool}
    hits = []
    for fire, prefer_heavy in ((light, False), (heavy, True)):
        for full in (True, False):
            # Weakest first; among equals the later ship, as the one capture went.
            group = sorted((u for u in reversed(pool) if (u.heavy == prefer_heavy) == full
                            and left[id(u)] > 1e-9), key=lambda u: left[id(u)])
            scale = 1.0 if full else OFF_CLASS
            for u in group:
                if fire <= 1e-12:
                    break
                dealt = min(left[id(u)], fire * scale)
                left[id(u)] -= dealt
                fire -= dealt / scale
                hits.append((u, dealt))
    return hits


def repairs(g):
    """Damaged ships in orbit of their own civ's shipyard mend a share a turn."""
    for s in g.ships.values():
        if s.condition >= 1.0 or not s.orbit:
            continue
        p = g.planets.get(s.orbit)
        d = g.designs.get(s.design)
        if p is None or d is None or p.owner != s.owner or not p.facilities.get(SHIPYARD):
            continue
        s.condition = min(1.0, s.condition + REPAIR[d.heavy])
