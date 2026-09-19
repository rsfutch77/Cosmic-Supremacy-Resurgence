"""
agent_msg.py , a message channel between agents working on different machines
=============================================================================
    python agent_msg.py read  --as a
    python agent_msg.py send  --to b "pulled 9b76e45, starting turn 12 here"
    python agent_msg.py send  --to b --file notes.md
    python agent_msg.py read  --as a --all

Two agents on two machines were relaying through the person sitting between
them, which is slow and loses detail: a diagnosis from one machine arrived on
the other as a summary of a summary. This gives them a direct channel over the
folder they already share.

Each direction is a directory of numbered files, never a single file both sides
write. Appending to one shared file over SMB is a lost-update waiting to
happen; a new file per message cannot collide, and the numbering gives a total
order that matches the order they were sent.

    <root>/agents/inbox_a/0001_b_20260918T194500.md     for agent a, from b
    <root>/agents/inbox_b/0002_a_20260918T194530.md     for agent b, from a
    <root>/agents/cursor_a.txt                          last message a has read

`read` shows what has arrived since the cursor and moves it. `--all` shows
everything and moves nothing, which is what you want when reconstructing who
knew what.

The channel carries claims, not authority. A message saying a thing was tested
is evidence that the other agent believes it, and the reader is still the one
responsible for whether it is true here.
"""
import argparse
import datetime
import os
import sys

DEFAULT_ROOT = os.sep * 2 + os.path.join('POWERHOUSE1', 'Sharing', 'cosmic')


def inbox(root, who):
    return os.path.join(root, 'agents', f'inbox_{who}')


def cursor_path(root, who):
    return os.path.join(root, 'agents', f'cursor_{who}.txt')


def next_index(d):
    os.makedirs(d, exist_ok=True)
    used = [int(f.split('_', 1)[0]) for f in os.listdir(d)
            if f[:4].isdigit()]
    return max(used) + 1 if used else 1


def send(root, to, frm, text):
    d = inbox(root, to)
    n = next_index(d)
    stamp = datetime.datetime.now().strftime('%Y%m%dT%H%M%S')
    name = f'{n:04d}_{frm}_{stamp}.md'
    tmp = os.path.join(d, name + '.tmp')
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text.rstrip() + '\n')
    os.replace(tmp, os.path.join(d, name))
    return os.path.join(d, name)


def messages(root, who):
    d = inbox(root, who)
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted(os.listdir(d)):
        if f.endswith('.tmp') or not f[:4].isdigit():
            continue
        out.append((int(f.split('_', 1)[0]), f, os.path.join(d, f)))
    return out


def read_cursor(root, who):
    p = cursor_path(root, who)
    if not os.path.exists(p):
        return 0
    try:
        return int(open(p, encoding='utf-8').read().strip() or 0)
    except ValueError:
        return 0


def write_cursor(root, who, n):
    p = cursor_path(root, who)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(str(n))
    os.replace(tmp, p)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('action', choices=['send', 'read'])
    ap.add_argument('text', nargs='?', help='the message, for send')
    ap.add_argument('--root', default=DEFAULT_ROOT,
                    help=f'the shared folder; default {DEFAULT_ROOT}')
    ap.add_argument('--to', help='which agent to send to, for send')
    ap.add_argument('--from', dest='frm', help='who is sending; inferred if omitted')
    ap.add_argument('--as', dest='who', help='which agent is reading')
    ap.add_argument('--file', help='send this file as the message body')
    ap.add_argument('--all', action='store_true',
                    help='with read, show everything and move no cursor')
    a = ap.parse_args()

    if not os.path.isdir(a.root):
        sys.exit(f'the shared folder is not reachable: {a.root}\n'
                 f'run check_store.py, the cause is usually the same')

    if a.action == 'send':
        if not a.to:
            sys.exit('send needs --to')
        frm = a.frm or ('a' if a.to == 'b' else 'b')
        body = (open(a.file, encoding='utf-8').read() if a.file else a.text)
        if not body:
            sys.exit('nothing to send; give text or --file')
        path = send(a.root, a.to, frm, body)
        print(f'sent {os.path.basename(path)} to inbox_{a.to}')
        return 0

    who = a.who
    if not who:
        sys.exit('read needs --as a or --as b')
    msgs = messages(a.root, who)
    seen = 0 if a.all else read_cursor(a.root, who)
    fresh = [m for m in msgs if m[0] > seen]
    if not fresh:
        print(f'inbox_{who}: nothing new '
              f'({len(msgs)} message(s) in total, cursor at {seen})')
        return 0
    for n, name, path in fresh:
        print(f'\n{"=" * 70}\n{name}\n{"=" * 70}')
        print(open(path, encoding='utf-8').read().rstrip())
    if not a.all:
        write_cursor(a.root, who, fresh[-1][0])
        print(f'\ncursor moved to {fresh[-1][0]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
