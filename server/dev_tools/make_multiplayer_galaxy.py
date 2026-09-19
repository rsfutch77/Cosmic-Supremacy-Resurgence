"""
make_multiplayer_galaxy.py , a galaxy with one civ per player and nothing else
==============================================================================
    python make_multiplayer_galaxy.py fresh.b64 --player Alice --player Bob
    python make_multiplayer_galaxy.py fresh.b64 --player Alice --player Bob \\
        --player Carol --store \\\\HOST\\Sharing\\cosmic\\galaxy2 --turn-seconds 3600
    python make_multiplayer_galaxy.py fresh.b64 --list

A multiplayer galaxy is humans only: no engine opponent, no leftover `BadGuy`,
exactly as many civs as there are players. The test galaxies on this branch
break that because they were grown from single-player fixtures, where the
second civ is scenery that holds a planet and takes up a seat.

That matters beyond tidiness. An unplayed civ is one nobody sends orders for,
so it sits there occupying a homeworld and a share of the map that a player
could have had.

What it does not do is act. Eight turns of a three-civ galaxy with nothing
submitted by anyone left every ship idle, every production queue as it was and
every research field unset, and the same run stamped as a different civ agreed.
The one order a galaxy starts with is written at generation, not during a tick;
`clear_ship_orders` takes it out.

**No civ is removed, because none has to be.** A freshly generated galaxy has
exactly two, so two players are a rename and more players are additions. There
is no path here that deletes an empire, which is the operation most likely to
leave dangling references, and not writing it is the main reason this is short.

What it does:

    rename the existing civs            one per player, in order
    rename their homeworlds             "<player>'s HQ", the engine's own idiom
    add civs for any players beyond two `inject_civ`, each with a homeworld
    give added civs matching ships      so seat three is not a worse start
    clear every ship's order            generation seeds one; see below
    equalise homeworlds                 generation does not; see below
    empty every civ's EXSY              an added civ inherits its donor's
    publish, optionally                 to a turn store, with the roster set

It expects a **freshly generated** galaxy. Run on a game in progress it would
strip the ship orders everyone had already given.

`Owner:4` is left alone. It reads 0 on one generated civ and 21 on the other,
its meaning is unsettled, and `inject_civ` assigns `max + 1` to civs it adds,
which is enough for them to be distinct. Renaming does not touch it.
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
import inject_ship as ish
import merge_orders as mo

NAME_LIMIT = 15          # the engine's name buffer


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

    Disagreement is reported and the smallest taken, because handing a new
    player more than an existing one is the worse mistake of the two.
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

    The two civs a galaxy is generated with do not get equal worlds. Across
    five independent generations captured on this machine the second civ's
    homeworld reads exactly the same two `PLPR` bytes every time, +4 = 32 and
    +11 = 44, while the first civ's read 62/94 or 42/194: the first civ carries
    the homeworld customisation the setup screens apply and the second gets the
    engine's opponent default. `PLPR+4` is `Planet:96`, the per-unit output
    rates, and the pair is what homeworld customisation writes.

    The effect is not cosmetic. Eight turns of a three-civ galaxy with no orders
    from anyone grew the first civ's world from 7 citizens to 9 and left the
    other two at 7, and the same run stamped as the second civ gave the same
    answer, so it is the planets and not who the tick client plays.

    Only the bytes before the population count are copied, which in a fresh
    galaxy is exactly those two. Anything more is reported.
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
        diff = [i for i in range(len(ref)) if head[i] != ref[i]]
        if not diff:
            continue
        out = bytearray(blob)
        out[sec.payload:sec.payload + mo.POP_COUNT_OFF] = ref
        blob = bytes(out)
        log(f'  {civ}: homeworld {target} matched to {ref_civ}\'s, '
            f'{len(diff)} byte(s) at ' +
            ', '.join(f'+{i} {head[i]}->{ref[i]}' for i in diff[:6]))
    return blob


def reset_exsy(blob: bytes, players, log=print) -> bytes:
    """Forget what an added civ inherited from the civ it was cloned from.

    `inject_civ` copies `EXSY` verbatim, so a civ added from a donor starts
    knowing every system the donor had entered. On a fresh galaxy that is one
    row, the donor's own home system, which is precisely the thing a new player
    should not be handed: seat three opens the map and knows where seat two
    lives.

    Every row is dropped rather than the donor's one, because a civ that has
    just been created has entered nothing. The engine writes the row for its own
    system when the galaxy is loaded, which is measured: a civ built this way
    came back knowing exactly its own system after one turn.
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

    A freshly generated galaxy is not order-free. Every generation captured on
    this machine, at turn 0 and before anyone has played, gives the second civ's
    first ship order type 2, Scout, with an 82-byte `ROUT`, while its other ship and
    both of the first civ's are idle. In single player that is the engine
    getting its opponent moving. In a humans-only galaxy it is an order nobody
    issued, handed to whoever draws seat two, and it also travels into any civ
    `add_ship` copies from.

    Clearing it is the three edits `inject_order.py` makes, reversed: the `SHCO`
    order-type byte, the has-orders byte, and the conditional `ROUT` and
    trailing word. The result is byte-identical to the idle `DYNO` the engine
    writes itself, which is what makes this safe to do to every ship.
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

    # Rename through a placeholder when a wanted name is already taken by a
    # civ we have not renamed yet, or the uniqueness check in add_civ and the
    # engine's own registry both see a collision that is about to disappear.
    for i, want in enumerate(players[:len(existing)]):
        current = icv.owner_records(blob)[i]['name']
        if want == current:
            continue
        if want in [o['name'] for o in icv.owner_records(blob)]:
            tmp = f'__tmp{i}'
            blob = rename_civ(blob, want, tmp, log=log)
        blob = rename_civ(blob, current, want, log=log)
    # any placeholders left belong to a later player
    for i, want in enumerate(players[:len(existing)]):
        for o in icv.owner_records(blob):
            if o['name'].startswith('__tmp'):
                blob = rename_civ(blob, o['name'], want, log=log)
                break

    # How many ships a starting civ has, so added ones start level. inject_civ
    # transplants a homeworld and explicitly does not give ships, so without
    # this every player past the second begins with no colony ship at all while
    # the first two have two each. That is not visible in any count the
    # generator prints, and the player who drew third seat is the one who finds
    # out.
    want_ships = _starting_ships(blob, log=log)

    for extra in players[len(existing):]:
        log(f'\nadding {extra!r}')
        blob, _used = icv.add_civ(blob, extra, log=log)
        for _ in range(want_ships):
            blob = ish.add_ship(blob, extra, log=lambda *a: None)
        if want_ships:
            log(f'  {want_ships} ship(s) to match the starting civs')

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
