"""
ejbo_viewer.py — Live EJBO Object Memory Viewer for Cosmic Supremacy

Scans game process memory for EJBO-tagged objects, classifies them by type,
and serves a live-updating browser dashboard on localhost where you can:

  - See all game objects grouped by type (Planet, Ship, Admiral, etc.)
  - Compare objects of the same type side-by-side
  - Watch values update in real time as you interact with the game
  - Annotate fields to document what each offset means
  - Export current state + annotations as CSV
  - Double-click any value to POKE (write) a new value into game memory

Usage:
    python ejbo_viewer.py [--port 8080] [--refresh 2]
    Then open http://localhost:8080 in your browser.

Requires: Windows (uses ReadProcessMemory API)
"""
import ctypes
from ctypes import wintypes
import struct
import sys
import os
import json
import time
import csv
import io
import argparse
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── Windows API ────────────────────────────────────────────────────────────
PROCESS_VM_READ           = 0x0010
PROCESS_VM_WRITE          = 0x0020
PROCESS_VM_OPERATION      = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT                = 0x1000
PAGE_READWRITE            = 0x04
PAGE_WRITECOPY            = 0x08
PAGE_EXECUTE_READWRITE    = 0x40
PAGE_EXECUTE_WRITECOPY    = 0x80
PAGE_GUARD                = 0x100

psapi    = ctypes.WinDLL("psapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress",       ctypes.c_void_p),
        ("AllocationBase",    ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize",        ctypes.c_size_t),
        ("State",             wintypes.DWORD),
        ("Protect",           wintypes.DWORD),
        ("Type",              wintypes.DWORD),
    ]

# Processes whose names match the "CosmicSupremacy" substring but are NOT the
# game. The launcher is the whole reason this list exists: it ships as
# CosmicSupremacyLauncher.exe, sits beside the client in every release, and is
# the FIRST match EnumProcesses happens to return often enough to matter. An AI
# that attached to it would read a Python process's memory, find no civs, and
# report "no civ named 'BadGuy'" — a wrong answer that looks like a game bug.
# game_cycle.py already carries the same exclusion for Stop-Process; this is the
# read side of it.
NOT_THE_GAME = ("cosmicsupremacylauncher.exe",)


def find_pid(exe_substr="CosmicSupremacy"):
    arr = (wintypes.DWORD * 4096)()
    cb  = ctypes.c_ulong()
    if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(cb)):
        raise OSError("EnumProcesses failed")
    count = cb.value // ctypes.sizeof(wintypes.DWORD)
    for i in range(count):
        pid = arr[i]
        if pid == 0:
            continue
        h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(260)
            n = psapi.GetModuleBaseNameW(h, None, buf, 260)
            if (n and exe_substr.lower() in buf.value.lower()
                    and buf.value.lower() not in NOT_THE_GAME):
                return pid, buf.value
        finally:
            kernel32.CloseHandle(h)
    return None, None

def enum_writable_regions(h):
    writable = PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY
    addr    = 0
    regions = []
    mbi     = MEMORY_BASIC_INFORMATION()
    while True:
        ret = kernel32.VirtualQueryEx(h, ctypes.c_void_p(addr),
                                       ctypes.byref(mbi), ctypes.sizeof(mbi))
        if ret == 0:
            break
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize
        if (mbi.State == MEM_COMMIT
            and not (mbi.Protect & PAGE_GUARD)
            and mbi.Protect & writable):
            regions.append((base, size))
        addr = base + size
        if addr >= 0x7fff0000:
            break
    return regions

def read_bytes(h, addr, size):
    buf  = (ctypes.c_ubyte * size)()
    read = ctypes.c_size_t()
    ok   = kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(read))
    if ok and read.value > 0:
        return bytes(buf[:read.value])
    return None

def write_bytes(h, addr, data):
    """Write raw bytes to process memory. Returns True on success."""
    buf     = (ctypes.c_ubyte * len(data))(*data)
    written = ctypes.c_size_t()
    ok = kernel32.WriteProcessMemory(h, ctypes.c_void_p(addr),
                                      buf, len(data), ctypes.byref(written))
    return bool(ok and written.value == len(data))

# ── EJBO Scanner ───────────────────────────────────────────────────────────
EJBO_TAG = b'EJBO'

