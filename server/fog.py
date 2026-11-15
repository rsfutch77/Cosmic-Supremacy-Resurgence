"""
fog.py: each civ's copy of a turn, holding only what its client shows it

    import fog

    view = fog.project(blob, "BadGuy", log=log)        # what BadGuy is served
    sub = fog.restore(blob, "BadGuy", sub)              # before the merge

    python fog.py ../galaxies/sandbox/turns/0080.b64            # every civ
    python fog.py <blob> --civ BadGuy --dat badguy.dat          # one view

The client writes a per-civ save of its own when a save target is set
(`[0x8578FC]`, tested by `0x52C570`): what the original server sent each
player. A view serves what that save holds, in the full form the engine can
tick.

Planets. The per-civ save writes a planet the civ does not own as a compact
record (`0x4EE641`): its population, its natural bytes (`PLPR` bytes 4 to 12),
whether it has a shipyard, and its deposits. The engine cannot tick that form,
since a populated planet with no citizens has no ruler (`0x4F1440` reads
`Owner+0x500` through it). So a view keeps the `PLPR` and serves the same
content in it: the population as farmers of the owner, a shipyard when it has
one and no other facility, no soldiers and an idle queue, with the governor,
the damage pool, the byte after the soldiers and the owner's running counters
at 0. The natural bytes, food store size, food per soldier and deposits stay.

The owner, population and name are what the civ knows. In a system it has
left they come from its `EXSY` memory, as the writer's own lookup
(`0x526DC0`) does. A system is left when its `EXSY` turn is before the blob's
turn and the civ holds no planet in it. Elsewhere they are live: a row
looked at this turn matches the live planet, and the client hides systems a
civ has never entered. The live owner and population stay on a remembered
planet whose memory names the viewer or a civ the galaxy does not hold, or
reads as a misread population. The name is the remembered one only when the
owner changed.

Other civs. Their `OWNR` keeps its sections, since ships name their design
and admiral, but what the per-civ save leaves out (`0x53C27D`) is served at
the `Owner` constructor's defaults (`0x546430`):

- `OWPR`: no credits, no research progress, only the two starting
  technologies, no topic, no resources, and the unknown words at `Owner:12`,
  `Owner:16`, `Owner:20`, `Owner:24`, `Owner:356` to `358`, `Owner:721`
  and `Owner:724` at 0. The score (`Owner:28`) and reputation (`Owner:32`),
  which every civ's score row shows, and the colour, setup flags, position,
  `Owner:400`, name, checkbox and arms stay. The tick's pass over other civs
  (`0x542850`) needs a technology in each completed list.
- `KNPL`, `EXSY`, `GOVS` and `SPQS` empty, and every `CVTR` trait 0.
- Their news is withheld: each list is served empty, and the news string
  table holds only the strings the civ's own list names, with its next id
  kept (`news.own_view`).

Object ids, systems and planets are all kept, so a view passes the referee's
galaxy check and its new objects are numbered as in the authoritative blob.
`restore` puts back every planet the civ does not own, the withheld sections
and the withheld news ahead of anything the submission added
(`news.restore_view`). The merge refuses any change to them, so nothing a
civ may change is lost.
"""
import argparse
import bisect
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
import news

PLNT_OWNER = 16         # u32, the owning civ's object id
PLNT_NAME = 24          # u32 length, then the name
PLPR_HEAD = 36          # fixed fields before the population count
# Header fields only the owner sees change, served as 0: (offset, size).
OWNER_FIELDS = (
    (15, 4),            # food store
    (23, 4),            # production points toward the current item
    (27, 1),            # recruitment rate
    (32, 4),            # military store
)
CITIZEN = 9             # bytes per citizen or soldier record
CITIZEN_OWNER = 3       # u32 inside a record
FACILITY_RECORD = 8     # u32 type, u32 count
SHIPYARD = 2            # the facility type the per-civ save carries
GOVERNOR = 4            # the PLNT's last u32, Planet:496, 0 for none
UNNAMED = b'Unnamed'    # what EXSY calls a planet nobody has named
MAX_REMEMBERED = 1000   # a remembered population above this is a misread
DEPOSIT = 5             # a u8 kind and a u32 amount, as in OWPR's resources

