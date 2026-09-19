"""
player_turn.py , serve one player their turn, and collect it back
=================================================================
    python player_turn.py serve   turn3.b64 --civ DemoPlayer
    python player_turn.py collect --name playerA
    python player_turn.py follow  --store <dir> --civ DemoPlayer

`serve` stamps the state with the civ this player controls, starts a client on
it, and stops the clock. `collect` takes the state back.

`follow` is the whole player-side loop, and it is what the launcher drives: wait
for the turn a `TurnStore` says is current, serve it, let the player play, and at
the deadline take their state back, submit it, and wait for the next turn. A
player does nothing manual between turns, which is the point: the `.dat` push
path is startup-only, so their client has to restart every turn, and the only way
that is acceptable is if nobody has to think about it.

Stopping the clock is not a nicety. A player's client that reaches a turn
boundary computes its own turn, and the state it hands back then mixes the
player's orders with a simulation the server never authorised. Extracting
orders from that is unsound: a diff of a ticked state against the state served
shows every consequence of the tick as well, and copying an order out of it
copies post-tick positions with it. Measured, a client left at turn 3 for a few
minutes returned turn 4 with 174 sections changed instead of one.

The clock lives at `0x0080AA08`, in seconds, and the engine re-applies
`GSET.turnlength` at every boundary, so a large value written after load holds
only while no boundary occurs, which is the point. This is the blunt version of
what the launcher is meant to do: own the turn clock, show the countdown, and
close the client when time is up.

Requires `cs_server.py` on port 8888, which is where `collect` receives the blob.
"""
import argparse
import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEV = os.path.join(REPO, "client", "dev_tools")
sys.path.insert(0, DEV)
sys.path.insert(0, os.path.join(DEV, "ai_player"))
sys.path.insert(0, os.path.join(HERE, "dev_tools"))

import save_parser as sp
import set_blob_player
from turn_store import TurnStore, open_store

HOLD_SECONDS = 86400          # a day; long enough that no boundary arrives
PLAYER_BUILD = "player"       # see game_cycle.resolve_exe for why


def hold_clock(seconds=HOLD_SECONDS, log=print):
    """Push the next turn boundary out of reach of a play session."""
    import advance_turns as at
    pid, name = at.find_pid()
    if not pid:
        raise SystemExit("no client to hold")
    access = (at.PROCESS_VM_READ | at.PROCESS_VM_WRITE |
              at.PROCESS_VM_OPERATION | at.PROCESS_QUERY_INFORMATION)
    h = at.kernel32.OpenProcess(access, False, pid)
    if not h:
        raise SystemExit(f"could not open pid {pid}")
    try:
        was = at.rd32(h, at.TURNLENGTH_ADDR)
        if was == 0xFFFFFFFF:
            raise SystemExit("turn length uninitialised; the galaxy is not up")
        at.wr32(h, at.TURNLENGTH_ADDR, seconds)
        log(f"  clock held: turn length {was}s -> {seconds}s")
        return was
    finally:
        at.kernel32.CloseHandle(h)


def describe_orders(served: bytes, submitted: bytes, civ: str) -> str:
    """A short account of what this submission actually carries.

    A submission that writes cleanly and reads back byte for byte can still be
    a turn where the player's click never reached the blob, and a log line
    saying only that some bytes were stored cannot tell the two apart. This
    says what changed and whose it is, so an empty turn is visible as an empty
    turn while it can still be acted on.
    """
    try:
        import order_diff
        lines = []
        mine, theirs, galaxy = order_diff.compare(
            served, submitted, civ, log=lines.append)
    except Exception as exc:                                # noqa: BLE001
        return f'could not summarise it ({type(exc).__name__})'
    if not (mine or theirs or galaxy):
        return 'NOTHING CHANGED, this turn carries no orders'
    # order_diff prefixes each object with a blank line, so strip before
    # matching: testing startswith on the raw line quietly matched nothing and
    # the summary said "changes" for everything.
    objs = []
    for line in lines:
        bare = line.lstrip('\n')
        if ' owner ' in bare and bare.startswith('  '):
            parts = bare.split()
            if len(parts) >= 2:
                objs.append(f'{parts[0]} {parts[1]}')
    what = ', '.join(objs[:4]) or 'changes'
    extra = ''
    if theirs:
        extra += f", {theirs} on another civ's objects, which will be dropped"
    if galaxy:
        extra += f', {galaxy} galaxy-level'
    return f'{mine} of yours ({what}){extra}'


