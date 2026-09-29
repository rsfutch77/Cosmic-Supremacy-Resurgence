"""
Two things the Galaxies page reads back from a galaxy: whether a player seated
without clicking Join has played their turn (J6), and what the galaxy decided
about a join that was asked for (J3's refused half).

    server\\.venv\\Scripts\\python.exe release\\tests\\test_join_answers.py

Headless. Sections 1 to 6 run offline against galaxies made in a temporary
folder, with the answer written by the store method the worker writes it with.
Section 7 runs only when FIRESTORE_EMULATOR_HOST is set: it puts the relay
behind a local port, lodges a join through it as an anonymous emulator user,
has `joins.apply` and `joins.commit` refuse it the way the referee would, and
reads the refusal back through the launcher's own code. Start the emulators with

    python functions/emulators.py --only firestore,storage,auth

and set the three variables it prints.

The launcher object is not built. The methods under test are called on a stub
that carries only the attributes they read, so nothing reaches Tk or a port.
"""
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import galaxy_directory                                         # noqa: E402
import launcher as L                                            # noqa: E402
import turn_store                                               # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


dirs = []


def fresh_dir():
    d = tempfile.mkdtemp(prefix="answers_")
    dirs.append(d)
    return d


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def make_galaxy(root, civs, turn=4):
    os.makedirs(os.path.join(root, "turns"), exist_ok=True)
    write_json(os.path.join(root, "state.json"),
               {"turn": turn, "turn_seconds": 1800, "civs": list(civs),
                "deadline": time.time() + 900, "hash": "x"})
    return root


class Label:
    """Enough of a tk label for the page's status and hint lines."""

    def __init__(self):
        self.text, self.fg, self.mapped = "", None, False

    def configure(self, text=None, fg=None, **kw):
        if text is not None:
            self.text = text
        if fg is not None:
            self.fg = fg

    def cget(self, key):
        return self.text

    def winfo_ismapped(self):
        return self.mapped

    def pack(self, **kw):
        self.mapped = True

    def pack_forget(self):
        self.mapped = False


class Page:
    """The Galaxies page's state, without the window it hangs off."""

    def __init__(self, data_dir, games_dir, player):
        self.data_dir, self.games_dir, self.player = data_dir, games_dir, player
        self.own_store = None
        self.games_busy = False
        self.games_status, self.games_hint = Label(), Label()
        self.games_rows, self.games_extra, self.games_err = None, {}, None
        self.games_recs = {}
        self.msgs = queue.Queue()
        self.said = []
        self.drawn = 0

    def say(self, line):
        self.said.append(line)

    def _draw_galaxies(self):
        self.drawn += 1
        self.games_recs = L.load_joined(self.data_dir)

    def _submitted_state(self, rows, recs, player):
        return L.Launcher._submitted_state(self, rows, recs, player)

    def _join_answers(self, rows, recs):
        return L.Launcher._join_answers(self, rows, recs)

    def _reclaims(self, rows, recs):
        return L.Launcher._reclaims(self, rows, recs)

    def refresh(self):
        """One Games refresh, run to the end: listed, posted, taken in."""
        L.Launcher.refresh_games(self)
        kind, rows, extra, err = self.msgs.get(timeout=30)
        L.Launcher._on_games(self, rows, extra, err)
        return {g.id: g for g in rows}, extra

    def view(self):
        return L.Launcher._galaxy_view(self)

    def hint(self, rows):
        L.Launcher._show_hint(self, list(rows), self.view())
        return self.games_hint.text if self.games_hint.mapped else ""


print("1. a player seated by first-use sees whether they have played (J6)")
root = fresh_dir()
h3 = make_galaxy(os.path.join(root, "h3check"), ["DemoPlayer", "Neighbor"])
make_galaxy(os.path.join(root, "quiet"), ["Bob"])
opened = turn_store.open_store(h3)
opened.submit("Neighbor", 4, b"orders")
data = fresh_dir()
check("this install has never clicked Join", L.load_joined(data), {})
page = Page(data, root, "Neighbor")
rows, extra = page.refresh()
check("the directory says the player is in h3check", rows["h3check"].joined,
      True)
# Fails when the refresh asks only for galaxies with a joined record, which
# is what it did before J6: this install has none.
check("and the refresh asked it whether they have submitted",
      extra.get("submitted"), {"h3check": True})
