"""
save_parser.py — Cosmic Supremacy save blob decoder
====================================================
Decodes the binary save format used by CosmicSupremacy.exe.

Usage:
    python save_parser.py save_game_0.dat          # summary
    python save_parser.py save_game_0.dat --gset    # dump galaxy settings
    python save_parser.py save_game_0.dat --full    # dump everything
    python save_parser.py save_game_0.dat --raw     # write decompressed blob

Format:
    Wire:   base64( uint32_LE(decompressed_size) + zlib(binary_blob) )
    Blob:   SAVE header + GSET + GLOB + TMGX + RSMA + NWDB + GLXY
            + [OWNR block per player]
            + [SOLA block per solar system (with SUN + PLNTs)]
            + [SHIP blocks] + [ROUT blocks] + [NEBU blocks]
"""

import base64
import zlib
import struct
import sys
import re
from collections import Counter


def decode_save(raw_bytes: bytes) -> bytes:
    """Decode a save blob from its wire format to decompressed binary."""
    decoded = base64.b64decode(raw_bytes)
    expected_size = struct.unpack_from('<I', decoded, 0)[0]
    decompressed = zlib.decompress(decoded[4:])
    assert len(decompressed) == expected_size, \
        f"Size mismatch: header says {expected_size}, got {len(decompressed)}"
    return decompressed


def encode_save(decompressed: bytes) -> bytes:
    """Encode decompressed binary back to wire format."""
    compressed = zlib.compress(decompressed)
    size_prefix = struct.pack('<I', len(decompressed))
    return base64.b64encode(size_prefix + compressed)


def parse_save_header(data: bytes) -> dict:
    """Parse the SAVE file header (first 12 bytes)."""
    magic = data[:4]
    assert magic == b'SAVE', f"Bad magic: {magic}"
    body_size = struct.unpack_from('<H', data, 4)[0]
    version = struct.unpack_from('<H', data, 6)[0]
    section_count = struct.unpack_from('<I', data, 8)[0]
    return {
        'magic': 'SAVE',
        'body_size': body_size,
        'version': hex(version),
        'section_count': section_count,
        'total_size': len(data),
    }


def parse_gset(data: bytes, offset: int) -> tuple[dict, int]:
    """Parse GSET (Galaxy Settings) section. Returns (settings_dict, end_offset)."""
    assert data[offset:offset+4] == b'GSET'
    pos = offset + 4
    section_size = struct.unpack_from('<I', data, pos)[0]
    pos += 4
    num_entries = struct.unpack_from('<I', data, pos)[0]
    pos += 4

    settings = {}
    for _ in range(num_entries):
        name_len = struct.unpack_from('<I', data, pos)[0]
        pos += 4
        name = data[pos:pos+name_len].decode('ascii', errors='replace')
        pos += name_len

        type_code = struct.unpack_from('<I', data, pos)[0]
        pos += 4

        if type_code == 0:  # uint32 with has_custom flag
            has_custom = data[pos]; pos += 1
            value = struct.unpack_from('<i', data, pos)[0]; pos += 4
            settings[name] = {'type': 'int32', 'value': value, 'custom': bool(has_custom)}

        elif type_code == 1:  # int32 pair with has_custom flag
            has_custom = data[pos]; pos += 1
            val1 = struct.unpack_from('<i', data, pos)[0]; pos += 4
            val2 = struct.unpack_from('<i', data, pos)[0]; pos += 4
            settings[name] = {'type': 'int32_pair', 'value': (val1, val2), 'custom': bool(has_custom)}

        elif type_code == 2:  # int16
            value = struct.unpack_from('<h', data, pos)[0]; pos += 2
            settings[name] = {'type': 'int16', 'value': value}

        elif type_code == 3:  # string
            str_len = struct.unpack_from('<I', data, pos)[0]; pos += 4
            value = data[pos:pos+str_len].decode('utf-8', errors='replace'); pos += str_len
            settings[name] = {'type': 'string', 'value': value}

        elif type_code == 4:  # uint32 array with has_custom flag
            has_custom = data[pos]; pos += 1
            count = struct.unpack_from('<I', data, pos)[0]; pos += 4
            values = []
            for _ in range(count):
                v = struct.unpack_from('<I', data, pos)[0]; pos += 4
                values.append(v)
            settings[name] = {'type': 'array', 'value': values, 'custom': bool(has_custom)}

        else:
            settings[name] = {'type': f'unknown({type_code})', 'value': None}
            break

    return settings, pos


