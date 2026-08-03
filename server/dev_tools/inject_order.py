"""
inject_order.py — Push a ship order into a save blob
====================================================
Takes a captured savegame blob, gives a chosen ship an order it never had, and
writes the result where `cs_server.py`'s `loadgame` will serve it.  The point is
the multiplayer/AI capability: the server decides a ship's orders and the client
picks them up on load with nobody at the keyboard.

    python inject_order.py saves/<capture>.b64 --ship 649 \
        --target-x 516.7029 --target-y 333.0293 --target-z 278.5347 \
        --order-type 1 -o loadgame_blob.b64

DYNO layout, from the writer at 0x004DA310
------------------------------------------
    u32   orbit planet id, 0 when not in orbit      [ship+0x34]  Ship:44
    SHCO  section, v2, 9-byte payload               [ship+0x3c]  byte 0 is the
                                                                 order type
    u32                                             [ship+0x50]  Ship:72
    u8    has-orders                                [ship+0x54]  Ship:76
    u32   admiral id, 0 when unassigned             [ship+0x58]  Ship:80
    -- the following two are written ONLY when the order pointer is non-null --
    ROUT  section
    u32                                             [ship+0x6c]  Ship:100

That conditional tail is what makes an unordered ship's DYNO 30 bytes and an
ordered one's 30 + (8 + ROUT payload) + 4, which is exactly what captures show.

Giving a ship an order therefore means three coordinated edits, not one:
the SHCO order-type byte, the has-orders byte, and the appended ROUT + u32.
Writing only the ROUT leaves the order type at 0 and the UI with nothing to say.
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp

SHCO_HDR = 8


def server_dir() -> str:
    """
    The directory holding cs_server.py, which is where relative output paths are
    resolved.  Found by looking here and one level up, so this script works
    whether it sits beside cs_server.py or in a dev_tools/ subdirectory next to
    it — the default `loadgame_blob.b64` has to land where cs_server.py looks for
    it, not merely beside this file.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (here, os.path.dirname(here)):
        if os.path.exists(os.path.join(cand, 'cs_server.py')):
            return cand
    return here


def parse_dyno(blob: bytes, dyno: 'sp.Section') -> dict:
    """Split a DYNO payload into the fields the writer emits."""
    p, o = blob[dyno.payload:dyno.end], 0
    d = {}
    d['orbit_id'] = struct.unpack_from('<I', p, o)[0]; o += 4
    tag = p[o:o + 4]
    word = struct.unpack_from('<I', p, o + 4)[0]
    if tag != b'SHCO':
        raise ValueError(f"expected SHCO at DYNO+{o}, found {tag!r}")
    shco_size, shco_ver = word & sp.SIZE_MASK, word >> sp.VERSION_SHIFT
    o += SHCO_HDR
    d['shco_version'] = shco_ver
    d['shco'] = bytearray(p[o:o + shco_size]); o += shco_size
    d['f72']        = struct.unpack_from('<I', p, o)[0]; o += 4
    d['has_orders'] = p[o]; o += 1
    d['admiral_id'] = struct.unpack_from('<I', p, o)[0]; o += 4
    d['head_len']   = o
    d['rout']       = None
    d['tail_u32']   = None
    if o < len(p):
        t, v, s = sp.read_header(blob, dyno.payload + o)
        if t != b'ROUT':
            raise ValueError(f"expected ROUT at DYNO+{o}, found {t!r}")
        d['rout'] = (v, p[o + 8:o + 8 + s])
        o += 8 + s
        d['tail_u32'] = struct.unpack_from('<I', p, o)[0]; o += 4
    if o != len(p):
        raise ValueError(f"DYNO not fully accounted for: {o} of {len(p)}")
    return d


def build_dyno(d: dict) -> bytes:
    out = bytearray()
    out += struct.pack('<I', d['orbit_id'])
    out += sp.build_section(b'SHCO', d['shco_version'], bytes(d['shco']))
    out += struct.pack('<I', d['f72'])
    out.append(d['has_orders'] & 0xFF)
    out += struct.pack('<I', d['admiral_id'])
    if d['rout'] is not None:
        version, payload = d['rout']
        out += sp.build_section(b'ROUT', version, bytes(payload))
        out += struct.pack('<I', d['tail_u32'] or 0)
    return bytes(out)


