"""
make_multiplayer_galaxy.py: a galaxy with one civ per player and nothing else
==============================================================================
    python make_multiplayer_galaxy.py fresh.b64 --player Alice --player Bob
    python make_multiplayer_galaxy.py fresh.b64 --player Alice --player Bob \\
        --player Carol --store \\\\HOST\\Sharing\\cosmic\\galaxy2 --turn-seconds 3600
    python make_multiplayer_galaxy.py fresh.b64 --list

A multiplayer galaxy is humans only: no engine opponent, exactly as many civs
as there are players. A freshly generated galaxy has two civs, so two players
are a rename and more are additions; no civ is ever removed.

What it does:

    rename the existing civs            one per player, in order
    give every civ a non-zero Owner:4   generation gives the human seat 0
    rename their homeworlds             "<player>'s HQ", the engine's own idiom
    add civs for any players beyond two `inject_civ`, each with a homeworld
                                        and this galaxy's own starting fleet
    clear every ship's order            generation seeds one; see below
    equalise homeworlds                 generation does not; see below
    empty every civ's EXSY              an added civ inherits its donor's
    publish, optionally                 to a turn store, with the roster set

It expects a freshly generated galaxy. Run on a game in progress it would
strip the ship orders everyone had already given.

`Owner:4`, the last u32 of an `OWNR` payload, has to be non-zero: a civ holding
0 is never empire-ticked, so its research, `Owner:24` and score never move
while its cash does. Generation gives the human seat 0 and the rival 21, so the
seat-one civ would play the whole galaxy without research. A zero takes the
lowest unused id from 20 up, as `make_single_player_galaxy.fix_player_civ_id`
does, before any civ is added; `inject_civ` gives added civs `max + 1`.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import save_parser as sp
import exsy
import inject_civ as icv
import inject_order as ino
import merge_orders as mo

NAME_LIMIT = 15          # the engine's name buffer

# The two PLPR bytes homeworld customisation writes, the only difference
# between a generated galaxy's two homeworlds. PLPR+4 is Planet:96, the
# per-unit output rates.
RATE_OFFSETS = (4, 11)

# The lowest Owner:4 a zero is replaced with; generated civs read 20, 21, ...
FIRST_OWNER_ID = 20


def rename_civ(blob: bytes, old: str, new: str, log=print) -> bytes:
    """Rename a civ in place. The name is length-prefixed, so sizes move."""
    if len(new) > NAME_LIMIT:
        raise SystemExit(f'{new!r} is longer than the {NAME_LIMIT}-char buffer')
    rec = next((o for o in icv.owner_records(blob) if o['name'] == old), None)
    if rec is None:
        raise SystemExit(f'no civ named {old!r}; saw '
                         f'{[o["name"] for o in icv.owner_records(blob)]}')
    if new == old:
        return blob
    body = struct.pack('<I', len(new)) + new.encode('ascii')
    out = icv.splice(blob, rec['payload'], rec['payload'] + 4 + rec['nlen'],
                     body, log=lambda *a: None, what='civ name')
    log(f"  civ {old!r} -> {new!r} (object {rec['oid']})")
    return out


def seated(blob):
    """Civs in seat order, which is the object id and not the blob order.

    The engine's serialiser does not keep civs in allocation order. Seat one
    is the lowest object id (as in `inject_civ.seat_one`) and carries the
    homeworld customisation `equalise_homeworlds` levels everyone to.
    """
    return sorted(icv.owner_records(blob), key=lambda o: o['oid'])


def homeworld(blob: bytes, civ_name: str):
    """A civ's most populous planet, which in a fresh galaxy is its only one."""
    civ = next((o for o in icv.owner_records(blob) if o['name'] == civ_name),
               None)
    if civ is None:
        raise SystemExit(f'no civ named {civ_name!r}')
    idx = mo.planet_index(blob)
    mine = [oid for oid, (owner, _p, _c, _n) in idx.items()
            if owner == civ['oid']]
    if not mine:
        return None

    def pop(oid):
        plpr = idx[oid][2]
        return len(mo.citizens_of(plpr) or []) if plpr else 0
    return max(mine, key=pop)


def owner_ids(blob):
    """{civ name: Owner:4}, the u32 ending each OWNR payload."""
    return {o['name']: struct.unpack_from('<I', blob, o['end'] - 4)[0]
            for o in icv.owner_records(blob)}


def fix_owner_ids(blob: bytes, log=print) -> bytes:
    """Give every civ holding Owner:4 = 0 the lowest unused id from 20 up.

    Four bytes in place per civ; no section length changes.
    """
    ids = owner_ids(blob)
    taken = {v for v in ids.values() if v}
    out = bytearray(blob)
    for o in icv.owner_records(blob):
        if ids[o['name']]:
            continue
        uid = next(i for i in range(FIRST_OWNER_ID, 1 << 16) if i not in taken)
        taken.add(uid)
        struct.pack_into('<I', out, o['end'] - 4, uid)
        log(f"  {o['name']}: Owner:4 0 -> {uid}, or its research and score "
            f"would never tick")
    return bytes(out)


