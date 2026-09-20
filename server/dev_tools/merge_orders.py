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

**Ship designs**, `OWNR > DATA > DSGN`. A player who designs a ship returns an
`OWNR` carrying a second `DSGN`/`SDPR` pair with the existing one untouched, the
civ's design count raised by one and `SAVE+0` raised to the new object id. The
subtree is copied whole, because it is self-contained and everything in it is
the design.

The hard part is the object id, not the bytes. **The client allocates a new
object id as one past the object count**, so two players who design a ship on
the same turn both arrive claiming the same number, and merging both would put
two different objects into one galaxy under one id. Whoever is merged second is
renumbered and every reference to the id they submitted is rewritten with them,
which today means the id inside the `DSGN` payload and the production queue that
names it.

This interacts with the queue, which is why it had to be fixed rather than
listed. A queue building a design the galaxy does not hold is a dangling
reference and the engine refuses the **whole galaxy** rather than the order: the
client starts, reads it and exits in about four seconds, which stalled a live
turn for both players. The guard that refuses such a queue stays; it is the
backstop, and carrying designs is what stops it firing.

Editing a design and deleting one are refused and named. Neither has been
measured, and a design with a ship already built from it is a reference nobody
has followed.

**Four `OWPR` bytes move in the same submission and none of them is carried.**
`+49`, `+77`, `+78`, `+100` and `+110` were surveyed across all 18 turns of the
rehearsal: the first three also move on turns where no design was created, and
`+100` is a counter the engine advances by 7 every tick on its own. The
decisive measurement is that the same player made the same design three times,
on turns 5, 6 and 7, identical parts and only the name differing, and the
fields disagreed across the three submissions. A value derived from the design
would have been the same all three times. What they are is not established and
nothing here needs it.

**People**, the citizen array in `PLNT > PLPR`, the stationed military array
that follows it, and a ship's crew in `SHPR`. Judged as one thing, because a
player can move somebody between them: conscription turns a citizen into a
soldier and crewing a ship moves that soldier onto it, and in the first
two-machine rehearsal a turn that did both was refused twice over and lost, by
two rules that were each correct about their own half.

A submission never ticks, so within one turn the total number of people a civ
holds cannot change. That is the check, along with: no soldier's existing
record may be altered, nobody changes hands, no per-citizen value is invented,
and the civ may not come back with fewer soldiers than it was served, which is
what retiring from service looks like and is not carried yet.

The total is a claim about a **submission**, not about the galaxy. The engine's
own recruitment makes soldiers out of food without costing a citizen, measured
at 60%: the population held at nine across the turn a soldier appeared. A rule
that assumed the total was invariant across a tick would refuse every turn
after the first.

The layout, which is what the rule is written against. `PLPR+36` is a `u32`
population count and `PLPR+40` begins that many nine-byte records:

    +0  u8   job id, 0 farmer, 1 worker, 2 scientist, 5 miner, 6 banker
    +3  u32  the owning civ's object id
    +7  u8   a per-citizen value, permuted by a reassignment rather than changed

Confirmed by having a player move one farmer to a banker: the array went from
seven farmers, two workers and a scientist to six farmers, two workers, a
scientist and a banker, matching the live population vector at `Planet:144`
exactly. The array is kept sorted by job, so a reassignment reorders it rather
than editing one byte in place, which is why the diff looked like values
shifting along.

Only the two arrays are copied, never the rest of `PLPR`, which carries the
planet's stores and derived economy.

**What the per-citizen owner id is for is not established.** It equals the
planet's owner on every naturally created planet measured. One colony in the
test galaxy held citizens carrying another civ's id, which looked like evidence
that a population can be shared between empires; it is not. That colony was
founded by a ship `inject_ship.py` had cloned, and the clone kept the donor
civ's id in two places the tool did not rewrite. The planet's owner owns the
working population. The field may exist for mid-tick bookkeeping, ownership of
soldiers during a battle being the obvious candidate, but that is a guess and
nothing here depends on it.

The checks still compare per-owner job multisets, because whatever the field
means, a turn in which a citizen changes hands is not a job reassignment.

