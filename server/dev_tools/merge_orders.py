"""
merge_orders.py: build the next authoritative blob from players' returned saves
================================================================================
    python merge_orders.py authoritative.b64 \
        --from DemoPlayer=playerA.b64 --from BadGuy=playerB.b64 --dat merged.dat
    python merge_orders.py authoritative.b64 --from BadGuy=playerB.b64 --list

The rule is merge orders, never states: only the parts of a returned state that
express the submitter's intent are taken, and only for objects that civ owns.
Every submission is judged against the state as it was served, never against
the blob as it accumulates other players' orders. Ownership is decided by the
served blob, so rewriting an owner field acquires nothing; the change is
dropped and named.

What is accepted
----------------
**Ship orders**, `SHIP > DYNO`. A move or a colonise is three edits inside the
ship's `DYNO`: the `SHCO` order-type byte (1 move, 3 colonise), the has-orders
byte, and an appended `ROUT` plus `u32`. Taking the order is copying the
`DYNO`.

**Research topic**, `OWNR > DATA > OWPR`, the technology id written twice, 8
bytes apart, after the completed technologies (`research_offsets`): at `+32`
and `+40` only while a civ has its two starting technologies. Copied field by field: the rest of `OWPR` is the civ's
property block (coat-of-arms count, flags).

**Current production**, `PLNT > PLPR > PROD`: what the planet is building, its
own fields and a nested section naming it (`WLTH` for nothing, `FCLT` with the
facility type at `+13`, `SCAN`, or `SHIP` with a design id). Copied whole,
unless the served item was hurried, which the UI does not let be swapped, it
names any other kind, or it starts a facility the client would not allow
(`facility_rules.can_build`: researched, fits the free space, the hyperspace
pair, the per-planet limit), judged on the served planet and research. The
planet's link to a production queue, `+2..+8`, is part of the copy and is
judged with the queues below.

**Production queues**, `OWNR > DATA > SPQS`: a `u32` count, then one `SPRQ` per
queue holding its object id, a `u32` count of the planets using it, a byte, and
a `PRQU` with the name and the entries in the order the UI shows them. An entry
is an entry id, a count, an infinite flag, a count mode and an item: a facility
type (item type 0), a design (item type 8) or a scan (item type 9; route is 2,
fleet 3). The client's queue editor offers nothing else. A planet joins a
queue through its `PROD`: a flag at `+2`, the queue's id at `+3`, and at `+7`
the id of the entry it is building, 0 after a manual override; Wealth clears
all three and is the only way out. Carried for the civ's own queues and
planets: creating, editing, reordering and deleting queues and entries, and the
links with the production that comes with them. A new queue takes a free object
id as a governor does and its planets follow it; a ship entry follows a
renumbered design. Refused: an entry naming a design the civ does not hold in
the merged galaxy, a facility type the galaxy lacks, a scan the scanning screen
does not list, any other item type, which the editor never writes, a planet
naming a queue or entry the merged galaxy lacks, and deleting a queue a planet
still uses. The planet counts are recounted from the merged links. The civ's `OWPR` `+132..+143` move with any production change,
queued or not, and are not carried.

**Ship designs**, `OWNR > DATA > DSGN`. A new design arrives as a second
`DSGN`/`SDPR` pair, the civ's design count raised by one and `SAVE+0` raised to
the new object id. The client allocates a new object id as one past the object
count, so two players designing on the same turn claim the same id; whoever is
merged second is renumbered, along with the id in the `DSGN` payload and in
any production queue that names it. A queue naming a design the galaxy does
not hold makes the engine refuse the whole galaxy, so such a queue is refused.
Deactivating or reactivating one of the civ's own designs is carried: the
design's last byte (`ShipDesign+0x114`), 1 inactive. Any other edit, and
deleting a design, are refused and named.

`OWPR` `+49`, `+77`, `+78`, `+100` and `+110` also move in a design submission
and are not carried: they move without a design too and differ between
identical designs, and `+100` is a counter the engine advances by 7 every tick.

**People**: the citizen array in `PLNT > PLPR`, the stationed military array
that follows it, and a ship's crew in `SHPR`, judged as one thing because
conscription and crewing move people between them. A submission never ticks,
so a civ's total people cannot change within it. Also: no existing soldier
record may be altered, nobody changes hands, and a civ may not come back with
fewer soldiers than it was served. The per-citizen value and opaque bytes are
judged against the state served, across all three arrays at once. Across a
tick the total does change: the engine's recruitment makes soldiers from food.

`PLPR+36` is a `u32` population count and `PLPR+40` begins that many
nine-byte records, kept sorted by job:

    +0  u8   job id, 0 farmer, 1 worker, 2 scientist, 5 miner, 6 banker
    +3  u32  the owning civ's object id
    +7  u8   a per-citizen value, permuted by a reassignment rather than changed

Soldiers are the same record with job id 3, in a second array that follows: a
`u32` count at `PLPR+40+9*citizens`, then the records. A ship's crew is counted
at `SHPR+4` with the records at `SHPR+8`. Moving somebody moves the record
byte for byte. Only these arrays are copied, never the rest of `PLPR`. The
per-citizen owner id usually equals the planet's owner, but a colony keeps the
owner of the colonists that founded it, so a civ can hold citizens on another
civ's planet. The checks compare how many people each owner holds, not their
jobs. People are carried only on the submitter's own planets and ships; a
change on a planet or ship another civ owns is refused and named, its own
citizens included, as the client has not been measured managing them.

**Military recruitment rate**, `PLPR+27`, a percentage in one byte.

**The setup prompts' confirms**, once each. The civ-trait prompt writes the
ten trait values in `OWNR > CVTR` and sets a flag in `OWPR`; the homeworld
prompt adds to the homeworld's three rate bytes (`PLPR+4..+6`) and its space
(`PLPR+11`) and sets another. A confirm is carried only when its flag was
clear as served and is set in the submission, and only as the dialog could
have made it: trait values the dialog gives and rates within 25..80, spending
`GSET.civilization_changes` or `GSET.homeworld_changes` exactly, on the planet
the client takes for the homeworld (the one founded earliest). Without the
confirm the prompt would come back on every served turn.

**System renaming**, the `SUN ` section's name at `+24` in its payload. Allowed
for a civ owning more than half of the system's settled planets in the served
state; unowned planets do not count.

**Planet renaming**, `PLNT` own payload `+24`: a `u32` length then the
characters. Carried for planets the submitter owns. `EXSY` also holds names
but is each civ's cache of names seen, not where a rename is authored, so it is
excluded.

**Governors**, `OWNR > DATA > GOVS`, and the planet's link to one, the last
`u32` of its `PLNT`. Creating a governor appends a `GVNR` and raises `SAVE+0`
to its id, so a new one is renumbered like a design when another player's
design, governor or production queue took the id. Editing rewrites the
`GVNR` in place, and deleting removes it. Assigning sets the planet's link; the
client offers no way to unassign. Carried for the civ's own governors and
planets, as records whose sections fit, with a printable name and at least one
rule, which the client requires; the rules themselves are the engine's to run.
Deleting a governor that still governs a planet is refused. Each `GVNR` counts
the planets it governs, and the referee recounts that from the merged links.

**Fleets**, `GLXY > FLET`: the object header (object id, x, z, y, owner), a
`DYNO` with the fleet's orders, and `FLDT`, a `u32`-length name, a `u32` count
and one `SHPR` per ship. Ships leave `GLXY` while in a fleet. A `SHPR` ends
with the id of the last fleet its ship was in, then the ship's own id.
Forming, renaming, a fleet's orders (copied whole, as a ship's are), ships
leaving and joining, one fleet joining another, and disbanding are carried for
the civ's own fleets, as where the submission leaves each of its ships. Every
ship keeps its `SHPR` as served, its crew judged with the people below; a ship
leaving takes the fleet's position and its own submitted `DYNO`, and a fleet
keeps its header. A new fleet raises `SAVE+0` and is renumbered like a design
when another player's new object took its id. It is refused unless its id is
new, it names the submitter, its name fits the client's 32 characters, and it
holds at least one ship, each the submitter's as served, placed once in the
submission, and all at one position. A ship joins only the civ's own fleet,
at that fleet's position. Moving or giving away a fleet, a fleet not laid out
as measured, and any change to another civ's fleet are refused. A refused
step leaves its ships where they were served.

What is refused
---------------
Everything not listed above is not accepted. Admirals and diplomacy are
refused by name until they are measured in the UI: creating, editing or
deleting an `ADML` in any civ's `ADMS`, a ship or fleet naming a different
admiral in its `DYNO` (its orders are taken, naming the admiral served), a
treaty added to, changed in or removed from the list after SAVE's last
section, and a stance moved in a civ's `KNPL`. Every
refusal is logged and recorded against the civ in the optional `notes` argument
to `merge`; `referee.py` writes it beside the turn and `player_turn.follow`
prints it. A submission
that cannot be read is dropped whole and named, with anything already taken
from it rolled back.
"""
import argparse
import collections
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp
import inject_civ as icv
import inject_design as idg
import facility_rules

# The research topic is written twice in a civ's OWPR, 8 bytes apart, after a
# map (u32 count at +8, 8-byte pairs) and the completed technologies (u32 count,
# [id, cost] records). They sit at +32 only while the civ has its two starting
# technologies and an empty map.
OWPR_MAP_COUNT_OFF = 8
OWPR_RECORD = 8
RESEARCH_FIELD_COUNT = 2
RESEARCH_FIELD_LEN = 4

# The setup prompts' flags, one byte each in OWPR, this far past the start of
# the second research field: Owner:359 (homeworld prompt, dialog 210) and
# Owner:360 (civ-trait prompt, dialog 218). Each prompt's OK sets its own.
HOMEWORLD_FLAG_OFF = OWPR_RECORD + 29
CIV_FLAG_OFF = OWPR_RECORD + 30

# The civ traits in CVTR: a u32 count at +16, then (index, value) pairs of
# u32s, one per trait the dialog shows. A confirm overwrites each value with
# what the clicks spent on it give.
CVTR_COUNT_OFF = 16
CVTR_PAIRS_OFF = 20
CVTR_PAIR = 8

# The civ-trait dialog's rules, from its table at 0x7FCD88 and value function
# 0x515910: per trait in index order, its name, the fewest clicks allowed, the
# value each click count gives from there, and what one click costs in
# changes. A negative cost is a trait that pays changes for being raised.
_OUTPUT = (-16, -8, 0, 8, 15, 21, 25, 28)
_STRIKE = (-20, -10, 0, 10, 19, 26, 31, 34)
TRAIT_RULES = (
    ('food consumption per citizen', -2, (-4, -2, 0, 2, 4), -1),
    ('income per citizen', -2, (-4, -2, 0, 2, 4), 1),
    ('food output', -2, _OUTPUT, 1),
    ('production output', -2, _OUTPUT, 1),
    ('science output', -2, _OUTPUT, 1),
    ('mining output', -2, (-24, -12, 0, 12, 23, 33, 41, 48), 1),
    ('ship speed', -2, (-10, -5, 0, 5, 9, 13, 16, 19), 1),
    ('ship firepower', -2, _STRIKE, 1),
    ('planetary defense', -2, _STRIKE, 1),
    ('troops', -2, _STRIKE, 1),
)

# The homeworld dialog's fields in PLPR: three signed rate bytes (food,
# production, science) and the planet space, a u16. A confirm adds the clicks
# to each rate, and raises space to a floor before adding five per click.
HOMEWORLD_RATE_OFFS = (4, 5, 6)
HOMEWORLD_SPACE_OFF = 11
HOMEWORLD_RATES = (25, 80)          # a rate the dialog leaves, inclusive
HOMEWORLD_SPACE_FLOOR = 350
HOMEWORLD_SPACE_STEP = 5
HOMEWORLD_SPACE_CLICKS = 20

# A planet's name, in PLNT's own payload: a u32 length then the characters.
PLANET_NAME_OFF = 24
MAX_NAME = 63

# What a client writes into a SUN whose stored name is empty.
DEFAULT_SUN_NAME = b'Unnamed'

# The citizen array inside a planet's PLPR.
POP_COUNT_OFF = 36
POP_ARRAY_OFF = 40
POP_RECORD = 9
JOB_IDS = {0: 'farmer', 1: 'worker', 2: 'scientist', 5: 'miner', 6: 'banker'}

# A soldier is the same nine-byte record with this job id, kept in a second
# array after the citizens.
MILITARY_JOB = 3

# Hurrying production moves three fields:
#
#     PLPR+23, u32            production points accumulated, raised to the cost
#     PROD payload byte 0     0 -> 1
#     OWNR, id_at+20, u32     credits, less 4 per point bought
#
# The manual's rule: 4 credits per production point left, and the item must be
# at least half finished. The item's total cost is not in the blob; the points
# bought are the distance the progress field moved. See hurry_acceptable.
#
# A ship's crew, inside SHPR: a u32 count then the same nine-byte records the
# planet keeps its soldiers in.
CREW_COUNT_OFF = 4
CREW_ARRAY_OFF = 8

# A design is a `DSGN` section inside the owning civ's `OWNR > DATA`, a sibling
# of `OWPR` and `SERV`, carrying its own object id and one `SDPR` child:
#
#     DSGN v4   u32 objectId ; SDPR ; one trailing byte
#     SDPR v0   u32 nameLen ; name ; six part lists ; u32 owningCivObjectId
#
# Immediately before a civ's first `DSGN`, in `DATA`'s own bytes, sits a `u32`
# count of that civ's designs. The readers are `inject_design.py`'s.
DESIGN_COUNT_BEFORE = 4     # the count dword, this far before the first DSGN
MAX_DESIGN_NAME = 15        # the client's name buffer, per inject_design

# A civ's governors are in its `OWNR > DATA > GOVS`: a u32 count, then one
# `GVNR` per governor, in creation order:
#
#     GVNR v0   u32 objectId ; u32 planets governed ; one byte ; GVDT
#     GVDT v1   u32 nameLen ; name ; two bytes ; u32 rule count ;
#               per rule a RULE (one byte, the rule's checkbox) then the
#               rule's own section (RLSP, RLPR, RLRR ...)
#
# A planet names its governor in the u32 after its `PLPR`, the last four bytes
# of the `PLNT`, 0 for none. The client offers no way to unassign one.
GOVS_COUNT = 4
GVNR_GOVERNS_AT = 4         # in the GVNR payload, after the object id
GVNR_FIXED = 9              # object id, planets governed and the one byte
GVDT_AFTER_NAME = 2
RULE_FLAG = 1
GOVERNOR_LINK = 4           # the PLNT's trailing u32

# Where a queued ship names its design, as an offset into a whole `PROD`
# section: past PROD's own 8-byte header, then the nested `SHIP` section whose
# header begins at +21 and whose payload is the design's object id.
PROD_DESIGN_AT = 8 + 29

# `OWPR` +49, +77, +78, +100 and +110 move in the same submission as a new
# design and are not carried: they also move without a design, and differ
# between submissions of identical designs. +100 is a u32 the engine advances
# by 7 every tick.

# PLPR+27 is the military recruitment rate, one byte, a percentage. PLPR+23 is
# a u32 of production points accumulated.
RECRUIT_OFF = 27
PROGRESS_OFF = 23

# A planet's food, as the client shows it: store over capacity, two adjacent
# u16 in PLPR. An unowned planet reads 0/40 and a generated homeworld 0/600;
# capacity grows with development, not population alone.
FOOD_STORE_OFF = 15
FOOD_CAP_OFF = 19

# A planet's facilities, as (type, count) pairs. They follow the military array
# after one byte: a u32 count, that many pairs of u32, then four zero bytes.
#
# An unbuilt colony carries none; a generated homeworld carries types 2, 6 and
# 9 once each. Type names are not decoded.
FACILITY_AFTER_MILITARY = 1
FACILITY_RECORD = 8

