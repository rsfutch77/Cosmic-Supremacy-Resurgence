"""
explore.py — the Explore rules (STRATEGY.md §4.1)
=================================================
R-EXP-02  send a ship to an undiscovered star so its system is revealed

    python explore.py            # dry run, one pass
    python explore.py --apply

── Why this exists, and why it is now the bottleneck ──────────────────────────
Client memory holds every planet in the galaxy whether or not it has been found.
Reading them all is cheating, so R-XPN-02 only considers systems in the
discovery set. The honest consequence is that a civ sitting in a fully colonised
home system has nowhere legal to go: it must physically send a ship somewhere
new before it can expand again. That is the actual game.

A SCOUT ORDER TARGETS A STAR, NOT A PLANET. `Ship:52 = 2` with the order's
destination set to a Sun's exact coordinates — confirmed at 0.0000 against two
independent scouts, with the nearest planet 12.6 away. That is precisely the
mechanic that makes fog of war real: you fly to the sun, and the system's
planets become visible.

No new actuator is needed. `create_order` and `retarget_order` already take any
object with coordinates at its allocation start, and `Sun` has them at the same
+0xc/+0x10/+0x14 the constructor reads from a `Ship` or a `Planet`.
"""
import sys

import actions
import cli
import gamestate as gs
import remote
import sensors

# A ship is "in" a system within this radius. Widest orbit measured is 39.6 and
# the tightest gap between two stars is 111.7, so anything in this band is
# unambiguous.
SYSTEM_RADIUS = 55.0

# Stop scouting once this many systems are known and still hold free planets;
# past that the civ has more room than it can use and the ship is better spent
# colonising.
ENOUGH_KNOWN_TARGETS = 3


def scoutable(snap, civ, hist, act):
    """Ships we may send scouting.

    A ship busy with a purposeful order is left alone. A COLONIZE order whose
    target we cannot legitimately see is not purposeful — it is a leftover from
    before fog of war was enforced, and repurposing it is a correction, not an
    interruption.
    """
    out = []
    for s in snap.owned_ships(civ):
        if not act.is_crewed(s):
            continue
        ot = s.order_type or 0
        if ot in (0, 1):
            out.append(s)
        elif ot == 2 and _scout_done(snap, s, hist):
            # The engine never clears a fulfilled scout order, so a ship that
            # has already revealed its target is free to be sent somewhere new.
            out.append(s)
        elif ot == 3:
            t = _colonize_target(snap, s)
            if t is None or not hist.can_see(snap, t):
                out.append(s)
    return out


def _scout_done(snap, ship, hist):
    """A scout order whose target system is already discovered is finished."""
    import struct
    if not gs.is_ptr(ship.order_ptr):
        return False
    xyz = struct.unpack("<fff", snap.read(ship.order_ptr + 16, 12))
    sun = min(snap.suns, key=lambda q: gs.dist(xyz, q.pos))
    return sun.id in hist.discovered


def _colonize_target(snap, ship):
    import struct
    if not gs.is_ptr(ship.order_ptr):
        return None
    xyz = struct.unpack("<fff", snap.read(ship.order_ptr + 16, 12))
    near = min(snap.planets, key=lambda q: gs.dist(xyz, q.pos))
    return near if gs.dist(xyz, near.pos) < 1.0 else None


def undiscovered_suns(snap, hist):
    return [s for s in snap.suns if s.id not in hist.discovered]


def free_targets_known(snap, hist):
    """Unowned planets we are already entitled to see."""
    return [p for p in snap.unowned_planets() if hist.can_see(snap, p)]


def run_exp02(snap, civ, act, hist, log=print):
    """Send each free ship to the nearest star nobody has visited."""
    known_free = free_targets_known(snap, hist)
    if len(known_free) >= ENOUGH_KNOWN_TARGETS:
        return 0                      # plenty to colonise; scouting can wait

    ships = scoutable(snap, civ, hist, act)
    if not ships:
        return 0
    targets = undiscovered_suns(snap, hist)
    if not targets:
        log("R-EXP-02: every system is already discovered")
        return 0

    log(f"R-EXP-02: {len(known_free)} known free planet(s), "
        f"{len(ships)} ship(s) free to scout, "
        f"{len(targets)} undiscovered system(s)")

    claimed, acted = set(), 0
    for ship in ships:
        if act.is_claimed(ship):
            # Expand already committed this ship this pass. The snapshot
            # still shows its old order, so without this it would be
            # redirected to a star and the colonisation quietly lost.
            continue
        speed = ship.design.speed if ship.design and ship.design.computed else None
        pool = [s for s in targets if s.id not in claimed]
        if not pool:
            break
        sun = min(pool, key=lambda s: gs.dist(ship.pos, s.pos))
        d = gs.dist(ship.pos, sun.pos)
        eta = f"{-(-d // speed):.0f}" if speed else "?"
        log(f"  {ship} -> Sun #{sun.id} at {d:.0f} units ({eta} turns)")
        claimed.add(sun.id)
        try:
            new_ptr = None
            if not gs.is_ptr(ship.order_ptr):
                new_ptr = act.create_order(ship, sun, civ)
                if new_ptr is None:
                    log("    [WOULD WRITE] scout order into the new object")
                    act.claim(ship)
                    acted += 1
                    continue
            act.retarget_order(ship, 2, sun.pos, order_ptr=new_ptr)
            act.claim(ship)
            acted += 1
        except (actions.NeedsEngineOrder, ValueError, RuntimeError) as e:
            log(f"    could not order the scout: {e}")
    return acted


RULES = [("R-EXP-02", run_exp02)]


def main():
    args = sys.argv[1:]
    dry_run = not cli.flag(args, "--apply")
    snap = gs.Snapshot()
    civ = gs.resolve_civ(snap, cli.opt(args, "--civ"))
    if civ is None:
        return
    print(f"\n=== Explore — turn {snap.turn} — {civ.civ_name!r} — "
          f"{'DRY RUN' if dry_run else 'APPLYING'} ===")
    hist = sensors.History().observe(snap, civ)
    print(f"  {len(hist.discovered)} system(s) discovered of {len(snap.suns)}")
    rem = None
    if not dry_run:
        rem = remote.Remote(snap.state.pid)
    act = actions.Actuator(snap, dry_run=dry_run, remote_=rem)
    try:
        run_exp02(snap, civ, act, hist)
    finally:
        if rem is not None:
            rem.close()
    print(f"\n{act.summary()}")
    if dry_run:
        print("re-run with --apply to perform these writes")


if __name__ == "__main__":
    main()
