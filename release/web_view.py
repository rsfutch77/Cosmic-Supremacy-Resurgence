"""
web_view.py: the launcher's window as a web page in the game's own skin
=========================================================================
The view `launcher.Launcher` draws through, by pywebview over release\\webui.
The page draws what this file tells it (window.ui.set and friends in app.js)
and hands the player's presses back through `Bridge`, pywebview's js_api.

Threading. pywebview owns the main thread for its window and runs each js_api
call on a thread of its own. The Launcher expects every call and timer on one
thread, and a dialog that keeps that thread's timers running while it waits.
`WebView` gives it that: one controller thread runs every Launcher call, every
timer, and every press posted from the page, one at a time; and a prompt waits
by running that same loop until it is answered, so the queue drain and the
once-a-second watch carry on underneath a dialog.
"""
from __future__ import annotations

import ctypes
import heapq
import itertools
import json
import os
import queue
import sys
import threading
import time
import traceback

import launcher as L

PAGE_W, PAGE_H = 720, 540

# The launcher names what a piece of text means with a colour (launcher.OK and
# the rest). The page draws the table on the game's slate and the status line
# on the light blue of its lower pipe, so each is mapped to one that reads on
# each.
ON_DARK = {L.TEXT: "#e8eef2", L.DIM: "#93a6b5", L.FAINT: "#6b7d8c",
           L.ACCENT: "#a3d8fe", L.OK: "#3ddc84", L.WARN: "#ffb020",
           L.BAD: "#ff6b5e"}
ON_LIGHT = {L.OK: "#17803d", L.WARN: "#9a5b00", L.BAD: "#b3261e"}
ON_LIGHT_TEXT = "#0a1a33"


def webui_dir() -> str:
    """The page's folder: bundled in a release, beside this file in a checkout."""
    bundled = L.bundled("webui")
    if os.path.isdir(bundled):
        return bundled
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui")


def ensure_art(webui: str) -> None:
    """Extract the game's UI art beside the page when it is not there yet.

    A release carries it, extracted by build.ps1. A checkout extracts it once
    from the client the repo tracks.
    """
    art = os.path.join(webui, "art")
    if os.path.isdir(art) and os.listdir(art):
        return
    import extract_ui_art
    exe = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "client", "CosmicSupremacy.exe")
    extract_ui_art.main(["extract_ui_art", exe, art])


class Bridge:
    """What the page can ask for. pywebview runs each on its own thread, so
    each one posts its work to the controller thread and returns at once.

    A press that arrives before the Launcher exists, while it is still booting,
    is dropped: there is nothing yet to act on it.
    """

    def __init__(self, view: "WebView"):
        self._view = view

    def _post(self, act):
        """Post `act(app)` to the controller thread."""
        view = self._view
        view.post(lambda: view.app is not None and act(view.app))

    def ready(self):
        self._view._page_ready.set()

    def play(self, mode_id):
        def act(app):
            mode = next((m for m in app.modes if m.get("id") == mode_id), None)
            if mode is not None:
                app.on_play(mode)
        self._post(act)

    def page(self, name):
        if name == "galaxies":
            self._post(lambda app: app.show_galaxies())
        elif name in ("home", "log"):
            self._post(lambda app: app.show_page(name))

    def change_name(self):
        self._post(lambda app: app.on_change_name())

    def sort(self, key):
        self._post(lambda app: app.sort_by(key))

    def refresh(self):
        self._post(lambda app: app.refresh_games())

    def row(self, index):
        self._post(lambda app: self._view.row_pressed(index))

    def control(self, key):
        def act(app):
            action = {"load": app.on_load, "save": app.on_save,
                      "turn": app.on_next_turn}.get(key)
            if action is not None:
                action()
        self._post(act)

    def send_log(self):
        self._post(lambda app: app.offer_log())

    def log_refs(self):
        self._post(lambda app: app.show_log_refs())

    def resolve(self, pid, value, text=None):
        self._view.resolve(pid, value, text)

    def minimize(self):
        self._view.window.minimize()

    def close(self):
        self._post(lambda app: app.on_close())


