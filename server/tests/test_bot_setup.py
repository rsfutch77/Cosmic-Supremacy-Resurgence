"""
test_bot_setup.py: a bot's setup confirms are ones the referee carries
======================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_bot_setup.py

`bot_setup.confirm` writes the civ-trait and homeworld confirms into a bot's
capture. Each case runs the result through `merge_orders.merge`, which carries
a confirm only as the dialog could have made it, so a pick the dialog could
not make fails here rather than being dropped on a live turn.
Uses the tracked turn-0 fixture `t4f`, where neither civ has confirmed.
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
import bot_setup                                                # noqa: E402

FIX = os.path.join(HERE, 'fixtures', 'tier4')
PASS, FAIL = [], []

UNTOUCHED = (0,) * 10


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def main():
    served = sp.load_any(os.path.join(FIX, 't4f.dat'))
    me, them = 190, 194                       # DemoPlayer, BadGuy
    home = 136
    plpr = lambda blob, p: mo.planet_index(blob)[p][2]        # noqa: E731

    print('the trait pick spends production first, then food')
    check('the shipped 5 changes', bot_setup.pick_traits(5),
          (0, 0, 15, 21, 0, 0, 0, 0, 0, 0))
    check('no changes picks nothing', bot_setup.pick_traits(0), UNTOUCHED)
    check('both maxed, then science', bot_setup.pick_traits(11),
          (0, 0, 28, 28, 8, 0, 0, 0, 0, 0))
    check('a budget nothing can hold', bot_setup.pick_traits(16), None)
    check('every budget it picks is one the dialog allows',
          [b for b in range(16)
           if not mo.traits_acceptable(bot_setup.pick_traits(b), b)[0]], [])

    print('the homeworld pick alternates production and food')
    check('the shipped 30 changes, space raised to the floor',
          bot_setup.pick_homeworld(plpr(served, home), 30),
          ((47, 45, 40), 350))
    check('an odd budget gives production the extra',
          bot_setup.pick_homeworld(plpr(served, home), 3)[0], (33, 32, 40))
    check('a budget past both caps', bot_setup.pick_homeworld(
        plpr(served, home), 200), None)

    print('a capture confirmed by the bot is carried whole')
    sub = bot_setup.confirm(served, served, 'DemoPlayer', log=quiet)
    out = mo.merge(served, [('DemoPlayer', sub)], log=quiet,
                   notes=(notes := {}))
    check('nothing is refused', notes.get('DemoPlayer', []), [])
    check('the traits are in the merged turn', mo.traits_of(out, me),
          (0, 0, 15, 21, 0, 0, 0, 0, 0, 0))
    check('the homeworld too', mo.homeworld_fields(plpr(out, home)),
          ((47, 45, 40), 350))
    check('both flags, so neither prompt comes back', mo.setup_flags(out, me),
          (1, 1))
    check('the other civ is untouched',
          (mo.setup_flags(out, them), mo.traits_of(out, them)),
          ((0, 0), UNTOUCHED))
    check('the blob still parses', bool(sp.parse_blob(out)), True)

    print('a prompt already confirmed is left alone')
    said = []
    again = bot_setup.confirm(out, out, 'DemoPlayer', log=said.append)
    check('confirmed as served: no change', again, out)
    check('and nothing said', said, [])
    by_client = mo.set_setup_flag(served, me, 'civ')
    mine = bot_setup.confirm(served, by_client, 'DemoPlayer', log=quiet)
    check('confirmed by the client: its traits stand',
          mo.traits_of(mine, me), UNTOUCHED)
    check('the homeworld is still confirmed', mo.setup_flags(mine, me),
          (1, 1))

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
