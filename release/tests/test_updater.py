"""
The launcher's self-update: reading a release, downloading and unpacking it,
the signing identity it trusts, swapping a build in and putting the old one
back, and what the launcher offers the player.

Headless: no game and no window. Downloads come from a server on 127.0.0.1.
The signature checks read the real v0.4.x builds when a release worktree
beside this checkout holds them, and are skipped otherwise.
"""
import hashlib
import http.server
import io
import json
import os
import pathlib
import queue
import shutil
import sys
import tempfile
import threading
import zipfile

REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "release", "tests"))
sys.path.insert(0, os.path.join(REPO, "server"))

import launcher as L                                            # noqa: E402
import stamp_build                                              # noqa: E402
import updater as U                                             # noqa: E402
from fake_view import FakeView                                  # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


def refused(fn, *args, **kw):
    """The UpdateError message `fn` raises, or None."""
    try:
        fn(*args, **kw)
    except U.UpdateError as exc:
        return str(exc)
    return None


dirs = []


def fresh_dir():
    d = tempfile.mkdtemp(prefix="updater_")
    dirs.append(d)
    return d


# ── a stand-in release host ──────────────────────────────────────────────────

class Host(http.server.ThreadingHTTPServer):
    """Serves `files`, a {path: bytes} map, and may send more than asked."""

    def __init__(self):
        self.files = {}
        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = self.server.files.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def build_zip(version, extra=None, top=None):
    """A release zip: one top folder, a launcher, a game folder and a data
    folder the updater must leave out."""
    top = top or f"CosmicSupremacy-Resurgence-v{version}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{top}/{U.LAUNCHER_EXE}", b"launcher " + version.encode())
        zf.writestr(f"{top}/README.txt", b"readme")
        zf.writestr(f"{top}/game/CosmicSupremacy.exe", b"client")
        zf.writestr(f"{top}/data/identity.json", b"must not arrive")
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def listing(version, zip_bytes, downloads, digest=None, notes_sha=None, **over):
    sha = hashlib.sha256(zip_bytes).hexdigest()
    tag = f"v{version}"
    name = U.ASSET.format(version=version)
    out = {"tag_name": tag, "draft": False, "prerelease": False,
           "body": f"notes\n\n    SHA-256  {notes_sha or sha}\n",
           "assets": [{"name": name, "size": len(zip_bytes),
                       "digest": "sha256:" + (digest or sha),
                       "browser_download_url": f"{downloads}{tag}/{name}"}]}
    out.update(over)
    return out


def good_signature(path):
    return {"path": path, "status": "Valid", "subject": U.SIGNER_SUBJECT,
            "issuer": "CN=Microsoft ID Verified CS EOC CA 03, "
                      "O=Microsoft Corporation, C=US",
            "ekus": ["1.3.6.1.5.5.7.3.3", U.SIGNER_EKU]}


def signed(paths):
    return [good_signature(p) for p in paths]


def reports(build):
    """A stand-in for running the staged launcher with --print-build."""
    def run(argv, **_kw):
        with open(argv[2], "w", encoding="utf-8") as fh:
            json.dump({"build": build}, fh)
    return run


print("1. versions compare by their leading number")
check("0.5.1 is newer than 0.5.0", U.is_newer("0.5.1", "0.5.0"), True)
check("0.5 is not newer than 0.5.0", U.is_newer("0.5", "0.5.0"), False)
check("0.10.0 is newer than 0.9.9", U.is_newer("0.10.0", "0.9.9"), True)
check("a checkout's 0.5.0+dev is not older than 0.5.0",
      U.is_newer("0.5.0", "0.5.0+dev"), False)
check("an unnumbered version is never newer", U.is_newer("next", "0.1"), False)

print("\n2. a release listing names one zip, from one release")
DL = U.DOWNLOADS
zip_a = build_zip("0.5.1")
good = U.parse_release(listing("0.5.1", zip_a, DL))
check("the version comes from the tag", good.version, "0.5.1")
check("the SHA-256 is the zip's", good.sha256, hashlib.sha256(zip_a).hexdigest())
check("a draft is refused",
      refused(U.parse_release, listing("0.5.1", zip_a, DL, draft=True)),
      lambda m: m and "draft" in m)
check("a pre-release is refused",
      refused(U.parse_release, listing("0.5.1", zip_a, DL, prerelease=True)),
      lambda m: m and "pre-release" in m)
