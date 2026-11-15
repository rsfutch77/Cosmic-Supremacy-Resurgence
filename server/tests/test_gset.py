"""
test_gset.py: galaxy settings read and write as the client lays them out
========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_gset.py

Every `GSET` entry is a name, a u32 type, a flag byte, then the value (reader
`0x4D4990`); a bool (type 2) is one byte. `save_parser.set_gset` rewrites a
whole-number or bool value in place.

Fixture: `client/SinglePlayerGalaxy.dat` (tracked), whose `waronly` bytes are
`01 00`, flag 1 and value 0.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp                                        # noqa: E402

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def refused(blob, changes):
    try:
        sp.set_gset(blob, changes)
    except ValueError as exc:
        return str(exc)
    return None


def main():
    blob = sp.load_any(GALAXY)
    g = sp.gset_of(blob)

    print('reading')
    check('32 settings', len(g), 32)
    at = blob.find(b'waronly') + len(b'waronly')
    check('waronly is a bool, flag 1, value 0, as its bytes say',
          (g['waronly']['type'], g['waronly']['flag'], g['waronly']['value'],
           blob[at + 4:at + 6]),
          ('bool', 1, 0, b'\x01\x00'))
    check('sandbox and autoattack are bools at 0',
          (g['sandbox']['type'], g['sandbox']['value'],
           g['autoattack']['type'], g['autoattack']['value']),
          ('bool', 0, 'bool', 0))
    check('the name is an empty string', (g['name']['type'],
                                          g['name']['value']), ('string', ''))
    check('the numbers the report confirms read as before',
          (g['turnlength']['value'], g['startcredits']['value'],
           g['planetspersystem']['value'], g['homeworld_properties']['value']),
          (3600, 200, (4, 6), [300, 32, 30, 40, 7]))

    print('writing')
    out = sp.set_gset(blob, {'waronly': 1, 'sandbox': 1, 'turnlength': 600})
    h = sp.gset_of(out)
    check('the values are rewritten',
          (h['waronly']['value'], h['sandbox']['value'],
           h['turnlength']['value']), (1, 1, 600))
    check('the flags are not', (h['waronly']['flag'], h['sandbox']['flag']),
          (g['waronly']['flag'], g['sandbox']['flag']))
    check('nothing else moved', len(out) == len(blob) and sum(
        a != b for a, b in zip(out, blob)), 4)
    check('the blob still parses', sp.parse_blob(out)[0].tag, b'SAVE')
    check('writing the old values back restores the blob',
          sp.set_gset(out, {'waronly': 0, 'sandbox': 0, 'turnlength': 3600}),
          blob)

    print('refusing')
    check('a setting the galaxy lacks', refused(blob, {'nosuch': 1}),
          "this galaxy has no 'nosuch' setting")
    check('a bool given 2', refused(blob, {'waronly': 2}),
          'waronly is a bool and takes 0 or 1, not 2')
    check('a pair', refused(blob, {'rank': 1}),
          'rank is a int32_pair setting, not a whole number or bool')

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
