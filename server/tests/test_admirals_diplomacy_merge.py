"""
test_admirals_diplomacy_merge.py: admirals and diplomacy are carried
====================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_admirals_diplomacy_merge.py

The merge carries a civ's own admirals, their assignment to its ships, and
every diplomacy step the client was seen to take, applying what a step does
to both civs at once: an accepted treaty's lump sums, the treaties a
declaration of war or a stance treaty ends, and both stances.

The records built here follow the client's writers (ADML 0x5A51B0, ADDT
0x5A4F20, TRTY 0x55A4D0, TRDT 0x559890); the fixtures are the governor, tier4
and tier5 sessions. `fixtures/admirals` is a session in the client on
`clean.dat` (27 October 2026): a1 created and assigned to the colony ship (a2),
b1 assigned (a3), a rule added and one unticked (a4), renamed (a5), Scout
created (a6), b1 moved to Scout (a7), b1 given a manual move (a8), b1 set to
none (a9), Scout deleted (a10), one turn advanced (a13). Turn 111 ticked on its
own between a2 and a3.

`fixtures/diplomacy` is a session in the client on `control_fork_played.dat`
with waronly off (27 and 28 October 2026). GoodGuy proposes an alliance (d1)
and withdraws it (d2), proposes a trade (d3); BadGuy declines it (d4); GoodGuy
proposes another (d5p); BadGuy accepts it (d5), deletes the declined one (d6),
declares war (d7); a turn passes (d8); BadGuy proposes a cease-fire (d9);
GoodGuy accepts it (d9a). d3b, d4g, d5b and d9g are the step before, reloaded
as the other civ.
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

import save_parser as sp                                        # noqa: E402
import inject_order as io                                       # noqa: E402
import merge_orders as mo                                       # noqa: E402

FIX = os.path.join(HERE, 'fixtures')
PASS, FAIL = [], []

ME, THEM = 199, 203                     # governors: DemoPlayer, BadGuy
GOOD, BAD = 656, 660                    # tier5 q and the client sessions
GROUPS = {
    'governors': ['g_base', 'g_made', 'g_assigned', 'g_switched',
                  'g_reassigned', 'g_deleted'],
    'tier4': ['t4e', 't4f', 't4g', 't4i', 't4j', 't4k'],
    'tier5': ['f0c', 'f1', 'f2', 'f3', 'f4', 'f5', 'f6'],
}


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def load(name):
    folder = 'governors' if name.startswith('g_') else (
        'tier4' if name.startswith('t4') else 'tier5')
    return sp.load_any(os.path.join(FIX, folder, name + '.dat'))


def adm(name):
    return sp.load_any(os.path.join(FIX, 'admirals', name + '.b64'))


def dip(name):
    return sp.load_any(os.path.join(FIX, 'diplomacy', name + '.b64'))


def merged(served, *subs):
    notes = {}
    out = mo.merge(served, list(subs), log=quiet, notes=notes)
    return out, notes


def adml(oid, name, kind=2, ships=0, rules=((b'TSCQ', 0, b'\x05\0\0\0'),)):
    """A whole ADML section as the writer lays one out. Each rule is (tag,
    version, payload) and is preceded by its checked RULE."""
    body = struct.pack('<I', len(name)) + name + struct.pack('<I', kind)
    body += struct.pack('<I', len(rules))
    for tag, version, payload in rules:
        body += sp.build_section(b'RULE', 0, b'\x01')
        body += sp.build_section(tag, version, payload)
    return sp.build_section(b'ADML', 0, struct.pack('<II', oid, ships)
                            + b'\x00' + sp.build_section(b'ADDT', 0, body))


def with_admirals(blob, civ, records):
    """`blob` with the civ's ADMS holding these whole ADML sections."""
    return mo.set_admirals(blob, civ, records)


def made(blob, civ, records, high):
    return mo.set_high_water(with_admirals(blob, civ, records), high)


def trdt(record):
    """(proposer, recipient, kind, status, length, start turn, turn proposed,
    deleted by proposer, deleted by recipient) of a whole TRTY section."""
    at = 8 + mo.TRTY_PARTIES_AT
    return struct.unpack_from('<IIIIiiI', record, at) + tuple(
        record[at + 28:at + 30])


