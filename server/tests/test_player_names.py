"""
test_player_names.py , a player name the engine cannot hold is refused once
===========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_player_names.py

The launcher checks a name at the button, but a join request can be sent
without the launcher. `turn_store.name_problem` is the rule the relay applies at
the door and `joins.refusal` applies at the boundary, so a name that is not
printable ASCII, too long, or not usable as a file name is answered with a
refusal instead of reaching `inject_civ`, which encodes it as ASCII inside every
tick that tries to seat it.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import turn_store
import joins

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


GOOD = ['Alice', 'QCplayer1', 'Dr. Who-2', '%s%s%s%n%n', 'a' * 15, "O'Brien"]
BAD = ['', '   ', ' Alice', 'a' * 16, 'Zo\u00eb', 'Ali\u202ece', 'tab\there',
       'new\nline', 'nul\x00', 'a/b', 'a\\b', 'what?', 'end.', 'C:x', '"q"',
       'emoji\U0001F600', 42, None]


def main():
    print('1. name_problem accepts what the engine can hold')
    for n in GOOD:
        check(f'accepts {ascii(n)}', turn_store.name_problem(n), '')
    print('\n2. name_problem refuses the rest, each with a reason')
    for n in BAD:
        check(f'refuses {ascii(n)}', bool(turn_store.name_problem(n)), True)

    print('\n3. joins.refusal refuses at the boundary as well')
    for n in ['Zo\u00eb', 'a/b', 'end.', 'tab\there']:
        check(f'boundary refuses {ascii(n)}', bool(joins.refusal(n, ['Neighbor'], b'')), True)
    check('boundary accepts a good name', joins.refusal('Alice', ['Neighbor'], b''), '')
    check('the length rules agree', turn_store.NAME_LIMIT, joins.NAME_LIMIT)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