# OWPR: credits and Owner:12, a map of 8-byte pairs counted at +8, the
# completed technologies (u32 count, 8-byte records), then two topic records.
OWPR_MAP_COUNT = 8
OWPR_RECORD = 8
NO_TOPIC = struct.pack('<iI', -1, 0)
# Every civ starts with these. The tick's rival pass (`0x542850`) takes the
# last grant `0x4A7EC0` finds in a civ's completed list, so it is never empty.
STARTING_TECHNOLOGIES = (0, 1)
# Past the second topic record: (offset, size) of each field served at 0.
OWPR_DEFAULTS = (
    (0, 3),             # Owner:356 to 358
    (7, 12),            # Owner:16, Owner:20, Owner:24
    (31, 4),            # Owner:724, a float
)
OWPR_RESOURCES = 47     # u32 count, then a u8 kind and a u32 amount each
OWPR_AFTER_RESOURCES = 4    # Owner:400, kept
OWPR_FLAG_721 = 1           # Owner:721, served at 0
OWPR_IDENTITY_TAIL = 5      # after the name: the checkbox and the arms id

CVTR_COUNT = 16
CVTR_PAIRS = 20
CVTR_PAIR = 8
EMPTY_LIST = struct.pack('<I', 0)
WITHHELD = (b'OWPR', b'KNPL', b'EXSY', b'CVTR', b'GOVS', b'SPQS')


def _civs(blob, tree):
    """{civ name: (object id, EXSY section or None)}."""
    return {name: (oid, next(o.find('EXSY'), None))
            for name, (oid, o) in _owners(blob, tree).items()}


def _owners(blob, tree):
    """{civ name: (object id, OWNR section)}."""
    oids = {o['name']: o['oid'] for o in icv.owner_records(blob)}
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for o in glxy.children:
        if o.tag != b'OWNR':
            continue
        n = struct.unpack_from('<I', blob, o.payload)[0]
        name = blob[o.payload + 4:o.payload + 4 + n].decode('latin-1')
        if name in oids:
            out[name] = (oids[name], o)
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


def _owner(blob, pl):
    return struct.unpack_from('<I', blob, pl.payload + PLNT_OWNER)[0]


def _pid(blob, pl):
    return struct.unpack_from('<I', blob, pl.payload)[0]


def remembered_rows(blob: bytes, oid: int, rows, tree=None):
    """The `EXSY` rows of civ `oid` that a view serves as remembered: looked
    at before the blob's turn, in a system the civ holds no planet in."""
    tree = tree or sp.parse_blob(blob)
    turn = icv.turn_of(blob)
    systems = _systems(blob, tree)
    out = []
    for row in rows:
        planets = systems.get(row['system'])
        if row['turn'] >= turn or planets is None:
            continue
        if any(_owner(blob, pl) == oid for pl in planets):
            continue
        out.append(row)
    return out


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
    fac = mil + 5 + FACILITY_RECORD * f + 4
    return (cit, mil, fac) if fac <= len(d) else None


def _shipyard(d: bytes) -> int:
    """1 when the PLPR's facility list holds a shipyard, as `0x4F4FD0`."""
    walk = _people(d)
    if walk is None:
        return 0
    at = walk[1] + 1
    f = struct.unpack_from('<I', d, at)[0]
    for i in range(f):
        kind, count = struct.unpack_from('<II', d, at + 4 + i * FACILITY_RECORD)
        if kind == SHIPYARD and count:
            return 1
    return 0


def _live_population(d: bytes) -> int:
    if len(d) < PLPR_HEAD + 4:
        return 0
    return struct.unpack_from('<I', d, PLPR_HEAD)[0]