def trty(oid, first, second, turn, kind=3, mine=(), theirs=(), war=False):
    """A whole TRTY section as the writer lays out a proposal made on `turn`,
    or with `war` a declaration of war."""
    fixed = ((first, second, 1, 3, -1, turn, turn) if war
             else (first, second, kind, 0, -2, -1, turn))
    body = struct.pack('<IIIIiiI', *fixed) + b'\0\0'
    for items in (mine, theirs):
        body += struct.pack('<I', len(items)) + b''.join(items)
    return sp.build_section(b'TRTY', 2, struct.pack('<I', oid)
                            + sp.build_section(b'TRDT', 1, body))


def with_treaties(blob, records, count=None):
    """`blob` with the list after SAVE's last section holding these TRTYs."""
    tree = sp.parse_blob(blob)
    save = tree[0]
    head = bytes(blob[save.payload:save.children[-1].end])
    tail = struct.pack('<I', len(records) if count is None else count)
    return sp.replace_payload(blob, tree, save,
                              head + tail + b''.join(records))


def proposing(blob, record):
    """`blob` with this treaty added and SAVE+0 raised to its id."""
    oid = struct.unpack_from('<I', record, 8)[0]
    return mo.set_high_water(with_treaties(
        blob, list(mo.treaties(blob).values()) + [record]), oid)


def without(blob, oid):
    return with_treaties(blob, [r for t, r in mo.treaties(blob).items()
                                if t != oid])


def both_stances(blob, a, b, code):
    return mo.set_stance(mo.set_stance(blob, a, b, code), b, a, code)


def with_ship_admiral(blob, ship, admiral):
    dyno = mo.ship_index(blob)[ship][1]
    return mo.replace_dyno(blob, ship, mo.with_dyno_admiral(dyno, admiral))


def with_fleet_admiral(blob, fleet, admiral):
    f = mo.fleet_index(blob)[fleet]
    out = bytearray(blob)
    at = f['sec'].payload + mo.OBJECT_HEADER + mo.dyno_admiral_at(f['dyno'])
    struct.pack_into('<I', out, at, admiral)
    return bytes(out)


def ship_admiral(blob, ship):
    return mo.dyno_admiral(mo.ship_index(blob)[ship][1])


def ships_of(blob, civ):
    """{admiral id: ships-commanded count} in a civ's ADMS."""
    return {oid: struct.unpack_from('<I', r, 8 + mo.ADML_SHIPS_AT)[0]
            for oid, r in mo.admirals_of(blob, civ)}


def ordered(blob, ship):
    """`blob` with the ship given a move order, as inject_order writes one."""
    tree = sp.parse_blob(blob)
    for oid, _owner, _pos, _sh, dyno in io.ship_sections(blob, tree):
        if oid != ship:
            continue
        d = io.parse_dyno(blob, dyno)
        d['shco'][0], d['has_orders'] = 1, 1
        d['rout'] = (0, struct.pack('<I', 1) + struct.pack('<3f', 1, 2, 3)
                     + struct.pack('<3f', 4, 5, 6) + struct.pack('<f', 7))
        d['tail_u32'] = 0
        return mo.replace_dyno(blob, ship, sp.build_section(
            b'DYNO', dyno.version, io.build_dyno(d)))
    raise SystemExit(f'ship {ship} not found')


def said(notes, civ, *words):
    return [n for n in notes.get(civ, []) if all(w in n for w in words)]


def fixtures():
    print('what the fixtures hold')
    for group, names in GROUPS.items():
        blobs = [load(n) for n in names]
        check(f'{group}: no civ holds an admiral and no DYNO names one',
              all([mo.admirals_of(b, o['oid']) for o in mo.icv.owner_records(b)]
                  == [[], []] and
                  {mo.dyno_admiral(d) for _o, d in mo.ship_index(b).values()}
                  | {mo.dyno_admiral(f['dyno'])
                     for f in mo.fleet_index(b).values()} == {0}
                  for b in blobs))
        check(f'{group}: the treaty list reads, and is empty',
              {repr(mo.treaties(b)) for b in blobs}, {'{}'})
        check(f'{group}: no step the client took reads as admirals or '
              f'diplomacy',
              [(n, step) for n, b in zip(names[1:], blobs[1:])
               for civ in (o['oid'] for o in mo.icv.owner_records(b))
               for step in list(mo.admiral_orders(blobs[0], b, civ))
               + list(mo.diplomacy_orders(blobs[0], b, civ, {}))
               if step], [])
    base = load('g_base')
    check('an admiral built here reads back',
          (mo.admirals_of(with_admirals(base, ME, [adml(230, b'a1')]), ME),
           mo.read_admiral(adml(230, b'a1'))),
          ([(230, adml(230, b'a1'))], (b'a1', '')))
    check('a treaty built here reads back',
          (list(mo.treaties(with_treaties(base, [trty(231, ME, THEM, 5)]))),
           mo.treaty_parties(trty(231, ME, THEM, 5, kind=1))),
          ([231], (ME, THEM, 1)))
    check("a DYNO's admiral id sits after its has-orders byte",
          [mo.dyno_admiral_at(d) for _o, d in mo.ship_index(base).values()],
          [8 + 4 + 17 + 5] * 4)


