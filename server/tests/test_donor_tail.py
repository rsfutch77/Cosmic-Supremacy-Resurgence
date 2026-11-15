"""
test_donor_tail.py: a ship whose SHPR names its donor is repaired
=================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_donor_tail.py

`merge_orders.repair_donor_tails` rewrites the id a cloned ship's `SHPR` ends
with to the ship's own, which `referee.tick` does before the engine loads the
galaxy. Inside a fleet that id is the ship's only identity.

Fixture: `fixtures/diplomacy/d1.b64`, where ships 677 and 678 name 672.
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
import merge_orders as mo                                       # noqa: E402

FIXTURE = os.path.join(HERE, 'fixtures', 'diplomacy', 'd1.b64')
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def ships(blob):
    """{oid: (why, own id the SHPR ends with)} for every GLXY SHIP."""
    _tree, _glxy, objects = mo.space_objects(blob)
    return {o['oid']: (o['why'], (mo.shpr_ids(o['shpr']) or (None, None))[1])
            for o in objects if o['tag'] == b'SHIP'}


def main():
    blob = sp.load_any(FIXTURE)
    before = ships(blob)
    check('the fixture holds 677 and 678 naming 672',
          (before[677], before[678]),
          (('its SHPR does not end by naming it', 672),) * 2)

    said = []
    out = mo.repair_donor_tails(blob, log=said.append)
    after = ships(out)
    check('both now name themselves and read',
          (after[677], after[678]), (('', 677), ('', 678)))
    check('every other ship is as it was',
          {k: v for k, v in after.items() if k not in (677, 678)},
          {k: v for k, v in before.items() if k not in (677, 678)})
    check('only the two ids changed', len(out) == len(blob) and sum(
        a != b for a, b in zip(out, blob)) <= 8, True)
    check('each repair is named', len(said), 2)
    check('the blob still parses', sp.parse_blob(out)[0].tag, b'SAVE')
    check('a repaired galaxy is left as it is',
          mo.repair_donor_tails(out, log=said.append), out)
    check('and nothing more is named', len(said), 2)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
