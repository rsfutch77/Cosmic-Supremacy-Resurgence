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
import typing

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
    """True if whatever holds the port speaks our protocol (a dev server, say).

    Asked only once the port is known to be taken, and only to name what has
    taken it. It is not a licence to use that server: see `_boot`.
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


# What a galaxy's state says about itself, by the keys turn_store writes. Read
# by key rather than through the store's own helpers for the reason
# `min_build` already is: the launcher holds the state it read anyway, these
# are answered from it without a second read, and a galaxy written before any
# of them existed simply has none. Absent therefore means open and not
# reclaimed, which is every galaxy that predates this.
STATUS_KEY = "status"
CLOSED = "closed"
CLOSED_REASON_KEY = "closed_reason"
RECLAIMED_KEY = "reclaimed"

# The other two words a listing uses. `forming` is not a status anything
# writes: it is what a directory calls a galaxy whose store has no first turn
# yet. They are here because the table orders and colours rows by them.
OPEN = "open"
FORMING = "forming"


def reclaimed_seat(state, name: str):
    """What became of this player's own seat, or None if nothing did.

    Read out of the state the launcher already holds rather than asked of the
    store a second time, and read by the raw key so that a galaxy written
    before seats could be taken back answers None instead of raising.

    Only the record for `name` is taken out. The key holds every seat this
    galaxy has reclaimed, and carrying the rest of it any further would answer
    "what happened to my seat" with a list of who else stopped playing.
    """
    taken = (state or {}).get(RECLAIMED_KEY)
    if not isinstance(taken, dict):
        return None
    record = taken.get(name)
    return record if isinstance(record, dict) else None


def _count(value):
    """A whole number from a record written elsewhere, or None.

    Booleans are numbers in Python and are not counts anywhere else, so one in
    a record is a malformed field rather than a turn number.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def roster_problem(name: str, civs, reclaimed=None):
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

    `reclaimed` is this player's own record from `reclaimed_seat`, and it is
    the commonest reason a seat is missing once a galaxy has been running for a
    while: the seat existed and the galaxy took it back. Told only that no seat
    exists, a player who played for a fortnight reads their own empire being
    deleted as the launcher being pointed at the wrong galaxy. The record names
    nobody but the player holding it, and the message echoes only the name they
    typed.
    """
    seats = list(civs)
    if name in seats:
        return None
    # A record with nothing in it says a seat was reclaimed and nothing about
    # when or why, which is less than the roster count already says, so it is
    # not a record. Anything that is not a mapping at all is a malformed state
    # rather than a reclaim, and answering it as one would tell a player their
    # empire was deleted on the strength of a typo.
    taken = reclaimed if isinstance(reclaimed, dict) and reclaimed else None
    if taken is not None:
        turn, missed = _count(taken.get("turn")), _count(taken.get("missed"))
        when = f" at turn {turn}" if turn is not None else ""
        after = ("" if not missed else
                 " after 1 missed turn" if missed == 1 else
                 f" after {missed} missed turns in a row")
        return (f"This galaxy took the seat {name!r} back{when}{after}.\n\n"
                "An empire that stops playing is removed so its "
                "planets are freed up for others. ")
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


def closed_problem(state) -> "str | None":
    """Why this galaxy is no longer being played, or None. For the player.

    Beside `version_problem` and asked with it, ahead of the roster, for the
    same reason: a galaxy the operator has ended is why everything below might
    look wrong. Its roster is whatever it was when it stopped, a submission
    into it is refused outright, and a launcher that checked the roster first
    would report one of those instead of the one fact that explains them.

    The operator's own words are shown when the state carries them. A galaxy is
    ended for a reason a player has no other way of learning, and "closed" on
    its own reads as a fault rather than a decision.

    A state with no status is open. That is every galaxy written before a
    galaxy could be closed, and treating a missing key as anything else would
    end all of them.
    """
    if (state or {}).get(STATUS_KEY) != CLOSED:
        return None
    reason = (state or {}).get(CLOSED_REASON_KEY)
    said = f"\n\n{reason}" if isinstance(reason, str) and reason.strip() else ""
    return (f"This galaxy has been closed.{said}\n\nIt still reads, so its "
            "turns are all still there, but it takes no more of them and a "
            "turn played into it would be refused.\n\nThe Galaxies list shows "
            "whatever else is running.")


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


def multiplayer_file(data_dir: str):
    """multiplayer.json as it stands, or None if there is not one to read.

    Separate from `multiplayer_config` because the file has two readers now.
    A file naming only a `directory` is a whole configuration and carries no
    store, and the store reader answering None for it must not lose the rest.
    """
    return _read_json(os.path.join(data_dir, MP_CONFIG))


def adopted_server(data_dir: str):
    """Where an already-running server keeps its captures, or None.

    `multiplayer.json`'s `adopt_server`: the saves directory of a server this
    launcher did not start and is allowed to share. Absent, which is the usual
    case, the launcher refuses a held port rather than reusing one blindly.

    That refusal is right and stays: a capture lands in whatever directory the
    server that took it was started with, there is no message in the protocol
    to ask it which, and reusing one silently sent every turn to a folder this
    launcher never read. What the refusal cannot know is the case where the
    operator knows perfectly well whose server it is, which on a machine that
    is both the referee and a player is every time: the unattended worker
    holds the port and writes to its own saves directory. `referee_worker`
    has the same escape for the same reason, `--adopt-server` with
    `--save-dir`.

    So this is not a way round the guard, it is the operator supplying the one
    fact the protocol cannot: not "share it" but "share it, and it writes
    here". A wrong directory fails the same way the bug did, so it is checked
    and refused rather than believed.
    """
    cfg = multiplayer_file(data_dir) or {}
    where = cfg.get("adopt_server")
    if not where or not isinstance(where, str):
        return None
    return os.path.abspath(os.path.expandvars(os.path.expanduser(where)))


def multiplayer_config(data_dir: str):
    """{"store": <dir or URL>} for this galaxy, or None.

    Kept in the data directory rather than the manifest because it is per
    player and per galaxy: the manifest ships to everyone, and which galaxy you
    joined is yours.

    Only `store` is required. `civ` used to be, and a file that still carries
    one is read once by `legacy_civ` to seed the player's name, after which the
    name comes from identity.json and this file names only the galaxy.

    An optional `auth`, true or false, says whether to put this install's
    Firebase identity on the requests. Left out, an https store is taken to
    want one and everything else is taken not to: see `store_wants_token`.
    """
    cfg = multiplayer_file(data_dir)
    if not cfg or not cfg.get("store"):
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


def store_wants_token(cfg) -> bool:
    """Whether this galaxy's store is one to put an identity on.

    A galaxy served by the beta's Cloud Function is reached over https and
    wants a token. A folder on this disk and a `turn_server.py` on the LAN do
    not: neither reads one, and signing this install up to Firebase to talk to
    either would be an account nobody asked for and a network call a player who
    is offline should not be waiting on. An `auth` key in multiplayer.json
    overrides both ways, which is what points the launcher at the function
    running in a local emulator.
    """
    want = cfg.get("auth")
    if isinstance(want, bool):
        return want
    return str(cfg.get("store", "")).startswith("https://")


def player_token(data_dir: str):
    """A zero-argument callable answering this install's Firebase ID token.

    None when this build cannot reach `fb_auth`, which is the same answer as a
    token that cannot be minted: the store sends no Authorization header and
    behaves as it did before any of this existed.

    The identity is separate from the player's name and is never shown. See
    server\\fb_auth.py. Called after multiplayer_modules(), because that is
    what puts server\\ on sys.path in a checkout.
    """
    try:
        import fb_auth
    except ImportError:
        return None
    return fb_auth.identity(data_dir).token


def _takes_token(fn) -> bool:
    """Whether `fn` accepts a `token` keyword.

    Asked rather than assumed because the store side of this lands separately:
    a launcher built against a turn_store that predates it has to keep opening
    galaxies, without a TypeError and without quietly swallowing a real one.
    """
    import inspect
    try:
        return "token" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def open_player_store(turn_store, spec: str, token=None):
    """`open_store`, carrying this install's identity when there is one.

    `open_store`, not `HttpTurnStore`, for the reason the call site already
    gives: which kind of store a spec names is the spec's business. The token
    is passed as the callable itself and not as a string, because a galaxy is
    followed for longer than a token lasts.
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


def fmt_ago(seconds: float) -> str:
    """How long ago something happened, for a line that is repainted every
    second.

    Rounded rather than exact, because the question it answers is whether the
    launcher is keeping up, not what the clock says. Anything under three
    seconds is "just now": a number ticking 1, 2, 3 beside a status that is not
    changing reads as a problem.
    """
    seconds = max(0.0, seconds)
    if seconds < 3:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds) // 60}m ago"
    return f"{int(seconds) // 3600}h ago"


# ── The galaxy directory ──────────────────────────────────────────────────────
# Which galaxies exist is a question a store cannot answer, so it is asked of a
# directory: server\galaxy_directory.py, opened from a spec the way a store is.
# The launcher lists what the directory holds and opens a galaxy with the store
# spec the directory handed back, so no config file names a galaxy.
#
# The directory is reached only when multiplayer.json does not name a store. A
# folder of turns and a LAN turn_server are named outright and keep working with
# no directory, no identity and no network call, which is what development and
# the two-machine test run on.
JOINED_FILE = "joined.json"

# What the collection in that file is written under. A file carrying this key
# holds one record per galaxy; anything else is the single record the file held
# before a player could be in more than one, and is read as one of these. The
# version is written for whoever reads the file next rather than for the reader
# below, which tells the two shapes apart by the key.
JOINED_KEY = "galaxies"
JOINED_VERSION = 2

# The directory listed when nothing names another one. A player who has never
# opened a config file gets this, which is the point of the Galaxies page.
#
# The relay rather than `firebase://cs-resurgence`, which is the same galaxies
# read the other way: reading Firestore directly authenticates as the project,
# and a player is not the project. It is the Cloud Run host rather than the
# cloudfunctions.net one because the two are gated separately and only this one
# is open to callers who are not members of the project.
BETA_DIRECTORY = "https://relay-r5t6py5oxa-uw.a.run.app"

# Where a join request goes in a galaxy that is a folder. A store that knows its
# own route is asked instead: see send_join_request.
JOIN_DIR = "joins"

# How often the page re-lists by itself. A listing is one query and the
# launcher is one of many, so this is slow on purpose: the countdown a row
# shows is worth less than the reads a faster poll would spend.
GAMES_POLL_MS = 120000


