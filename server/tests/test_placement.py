"""
test_placement.py: a new civ's homeworld is placed farthest or at random
========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_placement.py

`inject_civ.pick_homeworld` places by `placement`: `farthest` is the maximin
over every claimed position, `random` a uniform pick among the free planets
that clear the margin. The pick checks use a row of planets on a line, where
the answer can be read off; the injection checks use the Single Player galaxy.
The join path is `test_joins.py`, the galaxy setting `test_galaxy_tool.py`.
"""
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp                                        # noqa: E402
import inject_civ as icv                                        # noqa: E402

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
INJECT = os.path.join(ROOT, 'server', 'dev_tools', 'inject_civ.py')
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def row_of_planets():
    """Planet 1 owned at x 0, planets 2 to 10 free at x 10, 20 ... 90."""
    return [{'id': i, 'pos': (10.0 * (i - 1), 0.0, 0.0),
             'owner': 7 if i == 1 else 0, 'nlen': 0} for i in range(1, 11)]


def refused(fn):
    try:
        fn()
    except SystemExit as exc:
        return str(exc)
    return None


def test_pick():
    print('picking from a row of planets')
    planets = row_of_planets()
    check('farthest takes the far end',
          icv.pick_homeworld(planets, set())['id'], 10)
    check('farthest is the default',
          icv.pick_homeworld(planets, set(), placement=icv.FARTHEST)['id'], 10)

    picks = {icv.pick_homeworld(planets, set(), placement=icv.RANDOM,
                                rng=random.Random(seed))['id']
             for seed in range(60)}
    check('random lands on more than one planet', len(picks) > 1)
    check('random lands nearer than the far end too', min(picks) < 10)
    check('random only takes free planets', picks <= set(range(2, 11)))

    picks = {icv.pick_homeworld(planets, set(), margin=45.0,
                                placement=icv.RANDOM,
                                rng=random.Random(seed))['id']
             for seed in range(60)}
    check('random keeps to the margin', picks, {6, 7, 8, 9, 10})

    picks = {icv.pick_homeworld(planets, {10}, placement=icv.RANDOM,
                                rng=random.Random(seed))['id']
             for seed in range(60)}
    check('random leaves a reserved planet alone', 10 in picks, False)

    check('the same seed picks the same planet',
          icv.pick_homeworld(planets, set(), placement=icv.RANDOM,
                             rng=random.Random('t12:Ada'))['id'],
          icv.pick_homeworld(planets, set(), placement=icv.RANDOM,
                             rng=random.Random('t12:Ada'))['id'])

    for placement in icv.PLACEMENTS:
        why = refused(lambda: icv.pick_homeworld(planets, set(), margin=95.0,
                                                 placement=placement))
        check(f'{placement}: a margin nothing clears is refused',
              bool(why and 'no room left' in why))
    why = refused(lambda: icv.pick_homeworld(planets, set(),
                                             placement='clustered'))
    check('a placement that names nothing is refused',
          bool(why and 'farthest, random' in why))
    # Control: the margin every planet clears changes nothing for farthest.
    check('CONTROL: a margin under the far end still takes it',
          icv.pick_homeworld(planets, set(), margin=85.0)['id'], 10)


def test_inject():
    print('adding a civ to the Single Player galaxy')
    blob = sp.load_any(GALAXY)
    far, home_far = icv.add_civ(blob, 'Ada', log=lambda *a: None)
    homes = set()
    for seed in range(8):
        out, home = icv.add_civ(blob, 'Ada', placement=icv.RANDOM,
                                rng=random.Random(seed), log=lambda *a: None)
        homes.add(home)
        sp.parse_blob(out)
    check('random homeworlds are not all the farthest one',
          homes != {home_far})
    owned = [p for p in icv.planet_records(out) if p['owner']]
    check('the civ added at random owns its homeworld',
          home in {p['id'] for p in owned})

    tmp = tempfile.mkdtemp(prefix='placement_')
    outs = []
    for run in range(2):
        path = os.path.join(tmp, f'run{run}.b64')
        subprocess.run([sys.executable, INJECT, GALAXY, '--name', 'Ada',
                        '--placement', 'random', '--seed', '3', '-o', path],
                       check=True, capture_output=True)
        outs.append(sp.load_any(path))
    check('the command line with --seed repeats', outs[0], outs[1])
    want, _ = icv.add_civ(blob, 'Ada', placement=icv.RANDOM,
                          rng=random.Random('3'), log=lambda *a: None)
    check('and is add_civ with that seed', outs[0], want)


def main():
    test_pick()
    test_inject()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
