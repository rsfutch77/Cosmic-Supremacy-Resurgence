"""
patch_hide_setup_prompts.py , silence the one-time setup prompts in a client
============================================================================
    python patch_hide_setup_prompts.py --build     # make the player exe
    python patch_hide_setup_prompts.py --status
    python patch_hide_setup_prompts.py --revert    # delete it

`--build` copies `CosmicSupremacy_TestBed.exe` to `CosmicSupremacy_Player.exe`
and patches the copy. **The tracked binaries are never modified.** A patched
client in the working tree is one `git commit -a` away from entering the history,
which is why every patch here writes somewhere gitignored instead.

The client has three one-time setup prompts, each its own Win32 dialog resource:

    210   Customize Your Home World
    218   Customize Your Civilization
    225   Pick your Civilization Name and Coat of Arms

On a served turn they are all in the way. A client that comes up behind a modal
prompt is not a turn a player can take, and the launcher cannot dismiss one on
the player's behalf without deciding something that belongs to the player.

**210 and 218 need no patch on the right build.** They are gated correctly as
shipped; they became unconditional only in `CosmicSupremacy_Resurgence.exe`,
where two of the six T1-T5 turn-pipeline bypasses land on their guards:

    0x0056E0EF   JNZ 0x56E189, skip when [session+0x19B] is set   -> 6x NOP
    0x0056E133   JZ  0x56E189, skip when 0x508C60() returns 0     -> 2x NOP

So a player's client wants `CosmicSupremacy_TestBed.exe`, which is the same
binary without those 22 bytes, and a player must not compute turns anyway.

**225 is what this patch is for.** Its decision function `0x0056E700` is the only
path that opens it, and it is gated on the local civ's `Owner:384` being
non-zero. That field is a COUNT: the civ's `OWPR` section is exactly
`138 + Owner:384` bytes, so writing a value adds that many one-byte records whose
meaning is undecoded and quite possibly the civilisation traits. Setting it to
silence a cosmetic prompt would fabricate game state, so this patch returns from
the decision function instead, which fabricates nothing.

    0x0056E700   sub esp, 0x18   ->   ret

One byte, no instruction lengths change, no branch targets move. The dialog and
every path that builds it are left intact, so a caller that wants to open it can
still do so.

`[ ]` **The real fix is a one-time civilisation setup step.** In the original, a
player chose their name and coat of arms once and the blob carried it forever,
which is why the field was non-zero and the prompt never came back. Our galaxies
are generated locally and never go through that flow. Until the launcher or the
site offers it, this patch stands in for it, and `Owner:384` stays untouched
rather than guessed at.
"""
import argparse
import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.dirname(HERE)

SOURCE_EXE = 'CosmicSupremacy_TestBed.exe'
PLAYER_EXE = 'CosmicSupremacy_Player.exe'

IMAGE_BASE = 0x00400000

# (virtual address, original bytes, patched bytes, what it is)
SITES = [
    (0x0056E700, b'\x83', b'\xc3',
     'dialog 225 decision function returns immediately'),
]


def file_offset(data: bytes, va: int) -> int:
    """Translate a virtual address using the PE section table."""
    e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b'PE\0\0':
        raise SystemExit('not a PE image')
    coff = e_lfanew + 4
    n_sections = struct.unpack_from('<H', data, coff + 2)[0]
    opt_size = struct.unpack_from('<H', data, coff + 16)[0]
    sect = coff + 20 + opt_size
    rva = va - IMAGE_BASE
    for i in range(n_sections):
        off = sect + i * 40
        vsize, sva, rawsize, rawptr = struct.unpack_from('<IIII', data, off + 8)
        if sva <= rva < sva + max(vsize, rawsize):
            return rawptr + (rva - sva)
    raise SystemExit(f'{va:#010x} is in no section')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--status', action='store_true')
    g.add_argument('--build', action='store_true',
                   help='copy the source client and patch the copy')
    g.add_argument('--revert', action='store_true',
                   help='delete the player client')
    ap.add_argument('--source', default=SOURCE_EXE,
                    help=f'client to copy from (default {SOURCE_EXE})')
    ap.add_argument('--out', default=PLAYER_EXE,
                    help=f'client to write (default {PLAYER_EXE})')
    a = ap.parse_args()

    src = a.source if os.path.isabs(a.source) else os.path.join(CLIENT_DIR, a.source)
    out = a.out if os.path.isabs(a.out) else os.path.join(CLIENT_DIR, a.out)

    if a.revert:
        if os.path.exists(out):
            os.remove(out)
            print(f'removed {os.path.basename(out)}')
        else:
            print(f'{os.path.basename(out)} does not exist')
        return 0

    if a.status:
        for path in (src, out):
            if not os.path.exists(path):
                print(f'  {os.path.basename(path):<34} missing')
                continue
            with open(path, 'rb') as f:
                data = f.read()
            for va, original, patched, what in SITES:
                off = file_offset(data, va)
                here = data[off:off + len(original)]
                how = ('original' if here == original else
                       'patched' if here == patched else
                       f'unknown ({here.hex()})')
                print(f'  {os.path.basename(path):<34} {va:#010x} {how:<12} {what}')
        return 0

    if not os.path.exists(src):
        sys.exit(f'no such client: {src}')
    shutil.copy2(src, out)
    with open(out, 'rb') as f:
        data = bytearray(f.read())
    for va, original, patched, what in SITES:
        off = file_offset(bytes(data), va)
        here = bytes(data[off:off + len(original)])
        if here != original:
            sys.exit(f'{va:#010x} reads {here.hex()}, not the expected '
                     f'{original.hex()}; refusing to patch')
        data[off:off + len(patched)] = patched
    with open(out, 'wb') as f:
        f.write(data)
    print(f'built {os.path.basename(out)} from {os.path.basename(src)}, '
          f'{len(SITES)} site(s) patched')
    return 0


if __name__ == '__main__':
    sys.exit(main())
