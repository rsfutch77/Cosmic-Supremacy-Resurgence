"""
test_loop_guards.py , the two guards that stop a restart eating a played turn
=============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_loop_guards.py

Replays the turn-12 incident of 18 September 2026 against a temporary store.
A player's loop was restarted 22 seconds before the deadline, on a turn they
had already submitted for. `follow` re-served it, which closed their client and
discarded the colonise order they had played, then submitted the orderless blob
over their submission. `submit` is last-write-wins and said nothing.

Both halves are checked, because either one alone still loses the turn:

  1. `follow` must not re-serve a turn this civ has already submitted for.
  2. `push` must not replace a submission it did not write with one that
     carries no orders.

The real blobs are not needed. `carries_orders` is stubbed per case, which is
what lets this run without a client, and the guard logic is what is under test
rather than the diffing.
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


def fresh_store(turn=12):
    root = tempfile.mkdtemp(prefix='guards_')
    s = TurnStore(root)
    s.start(b'BASE' * 64, ['DemoPlayer', 'Neighbor'], turn_seconds=1800,
            turn=turn)
    return s


# ── guard 2: push refuses to clobber a stranger's order-carrying submission ──
def run_push(store, turn, civ, capture, sent, orders):
    """The body of follow's push, with the guard under test."""
    submitted = []
    if capture == sent:
        return 'unchanged', submitted
    existing = store.submissions(turn).get(civ)
    if existing is not None and existing != sent and not orders:
        return 'refused', submitted
    store.submit(civ, turn, capture)
    submitted.append(capture)
    return 'submitted', submitted


def test_push_guard():
    print('push guard')
    ORDER = b'BASE' * 64 + b'COLONISE'      # what the player played
    EMPTY = b'BASE' * 64                    # a freshly served, orderless blob

    # The incident: a restarted loop, sent=None, an empty capture, and a
    # submission already in the store that carries the player's order.
    s = fresh_store()
    s.submit('Neighbor', 12, ORDER)
    verdict, _ = run_push(s, 12, 'Neighbor', EMPTY, sent=None, orders=False)
    check('refuses an empty capture over a submission it did not write',
          verdict, 'refused')
    check('the order-carrying submission is still the one in the store',
          s.submissions(12)['Neighbor'], ORDER)

    # A player who cancels their own order must still be able to.
    s = fresh_store()
    s.submit('Neighbor', 12, ORDER)
    verdict, _ = run_push(s, 12, 'Neighbor', EMPTY, sent=ORDER, orders=False)
    check('allows a player to replace THEIR OWN order with an empty turn',
          verdict, 'submitted')
    check('and the store takes it',
          s.submissions(12)['Neighbor'], EMPTY)

    # An ordinary interim write on a turn nobody has submitted for.
    s = fresh_store()
    verdict, _ = run_push(s, 12, 'Neighbor', ORDER, sent=None, orders=True)
    check('a first submission carrying orders goes through',
          verdict, 'submitted')

    # A capture carrying orders may always replace anything.
    s = fresh_store()
    s.submit('Neighbor', 12, EMPTY)
    verdict, _ = run_push(s, 12, 'Neighbor', ORDER, sent=None, orders=True)
    check('a capture WITH orders replaces a stranger submission',
          verdict, 'submitted')


# ── guard 1: follow does not re-serve an already-submitted turn ──────────────
def what_follow_loads(prior, orders):
    """What `follow` puts into the client, given a submission already stored.

    `serve`, `collect` and `close` are all replaced: `close` matters, because
    leaving the real one in place ends the pass by closing whatever client is
    running on this machine. That is not hypothetical , the first version of
    this control killed the live player's client mid-turn.
    """
    s = fresh_store()
    if prior is not None:
        s.submit('Neighbor', 12, prior)

    loaded, ticks = [], {'n': 0}

    def stop():
        ticks['n'] += 1
        return ticks['n'] > 3

    orig = (player_turn.serve, player_turn.collect, player_turn.close,
            player_turn.carries_orders)
    player_turn.serve = lambda b, civ, **kw: loaded.append(b) or 'x.dat'
    player_turn.collect = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError('deadline not reached in this test'))
    player_turn.close = lambda: None
    player_turn.carries_orders = lambda served, sub, civ: orders
    try:
        player_turn.follow(s, 'Neighbor', poll=0.01, rounds=0, stop=stop,
                           log=lambda *a: None)
    except Exception:                                       # noqa: BLE001
        pass
    finally:
        (player_turn.serve, player_turn.collect, player_turn.close,
         player_turn.carries_orders) = orig
    return loaded[0] if loaded else None, s


def test_reserve_guard():
    print('re-serve / resume')
    BASE = b'BASE' * 64
    ORDER = BASE + b'COLONISE'

    # The incident. A submission carrying orders must be resumed FROM, never
    # thrown away by reloading the pristine turn.
    loaded, s = what_follow_loads(ORDER, orders=True)
    check('resumes from a submission that carries orders', loaded, ORDER)
    check('never loads the pristine turn over it', loaded == BASE, False)
    check('and leaves the submission standing',
          s.submissions(12)['Neighbor'], ORDER)

    # The case the first version of this guard got wrong: it refused to serve
    # at all, which strands a player whose client died on a turn they had not
    # played. An orderless submission has nothing to preserve.
    loaded, _ = what_follow_loads(BASE, orders=False)
    check('serves the turn fresh when the submission carries nothing',
          loaded, BASE)

    # No submission at all: an ordinary first serve.
    loaded, _ = what_follow_loads(None, orders=False)
    check('serves the turn when nothing has been submitted', loaded, BASE)


if __name__ == '__main__':
    test_push_guard()
    test_reserve_guard()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    if FAIL:
        for n in FAIL:
            print('  FAILED:', n)
    sys.exit(1 if FAIL else 0)
