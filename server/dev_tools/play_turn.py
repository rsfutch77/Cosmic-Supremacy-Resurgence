"""
play_turn.py , take one civ's turn, driven by the launcher's own config
=======================================================================
    python server/dev_tools/play_turn.py            # one round
    python server/dev_tools/play_turn.py --rounds 0 # keep following

This is the launcher's multiplayer worker with no Tk around it: same
`multiplayer.json`, same `open_store`, same `player_turn.follow`. It exists
because the store string is a UNC path, and a UNC path cannot survive being
typed on a command line , every shell between here and `argparse` takes a bite
out of the leading backslashes, and `\\\\host\\share` arrives as `\\host\\share`,
which fails as "no galaxy there" and looks exactly like a share that is down.

Reading the path from the JSON the launcher already reads removes the hand that
does the mangling. Nothing here takes the store as an argument, deliberately.
"""
import argparse
import json
import os
import socket
import sys

CS_PORT = int(os.environ.get("CSPORT", 8888))

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, "server"),
          os.path.join(ROOT, "server", "dev_tools"),
          os.path.join(ROOT, "client", "dev_tools"),
          os.path.join(ROOT, "client", "dev_tools", "ai_player")):
    if d not in sys.path:
        sys.path.insert(0, d)

import player_turn
from turn_store import open_store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.path.join(ROOT, "release", "data"),
                    help="where multiplayer.json lives")
    ap.add_argument("--rounds", type=int, default=1,
                    help="0 keeps following")
    ap.add_argument("--poll", type=float, default=5.0)
    a = ap.parse_args()

    cfg_path = os.path.join(a.data_dir, "multiplayer.json")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    store = open_store(cfg["store"])
    if not store.exists():
        raise SystemExit(f"no galaxy at {cfg['store']}")

    # The client saves by POSTing to the stub server, so a missing cs_server
    # does not fail until `collect` at the deadline , by which time the turn
    # has been played and is lost. Checked here, before anything is served.
    with socket.socket() as s:
        s.settimeout(2.0)
        if s.connect_ex(("127.0.0.1", CS_PORT)) != 0:
            raise SystemExit(
                f"nothing is listening on 127.0.0.1:{CS_PORT}, so SaveGame "
                "will fail and the turn would be forfeited at the deadline.\n"
                "Start it first:  python server/cs_server.py")

    turn, _due = store.current()
    civs = store.civs()
    if cfg["civ"] not in civs:
        raise SystemExit(f"{cfg['civ']} is not in this galaxy: {civs}")
    print(f"store {cfg['store']}")
    print(f"turn {turn}, {round(store.seconds_left())}s left, civs {civs}")

    # The capture lands wherever the stub server keeps its data. Nothing else
    # is running one here, so `collect` starts the checkout's own and its
    # default applies; passing the launcher's release/data/saves would point at
    # a directory no server is writing to.
    player_turn.follow(store, cfg["civ"], poll=a.poll, rounds=a.rounds)


if __name__ == "__main__":
    main()
