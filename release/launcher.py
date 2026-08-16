"""
launcher.py — the player-facing front door for Cosmic Supremacy: Resurgence
===========================================================================
One window, one click per mode. It replaces three things a non-developer should
never have had to do: install Python, start the stub server from a terminal, and
drag the right .csgalaxy file onto the right EXE.

WHAT A MODE ACTUALLY IS. The client takes exactly one command-line argument: the
path to a .csgalaxy pass file. Dropping a file on an EXE in Explorer *is* that
argument — there is no other mechanism, and nothing about drag-and-drop was ever
load-bearing. So a mode is a pair, (which EXE, which pass file), and that pair
lives in manifest.json rather than in this file. Today the pairs span three EXEs
because the localhost redirect and the TestBed code patches have not been
reconciled into one binary; when they are, only the manifest changes.

THE SERVER RUNS IN THIS PROCESS. cs_server.py imports nothing outside the
standard library, so it is imported here and served on a daemon thread instead
of being shipped as a second executable the player has to keep alive. Two
consequences worth stating plainly:

  • Closing this window stops the server. TestBed needs it, so the window has to
    stay open for the session — the UI says so, and closing with a game running
    asks first.
  • The listener binds 127.0.0.1, not 0.0.0.0 as cs_server's own __main__ block
    does. A single-player game has no reason to be reachable from the LAN, and a
    player who unzips this on a coffee-shop network should not be publishing an
    unauthenticated save endpoint to the room.

RUNS FROM THE REPO TOO. Frozen, it looks for the game beside itself in game\\.
From a checkout it finds client\\ instead, so `python release\\launcher.py`
exercises the real launcher without a build. The two layouts are the reason for
the search lists in find_game_root / find_galaxy_root rather than one fixed path.
"""
from __future__ import annotations

import json
import os
import queue
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
    # that cannot exist — listing it in the not-found message is pure confusion.
    if not getattr(sys, "frozen", False):
        cands.append(os.path.join(_repo_root(), "client"))
    return cands


