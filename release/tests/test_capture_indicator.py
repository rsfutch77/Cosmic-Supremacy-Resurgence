"""
test_capture_indicator.py: what the launcher shows about a turn in flight
    server\\.venv\\Scripts\\python.exe release\\tests\\test_capture_indicator.py

Covers the capture indicator (capturing, captured but not sent, sent), the
Save button, the turn readout, and the refusal of a foreign server on 8888.

Headless: the view is a FakeView and the Launcher is built without __init__.
The indicator's visibility is checked against web_view.WebView with a
recording stand-in for the page.
"""
import os
import queue
import sys
import threading
import time

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import launcher as L                                            # noqa: E402
import web_view as WV                                           # noqa: E402
from fake_view import FakeView, PageWindow                      # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


class Store:
    """A galaxy clock the readout can ask about, counting what it is asked.

    Each call is a Firestore document read on a live galaxy.
    """

    def __init__(self, turn=14, left=930.0):
        self.turn, self.left = turn, left
        self.reads = 0

    def current(self):
        self.reads += 1
        return self.turn, time.time() + self.left

    def seconds_left(self):
        self.reads += 1
        return self.left


class Thread:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


def bare(**over):
    """A Launcher with no window behind it: the real methods against a FakeView."""
    app = object.__new__(L.Launcher)
    app.mp_store = None
    app.mp_note = ""
    app.mp_turn = None
    app.mp_deadline = None
    # Together these decide whether a passed deadline is a late referee or a
    # stopped one. Four hours, so a deadline a few seconds old is ordinary.
    app.mp_turn_seconds = 14400
    app.mp_waiting = False
    app.mp_civ = "DemoPlayer"
    app.mp_capture = None
    app.mp_playing = None
    app.mp_thread = None
    app.mp_client_seen = False
    app.mp_client_gone = False
    # Whether a turn is still on its way, which decides if closing the window
    # announces a send.
    app.mp_turn_open = True
    app.mp_sending = False
    app.mp_send_now = threading.Event()
    app.mp_reopen = threading.Event()
    app.mp_reopening = False
    app.mp_stop = False
    app.data_dir = os.path.join(REPO, "release", "data")
    app.view = FakeView()
    app._ctl_client = None
    app._ctl_busy = False
    app._last_seen_turn = None
    app._waiting_since = None
    app.running_mode = None
    app.said = []
    app.say = app.said.append
    app.msgs = queue.Queue()
    for k, v in over.items():
        setattr(app, k, v)
    return app


print("\n1. the three states the indicator has to tell apart")
seen = {}
for state in (L.CAPTURING, L.HELD, L.SENT):
    seen[state] = L.capture_readout(state, 8.0)
check("capturing now says so", seen[L.CAPTURING][0],
      lambda t: "saving" in t.lower())
check("captured says saved", seen[L.HELD][0],
      lambda t: "saved" in t.lower())
check("sent says sent", seen[L.SENT][0], lambda t: "sent" in t.lower())
check("all three read differently",
      len({t for t, _c in seen.values()}), 3)
# While the game is open, held work is on disk and the upload is not due yet,
# so it is not a warning.
check("a capture held while the game is open is good news",
      seen[L.HELD][1], L.OK)
check("sent is the colour the server dot uses when it is up",
      seen[L.SENT][1], L.OK)

print("\n1b. and what those states mean once the game has closed")
# Once the game is gone, held work is waiting to be sent.
closed = {s: L.capture_readout(s, 8.0, client_open=False)
          for s in (L.CAPTURING, L.HELD, L.SENT)}
check("a capture still unsent after the game closed is amber",
      closed[L.HELD][1], L.WARN)
check("and says it is sending rather than that it is saved",
      closed[L.HELD][0], lambda t: "sending" in t.lower())
check("a capture mid-flight reads the same, because it is the same wait",
      closed[L.CAPTURING][0], closed[L.HELD][0])
check("and sent is still the answer the player is waiting for",
      closed[L.SENT][1], L.OK)
check("which is the one thing that differs from the open case",
      closed[L.SENT][0], seen[L.SENT][0])
check("a failed capture is the bad colour",
      L.capture_readout(L.CAPTURE_FAILED, 1.0)[1], L.BAD)
check("nothing captured yet shows nothing at all",
      L.capture_readout(None, 0.0)[0], "")