def carries_orders(served: bytes, submitted: bytes, civ: str) -> bool:
    """Does this submission change anything of this civ's?

    Deliberately conservative: anything that cannot be summarised counts as
    carrying orders, because the only thing this answer is used for is deciding
    whether a submission may be thrown away, and an unreadable blob is not one
    to discard.
    """
    try:
        import order_diff
        mine, _theirs, _galaxy = order_diff.compare(
            served, submitted, civ, log=lambda *a: None)
        return bool(mine)
    except Exception:                                       # noqa: BLE001
        return True


def save_path_ready(host='127.0.0.1', port=8888, timeout=2.0):
    """(ok, why) for the path a captured turn has to travel.

    A turn is lost anywhere along serve, play, save, capture, submit, and the
    save link is the one nothing was watching. `SaveGame` posts to the stub
    server, so if nothing is listening the capture fails, and it fails at the
    deadline, when the turn has already been played and cannot be replayed.
    That is exactly how a player lost a turn the first time two machines ran
    together: the run looked healthy for its whole length because serving never
    touches the save path.

    Checked before serving rather than at collect, because the only useful time
    to find out is before the turn is spent.
    """
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ''
    except OSError as exc:
        return False, (f'nothing is listening on {host}:{port} ({exc.strerror or exc}), '
                       f'so SaveGame would fail and the turn would be lost at '
                       f'the deadline. Start it with: python server/cs_server.py')


def serve(blob: bytes, civ: str, work_dir=None, hold=HOLD_SECONDS,
          exe=PLAYER_BUILD, check_save_path=True, wait_for_lock=0.0,
          log=print):
    """Stamp, launch, hold. Returns the path served.

    `wait_for_lock` is how long to wait for another tool to finish with the
    client. Zero refuses at once, which is what somebody at a command line
    wants; `follow` passes a real number, because a player's loop that gives up
    because the referee was mid-tick has failed their turn over a few seconds.
    """
    import game_cycle as gc
    if check_save_path:
        ok, why = save_path_ready()
        if not ok:
            raise SystemExit(f'refusing to serve {civ}: {why}')
    work_dir = work_dir or os.path.join(HERE, "referee_work")
    os.makedirs(work_dir, exist_ok=True)
    stamped = set_blob_player.set_player(blob, civ, log=log)
    dat = os.path.join(work_dir, f"serve_{civ}.dat")
    with open(dat, "wb") as f:
        f.write(stamped)
    # Lock first, close second. Serving a turn used to close whatever was
    # running before `launch` reached the lock, so two tools racing ended with
    # one client killed rather than one caller refused.
    snap = gc.restart(dat, purpose=f"serving {civ}", exe=exe,
                      wait_for_lock=wait_for_lock)
    local = snap.local_civ()
    if local is None or local.civ_name != civ:
        raise SystemExit(f"served {civ} but the client came up as "
                         f"{local.civ_name if local else None}")
    log(f"  serving {civ} at turn {snap.turn}")
    if hold:
        hold_clock(hold, log=log)
    return dat


def collect(name="player", save_dir=None, log=print) -> str:
    """Take the player's state back. Returns the capture path.

    `save_dir` is where the capture will land, which is wherever the stub server
    was told to keep its data. The launcher runs the server against its own data
    directory rather than the checkout's, so the caller has to say.
    """
    import game_cycle as gc
    if save_dir is None:
        return gc.capture_save(name[:15])

    before = time.time() - 1
    # game_cycle.trigger_save rather than a subprocess on trigger_save.py:
    # sys.executable is the launcher executable in a frozen build, so that
    # command line starts a second launcher and no save is taken.
    rc, out = gc.trigger_save(name)
    if rc != 0:
        log(out)
        raise SystemExit("SaveGame did not report success")
    fresh = [os.path.join(save_dir, f) for f in os.listdir(save_dir)
             if f.endswith(".b64")
             and os.path.getmtime(os.path.join(save_dir, f)) >= before]
    if not fresh:
        raise SystemExit(f"SaveGame succeeded but no capture appeared in "
                         f"{save_dir}; is the launcher's server running?")
    path = max(fresh, key=os.path.getmtime)
    log(f"  captured {os.path.basename(path)}")
    return path


