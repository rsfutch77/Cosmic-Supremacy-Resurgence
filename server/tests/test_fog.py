"""
test_fog.py: a civ is served the systems it has left as it last saw them
========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_fog.py

`fog.project` rewrites each stale planet to the civ's `EXSY` memory, and
`fog.restore` puts the authoritative planets back into what the civ returns.

Fixture: `server/tests/fixtures/diplomacy/d9.b64` (turn 181, tracked). BadGuy
last looked at GoodGuy's home system at turn 34, before two of its colonies
were founded; GoodGuy last looked at BadGuy's home at turn 110.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools', 'ai_player')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import inject_civ as icv
import inject_design as idg
import exsy
import fog
import news
import player_turn
import referee
import turn_store
import wipe_civ

GALAXY = os.path.join(ROOT, 'server', 'tests', 'fixtures', 'diplomacy', 'd9.b64')
SPGALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def planets(blob):
    """{planet id: (owner, population, name)}."""
    out = {}
    for p in icv.planet_records(blob):
        d = blob[p['plpr_payload']:p['plpr_end']]
        out[p['id']] = (p['owner'], struct.unpack_from('<I', d, fog.PLPR_HEAD)[0],
                        p['name'])
    return out


def memory(blob, civ):
    """{planet id: (owner, population)} from `civ`'s EXSY, by galaxy order."""
    tree = sp.parse_blob(blob)
    _oid, table = fog._civs(blob, tree)[civ]
    systems = fog._systems(blob, tree)
    out = {}
    for row in exsy.parse(blob[table.payload:table.end]):
        for pl, mem in zip(systems[row['system']], row['planets']):
            pid = struct.unpack_from('<I', blob, pl.payload)[0]
            out[pid] = (mem['owner'], mem['population'])
    return out


