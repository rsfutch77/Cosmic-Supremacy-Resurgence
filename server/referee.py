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

A submission the referee cannot use is dropped with a note and the turn closes
without it. At four hours a turn, a submission that ends the tick costs every
player in the galaxy their turn, which is a far worse outcome than one player
losing the orders they sent. `screen_submission` is the cheap half of that and
`merge_orders.merge` catches the rest. None of it is a security measure:
cheating is accepted for this beta and said so in the player-facing text.

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
import inject_civ as icv
import merge_orders
import canonical
import abandonment
import joins
import turn_store
from turn_store import TurnStore, open_store

# The largest submission the referee will read, as a decompressed blob. The
# galaxy this was written against is 39 KB served and 39 KB back, so nobody
# plays anywhere near this; it is a ceiling on what one client can make the
# referee hold. The wire form is capped separately and lower, at 2 MB, by the
# storage rule, because that is where a submission lands before anything here
# sees it.
MAX_SUBMISSION_BYTES = 8 * 1024 * 1024

# The referee runs unattended behind a scheduled task, and a console child
# started without this is a window on the desktop at every tick.
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def turn_of(blob: bytes) -> int:
    """The turn a blob is at, so the referee reports the engine's number
    rather than its own arithmetic."""
    import turn_store
    return turn_store.turn_of(blob)


def apply_orders(blob: bytes, submissions, log=print, notes=None) -> bytes:
    """Take each player's orders out of the state they returned.

    submissions: [(civ_name, submitted_blob)]. Only changes on objects the
    authoritative blob says that civ owns are taken; everything else is dropped
    and named. See merge_orders.py for what "orders" currently covers.

    `notes` is an optional `{civ: [reason]}` filled with every refusal, so the
    player who made a dropped order can be told. `resolve_turn` writes what
    lands there into the store.
    """
    if not submissions:
        return blob
    return merge_orders.merge(blob, submissions, log=log, notes=notes)


def galaxy_id(blob: bytes):
    """What says two blobs are the same galaxy: the civs in it by object id and
    name, the systems by object id, and the planets by object id.

    None of the three moves while a player is taking a turn. A turn is played
    offline against a served state and nothing is created or destroyed until
    the tick, so a submission carries the same civs, suns and planets it was
    handed. Measured on the three real submissions in `galaxy_demo`, turns 7, 8
    and 9: all three sets are identical between each served turn and the state
    the player handed back, including the turn that queued a build and
    reassigned a citizen.

    Sun and planet object ids alone would not separate two galaxies, because
    ids are allocated in order from 1 and two galaxies start the same way. The
    civ names and the sizes of the three sets are what carry the answer.

    Computing this walks every OWNR, SUN and PLNT in the blob, which is most of
    what the merge will read, so a submission whose sections are internally
    inconsistent fails here rather than several thousand lines later.
    """
    civs = sorted((o['oid'], o['name']) for o in icv.owner_records(blob))
    return (tuple(civs),
            tuple(sorted(merge_orders.systems(blob))),
            tuple(sorted(merge_orders.planet_index(blob))))


