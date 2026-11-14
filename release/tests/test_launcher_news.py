"""
The news line the Play page shows below Multiplayer, read from the website.
A local HTTP server stands in for cosmicresurgence.com, and a stand-in window
records what the page is sent. pywebview itself is not imported.
"""
import http.server
import json
import os
import re
import sys
import threading
import types

import pathlib

REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))

import launcher as L                                            # noqa: E402
sys.modules.setdefault("launcher", L)
import web_view as W                                            # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


class Site(http.server.BaseHTTPRequestHandler):
    body = b"{}"
    status = 200

    def do_GET(self):
        self.send_response(Site.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(Site.body)

    def log_message(self, *_a):
        pass


httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{httpd.server_address[1]}/launcher_news.json"

print("1. the line is read from the file")
check("the text is the line", L.news_line({"text": "Patch day Friday"}),
      "Patch day Friday")
check("an empty text is no line", L.news_line({"text": ""}), "")
check("no text is no line", L.news_line({}), "")
check("a text that is not a string is no line", L.news_line({"text": 5}), "")
check("a file that is not an object is no line", L.news_line(["x"]), "")
check("line breaks and runs of spaces become one space",
      L.news_line({"text": "  two\n lines  here "}), "two lines here")
long = "x" * (L.NEWS_LIMIT + 50)
check("a long line is cut to the limit", len(L.news_line({"text": long})),
      L.NEWS_LIMIT)
check("and says so", L.news_line({"text": long}).endswith("..."), True)

print("2. fetched from the site")
Site.body = json.dumps({"text": "Sandbox reset on turn 200"}).encode()
check("the site's line", L.fetch_news(URL), "Sandbox reset on turn 200")
Site.status = 404
try:
    L.fetch_news(URL)
    check("a missing file raises", False, True)
except Exception as exc:                                        # noqa: BLE001
    check("a missing file raises", type(exc).__name__, "HTTPError")
Site.status = 200
check("a checkout may name a stand-in",
      (os.environ.__setitem__(L.NEWS_URL_ENV, URL), L.news_url())[1], URL)
del os.environ[L.NEWS_URL_ENV]
check("otherwise the website's file", L.news_url(), L.NEWS_URL)

print("3. what the page is sent")


class Window:
    def __init__(self):
        self.sent = []

    def run_js(self, script):
        m = re.fullmatch(r"window\.ui\.(\w+)\((.*)\)", script, re.S)
        self.sent.append((m.group(1), json.loads(m.group(2))))


view = W.WebView({"product": "CS"})
view.window = Window()
said = []
app = types.SimpleNamespace(view=view, say=said.append)
L.Launcher._on_news(app, "Patch day Friday", None)
check("a line is sent as news", view.window.sent, [("set", {"news": "Patch day Friday"})])
L.Launcher._on_news(app, "Patch day Friday", None)
check("the same line is not sent again", len(view.window.sent), 1)
L.Launcher._on_news(app, None, "URLError: offline")
check("a failed read keeps the last line", len(view.window.sent), 1)
check("and is logged", said, ["news: not read (URLError: offline)"])
L.Launcher._on_news(app, "", None)
check("an empty line clears it", view.window.sent[-1], ("set", {"news": ""}))

print("4. the page draws it as text, under Multiplayer")
js = pathlib.Path(REPO, "release", "webui", "app.js").read_text(encoding="utf-8")
check("the line is drawn with textContent (el)",
      'el("div", "news", newsLine)' in js, True)
check("inside the multiplayer row", '[data-mode="multiplayer"]' in js, True)

httpd.shutdown()
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
