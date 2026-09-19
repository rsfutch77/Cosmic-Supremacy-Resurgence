"""
test_rename_materialisation.py , a default name is not a rename
===============================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_rename_materialisation.py

**This test fails today. That is the point.** It states the behaviour the
19 September rehearsal says we want, so whoever fixes `merge_orders` can tell
when they are done, and so the bug cannot quietly persist behind a green suite.

A client that has been running materialises the default star name `Unnamed`
wherever the blob holds an empty string , the same effect as A4's 756 bytes
across 108 suns, here 7 bytes across each of 32. `merge_orders` compares names
and sees 32 attempted system renames every single turn.

They are dropped today only because nobody holds a majority of a system's
planets. Measured in the rehearsal: with a fixture giving a civ 3 of 4 planets
in system 193, the merge **accepts** the phantom rename. In a real game a
player who controls a system would have its name overwritten with `Unnamed`
every turn, silently, including over a name they had just set.

The fix is the A4 lesson: **materialisation is not an edit.** Compare the
submitted name against what was SERVED. Empty in, `Unnamed` out, is the engine
filling in a default. Only a name that differs from the served name and is not
the engine's default counts as a player renaming something.

`canonical.py` deliberately does not mask star names, because masking a name to
forgive a default would also forgive a real change. Same reasoning, same
answer: do not mask, compare against what was served.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import merge_orders
import inject_civ as icv

DEFAULT_NAME = b'Unnamed'
PASS, FAIL = [], []


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def systems(blob):
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        sid = None
        for sun in sola.find('SUN '):
            sid = struct.unpack_from('<I', blob, sun.payload)[0]
            break
        if sid is not None:
            out[sid] = sola
    return out


def give_majority(blob, civ_name, system_id):
    """A local fixture where `civ_name` owns most of `system_id`."""
    oid = next(o['oid'] for o in icv.owner_records(blob)
               if o['name'] == civ_name)
    buf = bytearray(blob)
    sola = systems(blob)[system_id]
    planets = list(sola.find('PLNT'))
    need = len(planets) // 2 + 1
    for p in planets[:need]:
        struct.pack_into('<I', buf, p.payload + 16, oid)
    return bytes(buf)


def set_sun_name(blob, system_id, name: bytes):
    return merge_orders.set_system_name(blob, system_id, name)


def main():
    fixture = os.path.join(ROOT, 'server', 'fog_work', 'fix80.dat')
    if not os.path.exists(fixture):
        print(f'no fixture at {fixture}; run against any multi-civ blob')
        return 0
    blob = sp.load_any(fixture)
    civ = next(o['name'] for o in icv.owner_records(blob))
    target = sorted(systems(blob))[0]

    served = give_majority(blob, civ, target)

    print(f'civ {civ!r}, system {target}, majority granted in the fixture only')

    # 1. The engine filling in its default must NOT count as a rename.
    materialised = set_sun_name(served, target, DEFAULT_NAME)
    log = []
    merged = merge_orders.merge(served, [(civ, materialised)],
                                log=log.append)
    check('a default name over an empty one is not a rename',
          merged == served, True)

    # 2. A real rename by a civ that holds the majority must still work.
    renamed = set_sun_name(served, target, b'Homestead')
    log = []
    merged = merge_orders.merge(served, [(civ, renamed)], log=log.append)
    check('a real rename by the majority holder is still taken',
          merged != served, True)

    # 3. And it must not be possible to erase a real name with the default.
    named = set_sun_name(served, target, b'Homestead')
    wiped = set_sun_name(named, target, DEFAULT_NAME)
    merged = merge_orders.merge(named, [(civ, wiped)], log=lambda *a: None)
    check('the default cannot overwrite a name the player set',
          merged == named, True)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    if FAIL:
        print('\nFailures here are the KNOWN BUG, not a broken test. See the')
        print('module docstring and the 19 September rehearsal notes.')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
