"""
The strategist's goals and the scripted roles that carry them out
(docs/RL_Training_Plan.md, sections 3 and 4).

    goals = Goals(posture='expand', line='economy')
    player = Player(civ, goals)
    orders = player.act(g)

A strategist sets `Goals`; `Player.act` turns them into this turn's orders
through five roles: the steward (jobs, recruitment, facilities), the settler
(colony ships and a scout), the scientist (research in the chosen line), the
shipwright (designs from researched parts) and the admiral (warships,
troopers and the mission).

The roles read only what the civ can see: its own planets and ships, the
systems it has looked at as they were then (`Civ.seen`), and contacts in
scanner range (`fog.visible_ships`).
"""
import math
from dataclasses import dataclass

from . import combat
from . import economy as ec
from . import fog
from . import movement as mv
from . import orders as od
from . import research as rs
from . import state as st
from . import tables as tb
from . import traits

POSTURES = ('expand', 'grow', 'arm', 'defend', 'balanced')
LINES = ('economy', 'weapons', 'hulls', 'defence', 'intel')

FARM, FACTORY, SHIPYARD, AUTO_FACTORY, UNIVERSITY, SCIENCE_LAB = 0, 1, 2, 3, 4, 5
CAMP, LIGHT_TURRET, HEAVY_TURRET, SHIELD_GEN = 6, 9, 10, 11
MINE, BUNKER, BANKING, FORTRESS = 13, 15, 16, 17

# What a planet builds when it has nothing better to do: (facility, how many).
BUILDS = {
    'expand': ((FARM, 2), (FACTORY, 2), (SHIPYARD, 1), (FARM, 4), (FACTORY, 4)),
    'grow': ((FACTORY, 3), (FARM, 2), (UNIVERSITY, 2), (MINE, 1), (AUTO_FACTORY, 2),
             (SCIENCE_LAB, 2), (BANKING, 1), (FACTORY, 6), (UNIVERSITY, 4), (FARM, 4)),
    'arm': ((FACTORY, 4), (SHIPYARD, 1), (MINE, 2), (AUTO_FACTORY, 3), (CAMP, 1),
            (FARM, 2), (FACTORY, 6)),
    'defend': ((LIGHT_TURRET, 4), (BUNKER, 2), (CAMP, 1), (FACTORY, 2), (HEAVY_TURRET, 2),
               (SHIELD_GEN, 1), (FORTRESS, 2), (LIGHT_TURRET, 8)),
    'balanced': ((FACTORY, 2), (FARM, 1), (UNIVERSITY, 1), (MINE, 1), (SHIPYARD, 1),
                 (FACTORY, 4), (UNIVERSITY, 2), (LIGHT_TURRET, 2), (FARM, 3)),
}
SHIPYARD_POP = 8                # citizens before a colony gets a shipyard
PLANETS_PER_SHIPYARD = 4        # one shipyard more for every this many planets
FOOD_MARGIN = {'expand': 0.8, 'grow': 1.0, 'arm': 0.8, 'defend': 0.8, 'balanced': 0.6}  # births and soldiers come from surplus
SCIENCE_SHARE = {'expand': 0.25, 'grow': 0.5, 'arm': 0.15, 'defend': 0.15, 'balanced': 0.35}
RECRUIT = {'expand': 20, 'grow': 10, 'arm': 99, 'defend': 99, 'balanced': 15}  # capped per planet
MINERS = {'arm': 2}             # per planet with ore, from six citizens; 1 otherwise
LOW_CREDITS = 200               # below this a tenth of the free citizens bank
RESERVE_CREDITS = 1000          # kept back from hurrying, for upkeep
COLONY_SHIPS = {'expand': 3, 'grow': 1, 'arm': 0, 'defend': 0, 'balanced': 1}  # in hand at once
WARSHIPS_PER_PLANET = {'expand': 0, 'grow': 0, 'arm': 99, 'defend': 1, 'balanced': 1}
TROOPERS = 6                    # kept for a mission; four sail with the wave, the rest follow
BOMBERS = 4                     # in the wave, to knock out the defence
WAVE_TROOPERS = 4
HELD_TROOPERS = 8               # kept once the orbit is held
GARRISON_UNTIL = 60             # loyalty below which a planet keeps its soldiers and gets more
TROOPS_ABOARD = 4               # soldiers past the flying crew before a trooper sails
LAUNCH = 4                      # crewed warships at home before a wave leaves
OUTNUMBER = 2.0                 # times the most ships seen at the target at once
WARSHIP_SPEED = 10.0            # thrust over units the shipwright aims for
SCOUT_SPEED = 35.0
SCOUTS = 2                      # while half the systems are unexplored; 1 after
SCOUT_SPREAD = 150              # distance a scout keeps from another's destination
BUILD_TURNS = 15                # the biggest chassis a planet makes in this many turns
AFFORD = 2                      # times a part's ship resources the civ must hold to use it


