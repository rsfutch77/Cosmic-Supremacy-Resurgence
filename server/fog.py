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
at 0. After the queue: no population history, `Planet:368` at 8 as on a
never-colonised planet, the served owner as the only ruler at full influence
since turn 0, no rebellion count and no energy links. The natural bytes, food
store size, food per soldier and deposits stay.

The owner, population and name are what the civ knows. In a system it has
left they come from its `EXSY` memory, as the writer's own lookup
(`0x526DC0`) does. A system is left when its `EXSY` turn is before the blob's
turn and the civ holds no planet in it. In a system it looked at this turn
they are live, which matches its row. The live owner and population stay on a
remembered planet whose memory names the viewer or a civ the galaxy does not
hold, or reads as a misread population. The name is the remembered one only
when the owner changed.

A planet in a system the civ has never entered, one with no `EXSY` row, is
served unowned, empty and unnamed, with no shipyard. The recon screen hides
those systems, but the diplomacy screen's Planets column counts every planet
a civ owns in the save, and the manual has it count only known planets.

Other civs. The per-civ save leaves out their `DATA` (`0x53C27D`). A view
keeps it, since their ships name their design and admiral, and serves what
it holds at the `Owner` constructor's defaults (`0x546430`):

- `OWPR`: no credits, no research progress, only the two starting
  technologies, no topic, no resources, and the unknown words at `Owner:12`,
  `Owner:16`, `Owner:20`, `Owner:24`, `Owner:356` to `358`, `Owner:721`
  and `Owner:724` at 0. The score (`Owner:28`) and reputation (`Owner:32`),
  which every civ's score row shows, and the colour, setup flags, position,
  `Owner:400`, name, checkbox and arms stay. The tick's pass over other civs
  (`0x542850`) needs a technology in each completed list.
- `KNPL` holding only its record of the viewer, as first contact makes one
  with the stance, the turn met and the war fields kept: the client's
  acceptance of a stance treaty and its declaration of war write both civs'
  records (`0x534340`) and stop when one is missing. The original client
  never ran either offline.
- `EXSY`, `GOVS` and `SPQS` empty, and every `CVTR` trait 0.
- Only the admirals their ships and fleets follow, only the designs their
  ships are built to or those admirals' rules name, and no scan reports.
- The end-galaxy vote in `USSE` at 0. The rest of `USSE` is the News
  screen's settings and stays.
- `SERV` with `Owner:652` and the active flag at 0. The baseline stays while
  the civ shows inactive, since every civ's `CLIE` published that percentage
  (`0x5381D0`); otherwise it is the current turn, which shows 0%.
- Their news is withheld: each list is served empty, and the news string
  table holds only the strings the civ's own list names, with its next id
  kept (`news.own_view`).

Treaties. The per-civ save writes only the treaties the civ is party to and
has not deleted from its side (`0x55B250`). A partner's shared line of sight
needs nothing more: the client finds it in those treaties (`0x559EE0`), and
the tick refreshes the civ's `EXSY` with it.

A caller with no seat (`civ` None) is served every planet and every civ as a
rival is, no news and no treaties.

