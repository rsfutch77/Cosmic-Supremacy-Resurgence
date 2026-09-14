#!/usr/bin/env python3
"""Preview public/ the way Firebase Hosting will serve it.

Python's built-in server returns a bare text 404, which is exactly the thing
this experiment is trying to check. This serves public/404.html instead, with
a real 404 status, so the staged site can be reviewed before firebase-tools or
any Firebase account exists.

    python serve.py            # http://localhost:8080
    python serve.py --port 9000
"""

import argparse
import http.server
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "public")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def send_error(self, code, message=None, explain=None):
        if code != 404:
            return super().send_error(code, message, explain)
        page = os.path.join(ROOT, "404.html")
        if not os.path.isfile(page):
            return super().send_error(code, message, explain)
        with open(page, "rb") as fh:
            body = fh.read()
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    if not os.path.isdir(ROOT):
        sys.exit("public/ not found - run stage.py first")

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"serving {ROOT}")
    print(f"  http://localhost:{args.port}/           home page")
    print(f"  http://localhost:{args.port}/forum/     404 page")
    print("Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
