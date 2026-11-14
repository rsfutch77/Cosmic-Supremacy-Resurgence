"""
test_knpl.py: the known-players table read and written whole
=============================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_knpl.py

`knpl.parse` walks every tracked fixture's tables, and `knpl.build` gives the
same bytes back. The diplomacy and messages sessions place the fields the
captures move: `fixtures/diplomacy` d6 to d7 is BadGuy declaring war on
GoodGuy, d7 to d8 a tick, and `fixtures/messages` m3 to m6 GoodGuy's colour,
territory and ships choices for BadGuy.
"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import inject_civ as icv
import knpl

FIX = os.path.join(HERE, 'fixtures')
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def _walk(s):
    yield s
    for c in s.children:
        yield from _walk(c)


def tables(blob):
    """{civ name: (KNPL section, [record])}."""
    out = {}
    for ownr in (s for s in _walk(sp.parse_blob(blob)[0]) if s.tag == b'OWNR'):
        n = int.from_bytes(blob[ownr.payload:ownr.payload + 4], 'little')
        name = blob[ownr.payload + 4:ownr.payload + 4 + n].decode('latin-1')
        sec = next((s for s in _walk(ownr) if s.tag == b'KNPL'), None)
        if sec is not None:
            out[name] = (sec, knpl.parse(blob[sec.payload:sec.end]))
    return out


def record(path, civ, other):
    blob = sp.load_any(os.path.join(FIX, path))
    oid = next(o['oid'] for o in icv.owner_records(blob) if o['name'] == other)
    return next(r for r in tables(blob)[civ][1] if r['civ'] == oid)


def main():
    print('every tracked table')
    paths = sorted(glob.glob(os.path.join(FIX, '*', '*.b64')))
    walked, same, handles = 0, 0, 0
    for path in paths:
        blob = sp.load_any(path)
        for sec, recs in tables(blob).values():
            walked += 1
            same += knpl.build(recs) == blob[sec.payload:sec.end]
            handles += all(r['handle'] == r['civ'] for r in recs)
    check(f'{walked} tables parse to the end', walked > 0, True)
    check('and build back byte for byte', same, walked)
    check('every record\'s handle is its key', handles, walked)

    print('\ndeclaring war')
    before = record('diplomacy/d6.b64', 'BadGuy', 'GoodGuy')
    after = record('diplomacy/d7.b64', 'BadGuy', 'GoodGuy')
    other = record('diplomacy/d7.b64', 'GoodGuy', 'BadGuy')
    check('both sides\' stance becomes war',
          (after['stance'], other['stance']), (knpl.WAR, knpl.WAR))
    check('the declarer\'s record says it declared, the other\'s does not',
          (before['declared'], after['declared'], other['declared']), (0, 1, 0))
    check('+20 goes from -1 to 100 on both sides',
          (before['w20'], after['w20'], other['w20']), (-1, 100, 100))
    tick = record('diplomacy/d8.b64', 'BadGuy', 'GoodGuy')
    check('the next tick sets +17, +19 and +24 on the declarer\'s side only',
          ((tick['w17'], tick['w19'], tick['w24']),
           record('diplomacy/d8.b64', 'GoodGuy', 'BadGuy')['w24']),
          ((1, 1, 149), 0))

    print('\nthe viewer\'s display choices')
    m3 = record('messages/m3.b64', 'GoodGuy', 'BadGuy')
    m6 = record('messages/m6.b64', 'GoodGuy', 'BadGuy')
    check('the constructor\'s defaults: territory hidden, ships and stance '
          'colouring on', (m3['territory'], m3['ships'], m3['by_stance']),
          (0, 1, 1))
    check('green, territory shown and ships hidden, as chosen',
          (m6['colour'].to_bytes(4, 'little').hex(), m6['territory'],
           m6['ships'], m6['by_stance']), ('01f80dff', 1, 0, 0))

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
