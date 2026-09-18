"""
list_dialogs.py , report the Win32 dialogs a running client has open
====================================================================
    python list_dialogs.py
    python list_dialogs.py --all        # every window, not only dialogs

The customisation prompts are real Win32 dialogs rather than in-engine overlays,
and they matter to the served path: a client that comes up behind a modal prompt
is not a turn a player can take. Three exist, each its own dialog resource:

    210   Customize Your Home World
    218   Customize Your Civilization
    225   Pick your Civilization Name and Coat of Arms

The main window's title is not enough to tell whether one is up, since a child
dialog leaves the title reading "Galaxy Map". This enumerates the windows owned
by the client's process instead, so a check costs no human attention.

Dialogs report the class name `#32770`, which is what `--all` filters on by
default.
"""
import argparse
import ctypes
import os
import sys
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from trigger_save import find_pid

user32 = ctypes.WinDLL('user32', use_last_error=True)

DIALOG_CLASS = '#32770'
ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def window_text(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def pid_of(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def windows_of(pid):
    """Every top-level window of a process, with its child windows."""
    found = []

    def top(hwnd, _lparam):
        if pid_of(hwnd) == pid:
            found.append((hwnd, 0))
            kids = []

            def child(ch, _lp):
                kids.append(ch)
                return True

            user32.EnumChildWindows(hwnd, ENUMPROC(child), 0)
            found.extend((ch, 1) for ch in kids)
        return True

    user32.EnumWindows(ENUMPROC(top), 0)
    return found


def dialogs(pid):
    """[(hwnd, title)] for each visible dialog the process owns."""
    out = []
    for hwnd, _depth in windows_of(pid):
        if class_name(hwnd) != DIALOG_CLASS:
            continue
        if not user32.IsWindowVisible(hwnd):
            continue
        out.append((hwnd, window_text(hwnd)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--all', action='store_true',
                    help='every window, not only visible dialogs')
    a = ap.parse_args()

    pid, name = find_pid()
    if not pid:
        sys.exit('no client running')
    print(f'{name} (pid {pid})')

    if a.all:
        for hwnd, depth in windows_of(pid):
            vis = 'visible' if user32.IsWindowVisible(hwnd) else 'hidden '
            indent = '    ' * depth
            print(f'  {vis}  {indent}{class_name(hwnd):<20} '
                  f'{window_text(hwnd)[:60]!r}')
        return 0

    found = dialogs(pid)
    if not found:
        print('  no dialog open')
        return 0
    for hwnd, title in found:
        print(f'  DIALOG {hwnd:#010x}  {title!r}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
