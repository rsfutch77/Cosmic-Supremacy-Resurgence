"""
test_movement.py: ships follow their routes and scouts pick the next system
===========================================================================
    server\\.venv\\Scripts\\python.exe sim\\tests\\test_movement.py

Runs on `client/SinglePlayerGalaxy.dat`, whose scout (ship 197) the engine
ticked from turn 0: it reached the sun 135 on the tick that ends turn 19, then
set out for the sun 140, the nearest system its civ had not entered, and stood
17.506 units from the sun 135 a turn later.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from sim import load as sl                                      # noqa: E402
from sim import movement as mv                                  # noqa: E402

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def main():
    a, b, length = mv.leg((0, 0, 0), 2.0, (100, 0, 0), 3.0)
    check('a leg stands off each body by 1.02 radii',
          (a, b, round(length, 6)), ((2.04, 0.0, 0.0), (96.94, 0.0, 0.0), 94.9))
    legs = [((0, 0, 0), (10, 0, 0), 10.0), ((10, 0, 0), (10, 5, 0), 5.0)]
    check('progress runs on into the next leg', mv.along(legs, 12.0), (10.0, 2.0, 0.0))

    g = sl.load(open(GALAXY, 'rb').read())
    scout = g.ships[197]
    check('a Colony Ship chassis scout moves 13.5 a turn', mv.speed(g, scout), 13.5)
    check('the scout\'s route is loaded although its has-orders byte is 0',
          (scout.order.kind, len(scout.order.legs)), (mv.SCOUT, 1))
    start = scout.pos
    mv.ships(g)
    check('one turn moves it its speed along the route',
          round(math.dist(start, scout.pos), 3), 15.132)
    for _turn in range(1, 20):
        mv.ships(g)
    check('it reaches the sun 135 on the twentieth tick',
          (scout.orbit, scout.pos), (135, g.suns[135]))
    check('arriving enters the system', 135 in g.civs[scout.owner].explored)
    check('and sets out for the nearest system not entered',
          scout.order.target, g.suns[140])
    mv.ships(g)
    check('a turn later it stands where the engine put it',
          round(math.dist(scout.pos, g.suns[135]), 3), 17.506)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
