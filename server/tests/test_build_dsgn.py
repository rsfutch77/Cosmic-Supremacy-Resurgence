"""
test_build_dsgn.py: a synthesised design has to have the shape a client writes
==============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_build_dsgn.py

A DSGN payload is objectId(4), one SDPR child, and one trailing zero byte.
Every real record surveyed carries that trailer.

The round trip takes a real design's parts through `read_design`, rebuilds it
with `build_dsgn`, and requires the result byte for byte. The `e1x2` and `e1x4`
probe designs in `server/saves` carry no scanner; `build_sdpr` defaults one in,
so those rebuild four bytes longer, and that default is tested on its own.

The galaxy2 rehearsal store is looked for beside this file first, then on the
share; `--galaxy` points it at another store. The captures in `server/saves`
are always read.
"""
import argparse
import glob
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
import inject_design as idg

SHARE = r'\\POWERHOUSE1\Sharing\cosmic\galaxy2'
SAVES = os.path.join(ROOT, 'server', 'saves')
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def blobs(galaxy):
    """Every decodable blob to survey, newest evidence first.

    A capture that will not decode is skipped; some in `server/saves` are
    truncated.
    """
    paths = []
    if os.path.exists(os.path.join(galaxy, 'turns')):
        paths += sorted(glob.glob(os.path.join(galaxy, 'turns', '*.b64')))
        paths += sorted(glob.glob(os.path.join(galaxy, 'submissions', '*',
                                               '*.b64')))
    paths += sorted(glob.glob(os.path.join(SAVES, '*.b64')))
    out, skipped = [], 0
    for p in paths:
        try:
            out.append((p, sp.load_any(p)))
        except Exception:
            skipped += 1
    return out, skipped


def trailer_of(blob, off):
    """The bytes of a DSGN payload that its objectId and SDPR child do not
    account for."""
    _dver, dlen = idg.sec_len(blob, off)
    _sver, slen = idg.sec_len(blob, off + 12)
    accounted = 4 + 8 + slen
    return bytes(blob[off + 8 + accounted:off + 8 + dlen])


