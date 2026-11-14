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
        +19   u8   set with +17                           (key `w19`)
        +20   i32  -1 until a war is declared, then 100  (key `w20`)
        +24   u16  set with +17                           (key `w24`)
        +26   u16  0 in every capture                     (key `w26`)
        +28   u8   0 in every capture                     (key `w28`)
        +29   u8   the civ's territory is drawn          (key `territory`)
        +30   u8   the civ's ships are drawn             (key `ships`)
        +31   u8   1 colours the civ by stance, 0 by +32 (key `by_stance`)
        +32   u32  the colour picked for the civ, B, G, R, A (key `colour`)

First contact (`0x534F90`) passes the current turn and the opening stance to
the constructor (`0x532E60`): war under `waronly`, by team in a team game,
else neutral. The constructor sets +20 to -1, +29 to 0, +30 and +31 to 1, and
never sets the colour, so a civ that never picked one carries whatever was in
memory. The reader forces the colour's top byte to 0xFF, and that is the
nondeterministic dword `canonical.py` masks.

`w17` to `w28` are named by offset: the diplomacy captures place them, not the
code that reads them.
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
    ('w19', 19, '<B'),
    ('w20', 20, '<i'),
    ('w24', 24, '<H'),
    ('w26', 26, '<H'),
    ('w28', 28, '<B'),
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

