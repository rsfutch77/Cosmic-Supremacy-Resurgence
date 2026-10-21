"""
test_setup_prompts_merge.py: a confirmed setup prompt stays confirmed
=====================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_setup_prompts_merge.py

The homeworld prompt and the civ-trait prompt are one-time: each OK sets a
flag in OWPR that keeps the prompt away. The referee carries that confirm into
the next turn, once, and only as the dialog could have made it; without it a
player was offered both prompts again on every served turn.

The valid confirms here are the ones measured on a live client: the traits
Food-Consumption +2 clicks (4), Food Output 5 (28) and Production Output 2
(15), and the homeworld Space +20 clicks (300 -> 450) and Food +10 (32 -> 42).
Uses the tracked turn-0 fixture `t4f`, where neither civ has confirmed.
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

FIX = os.path.join(HERE, 'fixtures', 'tier4')
PASS, FAIL = [], []

TRAITS = (4, 0, 28, 15, 0, 0, 0, 0, 0, 0)
UNTOUCHED = (0,) * 10


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def homeworld_plpr(plpr, rates=None, space=None):
    """A PLPR payload with the homeworld dialog's fields replaced."""
    out = bytearray(plpr)
    for off, v in zip(mo.HOMEWORLD_RATE_OFFS, rates or ()):
        struct.pack_into('<b', out, off, v)
    if space is not None:
        struct.pack_into('<H', out, mo.HOMEWORLD_SPACE_OFF, space)
    return bytes(out)


def submit(served, civ, traits=None, homeworld=None, flags=('civ', 'homeworld')):
    """A submission as the client writes one after confirming.

    `civ` is the object id. `homeworld` is (planet, rates, space). `flags`
    are the ones the OKs set.
    """
    blob = served
    if traits is not None:
        blob = mo.set_traits(blob, civ, traits)
    if homeworld is not None:
        planet, rates, space = homeworld
        plpr = mo.planet_index(served)[planet][2]
        blob = mo.set_homeworld(blob, planet, homeworld_plpr(plpr, rates, space))
    for which in flags:
        blob = mo.set_setup_flag(blob, civ, which)
    return blob


def merged(served, *subs):
    notes = {}
    out = mo.merge(served, list(subs), log=quiet, notes=notes)
    return out, notes