def close():
    import game_cycle as gc
    gc.close_client()


def follow(store: TurnStore, civ: str, poll: float = 5.0, rounds: int = 0,
           on_state=None, exe=PLAYER_BUILD, save_dir=None, stop=None,
           submit_every: float = 20.0, log=print):
    """Play one civ's turns as the store publishes them.

    One pass is: serve the current turn, wait until its deadline, take the
    player's state back, submit it, then wait for the referee to publish the
    next one. `rounds` of 0 means keep going.

    `on_state(kind, **facts)` is called at each step so a UI can narrate without
    this function knowing what a UI is. The launcher passes one; the command line
    does not.

    `stop()` is checked wherever this would otherwise sleep, so a UI can end the
    loop between turns without killing the thread mid-capture and losing the
    player's orders.

    **A player's state is submitted throughout the turn, every `submit_every`
    seconds, not once at the deadline.** Submitting once put the whole turn on a
    single write landing in a narrow window, and the first time two machines
    played, one player's orders were lost to exactly that: their submission
    never reached the store, and the referee closed the turn reporting a missing
    player, which reads as somebody who did not turn up rather than a write that
    failed. Writing continuously means the worst case is losing the last few
    seconds of thought rather than the whole turn, and the store already treats
    a second submission as replacing the first, since the latest is the
    player's intent.

    A submission identical to the one already sent is skipped, so an idle turn
    costs one write rather than one every interval.
    """
    def emit(kind, **facts):
        if on_state:
            on_state(kind, **facts)

    def halted():
        return bool(stop and stop())

    def nap(seconds):
        """Sleep, but wake early if asked to stop."""
        end = time.time() + seconds
        while time.time() < end:
            if halted():
                return
            time.sleep(min(0.5, max(0.0, end - time.time())))

    played = 0
    while not halted():
        def current_or_wait():
            """(turn, deadline), waiting out a store that cannot be read.

            Every call into the store crosses a network share that another
            machine rewrites, so any of them can fail for a moment. Returning
            None lets the caller keep waiting instead of unwinding the loop.
            """
            while not halted():
                try:
                    return store.current()
                except OSError as exc:
                    log(f"[{civ}] store unreadable ({exc.__class__.__name__}), "
                        f"retrying in {poll}s")
                    nap(poll)
            return None

        got = current_or_wait()
        if got is None:
            break
        turn, deadline = got

        # Never re-serve a turn this civ has already submitted for.
        #
        # Serving is destructive: it closes whatever client is running and
        # relaunches on the store's pristine turn blob. If the player has
        # already played this turn, that throws away what they played AND the
        # orderless state that replaces it then overwrites their submission,
        # because `submit` is last-write-wins. Measured: a loop restarted with
        # 22 seconds left on turn 12 took the player's client away, submitted
        # 39,128 bytes reading NOTHING CHANGED over a 39,222-byte submission
        # carrying their colonise order, and only an unclosed turn and a copy
        # still on disk got it back.
        #
        # Waiting is always safe and redoing never is, so wait. The player has
        # submitted; that submission is their turn.
        blob = store.turn_blob(turn)

        # `blob` stays the authoritative turn throughout: it is the baseline
        # every diff and summary is measured against. `to_load` is only what
        # goes into the client, and on a restart those are not the same thing.
        to_load = blob
        prior = store.submissions(turn).get(civ)
        if prior is not None:
            if carries_orders(blob, prior, civ):
                # Resume, do not restart. The submission is a complete blob of
                # this turn carrying what the player played, so loading it puts
                # them back where they were. Loading `blob` instead would take
                # their orders away and then overwrite the submission holding
                # them, which is how turn 12 was nearly lost: a loop restarted
                # 22 seconds before the deadline served the pristine turn and
                # submitted 39,128 orderless bytes over a 39,222-byte colonise
                # order.
                to_load = prior
                log(f"[{civ}] turn {turn}: resuming from your submission "
                    f"({len(prior):,} bytes, it carries orders)")
                emit("resuming", turn=turn, civ=civ)
            else:
                # An orderless submission is the interim write of a turn nobody
                # has played yet. There is nothing in it to preserve, so a
                # plain serve is correct and refusing here would strand a
                # player whose client died with their turn still open.
                log(f"[{civ}] turn {turn}: a submission exists but carries no "
                    f"orders, serving the turn fresh")
        emit("serving", turn=turn, civ=civ)
        log(f"[{civ}] turn {turn}: serving")
        try:
            serve(to_load, civ, hold=HOLD_SECONDS, exe=exe,
                  wait_for_lock=240.0, log=log)
        except BaseException as exc:                        # noqa: BLE001
            # A serve that fails costs THIS turn. Letting it end the loop costs
            # every turn after it, and nobody is watching a player's loop at
            # 03:00. Measured: a client that came up but never became readable
            # killed this loop at turn 28, and the galaxy reached turn 47 with
            # the player absent from all nineteen.
            log(f"[{civ}] turn {turn}: COULD NOT SERVE, {exc}")
            emit("serve_failed", turn=turn, civ=civ, error=str(exc))
            try:
                close()
            except BaseException:                           # noqa: BLE001
                pass
            # Wait this turn out rather than spinning on a client that may be
            # wedged, then try again on the next one.
            while not halted():
                got = current_or_wait()
                if got is None or got[0] != turn:
                    break
                nap(poll)
            continue

        sent = None                     # the last blob this turn actually stored
        last_try = 0.0

        def push(final):
            """Capture and store, returning what landed. Quiet unless it moves."""
            nonlocal sent
            capture = collect(f"{civ[:8]}t{turn}", save_dir=save_dir, log=log)
            mine = sp.load_any(capture)
            if mine == sent:
                return True

            # Refuse to replace a submission we did not write with one that
            # carries nothing. `sent` is what THIS pass stored, so `existing`
            # differing from it means the submission came from somewhere else:
            # an earlier run, another process, a restart mid-turn. Overwriting
            # that with an empty capture is the one failure today that
            # destroyed rather than dropped, and it is silent because
            # last-write-wins has no opinion about what it is replacing.
            #
            # A player who genuinely cancels their order is still served: the
            # submission we wrote ourselves is `sent`, so replacing our own
            # with an empty one is allowed. Only a stranger's is protected.
            existing = store.submissions(turn).get(civ)
            if (existing is not None and existing != sent
                    and not carries_orders(blob, mine, civ)):
                # Both are orderless: nothing is at stake, so say so quietly.
                # Shouting about a no-op is how a warning that matters gets
                # read past, and this fired on the first live turn after the
                # guard went in, replacing one empty submission with another.
                if carries_orders(blob, existing, civ):
                    log(f"[{civ}] turn {turn}: REFUSING to submit, this "
                        f"capture carries no orders and would overwrite a "
                        f"{len(existing):,}-byte submission carrying orders "
                        f"that this loop did not write. Leaving it standing.")
                    emit("refused", turn=turn, civ=civ)
                else:
                    log(f"[{civ}] turn {turn}: nothing played yet, leaving the "
                        f"existing empty submission alone")
                return True

            store.submit(civ, turn, mine)
            landed = store.submissions(turn).get(civ)
            if landed != mine:
                raise RuntimeError(
                    f"submission for turn {turn} did not land in the store: "
                    f"wrote {len(mine):,} bytes, read back "
                    + (f"{len(landed):,}" if landed is not None else "nothing")
                    + ". Can this machine WRITE to the store?")
            sent = mine
            log(f"[{civ}] turn {turn}: submitted {len(mine):,} bytes"
                + (" (final)" if final else "")
                + f", {describe_orders(blob, mine, civ)}")
            emit("submitted", turn=turn, civ=civ, final=final)
            return True

        while not halted():
            # A store read can fail transiently , the store lives on an SMB
            # share and the referee rewrites it from another machine. `state()`
            # already retries for a couple of seconds, and when an outage
            # outlasts that the right answer is still not to exit: this loop
            # dying is how a player silently stops taking turns. Measured, a
            # `FileNotFoundError` on `state.json` here killed the loop at turn
            # 18 and the galaxy was at turn 20 before anyone noticed.
            try:
                left = store.seconds_left()
                cur, _d = store.current()
            except OSError as exc:
                log(f"[{civ}] turn {turn}: store unreadable ({exc.__class__.__name__}), "
                    f"retrying in {poll}s")
                nap(poll)
                continue
            if cur != turn:
                # The referee moved on without us, which happens if this player
                # joined late or the machine slept. Nothing to submit for a turn
                # that is already closed.
                log(f"[{civ}] turn {turn} closed while playing; skipping submit")
                emit("overtaken", turn=turn, current=cur)
                break
            if left <= 0:
                emit("collecting", turn=turn, civ=civ)
                log(f"[{civ}] turn {turn}: time is up, collecting")
                try:
                    push(final=True)
                except Exception as exc:                    # noqa: BLE001
                    # This turn is forfeit either way, but stopping here also
                    # abandons every turn after it, which is how one bad
                    # collect became a player who had simply stopped. Say it
                    # loudly, then carry on to the next turn.
                    log(f"[{civ}] turn {turn}: LOST, {exc}")
                    emit("lost", turn=turn, civ=civ, error=str(exc))
                break

            if time.time() - last_try >= submit_every:
                last_try = time.time()
                try:
                    push(final=False)
                except Exception as exc:                    # noqa: BLE001
                    # A failed interim write is worth saying and not worth
                    # stopping for: the next one is seconds away, and the one at
                    # the deadline still has to succeed or raise.
                    log(f"[{civ}] turn {turn}: interim submit failed, {exc}")

            emit("playing", turn=turn, civ=civ, seconds_left=left)
            nap(min(poll, left))

        close()
        played += 1
        if rounds and played >= rounds:
            emit("done", turns=played)
            return played
        if halted():
            break

        emit("waiting", turn=turn, civ=civ)
        log(f"[{civ}] waiting for the referee to publish past turn {turn}")
        while not halted():
            got = current_or_wait()
            if got is None or got[0] != turn:
                break
            nap(poll)

    emit("stopped", turns=played)
    return played


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve")
    s.add_argument("blob")
    s.add_argument("--civ", required=True)
    s.add_argument("--hold", type=int, default=HOLD_SECONDS,
                   help="turn length to write after load; 0 leaves it alone")
    s.add_argument("--exe", default=PLAYER_BUILD,
                   help="client build: testbed, resurgence, or a path")

    c = sub.add_parser("collect")
    c.add_argument("--name", default="player")
    c.add_argument("--save-dir", help="where the capture will land; defaults "
                                      "to the checkout's server/saves")

    f = sub.add_parser("follow")
    f.add_argument("--store", required=True)
    f.add_argument("--civ", required=True)
    f.add_argument("--rounds", type=int, default=0,
                   help="stop after this many turns; 0 keeps going")
    f.add_argument("--poll", type=float, default=5.0)
    f.add_argument("--exe", default=PLAYER_BUILD)
    f.add_argument("--submit-every", type=float, default=20.0,
                   help="seconds between interim submissions during a turn, so "
                        "a turn never rests on one write at the deadline")

    a = ap.parse_args()
    if a.cmd == "serve":
        print(serve(sp.load_any(a.blob), a.civ, hold=a.hold, exe=a.exe))
    elif a.cmd == "collect":
        print(collect(a.name, save_dir=a.save_dir))
    else:
        store = open_store(a.store)
        if not store.exists():
            raise SystemExit(f"no galaxy at {a.store}")
        follow(store, a.civ, poll=a.poll, rounds=a.rounds, exe=a.exe,
               submit_every=a.submit_every)


if __name__ == "__main__":
    sys.exit(main())
