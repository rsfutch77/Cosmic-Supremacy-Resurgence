"""
test_admirals_diplomacy_merge.py: admirals and diplomacy are refused by name
=============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_admirals_diplomacy_merge.py

The merge carries none of it and says what it dropped. Before this, a new
admiral or a treaty was ignored without a note, and an admiral id in a ship's
DYNO rode along with its orders, naming an admiral the merged galaxy did not
hold.

The records built here follow the client's writers (ADML 0x5A51B0, ADDT
0x5A4F20, TRTY 0x55A4D0, TRDT 0x559890); the fixtures are the governor, tier4
and tier5 sessions. `fixtures/admirals` is a session in the client on
`clean.dat` (27 October 2026): a1 created and assigned to the colony ship (a2),
b1 assigned (a3), a rule added and one unticked (a4), renamed (a5), Scout
created (a6), b1 moved to Scout (a7), b1 given a manual move (a8), b1 set to
none (a9), Scout deleted (a10), one turn advanced (a13). Turn 111 ticked on its
own between a2 and a3.
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
GOOD, BAD = 656, 660                    # tier5 q: GoodGuy, BadGuy
GROUPS = {
    'governors': ['g_base', 'g_made', 'g_assigned', 'g_switched',
                  'g_reassigned', 'g_deleted'],
    'tier4': ['t4e', 't4f', 't4g', 't4i', 't4j', 't4k'],
    'tier5': ['f0c', 'f1', 'f2', 'f3', 'f4', 'f5', 'f6'],
    'tier5 queues': ['q1base2', 'q1assign', 'q2b', 'q3', 'q4', 'q5', 'q6',
                     'q7', 'q8', 'q9', 'q10', 'q11s2', 'q12', 'q13', 'qs0',
                     'qs1', 'qs2', 'qs3'],
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


def merged(served, *subs):
    notes = {}
    out = mo.merge(served, list(subs), log=quiet, notes=notes)
    return out, notes


def adml(oid, name, kind=0, ships=0, rules=((b'ATPL', 0, b'\x00' * 4),)):
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
    tree, adms = mo._civ_section(blob, civ, 'ADMS')
    payload = struct.pack('<I', len(records)) + b''.join(records)
    return sp.replace_payload(blob, tree, adms, payload)


def trty(oid, first, second, kind=3, mine=(), theirs=()):
    """A whole TRTY section as the writer lays one out: the parties, the
    kind, four undecoded fields, the turn, two bytes and two item lists."""
    body = struct.pack('<IIIIiII', first, second, kind, 0, -2, 0, 0) + b'\0\0'
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


def with_stance(blob, civ, other, code):
    """`blob` with the civ's KNPL record for `other` reading this stance."""
    _tree, knpl = mo._civ_section(blob, civ, 'KNPL')
    out = bytearray(blob)
    count = struct.unpack_from('<I', blob, knpl.payload)[0]
    for i in range(count):
        at = knpl.payload + mo.KNPL_COUNT + i * mo.KNPL_RECORD
        if struct.unpack_from('<I', blob, at)[0] == other:
            struct.pack_into('<I', out, at + mo.KNPL_STANCE_AT, code)
            return bytes(out)
    raise SystemExit(f'civ {civ} has not met {other}')


def with_ship_admiral(blob, ship, admiral):
    dyno = mo.ship_index(blob)[ship][1]
    return mo.replace_dyno(blob, ship, mo.admiral_kept(dyno, (admiral,))[0])


def with_fleet_admiral(blob, fleet, admiral):
    f = mo.fleet_index(blob)[fleet]
    out = bytearray(blob)
    at = f['sec'].payload + mo.OBJECT_HEADER + mo.dyno_admiral_at(f['dyno'])
    struct.pack_into('<I', out, at, admiral)
    return bytes(out)


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


