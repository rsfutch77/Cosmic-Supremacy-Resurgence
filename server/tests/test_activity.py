"""
test_activity.py: a civ that submits is marked active for the tick
===================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_activity.py

The referee sets the activity flag in each submitting civ's `SERV`, and the
engine refreshes the Inactivity baseline from it at the boundary. The tick
itself needs the game client and is not tested here.

Fixture: `client/SinglePlayerGalaxy.dat` (turn 0, tracked).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools', 'ai_player')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import activity
import referee

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def differing(a, b):
    return [i for i, (x, y) in enumerate(zip(a, b)) if x != y]


def main():
    blob = sp.load_any(GALAXY)
    before = activity.state(blob)

    print('a generated galaxy')
    check('every civ has a SERV with a flag', sorted(before),
          ['BadGuy', 'DemoPlayer'])
    check('no civ is flagged and every baseline is 0',
          set(before.values()), {(0, 0)})

    print('\nthe penalty the tooltip shows')
    check('none in the first 72 turns', activity.penalty(72, 0), 0)
    check('one point on turn 73', activity.penalty(73, 0), 1)
    check('a point a turn after that', activity.penalty(120, 0), 48)
    check('none for a civ seen last turn', activity.penalty(120, 119), 0)
    check('never past 100', activity.penalty(400, 0), 100)

    print('\nmark_present')
    marked = activity.mark_present(blob, ['BadGuy'], log=quiet)
    check('sets that civ\'s flag', activity.state(marked)['BadGuy'], (0, 1))
    check('leaves the other civ', activity.state(marked)['DemoPlayer'],
          before['DemoPlayer'])
    check('moves one byte and no size', (len(marked), len(differing(blob, marked))),
          (len(blob), 1))
    check('and the blob still parses', bool(sp.parse_blob(marked)))
    check('marking again changes nothing',
          activity.mark_present(marked, ['BadGuy'], log=quiet), marked)
    said = []
    check('a civ the galaxy does not hold is left alone',
          activity.mark_present(blob, ['Nobody'], log=said.append), blob)
    check('and named in the log', len(said), 1)

    print('\nreferee.apply_orders')
    out = referee.apply_orders(blob, [('DemoPlayer', blob)], log=quiet)
    check('marks the civ that submitted', activity.state(out)['DemoPlayer'][1], 1)
    check('and not the one that did not', activity.state(out)['BadGuy'][1], 0)
    check('no submissions leaves the blob as served',
          referee.apply_orders(blob, [], log=quiet), blob)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
