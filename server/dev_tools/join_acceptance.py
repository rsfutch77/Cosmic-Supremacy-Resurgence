"""
join_acceptance.py , does a galaxy still play after civs join it?
=================================================================
    python join_acceptance.py prepare
    python join_acceptance.py k1 --turns 60
    python join_acceptance.py k1load
    python join_acceptance.py k2 --joins 12 --turns 100
    python join_acceptance.py load --dat k2_out.dat

The live half of K1 and the long run of K2. Everything `inject_civ` does to a
blob is checked on disk elsewhere; what is checked here is the engine, so every
step runs a real client and reads the blob the client wrote back.

`prepare` builds the K1 fixture. It gives seat one two colony ships and two
colonise orders, and the first of those two targets is chosen to be the exact
planet `pick_homeworld` returns when reservations are not fed to it. That is
what makes the K1 placement check able to fail: on this galaxy, as on every
galaxy in the repository, the maximin pick does not collide with a colonisation
in flight by accident, so a collision has to be arranged before the reservation
has anything to do. With the order in place an unreserved picker takes the
planet the colony ship is flying to, and the run records both answers.

The second order is short range, a planet a system away rather than across the
galaxy, because the colonisation has to actually complete inside a run that a
person will wait for. It is the one whose arrival turn is compared.

The runs, and what counts as a failure:

    prepare   the injected civ's homeworld is neither ship's destination, and
              both ships' `ROUT` sections come through the injection unchanged.
              Failure: the pick lands on a reserved planet, or a `ROUT` moves.
    k1        the base galaxy is played turn by turn until the near target
              changes hands, then the joined galaxy is played the same number
              of turns. Failure: the joined galaxy does not load, the
              colonisation does not happen, it happens on a different turn, or
              the planet goes to a different civ.
    k1load    the joined galaxy stamped for the civ that just joined opens in
              the build a player runs. Failure: the client exits during the
              load, or its state never becomes readable.
    k2        one galaxy takes N joins spread over M turns, playing the turns
              between them. Failure: any segment's client will not open the
              galaxy, any segment that does not advance the turns it was asked
              for, an object id that does not grow, or a civ count or
              high-water id that disagrees with the blob at the end.
    load      a blob a run produced is opened cold, in a client that was not
              already holding it. Failure: the client exits during the load, or
              the state never becomes readable.

The turn count matters to what K1 proves. The near target is settled within two
turns and the contested planet takes fifty-two, so a run short enough to see the
first says nothing about the second, and the second is the one the reservation
was written for. Sixty turns covers both.

A blob that loads once is not the bar, so nothing here scores a load by the
process still being alive: `game_cycle.launch` waits for the galaxy to become
readable and reports the turn it read. The trap on the other side is that the
same check fails for a galaxy that loaded perfectly well when the blob is
stamped for a civ that owns nothing. Every blob written here is stamped for a
civ that owns at least one planet and the stamp is named in the log.

Nothing here writes to a turn store. The galaxy under test is a copy on disk.
"""
import argparse
import json
import os
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
REPO = os.path.dirname(SERVER)
CLIENT_DEV = os.path.join(REPO, "client", "dev_tools")
for d in (HERE, SERVER, CLIENT_DEV, os.path.join(CLIENT_DEV, "ai_player")):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import inject_civ as icv
import inject_design as idg
import inject_order as ior
import merge_orders as mo
import set_blob_player
import wipe_acceptance as wa

WORK = os.path.join(SERVER, "join_work")
DEFAULT_FIXTURE = os.path.join(SERVER, "galaxy_demo", "turns", "0011.b64")
ADVANCE = os.path.join(CLIENT_DEV, "advance_turns.py")
COLONISE = 3


def log(msg=""):
    print(msg, flush=True)


def path(name):
    return os.path.join(WORK, name)


def write(name, blob):
    os.makedirs(WORK, exist_ok=True)
    with open(path(name), "wb") as f:
        f.write(blob)
    log(f"  wrote {name} ({len(blob):,} bytes, turn {wa.turn_of(blob)})")
    return path(name)


def owner_of(blob, planet_id):
    return next(p["owner"] for p in icv.planet_records(blob)
                if p["id"] == planet_id)


def oid_of(blob, civ):
    rec = next((o for o in icv.owner_records(blob) if o["name"] == civ), None)
    return rec["oid"] if rec else None