def main():
    blob = sp.load_any(GALAXY)
    before = planets(blob)

    print('which planets are stale')
    check('BadGuy: GoodGuy\'s home system, five planets that changed',
          sorted(fog.stale_planets(blob, 'BadGuy')), [261, 262, 263, 265, 266])
    check('GoodGuy: BadGuy\'s capital, grown since turn 110',
          fog.stale_planets(blob, 'GoodGuy'), [384])

    print('\nBadGuy\'s view')
    view = fog.project(blob, 'BadGuy', log=quiet)
    after = planets(view)
    mem = memory(blob, 'BadGuy')
    for pid in (261, 262, 263, 265, 266):
        check(f'planet {pid} reads as remembered', after[pid][:2], mem[pid])
    check('colonies founded since read as empty and unnamed',
          [after[p] for p in (262, 266)], [(0, 0, ''), (0, 0, '')])
    stale = {261, 262, 263, 265, 266}
    check('every other planet is as served',
          {p: v for p, v in after.items() if p not in stale},
          {p: v for p, v in before.items() if p not in stale})
    check('no planet, system or civ is added or removed',
          referee.galaxy_id(view), referee.galaxy_id(blob))
    check('the object-id high water is unchanged',
          struct.unpack_from('<I', view, idg.HIGH_WATER_ID)[0],
          struct.unpack_from('<I', blob, idg.HIGH_WATER_ID)[0])
    check('the view parses to the end', sp.parse_blob(view)[0].end, len(view))
    check('the turn is unchanged', icv.turn_of(view), icv.turn_of(blob))
    check('BadGuy\'s own planets are untouched',
          {p: v for p, v in after.items() if v[0] == 660},
          {p: v for p, v in before.items() if v[0] == 660})
    empty = next(p for p in icv.planet_records(view) if p['id'] == 262)
    d = view[empty['plpr_payload']:empty['plpr_end']]
    cits, mil, fac = fog._people(d)
    check('an emptied colony keeps no soldiers and no facilities',
          (struct.unpack_from('<I', d, cits)[0],
           struct.unpack_from('<I', d, mil + 1)[0]), (0, 0))
    check('its production is a never-colonised planet\'s',
          icv.planet_prod(view, 262), icv.empty_prod(blob))
    check('a remembered colony\'s citizens are all its owner\'s',
          {struct.unpack_from('<I', d2, i + fog.CITIZEN_OWNER)[0]
           for p in icv.planet_records(view) if p['id'] == 263
           for d2 in [view[p['plpr_payload']:p['plpr_end']]]
           for i in range(fog.PLPR_HEAD + 4,
                          fog.PLPR_HEAD + 4 + fog.CITIZEN * after[263][1],
                          fog.CITIZEN)}, {656})

    print('\nonly what the recon screen shows survives on a remembered planet')
    def plpr(b, pid):
        p = next(p for p in icv.planet_records(b) if p['id'] == pid)
        return b[p['plpr_payload']:p['plpr_end']]

    def owner_state(b, pid):
        d = plpr(b, pid)
        cits, mil, _fac = fog._people(d)
        return {
            'fields': [d[o:o + n] for o, n in fog.OWNER_FIELDS],
            'jobs': {d[i] for i in range(fog.PLPR_HEAD + 4, cits, fog.CITIZEN)},
            'soldiers': struct.unpack_from('<I', d, cits)[0],
            'facilities': struct.unpack_from('<I', d, mil + 1)[0],
            'prod': icv.planet_prod(b, pid),
        }
    check('planet 265 holds a live military store, recruitment and soldiers '
          'in the turn',
          owner_state(blob, 265)['fields'][2:], [bytes([20]), struct.pack('<I', 94)])
    for pid in (261, 263, 265):
        got = owner_state(view, pid)
        check(f'planet {pid}: food, production, recruitment and military '
              f'stores read 0', got['fields'],
              [bytes(n) for _o, n in fog.OWNER_FIELDS])
        check(f'planet {pid}: every citizen a farmer, no soldiers, no '
              f'facilities, an idle queue',
              (got['jobs'] <= {0}, got['soldiers'], got['facilities'],
               got['prod']), (True, 0, 0, icv.empty_prod(blob)))
    check('the base outputs and food store size stay as they are',
          [plpr(view, p)[4:15] + plpr(view, p)[19:23] for p in (261, 263, 265)],
          [plpr(blob, p)[4:15] + plpr(blob, p)[19:23] for p in (261, 263, 265)])

    # A planet that has not changed since the last look is still served
    # without its owner's running state.
    tree = sp.parse_blob(blob)
    _oid, table = fog._civs(blob, tree)['BadGuy']
    rows = exsy.parse(blob[table.payload:table.end])
    systems = fog._systems(blob, tree)
    for row in rows:
        for pl, mem in zip(systems.get(row['system'], ()), row['planets']):
            if struct.unpack_from('<I', blob, pl.payload)[0] == 265:
                mem['population'] = before[265][1]
                mem['owner'] = before[265][0]
    same = icv.splice(blob, table.payload, table.end, exsy.build(rows))
    check('a planet unchanged since the last look is still served as '
          'remembered', 265 in fog.stale_planets(same, 'BadGuy'), True)
    sview = fog.project(same, 'BadGuy', log=quiet)
    check('with its owner and population', planets(sview)[265][:2],
          before[265][:2])
    check('and without its military store or soldiers',
          (owner_state(sview, 265)['fields'][3], owner_state(sview, 265)['soldiers']),
          (bytes(4), 0))
    check('and restored whole', fog.restore(same, 'BadGuy', sview) == same, True)

    print('\nprojecting twice and restoring')
    check('a view projected again is unchanged',
          fog.project(view, 'BadGuy', log=quiet) == view, True)
    check('restoring an unchanged view gives back the authoritative blob',
          fog.restore(blob, 'BadGuy', view) == blob, True)
    gview = fog.project(blob, 'GoodGuy', log=quiet)
    check('and for GoodGuy', fog.restore(blob, 'GoodGuy', gview) == blob, True)
    check('GoodGuy sees BadGuy\'s capital at its turn-110 size',
          planets(gview)[384][:2], memory(blob, 'GoodGuy')[384])
    check('restoring leaves a civ with nothing stale alone',
          fog.restore(blob, 'BadGuy', blob) == blob, True)

    print('\nthe merge takes a fogged civ\'s orders as an unfogged one\'s')
    notes_fogged, notes_plain = {}, {}
    fogged = referee.apply_orders(blob, [('BadGuy', view)], log=quiet,
                                  notes=notes_fogged)
    plain = referee.apply_orders(blob, [('BadGuy', blob)], log=quiet,
                                 notes=notes_plain)
    check('the same merged blob', fogged == plain, True)
    check('and no refusal', notes_fogged, notes_plain)

    print('\na folder store keeps each civ\'s view beside the turn')
    with tempfile.TemporaryDirectory() as tmp:
        store = turn_store.TurnStore(tmp)
        turn = icv.turn_of(blob)
        store.start(blob, ['GoodGuy', 'BadGuy'], turn=turn - 1)
        held = referee.views(blob, ['GoodGuy', 'BadGuy', 'Nobody'], log=quiet)
        check('the referee projects only civs the galaxy holds, and the view '
              'without news', sorted(held),
              sorted(['BadGuy', 'GoodGuy', turn_store.PUBLIC_VIEW]))
        public = held[turn_store.PUBLIC_VIEW]
        check('which holds no news',
              [news.items_of(public, o['oid']) for o in icv.owner_records(public)],
              [[] for _o in icv.owner_records(public)])
        store.publish(turn, blob, views=held)
        check('BadGuy is served its view',
              player_turn.served_blob(store, 'BadGuy', turn) == view, True)
        check('a caller off the roster is served the view without news',
              player_turn.served_blob(store, 'Third', turn) == public, True)
        store.publish(turn + 1, blob)
        check('the next publish keeps the last turn\'s views',
              player_turn.served_blob(store, 'BadGuy', turn) == view, True)
        check('and serves the new turn to everyone',
              player_turn.served_blob(store, 'BadGuy', turn + 1) == blob, True)
        store.publish(turn + 2, blob)
        check('the publish after that removes them',
              os.path.exists(os.path.dirname(store.view_path('BadGuy', turn))),
              False)

    print('\nwiping a civ leaves rivals remembering what they last saw')
    bad = next(o['oid'] for o in icv.owner_records(blob) if o['name'] == 'BadGuy')
    good = next(o['oid'] for o in icv.owner_records(blob) if o['name'] == 'GoodGuy')

    def remembered(b, civ, pid):
        return memory(b, civ).get(pid)

    def set_row_turn(b, civ, pid, turn):
        """`b` with the turn of `civ`'s row for planet `pid`'s system set."""
        tree = sp.parse_blob(b)
        _oid, table = fog._civs(b, tree)[civ]
        rows = exsy.parse(b[table.payload:table.end])
        systems = fog._systems(b, tree)
        for row in rows:
            ids = [struct.unpack_from('<I', b, pl.payload)[0]
                   for pl in systems.get(row['system'], ())]
            if pid in ids:
                row['turn'] = turn
        return icv.splice(b, table.payload, table.end, exsy.build(rows))

    wiped, _planets, _ships = wipe_civ.wipe(blob, 'BadGuy', log=quiet)
    check('GoodGuy still remembers BadGuy\'s capital as of turn 110',
          remembered(wiped, 'GoodGuy', 384), memory(blob, 'GoodGuy')[384])
    check('and is served it as remembered, though it is empty now',
          (planets(wiped)[384][0], planets(fog.project(wiped, 'GoodGuy',
                                                       log=quiet))[384][:2]),
          (0, memory(blob, 'GoodGuy')[384]))
    check('BadGuy forgets its own planets',
          [m for m in memory(wiped, 'BadGuy').values() if m[0] == bad], [])
    looking = set_row_turn(blob, 'GoodGuy', 384, icv.turn_of(blob))
    wiped_now, _p, _s = wipe_civ.wipe(looking, 'BadGuy', log=quiet)
    check('a civ looking at the system this turn sees it empty',
          remembered(wiped_now, 'GoodGuy', 384), (0, 0))
    gone, _p, _s = wipe_civ.wipe(blob, 'BadGuy', remove_owner=True,
                                 force_owner=True, log=quiet)
    check('removing the civ\'s record clears every memory of it',
          [m for m in memory(gone, 'GoodGuy').values() if m[0] == bad], [])
    check('GoodGuy\'s own planets are remembered as before',
          {p: m for p, m in memory(wiped, 'GoodGuy').items() if m[0] == good},
          {p: m for p, m in memory(blob, 'GoodGuy').items() if m[0] == good})

    print('\na turn-0 galaxy')
    sp0 = sp.load_any(SPGALAXY)
    check('nothing is stale on the turn a galaxy is made',
          [fog.stale_planets(sp0, c) for c in ('DemoPlayer', 'BadGuy')], [[], []])
    check('and the view is the blob',
          fog.project(sp0, 'BadGuy', log=quiet) == sp0, True)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