def find_game_root(modes) -> "str | None":
    """First directory that holds every EXE the manifest asks for."""
    wanted = {m["exe"] for m in modes if is_playable(m)}
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

    A placeholder — "enabled": false, or simply no EXE named — is shown as a
    greyed-out card advertising what is coming. It must not be counted when
    looking for the game files, or a release that ships no multiplayer client
    would decide the whole install is broken.
    """
    return bool(mode.get("enabled", True) and mode.get("exe") and mode.get("galaxy"))


def find_galaxy_root(game_root: str, modes) -> "str | None":
    wanted = {m["galaxy"] for m in modes if is_playable(m)}
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

    Frozen, the build packs cosmic.ico in beside the manifest. From a checkout
    nothing has been built, so the icon is extracted out of the client EXE on
    first run and cached in the data directory — the same code the build uses,
    so a dev run looks like the real thing.
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
    # Checked rather than returned from inside the try: extract_icon prints a
    # summary on success, and a console-less build would raise on that print
    # after the file was already written correctly.
    return cached if os.path.exists(cached) else None


def find_data_dir() -> str:
    """
    Where the server writes its log and captured saves.

    Beside the executable, so a player who wants to send us a log can find it
    without knowing what AppData is — unless that location is read-only, which is
    what unzipping into Program Files or running from the still-mounted zip
    produces. Falling back rather than dying keeps a wrong-place install playable.
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

    cs_server reads its environment at import time — PORT, and the DATA_DIR that
    the log, saves\\ and governor blobs hang off — so the environment has to be
    set before the import, not after. Hence the import sitting inside this
    function rather than at module scope.
    """
    os.environ["CS_DATA_DIR"] = data_dir
    os.environ["CS_GALAXY_DIR"] = galaxy_dir
    os.environ["CSPORT"] = str(port)

    if "cs_server" in sys.modules:
        raise RuntimeError("cs_server was imported before its environment was set")
    # Frozen, cs_server is packed in beside the launcher. From a checkout it is
    # server\cs_server.py, which is on nobody's path until we say so.
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
    # ::1 before 127.0.0.1. Bound to IPv4 alone, the first connection attempt
    # sits in SynSent against a port nothing is listening on and only reaches us
    # after the client gives up and retries over IPv4 — observed as a stalled
    # startup with no request arriving for many seconds.
    #
    # Two loopback listeners rather than one dual-stack socket on ::, because ::
    # would also accept connections from the LAN. A single-player game has no
    # reason to be reachable from the network, and both of these are addressable
    # only from this machine.
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

    This matters more than it looks: starting a second client does not open a
    second game, it silently kills the first. A player who clicks Tutorial while
    TestBed is up would watch their game vanish with no message, so the launcher
    checks first and says so.

    Match the manifest's exact EXE names, never a "CosmicSupremacy" prefix: the
    launcher is itself CosmicSupremacyLauncher.exe, so a prefix test finds this
    very process and every mode button reports a game already running.
    """
    wanted = {n.lower() for n in exe_names}
    # Belt and braces for the same trap: whatever this process is called, it is
    # not a client, even if someone renames a binary into collision.
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

    Two layouts, the same pair as the game itself. Frozen, the AI is a second
    PyInstaller executable in game\\ beside the client — it has to be its own
    process rather than a thread in this one, because it drives a blocking
    turn loop and reads another process's memory, and because a crash in it
    must not take the server down with it. From a checkout there is nothing
    built, so it runs from source under the interpreter running this file.

    game\\ is searched first: the release keeps it there so the player sees only
    the launcher at the top level, and a copy left beside the launcher by an
    older build would otherwise win and quietly run stale code.
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

    --stall 0 disables the no-turn-in-N-seconds bail. That guard exists for an
    unattended harness, where a stalled turn pipeline means something broke. In
    single player the turn pipeline is the PLAYER: they advance turns when they
    feel like it, and an afternoon between two of them is a lunch break, not a
    fault. Left at its 300s default the opponent quietly exits mid-game.

    --vision known keeps the AI under the same fog of war the player is under.
    It is the honest default and the one thing here that is about fair play
    rather than plumbing.
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
        # The status to fall back to whenever no game is running. Recorded when
        # the server settles so that a game exiting restores whatever was true
        # then — "server running", "port taken", "reusing the existing server" —
        # rather than a guess.
        self._ready: "tuple[str, str]" = ("starting…", WARN)
        self.log_visible = False
        self.buttons: "list" = []
        self.game_root = self.galaxy_root = self.data_dir = None
        # Every client the manifest knows about, including modes not shown: a
        # hidden mode's client is still a running game that a second launch
        # would kill.
        self.client_exes = {m["exe"] for m in cfg["modes"] if m.get("exe")}
        # Diagnostics start before the data directory is known — where the game
        # was found, and whether it was found at all, are exactly the lines a
        # failed startup needs to leave behind — so they buffer until there is a
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
        tk.Label(self.root, text=f"v{self.cfg['version']}",
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
                # Only playable buttons go in the list fail() disables — a
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

        # ── Game controls ────────────────────────────────────────────────────
        # Hidden until a mode that needs them is running. These are the TestBed
        # dialog's three buttons: a game launched on a .dat never gets that
        # dialog, so without these the player cannot end a turn at all.
        self.ctl_frame = tk.Frame(self.root, bg=PANEL,
                                  highlightbackground=EDGE, highlightthickness=1)
        inner = tk.Frame(self.ctl_frame, bg=PANEL)
        inner.pack(fill="x", padx=12, pady=10)
        # Buttons right, status left. The status says things like "BadGuy is
        # thinking", which a fixed narrow label clipped straight behind the
        # buttons — the one part of this bar that has something to say was the
        # part you could not read.
        #
        # The width is fixed rather than expanding because the window is not
        # resizable and sizes itself to its widest child: a label that grew with
        # its text would make the whole launcher jump every time the opponent
        # started or stopped thinking. Wide enough for the longest message this
        # can produce, and no wider.
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
        self.turn_label = tk.Label(inner, text="turn —", bg=PANEL, fg=TEXT,
                                   font=("Segoe UI", 9, "bold"), width=34,
                                   anchor="w")
        self.turn_label.pack(side="left", fill="x")

        tk.Label(self.root,
                 text="Keep this window open while you play — it is the game server.",
                 bg=BG, fg=FAINT, font=("Segoe UI", 8)).pack(anchor="w",
                                                             pady=(6, 0), **pad)

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
        # The data directory depends on nothing else, so it is resolved first and
        # the log opened immediately: a startup that cannot find the game must
        # still leave behind the reason it could not.
        self.data_dir = find_data_dir()
        self._open_log(self.data_dir)
        self.say(f"{self.cfg['product']} v{self.cfg['version']}")
        self.say(f"data    {self.data_dir}")

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
                                       if is_playable(m)}))
            for line in ("game files not found; searched:", searched):
                self.say(line)
            self.fail(
                "Game files not found.\n\n"
                "This launcher needs a 'game' folder beside it containing:\n"
                f"    {needed}\n\n"
                "Looked in:\n" + searched + "\n\n"
                "If you are running the launcher out of a build folder, run the "
                "one in dist\\ instead — that is the complete release. If you "
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
                self.say(f"port {port} already serving our protocol — reusing it")
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
        # The button is disabled, so this is unreachable from the UI — but a
        # placeholder has no EXE to run and must never fall through to Popen.
        if not is_playable(mode):
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
                      "incomplete — try downloading it again.")
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

    def start_ai(self, mode):
        """Start the opponent, and say clearly if there is not one to start."""
        try:
            self.ai_child = launch_ai(mode, self.data_dir)
        except OSError as exc:
            self.ai_child = None
            self.say(f"opponent failed to start: {exc}")
        if self.ai_child is None:
            self.say(f"opponent NOT started — {AI_EXE} was not found")
            # Not a silent degradation: this mode's whole premise is that
            # something is playing against you, so an install missing the AI
            # has to say so rather than hand over an empty galaxy.
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

        Worth the thread: every question about single player — is it playing,
        what is it doing, why has it stopped — is answered by these lines, and
        without them a working AI and a dead one look identical from here.
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
        for the duration, which reads exactly like a crash — and this window is
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
                        "launcher's server is not reachable — keep this window "
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

        Returns (ready, why). A mode with no AI is always ready. So is one whose
        opponent has died — the game stays playable, it just has nobody in it,
        and that is already said in the log.
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
        """Update the turn readout, quietly — this runs once a second.

        The handle is cached rather than reopened per tick: finding the client
        means enumerating every process on the machine, and _watch_game already
        does that once a second. Twice a second, to print one number, is a poor
        trade. A stale handle simply reads None and is dropped.
        """
        import gamectl
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
                    "local server, and TestBed needs it — saving and loading "
                    "will fail from that point on.\n\nClose anyway?"):
                return
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
        """
        Keep the status line honest about whether a game is up.

        Two sources, because neither alone is enough. The child handle is exact
        — it names the mode that was clicked — but only covers games this
        launcher started. Enumerating processes also catches a client started
        from a dev tool or outside the launcher entirely, at the cost of only
        knowing the EXE, and one EXE serves both Tutorial and Demo.
        """
        if self.child is not None and self.child.poll() is not None:
            name = _short(self.running_mode) if self.running_mode else "the game"
            code = self.child.returncode
            self.child = None
            self.running_mode = None
            self.say(f"{name} exited (code {code})")
            self.stop_ai("the game exited")
            self._close_ctl_client()

        # An opponent that dies while the game is still up is the failure this
        # mode cannot afford to hide: play continues, the other empire simply
        # stops doing anything, and nothing on screen would ever say why.
        if self.ai_child is not None and self.ai_child.poll() is not None:
            code = self.ai_child.returncode
            self.ai_child = None
            self.say(f"opponent exited (code {code}) — the other empire will "
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
        """Repaint only on a real change — this runs once a second."""
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
            mb.showerror("Cosmic Supremacy — Resurgence",
                         "The launcher failed to start.\n\n" + detail)
        except Exception:
            pass
        sys.exit(1)
