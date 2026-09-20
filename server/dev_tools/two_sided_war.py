"""
two_sided_war.py , determinism across a battle both sides can fight
====================================================================
A4 measured determinism across a war in which only one side was armed: an
attacker against an unarmed defender and its planet. This arms the defender and
repeats the measurement, so the engagement exercises return fire rather than
target practice.

    python two_sided_war.py fixture                 # offline, arms BadGuy
    python two_sided_war.py arm                     # live, war and orders
    python two_sided_war.py run --turns 70 --runs 3 # live, the branches
    python two_sided_war.py compare a.dat b.dat

`run` is the measurement. It ticks the fork several times from a cold load and
compares the results to each other, which is the case the referee exercises, and
it plays one branch straight through without reloading, which is the case a
player's client exercises. A control fixture with no war runs the same way, so a
difference found under combat can be told apart from one the client produces
whatever the galaxy is doing.

Star names are normalised away before the played branch is compared, and only
there. A running client materialises the default name `Unnamed` over every empty
one, which is 7 bytes per sun and nothing to do with the simulation.
"""
import argparse
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
REPO = os.path.dirname(SERVER)
CLIENT_DEV = os.path.join(REPO, "client", "dev_tools")
AI = os.path.join(CLIENT_DEV, "ai_player")
for p in (HERE, SERVER, CLIENT_DEV, AI):
    if p not in sys.path:
        sys.path.insert(0, p)

import save_parser as sp
import inject_civ as icv
import inject_design as idg
import inject_ship as ish
import merge_orders as mo
import canonical

# GoodGuy's `f1` in the rehearsal galaxy: the cheapest hull that carries a
# weapon. BadGuy is given the same parts so neither side has an advantage the
# comparison would have to account for.
FIGHTER = {"chassis": [0], "scanners": [0], "engines": [0], "weapons": [0]}
DESIGN_NAME = "bgf1"
CREW = 2                      # crew per injected warship

DEFAULT_FIXTURE = os.path.join(REPO, "client", "cycle.dat")


def log(msg=""):
    print(msg, flush=True)


# ── the fixture, offline ─────────────────────────────────────────────────────
def build_fixture(blob, civ="BadGuy", count=2):
    """Give `civ` a fighter design and `count` hulls over its largest planet.

    The design is synthesised rather than cloned out of the other civ, because a
    DSGN carries the id of the civ that owns it and a clone would name the wrong
    one. `inject_design.make` writes the owning civ's id into the record it
    builds.
    """
    home = mo.civ_by_name(blob, civ)
    like = next(oid for _off, oid, _n in idg.design_records(blob)
                if _inside(blob, _off, home))
    blob = idg.make(blob, like, DESIGN_NAME, FIGHTER, log=log)
    design = max(oid for _o, oid, _n in idg.design_records(blob))
    log(f"\n{civ} owns design {design} {DESIGN_NAME!r} {FIGHTER}")
    for _ in range(count):
        blob = ish.add_ship(blob, civ, design_id=design, log=log)
    log()
    ish.describe(blob, log=log)
    return blob


def _inside(blob, off, owner_rec):
    return owner_rec["off"] < off < owner_rec["end"]


# `exterminate.relation_code` names these: 0 Neutral, 1 War, 2 Cease-Fire,
# 3 Peace, 4 Alliance.
PEACE = 3


def set_relation(act, a, b, code):
    """Write one relation code between two civs, both directions.

    `declare_war` is the actuator for going to War and applies the standing
    cost that goes with it. Nothing existed for the other direction, and the
    control arm needs it: it has to be able to take a galaxy that is ALREADY at
    war back to peace.
    """
    import remote
    for x, y in ((a, b), (b, a)):
        rec = act.remote.relation_record(x.addr, y.addr)
        if rec is None:
            raise RuntimeError(f"no relation record for {x.civ_name!r} -> "
                               f"{y.civ_name!r}; refusing to half-set it")
        before = act.snap.rd32(rec + remote.RELATION_CODE_OFF)
        act._u32(rec + remote.RELATION_CODE_OFF, code,
                 f"{x.civ_name!r} -> {y.civ_name!r}: "
                 f"{act.RELATION_NAMES.get(before, before)} -> "
                 f"{act.RELATION_NAMES.get(code, code)}")


