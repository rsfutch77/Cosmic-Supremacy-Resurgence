"""
stamp_build.py , record which build the packaging step is producing.

The launcher's build name is decided at package time, not in the source: it is
build.ps1's -Version switch, or manifest.json's "version" when the switch is not
given. Nothing carried that decision into the launcher, which is how a build
made with -Version 0.1.1 shipped a launcher that called itself 0.1.0 and a
dist\\ folder that called it 0.1.1.

This writes the decision to build.json. CosmicSupremacyLauncher.spec calls it
and packs the result into the frozen build beside manifest.json, where
launcher.build_info() reads it back through bundled().

Run by hand as:

    python release/stamp_build.py                    , version from manifest.json
    python release/stamp_build.py 0.1.2 release/build

A checkout is not stamped and is not meant to be: build.json is written into the
build directory, which is not on bundled()'s search path when the launcher runs
from source, so a clone reports itself as a development build.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BUILD_FILE = "build.json"

CREATE_NO_WINDOW = 0x08000000


def manifest_version(release_dir: str = HERE) -> str:
    """The version manifest.json carries, which is build.ps1's default."""
    try:
        with open(os.path.join(release_dir, "manifest.json"),
                  encoding="utf-8") as fh:
            return str(json.load(fh).get("version") or "0.0.0")
    except (OSError, ValueError, AttributeError):
        return "0.0.0"


def git_commit(repo: str = REPO) -> "str | None":
    """The checkout this build came from, with a trailing + if it was dirty.

    None when git is not installed or the build is not running in a checkout,
    which is a fact about the build machine rather than a failure: the stamp is
    still written, one field shorter.
    """
    def git(*args):
        return subprocess.run(("git", "-C", repo) + args, capture_output=True,
                              text=True, timeout=15,
                              creationflags=CREATE_NO_WINDOW
                              if os.name == "nt" else 0)

    try:
        head = git("rev-parse", "--short", "HEAD")
        if head.returncode != 0:
            return None
        commit = head.stdout.strip()
        if not commit:
            return None
        dirty = git("status", "--porcelain")
        if dirty.returncode == 0 and dirty.stdout.strip():
            commit += "+"
        return commit
    except (OSError, subprocess.SubprocessError):
        return None


def stamp(version: "str | None", out_dir: str) -> str:
    """Write build.json into `out_dir` and return its path.

    `version` is what the packaging step decided. None falls back to the
    manifest, which is the same fallback build.ps1 makes for -Version, so a
    build run either way stamps the name it actually used.
    """
    info = {
        "build": (version or "").strip() or manifest_version(),
        # UTC and to the second. A build is compared with a log line written on
        # somebody else's machine, and a local timestamp cannot be.
        "stamped_at": datetime.datetime.now(datetime.timezone.utc)
                              .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    commit = git_commit()
    if commit:
        info["commit"] = commit

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, BUILD_FILE)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(info, fh, indent=2)
        fh.write("\n")
    return path


def main(argv) -> int:
    version = argv[1] if len(argv) > 1 else None
    out_dir = argv[2] if len(argv) > 2 else os.path.join(HERE, "build")
    path = stamp(version, out_dir)
    print(path)
    with open(path, encoding="utf-8") as fh:
        print(fh.read().strip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
