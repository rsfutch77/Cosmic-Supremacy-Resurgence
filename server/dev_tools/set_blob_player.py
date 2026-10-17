"""
set_blob_player.py: stamp a save blob with the civ its recipient plays
    python set_blob_player.py turn3.b64 --show
    python set_blob_player.py turn3.b64 --civ DemoPlayer --dat playerA.dat
    python set_blob_player.py turn3.b64 --civ BadGuy     --dat playerB.dat

`GLOB` holds the object id of the civ the loading client will play; it is the
only field that differs between captures of one galaxy taken as different civs.
It is not at a fixed offset: `GLOB` carries a variable-length list of known
players (user id and name each), so the id moves once civs have met. The tag
after it is stable:

    ... u32 contentVersion ; u32 localPlayerObjectId ; 'TMGX' ...

so the id is found by locating `TMGX` and stepping back four bytes, and is
checked against the blob's civs before anything is written.

The dword before the id is the content version, which the engine loads into
`[0x0080AA00]` to pick its stat tables. TestBed captures carry 99999, which
selects unreleased tables. See docs/CosmicSupremacy_Stat_Tables.md section 4a.

`client/dev_tools/set_local_player.py` does the same to a running client. The
stamp sets who a player plays, not what they see; see `filter_blob.py`.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp
import inject_civ as icv

ANCHOR = b'TMGX'               # the tag that follows the local-player id

# Content versions select the facility and ship-component stat tables.
# See docs/CosmicSupremacy_Stat_Tables.md 4a.
RELEASED_CONTENT_VERSION = 565   # the binary's own .data value; .csgalaxy galaxies run at it
CONTENT_VERSION_CEILING = 639    # at or above this, the unreleased tables


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


def content_version_field(blob):
    """Absolute offset of the content version, the dword before the player id."""
    tree, glob, off = player_field(blob)
    if off - 4 < glob.payload:
        raise SystemExit('no room for a content version before the player id')
    return tree, glob, off - 4


def get_content_version(blob) -> int:
    _tree, _glob, off = content_version_field(blob)
    return struct.unpack_from('<I', blob, off)[0]


def set_content_version(blob: bytes, version=RELEASED_CONTENT_VERSION,
                        log=print) -> bytes:
    """Stamp the content version that decides which stat tables the client uses.

    TestBed captures carry 99999, so a served blob must be stamped.
    """
    if version >= CONTENT_VERSION_CEILING:
        raise SystemExit(f'content version {version} is at or above '
                         f'{CONTENT_VERSION_CEILING}, which selects the '
                         f'unreleased stat tables')
    _tree, _glob, off = content_version_field(blob)
    out = bytearray(blob)
    was = struct.unpack_from('<I', out, off)[0]
    if was == version:
        return bytes(out)
    struct.pack_into('<I', out, off, version)
    log(f'  content version: {was} -> {version}')
    return bytes(out)


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
    ap.add_argument('--content-version', nargs='?', type=int,
                    const=RELEASED_CONTENT_VERSION, default=None,
                    metavar='N',
                    help=f'stamp the content version that selects the stat '
                         f'tables; bare flag means {RELEASED_CONTENT_VERSION}')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--dat', help='write the decompressed result here')
    ap.add_argument('-o', '--out', help='write a .b64 capture here')
    a = ap.parse_args()

    blob = sp.load_any(a.blob)
    names = {o['oid']: o['name'] for o in icv.owner_records(blob)}
    if a.show or (not a.civ and a.content_version is None):
        cur = get_player(blob)
        _t, glob, off = player_field(blob)
        ver = get_content_version(blob)
        flag = '' if ver < CONTENT_VERSION_CEILING else '  <- UNRELEASED stat tables'
        print(f"  GLOB+{off - glob.payload} = {cur} ({names.get(cur, 'unknown')})")
        print(f"  content version {ver}{flag}")
        print(f"  civs: " + ', '.join(f'{n} ({i})' for i, n in sorted(names.items())))
        if not a.civ and a.content_version is None:
            return

    if a.content_version is not None:
        blob = set_content_version(blob, a.content_version)
    if a.civ:
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
