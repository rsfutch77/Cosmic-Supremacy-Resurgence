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


    # ── a galaxy with nothing left free ──────────────────────────────────────
    # The late state of a permanent sandbox, and the one an abandonment has to
    # be processed in. No blob in the archive has reached it, 0 of 475, which
    # is why this went unnoticed until it was pointed out.
    print('\n10. every planet colonised')
    BASE = sp.load_any(GALAXY)
    full = bytearray(BASE)
    keeper = next(o['oid'] for o in icv.owner_records(BASE) if o['name'] != VICTIM)
    freed = 0
    for q in icv.planet_records(BASE):
        if not q['owner']:
            freed += 1
            struct.pack_into('<I', full, q['payload'] + 16, keeper)
    full = bytes(full)
    check('the fixture really has nothing free',
          sum(1 for q in icv.planet_records(full) if not q['owner']), 0)
    check('and it had some before', freed > 0, True)

    try:
        wipe_civ.wipe(full, VICTIM, log=lambda *a: None)
        check('wiping without a template is refused', False)
    except SystemExit as exc:
        check('wiping without a template is refused', True)
        check('and the refusal names --template-from',
              '--template-from' in str(exc), True)

    out, planets, ships = wipe_civ.wipe(full, VICTIM, log=lambda *a: None,
                                        template_blob=BASE)
    got = {q['id']: q for q in icv.planet_records(out)}
    check('the wiped planets came back unowned',
          all(got[i]['owner'] == 0 for i in planets), True)
    check('and carry a 137-byte blank record',
          {got[i]['plpr_ln'] for i in planets}, {137})
    # 90..105 is galaxy-wide, so a template from this galaxy reproduces it
    # exactly. One from a different galaxy would not, which is why a single
    # canonical record cannot be shipped in the tree as a fallback.
    blanks = [q for q in icv.planet_records(BASE)
              if not q['owner'] and not q['nlen']]
    want = bytes(BASE[blanks[0]['plpr_payload']:blanks[0]['plpr_end']])[90:106]
    check("and the galaxy-wide run is this galaxy's",
          {bytes(out[got[i]['plpr_payload']:got[i]['plpr_end']])[90:106]
           for i in planets}, {want})
    check('and the ships are gone',
          [s for s in ish.ship_records(out) if s['id'] in ships], [])


    # ── the shell keeps its score row ────────────────────────────────────────
    # Deliberate, and documented in wipe_civ's header: the score list keys on
    # Owner:4, the u32 ending the OWNR payload, and a shell wipe never touches
    # OWNR. If that ever stops being true the row disappears silently, which is
    # a behaviour change nobody would see in a passing blob check.
    print('\n11. a wiped civ keeps its OWNR, and so its score row')
    before = sp.load_any(GALAXY)
    after, _p, _s = wipe_civ.wipe(before, VICTIM, log=lambda *a: None)
    ra = {o['name']: o for o in icv.owner_records(before)}
    rb = {o['name']: o for o in icv.owner_records(after)}
    check('every OWNR record survives the wipe', sorted(rb), sorted(ra))
    same = all(bytes(before[ra[n]['off']:ra[n]['end']])
               == bytes(after[rb[n]['off']:rb[n]['end']]) for n in ra)
    check('and every one is byte-identical, the victim included', same, True)
    uid_before = struct.unpack_from('<I', before, ra[VICTIM]['end'] - 4)[0]
    uid_after = struct.unpack_from('<I', after, rb[VICTIM]['end'] - 4)[0]
    check("the victim's Owner:4 is unchanged", uid_after, uid_before)
    others = {struct.unpack_from('<I', after, rb[n]['end'] - 4)[0]
              for n in rb if n != VICTIM}
    check('and still distinct from every survivor', uid_after not in others, True)


    # ── the remembered map is cleared too ────────────────────────────────────
    # EXSY carries a last-known owner and the planet's name as each civ last
    # saw it, and the client draws the hover label and the system's 3D name
    # from there rather than from the live record. Without this a wiped civ
    # keeps its name on a planet that reads as unowned.
    print('\n12. every civ forgets the wiped civ')
    import exsy, inject_design as _idg
    src = sp.load_any(GALAXY)
    victim = next(o for o in icv.owner_records(src) if o['name'] == VICTIM)

    def remembered(blob, oid):
        total = 0
        for o in icv.owner_records(blob):
            off = next((i for i in _idg.find_all(blob, b'EXSY')
                        if o['off'] <= i < o['end']), None)
            if off is None:
                continue
            ln = _idg.sec_len(blob, off)[1]
            for sysrec in exsy.parse(bytes(blob[off + 8:off + 8 + ln])):
                total += sum(1 for q in sysrec['planets'] if q['owner'] == oid)
        return total

    # SinglePlayerGalaxy is fresh, so nobody remembers anything yet and the
    # check below would pass for the wrong reason. The precondition is built
    # rather than borrowed: the galaxy that has it naturally lives in
    # referee_work, which is not tracked.
    def remember(blob, oid):
        o = next(x for x in icv.owner_records(blob) if x['name'] != VICTIM)
        off = next(i for i in _idg.find_all(blob, b'EXSY')
                   if o['off'] <= i < o['end'])
        ln = _idg.sec_len(blob, off)[1]
        table = exsy.parse(bytes(blob[off + 8:off + 8 + ln]))
        if not table or not table[0]['planets']:
            return blob, False
        table[0]['planets'][0]['owner'] = oid
        table[0]['planets'][0]['name'] = b"%s's HQ" % VICTIM.encode()
        return icv.splice(blob, off + 8, off + 8 + ln, exsy.build(table),
                          log=lambda *a: None, what='exsy'), True

    src, planted = remember(src, victim['oid'])
    if not planted:
        print('  [SKIP] no EXSY row to plant a memory in')
    check('some civ remembers the victim owning something',
          remembered(src, victim['oid']) > 0, True)
    gone, _p, _s = wipe_civ.wipe(src, VICTIM, log=lambda *a: None)
    check('and nobody does afterwards', remembered(gone, victim['oid']), 0)
    # the tables must still be walkable, which is what proves the rewrite is
    # a rewrite and not a truncation
    walked = 0
    for o in icv.owner_records(gone):
        off = next((i for i in _idg.find_all(gone, b'EXSY')
                    if o['off'] <= i < o['end']), None)
        if off is None:
            continue
        ln = _idg.sec_len(gone, off)[1]
        exsy.parse(bytes(gone[off + 8:off + 8 + ln]))
        walked += 1
    check('and every table still walks cleanly end to end', walked > 0, True)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
