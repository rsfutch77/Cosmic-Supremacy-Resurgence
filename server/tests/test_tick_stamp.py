"""
test_tick_stamp.py: the referee ticks a galaxy as a civ that holds a planet
============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_tick_stamp.py

The client opens a galaxy only when its local player owns a planet. The local
player of a capture is whichever civ the referee's client last played as, and an
abandonment wipe can leave that civ holding nothing. `referee.stamp_for_a_holder`
restamps such a blob for a civ that still holds a planet and leaves every other
blob byte for byte alone.

`client/SinglePlayerGalaxy.dat` is the fixture, stamped for each of its civs in
turn and then with its local player wiped. That the restamped blob loads is a
client question; it is answered by loading one, not here.
"""
import os
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
import set_blob_player
import wipe_civ
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


def holders(blob):
    return {p['owner'] for p in icv.planet_records(blob)}


def main():
    if not os.path.exists(GALAXY):
        print(f'no fixture at {GALAXY}')
        return 1
    base = sp.load_any(GALAXY)
    names = [o['name'] for o in icv.owner_records(base)]
    print(f'{os.path.basename(GALAXY)}: civs {names}')
    check('the fixture has two civs to choose between', len(names) >= 2)

    print('\n1. a local player that holds a planet is left alone')
    for name in names:
        stamped = set_blob_player.set_player(base, name, log=quiet)
        check(f'stamped for {name}, unchanged',
              referee.stamp_for_a_holder(stamped, log=quiet) == stamped)

    print('\n2. a wiped local player is replaced by a civ that holds a planet')
    victim = names[0]
    stamped = set_blob_player.set_player(base, victim, log=quiet)
    wiped, _planets, _ships = wipe_civ.wipe(stamped, victim, log=quiet)
    oid = {o['name']: o['oid'] for o in icv.owner_records(wiped)}
    check('the wipe leaves the local player holding nothing',
          oid[victim] in holders(wiped), False)
    fixed = referee.stamp_for_a_holder(wiped, log=quiet)
    now = set_blob_player.get_player(fixed)
    check('the new local player holds a planet', now in holders(fixed))
    check('and it is the first holder in OWNR order',
          now, next(o['oid'] for o in icv.owner_records(wiped)
                    if o['oid'] in holders(wiped)))
    check('only the local player field changed',
          sum(a != b for a, b in zip(fixed, wiped)) <= 4
          and len(fixed) == len(wiped))

    print('\n3. a galaxy where nobody holds a planet is refused')
    # wipe() refuses the last civ standing, so the remaining planets are
    # returned to uncolonised directly.
    empty = wiped
    blank = wipe_civ.blank_plpr(empty, log=quiet)
    for p in icv.planet_records(wiped):
        if p['owner'] in oid.values():
            empty = wipe_civ.uncolonise(empty, p['id'], blank, log=quiet)
    check('no civ holds a planet', holders(empty) & set(oid.values()), set())
    try:
        referee.stamp_for_a_holder(empty, log=quiet)
        refused = False
    except SystemExit:
        refused = True
    check('stamp_for_a_holder refuses', refused)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