def admirals():
    base = load('g_base')
    ship = min(s for s, (o, _d) in mo.ship_index(base).items() if o == ME)
    their_ship = min(s for s, (o, _d) in mo.ship_index(base).items()
                     if o == THEM)

    print("a civ's own admirals are carried")
    out, notes = merged(base, ('DemoPlayer', made(base, ME, [adml(230, b'a1')],
                                                 230)))
    check('creating one is taken, unnamed',
          (mo.admirals_of(out, ME), notes.get('DemoPlayer')),
          ([(230, adml(230, b'a1'))], None))
    out, notes = merged(base,
                        ('DemoPlayer', made(base, ME, [adml(230, b'a1')], 230)),
                        ('BadGuy', made(base, THEM, [adml(230, b'b1')], 230)))
    check('two civs creating one under the same id: the second is renumbered',
          ([oid for oid, _r in mo.admirals_of(out, ME)],
           [(oid, mo.admiral_name(r)) for oid, r in mo.admirals_of(out, THEM)]),
          ([230], [(231, b'b1')]))
    high = struct.unpack_from('<I', base, mo.idg.HIGH_WATER_ID)[0]
    out, notes = merged(base, ('DemoPlayer', with_admirals(
        base, ME, [adml(high, b'a1')])))
    check('an id that is not new is refused',
          (mo.admirals_of(out, ME), notes.get('DemoPlayer')),
          ([], [f"admiral {high} ('a1') DROPPED, it is new and {high} is not "
                f"a new object id"]))

    held = with_admirals(base, ME, [adml(230, b'a1'), adml(232, b'a2')])
    for label, records, want in (
            ('renaming', [adml(230, b'a9'), adml(232, b'a2')], None),
            ('a new rule', [adml(230, b'a1', rules=(
                (b'TSCQ', 0, b'\5\0\0\0'), (b'FSHP', 0, b''))),
                adml(232, b'a2')], None),
            ('deleting', [adml(232, b'a2')], None),
            ('a ships-assigned count alone', [adml(230, b'a1', ships=3),
                                              adml(232, b'a2')], None)):
        out, notes = merged(held, ('DemoPlayer', with_admirals(
            held, ME, records)))
        check(f'{label}: taken, unnamed',
              (mo.admirals_of(out, ME), notes.get('DemoPlayer')),
              ([(oid, mo._with_ships(r, 0)) for oid, r in
                mo.admirals_of(with_admirals(held, ME, records), ME)], want))
    for label, record, why in (
            ('a rule the client does not know', adml(230, b'a1', rules=(
                (b'XXXX', 0, b''),)), "rule b'XXXX' is not one the client knows"),
            ('a fifth type', adml(230, b'a1', kind=4),
             'its type 4 is not one the client offers'),
            ('an empty name', adml(230, b''), 'the name is empty')):
        sub = with_admirals(held, ME, [record, adml(232, b'a2')])
        out, notes = merged(held, ('DemoPlayer', sub))
        check(f'{label} is refused, the admiral as served',
              (mo.admirals_of(out, ME), said(notes, 'DemoPlayer', why) != []),
              (mo.admirals_of(held, ME), True))
    out, notes = merged(base, ('DemoPlayer', with_admirals(
        base, THEM, [adml(230, b'x')])))
    check("another civ's admirals are refused as theirs",
          notes.get('DemoPlayer'), ['admirals DROPPED, they belong to BadGuy'])
    tree, adms = mo._civ_section(base, ME, 'ADMS')
    broken = sp.replace_payload(base, tree, adms, struct.pack('<I', 1))
    out, notes = merged(base, ('DemoPlayer', broken))
    check('an ADMS that does not hold its count is refused whole',
          (notes.get('DemoPlayer'), out),
          (['admirals DROPPED, ADMS does not hold its count of admirals'],
           base))

    print('a ship names an admiral its civ holds')
    out, notes = merged(held, ('DemoPlayer', with_ship_admiral(held, ship, 230)))
    check('assigning one to an idle ship is taken and counted',
          (ship_admiral(out, ship), ships_of(out, ME), notes.get('DemoPlayer')),
          (230, {230: 1, 232: 0}, None))
    sub = with_ship_admiral(made(base, ME, [adml(230, b'a1')], 230), ship, 230)
    out, notes = merged(base, ('DemoPlayer', sub))
    check('an admiral created and assigned in one submission',
          (ship_admiral(out, ship), ships_of(out, ME), notes.get('DemoPlayer')),
          (230, {230: 1}, None))
    out, notes = merged(base,
                        ('BadGuy', made(base, THEM, [adml(230, b'b1')], 230)),
                        ('DemoPlayer', sub))
    check('and renumbered, the ship follows its new id',
          (ship_admiral(out, ship), ships_of(out, ME)), (231, {231: 1}))
    moved = ordered(held, ship)
    out, notes = merged(held, ('DemoPlayer', with_ship_admiral(moved, ship,
                                                               232)))
    check('a move order and an admiral together are both taken',
          mo.ship_index(out)[ship],
          mo.ship_index(with_ship_admiral(moved, ship, 232))[ship])
    out, notes = merged(held, ('DemoPlayer', with_ship_admiral(held, ship, 999)))
    check('an admiral the civ does not hold is refused',
          (ship_admiral(out, ship), notes.get('DemoPlayer')),
          (0, [f'ship {ship}: admiral 999 assignment DROPPED, 999 is not an '
               f'admiral this civ holds']))
    theirs = with_admirals(held, THEM, [adml(240, b'b1')])
    out, notes = merged(theirs, ('DemoPlayer', with_ship_admiral(theirs, ship,
                                                                 240)))
    check("nor another civ's admiral",
          (ship_admiral(out, ship), len(said(notes, 'DemoPlayer',
                                             'admiral 240'))), (0, 1))
    out, notes = merged(held, ('DemoPlayer', with_ship_admiral(
        held, their_ship, 230)))
    check("another civ's ship is refused as theirs, as before",
          notes.get('DemoPlayer'),
          [f'ship {their_ship}: DROPPED, owned by BadGuy'])
    assigned = with_ship_admiral(held, ship, 230)
    out, notes = merged(assigned, ('DemoPlayer', with_ship_admiral(
        assigned, ship, 0)))
    check('unassigning one is taken and the count falls',
          (ship_admiral(out, ship), ships_of(out, ME), notes.get('DemoPlayer')),
          (0, {230: 0, 232: 0}, None))
    out, notes = merged(assigned, ('DemoPlayer', with_admirals(
        assigned, ME, [adml(232, b'a2')])))
    check('deleting one that still commands a ship is refused',
          (ships_of(out, ME), notes.get('DemoPlayer')),
          ({230: 1, 232: 0}, ["admiral 230 ('a1') deletion DROPPED, it still "
                              "commands 1 ship(s) or fleet(s)"]))
    out, notes = merged(assigned, ('DemoPlayer', with_ship_admiral(
        with_admirals(assigned, ME, [adml(232, b'a2')]), ship, 0)))
    check('and taken once the same submission unassigns it',
          (ships_of(out, ME), ship_admiral(out, ship), notes.get('DemoPlayer')),
          ({232: 0}, 0, None))

    print('an admiral on a fleet is not carried yet')
    f0c, f1, f4 = load('f0c'), load('f1'), load('f4')
    out, notes = merged(f1, ('DemoPlayer', with_fleet_admiral(f1, 202, 230)))
    check('assigning one to a fleet is named', notes.get('DemoPlayer'),
          ['fleet 202: admiral 230 assignment DROPPED, an admiral on a fleet is '
           'not carried yet'])
    check('and the fleet is as served', out, f1)
    out, notes = merged(f0c, ('DemoPlayer', with_fleet_admiral(f1, 202, 230)))
    check('a new fleet naming one is formed without it',
          (sorted(mo.fleet_index(out)),
           mo.dyno_admiral(mo.fleet_index(out)[202]['dyno']),
           len(notes.get('DemoPlayer', []))), ([202], 0, 1))
    left = next(s for s in (193, 194)
                if s not in [m for m, *_ in mo.fleet_index(f4)[202]['members']])
    out, notes = merged(f1, ('DemoPlayer', with_ship_admiral(f4, left, 230)))
    check('a ship leaving a fleet with a new admiral leaves without it',
          (left in mo.ship_index(out), ship_admiral(out, left),
           notes.get('DemoPlayer')),
          (True, 0, [f'ship {left}: admiral 230 assignment DROPPED, a ship '
                     f'leaving a fleet keeps its admiral or has none']))

    print('the admiral session in the client, each step merged')
    s = {n: adm(n) for n in ('a0', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7', 'a8',
                             'a9', 'a10', 'a13')}
    for prev, cur, what in (('a0', 'a2', 'created and assigned'),
                            ('a2', 'a3', 'b1 assigned, across a tick'),
                            ('a3', 'a4', 'a rule added and one unticked'),
                            ('a4', 'a5', 'renamed'),
                            ('a5', 'a6', 'Scout created'),
                            ('a6', 'a7', 'b1 moved to Scout'),
                            ('a7', 'a8', 'b1 given a manual move'),
                            ('a8', 'a9', 'b1 set to none'),
                            ('a9', 'a10', 'Scout deleted')):
        if prev == 'a2':
            continue
        out, notes = merged(s[prev], ('GoodGuy', s[cur]))
        check(f'{cur} over {prev} ({what}): ADMS and both ships as the client '
              f'left them, unnamed',
              (mo.admirals_of(out, GOOD),
               [ship_admiral(out, sh) for sh in (669, 672)],
               notes.get('GoodGuy')),
              (mo.admirals_of(s[cur], GOOD),
               [ship_admiral(s[cur], sh) for sh in (669, 672)], None))
    check('a turn leaves the admirals as they were',
          mo.admirals_of(s['a13'], GOOD), mo.admirals_of(s['a10'], GOOD))


