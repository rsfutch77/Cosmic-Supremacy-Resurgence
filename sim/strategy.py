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
systems it has looked at as they were then (`Civ.seen`), contacts in scanner
range (`fog.visible_ships`), and the battle and landing reports in its news
(`combat._report`).
"""
import math
from dataclasses import dataclass

from . import combat
from . import economy as ec
from . import fog
from . import movement as mv
from . import orders as od
from . import research as rs
from . import scans as sc
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
TROOPERS = 6                    # kept for a mission at the least
MAX_TROOPERS = 30
LANDING_MARGIN = 1.5            # troops over the expected need before troopers land
GAMBLE = 0.8                    # share of the need worth landing when no more troops are coming
PLAN_AHEAD = 20                 # turns ahead the garrison is sized for when building troopers
PRIOR_RATE = 0.1                # soldiers a turn a garrison grows by with no scan, a colony's 90th percentile
MOST_FOOD_RATE = 40             # the best farmer rate a generated planet has
PLANETARY = 0                   # scans.SCANS id of the planetary scan
RESCAN = 10                     # turns before a held target is scanned again
BOMBERS = 4                     # in the wave, to knock out the defence
MAX_BOMBERS = 12
BOMB_TURNS = 3                  # turns the bombers should take to bring a reported defence down
FRESH = 2                       # turns a battle report stays current
WAVE_TROOPERS = 4
HELD_TROOPERS = 8               # kept once the orbit is held
GARRISON_UNTIL = 60             # loyalty below which a planet keeps its soldiers and gets more
TROOPS_ABOARD = 4               # soldiers past the flying crew before a trooper sails
LAUNCH = 4                      # crewed warships at home before a wave leaves
OUTNUMBER = 1.5                 # odds a wave leaves at, as a ship count over the target's defenders
UNKNOWN_DEFENCE = 'probe'       # a target of unknown defence: 'probe' it with a scout, 'assume' turrets, or 'blind'
ASSUMED_TURRETS = 2             # light turrets taken to guard a planet of unknown defence
PROBE_PATIENCE = 30             # turns a wave waits on a probe before it assumes the turrets
SURE = 10.0                     # the odds against a target nothing defends
FLEET_GAMBLE = 1.2              # odds worth sailing at when waiting will not reach OUTNUMBER soon
ODDS_WINDOW = 20                # turns of trend the odds are projected over
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
        self.last_seen = {}         # planet -> other civs' ships in its orbit when last in view
        self.odds = []              # (turn, odds) of a wave at the mission's target
        self.home = None            # the rally planet, kept while it has a shipyard
        self.held = None            # the turn the mission's orbit was taken
        self.read = 0               # news items already read
        self.owned_since = {}       # other civs' planets -> the turn first seen owned
        self.intel = {}             # planet -> what the newest battle report there said
        self.failed = {}            # planet -> (turn, soldiers a failed landing left it at least)
        self.probing = {}           # planet -> the turn its unknown defence was first wanted

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

    def reports(self, g):
        """Read new battle and landing reports into `intel` and `failed`."""
        news = g.civs[self.civ].news
        for turn, what in news[self.read:]:
            if what[0] == 'battle' and what[1]['planet'] is not None:
                r = what[1]
                mine = r['sides'].get(self.civ, {})
                foes = [v for c, v in r['sides'].items() if c != self.civ]
                fire = sum(v['firepower'][0] + v['firepower'][1] for v in foes)
                hp = sum(v['ship_hp'][0] + v['ship_hp'][1] for v in foes)
                old = self.intel.get(r['planet'], {})
                self.intel[r['planet']] = {
                    'turn': turn, 'planet_hp': r['planet_hp'], 'defence_left': r['defence_left'],
                    'lost': mine.get('destroyed', 0),
                    'enemy_ships': sum(v['ships'] for v in foes),
                    'enemy_now': fire * hp,
                    # The most the enemy has fought with there: fire, the turrets'
                    # included, ships' hitpoints and the defence's hitpoints.
                    'fire_most': max(old.get('fire_most', 0.0), fire),
                    'hp_most': max(old.get('hp_most', 0.0), hp),
                    'planet_most': max(old.get('planet_most', 0.0), r['planet_hp'])}
            elif what[0] == 'landing' and not what[1]['taken']:
                # The garrison was at least what beats that many troops, and
                # the fight left at least what that many would have left.
                planet = what[1]['planet']
                _est, _rate, bunkers, forts = self.garrison(g, planet, turn)
                floor = defenders_beating(what[1]['landed'], bunkers, forts)
                self.failed[planet] = (turn, ground_left(what[1]['landed'], floor, bunkers, forts))
        self.read = len(news)
        for oid, (owner, _pop, turn) in self.seen_planets(g).items():
            if owner:
                self.owned_since.setdefault(oid, turn)
            else:
                self.owned_since.pop(oid, None)

    def scans_of(self, g, planet):
        return [r for r in g.civs[self.civ].intel if r['kind'] == 'planetary' and r['target'] == planet]

    def garrison(self, g, planet, turn):
        """(soldiers expected on `turn`, growth a turn, bunkers, fortresses).
        From the newest planetary scan: its count, growing at the rate seen
        between the last two scans, or with one scan at the most its farmers
        could feed at full recruitment, or its average since first seen
        owned if slower. With no scan, PRIOR_RATE a turn since the start for
        a homeworld and since it was first seen owned for a colony. A failed
        landing since restarts the count from the soldiers it left."""
        scans = self.scans_of(g, planet)
        if scans:
            last = scans[-1]
            prev = next((r for r in reversed(scans[:-1]) if r['turn'] < last['turn']), None)
            if prev is not None:
                rate = max(0.0, (last['soldiers'] - prev['soldiers']) / (last['turn'] - prev['turn']))
            else:
                # One count: the average since it was first seen owned, no
                # faster than its farmers could feed.
                age = max(1, last['turn'] - self.owned_since.get(planet, 0))
                rate = min(most_recruits(last), last['soldiers'] / age)
            since, base, fac = last['turn'], last['soldiers'], last['facilities']
        else:
            founded = 0 if planet in g.homes.values() else self.owned_since.get(planet, 0)
            rate, since, base, fac = PRIOR_RATE, founded, 0, {}
        f_turn, left = self.failed.get(planet, (0, 0))
        if f_turn > since:
            since, base = f_turn, left
        return base + rate * max(0, turn - since), rate, fac.get(BUNKER, 0), fac.get(FORTRESS, 0)

    def scan_due(self, g, target, holding):
        """Scan the target when a scan is in stock: once, again RESCAN turns
        later to measure its growth, and every RESCAN turns while the orbit is
        held, to count the garrison before a landing."""
        if g.civs[self.civ].scans.get(PLANETARY, 0) < 1:
            return False
        scans = self.scans_of(g, target)
        return not scans or ((holding or len(scans) < 2) and scans[-1]['turn'] <= g.turn - RESCAN)

    def troops_needed(self, g, planet, turn):
        est, _rate, bunkers, forts = self.garrison(g, planet, turn)
        return troops_beating(math.ceil(est), bunkers, forts)

    def defence_down(self, g, planet):
        """A current report says the planet's defence is gone."""
        r = self.intel.get(planet)
        return r is not None and r['turn'] >= g.turn - FRESH and r['defence_left'] <= 1e-9

    def strength(self, g, ships):
        """Firepower times hitpoints, the measure a battle report gives."""
        civ = g.civs[self.civ]
        fire = hp = 0.0
        for s in ships:
            d = g.designs[s.design]
            fire += d.firepower[0] + d.firepower[1]
            hp += combat.hitpoints(civ, d) * s.condition
        return fire * hp

    def known(self, g, target):
        """A battle report or planetary scan of the target, of any age: a
        defence only grows by what the wave's own report will show."""
        return target in self.intel or bool(self.scans_of(g, target))

    def assumes(self, g, target):
        """Turrets are assumed at a target of unknown defence under 'assume',
        and under 'probe' once a probe has been wanted PROBE_PATIENCE turns."""
        if UNKNOWN_DEFENCE == 'assume':
            return True
        return UNKNOWN_DEFENCE == 'probe' and g.turn - self.probing.get(target, g.turn) >= PROBE_PATIENCE

    def per_ship(self, g):
        """(firepower, hitpoints) of the civ's average warship, which the
        ships seen in another civ's orbit are taken to match."""
        civ = g.civs[self.civ]
        ds = [g.designs[s.design] for s in g.ships.values() if s.owner == self.civ
              and s.design in g.designs and kind_of(g.designs[s.design]) == 'warship']
        ds = ds or [d for d in g.designs.values() if d.owner == self.civ and kind_of(d) == 'warship']
        if not ds:
            return 1.0, 1.0
        return (sum(d.firepower[0] + d.firepower[1] for d in ds) / len(ds),
                sum(combat.hitpoints(civ, d) for d in ds) / len(ds))

    def defence(self, g, target):
        """(firepower, ships' hitpoints, defence hitpoints) the target is
        expected to fight with: the most its battle reports showed, the
        turrets of the newest planetary scan, and the ships last seen in its
        orbit. An unknown defence is ASSUMED_TURRETS light turrets when
        `assumes` says so, else none."""
        r = self.intel.get(target)
        scans = self.scans_of(g, target)
        fac = scans[-1]['facilities'] if scans else {}
        if r is None and not scans and self.assumes(g, target):
            fac = {LIGHT_TURRET: ASSUMED_TURRETS}
        t_fire = sum(fac.get(k, 0) * (fl + fh) for k, (_u, fl, fh) in combat.TURRETS.items())
        t_hp = (sum(fac.get(k, 0) * u for k, (u, _fl, _fh) in combat.TURRETS.items())
                + fac.get(SHIELD_GEN, 0) * combat.SHIELD_UNITS)
        seen = self.last_seen.get(target, self.sighted.get(target, 0))
        fire, hp = self.per_ship(g)
        return (max(r['fire_most'] if r else 0.0, t_fire + seen * fire),
                max(r['hp_most'] if r else 0.0, seen * hp),
                max(r['planet_most'] if r else 0.0, t_hp))

    def odds_against(self, g, ships, target):
        """The ships' odds against the target's expected defence, as a ship
        count ratio: the square root of the turns the defence takes to kill
        them over the turns they take to clear it, guns on its ships and bombs
        on its defence. 0 when they cannot clear it, SURE when nothing fires."""
        civ = g.civs[self.civ]
        guns = bombs = hp = 0.0
        for s in ships:
            d = g.designs[s.design]
            guns += d.firepower[0] + d.firepower[1]
            bombs += d.firepower[2]
            hp += combat.hitpoints(civ, d) * s.condition
        fire, ship_hp, planet_hp = self.defence(g, target)
        need = []
        if ship_hp:
            need.append(ship_hp / guns if guns else math.inf)
        if planet_hp:
            need.append(planet_hp / bombs if bombs else math.inf)
        clear = max(need, default=0.0)
        if clear == math.inf or not hp:
            return 0.0
        if not fire or not clear:
            return SURE
        return min(SURE, math.sqrt(hp / fire / clear))

    def troopers_wanted(self, g, designs):
        """TROOPERS until the target's orbit is held, so soldiers crew the
        warships first; then enough to land LANDING_MARGIN times the troops
        the target is expected to need PLAN_AHEAD turns from now."""
        t = self.goals.mission
        if t is None or 'trooper' not in designs or t not in g.planets or self.held is None:
            return TROOPERS
        d = g.designs[designs['trooper']]
        seats = od.seats_of(d) - combat.crew_needed(d, g.civs[self.civ])
        need = LANDING_MARGIN * self.troops_needed(g, t, g.turn + PLAN_AHEAD)
        return max(TROOPERS, min(MAX_TROOPERS, math.ceil(need / max(1, seats)) + 1))

    def bombers_wanted(self, g, designs):
        """Enough bomb fire to take the expected defence down in BOMB_TURNS."""
        t = self.goals.mission
        per = g.designs[designs['bomber']].firepower[2] if 'bomber' in designs else 0
        planet_hp = self.defence(g, t)[2] if t in g.planets else 0
        if not per or not planet_hp:
            return BOMBERS
        return max(BOMBERS, min(MAX_BOMBERS, math.ceil(planet_hp / (per * BOMB_TURNS))))

    # -- the turn --------------------------------------------------------

    def act(self, g):
        civ, gl = self.civ, self.goals
        c = g.civs[civ]
        mine = [p for p in g.planets.values() if p.owner == civ]
        ships = [s for s in g.ships.values() if s.owner == civ]
        if not mine:
            return []
        out = []
        self.reports(g)
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

    def scan_builds(self, g, c, mine):
        """On a mission, once planetary scans are researched: a Central
        Defense Agency on the biggest planet without a shipyard, so the fleet
        is not held up, then a scan in stock."""
        if self.goals.mission is None or PLANETARY not in sc.unlocked(c):
            return []
        agencies = [p for p in mine if p.facilities.get(sc.CDA)]
        if not agencies:
            if any(p.production is not None and p.production.kind == 'FCLT'
                   and p.production.item == sc.CDA for p in mine):
                return []
            room = [p for p in mine if not p.facilities.get(SHIPYARD)
                    and od.free_space(g, p) >= tb.FACILITIES[sc.CDA].space]
            return [('facility', max(room, key=lambda p: len(p.citizens)).oid, sc.CDA)] if room else []
        if c.scans.get(PLANETARY, 0) or any(p.production is not None and p.production.kind == 'SCAN'
                                            for p in mine):
            return []
        return [('build_scan', agencies[0].oid, PLANETARY)]

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
            'trooper': self.troopers_wanted(g, designs) if self.goals.mission is not None else 0,
            'bomber': self.bombers_wanted(g, designs) if self.goals.mission is not None else 0,
        }
        if self.goals.mission is not None and posture not in ('arm',):
            want['warship'] = max(want['warship'], LAUNCH)
        claimed = self.scan_builds(g, c, mine)
        out += claimed
        busy = {a[1] for a in claimed}
        for p in sorted(mine, key=lambda q: -len(q.citizens)):
            prod = p.production
            if p.oid in busy:
                continue
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
        # On a mission a wave's troopers board first, then warships and bombers until the
        # target's orbit is held, then every trooper; otherwise ships in id
        # order.
        kind = {s.oid: kind_of(g.designs[s.design]) if s.design in g.designs else None for s in ships}
        wave = {s.oid for s in sorted((s for s in ships if kind[s.oid] == 'trooper'),
                                      key=lambda s: s.oid)[:WAVE_TROOPERS]}

        def rank(s):
            if self.goals.mission is None:
                return 0
            if s.oid in wave or (self.held is not None and kind[s.oid] == 'trooper'):
                return 0
            return 1 if kind[s.oid] in ('warship', 'bomber') else 2
        order = sorted(ships, key=lambda s: (rank(s), s.oid))
        for s in order:
            d = g.designs.get(s.design)
            if d is None or s.order is not None or s.orbit not in soldiers:
                continue
            if g.planets[s.orbit].loyal() < GARRISON_UNTIL:
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
        explore = SCOUTS if left * 2 > len(g.suns) else 1 if left else 0
        return explore + (1 if self.goals.mission is not None else 0)

    def picket_post(self, g):
        """Where a scout watches the mission's target from: the unowned
        planet or sun of its system nearest the target, so its scanners count
        the ships in orbit before a wave is sent."""
        t = self.goals.mission
        if t is None or t not in g.planets:
            return None
        p = g.planets[t]
        posts = [q for q in g.system_planets.get(p.system, []) if not g.planets[q].owner]
        return min(posts, key=lambda q: math.dist(g.planets[q].pos, p.pos), default=p.system)

    def probe_due(self, g):
        """Under 'probe', the mission's target has no known defence."""
        t = self.mission_target(g)
        if UNKNOWN_DEFENCE != 'probe' or t is None or self.known(g, t):
            return False
        self.probing.setdefault(t, g.turn)
        return True

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
        scouts = sorted(groups['scout'], key=lambda s: s.oid)
        post = self.picket_post(g)
        if self.probe_due(g):
            # The picket flies into the target's orbit; the battle report of
            # the fire it draws is the defence's measure.
            post = self.goals.mission
        if post is not None and scouts:
            picket = scouts.pop(0)
            if picket.order is None and picket.orbit != post \
                    and len(picket.crew) >= combat.crew_needed(g.designs[picket.design], c):
                out.append(('move', picket.oid, post))
        for s in scouts:
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
        for sid, (turn, rows) in civ.seen.items():
            if turn == g.turn:
                for oid in rows:
                    self.last_seen[oid] = here.get(oid, 0)
        fleet = groups['warship'] + groups['bomber']
        ready = [s for s in fleet if s.order is None
                 and len(s.crew) >= combat.crew_needed(g.designs[s.design], civ)]
        resisting = {p.oid: p for p in mine if p.citizens and p.loyal() < GARRISON_UNTIL}
        spare = {p.oid: len(p.military) for p in mine if p.oid not in resisting}
        target = self.mission_target(g)
        out_there = self.wave_out(g)
        # Full troopers wait in the target's system while the fleet works on
        # the defence, a few turns from landing.
        muster = g.planets[target].system if out_there else home.oid
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
                out.append(('move', s.oid, min(resisting.values(), key=lambda p: p.loyal()).oid))
                continue
            full = len(s.crew) >= od.seats_of(d)
            if full and s.orbit not in (muster, target):
                out.append(('move', s.oid, muster))
            elif not full and not spare.get(s.orbit):
                best = max(spare, key=spare.get)
                if spare[best]:
                    spare[best] -= od.seats_of(d) - len(s.crew)
                    out.append(('move', s.oid, best))
        if target is None:
            for s in ready:
                if s.orbit != home.oid:
                    out.append(('move', s.oid, home.oid))
            return out
        at_home = [s for s in ready if s.orbit == home.oid and s.oid not in self.launch]
        for s in ready:
            if s.orbit not in (home.oid, target) and s.oid not in self.launch:
                out.append(('move', s.oid, home.oid))
        loaded = [s for s in groups['trooper'] if s.order is None and s.oid not in self.launch
                  and s.orbit in (home.oid, muster)
                  and len(s.crew) >= combat.crew_needed(g.designs[s.design], civ) + TROOPS_ABOARD]
        mustered = [s for s in loaded if s.orbit == muster]
        # The orbit is held once armed ships sit there with none of the
        # owner's ships seen beside them; their fight each turn keeps the
        # defence down. A third civ's ships there do not stop a landing.
        in_orbit = [s for s in fleet if s.orbit == target and s.order is None]
        owner = self.seen_planets(g)[target][0]
        guards = sum(1 for s in fog.visible_ships(g, self.civ) if s.orbit == target and s.owner == owner)
        holding = bool(in_orbit) and not guards
        if not holding:
            self.held = None
        elif self.held is None:
            self.held = g.turn
        down = self.defence_down(g, target)
        if self.scan_due(g, target, holding):
            out.append(('scan', PLANETARY, target))
        if holding and down:
            # The defence is gone and warships keep the orbit. Troopers land
            # together once they carry LANDING_MARGIN times the troops the
            # garrison is expected to need when they arrive, or GAMBLE times
            # it when waiting for the troopers on the way would leave worse
            # odds, the garrison growing meanwhile.
            aboard = lambda ships: sum(len(s.crew) - combat.crew_needed(g.designs[s.design], civ)
                                       for s in ships)
            hop = max([math.ceil(math.dist(s.pos, g.planets[target].pos) / max(0.1, mv.speed(g, s)))
                       for s in mustered] or [0])
            need = self.troops_needed(g, target, g.turn + hop)
            post = g.suns[muster] if muster in g.suns else g.planets[muster].pos
            coming = [s for s in groups['trooper'] if s not in mustered and (
                bound_for(s, post) or (s.order is None and len(s.crew) >= od.seats_of(g.designs[s.design])))]
            have = aboard(mustered)
            odds = have / max(1, need)
            if coming:
                later = max(math.ceil(math.dist(s.pos, post) / max(0.1, mv.speed(g, s))) for s in coming)
                odds_later = (have + aboard(coming)) / max(1, self.troops_needed(g, target, g.turn + hop + later))
            else:
                odds_later = 0.0
            if odds >= LANDING_MARGIN or (odds >= GAMBLE and odds_later <= odds):
                for s in mustered:
                    out.append(('conquer', s.oid, target))
            for s in at_home:
                out.append(('conquer', s.oid, target))
            return out
        bombers_left = [s for s in groups['bomber'] if s.orbit == target or s in at_home
                        or bound_for(s, g.planets[target].pos)]
        r = self.intel.get(target)
        if in_orbit and not holding and r is not None and r['turn'] >= g.turn - FRESH and r['lost'] \
                and r['enemy_now'] > self.strength(g, in_orbit):
            # Losing the orbit to a stronger fleet: come home and gather.
            for s in in_orbit:
                out.append(('move', s.oid, home.oid))
            return out
        if in_orbit and guards:
            # Idle ships spare unarmed ones, and any of the owner's ships in
            # orbit stops a landing: an Attack order clears them.
            out += [('attack', s.oid, target) for s in in_orbit]
        if holding:
            if not bombers_left and r is not None and r['turn'] >= g.turn - FRESH and r['lost'] \
                    and not r['enemy_ships'] and r['defence_left'] > 1e-9:
                # The defence is killing ships and nothing can bomb it: come
                # home until bombers are built.
                for s in in_orbit:
                    out.append(('move', s.oid, home.oid))
                return out
            for s in at_home:
                out.append(('conquer', s.oid, target))
            return out
        # One wave: warships to clear the orbit and bombers to knock out the
        # defence, timed to arrive on the same turn. Troopers wait in the
        # target's system until a report says the defence is down.
        bombers = [s for s in at_home if s in groups['bomber']]
        need_bombers = BOMBERS if 'bomber' in designs else 0
        need_troops = WAVE_TROOPERS if 'trooper' in designs and self.seen_planets(g)[target][1] else 0
        sailing = any(s.order is not None and s.order.kind == mv.CONQUER
                      for s in fleet) or any(s.oid in self.launch for s in fleet)
        odds = self.odds_against(g, at_home, target)
        self.odds = [(t, o) for t, o in self.odds if t >= g.turn - ODDS_WINDOW] + [(g.turn, odds)]
        # Wait past FLEET_GAMBLE only while the trend reaches OUTNUMBER within
        # ODDS_WINDOW turns; a fleet that cannot outgrow the enemy's sails.
        # A wave waits on its probe until the defence is known or assumed.
        soon = odds + (odds - self.odds[0][1]) >= OUTNUMBER
        go = odds >= OUTNUMBER or (odds >= FLEET_GAMBLE and not soon)
        if UNKNOWN_DEFENCE == 'probe' and not self.known(g, target) and not self.assumes(g, target):
            self.probing.setdefault(target, g.turn)
            go = False
        if not sailing and go and len(at_home) - len(bombers) >= LAUNCH and len(bombers) >= need_bombers \
                and len(loaded) >= need_troops:
            pos = g.planets[target].pos
            eta = {s.oid: math.dist(s.pos, pos) / max(0.1, mv.speed(g, s)) for s in at_home}
            longest = max(eta.values())
            for s in at_home:
                self.launch[s.oid] = g.turn + int(longest - eta[s.oid])
        for s in groups['warship'] + groups['bomber']:
            if self.launch.get(s.oid, 10 ** 9) <= g.turn and s.order is None and s.orbit != target:
                out.append(('conquer', s.oid, target))
                del self.launch[s.oid]
        for k in [k for k in self.launch if k not in g.ships]:
            del self.launch[k]
        return out

    def wave_out(self, g):
        """Warships or bombers are in the mission target's orbit, flying
        there, or booked to leave for it."""
        t = self.mission_target(g)
        if t is None:
            return False
        pos = g.planets[t].pos
        return any(s.owner == self.civ and s.design in g.designs
                   and kind_of(g.designs[s.design]) in ('warship', 'bomber')
                   and (s.orbit == t or bound_for(s, pos) or s.oid in self.launch)
                   for s in g.ships.values())

    def mission_target(self, g):
        t = self.goals.mission
        seen = self.seen_planets(g)
        if t is None or t not in seen:
            return None
        owner = seen[t][0]
        if not owner or not self.at_war(g, owner):
            return None
        return t


