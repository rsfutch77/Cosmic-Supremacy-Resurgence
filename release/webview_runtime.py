"""
webview_runtime.py: make sure Microsoft Edge WebView2 Runtime is present before a window opens

Without the runtime pywebview falls back to the Internet Explorer engine, which
cannot draw the page. A missing runtime is installed with Microsoft's bundled
bootstrapper, which needs no administrator rights: run unelevated, it installs
for the current user. Messages are plain Windows message boxes, since the
window itself needs the runtime.
"""
from __future__ import annotations

import ctypes
import os
import subprocess

# The runtime's EdgeUpdate client key. Microsoft documents a `pv` value above
# 0.0.0.0 under it, per machine or per user, as the test for an installed runtime.
CLIENT_KEY = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

BOOTSTRAPPER = "MicrosoftEdgeWebview2Setup.exe"
DOWNLOAD_PAGE = "https://developer.microsoft.com/microsoft-edge/webview2/consumer/"

# pywebview's WinForms backend needs .NET Framework 4.6.2 or later, which is the
# `Release` value 394802 under this key. Every supported Windows 10 has it.
NETFX_KEY = r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full"
NETFX_462 = 394802

INSTALL_TIMEOUT = 15 * 60

MB_OK, MB_YESNO, MB_ICONWARNING, MB_ICONQUESTION = 0x0, 0x4, 0x30, 0x20
IDYES = 6


def installed_version() -> "str | None":
    """The installed runtime's version string, or None if there is none."""
    import winreg
    for hive, path in ((winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + CLIENT_KEY),
                       (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + CLIENT_KEY),
                       (winreg.HKEY_CURRENT_USER, "Software\\" + CLIENT_KEY)):
        try:
            with winreg.OpenKey(hive, path) as key:
                pv, _ = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        if pv and pv != "0.0.0.0":
            return pv
    return None


def netfx_ok() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, NETFX_KEY) as key:
            release, _ = winreg.QueryValueEx(key, "Release")
    except OSError:
        return False
    return release >= NETFX_462


def _box(product: str, text: str, flags: int) -> int:
    return ctypes.windll.user32.MessageBoxW(None, text, product, flags)


def _send_to_download_page(product: str, text: str) -> None:
    if _box(product, text + "\n\nOpen Microsoft's download page now?",
            MB_YESNO | MB_ICONWARNING) == IDYES:
        os.startfile(DOWNLOAD_PAGE)


def ensure(product: str, bootstrapper: "str | None") -> bool:
    """True once the runtime is present. Asks before installing it.

    `bootstrapper` is the bundled installer's path, or None when this build has
    none (a checkout), in which case the player is pointed at Microsoft's page.
    """
    if not netfx_ok():
        _box(product, f"{product} needs Microsoft .NET Framework 4.6.2 or later, "
             "which Windows Update installs.", MB_OK | MB_ICONWARNING)
        return False
    if installed_version():
        return True

    missing = (f"{product} needs Microsoft Edge WebView2 Runtime to draw its "
               "window. It is a free Microsoft component that many Windows "
               "programs share, and this computer does not have it yet.")
    if not bootstrapper or not os.path.exists(bootstrapper):
        _send_to_download_page(product, missing)
        return False
    if _box(product, missing + "\n\nInstall it now? It downloads from Microsoft "
            "and takes a minute or two.", MB_YESNO | MB_ICONQUESTION) != IDYES:
        return False

    try:
        # The documented unattended form. It shows nothing while it works.
        subprocess.run([bootstrapper, "/silent", "/install"], timeout=INSTALL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if installed_version():
        return True
    _send_to_download_page(product, "WebView2 Runtime did not install. It can "
                           "be installed by hand from Microsoft's download page.")
    return False
