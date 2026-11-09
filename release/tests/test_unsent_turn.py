"""
test_unsent_turn.py: a turn held up by an outage says so, and a lost
connection is read at once
=====================================================================
    python release/tests/test_unsent_turn.py

With the game closed and the final send retried through an outage, the status
line names the outage, the capture indicator says the turn is not sent, and
the Galaxies page line says why Play is grey. The status bar holds the build
line, the status and the capture indicator on one line, so the two short texts
are kept short.

`_watch_network` asks Windows every NET_CHECK_EVERY seconds whether this
machine is online, and sets `mp_check_now` when the answer changes, so the
turn loop reads the galaxy at once. Headless, against a Launcher built without
`__init__` and a stand-in for `netstate`.
"""
import os
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import launcher as L                                            # noqa: E402
import netstate                                                 # noqa: E402
import turn_store                                               # noqa: E402
from fake_view import FakeView                                  # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


class Thread:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


class Store:
    def current(self):
        return 14, time.time() + 900


def bare(**over):
    """Following a galaxy with the game closed, its final send retried."""
    app = object.__new__(L.Launcher)
    app.mp_store = Store()
    app.mp_turn = 14
    app.mp_deadline = time.time() + 900
    app.mp_turn_seconds = 14400
    app.mp_waiting = False
    app.mp_civ = "DemoPlayer"
    app.mp_capture = (L.HELD, time.time())
    app.mp_playing = {"id": "sandbox", "name": "Sandbox",
                      "store": "C:\\g\\sandbox"}
    app.mp_thread = Thread()
    app.mp_client_seen = True
    app.mp_client_gone = True
    app.mp_turn_open = True
    app.mp_sending = True
    app.mp_note = ""
    app.mp_unreachable = None
    app.mp_send_now = threading.Event()
    app.mp_reopen = threading.Event()
    app.mp_check_now = threading.Event()
    app.mp_reopening = False
    app.mp_closed = False
    app.mp_stop = False
    app.data_dir = os.path.join(REPO, "release", "data")
    app.view = FakeView()
    app._ctl_client = None
    app._ctl_busy = False
    app.running_mode = None
    app.said = []
    app.say = app.said.append
    app.say_threadsafe = app.said.append
    app.msgs = types.SimpleNamespace(put=lambda *a: None)
    for k, v in over.items():
        setattr(app, k, v)
    return app


def width(text):
    """Rough Tahoma 11px width: 5.2 px a character, measured on these texts."""
    return 5.2 * len(text)


print("1. the short texts cover every outage the store names")
names = {turn_store.OUTAGE_NETWORK, turn_store.OUTAGE_SERVICE,
         turn_store.OUTAGE_STORAGE}
check("the status texts", set(L.OUTAGE_SHORT), names)
check("the Galaxies page reasons", set(L.OUTAGE_REASONS), names)
longest = max(list(L.OUTAGE_SHORT.values()) + [L.UNREACHABLE_SHORT], key=len)
check("the longest status, the capture text and the build line fit the "
      "480 px status bar",
      width(longest) + width(L.UNSENT_CAPTURE)
      + width("v0.5.0.3 \u00b7 player code 15O27XJc") + 3 * 18 + 2 * 13,
      lambda w: w <= 480)

print("\n2. sending with the game closed")
app = bare()
app._mp_state("collecting", turn=14, civ="DemoPlayer")
check("not retrying, the status says to keep the window open",
      app._sending_status(), "keep this window open")
app._refresh_turn()
check("and the capture indicator says it is sending",
      app.view.capture[0], lambda t: "sending" in t.lower())

print("\n3. the final send retried through an outage")
app._mp_state("retrying", turn=14, civ="DemoPlayer", error="offline",
              outage=turn_store.OUTAGE_NETWORK)
check("the orders are still on their way", app.mp_sending, True)
check("the status names the outage", app._sending_status(), "no internet")
app._refresh_turn()
check("the capture indicator says the turn is not sent",
      app.view.capture, (L.UNSENT_CAPTURE, L.WARN))
check("the turn readout says the orders are not sent and why",
      app.view.turn_label,
      lambda t: t.startswith(L.RETRYING_LEAD) and "internet" in t)
check("Play stays grey", app.turn_busy(), L.TURN_SENDING)
check("and the Galaxies page line says why",
      L.unsent_hint(app._mp_retry_outage()),
      lambda t: "not sent yet" in t and "no internet connection" in t
      and "keep the launcher open" in t)
check("a server outage is named as one",
      L.unsent_hint(turn_store.OUTAGE_SERVICE),
      lambda t: "galaxy server is not answering" in t)
check("an outage the store could not name still reads",
      (bare(mp_retry_outage=None, mp_note=L.RETRYING_LEAD + "x")
       ._sending_status(), L.unsent_hint(None)),
      lambda p: p[0] == L.UNREACHABLE_SHORT and "cannot be reached" in p[1])

app._mp_state("submitted", turn=14, civ="DemoPlayer", final=True)
check("once it lands the send is over", app.mp_sending, False)
check("and the status no longer claims it is retrying",
      app._mp_retrying(), False)
app._refresh_turn()
check("the capture indicator says it was sent",
      app.view.capture[0], lambda t: "turn sent" in t)

print("\n4. a change in the connection asks for a read")
answers = []
fake = types.SimpleNamespace(online=lambda: answers.pop(0))
saved = sys.modules.get("netstate")
sys.modules["netstate"] = fake
try:
    app = bare(mp_sending=False)
    t0 = 5000.0
    answers[:] = [True]
    app._watch_network(t0)
    check("the first answer only sets what is known",
          app.mp_check_now.is_set(), False)
    answers[:] = [True]
    app._watch_network(t0 + 0.5)
    check("Windows is asked at most every NET_CHECK_EVERY seconds",
          answers, [True])
    app._watch_network(t0 + L.NET_CHECK_EVERY)
    check("the same answer again asks for nothing",
          app.mp_check_now.is_set(), False)
    answers[:] = [False]
    app._watch_network(t0 + 2 * L.NET_CHECK_EVERY)
    check("going offline asks the loop for a read",
          app.mp_check_now.is_set(), True)
    check("and the log says why", app.said,
          lambda lines: any("went offline" in l for l in lines))
    app.mp_check_now.clear()
    answers[:] = [True]
    app._watch_network(t0 + 3 * L.NET_CHECK_EVERY)
    check("coming back asks for one too", app.mp_check_now.is_set(), True)
    app.mp_check_now.clear()
    answers[:] = [None]
    app._watch_network(t0 + 4 * L.NET_CHECK_EVERY)
    answers[:] = [False]
    app._watch_network(t0 + 5 * L.NET_CHECK_EVERY)
    check("an answer after Windows could not say is only a starting point",
          app.mp_check_now.is_set(), False)
    app = bare(mp_thread=None)
    answers[:] = [True]
    app._watch_network(t0)
    answers[:] = [False]
    app._watch_network(t0 + L.NET_CHECK_EVERY)
    check("with no galaxy followed nothing is asked for",
          app.mp_check_now.is_set(), False)
finally:
    if saved is not None:
        sys.modules["netstate"] = saved
    else:
        sys.modules.pop("netstate", None)

print("\n5. this machine answers")
check("netstate.online is True, False or None", netstate.online(),
      lambda v: v in (True, False, None))
if sys.platform == "win32":
    check("and on Windows it can say", netstate.online(),
          lambda v: v is not None)

print()
if fails:
    print(f"FAILURES: {fails}")
    sys.exit(1)
print("ALL PASSED")
