"""
knpl.py: read and rewrite a civ's known-players table
    from knpl import parse, build

`KNPL` (v12) sits under `OWNR > DATA` and holds one record per civ this civ
has met. The layout is the client's own: a map from the other civ's object id
to a record the reader `0x5332E0` and writer `0x532F20` walk member by member.

## Layout

    u32   record count
    per record, 36 bytes:
        +0    u32  the other civ's object id, the map key
        +4    u32  the same id, the record's own handle to that civ
        +8    u32  the turn of first contact            (key `met`)
        +12   u32  the stance: 0 neutral, 1 war, 2 cease-fire, 3 peace,
                   4 alliance                            (key `stance`)
        +16   u8   1 on the side that declared the war   (key `declared`)
        +17   u16  set during the first tick of a war    (key `w17`)
        +19   u8   1 once the significant battle has been fought; no
                   reputation is gained or lost for attacking again
                                                         (key `battled`)
        +20   i32  the other civ's Inactivity percent when the war was
                   declared, -1 before                   (key `inactivity`)
        +24   u16  reputation-gain cooldown: +150 per gain, -1 a tick
                                                         (key `cooldown`)
        +26   u16  bio-bomb sets dropped on the civ, capped at 100, -1 a
                   tick; its loyalty penalty in percent  (key `biobombed`)
        +28   u8   1 once the civ's leader's name is known
                                                         (key `leader_known`)
        +29   u8   the civ's territory is drawn          (key `territory`)
        +30   u8   the civ's ships are drawn             (key `ships`)
        +31   u8   1 colours the civ by stance, 0 by +32 (key `by_stance`)
        +32   u32  the colour picked for the civ, B, G, R, A (key `colour`)

First contact (`0x534F90`) passes the current turn and the opening stance to
the constructor (`0x532E60`): war under `waronly`, by team in a team game,
else neutral. The constructor sets `inactivity` to -1, `territory` to 0,
`ships` and `by_stance` to 1, and never sets the colour, so a civ that never
picked one carries whatever was in memory. The reader forces the colour's top
byte to 0xFF. The colour getter (`0x534E40`) reads it only when `by_stance` is
0, and `canonical.py` masks every record's colour.

In the client a record is a map node whose value starts at node+0x10; the
relation getter `0x534340` returns node+0x14, the `met` field. Accepting a
war (`0x55BD40`) sets `declared` on the sender's side, clears it on the
receiver's, zeroes `w17` on both and records each side's `inactivity`; any
treaty but war and cease-fire clears `battled` on both. Attacking calls
`0x534D10`, which sets `battled` and, for a gain, adds 150 to `cooldown`.
The reputation tooltip (`0x4430F0`) reads `battled`, `0x517A40` takes the
higher of the target's Inactivity now and `inactivity`, and `0x5177F0` turns
`cooldown` into the gain reduction. Bio-bombing (`0x4F7147`) adds the sets
dropped to `biobombed` through `0x534D50`, and the planet loyalty tooltip
lists it per civ. `leader_known` is set at `0x54EF99` and refuses a second
"name of the Empire Leader" message. The tick (`0x5344B0`) takes `cooldown`
and `biobombed` down by 1, or 2 under the galaxy option it reads.

`w17` has no writer reachable through the relation getters: the client sets
it somewhere else in the war's first tick.
"""
import struct

RECORD = 36
FIELDS = (                      # (key, offset, struct format)
    ('civ', 0, '<I'),
    ('handle', 4, '<I'),
    ('met', 8, '<I'),
    ('stance', 12, '<I'),
    ('declared', 16, '<B'),
    ('w17', 17, '<H'),
    ('battled', 19, '<B'),
    ('inactivity', 20, '<i'),
    ('cooldown', 24, '<H'),
    ('biobombed', 26, '<H'),
    ('leader_known', 28, '<B'),
    ('territory', 29, '<B'),
    ('ships', 30, '<B'),
    ('by_stance', 31, '<B'),
    ('colour', 32, '<I'),
)
NEUTRAL, WAR, CEASE_FIRE, PEACE, ALLIANCE = range(5)


def parse(payload: bytes):
    """[record dict] in table order. Raises unless the payload is exactly the
    count and that many records."""
    n = struct.unpack_from('<I', payload, 0)[0]
    if len(payload) != 4 + RECORD * n:
        raise ValueError(f'KNPL of {len(payload)} bytes does not hold {n} '
                         f'record(s) of {RECORD}')
    out = []
    for i in range(n):
        at = 4 + RECORD * i
        out.append({k: struct.unpack_from(f, payload, at + off)[0]
                    for k, off, f in FIELDS})
    return out


def build(records) -> bytes:
    out = bytearray(struct.pack('<I', len(records)))
    for r in records:
        rec = bytearray(RECORD)
        for k, off, f in FIELDS:
            struct.pack_into(f, rec, off, r[k])
        out += rec
    return bytes(out)

