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
            if n and exe_substr.lower() in buf.value.lower():
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

# Known type descriptor pointers (at EJBO-8) — extend as we discover more
KNOWN_TYPES = {
    0x00768ddc: "Planet",
    0x00784934: "Admiral",
    0x00771df8: "ShipDesign",
    0x00771df0: "ShipDesign",  # secondary design type ptr
    0x00768B04: "Ship",        # actual ship instances (floats for HP/coords)
}

# How many bytes to read before/after the EJBO tag
READ_BEFORE = 32   # enough for type ptrs + object ID
READ_AFTER  = 192  # covers all known stat fields + some linked-list ptrs

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

def classify_object(h, ejbo_addr):
    """Read the type pointer at EJBO-8 and classify."""
    header = read_bytes(h, ejbo_addr - 12, 12)
    if header is None or len(header) < 12:
        return "Unknown", 0, 0
    type_ptr1 = struct.unpack_from("<I", header, 0)[0]  # EJBO-12
    type_ptr2 = struct.unpack_from("<I", header, 4)[0]  # EJBO-8
    obj_id    = struct.unpack_from("<I", header, 8)[0]  # EJBO-4
    type_name = KNOWN_TYPES.get(type_ptr2,
                KNOWN_TYPES.get(type_ptr1, "Unknown"))
    return type_name, obj_id, type_ptr2

def read_object_fields(h, ejbo_addr):
    """Read the raw bytes around an EJBO object and decode into field list."""
    start = ejbo_addr - READ_BEFORE
    total = READ_BEFORE + 4 + READ_AFTER   # before + EJBO tag + after
    raw   = read_bytes(h, start, total)
    if raw is None:
        return None, ""
    # Decode every 4-byte dword as multiple types
    fields = []
    for i in range(0, len(raw) - 3, 4):
        offset = i - READ_BEFORE  # offset relative to EJBO
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
    # depending on object type).
    name = ""
    for name_off in (8, 12, 16, 4):
        pos = READ_BEFORE + 4 + name_off
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
ANNOTATIONS_FILE = "ejbo_annotations.json"

def load_annotations():
    if os.path.exists(ANNOTATIONS_FILE):
        with open(ANNOTATIONS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_annotations(ann):
    with open(ANNOTATIONS_FILE, "w") as f:
        json.dump(ann, f, indent=2)

# ── Global state ───────────────────────────────────────────────────────────
class ViewerState:
    def __init__(self):
        self.pid         = None
        self.proc_name   = ""
        self.handle      = None
        self.ejbo_addrs  = []          # raw EJBO addresses
        self.objects     = []          # [{addr, type, id, type_ptr, name, fields}, ...]
        self.prev_values = {}          # addr -> {offset: u32} for change detection
        self.annotations = load_annotations()
        self.lock        = threading.Lock()
        self.last_update = 0
        self.scan_count  = 0

    def connect(self):
        pid, name = find_pid("CosmicSupremacy")
        if not pid:
            return False
        if self.handle:
            kernel32.CloseHandle(self.handle)
        self.pid       = pid
        self.proc_name = name
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

    def scan(self):
        if not self.handle:
            return 0
        regions = enum_writable_regions(self.handle)
        self.ejbo_addrs = scan_for_ejbo(self.handle, regions)
        self.scan_count = len(self.ejbo_addrs)
        self._refresh_objects()
        return self.scan_count

    def _refresh_objects(self):
        objs = []
        for addr in self.ejbo_addrs:
            type_name, obj_id, type_ptr = classify_object(self.handle, addr)
            fields, name = read_object_fields(self.handle, addr)
            if fields is None:
                continue
            objs.append({
                "addr":     addr,
                "addr_hex": f"0x{addr:08X}",
                "type":     type_name,
                "id":       obj_id,
                "type_ptr": f"0x{type_ptr:08X}",
                "name":     name,
                "fields":   fields,
            })
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
        w.writerow(["Type", "ObjID", "Name", "EJBO_Addr",
                     "Offset", "Hex", "UInt32", "Int32", "Float32",
                     "ASCII", "Annotation", "Changed"])
        with self.lock:
            for obj in self.objects:
                for f in obj["fields"]:
                    ann_key = f"{obj['type']}:{f['offset']}"
                    ann     = self.annotations.get(ann_key, "")
                    w.writerow([
                        obj["type"], obj["id"], obj["name"], obj["addr_hex"],
                        f["offset"], f["hex"], f["u32"], f["i32"],
                        f["f32"], f["ascii"], ann, f["changed"]
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