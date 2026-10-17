"""
A stand-in for the launcher's window, for tests that drive `launcher.Launcher`
without one.

`FakeView` records what the Launcher last told it to show, in plain attributes
a test can read, and answers the Launcher's prompts from scripted answers.
Every method of web_view.WebView that the Launcher calls is here, with the
same arguments.

`PageWindow` stands in for the pywebview window under a real WebView instead,
for a test that wants the real view: it records the script the page is sent.
"""
import json
import re


class PageWindow:
    """Records what the page is sent; destroy() is the window closing."""

    def __init__(self, view):
        self.view = view
        self.sent = []
        self.destroyed = False

    def run_js(self, script):
        m = re.fullmatch(r"window\.ui\.(\w+)\((.*)\)", script, re.S)
        self.sent.append((m.group(1), json.loads(m.group(2))))

    def destroy(self):
        self.destroyed = True
        self.view._on_closed()

    def prompts(self):
        return [arg for fn, arg in self.sent if fn == "prompt"]

    def last(self, key):
        """The last value the page was sent for `key` in a window.ui.set."""
        for fn, arg in reversed(self.sent):
            if fn == "set" and key in arg:
                return arg[key]
        return None


class FakeView:
    def __init__(self, **answers):
        # Scripted prompt answers, by prompt name: "yes_no", "ok_cancel",
        # "name", "choose", "beta_notice", "open_file". A list is answered
        # from the front, one item per prompt.
        self.answers = answers
        self.app = None
        self.status = ("", "")
        self.capture = ("", "")
        self.identity = None
        self.build = ""
        self.page = "home"
        self.sort = None
        self.games_status = ("", "")
        self.rows = None
        self.note = ""
        self.cells = {}
        self.hint = ""
        self.controls_shown = False
        self.controls = {}
        self.turn_label = ""
        self.log = []
        self.log_refs_link = False
        self.modes_disabled = False
        self.closed = False
        self.icon = None
        self.timers = []
        # Whether the window reads as minimised to the taskbar.
        self.minimized = False
        # Every prompt shown, as (kind, args), in order.
        self.prompts = []

    def _answer(self, kind, default):
        got = self.answers.get(kind, default)
        if isinstance(got, list):
            return got.pop(0) if got else default
        return got

    # lifecycle
    def build(self, app):
        self.app = app

    def after(self, ms, fn):
        self.timers.append((ms, fn))

    def close(self):
        self.closed = True

    def set_icon(self, path):
        self.icon = path

    def is_minimized(self):
        return self.minimized

    # identity and build
    def show_identity(self, player):
        self.identity = player

    def show_build(self, text):
        self.build = text

    # status
    def set_status(self, text, colour):
        self.status = (text, colour)

    def show_capture(self, text, colour):
        self.capture = (text, colour)

    def disable_modes(self):
        self.modes_disabled = True

    # controls
    def show_controls(self, show):
        self.controls_shown = bool(show)

    def set_control(self, key, enabled=None, text=None):
        c = self.controls.setdefault(key, {"enabled": True, "text": key})
        if enabled is not None:
            c["enabled"] = bool(enabled)
        if text is not None:
            c["text"] = text

    def set_turn_label(self, text):
        self.turn_label = text

    # pages and the table
    def show_page(self, name):
        self.page = name

    def show_sort(self, key, desc):
        self.sort = (key, desc)

    def set_games_status(self, text, colour):
        self.games_status = (text, colour)

    def draw_galaxies(self, rows, note=""):
        self.rows = None if rows is None else list(rows)
        self.note = note
        self.cells = {}
        for r, row in enumerate(self.rows or []):
            for i, cell in enumerate(row.cells):
                self.cells[r, i] = cell

    def set_cell(self, row, column, text, colour):
        if (row, column) in self.cells:
            self.cells[row, column] = (text, colour)

    def set_hint(self, text):
        self.hint = text or ""

    # the log
    def append_log(self, msg):
        self.log.append(msg)

    def show_log_refs_link(self):
        self.log_refs_link = True

    # prompts
    def warn(self, msg):
        self.prompts.append(("warn", (msg,)))

    def error(self, msg):
        self.prompts.append(("error", (msg,)))

    def ask_yes_no(self, msg):
        self.prompts.append(("yes_no", (msg,)))
        return bool(self._answer("yes_no", False))

    def ask_ok_cancel(self, title, msg):
        self.prompts.append(("ok_cancel", (title, msg)))
        return bool(self._answer("ok_cancel", False))

    def ask_open_file(self, title, initialdir, filetypes):
        self.prompts.append(("open_file", (title, initialdir, filetypes)))
        return self._answer("open_file", "")

    def ask_name(self, text, current, rules, validate):
        self.prompts.append(("name", (text, current, rules)))
        raw = self._answer("name", None)
        if raw is None:
            return None
        name, _problem = validate(raw)
        return name

    def choose(self, title, body, choices):
        self.prompts.append(("choose", (title, body, choices)))
        return self._answer("choose", None)

    def beta_notice(self, heading, lead, body):
        self.prompts.append(("beta_notice", (heading, lead, body)))
        return bool(self._answer("beta_notice", False))

    def log_refs(self, title, body, refs):
        self.prompts.append(("log_refs", (title, body, list(refs))))

    # what a test usually wants to read back
    def warnings(self):
        return [args[0] for kind, args in self.prompts if kind == "warn"]