Object ids, systems and planets are all kept, so a view passes the referee's
galaxy check and its new objects are numbered as in the authoritative blob.
`restore` puts back every planet the civ does not own, the withheld sections,
admirals, designs, reports, vote, activity and treaties, and the withheld news
ahead of anything the submission added (`news.restore_view`). The merge refuses any
change to them, so nothing a civ may change is lost.
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
import knpl
import activity
import exsy
import merge_orders as mo
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
# The name `_plan` gives a planet in a system the civ has never entered: it is
# served unowned, empty and unnamed, so it counts toward no civ's planets.
UNSEEN = object()
MAX_REMEMBERED = 1000   # a remembered population above this is a misread
DEPOSIT = 5             # a u8 kind and a u32 amount, as in OWPR's resources
# Between PROD and ENLI: the population history, Planet:368, the conqueror
# list, the rebellion counter, then the deposits.
HISTORY = 8             # u16 slots, served at 0
SETTLED = 8             # Planet:368, as a never-colonised planet holds it
RULER = struct.Struct('<IfIi')  # owner, influence, first turn, last turn
FULL_INFLUENCE = 100.0
RULING = -1             # a ruler's last turn while it holds the planet
REBELLION = 2           # u16, served at 0

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
KNPL_KEPT = ('stance', 'declared', 'w17', 'battled', 'inactivity')
SECTION_HEAD = 8        # tag, then size and version
SERV_BASELINE = 4       # SERV: Owner:652, then the turn last seen active
SERV_FLAG = 8           # SERV: 1 while the civ is active this turn
TRDT_PARTIES = 8 + mo.TRTY_PARTIES_AT  # in a whole TRTY: proposer, recipient
TRDT_DELETED = 28       # past the parties: a byte per party, 1 once deleted
USSE_SIZE = 45
USSE_VOTE_VERSION = 2
END_VOTE_AT = 44        # USSE: the end-galaxy vote, Owner+0x2F8


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
    if civ is not None and civ not in civs:
        raise KeyError(f'no civ named {civ!r} in this blob')
    me, table = civs[civ] if civ is not None else (None, None)
    oids = {oid for oid, _t in civs.values()}
    rows = exsy.parse(blob[table.payload:table.end]) if table is not None else []
    entered = {row['system'] for row in rows}
    stale = {row['system']: row for row in remembered_rows(blob, me, rows, tree)}
    out = []
    for sid, planets in _systems(blob, tree).items():
        # No civ has no memory to hide behind: it is served every planet live.
        if civ is not None and sid not in entered:
            out.extend((pl, 0, 0, UNSEEN) for pl in planets
                       if _owner(blob, pl) != me
                       and next(pl.find('PLPR'), None) is not None)
            continue
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
    return [_pid(blob, pl) for pl, _o, _p, name in plan
            if name is not None and name is not UNSEEN]


def unseen_planets(blob: bytes, civ: str):
    """[planet id] that `project` serves unowned, in systems `civ` has never
    entered."""
    plan, _turn = _plan(blob, civ)
    return [_pid(blob, pl) for pl, _o, _p, name in plan if name is UNSEEN]


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


def _served_plpr(blob, plpr, owner: int, pop: int, blank_prod, yard=True):
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
    yards = ([struct.pack('<II', SHIPYARD, 1)] if yard and _shipyard(d[:first])
             else [])
    out = bytes(head) + struct.pack('<I', pop) + _farmer(owner) * pop
    out += struct.pack('<I', 0) + bytes(1)
    out += struct.pack('<I', len(yards)) + b''.join(yards) + struct.pack('<I', 0)
    if [c.tag for c in plpr.children] != [b'PROD', b'ENLI']:
        return None
    prod, enli = plpr.children
    p0, p1 = prod.start - plpr.payload, prod.end - plpr.payload
    e0, e1 = enli.start - plpr.payload, enli.end - plpr.payload
    settled = _settled(d[p1:e0], owner)
    if settled is None or e1 != len(d):
        return None
    queue = blank_prod if blank_prod is not None else d[p0:p1]
    return (out + d[first:p0] + queue + settled
            + sp.build_section(b'ENLI', enli.version, EMPTY_LIST))


def _settled(d: bytes, owner: int):
    """The block between PROD and ENLI as another civ is served it, or None
    when it does not walk to its deposits: no population history, settled,
    `owner` the only ruler, no rebellion, and the deposits kept."""
    n_at = 2 * HISTORY + 4
    if n_at + 4 > len(d):
        return None
    n = struct.unpack_from('<I', d, n_at)[0]
    at = n_at + 4 + RULER.size * n + REBELLION
    if at + 4 > len(d):
        return None
    k = struct.unpack_from('<I', d, at)[0]
    if at + 4 + DEPOSIT * k != len(d):
        return None
    rulers = [RULER.pack(owner, FULL_INFLUENCE, 0, RULING)] if owner else []
    return (bytes(2 * HISTORY) + struct.pack('<II', SETTLED, len(rulers))
            + b''.join(rulers) + bytes(REBELLION) + d[at:])


