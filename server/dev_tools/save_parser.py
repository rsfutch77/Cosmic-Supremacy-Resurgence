"""
save_parser.py — Cosmic Supremacy save blob decoder
====================================================
Decodes the binary save format used by CosmicSupremacy.exe.

Usage:
    python save_parser.py save.b64                 # summary
    python save_parser.py save.b64 --tree          # full section tree
    python save_parser.py save.b64 --gset          # dump galaxy settings
    python save_parser.py save.b64 --rout          # dump every ROUT record
    python save_parser.py save.b64 --raw           # write decompressed blob

Wire format
-----------
    base64( uint32_LE(decompressed_size) + zlib_deflate(blob) )

Section framing
---------------
Every section — from the outermost SAVE down to the smallest leaf — carries the
same 8-byte header:

    +0  char[4]  tag, e.g. 'ROUT'   (an MSVC multi-char constant, byte-swapped
                                     on write, which is why it reads as ASCII)
    +4  uint32   bits 0..25 = payload size in bytes, EXCLUDING this header
                 bits 26..31 = section version

`Archive::BeginSection(tag, version)` at 0x005e6260 writes the tag byte-swapped,
then a dword whose low 26 bits are a 0x3ffffff placeholder and whose top 6 bits
are the version.  `Archive::EndSection` at 0x005e6320 seeks back and patches the
low 26 bits with `current_offset - section_start - 8`.

Everything inside a section is a flat byte append: `Archive::WriteRaw` at
0x005e5e10 is a plain memcpy into the growing buffer with no per-field framing.
A payload is exactly the concatenation of the fields its writer emits.

"""

import base64
import os
import struct
import sys
import zlib
from collections import Counter

# Tags seen in the wild.  Used to decide whether a payload is a container of
# sub-sections; the framing itself does not depend on the list.
KNOWN_TAGS = {
    b'SAVE', b'GSET', b'GLOB', b'TMGX', b'RSMA', b'NWDB', b'GLXY', b'OWNR',
    b'DATA', b'OWPR', b'KNPL', b'EXSY', b'HQAS', b'CVTR', b'SERV', b'DSGN',
    b'SDPR', b'GOVS', b'ADMS', b'SPQS', b'USSE', b'SOLA', b'SUN ', b'PLNT',
    b'PLPR', b'PROD', b'WLTH', b'ENLI', b'SHIP', b'DYNO', b'SHCO', b'SHPR',
    b'ROUT', b'NEBU', b'CZYY', b'INFO',
}

SIZE_MASK     = 0x03FFFFFF
VERSION_SHIFT = 26


# ── Wire codec ────────────────────────────────────────────────────────────────

def decode_save(raw) -> bytes:
    """Decode a save payload from wire format to the decompressed blob."""
    if isinstance(raw, str):
        raw = raw.encode('ascii')
    decoded  = base64.b64decode(raw)
    expected = struct.unpack_from('<I', decoded, 0)[0]
    blob     = zlib.decompress(decoded[4:])
    if len(blob) != expected:
        raise ValueError(f"size mismatch: header says {expected}, got {len(blob)}")
    return blob


def encode_save(blob: bytes) -> bytes:
    """Encode a decompressed blob back to the wire format (base64 bytes)."""
    return base64.b64encode(struct.pack('<I', len(blob)) + zlib.compress(blob))


# ── Section tree ──────────────────────────────────────────────────────────────

class Section:
    __slots__ = ('tag', 'version', 'start', 'payload', 'size', 'children',
                 'prologue', 'epilogue')

    def __init__(self, tag, version, start, payload, size):
        self.tag      = tag        # bytes, 4 chars
        self.version  = version
        self.start    = start      # offset of the tag in the blob
        self.payload  = payload    # offset of the first payload byte
        self.size     = size       # payload length, header excluded
        self.children = []
        self.prologue = 0          # own bytes before the first child
        self.epilogue = 0          # own bytes after the last child

    @property
    def end(self):
        return self.payload + self.size

    def __repr__(self):
        return (f"<{self.tag.decode('latin-1')} v{self.version} "
                f"@{self.start:#x} size={self.size}>")

    def find(self, tag):
        """Depth-first iterator over this subtree matching a tag."""
        if isinstance(tag, str):
            tag = tag.encode('ascii')
        if self.tag == tag:
            yield self
        for c in self.children:
            yield from c.find(tag)


