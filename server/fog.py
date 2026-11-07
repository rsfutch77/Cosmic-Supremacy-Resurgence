"""
fog.py: each civ's copy of a turn, with systems it has left shown as it last saw them

    import fog

    view = fog.project(blob, "BadGuy", log=log)        # what BadGuy is served
    sub = fog.restore(blob, "BadGuy", sub)              # before the merge

    python fog.py ../galaxies/sandbox/turns/0080.b64            # every civ
    python fog.py <blob> --civ BadGuy --dat badguy.dat          # one view

The client draws a planet from the live record in every system its civ has
entered, so a civ that has left a system watches it grow. The engine already
keeps what each civ last saw, in that civ's `EXSY`: per system the turn it last
looked, and per planet the owner and population then (`server/dev_tools/exsy.py`).

A system is stale for a civ when its `EXSY` turn is before the blob's turn and
the civ owns no planet in it. A planet in a stale system is rewritten when its
live owner or population differs from memory:

- remembered empty: owner 0, name cleared, no people, no facilities, and the
  production of a never-colonised planet
- remembered owned: owner set to the remembered civ and the citizen array cut
  or padded to the remembered population; when the owner changed, the
  stationed military and the facilities go and the name is the remembered one

Never rewritten: a planet the civ owns or has citizens on, one whose memory
names the viewer or a civ the galaxy does not hold, and one whose `PLPR` does
not walk. Systems a civ has never entered are left alone; the client already
hides them.

Object ids, systems and planets are all kept, so a view passes the referee's
galaxy check and its new objects are numbered as in the authoritative blob.
`restore` puts the authoritative planets back into a submission. A civ cannot
order another civ's planet, so nothing it may change is lost.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (HERE, os.path.join(HERE, 'dev_tools')):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import save_parser as sp
import inject_civ as icv
import exsy

PLNT_OWNER = 16         # u32, the owning civ's object id
PLNT_NAME = 24          # u32 length, then the name
PLPR_HEAD = 36          # fixed fields before the population count
CITIZEN = 9             # bytes per citizen or soldier record
CITIZEN_OWNER = 3       # u32 inside a record
UNNAMED = b'Unnamed'    # what EXSY calls a planet nobody has named
MAX_REMEMBERED = 1000   # a remembered population above this is a misread


def _civs(blob, tree):
    """{civ name: (object id, EXSY section or None)}."""
    oids = {o['name']: o['oid'] for o in icv.owner_records(blob)}
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for o in glxy.children:
        if o.tag != b'OWNR':
            continue
        n = struct.unpack_from('<I', blob, o.payload)[0]
        name = blob[o.payload + 4:o.payload + 4 + n].decode('latin-1')
        if name in oids:
            out[name] = (oids[name], next(o.find('EXSY'), None))
    return out


def _systems(blob, tree):
    """{system id: [PLNT section]} in galaxy order."""
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        sun = next(sola.find('SUN '), None)
        if sun is not None:
            sid = struct.unpack_from('<I', blob, sun.payload)[0]
            out[sid] = list(sola.find('PLNT'))
    return out


def _walk(blob, plpr):
    """(payload, citizens end, military end) for a PLPR, or None when its
    people and facilities do not end exactly on its first child section."""
    d = blob[plpr.payload:plpr.end]
    first = plpr.children[0].start - plpr.payload if plpr.children else len(d)
    walk = _people(d[:first])
    if walk is None or walk[2] != first:
        return None
    return d, walk[0], walk[1]


def _people(d: bytes):
    """(citizens end, military end, facilities end) inside a PLPR's own bytes,
    or None when they run past the end."""
    if len(d) < PLPR_HEAD + 4:
        return None
    n = struct.unpack_from('<I', d, PLPR_HEAD)[0]
    cit = PLPR_HEAD + 4 + CITIZEN * n
    if cit + 4 > len(d):
        return None
    k = struct.unpack_from('<I', d, cit)[0]
    mil = cit + 4 + CITIZEN * k
    if mil + 5 > len(d):
        return None
    f = struct.unpack_from('<I', d, mil + 1)[0]
    fac = mil + 5 + 8 * f + 4
    return (cit, mil, fac) if fac <= len(d) else None


def _citizens(d: bytes, cit_end: int):
    return [d[i:i + CITIZEN] for i in range(PLPR_HEAD + 4, cit_end, CITIZEN)]


def _owned_by(rec: bytes, oid: int) -> bytes:
    return rec[:CITIZEN_OWNER] + struct.pack('<I', oid) + rec[CITIZEN_OWNER + 4:]


def _farmer(oid: int) -> bytes:
    return b'\x00\x00\x00' + struct.pack('<I', oid) + b'\x00\x00'


def _remembered_plpr(blob, plpr, owner: int, pop: int, same_owner: bool,
                     blank_prod):
    """The PLPR payload with its people (and, on a change of owner, its
    military and facilities) as remembered. None when it does not walk."""
    walk = _walk(blob, plpr)
    if walk is None:
        return None
    d, cit_end, mil_end = walk
    first = plpr.children[0].start - plpr.payload if plpr.children else len(d)
    fac_end = first
    cits = _citizens(d, cit_end)
    if not same_owner:
        cits = [_owned_by(c, owner) for c in cits]
    if len(cits) >= pop:
        cits = cits[:pop]
    else:
        cits += [cits[-1] if cits else _farmer(owner)] * (pop - len(cits))

    out = bytearray(d[:PLPR_HEAD] + struct.pack('<I', pop) + b''.join(cits))
    if same_owner:
        out += d[cit_end:fac_end]
    else:
        # No soldiers, and an empty facility list with its trailing word.
        out += struct.pack('<I', 0) + d[mil_end:mil_end + 1]
        out += struct.pack('<I', 0) + d[fac_end - 4:fac_end]
    rest = d[first:]
    if owner == 0 and blank_prod is not None:
        prod = next(plpr.find('PROD'), None)
        if prod is not None:
            a, b = prod.start - plpr.payload, prod.end - plpr.payload
            rest = d[first:a] + blank_prod + d[b:]
    return bytes(out) + rest


def _plan(blob: bytes, civ: str, tree=None):
    """[(PLNT section, remembered owner, remembered population, EXSY name)]
    for each planet `project` rewrites for `civ`, and the turn."""
    tree = tree or sp.parse_blob(blob)
    turn = icv.turn_of(blob)
    civs = _civs(blob, tree)
    if civ not in civs:
        raise KeyError(f'no civ named {civ!r} in this blob')
    me, table = civs[civ]
    if table is None:
        return [], turn
    oids = {oid for oid, _t in civs.values()}
    systems = _systems(blob, tree)
    out = []
    for row in exsy.parse(blob[table.payload:table.end]):
        planets = systems.get(row['system'])
        if row['turn'] >= turn or planets is None:
            continue
        if len(planets) != len(row['planets']):
            continue
        if any(struct.unpack_from('<I', blob, pl.payload + PLNT_OWNER)[0] == me
               for pl in planets):
            continue
        for pl, mem in zip(planets, row['planets']):
            owner = struct.unpack_from('<I', blob, pl.payload + PLNT_OWNER)[0]
            plpr = next(pl.find('PLPR'), None)
            walk = _walk(blob, plpr) if plpr is not None else None
            if walk is None:
                continue
            d = walk[0]
            pop = struct.unpack_from('<I', d, PLPR_HEAD)[0]
            if (owner, pop) == (mem['owner'], mem['population']):
                continue
            if mem['owner'] == me or (mem['owner'] and mem['owner'] not in oids):
                continue
            if mem['population'] > MAX_REMEMBERED or (mem['population'] and not mem['owner']):
                continue
            if any(struct.unpack_from('<I', c, CITIZEN_OWNER)[0] == me
                   for c in _citizens(d, walk[1])):
                continue
            out.append((pl, mem['owner'], mem['population'], mem['name']))
    return out, turn


def stale_planets(blob: bytes, civ: str):
    """[planet id] that `project` rewrites for `civ`."""
    plan, _turn = _plan(blob, civ)
    return [struct.unpack_from('<I', blob, pl.payload)[0] for pl, *_ in plan]


def _rewritten(blob, pl, owner, pop, name, blank_prod):
    """The new PLNT payload, or None when its PLPR does not walk."""
    live_owner = struct.unpack_from('<I', blob, pl.payload + PLNT_OWNER)[0]
    same = owner == live_owner
    plpr = next(pl.find('PLPR'))
    new_plpr = _remembered_plpr(blob, plpr, owner, pop, same, blank_prod)
    if new_plpr is None:
        return None
    nlen = struct.unpack_from('<I', blob, pl.payload + PLNT_NAME)[0]
    name_end = pl.payload + PLNT_NAME + 4 + nlen
    head = bytearray(blob[pl.payload:pl.payload + PLNT_NAME])
    struct.pack_into('<I', head, PLNT_OWNER, owner)
    if same:
        label = blob[pl.payload + PLNT_NAME + 4:name_end]
    else:
        label = b'' if owner == 0 or name == UNNAMED else name
    out = bytes(head) + struct.pack('<I', len(label)) + label
    out += blob[name_end:plpr.start]
    out += sp.build_section(b'PLPR', plpr.version, new_plpr)
    out += blob[plpr.end:pl.end]
    return out


def project(blob: bytes, civ: str, log=print) -> bytes:
    """The blob as `civ` is served it: every stale planet as remembered."""
    tree = sp.parse_blob(blob)
    plan, turn = _plan(blob, civ, tree)
    if not plan:
        return blob
    blank_prod = None
    if any(owner == 0 for _pl, owner, _p, _n in plan):
        blank_prod = icv.empty_prod(blob)
    edits = []
    for pl, owner, pop, name in plan:
        new = _rewritten(blob, pl, owner, pop, name, blank_prod)
        pid = struct.unpack_from('<I', blob, pl.payload)[0]
        if new is None:
            log(f"  fog: {civ}: planet {pid} does not walk; served live")
            continue
        edits.append((pl.payload, pl.end, new))
    # From the end backwards, so no edit moves another's offsets.
    for start, end, new in sorted(edits, reverse=True):
        blob = icv.splice(blob, start, end, new)
    log(f"  fog: {civ}: {len(edits)} planet(s) shown as last seen at turn "
        f"{turn}")
    return blob


def restore(blob: bytes, civ: str, sub: bytes) -> bytes:
    """`sub` with each planet `project` rewrote for `civ` put back as `blob`
    holds it. A planet missing from `sub` is left to the merge to refuse."""
    ids = stale_planets(blob, civ)
    if not ids:
        return sub
    auth = {p['id']: p for p in icv.planet_records(blob)}
    edits = []
    for p in icv.planet_records(sub):
        if p['id'] in ids:
            a = auth[p['id']]
            edits.append((p['payload'], p['end'], blob[a['payload']:a['end']]))
    for start, end, new in sorted(edits, reverse=True):
        sub = icv.splice(sub, start, end, new)
    return sub


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('blob', help='a .b64 or .dat galaxy')
    ap.add_argument('--civ', help='write this civ\'s view')
    ap.add_argument('--dat', help='where to write the view, as a .dat')
    a = ap.parse_args()
    blob = sp.load_any(a.blob)
    if a.civ:
        view = project(blob, a.civ)
        if a.dat:
            with open(a.dat, 'wb') as f:
                f.write(view)
            print(f"wrote {a.dat}, {len(view):,} bytes")
        return
    tree = sp.parse_blob(blob)
    for name in _civs(blob, tree):
        plan, turn = _plan(blob, name, tree)
        print(f"{name}: {len(plan)} planet(s) stale at turn {turn}")
        for pl, owner, pop, _n in plan:
            pid = struct.unpack_from('<I', blob, pl.payload)[0]
            live = struct.unpack_from('<I', blob, pl.payload + PLNT_OWNER)[0]
            print(f"  planet {pid}: owner {live} -> {owner}, population -> {pop}")


if __name__ == '__main__':
    main()