def _served(blob, pl, owner, pop, name, blank_prod):
    """The PLNT payload as another civ is served it, or None when its PLPR
    does not walk."""
    plpr = next(pl.find('PLPR'))
    new_plpr = _served_plpr(blob, plpr, owner, pop, blank_prod,
                            yard=name is not UNSEEN)
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


def _withheld(tag, d, viewer=None):
    if tag == b'OWPR':
        return _blank_owpr(d)
    if tag == b'CVTR':
        return _blank_cvtr(d)
    if tag == b'KNPL':
        return _viewer_record(d, viewer)
    return EMPTY_LIST


def _viewer_record(d: bytes, viewer):
    """A rival's KNPL holding only its record of the viewer, as first contact
    makes one, with the stance, the turn met and the war fields kept. The
    client's acceptance and declaration write both civs' records
    (`0x534340`), and stop when one is missing. None when it does not walk."""
    try:
        records = knpl.parse(d)
    except (ValueError, struct.error):
        return None
    out = [dict(mo.FIRST_CONTACT, civ=r['civ'], handle=r['handle'],
                met=r['met'], **{k: r[k] for k in KNPL_KEPT})
           for r in records if r['civ'] == viewer]
    return knpl.build(out)


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
    at their defaults. `civ` None is a caller with no seat, served every
    planet and every civ that way, and no news."""
    blob = _others_planets(blob, civ, log)
    oid = next((o['oid'] for o in icv.owner_records(blob) if o['name'] == civ),
               None)
    if oid is None and civ is not None:
        return blob
    view = news.own_view(blob, oid)
    if view != blob:
        log(f"  fog: {civ or 'no seat'}: other civs' news withheld")
    return _own_treaties(_private(view, civ, log), civ, oid, log)


def _party(record, oid):
    """Whether the per-civ save writes this TRTY for `oid` (`0x55B250`): a
    party to it that has not deleted it from its side."""
    proposer, recipient = struct.unpack_from('<II', record, TRDT_PARTIES)
    deleted = record[TRDT_PARTIES + TRDT_DELETED:][:2]
    return ((proposer == oid and not deleted[0])
            or (recipient == oid and not deleted[1]))


def _own_treaties(blob: bytes, civ: str, oid, log=print) -> bytes:
    """`blob` with only the treaties `civ` is served."""
    records = mo.treaties(blob)
    if records is None:
        log(f"  fog: {civ or 'no seat'}: the treaty list does not walk; "
            f"served whole")
        return blob
    kept = [r for r in records.values() if _party(r, oid)]
    if len(kept) == len(records):
        return blob
    log(f"  fog: {civ or 'no seat'}: {len(records) - len(kept)} of "
        f"{len(records)} treaties withheld")
    return mo.set_treaties(blob, kept)


def _restore_treaties(blob: bytes, oid, sub: bytes) -> bytes:
    """`sub` with the treaties `oid` was not served put back where `blob`
    holds them. A submitted treaty under a withheld id is not kept."""
    a, s = mo.treaties(blob), mo.treaties(sub)
    if a is None or s is None:
        return sub
    hidden = {k: r for k, r in a.items() if not _party(r, oid)}
    if not hidden:
        return sub
    out = [hidden[k] if k in hidden else s[k] for k in a
           if k in hidden or k in s]
    out += [r for k, r in s.items() if k not in a]
    return mo.set_treaties(sub, out)


def _others_planets(blob: bytes, civ: str, log=print) -> bytes:
    """`blob` with every planet `civ` does not own as it is served it."""
    tree = sp.parse_blob(blob)
    plan, turn = _plan(blob, civ, tree)
    if not plan:
        return blob
    blank_prod = icv.empty_prod(blob)
    edits, remembered, unseen = [], 0, 0
    for pl, owner, pop, name in plan:
        new = _served(blob, pl, owner, pop, name, blank_prod)
        if new is None:
            log(f"  fog: {civ}: planet {_pid(blob, pl)} does not walk; "
                f"served whole")
            continue
        unseen += name is UNSEEN
        remembered += name is not None and name is not UNSEEN
        if new != blob[pl.payload:pl.end]:
            edits.append((pl.payload, pl.end, new))
    blob = _apply(blob, tree, edits)
    log(f"  fog: {civ}: {len(plan)} planet(s) of other civs served as seen, "
        f"{remembered} as last seen before turn {turn}, {unseen} unowned in "
        f"systems never entered")
    if blank_prod is None:
        log(f"  fog: {civ}: no never-colonised planet left; production queues "
            f"served live")
    return blob


def _design_block(blob, data):
    """(start, [(design id, start, end)], end) of the designs and scan reports
    in a civ's DATA: a u32 count of DSGN sections after SERV, then a u32 count
    of report sections. None when it does not walk."""
    serv = next((c for c in data.children if c.tag == b'SERV'), None)
    if serv is None:
        return None

    def section_end(p):
        return p + SECTION_HEAD + (struct.unpack_from('<I', blob, p + 4)[0]
                                   & sp.SIZE_MASK)

    start = serv.end
    if start + 4 > data.end:
        return None
    p, designs = start + 4, []
    for _ in range(struct.unpack_from('<I', blob, start)[0]):
        if p + SECTION_HEAD + 4 > data.end or bytes(blob[p:p + 4]) != b'DSGN':
            return None
        end = section_end(p)
        designs.append((struct.unpack_from('<I', blob, p + SECTION_HEAD)[0],
                        p, end))
        p = end
    if p + 4 > data.end:
        return None
    reports = struct.unpack_from('<I', blob, p)[0]
    p += 4
    for _ in range(reports):
        if (p + SECTION_HEAD > data.end
                or bytes(blob[p:p + 4]) not in mo.REPORT_TAGS):
            return None
        p = section_end(p)
    return (start, designs, p) if p <= data.end else None


def _followed(blob, oid, objects):
    """[whole ADML section] of the admirals a civ's ships and fleets follow,
    in order, or None when its ADMS does not walk."""
    admirals = mo.admirals_of(blob, oid)
    if admirals is None:
        return None
    named = {mo.dyno_admiral(o.get('dyno')) for o in objects
             if o.get('owner') == oid}
    return [r for a, r in admirals if a in named]


def _served_designs(blob, oid, data, crews, admirals):
    """(start, end, bytes) serving a civ's DATA with only the designs its
    ships are built to or `admirals`' rules name, and no scan reports, or
    None when the block does not walk."""
    block = _design_block(blob, data)
    if block is None:
        return None
    start, designs, end = block
    ships = {struct.unpack_from('<I', shpr)[0]
             for owner, shpr in crews.values()
             if owner == oid and shpr and len(shpr) >= 4}
    rules = b''.join(r[SECTION_HEAD + mo.ADML_FIXED:] for r in admirals)
    kept = [blob[a:b] for d, a, b in designs
            if d in ships or struct.pack('<I', d) in rules]
    return (start, end, struct.pack('<I', len(kept)) + b''.join(kept)
            + EMPTY_LIST)


def _served_serv(d: bytes, turn: int):
    """A SERV payload as another civ is served it, or None when it holds no
    baseline: `Owner:652` and the active flag at 0, and the baseline kept only
    while the civ shows inactive, which every civ's `CLIE` published. Otherwise
    the current turn, which shows 0% as the original did."""
    if len(d) <= SERV_FLAG:
        return None
    baseline = struct.unpack_from('<I', d, SERV_BASELINE)[0]
    if activity.penalty(turn, baseline) == 0:
        baseline = turn
    out = bytearray(d)
    out[:SERV_BASELINE] = bytes(SERV_BASELINE)
    struct.pack_into('<I', out, SERV_BASELINE, baseline)
    out[SERV_FLAG] = 0
    return bytes(out)


def _usse(data):
    """A civ's USSE section, when it is one that holds the vote."""
    usse = next((c for c in data.children if c.tag == b'USSE'), None)
    if (usse is None or usse.end - usse.payload != USSE_SIZE
            or usse.version < USSE_VOTE_VERSION):
        return None
    return usse


