"""
launcher.py , the player-facing front door for Cosmic Supremacy: Resurgence
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

CREATE_NO_WINDOW = 0x08000000
APP_DIRNAME = "CosmicSupremacyResurgence"


# ── Layout discovery ──────────────────────────────────────────────────────────
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
    # The checkout layout only applies when running from source. Frozen,
    # _repo_root() is derived from a temp extraction path and names a directory
    # that cannot exist , listing it in the not-found message is pure confusion.
    if not getattr(sys, "frozen", False):
        cands.append(os.path.join(_repo_root(), "client"))
    return cands


def find_game_root(modes) -> "str | None":
    """First directory that holds every EXE the manifest asks for.

    A mode with a "session" names no EXE, because whatever runs the session
    chooses the build. Asking for its EXE would decide the whole install is
    broken over a file the manifest never claimed existed.
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
    Can this mode actually be launched today?

    A placeholder , "enabled": false, or simply no EXE named , is shown as a
    greyed-out card advertising what is coming. It must not be counted when
    looking for the game files, or a release that ships no multiplayer client
    would decide the whole install is broken.

    A mode with a "session" names something that starts the game itself rather
    than being an EXE plus a pass file. Multiplayer is one: the turn loop writes
    the .dat for the turn and chooses the build, so there is no galaxy file to
    name and naming one would be a fiction.
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
    Give Windows an explicit taskbar identity.

    Without one, a process that loads the Python DLL can be grouped under the
    interpreter's identity, and the taskbar button then shows the interpreter's
    icon no matter what the window or the EXE carries. Setting this before the
    first window is created makes the taskbar button ours.
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


# ── Server ────────────────────────────────────────────────────────────────────
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
    """True if whatever holds the port speaks our protocol (a dev server, say)."""
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


def start_server(host: str, port: int, data_dir: str, galaxy_dir: str, sink):
    """
    Import cs_server and serve it on daemon threads. Returns (servers, logfile).

    cs_server reads its environment at import time , PORT, and the DATA_DIR that
    the log, saves\\ and governor blobs hang off , so the environment has to be
    set before the import, not after. Hence the import sitting inside this
    function rather than at module scope.
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

    # Tee the server's own logging into the launcher's log pane. Every call site
    # in cs_server goes through the module global, so rebinding it here catches
    # them all, including _log_unknown_action.
    inner = cs_server.log
    def tee(msg: str):
        inner(msg)
        sink(msg)
    cs_server.log = tee

    # ── Listen on BOTH loopback addresses ────────────────────────────────────
    # The client connects to the name "localhost", and Windows resolves that to
    # ::1 before 127.0.0.1.
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
            # A host with IPv6 disabled still works over IPv4, just slower to
            # make the first connection. Not worth failing the launch.
            sink(f"ipv6 loopback listener unavailable ({exc})")

    return servers, cs_server.LOGFILE


# ── Client process ────────────────────────────────────────────────────────────
def _process_names_via_api() -> "list[str] | None":
    """
    Every running process name, from the toolhelp snapshot API.

    The status watcher asks once a second, and spawning tasklist.exe at that
    rate to read a list the kernel will hand over directly is wasteful. Returns
    None if the API is unavailable so the caller can fall back.
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

    --stall 0 disables the no-turn-in-N-seconds bail

    --vision known keeps the AI under the same fog of war the player is under.
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


def launch_mode(mode, game_root: str, galaxy_root: str) -> subprocess.Popen:
    exe = os.path.join(game_root, mode["exe"])
    galaxy = os.path.join(galaxy_root, mode["galaxy"])
    if not os.path.exists(exe):
        raise FileNotFoundError(exe)
    if not os.path.exists(galaxy):
        raise FileNotFoundError(galaxy)
    # cwd is the EXE's own folder: the client writes save_game_*.dat relative to
    # it, and that is the arrangement every dev tool has been exercised against.
    return subprocess.Popen([exe, galaxy], cwd=game_root)


# ── Player identity ───────────────────────────────────────────────────────────
# One name, typed once. It is who this launcher says you are and it is the name
# of your civilisation in a galaxy, because those are the same thing until there
# are accounts to tell them apart.
#
# It lives in the data directory, not in manifest.json: the manifest ships to
# everyone and an upgrade replaces it, while the data directory is this player's
# and survives.
IDENTITY_FILE = "identity.json"

# The engine's civilisation name buffer. make_multiplayer_galaxy.py enforces the
# same ceiling when it builds a roster, so a name that fits here fits a seat.
NAME_LIMIT = 15

# Characters Windows refuses in a file name. A civ name is one: TurnStore keeps
# a player's orders in submissions\<turn>\<civ>.b64, so a name holding any of
# these either writes somewhere else or does not write at all.
NAME_FORBIDDEN = r'\/:*?"<>|'

NAME_RULES = (f"Up to {NAME_LIMIT} characters. Letters, digits and ordinary "
              f"punctuation, but not {' '.join(NAME_FORBIDDEN)}")


def validate_player_name(raw):
    """(name, None) for a name that can be used, or (None, why not).

    The reason is written for the player, because it is shown next to the field
    they are typing in. Surrounding spaces are trimmed rather than rejected,
    which is the one correction a player never wants to be told about.
    """
    name = (raw or "").strip()
    if not name:
        return None, "Enter a name."
    if len(name) > NAME_LIMIT:
        return None, (f"That is {len(name)} characters. The game's civilisation "
                      f"name holds {NAME_LIMIT}.")
    if any(not (" " <= c <= "~") for c in name):
        return None, ("Letters, digits and ordinary punctuation only. The "
                      "game's name field is ASCII.")
    bad = sorted({c for c in name if c in NAME_FORBIDDEN})
    if bad:
        return None, ("A name cannot contain " + " ".join(bad) + ", because it "
                      "is also the name of the file a galaxy keeps your orders "
                      "in.")
    if name.endswith("."):
        return None, "A name cannot end in a full stop."
    return name, None


def identity_path(data_dir: str) -> str:
    return os.path.join(data_dir, IDENTITY_FILE)


