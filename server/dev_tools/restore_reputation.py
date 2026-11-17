"""
restore_reputation.py: give a civ back reputation a referee fault took
=======================================================================
    python restore_reputation.py <store> --civ "Elder Race" --add 8.074 --dry-run
    python restore_reputation.py <store> --civ "Elder Race" --add 8.074 --at-next-turn
    python restore_reputation.py <store> --civ "Elder Race" --add 8.074 --turn 187

Adds to a civ's reputation (`Owner:32`, an f32 in its `OWPR`) in one published
turn and in every view of it. The turn and its views are rewritten in place:
the clock, the submissions and the list of who submitted are left as they are.
The galaxy's `hash` and the archive record that names the turn are updated,
with the old hash kept in a `reputation_restored` note.

Edit at a publish, not mid-turn. `--at-next-turn` waits for the referee to
publish, then edits that turn. `--save` keeps the blobs as they were before.
"""
import argparse
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import canonical
import inject_civ as icv
import merge_orders as mo
import save_parser as sp
import turn_store
from turn_store import open_store

REPUTATION_AFTER_RESEARCH = 27      # f32 Owner:32, as `sim/load.py` reads it


def reputation_at(blob, name):
    """(offset, value) of a civ's reputation, or None when its OWPR does not
    hold one."""
    oid = next((o['oid'] for o in icv.owner_records(blob) if o['name'] == name),
               None)
    owpr = mo._owpr(blob, oid) if oid is not None else None
    offs = mo.research_offsets(blob, owpr) if owpr is not None else None
    if not offs or offs[1] + REPUTATION_AFTER_RESEARCH + 4 > owpr.size:
        return None
    at = owpr.payload + offs[1] + REPUTATION_AFTER_RESEARCH
    return at, struct.unpack_from('<f', blob, at)[0]


def with_reputation(blob, name, add):
    """(blob, before, after) with `add` added to the civ's reputation, or
    (blob, None, None) when the blob does not hold it."""
    got = reputation_at(blob, name)
    if got is None:
        return blob, None, None
    at, before = got
    out = bytearray(blob)
    struct.pack_into('<f', out, at, before + add)
    out = bytes(out)
    sp.parse_blob(out)
    return out, before, struct.unpack_from('<f', out, at)[0]


def _write(store, turn, blob, civ=None):
    data = sp.encode_save(blob)
    if hasattr(store, 'turn_object'):
        name = (store.view_object(civ, turn) if civ else
                store.turn_object(turn))
        store._put(name, data, 'text/plain')
    else:
        turn_store._atomic_write(store.view_path(civ, turn) if civ else
                                 store.turn_path(turn), data)


def restore(store, turn, name, add, dry_run=False, save=None, log=print):
    """Add to `name`'s reputation in `turn` and its views. True if written."""
    blob = store.turn_blob(turn)
    out, before, after = with_reputation(blob, name, add)
    if before is None:
        raise SystemExit(f'turn {turn}: {name} has no reputation to change')
    log(f'turn {turn}: {name} reputation {before:.3f} -> {after:.3f}')
    viewers = (store.state().get(turn_store.VIEWS_KEY) or {}).get(str(turn), [])
    views = {}
    for civ in viewers:
        view = store.view_blob(civ, turn)
        if view is None:
            continue
        changed, v_before, v_after = with_reputation(view, name, add)
        if v_before is None:
            log(f'  view {civ}: holds no reputation for {name}; left alone')
            continue
        log(f'  view {civ}: {v_before:.3f} -> {v_after:.3f}')
        views[civ] = (view, changed)
    if save:
        os.makedirs(save, exist_ok=True)
        with open(os.path.join(save, f'{turn:04d}.b64'), 'wb') as f:
            f.write(sp.encode_save(blob))
        for civ, (view, _c) in views.items():
            with open(os.path.join(save, f'{turn:04d}_{civ}.b64'), 'wb') as f:
                f.write(sp.encode_save(view))
        log(f'  the blobs as they were are in {save}')
    if dry_run:
        log('  dry run: nothing written')
        return False

    for civ, (_view, changed) in views.items():
        _write(store, turn, changed, civ)
    _write(store, turn, out)
    old, new = canonical.canonical_hash(blob), canonical.canonical_hash(out)
    store.update_state({'hash': new})
    log(f'  turn {turn} and {len(views)} view(s) rewritten, canonical '
        f'{new[:16]}')

    rec = store.archive_record(turn - 1)
    version = (rec or {}).get('hash_version', 1)
    if (rec and rec.get('published') == turn
            and rec.get('hash_out') == canonical.canonical_hash(blob, version)):
        rec['hash_out'] = canonical.canonical_hash(out, version)
        rec['reputation_restored'] = {'at': time.time(), 'civ': name,
                                      'add': add, 'hash_out_before': old}
        store.archive(turn - 1, rec)
        log(f'  archive record {turn - 1} updated, with the old hash kept in it')
    elif rec:
        log(f'  archive record {turn - 1} does not name this blob; left alone')
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('store')
    ap.add_argument('--civ', required=True)
    ap.add_argument('--add', type=float, required=True,
                    help='reputation to add; negative takes it away')
    ap.add_argument('--turn', type=int, help='edit this turn now')
    ap.add_argument('--at-next-turn', action='store_true',
                    help='wait for the referee to publish, then edit that turn')
    ap.add_argument('--dry-run', action='store_true',
                    help='say what would change on the current turn')
    ap.add_argument('--save', help='a folder for the blobs as they were')
    ap.add_argument('--poll', type=float, default=5.0)
    ap.add_argument('--timeout', type=float, default=6 * 3600.0)
    a = ap.parse_args()

    store = open_store(a.store)
    if not store.exists():
        raise SystemExit(f'no galaxy at {a.store}')
    if a.dry_run:
        turn, _ = store.current()
        restore(store, a.turn or turn, a.civ, a.add, dry_run=True, save=a.save)
        return 0
    if a.turn is not None:
        return 0 if restore(store, a.turn, a.civ, a.add, save=a.save) else 1
    if not a.at_next_turn:
        raise SystemExit('say --turn N, --at-next-turn or --dry-run')

    start, _deadline = store.current()
    print(f'turn {start} is in flight, {round(store.seconds_left())}s left; '
          f'waiting for the next publish')
    give_up = time.time() + a.timeout
    while time.time() < give_up:
        turn, _ = store.current()
        if turn != start:
            print(f'turn {turn} published')
            return 0 if restore(store, turn, a.civ, a.add, save=a.save) else 1
        time.sleep(a.poll)
    raise SystemExit(f'no new turn within {a.timeout}s; the referee may be down')


if __name__ == '__main__':
    sys.exit(main())
