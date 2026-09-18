"""
filter_blob.py , remove galaxy content from a save blob, for fog-of-war tests
==============================================================================
    python filter_blob.py <capture.b64|.dat> --drop-systems 3 --dat out.dat
    python filter_blob.py <capture.b64|.dat> --list

The question this exists to answer: **can a client load a galaxy with objects
removed?** If it can, a server can hand each player a state containing only what
that player knows, and pushing whole states stops being a maphack. If it cannot,
full-state distribution is a property of the blob-push design and has to be
accepted knowingly.

A star system is the natural unit. `SOLA` is a child of `GLXY` and contains its
own `SUN` and `PLNT` records, so dropping one removes a self-contained subtree
and creates no dangling owner references, as long as the system holds no
colonised planet. That is also exactly what fog of war would hide: a system the
player has never explored.

Two counts have to be corrected or the client rejects the blob:

    SAVE payload +0    u32 total object count
    GLXY payload +0    u32 civ count            (unchanged here, civs are kept)

Section sizes are fixed by `save_parser.replace_payload`, which corrects every
enclosing section.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp


def sola_planets(blob, sola):
    """[(PLNT section, object id, owner id)] for one system."""
    out = []
    for sec in sola.find('PLNT'):
        oid = struct.unpack_from('<I', blob, sec.payload)[0]
        # PLNT payload: u32 objectId ; f32 x, z, y ; u32 ownerObjectId ; ...
        owner = struct.unpack_from('<I', blob, sec.payload + 16)[0]
        out.append((sec, oid, owner))
    return out


def describe(blob):
    tree = sp.parse_blob(blob)
    save = tree[0]
    glxy = next(save.find('GLXY'))
    objects = struct.unpack_from('<I', blob, save.payload)[0]
    civs = struct.unpack_from('<I', blob, glxy.payload)[0]
    solas = [c for c in glxy.children if c.tag == b'SOLA']
    print(f'{len(blob):,} bytes, {objects} objects, {civs} civ(s), '
          f'{len(solas)} system(s)')
    return tree, save, glxy, solas, objects


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('file')
    ap.add_argument('--drop-systems', type=int, default=0,
                    help='how many uncolonised systems to remove')
    ap.add_argument('--dat', help='write the decompressed result here')
    ap.add_argument('--list', action='store_true')
    args = ap.parse_args()

    blob = sp.load_any(args.file)
    tree, save, glxy, solas, objects = describe(blob)

    # a system is droppable when nothing in it is owned
    droppable = []
    for s in solas:
        planets = sola_planets(blob, s)
        if any(owner for _, _, owner in planets):
            continue
        n_obj = 1 + len(planets)          # the SUN plus its planets
        droppable.append((s, n_obj, len(planets)))

    print(f'{len(droppable)} of {len(solas)} system(s) hold no owned planet')
    if args.list:
        for s, n_obj, n_pl in droppable[:12]:
            print(f'  SOLA @{s.start:#08x} size={s.size} '
                  f'{n_pl} planet(s), {n_obj} object(s)')
        return

    if not args.drop_systems:
        return

    # Take them from the end, so the earliest systems (which the player is most
    # likely to have explored) are the ones kept.
    victims = droppable[-args.drop_systems:]
    removed_objects = sum(n for _, n, _ in victims)
    victim_starts = {s.start for s, _, _ in victims}
    print(f'dropping {len(victims)} system(s), {removed_objects} object(s)')

    # Rebuild GLXY's payload by copying all of it except the victims' byte
    # ranges. Copying "prologue, then each kept child, then epilogue" would be
    # wrong: a payload's children are ordered but need not be contiguous, and
    # anything sitting in a gap between two of them would be dropped. Deleting
    # ranges keeps whatever we do not understand.
    out = bytearray()
    pos = glxy.payload
    for c in glxy.children:
        if c.start not in victim_starts:
            continue
        out += blob[pos:c.start]
        pos = c.end
    out += blob[pos:glxy.end]

    new_blob = sp.replace_payload(blob, tree, glxy, bytes(out))

    # SAVE's object count sits at its payload start, ahead of the splice
    new_blob = bytearray(new_blob)
    struct.pack_into('<I', new_blob, save.payload, objects - removed_objects)
    new_blob = bytes(new_blob)

    print('\nresult:')
    describe(new_blob)

    if args.dat:
        if not args.dat.lower().endswith('.dat'):
            sys.exit('--dat path must end in .dat')
        with open(args.dat, 'wb') as f:
            f.write(new_blob)
        print(f'wrote {args.dat}')


if __name__ == '__main__':
    main()