view = page.view()
you = L.galaxy_column("you")
check("so the row says the turn is played", you.text(rows["h3check"], view),
      "turn played")
check("a galaxy they are not in is not asked about",
      "quiet" in extra.get("submitted", {}), False)
check("nothing was written into joined.json to get there",
      os.path.exists(L.joined_path(data)), False)

other = Page(fresh_dir(), root, "DemoPlayer")
rows, extra = other.refresh()
check("a seated player who has not played reads their turn as waiting",
      you.text(rows["h3check"], other.view()), "your turn")

print("\n2. a joined record still names the seat it was asked for under")
renamed = fresh_dir()
L.save_joined(renamed, L.joined_record(rows["h3check"], "Neighbor", None,
                                       root))
page = Page(renamed, root, "DemoPlayer")
_rows, extra = page.refresh()
check("the record's name is the one asked about, not the current one",
      extra.get("submitted"), {"h3check": True})

print("\n3. whether an answer belongs to the request a record holds")
rec = {"requested_turn": 6, "name": "Ada"}
refused7 = {"outcome": "refused", "reason": "no room", "turn": 7}
check("an answer seating into a later turn is this request's",
      L.answer_for(rec, refused7), refused7)
# Fails if the turn were not compared: the answer to an earlier request stays
# filed under the same key, and a player who asked again would be shown it.
check("an answer at the turn the request was lodged is an older request's",
      L.answer_for(rec, dict(refused7, turn=6)), None)
check("and one before it too", L.answer_for(rec, dict(refused7, turn=3)),
      None)
check("an answer with no outcome is not an answer",
      L.answer_for(rec, {"reason": "x", "turn": 9}), None)
check("nor is nothing", L.answer_for(rec, None), None)
check("a record with no turn takes the answer as it is",
      L.answer_for({"name": "Ada"}, refused7), refused7)
check("the record keeps the turn read at the moment of joining",
      L.joined_record(rows["h3check"], "Ada", None, root,
                      turn=9)["requested_turn"], 9)
check("and falls back to the listing's when there is none",
      L.joined_record(rows["h3check"], "Ada", None, root)["requested_turn"],
      rows["h3check"].turn)

print("\n4. a refused join is told so, and why (J3)")
root = fresh_dir()
sandbox = make_galaxy(os.path.join(root, "sandbox"), ["Bob", "Carol"], turn=4)
make_galaxy(os.path.join(root, "mine"), ["Ada"], turn=2)
directory = L.open_galaxy_directory(root)
listed = {g.id: g for g in directory.galaxies(player="Ada")}
data = fresh_dir()
store = turn_store.open_store(listed["sandbox"].store)
L.send_join_request(store, L.join_request("Ada", None, "0.1.5", turn=4))
L.save_joined(data, L.joined_record(listed["sandbox"], "Ada", None, root,
                                    turn=4))
page = Page(data, root, "Ada")

asked = []
real_open = L.open_player_store


def counting_open(ts, spec, token=None):
    asked.append(spec)
    return real_open(ts, spec, token)


L.open_player_store = counting_open
try:
    rows, extra = page.refresh()
finally:
    L.open_player_store = real_open
check("while the request waits there is no answer", extra.get("answers"), {})
check("and the row says so", you.text(rows["sandbox"], page.view()),
      "joining next turn")
check("with no button, because there is nothing to press yet",
      L.row_action(rows["sandbox"], page.view().recs), None)
# Fails if the refresh asked every row, or asked a galaxy the player is in.
check("one galaxy was asked about the join, the one with the request",
      [s for s in asked if s == listed["sandbox"].store], [listed["sandbox"]
                                                           .store])
check("the galaxy the player is in was asked only whether they played",
      asked.count(listed["mine"].store), 1)

reason = ("This galaxy had no room to put a new empire in: no free planet. "
          "Nothing was changed. Try another galaxy, or ask whoever runs "
          "this one.")
key = turn_store.join_key({"uid": None, "name": "Ada"})
store.answer_join(key, {"key": key, "name": "Ada", "outcome": "refused",
                        "reason": reason, "turn": 5, "requested_turn": 4})
rows, extra = page.refresh()
check("the refresh reads the refusal", sorted(extra.get("answers") or {}),
      ["sandbox"])