def stage(blob, attacker="GoodGuy", defender="BadGuy"):
    """Move the attacker's armed hulls onto the defender's largest planet.

    WITHOUT THIS THERE IS NO BATTLE TO MEASURE. Sent from where `cycle.dat`
    leaves them, the attacker's warships cover about 3.4 units per turn against
    a separation of 450, so they need roughly 140 turns to arrive and a 70-turn
    branch ends with them still in transit. Measured: a 70-turn branch in which
    every surviving hull came out at condition 1.0 and the defenders never fired.

    Combat triggers on co-location, so placing the attacker at the target is the
    same engagement the approach would have produced, without spending 140 turns
    of client time reaching it. The approach itself is not what A4 set out to
    measure.
    """
    home = _largest_planet(blob, defender)
    atk = mo.civ_by_name(blob, attacker)
    armed = _armed_designs(blob, atk)
    out = bytearray(blob)
    moved = []
    for s in ish.ship_records(blob):
        if s["owner"] != atk["oid"] or s["design"] not in armed:
            continue
        struct.pack_into("<fff", out, s["payload"] + 4, *home["pos"])
        moved.append(s["id"])
    log(f"  staged {attacker} hull(s) {moved} onto planet #{home['id']} "
        f"at ({home['pos'][0]:.0f}, {home['pos'][1]:.0f}, {home['pos'][2]:.0f})")
    if not moved:
        raise SystemExit(f"{attacker} has no armed hull to stage")
    return bytes(out)


def _largest_planet(blob, civ_name):
    civ = mo.civ_by_name(blob, civ_name)
    mine = [p for p in icv.planet_records(blob) if p["owner"] == civ["oid"]]
    if not mine:
        raise SystemExit(f"{civ_name} owns no planet")
    return max(mine, key=lambda p: p["plpr_ln"])


def _armed_designs(blob, owner_rec):
    """Design ids belonging to `owner_rec` that carry a weapon."""
    out = set()
    for oid, d in mo.design_index(blob).items():
        if d["civ"] == owner_rec["oid"] and (d["parts"] or {}).get("weapons"):
            out.add(oid)
    return out


