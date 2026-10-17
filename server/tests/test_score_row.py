"""
test_score_row.py: a civ injected between turns is on the highscore list
========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_score_row.py

The highscore list is saved in `GLOB` and only a turn boundary rebuilds it,
so `inject_civ.add_civ` writes the newcomer's row itself, carrying the donor's
score as the clone's `OWNR` does. A list with no rows yet (turn 0) is left for
the first boundary.

Fixtures: `client/SinglePlayerGalaxy.dat` (turn 0, tracked) and turn 9 of
`server/galaxy_demo` (local). Loading the result needs the game client and is
not tested here.
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
import inject_civ as icv

TURN_ZERO = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
TURN_NINE = os.path.join(ROOT, 'server', 'galaxy_demo', 'turns', '0009.b64')
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def owner4(blob, name):
    o = next(r for r in icv.owner_records(blob) if r['name'] == name)
    return struct.unpack_from('<I', blob, o['end'] - 4)[0]


def parses(blob):
    try:
        sp.parse_blob(blob)
        return True
    except Exception:                                   # noqa: BLE001
        return False


def main():
    print('turn 0: no rows yet')
    blob = sp.load_any(TURN_ZERO)
    joined, _home = icv.add_civ(blob, 'Ceti', log=quiet)
    check('the list stays empty for the first boundary to fill',
          icv.score_rows(joined)[1], {})
    check('and the blob still parses', parses(joined))

    if not os.path.exists(TURN_NINE):
        print(f'\nno galaxy at {TURN_NINE}; the turn-9 checks need it')
        return 1
    print('\nturn 9: two rows')
    blob = sp.load_any(TURN_NINE)
    before = icv.score_rows(blob)[1]
    donor_tail = before[owner4(blob, 'BadGuy')]
    joined, _home = icv.add_civ(blob, 'Ceti', donor_name='BadGuy', log=quiet)
    after = icv.score_rows(joined)[1]
    uid = owner4(joined, 'Ceti')
    check('the newcomer has a row', uid in after)
    check('one row more than before', len(after), len(before) + 1)
    check("carrying the donor's score", after.get(uid), donor_tail)
    check('the other rows are unchanged',
          {k: v for k, v in after.items() if k != uid}, before)
    check('and the blob still parses', parses(joined))

    again = icv.add_score_row(joined, uid, 'Ceti', donor_tail, log=quiet)
    check('a civ that already has a row is not given a second', again, joined)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
