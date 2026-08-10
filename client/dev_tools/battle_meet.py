"""
battle_meet.py — put two civs' warships in the same place and watch
===================================================================
    python battle_meet.py                       # report, order nothing
    python battle_meet.py --apply               # send the fleets together
    python battle_meet.py --apply --attacker BadGuy --defender GoodGuy

THE QUESTION THIS ANSWERS. STRATEGY.md §4.4 has said since the first battle that
"combat triggers on CO-LOCATION" and that a Move order which delivers the hull
does the whole job. Measured on the AI-vs-AI galaxy, that is not sufficient:
both civs reached war, both fleets flew, both ARRIVED at enemy planets, and
nothing happened for fifty turns — because R-XTM-03 targets the WEAKEST enemy
planet, which on an expanded civ means an undefended colony with nothing aboard
to fight.

That leaves the underlying capability untested. The one battle this project has
seen was fleet-versus-DEFENDED-PLANET (weakness 500). Whether two fleets meeting
in open space fight each other has never been observed, and both R-XTM-02
(defend) and any future interception rule assume it does.

So this does the smallest thing that settles it: take the attacker's warships,
send them to where the defender's warship actually IS, and step turn boundaries
until they are co-located, sampling `Ship:136` condition and the ship roster at
every step. It changes no rules and teaches the AI nothing — it is a probe.

WHAT COUNTS AS AN ANSWER
------------------------
Predictions are registered before the run, because "something happened" is not a
result:

  * co-location, and a fight   -> some ship's condition falls below 1.0, or a
                                  ship disappears from the scan (ships are
                                  DELETED on death, not marked)
  * co-location, and nothing   -> every condition still 1.0 after several
                                  boundaries with the hulls at the same point.
                                  Then combat needs more than position, and the
                                  §4.4 doctrine is wrong in a second way

Take a checkpoint first. `checkpoint.py save` costs seconds and this deliberately
throws hulls at each other.
"""
import argparse
import math
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ai_player"))

import actions
import ejbo_viewer as ev
import exterminate
import gamestate as gs
import remote

TURNLENGTH = 0x0080AA08


def log(m):
    print(m, flush=True)


def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def warships(snap, civ):
    return [s for s in snap.owned_ships(civ) if s.role == "WARSHIP"]


def roster(snap):
    """{shipId: (civ, condition)} — the thing a death shows up in."""
    out = {}
    for civ in snap.civs:
        for s in snap.owned_ships(civ):
            out[s.id] = (civ.civ_name, s.condition)
    return out


def report(snap, attacker, defender):
    for civ in (attacker, defender):
        for s in warships(snap, civ):
            orb = s.orbiting
            log(f"  {civ.civ_name:>8} #{s.id:<4} {s.design.design_name if s.design else '?':<8} "
                f"cond {s.condition:.3f} order {s.order_type} "
                f"pos ({s.pos[0]:.0f}, {s.pos[1]:.0f}, {s.pos[2]:.0f}) "
                f"orbiting #{orb.id if orb else None}")


def turn_of(snap):
    b = ev.read_bytes(snap.state.handle, gs.TURN_COUNTER, 4)
    return struct.unpack("<I", b)[0] if b and len(b) == 4 else None


def wait_turn(snap, after, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = turn_of(snap)
        if t is not None and t > after:
            time.sleep(1.5)          # let resolution settle before reading
            return t
        time.sleep(1.0)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--attacker", default="GoodGuy")
    ap.add_argument("--defender", default="BadGuy")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--turns", type=int, default=8)
    ap.add_argument("--secs", type=int, default=20)
    a = ap.parse_args()

    snap = gs.Snapshot()
    atk = snap.civ(a.attacker)
    dfn = snap.civ(a.defender)
    if atk is None or dfn is None:
        sys.exit(f"need both civs; saw {[c.civ_name for c in snap.civs]}")

    log(f"=== turn {snap.turn} ===")
    report(snap, atk, dfn)

    mine = warships(snap, atk)
    theirs = warships(snap, dfn)
    if not mine or not theirs:
        sys.exit(f"need a warship on each side: {a.attacker} has {len(mine)}, "
                 f"{a.defender} has {len(theirs)}")

    # Aim at the defender's ship that our hulls can reach soonest, and aim at
    # the SHIP rather than at a planet: a planet is what the AI already targets
    # and it is exactly what produced fifty turns of nothing.
    quarry = min(theirs, key=lambda t: min(dist(t.pos, m.pos) for m in mine))
    gap = min(dist(quarry.pos, m.pos) for m in mine)
    log(f"\nquarry: {a.defender} #{quarry.id} at "
        f"({quarry.pos[0]:.0f}, {quarry.pos[1]:.0f}, {quarry.pos[2]:.0f}), "
        f"nearest attacker {gap:.1f} away")

    if not a.apply:
        log("\nreport only; re-run with --apply to send them in")
        return

    original = struct.unpack("<I", ev.read_bytes(snap.state.handle,
                                                 TURNLENGTH, 4))[0]
    rem = remote.Remote(snap.state.pid, log=log)
    act = actions.Actuator(snap, dry_run=False, log=log, remote_=rem)
    before = roster(snap)
    try:
        for s in mine:
            log(f"  sending #{s.id} to the quarry")
            exterminate.send_to(act, s, quarry, atk)

        ev.write_bytes(snap.state.handle, TURNLENGTH, struct.pack("<I", a.secs))
        t = turn_of(snap)
        for step in range(a.turns):
            t = wait_turn(snap, t)
            if t is None:
                log("[!] no turn boundary — is the client alive?")
                return
            cur = gs.Snapshot(snap.state)
            atk_c, dfn_c = cur.civ(a.attacker), cur.civ(a.defender)
            if atk_c is None or dfn_c is None:
                log("[!] lost a civ from the scan")
                return
            now = roster(cur)
            closest = None
            for m in warships(cur, atk_c):
                for q in warships(cur, dfn_c):
                    d = dist(m.pos, q.pos)
                    closest = d if closest is None else min(closest, d)
            log(f"\n--- turn {t} --- closest hostile pair: "
                f"{'n/a' if closest is None else f'{closest:.2f}'} units")
            report(cur, atk_c, dfn_c)

            gone = set(before) - set(now)
            hurt = [i for i in set(before) & set(now)
                    if now[i][1] < before[i][1] - 1e-6]
            if gone:
                log(f"  *** SHIPS DESTROYED: "
                    f"{', '.join(f'#{i} ({before[i][0]})' for i in sorted(gone))}")
            if hurt:
                for i in sorted(hurt):
                    log(f"  *** DAMAGE: #{i} ({now[i][0]}) "
                        f"{before[i][1]:.3f} -> {now[i][1]:.3f}")
            if gone or hurt:
                log("\nANSWER: ships fight ships on co-location.")
                return
            before = now
        log(f"\nANSWER: {a.turns} boundaries with no damage and no losses. "
            f"If the hulls are co-located above, position alone does NOT start "
            f"a fight and §4.4 needs correcting a second time.")
    finally:
        ev.write_bytes(snap.state.handle, TURNLENGTH,
                       struct.pack("<I", original))
        rem.close()


if __name__ == "__main__":
    main()
