"""
test_starting_kit.py: a joiner starts with what everybody else started with
============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_starting_kit.py

`inject_civ.add_civ` clones seat one, which in a running galaxy is a developed
empire, then resets the clone to the starting kit. The reference for the kit is
a civ `make_multiplayer_galaxy.build` adds at creation.

The donor is developed first (more designs, population, bank, research topic,
recruitment rate and production points), and each kit field is compared with
the generated civ and with the same injection run with `kit=False`.

The fixture is `client/SinglePlayerGalaxy.dat`, as for `test_wipe_civ` and
`test_inject_donor`. Loading and playing a joined galaxy is
`server/dev_tools/join_kit_acceptance.py`, which needs the client.
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
import inject_ship as ish
import merge_orders as mo
import make_multiplayer_galaxy as mmg
import joins

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


def civ(blob, name):
    return next(o for o in icv.owner_records(blob) if o['name'] == name)


def homeworld(blob, name):
    """The planet a civ's kit lives on, chosen the way `add_civ` chooses one."""
    oid = civ(blob, name)['oid']
    mine = [p for p in icv.planet_records(blob) if p['owner'] == oid]
    return max(mine, key=lambda p: p['plpr_ln']) if mine else None


def kit_of(blob, name):
    """Every field of the starting kit this project can read, for one civ."""
    o = civ(blob, name)
    hq = homeworld(blob, name)
    plpr = bytes(blob[hq['plpr_payload']:hq['plpr_end']]) if hq else b''
    ships = [s for s in ish.ship_records(blob) if s['owner'] == o['oid']]
    designs = [(oid, nm) for off, oid, nm in idg.design_records(blob)
               if o['off'] < off < o['end']]
    return {
        'planets': len([p for p in icv.planet_records(blob)
                        if p['owner'] == o['oid']]),
        'plpr_len': len(plpr),
        'jobs': [c[0] for c in (mo.citizens_of(plpr) or [])],
        'military': len(mo.military_of(plpr)[1] or []),
        'rates': (plpr[4], plpr[11]),
        'recruit': mo.recruit_of(plpr),
        'progress': mo.progress_of(plpr),
        'food': mo.food_of(plpr),
        'facilities': mo.facilities_of(plpr),
        'settled': settled_masked(blob, hq['id']),
        'prod': icv.planet_prod(blob, hq['id']),
        'credits': mo.credits_of(blob, o['oid']),
        'research': mo.research_of(blob, o['oid']),
        'designs': len(designs),
        'design_names': sorted(nm for _oid, nm in designs),
        'ships': len(ships),
        'hulls': sorted(masked_ship(blob, s, o['oid']) for s in ships),
    }


def settled_masked(blob, planet_id):
    """The settled block without its owner, which is the civ's own id."""
    got = mo.settled_of(blob, planet_id)
    if got is None:
        return None
    got = dict(got)
    got.pop('owner')
    return got


def masked_plpr(blob, name):
    """A civ's homeworld PLPR with its owner id and rate pair blanked.

    Those are the only differences a joiner may have from a generated civ.
    """
    o = civ(blob, name)
    hq = homeworld(blob, name)
    rec = bytearray(blob[hq['plpr_payload']:hq['plpr_end']])
    icv.replace_u32(rec, o['oid'], 0, 0, len(rec))
    for off in mmg.RATE_OFFSETS:
        rec[off] = 0
    return bytes(rec)


def masked_ship(blob, ship, owner_oid):
    """A ship record with its identity blanked, leaving the hull.

    Blanked: the object id (carried twice), owner, position, orbit and design.
    What is left is the section shape, the `DYNO` and the crew.
    """
    rec = bytearray(blob[ship['off']:ship['end']])
    icv.replace_u32(rec, owner_oid, 0, 0, len(rec))
    struct.pack_into('<I', rec, 8, 0)                             # object id
    struct.pack_into('<fff', rec, 12, 0.0, 0.0, 0.0)              # position
    struct.pack_into('<I', rec, ship['dyno'] - ship['off'] + 8, 0)   # orbit
    struct.pack_into('<I', rec, ship['shpr'] - ship['off'] + 8, 0)   # design
    struct.pack_into('<I', rec, len(rec) - 4, 0)          # the id again
    return bytes(rec)


