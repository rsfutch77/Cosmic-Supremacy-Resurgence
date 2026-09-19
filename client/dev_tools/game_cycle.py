"""
game_cycle.py , save, close, edit the blob, relaunch, without a human
=====================================================================
    # add two designs to the running game and come back up on the result
    python game_cycle.py --design "f1:chassis=0,scanner=0,engine=0,weapon=0" \
                         --design "b1:chassis=0,scanner=0,engine=0,weapon=10"

    python game_cycle.py --save-only          # just capture a blob
    python game_cycle.py --relaunch <file>    # bring a .dat back up

Ship designs cannot be created in memory: the object gets built and fitted, but
nothing registers it with the civ and nothing computes its stat block, so it
never reaches the UI. The blob route works because the engine's own deserialiser
does the registration on load , confirmed live, an injected design appears in the
design list and can be selected for building, the client has to save, exit, and be
relaunched on the edited file. This automates that so an experiment does not need
a human at the keyboard between each step.

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
    # The 'CosmicSupremacy*' wildcard also matches CosmicSupremacyLauncher.exe,
    # the player-facing launcher added in release/. close_client() force-kills
    # everything this returns, so without the exclusion a harness run with the
    # launcher open would take the launcher , and the stub server living inside
    # it , down with the client, mid-game.
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process -Name 'CosmicSupremacy*' -ErrorAction SilentlyContinue "
         "| Where-Object { $_.ProcessName -ne 'CosmicSupremacyLauncher' } "
         "| Select-Object -ExpandProperty Id"],
        capture_output=True, text=True).stdout
    return [int(x) for x in out.split() if x.strip().isdigit()]


def close_client(timeout=20, force=False):
    """Stop the machine's client. Refuses to kill one somebody else holds.

    The lock did not protect anything on its own, because every caller closed
    the running client and only then launched, and `launch` is where the lock
    is taken. The loser of a race was force-killed rather than refused, which
    is the exact outcome the lock exists to prevent, and in the case that
    matters that is a player mid-turn. Checking here covers every caller,
    including the dev tools that will never be revisited.

    `force` is for the caller that knows the holder is gone in a way `_pid_alive`
    cannot see, and should be rare enough to be conspicuous.
    """
    held = lock_holder()
    if (held and not force and held[0] != os.getpid()
            and _pid_alive(held[0]) and client_pids()):
        raise SystemExit(
            f"the client is held by pid {held[0]} ({held[1]}). Closing it now "
            f"would destroy whatever that is doing, which if it is a player's "
            f"turn leaves no trace. Wait for it, or stop that process.")
    # Releasing here rather than only on the happy path: close_client is what
    # every caller runs when it is finished with the client, including the
    # finally blocks, so this is where the lock actually stops being needed.
    release_client_lock()
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


import tempfile

# One machine has one game process, and more than one tool wants it. A referee
# waking on its deadline calls close_client and takes it; so does an experiment;
# so does a player's loop serving a turn. Whoever moves second destroys what the
# first was doing, and in the case that matters that is a player's turn, mid
# play, with no trace afterwards beyond a client that vanished.
#
# The lock is a file naming the holder and its pid. It is advisory, since
# nothing stops a tool calling Popen itself, but every path in this project goes
# through launch() and close_client(). A holder that has died leaves a stale
# lock, which is detected by asking whether the pid is still alive rather than
# by a timeout, because a legitimate hold can last a whole turn.
CLIENT_LOCK = os.path.join(tempfile.gettempdir(), "cosmic_client.lock")

TESTBED_EXE = os.path.join(CLIENT_DIR, "CosmicSupremacy_TestBed.exe")
PLAYER_EXE = os.path.join(CLIENT_DIR, "CosmicSupremacy_Player.exe")

BUILDS = {"resurgence": EXE, "testbed": TESTBED_EXE, "player": PLAYER_EXE}


def _pid_alive(pid):
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                             capture_output=True, text=True, timeout=15)
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

    `wait` is how long to wait for a current holder to finish. Zero refuses at
    once, which is what an interactive tool wants; a referee that can afford to
    wait passes a real number.
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

    Resurgence and TestBed are the same binary apart from 22 bytes at six sites,
    the T1-T5 turn pipeline bypasses. Those are what let a client advance a turn
    with no server, and two of the six also land on the guards for the homeworld
    setup prompt, which is why that prompt appeared on every served turn. So the
    choice is not cosmetic:

        resurgence   ticks without a server, offers the setup prompts always
        testbed      waits for a server tick, gates the prompts as shipped
        player       testbed, plus one byte that silences the coat-of-arms
                     prompt; built by patch_hide_setup_prompts.py --build
                     and gitignored, so no tracked binary is ever modified

    A player's client wants player, or testbed if that has not been built, since
    a player must never compute a turn. The referee wants resurgence, since
    computing turns is its whole job.
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


def restart(dat, purpose, wait_for_lock=0.0, timeout=180, exe=None):
    """Take the client, close whatever is on it, and launch on `dat`.

    The order is the point. Taking the lock first means a caller that cannot
    have the client is refused before it kills anybody's session; closing first
    and locking second, which is what serve and tick used to do, hands the
    client to whoever moves second.
    """
    take_client_lock(purpose, wait=wait_for_lock)
    try:
        close_client()
        return launch(dat, timeout=timeout, exe=exe, purpose=purpose,
                      wait_for_lock=wait_for_lock)
    except BaseException:
        release_client_lock()
        raise


def launch(dat, timeout=180, exe=None, purpose=None, wait_for_lock=0.0):
    """Start the client on a .dat and wait until its state is readable.

    Takes the machine's client lock first, so a second tool cannot close this
    client out from under whoever started it. `purpose` is what the other tool
    will be told is holding it, so make it something a person can act on.
    `wait_for_lock` of zero refuses immediately when somebody else holds it.

    Waiting on the STATE rather than on a timer is the point: a load that takes
    40 seconds and a load that crashed look identical for the first 39.

    The timeout is generous because the cost of the two mistakes is not
    symmetric. Waiting too long for a client that died wastes a minute; giving
    up on one that was merely slow fails a turn, and unattended runs saw that
    happen at 90 seconds on a machine that was also running a client for
    something else.
    """
    if client_pids():
        raise SystemExit("a client is already running; close it first , "
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
    while time.time() < deadline:
        if not client_pids():
            time.sleep(0.5)
            continue
        try:
            snap = gs.Snapshot()
            # quiet: this is a poll, and a client that has not finished
            # populating its player slot fails the read a few times before it
            # succeeds. Reporting each attempt makes every healthy launch look
            # like a failure in the log.
            civ = gs.resolve_civ(snap, None, quiet=True)
            if civ is not None and snap.owned_planets(civ) and len(snap.suns) > 10:
                log(f"  up: turn {snap.turn}, {civ.civ_name!r}, "
                    f"{len(snap.owned_planets(civ))} planet(s), "
                    f"{len(snap.designs)} design(s)")
                return snap
        except Exception:
            pass
        time.sleep(1.0)
    raise SystemExit(f"client did not become readable within {timeout}s")


# ── the pieces ────────────────────────────────────────────────────────────────
def trigger_save(name, gameid=0, timeout=30):
    """Make the running client save. Returns (exit code, the tool's output).

    In process rather than `sys.executable trigger_save.py`. In the frozen
    launcher sys.executable is the launcher itself, so that command line starts
    a second launcher and hands it a script path it has no use for, and the
    save never happens. trigger_save does its work in a remote thread inside
    the game client, so the only thing a separate process ever contributed was
    an argument parser.

    trigger_save.main() reads its arguments from sys.argv, so sys.argv is what
    it is given. The machine's one client is held by whoever is calling this,
    which is what keeps two of these from overlapping.
    """
    import contextlib
    import io
    import trigger_save as ts

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
                         "server/saves , is cs_server.py running?")
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

    This is what makes --all-civs possible without the running client. The
    single-civ path needs memory only to disambiguate two identically named
    'Colony Ship' designs; when every civ is getting the design there is nothing
    to disambiguate, and the blob says which designs belong to whom by nesting.
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
        # Re-resolve per civ: every injection shifts the offsets behind it, but
        # object IDS are stable, and that is what inject_design keys on.
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
                         "just the local player , an AI-vs-AI galaxy needs both "
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

    # Resolve the local civ BEFORE closing: only the running client can say
    # which of the two identically named starting designs is ours.
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