def read_header(blob: bytes, off: int):
    """Return (tag, version, size) for the section header at off, or None."""
    if off + 8 > len(blob):
        return None
    tag  = blob[off:off + 4]
    word = struct.unpack_from('<I', blob, off + 4)[0]
    return tag, word >> VERSION_SHIFT, word & SIZE_MASK


# A container's payload does not necessarily start with its first child: SAVE
# opens with a uint32 object count, and other sections carry their own fields
# ahead of any sub-sections.  Rather than hardcode every prologue, the walker
# searches a short window for the offset where a contiguous run of sections
# begins.  PROLOGUE_WINDOW bounds that search; anything skipped is recorded on
# the parent as `prologue`/`epilogue` so it is visibly unparsed rather than lost.
PROLOGUE_WINDOW = 256

# A candidate run has to tile most of the payload to be believed, which is what
# keeps four bytes of float data that happen to spell a tag from being accepted.
MIN_COVERAGE = 0.5


def _scan_run(blob: bytes, off: int, end: int):
    """Flat, non-recursive run of back-to-back sections starting at off."""
    out = []
    while off + 8 <= end:
        hdr = read_header(blob, off)
        if hdr is None:
            break
        tag, version, size = hdr
        if tag not in KNOWN_TAGS or off + 8 + size > end:
            break
        out.append(Section(tag, version, off, off + 8, size))
        off += 8 + size
    return out


def _best_run(blob: bytes, start: int, end: int):
    """
    Find where the sub-section run inside blob[start:end] begins.

    Returns (run_start, sections) for the candidate covering the most bytes, or
    (None, []) when nothing in the window yields a believable run.
    """
    span = end - start
    if span < 8:
        return None, []
    best, best_cov = (None, []), 0
    limit = min(start + PROLOGUE_WINDOW, end - 7)
    for off in range(start, limit):
        run = _scan_run(blob, off, end)
        if not run:
            continue
        cov = sum(s.size + 8 for s in run)
        if cov > best_cov:
            best, best_cov = (off, run), cov
    if best_cov < span * MIN_COVERAGE:
        return None, []
    return best


def parse_sections(blob: bytes, start: int, end: int, depth: int = 0) -> list:
    """
    Parse the sections occupying blob[start:end], recursing into each payload.

    A payload with no believable run of sections in it is left as a leaf, so a
    partly-understood region degrades to unparsed bytes rather than to garbage.
    """
    run_start, run = _best_run(blob, start, end)
    if run_start is None:
        return []
    for sec in run:
        if depth < 12:
            sec.children = parse_sections(blob, sec.payload, sec.end, depth + 1)
            if sec.children:
                first, last = sec.children[0], sec.children[-1]
                sec.prologue = first.start - sec.payload
                sec.epilogue = sec.end - last.end
    return run


def parse_blob(blob: bytes) -> list:
    """Parse a whole decompressed blob into a list of top-level sections."""
    return parse_sections(blob, 0, len(blob))


def flatten(sections):
    for s in sections:
        yield s
        yield from flatten(s.children)


def print_tree(sections, blob=None, indent=0, max_depth=99):
    for s in sections:
        pad   = '  ' * indent
        extra = ''
        if not s.children and blob is not None and s.size:
            head  = blob[s.payload:s.payload + min(16, s.size)]
            extra = '  ' + head.hex(' ')
        elif s.children:
            extra = f"  [{len(s.children)} children"
            if s.prologue:
                extra += f", +{s.prologue} own bytes first"
            if s.epilogue:
                extra += f", +{s.epilogue} own bytes last"
            extra += ']'
        print(f"{pad}{s.tag.decode('latin-1')} v{s.version} "
              f"@{s.start:#08x} size={s.size}{extra}")
        if indent < max_depth:
            print_tree(s.children, blob, indent + 1, max_depth)


def find_path(sections, target):
    """Ancestor chain from a top-level section down to target, inclusive."""
    for s in sections:
        if s is target:
            return [s]
        sub = find_path(s.children, target)
        if sub:
            return [s] + sub
    return None


