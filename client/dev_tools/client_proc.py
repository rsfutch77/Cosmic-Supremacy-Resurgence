"""
client_proc.py , find the game client's process, in one place
=============================================================
    from client_proc import find_pid, NOT_THE_CLIENT
    pid, name = find_pid()

Every tool here that attaches to the running game needs the same answer to the
same question, and until now each carried its own copy of it. **Six copies**,
of which two had been fixed and four had not.

The bug the fixed ones carry a guard against: the player-facing launcher is
`CosmicSupremacyLauncher.exe`, which matches the substring `CosmicSupremacy`
and is a **64-bit** process. Attaching to it and injecting a 32-bit stub gets
as far as `VirtualAllocEx` returning an address above 4 GB, which then will not
pack into a `push imm32`. A confusing way to discover you are in the wrong
process.

It has cost two separate afternoons. Once in `trigger_save.py`, fixed there;
then again in `advance_turns.py`, which is the copy `hold_clock` and the
referee's tick both use, where it meant a client's clock was never actually
held , the launcher had 86400 written into its own memory at the address the
client keeps its clock at, and nothing failed loudly.

Fixing a defect in one copy of six does not fix the defect. This module is the
one copy.
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

    `exclude` is a tuple of lowercase substrings that disqualify a match. It is
    a parameter rather than a constant so a caller that genuinely wants the
    launcher can ask for it, but the default is what every existing caller
    means by "the client".
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
