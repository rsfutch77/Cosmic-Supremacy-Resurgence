"""
test_abandonment.py , the seat of a player who stopped playing
==============================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_abandonment.py

K3 warns a player who has missed too many turns in a row and then takes their
civ off the board. Everything here is offline: the count is a read of archive
records the referee already writes, and the wipe is `wipe_civ`, which is bytes
in and bytes out. What is **not** tested here is that a wiped blob loads and
ticks, which needs the game client and is `wipe_acceptance.py`.

Each galaxy in this file is built to hold the condition it is about, rather
than borrowed from one that might. That is the whole reason for the setup
below being as long as it is:

  * the silence is built. A galaxy where everybody submitted every turn would
    pass "nobody was warned" without the count ever working.
  * the **full galaxy is built**, by giving every uncolonised planet an owner,
    and the test asserts that `wipe_civ` refuses it without a template before
    asking whether the module finds one. Run against the fixture as it ships,
    which has 156 free planets, the template search would never be entered and
    the K4 constraint would go untested while appearing to pass.
  * the **late joiner is built**, seated at a turn most of the archive predates.
    This is the case that decides whether the counter is usable at all: a walk
    backwards that reads "no submission" as "missed" reclaims a seat the turn
    after it is taken, and every galaxy that has ever been joined mid-game
    would have fired it.
  * a control galaxy where the same civ played every turn is run through the
    same `enforce`, so a warning that appeared for everyone would be caught.
"""
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import inject_civ as icv
import inject_ship as ish
import wipe_civ
import abandonment
import turn_store
from turn_store import TurnStore

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
ROSTER = ['DemoPlayer', 'BadGuy']
VICTIM = 'BadGuy'
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def raises(name, fn, want=Exception):
    try:
        fn()
    except want as exc:                                     # noqa: BLE001
        check(f'{name} ({type(exc).__name__})', True)
        return
    except Exception as exc:                                # noqa: BLE001
        check(f'{name}, but it raised {type(exc).__name__}: {exc}', False)
        return
    check(f'{name}, but nothing was raised', False)


# ── building a galaxy that holds the condition ───────────────────────────────
def fill_every_planet(blob, owner_name):
    """Give every uncolonised planet an owner, so the galaxy has no free rock.

    This is the late sandbox K4 names and no archived galaxy has reached, so it
    has to be constructed. Only the owner dword is written: a planet with an
    owner and no name is not a planet anyone would want to play, and nothing
    here loads it. What matters is that `pristine_planet` can no longer find a
    template in it, which is the condition under test.
    """
    civ = next(o for o in icv.owner_records(blob) if o['name'] == owner_name)
    buf = bytearray(blob)
    for p in icv.planet_records(blob):
        if not p['owner']:
            struct.pack_into('<I', buf, p['payload'] + wipe_civ.PLNT_OWNER_OFF,
                             civ['oid'])
    return bytes(buf)


def free_planets(blob):
    return [p['id'] for p in icv.planet_records(blob)
            if not p['owner'] and p['nlen'] == 0]


def build(root, turns, played, roster=None, blobs=None, first=1):
    """A galaxy with an archive that says exactly who played which turn.

    `played` is {turn: [civ]}. Every other civ in that turn's roster is written
    into `missing`, which is the shape `referee.resolve_turn` writes and the
    shape the counter reads. `roster` may be a {turn: [civ]} as well, which is
    how a late joiner is expressed: a civ that is in neither list for a turn
    was not seated for it.
    """
    blob = sp.load_any(GALAXY)
    store = TurnStore(root)
    store.start(blob, civs=roster.get(turns, ROSTER) if isinstance(roster, dict)
                else (roster or ROSTER), turn=first, turn_seconds=14400)
    for turn in range(first, turns + 1):
        this = (blobs or {}).get(turn, blob)
        if turn > first:
            store.publish(turn, this)
        seats = (roster.get(turn, ROSTER) if isinstance(roster, dict)
                 else (roster or ROSTER))
        subs = [c for c in seats if c in played.get(turn, seats)]
        if turn < turns:
            store.archive(turn, {
                'turn': turn, 'published': turn + 1,
                'submitted': sorted(subs),
                'missing': sorted(c for c in seats if c not in subs),
            })
        else:
            # The last turn is the one being closed, which the referee has not
            # archived yet, so whoever played it is known from the submission
            # and nowhere else. Leaving these out would make every civ in the
            # galaxy look one turn more silent than it is.
            for civ in subs:
                store.submit(civ, turn, this)
    return store


