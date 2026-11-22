"""
test_scans_notes_wormholes_merge.py: scans, notes and wormhole links
=====================================================================
    python server\\tests\\test_scans_notes_wormholes_merge.py

Scans use `fixtures/unruled` (see `test_design_edits_merge.py`), from `d4`:

    sc1  DOSC twice: a planetary scan of BadGuy's HQ and a route scan of
         BadGuy's ship 677, two reports after GoodGuy's designs
    sc2  DLSC: the planetary report deleted

Notes the client wrote, from `s0` on the News screen's Notes row (`Create a
New Note`) in TestBed, saved after each step (Robert, 22 November 2026):

    nt1  a note written: subject `testnote`, text `hellotestnote`
    nt2  that note edited: `testnoteedit`, `hellotestnoteedit`, important

Wormholes the client connected, also from `s0`, given a `wgen` design with a
Wormhole Generator and two ships of it, sent out and ordered to create
wormholes in GoodGuy's sphere, then ticked to turn 304 (22 November 2026).
The engine made 682; the client allows `planets / 10 + 1` (`0x0053A5A0`), so
683 is 682's record copied to the second ship's target:

    wo0  turn 230, both wgen ships at their targets, unordered
    wo1  each given Create Wormhole (order 8) by Robert
    wh0  682 and 683 unlinked, as the client saved them
    wh1  Connect To on 682, picking 683 (Robert)
    wh2  ship 681, holding at 683, sent through to beyond 682; one tick
         later the engine had it at 682

An ally's wormhole connected by treaty, from `wh0` with 683 handed to BadGuy
and an alliance (684) in force (22 November 2026):

    sw0  staged; 682 GoodGuy's and 683 BadGuy's, both unlinked
    sw1  Connect To on 682, picking 683: trade 685 proposed (TestBed)
    sw2  BadGuy accepted it (TestBed)

Orders on a planet, from `wo0` given a Colony Ship (684) over unowned 264,
and a Troop Bay frigate (685) and a Bio Bombs frigate (686) over BadGuy's
homeworld 384 (Robert, 22 November 2026):

    tg0  the three ships placed, unordered
    tg1  684 given Colonize Planet on 264
    tw0  tg1 as reopened
    tw1  war declared on BadGuy, 685 given Conquer Planet and 686
         Bio-Bombing Planet (two runs of one module) on 384

Other notes and wormholes are built here from what the client's routines
write.
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
import merge_orders as mo                                       # noqa: E402
import news                                                     # noqa: E402
import inject_civ as icv                                        # noqa: E402

FIX = os.path.join(HERE, 'fixtures', 'unruled')
GOOD, BAD = 656, 660
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def load(name):
    return sp.load_any(os.path.join(FIX, name + '.b64'))


def merged(served, sub, civ='GoodGuy', scans=None):
    notes = {}
    out = mo.merge(served, [(civ, sub)], log=lambda *_: None, notes=notes,
                   scans=scans)
    return out, notes.get(civ, [])


def news_of(blob):
    """GoodGuy's news items, less where each sits in the blob."""
    return [{k: v for k, v in it.items() if k != 'sec'}
            for it in news.items_of(blob, GOOD)]


def note(turn, subject=b'Plan', body=b'Hold 262\r\nthen 263', flags=0, **kw):
    item = {'type': news.NOTE, 'flags': flags, 'turn': turn,
            'title': mo.NOTE_TITLE + subject, 'body': body, 'title_id': 0,
            'body_id': 0, 'ids': [], 'position': mo.NO_POSITION}
    item.update(kw)
    return news.build_item(item)


def worm(oid, owner, link=0):
    return sp.build_section(b'WORM', 0, struct.pack(
        '<IfffIII', oid, 10.0, 0.0, 20.0, owner, 0, 0) + struct.pack(
        '<I', link))


def with_worms(blob, *sections):
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    return sp.replace_payload(blob, tree, glxy,
                              bytes(blob[glxy.payload:glxy.end])
                              + b''.join(sections))


