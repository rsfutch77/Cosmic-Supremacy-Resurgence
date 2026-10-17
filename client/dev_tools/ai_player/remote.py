"""
remote.py: Call the game's own functions in its own process.

Runs short x86 stubs in the game via CreateRemoteThread, so any calling
convention works, including __thiscall. Argument counts come from each
function's `ret N`. Each stub runs on a fresh page and returns EAX as the
thread exit code. A wrong address or argument count crashes the client.

Addresses (CosmicSupremacy_Resurgence.exe, image base 0x00400000):
  0x0065165A  operator new(size_t), __cdecl, one arg, ret 0
  0x004E8420  order-object constructor, __thiscall, six stack args, ret 0x18
              (this, origin*, target*, Owner*, float, int, ptr). Origin and
              target are allocation starts (EJBO tag - 8); Owner is its tag-52
              primary form.
  vftable+0x30 on a ship returns the float for argument 4; no stack args.
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

# A citizen and a stationed military unit are the same 16-byte record in two
# vectors. Drafting sets the job id to 3; PlanetProperties::CommitPopulation
# migrates every job-3 record across.
CHANGE_CITIZEN_JOBS = 0x00574180  # __cdecl(Planet* alloc, container* indices,
                                  #         int newJobId, bool bFromMilitaryList)
GET_DRAFT_COST      = 0x00516060  # __thiscall(int count), ret 4
DRAFT_COST_THIS     = 728         # ECX = Owner_primary + 0x30c == civ_tag + 728
DRAFT_JOB           = 3           # job id 3 is crew/military
CONTAINER_VECTOR    = 12          # a container holds its vector 12 bytes in
DRAFT_FREE_BELOW    = 0x0080AA00  # content version; below 150 drafting is free
PLANET_ALLOC_FROM_TAG = -8        # Planet allocation starts at tag - 8

DESIGN_SIZE = 0x118           # 280 bytes
DESIGN_CTOR = 0x00546EC0      # __thiscall, no stack arguments

DESIGN_STREAM_CTOR = 0x005470A0

# ShipDesignData lives at EJBO tag + 4. Derived stats are lazy caches reset
# to -1 when parts change; each getter computes, caches and returns.
DESIGN_DATA_OFFSET  = 4        # from the EJBO tag
DESIGN_SPEED_GETTER = 0x005480A0   # __thiscall, no args, returns in st(0)
DESIGN_MINCREW_GETTER = 0x005490E0 # __thiscall, no args, returns in eax
# Firepower pools: 1 and 2 are anti-ship, 3 counts toward planetary defence.
DESIGN_FP_GETTERS = (0x00548CC0, 0x00548E20, 0x00548F80)

# A constructed ShipDesign shows these at its allocation start.
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
    """A remote thread did not finish in time. Its stub page must be leaked."""


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
        self._design_cache = {}
        self._pass_cache = {}
        self.cache_hits = 0
        self.cache_misses = 0
        # Stub pages left allocated because a remote thread may still run them.
        self.leaked_pages = []
        self.h = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not self.h:
            raise RemoteCallError(
                f"OpenProcess(PROCESS_ALL_ACCESS) failed on pid {pid} "
                f"(error {ctypes.get_last_error()}). CreateRemoteThread needs "
                f"more rights than the viewer's read/write handle.")
        self._selftest()

    def _selftest(self):
        """Prove the remote-call machinery works."""
        probe = 0x5EE1F00D
        code = mov_eax_imm(probe) + RET_4
        got = self.run_stub(code, "self-test")
        if got != probe:
            raise RemoteCallError(
                f"remote-call self-test failed: expected 0x{probe:08X}, got "
                f"{got!r}. Engine calls do not work in this process, so every "
                f"rule that needs one would fail silently; refusing to start "
                f"rather than play a hollow game.")

    def begin_pass(self):
        """Drop everything that is only true of the turn just gone."""
        self._pass_cache.clear()

    def invalidate(self, *keys):
        """Forget specific cached facts after we change the thing they describe."""
        for k in keys:
            self._pass_cache.pop(k, None)

    def _cached(self, store, key, compute):
        if key in store:
            self.cache_hits += 1
            return store[key]
        self.cache_misses += 1
        value = compute()
        # A failed call returns None and is not cached.
        if value is not None:
            store[key] = value
        return value

    def cache_summary(self):
        total = self.cache_hits + self.cache_misses
        if not total:
            return "no engine calls"
        return (f"{self.cache_hits}/{total} engine call(s) served from cache "
                f"({100 * self.cache_hits // total}%)")

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
        """Start a thread at `start` and return its exit code (EAX)."""
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
        """Run a self-contained stub. Returns EAX at its `ret`."""
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

    def operator_new(self, size=ORDER_SIZE):
        """Allocate from the engine's heap, so the engine may free it.

        __cdecl with one argument, the shape CreateRemoteThread passes, so no
        stub is needed.
        """
        p = self._run(OPERATOR_NEW, size)
        if not p:
            raise RemoteCallError("operator new returned NULL")
        self.log(f"  [new] {size} bytes at 0x{p:08X} (engine heap)")
        return p

    def ship_speed_float(self, ship_alloc):
        """Call the ship's vftable[0x30] getter and return its float bits."""
        code = (mov_ecx_imm(ship_alloc) +
                b"\x8B\x01" +              # mov eax, [ecx]        vftable
                b"\x8B\x40\x30" +          # mov eax, [eax+0x30]
                CALL_EAX +                 # float returned in ST0
                SUB_ESP_4 + FSTP_ESP + POP_EAX +
                RET_4)
        return self.run_stub(code, "ship vftable[0x30] getter")

    def construct_design(self, block):
        """Default-construct a ShipDesign, leaving its five part vectors empty."""
        code = (mov_ecx_imm(block) +
                mov_eax_imm(DESIGN_CTOR) +
                CALL_EAX +
                RET_4)
        return self.run_stub(code, f"ShipDesign default ctor -> 0x{block:08X}")

    def verify_design(self, block):
        """Check that a block really is a constructed ShipDesign."""
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
        """Call the engine's speed getter for a design; returns the float bits."""
        def call():
            code = (mov_ecx_imm(design_tag + DESIGN_DATA_OFFSET) +
                    mov_eax_imm(DESIGN_SPEED_GETTER) +
                    CALL_EAX +
                    SUB_ESP_4 + FSTP_ESP + POP_EAX +
                    RET_4)
            return self.run_stub(code, f"design speed getter on 0x{design_tag:08X}")
        return self._cached(self._design_cache, ("speed", design_tag), call)

    def hurry_production(self, planet_tag):
        """Spend cash to finish this planet's current production immediately."""
        code = (mov_ecx_imm(planet_tag + PLANET_PROPERTIES) +
                mov_eax_imm(HURRY_PRODUCTION) +
                CALL_EAX +
                RET_4)
        return self.run_stub(code, f"HurryProduction on 0x{planet_tag:08X}")

    def hurry_cost(self, planet_tag):
        """4 x (total production needed - progress); -1 when not hurryable."""
        def call():
            code = (push_imm(planet_tag + PLANET_PROPERTIES) +
                    mov_eax_imm(HURRY_COST) +
                    CALL_EAX +
                    ADD_ESP_4 +
                    RET_4)
            v = self.run_stub(code, f"hurry cost on 0x{planet_tag:08X}")
            return None if v is None else struct.unpack("<i", struct.pack("<I", v))[0]
        return self._cached(self._pass_cache, ("hurry_cost", planet_tag), call)

    def production_total(self, planet_tag, vftable_slot_addr):
        """PP vftable[0x74]: the production this build needs in total."""
        def call():
            code = (mov_ecx_imm(planet_tag + PLANET_PROPERTIES) +
                    mov_eax_imm(vftable_slot_addr) +
                    CALL_EAX +
                    RET_4)
            return self.run_stub(code, f"production total on 0x{planet_tag:08X}")
        return self._cached(self._pass_cache,
                            ("prod_total", planet_tag, vftable_slot_addr), call)

    def production_done(self, planet_tag):
        def call():
            code = (mov_ecx_imm(planet_tag + PLANET_PROPERTIES) +
                    mov_eax_imm(PRODUCTION_DONE) +
                    CALL_EAX +
                    RET_4)
            return self.run_stub(code, f"production done on 0x{planet_tag:08X}")
        return self._cached(self._pass_cache, ("prod_done", planet_tag), call)

    def make_ship_command(self, block, order_type, target_alloc):
        """Run the engine's ShipCommand constructor on a block we hand it."""
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
        """Ship::SetCommand: writes Ship:52/56/60/61 from the ShipCommand.

        ECX is the ship's allocation start (tag - 8).
        """
        code = (push_imm(cmd_block) +
                mov_ecx_imm(ship_tag + SHIP_ALLOC_FROM_TAG) +
                mov_eax_imm(SHIP_SETCOMMAND) +
                CALL_EAX +
                RET_4)
        return self.run_stub(code, f"Ship::SetCommand on 0x{ship_tag:08X}")

    def relation_code(self, civ_tag, other_tag):
        """The diplomacy relation code between two civs, via the engine.

        0 Neutral (also when there is no record), 1 War, 2 Cease-Fire, 3 Peace,
        4 Alliance.
        """
        code = (push_imm(other_tag + OWNER_PRIMARY_FROM_TAG) +
                mov_ecx_imm(civ_tag + OWNER_RELATIONS) +
                mov_eax_imm(GET_RELATION_CODE) +
                CALL_EAX +
                RET_4)
        return self.run_stub(code, f"relation code vs 0x{other_tag:08X}")

    def design_firepower(self, design_tag):
        """(light, heavy, third) via the engine's getters, warming the cache.

        ShipDesign:60/64/68 read -1 until a getter computes them.
        """
        def call():
            out = []
            for fn in DESIGN_FP_GETTERS:
                v = self.run_stub(
                    mov_ecx_imm(design_tag + DESIGN_DATA_OFFSET) +
                    mov_eax_imm(fn) + CALL_EAX + RET_4,
                    f"firepower getter 0x{fn:08X}")
                out.append(0 if v is None else struct.unpack("<i",
                           struct.pack("<I", v))[0])
            return tuple(out)
        # Three remote threads per call, for a value that changes only on refit.
        return self._cached(self._design_cache, ("firepower", design_tag), call)

    def first_contact(self, civ_tag, other_tag):
        """Create the relation record between two civs if it does not exist.

        Safe to call unconditionally. The engine picks the initial code
        (Neutral when GetGameOption(2) reads 0).
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
        """Add to the standing score at record +0x10, clamped above at 100 only."""
        code = (push_imm(delta & 0xFFFFFFFF) +
                push_imm(other_tag + OWNER_PRIMARY_FROM_TAG) +
                mov_ecx_imm(civ_tag + OWNER_RELATIONS) +
                mov_eax_imm(REPUTATION_ADD) + CALL_EAX + RET_4)
        return self.run_stub(code, f"reputation {delta:+d} vs 0x{other_tag:08X}")

    def design_min_crew(self, design_tag):
        """ShipDesign:76: the minimum crew a design needs to function."""
        def call():
            code = (mov_ecx_imm(design_tag + DESIGN_DATA_OFFSET) +
                    mov_eax_imm(DESIGN_MINCREW_GETTER) +
                    CALL_EAX +
                    RET_4)
            return self.run_stub(code, f"design min-crew on 0x{design_tag:08X}")
        return self._cached(self._design_cache, ("min_crew", design_tag), call)

    def draft_cost(self, civ_tag, count=1):
        """What the engine will charge to draft `count` citizens (read-only).

        The price rises with the civ's military across the whole empire
        (every planet's Planet:168 count plus every owned ship's crew).
        """
        def call():
            code = (push_imm(count) +
                    mov_ecx_imm(civ_tag + DRAFT_COST_THIS) +
                    mov_eax_imm(GET_DRAFT_COST) +
                    CALL_EAX +
                    RET_4)
            return self.run_stub(code,
                                 f"draft cost for {count} on 0x{civ_tag:08X}")
        # Per pass only; callers that change military call invalidate_draft_cost().
        return self._cached(self._pass_cache, ("draft_cost", civ_tag, count),
                            call)

    def invalidate_draft_cost(self, civ_tag=None):
        """Forget cached draft prices after the military count changes."""
        for key in [k for k in self._pass_cache
                    if k[0] == "draft_cost"
                    and (civ_tag is None or k[1] == civ_tag)]:
            del self._pass_cache[key]

    def draft_is_free(self):
        """True when drafting is free: the content version at [0x0080AA00] is below 150."""
        b = self.read(DRAFT_FREE_BELOW, 4)
        if not b or len(b) != 4:
            return None
        return struct.unpack("<I", b)[0] < 150

    def change_citizen_jobs(self, planet_tag, indices, new_job=DRAFT_JOB,
                            from_military=0):
        """Set the job of the listed citizens; the engine does the rest."""
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
        """Run the engine's order constructor on an engine-allocated block."""
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

    # __thiscall bool PlanetProperties::SellFacility(int typeId, int count), ret 8.
    # The engine prices the sale, credits Owner:8 and removes the units, so cash
    # is never written here (STRATEGY.md 1.1). Price is
    # round(facilityValue * 0.05) * count; the 0.05 is the double at 0x753CF8.
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
