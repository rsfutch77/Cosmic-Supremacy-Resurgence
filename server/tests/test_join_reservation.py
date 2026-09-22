"""
test_join_reservation.py , a join keeps off a planet a ship is flying to
========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_join_reservation.py

`inject_civ.pick_homeworld` takes the uncolonised planet furthest from
everything claimed. A planet nobody owns is still claimed when a ship is under
orders to fly there, and on the galaxies in this repository those two never
disagree by accident: the maximin pick lands hundreds of units from anything
anyone is doing. So a test that injects a civ and finds it did not take a
colonisation in flight has proved nothing unless the collision was arranged
first.

That is what this does. It reads which planet the pick returns with nothing
reserved, orders a colony ship to that exact planet, and then injects. The
mutation check is the other half: calling the picker the way it was called
before reservations existed, with an empty `taken`, takes the contested planet
every time, so the passing assertion is not vacuous.

The repeated-join arithmetic is here as well, because it costs nothing and is
the half of K2 a client cannot make more true: twelve joins in a row on one
blob, with the object ids, the `GLXY` civ count and the high-water id checked
after each one. What a client is needed for, and what
`server/dev_tools/join_acceptance.py` does instead, is that such a galaxy then
loads, plays and ticks.

`client/SinglePlayerGalaxy.dat` is the fixture for the reason `test_wipe_civ.py`
and `test_inject_donor.py` use it: it is tracked. The galaxies that carry
colonisations in flight of their own are gitignored captures, and a test cannot
depend on one.
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
import inject_design as idg
import inject_order as ior
import wipe_acceptance as wa

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
COLONIST = 'DemoPlayer'
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def routs(blob):
    """Every ship's raw `ROUT` bytes, by ship object id."""
    out = {}
    tree = sp.parse_blob(blob)
    for sid, _owner, _pos, _sh, dyno in ior.ship_sections(blob, tree):
        if dyno is None:
            continue
        rout = next((c for c in dyno.children if c.tag == icv.ROUT), None)
        if rout is not None:
            out[sid] = bytes(blob[rout.start:rout.end])
    return out


def oid_of(blob, name):
    rec = next((o for o in icv.owner_records(blob) if o['name'] == name), None)
    return rec['oid'] if rec else None


def civ_count(blob):
    return struct.unpack_from('<I', blob, icv.civ_count_at(blob))[0]


def high_water(blob):
    return struct.unpack_from('<I', blob, idg.HIGH_WATER_ID)[0]


def main():
    blob = sp.load_any(GALAXY)
    print(f'{os.path.basename(GALAXY)}: {len(blob):,} bytes, '
          f'{len(icv.owner_records(blob))} civ(s)')

    # ── the collision, arranged ──────────────────────────────────────────────
    print('\na colony ship is sent to the planet the picker wants')
    contested = icv.pick_homeworld(icv.planet_records(blob),
                                   icv.reserved_planets(blob), margin=0.0)
    staged, ship = wa.stage_colonisation(blob, COLONIST, contested['id'],
                                         log=quiet)
    reserved = icv.reserved_planets(staged)
    check('the order reserves the contested planet',
          contested['id'] in reserved)
    check('the order is read as naming one planet, not a whole system',
          [d['kind'] for d in icv.ship_destinations(staged)
           if d['ship'] == ship], ['planet'])

    # The pick is re-taken on the staged blob rather than reused from above:
    # adding a ship moves offsets, and the point is what the picker says about
    # the blob that is actually injected into.
    unreserved = icv.pick_homeworld(icv.planet_records(staged), set(),
                                    margin=0.0)
    check('with nothing reserved the picker takes the contested planet',
          unreserved['id'], contested['id'])

    before = routs(staged)
    joined, home_id = icv.add_civ(staged, 'Ceti', log=quiet)
    check('the newcomer is not put on the contested planet',
          home_id != contested['id'])
    check('the newcomer is not put on any reserved planet',
          home_id not in reserved)
    check('the newcomer owns the planet it was given',
          next(p['owner'] for p in icv.planet_records(joined)
               if p['id'] == home_id), oid_of(joined, 'Ceti'))

    # ── the orders in flight ─────────────────────────────────────────────────
    print('\nevery order in flight survives the join byte for byte')
    after = routs(joined)
    check('the same ships carry a ROUT', sorted(after), sorted(before))
    for sid in sorted(before):
        check(f'ship {sid} keeps its ROUT unchanged', after.get(sid),
              before[sid])

    # ── twelve joins on one blob ─────────────────────────────────────────────
    print('\ntwelve joins in a row')
    grown = staged
    base_civs = len(icv.owner_records(grown))
    oids, homes = [], []
    for i in range(12):
        grown, home = icv.add_civ(grown, f'Join{i + 1:02d}', log=quiet)
        oids.append(oid_of(grown, f'Join{i + 1:02d}'))
        homes.append(home)
    check('object ids grow monotonically', oids, sorted(set(oids)))
    check('no two newcomers share a homeworld', len(set(homes)), len(homes))
    check('no newcomer lands on a reserved planet',
          [h for h in homes if h in reserved], [])
    check('the GLXY civ count matches the OWNR records',
          civ_count(grown), len(icv.owner_records(grown)))
    check('twelve civs were added', len(icv.owner_records(grown)),
          base_civs + 12)
    check('the high-water id covers every id in use',
          high_water(grown) >= idg.max_object_id(grown))
    check('every newcomer holds a distinct Owner:4',
          len({struct.unpack_from('<I', grown, o['end'] - 4)[0]
               for o in icv.owner_records(grown)}),
          len(icv.owner_records(grown)))
    check('the result still reparses section by section',
          bool(sp.parse_blob(grown)))

    # The donor cannot drift onto a civ the run itself added, which is what
    # makes twelve joins twelve clones of one seat rather than a chain.
    seat = icv.seat_one(icv.owner_records(grown))['name']
    smallest = min(icv.owner_records(grown), key=lambda o: o['ln'])['name']
    check('seat one is still the civ it was before the twelve joins',
          seat, icv.seat_one(icv.owner_records(staged))['name'])
    check('the rule the default replaced would have drifted onto a newcomer',
          smallest.startswith('Join'))

    # And the reservation is what keeps those twelve off the contested planet.
    # With the destinations hidden, the same twelve joins take it.
    print('\nthe same twelve joins with reservations suppressed')
    real = icv.ship_destinations
    try:
        icv.ship_destinations = lambda blob, tree=None: []
        blind, blind_homes = staged, []
        for i in range(12):
            blind, home = icv.add_civ(blind, f'Blind{i + 1:02d}', log=quiet)
            blind_homes.append(home)
    finally:
        icv.ship_destinations = real
    check('one of them takes the planet the colony ship is flying to',
          contested['id'] in blind_homes)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for f in FAIL:
        print(f'  FAILED: {f}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
