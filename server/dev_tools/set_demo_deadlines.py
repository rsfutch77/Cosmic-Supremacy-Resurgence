"""
set_demo_deadlines.py , put the UI demo galaxies back where the stall check
needs them.

    python server\\dev_tools\\set_demo_deadlines.py
    python server\\dev_tools\\set_demo_deadlines.py --show

`server\\uidemo` is the operator's fixture for looking at the Galaxies page. Its
deadlines are absolute timestamps, so the arrangement they were set up to
produce lasts about an hour and then decays: every galaxy drifts past its stall
threshold and the page reads `stopped` for all of them. That is the code being
right and the fixture being old, but on screen the two look identical, which is
the failure worth avoiding. Run this before looking at the page.

What it arranges, and why these six:

    outpost   15m turn   12 min over   stopped
    frontier   1h turn   70 min over   stopped
    lapsed     4h turn   12 min over   over, and not stopped
    crowded    4h turn    5 min over   over, and not stopped

`outpost` against `lapsed` is the pair that carries it: the same twelve minutes
late, opposite verdicts, because one is late for a fifteen-minute turn and the
other for a four-hour one. That is the whole claim, that lateness is judged
against the turn it is late for rather than against a constant. `crowded`
against `lapsed` is then one turn length at two distances, which says the
threshold is read at all rather than the row being stopped for some other
reason.

The two that are not stopped are placed close to their deadlines rather than
close to their thresholds, so the arrangement survives being looked at. The
first row to change is `lapsed`, 48 minutes after this runs. The tool prints
that, so a page examined later is not read as a page that disagrees.

`retired` is closed and reads closed whatever its clock says, so it is left
alone and stands as the control.

**`sandbox` is never written.** The referee worker holds it and is advancing it,
and a second writer would be racing the process whose output the page is meant
to be showing. It is also the only row that should be counting down rather than
overdue, which it already is.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEMO = os.path.join(REPO, 'server', 'uidemo')

# Seconds past each galaxy's deadline to place it. Read with the turn length
# beside it: the threshold is max(300, min(3600, turn_seconds / 4)).
OVERDUE = {
    'outpost': 12 * 60,
    'frontier': 70 * 60,
    'lapsed': 12 * 60,
    'crowded': 5 * 60,
}

# Held by the referee worker, or closed. Neither is ours to move.
LEAVE = ('sandbox', 'retired')


def threshold(turn_seconds) -> float:
    """How late a galaxy may be before the page calls it stopped.

    The same expression the launcher uses. Repeated rather than imported
    because this is a fixture tool and importing the launcher to set up the
    fixture it is checked against would let one mistake agree with itself.
    """
    return max(300, min(3600, (turn_seconds or 0) / 4))


def state_path(galaxy: str) -> str:
    return os.path.join(DEMO, galaxy, 'state.json')


def read(galaxy: str):
    try:
        with open(state_path(galaxy), encoding='utf-8') as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return None


def describe(galaxy: str, state: dict, now: float) -> str:
    deadline = state.get('deadline')
    seconds = state.get('turn_seconds')
    if not deadline:
        return f'{galaxy:<10} no deadline'
    over = now - deadline
    limit = threshold(seconds)
    if state.get('status') == 'closed':
        verdict = 'closed'
    elif over <= 0:
        verdict = f'{-over / 60:.0f} min left'
    elif over > limit:
        verdict = 'STOPPED'
    else:
        verdict = 'over, not stopped'
    return (f'{galaxy:<10} turn_seconds={seconds or 0:<6} '
            f'{over / 60:+.0f} min  limit {limit / 60:.0f} min  {verdict}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('--show', action='store_true',
                    help='report where each galaxy stands, write nothing')
    args = ap.parse_args(argv)

    if not os.path.isdir(DEMO):
        print(f'no demo galaxies at {DEMO}', file=sys.stderr)
        return 1

    now = time.time()
    if not args.show:
        for galaxy, late in OVERDUE.items():
            state = read(galaxy)
            if state is None:
                print(f'  {galaxy}: no state to move', file=sys.stderr)
                continue
            state['deadline'] = now - late
            # Written whole rather than patched in place. These are small files
            # and nothing else is writing them, the one process that does write
            # one being the worker on `sandbox`, which is not in OVERDUE.
            with open(state_path(galaxy), 'w', encoding='utf-8') as fh:
                json.dump(state, fh, indent=2)
                fh.write('\n')

    print('where the page should read them:')
    stopped = 0
    # When the first row that is not stopped will become stopped. A page read
    # after that is a correct page showing a different arrangement, and without
    # this it is indistinguishable from a page that disagrees, which is the
    # confusion the whole tool exists to remove.
    turns_over = []
    for galaxy in sorted(os.listdir(DEMO)):
        state = read(galaxy)
        if state is None:
            continue
        line = describe(galaxy, state, now)
        held = '  (left alone)' if galaxy in LEAVE else ''
        print('  ' + line + held)
        if 'STOPPED' in line:
            stopped += 1
        elif 'not stopped' in line:
            turns_over.append((
                state['deadline'] + threshold(state.get('turn_seconds')) - now,
                galaxy))
    print(f'\n{stopped} galaxies should read stopped.')
    if turns_over:
        left, galaxy = min(turns_over)
        print(f'This holds for {left / 60:.0f} more minutes, until {galaxy} '
              f'crosses its own threshold. Run it again after that.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
