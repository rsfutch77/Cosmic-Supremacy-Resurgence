"""
test_fleet_and_queue.py: fleets are carried, production queues refused, nothing else is lost
=========================================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_fleet_and_queue.py

Fleets are carried; `test_fleets_merge.py` covers each step and refusal.
Production queues are not: a planet that joins a queue keeps what it was
building, with a note.

Fixtures in `fixtures/tier4`, saved in one Single Player session at turn 0:
`t4e` builds a Colony Ship, `t4f` adds it to a production queue, `t4g` forms
the two Colony Ships into a fleet, `t4i` gives the fleet a move order, `t4j`
takes one ship out, `t4k` disbands it.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import merge_orders as mo

FIX = os.path.join(HERE, 'fixtures', 'tier4')
CIV = 'DemoPlayer'
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def load(step):
    return sp.load_any(os.path.join(FIX, f't4{step}.dat'))


def merged(served, sub):
    notes = {}
    out = mo.merge(served, [(CIV, sub)], log=lambda *a: None, notes=notes)
    return out, notes.get(CIV, [])


def fleets(blob):
    glxy = next(sp.parse_blob(blob)[0].find('GLXY'))
    return [c for c in glxy.children if c.tag == b'FLET']


def main():
    f, g, i, j = load('f'), load('g'), load('i'), load('j')

    print('the parser knows fleets')
    fl = fleets(g)
    check('t4g holds one fleet', len(fl), 1)
    fldt = next(fl[0].find('FLDT'))
    check('with its two members inside FLDT', len(list(fldt.find('SHPR'))), 2)
    check("and its members are no longer SHIPs", {192, 193} & set(mo.ship_index(g)),
          set())

    print('a fleet formed in a turn')
    out, notes = merged(f, g)
    check('the fleet is carried', [mo.fleet_index(out)[204]['record']],
          [mo.fleet_index(g)[204]['record']])
    check('nothing is refused because the ships moved into a fleet', notes, [])

    print("a fleet's move order")
    out, notes = merged(f, i)
    check('is carried in the fleet', mo.fleet_index(out)[204]['dyno'],
          mo.fleet_index(i)[204]['dyno'])

    print('a ship that left a fleet within the turn')
    out, notes = merged(f, j)
    check('is a single ship and the other stays in the fleet',
          (193 in mo.ship_index(out),
           [m for m, _s, _v in mo.fleet_index(out)[204]['members']]),
          (True, [192]))

    print('a production queue')
    e = load('e')
    planet = next(p for p, v in mo.planet_index(f).items()
                  if mo.prod_queue(v[1]) not in (None, bytes(7)))
    out, notes = merged(e, f)
    check('a planet joining a queue keeps what it was building',
          mo.planet_index(out)[planet][1], mo.planet_index(e)[planet][1])
    check('and the player is told', any('production queue' in n for n in notes))

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