def main(galaxy):
    parts = {'chassis': [1], 'scanners': [0], 'engines': [2, 2],
             'weapons': [], 'modules': [3], 'list6': []}

    print('the record build_dsgn emits is DSGN + objectId + SDPR + one byte')
    rec = idg.build_dsgn(500, 'probe', parts, 198)
    dver, dlen = idg.sec_len(rec, 0)
    sver, slen = idg.sec_len(rec, 12)
    check('it is a DSGN section', rec[0:4], b'DSGN')
    check('version 4, as every real record is', dver, 4)
    check('its child is an SDPR, version 0', (rec[12:16], sver), (b'SDPR', 0))
    check('the DSGN length counts the objectId, the SDPR and the trailer',
          dlen, 4 + 8 + slen + 1)
    check('the record is exactly as long as its length word says',
          len(rec), 8 + dlen)
    check('the trailer is one zero byte', trailer_of(rec, 0), b'\x00')

    print('the trailer is what every real record carries')
    found, skipped = blobs(galaxy)
    lens, values, records = set(), set(), 0
    for _p, blob in found:
        for off, _oid, _name in idg.design_records(blob):
            t = trailer_of(blob, off)
            lens.add(len(t))
            values.add(t)
            records += 1
    print(f'    {records} DSGN record(s) in {len(found)} blob(s), '
          f'{skipped} undecodable capture(s) skipped')
    check('there are real records to measure', records > 0)
    check('every trailer is exactly one byte wide', lens, {1})
    check('and that byte is zero in all of them', values, {b'\x00'})

    print('a rebuilt design is the record the client wrote, byte for byte')
    same = differ = defaulted = 0
    for _p, blob in found:
        for off, _oid, _name in idg.design_records(blob):
            real = blob[off:idg.sec_end(blob, off)]
            oid, name, rparts, owner = idg.read_design(blob, off)
            built = idg.build_dsgn(oid, name, rparts, owner)
            if built == real:
                same += 1
            elif not rparts['scanners']:
                # The defaulted scanner, tested below.
                defaulted += 1
            else:
                differ += 1
                if differ == 1:
                    print(f'          first mismatch {name!r} in {_p}')
                    print(f'          real  {real.hex()}')
                    print(f'          built {built.hex()}')
    print(f'    {same} identical, {defaulted} differing only by the defaulted '
          f'scanner, {differ} otherwise different')
    check('every design that records a scanner rebuilds byte for byte',
          differ, 0)
    check('and that is most of them', same > 0)

    print('the defaulted scanner is the one thing a rebuild adds')
    # A design with no scanner is given the UI's default, four bytes longer.
    bare = dict(parts, scanners=[])
    filled = idg.build_dsgn(500, 'probe', bare, 198)
    check('a design asked for with no scanner is given one',
          idg.read_design(filled, 0)[2]['scanners'], idg.DEFAULT_SCANNERS)
    check('and defaulting it in is the same record as asking for scanner 0',
          len(filled) - len(idg.build_dsgn(500, 'probe', parts, 198)), 0)

    print('the section parser sees the trailer as the DSGN epilogue')
    # save_parser chains the SDPR child and reports the leftover byte as the
    # epilogue.
    tree = sp.parse_sections(rec, 0, len(rec))
    check('a synthesised record parses as one DSGN', len(tree), 1)
    check('with one SDPR child', [c.tag for c in tree[0].children], [b'SDPR'])
    check('and one byte after it', tree[0].epilogue, 1)

    print('make() inserts that record and the blob still adds up')
    with_designs = [(p, b) for p, b in found if idg.design_records(b)]
    check('there is a blob to insert into', bool(with_designs))
    if with_designs:
        path, blob = with_designs[0]
        before = idg.design_records(blob)
        ref_off, ref_id, _ref_name = before[0]
        _o, _n, rparts, owner = idg.read_design(blob, ref_off)
        new = idg.make(blob, ref_id, 'probe rt', dict(rparts), None,
                       log=lambda *a: None)
        after = idg.design_records(new)
        made = [r for r in after if r[2] == 'probe rt']
        check('one design more than before', len(after), len(before) + 1)
        check('exactly one of them is the new one', len(made), 1)
        if made:
            off, oid, _name = made[0]
            rec2 = new[off:idg.sec_end(new, off)]
            check('the inserted bytes are what build_dsgn emits',
                  rec2, idg.build_dsgn(oid, 'probe rt', dict(rparts), owner))
            check('the blob grew by the whole record, trailer included',
                  len(new) - len(blob), len(rec2))
            check('it reads back with the parts it was given',
                  idg.read_design(new, off), (oid, 'probe rt', rparts, owner))
            check('its trailer is a zero byte like every other',
                  trailer_of(new, off), b'\x00')

            # The count dword before a civ's first design must follow the
            # insertion, or the client misreads the layout.
            mine = [r for r in after
                    if idg.owner_of_design(new, r[0])
                    == idg.owner_of_design(new, off)]
            first = min(r[0] for r in mine)
            count = struct.unpack_from('<I', new, first - 4)[0]
            check('the civ design count dword matches the records',
                  count, len(mine))

            # Every enclosing section is widened by the record length.
            grow = len(rec2)
            olds = {t: ln for _o2, t, _v, ln in
                    idg.containing_sections(blob, ref_off)}
            news = {t: ln for _o2, t, _v, ln in
                    idg.containing_sections(new, off)}
            check('every ancestor grew by the record length, no more, no less',
                  {t: news[t] - olds[t] for t in olds if t in news},
                  {t: grow for t in olds if t in news})
            check('the ancestor chain is the same sections it was',
                  set(news), set(olds))

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--galaxy', default=None,
                    help='a turn store holding turns and submissions')
    a = ap.parse_args()
    local = os.path.join(HERE, 'galaxy2')
    sys.exit(main(a.galaxy or (local if os.path.exists(local) else SHARE)))