# Fallback type map, used only if RTTI resolution fails. The pointer at EJBO-8
# is a vftable; resolve_class_name() reads the class name out of the binary's
# RTTI instead of relying on this table.
KNOWN_TYPES = {
    0x00768ddc: "Planet",
    0x00784934: "Admiral",
    0x00771df8: "ShipDesign",
    0x00771df0: "ShipDesign",  # secondary design vftable (multiple inheritance)
    0x00768B04: "Ship",        # actual ship instances (floats for HP/coords)
    0x007707E0: "Owner",       # civilization-level stats (RTTI name: Owner)
}

# ── RTTI class-name resolution ─────────────────────────────────────────────
# MSVC emits, for every polymorphic class, a vftable whose [-4] slot points at
# an RTTICompleteObjectLocator. Locator+12 is a TypeDescriptor whose +8 holds
# the mangled class name (".?AVPlanet@@"). Walking that chain live means new
# object types name themselves instead of piling into an "Unknown" bucket.
MODULE_LO = 0x00400000   # image base
MODULE_HI = 0x00C30000   # past the end of .rsrc

def _demangle(mangled):
    """'.?AVPlanet@@' -> 'Planet'; template forms -> 'Base<Arg>'."""
    if not (mangled.startswith(".?A") and mangled.endswith("@@")):
        return None
    core = mangled[4:-2]
    if core.startswith("?$"):                      # template specialisation
        parts = [p for p in core[2:].split("@") if p]
        if len(parts) > 1:
            return f"{parts[0]}<{','.join(p.lstrip('V') for p in parts[1:])}>"
        return parts[0] if parts else None
    return core.split("@")[0] or None

def resolve_class_name(h, vftable_va, cache):
    """Resolve a vftable address to its RTTI class name, or None."""
    if vftable_va in cache:
        return cache[vftable_va]
    name = None
    if MODULE_LO < vftable_va < MODULE_HI and vftable_va % 4 == 0:
        raw = read_bytes(h, vftable_va - 4, 4)
        if raw and len(raw) == 4:
            col = struct.unpack("<I", raw)[0]
            if MODULE_LO < col < MODULE_HI:
                loc = read_bytes(h, col, 16)
                if loc and len(loc) == 16:
                    sig, _off, _cd, td = struct.unpack("<IIII", loc)
                    if sig == 0 and MODULE_LO < td < MODULE_HI:
                        blob = read_bytes(h, td + 8, 128)
                        if blob:
                            end = blob.find(b'\x00')
                            if end > 3:
                                try:
                                    name = _demangle(blob[:end].decode("ascii"))
                                except UnicodeDecodeError:
                                    name = None
    cache[vftable_va] = name
    return name

# ── Pointer resolution ─────────────────────────────────────────────────────
# Most of the game is NOT EJBO-tagged (Facility, Production, Scan, Treaty...),
# but every polymorphic class keeps its vftable at offset 0, so the class at the
# far end of any pointer can be named through the same RTTI chain. Two shapes
# are handled: a direct object pointer, and the reference-node idiom where the
# field points at a small node whose first dword is the target's allocation
# start (see Planet:40 ownership).
PTR_LO, PTR_HI = 0x00010000, 0x7FFF0000

# Sentinels whose first dword is 0, so RTTI cannot name them.
NULL_REF_NODE = 0x00857C54
KNOWN_STATICS = {
    NULL_REF_NODE: "null-reference node",
}

# Planet:40 is the owner link. Colonised planets point at a node whose node[0]
# is an Owner; everything else shares the static null node in .data. The viewer
# splits the two into separate tabs — there are ~500 uncolonised planets and
# rendering them all is what makes the tab crawl.
OWNER_LINK_OFFSET = 40

def is_owned(owner_field):
    """True if an owner-link field resolves to a real Owner. Prefers the
    RTTI-resolved description and falls back to the null-node address when the
    pointer could not be walked."""
    node = owner_field["u32"]
    if node in (0, NULL_REF_NODE):
        return False
    desc = owner_field.get("ptr")
    if desc:
        return "Owner" in desc
    return True

def resolve_pointer(h, value, rtti_cache, ptr_cache, ejbo_by_start):
    """Describe what `value` points at, or None. Cached by pointer value."""
    if not (PTR_LO < value < PTR_HI) or value % 4:
        return None
    if value in ptr_cache:
        return ptr_cache[value]
    desc = KNOWN_STATICS.get(value)
    if desc is None:
        first = read_bytes(h, value, 4)
        if first and len(first) == 4:
            head = struct.unpack("<I", first)[0]
            cls = resolve_class_name(h, head, rtti_cache)
            if cls:
                desc = f"-> {cls}"
            elif PTR_LO < head < PTR_HI:
                # reference-node idiom: node[0] is the target object's start
                hit = ejbo_by_start.get(head)
                if hit:
                    desc = f"-> node -> {hit[0]} #{hit[1]}"
                else:
                    inner = read_bytes(h, head, 4)
                    if inner and len(inner) == 4:
                        c2 = resolve_class_name(
                            h, struct.unpack("<I", inner)[0], rtti_cache)
                        if c2:
                            desc = f"-> node -> {c2}"
    ptr_cache[value] = desc
    return desc

