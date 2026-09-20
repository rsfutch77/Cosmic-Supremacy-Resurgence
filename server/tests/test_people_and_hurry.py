"""
test_people_and_hurry.py , the C3 immediate-effect rules, against a real galaxy
==============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_people_and_hurry.py

Hurry production, conscription, crew assignment, job reallocation and the
recruitment rate all take effect the moment a player clicks them, so a
submission carries the effect rather than the request. The rules that judge
them are `people_acceptable` and `hurry_acceptable`, and neither had a test.

The first case here is the one that matters most, because it is the one that
was wrong in the field. `people_acceptable` asked whether a citizen's three
opaque bytes were ZERO rather than whether they matched what was served, and it
asked it of the citizen array alone. Two consequences, both reproduced below:

  * a civ holding such a record was refused every turn whatever it did,
    including a turn in which it did nothing, and
  * drafting that citizen moved the record into the military array, out of the
    check's sight, so the rule accepted the turn that hid the bytes and refused
    the turn that did not.

`client/SinglePlayerGalaxy.dat` is the fixture because it is the only galaxy
blob a checkout is guaranteed to have: every capture under `server/saves`,
`server/galaxy_*/` and `client/*.dat` is gitignored game state. It carries two
civs with seven citizens and four crew each, and the opaque bytes that provoked
the bug are written into it here rather than waited for, which is what makes
the case reproducible rather than a story about one galaxy.
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

import merge_orders as mo

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
ODD = bytes.fromhex('fe3b00')       # what GoodGuy's citizens carry in the live
                                    # two-sided fixture, and what the check used
                                    # to refuse outright
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def accepted(name, served, submitted, civ):
    check(name, mo.people_acceptable(served, submitted, civ), (True, ''))


def refused(name, served, submitted, civ, saying):
    ok, why = mo.people_acceptable(served, submitted, civ)
    check(name, (ok, saying in why), (False, True))
    if ok or saying not in why:
        print(f'          the reason given was {why!r}')


def a_planet_of(blob, civ):
    """The first planet this civ holds citizens on, and its record array."""
    planets, _ships = mo.population(blob, civ)
    for oid in sorted(planets):
        if planets[oid][0]:
            return oid
    raise SystemExit(f'civ {civ} holds no populated planet')


def set_record_bytes(blob, planet, idx, rest):
    """Write the three opaque bytes of one citizen record, in place."""
    plpr = mo.planet_index(blob)[planet][2]
    arr = bytearray(mo.citizen_bytes(plpr))
    at = idx * mo.POP_RECORD
    arr[at + 1], arr[at + 2], arr[at + 8] = rest
    return mo.set_citizens(blob, planet, bytes(arr))


def conscript(blob, planet, idx):
    """Draft one citizen: out of the citizen array, into the military array.

    The record is carried across with only the job byte rewritten, which is
    what the client's own draft does and what makes a soldier's bytes traceable
    back to the citizen they were.
    """
    plpr = mo.planet_index(blob)[planet][2]
    arr = mo.citizen_bytes(plpr)
    at = idx * mo.POP_RECORD
    rec = arr[at:at + mo.POP_RECORD]
    kept = arr[:at] + arr[at + mo.POP_RECORD:]
    _off, mil = mo.military_of(plpr)
    return mo.set_people(blob, planet, kept, list(mil) + [bytes((3,)) + rec[1:]])


def main():
    served = open(GALAXY, 'rb').read()
    civ, other = [o['oid'] for o in __import__('inject_civ').owner_records(
        served)][:2]
    planet = a_planet_of(served, civ)

    print('a civ is judged on what it changed, not on what it was handed')
    check('the fixture starts clean', mo.people_acceptable(served, served, civ),
          (True, ''))

    # The regression. A galaxy in which a citizen already carries opaque bytes
    # is a galaxy the referee served, so submitting it back unaltered is the
    # emptiest turn there is and must be accepted.
    odd = set_record_bytes(served, planet, 0, ODD)
    accepted('a turn that changes nothing is accepted, opaque bytes and all',
             odd, odd, civ)
    refused('and inventing those bytes within the turn is still refused',
            served, odd, civ, 'opaque bytes in a people record were invented')

    # The other half of the same bug: the checks used to lose sight of a record
    # the moment it was drafted, so hiding the bytes was the accepted move.
    accepted('drafting the citizen holding them is accepted',
             odd, conscript(odd, planet, 0), civ)
    refused('and bytes invented ON a drafted record are refused too',
            served, conscript(set_record_bytes(served, planet, 0, ODD),
                              planet, 0), civ,
            'opaque bytes in a people record were invented')

    print('what a submission may do')
    accepted('a citizen becomes a soldier within one turn',
             served, conscript(served, planet, 0), civ)

    planets, ships = mo.population(served, civ)
    ship = sorted(v for v in ships if ships[v])[0]
    drafted = conscript(served, planet, 0)
    _off, mil = mo.military_of(mo.planet_index(drafted)[planet][2])
    boarded = mo.set_crew(mo.set_people(drafted, planet,
                                        mo.citizen_bytes(
                                            mo.planet_index(drafted)[planet][2]),
                                        []),
                          ship, ships[ship] + mil)
    accepted('and is posted to a ship in the same turn',
             served, boarded, civ)

    print('what it may not do')
    refused('people may not be minted', served,
            mo.set_crew(served, ship, ships[ship] + ships[ship]), civ,
            'people served and')
    refused('soldiers may not be retired', served,
            mo.set_crew(served, ship, ships[ship][:-1]), civ,
            'retiring from service is not carried yet')

    jobs = mo.planet_index(served)[planet][2]
    arr = bytearray(mo.citizen_bytes(jobs))
    arr[0] = 99
    refused('a job id the client does not offer', served,
            mo.set_citizens(served, planet, bytes(arr)), civ,
            'unknown job id(s) [99]')

    arr = bytearray(mo.citizen_bytes(jobs))
    arr[7] = (arr[7] + 7) & 0xFF
    refused('a per-citizen value invented rather than reordered', served,
            mo.set_citizens(served, planet, bytes(arr)), civ,
            'per-citizen values were invented')

    arr = bytearray(mo.citizen_bytes(jobs))
    struct.pack_into('<I', arr, 3, other)
    refused('a citizen handed to another civ', served,
            mo.set_citizens(served, planet, bytes(arr)), civ,
            'somebody changed hands')

    print('hurrying production is priced from the state served')
    # 110 of 200 is the measured case: more than half done, and 4 credits for
    # each of the 90 points left.
    before = mo.set_progress(served, planet, 110)
    after = mo.set_progress(served, planet, 200)
    pb = mo.planet_index(before)[planet][2]
    pa = mo.planet_index(after)[planet][2]
    check('an affordable hurry is priced at 4 credits a point',
          mo.hurry_acceptable(pb, pa, 10065), (True, '', 360, 90))
    ok, why, _c, _p = mo.hurry_acceptable(pb, pa, 100)
    check('a civ that cannot afford the charge is refused',
          (ok, '360 credits needed and 100 served' in why), (False, True))
    ok, why, _c, _p = mo.hurry_acceptable(
        mo.planet_index(mo.set_progress(served, planet, 40))[planet][2],
        pa, 10065)
    check('an item less than half finished is refused',
          (ok, 'the engine requires half' in why), (False, True))
    ok, why, _c, _p = mo.hurry_acceptable(pb, pb, 10065)
    check('a progress field that did not move buys nothing',
          (ok, 'no progress was bought' in why), (False, True))

    print('the recruitment rate is a percentage in one byte')
    rate = mo.recruit_of(mo.planet_index(served)[planet][2])
    check('it reads back as a percentage', rate is not None and 0 <= rate <= 100)
    moved = mo.set_recruit(served, planet, 60)
    check('and a value written comes back',
          mo.recruit_of(mo.planet_index(moved)[planet][2]), 60)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