def holdings(blob):
    """{civ: (planets, ships, OWNR bytes)}, what a join must not move."""
    out = {}
    for o in icv.owner_records(blob):
        out[o['name']] = (
            sorted(p['id'] for p in icv.planet_records(blob)
                   if p['owner'] == o['oid']),
            sorted(s['id'] for s in ish.ship_records(blob)
                   if s['owner'] == o['oid']),
            bytes(blob[o['payload']:o['end']]))
    return out


def exsy_of(blob, name):
    import exsy
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    at = {r['off']: r['name'] for r in icv.owner_records(blob)}
    for ownr in glxy.find('OWNR'):
        if at.get(ownr.start) != name:
            continue
        sec = next(ownr.find('EXSY'), None)
        if sec is None:
            return None
        return exsy.systems_known(bytes(blob[sec.payload:sec.end]))
    return None


def develop(blob, name, log=quiet):
    """Make seat one look like a civ eighty turns in.

    Every field the kit resets is moved off its starting value, using the
    writers `merge_orders` uses on live blobs.
    """
    o = civ(blob, name)
    hq = homeworld(blob, name)

    # a bigger population, and soldiers stationed on the rock
    citizen = bytes((0, 0, 0)) + struct.pack('<I', o['oid']) + b'\x00\x00'
    soldier = bytes((mo.MILITARY_JOB, 0, 0)) + struct.pack('<I', o['oid']) \
        + b'\x00\x00'
    blob = mo.set_people(blob, hq['id'], citizen * 19, [soldier] * 3)

    # a facility built on the capital, and a settled block with a population
    # history and both undecoded counters moved
    blob = mo.set_facilities(blob, hq['id'],
                             [(0, 4)] + list(icv.STARTING_FACILITIES))
    blob = mo.set_settled(blob, hq['id'], pop=19,
                          history=[0x1012, 0x1012, 0x1011, 0x1010, 0x100f,
                                   0x100e, 0x100d],
                          counter=6, last=7, age=5.0, founded=3)

    blob = mo.set_recruit(blob, hq['id'], 20)
    blob = mo.set_progress(blob, hq['id'], 97)
    blob = mo.set_credits(blob, o['oid'], 10820)
    blob = mo.set_research(blob, o['oid'],
                           (struct.pack('<I', 3), struct.pack('<I', 18)))

    # designs the donor has researched since, taken from the other civ in the
    # fixture so the records are ones the engine wrote
    other = next(x for x in icv.owner_records(blob) if x['name'] != name)
    theirs = [d for oid, d in sorted(mo.design_index(blob).items())
              if d['civ'] == other['oid']][1:]
    next_id = idg.max_object_id(blob)
    for d in theirs:
        next_id += 1
        rec = mo.with_design_id(d['record'], next_id)
        rec = bytearray(rec)
        icv.replace_u32(rec, other['oid'], civ(blob, name)['oid'], 0, len(rec))
        blob = mo.add_design(blob, civ(blob, name)['oid'], bytes(rec))
    blob = mo.set_high_water(blob, next_id)
    log(f'  donor developed: {len(theirs) + 1} designs, 19 citizens')
    return blob


def generated_reference(fixture, players):
    """A galaxy `make_multiplayer_galaxy` built: the reference starting kit."""
    return mmg.build(fixture, players, log=quiet)


