"""
canonical.py: the form of a blob that two honest computations agree on

    python canonical.py turn0009.b64
    python canonical.py runA.b64 runB.b64        # compare two runs

    from canonical import canonical_hash
    canonical_hash(blob)                    # HASH_VERSION
    canonical_hash(blob, version=1)         # a record archived before it

Recomputing a turn gives the same bytes except for the colour of every `KNPL`
record (`dev_tools/knpl.py`): the client never sets it until a player picks
one, so it carries whatever was in memory, and the reader forces its top byte
to 0xFF. The canonical form zeroes every record's colour and compares every
other byte. The colour is drawn only for a civ coloured by choice, which sets
it.

Version 1 zeroes only each `KNPL` payload's trailing `u32`, the last record's
colour, or the whole payload when it is empty. `referee.py` records the
version beside its hashes, and a record without one is version 1. Given two
blobs, the tool prints where they still differ after masking.
"""
import argparse
import hashlib
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'dev_tools'))

import save_parser as sp

HASH_VERSION = 2
KNPL_RECORD = 36        # bytes per KNPL record, after its u32 count
KNPL_COLOUR = 32        # the record's colour, a u32


def _walk(sections, path=()):
    for s in sections:
        here = path + (s.tag.decode('latin-1'),)
        yield here, s
        yield from _walk(s.children, here)


def _knpl_colours(blob: bytes, sec):
    """The blob offset of each record's colour, or None when the payload is
    not a count and that many records."""
    if sec.size < 4:
        return None
    n = struct.unpack_from('<I', blob, sec.payload)[0]
    if sec.size != 4 + KNPL_RECORD * n:
        return None
    return [sec.payload + 4 + KNPL_RECORD * i + KNPL_COLOUR for i in range(n)]


def masked_ranges(blob: bytes, version: int = HASH_VERSION):
    """[(start, end, why)] of every byte range the canonical form zeroes. A
    `KNPL` that does not walk has its trailing `u32` zeroed in either
    version."""
    if version not in (1, 2):
        raise ValueError(f'no canonical hash version {version}')
    out = []
    for path, sec in _walk(sp.parse_blob(blob)):
        if sec.tag != b'KNPL':
            continue
        where = ">".join(path)
        colours = _knpl_colours(blob, sec) if version == 2 else None
        if colours is not None:
            out += [(at, at + 4, f'{where} record {i} colour')
                    for i, at in enumerate(colours)]
        elif sec.size >= 4:
            out.append((sec.end - 4, sec.end, f'{where} trailing 4 byte(s)'))
    return out


def canonical(blob: bytes, version: int = HASH_VERSION) -> bytes:
    """The blob with every volatile field zeroed."""
    out = bytearray(blob)
    for start, end, _why in masked_ranges(blob, version):
        out[start:end] = b'\0' * (end - start)
    return bytes(out)


def canonical_hash(blob: bytes, version: int = HASH_VERSION) -> str:
    return hashlib.sha256(canonical(blob, version)).hexdigest()


def differences(a: bytes, b: bytes, limit: int = 20,
                version: int = HASH_VERSION):
    """Where two blobs differ once both are canonical."""
    ca, cb = canonical(a, version), canonical(b, version)
    if len(ca) != len(cb):
        return [(None, len(ca), len(cb))]
    out = []
    for i, (x, y) in enumerate(zip(ca, cb)):
        if x != y:
            out.append((i, x, y))
            if len(out) >= limit:
                break
    return out


def locate(blob: bytes, offset: int) -> str:
    """The section path containing a byte offset, for naming a difference."""
    best = None
    for path, sec in _walk(sp.parse_blob(blob)):
        if sec.start <= offset < sec.end:
            if best is None or sec.start >= best[1].start:
                best = (path, sec)
    if best is None:
        return 'outside every section'
    path, sec = best
    return f'{">".join(path)} +{offset - sec.payload}'


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('blob')
    ap.add_argument('other', nargs='?', help='a second run of the same turn')
    ap.add_argument('--show-masked', action='store_true')
    a = ap.parse_args()

    blob = sp.load_any(a.blob)
    ranges = masked_ranges(blob)
    print(f'{os.path.basename(a.blob)}: {len(blob):,} bytes, '
          f'{len(ranges)} masked range(s), '
          f'{sum(e - s for s, e, _ in ranges)} byte(s) masked')
    print(f'  canonical sha256 {canonical_hash(blob)}')
    if a.show_masked:
        for s, e, why in ranges:
            print(f'    {s:#08x}..{e:#08x}  {why}')

    if not a.other:
        return 0

    other = sp.load_any(a.other)
    print(f'{os.path.basename(a.other)}: {len(other):,} bytes')
    print(f'  canonical sha256 {canonical_hash(other)}')
    if canonical_hash(blob) == canonical_hash(other):
        print('\nthe two runs agree')
        return 0
    diffs = differences(blob, other)
    if diffs and diffs[0][0] is None:
        print(f'\nlengths differ: {diffs[0][1]} against {diffs[0][2]}')
        return 1
    print(f'\nthey do NOT agree, {len(diffs)} differing byte(s) shown:')
    for off, x, y in diffs:
        print(f'  {off:#08x}  {x:#04x} -> {y:#04x}   {locate(blob, off)}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
