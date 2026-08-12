"""
bench_pass.py — how fast can a decision pass actually run?
==========================================================
    python bench_pass.py                 # full breakdown, one civ
    python bench_pass.py --civs 3        # what a three-civ turn costs
    python bench_pass.py --repeat 5

TURN LENGTH HAS ALWAYS BEEN GUESSED. `--drive 20` was chosen to be comfortably
slack, never measured, and that is fine for watching a game and useless for
training: a learner needs thousands of games, so the floor on turn length is a
direct multiplier on how long any of this takes. This measures the floor instead
of assuming it.

WHAT IS ACTUALLY BEING PAID FOR, in the order it happens:

  1. `ViewerState.scan()` — enumerate the process's writable regions and search
     every one of them for the EJBO tag, then classify and field-read each
     object found. This is a whole-address-space sweep and it is expected to
     dominate; everything else operates on the result.
  2. `Snapshot` — wrapping those raw objects into the entity model.
  3. the rules — pure computation over the snapshot, plus engine calls through
     remote stubs for anything that needs a lazy stat or a price.

Reported per phase, because the remedy differs: a slow scan is fixed by scanning
ONCE PER TURN and sharing it across civs (duel.py does not, which is a standing
`[ ]`), while a slow rule is fixed by fixing the rule.

READ-ONLY: it runs the rules in dry-run mode, so nothing is written to the game.
Timings taken while a duel is running are INFLATED by contention for the same
process and the same CPU — the numbers are only clean on an idle client.
"""
import argparse
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ai_player"))

import actions
import ai
import ejbo_viewer as ev
import gamestate as gs
import sensors


def timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--civs", type=int, default=0,
                    help="0 = every civ in the galaxy")
    a = ap.parse_args()

    state = ev.ViewerState()
    if not state.connect():
        sys.exit("CosmicSupremacy is not running")

    # -- 1. the scan, on its own ----------------------------------------
    scans = []
    for _ in range(a.repeat):
        _, dt = timed(state.scan)
        scans.append(dt)
    objects = len(state.objects)
    print(f"scan                : {statistics.median(scans):6.3f}s median "
          f"(min {min(scans):.3f}, max {max(scans):.3f}) over {a.repeat} runs, "
          f"{objects} objects")

    # -- 2. the whole snapshot, which includes a scan --------------------
    snaps = []
    for _ in range(a.repeat):
        snap, dt = timed(lambda: gs.Snapshot(state))
        snaps.append(dt)
    snap = gs.Snapshot(state)
    wrap = statistics.median(snaps) - statistics.median(scans)
    print(f"snapshot            : {statistics.median(snaps):6.3f}s median "
          f"(scan {statistics.median(scans):.3f} + wrap {wrap:.3f})")
    print(f"                      {len(snap.planets)} planets, "
          f"{len(snap.ships)} ships, {len(snap.civs)} civs, "
          f"{len(snap.designs)} designs")

    # -- 3. the rules, per civ, dry ------------------------------------
    civs = [c.civ_name for c in snap.civs if c.civ_name]
    if a.civs:
        civs = civs[:a.civs]
    print(f"\nrules, dry run, per civ ({', '.join(civs)}):")
    per_civ = {}
    for name in civs:
        civ = snap.civ(name)
        hist = sensors.History(snap, name, persist=False)
        hist.observe(snap, civ)
        act = actions.Actuator(snap, dry_run=True, log=lambda *_: None)
        total, slow = 0.0, []
        for rule_name, fn in ai.RULES:
            def call(fn=fn, civ=civ, act=act, hist=hist):
                try:
                    if fn is ai.expand.run_xpn02:
                        return fn(snap, civ, act, vision="known", top=0,
                                  hist=hist, log=lambda *_: None)
                    if fn in (ai.exploit.run_food, ai.explore.run_exp02,
                              ai.exploit.run_facilities, ai.exploit.run_jobs,
                              ai.exploit.run_hurry, ai.exploit.run_crew_supply,
                              ai.exploit.run_conscript, ai.explore.run_exp01,
                              ai.exterminate.run_xtm00, ai.exterminate.run_xtm01,
                              ai.exterminate.run_xtm03, ai.exterminate.run_xtm04,
                              ai.exterminate.run_xtm05, ai.exterminate.run_xtm06,
                              ai.exterminate.run_xtm07):
                        return fn(snap, civ, act, hist, log=lambda *_: None)
                    return fn(snap, civ, act, log=lambda *_: None)
                except Exception:
                    return None
            _, dt = timed(call)
            total += dt
            slow.append((dt, rule_name))
        per_civ[name] = total
        slow.sort(reverse=True)
        top = ", ".join(f"{n} {d * 1000:.0f}ms" for d, n in slow[:3])
        print(f"  {name:>10}: {total:6.3f}s total   slowest: {top}")

    # -- 4. what that means for a turn ---------------------------------
    scan_med = statistics.median(scans)
    snap_med = statistics.median(snaps)
    rules_med = statistics.median(per_civ.values()) if per_civ else 0.0
    n = len(civs)
    now = n * (snap_med + rules_med)
    shared = snap_med + n * rules_med
    print(f"\nfor {n} civ(s) per turn:")
    print(f"  as duel.py runs today (a snapshot each): {now:6.2f}s")
    print(f"  sharing ONE snapshot per turn          : {shared:6.2f}s "
          f"({now - shared:.2f}s saved, {100 * (now - shared) / now:.0f}%)")
    for k in (2, 3, 8, 20):
        print(f"  {k:>2} civs, shared snapshot            : "
              f"{snap_med + k * rules_med:6.2f}s")
    print(f"\nthe scan is {100 * scan_med / max(snap_med + rules_med, 1e-9):.0f}%"
          f" of a shared-snapshot turn, so it is the thing to attack first "
          f"if this is ever a training loop")


if __name__ == "__main__":
    main()