# ── Object extents ─────────────────────────────────────────────────────────
# The window read after the EJBO tag must not run past the end of the object,
# or the viewer shows neighbouring heap allocations as if they were fields —
# and any annotation made on them is junk. Real sizes are derived at scan time
# from the stride between consecutive same-class tags (see measure_extents).
READ_BEFORE       = 32    # covers the header plus the preceding object's tail
DEFAULT_READ_AFTER = 192  # used when a class has too few instances to measure
MAX_READ_AFTER    = 2048  # ceiling, keeps per-refresh reads bounded. Owner alone
                          # is ~1356 bytes, so this must stay well above 608.

# Measured 2026-07-27 against a TestBed galaxy (201 objects). Config is a
# starting point only; measure_extents() overrides it whenever it can measure.
CLASS_EXTENTS = {
    "Planet":     600,   # stride 616/608, 8-byte header
    "Admiral":     88,   # stride is 288 but the object ends near +88: the gap
                         # was overwritten by the game's log buffer during a
                         # turn, so it is a sub-allocation, not Admiral data
    "Sun":         96,   # stride 104, 8-byte header
    # Sparse classes: no dense array, so no stride. Bounded instead by where
    # instances stop agreeing structurally, and by the NT heap block header
    # (0x0803xxxx/0x0804xxxx with a matching low half) that follows an object.
    "Ship":       152,   # last agreeing dword +148; +152 is heap metadata
    "Owner":     1344,   # agree to +1356, heap header at +1360; four
                         # std::strings line up at +368/+528/+1144/+1312
    # Part-category vectors sit at a 24-byte stride: +128 chassis, +152 scanner,
    # +176 engine, +200 weapon, and there is room for more above that. 192 cut the
    # weapon vector off entirely, so the window must reach past it.
    "ShipDesign": 320,
}

# How many bytes BEFORE the EJBO tag belong to the object. For a single-inheritance
# class this is just the 8-byte header, and READ_BEFORE's 32 is generous. Multiple-
# inheritance classes are different: the allocation starts at the PRIMARY vftable,
# which sits well before the tag, and the fields in between are real object data.
#
# Owner was found this way — the .data vector at 0x0082AA28 holds Owner* values
# pointing at tag-52, which is where Owner's primary vftable lives. The 52 bytes
# in front of the tag hold the CIV NAME, so reading only from tag-8 hid it
# completely. Note that the reference-node idiom points at tag-8 instead: under
# multiple inheritance both are valid pointers to the same object, so anything
# resolving an Owner* has to accept either.
CLASS_FRONT = {
    "Owner":      52,   # -52 vftable#1, -48 galaxy welcome text, -44 name string
                        # (_Mysize -28, _Myres -24), -8 vftable#2, -4 object id
    "ShipDesign": 12,   # second vftable at -12; measure_extents already knows
}

# Offset of the object's display-name std::string, relative to the EJBO tag. The
# generic name sniffer looks for printable runs and mis-slices SSO buffers (it
# read Owner 'GoodGuy' as 'Guy'), so classes whose name field is known are decoded
# properly instead: 16-byte SSO buffer, then _Mysize, then _Myres.
# Located by scanning each class's window for the SSO signature (_Myres == 15)
# across every instance, rather than by guessing at printable runs.
CLASS_NAME_STRING = {
    "Owner":      -44,  # 'GoodGuy' / 'BadGuy'; Owner has four more strings at
                        # +368/+528/+1144/+1312, all empty in a fresh galaxy
    "Planet":      52,  # second Planet string at +428, purpose unknown
    "ShipDesign":   8,  # 'Colony Ship'
    "Sun":         52,
    "Fleet":      132,
}

