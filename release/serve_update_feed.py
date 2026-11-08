"""
serve_update_feed.py: a stand-in release page for trying the self-update.

Serves one release zip on 127.0.0.1 the way GitHub's latest-release API lists
it, for a test build made with

    build.ps1 -UpdateFeed http://127.0.0.1:8765/feed -UpdateDownloads http://127.0.0.1:8765/dl/

Run as:

    python release/serve_update_feed.py dist/CosmicSupremacy-Resurgence-v0.5.0.1.zip
    python release/serve_update_feed.py <zip> --bad-sha     : lists a wrong SHA-256

The version comes from the zip's name. Stop it with Ctrl+C.
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import re
import sys

ZIP_NAME = re.compile(r"^CosmicSupremacy-Resurgence-v(\d+(?:\.\d+){1,3})\.zip$")


def listing(name: str, version: str, size: int, sha: str, base: str) -> dict:
    tag = "v" + version
    return {"tag_name": tag, "draft": False, "prerelease": False,
            "body": f"Test release.\n\n    SHA-256  {sha}\n",
            "assets": [{"name": name, "size": size, "digest": "sha256:" + sha,
                        "browser_download_url": f"{base}/dl/{tag}/{name}"}]}


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("zip")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--bad-sha", action="store_true")
    args = ap.parse_args(argv)

    name = os.path.basename(args.zip)
    found = ZIP_NAME.match(name)
    if not found:
        print(f"{name} is not named like a release zip")
        return 2
    version = found.group(1)
    data = open(args.zip, "rb").read()
    sha = hashlib.sha256(data).hexdigest()
    if args.bad_sha:
        sha = sha[:-1] + ("0" if sha[-1] != "0" else "1")
    base = f"http://127.0.0.1:{args.port}"
    feed = json.dumps(listing(name, version, len(data), sha, base)).encode()
    files = {"/feed": feed, f"/dl/v{version}/{name}": data}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = files.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.end_headers()
            if body is not None:
                self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"v{version}, {len(data)} bytes, SHA-256 {sha}"
          + (" (wrong on purpose)" if args.bad_sha else ""))
    print(f"feed      {base}/feed")
    print(f"downloads {base}/dl/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