What is refused, and why
------------------------
[ ] **Citizens a player owns on someone else's planet cannot be managed.**
Only planets the submitter owns are considered, so a player with population on a
rival's colony cannot reassign it. Correct behaviour is probably to allow it,
since the per-citizen owner already says whose it is, but it has not been
measured and letting one player write into another's `PLPR` deserves more care
than a guess.

**Where the soldiers are.** They are the same nine-byte record with job id 3,
in a second array starting where the citizen array ends: a `u32` count at
`PLPR+40+9*citizens`, then that many records. A ship keeps its crew the same
way, counted at `SHPR+4` with the records at `SHPR+8`.

Measured by a player dismissing a colony ship's two crew: the ship's payload
went 38 bytes to 20 and its count 2 to 0, the planet's grew by the same 18 bytes
with its count 19 to 21, and **the two records arrived byte for byte**. Moving
somebody does not rewrite them, which is why an existing soldier's record
appearing changed is refused.

**Military recruitment rate**, `PLPR+27`, a percentage in one byte. A setting
rather than an immediate effect: the manual says it diverts food into military
growth each turn. Measured by a player moving the slider from 0 to 50.

**System renaming**, the `SUN ` section's name, at the same `+24` offset in its
own payload. Measured by having a player rename the system they hold five of six
planets in: the name appeared on the `SUN `, and the renamer's own `EXSY` cache
picked it up while the other civ's did not. A system belongs to nobody, so the
right to rename it is the game's own rule, **owning more than half of its
settled planets**, read from the served state. Unowned planets do not count
toward the denominator: one settled planet in an otherwise empty system is a
majority of one, and the client lets its owner name the system.

**Planet renaming**, `PLNT` own payload `+24`: a `u32` length then the
characters. Measured by having a player rename a colony, which changed that
field and nothing else on the object. The game gates it, refusing with "you
need to own the majority of the planet to rename", so a name is authoritative
galaxy data rather than a private label, and it is carried for planets the
submitter owns.

`EXSY` is **not** where a rename is authored, though it holds names. Each civ's
`EXSY` is that civ's cache of the names it has seen: one player's table gained
`Neighbor's HQ` purely from loading a turn, with no action by that player. It
is knowledge the referee recomputes, so it stays excluded.

**Everything else**: facility selection outside the queue, governors, admirals,
diplomacy proposals. Not measured, so not accepted. An order type nobody has
measured is not a gap in a list, it is a change of unknown extent being copied
between players.

**Every refusal is recorded against the civ that made it**, in the optional
`notes` argument to `merge`, as well as logged. The log reaches whoever is
running the referee, which on a player's machine is nobody: a system rename and
a conscription were both dropped in the first two-machine rehearsal and both
players saw nothing but an order that had not happened. `referee.py` writes
what lands in `notes` beside the turn and `player_turn.follow` prints it.

[ ] **Nothing here checks that an order is legal**, only that it is the
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
import inject_design as idg

# Fields inside a civ's OWPR that carry the research topic, as (offset, length).
RESEARCH_FIELDS = ((32, 4), (40, 4))

# A planet's name, in PLNT's own payload: a u32 length then the characters.
PLANET_NAME_OFF = 24
MAX_NAME = 63

# What a client writes into a SUN whose stored name is empty. See A4 in the
# plan: 108 suns times seven bytes is the whole of a 756-byte difference
# that once looked like combat diverging.
DEFAULT_SUN_NAME = b'Unnamed'

# The citizen array inside a planet's PLPR.
POP_COUNT_OFF = 36
POP_ARRAY_OFF = 40
POP_RECORD = 9
JOB_IDS = {0: 'farmer', 1: 'worker', 2: 'scientist', 5: 'miner', 6: 'banker'}

# A soldier is the same nine-byte record with this job id, kept in a second
# array rather than among the citizens. Conscription rewrites a citizen into
# one, which is why jobs and military cannot be judged by separate rules.
MILITARY_JOB = 3

