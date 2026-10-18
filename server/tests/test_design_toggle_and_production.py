"""
test_design_toggle_and_production.py: deactivating a design, and what a planet builds
=====================================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_design_toggle_and_production.py

A design's last byte (`ShipDesign+0x114`) is its active flag; a submission that
changes only that byte on one of its own designs is carried. A planet's current
production (`PLPR > PROD`) is carried for its owner unless the served item was
hurried, or it names something that is not wealth, a facility, a scan or a
ship.

Uses turn 9 of `server/galaxy_demo` (local).
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

GALAXY = os.path.join(ROOT, 'server', 'galaxy_demo')
TURN = 9
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(*_a):
    pass


def merged(served, civ, sub):
    notes = {}
    out = mo.merge(served, [(civ, sub)], log=quiet, notes=notes)
    return out, notes.get(civ, [])


def with_byte(blob, at, value):
    out = bytearray(blob)
    out[at] = value
    return bytes(out)


def design_end(blob, oid):
    for _civ, _data, designs in mo.design_sections(blob)[0]:
        for d in designs:
            if struct.unpack_from('<I', blob, d.payload)[0] == oid:
                return d.end
    raise KeyError(oid)


def with_prod(blob, planet_oid, edit):
    """`blob` with one planet's PROD rewritten by `edit(bytearray)`."""
    prod = bytearray(mo.planet_index(blob)[planet_oid][1])
    edit(prod)
    return mo.replace_prod(blob, planet_oid, bytes(prod))


def main():
    path = os.path.join(GALAXY, 'turns', f'{TURN:04d}.b64')
    if not os.path.exists(path):
        print(f'no galaxy at {path}')
        return 1
    served = sp.load_any(path)
    designs = mo.design_index(served)
    civs = {o['oid']: o['name'] for o in mo.icv.owner_records(served)}
    mine = next(oid for oid, d in designs.items() if civs[d['civ']] == 'DemoPlayer')
    theirs = next(oid for oid, d in designs.items() if civs[d['civ']] != 'DemoPlayer')

    print('design deactivation')
    sub = with_byte(served, design_end(served, mine) - 1, 1)
    out, notes = merged(served, 'DemoPlayer', sub)
    check('deactivating an own design is carried',
          out[design_end(out, mine) - 1], 1)
    check('with no note', notes, [])
    out2, _ = merged(out, 'DemoPlayer', with_byte(out, design_end(out, mine) - 1, 0))
    check('and reactivating it too', out2[design_end(out2, mine) - 1], 0)
    sub = with_byte(served, design_end(served, theirs) - 1, 1)
    out, notes = merged(served, 'DemoPlayer', sub)
    check("another civ's design is not touched",
          out[design_end(out, theirs) - 1], 0)
    check('and the player is told', any('another civ' in n for n in notes))
    sub = with_byte(served, design_end(served, mine) - 1, 7)
    out, notes = merged(served, 'DemoPlayer', sub)
    check('a value other than 0 or 1 is an edit, refused',
          any('editing a design' in n for n in notes))

    print('what a planet builds')
    planets = mo.planet_index(served)
    owners = {o['name']: o['oid'] for o in mo.icv.owner_records(served)}
    planet = next(p for p, v in sorted(planets.items())
                  if v[0] == owners['DemoPlayer'] and v[1] is not None)

    def to_farm(prod):
        prod[8 + 1] = 1
        prod[8 + 21:8 + 25] = b'FCLT'
        struct.pack_into('<I', prod, 8 + 13, 0)

    sub = with_prod(served, planet, to_farm)
    out, notes = merged(served, 'DemoPlayer', sub)
    check('a facility on an own planet is carried',
          mo.prod_kind(mo.planet_index(out)[planet][1]), b'FCLT')
    check('with no note', notes, [])

    hurried = with_prod(served, planet, lambda p: p.__setitem__(8, 1))
    sub = with_prod(hurried, planet, to_farm)
    sub = with_prod(sub, planet, lambda p: p.__setitem__(8, 1))
    struct_changed = with_prod(sub, planet,
                               lambda p: struct.pack_into('<I', p, 8 + 13, 1))
    out, notes = merged(hurried, 'DemoPlayer', struct_changed)
    check('a hurried item is not swapped',
          mo.planet_index(out)[planet][1], mo.planet_index(hurried)[planet][1])
    check('and the player is told', any('hurried' in n for n in notes))

    sub = with_prod(served, planet,
                    lambda p: p.__setitem__(slice(8 + 21, 8 + 25), b'ZZZZ'))
    out, notes = merged(served, 'DemoPlayer', sub)
    check('a build that is not wealth, a facility, a scan or a ship is refused',
          any("b'ZZZZ'" in n for n in notes))
    check('and the planet keeps what it was building',
          mo.planet_index(out)[planet][1], planets[planet][1])

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
