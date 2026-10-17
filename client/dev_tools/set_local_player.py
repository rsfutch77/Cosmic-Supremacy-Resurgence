"""
set_local_player.py: tell a running client which civ the player controls

    python set_local_player.py --list
    python set_local_player.py --civ BadGuy
    python set_local_player.py --id 202

`0x00857904` holds a reference cell naming the local civ. It is filled at load
from a TLS red-black tree of player slots: the selection routine at `0x0052DE10`
takes the first slot whose `+0x38` is non-zero and reads the civ's object id from
its `+0x30`. Neither OWNR order nor Owner:4 in the blob affects the selection.

This writes the cell directly with `0x00537BF0(objectId)`, the engine's lookup
from object id to reference cell through the map at `0x00857C7C`. The engine
calls `0x0052DD80` right after its own store at `0x0052F4B7`; `--refresh` does
the same.
"""
import argparse
import ctypes
import os
import struct
import sys
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ai_player"))

# No bytecode for trigger_save: Defender quarantines its .pyc as
# Exploit:Python/Leivion.C.
sys.dont_write_bytecode = True
from trigger_save import (find_pid, write, kernel32,
                          PROCESS_VM_READ, PROCESS_VM_WRITE, PROCESS_VM_OPERATION,
                          PROCESS_QUERY_INFORMATION, PROCESS_CREATE_THREAD,
                          MEM_COMMIT, MEM_RESERVE, MEM_RELEASE,
                          PAGE_EXECUTE_READWRITE)

RESOLVE_FN   = 0x00537BF0     # ref_cell *__cdecl resolve(int objectId)
LOCAL_PLAYER = 0x00857904     # the cell the engine reads for "who am I"
REFRESH_FN   = 0x0052DD80     # what the engine calls after its own store


def build_stub(object_id: int, refresh: bool) -> bytes:
    """A thread proc that performs the engine's own two-step assignment."""
    code  = b"\x68" + struct.pack("<I", object_id & 0xFFFFFFFF)   # push id
    code += b"\xB8" + struct.pack("<I", RESOLVE_FN)               # mov eax, fn
    code += b"\xFF\xD0"                                           # call eax
    code += b"\x83\xC4\x04"                                       # add esp, 4
    code += b"\xA3" + struct.pack("<I", LOCAL_PLAYER)             # mov [glob], eax
    if refresh:
        code += b"\x50"                                           # push eax
        code += b"\xB8" + struct.pack("<I", REFRESH_FN)
        code += b"\xFF\xD0"
        code += b"\x58"                                           # pop eax
    code += b"\xC2\x04\x00"                                       # ret 4
    return code


def civs():
    """[(name, objectId, address)] from the running client."""
    import gamestate as gs
    snap = gs.Snapshot()
    local = snap.local_civ()
    out = []
    for c in snap.civs:
        # The object id sits just ahead of the address the viewer reports.
        oid = snap.rd32(c.addr - 4)
        out.append((c.civ_name, oid, c.addr, local is not None and c.addr == local.addr))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--civ", help="civ name, resolved against the running client")
    g.add_argument("--id", type=int, help="civ object id, as it appears in OWNR")
    g.add_argument("--list", action="store_true", help="show the civs and exit")
    ap.add_argument("--refresh", action="store_true",
                    help="also call 0x0052DD80, as the engine does")
    ap.add_argument("--timeout", type=int, default=30)
    a = ap.parse_args()

    if a.list or a.civ:
        found = civs()
        if a.list:
            for name, oid, addr, is_local in found:
                print(f"  {name:<14} objectId={oid:<5} addr={addr:#010x}"
                      f"{'  <== local' if is_local else ''}")
            return 0
        match = [f for f in found if f[0] == a.civ]
        if not match:
            sys.exit(f"no civ named {a.civ!r}; saw {[f[0] for f in found]}")
        object_id = match[0][1]
    else:
        object_id = a.id

    pid, pname = find_pid()
    if not pid:
        sys.exit("ERROR: CosmicSupremacy not found. Launch the game first.")
    access = (PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION |
              PROCESS_QUERY_INFORMATION | PROCESS_CREATE_THREAD)
    h = kernel32.OpenProcess(access, False, pid)
    if not h:
        sys.exit(f"ERROR: could not open PID {pid}: {ctypes.get_last_error()}")
    print(f"attached to {pname} (pid {pid}), setting local player to object {object_id}")

    remote = kernel32.VirtualAllocEx(h, None, 0x1000, MEM_COMMIT | MEM_RESERVE,
                                     PAGE_EXECUTE_READWRITE)
    if not remote:
        sys.exit(f"ERROR: VirtualAllocEx failed: {ctypes.get_last_error()}")
    remote = int(remote)
    try:
        stub = build_stub(object_id, a.refresh)
        write(h, remote, stub)
        print(f"  stub at {remote:#010x} ({len(stub)} bytes)")
        tid = wintypes.DWORD()
        th = kernel32.CreateRemoteThread(h, None, 0, ctypes.c_void_p(remote),
                                         None, 0, ctypes.byref(tid))
        if not th:
            sys.exit(f"ERROR: CreateRemoteThread failed: {ctypes.get_last_error()}")
        rc = kernel32.WaitForSingleObject(th, a.timeout * 1000)
        if rc != 0:
            print("  WARNING: the thread has not finished; leaving its page mapped")
            return 1
        code = wintypes.DWORD()
        kernel32.GetExitCodeThread(th, ctypes.byref(code))
        kernel32.CloseHandle(th)
        print(f"  0x00857904 <- {code.value:#010x}")
        kernel32.VirtualFreeEx(h, ctypes.c_void_p(remote), 0, MEM_RELEASE)
        return 0
    finally:
        kernel32.CloseHandle(h)


if __name__ == "__main__":
    sys.exit(main())