# ── arming it, live ──────────────────────────────────────────────────────────
def arm(dat, attacker="GoodGuy", defender="BadGuy", declare=True,
        save_dir=None, play=0, secs=5, keep_open=False):
    """Load `dat`, crew the defender's hulls, declare war, send the attacker in.

    Returns (fork, played). `fork` is the capture every loaded branch starts
    from. `played` is the same session carried on for `play` further turns with
    no reload, which is the only way to produce that arm: every other entry
    point here loads a blob first.

    `declare` off produces the control: the same galaxy, the same crewed hulls,
    the same fleet movement, and no war. Combat is gated on the relation, so the
    control moves ships without anybody shooting.
    """
    import game_cycle as gc
    import gamestate as gs
    import actions
    import remote
    import exterminate
    import player_turn

    snap = gc.restart(dat, purpose="two-sided war", wait_for_lock=240.0)
    log(f"  galaxy up at turn {snap.turn}")

    rem = remote.Remote(snap.state.pid, log=lambda *a: None)
    act = actions.Actuator(snap, dry_run=False, remote_=rem, log=log)
    try:
        atk = gs.resolve_civ(snap, attacker)
        dfn = gs.resolve_civ(snap, defender)

        targets = snap.owned_planets(dfn)
        if not targets:
            raise SystemExit(f"{defender} owns no planet to defend")
        home = max(targets, key=lambda p: len(p.vec(act.CITIZEN_LIST,
                                                    act.CITIZEN_STRIDE)))
        log(f"\n  {defender}'s planet under attack: {home}")

        # The defender's hulls come out of the blob with no crew, because the
        # donor record they were cloned from carries none. A crewless ship loses
        # its orders at every boundary and has nobody to fight with.
        armed = []
        for sh in snap.ships:
            if sh.owner is None or sh.owner.addr != dfn.addr:
                continue
            if getattr(sh.design, "name", None) != DESIGN_NAME:
                continue
            act.conscript_to_crew(home, sh, CREW, dfn)
            armed.append(sh)
        log(f"  crewed {len(armed)} {defender} warship(s)")
        if not armed:
            raise SystemExit(f"{defender} has no {DESIGN_NAME!r} hull to crew; "
                             f"run the fixture phase first")

        if declare:
            act.declare_war(atk, dfn)
        else:
            # NOT merely skipping the declaration. `cycle.dat` already holds
            # these two at War, so a control that only declined to declare
            # would run the same war as the experiment and forgive whatever it
            # found. Peace has to be written.
            set_relation(act, atk, dfn, PEACE)

        # Re-read: conscription and the relation writes both move state the
        # ship records are resolved against.
        snap = gs.Snapshot(snap.state)
        atk = gs.resolve_civ(snap, attacker)
        home = next(p for p in snap.planets if p.id == home.id)
        act = actions.Actuator(snap, dry_run=False, remote_=rem, log=log)

        sent = 0
        for sh in snap.ships:
            if sh.owner is None or sh.owner.addr != atk.addr:
                continue
            if not getattr(sh.design, "weapons", None):
                continue
            if not act.is_crewed(sh):
                log(f"  {sh} is unarmed crew-wise, not sending it")
                continue
            exterminate.send_to(act, sh, home, atk)
            sent += 1
        log(f"  sent {sent} {attacker} warship(s) at {home}")
    finally:
        rem.close()

    fork = sp.load_any(player_turn.collect("fork", save_dir=save_dir, log=log))
    log(f"  fork captured: {len(fork):,} bytes, "
        f"canonical {canonical.canonical_hash(fork)[:16]}")

    played_blob = None
    if play:
        # Same client, no reload. Capturing the fork did not disturb it:
        # SaveGame serialises the galaxy and leaves it running.
        log(f"\n  playing {play} turn(s) on in the SAME session")
        _advance(play, secs)
        played_blob = sp.load_any(
            player_turn.collect("played", save_dir=save_dir, log=log))
        log(f"  played branch: {len(played_blob):,} bytes, "
            f"canonical {canonical.canonical_hash(played_blob)[:16]}")

    if keep_open:
        # The played arm continues THIS session, so the client has to survive
        # the call that produced the fork. It is also how a human checks the
        # crewing by eye before the branches are spent on it.
        log("  client left running")
    else:
        gc.close_client()
    return fork, played_blob


def _advance(turns, secs):
    """`turns` turn boundaries in the client that is already running."""
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(CLIENT_DEV,
                                                     "advance_turns.py"),
                        str(turns), "--secs", str(secs)],
                       capture_output=True, text=True, cwd=CLIENT_DEV)
    if r.returncode != 0:
        log(r.stdout + r.stderr)
        raise SystemExit("advance_turns failed")


# ── the branches, live ───────────────────────────────────────────────────────
def branch(blob, turns, secs=5, save_dir=None):
    """One cold load of `blob`, `turns` turns, and the capture that comes out."""
    import referee
    return referee.tick(blob, turns=turns, secs=secs, save_dir=save_dir,
                        log=log)


# ── comparison ───────────────────────────────────────────────────────────────
def without_sun_names(blob):
    """The blob with every system name set empty.

    Only for the played-against-loaded comparison. `canonical.py` deliberately
    does not mask names, because a mask that forgives a default also forgives a
    change.
    """
    for oid, (_owners, _total, (ln, _name)) in mo.systems(blob).items():
        if ln:
            blob = mo.set_system_name(blob, oid, b"")
    return blob


def compare(a, b, label_a="A", label_b="B"):
    """Canonical hashes, then where they differ. Returns True when equal."""
    ha, hb = canonical.canonical_hash(a), canonical.canonical_hash(b)
    log(f"  {label_a}: {len(a):,} bytes  {ha[:16]}")
    log(f"  {label_b}: {len(b):,} bytes  {hb[:16]}")
    if ha == hb:
        log(f"  IDENTICAL")
        return True
    log(f"  DIFFERENT ({abs(len(a) - len(b))} bytes apart in length)")
    for d in canonical.differences(a, b):
        log(f"    {d}")
    return False