def _plan(blob: bytes, civ: str, tree=None):
    """[(PLNT section, owner, population, EXSY name or None)] for each planet
    `project` serves `civ` without its owner's state, and the turn. The name is
    None when the planet's live name is served."""
    tree = tree or sp.parse_blob(blob)
    turn = icv.turn_of(blob)
    civs = _civs(blob, tree)
    if civ not in civs:
        raise KeyError(f'no civ named {civ!r} in this blob')
    me, table = civs[civ]
    oids = {oid for oid, _t in civs.values()}
    rows = exsy.parse(blob[table.payload:table.end]) if table is not None else []
    stale = {row['system']: row for row in remembered_rows(blob, me, rows, tree)}
    out = []
    for sid, planets in _systems(blob, tree).items():
        row = stale.get(sid)
        mems = (row['planets'] if row and len(row['planets']) == len(planets)
                else [None] * len(planets))
        for pl, mem in zip(planets, mems):
            owner = _owner(blob, pl)
            plpr = next(pl.find('PLPR'), None)
            if owner == me or plpr is None:
                continue
            target = (owner, _live_population(blob[plpr.payload:plpr.end]), None)
            if mem is not None and not (
                    mem['owner'] == me
                    or (mem['owner'] and mem['owner'] not in oids)
                    or mem['population'] > MAX_REMEMBERED
                    or (mem['population'] and not mem['owner'])):
                target = (mem['owner'], mem['population'], mem['name'])
            out.append((pl, *target))
    return out, turn


def stale_planets(blob: bytes, civ: str):
    """[planet id] that `project` serves from `civ`'s memory."""
    plan, _turn = _plan(blob, civ)
    return [_pid(blob, pl) for pl, _o, _p, name in plan if name is not None]


def withheld_planets(blob: bytes, civ: str):
    """[planet id] that `project` serves `civ` without their owner's state."""
    plan, _turn = _plan(blob, civ)
    return [_pid(blob, pl) for pl, *_ in plan]


def _walk(blob, plpr):
    """(payload, military end) for a PLPR, or None when its people and
    facilities do not end exactly on its first child section."""
    d = blob[plpr.payload:plpr.end]
    first = plpr.children[0].start - plpr.payload if plpr.children else len(d)
    walk = _people(d[:first])
    if walk is None or walk[2] != first:
        return None
    return d, first


def _farmer(oid: int) -> bytes:
    return b'\x00\x00\x00' + struct.pack('<I', oid) + b'\x00\x00'


def _served_plpr(blob, plpr, owner: int, pop: int, blank_prod):
    """The PLPR payload as another civ is served it, or None when it does not
    walk: the owner's running state zeroed, `pop` farmers of `owner`, no
    soldiers, a shipyard when it has one and no other facility, and an idle
    queue.

    The byte after the soldiers is PlanetProperties+0x10, 0 on every tracked
    planet. The `u32` after the facility list is PlanetProperties+0x7C, a
    pool an attack fills (`0x4F86E0`) and the planet spends on destroying
    facilities (`0x4F5710`)."""
    walk = _walk(blob, plpr)
    if walk is None:
        return None
    d, first = walk
    head = bytearray(d[:PLPR_HEAD])
    for off, size in OWNER_FIELDS:
        head[off:off + size] = bytes(size)
    yards = ([struct.pack('<II', SHIPYARD, 1)] if _shipyard(d[:first])
             else [])
    out = bytes(head) + struct.pack('<I', pop) + _farmer(owner) * pop
    out += struct.pack('<I', 0) + bytes(1)
    out += struct.pack('<I', len(yards)) + b''.join(yards) + struct.pack('<I', 0)
    rest = d[first:]
    prod = next(plpr.find('PROD'), None)
    if blank_prod is not None and prod is not None:
        a, b = prod.start - plpr.payload, prod.end - plpr.payload
        rest = d[first:a] + blank_prod + d[b:]
    return out + rest


def _served(blob, pl, owner, pop, name, blank_prod):
    """The PLNT payload as another civ is served it, or None when its PLPR
    does not walk."""
    plpr = next(pl.find('PLPR'))
    new_plpr = _served_plpr(blob, plpr, owner, pop, blank_prod)
    if new_plpr is None:
        return None
    nlen = struct.unpack_from('<I', blob, pl.payload + PLNT_NAME)[0]
    name_end = pl.payload + PLNT_NAME + 4 + nlen
    head = bytearray(blob[pl.payload:pl.payload + PLNT_NAME])
    struct.pack_into('<I', head, PLNT_OWNER, owner)
    if name is None or owner == _owner(blob, pl):
        label = blob[pl.payload + PLNT_NAME + 4:name_end]
    else:
        label = b'' if owner == 0 or name == UNNAMED else name
    out = bytes(head) + struct.pack('<I', len(label)) + label
    out += blob[name_end:plpr.start]
    out += sp.build_section(b'PLPR', plpr.version, new_plpr)
    tail = blob[plpr.end:pl.end]
    if len(tail) >= GOVERNOR:
        tail = tail[:-GOVERNOR] + bytes(GOVERNOR)
    return out + tail


