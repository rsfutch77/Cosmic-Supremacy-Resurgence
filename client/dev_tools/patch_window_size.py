"""
patch_window_size.py: open the game's main window 10% larger by default

    python patch_window_size.py --source CosmicSupremacy.exe --out <stage>\\game\\CosmicSupremacy.exe
    python patch_window_size.py --status <exe> [<exe> ...]

`--out` may be `--source` when neither is tracked by git; the release build
patches each staged client in place after patch_launch_messages.py.

The main window's default placement (`0x00403CB0`) is a fixed 950 by 609,
40 pixels in from the right and bottom of the screen, with x and y floored at
60. It is used when no valid `MainWindowPos` is saved under
HKCU\\Software\\CosmicSupremacy, when Ctrl is held at startup, and always in the
Tutorial, which places the main window beside its own panel. A saved position
still wins everywhere else. The four clients hold these bytes at the same
offsets.
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

WIDTH, HEIGHT = 1045, 670             # 950 x 609, 10% larger
MARGIN = 40                           # from the screen's right and bottom edges

# (file offset, format, original, patched, what)
SITES = [
    (0x30C4, '<i', -(950 + MARGIN), -(WIDTH + MARGIN), 'left = screen width - this'),
    (0x30D6, '<i', -(609 + MARGIN), -(HEIGHT + MARGIN), 'top = screen height - this'),
    (0x30EF, '<H', 950, WIDTH, 'width'),
    (0x30F5, '<H', 609, HEIGHT, 'height'),
]


def state(data):
    got = [struct.unpack_from(fmt, data, off)[0] for off, fmt, _o, _p, _w in SITES]
    if got == [o for _f, _t, o, _p, _w in SITES]:
        return 'original'
    if got == [p for _f, _t, _o, p, _w in SITES]:
        return 'patched'
    return 'unknown'


def patch(data):
    """Patch `data` (a bytearray) in place. Returns 1 if it changed, else 0."""
    now = state(data)
    if now == 'unknown':
        raise SystemExit('the main window default is not where it was measured')
    if now == 'patched':
        return 0
    for off, fmt, _original, patched, _what in SITES:
        struct.pack_into(fmt, data, off, patched)
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