class WebView:
    """The launcher's window: every method the Launcher calls on its view."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.app = None
        self.window = None
        self._calls: "queue.Queue" = queue.Queue()
        self._timers: list = []
        self._seq = itertools.count()
        self._page_ready = threading.Event()
        self._closed = False
        self._closing = False
        self._icon = None
        self._shown = False
        self._hwnd = None
        self._prompt_ids = itertools.count(1)
        self._answers: dict = {}
        self._row_actions: list = []
        # What the page shows now, so a once-a-second repaint that changes
        # nothing sends nothing.
        self._sent: dict = {}

    # ── running the window ──
    def run(self, make_app) -> None:
        """Open the window and run `make_app()` on the controller thread once
        the page is up. Returns when the window has closed."""
        import webview
        webui = webui_dir()
        ensure_art(webui)
        self.window = webview.create_window(
            self.cfg["product"], os.path.join(webui, "index.html"),
            js_api=Bridge(self), width=PAGE_W, height=PAGE_H, resizable=False,
            frameless=True, easy_drag=False, background_color="#001428")
        self.window.events.shown += self._on_shown
        self.window.events.closing += self._on_closing
        self.window.events.closed += self._on_closed
        self.window.events.restored += self._on_restored
        # Named, so a missing runtime fails here instead of quietly drawing the
        # page in the Internet Explorer engine. webview_runtime has checked
        # for it before this runs.
        webview.start(self._controller, (make_app,), gui="edgechromium",
                      http_server=True, debug="--debug" in sys.argv)

    def _controller(self, make_app) -> None:
        self._page_ready.wait()
        try:
            self.app = make_app()
        except Exception:
            self._report(traceback.format_exc())
            self.close()
            return
        self._pump()

    def post(self, fn) -> None:
        """Run `fn` on the controller thread. Safe from any thread."""
        self._calls.put(fn)

    def _pump(self, until: "threading.Event | None" = None) -> None:
        """Run posted calls and due timers until the window closes, or until
        `until` is set. This is the controller thread's event loop, and a
        prompt waiting for its answer runs it too."""
        while not self._closed and not (until is not None and until.is_set()):
            wait = 0.25
            if self._timers:
                wait = max(0.0, min(wait, self._timers[0][0] - time.monotonic()))
            try:
                fn = self._calls.get(timeout=wait)
            except queue.Empty:
                fn = None
            if fn is not None:
                self._run(fn)
            now = time.monotonic()
            while self._timers and self._timers[0][0] <= now and not self._closed:
                _due, _seq, timer = heapq.heappop(self._timers)
                self._run(timer)

    def _run(self, fn) -> None:
        try:
            fn()
        except Exception:
            # A failed callback is reported and the loop carries on.
            self._report(traceback.format_exc())

    def _report(self, detail: str) -> None:
        sys.stderr.write(detail)
        if self.app is not None:
            self.app._persist("window: " + detail.strip().replace("\n", "\n    "))

    def _js(self, fn: str, arg) -> None:
        if self.window is not None and not self._closed:
            self.window.run_js(f"window.ui.{fn}({json.dumps(arg)})")

    def _set(self, **state) -> None:
        self._js("set", state)

    def _changed(self, key, value) -> bool:
        if self._sent.get(key) == value:
            return False
        self._sent[key] = value
        return True

    # ── native window ──
    def _on_shown(self) -> None:
        self._shown = True
        self._hwnd = self.window.native.Handle.ToInt64()
        self._dress()

    def _on_restored(self) -> None:
        if self.app is not None:
            self.post(self.app.on_restored)

    def is_minimized(self) -> bool:
        """Whether the window is minimised to the taskbar, asked of Windows
        by the handle taken when it was shown, so any thread may ask."""
        if self._hwnd is None or self._closed:
            return False
        return bool(ctypes.windll.user32.IsIconic(self._hwnd))

    def _dress(self) -> None:
        """Exact size, the taskbar icon, and taskbar minimize for a window with
        no title bar."""
        user32 = ctypes.windll.user32
        hwnd = self.window.native.Handle.ToInt64()
        # pywebview takes a normal window's title bar and borders off the size
        # it is given, and a frameless window has neither, so the page would
        # come up short by both. With no frame the window is all page: size it
        # to the page, in this monitor's pixels.
        scale = user32.GetDpiForWindow(hwnd) / 96
        swp_nomove, swp_nozorder = 0x2, 0x4
        user32.SetWindowPos(hwnd, None, 0, 0, round(PAGE_W * scale),
                            round(PAGE_H * scale), swp_nomove | swp_nozorder)
        if self._icon:
            lr_loadfromfile, image_icon, wm_seticon = 0x10, 1, 0x80
            for which, size in ((1, 32), (0, 16)):
                h = user32.LoadImageW(None, self._icon, image_icon, size, size,
                                      lr_loadfromfile)
                if h:
                    user32.SendMessageW(hwnd, wm_seticon, which, h)
        # A borderless form has no minimize box, so a click on its taskbar
        # button cannot minimize it. Giving it the style back restores that.
        gwl_style, ws_minimizebox = -16, 0x00020000
        user32.SetWindowLongW.restype = ctypes.c_long
        style = user32.GetWindowLongW(hwnd, gwl_style)
        user32.SetWindowLongW(hwnd, gwl_style, style | ws_minimizebox)

    def _on_closing(self):
        """Alt+F4 and the taskbar's Close ask the Launcher, as the X does."""
        if self._closing or self.app is None:
            return True
        self.post(self.app.on_close)
        return False

    def _on_closed(self) -> None:
        self._closed = True
        self.post(lambda: None)

    # ── lifecycle ──
    def build(self, app) -> None:
        self.app = app
        self._set(
            product=app.cfg["product"],
            modes=[{"id": m.get("id"), "title": m["title"], "blurb": m["blurb"],
                    "ready": L.is_playable(m)} for m in app.modes],
            columns=[{"key": c.key, "heading": c.heading}
                     for c in L.GALAXY_COLUMNS],
            controls=[{"key": k, "label": label} for k, label in L.CONTROLS],
            page="home")
        self.show_sort(app.sort_key, app.sort_desc)

    def after(self, ms: int, fn) -> None:
        heapq.heappush(self._timers,
                       (time.monotonic() + ms / 1000.0, next(self._seq), fn))

    def close(self) -> None:
        self._closing = True
        self._closed = True
        if self.window is not None:
            self.window.destroy()

    def set_icon(self, path: str) -> None:
        self._icon = path
        if self._shown:
            self._dress()

    # ── who you are, and the build ──
    def show_identity(self, player) -> None:
        self._set(name=player or "")

    def show_build(self, text: str) -> None:
        if self._changed("build", text):
            self._set(build=text)

    # ── status line ──
    def set_status(self, text: str, colour: str) -> None:
        self._set(status={"text": text,
                          "colour": ON_LIGHT.get(colour, ON_LIGHT_TEXT)})

    def show_capture(self, text: str, colour: str) -> None:
        value = {"text": text, "colour": ON_LIGHT.get(colour, ON_LIGHT_TEXT)}
        if self._changed("capture", value):
            self._set(capture=value)

    def disable_modes(self) -> None:
        self._set(modesDisabled=True)

    # ── game controls ──
    def show_controls(self, show: bool) -> None:
        if self._changed("controls_shown", bool(show)):
            self._set(controlsShown=bool(show))

    def set_control(self, key: str, enabled=None, text=None) -> None:
        now = dict(self._sent.get(("control", key)) or {})
        if enabled is not None:
            now["enabled"] = bool(enabled)
        if text is not None:
            now["text"] = text
        if self._changed(("control", key), now):
            self._set(control=dict(now, key=key))

    def set_turn_label(self, text: str) -> None:
        if self._changed("turn_label", text):
            self._set(turnLabel=text)

    # ── pages and the table ──
    def show_page(self, name: str) -> None:
        self._set(page=name)

    def show_sort(self, key: str, desc: bool) -> None:
        self._set(sort={"key": key, "desc": bool(desc)})

    def set_games_status(self, text: str, colour: str) -> None:
        self._set(gamesStatus={"text": text,
                               "colour": ON_DARK.get(colour, colour)})

    def draw_galaxies(self, rows, note: str = "") -> None:
        self._sent = {k: v for k, v in self._sent.items()
                      if not (isinstance(k, tuple) and k[0] == "cell")}
        if rows is None:
            self._row_actions = []
            self._set(galaxies={"rows": None, "note": note})
            return
        self._row_actions = [row.on_action for row in rows]
        drawn = []
        for r, row in enumerate(rows):
            for i, cell in enumerate(row.cells):
                self._sent[("cell", r, i)] = tuple(cell)
            drawn.append({"cells": [[t, ON_DARK.get(c, c)] for t, c in row.cells],
                          "action": row.action})
        self._set(galaxies={"rows": drawn, "note": ""})

    def row_pressed(self, index: int) -> None:
        if 0 <= index < len(self._row_actions) and self._row_actions[index]:
            self._row_actions[index]()

    def set_cell(self, row: int, column: int, text: str, colour: str) -> None:
        key = ("cell", row, column)
        if key in self._sent and self._changed(key, (text, colour)):
            self._set(cell={"row": row, "column": column, "text": text,
                            "colour": ON_DARK.get(colour, colour)})

    def set_hint(self, text: str) -> None:
        if self._changed("hint", text or ""):
            self._set(hint=text or "")

    # ── the log ──
    def append_log(self, msg: str) -> None:
        self._js("log", [msg])

    def show_log_refs_link(self) -> None:
        self._set(logRefs=True)

    # ── prompts ──
    def _prompt(self, spec: dict):
        """Show a dialog and run the controller loop until it is answered.

        Returns (value, text): the pressed button's value, and the input's text
        for a prompt that has one. A window closed underneath answers the
        prompt's cancel value.
        """
        pid = next(self._prompt_ids)
        done = threading.Event()
        self._answers[pid] = [done, (spec.get("cancel"), None)]
        self._js("prompt", dict(spec, id=pid))
        self._pump(until=done)
        return self._answers.pop(pid)[1]

    def resolve(self, pid, value, text) -> None:
        """The page's answer to a prompt. Called on a js_api thread."""
        slot = self._answers.get(pid)
        if slot is not None:
            slot[1] = (value, text)
            slot[0].set()
            self.post(lambda: None)

    def _buttons(self, *pairs):
        return [{"label": label, "value": value} for label, value in pairs]

    def warn(self, msg: str) -> None:
        self._prompt({"title": self.cfg["product"], "text": msg,
                      "buttons": self._buttons(("OK", True)),
                      "cancel": True, "default": True})

    def error(self, msg: str) -> None:
        self.warn(msg)

    def ask_yes_no(self, msg: str) -> bool:
        value, _ = self._prompt({"title": self.cfg["product"], "text": msg,
                                 "buttons": self._buttons(("No", False),
                                                          ("Yes", True)),
                                 "cancel": False, "default": True})
        return bool(value)

    def ask_ok_cancel(self, title: str, msg: str) -> bool:
        value, _ = self._prompt({"title": title, "text": msg,
                                 "buttons": self._buttons(("Cancel", False),
                                                          ("OK", True)),
                                 "cancel": False, "default": True})
        return bool(value)

    def ask_open_file(self, title: str, initialdir: str, filetypes) -> str:
        import webview
        types = tuple(f"{label} ({pattern})" for label, pattern in filetypes)
        picked = self.window.create_file_dialog(
            webview.OPEN_DIALOG, directory=initialdir, file_types=types)
        return picked[0] if picked else ""

    def ask_name(self, text: str, current: str, rules: str, validate):
        """Ask until `validate` takes a name or the player cancels. A refusal
        comes back in the same dialog, above the rules."""
        problem = ""
        while True:
            value, raw = self._prompt({
                "title": "Your name", "text": text, "note": rules,
                "error": problem,
                "input": {"label": "Name", "value": current,
                          "maxlength": L.NAME_LIMIT},
                "buttons": self._buttons(("Cancel", None), ("Save", "save")),
                "cancel": None, "default": "save"})
            if value != "save":
                return None
            name, problem = validate(raw or "")
            if name:
                return name
            current = raw or ""

    def choose(self, title: str, body: str, choices):
        value, _ = self._prompt({
            "title": title, "text": body,
            "buttons": [{"label": label, "value": value, "primary": primary}
                        for label, value, primary in choices],
            "cancel": None})
        return value

    def beta_notice(self, heading: str, lead: str, body: str) -> bool:
        value, _ = self._prompt({
            "kind": "notice", "title": heading, "text": lead, "body": body,
            "buttons": self._buttons(("Not yet", False), ("Join", True)),
            "cancel": False})
        return bool(value)

    def log_refs(self, title: str, body: str, refs) -> None:
        self._prompt({"title": title, "text": body,
                      "refs": [{"id": ref, "when": when} for ref, when in refs],
                      "copy": L.LOG_COPY, "copied": L.LOG_COPIED,
                      "buttons": self._buttons(("Close", None)),
                      "cancel": None, "default": None})
