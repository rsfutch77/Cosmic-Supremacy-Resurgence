"""
remote.py — Call the game's own functions in its own process
============================================================
Route 2 (STRATEGY.md §6a): external code cannot originate a ship order, because
the order object owns engine-side allocations and the engine will not build one
lazily. The way out is to stop imitating the engine and just *call* it.

This module runs a short x86 stub inside the game via CreateRemoteThread, which
lets us invoke any function with any calling convention — including __thiscall,
which needs ECX and which a bare CreateRemoteThread cannot express (it passes
exactly one stack argument and nothing in registers).

SAFETY
  Everything here executes code in a live game. A wrong address or argument
  count corrupts the stack and takes the client down. The mitigations used are:
    * every function's argument count is taken from its `ret N`, not guessed
    * stubs are validated on a side-effect-free getter before anything that
      mutates state
    * each stub is a fresh VirtualAllocEx page, freed after the thread exits
    * the thread's exit code carries the return value, so a call can be checked
      without reading game memory at all

ADDRESSES (CosmicSupremacy_Resurgence.exe, image base 0x00400000)
  0x0065165A  operator new(size_t)      __cdecl, one arg, ret 0
  0x004E8420  order-object constructor  __thiscall, SIX stack args, ret 0x18
              (this, origin*, target*, Owner*, float, int, ptr)
              Origin and target are ALLOCATION STARTS (EJBO tag - 8) and the
              Owner is its tag-52 primary form, not the tag-8 reference-node
              form — mixing these up is the easiest way to crash this.
  vftable+0x30 on a ship returns the float for argument 4, and takes no stack
              arguments (confirmed at two independent call sites).
"""
import ctypes
import struct
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_ALL_ACCESS = 0x1F0FFF
MEM_COMMIT_RESERVE = 0x3000
MEM_RELEASE        = 0x8000
PAGE_EXEC_RW       = 0x40
PAGE_READWRITE     = 0x04
INFINITE           = 0xFFFFFFFF

OPERATOR_NEW = 0x0065165A
ORDER_CTOR   = 0x004E8420
ORDER_SIZE   = 0x60

# ShipDesign. Size and both constructors come from the two allocation sites,
# which each do `push 0x118 ; call operator new` and then call one of these.
DESIGN_SIZE = 0x118           # 280 bytes
DESIGN_CTOR = 0x00546EC0      # __thiscall, NO stack arguments. The real one.

# 0x005470A0 IS NOT A COPY CONSTRUCTOR. It is ShipDesign::ShipDesign(Stream*),
# a DESERIALIZING constructor that reads a design out of a save blob. Its call
# site gives it away: immediately before allocating, the caller does
#     push 4 ; push 0x4453474e ("DSGN") ; mov ecx, edi ; call 0x005E6400
# so `edi` is a serialization stream positioned at the save format's DSGN
# section, and that same `edi` is what gets passed here.
#
# It was once mistaken for a copy constructor purely because it takes one
# argument. Passing it a ShipDesign* CRASHED THE CLIENT: the constructor treated
# the design object as a stream, read nonsense through it, and left the new
# object uninitialised — the 280-byte block still held stale UTF-16 UI text
# afterwards. Do not call this without a real stream.
DESIGN_STREAM_CTOR = 0x005470A0

# Every ShipDesign must show these at its allocation start, or construction
# did not happen. Checking them is cheap and is the difference between a bad
# call being caught and a bad call being written into.
DESIGN_VFTABLE_1 = 0x00771DF8   # at block + 0
DESIGN_VFTABLE_2 = 0x00771DF0   # at block + 4
EJBO_TAG         = 0x4F424A45   # 'EJBO' at block + 12

kernel32.VirtualAllocEx.restype  = ctypes.c_void_p
kernel32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                    ctypes.c_size_t, wintypes.DWORD,
                                    wintypes.DWORD]
kernel32.VirtualFreeEx.argtypes  = [wintypes.HANDLE, ctypes.c_void_p,
                                    ctypes.c_size_t, wintypes.DWORD]
