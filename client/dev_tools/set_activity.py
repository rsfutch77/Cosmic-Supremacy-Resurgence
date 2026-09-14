"""
set_activity.py , stop an offline galaxy decaying from "inactivity"
===================================================================
    python set_activity.py            # show every civ's state
    python set_activity.py --apply    # mark EVERY civ active
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "ai_player"))
import gamestate as gs

ACTIVE_FLAG = 712      # Owner:712 , latched for this turn
PENDING_FLAG = 720     # Owner:720 , the signal we set
BASELINE = 716         # Owner:716 , the engine's own timer, DO NOT WRITE
DEFAULT_OFFSET = 72    # GetGameOption(4)->[+0x30] == 0
DEFAULT_DIVISOR = 100


def pct_for(turn, baseline, offset=DEFAULT_OFFSET, divisor=DEFAULT_DIVISOR):
    return max(0, min(100, (turn - offset - baseline) * 100 // divisor))


def report(snap):
    print(f"turn {snap.turn}")
    print(f"  {'civ':12s} {'active':>7s} {'pending':>8s} {'baseline':>9s} "
          f"{'inactivity':>11s}")
    for c in snap.civs:
        a = snap.read(c.addr + ACTIVE_FLAG, 1)[0]
        p = snap.read(c.addr + PENDING_FLAG, 1)[0]
        b = c.i32(BASELINE)
        print(f"  {c.civ_name!r:12s} {a:7d} {p:8d} {b:9d} "
              f"{pct_for(snap.turn, b):10d}%")


def apply(snap):
    """Set the pending activity flag on EVERY civ."""
    done = []
    for c in snap.civs:
        addr = c.addr + PENDING_FLAG
        before = snap.read(addr, 1)[0]
        if before == 1:
            print(f"  {c.civ_name!r}: already flagged active")
            done.append(c)
            continue
        if not gs.ev.write_bytes(snap.h, addr, b"\x01"):
            print(f"  {c.civ_name!r}: WRITE FAILED at 0x{addr:08X}")
            continue
        print(f"  {c.civ_name!r}: Owner:720 {before} -> 1 (0x{addr:08X})")
        done.append(c)
    if len(done) != len(snap.civs):
        print("\n  [!] NOT every civ was marked. Leaving it partial would hand "
              "whoever got the flag a compounding food advantage , fix the "
              "failures and re-run before playing on.")
    else:
        print(f"\n  all {len(done)} civ(s) marked active; the engine refreshes "
              f"Owner:716 itself after two turn boundaries")


def main():
    snap = gs.Snapshot()
    report(snap)
    if "--apply" not in sys.argv:
        print("\nre-run with --apply to mark every civ active")
        return
    print()
    apply(snap)


if __name__ == "__main__":
    main()
