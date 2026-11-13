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
mid-space battles from attack orders and collateral damage are not modelled,
no captured battle had a ranked crew, and conquest follows the manual with no
capture to check it: a planet whose defence is bombed to nothing loses its
turrets and shield generators, and with no defending ships left the soldiers
aboard ships with a Troop Bay land and fight the stationed soldiers.
"""
from . import economy
from . import traits
from . import movement
from . import renown
from . import state as st
from . import tables as tb

SUB_ROUNDS = 100
GROUND_ROUNDS = 5
BUNKER, FORTRESS = 15, 17
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
        civ = g.civs.get(ship.owner)
        self.ship, self.heavy = ship, d.heavy
        self.max = hitpoints(civ, d)
        self.hp = self.max * ship.condition
        light, heavy, bomb = d.firepower
        need = tb.CHASSIS[d.chassis].crew[0] if d.chassis is not None else 0
        crewed = d.chassis is not None and len(ship.crew) >= need
        boost = (1 + RANK_BONUS * ship_rank(ship, need)) * fire_bonus(civ, d)
        self.fire = (light * boost, heavy * boost, bomb * boost) if crewed else (0, 0, 0)
        self.armed = crewed and (light or heavy)


def hitpoints(civ, d):
    """Units raised by trait 11, shields by the design's strength plus trait
    10's points, then trait 17. The order the client applies them in is not
    checked; no captured battle had any of these traits."""
    units = traits.pct(civ, 11, d.units)
    return traits.pct(civ, 17, units * (100 + d.shield + traits.value(civ, 10)) / 100)


def fire_bonus(civ, d):
    """Trait 7 for every ship and trait 19 on top for a heavy one, as percents."""
    pct = traits.value(civ, 7) + (traits.value(civ, 19) if d.heavy else 0)
    return 1 + pct / 100


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
        civ = g.civs.get(p.owner)
        self.planet, self.fire = p, (traits.pct(civ, 8, light), traits.pct(civ, 8, heavy))
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
        bio_bomb(g, p, [s for s in foes if s.oid in g.ships])


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
    attacker = foes[0].owner
    killed_home = sum(g.designs[u.ship.design].units for u in home if u.hp <= 1e-9)
    killed_away = sum(g.designs[u.ship.design].units for u in away if u.hp <= 1e-9)
    if planet.max and planet.hp <= 1e-9:
        killed_home += defence_units(p)
    renown.battle(g, attacker, p.owner, killed_away, killed_home,
                  power(g, p.owner), power(g, attacker))
    renown.experience(home, away, sum(Planet(g, p).fire))
    for u in home + away:
        if u.hp <= 1e-9:
            movement.remove_ship(g, u.ship)
        else:
            u.ship.condition = u.hp / u.max
    if planet.max and planet.hp <= 1e-9:
        for kind in (LIGHT_TURRET, HEAVY_TURRET, SHIELD_GENERATOR):
            p.facilities.pop(kind, None)
    defended = planet.hp > 1e-9 or alive(home)
    bombing = any(u.ship.order and u.ship.order.kind == movement.BIO_BOMB and u.ship.order.runs
                  for u in alive(away))
    if not defended and alive(away) and not bombing:
        invade(g, p, [u.ship for u in alive(away)])


BIO_BOMBS = 4                   # module id
BIO_KILLS = 20                  # soldiers or citizens a bomb set kills a turn
BIO_BLIGHT = 5                  # space a set makes uninhabitable
BIO_REPUTATION = 0.1            # lost per set dropped
BIO_SHELTER = (2, 3)            # 1 in 2 in a bunker, 1 in 3 in a fortress is killed


def bio_bomb(g, p, ships):
    """The manual's bio-bombing, after conventional bombing: each set kills 20,
    soldiers first, those sheltered in bunkers and fortresses less often; it
    blights 5 space, costs the bomber 0.1 reputation, and turns the victim's
    former people on the bomber's planets 1 loyalty point against it. No
    capture has shown one."""
    for s in ships:
        o = s.order
        if o is None or o.kind != movement.BIO_BOMB or not o.runs or s.orbit != p.oid:
            continue
        d = g.designs.get(s.design)
        sets = min(o.bombs or 1, (d.parts.get('modules') or ()).count(BIO_BOMBS)) if d else 0
        if not sets:
            continue
        kills = BIO_KILLS * sets
        bunkered = 4 * p.facilities.get(BUNKER, 0)
        fortified = 8 * p.facilities.get(FORTRESS, 0)
        xp = sorted(p.military_xp or [0.0] * len(p.military), reverse=True)
        sheltered = min(len(xp), fortified + bunkered)
        open_ = len(xp) - sheltered
        dead = min(open_, kills)
        kills -= dead
        if kills:
            dead_f = min(min(fortified, sheltered), kills // BIO_SHELTER[1])
            dead_b = min(sheltered - min(fortified, sheltered), (kills - dead_f) // BIO_SHELTER[0])
            kills -= dead_f * BIO_SHELTER[1] + dead_b * BIO_SHELTER[0]
            dead += dead_f + dead_b
        keep = len(xp) - dead
        p.military, p.military_xp = p.military[:keep], xp[:keep]
        if kills > 0:
            p.citizens = p.citizens[min(len(p.citizens), kills):]
        p.blight += BIO_BLIGHT * sets
        g.civs[s.owner].reputation -= BIO_REPUTATION * sets
        for q in g.planets.values():
            if q.owner == s.owner and q.prior_owner == p.owner:
                q.loyalty = max(0.0, q.loyalty - sets)
        o.runs -= 1
        if not o.runs:
            s.order = None


def defence_units(p):
    return (sum(p.facilities.get(k, 0) * u for k, (u, _l, _h) in TURRETS.items())
            + p.facilities.get(SHIELD_GENERATOR, 0) * SHIELD_UNITS)


def power(g, civ):
    """The manual's physical power: a civ's ship units and defence units."""
    return (sum(g.designs[s.design].units for s in g.ships.values()
                if s.owner == civ and s.design in g.designs)
            + sum(defence_units(p) for p in g.planets.values() if p.owner == civ))


def troops_aboard(g, ship):
    """Soldiers a ship can land: only a ship with a Troop Bay lands any, and it
    lands everyone beyond the crew it needs to fly, the most experienced staying
    aboard. Pilots of other ships never land."""
    d = g.designs[ship.design]
    if economy.TROOP_BAY not in (d.parts.get('modules') or ()):
        return []
    need = tb.CHASSIS[d.chassis].crew[0] if d.chassis is not None else 0
    xp = sorted(ship.crew_xp or [0.0] * len(ship.crew), reverse=True)
    return xp[need:]


def ground_side(xps, bunkers, fortresses):
    """[(health, fire)] per soldier: 1 and 2, +10% and +20% a rank, and the
    best-ranked get +100% from each bunker's 4 places and +200% from each
    fortress's 8."""
    out = []
    for i, x in enumerate(sorted(xps, reverse=True)):
        r = economy.rank(x)
        boost = 1.0
        if i < 8 * fortresses:
            boost = 3.0
        elif i < 8 * fortresses + 4 * bunkers:
            boost = 2.0
        out.append([(1 + 0.1 * r) * boost, (2 + 0.4 * r) * boost])
    return out


