"""
merge_orders.py , build the next authoritative blob from players' returned saves
================================================================================
    python merge_orders.py authoritative.b64 \
        --from DemoPlayer=playerA.b64 --from BadGuy=playerB.b64 --dat merged.dat
    python merge_orders.py authoritative.b64 --from BadGuy=playerB.b64 --list

The design rule is merge orders, never states. A player is handed a state, plays
offline, and hands a whole state back; only the parts of it that express that
player's intent may be taken, and only for objects that player owns.

Every submission is judged against **the state as it was served**, never against
the blob as it accumulates other players' orders. Judging against the
accumulating blob makes the first player's orders look like the second player's
edits: the authoritative state moves under them, and their untouched copy of a
ship they do not own then differs from it.

Ownership is likewise decided by the served blob. A submission that rewrites an
owner field must not thereby acquire the object. Tested by rewriting one civ's
ship to claim another owned it, planting a real order on it and submitting: the
change is dropped and named, and the submitter's own orders still apply.

What is accepted
----------------
Each rule below was measured by serving a turn, having a player perform exactly
one action, and diffing with `order_diff.py`.

**Ship orders**, `SHIP > DYNO`. A move or a colonise is three coordinated edits
and all three live inside the ship's `DYNO`: the `SHCO` order-type byte (1 for
move, 3 for colonise), the has-orders byte, and an appended `ROUT` plus `u32`.
Because they are contiguous, taking the order is copying the `DYNO`.

**Research topic**, `OWNR > DATA > OWPR` at `+32` and `+40`. Setting a topic
writes the technology id to both, eight bytes apart, and nothing else outside
this civ moves. Copied field by field rather than by section: `OWPR` is the
civ's whole property block and also holds the coat-of-arms count and several
flags, so taking all of it would take far more than a research decision.

**Production queue**, `PLNT > PLPR > PROD`. Queuing a build changes only this
section, whose payload holds the queue's own fields and a nested section naming
what is queued, `FCLT` for a facility. It is self-contained, so it is copied
whole.

What is refused, and why
------------------------
**Job allocation.** Measured: moving one farmer to a banker changes the citizen
array inside `PLNT > PLPR`, ten nine-byte records each keyed by the owning civ's
object id, plus a flag byte in `OWPR`. The array is in `PLPR`'s own bytes, which
also carry the planet's population, stores and derived economy. Copying `PLPR`
wholesale would let a player hand back an edited population, and the citizen
records are not decoded well enough to copy field by field yet. Refused until
`PLPR` is decoded.

**Everything else**: facility selection outside the queue, ship designs,
governors, admirals, diplomacy proposals. Not measured, so not accepted. An
order type nobody has measured is not a gap in a list, it is a change of unknown
extent being copied between players.

`[ ]` **Nothing here checks that an order is legal**, only that it is the
player's own. A submission naming a technology the civ cannot research, or a
queue entry it cannot afford, is copied as given. That is C4, the legality gate,
and it needs the rule the UI enforces rather than an inference from the state.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp
import inject_civ as icv

# Fields inside a civ's OWPR that carry the research topic, as (offset, length).
RESEARCH_FIELDS = ((32, 4), (40, 4))


# ── reading the blob ─────────────────────────────────────────────────────────
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


def planet_index(blob):
    """{planetObjectId: (owner, prod_bytes_or_None)}."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            oid, _x, _z, _y, owner = struct.unpack_from('<IfffI', blob,
                                                        sec.payload)
            plpr = next(sec.find('PLPR'), None)
            prod = next(plpr.find('PROD'), None) if plpr else None
            out[oid] = (owner,
                        bytes(blob[prod.start:prod.end]) if prod else None)
    return out


def research_of(blob, civ_oid):
    """The bytes of a civ's research fields, or None when OWPR is unreadable."""
    owpr = _owpr(blob, civ_oid)
    if owpr is None:
        return None
    return tuple(bytes(blob[owpr.payload + off:owpr.payload + off + n])
                 for off, n in RESEARCH_FIELDS)


def _owpr(blob, civ_oid):
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for o in glxy.children:
        if o.tag != b'OWNR':
            continue
        n = struct.unpack_from('<I', blob, o.payload)[0]
        if struct.unpack_from('<I', blob, o.payload + 4 + n)[0] != civ_oid:
            continue
        owpr = next(o.find('OWPR'), None)
        if owpr is None or owpr.size < max(off + n2 for off, n2 in RESEARCH_FIELDS):
            return None
        return owpr
    return None


def civ_by_name(blob, name):
    for o in icv.owner_records(blob):
        if o['name'] == name:
            return o
    raise SystemExit(f"no civ named {name!r}; saw "
                     f"{[o['name'] for o in icv.owner_records(blob)]}")


# ── writing the blob ─────────────────────────────────────────────────────────
def _replace_child(blob, parent_finder, new_bytes):
    """Swap one section for bytes of any length, fixing every enclosing size."""
    tree = sp.parse_blob(blob)
    target = parent_finder(blob, tree)
    if target is None:
        raise SystemExit('the section to replace is not in this blob')
    parent, sec = target
    body = (bytes(blob[parent.payload:sec.start]) + new_bytes +
            bytes(blob[sec.end:parent.end]))
    return sp.replace_payload(blob, tree, parent, body)