def read_std_string(raw, pos):
    """Decode an MSVC std::string at raw[pos:]. Returns None if it is not one.
    Only the SSO case is handled from the buffer; longer strings live off-heap
    and the caller has to follow the pointer itself."""
    if pos < 0 or pos + 24 > len(raw):
        return None
    size = struct.unpack_from("<I", raw, pos + 16)[0]
    res  = struct.unpack_from("<I", raw, pos + 20)[0]
    if res != 15 or size > 15:
        return None
    chunk = raw[pos:pos + size]
    if not all(0x20 <= c < 0x7f for c in chunk):
        return None
    return chunk.decode("ascii")

def scan_for_ejbo(h, regions):
    """Scan all writable regions for the EJBO tag. Returns list of absolute
    addresses where the tag starts."""
    found = []
    for base, size in regions:
        data = read_bytes(h, base, size)
        if data is None:
            continue
        off = 0
        while True:
            idx = data.find(EJBO_TAG, off)
            if idx < 0:
                break
            found.append(base + idx)
            off = idx + 4
    return sorted(found)

def classify_object(h, ejbo_addr, rtti_cache=None):
    """Classify an object from its vftable. EJBO-8 always holds the primary
    vftable; EJBO-12 holds a second one only for multiple-inheritance classes
    (ShipDesign), and is ordinary field data otherwise — so it is tried second
    and only accepted if RTTI validates it."""
    header = read_bytes(h, ejbo_addr - 12, 12)
    if header is None or len(header) < 12:
        return "Unknown", 0, 0
    type_ptr1 = struct.unpack_from("<I", header, 0)[0]  # EJBO-12
    type_ptr2 = struct.unpack_from("<I", header, 4)[0]  # EJBO-8
    obj_id    = struct.unpack_from("<I", header, 8)[0]  # EJBO-4
    type_name = None
    if rtti_cache is not None:
        type_name = (resolve_class_name(h, type_ptr2, rtti_cache)
                     or resolve_class_name(h, type_ptr1, rtti_cache))
    if not type_name:
        type_name = KNOWN_TYPES.get(type_ptr2,
                    KNOWN_TYPES.get(type_ptr1, "Unknown"))
    return type_name, obj_id, type_ptr2

def measure_extents(h, addrs, class_of, rtti_cache):
    """Derive how many bytes after the EJBO tag actually belong to each class.

    In a dense array the gap between consecutive same-class tags is an UPPER
    BOUND on the object's allocation size, so `min(stride) - header` is a safe
    read window. Header is 8 bytes (vftable + object id) unless EJBO-12 holds a
    second vftable that RTTI resolves to the same class, in which case it is 12.

    Stride is only an upper bound, not the size: a class may allocate sub-objects
    between its instances, leaving unrelated heap inside the stride. Admiral is
    the known case — stride 288 but the object ends near +88, and the gap was
    later reused by the game's log buffer. Where a class has a verified smaller
    size, CLASS_EXTENTS overrides the measurement.
    """
    by_class = {}
    for a in addrs:
        by_class.setdefault(class_of.get(a, "Unknown"), []).append(a)

    # Hard bound: an object can never extend into the next object's header,
    # whatever its class. 12 bytes covers the widest header seen (two vftables
    # plus the object id).
    ordered   = sorted(addrs)
    next_tag  = {a: b for a, b in zip(ordered, ordered[1:])}

    extents, notes = {}, []
    for cls, lst in by_class.items():
        lst.sort()
        # header size: is EJBO-12 a second vftable for this same class?
        header = 8
        ptrs = set()
        for a in lst:
            raw = read_bytes(h, a - 12, 4)
            if raw and len(raw) == 4:
                ptrs.add(struct.unpack("<I", raw)[0])
        if len(ptrs) == 1:
            only = next(iter(ptrs))
            if resolve_class_name(h, only, rtti_cache) == cls:
                header = 12

        # Never read into the next object's header, whatever its class.
        gaps_any = [next_tag[a] - a - 12 for a in lst if a in next_tag]
        hard_cap = min(gaps_any) if gaps_any else MAX_READ_AFTER

        strides  = [b - a for a, b in zip(lst, lst[1:])]
        # An allocation size repeats across a dense array. A stride seen only
        # once is just the distance to the next heap region and says nothing
        # about the object's size, so fall back to the configured value.
        repeated = [s for s in strides if strides.count(s) > 1]
        if repeated:
            size  = min(repeated)
            basis = f"stride {size}, header {header}"
            limit = max(0, size - header)
        else:
            limit = CLASS_EXTENTS.get(cls, DEFAULT_READ_AFTER)
            basis = f"only {len(lst)} instance(s), no repeated stride — configured"

        # A verified size always wins over a measured stride, which only bounds it.
        cfg = CLASS_EXTENTS.get(cls)
        if cfg is not None and cfg < limit:
            limit, basis = cfg, f"{basis}, overridden by verified size"
        extents[cls] = min(limit, hard_cap, MAX_READ_AFTER)
        capped = " [capped by next object]" if hard_cap < min(limit, MAX_READ_AFTER) else ""
        notes.append(f"{cls}: {basis} -> {extents[cls]}{capped}")
    return extents, notes