check("the two states worth ageing carry the age",
      (L.capture_readout(L.HELD, 75.0)[0],
       L.capture_readout(L.SENT, 75.0)[0]),
      lambda pair: all("1m ago" in t for t in pair))
check("and a fresh one does not count seconds at the player",
      L.capture_readout(L.SENT, 1.0)[0], lambda t: "just now" in t)

def shown(app):
    """Whether the indicator is up. The view hides it for empty text."""
    return app.view.capture[0] != ""


print("\n2. the indicator is packed only while there is something to say")
app = bare()
app._show_capture(None, 0.0)
check("nothing to say leaves the status row as it was", shown(app), False)
app._show_capture(L.CAPTURING, 0.0)
check("a capture starting puts the dot up", shown(app), True)
check("with its own colour", app.view.capture[1], L.WARN)
app._show_capture(L.SENT, 2.0)
check("and sending repaints it green", app.view.capture[1], L.OK)
app._show_capture(None, 0.0)
check("and it goes again when the loop stops", shown(app), False)

# The page hides the dot and its status for empty text (app.js), and a state
# reaches the page in the colour that reads on the status pipe.
wv = WV.WebView({"product": "CS"})
wv.window = PageWindow(wv)
wv.show_capture(*L.capture_readout(L.CAPTURING, 0.0))
check("a state reaches the page with its text",
      wv.window.last("capture")["text"], lambda t: bool(t))
check("in the state's colour for the status pipe",
      wv.window.last("capture")["colour"], WV.ON_LIGHT[L.WARN])
wv.show_capture("", "")
check("and empty text is what hides it", wv.window.last("capture")["text"], "")

print("\n3. the states the turn loop emits reach the indicator")
app = bare()
app._mp_state("serving", turn=14, civ="DemoPlayer")
check("a new turn starts with nothing captured", app.mp_capture, None)
app._mp_state("capturing", turn=14, civ="DemoPlayer")
check("a capture in flight is recorded", app.mp_capture[0], L.CAPTURING)
app._mp_state("captured", turn=14, civ="DemoPlayer", pending=True)
check("a capture the store has not got is held", app.mp_capture[0], L.HELD)
app._mp_state("captured", turn=14, civ="DemoPlayer", pending=False)
check("a capture the store already holds counts as sent",
      app.mp_capture[0], L.SENT)
app._mp_state("captured", turn=14, civ="DemoPlayer", pending=True)
app._mp_state("submitted", turn=14, civ="DemoPlayer", final=False)
check("and an upload is sent", app.mp_capture[0], L.SENT)
check("the capture states leave the turn readout's note alone",
      app.mp_note, "orders sent")
app._mp_state("capture_failed", turn=14, civ="DemoPlayer", error="no client")
check("a capture that failed says so", app.mp_capture[0], L.CAPTURE_FAILED)
app.mp_sending = True
app._mp_state("waiting", turn=14, civ="DemoPlayer")
check("a turn that has ended is no longer being sent", app.mp_sending, False)
check("a new turn clears the last turn's sent",
      (app._mp_state("serving", turn=15, civ="DemoPlayer"), app.mp_capture)[1],
      None)

print("\n4. Save means two things and says which")
app = bare(mp_thread=Thread(alive=True), mp_playing={"name": "sandbox"})
app.on_save()
check("Save during a turn asks the loop to send", app.mp_send_now.is_set(),
      True)
check("and says so", app.said, lambda lines: any("sending" in l for l in lines))
check("it does not touch the client itself",
      any("saving" in l for l in app.said), False)

app = bare(mp_thread=None, view=FakeView(name="Before war"),
           _save_name="singleplayer")
called = []
app._in_background = lambda label, work, done=None: called.append(label)
app.on_save()
check("outside multiplayer it still writes a .dat", called, ["saving"])
check("and asks no turn loop for anything", app.mp_send_now.is_set(), False)
check("it asks for a name, offering the last one",
      app.view.prompts[0][1][1], "singleplayer")
check("and offers the new name next time", app._save_name, "Before war")

app = bare(mp_thread=None, view=FakeView(name=None),
           _save_name="singleplayer")
called = []
app._in_background = lambda label, work, done=None: called.append(label)
app.on_save()
check("cancelling the name saves nothing", called, [])

check("a save name refuses what the server would drop",
      L.validate_save_name("war#1")[0], None)
check("and what Windows refuses in a file name",
      L.validate_save_name("a/b")[0], None)
