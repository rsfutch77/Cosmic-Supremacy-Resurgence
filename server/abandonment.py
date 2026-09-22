"""
abandonment.py , the seat a player stopped using
=================================================
    import abandonment

    blob = abandonment.enforce(store, turn, blob, log=log)   # the referee's line
    store.publish(new_turn, blob)

    python abandonment.py ../galaxies/sandbox            # what it would do
    python abandonment.py ../galaxies/sandbox --warn 6 --reclaim 12 --set

A permanent sandbox has no end, so it has to be able to take a seat back.
A player who misses too many turns in a row is warned, and if they keep missing
them their civ is wiped and their seat is taken. That is K3, and it needs no
new bookkeeping: the archive already records who submitted for each turn, so
the count is a read of what the referee has been writing all along.

Two stages, because one is a punishment and two is a warning that can be acted
on. The warning goes through `put_note`, which is the path a player's launcher
already shows, and it repeats every turn until they play or the seat goes: a
note written once, on the exact turn the count crossed, is a note the player who
comes back three turns later never sees.

Why this is a module and not part of the referee
------------------------------------------------
`referee.resolve_turn` is one function that closes a turn, and everything about
which civs are still playing is a different question from how a turn is
computed. Keeping it out means the count can be run against a live galaxy
without a client, an engine or a turn, which is what the `__main__` below is
for: an operator can ask what would happen before anything does.

Counting, and the mistake that would make it worthless
-------------------------------------------------------
A civ that joined at turn 90 has no submission for turn 89, and a naive walk
backwards reads that as a player who has missed every turn since the galaxy
began. It would reclaim a seat the moment it was taken. So the count reads the
archive record, which separates the three cases: a civ named in `submitted`
played, a civ named in `missing` was seated and did not, and a civ in neither
was not in the roster for that turn, which ends the count.

A turn with no archive record ends the count as well, rather than being guessed
at, with one exception: the turn being closed right now, whose record the
referee has not written yet and whose roster is the one being read. Ending the
count early can only delay a reclaim; guessing at it can take a seat that was
never missed.

The template a wipe needs
-------------------------
`wipe_civ` replaces a wiped planet's `PLPR` with one from a planet nobody has
colonised, and a galaxy where every planet is taken has none. That is the
ordinary late state of a sandbox and it is exactly when an abandonment happens,
so the template comes from an earlier turn of this galaxy, which is what the
store's archive of turns is. The search doubles its step backwards rather than
walking every turn, so turn 1 of a 500-turn galaxy is 10 reads away, and an
early turn is where a free planet is most likely to be anyway.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (HERE, os.path.join(HERE, 'dev_tools'),
           os.path.join(os.path.dirname(HERE), 'client', 'dev_tools')):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import turn_store

# The thresholds, in consecutive missed turns, and the state keys that override
# them per galaxy. At the beta's 4-hour turns the reclaim is two days and the
# warning is one, which is the balance the plan asks for: long enough that a
# player who slept is not punished, short enough that a sandbox does not fill
# with empires nobody is playing.
WARN_KEY, RECLAIM_KEY = 'warn_after_misses', 'reclaim_after_misses'
WARN_AFTER, RECLAIM_AFTER = 6, 12

# What a civ's turn was, as the archive can tell it apart.
PLAYED, MISSED, UNSEATED = 'played', 'missed', 'unseated'


def thresholds(state) -> tuple:
    """(warn, reclaim) for a galaxy, from its state or from the defaults.

    Either one set to 0 turns that stage off, so an operator can run a galaxy
    that warns and never reclaims, which is the right setting for a galaxy of
    people who know each other.

    Anything that is not a whole number at or above zero falls back to the
    default rather than being refused, for the reason `version_problem` ignores
    a minimum build it cannot read: one typo in one galaxy's state must not
    decide what happens to every seat in it. Note that this is a fallback and
    not a disable, so a galaxy is never left silently unenforced by a mistyped
    threshold; 0 is the way to say never, and it has to be said on purpose.
    """
    def read(key, fallback):
        value = (state or {}).get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return fallback
        return value

    return read(WARN_KEY, WARN_AFTER), read(RECLAIM_KEY, RECLAIM_AFTER)


def set_thresholds(store, warn: int = None, reclaim: int = None) -> dict:
    """Configure one galaxy. Returns the state as it now reads."""
    fields = {}
    if warn is not None:
        fields[WARN_KEY] = int(warn)
    if reclaim is not None:
        fields[RECLAIM_KEY] = int(reclaim)
    return store.update_state(fields) if fields else store.state()


def turn_outcome(store, civ: str, turn: int, current: int = None) -> str:
    """What one civ did with one turn, as far as the store can say.

    `current` is the turn being closed, if one is. Its archive record does not
    exist yet, so that turn alone is read from the submission itself; the civ
    is known to be seated for it, because the caller took the name out of the
    roster that is closing.
    """
    record = store.archive_record(turn)
    if record is not None:
        if civ in (record.get('submitted') or []):
            return PLAYED
        if civ in (record.get('missing') or []):
            return MISSED
        # Named in neither, so the roster of that turn did not hold this civ.
        return UNSEATED
    if current is not None and turn == current:
        return PLAYED if store.has_submitted(civ, turn) else MISSED
    return UNSEATED


def missed_streak(store, civ: str, through: int, limit: int = None) -> int:
    """How many turns in a row this civ has missed, ending at `through`.

    Stops at the first turn they played, at the first turn they were not seated
    for, and at `limit`, which exists so that a galaxy hundreds of turns long
    does not read hundreds of archive records to answer a question that is
    settled once the count passes the reclaim threshold.

    A count that reaches the limit is reported as the limit. In a galaxy the
    referee has been enforcing all along the two are the same number, because
    the seat goes on the turn the threshold is crossed; they differ only the
    first time enforcement runs over an archive that was written before it,
    where the note then says the threshold rather than the true depth of the
    silence. Understating that is better than reading a hundred records to put
    a larger number in a sentence that ends the same way.
    """
    count = 0
    turn = through
    while turn >= 0 and (limit is None or count < limit):
        if turn_outcome(store, civ, turn, current=through) != MISSED:
            break
        count += 1
        turn -= 1
    return count


def review(store, through: int = None) -> list:
    """What the thresholds say about every seat, changing nothing.

    A list of `{civ, missed, warn, reclaim}`, in roster order. This is the
    operator's dry run and the test's observation point, and it is also the
    thing to run before a referee starts enforcing on a galaxy that has been
    played for months: turning the count on does not start it from today, it
    reads the archive that is already there.
    """
    state = store.state()
    warn, reclaim = thresholds(state)
    if through is None:
        through = state['turn']
    limit = max(warn, reclaim) or None
    out = []
    for civ in state.get('civs', []):
        missed = missed_streak(store, civ, through, limit=limit)
        out.append({
            'civ': civ,
            'missed': missed,
            'warn': bool(warn) and missed >= warn,
            'reclaim': bool(reclaim) and missed >= reclaim,
        })
    return out


def _has_free_planet(blob) -> bool:
    """Whether a blob holds a planet nobody has colonised.

    The same test `wipe_civ.pristine_planet` makes, asked without the refusal,
    because here a blob with no free planet is a reason to look at an earlier
    turn rather than a failure.
    """
    import inject_civ as icv
    return any(not p['owner'] and p['nlen'] == 0
               for p in icv.planet_records(blob))


def candidate_turns(before: int, floor: int = 0) -> list:
    """Turns to look in for a template, recent first, doubling backwards.

    A sandbox that needs a template is one that has run long enough to fill,
    so a walk from the present to the beginning would read every turn it has.
    Doubling reaches the start of a 500-turn galaxy in 10 steps, and the floor
    is included whether or not the doubling lands on it, because the earliest
    turn is the one most likely to still hold a free planet.
    """
    out, step, turn = [], 1, before
    while turn > floor:
        out.append(turn)
        turn -= step
        step *= 2
    out.append(floor)
    return out


def find_template(store, before: int, floor: int = 0, log=print):
    """(blob, turn) for an earlier turn holding a free planet, or (None, None).

    `before` is exclusive of nothing: a caller passes the turn before the one
    being wiped, because the blob being wiped is the one that has no free
    planet left.
    """
    for turn in candidate_turns(before, floor):
        try:
            if not store.has_turn(turn):
                continue
            blob = store.turn_blob(turn)
        except (OSError, ValueError):
            # A turn that cannot be read is one fewer place to look, not a
            # reason to leave a seat held by somebody who left.
            continue
        if _has_free_planet(blob):
            log(f'  abandonment: template from turn {turn}')
            return blob, turn
    return None, None


def warning_note(civ: str, missed: int, reclaim: int) -> list:
    """What a player who has stopped playing is told, and why it says it.

    The number of turns left is the only part that matters, so it is the part
    that is concrete. A warning that said only "you have missed several turns"
    would leave a player who is away for a week no way to know whether coming
    back on Friday is soon enough.
    """
    lines = [f'You have missed {missed} turns in a row.']
    if reclaim:
        left = max(reclaim - missed, 0)
        lines.append(
            f'After {reclaim} missed turns this civ is wiped and the seat is '
            f'given up, which is {left} more turn(s) from here.')
        lines.append('Playing any turn resets the count.')
    return lines


def reclaim_note(civ: str, missed: int, turn: int) -> list:
    """What is left for the player whose seat has just gone.

    Written into the turn that took it, so a player who comes back and reads
    their last turn's note finds the reason there rather than only a launcher
    refusing to start.
    """
    return [
        f'This civ was wiped at turn {turn} after {missed} missed turns in a '
        f'row.',
        'Its planets are uncolonised and its ships are gone, and the seat is '
        'no longer on this galaxy\'s roster.',
        'Ask whoever runs the galaxy for a new seat if you want to play again.',
    ]


def _append_note(store, civ: str, turn: int, lines, log=print) -> None:
    """Add to whatever note that civ already has for that turn.

    `put_note` replaces, and the referee writes refusals through it before this
    runs. A civ counted as missing did not submit and so has no refusals, which
    makes the collision impossible today; appending costs one read and means it
    stays impossible if that ever stops being true.
    """
    try:
        existing = store.note(civ, turn)
        store.put_note(civ, turn, list(existing) + list(lines))
    except OSError as exc:
        # The same rule the referee applies to a refusal note: a note that
        # cannot be written is not worth losing a turn over.
        log(f'  abandonment: could not leave {civ} a note about turn {turn}, '
            f'{exc}')


def reclaim(store, civ: str, blob: bytes, turn: int, missed: int, log=print):
    """Wipe one civ out of `blob` and take its seat off the roster.

    Returns the blob to publish, which is the one that came in when the wipe
    could not be done. A wipe that fails leaves the seat held and the count
    running, so the next turn tries again, which is right: the alternative is a
    roster that no longer names a civ the galaxy is still full of.

    The roster write happens only after the wipe has produced a blob, for the
    same reason `uncolonise` zeroes the owner last. A seat removed from a civ
    that is still on the board is a player locked out of an empire that is
    still playing itself.
    """
    import wipe_civ

    template = None
    if not _has_free_planet(blob):
        template, _found = find_template(store, turn - 1, log=log)
        if template is None:
            log(f'  abandonment: every planet in this galaxy is colonised and '
                f'no earlier turn holds a free one, so {civ} cannot be wiped '
                f'yet; the seat stays and the count keeps running')
            return blob, None
    try:
        wiped, planets, ships = wipe_civ.wipe(blob, civ, log=log,
                                              template_blob=template)
    except (SystemExit, ValueError, KeyError, StopIteration) as exc:
        # `wipe_civ` raises SystemExit for the two cases it refuses outright, a
        # civ that is not there and a galaxy of one. Neither is worth stopping
        # a turn for, and both are worth saying out loud.
        log(f'  abandonment: could not wipe {civ}, {type(exc).__name__}: {exc}')
        return blob, None

    roster = [c for c in store.civs() if c != civ]
    taken = store.reclaimed() or {}
    taken[civ] = {'turn': int(turn), 'missed': int(missed), 'at': time.time()}
    store.update_state({'civs': roster, turn_store.RECLAIMED_KEY: taken})
    _append_note(store, civ, turn, reclaim_note(civ, missed, turn), log=log)
    log(f'  abandonment: reclaimed {civ} after {missed} missed turns, '
        f'{len(planets)} planet(s) freed and {len(ships)} ship(s) deleted')
    return wiped, {'civ': civ, 'missed': missed, 'planets': planets,
                   'ships': ships}


def enforce(store, turn: int, blob: bytes, log=print):
    """Warn, reclaim, and hand back the blob to publish. The referee's seam.

    Called with the turn that has just been closed and the blob the tick
    produced for the next one, before `publish`. Before, because a reclaim
    changes that blob, and republishing a turn to change it afterwards would
    restart the clock on a turn players are already holding.

    Everything here is skipped for a closed galaxy. Nobody is required to play
    a galaxy that has ended, and a run of the referee over one would otherwise
    reclaim every seat in it.
    """
    if store.is_closed():
        return blob
    state = store.state()
    warn, reclaim_at = thresholds(state)
    if not warn and not reclaim_at:
        return blob
    limit = max(warn, reclaim_at) or None

    for civ in list(state.get('civs', [])):
        missed = missed_streak(store, civ, turn, limit=limit)
        if not missed:
            continue
        if reclaim_at and missed >= reclaim_at:
            blob, done = reclaim(store, civ, blob, turn, missed, log=log)
            if done is not None:
                continue
            # The wipe did not happen, so the warning is still the honest
            # thing to leave them.
        if warn and missed >= warn:
            _append_note(store, civ, turn,
                         warning_note(civ, missed, reclaim_at), log=log)
            log(f'  abandonment: warned {civ}, {missed} missed turn(s)')
    return blob


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('store', help='a galaxy: a directory, a URL or a '
                                  'firebase:// spec')
    ap.add_argument('--turn', type=int, default=None,
                    help='count as at this turn rather than the current one')
    ap.add_argument('--warn', type=int, default=None)
    ap.add_argument('--reclaim', type=int, default=None)
    ap.add_argument('--set', action='store_true',
                    help='write --warn and --reclaim into the galaxy rather '
                         'than only reporting with them')
    a = ap.parse_args()

    store = turn_store.open_store(a.store)
    if not store.exists():
        raise SystemExit(f'no galaxy in {a.store}')
    if a.set:
        set_thresholds(store, warn=a.warn, reclaim=a.reclaim)
    state = store.state()
    warn, reclaim_at = thresholds(state)
    if not a.set:
        warn = a.warn if a.warn is not None else warn
        reclaim_at = a.reclaim if a.reclaim is not None else reclaim_at
    print(f'{a.store}: turn {state["turn"]}, status {store.status()}, '
          f'warn at {warn or "never"}, reclaim at {reclaim_at or "never"}')
    for row in review(store, through=a.turn):
        mark = ('RECLAIM' if reclaim_at and row['missed'] >= reclaim_at else
                'warn' if warn and row['missed'] >= warn else '')
        print(f'  {row["civ"]:<20} {row["missed"]:>3} missed  {mark}')
    taken = store.reclaimed()
    for civ, rec in sorted(taken.items()):
        print(f'  reclaimed: {civ} at turn {rec.get("turn")} after '
              f'{rec.get("missed")} missed turns')
    return 0


if __name__ == '__main__':
    sys.exit(main())
