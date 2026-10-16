"""
patch_launch_messages.py: point a shipped client's setup messages at the launcher
=================================================================================
    python patch_launch_messages.py --source CosmicSupremacy.exe --out <stage>\\game\\CosmicSupremacy.exe
    python patch_launch_messages.py --status <exe> [<exe> ...]

`--source`/`--out` reads a client and writes a patched copy to `--out`. The
release build calls it in place of copying each client into `game\\`, so the
patched bytes are written once, to a new file, and **the tracked binaries are
never modified**. `--out` refuses any path git tracks.

A client started without a galaxy pass, which is what double-clicking one in
`game\\` does, tries to register the `.csgalaxy` file association and then
shows one of two message boxes. Both walk the player through the original
site's flow: visit the portal, sign up, download a Galaxy Pass, drag it onto
the exe. The portal they name is the first host string, which the localhost
redirect turned into `127.0.0.1:8888`. A third message, shown when a client
has no login, names the same host.

That host string is display text only. It is copied into the global string at
`0x0086F104` at startup, and every read of that global is one of these three
format calls. The connection host is the second string, read by different code,
and nothing here touches either of them.

    0x00779C90   586 bytes   "%s setup partly failed! ..."
    0x00779EE0   444 bytes   "%s setup completed successfully. ..."
    0x0077A460    40 bytes   "Please login via the %s portal.\\nVisit %s"

Each is a sprintf format whose first argument is the product name, followed by
the host, the extension and the exe name in an order that differs per message.
The replacements keep that leading `%s` and drop the rest.

A fourth message, shown when Direct3D fails to create a device, asks the player
to email `problems@` at the connection host, so it reads
`problems@127.0.0.1:8888`. Its caller at `0x0057295F` passes the host from the
global at `0x0086F124` and then the path of the client's `log.txt`.

    0x00779130  1223 bytes   "\\n\\nYou can do the following ... problems@%s ..."

Its replacement names the project's issue tracker instead and consumes the host
with `%.0s`, which prints none of it, so the log path still lands on the second
conversion.

Every replacement uses a leading run of its original's arguments, each one
`%s` or `%.0s`. The caller removes its own arguments from the stack, so sprintf
leaving the trailing ones unread is safe. Each replacement is written into the
original's slot and the remainder is filled with NUL bytes, so no address moves
and no length grows.

The four strings sit at the same address in all four clients.
"""
import argparse
import os
import re
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(CLIENT_DIR)

IMAGE_BASE = 0x00400000

_FAILED = (
    b"%s setup partly failed!\n\nIt appears as if you do not have sufficient "
    b"rights to register the necessary file-associations.\n\nHowever, you can "
    b"still play the game by following these instructions:\n    o) Visit %s and "
    b"sign up to a galaxy of your choice\n    o) Click 'Enter Galaxy', this will "
    b"download the Galaxy-Pass file (ending with %s)\n    o) In the dialog box "
    b"that pops up select 'Save' and preferably save it to the same folder where "
    b"'%s' is located\n    o) Open a windows explorer and navigate to the folder "
    b"where '%s' is located\n    o) Drag the 'GalaxyPass%s' file onto '%s' and "
    b"drop it")

_COMPLETED = (
    b"%s setup completed successfully.\n\nYou can now visit %s and enter a galaxy "
    b"that you have signed up to.\n\nFollow these steps to enter a galaxy:\n    "
    b"o) Visit %s and sign up to a galaxy of your choice\n    o) Click 'Enter "
    b"Galaxy', this will download the Galaxy Pass file (ending with %s)\n    o) "
    b"In the dialog box that pops up select 'Open'\n    o) You can un-tick "
    b"'Always ask before opening this type of file' to suppress the dialog-box "
    b"in the future\n")

_LOGIN = b"Please login via the %s portal.\nVisit %s"

_USE_LAUNCHER = (
    b"%s is started from the launcher, not from this file.\n\n"
    b"To play:\n"
    b"    o) Close this window\n"
    b"    o) Go up one folder, out of the 'game' folder\n"
    b"    o) Double-click CosmicSupremacyLauncher.exe\n"
    b"    o) Pick a mode\n\n"
    b"The launcher starts the game with the right galaxy and saves your turns. "
    b"Leave it open while you play.")

