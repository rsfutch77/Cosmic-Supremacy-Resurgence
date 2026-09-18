"""
order_diff.py , what did this player change, and was any of it theirs?
=====================================================================
    python order_diff.py served.b64 returned.b64
    python order_diff.py served.b64 returned.b64 --civ DemoPlayer
    python order_diff.py served.b64 returned.b64 --bytes

Measuring a new order type costs one click in a client and then this. Give it
the exact bytes the player was served and the save they handed back, and it says
which objects changed, who owns each one, and which section inside it moved.
That is the input to both the whitelist (C2) and the legality gate (C4).

**Objects are matched by object id, not by position.** `diff_saves.py` pairs
sections by their place in the tree, which is right for asking whether a save
round trip is lossless and wrong here: the client writes the civs back in its
own order, so a positional diff of two captures of one galaxy reports every civ
as changed and buries the one field that actually moved. Measured on a real
pair, positional matching reported 7 changed sections where 1 had changed.

Ownership comes from the SERVED blob. A submission that rewrites an owner field
must not thereby acquire the object, so the question asked of every change is
"who owned this at the start of the turn", never "who does the submission say
owns it".

Layouts used, from the writers:

    OWNR payload    u32 nameLen ; char name[] ; u32 objectId
    PLNT payload    u32 objectId ; f32 x, z, y ; u32 ownerObjectId ; ...
    SHIP payload    u32 objectId ; f32 x, z, y ; u32 ownerObjectId ; ...

Anything else is attributed to whatever object encloses it, so a `DYNO` under a
`SHIP` is that ship's, and a section under `GLXY` is nobody's and shows as
galaxy-level. Galaxy-level changes are worth seeing: a player's client has no
business writing them, and one that does is either a bug in the pusher or a
player editing their save.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp
import inject_civ as icv

OWNED = {b'PLNT', b'SHIP'}          # payload carries an object id then an owner
OBJECT_TAGS = OWNED | {b'OWNR'}


def owner_of_ownr(blob, sec):
    n = struct.unpack_from('<I', blob, sec.payload)[0]
    return struct.unpack_from('<I', blob, sec.payload + 4 + n)[0]


def objects(blob):
    """{(tag, objectId): section} for every object-bearing section."""
    out = {}
    tree = sp.parse_blob(blob)

    def visit(sections):
        for s in sections:
            if s.tag == b'OWNR':
                oid = owner_of_ownr(blob, s)
                out[(s.tag, oid)] = s
            elif s.tag in OWNED:
                oid = struct.unpack_from('<I', blob, s.payload)[0]
                out[(s.tag, oid)] = s
            visit(s.children)

    visit(tree)
    return out


def owner_id(blob, tag, sec):
    """Who owned this object in this blob. 0 for unowned, None for n/a."""
    if tag == b'OWNR':
        return owner_of_ownr(blob, sec)
    if tag in OWNED:
        return struct.unpack_from('<I', blob, sec.payload + 16)[0]
    return None


def own_bytes(blob, sec):
    """A section's own payload, with its child sections cut out.

    Delegated to the parser, which knows that children can have gaps between
    them. Treating a section's own bytes as a prologue plus an epilogue loses
    whatever sits in a gap, and for a `DYNO` that is the has-orders byte and the
    admiral id, which is to say most of what an order consists of.
    """
    return sp.own_bytes(blob, sec)


def child_paths(blob, sec, path=()):
    """(path, section) for every descendant, keyed within this object."""
    counts = {}
    for c in sec.children:
        tag = c.tag.decode('latin-1')
        i = counts.get(tag, 0)
        counts[tag] = i + 1
        here = path + (f'{tag}[{i}]',)
        yield here, c
        yield from child_paths(blob, c, here)


def byte_changes(a_bytes, b_bytes, limit=8):
    """Differing offsets, as (offset, a_dword, b_dword) where they line up."""
    out = []
    for i in range(0, min(len(a_bytes), len(b_bytes))):
        if a_bytes[i] == b_bytes[i]:
            continue
        word = i - (i % 4)
        if out and out[-1][0] == word:
            continue
        av = bv = None
        if word + 4 <= len(a_bytes) and word + 4 <= len(b_bytes):
            av = struct.unpack_from('<I', a_bytes, word)[0]
            bv = struct.unpack_from('<I', b_bytes, word)[0]
        out.append((word, av, bv))
        if len(out) >= limit:
            break
    return out


def compare(served: bytes, returned: bytes, civ_name=None, show_bytes=False,
            log=print):
    """Report every object that changed. Returns (mine, theirs, galaxy) counts."""
    names = {o['oid']: o['name'] for o in icv.owner_records(served)}
    mine_id = None
    if civ_name:
        match = [i for i, n in names.items() if n == civ_name]
        if not match:
            raise SystemExit(f'no civ named {civ_name!r}; saw {sorted(names.values())}')
        mine_id = match[0]

    a_objs, b_objs = objects(served), objects(returned)
    mine = theirs = galaxy = 0

    only_a = sorted(set(a_objs) - set(b_objs))
    only_b = sorted(set(b_objs) - set(a_objs))
    for tag, oid in only_a:
        log(f'  REMOVED {tag.decode()} {oid}')
    for tag, oid in only_b:
        log(f'  ADDED   {tag.decode()} {oid}')

    for key in sorted(set(a_objs) & set(b_objs)):
        tag, oid = key
        a, b = a_objs[key], b_objs[key]
        if bytes(served[a.start:a.end]) == bytes(returned[b.start:b.end]):
            continue

        owner = owner_id(served, tag, a)
        if tag == b'OWNR':
            whose = names.get(oid, oid)
        elif owner:
            whose = names.get(owner, owner)
        else:
            whose = 'unowned'
        if mine_id is not None:
            if tag == b'OWNR':
                verdict = 'YOURS' if oid == mine_id else "ANOTHER CIV'S"
            elif owner == mine_id:
                verdict = 'YOURS'
            elif owner:
                verdict = "ANOTHER CIV'S"
            else:
                verdict = 'UNOWNED'
            if verdict == 'YOURS':
                mine += 1
            elif verdict == 'UNOWNED':
                galaxy += 1
            else:
                theirs += 1
        else:
            verdict = ''
            mine += 1

        label = f'{tag.decode()} {oid}'
        log(f'\n  {label:<12} owner {whose!s:<14} {a.size} -> {b.size} bytes'
            f'{"   " + verdict if verdict else ""}')

        a_own, b_own = own_bytes(served, a), own_bytes(returned, b)
        if a_own != b_own:
            log(f'      own payload {len(a_own)} -> {len(b_own)} bytes')
            for off, av, bv in byte_changes(a_own, b_own):
                extra = f'  {av} -> {bv}' if av is not None else ''
                log(f'        +{off}{extra}')

        a_kids = dict(child_paths(served, a))
        b_kids = dict(child_paths(returned, b))
        for path in sorted(set(a_kids) | set(b_kids)):
            ca, cb = a_kids.get(path), b_kids.get(path)
            name = '>'.join(path)
            if ca is None:
                log(f'      + {name}  appeared, {cb.size} bytes')
                continue
            if cb is None:
                log(f'      - {name}  gone, was {ca.size} bytes')
                continue
            sa = bytes(served[ca.start:ca.end])
            sb = bytes(returned[cb.start:cb.end])
            if sa == sb:
                continue
            log(f'      ~ {name}  {ca.size} -> {cb.size} bytes')
            oa, ob = own_bytes(served, ca), own_bytes(returned, cb)
            if oa != ob:
                for off, av, bv in byte_changes(oa, ob):
                    extra = f'  {av} -> {bv}' if av is not None else ''
                    log(f'          +{off}{extra}')
            if show_bytes:
                log(f'          served   {oa[:64].hex(" ")}')
                log(f'          returned {ob[:64].hex(" ")}')

    return mine, theirs, galaxy


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('served', help='the exact bytes handed to the player')
    ap.add_argument('returned', help='the save they handed back')
    ap.add_argument('--civ', help='whose submission this is, to judge ownership')
    ap.add_argument('--bytes', action='store_true', dest='show_bytes',
                    help='dump the raw payloads of changed sections')
    a = ap.parse_args()

    served = sp.load_any(a.served)
    returned = sp.load_any(a.returned)
    print(f'served   {len(served):,} bytes')
    print(f'returned {len(returned):,} bytes')
    if a.civ:
        print(f'judging as {a.civ}\n')
    mine, theirs, galaxy = compare(served, returned, a.civ, a.show_bytes)
    print()
    if a.civ:
        print(f'{mine} object(s) of theirs, {theirs} belonging to another civ, '
              f'{galaxy} unowned')
    else:
        print(f'{mine} object(s) changed')


if __name__ == '__main__':
    main()
