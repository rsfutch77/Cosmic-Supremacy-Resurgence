"""
set_blob_player.py , stamp a save blob with the civ its recipient plays
=======================================================================
    python set_blob_player.py turn3.b64 --show
    python set_blob_player.py turn3.b64 --civ DemoPlayer --dat playerA.dat
    python set_blob_player.py turn3.b64 --civ BadGuy     --dat playerB.dat

Two clients sharing one galaxy have to control different civs. The blob decides
it: `GLOB` holds the object id of the civ the loading client will play, and it
is the only byte range in the blob that changes when the local player changes.

Measured by capturing the same galaxy twice from one client, once as each civ.
The two captures were 37,954 bytes each, 419 sections each, and differed in
exactly one dword, reading 202 against 198, the two civs' object ids.

**The field is not at a fixed offset.** `GLOB` carries a variable-length list of
the players this galaxy knows about, each entry holding a user id and a name, so
the id sits further along once any civ has met another. A blob whose civs have
never met puts it at `+40`; after first contact it moves. Writing the fixed
offset into a blob with one known player lands on the length prefix of a name
and produces a save the client rejects with an exception dialog.

What is stable is the tag that follows it: the payload runs

    ... u32 99999 ; u32 localPlayerObjectId ; 'TMGX' ...

in both layouts, so the id is located by finding `TMGX` and stepping back four
bytes. The result is checked against the civs the blob actually contains before
anything is written.

This is what makes per-player distribution a data operation. The server holds one
authoritative state and writes each player a copy stamped with their own civ, so
nothing about a player's identity depends on patching a running process.
`client/dev_tools/set_local_player.py` does the same thing to a client that is
already up, which is useful while testing and is not how a turn should be served.

The stamp says who you PLAY, not what you can SEE. Hiding what a player has not
explored is a separate job; see `filter_blob.py`.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp
import inject_civ as icv

ANCHOR = b'TMGX'               # the tag that follows the local-player id


def player_field(blob):
    """Absolute offset of the local-player object id inside GLOB."""
    tree = sp.parse_blob(blob)
    glob = next(tree[0].find('GLOB'), None)
    if glob is None:
        raise SystemExit('no GLOB section in this blob')
    payload = bytes(blob[glob.payload:glob.end])
    at = payload.find(ANCHOR)
    if at < 0:
        raise SystemExit(f'no {ANCHOR.decode()} anchor in GLOB; '
                         f'{len(payload)} byte payload')
    if payload.find(ANCHOR, at + 1) >= 0:
        raise SystemExit(f'{ANCHOR.decode()} appears more than once in GLOB')
    if at < 4:
        raise SystemExit(f'{ANCHOR.decode()} at +{at}, too early to hold an id')
    return tree, glob, glob.payload + at - 4


def get_player(blob) -> int:
    _tree, _glob, off = player_field(blob)
    return struct.unpack_from('<I', blob, off)[0]


def set_player(blob: bytes, civ_name: str, log=print) -> bytes:
    owners = icv.owner_records(blob)
    civ = next((o for o in owners if o['name'] == civ_name), None)
    if civ is None:
        raise SystemExit(f"no civ named {civ_name!r}; saw "
                         f"{[o['name'] for o in owners]}")
    _tree, _glob, off = player_field(blob)
    out = bytearray(blob)
    was = struct.unpack_from('<I', out, off)[0]
    names = {o['oid']: o['name'] for o in owners}
    if was not in names:
        raise SystemExit(f'the field reads {was}, which is no civ in this blob '
                         f'({sorted(names)}); refusing to write')
    struct.pack_into('<I', out, off, civ['oid'])
    log(f"  local player: {was} ({names.get(was, 'unknown')}) -> "
        f"{civ['oid']} ({civ_name})")
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('blob')
    ap.add_argument('--civ', help='the civ the recipient will play')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--dat', help='write the decompressed result here')
    ap.add_argument('-o', '--out', help='write a .b64 capture here')
    a = ap.parse_args()

    blob = sp.load_any(a.blob)
    names = {o['oid']: o['name'] for o in icv.owner_records(blob)}
    if a.show or not a.civ:
        cur = get_player(blob)
        _t, glob, off = player_field(blob)
        print(f"  GLOB+{off - glob.payload} = {cur} ({names.get(cur, 'unknown')})")
        print(f"  civs: " + ', '.join(f'{n} ({i})' for i, n in sorted(names.items())))
        if not a.civ:
            return

    blob = set_player(blob, a.civ)
    sp.parse_blob(blob)
    if a.dat:
        if not a.dat.lower().endswith('.dat'):
            raise SystemExit('--dat path must end in .dat')
        open(a.dat, 'wb').write(blob)
        print(f'wrote {a.dat}')
    if a.out:
        open(a.out, 'wb').write(sp.encode_save(blob))
        print(f'wrote {a.out}')


if __name__ == '__main__':
    main()