check("a tag that is not a version is refused",
      refused(U.parse_release, listing("0.5.1", zip_a, DL, tag_name="latest")),
      lambda m: m and "not a version" in m)
other = listing("0.5.1", zip_a, DL)
other["assets"][0]["name"] = "CosmicSupremacy-Resurgence-v0.5.0.zip"
check("a zip named for another version is refused",
      refused(U.parse_release, other), lambda m: m and "no single" in m)
moved = listing("0.5.1", zip_a, "https://example.com/")
check("a zip offered from anywhere else is refused",
      refused(U.parse_release, moved), lambda m: m and "not offered from" in m)
older = listing("0.5.1", zip_a, DL)
older["assets"][0]["browser_download_url"] = (
    DL + "v0.4.1/" + U.ASSET.format(version="0.5.1"))
check("a zip from another release's folder is refused",
      refused(U.parse_release, older), lambda m: m and "not offered from" in m)
check("the asset digest and the notes must agree",
      refused(U.parse_release, listing("0.5.1", zip_a, DL, notes_sha="0" * 64)),
      lambda m: m and "disagree" in m)
bare_notes = listing("0.5.1", zip_a, DL, body="no checksum here")
check("a release without the notes' SHA-256 is refused",
      refused(U.parse_release, bare_notes), lambda m: m and "both" in m)
huge = listing("0.5.1", zip_a, DL)
huge["assets"][0]["size"] = U.MAX_ZIP + 1
check("a listed size past the ceiling is refused",
      refused(U.parse_release, huge), lambda m: m and "out of range" in m)
check("the newest release is offered only when newer",
      U.check("0.5.1", fetch=lambda _f: listing("0.5.1", zip_a, DL)), None)
check("and is when it is",
      U.check("0.5.0", fetch=lambda _f: listing("0.5.1", zip_a, DL)).version,
      "0.5.1")
check("an unreachable feed says so",
      refused(U.fetch_listing, "http://127.0.0.1:9/none", timeout=3),
      lambda m: m and "could not be reached" in m)

print("\n3. a download stops past its listed size and must match its SHA-256")
host = Host()
downloads = host.base + "/dl/"
host.files["/dl/v0.5.1/" + U.ASSET.format(version="0.5.1")] = zip_a
rel = U.parse_release(listing("0.5.1", zip_a, downloads), downloads)
dest = os.path.join(fresh_dir(), "update", U.DOWNLOAD)
check("a matching zip downloads", U.download(rel, dest), dest)
check("and is the zip", open(dest, "rb").read() == zip_a, True)
padded = zip_a + b"x" * 1000
host.files["/dl/v0.5.1/" + U.ASSET.format(version="0.5.1")] = padded
check("a host that sends more than listed is cut off",
      refused(U.download, rel, dest), lambda m: m and "ran past" in m)
check("and nothing is left behind", os.path.exists(dest), False)
host.files["/dl/v0.5.1/" + U.ASSET.format(version="0.5.1")] = zip_a[:-1] + b"?"
check("a zip with the wrong SHA-256 is refused",
      refused(U.download, rel, dest), lambda m: m and "does not match" in m)
host.files["/dl/v0.5.1/" + U.ASSET.format(version="0.5.1")] = zip_a[:100]
check("a short download is refused",
      refused(U.download, rel, dest), lambda m: m and "stopped at" in m)
cancel = threading.Event()
cancel.set()
host.files["/dl/v0.5.1/" + U.ASSET.format(version="0.5.1")] = zip_a
check("a cancelled download stops",
      refused(U.download, rel, dest, cancel), lambda m: m and "cancelled" in m)

print("\n4. unpacking leaves data out and refuses unsafe paths")
staged = os.path.join(fresh_dir(), "staged")
z = os.path.join(fresh_dir(), "a.zip")
open(z, "wb").write(zip_a)
U.stage(z, staged)
check("the launcher is unpacked at the top",
      os.path.isfile(os.path.join(staged, U.LAUNCHER_EXE)), True)
check("the game folder comes with it",
      os.path.isfile(os.path.join(staged, "game", "CosmicSupremacy.exe")), True)
check("the zip's data folder does not", os.path.exists(
    os.path.join(staged, "data")), False)
