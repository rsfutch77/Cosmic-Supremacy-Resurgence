"""
The WebView2 Runtime check that runs before the launcher opens its window: what
it does when the runtime is there, missing, refused, or fails to install.

The registry, the message boxes and the installer are stood in for. The real
registry read runs once against this machine and only has to not raise.
"""
import os
import sys
import tempfile

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))

import webview_runtime as W

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


class Machine:
    """Stands in for the registry, the message boxes, the installer and the browser."""

    def __init__(self, runtime=None, netfx=True, answer=W.IDYES, installs=True):
        self.runtime, self.netfx, self.answer, self.installs = runtime, netfx, answer, installs
        self.boxes, self.ran, self.opened = [], [], []

    def __enter__(self):
        self.saved = (W.installed_version, W.netfx_ok, W._box, W.subprocess.run, W.os.startfile)
        W.installed_version = lambda: self.runtime
        W.netfx_ok = lambda: self.netfx
        W._box = self.box
        W.subprocess.run = self.run
        W.os.startfile = self.opened.append
        return self

    def __exit__(self, *exc):
        (W.installed_version, W.netfx_ok, W._box, W.subprocess.run, W.os.startfile) = self.saved

    def box(self, product, text, flags):
        self.boxes.append(text)
        return self.answer

    def run(self, argv, timeout=None):
        self.ran.append(argv)
        if self.installs:
            self.runtime = "130.0.0.0"


setup = tempfile.NamedTemporaryFile(suffix=".exe", delete=False)
setup.close()

print("1. the registry read answers on this machine")
check("a version string or None", W.installed_version(),
      lambda v: v is None or isinstance(v, str))
check(".NET check answers", W.netfx_ok(), lambda v: isinstance(v, bool))

print("\n2. a machine that has the runtime opens the window and asks nothing")
with Machine(runtime="129.0.2792.65") as m:
    check("ready", W.ensure("CS", setup.name), True)
    check("no message", m.boxes, [])
    check("nothing run", m.ran, [])

print("\n3. a missing runtime is installed with the bundled bootstrapper, after asking")
with Machine() as m:
    check("ready after the install", W.ensure("CS", setup.name), True)
    check("asked once", len(m.boxes), 1)
    check("ran the documented silent install", m.ran, [[setup.name, "/silent", "/install"]])

print("\n4. a player who says no gets no install and no window")
with Machine(answer=7) as m:
    check("not ready", W.ensure("CS", setup.name), False)
    check("nothing run", m.ran, [])

print("\n5. an install that does not take points at Microsoft's page")
with Machine(installs=False) as m:
    check("not ready", W.ensure("CS", setup.name), False)
    check("asked, then explained", len(m.boxes), 2)
    check("the download page opened on yes", m.opened, [W.DOWNLOAD_PAGE])

print("\n6. a build with no bootstrapper points at Microsoft's page")
with Machine() as m:
    check("not ready", W.ensure("CS", None), False)
    check("nothing run", m.ran, [])
    check("the download page opened on yes", m.opened, [W.DOWNLOAD_PAGE])

print("\n7. .NET Framework older than 4.6.2 stops before the runtime is looked at")
with Machine(runtime="129.0.2792.65", netfx=False) as m:
    check("not ready", W.ensure("CS", setup.name), False)
    check("one message", len(m.boxes), 1)

os.unlink(setup.name)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
