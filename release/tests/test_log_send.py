"""
Sending the launcher's log (M1): when the player is offered it, what they are
told, and that nothing leaves without Send.

    server\\.venv\\Scripts\\python.exe release\\tests\\test_log_send.py

Headless. The relay is a local HTTP server on a free port that records what it
is sent and answers the way `functions/relay.py` does, so this needs no
emulator; `server/tests/test_log_upload.py` runs the real relay. The launcher
object is not built: its methods run on a stand-in carrying the attributes
they read.
"""
import base64
import http.server
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import types
import zlib

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import launcher as L                                            # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}"[:300])
    if not ok:
        fails.append(label)


dirs = []


def fresh_dir():
    d = tempfile.mkdtemp(prefix="logsend_")
    dirs.append(d)
    return d


class Relay(http.server.BaseHTTPRequestHandler):
    """Records each POST and answers with `answer`."""

    got = []
    answer = (200, {"id": "20260929T040000Z-abcdefgh-0a0b0c", "bytes": 1})

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        Relay.got.append((self.path, dict(self.headers), self.rfile.read(n)))
        code, body = Relay.answer
        out = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Relay)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
ROOT = f"http://127.0.0.1:{httpd.server_address[1]}"


def make_app(data_dir, store_spec=None, choose=("send",)):
    app = types.SimpleNamespace()
    app.data_dir = data_dir
    app.games_dir = None
    app.mp_store = object()
    app.mp_civ = "Ada"
    app.mp_turn = 12
    app.mp_note = ""
    app.mp_deadline = None
    app.mp_waiting = False
    app.mp_capture = None
    app.mp_playing = ({"id": "sandbox", "name": "Sandbox",
                       "store": store_spec} if store_spec else None)
    app.mp_reopening = False
    app.mp_sending = False
    app.mp_turn_open = False
    app.mp_stop = False
    app.mp_closed = False
    app.mp_probed = None
    app.mp_log_offered = False
    app.log_sending = False
    app.msgs = queue.Queue()
    app.said, app.warned, app.dialogs, app.opened = [], [], [], []
    app.choose = list(choose)
    app.say = app.said.append
    app.say_threadsafe = app.said.append
    app.warn = app.warned.append

    def dialog(title, body, choices):
        app.dialogs.append((title, body, [c[0] for c in choices]))
        return app.choose.pop(0) if app.choose else None
    app._choice_dialog = dialog
    app._open_file = app.opened.append
    app._mp_probe_closed = lambda store: None
    for name in ("_mp_state", "_on_log_offer", "offer_log", "_on_log_sent"):
        setattr(app, name, getattr(L.Launcher, name).__get__(app))

    def drain(wait=10.0):
        seen = []
        end = time.time() + wait
        while time.time() < end:
            try:
                msg = app.msgs.get(timeout=0.05)
            except queue.Empty:
                if not app.log_sending:
                    return seen
                continue
            seen.append(msg[0])
            if msg[0] == "__log_offer__":
                app._on_log_offer(msg[1])
            elif msg[0] == "__log_sent__":
                app._on_log_sent(*msg[1:])
        return seen
    app.drain = drain
    return app


def galaxy_dir(auth=True):
    d = fresh_dir()
    with open(os.path.join(d, L.MP_CONFIG), "w") as f:
        json.dump({"store": f"{ROOT}/sandbox", "auth": auth}, f)
    home = os.path.expanduser("~")
    with open(os.path.join(d, L.LOG_NAME), "w", encoding="utf-8") as f:
        f.write(f"[04:00:00] data    {home}\\Desktop\\game\n"
                "[04:00:01] [Ada] turn 12: LOST, HTTP Error 400: Bad Request: "
                "this is not a save of turn 12\n"
                "[04:00:02]   body: userid=1&gamename='x'&turn=12&data="
                + "Q" * 400 + "\n")
    return d


real_token = L.player_token
L.player_token = lambda d: (lambda: "tok-123")

