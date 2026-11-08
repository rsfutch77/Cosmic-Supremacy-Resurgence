"""
updater.py: the launcher finds a newer release, checks it, and replaces itself.

The order is check, download, stage, verify, hand off, finish:

  check      reads the newest release from the one hard-coded feed
  download   streams the zip, stops past the listed size, checks its SHA-256
  stage      unpacks it under update\\ in the launcher's folder, without data\\
  verify     every .exe is signed by the project's identity, and the staged
             launcher's own build is newer than the running one
  hand off   the running launcher starts the staged one with --finish-update
             and exits
  finish     the staged launcher waits for it to exit, moves the old files to
             update\\previous, copies the new ones in, starts the new launcher,
             and puts the old files back if it never says it started

data\\ is never read, moved or written. Nothing is written outside the
launcher's folder and nothing asks for elevation.

The signing identity is what is trusted, not the feed: a release listing that
names a zip whose executables are not signed by the identity below is refused,
whatever its SHA-256.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

REPO = "rsfutch77/Cosmic-Supremacy-Resurgence"
FEED = f"https://api.github.com/repos/{REPO}/releases/latest"
# Every zip a release offers is downloaded from under this.
DOWNLOADS = f"https://github.com/{REPO}/releases/download/"
ASSET = "CosmicSupremacy-Resurgence-v{version}.zip"
LAUNCHER_EXE = "CosmicSupremacyLauncher.exe"

MAX_LISTING = 1 << 20           # bytes of release listing read
MAX_ZIP = 200 << 20             # largest zip accepted, whatever a listing says
MAX_UNPACKED = 400 << 20        # largest total a zip may unpack to
CHUNK = 1 << 16

# The release signing identity, Azure Artifact Signing. The EKU is the
# identity validation's own OID and outlives each day's certificate.
SIGNER_SUBJECT = "CN=Robert Futch, O=Robert Futch, L=Tucson, S=az, C=US"
SIGNER_ISSUER = re.compile(r"^CN=Microsoft ID Verified CS [A-Z]+ CA \d+, "
                           r"O=Microsoft Corporation, C=US$")
SIGNER_EKU = "1.3.6.1.4.1.311.97.749296695.119938405.159986175.230917329"

UPDATE_DIR = "update"           # under the launcher's folder
STAGED = "staged"
PREVIOUS = "previous"
DOWNLOAD = "download.zip"
STARTED = "started"             # written by a new launcher once its window is up
FAILED = "failed.json"          # written by a helper that put the old build back
# Top-level entries an update never moves, removes or writes.
KEEP = {"data", UPDATE_DIR}

START_WAIT = 90                 # seconds a new launcher has to say it started
EXIT_WAIT = 60                  # seconds the old launcher has to exit
MOVE_TRIES = 10                 # an antivirus scan holds fresh files briefly


class UpdateError(Exception):
    """A step refused or failed. The message is written for the log."""


@dataclasses.dataclass(frozen=True)
class Release:
    version: str
    url: str
    size: int
    sha256: str


def version_parts(text: str) -> "tuple[int, ...]":
    """The dotted number a version or build name starts with: "0.5.1+dev" gives (0, 5, 1)."""
    lead = re.match(r"\d+(?:\.\d+)*", (text or "").strip())
    return tuple(int(p) for p in lead.group(0).split(".")) if lead else ()


def is_newer(candidate: str, current: str) -> bool:
    """Whether `candidate` is a later version than `current`, zero-padded."""
    a, b = version_parts(candidate), version_parts(current)
    if not a:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def update_root(app_dir: str) -> str:
    return os.path.join(app_dir, UPDATE_DIR)


# ── check ─────────────────────────────────────────────────────────────────────

def parse_release(listing, downloads: str = DOWNLOADS) -> Release:
    """The release a listing describes, or UpdateError naming what is wrong.

    The version, the zip and its SHA-256 all come from this one listing. The
    SHA-256 is read twice, from the asset's digest and from the notes' SHA-256
    line, and the two must agree.
    """
    if not isinstance(listing, dict):
        raise UpdateError("the release listing is not an object")
    if listing.get("draft") or listing.get("prerelease"):
        raise UpdateError("the newest release is a draft or a pre-release")
    tag = listing.get("tag_name")
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+(?:\.\d+){1,3}", tag):
        raise UpdateError(f"the release tag {tag!r} is not a version")
    version = tag[1:]
    want = ASSET.format(version=version)
    assets = [a for a in listing.get("assets") or []
              if isinstance(a, dict) and a.get("name") == want]
    if len(assets) != 1:
        raise UpdateError(f"the release has no single {want}")
    asset = assets[0]
    url, size = asset.get("browser_download_url"), asset.get("size")
    if not isinstance(url, str) or not url.startswith(downloads + tag + "/"):
        raise UpdateError(f"the zip is not offered from {downloads}")
    if not isinstance(size, int) or not 0 < size <= MAX_ZIP:
        raise UpdateError(f"the zip's listed size {size!r} is out of range")
    digest = asset.get("digest")
    from_asset = (digest[7:].lower() if isinstance(digest, str)
                  and digest.startswith("sha256:") else None)
    noted = re.search(r"SHA-256\s+([0-9a-fA-F]{64})\b", listing.get("body") or "")
    from_notes = noted.group(1).lower() if noted else None
    if not from_asset or not from_notes:
        raise UpdateError("the release does not carry the zip's SHA-256 "
                          "in both its asset and its notes")
    if from_asset != from_notes:
        raise UpdateError("the release's two SHA-256 values disagree")
    return Release(version, url, size, from_asset)


def _open(url: str, timeout: float):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "CosmicSupremacyLauncher"})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_listing(feed: str = FEED, timeout: float = 15):
    """The feed's JSON, read up to MAX_LISTING. UpdateError when unreachable."""
    try:
        with _open(feed, timeout) as resp:
            raw = resp.read(MAX_LISTING + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(f"the release page could not be reached ({exc})")
    if len(raw) > MAX_LISTING:
        raise UpdateError("the release listing is larger than any real one")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise UpdateError("the release listing is not JSON")


def check(current: str, feed: str = FEED, downloads: str = DOWNLOADS,
          fetch=fetch_listing) -> "Release | None":
    """The newest release if it is newer than `current`, else None."""
    release = parse_release(fetch(feed), downloads)
    return release if is_newer(release.version, current) else None


# ── download and stage ────────────────────────────────────────────────────────

def download(release: Release, dest: str, cancel=None, timeout: float = 30) -> str:
    """Stream the zip to `dest`, stopping past its listed size, and check it."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    sha, got = hashlib.sha256(), 0
    try:
        with _open(release.url, timeout) as resp, open(dest, "wb") as out:
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateError("the download was cancelled")
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                got += len(chunk)
                if got > release.size:
                    raise UpdateError("the download ran past the zip's "
                                      "listed size")
                sha.update(chunk)
                out.write(chunk)
    except (urllib.error.URLError, OSError) as exc:
        _remove(dest)
        raise UpdateError(f"the download failed ({exc})")
    except UpdateError:
        _remove(dest)
        raise
    if got != release.size:
        _remove(dest)
        raise UpdateError(f"the download stopped at {got} of "
                          f"{release.size} bytes")
    if sha.hexdigest() != release.sha256:
        _remove(dest)
        raise UpdateError("the zip's SHA-256 does not match the release")
    return dest


def _members(zf: zipfile.ZipFile):
    """(member, path under the zip's one top folder) for each file to unpack.

    A path that is absolute or climbs out of the folder refuses the whole zip,
    and anything under data\\ is left out.
    """
    names = [i for i in zf.infolist() if not i.is_dir()]
    tops = {i.filename.replace("\\", "/").split("/", 1)[0] for i in names}
    if len(tops) != 1:
        raise UpdateError("the zip does not hold exactly one folder")
    total = 0
    for info in names:
        parts = info.filename.replace("\\", "/").split("/")[1:]
        if (not parts or any(p in ("", ".", "..") for p in parts)
                or ":" in parts[0]):
            raise UpdateError(f"the zip holds an unsafe path {info.filename!r}")
        total += info.file_size
        if total > MAX_UNPACKED:
            raise UpdateError("the zip unpacks to more than any real release")
        if parts[0].lower() in KEEP:
            continue
        yield info, os.path.join(*parts)


def stage(zip_path: str, staged: str) -> str:
    """Unpack the zip into `staged`, emptied first."""
    shutil.rmtree(staged, ignore_errors=True)
    os.makedirs(staged)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info, rel in list(_members(zf)):
                target = os.path.join(staged, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, CHUNK)
    except zipfile.BadZipFile as exc:
        raise UpdateError(f"the zip cannot be read ({exc})")
    if not os.path.isfile(os.path.join(staged, LAUNCHER_EXE)):
        raise UpdateError(f"the zip has no {LAUNCHER_EXE}")
    return staged


# ── verify ────────────────────────────────────────────────────────────────────

_SIGNATURES = r"""
$ErrorActionPreference = 'Stop'
$out = foreach ($p in $paths) {
  $s = Get-AuthenticodeSignature -LiteralPath $p
  $c = $s.SignerCertificate
  [pscustomobject]@{
    path = $p; status = [string]$s.Status
    subject = $(if ($c) { $c.Subject } else { '' })
    issuer = $(if ($c) { $c.Issuer } else { '' })
    ekus = @($(if ($c) { $c.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId } }))
  }
}
ConvertTo-Json -InputObject @($out) -Depth 3 -Compress
"""


def read_signatures(paths) -> "list[dict]":
    """What Windows says about each file's Authenticode signature."""
    quoted = ",".join("'" + p.replace("'", "''") + "'" for p in paths)
    script = f"$paths = @({quoted})\n" + _SIGNATURES
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-EncodedCommand", encoded],
            capture_output=True, text=True, timeout=120,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"the signatures could not be read ({exc})")
    if done.returncode != 0:
        raise UpdateError("the signatures could not be read: "
                          + (done.stderr or "").strip()[:300])
    try:
        rows = json.loads(done.stdout)
    except ValueError:
        raise UpdateError("the signature check answered with something "
                          "other than JSON")
    return rows if isinstance(rows, list) else [rows]