# The block between a colonised planet's `PROD` and its `ENLI`: 51 bytes, laid
# out below as offsets from the end of `PROD`. A planet given an owner without
# being colonised (only synthetic test galaxies) carries a 35-byte block
# without these fields.
#
# The founding turn is a u32 here and again at `PLPR+0`; a homeworld reads 0
# with an age equal to the turn. The population field equals the citizen
# count, and the seven u16 after it are earlier turns' populations (newest
# first, some carrying 0x1000, all zero at generation). A generated homeworld
# reads 1 at +16 and 10 at +47.
SETTLED_LEN = 51
SETTLED_POP_OFF = 0             # u16
SETTLED_HISTORY_OFF = 2         # SETTLED_HISTORY_LEN u16s
SETTLED_HISTORY_LEN = 7
SETTLED_COUNTER_OFF = 16        # u32, undecoded
SETTLED_OWNER_OFF = 24          # u32, the owning civ's object id
SETTLED_AGE_OFF = 28            # f32, turns since founded
SETTLED_FOUNDED_OFF = 32        # u32, the turn it was founded
SETTLED_LAST_OFF = 47           # u32, undecoded
FOUNDED_OFF = 0                 # u32 at PLPR+0, the founding turn again
PROD_HURRIED_OFF = 8            # PROD's own payload begins after its header
CREDITS_AFTER_ID = 20           # past the object id, which is itself past
                                # OWNR's length-prefixed name
HURRY_PER_POINT = 4


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


# A fleet, GLXY > FLET: the 20-byte object header (object id, x, z, y, owner),
# a DYNO with the fleet's orders, then FLDT: a u32-length name, a u32 member
# count and one SHPR per member. Member ships leave GLXY as SHIPs while in a
# fleet. A SHIP is the same header, a DYNO and a SHPR.
OBJECT_HEADER = 20
OBJECT_POS = slice(4, 16)       # x, z, y, three f32
OBJECT_OWNER_AT = 16
SHIP_VERSION = 0
FLET_VERSION = 0
FLDT_VERSION = 1
MAX_FLEET_NAME = 32             # the client cuts a longer name to this

# A SHPR ends with 12 bytes after its crew: a f32, the id of the last fleet the
# ship was in, and the ship's own id. The last fleet outlives the fleet.
SHPR_TAIL = 12
SHPR_FLEET_FROM_END = 8         # then the ship's own id, the last four bytes


def _section_bytes(tag, version, payload):
    return tag + struct.pack('<I', (version << sp.VERSION_SHIFT) | len(payload)) + payload


def shpr_ids(shpr):
    """(last fleet, own id) from a SHPR payload, or None unless its crew and
    tail fill it exactly."""
    crew = crew_of(shpr)
    if crew is None or len(shpr) != (CREW_ARRAY_OFF + len(crew) * POP_RECORD
                                     + SHPR_TAIL):
        return None
    return struct.unpack_from('<II', shpr, len(shpr) - SHPR_FLEET_FROM_END)


def with_last_fleet(shpr, fleet_oid):
    """A SHPR payload naming a different last fleet."""
    out = bytearray(shpr)
    struct.pack_into('<I', out, len(out) - SHPR_FLEET_FROM_END, int(fleet_oid))
    return bytes(out)


def _children(blob, at, end):
    """[(tag, version, whole section, payload)] filling blob[at:end] exactly,
    or None."""
    secs = _sections(blob, at, end)
    if secs is None:
        return None
    return [(tag, struct.unpack_from('<I', blob, p - 4)[0] >> sp.VERSION_SHIFT,
             bytes(blob[p - 8:e]), bytes(blob[p:e])) for tag, p, e in secs]


def fleet_name_acceptable(name):
    """(ok, reason) for a submitted fleet name."""
    if len(name) > MAX_FLEET_NAME:
        return False, (f'the name is {len(name)} bytes and the client cuts '
                       f'one to {MAX_FLEET_NAME}')
    if any(b < 0x20 or b > 0x7e for b in name):
        return False, 'the name holds bytes outside printable ASCII'
    return True, ''


def _read_object(blob, sec):
    """The header fields every SHIP and FLET starts with."""
    head = bytes(blob[sec.payload:sec.payload + OBJECT_HEADER])
    if len(head) < OBJECT_HEADER:
        return {'tag': sec.tag, 'sec': sec, 'oid': None, 'owner': None,
                'pos': None, 'record': bytes(blob[sec.start:sec.end]),
                'why': 'it is shorter than an object header'}
    return {'tag': sec.tag, 'sec': sec, 'oid': struct.unpack_from('<I', head)[0],
            'owner': struct.unpack_from('<I', head, OBJECT_OWNER_AT)[0],
            'pos': head[OBJECT_POS], 'record': bytes(blob[sec.start:sec.end]),
            'why': ''}


def read_ship(blob, sec):
    """A GLXY SHIP as a dict: oid, owner, pos, dyno (the whole section), shpr
    (its payload), shpr_version, last_fleet, and why, '' when it is a header,
    a DYNO and a SHPR naming the ship."""
    out = _read_object(blob, sec)
    old = next(sec.find('SHPR'), None)
    out.update(dyno=None, shpr_version=None, last_fleet=None,
               shpr=bytes(blob[old.payload:old.end]) if old else None)
    if out['why']:
        return out
    kids = _children(blob, sec.payload + OBJECT_HEADER, sec.end)
    if not kids or [k[0] for k in kids] != [b'DYNO', b'SHPR']:
        out['why'] = 'it is not a header, a DYNO and a SHPR'
        return out
    ids = shpr_ids(kids[1][3])
    if ids is None or ids[1] != out['oid']:
        out['why'] = 'its SHPR does not end by naming it'
        return out
    out.update(dyno=kids[0][2], shpr=kids[1][3], shpr_version=kids[1][1],
               last_fleet=ids[0])
    return out


def read_fleet(blob, sec):
    """A GLXY FLET as a dict: oid, owner, pos, dyno (the whole section), name,
    members [(ship id, SHPR payload, SHPR version)] in order, and why, '' when
    it has the layout measured."""
    out = _read_object(blob, sec)
    out.update(dyno=None, name=None, members=[])
    if out['why']:
        return out
    kids = _children(blob, sec.payload + OBJECT_HEADER, sec.end)
    if (sec.version != FLET_VERSION or not kids
            or [k[0] for k in kids] != [b'DYNO', b'FLDT']
            or kids[1][1] != FLDT_VERSION):
        out['why'] = 'it is not a header, a DYNO and an FLDT as measured'
        return out
    fldt = kids[1][3]
    n = struct.unpack_from('<I', fldt)[0] if len(fldt) >= 4 else None
    if n is None or 8 + n > len(fldt):
        out['why'] = 'its name runs past its FLDT'
        return out
    count = struct.unpack_from('<I', fldt, 4 + n)[0]
    members = _children(fldt, 8 + n, len(fldt))
    if (members is None or len(members) != count
            or any(tag != b'SHPR' for tag, *_r in members)):
        out['why'] = f'its FLDT does not hold the {count} SHPR(s) it counts'
        return out
    ids = [shpr_ids(payload) for _t, _v, _w, payload in members]
    if None in ids:
        out['why'] = 'a member SHPR does not end by naming its ship'
        return out
    out.update(dyno=kids[0][2], name=bytes(fldt[4:4 + n]),
               members=[(own, payload, version) for (_f, own), (_t, version,
                        _w, payload) in zip(ids, members)])
    return out


def space_objects(blob):
    """(tree, GLXY, [object]): GLXY's SHIPs and FLETs in order, each read by
    read_ship or read_fleet, its section under 'sec'."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    objects = []
    for sec in glxy.children:
        if sec.tag == b'SHIP':
            objects.append(read_ship(blob, sec))
        elif sec.tag == b'FLET':
            objects.append(read_fleet(blob, sec))
    return tree, glxy, objects


def fleet_index(blob):
    """{fleetObjectId: fleet} for every FLET, read by read_fleet."""
    return {o['oid']: o for o in space_objects(blob)[2] if o['tag'] == b'FLET'}


def fleet_orders(served, sub, civ_oid, names):
    """(layout, refusals) for one civ's fleets, judged against the state served.

    layout is None when nothing changes, else how the civ's ships and fleets
    end the turn:

        target      {ship: the fleet it ends in, or None}, for each ship
        fleets      {fleet: {name, dyno, members, pos, new}}, each with a ship
        out         {ship: DYNO section} for each ship leaving a fleet
        last_fleet  {ship: the last fleet its submitted SHPR names}, singles
        appended    [('ship' | 'fleet', id)], the new objects in submitted order
        log         [line], one per change taken

    Fleet ids are as submitted; a new one is renumbered when it is applied. A
    ship whose move is refused stays where it was served, and a fleet left
    with no ship is gone, as disbanding leaves it.
    """
    s_objects = space_objects(served)[2]
    t_objects = space_objects(sub)[2]
    high = struct.unpack_from('<I', served, idg.HIGH_WATER_ID)[0]
    refusals, log = [], []

    def who(civ):
        return names.get(civ, civ)

    def label(fid, name):
        return f"fleet {fid} ({name.decode('latin1')!r})" if name is not None \
            else f'fleet {fid}'

    served_fleets = {o['oid']: o for o in s_objects if o['tag'] == b'FLET'}
    mine_fleets = {fid: f for fid, f in served_fleets.items()
                   if f['owner'] == civ_oid and not f['why']}
    at, owner_of, pos_of, field_of = {}, {}, {}, {}
    for o in s_objects:
        if o['why']:
            continue
        if o['tag'] == b'SHIP':
            at[o['oid']], owner_of[o['oid']] = None, o['owner']
            pos_of[o['oid']], field_of[o['oid']] = o['pos'], o['last_fleet']
        else:
            for m, shpr, _v in o['members']:
                at[m], owner_of[m], pos_of[m] = o['oid'], o['owner'], o['pos']
                field_of[m] = shpr_ids(shpr)[0]
    mine = {s for s, owner in owner_of.items() if owner == civ_oid}

    # Where the submission puts each ship, and its readable fleets.
    counted = collections.Counter(o['oid'] for o in t_objects
                                  if o['tag'] == b'FLET')
    places = collections.defaultdict(list)
    singles, fleets_t, pinned = {}, {}, set()
    for o in t_objects:
        if o['tag'] == b'SHIP':
            places[o['oid']].append(None)
            singles[o['oid']] = o
            continue
        fid, why = o['oid'], o['why']
        if not why and counted[fid] > 1:
            why = f'the submission holds {counted[fid]} fleets under its id'
        if why:
            was = served_fleets.get(fid)
            if was is None:
                refusals.append(f'{label(fid, None)} DROPPED, {why}')
            elif was['owner'] != civ_oid:
                if o['record'] != was['record']:
                    refusals.append(f'fleet {fid}: DROPPED, owned by '
                                    f"{who(was['owner'])}")
            else:
                if o['record'] != was['record'] or counted[fid] > 1:
                    refusals.append(f'fleet {fid}: changes DROPPED, {why}; it '
                                    f'stays as served')
                pinned |= {m for m, _s, _v in was['members']}
            continue
        fleets_t[fid] = o
        for m, _s, _v in o['members']:
            places[m].append(fid)

    for fid, f in fleets_t.items():
        was = served_fleets.get(fid)
        if was is not None and was['owner'] != civ_oid \
                and f['record'] != was['record']:
            refusals.append(f"fleet {fid}: DROPPED, owned by {who(was['owner'])}")

    # New fleets: formed this turn, from ships this civ was served together.
    accepted = {}
    for fid, f in fleets_t.items():
        if fid in served_fleets:
            continue
        members = [m for m, _s, _v in f['members']]
        stray = next((m for m in members if m not in mine), None)
        twice = next((m for m in members
                      if len(places[m]) > 1 or m in pinned), None)
        name_ok, name_why = fleet_name_acceptable(f['name'])
        if fid <= high:
            why = f'it is new and {fid} is not a new object id'
        elif f['owner'] != civ_oid:
            why = f"it names {who(f['owner'])} as its owner"
        elif not name_ok:
            why = name_why
        elif not members:
            why = 'it holds no ship'
        elif stray is not None:
            why = f'ship {stray} is not one this civ was served'
        elif twice is not None:
            why = f'ship {twice} is placed more than once'
        elif len({pos_of[m] for m in members}) != 1:
            why = 'its ships are not all at one position'
        else:
            accepted[fid] = f
            continue
        refusals.append(f'{label(fid, f["name"])} DROPPED, {why}; its ships '
                        f'stay as they were')

    # Where each of the civ's ships ends the turn.
    target, out = {}, {}
    for s in sorted(mine):
        here, there = at[s], places.get(s, [])
        if s in pinned or not there or there == [here]:
            target[s] = here
            continue
        target[s] = here
        if len(there) > 1:
            refusals.append(f'ship {s}: fleet change DROPPED, the submission '
                            f'places it {len(there)} times')
            continue
        there = there[0]
        if there is None:
            if singles[s]['why']:
                refusals.append(f'ship {s}: leaving fleet {here} DROPPED, '
                                f"{singles[s]['why']}")
            else:
                # It may keep the fleet's admiral or have none.
                dyno, why = admiral_kept(singles[s]['dyno'], (
                    dyno_admiral(served_fleets[here]['dyno']) or 0, 0))
                if why:
                    refusals.append(f'ship {s}: {why}')
                target[s], out[s] = None, dyno
        elif there in accepted:
            target[s] = there
        elif there in mine_fleets:
            if pos_of[s] != mine_fleets[there]['pos']:
                refusals.append(f'ship {s}: joining fleet {there} DROPPED, it '
                                f"is not at the fleet's position")
            else:
                target[s] = there
        elif there in served_fleets:
            refusals.append(f'ship {s}: joining fleet {there} DROPPED, the '
                            f"fleet is owned by {who(served_fleets[there]['owner'])}")

    # The fleets that end the turn with a ship in them.
    fleets = {}
    for fid in list(mine_fleets) + list(accepted):
        listed = [m for m, _s, _v in fleets_t[fid]['members']] \
            if fid in fleets_t else []
        kept = [m for m, _s, _v in mine_fleets[fid]['members']] \
            if fid in mine_fleets else []
        members = [m for m in listed if target.get(m) == fid]
        members += [m for m in kept if target.get(m) == fid and m not in members]
        was = mine_fleets.get(fid)
        now = fleets_t.get(fid)
        if not members:
            if was is not None:
                log.append(f'fleet {fid}: disbanded, no ship is left in it')
            continue
        if was is None:
            dyno, why = admiral_kept(now['dyno'], (0,))
            if why:
                refusals.append(f'fleet {fid}: {why}')
            fleets[fid] = {'name': now['name'], 'dyno': dyno,
                           'members': members, 'pos': pos_of[members[0]],
                           'new': True}
            log.append(f"{label(fid, now['name'])}: formed from ship(s) "
                       f'{members}')
            continue
        name, dyno = was['name'], was['dyno']
        if now is not None:
            if (now['pos'], now['owner']) != (was['pos'], was['owner']):
                refusals.append(f'fleet {fid}: position or owner DROPPED, a '
                                f'submission neither moves nor gives away a '
                                f'fleet')
            if now['name'] != was['name']:
                ok, why = fleet_name_acceptable(now['name'])
                if ok:
                    name = now['name']
                    log.append(f"fleet {fid}: renamed "
                               f"{was['name'].decode('latin1')!r} -> "
                               f"{name.decode('latin1')!r}")
                else:
                    refusals.append(f'fleet {fid}: rename DROPPED, {why}')
            if now['dyno'] != was['dyno']:
                taken, why = admiral_kept(
                    now['dyno'], (dyno_admiral(was['dyno']) or 0,))
                if why:
                    refusals.append(f'fleet {fid}: {why}')
                if taken != was['dyno']:
                    dyno = taken
                    log.append(f'fleet {fid}: order taken ({len(was["dyno"])} '
                               f'-> {len(dyno)} bytes)')
        fleets[fid] = {'name': name, 'dyno': dyno, 'members': members,
                       'pos': was['pos'], 'new': False}
        joined = [m for m in members if at[m] != fid]
        if joined:
            log.append(f'fleet {fid}: joined by ship(s) {joined}')
    for s in sorted(out):
        log.append(f'ship {s}: left fleet {at[s]}')

    last_fleet = {s: singles[s]['last_fleet'] for s in mine
                  if target[s] is None and s in singles
                  and not singles[s]['why']}
    refusals = list(dict.fromkeys(refusals))    # a twice-held id is said once
    if not log and all(v == field_of[s] for s, v in last_fleet.items()):
        return None, refusals

    appended = []
    for o in t_objects:
        if o['tag'] == b'SHIP' and o['oid'] in out:
            appended.append(('ship', o['oid']))
        elif o['tag'] == b'FLET' and o['oid'] in accepted:
            appended.append(('fleet', o['oid']))
    return {'target': target, 'fleets': fleets, 'out': out,
            'last_fleet': last_fleet, 'appended': appended,
            'log': log}, refusals


def set_fleets(blob, civ_oid, layout, renumbered=None):
    """`blob` with one civ's ships and fleets laid out as `layout` says (see
    fleet_orders), every enclosing size corrected.

    Each ship keeps the SHPR `blob` holds for it, crew included, naming the
    fleet it is in, or for a single ship the last fleet it was in. A fleet
    keeps its header and a new one takes its ships' position. Ships and fleets
    that are new to GLXY follow its last SHIP or FLET, where the client puts
    them. `renumbered` maps a new fleet's submitted id to the id it takes.
    """
    renumbered = renumbered or {}
    tree, glxy, objects = space_objects(blob)
    shpr, pos_of = {}, {}
    for o in objects:
        if o['owner'] != civ_oid or o['why']:
            continue
        if o['tag'] == b'SHIP':
            shpr[o['oid']] = (o['shpr'], o['shpr_version'])
            pos_of[o['oid']] = o['pos']
        else:
            for m, payload, version in o['members']:
                shpr[m] = (payload, version)
                pos_of[m] = o['pos']
    final = {renumbered.get(fid, fid) for fid in layout['fleets']}

    def last(s):
        held = shpr_ids(shpr[s][0])[0]
        want = layout['last_fleet'].get(s)
        if want is None:
            return held
        want = renumbered.get(want, want)
        return want if want == held or want in final else held

    def ship_bytes(s, pos, dyno, version=SHIP_VERSION):
        payload, shpr_version = shpr[s]
        head = struct.pack('<I', s) + pos + struct.pack('<I', civ_oid)
        return _section_bytes(b'SHIP', version, head + dyno + _section_bytes(
            b'SHPR', shpr_version, with_last_fleet(payload, last(s))))

    def fleet_bytes(fid):
        f, oid = layout['fleets'][fid], renumbered.get(fid, fid)
        fldt = (struct.pack('<I', len(f['name'])) + f['name']
                + struct.pack('<I', len(f['members']))
                + b''.join(_section_bytes(b'SHPR', shpr[m][1],
                                          with_last_fleet(shpr[m][0], oid))
                           for m in f['members']))
        head = struct.pack('<I', oid) + f['pos'] + struct.pack('<I', civ_oid)
        return _section_bytes(b'FLET', FLET_VERSION, head + f['dyno']
                              + _section_bytes(b'FLDT', FLDT_VERSION, fldt))

    edits, after = [], None
    for o in objects:
        sec = o['sec']
        after = sec.end
        if o['owner'] != civ_oid or o['why']:
            continue
        if o['tag'] == b'SHIP':
            if o['oid'] not in layout['target']:
                continue
            if layout['target'][o['oid']] is None:
                new = ship_bytes(o['oid'], o['pos'], o['dyno'], sec.version)
            else:
                new = b''
        else:
            new = fleet_bytes(o['oid']) if o['oid'] in layout['fleets'] else b''
        if new != o['record']:
            edits.append((sec.start, sec.end, new))
    added = b''.join(fleet_bytes(oid) if kind == 'fleet'
                     else ship_bytes(oid, pos_of[oid], layout['out'][oid])
                     for kind, oid in layout['appended'])
    if added:
        if after is None:
            raise SystemExit('GLXY holds no SHIP or FLET to add after')
        edits.append((after, after, added))
    body = bytearray(blob[glxy.payload:glxy.end])
    for start, end, new in sorted(edits, reverse=True):
        body[start - glxy.payload:end - glxy.payload] = new
    return sp.replace_payload(blob, tree, glxy, bytes(body))


def planet_index(blob):
    """{planetObjectId: (owner, prod, plpr_payload, (len, name))}."""
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
                        bytes(blob[prod.start:prod.end]) if prod else None,
                        bytes(blob[plpr.payload:plpr.end]) if plpr else None,
                        planet_name(blob, sec))
    return out


def citizens_of(raw):
    """[(job, owner, extra, rest_bytes)] from a PLPR payload, or None."""
    if len(raw) < POP_ARRAY_OFF + 4:
        return None
    n = struct.unpack_from('<I', raw, POP_COUNT_OFF)[0]
    end = POP_ARRAY_OFF + n * POP_RECORD
    if end > len(raw):
        return None
    out = []
    for i in range(n):
        r = raw[POP_ARRAY_OFF + i * POP_RECORD:
                POP_ARRAY_OFF + (i + 1) * POP_RECORD]
        out.append((r[0], struct.unpack_from('<I', r, 3)[0], r[7],
                    bytes((r[1], r[2], r[8]))))
    return out


def citizen_bytes(raw):
    """The array's own bytes, for copying, or None when it is unreadable."""
    if citizens_of(raw) is None:
        return None
    n = struct.unpack_from('<I', raw, POP_COUNT_OFF)[0]
    return raw[POP_ARRAY_OFF:POP_ARRAY_OFF + n * POP_RECORD]