try:
    print("1. where a log goes")
    d = galaxy_dir()
    check("a followed relay galaxy's own relay",
          L.log_upload_base(d, f"{ROOT}/sandbox", None), ROOT)
    check("the directory when nothing is followed",
          L.log_upload_base(d, None, ROOT + "/"), ROOT)
    check("a folder galaxy reaches no relay",
          L.log_upload_base(fresh_dir(), r"C:\galaxies\sandbox", None), None)
    # Fails if an http spec were taken without the identity rule a store uses.
    check("a LAN referee over http reaches none",
          L.log_upload_base(fresh_dir(), "http://192.168.1.5:8765", None),
          None)
    check("an https relay does without being told",
          L.log_upload_base(fresh_dir(), "https://relay.example/sandbox",
                            None), "https://relay.example")

    print("\n2. what is sent")
    body = L.log_payload("hello\n", "0.1.6", L.LOG_WHY_TURN, galaxy="g",
                         civ="Ada", turn=12)
    sent = json.loads(body)
    check("the text, compressed and in base64",
          zlib.decompress(base64.b64decode(sent["log"])).decode(), "hello\n")
    check("with what it is about",
          (sent["why"], sent["build"], sent["galaxy"], sent["civ"],
           sent["turn"]), ("turn_failed", "0.1.6", "g", "Ada", 12))

    print("\n3. a turn that goes wrong offers the log, once")
    d = galaxy_dir()
    app = make_app(d, f"{ROOT}/sandbox")
    Relay.got.clear()
    app._mp_state("lost", turn=12, civ="Ada", error="HTTP Error 400")
    app._mp_state("lost", turn=13, civ="Ada", error="HTTP Error 400")
    seen = app.drain()
    check("offered once for two lost turns", seen.count("__log_offer__"), 1)
    title, text, buttons = app.dialogs[0]
    check("the box says why it is asking",
          text.startswith(L.LOG_TURN_LEAD.strip()[:30]), True)
    # Fails if the box stopped saying what the copy holds (M2).
    check("and what the copy holds and what is taken out",
          all(s in text for s in ("player name", "galaxy", "turn numbers",
                                  "Windows account name is taken out",
                                  "saved-game data")), True)
    check("and that nothing goes without Send",
          "Nothing is sent unless you press Send" in text, True)
    check("with Send, Read it first and Not now", buttons,
          ["Send", "Read it first", "Not now"])
    check("Send posted one log to the relay's route",
          [p for p, _h, _b in Relay.got], ["/_logs"])
    path, headers, raw = Relay.got[0]
    check("with this install's token",
          {k.lower(): v for k, v in headers.items()}.get("authorization"),
          "Bearer tok-123")
    posted = json.loads(raw)
    text_sent = zlib.decompress(base64.b64decode(posted["log"])).decode()
    account = os.path.basename(os.path.expanduser("~"))
    check("the redacted copy, not the log",
          (account.lower() in text_sent.lower(), "Q" * 50 in text_sent,
           "LOST" in text_sent), (False, False, True))
    check("exactly the copy written beside the log",
          open(os.path.join(d, L.REDACTED_LOG_NAME), encoding="utf-8").read(),
          text_sent)
    check("naming the galaxy, civ and the latest turn the loop reported",
          (posted["why"], posted["galaxy"], posted["civ"], posted["turn"]),
          ("turn_failed", "sandbox", "Ada", 13))
    check("and the player is told it went, with a reference",
          app.dialogs[-1][:2],
          lambda t: t[0] == "Log sent"
          and "20260929T040000Z-abcdefgh-0a0b0c" in t[1])

    print("\n4. nothing leaves without Send")
    d = galaxy_dir()
    app = make_app(d, f"{ROOT}/sandbox", choose=("read", None))
    Relay.got.clear()
    app.offer_log()
    # Anything sent goes on a thread, so it is waited for before looking.
    time.sleep(1.0)
    app.drain()
    check("Read it first opens the copy that would be sent", app.opened,
          [os.path.join(d, L.REDACTED_LOG_NAME)])
    check("and asks again", len(app.dialogs), 2)
    # Fails if Not now or closing the box sent anything.
    check("Not now sends nothing", Relay.got, [])
    check("the button's box has no failed-turn lead",
          app.dialogs[0][1].startswith("Send the launcher's log"), True)

    print("\n5. when it is not offered")
    d = galaxy_dir()
    app = make_app(d, f"{ROOT}/sandbox")
    app._mp_state("failed", error="DemoPlayer is " + L.SEAT_HELD)
    check("not for a seat held by another sign-in", app.drain(), [])
    app = make_app(d, f"{ROOT}/sandbox")
    app._mp_state("lost", turn=12, civ="Ada")
    app.mp_closed = True
    app.drain()
    check("not for a galaxy found closed", app.dialogs, [])
    app = make_app(d, f"{ROOT}/sandbox")
    app._mp_state("capture_failed", turn=12, civ="Ada", error="x")
    app._mp_state("overtaken", turn=12, current=13)
    check("not for a capture that failed once or a turn overtaken",
          app.drain(), [])
    app = make_app(fresh_dir(), r"C:\galaxies\sandbox")
    app._mp_state("lost", turn=12, civ="Ada")
    app.drain()
    check("after a lost turn with no relay, no box at all",
          (app.dialogs, app.warned), ([], []))
    check("and a line in the log", any("no relay" in s for s in app.said),
          True)
    app.offer_log()
    check("the link says there is nowhere to send, and where the copy is",
          app.warned, lambda w: len(w) == 1 and "not connected to the beta"
          in w[0] and L.REDACTED_LOG_NAME in w[0])
    # The reset is read from the source: both methods need a whole window.
    import inspect
    src = inspect.getsource(L.Launcher.stop_multiplayer)
    check("stopping the loop lets the next galaxy offer again",
          "self.mp_log_offered = False" in src, True)
    src = inspect.getsource(L.Launcher.start_multiplayer)
    check("and so does starting one", "self.mp_log_offered = False" in src,
          True)

    print("\n6. a refusal is said in the relay's words")
    d = galaxy_dir()
    app = make_app(d, f"{ROOT}/sandbox")
    Relay.answer = (429, {"error": "this sign-in has sent 5 logs today, "
                                   "which is the most one may send in a day"})
    app.offer_log()
    app.drain()
    check("the player reads the relay's sentence and where the copy is",
          app.warned, lambda w: len(w) == 1 and "most one may send" in w[0]
          and L.REDACTED_LOG_NAME in w[0])
    check("and may try again", app.log_sending, False)
    Relay.answer = (200, {"id": "x"})

    print("\n7. the notice clause")
    check("says nothing goes by itself",
          L.LOG_UPLOAD_CLAUSE.startswith("Nothing is sent by itself"), True)
    check("and when it does go", "press Send" in L.LOG_UPLOAD_CLAUSE, True)
finally:
    L.player_token = real_token
    httpd.shutdown()
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)

print()
if fails:
    print(f"{len(fails)} FAILED:")
    for f in fails:
        print(f"  {f}")
    sys.exit(1)
print("ALL PASSED")
