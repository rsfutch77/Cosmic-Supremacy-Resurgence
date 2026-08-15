"""
combat_watch.py — report every shot fired, as it happens
========================================================
    python combat_watch.py                 # follow until Ctrl-C
    python combat_watch.py --turns 200 --out combat.csv

Ships are DELETED on death rather than flagged, and `Ship:136` condition is the
only per-ship damage state, so a battle leaves exactly two traces: a condition
that fell, and a hull that stopped existing. Both are invisible in a standings
line — two civs can fight, lose ships and rebuild between two samples of "score
and planet count" with nothing to show for it.

This samples every ship each turn and prints only the differences, so a run can
be left alone and the first engagement is still caught with the turn number, the
civs involved and what it cost them. It writes nothing to the game.

Run it ALONGSIDE duel.py: the rules are what cause the fight, and reading the
same process from two programs is fine.
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


def fleet(snap):
    """{shipId: (civ, design, condition, position)}"""
    out = {}
    for civ in snap.civs:
        for s in snap.owned_ships(civ):
            d = s.design
            out[s.id] = (civ.civ_name, d.design_name if d else "?",
                         s.condition, s.pos)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(HERE, "combat.csv"))
    a = ap.parse_args()

    state = ev.ViewerState()
    if not state.connect():
        sys.exit("client is not running")
    h = state.handle
    prev, seen_turn, turns = {}, None, 0
    fights = 0

    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["turn", "event", "ship", "civ", "design",
                    "condition_before", "condition_after"])
        print(f"watching; writing {a.out}")
        while True:
            b = ev.read_bytes(h, gs.TURN_COUNTER, 4)
            if not b:
                print("lost the process")
                return
            t = struct.unpack("<I", b)[0]
            if t == seen_turn:
                time.sleep(1.0)
                continue
            time.sleep(1.5)                    # let resolution finish
            snap = gs.Snapshot(state)
            cur = fleet(snap)
            if prev:
                for sid, (civ, design, cond, _p) in cur.items():
                    was = prev.get(sid)
                    if was and cond is not None and was[2] is not None \
                            and cond < was[2] - 1e-6:
                        fights += 1
                        print(f"  turn {snap.turn}: DAMAGE  {civ} #{sid} "
                              f"{design}  {was[2]:.3f} -> {cond:.3f}")
                        w.writerow([snap.turn, "damage", sid, civ, design,
                                    f"{was[2]:.4f}", f"{cond:.4f}"])
                for sid, (civ, design, cond, _p) in prev.items():
                    if sid not in cur:
                        fights += 1
                        print(f"  turn {snap.turn}: DESTROYED  {civ} #{sid} "
                              f"{design}  (was {cond:.3f})")
                        w.writerow([snap.turn, "destroyed", sid, civ, design,
                                    f"{cond:.4f}", ""])
                fh.flush()
            prev, seen_turn = cur, t
            turns += 1
            if turns % 25 == 0:
                by = {}
                for civ, _d, _c, _p in cur.values():
                    by[civ] = by.get(civ, 0) + 1
                print(f"  [turn {snap.turn}] {turns} sampled, {fights} combat "
                      f"event(s) so far; fleets {by}")
            if a.turns and turns >= a.turns:
                print(f"\n{turns} turns sampled, {fights} combat event(s)")
                return


if __name__ == "__main__":
    main()