# Hurrying production, measured 19 September 2026 with a player clicking it.
# A farm 110 points into a 200-point build was offered at 360 credits; the
# player's credits went 10065 -> 9705 and three fields moved:
#
#     PLPR+23, u32   production points accumulated, 110 -> 200
#     PROD payload byte 0     0 -> 1
#     OWNR, id_at+20, u32     credits, 10065 -> 9705
#
# 360 = 4 * (200 - 110), which is the manual's rule verbatim: "The cost of
# hurrying production is 4 credits per production point left", and "the current
# production needs to be at least half finished, in order to hurry it". 110 of
# 200 is 55%.
#
# The total cost of the item is nowhere in the blob, so the referee cannot price
# a hurry from the served state alone. It does not need to: the points bought
# are the distance the submission moved the progress field, and that is a
# difference between two states it holds. See hurry_acceptable.
# The military recruitment rate, a percentage in one byte, measured by a player
# moving the slider from 0 to 50 and touching nothing else: PLPR+27 read 0 and
# came back 0x32. It is a setting rather than an immediate effect, so it is
# copied like any other decision, and it was being dropped in silence, which for
# this field means a player gets no army and no message saying why.
# A ship's crew, inside SHPR: a u32 count then the same nine-byte records the
# planet keeps its soldiers in.
CREW_COUNT_OFF = 4
CREW_ARRAY_OFF = 8

# ── ship designs ─────────────────────────────────────────────────────────────
# A design is a `DSGN` section inside the owning civ's `OWNR > DATA`, a sibling
# of `OWPR` and `SERV`, carrying its own object id and one `SDPR` child:
#
#     DSGN v4   u32 objectId ; SDPR ; one trailing byte
#     SDPR v0   u32 nameLen ; name ; six part lists ; u32 owningCivObjectId
#
# Immediately before a civ's first `DSGN`, in `DATA`'s own bytes, sits a `u32`
# count of that civ's designs. `inject_design.py` measured this format and its
# readers are used here rather than a second set being written.
#
# Measured against the 19 September 2026 rehearsal, whose store kept every
# submission: a player who created a design returned an `OWNR` grown by 75
# bytes holding a second `DSGN`/`SDPR` pair, the existing one untouched, the
# count dword 1 -> 2 and `SAVE+0` 205 -> 206.
DESIGN_COUNT_BEFORE = 4     # the count dword, this far before the first DSGN
MAX_DESIGN_NAME = 15        # the client's name buffer, per inject_design

# Where a queued ship names its design, as an offset into a whole `PROD`
# section: past PROD's own 8-byte header, then the nested `SHIP` section whose
# header begins at +21 and whose payload is the design's object id.
PROD_DESIGN_AT = 8 + 29

# Four `OWPR` bytes move in the same submission as a new design, and none of
# them is carried. Measured across all 18 turns of the rehearsal:
#
#     +49   also moves on turns 8, 9 and 13, where no design was created, and
#           the engine rewrites it across a tick
#     +77   Laptop's moved on turn 1, no design created
#     +78   Powerhouse's moved on turn 1, no design created
#     +100  a u32 the engine advances by 7 every tick on its own. It went
#           637 -> 644 across turn 6 -> 7 while that turn's design was being
#           dropped, so nothing about it waits on a submission
#     +110  the only one of the four the engine never rewrote across a tick
#
# The decisive measurement is that the player created the SAME design three
# times, on turns 5, 6 and 7: identical parts, only the name differing. The
# fields did not agree across those three submissions. `+100` went 630 -> 466
# on turn 5 and 637 -> 735 on turn 6; `+110` went 260 -> 242 and then
# 260 -> 272. A value derived from the design would have been the same all
# three times, so none of the four is a function of the design.
#
# What they are is not established, and carrying a design does not depend on
# it. `OWPR` is the civ's whole property block and holds the research topic and
# the coat-of-arms count, so an unexplained byte in it stays where it is.

RECRUIT_OFF = 27
PROGRESS_OFF = 23
PROD_HURRIED_OFF = 8            # PROD's own payload begins after its header
CREDITS_AFTER_ID = 20           # past the object id, which is itself past
                                # OWNR's length-prefixed name
