"""
test_refusal_notes.py , a dropped order has to reach the player who made it
===========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_refusal_notes.py

Every refusal was already written down. It was written down on the referee's
console, and on a player's machine that is nowhere. Two of the first
two-machine rehearsal's six findings were silent for exactly that reason: a
system rename and a conscription, both accepted by the client, both dropped by
the merge, both simply gone the next turn with nothing to look at.

The route is store-shaped, because the store is the only thing both sides
touch: `merge` records each refusal against the civ that made it, `referee`
writes that beside the turn, and `player_turn.report_refusals` prints it into
the launcher's log pane. No new UI, which is what was asked for.

The blobs here are short byte strings. What is under test is the route, not the
diffing, and `merge` is exercised against real captures in
`test_ship_designs.py`.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import player_turn
from turn_store import TurnStore

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def fresh_store(turn=6):
    root = tempfile.mkdtemp(prefix='notes_')
    s = TurnStore(root)
    s.start(b'BASE' * 64, ['Powerhouse', 'Laptop'], turn_seconds=1800,
            turn=turn)
    return s


def main():
    print('the store carries a note')
    s = fresh_store()
    check('a turn nobody was refused anything on has no note',
          s.note('Powerhouse', 6), [])

    reasons = ["system 193: rename DROPPED, Powerhouse holds 0 of the 1 "
               "settled planet(s) in a system of 4, not a majority",
               "people DROPPED, 9 people served and 10 came back"]
    s.put_note('Powerhouse', 6, reasons)
    check('a note written comes back as it was written',
          s.note('Powerhouse', 6), reasons)
    check('and only to the player it was left for',
          s.note('Laptop', 6), [])
    check('notes are per turn', s.note('Powerhouse', 7), [])

    # A note is written before the clock moves, so it is already there when the
    # player's loop notices the next turn.
    s.publish(7, b'NEXT' * 64)
    check('publishing the next turn does not disturb it',
          s.note('Powerhouse', 6), reasons)

    print('the player is told')
    said = []
    n = player_turn.report_refusals(s, 'Powerhouse', 6, log=said.append)
    check('report_refusals returns how many were refused', n, 2)
    check('it says how many', any('refused 2' in line for line in said))
    for reason in reasons:
        check(f'it prints: {reason[:34]}...',
              any(reason in line for line in said))
    check('every line is tagged with the civ, for the launcher log pane',
          all(line.startswith('[Powerhouse]') for line in said))

    quiet = []
    n = player_turn.report_refusals(s, 'Laptop', 6, log=quiet.append)
    check('a player who was refused nothing is told nothing',
          (n, quiet), (0, []))

    print('it does not take the turn loop down with it')

    class Broken:
        def note(self, civ, turn):
            raise OSError('the share went away')

    said = []
    n = player_turn.report_refusals(Broken(), 'Powerhouse', 6, log=said.append)
    check('an unreadable store is reported, not raised', n, 0)
    check('and it says so', any('could not read' in line for line in said))

    print('the merge records refusals against the civ that made them')
    import merge_orders
    check('merge takes a notes argument',
          'notes' in merge_orders.merge.__code__.co_varnames)
    import referee
    check('apply_orders passes it through',
          'notes' in referee.apply_orders.__code__.co_varnames)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
