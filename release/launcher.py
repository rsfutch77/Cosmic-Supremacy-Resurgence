"""
launcher.py: the player-facing front door for Cosmic Supremacy: Resurgence
"""
from __future__ import annotations

import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import typing

CREATE_NO_WINDOW = 0x08000000
APP_DIRNAME = "CosmicSupremacyResurgence"


def app_dir() -> str:
    """The directory the player sees: where the .exe sits, or release\\ in a checkout."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def bundled(name: str) -> str:
    """A file packed into the frozen build (PyInstaller unpacks these to _MEIPASS)."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


def _repo_root() -> str:
    """Checkout root, one level above release\\. Meaningless in a frozen build."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def game_root_candidates() -> "list[str]":
    """Where a game folder can legitimately be, in priority order."""
    cands = [os.path.join(app_dir(), "game"),   # a release
             app_dir()]                         # exes beside the launcher
    # Frozen, _repo_root() names a temp extraction path, so the checkout
    # layout applies only when running from source.
    if not getattr(sys, "frozen", False):
        cands.append(os.path.join(_repo_root(), "client"))
    return cands


def find_game_root(modes) -> "str | None":
    """First directory that holds every EXE the manifest asks for.

    A mode with a "session" names no EXE: whatever runs the session chooses
    the build.
    """
    wanted = {m["exe"] for m in modes if is_playable(m) and m.get("exe")}
    for cand in game_root_candidates():
        if all(os.path.exists(os.path.join(cand, exe)) for exe in wanted):
            return cand
    return None


def _short(mode) -> str:
    """A mode's name as a noun, for status lines. Falls back to the button text."""
    return mode.get("short") or mode["title"]


def is_playable(mode) -> bool:
    """
    Whether this mode can be launched.

    A placeholder ("enabled": false, or no EXE named) is shown as a greyed-out
    card and is not counted when looking for the game files. A mode with a
    "session" (multiplayer) starts the game itself and names no galaxy file.
    """
    if not mode.get("enabled", True):
        return False
    if mode.get("session"):
        return True
    return bool(mode.get("exe") and mode.get("galaxy"))


def find_galaxy_root(game_root: str, modes) -> "str | None":
    wanted = {m["galaxy"] for m in modes if is_playable(m) and m.get("galaxy")}
    for cand in (os.path.join(game_root, "galaxies"), game_root):
        if all(os.path.exists(os.path.join(cand, g)) for g in wanted):
            return cand
    return None


def set_app_user_model_id():
    """
    Give Windows an explicit taskbar identity, so the taskbar button shows
    our icon and not the Python interpreter's. Call before the first window.
    """
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "CosmicSupremacy.Resurgence.Launcher")
    except Exception:
        pass        # cosmetic only; never worth failing a launch over


def find_icon(data_dir: "str | None" = None) -> "str | None":
    """
    Locate a .ico for the window, or build one.
    """
    packed = bundled("cosmic.ico")
    if os.path.exists(packed):
        return packed
    if not data_dir:
        return None
    cached = os.path.join(data_dir, "cosmic.ico")
    if os.path.exists(cached):
        return cached
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import extract_icon
        for root in game_root_candidates():
            src = os.path.join(root, "CosmicSupremacy.exe")
            if os.path.exists(src):
                extract_icon.main(["extract_icon", src, cached])
                break
    except Exception:
        pass
    return cached if os.path.exists(cached) else None


def find_data_dir() -> str:
    """
    Where the server writes its log and captured saves.
    """
    preferred = os.path.join(app_dir(), "data")
    try:
        os.makedirs(preferred, exist_ok=True)
        probe = os.path.join(preferred, ".writable")
        with open(probe, "w") as fh:
            fh.write("")
        os.remove(probe)
        return preferred
    except OSError:
        fallback = os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP_DIRNAME)
        os.makedirs(fallback, exist_ok=True)
        return fallback


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # No SO_REUSEADDR: we want to know whether anyone is actually listening,
        # and on Windows REUSEADDR would let this bind succeed alongside them.
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def stub_server_answers(host: str, port: int, timeout: float = 1.5) -> bool:
    """True if whatever holds the port speaks our protocol (a dev server, say).

    Used to name the holder, not to decide to share it: see `_boot`.
    """
    import http.client
    conn = None
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("POST", "/clientinterface.php?action=testconnection", "",
                     {"Content-Type": "application/x-cosmicsupremacy",
                      "Content-Length": "0"})
        return conn.getresponse().read(32).strip() == b"READY"
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# A held port is refused (see `_boot`) with a message naming the holder,
# identified by command line as server\referee_worker.py `is_our_cs_server`
# does, plus whether a referee_worker.py is among its parents. Nothing here
# ends a process. A live referee's server is shared, by `referee_share`, once
# its saves folder exists.
HOLDER_REFEREE = "referee"          # this install's cs_server, its worker alive
HOLDER_LEFT_OVER = "left_over"      # this install's cs_server, no worker
HOLDER_OTHER_COPY = "other_copy"    # a cs_server from some other folder
HOLDER_LAUNCHER = "launcher"        # another copy of this launcher
HOLDER_OTHER = "other"              # a program that is not part of the game
HOLDER_UNKNOWN = "unknown"          # nothing could be read

LAUNCHER_EXE = "CosmicSupremacyLauncher.exe"
SERVER_SCRIPT = "cs_server.py"
WORKER_SCRIPT = "referee_worker.py"

_HOLDER_DEPTH = 5   # the listener plus parents read; three is usual

# One PowerShell call: the listener's pid, then each parent in turn, as JSON.
# `-InputObject @()` keeps a one-element answer a list in PowerShell 5.1.
_HOLDER_PS = (
    "$c = Get-NetTCPConnection -LocalPort {port} -State Listen "
    "-ErrorAction SilentlyContinue | Select-Object -First 1; "
    "$out = @(); if ($c) {{ $id = [int]$c.OwningProcess; "
    "for ($i = 0; $i -lt {depth} -and $id -gt 0; $i++) {{ "
    "$p = Get-CimInstance Win32_Process -Filter \"ProcessId=$id\"; "
    "if (-not $p) {{ break }}; "
    "$out += [pscustomobject]@{{pid = [int]$p.ProcessId; "
    "ppid = [int]$p.ParentProcessId; name = [string]$p.Name; "
    "cmd = [string]$p.CommandLine}}; $id = [int]$p.ParentProcessId }} }}; "
    "ConvertTo-Json -Compress -InputObject @($out)")


def port_holder_chain(port: int) -> list:
    """[{pid, ppid, name, cmd}] for whatever listens on `port`, then its
    parents, nearest first. [] when nothing can be read.

    A parent pid may have exited or been reused, so a parent is identified
    only by its command line.
    """
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             _HOLDER_PS.format(port=int(port), depth=_HOLDER_DEPTH)],
            capture_output=True, text=True, timeout=30,
            creationflags=CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    try:
        got = json.loads(out or "[]")
    except ValueError:
        return []
    if isinstance(got, dict):
        got = [got]
    chain, seen = [], set()
    for row in got if isinstance(got, list) else []:
        if not isinstance(row, dict) or not _whole(row.get("pid")):
            continue
        if row["pid"] in seen:
            break
        seen.add(row["pid"])
        chain.append({"pid": row["pid"], "ppid": row.get("ppid"),
                      "name": str(row.get("name") or ""),
                      "cmd": str(row.get("cmd") or "")})
    return chain


def install_server_scripts() -> list:
    """The cs_server.py files that count as this install's own.

    server\\cs_server.py beside release\\ in a checkout, or in the checkout
    above a packaged dist\\<name>\\. A package unzipped elsewhere finds none.
    """
    out, d = [], app_dir()
    for _ in range(4):
        cand = os.path.join(d, "server", SERVER_SCRIPT)
        if os.path.isfile(cand) and cand not in out:
            out.append(cand)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return out


def _script_in(cmd: str, name: str):
    """The full path of the `name` script a command line runs, or None."""
    pattern = r'([A-Za-z]:\\[^"]*?\\' + re.escape(name) + r')(?=["\s]|$)'
    found = re.search(pattern, cmd or "", re.IGNORECASE)
    return found.group(1) if found else None


def _cmd_option(cmd: str, flag: str):
    """The value of `--flag` in a command line, quoted or not, or None."""
    found = re.search(re.escape(flag) + r'(?:=|\s+)(?:"([^"]+)"|(\S+))',
                      cmd or "")
    return (found.group(1) or found.group(2)) if found else None


def referee_saves(worker_cmd: str, server_script: str) -> str:
    """Where a referee worker's captures land, read off its command line.

    `--save-dir` when given, else `--data-dir`\\saves, else the server
    directory's own saves folder.
    """
    saves = _cmd_option(worker_cmd, "--save-dir")
    if saves:
        return saves
    data = _cmd_option(worker_cmd, "--data-dir")
    if data:
        return os.path.join(data, "saves")
    return os.path.join(os.path.dirname(server_script or ""), "saves")


def port_holder(chain, scripts) -> dict:
    """What holds the port, from `port_holder_chain` and this install's
    `install_server_scripts`.

    {kind, pid, name} always, and for a cs_server also `script`, `worker`
    (the pid of a live referee_worker.py among its parents, or None) and
    `saves` (where that worker collects captures).
    """
    if not chain:
        return {"kind": HOLDER_UNKNOWN, "pid": None, "name": ""}
    top = chain[0]
    info = {"kind": HOLDER_OTHER, "pid": top["pid"], "name": top["name"]}
    cmd = os.path.normcase(top["cmd"])
    if SERVER_SCRIPT in cmd:
        script = _script_in(top["cmd"], SERVER_SCRIPT) or ""
        mine = any(os.path.normcase(s) in cmd for s in scripts or ())
        worker = next((p for p in chain[1:]
                       if WORKER_SCRIPT in os.path.normcase(p["cmd"])), None)
        info.update(script=script,
                    worker=worker["pid"] if worker else None,
                    saves=referee_saves(worker["cmd"], script)
                    if worker else None)
        if not mine:
            info["kind"] = HOLDER_OTHER_COPY
        else:
            info["kind"] = HOLDER_REFEREE if worker else HOLDER_LEFT_OVER
    elif (LAUNCHER_EXE.lower() in cmd
          or os.path.normcase(os.path.join("release", "launcher.py")) in cmd):
        info["kind"] = HOLDER_LAUNCHER
    return info


def port_held_message(port: int, holder: dict, speaks_protocol: bool,
                      data_dir: str):
    """(status line, log line, box text) for a port this launcher cannot use.
    """
    pid, kind = holder.get("pid"), holder.get("kind")
    saves_here = os.path.join(data_dir, "saves")
    retry = "Close it and start this launcher again."
    if kind == HOLDER_REFEREE:
        line = json.dumps({"adopt_server": holder.get("saves") or ""})[1:-1]
        return (f"port {port} is held by this PC's referee",
                f"port {port} is held by pid {pid}, the cs_server of this "
                f"install's referee worker (pid {holder.get('worker')})",
                f"The referee running on this PC holds port {port}. It keeps "
                f"the game's server there for its own turns (pid {pid}), so "
                "it must not be closed while it is refereeing.\n\n"
                "To play on this PC as well, this launcher can share that "
                f"server. Add this entry to {MP_CONFIG} in\n{data_dir}\n\n"
                f"    {line}\n\n"
                "inside its braces, then start this launcher again.")
    if kind == HOLDER_LEFT_OVER:
        return (f"port {port} is held by a leftover referee server",
                f"port {port} is held by pid {pid}, a cs_server of this "
                "install with no referee worker running",
                f"Port {port} is held by a game server that a referee on "
                "this PC started and left running when it stopped "
                f"(pid {pid}, {holder.get('name') or 'python.exe'}). No "
                "referee is running to use it.\n\n"
                "Starting the referee again normally ends it and takes the "
                "port back. Or end it yourself in Task Manager, on the "
                "Details tab, "
                f"pid {pid}. Then start this launcher again.")
    if kind == HOLDER_OTHER_COPY:
        where = os.path.dirname(holder.get("script") or "") or "another folder"
        ref = (f", and a referee from that copy is running (pid "
               f"{holder.get('worker')})" if holder.get("worker") else "")
        return (f"port {port} is held by another copy's server",
                f"port {port} is held by pid {pid}, a cs_server from {where}",
                f"Port {port} is held by a game server from another copy of "
                f"the game, in\n{where}\n\n(pid {pid}){ref}.\n\nIt keeps its "
                "saved turns in its own folder, so this launcher cannot "
                "share it. " + retry)
    if kind == HOLDER_LAUNCHER:
        return (f"port {port} is held by another launcher",
                f"port {port} is held by pid {pid}, another copy of this "
                "launcher",
                "Another copy of this launcher is already running and holds "
                f"port {port} (pid {pid}).\n\nUse that window, or close it "
                "and start this one again.")
    if kind == HOLDER_OTHER and not speaks_protocol:
        what = holder.get("name") or "another program"
        return (f"port {port} is held by {what}",
                f"port {port} is held by pid {pid} ({what}), not part of "
                "this game",
                f"Port {port} is held by {what} "
                f"(pid {pid}), which is not part of this game.\n\nThe game "
                "can only talk to that exact port, so multiplayer and TestBed "
                "will not work until it is free. Tutorial and Demo are "
                "unaffected.\n\n" + retry)
    # Nothing could be read about the holder, or it speaks the protocol from
    # a process none of the above describes.
    return (f"port {port} is held by another server",
            f"port {port} is in use by "
            + ("a server speaking our protocol" if speaks_protocol
               else "something that did not answer testconnection")
            + ", not reusing it",
            f"Another server already holds port {port}.\n\n"
            + ("It speaks this game's protocol, so it is probably a second "
               "copy of this launcher or a cs_server you started yourself. "
               "It cannot be shared: it keeps its saved turns in its own "
               "folder, this launcher would look for them in\n"
               f"{saves_here}\n\nand every turn "
               "would be captured somewhere this launcher never reads.\n\n"
               if speaks_protocol else
               "The game can only talk to that exact port, so multiplayer "
               "and TestBed will not work until it is free. Tutorial and "
               "Demo are unaffected.\n\n")
            + retry)


def referee_share(holder: dict, speaks_protocol: bool, configured=None):
    """The saves folder of this PC's referee server to share, or None.

    Only for a `HOLDER_REFEREE` holder that answers the protocol, and only
    when `multiplayer.json` names no `adopt_server` (a configured entry is used
    or refused as written). The folder, from `referee_saves`, must be absolute
    (a relative one resolves against the worker's unknown working directory)
    and listable. The worker's status file in server\\worker_work is not read:
    it does not record where captures land and can outlive the worker.

    Sharing never touches the holder: it is not ended, rebound or restarted.
    """
    if configured:
        return None
    if not speaks_protocol or holder.get("kind") != HOLDER_REFEREE:
        return None
    if not _whole(holder.get("worker")) or not _whole(holder.get("pid")):
        return None
    saves = holder.get("saves")
    if not saves or not isinstance(saves, str) or not os.path.isabs(saves):
        return None
    try:
        if not os.path.isdir(saves):
            return None
        os.listdir(saves)
    except OSError:
        return None
    return os.path.normpath(saves)


def start_server(host: str, port: int, data_dir: str, galaxy_dir: str, sink):
    """
    Import cs_server and serve it on daemon threads. Returns (servers, logfile).

    cs_server reads PORT and DATA_DIR from the environment at import time, so
    the environment is set first and the import happens here.
    """
    os.environ["CS_DATA_DIR"] = data_dir
    os.environ["CS_GALAXY_DIR"] = galaxy_dir
    os.environ["CSPORT"] = str(port)

    if "cs_server" in sys.modules:
        raise RuntimeError("cs_server was imported before its environment was set")
    if not getattr(sys, "frozen", False):
        server_dir = os.path.join(_repo_root(), "server")
        if server_dir not in sys.path:
            sys.path.insert(0, server_dir)
    import http.server
    import cs_server

    # Tee the server's logging into the log pane. cs_server logs through the
    # module global, so rebinding it catches every call site.
    inner = cs_server.log
    def tee(msg: str):
        inner(msg)
        sink(msg)
    cs_server.log = tee

    # Listen on both loopback addresses: the client connects to "localhost",
    # which Windows resolves to ::1 before 127.0.0.1.
    servers = []
    httpd = http.server.HTTPServer((host, port), cs_server.CSHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="cs_server-v4").start()
    servers.append(httpd)

    if socket.has_ipv6:
        class _V6Server(http.server.HTTPServer):
            address_family = socket.AF_INET6
        try:
            v6 = _V6Server(("::1", port), cs_server.CSHandler)
            threading.Thread(target=v6.serve_forever, daemon=True,
                             name="cs_server-v6").start()
            servers.append(v6)
        except OSError as exc:
            # A host with IPv6 disabled still works over IPv4.
            sink(f"ipv6 loopback listener unavailable ({exc})")

    return servers, cs_server.LOGFILE


def _process_names_via_api() -> "list[str] | None":
    """
    Every running process name, from the toolhelp snapshot API.

    None if the API is unavailable, so the caller can fall back to tasklist.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD),
                        ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                        ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD),
                        ("szExeFile", wintypes.WCHAR * 260)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.Process32FirstW.argtypes = [wintypes.HANDLE,
                                        ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.argtypes = [wintypes.HANDLE,
                                       ctypes.POINTER(PROCESSENTRY32W)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        TH32CS_SNAPPROCESS = 0x00000002
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == wintypes.HANDLE(-1).value:
            return None
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not k32.Process32FirstW(snap, ctypes.byref(entry)):
                return []
            names = []
            while True:
                names.append(entry.szExeFile)
                if not k32.Process32NextW(snap, ctypes.byref(entry)):
                    break
            return names
        finally:
            k32.CloseHandle(snap)
    except Exception:
        return None


def running_clients(exe_names) -> "list[str]":
    """
    Names of any Cosmic Supremacy *client* already running.
    """
    wanted = {n.lower() for n in exe_names}
    wanted.discard(os.path.basename(sys.executable).lower())
    if not wanted:
        return []

    names = _process_names_via_api()
    if names is None:
        try:
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=10,
                                 creationflags=CREATE_NO_WINDOW).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        names = [line.split('","')[0].lstrip('"')
                 for line in out.splitlines() if line.startswith('"')]

    return sorted({n for n in names if n.lower() in wanted})


AI_EXE = "CosmicSupremacyAI.exe"


def find_ai() -> "list[str] | None":
    """How to start the opponent, or None if it is not installed.
    """
    for root in (os.path.join(app_dir(), "game"), app_dir()):
        cand = os.path.join(root, AI_EXE)
        if os.path.exists(cand):
            return [cand]
    if not getattr(sys, "frozen", False):
        script = os.path.join(_repo_root(), "client", "dev_tools",
                              "ai_player", "ai.py")
        if os.path.exists(script):
            return [sys.executable, script]
    return None


def ai_args(mode) -> "list[str]":
    """The opponent's command line.

    --stall 0 disables the no-turn-in-N-seconds bail. --vision known keeps
    the AI under the player's fog of war.
    """
    return ["--civ", mode["ai"]["civ"], "--apply", "--follow",
            "--vision", "known", "--stall", "0", "--wait-for-client", "180"]


def launch_ai(mode, data_dir: str) -> "subprocess.Popen | None":
    cmd = find_ai()
    if cmd is None:
        return None
    env = dict(os.environ)
    # The frozen AI unpacks into a temp directory Windows deletes on exit, so
    # its discovery set has to live somewhere that survives the process.
    env["CS_AI_STATE_DIR"] = os.path.join(data_dir, "ai_state")
    env["PYTHONUNBUFFERED"] = "1"      # or the log pane fills in 8 KB bursts
    os.makedirs(env["CS_AI_STATE_DIR"], exist_ok=True)
    return subprocess.Popen(
        cmd + ai_args(mode), cwd=os.path.dirname(cmd[-1]) or None,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, errors="replace",
        bufsize=1, env=env, creationflags=CREATE_NO_WINDOW)


# Written by cs_server when the client reports the Tutorial finished.
TUTORIAL_FILE = "tutorial.json"


def mode_done(mode, data_dir: str) -> bool:
    """Whether a mode is one the player has finished: the Tutorial, once
    passed."""
    if mode.get("id") != "tutorial" or not data_dir:
        return False
    return os.path.exists(os.path.join(data_dir, TUTORIAL_FILE))


# New Game's galaxy, among the saves so that Play continues it.
NEW_GAME_FILE = "New Game_turn0.dat"
GENERATE_TIMEOUT = 120.0   # seconds for the client to build a galaxy


def galaxy_builder(game_root: str) -> "str | None":
    """The client New Game builds a galaxy with: a TestBed-based one, whose
    galaxies are the shipped Single Player galaxy's size (32 suns). The Single
    Player client builds them three times larger from the same pass file."""
    dirs = [game_root]
    if not getattr(sys, "frozen", False):
        dirs.append(os.path.join(_repo_root(), "client"))
    for d in dirs:
        for exe in MP_CLIENTS:
            if os.path.exists(os.path.join(d, exe)):
                return os.path.join(d, exe)
    return None


def generate_galaxy(game_root, pass_file, save_dir, out, log=print,
                    timeout=GENERATE_TIMEOUT) -> str:
    """Build a fresh galaxy and write it, seeded for the AI, to `out`. The
    building client is closed again either way."""
    import gamectl
    if multiplayer_modules() is None:
        raise gamectl.GameError("this build has no galaxy tools")
    builder = galaxy_builder(game_root)
    if builder is None:
        raise gamectl.GameError(f"none of {', '.join(MP_CLIENTS)} to build "
                                f"a galaxy with")
    if not os.path.exists(pass_file):
        raise FileNotFoundError(pass_file)
    import gamestate as gs
    import make_single_player_galaxy as msg

    started = time.time() - 1
    child = subprocess.Popen([builder, pass_file], cwd=os.path.dirname(builder))
    try:
        deadline = time.time() + timeout
        while True:
            if child.poll() is not None:
                raise gamectl.GameError("the game exited while building the galaxy")
            try:
                snap = gs.Snapshot()
                if snap.civs and len(snap.suns) > 10:
                    break
            except Exception:
                pass            # still loading
            if time.time() > deadline:
                raise gamectl.GameError("the galaxy was not built in time")
            time.sleep(1.0)
        log(f"new galaxy: {len(snap.suns)} suns, civs "
            f"{[c.civ_name for c in snap.civs]}")
        pid, _name = gamectl.find_client()
        with gamectl.Client(pid) as c:
            if not c.save_game("newgalaxy"):
                raise gamectl.GameError("the game refused to save the galaxy")
        time.sleep(1.5)
        cap = gamectl.newest_capture(save_dir, started)
        if cap is None:
            raise gamectl.GameError("the galaxy's save never reached the server")
        with open(cap, "r", encoding="ascii") as fh:
            blob = gamectl.decode_blob(fh.read())
    finally:
        child.terminate()
        try:
            child.wait(timeout=10)
        except Exception:
            pass
    blob = msg.seeded(blob, log=lambda _m: None)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, out)
    log(f"new galaxy written to {os.path.basename(out)}")
    return out


def latest_save(dat_dir: str) -> "str | None":
    """The most recently written .dat in `dat_dir`, or None."""
    try:
        dats = [os.path.join(dat_dir, n) for n in os.listdir(dat_dir)
                if n.lower().endswith(".dat")]
    except OSError:
        return None
    return max(dats, key=os.path.getmtime, default=None)


# Single Player saves itself as each turn ends, so Play continues from the last
# turn played even when the player never pressed Save. A manual save is always
# "<name>_turn<N>.dat", so no manual save can match AUTOSAVE_FILE.
AUTOSAVE_NAME = "Autosave"          # the name the engine is given, under 16 chars
AUTOSAVE_FILE = re.compile(r"^Autosave, turn (\d+)\.dat$")
AUTOSAVE_KEEP = 2


def autosave_path(dat_dir: str, turn) -> str:
    return os.path.join(dat_dir, f"Autosave, turn {turn}.dat")


def prune_autosaves(dat_dir: str, keep: int = AUTOSAVE_KEEP) -> list:
    """Delete all but the `keep` newest autosaves. Returns the names removed."""
    try:
        names = [n for n in os.listdir(dat_dir) if AUTOSAVE_FILE.match(n)]
    except OSError:
        return []
    # The turn breaks a tie between saves written within the clock's resolution.
    paths = sorted((os.path.join(dat_dir, n) for n in names), reverse=True,
                   key=lambda p: (os.path.getmtime(p),
                                  int(AUTOSAVE_FILE.match(os.path.basename(p))[1])))
    removed = []
    for path in paths[keep:]:
        try:
            os.remove(path)
            removed.append(os.path.basename(path))
        except OSError:
            pass            # held open by a Load in progress; next turn retries
    return removed


def launch_mode(mode, game_root: str, galaxy_root: str,
                galaxy: "str | None" = None) -> subprocess.Popen:
    """Start a mode's client on its galaxy, or on `galaxy` when given."""
    exe = os.path.join(game_root, mode["exe"])
    galaxy = galaxy or os.path.join(galaxy_root, mode["galaxy"])
    if not os.path.exists(exe):
        raise FileNotFoundError(exe)
    if not os.path.exists(galaxy):
        raise FileNotFoundError(galaxy)
    # cwd is the EXE's own folder: the client writes save_game_*.dat relative
    # to it.
    return subprocess.Popen([exe, galaxy], cwd=game_root)


