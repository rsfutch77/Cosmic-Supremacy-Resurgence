"""
test_bot_worker.py: which bot civs a pass plays, and when it leaves the client
==============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_bot_worker.py

A pass plays each named bot civ in the roster that has not submitted, soonest
deadline first, and starts none near a refereed tick, with a client running,
or with too little of its own turn left.

The stores are stand-ins and play is recorded, so no client is needed.
"""
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bot_worker as bw                                         # noqa: E402
import referee_worker as rw                                     # noqa: E402
import turn_store                                               # noqa: E402

PASS, FAIL = [], []
NOW = 1_000_000.0


def check(name, got, want):
    (PASS if got == want else FAIL).append(name)
    print(f'  {"ok  " if got == want else "FAIL"}  {name}')
    if got != want:
        print(f'          wanted {want!r}, got {got!r}')


class Store:
    def __init__(self, galaxy, civs, bots, left=3600, submitted=(),
                 status=None, turn=10):
        self.galaxy = galaxy
        self.doc = {'turn': turn, 'deadline': NOW + left, 'civs': list(civs),
                    'bots': list(bots)}
        if status:
            self.doc['status'] = status
        self.submitted = set(submitted)

    def state(self):
        return dict(self.doc)

    def submitted_civs(self, turn):
        return sorted(self.submitted)

    def update_state(self, fields):
        self.doc.update(fields)
        return self.state()


def opener(stores):
    return lambda spec: stores[spec]


def run_pass(stores, referee_dir=None, busy=None, clock=None, stop_after=None):
    """one_pass over `stores`; returns (report, [(galaxy, civ)] played)."""
    played = []
    t = {'now': NOW}
    clock = clock or (lambda: t['now'])

    def play(spec, store, civs, may_start, log):
        for civ in civs:
            if not may_start():
                break
            played.append((store.galaxy, civ))
            store.submitted.add(civ)
            if stop_after is not None:
                stop_after(t)
    report = bw.one_pass(list(stores), 'unused', referee_dir=referee_dir,
                         open_store=opener(stores), play=play,
                         busy=busy or (lambda: None), clock=clock,
                         log=lambda _m='': None)
    return report, played


