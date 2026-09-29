"""
One-click update (L3): a launcher below a galaxy's minimum build reaches the
current release from the refusal itself, and keeps its identity on the way.

    server\\.venv\\Scripts\\python.exe release\\tests\\test_update.py

Headless and offline. GitHub is a local HTTP server on a free port serving a
release listing and a zip built here in the layout `build.ps1` produces. The
installs are temporary folders. Nothing is started: `os.startfile` is
recorded rather than called.
"""
import hashlib
import http.server
import inspect
import io
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import types
import zipfile

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
    d = tempfile.mkdtemp(prefix="update_")
    dirs.append(d)
    return d


NEW = "CosmicSupremacy-Resurgence-v0.2.0"


def release_zip(top=NEW, extra=None, launcher=True):
    """A zip laid out as build.ps1 lays one out."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if launcher:
            zf.writestr(f"{top}/{L.LAUNCHER_EXE}", b"MZ new launcher")
        zf.writestr(f"{top}/game/CosmicSupremacy.exe", b"MZ game")
        zf.writestr(f"{top}/README.txt", b"readme")
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


class GitHub(http.server.BaseHTTPRequestHandler):
    """`releases/latest` and its one asset."""

    zip_bytes = release_zip()
    notes = None
    asked = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        GitHub.asked.append(self.path)
        if self.path == "/latest":
            sha = hashlib.sha256(GitHub.zip_bytes).hexdigest()
            doc = {"tag_name": "v0.2.0",
                   "body": GitHub.notes if GitHub.notes is not None else
                   f"Notes.\n\n    SHA-256  {sha}\n",
                   "assets": [{"name": f"{NEW}.zip",
                               "size": len(GitHub.zip_bytes),
                               "browser_download_url":
                               f"{BASE}/download/{NEW}.zip"}]}
            out = json.dumps(doc).encode()
        elif self.path.startswith("/download/"):
            out = GitHub.zip_bytes
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), GitHub)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{httpd.server_address[1]}"
API = f"{BASE}/latest"


def old_install():
    """(install dir, data dir) of a v0.1.5 install with a player in it."""
    parent = fresh_dir()
    install = os.path.join(parent, "CosmicSupremacy-Resurgence-v0.1.5")
    data = os.path.join(install, "data")
    os.makedirs(os.path.join(data, "saves"))
    files = {L.IDENTITY_FILE: '{"player": "Ada"}',
             "fb_identity.json": '{"uid": "abcdefgh1234", "refresh": "r"}',
             L.JOINED_FILE: '{"sandbox": {}}',
             L.MP_CONFIG: '{"directory": "https://relay.example"}',
             L.LOG_NAME: "log lines\n",
             os.path.join("saves", "x.b64"): "save"}
    for name, text in files.items():
        with open(os.path.join(data, name), "w") as f:
            f.write(text)
    with open(os.path.join(install, L.LAUNCHER_EXE), "wb") as f:
        f.write(b"MZ old launcher")
    return install, data


try:
    print("1. reading the release")
    sha = "ab" * 32
    info = L.release_info({"tag_name": "v0.2.0",
                           "body": f"x\n    SHA-256  {sha}\n",
                           "assets": [{"name": "notes.txt"},
                                      {"name": f"{NEW}.zip", "size": 5,
                                       "browser_download_url": "https://x/z"}]})
    check("the zip asset, not the first asset",
          (info["name"], info["url"], info["size"]),
          (f"{NEW}.zip", "https://x/z", 5))
    check("the build from the tag", info["build"], "0.2.0")
    check("the digest from the notes", info["sha256"], sha)
    other = "cd" * 32
    info = L.release_info({"tag_name": "v0.2.0",
                           "body": f"old.zip {other}\n\n{NEW}.zip {sha}\n",
                           "assets": [{"name": f"{NEW}.zip"}]})
    # Fails if the first digest in the notes were taken whatever it named.
    check("of several, the one beside the zip's name", info["sha256"], sha)
    check("notes with none give none",
          L.release_info({"tag_name": "v1", "body": "no digest",
                          "assets": [{"name": "a.zip"}]})["sha256"], None)
    try:
        L.release_info({"tag_name": "v0.2.0", "assets": []})
        check("a release with no zip is refused", False, True)
    except L.UpdateRefused as exc:
        check("a release with no zip is refused", "no download" in str(exc),
              True)
    check("fetched from the listing", L.fetch_release(API)["build"], "0.2.0")

    print("\n2. the download is checked")
    dest = fresh_dir()
    info = L.fetch_release(API)
    path = L.download_release(info, dest)
    check("it lands whole", open(path, "rb").read(), GitHub.zip_bytes)
    for label, bad, want in (
            ("a digest that does not match", dict(info, sha256="00" * 32),
             "checksum"),
            ("a size that does not match", dict(info, size=info["size"] + 1),
             "stopped at"),
            ("a download over the cap", dict(info, name="big.zip"), "larger")):
        d = fresh_dir()
        try:
            L.download_release(bad, d, cap=(10 if "cap" in label
                                            else L.UPDATE_MAX_BYTES))
            check(f"{label} is refused", False, True)
        except L.UpdateRefused as exc:
            check(f"{label} is refused", want in str(exc), True)
        check(f"and leaves nothing behind", os.listdir(d), [])

    print("\n3. the new install keeps the player's identity")
    install, data = old_install()
    target = L.install_release(path, install, data, url=info["url"])
    parent = os.path.dirname(install)
    check("it is unpacked beside the old one", target,
          os.path.join(parent, NEW))
    check("the old one is left alone",
          open(os.path.join(install, L.LAUNCHER_EXE), "rb").read(),
          b"MZ old launcher")
    new_data = os.path.join(target, "data")
    # Fails if fb_identity.json were left behind: the new folder would be a
    # new sign-in and the relay would refuse the player their own seat (J4).
    for name in L.UPDATE_CARRIED:
        check(f"{name} is carried",
              open(os.path.join(new_data, name)).read(),
              open(os.path.join(data, name)).read())
    check("the log and the saves are not", sorted(os.listdir(new_data)),
          sorted(L.UPDATE_CARRIED))
    check("no unpacking folder is left", sorted(os.listdir(parent)),
          sorted([os.path.basename(install), NEW]))
    try:
        with open(os.path.join(target, L.LAUNCHER_EXE)
                  + ":Zone.Identifier") as f:
            mark = f.read()
    except OSError:
        mark = None
    # Fails if the launcher wrote the executable without the mark a browser
    # download carries, which would skip SmartScreen's check of it.
    check("the executables carry the download mark",
          mark, lambda m: m is not None and "ZoneId=3" in m)
    try:
        L.install_release(path, install, data)
        check("a second unpack over the first is refused", False, True)
    except L.UpdateRefused as exc:
        check("a second unpack over the first is refused",
              "already beside" in str(exc), True)

    for label, blob, want in (
            ("a zip naming a file outside its folder",
             release_zip(extra={f"{NEW}/../evil.txt": b"x"}), "outside"),
            ("a zip with no launcher", release_zip(launcher=False),
             "no launcher"),
            ("a zip of two folders",
             release_zip(extra={"Other/x.txt": b"x"}), "laid out")):
        install, data = old_install()
        z = os.path.join(fresh_dir(), "r.zip")
        with open(z, "wb") as f:
            f.write(blob)
        try:
            L.install_release(z, install, data)
            check(f"{label} is refused", False, True)
        except L.UpdateRefused as exc:
            check(f"{label} is refused", want in str(exc), True)
        check("and nothing is unpacked",
              os.listdir(os.path.dirname(install)),
              [os.path.basename(install)])

    print("\n4. from the refusal to the new build, with no human")
    install, data = old_install()
    started, destroyed = [], []

    app = types.SimpleNamespace(
        data_dir=data, updating=False, msgs=queue.Queue(), said=[],
        warned=[], dialogs=[], choose=["update", "start"], client_exes=[],
        servers=[types.SimpleNamespace(shutdown=lambda: destroyed.append(
            "server"))])
    app.say = app.said.append
    app.say_threadsafe = app.said.append
    app.warn = app.warned.append
    app.stop_multiplayer = lambda why="": destroyed.append("loop")
    app.stop_ai = lambda why="": None
    app.root = types.SimpleNamespace(destroy=lambda: destroyed.append("root"))

    def dialog(title, body, choices):
        app.dialogs.append((title, body, [c[0] for c in choices]))
        return app.choose.pop(0) if app.choose else None
    app._choice_dialog = dialog
    for name in ("_offer_update", "_on_update", "_start_install"):
        setattr(app, name, getattr(L.Launcher, name).__get__(app))

    saved = (L.update_possible, L.app_dir, L.build_id, L.fetch_release,
             L.os.startfile if hasattr(L.os, "startfile") else None)
    L.update_possible = lambda: True
    L.app_dir = lambda: install
    L.build_id = lambda: "0.1.5"
    L.fetch_release = lambda api=API: saved[3](API)
    L.os.startfile = lambda p: (started.append(p), destroyed.append("start"))
    try:
        problem = L.version_problem("0.1.5", {"min_build": "0.2.0"})
        app._offer_update(problem)
        msg = app.msgs.get(timeout=20)
        check("the update runs on its own thread and reports back",
              msg[0], "__update__")
        app._on_update(*msg[1:])
        first, ready = app.dialogs[0], app.dialogs[1]
        check("the refusal offers Update", (first[0], first[2]),
              ("Update needed", ["Update", "Close"]))
        check("and still says what was refused",
              "needs build 0.2.0 or newer" in first[1], True)
        check("the ready box says where and what came with it",
              (ready[0], NEW in ready[1], "Your name and your seats"
               in ready[1]), ("Update ready", True, True))
        check("with Start, Show the folder and Later", ready[2],
              ["Start", "Show the folder", "Later"])
        new_exe = os.path.join(os.path.dirname(install), NEW, L.LAUNCHER_EXE)
        check("Start opens the new launcher", started, [new_exe])
        # Fails if the new launcher were started while this one still held
        # the port, which the new one would refuse.
        check("after this one's loop and server stop, and then it closes",
              destroyed, ["loop", "server", "start", "root"])
        check("and the new one has this install's sign-in",
              open(os.path.join(os.path.dirname(new_exe), "data",
                                "fb_identity.json")).read(),
              open(os.path.join(data, "fb_identity.json")).read())
        check("nothing was put to the player as a warning", app.warned, [])

        print("\n   when it cannot")
        app.dialogs.clear()
        app.choose = ["update"]
        L.build_id = lambda: "0.2.0+dev"
        app._offer_update(problem)
        app._on_update(*app.msgs.get(timeout=20)[1:])
        check("a release no newer than this build is not installed",
              app.warned, lambda w: len(w) == 1 and "not newer" in w[0]
              and L.UPDATE_URL in w[0])
        app.warned.clear()
        L.build_id = lambda: "0.1.5"
        app.choose = ["update"]
        app._offer_update(problem)
        app._on_update(*app.msgs.get(timeout=20)[1:])
        check("a folder already there is said, with the manual link",
              app.warned, lambda w: len(w) == 1 and "already beside" in w[0])
        app.warned.clear()
        app.choose = [None]
        app._offer_update(problem)
        check("Close does nothing more", (app.msgs.empty(), app.warned),
              (True, []))
        L.update_possible = lambda: False
        app.dialogs.clear()
        app._offer_update(problem)
        check("a checkout gets the refusal alone", (app.dialogs, app.warned),
              ([], [problem]))
        app.warned.clear()
        L.update_possible = lambda: True
        L.running_clients_saved = L.running_clients
        L.running_clients = lambda exes: ["CosmicSupremacy.exe"]
        destroyed.clear()
        started.clear()
        app._start_install(os.path.dirname(new_exe))
        check("Start is refused while a game is running",
              (started, destroyed, len(app.warned)), ([], [], 1))
        L.running_clients = L.running_clients_saved
    finally:
        (L.update_possible, L.app_dir, L.build_id, L.fetch_release) = saved[:4]
        if saved[4] is not None:
            L.os.startfile = saved[4]

    print("\n5. both version gates offer it")
    for name in ("start_multiplayer", "on_join"):
        src = inspect.getsource(getattr(L.Launcher, name))
        at = src.find("version_problem(")
        check(f"{name} answers a low build with Update",
              at >= 0 and "self._offer_update(problem)" in src[at:at + 600],
              True)
finally:
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
