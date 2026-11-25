"""
test_turn_deadline.py: a galaxy's clock keeps its times
=======================================================
    python server\\tests\\test_turn_deadline.py

A turn's deadline is one turn length after the last one, however long the
tick took, so a four-hour galaxy ticks at the same times each day. A slot
that would leave less than half a turn to play is skipped.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import turn_store

PASS, FAIL = [], []
H = 3600.0
FOUR = 4 * H
T = 1_800_000_000.0   # a deadline in 2027


def check(label, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f'  {"ok  " if ok else "FAIL"}  {label}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def main():
    nd = turn_store.next_deadline
    print('the clock keeps its times')
    check('a tick that ran two minutes leaves the next deadline on the slot',
          nd(1000.0, FOUR, now=1000.0 + 20 + 120), 1000.0 + FOUR)
    check('eleven slow ticks in a row do not drift', _chain(11), T + 11 * FOUR)
    check('a held turn moves one turn length on',
          nd(5000.0, H, now=5000.0 + 25), 5000.0 + H)

    print('a late tick skips to the next slot rather than opening short')
    check('three hours late on a four-hour turn skips one slot',
          nd(T, FOUR, now=T + 3 * H), T + 2 * FOUR)
    check('a day late skips to the first slot with half a turn left',
          nd(T, FOUR, now=T + 24 * H + 60), T + 7 * FOUR)
    check('exactly half a turn left keeps the slot',
          nd(T, FOUR, now=T + 2 * H), T + FOUR)

    print('with no previous deadline the clock starts now')
    check('no previous deadline', nd(None, FOUR, now=123.0), 123.0 + FOUR)
    check('a zero previous deadline', nd(0, FOUR, now=123.0), 123.0 + FOUR)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


def _chain(turns):
    deadline = T
    for _ in range(turns):
        deadline = turn_store.next_deadline(deadline, FOUR,
                                            now=deadline + 20 + 150)
    return deadline


if __name__ == '__main__':
    sys.exit(main())