def population(blob, civ_oid):
    """({planet: (citizens, military)}, {ship: crew}) for one civ, or None."""
    planets, ships = {}, {}
    for oid, (owner, _prod, plpr, _nm) in planet_index(blob).items():
        if owner != civ_oid:
            continue
        cz = citizens_of(plpr)
        _at, mil = military_of(plpr)
        if cz is None or mil is None:
            return None, None
        planets[oid] = (cz, mil)
    for oid, (owner, shpr) in _ship_crew_index(blob).items():
        if owner != civ_oid:
            continue
        crew = crew_of(shpr or b'')
        if crew is None:
            return None, None
        ships[oid] = crew
    return planets, ships


def _owner_of(record):
    return struct.unpack_from('<I', record, 3)[0]


def _record(raw):
    """(job, owner, extra, rest) from a raw nine-byte soldier or crew record.

    The same shape `citizens_of` returns.
    """
    return (raw[0], struct.unpack_from('<I', raw, 3)[0], raw[7],
            bytes((raw[1], raw[2], raw[8])))


def _everyone(planets, ships):
    citizens, soldiers = [], []
    for _oid, (cz, mil) in planets.items():
        citizens += cz
        soldiers += mil
    for _oid, crew in ships.items():
        soldiers += crew
    return citizens, soldiers


def people_acceptable(served, submitted, civ_oid):
    """(ok, reason) for everything a civ did to its own people in one turn.

    What a submission may do, given that it never ticks:

        move soldiers between the civ's own planets and ships
        turn a citizen into a soldier
        reassign jobs among citizens

    What it may not do:

        change the total number of people it holds
        come back with fewer soldiers than it was served
        alter an existing soldier's record
        change who owns anybody
        invent per-citizen values

    The total holds within a submission only; across a tick the engine's
    recruitment makes soldiers from food without costing a citizen.
    """
    import collections
    pa, sa = population(served, civ_oid)
    pb, sb = population(submitted, civ_oid)
    if pa is None or pb is None:
        return False, 'the citizen or military array is unreadable'
    if set(pa) != set(pb) or set(sa) != set(sb):
        return False, 'the set of planets or ships changed within the turn'

    cit_a, sol_a = _everyone(pa, sa)
    cit_b, sol_b = _everyone(pb, sb)
    if len(sol_b) < len(sol_a):
        return (False, f'{len(sol_a)} soldier(s) served and {len(sol_b)} came '
                f'back; retiring from service is not carried yet')
    if len(cit_a) + len(sol_a) != len(cit_b) + len(sol_b):
        return (False, f'{len(cit_a) + len(sol_a)} people served and '
                f'{len(cit_b) + len(sol_b)} came back')
    if collections.Counter(sol_a) - collections.Counter(sol_b):
        return (False, "an existing soldier's record was rewritten rather than "
                "moved")
    owners_a = collections.Counter([c[1] for c in cit_a]
                                   + [_owner_of(r) for r in sol_a])
    owners_b = collections.Counter([c[1] for c in cit_b]
                                   + [_owner_of(r) for r in sol_b])
    if owners_a != owners_b:
        return False, 'somebody changed hands'
    bad = {c[0] for c in cit_b} - set(JOB_IDS)
    if bad:
        return False, f'unknown job id(s) {sorted(bad)}'

    # What a record carries may not be invented: judged against what was
    # served (a civ may carry whatever the engine gave it, including non-zero
    # opaque bytes), across citizens, soldiers and crew at once, because
    # conscription rewrites only the job.
    all_a = [_record(r) for r in sol_a] + list(cit_a)
    all_b = [_record(r) for r in sol_b] + list(cit_b)
    if collections.Counter(c[3] for c in all_b) - collections.Counter(
            c[3] for c in all_a):
        return (False, 'opaque bytes in a people record were invented rather '
                'than carried')
    if collections.Counter(c[2] for c in all_b) - collections.Counter(
            c[2] for c in all_a):
        return False, 'per-citizen values were invented rather than reordered'
    return True, ''


def foreign_people_refusals(planets, crews, submitted, civ_oid, names):
    """[reason] for people changed on a planet or ship another civ owns.

    `planets` and `crews` are `planet_index` and `_ship_crew_index` of the
    state served. `people_acceptable` judges only what the submitter owns, so
    a change anywhere else is named here rather than passed over. A civ's own
    citizens on such a planet are named with what was done to them.
    """
    out = []
    after = planet_index(submitted)
    for oid, (owner, _prod, plpr, _nm) in sorted(planets.items()):
        if owner == civ_oid or oid not in after or plpr is None:
            continue
        sub_plpr = after[oid][2]
        if sub_plpr is None:
            continue
        if (citizen_bytes(plpr), military_of(plpr)[1]) == (
                citizen_bytes(sub_plpr), military_of(sub_plpr)[1]):
            continue
        why = (f"planet {oid}: people DROPPED, owned by "
               f"{names.get(owner, owner) if owner else 'nobody'}")
        cz_a, cz_b = citizens_of(plpr), citizens_of(sub_plpr)
        if cz_a is not None and cz_b is not None:
            own_a = [c[0] for c in cz_a if c[1] == civ_oid]
            own_b = [c[0] for c in cz_b if c[1] == civ_oid]
            if sorted(own_a) != sorted(own_b):
                why += (f"; {names.get(civ_oid, civ_oid)}'s citizens there "
                        f"went {_tally(own_a) or 'none'} -> "
                        f"{_tally(own_b) or 'none'}, and people on another "
                        f"civ's planet are not carried")
        out.append(why)
    after = _ship_crew_index(submitted)
    for oid, (owner, shpr) in sorted(crews.items()):
        if owner == civ_oid or oid not in after:
            continue
        if crew_of(shpr or b'') != crew_of(after[oid][1] or b''):
            out.append(f"ship {oid}: crew DROPPED, owned by "
                       f"{names.get(owner, owner)}")
    return out


def systems(blob):
    """{sunObjectId: (planet_owner_counts, planet_total, (len, name))}."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        sun = next((s for s in sola.children if s.tag == b'SUN '), None)
        if sun is None:
            continue
        oid = struct.unpack_from('<I', blob, sun.payload)[0]
        owners, total = {}, 0
        for sec in sola.find('PLNT'):
            total += 1
            o = struct.unpack_from('<I', blob, sec.payload + 16)[0]
            if o:
                owners[o] = owners.get(o, 0) + 1
        out[oid] = (owners, total, object_name(blob, sun))
    return out


def set_system_name(blob, sun_oid, name: bytes):
    """Write a system's name onto its SUN section."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sun in sola.children:
            if sun.tag != b'SUN ':
                continue
            if struct.unpack_from('<I', blob, sun.payload)[0] != sun_oid:
                continue
            at = sun.payload + PLANET_NAME_OFF
            old = struct.unpack_from('<I', blob, at)[0]
            body = (bytes(blob[sun.payload:at]) + struct.pack('<I', len(name))
                    + name + bytes(blob[at + 4 + old:sun.end]))
            return sp.replace_payload(blob, tree, sun, body)
    raise SystemExit(f'system {sun_oid} not found')


def object_name(blob, sec):
    """(length, name_bytes) from a PLNT or SUN section, or None."""
    own = sp.own_bytes(blob, sec)
    if len(own) < PLANET_NAME_OFF + 4:
        return None
    n = struct.unpack_from('<I', own, PLANET_NAME_OFF)[0]
    if n > MAX_NAME or PLANET_NAME_OFF + 4 + n > len(own):
        return None
    return n, bytes(own[PLANET_NAME_OFF + 4:PLANET_NAME_OFF + 4 + n])


def planet_name(blob, sec):
    """(length, name_bytes) from a PLNT section, or None if unreadable."""
    return object_name(blob, sec)


def name_acceptable(name: bytes):
    """(ok, reason) for a submitted planet name."""
    if len(name) > MAX_NAME:
        return False, f'name is {len(name)} bytes, over {MAX_NAME}'
    if any(b < 0x20 or b > 0x7e for b in name):
        return False, 'name holds bytes outside printable ASCII'
    return True, ''


def set_planet_name(blob, planet_oid, name: bytes):
    """Write a planet's name. Its length may change, so sizes are corrected."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            at = sec.payload + PLANET_NAME_OFF
            old = struct.unpack_from('<I', blob, at)[0]
            body = (bytes(blob[sec.payload:at]) + struct.pack('<I', len(name))
                    + name + bytes(blob[at + 4 + old:sec.end]))
            return sp.replace_payload(blob, tree, sec, body)
    raise SystemExit(f'planet {planet_oid} not found')


def research_offsets(blob, owpr):
    """Offsets within OWPR's payload of the research fields, or None when
    the lists before them do not fit the section."""
    p, size = owpr.payload, owpr.size
    if size < OWPR_MAP_COUNT_OFF + 4:
        return None
    m = struct.unpack_from('<I', blob, p + OWPR_MAP_COUNT_OFF)[0]
    n_at = OWPR_MAP_COUNT_OFF + 4 + m * OWPR_RECORD
    if n_at + 4 > size:
        return None
    n = struct.unpack_from('<I', blob, p + n_at)[0]
    first = n_at + 4 + n * OWPR_RECORD
    offs = tuple(first + i * OWPR_RECORD for i in range(RESEARCH_FIELD_COUNT))
    if offs[-1] + RESEARCH_FIELD_LEN > size:
        return None
    return offs


def completed_research(blob, civ_oid):
    """The technology ids a civ has completed, in order, or None."""
    owpr = _owpr(blob, civ_oid)
    if owpr is None:
        return None
    p = owpr.payload
    m = struct.unpack_from('<I', blob, p + OWPR_MAP_COUNT_OFF)[0]
    n_at = p + OWPR_MAP_COUNT_OFF + 4 + m * OWPR_RECORD
    n = struct.unpack_from('<I', blob, n_at)[0]
    return [struct.unpack_from('<I', blob, n_at + 4 + i * OWPR_RECORD)[0]
            for i in range(n)]


def research_of(blob, civ_oid):
    """The bytes of a civ's research fields, or None when OWPR is unreadable."""
    owpr = _owpr(blob, civ_oid)
    if owpr is None:
        return None
    return tuple(bytes(blob[owpr.payload + off:owpr.payload + off + RESEARCH_FIELD_LEN])
                 for off in research_offsets(blob, owpr))


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
        if owpr is None or research_offsets(blob, owpr) is None:
            return None
        return owpr
    return None


def civ_by_name(blob, name):
    for o in icv.owner_records(blob):
        if o['name'] == name:
            return o
    raise SystemExit(f"no civ named {name!r}; saw "
                     f"{[o['name'] for o in icv.owner_records(blob)]}")


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


def progress_of(plpr):
    """Production points accumulated on a planet, or None."""
    if plpr is None or len(plpr) < PROGRESS_OFF + 4:
        return None
    return struct.unpack_from('<I', plpr, PROGRESS_OFF)[0]


