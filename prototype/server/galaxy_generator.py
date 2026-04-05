"""
galaxy_generator.py — Generate new Cosmic Supremacy galaxy save blobs
=====================================================================
Uses a captured turn-0 save blob as a template, modifying player name
and galaxy settings to create fresh galaxies that the client can load.

Strategy:
    Rather than building blobs from scratch (risky — any wrong field
    crashes the client), we take a known-good turn-0 save and patch
    specific fields. The client already loads these blobs successfully.

Usage:
    from galaxy_generator import generate_sandbox_galaxy

    # Returns base64-encoded wire-format blob ready for loadgame response
    blob = generate_sandbox_galaxy(player_name="Player1")

    # Server returns: "DONE#VER#000000#DATA#" + blob.decode('ascii')
"""

import base64
import zlib
import struct
import os
import re


TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), 'template_sandbox_t0.dat')


def _decode_wire(raw_bytes: bytes) -> bytes:
    """Decode wire format (base64 → size_prefix + zlib) to raw binary."""
    decoded = base64.b64decode(raw_bytes)
    expected_size = struct.unpack_from('<I', decoded, 0)[0]
    decompressed = zlib.decompress(decoded[4:])
    assert len(decompressed) == expected_size, \
        f"Size mismatch: header says {expected_size}, got {len(decompressed)}"
    return decompressed


def _encode_wire(data: bytes) -> bytes:
    """Encode raw binary to wire format (base64 of size_prefix + zlib)."""
    compressed = zlib.compress(data)
    size_prefix = struct.pack('<I', len(data))
    return base64.b64encode(size_prefix + compressed)


def _find_sections(data: bytes) -> list[tuple[int, str]]:
    """Find all section markers and their offsets in the binary blob."""
    pattern = (
        rb'SAVE|GSET|GLOB|TMGX|RSMA|NWDB|GLXY|OWNR|DATA|OWPR|KNPL|EXSY|'
        rb'HQAS|CVTR|SERV|DSGN|SDPR|GOVS|ADMS|SPQS|USSE|SOLA|SUN |PLNT|'
        rb'PLPR|PROD|WLTH|ENLI|SHIP|DYNO|SHCO|SHPR|ROUT|NEBU|CZYY'
    )
    return [(m.start(), m.group().decode('ascii')) for m in re.finditer(pattern, data)]


def _patch_gset_name(data: bytes, new_name: str) -> bytes:
    """Patch the galaxy name in the GSET section.

    GSET format:
        'GSET' (4) + section_size (4) + num_entries (4)
        For each entry: name_len (4) + name + type_code (4) + value

    The first entry is 'name' with type_code=3 (string).
    String format: str_len (4) + string_bytes
    """
    gset_off = data.find(b'GSET')
    if gset_off < 0:
        return data

    pos = gset_off + 4  # skip 'GSET'
    section_size = struct.unpack_from('<I', data, pos)[0]
    pos += 4
    num_entries = struct.unpack_from('<I', data, pos)[0]
    pos += 4

    # First entry should be 'name'
    name_len = struct.unpack_from('<I', data, pos)[0]
    pos += 4
    entry_name = data[pos:pos + name_len].decode('ascii')
    pos += name_len

    if entry_name != 'name':
        # Not what we expected — return unmodified
        return data

    type_code = struct.unpack_from('<I', data, pos)[0]
    pos += 4

    if type_code != 3:  # not a string type
        return data

    # Found the string value — replace it
    old_str_len = struct.unpack_from('<I', data, pos)[0]
    old_str_start = pos + 4
    old_str_end = old_str_start + old_str_len

    new_name_bytes = new_name.encode('utf-8')
    new_str_len_bytes = struct.pack('<I', len(new_name_bytes))

    # Rebuild: everything before str_len + new str_len + new string + everything after
    new_data = data[:pos] + new_str_len_bytes + new_name_bytes + data[old_str_end:]

    # Update GSET section_size to reflect the size change
    size_delta = len(new_name_bytes) - old_str_len
    new_section_size = section_size + size_delta
    struct.pack_into('<I', bytearray(new_data) if isinstance(new_data, bytes) else new_data,
                     gset_off + 4, new_section_size)

    # Need mutable for pack_into
    result = bytearray(new_data)
    struct.pack_into('<I', result, gset_off + 4, new_section_size)

    return bytes(result)


