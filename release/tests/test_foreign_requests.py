"""
cs_server refuses requests from a foreign web page and serves the client's own.

A page the player has open can reach a loopback port, so the server answers 403
to anything carrying a browser's marks of another origin, and keeps a governor id
inside DATA_DIR. The client's requests, which carry none of those marks, are
served as before.
"""
import http.client
import os
import shutil
import sys
import tempfile
import threading
import urllib.parse

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])

DATA = tempfile.mkdtemp(prefix="foreigntest_")
os.environ["CS_DATA_DIR"] = os.path.join(DATA, "data")
os.environ["CSPORT"] = "8898"
os.environ.pop("CSHOST", None)
sys.path.insert(0, os.path.join(REPO, "server"))

import http.server
import cs_server

srv = http.server.HTTPServer(("127.0.0.1", 8898), cs_server.CSHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got:  {got!r}")
        print(f"         want: {want!r}")
        fails.append(label)


def request(method, action, headers=None, **fields):
    body = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in fields.items())
    path = f"/clientinterface.php?action={action}"
    if method == "GET" and body:
        path += "&" + body
        body = ""
    c = http.client.HTTPConnection("127.0.0.1", 8898, timeout=5)
    c.putrequest(method, path, skip_host=True)
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    if method == "POST":
        c.putheader("Content-Type", "application/x-www-form-urlencoded")
        c.putheader("Content-Length", str(len(body)))
    c.endheaders(body.encode("latin-1") if body else None)
    r = c.getresponse()
    out = r.status, r.read().decode("latin-1")
    c.close()
    return out


CLIENT = {"Host": "localhost:8888"}

print("1. the client's own requests are served")
check("POST with a loopback Host", request("POST", "testconnection", CLIENT)[0], 200)
check("POST with no Host", request("POST", "testconnection")[0], 200)
check("POST with Host 127.0.0.1", request("POST", "testconnection", {"Host": "127.0.0.1:8888"})[0], 200)
check("POST with Host [::1]", request("POST", "testconnection", {"Host": "[::1]:8888"})[0], 200)
check("savegov with a numeric govid", request("POST", "savegov", CLIENT, govid=3, data="x"), (200, "DONE"))
check("same-origin page", request("GET", "testconnection",
      {"Host": "localhost:8888", "Origin": "http://localhost:8888",
       "Sec-Fetch-Site": "same-origin"})[0], 200)
check("address typed into the browser", request("GET", "testconnection",
      {"Host": "localhost:8888", "Sec-Fetch-Site": "none"})[0], 200)

print("\n2. a foreign page is refused")
check("rebound Host", request("POST", "savegame", {"Host": "evil.example:8888"}, data="x"),
      (403, "FORBIDDEN"))
check("foreign Origin", request("POST", "savegame",
      {"Host": "localhost:8888", "Origin": "https://evil.example"}, data="x"), (403, "FORBIDDEN"))
check("Origin null", request("POST", "savegame",
      {"Host": "localhost:8888", "Origin": "null"}, data="x"), (403, "FORBIDDEN"))
check("cross-site GET, as from an img tag", request("GET", "savegov",
      {"Host": "localhost:8888", "Sec-Fetch-Site": "cross-site"}, govid=1, data="x"),
      (403, "FORBIDDEN"))
check("same-site GET", request("GET", "testconnection",
      {"Host": "localhost:8888", "Sec-Fetch-Site": "same-site"})[0], 403)
check("nothing saved by a refused request",
      os.path.exists(os.path.join(DATA, "data", "saves")), False)

print("\n3. a governor id stays inside DATA_DIR")
check("savegov with a path in govid", request("POST", "savegov", CLIENT,
      govid=r"x\..\..\escaped", data="x"), (400, "ERROR"))
check("nothing written outside DATA_DIR", os.path.exists(os.path.join(DATA, "escaped.dat")), False)
check("loadgov with a path in govid", request("POST", "loadgov", CLIENT, govid="../x")[0], 400)

srv.shutdown()
shutil.rmtree(DATA, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