class JoinNotAccepted(Exception):
    """This galaxy has nowhere to put a join request."""


def directory_spec(cfg, mp_cfg=None) -> str:
    """Which directory to list: multiplayer.json's, the manifest's, or the beta's.

    multiplayer.json comes first because a player pointed at a folder of
    galaxies is overriding the release they are running. The manifest comes
    second, so a release can ship a different directory without a code change.
    """
    for src in (mp_cfg, (cfg or {}).get("multiplayer")):
        spec = (src or {}).get("directory")
        if isinstance(spec, str) and spec.strip():
            return spec.strip()
    return BETA_DIRECTORY


def directory_to_list(cfg, mp_cfg=None):
    """Which directory the Galaxies page lists, or None when there is not one.

    A multiplayer.json that names a `directory` is listed whatever else it
    names, so a player who pointed the launcher at one galaxy by hand still
    sees the rest of them beside it.

    A multiplayer.json that names only a `store` reaches no directory at all.
    That is the folder and LAN path, which runs with no identity and no
    network call, and falling back to the beta's directory for it would be a
    query nobody asked for on a machine that may well be offline.
    """
    named = (mp_cfg or {}).get("directory")
    if isinstance(named, str) and named.strip():
        return named.strip()
    if (mp_cfg or {}).get("store"):
        return None
    return directory_spec(cfg, mp_cfg)


def open_galaxy_directory(spec: str, token=None):
    """The directory named by `spec`, or None when this build cannot open one.

    `galaxy_directory` sits in server\\ beside the turn machinery and is reached
    the same way, so multiplayer_modules() is what puts that directory on
    sys.path in a checkout. A build frozen without it lists nothing rather than
    failing to start, which is the rule the whole multiplayer half follows.

    The token is asked for rather than assumed, the way `open_player_store`
    asks: a launcher running against a `galaxy_directory` that predates the
    relay listing still has to list a folder, without a TypeError.
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

    Read from fb_identity.json, and minted only when the galaxy being joined
    wants an identity anyway. A folder galaxy asks for no uid and makes no
    network call to discover it has none.
    """
    try:
        import fb_auth
    except ImportError:
        return None
    ident = fb_auth.identity(data_dir)
    if ident.uid is None and mint:
        ident.token()
    return ident.uid


# ── What this player joined ───────────────────────────────────────────────────
# The launcher's own record of the actions the player took, not settings they
# are asked to keep, which is why this lives beside identity.json and
# fb_identity.json rather than in multiplayer.json. Each record holds the store
# spec the directory gave, so a galaxy that moves between a folder, a LAN server
# and the beta is followed without anyone editing anything.
#
# One record per galaxy, keyed by galaxy id. Being in a galaxy is membership and
# a player may hold seats in several at once; playing one is a client
# constraint, because a machine runs one game, and that limit is enforced where
# a turn loop starts rather than here.
def joined_path(data_dir: str) -> str:
    return os.path.join(data_dir, JOINED_FILE)


def joined_records(raw):
    """The records in a joined.json of either shape, keyed by galaxy id.

    Before a player could hold more than one seat the file was a single record
    with its `store` at the top level. One of those is read here as the
    collection it would be written as today, so an install that joined a galaxy
    before this existed is still in that galaxy afterwards. Nothing writes the
    old shape again: the next save writes the collection.

    A record with no store is no record: it names a galaxy the launcher cannot
    open, and treating it as one would leave the player in front of a Play
    button that has nowhere to go. A record that arrived without a galaxy id is
    kept under its store spec, which is what the single-record file was played
    from, so dropping it would be losing a seat over a missing key.

    Order is the order the file lists, which is the order the galaxies were
    joined in, so the page does not reshuffle itself between refreshes.
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
        # The key is what every caller matches a row against, so the record
        # carries it whatever the file said.
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

    Joining a second galaxy is joining rather than moving: the record for the
    first is the seat the player still holds in it. A galaxy joined again keeps
    the place it already had in the file rather than moving to the end.
    """
    records = load_joined(data_dir)
    gid = str(record.get("galaxy") or record.get("store") or "")
    records[gid] = dict(record, galaxy=gid)
    write_joined(data_dir, records)


def clear_joined(data_dir: str, galaxy=None) -> None:
    """Forget one galaxy, or all of them.

    One galaxy by default, because a seat taken back in one is not a reason to
    forget the others. The file goes when the last record does, so an install
    that has left every galaxy looks like one that never joined any.
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


def joined_record(galaxy, name: str, uid, directory: str, now=None) -> dict:
    """What the launcher writes down when a player joins a galaxy."""
    return {"directory": directory,
            "galaxy": galaxy.id,
            "name": name,
            "store": galaxy.store,
            "uid": uid,
            "requested_at": now if now is not None else time.time(),
            "requested_turn": galaxy.turn}


def newest_joined(records):
    """The record for the galaxy joined most recently, or None.

    Asked only when nothing named a galaxy to play. The newest join is the one
    the player last said they wanted, which is the best a caller with no row
    can do, and it is a decision rather than whichever record the file happened
    to list first. Two joins written in the same second are settled by galaxy
    id, so the answer is the same on every call.
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

    multiplayer.json wins when it names a store: a folder or a LAN referee is a
    galaxy somebody chose outright, and a listing must not take it from them.
    Otherwise it is a galaxy the player joined, and the `joined` key carries
    that record so the caller can tell which name the seat was asked for under.

    This is the answer for a caller that named no row. A player in several
    galaxies chooses one by pressing Play on its row, which reaches
    `galaxy_config` instead and never asks this.

    Answered from the records alone, with no store read, so it can name a join
    that has not landed yet. What that galaxy does about a player with no seat
    in it is the roster check's to say, as it was when there was one record.
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


# ── Joining ───────────────────────────────────────────────────────────────────
# A join is a request rather than a change. The worker applies it at a turn
# boundary, on the authoritative blob, so a player who clicks Join during turn N
# is playing at turn N+1. The launcher's half is to write the request down where
# the worker will find it and to say plainly that it is waiting.
def join_request(name: str, uid, build: str, turn=None, now=None) -> dict:
    """The request a Join writes, read by the worker at the next turn boundary.

    The uid is in it because a seat is bound to the identity that claimed it,
    and the worker is the only thing that can record that binding. It is None
    for every folder and LAN galaxy, where there is no identity to bind to and
    nothing asking for one.
    """
    return {"name": name, "uid": uid, "build": build,
            "requested_at": now if now is not None else time.time(),
            "requested_turn": turn}


def send_join_request(store, req: dict) -> str:
    """Put a join request where this galaxy's worker will find it.

    A store that has a `request_join` is asked, because a relay's own route is
    the store's to know. A galaxy that is a folder has no such route and needs
    none: the request is a file beside the submissions, named by the uid when
    there is one and by the player's name when there is not, both of which are
    already file-name safe.

    Anything else raises rather than reporting success. A join that went
    nowhere looks exactly like a join that is waiting for the next turn, and
    the player has no way to tell those apart afterwards.
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

    A seat belongs to the identity that claimed it, so a name already in the
    roster is refused here rather than at the turn boundary, where the refusal
    would reach the player as a join that quietly did nothing.

    What comes back names nobody. It echoes the name the player typed and the
    number of seats, for the reason `roster_problem` gives: a launcher that
    answered "is there a seat for me" with the roster is a way to enumerate who
    is playing. A seat differing only in capitalisation is refused the same way
    and is not echoed back, because that spelling belongs to somebody else.

    `held` is the name this install already holds in this galaxy, which is not
    a claim to refuse.
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

    The state between clicking Join and the worker merging the civ at the next
    turn boundary. It is a fact about the launcher's own records rather than
    about the galaxy, because the galaxy shows nothing until the merge lands.
    """
    return bool(recs) and g.id in recs and not g.joined


def joinable(g, recs=None) -> bool:
    """Whether this galaxy would take a join request from this player.

    Only an open galaxy takes new players. One that is forming has no first
    turn for a worker to merge a join into, and a closed one has stopped taking
    them and is still listed, which is why the answer is a control being absent
    rather than the row being absent.
    """
    if g.joined or pending_join(g, recs):
        return False
    return g.status == OPEN


# ── When a galaxy has stopped ────────────────────────────────────────────────
# Turns are closed by a referee running as a scheduled task on one person's PC,
# which starts when they log in. A machine that is off, or one that rebooted
# and is sitting at a logon screen, leaves every galaxy it refs with a deadline
# that passes and a turn nothing closes. Until this, a player could not tell
# that apart from a turn about to close: the countdown reached zero and then
# nothing happened, which is the same screen either way, so nobody had a reason
# to tell the operator that the machine needed switching back on.
#
# Being past the deadline is not the fault, and a rule that said so would flag
# every healthy galaxy six times a day. What the referee is allowed to be late
# by is written down in its own files:
#
#   referee_worker.GRACE        20s   submissions are still taken after the
#                                     deadline, so it does not even begin yet
#   referee_worker.POLL          5s   how long a passed deadline can go unread
#   N5                         ~10s   a measured tick, client launch included
#   referee_worker.BACKOFF_MAX  300s  the longest a live worker waits between
#                                     attempts at a turn that keeps failing
#
# So a worker that is up and working can be five minutes behind, and that is
# STALL_FLOOR: below it, a galaxy is late and no more, whatever its turn
# length. Above it the judgement has to scale, because a minute means nothing
# to a 4-hour galaxy and a third of a turn to a 15-minute one, so the threshold
# is a share of the turn. A quarter is late enough to be unambiguous and early
# enough to be worth acting on, and STALL_CEILING holds it at an hour however
# long the turn is, which is the operator's own line for lateness that is not
# acceptable. At the beta's 4-hour turn the share and the ceiling agree.
STALL_FLOOR = 300.0
STALL_SHARE = 4.0
STALL_CEILING = 3600.0


def stall_after(turn_seconds) -> float:
    """How overdue a turn has to be before its galaxy counts as stopped.

    A galaxy whose state carries no usable turn length is judged by the
    ceiling. That is the latest this can fire, and late is the right direction
    to be wrong in: calling a healthy galaxy stopped sends a player to the
    operator over nothing, and telling them late costs them the difference.
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

    Answered from the deadline and the turn length the caller already holds,
    so it reads nothing. That is the point as much as the arithmetic is: the
    store is metered, and a check that cost a read per galaxy per second would
    spend more than the whole polling schedule H5 just cut.

    A closed galaxy is never stopped. The worker deliberately ticks nothing in
    one, so its deadline passes and stays passed for as long as the galaxy is
    listed, and that is the operator having ended it rather than a machine
    being off. A galaxy with no deadline has published no turn and has no clock
    to be behind on.
    """
    if status == CLOSED or not deadline:
        return 0.0
    over = (time.time() if now is None else now) - deadline
    return over if over > stall_after(turn_seconds) else 0.0


