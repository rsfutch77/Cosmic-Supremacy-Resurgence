"""
fog_tables.py: what the per-civ tables reference, and what filtering breaks
    python fog_tables.py <blob>

Reports the ids the two per-civ tables under `OWNR` name, and whether each
still exists in the blob:

- `EXSY` is keyed by system id, so a dropped system leaves a dangling row.
  `exsy.py` reads and rewrites it.
- `KNPL` is keyed by civ id, despite the name: a u32 count, then 36-byte
  records whose first u32 is a civ id (e.g. `[198, 198, 3, ...]`, 3 being the
  turn contact was made). System filtering removes no civs, so it cannot
  dangle.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp
import inject_civ as icv
import exsy

KNPL_RECORD = 36


def read_knpl(blob, sec):
    """[civ id]: the first word of each fixed-size record.

    The trailing `u32` of a `KNPL` payload is not deterministic
    (`canonical.py` masks it), so only the key is read.
    """
    p, end = sec.payload, sec.end
    if p + 4 > end:
        return [], 'too short'
    count, = struct.unpack_from('<I', blob, p)
    p += 4
    ids = []
    for i in range(count):
        if p + KNPL_RECORD > end:
            return ids, f'ran out at record {i} of {count}'
        ids.append(struct.unpack_from('<I', blob, p)[0])
        p += KNPL_RECORD
    return ids, ('ok' if p == end else f'{end - p} trailing byte(s) unread')


def galaxy_ids(blob, tree):
    """(object ids in the galaxy tree, civ ids), as separate sets."""
    save = tree[0]
    objs = set()
    for tag in ('SUN ', 'PLNT', 'SHIP'):
        for sec in save.find(tag):
            objs.add(struct.unpack_from('<I', blob, sec.payload)[0])
    civs = {o['oid'] for o in icv.owner_records(blob)}
    return objs, civs


def survey(blob, label=''):
    tree = sp.parse_blob(blob)
    save = tree[0]
    objs, civs = galaxy_ids(blob, tree)
    print(f'{label}{len(blob):,} bytes, {len(objs)} object id(s), '
          f'{len(civs)} civ(s)')
    for ownr in save.find('OWNR'):
        for sec in ownr.find('EXSY'):
            payload = blob[sec.payload:sec.end]
            try:
                known = exsy.systems_known(payload)
            except ValueError as e:
                print(f'  EXSY  {len(payload):>4}B  UNREADABLE: {e}')
                continue
            missing = [s for s in known if s not in objs]
            flag = f'  <-- {len(missing)} DANGLING {missing}' if missing else ''
            print(f'  EXSY  {len(payload):>4}B  systems {known}{flag}')
        for sec in ownr.find('KNPL'):
            ids, note = read_knpl(blob, sec)
            # KNPL ids are civ ids, so they are checked against civs.
            missing = [i for i in ids if i not in civs]
            flag = f'  <-- {len(missing)} unknown civ(s) {missing}' if missing else ''
            print(f'  KNPL  {len(blob[sec.payload:sec.end]):>4}B  '
                  f'civs {ids} [{note}]{flag}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('file')
    a = ap.parse_args()
    survey(sp.load_any(a.file))


if __name__ == '__main__':
    main()
