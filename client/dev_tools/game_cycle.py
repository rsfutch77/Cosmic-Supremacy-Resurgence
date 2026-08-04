"""
game_cycle.py — save, close, edit the blob, relaunch, without a human
=====================================================================
    # add two designs to the running game and come back up on the result
    python game_cycle.py --design "f1:chassis=0,scanner=0,engine=0,weapon=0" \
                         --design "b1:chassis=0,scanner=0,engine=0,weapon=10"

    python game_cycle.py --save-only          # just capture a blob
    python game_cycle.py --relaunch <file>    # bring a .dat back up

Ship designs cannot be created in memory: the object gets built and fitted, but
nothing registers it with the civ and nothing computes its stat block, so it
never reaches the UI. The blob route works because the engine's own deserialiser
does the registration on load — confirmed live, an injected design appears in the
design list and can be selected for building.

The cost is a round trip through the disk: the client has to save, exit, and be
relaunched on the edited file. This automates that so an experiment does not need
a human at the keyboard between each step.

── Why each step is the way it is ─────────────────────────────────────────────
* SAVE goes through the engine's own SaveGame (trigger_save.py), so the blob is
  bytes the engine produced rather than a layout we assembled.
* CLOSE is a hard terminate. There is no clean-exit entry point we have found,
  and the state we care about is already on disk by then.
* ONE GAME PROCESS PER MACHINE. Launching a second client silently kills the
  first, so this always closes before launching and never assumes it can run two.
* RELAUNCH passes the .dat on the command line. `loadgame` is NOT the load path —
  the client never requests it at startup — so the edited blob has to arrive as a
  file argument.
* After launching, this WAITS until the game state is actually readable rather
  than sleeping a fixed time: a slow load otherwise looks exactly like a crash.

A pushed state can re-open the home-world customisation popup, and confirming one
is destructive. This never clicks anything; if a popup blocks the turn counter,
that is reported rather than worked around.
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(CLIENT_DIR)
EXE = os.path.join(CLIENT_DIR, "CosmicSupremacy_Resurgence.exe")
SAVES = os.path.join(REPO, "server", "saves")
INJECT = os.path.join(REPO, "server", "dev_tools", "inject_design.py")
PARSER = os.path.join(REPO, "server", "dev_tools", "save_parser.py")

sys.path.insert(0, os.path.join(HERE, "ai_player"))


def log(msg):
    print(msg, flush=True)


# ── process control ───────────────────────────────────────────────────────────
def client_pids():
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process -Name 'CosmicSupremacy*' -ErrorAction SilentlyContinue "
         "| Select-Object -ExpandProperty Id"],
        capture_output=True, text=True).stdout
    return [int(x) for x in out.split() if x.strip().isdigit()]


def close_client(timeout=20):
    pids = client_pids()
    if not pids:
        log("  client is not running")
        return True
    for pid in pids:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Stop-Process -Id {pid} -Force"], capture_output=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not client_pids():
            log(f"  closed client (pid {', '.join(map(str, pids))})")
            return True
        time.sleep(0.5)
    log("  [!] client did not exit")
    return False


def launch(dat, timeout=90):
    """Start the client on a .dat and wait until its state is readable.

    Waiting on the STATE rather than on a timer is the point: a load that takes
    40 seconds and a load that crashed look identical for the first 39.
    """
    if client_pids():
        raise SystemExit("a client is already running; close it first — "
                         "launching a second silently kills the first")
    dat = os.path.abspath(dat)
    if not os.path.exists(dat):
        raise SystemExit(f"no such file: {dat}")
    log(f"  launching on {dat}")
    subprocess.Popen([EXE, dat], cwd=CLIENT_DIR,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    import gamestate as gs
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not client_pids():
            time.sleep(0.5)
            continue
        try:
            snap = gs.Snapshot()
            civ = gs.resolve_civ(snap, None)
            if civ is not None and snap.owned_planets(civ) and len(snap.suns) > 50:
                log(f"  up: turn {snap.turn}, {civ.civ_name!r}, "
                    f"{len(snap.owned_planets(civ))} planet(s), "
                    f"{len(snap.designs)} design(s)")
                return snap
        except Exception:
            pass
        time.sleep(1.0)
    raise SystemExit(f"client did not become readable within {timeout}s")


# ── the pieces ────────────────────────────────────────────────────────────────
def capture_save(name="cycle"):
    before = set(os.listdir(SAVES)) if os.path.isdir(SAVES) else set()
    r = subprocess.run([sys.executable, os.path.join(HERE, "trigger_save.py"),
                        "--name", name[:15]],
                       capture_output=True, text=True, cwd=HERE)
    if "saved" not in r.stdout:
        log(r.stdout + r.stderr)
        raise SystemExit("SaveGame did not report success")
    new = [f for f in os.listdir(SAVES) if f.endswith(".b64") and f not in before]
    if not new:
        raise SystemExit("SaveGame succeeded but no new capture appeared in "
                         "server/saves — is cs_server.py running?")
    path = os.path.join(SAVES, sorted(new)[-1])
    log(f"  captured {os.path.basename(path)}")
    return path


def parse_design(spec):
    """'name:chassis=0,engine=0,weapon=10' -> (name, {list: [ids]})"""
    if ":" not in spec:
        raise SystemExit(f"--design needs 'name:key=id,...', got {spec!r}")
    name, rest = spec.split(":", 1)
    keys = {"chassis": "chassis", "scanner": "scanners", "scanners": "scanners",
            "engine": "engines", "engines": "engines", "weapon": "weapons",
            "weapons": "weapons", "module": "modules", "modules": "modules"}
    parts = {}
    for item in rest.split(","):
        if not item.strip():
            continue
        k, _, v = item.partition("=")
        k = keys.get(k.strip())
        if k is None:
            raise SystemExit(f"unknown part list in {item!r}")
        parts.setdefault(k, []).append(int(v))
    return name.strip(), parts


def inject_designs(capture, designs, out_b64, like_id=None):
    """Add each design in turn, threading the blob through inject_design."""
    sys.path.insert(0, os.path.join(REPO, "server", "dev_tools"))
    import save_parser as sp
    import inject_design as idg

    if like_id is None:
        raise SystemExit("inject_designs needs like_id: both civs start with an "
                         "identically named 'Colony Ship', so only the running "
                         "client can say which one is ours")
    blob = sp.decode_save(open(capture).read())
    for name, parts in designs:
        parts.setdefault("list6", [])
        blob = idg.make(blob, like_id, name, parts, None, log=log)
    with open(out_b64, "w") as f:
        f.write(sp.encode_save(blob) if isinstance(sp.encode_save(blob), str)
                else sp.encode_save(blob).decode("ascii"))
    return blob


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", action="append", default=[],
                    help="name:chassis=0,scanner=0,engine=0,weapon=10")
    ap.add_argument("--like-id", type=int, default=None,
                    help="object id of a design of the civ to extend; taken "
                         "from the running client when omitted")
    ap.add_argument("--name", default="cycle", help="save name (<=15 chars)")
    ap.add_argument("--dat", default=None, help="where to write the .dat")
    ap.add_argument("--save-only", action="store_true")
    ap.add_argument("--relaunch", default=None,
                    help="skip everything and just bring this .dat up")
    a = ap.parse_args()

    if a.relaunch:
        close_client()
        launch(a.relaunch)
        return

    # Resolve the local civ BEFORE closing: only the running client can say
    # which of the two identically named starting designs is ours.
    like_id = a.like_id
    if like_id is None and not a.save_only:
        import gamestate as gs
        snap = gs.Snapshot()
        civ = gs.resolve_civ(snap, None)
        if civ is None:
            raise SystemExit("cannot resolve the local civ; pass --like-id")
        mine = [d for d in snap.designs
                if d.owner is not None and d.owner.addr == civ.addr]
        if not mine:
            raise SystemExit(f"{civ.civ_name!r} has no designs to extend")
        like_id = mine[0].id
        log(f"  local civ {civ.civ_name!r}; extending alongside design "
            f"id {like_id} ({mine[0].design_name!r})")

    log("1. saving")
    capture = capture_save(a.name)
    if a.save_only:
        log(f"\ncapture: {capture}")
        return

    log("2. closing the client")
    close_client()

    log("3. editing the blob")
    designs = [parse_design(s) for s in a.design]
    out_b64 = os.path.join(REPO, "server", "loadgame_blob.b64")
    inject_designs(capture, designs, out_b64, like_id=like_id)

    dat = a.dat or os.path.join(CLIENT_DIR, "cycle.dat")
    sys.path.insert(0, os.path.join(REPO, "server", "dev_tools"))
    import save_parser as sp
    blob = sp.decode_save(open(out_b64).read())
    with open(dat, "wb") as f:
        f.write(blob)
    log(f"  wrote {dat} ({len(blob)} bytes)")

    log("4. relaunching")
    snap = launch(dat)
    names = sorted(d.design_name for d in snap.designs)
    log(f"\ndesigns after the cycle: {names}")


if __name__ == "__main__":
    main()