def find_sections(data: bytes) -> list[tuple[str, int]]:
    """Find all section markers and their offsets."""
    pattern = rb'SAVE|GSET|GLOB|TMGX|RSMA|NWDB|GLXY|OWNR|DATA|OWPR|KNPL|EXSY|HQAS|CVTR|SERV|DSGN|SDPR|GOVS|ADMS|SPQS|USSE|SOLA|SUN |PLNT|PLPR|PROD|WLTH|ENLI|SHIP|DYNO|SHCO|SHPR|ROUT|NEBU|CZYY'
    return [(m.start(), m.group().decode('ascii')) for m in re.finditer(pattern, data)]


def summarize(data: bytes):
    """Print a summary of the save file."""
    header = parse_save_header(data)
    sections = find_sections(data)
    counts = Counter(name for _, name in sections)

    print(f"=== Save File Summary ===")
    print(f"Total size:      {header['total_size']:,} bytes (decompressed)")
    print(f"Version:         {header['version']}")
    print(f"Section count:   {header['section_count']}")
    print(f"Unique sections: {len(counts)}")
    print()

    # GSET
    gset_offset = data.find(b'GSET')
    if gset_offset >= 0:
        settings, _ = parse_gset(data, gset_offset)
        print(f"Galaxy: name='{settings.get('name', {}).get('value', '?')}', "
              f"maxusers={settings.get('maxusers', {}).get('value', '?')}, "
              f"turnlength={settings.get('turnlength', {}).get('value', '?')}s, "
              f"sectorsize={settings.get('sectorsize', {}).get('value', '?')}")

    # Players
    ownr_offsets = [off for off, name in sections if name == 'OWNR']
    print(f"\nPlayers ({len(ownr_offsets)}):")
    for opos in ownr_offsets:
        nl = struct.unpack_from('<I', data, opos + 8)[0]
        name = data[opos+12:opos+12+nl].decode('ascii', errors='replace')
        print(f"  - {name}")

    # Galaxy composition
    print(f"\nGalaxy:")
    print(f"  Solar systems: {counts.get('SOLA', 0)}")
    print(f"  Planets:       {counts.get('PLNT', 0)}")
    print(f"  Ships:         {counts.get('SHIP', 0)}")
    print(f"  Nebulae:       {counts.get('NEBU', 0)}")
    print(f"  Fleet routes:  {counts.get('ROUT', 0)}")

    print(f"\nAll section types:")
    for name, count in counts.most_common():
        print(f"  {name}: {count}")


def dump_gset(data: bytes):
    """Print all galaxy settings."""
    gset_offset = data.find(b'GSET')
    if gset_offset < 0:
        print("No GSET section found.")
        return

    settings, _ = parse_gset(data, gset_offset)
    print(f"=== Galaxy Settings ({len(settings)} entries) ===")
    for name, info in settings.items():
        custom = ' (custom)' if info.get('custom') else ''
        print(f"  {name:30s} = {info['value']}{custom}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Cosmic Supremacy save file parser')
    parser.add_argument('file', help='Path to .dat save file')
    parser.add_argument('--gset', action='store_true', help='Dump galaxy settings')
    parser.add_argument('--full', action='store_true', help='Dump all sections')
    parser.add_argument('--raw', action='store_true', help='Write decompressed blob to .raw file')
    args = parser.parse_args()

    with open(args.file, 'rb') as f:
        raw = f.read()

    data = decode_save(raw)

    if args.raw:
        out_path = args.file.replace('.dat', '.raw')
        with open(out_path, 'wb') as f:
            f.write(data)
        print(f"Wrote {len(data)} bytes to {out_path}")
        return

    if args.gset:
        dump_gset(data)
    elif args.full:
        summarize(data)
        print()
        dump_gset(data)
    else:
        summarize(data)


if __name__ == '__main__':
    main()