def load_identity(data_dir: str):
    """The name this player entered, or None.

    A file that is missing, unreadable or holds a name the rules reject counts
    as no name at all. Asking again is cheap; submitting a whole turn under a
    name no galaxy can accept is not.
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
    """The `civ` a player hand-wrote into multiplayer.json before this existed.

    Taken as typed, without the rules above. Those rules govern what a player
    may enter; a name already sitting in a live galaxy's roster is that galaxy's
    fact, and rejecting it here would lock a beta player out of a game they are
    part way through. Only the length ceiling and the file name characters are
    checked, because those are the two ways a name breaks rather than displeases.
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

    Before the launcher asked, a player hand-wrote `civ` into multiplayer.json.
    Upgrading must not make them type it again, so the first run after the
    upgrade copies it across and from then on there is one place it is typed.
    The `civ` key is left in the file rather than removed, so downgrading to a
    launcher that still requires it works.
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


def roster_problem(name: str, civs):
    """Why this name cannot play this galaxy, or None. Written for the player.

    The other players' names are not in the answer. A launcher pointed at a
    store can read the whole roster, so printing it would let anyone who can
    reach a galaxy list who is in it, and someone who has mistyped their own
    name has no need to know that. What they get instead is how many seats the
    galaxy has, which says whether they are at the wrong galaxy or merely
    spelling their own name differently, and names nobody.

    A seat that differs only in case is the exception, because the name it
    echoes back is the one the player just typed. It reveals nobody new, and it
    is the mistake a player makes when they were told their name out loud.
    """
    seats = list(civs)
    if name in seats:
        return None
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


# ── Which build this is ───────────────────────────────────────────────────────
# A build has to be able to name itself. A galaxy refuses one that is too old
# (version_problem below), and a log from a failure nobody watched is worth much
# less if it does not say which build wrote it.
#
# The name is stamped at package time rather than kept as a constant here,
# because the packaging step is what decides it: build.ps1 takes the version
# from its -Version switch, or from manifest.json when the switch is not given.
# The switch is why dist\ holds a CosmicSupremacy-Resurgence-v0.1.1 that
# manifest.json never mentioned, so the manifest on its own cannot answer which
# build a player is running.
#
# stamp_build.py writes that decision to build.json and the spec packs it into
# the frozen build beside manifest.json, which is the route manifest.json and
# cosmic.ico already take: a data file read back through bundled().
#
# A build with no build.json reports the manifest's version with a marker, so a
# checkout and an unstamped package are never mistaken for a release.
BUILD_FILE = "build.json"
DEV_MARK = "dev"                # run from a clone; no packaging step involved
UNSTAMPED_MARK = "unstamped"    # packaged, but the stamp did not reach the bundle

# Where a launcher that is too old is told to go. The site's download page and
# this link both resolve to the same GitHub release asset.
UPDATE_URL = "https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/releases/latest"

# The key a galaxy's state carries its minimum build in. Optional: a galaxy
# without one is played by any build, which is every galaxy that predates it.
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

    Only the leading dotted number counts. What follows it says how a build was
    made rather than how new it is, and ordering on it would put a stamped
    0.1.1 and a development 0.1.1 on opposite sides of a gate neither is meant
    to be caught by.
    """
    lead = re.match(r"\d+(?:\.\d+)*", (build or "").strip())
    if lead is None:
        return ()
    return tuple(int(p) for p in lead.group(0).split("."))


def build_is_below(build: str, minimum: str) -> bool:
    """Is `build` older than `minimum`?

    Shorter names are padded with zeros, so 0.2 and 0.2.0 are the same build. A
    minimum with no number in it orders nothing and holds nobody back; a build
    with no number in it is below every minimum, because a launcher that cannot
    say what it is cannot claim to be current.
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

    The minimum is `min_build` in the galaxy's state, and it is optional: a
    galaxy that names none is played by any build.

    The message names both builds and where the new one is, because the failure
    it prevents is a silent one. A turn resolved by a client the referee does
    not match produces a blob whose symptom is a mystery rather than an error,
    and a player told only "no" has nothing to act on.

    A minimum that cannot be read as a build is not enforced. That is the
    referee's mistake, and a typo in one galaxy's state must not shut every
    player out of it.
    """
    minimum = (state or {}).get(MIN_BUILD_KEY)
    if not isinstance(minimum, str) or not build_parts(minimum):
        return None
    if not build_is_below(build, minimum):
        return None
    return (f"This galaxy needs build {minimum} or newer.\n\nThis launcher is "
            f"build {build}.\n\nAn older build can hand the galaxy a turn the "
            "referee cannot use, and that goes wrong quietly, so the galaxy "
            "stops here instead.\n\nThe current release is at\n" + UPDATE_URL)


# ── Multiplayer ───────────────────────────────────────────────────────────────
# A multiplayer galaxy has a turn store, which is the one thing a player's
# launcher, the other players' launchers and the referee all agree through. The
# store says which turn is current and when it is due; the launcher's job is to
# make the turn boundary invisible. The .dat push path is startup-only, so the
# client has to restart every turn, and the only way that is acceptable is if
# nobody has to think about it.
#
# The turn machinery ships inside the frozen build: build.ps1 names
# player_turn, turn_store and the modules they reach from inside functions as
# hidden imports, and copies the player client into game\ alongside the
# single-player one. multiplayer_modules() imports them directly when frozen
# and off sys.path when run from a clone; bind_multiplayer_paths() then points
# them at the player's folders, because every one of them derives its paths
# from __file__ and __file__ in a frozen build is a temporary directory.
MP_CONFIG = "multiplayer.json"

# What the turn loop may start. Not in the manifest: the loop picks the build
# itself, and a release does not ship the player build yet.
MP_CLIENTS = ("CosmicSupremacy_Player.exe", "CosmicSupremacy_TestBed.exe")


def multiplayer_config(data_dir: str):
    """{"store": <dir or URL>} for this galaxy, or None.

    Kept in the data directory rather than the manifest because it is per
    player and per galaxy: the manifest ships to everyone, and which galaxy you
    joined is yours.

    Only `store` is required. `civ` used to be, and a file that still carries
    one is read once by `legacy_civ` to seed the player's name, after which the
    name comes from identity.json and this file names only the galaxy.
    """
    path = os.path.join(data_dir, MP_CONFIG)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return None
    if not cfg.get("store"):
        return None
    return cfg


def multiplayer_modules():
    """The turn machinery, or None if this build cannot reach it.

    Frozen, the modules are in the bundle and the checkout directories do not
    exist, so the import is direct. From a clone they are found by putting
    their four directories on sys.path first. A build that was frozen without
    them still imports cleanly as a launcher, so the failure is reported rather
    than raised: the rest of the launcher works.
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


