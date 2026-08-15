"""
extract_icon.py — lift the application icon out of a Win32 PE into a .ico file
==============================================================================
    python extract_icon.py client\\CosmicSupremacy.exe release\\build\\cosmic.ico

WHY THIS EXISTS. The launcher should carry the game's own icon, not PyInstaller's
default, and the only copy of that icon we have is inside the client EXE. The
obvious shortcut — .NET's Icon.ExtractAssociatedIcon — hands back a single 32×32
image, which is what a launcher pinned to a modern taskbar would then look like.
Walking the resource directory instead recovers every size the original artist
shipped, at the cost of about a hundred lines of well-specified structure
parsing and no dependencies at all.

WHAT IT PARSES. Two resource types and the relationship between them. RT_ICON
(3) holds the images; RT_GROUP_ICON (14) holds a directory listing which images
form one logical icon and at what sizes. A .ico file on disk is very nearly the
group resource verbatim — the only edit is that each entry's trailing 2-byte
resource ID becomes a 4-byte file offset, which is the whole of build_ico below.

Failure is a non-zero exit and a message, never a corrupt .ico: the build script
treats this step as optional and falls back to no icon.
"""
import os
import struct
import sys

RT_ICON = 3
RT_GROUP_ICON = 14


def parse_sections(data: bytes):
    """Return (sections, resource_rva). Sections are (va, vsize, rawsize, rawptr)."""
    if data[:2] != b"MZ":
        raise ValueError("not a PE image (no MZ signature)")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("not a PE image (no PE signature)")

    n_sections = struct.unpack_from("<H", data, pe + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x10B:      # PE32
        dd = opt + 96
    elif magic == 0x20B:    # PE32+
        dd = opt + 112
    else:
        raise ValueError(f"unknown optional header magic 0x{magic:04x}")

    # Data directory entry 2 is the resource table.
    res_rva = struct.unpack_from("<I", data, dd + 2 * 8)[0]
    if not res_rva:
        raise ValueError("image has no resource directory")

    sections = []
    sec = pe + 24 + opt_size
    for i in range(n_sections):
        off = sec + i * 40
        va, rawsize, rawptr = struct.unpack_from("<III", data, off + 12)
        vsize = struct.unpack_from("<I", data, off + 8)[0]
        sections.append((va, vsize, rawsize, rawptr))
    return sections, res_rva


def rva_to_off(sections, rva: int) -> int:
    for va, vsize, rawsize, rawptr in sections:
        # Compare against the larger of the two sizes: a section can be padded on
        # disk (rawsize > vsize) or zero-filled in memory (vsize > rawsize), and
        # resource data legitimately lives in either overhang.
        if va <= rva < va + max(vsize, rawsize):
            return rawptr + (rva - va)
    raise ValueError(f"RVA 0x{rva:08x} is not inside any section")


def dir_entries(data: bytes, off: int):
    """Yield (id_or_none, child_offset, is_subdir) for one resource directory."""
    n_named, n_id = struct.unpack_from("<HH", data, off + 12)
    for i in range(n_named + n_id):
        name, child = struct.unpack_from("<II", data, off + 16 + i * 8)
        # High bit on the name means a string identifier; icons are numbered, so
        # those are not ours and are skipped rather than guessed at.
        ident = None if name & 0x80000000 else name
        yield ident, child & 0x7FFFFFFF, bool(child & 0x80000000)


def first_leaf(data: bytes, res_off: int, off: int) -> "tuple[int, int]":
    """Descend to the first data leaf and return its (rva, size)."""
    node = off
    for _ in range(8):  # depth guard; real trees are type/name/language
        for _ident, child, is_dir in dir_entries(data, node):
            if is_dir:
                node = res_off + child
                break
            rva, size = struct.unpack_from("<II", data, res_off + child)
            return rva, size
        else:
            raise ValueError("empty resource directory")
    raise ValueError("resource directory nested too deeply")


def collect(data: bytes, sections, res_off: int, want_type: int) -> dict:
    """{resource id: bytes} for one resource type."""
    out = {}
    for ident, child, is_dir in dir_entries(data, res_off):
        if ident != want_type or not is_dir:
            continue
        for res_id, sub, sub_is_dir in dir_entries(data, res_off + child):
            if res_id is None or not sub_is_dir:
                continue
            rva, size = first_leaf(data, res_off, res_off + sub)
            start = rva_to_off(sections, rva)
            out[res_id] = data[start:start + size]
    return out


def build_ico(group: bytes, images: dict) -> bytes:
    """
    Turn a GRPICONDIR plus its RT_ICON images into a .ico file.

    Both formats share a 6-byte header and a per-image entry that agrees on its
    first 8 bytes (width, height, colour count, reserved, planes, bit count).
    They diverge after that: the resource entry is 14 bytes ending in a 4-byte
    size and a 2-byte icon ID, the file entry is 16 bytes ending in a 4-byte size
    and a 4-byte offset. So the shared prefix to copy is 8 bytes, not 12 — take
    12 and the size field gets written twice, producing 20-byte entries that
    every reader then walks at the wrong stride.
    """
    reserved, kind, count = struct.unpack_from("<HHH", group, 0)
    if reserved != 0 or kind != 1:
        raise ValueError("not an icon group resource")

    entries, blobs = [], []
    offset = 6 + 16 * count
    for i in range(count):
        head = group[6 + 14 * i: 6 + 14 * i + 8]
        icon_id = struct.unpack_from("<H", group, 6 + 14 * i + 12)[0]
        blob = images.get(icon_id)
        if blob is None:
            continue
        entries.append(head + struct.pack("<II", len(blob), offset))
        blobs.append(blob)
        offset += len(blob)

    if not entries:
        raise ValueError("icon group referenced no images present in the image")
    # count is rewritten because entries whose image was missing are dropped.
    return (struct.pack("<HHH", 0, 1, len(entries))
            + b"".join(entries) + b"".join(blobs))


def main(argv) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    src, dst = argv[1], argv[2]

    with open(src, "rb") as fh:
        data = fh.read()
    sections, res_rva = parse_sections(data)
    res_off = rva_to_off(sections, res_rva)

    groups = collect(data, sections, res_off, RT_GROUP_ICON)
    images = collect(data, sections, res_off, RT_ICON)
    if not groups:
        raise ValueError("no RT_GROUP_ICON resources")

    # Windows shows the numerically lowest group as the application icon, which
    # is the one Explorer draws for this EXE and therefore the one to match.
    gid = min(groups)
    ico = build_ico(groups[gid], images)

    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(dst, "wb") as fh:
        fh.write(ico)

    n = struct.unpack_from("<H", ico, 4)[0]
    sizes = []
    for i in range(n):
        w, h = ico[6 + 16 * i], ico[6 + 16 * i + 1]
        sizes.append(f"{w or 256}x{h or 256}")
    print(f"{dst}: group {gid}, {n} image(s) [{', '.join(sizes)}], {len(ico):,} bytes")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:
        print(f"extract_icon: {exc}", file=sys.stderr)
        sys.exit(1)
