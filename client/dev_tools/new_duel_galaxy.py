"""
new_duel_galaxy.py — turn a fresh two-civ galaxy into an N-civ AI testbed
========================================================================
    # with a brand-new galaxy already running (drag DemoGalaxy_local.csgalaxy
    # onto the EXE, or launch it with that as the argument):
    python new_duel_galaxy.py --add Ceti --secs 5
    python new_duel_galaxy.py --add Ceti --add Draco --secs 3 --no-relaunch

Everything here is a composition of tools that already exist, in the one order
that works. Doing it by hand is five commands and the order is not obvious:

  1. capture the running galaxy            trigger_save via game_cycle
  2. add each new civ                      inject_civ    (OWNR clone + homeworld)
  3. give each new civ colony ships        inject_ship   (the generator gives 2)
  4. give EVERY civ a scout design         inject_design (via game_cycle)
  5. set the galaxy's own turn length      set_turnlength
  6. relaunch on the result

WHY THE ORDER MATTERS. A civ has to exist before it can be given a ship, and a
ship's SHPR names a design id, so the civ's designs have to exist first — but
step 4 gives designs to "every civ", which means it has to run after the new civs
are in. Ships are injected before designs only because inject_ship defaults to
the civ's FIRST design, which is the cloned Colony Ship either way.

WHAT IT DELIBERATELY DOES NOT DO. No armed design and no troop transport: at turn
0 no weapon and no troop bay is researched, and fitting parts a civ has not
unlocked is the §1.1 violation that produced illegal military camps once already.
Those go in later, with `game_cycle.py --all-civs --design ...`, once R-XPL-04
has bought the research. A scout is legal from turn 0 — chassis and engines only.

The turn length is set in `GSET`, not driven: the engine re-applies it at every
boundary, so the galaxy keeps its own time and the controller never races it.
Below 60s needs `patch_turn_floor.py --apply` or the engine clamps it.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.dirname(HERE)
REPO = os.path.dirname(CLIENT)
SAVES = os.path.join(REPO, "server", "saves")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ai_player"))
sys.path.insert(0, os.path.join(REPO, "server", "dev_tools"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", action="append", default=[],
                    help="name of a civ to add; repeatable")
    ap.add_argument("--ships", type=int, default=2,
                    help="colony ships per added civ (the generator gives 2)")
    ap.add_argument("--secs", type=int, default=None,
                    help="galaxy turn length; below 60 needs patch_turn_floor")
    ap.add_argument("--scout", default="scout1:chassis=0,engine=0,engine=0,"
                                       "engine=0,engine=0",
                    help="design given to every civ; must use only parts a "
                         "turn-0 civ has unlocked")
    ap.add_argument("--dat", default=os.path.join(CLIENT, "duel_galaxy.dat"))
    ap.add_argument("--no-relaunch", action="store_true")
    a = ap.parse_args()

    import game_cycle as gc
    import save_parser as sp
    import inject_civ as icv
    import inject_ship as ish
    import set_turnlength as stl

    print("1. capturing the running galaxy")
    capture = gc.capture_save("duelsetup")
    blob = sp.decode_save(open(capture).read())
    icv.describe(blob)

    for name in a.add:
        print(f"\n2. adding {name!r}")
        blob, _home = icv.add_civ(blob, name)
        for i in range(a.ships):
            print(f"\n3. ship {i + 1}/{a.ships} for {name!r}")
            blob = ish.add_ship(blob, name)

    if a.scout:
        print(f"\n4. giving every civ {a.scout.split(':')[0]!r}")
        tmp = os.path.join(SAVES, "_duelsetup_designs.b64")
        enc = sp.encode_save(blob)
        with open(tmp, "w") as f:
            f.write(enc if isinstance(enc, str) else enc.decode("ascii"))
        out = os.path.join(REPO, "server", "loadgame_blob.b64")
        blob = gc.inject_designs(tmp, [gc.parse_design(a.scout)], out,
                                 all_civs=True)

    if a.secs is not None:
        print(f"\n5. galaxy turn length -> {a.secs}s")
        entries = stl.gset_entries(blob, blob.find(b"GSET") + 8)
        hit = next(e for e in entries if e[0] == "turnlength")
        buf = bytearray(blob)
        import struct
        struct.pack_into("<i", buf, hit[2], a.secs)
        blob = bytes(buf)
        if a.secs < 60:
            print("   (below the engine's 60s floor — needs "
                  "patch_turn_floor.py --apply, or it will be clamped)")

    with open(a.dat, "wb") as f:
        f.write(blob)
    print(f"\nwrote {a.dat} ({len(blob):,} bytes)")
    icv.describe(blob)
    ish.describe(blob)

    if not a.no_relaunch:
        print("\n6. relaunching")
        gc.close_client()
        gc.launch(a.dat)
        print("\nNow mark every civ active (set_activity.py --apply) and start "
              "duel.py. No --drive is needed once the galaxy carries its own "
              "turn length, beyond one write to start the first boundary.")


if __name__ == "__main__":
    main()
