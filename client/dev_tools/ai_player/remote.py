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
SELL_FACILITY = 0x004F8580      # PlanetProperties::SellFacility
HURRY_PRODUCTION = 0x004EFFF0   # PlanetProperties::HurryProduction
SHIPCOMMAND_CTOR = 0x004E9BA0   # __thiscall(int orderType, EJBO* target), ret 8
GET_RELATION_CODE = 0x00534D90  # __thiscall(Owner* other) on Owner:248, ret 4
FIRST_CONTACT     = 0x00534F90  # __thiscall(Owner* other); creates the record
RELATION_FIND     = 0x00534340  # __thiscall(Owner* other) -> record pointer
REPUTATION_ADD    = 0x00534D50  # __thiscall(Owner* other, short delta), ret 8
RELATION_CODE_OFF = 4           # within the record Find returns
OWNER_RELATIONS = 248           # Owner:248, the diplomacy container
OWNER_PRIMARY_FROM_TAG = -52    # Owner allocation starts at tag - 52
SHIP_SETCOMMAND  = 0x004D9DC0   # __thiscall(ShipCommand*), ret 4
SHIPCOMMAND_SIZE = 16           # +0 type, +4 target node, +8/+9 two bytes
SHIP_ALLOC_FROM_TAG = -8        # Ship:N == ship_allocation + (N + 8)
HURRY_COST       = 0x00555660   # __cdecl(PlanetProperties*) -> cost, or -1
PRODUCTION_DONE  = 0x004F0260   # __thiscall(PP) -> production accumulated
PP_TOTAL_SLOT    = 0x74         # PP vftable slot -> total production needed
HURRY_MIN_FRACTION = 0.5        # [0x00750360]; below this the engine refuses
PLANET_PROPERTIES = 88          # PlanetProperties lives at planet_tag + 88
OFFLINE_FLAG = 0x0086F1A0       # the local-apply path runs only when this byte is set
ORDER_CTOR   = 0x004E8420
ORDER_SIZE   = 0x60

# ── Conscription ───────────────────────────────────────────────────────────
# Static analysis in dev_tools/findings_production_path.md 1.0-1.6; every
# signature below is CONFIRMED there from the callee and both call sites.
#
# Conscription is not an append to the military list. A citizen and a stationed
# military unit are the SAME 16-byte record in two different vectors, and
# drafting is a job change: set the record's job id to 3 and hand the list back,
# and PlanetProperties::CommitPopulation migrates every job-3 record across.
CHANGE_CITIZEN_JOBS = 0x00574180  # __cdecl(Planet* alloc, container* indices,
                                  #         int newJobId, bool bFromMilitaryList)
GET_DRAFT_COST      = 0x00516060  # __thiscall(int count), ret 4
DRAFT_COST_THIS     = 728         # ECX = Owner_primary + 0x30c == civ_tag + 728
DRAFT_JOB           = 3           # job id 3 is crew/military
CONTAINER_VECTOR    = 12          # a container holds its vector 12 bytes in —
                                  # the same wrapper idiom as Planet:132 and the
                                  # order object's +28/+52 list heads
DRAFT_FREE_BELOW    = 0x0080AA00  # content version; < 150 and the engine skips
                                  # the whole cost block, i.e. drafting is FREE
PLANET_ALLOC_FROM_TAG = -8        # Planet is single-inheritance: an 8-byte
                                  # header, so the allocation starts at tag - 8

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

# ShipDesignData lives at ShipDesign allocation + 0x10, i.e. (EJBO tag) + 4.
# Every derived stat is a lazy cache: the block is set to -1 whenever parts
# change, and each getter is `if (cached == -1) { compute; cache; } return`.
# Calling the getter therefore both READS the value and warms the cache.
DESIGN_DATA_OFFSET  = 4        # from the EJBO tag
DESIGN_SPEED_GETTER = 0x005480A0   # __thiscall, no args, returns in st(0)
DESIGN_MINCREW_GETTER = 0x005490E0 # __thiscall, no args, returns in eax
# The three firepower pools. Each is an independent damage class: 1 and 2 are
# anti-ship, 3 is what planetary-defence participants are counted into.
DESIGN_FP_GETTERS = (0x00548CC0, 0x00548E20, 0x00548F80)

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


