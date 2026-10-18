"""
bot_seats.py: the Python AI plays named civs in a galaxy, one turn at a time
    python server/dev_tools/bot_seats.py firebase://cs-resurgence/sandbox --civ Alpha --civ Beta
    python server/dev_tools/bot_seats.py <store> --civ Alpha --civ Beta --follow
    python server/dev_tools/bot_seats.py <store> --civ Alpha --dry-run

For each civ: serve the current turn into the client as that civ, run one pass
of `ai.py`'s rules, capture the client's state and submit it. The client is
closed after the last civ. `--follow` repeats at every published turn.

Submissions go straight to the store with this machine's credentials, not
through the relay, so a bot civ needs no seat. A civ that already has a
submission this turn is skipped unless `--replace` is given.

Needs `cs_server.py` from this checkout on port 8888, where captures land, and
the game client, which `player_turn.serve` takes under the client lock.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, "server"),
          os.path.join(ROOT, "server", "dev_tools"),
          os.path.join(ROOT, "client", "dev_tools"),
          os.path.join(ROOT, "client", "dev_tools", "ai_player")):
    if d not in sys.path:
        sys.path.insert(0, d)

import ai
import player_turn
import save_parser as sp
from turn_store import open_store

POLL = 60.0             # seconds between store reads while waiting for a turn
CLIENT_WAIT = 240.0     # seconds to wait for another tool to free the client


def play(store, civ, turn, blob, dry_run=False, log=print):
    """Serve `civ` its turn, run one AI pass, and submit the result.

    Returns the bytes submitted, or None on a dry run.
    """
    player_turn.serve(blob, civ, wait_for_lock=CLIENT_WAIT, log=log)
    loop = ai.Loop(civ_name=civ, dry_run=dry_run, vision="known", log=log)
    loop.one_pass()
    if dry_run:
        log(f"[{civ}] turn {turn}: dry run, nothing submitted")
        return None
    capture = player_turn.collect(f"{civ[:8]}t{turn}", log=log)
    mine = sp.load_any(capture)
    store.submit(civ, turn, mine)
    if store.submission(civ, turn) != mine:
        raise RuntimeError(f"[{civ}] turn {turn}: the submission did not "
                           f"land in the store")
    log(f"[{civ}] turn {turn}: submitted {len(mine):,} bytes, "
        f"{player_turn.describe_orders(blob, mine, civ)}")
    return mine


def round_once(store, civs, dry_run=False, replace=False, log=print):
    """Play every civ in `civs` on the current turn. Returns the turn played."""
    turn, _deadline = store.current()
    roster = store.civs()
    blob = None
    try:
        for civ in civs:
            if civ not in roster:
                log(f"[{civ}] not in this galaxy: {roster}")
                continue
            if not replace and store.submission(civ, turn) is not None:
                log(f"[{civ}] turn {turn}: already submitted, skipping")
                continue
            if blob is None:
                blob = store.turn_blob(turn)
            try:
                play(store, civ, turn, blob, dry_run=dry_run, log=log)
            except Exception as exc:                        # noqa: BLE001
                # One civ's failure leaves the others to play.
                log(f"[{civ}] turn {turn}: FAILED, {type(exc).__name__}: {exc}")
    finally:
        if blob is not None:
            player_turn.close()
    return turn


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1].strip())
    ap.add_argument("store", help="the galaxy, as open_store takes it")
    ap.add_argument("--civ", action="append", required=True,
                    help="a civ the AI plays; repeat for more")
    ap.add_argument("--follow", action="store_true",
                    help="play every turn the referee publishes")
    ap.add_argument("--dry-run", action="store_true",
                    help="decide without acting or submitting")
    ap.add_argument("--replace", action="store_true",
                    help="play a civ that already submitted this turn")
    a = ap.parse_args(argv)

    ok, why = player_turn.save_path_ready()
    if not ok:
        raise SystemExit(why)
    store = open_store(a.store)
    if not store.exists():
        raise SystemExit(f"no galaxy at {a.store}")

    while True:
        turn = round_once(store, a.civ, dry_run=a.dry_run, replace=a.replace)
        if not a.follow:
            return 0
        print(f"waiting for the referee to publish turn {turn + 1}")
        while store.current()[0] == turn:
            time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
