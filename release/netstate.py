"""
netstate.py: whether this machine has an internet connection
=============================================================
Asks Windows, which already tracks it, so a check sends nothing over the
network and costs a system call. `online()` is True, False, or None when
Windows cannot say (another platform, or an older Windows).

Windows' answer comes from its own probe, which a firewall or proxy can fool
into "no internet" on a working connection. The launcher uses a change in the
answer only as a reason to read the galaxy at once, and the read decides what
is shown.
"""
import ctypes
import sys

# NL_NETWORK_CONNECTIVITY_LEVEL_HINT values from netioapi.h.
LEVEL_NONE = 1
LEVEL_LOCAL = 2              # a network, but no route to the internet
LEVEL_INTERNET = 3
LEVEL_CONSTRAINED = 4        # internet through a captive portal or a cap


class _Hint(ctypes.Structure):
    """NL_NETWORK_CONNECTIVITY_HINT."""
    _fields_ = [("level", ctypes.c_int),
                ("cost", ctypes.c_int),
                ("approaching_limit", ctypes.c_ubyte),
                ("over_limit", ctypes.c_ubyte),
                ("roaming", ctypes.c_ubyte)]


def _from_hint():
    """From GetNetworkConnectivityHint, on Windows 10 2004 and later."""
    try:
        query = ctypes.windll.iphlpapi.GetNetworkConnectivityHint
    except (AttributeError, OSError):
        return None
    hint = _Hint()
    if query(ctypes.byref(hint)) != 0:
        return None
    if hint.level in (LEVEL_INTERNET, LEVEL_CONSTRAINED):
        return True
    if hint.level in (LEVEL_NONE, LEVEL_LOCAL):
        return False
    return None


def _from_wininet():
    """From InternetGetConnectedState, on older Windows. It says only whether
    any network is up."""
    try:
        flags = ctypes.c_ulong()
        return bool(ctypes.windll.wininet.InternetGetConnectedState(
            ctypes.byref(flags), 0))
    except (AttributeError, OSError):
        return None


def online():
    """True, False, or None when this machine cannot say."""
    if sys.platform != "win32":
        return None
    got = _from_hint()
    return got if got is not None else _from_wininet()


if __name__ == "__main__":
    print(online())
