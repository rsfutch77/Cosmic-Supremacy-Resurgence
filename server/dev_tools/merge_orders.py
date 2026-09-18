"""
merge_orders.py , build the next authoritative blob from players' returned saves
================================================================================
    python merge_orders.py authoritative.b64 \
        --from DemoPlayer=playerA.b64 --from BadGuy=playerB.b64 --dat merged.dat
    python merge_orders.py authoritative.b64 --from BadGuy=playerB.b64 --list

The design rule is merge orders, never states. A player is handed a state, plays
offline, and hands a whole state back; only the parts of it that express that
player's intent may be taken, and only for objects that player owns.

This implements that rule for ship movement, which is the one order type whose
blob representation is fully measured. A move is three coordinated edits and all
three live inside the ship's `DYNO` section:

    SHCO byte 0     the order type, 0 when idle
    has-orders      Ship:76
    ROUT + u32      appended, and present only when an order exists

Because they are contiguous, taking a player's order is copying their `DYNO`
over the authoritative one. Nothing outside `DYNO` is read, so a player who
edits their planets, their research or another civ's ships changes nothing.

Ownership is decided by the AUTHORITATIVE blob, never by the submitted one, so
a player cannot claim a ship by rewriting the owner field in their own copy.
Every ship the submission changed that the player does not own is dropped and
named.

Not yet handled, and each needs its own measurement before it can be accepted:
production queues, research topic, job allocation, facility selection, ship
designs, governors, admirals and diplomacy. Ship orders issued through an
admiral are also out of scope, since the admiral id lives in `DYNO` and would
be copied with the rest.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp
import inject_civ as icv


def ship_index(blob):
    """{shipObjectId: (owner, dyno_bytes)} for every ship carrying a DYNO."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for sec in glxy.find('SHIP'):
        oid, _x, _z, _y, owner = struct.unpack_from('<IfffI', blob, sec.payload)
        dyno = next(sec.find('DYNO'), None)
        if dyno is not None:
            out[oid] = (owner, bytes(blob[dyno.start:dyno.end]))
    return out


def civ_by_name(blob, name):
    for o in icv.owner_records(blob):
        if o['name'] == name:
            return o
    raise SystemExit(f"no civ named {name!r}; saw "
                     f"{[o['name'] for o in icv.owner_records(blob)]}")


def replace_dyno(blob, ship_oid, new_dyno):
    """Swap one ship's DYNO section, correcting every enclosing section size."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sec in glxy.find('SHIP'):
        if struct.unpack_from('<I', blob, sec.payload)[0] != ship_oid:
            continue
        dyno = next(sec.find('DYNO'), None)
        if dyno is None:
            raise SystemExit(f"ship {ship_oid} has no DYNO to replace")
        body = (bytes(blob[sec.payload:dyno.start]) + new_dyno +
                bytes(blob[dyno.end:sec.end]))
        return sp.replace_payload(blob, tree, sec, body)
    raise SystemExit(f"ship {ship_oid} not found in the authoritative blob")


def merge(blob, submissions, log=print):
    """submissions: [(civ_name, submitted_blob)]. Returns the merged blob.

    Every submission is judged against the state as it was at the start of the
    turn, which is what each player was served, and never against the blob as it
    accumulates other players' orders. Judging against the accumulating blob
    makes the first player's orders look like the second player's edits: the
    authoritative state moves under them, and their untouched copy of a ship they
    do not own then differs from it. A two-player round logged two such drops
    with neither player having done anything. They were harmless, being on ships
    the submitter did not own, but a drop log that cries wolf hides a real
    rejection, and the legality gate has to be able to trust it.
    """
    served = ship_index(blob)
    names = {o['oid']: o['name'] for o in icv.owner_records(blob)}
    accepted = dropped = 0
    for civ_name, sub in submissions:
        civ = civ_by_name(blob, civ_name)
        log(f"{civ_name} (object {civ['oid']}):")
        for ship_oid, (_claimed_owner, dyno) in sorted(ship_index(sub).items()):
            if ship_oid not in served:
                log(f"    ship {ship_oid}: DROPPED, not in the state served")
                dropped += 1
                continue
            owner, as_served = served[ship_oid]
            if dyno == as_served:
                continue                        # untouched, nothing to say
            if owner != civ['oid']:
                log(f"    ship {ship_oid}: DROPPED, owned by "
                    f"{names.get(owner, owner)}")
                dropped += 1
                continue
            blob = replace_dyno(blob, ship_oid, dyno)
            log(f"    ship {ship_oid}: order taken "
                f"({len(as_served)} -> {len(dyno)} bytes)")
            accepted += 1
    log(f"{accepted} order(s) taken, {dropped} change(s) dropped")
    return blob


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('authoritative')
    ap.add_argument('--from', dest='subs', action='append', default=[],
                    metavar='CIV=FILE', help='a player submission; repeatable')
    ap.add_argument('--dat', help='write the decompressed result here')
    ap.add_argument('-o', '--out', help='write a .b64 capture here')
    ap.add_argument('--list', action='store_true',
                    help='show ships and owners, change nothing')
    a = ap.parse_args()

    blob = sp.load_any(a.authoritative)
    names = {o['oid']: o['name'] for o in icv.owner_records(blob)}
    if a.list:
        for oid, (owner, dyno) in sorted(ship_index(blob).items()):
            print(f"  ship {oid:<5} owner {names.get(owner, owner):<14} "
                  f"DYNO {len(dyno)} bytes "
                  f"{'(under orders)' if len(dyno) > 38 else '(idle)'}")
        return

    submissions = []
    for spec in a.subs:
        if '=' not in spec:
            raise SystemExit(f"--from wants CIV=FILE, got {spec!r}")
        civ_name, path = spec.split('=', 1)
        submissions.append((civ_name, sp.load_any(path)))
    if not submissions:
        raise SystemExit('nothing to merge; pass at least one --from')

    merged = merge(blob, submissions)
    sp.parse_blob(merged)
    print(f"merged blob: {len(merged):,} bytes (was {len(blob):,})")

    if a.dat:
        if not a.dat.lower().endswith('.dat'):
            raise SystemExit('--dat path must end in .dat')
        open(a.dat, 'wb').write(merged)
        print(f"wrote {a.dat}")
    if a.out:
        open(a.out, 'w').write(sp.encode_save(merged))
        print(f"wrote {a.out}")


if __name__ == '__main__':
    main()