def strike(shooters, targets):
    """A fifth of each side's fire, weakest target first."""
    fire = sum(f for h, f in shooters if h > 0) / GROUND_ROUNDS
    for t in sorted((t for t in targets if t[0] > 0), key=lambda t: t[0]):
        if fire <= 0:
            break
        dealt = min(t[0], fire)
        t[0] -= dealt
        fire -= dealt


def invade(g, p, ships):
    """Ground combat once the planet's defence and ships are gone, the
    manual's rules; no capture has shown one. Defenders shoot first in each
    of five sub-rounds. Winning troops stay as the planet's soldiers."""
    landing = [(s, x) for s in ships for x in troops_aboard(g, s)]
    if not landing:
        return
    attack = ground_side([x for _s, x in landing], 0, 0)
    defend = ground_side(p.military_xp or [0.0] * len(p.military),
                         p.facilities.get(BUNKER, 0), p.facilities.get(FORTRESS, 0))
    for _round in range(GROUND_ROUNDS):
        strike(defend, attack)
        strike(attack, defend)
    for s in {id(s): s for s, _x in landing}.values():
        d = g.designs[s.design]
        need = tb.CHASSIS[d.chassis].crew[0] if d.chassis is not None else 0
        keep = sorted(s.crew_xp or [0.0] * len(s.crew), reverse=True)[:need]
        s.crew, s.crew_xp = s.crew[:len(keep)], keep
    survivors = [x for (_s, x), (h, _f) in zip(landing, attack) if h > 0]
    held = [x for x, (h, _f) in zip(sorted(p.military_xp or [0.0] * len(p.military),
                                           reverse=True), defend) if h > 0]
    if held or not survivors:
        p.military, p.military_xp = [(economy.MILITARY, p.owner)] * len(held), held
        return
    owner = ships[0].owner
    economy.conquered(g, p, owner)
    p.owner = owner
    p.citizens = [(job, owner) for job, _w in p.citizens]
    p.military = [(economy.MILITARY, owner)] * len(survivors)
    p.military_xp = survivors
    p.production = st.Production('WLTH')
    p.recruit = 0


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
        s.condition = min(1.0, s.condition + traits.pct(g.civs.get(s.owner), 22, REPAIR[d.heavy]))
