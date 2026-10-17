"""
set_civ_id.py: read or rewrite a civ's Owner:4 in a galaxy blob
    python set_civ_id.py client/SinglePlayerGalaxy.dat
    python set_civ_id.py client/SinglePlayerGalaxy.dat --civ DemoPlayer --id 20 \
        --out client/SinglePlayerGalaxy_uid20.dat

`Owner:4` is the last u32 of an OWNR payload: the per-civ id the score list
keys on. Two civs sharing one value collapse to a single row.

Galaxies generated through the TestBed pass token carry `0` here for the human
civ and a real id for the rival, and in those galaxies the human's empire-level
fields (research `Owner:12`, `Owner:24`, score `Owner:28`) do not tick:

    SinglePlayerGalaxy.dat   DemoPlayer=0    BadGuy=21
    fog_test.dat             DemoPlayer=0    BadGuy=21
    clean.dat                GoodGuy=20      BadGuy=21
    e2e.dat                  GoodGuy=20      BadGuy=21

Rewriting only this field and launching the result tests whether it is the
cause. Writing touches four bytes in place; no section length changes.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import inject_civ as icv                                    # noqa: E402
import save_parser as sp                                    # noqa: E402


def load(path: str) -> bytes:
    """A raw .dat, or a base64 .b64 capture."""
    with open(path, "rb") as fh:
        data = fh.read()
    if path.endswith(".b64"):
        return sp.decode_save(data)
    return data


def civ_ids(blob: bytes):
    """[(name, owner4, offset of that u32)] in blob order."""
    return [(o["name"], struct.unpack_from("<I", blob, o["end"] - 4)[0],
             o["end"] - 4)
            for o in icv.owner_records(blob)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("blob", help="a galaxy .dat, or a .b64 capture")
    ap.add_argument("--civ", help="civ to rewrite (default: report only)")
    ap.add_argument("--id", type=int, help="the value to write")
    ap.add_argument("--out", help="where to write the result")
    a = ap.parse_args()

    blob = load(a.blob)
    rows = civ_ids(blob)
    if not rows:
        raise SystemExit(f"no OWNR records in {a.blob}")
    for name, uid, off in rows:
        print(f"  {name:<14} Owner:4 = {uid:<6} at offset {off}")

    if a.civ is None:
        return 0
    if a.id is None or a.out is None:
        raise SystemExit("--civ needs both --id and --out")

    match = [r for r in rows if r[0] == a.civ]
    if not match:
        raise SystemExit(f"no civ named {a.civ!r}; saw {[r[0] for r in rows]}")
    if len(match) > 1:
        raise SystemExit(f"{len(match)} civs are named {a.civ!r}")
    name, uid, off = match[0]

    # The score list holds one row per distinct id, so a duplicate hides a player.
    clash = [r[0] for r in rows if r[0] != name and r[1] == a.id]
    if clash:
        raise SystemExit(f"{a.id} is already {clash[0]!r}'s id; pick another")

    buf = bytearray(blob)
    struct.pack_into("<I", buf, off, a.id)
    with open(a.out, "wb") as fh:
        fh.write(bytes(buf))
    print(f"\n{name}: Owner:4 {uid} -> {a.id}")
    print(f"wrote {a.out} ({len(buf)} bytes, 4 changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