def replace_dyno(blob, ship_oid, new_dyno):
    def find(b, tree):
        glxy = next(tree[0].find('GLXY'))
        for sec in glxy.find('SHIP'):
            if struct.unpack_from('<I', b, sec.payload)[0] != ship_oid:
                continue
            dyno = next(sec.find('DYNO'), None)
            return (sec, dyno) if dyno else None
        return None
    return _replace_child(blob, find, new_dyno)


def replace_prod(blob, planet_oid, new_prod):
    def find(b, tree):
        glxy = next(tree[0].find('GLXY'))
        for sola in (c for c in glxy.children if c.tag == b'SOLA'):
            for sec in sola.find('PLNT'):
                if struct.unpack_from('<I', b, sec.payload)[0] != planet_oid:
                    continue
                plpr = next(sec.find('PLPR'), None)
                if plpr is None:
                    return None
                prod = next(plpr.find('PROD'), None)
                return (plpr, prod) if prod else None
        return None
    return _replace_child(blob, find, new_prod)


def set_research(blob, civ_oid, values):
    """Write a civ's research fields in place. Sizes do not change."""
    owpr = _owpr(blob, civ_oid)
    if owpr is None:
        raise SystemExit(f'civ {civ_oid} has no readable OWPR')
    out = bytearray(blob)
    for (off, n), value in zip(RESEARCH_FIELDS, values):
        out[owpr.payload + off:owpr.payload + off + n] = value
    return bytes(out)


# ── the rules ────────────────────────────────────────────────────────────────
def merge(blob, submissions, log=print):
    """submissions: [(civ_name, submitted_blob)]. Returns the merged blob."""
    served_ships = ship_index(blob)
    served_planets = planet_index(blob)
    names = {o['oid']: o['name'] for o in icv.owner_records(blob)}
    accepted = dropped = 0

    for civ_name, sub in submissions:
        civ = civ_by_name(blob, civ_name)
        mine = civ['oid']
        log(f"{civ_name} (object {mine}):")

        # ship orders
        for ship_oid, (_claimed, dyno) in sorted(ship_index(sub).items()):
            if ship_oid not in served_ships:
                log(f"    ship {ship_oid}: DROPPED, not in the state served")
                dropped += 1
                continue
            owner, as_served = served_ships[ship_oid]
            if dyno == as_served:
                continue
            if owner != mine:
                log(f"    ship {ship_oid}: DROPPED, owned by "
                    f"{names.get(owner, owner)}")
                dropped += 1
                continue
            blob = replace_dyno(blob, ship_oid, dyno)
            log(f"    ship {ship_oid}: order taken "
                f"({len(as_served)} -> {len(dyno)} bytes)")
            accepted += 1

        # production queues
        for planet_oid, (_claimed, prod) in sorted(planet_index(sub).items()):
            if planet_oid not in served_planets:
                log(f"    planet {planet_oid}: DROPPED, not in the state served")
                dropped += 1
                continue
            owner, as_served = served_planets[planet_oid]
            if prod == as_served:
                continue
            if owner != mine:
                log(f"    planet {planet_oid}: production DROPPED, owned by "
                    f"{names.get(owner, owner)}")
                dropped += 1
                continue
            if prod is None or as_served is None:
                log(f"    planet {planet_oid}: production DROPPED, no PROD "
                    f"section on one side")
                dropped += 1
                continue
            blob = replace_prod(blob, planet_oid, prod)
            log(f"    planet {planet_oid}: production queue taken")
            accepted += 1

        # research topic
        theirs = research_of(sub, mine)
        served = research_of(blob, mine)
        if theirs is not None and served is not None and theirs != served:
            blob = set_research(blob, mine, theirs)
            topic = struct.unpack_from('<I', theirs[0], 0)[0]
            log(f"    research: topic taken (id {topic})")
            accepted += 1

        # research belonging to anyone else
        for other in (o for o in icv.owner_records(blob) if o['oid'] != mine):
            a = research_of(sub, other['oid'])
            b = research_of(blob, other['oid'])
            if a is not None and b is not None and a != b:
                log(f"    research: DROPPED, belongs to {other['name']}")
                dropped += 1

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
                    help='show what the rules can see, change nothing')
    a = ap.parse_args()

    blob = sp.load_any(a.authoritative)
    names = {o['oid']: o['name'] for o in icv.owner_records(blob)}
    if a.list:
        for oid, (owner, dyno) in sorted(ship_index(blob).items()):
            print(f"  ship   {oid:<5} owner {names.get(owner, owner):<14} "
                  f"DYNO {len(dyno)} bytes "
                  f"{'(under orders)' if len(dyno) > 38 else '(idle)'}")
        for oid, (owner, prod) in sorted(planet_index(blob).items()):
            if not owner:
                continue
            print(f"  planet {oid:<5} owner {names.get(owner, owner):<14} "
                  f"PROD {len(prod) if prod else 'none'} bytes")
        for o in icv.owner_records(blob):
            r = research_of(blob, o['oid'])
            topic = struct.unpack_from('<I', r[0], 0)[0] if r else None
            print(f"  civ    {o['oid']:<5} {o['name']:<14} "
                  f"research topic {topic}")
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
        open(a.out, 'wb').write(sp.encode_save(merged))
        print(f"wrote {a.out}")


if __name__ == '__main__':
    main()