def read_object_fields(h, ejbo_addr, read_after=DEFAULT_READ_AFTER,
                       read_before=READ_BEFORE, cls=None):
    """Read the raw bytes around an EJBO object and decode into field list.

    read_before must be a multiple of 4 so field offsets stay dword-aligned."""
    start = ejbo_addr - read_before
    total = read_before + 4 + read_after   # before + EJBO tag + after
    raw   = read_bytes(h, start, total)
    if raw is None:
        return None, ""
    # Decode every 4-byte dword as multiple types
    fields = []
    for i in range(0, len(raw) - 3, 4):
        offset = i - read_before  # offset relative to EJBO
        b = raw[i:i+4]
        u32 = struct.unpack("<I", b)[0]
        i32 = struct.unpack("<i", b)[0]
        f32 = struct.unpack("<f", b)[0]
        # ASCII representation
        asc = ''.join(chr(x) if 0x20 <= x < 0x7f else '.' for x in b)
        # Check if it looks like a reasonable float
        f_str = ""
        if b != b'\x00\x00\x00\x00' and b != b'\xff\xff\xff\xff':
            exp = (u32 >> 23) & 0xFF
            if 0 < exp < 255 and abs(f32) > 1e-6 and abs(f32) < 1e8:
                f_str = f"{f32:.4f}"
        fields.append({
            "offset": offset,
            "hex":    f"0x{u32:08X}",
            "u32":    u32,
            "i32":    i32,
            "f32":    f_str,
            "ascii":  asc,
            "raw":    b.hex(),
        })
    # Try to extract a name string — search EJBO+4 through EJBO+16 for
    # the start of a printable ASCII run (name may be at different offsets
    # depending on object type). Negative offsets are included for multiple-
    # inheritance classes, whose fields start before the tag: Owner's civ name
    # is at -44.
    name = ""
    known = CLASS_NAME_STRING.get(cls)
    if known is not None:
        name = read_std_string(raw, read_before + known) or ""
    for name_off in ([] if name else (8, 12, 16, 4)):
        pos = read_before + 4 + name_off
        if pos + 4 > len(raw):
            continue
        name_bytes = raw[pos:pos+32]
        # Must start with a printable char
        if not (0x20 < name_bytes[0] < 0x7f):
            continue
        null_pos = name_bytes.find(b'\x00')
        if null_pos < 2:
            continue
        candidate = name_bytes[:null_pos]
        if all(0x20 <= c < 0x7f for c in candidate):
            try:
                name = candidate.decode('ascii')
                break
            except:
                continue
    return fields, name

# ── Annotations persistence ────────────────────────────────────────────────
# Resolve next to this script, not the cwd — the viewer is often launched with
# an absolute path from the repo root, which would silently load zero annotations.
ANNOTATIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "ejbo_annotations.json")

def load_annotations():
    if os.path.exists(ANNOTATIONS_FILE):
        # Encoding must be explicit: the file contains non-ASCII punctuation and
        # Windows would otherwise decode it as cp1252, turning every em-dash into
        # mojibake that the next save then escapes permanently.
        with open(ANNOTATIONS_FILE, "r", encoding="utf-8") as f:
            text = f.read()
        # Duplicate keys are legal JSON and silently resolve to the LAST value,
        # so a hand-edited file can carry two annotations for one offset while the
        # viewer shows only one. The first rewrite then destroys the hidden one.
        # Planet:356/360/364 each sat duplicated for several commits this way.
        seen, dupes = set(), []
        def _hook(pairs):
            for k, _ in pairs:
                (dupes.append(k) if k in seen else seen.add(k))
            return dict(pairs)
        ann = json.loads(text, object_pairs_hook=_hook)
        if dupes:
            print(f"[annotations] WARNING duplicate keys, only the last of each "
                  f"is in effect and a save will drop the rest: "
                  f"{sorted(set(dupes))}")
        return ann
    return {}

