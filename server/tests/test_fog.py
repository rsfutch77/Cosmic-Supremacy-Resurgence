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
        check('a civ given no view is served the turn',
              player_turn.served_blob(store, 'Third', turn) == blob, True)
        store.publish(turn + 1, blob)
        check('the next publish removes the last turn\'s views',
              os.path.exists(os.path.dirname(store.view_path('BadGuy', turn))),
              False)
        check('and serves the turn to everyone',
              player_turn.served_blob(store, 'BadGuy', turn + 1) == blob, True)

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
