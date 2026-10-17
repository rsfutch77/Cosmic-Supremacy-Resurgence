"""
test_rename_materialisation.py: a default name is not a rename
===============================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_rename_materialisation.py

A running client materialises the default star name `Unnamed` wherever the
blob holds an empty string, so a submission appears to rename every unnamed
system. For a civ holding a system's majority, that would overwrite the name
with `Unnamed` every turn.

The rule: compare the submitted name against what was served. An empty name
served and `Unnamed` submitted is the engine filling in a default, and the
default never replaces a name; only a name that differs from the served one and
is not the default is a rename. The fixture is the tracked Single Player galaxy.
`canonical.py` does not mask star names, since masking would also forgive a
real change.
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
    fixture = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
    if not os.path.exists(fixture):
        print(f'no fixture at {fixture}')
        return 1
    blob = sp.load_any(fixture)
    civ = next(o['name'] for o in icv.owner_records(blob))
    target = sorted(systems(blob))[0]

    served = give_majority(blob, civ, target)

    print(f'civ {civ!r}, system {target}, majority granted in the fixture only')

    # 1. The engine filling in its default does not count as a rename.
    materialised = set_sun_name(served, target, DEFAULT_NAME)
    log = []
    merged = merge_orders.merge(served, [(civ, materialised)],
                                log=log.append)
    check('a default name over an empty one is not a rename',
          merged == served, True)

    # 2. A real rename by a civ that holds the majority is taken.
    renamed = set_sun_name(served, target, b'Homestead')
    log = []
    merged = merge_orders.merge(served, [(civ, renamed)], log=log.append)
    check('a real rename by the majority holder is still taken',
          merged != served, True)

    # 3. The default cannot erase a real name.
    named = set_sun_name(served, target, b'Homestead')
    wiped = set_sun_name(named, target, DEFAULT_NAME)
    merged = merge_orders.merge(named, [(civ, wiped)], log=lambda *a: None)
    check('the default cannot overwrite a name the player set',
          merged == named, True)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
