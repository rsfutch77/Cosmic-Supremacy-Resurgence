"""
test_oracle.py: the oracle scores a simulated tick against an engine tick
=========================================================================
    server\\.venv\\Scripts\\python.exe sim\\tests\\test_oracle.py

Runs on `client/SinglePlayerGalaxy.dat` alone: a galaxy compared with itself
matches in every module, an edit to one field is counted against the module
that owns it, and the simulator's tick leaves a galaxy one turn on.
"""
import copy
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from sim import load as sl                                      # noqa: E402
from sim import oracle                                          # noqa: E402
from sim import tick as st_tick                                 # noqa: E402

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def misses(result):
    return {m: dict(row['misses']) for m, row in result.items() if row['misses']}


def main():
    g = sl.load_path(GALAXY)
    snap = oracle.snapshot(g)
    same = oracle.compare(snap, snap)
    check('a galaxy matches itself', misses(same), {})
    check('every snapshot module is a known one',
          {m for m, _e, _f in snap} <= set(oracle.MODULES), True)

    moved = copy.deepcopy(g)
    ship = next(iter(moved.ships.values()))
    ship.pos = (ship.pos[0] + 5.0, ship.pos[1], ship.pos[2])
    civ = next(iter(moved.civs.values()))
    civ.credits += 7
    check('a moved ship and a credit change are each one miss',
          misses(oracle.compare(oracle.snapshot(moved), snap)),
          {'movement': {'pos': 1}, 'economy': {'credits': 1}})

    nudged = copy.deepcopy(g)
    ship = next(iter(nudged.ships.values()))
    ship.pos = (ship.pos[0] * (1 + oracle.FLOAT_TOLERANCE / 10),
                ship.pos[1], ship.pos[2])
    check('a float inside tolerance matches',
          misses(oracle.compare(oracle.snapshot(nudged), snap)), {})

    gone = copy.deepcopy(g)
    oid = next(iter(gone.ships))
    del gone.ships[oid]
    result = misses(oracle.compare(oracle.snapshot(gone), snap))
    check('a lost ship is missed by combat', result.get('combat'),
          {'exists': 1, 'condition': 1})

    after = st_tick.tick(g)
    check('the tick advances the turn', after.turn, g.turn + 1)
    check('the tick leaves its input alone', g.turn, 0)

    corpus = tempfile.mkdtemp(prefix='sim_oracle_')
    try:
        for side in ('before', 'after'):
            shutil.copy(GALAXY, os.path.join(corpus, f'same.{side}.dat'))
        check('the corpus lists its pair', [p[0] for p in oracle.pairs(corpus)],
              ['same'])
        total = oracle.score(corpus, null=True, log=lambda *_a: None)
        check('a pair of one blob scores every field', misses(total), {})
    finally:
        shutil.rmtree(corpus, ignore_errors=True)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