def row_stalled_for(g, view=None) -> float:
    """`stalled_for` for one row of the table, whichever kind of row it is.

    The turn length is fetched rather than named, because a row handed over by
    a directory this launcher did not ship with would not carry one, and a
    missing length is a threshold rather than a crash.
    """
    return stalled_for(g.deadline, getattr(g, "turn_seconds", None), g.status,
                       None if view is None else view.clock)


def stall_hint(stalled) -> str:
    """The line under the table when a galaxy's turns have stopped.

    One sentence, the same one however many galaxies are stopped. Which ones
    they are is in the table, a word per row, and the operator's call is that
    repeating it underneath was not worth the length. So nothing here varies,
    and `stalled` is taken and not read.

    The argument stays in the signature because the callers pass it and because
    what this line is allowed to say is a question that gets revisited. Two
    things a longer version carried are gone and are worth naming in case they
    are wanted back: how long it has been stopped, which appears nowhere else
    on the page because the time column reads `time is up` and never how long
    ago, and who to tell, which was the only act the line offered.
    """
    return "Some galaxies are stopped on the server, check back later"


# ── The table the Galaxies page draws ────────────────────────────────────────
# One row per galaxy, sorted by whatever column the player clicked. A column is
# two halves that are deliberately not the same thing: the value it orders by,
# and the text it prints. Those disagree wherever the text is written for a
# player, which is nearly everywhere , turn 9 sorts below turn 11 but prints
# above it, "1h 04m left" prints above "9:30" and is further away, and "you are
# in" prints after "joining next turn" while being the row that matters more.
# So the table sorts on the value and never on the cell.
PLAY = "play"
VIEW = "view"
ACTION_TEXT = {PLAY: "Play", VIEW: "View"}

# How far along a galaxy is, which is what the status column orders by. Not the
# spelling of the word: alphabetically the closed galaxies come first, which is
# the wrong end of a list of what there is to play.
STATUS_ORDER = (OPEN, FORMING, CLOSED)


class View(typing.NamedTuple):
    """What a cell needs to know that its own row does not carry.

    `recs` is what this launcher has joined, keyed by galaxy id, so a row finds
    its own record and no other row's.

    `submitted` is keyed the same way. Finding out costs a read per galaxy, so
    the launcher asks it of the galaxies this player is actually seated in and
    of no others, and a row with no entry reads it as "not asked" rather than
    as "not played".

    `own` is the store multiplayer.json names outright, which is the one row
    that is played whatever the listing says about it.
    """
    now: "float | None" = None
    recs: "dict | None" = None
    submitted: "dict | None" = None
    own: "str | None" = None

    @property
    def clock(self) -> float:
        """The moment the countdowns are measured from."""
        return time.time() if self.now is None else self.now


class Column(typing.NamedTuple):
    """One column: what it sorts on, and what it prints.

    `value` answers None for a galaxy the column cannot speak for, which is a
    third thing and not a zero: a forming galaxy has no turn, and a closed one
    has no countdown.
    """
    key: str
    heading: str
    width: int
    value: typing.Callable      # (galaxy, view) -> the sort value, or None
    text: typing.Callable       # (galaxy, view) -> the cell


def _name_value(g, view):
    # Folded, or a galaxy called "sandbox" lands after every capitalised one.
    return (g.name or g.id or "").lower()


def _name_text(g, view):
    return g.name or g.id or ""


def _status_value(g, view):
    return (STATUS_ORDER.index(g.status) if g.status in STATUS_ORDER
            else len(STATUS_ORDER))


def _status_text(g, view):
    """The galaxy's own word, or "stopped" over the top of it.

    The word the store holds is `open`, and an open galaxy nothing is ticking
    is the state this column has to be able to show: a player scanning the
    table for somewhere to play reads the status before anything else, and a
    row that said `open` about a galaxy whose referee has been off since
    Tuesday is the table telling them the wrong thing at the place they look
    first. The stored word is still what the column sorts on, which is the
    table's standing rule and not an exception made here.
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

    A closed galaxy has none by decision rather than by omission. Its deadline
    is frozen at whatever it was when the operator ended it, and a clock
    counting down to it promises a turn that never comes.
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
        played = (view.submitted or {}).get(g.id)
        if played is True:
            return "turn played"
        if played is False:
            return "your turn"
        return "you are in"
    if pending_join(g, view.recs):
        return "joining next turn"
    return ""


GALAXY_COLUMNS = (
    Column("name", "Galaxy", 132, _name_value, _name_text),
    Column("status", "Status", 64, _status_value, _status_text),
    Column("turn", "Turn", 46, _turn_value, _turn_text),
    Column("left", "Next turn", 84, _left_value, _left_text),
    Column("players", "Players", 54, _players_value, _players_text),
    # Wide enough for the longest thing it says, "joining next turn". A
    # minsize narrower than the cell would let that column push the table
    # wider than the home page and resize the window on the way in.
    Column("you", "You", 120, _you_value, _you_text),
)

# The action column, which sorts nothing and so is not one of the above.
ACTION_WIDTH = 66

DEFAULT_SORT = "name"


def galaxy_column(key: str):
    """The column called `key`, or None."""
    return next((c for c in GALAXY_COLUMNS if c.key == key), None)


GALAXY_LEFT = galaxy_column("left")
# The two cells whose text is a function of the clock rather than of the
# listing. Both are rewritten between listings, which is two minutes apart, and
# a galaxy crossing into "stopped" in the middle of one must not wait for the
# next query to say so.
GALAXY_STATUS = galaxy_column("status")
LIVE_COLUMNS = (GALAXY_STATUS, GALAXY_LEFT)


def sort_galaxies(rows, key: str = DEFAULT_SORT, reverse: bool = False,
                  view: "View | None" = None):
    """`rows` in the order one column asks for.

    Ordered on the value the column computes and never on the text it draws,
    because the two are written for different readers.

    A row the column cannot speak for keeps to the bottom in both directions
    rather than swapping ends with the reversal. A galaxy with no turn yet is
    not the earliest turn and not the latest one, and either end would be a
    claim the row does not make.

    The sort is stable, so rows the column cannot tell apart stay in the order
    the listing handed them over in.
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

    Compared as written first, because a URL is a URL and there is nothing
    else to do with one. Two local paths are compared as paths as well:
    multiplayer.json is typed by a player and a directory's index is written
    by a tool, so the same folder arrives here spelled two ways.
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
    """Which control this row offers: PLAY, VIEW, or nothing at all.

    Play is any galaxy you are in, and it is what the Multiplayer button used
    to do. Every galaxy you hold a seat in offers it, because being in a galaxy
    is membership and a player may be in several; which one is being played is
    a question for the turn loop, which follows one at a time and says so when
    Play is pressed on a second. View is a galaxy you could join: it opens the
    notice, which is where the joining is confirmed, so the row itself joins
    nothing.

    A closed galaxy offers neither, whoever is in it, because a turn played
    into one is refused at the submission. Nor does a galaxy this launcher has
    already asked for a seat in: the request is with the worker and there is
    nothing for a second click to do.

    `own` is the store multiplayer.json names, and it is offered Play whatever
    the roster says. It is the galaxy the launcher was pointed at by hand, its
    state may not even be readable from here, and the checks in
    start_multiplayer are what answer whether a turn can be taken in it.
    """
    if g.status == CLOSED:
        return None
    if same_store(g.store, own):
        return PLAY
    if g.joined:
        return PLAY
    if pending_join(g, recs):
        return None
    return VIEW if joinable(g, recs) else None


class OwnGalaxy(typing.NamedTuple):
    """A row for the galaxy no directory lists.

    The same facts a directory's row carries, declared here rather than
    borrowed from `galaxy_directory`, because a build frozen without that
    module still has a multiplayer.json to show and a row type that is allowed
    to be missing is no row type at all.
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


def store_label(spec: str) -> str:
    """What to call a galaxy that is only a store spec.

    The last part of it, which is the folder or the host a player would
    recognise, because a folder of turns and a LAN referee are registered
    nowhere and have no name but where they are.
    """
    spec = str(spec)
    return os.path.basename(spec.rstrip("/\\").replace("\\", "/")) or spec


def named_galaxy(spec: str, state, player: "str | None" = None) -> OwnGalaxy:
    """The row for a store multiplayer.json names, built from its own state.

    Identified by the spec, because that is all there is: a folder of turns
    and a LAN referee are registered nowhere and have no id but where they
    are. Shown under the name `store_label` reads out of it.

    A store whose state cannot be read is forming rather than absent, for the
    reason a directory's row gives: the referee publishes the first turn some
    time after the folder exists, and a row that vanished in between reads as
    the galaxy having failed.
    """
    spec = str(spec)
    name = store_label(spec)
    if not state:
        return OwnGalaxy(spec, name, FORMING, None, None, 0, False, spec)
    civs = list(state.get("civs", []))
    return OwnGalaxy(spec, name, state.get(STATUS_KEY) or OPEN,
                     state.get("turn"), state.get("deadline"), len(civs),
                     bool(player) and player in civs, spec,
                     state.get("turn_seconds"))


def own_row(data_dir: str, cfg, player: "str | None" = None) -> OwnGalaxy:
    """Read the galaxy multiplayer.json names and turn it into a row.

    Opened exactly as playing it opens it, `store_wants_token` and all, so a
    folder and a LAN referee are read with no identity and no sign-in. A store
    that cannot be reached is a row saying so rather than a listing that
    fails: this is the one galaxy the player definitely wants to see.
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

    The galaxy multiplayer.json names goes first and goes in whether or not
    the directory answered, because a player who has a store and no directory
    still has a galaxy to play and a page that showed them nothing would be
    the launcher looking broken.

    A directory that cannot be reached is a problem reported beside whatever
    rows there are rather than instead of them, for the same reason.

    Called on a worker thread. Everything here reads a store or a service.
    """
    rows, problem = [], None
    if spec:
        try:
            # The same rule a store follows, and asked the same way so that the
            # `auth` key which points the launcher at a local emulator answers
            # for the listing as well as for the galaxy: an https directory is
            # the relay and wants this install's identity, a folder does not
            # read one, and a player who is offline should not be waiting on a
            # sign-up to see the galaxy they already have.
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
    # Unless the directory already listed it. A registered galaxy has a name
    # the operator gave it and a row read in the same query as the rest, and
    # two rows for one galaxy reads as a fault. It is still played from the
    # store multiplayer.json names, because `row_action` matches that row by
    # its store rather than by where the row came from.
    if cfg and not any(same_store(g.store, cfg["store"]) for g in rows):
        rows.insert(0, own_row(data_dir, cfg, player))
    return rows, problem


# ── The notice a player reads before joining ──────────────────────────────────
# server\beta_notice.py holds the words, in a .txt beside it, because they are
# for players rather than for programmers. The launcher's half is to show them
# where they cannot be walked past: on the join step, with the Join control on
# the far side of them, rather than behind a help menu.
#
# Three values in that text are left to whoever shows it. Two are the galaxy's
# own miss thresholds, which are per galaxy, so the numbers a player reads are
# the ones the galaxy they are joining actually enforces rather than the
# defaults. The third says what happens to the log copy, and the launcher fills
# it from what the launcher does today.
#
# Every way of not having the whole notice is refused rather than shown short.
# A rule with a blank where the number should be reads as no rule at all, and a
# missing notice reads as there being nothing to agree to.

# What can honestly be said about the log copy today. M1 built the redaction
# and nothing uploads: write_redacted_log writes the copy beside launcher.log
# and the upload itself waits on the relay. This is the clause to change when
# that lands, and it is deliberately the narrower of the two claims the notice
# offers, because the notice is read once and believed afterwards.
LOG_UPLOAD_CLAUSE = ("Nothing is sent by itself. The launcher writes that "
                     "copy to a file on your own machine, and it reaches us "
                     "only if you send it to us yourself.")


def beta_notice_module():
    """server\\beta_notice, or None when this build cannot reach it.

    Found the way `galaxy_directory` is, and answering None the same way, so
    that a build frozen without it is a join refused with a reason rather than
    a launcher that will not start.
    """
    multiplayer_modules()
    try:
        import beta_notice
    except ImportError:
        return None
    return beta_notice


def beta_notice_text(state):
    """(notice, None) as this galaxy's player must read it, or (None, reason).

    `state` is the galaxy being joined, for any value the notice wants to state
    per galaxy. It states none today.

    The reason is for showing rather than for swallowing. A build without the
    module, a build without the file, and a marker nobody filled are three
    different faults and one outcome for the player, so each of them refuses
    here and says which it was.

    **The notice no longer states the miss thresholds**, by a deliberate
    editorial decision: turn limits are explained elsewhere and not on this
    page. So the two markers are gone and with them the refusal that fired when
    a build could not fill them. `miss_thresholds` and the `abandonment` import
    behind it went at the same time and for the same reason. Any future value
    the notice wants per galaxy goes in `values` and gets its own marker back.
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

