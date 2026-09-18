"""
research_tick_check.py , does a turn add research points, and to whom?
=======================================================================
    python research_tick_check.py                  # read only, no turns fired
    python research_tick_check.py --drive launcher # one turn the launcher's way
    python research_tick_check.py --drive held     # one turn with the countdown held
    python research_tick_check.py --drive both     # one of each, launcher first

Players reported after v0.1 that clicking Next Turn adds no research points to
the selected technology. This is the tool that measured it, and it is kept
because the same reading is the regression test for the fix.

ANSWERED: the human civ was not being empire-ticked at all, on any turn fired
by any means, because the shipped galaxy carries `Owner:4 == 0` for it. Both
rivals gained +40 research a turn while the local player held at 0 for eight
turns; writing a fresh unique id into `Owner:4` started it at +40 immediately.
Cash moves in both states, which is what hid it. Full measurement in
docs/CosmicSupremacy_Memory_Reconstruction_Report.md, "Research/science
accrual"; the shipping fix is server/dev_tools/set_civ_id.py.

The two drive modes remain because they are different code paths and a future
fault may live in one of them:

    launcher   what release/gamectl.py does: write 1 to the turn countdown at
               0x0080AA08, poll until the turn counter moves, then immediately
               restore the countdown. That restore can land inside a boundary
               that has not finished.
    held       what fast_turns.py does: write the countdown low and leave it
               there across the whole boundary.

THIS ADVANCES REAL TURNS in whatever game is running. Run it on a throwaway
game, or save first.
"""
import argparse
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "ai_player"))
sys.path.insert(0, os.path.join(REPO, "release"))

import gamestate as gs                                      # noqa: E402
import research                                             # noqa: E402

TURNLENGTH_ADDR = 0x0080AA08
SCIENTIST = 2


def reading(snap):
    """What every civ's research looks like right now."""
    local = snap.local_civ()
    rows = []
    for civ in snap.civs:
        done = [t for t, _c in civ.completed]
        topic = civ.topic
        rows.append({
            "name": civ.civ_name,
            "local": local is not None and civ.addr == local.addr,
            "stockpile": civ.research,
            "topic": topic,
            "topic_name": "none" if topic in (None, -1) else research.name(topic),
            "topic_cost": (None if topic in (None, -1)
                           else research.cost(topic, len(done))),
            "completed": len(done),
            "planets": len(snap.owned_planets(civ)),
            "scientists": sum(p.jobs.get(SCIENTIST, 0)
                              for p in snap.owned_planets(civ)),
        })
    return rows


def show(rows, before=None):
    for row in rows:
        was = next((b for b in (before or []) if b["name"] == row["name"]), None)
        delta = ("" if was is None or was["stockpile"] is None
                 or row["stockpile"] is None
                 else f"  ({row['stockpile'] - was['stockpile']:+d})")
        cost = "?" if row["topic_cost"] is None else f"{row['topic_cost']:,}"
        print(f"  {'* ' if row['local'] else '  '}{row['name']:<14} "
              f"research {str(row['stockpile']):>8}{delta:<10} "
              f"topic {row['topic_name']} ({cost})  "
              f"{row['completed']} done, {row['planets']} planet(s), "
              f"{row['scientists']} scientist(s)")
    print("  * = the local player, from the 0x00857904 node")


def drive_launcher(snap, timeout=60.0):
    """Exactly what the launcher's Next Turn button does."""
    import gamectl
    with gamectl.Client(snap.state.pid) as c:
        return c.advance_turn(timeout=timeout)


def drive_held(snap, timeout=60.0, poll=0.25):
    """Write the countdown low and leave it there until the turn has landed."""
    import ejbo_viewer as ev
    h = snap.state.handle
    before = snap.rd32(gs.TURN_COUNTER)
    original = snap.rd32(TURNLENGTH_ADDR)
    if original in (None, 0xFFFFFFFF):
        original = 3600
    ev.write_bytes(h, TURNLENGTH_ADDR, struct.pack("<I", 1))
    deadline = time.time() + timeout
    landed = None
    while time.time() < deadline:
        time.sleep(poll)
        now = snap.rd32(gs.TURN_COUNTER)
        if now is not None and now != before:
            landed = now
            break
    # Only now, and after a further settle, does the countdown go back. Holding
    # it through the whole boundary is the entire difference from the launcher.
    time.sleep(3.0)
    ev.write_bytes(h, TURNLENGTH_ADDR, struct.pack("<I", original))
    if landed is None:
        raise SystemExit(f"the turn did not resolve within {timeout:.0f}s")
    return landed


def one_turn(label, drive, timeout):
    snap = gs.Snapshot()
    print(f"\n=== {label}: turn {snap.turn} ===")
    before = reading(snap)
    show(before)
    turn = drive(snap, timeout)
    time.sleep(2.0)                 # let the boundary finish before reading
    snap = gs.Snapshot()
    print(f"--- after, turn {turn} ---")
    after = reading(snap)
    show(after, before)
    for row in after:
        was = next(b for b in before if b["name"] == row["name"])
        if row["stockpile"] is not None and was["stockpile"] is not None \
                and row["stockpile"] == was["stockpile"]:
            who = "the local player" if row["local"] else "a rival"
            print(f"  [!] {row['name']} ({who}) gained NO research this turn")


def set_owner4(snap, value):
    """Give the local player a fresh per-civ id, the one field it reads 0 for.

    `Owner:4 == 0` is CONFIRMED to be what marks a civ as not empire-ticked:
    writing a fresh unique id here and firing one turn started research at the
    same +40 the rivals were getting, and brought Owner:24 and the score back
    with it. See the September 2026 note in
    docs/CosmicSupremacy_Memory_Reconstruction_Report.md.

    This is a LIVE write and it does not outlive the process. The shipping fix
    is the same four bytes in the galaxy blob, via
    server/dev_tools/set_civ_id.py.
    """
    import ejbo_viewer as ev
    civ = snap.local_civ()
    if civ is None:
        raise SystemExit("could not resolve the local player from 0x00857904")
    taken = {c.u32(4) for c in snap.civs if c.addr != civ.addr}
    if value in taken:
        raise SystemExit(f"{value} is already in use by another civ {sorted(taken)}; "
                         f"two civs sharing an id hide one from the score list")
    was = civ.u32(4)
    if not ev.write_bytes(snap.state.handle, civ.addr + 4,
                          struct.pack("<I", value)):
        raise SystemExit("the write failed")
    print(f"{civ.civ_name}: Owner:4 {was} -> {value}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drive", choices=("none", "launcher", "held", "both"),
                    default="none")
    ap.add_argument("--set-owner4", type=int, metavar="N",
                    help="write this per-civ id onto the local player first")
    ap.add_argument("--timeout", type=float, default=60.0)
    a = ap.parse_args()

    snap = gs.Snapshot()
    print(f"turn {snap.turn}, {len(snap.civs)} civ(s), "
          f"countdown 0x{TURNLENGTH_ADDR:08X} = {snap.rd32(TURNLENGTH_ADDR)}")
    show(reading(snap))
    if a.set_owner4 is not None:
        set_owner4(snap, a.set_owner4)

    if a.drive == "none":
        print("\nno turn fired. Re-run with --drive both to measure one.")
        return 0
    if a.drive in ("launcher", "both"):
        one_turn("launcher: countdown restored the moment the counter moves",
                 drive_launcher, a.timeout)
    if a.drive in ("held", "both"):
        one_turn("held: countdown left low across the whole boundary",
                 drive_held, a.timeout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