def rename_homeworld(blob: bytes, civ_name: str, log=print) -> bytes:
    """Name a civ's homeworld after them, as the generator does."""
    target = homeworld(blob, civ_name)
    if target is None:
        log(f'  {civ_name!r} owns no planet to name')
        return blob
    want = f"{civ_name}'s HQ".encode('ascii')
    if len(want) > mo.MAX_NAME:
        want = civ_name.encode('ascii')[:mo.MAX_NAME]
    out = mo.set_planet_name(blob, target, want)
    log(f'  planet {target} -> {want.decode()!r}')
    return out


def _starting_ships(blob, log=print) -> int:
    """How many ships each existing civ has, if they agree.

    On disagreement, logs it and returns the smallest.
    """
    counts = {}
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    names = {o['oid']: o['name'] for o in icv.owner_records(blob)}
    for sec in glxy.find('SHIP'):
        _oid, _x, _z, _y, owner = struct.unpack_from('<IfffI', blob,
                                                     sec.payload)
        n = names.get(owner, owner)
        counts[n] = counts.get(n, 0) + 1
    for name in names.values():
        counts.setdefault(name, 0)
    if len(set(counts.values())) > 1:
        log(f'  starting civs hold different fleets, {counts}; '
            f'giving new civs the smallest')
    return min(counts.values()) if counts else 0


