"""
test_scan.py: a scan request read, and an answer built from a capture
======================================================================
    python server\\tests\\test_scan.py

Uses `fixtures/unruled/sc1`, `d4` after GoodGuy's planetary scan of BadGuy's
HQ (384) and route scan of ship 677, made by the client offline. No client
is started.
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

import save_parser as sp                                        # noqa: E402
import merge_orders as mo                                       # noqa: E402
import scan                                                     # noqa: E402

FIX = os.path.join(HERE, 'fixtures', 'unruled')
GOOD = 656
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def refused(payload):
    try:
        scan.parse_request(payload)
    except scan.ScanError:
        return True
    return False


def sections(body, at):
    """[(tag, whole section)] from `at` to the end of `body`."""
    out = []
    while at < len(body):
        n = struct.unpack_from('<I', body, at + 4)[0] & sp.SIZE_MASK
        out.append((body[at:at + 4], body[at:at + 8 + n]))
        at += 8 + n
    return out


def main():
    print('the request')
    seen = bytes.fromhex('01000000' '80010000' '00000000')
    check('the 12 bytes the client sent read as a planetary scan of 384',
          scan.parse_request(seen), (0, [384]))
    check('    and build back the same',
          scan.build_request(0, [384]), seen)
    check('two targets of a route scan round-trip',
          scan.parse_request(scan.build_request(2, [677, 678])), (2, [677, 678]))
    check('a short request is refused', refused(b'\x01\x00\x00\x00'))
    check('a count past the bytes is refused',
          refused(struct.pack('<III', 2, 384, 0)))
    check('trailing bytes are refused', refused(seen + b'\x00'))
    check('an unknown scan type is refused',
          refused(struct.pack('<III', 1, 384, 8)))
    check('an absurd target count is refused',
          refused(struct.pack('<I', 100000) + b'\x00' * 8))

    print('the answer')
    sc1 = sp.load_any(os.path.join(FIX, 'sc1.b64'))
    reports = mo.reports_of(sc1, GOOD)
    body = scan.answer_body(sc1, 'GoodGuy', 1)
    check('the count is the reports made', struct.unpack_from('<I', body)[0], 1)
    parts = sections(body, 4)
    check('then the last report, the scanner\'s OWPR and its KNPL',
          [t for t, _ in parts], [b'SCRO', b'OWPR', b'KNPL'])
    check('    and the report is the capture\'s own bytes',
          parts[0][1], reports[-1])
    both = sections(scan.answer_body(sc1, 'GoodGuy', 2), 4)
    check('two reports come in the order made',
          [t for t, _ in both][:2], [b'SCPL', b'SCRO'])
    none = scan.answer_body(sc1, 'GoodGuy', 0)
    check('a scan that made nothing answers a count of 0',
          (struct.unpack_from('<I', none)[0], [t for t, _ in sections(none, 4)]),
          (0, [b'OWPR', b'KNPL']))
    try:
        scan.answer_body(sc1, 'GoodGuy', 3)
        check('more reports than the civ holds is refused', False)
    except scan.ScanError:
        check('more reports than the civ holds is refused', True)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
