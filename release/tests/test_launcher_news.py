"""
The news panel the Play page shows under the modes, read from the website.
A local HTTP server stands in for cosmicresurgence.com, and a stand-in window
records what the page is sent. pywebview itself is not imported.
"""
import base64
import http.server
import json
import os
import pathlib
import re
import sys
import threading
import types

REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))

import launcher as L                                            # noqa: E402
sys.modules.setdefault("launcher", L)
import web_view as W                                            # noqa: E402

fails = []
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {str(got)[:90]!r}")
    if not ok:
        fails.append(label)


class Site(http.server.BaseHTTPRequestHandler):
    files = {}

    def do_GET(self):
        body = Site.files.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.end_headers()
        self.wfile.write(body or b"")

    def log_message(self, *_a):
        pass


httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{httpd.server_address[1]}"
URL = BASE + "/launcher_news.json"


def serve(doc, **files):
    Site.files = {"/launcher_news.json": json.dumps(doc).encode(), **files}


print("1. the item is read from the file")
item = L.news_item({"title": "Patch day", "date": "2026-11-14",
                    "text": "  two\n lines  here ", "image": "images/a.png"})
check("title, date as words, one-line text and the picture path", item,
      {"title": "Patch day", "date": "14 November 2026", "text": "two lines here",
       "image": "images/a.png"})
check("a title alone is an item", L.news_item({"title": "T"})["title"], "T")
check("no title and no text is no item", L.news_item({"date": "2026-11-14"}), None)
check("a file that is not an object is no item", L.news_item(["x"]), None)
check("a date that is not YYYY-MM-DD is left out",
      L.news_item({"title": "T", "date": "Friday"})["date"], "")
check("a text that is not a string is left out",
      L.news_item({"title": "T", "text": 5})["text"], "")
long = L.news_item({"title": "x" * 99, "text": "y" * (L.NEWS_TEXT_LIMIT + 9)})
check("a long title is cut", (len(long["title"]), long["title"][-3:]),
      (L.NEWS_TITLE_LIMIT, "..."))
check("a long text is cut", len(long["text"]), L.NEWS_TEXT_LIMIT)

print("2. fetched from the site, the picture inlined")
serve({"title": "T", "text": "x", "image": "images/a.png"}, **{"/images/a.png": PNG})
got = L.fetch_news(URL)
check("the picture is a data URI of its bytes", got["image"],
      "data:image/png;base64," + base64.b64encode(PNG).decode())
serve({"title": "T", "image": "images/a.txt"}, **{"/images/a.txt": b"not a picture"})
check("a file that is not a picture is left out", L.fetch_news(URL)["image"], "")
serve({"title": "T", "image": "images/big.png"},
      **{"/images/big.png": PNG + b"\0" * L.NEWS_IMAGE_LIMIT})
check("a picture over the limit is left out", L.fetch_news(URL)["image"], "")
serve({"title": "T", "image": "images/gone.png"})
check("a missing picture is left out, the item kept",
      (L.fetch_news(URL)["image"], L.fetch_news(URL)["title"]), ("", "T"))
serve({"title": "T", "image": "https://elsewhere.example/a.png"})
check("a picture on another site is never fetched", L.fetch_news(URL)["image"], "")
serve({"date": "2026-11-14"})
check("a file with no title or text shows nothing", L.fetch_news(URL), None)
Site.files = {}
try:
    L.fetch_news(URL)
    check("a missing news file raises", False, True)
except Exception as exc:                                        # noqa: BLE001
    check("a missing news file raises", type(exc).__name__, "HTTPError")
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
one = {"title": "T", "date": "14 November 2026", "text": "x", "image": ""}
L.Launcher._on_news(app, one, None)
check("an item is sent as news", view.window.sent, [("set", {"news": one})])
L.Launcher._on_news(app, dict(one), None)
check("the same item is not sent again", len(view.window.sent), 1)
L.Launcher._on_news(app, None, "URLError: offline")
check("a failed read keeps the last item", len(view.window.sent), 1)
check("and is logged", said, ["news: not read (URLError: offline)"])
L.Launcher._on_news(app, None, None)
check("no item clears the panel", view.window.sent[-1], ("set", {"news": None}))

print("4. the page draws it as text")
js = pathlib.Path(REPO, "release", "webui", "app.js").read_text(encoding="utf-8")
check("the title, date and text are drawn with textContent (el)",
      all(f'el("div", "{c}"' in js for c in ("ntitle", "ndate", "ntext")), True)
check("a picture is shown only as a data URI",
      'newsItem.image.startsWith("data:image/")' in js, True)

httpd.shutdown()
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
