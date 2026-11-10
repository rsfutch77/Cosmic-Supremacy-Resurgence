"""
test_messages_merge.py: messages between players are carried
=============================================================
    python server\\tests\\test_messages_merge.py

A message is a news item in each recipient's list and one in the sender's,
naming its title and body in the galaxy's string table (`NWDB`). The merge
takes one when the sender's copy and every recipient's copy are as the client
writes them, renumbers its strings into the merged table, and sets each
recipient's new-news flag.

`fixtures/messages` is one Single Player session on `control_fork_played.dat`
(9 November 2026), GoodGuy and BadGuy at peace. As GoodGuy: m0 as loaded, m1
"s1" sent to BadGuy, m2 "s2" sent with `Zoë`, `<b>bold</b>` and `a|b` (the
game ticked to turn 181 before it), m3 "s2" marked important, m4 to m6
BadGuy's colour, territory and ships changed. As BadGuy: n0 m6 reloaded, n1
the news opened, n2 a reply to "s1".
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
import inject_civ as icv                                        # noqa: E402
import merge_orders as mo                                       # noqa: E402
import news                                                     # noqa: E402
import fog                                                      # noqa: E402

FIX = os.path.join(HERE, 'fixtures', 'messages')
GOOD, BAD = 656, 660
NAMES = {GOOD: b'GoodGuy', BAD: b'BadGuy'}
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def load(name):
    return sp.load_any(os.path.join(FIX, name + '.b64'))


def merged(blob, *subs):
    notes = {}
    out = mo.merge(blob, list(subs), log=quiet, notes=notes)
    return out, notes


def messages(blob, civ):
    """[(type, title, body, ids, flags)] of a civ's messages, by text."""
    strings = news.read_table(blob)[1]
    return [(i['type'], strings.get(i['title_id']), strings.get(i['body_id']),
             i['ids'], i['flags'])
            for i in news.items_of(blob, civ)
            if i['type'] in (news.RECEIVED, news.SENT)]


def flag(blob, civ):
    return blob[mo._new_news_at(blob, civ)]


def send(blob, sender, to, subject, body, title_from=None, ids=None,
         sent_ids=None):
    """`blob` with a message added as the client adds one."""
    next_id, strings = news.read_table(blob)
    head = title_from or b'Message from %s - Subject: ' % NAMES[sender]
    t_from, next_id = news.intern(next_id, strings, head + subject)
    t_body, next_id = news.intern(next_id, strings, body)
    t_to, next_id = news.intern(next_id, strings, b'Message to %s - Subject: %s' % (
        b', '.join(NAMES.get(c, b'?') for c in to), subject))
    blob = news.write_table(blob, next_id, strings)
    turn = icv.turn_of(blob)
    for civ in to:
        if civ in NAMES:
            blob = news.append_item(blob, civ, news.build_item({
                'type': news.RECEIVED, 'flags': 0, 'turn': turn, 'title': b'',
                'body': b'', 'title_id': t_from, 'body_id': t_body,
                'ids': ids or [sender], 'position': mo.NO_POSITION}))
    return news.append_item(blob, sender, news.build_item({
        'type': news.SENT, 'flags': 0, 'turn': turn, 'title': b'', 'body': b'',
        'title_id': t_to, 'body_id': t_body, 'ids': sent_ids or list(to),
        'position': mo.NO_POSITION}))