def main():
    d4, sc1, sc2 = load('d4'), load('sc1'), load('sc2')

    print('scans')
    check('the client wrote two reports',
          [r[:4] for r in mo.reports_of(sc1, GOOD)], [b'SCPL', b'SCRO'])
    check('and deleting one left the route scan',
          mo.reports_of(sc2, GOOD), mo.reports_of(sc1, GOOD)[1:])
    out, notes = merged(d4, sc1)
    check('scans the referee did not answer are refused and named',
          any("answered it online" in n for n in notes))
    check('    no report is taken', mo.reports_of(out, GOOD), [])
    check('    and the news after them still reads',
          len(news.items_of(out, GOOD)), len(news.items_of(d4, GOOD)))
    out, notes = merged(d4, sc1, scans={'GoodGuy': 2})
    check('two reports the referee answered are expected, with no note',
          notes, [])
    check('    and still not taken from the submission',
          mo.reports_of(out, GOOD), [])
    out, notes = merged(d4, sc1, scans={'GoodGuy': 1})
    check('a report past what the referee answered is refused',
          [n for n in notes if 'answered it online' in n][:1],
          ['1 scan report(s) DROPPED: a scan is carried only when the referee '
           'answered it online; the scans stay in stock'])
    check('    another civ\'s answers do not count for this one',
          any('2 scan report(s) DROPPED' in n
              for n in merged(d4, sc1, scans={'BadGuy': 2})[1]))
    out, notes = merged(sc1, sc2)
    check('deleting a report is carried', (mo.reports_of(out, GOOD), notes),
          (mo.reports_of(sc2, GOOD), []))
    swapped = mo.set_reports(sc1, GOOD, mo.reports_of(sc1, GOOD)[::-1])
    out, notes = merged(sc1, swapped)
    check('reports served come back reordered: refused',
          any('reordered' in n for n in notes))
    check('    and kept as served', mo.reports_of(out, GOOD),
          mo.reports_of(sc1, GOOD))

    print('\nnotes the client wrote')
    s0, nt1, nt2 = load('s0'), load('nt1'), load('nt2')
    turn = icv.turn_of(s0)
    written = news.items_of(nt1, GOOD)[-1]
    check('the Note dialog wrote one item, dated this turn, naming nothing',
          (len(news.items_of(nt1, GOOD)) - len(news.items_of(s0, GOOD)),
           written['type'], written['turn'], written['title'],
           mo.note_refusal(written, turn)),
          (1, news.NOTE, turn, b'Note - Subject: testnote', None))
    for served, sub, what in ((s0, nt1, 'writing it'),
                              (nt1, nt2, 'editing it'),
                              (s0, nt2, 'both in one turn')):
        out, notes = merged(served, sub)
        check(f'{what} is carried as the client saved it',
              (news_of(out), notes), (news_of(sub), []))
    edited = news.items_of(nt2, GOOD)[-1]
    check('the edit kept the item and set only the important mark',
          (edited['body'], edited['flags'], mo.note_refusal(edited, turn)),
          (b'hellotestnoteedit', news.IMPORTANT, None))
    check('and no string was added', news.read_table(nt2),
          news.read_table(s0))

    print("\nthe News screen's settings (USSE, sent with SVUS)")
    import canonical
    settings = [mo.settings_orders(s0, b, GOOD) for b in (s0, nt1, nt2)]
    check('using the screen changed one setting, byte 27, and only once',
          ([i for i in range(mo.END_VOTE_AT)
            if settings[1] and settings[1][i] != load('s0')[
                mo._usse(s0, GOOD).payload + i]],
           mo.settings_orders(nt1, nt2, GOOD)), ([27], None))
    out, notes = merged(s0, nt1)
    check('it is carried, and the whole save is the client\'s',
          (canonical.canonical_hash(out), notes),
          (canonical.canonical_hash(nt1), []))
    voted = mo.set_end_vote(nt1, GOOD, 1)
    out, notes = merged(s0, voted)
    check('the vote beside it is still judged on its own',
          (mo.end_vote_of(out, GOOD), mo.settings_orders(s0, out, GOOD)),
          (1, settings[1]))

    print('\nnotes')
    out, notes = merged(s0, news.append_item(s0, GOOD, note(turn)))
    items = news.items_of(out, GOOD)
    check('a note written is carried', (items[-1]['type'], notes),
          (news.NOTE, []))
    check('    inline, adding no string', news.read_table(out),
          news.read_table(s0))
    for what, kw, why in (
            ('a note dated another turn', {'turn': turn - 1}, 'as the client'),
            ('a note naming a civ', {'ids': [BAD]}, 'as the client'),
            ('a note naming a string', {'title_id': 5}, 'as the client'),
            ('a note with no subject', {'subject': b''}, 'empty'),
            ('a note with no text', {'body': b''}, 'empty'),
            ('a note holding a control byte', {'body': b'a\x07b'},
             'control character')):
        out, notes = merged(s0, news.append_item(
            s0, GOOD, note(**dict({'turn': turn}, **kw))))
        check(f'{what} is refused', any(why in n for n in notes))
        check('    and not written', len(news.items_of(out, GOOD)),
              len(news.items_of(s0, GOOD)))

    served = news.append_item(s0, GOOD, note(turn - 3, flags=1))
    at = len(news.items_of(served, GOOD)) - 1
    edit = news.replace_item(served, GOOD, at, note(
        turn, b'Plan B', b'Hold 263', flags=1))
    out, notes = merged(served, edit)
    check('a note edited is carried', (news.items_of(out, GOOD)[at]['body'],
                                       notes), (b'Hold 263', []))
    first = news.items_of(s0, GOOD)[0]
    rewritten = news.replace_item(s0, GOOD, 0, note(turn))
    out, notes = merged(s0, rewritten)
    check('a message rewritten as a note is refused',
          any('does not begin with the items served' in n for n in notes))
    check('    and stays', news.items_of(out, GOOD)[0]['type'], first['type'])

    print('\nwormholes the client connected')
    wh0, wh1 = load('wh0'), load('wh1')
    check('the client saved both unlinked, then each naming the other',
          ({k: v[1] for k, v in mo.wormholes(wh0).items()},
           {k: v[1] for k, v in mo.wormholes(wh1).items()}),
          ({682: 0, 683: 0}, {682: 683, 683: 682}))
    drift = [mo.wormholes(b)[682][2][mo.WORM_DRIFT:mo.WORM_DRIFT + 4]
             for b in (wh0, wh1)]
    check('the f32 after the owner changed between the saves on its own',
          drift[0] != drift[1])
    out, notes = merged(wh0, wh1)
    check('Connect To is carried',
          ({k: v[1] for k, v in mo.wormholes(out).items()}, notes),
          ({682: 683, 683: 682}, []))
    wh2 = load('wh2')
    out, notes = merged(wh1, wh2)
    check('a course through the pair is carried as a ship order',
          (mo.wormholes(out)[682][1], notes), (683, []))
    spun = bytearray(wh0)
    at = wh0.index(mo.wormholes(wh0)[682][2]) + mo.WORM_DRIFT
    spun[at:at + 4] = drift[1]
    out, notes = merged(wh0, bytes(spun))
    check('a save where only that f32 moved changes nothing and says nothing',
          (out == wh0, notes), (True, []))

    print("\nan ally's wormhole connected by treaty (SHWH, 0x0055D1C0)")
    sw0, sw1, sw2 = load('sw0'), load('sw1'), load('sw2')

    def links(blob):
        return {k: v[1] for k, v in mo.wormholes(blob).items()}
    proposed = mo.read_trdt(mo.treaties(sw1)[685])
    pair = struct.pack('<II', 682, 683)
    check('Connect To on an ally\'s wormhole proposes a trade naming both, '
          'in both lists',
          (proposed['kind'], proposed['demands'], proposed['offers'],
           links(sw1)),
          (0, [(b'SHWH', pair)], [(b'SHWH', pair)], {682: 0, 683: 0}))
    out, notes = merged(sw0, sw1)
    check('the proposal is kept', (mo.treaties(out), notes),
          (mo.treaties(sw1), []))
    check('accepting it links the pair in the client', links(sw2),
          {682: 683, 683: 682})
    out, notes = merged(sw1, sw2, civ='BadGuy')
    check('and the merge links them too, nothing refused',
          (links(out), mo.treaties(out), notes),
          ({682: 683, 683: 682}, mo.treaties(sw2), []))

    peace = sw1
    for a, b in ((GOOD, BAD), (BAD, GOOD)):
        peace = mo.set_stance(peace, a, b, 3)
    taken = mo.treaties(sw2)[685]
    sub = mo.set_treaties(peace, [r for k, r in mo.treaties(peace).items()
                                  if k != 685] + [taken])
    out, notes = merged(peace, sub, civ='BadGuy')
    check('between civs not allied it is accepted and links nothing',
          (mo.read_trdt(mo.treaties(out)[685])['status'], links(out), notes),
          (mo.ACCEPTED, {682: 0, 683: 0}, []))

    rival = mo.set_treaties(sw2, list(mo.treaties(sw2).values()) + [
        mo.with_treaty_id(mo.treaties(sw1)[685], 690)])
    served = mo.set_high_water(mo.set_treaties(
        sw1, list(mo.treaties(sw1).values()) + [
            mo.with_treaty_id(mo.treaties(sw1)[685], 690)]), 690)
    out, notes = merged(served, sw2, civ='BadGuy')
    check('another pending proposal naming either wormhole is erased '
          '(0x0055B480), and its absence is not named',
          (sorted(mo.treaties(out)), notes), ([684, 685], []))
    check('    while one left in the submission is erased all the same',
          sorted(mo.treaties(merged(served, mo.set_high_water(rival, 690),
                                    civ='BadGuy')[0])), [684, 685])

    linked = merged(sw1, sw2, civ='BadGuy')[0]
    war = sp.build_section(b'TRTY', 2, struct.pack('<I', 700) + sp.build_section(
        b'TRDT', 1, struct.pack('<IIIIiiI', BAD, GOOD, 1, 3, -1, 304, 304)
        + b'\0\0' + struct.pack('<II', 0, 0)))
    unlinked = mo.set_wormhole_link(mo.set_wormhole_link(linked, 682, 0), 683, 0)
    declared = mo.set_high_water(mo.set_treaties(unlinked, [war]), 700)
    for a, b in ((GOOD, BAD), (BAD, GOOD)):
        declared = mo.set_stance(declared, a, b, 1)
    out, notes = merged(linked, declared, civ='BadGuy')
    check('a war that ends the treaty unlinks the pair (0x0055D290)',
          (sorted(mo.treaties(out)), links(out), notes),
          ([700], {682: 0, 683: 0}, []))

    turn = icv.turn_of(sw0)

    def offer(*items):
        body = struct.pack('<IIIIiiI', GOOD, BAD, 0, 0, -2, -1, turn) + b'\0\0'
        body += struct.pack('<I', 0) + struct.pack('<I', len(items)) + b''.join(
            sp.build_section(b'SHWH', 0, i) for i in items)
        return mo.set_high_water(mo.set_treaties(
            sw0, list(mo.treaties(sw0).values()) + [sp.build_section(
                b'TRTY', 2, struct.pack('<I', 700)
                + sp.build_section(b'TRDT', 1, body))]), 700)
    for label, sub, why in (
            ('two wormholes of one civ', offer(struct.pack('<II', 682, 682)),
             "not one of each party's"),
            ('something that is not a wormhole',
             offer(struct.pack('<II', 682, 384)), 'not a wormhole'),
            ('one id', offer(struct.pack('<I', 682)), 'not two wormholes')):
        out, notes = merged(sw0, sub)
        check(f'a connection naming {label} is refused',
              (sorted(mo.treaties(out)), sum(why in n for n in notes)),
              ([684], 1))

    print('\nan order that needs a module (0x0050A110)')
    wo0, wo1 = load('wo0'), load('wo1')
    out, notes = merged(wo0, wo1)
    check('Create Wormhole on two generator ships is carried',
          ([mo.order_type(mo.ship_index(out)[s][1]) for s in (680, 681)],
           notes), ([8, 8], []))

    def on_design(blob, ship, design):
        """`blob` with one ship rebuilt on another design."""
        out = bytearray(blob)
        glxy = next(sp.parse_blob(blob)[0].find('GLXY'))
        for sec in glxy.find('SHIP'):
            if struct.unpack_from('<I', blob, sec.payload)[0] == ship:
                shpr = next(sec.find('SHPR'))
                struct.pack_into('<I', out, shpr.payload, design)
        return bytes(out)

    def as_order(blob, ship, kind):
        """`blob` with one ship's order type changed."""
        out = bytearray(blob)
        glxy = next(sp.parse_blob(blob)[0].find('GLXY'))
        for sec in glxy.find('SHIP'):
            if struct.unpack_from('<I', blob, sec.payload)[0] == ship:
                dyno = next(sec.find('DYNO'))
                out[dyno.start + 8 + mo.DYNO_ORBIT + 8] = kind
        return bytes(out)

    scout = 675                     # GoodGuy's design with no module
    out, notes = merged(on_design(wo0, 680, scout),
                        on_design(wo1, 680, scout))
    check('on a ship with no generator it is refused and named',
          notes, ['ship 680: order DROPPED, Create Wormhole needs a Wormhole '
                  'Generator, and the ship carries none'])
    check('    and the ship keeps its order as served, the other its new one',
          [mo.order_type(mo.ship_index(out)[s][1]) for s in (680, 681)],
          [0, 8])
    for kind, module in ((3, 'Colony Module'), (5, 'Troop Bay'),
                         (6, 'Bio Bombs')):
        out, notes = merged(wo0, as_order(wo1, 680, kind))
        check(f'{mo.ORDER_NAMES[kind]} without a {module} is refused',
              any(f'needs a {module}' in n for n in notes))
    out, notes = merged(wo0, as_order(wo1, 680, 1))
    check('an order needing no module is not judged here', notes, [])
    designs, modules = mo.ship_designs(wo0), mo.design_modules(wo0)
    check('a fleet passes when any ship of it has the module',
          (mo.order_module_refusal(mo.ship_index(wo1)[680][1], [669, 680],
                                   designs, modules),
           'no ship of the fleet' in mo.order_module_refusal(
               mo.ship_index(wo1)[680][1], [669, 670], designs, modules)),
          (None, True))

    print('\nthe planet an order names, as the client offers it')
    tg0, tg1, tw0, tw1 = (load(n) for n in ('tg0', 'tg1', 'tw0', 'tw1'))
    places, owners = mo.planet_places(tg0)
    check('each order names the planet its route ends on',
          [mo.order_target(mo.ship_index(b)[s][1], places)
           for b, s in ((tg1, 684), (tw1, 685), (tw1, 686))], [264, 384, 384])
    out, notes = merged(tg0, tg1)
    check('Colonize Planet on an unowned planet is carried', notes, [])
    out, notes = merged(tw0, tw1)
    check('war, Conquer Planet and Bio-Bombing Planet on its planet are '
          'carried', notes, [])

    def aimed(blob, ship, planet):
        """`blob` with one ship's order moved onto another planet."""
        spot = {p: k for k, p in places.items()}
        dyno = mo.ship_index(blob)[ship][1]
        was = mo.order_target(dyno, places)
        moved = dyno.replace(spot[was], spot[planet]).replace(
            struct.pack('<I', was), struct.pack('<I', planet))
        return mo.replace_dyno(blob, ship, moved)

    own, theirs = 261, 384
    for served, sub, ship, planet, why in (
            (tg0, tg1, 684, own, "an unowned planet, and planet 261 is "
                                 "the civ's own"),
            (tg0, tg1, 684, theirs, "an unowned planet, and planet 384 is "
                                    "another civ's"),
            (tw0, tw1, 685, own, "another civ's planet, and planet 261 is "
                                 "the civ's own"),
            (tw0, tw1, 686, 264, "another civ's planet, and planet 264 is "
                                 "unowned")):
        kind = mo.ORDER_NAMES[mo.order_type(mo.ship_index(sub)[ship][1])]
        out, notes = merged(served, aimed(sub, ship, planet))
        check(f'{kind} on planet {planet} is refused',
              [n for n in notes if f'ship {ship}:' in n],
              [f'ship {ship}: order DROPPED, {kind} is offered only on '
               f'{why}'])

    print('\nwormholes')
    base = with_worms(s0, worm(900, GOOD), worm(901, GOOD), worm(902, GOOD),
                      worm(903, BAD))
    check('the fixture holds four', sorted(mo.wormholes(base)),
          [900, 901, 902, 903])
    pair = mo.set_wormhole_link(mo.set_wormhole_link(base, 900, 902), 902, 900)
    out, notes = merged(base, pair)
    w = mo.wormholes(out)
    check('two of its own connected are carried',
          ((w[900][1], w[902][1], w[901][1]), notes), ((902, 900, 0), []))
    one = mo.set_wormhole_link(base, 900, 902)
    out, notes = merged(base, one)
    check('a link one way only is refused',
          any('does not name it back' in n for n in notes))
    check('    and nothing is linked', mo.wormholes(out)[900][1], 0)
    theirs = mo.set_wormhole_link(mo.set_wormhole_link(base, 900, 903), 903,
                                  900)
    out, notes = merged(base, theirs)
    check("one of another civ's is refused, a treaty connects those",
          any('by treaty' in n for n in notes))
    check('    and neither is linked', (mo.wormholes(out)[900][1],
                                        mo.wormholes(out)[903][1]), (0, 0))
    out, notes = merged(pair, mo.set_wormhole_link(mo.set_wormhole_link(
        pair, 900, 901), 901, 900))
    check('relinking a connected one is refused',
          any('already linked' in n for n in notes))
    out, notes = merged(base, with_worms(base, worm(904, GOOD)))
    check('a wormhole that appears is refused',
          any('added' in n for n in notes))

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