def screen_submission(blob: bytes, civ: str, sub: bytes, turn: int,
                      served_id=None):
    """(ok, reason) for one submission, before any of it is believed.

    Cheap checks in the order that costs least, and nothing that needs the
    rules. Whether an order is legal is a different question, it is C4, and it
    is not asked here; this asks only whether these bytes can be used at all.
    Every answer is phrased as something the player who sent it can read,
    because it goes to them as a note.

    Each case here was measured against `merge` as it stood. A truncated blob,
    an empty one and bytes that are not a save each raised out of the merge and
    ended the turn for the whole galaxy. A submission from another turn and one
    from another galaxy did something worse than raise: they merged, and one
    order out of a state nobody is playing was written into the galaxy.

    `served_id` is `galaxy_id(blob)` when the caller already has it. It is the
    expensive part of this, a second on a 121 KB galaxy, and it is the same
    answer for every submission in a turn.
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
        # merge_orders raises SystemExit for a civ the galaxy does not hold,
        # which is right for a person running it with a typo and wrong for a
        # turn being closed. The roster is checked before this and a civ can
        # still be in the roster and not in the galaxy, a wiped civ being the
        # case that will arrive.
        return False, f'submission DROPPED, {civ} is not a civ in this galaxy'
    # The served blob's own identity is read outside the guard below. A galaxy
    # the referee cannot read is the referee's problem and has to be raised as
    # one, rather than reported to a player as something wrong with what they
    # sent.
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

    A store decodes each submission as it reads it, so one player whose bytes
    are not a save takes the whole listing down with them and the referee never
    reaches a check that could name whose fault it was. Measured against the
    directory store with a submission truncated on the wire: `submissions`
    raises `zlib.error` and the turn ends there, for everyone.

    The listing is tried first because it is one operation and it is what the
    stores are built around. Only when it fails is the roster read one civ at a
    time, which costs a read per player and attributes the fault to the player
    who caused it. A civ outside the roster is not looked for on that path,
    which matches `resolve_turn` ignoring their submission anyway, and a galaxy
    started without a roster at all has nothing to read one civ at a time by,
    so on that path it closes the turn with no submissions rather than not at
    all. Every galaxy the beta starts has a roster.

    `notes` and `dropped` are filled the same way `screen_submission`'s
    refusals are, so a submission refused here and one refused there read the
    same to the player and to the archive.
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


def tick(blob: bytes, turns: int = 1, secs: int = 10, work_dir=None,
         save_dir=None, log=print, check_save_path=True) -> bytes:
    """Advance the galaxy by `turns` turns and return the resulting blob.

    `save_dir` is where the capture will land, which is wherever the stub server
    was told to keep its data. It has to be said rather than assumed: a launcher
    hosting the server runs it against its own data directory, and a referee
    looking in the checkout's would wait for a file that is being written
    somewhere else.

    The save path is checked before the client is launched, for the same reason
    `player_turn.serve` checks it before serving: the tick takes minutes, ends
    in a `SaveGame`, and a `SaveGame` with nothing listening fails after the
    work is done. This module's header has always named `cs_server.py` on 8888
    as a requirement and nothing verified it, so the failure arrived as
    `SaveGame returned 0` from inside the client rather than as a missing
    server. Measured: with the port closed, a real turn resolution ran the whole
    merge, launched the client, advanced the galaxy and only then lost it.
    """
    import game_cycle as gc
    import player_turn

    if check_save_path:
        ok, why = player_turn.save_path_ready()
        if not ok:
            raise SystemExit(f'refusing to tick: {why}')

    work_dir = work_dir or os.path.join(HERE, "referee_work")
    os.makedirs(work_dir, exist_ok=True)
    dat = os.path.join(work_dir, f"tick_{int(time.time())}.dat")
    with open(dat, "wb") as f:
        f.write(blob)
    log(f"  referee: {len(blob):,} bytes -> {os.path.basename(dat)}")

    # The referee can afford to wait; a player mid-turn cannot afford
    # for it not to. wait_for_client_free already watched for the client
    # to exit, and this closes the gap between that check and the launch.
    # `restart` takes the lock before closing anything, so a referee that
    # cannot have the client is refused rather than ending someone's turn.
    snap = gc.restart(dat, purpose="referee tick", wait_for_lock=240.0)
    start = snap.turn
    log(f"  referee: galaxy up at turn {start}, advancing {turns}")

    # A subprocess on sys.executable is safe here and is not in the frozen
    # build. The referee runs from a checkout, where sys.executable is a real
    # interpreter; the release freezes release/launcher.py, which reaches
    # player_turn and game_cycle but never this module. Computing turns stays
    # a checkout job.
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

    The save path is checked here as well as in `tick`, because between the two
    this writes each player's refusal notes. `tick` refusing would leave notes
    for a turn that did not close, and the next attempt would write them again.
    Checking first means a referee that cannot save changes nothing at all.
    """
    import player_turn
    ok, why = player_turn.save_path_ready()
    if not ok:
        raise SystemExit(f'refusing to close the turn: {why}')

    turn, _deadline = store.current()
    blob = store.turn_blob(turn)
    roster = store.civs()

    # Filled from here rather than at the merge, because a submission dropped
    # before the merge is refused as much as an order dropped inside it, and
    # the player is owed the same note either way.
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
            # Nothing of a dropped submission reaches the galaxy, so to every
            # other player this civ did not submit. Only the player who sent it
            # is told, and the reason is kept in the archive for the operator.
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

    # Tell each player what was refused. Until now every refusal was logged
    # here, on the referee's console, which on the player's machine is nowhere:
    # a system rename and a conscription were both dropped in the first
    # two-machine rehearsal and both players saw only their order missing the
    # next turn. The note goes in before the tick, which takes minutes, so it
    # is certainly there by the time a player's launcher sees the new turn and
    # goes looking for it.
    #
    # It adds to whatever that turn's note already holds rather than replacing
    # it. A player seated at the previous boundary was left a note on this
    # turn, which is the first turn they play and so the first one
    # `player_turn.report_refusals` reads for them, and a replace here would
    # take it away before they ever saw it.
    for civ, lines in sorted(notes.items()):
        try:
            joins.append_note(store, civ, turn, lines, log=log)
            log(f"  referee: {len(lines)} refusal(s) noted for {civ}")
        except OSError as exc:
            # A note that cannot be written is not worth losing a turn over.
            log(f"  referee: could not leave {civ} a note about turn {turn}, "
                f"{exc}")

    nxt = tick(merged, turns=1, save_dir=save_dir, log=log)
    new_turn = turn_of(nxt)
    if new_turn <= turn:
        # The engine decides the turn number, so trust it and say so rather than
        # inventing one: a galaxy that did not advance is a fault to surface.
        log(f"  referee: WARNING the tick produced turn {new_turn}, "
            f"not past {turn}")

    # Warn, then reclaim, the seats of players who have stopped. It runs on the
    # blob the tick produced and before it is published, because a reclaim
    # rewrites that blob: publishing first and correcting afterwards would
    # restart the clock on a turn players already hold. `turn` is the turn just
    # closed, not the new one, because that is the turn the miss is counted
    # against. A warning is left on `new_turn`, since that is the turn the
    # silent player is served next and the note a launcher reads. It
    # never raises for an abandonment-shaped problem; a wipe it cannot do
    # leaves the seat and is counted again next turn.
    nxt = abandonment.enforce(store, turn, nxt, log=log, new_turn=new_turn)

    # Then seat whoever asked to join during the turn that just closed, on the
    # same blob and for the same reason: a join applied anywhere but here is
    # applied to a copy, and the next publish writes over it. Abandonment runs
    # first because a reclaim frees planets and a name that a join in the same
    # tick can then use, and because a wipe needs an uncolonised planet for its
    # blank PLPR, which is the one thing a join consumes. See joins.py.
    #
    # `apply` changes bytes and nothing else. Every durable write is in
    # `commit`, below the publish, so a referee killed between them leaves a
    # galaxy holding an empire nobody is seated on, which an operator can
    # answer by adding the name, rather than a roster naming a civ the galaxy
    # does not hold, which every submission from that player then fails the
    # screen for, turn after turn, with nothing to fix.
    nxt, joined = joins.apply(store, turn, new_turn, nxt, log=log)

    store.publish(new_turn, nxt)
    joins.commit(store, turn, new_turn, joined, log=log)
    store.archive(turn, {
        "turn": turn,
        "published": new_turn,
        "submitted": sorted(dict(taken)),
        "missing": missing,
        "ignored": ignored,
        # A dropped submission is a missing one to the rest of the galaxy, so
        # the civ is in `missing` above as well. The reason is here because it
        # is the only place the operator can read why a player who says they
        # played is recorded as absent.
        "dropped": refused,
        # Who joined at this boundary and who was refused one, which is the
        # only place an operator can read why a player who pressed Join is not
        # in the galaxy. The blob's own civ count says a join happened and
        # never says whose request it was.
        "joined": [{k: v for k, v in o.items() if k != "path"}
                   for o in joined],
        "bytes_in": len(blob),
        "bytes_out": len(nxt),
        "closed_at": time.time(),
        "refused": notes,
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
