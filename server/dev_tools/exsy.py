"""
exsy.py , read and rewrite a civ's explored-systems table
=========================================================
    from exsy import parse, build, systems_known

`EXSY` sits under `OWNR > DATA` and is one civ's record of the systems it has
entered. `filter_blob.py` removes whole `SOLA` subtrees from `GLXY`, which is
clean for the galaxy tree because a system is self-contained; it is not clean
for this, because every dropped system is still named here by id.

## Layout, measured

Derived on the live galaxy1 blob at turn 13 and checked against all three
civs' tables, 266, 153 and 111 bytes. Each parse consumes every byte, which is
the check that matters: a layout that is merely plausible stops early or runs
over, and this one lands exactly on the end three times out of three.

    u32   system count
    per system:
        u32   system id
        u32   sun id          , equal to the system id in every row seen
        str   sun name        , u32 length then unpadded bytes
        u32   undecoded       , 13, 3 and 2 observed
        u32   planet count
        per planet:
            u32   last known owner civ id, 0 for never seen
            u32   an observed quantity, undecoded
            str   planet name

`str` is a `u32` length followed by exactly that many bytes, **unpadded**, so
records are not aligned and the table can only be walked, never indexed.

## What it is

**A cache of what this civ has seen, not a record of what it decided.** Three
things say so, and they matter because they decide whether dropping a row can
lose anything a player would miss.

- The same planet carries different values in different civs' tables. Planet
  `DemoPlayer's HQ` reads `(198, 11)` in one and `(198, 7)` in another.
- One civ records `BadGuy's HQ` as owned by 206 while another has the correct
  202. That is a civ remembering what was true when it last looked , the
  engine is already keeping per-player knowledge, and it is already stale in
  the way fog of war is supposed to be.
- A rename is authored on the object itself, `PLNT`/`SUN ` payload +24, so a
  name survives in the galaxy tree whether or not it is cached here.

So removing a system's row removes a cached observation. It cannot remove
anybody's decision, which is what makes this safe to filter.

The undecoded fields are carried through byte for byte rather than
reconstructed, so filtering cannot change a value it does not understand.
"""
import struct


def _rd_str(d, i):
    n, = struct.unpack_from('<I', d, i)
    return d[i + 4:i + 4 + n], i + 4 + n


def _wr_str(b: bytes) -> bytes:
    return struct.pack('<I', len(b)) + b


def parse(payload: bytes):
    """[system] for one civ's table, or raise if it does not walk cleanly.

    Raising on a short or long read is deliberate. A tolerant reader that
    returned what it managed would let a misunderstood table look like a small
    one, and the caller would then rewrite it and destroy the rest.
    """
    i = 0
    n_sys, = struct.unpack_from('<I', payload, i)
    i += 4
    systems = []
    for s in range(n_sys):
        sys_id, sun_id = struct.unpack_from('<II', payload, i)
        i += 8
        sun_name, i = _rd_str(payload, i)
        unknown, n_pl = struct.unpack_from('<II', payload, i)
        i += 8
        planets = []
        for p in range(n_pl):
            owner, seen = struct.unpack_from('<II', payload, i)
            i += 8
            name, i = _rd_str(payload, i)
            planets.append({'owner': owner, 'seen': seen, 'name': name})
        systems.append({'system': sys_id, 'sun': sun_id, 'sun_name': sun_name,
                        'unknown': unknown, 'planets': planets})
    if i != len(payload):
        raise ValueError(f'EXSY walked to {i} of {len(payload)} bytes; the '
                         f'layout does not fit this table')
    return systems


def build(systems) -> bytes:
    """The wire form of a table. `build(parse(x)) == x` for a table we
    understand, which is the only guarantee worth having here."""
    out = bytearray(struct.pack('<I', len(systems)))
    for s in systems:
        out += struct.pack('<II', s['system'], s['sun'])
        out += _wr_str(s['sun_name'])
        out += struct.pack('<II', s['unknown'], len(s['planets']))
        for p in s['planets']:
            out += struct.pack('<II', p['owner'], p['seen'])
            out += _wr_str(p['name'])
    return bytes(out)


def systems_known(payload: bytes):
    """The system ids this civ has entered."""
    return [s['system'] for s in parse(payload)]


def drop_systems(payload: bytes, drop_ids) -> bytes:
    """The table with the named systems removed."""
    drop = set(drop_ids)
    return build([s for s in parse(payload) if s['system'] not in drop])
