"""
referee.py , the authoritative turn, as two calls
=================================================
    python referee.py auth.b64 --from BadGuy=sub.b64 --turns 1 -o next.b64

    from referee import apply_orders, tick
    blob = apply_orders(blob, [("BadGuy", submitted_blob)])
    blob = tick(blob)

The server owns one galaxy blob. It hands each player a state, takes their state
back, keeps only the parts that express their intent, and advances the clock.
`apply_orders` is the first half and `tick` is the second.

`tick` runs the engine, because the engine is the only implementation of the
rules that exists. It writes the blob to a file, starts a client on it, drives
the turn clock, captures the result and closes the client. The turn is a pure
function of state plus orders, measured over 20 turns across three separate
process launches, so a turn computed this way is reproducible.

The two calls are kept apart on purpose. A Python reimplementation of the rules
would replace `tick` alone, and every caller keeps working.

`loop` is the unattended form: it watches a `TurnStore`, and when a turn's
deadline passes it takes whatever players submitted, applies it, ticks, and
publishes the next turn. It never waits for a submission. The original advanced
on the clock whether a player was there or not, and a turn that waits for
everyone is a turn one absent player can stall forever.

Requirements: `cs_server.py` on port 8888, since the capture arrives over its
`savegame` endpoint, and the Resurgence client, which is the build patched to
advance turns without a server.
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEV = os.path.join(REPO, "client", "dev_tools")
sys.path.insert(0, DEV)
sys.path.insert(0, os.path.join(DEV, "ai_player"))
sys.path.insert(0, os.path.join(HERE, "dev_tools"))

import save_parser as sp
import merge_orders
import canonical
from turn_store import TurnStore, open_store


def turn_of(blob: bytes) -> int:
    """The turn a blob is at, so the referee reports the engine's number
    rather than its own arithmetic."""
    import turn_store
    return turn_store.turn_of(blob)


def apply_orders(blob: bytes, submissions, log=print) -> bytes:
    """Take each player's orders out of the state they returned.

    submissions: [(civ_name, submitted_blob)]. Only changes on objects the
    authoritative blob says that civ owns are taken; everything else is dropped
    and named. See merge_orders.py for what "orders" currently covers.
    """
    if not submissions:
        return blob
    return merge_orders.merge(blob, submissions, log=log)


def tick(blob: bytes, turns: int = 1, secs: int = 10, work_dir=None,
         save_dir=None, log=print) -> bytes:
    """Advance the galaxy by `turns` turns and return the resulting blob.

    `save_dir` is where the capture will land, which is wherever the stub server
    was told to keep its data. It has to be said rather than assumed: a launcher
    hosting the server runs it against its own data directory, and a referee
    looking in the checkout's would wait for a file that is being written
    somewhere else.
    """
    import game_cycle as gc
    import player_turn

    work_dir = work_dir or os.path.join(HERE, "referee_work")
    os.makedirs(work_dir, exist_ok=True)
    dat = os.path.join(work_dir, f"tick_{int(time.time())}.dat")
    with open(dat, "wb") as f:
        f.write(blob)
    log(f"  referee: {len(blob):,} bytes -> {os.path.basename(dat)}")

    gc.close_client()
    # The referee can afford to wait; a player mid-turn cannot afford
    # for it not to. wait_for_client_free already watched for the client
    # to exit, and this closes the gap between that check and the launch.
    snap = gc.launch(dat, purpose="referee tick", wait_for_lock=240.0)
    start = snap.turn
    log(f"  referee: galaxy up at turn {start}, advancing {turns}")

    r = subprocess.run([sys.executable, os.path.join(DEV, "advance_turns.py"),
                        str(turns), "--secs", str(secs)],
                       capture_output=True, text=True, cwd=DEV)
    if r.returncode != 0:
        log(r.stdout + r.stderr)
        raise SystemExit("advance_turns failed")

    capture = player_turn.collect("referee", save_dir=save_dir, log=log)
    out = sp.load_any(capture)
    gc.close_client()
    log(f"  referee: turn {start} -> captured {len(out):,} bytes")
    return out


def resolve_turn(store: TurnStore, save_dir=None, log=print) -> int:
    """Close the current turn and publish the next one. Returns the new turn."""
    turn, _deadline = store.current()
    blob = store.turn_blob(turn)
    submitted = store.submissions(turn)
    roster = store.civs()

    taken, ignored = [], []
    for civ, sub in submitted.items():
        if roster and civ not in roster:
            ignored.append(civ)
            continue
        taken.append((civ, sub))
    if ignored:
        log(f"  referee: ignoring submissions from {ignored}, not in the roster")
    missing = [c for c in roster if c not in dict(taken)]
    if missing:
        log(f"  referee: no submission from {missing}; the clock does not wait")

    log(f"  referee: closing turn {turn} with {len(taken)} submission(s)")
    merged = apply_orders(blob, taken, log=log)
    nxt = tick(merged, turns=1, save_dir=save_dir, log=log)
    new_turn = turn_of(nxt)
    if new_turn <= turn:
        # The engine decides the turn number, so trust it and say so rather than
        # inventing one: a galaxy that did not advance is a fault to surface.
        log(f"  referee: WARNING the tick produced turn {new_turn}, "
            f"not past {turn}")
    store.publish(new_turn, nxt)
    store.archive(turn, {
        "turn": turn,
        "published": new_turn,
        "submitted": sorted(dict(taken)),
        "missing": missing,
        "ignored": ignored,
        "bytes_in": len(blob),
        "bytes_out": len(nxt),
        "closed_at": time.time(),
        # Enough to recompute this turn later and check the answer. The
        # submissions are hashed too, so a rerun that disagrees can be told
        # apart from a rerun given different orders.
        "hash_in": canonical.canonical_hash(blob),
        "hash_out": canonical.canonical_hash(nxt),
        "hash_submissions": {civ: canonical.canonical_hash(s)
                             for civ, s in taken},
    })
    log(f"  referee: published turn {new_turn}, "
        f"canonical {canonical.canonical_hash(nxt)[:16]}")
    return new_turn


def verify_turn(store: TurnStore, turn: int, recompute: bool = True,
                save_dir=None, log=print) -> bool:
    """Check a past turn against what the referee recorded for it.

    Two questions, and they fail differently. Does the stored blob still match
    the hash written when it was published, which catches an archive that has
    been corrupted or edited; and does running the turn again produce the same
    answer, which catches a referee that computed something different. The
    second costs a real tick, so it can be turned off.
    """
    rec = store.archive_record(turn)
    if rec is None:
        log(f"  verify: turn {turn} has no archive record")
        return False
    if "hash_out" not in rec:
        log(f"  verify: turn {turn} was archived before hashes were recorded")
        return False

    published = rec["published"]
    ok = True

    if store.has_turn(published):
        stored = canonical.canonical_hash(store.turn_blob(published))
        same = stored == rec["hash_out"]
        log(f"  verify: stored turn {published} "
            f"{'matches' if same else 'does NOT match'} its record "
            f"({stored[:16]} against {rec['hash_out'][:16]})")
        ok &= same
    else:
        log(f"  verify: turn {published} is not in the store")
        ok = False

    if not recompute:
        return ok

    blob = store.turn_blob(turn)
    if canonical.canonical_hash(blob) != rec["hash_in"]:
        log(f"  verify: turn {turn}'s own blob no longer matches what was "
            f"closed; a rerun would not be comparable")
        return False

    subs = store.submissions(turn)
    for civ, want in rec.get("hash_submissions", {}).items():
        if civ not in subs:
            log(f"  verify: {civ}'s submission is missing")
            return False
        if canonical.canonical_hash(subs[civ]) != want:
            log(f"  verify: {civ}'s submission is not the one that was used")
            return False

    taken = [(civ, subs[civ]) for civ in sorted(rec.get("hash_submissions", {}))]
    log(f"  verify: recomputing turn {turn} with {len(taken)} submission(s)")
    merged = apply_orders(blob, taken, log=log)
    again = tick(merged, turns=1, save_dir=save_dir, log=log)
    got = canonical.canonical_hash(again)
    same = got == rec["hash_out"]
    log(f"  verify: recomputation {'agrees' if same else 'DISAGREES'} "
        f"({got[:16]} against {rec['hash_out'][:16]})")
    if not same:
        for off, x, y in canonical.differences(store.turn_blob(published), again):
            log(f"    {off:#08x}  {x:#04x} -> {y:#04x}   "
                f"{canonical.locate(again, off)}")
    return ok and same


def wait_for_client_free(timeout: float = 180.0, poll: float = 2.0,
                         log=print) -> bool:
    """Wait until no game client is running, so ticking cannot steal one.

    On one machine the referee and the players share a single game process, and
    `tick` closes whatever is running before it starts its own. The instant a
    deadline passes, a player's launcher is still capturing their turn, so
    ticking immediately would close their client mid-capture and lose the orders
    it was in the middle of writing. Waiting costs a few seconds and is the
    difference between a turn and a lost turn.

    In the deployment this is heading for the referee is on another host and this
    returns immediately, which is the right shape for it: a check that becomes
    free rather than a rule that has to be remembered.
    """
    import game_cycle as gc
    end = time.time() + timeout
    said = False
    while time.time() < end:
        if not gc.client_pids():
            return True
        if not said:
            log("  referee: a client is still running, waiting for it to finish")
            said = True
        time.sleep(poll)
    log(f"  referee: a client is still running after {timeout:.0f}s; "
        f"ticking anyway")
    return False


def loop(store: TurnStore, poll: float = 5.0, once: bool = False,
         grace: float = 20.0, save_dir=None, log=print):
    """Resolve each turn as its deadline passes, for as long as asked.

    `grace` is how long after the deadline to keep accepting submissions. A
    player's launcher needs a few seconds to trigger a save, wait for it to
    arrive over HTTP and write it to the store, and a turn resolved inside that
    window drops orders the player did give.
    """
    while True:
        left = store.seconds_left()
        if left > 0:
            if once:
                log(f"  referee: turn {store.current()[0]} has "
                    f"{left:.0f}s left, nothing to do")
                return
            time.sleep(min(poll, left))
            continue
        if -left < grace:
            log(f"  referee: turn {store.current()[0]} is due, holding "
                f"{grace - -left:.0f}s more for submissions")
            time.sleep(min(poll, grace - -left))
            continue
        wait_for_client_free(log=log)
        resolve_turn(store, save_dir=save_dir, log=log)
        if once:
            return


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("authoritative", nargs="?")
    ap.add_argument("--from", dest="subs", action="append", default=[],
                    metavar="CIV=FILE", help="a player submission; repeatable")
    ap.add_argument("--turns", type=int, default=1)
    ap.add_argument("--secs", type=int, default=10,
                    help="turn length used while driving; the engine clamps "
                         "below 60 without patch_turn_floor.py")
    ap.add_argument("--turn-seconds", type=int, default=3600,
                    help="with --start, how long players get per turn")
    ap.add_argument("-o", "--out", help="write the next state as a .b64 capture")
    ap.add_argument("--dat", help="also write it decompressed, for a client")
    ap.add_argument("--no-tick", action="store_true",
                    help="apply orders only, do not run the engine")
    ap.add_argument("--store",
                    help="a turn store: a directory, or the base URL of a "
                         "turn_server.py")
    ap.add_argument("--loop", action="store_true",
                    help="with --store, resolve turns as their deadlines pass")
    ap.add_argument("--once", action="store_true",
                    help="with --store, resolve at most one turn and exit")
    ap.add_argument("--verify", type=int, metavar="TURN",
                    help="with --store, check a past turn against its record")
    ap.add_argument("--no-recompute", action="store_true",
                    help="with --verify, check hashes only, do not run a tick")
    ap.add_argument("--grace", type=float, default=20.0,
                    help="seconds past the deadline to keep taking submissions")
    ap.add_argument("--save-dir",
                    help="where captures land, which is the data directory of "
                         "whoever is hosting cs_server; defaults to the "
                         "checkout's server/saves")
    ap.add_argument("--start", action="store_true",
                    help="with --store, publish the authoritative blob as the "
                         "first turn and start the clock")
    ap.add_argument("--civ", action="append", default=[],
                    help="with --start, a civ in the roster; repeatable")
    a = ap.parse_args()

    if a.store:
        store = open_store(a.store)
        if a.start:
            if not a.authoritative:
                raise SystemExit("--start needs a blob to publish")
            blob = sp.load_any(a.authoritative)
            turn = store.start(blob, civs=a.civ, turn_seconds=a.turn_seconds)
            print(f"turn {turn} published, {a.turn_seconds}s on the clock, "
                  f"roster {a.civ or '(none)'}")
            return 0
        if not store.exists():
            raise SystemExit(f"no galaxy in {a.store}; run --start first")
        if a.verify is not None:
            ok = verify_turn(store, a.verify, recompute=not a.no_recompute,
                             save_dir=a.save_dir)
            print('verified' if ok else 'NOT verified')
            return 0 if ok else 1
        if a.loop or a.once:
            loop(store, once=a.once, grace=a.grace, save_dir=a.save_dir)
            return 0
        turn, deadline = store.current()
        print(f"turn {turn}, {store.seconds_left():.0f}s left, "
              f"submitted: {sorted(store.submissions(turn)) or '(none)'}")
        print(f"canonical {canonical.canonical_hash(store.turn_blob(turn))}")
        return 0

    if not a.authoritative:
        raise SystemExit("give a blob, or --store with what to do to it")
    blob = sp.load_any(a.authoritative)
    submissions = []
    for spec in a.subs:
        if "=" not in spec:
            raise SystemExit(f"--from wants CIV=FILE, got {spec!r}")
        civ, path = spec.split("=", 1)
        submissions.append((civ, sp.load_any(path)))

    blob = apply_orders(blob, submissions)
    if not a.no_tick:
        blob = tick(blob, turns=a.turns, secs=a.secs, save_dir=a.save_dir)

    if a.out:
        open(a.out, "wb").write(sp.encode_save(blob))
        print(f"wrote {a.out}")
    if a.dat:
        if not a.dat.lower().endswith(".dat"):
            raise SystemExit("--dat path must end in .dat")
        open(a.dat, "wb").write(blob)
        print(f"wrote {a.dat}")
    if not a.out and not a.dat:
        print(f"{len(blob):,} bytes (nothing written; pass -o or --dat)")


if __name__ == "__main__":
    sys.exit(main())
