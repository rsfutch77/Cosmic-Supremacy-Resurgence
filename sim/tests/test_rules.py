"""
test_rules.py: research, colonisation, ship builds, loyalty, combat, orders
==========================================================================
    server\\.venv\\Scripts\\python.exe sim\\tests\\test_rules.py

Each check restates something an engine tick showed (docs/Fast_Sim_Plan.md,
section 5b), on `client/SinglePlayerGalaxy.dat` with the state the capture had
put in place, since the oracle corpus itself is not in the repository.
"""
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from sim import combat                                          # noqa: E402
from sim import economy as ec                                   # noqa: E402
from sim import fog                                             # noqa: E402
from sim import env as ev                                       # noqa: E402
from sim import load as sl                                      # noqa: E402
from sim import movement as mv                                  # noqa: E402
from sim import orders as od                                    # noqa: E402
from sim import research as rs                                  # noqa: E402
from sim import reward as rw                                    # noqa: E402
from sim import scans as sc                                     # noqa: E402
from sim import state as st                                     # noqa: E402
from sim import traits                                          # noqa: E402

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
DEMO, BAD = 191, 195
HOME = 138                      # DemoPlayer's homeworld: shipyard, camp, light turret
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def galaxy():
    return copy.deepcopy(BASE)


def research_checks():
    check('a technology costs 800 x factor x techs done', rs.cost(29, [0, 1, 27, 28]), 16000)
    check('a goal walks its prerequisites, cheapest first then lower id',
          rs.next_topic(11, [0, 1]), 3)
    check('Robo Mining takes Mining before Particle Engineering',
          rs.next_topic(37, [0, 1, 12, 2]), 35)
    check('a doctrine needs one of each tier its list names',
          [rs.next_topic(67, done) for done in ([0, 1], [0, 1, 43], [0, 1, 43, 47])],
          [43, 47, 50])
    g = galaxy()
    c = g.civs[BAD]
    c.uid, c.research, c.topic, c.goal = 21, 4706, 27, 28
    c.completed = [0, 1, 3, 18, 43, 12]
    g.planets[186].citizens = [(ec.SCIENTIST, BAD)] * 3 + g.planets[186].citizens[3:]
    g = ev.tk.tick(g)
    check('the turn\'s science is banked before the topic is paid',
          (27 in g.civs[BAD].completed, g.civs[BAD].topic, g.civs[BAD].goal), (True, 28, 28))


def colonise_checks():
    g = galaxy()
    ship = g.ships[193]
    target = min((p for p in g.planets.values() if not p.owner and p.system == g.planets[HOME].system),
                 key=lambda p: p.oid)
    check('a colony ship with crew may be sent', od.colonise(g, DEMO, 193, target.oid), (True, ''))
    for _ in range(20):
        g = ev.tk.tick(g)
        if 193 not in g.ships:
            break
    p = g.planets[target.oid]
    check('arriving founds the colony and the ship is gone',
          (193 in g.ships, p.owner, p.jobs(), p.food[1], p.production.kind, p.history_len),
          (False, DEMO, {ec.FARMER: 2}, ec.food_capacity(2), 'WLTH', 1))


def build_checks():
    g = galaxy()
    colony = g.designs[g.ships[193].design]
    check('a colony ship costs 100 more per planet and colony ship, less one',
          ec.ship_cost(g, DEMO, colony), colony.cost + 100 * (1 + 2 - 1))
    store = list(g.civs[DEMO].resources)
    check('starting a ship charges its resources at once',
          (od.build_ship(g, DEMO, HOME, colony.oid), g.civs[DEMO].resources),
          ((True, ''), [a - b for a, b in zip(store, colony.resources)]))
    p = g.planets[HOME]
    cost = ec.ship_cost(g, DEMO, colony)
    p.production.progress = cost // 2 - 1
    check('a hurry needs the item half done', od.hurry(g, DEMO, HOME)[0], False)
    p.production.progress = cost - 100
    credits = g.civs[DEMO].credits = 1000
    check('a hurry costs 4 credits a point left',
          (od.hurry(g, DEMO, HOME)[0], g.civs[DEMO].credits), (True, credits - 400))
    before = set(g.ships)
    g = ev.tk.tick(g)
    new = [s for s in g.ships.values() if s.oid not in before]
    check('a finished ship is launched in orbit, uncrewed, and the planet falls to Wealth',
          ([(s.owner, s.design, s.orbit, len(s.crew)) for s in new], g.planets[HOME].production.kind),
          ([(DEMO, colony.oid, HOME, 0)], 'WLTH'))