def design_sections(blob):
    """([(civObjectId, DATA section, [DSGN sections])], tree) for every civ.

    The tree comes back with them because `sp.replace_payload` has to be given
    the one its target was parsed from.
    """
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = []
    for o in glxy.children:
        if o.tag != b'OWNR':
            continue
        n = struct.unpack_from('<I', blob, o.payload)[0]
        oid = struct.unpack_from('<I', blob, o.payload + 4 + n)[0]
        data = next((c for c in o.children if c.tag == b'DATA'), None)
        if data is None:
            continue
        out.append((oid, data, [c for c in data.children if c.tag == b'DSGN']))
    return out, tree


def design_index(blob):
    """{designObjectId: {civ, name, parts, claims, record}} for every design.

    `civ` is the civ whose `OWNR` the design sits in and `claims` is the civ its
    own payload names. `name`, `parts` and `claims` are None when the record
    does not parse.
    """
    out = {}
    for civ_oid, _data, designs in design_sections(blob)[0]:
        for d in designs:
            oid = struct.unpack_from('<I', blob, d.payload)[0]
            try:
                _oid, name, parts, claims = idg.read_design(blob, d.start)
            except Exception:                                   # noqa: BLE001
                name, parts, claims = None, None, None
            out[oid] = {'civ': civ_oid, 'name': name, 'parts': parts,
                        'claims': claims,
                        'record': bytes(blob[d.start:d.end])}
    return out


def designs_of(blob, civ_oid):
    """The design object ids one civ owns."""
    return {oid for oid, d in design_index(blob).items() if d['civ'] == civ_oid}


def next_object_id(blob):
    """An object id nothing in this blob is using.

    `SAVE+0` is the highest object id in use, not a count of objects, and the
    engine allocates its next object at that value plus one. A carried design
    must raise it (`set_high_water`).
    """
    return idg.max_object_id(blob) + 1


def set_high_water(blob, oid):
    """Raise `SAVE`'s object counter to cover an object just added."""
    out = bytearray(blob)
    if struct.unpack_from('<I', out, idg.HIGH_WATER_ID)[0] < oid:
        struct.pack_into('<I', out, idg.HIGH_WATER_ID, int(oid))
    return bytes(out)


def with_design_id(record, oid):
    """A `DSGN` record carrying a different object id."""
    out = bytearray(record)
    struct.pack_into('<I', out, 8, int(oid))
    return bytes(out)


def with_prod_design(prod, oid):
    """A `PROD` section whose queued ship names a different design."""
    out = bytearray(prod)
    struct.pack_into('<I', out, PROD_DESIGN_AT, int(oid))
    return bytes(out)


def design_name_acceptable(name):
    """(ok, reason) for a submitted design name."""
    if name is None:
        return False, 'the record does not parse'
    raw = name.encode('latin1')
    if not raw:
        return False, 'the name is empty'
    if len(raw) > MAX_DESIGN_NAME:
        return (False, f'the name is {len(raw)} bytes and the buffer the '
                f'client offers holds {MAX_DESIGN_NAME}')
    if any(b < 0x20 or b > 0x7e for b in raw):
        return False, 'the name holds bytes outside printable ASCII'
    return True, ''


def add_design(blob, civ_oid, record):
    """Append a `DSGN` to a civ's `DATA` and bump its design count.

    Every enclosing size is corrected. The count dword is only locatable
    relative to the civ's first `DSGN`, so a civ owning none is refused.
    """
    secs, tree = design_sections(blob)
    for oid, data, designs in secs:
        if oid != civ_oid:
            continue
        if not designs:
            raise SystemExit(f'civ {civ_oid} owns no design to append after, '
                             f'and the count dword is only locatable from one')
        first, last = designs[0], designs[-1]
        count_at = first.start - DESIGN_COUNT_BEFORE
        if count_at < data.payload:
            raise SystemExit(f'civ {civ_oid} has no design count before its '
                             f'first DSGN')
        count = struct.unpack_from('<I', blob, count_at)[0]
        if count != len(designs):
            raise SystemExit(f'civ {civ_oid} holds {len(designs)} design(s) '
                             f'and a count field reading {count}')
        body = bytearray(blob[data.payload:data.end])
        struct.pack_into('<I', body, count_at - data.payload, count + 1)
        at = last.end - data.payload
        body[at:at] = record
        return sp.replace_payload(blob, tree, data, bytes(body))
    raise SystemExit(f'civ {civ_oid} is not in this blob')


def design_orders(served, submitted, civ_oid):
    """([(objectId, design)], [(objectId, inactive)], [refusals]) for one civ's
    ship designs.

    `served` and `submitted` are `design_index` results. A design is new when
    the submission holds one the served state does not. A design whose record
    differs only in its last byte was deactivated or reactivated. Other edits,
    deleting, and touching another civ's design are refused and named.
    """
    new, toggled, refused = [], [], []

    for oid in sorted(set(served) - set(submitted)):
        whose = 'its own' if served[oid]['civ'] == civ_oid else "another civ's"
        refused.append(f'design {oid} ({served[oid]["name"]!r}) DROPPED, it is '
                       f'missing from the submission and is {whose}; deleting '
                       f'a design is not carried yet')

    for oid in sorted(set(served) & set(submitted)):
        was, now = served[oid]['record'], submitted[oid]['record']
        if now == was:
            continue
        if (len(now) == len(was) and now[:-1] == was[:-1]
                and now[-1] in (0, 1)):
            if served[oid]['civ'] != civ_oid:
                refused.append(f'design {oid} ({served[oid]["name"]!r}) '
                               f"(de)activation DROPPED, it is another civ's")
            else:
                toggled.append((oid, bool(now[-1])))
            continue
        refused.append(f'design {oid} ({served[oid]["name"]!r}) DROPPED, it '
                       f'came back changed; editing a design is not carried '
                       f'yet')

    for oid in sorted(set(submitted) - set(served)):
        d = submitted[oid]
        ok, why = design_name_acceptable(d['name'])
        if not ok:
            refused.append(f'design {oid} DROPPED, {why}')
            continue
        if d['civ'] != civ_oid:
            refused.append(f'design {oid} ({d["name"]!r}) DROPPED, it arrived '
                           f"under another civ's OWNR")
            continue
        if d['claims'] != civ_oid:
            refused.append(f'design {oid} ({d["name"]!r}) DROPPED, its payload '
                           f'names civ {d["claims"]} as the owner, not the '
                           f'submitter')
            continue
        siblings = {v['name'] for k, v in submitted.items()
                    if v['civ'] == civ_oid and k != oid}
        if d['name'] in siblings:
            refused.append(f'design {oid} DROPPED, this civ already owns one '
                           f'named {d["name"]!r} and the client requires a '
                           f'name to be unique within a civ')
            continue
        new.append((oid, d))
    return new, toggled, refused


def set_design_inactive(blob, design_oid, inactive):
    """`blob` with a design's last byte, `ShipDesign+0x114`, set: 1 inactive,
    0 active. The design's own section ends with it."""
    for _civ, _data, designs in design_sections(blob)[0]:
        for d in designs:
            if struct.unpack_from('<I', blob, d.payload)[0] == design_oid:
                out = bytearray(blob)
                out[d.end - 1] = 1 if inactive else 0
                return bytes(out)
    raise KeyError(design_oid)


# What a PROD can name in its nested section: nothing (piling up wealth), a
# facility, a scan, or a ship by design id.
PROD_KINDS = (b'WLTH', b'FCLT', b'SCAN', b'SHIP')
PROD_QUEUE_OFF = 2          # queue flag, queue object id, u16 entry id
PROD_LIST_OFF = 9           # u32 count of four-byte pairs
PROD_FACILITY_OFF = 13      # u32 facility type, after an empty list
PROD_KIND_OFF = 21          # the nested section, after an empty list


def _prod_list_len(prod):
    """Bytes taken by PROD's list at +9: a u32 count of four-byte pairs. It
    has been empty in every capture, but what follows it moves with it."""
    return 4 * struct.unpack_from('<I', prod, 8 + PROD_LIST_OFF)[0]


def prod_kind(prod):
    """The four-byte tag of what a PROD builds, or None."""
    if prod is None or len(prod) < 8 + PROD_LIST_OFF + 4:
        return None
    at = 8 + PROD_KIND_OFF + _prod_list_len(prod)
    if len(prod) < at + 4:
        return None
    return bytes(prod[at:at + 4])


def prod_queue(prod):
    """The production-queue link in a PROD: the flag at +2, the queue's object
    id at +3 and the u16 at +7. All zero on a planet in no queue."""
    if prod is None or len(prod) < 8 + PROD_LIST_OFF:
        return None
    return bytes(prod[8 + PROD_QUEUE_OFF:8 + PROD_LIST_OFF])


def queue_link(prod):
    """(flag, queue object id, entry id) of a PROD, or None.

    The entry id names the queue entry the planet is building, 0 after a
    manual override."""
    link = prod_queue(prod)
    return None if link is None else struct.unpack('<BIH', link)


def with_prod_queue(prod, oid):
    """A `PROD` section linked to a different queue object id."""
    out = bytearray(prod)
    struct.pack_into('<I', out, 8 + PROD_QUEUE_OFF + 1, int(oid))
    return bytes(out)


def prod_facility(prod):
    """The facility type a PROD builds, or None when it builds no facility."""
    if prod_kind(prod) != b'FCLT':
        return None
    return struct.unpack_from('<I', prod, 8 + PROD_FACILITY_OFF
                              + _prod_list_len(prod))[0]


def facility_refusal(blob, completed, plpr, facility_type):
    """Why a facility may not be started on a planet, or '' when it may."""
    if plpr is None:
        return 'the planet has no PLPR'
    ok, why = facility_rules.can_build(blob, completed, plpr,
                                       facilities_of(plpr) or [], facility_type)
    return '' if ok else why


def prod_builds(prod):
    """The design a queue entry names, or None if it names none.

    A `PROD` payload ends with a nested section saying what is queued: `FCLT`
    with an empty payload for a facility, `WLTH` for nothing, and `SHIP` with a
    four-byte payload holding the design's object id.
    """
    if prod is None or len(prod) < 8 + 33:
        return None
    body = prod[8:]
    if body[21:25] != b'SHIP':
        return None
    size = struct.unpack_from('<I', body, 25)[0] & sp.SIZE_MASK
    if size < 4 or len(body) < 33:
        return None
    return struct.unpack_from('<I', body, 29)[0]


def military_of(plpr):
    """(offset of the count, [records]) for a planet's stationed military.

    Soldiers are the same nine-byte record as citizens, in a second array that
    begins where the citizen array ends: a `u32` count, then that many records,
    then the rest of the planet's fields.
    """
    if plpr is None or len(plpr) < POP_ARRAY_OFF + 4:
        return None, None
    pop = struct.unpack_from('<I', plpr, POP_COUNT_OFF)[0]
    at = POP_ARRAY_OFF + pop * POP_RECORD
    if at + 4 > len(plpr):
        return None, None
    n = struct.unpack_from('<I', plpr, at)[0]
    end = at + 4 + n * POP_RECORD
    if end > len(plpr):
        return None, None
    return at, [bytes(plpr[at + 4 + i * POP_RECORD:
                           at + 4 + (i + 1) * POP_RECORD]) for i in range(n)]


def crew_of(shpr):
    """[records] for a ship's crew, or None.

    `SHPR+4` is the count and the records follow at `+8`.
    """
    if shpr is None or len(shpr) < CREW_ARRAY_OFF:
        return None
    n = struct.unpack_from('<I', shpr, CREW_COUNT_OFF)[0]
    end = CREW_ARRAY_OFF + n * POP_RECORD
    if end > len(shpr):
        return None
    return [bytes(shpr[CREW_ARRAY_OFF + i * POP_RECORD:
                       CREW_ARRAY_OFF + (i + 1) * POP_RECORD]) for i in range(n)]


def set_military(blob, planet_oid, records):
    """Write a planet's stationed military. The section's size changes."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            raw = bytes(blob[plpr.payload:plpr.end])
            at, old = military_of(raw)
            if at is None:
                raise SystemExit(f'planet {planet_oid} has no readable military')
            tail = raw[at + 4 + len(old) * POP_RECORD:]
            body = (raw[:at] + struct.pack('<I', len(records))
                    + b''.join(records) + tail)
            return sp.replace_payload(blob, tree, plpr, body)
    raise SystemExit(f'planet {planet_oid} not found')


def _shpr_section(blob, glxy, ship_oid):
    """A ship's SHPR section, in its SHIP or in the fleet it is in, or None.
    Raises when its SHIP has none."""
    for sec in glxy.children:
        if (sec.tag == b'SHIP'
                and struct.unpack_from('<I', blob, sec.payload)[0] == ship_oid):
            shpr = next(sec.find('SHPR'), None)
            if shpr is None:
                raise SystemExit(f'ship {ship_oid} has no SHPR')
            return shpr
        if sec.tag == b'FLET':
            for shpr in sec.find('SHPR'):
                ids = shpr_ids(bytes(blob[shpr.payload:shpr.end]))
                if ids is not None and ids[1] == ship_oid:
                    return shpr
    return None


def set_crew(blob, ship_oid, records):
    """Write a ship's crew, in a fleet or not. The section's size changes."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    shpr = _shpr_section(blob, glxy, ship_oid)
    if shpr is not None:
        raw = bytes(blob[shpr.payload:shpr.end])
        old = crew_of(raw)
        if old is None:
            raise SystemExit(f'ship {ship_oid} has no readable crew')
        tail = raw[CREW_ARRAY_OFF + len(old) * POP_RECORD:]
        body = (raw[:CREW_COUNT_OFF] + struct.pack('<I', len(records))
                + raw[CREW_COUNT_OFF + 4:CREW_ARRAY_OFF]
                + b''.join(records) + tail)
        return sp.replace_payload(blob, tree, shpr, body)
    raise SystemExit(f'ship {ship_oid} not found')


def recruit_of(plpr):
    """A planet's military recruitment rate as a percentage, or None."""
    if plpr is None or len(plpr) <= RECRUIT_OFF:
        return None
    return plpr[RECRUIT_OFF]


def food_of(plpr):
    """A planet's (food store, food capacity), or None.

    The pair the client renders as `store/capacity`.
    """
    if plpr is None or len(plpr) < FOOD_CAP_OFF + 2:
        return None
    return (struct.unpack_from('<H', plpr, FOOD_STORE_OFF)[0],
            struct.unpack_from('<H', plpr, FOOD_CAP_OFF)[0])


def set_food(blob, planet_oid, store, capacity):
    """Write a planet's food pair. Size does not change."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            out = bytearray(blob)
            struct.pack_into('<H', out, plpr.payload + FOOD_STORE_OFF,
                             int(store) & 0xFFFF)
            struct.pack_into('<H', out, plpr.payload + FOOD_CAP_OFF,
                             int(capacity) & 0xFFFF)
            return bytes(out)
    raise SystemExit(f'planet {planet_oid} not found')


def _planet_plpr(blob, planet_oid):
    """(tree, PLPR section) for one planet, or raise naming what is missing."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            return tree, plpr
    raise SystemExit(f'planet {planet_oid} not found')


def _facility_at(plpr):
    """Offset of the facility count within a PLPR payload, or None."""
    at, mil = military_of(plpr)
    if at is None:
        return None
    c = at + 4 + len(mil) * POP_RECORD + FACILITY_AFTER_MILITARY
    if c + 4 > len(plpr):
        return None
    n = struct.unpack_from('<I', plpr, c)[0]
    if c + 4 + n * FACILITY_RECORD > len(plpr):
        return None
    return c


def facilities_of(plpr):
    """A planet's facilities as [(type, count)], or None."""
    if plpr is None:
        return None
    c = _facility_at(plpr)
    if c is None:
        return None
    n = struct.unpack_from('<I', plpr, c)[0]
    return [struct.unpack_from('<II', plpr, c + 4 + i * FACILITY_RECORD)
            for i in range(n)]


def set_facilities(blob, planet_oid, pairs):
    """Write a planet's facility list. Size changes with the pair count."""
    tree, plpr = _planet_plpr(blob, planet_oid)
    raw = bytes(blob[plpr.payload:plpr.end])
    c = _facility_at(raw)
    if c is None:
        raise SystemExit(f'planet {planet_oid} has no readable facility list')
    n = struct.unpack_from('<I', raw, c)[0]
    body = (raw[:c] + struct.pack('<I', len(pairs))
            + b''.join(struct.pack('<II', t, k) for t, k in pairs)
            + raw[c + 4 + n * FACILITY_RECORD:])
    return sp.replace_payload(blob, tree, plpr, body)


