"""
restore_galaxy.py: a galaxy restored to an earlier turn, into another store

    python server/dev_tools/restore_galaxy.py firebase://cs-resurgence/sandbox ../ops2/sandbox --turn 180
    python server/dev_tools/restore_galaxy.py SRC DST --turn 180 --dry-run

Reads `SRC` and writes nothing to it. `DST` must not hold a galaxy yet. The
copy gets turn N's blob, the source's roster, seats and settings, and every
archive record up to N; each civ's view of turn N is computed again, as the
referee does, since a store keeps views for the last two turns only. The copy
is open, with a fresh deadline and no submissions.

Restoring the live galaxy in place is: restore it into a folder, check the
copy (the operator page, a launcher pointed at it), then publish the copy's
turn N over the live one with the referee stopped.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
for d in (SERVER, HERE):
    if d not in sys.path:
        sys.path.insert(0, d)

import turn_store                                               # noqa: E402

# State the copy makes for itself rather than taking from the source.
OWN_FIELDS = {'turn', 'deadline', 'hash', turn_store.VIEWS_KEY,
              turn_store.STATUS_KEY, turn_store.CLOSED_REASON_KEY}


def carried_state(state: dict) -> dict:
    return {k: v for k, v in state.items() if k not in OWN_FIELDS}


def restore(src, dst, turn: int, turn_seconds: int = None, dry_run=False,
            log=print) -> dict:
    """Restore `src` at `turn` into `dst`, and say what was copied."""
    import referee
    import inject_civ as icv
    if dst.exists():
        raise SystemExit(f'{dst!r} already holds a galaxy; restore into an '
                         f'empty store')
    state = src.state()
    if not src.has_turn(turn):
        raise SystemExit(f'{src!r} has no turn {turn}; it is at turn '
                         f'{state.get("turn")}')
    blob = src.turn_blob(turn)
    civs = list(state.get('civs', []))
    seconds = int(turn_seconds or state.get('turn_seconds') or 3600)
    records = {t: r for t in range(0, turn + 1)
               for r in [src.archive_record(t)] if r is not None}
    done = {'turn': turn, 'civs': civs, 'turn_seconds': seconds,
            'archive_records': len(records),
            'fields': sorted(carried_state(state))}
    if dry_run:
        log(f'would restore turn {turn} of {src!r} into {dst!r}: '
            f'{len(civs)} civs, {len(records)} archive records')
        return done
    dst.start(blob, civs, turn_seconds=seconds, turn=turn)
    dst.update_state(carried_state(state))
    held = referee.views(blob, [o['name'] for o in icv.owner_records(blob)],
                         log=log)
    dst.publish(turn, blob, turn_seconds=seconds,
                views=referee.published_views(blob, held))
    for t, rec in sorted(records.items()):
        dst.archive(t, rec)
    done['views'] = sorted(referee.published_views(blob, held))
    log(f'restored turn {turn} of {src!r} into {dst!r}: {len(civs)} civs, '
        f'{len(done["views"])} views, {len(records)} archive records')
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('src', help='the galaxy to restore from (read only)')
    ap.add_argument('dst', help='an empty store to restore into')
    ap.add_argument('--turn', type=int, required=True)
    ap.add_argument('--turn-seconds', type=int)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    restore(turn_store.open_store(a.src), turn_store.open_store(a.dst), a.turn,
            turn_seconds=a.turn_seconds, dry_run=a.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