def save_annotations(ann):
    with open(ANNOTATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(ann, f, indent=2, ensure_ascii=False)

# ── Global state ───────────────────────────────────────────────────────────
class ViewerState:
    def __init__(self):
        self.pid         = None
        self.proc_name   = ""
        self.handle      = None
        self.ejbo_addrs  = []          # raw EJBO addresses
        self.objects     = []          # [{addr, type, id, type_ptr, name, fields}, ...]
        self.prev_values = {}          # addr -> {offset: u32} for change detection
        self.rtti_cache  = {}          # vftable VA -> class name (or None)
        self.ptr_cache   = {}          # pointer value -> description (or None)
        self.extents     = {}          # class name -> bytes readable after the tag
        self.annotations = load_annotations()
        self.lock        = threading.Lock()
        self.last_update = 0
        self.scan_count  = 0
        # Warm-scan state; see ViewerState.scan.
        self.hot_regions = []          # regions that have held objects
        self.prev_regions = []         # last enum, to spot new or grown ones
        self.scans_since_full = 0
        self._extent_notes = None      # so extents are reported on change only

    def connect(self):
        pid, name = find_pid("CosmicSupremacy")
        if not pid:
            return False
        if self.handle:
            kernel32.CloseHandle(self.handle)
        self.pid        = pid
        self.proc_name  = name
        self.rtti_cache = {}   # heap addresses are per-process; vftables are not,
                               # but a fresh cache costs one read per class
        self.ptr_cache  = {}
        self.handle    = kernel32.OpenProcess(
            PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION,
            False, pid)
        return bool(self.handle)

    def _is_process_alive(self):
        """Check if the attached process is still running."""
        if not self.handle:
            return False
        code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code))
        if not ok:
            return False
        STILL_ACTIVE = 259  # 0x103
        return code.value == STILL_ACTIVE

    def reconnect_if_needed(self):
        """Detect game restart (PID gone) and auto-reconnect + rescan."""
        if self._is_process_alive():
            return False  # still alive, no reconnect needed
        old_pid = self.pid
        print(f"[!] PID {old_pid} gone — searching for new process...")
        if self.connect():
            print(f"[+] Reconnected to PID {self.pid} ({self.proc_name})")
            n = self.scan()
            print(f"[+] Rescanned: {n} EJBO objects")
            return True
        else:
            with self.lock:
                self.objects = []
                self.ejbo_addrs = []
                self.scan_count = 0
                self.last_update = time.time()
            return False

    # A full sweep reads the whole writable address space looking for the tag.
    # Measured: 118 MB at ~103 MB/s = 1.15s, which is 87% of a scan, while every
    # object read that follows it costs 0.17s.
    #
    # It is also almost entirely wasted. All 682 objects in a live galaxy sit in
    # TWO heap regions totalling 2.44 MB -- 2.1% of what gets swept -- and
    # sweeping just those finds the identical set in 0.021s.
    #
    # So a warm scan sweeps the regions that HAVE held objects, plus any region
    # the process has added or grown since the last look, and takes a full sweep
    # every FULL_SWEEP_EVERY scans as a backstop. The middle clause is what makes
    # this safe rather than merely fast: a newly built ship is allocated from the
    # same heap as every other ship, and if the engine ever allocates somewhere
    # new, that region is new territory and gets swept because it is new.
    FULL_SWEEP_EVERY = 20

    def scan(self, full=False):
        if not self.handle:
            return 0
        regions = enum_writable_regions(self.handle)
        stale = self.scans_since_full >= self.FULL_SWEEP_EVERY
        if full or not self.hot_regions or stale:
            sweep = regions
            self.scans_since_full = 0
        else:
            known = set(self.prev_regions)
            sweep = list(self.hot_regions) + [r for r in regions
                                              if r not in known]
            self.scans_since_full += 1
        self.prev_regions = regions
        self.ejbo_addrs = scan_for_ejbo(self.handle, sweep)
        # Remember where they actually were, so the next warm sweep is narrow.
        self.hot_regions = [r for r in regions
                            if any(r[0] <= a < r[0] + r[1]
                                   for a in self.ejbo_addrs)]
        self.scan_count = len(self.ejbo_addrs)
        # Pointer targets can be freed and reused, so drop the cache on a rescan
        self.ptr_cache = {}
        # Classify once up front so object extents can be measured per class
        class_of = {a: classify_object(self.handle, a, self.rtti_cache)[0]
                    for a in self.ejbo_addrs}
        self.extents, notes = measure_extents(self.handle, self.ejbo_addrs,
                                              class_of, self.rtti_cache)
        # Say it once, not every scan. These are derivations, not events, and
        # they were tolerable at one scan per turn; a warm scan is cheap enough
        # to run often, and twelve unchanged lines per scan buries the log lines
        # that do mean something.
        if notes != self._extent_notes:
            for n in sorted(notes):
                print(f"[extent] {n}")
            self._extent_notes = notes
        self._refresh_objects()
        return self.scan_count

    def _refresh_objects(self):
        objs = []
        for addr in self.ejbo_addrs:
            type_name, obj_id, type_ptr = classify_object(self.handle, addr,
                                                          self.rtti_cache)
            fields, name = read_object_fields(
                self.handle, addr,
                self.extents.get(type_name, DEFAULT_READ_AFTER),
                CLASS_FRONT.get(type_name, READ_BEFORE), type_name)
            if fields is None:
                continue
            objs.append({
                "addr":     addr,
                "addr_hex": f"0x{addr:08X}",
                # Objects inside the module image are static templates / dialog
                # working buffers, not game entities (the .data Admiral and
                # Governor). They must be excluded from any state sync.
                "static":   MODULE_LO <= addr < MODULE_HI,
                "type":     type_name,
                "id":       obj_id,
                "type_ptr": f"0x{type_ptr:08X}",
                "name":     name,
                "fields":   fields,
            })
        # Name whatever each pointer-shaped field points at. Objects must all be
        # known first so the reference-node idiom can resolve to "Owner #194".
        by_start = {}
        for o in objs:
            by_start[o["addr"] - 8]  = (o["type"], o["id"])
            by_start[o["addr"] - 12] = (o["type"], o["id"])
        for o in objs:
            for f in o["fields"]:
                f["ptr"] = resolve_pointer(self.handle, f["u32"], self.rtti_cache,
                                           self.ptr_cache, by_start)
        # Ownership, once the pointers are named. Only Planet needs it — it is
        # the only class the viewer splits by owner.
        for o in objs:
            if o["type"] != "Planet":
                continue
            owner_field = next((f for f in o["fields"]
                                if f["offset"] == OWNER_LINK_OFFSET), None)
            o["owned"] = bool(owner_field) and is_owned(owner_field)
        with self.lock:
            # Compute changes
            new_prev = {}
            for obj in objs:
                addr = obj["addr"]
                prev = self.prev_values.get(addr, {})
                new_map = {}
                for f in obj["fields"]:
                    off = f["offset"]
                    new_map[off] = f["u32"]
                    if off in prev and prev[off] != f["u32"]:
                        f["changed"] = True
                    else:
                        f["changed"] = False
                new_prev[addr] = new_map
            self.prev_values = new_prev
            self.objects     = objs
            self.last_update = time.time()

    def refresh(self):
        """Re-read values for all known EJBO objects (no re-scan)."""
        if not self.handle or not self.ejbo_addrs:
            return
        self._refresh_objects()

    def get_data_json(self):
        with self.lock:
            # Group by type
            groups = {}
            for obj in self.objects:
                t = obj["type"]
                if t not in groups:
                    groups[t] = []
                groups[t].append(obj)
            # Sort each group by object ID for consistent column ordering
            for t in groups:
                groups[t].sort(key=lambda o: o["id"])
            return json.dumps({
                "pid":        self.pid,
                "proc":       self.proc_name,
                "scan_count": self.scan_count,
                "last_update": self.last_update,
                "groups":     groups,
                "extents":    self.extents,
                "annotations": self.annotations,
            })

    def set_annotation(self, type_name, offset, text):
        key = f"{type_name}:{offset}"
        if text.strip():
            self.annotations[key] = text.strip()
        else:
            self.annotations.pop(key, None)
        save_annotations(self.annotations)

    def poke(self, ejbo_addr, offset, value_str, fmt):
        """Write a value to game memory. fmt is 'i32', 'u32', 'f32', or 'hex'.
        Returns (success: bool, message: str)."""
        if not self.handle:
            return False, "No process handle"
        addr = ejbo_addr + offset
        try:
            if fmt == "f32":
                raw = struct.pack("<f", float(value_str))
            elif fmt == "i32":
                raw = struct.pack("<i", int(value_str))
            elif fmt == "u32":
                raw = struct.pack("<I", int(value_str))
            elif fmt == "hex":
                # Accept "0x00771df8" or "00771df8"
                v = int(value_str.replace("0x", "").replace("0X", ""), 16)
                raw = struct.pack("<I", v)
            else:
                return False, f"Unknown format: {fmt}"
        except (ValueError, struct.error) as e:
            return False, f"Bad value: {e}"
        # Read the old value first for logging
        old = read_bytes(self.handle, addr, 4)
        ok  = write_bytes(self.handle, addr, raw)
        if ok:
            old_hex = old.hex() if old else "????"
            new_hex = raw.hex()
            msg = f"Wrote {fmt}({value_str}) to 0x{addr:08X} [old={old_hex} new={new_hex}]"
            print(f"[POKE] {msg}")
            return True, msg
        else:
            err = ctypes.get_last_error()
            return False, f"WriteProcessMemory failed at 0x{addr:08X} (error {err})"

    def export_csv(self):
        """Export all objects as CSV."""
        out = io.StringIO()
        w   = csv.writer(out)
        w.writerow(["Type", "ObjID", "Name", "EJBO_Addr", "Static",
                     "Offset", "Hex", "UInt32", "Int32", "Float32",
                     "ASCII", "PointsTo", "Annotation", "Changed"])
        with self.lock:
            for obj in self.objects:
                for f in obj["fields"]:
                    ann_key = f"{obj['type']}:{f['offset']}"
                    ann     = self.annotations.get(ann_key, "")
                    w.writerow([
                        obj["type"], obj["id"], obj["name"], obj["addr_hex"],
                        "STATIC" if obj.get("static") else "",
                        f["offset"], f["hex"], f["u32"], f["i32"],
                        f["f32"], f["ascii"], f.get("ptr") or "", ann,
                        f["changed"]
                    ])
        return out.getvalue()

