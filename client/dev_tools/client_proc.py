"""
client_proc.py: find the game client's process

    from client_proc import find_pid, NOT_THE_CLIENT
    pid, name = find_pid()

The launcher, `CosmicSupremacyLauncher.exe`, also matches `CosmicSupremacy` but
is a 64-bit process, so it is excluded by default.
"""
import ctypes
from ctypes import wintypes

psapi = ctypes.WinDLL('psapi', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

# Names that contain the substring below but are not the game client.
NOT_THE_CLIENT = ('launcher',)


def find_pid(exe_substr='CosmicSupremacy', exclude=NOT_THE_CLIENT):
    """(pid, name) of the running game client, or (None, None).

    `exclude` is a tuple of lowercase substrings that disqualify a match.
    """
    arr = (wintypes.DWORD * 4096)()
    cb = ctypes.c_ulong()
    if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr),
                               ctypes.byref(cb)):
        raise OSError('EnumProcesses failed')
    for i in range(cb.value // ctypes.sizeof(wintypes.DWORD)):
        pid = arr[i]
        if not pid:
            continue
        h = kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(260)
            name = buf.value if psapi.GetModuleBaseNameW(h, None, buf, 260) \
                else ''
            low = name.lower()
            if exe_substr.lower() in low and not any(x in low for x in exclude):
                return pid, name
        finally:
            kernel32.CloseHandle(h)
    return None, None