def signature_problem(row: dict) -> "str | None":
    """Why one file's signature is not the project's, or None."""
    name = os.path.basename(str(row.get("path")))
    if row.get("status") != "Valid":
        return f"{name} is not validly signed ({row.get('status')})"
    if row.get("subject") != SIGNER_SUBJECT:
        return f"{name} is signed by {row.get('subject')!r}"
    if not SIGNER_ISSUER.match(row.get("issuer") or ""):
        return f"{name}'s certificate is from {row.get('issuer')!r}"
    if SIGNER_EKU not in (row.get("ekus") or []):
        return f"{name}'s certificate does not carry the project's identity"
    return None


def verify(staged: str, signatures=read_signatures) -> None:
    """Every executable in the staged build is signed by the project."""
    exes = sorted(os.path.join(root, f)
                  for root, _dirs, files in os.walk(staged)
                  for f in files if f.lower().endswith((".exe", ".dll")))
    if not exes:
        raise UpdateError("the staged build holds no executables")
    rows = signatures(exes)
    if len(rows) != len(exes):
        raise UpdateError("the signature check skipped a file")
    for row in rows:
        problem = signature_problem(row)
        if problem:
            raise UpdateError(problem)


def staged_build(staged: str, run=subprocess.run) -> str:
    """The build the staged launcher reports, asked of the launcher itself.

    Run only after `verify`, so what answers is a signed build.
    """
    out = os.path.join(os.path.dirname(staged), "staged_build.json")
    _remove(out)
    try:
        run([os.path.join(staged, LAUNCHER_EXE), "--print-build", out],
            timeout=60, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        with open(out, encoding="utf-8") as fh:
            build = json.load(fh).get("build")
    except (OSError, ValueError, subprocess.TimeoutExpired, AttributeError) as exc:
        raise UpdateError(f"the staged launcher did not report its build ({exc})")
    finally:
        _remove(out)
    if not isinstance(build, str) or not version_parts(build):
        raise UpdateError(f"the staged launcher reports build {build!r}")
    return build


def prepare(app_dir: str, current: str, feed: str = FEED,
            downloads: str = DOWNLOADS, cancel=None, fetch=fetch_listing,
            signatures=read_signatures, run=subprocess.run,
            fetch_zip=None) -> "tuple[Release, str] | None":
    """Check, download, stage and verify. The release and its staged folder,
    or None when nothing newer is out.

    The staged build must name the release's own version: a listing that
    claims a newer version than the zip holds is refused, and so is any build
    no newer than `current`.
    """
    release = check(current, feed, downloads, fetch)
    if release is None:
        return None
    root = update_root(app_dir)
    zip_path = os.path.join(root, DOWNLOAD)
    (fetch_zip or download)(release, zip_path, cancel)
    staged = stage(zip_path, os.path.join(root, STAGED))
    _remove(zip_path)
    verify(staged, signatures)
    build = staged_build(staged, run)
    if version_parts(build) != version_parts(release.version):
        raise UpdateError(f"the release says {release.version} but its "
                          f"launcher is build {build}")
    if not is_newer(build, current):
        raise UpdateError(f"the staged build {build} is not newer than "
                          f"{current}")
    return release, staged


# ── hand off and finish ───────────────────────────────────────────────────────

def hand_off(app_dir: str, staged: str, popen=subprocess.Popen) -> None:
    """Start the staged launcher to finish the update once this one exits."""
    _remove(os.path.join(update_root(app_dir), STARTED))
    _remove(os.path.join(update_root(app_dir), FAILED))
    popen([os.path.join(staged, LAUNCHER_EXE), "--finish-update",
           app_dir, str(os.getpid())],
          cwd=staged, close_fds=True,
          creationflags=(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
          if os.name == "nt" else 0)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _retry(fn, *args):
    for attempt in range(MOVE_TRIES):
        try:
            return fn(*args)
        except PermissionError:
            if attempt == MOVE_TRIES - 1:
                raise
            time.sleep(1)


def _copy(src: str, dst: str) -> None:
    if os.path.isdir(src):
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def _entries(folder: str) -> "list[str]":
    return sorted(n for n in os.listdir(folder) if n.lower() not in KEEP)


def wait_for_exit(pid: int, timeout: float) -> bool:
    """Whether process `pid` has exited within `timeout` seconds."""
    if os.name != "nt":
        return True
    import ctypes
    SYNCHRONIZE = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return True                     # already gone
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(
            handle, int(timeout * 1000)) == 0
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def swap_in(app_dir: str, staged: str, log) -> None:
    """Move the old build to update\\previous and copy the staged one in.

    Puts the old build back and raises if any step fails.
    """
    previous = os.path.join(update_root(app_dir), PREVIOUS)
    shutil.rmtree(previous, ignore_errors=True)
    os.makedirs(previous)
    moved, copied = [], []
    try:
        for name in _entries(app_dir):
            _retry(shutil.move, os.path.join(app_dir, name),
                   os.path.join(previous, name))
            moved.append(name)
        for name in _entries(staged):
            _retry(_copy, os.path.join(staged, name), os.path.join(app_dir, name))
            copied.append(name)
    except OSError as exc:
        log(f"update: swapping in failed ({exc}); putting the old build back")
        restore(app_dir, copied, moved, log)
        raise UpdateError(f"the new build could not be copied in ({exc})")
    log(f"update: moved {len(moved)} old entries aside, copied "
        f"{len(copied)} new ones in")


def restore(app_dir: str, copied, moved, log) -> None:
    """Remove what was copied in and move the old build back."""
    previous = os.path.join(update_root(app_dir), PREVIOUS)
    for name in copied:
        path = os.path.join(app_dir, name)
        try:
            _retry(shutil.rmtree if os.path.isdir(path) else os.remove, path)
        except OSError as exc:
            log(f"update: could not remove new {name} ({exc})")
    for name in moved:
        try:
            _retry(shutil.move, os.path.join(previous, name),
                   os.path.join(app_dir, name))
        except OSError as exc:
            log(f"update: could not put back {name} ({exc})")


def wait_for_start(app_dir: str, proc, timeout: float = START_WAIT) -> bool:
    """Whether the new launcher said it started before exiting or timing out."""
    marker = os.path.join(update_root(app_dir), STARTED)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if os.path.exists(marker):
            return True
        if proc.poll() is not None:
            return os.path.exists(marker)
        time.sleep(0.5)
    return os.path.exists(marker)


def _start(app_dir: str, *args, popen=subprocess.Popen):
    return popen([os.path.join(app_dir, LAUNCHER_EXE), *args], cwd=app_dir,
                 close_fds=True,
                 creationflags=(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
                 if os.name == "nt" else 0)


def finish(app_dir: str, staged: str, old_pid: int, log,
           popen=subprocess.Popen, start_wait: float = START_WAIT,
           exit_wait: float = EXIT_WAIT) -> bool:
    """Run by the staged launcher. Whether the new build is now in place.

    On any failure the old build is put back and started with a note of why,
    in update\\failed.json.
    """
    if not wait_for_exit(old_pid, exit_wait):
        log(f"update: the old launcher (pid {old_pid}) did not exit")
        return False
    previous = os.path.join(update_root(app_dir), PREVIOUS)
    try:
        swap_in(app_dir, staged, log)
    except UpdateError as exc:
        _fail(app_dir, str(exc), popen)
        return False
    marker = os.path.join(update_root(app_dir), STARTED)
    _remove(marker)
    proc = _start(app_dir, "--updated", str(os.getpid()), popen=popen)
    if wait_for_start(app_dir, proc, start_wait):
        log("update: the new launcher started")
        return True
    log("update: the new launcher did not start; putting the old build back")
    try:
        if proc.poll() is None:
            proc.kill()
            proc.wait(15)
    except Exception:
        pass
    restore(app_dir, _entries(staged), _entries(previous), log)
    _fail(app_dir, "the new launcher did not start", popen)
    return False


def _fail(app_dir: str, why: str, popen) -> None:
    try:
        with open(os.path.join(update_root(app_dir), FAILED), "w",
                  encoding="utf-8") as fh:
            json.dump({"why": why}, fh)
    except OSError:
        pass
    try:
        _start(app_dir, popen=popen)
    except OSError:
        pass


def confirm_started(app_dir: str) -> None:
    """Tell the helper this launcher's window is up."""
    root = update_root(app_dir)
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, STARTED), "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))


def take_failure(app_dir: str) -> "str | None":
    """Why the last update was rolled back, once, or None."""
    path = os.path.join(update_root(app_dir), FAILED)
    try:
        with open(path, encoding="utf-8") as fh:
            why = json.load(fh).get("why")
    except (OSError, ValueError, AttributeError):
        return None
    _remove(path)
    return why if isinstance(why, str) else "unknown"


def clean(app_dir: str) -> None:
    """Remove what an update left behind. Anything still in use stays."""
    root = update_root(app_dir)
    for name in (STAGED, PREVIOUS):
        shutil.rmtree(os.path.join(root, name), ignore_errors=True)
    for name in (DOWNLOAD, STARTED):
        _remove(os.path.join(root, name))


def finish_main(argv, log_path: "str | None" = None) -> int:
    """`--finish-update <app_dir> <old pid>`, run from update\\staged."""
    app_dir, old_pid = argv[0], int(argv[1])
    staged = os.path.dirname(os.path.abspath(sys.executable))
    log_path = log_path or os.path.join(app_dir, "data", "launcher.log")

    def log(msg):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        except OSError:
            pass
    log(f"update: finishing from {staged}")
    return 0 if finish(app_dir, staged, old_pid, log) else 1
