"""
fog_tables.py , what the per-civ tables reference, and what filtering breaks
============================================================================
    python fog_tables.py <blob>

`filter_blob.py` removes whole uncolonised `SOLA` subtrees. That is clean for
the galaxy tree, because a system is self-contained. This reports what the two
per-civ tables still name afterwards, so "does a filtered blob load" is asked
about a blob whose dangling references are known rather than guessed at.

The answer turned out to be narrower than expected, and one half of the
suspicion was wrong:

- **`EXSY` does dangle.** It is keyed by system id, so every dropped system
  leaves a row naming nothing. `exsy.py` reads and rewrites it.
- **`KNPL` does not, and cannot.** Its records are keyed by **civ** id, not
  planet id, despite the name. The one record in galaxy1 reads
  `[198, 198, 3, ...]`, and 198 is DemoPlayer, a civ, with 3 being the turn
  contact was made. Dropping uncolonised systems removes no civs, so this
  table is untouched by system filtering.

That was worth measuring rather than assuming: `KNPL` was half of the suspected
cause of the client bailing at a turn boundary on a filtered blob, and it
cannot be any of it. An id-based check that does not know which *kind* of id it
is holding reports a civ id as a dangling planet, which is exactly the false
lead this tool exists to stop.
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
    """[civ id] , the first word of each fixed-size record.

    The trailing `u32` of a `KNPL` payload is the one field in the format that
    is not deterministic, which `canonical.py` masks, so this reads the key and
    deliberately leaves the rest alone.
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
    """(object ids in the galaxy tree, civ ids), kept apart on purpose."""
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
            # Checked against CIVS, not objects. Checking these against object
            # ids reports every row as dangling, which is how this table got
            # blamed for a bail it cannot cause.
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
