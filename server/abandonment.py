"""
abandonment.py: the seat a player stopped using

    import abandonment

    blob = abandonment.enforce(store, turn, blob, log=log)   # the referee's line
    store.publish(new_turn, blob)

    python abandonment.py ../galaxies/sandbox            # what it would do
    python abandonment.py ../galaxies/sandbox --warn 6 --reclaim 24 --set

A player who misses too many turns in a row is warned through `put_note` every
turn, and past a second threshold their civ is wiped and their seat taken. The
count reads the archive record of each turn: a civ in `submitted` played, a civ
in `missing` was seated and did not, and a civ in neither (or a turn with no
record) ends the count. The turn being closed has no record yet and is read from
its submissions.

A wipe needs a template `PLPR` from an uncolonised planet. When the current
blob has none, earlier turns are searched, doubling the step backwards.
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

# Thresholds in consecutive missed turns, and the state keys that override them per galaxy.
WARN_KEY, RECLAIM_KEY = turn_store.WARN_KEY, turn_store.RECLAIM_KEY
WARN_AFTER, RECLAIM_AFTER = 6, 24

# What a civ's turn was, as the archive can tell it apart.
PLAYED, MISSED, UNSEATED = 'played', 'missed', 'unseated'


def thresholds(state) -> tuple:
    """(warn, reclaim) for a galaxy, from its state or from the defaults.

    0 turns a stage off. Anything that is not a whole number at or above zero
    falls back to the default.
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
    """What one civ did with one turn: PLAYED, MISSED or UNSEATED.

    `current` is the turn being closed, which has no archive record yet and is
    read from the submission; the civ is assumed seated for it.
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

    Stops at the first turn they played or were not seated for, and at `limit`;
    a count that reaches the limit is reported as the limit.
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

    A list of `{civ, missed, warn, reclaim}`, in roster order.
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
    """Whether a blob holds a planet nobody has colonised (the
    `wipe_civ.pristine_planet` test, without raising)."""
    import inject_civ as icv
    return any(not p['owner'] and p['nlen'] == 0
               for p in icv.planet_records(blob))


def candidate_turns(before: int, floor: int = 0) -> list:
    """Turns to look in for a template, recent first, doubling backwards.
    The floor is always included."""
    out, step, turn = [], 1, before
    while turn > floor:
        out.append(turn)
        turn -= step
        step *= 2
    out.append(floor)
    return out


def find_template(store, before: int, floor: int = 0, log=print):
    """(blob, turn) for an earlier turn holding a free planet, or (None, None).

    `before` is inclusive; callers pass the turn before the one being wiped.
    """
    for turn in candidate_turns(before, floor):
        try:
            if not store.has_turn(turn):
                continue
            blob = store.turn_blob(turn)
        except (OSError, ValueError):
            continue
        if _has_free_planet(blob):
            log(f'  abandonment: template from turn {turn}')
            return blob, turn
    return None, None


def warning_note(civ: str, missed: int, reclaim: int) -> list:
    """The note lines warning a player how many turns remain before reclaim."""
    lines = [f'You have missed {missed} turns in a row.']
    if reclaim:
        left = max(reclaim - missed, 0)
        lines.append(
            f'After {reclaim} missed turns this civ is wiped and the seat is '
            f'given up, which is {left} more turn(s) from here.')
        lines.append('Playing any turn resets the count.')
    return lines


def reclaim_note(civ: str, missed: int, turn: int) -> list:
    """The note lines written into the turn that reclaimed a seat."""
    return [
        f'This civ was wiped at turn {turn} after {missed} missed turns in a '
        f'row.',
        'Its planets are uncolonised and its ships are gone, and the seat is '
        'no longer on this galaxy\'s roster.',
        'Ask whoever runs the galaxy for a new seat if you want to play again.',
    ]


def _holds(existing: list, lines: list) -> bool:
    """Whether `lines` already appear in `existing`, in order and together."""
    n = len(lines)
    return bool(n) and any(existing[i:i + n] == lines
                           for i in range(len(existing) - n + 1))


def _append_note(store, civ: str, turn: int, lines, log=print) -> None:
    """Append to the civ's note for that turn (`put_note` replaces). Lines
    already present are not written again."""
    try:
        existing = list(store.note(civ, turn))
        lines = list(lines)
        if _holds(existing, lines):
            return
        store.put_note(civ, turn, existing + lines)
    except OSError as exc:
        # A note that cannot be written does not stop the turn.
        log(f'  abandonment: could not leave {civ} a note about turn {turn}, '
            f'{exc}')


def reclaim(store, civ: str, blob: bytes, turn: int, missed: int, log=print):
    """Wipe one civ out of `blob` and take its seat off the roster.

    Returns (blob to publish, summary or None). A failed wipe returns the blob
    unchanged and leaves the seat held, so the next turn tries again. The roster
    is written only after the wipe has produced a blob.
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
        # `wipe_civ` raises SystemExit for a missing civ and for a galaxy of one.
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


def enforce(store, turn: int, blob: bytes, log=print, new_turn: int = None):
    """Warn, reclaim, and hand back the blob to publish.

    Called with the turn just closed and the blob the tick produced for the
    next one, before `publish`, since a reclaim changes that blob. A warning is
    written on `new_turn`, the turn the player is served next, which defaults to
    the turn the blob carries; a blob no later than `turn` puts it on `turn`.
    A closed galaxy is skipped.
    """
    if store.is_closed():
        return blob
    state = store.state()
    warn, reclaim_at = thresholds(state)
    if not warn and not reclaim_at:
        return blob
    limit = max(warn, reclaim_at) or None
    if new_turn is None:
        try:
            new_turn = turn_store.turn_of(blob)
        except Exception:                                   # noqa: BLE001
            new_turn = None

    for civ in list(state.get('civs', [])):
        missed = missed_streak(store, civ, turn, limit=limit)
        if not missed:
            continue
        if reclaim_at and missed >= reclaim_at:
            blob, done = reclaim(store, civ, blob, turn, missed, log=log)
            if done is not None:
                continue
            # The wipe failed, so the civ is warned instead.
        if warn and missed >= warn:
            on = new_turn if new_turn is not None and new_turn > turn else turn
            _append_note(store, civ, on,
                         warning_note(civ, missed, reclaim_at), log=log)
            log(f'  abandonment: warned {civ}, {missed} missed turn(s), on '
                f'turn {on}')
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
