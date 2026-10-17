"""
trigger_save_message.py: make the client save by posting it a window message

    python trigger_save_message.py                 # post the save message
    python trigger_save_message.py --gameid 0
    python trigger_save_message.py --probe          # report the path, touch nothing

Finds the client's main window and posts it a registered window message with
`PostMessageW`. No process memory is written and no remote thread is created.

The message reaches `SaveGame` only if the client carries a resident handler for
it. A stock client does not: `SaveGame` at `0x0048B350` has one caller, the
Save/Load dialog's command handler `0x0048B950`, which needs that dialog open.
Without the handler no capture appears.
"""
import argparse
import ctypes
import os
import sys
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# No bytecode for trigger_save: Defender quarantines its .pyc.
sys.dont_write_bytecode = True
from client_proc import find_pid

user32 = ctypes.WinDLL("user32", use_last_error=True)
ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# Registered by both the launcher and the resident handler; the two must match.
SAVE_MESSAGE_NAME = "CosmicSupremacyResurgenceSave"


def save_message_id() -> int:
    """The registered id for the save message."""
    mid = user32.RegisterWindowMessageW(SAVE_MESSAGE_NAME)
    if not mid:
        raise OSError(f"RegisterWindowMessageW failed: {ctypes.get_last_error()}")
    return mid


def _pid_of(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def main_window(pid: int):
    """The client's main top-level window, or None.

    The visible top-level window with the longest title; child dialogs and
    hidden helper windows are skipped.
    """
    best = []

    def visit(hwnd, _lparam):
        if _pid_of(hwnd) == pid and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            best.append((length, hwnd))
        return True

    user32.EnumWindows(ENUMPROC(visit), 0)
    if not best:
        return None
    best.sort(reverse=True)
    return best[0][1]


def post_save(gameid: int = 0):
    """Post the save message to the running client. Returns (ok, detail).

    `ok` reports only that the message was posted; the caller confirms the save
    by a new capture appearing.
    """
    pid, pname = find_pid()
    if not pid:
        return False, "CosmicSupremacy is not running"
    hwnd = main_window(pid)
    if not hwnd:
        return False, f"{pname} (pid {pid}) has no visible main window to post to"
    mid = save_message_id()
    if not user32.PostMessageW(hwnd, mid, gameid, 0):
        return False, (f"PostMessageW failed: {ctypes.get_last_error()}")
    return True, (f"posted save message {mid:#06x} to {pname} "
                  f"(pid {pid}, hwnd {hwnd:#010x}), gameid {gameid}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--gameid", type=int, default=0,
                    help="save slot; 0 matches the slot cs_server advertises")
    ap.add_argument("--probe", action="store_true",
                    help="report the window and message id, post nothing")
    a = ap.parse_args()

    if a.probe:
        pid, pname = find_pid()
        if not pid:
            print("no client running")
            return 1
        hwnd = main_window(pid)
        print(f"client {pname} (pid {pid})")
        print(f"main window {hwnd:#010x}" if hwnd else "no visible main window")
        print(f"save message {SAVE_MESSAGE_NAME!r} -> id {save_message_id():#06x}")
        return 0

    ok, detail = post_save(a.gameid)
    print(detail)
    print("check the launcher's save directory for the capture")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
