"""
test_end_vote.py: an end-galaxy vote reaches the referee and the operator page
==============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_end_vote.py

The Overview Screen's "Vote to End the Galaxy" tick-box writes the last byte of
the civ's `USSE`, 1 cast (Memory report, "vote to end galaxy"). The merge
carries the submitter's own vote, the referee archives the votes standing in
the turn it publishes, and the operator page counts them against the roster.

Merge checks use `fixtures/diplomacy/d0` (GoodGuy and BadGuy, turn 180). The
referee and page checks use turn 9 of `server/galaxy_demo` with the engine tick
stubbed, as `test_bad_submissions.py` does.
"""
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp                                        # noqa: E402
import merge_orders as mo                                       # noqa: E402
import operator_view as ov                                      # noqa: E402
import player_turn                                              # noqa: E402
import referee                                                  # noqa: E402
from turn_store import TurnStore                                # noqa: E402

FIX = os.path.join(HERE, 'fixtures')
GALAXY = os.path.join(ROOT, 'server', 'galaxy_demo')
TURN = 9
ROSTER = ['DemoPlayer', 'Neighbor', 'BadGuy']
GOOD, BAD = 656, 660
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(*_a, **_k):
    pass


def merged(blob, *subs):
    notes = {}
    out = mo.merge(blob, list(subs), log=quiet, notes=notes)
    return out, notes


def with_byte(blob, civ, value):
    """The blob with a civ's vote byte written raw, any value."""
    usse = mo._usse(blob, civ)
    out = bytearray(blob)
    out[usse.payload + mo.END_VOTE_AT] = value
    return bytes(out)


def test_merge():
    print('merge: the submitter\'s own vote is carried')
    served = sp.load_any(os.path.join(FIX, 'diplomacy', 'd0.b64'))
    check('the fixture is served with no votes',
          mo.end_votes(served), {'GoodGuy': False, 'BadGuy': False})

    out, notes = merged(served, ('GoodGuy', mo.set_end_vote(served, GOOD, 1)))
    check('a vote cast is carried', mo.end_votes(out),
          {'GoodGuy': True, 'BadGuy': False})
    check('and nothing is refused', notes, {})
    check('the size does not change', len(out), len(served))
    check('only the vote byte moves',
          [i for i in range(len(out)) if out[i] != served[i]],
          [mo._usse(served, GOOD).payload + mo.END_VOTE_AT])

    voted = mo.set_end_vote(served, GOOD, 1)
    out, _ = merged(voted, ('GoodGuy', served))
    check('a vote withdrawn is carried', mo.end_vote_of(out, GOOD), 0)

    out, notes = merged(served, ('GoodGuy', mo.set_end_vote(served, BAD, 1)))
    check('a vote written into another civ is not carried',
          mo.end_votes(out), {'GoodGuy': False, 'BadGuy': False})

    out, notes = merged(served, ('GoodGuy', with_byte(served, GOOD, 7)))
    check('a vote byte other than 0 or 1 is refused',
          mo.end_vote_of(out, GOOD), 0)
    check('and the sender is told',
          any('end-galaxy vote DROPPED' in n for n in notes.get('GoodGuy', [])))

    out, _ = merged(served, ('GoodGuy', mo.set_end_vote(served, GOOD, 1)),
                    ('BadGuy', mo.set_end_vote(served, BAD, 1)))
    check('two civs voting in one turn are both carried',
          mo.end_votes(out), {'GoodGuy': True, 'BadGuy': True})

    # Control: a submission with no change leaves the votes as served.
    out, _ = merged(voted, ('GoodGuy', voted))
    check('CONTROL: an unchanged vote stays cast', mo.end_vote_of(out, GOOD), 1)


def close_turn(store):
    """`resolve_turn` with the engine stubbed; the tick only moves the turn."""
    def fake_tick(blob, turns=1, secs=10, save_dir=None, log=print, **kw):
        return set_turn(blob, TURN + 1)

    real_tick, real_ready = referee.tick, player_turn.save_path_ready
    referee.tick = fake_tick
    player_turn.save_path_ready = lambda: (True, '')
    try:
        return referee.resolve_turn(store, log=quiet)
    finally:
        referee.tick, player_turn.save_path_ready = real_tick, real_ready


def set_turn(blob, turn):
    """The same blob carrying a different turn number."""
    tree = sp.parse_blob(blob)
    glob = next(tree[0].find('GLOB'))
    out = bytearray(blob)
    struct.pack_into('<I', out, glob.payload, turn)
    return bytes(out)


def test_referee_and_page():
    print('referee: the votes standing are archived and shown')
    served = TurnStore(GALAXY).turn_blob(TURN)
    neighbor = mo.civ_by_name(served, 'Neighbor')['oid']
    root = tempfile.mkdtemp(prefix='endvote_')
    try:
        store = TurnStore(root)
        store.start(served, ROSTER, turn_seconds=1800, turn=TURN)
        store.submit('Neighbor', TURN, mo.set_end_vote(served, neighbor, 1))
        new_turn = close_turn(store)
        rec = store.archive_record(TURN)
        check('the turn closes', new_turn, TURN + 1)
        check('the archive names the civ that voted', rec.get('end_votes'),
              ['Neighbor'])
        check('the published turn carries the vote',
              mo.end_votes(store.turn_blob(new_turn)).get('Neighbor'), True)

        r = ov.galaxy_report(store)
        check('the page counts it against the roster', r['end_votes'],
              {'turn': TURN + 1, 'of': 3, 'voted': ['Neighbor']})
        check('the text page says so',
              '1 of 3 civs (Neighbor), as of turn 10' in ov.render_text([r]))
        check('so does the html page',
              '1 of 3 civs (Neighbor), as of turn 10' in ov.render_html([r]))
        check('a vote is not a problem',
              any('vote' in p for p in r['problems']), False)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print('operator page: a galaxy whose records hold no votes')
    plain = ov.galaxy_report(TurnStore(GALAXY))
    check('CONTROL: an archive written before votes were recorded shows none',
          plain['end_votes'], None)
    check('CONTROL: and the text page has no vote line',
          'end vote' in ov.render_text([plain]), False)


def main():
    test_merge()
    if os.path.isdir(GALAXY):
        test_referee_and_page()
    else:
        print(f'  SKIPPED referee and page checks, no {GALAXY}')
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
