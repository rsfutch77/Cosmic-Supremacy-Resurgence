"""
webview_spike.py , the launcher's window drawn in the game's own skin, nothing behind it
========================================================================================
    uv run --no-project --with pywebview python release\\webview_spike.py

A frameless pywebview window over release\\webui. It proves the parts of the port
that tkinter made hard or that pywebview could get wrong: the game chrome with no
Windows title bar, dragging by the title, minimize and close from the game's own
buttons, the taskbar icon, a prompt a worker thread blocks on until the player
answers, and state pushed from a worker thread.

It starts no server and no game, and writes nothing to the data directory. The
name it asks for is held in memory and forgotten on exit.
"""
from __future__ import annotations

import ctypes
import itertools
import json
import os
import sys
import threading
import time

import webview

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import extract_icon  # noqa: E402
import extract_ui_art  # noqa: E402
import launcher  # noqa: E402
import webview_runtime  # noqa: E402

WEBUI = os.path.join(HERE, "webui")
ART = os.path.join(WEBUI, "art")
CLIENT_EXE = os.path.join(os.path.dirname(HERE), "client", "CosmicSupremacy.exe")

OK, WARN = "#17803d", "#c47a00"


class View:
    """What the window shows, pushed from any thread, and prompts that wait."""

    def __init__(self):
        self.window = None
        self._ids = itertools.count(1)
        self._waiting = {}  # prompt id -> [Event, answer]

    def _js(self, fn: str, arg) -> None:
        if self.window:
            self.window.evaluate_js(f"window.ui.{fn}({json.dumps(arg)})")

    def set(self, **state) -> None:
        self._js("set", state)

    def log(self, *lines: str) -> None:
        self._js("log", list(lines))

    def prompt(self, title: str, text: str, buttons, *, cancel=None, default=None,
               input=None, note: str = "", error: str = ""):
        """Show a dialog and block the calling thread until it is answered.

        Returns (button value, input text). Never call this on the GUI thread:
        the answer arrives on a js_api thread, which the GUI thread would be
        stuck waiting for.
        """
        pid = next(self._ids)
        done = threading.Event()
        self._waiting[pid] = [done, None]
        self._js("prompt", {"id": pid, "title": title, "text": text, "note": note,
                            "error": error,
                            "buttons": [{"label": b, "value": v} for b, v in buttons],
                            "cancel": cancel, "default": default, "input": input})
        done.wait()
        return self._waiting.pop(pid)[1]

    def resolve(self, pid, value, text) -> None:
        slot = self._waiting.get(pid)
        if slot:
            slot[1] = (value, text)
            slot[0].set()


VIEW = View()


class Api:
    """The page's calls into Python. pywebview runs each on its own thread."""

    def __init__(self, cfg):
        self._cfg = cfg
        self._name = None

    def ready(self):
        modes = [m for m in self._cfg["modes"] if m.get("show", True)]
        VIEW.set(product=self._cfg["product"], build=f"v{launcher.build_id()}",
                 name=self._name,
                 status={"text": "spike: no server", "colour": WARN},
                 modes=[{"id": m["id"], "title": m["title"], "blurb": m["blurb"],
                         "ready": True} for m in modes])
        VIEW.log(f"{self._cfg['product']} v{launcher.build_id()}",
                 "spike   pywebview window, nothing behind it")
        threading.Thread(target=self._ticker, daemon=True, name="ticker").start()

    def _ticker(self):
        # State pushed from a worker thread once a second, as the turn loop will.
        start = time.monotonic()
        while VIEW.window:
            time.sleep(1)
            VIEW.set(status={"text": f"spike: up {int(time.monotonic() - start)}s",
                             "colour": OK})

    def play(self, mode_id):
        VIEW.log(f"play    {mode_id} pressed")
        value, _ = VIEW.prompt("Not in the spike",
                               f"\"{mode_id}\" would start here. The spike starts nothing.",
                               [("OK", True)], cancel=True, default=True)
        VIEW.log(f"play    {mode_id} acknowledged")

    def change_name(self):
        current, problem = self._name or "", ""
        while True:
            # A refused name comes back in the same dialog with the reason above
            # the rules, rather than as a second dialog to dismiss.
            value, text = VIEW.prompt(
                "Your name",
                "This is the name other players see, and the name of your "
                "civilisation in a galaxy. You enter it once.",
                [("Cancel", None), ("Save", "save")], cancel=None, default="save",
                input={"label": "Name", "value": current,
                       "maxlength": launcher.NAME_LIMIT},
                note=launcher.NAME_RULES, error=problem)
            if value != "save":
                return
            name, problem = launcher.validate_player_name(text)
            if name:
                self._name = name
                VIEW.set(name=name)
                VIEW.log(f"player  {name} (spike: not saved)")
                return
            current = text

    def resolve(self, pid, value, text):
        VIEW.resolve(pid, value, text)

    def minimize(self):
        VIEW.window.minimize()

    def close(self):
        VIEW.window.destroy()