def loyalty_checks():
    p = st.Planet(25, DEMO, 0, (0, 0, 0), 'p', citizens=[(0, DEMO)] * 24,
                  military=[(3, DEMO)] * 4, military_xp=[1.2] * 4)
    check('four green soldiers lift 24 citizens 0.83%', round(ec.loyalty_bonus(p), 2), 0.83)
    check('rank is the doubling ladder', [ec.rank(x) for x in (0, 1.9, 2, 4, 7.9, 8, 300)],
          [0, 0, 1, 2, 2, 3, 8])
    p.facilities = {ec.MILITARY_CAMP: 1}
    ec.train(p)
    check('a camp doubles training to 0.2 a turn', [round(x, 3) for x in p.military_xp], [1.4] * 4)


def combat_checks():
    """The turn-110 battle: a fighter and a bomber arrive at a planet with a
    light turret and two fighters."""
    g = galaxy()
    fighter = st.Design(900, BAD, 'f1', {'chassis': [0], 'scanners': [], 'engines': [0, 0],
                                         'weapons': [0], 'modules': [], 'shields': []})
    bomber = st.Design(901, BAD, 'b1', {'chassis': [0], 'scanners': [], 'engines': [0, 0],
                                        'weapons': [10], 'modules': [], 'shields': []})
    guard = st.Design(902, DEMO, 'bgf1', fighter.parts)
    g.designs.update({900: fighter, 901: bomber, 902: guard})
    home = g.planets[HOME]
    for oid, owner, d, crew in ((910, BAD, 900, 1), (911, BAD, 901, 1),
                                (912, DEMO, 902, 2), (913, DEMO, 902, 2)):
        g.ships[oid] = st.Ship(oid, owner, home.pos, d, [(3, owner)] * crew, 1.0,
                               orbit=HOME, crew_xp=[0.0] * crew)
        if owner == BAD:
            g.ships[oid].arrived = g.turn          # they flew in on a move order
    g.civs[DEMO].stances[BAD] = g.civs[BAD].stances[DEMO] = combat.WAR
    check('the designs are the captured ones', (fighter.hitpoints, fighter.firepower,
                                                bomber.hitpoints, bomber.firepower),
          (42.0, (35, 10, 0), 80.0, (0, 0, 500)))
    combat.battles(g)
    check('both attackers die and one defender keeps 92.38%',
          (910 in g.ships, 911 in g.ships, round(g.ships[912].condition, 4),
           round(g.ships[913].condition, 4)), (False, False, 1.0, 0.9238))
    check('defending crews gain 2 x 545 / 590 experience, the capture\'s 1.85',
          [round(x, 3) for x in g.ships[913].crew_xp], [1.847, 1.847])
    check('the defender gains score and reputation for the attackers it killed',
          (g.civs[DEMO].score > 0, g.civs[DEMO].reputation > 0, g.civs[BAD].reputation), (True, True, 0.0))
    combat.battles(g)
    check('a light ship at its shipyard mends 15% a turn', g.ships[913].condition, 1.0)
    participation_checks()


