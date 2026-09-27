"""
test_research_merge.py , two players may both change research in one turn
=========================================================================
The rule under test refuses a submission that alters **another** civ's research
topic. A player holds the whole galaxy, so they could edit anyone's, and this is
what stops it.

It used to compare the submission against the blob as it stood rather than
against the state that was served. Nothing in the merge ever writes another
civ's research, so those two differ only where an earlier submission in the same
turn was honestly applied, and the effect was that the second player to merge in
any turn where two of them changed topic was told

    research: DROPPED, belongs to <the other player>

for something they had not touched. Their own orders still applied, so the harm
was a note accusing a player of an edit they did not make, in a galaxy where
notes are the only thing telling them what the referee refused.

Every check here runs both submissions together, because the bug needs two: with
one submission the accumulating blob and the served blob are the same thing and
the old code passed.
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
    # Two civs that both have readable research, which is what the rule needs.
    pair = [o for o in owners if mo.research_of(served, o['oid']) is not None][:2]
    if len(pair) < 2:
        print('  [SKIP] this galaxy has fewer than two civs with research')
        return 0
    a, b = pair
    print(f'two civs changing research in one turn: '
          f'{a["name"]!r} and {b["name"]!r}')

    sub_a = bump(served, a['oid'])
    sub_b = bump(served, b['oid'])

    # The precondition. Without it the rest passes for the wrong reason.
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
        # This is the check the old code failed: the second one to merge was
        # told it had touched the first one's research.
        for who in (first, second):
            said = [n for n in notes.get(who['name'], []) if 'research' in n]
            check(f'{who["name"]} is told nothing about research', said, [])
        # And the orders themselves still land, so this is not a merge that
        # passed by refusing to do anything.
        check('both topics are in the merged blob',
              [mo.research_of(out, a['oid']) == mo.research_of(sub_a, a['oid']),
               mo.research_of(out, b['oid']) == mo.research_of(sub_b, b['oid'])],
              [True, True])

    # The control. The rule still has to refuse a player who edits somebody
    # else's topic, or the fix above would be indistinguishable from deleting it.
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