def _line_of(tech):
    """The research line a technology belongs to, from what it unlocks."""
    for kind, oid, _v in traits.GRANTS.get(tech, ()):
        if kind == traits.FACILITY:
            if oid in (FARM, FACTORY, AUTO_FACTORY, UNIVERSITY, SCIENCE_LAB, 12, MINE, 14,
                       BANKING):
                return 'economy'
            if oid in (LIGHT_TURRET, HEAVY_TURRET, SHIELD_GEN, BUNKER, FORTRESS):
                return 'defence'
            if oid == 8:
                return 'intel'
        elif kind in (3, 5):
            return 'weapons'
        elif kind in (1, 4):
            return 'hulls'
        elif kind == 10:
            return 'defence'
        elif kind in (6, 9):
            return 'intel'
    return None


TECH_LINES = {line: sorted(t for t in rs.TECHS if not rs.is_doctrine(t) and _line_of(t) == line)
              for line in LINES}


@dataclass
class Goals:
    posture: str = 'balanced'
    line: str = 'economy'
    war: int = None             # a rival to declare war on, once
    mission: int = None         # a seen enemy planet to take; None guards home


def kind_of(d):
    """What a design is for: colony, trooper, bomber, warship or scout."""
    modules = d.parts.get('modules') or ()
    if ec.COLONY_MODULE in modules:
        return 'colony'
    if ec.TROOP_BAY in modules:
        return 'trooper'
    light, heavy, planet = d.firepower
    if planet and not (light or heavy):
        return 'bomber'
    if light or heavy:
        return 'warship'
    return 'scout'