def main():
    m0, m1, m2, m3 = load('m0'), load('m1'), load('m2'), load('m3')
    n0, n1, n2 = load('n0'), load('n1'), load('n2')

    print('\nthe captures')
    check('m1 holds the message as the client wrote it', messages(m1, BAD),
          [(1, b'Message from GoodGuy - Subject: s1', b'hello badguy', [GOOD],
            0)])
    check("and GoodGuy's copy", messages(m1, GOOD),
          [(6, b'Message to BadGuy - Subject: s1', b'hello badguy', [BAD], 0)])
    built = send(m0, GOOD, [BAD], b's1', b'hello badguy')
    check('the test helper writes the same table and items',
          (news.read_table(built), messages(built, GOOD), messages(built, BAD)),
          (news.read_table(m1), messages(m1, GOOD), messages(m1, BAD)))
    check('every table string carries the hash the client wrote',
          all(news.write_table(b, *news.read_table(b)) == b
              for b in (m0, m1, m2, n2)), True)

    print("\nGoodGuy's message to BadGuy, from the client")
    out, notes = merged(m0, ('GoodGuy', m1))
    check('BadGuy holds it', messages(out, BAD), messages(m1, BAD))
    check('GoodGuy holds its copy', messages(out, GOOD), messages(m1, GOOD))
    check("BadGuy's new-news flag is set", flag(out, BAD), 1)
    check('with no note', notes, {})
    check('the merged galaxy parses', sp.parse_blob(out)[0].tag, b'SAVE')

    print("\nBadGuy's reply and its reading, from the client")
    out, notes = merged(n1, ('BadGuy', n2))
    check('GoodGuy holds the reply', messages(out, GOOD), messages(n2, GOOD))
    check("and GoodGuy's new-news flag is set", flag(out, GOOD), 1)
    check('with no note', notes, {})
    out, notes = merged(n0, ('BadGuy', n1))
    check('opening the news clears its flag', (flag(n0, BAD), flag(out, BAD)),
          (1, 0))

    print('\nmarking a sent message important, from the client')
    out, notes = merged(m2, ('GoodGuy', m3))
    check('the flag is carried', messages(out, GOOD), messages(m3, GOOD))
    check('with no note', notes, {})

    print('\ntwo civs send in one turn')
    a = send(m0, GOOD, [BAD], b'from good', b'one')
    b = send(m0, BAD, [GOOD], b'from bad', b'two')
    out, notes = merged(m0, ('GoodGuy', a), ('BadGuy', b))
    check('both arrive, each with its own text',
          ([m[1:3] for m in messages(out, BAD)][-2:],
           [m[1:3] for m in messages(out, GOOD)][-2:]),
          ([(b'Message from GoodGuy - Subject: from good', b'one'),
            (b'Message to GoodGuy - Subject: from bad', b'two')],
           [(b'Message to BadGuy - Subject: from good', b'one'),
            (b'Message from BadGuy - Subject: from bad', b'two')]))
    table = news.read_table(out)
    check('and the table numbers every string once',
          (table[0], len(set(table[1].values())), sorted(table[1])),
          (15, 14, list(range(1, 15))))
    check('with no note', notes, {})

    print('\nthe text is made plain')
    sub = send(m0, GOOD, [BAD], b'100% <b>', b'a|b <i>x</i>\r\nZo\xeb')
    out, notes = merged(m0, ('GoodGuy', sub))
    check('markup characters get stand-ins and Latin-1 is kept',
          messages(out, BAD)[-1][1:3],
          (b'Message from GoodGuy - Subject: 100pct (b)',
           b'a/b (i)x(/i)\r\nZo\xeb'))
    check('with no note', notes, {})

    print('\nrefused')
    cases = [
        ('a body over 18 rows', send(m0, GOOD, [BAD], b'long',
                                      b'\r\n'.join([b'x'] * 19)),
         ["message b'long' DROPPED, its body is over the send dialog's 18 "
          "rows"]),
        ('a control character', send(m0, GOOD, [BAD], b'bell', b'ding\x07'),
         ["message b'bell' DROPPED, it holds characters the client cannot "
          "show"]),
        ('a subject over 80 characters', send(m0, GOOD, [BAD], b'x' * 81, b'y'),
         [f"message {(b'x' * 30)!r} DROPPED, its subject is over 80 "
          f"characters"]),
        ('a title naming another sender',
         send(m0, GOOD, [BAD], b'fake', b'z',
              title_from=b'Message from BadGuy - Subject: '),
         ["message b'fake' DROPPED, a recipient does not hold it as the "
          "client writes it",
          'news item for BadGuy DROPPED, no message GoodGuy sent matches it']),
        ('a civ not met', send(m0, GOOD, [999], b'who', b'z'),
         ['message DROPPED, it names a civ GoodGuy has not met: 999']),
    ]
    for name, sub, want in cases:
        out, notes = merged(m0, ('GoodGuy', sub))
        check(f'{name} is refused', (messages(out, BAD), notes.get('GoodGuy')),
              (messages(m0, BAD), want))
    extra = news.append_item(m1, BAD, news.build_item(dict(
        news.items_of(m1, BAD)[-1], sec=None)))
    out, notes = merged(m0, ('GoodGuy', extra))
    check('a received copy with no sent one is refused, the message kept',
          (messages(out, BAD), notes.get('GoodGuy')),
          (messages(m1, BAD),
           ['news item for BadGuy DROPPED, no message GoodGuy sent matches it']))
    changed = news.set_flags(m0, BAD, 0, 1)
    out, notes = merged(m0, ('GoodGuy', changed))
    check("a change to another civ's item is refused",
          (news.items_of(out, BAD)[0]['flags'], notes.get('GoodGuy')),
          (news.items_of(m0, BAD)[0]['flags'],
           ['news item for BadGuy DROPPED, it is not a message as the client '
            'writes one']))

    print('\neach civ is served only its own news')
    view = fog.project(m1, 'BadGuy', log=quiet)
    check("BadGuy's view holds its own list whole", messages(view, BAD),
          messages(m1, BAD))
    check("and none of GoodGuy's", news.items_of(view, GOOD), [])
    check("nor a string only GoodGuy's list names",
          b'Message to BadGuy - Subject: s1' in news.read_table(view)[1].values(),
          False)
    check('the next id is kept', news.read_table(view)[0],
          news.read_table(m1)[0])
    mine = fog.project(m0, 'GoodGuy', log=quiet)
    sub = send(mine, GOOD, [BAD], b'from the view', b'hi')
    out, notes = merged(m0, ('GoodGuy', sub))
    check("a message sent from the view reaches BadGuy's whole list",
          messages(out, BAD),
          messages(m0, BAD) + [(1, b'Message from GoodGuy - Subject: from the '
                                   b'view', b'hi', [GOOD], 0)])
    check("and BadGuy's items served to it are kept",
          len(news.items_of(out, BAD)), len(news.items_of(m0, BAD)) + 1)
    check('with no note', notes, {})

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
