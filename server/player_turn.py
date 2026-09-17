"""
player_turn.py , serve one player their turn, and collect it back
=================================================================
    python player_turn.py serve   turn3.b64 --civ DemoPlayer
    python player_turn.py collect --name playerA

`serve` stamps the state with the civ this player controls, starts a client on
it, and stops the clock. `collect` takes the state back.

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

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEV = os.path.join(REPO, "client", "dev_tools")
sys.path.insert(0, DEV)
sys.path.insert(0, os.path.join(DEV, "ai_player"))
sys.path.insert(0, os.path.join(HERE, "dev_tools"))

import save_parser as sp
import set_blob_player

HOLD_SECONDS = 86400          # a day; long enough that no boundary arrives


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


def serve(blob: bytes, civ: str, work_dir=None, hold=HOLD_SECONDS, log=print):
    """Stamp, launch, hold. Returns the path served."""
    import game_cycle as gc
    work_dir = work_dir or os.path.join(HERE, "referee_work")
    os.makedirs(work_dir, exist_ok=True)
    stamped = set_blob_player.set_player(blob, civ, log=log)
    dat = os.path.join(work_dir, f"serve_{civ}.dat")
    with open(dat, "wb") as f:
        f.write(stamped)
    gc.close_client()
    snap = gc.launch(dat)
    local = snap.local_civ()
    if local is None or local.civ_name != civ:
        raise SystemExit(f"served {civ} but the client came up as "
                         f"{local.civ_name if local else None}")
    log(f"  serving {civ} at turn {snap.turn}")
    if hold:
        hold_clock(hold, log=log)
    return dat


def collect(name="player", log=print) -> str:
    """Take the player's state back. Returns the capture path."""
    import game_cycle as gc
    return gc.capture_save(name[:15])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve")
    s.add_argument("blob")
    s.add_argument("--civ", required=True)
    s.add_argument("--hold", type=int, default=HOLD_SECONDS,
                   help="turn length to write after load; 0 leaves it alone")

    c = sub.add_parser("collect")
    c.add_argument("--name", default="player")

    a = ap.parse_args()
    if a.cmd == "serve":
        print(serve(sp.load_any(a.blob), a.civ, hold=a.hold))
    else:
        print(collect(a.name))


if __name__ == "__main__":
    sys.exit(main())