# The window's own padding, in one place. Every child of the root is inset by
# PAD_X, and anything with a bottom edge of its own is inset from it by PAD_Y,
# which is the number the panels already use between their border and their
# last row. A panel that padded one edge and not the other is the defect this
# replaces: the eye reads the missing gap before it reads anything in the box.
PAD_X = 26
PAD_Y = 12

# Inside a bordered panel, between its edge and its contents. Equal on all four
# sides for the same reason.
PANEL_PAD = 12

# How a page sits in the window. Both pages take it, so they line up with the
# header above them and with each other, and both edges are padded.
PAGE_PACK = {"fill": "both", "expand": True, "padx": PAD_X,
             "pady": (PAD_Y, PAD_Y)}

# What the galaxy table needs across, its panel and that panel's border
# included. The home page is held to the same width, so moving between the two
# pages does not resize a window that cannot be resized by hand.
PAGE_WIDTH = (sum(c.width for c in GALAXY_COLUMNS) + ACTION_WIDTH
              + 2 * PANEL_PAD + 2)

# ── The capture indicator ─────────────────────────────────────────────────────
# What the turn loop last did with the player's work, beside the dot that says
# the server is up. The question it answers is the one a player cannot answer
# from the log: is what I have just done safe yet.
#
# Three states have to be distinguishable at a glance, because the player's next
# move depends on which one it is. A capture running now means wait a moment. A
# capture taken and not sent means the work is on this disk but not in the
# galaxy, so closing the machine loses it. Sent means the galaxy has it and the
# turn would survive this launcher being closed.
CAPTURING = "capturing"     # the client is being asked for the player's state
HELD = "held"               # captured here, not in the store yet
SENT = "sent"               # the store holds everything captured
CAPTURE_FAILED = "failed"   # the last capture did not come back