def civ_count(blob):
    return struct.unpack_from("<I", blob, icv.civ_count_at(blob))[0]


def high_water(blob):
    return struct.unpack_from("<I", blob, idg.HIGH_WATER_ID)[0]


def routs(blob):
    """Every ship's raw `ROUT` bytes, by ship object id.

    Compared before and after an injection. The parsed fields would miss a
    section that kept its numbers and moved, and the splice that widens the
    enclosing sections is exactly the operation that could move one.
    """
    out = {}
    tree = sp.parse_blob(blob)
    for sid, _owner, _pos, _sh, dyno in ior.ship_sections(blob, tree):
        if dyno is None:
            continue
        rout = next((c for c in dyno.children if c.tag == icv.ROUT), None)
        if rout is not None:
            out[sid] = bytes(blob[rout.start:rout.end])
    return out


# ── the client ───────────────────────────────────────────────────────────────
def advance(n, secs=10):
    """Drive the running client `n` turns. Raises unless it got there."""
    r = subprocess.run([sys.executable, ADVANCE, str(n), "--secs", str(secs)],
                       capture_output=True, text=True, cwd=CLIENT_DEV)
    text = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or "STALLED" in text or "lost the process" in text:
        log(text)
        raise SystemExit(f"advance_turns did not deliver {n} turn(s)")
    return text


def play(dat, turns, purpose, tag, secs=10, every=1):
    """Launch on `dat`, advance `turns` turns, capture every `every` of them.

    Captures come out of the running client rather than out of a relaunch, so a
    run of twenty turns costs one load rather than twenty. `game_cycle.launch`
    is what decides the galaxy opened, and it waits on the state rather than on
    a timer.

    Returns (turn the client opened at, [(turn, blob)]).
    """
    import game_cycle as gc
    snap = gc.restart(dat, purpose=purpose, wait_for_lock=600.0)
    start = snap.turn
    caps = []
    try:
        done = 0
        while done < turns:
            step = min(every, turns - done)
            advance(step, secs=secs)
            done += step
            blob = sp.load_any(gc.capture_save(f"{tag}{done}"[:15]))
            caps.append((wa.turn_of(blob), blob))
            log(f"    turn {caps[-1][0]} captured ({len(blob):,} bytes)")
    finally:
        gc.close_client()
    return start, caps


def load_check(dat, purpose, exe=None, min_suns=11):
    """Open a blob in a client and report the state it read. Returns the turn."""
    import game_cycle as gc
    snap = gc.restart(dat, purpose=purpose, wait_for_lock=600.0, exe=exe,
                      min_suns=min_suns)
    civ = None
    try:
        import gamestate as gs
        civ = gs.resolve_civ(snap, None, quiet=True)
    except Exception:
        pass
    log(f"  opened: turn {snap.turn}, {len(snap.suns)} sun(s), local civ "
        f"{civ.civ_name if civ else '?'}, "
        f"{len(snap.owned_planets(civ)) if civ else 0} planet(s)")
    gc.close_client()
    return snap.turn


# ── prepare ──────────────────────────────────────────────────────────────────
def near_target(blob, civ):
    """The closest free planet to a civ's homeworld.

    Range is the whole of why this one exists. The far target proves the
    reservation binds; a colonisation only proves it completed if somebody is
    still waiting when it does.
    """
    owner = oid_of(blob, civ)
    planets = icv.planet_records(blob)
    home = max((p for p in planets if p["owner"] == owner),
               key=lambda p: p["plpr_ln"])
    free = [p for p in planets if not p["owner"] and p["nlen"] == 0]
    return home, min(free, key=lambda p: icv.dist(p["pos"], home["pos"]))


