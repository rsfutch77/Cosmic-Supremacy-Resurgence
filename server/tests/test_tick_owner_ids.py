"""
test_tick_owner_ids.py: the referee ticks every civ, so every civ is told
=========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_tick_owner_ids.py

The engine skips a civ whose `Owner:4` is 0: no research, no score, and
`Owner::AddNews` (0x543720) drops its news, so an attacker holding 0 loses a
fleet and gets no battle report. `referee.simulate_every_civ` runs before every
tick and gives such a civ the lowest unused id from 20 up. A blob in which every
civ already holds a non-zero id is returned byte for byte.

`client/SinglePlayerGalaxy.dat` is the fixture (DemoPlayer 20, BadGuy 21).
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import inject_civ as icv
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


def ids(blob):
    return {o['name']: struct.unpack_from('<I', blob, o['end'] - 4)[0]
            for o in icv.owner_records(blob)}


def with_id(blob, name, uid):
    out = bytearray(blob)
    o = next(o for o in icv.owner_records(blob) if o['name'] == name)
    struct.pack_into('<I', out, o['end'] - 4, uid)
    return bytes(out)


def main():
    if not os.path.exists(GALAXY):
        print(f'no fixture at {GALAXY}')
        return 1
    base = sp.load_any(GALAXY)
    have = ids(base)
    print(f'{os.path.basename(GALAXY)}: Owner:4 {have}')
    check('the fixture gives every civ a non-zero id', all(have.values()))

    print('\n1. a blob in which every civ holds an id is left alone')
    check('unchanged', referee.simulate_every_civ(base, log=quiet) == base)

    print('\n2. a civ holding 0 takes the lowest unused id from 20 up')
    first, second = list(have)[:2]
    zeroed = with_id(base, first, 0)
    lines = []
    fixed = referee.simulate_every_civ(zeroed, log=lines.append)
    got = ids(fixed)
    check(f'{first} is given an id', got[first] != 0)
    check(f'{first} takes 20 while {second} holds {have[second]}',
          got[first], min(i for i in range(20, 1 << 16) if i != have[second]))
    check(f'{second} keeps its id', got[second], have[second])
    check('only those four bytes change',
          sum(a != b for a, b in zip(fixed, zeroed)) <= 4 and len(fixed) == len(zeroed))
    check('the repair is logged', any(first in m for m in lines))

    print('\n3. two civs holding 0 take distinct ids')
    both = with_id(with_id(base, first, 0), second, 0)
    got = ids(referee.simulate_every_civ(both, log=quiet))
    check('both are non-zero', all(got.values()))
    check('and distinct', len(set(got.values())), len(got))

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
