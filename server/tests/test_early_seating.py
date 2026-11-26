"""
test_early_seating.py: a join seated straight into a galaxy's opening turn
===========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_early_seating.py
    server\\.venv\\Scripts\\python.exe server\\tests\\test_early_seating.py galaxy.b64

`referee.seat_early` seats the joins waiting on turn 0 into turn 0 and
republishes it with `reseat`, which keeps the copy it replaced. A submission
sent against that copy is judged against it at the tick. Everything here is
offline, with the engine tick stubbed as in `test_joins.py`.

The galaxy is `client/SinglePlayerGalaxy.dat` set to turn 0, or the blob
given, every civ in it on the roster.
"""
import os
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools'),
          os.path.join(ROOT, 'release')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import inject_civ as icv
import merge_orders as mo
import joins
import referee
import referee_worker
import player_turn
import turn_store
from turn_store import TurnStore

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
DAYS = 3 * 86400
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(*_a):
    pass


def set_turn(blob, turn):
    """The same blob carrying a different turn number."""
    tree = sp.parse_blob(blob)
    glob = next(tree[0].find('GLOB'))
    out = bytearray(blob)
    struct.pack_into('<I', out, glob.payload, turn)
    return bytes(out)


def names(blob):
    return [o['name'] for o in icv.owner_records(blob)]


def oid_of(blob, name):
    return next(o['oid'] for o in icv.owner_records(blob) if o['name'] == name)


def opening_store(blob, turn=0, seconds=DAYS):
    """A folder galaxy on `turn` with every civ in `blob` on the roster."""
    first = set_turn(blob, turn)
    store = TurnStore(tempfile.mkdtemp(prefix='early_'))
    store.start(first, names(first), turn_seconds=seconds, turn=turn)
    return store, first


def write_request(store, name, uid=None, turn=0):
    """A join request exactly as the launcher writes one."""
    import launcher
    req = launcher.join_request(name, uid, '0.8.0+dev', turn=turn)
    return launcher.send_join_request(store, req)


def bump(blob, oid):
    """A civ changing its own research topic, the way a player would."""
    cur = mo.research_of(blob, oid)
    first = bytearray(cur[0])
    first[0] = (first[0] + 1) % 256
    return mo.set_research(blob, oid, (bytes(first),) + tuple(cur[1:]))


def stubbed(fn, *args, **kw):
    """`fn` with the engine tick stubbed to advance the turn number only."""
    def fake_tick(blob, turns=1, secs=10, save_dir=None, log=print, **_kw):
        return set_turn(blob, turn_store.turn_of(blob) + 1)

    real_tick, real_ready = referee.tick, player_turn.save_path_ready
    referee.tick = fake_tick
    player_turn.save_path_ready = lambda: (True, '')
    try:
        return fn(*args, **kw)
    finally:
        referee.tick, player_turn.save_path_ready = real_tick, real_ready


def test_seat(blob):
    print('\na join on the opening turn is seated into it')
    store, first = opening_store(blob)
    before = store.state()
    write_request(store, 'Ada', uid='uidAda')
    got = referee.seat_early(store, log=quiet)
    check('Ada is granted', [o['name'] for o in got], ['Ada'])
    state = store.state()
    check('the turn is still 0', state['turn'], 0)
    check('the deadline is unchanged', state['deadline'], before['deadline'])
    check('Ada is on the roster', 'Ada' in state['civs'])
    check('the replaced copy is kept as copy 1',
          state.get(turn_store.COPIES_KEY), {'0': 1})
    check('copy 1 is the turn as first published',
          store.earlier_copies(0), [(1, first)])
    now = store.turn_blob(0)
    check('turn 0 now holds Ada', 'Ada' in names(now))
    check('turn 0 still carries turn 0', turn_store.turn_of(now), 0)
    check('the state hash is the new turn\'s',
          state['hash'], referee.canonical.canonical_hash(now))
    check('Ada is served her own view',
          turn_store.served_copy(state, 0, 'Ada'), 'Ada')
    check('a caller with no seat is still served',
          turn_store.served_copy(state, 0, None), turn_store.PUBLIC_VIEW)
    check('her joined record is turn 0',
          state[turn_store.JOINED_KEY]['Ada']['turn'], 0)
    check('her welcome note is on turn 0',
          any('You joined this galaxy as Ada at turn 0' in line
              for line in store.note('Ada', 0)))
    check('the request is consumed',
          joins.pending(store, log=quiet), [])
    check('a second pass seats nobody', referee.seat_early(store, log=quiet),
          [])
    check('and keeps no further copy',
          store.state().get(turn_store.COPIES_KEY), {'0': 1})


def test_dry_run(blob):
    print('\na dry run writes nothing')
    store, first = opening_store(blob)
    write_request(store, 'Bo', uid='uidBo')
    got = referee.seat_early(store, log=quiet, dry_run=True)
    check('Bo would be granted', [o['name'] for o in got], ['Bo'])
    check('the roster is unchanged', store.civs(), names(first))
    check('no copy is kept', store.state().get(turn_store.COPIES_KEY), None)
    check('the turn is unchanged', store.turn_blob(0), first)
    check('the request still waits',
          [r['name'] for r in joins.pending(store, log=quiet)], ['Bo'])