def main():
    served = sp.load_any(os.path.join(FIX, 't4f.dat'))
    me, them = 190, 194                       # DemoPlayer, BadGuy
    home, their_home = 136, 189
    plpr = lambda blob, p: mo.planet_index(blob)[p][2]        # noqa: E731

    print('what the fixture holds')
    check('neither civ has confirmed', [mo.setup_flags(served, c)
                                         for c in (me, them)], [(0, 0), (0, 0)])
    check('the budgets are the shipped ones',
          (mo.gset_value(served, 'civilization_changes'),
           mo.gset_value(served, 'homeworld_changes')), (5, 30))
    check('the homeworld is the planet founded first',
          mo.homeworlds(mo.planet_index(served), me), {home})

    print('the rules match the confirms measured on a live client')
    check('the live trait confirm is acceptable',
          mo.traits_acceptable(TRAITS, 5), (True, ''))
    check('and the live homeworld confirm',
          mo.homeworld_acceptable(plpr(served, home), homeworld_plpr(
              plpr(served, home), (42, 30, 40), 450), 30), (True, ''))

    print('both confirms are carried')
    sub = submit(served, me, TRAITS, (home, (42, 30, 40), 450))
    out, notes = merged(served, ('DemoPlayer', sub))
    check('nothing is refused', notes.get('DemoPlayer', []), [])
    check('the traits are in the merged turn', mo.traits_of(out, me), TRAITS)
    check('the homeworld too', mo.homeworld_fields(plpr(out, home)),
          ((42, 30, 40), 450))
    check('and both flags, so neither prompt comes back',
          mo.setup_flags(out, me), (1, 1))
    check('the other civ is untouched', (mo.setup_flags(out, them),
                                          mo.traits_of(out, them)),
          ((0, 0), UNTOUCHED))
    check('the blob still parses', bool(sp.parse_blob(out)), True)

    print('two civs confirming in one turn, either order')
    sub_them = submit(served, them, (0, 0, 0, 0, 0, 0, 0, 0, 0, 34),
                      (their_home, (32, 42, 58), 350))
    for order in ((('DemoPlayer', sub), ('BadGuy', sub_them)),
                  (('BadGuy', sub_them), ('DemoPlayer', sub))):
        out, notes = merged(served, *order)
        check(f'{order[0][0]} first: both are carried',
              [mo.setup_flags(out, c) for c in (me, them)], [(1, 1), (1, 1)])
        check('with nothing refused', sum(notes.values(), []), [])

    print('a confirm the dialog could not have made is refused whole')
    cases = {
        'traits over budget': (submit(
            served, me, (0, 0, 28, 28, 0, 0, 0, 0, 0, 0),
            flags=('civ',)), 'civilization traits DROPPED, the traits spend 10'),
        'traits under budget': (submit(
            served, me, (0, 0, 8, 0, 0, 0, 0, 0, 0, 0),
            flags=('civ',)), 'civilization traits DROPPED, the traits spend 1'),
        'a trait value no click count gives': (submit(
            served, me, (0, 0, 30, 0, 0, 0, 0, 0, 0, 0),
            flags=('civ',)), 'food output +30 is not a value'),
        'traits without the confirm': (submit(
            served, me, TRAITS, flags=()),
            "changed without the prompt's confirm"),
        'homeworld over budget': (submit(
            served, me, homeworld=(home, (52, 30, 40), 450),
            flags=('homeworld',)), 'the homeworld spends 40'),
        'a rate the dialog does not reach': (submit(
            served, me, homeworld=(home, (12, 30, 70), 450),
            flags=('homeworld',)), 'food rate 12 is outside 25..80'),
        'space that is not whole clicks': (submit(
            served, me, homeworld=(home, (42, 30, 40), 452),
            flags=('homeworld',)), 'is not a click count'),
        'homeworld without the confirm': (submit(
            served, me, homeworld=(home, (42, 30, 40), 450),
            flags=()), "changed without the prompt's confirm"),
    }
    for label, (bad, fragment) in cases.items():
        out, notes = merged(served, ('DemoPlayer', bad))
        said = notes.get('DemoPlayer', [])
        check(f'{label}: refused, saying why',
              any(fragment in n for n in said), True)
        check('  and nothing of it is carried, the flags included',
              (mo.setup_flags(out, me), mo.traits_of(out, me),
               mo.homeworld_fields(plpr(out, home))),
              ((0, 0), UNTOUCHED, mo.homeworld_fields(plpr(served, home))))

    print('a prompt confirmed once is not confirmed again')
    once, _ = merged(served, ('DemoPlayer', sub))
    again = submit(once, me, (0, 0, 0, 28, 8, 0, 0, 0, 0, 0),
                   (home, (42, 60, 40), 450))
    out, notes = merged(once, ('DemoPlayer', again))
    said = notes.get('DemoPlayer', [])
    check('the traits are refused', any('traits DROPPED, they were already'
                                        in n for n in said), True)
    check('the homeworld is refused', any('homeworld customisation DROPPED, it '
                                          'was already' in n for n in said), True)
    check('and the first confirm stands',
          (mo.traits_of(out, me), mo.homeworld_fields(plpr(out, home))),
          (TRAITS, ((42, 30, 40), 450)))

    print('only the homeworld can be customised')
    # The served index as if this civ also held a colony founded on turn 6.
    planets = dict(mo.planet_index(served))
    _owner, prod, colony, name = planets[their_home]
    colony = bytearray(colony)
    struct.pack_into('<I', colony, mo.FOUNDED_OFF, 6)
    planets[their_home] = (me, prod, bytes(colony), name)
    sub_planets = dict(planets)
    sub_planets[their_home] = (me, prod, homeworld_plpr(
        bytes(colony), (42, 30, 40), 450), name)
    taken, refusals = mo.setup_orders(
        served, mo.set_setup_flag(served, me, 'homeworld'), me, planets,
        sub_planets)
    check('a colony founded later is refused', (taken, refusals),
          ([], [f'homeworld customisation DROPPED, planet {their_home} is not '
                f'the homeworld']))

    print('another submission does not lose its fleet to the merge before it')
    g = sp.load_any(os.path.join(FIX, 't4g.dat'))
    _out, notes = merged(served, ('BadGuy', served), ('DemoPlayer', g))
    check('the second submission is read, not dropped whole',
          [n for n in notes.get('DemoPlayer', []) if 'could not be read' in n], [])
    check('and its fleet is reported as usual',
          any('fleet 204' in n for n in notes.get('DemoPlayer', [])), True)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
