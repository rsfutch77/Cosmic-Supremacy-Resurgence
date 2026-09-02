"""
set_turnlength.py — change a galaxy's CONFIGURED turn length in a save blob
===========================================================================
    python set_turnlength.py <capture.b64|.dat> --secs 10 --dat out.dat

"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp


def gset_entries(blob, payload):
    """[(name, type_code, value_offset, value)] for an int32-shaped entry."""
    pos = payload
    n = struct.unpack_from("<I", blob, pos)[0]
    pos += 4
    out = []
    for _ in range(n):
        nlen = struct.unpack_from("<I", blob, pos)[0]; pos += 4
        name = blob[pos:pos + nlen].decode("ascii", "replace"); pos += nlen
        tc = struct.unpack_from("<I", blob, pos)[0]; pos += 4
        if tc == 0:
            pos += 1                                   # custom flag
            out.append((name, tc, pos,
                        struct.unpack_from("<i", blob, pos)[0]))
            pos += 4
        elif tc == 1:
            pos += 1
            out.append((name, tc, pos,
                        struct.unpack_from("<ii", blob, pos)))
            pos += 8
        elif tc == 2:
            out.append((name, tc, pos,
                        struct.unpack_from("<h", blob, pos)[0]))
            pos += 2
        elif tc == 3:
            ln = struct.unpack_from("<I", blob, pos)[0]; pos += 4
            out.append((name, tc, pos,
                        blob[pos:pos + ln].decode("utf-8", "replace")))
            pos += ln
        elif tc == 4:
            pos += 1
            cnt = struct.unpack_from("<I", blob, pos)[0]; pos += 4
            out.append((name, tc, pos,
                        list(struct.unpack_from(f"<{cnt}I", blob, pos))))
            pos += 4 * cnt
        else:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--secs", type=int, default=None)
    ap.add_argument("--dat", default=None)
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()

    raw = (open(a.capture, "rb").read() if a.capture.endswith(".dat")
           else sp.decode_save(open(a.capture).read()))
    blob = bytearray(raw)
    g = blob.find(b"GSET")
    if g < 0:
        sys.exit("no GSET section")
    entries = gset_entries(bytes(blob), g + 8)
    print(f"{os.path.basename(a.capture)}: {len(blob):,} bytes, "
          f"{len(entries)} setting(s)")
    for name, tc, off, val in entries:
        mark = "  <--" if name == "turnlength" else ""
        print(f"  {name:<20} type {tc}  @{off}  = {val}{mark}")

    if a.secs is None:
        print("\nno --secs given; nothing changed")
        return
    hit = next((e for e in entries if e[0] == "turnlength"), None)
    if hit is None:
        sys.exit("this galaxy has no 'turnlength' setting")
    _n, tc, off, old = hit
    if tc != 0:
        sys.exit(f"turnlength is type {tc}, not the int32 this rewrites")
    struct.pack_into("<i", blob, off, a.secs)
    print(f"\nturnlength {old} -> {a.secs} (in place, no section resized)")

    out = a.out or (a.capture if a.capture.endswith(".b64")
                    else None)
    if out and out.endswith(".b64"):
        enc = sp.encode_save(bytes(blob))
        with open(out, "w") as f:
            f.write(enc if isinstance(enc, str) else enc.decode("ascii"))
        print(f"wrote {out}")
    if a.dat:
        with open(a.dat, "wb") as f:
            f.write(bytes(blob))
        print(f"wrote {a.dat}")


if __name__ == "__main__":
    main()
