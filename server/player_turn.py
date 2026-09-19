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


def serve(blob: bytes, civ: str, work_dir=None, hold=HOLD_SECONDS,
          exe=PLAYER_BUILD, log=print):
    """Stamp, launch, hold. Returns the path served."""
    import game_cycle as gc
    work_dir = work_dir or os.path.join(HERE, "referee_work")
    os.makedirs(work_dir, exist_ok=True)
    stamped = set_blob_player.set_player(blob, civ, log=log)
    dat = os.path.join(work_dir, f"serve_{civ}.dat")
    with open(dat, "wb") as f:
        f.write(stamped)
    gc.close_client()
    snap = gc.launch(dat, exe=exe)
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
    r = subprocess.run([sys.executable, os.path.join(DEV, "trigger_save.py"),
                        "--name", name[:15]],
                       capture_output=True, text=True, cwd=DEV)
    if "saved" not in r.stdout:
        log(r.stdout + r.stderr)
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
        turn, deadline = store.current()
        blob = store.turn_blob(turn)
        emit("serving", turn=turn, civ=civ)
        log(f"[{civ}] turn {turn}: serving")
        serve(blob, civ, hold=HOLD_SECONDS, exe=exe, log=log)

        sent = None                     # the last blob this turn actually stored
        last_try = 0.0

        def push(final):
            """Capture and store, returning what landed. Quiet unless it moves."""
            nonlocal sent
            capture = collect(f"{civ[:8]}t{turn}", save_dir=save_dir, log=log)
            mine = sp.load_any(capture)
            if mine == sent:
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
                + (" (final)" if final else ""))
            emit("submitted", turn=turn, civ=civ, final=final)
            return True

        while not halted():
            left = store.seconds_left()
            cur, _d = store.current()
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
                push(final=True)
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
        while store.current()[0] == turn and not halted():
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