check("and takes an ordinary name, trimmed",
      L.validate_save_name("  Before war ")[0], "Before war")

import inspect                                                  # noqa: E402
src = inspect.getsource(L.Launcher.on_save)
check("the single-player half is unreachable while a loop is running",
      src.index("playing_now()") < src.index("_save_to_dat"), True)

print("\n5. the button names what it will do")
app = bare(mp_store=Store(), mp_thread=Thread(alive=True))
app._refresh_turn()
check("in multiplayer the button says it sends the turn",
      app.view.controls["save"]["text"], "Send Turn")
check("and Next Turn is not offered, the referee owns the clock",
      app.view.controls["turn"]["enabled"], False)
check("while Save stays live", app.view.controls["save"]["enabled"], True)

app = bare(mp_store=None)
app._refresh_turn()
check("outside multiplayer it is Save again",
      app.view.controls["save"]["text"], "Save")

print("\n6. closing the game clears the readout at once")
app = bare(mp_store=Store(turn=14, left=930.0), mp_thread=Thread(alive=True))
app._watch_mp_client(True)
app._refresh_turn()
check("while the client is up the readout is the galaxy's clock",
      app.view.turn_label, lambda t: t.startswith("turn 14"))
app._watch_mp_client(False)
check("the client going is noticed on the tick it happens",
      app.mp_client_gone, True)
app._refresh_turn()
check("and the turn readout stops claiming a turn is open",
      app.view.turn_label, lambda t: "turn 14" not in t)
check("saying instead that the turn is still being sent",
      app.view.turn_label, lambda t: "sending" in t.lower())
check("the log says it too",
      app.said, lambda lines: any("closed" in l for l in lines))
check("and the status says the sending is still happening",
      app.mp_sending, True)
check("Save has nothing left to ask for and goes grey",
      app.view.controls["save"]["enabled"], False)
app.mp_capture = (L.HELD, time.time())
app._refresh_turn()
check("while the indicator keeps reporting the send",
      app.view.capture[0], lambda t: "sending" in t.lower())

app._mp_state("submitted", turn=14, civ="DemoPlayer", final=True)
check("the final submission ends the sending", app.mp_sending, False)
app._refresh_turn()
check("and the readout stops saying it",
      app.view.turn_label, lambda t: "sending" not in t)
check("without putting the closed turn's countdown back",
      app.view.turn_label, lambda t: "turn 14" not in t)

# The send finishing is drawn as a confirmation.
check("the confirmation is on screen after the send, not skipped",
      app.view.capture[0], lambda t: "sent" in t.lower())
check("and it is green, because the turn is safe",
      app.view.capture[1], L.OK)
check("and the indicator is still up to be read", shown(app), True)
# The confirmation is held for a while, not kept.
app.mp_capture = (L.SENT, time.time() - L.CAPTURE_HOLD - 1)
app._refresh_turn()
check("and it goes once it has been up long enough", shown(app), False)

# Play with the window closed asks the loop to open the game again, and the
# readout says so until the client is up.
app.mp_reopening = True
app._refresh_turn()
check("a game being opened again says so rather than saying it is waiting",
      app.view.turn_label, lambda t: "opening" in t.lower())
app._mp_state("playing", turn=14, civ="DemoPlayer", seconds_left=900)
check("and stops saying it once the turn is running", app.mp_reopening, False)
app.mp_reopening = True

app._watch_mp_client(True)
check("a client coming back puts the readout back",
      app.mp_client_gone, False)
check("and answers whatever asked for it", app.mp_reopening, False)
app._refresh_turn()
check("with the galaxy's clock on it",
      app.view.turn_label, lambda t: t.startswith("turn 14"))
check("and Save live again", app.view.controls["save"]["enabled"], True)

print("\n7. the countdown subtracts, it does not poll the store")
# The readout runs once a second and must not read the store. It counts down
# from the time left that the loop reports with every capture.
store = Store(turn=14, left=930.0)
app = bare(mp_store=store, mp_thread=Thread(alive=True))
app._refresh_turn()
check("the first tick asks the store, because nothing has been heard yet",
      store.reads, 1)
check("and it says the turn", app.view.turn_label,
      lambda t: t.startswith("turn 14"))
for _ in range(600):
    app._refresh_turn()
check("ten minutes of ticks after it ask the store nothing more",
      store.reads, 1)
check("while the readout goes on saying the turn",
      app.view.turn_label, lambda t: t.startswith("turn 14"))