def _settled_at(blob, plpr):
    """Blob offset of a planet's settled block, or None when it has none."""
    prod = next(plpr.find('PROD'), None)
    enli = next(plpr.find('ENLI'), None)
    if prod is None or enli is None or enli.start - prod.end != SETTLED_LEN:
        return None
    return prod.end


def settled_of(blob, planet_oid):
    """The settled block's fields for one planet, or None when it has none."""
    _tree, plpr = _planet_plpr(blob, planet_oid)
    at = _settled_at(blob, plpr)
    if at is None:
        return None
    u16 = lambda off: struct.unpack_from('<H', blob, at + off)[0]
    u32 = lambda off: struct.unpack_from('<I', blob, at + off)[0]
    return {
        'pop': u16(SETTLED_POP_OFF),
        'history': [u16(SETTLED_HISTORY_OFF + 2 * i)
                    for i in range(SETTLED_HISTORY_LEN)],
        'counter': u32(SETTLED_COUNTER_OFF),
        'owner': u32(SETTLED_OWNER_OFF),
        'age': struct.unpack_from('<f', blob, at + SETTLED_AGE_OFF)[0],
        'founded': u32(SETTLED_FOUNDED_OFF),
        'founded_head': struct.unpack_from('<I', blob,
                                           plpr.payload + FOUNDED_OFF)[0],
        'last': u32(SETTLED_LAST_OFF),
    }


def set_settled(blob, planet_oid, pop=None, history=None, counter=None,
                age=None, founded=None, last=None):
    """Write the named settled-block fields. Size does not change.

    `founded` is written in both places a planet carries it. The owner field
    is not writable here: it is the civ's object id and `add_civ` sets it with
    every other owner reference.
    """
    _tree, plpr = _planet_plpr(blob, planet_oid)
    at = _settled_at(blob, plpr)
    if at is None:
        raise SystemExit(f'planet {planet_oid} has no settled block')
    out = bytearray(blob)
    if pop is not None:
        struct.pack_into('<H', out, at + SETTLED_POP_OFF, int(pop) & 0xFFFF)
    if history is not None:
        if len(history) != SETTLED_HISTORY_LEN:
            raise ValueError(f'history holds {SETTLED_HISTORY_LEN} turns')
        for i, v in enumerate(history):
            struct.pack_into('<H', out, at + SETTLED_HISTORY_OFF + 2 * i,
                             int(v) & 0xFFFF)
    if counter is not None:
        struct.pack_into('<I', out, at + SETTLED_COUNTER_OFF, int(counter))
    if age is not None:
        struct.pack_into('<f', out, at + SETTLED_AGE_OFF, float(age))
    if founded is not None:
        struct.pack_into('<I', out, at + SETTLED_FOUNDED_OFF, int(founded))
        struct.pack_into('<I', out, plpr.payload + FOUNDED_OFF, int(founded))
    if last is not None:
        struct.pack_into('<I', out, at + SETTLED_LAST_OFF, int(last))
    return bytes(out)


def set_recruit(blob, planet_oid, value):
    """Write a planet's recruitment rate. Size does not change."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            out = bytearray(blob)
            out[plpr.payload + RECRUIT_OFF] = int(value) & 0xFF
            return bytes(out)
    raise SystemExit(f'planet {planet_oid} not found')


def is_hurried(prod):
    """Has the current item been paid off? None when there is no PROD."""
    if prod is None or len(prod) <= PROD_HURRIED_OFF:
        return None
    return bool(prod[PROD_HURRIED_OFF])


def credits_of(blob, civ_oid):
    """A civ's credits, or None when the civ is not in this blob."""
    rec = next((o for o in icv.owner_records(blob) if o['oid'] == civ_oid), None)
    if rec is None:
        return None
    at = rec['id_at'] + CREDITS_AFTER_ID
    if at + 4 > len(blob):
        return None
    return struct.unpack_from('<I', blob, at)[0]


def set_credits(blob, civ_oid, value):
    """Write a civ's credits. Size does not change."""
    rec = next((o for o in icv.owner_records(blob) if o['oid'] == civ_oid), None)
    if rec is None:
        raise SystemExit(f'civ {civ_oid} is not in this blob')
    out = bytearray(blob)
    struct.pack_into('<I', out, rec['id_at'] + CREDITS_AFTER_ID,
                     int(value))
    return bytes(out)


def set_progress(blob, planet_oid, value):
    """Write a planet's accumulated production points. Size does not change."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            out = bytearray(blob)
            struct.pack_into('<I', out, plpr.payload + PROGRESS_OFF, int(value))
            return bytes(out)
    raise SystemExit(f'planet {planet_oid} not found')


def hurry_acceptable(served_plpr, submitted_plpr, credits):
    """(ok, why, cost, points) for a claimed hurry.

    The manual's rule: the points bought are how far the submission advanced
    the progress field, at four credits each. Refused when the item was less
    than half finished or the civ was not served enough credits. Hurrying
    completes the item, so the submitted progress is the item's total.
    """
    before, after = progress_of(served_plpr), progress_of(submitted_plpr)
    if before is None or after is None:
        return False, 'the progress field is unreadable', 0, 0
    points = after - before
    if points <= 0:
        return False, f'no progress was bought ({before} -> {after})', 0, 0
    if before * 2 < after:
        return (False, f'only {before} of {after} points were done, and the '
                f'engine requires half', 0, points)
    cost = points * HURRY_PER_POINT
    if credits is None:
        return False, 'the civ has no readable credit field', cost, points
    if credits < cost:
        return (False, f'{cost} credits needed and {credits} served', cost,
                points)
    return True, '', cost, points


def set_citizens(blob, planet_oid, new_array):
    """Write a planet's citizen array in place. Its length does not change."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            at = plpr.payload + POP_ARRAY_OFF
            out = bytearray(blob)
            out[at:at + len(new_array)] = new_array
            return bytes(out)
    raise SystemExit(f'planet {planet_oid} not found')


