"""
settle_watch.py — sample Planet:368 every turn, so a capture is caught live
===========================================================================
    python settle_watch.py                    # follow until Ctrl-C
    python settle_watch.py --turns 40 --out settle.csv

Planet:368 is SETTLEDNESS: 8 on a long-held planet, knocked down when the planet
changes hands, climbing back to 8 over turns. It was found by diffing two
captures against controls ELEVEN turns apart, which is enough to see that it
recovers and nowhere near enough to see HOW — and the open question is exactly a
rate question: does an occupying garrison speed the recovery, or is this simply
"turns since acquisition" and the garrison buys nothing?

An eleven-turn diff cannot answer that. Per-turn samples can, so this records
every owned planet's settledness, garrison and owner once per turn boundary and
writes a CSV that can be read as a trace per planet.

Run it ALONGSIDE duel.py rather than instead of it: the rules are what cause
captures, and this only reads. Two processes reading the same game is fine —
nothing here writes, not even the turn length.
"""
import argparse
import csv
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ai_player"))

import ejbo_viewer as ev
import gamestate as gs

SETTLEDNESS = 368


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=0, help="0 = until Ctrl-C")
    ap.add_argument("--out", default=os.path.join(HERE, "settle.csv"))
    a = ap.parse_args()

    snap = gs.Snapshot()
    h = snap.state.handle
    seen = None
    rows = 0
    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["turn", "planet", "owner", "settledness", "military",
                    "pop", "colonised_turn"])
        print(f"writing {a.out}; sampling Planet:{SETTLEDNESS} each turn")
        try:
            while True:
                b = ev.read_bytes(h, gs.TURN_COUNTER, 4)
                if not b:
                    print("lost the process")
                    return
                t = struct.unpack("<I", b)[0]
                if t == seen:
                    time.sleep(2.0)
                    continue
                time.sleep(2.0)          # let resolution finish before reading
                cur = gs.Snapshot(snap.state)
                unsettled = []
                for p in cur.planets:
                    if p.owner is None:
                        continue
                    s = p.u32(SETTLEDNESS)
                    w.writerow([cur.turn, p.id, p.owner.civ_name, s,
                                p.military, len(p.population),
                                p.colonised_turn])
                    if s is not None and s < 8:
                        unsettled.append((p.id, p.owner.civ_name, s,
                                          p.military))
                fh.flush()
                seen = cur.turn
                rows += 1
                if unsettled:
                    print(f"turn {cur.turn}: " + ", ".join(
                        f"#{i} {o} settledness={s} garrison={m}"
                        for i, o, s, m in unsettled))
                else:
                    print(f"turn {cur.turn}: every owned planet settled (8)")
                if a.turns and rows >= a.turns:
                    return
        except KeyboardInterrupt:
            print(f"\nstopped after {rows} turn(s); {a.out}")


if __name__ == "__main__":
    main()