app._mp_state("playing", turn=15, civ="DemoPlayer", seconds_left=14400.0)
app._refresh_turn()
check("a new turn reaches it from the loop rather than from a read",
      (app.view.turn_label.startswith("turn 15"), store.reads),
      (True, 1))
check("with the loop's own clock on it",
      app.view.turn_label, lambda t: t.endswith("left"))

print("\n8. a foreign server on the port is refused, not reused")
boot = inspect.getsource(L.Launcher._boot)
taken = boot[boot.index("if not port_is_free("):
             boot.index("self.servers, logfile = start_server")]
check("nothing reuses a server this launcher did not start",
      "already serving our protocol" in boot, False)
# A taken port is accepted when multiplayer.json says where the holding server
# writes, or when it is this install's own referee. Otherwise it is refused.
check("a taken port with nothing said about it is still refused",
      "not reusing it" in inspect.getsource(L.port_held_message)
      and "self.warn(box)" in taken, True)
check("sharing is the only other way out, named or detected",
      taken.count("return"), 3)
check("and it is gated on the operator having named the directory",
      "if ours and adopt and os.path.isdir(adopt)" in taken, True)
check("or on referee_share finding this PC's referee and its folder",
      "shared = referee_share(holder, ours, configured=adopt)" in taken, True)
check("the refusal really is the branch that was read",
      len(taken), lambda n: 400 < n < 4000)
# The words are in `port_held_message`, which `_boot` hands what holds the port.
said = inspect.getsource(L.port_held_message)
check("the refusal hands its words to port_held_message",
      "port_held_message(port, holder, ours" in taken, True)
check("the refusal names the port",
      "Another server already holds port" in said, True)
check("and tells the player to close it",
      "Close it and start this launcher again" in said, True)
check("the port check is still one bind",
      boot.count("port_is_free("), 1)
check("and the protocol probe only runs once the port is known to be taken",
      boot.index("port_is_free(") < boot.index("stub_server_answers("), True)
check("the ordinary case, a free port, still starts a server",
      boot.index("stub_server_answers(") < boot.index("start_server("), True)
check("a server is healthy because we started it, because we were told "
      "where it writes, or because it is this PC's referee",
      boot.count("self.server_ok = True"), 3)
mp = inspect.getsource(L.Launcher.start_multiplayer)
check("and a turn will not start without one",
      "if not self.server_ok:" in mp, True)

print("\n9. the port check itself, against a socket nothing is on")
import socket                                                   # noqa: E402
probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
probe.bind(("127.0.0.1", 0))
held = probe.getsockname()[1]
probe.listen(1)
try:
    check("a port something is listening on reads as taken",
          L.port_is_free("127.0.0.1", held), False)
    check("and it does not answer our protocol",
          L.stub_server_answers("127.0.0.1", held, timeout=0.5), False)
finally:
    probe.close()
check("a port nothing holds reads as free",
      L.port_is_free("127.0.0.1", held), True)

print()
print("the status line while the loop outlives the game window")


class _St:
    """Just enough Launcher to drive the status branch."""

    def __init__(self, sending, gone, civ="DemoPlayer"):
        self.mp_sending, self.mp_client_gone, self.mp_civ = sending, gone, civ
        self.said = []

    def _status_if_changed(self, text, colour):
        self.said.append((text, colour))


READY = ("ready", "ok")


def _status_for(sending, gone):
    """The branch as `_watch_game` runs it, with mp_live true and no client."""
    st = _St(sending, gone)
    if st.mp_sending:
        st._status_if_changed("sending your turn, keep this window open", "warn")
    elif st.mp_client_gone:
        st._status_if_changed(*READY)
    else:
        st._status_if_changed(f"Multiplayer: {st.mp_civ}", "ok")
    return st.said[0][0]


playing = _status_for(False, False)
waiting = _status_for(False, True)
sending = _status_for(True, True)
check("playing names the civ", "DemoPlayer" in playing, True)
# The loop stays alive between turns, so waiting and playing must read
# differently.
check("waiting does not read as playing", waiting == playing, False)
check("and is the launcher's own ready line", waiting, READY[0])
check("which says nothing about multiplayer",
      "Multiplayer" in waiting, False)
check("sending is distinct from both",
      sending not in (playing, waiting), True)
