"""
extract_ui_art.py: lift the game's own UI skin out of a Win32 PE into image files
==================================================================================
    python extract_ui_art.py client\\CosmicSupremacy.exe release\\webui\\art

Most pieces are PNGs in a string-named "PNG" resource type, a few are
RT_BITMAP, written as .bmp with the file header added. Each file takes its
resource name. Only the pieces the launcher's skin uses are written, chosen by
name prefix. The art is never committed.
"""
import os
import struct
import sys

from extract_icon import first_leaf, parse_sections, rva_to_off

RT_BITMAP = 2

# Name prefixes of the pieces the skin draws. Pieces are named by position and
# state, so a prefix takes the whole 9-slice or 3-slice set.
PREFIXES = (
    "MAINWINDOWBORDER",
    "WINDOWBORDER",
    "TITLEBARBUTTON",
    "BUTTONLEFT", "BUTTONMIDDLE", "BUTTONRIGHT",
    "BUTTONTICKBOX",
    "SCROLLBARVERT",
    "NEWHEADER",
)


def res_name(data: bytes, res_off: int, raw: int) -> "str | int":
    """A directory entry's identifier: its string if named, else its number."""
    if not raw & 0x80000000:
        return raw
    at = res_off + (raw & 0x7FFFFFFF)
    n = struct.unpack_from("<H", data, at)[0]
    return data[at + 2:at + 2 + 2 * n].decode("utf-16-le")


def entries(data: bytes, res_off: int, off: int):
    """Yield (name_or_id, child_offset, is_subdir), string names included."""
    n_named, n_id = struct.unpack_from("<HH", data, off + 12)
    for i in range(n_named + n_id):
        raw, child = struct.unpack_from("<II", data, off + 16 + i * 8)
        yield res_name(data, res_off, raw), child & 0x7FFFFFFF, bool(child & 0x80000000)


def collect_named(data: bytes, sections, res_off: int, want) -> dict:
    """{resource name: bytes} for one resource type, by number or by string."""
    out = {}
    for ident, child, is_dir in entries(data, res_off, res_off):
        if ident != want or not is_dir:
            continue
        for name, sub, sub_is_dir in entries(data, res_off, res_off + child):
            if not isinstance(name, str) or not sub_is_dir:
                continue
            rva, size = first_leaf(data, res_off, res_off + sub)
            start = rva_to_off(sections, rva)
            out[name] = data[start:start + size]
    return out


def bmp_file(dib: bytes) -> bytes:
    """A .bmp file from an RT_BITMAP resource, which is the DIB without its header."""
    header_size, = struct.unpack_from("<I", dib, 0)
    bpp, = struct.unpack_from("<H", dib, 14)
    used, = struct.unpack_from("<I", dib, 32)
    palette = (used or (1 << bpp if bpp <= 8 else 0)) * 4
    pixels = 14 + header_size + palette
    return b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, pixels) + dib


def wanted(name: str) -> bool:
    return name.startswith(PREFIXES)


def main(argv) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    src, dst = argv[1], argv[2]

    with open(src, "rb") as fh:
        data = fh.read()
    sections, res_rva = parse_sections(data)
    res_off = rva_to_off(sections, res_rva)

    files = {}
    for name, blob in collect_named(data, sections, res_off, "PNG").items():
        if wanted(name):
            files[name + ".png"] = blob
    for name, blob in collect_named(data, sections, res_off, RT_BITMAP).items():
        if wanted(name):
            files[name + ".bmp"] = bmp_file(blob)
    if not files:
        raise ValueError("no UI art found under the expected names")

    os.makedirs(dst, exist_ok=True)
    for fname, blob in files.items():
        with open(os.path.join(dst, fname), "wb") as fh:
            fh.write(blob)
    print(f"{dst}: {len(files)} images, {sum(map(len, files.values())):,} bytes")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:
        print(f"extract_ui_art: {exc}", file=sys.stderr)
        sys.exit(1)
