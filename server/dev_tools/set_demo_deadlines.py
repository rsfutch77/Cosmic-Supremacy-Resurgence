"""
set_demo_deadlines.py: put the UI demo galaxies back where the stall check
needs them.

    python server\\dev_tools\\set_demo_deadlines.py
    python server\\dev_tools\\set_demo_deadlines.py --show

`server\\uidemo` is the operator's fixture for the Galaxies page. Its deadlines
are absolute timestamps, so after about an hour every galaxy drifts past its
stall threshold and reads `stopped`. Run this before looking at the page.

What it arranges:

    outpost   15m turn   12 min over   stopped
    frontier   1h turn   70 min over   stopped
    lapsed     4h turn   12 min over   over, and not stopped
    crowded    4h turn    5 min over   over, and not stopped

`outpost` and `lapsed` are equally late with opposite verdicts, because the
threshold scales with turn length. `crowded` and `lapsed` share a turn length at
two distances. The tool prints how long the arrangement holds.

`retired` is closed and stands as the control. `sandbox` is never written: the
referee worker holds it and is advancing it.
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

# Seconds past each galaxy's deadline to place it.
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

    The same expression the launcher uses, kept separate so the fixture checks
    it independently.
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
            with open(state_path(galaxy), 'w', encoding='utf-8') as fh:
                json.dump(state, fh, indent=2)
                fh.write('\n')

    print('where the page should read them:')
    stopped = 0
    # When the first row that is not stopped will become stopped.
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