check("the real launcher falls back to its ready line",
      "_status_if_changed(*self._ready)"
      in inspect.getsource(L.Launcher._watch_game), True)


print()
print("Play on the galaxy already being followed")
src = inspect.getsource(L.Launcher._take_turn_loop)
# With a live client, Play is refused as a second game. With the client closed,
# it asks the loop, the only thing allowed to start a client, to open it again.
check("the two states are told apart", "mp_client_gone" in src, True)
check("a closed game asks the loop to open it again",
      "self.mp_reopen.set()" in src, True)
check("and is not turned away with a window saying the turn was sent",
      "already been sent" in src, False)
check("while a live client still reads as playing",
      "You are already playing" in src, True)
check("neither branch starts anything itself",
      ("start_multiplayer" in src, "serve(" in src), (False, False))
check("and the loop is handed the event it watches",
      "reopen=self.mp_reopen" in mp, True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))

print()
print("the indicator once the game window has closed")
src = inspect.getsource(L.Launcher._refresh_turn)
# A failed last capture followed by a send with nothing new to store must not
# leave the indicator on 'saving your turn...'.
check("a closed game stops the indicator saying anything",
      "mp_client_gone and not self.mp_sending" in src, True)
check("and a state of None is what hides it",
      L.capture_readout(None, 0.0)[0] in (None, ""), True)
check("while a submission in flight still says so",
      L.capture_readout("capturing", 0.0)[0] not in (None, ""),
      True)

print()
print("the turn controls follow the game window, not the loop")
wsrc = inspect.getsource(L.Launcher._watch_game)
# Next Turn, Send Turn and Load act on a running client, so they are hidden
# while the loop waits without one.
check("they are hidden once the client has gone",
      "_show_controls(not self.mp_client_gone)" in wsrc, True)
check("and nothing shows them unconditionally while a loop runs",
      "_show_controls(True)" in wsrc, False)

print()
print("a window closed after the turn already finished")


class _Close:
    """Just enough Launcher to drive _watch_mp_client."""

    def __init__(self, turn_open):
        self.mp_client_seen, self.mp_client_gone = True, False
        self.mp_sending, self.mp_turn_open = False, turn_open
        self.said = []

    def say(self, m):
        self.said.append(m)


mid = _Close(turn_open=True)
L.Launcher._watch_mp_client(mid, False)
check("a turn still open says it is being sent", mid.mp_sending, True)

# The loop finished the turn and emitted `waiting` before the window was
# closed, so closing it announces no send.
done = _Close(turn_open=False)
L.Launcher._watch_mp_client(done, False)
check("a turn already finished does not", done.mp_sending, False)
check("and it is still noticed that the game closed",
      any("has closed" in m for m in done.said), True)
check("without claiming anything is on its way",
      any("sending" in m for m in done.said), False)

st = inspect.getsource(L.Launcher._mp_state)
check("a finished turn closes the window of sending",
      "mp_turn_open = False" in st, True)
check("and serving a turn opens it again",
      "mp_turn_open = True" in st, True)

print()
print("sharing a server this launcher did not start")
import tempfile, json as _json
_d = tempfile.mkdtemp()

check("with no adopt_server there is nothing to share",
      L.adopted_server(_d), None)

_saves = os.path.join(_d, 'their_saves')
os.makedirs(_saves, exist_ok=True)
_json.dump({'adopt_server': _saves},
           open(os.path.join(_d, 'multiplayer.json'), 'w'))
check("and with one it names where that server writes",
      L.adopted_server(_d), os.path.abspath(_saves))

_json.dump({'adopt_server': 12},
           open(os.path.join(_d, 'multiplayer.json'), 'w'))
check("a value that is not a path is not one", L.adopted_server(_d), None)

# A held port with no adopt_server is still refused: the protocol cannot ask
# that server where its captures land.
bsrc = inspect.getsource(L.Launcher._boot)
check("a held port with nothing said about it is still refused",
      'not reusing it' in inspect.getsource(L.port_held_message)
      and 'self.warn(box)' in bsrc, True)
check("sharing needs the server to answer our protocol",
      'if ours and adopt and os.path.isdir(adopt)' in bsrc, True)
check("and a directory that is not there is refused, not believed",
      'which is not a ' in bsrc, True)
check("the turn loop collects from the shared directory",
      'self.save_dir or os.path.join' in
      inspect.getsource(L.Launcher.start_multiplayer), True)
sys.exit(1 if fails else 0)
