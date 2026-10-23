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


def with_completed(blob, oid, tech):
    """`blob` with one more completed technology on a civ, as the engine
    records it: the count raised and an [id, cost] record appended."""
    import struct
    owpr = mo._owpr(blob, oid)
    body = bytearray(blob[owpr.payload:owpr.end])
    m = struct.unpack_from('<I', body, mo.OWPR_MAP_COUNT_OFF)[0]
    n_at = mo.OWPR_MAP_COUNT_OFF + 4 + m * mo.OWPR_RECORD
    n = struct.unpack_from('<I', body, n_at)[0]
    struct.pack_into('<I', body, n_at, n + 1)
    at = n_at + 4 + n * mo.OWPR_RECORD
    body[at:at] = struct.pack('<II', tech, 1600)
    tree = sp.parse_blob(blob)
    sec = next(s for s in tree[0].find('OWPR') if s.payload == owpr.payload)
    return sp.replace_payload(blob, tree, sec, bytes(body))


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

    # The topic follows the completed list, which grows as research completes.
    print('\na civ with more research completed')
    grown = with_completed(served, a['oid'], 18)
    check('the completed list reads three technologies',
          mo.completed_research(grown, a['oid']),
          mo.completed_research(served, a['oid']) + [18])
    check('and the topic still reads as served',
          mo.research_of(grown, a['oid']), mo.research_of(served, a['oid']))
    out = mo.merge(grown, [(a['name'], bump(grown, a['oid']))], log=quiet)
    check('a topic change lands', mo.research_of(out, a['oid']),
          mo.research_of(bump(grown, a['oid']), a['oid']))
    check('and leaves the completed list alone',
          mo.completed_research(out, a['oid']),
          mo.completed_research(grown, a['oid']))

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
