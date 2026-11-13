"""
test_fair_start.py: a joiner starts as a new civ, not as a copy of seat one
==========================================================================
    python server\\tests\\test_fair_start.py

A joiner is cloned from seat one. `joins.fresh_start` takes back what the
clone inherited: the score, the civ name and coat of arms dialog 225 chose,
the traits and the trait prompt, and, given the galaxy's turn 0, the homeworld
and the homeworld prompt. `fixtures/messages` m0 and m2 are one galaxy at
turns 180 and 181; neither civ has confirmed a setup prompt, so the test
confirms GoodGuy's, seat one's, before cloning it.
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
import inject_civ as icv                                        # noqa: E402
import merge_orders as mo                                       # noqa: E402
import joins                                                    # noqa: E402

FIX = os.path.join(HERE, 'fixtures', 'messages')
GOOD, BAD = 656, 660
CHOSEN = (0, 0, 28, 0, 0, 0, 0, 0, 0, 0)    # food output raised
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(*_a, **_k):
    pass


def oid_of(blob, name):
    return next(o['oid'] for o in icv.owner_records(blob) if o['name'] == name)


def uid_of(blob, oid):
    owner = next(o for o in icv.owner_records(blob) if o['oid'] == oid)
    return struct.unpack_from('<I', blob, owner['end'] - 4)[0]


def row_score(blob, oid):
    tail = icv.score_rows(blob)[1].get(uid_of(blob, oid))
    return None if tail is None else struct.unpack_from('<I', tail)[0]


def owpr_score(blob, oid):
    owpr = mo._owpr(blob, oid)
    return struct.unpack_from(
        '<I', blob, mo._identity_at(blob, owpr) - icv.SCORE_BACK)[0]


def home(blob, oid):
    planets = mo.planet_index(blob)
    return mo.homeworld_fields(planets[sorted(mo.homeworlds(planets, oid))[0]][2])


def state(blob, oid):
    return {'flags': mo.setup_flags(blob, oid), 'traits': mo.traits_of(blob, oid),
            'home': home(blob, oid), 'name': mo.civ_identity(blob, oid),
            'score': owpr_score(blob, oid)}


def confirmed(blob):
    """GoodGuy with both prompts confirmed, a name and arms chosen."""
    planets = mo.planet_index(blob)
    hw = sorted(mo.homeworlds(planets, GOOD))[0]
    plpr = bytearray(planets[hw][2])
    plpr[mo.HOMEWORLD_RATE_OFFS[0]] = 62
    struct.pack_into('<H', plpr, mo.HOMEWORLD_SPACE_OFF, 350)
    blob = mo.set_homeworld(blob, hw, bytes(plpr))
    blob = mo.set_traits(blob, GOOD, CHOSEN)
    blob = mo.set_setup_flag(blob, GOOD, 'homeworld', 1)
    blob = mo.set_setup_flag(blob, GOOD, 'civ', 1)
    return mo.set_civ_identity(blob, GOOD, b'Goody', 1, 7)


def main():
    m0, m2 = (sp.load_any(os.path.join(FIX, n + '.b64')) for n in ('m0', 'm2'))
    print('\nseat one, confirmed')
    base = confirmed(m2)
    donor = state(base, GOOD)
    check('GoodGuy has both prompts confirmed', donor['flags'], (1, 1))
    check('and a score in OWPR that matches its score row',
          donor['score'], row_score(base, GOOD))
    check('the score is not zero', donor['score'] > 0, True)

    print('\na clone of it')
    joined, planet = icv.add_civ(base, 'Newcomer', log=quiet)
    new = oid_of(joined, 'Newcomer')
    clone = state(joined, new)
    check('inherits the traits, flags, name and score',
          (clone['flags'], clone['traits'], clone['name'], clone['score']),
          (donor['flags'], donor['traits'], donor['name'], donor['score']))
    check("and the donor's homeworld fields", clone['home'], donor['home'])
    joined = icv.set_score(joined, uid_of(joined, new), donor['score'])
    check("and a score row carrying the donor's score",
          row_score(joined, new), donor['score'])

    print('\nfresh_start, given turn 0')
    said = []
    fresh = joins.fresh_start(joined, 'Newcomer', planet, start=m0,
                              log=said.append)
    after = state(fresh, new)
    check('both prompts are offered again', after['flags'], (0, 0))
    check('every trait is untouched', after['traits'], (0,) * len(CHOSEN))
    check('the homeworld is an untouched one', after['home'], home(m0, BAD))
    check('no civ name or arms is chosen', after['name'], (b'', 0, mo.NO_ARMS))
    check('the score is zero in OWPR', after['score'], 0)
    check('and in the score list', row_score(fresh, new), 0)
    check('the incumbents are untouched',
          (state(fresh, GOOD), state(fresh, BAD), row_score(fresh, GOOD)),
          (state(joined, GOOD), state(joined, BAD), row_score(joined, GOOD)))
    check('one line is logged', len(said), 1)
    check('the galaxy parses', sp.parse_blob(fresh)[0].tag, b'SAVE')

    print('\nfresh_start, with no turn 0')
    said = []
    kept = joins.fresh_start(joined, 'Newcomer', planet, start=None,
                             log=said.append)
    after = state(kept, new)
    check('the trait prompt is still offered again', after['flags'], (1, 0))
    check('the homeworld is left as cloned', after['home'], donor['home'])
    check('and says so', any('homeworld left as cloned' in s for s in said),
          True)

    print('\nthe merge takes the joiner\'s own confirm afterwards')
    sub = mo.set_traits(fresh, new, CHOSEN)
    sub = mo.set_setup_flag(sub, new, 'civ', 1)
    notes = {}
    out = mo.merge(fresh, [('Newcomer', sub)], log=quiet, notes=notes)
    check('its traits are taken', mo.traits_of(out, new), CHOSEN)
    check('with no note', notes, {})

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