# ── counting ─────────────────────────────────────────────────────────────────
def test_counting(tmp):
    print('\ncounting, out of the archive the referee already writes')

    # BadGuy plays 1 to 3 and then stops. Turn 20 is the turn being closed and
    # has no archive record yet, which is the referee's position when it calls.
    root = os.path.join(tmp, 'silent')
    store = build(root, 20, {t: ROSTER if t <= 3 else ['DemoPlayer']
                             for t in range(1, 21)})
    check('a civ that played every turn has no streak',
          abandonment.missed_streak(store, 'DemoPlayer', 20), 0)
    check('a civ silent since turn 4 has missed 17 turns',
          abandonment.missed_streak(store, VICTIM, 20), 17)
    check('a civ that submitted the turn being closed has no streak either, '
          'though nothing has archived that turn yet',
          abandonment.turn_outcome(store, 'DemoPlayer', 20, current=20),
          abandonment.PLAYED)
    check('the turn being closed counts from its submissions, not its archive',
          abandonment.turn_outcome(store, VICTIM, 20, current=20),
          abandonment.MISSED)

    # The same galaxy, with one turn played in the middle of the silence.
    root = os.path.join(tmp, 'resumed')
    store = build(root, 20, {t: ROSTER if t <= 3 or t == 15 else ['DemoPlayer']
                             for t in range(1, 21)})
    check('one turn played resets the count',
          abandonment.missed_streak(store, VICTIM, 20), 5)

    # A limit stops the read without changing the answer either side of it.
    check('a limit caps the count rather than ending it early',
          abandonment.missed_streak(store, VICTIM, 20, limit=3), 3)


def test_late_joiner(tmp):
    """The check that decides whether any of this is usable.

    Failure here is a `missed` of 19 for a player seated two turns ago, which
    is an instant reclaim of every seat the moment it is joined.
    """
    print('\na seat taken at turn 19 has not missed the 18 turns before it')
    root = os.path.join(tmp, 'joiner')
    seats = {t: (ROSTER if t < 19 else ROSTER + ['Latecomer'])
             for t in range(1, 21)}
    store = build(root, 20, {t: ROSTER for t in range(1, 21)}, roster=seats)
    store.update_state({'civs': ROSTER + ['Latecomer']})

    check('the archive says a pre-join turn was neither played nor missed',
          abandonment.turn_outcome(store, 'Latecomer', 10), abandonment.UNSEATED)
    check('a civ seated at turn 19 has missed 2 turns at turn 20',
          abandonment.missed_streak(store, 'Latecomer', 20), 2)
    check('and is not up for a reclaim',
          [r['reclaim'] for r in abandonment.review(store, through=20)
           if r['civ'] == 'Latecomer'], [False])


def test_thresholds(tmp):
    print('\nthresholds, per galaxy')
    root = os.path.join(tmp, 'thresholds')
    store = build(root, 10, {t: ['DemoPlayer'] for t in range(1, 11)})
    check('a galaxy that configures neither gets the defaults',
          abandonment.thresholds(store.state()),
          (abandonment.WARN_AFTER, abandonment.RECLAIM_AFTER))
    abandonment.set_thresholds(store, warn=2, reclaim=4)
    check('what was written is what is read back',
          abandonment.thresholds(store.state()), (2, 4))
    store.update_state({abandonment.RECLAIM_KEY: 0})
    check('0 turns a stage off', abandonment.thresholds(store.state()), (2, 0))
    store.update_state({abandonment.RECLAIM_KEY: 'soon'})
    check('a threshold that is not a number falls back to the default, so a '
          'typo cannot silently stop enforcement',
          abandonment.thresholds(store.state()),
          (2, abandonment.RECLAIM_AFTER))