def _blank_owpr(d: bytes):
    """An OWPR payload with what another civ keeps private at its defaults,
    or None when its lists do not walk to the identity at its end."""
    if len(d) < OWPR_MAP_COUNT + 4:
        return None
    m = struct.unpack_from('<I', d, OWPR_MAP_COUNT)[0]
    n_at = OWPR_MAP_COUNT + 4 + OWPR_RECORD * m
    if n_at + 4 > len(d):
        return None
    n = struct.unpack_from('<I', d, n_at)[0]
    done = [d[n_at + 4 + OWPR_RECORD * i:n_at + 4 + OWPR_RECORD * (i + 1)]
            for i in range(n)]
    start = [r for r in done
             if struct.unpack_from('<I', r)[0] in STARTING_TECHNOLOGIES]
    tail = bytearray(d[n_at + 4 + OWPR_RECORD * n + 2 * OWPR_RECORD:])
    if len(tail) < OWPR_RESOURCES + 4:
        return None
    for off, size in OWPR_DEFAULTS:
        tail[off:off + size] = bytes(size)
    r = struct.unpack_from('<I', tail, OWPR_RESOURCES)[0]
    at = OWPR_RESOURCES + 4
    if at + DEPOSIT * r > len(tail):
        return None
    for i in range(r):
        tail[at + DEPOSIT * i + 1:at + DEPOSIT * (i + 1)] = bytes(DEPOSIT - 1)
    at += DEPOSIT * r + OWPR_AFTER_RESOURCES
    if at + OWPR_FLAG_721 + 4 > len(tail):
        return None
    tail[at:at + OWPR_FLAG_721] = bytes(OWPR_FLAG_721)
    at += OWPR_FLAG_721
    name = struct.unpack_from('<I', tail, at)[0]
    if at + 4 + name + OWPR_IDENTITY_TAIL != len(tail):
        return None
    return (bytes(OWPR_MAP_COUNT) + EMPTY_LIST
            + struct.pack('<I', len(start)) + b''.join(start) + NO_TOPIC * 2
            + bytes(tail))


def _blank_cvtr(d: bytes):
    """A CVTR payload with every trait 0, or None when it does not hold its
    count of pairs."""
    if len(d) < CVTR_PAIRS:
        return None
    count = struct.unpack_from('<I', d, CVTR_COUNT)[0]
    if len(d) != CVTR_PAIRS + CVTR_PAIR * count:
        return None
    out = bytearray(d)
    for i in range(count):
        at = CVTR_PAIRS + CVTR_PAIR * i + 4
        out[at:at + 4] = bytes(4)
    return bytes(out)


def _withheld(tag, d):
    if tag == b'OWPR':
        return _blank_owpr(d)
    if tag == b'CVTR':
        return _blank_cvtr(d)
    return EMPTY_LIST


def _sections(o):
    """{tag: section} of the WITHHELD sections under an OWNR's DATA."""
    data = next(o.find('DATA'), None)
    if data is None:
        return {}
    return {s.tag: s for s in data.children if s.tag in WITHHELD}


def _apply(blob: bytes, tree, edits) -> bytes:
    """`blob` with each (start, end, bytes) edit made and every section that
    encloses one widened, in one pass. An edit is a section's payload or a
    whole section; edits do not overlap."""
    if not edits:
        return blob
    edits = sorted(edits)
    out = bytearray()
    pos = 0
    for start, end, new in edits:
        out += blob[pos:start] + new
        pos = end
    out += blob[pos:]
    grow = {}
    for start, end, new in edits:
        level = tree
        while level:
            s = next((c for c in level
                      if c.payload <= start and end <= c.end), None)
            if s is None:
                break
            entry = grow.setdefault(id(s), [s, 0])
            entry[1] += len(new) - (end - start)
            level = s.children
    ends = [end for _s, end, _n in edits]
    moved = [0]
    for start, end, new in edits:
        moved.append(moved[-1] + len(new) - (end - start))
    for s, delta in grow.values():
        icv.add_len(out, s.start + moved[bisect.bisect_right(ends, s.start)],
                    delta)
    return bytes(out)


