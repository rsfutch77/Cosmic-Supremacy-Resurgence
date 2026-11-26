"""
test_load.py: a save blob loads into the simulator's state
==========================================================
    server\\.venv\\Scripts\\python.exe sim\\tests\\test_load.py

`client/SinglePlayerGalaxy.dat` is the shipped Single Player start: two civs,
DemoPlayer and BadGuy, one homeworld of seven citizens each, two ships each
and five designs. The governor fixtures are a later galaxy with production,
facilities and governors. Design stats are checked against the component
tables by hand: a Colony Ship is a Shuttle, a Neutron Scanner, three Nuclear
Drives and a Colony Module, which fills the Shuttle's 120 space exactly.
"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from sim import load as sl                                      # noqa: E402

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
INVADE = os.path.join(ROOT, 'sim', 'corpus', 'capture_live_invade_t{:04d}.after.dat')
FIXTURES = sorted(glob.glob(os.path.join(ROOT, 'server', 'tests', 'fixtures',
                                         '*', '*.dat')))
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def single_player():
    print('Single Player start')
    g = sl.load_path(GALAXY)
    check('no gaps', g.gaps, [])
    check('turn', g.turn, 0)
    check('content version is released', g.content_version, 565)
    check('market empty', g.market, [0, 0, 0, 0, 0])
    check('civs', sorted((c.oid, c.name) for c in g.civs.values()),
          [(191, 'DemoPlayer'), (195, 'BadGuy')])
    check('planets', len(g.planets), 158)
    check('systems', len(g.suns), 32)
    check('every planet sits in a system', all(p.system in g.suns
                                              for p in g.planets.values()), True)
    for civ in g.civs.values():
        home = g.civ_planets(civ.oid)
        check(f'{civ.name}: one homeworld', len(home), 1)
        check(f'{civ.name}: seven citizens', len(home[0].citizens), 7)
        check(f'{civ.name}: citizens are its own',
              {c[1] for c in home[0].citizens}, {civ.oid})
        check(f'{civ.name}: two ships', len(g.civ_ships(civ.oid)), 2)
        check(f'{civ.name}: credits', civ.credits, 200)
        check(f'{civ.name}: resources', civ.resources, [630, 350, 260, 0, 0])
        check(f'{civ.name}: two starting technologies', len(civ.completed), 2)
        check(f'{civ.name}: no research topic', civ.topic, -1)
    check('every ship is undamaged', {s.condition for s in g.ships.values()},
          {1.0})
    check('every ship has a held design',
          all(s.design in g.designs for s in g.ships.values()), True)

    by_name = {(d.owner, d.name): d for d in g.designs.values()}
    colony = by_name[(191, 'Colony Ship')]
    check('Colony Ship parts', colony.parts,
          {'chassis': [0], 'scanners': [0], 'engines': [0, 0, 0],
           'weapons': [], 'modules': [0], 'shields': []})
    check('Colony Ship units', colony.units, 20 + 5 + 3 * 5 + 20)
    check('Colony Ship fills its chassis', (colony.space_used, colony.space),
          (120, 120))
    check('Colony Ship cost', colony.cost, 225 + 100 + 3 * 120 + 150)
    check('Colony Ship is light', colony.heavy, False)
    check('Colony Ship unarmed', colony.firepower, (0, 0, 0))
    bomber = next(d for d in g.designs.values() if d.name == 'b1')
    check('b1 carries a Fusion Bomb', bomber.parts['weapons'], [10])
    check('b1 firepower', bomber.firepower, (0, 0, 500))
    fighter = next(d for d in g.designs.values() if d.name == 'f1')
    check('f1 carries a Mass Driver', fighter.firepower, (35, 10, 0))
    check('f1 hitpoints, no shield', fighter.hitpoints, fighter.units)


def fixtures():
    print('\nFixtures')
    for path in FIXTURES:
        name = os.path.relpath(path, ROOT)
        g = sl.load_path(path)
        check(f'{name}: no gaps', g.gaps, [])
        check(f'{name}: designs fit their chassis',
              [d.name for d in g.designs.values() if d.space_used > d.space], [])
        check(f'{name}: every fleet member is a loaded ship',
              all(s in g.ships for f in g.fleets.values() for s in f.ships), True)
        check(f'{name}: citizens hold known jobs',
              {c[0] for p in g.planets.values() for c in p.citizens}
              <= {0, 1, 2, 5, 6}, True)


def conquest():
    """Civ 656 takes planet 384 from 660 in the turn-125 tick, 5 soldiers on 16
    citizens; the corpus is gitignored, so this is skipped without it."""
    print('\nConquest (capture_live_invade)')
    if not os.path.exists(INVADE.format(129)):
        print('  skipped: sim/corpus is not here')
        return
    for tick, loyalty, unrest in ((125, 23.81, 0), (126, 26.93, 1), (127, 30.05, 0),
                                  (129, 36.29, 0)):
        g = sl.load_path(INVADE.format(tick))
        p = g.planets[384]
        check(f'tick {tick}: no gaps', g.gaps, [])
        check(f'tick {tick}: loyalty', round(p.loyalty, 2), loyalty)
        check(f'tick {tick}: rebellion counter', p.unrest, unrest)
    check('two rulers, the taker gaining 4.094 a turn',
          [(o, round(i, 3), f, t) for o, i, f, t in p.rulers],
          [(660, 83.623, 0, 125), (656, 16.377, 126, -1)])
    check('taken from 660', p.prior_owner, 660)
    check('production and ore still read', (p.production is not None, p.ore), (True, 0))


def main():
    single_player()
    fixtures()
    conquest()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
