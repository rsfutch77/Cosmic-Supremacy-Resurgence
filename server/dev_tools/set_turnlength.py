"""
set_turnlength.py — change a galaxy's CONFIGURED turn length in a save blob
===========================================================================
    python set_turnlength.py <capture.b64|.dat> --secs 10 --dat out.dat

WHY THIS RATHER THAN WRITING MEMORY. `ai.py --drive` writes the live turn length
at `0x0080AA08` directly, and the engine overwrites it again at every turn
boundary — the loop's own log shows it re-asserting once per turn, forever. That
works and it is fragile: it depends on the re-assert landing every turn for the
life of a run, and a run where it stops landing resolves turns nobody decided on.

`3600` is also exactly what `GSET` carries as this galaxy's `turnlength`, which
makes the per-turn refresh look like a read of the galaxy config rather than a
clamp. If that is right, setting the CONFIG makes the engine's own refresh write
the value we want, and the re-assert loop stops being load-bearing.

An int32 rewritten in place changes no section lengths, so unlike every other
injector here this one needs no ancestor widening and no count bumped.

**IT WORKS — AT EVERY TURN BOUNDARY, NOT AT LOAD.** CONFIRMED: a galaxy set to
`turnlength = 75` was driven, and the engine re-applied **75** at each boundary:

    [loop] waiting for turn 187 (turn length 75s, Ctrl-C to stop)
    [loop] waiting for turn 188 (turn length 75s, Ctrl-C to stop)

**So `GSET.turnlength` is the source of the per-turn refresh**, and a galaxy
configured above the 60-second floor runs at its configured rate with NOTHING
driving it — no `--drive`, no re-assertion, no fight.

This took two wrong answers to reach, both worth knowing:

1. *"Falsified — setting it changes nothing."* The test read the live value
   immediately after load, where it comes up 3600 whatever the config says, and
   never drove a boundary. The refresh only runs AT a boundary, and with a
   3600-second turn the first one is an hour away, so the experiment could not
   have observed the thing it was testing.
2. *"The 60-second clamp fires continuously."* It did — because by then the
   config had been set to 10, so the refresh was writing 10 and the clamp was
   forcing it to 60. That was the refresh finally using the configured value,
   read as evidence against it.

PRACTICAL UPSHOT. For unattended runs, set the galaxy to 61s or more and do not
drive at all: the engine keeps its own time and the controller stops racing it.
Below 61s the clamp applies and needs the one-byte-per-site patch (`3c` → `01`).

**THE 60-SECOND CLAMP DOES FIRE ON US — CORRECTED.** This file previously said
it never had, reasoning that writing `0x0080AA08` directly does not execute the
code guarding it. That reasoning is right and the conclusion was wrong: the
engine periodically re-applies the CURRENT value through the guarded path,

    cmp esi, 60 ; mov [0x0080AA08], esi ; jg skip ; mov [0x0080AA08], 60

so it reads back whatever we wrote, sees 15 (or 20, or 40) is not greater than
60, and forces 60. Caught in a duel log driving 15s turns:

    [loop] waiting for turn 175 (turn length 60s, Ctrl-C to stop)

**So 60 is the engine's real floor and it is enforced against us every turn.**
Anything below it survives only for as long as it takes that path to run again,
which is why `--drive` re-asserts once a second and why turn timing oscillates.
A one-byte change per site (`3c` → `01`) lowers the floor without removing the
check, and is the honest fix if sub-60s turns are ever wanted.

The separate 3600 seen at other times is still unexplained; candidates are a
standalone default, the `GLOB` section, or a server-supplied value that falls
back to an hour offline. `GSET` also carries `primetime_turnlength = -1`, which
may be the other half of whatever picks it.

The tool itself is correct and left in place — it edits the setting it says it
edits, and the setting simply is not the one that matters.
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