def _patch_player_name(data: bytes, player_index: int, new_name: str) -> bytes:
    """Patch a player name in an OWNR section.

    OWNR format: 'OWNR' (4) + header (4) + name_len (4) + name_bytes + trailing (2-4 bytes)

    The name_len field controls how many bytes follow as the player name.
    """
    sections = _find_sections(data)
    ownr_offsets = [off for off, name in sections if name == 'OWNR']

    if player_index >= len(ownr_offsets):
        return data

    off = ownr_offsets[player_index]

    # OWNR: magic(4) + header(4) + name_len(4) + name + trailing_data
    old_name_len = struct.unpack_from('<I', data, off + 8)[0]
    old_name_end = off + 12 + old_name_len

    new_name_bytes = new_name.encode('ascii', errors='replace')
    new_name_len = struct.pack('<I', len(new_name_bytes))

    # Rebuild
    result = bytearray(data[:off + 8] + new_name_len + new_name_bytes + data[old_name_end:])

    return bytes(result)


def _patch_gset_setting(data: bytes, setting_name: str, new_value) -> bytes:
    """Patch a specific GSET setting value.

    Supports type 0 (int32) and type 2 (int16) settings.
    """
    gset_off = data.find(b'GSET')
    if gset_off < 0:
        return data

    pos = gset_off + 4  # skip 'GSET'
    section_size = struct.unpack_from('<I', data, pos)[0]
    pos += 4
    num_entries = struct.unpack_from('<I', data, pos)[0]
    pos += 4

    result = bytearray(data)

    for _ in range(num_entries):
        name_len = struct.unpack_from('<I', data, pos)[0]
        pos += 4
        entry_name = data[pos:pos + name_len].decode('ascii', errors='replace')
        pos += name_len

        type_code = struct.unpack_from('<I', data, pos)[0]
        pos += 4

        if type_code == 0:  # int32 with has_custom flag
            has_custom_off = pos
            pos += 1  # skip has_custom
            value_off = pos
            pos += 4  # skip value

            if entry_name == setting_name:
                struct.pack_into('<i', result, value_off, int(new_value))
                result[has_custom_off] = 1  # mark as custom
                return bytes(result)

        elif type_code == 1:  # int32 pair
            pos += 1  # has_custom
            pos += 8  # two int32s

        elif type_code == 2:  # int16
            value_off = pos
            pos += 2

            if entry_name == setting_name:
                struct.pack_into('<h', result, value_off, int(new_value))
                return bytes(result)

        elif type_code == 3:  # string
            str_len = struct.unpack_from('<I', data, pos)[0]
            pos += 4 + str_len

        elif type_code == 4:  # array
            pos += 1  # has_custom
            count = struct.unpack_from('<I', data, pos)[0]
            pos += 4 + count * 4

        else:
            break  # unknown type, stop

    return data  # setting not found


def _update_save_header(data: bytes) -> bytes:
    """Update the SAVE header's body_size field to match actual data size."""
    result = bytearray(data)
    # SAVE header: magic(4) + body_size(2) + version(2) + section_count(4)
    # body_size = total_size - SAVE header size (but looking at the data,
    # body_size seems to be total - 8, based on: total=35767, body_size=0x8740=34624...
    # Actually the pattern is inconsistent. Let's just update it to total - 8.
    body_size = len(data) - 8
    # Clamp to uint16
    if body_size > 0xFFFF:
        body_size = body_size & 0xFFFF  # low 16 bits
    struct.pack_into('<H', result, 4, body_size)
    return bytes(result)


