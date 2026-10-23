"""
patch_civ_name.py: the civ name dialog always sends the name to the server

    python patch_civ_name.py --source CosmicSupremacy_Resurgence.exe --out <stage>\\game\\CosmicSupremacy_Resurgence.exe
    python patch_civ_name.py --status <exe> [<exe> ...]

`--out` may be `--source` when neither is tracked by git; the release build
patches the staged Single Player client in place.

The "Pick your Civilization Name and Coat of Arms" dialog (225) writes the
name into the local civ (`Owner+0x1A0`, saved in `OWPR`) and sends
`uploadcivname` only when no dropdown entry is selected (`CB_GETCURSEL` is
-1, `0x00497505`). The server lists the stored name and the dropdown selects
it, so a typed name was never sent, and each session offered the old name
again. The branch becomes six NOPs, so every OK sends the name.
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

SITE = 0x96905                        # file offset of the jne at 0x00497505
ORIGINAL = bytes.fromhex('0f85ec000000')
PATCHED = b'\x90' * len(ORIGINAL)


def state(data):
    here = bytes(data[SITE:SITE + len(ORIGINAL)])
    return ('original' if here == ORIGINAL else
            'patched' if here == PATCHED else 'unknown')


def patch(data):
    """Patch `data` (a bytearray) in place. Returns 1 if it changed, else 0."""
    now = state(data)
    if now == 'unknown':
        raise SystemExit('the civ name upload branch is not where it was measured')
    if now == 'patched':
        return 0
    data[SITE:SITE + len(PATCHED)] = PATCHED
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
                    help='report the civ name upload in these clients')
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
          f'civ name upload {"patched" if done else "already patched"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
