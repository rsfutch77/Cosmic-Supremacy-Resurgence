"""
test_facilities_merge.py: selling facilities, energy links and retiring soldiers
=================================================================================
    python server\\tests\\test_facilities_merge.py

`fixtures/unruled` is one Single Player session on 14 November 2026: the
`diplomacy/d0` galaxy at turn 180 with two hyperspace transmitters on GoodGuy's
planet 261, one on 262 and a receiver and a Central Defense Agency on 263,
loaded and saved after each command, run through the client's own routine:

    s0   as loaded; the engine linked the receiver on 263 to 262
    s1   SLFC: a farm sold on 261, 10 credits
    s2   STEL: 263 linked to 261 as well
    s4   SLFC: the transmitter on 262 sold, 125 credits, and 263 drops it
    s5   RTMI: two of the four soldiers on 265 retired
    s6   SLFC: the receiver on 263 sold, 400 credits, and its links emptied
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


def facilities(blob, planet):
    return mo.facilities_of(mo.planet_index(blob)[planet][2])


def soldiers(blob, planet):
    return mo.military_of(mo.planet_index(blob)[planet][2])[1]


def main():
    s = {n: load(n) for n in ('s0', 's1', 's2', 's4', 's5', 's6')}

    print('what the client did')
    check('a farm sold pays 5% of its 200', mo.credits_of(s['s1'], GOOD)
          - mo.credits_of(s['s0'], GOOD), 10)
    check('a transmitter, of 2500', mo.credits_of(s['s4'], GOOD)
          - mo.credits_of(s['s2'], GOOD), 125)
    check('a receiver, of 8000', mo.credits_of(s['s6'], GOOD)
          - mo.credits_of(s['s5'], GOOD), 400)
    check('the refund is priced as the engine prices it',
          [mo.sale_refund(s['s0'], t, 1) for t in (0, 18, 19)], [10, 125, 400])
    check('selling the transmitter took it out of the receiver\'s links',
          (mo.enli_of(s['s2'])[263], mo.enli_of(s['s4'])[263]),
          ([262, 261], [261]))
    check('selling the receiver emptied them', mo.enli_of(s['s6'])[263], [])

    print('\neach step merged reproduces the client\'s save')
    for a, b in (('s0', 's1'), ('s1', 's2'), ('s2', 's4'), ('s4', 's5'),
                 ('s5', 's6'), ('s0', 's6')):
        out, notes = merged(s[a], s[b])
        check(f'{a} -> {b}: no note', notes, [])
        check(f'{a} -> {b}: credits', mo.credits_of(out, GOOD),
              mo.credits_of(s[b], GOOD))
        check(f'{a} -> {b}: facilities, links and soldiers',
              [(facilities(out, p), mo.enli_of(out)[p], soldiers(out, p))
               for p in (261, 262, 263, 265)],
              [(facilities(s[b], p), mo.enli_of(s[b])[p], soldiers(s[b], p))
               for p in (261, 262, 263, 265)])

    print('\nwhat is refused')
    s0 = s['s0']
    built = mo.set_facilities(s0, 261, [(0, 8), (2, 1), (4, 1), (6, 1),
                                        (18, 2)])
    out, notes = merged(s0, built)
    check('a facility that appears is refused',
          any('no command builds one' in n for n in notes))
    check('    and the planet keeps its list', facilities(out, 261),
          facilities(s0, 261))
    planets = mo.planet_index(s0)
    theirs = next(p for p, v in sorted(planets.items()) if v[0] == BAD)
    sold = mo.set_facilities(s0, theirs, facilities(s0, theirs)[1:])
    out, notes = merged(s0, sold)
    check("selling another civ's facility is refused",
          any('owned by BadGuy' in n for n in notes))
    check('    and no credits move', mo.credits_of(out, GOOD),
          mo.credits_of(s0, GOOD))

    hurried = mo.set_facilities(s0, 263, [(0, 8), (6, 1), (19, 1)])
    prod = planets[263][1]
    check('a hurried scan keeps the Central Defense Agency',
          mo.sale_refusal(_with_kind(prod, b'SCAN', hurried=True), mo.CDA)
          is not None)
    check('a scan not hurried does not', mo.sale_refusal(
        _with_kind(prod, b'SCAN', hurried=False), mo.CDA), None)
    check('a hurried ship keeps the shipyard',
          mo.sale_refusal(_with_kind(prod, b'SHIP', hurried=True),
                          mo.SHIPYARD) is not None)
    check('and a hurried ship does not keep a farm',
          mo.sale_refusal(_with_kind(prod, b'SHIP', hurried=True), 0), None)
    out, _notes = merged(s0, hurried)
    check('the agency sold on a planet building wealth is carried',
          facilities(out, 263), [(0, 8), (6, 1), (19, 1)])

    linked = mo.set_enli(s0, 263, [262, 266])
    out, notes = merged(s0, linked)
    check('a link to a planet with no transmitter is refused',
          any('energy link DROPPED' in n for n in notes))
    check('    and the receiver keeps its links', mo.enli_of(out)[263],
          mo.enli_of(s0)[263])
    out, notes = merged(s0, mo.set_enli(s0, 263, [262, 262]))
    check('a transmitter linked twice is refused',
          any('linked twice' in n for n in notes))
    out, notes = merged(s0, mo.set_enli(s0, 261, [262]))
    check('a link on a planet with no receiver is refused',
          any('no hyperspace receiver' in n for n in notes))
    out, notes = merged(s0, mo.set_enli(s0, 263, []))
    check('every link removed is carried, as the menu allows',
          (mo.enli_of(out)[263], notes), ([], []))

    print('\nretiring soldiers')
    four = soldiers(s0, 265)
    out, notes = merged(s0, mo.set_military(s0, 265, four[1:]))
    check('one of four retired is carried', (len(soldiers(out, 265)), notes),
          (3, []))
    out, notes = merged(s0, mo.set_military(s0, 265, four + four[:1]))
    check('a soldier added from nowhere is refused',
          any('people served' in n for n in notes))

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


def _with_kind(prod, kind, hurried):
    """A PROD with its nested section's tag and hurried byte rewritten."""
    out = bytearray(prod)
    at = 8 + mo.PROD_KIND_OFF + mo._prod_list_len(prod)
    out[at:at + 4] = kind
    out[mo.PROD_HURRIED_OFF] = 1 if hurried else 0
    return bytes(out)


if __name__ == '__main__':
    sys.exit(main())