def ground_won(attackers, defenders, bunkers=0, fortresses=0):
    """Fresh troops landing on fresh soldiers, by combat.invade's rules,
    traits left out since the enemy's are unknown."""
    attack = combat.ground_side([0.0] * attackers, 0, 0)
    defend = combat.ground_side([0.0] * defenders, bunkers, fortresses)
    for _round in range(combat.GROUND_ROUNDS):
        a_fire, d_fire = combat.volume(attack), combat.volume(defend)
        combat.strike(d_fire, attack)
        combat.strike(a_fire, defend)
    return any(h > 0 for h, _f in attack) and not any(h > 0 for h, _f in defend)


def ground_left(attackers, defenders, bunkers=0, fortresses=0):
    """The defenders standing after fresh troops land on fresh soldiers."""
    attack = combat.ground_side([0.0] * attackers, 0, 0)
    defend = combat.ground_side([0.0] * defenders, bunkers, fortresses)
    for _round in range(combat.GROUND_ROUNDS):
        a_fire, d_fire = combat.volume(attack), combat.volume(defend)
        combat.strike(d_fire, attack)
        combat.strike(a_fire, defend)
    return sum(1 for h, _f in defend if h > 0)


def troops_beating(defenders, bunkers=0, fortresses=0):
    """The fewest troops that take a planet these soldiers hold."""
    n = max(1, defenders)
    while not ground_won(n, defenders, bunkers, fortresses):
        n += max(1, n // 8)
    return n


def defenders_beating(attackers, bunkers=0, fortresses=0):
    """The fewest soldiers that hold against this many troops."""
    d = 1
    while ground_won(attackers, d, bunkers, fortresses):
        d += 1
    return d


def most_recruits(scan):
    """Soldiers a turn a scanned planet could add with every surplus meal
    going to recruitment, its farmers on the best rate a planet has."""
    jobs = scan['jobs']
    pop = sum(jobs.values())
    farms = scan['facilities'].get(FARM, 0)
    food = jobs.get(ec.FARMER, 0) * MOST_FOOD_RATE * (1 + ec.FARM_BONUS * farms / 100)
    return max(0.0, food - ec.FOOD_PER_CITIZEN * pop) / ec.SOLDIER_FOOD


def bound_for(s, pos):
    """The ship is flying to `pos`."""
    return s.order is not None and s.order.target is not None and math.dist(s.order.target, pos) < 1.0


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
