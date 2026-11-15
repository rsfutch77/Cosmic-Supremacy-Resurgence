"""
trigger_save_message.py: make the client save by sending it a window message

    python trigger_save_message.py                 # send the save message
    python trigger_save_message.py --gameid 0
    python trigger_save_message.py --probe          # report the path, touch nothing

Finds the client's main window and sends it a registered window message with
`SendMessageTimeoutW`. No process memory is written and no remote thread is
created.

The message reaches `SaveGame` only in a client carrying the handler
`patch_save_message.py` adds, which calls it on the client's main thread and
returns its result, so the send returns once the save is done. A stock client
answers 0: `SaveGame` at `0x0048B350` has one caller, the Save/Load dialog's
command handler `0x0048B950`, which needs that dialog open.
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

# Registered by both the launcher and the resident handler; the two must match
# patch_save_message.SAVE_MESSAGE_NAME.
SAVE_MESSAGE_NAME = "CosmicSupremacyResurgenceSave"

SMTO_ABORTIFHUNG = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SEND_TIMEOUT = 30            # seconds; the injected path waits as long


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


def image_path(pid: int):
    """The running client's exe path, read with query rights only."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        kernel32.CloseHandle(h)


def client_has_handler() -> bool:
    """Whether the running client's exe carries the save handler."""
    import patch_save_message as psm
    pid, _ = find_pid()
    path = image_path(pid) if pid else None
    if not path:
        return False
    try:
        with open(path, "rb") as f:
            return psm.state(f.read()) == "patched"
    except OSError:
        return False


def send_save(gameid: int = 0, timeout: int = SEND_TIMEOUT):
    """Send the save message and wait for the client's answer.

    Returns (ok, detail); `ok` is SaveGame's own result.
    """
    pid, pname = find_pid()
    if not pid:
        return False, "CosmicSupremacy is not running"
    hwnd = main_window(pid)
    if not hwnd:
        return False, f"{pname} (pid {pid}) has no visible main window to send to"
    mid = save_message_id()
    result = ctypes.c_size_t()
    sent = user32.SendMessageTimeoutW(hwnd, mid, gameid, 0, SMTO_ABORTIFHUNG,
                                      timeout * 1000, ctypes.byref(result))
    if not sent:
        return False, (f"SendMessageTimeoutW to {pname} (pid {pid}) failed or "
                       f"timed out after {timeout}s: {ctypes.get_last_error()}")
    ok = result.value == 1
    return ok, (f"sent save message {mid:#06x} to {pname} (pid {pid}, "
                f"hwnd {hwnd:#010x}), gameid {gameid}: SaveGame returned "
                f"{result.value} ({'saved' if ok else 'not saved'})")


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
        print(f"save handler {'present' if client_has_handler() else 'absent'}")
        return 0

    ok, detail = send_save(a.gameid)
    print(detail)
    print("check the launcher's save directory for the capture")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
