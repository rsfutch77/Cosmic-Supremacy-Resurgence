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
         log=print) -> bytes:
    """Advance the galaxy by `turns` turns and return the resulting blob."""
    import game_cycle as gc

    work_dir = work_dir or os.path.join(HERE, "referee_work")
    os.makedirs(work_dir, exist_ok=True)
    dat = os.path.join(work_dir, f"tick_{int(time.time())}.dat")
    with open(dat, "wb") as f:
        f.write(blob)
    log(f"  referee: {len(blob):,} bytes -> {os.path.basename(dat)}")

    gc.close_client()
    snap = gc.launch(dat)
    start = snap.turn
    log(f"  referee: galaxy up at turn {start}, advancing {turns}")

    r = subprocess.run([sys.executable, os.path.join(DEV, "advance_turns.py"),
                        str(turns), "--secs", str(secs)],
                       capture_output=True, text=True, cwd=DEV)
    if r.returncode != 0:
        log(r.stdout + r.stderr)
        raise SystemExit("advance_turns failed")

    capture = gc.capture_save("referee")
    out = sp.decode_save(open(capture).read())
    gc.close_client()
    log(f"  referee: turn {start} -> captured {len(out):,} bytes")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("authoritative")
    ap.add_argument("--from", dest="subs", action="append", default=[],
                    metavar="CIV=FILE", help="a player submission; repeatable")
    ap.add_argument("--turns", type=int, default=1)
    ap.add_argument("--secs", type=int, default=10,
                    help="turn length used while driving; the engine clamps "
                         "below 60 without patch_turn_floor.py")
    ap.add_argument("-o", "--out", help="write the next state as a .b64 capture")
    ap.add_argument("--dat", help="also write it decompressed, for a client")
    ap.add_argument("--no-tick", action="store_true",
                    help="apply orders only, do not run the engine")
    a = ap.parse_args()

    blob = sp.load_any(a.authoritative)
    submissions = []
    for spec in a.subs:
        if "=" not in spec:
            raise SystemExit(f"--from wants CIV=FILE, got {spec!r}")
        civ, path = spec.split("=", 1)
        submissions.append((civ, sp.load_any(path)))

    blob = apply_orders(blob, submissions)
    if not a.no_tick:
        blob = tick(blob, turns=a.turns, secs=a.secs)

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