STATE = ViewerState()

# ── Background refresh thread ──────────────────────────────────────────────
def background_refresh(interval):
    while True:
        try:
            STATE.reconnect_if_needed()
            STATE.refresh()
        except Exception as e:
            print(f"[refresh] error: {e}")
        time.sleep(interval)

# ── HTTP server ────────────────────────────────────────────────────────────
class ViewerHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # silence per-request logging

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        if path == "/":
            self._serve_html()
        elif path == "/api/data":
            self._json_response(STATE.get_data_json())
        elif path == "/api/rescan":
            n = STATE.scan()
            self._json_response(json.dumps({"count": n}))
        elif path == "/api/export.csv":
            data = STATE.export_csv()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition",
                             f"attachment; filename=ejbo_export_{datetime.now():%Y%m%d_%H%M%S}.csv")
            self.end_headers()
            self.wfile.write(data.encode())
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        if parsed.path == "/api/annotate":
            STATE.set_annotation(body["type"], body["offset"], body["text"])
            self._json_response('{"ok":true}')
        elif parsed.path == "/api/poke":
            # body: {ejbo_addr: int, offset: int, value: str, fmt: "i32"|"u32"|"f32"|"hex"}
            ok, msg = STATE.poke(body["ejbo_addr"], body["offset"],
                                  str(body["value"]), body["fmt"])
            self._json_response(json.dumps({"ok": ok, "msg": msg}))
        else:
            self.send_error(404)

    def _json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data.encode())

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode())

