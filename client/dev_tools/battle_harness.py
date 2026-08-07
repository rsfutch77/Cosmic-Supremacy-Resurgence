"""battle_harness.py — get to the dangerous step fast, save, then take one step.

The turn-405 crash cost a 400-turn game because there was no restore point near
the failure. This builds a battle-ready state in ~20 turns instead of ~400, SAVES
it, and only then issues the order under test, so each retry costs a relaunch
rather than a replay.

    python battle_harness.py --arm                  # war + b1 built + crewed, then save
    python battle_harness.py --order 1              # issue Move, advance 1 turn
    python battle_harness.py --order 4              # issue Attack, advance 1 turn
    python battle_harness.py --status

The ONLY thing under test is whether the client survives resolving the order, so
the script advances exactly one turn boundary and reports survival rather than
running a game.
"""
import argparse
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(CLIENT_DIR)
SAVES = os.path.join(REPO, "server", "saves")
sys.path.insert(0, os.path.join(HERE, "ai_player"))

import actions
import ejbo_viewer as ev
import exploit
import exterminate
import gamestate as gs
import remote

TURNLENGTH = 0x0080AA08
CIV = "GoodGuy"


def log(m):
    print(m, flush=True)


def connect():
    snap = gs.Snapshot()
    civ = gs.resolve_civ(snap, CIV)
    if civ is None:
        sys.exit("cannot resolve GoodGuy")
    rem = remote.Remote(snap.state.pid, log=log)
    act = actions.Actuator(snap, dry_run=False, log=log, remote_=rem)
    return snap, civ, rem, act


def turn_of(snap):
    b = ev.read_bytes(snap.state.handle, gs.TURN_COUNTER, 4)
    return struct.unpack("<I", b)[0] if b and len(b) == 4 else None


def drive(snap, secs=10):
    ev.write_bytes(snap.state.handle, TURNLENGTH, struct.pack("<I", secs))


def alive():
    import subprocess
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-Process -Name 'CosmicSupremacy*' "
                          "-ErrorAction SilentlyContinue | "
                          "Select-Object -ExpandProperty Id"],
                         capture_output=True, text=True).stdout
    return [int(x) for x in out.split() if x.strip().isdigit()]


def wait_turns(snap, n, timeout=400):
    """Advance n turn boundaries. Returns False if the client dies."""
    start = turn_of(snap)
    target = start + n
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not alive():
            log(f"  [!] CLIENT DIED while waiting for turn {target}")
            return False
        t = turn_of(snap)
        if t is None:
            log("  [!] CLIENT DIED (handle went away)")
            return False
        if t >= target:
            return True
        drive(snap, 10)          # re-assert; the engine resets it
        time.sleep(2)
    log(f"  [!] timed out waiting for turn {target}")
    return False


def status():
    snap, civ, rem, act = connect()
    try:
        log(f"turn {turn_of(snap)}  cash {civ.cash}  score {civ.score}")
        for c in snap.civs:
            if c.addr != civ.addr:
                code = exterminate.relation_code(snap, civ, c, act)
                log(f"  relation {c.civ_name!r}: {code} "
                    f"({'WAR' if code == 1 else 'not at war'})")
        for s in snap.owned_ships(civ):
            d = s.design.design_name if s.design else None
            log(f"  {s} {d!r} role={s.role} crew={len(s.crew)} "
                f"order={s.u32(52)} cond={s.condition}")
        for p in snap.owned_planets(civ):
            if exploit.SHIPYARD in p.facilities:
                log(f"  yard {p} active={p.u32(344)} military={p.military}")
    finally:
        rem.close()


def arm():
    """War + a built, crewed b1 sitting at the shipyard. Then save."""
    snap, civ, rem, act = connect()
    try:
        drive(snap, 10)
        bad = next(c for c in snap.civs if c.civ_name != CIV)

        if exterminate.relation_code(snap, civ, bad, act) != exterminate.WAR:
            log("1. declaring war")
            act.declare_war(civ, bad)
            exterminate.clear_relation_cache()
        else:
            log("1. already at war")

        b1 = next(d for d in snap.designs
                  if d.design_name == "b1"
                  and d.owner is not None and d.owner.addr == civ.addr)
        yard = next(p for p in snap.owned_planets(civ)
                    if exploit.SHIPYARD in p.facilities)

        have = [s for s in snap.owned_ships(civ)
                if s.design is not None and s.design.addr == b1.addr]
        if not have:
            log(f"2. queueing b1 at {yard}")
            act.queue_ship(yard, b1)
            # Hurry needs progress/total >= 0.5, so let it build a little first.
            for _ in range(12):
                if not wait_turns(snap, 1):
                    return
                snap = gs.Snapshot(); civ = gs.resolve_civ(snap, CIV)
                act = actions.Actuator(snap, dry_run=False, log=log, remote_=rem)
                yard = next(p for p in snap.owned_planets(civ)
                            if exploit.SHIPYARD in p.facilities)
                st = act.hurry_state(yard)
                log(f"   build state: {st}")
                built = [s for s in snap.owned_ships(civ)
                         if s.design is not None
                         and s.design.design_name == "b1"]
                if built:
                    break
                try:
                    act.hurry_production(yard)
                    log("   hurried")
                except (ValueError, RuntimeError) as e:
                    log(f"   cannot hurry yet: {e}")

        snap = gs.Snapshot(); civ = gs.resolve_civ(snap, CIV)
        act = actions.Actuator(snap, dry_run=False, log=log, remote_=rem)
        ship = next((s for s in snap.owned_ships(civ)
                     if s.design is not None and s.design.design_name == "b1"),
                    None)
        if ship is None:
            log("   b1 never appeared; aborting")
            return
        log(f"3. b1 is built: {ship}")

        # Crew it. Recruit at the ceiling until a unit exists, then load.
        for _ in range(40):
            snap = gs.Snapshot(); civ = gs.resolve_civ(snap, CIV)
            act = actions.Actuator(snap, dry_run=False, log=log, remote_=rem)
            ship = next(s for s in snap.owned_ships(civ)
                        if s.design is not None
                        and s.design.design_name == "b1")
            need = exploit.min_crew(ship, act)
            if len(ship.crew) >= need:
                log(f"4. crewed {len(ship.crew)}/{need}")
                break
            p = ship.orbiting
            if p is None:
                log("   b1 is not in orbit; cannot crew")
                return
            if p.military >= need:
                log(f"4. loading {need} crew from {p}")
                act.load_crew(p, ship, need)
                continue
            act.set_recruitment(p, act.max_recruitment(p))
            log(f"   recruiting at {p}: military {p.military}/{need}")
            if not wait_turns(snap, 1):
                return
        else:
            log("   never got crew; aborting")
            return

        log("5. saving the armed state")
        save_restore_point()
    finally:
        rem.close()