def prepare(fixture, civ, joiner):
    blob = sp.load_any(fixture)
    log(f"{os.path.basename(fixture)}: {len(blob):,} bytes, "
        f"turn {wa.turn_of(blob)}")
    icv.describe(blob, log=log)

    if oid_of(blob, civ) is None:
        raise SystemExit(f"no civ named {civ!r} in the fixture")

    planets = icv.planet_records(blob)
    already = icv.reserved_planets(blob)
    far = icv.pick_homeworld(planets, already, margin=0.0)
    home, near = near_target(blob, civ)
    if near["id"] == far["id"]:
        raise SystemExit("the nearest free planet is also the maximin pick; "
                         "the two orders would not be distinguishable")

    log(f"\nstaging two colonisations for {civ!r} from planet #{home['id']}")
    log(f"  far  target #{far['id']}, which is what pick_homeworld returns on "
        f"this galaxy when nothing is reserved")
    log(f"  near target #{near['id']}, "
        f"{icv.dist(near['pos'], home['pos']):.0f} away")

    base, ship_far = wa.stage_colonisation(blob, civ, far["id"], log=log)
    base, ship_near = wa.stage_colonisation(base, civ, near["id"], log=log)

    dests = icv.ship_destinations(base)
    reserved = icv.reserved_planets(base)
    log(f"\n{len(dests)} ship(s) under orders reserve {len(reserved)} planet(s)")
    for d in dests:
        log(f"    ship {d['ship']:<6} owner {d['owner']:<6} type "
            f"{d['order_type']} -> {d['kind']} {d['planets']}")
    for want, name in ((far["id"], "far"), (near["id"], "near")):
        if want not in reserved:
            raise SystemExit(f"the {name} target #{want} is not reserved after "
                             f"the order was staged; the rest of the run would "
                             f"prove nothing")

    # What the placement would do with no reservation at all. This is the
    # control for the check below: without it, "the newcomer did not take the
    # target" is a sentence about a galaxy where the collision never arose.
    unreserved = icv.pick_homeworld(icv.planet_records(base), set(), margin=0.0)
    log(f"\n  an unreserved pick would take planet #{unreserved['id']}")
    if unreserved["id"] != far["id"]:
        raise SystemExit(f"the unreserved pick is #{unreserved['id']} and the "
                         f"far target is #{far['id']}; the collision this test "
                         f"needs was not arranged")
    log(f"  which is the far target, so the placement check below can fail")

    before = routs(base)
    joined, home_id = icv.add_civ(base, joiner, log=log)
    after = routs(joined)

    log(f"\n=== placement ===")
    log(f"  {joiner!r} homeworld: planet #{home_id}")
    if home_id in reserved:
        raise SystemExit(f"FAIL: #{home_id} is reserved by a ship in flight")
    log(f"  not #{far['id']} and not #{near['id']}, and not one of the "
        f"{len(reserved)} reserved planet(s)")

    log(f"\n=== the orders in flight ===")
    if sorted(before) != sorted(after):
        raise SystemExit(f"FAIL: ships carrying a ROUT changed from "
                         f"{sorted(before)} to {sorted(after)}")
    for sid in sorted(before):
        same = before[sid] == after[sid]
        log(f"  ship {sid}: ROUT {len(before[sid])} bytes, "
            f"{'unchanged' if same else 'CHANGED'}")
        if not same:
            raise SystemExit(f"FAIL: ship {sid}'s ROUT did not survive the join")

    stamped_base = set_blob_player.set_player(base, civ, log=log)
    stamped_join = set_blob_player.set_player(joined, civ, log=log)
    stamped_new = set_blob_player.set_player(joined, joiner, log=log)
    log(f"\nbase and joined are stamped for {civ!r}, which owns "
        f"{len([p for p in icv.planet_records(base) if p['owner'] == oid_of(base, civ)])}"
        f" planet(s); joined_as_new is stamped for {joiner!r}, which owns 1")

    write("base.dat", stamped_base)
    write("joined.dat", stamped_join)
    write("joined_as_new.dat", stamped_new)
    meta = {
        "fixture": fixture,
        "civ": civ,
        "joiner": joiner,
        "far": far["id"],
        "near": near["id"],
        "home": home_id,
        "ship_far": ship_far,
        "ship_near": ship_near,
        "reserved": sorted(reserved),
        "unreserved_pick": unreserved["id"],
        "civ_oid_base": oid_of(base, civ),
        "civ_oid_joined": oid_of(joined, civ),
        "joiner_oid": oid_of(joined, joiner),
        "turn": wa.turn_of(base),
    }
    with open(path("k1_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    log(f"\nwrote {path('k1_meta.json')}")
    return meta


# ── K1 ───────────────────────────────────────────────────────────────────────
def meta():
    with open(path("k1_meta.json")) as f:
        return json.load(f)


def first_flip(caps, planet_id, want_owner):
    """The first captured turn on which a planet reads `want_owner`."""
    for turn, blob in caps:
        if owner_of(blob, planet_id) == want_owner:
            return turn
    return None


def flight(blob):
    """Every ship under orders: what it is doing, where, and how far along.

    The progress field is what makes this worth comparing turn by turn. Two
    runs can agree on every planet's owner while one of them is flying a ship
    somewhere else, and a colonisation that lands on the right turn by accident
    is the failure this is meant to catch.
    """
    return {d["ship"]: (d["order_type"], tuple(sorted(d["planets"])),
                        round(d["progress"], 2))
            for d in icv.ship_destinations(blob)}


def population(blob, planet_id):
    owner, _p, plpr, _nm = mo.planet_index(blob)[planet_id]
    return owner, len(mo.citizens_of(plpr) or []) if plpr else 0


def k1(turns, secs=10):
    m = meta()
    log(f"=== K1: colonisation of planet #{m['near']} by {m['civ']!r}, "
        f"with and without {m['joiner']!r} in the galaxy ===")

    log(f"\n--- base.dat, no join ---")
    b_start, b_caps = play(path("base.dat"), turns, "join_acceptance K1, base galaxy", "k1b",
                           secs=secs)
    b_flip = first_flip(b_caps, m["near"], m["civ_oid_base"])
    b_far = first_flip(b_caps, m["far"], m["civ_oid_base"])

    log(f"\n--- joined.dat, {m['joiner']!r} has joined ---")
    j_start, j_caps = play(path("joined.dat"), turns, "join_acceptance K1, joined galaxy", "k1j",
                           secs=secs)
    j_flip = first_flip(j_caps, m["near"], m["civ_oid_joined"])
    j_far = first_flip(j_caps, m["far"], m["civ_oid_joined"])

    log(f"\n=== verdict ===")
    log(f"  base   opened at turn {b_start}, played to {b_caps[-1][0]}")
    log(f"  joined opened at turn {j_start}, played to {j_caps[-1][0]}")
    log(f"  planet #{m['near']} taken by {m['civ']!r}: base turn {b_flip}, "
        f"joined turn {j_flip}")
    log(f"  planet #{m['far']} taken by {m['civ']!r}: base turn {b_far}, "
        f"joined turn {j_far}")

    ok = True
    if b_flip is None:
        log(f"  FAIL: the colonisation never happened in the base galaxy, so "
            f"there is no schedule to be on")
        ok = False
    if j_flip is None:
        log(f"  FAIL: the colonisation never happened after the join")
        ok = False
    if b_flip is not None and j_flip is not None and b_flip != j_flip:
        log(f"  FAIL: the join moved the colonisation from turn {b_flip} to "
            f"turn {j_flip}")
        ok = False
    if b_far != j_far:
        log(f"  FAIL: the join moved the contested planet's colonisation from "
            f"turn {b_far} to turn {j_far}")
        ok = False
    if b_far is None:
        log(f"  note: {turns} turn(s) was not long enough for the contested "
            f"planet #{m['far']} to be settled in either galaxy; the schedule "
            f"above is the near target's")

    # Every ship under orders, every turn, in both galaxies. The owner of one
    # planet agreeing says nothing about the ship that has not arrived yet, and
    # the contested planet is the one a ship is still flying to.
    log(f"\n=== ships under orders, turn by turn ===")
    shared = dict(j_caps)
    diverged = []
    for turn, blob in b_caps:
        if turn not in shared:
            continue
        a, b = flight(blob), flight(shared[turn])
        if a != b:
            diverged.append((turn, a, b))
    log(f"  {len([t for t, _ in b_caps if t in shared])} turn(s) compared, "
        f"{len(diverged)} divergence(s)")
    for turn, a, b in diverged[:5]:
        log(f"    turn {turn}: base {a}")
        log(f"               joined {b}")
    if diverged:
        log(f"  FAIL: the join changed what a ship in flight was doing")
        ok = False
    else:
        last_flight = flight(b_caps[-1][1])
        log(f"  at turn {b_caps[-1][0]} both galaxies read {last_flight}")

    # A galaxy where nothing changes cannot show that anything works. The
    # colonised planet has to grow, and so does the world the newcomer was
    # given, or the run is a screenshot rather than a game.
    log(f"\n=== the galaxy moved ===")
    for label, caps, pid in (("near target", j_caps, m["near"]),
                             ("joiner homeworld", j_caps, m["home"])):
        first, last = population(caps[0][1], pid), population(caps[-1][1], pid)
        log(f"  {label} #{pid}: owner {first[0]} pop {first[1]} at turn "
            f"{caps[0][0]} -> owner {last[0]} pop {last[1]} at turn "
            f"{caps[-1][0]}")
        if last[1] <= first[1]:
            log(f"  FAIL: #{pid} did not grow, so the run proves nothing")
            ok = False

    # The joined galaxy must still hold the civ that joined, with the homeworld
    # it was given. A civ the engine dropped on load would leave every other
    # check above passing.
    last = j_caps[-1][1]
    still = oid_of(last, m["joiner"])
    home_owner = owner_of(last, m["home"])
    log(f"  {m['joiner']!r} after {turns} turn(s): object id {still}, "
        f"planet #{m['home']} owner {home_owner}")
    if still is None or home_owner != still:
        log(f"  FAIL: the joined civ or its homeworld did not survive the run")
        ok = False

    log(f"\n  K1 live half: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def k1_load():
    m = meta()
    log(f"=== K1 load: joined.dat stamped for {m['joiner']!r}, the civ that "
        f"joined, in the build a player runs ===")
    turn = load_check(path("joined_as_new.dat"),
                      f"join_acceptance K1 load as {m['joiner']}", exe="player")
    log(f"  the newcomer's client opened the galaxy at turn {turn}")
    return 0


# ── K2 ───────────────────────────────────────────────────────────────────────
def k2(fixture, joins, turns, secs=10, stamp=None, stamp_mode="newcomer"):
    """N joins spread across M turns of one galaxy.

    Each segment plays its turns from a cold launch and captures once, so the
    blob every join is applied to is one the engine wrote. The civ a segment is
    stamped for is by default the one that joined last, which is the only stamp
    that shows a newcomer is playable rather than merely present. `seat-one`
    keeps every segment on the civ the galaxy started with, which is the mode to
    fall back to if a newcomer's client turns out to want something from a
    person before it will advance a turn.
    """
    blob = sp.load_any(fixture)
    schedule = [round((i + 1) * turns / joins) for i in range(joins)]
    log(f"=== K2: {joins} join(s) across {turns} turn(s) of "
        f"{os.path.basename(fixture)} ===")
    log(f"  joining after turns {schedule}")
    log(f"  starting at turn {wa.turn_of(blob)} with "
        f"{civ_count(blob)} civ(s), high-water {high_water(blob)}")

    rows, done, last_oid = [], 0, 0
    seat = stamp or icv.seat_one(icv.owner_records(blob))["name"]
    stamp_for = seat
    for i, at in enumerate(schedule):
        step = at - done
        name = f"Join{i + 1:02d}"

        if step:
            dat = write(f"k2_{i:02d}_in.dat",
                        set_blob_player.set_player(blob, stamp_for,
                                                   log=lambda *a: None))
            log(f"\n--- segment {i + 1}/{joins}: {step} turn(s) as "
                f"{stamp_for!r} ---")
            start, caps = play(dat, step, f"join_acceptance K2 segment {i + 1}",
                               f"k2s{i:02d}", secs=secs, every=step)
            got = caps[-1][0]
            if got - start < step:
                raise SystemExit(f"segment {i + 1} asked for {step} turn(s) "
                                 f"from turn {start} and reached {got}")
            blob = caps[-1][1]
            done = at

        log(f"\n--- join {i + 1}/{joins}: {name!r} at turn {wa.turn_of(blob)} ---")
        blob, home_id = icv.add_civ(blob, name, log=log)
        new_oid = oid_of(blob, name)
        if new_oid <= last_oid:
            raise SystemExit(f"object ids did not grow: {name} took {new_oid} "
                             f"after {last_oid}")
        last_oid = new_oid
        stamp_for = name if stamp_mode == "newcomer" else seat
        rows.append({
            "join": i + 1, "name": name, "turn": wa.turn_of(blob),
            "oid": new_oid, "home": home_id,
            "civs": civ_count(blob), "ownr": len(icv.owner_records(blob)),
            "high_water": high_water(blob), "max_id": idg.max_object_id(blob),
        })
        log(f"  civ count {rows[-1]['civs']}, {rows[-1]['ownr']} OWNR, "
            f"high-water {rows[-1]['high_water']}")

    # One last segment, so the final join is played rather than only written.
    dat = write("k2_final_in.dat",
                set_blob_player.set_player(blob, stamp_for,
                                           log=lambda *a: None))
    log(f"\n--- final segment: 1 turn as {stamp_for!r} ---")
    start, caps = play(dat, 1, "join_acceptance K2 final segment", "k2fin", secs=secs)
    blob = caps[-1][1]
    write("k2_out.dat", blob)

    log(f"\n=== K2 rows ===")
    log(f"  {'join':>4} {'turn':>5} {'oid':>6} {'home':>6} {'civs':>5} "
        f"{'OWNR':>5} {'high':>6} {'maxid':>6}")
    for r in rows:
        log(f"  {r['join']:>4} {r['turn']:>5} {r['oid']:>6} {r['home']:>6} "
            f"{r['civs']:>5} {r['ownr']:>5} {r['high_water']:>6} "
            f"{r['max_id']:>6}")

    owners = icv.owner_records(blob)
    count, hw, mx = civ_count(blob), high_water(blob), idg.max_object_id(blob)
    log(f"\n=== verdict, the blob the engine wrote back ===")
    log(f"  turn {wa.turn_of(blob)}, {len(blob):,} bytes")
    log(f"  civ count field {count}, OWNR records {len(owners)}: "
        f"{[o['name'] for o in owners]}")
    log(f"  high-water id {hw}, largest object id in the blob {mx}")

    ok = True
    if count != len(owners):
        log(f"  FAIL: the civ count field and the OWNR records disagree")
        ok = False
    if len(owners) != joins + rows[0]["ownr"] - 1:
        log(f"  FAIL: expected {joins + rows[0]['ownr'] - 1} civ(s)")
        ok = False
    if hw < mx:
        log(f"  FAIL: the high-water id is below an id in use, so the next "
            f"allocation would collide")
        ok = False
    ids = [r["oid"] for r in rows]
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        log(f"  FAIL: object ids did not grow monotonically: {ids}")
        ok = False
    for name in [r["name"] for r in rows]:
        if oid_of(blob, name) is None:
            log(f"  FAIL: {name!r} is not in the final blob")
            ok = False

    log(f"\n  K2: {'PASS' if ok else 'FAIL'}, {len(rows)} join(s), "
        f"turn {wa.turn_of(sp.load_any(fixture))} to {wa.turn_of(blob)}")
    with open(path("k2_rows.json"), "w") as f:
        json.dump({"rows": rows, "ok": ok, "civs": count, "ownr": len(owners),
                   "high_water": hw, "max_id": mx,
                   "turn": wa.turn_of(blob)}, f, indent=2)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("step", choices=["prepare", "k1", "k1load", "k2", "load"])
    ap.add_argument("--dat", default="k2_out.dat",
                    help="with load, which file in server/join_work to open")
    ap.add_argument("--exe", default=None,
                    help="with load, which client build; default resurgence")
    ap.add_argument("--fixture", default=DEFAULT_FIXTURE)
    ap.add_argument("--civ", default="DemoPlayer",
                    help="the civ that does the colonising in K1")
    ap.add_argument("--joiner", default="Joiner", help="the civ K1 adds")
    ap.add_argument("--turns", type=int, default=12)
    ap.add_argument("--joins", type=int, default=12)
    ap.add_argument("--secs", type=int, default=10)
    ap.add_argument("--stamp", default=None,
                    help="with k2, the civ the first segment is played as; "
                         "defaults to seat one")
    ap.add_argument("--stamp-mode", choices=["newcomer", "seat-one"],
                    default="newcomer",
                    help="with k2, which civ each later segment is played as")
    a = ap.parse_args()

    if a.step == "prepare":
        prepare(a.fixture, a.civ, a.joiner)
        return 0
    if a.step == "k1":
        return k1(a.turns, secs=a.secs)
    if a.step == "k1load":
        return k1_load()
    if a.step == "load":
        # A result of a run is not shown to load by the run that produced it:
        # the client that wrote it was already open. Opening it cold is the
        # only thing that says the blob the engine wrote back is one the engine
        # will take, which is the check K4's tick step exists to make.
        blob = sp.load_any(path(a.dat))
        log(f"{a.dat}: turn {wa.turn_of(blob)}, {len(blob):,} bytes, "
            f"{civ_count(blob)} civ(s), high-water {high_water(blob)}")
        load_check(path(a.dat), f"join_acceptance load {a.dat}", exe=a.exe)
        return 0
    if a.step == "k2":
        return k2(a.fixture, a.joins, a.turns, secs=a.secs, stamp=a.stamp,
                  stamp_mode=a.stamp_mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