for label, extra in (
        ("a path that climbs out",
         {"CosmicSupremacy-Resurgence-v0.5.1/../evil.txt": b"x"}),
        ("an absolute drive path",
         {"CosmicSupremacy-Resurgence-v0.5.1/C:/evil.txt": b"x"}),
        ("a second top folder", {"Other/evil.txt": b"x"})):
    open(z, "wb").write(build_zip("0.5.1", extra))
    check(f"{label} refuses the zip", refused(U.stage, z, staged),
          lambda m: m is not None)
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("CosmicSupremacy-Resurgence-v0.5.1\\README.txt", b"x")
open(z, "wb").write(buf.getvalue())
check("a zip with no launcher is refused", refused(U.stage, z, staged),
      lambda m: m and "no " + U.LAUNCHER_EXE in m)

print("\n5. only the project's signing identity is trusted")
check("the identity's signature passes",
      U.signature_problem(good_signature("x.exe")), None)
for label, change in (
        ("an unsigned file", {"status": "NotSigned"}),
        ("a broken chain", {"status": "UnknownError"}),
        ("another subject", {"subject": "CN=Someone Else"}),
        ("another issuer", {"issuer": "CN=Some CA, O=Someone, C=US"}),
        ("a certificate without the identity's EKU",
         {"ekus": ["1.3.6.1.5.5.7.3.3"]})):
    row = dict(good_signature("x.exe"), **change)
    check(f"{label} is refused", U.signature_problem(row),
          lambda m: m is not None)
open(z, "wb").write(zip_a)
U.stage(z, staged)


def one_stranger(paths):
    rows = signed(paths)
    rows[-1] = dict(rows[-1], subject="CN=X")
    return rows


check("verify passes a build signed throughout", U.verify(staged, signed), None)
check("verify refuses a build with one file signed by someone else",
      refused(U.verify, staged, one_stranger),
      lambda m: m and "signed by 'CN=X'" in m)
check("verify refuses a check that skips a file",
      refused(U.verify, staged, lambda paths: signed(paths)[1:]),
      lambda m: m and "skipped" in m)
check("verify refuses a folder with nothing to check",
      refused(U.verify, fresh_dir(), signed),
      lambda m: m and "no executables" in m)
here = pathlib.Path(REPO).parent
real = [p for p in (
    here / "resurgence-release-v0.4.1" / "dist" / "CosmicSupremacy-Resurgence-v0.4.1",
    here / "resurgence-release-v0.4.0" / "dist" / "CosmicSupremacy-Resurgence-v0.4.0")
    if (p / U.LAUNCHER_EXE).is_file()]
if not real:
    print("  [SKIP] no v0.4.x release folder beside this checkout")
for folder in real:
    try:
        U.verify(str(folder))
        check(f"{folder.name}, signed by Artifact Signing, verifies", True, True)
    except U.UpdateError as exc:
        check(f"{folder.name}, signed by Artifact Signing, verifies", str(exc), True)
python = sys.executable
row = U.read_signatures([python])[0]
check("python.exe is refused, whoever signed it",
      U.signature_problem(row), lambda m: m is not None)

print("\n6. prepare checks, downloads, stages and verifies in one pass")
host.files["/dl/v0.5.1/" + U.ASSET.format(version="0.5.1")] = zip_a
host.files["/feed"] = json.dumps(listing("0.5.1", zip_a, downloads)).encode()
app = fresh_dir()
got = U.prepare(app, "0.5.0", host.base + "/feed", downloads,
                signatures=signed, run=reports("0.5.1"))
check("a newer release is staged", got and got[0].version, "0.5.1")
check("under the launcher's update folder",
      got and got[1], os.path.join(app, U.UPDATE_DIR, U.STAGED))
check("and the zip is gone once unpacked",
      os.path.exists(os.path.join(app, U.UPDATE_DIR, U.DOWNLOAD)), False)
check("nothing newer is nothing to do",
      U.prepare(app, "0.5.1", host.base + "/feed", downloads,
                signatures=signed, run=reports("0.5.1")), None)
check("a launcher that reports another build is refused",
      refused(U.prepare, app, "0.5.0", host.base + "/feed", downloads,
              signatures=signed, run=reports("0.5.2")),
      lambda m: m and "says 0.5.1" in m)
check("a launcher that reports nothing is refused",
      refused(U.prepare, app, "0.5.0", host.base + "/feed", downloads,
              signatures=signed, run=lambda *a, **k: None),
      lambda m: m and "did not report" in m)
check("a release with an unsigned file is refused before it is run",
      refused(U.prepare, app, "0.5.0", host.base + "/feed", downloads,
              signatures=lambda ps: [dict(good_signature(p), status="NotSigned")
                                     for p in ps],
              run=reports("0.5.1")),
      lambda m: m and "not validly signed" in m)