def ship_sections(blob, tree):
    """[(ship_id, owner_id, position, SHIP section, DYNO section)]"""
    out = []
    for sh in (s for s in sp.flatten(tree) if s.tag == b'SHIP'):
        sid   = struct.unpack_from('<I', blob, sh.payload)[0]
        pos   = struct.unpack_from('<3f', blob, sh.payload + 4)
        owner = struct.unpack_from('<I', blob, sh.payload + 16)[0]
        dyno  = next((c for c in sh.children if c.tag == b'DYNO'), None)
        out.append((sid, owner, pos, sh, dyno))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('blob', help='captured savegame payload (.b64) or .raw blob')
    ap.add_argument('--ship', type=int, help='ship object id')
    ap.add_argument('--target-x', type=float)
    ap.add_argument('--target-y', type=float)
    ap.add_argument('--target-z', type=float)
    ap.add_argument('--order-type', type=int, default=1,
                    help='1=Move 2=Scout 3=Colonize 4=Attack 5=Conquer 7=MoveNear')
    ap.add_argument('--progress', type=float, default=0.0,
                    help='distance already travelled; leave 0 for a fresh order')
    ap.add_argument('--list', action='store_true', help='list ships and exit')
    ap.add_argument('-o', '--out', default='loadgame_blob.b64',
                    help='wire-format output for loadgame to serve. A relative '
                         'path is resolved against the directory holding '
                         'cs_server.py, not against this script, so the default '
                         'lands where the server looks for it. Empty to skip.')
    ap.add_argument('--dat', default='',
                    help='ALSO write the raw blob to this .dat path. The client '
                         'loads a .dat given as its command-line argument during '
                         'startup, on the main thread, with no server involved: '
                         '0x0056E7F0 accepts argv[0] when it ends in .dat and '
                         'exists, then 0x0056DAD0 feeds it to the loader. That '
                         'path does NOT base64-decode or inflate '
                         '(0x005E53E0 only reads the file), so the .dat holds the '
                         "raw blob starting with 'SAVE' — not the wire format.")
    args = ap.parse_args()

    blob = sp.load_any(args.blob)
    tree = sp.parse_blob(blob)
    ships = ship_sections(blob, tree)

    if args.list:
        print(f"{len(ships)} ships in {args.blob}")
        for sid, owner, pos, sh, dyno in ships:
            d = parse_dyno(blob, dyno)
            print(f"  id={sid} owner={owner} pos=({pos[0]:.4f}, {pos[1]:.4f}, "
                  f"{pos[2]:.4f}) orbit={d['orbit_id']} "
                  f"order_type={d['shco'][0]} has_orders={d['has_orders']} "
                  f"rout={'yes' if d['rout'] else 'no'}")
        return 0

    missing = [n for n, v in (('--ship', args.ship), ('--target-x', args.target_x),
                              ('--target-y', args.target_y),
                              ('--target-z', args.target_z)) if v is None]
    if missing:
        sys.exit(f"required unless --list: {', '.join(missing)}")

    match = [t for t in ships if t[0] == args.ship]
    if not match:
        sys.exit(f"ship id {args.ship} not found; --list to see what is there")
    sid, owner, pos, sh, dyno = match[0]

    d = parse_dyno(blob, dyno)
    print(f"ship {sid}: owner={owner} pos=({pos[0]:.4f}, {pos[1]:.4f}, {pos[2]:.4f})")
    print(f"  before: DYNO {dyno.size} bytes, order_type={d['shco'][0]}, "
          f"has_orders={d['has_orders']}, rout={'yes' if d['rout'] else 'no'}")

    target = (args.target_x, args.target_y, args.target_z)
    leg    = sp.make_leg(pos, target)
    print(f"  leg: {tuple(round(v, 4) for v in pos)} -> "
          f"{tuple(round(v, 4) for v in target)} length={leg['length']:.4f}")

    # ref_id 0 is the safe choice: both captured orders carried the static null
    # reference node 0x00857C54, so 0 is a value the loader is known to accept.
    rout_payload = sp.build_rout({
        'order_kind': 1,               # non-zero or Ship::SetOrder frees the order
        'flag_1': 0,
        'origin_x': pos[0], 'origin_y': pos[1], 'origin_z': pos[2],
        'target_x': target[0], 'target_y': target[1], 'target_z': target[2],
        'legs_a': [leg], 'legs_b': [],
        'progress': args.progress,
        # +80/84/88 is the origin triple again
        'field_80': struct.unpack('<I', struct.pack('<f', pos[0]))[0],
        'field_84': struct.unpack('<I', struct.pack('<f', pos[1]))[0],
        'field_88': struct.unpack('<I', struct.pack('<f', pos[2]))[0],
        'ref_id': 0,
    }, version=1)[8:]                  # strip the header; build_dyno re-adds it

    d['shco'][0]    = args.order_type
    d['has_orders'] = 1
    d['rout']       = (1, rout_payload)
    d['tail_u32']   = d['tail_u32'] or 0

    new_dyno = build_dyno(d)
    print(f"  after:  DYNO {len(new_dyno)} bytes, order_type={args.order_type}, "
          f"has_orders=1, rout={len(rout_payload)} byte payload")

    out_blob = sp.replace_payload(blob, tree, dyno, new_dyno)

    # re-parse the result and prove the edit survived a full round trip
    check_tree  = sp.parse_blob(out_blob)
    check_ships = ship_sections(out_blob, check_tree)
    got = [t for t in check_ships if t[0] == args.ship]
    if not got:
        sys.exit("re-parse lost the ship — refusing to write")
    cd = parse_dyno(out_blob, got[0][4])
    if not cd['rout'] or cd['shco'][0] != args.order_type or cd['has_orders'] != 1:
        sys.exit("re-parse did not see the injected order — refusing to write")
    routs = [s for s in sp.flatten(check_tree) if s.tag == b'ROUT']
    print(f"  re-parse: {len(routs)} ROUT sections "
          f"(was {len([s for s in sp.flatten(tree) if s.tag == b'ROUT'])}), "
          f"blob {len(blob)} -> {len(out_blob)} bytes")

    here = server_dir()
    if args.out:
        out_path = args.out
        if not os.path.isabs(out_path):
            out_path = os.path.join(here, out_path)
        with open(out_path, 'w', encoding='ascii') as f:
            f.write(sp.encode_save(out_blob).decode('ascii'))
        print(f"wrote {out_path} ({len(out_blob)} bytes -> wire format)")
        print("  loadgame will serve this blob")
    if args.dat:
        dat_path = args.dat
        if not os.path.isabs(dat_path):
            dat_path = os.path.join(here, dat_path)
        if not dat_path.lower().endswith('.dat'):
            sys.exit("--dat path must end in .dat or the client will not accept it")
        with open(dat_path, 'wb') as f:
            f.write(out_blob)
        print(f"wrote {dat_path} ({len(out_blob)} raw bytes, starts "
              f"{out_blob[:4]!r})")
        print(f'  launch: CosmicSupremacy_Resurgence.exe "{dat_path}"')
    return 0


if __name__ == '__main__':
    sys.exit(main())