HURRY_PER_POINT = 4


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

    Jobs and military were separate rules and each was right about its own
    narrow case: jobs insisted the population was unchanged, military insisted
    the soldier records were identical. Conscription turns a citizen into a
    soldier, which breaks both, and the client lets a player do it. Refusing it
    twice cost a player a turn in the first two-machine rehearsal, silently.

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

    The total is deliberately a claim about a **submission**, not about the
    galaxy. The engine's own recruitment creates soldiers out of food without
    costing a citizen, measured at 60%: population held at nine across the turn
    a soldier appeared. So the total is not invariant across a tick, and a rule
    that assumed it was would refuse every turn after one.
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
    if any(c[3] != b'\x00\x00\x00' for c in cit_b):
        return False, 'unknown bytes in a citizen record were written'
    vals_a = collections.Counter(c[2] for c in cit_a)
    vals_b = collections.Counter(c[2] for c in cit_b)
    if vals_b - vals_a:
        return False, 'per-citizen values were invented rather than reordered'
    return True, ''


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
    own payload names. Both are reported because a submission that disagrees
    with itself is refused rather than half believed, and because the two are
    written in different places.

    `name`, `parts` and `claims` are None when the record does not parse, which
    is a refusal rather than a crash.
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

    `inject_design.max_object_id` takes the larger of `SAVE`'s counter and every
    id a scan can find, so one past it is free under either reading of `SAVE+0`,
    the highest id in use or the number of objects. The two readings coincide in
    practice because ids are allocated densely from that counter, and a design
    placed at max + 1 satisfies both.
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

    The section grows, so every enclosing size is corrected. The count dword is
    only locatable relative to the civ's first `DSGN`, so a civ owning none is
    refused rather than guessed at. Every civ a generated galaxy starts with
    owns a Colony Ship.
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
    """([(objectId, design)], [refusals]) for one civ's ship designs.

    `served` and `submitted` are `design_index` results. A design is new when
    the submission holds one the served state does not. Everything else a
    submission can do to the set of designs is refused and named, because
    nothing else has been measured: editing one, deleting one, and touching
    anybody else's.
    """
    new, refused = [], []

    for oid in sorted(set(served) - set(submitted)):
        whose = 'its own' if served[oid]['civ'] == civ_oid else "another civ's"
        refused.append(f'design {oid} ({served[oid]["name"]!r}) DROPPED, it is '
                       f'missing from the submission and is {whose}; deleting '
                       f'a design is not carried yet')

    for oid in sorted(set(served) & set(submitted)):
        if submitted[oid]['record'] == served[oid]['record']:
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
    return new, refused


def prod_builds(prod):
    """The design a queue entry names, or None if it names none.

    A `PROD` payload ends with a nested section saying what is queued: `FCLT`
    with an empty payload for a facility, `WLTH` for nothing, and `SHIP` with a
    four-byte payload holding the **design's object id**. That id is the whole
    of the problem this exists to catch.
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
    then the rest of the planet's fields. There is no fixed offset for it,
    because the citizen array in front of it grows with the population.
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

    `SHPR+4` is the count and the records follow at `+8`. Measured by a player
    dismissing two: the payload went 38 bytes to 20, the count 2 to 0, and the
    two records turned up on the planet byte for byte.
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


def set_crew(blob, ship_oid, records):
    """Write a ship's crew. The section's size changes."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sec in glxy.find('SHIP'):
        if struct.unpack_from('<I', blob, sec.payload)[0] != ship_oid:
            continue
        shpr = next(sec.find('SHPR'), None)
        if shpr is None:
            raise SystemExit(f'ship {ship_oid} has no SHPR')
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

    The rule is the manual's, not one inferred from what the client did. The
    points bought are how far the submission advanced the progress field, which
    both sides of the diff carry, and the price is four credits each. Two things
    make it refusable: an item less than half finished cannot be hurried, and a
    civ cannot spend credits it was not served.

    Judging "half finished" against the submitted total rather than a cost the
    blob does not record is sound in the direction that matters: hurrying
    completes the item, so the submitted progress *is* the total, and a client
    that understates it buys fewer points and pays for exactly those.
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

    They are adjacent and each is length-prefixed, so the second cannot be
    written without knowing the first: conscription shortens the citizens and
    lengthens the soldiers in the same turn, and patching one in place would
    leave the other's count pointing into the middle of a record.
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
    for (off, n), value in zip(RESEARCH_FIELDS, values):
        out[owpr.payload + off:owpr.payload + off + n] = value
    return bytes(out)


