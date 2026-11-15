"""
set_turnlength.py: change a galaxy's configured turn length in a save blob
    python set_turnlength.py <capture.b64|.dat> --secs 10 --dat out.dat
    python set_turnlength.py clean.dat --set waronly=0 --dat peace.dat

`--set NAME=VALUE` rewrites any whole-number or bool setting in place, and
may be repeated. A bool takes 0 or 1.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--secs", type=int, default=None)
    ap.add_argument("--set", action="append", default=[],
                    metavar="NAME=VALUE", help="any int32 or int16 setting")
    ap.add_argument("--dat", default=None)
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()

    raw = (open(a.capture, "rb").read() if a.capture.endswith(".dat")
           else sp.decode_save(open(a.capture).read()))
    blob = bytearray(raw)
    settings = sp.gset_of(bytes(blob))
    if not settings:
        sys.exit("no GSET section")
    print(f"{os.path.basename(a.capture)}: {len(blob):,} bytes, "
          f"{len(settings)} setting(s)")
    for name, e in settings.items():
        mark = "  <--" if name == "turnlength" else ""
        print(f"  {name:<24} {e['type']:<10} @{e.get('offset')}  = "
              f"{e['value']}{mark}")

    changes = [("turnlength", a.secs)] if a.secs is not None else []
    for spec in a.set:
        name, _eq, value = spec.partition("=")
        try:
            changes.append((name, int(value)))
        except ValueError:
            sys.exit(f"--set wants NAME=NUMBER, got {spec!r}")
    if not changes:
        print("\nno --secs or --set given; nothing changed")
        return
    print()
    try:
        blob = bytearray(sp.set_gset(bytes(blob), dict(changes)))
    except ValueError as e:
        sys.exit(str(e))
    for name, value in changes:
        print(f"{name} {settings[name]['value']} -> {value} "
              f"(in place, no section resized)")

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
