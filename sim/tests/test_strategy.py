"""
test_strategy.py: each goal changes play the way it says
=========================================================
    server\\.venv\\Scripts\\python.exe sim\\tests\\test_strategy.py

Plays the Single Player galaxy with one civ on fixed goals (sim/strategy.py)
and the other on the scripted baseline, and checks the outcome each goal
should push toward. These are the M1 checks of docs/RL_Training_Plan.md.
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from sim import baseline                                        # noqa: E402
from sim import reward as rw                                    # noqa: E402
from sim import strategy as sg                                  # noqa: E402
from sim.env import Env                                         # noqa: E402

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
TURNS = 150
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def play(goals, turns=TURNS, switch=None):
    """One game; `switch` is (turn, goals) to change to."""
    env = Env.from_blob(GALAXY, horizon=turns)
    env.reset()
    me, them = env.played
    pl = sg.Player(me, goals)
    refused = []
    for _t in range(turns):
        if switch and env.g.turn == switch[0]:
            pl.goals = switch[1]
        _obs, _r, done, info = env.step({me: pl.act(env.g), them: baseline.act(env.g, them)})
        refused += info.get(me, [])
        if done:
            break
    g = env.g
    mine = [p for p in g.planets.values() if p.owner == me]
    fac = collections.Counter()
    for p in mine:
        fac.update(p.facilities)
    ships = collections.Counter(sg.kind_of(g.designs[s.design])
                                for s in g.ships.values() if s.owner == me)
    return {'planets': len(mine), 'S': rw.strength(g, me), 'T': rw.ground(g, me),
            'done': set(g.civs[me].completed or ()), 'facilities': fac, 'ships': ships,
            'explored': len(g.civs[me].explored), 'refused': refused, 'g': g, 'them': them,
            'me': me}


def main():
    runs = {(posture, line): play(sg.Goals(posture=posture, line=line))
            for posture in sg.POSTURES for line in ('economy', 'weapons', 'defence')}
    check('no role order is refused',
          sum(len(r['refused']) for r in runs.values()), 0)

    planets = {k: r['planets'] for k, r in runs.items()}
    check('expand settles more planets than any arm or defend game',
          min(planets[('expand', line)] for line in ('economy', 'weapons', 'defence'))
          > max(planets[(p, line)] for p in ('arm', 'defend')
                for line in ('economy', 'weapons', 'defence')))
    strength = {k: r['S'] for k, r in runs.items() if k[1] == 'weapons'}
    check('arm with weapons research has the strongest fleet of the weapons games',
          max(strength, key=strength.get), ('arm', 'weapons'))
    turrets = {k: r['facilities'][sg.LIGHT_TURRET] for k, r in runs.items()}
    check('defend with defence research builds the most turrets',
          max(turrets, key=turrets.get), ('defend', 'defence'))
    science = {k: r['facilities'][sg.UNIVERSITY] for k, r in runs.items() if k[1] == 'economy'}
    check('grow builds more universities than arm or defend',
          science[('grow', 'economy')] > max(science[('arm', 'economy')],
                                             science[('defend', 'economy')]))
    check('the weapons line researches Cold Fusion, the Mass Driver',
          3 in runs[('balanced', 'weapons')]['done'])
    check('the defence line researches Planetary Defense 1, the Light Turret',
          27 in runs[('balanced', 'defence')]['done'])
    check('the economy line researches Astro Engineering, the Factory',
          2 in runs[('balanced', 'economy')]['done'])
    check('scouts reach most of the galaxy', runs[('balanced', 'economy')]['explored'] >= 20)

    war = play(sg.Goals(posture='expand', line='hulls'), turns=330,
               switch=(90, sg.Goals(posture='arm', line='weapons', war=195, mission=186)))
    g = war['g']
    check('a war goal declares war', g.civs[war['me']].stances.get(war['them']), 1)
    check('arming after expanding builds a fleet', war['ships']['warship'] >= 20)
    check('the fleet destroys most of the rival\'s warships',
          sum(1 for s in g.ships.values() if s.owner == war['them']
              and sg.kind_of(g.designs[s.design]) == 'warship') <= 5)
    check("and the troops take the rival's homeworld", g.planets[186].owner, war['me'])
    reports = [what[1] for _t, what in g.civs[war['me']].news
               if what[0] == 'battle' and what[1]['planet'] == 186]
    check('battle reports at the homeworld tell both sides and what the defence has left',
          bool(reports) and all(len(r['sides']) >= 2 and 'defence_left' in r for r in reports))
    check('a report showed the defence down before the landing',
          any(r['defence_left'] <= 1e-9 for r in reports))
    check('the first report from the target is an unarmed probe\'s',
          bool(reports) and not any(reports[0]['sides'][war['me']]['firepower']))

    print('the defence a wave is sent against')
    pl = sg.Player(war['me'], sg.Goals('arm', 'weapons', war=war['them'], mission=186))
    mine = [s for s in g.ships.values() if s.owner == war['me']]
    guns = [s for s in mine if sg.kind_of(g.designs[s.design]) == 'warship'][:10]
    bombs = [s for s in mine if sg.kind_of(g.designs[s.design]) == 'bomber']
    pl.intel[186] = {'turn': g.turn, 'fire_most': 500.0, 'hp_most': 0.0, 'planet_most': 1000.0}
    check('warships alone cannot clear a turret', pl.odds_against(g, guns, 186), 0.0)
    check('with bombers they can', pl.odds_against(g, guns + bombs, 186) > 0)
    del pl.intel[186]
    check('an unknown defence is none until assumed', pl.defence(g, 186), (0.0, 0.0, 0.0))
    sg.UNKNOWN_DEFENCE = 'assume'
    check('and two light turrets when assumed', pl.defence(g, 186), (1000.0, 0.0, 2000.0))
    sg.UNKNOWN_DEFENCE = 'probe'
    g.civs[war['me']].intel.append({'turn': g.turn, 'kind': 'planetary', 'target': 186, 'soldiers': 0,
                                    'jobs': {}, 'facilities': {sg.HEAVY_TURRET: 1}})
    check('a scan\'s turrets are the known defence', (pl.known(g, 186), pl.defence(g, 186)),
          (True, (1200.0, 0.0, 2000.0)))
    g.civs[war['me']].intel.pop()

    print('the garrison estimate')
    check('the troops it asks for take the planet',
          all(sg.ground_won(sg.troops_beating(d), d) for d in (1, 5, 20, 40)))
    check('bunkers raise the troops needed', sg.troops_beating(20, 2) > sg.troops_beating(20))
    check('a failed landing of 20 troops shows at least as many soldiers',
          sg.defenders_beating(20) >= 1 and not sg.ground_won(20, sg.defenders_beating(20)))
    pl = sg.Player(war['me'], sg.Goals('arm', 'weapons', war=war['them'], mission=186))
    g = war['g']
    g.civs[war['me']].intel += [
        {'turn': 100, 'kind': 'planetary', 'target': 186, 'soldiers': 10, 'jobs': {0: 6, 1: 6},
         'facilities': {}},
        {'turn': 120, 'kind': 'planetary', 'target': 186, 'soldiers': 14, 'jobs': {0: 6, 1: 6},
         'facilities': {15: 1}}]
    est, rate, bunkers, _forts = pl.garrison(g, 186, 140)
    check('two scans give the growth between them', (round(rate, 2), round(est, 1), bunkers), (0.2, 18.0, 1))
    floor = sg.defenders_beating(20)
    check('a failed landing leaves fewer soldiers than beat it', 0 <= sg.ground_left(20, floor) < floor)
    pl.failed[186] = (130, 5)
    check('a failed landing after the scans restarts the estimate from what it left',
          round(pl.garrison(g, 186, 140)[0], 1), 7.0)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
