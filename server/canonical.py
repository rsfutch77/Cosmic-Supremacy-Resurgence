"""
canonical.py , the form of a blob that two honest computations agree on
=======================================================================
    python canonical.py turn0009.b64
    python canonical.py runA.b64 runB.b64        # compare two runs

    from canonical import canonical_hash
    canonical_hash(blob)

A turn is a pure function of state plus orders, so recomputing one must produce
the same answer. It does, except for one field, and hashing the raw bytes would
therefore report honest agreement as disagreement. That is the opposite of what
a hash is for: the point is to notice a referee that computed something
different, and a check that cries wolf gets switched off.

What is masked, and why
-----------------------
**The trailing `u32` of every `KNPL` payload.** Measured across six captures:
the low three bytes survive a save and load faithfully, but the top byte reads
`0x00` or `0x09` in a client that played and `0xFF` in one that loaded, whatever
the blob held, and an AI civ's low bytes differ between otherwise identical
runs. It is the only nondeterminism found anywhere in the format, and both ends
of the dword move, so the whole dword goes.

An empty `KNPL` is four bytes, which is exactly this field, so such a section is
masked entirely. That is the same rule, not a special case.

Masking is deliberately narrow. Every byte not named here is compared, so a
divergence anywhere else is a real one and shows up as a differing hash rather
than being quietly forgiven. Passing two blobs compares them **after** masking
and prints where they still differ, which is how a second volatile field would
be found: the report notes that sweeping for the same signature is worth doing,
a field that changes between two otherwise identical runs or whose top byte
becomes `0xFF` after a load.
"""
import argparse
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'dev_tools'))

import save_parser as sp

VOLATILE_TAIL = {b'KNPL': 4}          # tag -> bytes at the end of the payload


def _walk(sections, path=()):
    for s in sections:
        here = path + (s.tag.decode('latin-1'),)
        yield here, s
        yield from _walk(s.children, here)


def masked_ranges(blob: bytes):
    """[(start, end, why)] of every byte range the canonical form zeroes."""
    out = []
    for path, sec in _walk(sp.parse_blob(blob)):
        n = VOLATILE_TAIL.get(sec.tag)
        if not n or sec.size < n:
            continue
        out.append((sec.end - n, sec.end,
                    f'{">".join(path)} trailing {n} byte(s)'))
    return out


def canonical(blob: bytes) -> bytes:
    """The blob with every volatile field zeroed."""
    out = bytearray(blob)
    for start, end, _why in masked_ranges(blob):
        out[start:end] = b'\0' * (end - start)
    return bytes(out)


def canonical_hash(blob: bytes) -> str:
    return hashlib.sha256(canonical(blob)).hexdigest()


def differences(a: bytes, b: bytes, limit: int = 20):
    """Where two blobs differ once both are canonical."""
    ca, cb = canonical(a), canonical(b)
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