def _tally(jobs):
    """"6 farmer, 2 worker" from a list of job ids, for a log line."""
    import collections
    c = collections.Counter(jobs)
    return ', '.join(f'{n} {JOB_IDS.get(j, j)}' for j, n in sorted(c.items()))


# ── the rules ────────────────────────────────────────────────────────────────
def military_transfer(served, sub, civ_oid):
    """([(kind, oid, before, after, records)], why_refused) for one civ.

    Assigning crew moves soldiers between a planet's stationed array and a
    ship's, and the records arrive byte for byte, so this is a transfer and not
    an edit. That is what makes it safe to carry without decoding a soldier:
    **the multiset of a civ's military records must be identical before and
    after**. A player may rearrange their army however they like; they may not
    come back with a soldier they did not have.

    The check is over the civ's whole empire rather than over one planet and one
    ship, because a submission carries every object and there is no reason to
    assume a transfer involves only the pair a person happened to click on.

    Ownership, as everywhere here, is read from the served state.
    """
    import collections

    before_recs, after_recs, changed = [], [], []
    for oid, (owner, _prod, plpr_served, _nm) in planet_index(served).items():
        if owner != civ_oid:
            continue
        entry = planet_index(sub).get(oid)
        if entry is None:
            continue
        _at, mine_served = military_of(plpr_served)
        _at2, mine_sub = military_of(entry[2])
        if mine_served is None or mine_sub is None:
            return [], f'planet {oid} has no readable military array'
        before_recs += mine_served
        after_recs += mine_sub
        if mine_served != mine_sub:
            changed.append(('planet', oid, len(mine_served), len(mine_sub),
                            mine_sub))

    for oid, (owner, shpr_served) in _ship_crew_index(served).items():
        if owner != civ_oid:
            continue
        entry = _ship_crew_index(sub).get(oid)
        if entry is None:
            continue
        crew_served, crew_sub = crew_of(shpr_served), crew_of(entry[1])
        if crew_served is None or crew_sub is None:
            return [], f'ship {oid} has no readable crew array'
        before_recs += crew_served
        after_recs += crew_sub
        if crew_served != crew_sub:
            changed.append(('ship', oid, len(crew_served), len(crew_sub),
                            crew_sub))

    if not changed:
        return [], ''
    if collections.Counter(before_recs) != collections.Counter(after_recs):
        if len(after_recs) < len(before_recs):
            # Retiring from military service is a real thing a player can click,
            # and it destroys soldiers rather than moving them, so it fails this
            # check honestly. It is refused rather than carried because nobody
            # has measured what else it writes: retiring cuts upkeep, and a rule
            # that takes the disappearance without the rest of it would be
            # guessing at the part that costs money.
            return ([], f'{len(before_recs)} soldier(s) served and '
                    f'{len(after_recs)} came back. Retiring from service is not '
                    f'carried yet, and nothing else should lose soldiers')
        return ([], f'{len(before_recs)} soldier(s) served and '
                f'{len(after_recs)} came back, or their records were rewritten; '
                f'a transfer moves them, it does not mint them')
    return changed, ''


def _ship_crew_index(blob):
    """{shipObjectId: (owner, SHPR payload bytes)}"""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for sec in glxy.find('SHIP'):
        oid, _x, _z, _y, owner = struct.unpack_from('<IfffI', blob, sec.payload)
        shpr = next(sec.find('SHPR'), None)
        out[oid] = (owner,
                    bytes(blob[shpr.payload:shpr.end]) if shpr else None)
    return out


