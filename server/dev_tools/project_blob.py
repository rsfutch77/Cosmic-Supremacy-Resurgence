"""
project_blob.py , the galaxy as one civ is entitled to see it
=============================================================
    python project_blob.py <blob> --civ Alice --dat alice.dat
    python project_blob.py <blob> --report

D1 asks that a player's copy omit what that player has not discovered, rather
than relying on the client to decline to draw it. This builds that copy.

**This is projection (1) of the two: the map is hidden, beliefs are not.**
Systems the civ has not entered are removed outright. Systems that are kept
show *current truth*, not what the civ last saw , so a planet they explored
once and never revisited still shows its real owner. Presenting stale belief
means writing `PLNT` records from `EXSY` rows instead of deleting subtrees, and
that is a much larger job, recorded and not attempted here. Anyone reading this
as "fog of war is done" is reading it wrong.

What is kept, per civ:

- every system the civ owns a planet in
- every system named in the civ's `EXSY`, which is the engine's own record of
  where that civ has been

`EXSY` is the input rather than a guess, which is the point: the engine already
maintains per-civ knowledge, so "what has this civ entered" is a question the
blob already answers. **Rows appear as a hull flies near a system, not when an
order completes** , machine A measured a scout registering a system it passed
at 58 units and never stopped at , so this is knowledge by proximity, which is
what a player would expect to see on their map.

Ships are safe by construction: `SHIP` sections are children of `GLXY`, not of
`SOLA`, so dropping a system can never drop a ship, including one in flight
through the space a dropped system occupies. Verified on the fixture rather
than assumed, because it was the case most likely to be got wrong.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp
import inject_civ as icv
import exsy


def _civ_oid(blob, civ):
    for o in icv.owner_records(blob):
        if o['name'] == civ:
            return o['oid']
    raise SystemExit(f'no civ named {civ!r} in this blob')


def _systems(blob, tree=None):
    """{system id: SOLA section}, keyed by the id of the system's sun."""
    tree = tree or sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sun in sola.find('SUN '):
            out[struct.unpack_from('<I', blob, sun.payload)[0]] = sola
            break
    return out


def knowledge(blob, civ):
    """(owned system ids, entered system ids) for one civ."""
    tree = sp.parse_blob(blob)
    save = tree[0]
    oid = _civ_oid(blob, civ)
    systems = _systems(blob, tree)

    owned = set()
    for sid, sola in systems.items():
        for pl in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, pl.payload + 16)[0] == oid:
                owned.add(sid)
                break

    entered = set()
    for ownr in save.find('OWNR'):
        if struct.unpack_from('<I', blob, ownr.payload)[0] != oid:
            # OWNR's own object id is not the civ id; match on the civ record
            # the name lookup gave us by checking EXSY under the right OWNR.
            pass
    # OWNR sections are ordered as owner_records reports them, so pair them up
    # rather than trusting a field whose meaning is not settled.
    order = [o['name'] for o in icv.owner_records(blob)]
    ownrs = list(save.find('OWNR'))
    for name, ownr in zip(order, ownrs):
        if name != civ:
            continue
        for sec in ownr.find('EXSY'):
            entered |= set(exsy.systems_known(blob[sec.payload:sec.end]))
    return owned, entered


def project(blob: bytes, civ: str, log=lambda *a: None) -> bytes:
    """The galaxy with every system this civ has not entered removed."""
    import filter_blob

    tree = sp.parse_blob(blob)
    save = tree[0]
    glxy = next(save.find('GLXY'))
    objects, = struct.unpack_from('<I', blob, save.payload)
    systems = _systems(blob, tree)

    owned, entered = knowledge(blob, civ)
    keep = owned | entered
    drop = {sid: sola for sid, sola in systems.items() if sid not in keep}
    log(f'{civ}: owns {sorted(owned)}, entered {sorted(entered)}, '
        f'keeping {len(keep)} of {len(systems)} system(s), '
        f'dropping {len(drop)}')

    removed = 0
    victim_starts = set()
    for sid, sola in drop.items():
        victim_starts.add(sola.start)
        removed += 1 + len(list(sola.find('PLNT')))

    out = bytearray()
    pos = glxy.payload
    for c in glxy.children:
        if c.start not in victim_starts:
            continue
        out += blob[pos:c.start]
        pos = c.end
    out += blob[pos:glxy.end]

    new_blob = sp.replace_payload(blob, tree, glxy, bytes(out))
    new_blob = bytearray(new_blob)
    struct.pack_into('<I', new_blob, save.payload, objects - removed)
    new_blob = bytes(new_blob)

    # Every civ's EXSY, not just this one's: another civ's table naming a
    # system this copy no longer holds is just as dangling.
    for _ in range(64):
        tree2 = sp.parse_blob(new_blob)
        target = None
        for ownr in tree2[0].find('OWNR'):
            for sec in ownr.find('EXSY'):
                payload = new_blob[sec.payload:sec.end]
                pruned = exsy.drop_systems(payload, set(drop))
                if pruned != payload:
                    target = (tree2, sec, pruned)
                    break
            if target:
                break
        if target is None:
            break
        tree2, sec, pruned = target
        log(f'  EXSY {sec.size} -> {len(pruned)} bytes')
        new_blob = sp.replace_payload(new_blob, tree2, sec, pruned)

    return new_blob


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('file')
    ap.add_argument('--civ')
    ap.add_argument('--dat')
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    blob = sp.load_any(a.file)

    if a.report:
        systems = _systems(blob)
        print(f'{len(blob):,} bytes, {len(systems)} system(s)')
        for o in icv.owner_records(blob):
            owned, entered = knowledge(blob, o['name'])
            print(f"  {o['name']:<8} owns {sorted(owned)} "
                  f"entered {sorted(entered)} "
                  f"unsettled-but-entered {sorted(entered - owned)}")
        return

    out = project(blob, a.civ, log=print)
    print(f'{len(blob):,} -> {len(out):,} bytes')
    if a.dat:
        with open(a.dat, 'wb') as f:
            f.write(out)
        print(f'wrote {a.dat}')


if __name__ == '__main__':
    main()
