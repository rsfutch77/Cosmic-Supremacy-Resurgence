"""
test_design_edits_merge.py: renaming, modifying and deleting ship designs
=========================================================================
    python server\\tests\\test_design_edits_merge.py

`fixtures/unruled` continues the session `test_facilities_merge.py` describes,
from `s6`, each command run through the client's own routine:

    d1   RNSD: design 664 'probe1' renamed 'Pathfinder'
    d2   MOSD: design 665 'probe2' given the name and parts of 675 'scout'
    d3   DLSD: design 673 'e1x2' deleted, the civ's design count lowered
    d4   RNSD: 665 renamed back to 'probe2', as the editor keeps the name

The editor never renames a design (d2 is refused for that), so d1 to d4 is
what a player could do: 665's parts changed and 673 deleted. Designs 657, 667
and 668 have ships built to them and are in use.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp                                        # noqa: E402
import merge_orders as mo                                       # noqa: E402
import inject_design as idg                                     # noqa: E402

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


def merged(served, sub, civ='GoodGuy'):
    notes = {}
    out = mo.merge(served, [(civ, sub)], log=lambda *_: None, notes=notes)
    return out, notes.get(civ, [])


def mine(blob):
    return {oid: (d['name'], d['parts'], d['record'][-1])
            for oid, d in mo.design_index(blob).items() if d['civ'] == GOOD}


def renamed(blob, oid, name):
    d = mo.design_index(blob)[oid]
    rec = idg.build_dsgn(oid, name, d['parts'], d['claims'])
    return mo.replace_design(blob, oid, rec[:-1] + d['record'][-1:])


def rebuilt(blob, oid, parts):
    d = mo.design_index(blob)[oid]
    rec = idg.build_dsgn(oid, d['name'], parts, d['claims'])
    return mo.replace_design(blob, oid, rec[:-1] + d['record'][-1:])


def main():
    s6, d1, d2, d3, d4 = (load(n) for n in ('s6', 'd1', 'd2', 'd3', 'd4'))

    print('what the client did')
    check('a rename changes only the name',
          (mine(d1)[664][0], mine(d1)[664][1:]), ('Pathfinder',
                                                  mine(s6)[664][1:]))
    check('a delete removes the design', 673 in mine(d3), False)
    check('designs with ships built are in use',
          sorted(mo.designs_in_use(s6, GOOD)), [657, 667, 668])

    print('\nmerged')
    out, notes = merged(s6, d1)
    check('the rename is carried', (mine(out), notes), (mine(d1), []))
    out, notes = merged(d1, d2)
    check("a design given another's name is refused",
          any('unique within a civ' in n for n in notes))
    check('    and keeps its record', mine(out)[665], mine(d1)[665])
    out, notes = merged(d1, d4)
    check('a modified unused design and a deletion are carried',
          (mine(out), notes), (mine(d4), []))
    check('    the design count follows',
          sorted(mo.designs_of(out, GOOD)), sorted(mo.designs_of(d4, GOOD)))

    print('\nin use')
    parts = dict(mo.design_index(s6)[667]['parts'], engines=[1])
    out, notes = merged(s6, rebuilt(s6, 667, parts))
    check('modifying a design a ship is built to is refused',
          any('opens it read-only' in n for n in notes))
    gone = mo.remove_design(s6, GOOD, 657)
    out, notes = merged(s6, gone)
    check('deleting one is refused', any('greys Delete out' in n
                                         for n in notes))
    check('    and it stays', 657 in mine(out), True)
    out, notes = merged(s6, renamed(s6, 667, 'Fighter Mk2'))
    check('renaming one is carried', (mine(out)[667][0], notes),
          ('Fighter Mk2', []))

    print('\nnames')
    for name, why in (('unnamed', 'reserved'), (' scout2', 'trims'),
                      ('SCOUT', 'unique'), ('x' * 33, 'buffer'),
                      ('a\x01b', 'printable')):
        out, notes = merged(s6, renamed(s6, 664, name))
        check(f'{name[:12]!r} is refused', any(why in n for n in notes))
    out, notes = merged(s6, renamed(s6, 664, 'x' * 32))
    check('32 characters are carried', (mine(out)[664][0], notes),
          ('x' * 32, []))

    print("\nanother civ's")
    theirs = sorted(mo.designs_of(s6, BAD))[0]
    out, notes = merged(s6, mo.remove_design(s6, BAD, theirs))
    check("deleting another civ's design is refused",
          any("another civ's" in n for n in notes))
    out, notes = merged(s6, renamed(s6, theirs, 'Mine Now'))
    check("renaming one is refused", any("another civ's" in n for n in notes))

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