# ── combat evidence ──────────────────────────────────────────────────────────
# A `NEWS` payload is a fixed 44-byte record, eleven dwords, the last three of
# which are a position. Read across cycle.dat and several branches of it:
#
#     +0   u32  varies with the category, 0, 2 or 66
#     +8   u32  the turn the item was raised
#     +20  u32  category
#     +24  u32  item id, unique and ascending within a civ
#     +32  3xf32  where it happened, zero for items with no place
#
# Categories seen: 1 and 3 carry no position, 8 to 11 carry one. An engagement
# raises TWO items on one turn at one position, one in each civ's OWNR, under
# different categories, which reads as each side's own view of it.
NEWS_LEN = 44


def news(blob):
    """[(civ, turn, category, id, (x, z, y))] for every `NEWS` record."""
    owners = icv.owner_records(blob)
    out = []
    for off in idg.find_all(blob, b"NEWS"):
        if idg.sec_len(blob, off)[1] != NEWS_LEN:
            continue
        p = off + 8
        f = struct.unpack_from("<8I", blob, p)
        pos = struct.unpack_from("<3f", blob, p + 32)
        who = next((o["name"] for o in owners if o["off"] < off < o["end"]), "-")
        out.append((who, f[2], f[5], f[6], pos))
    return out


def contacts(before, after):
    """News raised between two blobs that carries a position.

    The point of this experiment is that an engagement happened, and a hull
    going missing does not show that: a warship can be lost in transit without
    meeting anything. A positioned news item on a turn inside the window is
    direct evidence of an event at a place, and the pair of them names both
    sides of it.
    """
    was = {(w, t, c, i) for w, t, c, i, _p in news(before)}
    return [n for n in news(after)
            if (n[0], n[1], n[2], n[3]) not in was and any(n[4])]


def fleet(blob):
    """{civ name: [(shipId, designName)]}, so losses can be attributed."""
    names = {o["oid"]: o["name"] for o in icv.owner_records(blob)}
    designs = {oid: d["name"] for oid, d in mo.design_index(blob).items()}
    out = {}
    for s in ish.ship_records(blob):
        out.setdefault(names.get(s["owner"], s["owner"]), []).append(
            (s["id"], designs.get(s["design"], s["design"])))
    return {k: sorted(v) for k, v in out.items()}


def report_losses(before, after):
    """What each civ lost between two blobs.

    The point of the experiment is that BOTH sides shoot, so an attacker that
    loses nothing means the defender never fired and the run proves no more than
    A4 already did.
    """
    b, a = fleet(before), fleet(after)
    for civ in sorted(set(b) | set(a)):
        was, now = set(b.get(civ, [])), set(a.get(civ, []))
        lost, built = sorted(was - now), sorted(now - was)
        log(f"  {civ}: {len(was)} -> {len(now)} hull(s)"
            + (f", lost {lost}" if lost else "")
            + (f", gained {built}" if built else ""))
    return b, a


# ── phases ───────────────────────────────────────────────────────────────────
def phase_fixture(args):
    blob = sp.load_any(args.source)
    log(f"{args.source}: {len(blob):,} bytes")
    blob = build_fixture(blob, civ=args.defender, count=args.hulls)
    if args.stage:
        log()
        blob = stage(blob, attacker=args.attacker, defender=args.defender)
    with open(args.out, "wb") as f:
        f.write(blob)
    log(f"\nwrote {args.out} ({len(blob):,} bytes)")


def phase_arm(args):
    fork, played_blob = arm(args.fixture, attacker=args.attacker,
                            defender=args.defender, declare=not args.control,
                            save_dir=args.save_dir, play=args.play,
                            secs=args.secs, keep_open=args.keep_open)
    with open(args.out, "wb") as f:
        f.write(fork)
    log(f"\nwrote {args.out} ({len(fork):,} bytes)")
    if played_blob is not None:
        p = args.out.replace(".dat", "_played.dat")
        with open(p, "wb") as f:
            f.write(played_blob)
        log(f"wrote {p} ({len(played_blob):,} bytes)")