def set_people(blob, planet_oid, citizens: bytes, military):
    """Write both of a planet's people arrays. Sizes change.

    The arrays are adjacent and each is length-prefixed, so both are written
    together.
    """
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            raw = bytes(blob[plpr.payload:plpr.end])
            at, old = military_of(raw)
            if at is None:
                raise SystemExit(f'planet {planet_oid} has no readable military')
            tail = raw[at + 4 + len(old) * POP_RECORD:]
            body = (raw[:POP_COUNT_OFF]
                    + struct.pack('<I', len(citizens) // POP_RECORD) + citizens
                    + struct.pack('<I', len(military)) + b''.join(military)
                    + tail)
            return sp.replace_payload(blob, tree, plpr, body)
    raise SystemExit(f'planet {planet_oid} not found')


def set_research(blob, civ_oid, values):
    """Write a civ's research fields in place. Sizes do not change."""
    owpr = _owpr(blob, civ_oid)
    if owpr is None:
        raise SystemExit(f'civ {civ_oid} has no readable OWPR')
    out = bytearray(blob)
    for off, value in zip(research_offsets(blob, owpr), values):
        out[owpr.payload + off:owpr.payload + off + RESEARCH_FIELD_LEN] = value
    return bytes(out)


def _setup_flag_offsets(blob, owpr):
    """Absolute offsets of the (homeworld, civ) setup flags in this OWPR."""
    second = owpr.payload + research_offsets(blob, owpr)[-1]
    return second + HOMEWORLD_FLAG_OFF, second + CIV_FLAG_OFF


def setup_flags(blob, civ_oid):
    """(homeworld, civ) setup-prompt flags of a civ, or None when unreadable."""
    owpr = _owpr(blob, civ_oid)
    if owpr is None:
        return None
    hw, cv = _setup_flag_offsets(blob, owpr)
    if cv >= owpr.end:
        return None
    return blob[hw], blob[cv]


def set_setup_flag(blob, civ_oid, which, value=1):
    """Write one setup flag, 'homeworld' or 'civ'. Sizes do not change."""
    owpr = _owpr(blob, civ_oid)
    if owpr is None:
        raise SystemExit(f'civ {civ_oid} has no readable OWPR')
    hw, cv = _setup_flag_offsets(blob, owpr)
    out = bytearray(blob)
    out[hw if which == 'homeworld' else cv] = value
    return bytes(out)


def _cvtr(blob, civ_oid):
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for o in glxy.children:
        if o.tag != b'OWNR':
            continue
        n = struct.unpack_from('<I', blob, o.payload)[0]
        if struct.unpack_from('<I', blob, o.payload + 4 + n)[0] == civ_oid:
            return next(o.find('CVTR'), None)
    return None


def traits_of(blob, civ_oid):
    """A civ's trait values in index order, or None unless CVTR holds exactly
    the dialog's traits, each once and in order."""
    cvtr = _cvtr(blob, civ_oid)
    if cvtr is None or cvtr.size < CVTR_PAIRS_OFF:
        return None
    count = struct.unpack_from('<I', blob, cvtr.payload + CVTR_COUNT_OFF)[0]
    if (count != len(TRAIT_RULES)
            or cvtr.size != CVTR_PAIRS_OFF + count * CVTR_PAIR):
        return None
    pairs = [struct.unpack_from('<Ii', blob, cvtr.payload + CVTR_PAIRS_OFF
                                + i * CVTR_PAIR) for i in range(count)]
    if [index for index, _v in pairs] != list(range(count)):
        return None
    return tuple(v for _i, v in pairs)


def set_traits(blob, civ_oid, values):
    """Write a civ's trait values in place. Sizes do not change."""
    cvtr = _cvtr(blob, civ_oid)
    if cvtr is None:
        raise SystemExit(f'civ {civ_oid} has no CVTR')
    out = bytearray(blob)
    for i, v in enumerate(values):
        struct.pack_into('<i', out, cvtr.payload + CVTR_PAIRS_OFF
                         + i * CVTR_PAIR + 4, v)
    return bytes(out)


def traits_acceptable(values, budget):
    """(ok, why) for trait values a civ-trait confirm submitted.

    Each value must be one the dialog gives, and the clicks behind them must
    spend the galaxy's allowance exactly, since OK refuses while any is left.
    Values overwrite rather than add, so the served values do not matter.
    """
    spent = 0
    for (name, fewest, table, cost), v in zip(TRAIT_RULES, values):
        if v not in table:
            return False, f'{name} {v:+d} is not a value the dialog gives'
        spent += (fewest + table.index(v)) * cost
    if spent != budget:
        return False, (f'the traits spend {spent} change(s), and the galaxy '
                       f'allows exactly {budget}')
    return True, ''


def gset_value(blob, name):
    """A galaxy setting's value, or None."""
    gset = next((s for s in sp.flatten(sp.parse_blob(blob))
                 if s.tag == b'GSET'), None)
    if gset is None:
        return None
    return sp.parse_gset(blob, gset).get(name, {}).get('value')


def homeworld_fields(plpr):
    """(rates, space) of a PLPR payload: the fields the homeworld dialog edits."""
    rates = tuple(struct.unpack_from('<b', plpr, off)[0]
                  for off in HOMEWORLD_RATE_OFFS)
    return rates, struct.unpack_from('<H', plpr, HOMEWORLD_SPACE_OFF)[0]


def homeworlds(planets, civ_oid):
    """The planets the client could take for a civ's homeworld.

    0x00508C60 takes the civ's planet founded earliest, from PLPR+0. A tie is
    broken by an order the blob does not show, so every planet tied for the
    earliest is a candidate.
    """
    founded = {oid: struct.unpack_from('<I', plpr, FOUNDED_OFF)[0]
               for oid, (owner, _p, plpr, _n) in planets.items()
               if owner == civ_oid and plpr is not None}
    if not founded:
        return set()
    first = min(founded.values())
    return {oid for oid, turn in founded.items() if turn == first}


def homeworld_acceptable(served_plpr, submitted_plpr, budget):
    """(ok, why) for a homeworld confirm, from the planet as served."""
    (rates_s, space_s) = homeworld_fields(served_plpr)
    (rates_t, space_t) = homeworld_fields(submitted_plpr)
    lo, hi = HOMEWORLD_RATES
    clicks = 0
    for kind, before, after in zip(('food', 'production', 'science'),
                                   rates_s, rates_t):
        if after != before and not lo <= after <= hi:
            return False, f'{kind} rate {after} is outside {lo}..{hi}'
        clicks += after - before
    added = space_t - max(space_s, HOMEWORLD_SPACE_FLOOR)
    if (added % HOMEWORLD_SPACE_STEP
            or not 0 <= added // HOMEWORLD_SPACE_STEP <= HOMEWORLD_SPACE_CLICKS):
        return False, f'space {space_s} -> {space_t} is not a click count'
    clicks += added // HOMEWORLD_SPACE_STEP
    if clicks != budget:
        return False, (f'the homeworld spends {clicks} change(s), and the '
                       f'galaxy allows exactly {budget}')
    return True, ''


def set_homeworld(blob, planet_oid, submitted_plpr):
    """Copy the homeworld dialog's fields from a submitted PLPR. In place."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            out = bytearray(blob)
            for off in HOMEWORLD_RATE_OFFS + (HOMEWORLD_SPACE_OFF,
                                              HOMEWORLD_SPACE_OFF + 1):
                out[plpr.payload + off] = submitted_plpr[off]
            return bytes(out)
    raise SystemExit(f'planet {planet_oid} not found')


def setup_orders(served, sub, civ_oid, served_planets, sub_planets):
    """([(kind, detail)], [refusal]) for the setup prompts' confirms.

    Each prompt is one-time: its confirm is carried when the flag was clear as
    served and is set in the submission, and only as the dialog could have
    made it. kind is 'civ' with the trait values, or 'homeworld' with
    (planet, submitted PLPR). Anything else that moves those fields is refused.
    """
    taken, refusals = [], []
    flags_s, flags_t = setup_flags(served, civ_oid), setup_flags(sub, civ_oid)
    if flags_s is None or flags_t is None:
        return taken, refusals

    traits_s, traits_t = traits_of(served, civ_oid), traits_of(sub, civ_oid)
    confirmed = not flags_s[1] and flags_t[1]
    if traits_t is None and confirmed:
        refusals.append('civilization traits DROPPED, CVTR is not the '
                        "dialog's ten traits")
    elif traits_t is not None and (confirmed or traits_t != traits_s):
        if flags_s[1]:
            refusals.append('civilization traits DROPPED, they were already '
                            'customised')
        elif not confirmed:
            refusals.append('civilization traits DROPPED, changed without the '
                            "prompt's confirm")
        else:
            ok, why = traits_acceptable(traits_t, gset_value(
                served, 'civilization_changes'))
            if ok:
                taken.append(('civ', traits_t))
            else:
                refusals.append(f'civilization traits DROPPED, {why}')

    moved = {oid for oid, (owner, _p, plpr, _n) in served_planets.items()
             if owner == civ_oid and plpr is not None
             and oid in sub_planets and sub_planets[oid][2] is not None
             and homeworld_fields(plpr) != homeworld_fields(sub_planets[oid][2])}
    confirmed = not flags_s[0] and flags_t[0]
    if moved or confirmed:
        if flags_s[0]:
            refusals.append('homeworld customisation DROPPED, it was already '
                            'customised')
        elif not confirmed:
            refusals.append('homeworld customisation DROPPED, changed without '
                            "the prompt's confirm")
        elif len(moved) != 1:
            refusals.append(f'homeworld customisation DROPPED, it changes '
                            f'{len(moved)} planets, not one')
        else:
            (oid,) = moved
            if oid not in homeworlds(served_planets, civ_oid):
                refusals.append(f'homeworld customisation DROPPED, planet '
                                f'{oid} is not the homeworld')
            else:
                ok, why = homeworld_acceptable(
                    served_planets[oid][2], sub_planets[oid][2],
                    gset_value(served, 'homeworld_changes'))
                if ok:
                    taken.append(('homeworld', (oid, sub_planets[oid][2])))
                else:
                    refusals.append(f'homeworld customisation DROPPED, {why}')
    return taken, refusals


def _civ_section(blob, civ_oid, tag):
    """(tree, the civ's first section with this tag), None when it has none."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for o in glxy.children:
        if o.tag != b'OWNR':
            continue
        n = struct.unpack_from('<I', blob, o.payload)[0]
        if struct.unpack_from('<I', blob, o.payload + 4 + n)[0] == civ_oid:
            return tree, next(o.find(tag), None)
    return tree, None


def _govs(blob, civ_oid):
    """(tree, the civ's GOVS section), the section None when it has none."""
    return _civ_section(blob, civ_oid, 'GOVS')


def _sections(raw, at, end):
    """[(tag, payload start, payload end)] of the sections filling raw[at:end]
    exactly, or None."""
    out = []
    while at < end:
        if end - at < 8:
            return None
        size = struct.unpack_from('<I', raw, at + 4)[0] & sp.SIZE_MASK
        if at + 8 + size > end:
            return None
        out.append((bytes(raw[at:at + 4]), at + 8, at + 8 + size))
        at += 8 + size
    return out


def governors_of(blob, civ_oid):
    """[(objectId, whole GVNR section)] of a civ in creation order, or None
    unless its GOVS holds exactly its count of GVNR sections."""
    _tree, govs = _govs(blob, civ_oid)
    if govs is None or govs.size < GOVS_COUNT:
        return None
    count = struct.unpack_from('<I', blob, govs.payload)[0]
    secs = _sections(blob, govs.payload + GOVS_COUNT, govs.end)
    if (secs is None or len(secs) != count
            or any(tag != b'GVNR' or end - at < GVNR_FIXED
                   for tag, at, end in secs)):
        return None
    return [(struct.unpack_from('<I', blob, at)[0], bytes(blob[at - 8:end]))
            for _tag, at, end in secs]


def governor_ids(blob):
    """Every governor's object id, whichever civ holds it."""
    ids = set()
    for o in icv.owner_records(blob):
        ids.update(oid for oid, _r in governors_of(blob, o['oid']) or [])
    return ids


def read_governor(record):
    """(name, why) for a whole GVNR section: the name as bytes and '', or None
    and what is wrong with it. The rules are judged only as sections that fit;
    the engine runs them."""
    body = _sections(record, 8 + GVNR_FIXED, len(record))
    if not body or len(body) != 1 or body[0][0] != b'GVDT':
        return None, 'its record is not one GVDT'
    _tag, at, end = body[0]
    if end - at < 4:
        return None, 'its GVDT is cut short'
    n = struct.unpack_from('<I', record, at)[0]
    rules_at = at + 4 + n + GVDT_AFTER_NAME
    if rules_at + 4 > end:
        return None, 'its name runs past its GVDT'
    name = bytes(record[at + 4:at + 4 + n])
    rules = struct.unpack_from('<I', record, rules_at)[0]
    secs = _sections(record, rules_at + 4, end)
    if secs is None or len(secs) != 2 * rules:
        return None, f'its GVDT does not hold the {rules} rule(s) it counts'
    for (flag, f_at, f_end), (rule, _a, _e) in zip(secs[::2], secs[1::2]):
        if flag != b'RULE' or f_end - f_at != RULE_FLAG or not rule.startswith(b'RL'):
            return None, 'a rule is not a RULE followed by its own section'
    if not rules:
        return None, 'it has no rules, which the client does not allow'
    if not name:
        return None, 'the name is empty'
    ok, why = name_acceptable(name)
    return (name, '') if ok else (None, why)


def with_governor_id(record, oid):
    """A GVNR section carrying a different object id."""
    out = bytearray(record)
    struct.pack_into('<I', out, 8, int(oid))
    return bytes(out)


def with_governed(record, count):
    """A GVNR section saying it governs `count` planets."""
    out = bytearray(record)
    struct.pack_into('<I', out, 8 + GVNR_GOVERNS_AT, int(count))
    return bytes(out)


def set_governors(blob, civ_oid, records):
    """Rewrite a civ's GOVS to hold these whole GVNR sections, in order."""
    tree, govs = _govs(blob, civ_oid)
    if govs is None:
        raise SystemExit(f'civ {civ_oid} has no GOVS')
    payload = struct.pack('<I', len(records)) + b''.join(records)
    return sp.replace_payload(blob, tree, govs, payload)


def governor_links(blob):
    """{planetObjectId: governorObjectId}, 0 for a planet with none."""
    return {struct.unpack_from('<I', blob, s.payload)[0]:
            struct.unpack_from('<I', blob, s.end - GOVERNOR_LINK)[0]
            for s in sp.flatten(sp.parse_blob(blob)) if s.tag == b'PLNT'}


def set_governor_link(blob, planet_oid, gov_oid):
    """Name a planet's governor. Sizes do not change."""
    for s in sp.flatten(sp.parse_blob(blob)):
        if (s.tag == b'PLNT'
                and struct.unpack_from('<I', blob, s.payload)[0] == planet_oid):
            out = bytearray(blob)
            struct.pack_into('<I', out, s.end - GOVERNOR_LINK, int(gov_oid))
            return bytes(out)
    raise SystemExit(f'planet {planet_oid} not found')


def governor_orders(served, sub, civ_oid, served_planets, names):
    """(new, edited, deleted, links, refusals) for a civ's governors, judged
    against the state served.

    new and edited are [(objectId, GVNR)], new ones under the id submitted;
    deleted is a list of ids; links is [(planet, governor)]. The number of
    planets each governor governs is the referee's to count, so it is ignored
    here. Deleting a governor that would still govern a planet, and
    unassigning one, are not something the client was seen to do, and are
    refused.
    """
    refusals = []
    for o in icv.owner_records(served):
        if (o['oid'] != civ_oid
                and governors_of(sub, o['oid']) != governors_of(served, o['oid'])):
            refusals.append(f"governors DROPPED, they belong to {o['name']}")
    mine_s, mine_t = governors_of(served, civ_oid), governors_of(sub, civ_oid)
    if mine_s is None or mine_t is None:
        if mine_t != mine_s:
            refusals.append('governors DROPPED, GOVS does not hold its count '
                            'of governors')
        return [], [], [], [], refusals
    held, kept = dict(mine_s), dict(mine_t)
    high = struct.unpack_from('<I', served, idg.HIGH_WATER_ID)[0]

    new, edited = [], []
    for oid, record in mine_t:
        if oid in held and with_governed(record, 0) == with_governed(held[oid], 0):
            continue
        if oid not in held and oid <= high:
            refusals.append(f'governor {oid} DROPPED, it is new and {oid} is '
                            f'not a new object id')
            continue
        name, why = read_governor(record)
        if name is None:
            refusals.append(f'governor {oid} DROPPED, {why}')
            continue
        (edited if oid in held else new).append((oid, record))

    usable = set(held) | {oid for oid, _r in new}
    links_s, links_t = governor_links(served), governor_links(sub)
    links = []
    for planet, gov in sorted(links_t.items()):
        was = links_s.get(planet)
        if was is None or gov == was:
            continue
        owner = served_planets[planet][0] if planet in served_planets else None
        if owner != civ_oid:
            refusals.append(f'planet {planet}: governor DROPPED, owned by '
                            f'{names.get(owner, owner)}')
        elif gov == 0:
            refusals.append(f'planet {planet}: governor DROPPED, unassigning '
                            f'one is not something the client offers')
        elif gov not in usable:
            refusals.append(f'planet {planet}: governor DROPPED, {gov} is not '
                            f'a governor this civ holds')
        else:
            links.append((planet, gov))

    final = dict(links_s)
    final.update(links)
    deleted = []
    for oid, record in mine_s:
        if oid in kept:
            continue
        governs = sorted(p for p, g in final.items() if g == oid)
        if governs:
            refusals.append(f'governor {oid} deletion DROPPED, it still '
                            f'governs planet(s) {governs}')
        else:
            deleted.append(oid)
    return new, edited, deleted, links, refusals


# A civ's admirals are in its `OWNR > DATA > ADMS`, after `GOVS`: a u32 count,
# then one `ADML` per admiral. Read off the client's writer (0x53B4A0, 0x5A51B0,
# 0x5A4F20) and reader (0x53D73C, 0x5A6FF0); no save on hand holds one:
#
#     ADML v0   u32 objectId ; u32 ships assigned ; one byte ; ADDT
#     ADDT v0   u32 nameLen ; name ; u32, likely the type ; u32 rule count ;
#               per rule a RULE (one byte, its checkbox) then the rule's own
#               section (ATPL, DFPL, RLYP, TMSC ... 24 tags)
#
# A ship or fleet names its admiral in its DYNO, the u32 after the has-orders
# byte. Admirals are not carried until they are measured in the UI.
ADMS_COUNT = 4
ADML_SHIPS_AT = 4           # in the ADML payload, after the object id
ADML_FIXED = 9              # object id, ships assigned and the one byte
DYNO_ORBIT = 4              # the orbit planet's id, before SHCO
DYNO_AFTER_SHCO = 5         # a u32 and the has-orders byte, then the admiral


def admirals_of(blob, civ_oid):
    """[(objectId, whole ADML section)] of a civ in order, or None unless its
    ADMS holds exactly its count of ADML sections."""
    _tree, adms = _civ_section(blob, civ_oid, 'ADMS')
    if adms is None or adms.size < ADMS_COUNT:
        return None
    count = struct.unpack_from('<I', blob, adms.payload)[0]
    secs = _sections(blob, adms.payload + ADMS_COUNT, adms.end)
    if (secs is None or len(secs) != count
            or any(tag != b'ADML' or end - at < ADML_FIXED
                   for tag, at, end in secs)):
        return None
    return [(struct.unpack_from('<I', blob, at)[0], bytes(blob[at - 8:end]))
            for _tag, at, end in secs]


def admiral_name(record):
    """The name in a whole ADML section, or None when its ADDT cannot be read."""
    body = _sections(record, 8 + ADML_FIXED, len(record))
    if not body or body[0][0] != b'ADDT' or body[0][2] - body[0][1] < 4:
        return None
    _tag, at, end = body[0]
    n = struct.unpack_from('<I', record, at)[0]
    return bytes(record[at + 4:at + 4 + n]) if at + 4 + n <= end else None


def _with_ships(record, count):
    out = bytearray(record)
    struct.pack_into('<I', out, 8 + ADML_SHIPS_AT, int(count))
    return bytes(out)


def admiral_refusals(served, sub, civ_oid, names):
    """[refusal] for every admiral a submission creates, edits or deletes, its
    own or another civ's. None of it is carried."""
    refusals = []
    for o in icv.owner_records(served):
        was, now = admirals_of(served, o['oid']), admirals_of(sub, o['oid'])
        if was == now:
            continue
        if o['oid'] != civ_oid:
            refusals.append(f"admirals DROPPED, they belong to {o['name']}")
            continue
        if was is None or now is None:
            refusals.append('admirals DROPPED, ADMS does not hold its count of '
                            'admirals')
            continue
        held, kept = dict(was), dict(now)

        def label(oid, record):
            name = admiral_name(record)
            return f'admiral {oid}' + (f" ({name.decode('latin1')!r})"
                                       if name is not None else '')
        for oid, record in now:
            if oid not in held:
                refusals.append(f'{label(oid, record)} DROPPED, creating an '
                                f'admiral is not carried yet')
            elif _with_ships(record, 0) != _with_ships(held[oid], 0):
                refusals.append(f'{label(oid, record)} DROPPED, editing an '
                                f'admiral is not carried yet')
        for oid, record in was:
            if oid not in kept:
                refusals.append(f'{label(oid, record)} deletion DROPPED, '
                                f'deleting an admiral is not carried yet')
    return refusals


def dyno_admiral_at(dyno):
    """Where a whole DYNO section holds its admiral id, or None."""
    at = 8 + DYNO_ORBIT
    if len(dyno) < at + 8 or dyno[at:at + 4] != b'SHCO':
        return None
    at += 8 + (struct.unpack_from('<I', dyno, at + 4)[0] & sp.SIZE_MASK)
    at += DYNO_AFTER_SHCO
    return at if at + 4 <= len(dyno) else None


def dyno_admiral(dyno):
    """The admiral id a whole DYNO section names, 0 for none, or None."""
    at = dyno_admiral_at(dyno) if dyno is not None else None
    return struct.unpack_from('<I', dyno, at)[0] if at is not None else None


def admiral_kept(dyno, allowed):
    """(dyno, refusal): a submitted DYNO naming one of the `allowed` admiral
    ids, the first of them the one served, or the same DYNO naming that one
    and the refusal of the change. Admiral assignments are not carried."""
    now = dyno_admiral(dyno)
    if now is None or now in allowed:
        return dyno, ''
    out = bytearray(dyno)
    struct.pack_into('<I', out, dyno_admiral_at(dyno), int(allowed[0]))
    if now == 0:
        why = f'unassigning admiral {allowed[0]} DROPPED'
    else:
        why = f'admiral {now} assignment DROPPED'
    return bytes(out), why + ', admirals are not carried yet'


# Treaties, proposals among them, follow the last section in SAVE: a u32
# count, then one TRTY per treaty. The client writes every treaty it holds
# (0x55A4D0, reached from SaveGame with no player) and reads them back into its
# registry (0x55BD00). Read off the writer and reader; no save on hand holds
# one:
#
#     TRTY v2   u32 objectId ; TRDT
#     TRDT v1   u32 first party ; u32 second party ; u32 kind (1 war) ;
#               u32 ; i32 ; u32 ; u32 turn applied ; two bytes ;
#               u32 count, items ; u32 count, items
#
# Each item is its own section: CRLS, CRPT, RSLS, RSPT, SLOS, SHBR, SHGM, SHWH,
# TRLE. A civ's stance toward each civ it has met is in its `KNPL`: a u32
# count, then a 36-byte record per civ, keyed by that civ's object id, the
# stance at +12 (3 in a fork at peace, 1 in the same fork after a declaration
# of war). Diplomacy is not carried until it is measured in the UI.
TREATY_COUNT = 4
TRTY_PARTIES_AT = 4 + 8     # in the TRTY payload, past the id and TRDT's header
KNPL_COUNT = 4
KNPL_RECORD = 36
KNPL_STANCE_AT = 12
STANCES = {0: 'Neutral', 1: 'War', 2: 'Cease-Fire', 3: 'Peace', 4: 'Alliance'}


def treaties(blob):
    """{objectId: whole TRTY section} after SAVE's last section, or None
    unless a u32 count of TRTY sections fills that tail exactly."""
    save = sp.parse_blob(blob)[0]
    at = save.children[-1].end if save.children else save.payload
    if save.end - at < TREATY_COUNT:
        return None
    count = struct.unpack_from('<I', blob, at)[0]
    secs = _sections(blob, at + TREATY_COUNT, save.end)
    if (secs is None or len(secs) != count
            or any(tag != b'TRTY' or end - p < TRTY_PARTIES_AT + 12
                   for tag, p, end in secs)):
        return None
    return {struct.unpack_from('<I', blob, p)[0]: bytes(blob[p - 8:end])
            for _tag, p, end in secs}


def treaty_parties(record):
    """(first party, second party, kind) of a whole TRTY section."""
    return struct.unpack_from('<III', record, 8 + TRTY_PARTIES_AT)


def stances(blob):
    """{(civ, other civ): stance code} for every civ whose KNPL reads as a
    count of whole records."""
    out = {}
    for o in icv.owner_records(blob):
        _tree, knpl = _civ_section(blob, o['oid'], 'KNPL')
        if knpl is None or knpl.size < KNPL_COUNT:
            continue
        count = struct.unpack_from('<I', blob, knpl.payload)[0]
        if knpl.size != KNPL_COUNT + count * KNPL_RECORD:
            continue
        for i in range(count):
            at = knpl.payload + KNPL_COUNT + i * KNPL_RECORD
            other, = struct.unpack_from('<I', blob, at)
            out[(o['oid'], other)] = struct.unpack_from(
                '<I', blob, at + KNPL_STANCE_AT)[0]
    return out


def diplomacy_refusals(served, sub, civ_oid, names):
    """[refusal] for every treaty a submission adds, changes or removes, and
    every stance it moves. None of it is carried."""
    refusals, named = [], set()

    def who(civ):
        return names.get(civ, civ)

    was, now = treaties(served), treaties(sub)
    if was != now:
        if was is None or now is None:
            refusals.append('diplomacy DROPPED, the treaty list does not hold '
                            'its count of treaties')
        else:
            for oid in sorted(set(was) | set(now)):
                if was.get(oid) == now.get(oid):
                    continue
                first, second, kind = treaty_parties(now.get(oid) or was[oid])
                named.add(frozenset((first, second)))
                what = ('proposing a treaty or declaring war' if oid not in was
                        else 'withdrawing or deleting a treaty' if oid not in now
                        else 'answering or changing a treaty')
                refusals.append(f'treaty {oid} ({who(first)} and '
                                f'{who(second)}, kind {kind}) DROPPED, {what} '
                                f'is not carried yet')
    s, t = stances(served), stances(sub)
    for civ, other in sorted((k for k in set(s) & set(t) if s[k] != t[k]),
                             key=lambda k: (k[0] != civ_oid, k)):
        pair = frozenset((civ, other))
        if pair in named:
            continue
        named.add(pair)
        refusals.append(f'stance of {who(civ)} toward {who(other)} DROPPED, '
                        f'{STANCES.get(s[(civ, other)], s[(civ, other)])} -> '
                        f'{STANCES.get(t[(civ, other)], t[(civ, other)])}; '
                        f'diplomacy is not carried yet')
    return refusals


# A civ's production queues are in its `OWNR > DATA > SPQS`: a u32 count, then
# one `SPRQ` per queue:
#
#     SPRQ v0   u32 objectId ; u32 planets using it ; one byte ; PRQU
#     PRQU v1   u16 entry-id counter ; u32 nameLen ; name ; u32 entry count ;
#               the entries, in the order the UI shows them
#
# An entry is a u16 entry id, a u16 count, a u8 infinite flag, a u8 count mode
# (1 "build up to") and a u32 item type, then a u32 naming the item for the
# types the reader follows with one: the facility type for 0, the design's
# object id for 8, the scan for 9. The writer is 0x556560 and the reader
# 0x556D20.
#
# `SPQS` is found by its tag in `DATA` and believed when it holds its count of
# `SPRQ` sections.
SPQS_COUNT = 4
SPRQ_USING_AT = 4           # in the SPRQ payload, after the object id
SPRQ_FIXED = 9              # object id, planets using it and the one byte
PRQU_VERSION = 1
QUEUE_ENTRY = 10            # entry id, count, two flags and the item type
ITEM_FACILITY = 0
ITEM_SHIP = 8
ITEM_SCAN = 9
SCAN_KINDS = 6                  # the scanning screen's six; route is 2, fleet 3
ITEM_ARGUMENT = {0, 7, 8, 9}    # item types followed by a u32
ITEM_TYPES = 10                 # the reader's switch covers 0..9


def _spqs(blob):
    """(tree, {civObjectId: (DATA, SPQS start, SPQS end) or None}) for every
    civ. A civ's entry is None unless its DATA holds exactly one SPQS holding
    its count of SPRQ sections."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for o in glxy.children:
        if o.tag != b'OWNR':
            continue
        n = struct.unpack_from('<I', blob, o.payload)[0]
        civ = struct.unpack_from('<I', blob, o.payload + 4 + n)[0]
        data = next((c for c in o.children if c.tag == b'DATA'), None)
        found = []
        at = blob.find(b'SPQS', data.payload, data.end - 8) if data else -1
        while at >= 0:
            end = (at + 8 + (struct.unpack_from('<I', blob, at + 4)[0]
                             & sp.SIZE_MASK))
            if at + 8 + SPQS_COUNT <= end <= data.end:
                count = struct.unpack_from('<I', blob, at + 8)[0]
                secs = _sections(blob, at + 8 + SPQS_COUNT, end)
                if (secs is not None and len(secs) == count
                        and all(tag == b'SPRQ' and e - p >= SPRQ_FIXED
                                for tag, p, e in secs)):
                    found.append((at, end))
            at = blob.find(b'SPQS', at + 1, data.end - 8)
        out[civ] = (data,) + found[0] if len(found) == 1 else None
    return tree, out


def queues_by_civ(blob):
    """{civObjectId: [(objectId, whole SPRQ section)] in order, or None when
    the civ's SPQS cannot be found}."""
    out = {}
    for civ, found in _spqs(blob)[1].items():
        if found is None:
            out[civ] = None
            continue
        _data, at, end = found
        secs = _sections(blob, at + 8 + SPQS_COUNT, end)
        out[civ] = [(struct.unpack_from('<I', blob, p)[0],
                     bytes(blob[p - 8:e])) for _tag, p, e in secs]
    return out


def queues_of(blob, civ_oid):
    """[(objectId, whole SPRQ section)] of a civ in order, or None when its
    SPQS cannot be found."""
    return queues_by_civ(blob).get(civ_oid)


def queue_ids(blob):
    """Every production queue's object id, whichever civ holds it."""
    return {oid for queues in queues_by_civ(blob).values()
            for oid, _r in queues or ()}


def set_queues(blob, civ_oid, records):
    """Rewrite a civ's SPQS to hold these whole SPRQ sections, in order."""
    tree, civs = _spqs(blob)
    if civs.get(civ_oid) is None:
        raise ValueError(f'civ {civ_oid} has no readable SPQS')
    data, at, end = civs[civ_oid]
    version = struct.unpack_from('<I', blob, at + 4)[0] >> sp.VERSION_SHIFT
    spqs = sp.build_section(b'SPQS', version, struct.pack('<I', len(records))
                            + b''.join(records))
    body = bytes(blob[data.payload:at]) + spqs + bytes(blob[end:data.end])
    return sp.replace_payload(blob, tree, data, body)


def read_queue(record):
    """([(entry id, item type, item, item offset)], why) for a whole SPRQ
    section: the entries and '', or None and what is wrong with it. The item
    and its offset are None for an item type with nothing after it."""
    body = _sections(record, 8 + SPRQ_FIXED, len(record))
    if not body or len(body) != 1 or body[0][0] != b'PRQU':
        return None, 'its record is not one PRQU'
    _tag, at, end = body[0]
    version = struct.unpack_from('<I', record, at - 4)[0] >> sp.VERSION_SHIFT
    if version != PRQU_VERSION:
        return None, f'its PRQU is version {version}, not {PRQU_VERSION}'
    if end - at < 6:
        return None, 'its PRQU is cut short'
    name_end = at + 6 + struct.unpack_from('<I', record, at + 2)[0]
    if name_end + 4 > end:
        return None, 'its name runs past its PRQU'
    ok, why = name_acceptable(bytes(record[at + 6:name_end]))
    if not ok:
        return None, why
    count = struct.unpack_from('<I', record, name_end)[0]
    entries, p = [], name_end + 4
    for _i in range(count):
        if p + QUEUE_ENTRY > end:
            break
        eid = struct.unpack_from('<H', record, p)[0]
        itype = struct.unpack_from('<I', record, p + 6)[0]
        p += QUEUE_ENTRY
        if itype >= ITEM_TYPES:
            return None, (f'entry {eid} has item type {itype}, which the '
                          f'engine does not read')
        item = item_at = None
        if itype in ITEM_ARGUMENT:
            if p + 4 > end:
                break
            item, item_at = struct.unpack_from('<I', record, p)[0], p
            p += 4
        entries.append((eid, itype, item, item_at))
    if len(entries) != count or p != end:
        return None, f'its PRQU does not hold the {count} entries it counts'
    ids = [e[0] for e in entries]
    if 0 in ids or len(set(ids)) != len(ids):
        return None, 'its entry ids are not distinct and non-zero'
    return entries, ''


def with_queue_id(record, oid):
    """An SPRQ section carrying a different object id."""
    out = bytearray(record)
    struct.pack_into('<I', out, 8, int(oid))
    return bytes(out)


def with_using(record, count):
    """An SPRQ section saying `count` planets use it."""
    out = bytearray(record)
    struct.pack_into('<I', out, 8 + SPRQ_USING_AT, int(count))
    return bytes(out)


def with_queue_designs(record, remap):
    """An SPRQ section whose ship entries follow designs renumbered in
    `remap`."""
    entries, _why = read_queue(record)
    out = bytearray(record)
    for _eid, itype, item, at in entries or ():
        if itype == ITEM_SHIP and item in remap:
            struct.pack_into('<I', out, at, int(remap[item]))
    return bytes(out)


def queue_refusal(record, designs, facility_types):
    """Why a queue may not be carried, or '' when it may.

    `designs` are the design ids the civ holds in the merged galaxy. The
    client's queue editor writes only facilities, ships and scans, so any
    other item type is refused.
    """
    entries, why = read_queue(record)
    if entries is None:
        return why
    for eid, itype, item, _at in entries:
        if itype == ITEM_FACILITY:
            if item >= facility_types:
                return (f'entry {eid} names facility type {item}, and the '
                        f'galaxy has {facility_types}')
        elif itype == ITEM_SHIP:
            if item not in designs:
                return (f'entry {eid} builds design {item}, which the civ '
                        f'does not own in the merged galaxy')
        elif itype == ITEM_SCAN:
            if item >= SCAN_KINDS:
                return (f'entry {eid} names scan {item}, and the scanning '
                        f'screen has {SCAN_KINDS}')
        else:
            return (f'entry {eid} has item type {itype}; the client queues '
                    f'only facilities, ships and scans')
    return ''


def queue_links(blob):
    """{planetObjectId: (flag, queue object id, entry id)} for every planet
    with a readable PROD."""
    out = {}
    for oid, (_owner, prod, _plpr, _name) in planet_index(blob).items():
        link = queue_link(prod)
        if link is not None:
            out[oid] = link
    return out


def link_refusal(prod, prod_served, queues):
    """Why a planet's queue link may not be carried, or '' when it may.

    `queues` is {queue object id: entry ids} for the queues the civ holds in
    the merged galaxy. Leaving a queue clears the flag.
    """
    link = queue_link(prod)
    if link == queue_link(prod_served):
        return ''
    flag, qid, entry = link
    if flag == 0:
        return ''
    if flag != 1:
        return f'its queue link flag reads {flag}'
    if qid not in queues:
        return (f'it names production queue {qid}, which is not one of the '
                f"civ's queues in the merged galaxy")
    if entry and entry not in queues[qid]:
        return (f'it is on entry {entry} of production queue {qid}, which '
                f'that queue does not hold')
    return ''


def queue_orders(served, sub, civ_oid, designs, remap, facility_types):
    """(new, edited, deleted, refusals) for a civ's production queues, judged
    against the state served.

    new and edited are [(objectId, SPRQ)], new ones under the id submitted,
    their ship entries following `remap`, the designs renumbered in this
    merge. deleted is a list of ids; the caller deletes one only once no
    planet uses it. The planet counts are the referee's to recount, so they
    are ignored here.
    """
    refusals = []
    all_s, all_t = queues_by_civ(served), queues_by_civ(sub)
    for o in icv.owner_records(served):
        if o['oid'] != civ_oid and all_t.get(o['oid']) != all_s.get(o['oid']):
            refusals.append(f"production queues DROPPED, they belong to "
                            f"{o['name']}")
    mine_s, mine_t = all_s.get(civ_oid), all_t.get(civ_oid)
    if mine_s is None or mine_t is None:
        if mine_t != mine_s:
            refusals.append('production queues DROPPED, SPQS does not hold '
                            'its count of queues')
        return [], [], [], refusals
    if len({oid for oid, _r in mine_t}) != len(mine_t):
        refusals.append('production queues DROPPED, two of them share an '
                        'object id')
        return [], [], [], refusals
    held, kept = dict(mine_s), dict(mine_t)
    high = struct.unpack_from('<I', served, idg.HIGH_WATER_ID)[0]

    new, edited = [], []
    for oid, record in mine_t:
        if oid in held and with_using(record, 0) == with_using(held[oid], 0):
            continue
        if oid not in held and oid <= high:
            refusals.append(f'production queue {oid} DROPPED, it is new and '
                            f'{oid} is not a new object id')
            continue
        record = with_queue_designs(record, remap)
        why = queue_refusal(record, designs, facility_types)
        if why:
            refusals.append(f'production queue {oid} DROPPED, {why}')
            continue
        (edited if oid in held else new).append((oid, record))
    deleted = [oid for oid, _r in mine_s if oid not in kept]
    return new, edited, deleted, refusals


def _tally(jobs):
    """"6 farmer, 2 worker" from a list of job ids, for a log line."""
    import collections
    c = collections.Counter(jobs)
    return ', '.join(f'{n} {JOB_IDS.get(j, j)}' for j, n in sorted(c.items()))


def _ship_crew_index(blob):
    """{shipObjectId: (owner, SHPR payload bytes)}, a fleet's ships under the
    fleet's owner."""
    out = {}
    for o in space_objects(blob)[2]:
        if o['tag'] == b'SHIP':
            out[o['oid']] = (o['owner'], o['shpr'])
        elif not o['why']:
            for m, shpr, _v in o['members']:
                out[m] = (o['owner'], shpr)
    return out


def merge(blob, submissions, log=print, notes=None):
    """submissions: [(civ_name, submitted_blob)]. Returns the merged blob.

    `notes` is an optional `{civ: [reason]}` that every refusal is recorded in
    as well as logged. `referee.py` writes it beside the turn so
    `player_turn.follow` can print it into the launcher's log.
    """
    served_ships = ship_index(blob)
    served_members = {m: f['owner'] for f in fleet_index(blob).values()
                      for m, _s, _v in f['members']}
    served_planets = planet_index(blob)
    served_crews = _ship_crew_index(blob)
    served_designs = design_index(blob)
    # Research as it was served, like the three indexes above: every
    # submission is compared against the state served, not the merged blob.
    served_research = {o['oid']: research_of(blob, o['oid'])
                       for o in icv.owner_records(blob)}
    served_completed = {o['oid']: completed_research(blob, o['oid']) or []
                        for o in icv.owner_records(blob)}
    names = {o['oid']: o['name'] for o in icv.owner_records(blob)}
    served = blob
    accepted = dropped = 0

    for civ_name, sub in submissions:
        def drop(reason, _civ=civ_name):
            """Refuse one change: log it and record it in `notes`."""
            nonlocal dropped
            dropped += 1
            log(f"    {reason}")
            if notes is not None:
                notes.setdefault(_civ, []).append(reason)

        # A submission that raises while being read is dropped whole and the
        # blob is put back to what it was before this civ, so one bad
        # submission cannot end the merge. `SystemExit` is not caught:
        # `civ_by_name` raises it for a civ the galaxy does not hold, a caller
        # error that `referee.screen_submission` refuses earlier.
        before, kept = blob, accepted
        try:
            civ = civ_by_name(blob, civ_name)
            mine = civ['oid']
            log(f"{civ_name} (object {mine}):")

            # Ship designs, before the production queues that may name them.
            # Judged against `served_designs`, the state as it was served.
            remap = {}
            new_designs, toggled, refusals = design_orders(
                served_designs, design_index(sub), mine)
            for why in refusals:
                drop(why)
            for oid, inactive in toggled:
                blob = set_design_inactive(blob, oid, inactive)
                log(f"    design {oid}: "
                    f"{'deactivated' if inactive else 'reactivated'}")
                accepted += 1
            for was, d in new_designs:
                oid = was
                if oid <= idg.max_object_id(blob):
                    # Another player's new object already took this id, so
                    # this one is renumbered. The only check made against the
                    # merged blob: an id is free or taken in the galaxy being
                    # built.
                    oid = next_object_id(blob)
                    remap[was] = oid
                blob = set_high_water(blob, oid)
                blob = add_design(blob, mine, with_design_id(d['record'], oid))
                log(f"    design {oid}: {d['name']!r} taken"
                    + (f", submitted as {was} and renumbered"
                       if oid != was else ""))
                accepted += 1
            mine_designs = designs_of(blob, mine)

            # Production queues, after the designs their entries may name and
            # before the planets that join them. A new one takes a free id as
            # a governor does; a deleted one goes after the planets have moved.
            new_queues, edited_queues, deleted_queues, refusals = queue_orders(
                served, sub, mine, mine_designs, remap,
                facility_rules.type_count(blob))
            for why in refusals:
                drop(why)
            queue_remap = {}
            if new_queues or edited_queues:
                records = dict(queues_of(blob, mine))
                for oid, record in edited_queues:
                    records[oid] = record
                    log(f"    production queue {oid}: edited")
                    accepted += 1
                for was, record in new_queues:
                    oid = was
                    if oid <= idg.max_object_id(blob):
                        # Taken in the galaxy being built, by another
                        # player's design, governor or queue.
                        oid = next_object_id(blob)
                        queue_remap[was] = oid
                    blob = set_high_water(blob, oid)
                    records[oid] = with_queue_id(record, oid)
                    log(f"    production queue {oid}: taken"
                        + (f", submitted as {was} and renumbered"
                           if oid != was else ""))
                    accepted += 1
                blob = set_queues(blob, mine, list(records.values()))
            usable = {oid: {e[0] for e in read_queue(r)[0] or ()}
                      for oid, r in queues_of(blob, mine) or ()
                      if oid not in deleted_queues}
            # Fleets, against the state served, before the ship orders and
            # crews that look for ships where the fleets leave them. A new
            # fleet takes a free id like a design.
            layout, refusals = fleet_orders(served, sub, mine, names)
            for why in refusals:
                drop(why)
            if layout is not None:
                renumbered = {}
                for was in sorted(f for f, v in layout['fleets'].items()
                                  if v['new']):
                    oid = was
                    if oid <= idg.max_object_id(blob):
                        oid = next_object_id(blob)
                        renumbered[was] = oid
                    blob = set_high_water(blob, oid)
                blob = set_fleets(blob, mine, layout, renumbered)
                for line in layout['log']:
                    log(f"    {line}")
                    accepted += 1
                for was, oid in renumbered.items():
                    log(f"    fleet {oid}: submitted as {was} and renumbered")

            # ship orders
            for ship_oid, (_claimed, dyno) in sorted(ship_index(sub).items()):
                if ship_oid not in served_ships:
                    if ship_oid in served_members:
                        # Out of a fleet: the fleet step judged it.
                        owner = served_members[ship_oid]
                        if owner != mine:
                            drop(f"ship {ship_oid}: DROPPED, owned by "
                                 f"{names.get(owner, owner)}")
                        continue
                    drop(f"ship {ship_oid}: DROPPED, not in the state served")
                    continue
                owner, as_served = served_ships[ship_oid]
                if dyno == as_served:
                    continue
                if owner != mine:
                    drop(f"ship {ship_oid}: DROPPED, owned by "
                         f"{names.get(owner, owner)}")
                    continue
                dyno, why = admiral_kept(dyno, (dyno_admiral(as_served) or 0,))
                if why:
                    drop(f"ship {ship_oid}: {why}")
                if dyno == as_served:
                    continue
                blob = replace_dyno(blob, ship_oid, dyno)
                log(f"    ship {ship_oid}: order taken "
                    f"({len(as_served)} -> {len(dyno)} bytes)")
                accepted += 1

            # production queues and job allocation
            for planet_oid, (_claimed, prod, plpr, name) in sorted(
                    planet_index(sub).items()):
                if planet_oid not in served_planets:
                    drop(f"planet {planet_oid}: DROPPED, not in the state "
                         f"served")
                    continue
                owner, prod_served, plpr_served, name_served = \
                    served_planets[planet_oid]

                # A queue naming a renumbered design follows the new id.
                if prod is not None and prod_builds(prod) in remap:
                    prod = with_prod_design(prod, remap[prod_builds(prod)])
                # A planet joining a renumbered production queue follows it.
                link = queue_link(prod)
                if link and link[0] == 1 and link[1] in queue_remap:
                    prod = with_prod_queue(prod, queue_remap[link[1]])

                if name is None and name_served is not None:
                    # planet_name returns None for a corrupt or overlong name.
                    drop(f"planet {planet_oid}: rename DROPPED, the name "
                         f"field is unreadable or longer than {MAX_NAME} "
                         f"bytes")
                elif (name is not None and name_served is not None
                      and name != name_served):
                    if owner != mine:
                        drop(f"planet {planet_oid}: rename DROPPED, owned by "
                             f"{names.get(owner, owner)}")
                    else:
                        ok, why = name_acceptable(name[1])
                        if not ok:
                            drop(f"planet {planet_oid}: rename DROPPED, {why}")
                        else:
                            blob = set_planet_name(blob, planet_oid, name[1])
                            log(f"    planet {planet_oid}: renamed "
                                f"{name_served[1].decode('latin1')!r} -> "
                                f"{name[1].decode('latin1')!r}")
                            accepted += 1

                if prod != prod_served:
                    if owner != mine:
                        drop(f"planet {planet_oid}: production DROPPED, "
                             f"owned by {names.get(owner, owner)}")
                    elif prod is None or prod_served is None:
                        drop(f"planet {planet_oid}: production DROPPED, no "
                             f"PROD section on one side")
                    elif is_hurried(prod_served):
                        # Paid off and finishing at the tick; the UI does not
                        # let it be swapped.
                        drop(f"planet {planet_oid}: production DROPPED, the "
                             f"item being built was hurried and cannot be "
                             f"changed")
                    elif prod_kind(prod) not in PROD_KINDS:
                        drop(f"planet {planet_oid}: production DROPPED, it "
                             f"builds {prod_kind(prod)!r}, which is not a "
                             f"facility, a ship, a scan or wealth")
                    elif why := link_refusal(prod, prod_served, usable):
                        # A planet naming a queue the merged galaxy lacks
                        # would point at nothing.
                        drop(f"planet {planet_oid}: production DROPPED, "
                             f"{why}")
                    elif (prod_facility(prod) is not None
                          and prod_facility(prod) != prod_facility(prod_served)
                          and (why := facility_refusal(
                              blob, served_completed.get(mine, []),
                              plpr_served, prod_facility(prod)))):
                        drop(f"planet {planet_oid}: production DROPPED, "
                             f"facility {prod_facility(prod)} cannot be built: "
                             f"{why}")
                    elif (prod_builds(prod) is not None
                          and prod_builds(prod) not in mine_designs):
                        # A queue naming a design the galaxy does not hold makes
                        # the engine refuse the whole galaxy. Checked against
                        # the submitter's designs in the merged galaxy, the
                        # file about to be published.
                        drop(f"planet {planet_oid}: production DROPPED, it "
                             f"builds design {prod_builds(prod)}, which "
                             f"{civ_name} does not own in the merged galaxy")
                    elif is_hurried(prod) and not is_hurried(prod_served):
                        # The submission carries a hurry's outcome (item
                        # complete, credits spent). The referee prices it from
                        # the served state and deducts the credits itself.
                        ok, why, cost, points = hurry_acceptable(
                            plpr_served, plpr, credits_of(blob, mine))
                        if not ok:
                            drop(f"planet {planet_oid}: hurry DROPPED, {why}")
                        else:
                            blob = replace_prod(blob, planet_oid, prod)
                            blob = set_progress(blob, planet_oid,
                                                progress_of(plpr))
                            blob = set_credits(blob, mine,
                                               credits_of(blob, mine) - cost)
                            log(f"    planet {planet_oid}: production "
                                f"hurried, {points} point(s) for {cost} "
                                f"credits")
                            accepted += 1
                    else:
                        blob = replace_prod(blob, planet_oid, prod)
                        log(f"    planet {planet_oid}: production taken")
                        accepted += 1

                if plpr is None or plpr_served is None:
                    continue
                rate, rate_served = recruit_of(plpr), recruit_of(plpr_served)
                if rate is not None and rate != rate_served:
                    if owner != mine:
                        drop(f"planet {planet_oid}: recruitment rate DROPPED, "
                             f"owned by {names.get(owner, owner)}")
                    elif not 0 <= rate <= 100:
                        drop(f"planet {planet_oid}: recruitment rate DROPPED, "
                             f"{rate} is not a percentage")
                    else:
                        blob = set_recruit(blob, planet_oid, rate)
                        log(f"    planet {planet_oid}: recruitment rate "
                            f"{rate_served}% -> {rate}%")
                        accepted += 1

            # A queue is deleted only once no planet uses it as merged, and
            # each counts the planets that do.
            merged_queues = queues_of(blob, mine)
            if merged_queues:
                links = queue_links(blob)
                using = collections.Counter(
                    qid for flag, qid, _e in links.values() if flag == 1)
                records = []
                for oid, record in merged_queues:
                    if oid in deleted_queues:
                        users = sorted(p for p, (flag, qid, _e)
                                       in links.items()
                                       if flag == 1 and qid == oid)
                        if users:
                            drop(f"production queue {oid} deletion DROPPED, "
                                 f"planet(s) {users} still use it")
                        else:
                            log(f"    production queue {oid}: deleted")
                            accepted += 1
                            continue
                    records.append(with_using(record, using[oid]))
                if records != [r for _oid, r in merged_queues]:
                    blob = set_queues(blob, mine, records)

            # Everybody this civ holds, judged together (see people_acceptable).
            ok, why = people_acceptable(blob, sub, mine)
            planets_before, ships_before = population(blob, mine)
            planets_after, ships_after = population(sub, mine)
            if planets_after is None:
                pass                   # unreadable; people_acceptable said so
            elif not ok:
                drop(f"people DROPPED, {why}")
            else:
                for oid in sorted(planets_after):
                    cz_b, mil_b = planets_before[oid]
                    cz_a, mil_a = planets_after[oid]
                    if (cz_b, mil_b) == (cz_a, mil_a):
                        continue
                    blob = set_people(blob, oid, citizen_bytes(
                        planet_index(sub)[oid][2]), mil_a)
                    what = []
                    if [c[0] for c in cz_b] != [c[0] for c in cz_a]:
                        what.append(f'{_tally([c[0] for c in cz_b])} -> '
                                    f'{_tally([c[0] for c in cz_a])}')
                    if len(mil_b) != len(mil_a):
                        what.append(f'{len(mil_b)} -> {len(mil_a)} stationed')
                    log(f"    planet {oid}: "
                        f"{'; '.join(what) or 'people moved'}")
                    accepted += 1
                for oid in sorted(ships_after):
                    if ships_before[oid] == ships_after[oid]:
                        continue
                    blob = set_crew(blob, oid, ships_after[oid])
                    log(f"    ship {oid}: crew {len(ships_before[oid])} -> "
                        f"{len(ships_after[oid])}")
                    accepted += 1
            # People on planets and ships other civs own, against the state
            # served: never copied, always named.
            for why in foreign_people_refusals(served_planets, served_crews,
                                               sub, mine, names):
                drop(why)

            # system names, which belong to whoever holds most of the system
            served_systems = systems(blob)
            for sun_oid, (_o, _t, name) in sorted(systems(sub).items()):
                if sun_oid not in served_systems:
                    drop(f"system {sun_oid}: DROPPED, not in the state served")
                    continue
                owners, total, name_served = served_systems[sun_oid]
                if name_served is None:
                    continue
                if name is None:
                    drop(f"system {sun_oid}: rename DROPPED, the name field "
                         f"is unreadable or longer than {MAX_NAME} bytes")
                    continue
                if name == name_served:
                    continue
                if name[1] == DEFAULT_SUN_NAME:
                    # Never a rename: a running client writes `Unnamed` into
                    # every SUN the blob left empty, and the default must not
                    # erase a name a player set. Skipped without a note.
                    continue
                # The majority is of the settled planets only, as the client
                # allows: the only settled planet in a system may name it.
                held = owners.get(mine, 0)
                settled = sum(owners.values())
                if held * 2 <= settled:
                    drop(f"system {sun_oid}: rename DROPPED, {civ_name} "
                         f"holds {held} of the {settled} settled planet(s) in "
                         f"a system of {total}, not a majority")
                    continue
                ok, why = name_acceptable(name[1])
                if not ok:
                    drop(f"system {sun_oid}: rename DROPPED, {why}")
                    continue
                blob = set_system_name(blob, sun_oid, name[1])
                log(f"    system {sun_oid}: renamed "
                    f"{name_served[1].decode('latin1')!r} -> "
                    f"{name[1].decode('latin1')!r}")
                accepted += 1

            # research topic
            theirs = research_of(sub, mine)
            topic_served = served_research.get(mine)
            if (theirs is not None and topic_served is not None
                    and theirs != topic_served):
                blob = set_research(blob, mine, theirs)
                topic = struct.unpack_from('<I', theirs[0], 0)[0]
                log(f"    research: topic taken (id {topic})")
                accepted += 1

            # The one-time setup prompts' confirms, against the state served.
            taken, refusals = setup_orders(served, sub, mine, served_planets,
                                           planet_index(sub))
            for why in refusals:
                drop(why)
            for kind, detail in taken:
                if kind == 'civ':
                    blob = set_traits(blob, mine, detail)
                    log(f"    civilization traits taken: {list(detail)}")
                else:
                    oid, plpr = detail
                    blob = set_homeworld(blob, oid, plpr)
                    rates, space = homeworld_fields(plpr)
                    log(f"    homeworld {oid} customised: rates {rates}, "
                        f"space {space}")
                blob = set_setup_flag(blob, mine, kind)
                accepted += 1

            # Governors, against the state served. A new one takes a free id,
            # and the planets-governed counts follow the links as merged.
            new_govs, edited, deleted, links, refusals = governor_orders(
                served, sub, mine, served_planets, names)
            for why in refusals:
                drop(why)
            if new_govs or edited or deleted or links:
                records = dict(governors_of(blob, mine))
                renumbered = {}
                for oid, record in edited:
                    records[oid] = record
                    log(f"    governor {oid}: "
                        f"{read_governor(record)[0].decode('latin1')!r} edited")
                    accepted += 1
                for was, record in new_govs:
                    oid = was
                    if oid <= idg.max_object_id(blob):
                        # Taken in the galaxy being built, by another player's
                        # design, governor or production queue.
                        oid = next_object_id(blob)
                        renumbered[was] = oid
                    blob = set_high_water(blob, oid)
                    records[oid] = with_governor_id(record, oid)
                    log(f"    governor {oid}: "
                        f"{read_governor(record)[0].decode('latin1')!r} taken"
                        + (f", submitted as {was} and renumbered"
                           if oid != was else ""))
                    accepted += 1
                for planet_oid, gov in links:
                    gov = renumbered.get(gov, gov)
                    blob = set_governor_link(blob, planet_oid, gov)
                    log(f"    planet {planet_oid}: governor {gov} assigned")
                    accepted += 1
                for oid in deleted:
                    del records[oid]
                    log(f"    governor {oid}: deleted")
                    accepted += 1
                governs = collections.Counter(governor_links(blob).values())
                blob = set_governors(blob, mine, [
                    with_governed(r, governs[oid]) for oid, r in records.items()])

            # Admirals and diplomacy are refused, named, until they are
            # measured in the UI. Nothing is taken from either.
            for why in admiral_refusals(served, sub, mine, names):
                drop(why)
            for why in diplomacy_refusals(served, sub, mine, names):
                drop(why)

            # Research belonging to anyone else, against the state as served.
            for other in (o for o in icv.owner_records(blob)
                          if o['oid'] != mine):
                a = research_of(sub, other['oid'])
                b = served_research.get(other['oid'])
                if a is not None and b is not None and a != b:
                    drop(f"research: DROPPED, belongs to {other['name']}")
        except Exception as exc:                                # noqa: BLE001
            blob, accepted = before, kept
            drop(f"submission DROPPED, it could not be read, "
                 f"{type(exc).__name__}: {exc}; nothing from it was taken")

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
        for oid, (owner, prod, plpr, nm) in sorted(planet_index(blob).items()):
            if not owner:
                continue
            cz = citizens_of(plpr) if plpr else None
            label = nm[1].decode('latin1') if nm and nm[0] else '(unnamed)'
            print(f"  planet {oid:<5} owner {names.get(owner, owner):<14} "
                  f"{label!r:<20} PROD {len(prod) if prod else 'none'} bytes, "
                  f"{_tally([c[0] for c in cz]) if cz else 'no population'}")
            if cz and any(c[1] != owner for c in cz):
                # Each citizen in array order: job, owner, per-citizen value.
                print(' ' * 9 + ', '.join(
                    f"{JOB_IDS.get(j, j)} {names.get(o, o)} {v}"
                    for j, o, v, _r in cz))
        for oid, f in sorted(fleet_index(blob).items()):
            label = f['why'] or (f"{f['name'].decode('latin1')!r}, ships "
                                 f"{[m for m, _s, _v in f['members']]}")
            print(f"  fleet  {oid:<5} owner {names.get(f['owner'], f['owner']):<14} "
                  f"{label}")
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