kept = L.load_joined(data)["sandbox"].get("answer") or {}
check("and keeps it in the joined record", kept.get("outcome"), "refused")
view = page.view()
# Fails without the refused half: the row said "joining next turn" forever.
check("the row says the join was refused", you.text(rows["sandbox"], view),
      "join refused")
check("and offers Reason", L.row_action(rows["sandbox"], view.recs), L.REASON)
check("which the button is labelled with", L.ACTION_TEXT[L.REASON], "Reason")
told = L.refusal_text(rows["sandbox"], L.load_joined(data)["sandbox"])
check("Reason tells them they were refused",
      "was refused" in told, True)
check("and why, in the worker's own words", reason in told, True)
check("and what they can do about it",
      "Ask again" in told and "Clear" in told, True)
check("the line under the table says it as well",
      page.hint(rows.values()), lambda t: "refused" in t and "Reason" in t)
check("and it is still a row that is this player's business",
      L.pending_join(rows["sandbox"], view.recs), True)

asked.clear()
L.open_player_store = counting_open
try:
    rows, extra = page.refresh()
finally:
    L.open_player_store = real_open
# Fails if an answered request were asked about again every refresh, which
# is a read per refused player per poll for as long as they leave it.
check("an answered request is not asked about again",
      listed["sandbox"].store in asked, False)
check("and the refusal is still shown", you.text(rows["sandbox"], page.view()),
      "join refused")

print("\n5. asking again, and clearing")
# Ask again is Join again: a new request and a record written over the old.
store.update_state({"turn": 5})
L.send_join_request(store, L.join_request("Ada", None, "0.1.5", turn=5))
L.save_joined(data, L.joined_record(rows["sandbox"], "Ada", None, root,
                                    turn=5))
rows, extra = page.refresh()
# Fails if an old answer were matched to a new request: the refusal for turn 5
# is still filed under the same key.
check("a request made again is not answered by the old refusal",
      extra.get("answers"), {})
check("so the row is waiting again", you.text(rows["sandbox"], page.view()),
      "joining next turn")
check("with the waiting line rather than the refused one",
      page.hint(rows.values()), lambda t: "refused" not in t)

store.answer_join(key, {"key": key, "name": "Ada", "outcome": "refused",
                        "reason": "Another reason.", "turn": 6})
rows, _extra = page.refresh()
check("the second refusal is read when it comes",
      (L.load_joined(data)["sandbox"].get("answer") or {}).get("reason"),
      "Another reason.")
L.clear_joined(data, "sandbox")
page._draw_galaxies()
view = page.view()
check("Clear takes the row back to View",
      L.row_action(rows["sandbox"], view.recs), L.VIEW)
check("and the You cell empty", you.text(rows["sandbox"], view), "")

print("\n6. an answer is kept only in the record it answers")
data = fresh_dir()
g = listed["sandbox"]
L.save_joined(data, L.joined_record(g, "Ada", None, root, now=100.0, turn=4))
answer = {"outcome": "refused", "reason": "r", "turn": 5}
L.record_join_answers(data, {"sandbox": (99.0, answer)})
check("an answer for a request since replaced is dropped",
      "answer" in L.load_joined(data)["sandbox"], False)
L.record_join_answers(data, {"gone": (100.0, answer)})
check("and one for a galaxy since cleared writes nothing new",
      sorted(L.load_joined(data)), ["sandbox"])
L.record_join_answers(data, {"sandbox": (100.0, answer)})
check("the answer to this request is kept",
      L.load_joined(data)["sandbox"]["answer"], answer)
granted = galaxy_directory.Galaxy("sandbox", "Sandbox", "open", 5, None, 3,
                                  True, g.store)
recs = L.load_joined(data)
check("a galaxy the player is in is never asked for an answer",
      L.awaiting_answer(granted, recs), False)
check("and shows no refusal, whatever the record holds",
      L.join_refusal(granted, recs), None)
calls = []
L.read_join_answers([granted, g], {"sandbox": dict(recs["sandbox"],
                                                   answer=None)},
                    lambda gg, rr: calls.append(gg.joined))
check("read_join_answers asks only the row not yet joined", calls, [False])