class Player:
    """One civ's scripted roles, steered by a strategist's goals."""

    def __init__(self, civ, goals=None):
        self.civ = civ
        self.goals = goals or Goals()
        self.claims = {}            # colony ship -> planet it is bound for
        self.launch = {}            # warship or trooper -> turn it leaves
        self.made = {}              # design kind -> parts it was last asked for
        self.sighted = {}           # planet -> most other civs' ships seen in its orbit at once
        self.home = None            # the rally planet, kept while it has a shipyard
        self.held = None            # the turn the mission's orbit was taken

    # -- what the civ can see --------------------------------------------

    def seen_planets(self, g):
        """{planet: (owner, population, turn seen)} for other civs' and
        unowned planets the civ has looked at."""
        out = {}
        for _sid, (turn, rows) in g.civs[self.civ].seen.items():
            for oid, (owner, pop) in rows.items():
                if owner != self.civ:
                    out[oid] = (owner, pop, turn)
        return out

    def at_war(self, g, other):
        return g.civs[self.civ].stances.get(other) == od.WAR

    # -- the turn --------------------------------------------------------

    def act(self, g):
        civ, gl = self.civ, self.goals
        c = g.civs[civ]
        mine = [p for p in g.planets.values() if p.owner == civ]
        ships = [s for s in g.ships.values() if s.owner == civ]
        if not mine:
            return []
        out = []
        out += self.scientist(g, c)
        designs = self.shipwright(g, c, mine, out)
        if gl.war is not None and gl.war in g.civs and gl.war != civ and not self.at_war(g, gl.war):
            out.append(('war', gl.war))
        groups = {k: [s for s in ships if g.designs.get(s.design) is not None
                      and kind_of(g.designs[s.design]) == k]
                  for k in ('colony', 'trooper', 'bomber', 'warship', 'scout')}
        for p in mine:
            out += self.steward_jobs(g, c, p)
        out += self.planet_builds(g, c, mine, designs, groups)
        out += self.hurry(g, c, mine)
        out += self.crew(g, mine, ships)
        out += self.settler(g, c, mine, groups)
        out += self.admiral(g, mine, groups, designs)
        return out

    # -- scientist -------------------------------------------------------

    def scientist(self, g, c):
        done = set(c.completed or ())
        if c.goal in TECH_LINES[self.goals.line] and c.goal not in done and c.topic is not None \
                and c.topic >= 0:
            return []
        todo = [t for t in TECH_LINES[self.goals.line] if t not in done
                and rs.next_topic(t, list(done)) >= 0]
        if not todo:
            todo = [t for line in LINES for t in TECH_LINES[line] if t not in done
                    and rs.next_topic(t, list(done)) >= 0]
        if not todo:
            return []
        goal = min(todo, key=lambda t: (rs.TECHS[t][1], t))
        if goal == c.goal and c.topic is not None and c.topic >= 0:
            return []
        return [('research', goal)]

    # -- shipwright ------------------------------------------------------

    def shipwright(self, g, c, mine, out):
        """{kind: design id} for the designs in use, asking for a new design
        when the researched parts make a better one."""
        have = od.unlocked_parts(g, self.civ)
        prod = max((ec.worker_yield(p, c) * p.jobs().get(ec.WORKER, 0) for p in mine), default=0)
        want = {}
        for kind in ('colony', 'scout', 'warship', 'bomber', 'trooper'):
            parts = self.parts_for(g, c, kind, have, prod)
            if parts is not None:
                want[kind] = parts
        own = [d for d in g.designs.values() if d.owner == self.civ and not d.inactive]
        found = {}
        for kind, parts in want.items():
            match = [d for d in own if _same(d.parts, parts)]
            if match:
                found[kind] = match[0].oid
                continue
            if self.made.get(kind) != parts:
                self.made[kind] = parts
                out.append(('design', f'{kind}{g.turn}', parts))
            older = [d for d in own if kind_of(d) == kind]
            if older:
                found[kind] = max(older, key=lambda d: d.oid).oid
        return found

    def parts_for(self, g, c, kind, have, prod):
        store = c.resources or [0] * 5

        def held(cat, i):
            need = tb.TABLES[cat][i].resources
            return all(s >= AFFORD * n for s, n in zip(store, need))

        have = {cat: {i for i in ids if held(cat, i)} for cat, ids in have.items()}
        have['chassis'] |= {0}

        def fits(parts):
            d = st.Design(0, self.civ, '', {cat: list(parts.get(cat) or ()) for cat in tb.CATEGORIES})
            return d.space_used <= od.hull_space(c, d), d

        def engines(parts, target):
            for best in sorted(have['engines'], key=lambda i: -tb.ENGINES[i].stat / tb.ENGINES[i].space):
                trial = dict(parts, engines=[best])
                while True:
                    ok, d = fits(trial)
                    if not ok:
                        break
                    if d.units and d.thrust / d.units >= target:
                        return trial
                    trial = dict(trial, engines=trial['engines'] + [best])
            return None

        def fill(parts, cat, pick):
            if pick is None:
                return parts
            while fits(dict(parts, **{cat: parts.get(cat, []) + [pick]}))[0]:
                parts = dict(parts, **{cat: parts.get(cat, []) + [pick]})
            return parts

        scanner = [min(have['scanners'])] if have['scanners'] else []
        if kind == 'colony':
            if ec.COLONY_MODULE not in have['modules']:
                return None
            return engines({'chassis': [0], 'scanners': scanner, 'modules': [ec.COLONY_MODULE]}, 12.0)
        if kind == 'scout':
            eyes = [max(have['scanners'], key=lambda i: tb.SCANNERS[i].stat)] if have['scanners'] else []
            return engines({'chassis': [0], 'scanners': eyes}, SCOUT_SPEED)
        for chassis in sorted(have['chassis'], key=lambda i: -tb.CHASSIS[i].space):
            if prod and tb.CHASSIS[chassis].cost > prod * BUILD_TURNS:
                continue
            base = {'chassis': [chassis], 'scanners': scanner}
            if kind == 'trooper':
                if ec.TROOP_BAY not in have['modules']:
                    return None
                parts = engines(dict(base, modules=[ec.TROOP_BAY]), 8.0)
                parts = parts and fill(parts, 'modules', ec.TROOP_BAY)
            else:
                planet_fire = kind == 'bomber'
                guns = [w for w in have['weapons'] if bool(tb.WEAPONS[w].fp[2]) == planet_fire]
                if not guns:
                    return None
                gun = max(guns, key=lambda w: sum(tb.WEAPONS[w].fp) / tb.WEAPONS[w].space)
                parts = engines(dict(base, weapons=[gun]), WARSHIP_SPEED)
                if parts and have['shields'] and kind == 'warship':
                    shield = max(have['shields'], key=lambda s: tb.SHIELDS[s].stat / tb.SHIELDS[s].space)
                    with_shield = dict(parts, shields=[shield])
                    if fits(with_shield)[0]:
                        parts = with_shield
                parts = parts and fill(parts, 'weapons', gun)
            if parts is None:
                continue
            d = fits(parts)[1]
            if all(have_r >= 2 * need for have_r, need in zip(store, d.resources)) or chassis == 0:
                return parts
        return None

    # -- steward ---------------------------------------------------------

    def steward_jobs(self, g, c, p):
        out = []
        posture = self.goals.posture
        pop = len(p.citizens)
        cap = od.RECRUIT_BASE + od.RECRUIT_CAMP * p.facilities.get(ec.MILITARY_CAMP, 0)
        rate = min(RECRUIT[posture], cap)
        if p.recruit != rate:
            out.append(('recruit', p.oid, rate))
        if not pop or not p.rates:
            return out
        eat = ec.FOOD_PER_CITIZEN + traits.value(c, 0)
        y = max(1, ec.farmer_yield(p, c))
        farmers = min(pop, math.ceil(pop * eat * (1 + FOOD_MARGIN[posture]) / y))
        free = pop - farmers
        miners = min(free, MINERS.get(posture, 1)) if p.ore_rate and pop >= 6 else 0
        free -= miners
        bankers = free // 10 if (c.credits or 0) < LOW_CREDITS else 0
        free -= bankers
        scientists = max(1, round(free * SCIENCE_SHARE[posture])) if p.rates[2] and free >= 2 else 0
        jobs = {ec.FARMER: farmers, ec.MINER: miners, ec.BANKER: bankers,
                ec.SCIENTIST: scientists, ec.WORKER: free - scientists}
        jobs = {j: n for j, n in jobs.items() if n}
        if jobs != p.jobs():
            out.append(('jobs', p.oid, jobs))
        return out

    def facility(self, g, c, p):
        unlocked = {oid for t in c.completed or ()
                    for k, oid, _v in traits.GRANTS.get(t, ()) if k == traits.FACILITY}
        free = od.free_space(g, p)
        mine = [q for q in g.planets.values() if q.owner == self.civ]
        yards = sum(1 for q in mine if q.facilities.get(SHIPYARD))
        for kind, n in BUILDS[self.goals.posture]:
            if kind == SHIPYARD and (len(p.citizens) < SHIPYARD_POP
                                     or yards >= 1 + len(mine) // PLANETS_PER_SHIPYARD):
                continue
            have = p.facilities.get(kind, 0)
            if have >= n or kind not in unlocked or free < tb.FACILITIES[kind].space:
                continue
            if have and kind in od.ONCE:
                continue
            return ('facility', p.oid, kind)
        return ('wealth', p.oid)

    # -- what each planet builds ------------------------------------------

    def planet_builds(self, g, c, mine, designs, groups):
        posture = self.goals.posture
        out = []
        store = list(c.resources or [0] * 5)
        building = {}
        for p in mine:
            prod = p.production
            if prod is not None and prod.kind == 'SHIP' and prod.item in g.designs:
                k = kind_of(g.designs[prod.item])
                building[k] = building.get(k, 0) + 1
        targets = len(self.colony_targets(g, mine))
        want = {
            'colony': min(COLONY_SHIPS[posture], targets),
            'scout': self.scouts_wanted(g),
            'warship': WARSHIPS_PER_PLANET[posture] * len(mine) if posture != 'arm' else 10 ** 6,
            'trooper': (HELD_TROOPERS if self.held is not None else TROOPERS)
                       if self.goals.mission is not None else 0,
            'bomber': BOMBERS if self.goals.mission is not None else 0,
        }
        if self.goals.mission is not None and posture not in ('arm',):
            want['warship'] = max(want['warship'], LAUNCH)
        for p in sorted(mine, key=lambda q: -len(q.citizens)):
            prod = p.production
            if prod is not None and prod.kind not in ('WLTH',) and prod.item is not None:
                continue
            choice = None
            if p.facilities.get(SHIPYARD):
                for k in ('scout', 'colony', 'bomber', 'trooper', 'warship'):
                    if k not in designs:
                        continue
                    if len(groups[k]) + building.get(k, 0) >= want[k]:
                        continue
                    need = g.designs[designs[k]].resources
                    if any(h < n for h, n in zip(store, need)):
                        continue
                    store = [h - n for h, n in zip(store, need)]
                    building[k] = building.get(k, 0) + 1
                    choice = ('ship', p.oid, designs[k])
                    break
            choice = choice or self.facility(g, c, p)
            if choice[0] == 'wealth' and prod is not None and prod.kind == 'WLTH':
                continue
            out.append(choice)
        return out

    def hurry(self, g, c, mine):
        """Spend credits above the reserve finishing half-done work, biggest
        first."""
        budget = (c.credits or 0) - RESERVE_CREDITS
        out = []
        work = []
        for p in mine:
            prod = p.production
            cost = od.cost_of(g, p)
            if cost is None or prod.hurried or prod.kind == 'WLTH':
                continue
            left = cost - prod.progress
            if 0 < left <= cost / 2:
                work.append((left, p.oid))
        for left, oid in sorted(work, reverse=True):
            price = od.HURRY_PER_POINT * left
            if price <= budget:
                budget -= price
                out.append(('hurry', oid))
        return out

    # -- crews -----------------------------------------------------------

    def crew(self, g, mine, ships):
        out = []
        soldiers = {p.oid: len(p.military) for p in mine}
        # On a mission troopers board first; otherwise ships that cannot fly do.
        first = 'trooper' if self.goals.mission is not None else None
        order = sorted(ships, key=lambda s: (g.designs.get(s.design) is None
                                             or kind_of(g.designs[s.design]) != first, s.oid))
        for s in order:
            d = g.designs.get(s.design)
            if d is None or s.order is not None or s.orbit not in soldiers:
                continue
            if g.planets[s.orbit].loyalty < GARRISON_UNTIL:
                continue        # its soldiers hold a planet that may rebel
            k = kind_of(d)
            if self.held is not None and k in ('warship', 'bomber'):
                continue        # the orbit is held: soldiers go to the troopers
            need = od.seats_of(d) if k == 'trooper' else combat.crew_needed(d, g.civs[self.civ])
            gap = need - len(s.crew)
            n = min(gap, soldiers[s.orbit])
            if n > 0:
                soldiers[s.orbit] -= n
                out.append(('crew', s.oid, n))
        return out

    # -- settler ---------------------------------------------------------

    def scouts_wanted(self, g):
        c = g.civs[self.civ]
        left = sum(1 for sid in g.suns if sid not in c.explored)
        return SCOUTS if left * 2 > len(g.suns) else 1 if left else 0

    def colony_targets(self, g, mine):
        seen = self.seen_planets(g)
        home = [p.pos for p in mine]
        out = []
        for oid, (owner, _pop, _turn) in seen.items():
            q = g.planets.get(oid)
            if owner or q is None or not q.rates:
                continue
            near = min(math.dist(q.pos, h) for h in home)
            out.append((sum(q.rates) / (1 + near / 50), oid))
        return [oid for _v, oid in sorted(out, reverse=True)]

    def settler(self, g, c, mine, groups):
        out = []
        live = {s.oid for s in groups['colony']}
        self.claims = {s: p for s, p in self.claims.items() if s in live}
        targets = [t for t in self.colony_targets(g, mine) if t not in self.claims.values()]
        for s in groups['colony']:
            if s.order is not None or s.oid in self.claims:
                continue
            if len(s.crew) < combat.crew_needed(g.designs[s.design], c):
                continue
            if targets:
                t = min(targets[:5], key=lambda q: math.dist(g.planets[q].pos, s.pos))
                targets.remove(t)
                self.claims[s.oid] = t
                out.append(('colonise', s.oid, t))
        bound = [s.order.target for s in groups['scout'] if s.order is not None]
        for s in groups['scout']:
            if s.order is not None or len(s.crew) < combat.crew_needed(g.designs[s.design], c):
                continue
            left = [(math.dist(pos, s.pos), sid) for sid, pos in g.suns.items()
                    if sid not in c.explored and sid != s.orbit
                    and all(math.dist(pos, b) > SCOUT_SPREAD for b in bound)]
            if left:
                sid = min(left)[1]
                bound.append(g.suns[sid])
                out.append(('move', s.oid, sid))
            elif any(sid not in c.explored for sid in g.suns):
                out.append(('scout', s.oid))
        return out

    # -- admiral ---------------------------------------------------------

    def admiral(self, g, mine, groups, designs):
        out = []
        kept = g.planets.get(self.home)
        if kept is None or kept.owner != self.civ or not kept.facilities.get(SHIPYARD):
            self.home = rally(mine).oid
        home = g.planets[self.home]
        civ = g.civs[self.civ]
        here = {}
        for s in fog.visible_ships(g, self.civ):
            if s.orbit in g.planets:
                here[s.orbit] = here.get(s.orbit, 0) + 1
        for oid, n in here.items():
            self.sighted[oid] = max(n, self.sighted.get(oid, 0))
        fleet = groups['warship'] + groups['bomber']
        ready = [s for s in fleet if s.order is None
                 and len(s.crew) >= combat.crew_needed(g.designs[s.design], civ)]
        resisting = {p.oid: p for p in mine if p.citizens and p.loyalty < GARRISON_UNTIL}
        spare = {p.oid: len(p.military) for p in mine if p.oid not in resisting}
        for s in groups['trooper']:
            # A conquered planet that may rebel takes every full trooper's
            # soldiers first. Full troopers gather at the rally; the rest fetch
            # soldiers from the planet that has most, since soldiers board
            # only where they stand.
            d = g.designs[s.design]
            if s.order is not None or s.oid in self.launch or len(s.crew) < combat.crew_needed(d, civ):
                continue
            aboard = len(s.crew) - combat.crew_needed(d, civ)
            if s.orbit in resisting:
                if aboard > 0:
                    out.append(('crew', s.oid, -aboard))
                continue
            if resisting and aboard >= TROOPS_ABOARD:
                out.append(('move', s.oid, min(resisting.values(), key=lambda p: p.loyalty).oid))
                continue
            full = len(s.crew) >= od.seats_of(d)
            if full and s.orbit != home.oid:
                out.append(('move', s.oid, home.oid))
            elif not full and not spare.get(s.orbit):
                best = max(spare, key=spare.get)
                if spare[best]:
                    spare[best] -= od.seats_of(d) - len(s.crew)
                    out.append(('move', s.oid, best))
        target = self.mission_target(g)
        if target is None:
            for s in ready:
                if s.orbit != home.oid:
                    out.append(('move', s.oid, home.oid))
            return out
        at_home = [s for s in ready if s.orbit == home.oid and s.oid not in self.launch]
        for s in ready:
            if s.orbit not in (home.oid, target) and s.oid not in self.launch:
                out.append(('move', s.oid, home.oid))
        troops = [s for s in groups['trooper'] if s.order is None and s.orbit == home.oid
                  and len(s.crew) >= combat.crew_needed(g.designs[s.design], civ) + TROOPS_ABOARD]
        # The orbit is held once armed ships sit there with no other civ's
        # ship seen beside them; their fight each turn keeps the defence down.
        holding = any(s.orbit == target and s.order is None for s in fleet) and not here.get(target)
        if not holding:
            self.held = None
        elif self.held is None:
            self.held = g.turn
        if holding:
            # Reinforce the orbit with everything ready, troops included.
            for s in at_home + (troops if len(troops) >= WAVE_TROOPERS else []):
                out.append(('conquer', s.oid, target))
            return out
        # One wave: warships to clear the orbit, bombers to knock out the
        # defence and troopers to land, timed to arrive on the same turn.
        bombers = [s for s in at_home if s in groups['bomber']]
        need_bombers = BOMBERS if 'bomber' in designs else 0
        need_troops = WAVE_TROOPERS if 'trooper' in designs and self.seen_planets(g)[target][1] else 0
        enough = max(LAUNCH, math.ceil(OUTNUMBER * self.sighted.get(target, 0)))
        sailing = any(s.order is not None and s.order.kind == mv.CONQUER
                      for s in fleet + groups['trooper']) or any(
            s.oid in self.launch for s in fleet + groups['trooper'])
        if not sailing and len(at_home) - len(bombers) >= enough and len(bombers) >= need_bombers                 and len(troops) >= need_troops:
            wave = at_home + troops
            pos = g.planets[target].pos
            eta = {s.oid: math.dist(s.pos, pos) / max(0.1, mv.speed(g, s)) for s in wave}
            longest = max(eta.values())
            for s in wave:
                self.launch[s.oid] = g.turn + int(longest - eta[s.oid])
        for s in groups['warship'] + groups['bomber'] + groups['trooper']:
            if self.launch.get(s.oid, 10 ** 9) <= g.turn and s.order is None and s.orbit != target:
                out.append(('conquer', s.oid, target))
                del self.launch[s.oid]
        return out

    def mission_target(self, g):
        t = self.goals.mission
        seen = self.seen_planets(g)
        if t is None or t not in seen:
            return None
        owner = seen[t][0]
        if not owner or not self.at_war(g, owner):
            return None
        return t


def rally(mine):
    """Where warships gather and crew: the biggest planet with a shipyard,
    since soldiers board only at the planet they are on."""
    yards = [p for p in mine if p.facilities.get(SHIPYARD)] or mine
    return max(yards, key=lambda p: (len(p.citizens), -p.oid))


def _same(a, b):
    return all(sorted(a.get(cat) or ()) == sorted(b.get(cat) or ()) for cat in tb.CATEGORIES)


def policy(players):
    """{civ: orders} for a dict of Players, for Env.step."""
    def act(g):
        return {civ: pl.act(g) for civ, pl in players.items()}
    return act
