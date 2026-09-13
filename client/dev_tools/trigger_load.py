"""
trigger_load.py , Make the running client load a save, with no user click
========================================================================
Companion to trigger_save.py.  Calls the game's own load routine in a remote
thread so a server-authored blob can be pushed into a client that nobody is
sitting at , which is the whole point of the exercise: the server decides the
galaxy state, including ship orders, and the client adopts it.

    python trigger_load.py                # load slot 0
    python trigger_load.py --gameid 0
    python trigger_load.py --dry-run
"""
import argparse
import ctypes
import sys
from ctypes import wintypes

from trigger_save import (PROCESS_VM_READ, PROCESS_VM_WRITE, PROCESS_VM_OPERATION,
                          PROCESS_QUERY_INFORMATION, PROCESS_CREATE_THREAD,
                          MEM_COMMIT, MEM_RESERVE, MEM_RELEASE,
                          PAGE_EXECUTE_READWRITE,
                          build_call_stub, find_pid, write, kernel32)

LOADGAME_FN = 0x0048B5D0     # bool __thiscall LoadGame(this, int gameid)


def main():
    ap = argparse.ArgumentParser(description="Trigger a loadgame in the running client")
    ap.add_argument("--gameid", type=int, default=0,
                    help="save slot to load; 0 is what cs_server advertises")
    ap.add_argument("--timeout", type=int, default=120,
                    help="seconds to wait; a full galaxy rebuild is not instant")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--i-know-it-crashes", action="store_true",
                    help="required: calling LoadGame off-thread faults on NULL "
                         "thread-local state and takes the game down (see the "
                         "module docstring)")
    args = ap.parse_args()

    if not args.dry_run and not args.i_know_it_crashes:
        sys.exit("refusing to run: LoadGame from a remote thread crashes the game "
                 "on NULL TLS state.\nRead the docstring; pass "
                 "--i-know-it-crashes to do it anyway.")

    if args.dry_run:
        stub = build_call_stub(LOADGAME_FN, [args.gameid], 0x11110000)
        print(f"stub ({len(stub)} bytes): {stub.hex(' ')}")
        print(f"  push gameid={args.gameid} / mov ecx,dummy / "
              f"call {LOADGAME_FN:#x} / ret 4")
        return 0

    pid, pname = find_pid()
    if not pid:
        sys.exit("ERROR: CosmicSupremacy not found. Launch the game first.")
    access = (PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION |
              PROCESS_QUERY_INFORMATION | PROCESS_CREATE_THREAD)
    h = kernel32.OpenProcess(access, False, pid)
    if not h:
        sys.exit(f"ERROR: could not open PID {pid}: {ctypes.get_last_error()}")
    print(f"attached to {pname} (pid {pid})")

    remote = kernel32.VirtualAllocEx(h, None, 0x1000, MEM_COMMIT | MEM_RESERVE,
                                     PAGE_EXECUTE_READWRITE)
    if not remote:
        kernel32.CloseHandle(h)
        sys.exit(f"ERROR: VirtualAllocEx failed: {ctypes.get_last_error()}")
    remote = int(remote)

    try:
        # `this` points at our own page: readable, and never dereferenced anyway
        write(h, remote, build_call_stub(LOADGAME_FN, [args.gameid], remote + 0x800))
        print(f"  stub at {remote:#010x}, calling LoadGame({args.gameid})")

        tid = wintypes.DWORD()
        th = kernel32.CreateRemoteThread(h, None, 0, ctypes.c_void_p(remote),
                                         None, 0, ctypes.byref(tid))
        if not th:
            sys.exit(f"ERROR: CreateRemoteThread failed: {ctypes.get_last_error()}")
        print(f"  thread {tid.value} started; waiting up to {args.timeout}s")

        rc = kernel32.WaitForSingleObject(th, args.timeout * 1000)
        if rc != 0:
            print(f"  WARNING: wait returned {rc} , thread unfinished. Leaving its "
                  f"page allocated so it cannot execute freed memory.")
            kernel32.CloseHandle(th)
            return 1

        code = wintypes.DWORD()
        kernel32.GetExitCodeThread(th, ctypes.byref(code))
        kernel32.CloseHandle(th)

        # The thread exit code is NOT trustworthy on its own: when the load
        # faults, the game's own crash handler runs on this thread, writes a
        # minidump and tears the process down, and the exit code we read back
        # can still look like success.  Check the process is actually alive.
        still_there, _ = find_pid()
        if not still_there:
            print(f"  thread exit code was {code.value & 0xFF}, but THE PROCESS IS "
                  f"GONE , the load crashed it.")
            print( "  look for CosmicSupremacy_Resurgence_<timestamp>.dmp next to "
                   "the exe; the exception record names the faulting address.")
            return 2

        ok = code.value & 0xFF
        print(f"  LoadGame returned {ok} "
              f"({'loaded' if ok else 'FAILED , client will have shown a dialog'})")
        kernel32.VirtualFreeEx(h, ctypes.c_void_p(remote), 0, MEM_RELEASE)
        return 0 if ok else 1
    finally:
        kernel32.CloseHandle(h)


if __name__ == "__main__":
    sys.exit(main())
