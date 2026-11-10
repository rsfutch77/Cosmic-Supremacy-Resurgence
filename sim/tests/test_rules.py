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
from sim import env as ev                                       # noqa: E402
from sim import load as sl                                      # noqa: E402
from sim import movement as mv                                  # noqa: E402
from sim import orders as od                                    # noqa: E402
from sim import research as rs                                  # noqa: E402
from sim import state as st                                     # noqa: E402

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
    g.civs[DEMO].stances[BAD] = g.civs[BAD].stances[DEMO] = combat.WAR
    check('the designs are the captured ones', (fighter.hitpoints, fighter.firepower,
                                                bomber.hitpoints, bomber.firepower),
          (42.0, (35, 10, 0), 80.0, (0, 0, 500)))
    combat.battles(g)
    check('both attackers die and one defender keeps 92.38%',
          (910 in g.ships, 911 in g.ships, round(g.ships[912].condition, 4),
           round(g.ships[913].condition, 4)), (False, False, 1.0, 0.9238))
    combat.battles(g)
    check('a light ship at its shipyard mends 15% a turn', g.ships[913].condition, 1.0)


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
                 combat_checks, order_checks, env_checks):
        part()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
