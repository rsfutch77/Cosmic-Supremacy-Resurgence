"""
test_fog.py: a civ is served only what its client shows it
==========================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_fog.py

`fog.project` serves every planet the civ does not own with only what the
client's per-civ save shows of it, from the civ's `EXSY` memory where it has
left the system, and other civs' private sections at their defaults. `fog.restore` puts the
authoritative state back into what the civ returns.

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
import activity
import inject_civ as icv
import inject_design as idg
import exsy
import fog
import merge_orders as mo
import news
import player_turn
import referee
import turn_store
import wipe_civ

GALAXY = os.path.join(ROOT, 'server', 'tests', 'fixtures', 'diplomacy', 'd9.b64')
ADMIRALS = os.path.join(ROOT, 'server', 'tests', 'fixtures', 'admirals',
                        'a10.b64')
SPGALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
GOOD, BAD = 656, 660
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def sections(blob):
    """{planet id: PLNT section}."""
    tree = sp.parse_blob(blob)
    return {fog._pid(blob, pl): pl
            for pls in fog._systems(blob, tree).values() for pl in pls}


def owner_state(blob, pl):
    """What only a planet's owner sees: (header fields, citizen owners,
    citizen jobs, soldiers, facilities, PROD, governor)."""
    plpr = next(pl.find('PLPR'))
    d = blob[plpr.payload:plpr.end]
    cits, mil, _fac = fog._people(d[:plpr.children[0].start - plpr.payload])
    f = struct.unpack_from('<I', d, mil + 1)[0]
    prod = next(plpr.find('PROD'))
    return ([d[o:o + n] for o, n in fog.OWNER_FIELDS],
            {struct.unpack_from('<I', d, i + fog.CITIZEN_OWNER)[0]
             for i in range(fog.PLPR_HEAD + 4, cits, fog.CITIZEN)},
            {d[i] for i in range(fog.PLPR_HEAD + 4, cits, fog.CITIZEN)},
            struct.unpack_from('<I', d, cits)[0],
            [struct.unpack_from('<II', d, mil + 5 + 8 * i) for i in range(f)],
            bytes(blob[prod.start:prod.end]),
            bytes(blob[pl.end - fog.GOVERNOR:pl.end]))


def planets(blob):
    """{planet id: (owner, population, name)}."""
    out = {}
    for pid, pl in sections(blob).items():
        nlen = struct.unpack_from('<I', blob, pl.payload + fog.PLNT_NAME)[0]
        name = blob[pl.payload + fog.PLNT_NAME + 4:
                    pl.payload + fog.PLNT_NAME + 4 + nlen].decode('latin-1')
        plpr = next(pl.find('PLPR'))
        out[pid] = (fog._owner(blob, pl),
                    struct.unpack_from('<I', blob, plpr.payload + fog.PLPR_HEAD)[0],
                    name)
    return out


def memory(blob, civ):
    """{planet id: (owner, population)} from `civ`'s EXSY, by galaxy order."""
    tree = sp.parse_blob(blob)
    _oid, table = fog._civs(blob, tree)[civ]
    systems = fog._systems(blob, tree)
    out = {}
    for row in exsy.parse(blob[table.payload:table.end]):
        for pl, mem in zip(systems[row['system']], row['planets']):
            out[fog._pid(blob, pl)] = (mem['owner'], mem['population'])
    return out


def set_row_turn(b, civ, pid, turn):
    """`b` with the turn of `civ`'s row for planet `pid`'s system set."""
    tree = sp.parse_blob(b)
    _oid, table = fog._civs(b, tree)[civ]
    rows = exsy.parse(b[table.payload:table.end])
    systems = fog._systems(b, tree)
    for row in rows:
        ids = [fog._pid(b, pl) for pl in systems.get(row['system'], ())]
        if pid in ids:
            row['turn'] = turn
    return icv.splice(b, table.payload, table.end, exsy.build(rows))


def changed(blob, civ):
    """Planets whose served owner or population differs from the live one."""
    view = fog.project(blob, civ, log=quiet)
    live, served = planets(blob), planets(view)
    return sorted(p for p in live if live[p][:2] != served[p][:2])


def owner_section(blob, oid, tag):
    _tree, s = mo._civ_section(blob, oid, tag)
    return bytes(blob[s.start:s.end])


