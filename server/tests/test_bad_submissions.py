"""
test_bad_submissions.py , one bad submission cannot stall the galaxy
====================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_bad_submissions.py

At four hours a turn, a submission that ends the tick costs every player in the
galaxy their turn. Measured against the referee as it stood, using the real
turn 9 of `server/galaxy_demo`: a submission truncated in half, an empty one,
and bytes that are not a save each raised out of `merge_orders.merge` and took
`resolve_turn` with them. A submission from another turn and one from another
galaxy did something worse than raise: they merged, and an order out of a state
nobody is playing was written into the galaxy.

Every case here is run with two good submissions beside the bad one, because a
referee that dropped everything would pass the half of this that only shows a
bad submission being refused. The good pair is real on one side and synthetic
on the other: `submissions/0009/DemoPlayer.b64` is what a person played, and
Neighbor's is the served turn with one planet renamed, which is the smallest
order the merge takes.

One note in here is not about this at all and is left standing: with two good
submissions and no bad one, whoever is merged second is told `research:
DROPPED, belongs to` the first. That rule compares the submission against the
accumulating blob rather than against the state as served, unlike every other
rule in `merge_orders`, so it fires on a player who did nothing. It predates
this file and is not N2's to fix; the checks below ask that no player is told
their SUBMISSION was dropped rather than that they were told nothing.

The engine tick is stubbed. What is under test is which submissions reach the
merge and what the galaxy and the players are left with, none of which needs a
turn to be computed; a real `resolve_turn` needs the client and the save server
and is not what this file can hold to account.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import merge_orders as mo
import player_turn
import referee
from turn_store import TurnStore

GALAXY = os.path.join(ROOT, 'server', 'galaxy_demo')
# A different galaxy, not a different turn of this one: two civs rather than
# three, 108 systems rather than 32, 547 planets rather than 165. `duel3.b64`
# looks like a candidate and is not one; it is this same galaxy at turn 3, and
# the turn check answers it before the galaxy check is reached.
OTHER_GALAXY = os.path.join(ROOT, 'server', 'saves',
                            'save_066_20260918_015123_g0_t110.b64')
TURN = 9
ROSTER = ['DemoPlayer', 'Neighbor', 'BadGuy']

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def said(lines, fragment):
    return any(fragment in line for line in lines)


def set_turn(blob, turn):
    """The same blob carrying a different turn number, so a fixture from
    another galaxy can be offered for the turn being closed and the galaxy
    check is what answers rather than the turn check."""
    tree = sp.parse_blob(blob)
    glob = next(tree[0].find('GLOB'))
    out = bytearray(blob)
    struct.pack_into('<I', out, glob.payload, turn)
    return bytes(out)


def fixtures():
    served = sp.load_any(os.path.join(GALAXY, 'turns', f'{TURN:04d}.b64'))
    good = sp.load_any(os.path.join(GALAXY, 'submissions', f'{TURN:04d}',
                                    'DemoPlayer.b64'))
    old = sp.load_any(os.path.join(GALAXY, 'submissions', '0007',
                                   'DemoPlayer.b64'))
    # Neighbor's turn: the state as served with one of their planets renamed.
    neighbor = mo.set_planet_name(served, 138, b'Border Post')
    other = set_turn(sp.load_any(OTHER_GALAXY), TURN)
    return served, good, neighbor, old, other


def bad_cases(good, old, other):
    """{label: (bytes, what the note has to say)}.

    The empty case is the decoded blob being empty rather than the file being
    missing: a submission that is there and carries nothing.
    """
    return {
        'truncated': (good[:len(good) // 2], 'not a save blob'),
        'empty': (b'', 'it is empty'),
        'not a save': (b'this is not a save, it is a sentence' * 8,
                       'not a save blob'),
        'oversized': (good + b'\0' * referee.MAX_SUBMISSION_BYTES,
                      'over the'),
        'another turn': (old, f'is turn 7, not turn {TURN}'),
        'another galaxy': (other, 'not this galaxy'),
    }


# ── the screen, one submission at a time ─────────────────────────────────────
def test_screen(served, good, neighbor, old, other):
    print('screen_submission')

    # The control. A screen that refuses everything passes every check below
    # and loses the galaxy its turn, so the two good submissions are asked
    # first and asked again inside every case that follows.
    check('the submission a person really played passes the screen',
          referee.screen_submission(served, 'DemoPlayer', good, TURN),
          (True, ''))
    check('a synthetic order on the served state passes the screen',
          referee.screen_submission(served, 'Neighbor', neighbor, TURN),
          (True, ''))

    for label, (sub, wanted) in bad_cases(good, old, other).items():
        ok, why = referee.screen_submission(served, 'DemoPlayer', sub, TURN)
        check(f'{label}: dropped', ok, False)
        check(f'{label}: the note says why ({wanted!r})', wanted in why)

    ok, why = referee.screen_submission(served, 'Stranger', good, TURN)
    check('a civ the galaxy does not hold is dropped rather than raising',
          (ok, 'not a civ in this galaxy' in why), (False, True))

    # The galaxy check has to be about the galaxy and not about the bytes: a
    # submission that changed nothing at all is still this galaxy.
    check('an untouched copy of the served state is not called a foreign one',
          referee.screen_submission(served, 'Neighbor', served, TURN),
          (True, ''))


# ── the merge, which is what a submission reaches once the screen passes ─────
def test_merge_guard(served, good, neighbor):
    print('merge drops one submission, not the turn')

    alone = mo.merge(served, [('Neighbor', neighbor)], log=lambda *a: None)

    notes, log = {}, []
    both = mo.merge(served, [('Neighbor', neighbor),
                             ('DemoPlayer', good[:len(good) // 2])],
                    log=log.append, notes=notes)
    check('the bad submission is refused and the reason names it',
          said(notes.get('DemoPlayer', []), 'could not be read'))
    check("the good submission in the same merge still applies",
          mo.planet_index(both)[138][3][1], b'Border Post')
    check('nothing of the refused submission is left in the blob',
          both, alone)
    check('the merged blob is still a blob', sp.parse_blob(both) != [])
    check('the player whose orders were taken is told nothing',
          notes.get('Neighbor'), None)

    # Order reversed, because a submission that fails is rolled back to what
    # the blob held when it was reached, and reaching it second is the case
    # where that matters.
    notes = {}
    other_way = mo.merge(served, [('DemoPlayer', good[:len(good) // 2]),
                                  ('Neighbor', neighbor)],
                         log=lambda *a: None, notes=notes)
    check('the same holds when the bad submission is merged first',
          other_way, alone)
    check('and it is still the one that is refused',
          sorted(notes), ['DemoPlayer'])


# ── the turn closing, which is what the galaxy sees ──────────────────────────
def fresh_store(served):
    root = tempfile.mkdtemp(prefix='badsub_')
    s = TurnStore(root)
    s.start(served, ROSTER, turn_seconds=1800, turn=TURN)
    return s


def close_turn(store):
    """`resolve_turn` with the engine stubbed. Returns the turn it published.

    `tick` is the only part that needs a client. Stubbing it keeps everything
    this file is about: the reading, the screening, the notes, the ordering
    between them, and what lands in the store.
    """
    def fake_tick(blob, turns=1, secs=10, save_dir=None, log=print, **kw):
        return set_turn(blob, TURN + 1)

    real_tick, real_ready = referee.tick, player_turn.save_path_ready
    referee.tick = fake_tick
    player_turn.save_path_ready = lambda: (True, '')
    try:
        new_turn = referee.resolve_turn(store, log=lambda *a: None)
    finally:
        referee.tick, player_turn.save_path_ready = real_tick, real_ready
    return new_turn


def test_resolve(served, good, neighbor, old, other):
    print('the turn closes for everyone else')
    demo = mo.civ_by_name(served, 'DemoPlayer')['oid']
    served_topic = mo.research_of(served, demo)
    played_topic = mo.research_of(good, demo)
    check('the fixture is one where the good submission changes something',
          played_topic != served_topic)

    cases = dict(bad_cases(good, old, other))
    # A submission whose bytes never decode, which the store raises on while
    # listing rather than handing over. Written as a file rather than through
    # `submit`, because `submit` encodes whatever it is given and this case is
    # about what arrives already wrecked.
    cases['unreadable on the wire'] = (None, 'not a save the referee can '
                                             'decode')

    for label, (sub, wanted) in cases.items():
        store = fresh_store(served)
        store.submit('DemoPlayer', TURN, good)
        store.submit('Neighbor', TURN, neighbor)
        if sub is None:
            with open(store.submission_path('BadGuy', TURN), 'wb') as f:
                f.write(b'not base64 and not a save either')
        else:
            store.submit('BadGuy', TURN, sub)

        new_turn = close_turn(store)
        published = store.turn_blob(new_turn)

        check(f'{label}: the turn still closes', new_turn, TURN + 1)
        check(f'{label}: the orders a person played are in the next turn',
              mo.research_of(published, demo), played_topic)
        check(f'{label}: so are the other player\'s',
              mo.planet_index(published)[138][3][1], b'Border Post')
        check(f'{label}: the player who sent it is told',
              said(store.note('BadGuy', TURN), wanted))
        told = [line for civ in ('DemoPlayer', 'Neighbor')
                for line in store.note(civ, TURN)
                if 'submission DROPPED' in line]
        check(f'{label}: nobody else is told their submission was dropped',
              told, [])

        rec = store.archive_record(TURN)
        check(f'{label}: the archive says it was dropped and why',
              wanted in rec.get('dropped', {}).get('BadGuy', ''))
        check(f'{label}: to the rest of the galaxy they did not submit',
              (sorted(rec['submitted']), rec['missing']),
              (['DemoPlayer', 'Neighbor'], ['BadGuy']))


def test_save_path_still_first(served, good):
    """N1: a referee that cannot save changes nothing at all.

    The drop notes are collected earlier in `resolve_turn` than the merge's
    are, so this is the check that they are still written no earlier than the
    turn is closable. A referee that noted a drop and then refused to tick
    would leave the player a note about a turn that did not close, and write it
    again on the next attempt.
    """
    print('a referee that cannot save leaves nothing behind')
    store = fresh_store(served)
    store.submit('DemoPlayer', TURN, good)
    store.submit('BadGuy', TURN, b'')

    real_tick, real_ready = referee.tick, player_turn.save_path_ready
    ticked = []
    referee.tick = lambda *a, **kw: ticked.append(1)
    player_turn.save_path_ready = lambda: (False, 'no server on port 8888')
    try:
        referee.resolve_turn(store, log=lambda *a: None)
        raised = False
    except SystemExit as exc:
        raised = 'no server on port 8888' in str(exc)
    finally:
        referee.tick, player_turn.save_path_ready = real_tick, real_ready

    check('it refuses to close the turn', raised)
    check('it does not tick', ticked, [])
    check('no note is written for the dropped submission',
          store.note('BadGuy', TURN), [])
    check('no turn is published', store.has_turn(TURN + 1), False)
    check('and the clock has not moved', store.current()[0], TURN)


def main():
    if not os.path.exists(os.path.join(GALAXY, 'turns', f'{TURN:04d}.b64')):
        print(f'no fixture galaxy at {GALAXY}')
        return 1
    served, good, neighbor, old, other = fixtures()
    test_screen(served, good, neighbor, old, other)
    test_merge_guard(served, good, neighbor)
    test_resolve(served, good, neighbor, old, other)
    test_save_path_still_first(served, good)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    if FAIL:
        for n in FAIL:
            print('  FAILED:', n)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
