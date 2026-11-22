"""
patch_notes_label.py: the News screen's Notes row says where a note is made

    python patch_notes_label.py --source CosmicSupremacy.exe --out <stage>\\game\\CosmicSupremacy.exe
    python patch_notes_label.py --status <exe> [<exe> ...]

`--out` may be `--source` when neither is tracked by git.

The News screen's category list reads its rows from a table of string
pointers in `.data`. The Notes row, under Private Messages, is `Notes `, the
note icon and a clickable `Create a New Note`, which opens the note dialog
(`NoteDlg`); nothing else on screen makes a note. The row becomes
`Notes (Make new note --->)` before the same icon and link.

The string has one free byte after it, so the new one goes in the `.text`
padding at `0x006D2400`, clear of the save handler at `0x006D20A0` and the
scan stubs at `0x006D2200`, and the table's pointer at `0x007FA874` moves to
it. The original string is left in place and unread.
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

ORIGINAL = 0x00751268                   # the row's string in .rdata
ORIGINAL_TEXT = b'Notes \x07\x0c<NewNote>\x07<Create a New Note|clickable>\0'
POINTER_AT = 0x3F8474                   # file offset of the table entry 0x007FA874
LABEL = 0x006D2400                      # inside 3947 bytes of int3 padding
LABEL_AT = 0x2D1800                     # its file offset
LABEL_ROOM = 0x80
LABEL_TEXT = (b'Notes (Make new note --->) \x07\x0c<NewNote>\x07'
              b'<Create a New Note|clickable>\0')
ORIGINAL_AT = 0x34FA68                  # file offset of ORIGINAL

assert len(LABEL_TEXT) <= LABEL_ROOM


def state(data):
    """'original', 'patched', or 'unknown'."""
    pointer = struct.unpack_from('<I', data, POINTER_AT)[0]
    if bytes(data[ORIGINAL_AT:ORIGINAL_AT + len(ORIGINAL_TEXT)]) != ORIGINAL_TEXT:
        return 'unknown'
    room = bytes(data[LABEL_AT:LABEL_AT + LABEL_ROOM])
    if pointer == ORIGINAL and room == b'\xCC' * LABEL_ROOM:
        return 'original'
    if pointer == LABEL and room.startswith(LABEL_TEXT):
        return 'patched'
    return 'unknown'


def patch(data):
    """Patch `data` (a bytearray) in place. Returns 1 if it changed, else 0."""
    now = state(data)
    if now == 'unknown':
        raise SystemExit('the Notes row is not where it was measured')
    if now == 'patched':
        return 0
    data[LABEL_AT:LABEL_AT + len(LABEL_TEXT)] = LABEL_TEXT
    struct.pack_into('<I', data, POINTER_AT, LABEL)
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
                    help='report the Notes row in these clients')
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
          f'Notes row {"patched" if done else "already patched"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