def replace_payload(blob: bytes, tree, target: 'Section',
                    new_payload: bytes) -> bytes:
    """
    Return a new blob with target's payload replaced, and every enclosing
    section's size field corrected.

    Sizes are stored, so a naive splice produces a blob the client rejects.
    Every ancestor's size grows by the same delta; each ancestor starts before
    the splice point, so their header offsets are unaffected by it.
    """
    path = find_path(tree, target)
    if path is None:
        raise ValueError("target is not in this tree")
    delta = len(new_payload) - target.size

    out = bytearray(blob[:target.payload] + new_payload + blob[target.end:])
    for sec in path:
        size = len(new_payload) if sec is target else sec.size + delta
        if size > SIZE_MASK:
            raise ValueError(f"{sec.tag!r} payload exceeds the 26-bit size field")
        word = (sec.version << VERSION_SHIFT) | size
        struct.pack_into('<I', out, sec.start + 4, word)
    return bytes(out)


def build_section(tag: bytes, version: int, payload: bytes) -> bytes:
    """Wrap a payload in the standard 8-byte section header."""
    if len(tag) != 4:
        raise ValueError("tag must be 4 bytes")
    if len(payload) > SIZE_MASK:
        raise ValueError("payload too large for a 26-bit size field")
    return tag + struct.pack('<I', (version << VERSION_SHIFT) | len(payload)) + payload


# ── ROUT — the ship order / route record ──────────────────────────────────────
#
# Writer  Route::Write       0x004e5310, called from the ship writer at 0x0056ecc0
# Reader  Route::ReadFields  0x004e6d40, via the factory at 0x004e6e90
# Attach  Ship::SetOrder     0x004da450, stores the order at ship_base + 0x38
#
# The order object is a plain `operator new(0x60)` allocation — 96 bytes, with no
# EJBO tag and no object id of its own — so these are raw offsets from the object
# base, not the tag-relative offsets the annotations use for EJBO classes.
#
# ROUT is optional: the ship writer emits it only when the order pointer is
# non-null (`test ebx, ebx / je` at 0x0056ecb9), and the reader only builds one
# when the next tag actually is ROUT.
#
# On-wire order is the same as the writer's emission order:
#
#   u8   order_kind        obj+0    non-zero or the order is freed on attach
#   u8   flag_1            obj+1    version >= 1 only
#   f32  origin  x,y,z     obj+4,8,12
#   f32  target  x,y,z     obj+16,20,24
#   u32  count_a + count_a x 28-byte legs      vector at obj+28 (_Myfirst +40)
#   u32  count_b + count_b x 28-byte legs      vector at obj+52 (_Myfirst +64)
#   f32  progress          obj+76
#   u32  field_80/84/88    obj+80,84,88
#   u32  ref_id                     object id behind the reference node at +92
#
# Fixed part is 54 bytes; total is 54 + 28 * (count_a + count_b).

LEG_SIZE   = 28
LEG_FIELDS = ['origin_x', 'origin_y', 'origin_z',
              'dest_x', 'dest_y', 'dest_z', 'length']

_VEC3 = ('x', 'y', 'z')


def _read_legs(blob, off):
    count = struct.unpack_from('<I', blob, off)[0]
    off  += 4
    legs  = []
    for _ in range(count):
        legs.append(dict(zip(LEG_FIELDS, struct.unpack_from('<7f', blob, off))))
        off += LEG_SIZE
    return legs, off


def parse_rout(blob: bytes, sec: 'Section') -> dict:
    """Decode one ROUT section payload into a dict."""
    off = sec.payload
    r   = {'_version': sec.version}
    r['order_kind'] = blob[off]; off += 1
    if sec.version >= 1:
        r['flag_1'] = blob[off]; off += 1
    for name in ('origin_x', 'origin_y', 'origin_z',
                 'target_x', 'target_y', 'target_z'):
        r[name] = struct.unpack_from('<f', blob, off)[0]; off += 4
    r['legs_a'], off = _read_legs(blob, off)
    r['legs_b'], off = _read_legs(blob, off)
    r['progress'] = struct.unpack_from('<f', blob, off)[0]; off += 4
    for name in ('field_80', 'field_84', 'field_88'):
        r[name] = struct.unpack_from('<I', blob, off)[0]; off += 4
    r['ref_id'] = struct.unpack_from('<I', blob, off)[0]; off += 4
    r['_consumed'] = off - sec.payload
    r['_declared'] = sec.size
    return r