def main():
    base = load('g_base')
    q = load('q1base2')
    ship = min(s for s, (o, _d) in mo.ship_index(base).items() if o == ME)
    their_ship = min(s for s, (o, _d) in mo.ship_index(base).items()
                     if o == THEM)

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
              [(n, why) for n, b in zip(names[1:], blobs[1:])
               for civ in (o['oid'] for o in mo.icv.owner_records(b))
               for why in mo.admiral_refusals(blobs[0], b, civ, {})
               + mo.diplomacy_refusals(blobs[0], b, civ, {})], [])
    check('the queue session has met, at war, as waronly starts it',
          mo.stances(q), {(GOOD, BAD): 1, (BAD, GOOD): 1})
    check('an admiral built here reads back',
          (mo.admirals_of(with_admirals(base, ME, [adml(230, b'a1')]), ME),
           mo.admiral_name(adml(230, b'a1'))),
          ([(230, adml(230, b'a1'))], b'a1'))
    check('a treaty built here reads back',
          (list(mo.treaties(with_treaties(base, [trty(231, ME, THEM)]))),
           mo.treaty_parties(trty(231, ME, THEM, kind=1))),
          ([231], (ME, THEM, 1)))
    check("a DYNO's admiral id sits after its has-orders byte",
          [mo.dyno_admiral_at(d) for _o, d in mo.ship_index(base).values()],
          [8 + 4 + 17 + 5] * 4)

    print('admirals are refused by name and nothing of them is taken')
    made = mo.set_high_water(with_admirals(base, ME, [adml(230, b'a1')]), 230)
    out, notes = merged(base, ('DemoPlayer', made))
    check('creating one is named', notes.get('DemoPlayer'),
          ["admiral 230 ('a1') DROPPED, creating an admiral is not carried "
           "yet"])
    check('and the galaxy is as served', out, base)

    held = with_admirals(base, ME, [adml(230, b'a1'), adml(232, b'a2')])
    edits = (('editing', [adml(230, b'a9'), adml(232, b'a2')],
              ["admiral 230 ('a9') DROPPED, editing an admiral is not "
               "carried yet"]),
             ('a new rule', [adml(230, b'a1', rules=(
                 (b'ATPL', 0, b'\0' * 4), (b'FSHP', 0, b''))),
                 adml(232, b'a2')],
              ["admiral 230 ('a1') DROPPED, editing an admiral is not "
               "carried yet"]),
             ('deleting', [adml(232, b'a2')],
              ["admiral 230 ('a1') deletion DROPPED, deleting an admiral is "
               "not carried yet"]),
             ('a ships-assigned count alone', [adml(230, b'a1', ships=1),
                                               adml(232, b'a2')], None))
    for label, records, want in edits:
        out, notes = merged(held, ('DemoPlayer', with_admirals(
            held, ME, records)))
        check(f'{label}: {"named" if want else "says nothing"}',
              notes.get('DemoPlayer'), want)
        check(f'{label}: ADMS as served', mo.admirals_of(out, ME),
              mo.admirals_of(held, ME))
    out, notes = merged(base, ('DemoPlayer', with_admirals(
        base, THEM, [adml(230, b'x')])))
    check("another civ's admirals are refused as theirs",
          notes.get('DemoPlayer'), ['admirals DROPPED, they belong to BadGuy'])
    broken = with_admirals(base, ME, [adml(230, b'a1')[:-1]])
    out, notes = merged(base, ('DemoPlayer', broken))
    check('an ADMS that does not hold its count is refused whole',
          (notes.get('DemoPlayer'), out),
          (['admirals DROPPED, ADMS does not hold its count of admirals'],
           base))

    print("a DYNO's admiral is kept as served; the orders still go through")
    out, notes = merged(base, ('DemoPlayer', with_ship_admiral(base, ship,
                                                               230)))
    check('assigning one to an idle ship is named',
          notes.get('DemoPlayer'),
          [f'ship {ship}: admiral 230 assignment DROPPED, admirals are not '
           f'carried yet'])
    check('and the ship is as served', out, base)
    moved = ordered(base, ship)
    out, notes = merged(base, ('DemoPlayer', with_ship_admiral(moved, ship,
                                                               230)))
    check('with a move order, the order is taken',
          mo.ship_index(out)[ship], mo.ship_index(moved)[ship])
    check('and the admiral named', len(said(notes, 'DemoPlayer', 'admiral 230')),
          1)
    out, notes = merged(base, ('DemoPlayer', with_ship_admiral(
        base, their_ship, 230)))
    check("another civ's ship is refused as theirs, as before",
          notes.get('DemoPlayer'),
          [f'ship {their_ship}: DROPPED, owned by BadGuy'])
    held_ship = with_ship_admiral(held, ship, 230)
    out, notes = merged(held_ship, ('DemoPlayer', with_ship_admiral(
        held_ship, ship, 0)))
    check('unassigning one is named',
          notes.get('DemoPlayer'),
          [f'ship {ship}: unassigning admiral 230 DROPPED, admirals are not '
           f'carried yet'])
    check('and the ship keeps it', mo.dyno_admiral(mo.ship_index(out)[ship][1]),
          230)

    f0c, f1, f4 = load('f0c'), load('f1'), load('f4')
    out, notes = merged(f1, ('DemoPlayer', with_fleet_admiral(f1, 202, 230)))
    check('assigning one to a fleet is named', notes.get('DemoPlayer'),
          ['fleet 202: admiral 230 assignment DROPPED, admirals are not '
           'carried yet'])
    check('and the fleet is as served', out, f1)
    out, notes = merged(f0c, ('DemoPlayer', with_fleet_admiral(f1, 202, 230)))
    check('a new fleet naming one is formed, the admiral named',
          (sorted(mo.fleet_index(out)), notes.get('DemoPlayer')),
          ([202], ['fleet 202: admiral 230 assignment DROPPED, admirals are '
                   'not carried yet']))
    check('and it names none', mo.dyno_admiral(mo.fleet_index(out)[202]['dyno']),
          0)
    left = next(s for s in (193, 194)
                if s not in [m for m, *_ in mo.fleet_index(f4)[202]['members']])
    out, notes = merged(f1, ('DemoPlayer', with_ship_admiral(f4, left, 230)))
    check('a ship leaving a fleet with a new admiral leaves without it',
          (left in mo.ship_index(out),
           mo.dyno_admiral(mo.ship_index(out)[left][1]),
           notes.get('DemoPlayer')),
          (True, 0, [f'ship {left}: admiral 230 assignment DROPPED, admirals '
                     f'are not carried yet']))
    out, notes = merged(f1, ('DemoPlayer', f4))
    check('and one leaving as measured says nothing', notes.get('DemoPlayer'),
          None)

    print('the admiral session in the client')
    steps = ['a0', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7', 'a8', 'a9', 'a10', 'a13']
    s = {n: adm(n) for n in steps}
    five, scout = (674, b'a1'), (675, b'Scout')
    named = (674, b'Admiral Five')
    check('each step reads its admirals',
          [[(a, mo.admiral_name(r)) for a, r in mo.admirals_of(s[n], GOOD)]
           for n in steps],
          [[], [five], [five], [five], [named], [named, scout],
           [named, scout], [named, scout], [named, scout], [named], [named]])
    check("and each step's ships name them in their DYNO (b1, colony ship)",
          [tuple(mo.dyno_admiral(mo.ship_index(s[n])[sh][1])
                 for sh in (669, 672)) for n in steps],
          [(0, 0), (0, 674), (674, 674), (674, 674), (674, 674), (674, 674),
           (675, 674), (675, 674), (0, 674), (0, 674), (0, 674)])
    check('a turn leaves the admirals as they were',
          mo.admirals_of(s['a13'], GOOD), mo.admirals_of(s['a10'], GOOD))
    for prev, cur, want in (
            ('a0', 'a2', "admiral 674 ('a1') DROPPED, creating"),
            ('a3', 'a4', "admiral 674 ('a1') DROPPED, editing"),
            ('a4', 'a5', "admiral 674 ('Admiral Five') DROPPED, editing"),
            ('a5', 'a6', "admiral 675 ('Scout') DROPPED, creating"),
            ('a6', 'a7', None),
            ('a9', 'a10', "admiral 675 ('Scout') deletion DROPPED, deleting")):
        got = mo.admiral_refusals(s[prev], s[cur], GOOD, {})
        check(f'{cur} over {prev}: {want or "a ships-assigned count says nothing"}',
              [g.startswith(want) for g in got] if want else got,
              [True] if want else [])
    for prev, cur, served, word in (('a6', 'a7', 674, 'assignment'),
                                    ('a8', 'a9', 675, 'unassigning')):
        dyno, why = mo.admiral_kept(mo.ship_index(s[cur])[669][1], (served,))
        check(f'{cur} over {prev}: b1 keeps admiral {served}, named',
              (mo.dyno_admiral(dyno), word in why), (served, True))
    out, notes = merged(s['a0'], ('GoodGuy', s['a2']))
    check('a2 over a0: the colony ship and ADMS are as served, both named',
          (mo.dyno_admiral(mo.ship_index(out)[672][1]),
           mo.admirals_of(out, GOOD), len(notes.get('GoodGuy', []))),
          (0, [], 2))
    out, notes = merged(s['a7'], ('GoodGuy', s['a8']))
    check("a8 over a7: b1's manual move is taken under its admiral, unnamed",
          (mo.ship_index(out)[669], notes.get('GoodGuy')),
          (mo.ship_index(s['a8'])[669], None))

    print('diplomacy is refused by name and nothing of it is taken')
    proposed = mo.set_high_water(with_treaties(base, [trty(231, ME, THEM)]),
                                 231)
    out, notes = merged(base, ('DemoPlayer', proposed))
    check('proposing a treaty is named', notes.get('DemoPlayer'),
          ['treaty 231 (DemoPlayer and BadGuy, kind 3) DROPPED, proposing a '
           'treaty or declaring war is not carried yet'])
    check('and the treaty list is as served', (mo.treaties(out), out),
          ({}, base))
    held_t = with_treaties(base, [trty(231, ME, THEM)])
    for label, records, word in (
            ('withdrawing', [], 'withdrawing or deleting'),
            ('answering', [trty(231, ME, THEM, kind=3, mine=(
                sp.build_section(b'TRLE', 0, struct.pack('<I', 60)),))],
             'answering or changing')):
        out, notes = merged(held_t, ('BadGuy', with_treaties(held_t, records)))
        check(f'{label} is named', len(said(notes, 'BadGuy', 'treaty 231',
                                            word)), 1)
        check(f'{label}: the treaty stays as served', mo.treaties(out),
              mo.treaties(held_t))
    out, notes = merged(base, ('DemoPlayer', with_treaties(base, [], count=1)))
    check('a treaty list that does not hold its count is refused',
          (notes.get('DemoPlayer'), out),
          (['diplomacy DROPPED, the treaty list does not hold its count of '
            'treaties'], base))

    peace = with_stance(with_stance(q, GOOD, BAD, 3), BAD, GOOD, 3)
    out, notes = merged(q, ('GoodGuy', peace))
    check('a stance moved in both records is named once, from the '
          "submitter's side",
          notes.get('GoodGuy'),
          ['stance of GoodGuy toward BadGuy DROPPED, War -> Peace; diplomacy '
           'is not carried yet'])
    check('and both records are as served', (mo.stances(out), out), (mo.stances(q), q))
    war = with_treaties(peace, [trty(700, GOOD, BAD, kind=1)])
    out, notes = merged(peace, ('GoodGuy', with_stance(with_stance(
        war, GOOD, BAD, 1), BAD, GOOD, 1)))
    check('a declaration of war is named by its treaty alone',
          notes.get('GoodGuy'),
          ['treaty 700 (GoodGuy and BadGuy, kind 1) DROPPED, proposing a '
           'treaty or declaring war is not carried yet'])

    print('one submission refused for admirals and diplomacy keeps the rest')
    both = with_treaties(with_admirals(ordered(base, ship), ME,
                                       [adml(230, b'a1')]),
                         [trty(231, ME, THEM)])
    out, notes = merged(base, ('DemoPlayer', both))
    check('the ship order is taken', mo.ship_index(out)[ship],
          mo.ship_index(ordered(base, ship))[ship])
    check('and both refusals are named',
          (len(said(notes, 'DemoPlayer', 'admiral 230')),
           len(said(notes, 'DemoPlayer', 'treaty 231'))), (1, 1))
    check('the merged galaxy parses', sp.parse_blob(out)[0].tag, b'SAVE')

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