class Proc:
    """A started launcher. `starts` decides whether it writes the marker."""

    def __init__(self, app_dir, starts):
        self.killed = False
        if starts:
            U.confirm_started(app_dir)

    def poll(self):
        return None if not self.killed else 1

    def kill(self):
        self.killed = True

    def wait(self, _t):
        return 1


def old_install():
    """A launcher folder at 0.5.0 with a player's data."""
    d = fresh_dir()
    open(os.path.join(d, U.LAUNCHER_EXE), "w").write("old launcher")
    open(os.path.join(d, "README.txt"), "w").write("old readme")
    os.makedirs(os.path.join(d, "game"))
    open(os.path.join(d, "game", "CosmicSupremacy.exe"), "w").write("old client")
    open(os.path.join(d, "game", "retired.exe"), "w").write("only in 0.5.0")
    os.makedirs(os.path.join(d, "data", "saves"))
    open(os.path.join(d, "data", "identity.json"), "w").write("the player")
    open(os.path.join(d, "data", "saves", "a.sav"), "w").write("a save")
    return d


def snapshot(d, under=""):
    out = {}
    for root, _dirs, files in os.walk(os.path.join(d, under)):
        for f in files:
            p = os.path.join(root, f)
            out[os.path.relpath(p, d)] = open(p, "rb").read()
    return out


def staged_new(app_dir):
    s = os.path.join(app_dir, U.UPDATE_DIR, U.STAGED)
    z = os.path.join(fresh_dir(), "n.zip")
    open(z, "wb").write(build_zip("0.5.1"))
    return U.stage(z, s)


print("\n7. the swap: the new build in, data untouched, the old one kept")
app = old_install()
data_before = snapshot(app, "data")
staged = staged_new(app)
started = []


def popen_starts(argv, **_kw):
    started.append(argv[1:])
    return Proc(app, starts=True)


log = []
ok = U.finish(app, staged, 0, log.append, popen=popen_starts, start_wait=5)
check("the update finishes", ok, True)
check("the new launcher is in place",
      open(os.path.join(app, U.LAUNCHER_EXE), "rb").read(), b"launcher 0.5.1")
check("the new client is in place",
      open(os.path.join(app, "game", "CosmicSupremacy.exe"), "rb").read(),
      b"client")
check("a file the new build dropped is gone from the folder",
      os.path.exists(os.path.join(app, "game", "retired.exe")), False)
check("data is byte for byte what it was", snapshot(app, "data"), data_before)
check("the zip's data folder never arrived",
      open(os.path.join(app, "data", "identity.json")).read(), "the player")
check("the old build waits in update\\previous", open(os.path.join(
    app, U.UPDATE_DIR, U.PREVIOUS, U.LAUNCHER_EXE)).read(), "old launcher")
check("the new launcher was started and told it was updated",
      started and started[0][0], "--updated")
U.clean(app)
check("clean removes the staged and previous builds",
      sorted(os.listdir(os.path.join(app, U.UPDATE_DIR))), [])

print("\n8. a new launcher that never starts puts the old build back")
app = old_install()
before = snapshot(app)
staged = staged_new(app)
starts = []


def popen_fails(argv, **_kw):
    starts.append(argv[1:])
    return Proc(app, starts=False)


ok = U.finish(app, staged, 0, lambda _m: None, popen=popen_fails, start_wait=1)
check("the update reports failure", ok, False)
check("every old file is back as it was",
      {k: v for k, v in snapshot(app).items()
       if not k.startswith(U.UPDATE_DIR)}, before)
check("the old launcher is started again, with no arguments",
      starts[-1:], [[]])
check("and is told why once", U.take_failure(app),
      "the new launcher did not start")
check("but only once", U.take_failure(app), None)

print("\n9. a swap that cannot copy puts the old build back")
app = old_install()
before = snapshot(app)
staged = staged_new(app)
real_copy = U._copy


def copy_fails(src, dst):
    if os.path.basename(src) == "game":
        raise PermissionError("held by a scan")
    real_copy(src, dst)


U._copy = copy_fails
U.MOVE_TRIES, tries = 2, U.MOVE_TRIES
try:
    ok = U.finish(app, staged, 0, lambda _m: None,
                  popen=lambda a, **k: Proc(app, True), start_wait=1)
finally:
    U._copy, U.MOVE_TRIES = real_copy, tries
check("the update reports failure", ok, False)
check("every old file is back as it was",
      {k: v for k, v in snapshot(app).items()
       if not k.startswith(U.UPDATE_DIR)}, before)