def build_rout(r: dict, version: int = 1) -> bytes:
    """
    Encode a ROUT section, header included, from a dict shaped like parse_rout's
    output.  This is the injection path: what the server writes so that a freshly
    loaded client comes up with the order already attached to the ship.
    """
    body = bytearray()
    body.append(r['order_kind'] & 0xFF)
    if version >= 1:
        body.append(r.get('flag_1', 0) & 0xFF)
    for name in ('origin_x', 'origin_y', 'origin_z',
                 'target_x', 'target_y', 'target_z'):
        body += struct.pack('<f', r[name])
    for key in ('legs_a', 'legs_b'):
        legs  = r.get(key) or []
        body += struct.pack('<I', len(legs))
        for leg in legs:
            body += struct.pack('<7f', *[leg[f] for f in LEG_FIELDS])
    body += struct.pack('<f', r['progress'])
    for name in ('field_80', 'field_84', 'field_88'):
        body += struct.pack('<I', r.get(name, 0))
    body += struct.pack('<I', r.get('ref_id', 0))
    return build_section(b'ROUT', version, bytes(body))


def make_leg(origin, dest):
    """Build a leg with its length filled in the way the engine stores it."""
    ox, oy, oz = origin
    dx, dy, dz = dest
    length = ((dx - ox) ** 2 + (dy - oy) ** 2 + (dz - oz) ** 2) ** 0.5
    return dict(zip(LEG_FIELDS, (ox, oy, oz, dx, dy, dz, length)))


# ── GSET — galaxy settings key/value block ────────────────────────────────────

def parse_gset(blob: bytes, sec: 'Section') -> dict:
    """Parse a GSET payload. Returns {name: {type, value, custom}}."""
    pos         = sec.payload
    num_entries = struct.unpack_from('<I', blob, pos)[0]
    pos        += 4

    settings = {}
    for _ in range(num_entries):
        name_len = struct.unpack_from('<I', blob, pos)[0]; pos += 4
        name     = blob[pos:pos + name_len].decode('ascii', errors='replace')
        pos     += name_len
        type_code = struct.unpack_from('<I', blob, pos)[0]; pos += 4

        if type_code == 0:                                   # int32 + flag
            custom = blob[pos]; pos += 1
            value  = struct.unpack_from('<i', blob, pos)[0]; pos += 4
            settings[name] = {'type': 'int32', 'value': value, 'custom': bool(custom)}
        elif type_code == 1:                                 # int32 pair + flag
            custom = blob[pos]; pos += 1
            v1, v2 = struct.unpack_from('<ii', blob, pos); pos += 8
            settings[name] = {'type': 'int32_pair', 'value': (v1, v2), 'custom': bool(custom)}
        elif type_code == 2:                                 # int16
            value = struct.unpack_from('<h', blob, pos)[0]; pos += 2
            settings[name] = {'type': 'int16', 'value': value}
        elif type_code == 3:                                 # string
            n     = struct.unpack_from('<I', blob, pos)[0]; pos += 4
            value = blob[pos:pos + n].decode('utf-8', errors='replace'); pos += n
            settings[name] = {'type': 'string', 'value': value}
        elif type_code == 4:                                 # uint32 array + flag
            custom = blob[pos]; pos += 1
            n      = struct.unpack_from('<I', blob, pos)[0]; pos += 4
            vals   = list(struct.unpack_from(f'<{n}I', blob, pos)); pos += 4 * n
            settings[name] = {'type': 'array', 'value': vals, 'custom': bool(custom)}
        else:
            settings[name] = {'type': f'unknown({type_code})', 'value': None}
            break

    return settings


# ── Reporting ─────────────────────────────────────────────────────────────────

def summarize(blob: bytes):
    top    = parse_blob(blob)
    allsec = list(flatten(top))
    counts = Counter(s.tag.decode('latin-1') for s in allsec)

    print("=== Save File Summary ===")
    print(f"Decompressed:  {len(blob):,} bytes")
    print(f"Sections:      {len(allsec)} ({len(counts)} distinct tags)")
    covered = sum(s.size + 8 for s in top)
    print(f"Top-level:     {len(top)} sections covering {covered:,} of "
          f"{len(blob):,} bytes")
    if covered != len(blob):
        print(f"  !! {len(blob) - covered} bytes were not parsed as sections")

    gset = next((s for s in allsec if s.tag == b'GSET'), None)
    if gset:
        st = parse_gset(blob, gset)
        def g(k):
            return st.get(k, {}).get('value', '?')
        print(f"\nGalaxy: name={g('name')!r} maxusers={g('maxusers')} "
              f"turnlength={g('turnlength')}s sectorsize={g('sectorsize')}")

    print("\nComposition:")
    for tag in ('OWNR', 'SOLA', 'PLNT', 'SHIP', 'ROUT', 'NEBU'):
        print(f"  {tag}: {counts.get(tag, 0)}")

    print("\nAll tags:")
    for name, n in counts.most_common():
        print(f"  {name}: {n}")


