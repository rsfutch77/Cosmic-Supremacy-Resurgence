"""
test_civ_name_merge.py: a civ name chosen in dialog 225 is carried once
=======================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_civ_name_merge.py

Dialog 225 writes the civ name, the "Reveal True Player Name" checkbox and the
coat-of-arms id at the end of the civ's OWPR, and is offered while the name is
empty. The referee carries the choice into the next turn, once, and only as
the dialog's OK allows: 3 to 30 characters after trimming spaces and tabs, no
name matching another civ's player name or chosen name in any case, and an
arms id the player uploaded. Uses the tracked turn-0 fixture `t4f`, where
neither civ has chosen.
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

FIX = os.path.join(HERE, 'fixtures', 'tier4')
PASS, FAIL = [], []

UNCHOSEN = (b'', 0, mo.NO_ARMS)


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def merged(served, *subs, arms=None):
    notes = {}
    out = mo.merge(served, list(subs), log=quiet, notes=notes, arms=arms)
    return out, notes


def main():
    served = sp.load_any(os.path.join(FIX, 't4f.dat'))
    me, them = 190, 194                       # DemoPlayer, BadGuy

    print('what the fixture holds')
    check('neither civ has chosen a name',
          [mo.civ_identity(served, c) for c in (me, them)], [UNCHOSEN] * 2)

    print('writing a name grows OWPR and leaves the rest of it alone')
    sub = mo.set_civ_identity(served, me, b'Star Kingdom', 1, mo.NO_ARMS)
    check('the name, checkbox and arms read back',
          mo.civ_identity(sub, me), (b'Star Kingdom', 1, mo.NO_ARMS))
    check('the blob grows by the name', len(sub) - len(served), 12)
    check('the research reads the same',
          mo.research_of(sub, me), mo.research_of(served, me))
    check('the setup flags read the same',
          mo.setup_flags(sub, me), mo.setup_flags(served, me))
    check('the other civ is untouched', mo.civ_identity(sub, them), UNCHOSEN)
    check('the blob still parses', bool(sp.parse_blob(sub)))

    print('a choice the dialog could make is carried once')
    out, notes = merged(served, ('DemoPlayer', sub))
    check('the name and checkbox are taken',
          mo.civ_identity(out, me), (b'Star Kingdom', 1, mo.NO_ARMS))
    check('with nothing refused', notes, {})
    again, notes = merged(out, ('DemoPlayer', out))
    check('served back unchanged, nothing is refused', notes, {})
    check('and the name stays', mo.civ_identity(again, me)[0], b'Star Kingdom')
    renamed = mo.set_civ_identity(out, me, b'Other Kingdom', 1, mo.NO_ARMS)
    again, notes = merged(out, ('DemoPlayer', renamed))
    check('a second choice is refused', notes.get('DemoPlayer'),
          ['civilization name DROPPED, it was already chosen'])
    check('and the first name stays', mo.civ_identity(again, me)[0],
          b'Star Kingdom')
    padded = mo.set_civ_identity(served, me, b'  Abc ', 0, mo.NO_ARMS)
    out, notes = merged(served, ('DemoPlayer', padded))
    check('a name of 3 after trimming is taken as typed',
          mo.civ_identity(out, me)[0], b'  Abc ')
    own = mo.set_civ_identity(served, me, b'DEMOPLAYER', 0, mo.NO_ARMS)
    out, notes = merged(served, ('DemoPlayer', own))
    check("the civ's own player name is allowed, as the dialog skips its own "
          "civ", mo.civ_identity(out, me)[0], b'DEMOPLAYER')

    print('what the dialog would refuse is refused')
    for name, why in (
            (b'Ab', 'it needs to be between 3 and 30 characters long'),
            (b' Ab ', 'it needs to be between 3 and 30 characters long'),
            (b'A' * 31, 'it needs to be between 3 and 30 characters long'),
            (b'Star\x01Kingdom', 'name holds bytes outside printable ASCII'),
            (b'badguy', 'a similar name is already used by BadGuy'),
            (b' BADGUY ', 'a similar name is already used by BadGuy')):
        bad = mo.set_civ_identity(served, me, name, 0, mo.NO_ARMS)
        out, notes = merged(served, ('DemoPlayer', bad))
        check(f'{name!r} is refused', notes.get('DemoPlayer'),
              [f'civilization name DROPPED, {why}'])
        check(f'{name!r} leaves the name unchosen', mo.civ_identity(out, me),
              UNCHOSEN)
    bad = mo.set_civ_identity(served, me, b'Star Kingdom', 2, mo.NO_ARMS)
    out, notes = merged(served, ('DemoPlayer', bad))
    check('a checkbox that is not 0 or 1 is refused', notes.get('DemoPlayer'),
          ['civilization name DROPPED, the reveal checkbox is 2'])
    bad = mo.set_civ_identity(served, me, b'', 1, mo.NO_ARMS)
    out, notes = merged(served, ('DemoPlayer', bad))
    check('a checkbox without a name is refused', notes.get('DemoPlayer'),
          ['civilization name DROPPED, the checkbox or coat of arms changed '
           'without a name'])

    print('a coat of arms is carried only when it is the player\'s upload')
    armed = mo.set_civ_identity(served, me, b'Star Kingdom', 0, 7)
    out, notes = merged(served, ('DemoPlayer', armed))
    check('an arms id the player did not upload is refused',
          notes.get('DemoPlayer'),
          ["civilization name DROPPED, coat of arms 7 is not one of this "
           "player's uploads"])
    check('and nothing is taken', mo.civ_identity(out, me), UNCHOSEN)
    out, notes = merged(served, ('DemoPlayer', armed),
                        arms={'DemoPlayer': {7}, 'BadGuy': {8}})
    check('the player\'s own upload is taken',
          mo.civ_identity(out, me), (b'Star Kingdom', 0, 7))
    out, notes = merged(served, ('DemoPlayer', armed), arms={'BadGuy': {7}})
    check('another player\'s upload is refused', mo.civ_identity(out, me),
          UNCHOSEN)

    print('two civs choosing the same name in one turn')
    mine = mo.set_civ_identity(served, me, b'Star Kingdom', 0, mo.NO_ARMS)
    theirs = mo.set_civ_identity(served, them, b'star kingdom', 0, mo.NO_ARMS)
    out, notes = merged(served, ('DemoPlayer', mine), ('BadGuy', theirs))
    check('the first is taken', mo.civ_identity(out, me)[0], b'Star Kingdom')
    check('the second is refused', notes.get('BadGuy'),
          ['civilization name DROPPED, a similar name is already used by '
           'DemoPlayer'])
    check('and keeps no name', mo.civ_identity(out, them), UNCHOSEN)
    theirs = mo.set_civ_identity(served, them, b'Elder Kingdom', 0,
                                 mo.NO_ARMS)
    out, notes = merged(served, ('DemoPlayer', mine), ('BadGuy', theirs))
    check('two different names are both taken',
          [mo.civ_identity(out, c)[0] for c in (me, them)],
          [b'Star Kingdom', b'Elder Kingdom'])

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
