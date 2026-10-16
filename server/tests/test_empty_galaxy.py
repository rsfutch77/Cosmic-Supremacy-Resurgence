"""
test_empty_galaxy.py: a galaxy that abandonment has emptied takes a join
=========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_empty_galaxy.py

Abandonment wipes every civ whose player stops, and when every player stops no
civ holds a planet. The client opens a galaxy only for a civ that holds one, so
such a galaxy cannot be ticked, and a join cloned its newcomer from seat one's
homeworld, which the wipe has taken. The galaxy has to continue once somebody
joins:

  * `inject_civ.add_civ` clones from an earlier turn when seat one holds no
    planet, or from any civ that still holds one;
  * `referee.resolve_turn` seats a join before the tick when nobody holds a
    planet, so the newcomer is the civ the tick runs as;
  * with nobody waiting, the turn is held: nothing is published and the
    deadline moves one turn length on.

The engine tick is stubbed as in `test_joins.py`. That a joined galaxy loads in
a real client is `server/dev_tools/join_turn_acceptance.py`.
"""
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (HERE, os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools'),
          os.path.join(ROOT, 'release')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import inject_civ as icv
import wipe_civ
import inject_ship as ish
import joins
import referee
import player_turn
import turn_store
import test_joins as tj

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(*_a):
    pass


def owned_by(blob, name):
    oid = next(o['oid'] for o in icv.owner_records(blob) if o['name'] == name)
    return [p['id'] for p in icv.planet_records(blob) if p['owner'] == oid]


def seat_one_wiped(blob):
    """The fixture with seat one wiped and the other civ still playing."""
    one = icv.seat_one(icv.owner_records(blob))['name']
    wiped, _p, _s = wipe_civ.wipe(blob, one, log=quiet)
    return wiped, one


def emptied(blob):
    """The fixture with every planet uncolonised and every ship deleted.

    `wipe` refuses a galaxy of one `OWNR`, so the last civ is emptied directly,
    as `test_tick_stamp.py` does. Its ships go too, because a reclaim deletes
    them and a galaxy with no ship left has none to clone a starting hull from.
    """
    out, _one = seat_one_wiped(blob)
    civs = {o['oid'] for o in icv.owner_records(out)}
    blank = wipe_civ.blank_plpr(out, log=quiet)
    for p in icv.planet_records(out):
        if p['owner'] in civs:
            out = wipe_civ.uncolonise(out, p['id'], blank, log=quiet)
    for s in ish.ship_records(out):
        out = wipe_civ.delete_ship(out, s['id'], log=quiet)
    return out


def test_donor_when_seat_one_is_wiped(blob):
    print('\n1. seat one wiped, another civ still holds a planet')
    wiped, one = seat_one_wiped(blob)
    check('seat one holds nothing', owned_by(wiped, one), [])
    joined, home = icv.add_civ(wiped, 'Newcomer', log=quiet)
    check('without a template the newcomer is cloned from a live holder',
          owned_by(joined, 'Newcomer'), [home])
    joined, home = icv.add_civ(wiped, 'Newcomer', template=blob, log=quiet)
    check('with a template the newcomer is cloned from it',
          owned_by(joined, 'Newcomer'), [home])
    check('the template galaxy still parses', tj.parses(joined))
    check('civ count and high water agree', tj.counters_agree(joined))


def test_donor_when_nobody_holds_a_planet(blob):
    print('\n2. nobody holds a planet')
    empty = emptied(blob)
    check('referee.holds_a_planet reads False', referee.holds_a_planet(empty),
          False)
    check('and no ship is left', ish.ship_records(empty), [])
    try:
        icv.add_civ(empty, 'Newcomer', log=quiet)
        refused = False
    except SystemExit:
        refused = True
    check('with no earlier turn the join is refused, not crashed', refused)
    joined, home = icv.add_civ(empty, 'Newcomer', template=blob, log=quiet)
    check('from an earlier turn the newcomer holds one planet',
          owned_by(joined, 'Newcomer'), [home])
    oid = next(o['oid'] for o in icv.owner_records(joined)
               if o['name'] == 'Newcomer')
    check('and the starting hulls, cloned from that turn',
          [s['owner'] for s in ish.ship_records(joined)],
          [oid] * icv.STARTING_SHIPS)
    check('the joined galaxy parses', tj.parses(joined))
    check('civ count and high water agree', tj.counters_agree(joined))
    check('and the galaxy can be ticked again', referee.holds_a_planet(joined))
    check('the stamp chooses the newcomer',
          referee.stamp_for_a_holder(joined, log=quiet) is not None)


def empty_store(blob):
    """A store whose history starts at `blob` and whose current turn is empty."""
    store = tj.fresh_store(blob, turn=tj.TURN - 1)
    store.publish(tj.TURN, tj.set_turn(emptied(blob), tj.TURN))
    store.update_state({'civs': []})
    return store


def test_empty_and_nobody_waiting(blob):
    print('\n3. empty galaxy, nobody waiting to join')
    store = empty_store(blob)
    store.update_state({'deadline': time.time() - 60})
    ticked = []
    real = referee.tick
    referee.tick = lambda *a, **k: ticked.append(1) or real(*a, **k)
    try:
        got = tj.close_turn(store)
    finally:
        referee.tick = real
    check('resolve_turn returns the same turn', got, tj.TURN)
    check('the tick was not run', ticked, [])
    check('no turn was published', store.has_turn(tj.TURN + 1), False)
    check('the deadline moved one turn length on',
          store.seconds_left() > 1700)
    shutil.rmtree(store.root, ignore_errors=True)


def test_empty_and_a_join_waiting(blob):
    print('\n4. empty galaxy, a join waiting')
    store = empty_store(blob)
    tj.write_request(store, 'Ada')
    seen = []

    def fake_tick(b, turns=1, secs=10, save_dir=None, log=print, **kw):
        seen.append(b)
        referee.stamp_for_a_holder(b, log=quiet)
        return tj.set_turn(b, turn_store.turn_of(b) + 1)

    real_tick, real_ready = referee.tick, player_turn.save_path_ready
    referee.tick, player_turn.save_path_ready = fake_tick, lambda: (True, '')
    try:
        got = referee.resolve_turn(store, log=quiet)
    finally:
        referee.tick, player_turn.save_path_ready = real_tick, real_ready
    check('the turn advanced', got, tj.TURN + 1)
    check('the tick ran on a galaxy in which a civ holds a planet',
          bool(seen) and referee.holds_a_planet(seen[0]))
    out = store.turn_blob(got)
    check('Ada holds exactly one planet', len(owned_by(out, 'Ada')), 1)
    check('Ada was added once',
          [o['name'] for o in icv.owner_records(out)].count('Ada'), 1)
    check('the roster is Ada', store.civs(), ['Ada'])
    ans = tj.answer(store, 'Ada')
    check('the request was answered granted',
          (ans or {}).get('outcome'), joins.GRANTED)
    check('nothing is left waiting', joins.pending(store, log=quiet), [])
    shutil.rmtree(store.root, ignore_errors=True)


def main():
    if not os.path.exists(tj.GALAXY):
        print(f'no fixture at {tj.GALAXY}')
        return 2
    blob = tj.set_turn(sp.load_any(tj.GALAXY), tj.TURN)
    for fn in (test_donor_when_seat_one_is_wiped,
               test_donor_when_nobody_holds_a_planet,
               test_empty_and_nobody_waiting,
               test_empty_and_a_join_waiting):
        fn(blob)
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