def save_restore_point(name="armed"):
    """Capture the current state through the engine's own SaveGame, then write a
    relaunchable .dat next to the client.

    This is the whole point of the harness. The first crash cost a 400-turn game
    because there was no restore point near the failure, so arming ALWAYS ends by
    saving — a trial should cost a relaunch, not a replay.
    """
    import subprocess
    before = set(os.listdir(SAVES)) if os.path.isdir(SAVES) else set()
    r = subprocess.run([sys.executable, os.path.join(HERE, "trigger_save.py"),
                        "--name", name[:15]],
                       capture_output=True, text=True, cwd=HERE)
    if "saved" not in r.stdout:
        log(r.stdout + r.stderr)
        log("  [!] SaveGame did not report success; no restore point written")
        return None
    new = [f for f in os.listdir(SAVES)
           if f.endswith(".b64") and f not in before]
    if not new:
        log("  [!] SaveGame succeeded but no blob appeared in server/saves — "
            "is cs_server.py running?")
        return None
    blob_path = os.path.join(SAVES, sorted(new)[-1])
    sys.path.insert(0, os.path.join(REPO, "server", "dev_tools"))
    import save_parser as sp
    dat = os.path.join(CLIENT_DIR, f"{name}.dat")
    with open(dat, "wb") as f:
        f.write(sp.decode_save(open(blob_path).read()))
    log(f"  restore point: {dat}")
    log(f"  relaunch with: CosmicSupremacy_Resurgence.exe {dat}")
    return dat


def issue(order_type, no_command=False):
    """Issue the order under test and advance exactly one turn boundary.

    `no_command` skips actuator L entirely and leaves the ship with an
    engine-built order object, a correct single-leg route and Ship:52 set by
    retarget_order — but Ship:56 still the static null node. That splits the two
    suspects: if this SURVIVES, set_command is the problem and is avoidable; if it
    dies too, the targeted order TYPES themselves are unusable.
    """
    snap, civ, rem, act = connect()
    try:
        drive(snap, 10)
        bad = next(c for c in snap.civs if c.civ_name != CIV)
        target = next(p for p in snap.planets
                      if p.owner is not None and p.owner.addr == bad.addr)
        ship = next((s for s in snap.owned_ships(civ)
                     if s.role == "WARSHIP" and act.is_crewed(s)), None)
        if ship is None:
            sys.exit("no crewed warship; run --arm first")
        code = exterminate.relation_code(snap, civ, bad, act)
        log(f"relation with BadGuy: {code} "
            f"({'WAR' if code == 1 else 'NOT AT WAR'})")
        log(f"issuing order type {order_type} "
            f"({gs.ORDER_TYPES.get(order_type)}) on {ship} -> {target}"
            + ("  [route only, NO set_command]" if no_command else ""))
        if no_command:
            ptr = act.create_order(ship, target, civ)
            act.retarget_order(ship, order_type, target.pos, order_ptr=ptr)
        else:
            act.set_command(ship, order_type, target, civ)
        log("order issued; now advancing ONE turn boundary — this is the step "
            "that killed the client at turn 405")
        ok = wait_turns(snap, 1)
        if ok:
            snap2 = gs.Snapshot()
            civ2 = gs.resolve_civ(snap2, CIV)
            s2 = next((s for s in snap2.owned_ships(civ2)
                       if s.addr == ship.addr), None)
            log(f"SURVIVED turn {turn_of(snap2)}. ship now: {s2} "
                f"order={s2.u32(52) if s2 else 'gone'} "
                f"pos={tuple(round(x, 1) for x in s2.pos) if s2 else None}")
        else:
            log("FAILED: the client did not survive resolving this order")
    finally:
        try:
            rem.close()
        except Exception:
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="store_true")
    ap.add_argument("--order", type=int, default=None)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--no-command", action="store_true",
                    help="route only; skip actuator L")
    a = ap.parse_args()
    if a.arm:
        arm()
    elif a.order is not None:
        issue(a.order, no_command=a.no_command)
    else:
        status()