def phase_run(args):
    import referee
    fork = sp.load_any(args.fork)
    log(f"fork {args.fork}: {len(fork):,} bytes, turn {referee.turn_of(fork)}, "
        f"canonical {canonical.canonical_hash(fork)[:16]}")
    log(f"{args.runs} loaded branch(es) of {args.turns} turns, "
        f"then one played branch\n")

    loaded = []
    for i in range(args.runs):
        log(f"--- loaded branch {i + 1}/{args.runs} ---")
        t0 = time.time()
        out = branch(fork, args.turns, secs=args.secs, save_dir=args.save_dir)
        loaded.append(out)
        with open(f"{args.out}.L{i + 1}.dat", "wb") as f:
            f.write(out)
        log(f"  {time.time() - t0:.0f}s, {len(out):,} bytes, "
            f"canonical {canonical.canonical_hash(out)[:16]}\n")

    log("=== loaded against loaded ===")
    if len(loaded) < 2:
        # One branch compares against nothing. Saying "reproduced" here would
        # be a claim the run did not make.
        log("  only one branch was run, so there is nothing to compare it to\n")
    else:
        same = all(compare(loaded[0], o, "L1", f"L{i + 2}")
                   for i, o in enumerate(loaded[1:]))
        log(f"  {'reproduced' if same else 'DIVERGED'} "
            f"across {len(loaded)} invocation(s)\n")

    log("=== did an engagement happen ===")
    seen = contacts(fork, loaded[0])
    for who, turn, cat, oid, pos in seen:
        log(f"  {who:8} turn {turn:<5} category {cat:<3} item {oid:<4} "
            f"at ({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f})")
    if not seen:
        log("  NO positioned news was raised, so nothing met anything")

    log("\n=== what the battle cost ===")
    report_losses(fork, loaded[0])

    log("\n=== star names ===")
    stripped = [without_sun_names(o) for o in loaded]
    for i, (raw, flat) in enumerate(zip(loaded, stripped)):
        log(f"  L{i + 1}: {len(raw):,} -> {len(flat):,} bytes with names cleared")


def phase_compare(args):
    a, b = sp.load_any(args.a), sp.load_any(args.b)
    log("=== as captured ===")
    compare(a, b, args.a, args.b)
    log("\n=== star names cleared ===")
    compare(without_sun_names(a), without_sun_names(b), args.a, args.b)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="phase", required=True)

    f = sub.add_parser("fixture", help="arm the defender, offline")
    f.add_argument("--source", default=DEFAULT_FIXTURE)
    f.add_argument("--attacker", default="GoodGuy")
    f.add_argument("--defender", default="BadGuy")
    f.add_argument("--hulls", type=int, default=2)
    f.add_argument("--stage", action="store_true",
                   help="put the attacker's warships on the defender's planet, "
                        "so the branch measures the battle and not the approach")
    f.add_argument("-o", "--out", default=os.path.join(REPO, "client",
                                                       "twosided.dat"))
    f.set_defaults(func=phase_fixture)

    a = sub.add_parser("arm", help="declare war and send the fleet in, live")
    a.add_argument("--fixture", default=os.path.join(REPO, "client",
                                                     "twosided.dat"))
    a.add_argument("--attacker", default="GoodGuy")
    a.add_argument("--defender", default="BadGuy")
    a.add_argument("--control", action="store_true",
                   help="same movement, no war declared")
    a.add_argument("--save-dir", default=os.path.join(SERVER, "saves"))
    a.add_argument("--play", type=int, default=0,
                   help="carry the SAME session on this many turns after the "
                        "fork, which is the played arm of the comparison")
    a.add_argument("--secs", type=int, default=5)
    a.add_argument("--keep-open", action="store_true",
                   help="leave the client running after the capture")
    a.add_argument("-o", "--out", default=os.path.join(REPO, "client",
                                                       "twosided_fork.dat"))
    a.set_defaults(func=phase_arm)

    r = sub.add_parser("run", help="the branches and the comparison")
    r.add_argument("--fork", default=os.path.join(REPO, "client",
                                                  "twosided_fork.dat"))
    r.add_argument("--turns", type=int, default=70)
    r.add_argument("--runs", type=int, default=3)
    r.add_argument("--secs", type=int, default=5)
    r.add_argument("--save-dir", default=os.path.join(SERVER, "saves"))
    r.add_argument("-o", "--out", default=os.path.join(SERVER, "referee_work",
                                                       "twosided"))
    r.set_defaults(func=phase_run)

    c = sub.add_parser("compare", help="two blobs, with and without names")
    c.add_argument("a")
    c.add_argument("b")
    c.set_defaults(func=phase_compare)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
