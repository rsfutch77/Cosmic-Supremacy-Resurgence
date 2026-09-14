"""
checkpoint.py , named restore points, so a test does not cost a whole game
==========================================================================
    python checkpoint.py save t41        # capture the running game
    python checkpoint.py list
    python checkpoint.py restore t41     # close the client, bring it up on t41

A checkpoint is just the engine's own save blob written where it can be found by
name: `client/checkpoints/<name>.dat`, in the raw form the client accepts on its
command line. Nothing here invents a format , `game_cycle.py` already does the
capture and the relaunch, and this is the naming and the index around them.
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(CLIENT_DIR)
CHECKPOINTS = os.path.join(CLIENT_DIR, "checkpoints")
STATE = os.path.join(HERE, "ai_player", "state")

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "server", "dev_tools"))


def path_for(name):
    return os.path.join(CHECKPOINTS, f"{name}.dat")


def turn_of(capture):
    """The turn the capture was taken on , the server names it `..._t<turn>.b64`."""
    stem = os.path.basename(capture).rsplit(".", 1)[0]
    tail = stem.rsplit("_t", 1)[-1]
    return int(tail) if tail.isdigit() else None


def do_save(name=None):
    import game_cycle as gc
    import save_parser as sp

    os.makedirs(CHECKPOINTS, exist_ok=True)
    # Capture FIRST when the name is to be derived: the turn counter can move
    # between deciding to save and the save landing, and a checkpoint called t41
    # that actually holds turn 42 is a trap for whoever restores it later.
    capture = gc.capture_save((name or "chk")[:15])
    turn = turn_of(capture)
    if name is None:
        name = f"t{turn}"
        print(f"  naming it {name!r} after the captured turn")
    elif turn is not None and name != f"t{turn}":
        print(f"  [!] captured turn {turn}, saving under the name {name!r}")

    dest = path_for(name)
    if os.path.exists(dest):
        raise SystemExit(f"{dest} exists; pick another name or delete it , "
                         f"overwriting a restore point is not something to do "
                         f"by accident")
    blob = sp.decode_save(open(capture).read())
    with open(dest, "wb") as f:
        f.write(blob)
    print(f"\ncheckpoint {name!r}: {dest} ({len(blob):,} bytes)")
    print(f"  from {os.path.basename(capture)}")
    return dest


def do_list():
    if not os.path.isdir(CHECKPOINTS):
        print("no checkpoints yet")
        return
    rows = sorted(f for f in os.listdir(CHECKPOINTS) if f.endswith(".dat"))
    if not rows:
        print("no checkpoints yet")
        return
    print(f"{'name':>16}  {'bytes':>10}  path")
    for f in rows:
        p = os.path.join(CHECKPOINTS, f)
        print(f"{f[:-4]:>16}  {os.path.getsize(p):>10,}  {p}")


def do_restore(name, fresh_map=False):
    import game_cycle as gc

    dat = path_for(name)
    if not os.path.exists(dat):
        raise SystemExit(f"no checkpoint named {name!r}; try `checkpoint.py list`")
    if fresh_map and os.path.isdir(STATE):
        for f in os.listdir(STATE):
            if f.startswith("discovered_"):
                os.remove(os.path.join(STATE, f))
                print(f"  cleared {f}")
    gc.close_client()
    gc.launch(dat)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("save", "list", "restore"))
    ap.add_argument("name", nargs="?")
    ap.add_argument("--fresh-map", action="store_true",
                    help="on restore, delete the AI's persisted fog-of-war so "
                         "the timeline re-runs instead of resuming")
    a = ap.parse_args()

    if a.action == "list":
        do_list()
        return
    if a.action == "save":
        do_save(a.name)            # name optional: defaults to t<capturedTurn>
    elif not a.name:
        raise SystemExit("restore needs a name; try `checkpoint.py list`")
    else:
        do_restore(a.name, fresh_map=a.fresh_map)


if __name__ == "__main__":
    main()
