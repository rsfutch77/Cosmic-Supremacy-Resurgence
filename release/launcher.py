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


def find_game_root(modes) -> "str | None":
    """First directory that holds every EXE the manifest asks for."""
    wanted = {m["exe"] for m in modes}
    for cand in (os.path.join(app_dir(), "game"),
                 app_dir(),
                 os.path.join(_repo_root(), "client")):
        if all(os.path.exists(os.path.join(cand, exe)) for exe in wanted):
            return cand
    return None


def find_galaxy_root(game_root: str, modes) -> "str | None":
    wanted = {m["galaxy"] for m in modes}
    for cand in (os.path.join(game_root, "galaxies"), game_root):
        if all(os.path.exists(os.path.join(cand, g)) for g in wanted):
            return cand
    return None


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
    Import cs_server and serve it on a daemon thread. Returns (httpd, note).

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

    httpd = http.server.HTTPServer((host, port), cs_server.CSHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="cs_server").start()
    return httpd, cs_server.LOGFILE


# ── Client process ────────────────────────────────────────────────────────────
def running_clients() -> "list[str]":
    """
    Names of any Cosmic Supremacy client already running.

    This matters more than it looks: starting a second client does not open a
    second game, it silently kills the first. A player who clicks Tutorial while
    TestBed is up would watch their game vanish with no message, so the launcher
    checks first and says so.
    """
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in out.splitlines():
        if not line.startswith('"'):
            continue
        name = line.split('","')[0].lstrip('"')
        if name.lower().startswith("cosmicsupremacy"):
            names.append(name)
    return sorted(set(names))


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
        self.httpd = None
        self.child: "subprocess.Popen | None" = None
        self.log_visible = False
        self.buttons: "list" = []
        self.game_root = self.galaxy_root = self.data_dir = None
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

    # ── construction ──
    def _build(self):
        tk = self.tk
        pad = {"padx": 26}

        tk.Label(self.root, text="COSMIC SUPREMACY", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 22, "bold")).pack(anchor="w", pady=(22, 0), **pad)
        tk.Label(self.root, text=f"Resurgence  ·  v{self.cfg['version']}",
                 bg=BG, fg=DIM, font=("Segoe UI", 10)).pack(anchor="w", **pad)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="x", pady=(18, 4), **pad)

        for mode in self.modes:
            card = tk.Frame(body, bg=PANEL, highlightbackground=EDGE,
                            highlightthickness=1)
            card.pack(fill="x", pady=5)
            btn = tk.Button(card, text=mode["title"], width=18,
                            bg=BTN, fg="#ffffff", activebackground=BTN_HI,
                            activeforeground="#ffffff", relief="flat", bd=0,
                            font=("Segoe UI", 10, "bold"), cursor="hand2",
                            command=lambda m=mode: self.on_play(m))
            btn.pack(side="left", padx=14, pady=14)
            btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=BTN_HI))
            btn.bind("<Leave>", lambda e, b=btn: b.configure(bg=BTN))
            self.buttons.append(btn)
            tk.Label(card, text=mode["blurb"], bg=PANEL, fg=DIM, justify="left",
                     wraplength=340, font=("Segoe UI", 9)).pack(
                         side="left", padx=(0, 14))

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

        # Only the modes actually offered gate startup. A hidden mode whose EXE
        # the build no longer ships is a stale manifest row, not a broken install.
        game_root = find_game_root(self.modes)
        if not game_root:
            self.fail("Game files not found.\n\nExpected a 'game' folder next to "
                      "this launcher.\n\nIf you unzipped only the launcher, "
                      "download the full release archive again and keep the "
                      "folder together.")
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
                self.set_status(f"using the server already on {host}:{port}", OK)
                self.say(f"port {port} already serving our protocol — reusing it")
                return
            self.set_status(f"port {port} is taken by something else", BAD)
            self.say(f"port {port} is in use and did not answer testconnection")
            self.warn(
                f"Port {port} is already in use by another program.\n\n"
                "The game can only talk to that exact port, so TestBed will not "
                "work until it is free. Tutorial and Demo are unaffected.\n\n"
                "Close any other copy of this launcher, or any cs_server.py you "
                "started yourself, and restart.")
            return

        try:
            self.httpd, logfile = start_server(
                host, port, self.data_dir, self.galaxy_root, self.say_threadsafe)
        except Exception as exc:
            self.set_status(f"server failed to start: {exc}", BAD)
            self.say("server failed: " + "".join(
                traceback.format_exception_only(type(exc), exc)).strip())
            return
        self.set_status(f"server running on {host}:{port}", OK)
        self.say(f"server listening on {host}:{port}")
        self.say(f"log     {logfile}")

    # ── actions ──
    def on_play(self, mode):
        busy = running_clients()
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
        self.say(f"launched {mode['id']}: {mode['exe']} {mode['galaxy']}")
        self.set_status(f"{mode['title']} is running", OK)

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
        if running_clients():
            if not messagebox.askokcancel(
                    "Quit launcher?",
                    "A game is still running.\n\nClosing the launcher stops the "
                    "local server, and TestBed needs it — saving and loading "
                    "will fail from that point on.\n\nClose anyway?"):
                return
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
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

    def _drain(self):
        try:
            while True:
                self.say(self.msgs.get_nowait())
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    def set_status(self, text: str, colour: str):
        self.status.configure(text=text)
        self.dot.configure(fg=colour)

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
