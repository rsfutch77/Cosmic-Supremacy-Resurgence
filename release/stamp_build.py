"""
stamp_build.py: record which build the packaging step is producing.

The build name is build.ps1's -Version, or manifest.json's "version" without
it. This writes it to build.json, which CosmicSupremacyLauncher.spec packs
beside manifest.json and launcher.build_info() reads through bundled().

Run by hand as:

    python release/stamp_build.py                    : version from manifest.json
    python release/stamp_build.py 0.1.2 release/build

A checkout is not stamped: the build directory is not on bundled()'s search
path from source, so a clone reports itself as a development build.
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

    None when git is not installed or the build is not running in a checkout.
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
    manifest, as build.ps1 does for -Version.
    """
    info = {
        "build": (version or "").strip() or manifest_version(),
        # UTC, to the second, so it compares with logs from other machines.
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
