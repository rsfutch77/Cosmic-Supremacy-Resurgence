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

    war = play(sg.Goals(posture='expand', line='hulls'), turns=300,
               switch=(90, sg.Goals(posture='arm', line='weapons', war=195, mission=186)))
    g = war['g']
    check('a war goal declares war', g.civs[war['me']].stances.get(war['them']), 1)
    check('arming after expanding builds a fleet', war['ships']['warship'] >= 20)
    check('the fleet destroys most of the rival\'s warships',
          sum(1 for s in g.ships.values() if s.owner == war['them']
              and sg.kind_of(g.designs[s.design]) == 'warship') <= 5)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