def prepare_art():
    if not os.path.isdir(ART) or not os.listdir(ART):
        extract_ui_art.main(["extract_ui_art", CLIENT_EXE, ART])
    ico = os.path.join(ART, "cosmic.ico")
    if not os.path.exists(ico):
        extract_icon.main(["extract_icon", CLIENT_EXE, ico])
    return ico


PAGE_W, PAGE_H = 720, 540


def dress_native_window(window, ico: str):
    """Exact size, the taskbar icon, and taskbar minimize for a window with no title bar."""
    user32 = ctypes.windll.user32
    hwnd = window.native.Handle.ToInt64()
    # pywebview takes a normal window's title bar and borders off the size it
    # is given, and a frameless window has neither, so the page came up short
    # by both. With no frame the window is all page: size it to the page, in
    # this monitor's pixels.
    scale = user32.GetDpiForWindow(hwnd) / 96
    SWP_NOMOVE, SWP_NOZORDER = 0x2, 0x4
    user32.SetWindowPos(hwnd, None, 0, 0, round(PAGE_W * scale), round(PAGE_H * scale),
                        SWP_NOMOVE | SWP_NOZORDER)
    LR_LOADFROMFILE, LR_DEFAULTSIZE, IMAGE_ICON, WM_SETICON = 0x10, 0x40, 1, 0x80
    for which, size in ((1, 32), (0, 16)):
        h = user32.LoadImageW(None, ico, IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if h:
            user32.SendMessageW(hwnd, WM_SETICON, which, h)
    # A borderless form has no minimize box, so a click on its taskbar button
    # cannot minimize it. Giving it the style back restores that.
    GWL_STYLE, WS_MINIMIZEBOX = -16, 0x00020000
    user32.SetWindowLongW.restype = ctypes.c_long
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_MINIMIZEBOX)


def bootstrapper() -> "str | None":
    """The bundled WebView2 installer, or the one build.ps1 cached in a checkout."""
    for path in (launcher.bundled(webview_runtime.BOOTSTRAPPER),
                 os.path.join(HERE, "build", webview_runtime.BOOTSTRAPPER)):
        if os.path.exists(path):
            return path
    return None


def main():
    with open(launcher.bundled("manifest.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    if not webview_runtime.ensure(cfg["product"], bootstrapper()):
        return
    print(f"webview2 runtime {webview_runtime.installed_version()}")
    ico = prepare_art()
    launcher.set_app_user_model_id()
    window = webview.create_window(
        cfg["product"], os.path.join(WEBUI, "index.html"), js_api=Api(cfg),
        width=PAGE_W, height=PAGE_H, resizable=False, frameless=True, easy_drag=False,
        background_color="#001428")
    VIEW.window = window
    window.events.shown += lambda: dress_native_window(window, ico)
    window.events.closed += lambda: setattr(VIEW, "window", None)
    # Named, so a missing runtime fails here instead of quietly drawing the
    # page in the Internet Explorer engine.
    webview.start(gui="edgechromium", http_server=True, debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