check("and why is recorded", U.take_failure(app),
      lambda m: m and "could not be copied" in m)

print("\n10. a test build names its own feed, and a release build none")
d = fresh_dir()
info = json.load(open(stamp_build.stamp("0.5.0", d), encoding="utf-8"))
check("a release stamp names no feed",
      "update_feed" in info or "update_downloads" in info, False)
info = json.load(open(stamp_build.stamp("0.5.0", d, "http://127.0.0.1:1/f",
                                        "http://127.0.0.1:1/d/"),
                      encoding="utf-8"))
check("a test stamp names both", (info.get("update_feed"),
                                  info.get("update_downloads")),
      ("http://127.0.0.1:1/f", "http://127.0.0.1:1/d/"))
try:
    stamp_build.stamp("0.5.0", d, "http://127.0.0.1:1/f", None)
    check("a feed without a prefix is refused", False, True)
except ValueError:
    check("a feed without a prefix is refused", True, True)
os.environ.pop(L.UPDATE_FEED_ENV, None)
os.environ.pop(L.UPDATE_DOWNLOADS_ENV, None)
check("a checkout with nothing set trusts the real feed",
      L.update_source(), (U.FEED, U.DOWNLOADS, False))
os.environ[L.UPDATE_FEED_ENV] = host.base + "/feed"
os.environ[L.UPDATE_DOWNLOADS_ENV] = downloads
check("a checkout may name a stand-in", L.update_source(),
      (host.base + "/feed", downloads, True))
os.environ.pop(L.UPDATE_FEED_ENV)
os.environ.pop(L.UPDATE_DOWNLOADS_ENV)
check("a checkout never installs", L.can_self_update(), False)
build_text = open(os.path.join(REPO, "release", "build.ps1"),
                  encoding="utf-8-sig").read()
check("the freeze carries the updater",
      "'--hidden-import', 'updater'" in build_text, True)


print("\n11. what the launcher offers")


def bare(**over):
    app = object.__new__(L.Launcher)
    app.view = FakeView(**over.pop("answers", {}))
    app.said = []
    app.say = app.said.append
    app.msgs = queue.Queue()
    app.update_ready = None
    app.update_busy = True
    app.client_exes = set()
    app.mp_thread = None
    app.mp_sending = False
    for k, v in over.items():
        setattr(app, k, v)
    return app


rel = U.Release("0.5.1", downloads + "x.zip", 1, "0" * 64)
app = bare()
app._on_update_checked(None, "the release page could not be reached (x)", None)
check("a failed check is logged as one, not as up to date",
      app.said[-1], lambda s: "could not be reached" in s
      and "no release newer" not in s)
check("and offers nothing", getattr(app.view, "update", ""), "")
app._on_update_checked((rel, "staged"), None, None)
check("a verified release is offered on the bottom bar",
      app.view.update, "Update to v0.5.1")
check("and the check is no longer busy", app.update_busy, False)
app = bare()
app._on_update_checked((rel, None), None, None)
check("a checkout only reports a release", app.update_ready, None)

app = bare(update_ready=(rel, "staged"))
app._below_minimum("needs 0.6.0", "0.6.0")
check("a galaxy needing more than the download gets the plain refusal",
      app.view.prompts[-1][0], "warn")
handed = []
real_hand_off, real_running = U.hand_off, L.running_clients
U.hand_off = lambda a, s: handed.append(s)
try:
    app = bare(update_ready=(rel, "staged"), answers={"ok_cancel": True})
    app._shut_down = lambda why: handed.append(why)
    L.running_clients = lambda exes: ["CosmicSupremacy.exe"]
    app.on_update()
    check("Update waits while the game is running", handed, [])
    check("and says so", app.view.prompts[-1][0], "warn")
    L.running_clients = lambda exes: []
    app._below_minimum("needs 0.5.1", "0.5.1")
    check("a galaxy the download satisfies offers it, then hands off once",
          handed, ["staged", "the launcher is updating"])
    check("with one question, not two",
          [k for k, _a in app.view.prompts].count("ok_cancel"), 1)
finally:
    U.hand_off, L.running_clients = real_hand_off, real_running

host.shutdown()
for d in dirs:
    shutil.rmtree(d, ignore_errors=True)

print(f"\n{'ALL PASSED' if not fails else f'{len(fails)} FAILED: {fails}'}")
sys.exit(1 if fails else 0)