# The player's name, which is also their civilisation's name in a galaxy. It
# lives in the data directory, which survives upgrades that replace the manifest.
IDENTITY_FILE = "identity.json"

NAME_LIMIT = 15   # the engine's civ name buffer, as make_multiplayer_galaxy.py

# Characters Windows refuses in a file name. TurnStore keeps a player's orders
# in submissions\<turn>\<civ>.b64, so a civ name is a file name.
NAME_FORBIDDEN = r'\/:*?"<>|'

NAME_RULES = (f"Up to {NAME_LIMIT} characters. Letters, digits and ordinary "
              f"punctuation, but not {' '.join(NAME_FORBIDDEN)}")


def validate_player_name(raw, forbidden=NAME_FORBIDDEN,
                         holder="The game's civilisation name"):
    """(name, None) for a name that can be used, or (None, why not).

    The reason is shown to the player next to the field. Surrounding spaces
    are trimmed.
    """
    name = (raw or "").strip()
    if not name:
        return None, "Enter a name."
    if len(name) > NAME_LIMIT:
        return None, (f"That is {len(name)} characters. {holder} "
                      f"holds {NAME_LIMIT}.")
    # The engine's name field is ASCII, and a civ name is also a file name, so
    # both rules refuse characters. The player is told which ones, not why.
    bad = sorted({c for c in name if c in forbidden or not " " <= c <= "~"})
    if bad:
        shown = [c for c in bad if c.isprintable() and not c.isspace()]
        if not shown:
            return None, "We can't accept that character in a name."
        listed = shown[0] if len(shown) == 1 else (
            ", ".join(shown[:-1]) + " or " + shown[-1])
        return None, f"We can't accept {listed} in a name."
    if name.endswith("."):
        return None, "A name cannot end in a full stop."
    return name, None


# The server drops # and ' from a save's name, and the name is also the .dat's.
SAVE_NAME_FORBIDDEN = NAME_FORBIDDEN + "#'"

SAVE_NAME_RULES = (f"Up to {NAME_LIMIT} characters. Letters, digits and ordinary "
                   f"punctuation, but not {' '.join(SAVE_NAME_FORBIDDEN)}")


def validate_save_name(raw):
    return validate_player_name(raw, SAVE_NAME_FORBIDDEN, "A save name")


def identity_path(data_dir: str) -> str:
    return os.path.join(data_dir, IDENTITY_FILE)


