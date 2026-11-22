"""
patch_window_size.py: open the game's main window sized to the screen

    python patch_window_size.py --source CosmicSupremacy.exe --out <stage>\\game\\CosmicSupremacy.exe
    python patch_window_size.py --status <exe> [<exe> ...]

`--out` may be `--source` when neither is tracked by git; the release build
patches each staged client in place after patch_launch_messages.py.

The main window's default placement (`0x00403CB0`, `__stdcall(RECT*)`) is a
fixed 950 by 609, 40 pixels in from the right and bottom of the screen, with
x and y floored at 60. It is used when no valid `MainWindowPos` is saved under
HKCU\\Software\\CosmicSupremacy, when Ctrl is held at startup, and always in the
Tutorial, which places the main window beside its own panel (`0x0057E597`). A
saved position still wins everywhere else. The four clients hold these bytes
at the same offsets.

The routine's first five bytes become a jump to a stub in the `.text` padding
at `0x006D2480`, clear of the Notes label at `0x006D2400`. The stub sizes the
window from the screen (`GetSystemMetrics`): three quarters of its width, in
the original's 950:609 shape, no taller than three fifths of its height, and
never smaller than 950 by 609. It places it as the original does.
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(CLIENT_DIR)

CREATE_NO_WINDOW = 0x08000000

TEXT_VA = 0x00401000                  # .text, file offset 0x400
TEXT_RAW = 0x400

HOOK = 0x00403CB0                     # the default placement
HOOK_ORIGINAL = bytes.fromhex('568b3578e67400')     # push esi; mov esi, [GetSystemMetrics]
GET_SYSTEM_METRICS = 0x0074E678       # the import's slot
STUB = 0x006D2480                     # inside 3947 bytes of int3 padding
STUB_ROOM = 0xB0

BASE_W, BASE_H = 950, 609             # the original default, and the floor
MARGIN = 40                           # from the screen's right and bottom edges
FLOOR = 60                            # the least x and y

# The old patch's four constants (10% larger), accepted as a starting point.
OLD_SITES = [(0x30C4, '<i', -(BASE_W + MARGIN), -(1045 + MARGIN)),
             (0x30D6, '<i', -(BASE_H + MARGIN), -(670 + MARGIN)),
             (0x30EF, '<H', BASE_W, 1045),
             (0x30F5, '<H', BASE_H, 670)]


def offset(va):
    return va - TEXT_VA + TEXT_RAW


def _rel(at, to, size=5):
    return struct.pack('<i', to - (at + size))


def stub():
    """The sizing routine, assembled for STUB. It keeps the shape of the
    routine it replaces: one RECT* argument, ret 4, ebx esi edi kept, and the
    RECT* returned in eax, which `OnCreate` reads the rectangle through."""
    c = bytearray()
    c += b'\x53\x56\x57'                                   # push ebx, esi, edi
    c += b'\x8B\x35' + struct.pack('<I', GET_SYSTEM_METRICS)   # mov esi, [GetSystemMetrics]
    c += b'\x6A\x00\xFF\xD6\x8B\xF8'                       # edi = screen width
    c += b'\x6A\x01\xFF\xD6\x8B\xD8'                       # ebx = screen height
    c += b'\x8D\x0C\x7F\xC1\xE9\x02'                       # ecx = width * 3 / 4
    c += b'\x69\xC1' + struct.pack('<I', BASE_H)           # imul eax, ecx, 609
    c += b'\x99\xBE' + struct.pack('<I', BASE_W)           # cdq; mov esi, 950
    c += b'\xF7\xFE\x8B\xF0'                               # idiv esi; esi = height
    c += b'\x8D\x04\x5B'                                   # lea eax, [ebx + ebx*2]
    c += b'\x99\x6A\x05\xF7\x3C\x24\x83\xC4\x04'           # eax = screen height * 3 / 5
    c += b'\x3B\xF0\x7E\x16'                               # cmp esi, eax; jle +22
    c += b'\x8B\xF0'                                       # esi = the cap
    c += b'\x69\xC6' + struct.pack('<I', BASE_W)           # imul eax, esi, 950
    c += b'\x99\x68' + struct.pack('<I', BASE_H)           # cdq; push 609
    c += b'\xF7\x3C\x24\x83\xC4\x04\x8B\xC8'               # ecx = height * 950 / 609
    c += b'\x81\xF9' + struct.pack('<I', BASE_W) + b'\x7D\x0A'   # cmp ecx, 950; jge +10
    c += b'\xB9' + struct.pack('<I', BASE_W)               # mov ecx, 950
    c += b'\xBE' + struct.pack('<I', BASE_H)               # mov esi, 609
    c += b'\x8B\xC7\x2B\xC1\x83\xE8' + bytes([MARGIN])     # eax = sw - w - 40
    c += b'\x83\xF8' + bytes([FLOOR]) + b'\x7D\x05'        # cmp eax, 60; jge +5
    c += b'\xB8' + struct.pack('<I', FLOOR)                # mov eax, 60
    c += b'\x8B\xD3\x2B\xD6\x83\xEA' + bytes([MARGIN])     # edx = sh - h - 40
    c += b'\x83\xFA' + bytes([FLOOR]) + b'\x7D\x05'        # cmp edx, 60; jge +5
    c += b'\xBA' + struct.pack('<I', FLOOR)                # mov edx, 60
    c += b'\x8B\x7C\x24\x10'                               # edi = the RECT*
    c += b'\x89\x07\x89\x57\x04'                           # left, top
    c += b'\x03\xC1\x89\x47\x08\x03\xD6\x89\x57\x0C'       # right, bottom
    c += b'\x8B\xC7'                                       # eax = the RECT*, as the
                                                           # original returns it
    c += b'\x5F\x5E\x5B\xC2\x04\x00'                       # pop edi, esi, ebx; ret 4
    assert len(c) <= STUB_ROOM
    return bytes(c)


def hook():
    return b'\xE9' + _rel(HOOK, STUB)


def size_for(screen_w, screen_h):
    """(width, height) the stub opens the window at, for a screen."""
    w = screen_w * 3 // 4
    h = int(w * BASE_H / BASE_W)
    cap = screen_h * 3 // 5
    if h > cap:
        h = cap
        w = int(h * BASE_W / BASE_H)
    if w < BASE_W:
        w, h = BASE_W, BASE_H
    return w, h


def state(data):
    """'original', 'patched', 'old' (the earlier fixed 10%), or 'unknown'."""
    at, s = offset(HOOK), offset(STUB)
    code = stub()
    head = bytes(data[at:at + len(HOOK_ORIGINAL)])
    room = bytes(data[s:s + STUB_ROOM])
    olds = [struct.unpack_from(f, data, o)[0] for o, f, _a, _b in OLD_SITES]
    if head[:5] == hook() and room.startswith(code):
        return 'patched'
    if head != HOOK_ORIGINAL or room != b'\xCC' * STUB_ROOM:
        return 'unknown'
    if olds == [a for _o, _f, a, _b in OLD_SITES]:
        return 'original'
    if olds == [b for _o, _f, _a, b in OLD_SITES]:
        return 'old'
    return 'unknown'


def patch(data):
    """Patch `data` (a bytearray) in place. Returns 1 if it changed, else 0."""
    now = state(data)
    if now == 'unknown':
        raise SystemExit('the main window default is not where it was measured')
    if now == 'patched':
        return 0
    code = stub()
    data[offset(STUB):offset(STUB) + len(code)] = code
    data[offset(HOOK):offset(HOOK) + 5] = hook()
    return 1


def is_tracked(path):
    try:
        r = subprocess.run(['git', 'ls-files', '--error-unmatch', path],
                           cwd=REPO_ROOT, capture_output=True,
                           creationflags=CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except OSError:
        return False
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--status', nargs='+', metavar='EXE',
                    help='report the main window default in these clients')
    ap.add_argument('--source', help='client to read')
    ap.add_argument('--out', help='patched client to write')
    a = ap.parse_args()

    if a.status:
        for path in a.status:
            if not os.path.exists(path):
                print(f'  {os.path.basename(path):<34} missing')
                continue
            with open(path, 'rb') as f:
                print(f'  {os.path.basename(path):<34} {state(f.read())}')
        return 0

    if not (a.source and a.out):
        ap.error('give --status, or both --source and --out')
    src = a.source if os.path.isabs(a.source) else os.path.join(CLIENT_DIR, a.source)
    out = os.path.abspath(a.out)
    if not os.path.exists(src):
        sys.exit(f'no such client: {src}')
    if is_tracked(out):
        sys.exit(f'{out} is tracked by git; refusing to patch it')

    with open(src, 'rb') as f:
        data = bytearray(f.read())
    done = patch(data)
    tmp = out + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    shutil.copystat(src, tmp)
    os.replace(tmp, out)
    print(f'wrote {os.path.basename(out)} from {os.path.basename(src)}, '
          f'main window default {"patched" if done else "already patched"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