def capture_readout(state, age: float):
    """(text, colour) for the capture indicator, or ("", colour) to hide it.

    `age` is how long ago the state was recorded. It is in the text for the two
    states where staleness is the thing worth knowing: "sent 4m ago" on a turn
    the player is still playing says the cadence is working, and the same line
    frozen at "sent 40m ago" says it is not.
    """
    if state == CAPTURING:
        return "saving your turn…", WARN
    if state == HELD:
        return f"saved here {fmt_ago(age)}, not sent yet", WARN
    if state == SENT:
        return f"turn sent {fmt_ago(age)}", OK
    if state == CAPTURE_FAILED:
        return "could not save your turn", BAD
    return "", FAINT


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
        # Which galaxy that loop is following, for the refusal a second Play
        # gets. A name and a store spec rather than the row, because the row
        # goes stale with the next listing and this has to name the galaxy for
        # as long as it is being played.
        self.mp_playing = None
        self.mp_stop = False
        self.mp_note = ""
        self.mp_turn = None
        # When this turn closes, as an absolute time. The turn loop reads the
        # store on a schedule of its own and says how long is left with every
        # `playing`, so the readout subtracts from this rather than asking the
        # store once a second: see player_turn.POLL_SHARE for what one costs.
        self.mp_deadline = None
        # This galaxy's turn length, read once with the state that start_
        # multiplayer already reads, and the scale `stalled_for` judges a
        # passed deadline against.
        self.mp_turn_seconds = None
        # Whether the loop is between turns rather than in one. Every other
        # note the readout can show is about something this launcher is doing
        # now and outranks a galaxy that has stopped; this is the state in
        # which it has nothing else to say and the player is watching a clock
        # that has run out.
        self.mp_waiting = False
        # What the loop last did with the player's state, and when: one of the
        # capture states above with a timestamp, or None before anything has
        # been captured. Recorded by _mp_state on the worker thread and painted
        # by _refresh_turn on the Tk thread, which is the route mp_note already
        # takes.
        self.mp_capture = None
        # Set by Save during a multiplayer turn, cleared by the loop when it
        # acts on it. An Event rather than a second call into the client,
        # because the loop is already driving that client.
        self.mp_send_now = threading.Event()
        # Set by Play on the galaxy this loop is already following once its
        # game window has closed, cleared by the loop when it opens the game
        # again. The loop owns the client, so Play asks it rather than
        # starting a second one, which is what the lock in game_cycle exists
        # to prevent. `mp_reopening` is the Tk side of the same thing: what
        # the status line and the turn readout say between the press and the
        # client coming back up.
        self.mp_reopen = threading.Event()
        self.mp_reopening = False
        # Whether the game client was up when the watcher last looked, so that
        # it closing clears the turn readout at once rather than at the next
        # poll, and whether the turn it was playing is still on its way to the
        # galaxy afterwards.
        self.mp_client_seen = False
        self.mp_client_gone = False
        self.mp_sending = False
        self.mp_turn_open = False
        # The Galaxies page: which directory it lists, the store
        # multiplayer.json names outright, and whether a listing is in flight.
        # The directory is None when multiplayer.json names a store and no
        # directory, which is the folder and LAN path and reaches no service.
        self.games_dir = None
        self.own_store = None
        self.games_busy = False
        # The last listing, kept so that sorting a column repaints the table
        # without going back to the directory for the same rows. None until
        # one has been asked for, which is what the page says while it waits.
        self.games_rows = None
        self.games_extra = {}
        self.games_err = None
        self.games_recs = {}
        self.sort_key = DEFAULT_SORT
        self.sort_desc = False
        # Which page the window is showing, and the widgets the table owns:
        # the cells, thrown away on every repaint, and the countdown cells,
        # which are rewritten once a second without disturbing the order.
        self.page = "home"
        self._cells: "list" = []
        self._clocks: "list" = []
        # This player's name, read from the data directory at boot. None until
        # they have entered one, which is a state the launcher runs in happily:
        # only multiplayer needs to know who you are.
        self.player = None
        # The status to fall back to whenever no game is running. Recorded when
        # the server settles so that a game exiting restores whatever was true
        # then , "server running", "port taken", "reusing the existing server" ,
        # rather than a guess.
        self._ready: "tuple[str, str]" = ("starting…", WARN)
        # Whether THIS launcher's server is the one on the port. False while a
        # foreign server holds it, which is a state multiplayer cannot run in:
        # the turn would be captured into that server's data directory.
        self.server_ok = False
        # Set when a server this launcher did not start is being shared.
        self.save_dir = None
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
        pad = {"padx": PAD_X}

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

        # The window's body, which is one page at a time. The home page holds
        # the mode cards; Multiplayer swaps in the Galaxies page and Back swaps
        # it out again.
        self.pages = tk.Frame(self.root, bg=BG)
        self.pages.pack(**PAGE_PACK)
        # One width for both pages, so that navigating does not resize a window
        # that cannot be resized by hand. Without it the window would widen
        # when the table appears and narrow again on the way back.
        tk.Frame(self.pages, bg=BG, width=PAGE_WIDTH, height=1).pack()
        self.home_page = tk.Frame(self.pages, bg=BG)
        self.galaxies_page = tk.Frame(self.pages, bg=BG)
        self.home_page.pack(fill="both", expand=True)

        for mode in self.modes:
            card = tk.Frame(self.home_page, bg=PANEL, highlightbackground=EDGE,
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

        self._build_galaxies(self.galaxies_page)

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
        # The capture indicator, beside the server's dot and built the same
        # way. Packed only while a turn loop is running: outside multiplayer
        # there is nothing being captured and an idle second dot reads as a
        # second thing that could be wrong.
        self.cap_dot = tk.Label(status, text="●", bg=BG, fg=FAINT,
                                font=("Segoe UI", 11))
        self.cap_status = tk.Label(status, text="", bg=BG, fg=DIM,
                                   font=("Segoe UI", 9))
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

        tk.Frame(self.root, bg=BG, height=PAD_Y).pack()

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

        # What the Galaxies page will list, before the game files are looked
        # for: which galaxies exist is worth showing even to an install that
        # cannot start one. Nothing is listed until the page is opened.
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
            # A server on this port that answers the protocol used to be reused.
            # It cannot be: every capture a turn takes lands in whatever data
            # directory THAT server was started with, and there is no message in
            # the protocol to ask it which one. On the night of 25 September a
            # checkout cs_server writing to server\saves held the port while
            # this launcher looked in release\data\saves, and every turn died
            # two seconds after serving with "SaveGame succeeded but no capture
            # appeared , is the launcher's server running?", which was true of
            # a server that was not this one. Half-working that way is worse
            # than refusing, so this refuses.
            ours = stub_server_answers(host, port)
            adopt = adopted_server(self.data_dir)
            if ours and adopt and os.path.isdir(adopt):
                # The operator has said whose server it is and where it
                # writes, which is the one fact the protocol cannot supply.
                # On a machine that is both the referee and a player this is
                # every time: the unattended worker holds the port.
                self.save_dir = adopt
                self.server_ok = True
                self.set_ready_status(
                    f"sharing the server on {host}:{port}", OK)
                self.say(f"port {port} is held by a server this launcher did "
                         f"not start; sharing it as multiplayer.json asks")
                self.say(f"captures are collected from {adopt}")
                return
            if ours and adopt:
                # A directory that is not there fails exactly the way the
                # bug did, so it is refused rather than believed.
                self.say(f"adopt_server names {adopt}, which is not a "
                         f"directory; not sharing the server")
            self.set_ready_status(f"port {port} is held by another server", BAD)
            self.say(f"port {port} is in use by "
                     + ("a server speaking our protocol" if ours
                        else "something that did not answer testconnection")
                     + " , not reusing it")
            self.warn(
                f"Another server already holds port {port}.\n\n"
                + ("It speaks this game's protocol, so it is probably a second "
                   "copy of this launcher or a cs_server you started yourself. "
                   "It cannot be shared: it keeps its saved turns in its own "
                   "folder, this launcher would look for them in\n"
                   f"{os.path.join(self.data_dir, 'saves')}\n\nand every turn "
                   "would be captured somewhere this launcher never reads.\n\n"
                   if ours else
                   "The game can only talk to that exact port, so multiplayer "
                   "and TestBed will not work until it is free. Tutorial and "
                   "Demo are unaffected.\n\n")
                + "Close it and start this launcher again.")
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
            # Navigation, not a launch. Which galaxy to play is a choice the
            # player makes in front of the list, and a button that guessed it
            # for them is what the Galaxies page replaces.
            self.show_galaxies()
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

    # ── The Galaxies page ────────────────────────────────────────────────────
    def _build_galaxies(self, parent):
        """The page the Multiplayer button opens: a sortable table of galaxies.

        The header, the status line and the column headings are built once and
        never thrown away, so Back is on screen while a listing is in flight
        and after one has failed. A repaint replaces the cells under the
        headings and touches nothing else.
        """
        tk = self.tk
        head = tk.Frame(parent, bg=BG)
        head.pack(fill="x")
        back = tk.Button(head, text="‹ Back", bg=BTN, fg="#ffffff",
                         activebackground=BTN_HI, activeforeground="#ffffff",
                         relief="flat", bd=0, cursor="hand2", width=8,
                         font=("Segoe UI", 9, "bold"), command=self.show_home)
        back.pack(side="left")
        back.bind("<Enter>", lambda e: back.configure(bg=BTN_HI))
        back.bind("<Leave>", lambda e: back.configure(bg=BTN))
        tk.Label(head, text="Galaxies", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 13, "bold")).pack(side="left", padx=(12, 0))
        self.games_refresh = tk.Label(head, text="refresh", bg=BG, fg=FAINT,
                                      font=("Segoe UI", 8, "underline"),
                                      cursor="hand2")
        self.games_refresh.pack(side="right")
        self.games_refresh.bind("<Button-1>", lambda e: self.refresh_games())

        self.games_status = tk.Label(parent, text="", bg=BG, fg=DIM,
                                     anchor="w", justify="left",
                                     wraplength=PAGE_WIDTH,
                                     font=("Segoe UI", 8))
        self.games_status.pack(fill="x", pady=(8, 0))

        panel = tk.Frame(parent, bg=PANEL, highlightbackground=EDGE,
                         highlightthickness=1)
        panel.pack(fill="both", expand=True, pady=(6, 0))
        self.games_table = tk.Frame(panel, bg=PANEL)
        self.games_table.pack(fill="both", expand=True, padx=PANEL_PAD,
                              pady=PANEL_PAD)
        for i, col in enumerate(GALAXY_COLUMNS):
            # Only the name stretches. The rest are as wide as their heading
            # needs, so the numbers stay in a column the eye can run down.
            self.games_table.grid_columnconfigure(
                i, minsize=col.width, weight=1 if col.key == "name" else 0)
        self.games_table.grid_columnconfigure(len(GALAXY_COLUMNS),
                                              minsize=ACTION_WIDTH)
        self.games_heads = {}
        for i, col in enumerate(GALAXY_COLUMNS):
            lab = tk.Label(self.games_table, bg=PANEL, fg=DIM, anchor="w",
                           cursor="hand2", font=("Segoe UI", 8, "bold"))
            lab.grid(row=0, column=i, sticky="w", padx=(0, 8), pady=(0, 6))
            lab.bind("<Button-1>", lambda e, k=col.key: self.sort_by(k))
            self.games_heads[col.key] = lab
        self._show_sort()

        self.games_hint = tk.Label(parent, bg=BG, fg=WARN, anchor="w",
                                   justify="left", wraplength=PAGE_WIDTH,
                                   font=("Segoe UI", 8))

    def show_page(self, name: str):
        """Swap the window's body between the launcher's two pages."""
        if self.page == name:
            return
        self.page = name
        showing = self.home_page if name == "home" else self.galaxies_page
        hidden = self.galaxies_page if name == "home" else self.home_page
        hidden.pack_forget()
        showing.pack(fill="both", expand=True)

    def show_home(self):
        self.show_page("home")

    def show_galaxies(self):
        """What the Multiplayer button does now: open the list, play nothing.

        The listing is asked for on the way in rather than kept warm behind
        the home page. A listing is a metered read in the beta, and a launcher
        nobody is looking at has no use for a fresher one.
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
        for col in GALAXY_COLUMNS:
            lab = self.games_heads.get(col.key)
            if lab is None:
                continue
            here = col.key == self.sort_key
            mark = (" ▾" if self.sort_desc else " ▴") if here else ""
            lab.configure(text=col.heading + mark, fg=ACCENT if here else DIM)

    def _games_tick(self):
        """Re-list on a slow timer, and keep the timer whatever a listing did.

        Only while the page is on screen. A countdown nobody is looking at is
        worth less than the reads a poll behind a hidden page would spend.
        """
        if self.page == "galaxies":
            self.refresh_games()
        self.root.after(GAMES_POLL_MS, self._games_tick)

    def refresh_games(self):
        """List off the Tk thread and repaint when it answers.

        Off the thread because a directory is a network service in the beta,
        and because the galaxy multiplayer.json names is opened and read here
        as well. This window is also the game's server: blocking it stops the
        server answering the client.
        """
        if self.games_busy or not (self.games_dir or self.own_store):
            return
        self.games_busy = True
        self.games_status.configure(text="listing galaxies…", fg=DIM)
        spec, player, data_dir = self.games_dir, self.player, self.data_dir
        recs = load_joined(data_dir)

        def work():
            extra = {}
            rows, err = galaxy_rows(data_dir, spec, player)
            if recs and player:
                extra["submitted"] = self._submitted_state(rows, recs, player)
            self.msgs.put(("__games__", rows, extra, err))

        threading.Thread(target=work, daemon=True, name="games").start()

    def _submitted_state(self, rows, recs, player):
        """Which of this player's galaxies have had the current turn handed in.

        One extra read per galaxy this launcher holds a seat in, which is why
        it is not asked of every row: a listing is one query and each of these
        is a request per refresh on a store that may charge for it. A galaxy
        left out of the answer is one that could not be read, which its row
        prints as "you are in" rather than as "you have not played".

        Each galaxy is asked for under the name its own seat was claimed
        under, because a player who has since changed their name still holds
        the seat they joined with.

        Total, because the listing is posted back to the window after this and
        an exception here would leave the page saying it is still listing.
        """
        out = {}
        for g in rows:
            rec = recs.get(g.id)
            if rec is None or not g.joined or g.turn is None:
                continue
            try:
                mods = multiplayer_modules()
                if mods is None:
                    return out
                _player_turn, turn_store = mods
                token = (player_token(self.data_dir)
                         if store_wants_token({"store": g.store}) else None)
                store = open_player_store(turn_store, g.store, token)
                out[g.id] = bool(store.has_submitted(rec.get("name") or player,
                                                     g.turn))
            except Exception:               # a readout is not worth an error path
                continue
        return out

    def _on_games(self, rows, extra, err):
        """Take a listing in. Runs on the Tk thread, off the message queue."""
        self.games_busy = False
        self.games_rows = list(rows)
        self.games_extra = extra or {}
        self.games_err = err
        if err:
            self.say(f"games   {err}")
        self._draw_galaxies()

    def _cell_colour(self, col, g, view, near: bool) -> str:
        """What colour one cell is drawn in.

        Ordinarily how close the galaxy is to this player: lit for the ones
        that are their business and dim for the rest. A stopped galaxy takes
        the warning colour in its status cell whether or not they are in it,
        because the row has to be different at a glance and a dim row that
        merely reads differently is not.
        """
        if col is GALAXY_STATUS and row_stalled_for(g, view):
            return WARN
        return TEXT if near else DIM

    def _galaxy_view(self):
        """What a cell needs to know that its own row does not carry."""
        return View(now=time.time(), recs=self.games_recs,
                    submitted=self.games_extra.get("submitted"),
                    own=self.own_store)

    def _draw_galaxies(self):
        """Paint the table from the last listing, in the order asked for."""
        tk = self.tk
        for widget in self._cells:
            widget.destroy()
        self._cells, self._clocks = [], []
        self.games_recs = load_joined(self.data_dir)
        view = self._galaxy_view()
        rows = sort_galaxies(self.games_rows or [], self.sort_key,
                             self.sort_desc, view)
        self._show_sort()
        self._show_source(rows)
        for r, g in enumerate(rows, start=1):
            action = row_action(g, view.recs, view.own)
            # The rows lit are the ones that are this player's business: the
            # galaxy they are in, the one they have asked to join, and the one
            # they named themselves.
            near = bool(g.joined or action == PLAY
                        or pending_join(g, view.recs))
            for i, col in enumerate(GALAXY_COLUMNS):
                cell = tk.Label(self.games_table, text=col.text(g, view),
                                bg=PANEL, fg=self._cell_colour(col, g, view,
                                                               near),
                                anchor="w", font=("Segoe UI", 9))
                cell.grid(row=r, column=i, sticky="w", padx=(0, 8), pady=3)
                self._cells.append(cell)
                if col in LIVE_COLUMNS:
                    self._clocks.append((cell, col, g, near))
            if action is None:
                continue
            btn = tk.Button(self.games_table, text=ACTION_TEXT[action], bg=BTN,
                            fg="#ffffff", activebackground=BTN_HI,
                            activeforeground="#ffffff", relief="flat", bd=0,
                            cursor="hand2", width=6,
                            font=("Segoe UI", 9, "bold"),
                            command=lambda gg=g, a=action: self.on_row(gg, a))
            btn.grid(row=r, column=len(GALAXY_COLUMNS), sticky="e", pady=3)
            self._cells.append(btn)
        self._show_hint(rows, view)

    def _show_source(self, rows):
        """The line above the table: where these rows came from, or why not.

        A directory that could not be reached is reported here rather than in
        place of the table, because the galaxy multiplayer.json names is in
        the table whatever the directory did and is still playable.
        """
        if self.games_err:
            self.games_status.configure(text=self.games_err, fg=WARN)
        elif self.games_rows is None:
            self.games_status.configure(text="listing galaxies…", fg=DIM)
        elif not rows:
            self.games_status.configure(
                text="No galaxies are listed here yet.", fg=DIM)
        elif self.games_dir:
            self.games_status.configure(text=str(self.games_dir), fg=FAINT)
        else:
            self.games_status.configure(
                text=f"{MP_CONFIG} names this galaxy, so no directory is "
                     "listed.", fg=FAINT)

    def _show_hint(self, rows, view):
        """One line saying what to press when there is nothing to play yet.

        This is the hole the page was built to close. A player with a
        directory and no seat pressed Multiplayer, the launcher had no galaxy
        to play, and what they got was a roster refusal offering to change
        their name. The answer belongs in front of the list, beside the thing
        to press.

        A stopped galaxy takes the line ahead of either. Both of the others
        are about what there is to play; this one is the only state in the
        page a player is asked to act on, and telling them to press View
        instead would be the launcher watching a dead referee and talking
        about something else.
        """
        actions = [row_action(g, view.recs, view.own) for g in rows]
        stalled = [(g, s) for g, s in
                   ((g, row_stalled_for(g, view)) for g in rows) if s]
        text = ""
        if stalled:
            text = stall_hint(stalled)
        elif rows and PLAY not in actions:
            if VIEW in actions:
                text = ("You are not in a galaxy yet. Press View on one to "
                        "read what you would be joining, and join from "
                        "there.")
            elif any(pending_join(g, view.recs) for g in rows):
                text = ("Your join is with the galaxy. A seat appears at the "
                        "next turn boundary, and Play appears with it.")
        if text:
            # Only on a change. This is called once a second now, and the
            # stopped line's own clock rounds to minutes, so repainting a
            # label that has not moved is a redraw a second for nothing.
            if self.games_hint.cget("text") != text:
                self.games_hint.configure(text=text)
            if not self.games_hint.winfo_ismapped():
                self.games_hint.pack(fill="x", pady=(8, 0))
        elif self.games_hint.winfo_ismapped():
            self.games_hint.pack_forget()

    def _tick_clocks(self):
        """Keep the countdowns and the stopped galaxies honest between
        listings.

        Those two columns are rewritten and the order is left alone. A table
        that re-sorted itself once a second would move the row being clicked
        on out from under the pointer.

        The hint goes with them. A galaxy crosses into stopped by the clock
        rather than by anything a listing says, and the line that tells a
        player what to do about it would otherwise appear up to two minutes
        after the row it explains.
        """
        view = self._galaxy_view()
        for cell, col, g, near in self._clocks:
            if not cell.winfo_exists():
                continue
            text = col.text(g, view)
            if cell.cget("text") != text:
                cell.configure(text=text)
            colour = self._cell_colour(col, g, view, near)
            if cell.cget("fg") != colour:
                cell.configure(fg=colour)
        self._show_hint(sort_galaxies(self.games_rows or [], self.sort_key,
                                      self.sort_desc, view), view)

    def on_row(self, g, action: str):
        """What a row's one button does. View reads, Play plays."""
        if action == PLAY:
            self.play_galaxy(g)
        else:
            self.on_join(g)

    def play_galaxy(self, g):
        """Take turns in one row's galaxy, which is what Multiplayer did.

        Home first. The turn readout, the turn controls and the status line
        are on the home page, and a session started from a page the player
        then has to leave is a session they cannot watch.
        """
        mode = next((m for m in self.modes if m.get("id") == "multiplayer"),
                    None)
        if mode is None or not is_playable(mode):
            self.warn("This build does not offer multiplayer.")
            return
        self.show_home()
        self.start_multiplayer(mode, g)

    def show_beta_notice(self, body: str, g) -> bool:
        """Show the notice and answer whether the player joined from it.

        Modal, and the only way past it is the Join button under the text. The
        done condition for this text is that it is read before joining rather
        than buried, so the button that joins is on the far side of it and
        there is no other route to one: the row's Join opens this, and this
        opens nothing else.

        The text is scrolled rather than shortened. It is the whole of what a
        player is agreeing to, and a summary with a Join button under it is the
        thing this exists instead of.
        """
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.title(self.cfg["product"])
        win.configure(bg=BG)
        win.transient(self.root)

        notice = beta_notice_module()
        heading = notice.title(body) if notice else "Before you join the beta"
        tk.Label(win, text=heading, bg=BG, fg=ACCENT,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=20,
                                                     pady=(18, 0))
        tk.Label(win, text=f"You are about to join {g.name or g.id}.",
                 bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(anchor="w", padx=20,
                                                           pady=(2, 10))

        frame = tk.Frame(win, bg=BG)
        frame.pack(fill="both", expand=True, padx=20)
        bar = tk.Scrollbar(frame, orient="vertical")
        text = tk.Text(frame, width=78, height=22, bg=PANEL, fg=TEXT,
                       relief="flat", wrap="word", padx=10, pady=8,
                       highlightbackground=EDGE, highlightthickness=1,
                       font=("Consolas", 9), yscrollcommand=bar.set)
        bar.configure(command=text.yview)
        bar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.insert("1.0", body)
        # After the insert, or the player can edit the rules they are agreeing
        # to before agreeing to them.
        text.configure(state="disabled")

        joined = {}

        def accept():
            joined["yes"] = True
            win.destroy()

        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=20, pady=(14, 18))
        tk.Button(row, text="Join", bg=BTN, fg="#ffffff",
                  activebackground=BTN_HI, activeforeground="#ffffff",
                  relief="flat", bd=0, cursor="hand2", width=12,
                  font=("Segoe UI", 9, "bold"), command=accept).pack(
                      side="right", padx=(6, 0))
        tk.Button(row, text="Not yet", bg=PANEL, fg=TEXT,
                  activebackground=EDGE, activeforeground="#ffffff",
                  relief="flat", bd=0, cursor="hand2", width=12,
                  font=("Segoe UI", 9, "bold"), command=win.destroy).pack(
                      side="right", padx=(6, 0))

        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        try:
            win.grab_set()
        except tk.TclError:
            pass
        self.root.wait_window(win)
        return bool(joined.get("yes"))

    def on_join(self, g):
        """Ask a galaxy for a seat. The worker seats the player at the next turn.

        Everything here is a check the player can act on before anything is
        written: who they are, whether this build can play the galaxy at all,
        whether the galaxy is still running, and whether the name they typed is
        already somebody's seat. Then the notice, which is the last thing
        between a player and a seat. The request itself is the step after that,
        so a refusal and a change of mind alike leave nothing behind.
        """
        from tkinter import messagebox
        civ = self.player or self.ask_player_name(
            "Before you can join a galaxy, this launcher needs to know who you "
            "are.")
        if not civ:
            self.say("join: no player name entered, not joining")
            return

        # Nothing is asked about the galaxies this launcher has already
        # joined. A seat in one is not a reason to refuse a seat in another:
        # joining is membership, and which of them is being played is settled
        # when Play is pressed.
        rec = load_joined(self.data_dir).get(g.id)

        mods = multiplayer_modules()
        if mods is None:
            self.warn("This build cannot play multiplayer.\n\nThe turn "
                      "machinery is missing from it. Everything else in this "
                      "launcher works as normal.")
            return
        _player_turn, turn_store = mods

        cfg = {"store": g.store}
        token = player_token(self.data_dir) if store_wants_token(cfg) else None
        try:
            store = open_player_store(turn_store, g.store, token)
            state = store.state() if store.exists() else None
        except Exception as exc:            # any store; unreachable is usual
            self.say(f"join: could not read {g.id} ({exc})")
            self.warn(f"Could not reach {g.name or g.id}:\n\n{exc}")
            return

        # Ahead of the seat check, for the reason start_multiplayer gives: a
        # build this galaxy will not accept is why anything further down might
        # look wrong, so it is answered first.
        if state is not None:
            problem = version_problem(build_id(), state)
            if problem:
                self.say(f"join: build {build_id()} is below {g.id}'s minimum "
                         f"{state.get(MIN_BUILD_KEY)!r}")
                self.warn(problem)
                return
            # Beside it, and ahead of the seat: the listing that offered this
            # Join is as old as the last refresh, and a galaxy closed since
            # then would otherwise take a request nothing will ever read.
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
            if messagebox.askyesno(self.cfg["product"],
                                   problem + "\n\nChange your name now?"):
                self.on_change_name()
            return

        # The last thing before the request, and the only one that is not a
        # check: what a player is agreeing to is read here, with the Join on
        # the far side of it, because a notice shown after the seat is asked
        # for is a notice shown after the decision.
        notice, why = beta_notice_text(state)
        if notice is None:
            self.say(f"join: the beta notice is not showable ({why})")
            self.warn("This build cannot show you what you are joining.\n\n"
                      f"{why}.\n\nJoining is stopped here rather than done "
                      "without it, because what would be missing is the rules "
                      "of the beta rather than a detail of them.")
            return
        if not self.show_beta_notice(notice, g):
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

        save_joined(self.data_dir, joined_record(g, civ, uid, self.games_dir))
        self.say(f"join: asked {g.id} for a seat as {civ} ({where})")
        when = f"turn {g.turn + 1}" if g.turn is not None else "the first turn"
        messagebox.showinfo(
            self.cfg["product"],
            f"You have asked to join {g.name or g.id} as {civ}.\n\nA galaxy "
            "takes new players at a turn boundary, so your empire appears at "
            f"{when}.\n\nIts row here grows a Play button once it does, and "
            "the launcher plays every turn from there.")
        self.refresh_games()

    # ── Multiplayer session ──────────────────────────────────────────────────
    def galaxy_config(self, g):
        """A multiplayer.json-shaped config for the galaxy about to be played.

        A row from the page names its own store, and that is what is played,
        whether it came from the directory or from multiplayer.json. The
        joined record is carried along when it is that row's, because the seat
        may have been asked for under a name the player has since changed.

        No row at all is the old question: whatever this launcher plays.
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

        The one place that answers whether a galaxy is being played. A player
        may hold seats in several, and every one of those rows offers Play;
        what there is only one of is this loop, because it drives the game
        client and the machine runs one of those.

        A running loop with nothing recorded against it answers with an empty
        record rather than with None. What the caller acts on is that a loop
        is running, and answering None there would let a second one start.
        """
        if self.mp_thread is None or not self.mp_thread.is_alive():
            return None
        return self.mp_playing or {}

    def _take_turn_loop(self, g) -> bool:
        """Whether the turn loop is free for this galaxy, asking to switch.

        This is where one-at-a-time lives, and it is about playing rather than
        about joining. A player can be in as many galaxies as they like; the
        turn loop starts the game client, the machine runs one of those, so
        two loops would be two games fighting over it.

        A second Play names the galaxy already being followed rather than
        saying a galaxy is running, because "which one" is the question a
        player with several seats is actually asking. Answering yes stops that
        loop between turns and starts this one, which is the switch the
        refusal offers and is the whole of what has to be done by hand
        otherwise.

        The stop happens here, ahead of the checks below, because the first of
        them asks whether a game client is running and a loop still following
        a galaxy is the usual reason one is. A galaxy that then refuses the
        player leaves neither being followed, and Play on the first row starts
        it again.

        Play on the galaxy being followed, with its game window closed, is not
        a second loop and is not refused: it asks the loop that is running to
        open the game again. Answering False is still right, because what must
        not happen here is a second loop, and the reopen is the running one's
        to do.
        """
        playing = self.playing_now()
        if playing is None:
            return True
        here = playing.get("name") or playing.get("store") or "a galaxy"
        if g is not None and same_store(playing.get("store"), g.store):
            # "Already playing" is true of the loop and not of the game.
            # The loop outlives the window: once a turn is sent it waits for
            # the referee, and at four hours a turn that is most of the day,
            # so the two answer differently.
            if self.mp_client_gone:
                # Play here means open the galaxy again, which is what the
                # player is asking for: the turn is sent, and they want
                # another look at the galaxy, or at a message in it. The loop
                # is asked rather than a client started from here, for the
                # reason Save is asked, because the loop owns that client.
                #
                # What it opens is the player's own submission for the turn it
                # is on, so nothing they played is taken away, and the new
                # turn instead if the referee has published one meanwhile.
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
        # Only ask when there is a game to lose. The dialog is there so a
        # player mid-turn is not dropped out of it, and once they have
        # closed the window themselves there is nothing on screen to
        # interrupt: the loop is only waiting for the referee. Asking then
        # made switching feel like it needed permission it did not need.
        if not self.mp_client_gone:
            from tkinter import messagebox
            if not messagebox.askyesno(
                    self.cfg["product"],
                    f"This launcher is playing {here} right now.\n\nYou can be in "
                    "as many galaxies as you like, but a turn starts the game "
                    "itself and this machine runs one game, so one galaxy is "
                    f"played at a time.\n\nStop following {here} and play {there} "
                    "instead?"):
                self.say(f"multiplayer: still following {here}, not switching")
                return False
        else:
            self.say(f"multiplayer: {here} had no game open, switching to {there}")

        # The thread stop_multiplayer let go of. It clears the handle whether
        # or not the loop finished, and a second loop started over one that is
        # still capturing a turn is two of them driving the same client.
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

        # Every capture this turn takes arrives through this launcher's own
        # server, so a turn played without one is a turn that cannot be
        # collected. The boot said why; saying it again here is the difference
        # between a refusal and a turn that dies two seconds after it starts.
        if not self.server_ok:
            port = self.cfg["server"]["port"]
            self.warn(
                "This launcher's server is not running, so a turn played now "
                "could not be saved.\n\nAnother server holds port "
                f"{port}. Close it and start this launcher again.")
            return

        # The row the player pressed Play on, or, with no row, the galaxy this
        # launcher plays. Either way this is a store spec and nothing below
        # here knows which of the two it came from.
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
        #
        # A galaxy behind the beta's function is opened with this install's
        # Firebase identity on the requests. A folder and a LAN turn_server are
        # opened exactly as before, with no token and no sign-in. An identity
        # that cannot be minted, because this player is offline or the project
        # is not answering, is a missing header rather than a refusal to start:
        # what the galaxy does about that is the galaxy's to say.
        token = player_token(self.data_dir) if store_wants_token(cfg) else None
        if token is not None and token() is None:
            self.say("multiplayer: no Firebase identity available, opening the "
                     "galaxy without one")
        store = open_player_store(turn_store, cfg["store"], token)
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
            # Beside the build check and ahead of the roster, because a closed
            # galaxy is one of the reasons the roster would read wrong: it
            # holds whoever was in it when the operator ended it, and the turn
            # a player went on to serve would be refused at the submission.
            problem = closed_problem(state)
            if problem:
                self.say("multiplayer: this galaxy is closed")
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
            taken = reclaimed_seat(state, civ)
            problem = roster_problem(civ, seats, taken)
            if problem:
                # The count, not the names. launcher.log is a file the player
                # can open, so it is one more place the roster would leak from.
                if taken:
                    self.say(f"multiplayer: {civ!r} was reclaimed at turn "
                             f"{taken.get('turn')} after {taken.get('missed')} "
                             f"missed turn(s)")
                self.say(f"multiplayer: {civ!r} is not one of this galaxy's "
                         f"{len(seats)} seat(s)")
                if taken:
                    # A reclaimed seat is not a name to spell differently, so
                    # the offer that fits a misspelling is not made. The
                    # launcher's own record of the join is dropped instead,
                    # because the galaxy has undone the thing it records: left
                    # in place it makes the page show a join still waiting to
                    # land, which is a dead end rather than a row to join
                    # again from. Only a galaxy that was joined from the list
                    # has such a record; one named in multiplayer.json is the
                    # player's own choice and is left alone. This galaxy's
                    # record and no other: the seats this player holds
                    # elsewhere are still theirs.
                    if cfg.get("joined"):
                        clear_joined(self.data_dir,
                                     cfg["joined"].get("galaxy"))
                        self.refresh_games()
                    self.warn(problem)
                    return
                from tkinter import messagebox
                if messagebox.askyesno(
                        self.cfg["product"],
                        problem + "\n\nChange your name now?"):
                    self.on_change_name()
                return

        self.mp_store = store
        self.mp_civ = civ
        self.mp_playing = {"id": g.id if g is not None else cfg["store"],
                           "name": (g.name or g.id) if g is not None
                           else store_label(cfg["store"]),
                           "store": cfg["store"]}
        self.mp_stop = False
        self.mp_deadline = None
        # Out of the state read above rather than asked for again. A galaxy
        # whose state would not read leaves this None, which `stall_after`
        # answers with its ceiling.
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
        self.say(f"multiplayer: following {cfg['store']} as {civ}")
        self.set_status(f"Multiplayer , {civ}", OK)
        self._show_controls(True)

        # Where this launcher's captures land, which is not its own saves
        # directory when it is sharing a server it did not start.
        save_dir = self.save_dir or os.path.join(self.data_dir, "saves")
        os.makedirs(save_dir, exist_ok=True)

        def work():
            try:
                player_turn.follow(
                    store, civ,
                    # The floor of the read schedule rather than the whole of
                    # it. The last stretch before a deadline is read this
                    # often and the rest of the turn backs off towards
                    # player_turn.POLL_CEILING.
                    poll=2.0,
                    on_state=self._mp_state,
                    save_dir=save_dir,
                    stop=lambda: self.mp_stop,
                    send_now=self.mp_send_now,
                    reopen=self.mp_reopen,
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
        left = facts.get("seconds_left")
        if left is not None:
            # `playing` carries this several times a turn, on the loop's own
            # capture cadence, which is far more often than a countdown needs.
            self.mp_deadline = time.time() + left
        if kind in ("playing", "waiting", "overtaken", "lost", "done",
                    "stopped", "failed", "serve_failed"):
            # The reopen has either happened or is not going to. Left standing
            # it would say the game was opening for the rest of the gap
            # between turns.
            self.mp_reopening = False

        # The capture half of the readout. These arrive several times a turn
        # and say nothing about which step of the turn is running, so they are
        # recorded on their own and do not disturb the note beside the turn
        # number. A capture that matches what the store already holds counts as
        # sent, because that is what it means to the player: everything they
        # have done is in the galaxy.
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
        if kind == "submitted":
            self.mp_capture = (SENT, time.time())
            if facts.get("final"):
                self.mp_sending = False
        elif kind in ("waiting", "overtaken", "lost", "done",
                      "stopped", "failed"):
            # The turn is over, however it ended, so nothing is still on its
            # way. Left standing, "sending your turn" would sit there for the
            # whole gap between turns.
            self.mp_sending = False
            # And nothing closing the window afterwards can put it back. A turn
            # whose deadline passed while the game was still open finishes here,
            # and the player closes the window minutes later: `_watch_mp_client`
            # would otherwise announce that a turn sent long ago was on its way,
            # beside a capture dot correctly reading "turn sent 2m ago".
            self.mp_turn_open = False
        if kind in ("serving", "playing", "reopening"):
            self.mp_turn_open = True
        if kind == "serving":
            # A new turn: nothing of it has been captured yet, and leaving the
            # last turn's "turn sent" standing would say the new one is safe.
            self.mp_capture = None

        self.mp_note = {
            "serving": "starting your turn",
            "playing": "",
            "collecting": "time is up, orders are in",
            "submitted": "orders sent",
            "reopening": "opening the game again",
            "waiting": "waiting for the next turn",
            "overtaken": "that turn closed without you",
            "failed": f"stopped: {facts.get('error', 'unknown')}",
            "stopped": "stopped",
            "done": "finished",
        }.get(kind, kind)
        # Set beside the note and from the same kind, so the two cannot come
        # apart: the readout asks this before it falls back to "waiting for
        # the next turn", and a galaxy that has stopped is what that wait has
        # turned into.
        self.mp_waiting = kind == "waiting"
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
        self.mp_playing = None
        self.mp_note = ""
        self.mp_turn = None
        self.mp_deadline = None
        self.mp_turn_seconds = None
        self.mp_waiting = False
        self.mp_capture = None
        self.mp_client_seen = False
        self.mp_client_gone = False
        self.mp_sending = False
        self.mp_turn_open = False
        self.mp_send_now.clear()
        self.mp_reopen.clear()
        self.mp_reopening = False
        self._show_capture(None, 0.0)

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
            self.ctl_frame.pack(fill="x", padx=PAD_X, pady=(0, PAD_Y))
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
        """Save, which means two different things and says which one it means.

        In single player it writes a .dat the player can load again. During a
        multiplayer turn it means send my turn now: the turn loop is already
        driving this client on its own cadence, and a second SaveGame into
        data\\games would be a file nothing reads, taken from a client another
        thread is saving at the same moment. The loop is asked instead, which
        is why the two can never run at once against one client , the loop is
        the only thing that ever calls SaveGame while it is running.

        That is also the answer to waiting for the upload cadence. A player who
        has just done something they care about presses this and it goes.
        """
        if self.playing_now() is not None:
            self.mp_send_now.set()
            self.say("multiplayer: sending your turn now")
            return

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

    def _mp_stalled(self) -> float:
        """How long the galaxy being followed has been stopped, or 0.

        Only while the loop is between turns. During a turn the deadline is
        ahead and there is nothing to judge; afterwards it is the moment the
        referee should have closed the turn, and how far past it we are is how
        long the referee has been gone.

        Nothing here reads the store. The deadline arrives with the loop's own
        `playing` reports and the turn length was read once when the galaxy
        was opened, which is what keeps a once-a-second check free.
        """
        if not self.mp_waiting:
            return 0.0
        return stalled_for(self.mp_deadline, self.mp_turn_seconds)

    def _refresh_turn(self):
        """Update the turn readout
        """
        import gamectl
        if self.mp_store is not None:
            state, when = self.mp_capture or (None, time.time())
            # The indicator is about capturing, and a closed game is not being
            # captured from. It used to stick on "saving your turn..." for the
            # whole wait between turns: the last capture attempt emits
            # `capturing` before it asks the client, the client is gone so the
            # attempt fails, and the send that follows has nothing new to
            # store and returns without emitting anything. Nothing superseded
            # it. While the final submission is still in flight there is
            # something worth saying, so that case keeps the indicator.
            if self.mp_client_gone and not self.mp_sending:
                state = None
            self._show_capture(state, time.time() - when)
            if self.mp_client_gone:
                # The turn number and the countdown are facts about a client
                # that has gone. Holding them on screen until the next poll
                # tells the player their turn is still open when it is not,
                # and the one thing they do need to know instead is whether
                # the launcher is still sending it.
                #
                # The wait is also where a dead referee is seen from. A turn
                # the galaxy is never going to close looks exactly like one
                # about to be closed, and this line is what a player waiting
                # for a turn is looking at, so it is where the difference has
                # to be said. Only over the wait: everything above it is this
                # launcher's own work and is both truer and more urgent.
                stopped = self._mp_stalled()
                label = ("sending your turn" if self.mp_sending
                         else "opening the game again" if self.mp_reopening
                         else f"turns have stopped, due {fmt_ago(stopped)}"
                         if stopped
                         else self.mp_note or "waiting for the next turn")
                if self.turn_label.cget("text") != label:
                    self.turn_label.configure(text=label)
            else:
                # In multiplayer the clock belongs to the store, not to the
                # client: the client's own countdown is held far into the
                # future so it can never compute a turn the referee has not.
                #
                # It does not belong to this method either. Asking the store
                # here is two Firestore document reads, and this runs once a
                # second while a game window is open: 7,200 an hour against an
                # allowance of 50,000 a day for the whole project, for a
                # countdown whose only work is to subtract. The turn loop reads
                # the store against the deadline and reports what it found, so
                # this counts down from that and asks nothing.
                try:
                    if self.mp_turn is None or self.mp_deadline is None:
                        # Nothing heard from the loop yet, which is the gap
                        # between a galaxy being followed and its first turn
                        # being served. One read answers it and is kept.
                        self.mp_turn, self.mp_deadline = self.mp_store.current()
                    turn = self.mp_turn
                    left = self.mp_deadline - time.time()
                    label = f"turn {turn} \u00b7 {fmt_left(left)} left"
                    # A player who reopened the game into an overdue turn
                    # would otherwise read "0:00 left" for as long as the
                    # referee stays down, which is the countdown claiming a
                    # turn is about to close.
                    stopped = stalled_for(self.mp_deadline,
                                          self.mp_turn_seconds)
                    if stopped:
                        label = (f"turn {turn} \u00b7 turns have stopped, due "
                                 f"{fmt_ago(stopped)}")
                    if self.mp_note:
                        label = f"turn {turn} \u00b7 {self.mp_note}"
                    if self.turn_label.cget("text") != label:
                        self.turn_label.configure(text=label)
                except Exception:
                    pass
            for key, btn in self.ctl_buttons.items():
                # Next Turn and Load are meaningless here. The referee owns the
                # clock, and a player who ends their own turn early would be
                # asking for a state it has not computed. Save stays, and means
                # send this turn now: see on_save.
                # Save is the one that comes and goes: there is a turn to
                # send only while a client is up to take it from.
                live = key == "save" and not self.mp_client_gone
                want = "normal" if live else "disabled"
                if btn.cget("state") != want:
                    btn.configure(state=want,
                                  bg=BTN if want == "normal" else FAINT,
                                  cursor="hand2" if want == "normal" else "")
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

    def _set_save_text(self, text: str):
        """Name what the Save button will actually do, once."""
        btn = self.ctl_buttons.get("save")
        if btn is not None and btn.cget("text") != text:
            btn.configure(text=text)

    def _show_capture(self, state, age: float):
        """Paint the capture indicator, or take it away when there is none.

        Packed and unpacked rather than blanked, so that a launcher with no
        turn loop running shows one dot and one status, which is what it had
        before any of this.
        """
        text, colour = capture_readout(state, age)
        if not text:
            if self.cap_dot.winfo_ismapped():
                self.cap_status.pack_forget()
                self.cap_dot.pack_forget()
            return
        if not self.cap_dot.winfo_ismapped():
            self.cap_dot.pack(side="left", padx=(16, 0))
            self.cap_status.pack(side="left", padx=(6, 0))
        if self.cap_status.cget("text") != text:
            self.cap_status.configure(text=text)
        if self.cap_dot.cget("fg") != colour:
            self.cap_dot.configure(fg=colour)

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
            self.log_frame.pack(fill="both", expand=True, padx=PAD_X,
                                pady=(8, 0))
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
        # The table's countdowns, which are re-listed only every two minutes
        # and would otherwise sit there being wrong for most of that.
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

        if self.ai_child is not None and self.ai_child.poll() is not None:
            code = self.ai_child.returncode
            self.ai_child = None
            self.say(f"opponent exited (code {code}) , the other empire will "
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
                    "sending your turn , keep this window open", WARN)
            elif mp_live and self.mp_reopening:
                self._status_if_changed(
                    "Multiplayer , opening the game again", OK)
            elif mp_live and self.mp_client_gone:
                # The status line is the server's. It says what is running,
                # and between turns nothing is: the loop is waiting for the
                # referee, which at four hours a turn is most of the day. A
                # green "Multiplayer" through all of it reads as a game
                # session to a player who closed the game themselves.
                #
                # That the galaxy is still being followed is not lost, it is
                # on the Games page, where the row for a galaxy you are in
                # says so. This line goes back to what it says when the
                # launcher has just opened and is only holding the port.
                #
                # Unless the galaxy has stopped, which is the one thing about
                # the wait worth putting here. It is not the claim this branch
                # exists to avoid: it says nothing about a game being open or
                # a loop being alive, it says the referee has not closed a
                # turn that fell due long enough ago to be a fault, and that
                # is a thing the player has to act on rather than watch.
                stopped = self._mp_stalled()
                if stopped:
                    self._status_if_changed(
                        f"the galaxy has stopped , its turn was due "
                        f"{fmt_ago(stopped)}", WARN)
                else:
                    self._status_if_changed(*self._ready)
            elif mp_live:
                self._status_if_changed(
                    f"Multiplayer , {self.mp_civ}" if self.mp_civ
                    else "Multiplayer", OK)
            elif busy:
                self._status_if_changed(f"{self._name_for(busy)} is running", OK)
            else:
                self._status_if_changed(*self._ready)

        # The controls belong to a running game, and only to a mode that asked
        # for them: Tutorial and Demo have their own UI and must not grow a
        # Next Turn button that means nothing there.
        if mp_live:
            # The controls belong to a game window, not to the loop. Next Turn,
            # Send Turn and Load all act on a running client, and the loop
            # outlives the window by most of a four-hour turn, so leaving them
            # up left three dead buttons and a turn readout on screen for
            # hours. What is worth saying while waiting is already in the
            # status line above them. They come back when the client does.
            self._show_controls(not self.mp_client_gone)
            self._refresh_turn()
            self.root.after(1000, self._watch_game)
            return
        self._show_capture(None, 0.0)
        wants = bool(self.running_mode and self.running_mode.get("controls")
                     and self.child is not None)
        self._show_controls(wants)
        if wants:
            self._refresh_turn()
        self.root.after(1000, self._watch_game)

    def _watch_mp_client(self, up: bool):
        """Notice the multiplayer client coming and going.

        The turn loop starts its own client, so there is no child handle to
        watch and the only evidence is the process list this already reads once
        a second. What it is for is the moment the player closes the game
        window: the readout is a fact about that client and has to go at once,
        while the final capture and its submission carry on behind it.
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
            # The last capture and its submission run on the worker thread and
            # are not waited for here: the readout goes now, the sending says
            # so until the loop reports the turn finished.
            if self.mp_turn_open:
                self.mp_sending = True
                self.say("multiplayer: the game has closed, sending your turn")
            else:
                # The turn had already finished before the window closed, so
                # there is nothing on its way and saying so would contradict
                # the capture dot beside it.
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
                elif isinstance(msg, tuple) and msg and msg[0] == "__games__":
                    _tag, rows, extra, err = msg
                    self._on_games(rows, extra, err)
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