def dump_rout(blob: bytes):
    routs = [s for s in flatten(parse_blob(blob)) if s.tag == b'ROUT']
    print(f"=== {len(routs)} ROUT sections ===")
    for i, sec in enumerate(routs):
        r  = parse_rout(blob, sec)
        ok = ('ok' if r['_consumed'] == r['_declared']
              else f"MISMATCH consumed={r['_consumed']} declared={r['_declared']}")
        print(f"\n[{i}] @{sec.start:#x} v{sec.version} size={sec.size} ({ok})")
        print(f"    order_kind={r['order_kind']} flag_1={r.get('flag_1')}")
        print(f"    origin=({r['origin_x']:.4f}, {r['origin_y']:.4f}, {r['origin_z']:.4f})")
        print(f"    target=({r['target_x']:.4f}, {r['target_y']:.4f}, {r['target_z']:.4f})")
        print(f"    progress={r['progress']:.6f}  ref_id={r['ref_id']}")
        print(f"    field_80={r['field_80']} field_84={r['field_84']} "
              f"field_88={r['field_88']}")
        for name in ('legs_a', 'legs_b'):
            for j, leg in enumerate(r[name]):
                print(f"    {name}[{j}] "
                      f"({leg['origin_x']:.3f},{leg['origin_y']:.3f},{leg['origin_z']:.3f})"
                      f" -> ({leg['dest_x']:.3f},{leg['dest_y']:.3f},{leg['dest_z']:.3f})"
                      f"  len={leg['length']:.4f}")


def dump_gset(blob: bytes):
    gset = next((s for s in flatten(parse_blob(blob)) if s.tag == b'GSET'), None)
    if gset is None:
        print("No GSET section found.")
        return
    settings = parse_gset(blob, gset)
    print(f"=== Galaxy Settings ({len(settings)} entries) ===")
    for name, info in settings.items():
        custom = ' (custom)' if info.get('custom') else ''
        print(f"  {name:30s} = {info['value']}{custom}")


def load_any(path: str) -> bytes:
    """Accept a .b64 wire payload or an already-decompressed .raw blob."""
    with open(path, 'rb') as f:
        raw = f.read()
    if raw[:4] in KNOWN_TAGS:
        return raw
    return decode_save(raw.strip())


def main():
    import argparse
    p = argparse.ArgumentParser(description='Cosmic Supremacy save parser')
    p.add_argument('file', help='.b64 wire payload or .raw decompressed blob')
    p.add_argument('--tree', action='store_true', help='print the section tree')
    p.add_argument('--gset', action='store_true', help='dump galaxy settings')
    p.add_argument('--rout', action='store_true', help='dump ROUT records')
    p.add_argument('--raw',  action='store_true', help='write the decompressed blob')
    p.add_argument('--dat', metavar='PATH',
                   help='write the decompressed blob to PATH for the client to '
                        'load at startup. The extension must be .dat: the gate at '
                        '0x0056E7F0 compares the last four characters of argv[0] '
                        'against ".dat" before it will load anything.')
    args = p.parse_args()

    blob = load_any(args.file)

    if args.dat:
        if not args.dat.lower().endswith('.dat'):
            sys.exit("--dat path must end in .dat or the client will ignore it")
        with open(args.dat, 'wb') as f:
            f.write(blob)
        print(f"Wrote {len(blob)} bytes to {args.dat} (starts {blob[:4]!r})")
        print(f'  launch: CosmicSupremacy_Resurgence.exe "{os.path.abspath(args.dat)}"')
        return

    if args.raw:
        out = args.file.rsplit('.', 1)[0] + '.raw'
        with open(out, 'wb') as f:
            f.write(blob)
        print(f"Wrote {len(blob)} bytes to {out}")
        return

    if args.tree:
        print_tree(parse_blob(blob), blob)
    elif args.gset:
        dump_gset(blob)
    elif args.rout:
        dump_rout(blob)
    else:
        summarize(blob)


if __name__ == '__main__':
    main()