def main():
    print('a bot is a named civ that is in the roster')
    check('bots_of keeps roster names', turn_store.bots_of(
        {'civs': ['Beta', 'Ann'], 'bots': ['Beta', 'Gone']}), ['Beta'])
    check('no field, no bots', turn_store.bots_of({'civs': ['Beta']}), [])

    print('a pass plays who has not submitted, soonest deadline first')
    stores = {'g/late': Store('late', ['A', 'B'], ['A'], left=5000),
              'g/soon': Store('soon', ['C', 'D', 'H'], ['C', 'D'], left=2000,
                              submitted=['D']),
              'g/none': Store('none', ['E'], [])}
    report, played = run_pass(stores)
    check('played in deadline order', played, [('soon', 'C'), ('late', 'A')])
    check('counted', (report['pending'], report['played']), (2, 2))
    _r, again = run_pass(stores)
    check('a second pass has nothing to play', again, [])

    stores = {'g/x': Store('x', ['A'], ['A'], status=turn_store.CLOSED)}
    check('a closed galaxy is left alone', run_pass(stores)[1], [])

    print('the client is shared')
    stores = {'g/x': Store('x', ['A'], ['A'])}
    report, played = run_pass(stores, busy=lambda: 'a client is running')
    check('a busy client plays nothing', (played, report['held']),
          ([], 'a client is running'))

    print('no civ starts near a tick')
    for left, want in ((100, []), (-300, []), (-2000, [('x', 'A')]),
                       (400, [('x', 'A')])):
        stores = {'g/x': Store('x', ['A'], ['A'], left=3600),
                  'g/ref': Store('ref', ['R'], ['R'], left=left,
                                 submitted=['R'])}
        check(f'refereed galaxy due in {left}s', run_pass(stores)[1], want)
    stores = {'g/x': Store('x', ['A'], ['A'], left=3600),
              'g/ref': Store('ref', ['R'], [], left=60)}
    check('without a referee dir, a galaxy with no bots does not hold',
          run_pass(stores)[1], [('x', 'A')])

    work = tempfile.mkdtemp()
    try:
        with open(os.path.join(work, 'worker_live.json'), 'w') as f:
            json.dump({'store': 'g/ref', 'updated': time.time()}, f)
        with open(os.path.join(work, 'worker_old.json'), 'w') as f:
            json.dump({'store': 'g/old', 'updated': time.time() - 3600}, f)
        keys = bw.refereed_keys(work, time.time())
        check('a live status file names its galaxy',
              keys, {rw.galaxy_key('g/ref')})
        stores = {'g/x': Store('x', ['A'], ['A'], left=3600),
                  'g/ref': Store('ref', ['R'], [], left=60),
                  'g/old': Store('old', ['O'], ['O'], left=60, submitted=['O'])}
        check('a refereed galaxy holds even without bots; a stale one does not',
              run_pass(stores, referee_dir=work)[1], [])
        os.remove(os.path.join(work, 'worker_live.json'))
        check('with no live referee nothing holds',
              run_pass(stores, referee_dir=work)[1], [('x', 'A')])
    finally:
        shutil.rmtree(work)

    print('a civ needs time on its own turn')
    stores = {'g/short': Store('short', ['A'], ['A'], left=60),
              'g/long': Store('long', ['B'], ['B'], left=4000)}
    check('without a referee dir, its own close deadline holds everything',
          run_pass(stores)[1], [])
    empty = tempfile.mkdtemp()
    try:
        check('refereed elsewhere: the short galaxy waits, the next one plays',
              run_pass(stores, referee_dir=empty)[1], [('long', 'B')])
    finally:
        shutil.rmtree(empty)

    print('a tick coming due stops a galaxy between civs')
    stores = {'g/x': Store('x', ['A', 'B', 'C'], ['A', 'B', 'C'], left=3600)}

    def jump(t):
        t['now'] = NOW + 3600 - 60
    check('one civ, then the hold', run_pass(stores, stop_after=jump)[1],
          [('x', 'A')])

    print('the bots list is edited by name')
    stores = {'g/x': Store('x', ['Beta', 'Blorp', 'Ann'], [])}
    q = lambda _m='': None
    check('add', bw.edit_bots('g/x', add=['Beta', 'Blorp'],
                              open_store=opener(stores), log=q),
          ['Beta', 'Blorp'])
    check('add again changes nothing', bw.edit_bots(
        'g/x', add=['Beta'], open_store=opener(stores), log=q),
        ['Beta', 'Blorp'])
    check('remove', bw.edit_bots('g/x', remove=['Beta'],
                                 open_store=opener(stores), log=q), ['Blorp'])
    try:
        bw.edit_bots('g/x', add=['Bta'], open_store=opener(stores), log=q)
        refused = False
    except SystemExit:
        refused = True
    check('a name not in the roster is refused', refused, True)

    print('galaxy specs')
    root = tempfile.mkdtemp()
    try:
        for name in ('a', 'b'):
            os.makedirs(os.path.join(root, name))
            open(os.path.join(root, name, 'state.json'), 'w').close()
        os.makedirs(os.path.join(root, 'junk'))
        got = bw.expand([os.path.join(root, '*'), os.path.join(root, 'a')])
        check('a folder * is each galaxy folder, once',
              [os.path.basename(g) for g in got], ['a', 'b'])
    finally:
        shutil.rmtree(root)
    check('a firebase galaxy segment', bw._galaxy_part(
        'firebase://cs-resurgence/*?bucket=x'), '*')
    check('a firebase galaxy label', bw.galaxy_label(
        Store('sand box', [], []), 'firebase://p/sand%20box'), 'sand_box')

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