def diplomacy():
    names = {GOOD: 'GoodGuy', BAD: 'BadGuy'}
    civ = {GOOD: 'GoodGuy', BAD: 'BadGuy'}
    steps = ['d0', 'd1', 'd2', 'd3', 'd4', 'd5', 'd6', 'd7', 'd8', 'd9', 'd9a']
    d = {n: dip(n) for n in steps + ['d3b', 'd4g', 'd5p', 'd5b', 'd9g']}

    print('the diplomacy session in the client')
    declined = (GOOD, BAD, 0, 2, -2, -1, 180, 0, 0)
    gone = declined[:-1] + (1,)
    war = (BAD, GOOD, 1, 3, -1, 180, 180, 0, 0)
    check('each step reads its treaties',
          [{t: trdt(r) for t, r in mo.treaties(d[n]).items()} for n in steps],
          [{}, {679: (GOOD, BAD, 4, 0, -2, -1, 180, 0, 0)}, {},
           {680: (GOOD, BAD, 0, 0, -2, -1, 180, 0, 0)}, {680: declined},
           {680: declined, 681: (GOOD, BAD, 0, 1, 10, 180, 180, 0, 0)},
           {680: gone, 681: (GOOD, BAD, 0, 1, 10, 180, 180, 0, 0)},
           {680: gone, 682: war}, {680: gone, 682: war},
           {680: gone, 682: war, 683: (BAD, GOOD, 2, 0, -2, -1, 181, 0, 0)},
           {680: gone, 683: (BAD, GOOD, 2, 1, 20, 181, 181, 0, 0)}])
    check('and each step its stances, the same both ways',
          [(mo.stances(d[n])[(GOOD, BAD)], mo.stances(d[n])[(BAD, GOOD)])
           for n in steps],
          [(3, 3)] * 7 + [(1, 1)] * 3 + [(2, 2)])
    check('and both civs read their resources, metal first',
          [mo.resources_of(d['d5b'], c) for c in (GOOD, BAD)],
          [[630, 350, 260, 0, 0]] * 2)
    check('a turn leaves the treaties as they were',
          mo.treaties(d['d8']), mo.treaties(d['d7']))

    for prev, cur, who, what in (
            ('d0', 'd1', GOOD, 'an alliance proposed'),
            ('d1', 'd2', GOOD, 'withdrawn'),
            ('d2', 'd3', GOOD, 'a trade proposed'),
            ('d3b', 'd4', BAD, 'declined'),
            ('d4g', 'd5p', GOOD, 'another trade proposed'),
            ('d5b', 'd5', BAD, 'accepted, 5 metal moving'),
            ('d5', 'd6', BAD, 'the declined one deleted'),
            ('d6', 'd7', BAD, 'war declared'),
            ('d8', 'd9', BAD, 'a cease-fire proposed'),
            ('d9g', 'd9a', GOOD, 'the cease-fire accepted')):
        out, notes = merged(d[prev], (civ[who], d[cur]))
        check(f'{cur} over {prev} ({what}): treaties, stances and resources '
              f'as the client left them, unnamed',
              (mo.treaties(out), mo.stances(out),
               [mo.resources_of(out, c) for c in (GOOD, BAD)],
               notes.get(civ[who])),
              (mo.treaties(d[cur]), mo.stances(d[cur]),
               [mo.resources_of(d[cur], c) for c in (GOOD, BAD)], None))
    out, _notes = merged(d['d5b'], ('GoodGuy', d['d5b']), ('BadGuy', d['d5']))
    check('the lump sum moves once with both civs submitting',
          [mo.resources_of(out, c)[0] for c in (GOOD, BAD)], [625, 635])

    print('both parties in one turn')
    goodbye = without(d['d5b'], 681)
    out, notes = merged(d['d5b'], ('GoodGuy', goodbye), ('BadGuy', d['d5']))
    check('withdrawn before it is accepted: the acceptance is refused',
          (681 in mo.treaties(out), notes.get('BadGuy'),
           [mo.resources_of(out, c)[0] for c in (GOOD, BAD)]),
          (False, ['treaty 681 DROPPED, the other party changed it this turn'],
           [630, 630]))
    out, notes = merged(d['d5b'], ('BadGuy', d['d5']), ('GoodGuy', goodbye))
    check('accepted before it is withdrawn: the withdrawal is refused',
          (trdt(mo.treaties(out)[681])[3], notes.get('GoodGuy')),
          (1, ['treaty 681 DROPPED, the other party changed it this turn']))
    broke = mo.set_resources(d['d5b'], GOOD, [2, 350, 260, 0, 0])
    out, notes = merged(broke, ('BadGuy', d['d5']))
    check('a proposer who cannot pay: the acceptance is refused',
          (trdt(mo.treaties(out)[681])[3], notes.get('BadGuy')),
          (0, ['treaty 681 DROPPED, GoodGuy cannot pay 5 of resource 0']))

    print('what the client does not do')
    turn = mo.icv.turn_of(d['d0'])
    for label, record, why in (
            ('a treaty claimed for the other civ',
             trty(700, BAD, GOOD, turn), 'it is proposed by BadGuy'),
            ('a treaty with itself', trty(700, GOOD, GOOD, turn),
             'this civ has not met GoodGuy'),
            ('a proposal dated another turn', trty(700, GOOD, BAD, turn - 1),
             'not laid out as a proposal made this turn'),
            ('an item the client does not offer',
             trty(700, GOOD, BAD, turn, kind=0,
                  mine=(sp.build_section(b'XXXX', 0, b''),)),
             "item b'XXXX' is not one the client offers"),
            ('a metal lump sum naming a sixth resource',
             trty(700, GOOD, BAD, turn, kind=0,
                  mine=(sp.build_section(b'RSLS', 0, struct.pack('<II', 5, 5)),)),
             'not an amount and a resource')):
        out, notes = merged(d['d0'], ('GoodGuy', proposing(d['d0'], record)))
        check(f'{label} is refused', (mo.treaties(out),
                                      len(said(notes, 'GoodGuy', why))),
              ({}, 1))
    out, notes = merged(d['d0'], ('GoodGuy', with_treaties(
        d['d0'], [trty(600, GOOD, BAD, turn)])))
    check('a new treaty under an id that is not new is refused',
          len(said(notes, 'GoodGuy', 'not a new object id')), 1)
    longer = mo.treaties(d['d5'])
    longer[681] = mo.with_trdt(longer[681], length=11)
    out, notes = merged(d['d5b'], ('BadGuy', with_treaties(
        d['d5'], list(longer.values()))))
    check('an acceptance with its own length is refused',
          (trdt(mo.treaties(out)[681])[3],
           len(said(notes, 'BadGuy', 'answer changes more'))), (0, 1))
    out, notes = merged(d['d3'], ('GoodGuy', with_treaties(
        d['d3'], [mo.with_trdt(mo.treaties(d['d3'])[680], status=1)])))
    check('the proposer accepting its own treaty is refused',
          (trdt(mo.treaties(out)[680])[3],
           len(said(notes, 'GoodGuy', 'not something the client offers'))),
          (0, 1))
    out, notes = merged(d['d5'], ('GoodGuy', without(d['d5'], 681)))
    check('ending a treaty in force without war is refused',
          (681 in mo.treaties(out),
           len(said(notes, 'GoodGuy', 'ending a treaty'))), (True, 1))
    out, notes = merged(d['d0'], ('GoodGuy', both_stances(d['d0'], GOOD, BAD,
                                                          4)))
    check('a stance moved without a treaty is named once',
          (mo.stances(out), notes.get('GoodGuy')),
          (mo.stances(d['d0']),
           ['stance of GoodGuy toward BadGuy DROPPED, Peace -> Alliance, a '
            'stance moves only with a treaty or a declaration of war']))
    turn8 = mo.icv.turn_of(d['d8'])
    out, notes = merged(d['d8'], ('GoodGuy', proposing(
        d['d8'], trty(700, GOOD, BAD, turn8, war=True))))
    check('declaring war on a civ already at war is refused',
          (sorted(mo.treaties(out)), notes.get('GoodGuy')),
          ([680, 682], ['treaty 700 DROPPED, GoodGuy is already at war with '
                        'BadGuy']))
    turn9 = mo.icv.turn_of(d['d9a'])
    out, notes = merged(d['d9a'], ('BadGuy', proposing(
        d['d9a'], trty(700, BAD, GOOD, turn9, war=True))))
    check('declaring war during a cease-fire is refused',
          (sorted(mo.treaties(out)), notes.get('BadGuy')),
          ([680, 683], [f'treaty 700 DROPPED, the cease-fire with GoodGuy '
                        f'binds until turn {181 + 20}']))
    out, notes = merged(d['d0'], ('GoodGuy', with_treaties(d['d0'], [],
                                                           count=1)))
    check('a treaty list that does not hold its count is refused',
          (notes.get('GoodGuy'), out),
          (['diplomacy DROPPED, the treaty list does not hold its count of '
            'treaties'], d['d0']))

    print('stance treaties not yet seen accepted, by the same rule')
    q = with_treaties(d['d0'], [trty(700, GOOD, BAD, turn, kind=4)])
    q = mo.set_high_water(q, 700)
    yes = with_treaties(q, [mo.with_trdt(mo.treaties(q)[700], 1, 100, turn)])
    out, notes = merged(q, ('BadGuy', yes))
    check('an alliance accepted sets both stances to Alliance for 100 turns',
          (trdt(mo.treaties(out)[700])[3:6],
           (mo.stances(out)[(GOOD, BAD)], mo.stances(out)[(BAD, GOOD)]),
           notes.get('BadGuy')),
          ((1, 100, turn), (4, 4), None))

    print('one submission with admirals and diplomacy keeps the rest')
    sub = made(d['d0'], GOOD, [adml(700, b'a1')], 700)
    sub = proposing(sub, trty(701, GOOD, BAD, turn, kind=4))
    out, notes = merged(d['d0'], ('GoodGuy', sub))
    check('the admiral and the proposal are both taken',
          ([oid for oid, _r in mo.admirals_of(out, GOOD)],
           sorted(mo.treaties(out)), notes.get('GoodGuy')),
          ([700], [701], None))
    check('the merged galaxy parses', sp.parse_blob(out)[0].tag, b'SAVE')


def main():
    fixtures()
    admirals()
    diplomacy()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