_RENDERER = (
    b"\n\nYou can do the following to determine the cause of this problem:\n"
    b"Direct-X comes with a diagnosis tool that checks for the most obvious "
    b"flaws.\nThis tool is called 'DXDIAG'. You can run it by clicking 'Run' in "
    b"your 'Start' menu.\nIn the command-prompt enter the word 'DXDIAG' and "
    b"press 'Enter'. In the upcoming\nwindow check all tabs for any error "
    b"messages, but pay special attention to the\n'Display' tab. Check if all "
    b"Direct-X Features are enabled, and run both the\n'Direct Draw' and "
    b"'Direct 3D' test. Check if there are any errors reported\nduring the "
    b"test.\n\nIf you can't find any obvious flaws click the 'Save All "
    b"Informations...' button\nand email that file to problems@%s. In addition "
    b"attach the latest log\nfile, which has been saved to the following "
    b"location:\n%s\nAlso please specify if you do have similar problems with "
    b"other 3D applications/games.\n\nThe first thing you should try to fix "
    b"this problem is to install the latest driver\nfor your graphics chip. If "
    b"you do not know the maker and the name of the 3D chip\ninside your "
    b"computer, use 'DXDIAG' again: in the 'Display' tab find 'Chip-Type' "
    b"and\n'Manufacturer'. Find the manufacturers' webpage and head for the "
    b"'Drivers' section.\nDownload and install a suitable driver.")

_RENDERER_EMAIL = (
    b"and email that file to problems@%s. In addition attach the latest log\n"
    b"file, which has been saved to the following location:\n%s\nAlso please "
    b"specify if you do have similar problems with other 3D "
    b"applications/games.")
_RENDERER_ISSUE = (
    b"and attach that file to an issue at%.0s\n"
    b"https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/issues\n"
    b"with the latest log file, saved at:\n%s\n"
    b"Also say if other 3D applications/games have similar problems.")

# (virtual address, original text, replacement text, what it is)
SITES = [
    (0x00779C90, _FAILED, _USE_LAUNCHER,
     'setup failed message points at the launcher'),
    (0x00779EE0, _COMPLETED, _USE_LAUNCHER,
     'setup completed message points at the launcher'),
    (0x0077A460, _LOGIN, b"Restart %s from the launcher.",
     'login message points at the launcher'),
    (0x00779130, _RENDERER, _RENDERER.replace(_RENDERER_EMAIL, _RENDERER_ISSUE),
     'renderer failure message points at the issue tracker'),
]

_CONVERSION = re.compile(rb'%[^a-zA-Z%]*[a-zA-Z%]')


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


def slots(original: bytes, patched: bytes):
    """The bytes a site holds before and after, both the full slot width."""
    width = len(original) + 1
    if len(patched) + 1 > width:
        raise SystemExit(f'replacement is {len(patched)} bytes, '
                         f'slot holds {len(original)}')
    if patched == original:
        raise SystemExit('replacement is the original text')
    used = _CONVERSION.findall(patched)
    if (any(c not in (b'%s', b'%.0s') for c in used)
            or len(used) > len(_CONVERSION.findall(original))):
        raise SystemExit("replacement must use a leading run of the "
                         "original's arguments, each as %s or %.0s")
    return original + b'\0', patched.ljust(width, b'\0')


def site_state(data: bytes, va: int, original: bytes, patched: bytes) -> str:
    before, after = slots(original, patched)
    off = file_offset(data, va)
    here = data[off:off + len(before)]
    return ('original' if here == before else
            'patched' if here == after else 'unknown')


def is_tracked(path: str) -> bool:
    try:
        r = subprocess.run(['git', 'ls-files', '--error-unmatch', path],
                           cwd=REPO_ROOT, capture_output=True)
    except OSError:
        return False
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--status', nargs='+', metavar='EXE',
                    help='report each site in these clients')
    ap.add_argument('--source', help='client to read')
    ap.add_argument('--out', help='patched client to write')
    a = ap.parse_args()

    if a.status:
        for path in a.status:
            if not os.path.exists(path):
                print(f'  {os.path.basename(path):<34} missing')
                continue
            with open(path, 'rb') as f:
                data = f.read()
            for va, original, patched, what in SITES:
                how = site_state(data, va, original, patched)
                print(f'  {os.path.basename(path):<34} {va:#010x} {how:<9} {what}')
        return 0

    if not (a.source and a.out):
        ap.error('give --status, or both --source and --out')
    src = a.source if os.path.isabs(a.source) else os.path.join(CLIENT_DIR, a.source)
    out = os.path.abspath(a.out)
    if not os.path.exists(src):
        sys.exit(f'no such client: {src}')
    if os.path.exists(out) and os.path.samefile(src, out):
        sys.exit('--out is --source; write the patched client somewhere else')
    if is_tracked(out):
        sys.exit(f'{out} is tracked by git; refusing to patch it')

    with open(src, 'rb') as f:
        data = bytearray(f.read())
    done = 0
    for va, original, patched, what in SITES:
        before, after = slots(original, patched)
        off = file_offset(bytes(data), va)
        here = bytes(data[off:off + len(before)])
        if here == after:
            continue
        if here != before:
            sys.exit(f'{va:#010x} does not hold the expected text '
                     f'({what}); refusing to patch')
        data[off:off + len(after)] = after
        done += 1
    with open(out, 'wb') as f:
        f.write(data)
    shutil.copystat(src, out)
    print(f'wrote {os.path.basename(out)} from {os.path.basename(src)}, '
          f'{done} of {len(SITES)} site(s) patched')
    return 0


if __name__ == '__main__':
    sys.exit(main())
