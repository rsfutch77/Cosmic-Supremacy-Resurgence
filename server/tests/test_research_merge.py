"""
test_research_merge.py: two players may both change research in one turn
=========================================================================
The rule under test refuses a submission that alters another civ's research
topic. It compares against the state that was served, so when two players both
change their own topic in one turn, neither is told the other's change was
theirs. Every check runs both submissions together, in both orders.
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
import inject_civ as icv                                        # noqa: E402
import merge_orders as mo                                       # noqa: E402

GALAXY = os.path.join(ROOT, 'server', 'galaxy_demo', 'turns', '0011.b64')
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def bump(blob, oid):
    """A civ changing its own research topic, the way a player would."""
    cur = mo.research_of(blob, oid)
    first = bytearray(cur[0])
    first[0] = (first[0] + 1) % 256
    return mo.set_research(blob, oid, (bytes(first),) + tuple(cur[1:]))


def main():
    if not os.path.exists(GALAXY):
        print(f'no fixture at {GALAXY}')
        return 0
    served = sp.load_any(GALAXY)
    owners = icv.owner_records(served)
    # Two civs that both have readable research.
    pair = [o for o in owners if mo.research_of(served, o['oid']) is not None][:2]
    if len(pair) < 2:
        print('  [SKIP] this galaxy has fewer than two civs with research')
        return 0
    a, b = pair
    print(f'two civs changing research in one turn: '
          f'{a["name"]!r} and {b["name"]!r}')

    sub_a = bump(served, a['oid'])
    sub_b = bump(served, b['oid'])

    # Precondition.
    check('each submission really does change its own topic',
          [mo.research_of(sub_a, a['oid']) != mo.research_of(served, a['oid']),
           mo.research_of(sub_b, b['oid']) != mo.research_of(served, b['oid'])],
          [True, True])
    check("and neither touches the other's",
          [mo.research_of(sub_a, b['oid']) == mo.research_of(served, b['oid']),
           mo.research_of(sub_b, a['oid']) == mo.research_of(served, a['oid'])],
          [True, True])

    for order, (first, fsub, second, ssub) in {
            'a then b': (a, sub_a, b, sub_b),
            'b then a': (b, sub_b, a, sub_a)}.items():
        print(f'\nmerged {order}')
        notes = {}
        out = mo.merge(served, [(first['name'], fsub), (second['name'], ssub)],
                       log=quiet, notes=notes)
        for who in (first, second):
            said = [n for n in notes.get(who['name'], []) if 'research' in n]
            check(f'{who["name"]} is told nothing about research', said, [])
        # The orders themselves still land.
        check('both topics are in the merged blob',
              [mo.research_of(out, a['oid']) == mo.research_of(sub_a, a['oid']),
               mo.research_of(out, b['oid']) == mo.research_of(sub_b, b['oid'])],
              [True, True])

    # Control: a player who edits somebody else's topic is still refused.
    print('\nthe rule still refuses a real attempt')
    meddle = bump(served, b['oid'])          # a submits b's research changed
    notes = {}
    mo.merge(served, [(a['name'], meddle)], log=quiet, notes=notes)
    said = [n for n in notes.get(a['name'], []) if 'research' in n]
    check(f'{a["name"]} editing {b["name"]}\'s research is refused',
          len(said), 1)
    check('and the refusal names whose it is',
          b['name'] in (said[0] if said else ''), True)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