def bind_multiplayer_paths(game_root: str, data_dir: str):
    """Point the turn machinery at the folders this player actually has.

    game_cycle and player_turn compute their paths from __file__ at import
    time. In a frozen build __file__ names the directory PyInstaller unpacks
    into, which holds no client executables and is deleted when the launcher
    exits, so every one of those paths is wrong. They are rebound here: the
    clients and the client working directory come from the game folder, and
    anything written during a turn goes to the data directory, which is the
    same place the stub server keeps its saves.

    Done in a checkout too. There the game folder is client\\ and the paths
    agree, except for the saves directory: the launcher runs the server against
    its own data directory rather than the checkout's server\\saves.
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
    # serve() writes each turn's .dat under HERE before handing it to the
    # client, and the client has to be able to read it after the launcher that
    # wrote it is gone.
    player_turn.HERE = data_dir


def fmt_left(seconds: float) -> str:
    """A countdown a player can read at a glance."""
    seconds = int(max(0, seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    return f"{seconds // 60}:{seconds % 60:02d}"


# ── The log, made fit to send ─────────────────────────────────────────────────
# launcher.log is the only account of a failure nobody was watching, and it
# cannot be sent anywhere as it stands. The launcher tees the stub server's own
# logging into it, and that includes the HTTP bodies of the save protocol: a
# save blob is base64 in the `data=` field and arrives in 400-character chunks
# tagged `body+`. cs_server holds a savegame body to its first 4000 characters,
# so it is around 4 KB per save request rather than the whole blob, and a
# multiplayer turn makes two of those. They diagnose nothing , the same blob is
# written to saves\ in full , and they are still most of the file: 12,744 bytes
# of the 23,310-byte log this was measured against, from three requests.
#
# redact_log_text() produces the copy that can be sent: body dumps replaced by
# a count, the player's Windows account name taken out of the paths, and the
# tail kept to a cap.
#
# WHAT THE REDACTED COPY STILL CONTAINS. Written out rather than summarised,
# because the text a player is shown before they agree to send one has to be
# written from this list rather than from a guess.
#
#   , The player's name, which is also their civilisation's name in a galaxy.
#     It is on every multiplayer line: "[Alice] turn 12: submitted".
#   , The galaxy: the store's path or URL exactly as multiplayer.json names it.
#     For a directory store on another machine that is a UNC path and therefore
#     that machine's name. The scrubbing covers this player's own account name,
#     not a referee's machine.
#   , The game name, turn number and action of each save request, from the head
#     of the body that is kept: "gamename='DemoPlayt8'&turn=8&version=1". A
#     `passhash` sits there too and is always empty; the engine sends a
#     credential field that the stub server does not use.
#   , Which modes were started and which exited, with exit codes, process ids
#     and the names of the game executables.
#   , The install's directory layout with the account name replaced:
#     "C:\Users\<user>\Desktop\...". The rest of each path survives, so a folder
#     a player named after themselves is still readable.
#   , The whole of the AI opponent's reasoning in single player, which names
#     planets, ships, research and engine addresses, and nothing about the
#     machine it ran on.
#   , Timestamps, to the second, of all of the above.
LOG_NAME = "launcher.log"
REDACTED_LOG_NAME = "launcher-redacted.log"

# The ceiling on a copy meant to be sent. Several turns of a multiplayer
# session fit once the body dumps are gone, and it is small enough to leave a
# home connection without anyone thinking about it.
LOG_UPLOAD_CAP = 256 * 1024

# Room held back from the cap for the line that says what the cap dropped.
_CAP_NOTE = 200

# A line as launcher.log writes it: the stub server's own indented text with
# the launcher's timestamp in front of it.
_STAMP = r"(?:\[\d\d:\d\d:\d\d\]\s*)?\s*"

# The continuation chunks of a request body, and the line that closes a
# truncated one. cs_server tags chunk 0 `body` and every chunk after it `body+`.
_BODY_MORE = re.compile(_STAMP + r"body(?:\+|\u2026)\s*[:(]")

# Chunk 0, which is worth keeping as far as `data=`: ahead of that field it
# carries the user, the game, the turn and the version. After it is the blob.
_BODY_HEAD = re.compile("(" + _STAMP + r"body:\s*.*?\bdata=)(.+)$")

# Any \Users\<name>\ in a path. The account name is the identifying part; the
# rest of the path says where the install is, which is what a diagnostic needs.
# Public and Default are Windows' own shared profiles and name nobody.
_USER_DIR = re.compile(r"([A-Za-z]:[\\/]+Users[\\/]+)(?!Public\b|Default\b)"
                       r"([^\\/\s\"']+)", re.I)
USER_MARK = "<user>"


def scrub_paths(text: str) -> str:
    """Replace the Windows account name in any path with <user>.

    Generalised rather than matched against this machine's account name. The
    copy is written on the player's machine and read on somebody else's, and a
    log can carry paths from a second profile or an older install that the
    account name in force right now would not match.

    A profile directory that is not under \\Users , a redirected domain profile,
    say , is matched by its own literal path instead.
    """
    text = _USER_DIR.sub(lambda m: m.group(1) + USER_MARK, text)
    home = os.path.expanduser("~")
    if home and _USER_DIR.match(home) is None:
        parent, leaf = os.path.split(home)
        if parent and leaf:
            masked = os.path.join(parent, USER_MARK)
            text = re.sub(re.escape(home), lambda m: masked, text, flags=re.I)
    return text


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

    Each run of body lines becomes one line naming how many bytes went with it.
    The count is kept rather than dropped because it is the one thing those
    lines were ever good for: it separates a turn that sent a 300 KB save from
    a turn that sent nothing, and that distinction is a diagnosis.

    The cap keeps the tail, because the failure being reported is the last
    thing that happened, and the line saying what was dropped goes at the top.
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
            out.append(scrub_paths(head.group(1)) + "\u2026")
            elided += len(head.group(2).encode("utf-8", "replace"))
            continue
        if _BODY_MORE.match(line) is not None:
            elided += len(line.encode("utf-8", "replace"))
            continue
        close_run()
        out.append(scrub_paths(line))
    close_run()

    body = "\n".join(out)
    if body:
        body += "\n"
    return _keep_tail(body, cap)


def redacted_log(data_dir: str, cap: int = LOG_UPLOAD_CAP) -> str:
    """The uploadable copy of this install's launcher.log.

    A missing or unreadable log answers with a copy that says so rather than
    raising. Whatever asks for this wants something to send either way, and
    "there was no log" is itself a report.
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

    Nothing sends it. The file is the deliverable: a player can be pointed at
    it and asked to attach it, and whatever uploads it later reads this file
    rather than redacting a second time in its own way.
    """
    out_path = out_path or os.path.join(data_dir, REDACTED_LOG_NAME)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(redacted_log(data_dir, cap))
    return out_path


# ── UI ────────────────────────────────────────────────────────────────────────
BG      = "#050a1a"
PANEL   = "#080f22"
EDGE    = "#1a3a6a"
TEXT    = "#a0c8ff"
DIM     = "#6090b0"
FAINT   = "#405070"
ACCENT  = "#00aaff"
BTN     = "#0055cc"
BTN_HI  = "#0077ff"
OK      = "#3ddc84"
WARN    = "#ffb020"
BAD     = "#ff5555"


class Launcher:
    def __init__(self, root, cfg):
        import tkinter as tk
        self.tk = tk
        self.root = root
        self.cfg = cfg
        self.modes = [m for m in cfg["modes"] if m.get("show", True)]
        self.msgs: "queue.Queue[str]" = queue.Queue()
        self.servers: "list" = []
        self.child: "subprocess.Popen | None" = None
        # The opponent. Tied to self.child's lifetime in both directions: it is
        # started right after the game and killed the moment the game is gone,
        # because an AI writing into a process that has exited is at best noise
        # in the log and at worst an attach to whatever gets that pid next.
        self.ai_child: "subprocess.Popen | None" = None
        # A cached handle on the running game, for the once-a-second turn
        # readout. Dropped whenever a read fails or the game exits.
        self._ctl_client = None
        # When the turn showing now first appeared, so a stuck opponent can be
        # timed out rather than locking the player out of their own game.
        self._waiting_since = None
        self._last_seen_turn = None
        self._ctl_busy = False
        self.running_mode = None
        # The multiplayer session: a worker thread running the turn loop, the
        # store it follows, and the flag that asks it to stop between turns
        # rather than mid-capture.
        self.mp_thread = None
        self.mp_store = None
        self.mp_civ = None
        self.mp_stop = False
        self.mp_note = ""
        self.mp_turn = None
        # This player's name, read from the data directory at boot. None until
        # they have entered one, which is a state the launcher runs in happily:
        # only multiplayer needs to know who you are.
        self.player = None
        # The status to fall back to whenever no game is running. Recorded when
        # the server settles so that a game exiting restores whatever was true
        # then , "server running", "port taken", "reusing the existing server" ,
        # rather than a guess.
        self._ready: "tuple[str, str]" = ("starting…", WARN)
        self.log_visible = False
        self.buttons: "list" = []
        self.game_root = self.galaxy_root = self.data_dir = None
        # Every client the manifest knows about, including modes not shown: a
        # hidden mode's client is still a running game that a second launch
        # would kill.
        self.client_exes = {m["exe"] for m in cfg["modes"] if m.get("exe")}
        # The multiplayer turn loop starts its own client, which the manifest
        # therefore does not name. It still has to count as a running game, or
        # a second launch would close someone's turn from under them.
        self.client_exes |= set(MP_CLIENTS)
        # Diagnostics start before the data directory is known , where the game
        # was found, and whether it was found at all, are exactly the lines a
        # failed startup needs to leave behind , so they buffer until there is a
        # file to put them in.
        self._logfh = None
        self._pending: "list[str]" = []

        root.title(cfg["product"])
        root.configure(bg=BG)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build()
        self._boot()
        self.root.after(120, self._drain)
        self.root.after(1000, self._watch_game)

    # ── construction ──
    def _build(self):
        tk = self.tk
        pad = {"padx": 26}

        # One line, from the manifest, so the header, the title bar and every
        # dialog title are the same string rather than three things to keep in
        # step. 20pt rather than 22: the full name is 28 characters and the
        # window sizes itself to its widest child.
        tk.Label(self.root, text=self.cfg["product"], bg=BG, fg=ACCENT,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w", pady=(22, 0), **pad)
        # The build, not the manifest's version: a build made with build.ps1's
        # -Version switch carries a name the manifest does not hold, and this
        # line is where a player reads back what a galaxy is about to refuse.
        tk.Label(self.root, text=f"v{build_id()}",
                 bg=BG, fg=DIM, font=("Segoe UI", 10)).pack(anchor="w", **pad)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="x", pady=(18, 4), **pad)

        for mode in self.modes:
            card = tk.Frame(body, bg=PANEL, highlightbackground=EDGE,
                            highlightthickness=1)
            card.pack(fill="x", pady=5)
            ready = is_playable(mode)
            btn = tk.Button(card, text=mode["title"], width=18,
                            bg=BTN if ready else PANEL,
                            fg="#ffffff" if ready else FAINT,
                            activebackground=BTN_HI, activeforeground="#ffffff",
                            relief="flat", bd=0,
                            font=("Segoe UI", 10, "bold"),
                            cursor="hand2" if ready else "",
                            state="normal" if ready else "disabled",
                            disabledforeground=FAINT,
                            command=lambda m=mode: self.on_play(m))
            btn.pack(side="left", padx=14, pady=14)
            if ready:
                btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=BTN_HI))
                btn.bind("<Leave>", lambda e, b=btn: b.configure(bg=BTN))
                # Only playable buttons go in the list fail() disables , a
                # coming-soon button is already disabled and must stay that way.
                self.buttons.append(btn)
            else:
                # Packed before the blurb so the right-hand side claims its
                # width first and the blurb wraps around it.
                tk.Label(card, text=" COMING SOON ", bg=EDGE, fg=TEXT,
                         font=("Segoe UI", 7, "bold")).pack(side="right",
                                                            padx=(0, 14))
            tk.Label(card, text=mode["blurb"], bg=PANEL,
                     fg=DIM if ready else FAINT, justify="left",
                     wraplength=340, font=("Segoe UI", 9)).pack(
                         side="left", padx=(0, 14))

        self.ctl_frame = tk.Frame(self.root, bg=PANEL,
                                  highlightbackground=EDGE, highlightthickness=1)
        inner = tk.Frame(self.ctl_frame, bg=PANEL)
        inner.pack(fill="x", padx=12, pady=10)
        self.ctl_buttons = {}
        for key, text, cmd in (("load", "Load", self.on_load),
                               ("save", "Save", self.on_save),
                               ("turn", "Next Turn", self.on_next_turn)):
            # Packed right-to-left, so they READ "Next Turn  Save  Load".
            b = tk.Button(inner, text=text, bg=BTN, fg="#ffffff",
                          activebackground=BTN_HI, activeforeground="#ffffff",
                          relief="flat", bd=0, cursor="hand2", width=10,
                          font=("Segoe UI", 9, "bold"), command=cmd)
            b.pack(side="right", padx=(6, 0))
            self.ctl_buttons[key] = b
        self.turn_label = tk.Label(inner, text="turn ,", bg=PANEL, fg=TEXT,
                                   font=("Segoe UI", 9, "bold"), width=34,
                                   anchor="w")
        self.turn_label.pack(side="left", fill="x")

        tk.Label(self.root,
                 text="Keep this window open while you play , it is the game server.",
                 bg=BG, fg=FAINT, font=("Segoe UI", 8)).pack(anchor="w",
                                                             pady=(6, 0), **pad)

        ident = tk.Frame(self.root, bg=BG)
        ident.pack(fill="x", pady=(10, 0), **pad)
        tk.Label(ident, text="Playing as", bg=BG, fg=FAINT,
                 font=("Segoe UI", 8)).pack(side="left")
        self.name_label = tk.Label(ident, text="", bg=BG, fg=TEXT,
                                   font=("Segoe UI", 9, "bold"))
        self.name_label.pack(side="left", padx=(6, 0))
        self.name_btn = tk.Label(ident, text="set name", bg=BG, fg=FAINT,
                                 font=("Segoe UI", 8, "underline"),
                                 cursor="hand2")
        self.name_btn.pack(side="left", padx=(8, 0))
        self.name_btn.bind("<Button-1>", lambda e: self.on_change_name())

        status = tk.Frame(self.root, bg=BG)
        status.pack(fill="x", pady=(12, 0), **pad)
        self.dot = tk.Label(status, text="●", bg=BG, fg=WARN,
                            font=("Segoe UI", 11))
        self.dot.pack(side="left")
        self.status = tk.Label(status, text="starting…", bg=BG, fg=DIM,
                               font=("Segoe UI", 9))
        self.status.pack(side="left", padx=(6, 0))
        self.log_btn = tk.Label(status, text="show log", bg=BG, fg=FAINT,
                                font=("Segoe UI", 8, "underline"), cursor="hand2")
        self.log_btn.pack(side="right")
        self.log_btn.bind("<Button-1>", lambda e: self.toggle_log())

        self.log_frame = tk.Frame(self.root, bg=BG)
        self.log = tk.Text(self.log_frame, height=11, bg="#02060f", fg=DIM,
                           insertbackground=TEXT, relief="flat",
                           font=("Consolas", 8), wrap="none",
                           highlightbackground=EDGE, highlightthickness=1)
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

        tk.Frame(self.root, bg=BG, height=18).pack()

    # ── startup ──
    def _boot(self):
        self.data_dir = find_data_dir()
        self._open_log(self.data_dir)
        info = build_info()
        self.say(f"{self.cfg['product']} v{info['build']}")
        # When and from what the build was packaged. Absent from a checkout and
        # from a package the stamp did not reach, which is itself the answer to
        # "which build produced this log".
        made = " ".join(f"{k} {info[k]}" for k in ("stamped_at", "commit")
                        if info.get(k))
        if made:
            self.say(f"build   {made}")
        self.say(f"data    {self.data_dir}")

        self.player = player_name(self.data_dir)
        self._show_identity()
        self.say(f"player  {self.player or 'not set yet'}")

        icon = find_icon(self.data_dir)
        if icon:
            try:
                # default= applies to this window and every dialog it spawns,
                # so the message boxes carry the icon too.
                self.root.iconbitmap(default=icon)
                self.say(f"icon    {icon}")
            except Exception as exc:
                self.say(f"icon    not applied ({exc})")
        else:
            self.say("icon    none found")

        # Only the modes actually offered gate startup. A hidden mode whose EXE
        # the build no longer ships is a stale manifest row, not a broken install.
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
                "one in dist\\ instead , that is the complete release. If you "
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
            if stub_server_answers(host, port):
                self.set_ready_status(f"using the server already on {host}:{port}", OK)
                self.say(f"port {port} already serving our protocol , reusing it")
                return
            self.set_ready_status(f"port {port} is taken by something else", BAD)
            self.say(f"port {port} is in use and did not answer testconnection")
            self.warn(
                f"Port {port} is already in use by another program.\n\n"
                "The game can only talk to that exact port, so TestBed will not "
                "work until it is free. Tutorial and Demo are unaffected.\n\n"
                "Close any other copy of this launcher, or any cs_server.py you "
                "started yourself, and restart.")
            return

        try:
            self.servers, logfile = start_server(
                host, port, self.data_dir, self.galaxy_root, self.say_threadsafe)
        except Exception as exc:
            self.set_ready_status(f"server failed to start: {exc}", BAD)
            self.say("server failed: " + "".join(
                traceback.format_exception_only(type(exc), exc)).strip())
            return
        self.set_ready_status(f"server running on {host}:{port}", OK)
        listening = ", ".join(
            f"{s.server_address[0]}:{s.server_address[1]}" for s in self.servers)
        self.say(f"server listening on {listening}")
        self.say(f"log     {logfile}")

    # ── actions ──
    def on_play(self, mode):
        if not is_playable(mode):
            return
        if mode.get("id") == "multiplayer":
            self.start_multiplayer(mode)
            return
        busy = running_clients(self.client_exes)
        if busy:
            self.warn("A Cosmic Supremacy game is already running "
                      f"({', '.join(busy)}).\n\nStarting a second one would "
                      "close the first without saving. Quit the running game "
                      "first, then try again.")
            return
        try:
            self.child = launch_mode(mode, self.game_root, self.galaxy_root)
        except FileNotFoundError as exc:
            self.warn(f"Missing file:\n\n{exc}\n\nThe release folder looks "
                      "incomplete , try downloading it again.")
            return
        except OSError as exc:
            self.warn(f"Could not start the game:\n\n{exc}")
            return
        self.running_mode = mode
        self.say(f"launched {mode['id']}: {mode['exe']} {mode['galaxy']}")
        # "short", not "title": the buttons are imperative ("Play the Tutorial")
        # and reading one back as a state gives "Play the Tutorial is running".
        self.set_status(f"{_short(mode)} is running", OK)
        if mode.get("ai"):
            self.start_ai(mode)

    # ── Who you are ───────────────────────────────────────────────────────────
    def _show_identity(self):
        self.name_label.configure(text=self.player or "nobody yet",
                                  fg=TEXT if self.player else FAINT)
        self.name_btn.configure(text="change" if self.player else "set name")

    def ask_player_name(self, reason: str = ""):
        """Ask for the player's name, store it, and return it. None if cancelled.

        Modal, and it validates in place: a name that does not fit is answered
        beside the field the player is typing in. That is the whole point of
        this dialog existing rather than a JSON file they edit, where the same
        mistake surfaces as a turn that quietly went nowhere.
        """
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.title("Your name")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.root)

        text = ("This is the name other players see, and the name of your "
                "civilisation in a galaxy. You enter it once.")
        if reason:
            text = reason + "\n\n" + text
        tk.Label(win, text=text, bg=BG, fg=DIM, justify="left", wraplength=360,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=20, pady=(18, 10))

        var = tk.StringVar(value=self.player or "")
        entry = tk.Entry(win, textvariable=var, bg=PANEL, fg=TEXT, width=22,
                         insertbackground=TEXT, relief="flat",
                         highlightbackground=EDGE, highlightthickness=1,
                         font=("Segoe UI", 12))
        entry.pack(anchor="w", padx=20, ipady=3)
        tk.Label(win, text=NAME_RULES, bg=BG, fg=FAINT, justify="left",
                 wraplength=360, font=("Segoe UI", 8)).pack(anchor="w", padx=20,
                                                            pady=(6, 0))
        why = tk.Label(win, text="", bg=BG, fg=BAD, justify="left",
                       wraplength=360, font=("Segoe UI", 8))
        why.pack(anchor="w", padx=20, pady=(4, 0))

        picked = {}

        def accept(_evt=None):
            name, problem = validate_player_name(var.get())
            if problem:
                why.configure(text=problem)
                return
            picked["name"] = name
            win.destroy()

        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=20, pady=(14, 18))
        for label, cmd in (("Cancel", win.destroy), ("Save", accept)):
            tk.Button(row, text=label, bg=BTN, fg="#ffffff",
                      activebackground=BTN_HI, activeforeground="#ffffff",
                      relief="flat", bd=0, cursor="hand2", width=9,
                      font=("Segoe UI", 9, "bold"), command=cmd).pack(
                          side="right", padx=(6, 0))
        entry.bind("<Return>", accept)
        win.bind("<Escape>", lambda e: win.destroy())
        entry.focus_set()
        # grab_set after the window is mapped: grabbing a window that is not yet
        # on screen fails on Windows and leaves the dialog non-modal.
        win.update_idletasks()
        try:
            win.grab_set()
        except tk.TclError:
            pass
        self.root.wait_window(win)

        name = picked.get("name")
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
        return name

    def on_change_name(self):
        """Let a player change their name, except out from under a live galaxy.

        Changing it is allowed because a beta player will spell it wrong, and
        the only cost of changing it outside a galaxy is that the next galaxy
        knows them by the new name.

        Mid-galaxy is different. A galaxy's roster is fixed when it is generated
        and the referee matches submissions to it by exact name, so a launcher
        that renamed itself mid-turn would carry on playing and carry on being
        ignored. Stopping first makes that a decision rather than a discovery at
        the next deadline.
        """
        if self.mp_thread is not None and self.mp_thread.is_alive():
            self.warn("You are playing a galaxy right now.\n\nYour name "
                      "is the name of your civilisation in it, and a "
                      "galaxy's "
                      f"roster cannot be renamed from here. Close {self.cfg['product']} "
                      "to leave the galaxy, then change it.")
            return
        self.ask_player_name()

    # ── Multiplayer session ──────────────────────────────────────────────────
    def start_multiplayer(self, mode):
        """Follow this galaxy's turns until the player stops or the launcher
        closes."""
        if self.mp_thread is not None and self.mp_thread.is_alive():
            self.warn("A multiplayer galaxy is already running.")
            return
        busy = running_clients(self.client_exes)
        if busy:
            self.warn("A Cosmic Supremacy game is already running "
                      f"({', '.join(busy)}).\n\nClose it first: a multiplayer "
                      "turn starts the game itself.")
            return

        cfg = multiplayer_config(self.data_dir)
        if cfg is None:
            self.warn(
                "This galaxy is not set up yet.\n\nMultiplayer needs a "
                f"{MP_CONFIG} in\n{self.data_dir}\n\nnaming the galaxy "
                "folder or the referee's address, for example:\n\n"
                '{"store": "C:\\\\galaxies\\\\demo"}\n\n'
                "Your own name does not go in that file. The launcher asks you "
                "for it.")
            return

        # Asked for here and not at first run: a player who only ever opens the
        # Tutorial should not be interrogated for a name nothing will use. This
        # is the first moment one is genuinely needed.
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
        # Before anything asks them for a path. The modules were imported with
        # the wrong idea of where they are, and serving a turn is the first
        # thing that acts on it.
        bind_multiplayer_paths(self.game_root, self.data_dir)

        # The turn loop needs a client that never computes a turn of its own,
        # which is not the one the single player modes use. Checked here rather
        # than left to the worker thread, where a missing file surfaces as a
        # failed turn several steps after the player pressed the button.
        if not any(os.path.exists(os.path.join(self.game_root, e))
                   for e in MP_CLIENTS):
            self.warn("This build ships no multiplayer client.\n\nExpected one "
                      f"of\n{', '.join(MP_CLIENTS)}\n\nin\n{self.game_root}")
            return

        # `open_store`, not `TurnStore`: the store is a directory on one machine
        # and a base URL once the referee is on another, and which one it is is
        # the player's to write in multiplayer.json. Naming the class here made
        # a URL silently mean "a folder called http:".
        store = turn_store.open_store(cfg["store"])
        if not store.exists():
            self.warn(f"No galaxy in\n{cfg['store']}\n\nThe referee has to "
                      "publish a first turn before anyone can play it.")
            return

        # Ahead of the roster, and ahead of anything being served. A galaxy can
        # require a minimum build, and this launcher being too old for it is a
        # reason the roster or the turn itself might look wrong further down,
        # so it is the first thing answered.
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

        # The roster is the galaxy's list of seats, and the referee matches
        # submissions to it by exact name and discards anything else. Checking
        # here turns a turn played and thrown away into a message before
        # anything starts. The other players' names stay in the store: see
        # roster_problem.
        try:
            seats = store.civs()
        except (OSError, ValueError, KeyError) as exc:
            seats = None
            self.say(f"multiplayer: could not read the roster ({exc})")
        if seats is not None:
            problem = roster_problem(civ, seats)
            if problem:
                # The count, not the names. launcher.log is a file the player
                # can open, so it is one more place the roster would leak from.
                self.say(f"multiplayer: {civ!r} is not one of this galaxy's "
                         f"{len(seats)} seat(s)")
                from tkinter import messagebox
                if messagebox.askyesno(
                        self.cfg["product"],
                        problem + "\n\nChange your name now?"):
                    self.on_change_name()
                return

        self.mp_store = store
        self.mp_civ = civ
        self.mp_stop = False
        self.running_mode = mode
        self.say(f"multiplayer: following {cfg['store']} as {civ}")
        self.set_status(f"Multiplayer , {civ}", OK)
        self._show_controls(True)

        save_dir = os.path.join(self.data_dir, "saves")
        os.makedirs(save_dir, exist_ok=True)

        def work():
            try:
                player_turn.follow(
                    store, civ,
                    poll=2.0,
                    on_state=self._mp_state,
                    save_dir=save_dir,
                    stop=lambda: self.mp_stop,
                    log=self.say_threadsafe)
            except BaseException as exc:            # noqa: BLE001
                # A dead worker must say so. Silence here reads as "my turn is
                # still being set up", and the player waits forever.
                self.say_threadsafe(f"multiplayer stopped: {exc}")
                self._mp_state("failed", error=str(exc))

        self.mp_thread = threading.Thread(target=work, daemon=True,
                                          name="multiplayer")
        self.mp_thread.start()

    def _mp_state(self, kind, **facts):
        """Called from the worker thread. Records a line for the turn readout."""
        civ = facts.get("civ", self.mp_civ)
        turn = facts.get("turn")
        if turn is not None:
            self.mp_turn = turn
        self.mp_note = {
            "serving": "starting your turn",
            "playing": "",
            "collecting": "time is up, orders are in",
            "submitted": "orders sent",
            "waiting": "waiting for the next turn",
            "overtaken": "that turn closed without you",
            "failed": f"stopped: {facts.get('error', 'unknown')}",
            "stopped": "stopped",
            "done": "finished",
        }.get(kind, kind)
        if kind in ("submitted", "waiting", "failed", "stopped", "done"):
            self.say_threadsafe(f"multiplayer: {self.mp_note} ({civ})")

    def stop_multiplayer(self, why: str = ""):
        """Ask the turn loop to stop. It finishes the step it is on first."""
        if self.mp_thread is None:
            return
        self.mp_stop = True
        if why:
            self.say(f"multiplayer: stopping , {why}")
        self.mp_thread.join(timeout=20)
        if self.mp_thread.is_alive():
            self.say("multiplayer: the turn loop is still finishing a capture")
        self.mp_thread = None
        self.mp_note = ""
        self.mp_turn = None

    def start_ai(self, mode):
        """Start the opponent, and say clearly if there is not one to start."""
        try:
            self.ai_child = launch_ai(mode, self.data_dir)
        except OSError as exc:
            self.ai_child = None
            self.say(f"opponent failed to start: {exc}")
        if self.ai_child is None:
            self.say(f"opponent NOT started , {AI_EXE} was not found")
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

    # ── Game controls ────────────────────────────────────────────────────────
    def _show_controls(self, show: bool):
        if show and not self.ctl_frame.winfo_ismapped():
            self.ctl_frame.pack(fill="x", padx=26, pady=(10, 12))
        elif not show and self.ctl_frame.winfo_ismapped():
            self.ctl_frame.pack_forget()

    def _set_controls_enabled(self, enabled: bool):
        for b in self.ctl_buttons.values():
            b.configure(state="normal" if enabled else "disabled",
                        bg=BTN if enabled else FAINT,
                        cursor="hand2" if enabled else "")

    def _in_background(self, label, work, done=None):
        """Run `work()` off the Tk thread, with the controls disabled meanwhile.

        Ending a turn takes as long as the engine takes, and a save serialises
        the whole object graph. Doing either on the Tk thread freezes the window
        for the duration, which reads exactly like a crash , and this window is
        also the game's server, so it must keep answering while the game works.
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

    def on_next_turn(self):
        def work():
            with self._client() as c:
                return c.advance_turn()
        self._in_background("ending the turn", work)

    def on_save(self):
        import gamectl
        save_dir = os.path.join(self.data_dir, "saves")
        dat_dir = os.path.join(self.data_dir, "games")
        os.makedirs(dat_dir, exist_ok=True)

        def work():
            started = time.time() - 1
            with self._client() as c:
                turn = c.turn()
                if not c.save_game("singleplayer"):
                    raise gamectl.GameError(
                        "the game refused to save. This usually means the "
                        "launcher's server is not reachable , keep this window "
                        "open while you play.")
            # The engine's save goes out over HTTP; give the server a moment to
            # finish writing the capture before looking for it.
            time.sleep(1.5)
            out = os.path.join(dat_dir, f"savegame_turn{turn}.dat")
            return gamectl.capture_to_dat(save_dir, out, since=started)

        self._in_background("saving", work)

    def on_load(self):
        from tkinter import filedialog, messagebox
        dat_dir = os.path.join(self.data_dir, "games")
        os.makedirs(dat_dir, exist_ok=True)
        path = filedialog.askopenfilename(
            title="Load a saved game", initialdir=dat_dir,
            filetypes=[("Saved games", "*.dat"), ("All files", "*.*")])
        if not path:
            return
        if not messagebox.askokcancel(
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
            self.start_ai(mode)

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
        # Never trap the player behind a stuck or crashed opponent. After the
        # timeout the button comes back and the log says why, which is a better
        # failure than a window that will not let you play.
        waited = time.time() - (self._waiting_since or time.time())
        if waited > gamectl.AI_READY_TIMEOUT:
            # Kept short: this shares one line with the turn number, and a
            # message that overflows is a message nobody reads.
            return True, f"{civ} not responding"
        return False, f"{civ} is thinking"

    def _refresh_turn(self):
        """Update the turn readout
        """
        import gamectl
        if self.mp_store is not None:
            # In multiplayer the clock belongs to the store, not to the client:
            # the client's own countdown is held far into the future so it can
            # never compute a turn the referee has not.
            try:
                turn, _deadline = self.mp_store.current()
                left = self.mp_store.seconds_left()
                label = f"turn {turn} \u00b7 {fmt_left(left)} left"
                if self.mp_note:
                    label = f"turn {turn} \u00b7 {self.mp_note}"
                if self.turn_label.cget("text") != label:
                    self.turn_label.configure(text=label)
            except Exception:
                pass
            for key, btn in self.ctl_buttons.items():
                # Save and Next Turn are meaningless here. The launcher submits
                # at the deadline, and a player who ends their own turn early
                # would be asking for a state the referee has not computed.
                want = "disabled" if key in ("turn", "load") else "normal"
                if btn.cget("state") != want:
                    btn.configure(state=want,
                                  bg=BTN if want == "normal" else FAINT,
                                  cursor="hand2" if want == "normal" else "")
            return
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
            if self.turn_label.cget("text") != label:
                self.turn_label.configure(text=label)
            # Only the turn button is gated. Save and Load stay live: neither
            # advances the clock, so neither can make the opponent miss a turn.
            btn = self.ctl_buttons.get("turn")
            if btn is not None and not self._ctl_busy:
                want = "normal" if ready else "disabled"
                if btn.cget("state") != want:
                    btn.configure(state=want, bg=BTN if ready else FAINT,
                                  cursor="hand2" if ready else "")
        except Exception:
            self._close_ctl_client()    # a readout is not worth an error path

    def _close_ctl_client(self):
        if self._ctl_client is not None:
            try:
                self._ctl_client.close()
            except Exception:
                pass
            self._ctl_client = None

    def toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_frame.pack(fill="both", expand=True, padx=26, pady=(8, 0))
            self.log_btn.configure(text="hide log")
        else:
            self.log_frame.pack_forget()
            self.log_btn.configure(text="show log")

    def on_close(self):
        from tkinter import messagebox
        if running_clients(self.client_exes):
            if not messagebox.askokcancel(
                    "Quit launcher?",
                    "A game is still running.\n\nClosing the launcher stops the "
                    "local server, and TestBed needs it. Saving and loading "
                    "will fail from that point on.\n\nClose anyway?"):
                return
        self.stop_multiplayer("the launcher is closing")
        self.stop_ai("the launcher is closing")
        for srv in self.servers:
            try:
                srv.shutdown()
            except Exception:
                pass
        self.root.destroy()

    # ── output ──
    def say(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
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
        """Called from the server thread; tkinter is not thread-safe."""
        self.msgs.put(msg)

    def _watch_game(self):
        if self.child is not None and self.child.poll() is not None:
            name = _short(self.running_mode) if self.running_mode else "the game"
            code = self.child.returncode
            self.child = None
            self.running_mode = None
            self.say(f"{name} exited (code {code})")
            self.stop_ai("the game exited")
            self._close_ctl_client()

        if self.ai_child is not None and self.ai_child.poll() is not None:
            code = self.ai_child.returncode
            self.ai_child = None
            self.say(f"opponent exited (code {code}) , the other empire will "
                     f"not take any more turns")

        if self.child is not None and self.running_mode is not None:
            self._status_if_changed(f"{_short(self.running_mode)} is running", OK)
        else:
            busy = running_clients(self.client_exes)
            if busy:
                self._status_if_changed(f"{self._name_for(busy)} is running", OK)
            else:
                self._status_if_changed(*self._ready)

        # The controls belong to a running game, and only to a mode that asked
        # for them: Tutorial and Demo have their own UI and must not grow a
        # Next Turn button that means nothing there.
        if self.mp_thread is not None and self.mp_thread.is_alive():
            self._show_controls(True)
            self._refresh_turn()
            self.root.after(1000, self._watch_game)
            return
        wants = bool(self.running_mode and self.running_mode.get("controls")
                     and self.child is not None)
        self._show_controls(wants)
        if wants:
            self._refresh_turn()
        self.root.after(1000, self._watch_game)

    def _name_for(self, exe_names) -> str:
        """
        What to call a game we did not launch, knowing only its EXE.

        CosmicSupremacy.exe backs both Tutorial and Demo, so when the mapping is
        ambiguous say "A game" rather than confidently name the wrong one.
        """
        shorts = {_short(m) for m in self.modes if m.get("exe") in exe_names}
        return shorts.pop() if len(shorts) == 1 else "A game"

    def _status_if_changed(self, text: str, colour: str):
        """Repaint only on a real change , this runs once a second."""
        if self.status.cget("text") != text:
            self.set_status(text, colour)

    def _drain(self):
        try:
            while True:
                msg = self.msgs.get_nowait()
                # Background game-control results arrive on the same queue as
                # log lines, tagged, so their completion runs on the Tk thread
                # where it is allowed to touch widgets.
                if isinstance(msg, tuple) and msg and msg[0] == "__ctl__":
                    _tag, label, result, err, done = msg
                    self._on_control_done(label, result, err, done)
                else:
                    self.say(msg)
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    def set_status(self, text: str, colour: str):
        self.status.configure(text=text)
        self.dot.configure(fg=colour)

    def set_ready_status(self, text: str, colour: str):
        """Set the status *and* remember it as the no-game-running state."""
        self._ready = (text, colour)
        self.set_status(text, colour)

    def warn(self, msg: str):
        from tkinter import messagebox
        messagebox.showwarning(self.cfg["product"], msg)

    def fail(self, msg: str):
        from tkinter import messagebox
        self.set_status("cannot start", BAD)
        for btn in self.buttons:
            btn.configure(state="disabled", bg=FAINT, cursor="")
            btn.unbind("<Enter>")
            btn.unbind("<Leave>")
        messagebox.showerror(self.cfg["product"], msg)


def main() -> int:
    import tkinter as tk
    with open(bundled("manifest.json"), "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    set_app_user_model_id()     # must precede the first window
    root = tk.Tk()
    Launcher(root, cfg)
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A windowed build has no console, so an unhandled exception would
        # otherwise be a window that never appears and no explanation anywhere.
        detail = traceback.format_exc()
        try:
            import tkinter.messagebox as mb
            mb.showerror("Cosmic Supremacy: Resurgence",
                         "The launcher failed to start.\n\n" + detail)
        except Exception:
            pass
        sys.exit(1)
