"""
test_store_unreachable.py: the turn loop says when it loses the store
=======================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_store_unreachable.py

The launcher judges a galaxy stopped from the last deadline the turn loop
reported. A store this machine cannot read leaves that deadline standing just
as a referee that is off does, so a player with no connection was told the
galaxy had stopped. `follow` now says `unreachable` when a read of the store
fails and `reachable` when one succeeds again, and this checks that it does,
once each, from the wait at the top of a turn and from inside one.

Runs against a temporary TurnStore whose `current()` fails on the reads named.
`serve`, `collect` and `close` are replaced, so no client is started or closed.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'), os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import player_turn
from turn_store import TurnStore

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append(name)
    print(f'  {"ok  " if got == want else "FAIL"}  {name}')
    if got != want:
        print(f'          wanted {want!r}, got {got!r}')


class Flaky:
    """A TurnStore whose `current()` raises on the calls numbered in `fail`."""

    def __init__(self, store, fail):
        self.store, self.fail, self.calls = store, set(fail), 0

    def current(self):
        self.calls += 1
        if self.calls in self.fail:
            raise OSError('<urlopen error [Errno 11001] getaddrinfo failed>')
        return self.store.current()

    def __getattr__(self, name):
        return getattr(self.store, name)


def events_for(fail, ticks=200):
    """The `on_state` kinds `follow` emits over one served turn."""
    root = tempfile.mkdtemp(prefix='unreachable_')
    s = TurnStore(root)
    s.start(b'BASE' * 64, ['DemoPlayer', 'Neighbor'], turn_seconds=1800,
            turn=12)
    store = Flaky(s, fail)
    seen, n = [], {'n': 0}

    def stop():
        n['n'] += 1
        return n['n'] > ticks or store.calls > max(fail, default=0) + 1

    orig = (player_turn.serve, player_turn.collect, player_turn.close)
    player_turn.serve = lambda b, civ, **kw: 'x.dat'
    player_turn.collect = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError('no client in this test'))
    player_turn.close = lambda: None
    try:
        player_turn.follow(store, 'Neighbor', poll=0.01, rounds=0, stop=stop,
                           on_state=lambda kind, **f: seen.append(kind),
                           log=lambda *a: None)
    except Exception:                                       # noqa: BLE001
        pass
    finally:
        player_turn.serve, player_turn.collect, player_turn.close = orig
    return [k for k in seen if k in ('unreachable', 'reachable', 'serving')]


def test_before_the_turn():
    print('the read that finds the turn')
    got = events_for({1, 2, 3})
    check('three failed reads say unreachable once, then reachable once',
          got[:2], ['unreachable', 'reachable'])
    check('and the turn is served after the store comes back',
          got[2:3], ['serving'])


def test_inside_the_turn():
    print('the reads made while a turn is played')
    got = events_for({2, 3})
    check('the first read is fine, so the turn is served with nothing said',
          got[:1], ['serving'])
    check('losing the store mid-turn is said, and so is getting it back',
          got[1:], ['unreachable', 'reachable'])


def test_quiet_when_reachable():
    print('a store that never fails')
    got = events_for(set(), ticks=20)
    check('says neither', [k for k in got if k != 'serving'], [])


if __name__ == '__main__':
    test_before_the_turn()
    test_inside_the_turn()
    test_quiet_when_reachable()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    if FAIL:
        for n in FAIL:
            print('  FAILED:', n)
    sys.exit(1 if FAIL else 0)