def load_identity(data_dir: str):
    """The name this player entered, or None.

    A file that is missing, unreadable or holds a name the rules reject counts
    as no name.
    """
    try:
        with open(identity_path(data_dir), encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        return None
    name = stored.get("name") if isinstance(stored, dict) else None
    name, _why = validate_player_name(name if isinstance(name, str) else "")
    return name


def save_identity(data_dir: str, name: str) -> None:
    with open(identity_path(data_dir), "w", encoding="utf-8") as fh:
        json.dump({"name": name}, fh, indent=2)


def legacy_civ(data_dir: str):
    """The `civ` a player hand-wrote into multiplayer.json, or None.

    Not held to the entry rules, since the name may already be in a live
    roster. Only the length ceiling and the file name characters are checked.
    """
    try:
        with open(os.path.join(data_dir, MP_CONFIG), encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return None
    civ = cfg.get("civ") if isinstance(cfg, dict) else None
    if not isinstance(civ, str):
        return None
    civ = civ.strip()
    if not civ or len(civ) > NAME_LIMIT:
        return None
    if any(c in NAME_FORBIDDEN or not (" " <= c <= "~") for c in civ):
        return None
    return civ


def player_name(data_dir: str):
    """This player's name, adopting an older multiplayer.json's `civ` once.

    The `civ` key is left in multiplayer.json, so a launcher that still
    requires it works.
    """
    name = load_identity(data_dir)
    if name:
        return name
    civ = legacy_civ(data_dir)
    if not civ:
        return None
    try:
        save_identity(data_dir, civ)
    except OSError:
        pass                # usable this run; asked for again on the next one
    return civ


# What a galaxy's state says about itself, by the keys turn_store writes, read
# from the state already held. An absent key means open and not reclaimed.
STATUS_KEY = "status"
CLOSED = "closed"
CLOSED_REASON_KEY = "closed_reason"
RECLAIMED_KEY = "reclaimed"

# The other words a listing orders and colours rows by. `forming` is not
# written anywhere: it is a galaxy whose store has no first turn yet.
OPEN = "open"
FORMING = "forming"


def reclaimed_seat(state, name: str):
    """What became of this player's own seat, or None if nothing did.

    Read by the raw key from the state already held, so a galaxy without the
    key answers None. Only the record for `name` is returned, so other
    players' reclaimed seats are never shown.
    """
    taken = (state or {}).get(RECLAIMED_KEY)
    if not isinstance(taken, dict):
        return None
    record = taken.get(name)
    return record if isinstance(record, dict) else None


def _count(value):
    """A whole number from a record written elsewhere, or None. Booleans are
    rejected as malformed.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def roster_problem(name: str, civs, reclaimed=None):
    """Why this name cannot play this galaxy, or None. Written for the player.

    Other players' names are never shown; the seat count is given instead. A
    seat that differs only in case is named, since it matches what the player
    typed. `reclaimed` is this player's own record from `reclaimed_seat`, so a
    seat the galaxy took back is reported as that and not as a missing seat.
    """
    seats = list(civs)
    if name in seats:
        return None
    # An empty record or a non-mapping is not reported as a reclaim.
    taken = reclaimed if isinstance(reclaimed, dict) and reclaimed else None
    if taken is not None:
        turn, missed = _count(taken.get("turn")), _count(taken.get("missed"))
        when = f" at turn {turn}" if turn is not None else ""
        after = ("" if not missed else
                 " after 1 missed turn" if missed == 1 else
                 f" after {missed} missed turns in a row")
        return (f"This galaxy took the seat {name!r} back{when}{after}.\n\n"
                "An empire that stops playing is removed so its "
                "planets are freed up for others. To play again, ask for a "
                "new seat from the Galaxies list.")
    near = next((c for c in seats if str(c).lower() == name.lower()), None)
    if near is not None:
        return (f"This galaxy spells your seat {near!r} and you are calling "
                f"yourself {name!r}.\n\nNames are matched exactly, so orders "
                f"sent as {name!r} would be ignored.")
    count = ("no seats at all" if not seats else
             "one seat" if len(seats) == 1 else f"{len(seats)} seats")
    return (f"This galaxy has no seat for {name!r}.\n\nIt has {count}, and "
            "names are matched exactly. Check the spelling of the name you "
            "were given, or ask whoever made the galaxy to add a seat for you.")


# The build name is stamped at package time: build.ps1 takes it from its
# -Version switch or from manifest.json, stamp_build.py writes it to
# build.json, and the spec packs that beside manifest.json, read back through
# bundled(). A build with no build.json reports the manifest's version with a
# marker, so it is never mistaken for a release.
BUILD_FILE = "build.json"
DEV_MARK = "dev"                # run from a clone; no packaging step involved
UNSTAMPED_MARK = "unstamped"    # packaged, but the stamp did not reach the bundle

# Where a launcher that is too old is told to go.
UPDATE_URL = "https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/releases/latest"

# The galaxy state key for its minimum build. A galaxy without one is played
# by any build.
MIN_BUILD_KEY = "min_build"


def _read_json(path: str):
    """A JSON object from `path`, or None if there is not one there."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def build_info() -> dict:
    """What the packaging step recorded about this build.

    Always carries "build". A stamped build also carries "stamped_at", when it
    was packaged, and "commit", the checkout it came from, with a trailing +
    when that checkout had uncommitted changes.
    """
    info = _read_json(bundled(BUILD_FILE))
    if info and isinstance(info.get("build"), str) and info["build"].strip():
        return info
    cfg = _read_json(bundled("manifest.json")) or {}
    version = str(cfg.get("version") or "0.0.0")
    mark = UNSTAMPED_MARK if getattr(sys, "frozen", False) else DEV_MARK
    return {"build": f"{version}+{mark}"}


def build_id() -> str:
    """This launcher's build, as the player sees it and as a galaxy reads it."""
    return build_info()["build"]


def build_parts(build: str) -> "tuple[int, ...]":
    """The orderable part of a build name: "0.1.10+dev" gives (0, 1, 10).

    Only the leading dotted number counts; the suffix says how a build was
    made, not how new it is.
    """
    lead = re.match(r"\d+(?:\.\d+)*", (build or "").strip())
    if lead is None:
        return ()
    return tuple(int(p) for p in lead.group(0).split("."))


def build_is_below(build: str, minimum: str) -> bool:
    """Is `build` older than `minimum`?

    Shorter names are padded with zeros, so 0.2 and 0.2.0 are the same build. A
    minimum with no number holds nobody back; a build with no number is below
    every minimum.
    """
    want = build_parts(minimum)
    if not want:
        return False
    have = build_parts(build)
    width = max(len(have), len(want))
    have += (0,) * (width - len(have))
    want += (0,) * (width - len(want))
    return have < want


def version_problem(build: str, state) -> "str | None":
    """Why this build cannot play this galaxy, or None. Written for the player.

    The minimum is the optional `min_build` in the galaxy's state. One that
    cannot be read as a build is not enforced. The message names both builds
    and where to get the new one.
    """
    minimum = (state or {}).get(MIN_BUILD_KEY)
    if not isinstance(minimum, str) or not build_parts(minimum):
        return None
    if not build_is_below(build, minimum):
        return None
    return (f"This galaxy needs build {minimum} or newer.\n\nThis launcher is "
            f"build {build}.\n\nAn older build can hand the galaxy a turn the "
            "referee cannot use, and that goes wrong quietly, so the galaxy "
            "stops here instead.\n\nThe current release is at\n" + UPDATE_URL
            + "\n\nCopy the data folder from this launcher's folder into the "
            "new one before starting it, or it signs in as someone new and "
            "your seat will be refused.")


def closed_problem(state) -> "str | None":
    """Why this galaxy is no longer being played, or None. For the player.

    Asked with `version_problem`, ahead of the roster check, since a closed
    galaxy explains any roster or submission refusal. The operator's reason is
    shown when the state carries one. A state with no status is open.
    """
    if (state or {}).get(STATUS_KEY) != CLOSED:
        return None
    reason = (state or {}).get(CLOSED_REASON_KEY)
    said = f"\n\n{reason}" if isinstance(reason, str) and reason.strip() else ""
    return (f"This galaxy has been closed.{said}\n\nIt still reads, so its "
            "turns are all still there, but it takes no more of them and a "
            "turn played into it would be refused.\n\nPick another galaxy "
            "from the Galaxies list to keep playing.")


# What the turn readout says once the galaxy being followed has closed.
GALAXY_ENDED_NOTE = "this galaxy has ended"

# What the turn readout and the status line say while the turn loop cannot read
# the galaxy's store: a connection or service problem, not the galaxy stopping.
UNREACHABLE_NOTE = "cannot reach the galaxy, retrying"


# server\abandonment.py warns a player who has missed too many turns in a row
# through `put_note`, each turn until they play or the seat goes. The warning
# is found by `warning_note`'s opening line, which sets it apart from refusal
# lines in the same note. Neither file imports the other; release\tests checks
# this constant against `warning_note`'s output.
MISSED_WARNING_OPENING = "You have missed "


def abandonment_warning(lines):
    """The warning part of a referee's note, or None when it holds none.

    From the warning's first line to the end, since `abandonment` appends it
    after anything already in the note.
    """
    if not isinstance(lines, (list, tuple)):
        return None
    for i, line in enumerate(lines):
        if isinstance(line, str) and line.startswith(MISSED_WARNING_OPENING):
            return [str(x) for x in lines[i:]]
    return None


def warning_text(galaxy: str, lines) -> str:
    """The box a warned player reads: the referee's own lines, whole."""
    where = f" in {galaxy}" if galaxy else ""
    return (f"A warning from the referee{where}:\n\n" + "\n".join(lines))


# The relay refuses a request with a status and a JSON body holding one
# sentence, `{"error": ...}`. `urllib`'s HTTPError text is only the status line.
# A seat is refused in one of two sentences: SEAT_HELD where the galaxy lets a
# sign-in claim a seat on first use, NOT_SEATED where it does not. An email
# account is refused in UNVERIFIED until its address is verified.
SEAT_HELD = "already held by another sign-in"
NOT_SEATED = "is bound to this sign-in"
UNVERIFIED = "verify the email address of this account"


def seat_refused(text) -> bool:
    """Whether a refusal's text says this sign-in holds no seat to play."""
    text = str(text or "")
    return SEAT_HELD in text or NOT_SEATED in text


def email_unverified(text) -> bool:
    """Whether a refusal's text says this account's email is not verified."""
    return UNVERIFIED in str(text or "")


def relay_refusal(exc):
    """(status, sentence) from a refused relay request, or None.

    None for anything that is not an HTTP error, and for one whose body holds
    no sentence to show, so the caller falls back to the exception's text.
    """
    code = getattr(exc, "code", None)
    read = getattr(exc, "read", None)
    if not _whole(code) or not callable(read):
        return None
    try:
        body = json.loads(read() or b"{}")
    except Exception:                       # not the relay's shape
        return None
    said = body.get("error") if isinstance(body, dict) else None
    if not isinstance(said, str) or not said.strip():
        return None
    return code, said.strip()


# A player who reinstalls, moves the game, or runs a second copy signs in as a
# new anonymous user, and their seat is refused to it. The operator rebinds the
# seat with server\dev_tools\seat_tool.py, whose `candidates` lists unseated
# sign-ins by uid. The player code is the first characters of this install's
# uid: enough to pick the line, and not a credential (the refresh token in
# fb_identity.json signs in and is never shown).
PLAYER_CODE_LENGTH = 8


def player_code(uid) -> "str | None":
    """The short code a player reads out to the operator, or None."""
    if not isinstance(uid, str) or len(uid) < PLAYER_CODE_LENGTH:
        return None
    return uid[:PLAYER_CODE_LENGTH]


def build_line(build: str, code) -> str:
    """The line under the title: the build, and the player code if any."""
    return f"v{build}" + (f"   \u00b7   player code {code}" if code else "")


def seat_held_text(civ: str, code) -> str:
    """What a player reads when their seat belongs to another sign-in."""
    said = (f"The seat {civ!r} in this galaxy is held by another sign-in. "
            "That usually means it was claimed from another copy of the "
            "game, or from this one before it was reinstalled or moved.")
    if code:
        return (said + "\n\nIf the seat is yours, send the galaxy's operator "
                f"this player code and they can move it to this copy:\n\n"
                f"    {code}\n\nThe same code is shown under the launcher's "
                "title.")
    return (said + "\n\nIf the seat is yours, ask the galaxy's operator to "
            "move it to this copy.")


def refusal_box(text, civ: str, data_dir: str):
    """What a player reads about a refusal they can act on, or None."""
    if seat_refused(text):
        return seat_held_text(civ, player_code(install_uid(data_dir)))
    if email_unverified(text):
        return VERIFY_LOOP_TEXT.format(
            email=account_email(data_dir) or "your address")
    return None


# A multiplayer galaxy has a turn store that every launcher and the referee
# agree through: it says which turn is current and when it is due. The client
# reads a .dat only at startup, so it restarts every turn.
#
# The turn machinery ships in the frozen build as hidden imports (build.ps1),
# with the player client copied into game\. multiplayer_modules() imports it,
# and bind_multiplayer_paths() repoints its __file__-derived paths.
MP_CONFIG = "multiplayer.json"

# What the turn loop may start. Not in the manifest: the loop picks the build.
MP_CLIENTS = ("CosmicSupremacy_Player.exe", "CosmicSupremacy_TestBed.exe")


def multiplayer_file(data_dir: str):
    """multiplayer.json as it stands, or None if there is not one to read.

    A file naming only a `directory` carries no store, so `multiplayer_config`
    answers None for it while this still returns it.
    """
    return _read_json(os.path.join(data_dir, MP_CONFIG))


def adopted_server(data_dir: str):
    """Where an already-running server keeps its captures, or None.

    `multiplayer.json`'s `adopt_server`: the saves directory of a server this
    launcher did not start and may share. Without it a held port is refused,
    except for this install's own referee (see `referee_share`). The protocol
    cannot report where a server writes captures, so the operator supplies it
    here, and a wrong directory is checked and refused. `referee_worker` has
    the same option, `--adopt-server` with `--save-dir`.
    """
    cfg = multiplayer_file(data_dir) or {}
    where = cfg.get("adopt_server")
    if not where or not isinstance(where, str):
        return None
    return os.path.abspath(os.path.expandvars(os.path.expanduser(where)))


def multiplayer_config(data_dir: str):
    """{"store": <dir or URL>} for this galaxy, or None.

    Kept in the data directory, since it is per player and per galaxy. Only
    `store` is required; a `civ` is read by `legacy_civ`. An optional boolean
    `auth` says whether to send this install's Firebase identity: see
    `store_wants_token`.
    """
    cfg = multiplayer_file(data_dir)
    if not cfg or not cfg.get("store"):
        return None
    return cfg


def multiplayer_modules():
    """The turn machinery, or None if this build cannot reach it.

    Frozen, the modules are imported from the bundle; from a clone their four
    directories go on sys.path first. A build frozen without them returns
    None, and the rest of the launcher still works.
    """
    if not getattr(sys, "frozen", False):
        root = _repo_root()
        server = os.path.join(root, "server")
        if not os.path.exists(os.path.join(server, "player_turn.py")):
            return None
        for d in (server, os.path.join(server, "dev_tools"),
                  os.path.join(root, "client", "dev_tools"),
                  os.path.join(root, "client", "dev_tools", "ai_player")):
            if d not in sys.path:
                sys.path.insert(0, d)
    try:
        import player_turn
        import turn_store
    except ImportError:
        return None
    return player_turn, turn_store


def store_wants_token(cfg) -> bool:
    """Whether this galaxy's store is one to put an identity on.

    An https store (the Cloud Function) wants a token. A folder or a LAN
    `turn_server.py` does not, so it needs no Firebase sign-up or network
    call. An `auth` key in multiplayer.json overrides either way (for a local
    emulator).
    """
    want = cfg.get("auth")
    if isinstance(want, bool):
        return want
    return str(cfg.get("store", "")).startswith("https://")


def player_token(data_dir: str):
    """A zero-argument callable answering this install's Firebase ID token.

    None when this build cannot reach `fb_auth`; the store then sends no
    Authorization header. The identity is separate from the player's name and
    is never shown: see server\\fb_auth.py. Call after multiplayer_modules(),
    which puts server\\ on sys.path in a checkout.
    """
    try:
        import fb_auth
    except ImportError:
        return None
    return fb_auth.identity(data_dir).token


def _takes_token(fn) -> bool:
    """Whether `fn` accepts a `token` keyword, so a turn_store without it
    still opens galaxies.
    """
    import inspect
    try:
        return "token" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def open_player_store(turn_store, spec: str, token=None):
    """`open_store`, carrying this install's identity when there is one.

    The token is passed as a callable, since a galaxy is followed for longer
    than a token lasts.
    """
    if token is None:
        return turn_store.open_store(spec)
    if _takes_token(turn_store.open_store):
        return turn_store.open_store(spec, token=token)
    http = turn_store.HttpTurnStore
    if _takes_token(http) and spec.startswith(("http://", "https://")):
        return http(spec, token=token)
    return turn_store.open_store(spec)


def bind_multiplayer_paths(game_root: str, data_dir: str):
    """Point the turn machinery at the folders this player actually has.

    game_cycle and player_turn compute their paths from __file__ at import
    time, which in a frozen build is PyInstaller's temporary directory. The
    clients and their working directory are rebound to the game folder, and
    turn output to the data directory where the stub server keeps its saves.
    In a checkout only the saves directory changes.
    """
    import game_cycle as gc
    import player_turn

    gc.HERE = game_root
    gc.CLIENT_DIR = game_root
    gc.EXE = os.path.join(game_root, "CosmicSupremacy_Resurgence.exe")
    gc.TESTBED_EXE = os.path.join(game_root, "CosmicSupremacy_TestBed.exe")
    gc.PLAYER_EXE = os.path.join(game_root, "CosmicSupremacy_Player.exe")
    gc.BUILDS = {"resurgence": gc.EXE, "testbed": gc.TESTBED_EXE,
                 "player": gc.PLAYER_EXE}
    gc.SAVES = os.path.join(data_dir, "saves")
    # serve() writes each turn's .dat under HERE, and the client must be able
    # to read it after the launcher has exited.
    player_turn.HERE = data_dir


def fmt_left(seconds: float) -> str:
    """A countdown a player can read at a glance."""
    seconds = int(max(0, seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    return f"{seconds // 60}:{seconds % 60:02d}"


def fmt_ago(seconds: float) -> str:
    """How long ago something happened, for a line that is repainted every
    second.

    Rounded; anything under three seconds is "just now".
    """
    seconds = max(0.0, seconds)
    if seconds < 3:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds) // 60}m ago"
    return f"{int(seconds) // 3600}h ago"


# Which galaxies exist is asked of a directory, server\galaxy_directory.py,
# opened from a spec the way a store is; a galaxy is opened with the store spec
# the directory returns. A multiplayer.json that names a store (a folder or a
# LAN turn_server) needs no directory, identity or network call.
JOINED_FILE = "joined.json"

# A joined.json carrying this key holds one record per galaxy; any other shape
# is a single record, read as one of these.
JOINED_KEY = "galaxies"
JOINED_VERSION = 2

# The directory listed when nothing names another: the relay's Cloud Run host,
# the only one open to callers outside the project. Players read through the
# relay because a direct Firestore read authenticates as the project.
BETA_DIRECTORY = "https://relay-r5t6py5oxa-uw.a.run.app"

# Where a join request goes in a galaxy that is a folder. A store that knows its
# own route is asked instead: see send_join_request.
JOIN_DIR = "joins"

GAMES_POLL_MS = 120000   # how often the page re-lists itself; slow on purpose


class JoinNotAccepted(Exception):
    """This galaxy has nowhere to put a join request."""


def directory_spec(cfg, mp_cfg=None) -> str:
    """Which directory to list: multiplayer.json's, the manifest's, or the beta's.

    multiplayer.json overrides the release; the manifest lets a release ship a
    different directory without a code change.
    """
    for src in (mp_cfg, (cfg or {}).get("multiplayer")):
        spec = (src or {}).get("directory")
        if isinstance(spec, str) and spec.strip():
            return spec.strip()
    return BETA_DIRECTORY


def directory_to_list(cfg, mp_cfg=None):
    """Which directory the Galaxies page lists, or None when there is not one.

    A multiplayer.json `directory` is listed whatever else the file names. One
    that names only a `store` (the folder and LAN path) reaches no directory,
    so it makes no network call.
    """
    named = (mp_cfg or {}).get("directory")
    if isinstance(named, str) and named.strip():
        return named.strip()
    if (mp_cfg or {}).get("store"):
        return None
    return directory_spec(cfg, mp_cfg)


def open_galaxy_directory(spec: str, token=None):
    """The directory named by `spec`, or None when this build cannot open one.

    `galaxy_directory` is in server\\, which multiplayer_modules() puts on
    sys.path in a checkout. The token is passed only when `open_directory`
    accepts one.
    """
    multiplayer_modules()
    try:
        import galaxy_directory
    except ImportError:
        return None
    if token is not None and _takes_token(galaxy_directory.open_directory):
        return galaxy_directory.open_directory(spec, token=token)
    return galaxy_directory.open_directory(spec)


def install_uid(data_dir: str, mint: bool = False):
    """This install's Firebase uid, or None.

    Read from fb_identity.json, and minted only when `mint` is set.
    """
    try:
        import fb_auth
    except ImportError:
        return None
    ident = fb_auth.identity(data_dir)
    if ident.uid is None and mint:
        ident.token()
    return ident.uid


def _fb_auth():
    """The fb_auth module, or None when this build cannot reach it."""
    try:
        import fb_auth
    except ImportError:
        multiplayer_modules()           # puts fb_auth on sys.path in a checkout
        try:
            import fb_auth
        except ImportError:
            return None
    return fb_auth


def account_identity(data_dir: str):
    """This install's Firebase identity, guest or account, or None when this
    build cannot reach fb_auth."""
    fb_auth = _fb_auth()
    return fb_auth.identity(data_dir) if fb_auth is not None else None


# The Google OAuth client's secret, kept out of the repository. build.ps1
# bundles release\google_client.json when it exists, and a build without it
# offers no Google login. The file is either {"client_secret": ...} or the JSON
# Google Cloud downloads for a Desktop client.
GOOGLE_CLIENT_FILE = "google_client.json"


def google_secret():
    """The Google client secret this build carries, or None."""
    got = _read_json(bundled(GOOGLE_CLIENT_FILE)) or {}
    got = got.get("installed", got) if isinstance(got, dict) else {}
    secret = got.get("client_secret") if isinstance(got, dict) else None
    return secret if isinstance(secret, str) and secret else None


def account_email(data_dir: str):
    """The email of the account this install is logged in to, or None."""
    ident = account_identity(data_dir)
    return ident.account["email"] if ident is not None and ident.account else None


# What the account dialogs say. Without an account a player plays as the
# install's guest, which is the anonymous identity every install already has.
PRIVACY_PAGE = "cosmicresurgence.com/support_terms.php.html"
EMAIL_USE_TEXT = ("Your email address is used to log in and for account "
                  "emails. Other players never see it. More at "
                  + PRIVACY_PAGE)
LOG_IN_TEXT = ("Log in to play your galaxies from any install.\n\n"
               + EMAIL_USE_TEXT)
CREATE_TEXT = ("Create an online account.\n\n" + EMAIL_USE_TEXT)
CREATED_TEXT = ("Your account is ready, and the galaxies this install joined "
                "are now your account's.\n\nWe sent a verification email to "
                "{email}. Open the link in it before you join or play a "
                "galaxy.")
VERIFY_TITLE = "Verify your email"
VERIFY_TEXT = ("Verify your email address to join or play a galaxy.\n\nOpen "
               "the link in the email we sent to {email}, then press I have "
               "verified it.")
VERIFY_LOOP_TEXT = ("Your turn was not sent, because the email address of "
                    "this account is not verified.\n\nOpen the link in the "
                    "email we sent to {email}, then close the game and press "
                    "Play again.")
GUEST_GAMES_TEXT = ("Notify an Admin if you would like to merge your account with your email. "
                    "Send player code {code} in your message.")
DROPPED_TEXT = ("You have been logged out.")
LOG_OUT_TEXT = ("Log out?")
GOOGLE_WAIT_TEXT = ("A login window should be open in your browser now.")
GOOGLE_LINKED_TEXT = ("You are logged in with Google.")


# The launcher's record of galaxies the player joined, one per galaxy id. Each
# holds the store spec the directory gave, so a galaxy that moves is followed
# without edits. A player may hold seats in several; the one-game-per-machine
# limit is enforced where a turn loop starts.
def joined_path(data_dir: str) -> str:
    return os.path.join(data_dir, JOINED_FILE)


def joined_records(raw):
    """The records in a joined.json of either shape, keyed by galaxy id.

    A single record with `store` at the top level is read as a one-entry
    collection. A record with no store is dropped; one with no galaxy id is
    kept under its store spec. Order is the file's, which is join order.
    """
    if not isinstance(raw, dict):
        return {}
    found = raw.get(JOINED_KEY)
    if not isinstance(found, dict):
        found = {str(raw.get("galaxy") or raw.get("store") or ""): raw}
    out = {}
    for gid, rec in found.items():
        if not isinstance(rec, dict):
            continue
        if not isinstance(rec.get("store"), str) or not rec["store"]:
            continue
        # Callers match rows by the key, so the record carries it.
        out[str(gid)] = dict(rec, galaxy=str(gid))
    return out


def load_joined(data_dir: str):
    """Every galaxy this player has joined or asked to join, keyed by id."""
    return joined_records(_read_json(joined_path(data_dir)))


def write_joined(data_dir: str, records) -> None:
    """Write the whole collection, in the order it is in."""
    with open(joined_path(data_dir), "w", encoding="utf-8") as fh:
        json.dump({"version": JOINED_VERSION, JOINED_KEY: dict(records)},
                  fh, indent=2)


def save_joined(data_dir: str, record: dict) -> None:
    """Write down one galaxy, leaving every other one where it was.

    A galaxy joined again keeps its place in the file.
    """
    records = load_joined(data_dir)
    gid = str(record.get("galaxy") or record.get("store") or "")
    records[gid] = dict(record, galaxy=gid)
    write_joined(data_dir, records)


def clear_joined(data_dir: str, galaxy=None) -> None:
    """Forget one galaxy, or all of them.

    One galaxy by default. The file is removed with the last record.
    """
    if galaxy is not None:
        records = load_joined(data_dir)
        if records.pop(str(galaxy), None) is None:
            return
        if records:
            write_joined(data_dir, records)
            return
    try:
        os.remove(joined_path(data_dir))
    except OSError:
        pass


def joined_record(galaxy, name: str, uid, directory: str, now=None,
                  turn=None) -> dict:
    """What the launcher writes down when a player joins a galaxy.

    `turn` is the galaxy's turn read at the moment of joining when the caller
    has one, else the listing's (which can lag a boundary). `answer_for` uses
    it to tell this request's answer from an older one.
    """
    return {"directory": directory,
            "galaxy": galaxy.id,
            "name": name,
            "store": galaxy.store,
            "uid": uid,
            "requested_at": now if now is not None else time.time(),
            "requested_turn": turn if _whole(turn) else galaxy.turn}


def newest_joined(records):
    """The record for the galaxy joined most recently, or None.

    Used when nothing named a galaxy to play. Joins in the same second are
    settled by galaxy id, so the answer is stable.
    """
    def when(item):
        gid, rec = item
        at = rec.get("requested_at")
        ok = isinstance(at, (int, float)) and not isinstance(at, bool)
        return (at if ok else 0.0, gid)

    if not records:
        return None
    return max(records.items(), key=when)[1]


def galaxy_to_play(data_dir: str):
    """The galaxy this launcher plays, shaped like a multiplayer.json, or None.

    A store named in multiplayer.json wins. Otherwise it is the newest joined
    galaxy, with its record under `joined` so the caller knows which name the
    seat was asked for under. For a caller that named no row (Play on a row
    goes through `galaxy_config`). Answered from the records alone, with no
    store read.
    """
    cfg = multiplayer_config(data_dir)
    if cfg:
        return cfg
    rec = newest_joined(load_joined(data_dir))
    if not rec:
        return None
    out = {"store": rec["store"], "joined": rec}
    if isinstance(rec.get("auth"), bool):
        out["auth"] = rec["auth"]
    return out


# A join is a request. The worker applies it at a turn boundary, so a player
# who clicks Join during turn N plays from turn N+1.
def join_request(name: str, uid, build: str, turn=None, now=None) -> dict:
    """The request a Join writes, read by the worker at the next turn boundary.

    The uid binds the seat to the identity that claimed it. It is None for
    folder and LAN galaxies.
    """
    return {"name": name, "uid": uid, "build": build,
            "requested_at": now if now is not None else time.time(),
            "requested_turn": turn}


def send_join_request(store, req: dict) -> str:
    """Put a join request where this galaxy's worker will find it.

    A store with a `request_join` is asked. For a folder galaxy the request is
    a file beside the submissions, named by the uid, else the player's name
    (both file-name safe). Anything else raises, since a join that went
    nowhere would look like one waiting for the next turn.
    """
    ask = getattr(store, "request_join", None)
    if callable(ask):
        return str(ask(req) or "the galaxy")
    root = getattr(store, "root", None)
    if not isinstance(root, str):
        raise JoinNotAccepted(type(store).__name__)
    path = os.path.join(root, JOIN_DIR, f"{req.get('uid') or req['name']}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(req, fh, indent=2)
    os.replace(tmp, path)
    return path


def seat_claim_problem(name: str, seats, held: str = None):
    """Why this launcher cannot claim a seat called `name`, or None.

    A name already in the roster is refused here, before the turn boundary.
    The message echoes only the typed name and the seat count (see
    `roster_problem`); a seat differing only in case is refused and not
    echoed. `held` is the name this install already holds in this galaxy.
    """
    seats = [str(c) for c in (seats or [])]
    if held and name == held:
        return None
    if not any(c == name or c.lower() == name.lower() for c in seats):
        return None
    count = "one seat" if len(seats) == 1 else f"{len(seats)} seats"
    return (f"This galaxy already has a seat called {name!r}.\n\nIt has "
            f"{count}, and a seat belongs to the install that claimed it, so "
            "this one cannot take it over.\n\nChoose a different name, or if "
            "that seat is yours on another machine, ask whoever runs the "
            "galaxy to move it.")


def pending_join(g, recs) -> bool:
    """Whether this player has asked for a seat in `g` and is not in it yet.

    Between clicking Join and the worker merging the civ at the next turn
    boundary. Read from the launcher's own records, since the galaxy shows
    nothing until the merge.
    """
    return bool(recs) and g.id in recs and not g.joined


# The worker files its answer to each request under `turn_store.join_key` (the
# uid, else the name). A granted player appears in the roster; a refused one
# learns why only from the answer. An answer is read only while a request is
# pending and unanswered, then kept in the joined record.
JOIN_GRANTED, JOIN_REFUSED = "granted", "refused"


def _whole(n) -> bool:
    return isinstance(n, int) and not isinstance(n, bool)


def answer_for(rec, answer):
    """`answer` when it answers the request this record holds, else None.

    A second request is filed under the same key as an earlier answer. The
    worker stamps an answer with the turn it seats the player into, so an
    answer at or before the record's `requested_turn` belongs to an earlier
    request. Without both turns the answer is taken as it is.
    """
    if not isinstance(rec, dict) or not isinstance(answer, dict):
        return None
    if answer.get("outcome") not in (JOIN_GRANTED, JOIN_REFUSED):
        return None
    at, asked = answer.get("turn"), rec.get("requested_turn")
    if _whole(at) and _whole(asked) and at <= asked:
        return None
    return answer


def join_refusal(g, recs):
    """The refusal answering this player's request for a seat in `g`, or None."""
    if not pending_join(g, recs):
        return None
    got = recs[g.id].get("answer")
    if isinstance(got, dict) and got.get("outcome") == JOIN_REFUSED:
        return got
    return None


def awaiting_answer(g, recs) -> bool:
    """Whether the request for a seat in `g` is still with the galaxy.

    The only rows a Games refresh asks an answer for: pending, unanswered, and
    not closed.
    """
    return (pending_join(g, recs) and g.status != CLOSED
            and not isinstance(recs[g.id].get("answer"), dict))


def read_join_answers(rows, recs, ask):
    """{galaxy id: (requested_at, answer)} for the requests that have one.

    `ask(g, rec)` reads one answer, None while there is none, and is called
    only for rows `awaiting_answer` names. `requested_at` is returned so the
    record can be checked to be the same request, since the player can press
    Join again while this runs. A galaxy that cannot be read is left out.
    """
    out = {}
    for g in rows:
        if not awaiting_answer(g, recs):
            continue
        rec = recs[g.id]
        try:
            got = answer_for(rec, ask(g, rec))
        except Exception:               # a readout is not worth an error path
            continue
        if got is not None:
            out[g.id] = (rec.get("requested_at"), dict(got))
    return out


def record_join_answers(data_dir: str, answers) -> None:
    """Keep each answer in the record of the request it answers.

    A record that has changed since it was asked about, because the player
    asked again or cleared it, is left alone.
    """
    if not answers:
        return
    records = load_joined(data_dir)
    changed = False
    for gid, (asked_at, answer) in answers.items():
        rec = records.get(gid)
        if rec is None or rec.get("requested_at") != asked_at:
            continue
        if rec.get("answer") == answer:
            continue
        records[gid] = dict(rec, answer=answer)
        changed = True
    if changed:
        write_joined(data_dir, records)


def refusal_text(g, rec) -> str:
    """What a refused player reads: that they were refused, why, and what next."""
    answer = (rec or {}).get("answer") or {}
    name = answer.get("name") or (rec or {}).get("name") or "you"
    reason = str(answer.get("reason") or "").strip() or (
        "The galaxy gave no reason.")
    return (f"Your request to join {g.name or g.id} as {name} was refused."
            f"\n\n{reason}\n\nPress Ask again to send a new request, or "
            "Clear to forget this one and offer View on the row again. "
            "Nothing else has changed.")


def joinable(g, recs=None) -> bool:
    """Whether this galaxy would take a join request from this player.

    Only an open galaxy takes new players. A forming one has no first turn to
    merge a join into, and a closed one is listed without a Join control.
    """
    if g.joined or pending_join(g, recs):
        return False
    return g.status == OPEN


# A seated player who stops playing has their civ reclaimed by the referee
# (server\abandonment.py): the directory's `joined` goes false while the joined
# record still holds the granted answer. The directory row does not carry the
# `reclaimed` map, so the state is read once per galaxy for that combination
# only, and the result is kept in the joined record.
RECLAIMED_REC_KEY = "reclaimed"


def seat_reclaimed(g, recs):
    """This player's reclaim record for `g`, kept in their joined record, or
    None."""
    if not pending_join(g, recs):
        return None
    got = recs[g.id].get(RECLAIMED_REC_KEY)
    return got if isinstance(got, dict) and got else None


def reclaim_suspect(g, recs) -> bool:
    """Whether a Games refresh should ask `g`'s state if this seat was taken
    back.

    A granted request, on a row that says the player is not in, not already
    known to be reclaimed. A listing can lag the answer by one refresh, so the
    state decides and this only chooses which rows to ask.
    """
    if not pending_join(g, recs) or seat_reclaimed(g, recs):
        return False
    answer = recs[g.id].get("answer")
    return isinstance(answer, dict) and answer.get("outcome") == JOIN_GRANTED


def read_reclaims(rows, recs, read_state):
    """{galaxy id: (requested_at, record)} for seats the state says were taken
    back.

    `read_state(g)` is one read of that galaxy's state, called only for rows
    `reclaim_suspect` names. A galaxy that cannot be read is left out.
    """
    out = {}
    for g in rows:
        if not reclaim_suspect(g, recs):
            continue
        rec = recs[g.id]
        try:
            taken = reclaimed_seat(read_state(g), rec.get("name") or "")
        except Exception:               # a readout is not worth an error path
            continue
        if taken:
            out[g.id] = (rec.get("requested_at"), dict(taken))
    return out


def record_reclaims(data_dir: str, reclaims) -> None:
    """Keep each reclaim in the record of the seat it took.

    A record that has changed since it was asked about is left alone, as
    `record_join_answers` leaves one.
    """
    if not reclaims:
        return
    records = load_joined(data_dir)
    changed = False
    for gid, (asked_at, taken) in reclaims.items():
        rec = records.get(gid)
        if rec is None or rec.get("requested_at") != asked_at:
            continue
        if rec.get(RECLAIMED_REC_KEY) == taken:
            continue
        records[gid] = dict(rec, **{RECLAIMED_REC_KEY: taken})
        changed = True
    if changed:
        write_joined(data_dir, records)


def reclaimed_text(g, rec) -> str:
    """What a player whose seat was taken back reads from the row's Review."""
    name = (rec or {}).get("name") or "you"
    said = roster_problem(name, (), (rec or {}).get(RECLAIMED_REC_KEY))
    return (f"{said}\n\nPress Confirm to continue. You can join again as a "
            "new player.")


def in_galaxy(g, recs=None, own=None) -> bool:
    """Whether `g` is this player's business: seated, asked for, or named in
    multiplayer.json."""
    return bool(g.joined or pending_join(g, recs) or same_store(g.store, own))


def ended_hint(ended) -> str:
    """The line under the table when a galaxy this player is in has ended."""
    names = ", ".join(g.name or g.id for g in ended)
    return (f"{names} has ended. Press Review on its row to read why, and "
            "pick another galaxy to keep playing.")


def ended_text(g) -> str:
    """What the Review button on an ended galaxy shows.

    The operator's words come from the row's `closed_reason`, so nothing is
    read. A galaxy with no reason still gets the sentence that it has closed.
    """
    return closed_problem({STATUS_KEY: CLOSED,
                           CLOSED_REASON_KEY: getattr(g, "closed_reason",
                                                      None)})


# Turns are closed by a referee running as a scheduled task on one person's PC,
# which starts when they log in. A machine that is off, or one that rebooted
# and is sitting at a logon screen, leaves every galaxy it refs with a deadline
# that passes and a turn nothing closes.
STALL_FLOOR = 300.0      # seconds a working referee can run behind
STALL_SHARE = 4.0        # stopped once overdue by turn length / STALL_SHARE
STALL_CEILING = 3600.0   # stopped after an hour overdue, whatever the turn


def stall_after(turn_seconds) -> float:
    """How overdue a turn has to be before its galaxy counts as stopped.

    A galaxy whose state carries no usable turn length is judged by the
    ceiling, the latest this can fire.
    """
    try:
        seconds = float(turn_seconds)
    except (TypeError, ValueError):
        return STALL_CEILING
    if seconds <= 0:
        return STALL_CEILING
    return max(STALL_FLOOR, min(STALL_CEILING, seconds / STALL_SHARE))


def stalled_for(deadline, turn_seconds, status=None, now=None) -> float:
    """How long this galaxy's turns have been stopped, or 0 if they have not.

    Answered from the deadline and turn length the caller holds, with no read.
    A closed galaxy is never stopped, since its deadline stays passed by
    design. A galaxy with no deadline has no clock to be behind on.
    """
    if status == CLOSED or not deadline:
        return 0.0
    over = (time.time() if now is None else now) - deadline
    return over if over > stall_after(turn_seconds) else 0.0


def row_stalled_for(g, view=None) -> float:
    """`stalled_for` for one row of the table, whichever kind of row it is.

    A row may not carry `turn_seconds`; a missing length uses the ceiling.
    """
    if view is not None and view.listed is not None:
        # A listing older than the floor (held over a sleep, a minimised
        # window or a stalled refresh) cannot show that the referee is behind.
        if view.clock - view.listed > STALL_FLOOR:
            return 0.0
    return stalled_for(g.deadline, getattr(g, "turn_seconds", None), g.status,
                       None if view is None else view.clock)


def stall_hint(stalled) -> str:
    """The line under the table when a galaxy's turns have stopped.

    One fixed sentence; the table marks which galaxies are stopped. `stalled`
    is accepted and not read.
    """
    return "Some galaxies are stopped on the server, check back later"


# The Galaxies table: one row per galaxy, sorted by the column the player
# clicked. A column orders by its value and prints its text, and the two differ
# (turn 9 sorts below turn 11), so the table never sorts on the cell.
PLAY = "play"
VIEW = "view"
REASON = "reason"
# Rows that only explain: an ended galaxy this player is in, and a seat the
# galaxy took back. Both read "Review" on the button.
ENDED = "ended"
RECLAIMED = "reclaimed"
ACTION_TEXT = {PLAY: "Play", VIEW: "View", REASON: "Review",
               ENDED: "Review", RECLAIMED: "Review"}

# The status column orders by how far along a galaxy is, not alphabetically.
STATUS_ORDER = (OPEN, FORMING, CLOSED)


class View(typing.NamedTuple):
    """What a cell needs to know that its own row does not carry.

    `recs`: this launcher's joined records, keyed by galaxy id.
    `submitted`: keyed the same way, asked only of galaxies the player is
    seated in; a missing entry means not asked. `own`: the store
    multiplayer.json names, played whatever the listing says. `listed`: when
    the rows were read, or None; only a recent listing can call a galaxy
    stopped.
    """
    now: "float | None" = None
    recs: "dict | None" = None
    submitted: "dict | None" = None
    own: "str | None" = None
    listed: "float | None" = None

    @property
    def clock(self) -> float:
        """The moment the countdowns are measured from."""
        return time.time() if self.now is None else self.now


class Column(typing.NamedTuple):
    """One column: what it sorts on, and what it prints.

    `value` answers None for a galaxy the column cannot speak for (a forming
    galaxy has no turn, a closed one has no countdown).
    """
    key: str
    heading: str
    value: typing.Callable      # (galaxy, view) -> the sort value, or None
    text: typing.Callable       # (galaxy, view) -> the cell


def _name_value(g, view):
    # Case-folded, so lower-case names sort among capitalised ones.
    return (g.name or g.id or "").lower()


def _name_text(g, view):
    return g.name or g.id or ""


def _status_value(g, view):
    return (STATUS_ORDER.index(g.status) if g.status in STATUS_ORDER
            else len(STATUS_ORDER))


def _status_text(g, view):
    """The galaxy's own word, or "stopped" when its turns have stopped. The
    column still sorts on the stored word.
    """
    if row_stalled_for(g, view):
        return "stopped"
    return g.status or ""


def _turn_value(g, view):
    return g.turn


def _turn_text(g, view):
    return "" if g.turn is None else str(g.turn)


def _left_value(g, view):
    """Seconds until this galaxy's deadline, or None when it has not got one.

    A closed galaxy has none: its deadline is frozen.
    """
    if g.status == CLOSED or not g.deadline:
        return None
    return g.deadline - view.clock


def _left_text(g, view):
    left = _left_value(g, view)
    if left is None:
        return ""
    return f"{fmt_left(left)} left" if left > 0 else "time is up"


def _players_value(g, view):
    return g.players or 0


def _players_text(g, view):
    return str(g.players or 0)


def _you_value(g, view):
    """How far into this galaxy the player is, nearest first."""
    if g.joined:
        return 0
    if pending_join(g, view.recs):
        return 1
    return 2


def _you_text(g, view):
    if g.joined:
        if g.status == CLOSED:
            return "galaxy ended"
        played = (view.submitted or {}).get(g.id)
        if played is True:
            return "turn played"
        if played is False:
            return "your turn"
        return "you are in"
    if join_refusal(g, view.recs):
        return "join refused"
    if seat_reclaimed(g, view.recs):
        return "seat reclaimed"
    if pending_join(g, view.recs):
        # A request lodged before the galaxy ended is never going to land.
        return "galaxy ended" if g.status == CLOSED else "joining next turn"
    return ""


GALAXY_COLUMNS = (
    Column("name", "Galaxy", _name_value, _name_text),
    Column("status", "Status", _status_value, _status_text),
    Column("turn", "Turn", _turn_value, _turn_text),
    Column("left", "Next turn", _left_value, _left_text),
    Column("players", "Players", _players_value, _players_text),
    Column("you", "You", _you_value, _you_text),
)

DEFAULT_SORT = "name"


def galaxy_column(key: str):
    """The column called `key`, or None."""
    return next((c for c in GALAXY_COLUMNS if c.key == key), None)


GALAXY_LEFT = galaxy_column("left")
# The cells whose text depends on the clock, rewritten between listings.
GALAXY_STATUS = galaxy_column("status")
LIVE_COLUMNS = (GALAXY_STATUS, GALAXY_LEFT)


def sort_galaxies(rows, key: str = DEFAULT_SORT, reverse: bool = False,
                  view: "View | None" = None):
    """`rows` in the order one column asks for.

    Ordered on the column's value, never its text. Rows the column cannot
    speak for stay at the bottom in both directions. The sort is stable.
    """
    col = galaxy_column(key)
    if col is None:
        return list(rows)
    view = view or View()
    known, unknown = [], []
    for g in rows:
        (unknown if col.value(g, view) is None else known).append(g)
    known.sort(key=lambda g: col.value(g, view), reverse=bool(reverse))
    return known + unknown


def same_store(a, b) -> bool:
    """Whether two store specs name the same galaxy.

    Compared as written, then as local paths, since a typed multiplayer.json
    and a tool-written index can spell the same folder differently.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    try:
        return (os.path.normcase(os.path.abspath(a))
                == os.path.normcase(os.path.abspath(b)))
    except (TypeError, ValueError):
        return False


def row_action(g, recs=None, own=None):
    """Which control this row offers: PLAY, VIEW, a Review kind, or None.

    Play is any galaxy you hold a seat in, and `own` (the store
    multiplayer.json names) whatever its roster says; start_multiplayer checks
    whether a turn can be taken. View is a galaxy you could join and opens the
    notice where joining is confirmed. A refused request (REASON) and a
    reclaimed seat (RECLAIMED) offer Review, closed or not. A closed galaxy
    offers ENDED if this player is in it, asked for it, or named it, else
    nothing. A pending request offers nothing.
    """
    if join_refusal(g, recs):
        return REASON
    if seat_reclaimed(g, recs):
        return RECLAIMED
    if g.status == CLOSED:
        return ENDED if in_galaxy(g, recs, own) else None
    if same_store(g.store, own):
        return PLAY
    if g.joined:
        return PLAY
    if pending_join(g, recs):
        return None
    return VIEW if joinable(g, recs) else None


class OwnGalaxy(typing.NamedTuple):
    """A row for the galaxy no directory lists.

    The same fields a directory's row carries, declared here so a build frozen
    without `galaxy_directory` can still show this row.
    """
    id: str
    name: str
    status: str
    turn: "int | None"
    deadline: "float | None"
    players: int
    joined: bool
    store: str
    turn_seconds: "int | None" = None
    closed_reason: "str | None" = None


def store_label(spec: str) -> str:
    """What to call a galaxy that is only a store spec.

    The last part of it: the folder or host, since such a store has no other
    name.
    """
    spec = str(spec)
    return os.path.basename(spec.rstrip("/\\").replace("\\", "/")) or spec


def named_galaxy(spec: str, state, player: "str | None" = None) -> OwnGalaxy:
    """The row for a store multiplayer.json names, built from its own state.

    Identified by the spec and shown under `store_label`. A store whose state
    cannot be read is forming, since the referee publishes the first turn some
    time after the folder exists.
    """
    spec = str(spec)
    name = store_label(spec)
    if not state:
        return OwnGalaxy(spec, name, FORMING, None, None, 0, False, spec)
    civs = list(state.get("civs", []))
    status = state.get(STATUS_KEY) or OPEN
    # Dropped from a galaxy that is not closed, as the directory's row does.
    reason = state.get(CLOSED_REASON_KEY) if status == CLOSED else None
    return OwnGalaxy(spec, name, status,
                     state.get("turn"), state.get("deadline"), len(civs),
                     bool(player) and player in civs, spec,
                     state.get("turn_seconds"), reason)


def own_row(data_dir: str, cfg, player: "str | None" = None) -> OwnGalaxy:
    """Read the galaxy multiplayer.json names and turn it into a row.

    Opened as playing opens it (`store_wants_token`), so a folder or LAN
    referee needs no sign-in. An unreachable store still gives a row.
    """
    spec = cfg["store"]
    mods = multiplayer_modules()
    if mods is None:
        return named_galaxy(spec, None, player)
    _player_turn, turn_store = mods
    try:
        token = player_token(data_dir) if store_wants_token(cfg) else None
        store = open_player_store(turn_store, spec, token)
        state = store.state() if store.exists() else None
    except Exception:           # any store; unreachable is the usual one
        state = None
    return named_galaxy(spec, state, player)


def galaxy_rows(data_dir: str, spec, player: "str | None" = None):
    """(rows, problem) for the Galaxies page: the directory's, and your own.

    The galaxy multiplayer.json names goes first, whether or not the directory
    answered, and an unreachable directory is reported beside the rows.
    Called on a worker thread.
    """
    rows, problem = [], None
    if spec:
        try:
            # Asked as a store is, so the `auth` key governs the listing too:
            # an https directory (the relay) wants this install's identity, a
            # folder does not.
            want = dict(multiplayer_file(data_dir) or {})
            want["store"] = spec
            token = player_token(data_dir) if store_wants_token(want) else None
            directory = open_galaxy_directory(spec, token=token)
            if directory is None:
                problem = ("This build cannot list galaxies: the directory it "
                           "would read is not in it.")
            else:
                rows = list(directory.galaxies(player=player))
        except Exception as exc:    # any directory; unreachable is usual
            problem = f"Could not reach the galaxy directory: {exc}"
    cfg = multiplayer_config(data_dir)
    # Unless the directory already listed it. `row_action` matches that row by
    # its store, so it is still played from the store multiplayer.json names.
    if cfg and not any(same_store(g.store, cfg["store"]) for g in rows):
        rows.insert(0, own_row(data_dir, cfg, player))
    return rows, problem


# server\beta_notice.py holds the notice a player reads before joining, in a
# .txt beside it. The launcher shows it on the join step, ahead of the Join
# control, and fills its log-copy marker. A notice that is missing or has an
# unfilled marker is refused, not shown short.

# What the notice says about the log copy: write_redacted_log writes it beside
# launcher.log, and it is uploaded only when the player presses Send. This
# clause must change if anything is ever sent without that.
LOG_UPLOAD_CLAUSE = ("Nothing is sent by itself. The launcher writes that "
                     "copy to a file on your own machine, and it reaches us "
                     "only when you press Send, on the launcher's send log "
                     "link or when it offers after a turn goes wrong.")


def beta_notice_module():
    """server\\beta_notice, or None when this build cannot reach it.

    Found the way `galaxy_directory` is. A build frozen without it refuses a
    join with a reason.
    """
    multiplayer_modules()
    try:
        import beta_notice
    except ImportError:
        return None
    return beta_notice


def beta_notice_text(state):
    """(notice, None) as this galaxy's player must read it, or (None, reason).

    `state` is the galaxy being joined, for any per-galaxy value the notice
    states. A missing module, a missing file and an unfilled marker each
    refuse with their own reason, which is meant to be shown.
    """
    notice = beta_notice_module()
    if notice is None:
        return None, ("this build does not carry beta_notice, so what a "
                      "player agrees to is not in it")
    body, why = notice.text_or_reason()
    if body is None:
        return None, why
    body = notice.fill({"log_upload": LOG_UPLOAD_CLAUSE}, body)
    missing = notice.unfilled(body)
    if missing:
        return None, ("the notice still has nothing to say for "
                      + ", ".join(missing))
    return body, None


# launcher.log carries the stub server's logging, including save request
# bodies: a base64 blob in the `data=` field, in 400-character chunks tagged
# `body+`, up to 4000 characters per request. redact_log_text() makes the copy
# that can be sent: body dumps replaced by a byte count, the Windows account
# name taken out of paths, email addresses taken out, and the tail kept to a
# cap.
#
# The redacted copy still contains (the send prompt is written from this list):
#   - The player's name, on every multiplayer line: "[Alice] turn 12: submitted".
#   - The galaxy's store path or URL as multiplayer.json names it. A UNC path
#     names the referee's machine.
#   - Each save request's game name, turn and action, from the kept head of the
#     body: "gamename='DemoPlayt8'&turn=8&version=1", plus an always-empty
#     `passhash`.
#   - Which modes started and exited, with exit codes, process ids and the game
#     executables' names.
#   - The install's paths with the account name replaced:
#     "C:\Users\<user>\Desktop\...". The rest of each path survives.
#   - The AI opponent's full reasoning in single player (planets, ships,
#     research and engine addresses).
#   - Timestamps, to the second.
LOG_NAME = "launcher.log"
REDACTED_LOG_NAME = "launcher-redacted.log"

LOG_UPLOAD_CAP = 256 * 1024   # bytes in a copy meant to be sent

_CAP_NOTE = 200   # bytes held back for the line saying what the cap dropped

# A line as launcher.log writes it: the stub server's own indented text with
# the launcher's timestamp in front of it.
_STAMP = r"(?:\[\d\d:\d\d:\d\d\]\s*)?\s*"

# The continuation chunks of a request body, and the line that closes a
# truncated one. cs_server tags chunk 0 `body` and every chunk after it `body+`.
_BODY_MORE = re.compile(_STAMP + r"body(?:\+|\u2026)\s*[:(]")

# Chunk 0, which is worth keeping as far as `data=`: ahead of that field it
# carries the user, the game, the turn and the version. After it is the blob.
_BODY_HEAD = re.compile("(" + _STAMP + r"body:\s*.*?\bdata=)(.+)$")

# Any \Users\<name>\ in a path. Public and Default are Windows' own shared
# profiles and name nobody.
_USER_DIR = re.compile(r"([A-Za-z]:[\\/]+Users[\\/]+)(?!Public\b|Default\b)"
                       r"([^\\/\s\"']+)", re.I)
USER_MARK = "<user>"

# An account's email, which the launcher logs at login. The operator finds an
# account by its uid instead.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
EMAIL_MARK = "<email>"


def scrub_paths(text: str) -> str:
    """Replace the Windows account name in any path with <user>.

    Matches any account name, since a log can carry paths from other profiles.
    A profile directory not under \\Users (a redirected domain profile, say)
    is matched by its own literal path.
    """
    text = _USER_DIR.sub(lambda m: m.group(1) + USER_MARK, text)
    home = os.path.expanduser("~")
    if home and _USER_DIR.match(home) is None:
        parent, leaf = os.path.split(home)
        if parent and leaf:
            masked = os.path.join(parent, USER_MARK)
            text = re.sub(re.escape(home), lambda m: masked, text, flags=re.I)
    return text


def scrub(text: str) -> str:
    """`scrub_paths`, and every email address replaced with <email>."""
    return _EMAIL.sub(EMAIL_MARK, scrub_paths(text))


def _keep_tail(text: str, cap: int) -> str:
    """The last `cap` bytes of a log, cut at a line boundary and labelled."""
    data = text.encode("utf-8", "replace")
    if len(data) <= cap:
        return text
    kept = data[-max(0, cap - _CAP_NOTE):]
    cut = kept.find(b"\n")
    if cut >= 0:
        kept = kept[cut + 1:]
    return (f"[{len(data) - len(kept)} bytes of older log dropped, keeping the "
            f"most recent {len(kept)}]\n") + kept.decode("utf-8", "replace")


def redact_log_text(text: str, cap: int = LOG_UPLOAD_CAP) -> str:
    """An uploadable copy of a launcher log.

    Each run of body lines becomes one line giving its byte count, which tells
    a turn that sent a save from one that sent nothing. The cap keeps the
    tail, with a line at the top saying what was dropped.
    """
    out = []
    elided = 0

    def close_run():
        nonlocal elided
        if elided:
            out.append(f"  [{elided} bytes of request body elided]")
            elided = 0

    for line in text.splitlines():
        head = _BODY_HEAD.match(line)
        if head is not None:
            close_run()
            out.append(scrub(head.group(1)) + "\u2026")
            elided += len(head.group(2).encode("utf-8", "replace"))
            continue
        if _BODY_MORE.match(line) is not None:
            elided += len(line.encode("utf-8", "replace"))
            continue
        close_run()
        out.append(scrub(line))
    close_run()

    body = "\n".join(out)
    if body:
        body += "\n"
    return _keep_tail(body, cap)


def redacted_log(data_dir: str, cap: int = LOG_UPLOAD_CAP) -> str:
    """The uploadable copy of this install's launcher.log.

    A missing or unreadable log gives a copy that says so.
    """
    path = os.path.join(data_dir, LOG_NAME)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        return scrub_paths(f"[no launcher log to send: {exc}]") + "\n"
    return redact_log_text(text, cap)


def write_redacted_log(data_dir: str, out_path: "str | None" = None,
                       cap: int = LOG_UPLOAD_CAP) -> str:
    """Write the uploadable copy beside the log and return where it went.

    The file is what the player can read before sending and what Send uploads.
    """
    out_path = out_path or os.path.join(data_dir, REDACTED_LOG_NAME)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(redacted_log(data_dir, cap))
    return out_path


# The redacted copy goes to the relay's `POST /_logs`, read by the operator
# with server\dev_tools\log_tool.py and served to no player. It is sent only
# when the player presses Send: on the "send log" link, or in the box offered
# once when a followed turn could not be sent or the turn loop stopped with an
# error. The relay takes the text zlib-compressed and base64 in a small JSON
# object, limits uploads per sign-in and per day, and refuses with a sentence
# that is shown as it is.
LOG_ROUTE = "_logs"
LOG_WHY_TURN = "turn_failed"
LOG_WHY_PLAYER = "player"

# The loop kinds after which the box is offered. A closed galaxy sets
# `mp_closed` first and is not offered the box.
LOG_OFFER_KINDS = ("lost", "failed", "serve_failed")

LOG_SEND_TITLE = "Send your log"
LOG_TURN_LEAD = ("Something went wrong with your turn. The launcher's log is "
                 "what the galaxy's operator needs to see what happened.\n\n")
LOG_SEND_TEXT = (
    "Send the launcher's log to the galaxy's operator?\n\n"
    "It is the launcher's record of what it did: your player name, the "
    "galaxy, turn numbers, which parts of the game started and stopped, and "
    "any errors. Your Windows account name is taken out of it first, and the "
    "saved-game data in it is removed. Nothing else on this computer is "
    "sent.\n\n"
    "Nothing is sent unless you press Send.")


def log_upload_base(data_dir: str, store_spec, directory) -> "str | None":
    """The relay a log goes to, or None when this install reaches none.

    The followed galaxy's relay first, then the directory's. A spec counts
    when it is http(s) and `store_wants_token` says it wants an identity, so a
    folder or LAN referee is never sent a log.
    """
    want = dict(multiplayer_file(data_dir) or {})
    for spec, is_store in ((store_spec, True), (directory, False)):
        if not isinstance(spec, str):
            continue
        spec = spec.strip().rstrip("/")
        if not spec.startswith(("http://", "https://")):
            continue
        want["store"] = spec
        if not store_wants_token(want):
            continue
        if is_store:
            spec = spec.rsplit("/", 1)[0]
        return spec
    return None


def log_payload(text: str, build, why: str, galaxy=None, civ=None,
                turn=None) -> bytes:
    """The body of a log upload."""
    import base64
    import zlib
    packed = base64.b64encode(zlib.compress(text.encode("utf-8"), 6))
    return json.dumps({
        "log": packed.decode("ascii"),
        "build": build,
        "why": why,
        "galaxy": galaxy,
        "civ": civ,
        "turn": turn if _whole(turn) else None,
    }).encode("utf-8")


def send_log(base: str, token, payload: bytes, timeout: float = 30.0) -> dict:
    """Upload one log. The relay's reply, or the HTTPError it refused with.

    `token` is a callable answering an ID token, or a token, or None.
    """
    import urllib.request
    req = urllib.request.Request(f"{base.rstrip('/')}/{LOG_ROUTE}",
                                 data=payload, method="POST",
                                 headers={"Content-Type": "application/json"})
    said = token() if callable(token) else token
    if said:
        req.add_header("Authorization", f"Bearer {said}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def log_sent_text(reply, code=None) -> str:
    """What a player reads once their log is sent.

    The reference is shown in a copyable row under this text. `code` is this
    install's player code, which the operator finds logs by
    (`log_tool list --code`).
    """
    ref = (reply or {}).get("id")
    said = "Your log was sent to the galaxy's operator."
    if ref:
        said += ("\n\nIf you write to them about it, quote this log reference. "
                 f"The last {LOG_REFS_KEEP} are kept under the log history "
                 "link, beside send log.")
    if code:
        said += ("\n\nIf you lose the log reference, send them your "
                 "player code "
                 f"instead and they can find your logs by it:\n\n    {code}"
                 "\n\nThe same code is shown under the launcher's title.")
    else:
        said += ("\n\nIf you lose the log reference, send them the "
                 "player code "
                 "shown under the launcher's title instead and they can find "
                 "your logs by it.")
    return said


# The relay answers each upload with a reference; the last few are kept for
# the log history link. Newest first; a missing or unreadable file is an empty
# list and is rewritten whole by the next send.
LOG_REFS_FILE = "log_refs.json"
LOG_REFS_KEY = "sent"
LOG_REFS_KEEP = 5
LOG_REFS_TITLE = "Log history"
LOG_COPY = "Copy"
LOG_COPIED = "Copied"


def log_refs_path(data_dir: str) -> str:
    return os.path.join(data_dir, LOG_REFS_FILE)


def load_log_refs(data_dir: str) -> list:
    """The references kept, newest first, as {"id", "sent"} records.

    A record without a string id is skipped; one without a string send time
    keeps its id with an empty time.
    """
    raw = _read_json(log_refs_path(data_dir)) or {}
    found = raw.get(LOG_REFS_KEY)
    if not isinstance(found, list):
        return []
    out = []
    for rec in found:
        if not isinstance(rec, dict):
            continue
        ref = rec.get("id")
        if not isinstance(ref, str) or not ref.strip():
            continue
        sent = rec.get("sent")
        out.append({"id": ref.strip(),
                    "sent": sent if isinstance(sent, str) else ""})
    return out[:LOG_REFS_KEEP]


def record_log_ref(data_dir: str, ref: str, when: float = None) -> list:
    """Keep one reference at the front of the list and answer the list.

    A reference already kept moves to the front rather than appearing twice.
    `when` is a Unix time, now if not given, written as UTC.
    """
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                          time.gmtime(time.time() if when is None else when))
    refs = [r for r in load_log_refs(data_dir) if r["id"] != ref]
    refs = ([{"id": ref, "sent": stamp}] + refs)[:LOG_REFS_KEEP]
    with open(log_refs_path(data_dir), "w", encoding="utf-8") as fh:
        json.dump({LOG_REFS_KEY: refs}, fh, indent=2)
    return refs


def log_ref_when(sent: str) -> str:
    """A kept send time as the player's local date and time, or ""."""
    try:
        import calendar
        secs = calendar.timegm(time.strptime(sent, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError, OverflowError):
        return ""
    return time.strftime("%d %b %Y %H:%M", time.localtime(secs))


def log_refs_text(code=None) -> str:
    """The body of the box the log history link opens."""
    said = (f"The log references of the last {LOG_REFS_KEEP} logs this "
            "launcher sent, newest first. Quote one when you write to the galaxy's "
            "operator about it.")
    if code:
        said += (f"\n\nIf the one you want is not here, send them your "
                 f"player code instead and they can find your logs by it:"
                 f"\n\n    {code}")
    return said


def log_not_sent_text(why, path) -> str:
    """What a player reads when their log could not be sent, or had nowhere
    to go. `why` is None for a launcher that reaches no relay."""
    if why is None:
        said = ("This launcher is not connected to the beta, so there is "
                "nowhere to send the log.")
    else:
        said = f"Your log could not be sent: {why}"
    return (f"{said}\n\nThe same copy is saved at\n\n{path}\n\nand you can "
            "attach it to a bug report instead.")


# What a piece of text means, as a colour: ordinary, secondary, faint, a
# heading, good, needs attention, bad. The window maps each to the colour that
# reads on whatever it is drawn over (web_view.ON_DARK and ON_LIGHT).
TEXT    = "#a0c8ff"
DIM     = "#6090b0"
FAINT   = "#405070"
ACCENT  = "#00aaff"
OK      = "#3ddc84"
WARN    = "#ffb020"
BAD     = "#ff5555"

# The game controls under a running game, by key and label, in reading order.
CONTROLS = (("load", "Load"), ("save", "Save"), ("turn", "Next Turn"))

# What the table says while the first listing is in flight.
GALAXIES_WAITING = "Please wait, loading galaxies…"


class GalaxyRow(typing.NamedTuple):
    """One row of the galaxy table as the window draws it.

    `cells` is (text, colour) per column of GALAXY_COLUMNS. `action` is the
    label of the row's one button, or None for a row without one, and
    `on_action` is what pressing it does.
    """
    cells: list
    action: "str | None" = None
    on_action: "typing.Callable | None" = None

# The capture indicator: what the turn loop last did with the player's work,
# shown beside the server dot.
CAPTURING = "capturing"     # the client is being asked for the player's state
HELD = "held"               # captured here, not in the store yet
SENT = "sent"               # the store holds everything captured
CAPTURE_FAILED = "failed"   # the last capture did not come back

# Seconds the indicator stays up after the game closes and the last send
# finishes, so "turn sent" can be read.
CAPTURE_HOLD = 10.0


def capture_readout(state, age: float, client_open: bool = True):
    """(text, colour) for the capture indicator, or ("", colour) to hide it.

    `age` is how long ago the state was recorded, shown for held and sent. The
    colour answers "is my work safe": while the game is open a held capture is
    green, since the upload is not due yet; once the game is gone only sent is
    green.
    """
    if state == CAPTURE_FAILED:
        return "could not save your turn", BAD
    if client_open:
        if state == CAPTURING:
            return "saving your turn…", WARN
        if state == HELD:
            return f"saved {fmt_ago(age)}", OK
        if state == SENT:
            return f"turn sent {fmt_ago(age)}", OK
    else:
        if state == SENT:
            return f"turn sent {fmt_ago(age)}", OK
        if state in (HELD, CAPTURING):
            return "sending your turn…", WARN
    return "", FAINT


class Launcher:
    """Everything the launcher decides, drawn by a view.

    `view` is the window (web_view.WebView). The Launcher calls its methods to
    show things and to ask the player things, and the view calls the
    Launcher's methods when the player presses something. Nothing here touches
    a widget, so every decision can be exercised without a window.
    """

    def __init__(self, view, cfg):
        self.view = view
        self.cfg = cfg
        self.modes = [m for m in cfg["modes"] if m.get("show", True)]
        self.msgs: "queue.Queue[str]" = queue.Queue()
        self.servers: "list" = []
        self.child: "subprocess.Popen | None" = None
        # The opponent, tied to self.child's lifetime: started after the game
        # and killed when it is gone, so it never attaches to a reused pid.
        self.ai_child: "subprocess.Popen | None" = None
        # A cached handle on the running game, for the once-a-second turn
        # readout. Dropped whenever a read fails or the game exits.
        self._ctl_client = None
        # When the turn showing now first appeared, so a stuck opponent can be
        # timed out.
        self._waiting_since = None
        self._last_seen_turn = None
        self._ctl_busy = False
        # The game whose turn clock the log has already said is held, so the
        # hold is announced once per game, not at every boundary.
        self._held_pid = None
        self._save_name = "singleplayer"   # offered again by the next Save
        self.running_mode = None
        # The multiplayer session: the turn loop's worker thread, the store it
        # follows, and a flag asking it to stop between turns.
        self.mp_thread = None
        self.mp_store = None
        self.mp_civ = None
        # Which galaxy that loop follows (a name and store spec, which outlive
        # the row), for the refusal a second Play gets.
        self.mp_playing = None
        self.mp_stop = False
        self.mp_note = ""
        self.mp_turn = None
        # When this turn closes, as an absolute time. The loop reports time
        # left with every `playing`, so the readout subtracts from this and
        # does not ask the store.
        self.mp_deadline = None
        # This galaxy's turn length, read once in start_multiplayer: the scale
        # `stalled_for` judges a passed deadline against.
        self.mp_turn_seconds = None
        # Whether the loop is between turns. Only then does a stopped galaxy
        # outrank the readout's other notes.
        self.mp_waiting = False
        # Since when the loop has been unable to read the galaxy's store, or
        # None while it can. An unreadable store freezes the deadline the same
        # way a referee that is off does.
        self.mp_unreachable = None
        # What the loop last did with the player's state, and when: a capture
        # state with a timestamp, or None. Recorded by _mp_state on the worker
        # thread and painted by _refresh_turn on the UI thread.
        self.mp_capture = None
        # Set by Save during a multiplayer turn, cleared by the loop when it
        # acts on it; the loop is already driving the client.
        self.mp_send_now = threading.Event()
        # Set by Play on the followed galaxy once its game window has closed,
        # cleared by the loop when it reopens the game. The loop owns the
        # client (game_cycle's lock forbids a second). `mp_reopening` is what
        # the UI says until the client is back up.
        self.mp_reopen = threading.Event()
        self.mp_reopening = False
        # Whether the galaxy being followed has been found closed, and the
        # deadline a stall last sent a probe for. See `_mp_probe_closed`.
        self.mp_closed = False
        self.mp_probed = None
        # Whether this galaxy's player has been offered to send their log
        # after a turn went wrong, and whether a log is on its way.
        self.mp_log_offered = False
        self.mp_seat_refused = False
        self.log_sending = False
        # Whether the client was up at the watcher's last pass, so its closing
        # clears the turn readout at once, and whether its turn is still being
        # sent.
        self.mp_client_seen = False
        self.mp_client_gone = False
        self.mp_sending = False
        self.mp_turn_open = False
        # The Galaxies page: the directory it lists (None on the folder and
        # LAN path), the store multiplayer.json names, and whether a listing is
        # in flight.
        self.games_dir = None
        self.own_store = None
        self.games_busy = False
        # The last listing, kept so sorting repaints without a new query. None
        # until one has been asked for.
        self.games_rows = None
        self.games_listed = None
        self.games_extra = {}
        self.games_err = None
        self.games_recs = {}
        self.sort_key = DEFAULT_SORT
        self.sort_desc = False
        # Which page the window is showing, and the table's countdown cells,
        # which are rewritten once a second without disturbing the order, as
        # (row, column, Column, galaxy, near).
        self.page = "home"
        self._clocks: "list" = []
        # This player's name, read at boot. None until entered; only
        # multiplayer needs it.
        self.player = None
        # The status to fall back to when no game is running, recorded when the
        # server settles.
        self._ready: "tuple[str, str]" = ("starting…", WARN)
        # What the status line says now, which is what the window starts with.
        self._status: "tuple[str, str]" = self._ready
        # False while a foreign server holds the port. Multiplayer cannot run
        # then: the turn would be captured into that server's data directory.
        self.server_ok = False
        # Set when a server this launcher did not start is being shared.
        self.save_dir = None
        self.game_root = self.galaxy_root = self.data_dir = None
        # Every client the manifest knows about, including hidden modes, since
        # a second launch would kill any running one.
        self.client_exes = {m["exe"] for m in cfg["modes"] if m.get("exe")}
        # The turn loop's own clients also count as a running game.
        self.client_exes |= set(MP_CLIENTS)
        # Log lines buffer until the data directory is known, so a failed
        # startup still records where the game was looked for.
        self._logfh = None
        self._pending: "list[str]" = []

        view.build(self)
        self._boot()
        view.after(120, self._drain)
        view.after(1000, self._watch_game)

    def _boot(self):
        self.data_dir = find_data_dir()
        self._open_log(self.data_dir)
        info = build_info()
        self.say(f"{self.cfg['product']} v{info['build']}")
        # When and from what the build was packaged. Absent from a checkout and
        # from an unstamped package.
        made = " ".join(f"{k} {info[k]}" for k in ("stamped_at", "commit")
                        if info.get(k))
        if made:
            self.say(f"build   {made}")
        self.say(f"data    {self.data_dir}")

        self.player = player_name(self.data_dir)
        self._show_identity()
        self._show_log_refs_link()
        self.say(f"player  {self.player or 'not set yet'}")

        # Set up the Galaxies page before looking for game files, so an install
        # that cannot start a game can still list galaxies. Nothing is listed
        # until the page is opened.
        mp_cfg = multiplayer_file(self.data_dir)
        self.own_store = (multiplayer_config(self.data_dir) or {}).get("store")
        self.games_dir = directory_to_list(self.cfg, mp_cfg)
        if self.own_store:
            self.say(f"games   {MP_CONFIG} names {self.own_store}")
        self.say(f"games   {self.games_dir or 'no directory to list'}")
        self._games_tick()

        icon = find_icon(self.data_dir)
        if icon:
            try:
                self.view.set_icon(icon)
                self.say(f"icon    {icon}")
            except Exception as exc:
                self.say(f"icon    not applied ({exc})")
        else:
            self.say("icon    none found")

        # Only the modes offered gate startup; a hidden mode's missing EXE is a
        # stale manifest row.
        game_root = find_game_root(self.modes)
        if not game_root:
            searched = "\n".join("    " + c for c in game_root_candidates())
            needed = ", ".join(sorted({m["exe"] for m in self.modes
                                       if is_playable(m) and m.get("exe")}))
            for line in ("game files not found; searched:", searched):
                self.say(line)
            self.fail(
                "Game files not found.\n\n"
                "This launcher needs a 'game' folder beside it containing:\n"
                f"    {needed}\n\n"
                "Looked in:\n" + searched + "\n\n"
                "If you are running the launcher out of a build folder, run the "
                "one in dist\\ instead; that is the complete release. If you "
                "unzipped only the launcher, download the full archive again and "
                "keep the folder together.")
            return
        galaxy_root = find_galaxy_root(game_root, self.modes)
        if not galaxy_root:
            self.fail("Galaxy files not found.\n\nExpected .csgalaxy files in "
                      f"{os.path.join(game_root, 'galaxies')}.")
            return

        self.game_root = game_root
        self.galaxy_root = galaxy_root
        self.say(f"game    {game_root}")
        self.say(f"galaxy  {galaxy_root}")

        host = self.cfg["server"]["host"]
        port = int(self.cfg["server"]["port"])

        if not port_is_free(host, port):
            # Another server's captures land in its own data directory, which
            # the protocol cannot report, so it is shared only when configured.
            ours = stub_server_answers(host, port)
            adopt = adopted_server(self.data_dir)
            if ours and adopt and os.path.isdir(adopt):
                # The operator has said where this server writes.
                self.save_dir = adopt
                self.server_ok = True
                self.set_ready_status(
                    f"sharing the server on {host}:{port}", OK)
                self.say(f"port {port} is held by a server this launcher did "
                         f"not start; sharing it as multiplayer.json asks")
                self.say(f"captures are collected from {adopt}")
                return
            if ours and adopt:
                # A configured directory that is not there is refused.
                self.say(f"adopt_server names {adopt}, which is not a "
                         f"directory; not sharing the server")
            # Worded by what holds the port. The holder is read, never ended.
            holder = port_holder(port_holder_chain(port),
                                 install_server_scripts())
            shared = referee_share(holder, ours, configured=adopt)
            if shared:
                # This install's referee holds the port and its worker's
                # command line says where it writes. Its server is used as it
                # is and left running.
                self.save_dir = shared
                self.server_ok = True
                self.set_ready_status(
                    "sharing the referee's server on this PC; do not close "
                    "the referee", OK)
                self.say(f"port {port} is held by pid {holder.get('pid')}, "
                         f"the cs_server of this install's referee worker "
                         f"(pid {holder.get('worker')}); sharing it")
                self.say(f"captures are collected from {shared}")
                return
            status, line, box = port_held_message(port, holder, ours,
                                                  self.data_dir)
            self.set_ready_status(status, BAD)
            self.say(line)
            self.warn(box)
            return

        try:
            self.servers, logfile = start_server(
                host, port, self.data_dir, self.galaxy_root, self.say_threadsafe)
        except Exception as exc:
            self.set_ready_status(f"server failed to start: {exc}", BAD)
            self.say("server failed: " + "".join(
                traceback.format_exception_only(type(exc), exc)).strip())
            return
        self.server_ok = True
        self.set_ready_status("ready", OK)
        listening = ", ".join(
            f"{s.server_address[0]}:{s.server_address[1]}" for s in self.servers)
        self.say(f"server listening on {listening}")
        self.say(f"log     {logfile}")

    def on_play(self, mode, fresh=False):
        """Start a mode. A mode that `continues` starts on its newest save
        unless `fresh`, which is New Game."""
        if not is_playable(mode):
            return
        if mode.get("id") == "multiplayer":
            # Navigation, not a launch: the galaxy is chosen on the Galaxies
            # page.
            self.show_galaxies()
            return
        busy = running_clients(self.client_exes)
        if busy:
            self.warn("A Cosmic Supremacy game is already running "
                      f"({', '.join(busy)}).\n\nStarting a second one would "
                      "close the first without saving. Quit the running game "
                      "first, then try again.")
            return
        if fresh and mode.get("generate"):
            self.new_galaxy(mode)
            return
        save = None
        if mode.get("continues") and not fresh:
            save = latest_save(os.path.join(self.data_dir, "games"))
        self._start(mode, save, fresh_ai=save is None)

    def _start(self, mode, galaxy, fresh_ai):
        """Launch a mode's client on `galaxy`, or on its own when None."""
        try:
            self.child = launch_mode(mode, self.game_root, self.galaxy_root,
                                     galaxy=galaxy)
        except FileNotFoundError as exc:
            self.warn(f"Missing file:\n\n{exc}\n\nThe release folder looks "
                      "incomplete; try downloading it again.")
            return
        except OSError as exc:
            self.warn(f"Could not start the game:\n\n{exc}")
            return
        self.running_mode = mode
        if galaxy:
            self.say(f"launched {mode['id']}: {mode['exe']}, on "
                     f"{os.path.basename(galaxy)}")
        else:
            self.say(f"launched {mode['id']}: {mode['exe']} {mode['galaxy']}")
        # "short", not "title": the button titles are imperative.
        self.set_status(f"{_short(mode)} is running", OK)
        if mode.get("ai"):
            self.start_ai(mode, fresh_galaxy=fresh_ai)

    def new_galaxy(self, mode):
        """New Game for a mode that `generate`s: the client builds a galaxy
        from the pass file, the launcher saves and seeds it for the AI, then
        plays it. Falls back to the mode's own galaxy if any step fails."""
        pass_file = os.path.join(self.galaxy_root, mode["generate"])
        save_dir = os.path.join(self.data_dir, "saves")
        out = os.path.join(self.data_dir, "games", NEW_GAME_FILE)
        self.set_status("preparing a new galaxy", WARN)

        def work():
            try:
                return generate_galaxy(self.game_root, pass_file, save_dir,
                                       out, log=self.say_threadsafe)
            except Exception as exc:          # any failure falls back
                self.say_threadsafe(f"new galaxy failed ({exc}); playing the "
                                    f"shipped one")
                return None

        def done(path):
            self._start(mode, path, fresh_ai=True)

        self._in_background("preparing a new galaxy", work, done)

    def _show_identity(self):
        self.view.show_identity(self.player)
        self._show_account()
        self._show_player_code()

    def _show_account(self):
        self.view.show_account(account_email(self.data_dir))

    def _show_player_code(self):
        """Put this install's player code beside the build, once it has one.

        Read from fb_identity.json and never minted here, so an install with no
        sign-in shows no code. Called again after every listing and every
        Play, which is when a first sign-in happens.
        """
        self.view.show_build(
            build_line(build_id(), player_code(install_uid(self.data_dir))))
        ident = account_identity(self.data_dir)
        if ident is not None and ident.dropped:
            ident.dropped = False
            self.say("account login refused; playing as this install's guest")
            self._show_account()
            self.warn(DROPPED_TEXT)

    def ask_player_name(self, reason: str = ""):
        """Ask for the player's name, store it, and return it. None if cancelled.

        Modal, and it validates in place beside the field.
        """
        text = ("This is the name other players see, and the default name of your "
                "civilisation in a galaxy.")
        if reason:
            text = reason + "\n\n" + text
        name = self.view.ask_name(text, self.player or "", NAME_RULES,
                                  validate_player_name)
        if not name:
            return None
        try:
            save_identity(self.data_dir, name)
        except OSError as exc:
            self.warn(f"Could not save your name:\n\n{exc}\n\nIt will "
                      "be used for this session and asked for again next time.")
        self.player = name
        self._show_identity()
        self.say(f"player  {name}")
        ident = account_identity(self.data_dir)
        if ident is not None and ident.account:
            self._push_name(ident)
        return name

    def on_change_name(self):
        """Let a player change their name, except out from under a live galaxy.

        Refused while a galaxy is being played: the referee matches
        submissions to the roster by exact name.
        """
        if self.mp_thread is not None and self.mp_thread.is_alive():
            self.warn("You are playing a galaxy right now.\n\nYour name "
                      "is the name of your civilisation in it, and a "
                      "galaxy's "
                      f"roster cannot be renamed from here. Close {self.cfg['product']} "
                      "to leave the galaxy, then change it.")
            return
        self.ask_player_name()

    def _account_locked(self) -> bool:
        """Refuse to log in or out under a live galaxy, which knows this
        player by the sign-in it was joined with."""
        if self.mp_thread is None or not self.mp_thread.is_alive():
            return False
        self.warn("You are playing a galaxy right now.\n\nIt knows you by "
                  "the login you joined it with. Close "
                  f"{self.cfg['product']} to leave the galaxy, then log in "
                  "or out.")
        return True

    def on_account(self):
        """Log in, create an account, or show the account that is logged in."""
        if self._account_locked():
            return
        ident = account_identity(self.data_dir)
        if ident is None:
            self.warn("This build cannot log in.")
            return
        if ident.account:
            self._account_panel(ident)
        else:
            self._log_in(ident)
        self._show_account()
        self._show_player_code()

    def _log_in(self, ident):
        """The log in dialog, until the player logs in, creates an account or
        cancels. A refusal comes back in the same dialog."""
        fb_auth = _fb_auth()
        secret = google_secret()
        buttons = [("Forgot password", "reset"), ("Create account", "create")]
        if secret:
            buttons.append(("Log in with Google", "google"))
        buttons.append(("Log in", "log_in"))
        email, problem = "", ""
        while True:
            value, got = self.view.ask_fields(
                "Log in", LOG_IN_TEXT,
                [("email", "Email", "email", email),
                 ("password", "Password", "password", "")],
                buttons, error=problem, default="log_in")
            email = (got.get("email") or "").strip()
            password = got.get("password") or ""
            if value is None:
                return
            if value == "create":
                if self._create_account(ident, email):
                    return
                problem = ""
                continue
            try:
                if value == "reset":
                    self.view.wait_for("Sending…",
                                       lambda: ident.send_password_reset(email))
                    self.warn(f"If an account uses {email}, a link to reset "
                              "its password is on its way.")
                    problem = ""
                    continue
                guest = self._guest_games(ident)
                code = player_code(ident.uid)
                if value == "google":
                    linked, info = self._google(ident, secret)
                else:
                    linked, info = self.view.wait_for(
                        "Logging in…",
                        lambda: (False, ident.log_in(email, password)
                                 and ident.lookup()))
            except fb_auth.AuthError as exc:
                problem = "" if exc.code == "CANCELLED" else str(exc)
                continue
            self.say(f"account logged in as {ident.account['email']}")
            self._adopt_account_name(ident, info)
            if linked:
                self.warn(GOOGLE_LINKED_TEXT)
            elif guest:
                self.warn(GUEST_GAMES_TEXT.format(names=", ".join(guest),
                                                  code=code))
            return

    def _google(self, ident, secret):
        """Log in with Google in the player's browser, with a Cancel button
        meanwhile. Returns (whether the guest became the account, lookup)."""
        fb_auth = _fb_auth()
        cancel = threading.Event()

        def work():
            got = ident.log_in_google(fb_auth.google_id_token(secret, cancel))
            return got["linked"], ident.lookup()

        return self.view.wait_for(GOOGLE_WAIT_TEXT, work, cancel=cancel)

    def _create_account(self, ident, email: str) -> bool:
        """The create account dialog. True once the guest is an account, False
        when the player goes back."""
        fb_auth = _fb_auth()
        problem = ""
        while True:
            value, got = self.view.ask_fields(
                "Create account", CREATE_TEXT,
                [("email", "Email", "email", email),
                 ("password", "Password", "password", ""),
                 ("confirm", "Confirm", "password", "")],
                [("Back", None), ("Create account", "create")],
                error=problem, note=fb_auth.PASSWORD_RULES, default="create")
            email = (got.get("email") or "").strip()
            password = got.get("password") or ""
            if value is None:
                return False
            if password != (got.get("confirm") or ""):
                problem = "The two passwords are not the same."
                continue
            try:
                self.view.wait_for("Creating your account…",
                                   lambda: ident.create_account(email, password))
            except fb_auth.AuthError as exc:
                problem = str(exc)
                continue
            self.say(f"account created for {email}")
            if self.player:
                self._push_name(ident)
            self.warn(CREATED_TEXT.format(email=email))
            return True

    def _account_panel(self, ident):
        """Who is logged in, whether the email is verified, and log out."""
        info = self.view.wait_for("Checking your account…", ident.lookup)
        text = f"You are logged in as {ident.account['email']}."
        choices = [("Close", None, False)]
        if info is not None and not info["verified"]:
            text += ("\n\nYour email address is not verified yet. Open the "
                     "link in the email we sent, or send it again.")
            choices.append(("Send email again", "verify", False))
        choices.append(("Log out", "log_out", True))
        value = self.view.choose("Account", text, choices)
        if value == "verify":
            sent = self.view.wait_for("Sending…", ident.send_verification)
            self.warn(f"We sent a verification email to {ident.account['email']}."
                      if sent else "The email could not be sent. Try again later.")
        elif value == "log_out" and self.view.ask_yes_no(LOG_OUT_TEXT):
            email = ident.account["email"]
            ident.log_out()
            self.say(f"account {email} logged out")

    def _email_ready(self) -> bool:
        """Whether this sign-in may join or play: a guest, a Google login, or
        an email account whose address is verified. Asks for the verification
        otherwise, and renews the token once it is done, so the relay sees it."""
        ident = account_identity(self.data_dir)
        if (ident is None or not ident.account
                or ident.account.get("provider") != "password"):
            return True
        email = ident.account["email"]
        lead = ""
        while True:
            info = self.view.wait_for("Checking your account…", ident.lookup)
            if info is None:
                return True             # offline: the relay decides
            if info["verified"]:
                self.view.wait_for("Checking your account…", ident.renew)
                return True
            value = self.view.choose(
                VERIFY_TITLE, lead + VERIFY_TEXT.format(email=email),
                [("Close", None, False), ("Send email again", "verify", False),
                 ("I have verified it", "check", True)])
            if value == "verify":
                sent = self.view.wait_for("Sending…", ident.send_verification)
                lead = (f"We sent another verification email to {email}.\n\n"
                        if sent else
                        "The email could not be sent. Try again later.\n\n")
            elif value == "check":
                lead = "Your email address is not verified yet.\n\n"
            else:
                self.say("account: email not verified, not joining or playing")
                return False

    def _guest_games(self, ident):
        """The names of the galaxies this install's guest joined, while the
        guest is who is playing."""
        if ident.account or not ident.uid:
            return []
        return [str(rec.get("galaxy")) for rec in load_joined(self.data_dir).values()
                if rec.get("uid") == ident.uid]

    def _adopt_account_name(self, ident, info):
        """Take the account's name, or give it this install's when it has none."""
        name, _why = validate_player_name((info or {}).get("name") or "")
        if name and name != self.player:
            try:
                save_identity(self.data_dir, name)
            except OSError:
                pass        # usable this run; read from the account next time
            self.player = name
            self.view.show_identity(name)
            self.say(f"player  {name}")
        elif not name and self.player:
            self._push_name(ident)

    def _push_name(self, ident):
        """Store the player's name on the account, off the UI thread."""
        threading.Thread(target=ident.set_display_name, args=(self.player,),
                         daemon=True, name="account_name").start()

    def show_page(self, name: str):
        """Swap the window's body between the launcher's two pages."""
        if self.page == name:
            return
        self.page = name
        self.view.show_page(name)

    def show_home(self):
        self.show_page("home")

    def show_galaxies(self):
        """What the Multiplayer button does: open the list, play nothing.

        The listing is asked for on the way in, not kept warm behind the home
        page.
        """
        self.show_page("galaxies")
        self._draw_galaxies()
        self.refresh_games()

    def sort_by(self, key: str):
        """Sort on this column, or turn it round if it is already the one."""
        if key == self.sort_key:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_key, self.sort_desc = key, False
        self._draw_galaxies()

    def _show_sort(self):
        """Mark the column being sorted on, and which way round it is."""
        self.view.show_sort(self.sort_key, self.sort_desc)

    def _games_tick(self):
        """Re-list on a slow timer, and keep the timer whatever a listing did.

        Only while the page is on screen and the window is not minimised.
        `on_restored` re-lists when the window comes back.
        """
        if self.page == "galaxies" and not self.view.is_minimized():
            self.refresh_games()
        self.view.after(GAMES_POLL_MS, self._games_tick)

    def on_restored(self):
        """The window came back from the taskbar. The Galaxies page was last
        listed before it went, so it is listed again now."""
        if self.page == "galaxies":
            self.refresh_games()

    def refresh_games(self):
        """List off the UI thread and repaint when it answers.

        The directory and the multiplayer.json store may be network reads, and
        this window is also the game's server: blocking it stops the server
        answering the client.
        """
        if self.games_busy or not (self.games_dir or self.own_store):
            return
        self.games_busy = True
        self.view.set_games_status("listing galaxies…", DIM)
        spec, player, data_dir = self.games_dir, self.player, self.data_dir
        recs = load_joined(data_dir)

        def work():
            extra = {}
            rows, err = galaxy_rows(data_dir, spec, player)
            if player:
                extra["submitted"] = self._submitted_state(rows, recs, player)
            if recs:
                extra["answers"] = self._join_answers(rows, recs)
                # This refresh's answers folded in, so a seat whose granted
                # answer arrives now is checked now.
                seen = {gid: dict(recs[gid], answer=answer)
                        for gid, (_at, answer) in extra["answers"].items()
                        if gid in recs}
                extra["reclaims"] = self._reclaims(rows, dict(recs, **seen))
            self.msgs.put(("__games__", rows, extra, err))

        threading.Thread(target=work, daemon=True, name="games").start()

    def _submitted_state(self, rows, recs, player):
        """Which of this player's galaxies have had the current turn handed in.

        One read per galaxy this player is in. A galaxy that could not be read
        is left out, and its row prints "you are in". Being in is the
        directory's `joined`; no joined record is required, since a seat taken
        under `seat_claim: first-use` never passes through Join. A galaxy with
        a record is asked under the name its seat was claimed under, else the
        current name.

        Never raises: the listing is posted back to the window after this.
        """
        out = {}
        for g in rows:
            rec = (recs or {}).get(g.id)
            # A closed galaxy has no turn left to play, so it is not asked.
            if not g.joined or g.turn is None or g.status == CLOSED:
                continue
            try:
                mods = multiplayer_modules()
                if mods is None:
                    return out
                _player_turn, turn_store = mods
                token = (player_token(self.data_dir)
                         if store_wants_token({"store": g.store}) else None)
                store = open_player_store(turn_store, g.store, token)
                name = (rec or {}).get("name") or player
                out[g.id] = bool(store.has_submitted(name, g.turn))
            except Exception:               # a readout is not worth an error path
                continue
        return out

    def _join_answers(self, rows, recs):
        """What each galaxy decided about a join this launcher is waiting on.

        One read of the store's `join_answer` per row `read_join_answers`
        picks, keyed by the request's own key so a player only sees their own
        answer. A store without that read is skipped. Never raises.
        """
        mods = multiplayer_modules()
        if mods is None:
            return {}
        _player_turn, turn_store = mods

        def ask(g, rec):
            key = turn_store.join_key(rec)
            if not key:
                return None
            token = (player_token(self.data_dir)
                     if store_wants_token({"store": g.store}) else None)
            store = open_player_store(turn_store, g.store, token)
            read = getattr(store, "join_answer", None)
            return read(key) if callable(read) else None

        try:
            return read_join_answers(rows, recs, ask)
        except Exception:                   # a readout is not worth an error path
            return {}

    def _reclaims(self, rows, recs):
        """Which granted seats the galaxy has since taken back.

        One state read per galaxy `reclaim_suspect` names. Never raises.
        """
        mods = multiplayer_modules()
        if mods is None:
            return {}
        _player_turn, turn_store = mods

        def read_state(g):
            token = (player_token(self.data_dir)
                     if store_wants_token({"store": g.store}) else None)
            return open_player_store(turn_store, g.store, token).state()

        try:
            return read_reclaims(rows, recs, read_state)
        except Exception:                   # a readout is not worth an error path
            return {}

    def _on_games(self, rows, extra, err):
        """Take a listing in. Runs on the UI thread, off the message queue.

        Join answers are written into joined.json here because Join and Clear
        write that file from this thread too.
        """
        self.games_busy = False
        try:
            record_join_answers(self.data_dir, (extra or {}).get("answers"))
            record_reclaims(self.data_dir, (extra or {}).get("reclaims"))
        except OSError as exc:
            self.say(f"games   could not keep a join answer ({exc})")
        self.games_rows = list(rows)
        self.games_listed = time.time()
        self.games_extra = extra or {}
        self.games_err = err
        if err:
            self.say(f"games   {err}")
        self._draw_galaxies()

    def _on_mp_played(self, gid, turn):
        """The followed galaxy holds this player's turn. Runs on the UI thread.

        The row reads "turn played" now rather than at the next listing. A
        listing from another turn is listed again instead.
        """
        row = next((g for g in self.games_rows or [] if g.id == gid), None)
        if row is None or turn is None:
            return
        if row.turn != turn:
            if self.page == "galaxies":
                self.refresh_games()
            return
        submitted = dict(self.games_extra.get("submitted") or {})
        if submitted.get(gid) is True:
            return
        submitted[gid] = True
        self.games_extra = dict(self.games_extra, submitted=submitted)
        if self.page == "galaxies":
            self._draw_galaxies()

    def _cell_colour(self, col, g, view, near: bool) -> str:
        """What colour one cell is drawn in.

        Lit for galaxies that are this player's business, dim for the rest. A
        stopped galaxy's status cell takes the warning colour regardless.
        """
        if col is GALAXY_STATUS and row_stalled_for(g, view):
            return WARN
        return TEXT if near else DIM

    def _galaxy_view(self):
        """What a cell needs to know that its own row does not carry."""
        return View(now=time.time(), recs=self.games_recs,
                    listed=getattr(self, "games_listed", None),
                    submitted=self.games_extra.get("submitted"),
                    own=self.own_store)

    def _draw_galaxies(self):
        """Paint the table from the last listing, in the order asked for."""
        self._clocks = []
        self.games_recs = load_joined(self.data_dir)
        view = self._galaxy_view()
        rows = sort_galaxies(self.games_rows or [], self.sort_key,
                             self.sort_desc, view)
        self._show_sort()
        self._show_source(rows)
        # Before the first listing lands (`games_rows` is None) the table
        # shows the waiting text.
        if self.games_rows is None:
            self.view.draw_galaxies(None, GALAXIES_WAITING)
            self._show_hint(rows, view)
            return
        drawn = []
        for r, g in enumerate(rows):
            action = row_action(g, view.recs, view.own)
            # Lit rows are this player's business: in it, asked to join it, or
            # named it themselves.
            near = bool(g.joined or action in (PLAY, ENDED, RECLAIMED)
                        or pending_join(g, view.recs))
            cells = []
            for i, col in enumerate(GALAXY_COLUMNS):
                cells.append((col.text(g, view),
                              self._cell_colour(col, g, view, near)))
                if col in LIVE_COLUMNS:
                    self._clocks.append((r, i, col, g, near))
            drawn.append(GalaxyRow(
                cells, ACTION_TEXT[action] if action is not None else None,
                (lambda gg=g, a=action: self.on_row(gg, a))
                if action is not None else None))
        self.view.draw_galaxies(drawn)
        self._show_hint(rows, view)

    def _show_source(self, rows):
        """The line above the table: where these rows came from, or why not.

        An unreachable directory is reported here and the table is still drawn,
        since the galaxy multiplayer.json names is listed regardless.
        """
        if self.games_err:
            self.view.set_games_status(self.games_err, WARN)
        elif self.games_rows is None:
            self.view.set_games_status("listing galaxies…", DIM)
        elif not rows:
            self.view.set_games_status("No galaxies are listed here yet.", DIM)
        elif self.games_dir:
            # Labelled, since the directory is a bare Cloud Run hostname.
            self.view.set_games_status(f"Connected to: {self.games_dir}", FAINT)
        else:
            self.view.set_games_status(
                f"{MP_CONFIG} names this galaxy, so no directory is listed.",
                FAINT)

    def _show_hint(self, rows, view):
        """One line saying what to press when there is nothing to play yet.

        A stopped galaxy takes the line first, then a refusal, a reclaimed
        seat, an ended galaxy, and last what to press when nothing is playable.
        """
        actions = [row_action(g, view.recs, view.own) for g in rows]
        stalled = [(g, s) for g, s in
                   ((g, row_stalled_for(g, view)) for g in rows) if s]
        refused = [g for g, a in zip(rows, actions) if a == REASON]
        reclaimed = [g for g, a in zip(rows, actions) if a == RECLAIMED]
        ended = [g for g, a in zip(rows, actions) if a == ENDED]
        text = ""
        if stalled:
            text = stall_hint(stalled)
        elif refused:
            names = ", ".join(g.name or g.id for g in refused)
            text = (f"Your request to join {names} was refused. Press Review "
                    "on its row to read why, then ask again or clear it.")
        elif reclaimed:
            names = ", ".join(g.name or g.id for g in reclaimed)
            text = (f"Your seat in {names} was taken back after missed turns. "
                    "Press Review on its row to read more.")
        elif ended:
            text = ended_hint(ended)
        elif rows and PLAY not in actions:
            if VIEW in actions:
                text = ("You are not in a galaxy yet. Press View on one to "
                        "read what you would be joining, and join from "
                        "there.")
            elif any(awaiting_answer(g, view.recs) for g in rows):
                text = ("Your join request is pending until the next turn.")
        # Called once a second; the view repaints only on a change.
        self.view.set_hint(text)

    def _tick_clocks(self):
        """Keep the countdowns and the stopped galaxies honest between
        listings.

        Only the live columns are rewritten; the order is left alone so rows
        do not move under the pointer. The hint is redrawn too, since a galaxy
        becomes stopped by the clock.
        """
        view = self._galaxy_view()
        for r, i, col, g, near in self._clocks:
            self.view.set_cell(r, i, col.text(g, view),
                               self._cell_colour(col, g, view, near))
        self._show_hint(sort_galaxies(self.games_rows or [], self.sort_key,
                                      self.sort_desc, view), view)

    def on_row(self, g, action: str):
        """What a row's one button does. View reads, Play plays, Review
        explains a refusal, an ended galaxy or a seat taken back."""
        if action == PLAY:
            self.play_galaxy(g)
        elif action == REASON:
            self.show_refusal(g)
        elif action == ENDED:
            self.show_ended(g)
        elif action == RECLAIMED:
            self.show_reclaimed(g)
        else:
            self.on_join(g)

    def show_refusal(self, g):
        """Tell a refused player why, and let them ask again or clear it.

        The reason is the worker's own sentence, shown whole. Ask again goes
        through Join, so every check and the notice run again. Clear forgets
        only this galaxy's record, which puts View back on the row.
        """
        rec = load_joined(self.data_dir).get(g.id)
        if not join_refusal(g, {g.id: rec} if rec else None):
            self._draw_galaxies()
            return
        what = self._choice_dialog("Join refused", refusal_text(g, rec),
                                   (("Ask again", "again", True),
                                    ("Clear", "clear", False),
                                    ("Close", None, False)))
        if what == "clear":
            clear_joined(self.data_dir, g.id)
            self.say(f"join: cleared the refused request for {g.id}")
            self._draw_galaxies()
        elif what == "again":
            self.on_join(g)

    def show_reclaimed(self, g):
        """Tell a player their seat was taken back, and let them clear it.

        Everything shown is in the joined record, so nothing is read. Clear
        forgets this galaxy's record, which puts View back on the row.
        """
        rec = load_joined(self.data_dir).get(g.id)
        if not seat_reclaimed(g, {g.id: rec} if rec else None):
            self._draw_galaxies()
            return
        what = self._choice_dialog("Seat reclaimed", reclaimed_text(g, rec),
                                   (("Confirm", "clear", True),
                                    ("Close", None, False)))
        if what == "clear":
            clear_joined(self.data_dir, g.id)
            self.say(f"games   cleared the reclaimed seat in {g.id}")
            self._draw_galaxies()

    def show_ended(self, g):
        """Say that a galaxy has ended, in the operator's words.

        The reason comes from the row already held, so nothing is read.
        """
        self.say(f"games   {g.id} has ended")
        self._choice_dialog("Galaxy ended", ended_text(g),
                            (("Close", None, True),))

    def _choice_dialog(self, title: str, body: str, choices):
        """A modal box with a title, a body and a row of buttons.

        `choices` is (label, value, primary) per button, left to right, and
        the value of the one pressed comes back. Escape and the window's own
        close answer None.
        """
        return self.view.choose(title, body, choices)

    def play_galaxy(self, g):
        """Take turns in one row's galaxy.

        The page stays where it is; the turn readout, controls and status line
        are visible from every page.
        """
        mode = next((m for m in self.modes if m.get("id") == "multiplayer"),
                    None)
        if mode is None or not is_playable(mode):
            self.warn("This build does not offer multiplayer.")
            return
        self.start_multiplayer(mode, g)

    def show_beta_notice(self, body: str, g, civ: str = None) -> bool:
        """Show the notice and answer whether the player joined from it.

        Modal, and the Join button under the full, scrolled text is the only
        route to joining. The line above the text says when the empire
        arrives, so no box follows the Join.
        """
        notice = beta_notice_module()
        heading = notice.title(body) if notice else "Before you join the beta"
        when = f"turn {g.turn + 1}" if g.turn is not None else "its first turn"
        who = f" as {civ}" if civ else ""
        lead = (f"You are about to join {g.name or g.id}{who}. New players "
                "arrive at a turn boundary, so your empire appears at "
                f"{when}, and its row here gets a Play button then.")
        return self.view.beta_notice(heading, lead, body)

    def on_join(self, g):
        """Ask a galaxy for a seat. The worker seats the player at the next turn.

        Checks come first (name, build, closed galaxy, seat name), then the
        notice, then the request, so a refusal or a change of mind writes
        nothing.
        """
        civ = self.player or self.ask_player_name(
            "Before you can join a galaxy, this launcher needs to know who you "
            "are.")
        if not civ:
            self.say("join: no player name entered, not joining")
            return

        # A seat in another galaxy does not block this join.
        rec = load_joined(self.data_dir).get(g.id)

        mods = multiplayer_modules()
        if mods is None:
            self.warn("This build cannot play multiplayer.\n\nThe turn "
                      "machinery is missing from it. Everything else in this "
                      "launcher works as normal.")
            return
        _player_turn, turn_store = mods

        cfg = {"store": g.store}
        if store_wants_token(cfg) and not self._email_ready():
            return
        token = player_token(self.data_dir) if store_wants_token(cfg) else None
        try:
            store = open_player_store(turn_store, g.store, token)
            state = store.state() if store.exists() else None
        except Exception as exc:            # any store; unreachable is usual
            self.say(f"join: could not read {g.id} ({exc})")
            self.warn(f"Could not reach {g.name or g.id}:\n\n{exc}")
            return

        # The build and closed checks come ahead of the seat check, since
        # either explains anything that looks wrong further down.
        if state is not None:
            problem = version_problem(build_id(), state)
            if problem:
                self.say(f"join: build {build_id()} is below {g.id}'s minimum "
                         f"{state.get(MIN_BUILD_KEY)!r}")
                self.warn(problem)
                return
            # The listing may predate the galaxy closing.
            problem = closed_problem(state)
            if problem:
                self.say(f"join: {g.id} is closed")
                self.warn(problem)
                return

        seats = list((state or {}).get("civs", []))
        held = rec.get("name") if rec else None
        problem = seat_claim_problem(civ, seats, held=held)
        if problem:
            # The count, not the names, in the window and in the log alike.
            self.say(f"join: {civ!r} cannot claim a seat in {g.id}, which has "
                     f"{len(seats)} seat(s)")
            if self.view.ask_yes_no(problem + "\n\nChange your name now?"):
                self.on_change_name()
            return

        # The notice is the last step before the request.
        notice, why = beta_notice_text(state)
        if notice is None:
            self.say(f"join: the beta notice is not showable ({why})")
            self.warn("This build cannot show you what you are joining.\n\n"
                      f"{why}.\n\nJoining is stopped here rather than done "
                      "without it, because what would be missing is the rules "
                      "of the beta rather than a detail of them.")
            return
        if not self.show_beta_notice(notice, g, civ):
            self.say(f"join: the notice was closed without joining {g.id}")
            return

        uid = install_uid(self.data_dir, mint=token is not None)
        try:
            where = send_join_request(store, join_request(civ, uid, build_id(),
                                                          turn=g.turn))
        except (JoinNotAccepted, OSError, ValueError) as exc:
            self.say(f"join: {g.id} took no join request ({exc})")
            self.warn("This galaxy cannot take a join request yet.\n\nIts "
                      "store has no route for one, so nothing was sent and "
                      "nothing has changed.")
            return

        # The turn just read; the listing's can be a boundary behind. See
        # `answer_for`.
        now_turn = (state or {}).get("turn")
        save_joined(self.data_dir, joined_record(g, civ, uid, self.games_dir,
                                                 turn=now_turn))
        self.say(f"join: asked {g.id} for a seat as {civ} ({where})")
        self.refresh_games()

    def galaxy_config(self, g):
        """A multiplayer.json-shaped config for the galaxy about to be played.

        A row's own store is played. The row's joined record is carried along,
        since the seat may have been asked for under an earlier name. With no
        row, `galaxy_to_play` answers.
        """
        if g is None:
            return galaxy_to_play(self.data_dir)
        cfg = multiplayer_config(self.data_dir)
        if cfg and same_store(cfg.get("store"), g.store):
            return cfg
        out = {"store": g.store}
        rec = load_joined(self.data_dir).get(g.id)
        if rec:
            out["joined"] = rec
            if isinstance(rec.get("auth"), bool):
                out["auth"] = rec["auth"]
        return out

    def playing_now(self):
        """The galaxy this launcher's turn loop is following, or None.

        The one place that answers whether a galaxy is being played; there is
        one loop, since it drives the game client. A running loop with nothing
        recorded answers an empty record, never None, so a second cannot start.
        """
        if self.mp_thread is None or not self.mp_thread.is_alive():
            return None
        return self.mp_playing or {}

    def _take_turn_loop(self, g) -> bool:
        """Whether the turn loop is free for this galaxy, asking to switch.

        One galaxy is played at a time, since the turn loop drives the one
        game client. A second Play names the galaxy being followed and offers
        to switch, which stops that loop between turns. The stop happens ahead
        of the caller's checks, the first of which asks whether a client is
        running.

        Play on the followed galaxy with its window closed asks the running
        loop to reopen the game, and still answers False.
        """
        playing = self.playing_now()
        if playing is None:
            return True
        here = playing.get("name") or playing.get("store") or "a galaxy"
        if g is not None and same_store(playing.get("store"), g.store):
            # The loop outlives the game window, waiting for the referee.
            if self.mp_client_gone:
                # Ask the loop, which owns the client, to reopen the game: the
                # player's own submission for this turn, or the new turn if
                # the referee has published one.
                self.mp_reopen.set()
                self.mp_reopening = True
                turn = self.mp_turn
                where = f"{here} at turn {turn}" if turn else here
                self.say(f"multiplayer: opening {where} again, with the "
                         f"orders you already sent")
            else:
                self.warn(f"You are already playing {here}.\n\nIts turns "
                          "are being followed now, and the readout on "
                          "the home page is this galaxy's.")
            return False

        there = (g.name or g.id) if g is not None else "another galaxy"
        # Ask only when a game window is open to lose.
        if not self.mp_client_gone:
            if not self.view.ask_yes_no(
                    f"This launcher is playing {here} right now.\n\nYou can be in "
                    "as many galaxies as you like, but a turn starts the game "
                    "itself and this machine runs one game, so one galaxy is "
                    f"played at a time.\n\nStop following {here} and play {there} "
                    "instead?"):
                self.say(f"multiplayer: still following {here}, not switching")
                return False
        else:
            self.say(f"multiplayer: {here} had no game open, switching to {there}")

        # stop_multiplayer clears the handle whether or not the loop finished,
        # and two loops must not drive the same client.
        thread = self.mp_thread
        self.stop_multiplayer(f"switching to {there}")
        if thread is not None and thread.is_alive():
            self.warn(f"{here} is still finishing a turn.\n\nIt stops between "
                      f"turns rather than mid-capture. Press Play on {there} "
                      "again in a moment.")
            return False
        return True

    def start_multiplayer(self, mode, g=None):
        """Follow this galaxy's turns until the player stops or the launcher
        closes."""
        if not self._take_turn_loop(g):
            return
        busy = running_clients(self.client_exes)
        if busy:
            self.warn("A Cosmic Supremacy game is already running "
                      f"({', '.join(busy)}).\n\nClose it first.")
            return

        # Every capture arrives through this launcher's own server.
        if not self.server_ok:
            port = self.cfg["server"]["port"]
            self.warn(
                "This launcher's server is not running, so a turn played now "
                "could not be saved.\n\nAnother server holds port "
                f"{port}. Close it and start this launcher again.")
            return

        # The row's galaxy, or with no row the galaxy this launcher plays.
        cfg = self.galaxy_config(g)
        if cfg is None:
            self.warn(
                "You have not joined a galaxy yet.\n\nPress Multiplayer to "
                "open the galaxy list, press View on one to read what you "
                "would be joining, and join from there. The launcher writes "
                "down which galaxy you joined, so there is no file to edit."
                "\n\nA galaxy of your own, in a folder or on another PC here, "
                f"is named in a {MP_CONFIG} in\n{self.data_dir}\n\nfor "
                'example:\n\n{"store": "C:\\\\galaxies\\\\demo"}')
            return

        # The name is first needed here, so it is not asked at first run.
        civ = self.player or self.ask_player_name(
            "Before you can join a galaxy, this launcher needs to know who you "
            "are.")
        if not civ:
            self.say("multiplayer: no player name entered, not starting")
            return

        mods = multiplayer_modules()
        if mods is None:
            self.warn("This build cannot play multiplayer.\n\nThe turn "
                      "machinery is missing from it. Everything else in this "
                      "launcher works as normal.")
            return
        player_turn, turn_store = mods
        # Before anything asks the modules for a path.
        bind_multiplayer_paths(self.game_root, self.data_dir)

        # The turn loop needs a client that never computes a turn of its own,
        # not the single player one. Checked here, before the worker starts.
        if not any(os.path.exists(os.path.join(self.game_root, e))
                   for e in MP_CLIENTS):
            self.warn("This build ships no multiplayer client.\n\nExpected one "
                      f"of\n{', '.join(MP_CLIENTS)}\n\nin\n{self.game_root}")
            return

        # `open_store` picks a folder or URL store from the spec. A galaxy
        # behind the Cloud Function gets this install's Firebase identity; a
        # folder or LAN turn_server gets none. An identity that cannot be
        # minted is a missing header, not a refusal to start.
        if store_wants_token(cfg) and not self._email_ready():
            return
        token = player_token(self.data_dir) if store_wants_token(cfg) else None
        if token is not None and token() is None:
            self.say("multiplayer: no Firebase identity available, opening the "
                     "galaxy without one")
        store = open_player_store(turn_store, cfg["store"], token)
        if not store.exists():
            self.warn(f"No galaxy in\n{cfg['store']}\n\nThe referee has to "
                      "publish a first turn before anyone can play it.")
            return

        # The build check comes ahead of the roster and of anything served.
        try:
            state = store.state()
        except (OSError, ValueError, KeyError) as exc:
            state = None
            self.say(f"multiplayer: could not read the galaxy's state ({exc})")
        if state is not None:
            problem = version_problem(build_id(), state)
            if problem:
                self.say(f"multiplayer: build {build_id()} is below this "
                         f"galaxy's minimum {state.get(MIN_BUILD_KEY)!r}")
                self.warn(problem)
                return
            # Also ahead of the roster: a closed galaxy refuses submissions.
            problem = closed_problem(state)
            if problem:
                self.say("multiplayer: this galaxy is closed")
                self.warn(problem)
                return

        # The referee matches submissions to the roster by exact name and
        # discards anything else. Other players' names are never shown: see
        # roster_problem.
        try:
            seats = store.civs()
        except (OSError, ValueError, KeyError) as exc:
            seats = None
            self.say(f"multiplayer: could not read the roster ({exc})")
        if seats is not None:
            taken = reclaimed_seat(state, civ)
            problem = roster_problem(civ, seats, taken)
            if problem:
                # The count, not the names, since the player can read the log.
                if taken:
                    self.say(f"multiplayer: {civ!r} was reclaimed at turn "
                             f"{taken.get('turn')} after {taken.get('missed')} "
                             f"missed turn(s)")
                self.say(f"multiplayer: {civ!r} is not one of this galaxy's "
                         f"{len(seats)} seat(s)")
                if taken:
                    # No rename offer for a reclaimed seat. This galaxy's
                    # joined record (if it was joined from the list) is
                    # dropped, so its row offers Join again.
                    if cfg.get("joined"):
                        clear_joined(self.data_dir,
                                     cfg["joined"].get("galaxy"))
                        self.refresh_games()
                    self.warn(problem)
                    return
                if self.view.ask_yes_no(problem + "\n\nChange your name now?"):
                    self.on_change_name()
                return

        # The turn loop reads notes only for turns it followed, so a returning
        # player's missed-turns warning is read once here, before the game
        # opens.
        warning = self._warning_note(store, civ, (state or {}).get("turn"))
        if warning:
            self.say(f"multiplayer: the referee has warned {civ} about "
                     f"missed turns")
            self.warn(warning_text(g.name or g.id if g is not None
                                   else store_label(cfg["store"]), warning))

        self.mp_store = store
        self.mp_civ = civ
        self.mp_closed = False
        self.mp_probed = None
        self.mp_log_offered = False
        self.mp_seat_refused = False
        self.mp_playing = {"id": g.id if g is not None else cfg["store"],
                           "name": (g.name or g.id) if g is not None
                           else store_label(cfg["store"]),
                           "store": cfg["store"]}
        self.mp_stop = False
        self.mp_deadline = None
        # From the state read above. None if it did not read, which
        # `stall_after` answers with its ceiling.
        self.mp_turn_seconds = (state or {}).get("turn_seconds")
        self.mp_waiting = False
        self.mp_capture = None
        self.mp_client_seen = False
        self.mp_client_gone = False
        self.mp_sending = False
        self.mp_turn_open = False
        self.mp_send_now.clear()
        self.mp_reopen.clear()
        self.mp_reopening = False
        self.running_mode = mode
        joined = cfg.get("joined")
        if joined and joined.get("name") and joined["name"] != civ:
            self.say(f"multiplayer: this galaxy was joined as "
                     f"{joined['name']!r}")
        self._show_player_code()
        self.say(f"multiplayer: following {cfg['store']} as {civ}")
        self.set_status(f"Multiplayer: {civ}", OK)
        self._show_controls(True)

        # Where this launcher's captures land, which is not its own saves
        # directory when it is sharing a server it did not start.
        save_dir = self.save_dir or os.path.join(self.data_dir, "saves")
        os.makedirs(save_dir, exist_ok=True)

        def work():
            try:
                player_turn.follow(
                    store, civ,
                    # The read schedule's floor, used near the deadline; the
                    # rest of the turn backs off towards POLL_CEILING.
                    poll=2.0,
                    on_state=self._mp_state,
                    save_dir=save_dir,
                    stop=lambda: self.mp_stop,
                    send_now=self.mp_send_now,
                    reopen=self.mp_reopen,
                    log=self.say_threadsafe)
            except BaseException as exc:            # noqa: BLE001
                # A dead worker must say so. A relay refusal is said in the
                # relay's own sentence, from the response body.
                said = relay_refusal(exc)
                why = said[1] if said else str(exc)
                self.say_threadsafe(f"multiplayer stopped: {why}")
                self._mp_state("failed", error=why)
                box = refusal_box(said[1], civ, self.data_dir) if said else None
                if box and not getattr(self, "mp_seat_refused", False):
                    self.mp_seat_refused = True
                    self.msgs.put(("__mp_refused__", box))

        self.mp_thread = threading.Thread(target=work, daemon=True,
                                          name="multiplayer")
        self.mp_thread.start()

    def _mp_state(self, kind, **facts):
        """Called from the worker thread. Records a line for the turn readout."""
        civ = facts.get("civ", self.mp_civ)
        turn = facts.get("turn")
        if turn is not None:
            self.mp_turn = turn
        left = facts.get("seconds_left")
        if left is not None:
            self.mp_deadline = time.time() + left
        if kind in ("playing", "waiting", "overtaken", "lost", "done",
                    "stopped", "failed", "serve_failed"):
            # The reopen has either happened or is not going to.
            self.mp_reopening = False

        # A seat held by another sign-in, or an email not verified, refuses
        # every upload while the loop carries on, so a rebind by the operator
        # takes effect without a restart. The box saying what to do is shown
        # once per followed galaxy.
        box = (refusal_box(facts.get("error"), civ, self.data_dir)
               if kind in ("send_failed", "lost") else None)
        if box and not getattr(self, "mp_seat_refused", False):
            self.mp_seat_refused = True
            self.msgs.put(("__mp_refused__", box))
        if kind == "send_failed":
            return
        # The store going and coming back. Not turn steps, so the note is left
        # alone; the readouts ask `_mp_cut_off` before judging a stall.
        if kind == "unreachable":
            if getattr(self, "mp_unreachable", None) is None:
                self.mp_unreachable = time.time()
            return
        if kind == "reachable":
            self.mp_unreachable = None
            return

        # Capture events are recorded apart from the turn note. A capture that
        # matches what the store holds counts as sent.
        if kind == "capturing":
            self.mp_capture = (CAPTURING, time.time())
            return
        if kind == "captured":
            self.mp_capture = (HELD if facts.get("pending") else SENT,
                               time.time())
            return
        if kind == "capture_failed":
            self.mp_capture = (CAPTURE_FAILED, time.time())
            return
        if kind == "warned":
            # The loop found a missed-turns warning in a closed turn's note
            # and passes its lines. Shown in a box; the readout is left alone.
            lines = facts.get("lines")
            store = self.mp_store
            if store is not None and lines:
                self.msgs.put(("__mp_warning__", store,
                               [str(x) for x in lines]))
            return
        if kind == "refused_orders":
            # Already in the log, which is where refusals are read.
            return
        if kind in ("lost", "failed") and not getattr(self, "mp_closed",
                                                      False):
            # A galaxy closed under a followed loop first shows as a refused
            # submission; one state read says whether that is the cause.
            self._mp_probe_closed(self.mp_store)
        if (kind in LOG_OFFER_KINDS
                and not getattr(self, "mp_log_offered", False)
                and not seat_refused(facts.get("error"))
                and not email_unverified(facts.get("error"))):
            # Once per followed galaxy. Queued behind the probe above, so a
            # galaxy found closed is not offered. A seat held by another
            # sign-in and an email not verified have their own box.
            self.mp_log_offered = True
            self.msgs.put(("__log_offer__", self.mp_store))
        if kind == "submitted":
            self.mp_capture = (SENT, time.time())
            if facts.get("final"):
                self.mp_sending = False
            playing = getattr(self, "mp_playing", None)
            if playing:
                self.msgs.put(("__mp_played__", playing["id"], turn))
        elif kind in ("waiting", "overtaken", "lost", "done",
                      "stopped", "failed"):
            # The turn is over, however it ended, so nothing is still on its
            # way.
            self.mp_sending = False
            # Closing the window after this must not report the turn as
            # still sending (see `_watch_mp_client`).
            self.mp_turn_open = False
        if kind in ("serving", "playing", "reopening"):
            self.mp_turn_open = True
        if kind == "serving":
            # A new turn: nothing of it has been captured yet.
            self.mp_capture = None

        self.mp_note = {
            "serving": "starting your turn",
            "playing": "",
            "collecting": "time is up, orders are in",
            "submitted": "orders sent",
            "reopening": "opening the game again",
            "waiting": "waiting for the next turn",
            "overtaken": "that turn closed without you",
            "lost": "that turn could not be sent",
            "failed": f"stopped: {facts.get('error', 'unknown')}",
            "stopped": "stopped",
            "done": "finished",
        }.get(kind, kind)
        if getattr(self, "mp_closed", False):
            # Once the galaxy has ended, that is the note whatever the loop
            # does on its way out.
            self.mp_note = GALAXY_ENDED_NOTE
        # Set from the same kind as the note, so the two cannot come apart.
        self.mp_waiting = kind == "waiting"
        if kind in ("submitted", "waiting", "failed", "stopped", "done"):
            self.say_threadsafe(f"multiplayer: {self.mp_note} ({civ})")

    def stop_multiplayer(self, why: str = ""):
        """Ask the turn loop to stop. It finishes the step it is on first."""
        if self.mp_thread is None:
            return
        self.mp_stop = True
        if why:
            self.say(f"multiplayer: stopping: {why}")
        self.mp_thread.join(timeout=20)
        if self.mp_thread.is_alive():
            self.say("multiplayer: the turn loop is still finishing a capture")
        self.mp_thread = None
        self.mp_playing = None
        self.mp_note = ""
        self.mp_turn = None
        self.mp_deadline = None
        self.mp_turn_seconds = None
        self.mp_waiting = False
        self.mp_unreachable = None
        self.mp_capture = None
        self.mp_client_seen = False
        self.mp_client_gone = False
        self.mp_sending = False
        self.mp_turn_open = False
        self.mp_send_now.clear()
        self.mp_reopen.clear()
        self.mp_reopening = False
        self.mp_closed = False
        self.mp_probed = None
        self.mp_log_offered = False
        self.mp_seat_refused = False
        self._show_capture(None, 0.0)

    def _warning_note(self, store, civ, current):
        """The referee's missed-turns warning for `civ`, or None.

        The referee writes the warning on the turn the player is served next,
        `current` (`abandonment.enforce`'s `new_turn`); the turn before is also
        read when that has none. A missing note, an unreadable store or no
        turn yet all answer None.
        """
        if not civ or not _whole(current) or current < 1:
            return None
        for turn in (current, current - 1):
            try:
                found = abandonment_warning(store.note(civ, turn))
            except Exception:               # a readout is not worth an error path
                continue
            if found:
                return found
        return None

    def _mp_probe_closed(self, store):
        """Read the followed galaxy's state once, and say so if it has closed.

        Called on the worker thread when a turn could not be sent, and on its
        own thread when the wait between turns looks stopped, since a closed
        galaxy and a stopped one look the same from the loop. The answer goes
        to the UI thread through the message queue.
        """
        if store is None:
            return
        try:
            state = store.state()
        except Exception:                   # a readout is not worth an error path
            return
        problem = closed_problem(state)
        if problem:
            self.msgs.put(("__mp_closed__", store, problem))

    def _on_mp_closed(self, store, problem):
        """The followed galaxy has closed. Stop following it and say why.

        Runs on the UI thread. A report about a store that is no longer the one
        being followed is dropped, since Play on another galaxy has moved on.
        """
        if store is not self.mp_store or self.mp_closed:
            return
        self.mp_closed = True
        self.mp_note = GALAXY_ENDED_NOTE
        self.mp_stop = True
        self.say("multiplayer: this galaxy has been closed; the turn loop "
                 "stops here")
        self.refresh_games()
        self.warn(problem)

    def _on_mp_warning(self, store, lines):
        """Show a missed-turns warning the followed loop found. UI thread."""
        if store is not self.mp_store:
            return
        name = (self.mp_playing or {}).get("name") or ""
        self.say(f"multiplayer: the referee has warned {self.mp_civ} about "
                 f"missed turns")
        self.warn(warning_text(name, lines))

    def _on_log_offer(self, store):
        """A followed turn went wrong: offer to send the log. UI thread.

        Not for a store no longer followed, a galaxy found closed, or an
        install with no relay to send to.
        """
        if store is not self.mp_store or getattr(self, "mp_closed", False):
            return
        self.offer_log(LOG_WHY_TURN, quiet_if_nowhere=True)

    def offer_log(self, why: str = LOG_WHY_PLAYER,
                  quiet_if_nowhere: bool = False):
        """Write the redacted copy, say what it holds, and send it on Send.

        UI thread. "Read it first" opens exactly the file that is sent.
        """
        if getattr(self, "log_sending", False):
            self.say("log: one is already on its way")
            return
        playing = getattr(self, "mp_playing", None) or {}
        multiplayer_modules()           # puts fb_auth on sys.path in a checkout
        base = log_upload_base(self.data_dir, playing.get("store"),
                               getattr(self, "games_dir", None))
        try:
            path = write_redacted_log(self.data_dir)
        except OSError as exc:
            self.say(f"log: could not write the copy to send ({exc})")
            if not quiet_if_nowhere:
                self.warn(f"Could not write a copy of the log to send:\n\n"
                          f"{exc}")
            return
        if base is None:
            self.say(f"log: no relay to send to; the copy is at {path}")
            if not quiet_if_nowhere:
                self.warn(log_not_sent_text(None, path))
            return
        lead = LOG_TURN_LEAD if why == LOG_WHY_TURN else ""
        while True:
            what = self._choice_dialog(
                LOG_SEND_TITLE, lead + LOG_SEND_TEXT,
                (("Send", "send", True), ("Read it first", "read", False),
                 ("Not now", None, False)))
            if what != "read":
                break
            self._open_file(path)
        if what != "send":
            self.say("log: not sent")
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            self.warn(log_not_sent_text(str(exc), path))
            return
        payload = log_payload(
            text, build_info().get("build"), why,
            galaxy=playing.get("id"),
            civ=getattr(self, "mp_civ", None) if playing else None,
            turn=getattr(self, "mp_turn", None) if playing else None)
        token = player_token(self.data_dir)
        self.log_sending = True
        self.say(f"log: sending {len(text.encode('utf-8')):,} bytes "
                 f"({len(payload):,} on the wire)")

        def work():
            try:
                reply = send_log(base, token, payload)
                self.msgs.put(("__log_sent__", reply, None, path))
            except Exception as exc:        # said to the player either way
                said = relay_refusal(exc)
                self.msgs.put(("__log_sent__", None,
                               said[1] if said else str(exc), path))

        threading.Thread(target=work, daemon=True, name="send-log").start()

    def _on_log_sent(self, reply, err, path):
        """Say whether the log went. UI thread."""
        self.log_sending = False
        if err is None:
            ref = (reply or {}).get("id")
            self.say(f"log: sent as {ref}")
            shown = []
            if isinstance(ref, str) and ref.strip():
                try:
                    shown = record_log_ref(self.data_dir, ref.strip())[:1]
                except OSError as exc:
                    self.say(f"log: could not keep the reference ({exc})")
                    shown = [{"id": ref.strip(), "sent": ""}]
                self._show_log_refs_link()
            code = player_code(install_uid(self.data_dir))
            self._log_refs_dialog("Log sent", log_sent_text(reply, code),
                                  shown)
        else:
            self.say(f"log: not sent ({err})")
            self.warn(log_not_sent_text(err, path))

    def _show_log_refs_link(self):
        """Show the log history link once a reference is kept. UI thread."""
        if self.data_dir is None or not load_log_refs(self.data_dir):
            return
        self.view.show_log_refs_link()

    def show_log_refs(self):
        """The box listing the references kept, each with a Copy button."""
        refs = load_log_refs(self.data_dir)
        code = player_code(install_uid(self.data_dir))
        self._log_refs_dialog(LOG_REFS_TITLE, log_refs_text(code), refs)

    def _log_refs_dialog(self, title: str, body: str, refs):
        """A modal box like `_choice_dialog` with one row per reference.

        Each reference is in a read-only entry with its send time and a Copy
        button. Close, Escape and the window's own close dismiss it.
        """
        self.view.log_refs(title, body, [
            (rec["id"], log_ref_when(rec.get("sent", ""))) for rec in refs])

    def _open_file(self, path: str):
        """Open a file in the program Windows has for it, which for a .log is
        a text editor."""
        try:
            os.startfile(path)              # noqa: S606, Windows only
        except (OSError, AttributeError) as exc:
            self.say(f"log: could not open {path} ({exc})")

    def start_ai(self, mode, fresh_galaxy: bool = True):
        """Start the opponent, and say clearly if there is not one to start.

        `fresh_galaxy` is false only for Load, which keeps the opponent's
        discovered map; every other path starts the galaxy over.
        """
        import gamectl
        dropped = gamectl.reset_ai_state(
            os.path.join(self.data_dir, "ai_state"), mode["ai"]["civ"],
            keep_discovery=not fresh_galaxy)
        if dropped:
            self.say("cleared opponent state from an earlier game: "
                     + ", ".join(sorted(dropped)))
        try:
            self.ai_child = launch_ai(mode, self.data_dir)
        except OSError as exc:
            self.ai_child = None
            self.say(f"opponent failed to start: {exc}")
        if self.ai_child is None:
            self.say(f"opponent NOT started: {AI_EXE} was not found")
            self.warn(
                f"The computer opponent could not be started.\n\n{AI_EXE} is "
                "missing from the game folder, so the other empire will not "
                "take any turns.\n\nThe game is still playable, but there is "
                "nobody to play against. Downloading the full archive again "
                "should fix it.")
            return
        self.say(f"opponent playing {mode['ai']['civ']!r} "
                 f"(pid {self.ai_child.pid})")
        threading.Thread(target=self._pump_ai, args=(self.ai_child,),
                         daemon=True, name="ai-log").start()

    def _pump_ai(self, proc):
        """Tee the opponent's output into the log pane.
        """
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self.say_threadsafe("[ai] " + line)
        except (OSError, ValueError):
            pass        # the pipe closes when the process exits; nothing to say

    def stop_ai(self, why: str = ""):
        proc, self.ai_child = self.ai_child, None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            try:
                proc.kill()
            except OSError:
                pass
        self.say("opponent stopped" + (f" ({why})" if why else ""))

    def _show_controls(self, show: bool):
        self.view.show_controls(show)

    def _set_controls_enabled(self, enabled: bool):
        for key, _label in CONTROLS:
            self.view.set_control(key, enabled=enabled)

    def _in_background(self, label, work, done=None):
        """Run `work()` off the UI thread with the controls disabled meanwhile.

        Ending a turn and saving are slow, and this window is also the game's
        server, so it must keep answering while the game works.
        """
        self._ctl_busy = True
        self._set_controls_enabled(False)
        self.say(f"{label}…")

        def runner():
            try:
                result = work()
                err = None
            except Exception as exc:      # gamectl raises GameError; be broad
                result, err = None, exc
            self.msgs.put(("__ctl__", label, result, err, done))

        threading.Thread(target=runner, daemon=True, name="gamectl").start()

    def _client(self):
        import gamectl
        pid, _name = gamectl.find_client()
        if pid is None:
            raise gamectl.GameError("the game is not running")
        return gamectl.Client(pid)

    def _save_to_dat(self, c, name, out_for_turn):
        """SaveGame on client `c` and turn the server's capture into a .dat.

        `out_for_turn(turn)` names the file. Returns its path.
        """
        import gamectl
        started = time.time() - 1
        turn = c.turn()
        if not c.save_game(name):
            raise gamectl.GameError(
                "the game refused to save. This usually means the "
                "launcher's server is not reachable; keep this window "
                "open while you play.")
        # The engine's save goes out over HTTP; give the server a moment to
        # finish writing the capture before converting it.
        return gamectl.capture_to_dat(os.path.join(self.data_dir, "saves"),
                                      out_for_turn(turn), since=started,
                                      wait=5.0)

    def _autosaves(self) -> bool:
        """Whether Next Turn saves first: Single Player, which Play continues."""
        return (self.playing_now() is None
                and bool((self.running_mode or {}).get("continues")))

    def on_next_turn(self):
        import gamectl
        autosave = self._autosaves()
        dat_dir = os.path.join(self.data_dir, "games")

        def work():
            with self._client() as c:
                # Saved before the turn resolves: the opponent has finished
                # deciding (the button waits for it), so nothing else is
                # driving the client, and the save holds everything the player
                # did this turn, a confirmed setup prompt included.
                if autosave:
                    try:
                        os.makedirs(dat_dir, exist_ok=True)
                        path = self._save_to_dat(
                            c, AUTOSAVE_NAME,
                            lambda turn: autosave_path(dat_dir, turn))
                        prune_autosaves(dat_dir)
                        self.say_threadsafe(f"autosaved to {os.path.basename(path)}")
                    except gamectl.GameError as exc:
                        self.say_threadsafe(f"autosave failed, ending the turn "
                                            f"anyway: {exc}")
                return c.advance_turn(restore=gamectl.HOLD_LENGTH)
        self._in_background("ending the turn", work)

    def on_save(self):
        """Save in single player; send the turn now in multiplayer.

        In single player it writes a .dat the player can load again. During a
        multiplayer turn it asks the turn loop to send, since the loop is the
        only thing that calls SaveGame while it is running.
        """
        if self.playing_now() is not None:
            self.mp_send_now.set()
            self.say("multiplayer: sending your turn now")
            return

        name = self.view.ask_name(
            "Name this save. The turn number is added to the file name, so "
            "saving under the same name on a later turn keeps both.",
            self._save_name, SAVE_NAME_RULES, validate_save_name,
            title="Save game")
        if not name:
            return
        self._save_name = name

        dat_dir = os.path.join(self.data_dir, "games")
        os.makedirs(dat_dir, exist_ok=True)

        def work():
            with self._client() as c:
                return self._save_to_dat(
                    c, name,
                    lambda turn: os.path.join(dat_dir, f"{name}_turn{turn}.dat"))

        self._in_background("saving", work)

    def on_load(self):
        dat_dir = os.path.join(self.data_dir, "games")
        os.makedirs(dat_dir, exist_ok=True)
        path = self.view.ask_open_file(
            "Load a saved game", dat_dir,
            [("Saved games", "*.dat"), ("All files", "*.*")])
        if not path:
            return
        if not self.view.ask_ok_cancel(
                "Load game?",
                "Loading restarts the game on the saved galaxy.\n\nAnything "
                "since your last save will be lost. Continue?"):
            return
        mode = self.running_mode
        if not (mode and mode.get("exe")):
            self.warn("Load only applies to a game this launcher started "
                      "directly. A multiplayer turn comes from the galaxy's "
                      "turn store, not from a file on disk.")
            return
        self.say(f"loading {os.path.basename(path)}")
        self.stop_ai("loading a save")
        if self.child is not None:
            try:
                self.child.terminate()
                self.child.wait(timeout=10)
            except Exception:
                pass
            self.child = None
        self.running_mode = None
        try:
            self.child = subprocess.Popen([
                os.path.join(self.game_root, mode["exe"]), path],
                cwd=self.game_root)
        except OSError as exc:
            self.warn(f"Could not restart the game:\n\n{exc}")
            return
        self.running_mode = mode
        self.set_status(f"{_short(mode)} is running", OK)
        if mode.get("ai"):
            self.start_ai(mode, fresh_galaxy=False)

    def _on_control_done(self, label, result, err, done):
        if err is not None:
            self.say(f"{label} failed: {err}")
            self.warn(f"Could not finish {label}.\n\n{err}")
        else:
            if label == "saving":
                self.say(f"saved to {result}")
            elif label == "ending the turn":
                self.say(f"turn {result}")
            if done:
                done(result)
        self._ctl_busy = False
        self._set_controls_enabled(True)
        self._refresh_turn()

    def _opponent_ready(self, turn):
        """Has the opponent finished deciding the turn now showing?
        """
        import gamectl
        mode = self.running_mode or {}
        civ = (mode.get("ai") or {}).get("civ")
        if not civ or self.ai_child is None:
            return True, ""
        if turn is None:
            return True, ""
        done, stamp = gamectl.ai_pass_marker(
            os.path.join(self.data_dir, "ai_state"), civ)
        if done is not None and done >= turn:
            return True, ""
        # A stuck or crashed opponent never traps the player: after the
        # timeout the button comes back.
        waited = time.time() - (self._waiting_since or time.time())
        if waited > gamectl.AI_READY_TIMEOUT:
            # Kept short: this shares one line with the turn number.
            return True, f"{civ} not responding"
        return False, f"{civ} is thinking"

    def _mp_cut_off(self) -> bool:
        """Whether the loop's last read of the galaxy's store failed.

        While it did, the deadline on hand may be stale, so no stall is judged
        and the readouts say the galaxy cannot be reached.
        """
        return getattr(self, "mp_unreachable", None) is not None

    def _mp_stalled(self) -> float:
        """How long the galaxy being followed has been stopped, or 0.

        Only while the loop is between turns. Nothing here reads the store:
        the deadline comes from the loop's `playing` reports and the turn
        length was read when the galaxy was opened.
        """
        if (not self.mp_waiting or getattr(self, "mp_closed", False)
                or getattr(self, "mp_unreachable", None) is not None):
            return 0.0
        stopped = stalled_for(self.mp_deadline, self.mp_turn_seconds)
        if stopped and getattr(self, "mp_probed", None) != self.mp_deadline:
            # A closed galaxy and a referee that is off look the same here.
            # One state read per deadline, off the UI thread, tells them apart.
            self.mp_probed = self.mp_deadline
            threading.Thread(target=self._mp_probe_closed,
                             args=(self.mp_store,), daemon=True,
                             name="closed-probe").start()
        return stopped

    def _refresh_turn(self):
        """Update the turn readout
        """
        import gamectl
        if self.mp_store is not None:
            state, when = self.mp_capture or (None, time.time())
            # Once the client has closed, the capture indicator stays up while
            # the final submission is sending, then for CAPTURE_HOLD seconds.
            age = time.time() - when
            if self.mp_client_gone and not self.mp_sending:
                if age > CAPTURE_HOLD:
                    state = None
            self._show_capture(state, age, client_open=not self.mp_client_gone)
            if self.mp_client_gone:
                # With the client gone, the turn number and countdown are
                # replaced by what the launcher is doing. A stopped galaxy is
                # reported only over the wait; this launcher's own work
                # outranks it.
                stopped = self._mp_stalled()
                label = ("sending your turn" if self.mp_sending
                         else "opening the game again" if self.mp_reopening
                         else UNREACHABLE_NOTE if self._mp_cut_off()
                         else f"turns have stopped, due {fmt_ago(stopped)}"
                         if stopped
                         else self.mp_note or "waiting for the next turn")
                self.view.set_turn_label(label)
            else:
                # The clock belongs to the store; the client's own countdown
                # is held far in the future. This runs once a second, so it
                # counts down from the deadline the loop reported and does not
                # read the store.
                try:
                    if self.mp_turn is None or self.mp_deadline is None:
                        # Nothing heard from the loop yet. One read answers it
                        # and is kept.
                        self.mp_turn, self.mp_deadline = self.mp_store.current()
                    turn = self.mp_turn
                    left = self.mp_deadline - time.time()
                    label = f"turn {turn} \u00b7 {fmt_left(left)} left"
                    # An overdue turn says it has stopped, not "0:00 left".
                    stopped = stalled_for(self.mp_deadline,
                                          self.mp_turn_seconds)
                    if self._mp_cut_off():
                        label = f"turn {turn} \u00b7 {UNREACHABLE_NOTE}"
                    elif stopped:
                        label = (f"turn {turn} \u00b7 turns have stopped, due "
                                 f"{fmt_ago(stopped)}")
                    if self.mp_note:
                        label = f"turn {turn} \u00b7 {self.mp_note}"
                    self.view.set_turn_label(label)
                except Exception:
                    pass
            for key, _label in CONTROLS:
                # Next Turn and Load are off: the referee owns the clock. Save
                # means send this turn now (see on_save), live only while a
                # client is up.
                live = key == "save" and not self.mp_client_gone
                self.view.set_control(key, enabled=live)
            self._set_save_text("Send Turn")
            return
        self._set_save_text("Save")
        try:
            if self._ctl_client is None:
                pid, _n = gamectl.find_client()
                if pid is None:
                    return
                self._ctl_client = gamectl.Client(pid)
            t = self._ctl_client.turn()
            if t is None:
                self._close_ctl_client()
                return
            if t != self._last_seen_turn:
                self._last_seen_turn = t
                self._waiting_since = time.time()
            ready, why = self._opponent_ready(t)
            label = f"turn {t}" + (f" · {why}" if why else "")
            self.view.set_turn_label(label)
            # Only the turn button is gated; Save and Load do not advance the
            # clock.
            if not self._ctl_busy:
                self.view.set_control("turn", enabled=ready)
                # A turn happens only on Next Turn. Left alone, the engine
                # resolves one every hour of wall clock, and on waking from
                # sleep catches up on all it owes, none of them seen by the
                # opponent. It re-applies its own length at each boundary, so
                # this runs every tick, and not while Next Turn is driving it.
                was = self._ctl_client.hold_clock()
                pid = self._ctl_client.pid
                if was is not None and self._held_pid != pid:
                    self._held_pid = pid
                    self.say(f"turn clock held: the game advances only on "
                             f"Next Turn (its own length was {was}s)")
        except Exception:
            self._close_ctl_client()    # a readout is not worth an error path

    def _set_save_text(self, text: str):
        """Name what the Save button will actually do."""
        self.view.set_control("save", text=text)

    def _show_capture(self, state, age: float, client_open: bool = True):
        """Paint the capture indicator, or take it away when there is none.

        Removed, not blanked, so a launcher with no turn loop shows one dot
        and one status.
        """
        self.view.show_capture(*capture_readout(state, age, client_open))

    def _close_ctl_client(self):
        if self._ctl_client is not None:
            try:
                self._ctl_client.close()
            except Exception:
                pass
            self._ctl_client = None

    def on_close(self):
        """Ask before quitting out from under a running game.

        The warning is about the player's turn, which covers both single
        player and the multiplayer turn loop.
        """
        if running_clients(self.client_exes):
            if not self.view.ask_ok_cancel(
                    "Quit launcher?",
                    "A game is still running.\n\nClose your client first to "
                    "make sure your turn is saved.\n\nClose anyway?"):
                return
        self.stop_multiplayer("the launcher is closing")
        self.stop_ai("the launcher is closing")
        for srv in self.servers:
            try:
                srv.shutdown()
            except Exception:
                pass
        self.view.close()

    def say(self, msg: str):
        self.view.append_log(msg)
        self._persist(msg)

    def _open_log(self, data_dir: str):
        try:
            self._logfh = open(os.path.join(data_dir, "launcher.log"), "a",
                               buffering=1, encoding="utf-8")
        except OSError:
            return                      # the pane still works; the file is a bonus
        self._logfh.write(f"\n=== launcher started ===\n")
        for line in self._pending:
            self._logfh.write(line + "\n")
        self._pending.clear()

    def _persist(self, msg: str):
        import datetime
        line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
        if self._logfh is None:
            self._pending.append(line)
            return
        try:
            self._logfh.write(line + "\n")
        except (OSError, ValueError):
            pass

    def say_threadsafe(self, msg: str):
        """Called from any other thread. The window is drawn from one thread
        only, so the line goes through the queue that thread drains."""
        self.msgs.put(msg)

    def _watch_game(self):
        # The table's countdowns tick between listings.
        if self.page == "galaxies":
            self._tick_clocks()

        if self.child is not None and self.child.poll() is not None:
            name = _short(self.running_mode) if self.running_mode else "the game"
            code = self.child.returncode
            self.child = None
            self.running_mode = None
            self.say(f"{name} exited (code {code})")
            self.stop_ai("the game exited")
            self._close_ctl_client()
            self.view.show_modes(self)

        if self.ai_child is not None and self.ai_child.poll() is not None:
            code = self.ai_child.returncode
            self.ai_child = None
            self.say(f"opponent exited (code {code}), the other empire will "
                     f"not take any more turns")

        mp_live = self.mp_thread is not None and self.mp_thread.is_alive()
        if self.child is not None and self.running_mode is not None:
            self._status_if_changed(f"{_short(self.running_mode)} is running", OK)
        else:
            busy = running_clients(self.client_exes)
            if mp_live:
                self._watch_mp_client(bool(busy))
            if mp_live and self.mp_sending:
                self._status_if_changed(
                    "sending your turn, keep this window open", WARN)
            elif mp_live and self.mp_reopening:
                self._status_if_changed(
                    "Multiplayer: opening the game again", OK)
            elif mp_live and self.mp_client_gone:
                # Between turns with the game closed, the status line returns
                # to its ready state, unless the store cannot be reached or
                # the galaxy has stopped (in that order).
                stopped = self._mp_stalled()
                if self._mp_cut_off():
                    self._status_if_changed(UNREACHABLE_NOTE, WARN)
                elif stopped:
                    self._status_if_changed(
                        f"the galaxy has stopped; its turn was due "
                        f"{fmt_ago(stopped)}", WARN)
                else:
                    self._status_if_changed(*self._ready)
            elif mp_live:
                self._status_if_changed(
                    f"Multiplayer: {self.mp_civ}" if self.mp_civ
                    else "Multiplayer", OK)
            elif busy:
                self._status_if_changed(f"{self._name_for(busy)} is running", OK)
            else:
                self._status_if_changed(*self._ready)

        # The controls belong to a running game, and only to a mode that asked
        # for them: Tutorial and Demo have their own UI.
        if mp_live:
            # The controls act on a running client, so they are shown only
            # while the game window is up.
            self._show_controls(not self.mp_client_gone)
            self._refresh_turn()
            self.view.after(1000, self._watch_game)
            return
        self._show_capture(None, 0.0)
        wants = bool(self.running_mode and self.running_mode.get("controls")
                     and self.child is not None)
        self._show_controls(wants)
        if wants:
            self._refresh_turn()
        self.view.after(1000, self._watch_game)

    def _watch_mp_client(self, up: bool):
        """Notice the multiplayer client coming and going.

        The turn loop starts its own client, so this watches the process list.
        When the player closes the game window the readout goes at once, while
        the final capture and submission carry on.
        """
        if up:
            self.mp_client_seen = True
            self.mp_client_gone = False
            # The game is up, so whatever asked for it has been answered.
            self.mp_reopening = False
            return
        if self.mp_client_seen:
            self.mp_client_seen = False
            self.mp_client_gone = True
            # The last capture and submission run on the worker thread;
            # sending is shown until the loop reports the turn finished.
            if self.mp_turn_open:
                self.mp_sending = True
                self.say("multiplayer: the game has closed, sending your turn")
            else:
                # The turn had already finished, so nothing is on its way.
                self.say("multiplayer: the game has closed")

    def _name_for(self, exe_names) -> str:
        """
        What to call a game we did not launch, knowing only its EXE.

        CosmicSupremacy.exe backs both Tutorial and Demo, so when the mapping is
        ambiguous say "A game" rather than confidently name the wrong one.
        """
        shorts = {_short(m) for m in self.modes if m.get("exe") in exe_names}
        return shorts.pop() if len(shorts) == 1 else "A game"

    def _status_if_changed(self, text: str, colour: str):
        """Repaint only on a real change; this runs once a second."""
        if self._status[0] != text:
            self.set_status(text, colour)

    def _drain(self):
        try:
            while True:
                msg = self.msgs.get_nowait()
                # Background game-control results arrive on the same queue as
                # log lines, tagged, so their completion runs on the UI thread
                # where it is allowed to touch widgets.
                if isinstance(msg, tuple) and msg and msg[0] == "__ctl__":
                    _tag, label, result, err, done = msg
                    self._on_control_done(label, result, err, done)
                elif isinstance(msg, tuple) and msg and msg[0] == "__games__":
                    _tag, rows, extra, err = msg
                    self._on_games(rows, extra, err)
                    # A listing is often the first sign-in.
                    self._show_player_code()
                elif isinstance(msg, tuple) and msg and msg[0] == "__mp_closed__":
                    _tag, store, problem = msg
                    self._on_mp_closed(store, problem)
                elif isinstance(msg, tuple) and msg and msg[0] == "__mp_warning__":
                    _tag, store, lines = msg
                    self._on_mp_warning(store, lines)
                elif isinstance(msg, tuple) and msg and msg[0] == "__mp_played__":
                    _tag, gid, turn = msg
                    self._on_mp_played(gid, turn)
                elif isinstance(msg, tuple) and msg and msg[0] == "__mp_refused__":
                    self.warn(msg[1])
                elif isinstance(msg, tuple) and msg and msg[0] == "__log_offer__":
                    self._on_log_offer(msg[1])
                elif isinstance(msg, tuple) and msg and msg[0] == "__log_sent__":
                    _tag, reply, err, path = msg
                    self._on_log_sent(reply, err, path)
                else:
                    self.say(msg)
        except queue.Empty:
            pass
        self.view.after(120, self._drain)

    def set_status(self, text: str, colour: str):
        self._status = (text, colour)
        self.view.set_status(text, colour)

    def set_ready_status(self, text: str, colour: str):
        """Set the status *and* remember it as the no-game-running state."""
        self._ready = (text, colour)
        self.set_status(text, colour)

    def warn(self, msg: str):
        self.view.warn(msg)

    def fail(self, msg: str):
        self.set_status("cannot start", BAD)
        self.view.disable_modes()
        self.view.error(msg)


def webview2_bootstrapper() -> "str | None":
    """Microsoft's WebView2 installer: packed into a release, or the copy
    build.ps1 keeps in release\\build in a checkout."""
    import webview_runtime
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (bundled(webview_runtime.BOOTSTRAPPER),
                 os.path.join(here, "build", webview_runtime.BOOTSTRAPPER)):
        if os.path.exists(path):
            return path
    return None


def main() -> int:
    # web_view imports this module by name. Run as a script it is __main__,
    # and without this the import would load a second copy beside it.
    sys.modules.setdefault("launcher", sys.modules[__name__])
    with open(bundled("manifest.json"), "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    set_app_user_model_id()     # must precede the first window
    import webview_runtime
    from web_view import WebView
    if not webview_runtime.ensure(cfg["product"], webview2_bootstrapper()):
        return 1
    view = WebView(cfg)
    view.run(lambda: Launcher(view, cfg))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A windowed build has no console. A plain Windows box, since the
        # window that failed may be the reason.
        detail = traceback.format_exc()
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, "The launcher failed to start.\n\n" + detail,
                "Cosmic Supremacy: Resurgence", 0x10)
        except Exception:
            pass
        sys.exit(1)
