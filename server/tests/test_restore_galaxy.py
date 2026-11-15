"""
test_restore_galaxy.py: a galaxy restored to an earlier turn, on a copy
=======================================================================
    python server\\tests\\test_restore_galaxy.py

`fixtures/messages` m0 and m2 are one galaxy at turns 180 and 181. A folder
galaxy is played to 181, then restored at 180 into an empty folder.
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

import restore_galaxy                                           # noqa: E402
import save_parser as sp                                        # noqa: E402
import turn_store                                               # noqa: E402

FIX = os.path.join(HERE, 'fixtures', 'messages')
CIVS = ['GoodGuy', 'BadGuy']
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
    m0, m2 = (sp.load_any(os.path.join(FIX, n + '.b64')) for n in ('m0', 'm2'))
    work = tempfile.mkdtemp()
    try:
        src = turn_store.open_store(os.path.join(work, 'live'))
        src.start(m0, CIVS, turn_seconds=14400, turn=180)
        src.update_state({'seats': {'uid-1': 'GoodGuy'}, 'min_build': '0.6.0'})
        src.archive(180, {'turn': 180, 'published': 181})
        src.publish(181, m2)
        src.archive(181, {'turn': 181, 'published': 182})
        src.close('the operator restores turn 180')
        before = src.state()

        print('\na dry run')
        dst = turn_store.open_store(os.path.join(work, 'copy'))
        said = restore_galaxy.restore(src, dst, 180, dry_run=True, log=quiet)
        check('names what it would copy', (said['turn'], said['archive_records']),
              (180, 1))
        check('and writes nothing', dst.exists(), False)

        print('\nrestored at turn 180')
        restore_galaxy.restore(src, dst, 180, log=quiet)
        state = dst.state()
        check('the copy is at turn 180', state['turn'], 180)
        check("with turn 180's blob", dst.turn_blob(180), m0)
        check('and not 181', dst.has_turn(181), False)
        check('the roster is carried', state['civs'], CIVS)
        check('the seats and settings are carried',
              (state.get('seats'), state.get('min_build')),
              ({'uid-1': 'GoodGuy'}, '0.6.0'))
        check('the turn length is carried', state['turn_seconds'], 14400)
        check('the copy is open though the source is closed',
              (dst.status(), state.get('closed_reason')),
              (turn_store.OPEN, None))
        check('archive records up to 180 only',
              (dst.archive_record(180), dst.archive_record(181)),
              ({'turn': 180, 'published': 181}, None))
        check('each civ is served its own view of turn 180',
              all(dst.view_blob(c, 180) is not None for c in CIVS), True)
        check('a view hides the rival', dst.view_blob('GoodGuy', 180) != m0, True)
        check('no submissions', dst.submitted_civs(180), [])
        check('the source is untouched', src.state(), before)

        print('\nrefusals')
        for name, args in (('an occupied destination', (src, dst, 180)),
                           ('a turn the source never had',
                            (src, turn_store.open_store(os.path.join(work, 'x')), 150))):
            try:
                restore_galaxy.restore(*args, log=quiet)
                check(f'{name} is refused', False, True)
            except SystemExit:
                check(f'{name} is refused', True, True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