def _private(blob: bytes, civ: str, log=print) -> bytes:
    """`blob` with every other civ's private sections at their defaults, only
    the admirals its ships follow and the designs they and its ships name, no
    scan reports, no vote and no activity."""
    tree = sp.parse_blob(blob)
    crews = mo._ship_crew_index(blob)
    objects = mo.space_objects(blob)[2]
    turn = icv.turn_of(blob)
    owners = _owners(blob, tree)
    viewer = owners[civ][0] if civ in owners else None
    edits = []
    for name, (oid, o) in owners.items():
        if name == civ:
            continue
        for tag, s in _sections(o).items():
            new = _withheld(tag, blob[s.payload:s.end], viewer)
            if new is None:
                log(f"  fog: {civ}: {name}'s {tag.decode()} does not walk; "
                    f"served whole")
                continue
            edits.append((s.start, s.end, sp.build_section(tag, s.version, new)))
        data = next(o.find('DATA'), None)
        if data is None:
            continue
        admirals = _followed(blob, oid, objects)
        adms = next((c for c in data.children if c.tag == b'ADMS'), None)
        if admirals is None or adms is None:
            log(f"  fog: {civ}: {name}'s admirals do not walk; served whole")
            admirals = [r for _a, r in mo.admirals_of(blob, oid) or ()]
        else:
            new = sp.build_section(b'ADMS', adms.version,
                                   struct.pack('<I', len(admirals))
                                   + b''.join(admirals))
            if new != blob[adms.start:adms.end]:
                edits.append((adms.start, adms.end, new))
        designs = _served_designs(blob, oid, data, crews, admirals)
        if designs is None:
            log(f"  fog: {civ}: {name}'s designs do not walk; served whole")
        elif designs[2] != blob[designs[0]:designs[1]]:
            edits.append(designs)
        serv = next((c for c in data.children if c.tag == b'SERV'), None)
        if serv is not None:
            new = _served_serv(blob[serv.payload:serv.end], turn)
            if new is None:
                log(f"  fog: {civ}: {name}'s SERV holds no baseline; served "
                    f"whole")
            elif new != blob[serv.payload:serv.end]:
                edits.append((serv.payload, serv.end, new))
        usse = _usse(data)
        if usse is not None and blob[usse.payload + END_VOTE_AT]:
            vote = usse.payload + END_VOTE_AT
            edits.append((vote, vote + 1, b'\x00'))
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
        a_data = next(auth[name][1].find('DATA'), None)
        s_data = next(o.find('DATA'), None)
        if a_data is None or s_data is None:
            continue
        a_block, s_block = _design_block(blob, a_data), _design_block(sub, s_data)
        if a_block is not None and s_block is not None:
            edits.append((s_block[0], s_block[2],
                          blob[a_block[0]:a_block[2]]))
        a_usse, s_usse = _usse(a_data), _usse(s_data)
        if a_usse is not None and s_usse is not None:
            edits.append((s_usse.start, s_usse.end,
                          blob[a_usse.start:a_usse.end]))
        for tag in (b'SERV', b'ADMS'):
            a = next((c for c in a_data.children if c.tag == tag), None)
            s = next((c for c in s_data.children if c.tag == tag), None)
            if a is not None and s is not None:
                edits.append((s.start, s.end, blob[a.start:a.end]))
    planets = {_pid(blob, pl): pl
               for pls in _systems(blob, a_tree).values() for pl in pls
               if _owner(blob, pl) != oid}
    for pls in _systems(sub, s_tree).values():
        for pl in pls:
            a = planets.get(_pid(sub, pl))
            if a is not None:
                edits.append((pl.start, pl.end, blob[a.start:a.end]))
    return _restore_treaties(blob, oid, _apply(sub, s_tree, edits))


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
