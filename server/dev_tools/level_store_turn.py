"""
level_store_turn.py: level homeworld rates in a galaxy that is already running
===============================================================================
    python level_store_turn.py <store> --list
    python level_store_turn.py <store> --from-planet 139 --planet 140 --at-next-turn
    python level_store_turn.py <store> --from-planet 139 --planet 140 --turn 21

Copies the homeworld rate pair (`PLPR` bytes `+4` and `+11`) from one planet
to others in a live galaxy. `make_multiplayer_galaxy.py` does the same at
generation.

Edit at a publish, not mid-turn: players hold the current turn's blob, and a
submission judged against a state never served can lose orders.
`--at-next-turn` waits for the referee to publish, then edits that turn.

The archive record for turn N holds the hash of the blob published as turn
N+1, so it is updated to the new hash and given a `levelled` note with the old
hash. The original deadline is written back after `publish`.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import canonical
import inject_civ as icv
import make_multiplayer_galaxy as mmg
import merge_orders as mo
import save_parser as sp
from turn_store import TurnStore, open_store


def planets(blob):
    """Every owned planet as (id, civ name, PLPR payload), for --list."""
    names = {o['oid']: o['name'] for o in icv.owner_records(blob)}
    out = []
    for poid, (owner, _prod, plpr, nm) in sorted(mo.planet_index(blob).items()):
        if owner and plpr:
            name = nm[1].decode('latin1') if nm and nm[0] else ''
            out.append((poid, names.get(owner, owner), plpr, name))
    return out


def copy_rates(blob, source: int, targets, log=print):
    """Copy the rate pair from one planet's PLPR to others. Nothing else moves.

    Planets are named by id: on a played galaxy a civ's most populous planet
    is not necessarily its homeworld.
    """
    src = mmg._plpr(blob, source)
    if src is None:
        raise SystemExit(f'planet {source} has no PLPR')
    ref = bytes(blob[src.payload:src.payload + mo.POP_COUNT_OFF])
    out = bytearray(blob)
    changed = False
    for target in targets:
        sec = mmg._plpr(blob, target)
        if sec is None:
            raise SystemExit(f'planet {target} has no PLPR')
        head = bytes(blob[sec.payload:sec.payload + mo.POP_COUNT_OFF])
        moved = [i for i in mmg.RATE_OFFSETS if head[i] != ref[i]]
        if not moved:
            log(f'  planet {target}: rates already match planet {source}')
            continue
        for i in moved:
            out[sec.payload + i] = ref[i]
        changed = True
        log(f'  planet {target}: ' +
            ', '.join(f'+{i} {head[i]}->{ref[i]}' for i in moved) +
            f', copied from planet {source}')
    return (bytes(out) if changed else blob)


def level(store, turn: int, source: int, targets, log=print) -> bool:
    """Copy the rate pair in one published turn. True if anything changed."""
    blob = store.turn_blob(turn)
    before = canonical.canonical_hash(blob)
    log(f'turn {turn}: {len(blob):,} bytes, canonical {before[:16]}')

    out = copy_rates(blob, source, targets, log=log)
    if out == blob:
        log('  nothing to change; the rates already match')
        return False
    sp.parse_blob(out)                      # refuse to publish what will not parse
    after = canonical.canonical_hash(out)

    state = store.state()
    deadline, seconds = state['deadline'], state['turn_seconds']
    store.publish(turn, out)
    state = store.state()
    state['deadline'] = deadline            # publish restarts the clock
    store._put_state(state)
    log(f'  published turn {turn} again, canonical {after[:16]}, '
        f'{round(deadline - time.time())}s still on the clock')

    rec = store.archive_record(turn - 1)
    if rec and rec.get('published') == turn and rec.get('hash_out') == before:
        rec['hash_out'] = after
        rec['levelled'] = {
            'at': time.time(),
            'source_planet': source,
            'planets': list(targets),
            'offsets': list(mmg.RATE_OFFSETS),
            'hash_out_before': before,
        }
        store.archive(turn - 1, rec)
        log(f'  archive record {turn - 1} updated, with the old hash kept in it')
    elif rec:
        log(f'  archive record {turn - 1} does not name this blob; left alone')
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('store')
    ap.add_argument('--from-planet', type=int,
                    help='the planet whose rate pair is copied')
    ap.add_argument('--planet', action='append', type=int, default=[],
                    help='a planet to write it to, repeatable')
    ap.add_argument('--list', action='store_true',
                    help='print every owned planet and its rates, then stop')
    ap.add_argument('--turn', type=int,
                    help='level this turn now, even if it is in flight')
    ap.add_argument('--at-next-turn', action='store_true',
                    help='wait for the referee to publish, then level that turn')
    ap.add_argument('--poll', type=float, default=5.0)
    ap.add_argument('--timeout', type=float, default=3600.0)
    a = ap.parse_args()

    store = open_store(a.store)
    if not store.exists():
        raise SystemExit(f'no galaxy at {a.store}')
    if not isinstance(store, TurnStore):
        raise SystemExit('this writes state.json directly, so it needs a '
                         'directory store rather than a service')
    if a.list:
        turn, _ = store.current()
        blob = store.turn_blob(turn)
        print(f'turn {turn}, civs {store.civs()}')
        for poid, civ, plpr, name in planets(blob):
            print(f'  planet {poid:<5} {civ:<12} +4 {plpr[4]:<4} '
                  f'+11 {plpr[11]:<4} {name!r}')
        return 0
    if a.from_planet is None or not a.planet:
        raise SystemExit('say --from-planet N and at least one --planet M, '
                         'or --list to see what is there')

    if a.turn is not None:
        return 0 if level(store, a.turn, a.from_planet, a.planet) else 1

    if not a.at_next_turn:
        raise SystemExit('say --turn N or --at-next-turn')

    start, _deadline = store.current()
    print(f'turn {start} is in flight, {round(store.seconds_left())}s left; '
          f'waiting for the next publish')
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        turn, _ = store.current()
        if turn != start:
            print(f'turn {turn} published')
            return 0 if level(store, turn, a.from_planet, a.planet) else 1
        time.sleep(a.poll)
    raise SystemExit(f'no new turn within {a.timeout}s; the referee may be down')


if __name__ == '__main__':
    sys.exit(main())
