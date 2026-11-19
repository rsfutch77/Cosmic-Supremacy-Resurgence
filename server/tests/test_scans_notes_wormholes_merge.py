"""
test_scans_notes_wormholes_merge.py: scans, notes and wormhole links
=====================================================================
    python server\\tests\\test_scans_notes_wormholes_merge.py

Scans use `fixtures/unruled` (see `test_design_edits_merge.py`), from `d4`:

    sc1  DOSC twice: a planetary scan of BadGuy's HQ and a route scan of
         BadGuy's ship 677, two reports after GoodGuy's designs
    sc2  DLSC: the planetary report deleted

Notes and wormholes are built here from what the client's routines write:
driving the note dialog's routine from outside closed the client, and no
galaxy holds a wormhole.
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

    print('\nnotes')
    s0 = load('s0')
    turn = icv.turn_of(s0)
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
