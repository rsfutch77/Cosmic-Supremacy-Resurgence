"""
patch_status_bar.py: the multiplayer client's status bar shows the turn number
and nothing else

    python patch_status_bar.py --source CosmicSupremacy_TestBed.exe --out <stage>\\game\\CosmicSupremacy_Player.exe
    python patch_status_bar.py --status <exe> [<exe> ...]

`--out` may be `--source` when neither is tracked by git; the release build
patches the staged multiplayer client in place.

The status bar is built from format strings in `.rdata`, each read at one site
in the function at 0x00405D00. Under the launcher its countdown is wrong (the
launcher holds the client's clock, so it reads about a day) and the connection
label means nothing. The label's word becomes spaces, keeping its colour tags;
a countdown string ends after "Turn: %d". Each string keeps its length, and an
argument the format no longer prints is ignored by the formatter at
0x005E35E0. The launcher shows the
deadline and the connection instead.
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(CLIENT_DIR)

CREATE_NO_WINDOW = 0x08000000


# (file offset, what the string reads, the word to blank, whether to blank
# from it to the end). A connection label loses its word; a countdown loses
# everything from its word on, keeping "Turn: %d" and the `%s` after it. Offsets are measured in
# CosmicSupremacy_TestBed.exe; Resurgence has the same strings there.
STRINGS = (
    (0x34E990, b'\r<%s>Disconnected    \r<000000>', b'Disconnected', False),
    (0x34E9B0, b'\r<000000>Connected    \r<000000>', b'Connected', False),
    (0x34E9E8, b'Turn: %d%s     Next Turn in \r<000000>%s', b'Next Turn in', True),
    (0x34EA10, b'Turn: %d     Game Starts in \r<000000>%s', b'Game Starts in', True),
)


def _patched(original, word, to_end):
    """`original` with `word` turned to spaces, or with the string ended
    where it starts when `to_end`, so no trailing blanks shift the bar."""
    cut = original.index(word)
    if to_end:
        kept = original[:cut].rstrip()
        return kept + b'\0' * (len(original) - len(kept))
    return original[:cut] + b' ' * len(word) + original[cut + len(word):]


SITES = tuple((off, orig, _patched(orig, word, to_end))
              for off, orig, word, to_end in STRINGS)


def state(data):
    """'original', 'patched', or 'unknown' when any string is neither."""
    seen = set()
    for off, orig, new in SITES:
        here = bytes(data[off:off + len(orig)])
        seen.add('original' if here == orig else
                 'patched' if here == new else 'unknown')
    return seen.pop() if len(seen) == 1 else 'unknown'


def patch(data):
    """Patch `data` (a bytearray) in place. Returns 1 if it changed, else 0."""
    now = state(data)
    if now == 'unknown':
        raise SystemExit('the status bar strings are not where they were measured')
    if now == 'patched':
        return 0
    for off, orig, new in SITES:
        data[off:off + len(new)] = new
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
                    help='report the status bar strings in these clients')
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
          f'status bar {"patched" if done else "already patched"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
