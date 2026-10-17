"""
game_cycle.py: save, close, edit the blob, relaunch, without a human

    # add two designs to the running game and come back up on the result
    python game_cycle.py --design "f1:chassis=0,scanner=0,engine=0,weapon=0" \
                         --design "b1:chassis=0,scanner=0,engine=0,weapon=10"

    python game_cycle.py --save-only          # just capture a blob
    python game_cycle.py --relaunch <file>    # bring a .dat back up

Ship designs cannot be created in memory: nothing registers the object with the
civ or computes its stat block. The engine's deserialiser does both on load, so
the client saves, exits, and is relaunched on the edited blob.
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

# Keeps a console child of a windowed parent from opening its own window.
# Redirected output still arrives.
CREATE_NO_WINDOW = 0x08000000


def no_window() -> int:
    """`creationflags` that hide a console child, and zero off Windows."""
    return CREATE_NO_WINDOW if os.name == "nt" else 0


def log(msg):
    print(msg, flush=True)


def client_pids():
    # The wildcard also matches CosmicSupremacyLauncher.exe, which hosts the stub
    # server and must never be returned: close_client() kills every pid here.
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process -Name 'CosmicSupremacy*' -ErrorAction SilentlyContinue "
         "| Where-Object { $_.ProcessName -ne 'CosmicSupremacyLauncher' } "
         "| Select-Object -ExpandProperty Id"],
        capture_output=True, text=True, creationflags=no_window()).stdout
    return [int(x) for x in out.split() if x.strip().isdigit()]


def close_client(timeout=20, force=False):
    """Stop the machine's client. Refuses to kill one somebody else holds.

    `force` skips the check, for a caller that knows the holder is gone in a way
    `_pid_alive` cannot see.
    """
    held = lock_holder()
    if (held and not force and held[0] != os.getpid()
            and _pid_alive(held[0]) and client_pids()):
        raise SystemExit(
            f"the client is held by pid {held[0]} ({held[1]}). Closing it now "
            f"would destroy whatever that is doing, which if it is a player's "
            f"turn leaves no trace. Wait for it, or stop that process.")
    release_client_lock()
    pids = client_pids()
    if not pids:
        log("  client is not running")
        return True
    for pid in pids:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Stop-Process -Id {pid} -Force"],
                       capture_output=True, creationflags=no_window())
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not client_pids():
            log(f"  closed client (pid {', '.join(map(str, pids))})")
            return True
        time.sleep(0.5)
    log("  [!] client did not exit")
    return False


import tempfile

# Advisory lock on the machine's one game process: a file holding
# "<pid> <purpose>", taken by launch() and checked by close_client(). A lock
# whose pid is no longer alive is stale; a legitimate hold can last a whole turn.
CLIENT_LOCK = os.path.join(tempfile.gettempdir(), "cosmic_client.lock")

TESTBED_EXE = os.path.join(CLIENT_DIR, "CosmicSupremacy_TestBed.exe")
PLAYER_EXE = os.path.join(CLIENT_DIR, "CosmicSupremacy_Player.exe")

BUILDS = {"resurgence": EXE, "testbed": TESTBED_EXE, "player": PLAYER_EXE}


def _pid_alive(pid):
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=no_window())
        return str(pid) in (out.stdout or "")
    except Exception:
        return True          # cannot tell, so assume the holder is alive


def lock_holder():
    """(pid, purpose) currently holding the client, or None."""
    try:
        raw = open(CLIENT_LOCK, encoding="utf-8").read().strip()
        pid, _, purpose = raw.partition(" ")
        return int(pid), purpose
    except (OSError, ValueError):
        return None


def take_client_lock(purpose, wait=0.0, poll=2.0):
    """Claim the machine's one game process. Returns True when held.

    `wait` is how long to wait for a current holder to finish; zero refuses at
    once.
    """
    deadline = time.time() + wait
    while True:
        held = lock_holder()
        if held is None or not _pid_alive(held[0]):
            if held is not None:
                log(f"  clearing a stale client lock from pid {held[0]} "
                    f"({held[1]})")
            try:
                fd = os.open(CLIENT_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    os.remove(CLIENT_LOCK)
                except OSError:
                    pass
                continue
            with os.fdopen(fd, "w") as f:
                f.write(f"{os.getpid()} {purpose}")
            return True
        if held[0] == os.getpid():
            return True                      # already ours, reentrant
        if time.time() >= deadline:
            raise SystemExit(
                f"the game client is held by pid {held[0]} ({held[1]}). "
                f"Starting one now would close theirs, which if it is a "
                f"player's turn destroys it. Wait, or stop that process.")
        time.sleep(poll)


def release_client_lock():
    held = lock_holder()
    if held and held[0] == os.getpid():
        try:
            os.remove(CLIENT_LOCK)
        except OSError:
            pass


def resolve_exe(which=None):
    """Pick a client build.

    Resurgence and TestBed differ by 22 bytes at six sites, the turn pipeline
    bypasses that let a client advance a turn with no server. Two of them also
    NOP the guards on the homeworld setup prompts.

        resurgence   ticks without a server, offers the setup prompts always
        testbed      waits for a server tick, gates the prompts as shipped
        player       testbed, plus one byte that silences the coat-of-arms
                     prompt; built by patch_hide_setup_prompts.py --build
                     and gitignored

    A player's client must never compute a turn, so it uses player or testbed.
    The referee uses resurgence.
    """
    if which is None:
        which = "resurgence"
    if which == "player" and not os.path.exists(PLAYER_EXE):
        log("  no CosmicSupremacy_Player.exe; falling back to testbed. Run "
            "patch_hide_setup_prompts.py --build to silence the last prompt.")
        return TESTBED_EXE
    if which in BUILDS:
        return BUILDS[which]
    if os.path.exists(which):
        return os.path.abspath(which)
    raise SystemExit(f"unknown client build {which!r}; want one of "
                     f"{sorted(BUILDS)}, or a path to an exe")


def restart(dat, purpose, wait_for_lock=0.0, timeout=180, exe=None,
            min_suns=11):
    """Take the client, close whatever is on it, and launch on `dat`.

    The lock is taken before closing, so a caller that cannot have the client is
    refused before it kills anybody's session.
    """
    take_client_lock(purpose, wait=wait_for_lock)
    try:
        close_client()
        return launch(dat, timeout=timeout, exe=exe, purpose=purpose,
                      wait_for_lock=wait_for_lock, min_suns=min_suns)
    except BaseException:
        release_client_lock()
        raise


def launch(dat, timeout=180, exe=None, purpose=None, wait_for_lock=0.0,
           min_suns=11):
    """Start the client on a .dat and wait until its state is readable.

    Takes the machine's client lock first. `purpose` is what another tool is
    told is holding it, so make it something a person can act on.
    `wait_for_lock` of zero refuses immediately when somebody else holds it.
    The timeout is generous: giving up on a slow load fails a turn.
    """
    if client_pids():
        raise SystemExit("a client is already running; close it first; "
                         "launching a second silently kills the first")
    dat = os.path.abspath(dat)
    if not os.path.exists(dat):
        raise SystemExit(f"no such file: {dat}")
    take_client_lock(purpose or f"launch {os.path.basename(dat)}",
                     wait=wait_for_lock)
    exe_path = resolve_exe(exe)
    if not os.path.exists(exe_path):
        release_client_lock()
        raise SystemExit(f"no such client: {exe_path}")
    log(f"  launching {os.path.basename(exe_path)} on {dat}")
    subprocess.Popen([exe_path, dat], cwd=CLIENT_DIR,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    import gamestate as gs
    deadline = time.time() + timeout
    seen_alive = False
    while time.time() < deadline:
        if not client_pids():
            # A client that started and exited has refused the galaxy; it writes
            # a minidump with no exception stream.
            if seen_alive:
                release_client_lock()
                raise SystemExit(
                    "the client started and exited without opening the "
                    "galaxy. That is the engine refusing the blob, not a slow "
                    "load; read the newest .dmp beside the EXE with "
                    "dump_threads.py")
            time.sleep(0.5)
            continue
        seen_alive = True
        try:
            snap = gs.Snapshot()
            # quiet: the read fails a few times while the player slot populates.
            civ = gs.resolve_civ(snap, None, quiet=True)
            # `min_suns` guards against reading a half-loaded galaxy, whose suns
            # arrive progressively. A caller serving a small galaxy lowers it.
            if (civ is not None and snap.owned_planets(civ)
                    and len(snap.suns) >= min_suns):
                log(f"  up: turn {snap.turn}, {civ.civ_name!r}, "
                    f"{len(snap.owned_planets(civ))} planet(s), "
                    f"{len(snap.designs)} design(s)")
                return snap
        except Exception:
            pass
        time.sleep(1.0)
    raise SystemExit(f"client did not become readable within {timeout}s")


def import_without_bytecode(name):
    """Import a module without writing its compiled bytecode to __pycache__.

    Windows Defender quarantines trigger_save's .pyc as Exploit:Python/Leivion.C
    and leaves the .py alone. An existing .pyc is still read.
    """
    import importlib
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        return importlib.import_module(name)
    finally:
        sys.dont_write_bytecode = previous


def save_via_message() -> bool:
    """Whether the save path posts a window message instead of injecting.

    Set by CS_SAVE_VIA_MESSAGE. The message path saves only on a client carrying
    a resident handler that calls SaveGame on the posted message.
    """
    return os.environ.get("CS_SAVE_VIA_MESSAGE", "").strip().lower() in (
        "1", "true", "yes", "on")


def trigger_save(name, gameid=0, timeout=30):
    """Make the running client save. Returns (exit code, the tool's output).

    The default calls SaveGame in a remote thread inside the client. With
    CS_SAVE_VIA_MESSAGE set it posts a window message instead (see
    trigger_save_message). trigger_save runs in process, because in the frozen
    launcher sys.executable is the launcher itself; its arguments go in through
    sys.argv.
    """
    if save_via_message():
        import trigger_save_message as tsm
        ok, detail = tsm.post_save(gameid)
        return (0 if ok else 1), detail + "\n"

    import contextlib
    import io
    ts = import_without_bytecode("trigger_save")

    out = io.StringIO()
    argv = sys.argv
    sys.argv = ["trigger_save.py", "--name", name[:15],
                "--gameid", str(gameid), "--timeout", str(timeout)]
    try:
        with contextlib.redirect_stdout(out):
            rc = ts.main()
    except SystemExit as exc:
        # trigger_save reports a missing client or a refused handle by exiting
        # with a message. It is kept as output so the caller logs the reason.
        rc = exc.code if isinstance(exc.code, int) else 1
        if exc.code and not isinstance(exc.code, int):
            print(exc.code, file=out)
    finally:
        sys.argv = argv
    return rc, out.getvalue()


def capture_save(name="cycle"):
    before = set(os.listdir(SAVES)) if os.path.isdir(SAVES) else set()
    rc, out = trigger_save(name)
    if rc != 0:
        log(out)
        raise SystemExit("SaveGame did not report success")
    new = [f for f in os.listdir(SAVES) if f.endswith(".b64") and f not in before]
    if not new:
        raise SystemExit("SaveGame succeeded but no new capture appeared in "
                         "server/saves, is cs_server.py running?")
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


def civ_template_ids(blob):
    """One template design id per civ: the first DSGN inside each OWNR.

    Needs no running client: the blob nests each civ's designs in its OWNR.
    """
    sys.path.insert(0, os.path.join(REPO, "server", "dev_tools"))
    import inject_civ as icv
    import inject_design as idg
    out = []
    for o in icv.owner_records(blob):
        mine = [r for r in idg.design_records(blob) if o["off"] < r[0] < o["end"]]
        if mine:
            first = min(mine)
            out.append((o["name"], first[1]))
    return out


def inject_designs(capture, designs, out_b64, like_id=None, all_civs=False):
    """Add each design in turn, threading the blob through inject_design."""
    sys.path.insert(0, os.path.join(REPO, "server", "dev_tools"))
    import save_parser as sp
    import inject_design as idg

    blob = sp.decode_save(open(capture).read())
    if all_civs:
        targets = civ_template_ids(blob)
        if not targets:
            raise SystemExit("no civ in the blob owns a design to extend")
        log(f"  extending every civ: "
            + ", ".join(f"{n!r} (like id {i})" for n, i in targets))
    elif like_id is None:
        raise SystemExit("inject_designs needs like_id or --all-civs: both civs "
                         "start with an identically named 'Colony Ship', so only "
                         "the running client can say which one is ours")
    else:
        targets = [(None, like_id)]

    for civ_name, tid in targets:
        # inject_design keys on object ids, which injection does not shift.
        for name, parts in designs:
            parts = dict(parts)
            parts.setdefault("list6", [])
            blob = idg.make(blob, tid, name, parts, None, log=log)
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
    ap.add_argument("--all-civs", action="store_true",
                    help="give every design to EVERY civ in the galaxy, not "
                         "just the local player, an AI-vs-AI galaxy needs both "
                         "sides able to build the same things")
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

    # Resolved before closing: only the running client can say which of the two
    # identically named starting designs is ours.
    like_id = a.like_id
    if like_id is None and not a.save_only and not a.all_civs:
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
    inject_designs(capture, designs, out_b64, like_id=like_id,
                   all_civs=a.all_civs)

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
