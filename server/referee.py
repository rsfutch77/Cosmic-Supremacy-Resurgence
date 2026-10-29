"""
referee.py: the authoritative turn, as two calls

    python referee.py auth.b64 --from BadGuy=sub.b64 --turns 1 -o next.b64

    from referee import apply_orders, tick
    blob = apply_orders(blob, [("BadGuy", submitted_blob)])
    blob = tick(blob)

`apply_orders` keeps the parts of each player's returned state that express
their intent. `tick` runs the engine: it writes the blob to a file, starts a
client on it, drives the turn clock, captures the result and closes the client.
A turn is a pure function of state plus orders, so it is reproducible.

A submission the referee cannot use is dropped with a note and the turn closes
without it; no submission may end the tick. `screen_submission` and
`merge_orders.merge` do this. They are not a security measure.

`loop` watches a `TurnStore` and resolves each turn when its deadline passes,
never waiting for a submission.

Requirements: `cs_server.py` on port 8888 (the capture arrives over its
`savegame` endpoint) and the Resurgence client.
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
import inject_civ as icv
import merge_orders
import canonical
import abandonment
import activity
import joins
import turn_store
from turn_store import TurnStore, open_store

MAX_SUBMISSION_BYTES = 8 * 1024 * 1024   # largest decompressed submission the referee reads

# Keeps a console child from opening a window at every tick.
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def turn_of(blob: bytes) -> int:
    """The turn a blob is at, as the engine numbers it."""
    import turn_store
    return turn_store.turn_of(blob)


def apply_orders(blob: bytes, submissions, log=print, notes=None) -> bytes:
    """Take each player's orders out of the state they returned.

    submissions: [(civ_name, submitted_blob)]. Only changes on objects the
    authoritative blob says that civ owns are taken; everything else is dropped
    and named. See merge_orders.py for what "orders" currently covers.

    `notes` is an optional `{civ: [reason]}` filled with every refusal.

    Each submitting civ is marked active for the tick, so its Inactivity
    penalty does not grow.
    """
    if not submissions:
        return blob
    merged = merge_orders.merge(blob, submissions, log=log, notes=notes)
    return activity.mark_present(merged, [civ for civ, _sub in submissions],
                                 log=log)


def galaxy_id(blob: bytes):
    """What says two blobs are the same galaxy: the civs in it by object id and
    name, the systems by object id, and the planets by object id.

    None of the three changes while a player takes a turn. Raises on a blob
    whose OWNR, SUN or PLNT sections are inconsistent.
    """
    civs = sorted((o['oid'], o['name']) for o in icv.owner_records(blob))
    return (tuple(civs),
            tuple(sorted(merge_orders.systems(blob))),
            tuple(sorted(merge_orders.planet_index(blob))))


def screen_submission(blob: bytes, civ: str, sub: bytes, turn: int,
                      served_id=None):
    """(ok, reason) for one submission, before any of it is believed.

    Checks only whether the bytes are usable (non-empty, within size, a save of
    this turn, a known civ, this galaxy), cheapest first; order legality is not
    checked here. The reason is sent to the player as a note.

    `served_id` is `galaxy_id(blob)` when the caller already has it; it is the
    expensive part and the same for every submission in a turn.
    """
    if not sub:
        return False, 'submission DROPPED, it is empty'
    if len(sub) > MAX_SUBMISSION_BYTES:
        return False, (f'submission DROPPED, {len(sub):,} bytes is over the '
                       f'{MAX_SUBMISSION_BYTES:,} byte limit')
    try:
        turn_store.check_save(sub, turn)
    except ValueError as exc:
        return False, f'submission DROPPED, {exc}'
    try:
        merge_orders.civ_by_name(blob, civ)
    except SystemExit:
        # merge_orders raises SystemExit for a civ the galaxy does not hold.
        return False, f'submission DROPPED, {civ} is not a civ in this galaxy'
    # Outside the guard: an unreadable served blob is the referee's error, not
    # the player's.
    if served_id is None:
        served_id = galaxy_id(blob)
    try:
        same = galaxy_id(sub) == served_id
    except Exception as exc:                                    # noqa: BLE001
        return False, (f'submission DROPPED, it does not read as a galaxy, '
                       f'{type(exc).__name__}: {exc}')
    if not same:
        return False, ('submission DROPPED, it is not this galaxy: its civs, '
                       'systems or planets are not the ones that were served')
    return True, ''


def read_submissions(store: TurnStore, turn: int, roster, log=print,
                     notes=None, dropped=None) -> dict:
    """{civ: blob} for a turn, with what cannot be decoded dropped and noted.

    Tries the whole listing first. If one undecodable submission makes that
    raise, the roster is read one civ at a time so the fault is attributed to
    its player. `notes` and `dropped` are filled as for `screen_submission`.
    """
    try:
        return store.submissions(turn)
    except Exception as exc:                                    # noqa: BLE001
        log(f"  referee: the submissions for turn {turn} could not be read "
            f"together ({type(exc).__name__}: {exc}); reading them one by one")
    out = {}
    for civ in roster:
        try:
            sub = store.submission(civ, turn)
        except Exception as exc:                                # noqa: BLE001
            reason = (f"submission DROPPED, its bytes are not a save the "
                      f"referee can decode, {type(exc).__name__}: {exc}")
            log(f"  referee: {civ}: {reason}")
            if notes is not None:
                notes.setdefault(civ, []).append(reason)
            if dropped is not None:
                dropped[civ] = reason
            continue
        if sub is not None:
            out[civ] = sub
    return out


def holds_a_planet(blob: bytes) -> bool:
    """Whether any civ in the blob owns a planet, which a tick needs."""
    civs = {o["oid"] for o in icv.owner_records(blob)}
    return any(p["owner"] in civs for p in icv.planet_records(blob))


def hold_empty(store: TurnStore, turn: int, outcomes, log=print) -> int:
    """Hold a galaxy nobody holds a planet in for one more turn. Returns `turn`.

    Nothing is ticked or published; the deadline moves one turn length on.
    Refused joins in `outcomes` are answered now.
    """
    joins.commit(store, turn, turn, outcomes or [], log=log)
    seconds = int(store.state().get("turn_seconds") or 3600)
    store.update_state({"deadline": time.time() + seconds})
    log(f"  referee: no civ in this galaxy holds a planet and nobody is "
        f"waiting to join; turn {turn} is held for {seconds}s")
    return turn


def stamp_for_a_holder(blob: bytes, log=print) -> bytes:
    """The blob with its local player set to a civ that holds a planet.

    The client exits during load when its local player owns no planet. A blob
    whose local player holds one is returned unchanged; otherwise the first
    civ in `OWNR` order that holds one is stamped.
    """
    import set_blob_player
    held = {p["owner"] for p in icv.planet_records(blob)}
    now = set_blob_player.get_player(blob)
    if now in held:
        return blob
    holder = next((o for o in icv.owner_records(blob) if o["oid"] in held), None)
    if holder is None:
        raise SystemExit("refusing to tick: no civ in this galaxy holds a "
                         "planet, and the client opens a galaxy only for a "
                         "civ that does")
    log(f"  referee: local player {now} holds no planet; stamping the tick "
        f"for {holder['name']!r}")
    return set_blob_player.set_player(blob, holder["name"], log=log)


def tick(blob: bytes, turns: int = 1, secs: int = 10, work_dir=None,
         save_dir=None, log=print, check_save_path=True) -> bytes:
    """Advance the galaxy by `turns` turns and return the resulting blob.

    `save_dir` is the stub server's data directory, where the capture lands.
    The save path is checked before the client is launched, since the tick
    ends in a `SaveGame`.
    """
    import game_cycle as gc
    import player_turn

    if check_save_path:
        ok, why = player_turn.save_path_ready()
        if not ok:
            raise SystemExit(f'refusing to tick: {why}')

    blob = stamp_for_a_holder(blob, log=log)
    work_dir = work_dir or os.path.join(HERE, "referee_work")
    os.makedirs(work_dir, exist_ok=True)
    dat = os.path.join(work_dir, f"tick_{int(time.time())}.dat")
    with open(dat, "wb") as f:
        f.write(blob)
    log(f"  referee: {len(blob):,} bytes -> {os.path.basename(dat)}")

    # `restart` takes the client lock before closing anything, so a client in
    # use by a player is never closed.
    snap = gc.restart(dat, purpose="referee tick", wait_for_lock=240.0)
    start = snap.turn
    log(f"  referee: galaxy up at turn {start}, advancing {turns}")

    # The referee runs from a checkout, so sys.executable is a real interpreter.
    r = subprocess.run([sys.executable, os.path.join(DEV, "advance_turns.py"),
                        str(turns), "--secs", str(secs)],
                       capture_output=True, text=True, cwd=DEV,
                       creationflags=_NO_WINDOW)
    if r.returncode != 0:
        log(r.stdout + r.stderr)
        raise SystemExit("advance_turns failed")

    capture = player_turn.collect("referee", save_dir=save_dir, log=log)
    out = sp.load_any(capture)
    gc.close_client()
    log(f"  referee: turn {start} -> captured {len(out):,} bytes")
    return out


def resolve_turn(store: TurnStore, save_dir=None, log=print) -> int:
    """Close the current turn and publish the next one. Returns the new turn.

    The save path is checked first so a referee that cannot save writes no
    refusal notes.
    """
    import player_turn
    ok, why = player_turn.save_path_ready()
    if not ok:
        raise SystemExit(f'refusing to close the turn: {why}')

    turn, _deadline = store.current()
    blob = store.turn_blob(turn)
    roster = store.civs()

    # Collects refusals from reading, screening and the merge alike.
    notes, refused = {}, {}
    submitted = read_submissions(store, turn, roster, log=log, notes=notes,
                                 dropped=refused)

    taken, ignored = [], []
    served_id = galaxy_id(blob) if submitted else None
    for civ, sub in submitted.items():
        if roster and civ not in roster:
            ignored.append(civ)
            continue
        ok, why = screen_submission(blob, civ, sub, turn, served_id=served_id)
        if not ok:
            # Only the sender is told; the reason is archived for the operator.
            refused[civ] = why
            notes.setdefault(civ, []).append(why)
            log(f"  referee: {civ}: {why}")
            continue
        taken.append((civ, sub))
    if ignored:
        log(f"  referee: ignoring submissions from {ignored}, not in the roster")
    missing = [c for c in roster if c not in dict(taken)]
    if missing:
        log(f"  referee: no submission from {missing}; the clock does not wait")

    log(f"  referee: closing turn {turn} with {len(taken)} submission(s)"
        + (f", {len(refused)} dropped" if refused else ""))
    merged = apply_orders(blob, taken, log=log, notes=notes)

    # Refusal notes are written before the tick, appended to any note the turn
    # already holds (such as a welcome note).
    for civ, lines in sorted(notes.items()):
        try:
            joins.append_note(store, civ, turn, lines, log=log)
            log(f"  referee: {len(lines)} refusal(s) noted for {civ}")
        except OSError as exc:
            # A note that cannot be written is not worth losing a turn over.
            log(f"  referee: could not leave {civ} a note about turn {turn}, "
                f"{exc}")

    # A galaxy in which no civ holds a planet cannot be ticked. Joins are then
    # applied before the tick; with nobody waiting the turn is held.
    early = None
    if not holds_a_planet(merged):
        merged, early = joins.apply(store, turn, turn + 1, merged, log=log)
        if not holds_a_planet(merged):
            return hold_empty(store, turn, early, log=log)

    nxt = tick(merged, turns=1, save_dir=save_dir, log=log)
    new_turn = turn_of(nxt)
    if new_turn <= turn:
        log(f"  referee: WARNING the tick produced turn {new_turn}, "
            f"not past {turn}")

    # Abandonment and joins run on the tick's blob before it is published, and
    # abandonment runs first so a join can use the planets a reclaim frees.
    nxt = abandonment.enforce(store, turn, nxt, log=log, new_turn=new_turn)

    # `joins.apply` changes only bytes; `joins.commit` must follow the publish.
    if early is None:
        nxt, joined = joins.apply(store, turn, new_turn, nxt, log=log)
    else:
        joined = early

    store.publish(new_turn, nxt)
    joins.commit(store, turn, new_turn, joined, log=log)
    store.archive(turn, {
        "turn": turn,
        "published": new_turn,
        "submitted": sorted(dict(taken)),
        "missing": missing,
        "ignored": ignored,
        # A dropped civ is also in `missing`.
        "dropped": refused,
        # Join outcomes granted and refused at this boundary.
        "joined": [{k: v for k, v in o.items() if k != "path"}
                   for o in joined],
        "bytes_in": len(blob),
        "bytes_out": len(nxt),
        "closed_at": time.time(),
        "refused": notes,
        # For `verify_turn` to recompute this turn and check the answer.
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

    Checks that the stored blob matches its recorded hash and, with
    `recompute`, that rerunning the turn gives the same answer (a real tick).
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
    """Wait until no game client is running, so a tick does not close a
    player's client mid-capture. Returns False on timeout."""
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

    `grace` is how many seconds after the deadline to keep accepting
    submissions, for launchers still capturing and uploading.
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