def participation_checks():
    """The live battle probes: an idle attacker fires back at an armed guard
    and spares an unarmed one, an Attack order opens fire, open space needs an
    order, a probed Destroyer needs 4 crew, bombing kills citizens and a bombed
    defence stays bombed while the attackers stay."""
    def staged(order=None, orbit=HOME, armed=True):
        g = galaxy()
        gun = st.Design(900, BAD, 'g', {'chassis': [1], 'scanners': [], 'engines': [0],
                                        'weapons': [1, 1, 1], 'modules': [], 'shields': []})
        guard = st.Design(902, DEMO, 'd', dict(gun.parts, weapons=[1, 1, 1] if armed else []))
        g.designs.update({900: gun, 902: guard})
        at = g.planets[HOME].pos if orbit else (1.0, 2.0, 3.0)
        g.ships[910] = st.Ship(910, BAD, at, 900, [(3, BAD)], 1.0, orbit=orbit, crew_xp=[0.0])
        g.ships[912] = st.Ship(912, DEMO, at, 902, [(3, DEMO)], 1.0, orbit=orbit, crew_xp=[0.0])
        if order:
            g.ships[910].order = st.Order(order, at, 0.0, [], 0)
        g.civs[DEMO].stances[BAD] = g.civs[BAD].stances[DEMO] = combat.WAR
        return g
    g = staged()
    combat.battles(g)
    check('an idle attacker in orbit fires back at an armed guard', g.ships[912].condition < 1.0)
    def guards(g):
        return sum(s.condition for s in g.ships.values() if s.owner == DEMO and s.orbit == HOME)
    g = staged(armed=False)
    whole = guards(g)
    combat.battles(g)
    check('and spares unarmed ones', guards(g), whole)
    g = staged(mv.ATTACK, armed=False)
    combat.battles(g)
    check('an Attack order opens fire on them', guards(g) < whole)
    g = staged()
    g.designs[900] = st.Design(900, BAD, 'dd', {'chassis': [3], 'scanners': [], 'engines': [1],
                                               'weapons': [5, 5], 'modules': [], 'shields': []})
    g.ships[910].crew, g.ships[910].crew_xp = [(3, BAD)] * 3, [0.0] * 3
    combat.battles(g)
    check('a Destroyer with 3 crew does not fight', (g.ships[912].condition, od.move(g, BAD, 910, HOME)[0]),
          (1.0, False))

    def need(parts):
        d = st.Design(0, BAD, '', dict({c: [] for c in mv.tb.CATEGORIES}, **parts))
        return mv.crew_needed(d, g.civs[BAD])
    check('the crew a design needs grows with the space its parts fill',
          [need(p) for p in ({'chassis': [3]},
                             {'chassis': [3], 'engines': [1], 'weapons': [5, 5], 'shields': [0],
                              'scanners': [0]},
                             {'chassis': [2], 'engines': [1], 'weapons': [2], 'shields': [2],
                              'scanners': [0]},
                             {'chassis': [2], 'engines': [1], 'modules': [1], 'scanners': [0]})],
          [3, 4, 1, 2])
    g = staged()
    p = g.planets[HOME]
    g.radius[HOME] = 1.6
    g.ships[910].orbit, g.ships[910].pos = 0, (p.pos[0] + 0.8, p.pos[1], p.pos[2])
    shot = combat.Planet(g, p).max > 0
    combat.battles(g)
    check('a ship inside the radius does not fire, and only the planet shoots it',
          (p.owner == DEMO, g.ships[912].condition,
           (910 not in g.ships or g.ships[910].condition < 1.0) == shot), (True, 1.0, True))
    g = staged(None, 0)
    combat.battles(g)
    check('in open space there is no battle without an order',
          (g.ships[910].condition, g.ships[912].condition), (1.0, 1.0))
    g = staged(mv.ATTACK, 0)
    combat.battles(g)
    check('with one both sides fire', (g.ships[910].condition < 1.0, g.ships[912].condition < 1.0),
          (True, True))
    g = galaxy()
    p = g.planets[HOME]
    bomber = st.Design(901, BAD, 'b', {'chassis': [0], 'scanners': [], 'engines': [0],
                                       'weapons': [10], 'modules': [], 'shields': []})
    g.designs[901] = bomber
    g.ships[911] = st.Ship(911, BAD, p.pos, 901, [(3, BAD)], 1.0, orbit=HOME, crew_xp=[0.0])
    g.civs[DEMO].stances[BAD] = g.civs[BAD].stances[DEMO] = combat.WAR
    p.facilities.pop(combat.LIGHT_TURRET, None)
    p.facilities[combat.SHIELD_GENERATOR] = 1
    pop = len(p.citizens)
    combat.battles(g)
    first = p.defence_damage
    combat.battles(g)
    check('a bombed defence stays bombed while the bomber stays', p.defence_damage > first > 0)
    check('bombing kills a citizen per 250 of defence damage',
          pop - len(p.citizens), int(first // 250) + int((p.defence_damage - first) // 250))


def bio_checks():
    """The manual's bio-bombing; no capture has one."""
    g = galaxy()
    home = g.planets[HOME]
    home.military, home.military_xp = [(3, DEMO)] * 5, [0.0] * 5
    pop = len(home.citizens)
    g.designs[904] = st.Design(904, BAD, 'bio', {'chassis': [1], 'scanners': [], 'engines': [0],
                                                 'weapons': [], 'modules': [4], 'shields': []})
    g.ships[930] = st.Ship(930, BAD, home.pos, 904, [(3, BAD)], 1.0, crew_xp=[0.0])
    g.civs[DEMO].stances[BAD] = g.civs[BAD].stances[DEMO] = combat.WAR
    check('a ship with bio-bombs takes the order', od.bio_bomb(g, BAD, 930, HOME, 2, 1), (True, ''))
    g.ships[930].orbit = HOME
    combat.bio_bomb(g, home, [g.ships[930]])
    check('one set kills 20, soldiers first, blights 5 space and costs 0.1 reputation',
          (len(home.military), pop - len(home.citizens), home.blight,
           round(g.civs[BAD].reputation, 2), g.ships[930].order.runs),
          (0, min(pop, 15), 5, -0.1, 1))


def trait_checks():
    g = galaxy()
    c = g.civs[BAD]
    check('Doctrine: Knowledge\'s -7 wastage turns 40 science into 43',
          40 - ec.R(40 * ec.wastage(40, st.Civ(0, '', completed=[47])) / 100), 43)
    c.golden_age = 5
    check('a golden age adds 50 to the four output traits',
          [traits.value(c, t) for t in (2, 3, 4, 5, 6)], [50, 50, 50, 50, 0])
    c.golden_age = 0
    c.completed = list(c.completed) + [43]
    check('Mobility speeds light ships 20%', round(mv.speed(g, g.ships[197]), 3), 16.2)
    check('a doctrine finished in research starts a 99-turn golden age',
          traits.GOLDEN_AGE_TURNS - 1, 99)


def doctrine_checks():
    """The doctrine probes: a doctrine bought in a tick already shapes that
    tick's output, Elite's +70 trains 1.7 times as fast, and Lightweight's
    -25 units speeds a ship by a third."""
    runs = {}
    for topic in (-1, 49):
        g = galaxy()
        c = g.civs[BAD]
        c.completed, c.research, c.topic, c.goal = list(c.completed) + [46], 5_000_000, topic, topic
        before = c.credits
        g = ev.tk.tick(g)
        runs[topic] = g.civs[BAD].credits - before
    check('Doctrine: Trade pays its +4 a citizen in the tick it is bought',
          runs[49] - runs[-1], 4 * len(BASE.planets[186].citizens))
    p = st.Planet(1, BAD, 0, (0, 0, 0), 'p', military=[(3, BAD)], military_xp=[0.0],
                  facilities={ec.MILITARY_CAMP: 1})
    ec.train(p, st.Civ(BAD, '', completed=[56]))
    check('Elite trains at 0.2 x 1.7', round(p.military_xp[0], 3), 0.34)
    g = galaxy()
    g.civs[BAD].completed = list(g.civs[BAD].completed) + [65]
    check('Lightweight\'s -25 units speeds a ship by a third', round(mv.speed(g, g.ships[197]), 3), 18.0)
    plain = st.Civ(BAD, '', completed=[])
    check('Stronghold adds 10% planet space and Habitat 20%',
          [ec.space_bonus(st.Civ(BAD, '', completed=[t])) - ec.space_bonus(plain) for t in (54, 62)],
          [10, 20])
    g = galaxy()
    g.civs[BAD].completed = list(g.civs[BAD].completed) + [44]
    g = ev.tk.tick(g)
    check('Colonization\'s -15 makes a soldier cost 255 food', g.planets[186].military_cost, 255)
    check('Elite\'s +20 makes a green soldier 1.2 health and 2.4 fire',
          [[round(v, 3) for v in s] for s in combat.ground_side([0.0], 0, 0, st.Civ(BAD, '', completed=[56]))],
          [[1.2, 2.4]])
    d = st.Design(1, BAD, 'b', {'chassis': [ec.BATTLESHIP]})
    check('Versatility adds 5% hull space, and Titan 25% more to a battleship',
          [od.hull_space(st.Civ(BAD, '', completed=c), d) for c in ([], [58], [58, 68])],
          [d.space, ec.R(d.space * 1.05), ec.R(d.space * 1.30)])
    g = galaxy()
    c = g.civs[BAD]
    c.completed = list(c.completed) + [57]
    c.scans[0] = 1
    draw = sc.chance
    sc.chance = lambda *_a: 0
    try:
        od.scan(g, BAD, 0, HOME)
    finally:
        sc.chance = draw
    check('Intelligence scans are spotted but never named', g.civs[DEMO].news[-1][1][-1], None)
    armed = next(d for d in galaxy().designs.values() if rw._fire(d))
    check('the reward counts Bio-Armor\'s +5% hitpoints',
          round(rw._armed(st.Civ(BAD, '', completed=[66]), armed)[1] / rw._armed(plain, armed)[1], 3), 1.05)


def debt_checks():
    """The upkeep probes: debt dismantles a ship at home, then the costliest
    facility, then soldiers, and credits never go below 0."""
    g = galaxy()
    c, p = g.civs[BAD], g.planets[186]
    c.credits = 0
    p.military = list(p.military) + [(ec.MILITARY, BAD)] * 200
    p.military_xp = [0.0] * len(p.military)
    home = [s.oid for s in g.ships.values() if s.owner == BAD and g.planets.get(s.orbit) is p]
    built = sum(p.facilities.values())
    g = ev.tk.tick(g)
    q = g.planets[186]
    check('a civ in debt ends the turn at 0 or above', g.civs[BAD].credits >= 0)
    check('it loses a ship orbiting its own planet', any(o not in g.ships for o in home) or not home)
    check('then its costliest facility, then soldiers',
          (sum(q.facilities.values()) == built - 1, len(q.military) < 200), (True, True))


def unrest_checks():
    g = galaxy()
    home = g.planets[HOME]
    home.founded, g.turn = 0, 40
    ec.conquered(g, home, BAD)
    check('a planet held 40 turns is 60% loyal to its taker', (home.loyalty, home.prior_owner), (60.0, DEMO))
    check('a resisting planet loses its shortfall', ec.loyalty_bonus(home), -40.0)
    home.owner, home.military, home.military_xp = BAD, [], []
    ec.settle(g, home, g.civs[BAD])
    check('it comes round a point a turn with no soldiers', home.loyalty, 61.0)


def fog_checks():
    g = galaxy()
    scout = g.ships[197]
    sun = 140
    planet = g.planets[g.system_planets[sun][0]]
    scout.pos = tuple(c + d for c, d in zip(planet.pos, (29.0, 0, 0)))
    g.turn += 1
    fog.update(g)
    check('a planet 29 away from a 30-range scanner refreshes its system',
          g.civs[BAD].seen.get(sun, (None,))[0], g.turn)
    scout.pos = tuple(c + d for c, d in zip(planet.pos, (300.0, 0, 0)))
    g.turn += 1
    fog.update(g)
    check('out of range the row keeps the turn it was last seen', g.civs[BAD].seen[sun][0], g.turn - 1)
    c = g.civs[BAD]
    check('a scan needs a Central Defense Agency', od.build_scan(g, BAD, 186, 2)[0], False)
    c.scans[1] = 1
    check('a system scan watches a system for 8 turns',
          (od.scan(g, BAD, 1, 135), c.watching.get(135)), ((True, ''), 8))
    c.scans[0] = 1
    ok, _ = od.scan(g, BAD, 0, HOME)
    check('a planetary scan reports facilities and soldiers',
          (ok, sorted(c.intel[-1]['facilities']), c.intel[-1]['soldiers']), (True, [2, 6, 9], 0))


def conquest_checks():
    """The manual's ground combat and the live invasion probe. Only a ship
    with a Troop Bay that arrives under a Conquer order lands soldiers, and it
    keeps one more than its minimum crew aboard."""
    for modules, crew, soldiers, order, want in (([1], 6, 0, mv.CONQUER, (BAD, 4)),
                                                 ([1], 6, 8, mv.CONQUER, (DEMO, 6)),
                                                 ([1], 6, 0, None, (DEMO, 0)),
                                                 ([], 3, 0, mv.CONQUER, (DEMO, 0))):
        g = galaxy()
        home = g.planets[HOME]
        home.facilities = {k: v for k, v in home.facilities.items() if k not in (9, 10, 11)}
        home.military, home.military_xp = [(3, DEMO)] * soldiers, [0.0] * soldiers
        for oid in [s.oid for s in g.ships.values() if s.orbit == HOME]:
            del g.ships[oid]        # any ship in orbit, even unmanned, guards the planet
        g.designs[903] = st.Design(903, BAD, 'troops', {'chassis': [1], 'scanners': [], 'engines': [0],
                                                        'weapons': [], 'modules': modules, 'shields': []})
        g.ships[920] = st.Ship(920, BAD, home.pos, 903, [(3, BAD)] * crew, 1.0, orbit=HOME,
                               crew_xp=[0.0] * crew)
        if order:
            g.ships[920].order = st.Order(order, home.pos, 0.0, [], 0)
            g.ships[920].arrived = g.turn
        g.civs[DEMO].stances[BAD] = g.civs[BAD].stances[DEMO] = combat.WAR
        combat.battles(g)
        bay = 'a troop ship' if modules else 'a ship without a Troop Bay'
        how = 'arriving under Conquer' if order else 'idle in orbit'
        check(f'{bay} {how} with {crew} aboard against {soldiers} soldiers',
              (home.owner, len(home.military)), want)
    check('a Troop Bay seats 5 more and a Pilot Cabin 4',
          [od.seats_of(st.Design(0, 0, '', {'chassis': [1], 'modules': m})) for m in ([], [1], [2], [1, 2])],
          [3, 8, 7, 12])


def order_checks():
    g = galaxy()
    check('every citizen must be placed', od.set_jobs(g, DEMO, HOME, {0: 1})[0], False)
    check('a valid reassignment holds', (od.set_jobs(g, DEMO, HOME, {0: 5, 1: 2}),
                                         g.planets[HOME].jobs()), ((True, ''), {0: 5, 1: 2}))
    check('an unresearched facility is refused', od.build_facility(g, DEMO, HOME, 16)[0], False)
    check('a farm is allowed', od.build_facility(g, DEMO, HOME, 0), (True, ''))
    g.planets[HOME].military, g.planets[HOME].military_xp = [(3, DEMO)] * 3, [0.5, 2.5, 1.0]
    g.ships[193].crew, g.ships[193].crew_xp, g.ships[193].orbit = [], [], HOME
    check('crew boards the most experienced first',
          (od.crew(g, DEMO, 193, 2), sorted(g.ships[193].crew_xp), len(g.planets[HOME].military)),
          ((True, ''), [1.0, 2.5], 1))
    check('someone else\'s ship takes no orders', od.move(g, DEMO, 197, HOME)[0], False)
    metal, credits, held = g.civs[DEMO].resources[0], g.civs[DEMO].credits, g.market[0]
    ok, _ = od.trade(g, DEMO, 0, 100)
    check('selling 100 metal pays the engine\'s quote and fills the market',
          (ok, g.civs[DEMO].credits - credits, g.civs[DEMO].resources[0], g.market[0]),
          (True, sl.mo.market_total(0, 100, held, 0), metal - 100, held + 100))


def reputation_checks():
    """The manual's reputation term reaches the reward through corruption."""
    g = galaxy()
    by = {}
    for rep in (-20, 0, 20):
        g.civs[DEMO].reputation = rep
        by[rep] = (rw.corruption(g, DEMO)[HOME], rw.income_before_fleet(g, DEMO))
    check('reputation lowers the corruption the reward counts',
          by[-20][0] < by[0][0] < by[20][0] <= 0)
    check('and income follows it', by[-20][1] <= by[0][1] <= by[20][1])


def env_checks():
    e = ev.Env(BASE, horizon=5)
    obs = e.reset()
    check('each played civ observes its own homeworld',
          {c: [p['id'] for p in o['planets']] for c, o in obs.items()}, {DEMO: [HOME], BAD: [186]})
    total, done = 0, False
    while not done:
        _o, r, done, info = e.step({})
        total += 1
    check('the game ends at the horizon with D in [0, 1]',
          (total, all(0 <= v <= 1 for v in r.values())), (5, True))


def main():
    global BASE
    BASE = sl.load_path(GALAXY)
    for part in (research_checks, colonise_checks, build_checks, loyalty_checks,
                 combat_checks, conquest_checks, bio_checks, trait_checks, doctrine_checks, debt_checks, unrest_checks, fog_checks,
                 order_checks, reputation_checks, env_checks):
        part()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
