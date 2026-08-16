"""
gamectl.py — Next Turn, Save and Load, driven from outside the game
===================================================================
The three buttons the TestBed dialog provides, reimplemented so the launcher can
own them.

WHY THE LAUNCHER HAS TO OWN THEM. The TestBed dialog only exists on the testbed
JOIN path: launch the client on a `.csgalaxy` pass file and the dialog appears,
launch it on a `.dat` save and it never does — measured over repeated 60-second
samples and confirmed on screen. But a `.dat` launch is the only way to start a
game whose ship designs were prepared in advance, because a design cannot be
created in memory (STRATEGY.md §3, actuator I) and only the engine's own
deserialiser registers one. So the choice is between a galaxy the AI can fight
in and a dialog that can end a turn, unless the buttons move out here.

Nothing in this module is new mechanism. Each button is a technique the dev
tools already proved:

  Next Turn  shorten the turn length and wait for the counter — advance_turns.py
  Save       call the engine's own SaveGame in a remote thread — trigger_save.py
  Load       relaunch the client on a .dat — game_cycle.py

STDLIB AND CTYPES ONLY, deliberately: the launcher is frozen with PyInstaller and
imports this directly, so anything heavier here becomes a packaging problem
there.

THE LAUNCHER IS 64-BIT AND THE CLIENT IS NOT. Injecting a 32-bit stub into the
wrong process gets as far as VirtualAllocEx returning an address above 4 GB,
which then will not pack into a `push imm32` — a confusing way to find out you
attached to the launcher. find_client() excludes it by name for that reason, and
the allocation is range-checked before it is used.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import struct
import time
import zlib
from ctypes import wintypes

PROCESS_VM_READ           = 0x0010
PROCESS_VM_WRITE          = 0x0020
PROCESS_VM_OPERATION      = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_CREATE_THREAD     = 0x0002
PROCESS_ALL = (PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION |
               PROCESS_QUERY_INFORMATION | PROCESS_CREATE_THREAD)

MEM_COMMIT             = 0x1000
MEM_RESERVE            = 0x2000
MEM_RELEASE            = 0x8000
PAGE_EXECUTE_READWRITE = 0x40
WAIT_OBJECT_0          = 0x0

# .data addresses, stable across launches (advance_turns.py)
TURNLENGTH_ADDR = 0x0080AA08
TURN_COUNTER    = 0x008578E8
DEFAULT_LENGTH  = 3600

# bool __thiscall SaveGame(void *this, int gameid, std::string *gamename)
# `this` is never read on this path, so the string allocation doubles for it.
SAVEGAME_FN = 0x0048B350
STRING_SIZE = 0x1C      # VC9 std::string with its inline buffer
STRING_CAP  = 15        # under 16 keeps the characters inline

# Names containing "CosmicSupremacy" that are NOT the game.
NOT_THE_CLIENT = ("launcher",)

psapi    = ctypes.WinDLL("psapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                    ctypes.c_size_t, wintypes.DWORD,
                                    wintypes.DWORD]
kernel32.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                   ctypes.c_size_t, wintypes.DWORD]
kernel32.CreateRemoteThread.restype = wintypes.HANDLE
kernel32.OpenProcess.restype = wintypes.HANDLE


class GameError(Exception):
    """Anything that stops a button from doing what it says."""


def find_client(exe_substr: str = "CosmicSupremacy"):
    """(pid, name) of the running client, or (None, None)."""
    arr = (wintypes.DWORD * 4096)()
    cb = ctypes.c_ulong()
    if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr),
                               ctypes.byref(cb)):
        return None, None
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
            name = buf.value if psapi.GetModuleBaseNameW(h, None, buf, 260) else ""
            low = name.lower()
            if (exe_substr.lower() in low
                    and not any(x in low for x in NOT_THE_CLIENT)):
                return pid, name
        finally:
            kernel32.CloseHandle(h)
    return None, None


class Client:
    """An open handle on the running game, with the operations the buttons need."""

    def __init__(self, pid: int):
        self.pid = pid
        self.h = kernel32.OpenProcess(PROCESS_ALL, False, pid)
        if not self.h:
            raise GameError(f"could not open the game process (pid {pid}); "
                            f"error {ctypes.get_last_error()}")

    # -- lifetime --
    def close(self):
        if self.h:
            kernel32.CloseHandle(self.h)
            self.h = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    # -- memory --
    def rd32(self, addr):
        buf = (ctypes.c_ubyte * 4)()
        n = ctypes.c_size_t()
        if not kernel32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, 4,
                                          ctypes.byref(n)) or n.value != 4:
            return None
        return struct.unpack("<I", bytes(buf))[0]

    def wr(self, addr, data: bytes):
        n = ctypes.c_size_t()
        buf = (ctypes.c_ubyte * len(data))(*data)
        if not kernel32.WriteProcessMemory(self.h, ctypes.c_void_p(addr), buf,
                                           len(data), ctypes.byref(n)) \
                or n.value != len(data):
            raise GameError(f"write to {addr:#x} failed: "
                            f"{ctypes.get_last_error()}")

    def wr32(self, addr, value):
        self.wr(addr, struct.pack("<I", value))

    # -- turn clock --
    def turn(self):
        return self.rd32(TURN_COUNTER)

    def turn_length(self):
        return self.rd32(TURNLENGTH_ADDR)

    def advance_turn(self, timeout=45.0, drive=1, poll=0.25):
        """Resolve exactly one turn, then put the turn length back.

        The engine runs turns off its own clock, so this does not "press" a
        button — it shortens the turn so the clock fires now, waits for the
        counter, and restores. The restore is in a finally: leaving a galaxy on
        a 1-second turn would run the game away from the player at speed, which
        is a far worse failure than the button not working.
        """
        before = self.turn()
        if before is None:
            raise GameError("cannot read the turn counter — is the game still up?")
        original = self.turn_length()
        if original in (None, 0xFFFFFFFF):
            original = DEFAULT_LENGTH
        try:
            self.wr32(TURNLENGTH_ADDR, drive)
            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(poll)
                now = self.turn()
                if now is None:
                    raise GameError("lost the game process while resolving the turn")
                if now != before:
                    return now
                # The engine rewrites the turn length at a boundary, often.
                if self.turn_length() != drive:
                    self.wr32(TURNLENGTH_ADDR, drive)
            raise GameError(
                f"the turn did not resolve within {timeout:.0f}s. If the game "
                f"is showing a dialog, dismiss it and try again.")
        finally:
            try:
                self.wr32(TURNLENGTH_ADDR, original)
            except GameError:
                pass        # the process is gone; nothing left to restore

    # -- save --
    def _alloc(self, size):
        addr = kernel32.VirtualAllocEx(self.h, None, size,
                                       MEM_COMMIT | MEM_RESERVE,
                                       PAGE_EXECUTE_READWRITE)
        if not addr:
            raise GameError(f"VirtualAllocEx failed: {ctypes.get_last_error()}")
        if addr > 0xFFFFFFFF:
            kernel32.VirtualFreeEx(self.h, ctypes.c_void_p(addr), 0, MEM_RELEASE)
            raise GameError(
                "the game process allocated above 4 GB, so it is not the "
                "32-bit client — refusing to inject a 32-bit stub into it")
        return addr

    def save_game(self, name: str = "singleplayer", gameid: int = 0,
                  timeout_ms: int = 30000) -> bool:
        """Call the engine's SaveGame in a remote thread. Returns its bool result.

        CAUTION, inherited from trigger_save.py: the serialiser walks the whole
        object graph, so this must not run during turn resolution. The launcher
        calls it between turns, which is the only time a player can click it.
        """
        raw = name.encode("latin-1", "replace")[:STRING_CAP]
        s = bytearray(STRING_SIZE)
        s[4:4 + len(raw)] = raw
        struct.pack_into("<I", s, 0x14, len(raw))       # _Mysize
        struct.pack_into("<I", s, 0x18, STRING_CAP)     # _Myres, < 16 -> inline

        string_addr = self._alloc(STRING_SIZE)
        code_addr = None
        thread = None
        try:
            self.wr(string_addr, bytes(s))
            # push &name ; push gameid ; mov ecx,this ; mov eax,fn ; call eax ; ret 4
            code = b""
            for a in (string_addr, gameid):
                code += b"\x68" + struct.pack("<I", a & 0xFFFFFFFF)
            code += b"\xB9" + struct.pack("<I", string_addr)   # `this`, unused
            code += b"\xB8" + struct.pack("<I", SAVEGAME_FN)
            code += b"\xFF\xD0"
            code += b"\xC2\x04\x00"
            code_addr = self._alloc(len(code))
            self.wr(code_addr, code)

            tid = wintypes.DWORD()
            thread = kernel32.CreateRemoteThread(
                self.h, None, 0, ctypes.c_void_p(code_addr), None, 0,
                ctypes.byref(tid))
            if not thread:
                raise GameError(f"CreateRemoteThread failed: "
                                f"{ctypes.get_last_error()}")
            if kernel32.WaitForSingleObject(thread, timeout_ms) != WAIT_OBJECT_0:
                raise GameError("the save did not finish in time")
            rc = wintypes.DWORD()
            kernel32.GetExitCodeThread(thread, ctypes.byref(rc))
            return bool(rc.value & 0xFF)
        finally:
            if thread:
                kernel32.CloseHandle(thread)
            for addr in (code_addr, string_addr):
                if addr:
                    kernel32.VirtualFreeEx(self.h, ctypes.c_void_p(addr), 0,
                                           MEM_RELEASE)


# ── Turning a captured save into something Load can launch ────────────────────
def decode_blob(text) -> bytes:
    """Wire format (base64 of [size][zlib]) -> the raw blob a .dat holds."""
    if isinstance(text, str):
        text = text.encode("ascii")
    decoded = base64.b64decode(text)
    expected = struct.unpack_from("<I", decoded, 0)[0]
    blob = zlib.decompress(decoded[4:])
    if len(blob) != expected:
        raise GameError(f"save size mismatch: header says {expected}, "
                        f"got {len(blob)}")
    return blob


def newest_capture(save_dir: str, since: float = 0.0):
    """The most recent .b64 the server captured, or None."""
    if not os.path.isdir(save_dir):
        return None
    best = None
    for entry in os.listdir(save_dir):
        if not entry.endswith(".b64"):
            continue
        path = os.path.join(save_dir, entry)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime >= since and (best is None or mtime > best[0]):
            best = (mtime, path)
    return best[1] if best else None


# ── Is the opponent ready for the next turn? ─────────────────────────────────
# The AI records the last turn it FINISHED deciding, and the launcher will not
# let the player advance past it.
#
# WHY THIS IS CORRECTNESS AND NOT POLISH. Advancing a turn the opponent has not
# decided yet does not make the game faster, it makes the opponent skip that
# turn — no order issued, no build queued, nothing. And it is silent: the AI
# simply wakes to a turn it never saw, exactly the burst that
# STRATEGY.md §1 warns makes per-turn reasoning meaningless, only caused by a
# person rather than by a sleeping host.
#
# A TIMER WOULD BE A GUESS. A pass is ~0.2s in an ordinary turn, but it is
# whatever the rules take on a turn with a lot happening, and any fixed delay is
# either too long to be pleasant or too short to be true. The AI knows exactly
# when it has finished, so it says so.
AI_READY_TIMEOUT = 45.0     # after this, let the player play anyway


def ai_pass_marker(state_dir: str, civ: str):
    """(turn, stamp) of the opponent's last completed pass, or (None, None)."""
    safe = "".join(c if c.isalnum() else "_" for c in (civ or "unknown"))
    path = os.path.join(state_dir, f"pass_{safe}.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("turn"), data.get("stamp")
    except (OSError, ValueError):
        return None, None


def capture_to_dat(save_dir: str, out_path: str, since: float = 0.0) -> str:
    """Convert the newest captured save into a .dat the client can be launched on.

    The engine's save goes out over HTTP and the server stores the wire form;
    a `.dat` is that same blob decompressed. Doing the conversion here is what
    makes Save and Load two halves of one feature rather than two unrelated
    things that both say "save" somewhere.
    """
    cap = newest_capture(save_dir, since)
    if cap is None:
        raise GameError(
            "the game reported a successful save but nothing arrived at the "
            "server. The launcher's own server has to be running for a save to "
            "be stored — keep this window open while you play.")
    with open(cap, "r", encoding="ascii") as fh:
        blob = decode_blob(fh.read())
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, out_path)       # atomic: a killed launcher cannot truncate a save
    return out_path
