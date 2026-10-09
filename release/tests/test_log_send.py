"""
Sending the launcher's log (M1): when the player is offered it, what they are
told, and that nothing leaves without Send.

    server\\.venv\\Scripts\\python.exe release\\tests\\test_log_send.py

Headless. The relay is a local HTTP server on a free port that records what it
is sent and answers the way `functions/relay.py` does, so this needs no
emulator; `server/tests/test_log_upload.py` runs the real relay. The launcher
object is not built: its methods run on a stand-in carrying the attributes
they read, with a `FakeView` for the window. The Tk rows of the reference box
are built once on a withdrawn root and read back without being shown.
"""
import base64
import http.server
import inspect
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import launcher as L                                            # noqa: E402
from fake_view import FakeView                                  # noqa: E402

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
    app.view = FakeView(choose=list(choose))
    app.said, app.opened = [], []
    app.say = app.said.append
    app.say_threadsafe = app.said.append
    app._open_file = app.opened.append
    app._mp_probe_closed = lambda store: None
    for name in ("_mp_state", "_on_log_offer", "offer_log", "_on_log_sent",
                 "show_log_refs", "_show_log_refs_link", "_log_refs_dialog",
                 "_choice_dialog", "warn"):
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


def dialogs(app):
    """The boxes the view was asked for, in order, as (title, body, labels):
    the button labels of a choice, or the references of a reference box."""
    out = []
    for kind, args in app.view.prompts:
        if kind in ("choose", "log_refs"):
            title, body, rows = args
            out.append((title, body, [row[0] for row in rows]))
    return out


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
real_uid = L.install_uid
L.install_uid = lambda d, mint=False: "abcd1234efgh5678"

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
    title, text, buttons = dialogs(app)[0]
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
    check("and the player is told it went, with the reference in a row",
          dialogs(app)[-1],
          lambda t: t[0] == "Log sent"
          and t[2] == ["20260929T040000Z-abcdefgh-0a0b0c"])
    # Fails if the box stopped offering the player code for a lost reference.
    check("and told the player code finds the log if the reference is lost",
          dialogs(app)[-1][1], lambda b: "If you lose the log reference" in b
          and "player code" in b and "abcd1234" in b)
    check("the reference is kept beside the launcher's other records",
          L.load_log_refs(d), lambda r: [x["id"] for x in r]
          == ["20260929T040000Z-abcdefgh-0a0b0c"]
          and os.path.dirname(L.log_refs_path(d)) == d)
    check("and the log history link is shown", app.view.log_refs_link, True)

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
    check("and asks again", len(dialogs(app)), 2)
    # Fails if Not now or closing the box sent anything.
    check("Not now sends nothing", Relay.got, [])
    check("the button's box has no failed-turn lead",
          dialogs(app)[0][1].startswith("Send the launcher's log"), True)

    print("\n5. when it is not offered")
    d = galaxy_dir()
    app = make_app(d, f"{ROOT}/sandbox")
    app._mp_state("failed", error="DemoPlayer is " + L.SEAT_HELD)
    check("not for a seat held by another sign-in", app.drain(), [])
    app = make_app(d, f"{ROOT}/sandbox")
    app._mp_state("lost", turn=12, civ="Ada")
    app.mp_closed = True
    app.drain()
    check("not for a galaxy found closed", dialogs(app), [])
    app = make_app(d, f"{ROOT}/sandbox")
    app._mp_state("capture_failed", turn=12, civ="Ada", error="x")
    app._mp_state("overtaken", turn=12, current=13)
    check("not for a capture that failed once or a turn overtaken",
          app.drain(), [])
    app = make_app(fresh_dir(), r"C:\galaxies\sandbox")
    app._mp_state("lost", turn=12, civ="Ada")
    app.drain()
    check("after a lost turn with no relay, no box at all",
          (dialogs(app), app.view.warnings()), ([], []))
    check("and a line in the log", any("no relay" in s for s in app.said),
          True)
    app.offer_log()
    check("the link says there is nowhere to send, and where the copy is",
          app.view.warnings(), lambda w: len(w) == 1
          and "not connected to the beta" in w[0]
          and L.REDACTED_LOG_NAME in w[0])
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
          app.view.warnings(), lambda w: len(w) == 1
          and "most one may send" in w[0] and L.REDACTED_LOG_NAME in w[0])
    check("and may try again", app.log_sending, False)
    Relay.answer = (200, {"id": "x"})

    print("\n7. the notice clause")
    check("says nothing goes by itself",
          L.LOG_UPLOAD_CLAUSE.startswith("Nothing is sent by itself"), True)
    check("and when it does go", "press Send" in L.LOG_UPLOAD_CLAUSE, True)
    print("\n8. copying a reference")
    # The box's rows, sent by the real view to a recording stand-in for the
    # page. The box is answered from a timer, as the page's Close would be.
    from fake_view import PageWindow
    from web_view import WebView
    view = WebView({"product": "test"})
    view.window = PageWindow(view)
    view.after(50, lambda: view.resolve(view.window.prompts()[-1]["id"],
                                        None, None))
    view.log_refs("Log sent", "body", [("ref-a", "1 Oct 2026 10:00"),
                                       ("ref-b", "")])
    box = view.window.prompts()[-1]
    check("each row carries its own reference with its send time",
          box["refs"], [{"id": "ref-a", "when": "1 Oct 2026 10:00"},
                        {"id": "ref-b", "when": ""}])
    check("and the button reads Copy, then Copied",
          (box["copy"], box["copied"]), ("Copy", "Copied"))
    js = open(os.path.join(REPO, "release", "webui", "app.js"),
              encoding="utf-8").read()
    rows = js[js.index("for (const ref of p.refs"):js.index("const btns")]
    # Fails if a row's button copied anything but its own reference.
    check("each row's button copies its own reference",
          "navigator.clipboard.writeText(ref.id)" in rows, True)
    check("and the entry holding it is read-only",
          "box.readOnly = true" in rows, True)
    check("and the button goes back to Copy a moment later",
          "setTimeout(" in rows, True)

    print("\n9. the references kept")
    d = fresh_dir()
    check("none before the first send", L.load_log_refs(d), [])
    for i in range(7):
        L.record_log_ref(d, f"ref-{i}", when=1790000000 + i)
    kept = L.load_log_refs(d)
    # Fails if the cap were dropped or the list kept oldest first.
    check(f"the last {L.LOG_REFS_KEEP}, newest first",
          [r["id"] for r in kept], ["ref-6", "ref-5", "ref-4", "ref-3",
                                    "ref-2"])
    check("and the file holds no more than that",
          len(json.load(open(L.log_refs_path(d), encoding="utf-8"))["sent"]),
          L.LOG_REFS_KEEP)
    check("with the send time in UTC", kept[0]["sent"],
          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(1790000006)))
    check("shown as a local date and time",
          L.log_ref_when(kept[0]["sent"]),
          time.strftime("%d %b %Y %H:%M", time.localtime(1790000006)))
    L.record_log_ref(d, "ref-3", when=1790000100)
    check("one sent again moves to the front, once",
          [r["id"] for r in L.load_log_refs(d)],
          ["ref-3", "ref-6", "ref-5", "ref-4", "ref-2"])
    with open(L.log_refs_path(d), "w", encoding="utf-8") as f:
        f.write("{not json")
    check("a corrupt file reads as none", L.load_log_refs(d), [])
    L.record_log_ref(d, "ref-new", when=1790000200)
    check("and the next send writes it whole again",
          [r["id"] for r in L.load_log_refs(d)], ["ref-new"])
    with open(L.log_refs_path(d), "w", encoding="utf-8") as f:
        json.dump({"sent": [{"id": "good", "sent": 5}, {"id": ""}, "x",
                            {"sent": "2026-09-30T00:00:00Z"},
                            {"id": "also", "sent": "2026-09-30T00:00:00Z"}]},
                  f)
    check("records of the wrong shape are skipped, a bad time is blank",
          L.load_log_refs(d), [{"id": "good", "sent": ""},
                               {"id": "also",
                                "sent": "2026-09-30T00:00:00Z"}])
    check("and a blank time shows as nothing", L.log_ref_when(""), "")
    with open(L.log_refs_path(d), "w", encoding="utf-8") as f:
        json.dump(["not", "an", "object"], f)
    check("a file holding a list reads as none", L.load_log_refs(d), [])

    print("\n10. finding a reference again")
    d = fresh_dir()
    L.record_log_ref(d, "older", when=1790000000)
    L.record_log_ref(d, "newer", when=1790000060)
    app = make_app(d)
    app.show_log_refs()
    title, body, refs = dialogs(app)[-1]
    check("log history lists the kept ones, newest first",
          (title, refs), (L.LOG_REFS_TITLE, ["newer", "older"]))
    check("each with its send time as the box shows it",
          [when for _ref, when in app.view.prompts[-1][1][2]],
          [time.strftime("%d %b %Y %H:%M", time.localtime(t))
           for t in (1790000060, 1790000000)])
    check("and names the player code for one not there",
          ("player code" in body, "abcd1234" in body), (True, True))
    check("the sent box says where the kept ones are",
          L.log_sent_text({"id": "r"}, "abcd1234"),
          lambda t: "kept under the log history link" in t)
    check("with no code yet it points at the launcher's title",
          L.log_sent_text({"id": "r"}, None),
          lambda t: "player code shown under the launcher's title" in t)
    real_record = L.record_log_ref

    def cannot(*a, **k):
        raise OSError("read-only")
    L.record_log_ref = cannot
    try:
        app = make_app(fresh_dir())
        app._on_log_sent({"id": "unkept"}, None, "p")
    finally:
        L.record_log_ref = real_record
    check("a reference that cannot be kept is still shown to copy",
          dialogs(app)[-1][2], ["unkept"])
    check("and the log says why", any("could not keep" in s
                                      for s in app.said), True)

    print("\n11. a seat held by another sign-in is said during the turn")
    held = "HTTP Error 403: Forbidden: QCplayer1 is " + L.SEAT_HELD
    app = make_app(fresh_dir())
    app._mp_state("send_failed", turn=38, civ="QCplayer1", error=held)
    seen = []
    while not app.msgs.empty():
        seen.append(app.msgs.get())
    # Fails if a refused upload in the middle of a turn is only logged, which
    # is how a refused player saw the status flicker and nothing else.
    check("a refused upload mid-turn puts up the held-seat box",
          [m[0] for m in seen], ["__mp_refused__"])
    check("which names the seat and this copy's player code",
          seen[0][1] if seen else "",
          lambda t: "'QCplayer1'" in t and "abcd1234" in t)
    check("and leaves the turn readout alone", app.mp_capture, None)
    app._mp_state("send_failed", turn=38, civ="QCplayer1", error=held)
    app._mp_state("lost", turn=38, civ="QCplayer1", error=held)
    check("once per followed galaxy, however many uploads are refused",
          app.msgs.empty(), True)
    app = make_app(fresh_dir())
    app._mp_state("send_failed", turn=38, civ="Ada",
                  error="HTTP Error 503: Service Unavailable")
    check("an upload that failed for another reason puts up nothing",
          app.msgs.empty(), True)
    import inspect
    src = inspect.getsource(L.Launcher.start_multiplayer)
    check("a new galaxy followed can say it again",
          "self.mp_seat_refused = False" in src, True)
    import player_turn
    src = inspect.getsource(player_turn.follow)
    check("the turn loop reports a refused interim upload to the launcher",
          'emit("send_failed"' in src, True)
finally:
    L.player_token = real_token
    L.install_uid = real_uid
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
