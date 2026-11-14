"""
test_display_merge.py: a civ's display choices for the civs it has met are carried
==================================================================================
    python server\tests\test_display_merge.py

Each civ's `KNPL` record for another civ holds whether that civ's territory and
ships are drawn and its colour (`+29` to `+35`, read off the client's writer
0x532F20 and reader 0x5332E0). The merge carries the submitter's own records and
nothing in any other civ's. `fixtures/diplomacy/d0` is GoodGuy and BadGuy at
peace on turn 180, both records at the constructor's defaults.
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

FIX = os.path.join(HERE, 'fixtures')
GOOD, BAD = 656, 660
PASS, FAIL = [], []

GREEN_SHOWN = bytes([1, 1, 0, 0x00, 0xFF, 0x00, 0xFF])  # territory, ships, custom green
DEFAULTS = bytes([0, 1, 1])


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def served():
    return sp.load_any(os.path.join(FIX, 'diplomacy', 'd0.b64'))


def display(blob, civ, other):
    return mo.display_of(mo.knpl_records(blob, civ)[other][1])


def merged(blob, *subs):
    notes = {}
    out = mo.merge(blob, list(subs), log=quiet, notes=notes)
    return out, notes


def main():
    base = served()
    print('\nthe fixture')
    check("GoodGuy's record for BadGuy is at the defaults",
          display(base, GOOD, BAD)[:3], DEFAULTS)
    check("and BadGuy's for GoodGuy", display(base, BAD, GOOD)[:3], DEFAULTS)

    print('\nthe submitter changes its own record')
    sub = mo.set_display(base, GOOD, BAD, GREEN_SHOWN)
    out, notes = merged(base, ('GoodGuy', sub))
    check('the territory, ships and colour are carried',
          display(out, GOOD, BAD), GREEN_SHOWN)
    check('with no note', notes.get('GoodGuy'), None)
    check('the stance is untouched', mo.stances(out)[(GOOD, BAD)],
          mo.stances(base)[(GOOD, BAD)])
    check("BadGuy's record is untouched", display(out, BAD, GOOD),
          display(base, BAD, GOOD))
    at = mo.knpl_records(base, GOOD)[BAD][0] + mo.KNPL_SHOW_TERRITORY_AT
    was = display(base, GOOD, BAD)
    check('nothing else in the blob moves',
          [i for i in range(len(base)) if base[i] != out[i]]
          if len(base) == len(out) else 'length changed',
          [at + i for i in range(len(was)) if was[i] != GREEN_SHOWN[i]])
    check('the log line names the colour',
          mo.describe_display(GREEN_SHOWN),
          'territory shown, ships shown, colour 0x00FF00')

    print("\nthe submitter changes another civ's record")
    sub = mo.set_display(base, BAD, GOOD, GREEN_SHOWN)
    out, notes = merged(base, ('GoodGuy', sub))
    check('it is not carried', display(out, BAD, GOOD), display(base, BAD, GOOD))

    print('\nback to the colour by stance')
    sub = mo.set_display(base, GOOD, BAD, GREEN_SHOWN)
    once, _ = merged(base, ('GoodGuy', sub))
    back = mo.set_display(once, GOOD, BAD, bytes([0, 1, 1]) + GREEN_SHOWN[3:])
    out, notes = merged(once, ('GoodGuy', back))
    check('the flag returns to 1 and the territory is hidden again',
          display(out, GOOD, BAD)[:3], DEFAULTS)

    print('\na flag that is not 0 or 1')
    bad = mo.set_display(base, GOOD, BAD, bytes([2]) + GREEN_SHOWN[1:])
    out, notes = merged(base, ('GoodGuy', bad))
    check('is refused', display(out, GOOD, BAD), display(base, GOOD, BAD))
    check('with a note naming the civ and the value',
          notes.get('GoodGuy'),
          ['display of BadGuy DROPPED, a flag reads 2'])

    print('\nboth civs at once')
    a = mo.set_display(base, GOOD, BAD, GREEN_SHOWN)
    red = bytes([0, 0, 0, 0x00, 0x00, 0xFF, 0xFF])
    b = mo.set_display(base, BAD, GOOD, red)
    out, notes = merged(base, ('GoodGuy', a), ('BadGuy', b))
    check("GoodGuy's choice is carried", display(out, GOOD, BAD), GREEN_SHOWN)
    check("and BadGuy's", display(out, BAD, GOOD), red)
    check('the merged galaxy parses', sp.parse_blob(out)[0].tag, b'SAVE')

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