def _plpr(blob, planet_oid):
    """A planet's PLPR section, or None."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sec in glxy.find('PLNT'):
        if struct.unpack_from('<I', blob, sec.payload)[0] == planet_oid:
            return next(sec.find('PLPR'), None)
    return None


def equalise_homeworlds(blob: bytes, players, log=print) -> bytes:
    """Give every player the same homeworld the first player has.

    The first generated civ carries the homeworld customisation the setup
    screens apply (e.g. +4/+11 = 62/94); the second gets the engine's opponent
    default 32/44, which grows more slowly.

    Only the rate pair `RATE_OFFSETS` is copied. Other differing bytes in the
    head are stores and production on a played galaxy; they are logged and
    left alone.
    """
    ref_civ = players[0]
    ref_home = homeworld(blob, ref_civ)
    if ref_home is None:
        log(f'  {ref_civ!r} has no homeworld to copy from')
        return blob
    sec = _plpr(blob, ref_home)
    if sec is None:
        log(f'  {ref_civ!r}\'s homeworld has no PLPR to copy from')
        return blob
    ref = bytes(blob[sec.payload:sec.payload + mo.POP_COUNT_OFF])

    for civ in players[1:]:
        target = homeworld(blob, civ)
        if target is None:
            continue
        sec = _plpr(blob, target)
        if sec is None:
            continue
        head = bytes(blob[sec.payload:sec.payload + mo.POP_COUNT_OFF])
        mine = [i for i in RATE_OFFSETS if head[i] != ref[i]]
        theirs = [i for i in range(len(ref))
                  if head[i] != ref[i] and i not in RATE_OFFSETS]
        if mine:
            out = bytearray(blob)
            for i in mine:
                out[sec.payload + i] = ref[i]
            blob = bytes(out)
            log(f'  {civ}: homeworld {target} rates matched to {ref_civ}\'s, ' +
                ', '.join(f'+{i} {head[i]}->{ref[i]}' for i in mine))
        if theirs:
            log(f'  {civ}: left {len(theirs)} other differing byte(s) alone at '
                + ', '.join(f'+{i}' for i in theirs))
    return blob


def reset_exsy(blob: bytes, players, log=print) -> bytes:
    """Forget what an added civ inherited from the civ it was cloned from.

    `inject_civ` copies `EXSY` verbatim, so an added civ would know the
    donor's home system. Every row is dropped; the engine writes the row for a
    civ's own system on load.
    """
    for _ in range(64):                      # one rewrite per table, bounded
        tree = sp.parse_blob(blob)
        glxy = next(tree[0].find('GLXY'))
        by_off = {r['off']: r['name'] for r in icv.owner_records(blob)}
        target = None
        for ownr in glxy.find('OWNR'):
            name = by_off.get(ownr.start)
            if name is None:
                continue
            sec = next(ownr.find('EXSY'), None)
            if sec is None:
                continue
            payload = bytes(blob[sec.payload:sec.end])
            try:
                ids = exsy.systems_known(payload)
            except Exception as exc:
                log(f'  {name}: EXSY unreadable, left alone ({exc})')
                continue
            if not ids:
                continue
            target = (tree, sec, name, ids)
            break
        if target is None:
            break
        tree, sec, name, ids = target
        blob = sp.replace_payload(blob, tree, sec, exsy.build([]))
        log(f'  {name}: forgot {len(ids)} inherited system(s) {ids}')
    return blob


def clear_ship_orders(blob: bytes, log=print) -> bytes:
    """Start every ship idle, because orders belong to players.

    Generation gives the second civ's first ship a Scout order (type 2, an
    82-byte `ROUT`). Clearing is the three edits `inject_order.py` makes,
    reversed: the `SHCO` order-type byte, the has-orders byte, and the
    conditional `ROUT` and trailing word. The result is byte-identical to an
    idle `DYNO` the engine writes.
    """
    cleared = []
    while True:
        tree = sp.parse_blob(blob)
        glxy = next(tree[0].find('GLXY'))
        hit = None
        for sec in glxy.find('SHIP'):
            dyno = next(sec.find('DYNO'), None)
            if dyno is None:
                continue
            d = ino.parse_dyno(blob, dyno)
            if not d['shco'][0] and not d['has_orders'] and d['rout'] is None:
                continue
            hit = (struct.unpack_from('<I', blob, sec.payload)[0], d,
                   dyno.version)
            break
        if hit is None:
            break
        oid, d, version = hit
        cleared.append((oid, d['shco'][0]))
        d['shco'][0] = 0
        d['has_orders'] = 0
        d['rout'] = None
        d['tail_u32'] = None
        blob = mo.replace_dyno(
            blob, oid, sp.build_section(b'DYNO', version, ino.build_dyno(d)))
    if cleared:
        log(f'  cleared {len(cleared)} starting order(s): '
            + ', '.join(f'ship {o} type {k}' for o, k in cleared))
    return blob


def build(blob: bytes, players, log=print) -> bytes:
    existing = [o['name'] for o in icv.owner_records(blob)]
    log(f'starting from {len(existing)} civ(s): {existing}')
    if len(players) < 2:
        raise SystemExit('a multiplayer galaxy needs at least two players')
    if len(set(players)) != len(players):
        raise SystemExit('player names have to be distinct')

    # Rename through a placeholder when a wanted name is still held by a civ
    # not yet renamed, so names stay unique at every step.
    for i, want in enumerate(players[:len(existing)]):
        current = seated(blob)[i]['name']
        if want == current:
            continue
        if want in [o['name'] for o in icv.owner_records(blob)]:
            tmp = f'__tmp{i}'
            blob = rename_civ(blob, want, tmp, log=log)
        blob = rename_civ(blob, current, want, log=log)
    # any placeholders left belong to a later player
    for i, want in enumerate(players[:len(existing)]):
        for o in seated(blob):
            if o['name'].startswith('__tmp'):
                blob = rename_civ(blob, o['name'], want, log=log)
                break

    # Before any civ is added, so `add_civ`'s `max + 1` stays clear of the
    # ids handed out here.
    blob = fix_owner_ids(blob, log=log)

    # Added civs get this galaxy's own starting fleet size.
    want_ships = _starting_ships(blob, log=log)

    for extra in players[len(existing):]:
        log(f'\nadding {extra!r}')
        blob, _used = icv.add_civ(blob, extra, ships=want_ships, log=log)

    log('\nnaming homeworlds')
    for name in players:
        blob = rename_homeworld(blob, name, log=log)

    log('\nlevelling the start')
    # last, so they cover ships and worlds that came from a civ that had one
    blob = clear_ship_orders(blob, log=log)
    blob = equalise_homeworlds(blob, players, log=log)
    blob = reset_exsy(blob, players, log=log)

    final = [o['name'] for o in icv.owner_records(blob)]
    if sorted(final) != sorted(players):
        raise SystemExit(f'ended with {final}, wanted {players}')
    log(f'\n{len(final)} civ(s), one per player: {final}')
    return blob


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('blob', help='a freshly generated galaxy capture')
    ap.add_argument('--player', action='append', default=[],
                    help='a player name, repeatable; one civ each')
    ap.add_argument('--store', help='publish the result here as turn 1')
    ap.add_argument('--turn-seconds', type=int, default=3600)
    ap.add_argument('--dat', help='also write it decompressed')
    ap.add_argument('-o', '--out', help='write a .b64 capture here')
    ap.add_argument('--list', action='store_true',
                    help='describe the input and stop')
    a = ap.parse_args()

    blob = sp.load_any(a.blob)
    if a.list or not a.player:
        icv.describe(blob)
        for oid, (owner, _p, plpr, nm) in sorted(mo.planet_index(blob).items()):
            if owner:
                cz = mo.citizens_of(plpr) if plpr else None
                print(f'  planet {oid:<5} owner {owner:<5} pop '
                      f'{len(cz or []):<3} '
                      f'{nm[1].decode("latin1") if nm and nm[0] else "(unnamed)"!r}')
        return 0

    out = build(blob, a.player)
    sp.parse_blob(out)

    if a.out:
        open(a.out, 'wb').write(sp.encode_save(out))
        print(f'wrote {a.out}')
    if a.dat:
        if not a.dat.lower().endswith('.dat'):
            raise SystemExit('--dat path must end in .dat')
        open(a.dat, 'wb').write(out)
        print(f'wrote {a.dat}')
    if a.store:
        from turn_store import open_store
        store = open_store(a.store)
        turn = store.start(out, civs=a.player, turn_seconds=a.turn_seconds)
        print(f'published turn {turn} to {a.store}, {a.turn_seconds}s per turn, '
              f'roster {a.player}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