class RemoteThreadStuck(RemoteCallError):
    """A remote thread was created and did not finish in time.

    Its stub page MUST be leaked, never freed — the thread is still inside it.
    Subclasses RemoteCallError so existing handlers keep catching it; the
    distinction only matters to run_stub, which needs it to decide cleanup.
    """


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
ADD_ESP_4  = b"\x83\xC4\x04"      # __cdecl callees leave their args to us
ADD_ESP_16 = b"\x83\xC4\x10"      # four of them


class Remote:
    """A privileged handle to the game plus the ability to run stubs in it."""

    def __init__(self, pid, log=print):
        self.pid = pid
        self.log = log
        # Stub pages we deliberately did NOT free, because a remote thread may
        # still be executing them. Leaking 4 KB is always the right trade
        # against decommitting a page under a running thread.
        self.leaked_pages = []
        self.h = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not self.h:
            raise RemoteCallError(
                f"OpenProcess(PROCESS_ALL_ACCESS) failed on pid {pid} "
                f"(error {ctypes.get_last_error()}). CreateRemoteThread needs "
                f"more rights than the viewer's read/write handle.")

    def close(self):
        if self.leaked_pages:
            self.log(f"  [stub] {len(self.leaked_pages)} page(s) left allocated "
                     f"in the client "
                     f"({', '.join(f'0x{p:08X}' for p in self.leaked_pages)}). "
                     f"A stuck remote thread was in each, so freeing them would "
                     f"crash it; they go when the client exits.")
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

    def _run(self, start, param, timeout_ms=15000):
        """Start a thread at `start` and return its exit code (EAX).

        A timeout raises RemoteThreadStuck rather than plain RemoteCallError,
        because the two demand opposite cleanup: if no thread was created the
        page is ours to free, but if a thread is still inside it, freeing is
        fatal. See run_stub.

        The wait was 5s and is now 15s. A getter returns instantly, but a call
        that contends with the main thread mid-turn can take far longer than a
        stopwatch suggests, and waiting is free while giving up is not.
        """
        th = kernel32.CreateRemoteThread(self.h, None, 0,
                                         ctypes.c_void_p(start),
                                         ctypes.c_void_p(param), 0, None)
        if not th:
            raise RemoteCallError(f"CreateRemoteThread(0x{start:08X}) failed "
                                  f"(error {ctypes.get_last_error()})")
        try:
            if kernel32.WaitForSingleObject(th, timeout_ms) != 0:
                raise RemoteThreadStuck(
                    f"remote thread at 0x{start:08X} did not finish within "
                    f"{timeout_ms}ms")
            code = wintypes.DWORD()
            kernel32.GetExitCodeThread(th, ctypes.byref(code))
            return code.value
        finally:
            kernel32.CloseHandle(th)

    def run_stub(self, code, what=""):
        """Run a self-contained stub. Returns EAX at its `ret`.

        THE PAGE IS NEVER FREED WHILE A THREAD MIGHT STILL BE IN IT. This used
        to free in a `finally`, which also fired on the timeout path: the wait
        gave up, we decommitted the page, and the still-running remote thread
        carried on into nothing. Two client crashes were logged with
        ACCESS_VIOLATION reading 0 AT instruction address 0, which is exactly
        what executing a freed page looks like.

        A leaked 4 KB page costs nothing. Killing the user's game costs a lot,
        and it is the one thing this module must never do.
        """
        page = self.alloc(max(len(code), 64), exec_=True)
        try:
            self.write(page, code)
            self.log(f"  [stub] 0x{page:08X} {len(code)}B  {what}")
            result = self._run(page, 0)
        except RemoteThreadStuck:
            self.leaked_pages.append(page)
            self.log(f"  [stub] LEAKING page 0x{page:08X}: a thread is still "
                     f"executing it and freeing it would crash the client")
            raise
        except BaseException:
            self.free(page)      # no thread was ever started; safe to reclaim
            raise
        self.free(page)
        return result

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

    def design_speed(self, design_tag):
        """Call the engine's speed getter for a design; returns the float bits.

        A design whose derived block reads -1 is NOT invalid — the cache is cold.
        Treating -1 as "unbuildable" froze the AI after every load, because a
        load leaves the block invalidated and only a getter call fills it. This
        both answers the question and warms the cache, so subsequent plain reads
        of ShipDesign:36 return the real number.
        """
        code = (mov_ecx_imm(design_tag + DESIGN_DATA_OFFSET) +
                mov_eax_imm(DESIGN_SPEED_GETTER) +
                CALL_EAX +
                SUB_ESP_4 + FSTP_ESP + POP_EAX +
                RET_4)
        return self.run_stub(code, f"design speed getter on 0x{design_tag:08X}")

    def hurry_production(self, planet_tag):
        """Spend cash to finish this planet's current production immediately.

        __thiscall on the PlanetProperties, no arguments. The engine does the
        whole transaction: it prices the remainder, debits Owner:8 and completes
        the build. Called rather than reproduced for the same reason as
        sell_facility -- STRATEGY.md 1.1 forbids writing the treasury, and a
        hurry that paid for itself would be indistinguishable from a free one.

        The engine's own refusals, which the caller should pre-check so a
        pointless remote thread is not spawned:
          * production at least half finished (progress / total >= 0.5)
          * cost <= Owner:8
          * production kind != 7 (a planet generating wealth has nothing to
            hurry), and Planet:300 == 0
        """
        code = (mov_ecx_imm(planet_tag + PLANET_PROPERTIES) +
                mov_eax_imm(HURRY_PRODUCTION) +
                CALL_EAX +
                RET_4)
        return self.run_stub(code, f"HurryProduction on 0x{planet_tag:08X}")

    def hurry_cost(self, planet_tag):
        """4 x (total production needed - progress); -1 when not hurryable."""
        code = (push_imm(planet_tag + PLANET_PROPERTIES) +
                mov_eax_imm(HURRY_COST) +
                CALL_EAX +
                ADD_ESP_4 +
                RET_4)
        v = self.run_stub(code, f"hurry cost on 0x{planet_tag:08X}")
        return None if v is None else struct.unpack("<i", struct.pack("<I", v))[0]

    def production_total(self, planet_tag, vftable_slot_addr):
        """PP vftable[0x74] — the production this build needs in total."""
        code = (mov_ecx_imm(planet_tag + PLANET_PROPERTIES) +
                mov_eax_imm(vftable_slot_addr) +
                CALL_EAX +
                RET_4)
        return self.run_stub(code, f"production total on 0x{planet_tag:08X}")

    def production_done(self, planet_tag):
        code = (mov_ecx_imm(planet_tag + PLANET_PROPERTIES) +
                mov_eax_imm(PRODUCTION_DONE) +
                CALL_EAX +
                RET_4)
        return self.run_stub(code, f"production done on 0x{planet_tag:08X}")

    def make_ship_command(self, block, order_type, target_alloc):
        """Run the engine's ShipCommand constructor on a block we hand it.

        __thiscall(int orderType, EJBO* target), ret 8 — callee-cleaned, so the
        stub pushes and forgets. Arguments go right to left, so the target is
        pushed first.

        `target_alloc` is an ALLOCATION START, not a tag. The constructor reads
        the object id at target[+4], which is tag-4 for every class we deal with,
        and getting this wrong is the same crash that mistaking a stream
        constructor for a copy constructor caused.

        Why this exists at all: the constructor is what turns a target OBJECT
        into the reference node that lands in Ship:56, via
        GetReferenceNode(target->objectId). Nothing else we have populates that
        field, and Ship::HasActiveTargetedOrder (0x004D9880) requires it to be
        non-null for order types 1 (Move), 7 (MoveNear) and 4 (Attack).
        """
        code = (push_imm(target_alloc) +
                push_imm(order_type) +
                mov_ecx_imm(block) +
                mov_eax_imm(SHIPCOMMAND_CTOR) +
                CALL_EAX +
                RET_4)
        return self.run_stub(
            code, f"ShipCommand({order_type}, 0x{target_alloc:08X}) "
                  f"-> 0x{block:08X}")

    def ship_set_command(self, ship_tag, cmd_block):
        """Ship::SetCommand — writes Ship:52/56/60/61 from the ShipCommand.

        __thiscall, ret 4, ECX = the ship's ALLOCATION start (tag - 8), because
        the function writes the order type at [ecx+0x3c] and Ship:52 sits at
        allocation + 60.

        The four fields are one value and the engine writes them together. Our
        own retarget_order writes Ship:52 alone, which is fine for Colonize and
        Scout — they carry coordinates and are not order types that consult
        Ship:56 — but is not fine for Move or Attack.
        """
        code = (push_imm(cmd_block) +
                mov_ecx_imm(ship_tag + SHIP_ALLOC_FROM_TAG) +
                mov_eax_imm(SHIP_SETCOMMAND) +
                CALL_EAX +
                RET_4)
        return self.run_stub(code, f"Ship::SetCommand on 0x{ship_tag:08X}")

    def relation_code(self, civ_tag, other_tag):
        """The diplomacy relation code between two civs, via the engine.

        0 Neutral, 1 War, 2 Cease-Fire, 3 Peace, 4 Alliance, and 0 also when
        there is no record at all — the engine treats "absent" and "Neutral" as
        the same thing.

        This is an engine call for a read, which is normally the wrong trade, but
        the container at Owner:248 is NOT a std::map: it holds four pointers
        where a map would hold {head, size}, and a hand-rolled traversal walked
        straight out of it into unrelated memory (one "node" it found contained
        the string "IllegalPosition"). Rather than reverse a custom container,
        ask the accessor that already knows how to search it.

        ECX is the container, and `other` must be the OWNER PRIMARY form
        (tag - 52), which is what the engine's own call sites pass.
        """
        code = (push_imm(other_tag + OWNER_PRIMARY_FROM_TAG) +
                mov_ecx_imm(civ_tag + OWNER_RELATIONS) +
                mov_eax_imm(GET_RELATION_CODE) +
                CALL_EAX +
                RET_4)
        return self.run_stub(code, f"relation code vs 0x{other_tag:08X}")

    def design_firepower(self, design_tag):
        """(light, heavy, third) via the engine's getters, warming the cache.

        ShipDesign:60/64/68 are lazy derived stats and read -1 until something
        asks for them, so a design that has never been inspected reports
        (0, 0, 0) and looks unarmed. Reading them through the getters both
        answers the question and leaves the plain reads correct afterwards.
        """
        out = []
        for fn in DESIGN_FP_GETTERS:
            v = self.run_stub(
                mov_ecx_imm(design_tag + DESIGN_DATA_OFFSET) +
                mov_eax_imm(fn) + CALL_EAX + RET_4,
                f"firepower getter 0x{fn:08X}")
            out.append(0 if v is None else struct.unpack("<i",
                       struct.pack("<I", v))[0])
        return tuple(out)

    def first_contact(self, civ_tag, other_tag):
        """Create the relation record between two civs if it does not exist.

        0x00534F90 skips silently when a record is already present or when the
        two civs are the same, so this is safe to call unconditionally. It picks
        the INITIAL code itself -- Neutral on this galaxy, because
        GetGameOption(2) reads 0 -- which is why declaring war is a second step.
        """
        code = (push_imm(other_tag + OWNER_PRIMARY_FROM_TAG) +
                mov_ecx_imm(civ_tag + OWNER_RELATIONS) +
                mov_eax_imm(FIRST_CONTACT) + CALL_EAX + RET_4)
        return self.run_stub(code, f"first contact 0x{civ_tag:08X} <-> "
                                   f"0x{other_tag:08X}")

    def relation_record(self, civ_tag, other_tag):
        """Pointer to the relation record, or None. Code lives at +4."""
        code = (push_imm(other_tag + OWNER_PRIMARY_FROM_TAG) +
                mov_ecx_imm(civ_tag + OWNER_RELATIONS) +
                mov_eax_imm(RELATION_FIND) + CALL_EAX + RET_4)
        p = self.run_stub(code, f"find relation record vs 0x{other_tag:08X}")
        return p if p and 0x10000 < p < 0x7FFF0000 else None

    def add_reputation(self, civ_tag, other_tag, delta):
        """Add to the standing score at record +0x10, engine-clamped to 100.

        The engine clamps only the HIGH side (cmp 0x64), so a large negative
        delta is not floored here -- pass sane values.
        """
        code = (push_imm(delta & 0xFFFFFFFF) +
                push_imm(other_tag + OWNER_PRIMARY_FROM_TAG) +
                mov_ecx_imm(civ_tag + OWNER_RELATIONS) +
                mov_eax_imm(REPUTATION_ADD) + CALL_EAX + RET_4)
        return self.run_stub(code, f"reputation {delta:+d} vs 0x{other_tag:08X}")

    def design_min_crew(self, design_tag):
        """ShipDesign:76 — the MINIMUM CREW a design needs to function.

        The report called :76 a "payload-delivery flag" because it reads 2 on
        the Colony Ship and troop designs and 1 on the weapon-only ones. The
        combat code settles it: the firepower accumulator computes the ship's
        crew count from its Ship:124 vector and SKIPS the ship entirely when
        that count is below this value, so an undercrewed warship contributes
        exactly zero damage. Read as a crew minimum, every observed value fits.

        Called rather than read because :76 is part of the lazy derived-stat
        block, which reads -1 until something asks for it. Our own Colony Ship
        reads -1 right now while the rival's reads 2.
        """
        code = (mov_ecx_imm(design_tag + DESIGN_DATA_OFFSET) +
                mov_eax_imm(DESIGN_MINCREW_GETTER) +
                CALL_EAX +
                RET_4)
        return self.run_stub(code, f"design min-crew on 0x{design_tag:08X}")

    # ── conscription ───────────────────────────────────────────────────
    def draft_cost(self, civ_tag, count=1):
        """What the engine will charge to draft `count` citizens, empire-wide.

        READ-ONLY, and deliberately the first of the pair to be built: §7 of
        STRATEGY.md says validate a new stub on a side-effect-free getter before
        pointing it at anything that mutates state, and this one is checkable
        against a formula derived independently from the disassembly:

            cost = sum over k in 0..count-1 of  5 * (T + k) + 105

        where T is the civ's military across the WHOLE empire — every planet's
        Planet:168 count plus every owned ship's crew. So the price rises by 5
        per unit already owned and conscription is cheapest early.

        ECX is Owner_primary + 0x30c, which is the civ-trait container; the
        function only uses it to reach the Owner behind it.
        """
        code = (push_imm(count) +
                mov_ecx_imm(civ_tag + DRAFT_COST_THIS) +
                mov_eax_imm(GET_DRAFT_COST) +
                CALL_EAX +
                RET_4)
        return self.run_stub(code, f"draft cost for {count} on 0x{civ_tag:08X}")

    def draft_is_free(self):
        """The engine skips the whole cost block when [0x0080AA00] < 150.

        That dword is the content/data version. Worth reading rather than
        assuming, because a controller that predicts a charge which never
        happens will keep refusing to draft on an affordability test it did not
        need to pass.
        """
        b = self.read(DRAFT_FREE_BELOW, 4)
        if not b or len(b) != 4:
            return None
        return struct.unpack("<I", b)[0] < 150

    def change_citizen_jobs(self, planet_tag, indices, new_job=DRAFT_JOB,
                            from_military=0):
        """Set the job of the listed citizens, letting the ENGINE do the rest.

        `__cdecl(Planet* allocation, container* indices, int newJobId,
                 bool bFromMilitaryList)` — four args, caller-cleaned, so it
        needs a stub: CreateRemoteThread passes exactly one.

        Called rather than reproduced for the STRATEGY.md §7 reason. With
        newJobId 3 the engine prices the draft, refuses outright if the cost
        exceeds Owner:8 — `jg` before any list write, so a rejected draft
        changes nothing — debits the treasury itself, and migrates the records
        into the military vector. Reproducing that by hand would mean writing
        Owner:8, which §1.1 forbids for exactly this reason: a draft that paid
        for itself would be indistinguishable from a free one.

        POINTER FORMS, both from the callee: arg1 is the planet's ALLOCATION
        start (tag - 8), because the callee reaches PlanetProperties as
        [arg1 + 0x60] and that is planet_tag + 88. arg2 is a CONTAINER whose
        vector sits 12 bytes in — the callee reads _Myfirst/_Mylast at +0xc/+0x10
        — not a bare vector.

        The container is a foreign allocation, and that is safe HERE though it
        was not for the order object: the original is a caller's stack local, so
        the engine reads it and never takes ownership of it. It is freed after
        the thread returns, never on the stuck path.
        """
        if not indices:
            raise RemoteCallError("no citizens selected to draft")
        n = len(indices)
        # [0 .. 24) container wrapper, [32 ..) the int array it points at.
        block = self.alloc(32 + 4 * n)
        arr = block + 32
        wrapper = bytearray(32)
        struct.pack_into("<III", wrapper, CONTAINER_VECTOR,
                         arr, arr + 4 * n, arr + 4 * n)   # first, last, end
        self.write(block, bytes(wrapper))
        self.write(arr, struct.pack(f"<{n}I", *indices))

        code = (push_imm(1 if from_military else 0) +
                push_imm(new_job) +
                push_imm(block) +
                push_imm(planet_tag + PLANET_ALLOC_FROM_TAG) +
                mov_eax_imm(CHANGE_CITIZEN_JOBS) +
                CALL_EAX +
                ADD_ESP_16 +
                RET_4)
        try:
            r = self.run_stub(code, f"draft {n} citizen(s) on "
                                    f"0x{planet_tag:08X} -> job {new_job}")
        except RemoteThreadStuck:
            raise                      # the page and this block both leak
        self.free(block)
        return r

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

    # ── selling a facility ────────────────────────────────────────────────
    # __thiscall bool PlanetProperties::SellFacility(int typeId, int count)
    # at 0x004F8580, ret 8 (callee-cleaned, so the stub pushes and forgets).
    #
    # ecx is the PlanetProperties, i.e. planet_allocation + 0x60, which is
    # planet_tag + 88 -- both call sites reach it with a literal `add ecx, 0x60`.
    #
    # It does the whole transaction itself: prices the facility, credits the
    # owner's treasury at Owner:8, and removes the units. That is exactly why we
    # call it instead of reproducing it. STRATEGY.md 1.1 forbids writing cash,
    # and a sale that paid itself would be indistinguishable from inventing
    # money -- letting the engine compute the payout keeps the rule intact.
    #
    #   price = round(facilityValue * 0.05) * count
    #
    # The 0.05 is the double at 0x753CF8. A military camp paying 75 implies a
    # facility value of 1500, which is the number to check the call against.
    def sell_facility(self, planet_tag, type_id, count=1):
        code = (push_imm(count) +
                push_imm(type_id) +
                mov_ecx_imm(planet_tag + PLANET_PROPERTIES) +
                mov_eax_imm(SELL_FACILITY) +
                CALL_EAX +
                RET_4)
        return self.run_stub(
            code, f"SellFacility(type {type_id} x{count}) on planet "
                  f"0x{planet_tag:08X}")
