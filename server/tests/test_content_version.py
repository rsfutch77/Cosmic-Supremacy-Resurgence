"""
test_content_version.py: a new galaxy runs on the released stat tables
======================================================================
    python server\\tests\\test_content_version.py

A blob's content version picks the client's stat tables, and 639 or above
picks the unreleased ones (`docs/CosmicSupremacy_Stat_Tables.md` §4a). TestBed
captures carry 99999, so the paths that start a galaxy stamp 565.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import galaxy_directory as gd                                   # noqa: E402
import galaxy_tool                                              # noqa: E402
import save_parser as sp                                        # noqa: E402
import set_blob_player as sbp                                   # noqa: E402

FIX = os.path.join(HERE, 'fixtures', 'messages', 'm0.b64')
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(*_a, **_k):
    pass


def main():
    base = sp.load_any(FIX)
    testbed = sbp.set_content_version(base, 565, log=quiet)
    testbed = bytearray(testbed)
    import struct
    _t, _g, off = sbp.content_version_field(bytes(testbed))
    struct.pack_into('<I', testbed, off, 99999)
    testbed = bytes(testbed)

    print('\nreleased()')
    check('a TestBed capture reads 99999', sbp.get_content_version(testbed), 99999)
    check('is stamped 565', sbp.get_content_version(sbp.released(testbed, log=quiet)),
          565)
    for v in (171, 565, 638):
        b = sbp.set_content_version(base, v, log=quiet)
        check(f'{v} is left as the galaxy chose it',
              sbp.get_content_version(sbp.released(b, log=quiet)), v)
    check('only the version changes',
          len(sbp.released(testbed, log=quiet)), len(testbed))

    print('\ngalaxy_tool start')
    work = tempfile.mkdtemp()
    try:
        path = os.path.join(work, 'capture.b64')
        with open(path, 'wb') as fh:
            fh.write(sp.encode_save(testbed))
        directory = gd.open_directory(os.path.join(work, 'galaxies'))
        galaxy_tool.start(directory, 'g1', 'G1', ['GoodGuy', 'BadGuy'],
                          blob_path=path, out=open(os.devnull, 'w'))
        store = directory.store('g1')
        turn = store.state()['turn']
        check('a galaxy started from a 99999 capture serves 565',
              sbp.get_content_version(store.turn_blob(turn)), 565)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
