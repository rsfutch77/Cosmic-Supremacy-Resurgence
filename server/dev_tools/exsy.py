"""
exsy.py: read and rewrite a civ's explored-systems table
    from exsy import parse, build, systems_known

`EXSY` sits under `OWNR > DATA` and is one civ's record of the systems it has
entered, naming each by id. A filter that drops `SOLA` subtrees from `GLXY`
must drop the same systems here.

## Layout

    u32   system count
    per system:
        u32   system id
        u32   sun id          (equal to the system id in every row seen)
        str   sun name        (u32 length then unpadded bytes)
        u32   undecoded       (13, 3 and 2 observed)
        u32   planet count
        per planet:
            u32   last known owner civ id, 0 for never seen
            u32   an observed quantity, undecoded
            str   planet name

`str` is a `u32` length followed by exactly that many unpadded bytes, so
records are not aligned and the table can only be walked.

The table is a per-civ cache of observations: the same planet carries
different values in different civs' tables, and an owner can be stale. A
rename is stored on the object itself (`PLNT`/`SUN ` payload +24). Dropping a
row therefore loses no player decision. Undecoded fields are carried through
byte for byte.
"""
import struct


def _rd_str(d, i):
    n, = struct.unpack_from('<I', d, i)
    return d[i + 4:i + 4 + n], i + 4 + n


def _wr_str(b: bytes) -> bytes:
    return struct.pack('<I', len(b)) + b


def parse(payload: bytes):
    """[system] for one civ's table; raises ValueError unless the walk ends
    exactly on the last byte, so a misread table is never rewritten."""
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
    """The wire form of a table. `build(parse(x)) == x`."""
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