# ── the two stages ───────────────────────────────────────────────────────────
def test_warning(tmp):
    print('\nthe warning, one turn before it is due and on the turn it is')
    root = os.path.join(tmp, 'warning')
    store = build(root, 5, {t: ['DemoPlayer'] for t in range(1, 6)})
    abandonment.set_thresholds(store, warn=5, reclaim=9)
    blob = store.turn_blob(5)

    out = abandonment.enforce(store, 4, blob)
    check('4 missed turns against a threshold of 5 leaves no note',
          store.note(VICTIM, 4), [])
    check('and the blob is handed straight back', out is blob)

    out = abandonment.enforce(store, 5, blob)
    note = store.note(VICTIM, 5)
    check('the 5th missed turn leaves a note', bool(note))
    check('which says how many turns are left',
          any('9' in line for line in note))
    check('the warned civ keeps its seat', VICTIM in store.civs())
    check('and the galaxy is unchanged', out is blob)
    check('nobody else is warned', store.note('DemoPlayer', 5), [])

    # The control. Same call, same thresholds, a galaxy nobody abandoned.
    root = os.path.join(tmp, 'control')
    played = build(root, 5, {t: ROSTER for t in range(1, 6)})
    abandonment.set_thresholds(played, warn=1, reclaim=2)
    abandonment.enforce(played, 5, played.turn_blob(5))
    check('CONTROL: a galaxy where everyone played is warned about nothing, '
          'at a threshold of one missed turn',
          [played.note(c, 5) for c in ROSTER], [[], []])
    check('CONTROL: and keeps its whole roster', played.civs(), ROSTER)


def test_reclaim(tmp):
    print('\nthe reclaim, in a galaxy that still has a free planet')
    root = os.path.join(tmp, 'reclaim')
    store = build(root, 6, {t: ['DemoPlayer'] for t in range(1, 7)})
    abandonment.set_thresholds(store, warn=2, reclaim=5)
    blob = store.turn_blob(6)

    before = [p['id'] for p in icv.planet_records(blob)
              if p['owner'] == next(o['oid'] for o in icv.owner_records(blob)
                                    if o['name'] == VICTIM)]
    ships = [s['id'] for s in ish.ship_records(blob)
             if s['owner'] == next(o['oid'] for o in icv.owner_records(blob)
                                   if o['name'] == VICTIM)]
    check('the victim owns something to lose', bool(before) and bool(ships))

    out = abandonment.enforce(store, 6, blob)
    check('the blob that comes back is not the one that went in', out != blob)
    check('the wipe holds', wipe_civ.check(out, VICTIM, before, ships))
    check('the seat is off the roster', store.civs(), ['DemoPlayer'])
    rec = store.reclaimed(VICTIM)
    # `missed` is 5 rather than 6 because the count stops once the answer is
    # settled. See missed_streak: in a galaxy the referee has been enforcing
    # all along the two numbers are the same one.
    check('and the store records why', bool(rec) and rec['turn'] == 6
          and rec['missed'] == 5)
    check('the player is left a note saying so',
          any('wiped' in line for line in store.note(VICTIM, 6)))
    check('the surviving civ is untouched',
          next(o['name'] for o in icv.owner_records(out)
               if o['name'] == 'DemoPlayer'), 'DemoPlayer')
    check('a second run finds nobody to reclaim',
          abandonment.enforce(store, 6, out) is out)