def main():
    fixture = sp.load_any(GALAXY)
    print(f'{os.path.basename(GALAXY)}: {len(fixture):,} bytes, '
          f'{len(icv.owner_records(fixture))} civ(s)')

    print('\nthe reference: a civ make_multiplayer_galaxy adds at generation')
    fresh = generated_reference(fixture, ['Alpha', 'Beta', 'Gamma'])
    ref = kit_of(fresh, 'Gamma')
    check('the added civ owns one planet', ref['planets'], 1)
    check('with the starting population',
          ref['jobs'], list(icv.STARTING_JOBS))
    check('and the starting fleet', ref['ships'], icv.STARTING_SHIPS)
    check('on one design', ref['designs'], 1)
    check('which is seat one\'s starting design',
          ref['design_names'], kit_of(fresh, 'Alpha')['design_names'])
    check('every hull the same as seat one\'s',
          ref['hulls'], kit_of(fresh, 'Alpha')['hulls'])

    print('\na donor eighty turns in')
    played = develop(fixture, 'DemoPlayer')
    sp.parse_blob(played)
    donor = kit_of(played, 'DemoPlayer')
    check('the fixture\'s seat one is no longer at its start',
          [donor['jobs'] == list(icv.STARTING_JOBS),
           donor['credits'] == icv.STARTING_CREDITS,
           donor['designs'] == 1,
           donor['recruit'] == icv.STARTING_RECRUIT,
           donor['progress'] == icv.STARTING_PROGRESS,
           donor['military'] == 0,
           donor['facilities'] == list(icv.STARTING_FACILITIES),
           donor['settled'] == ref['settled']],
          [False] * 8)
    check('and its research topic is set',
          donor['research'] != (icv.RESEARCH_UNSET, icv.RESEARCH_UNSET))

    before = holdings(played)
    joined, planet = icv.add_civ(played, 'Newcomer', log=quiet)
    sp.parse_blob(joined)
    got = kit_of(joined, 'Newcomer')

    print('\nfield by field against the civ generation would have made')
    for field in ('planets', 'plpr_len', 'jobs', 'military', 'recruit',
                  'progress', 'food', 'facilities', 'settled', 'prod',
                  'credits', 'research', 'designs', 'ships', 'hulls'):
        check(f'{field} matches the generated civ', got[field], ref[field])
    # Catches any byte the fields above do not name.
    check('the homeworld PLPR is the generated one, byte for byte, but for '
          'the owner id and the rate pair',
          masked_plpr(joined, 'Newcomer'), masked_plpr(fresh, 'Gamma'))
    check('the design is seat one\'s, renamed by nothing',
          got['design_names'], kit_of(played, 'DemoPlayer')['design_names'][:1])
    check('the rate pair is seat one\'s, which is the one field that is '
          'copied from the donor on purpose',
          got['rates'], kit_of(played, 'DemoPlayer')['rates'])

    print('\nthe same injection with the kit off, which is what it used to do')
    bare, _p = icv.add_civ(played, 'Newcomer', kit=False, log=quiet)
    raw = kit_of(bare, 'Newcomer')
    check('inherits the donor\'s population', raw['jobs'], donor['jobs'])
    check('inherits the donor\'s stationed military',
          raw['military'], donor['military'])
    check('inherits the donor\'s design book', raw['designs'], donor['designs'])
    check('inherits the donor\'s bank', raw['credits'], donor['credits'])
    check('inherits the donor\'s research topic',
          raw['research'], donor['research'])
    check('inherits the donor\'s recruitment rate',
          raw['recruit'], donor['recruit'])
    check('inherits the donor\'s production points',
          raw['progress'], donor['progress'])
    check('inherits the donor\'s facilities',
          raw['facilities'], donor['facilities'])
    check('inherits the donor\'s settled block',
          raw['settled'], donor['settled'])
    check('and has no ships at all', raw['ships'], 0)

    print('\nnobody who was already in the galaxy moved')
    after = holdings(joined)
    for name, (planets, ships, ownr) in before.items():
        check(f'{name} kept exactly its planets', after[name][0], planets)
        check(f'{name} kept exactly its ships', after[name][1], ships)
        check(f'{name}\'s OWNR is byte-identical', after[name][2], ownr)
    check('the newcomer\'s world was free before',
          planet in [p['id'] for p in icv.planet_records(played)
                     if not p['owner'] and p['nlen'] == 0])

    print('\nthe counters the engine reads')
    check('the GLXY civ count matches the OWNR records',
          struct.unpack_from('<I', joined, icv.civ_count_at(joined))[0],
          len(icv.owner_records(joined)))
    check('the high-water id matches the largest object id',
          struct.unpack_from('<I', joined, idg.HIGH_WATER_ID)[0],
          idg.max_object_id(joined))
    check('every object id is distinct',
          len({s['id'] for s in ish.ship_records(joined)}
              | {o['oid'] for o in icv.owner_records(joined)}
              | {oid for _o, oid, _n in idg.design_records(joined)}),
          len(ish.ship_records(joined)) + len(icv.owner_records(joined))
          + len(idg.design_records(joined)))
    check('every ship names itself in both places it carries an id',
          [s['id'] for s in ish.ship_records(joined)
           if struct.unpack_from('<I', joined, s['end'] - 4)[0] != s['id']],
          [])
    check('the hulls fly the design the newcomer owns',
          {s['design'] for s in ish.ship_records(joined)
           if s['owner'] == civ(joined, 'Newcomer')['oid']},
          {oid for off, oid, _n in idg.design_records(joined)
           if civ(joined, 'Newcomer')['off'] < off
           < civ(joined, 'Newcomer')['end']})
    check('and no ship flies a design that was dropped',
          [s['id'] for s in ish.ship_records(joined)
           if s['design'] not in {oid for _o, oid, _n
                                  in idg.design_records(joined)}], [])
    check('no production queue names a design the galaxy does not hold',
          [p['id'] for p in icv.planet_records(joined)
           if (mo.prod_builds(icv.planet_prod(joined, p['id']) or b'')
               not in (None, *{oid for _o, oid, _n
                               in idg.design_records(joined)}))], [])

    print('\nthe explored map, which the kit must not put back')
    check('the donor still knows something', bool(exsy_of(played, 'DemoPlayer')))
    check('the clone inherits it, as it always did',
          exsy_of(joined, 'Newcomer'), exsy_of(played, 'DemoPlayer'))
    forgotten = joins.forget_inherited_map(joined, 'Newcomer', log=quiet)
    check('and joins.forget_inherited_map still empties it',
          exsy_of(forgotten, 'Newcomer'), [])
    check('while every incumbent keeps theirs',
          [exsy_of(forgotten, n) for n in before],
          [exsy_of(played, n) for n in before])

    print('\nthe pieces on their own')
    check('empty_prod finds this galaxy\'s never-used queue',
          icv.empty_prod(played),
          icv.planet_prod(fixture, homeworld(fixture, 'DemoPlayer')['id']))
    one = bytes(played[civ(played, 'BadGuy')['off']:
                       civ(played, 'BadGuy')['end']])
    check('keep_starting_design leaves a record it cannot read a count for '
          'alone', icv.keep_starting_design(bytearray(b'DSGN'), log=quiet),
          b'DSGN')
    kept = icv.keep_starting_design(bytearray(one), log=quiet)
    check('and keeps the lowest design id when it can',
          [oid for _o, oid, _n in idg.design_records(kept)],
          [min(oid for _o, oid, _n in idg.design_records(one))])
    check('shrinking the record by the designs it dropped',
          len(kept) < len(one))
    check('and leaving something that still parses as sections',
          [t for t in (idg.sec_len(kept, 0),)][0][1], len(kept) - 8)

    print('\na galaxy where every ship left is already under orders')
    # The engine deletes SHIP sections itself and renumbers nothing, so
    # dropping the idle ships gives a real shape: no idle hull to clone.
    crowded = played
    for s in sorted(ish.ship_records(crowded), key=lambda s: -s['off']):
        if not s['ordered']:
            crowded = bytes(icv.remove_section(bytearray(crowded), s['off']))
    sp.parse_blob(crowded)
    check('every ship left in the fixture carries an order',
          [s['id'] for s in ish.ship_records(crowded) if not s['ordered']], [])
    pressed, _p = icv.add_civ(crowded, 'Pressed', log=quiet)
    sp.parse_blob(pressed)
    check('the newcomer still gets the starting fleet',
          kit_of(pressed, 'Pressed')['ships'], icv.STARTING_SHIPS)
    check('and every hull of it is idle',
          [s['id'] for s in ish.ship_records(pressed)
           if s['owner'] == civ(pressed, 'Pressed')['oid'] and s['ordered']],
          [])
    check('a hull cloned off an ordered donor is the generated hull again',
          kit_of(pressed, 'Pressed')['hulls'], ref['hulls'])

    print('\nan explicit hull count, which is what generation passes')
    three, _p = icv.add_civ(played, 'Trio', ships=3, log=quiet)
    check('three hulls when three are asked for',
          kit_of(three, 'Trio')['ships'], 3)
    none, _p = icv.add_civ(played, 'Solo', ships=0, log=quiet)
    check('and none when none are',
          kit_of(none, 'Solo')['ships'], 0)
    check('the levelling still happened without hulls',
          kit_of(none, 'Solo')['credits'], icv.STARTING_CREDITS)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for name in FAIL:
        print(f'  failed: {name}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