def project(blob: bytes, civ: str, log=print) -> bytes:
    """The blob as `civ` is served it: every planet it does not own without
    its owner's state, only its own news, and other civs' private sections
    at their defaults."""
    blob = _others_planets(blob, civ, log)
    oid = next((o['oid'] for o in icv.owner_records(blob) if o['name'] == civ),
               None)
    if oid is None:
        return blob
    view = news.own_view(blob, oid)
    if view != blob:
        log(f"  fog: {civ}: other civs' news withheld")
    return _private(view, civ, log)


def _others_planets(blob: bytes, civ: str, log=print) -> bytes:
    """`blob` with every planet `civ` does not own as it is served it."""
    tree = sp.parse_blob(blob)
    plan, turn = _plan(blob, civ, tree)
    if not plan:
        return blob
    blank_prod = icv.empty_prod(blob)
    edits, remembered = [], 0
    for pl, owner, pop, name in plan:
        new = _served(blob, pl, owner, pop, name, blank_prod)
        if new is None:
            log(f"  fog: {civ}: planet {_pid(blob, pl)} does not walk; "
                f"served whole")
            continue
        remembered += name is not None
        if new != blob[pl.payload:pl.end]:
            edits.append((pl.payload, pl.end, new))
    blob = _apply(blob, tree, edits)
    log(f"  fog: {civ}: {len(plan)} planet(s) of other civs served as seen, "
        f"{remembered} as last seen before turn {turn}")
    if blank_prod is None:
        log(f"  fog: {civ}: no never-colonised planet left; production queues "
            f"served live")
    return blob


def _private(blob: bytes, civ: str, log=print) -> bytes:
    """`blob` with every other civ's private sections at their defaults."""
    tree = sp.parse_blob(blob)
    edits = []
    for name, (_oid, o) in _owners(blob, tree).items():
        if name == civ:
            continue
        for tag, s in _sections(o).items():
            new = _withheld(tag, blob[s.payload:s.end])
            if new is None:
                log(f"  fog: {civ}: {name}'s {tag.decode()} does not walk; "
                    f"served whole")
                continue
            edits.append((s.start, s.end, sp.build_section(tag, s.version, new)))
    blob = _apply(blob, tree, edits)
    if edits:
        log(f"  fog: {civ}: other civs' private sections withheld")
    return blob


def restore(blob: bytes, civ: str, sub: bytes) -> bytes:
    """`sub` with what `project` withheld from `civ` put back as `blob` holds
    it: other civs' news and private sections, and every planet `civ` does
    not own. A planet or section missing from `sub` is left to the merge to
    refuse."""
    oid = next((o['oid'] for o in icv.owner_records(blob) if o['name'] == civ),
               None)
    if oid is None:
        return sub
    sub = news.restore_view(blob, oid, sub)
    a_tree, s_tree = sp.parse_blob(blob), sp.parse_blob(sub)
    auth = _owners(blob, a_tree)
    edits = []
    for name, (_oid, o) in _owners(sub, s_tree).items():
        if name == civ or name not in auth:
            continue
        theirs = _sections(auth[name][1])
        for tag, s in _sections(o).items():
            if tag in theirs:
                edits.append((s.start, s.end,
                              blob[theirs[tag].start:theirs[tag].end]))
    planets = {_pid(blob, pl): pl
               for pls in _systems(blob, a_tree).values() for pl in pls
               if _owner(blob, pl) != oid}
    for pls in _systems(sub, s_tree).values():
        for pl in pls:
            a = planets.get(_pid(sub, pl))
            if a is not None:
                edits.append((pl.start, pl.end, blob[a.start:a.end]))
    return _apply(sub, s_tree, edits)


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
        stale = [p for p in plan if p[3] is not None]
        print(f"{name}: {len(plan)} planet(s) of other civs, {len(stale)} "
              f"remembered at turn {turn}")
        for pl, owner, pop, _n in stale:
            print(f"  planet {_pid(blob, pl)}: owner {_owner(blob, pl)} -> "
                  f"{owner}, population -> {pop}")


if __name__ == '__main__':
    main()