def test_reclaim_in_a_full_galaxy(tmp):
    """K4's constraint: no free planet to take a blank PLPR from.

    The precondition is asserted before the thing under test, because a galaxy
    with a free planet in it would take the template out of itself and this
    would pass without the archive being touched at all.
    """
    print('\nthe reclaim, in a galaxy where every planet is colonised')
    plain = sp.load_any(GALAXY)
    full = fill_every_planet(plain, 'DemoPlayer')
    check('SETUP: the fixture as it ships has free planets',
          len(free_planets(plain)) > 0)
    check('SETUP: the built galaxy has none', free_planets(full), [])
    raises('SETUP: and wipe_civ refuses it with no template',
           lambda: wipe_civ.blank_plpr(full, None, log=lambda *a: None),
           SystemExit)

    # Turns 1 to 4 are the galaxy as it shipped; from turn 5 it is full. The
    # template can only come from the archive.
    root = os.path.join(tmp, 'full')
    store = build(root, 8, {t: ['DemoPlayer'] for t in range(1, 9)},
                  blobs={t: full for t in range(5, 9)})
    abandonment.set_thresholds(store, warn=2, reclaim=5)
    check('SETUP: the turn being wiped has no free planet',
          free_planets(store.turn_blob(8)), [])

    found, turn = abandonment.find_template(store, 7, log=lambda *a: None)
    check('an earlier turn in the archive supplies the template',
          found is not None and turn < 5)

    blob = store.turn_blob(8)
    victim = next(o['oid'] for o in icv.owner_records(blob)
                  if o['name'] == VICTIM)
    planets = [p['id'] for p in icv.planet_records(blob)
               if p['owner'] == victim]
    ships = [s['id'] for s in ish.ship_records(blob) if s['owner'] == victim]
    out = abandonment.enforce(store, 8, blob)
    check('and the wipe goes through', wipe_civ.check(out, VICTIM, planets,
                                                      ships))
    check('the seat is taken', store.civs(), ['DemoPlayer'])

    # A galaxy with no earlier turn to take one from keeps the seat rather than
    # losing a turn over it.
    root = os.path.join(tmp, 'full_no_history')
    lone = TurnStore(root)
    lone.start(full, civs=ROSTER, turn=9, turn_seconds=14400)
    abandonment.set_thresholds(lone, warn=2, reclaim=3)
    lone.archive(9, {'turn': 9, 'published': 10, 'submitted': ['DemoPlayer'],
                     'missing': [VICTIM]})
    lone.publish(10, full)
    for t in (10, 11):
        lone.archive(t, {'turn': t, 'published': t + 1,
                         'submitted': ['DemoPlayer'], 'missing': [VICTIM]})
    out = abandonment.enforce(lone, 11, full)
    check('with no earlier turn to take a template from, the seat stays',
          VICTIM in lone.civs())
    check('the blob is unchanged', out is full)
    check('and the player is warned rather than nothing happening',
          bool(lone.note(VICTIM, 11)))


def test_closed_galaxy_reclaims_nobody(tmp):
    print('\na closed galaxy asks nothing of anyone')
    root = os.path.join(tmp, 'closed')
    store = build(root, 8, {t: ['DemoPlayer'] for t in range(1, 9)})
    abandonment.set_thresholds(store, warn=1, reclaim=2)
    store.close(reason='the operator called it')
    blob = store.turn_blob(8)
    out = abandonment.enforce(store, 8, blob)
    check('no seat is taken', store.civs(), ROSTER)
    check('no note is left', store.note(VICTIM, 8), [])
    check('and the blob is handed back', out is blob)


def main():
    if not os.path.exists(GALAXY):
        print(f'no fixture at {GALAXY}')
        return 2
    tmp = tempfile.mkdtemp(prefix='abandon_')
    try:
        test_counting(tmp)
        test_late_joiner(tmp)
        test_thresholds(tmp)
        test_warning(tmp)
        test_reclaim(tmp)
        test_reclaim_in_a_full_galaxy(tmp)
        test_closed_galaxy_reclaims_nobody(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for name in FAIL:
        print(f'  failed: {name}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