def merge(blob, submissions, log=print, notes=None):
    """submissions: [(civ_name, submitted_blob)]. Returns the merged blob.

    `notes` is an optional `{civ: [reason]}` that every refusal is recorded in
    as well as logged. The log goes to whoever is running the referee, which is
    nobody on the player's machine: two of the rehearsal's six findings were a
    rename and a conscription that the client accepted, the merge dropped and
    the player never heard about. `referee.py` writes what lands here beside
    the turn so `player_turn.follow` can print it into the launcher's log.
    """
    served_ships = ship_index(blob)
    served_planets = planet_index(blob)
    served_designs = design_index(blob)
    names = {o['oid']: o['name'] for o in icv.owner_records(blob)}
    accepted = dropped = 0

    for civ_name, sub in submissions:
        civ = civ_by_name(blob, civ_name)
        mine = civ['oid']
        log(f"{civ_name} (object {mine}):")

        def drop(reason, _civ=civ_name):
            """Refuse one change: say it here, and tell the player who made it.

            A refusal that only reaches the referee's console is a turn the
            player watches disappear, which is what happened to a system rename
            and a conscription in the first two-machine rehearsal.
            """
            nonlocal dropped
            dropped += 1
            log(f"    {reason}")
            if notes is not None:
                notes.setdefault(_civ, []).append(reason)

        # Ship designs, before the production queues, because a queue that
        # builds a design the galaxy does not hold is a dangling reference and
        # the engine refuses the whole file rather than the order.
        #
        # Judged against `served_designs`, the state as it was served, like
        # every other rule here: applying one player's designs first would
        # otherwise make the next player's untouched copy of the galaxy look
        # like a submission that had deleted them.
        remap = {}
        new_designs, refusals = design_orders(served_designs,
                                              design_index(sub), mine)
        for why in refusals:
            drop(why)
        for was, d in new_designs:
            oid = was
            if oid in design_index(blob):
                # The client allocates a new object id as one past the object
                # count, so two players who design a ship on the same turn both
                # arrive claiming the same id, and merging both would put two
                # objects into one galaxy under one id. Whoever is merged second
                # is renumbered.
                #
                # This is the one judgement made against the accumulating blob
                # rather than against the state served, and it has to be: an id
                # is taken or free in the galaxy being built, not in the one
                # either player was handed.
                oid = next_object_id(blob)
                remap[was] = oid
            blob = set_high_water(blob, oid)
            blob = add_design(blob, mine, with_design_id(d['record'], oid))
            log(f"    design {oid}: {d['name']!r} taken"
                + (f", submitted as {was} and renumbered"
                   if oid != was else ""))
            accepted += 1
        mine_designs = designs_of(blob, mine)

        # ship orders
        for ship_oid, (_claimed, dyno) in sorted(ship_index(sub).items()):
            if ship_oid not in served_ships:
                drop(f"ship {ship_oid}: DROPPED, not in the state served")
                continue
            owner, as_served = served_ships[ship_oid]
            if dyno == as_served:
                continue
            if owner != mine:
                drop(f"ship {ship_oid}: DROPPED, owned by "
                     f"{names.get(owner, owner)}")
                continue
            blob = replace_dyno(blob, ship_oid, dyno)
            log(f"    ship {ship_oid}: order taken "
                f"({len(as_served)} -> {len(dyno)} bytes)")
            accepted += 1

        # production queues and job allocation
        for planet_oid, (_claimed, prod, plpr, name) in sorted(
                planet_index(sub).items()):
            if planet_oid not in served_planets:
                drop(f"planet {planet_oid}: DROPPED, not in the state served")
                continue
            owner, prod_served, plpr_served, name_served = \
                served_planets[planet_oid]

            # A queue naming a design that was renumbered has to name the
            # number it ended up with. The client wrote the id it allocated,
            # and that id belongs to somebody else now.
            if prod is not None and prod_builds(prod) in remap:
                prod = with_prod_design(prod, remap[prod_builds(prod)])

            if name is None and name_served is not None:
                # planet_name refuses a length it cannot trust, so a submission
                # with a corrupt or overlong name arrives as None. Say that,
                # rather than letting the next rule report whatever it happens
                # to notice about the same wreckage.
                drop(f"planet {planet_oid}: rename DROPPED, the name field "
                     f"is unreadable or longer than {MAX_NAME} bytes")
            elif name is not None and name_served is not None and name != name_served:
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
                    drop(f"planet {planet_oid}: production DROPPED, owned by "
                         f"{names.get(owner, owner)}")
                elif prod is None or prod_served is None:
                    drop(f"planet {planet_oid}: production DROPPED, no PROD "
                         f"section on one side")
                elif (prod_builds(prod) is not None
                      and prod_builds(prod) not in mine_designs):
                    # A queue that names a design the galaxy does not hold is a
                    # dangling reference, and the engine refuses the whole
                    # galaxy rather than the order: the client started, read it,
                    # and exited in four seconds. Designs are carried now, so
                    # this fires for a queue naming something that was refused
                    # or that the submitter does not own, and it stays because
                    # it is what keeps an unloadable galaxy from being
                    # published.
                    #
                    # Asked of the merged galaxy rather than of the state
                    # served, because the question is whether the reference will
                    # resolve in the file the referee is about to publish. It is
                    # asked per civ so that one player's new design cannot
                    # become the thing another player's queue happens to name.
                    drop(f"planet {planet_oid}: production DROPPED, it builds "
                         f"design {prod_builds(prod)}, which {civ_name} does "
                         f"not own in the merged galaxy")
                elif is_hurried(prod) and not is_hurried(prod_served):
                    # A hurry takes effect the moment it is clicked, so the
                    # submission carries the outcome rather than the request:
                    # the item complete and the credits already gone. The
                    # referee prices it from the state it served and spends the
                    # credits itself, so a client that edited its own balance
                    # gains nothing.
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
                        log(f"    planet {planet_oid}: production hurried, "
                            f"{points} point(s) for {cost} credits")
                        accepted += 1
                else:
                    blob = replace_prod(blob, planet_oid, prod)
                    log(f"    planet {planet_oid}: production queue taken")
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

        # Everybody this civ holds, judged together. Jobs and military used to
        # be two rules and a turn that did both, conscripting a citizen and
        # posting the new soldier to a ship, was refused twice over.
        ok, why = people_acceptable(blob, sub, mine)
        planets_before, ships_before = population(blob, mine)
        planets_after, ships_after = population(sub, mine)
        if planets_after is None:
            pass                              # unreadable; people_acceptable said so
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
                log(f"    planet {oid}: {'; '.join(what) or 'people moved'}")
                accepted += 1
            for oid in sorted(ships_after):
                if ships_before[oid] == ships_after[oid]:
                    continue
                blob = set_crew(blob, oid, ships_after[oid])
                log(f"    ship {oid}: crew {len(ships_before[oid])} -> "
                    f"{len(ships_after[oid])}")
                accepted += 1

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
                drop(f"system {sun_oid}: rename DROPPED, the name field is "
                     f"unreadable or longer than {MAX_NAME} bytes")
                continue
            if name == name_served:
                continue
            if name_served[0] == 0 and name[1] == DEFAULT_SUN_NAME:
                # Not a rename. A running client materialises `Unnamed` into
                # every SUN the blob left empty, so a fresh galaxy comes back
                # from each player with a "rename" on every system they do not
                # own. Dropping it is correct and saying so 32 times a turn per
                # player buries the drops that mean something: the first live
                # merge of the rehearsal reported 65 drops, 64 of them this.
                continue
            # The majority is of the planets that have an owner, not of every
            # rock in the system. A player holding the only settled planet in a
            # system of six may name it, which is what the client allows and
            # what the first live rehearsal caught us refusing: both players
            # named their own home system on turn 2 and both were dropped,
            # holding 1 of 6 and 1 of 4. The rule was inherited by analogy from
            # planet renaming, where the client's own refusal names a majority,
            # and the analogy was wrong.
            held = owners.get(mine, 0)
            settled = sum(owners.values())
            if held * 2 <= settled:
                drop(f"system {sun_oid}: rename DROPPED, {civ_name} holds "
                     f"{held} of the {settled} settled planet(s) in a system "
                     f"of {total}, not a majority")
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
                drop(f"research: DROPPED, belongs to {other['name']}")

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