# ── HTML Dashboard ─────────────────────────────────────────────────────────
# Loaded from ejbo_viewer.html at runtime (keeps this file under 500 lines).
HTML_PAGE = ""  # populated in main()

def _load_html():
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ejbo_viewer.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="EJBO Viewer — live game object browser")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (default 8080)")
    parser.add_argument("--refresh", type=float, default=2.0,
                        help="Background refresh interval in seconds (default 2)")
    args = parser.parse_args()

    global HTML_PAGE
    HTML_PAGE = _load_html().replace("%%REFRESH_MS%%", str(int(args.refresh * 1000)))

    print(f"[*] Searching for Cosmic Supremacy process...")
    if not STATE.connect():
        print("[!] Could not find CosmicSupremacy process. Is the game running?")
        sys.exit(1)
    print(f"[+] Attached to PID {STATE.pid} ({STATE.proc_name})")

    n = STATE.scan()
    print(f"[+] Found {n} EJBO objects")

    # Start background refresh
    t = threading.Thread(target=background_refresh, args=(args.refresh,), daemon=True)
    t.start()
    print(f"[+] Background refresh every {args.refresh}s")

    print(f"[*] Dashboard at http://localhost:{args.port}")
    server = HTTPServer(("", args.port), ViewerHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down.")
        server.server_close()

if __name__ == "__main__":
    main()