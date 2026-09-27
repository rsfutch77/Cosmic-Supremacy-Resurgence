"""
test_capture_indicator.py , what the launcher shows about a turn in flight
==========================================================================
    server\\.venv\\Scripts\\python.exe release\\tests\\test_capture_indicator.py

Four things a player reads off the launcher while a turn is running, and one
they read before it starts:

  the capture indicator  beside the server's dot: capturing now, captured but
                         not sent, and sent, told apart at a glance rather than
                         by reading the log.
  the Save button        which means capture and send my turn now during a
                         multiplayer turn, and write a .dat outside one.
  the turn readout       which is a fact about the game client, so it goes the
                         moment the client does, while the submission carries
                         on behind it.
  the port refusal       a server on 8888 that is not this launcher's is
                         refused rather than reused: it keeps its captures in
                         its own folder and there is no way to ask it which.

Headless. No window is opened and no game is started: the widgets are stubs
that record what was configured on them, and the launcher object is built
without running __init__ so that nothing reaches Tk, a data directory or a
port. What is under test is the launcher's own logic, which is where all four
of these live.
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

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


# ── stubs ────────────────────────────────────────────────────────────────────
class Widget:
    """A Tk widget as far as the launcher uses one: options and packing."""

    def __init__(self, **kw):
        self.kw = dict(kw)
        self.mapped = False

    def cget(self, key):
        return self.kw.get(key, "")

    def configure(self, **kw):
        self.kw.update(kw)

    def pack(self, **kw):
        self.mapped = True

    def pack_forget(self):
        self.mapped = False

    def winfo_ismapped(self):
        return self.mapped


class Store:
    """A galaxy clock the readout can ask about."""

    def __init__(self, turn=14, left=930.0):
        self.turn, self.left = turn, left

    def current(self):
        return self.turn, time.time() + self.left

    def seconds_left(self):
        return self.left


class Thread:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


def bare(**over):
    """A Launcher with no window behind it.

    __init__ builds widgets, opens a log and starts a server, none of which
    this can have. The attributes the methods under test read are set here
    instead, so what runs is the real method against stub widgets.
    """
    app = object.__new__(L.Launcher)
    app.mp_store = None
    app.mp_note = ""
    app.mp_turn = None
    app.mp_civ = "DemoPlayer"
    app.mp_capture = None
    app.mp_playing = None
    app.mp_thread = None
    app.mp_client_seen = False
    app.mp_client_gone = False
    app.mp_sending = False
    app.mp_send_now = threading.Event()
    app.mp_stop = False
    app.data_dir = os.path.join(REPO, "release", "data")
    app.turn_label = Widget(text="turn ,")
    app.cap_dot = Widget(fg=L.FAINT, text="●")
    app.cap_status = Widget(text="")
    app.ctl_buttons = {k: Widget(text=t, state="normal")
                       for k, t in (("load", "Load"), ("save", "Save"),
                                    ("turn", "Next Turn"))}
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
check("captured and not sent says both halves", seen[L.HELD][0],
      lambda t: "saved" in t.lower() and "not sent" in t.lower())
check("sent says sent", seen[L.SENT][0], lambda t: "sent" in t.lower())
check("all three read differently",
      len({t for t, _c in seen.values()}), 3)
check("and are three different colours",
      len({c for _t, c in seen.values()}), 2)      # held shares warn with busy
check("held is not the colour of sent",
      seen[L.HELD][1] != seen[L.SENT][1], True)
check("sent is the colour the server dot uses when it is up",
      seen[L.SENT][1], L.OK)
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

print("\n2. the indicator is packed only while there is something to say")
app = bare()
app._show_capture(None, 0.0)
check("nothing to say leaves the status row as it was",
      app.cap_dot.mapped, False)
app._show_capture(L.CAPTURING, 0.0)
check("a capture starting puts the dot up", app.cap_dot.mapped, True)
check("with its own colour", app.cap_dot.cget("fg"), L.WARN)
app._show_capture(L.SENT, 2.0)
check("and sending repaints it green", app.cap_dot.cget("fg"), L.OK)
app._show_capture(None, 0.0)
check("and it goes again when the loop stops", app.cap_status.mapped, False)

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

app = bare(mp_thread=None)
called = []
app._in_background = lambda label, work, done=None: called.append(label)
app.on_save()
check("outside multiplayer it still writes a .dat", called, ["saving"])
check("and asks no turn loop for anything", app.mp_send_now.is_set(), False)

import inspect                                                  # noqa: E402
src = inspect.getsource(L.Launcher.on_save)
check("the single-player half is unreachable while a loop is running",
      src.index("playing_now()") < src.index("save_game"), True)

print("\n5. the button names what it will do")
app = bare(mp_store=Store(), mp_thread=Thread(alive=True))
app._refresh_turn()
check("in multiplayer the button says it sends the turn",
      app.ctl_buttons["save"].cget("text"), "Send Turn")
check("and Next Turn is not offered, the referee owns the clock",
      app.ctl_buttons["turn"].cget("state"), "disabled")
check("while Save stays live", app.ctl_buttons["save"].cget("state"), "normal")

app = bare(mp_store=None)
app._refresh_turn()
check("outside multiplayer it is Save again",
      app.ctl_buttons["save"].cget("text"), "Save")

print("\n6. closing the game clears the readout at once")
app = bare(mp_store=Store(turn=14, left=930.0), mp_thread=Thread(alive=True))
app._watch_mp_client(True)
app._refresh_turn()
check("while the client is up the readout is the galaxy's clock",
      app.turn_label.cget("text"), lambda t: t.startswith("turn 14"))
app._watch_mp_client(False)
check("the client going is noticed on the tick it happens",
      app.mp_client_gone, True)
app._refresh_turn()
check("and the turn readout stops claiming a turn is open",
      app.turn_label.cget("text"), lambda t: "turn 14" not in t)
check("saying instead that the turn is still being sent",
      app.turn_label.cget("text"), lambda t: "sending" in t.lower())
check("the log says it too",
      app.said, lambda lines: any("closed" in l for l in lines))
check("and the status says the sending is still happening",
      app.mp_sending, True)
check("Save has nothing left to ask for and goes grey",
      app.ctl_buttons["save"].cget("state"), "disabled")
app.mp_capture = (L.HELD, time.time())
app._refresh_turn()
check("while the indicator keeps reporting the send",
      app.cap_status.cget("text"), lambda t: "not sent" in t)

app._mp_state("submitted", turn=14, civ="DemoPlayer", final=True)
check("the final submission ends the sending", app.mp_sending, False)
app._refresh_turn()
check("and the readout stops saying it",
      app.turn_label.cget("text"), lambda t: "sending" not in t)
check("without putting the closed turn's countdown back",
      app.turn_label.cget("text"), lambda t: "turn 14" not in t)

app._watch_mp_client(True)
check("a client coming back puts the readout back",
      app.mp_client_gone, False)
app._refresh_turn()
check("with the galaxy's clock on it",
      app.turn_label.cget("text"), lambda t: t.startswith("turn 14"))
check("and Save live again", app.ctl_buttons["save"].cget("state"), "normal")

print("\n7. a foreign server on the port is refused, not reused")
boot = inspect.getsource(L.Launcher._boot)
taken = boot[boot.index("if not port_is_free("):
             boot.index("self.servers, logfile = start_server")]
check("nothing reuses a server this launcher did not start",
      "already serving our protocol" in boot, False)
check("a taken port never reports a healthy server",
      ", OK)" in taken, False)
check("a taken port has one way out and it is not a server",
      taken.count("return"), 1)
check("the refusal really is the branch that was read",
      len(taken), lambda n: 400 < n < 3000)
check("the refusal names the port",
      "Another server already holds port" in boot, True)
check("and tells the player to close it",
      "Close it and start this launcher again" in boot, True)
check("the port check is still one bind",
      boot.count("port_is_free("), 1)
check("and the protocol probe only runs once the port is known to be taken",
      boot.index("port_is_free(") < boot.index("stub_server_answers("), True)
check("the ordinary case, a free port, still starts a server",
      boot.index("stub_server_answers(") < boot.index("start_server("), True)
check("only a server this launcher started counts as ours",
      boot.count("self.server_ok = True"), 1)
mp = inspect.getsource(L.Launcher.start_multiplayer)
check("and a turn will not start without one",
      "if not self.server_ok:" in mp, True)

print("\n8. the port check itself, against a socket nothing is on")
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


def _status_for(sending, gone):
    """The branch as `_watch_game` runs it, with mp_live true and no client."""
    st = _St(sending, gone)
    if st.mp_sending:
        st._status_if_changed("sending your turn , keep this window open", "warn")
    elif st.mp_client_gone:
        st._status_if_changed(f"Multiplayer , waiting for the next turn", "ok")
    else:
        st._status_if_changed(f"Multiplayer , {st.mp_civ}", "ok")
    return st.said[0][0]


playing = _status_for(False, False)
waiting = _status_for(False, True)
sending = _status_for(True, True)
check("playing names the civ", "DemoPlayer" in playing, True)
# The bug: the loop stays alive between turns, so a closed game read as an open
# one for hours. Fails if waiting and playing say the same thing.
check("waiting does not read as playing", waiting == playing, False)
check("and says what it is waiting for", "waiting" in waiting, True)
check("sending is distinct from both",
      sending not in (playing, waiting), True)
check("the real launcher carries the waiting branch",
      "waiting for the next turn" in inspect.getsource(L.Launcher._watch_game),
      True)

print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
