"""
test_message_blocks.py: a player blocks another's messages (M17)
================================================================
    python server\\tests\\test_message_blocks.py

The operator adds a block with `block_tool.py`; the referee reads the
galaxy state's `blocks` and the merge leaves a blocked sender's message out of
that recipient's news. The sender keeps its copy and is not told. The list is
never served to a player.

Uses `fixtures/messages` as `test_messages_merge.py` does: m1 is GoodGuy's
message "s1" to BadGuy, as the client wrote it.
"""
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'functions'),
          HERE):
    if d not in sys.path:
        sys.path.insert(0, d)

os.environ.setdefault('CS_RELAY_PROJECT', 'demo-cs-resurgence')

import save_parser as sp                                        # noqa: E402
import merge_orders as mo                                       # noqa: E402
import news                                                     # noqa: E402
import turn_store                                               # noqa: E402
import firebase_store                                           # noqa: E402
import block_tool                                               # noqa: E402
import referee                                                  # noqa: E402
import relay                                                    # noqa: E402
from test_messages_merge import (load, messages, flag, send,     # noqa: E402
                                 GOOD, BAD)

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def merged(blob, subs, blocks=None):
    notes, lines = {}, []
    out = mo.merge(blob, subs, log=lines.append, notes=notes, blocks=blocks)
    return out, notes, lines


def test_merge():
    m0, m1 = load('m0'), load('m1')
    print('\nthe merge')
    out, notes, _ = merged(m0, [('GoodGuy', m1)])
    check('CONTROL: unblocked, BadGuy receives it', messages(out, BAD),
          messages(m1, BAD))

    out, notes, lines = merged(m0, [('GoodGuy', m1)],
                               blocks={'BadGuy': {'GoodGuy'}})
    check('blocked, BadGuy receives nothing', messages(out, BAD),
          messages(m0, BAD))
    check("and BadGuy's new-news flag is as it was", flag(out, BAD),
          flag(m0, BAD))
    check('GoodGuy keeps its sent copy', messages(out, GOOD),
          messages(m1, GOOD))
    check('GoodGuy is not told', notes, {})
    check("the referee's log says who was not given it",
          any('not delivered to BadGuy, who block GoodGuy' in line
              for line in lines))
    check('the merged galaxy parses', sp.parse_blob(out)[0].tag, b'SAVE')

    out, notes, _ = merged(m0, [('GoodGuy', m1)],
                           blocks={'GoodGuy': {'BadGuy'}})
    check("a block the other way does not stop GoodGuy's message",
          messages(out, BAD), messages(m1, BAD))

    b = send(m0, BAD, [GOOD], b'from bad', b'two')
    out, notes, _ = merged(m0, [('GoodGuy', m1), ('BadGuy', b)],
                           blocks={'BadGuy': {'GoodGuy'}})
    check("BadGuy's own message to GoodGuy still arrives",
          [m[1:3] for m in messages(out, GOOD)][-1:],
          [(b'Message from BadGuy - Subject: from bad', b'two')])
    check('while GoodGuy\'s to BadGuy does not',
          [m for m in messages(out, BAD) if m[0] == news.RECEIVED],
          [m for m in messages(m0, BAD) if m[0] == news.RECEIVED])


def test_state():
    print('\nthe stored list')
    check('blocks_of reads the lists',
          turn_store.blocks_of({'blocks': {'A': ['B', 'C'], 'D': []}}),
          {'A': {'B', 'C'}})
    check('a malformed field blocks nobody',
          (turn_store.blocks_of({'blocks': ['A']}),
           turn_store.blocks_of({'blocks': {'A': 'B'}}),
           turn_store.blocks_of({'blocks': {'A': [3, 'A']}})),
          ({}, {}, {}))
    check('blocks_without takes a civ out on both sides',
          turn_store.blocks_without(
              {'blocks': {'A': ['B'], 'B': ['A', 'C']}}, 'A'),
          {'A': [], 'B': ['C']})
    check('the relay never serves it', turn_store.BLOCKS_KEY in
          relay.PRIVATE_FIELDS and turn_store.BLOCKS_KEY in
          firebase_store.STATE_FIELDS)


def test_tool(tmp):
    print('\nthe operator tool, on a galaxy directory')
    store = turn_store.TurnStore(os.path.join(tmp, 'g'))
    store.start(load('m0'), civs=['GoodGuy', 'BadGuy', 'Third'])
    out = io.StringIO()
    check('add answers 0', block_tool.main([store.root, 'add', 'BadGuy',
                                            'GoodGuy'], out=out), 0)
    block_tool.main([store.root, 'add', 'Third', 'GoodGuy'], out=io.StringIO())
    check('both are kept', turn_store.blocks_of(store.state()),
          {'BadGuy': {'GoodGuy'}, 'Third': {'GoodGuy'}})
    check('and listed', 'BadGuy blocks GoodGuy' in out.getvalue())
    for args, why in ((['add', 'BadGuy', 'GoodGuy'], 'twice'),
                      (['add', 'BadGuy', 'Nobody'], 'a civ not on the roster'),
                      (['add', 'BadGuy', 'BadGuy'], 'itself'),
                      (['remove', 'GoodGuy', 'BadGuy'], 'a block not held')):
        check(f'refuses {why}', block_tool.main([store.root] + args,
                                                out=io.StringIO()), 2)
    block_tool.main([store.root, 'remove', 'BadGuy', 'GoodGuy'],
                    out=io.StringIO())
    check('remove lifts one and keeps the other',
          turn_store.blocks_of(store.state()), {'Third': {'GoodGuy'}})


def test_referee_reads(tmp):
    print('\nthe referee hands the list to the merge')
    seen = {}
    real = mo.merge

    def spy(*args, **kw):
        seen['blocks'] = kw.get('blocks')
        return real(*args, **kw)

    mo.merge = spy
    try:
        referee.apply_orders(load('m0'), [('GoodGuy', load('m1'))],
                             log=lambda *_: None,
                             blocks={'BadGuy': {'GoodGuy'}})
    finally:
        mo.merge = real
    check('apply_orders passes blocks on', seen.get('blocks'),
          {'BadGuy': {'GoodGuy'}})


def main():
    tmp = tempfile.mkdtemp(prefix='blocks_')
    try:
        test_merge()
        test_state()
        test_tool(tmp)
        test_referee_reads(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