def _update_section_count(data: bytes, count: int | None = None) -> bytes:
    """Update section count in SAVE header.

    If count is None, preserves the existing value (safest when patching
    a template without adding/removing sections, since our regex-based
    section finder produces false positives in binary data).
    """
    if count is None:
        return data  # preserve existing count
    result = bytearray(data)
    struct.pack_into('<I', result, 8, count)
    return bytes(result)


def generate_sandbox_galaxy(
    player_name: str = "Player1",
    galaxy_name: str = "Sandbox",
    template_path: str | None = None,
) -> bytes:
    """Generate a new sandbox galaxy blob ready for the loadgame HTTP response.

    Args:
        player_name: Name for the human player (replaces "DemoPlayer" in template)
        galaxy_name: Galaxy name shown in-game
        template_path: Path to template .dat file (default: template_sandbox_t0.dat)

    Returns:
        base64-encoded wire-format blob (bytes).
        Use in loadgame response as: "DONE#VER#000000#DATA#" + blob.decode('ascii')
    """
    if template_path is None:
        template_path = TEMPLATE_PATH

    with open(template_path, 'rb') as f:
        raw = f.read()

    # Decode the template
    data = _decode_wire(raw)

    # Patch galaxy name in GSET
    data = _patch_gset_name(data, galaxy_name)

    # Patch player name (index 0 = first OWNR = human player)
    data = _patch_player_name(data, 0, player_name)

    # Ensure sandbox=1 in GSET
    data = _patch_gset_setting(data, 'sandbox', 1)

    # Update header (preserve original section count — our regex over-counts)
    data = _update_section_count(data)  # no-op, preserves template count
    data = _update_save_header(data)

    # Encode back to wire format
    return _encode_wire(data)


def generate_empty_save_response(
    player_name: str = "Player1",
    galaxy_name: str = "Sandbox",
) -> str:
    """Generate a complete loadgame HTTP response body.

    Returns the full response string: "DONE#VER#000000#DATA#<base64_blob>"
    """
    blob = generate_sandbox_galaxy(player_name=player_name, galaxy_name=galaxy_name)
    return "DONE#VER#000000#DATA#" + blob.decode('ascii')


# ── CLI for testing ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys

    player = sys.argv[1] if len(sys.argv) > 1 else "TestPlayer"
    galaxy = sys.argv[2] if len(sys.argv) > 2 else "MySandbox"

    print(f"Generating sandbox galaxy: player={player!r}, galaxy={galaxy!r}")

    blob = generate_sandbox_galaxy(player_name=player, galaxy_name=galaxy)
    print(f"Wire blob: {len(blob)} bytes (base64)")

    # Verify it round-trips
    data = _decode_wire(blob)
    sections = _find_sections(data)
    from collections import Counter
    counts = Counter(name for _, name in sections)

    print(f"Decompressed: {len(data)} bytes")
    print(f"Sections: {len(sections)}")
    print(f"  OWNR: {counts.get('OWNR', 0)}")
    print(f"  SOLA: {counts.get('SOLA', 0)}")
    print(f"  PLNT: {counts.get('PLNT', 0)}")
    print(f"  SHIP: {counts.get('SHIP', 0)}")

    # Verify player name
    from save_parser import decode_save, parse_save_header
    header = parse_save_header(data)
    print(f"SAVE version: {header['version']}, sections: {header['section_count']}")

    # Check GSET
    from save_parser import parse_gset
    gset_off = data.find(b'GSET')
    settings, _ = parse_gset(data, gset_off)
    print(f"Galaxy name: {settings.get('name', {}).get('value', '?')!r}")
    print(f"Sandbox: {settings.get('sandbox', {}).get('value', '?')}")

    # Check player name
    for off, name in sections:
        if name == 'OWNR':
            nl = struct.unpack_from('<I', data, off + 8)[0]
            pname = data[off + 12:off + 12 + nl].decode('ascii', errors='replace')
            print(f"Player: {pname!r}")

    # Save test output
    out_path = os.path.join(os.path.dirname(__file__), 'test_generated.dat')
    with open(out_path, 'wb') as f:
        f.write(blob)
    print(f"\nSaved to {out_path}")