kernel32.CreateRemoteThread.restype  = wintypes.HANDLE
kernel32.CreateRemoteThread.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.c_void_p,
                                        ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.c_void_p]
kernel32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_size_t,
                                        ctypes.POINTER(ctypes.c_size_t)]


class RemoteCallError(Exception):
    pass


# ── x86 encoding helpers ───────────────────────────────────────────────────
def push_imm(v):     return b"\x68" + struct.pack("<I", v & 0xFFFFFFFF)
def mov_ecx_imm(v):  return b"\xB9" + struct.pack("<I", v & 0xFFFFFFFF)
def mov_eax_imm(v):  return b"\xB8" + struct.pack("<I", v & 0xFFFFFFFF)
CALL_EAX   = b"\xFF\xD0"
RET_4      = b"\xC2\x04\x00"
PUSH_ECX   = b"\x51"
FSTP_ESP   = b"\xD9\x1C\x24"      # fstp dword ptr [esp]
POP_EAX    = b"\x58"
SUB_ESP_4  = b"\x83\xEC\x04"


class Remote:
    """A privileged handle to the game plus the ability to run stubs in it."""

    def __init__(self, pid, log=print):
        self.pid = pid
        self.log = log
        self.h = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not self.h:
            raise RemoteCallError(
                f"OpenProcess(PROCESS_ALL_ACCESS) failed on pid {pid} "
                f"(error {ctypes.get_last_error()}). CreateRemoteThread needs "
                f"more rights than the viewer's read/write handle.")

    def close(self):
        if self.h:
            kernel32.CloseHandle(self.h)
            self.h = None

    def alloc(self, size, exec_=False):
        p = kernel32.VirtualAllocEx(self.h, None, size, MEM_COMMIT_RESERVE,
                                    PAGE_EXEC_RW if exec_ else PAGE_READWRITE)
        if not p:
            raise RemoteCallError(f"VirtualAllocEx({size}) failed "
                                  f"(error {ctypes.get_last_error()})")
        return int(p)

    def free(self, addr):
        kernel32.VirtualFreeEx(self.h, ctypes.c_void_p(addr), 0, MEM_RELEASE)

    def write(self, addr, data):
        n = ctypes.c_size_t()
        buf = (ctypes.c_char * len(data))(*data)
        if not kernel32.WriteProcessMemory(self.h, ctypes.c_void_p(addr), buf,
                                           len(data), ctypes.byref(n)):
            raise RemoteCallError(f"WriteProcessMemory(0x{addr:08X}) failed "
                                  f"(error {ctypes.get_last_error()})")
        return n.value

    def _run(self, start, param, timeout_ms=5000):
        """Start a thread at `start` and return its exit code (EAX)."""
        th = kernel32.CreateRemoteThread(self.h, None, 0,
                                         ctypes.c_void_p(start),
                                         ctypes.c_void_p(param), 0, None)
        if not th:
            raise RemoteCallError(f"CreateRemoteThread(0x{start:08X}) failed "
                                  f"(error {ctypes.get_last_error()})")
        try:
            if kernel32.WaitForSingleObject(th, timeout_ms) != 0:
                raise RemoteCallError(
                    f"remote thread at 0x{start:08X} did not finish within "
                    f"{timeout_ms}ms — the game is probably wedged or dead")
            code = wintypes.DWORD()
            kernel32.GetExitCodeThread(th, ctypes.byref(code))
            return code.value
        finally:
            kernel32.CloseHandle(th)

    def run_stub(self, code, what=""):
        """Run a self-contained stub. Returns EAX at its `ret`."""
        page = self.alloc(max(len(code), 64), exec_=True)
        try:
            self.write(page, code)
            self.log(f"  [stub] 0x{page:08X} {len(code)}B  {what}")
            return self._run(page, 0)
        finally:
            self.free(page)

    # ── the calls we actually need ─────────────────────────────────────
    def operator_new(self, size=ORDER_SIZE):
        """Allocate from the ENGINE's heap, so the engine may legally free it.

        operator new is __cdecl with a single argument, which is exactly the
        shape CreateRemoteThread passes, so this needs no stub. The 4-byte
        caller-cleanup that never happens lands on a thread stack that is about
        to be discarded.
        """
        p = self._run(OPERATOR_NEW, size)
        if not p:
            raise RemoteCallError("operator new returned NULL")
        self.log(f"  [new] {size} bytes at 0x{p:08X} (engine heap)")
        return p

    def ship_speed_float(self, ship_alloc):
        """Call the ship's vftable[0x30] getter and return its float BITS.

        Side-effect-free, which is why it is the first thing to run: it proves
        the stub machinery works before any call that mutates state.
        """
        code = (mov_ecx_imm(ship_alloc) +
                b"\x8B\x01" +              # mov eax, [ecx]        vftable
                b"\x8B\x40\x30" +          # mov eax, [eax+0x30]
                CALL_EAX +                 # float returned in ST0
                SUB_ESP_4 + FSTP_ESP + POP_EAX +
                RET_4)
        return self.run_stub(code, "ship vftable[0x30] getter")

    def construct_design(self, block):
        """Default-construct a ShipDesign. Takes no arguments at all.

        Leaves the five part vectors EMPTY, so the result is a valid but
        equipment-less design that almost certainly cannot be built as-is.
        Populating those vectors is unsolved — see the report.
        """
        code = (mov_ecx_imm(block) +
                mov_eax_imm(DESIGN_CTOR) +
                CALL_EAX +
                RET_4)
        return self.run_stub(code, f"ShipDesign default ctor -> 0x{block:08X}")

    def verify_design(self, block):
        """Check that a block really is a constructed ShipDesign.

        This exists because a constructor that silently does nothing is
        indistinguishable from one that worked, until something else crashes.
        Calling 0x005470A0 with the wrong argument type left a block full of
        stale UTF-16 UI text, three writes went into it as though it were an
        object, and the client died shortly after. Any construction call must be
        checked before anything is written into its result.

        Returns (ok, description).
        """
        raw = self.read(block, 16)
        if not raw or len(raw) < 16:
            return False, "block is unreadable"
        v1, v2, oid, tag = struct.unpack("<IIII", raw)
        problems = []
        if v1 != DESIGN_VFTABLE_1:
            problems.append(f"+0 vftable 0x{v1:08X} != 0x{DESIGN_VFTABLE_1:08X}")
        if v2 != DESIGN_VFTABLE_2:
            problems.append(f"+4 vftable 0x{v2:08X} != 0x{DESIGN_VFTABLE_2:08X}")
        if tag != EJBO_TAG:
            problems.append(f"+12 tag 0x{tag:08X} is not 'EJBO'")
        if problems:
            return False, "; ".join(problems)
        return True, f"ShipDesign #{oid}, EJBO-tagged"

    def read(self, addr, size):
        buf = (ctypes.c_char * size)()
        n = ctypes.c_size_t()
        if not kernel32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf,
                                          size, ctypes.byref(n)):
            return None
        return bytes(buf[:n.value])

    def construct_order(self, order, origin_alloc, target_alloc, owner_alloc,
                        f_bits, a5=1, a6=0):
        """Run the engine's order constructor on an engine-allocated block.

        Six stack arguments, callee-cleaned (`ret 0x18`), pushed right to left:
        a6, a5, float, owner, target, origin.
        """
        code = (push_imm(a6) +
                push_imm(a5) +
                push_imm(f_bits) +          # the float, as raw bits
                push_imm(owner_alloc) +
                push_imm(target_alloc) +
                push_imm(origin_alloc) +
                mov_ecx_imm(order) +
                mov_eax_imm(ORDER_CTOR) +
                CALL_EAX +
                RET_4)
        return self.run_stub(code, f"order ctor on 0x{order:08X}")
