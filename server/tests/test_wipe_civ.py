"""
test_wipe_civ.py , what a wipe removes, and what it must leave alone
====================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_wipe_civ.py

`wipe_civ.wipe` takes a civ off the board: its planets go back to uncolonised
and its ships are deleted, while its `OWNR`, the `GLXY` civ count and the
high-water object id at `SAVE+0` stay exactly as they were. The last three are
the point. Deleting from the middle of the contiguous object id space is the
shape of the thing that made every fog projection fail to load (D1), so a wipe
that quietly renumbered or compacted anything would be reintroducing that risk
with no warning.

`client/SinglePlayerGalaxy.dat` is the fixture because it is the only galaxy
blob a checkout is guaranteed to have: every capture under `server/saves`,
`server/galaxy_*/` and `client/*.dat` is gitignored game state. It holds two
civs at turn 0 with two ships each, so wiping one exercises both halves.

What is NOT tested here is that the result loads and ticks, which is what K4
actually asks and which nothing offline can answer. That is
`server/dev_tools/wipe_acceptance.py`, and it needs the game client.
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
import inject_design as idg
import inject_ship as ish
import wipe_civ

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
VICTIM = 'BadGuy'
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def counts(blob):
    """Section counts by tag, from the framing rather than a tag search."""
    out = {}
    for sec in sp.flatten(sp.parse_blob(blob)):
        tag = sec.tag.decode('latin-1')
        out[tag] = out.get(tag, 0) + 1
    return out


def quiet(_msg=''):
    pass


def main():
    if not os.path.exists(GALAXY):
        print(f'no fixture at {GALAXY}')
        return 1
    before = sp.load_any(GALAXY)
    civ = next(o for o in icv.owner_records(before) if o['name'] == VICTIM)
    planets = [p['id'] for p in icv.planet_records(before)
               if p['owner'] == civ['oid']]
    ships = [s['id'] for s in ish.ship_records(before) if s['owner'] == civ['oid']]
    print(f'{os.path.basename(GALAXY)}: {len(before):,} bytes, '
          f'{VICTIM} (id {civ["oid"]}) holds planets {planets} ships {ships}')
    check('the fixture gives the victim something to lose',
          bool(planets) and bool(ships))

    after, got_planets, got_ships = wipe_civ.wipe(before, VICTIM, log=quiet)
    check('wipe reports the planets it took', sorted(got_planets),
          sorted(planets))
    check('wipe reports the ships it took', sorted(got_ships), sorted(ships))

    print('\nthe civ is off the board')
    check('it owns no planet',
          [p['id'] for p in icv.planet_records(after)
           if p['owner'] == civ['oid']], [])
    check('it owns no ship',
          [s['id'] for s in ish.ship_records(after)
           if s['owner'] == civ['oid']], [])
    check('wipe_civ.check agrees',
          wipe_civ.check(after, VICTIM, got_planets, got_ships, log=quiet))

    print('\nthe id space is untouched, which is the D1 risk')
    check('SAVE+0 is unchanged',
          struct.unpack_from('<I', after, idg.HIGH_WATER_ID)[0],
          struct.unpack_from('<I', before, idg.HIGH_WATER_ID)[0])
    check('the GLXY civ count is unchanged',
          struct.unpack_from('<I', after, icv.civ_count_at(after))[0],
          struct.unpack_from('<I', before, icv.civ_count_at(before))[0])
    check('every OWNR is still there',
          [o['name'] for o in icv.owner_records(after)],
          [o['name'] for o in icv.owner_records(before)])
    check('the wiped civ keeps its object id',
          next(o['oid'] for o in icv.owner_records(after)
               if o['name'] == VICTIM), civ['oid'])
    survivors = [s['id'] for s in ish.ship_records(before)
                 if s['owner'] != civ['oid']]
    check('no surviving ship was renumbered',
          [s['id'] for s in ish.ship_records(after)], survivors)
    check('no planet was renumbered',
          [p['id'] for p in icv.planet_records(after)],
          [p['id'] for p in icv.planet_records(before)])

    print('\nexactly the ship sections went')
    cb, ca = counts(before), counts(after)
    for tag in ('SHIP', 'DYNO', 'SHCO', 'SHPR'):
        check(f'{tag} count fell by {len(ships)}',
              ca.get(tag, 0), cb.get(tag, 0) - len(ships))
    for tag in ('OWNR', 'PLNT', 'PLPR', 'SOLA', 'SUN ', 'DSGN'):
        check(f'{tag} count is unchanged', ca.get(tag, 0), cb.get(tag, 0))
    check('the blob got smaller', len(after) < len(before))

    print('\na wiped planet is shaped like one nobody ever colonised')
    template = wipe_civ.pristine_planet(after)
    blank = after[template['plpr_payload']:template['plpr_end']]
    recs = {p['id']: p for p in icv.planet_records(after)}
    natural = [p for p in icv.planet_records(after)
               if not p['owner'] and p['nlen'] == 0 and p['id'] not in planets]
    for pid in planets:
        p = recs[pid]
        check(f'planet {pid} has no owner', p['owner'], 0)
        check(f'planet {pid} has no name', p['nlen'], 0)
        check(f'planet {pid} carries a blank PLPR',
              after[p['plpr_payload']:p['plpr_end']], blank)
        check(f'planet {pid} has no population',
              struct.unpack_from('<I', after, p['plpr_payload'] + 36)[0], 0)
        check(f'planet {pid} is the size of a natural free planet',
              p['ln'], natural[0]['ln'])

    print('\nthe rock keeps what is its own rather than the template\'s')
    was = {p['id']: p for p in icv.planet_records(before)}
    for pid in planets:
        check(f'planet {pid} keeps its position', recs[pid]['pos'],
              was[pid]['pos'])
        check(f'planet {pid} keeps the float at PLNT+20', recs[pid]['f5'],
              was[pid]['f5'])
        check(f'planet {pid} keeps the six bytes behind its name',
              after[recs[pid]['payload'] + 28:recs[pid]['payload'] + 34],
              before[was[pid]['payload'] + 28 + was[pid]['nlen']:
                     was[pid]['payload'] + 34 + was[pid]['nlen']])

    print('\nthe blob still parses as one whole SAVE')
    tree = sp.parse_blob(after)
    check('one top-level section', len(tree), 1)
    check('it covers every byte', tree[0].size + 8, len(after))

    print('\ndeleting the OWNR is guarded rather than offered')
    check('nobody in this fixture names anybody',
          wipe_civ.referenced_by(before, civ['oid'], VICTIM), {})
    dropped, _p, _s = wipe_civ.wipe(before, VICTIM, remove_owner=True,
                                    log=quiet)
    check('with nothing naming it, the OWNR goes',
          [o['name'] for o in icv.owner_records(dropped)],
          [o['name'] for o in icv.owner_records(before) if o['name'] != VICTIM])
    check('and the GLXY civ count follows it down',
          struct.unpack_from('<I', dropped, icv.civ_count_at(dropped))[0],
          struct.unpack_from('<I', before, icv.civ_count_at(before))[0] - 1)
    check('while SAVE+0 still does not move',
          struct.unpack_from('<I', dropped, idg.HIGH_WATER_ID)[0],
          struct.unpack_from('<I', before, idg.HIGH_WATER_ID)[0])

    # The refusal is what matters and the fixture cannot show it on its own:
    # neither civ has met the other at turn 0, which is exactly the easy case
    # the one measured delete-owner run was also in. So the reference is
    # written in, over the surviving civ's own Owner:4, which nothing here
    # loads or reads back.
    other = next(o for o in icv.owner_records(before) if o['name'] != VICTIM)
    planted = bytearray(before)
    struct.pack_into('<I', planted, other['end'] - 4, civ['oid'])
    planted = bytes(planted)
    check('a reference inside another civ is seen',
          sorted(wipe_civ.referenced_by(planted, civ['oid'], VICTIM)),
          [other['name']])
    try:
        wipe_civ.wipe(planted, VICTIM, remove_owner=True, log=quiet)
        check('and it refuses the delete', False)
    except SystemExit:
        check('and it refuses the delete', True)
    forced, _p, _s = wipe_civ.wipe(planted, VICTIM, remove_owner=True,
                                   force_owner=True, log=quiet)
    check('--force-delete-owner overrides it',
          [o['name'] for o in icv.owner_records(forced)], [other['name']])

    print('\nrefusals')
    try:
        wipe_civ.wipe(after, 'NoSuchCiv', log=quiet)
        check('an unknown civ is refused', False)
    except SystemExit:
        check('an unknown civ is refused', True)
    try:
        wipe_civ.wipe(dropped, other['name'], log=quiet)
        check('wiping the last civ standing is refused', False)
    except SystemExit:
        check('wiping the last civ standing is refused', True)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