def main():
    blob = sp.load_any(GALAXY)
    before = planets(blob)
    live = sections(blob)

    print('which planets are served from memory')
    check('BadGuy: GoodGuy\'s home system, five planets that changed',
          changed(blob, 'BadGuy'), [261, 262, 263, 265, 266])
    check('they are among those served from memory',
          {261, 262, 263, 265, 266} <= set(fog.stale_planets(blob, 'BadGuy')))
    check('GoodGuy: BadGuy\'s capital, grown since turn 110',
          changed(blob, 'GoodGuy'), [384])

    print('\nBadGuy\'s view')
    view = fog.project(blob, 'BadGuy', log=quiet)
    after = planets(view)
    mem = memory(blob, 'BadGuy')
    served = sections(view)
    for pid in (261, 262, 263, 265, 266):
        check(f'planet {pid} reads as remembered', after[pid][:2], mem[pid])
    check('colonies founded since read as empty and unnamed',
          [after[p] for p in (262, 266)], [(0, 0, ''), (0, 0, '')])
    stale = {261, 262, 263, 265, 266}
    check('every other planet keeps its owner, population and name',
          {p: v for p, v in after.items() if p not in stale},
          {p: v for p, v in before.items() if p not in stale})
    mine = {p for p, v in before.items() if v[0] == BAD}
    check('BadGuy\'s own planets are untouched, byte for byte',
          {p: view[served[p].start:served[p].end] for p in mine},
          {p: blob[live[p].start:live[p].end] for p in mine})
    blank = icv.empty_prod(blob)
    no_state = ([bytes(n) for _o, n in fog.OWNER_FIELDS], 0, blank, bytes(4))
    others = {p: owner_state(view, served[p]) for p in served if p not in mine}
    check('every other planet holds no stores, soldiers, queue or governor',
          sorted(p for p, o in others.items()
                 if (o[0], o[3], o[5], o[6]) != no_state), [])
    check('its citizens are all farmers of the owner it is served with',
          sorted(p for p, o in others.items()
                 if o[2] - {0} or o[1] - {after[p][0]}), [])
    check('and its only facility is a shipyard, when it has one',
          sorted(p for p, o in others.items() if o[4] not in ([], [(2, 1)])),
          [])
    check('no planet, system or civ is added or removed',
          referee.galaxy_id(view), referee.galaxy_id(blob))
    check('the object-id high water is unchanged',
          struct.unpack_from('<I', view, idg.HIGH_WATER_ID)[0],
          struct.unpack_from('<I', blob, idg.HIGH_WATER_ID)[0])
    check('the view parses to the end', sp.parse_blob(view)[0].end, len(view))
    check('the turn is unchanged', icv.turn_of(view), icv.turn_of(blob))

    print('\nwhat the per-civ save shows of a planet stays')

    def after_queue(b, pid):
        """(natural bytes and food store size, history, Planet:368, rulers,
        rebellion, deposits, ENLI payload) of a planet."""
        plpr = next(sections(b)[pid].find('PLPR'))
        d = b[plpr.payload:plpr.end]
        prod, enli = plpr.children
        t = b[prod.end:enli.start]
        n = struct.unpack_from('<I', t, 20)[0]
        at = 24 + fog.RULER.size * n
        return (d[4:15] + d[19:23], t[:16], struct.unpack_from('<I', t, 16)[0],
                [fog.RULER.unpack_from(t, 24 + fog.RULER.size * i)
                 for i in range(n)],
                t[at:at + 2], t[at + 2:], b[enli.payload:enli.end])
    for pid in (261, 265, 100):
        got, auth = after_queue(view, pid), after_queue(blob, pid)
        owner = fog._owner(view, sections(view)[pid])
        check(f'planet {pid}: its natural bytes, food store size and deposits '
              f'stay', (got[0], got[5]), (auth[0], auth[5]))
        check(f'    no population history, settled, its served owner the only '
              f'ruler since turn 0, no rebellion and no energy link',
              got[1:5] + got[6:],
              (bytes(16), 8, [(owner, 100.0, 0, -1)] if owner else [],
               bytes(2), bytes(4)))
    check('GoodGuy\'s planet 261 has a population history and a ruler since '
          'a later turn in the turn',
          (after_queue(blob, 261)[1] != bytes(16),
           after_queue(blob, 261)[3][0][2] > 0), (True, True))
    check('GoodGuy\'s planet 265 holds soldiers and several facilities in the '
          'turn', (owner_state(blob, live[265])[3] > 0,
                   len(owner_state(blob, live[265])[4]) > 1), (True, True))
    check('and GoodGuy\'s home planets are served only their shipyards',
          [owner_state(view, served[p])[4] for p in (261, 265)],
          [[(2, 1)], [(2, 1)]])

    print('\na rival\'s planet in a system the viewer is in this turn')
    now = set_row_turn(blob, 'BadGuy', 265, icv.turn_of(blob))
    nview = fog.project(now, 'BadGuy', log=quiet)
    check('is served with its live owner, population and name',
          planets(nview)[265], before[265])
    o = owner_state(nview, sections(nview)[265])
    check('without its soldiers, stores, queue or governor',
          (o[0], o[3], o[5], o[6]), no_state)
    check('and restored whole', fog.restore(now, 'BadGuy', nview) == now)

    print('\nanother civ\'s private state')
    check('GoodGuy holds credits, research and resources in the turn',
          (mo.credits_of(blob, GOOD) > 0, len(mo.completed_research(blob, GOOD)) > 2,
           sum(mo.resources_of(blob, GOOD)) > 0), (True, True, True))
    check('BadGuy is served GoodGuy with no credits or resources, and only '
          'the two starting technologies',
          (mo.credits_of(view, GOOD), mo.completed_research(view, GOOD),
           mo.resources_of(view, GOOD)), (0, [0, 1], [0, 0, 0, 0, 0]))
    check('and no research topic',
          mo.research_of(view, GOOD), (b'\xff' * 4, b'\xff' * 4))
    check('and no memory, governors or queues',
          [mo._civ_section(view, GOOD, t)[1].size for t in
           ('EXSY', 'GOVS', 'SPQS')], [4, 4, 4])
    theirs = mo.knpl.parse(owner_section(blob, GOOD, 'KNPL')[8:])
    check('and of the civs it has met, only BadGuy, as first contact makes '
          'the record, with the stance, the turn met and the war fields',
          mo.knpl.parse(owner_section(view, GOOD, 'KNPL')[8:]),
          [dict(mo.FIRST_CONTACT, civ=BAD, handle=BAD, met=r['met'],
                **{k: r[k] for k in fog.KNPL_KEPT})
           for r in theirs if r['civ'] == BAD])
    check('    which d9 holds, at war', [r['stance'] for r in theirs
                                         if r['civ'] == BAD], [1])
    check('and every trait 0', set(mo.traits_of(view, GOOD) or (None,)), {0})
    owpr_at = mo._civ_section(blob, GOOD, 'OWPR')[1]
    v_at = mo._civ_section(view, GOOD, 'OWPR')[1]
    r_auth = owpr_at.payload + mo.research_offsets(blob, owpr_at)[1]
    r_view = v_at.payload + mo.research_offsets(view, v_at)[1]
    check('its score and reputation stay, as the score rows show them',
          (view[r_view + 11:r_view + 15], view[r_view + 27:r_view + 31]),
          (blob[r_auth + 11:r_auth + 15], blob[r_auth + 27:r_auth + 31]))
    check('its colour, setup flags and position stay',
          view[r_view + 31:r_view + 55], blob[r_auth + 31:r_auth + 55])
    check('its name, checkbox and arms stay',
          mo.civ_identity(view, GOOD), mo.civ_identity(blob, GOOD))
    check('only the designs its ships are built to stay',
          (mo.designs_of(view, GOOD), set(mo.designs_in_use(blob, GOOD)),
           len(mo.designs_of(blob, GOOD))), ({657}, {657}, 8))
    check('    and restore puts the other seven back',
          mo.designs_of(fog.restore(blob, 'BadGuy', view), GOOD),
          mo.designs_of(blob, GOOD))
    check('BadGuy\'s own private sections are untouched',
          [owner_section(view, BAD, t) for t in fog.WITHHELD],
          [owner_section(blob, BAD, t) for t in fog.WITHHELD])

    print('\nprojecting twice and restoring')
    check('a view projected again is unchanged',
          fog.project(view, 'BadGuy', log=quiet) == view, True)
    check('restoring an unchanged view gives back the authoritative blob',
          fog.restore(blob, 'BadGuy', view) == blob, True)
    gview = fog.project(blob, 'GoodGuy', log=quiet)
    check('and for GoodGuy', fog.restore(blob, 'GoodGuy', gview) == blob, True)
    check('GoodGuy sees BadGuy\'s capital at its turn-110 size',
          planets(gview)[384][:2], memory(blob, 'GoodGuy')[384])
    check('restoring the turn itself leaves it alone',
          fog.restore(blob, 'BadGuy', blob) == blob, True)
    sub, want = bytearray(view), bytearray(blob)
    struct.pack_into('<f', sub, served[min(mine)].payload + 20, 1.5)
    struct.pack_into('<f', want, live[min(mine)].payload + 20, 1.5)
    check('a change to the civ\'s own planet is kept by restore',
          fog.restore(blob, 'BadGuy', bytes(sub)) == bytes(want), True)

    print('\nanother civ\'s scan reports and end-galaxy vote')
    report = sp.build_section(b'SCPL', 0, bytes(12))
    voted = bytearray(mo.set_reports(mo.set_reports(blob, GOOD, [report]),
                                     BAD, [report]))
    tree = sp.parse_blob(bytes(voted))
    for _name, (_oid, o) in fog._owners(bytes(voted), tree).items():
        voted[fog._usse(next(o.find('DATA'))).payload + fog.END_VOTE_AT] = 1
    voted = bytes(voted)
    vview = fog.project(voted, 'BadGuy', log=quiet)
    check('GoodGuy\'s reports and vote are withheld',
          (mo.reports_of(vview, GOOD), mo.end_vote_of(vview, GOOD)), ([], 0))
    check('BadGuy keeps its own',
          (mo.reports_of(vview, BAD), mo.end_vote_of(vview, BAD)),
          ([report], 1))
    check('restore puts GoodGuy\'s back',
          fog.restore(voted, 'BadGuy', vview) == voted, True)

    print('\nanother civ\'s admirals and activity')
    a10 = sp.load_any(ADMIRALS)
    aview = fog.project(a10, 'BadGuy', log=quiet)
    check('GoodGuy\'s admiral 674 stays, since one of its ships follows it',
          [a for a, _r in mo.admirals_of(aview, GOOD)], [674])
    ship = next(o for o in mo.space_objects(a10)[2]
                if o['owner'] == GOOD and mo.dyno_admiral(o['dyno']) == 674)
    idle = a10.replace(ship['dyno'], mo.with_dyno_admiral(ship['dyno'], 0), 1)
    iview = fog.project(idle, 'BadGuy', log=quiet)
    check('with no ship following it, it is withheld',
          mo.admirals_of(iview, GOOD), [])
    check('    and restore puts it back',
          fog.restore(idle, 'BadGuy', iview) == idle, True)
    turn = icv.turn_of(idle)
    active = activity.mark_present(activity.set_baseline(idle, 'GoodGuy',
                                                         turn - 1), ['GoodGuy'])
    sview = fog.project(active, 'BadGuy', log=quiet)
    check('a civ active last turn shows active this turn, with no flag',
          activity.state(sview)['GoodGuy'], (turn, 0))
    check('    and restore puts its baseline and flag back',
          fog.restore(active, 'BadGuy', sview) == active, True)
    check('a civ showing inactive keeps the baseline its percentage pins',
          (activity.penalty(turn, 0), activity.state(fog.project(
              activity.set_baseline(idle, 'GoodGuy', 0), 'BadGuy',
              log=quiet))['GoodGuy']), (turn - 72, (0, 0)))
    check('BadGuy\'s own activity is untouched',
          activity.state(fog.project(activity.mark_present(
              idle, ['BadGuy']), 'BadGuy', log=quiet))['BadGuy'][1], 1)

    print('\ntreaties, as the per-civ save writes them (0x55B250)')
    check('d9: treaty 680 is GoodGuy\'s declined trade, deleted from '
          'BadGuy\'s side', sorted(mo.treaties(blob)), [680, 682, 683])
    check('BadGuy is served the treaties it is party to and has not deleted',
          sorted(mo.treaties(view)), [682, 683])
    check('GoodGuy is served all three',
          sorted(mo.treaties(gview)), [680, 682, 683])
    stranger = mo.set_treaties(blob, list(mo.treaties(blob).values()) + [
        mo.treaties(blob)[683][:8] + struct.pack('<I', 690)
        + mo.treaties(blob)[683][12:20] + struct.pack('<II', GOOD, 999)
        + mo.treaties(blob)[683][28:]])
    check('a treaty between two other civs is not served',
          sorted(mo.treaties(fog.project(stranger, 'BadGuy', log=quiet))),
          [682, 683])
    accepted = mo.with_trdt(mo.treaties(view)[683], 1, 20, icv.turn_of(blob))
    answered = mo.set_treaties(view, [mo.treaties(view)[682], accepted])
    check('restore puts the withheld one back in its place and keeps the '
          'civ\'s answer', mo.treaties(fog.restore(blob, 'BadGuy', answered)),
          {**mo.treaties(blob), 683: accepted})
    check('and keeps a new proposal after them',
          list(mo.treaties(fog.restore(blob, 'BadGuy', mo.set_treaties(
              view, list(mo.treaties(view).values()) + [mo.treaties(
                  stranger)[690][:8] + struct.pack('<I', 700)
                  + mo.treaties(stranger)[690][12:]])))),
          [680, 682, 683, 700])

    print('\na caller with no seat')
    pview = fog.project(blob, None, log=quiet)
    rest = [t for t in fog.WITHHELD if t != b'KNPL']
    check('is served each civ\'s private sections as its rival is',
          ([owner_section(pview, GOOD, t) for t in rest],
           [owner_section(pview, BAD, t) for t in rest]),
          ([owner_section(view, GOOD, t) for t in rest],
           [owner_section(gview, BAD, t) for t in rest]))
    check('    and no civ\'s known players, since it is no civ',
          [mo._civ_section(pview, c, 'KNPL')[1].size for c in (GOOD, BAD)],
          [4, 4])
    check('and only the designs each civ\'s ships use',
          (mo.designs_of(pview, GOOD), mo.designs_of(pview, BAD)),
          (set(mo.designs_in_use(blob, GOOD)) & mo.designs_of(blob, GOOD),
           {d for d in mo.designs_of(blob, BAD)
            if d in mo.designs_of(view, BAD)
            and d in mo.designs_of(gview, BAD)}))
    check('every planet with its live owner and population, from no memory',
          {p: v[:2] for p, v in planets(pview).items()},
          {p: v[:2] for p, v in planets(blob).items()})
    check('and none with its owner\'s state',
          sorted(p for p, pl in sections(pview).items()
                 if owner_state(pview, pl)[3]
                 or owner_state(pview, pl)[4] not in ([], [(2, 1)])), [])
    check('no civ\'s news',
          [news.items_of(pview, o['oid']) for o in icv.owner_records(pview)],
          [[] for _o in icv.owner_records(pview)])
    check('no treaties', mo.treaties(pview), {})
    check('it parses and is the same galaxy',
          (sp.parse_blob(pview)[0].end == len(pview),
           referee.galaxy_id(pview) == referee.galaxy_id(blob)), (True, True))

    print('\nthe merge takes a fogged civ\'s orders as an unfogged one\'s')
    notes_fogged, notes_plain = {}, {}
    fogged = referee.apply_orders(blob, [('BadGuy', view)], log=quiet,
                                  notes=notes_fogged)
    plain = referee.apply_orders(blob, [('BadGuy', blob)], log=quiet,
                                 notes=notes_plain)
    check('the same merged blob', fogged == plain, True)
    check('and no refusal', notes_fogged, notes_plain)
    gfogged = referee.apply_orders(blob, [('GoodGuy', gview)], log=quiet,
                                   notes=notes_fogged)
    check('and for GoodGuy',
          gfogged == referee.apply_orders(blob, [('GoodGuy', blob)], log=quiet),
          True)

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
    wiped, _planets, _ships = wipe_civ.wipe(blob, 'BadGuy', log=quiet)
    check('GoodGuy still remembers BadGuy\'s capital as of turn 110',
          memory(wiped, 'GoodGuy').get(384), memory(blob, 'GoodGuy')[384])
    check('and is served it as remembered, though it is empty now',
          (planets(wiped)[384][0], planets(fog.project(wiped, 'GoodGuy',
                                                       log=quiet))[384][:2]),
          (0, memory(blob, 'GoodGuy')[384]))
    check('BadGuy forgets its own planets',
          [m for m in memory(wiped, 'BadGuy').values() if m[0] == BAD], [])
    looking = set_row_turn(blob, 'GoodGuy', 384, icv.turn_of(blob))
    wiped_now, _p, _s = wipe_civ.wipe(looking, 'BadGuy', log=quiet)
    check('a civ looking at the system this turn sees it empty',
          memory(wiped_now, 'GoodGuy').get(384), (0, 0))
    gone, _p, _s = wipe_civ.wipe(blob, 'BadGuy', remove_owner=True,
                                 force_owner=True, log=quiet)
    check('removing the civ\'s record clears every memory of it',
          [m for m in memory(gone, 'GoodGuy').values() if m[0] == BAD], [])
    check('GoodGuy\'s own planets are remembered as before',
          {p: m for p, m in memory(wiped, 'GoodGuy').items() if m[0] == GOOD},
          {p: m for p, m in memory(blob, 'GoodGuy').items() if m[0] == GOOD})

    print('\na turn-0 galaxy')
    sp0 = sp.load_any(SPGALAXY)
    check('nothing is served from memory on the turn a galaxy is made',
          [changed(sp0, c) for c in ('DemoPlayer', 'BadGuy')], [[], []])
    view0 = fog.project(sp0, 'BadGuy', log=quiet)
    check('its view parses and restores to the blob',
          (sp.parse_blob(view0)[0].end == len(view0),
           fog.restore(sp0, 'BadGuy', view0) == sp0), (True, True))

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