def run_emulator():
    """The refused half on the relay, the worker and the launcher together."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import urllib.parse
    import urllib.request
    for d in (os.path.join(REPO, "server", "dev_tools"),
              os.path.join(REPO, "functions")):
        if d not in sys.path:
            sys.path.insert(0, d)
    project = os.environ.get("CS_RELAY_PROJECT") or "demo-cs-resurgence"
    bucket = os.environ.get("CS_RELAY_BUCKET") or \
        f"{project}.firebasestorage.app"
    os.environ["CS_RELAY_PROJECT"] = project
    os.environ["CS_RELAY_BUCKET"] = bucket
    auth = os.environ.get("FIREBASE_AUTH_EMULATOR_HOST") or "127.0.0.1:9099"
    import joins
    import relay
    from firebase_store import FirebaseTurnStore

    class Relay(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _run(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            status, headers, out = relay.handle(
                self.command, urllib.parse.urlparse(self.path).path,
                dict(self.headers), body)
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        do_GET = do_POST = _run

    req = urllib.request.Request(
        f"http://{auth}/identitytoolkit.googleapis.com/v1/"
        f"accounts:signUp?key=fake-api-key",
        data=json.dumps({"returnSecureToken": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        signed = json.loads(r.read())
    uid, token = signed["localId"], signed["idToken"]

    galaxy = f"answers_{int(time.time())}"
    referee = FirebaseTurnStore(project, galaxy, bucket=bucket)
    import struct
    import save_parser as sp

    def section(tag, payload):
        return tag + struct.pack("<I", len(payload) & sp.SIZE_MASK) + payload

    # The smallest blob `turn_of` reads a turn out of, which is all a start
    # needs. The worker refuses the request below before it reads the blob.
    blob0 = section(b"SAVE", section(b"GLOB", struct.pack("<I", 0) + b"." * 60))
    referee.start(blob0, ["DemoPlayer"], turn_seconds=1800)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Relay)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/{galaxy}"
    saved = (L.store_wants_token, L.player_token)
    L.store_wants_token = lambda cfg: True
    L.player_token = lambda d: (lambda: token)
    try:
        row = galaxy_directory.Galaxy(galaxy, "Relay", "open", 0, None, 1,
                                      False, base)
        data = fresh_dir()
        player = turn_store.HttpTurnStore(base, token=lambda: token)
        # A name the worker refuses before it looks at the blob, so the
        # refusal is the worker's real sentence without a real galaxy.
        name = "A" * (joins.NAME_LIMIT + 1)
        L.send_join_request(player, L.join_request(name, uid, "0.1.5",
                                                   turn=0))
        L.save_joined(data, L.joined_record(row, name, uid, base, turn=0))
        page = Page(data, None, "Somebody")
        recs = L.load_joined(data)
        check("relay: while the request waits there is no answer",
              L.Launcher._join_answers(page, [row], recs), {})
        blob = referee.turn_blob(0)
        _blob, outcomes = joins.apply(referee, 0, 1, blob, log=lambda *a: None)
        joins.commit(referee, 0, 1, outcomes, log=lambda *a: None)
        check("relay: the worker refused it",
              [o["outcome"] for o in outcomes], ["refused"])
        got = L.Launcher._join_answers(page, [row], L.load_joined(data))
        answer = (got.get(galaxy) or (None, {}))[1]
        # Fails if the launcher asked under any key but the uid the relay
        # filed the request under, or if the route needed a seat.
        check("relay: the launcher reads the refusal back",
              answer.get("outcome"), "refused")
        check("relay: with the worker's reason",
              answer.get("reason"), outcomes[0]["reason"])
        L.record_join_answers(data, got)
        recs = L.load_joined(data)
        check("relay: and the row says refused",
              L.galaxy_column("you").text(row, L.View(recs=recs)),
              "join refused")
        check("relay: with the reason under Reason",
              outcomes[0]["reason"] in L.refusal_text(row, recs[galaxy]),
              True)
    finally:
        L.store_wants_token, L.player_token = saved
        httpd.shutdown()
        httpd.server_close()
        referee.delete_everything()


print("\n7. the same refusal through the relay, on the emulator")
if os.environ.get("FIRESTORE_EMULATOR_HOST"):
    run_emulator()
else:
    print("  SKIPPED: no FIRESTORE_EMULATOR_HOST")

for d in dirs:
    shutil.rmtree(d, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