def test_left_for_boundary(blob):
    print('\na name the boundary would refuse is left for it')
    store, first = opening_store(blob)
    write_request(store, 'A' * (joins.NAME_LIMIT + 1), uid='uidLong')
    check('nobody is seated', referee.seat_early(store, log=quiet), [])
    check('the request still waits', len(joins.pending(store, log=quiet)), 1)
    check('no copy is kept', store.state().get(turn_store.COPIES_KEY), None)
    check('the turn is unchanged', store.turn_blob(0), first)


def test_only_opening(blob):
    print('\nonly an open galaxy on turn 0, well before its deadline')
    store, _first = opening_store(blob, turn=11)
    write_request(store, 'Cy', uid='uidCy', turn=11)
    check('turn 11 seats nobody early', referee.seat_early(store, log=quiet),
          [])
    store, _first = opening_store(blob, seconds=referee.SEAT_MARGIN - 10)
    write_request(store, 'Cy', uid='uidCy')
    check('a turn 0 inside the margin seats nobody early',
          referee.seat_early(store, log=quiet), [])
    store, _first = opening_store(blob)
    store.close('over')
    check('a closed galaxy is not seated early',
          referee.seats_early(store.state()), False)
    now = time.time()
    check('turn 0 with a day left seats early',
          referee.seats_early({'turn': 0, 'deadline': now + 86400}, now), True)


def test_replaced_copy(blob):
    print('\na submission sent against a replaced copy is judged against it')
    store, first = opening_store(blob)
    incumbent = names(first)[0]
    write_request(store, 'Ada', uid='uidAda')
    referee.seat_early(store, log=quiet)

    # Ada plays the copy she was served, then Bob is seated over it.
    served = store.served_turn('Ada', 0)
    ada = oid_of(served, 'Ada')
    sent = bump(served, ada)
    store.submit('Ada', 0, sent)
    write_request(store, 'Bob', uid='uidBob')
    check('Bob is seated after Ada submitted',
          [o['name'] for o in referee.seat_early(store, log=quiet)], ['Bob'])
    check('the copy Ada played is kept as copy 2',
          store.state().get(turn_store.COPIES_KEY), {'0': 2})
    now = store.turn_blob(0)
    check('against the turn as it is now, her submission is dropped',
          referee.screen_submission(now, 'Ada', sent, 0)[0], False)
    copies = referee.earlier_copies(store, 0)
    check('and it is matched to copy 2',
          referee.earlier_served(copies, 'Ada', sent, 0)[0], 2)

    # The incumbent plays the turn as it is now.
    mine = store.served_turn(incumbent, 0)
    inc = oid_of(mine, incumbent)
    store.submit(incumbent, 0, bump(mine, inc))

    published = stubbed(referee.resolve_turn, store, log=quiet)
    check('the turn closes into turn 1', published, 1)
    rec = store.archive_record(0)
    check('both submissions are taken',
          rec['submitted'], sorted(['Ada', incumbent]))
    check('none is dropped', rec['dropped'], {})
    check('the archive names the copy Ada was served',
          rec['served_copies'], {'Ada': 2})
    after = store.turn_blob(1)
    check("Ada's research order landed",
          mo.research_of(after, oid_of(after, 'Ada'))[0],
          mo.research_of(sent, ada)[0])
    check(f"{incumbent}'s research order landed",
          mo.research_of(after, oid_of(after, incumbent))[0],
          mo.research_of(bump(mine, inc), inc)[0])
    check('Bob is in turn 1', 'Bob' in names(after))
    check('no refusal note for Ada',
          [l for l in store.note('Ada', 0) if 'DROPPED' in l], [])
    check('the closed turn verifies against its copies',
          stubbed(referee.verify_turn, store, 0, log=quiet), True)


def test_worker(blob):
    print('\nthe referee worker seats early between deadlines')
    store, _first = opening_store(blob)
    write_request(store, 'Dee', uid='uidDee')
    g = referee_worker.Watched(store.root, store)
    gap, outcome = referee_worker.tend(g, once=True, log=quiet)
    check('the pass is idle', outcome, referee_worker.IDLE)
    check('it comes back within a minute', gap <= referee.SEAT_EVERY)
    check('Dee is on the roster', 'Dee' in store.civs())
    write_request(store, 'Eve', uid='uidEve')
    referee_worker.tend(g, once=True, log=quiet)
    check('a second look inside the minute seats nobody',
          'Eve' in store.civs(), False)
    g.seated_at -= referee.SEAT_EVERY
    referee_worker.tend(g, once=True, log=quiet)
    check('a look a minute later seats Eve', 'Eve' in store.civs())


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else GALAXY
    blob = sp.load_any(path)
    print(f'galaxy {path}: {names(blob)}')
    for test in (test_seat, test_dry_run, test_left_for_boundary,
                 test_only_opening, test_replaced_copy, test_worker):
        test(blob)
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
